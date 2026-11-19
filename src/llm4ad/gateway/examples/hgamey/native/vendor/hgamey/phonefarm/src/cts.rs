//! CTS 自动化测试 Harness (CTS Harness Spec v1.0): 面向 A2OH 桥接环境的轻量化
//! instrumentation 批量测试与诊断支持。纯增量模块——不触碰 run/benchmark/script/serve
//! 的任何既有路径；设备层只新增 stream_shell/push_file 两个方法，看门狗、遥测切片与
//! 崩溃打包全部作为执行器外层的包装，不进入 Device 基础通道。
//!
//! 与 A2OH 团队《API 桥接的 CTS 快速测试流程》(CTS_FAST_TESTING.md) 的对齐契约:
//!   ① 用例粒度原生支持 Class#method 按需切片(-e class com.x.C#m),而非整包盲跑;
//!   ② 判定枚举严格采用 PASS / ASSERTION_FAIL / ENV_BLOCKED / TIMEOUT / NOT_RUN,
//!      跳过、零用例、运行器错误一律不得计为 PASS;
//!   ③ 掉线纪律: 停设备写操作→留恢复记录→重连后核对 boot_id/包/进程再继续,
//!      不重用旧 PID,不因观察超时直接重启测试;
//!   ④ 证据目录固定四件: stdout.log / hilog_slice.log / telemetry.json / 批次报告
//!      (junit.xml + summary.json),含阶段耗时与恢复状态 VERIFIED|PENDING。
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

// ══════════════ 判定枚举(同事流程对齐,字面量即契约) ══════════════

/// 单用例判定。序列化值为同事流程约定的字面值,跨工具可直接对账。
/// 变体名刻意保持 SCREAMING(与同事文档字面值 1:1),禁 camelCase/acronym 风格警告。
#[allow(non_camel_case_types, clippy::upper_case_acronyms)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum CaseVerdict {
    /// 断言全过
    PASS,
    /// 断言失败(业务语义上的测试失败)
    ASSERTION_FAIL,
    /// 环境阻塞: 运行器缺失、设备掉线、桥接进程崩溃、权限/依赖不满足
    ENV_BLOCKED,
    /// 看门狗超时(全局或静默)
    TIMEOUT,
    /// 未运行: 用例被列出但没有产生结果(跳过/忽略/轮次中断)
    NOT_RUN,
}

impl CaseVerdict {
    pub fn as_str(&self) -> &'static str {
        match self {
            CaseVerdict::PASS => "PASS",
            CaseVerdict::ASSERTION_FAIL => "ASSERTION_FAIL",
            CaseVerdict::ENV_BLOCKED => "ENV_BLOCKED",
            CaseVerdict::TIMEOUT => "TIMEOUT",
            CaseVerdict::NOT_RUN => "NOT_RUN",
        }
    }
}

/// 单用例结果(含现场材料)
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CaseResult {
    #[serde(rename = "class")]
    pub class_name: String,
    pub method: String,
    pub verdict: CaseVerdict,
    /// Java 异常调用栈(失败/错误时)
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub stack: String,
    /// 运行器 stream 文本(附加输出)
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub stream: String,
    /// 该用例窗口内的原始输出行(stdout.log 的材料)
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub raw_lines: Vec<String>,
    pub duration_ms: u64,
}

impl CaseResult {
    pub fn id(&self) -> String {
        format!("{}#{}", self.class_name, self.method)
    }
}

// ══════════════ instrument 原语 ══════════════

/// 目标平台: Android(am instrument)/ OpenHarmony(aa test, arkxtest 官方 runner)。
/// 判定枚举与证据契约两族完全共用;仅命令合成与输出协议不同(解析层做归一化)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
pub enum Platform {
    #[default]
    Android,
    Oh,
}

impl Platform {
    pub fn as_str(&self) -> &'static str {
        match self {
            Platform::Android => "android",
            Platform::Oh => "oh",
        }
    }
    /// 宽松解析(profile/CLI 字符串 → Platform);未知值返 None 由调用方报错
    pub fn parse(s: &str) -> Option<Platform> {
        match s.trim().to_ascii_lowercase().as_str() {
            "android" | "adb" | "" => Some(Platform::Android),
            "oh" | "hdc" | "openharmony" | "harmonyos" => Some(Platform::Oh),
            _ => None,
        }
    }
}

/// instrument 动作规格: 原生支持 Class#method 级切片
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InstrumentSpec {
    /// 被测包名(target package / OH bundle 名)
    pub package: String,
    /// Instrumentation runner(AndroidJUnitRunner / OpenHarmonyTestRunner 等)
    pub runner: String,
    /// OH(stage model)HAP 模块名(aa test -m);Android 忽略
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub module: Option<String>,
    /// 目标平台;缺省 Android(既有行为零变化)
    #[serde(default)]
    pub platform: Platform,
    /// 可选 Class 或 Class#method;缺省=整个 runner 全量
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub class_or_method: Option<String>,
    /// 全局超时(毫秒),0=不设
    #[serde(default = "d_total_ms")]
    pub timeout_ms: u64,
    /// 静默超时(毫秒): 管道长时间无字符流判定锁死
    #[serde(default = "d_idle_ms")]
    pub idle_timeout_ms: u64,
    /// 透传运行器参数(Android: -e k v / OH: -s k v)
    #[serde(default)]
    pub env_args: Vec<(String, String)>,
}
fn d_total_ms() -> u64 {
    600_000
}
fn d_idle_ms() -> u64 {
    90_000
}

/// 组装 instrument 命令(纯函数供单测)。
/// Android: `am instrument -r -w`;runner 已含包名(含'.')时原样使用,相对写法(".FooRunner")补包名前缀。
/// OH: `aa test -b <bundle> [-m <module>] -s unittest <runner> [-s class C[#m]] [-s k v]…`
/// (arkxtest Delegator 契约;runner 类名原样使用,不补包名;OH 的 timeout 单位是秒,透传时自行换算)
pub fn instrument_command(spec: &InstrumentSpec) -> String {
    match spec.platform {
        Platform::Oh => {
            let mut c = format!("aa test -b {}", spec.package);
            if let Some(m) = &spec.module {
                if !m.is_empty() {
                    c.push_str(&format!(" -m {m}"));
                }
            }
            c.push_str(&format!(" -s unittest {}", spec.runner));
            if let Some(cm) = &spec.class_or_method {
                if !cm.is_empty() {
                    c.push_str(&format!(" -s class {}", cm));
                }
            }
            for (k, v) in &spec.env_args {
                c.push_str(&format!(" -s {} {}", k, v));
            }
            c
        }
        Platform::Android => {
            let mut c = String::from("am instrument -r -w");
            for (k, v) in &spec.env_args {
                c.push_str(&format!(" -e {} {}", k, v));
            }
            if let Some(cm) = &spec.class_or_method {
                if !cm.is_empty() {
                    c.push_str(&format!(" -e class {}", cm));
                }
            }
            let runner = if spec.runner.contains('.') {
                spec.runner.clone()
            } else {
                format!("{}.{}", spec.package, spec.runner)
            };
            c.push_str(&format!(" {}/{}", spec.package, runner));
            c
        }
    }
}

// ══════════════ 流式输出解析状态机 ══════════════

/// OH 用例收官码(OHOS_REPORT_CODE)→ Android STATUS_CODE 语义(纯函数供单测)。
/// 0=PASS; -2=断言失败; -1=用例错误(未捕获异常,按断言失败记,栈随 stack 字段保留);
/// 其余负值=未运行;无法识别返 99(不会匹配任何收官分支,本行被忽略)。
fn oh_case_code(s: &str) -> i32 {
    match s.parse::<i32>() {
        Ok(0) => 0,
        Ok(-1 | -2) => -2,
        Ok(n) if n < 0 => -3,
        _ => 99,
    }
}

/// 解析器产出的事件: 用例开始/用例结束/整轮结束
#[derive(Debug, Clone, PartialEq)]
pub enum ParseEvent {
    CaseStarted { class_name: String, method: String },
    CaseFinished(CaseResult),
    RunFinished { code: i32, tail: Vec<String> },
}

/// `am instrument -r` / `aa test`(OH 归一化后)输出流状态机。
/// 协议要点(实测 AndroidJUnitRunner;OH 官方 runner 行在入口归一化,见 feed_line):
///   INSTRUMENTATION_STATUS: <key>=<value>   — 字段行;stream/stack 的值可延续到
///                                             后续不带 INSTRUMENTATION_ 前缀的行
///   INSTRUMENTATION_STATUS_CODE: <n>        — 状态码: 1=开始 0=PASS -2=断言失败
///                                             -3=忽略(跳过) -4=假设失败(亦按跳过)
///   INSTRUMENTATION_RESULT: ...             — 整轮结果段
///   INSTRUMENTATION_CODE: <n>               — 整轮结束(-1)
/// 不等整轮结束才出结果: 每个 STATUS_CODE 到达即产出一条 CaseFinished。
pub struct InstrumentParser {
    fields: HashMap<String, String>,
    /// 多行值的归属键(stream/stack);新 INSTRUMENTATION_ 行到达时结束续行
    cont_key: Option<String>,
    cur_class: String,
    cur_method: String,
    cur_lines: Vec<String>,
    cur_start: Option<Instant>,
    /// 整轮尾部原文(OK (N tests) / FAILURES!!! 等),供报告引用
    tail: Vec<String>,
    crashed: bool,
    /// runner 宣称的用例总数(numtests 字段,整轮对账用;未播报则为 None)
    numtests: Option<u32>,
    /// runner 级错误(INSTRUMENTATION_STATUS: Error=...,组件缺失/参数非法等)
    runner_error: Option<String>,
    /// 最近一次收官的用例(去重: 部分 OH 版本同一用例既发 OHOS_REPORT_CODE
    /// 又发 STATUS_CODE 收官行,不得双记;Android 每用例必先 STATUS_CODE: 1 重开,不受影响)
    last_finished: Option<(String, String)>,
}

impl Default for InstrumentParser {
    fn default() -> Self {
        Self::new()
    }
}

impl InstrumentParser {
    pub fn new() -> Self {
        InstrumentParser {
            fields: HashMap::new(),
            cont_key: None,
            cur_class: String::new(),
            cur_method: String::new(),
            cur_lines: Vec::new(),
            cur_start: None,
            tail: Vec::new(),
            crashed: false,
            numtests: None,
            runner_error: None,
            last_finished: None,
        }
    }

    /// 喂入一行原始输出,返回本行触发的事件(可能为空)
    pub fn feed_line(&mut self, line: &str) -> Vec<ParseEvent> {
        let mut events = Vec::new();
        let raw = line.trim_end();
        // 证据行(cur_lines/raw_lines)永远存原始行,协议识别可用归一化行
        self.cur_lines.push(raw.to_string());

        // ── OH 官方 runner 协议行归一化(arkxtest OpenHarmonyTestRunner → Android 等价)──
        // 字段语义逐位对齐: STATUS/STATUS_CODE/RESULT/RESULT_CODE 两族互为镜像。
        // OH 独立的用例收官行 OHOS_REPORT_CODE 映射进 STATUS_CODE 语义
        //   (见 oh_case_code: 0=PASS -1=用例错误按断言失败记 -2=断言失败 其余负值=NOT_RUN)
        // OH 用例错误(STATUS_CODE: -1)仅当行来自 OH 归一化且带用例名时才记失败,
        // Android 既有路径 -1 行为不变(绝对增量)。
        let mut oh_line = false;
        let owned;
        let l: &str = if let Some(r) = raw.strip_prefix("OHOS_REPORT_STATUS_CODE: ") {
            oh_line = true;
            owned = format!("INSTRUMENTATION_STATUS_CODE: {r}");
            &owned
        } else if let Some(r) = raw.strip_prefix("OHOS_REPORT_STATUS: ") {
            owned = format!("INSTRUMENTATION_STATUS: {r}");
            &owned
        } else if let Some(r) = raw.strip_prefix("OHOS_REPORT_RESULT_CODE: ") {
            owned = format!("INSTRUMENTATION_CODE: {r}");
            &owned
        } else if let Some(r) = raw.strip_prefix("OHOS_REPORT_RESULT: ") {
            owned = format!("INSTRUMENTATION_RESULT: {r}");
            &owned
        } else if let Some(r) = raw.strip_prefix("OHOS_REPORT_CODE: ") {
            oh_line = true;
            owned = format!("INSTRUMENTATION_STATUS_CODE: {}", oh_case_code(r.trim()));
            &owned
        } else {
            raw
        };

        if l.contains("Process crashed") || l.contains("shortMsg=Process crashed") {
            self.crashed = true;
        }
        // runner 级失败兜底: 设备侧 stdout 可能因进程死亡竞态丢 Error= 行(实测偶发),
        // stderr 的 INSTRUMENTATION_FAILED 字样是最后的线索;Error= 字段到达时会覆盖它
        if l.contains("INSTRUMENTATION_FAILED") && self.runner_error.is_none() {
            self.runner_error = Some(l.trim().to_string());
        }
        if let Some(rest) = l.strip_prefix("INSTRUMENTATION_STATUS: ") {
            self.cont_key = None;
            if let Some((k, v)) = rest.split_once('=') {
                let k = k.trim().to_string();
                let v = v.to_string();
                if k == "stream" || k == "stack" {
                    self.cont_key = Some(k.clone());
                }
                if k == "numtests" {
                    self.numtests = v.trim().parse().ok();
                }
                if k == "Error" {
                    self.runner_error = Some(v.clone());
                }
                self.fields.insert(k, v);
            }
            return events;
        }
        if let Some(code_s) = l.strip_prefix("INSTRUMENTATION_STATUS_CODE: ") {
            self.cont_key = None;
            let code: i32 = code_s.trim().parse().unwrap_or(i32::MIN);
            let f = &self.fields;
            let class_name = f.get("class").cloned().unwrap_or_default();
            let method = f.get("test").cloned().unwrap_or_default();
            // 收官时的用例身份: 行内字段优先,缺失回落到本用例开始时记下的身份。
            // (每条 STATUS_CODE 行处理完都会清空 fields,OH 的收官行往往只剩 stack)
            let id_class = if class_name.is_empty() {
                self.cur_class.clone()
            } else {
                class_name.clone()
            };
            let id_method = if method.is_empty() {
                self.cur_method.clone()
            } else {
                method.clone()
            };
            // 收官码归一: Android 0/-2/-3/-4 原样;OH 用例错误(-1,仅 OH 归一化行且已知用例名)
            // 按断言失败记(栈在 stack 字段,绝不静默零记)——Android 既有路径 -1 行为零变化
            let finish = if matches!(code, 0 | -2 | -3 | -4) {
                Some(code)
            } else if code == -1 && oh_line && (!id_class.is_empty() || !id_method.is_empty()) {
                Some(-2)
            } else {
                None
            };
            match (code, finish) {
                (1, _) => {
                    // 用例开始
                    self.cur_class = class_name.clone();
                    self.cur_method = method.clone();
                    self.cur_lines.clear();
                    self.cur_start = Some(Instant::now());
                    self.last_finished = None;
                    if !class_name.is_empty() {
                        events.push(ParseEvent::CaseStarted { class_name, method });
                    }
                }
                (_, Some(fc)) => {
                    let verdict = match fc {
                        0 => CaseVerdict::PASS,
                        -2 => CaseVerdict::ASSERTION_FAIL,
                        // 跳过/假设失败不得计为通过(同事流程: 跳过不算 PASS)
                        _ => CaseVerdict::NOT_RUN,
                    };
                    let dur = self
                        .cur_start
                        .map(|t| t.elapsed().as_millis() as u64)
                        .unwrap_or(0);
                    let (cn, m) = (id_class, id_method);
                    // OH 双收官去重: 同一用例既发 OHOS_REPORT_CODE 又发 STATUS_CODE 时只记一次
                    // (Android 每用例必先 STATUS_CODE: 1 重开,会清空本标记,不受影响)
                    let key = (cn.clone(), m.clone());
                    if self.last_finished.as_ref() == Some(&key) {
                        self.fields.clear();
                        return events;
                    }
                    self.last_finished = Some(key);
                    events.push(ParseEvent::CaseFinished(CaseResult {
                        class_name: cn,
                        method: m,
                        verdict,
                        stack: f.get("stack").cloned().unwrap_or_default(),
                        stream: f.get("stream").cloned().unwrap_or_default(),
                        raw_lines: std::mem::take(&mut self.cur_lines),
                        duration_ms: dur,
                    }));
                }
                _ => {}
            }
            self.fields.clear();
            return events;
        }
        if let Some(rest) = l.strip_prefix("INSTRUMENTATION_RESULT: ") {
            self.cont_key = None;
            if let Some((k, _v)) = rest.split_once('=') {
                if k.trim() == "stream" {
                    self.cont_key = Some("__tail__".into());
                }
                self.tail.push(l.to_string());
            }
            return events;
        }
        if let Some(code_s) = l.strip_prefix("INSTRUMENTATION_CODE: ") {
            self.cont_key = None;
            let code: i32 = code_s.trim().parse().unwrap_or(i32::MIN);
            events.push(ParseEvent::RunFinished {
                code,
                tail: self.tail.clone(),
            });
            return events;
        }

        // 非协议行: 多行值续行(stack/stream),或整轮尾部原文
        match self.cont_key.clone().as_deref() {
            Some(k @ ("stream" | "stack")) => {
                self.fields
                    .entry(k.to_string())
                    .and_modify(|v| {
                        v.push('\n');
                        v.push_str(l);
                    })
                    .or_insert_with(|| l.to_string());
            }
            Some("__tail__") => self.tail.push(l.to_string()),
            _ => {
                // runner 直出文本(如 "com.example.FooTest:" 用例头),仅留痕
                if !l.is_empty() && !self.tail.is_empty() {
                    self.tail.push(l.to_string());
                }
            }
        }
        events
    }

    /// 进程崩溃标记(桥接层 Native Crash 经由 runner 文本露出)
    pub fn saw_process_crash(&self) -> bool {
        self.crashed
    }

    /// runner 宣称的用例总数(未播报为 None)
    pub fn numtests(&self) -> Option<u32> {
        self.numtests
    }

    /// runner 级错误(STATUS Error= 字段,如 instrumentation 未安装/组件不存在)
    pub fn runner_error(&self) -> Option<&str> {
        self.runner_error.as_deref()
    }
}

// ══════════════ 双重看门狗流式执行器 ══════════════

/// 一轮 instrument 执行的总判定
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum RunVerdict {
    /// 正常收官(用例成败看各自 verdict)
    Completed,
    /// 全局总超时触发级联清理
    TotalTimeout,
    /// 静默超时(管道长时间无字符流,判定锁死)触发级联清理
    IdleTimeout,
    /// 本地进程拉起失败
    SpawnFailed,
}

impl RunVerdict {
    pub fn as_str(&self) -> &'static str {
        match self {
            RunVerdict::Completed => "Completed",
            RunVerdict::TotalTimeout => "TotalTimeout",
            RunVerdict::IdleTimeout => "IdleTimeout",
            RunVerdict::SpawnFailed => "SpawnFailed",
        }
    }
}

#[derive(Debug)]
pub struct InstrumentRun {
    pub cases: Vec<CaseResult>,
    pub verdict: RunVerdict,
    /// 全部原始输出行(stdout.log 落盘材料)
    pub stdout_lines: Vec<String>,
    /// 解析器是否看到桥接进程崩溃
    pub process_crashed: bool,
    pub wall_ms: u64,
}

/// 流式执行 instrument: 拉起管道→逐行解析→双重看门狗(total+idle)→超时级联清理。
/// 看门狗与清理是本函数对执行器的纯外层包装;log_sink 逐行接收原始输出(供落盘)。
pub fn run_instrument(
    phone: &crate::device::Device,
    spec: &InstrumentSpec,
    log_sink: &mut dyn FnMut(&str),
) -> InstrumentRun {
    let t0 = Instant::now();
    let cmd = instrument_command(spec);
    log_sink(&format!("$ {cmd}"));

    let spawned = phone.stream_shell(&cmd);
    let mut child: Child = match spawned {
        Ok(c) => c,
        Err(e) => {
            log_sink(&format!("!! spawn failed: {e}"));
            return InstrumentRun {
                cases: vec![],
                verdict: RunVerdict::SpawnFailed,
                stdout_lines: vec![format!("spawn failed: {e}")],
                process_crashed: false,
                wall_ms: t0.elapsed().as_millis() as u64,
            };
        }
    };

    // stdout/stderr 双 reader 线程 → 行通道
    let (tx, rx) = mpsc::channel::<String>();
    fn spawn_reader<R: std::io::Read + Send + 'static>(r: Option<R>, tx: mpsc::Sender<String>) {
        if let Some(s) = r {
            std::thread::spawn(move || {
                let mut reader = BufReader::new(s);
                let mut buf = String::new();
                loop {
                    buf.clear();
                    match reader.read_line(&mut buf) {
                        Ok(0) | Err(_) => break,
                        Ok(_) => {
                            if tx.send(buf.trim_end().to_string()).is_err() {
                                break;
                            }
                        }
                    }
                }
            });
        }
    }
    spawn_reader(child.stdout.take(), tx.clone());
    spawn_reader(child.stderr.take(), tx.clone());
    drop(tx);

    let mut parser = InstrumentParser::new();
    let mut cases: Vec<CaseResult> = Vec::new();
    let mut stdout_lines: Vec<String> = Vec::new();
    let mut last_output = Instant::now();
    let mut verdict = RunVerdict::Completed;

    loop {
        // 收干已到达的行
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(line) => {
                last_output = Instant::now();
                log_sink(&line);
                stdout_lines.push(line.clone());
                for ev in parser.feed_line(&line) {
                    match ev {
                        ParseEvent::CaseStarted { class_name, method } => {
                            println!("    ▶ {class_name}#{method}");
                        }
                        ParseEvent::CaseFinished(c) => {
                            println!(
                                "    {} {} ({:.1}s)",
                                match c.verdict {
                                    CaseVerdict::PASS => "✓",
                                    CaseVerdict::ASSERTION_FAIL => "✗",
                                    CaseVerdict::ENV_BLOCKED => "⊘",
                                    CaseVerdict::TIMEOUT => "⏱",
                                    CaseVerdict::NOT_RUN => "○",
                                },
                                c.id(),
                                c.duration_ms as f64 / 1000.0
                            );
                            cases.push(c);
                        }
                        ParseEvent::RunFinished { code, .. } => {
                            println!("    ── runner 收官 code={code}");
                        }
                    }
                }
                continue;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                // 管道全关: 子进程应已退出,确认后收官
                match child.try_wait() {
                    Ok(Some(_)) => break,
                    Ok(None) => { /* 子进程尚在(管道先于退出关闭的极端态),走超时判定 */
                    }
                    Err(_) => break,
                }
            }
        }

        // 子进程自然退出
        if let Ok(Some(_)) = child.try_wait() {
            // 再抽干剩余行(下一轮 recv 会 Disconnected 后退出)
            continue;
        }

        // 看门狗: 静默超时(管道锁死) / 全局总超时
        let idle = last_output.elapsed();
        let total = t0.elapsed();
        let hit_idle =
            spec.idle_timeout_ms > 0 && idle > Duration::from_millis(spec.idle_timeout_ms);
        let hit_total = spec.timeout_ms > 0 && total > Duration::from_millis(spec.timeout_ms);
        if hit_idle || hit_total {
            verdict = if hit_idle {
                RunVerdict::IdleTimeout
            } else {
                RunVerdict::TotalTimeout
            };
            let kind = verdict.as_str();
            log_sink(&format!(
                "!! watchdog {kind}: idle={:.1}s total={:.1}s — 级联清理开始",
                idle.as_secs_f64(),
                total.as_secs_f64()
            ));
            cascade_cleanup(phone, &mut child, &spec.package, log_sink);
            break;
        }
    }

    let wall_ms = t0.elapsed().as_millis() as u64;
    let crashed = parser.saw_process_crash();
    reconcile_cases(
        &mut cases,
        &parser,
        &verdict,
        crashed,
        spec.class_or_method.is_none(),
        wall_ms,
    );

    InstrumentRun {
        cases,
        verdict,
        stdout_lines,
        process_crashed: crashed,
        wall_ms,
    }
}

/// 收尾对账(纯函数供单测): 无论一轮以何种方式结束,绝不让用例凭空消失。
/// ① 已开始但从未收官的在飞用例: 看门狗强杀→TIMEOUT;进程崩溃/管道早断→ENV_BLOCKED
///    (同事契约: 桥崩溃属环境阻塞,且绝不允许"开始后消失"被误记或漏记)。
/// ② numtests 整轮对账(仅整模块轮次): runner 宣称数 > 实际产出数,缺额记一条
///    NOT_RUN(未产出不算 PASS)。切片轮次由 profile 按名对账,不在此重复记。
/// ③ runner 级错误(STATUS Error=...)且零产出: 记 ENV_BLOCKED(未安装/组件不存在
///    等运行器错误不得静默为零用例)。
fn reconcile_cases(
    cases: &mut Vec<CaseResult>,
    parser: &InstrumentParser,
    verdict: &RunVerdict,
    crashed: bool,
    whole_module: bool,
    wall_ms: u64,
) {
    let finished_started = cases
        .iter()
        .any(|c| c.class_name == parser.cur_class && c.method == parser.cur_method);
    if !parser.cur_class.is_empty() && !finished_started {
        let (v, stream) = if *verdict != RunVerdict::Completed {
            (
                CaseVerdict::TIMEOUT,
                format!(
                    "watchdog {} after {:.1}s",
                    verdict.as_str(),
                    wall_ms as f64 / 1000.0
                ),
            )
        } else {
            (
                CaseVerdict::ENV_BLOCKED,
                if crashed {
                    "process crashed: runner 收官时该用例未产出结果".to_string()
                } else {
                    "runner 管道中断: 已开始用例未产出结果".to_string()
                },
            )
        };
        cases.push(CaseResult {
            class_name: parser.cur_class.clone(),
            method: parser.cur_method.clone(),
            verdict: v,
            stack: String::new(),
            stream,
            raw_lines: parser.cur_lines.clone(),
            duration_ms: wall_ms,
        });
    }
    // ③ runner 级错误(STATUS Error=...,如 instrumentation 未安装/组件不存在):
    //    零产出时如实补记 ENV_BLOCKED,绝不允许"0 用例"静默溜过
    if cases.is_empty() {
        if let Some(err) = parser.runner_error() {
            cases.push(CaseResult {
                class_name: "(runner)".into(),
                method: "runner_error".into(),
                verdict: CaseVerdict::ENV_BLOCKED,
                stack: String::new(),
                stream: format!("runner 报错: {err}"),
                raw_lines: parser.cur_lines.clone(),
                duration_ms: wall_ms,
            });
        }
    }
    if whole_module {
        if let Some(n) = parser.numtests() {
            let (declared, accounted) = (n as usize, cases.len());
            if declared > accounted {
                cases.push(CaseResult {
                    class_name: "(runner)".into(),
                    method: format!("unaccounted_cases_x{}", declared - accounted),
                    verdict: CaseVerdict::NOT_RUN,
                    stack: String::new(),
                    stream: format!(
                        "runner 宣称 {declared} 用例,仅产出 {accounted};缺额未运行(崩溃/看门狗提前终结)"
                    ),
                    raw_lines: vec![],
                    duration_ms: 0,
                });
            }
        }
    }
}

/// 级联清理(看门狗外层包装): 终止本地管道 → 设备端强杀被测进程 → 残余清理。
/// 每步 best-effort,如实记录,不抛错。
fn cascade_cleanup(
    phone: &crate::device::Device,
    child: &mut Child,
    pkg: &str,
    log_sink: &mut dyn FnMut(&str),
) {
    log_sink("  [1/3] 终止本地管道(child.kill)");
    let _ = child.kill();
    let _ = child.wait();
    // 按后端分发: adb→am force-stop / hdc→aa force-stop+pidof 补刀(内部已含)
    log_sink(&format!("  [2/3] 设备端强杀被测进程 {pkg}"));
    phone.force_stop(pkg);
    if phone.backend_name() == "adb" {
        // runner 宿主进程(与 target 同包名)若仍在,按 pidof 补刀(OH 后端 force_stop 已内置)
        let out2 = phone.shell(
            &format!("p=$(pidof {pkg}); [ -n \"$p\" ] && kill -9 $p; echo rc=$?"),
            6000,
        );
        log_sink(&format!("    pidof-kill: {}", out2.trim()));
    }
    log_sink("  [3/3] 残余环境清理(回 HOME,释放输入焦点)");
    phone.home();
}

// ══════════════ 日志时间窗切片(hilog / logcat 同构) ══════════════

/// 设备侧日志 dump: OH 走 `hilog -x`,Android 走 `logcat -d`。
pub fn device_log_dump(phone: &crate::device::Device) -> String {
    match phone.backend_name() {
        "hdc" => phone.shell("hilog -x", 15000),
        _ => phone.shell("logcat -d", 15000),
    }
}

/// 设备当前时间锚点(月,日,当年秒数浮点): hilog/logcat 行首时间戳无年份,
/// 以设备 `date` 为锚,窗口比较全部在设备时间域内进行(主机时区/时钟偏差不入链)。
pub fn device_time_anchor(phone: &crate::device::Device) -> Option<(u32, u32, f64)> {
    let out = phone.shell("date '+%m %d %H %M %S'", 5000);
    let nums: Vec<f64> = out
        .split(|c: char| !c.is_ascii_digit())
        .filter(|t| !t.is_empty())
        .filter_map(|t| t.parse().ok())
        .collect();
    if nums.len() >= 5 {
        Some((
            nums[0] as u32,
            nums[1] as u32,
            nums[2] * 3600.0 + nums[3] * 60.0 + nums[4],
        ))
    } else {
        None
    }
}

/// 伪时间戳比较键: (年内日序*86400 + 当日秒数)。跨年/跨日边界按月日序保守比较——
/// 测试窗口量级是秒到分钟,年内日序已足够单调。
fn ts_key(month: u32, day: u32, secs: f64) -> f64 {
    let mut days = 0u32;
    for m in 1..month.min(13) {
        days += match m {
            1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
            4 | 6 | 9 | 11 => 30,
            _ => 28,
        };
    }
    (days + day) as f64 * 86400.0 + secs
}

/// 解析日志行首时间戳 "MM-DD HH:MM:SS.mmm" → ts_key(纯函数)。
pub fn parse_log_ts(line: &str) -> Option<f64> {
    let b = line.as_bytes();
    if b.len() < 18 || b[2] != b'-' || b[5] != b' ' {
        return None;
    }
    let month: u32 = line[0..2].parse().ok()?;
    let day: u32 = line[3..5].parse().ok()?;
    let hour: f64 = line[6..8].parse().ok()?;
    let min: f64 = line[9..11].parse().ok()?;
    let sec: f64 = line[12..14].parse().ok()?;
    let ms: f64 = line[15..18].parse().ok()?;
    Some(ts_key(
        month,
        day,
        hour * 3600.0 + min * 60.0 + sec + ms / 1000.0,
    ))
}

/// 按时间窗切片日志(前后带 buffer 秒)。无时间戳的行跟随上一行归属(续行语义)。
/// 若整段日志一个时间戳都解析不出,如实返回全文加说明头——不假装切了。
pub fn slice_log_by_window(
    dump: &str,
    start: (u32, u32, f64),
    end: (u32, u32, f64),
    buffer_secs: f64,
) -> String {
    let lo = ts_key(start.0, start.1, start.2) - buffer_secs;
    let hi = ts_key(end.0, end.1, end.2) + buffer_secs;
    let mut any_ts = false;
    let mut in_window = false;
    let mut kept: Vec<&str> = Vec::new();
    for line in dump.lines() {
        match parse_log_ts(line) {
            Some(k) => {
                any_ts = true;
                in_window = k >= lo && k <= hi;
                if in_window {
                    kept.push(line);
                }
            }
            None => {
                if in_window {
                    kept.push(line);
                }
            }
        }
    }
    if !any_ts {
        return format!("(警告: 日志行无标准时间戳,无法按窗切片,以下为全文)\n{dump}");
    }
    if kept.is_empty() {
        return format!("(窗口 [{lo:.0}, {hi:.0}] 内无日志行——可能设备日志缓冲区已滚动)\n");
    }
    kept.join("\n")
}

// ══════════════ 遥测前后差值 ══════════════

/// 用例前后遥测对(内存/FD/线程/crash/anr 计数),差值进 telemetry.json。
pub fn telemetry_snapshot(phone: &crate::device::Device, pkg: &str) -> Value {
    let t = phone.telemetry(false, pkg);
    json!({
        "pid": t.pid,
        "vm_rss_kb": t.vm_rss_kb,
        "threads": t.threads,
        "fd_count": t.fd_count,
        "socket_count": t.socket_count,
        "app_pss_kb": t.app_pss_kb,
        "crash_count": t.crash_count,
        "anr_count": t.anr_count,
        "mem_avail_kb": t.mem_avail_kb,
        "cpu_total_pct": t.cpu_total_pct,
    })
}

/// 前后快照差值(纯函数): 数值字段出 delta,缺测如实为 null
pub fn telemetry_diff(before: &Value, after: &Value) -> Value {
    let mut m = serde_json::Map::new();
    m.insert("before".into(), before.clone());
    m.insert("after".into(), after.clone());
    let mut delta = serde_json::Map::new();
    for k in [
        "vm_rss_kb",
        "threads",
        "fd_count",
        "socket_count",
        "app_pss_kb",
        "crash_count",
        "anr_count",
        "mem_avail_kb",
    ] {
        let d = match (before[k].as_i64(), after[k].as_i64()) {
            (Some(b), Some(a)) => Value::from(a - b),
            _ => Value::Null,
        };
        delta.insert(k.to_string(), d);
    }
    m.insert("delta".into(), Value::Object(delta));
    Value::Object(m)
}

// ══════════════ Crash Bundle 打包 ══════════════

/// 文件系统安全的用例目录名(Class#method → Class_method)
pub fn sanitize_case_id(id: &str) -> String {
    id.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

/// 失败用例现场打包: artifacts/<module>/<test_case>/
///   stdout.log        该用例窗口的运行器原始输出
///   hilog_slice.log   前后带缓冲的设备日志切片
///   telemetry.json    前后内存/FD 遥测差值
///   meta.json         判定/耗时/时间锚点
#[allow(clippy::too_many_arguments)]
pub fn write_crash_bundle(
    out_root: &Path,
    module: &str,
    case: &CaseResult,
    stdout_lines: &[String],
    log_dump: &str,
    win_start: (u32, u32, f64),
    win_end: (u32, u32, f64),
    tele: &Value,
) -> Result<PathBuf, String> {
    let dir = out_root
        .join("artifacts")
        .join(sanitize_case_id(module))
        .join(sanitize_case_id(&case.id()));
    fs::create_dir_all(&dir).map_err(|e| format!("建目录失败 {}: {e}", dir.display()))?;

    let case_lines: Vec<&str> = if case.raw_lines.is_empty() {
        stdout_lines.iter().map(|s| s.as_str()).collect()
    } else {
        case.raw_lines.iter().map(|s| s.as_str()).collect()
    };
    fs::write(dir.join("stdout.log"), case_lines.join("\n")).map_err(|e| e.to_string())?;
    fs::write(
        dir.join("hilog_slice.log"),
        slice_log_by_window(log_dump, win_start, win_end, 5.0),
    )
    .map_err(|e| e.to_string())?;
    fs::write(
        dir.join("telemetry.json"),
        serde_json::to_string_pretty(tele).unwrap_or_default(),
    )
    .map_err(|e| e.to_string())?;
    fs::write(
        dir.join("meta.json"),
        serde_json::to_string_pretty(&json!({
            "module": module,
            "case": case.id(),
            "verdict": case.verdict.as_str(),
            "duration_ms": case.duration_ms,
            "window": {"start": win_start, "end": win_end, "buffer_s": 5},
        }))
        .unwrap_or_default(),
    )
    .map_err(|e| e.to_string())?;
    Ok(dir)
}

// ══════════════ 报告: JUnit XML + summary.json ══════════════

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

/// 单模块 JUnit XML(纯函数供单测)。跳过/未运行按 <skipped>,绝不计入通过。
pub fn junit_xml(module: &str, cases: &[CaseResult], wall_ms: u64) -> String {
    let tests = cases.len();
    let failures = cases
        .iter()
        .filter(|c| c.verdict == CaseVerdict::ASSERTION_FAIL)
        .count();
    let errors = cases
        .iter()
        .filter(|c| matches!(c.verdict, CaseVerdict::ENV_BLOCKED | CaseVerdict::TIMEOUT))
        .count();
    let skipped = cases
        .iter()
        .filter(|c| c.verdict == CaseVerdict::NOT_RUN)
        .count();
    let mut s = String::new();
    s.push_str("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n");
    s.push_str(&format!(
        "<testsuite name=\"{}\" tests=\"{}\" failures=\"{}\" errors=\"{}\" skipped=\"{}\" time=\"{:.3}\">\n",
        xml_escape(module), tests, failures, errors, skipped, wall_ms as f64 / 1000.0));
    for c in cases {
        s.push_str(&format!(
            "  <testcase classname=\"{}\" name=\"{}\" time=\"{:.3}\" verdict=\"{}\">",
            xml_escape(&c.class_name),
            xml_escape(&c.method),
            c.duration_ms as f64 / 1000.0,
            c.verdict.as_str()
        ));
        match c.verdict {
            CaseVerdict::PASS => s.push_str("</testcase>\n"),
            CaseVerdict::ASSERTION_FAIL => {
                let msg = c.stack.lines().next().unwrap_or("assertion failed");
                s.push_str(&format!(
                    "\n    <failure message=\"{}\">{}</failure>\n  </testcase>\n",
                    xml_escape(msg),
                    xml_escape(&c.stack)
                ));
            }
            CaseVerdict::NOT_RUN => {
                s.push_str("\n    <skipped/>\n  </testcase>\n");
            }
            _ => {
                let msg = if c.stream.is_empty() {
                    c.verdict.as_str().to_string()
                } else {
                    c.stream.clone()
                };
                s.push_str(&format!(
                    "\n    <error message=\"{}\">{}</error>\n  </testcase>\n",
                    xml_escape(c.verdict.as_str()),
                    xml_escape(&msg)
                ));
            }
        }
    }
    s.push_str("</testsuite>\n");
    s
}

/// 批次汇总(summary.json 的骨架)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModuleReport {
    pub module: String,
    pub package: String,
    pub runner: String,
    pub run_verdict: String,
    pub cases: Vec<CaseResult>,
    pub wall_ms: u64,
    pub retries_used: u32,
    /// 恢复状态: VERIFIED / PENDING(同事流程: PENDING 时不得宣称整轮完成)
    pub recovery: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub crash_bundles: Vec<String>,
}

pub fn summary_json(batch_id: &str, serial: &str, modules: &[ModuleReport], wall_ms: u64) -> Value {
    let mut counts = BTreeMap::new();
    for v in [
        CaseVerdict::PASS,
        CaseVerdict::ASSERTION_FAIL,
        CaseVerdict::ENV_BLOCKED,
        CaseVerdict::TIMEOUT,
        CaseVerdict::NOT_RUN,
    ] {
        counts.insert(v.as_str().to_string(), 0usize);
    }
    let mut total_cases = 0usize;
    for m in modules {
        for c in &m.cases {
            *counts.entry(c.verdict.as_str().to_string()).or_insert(0) += 1;
            total_cases += 1;
        }
    }
    let all_verified = modules.iter().all(|m| m.recovery == "VERIFIED");
    json!({
        "batch_id": batch_id,
        "serial": serial,
        "wall_ms": wall_ms,
        "modules": modules.len(),
        "total_cases": total_cases,
        "verdicts": counts,
        "recovery": if all_verified { "VERIFIED" } else { "PENDING" },
        "module_reports": modules,
    })
}

/// 从历史 summary 中取出某模块的报告(纯函数供单测): 续跑跳过时沿用证据
fn carried_report(prev: &Value, module: &str) -> Option<ModuleReport> {
    prev["module_reports"]
        .as_array()?
        .iter()
        .find(|r| r["module"].as_str() == Some(module))
        .and_then(|r| serde_json::from_value::<ModuleReport>(r.clone()).ok())
}

/// 批次是否"干净收官"(纯函数供单测): 恢复全 VERIFIED 且无失败/阻塞/超时
pub fn batch_is_clean(summary: &Value) -> bool {
    summary["recovery"] == "VERIFIED"
        && summary["verdicts"]["ASSERTION_FAIL"].as_u64() == Some(0)
        && summary["verdicts"]["ENV_BLOCKED"].as_u64() == Some(0)
        && summary["verdicts"]["TIMEOUT"].as_u64() == Some(0)
}

// ══════════════ 批次状态(断点续跑) ══════════════

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct BatchState {
    /// 模块名 → 状态("done" | "failed")
    #[serde(default)]
    pub modules: BTreeMap<String, String>,
}

impl BatchState {
    pub fn load(out_dir: &Path) -> BatchState {
        fs::read_to_string(out_dir.join("batch_state.json"))
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }
    pub fn save(&self, out_dir: &Path) {
        let _ = fs::write(
            out_dir.join("batch_state.json"),
            serde_json::to_string_pretty(self).unwrap_or_default(),
        );
    }
}

// ══════════════ 同事 profile / environment.json 兼容层 ══════════════

/// 同事的 API 测试配置(profile): 一个 API 对应原始 CTS 模块 + Class#method 列表。
/// 未知字段一律容忍(serde 默认),只消费认识的键——对齐而不绑架对方格式演进。
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CtsProfile {
    /// API 名(如 getPackageInfo)
    #[serde(default, alias = "api", alias = "name")]
    pub api_name: String,
    /// 原始 CTS 模块名
    #[serde(default, alias = "cts_module")]
    pub module: String,
    /// 被测包名
    #[serde(default, alias = "target_package", alias = "pkg")]
    pub package: String,
    /// instrumentation runner
    #[serde(default)]
    pub runner: String,
    /// OH(stage model)HAP 模块名(aa test -m);Android 忽略。
    /// 注意不要与上面的 `module`(原始 CTS 模块名,仅作报表标识)混淆。
    #[serde(default, alias = "hap", alias = "oh_module", skip_serializing_if = "Option::is_none")]
    pub hap_module: Option<String>,
    /// 目标平台: "android"(缺省)/ "oh"
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub platform: Option<String>,
    /// Class#method 列表(空=整模块)
    #[serde(default, alias = "methods", alias = "test_cases")]
    pub cases: Vec<String>,
    /// 预期用例数(对账用;缺省=cases.len())
    #[serde(default, alias = "expected_count")]
    pub expected_cases: Option<usize>,
    /// 测试 APK 路径(用于部署;可空=设备上已装)
    #[serde(default, alias = "apk")]
    pub apk_path: Option<String>,
}

/// 同事的环境配置(environment.json): 设备/版本/ABI/夹具。
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CtsEnvironment {
    #[serde(default, alias = "device", alias = "serial")]
    pub device_serial: Option<String>,
    #[serde(default, alias = "runtime_version")]
    pub android_runtime: Option<String>,
    #[serde(default)]
    pub abi: Option<String>,
    #[serde(default, alias = "cts_version")]
    pub cts_or_apk_version: Option<String>,
}

pub fn load_profile(path: &Path) -> Result<CtsProfile, String> {
    let text =
        fs::read_to_string(path).map_err(|e| format!("读不到 profile {}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("profile 解析失败 {}: {e}", path.display()))
}

pub fn load_environment(path: &Path) -> Result<CtsEnvironment, String> {
    let text = fs::read_to_string(path)
        .map_err(|e| format!("读不到 environment {}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("environment 解析失败 {}: {e}", path.display()))
}

// ══════════════ APK 发现与差量部署 ══════════════

/// `pm list instrumentation` 输出解析(纯函数供单测):
///   instrumentation:com.x.test/androidx.test.runner.AndroidJUnitRunner (target=com.x)
/// 返回 (test_pkg, runner, target_pkg) 三元组列表。
pub fn parse_instrumentation_list(s: &str) -> Vec<(String, String, String)> {
    let re = Regex::new(r"instrumentation:([^/\s]+)/(\S+)\s+\(target=([^)]+)\)").unwrap();
    re.captures_iter(s)
        .map(|c| (c[1].to_string(), c[2].to_string(), c[3].to_string()))
        .collect()
}

/// aapt dump badging 的包名解析(纯函数): `package: name='com.x' ...`
pub fn parse_aapt_package(s: &str) -> Option<String> {
    let re = Regex::new(r"package: name='([^']+)'").unwrap();
    re.captures(s).map(|c| c[1].to_string())
}

/// 文件 SHA256(差量传输的判据)
pub fn file_sha256(path: &Path) -> Result<String, String> {
    use sha2::Digest;
    let mut f = fs::File::open(path).map_err(|e| format!("打不开 {}: {e}", path.display()))?;
    let mut h = sha2::Sha256::new();
    let mut buf = [0u8; 65536];
    loop {
        match f.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => h.update(&buf[..n]),
            Err(e) => return Err(format!("读文件失败: {e}")),
        }
    }
    Ok(format!("{:x}", h.finalize()))
}

/// 设备端文件缓存；缓存文件/旧 .sha256 侧车不是当前安装事实。
pub const DEVICE_CACHE_DIR: &str = "/data/local/tmp/pf_cts_cache";

fn install_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

/// CTS-local capture: Device::shell intentionally returns only stdout. Installation
/// needs both the transport status and a fresh remote completion receipt instead.
struct InstallEvidence {
    dir: PathBuf,
    sequence: usize,
}

impl InstallEvidence {
    fn command(
        &mut self,
        label: &str,
        command: &str,
        spawn: impl FnOnce() -> std::io::Result<Child>,
        timeout: Duration,
    ) -> Result<String, String> {
        use std::os::fd::AsRawFd;
        const LIMIT: usize = 1024 * 1024;
        self.sequence += 1;
        let stem = self.dir.join(format!("{:02}-{label}", self.sequence));
        let mut out = fs::File::create(stem.with_extension("stdout")).map_err(|e| e.to_string())?;
        let mut err = fs::File::create(stem.with_extension("stderr")).map_err(|e| e.to_string())?;
        let start = Instant::now();
        let mut status = None;
        let mut output = Vec::new();
        let mut sizes = [0usize; 2];
        let mut failure = None;
        match spawn() {
            Err(e) => failure = Some(format!("spawn failed: {e}")),
            Ok(mut child) => {
                let mut pipes: Vec<Box<dyn Read>> = Vec::new();
                // Nonblocking pipes keep both output and deadlines bounded, even if
                // a descendant retains a pipe after the transport itself exits.
                for fd in [
                    child.stdout.as_ref().map(|p| p.as_raw_fd()),
                    child.stderr.as_ref().map(|p| p.as_raw_fd()),
                ] {
                    match fd {
                        Some(fd)
                            if unsafe { libc::fcntl(fd, libc::F_SETFL, libc::O_NONBLOCK) } >= 0 => {
                        }
                        _ => failure = Some("cannot capture transport pipes".into()),
                    }
                }
                if let Some(pipe) = child.stdout.take() {
                    pipes.push(Box::new(pipe));
                }
                if let Some(pipe) = child.stderr.take() {
                    pipes.push(Box::new(pipe));
                }
                let mut eof = [false; 2];
                while failure.is_none() {
                    for (i, pipe) in pipes.iter_mut().enumerate() {
                        let mut buf = [0u8; 8192];
                        match pipe.read(&mut buf) {
                            Ok(0) => eof[i] = true,
                            Ok(n) => {
                                let keep = n.min(LIMIT.saturating_sub(sizes[i]));
                                let saved = if i == 0 {
                                    out.write_all(&buf[..keep])
                                } else {
                                    err.write_all(&buf[..keep])
                                };
                                if let Err(e) = saved {
                                    failure = Some(format!("cannot preserve output: {e}"));
                                }
                                if i == 0 {
                                    output.extend_from_slice(&buf[..keep]);
                                }
                                sizes[i] += keep;
                                if keep < n {
                                    failure = Some(
                                        "output exceeded 1 MiB per stream; raw prefix preserved"
                                            .into(),
                                    );
                                }
                            }
                            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
                            Err(e) => failure = Some(format!("pipe read failed: {e}")),
                        }
                    }
                    match child.try_wait() {
                        Ok(Some(s)) => status = Some(s),
                        Ok(None) => {}
                        Err(e) => failure = Some(format!("wait failed: {e}")),
                    }
                    if status.is_some() && eof.iter().all(|v| *v) {
                        break;
                    }
                    if start.elapsed() >= timeout {
                        failure = Some("deadline exceeded; remote completion unknown".into());
                    }
                    if failure.is_none() {
                        std::thread::sleep(Duration::from_millis(5));
                    }
                }
                if failure.is_some() && status.is_none() {
                    // Killing a local transport does NOT establish remote rollback.
                    let _ = child.kill();
                    let deadline = Instant::now() + Duration::from_secs(1);
                    while Instant::now() < deadline {
                        match child.try_wait() {
                            Ok(Some(s)) => {
                                status = Some(s);
                                break;
                            }
                            Err(_) => break,
                            Ok(None) => std::thread::sleep(Duration::from_millis(5)),
                        }
                    }
                }
            }
        }
        if failure.is_none() && !status.is_some_and(|s| s.success()) {
            failure = Some("transport/tool returned nonzero or signal status".into());
        }
        let decoded = String::from_utf8(output);
        if failure.is_none() && decoded.is_err() {
            failure = Some("stdout is not UTF-8".into());
        }
        fs::write(stem.with_extension("json"), serde_json::to_vec_pretty(&json!({
            "command": command, "exit_code": status.and_then(|s| s.code()),
            "elapsed_ms": start.elapsed().as_millis(), "stdout_bytes": sizes[0], "stderr_bytes": sizes[1],
            "failure": failure,
        })).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
        match failure {
            Some(reason) => Err(format!("{label}: {reason}; evidence {}", stem.display())),
            None => Ok(decoded.unwrap()),
        }
    }

    fn remote(
        &mut self,
        phone: &crate::device::Device,
        label: &str,
        command: &str,
        seconds: u64,
    ) -> Result<String, String> {
        // A subshell keeps `exit` in the command from bypassing our receipt. The
        // marker is unique to this attempt + command, not a generic Success word.
        let marker = format!(
            "PF_CTS_RC_{}_{}:",
            self.dir.file_name().unwrap().to_string_lossy(),
            self.sequence + 1
        );
        let wrapped = format!("(\n{command}\n); pf_cts_rc=$?; printf '\\n{marker}%s\\n' \"$pf_cts_rc\"; exit \"$pf_cts_rc\"");
        let text = self.command(
            label,
            command,
            || phone.stream_shell(&wrapped),
            Duration::from_secs(seconds),
        )?;
        let mut lines: Vec<&str> = text.lines().collect();
        let receipts: Vec<_> = lines
            .iter()
            .filter(|line| line.starts_with(&marker))
            .collect();
        if receipts.len() != 1 || lines.last().copied() != Some(format!("{marker}0").as_str()) {
            return Err(format!(
                "{label}: missing/invalid remote completion receipt; completion unknown"
            ));
        }
        lines.pop();
        Ok(lines.join("\n").trim_end().to_string())
    }
}

fn installed_apk(
    phone: &crate::device::Device,
    evidence: &mut InstallEvidence,
    package: &str,
) -> Result<Option<(String, String)>, String> {
    let listing = evidence.remote(
        phone,
        "package-list",
        &format!("pm list packages {}", install_quote(package)),
        15,
    )?;
    let mut present = false;
    for line in listing.lines() {
        let name = line
            .strip_prefix("package:")
            .ok_or("uninterpretable installed package list")?;
        if name.is_empty()
            || !name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'.')
        {
            return Err("uninterpretable installed package identity".into());
        }
        present |= name == package;
    }
    if !present {
        return Ok(None);
    }
    let paths = evidence.remote(
        phone,
        "package-path",
        &format!("pm path {}", install_quote(package)),
        15,
    )?;
    let lines: Vec<_> = paths.lines().collect();
    if lines.len() != 1 {
        return Err("installed APK path unknown or split package unsupported by CTS cache".into());
    }
    let path = lines[0]
        .strip_prefix("package:")
        .ok_or("invalid installed APK path")?;
    if !path.starts_with('/') || path.chars().any(char::is_control) {
        return Err("invalid installed APK path".into());
    }
    let digest = evidence.remote(
        phone,
        "installed-sha256",
        &format!("sha256sum {}", install_quote(path)),
        30,
    )?;
    Ok(Some((path.to_string(), install_digest(&digest, path)?)))
}

fn install_digest(output: &str, path: &str) -> Result<String, String> {
    let (hash, tail) = output
        .split_once(char::is_whitespace)
        .ok_or("missing SHA256 receipt")?;
    if hash.len() != 64 || !hash.bytes().all(|b| b.is_ascii_hexdigit()) || tail.trim() != path {
        return Err("uninterpretable SHA256 receipt".into());
    }
    Ok(hash.to_ascii_lowercase())
}

fn deploy_verified_apk(
    phone: &crate::device::Device,
    apk: &Path,
    install_cmd: Option<&str>,
    log_sink: &mut dyn FnMut(&str),
    evidence: &mut InstallEvidence,
) -> Result<(String, bool, String), String> {
    // HDC/OH module execution and cts-fetch remain available. Only this old
    // installer is blocked until a bundle identity/completion contract exists.
    if phone.backend_name() != "adb"
        || install_cmd.is_some_and(|cmd| cmd.trim() != "pm install -r {apk}")
    {
        return Err("ENV_BLOCKED: CTS asset installation supports only Android default pm install -r / --install-cmd 'pm install -r {apk}'. HAP/A2OH/custom templates lack a verified identity/completion adapter; install separately and run an already-installed --module/profile without apk_path, or use the typed games installer for supported Android fresh installs. No installation command was sent.".into());
    }
    if apk.to_str().is_none() || apk.to_string_lossy().chars().any(char::is_control) {
        return Err("APK path must be UTF-8 without control characters".into());
    }
    let local_hash = file_sha256(apk)?;
    // Explicit AAPT2_BIN is authoritative. The legacy aapt fallback must also
    // return a successful, parsed identity; filenames and target packages are not metadata.
    let metadata_tool = if std::env::var_os("AAPT2_BIN").is_some() {
        crate::apk_metadata::tool().ok_or("invalid AAPT2_BIN; APK identity unavailable")?
    } else {
        crate::apk_metadata::tool().unwrap_or_else(|| PathBuf::from("aapt"))
    };
    let metadata = evidence.command(
        "apk-metadata",
        "aapt/aapt2 dump badging (local APK)",
        || {
            std::process::Command::new(&metadata_tool)
                .args(["dump", "badging"])
                .arg(apk)
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
        },
        Duration::from_secs(30),
    )?;
    if metadata
        .lines()
        .filter(|line| line.starts_with("package:"))
        .count()
        != 1
    {
        return Err("APK metadata must contain exactly one package identity".into());
    }
    let package_line = metadata
        .lines()
        .find(|line| line.starts_with("package:"))
        .unwrap();
    let package = parse_aapt_package(package_line).ok_or("APK package identity unavailable")?;
    if package.is_empty()
        || !package
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'.')
    {
        return Err("invalid APK package identity".into());
    }
    if let Some((path, hash)) = installed_apk(phone, evidence, &package)? {
        if hash == local_hash {
            log_sink(&format!(
                "  当前安装已核验: {package} 单 APK SHA256 一致，跳过传输与安装"
            ));
            return Ok((path, false, package));
        }
    }
    // Content-addressed path avoids basename collisions and shell interpolation.
    let remote = format!("{DEVICE_CACHE_DIR}/{local_hash}.apk");
    evidence.remote(
        phone,
        "cache-directory",
        &format!("mkdir -p {}", install_quote(DEVICE_CACHE_DIR)),
        5,
    )?;
    let cached = evidence.remote(
        phone,
        "cache-sha256",
        &format!(
            "if [ -e {p} ]; then sha256sum {p}; else printf 'PF_CTS_CACHE_MISSING\\n'; fi",
            p = install_quote(&remote)
        ),
        30,
    )?;
    if cached != "PF_CTS_CACHE_MISSING" && install_digest(&cached, &remote)? == local_hash {
        log_sink("  远端缓存文件 SHA256 已核验，只复用传输字节；安装事实另查");
    } else {
        log_sink(&format!("  推送 {} → {remote}", apk.display()));
        if !phone.push_file(apk.to_str().unwrap(), &remote) {
            return Err("push did not confirm completion; installation not started".into());
        }
        let digest = evidence.remote(
            phone,
            "pushed-sha256",
            &format!("sha256sum {}", install_quote(&remote)),
            30,
        )?;
        if install_digest(&digest, &remote)? != local_hash {
            return Err("pushed APK SHA256 mismatch; installation not started".into());
        }
    }
    let output = evidence.remote(
        phone,
        "install",
        &format!("pm install -r {}", install_quote(&remote)),
        180,
    )?;
    if output.trim() != "Success" {
        return Err("installation lacks exact Success receipt; installed state unverified, do not automatically retry".into());
    }
    match installed_apk(phone, evidence, &package)? {
        Some((_, hash)) if hash == local_hash => {}
        _ => return Err("installer receipt not confirmed by actual installed APK SHA256; do not automatically retry".into()),
    }
    log_sink(&format!(
        "  安装已核验: {package}，命令回执及当前单 APK SHA256 一致"
    ));
    Ok((remote, true, package))
}

/// 差量安装仅以本轮实际包身份和已安装字节核验为准；旧 hash 侧车不再使用。
/// 每次尝试在 out/installation 留下独立、有限原始输出；未知完成不会自动重试。
pub fn deploy_apk(
    phone: &crate::device::Device,
    apk: &Path,
    install_cmd: Option<&str>,
    log_sink: &mut dyn FnMut(&str),
    out_dir: &Path,
) -> Result<(String, bool, String), String> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static SEQUENCE: AtomicU64 = AtomicU64::new(0);
    let root = out_dir.join("installation");
    fs::create_dir_all(&root).map_err(|e| e.to_string())?;
    let dir = root.join(format!(
        "{}-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&dir).map_err(|e| e.to_string())?;
    let mut evidence = InstallEvidence { dir, sequence: 0 };
    let result = deploy_verified_apk(phone, apk, install_cmd, log_sink, &mut evidence);
    fs::write(
        evidence.dir.join("result.json"),
        serde_json::to_vec_pretty(&json!({
            "apk": apk, "backend": phone.backend_name(), "installed_verified": result.is_ok(),
            "installed_this_attempt": result.as_ref().ok().map(|(_, installed, _)| *installed),
            "package": result.as_ref().ok().map(|(_, _, package)| package),
            "error": result.as_ref().err(), "recovery_verified": false,
        }))
        .map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    result.map_err(|error| format!("{error}; installation evidence {}", evidence.dir.display()))
}

// ══════════════ 掉线恢复记录(不重用旧 PID 纪律) ══════════════

/// 设备 boot_id: 重连后核对——变了说明设备重启过,旧进程身份全部作废。
pub fn device_boot_id(phone: &crate::device::Device) -> String {
    phone
        .shell("cat /proc/sys/kernel/random/boot_id 2>/dev/null", 5000)
        .trim()
        .to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecoveryRecord {
    pub batch_id: String,
    pub boot_id: String,
    pub pending_module: String,
    pub note: String,
    pub ts_ms: u64,
}

pub fn save_recovery(out_dir: &Path, rec: &RecoveryRecord) {
    let _ = fs::write(
        out_dir.join("recovery_pending.json"),
        serde_json::to_string_pretty(rec).unwrap_or_default(),
    );
}

pub fn clear_recovery(out_dir: &Path) {
    let _ = fs::remove_file(out_dir.join("recovery_pending.json"));
}

/// 自愈 Hook: 桥接进程异常/设备失联时调外部恢复脚本,然后核对 boot_id。
/// 返回 (是否恢复到有心跳, 新boot_id)。脚本路径为空=不自愈。
pub fn heal_and_verify(
    phone: &crate::device::Device,
    heal_script: Option<&str>,
    log_sink: &mut dyn FnMut(&str),
) -> (bool, String) {
    if let Some(script) = heal_script {
        log_sink(&format!("  🔧 触发自愈脚本: {script}"));
        let st = std::process::Command::new("sh")
            .arg(script)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        match st {
            Ok(s) if s.success() => log_sink("  自愈脚本执行完成"),
            Ok(s) => log_sink(&format!("  ⚠ 自愈脚本退出码 {s}")),
            Err(e) => log_sink(&format!("  ⚠ 自愈脚本拉起失败: {e}")),
        }
    }
    // 新会话新进程探活(不重用任何旧 PID/旧管道)
    for i in 1..=10 {
        if phone.health_check(5000) {
            let bid = device_boot_id(phone);
            log_sink(&format!("  设备心跳恢复(第{i}次), boot_id={bid}"));
            return (true, bid);
        }
        std::thread::sleep(Duration::from_secs(2));
    }
    (false, String::new())
}

// ══════════════ 批量调度器 ══════════════

pub struct BatchCfg {
    pub serial: Option<String>,
    /// APK 目录扫描模式
    pub apk_dir: Option<String>,
    /// 显式模块列表(pkg/runner),与 apk_dir 二选一
    pub modules: Vec<String>,
    /// 同事 profile(JSON),从中展开 Class#method 切片
    pub profile: Option<String>,
    /// 同事 environment.json
    pub environment: Option<String>,
    pub include: Option<String>,
    pub exclude: Option<String>,
    pub resume: bool,
    pub retry: u32,
    pub timeout_ms: u64,
    pub idle_timeout_ms: u64,
    pub heal_script: Option<String>,
    pub install_cmd: Option<String>,
    pub out_dir: Option<String>,
}

/// 一个待跑模块: 包名 + runner + 该模块下的方法切片(空=整模块)
#[derive(Debug, Clone)]
pub struct ModulePlan {
    package: String,
    runner: String,
    /// OH(stage model)HAP 模块名;Android 为 None
    module: Option<String>,
    /// 目标平台(决定 instrument 命令合成与输出协议)
    platform: Platform,
    methods: Vec<String>,
    apk: Option<PathBuf>,
}

impl ModulePlan {
    fn name(&self) -> String {
        format!("{}/{}", self.package, self.runner)
    }
}

/// 解析 --module 参数 "pkg/runner" 或 "pkg/runner#Class.method"…(纯函数)
pub fn parse_module_arg(s: &str) -> Option<(String, String)> {
    let (pkg, runner) = s.split_once('/')?;
    if pkg.is_empty() || runner.is_empty() {
        return None;
    }
    Some((pkg.to_string(), runner.to_string()))
}

/// 解析 --module 为 ModulePlan(纯函数供单测):
///   "pkg/runner"                → Android(am instrument)
///   "oh:bundle/module/Runner"   → OpenHarmony(aa test;bundle/module/runner 三段,
///                                  module 即 stage model 的 HAP 模块名)
pub fn parse_module_plan(s: &str) -> Result<ModulePlan, String> {
    if let Some(rest) = s.strip_prefix("oh:") {
        let parts: Vec<&str> = rest.split('/').collect();
        if parts.len() != 3 || parts.iter().any(|p| p.is_empty()) {
            return Err(format!(
                "OH 模块格式应为 oh:bundle/module/Runner,收到 '{s}'"
            ));
        }
        return Ok(ModulePlan {
            package: parts[0].to_string(),
            module: Some(parts[1].to_string()),
            runner: parts[2].to_string(),
            platform: Platform::Oh,
            methods: vec![],
            apk: None,
        });
    }
    let (pkg, runner) = parse_module_arg(s)
        .ok_or_else(|| format!("--module 格式应为 pkg/runner 或 oh:bundle/module/Runner,收到 '{s}'"))?;
    Ok(ModulePlan {
        package: pkg,
        module: None,
        runner,
        platform: Platform::Android,
        methods: vec![],
        apk: None,
    })
}

/// profile → ModulePlan(纯函数): cases 列表原样作为 Class#method 切片
pub fn plan_from_profile(p: &CtsProfile) -> Result<ModulePlan, String> {
    if p.package.is_empty() || p.runner.is_empty() {
        return Err("profile 缺 package/runner 字段".into());
    }
    if let Some(exp) = p.expected_cases {
        if exp != p.cases.len() {
            println!(
                "  ⚠ profile 预期 {exp} 用例,实际列出 {} 条——以实列为准并对账",
                p.cases.len()
            );
        }
    }
    // 平台缺省 Android(既有 profile 零改动);写了但不认识的值直接报错,不猜
    let platform = match p.platform.as_deref() {
        None => Platform::Android,
        Some(s) => Platform::parse(s)
            .ok_or_else(|| format!("profile platform 不认识: {s:?}(可选 android / oh)"))?,
    };
    Ok(ModulePlan {
        package: p.package.clone(),
        module: p.hap_module.clone(),
        platform,
        runner: p.runner.clone(),
        methods: p.cases.clone(),
        apk: p.apk_path.as_ref().map(PathBuf::from),
    })
}

/// APK 目录扫描 → ModulePlan 列表: 复用安装阶段实际 metadata 包名，
/// pm list instrumentation 对账 runner;对不上的模块如实 ENV_BLOCKED,不猜。
fn scan_apk_dir(
    phone: &crate::device::Device,
    dir: &Path,
    install_cmd: Option<&str>,
    log_sink: &mut dyn FnMut(&str),
    out_dir: &Path,
) -> Result<Vec<ModulePlan>, String> {
    let mut apks: Vec<PathBuf> = fs::read_dir(dir)
        .map_err(|e| format!("读不到 APK 目录 {}: {e}", dir.display()))?
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().and_then(|x| x.to_str()) == Some("apk"))
        .collect();
    apks.sort();
    if apks.is_empty() {
        return Err(format!("目录 {} 下没有 .apk", dir.display()));
    }

    let instr = parse_instrumentation_list(&phone.shell("pm list instrumentation", 15000));
    let mut plans = Vec::new();
    for apk in apks {
        let (_, installed, package) = deploy_apk(phone, &apk, install_cmd, log_sink, out_dir)?;
        if installed {
            // 新装后 runner 清单可能变化,重查
            let instr2 = parse_instrumentation_list(&phone.shell("pm list instrumentation", 15000));
            for (tp, runner, target) in instr2 {
                if target == package || tp == package {
                    plans.push(ModulePlan {
                        package: target,
                        module: None,
                        platform: Platform::Android,
                        runner,
                        methods: vec![],
                        apk: Some(apk.clone()),
                    });
                }
            }
        } else {
            for (tp, runner, target) in &instr {
                if target == &package || tp == &package {
                    plans.push(ModulePlan {
                        package: target.clone(),
                        module: None,
                        platform: Platform::Android,
                        runner: runner.clone(),
                        methods: vec![],
                        apk: Some(apk.clone()),
                    });
                }
            }
        }
        if !plans
            .iter()
            .any(|p| p.apk.as_deref() == Some(apk.as_path()))
        {
            log_sink(&format!(
                "  ⚠ {} 装上了但 pm list instrumentation 对不出 runner——记 ENV_BLOCKED 待查",
                apk.display()
            ));
        }
    }
    Ok(plans)
}

/// 批次主入口。返回进程退出码(0=全部 VERIFIED 且无 ASSERTION_FAIL/ENV_BLOCKED/TIMEOUT)
pub fn run_batch(cfg: &BatchCfg) -> Result<i32, String> {
    let t0 = Instant::now();
    let batch_id = chrono::Utc::now().format("%Y%m%d-%H%M%S").to_string();
    let out_dir = PathBuf::from(
        cfg.out_dir
            .clone()
            .unwrap_or_else(|| format!("cts-batch-{batch_id}")),
    );
    fs::create_dir_all(&out_dir).map_err(|e| format!("建输出目录失败: {e}"))?;

    // 环境文件对齐(同事 environment.json)
    let mut serial = cfg.serial.clone();
    if let Some(env_path) = &cfg.environment {
        let env = load_environment(Path::new(env_path))?;
        if serial.is_none() {
            serial = env.device_serial.clone();
        }
        println!(
            "环境: serial={:?} runtime={:?} abi={:?} cts={:?}",
            serial, env.android_runtime, env.abi, env.cts_or_apk_version
        );
    }

    let tmp = std::env::temp_dir()
        .join(format!("phonefarm-cts-{}", std::process::id()))
        .to_string_lossy()
        .to_string();
    let _ = fs::create_dir_all(&tmp);
    let phone = crate::device::Device::new(serial.clone(), tmp);

    // 模块计划: profile > --module > --dir
    let mut plans: Vec<ModulePlan> = Vec::new();
    let mut sink = |line: &str| println!("{line}");
    if let Some(pf) = &cfg.profile {
        let profile = load_profile(Path::new(pf))?;
        println!(
            "profile: api={} module={} 切片={}条",
            profile.api_name,
            profile.module,
            profile.cases.len()
        );
        plans.push(plan_from_profile(&profile)?);
    }
    for m in &cfg.modules {
        let plan = parse_module_plan(m)?;
        if plan.platform == Platform::Android && phone.backend_name() == "hdc" {
            println!(
                "  ⚠ 当前设备是 OH(hdc)后端,Android 模块写法 '{m}' 多半跑不通——\n    OH 官方 runner 请用 oh:bundle/module/Runner 三段式"
            );
        }
        plans.push(plan);
    }
    if let Some(dir) = &cfg.apk_dir {
        plans.extend(scan_apk_dir(
            &phone,
            Path::new(dir),
            cfg.install_cmd.as_deref(),
            &mut sink,
            &out_dir,
        )?);
    }
    if plans.is_empty() {
        return Err("没有可跑的模块: 给 --profile / --module / --dir 之一".into());
    }

    // 白/黑名单正则(模块名 pkg/runner)
    let inc = cfg
        .include
        .as_deref()
        .map(Regex::new)
        .transpose()
        .map_err(|e| format!("--include 正则无效: {e}"))?;
    let exc = cfg
        .exclude
        .as_deref()
        .map(Regex::new)
        .transpose()
        .map_err(|e| format!("--exclude 正则无效: {e}"))?;
    plans.retain(|p| {
        let n = p.name();
        inc.as_ref().is_none_or(|r| r.is_match(&n)) && exc.as_ref().is_none_or(|r| !r.is_match(&n))
    });
    if plans.is_empty() {
        return Err("白/黑名单过滤后没有剩余模块".into());
    }

    // 断点续跑
    let mut state = BatchState::load(&out_dir);
    if cfg.resume && !state.modules.is_empty() {
        println!(
            "断点续跑: 跳过已完成 {} 个模块",
            state.modules.values().filter(|s| *s == "done").count()
        );
    }

    // 开局核对: boot_id + 上次未完成的恢复(同事流程"检查"步)
    let boot_id0 = device_boot_id(&phone);
    if out_dir.join("recovery_pending.json").exists() {
        println!("⚠ 发现上次未完成的恢复记录(recovery_pending.json)——先核对设备状态再继续");
        println!("  当前 boot_id={boot_id0}");
    }

    println!("════ CTS 批次 {batch_id} ════");
    println!("输出目录: {}", out_dir.display());
    println!(
        "模块: {} 个 | 重试: {} | 总超时: {}ms | 静默超时: {}ms",
        plans.len(),
        cfg.retry,
        cfg.timeout_ms,
        cfg.idle_timeout_ms
    );

    let mut reports: Vec<ModuleReport> = Vec::new();
    // 续跑跳过 ≠ 抹掉证据: 先读上一份 summary,跳过的模块沿用其历史报告计入总账
    let prev_summary: Option<Value> = if cfg.resume {
        fs::read_to_string(out_dir.join("summary.json"))
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
    } else {
        None
    };
    for plan in &plans {
        let mname = plan.name();
        if cfg.resume && state.modules.get(&mname).map(|s| s.as_str()) == Some("done") {
            match prev_summary
                .as_ref()
                .and_then(|s| carried_report(s, &mname))
            {
                Some(r) => {
                    println!(
                        "── {mname}: 已完成,跳过(沿用历史报告 {} 用例)",
                        r.cases.len()
                    );
                    reports.push(r);
                }
                None => println!("── {mname}: 已完成,跳过(无历史报告可沿用)"),
            }
            continue;
        }
        println!("── 模块 {mname} [{}] (切片 {} 条) ──", plan.platform.as_str(), plan.methods.len());

        // profile 的 APK 尚未部署时(直接给 --profile 而非 --dir),补差量部署
        if let Some(apk) = &plan.apk {
            let _ = deploy_apk(&phone, apk, cfg.install_cmd.as_deref(), &mut sink, &out_dir)?;
        }

        // 遥测通道(被测包)
        let tele_root = phone.telemetry_setup(&plan.package);
        // HDC 无 root 仍可采 hidumper 等公开指标。telemetry_setup 的返回值
        // 表示 root 权限,不能把它当成鸿蒙遥测整体是否可用。
        let tele_ok = tele_root || phone.backend_name() == "hdc";

        // 方法切片列表(空=整模块一次跑)
        let methods: Vec<Option<String>> = if plan.methods.is_empty() {
            vec![None]
        } else {
            plan.methods.iter().map(|m| Some(m.clone())).collect()
        };

        let mut module_cases: Vec<CaseResult> = Vec::new();
        let mut retries_used = 0u32;
        let mut run_verdict = RunVerdict::Completed;
        let mut recovery = "VERIFIED".to_string();
        let mut bundles: Vec<String> = Vec::new();
        let mod_t0 = Instant::now();
        let stdout_path = out_dir.join(format!("{}_stdout.log", sanitize_case_id(&mname)));
        let mut stdout_file = fs::File::create(&stdout_path).map_err(|e| e.to_string())?;
        let mut file_sink = |line: &str| {
            let _ = writeln!(stdout_file, "{line}");
        };

        for method in &methods {
            let spec = InstrumentSpec {
                package: plan.package.clone(),
                runner: plan.runner.clone(),
                module: plan.module.clone(),
                platform: plan.platform,
                class_or_method: method.clone(),
                timeout_ms: cfg.timeout_ms,
                idle_timeout_ms: cfg.idle_timeout_ms,
                env_args: vec![],
            };

            let win_start = device_time_anchor(&phone).unwrap_or((1, 1, 0.0));
            let tele_before = if tele_ok {
                telemetry_snapshot(&phone, &plan.package)
            } else {
                Value::Null
            };
            let run = run_instrument(&phone, &spec, &mut file_sink);
            let win_end = device_time_anchor(&phone).unwrap_or(win_start);
            let tele_after = if tele_ok {
                telemetry_snapshot(&phone, &plan.package)
            } else {
                Value::Null
            };

            if run.verdict != RunVerdict::Completed {
                run_verdict = run.verdict.clone();
            }

            // 桥接进程崩溃 / 看门狗触发 → 掉线纪律: 停写→留记录→自愈→核对 boot_id
            if run.process_crashed || run.verdict != RunVerdict::Completed {
                eprintln!(
                    "  ⚠ {} (crash={})——进入恢复流程",
                    run.verdict.as_str(),
                    run.process_crashed
                );
                save_recovery(
                    &out_dir,
                    &RecoveryRecord {
                        batch_id: batch_id.clone(),
                        boot_id: boot_id0.clone(),
                        pending_module: mname.clone(),
                        note: format!(
                            "verdict={} crashed={}",
                            run.verdict.as_str(),
                            run.process_crashed
                        ),
                        ts_ms: SystemTime::now()
                            .duration_since(UNIX_EPOCH)
                            .map(|d| d.as_millis() as u64)
                            .unwrap_or(0),
                    },
                );
                let (alive, new_boot) =
                    heal_and_verify(&phone, cfg.heal_script.as_deref(), &mut sink);
                if !alive {
                    recovery = "PENDING".into();
                    eprintln!("  ✗ 设备未恢复——本模块剩余切片记 ENV_BLOCKED,批次继续下一个模块");
                    break;
                }
                if !new_boot.is_empty() && new_boot != boot_id0 {
                    println!("  (boot_id 变化: 设备已重启,旧进程身份作废)");
                }
                clear_recovery(&out_dir);
            }

            // 用例归账 + 失败重试(--retry)
            let mut round_cases = run.cases;
            if method.is_some() && cfg.retry > 0 {
                let mut attempt = 0;
                while round_cases.iter().any(|c| {
                    matches!(
                        c.verdict,
                        CaseVerdict::ASSERTION_FAIL | CaseVerdict::TIMEOUT
                    )
                }) && attempt < cfg.retry
                {
                    attempt += 1;
                    retries_used += 1;
                    println!(
                        "    ↻ 重试 {}/{}: {}",
                        attempt,
                        cfg.retry,
                        method.as_deref().unwrap_or("")
                    );
                    let rerun = run_instrument(&phone, &spec, &mut file_sink);
                    if rerun.verdict == RunVerdict::Completed {
                        round_cases = rerun.cases;
                    }
                }
            }
            for c in &round_cases {
                if matches!(
                    c.verdict,
                    CaseVerdict::ASSERTION_FAIL | CaseVerdict::ENV_BLOCKED | CaseVerdict::TIMEOUT
                ) {
                    let dump = device_log_dump(&phone);
                    let tele = telemetry_diff(&tele_before, &tele_after);
                    match write_crash_bundle(
                        &out_dir,
                        &mname,
                        c,
                        &run.stdout_lines,
                        &dump,
                        win_start,
                        win_end,
                        &tele,
                    ) {
                        Ok(dir) => bundles.push(dir.display().to_string()),
                        Err(e) => eprintln!("  ⚠ crash bundle 打包失败: {e}"),
                    }
                }
            }
            module_cases.extend(round_cases);
        }

        // profile 期望用例对账: 缺测补 NOT_RUN(零用例/漏报不得计为通过)
        if !plan.methods.is_empty() {
            for m in &plan.methods {
                let seen = module_cases.iter().any(|c| &c.id() == m);
                if !seen {
                    module_cases.push(CaseResult {
                        class_name: m.split('#').next().unwrap_or("").to_string(),
                        method: m.split('#').nth(1).unwrap_or(m).to_string(),
                        verdict: CaseVerdict::NOT_RUN,
                        stack: String::new(),
                        stream: "profile 列出的用例未产生运行器结果".into(),
                        raw_lines: vec![],
                        duration_ms: 0,
                    });
                }
            }
        }

        // 零用例红线(同事流程: 零用例明确失败,不得静默通过)——整个模块一条结果
        // 都没产出,说明运行器/设备/安装有环境问题,如实记 ENV_BLOCKED
        if module_cases.is_empty() {
            eprintln!("  ✗ 模块 {mname} 零用例——运行器未产出任何结果,记 ENV_BLOCKED");
            module_cases.push(CaseResult {
                class_name: plan.package.clone(),
                method: "(zero-case run)".into(),
                verdict: CaseVerdict::ENV_BLOCKED,
                stack: String::new(),
                stream: "runner produced no case results (device offline / runner missing / install broken)"
                    .into(),
                raw_lines: vec![],
                duration_ms: 0,
            });
        }

        let wall = mod_t0.elapsed().as_millis() as u64;
        let failed = module_cases.iter().any(|c| c.verdict != CaseVerdict::PASS);
        state.modules.insert(
            mname.clone(),
            if failed {
                "failed".into()
            } else {
                "done".into()
            },
        );
        state.save(&out_dir);

        // 模块 JUnit
        let junit = junit_xml(&mname, &module_cases, wall);
        let junit_path = out_dir.join(format!("junit_{}.xml", sanitize_case_id(&mname)));
        let _ = fs::write(&junit_path, &junit);
        println!(
            "  模块收官: {} 用例 wall={:.1}s → {}",
            module_cases.len(),
            wall as f64 / 1000.0,
            junit_path.display()
        );

        reports.push(ModuleReport {
            module: mname,
            package: plan.package.clone(),
            runner: plan.runner.clone(),
            run_verdict: run_verdict.as_str().to_string(),
            cases: module_cases,
            wall_ms: wall,
            retries_used,
            recovery,
            crash_bundles: bundles,
        });
    }

    // 批次报告
    let wall = t0.elapsed().as_millis() as u64;
    let summary = summary_json(&batch_id, serial.as_deref().unwrap_or(""), &reports, wall);
    let summary_path = out_dir.join("summary.json");
    fs::write(
        &summary_path,
        serde_json::to_string_pretty(&summary).unwrap_or_default(),
    )
    .map_err(|e| e.to_string())?;

    let v = &summary["verdicts"];
    println!("════ 批次 {batch_id} 收官 ════");
    println!(
        "  用例 {} | PASS {} | ASSERTION_FAIL {} | ENV_BLOCKED {} | TIMEOUT {} | NOT_RUN {}",
        summary["total_cases"],
        v["PASS"],
        v["ASSERTION_FAIL"],
        v["ENV_BLOCKED"],
        v["TIMEOUT"],
        v["NOT_RUN"]
    );
    println!(
        "  恢复状态: {} | 报告: {}",
        summary["recovery"].as_str().unwrap_or("?"),
        summary_path.display()
    );

    let clean = batch_is_clean(&summary);
    Ok(if clean { 0 } else { 1 })
}

// ══════════════ 结果提取(cts-fetch): 设备上已有 CTS/XTS 结果 → 拉回 + 断言扫描 ══════════════
// 场景: 官方 CTS/XTS 套件(或同事工具)已把结果落在设备上,不想再 hdc shell 进机器翻断言。
// 本命令把结果树拉回本地,对文本类文件做断言扫描,产出 assertions.json + 终端摘要。
// 设备侧只读(find/pull 之外零写入),与 test-batch(自己拉起 instrument)互补。

/// cts-fetch 参数
#[derive(Debug, Clone)]
pub struct FetchCfg {
    pub serial: Option<String>,
    /// 设备侧结果路径(文件或目录)
    pub remote: String,
    /// 本地输出目录(缺省 cts-fetch-<时间戳>)
    pub out: Option<String>,
    /// 断言扫描正则覆盖(缺省 DEFAULT_ASSERTION_PATTERN)
    pub pattern: Option<String>,
    /// 单文件扫描上限(MB,缺省 16)
    pub max_mb: u64,
}

/// 拉回整个结果树: 先整点 pull(adb pull -a / hdc file recv),零产物则 find 枚举
/// 逐文件镜像拉回(hdc file recv 目录递归支持依版本而定)。返回拉到的文件数。
fn pull_tree(
    phone: &crate::device::Device,
    remote: &str,
    local_root: &Path,
    log: &mut dyn FnMut(&str),
) -> Result<usize, String> {
    if phone.pull(remote, &local_root.to_string_lossy()) {
        let n = walk_files(local_root).len();
        if n > 0 {
            return Ok(n);
        }
    }
    log(&format!("  整点拉取无产物,改用 find 枚举逐文件拉回: {remote}"));
    let listing = phone.shell(&format!("find '{remote}' -type f 2>/dev/null"), 30_000);
    let prefix = remote.trim_end_matches('/');
    let mut n = 0usize;
    for line in listing.lines() {
        let r = line.trim();
        if !r.starts_with('/') {
            continue;
        }
        let rel = r
            .strip_prefix(prefix)
            .unwrap_or(r)
            .trim_start_matches('/')
            .to_string();
        let dest = local_root.join(&rel);
        if let Some(p) = dest.parent() {
            let _ = fs::create_dir_all(p);
        }
        if phone.pull(r, &dest.to_string_lossy()) && dest.is_file() {
            n += 1;
        } else {
            log(&format!("    ⚠ 拉取失败(跳过): {r}"));
        }
    }
    Ok(n)
}

/// 递归收集文件(单文件入参 → 返回它自己);同名排序保证扫描顺序确定
fn walk_files(p: &Path) -> Vec<PathBuf> {
    if p.is_file() {
        return vec![p.to_path_buf()];
    }
    let mut out = Vec::new();
    if let Ok(rd) = fs::read_dir(p) {
        let mut entries: Vec<PathBuf> = rd.flatten().map(|e| e.path()).collect();
        entries.sort();
        for e in entries {
            if e.is_dir() {
                out.extend(walk_files(&e));
            } else if e.is_file() {
                out.push(e);
            }
        }
    }
    out
}

/// 默认断言扫描模式(junit failure/error、断言异常族、本工具 verdict 行、
/// instrument/OH 收官负码、套件级失败计数)。--pattern 可整体覆盖。
pub const DEFAULT_ASSERTION_PATTERN: &str = concat!(
    r#"(?i)(assertionfailederror|assertionerror|comparisonfailure|junit\.framework\.assert|"#,
    r#"<failure\b|<error\b|"#,
    r#""verdict"\s*:\s*"(ASSERTION_FAIL|ENV_BLOCKED|TIMEOUT|NOT_RUN)"|"#,
    r#"OHOS_REPORT_CODE: -|INSTRUMENTATION_STATUS_CODE: -|"#,
    r#"FAILURES!!!|failures="[1-9]|errors="[1-9]|Failures: [1-9]|Errors: [1-9])"#
);

/// 断言扫描结果(可序列化进 assertions.json)
#[derive(Debug, Clone, Serialize)]
pub struct AssertionScan {
    pub scanned_files: usize,
    pub skipped_files: usize,
    pub total_matches: usize,
    pub files: Vec<FileAssertions>,
}

#[derive(Debug, Clone, Serialize)]
pub struct FileAssertions {
    pub path: String,
    pub truncated: bool,
    pub hits: Vec<AssertionHit>,
}

#[derive(Debug, Clone, Serialize)]
pub struct AssertionHit {
    pub line: u64,
    pub text: String,
}

/// 对本地目录(树)做断言扫描(供单测): 文本类文件逐行匹配 pattern。
/// 跳过: 超过 max_file_bytes、二进制嗅探(首 8KB 含 NUL)、媒体/归档扩展名。
/// 每文件命中上限 500 行(truncated 标记),文件数上限 5000。
pub fn scan_assertions(root: &Path, pattern: &Regex, max_file_bytes: u64) -> AssertionScan {
    const MAX_HITS_PER_FILE: usize = 500;
    const MAX_FILES: usize = 5000;
    const SCAN_SKIP_EXT: &[&str] = &[
        "png", "jpg", "jpeg", "gif", "webp", "bmp", "mp4", "mp3", "zip", "gz", "tgz",
        "tar", "so", "apk", "hap", "bin", "pdf", "dex", "jar", "abc", "wav", "ogg",
    ];
    let mut scan = AssertionScan {
        scanned_files: 0,
        skipped_files: 0,
        total_matches: 0,
        files: vec![],
    };
    for f in walk_files(root) {
        if scan.scanned_files >= MAX_FILES {
            break;
        }
        let ext = f
            .extension()
            .and_then(|e| e.to_str())
            .map(|s| s.to_ascii_lowercase());
        if ext.as_deref().map_or(false, |e| SCAN_SKIP_EXT.contains(&e)) {
            scan.skipped_files += 1;
            continue;
        }
        let ok = fs::metadata(&f).map(|m| m.len() <= max_file_bytes).unwrap_or(false);
        let bytes = if ok { fs::read(&f).unwrap_or_default() } else { vec![] };
        if !ok || bytes.iter().take(8192).any(|&b| b == 0) {
            scan.skipped_files += 1;
            continue;
        }
        let mut hits = Vec::new();
        let mut truncated = false;
        for (i, line) in String::from_utf8_lossy(&bytes).lines().enumerate() {
            if pattern.is_match(line) {
                if hits.len() >= MAX_HITS_PER_FILE {
                    truncated = true;
                    break;
                }
                let t: String = line.trim().chars().take(500).collect();
                hits.push(AssertionHit { line: (i + 1) as u64, text: t });
            }
        }
        scan.scanned_files += 1;
        if !hits.is_empty() {
            scan.total_matches += hits.len();
            scan.files.push(FileAssertions {
                path: f.to_string_lossy().to_string(),
                truncated,
                hits,
            });
        }
    }
    scan
}

/// cts-fetch 主入口: 拉回 → 扫描 → assertions.json + 终端摘要。返回退出码(0=成功)。
pub fn run_fetch(cfg: &FetchCfg) -> Result<i32, String> {
    let t0 = Instant::now();
    let batch_id = chrono::Utc::now().format("%Y%m%d-%H%M%S").to_string();
    let out_dir = PathBuf::from(
        cfg.out
            .clone()
            .unwrap_or_else(|| format!("cts-fetch-{batch_id}")),
    );
    fs::create_dir_all(&out_dir).map_err(|e| format!("建输出目录失败: {e}"))?;
    let raw_root = out_dir.join("raw");

    let tmp = std::env::temp_dir()
        .join(format!("phonefarm-ctsfetch-{}", std::process::id()))
        .to_string_lossy()
        .to_string();
    let _ = fs::create_dir_all(&tmp);
    let phone = crate::device::Device::new(cfg.serial.clone(), tmp);
    let backend = phone.backend_name();
    if !phone.health_check(8000) {
        return Err(format!(
            "设备无心跳(serial={:?} backend={backend})——先 phonefarm devices 核对",
            cfg.serial
        ));
    }

    println!("════ CTS 结果提取 {batch_id} ════");
    println!("设备: {:?} [{backend}] | 设备侧路径: {}", cfg.serial, cfg.remote);
    let mut sink = |line: &str| println!("{line}");
    let n = pull_tree(&phone, &cfg.remote, &raw_root, &mut sink)?;
    if n == 0 {
        return Err(format!(
            "设备上拉不到任何文件: {} 不存在或为空(设备侧 find 无产物)",
            cfg.remote
        ));
    }
    println!("拉回 {n} 个文件 → {}", raw_root.display());

    let pattern_src = cfg
        .pattern
        .clone()
        .unwrap_or_else(|| DEFAULT_ASSERTION_PATTERN.to_string());
    let pattern = Regex::new(&pattern_src).map_err(|e| format!("--pattern 正则无效: {e}"))?;
    let max_bytes = cfg.max_mb.saturating_mul(1024 * 1024).max(1);
    let scan = scan_assertions(&raw_root, &pattern, max_bytes);

    // phonefarm 自产报告提示(拉回的若是本工具的批次目录,直接点出报告位置)
    let pf_reports: Vec<String> = walk_files(&raw_root)
        .iter()
        .filter(|p| {
            let name = p.file_name().and_then(|s| s.to_str()).unwrap_or("");
            name == "summary.json" || name.starts_with("junit_")
        })
        .map(|p| p.to_string_lossy().to_string())
        .collect();

    println!(
        "断言扫描: {} 处命中 / {} 个文件(扫描 {} 跳过 {})",
        scan.total_matches,
        scan.files.len(),
        scan.scanned_files,
        scan.skipped_files
    );
    for fa in scan.files.iter().take(10) {
        println!(
            "  ▸ {} ({} 处{})",
            fa.path,
            fa.hits.len(),
            if fa.truncated { ",已截断" } else { "" }
        );
        for h in fa.hits.iter().take(3) {
            println!("      L{}: {}", h.line, h.text);
        }
    }
    if scan.files.len() > 10 {
        println!("  … 其余 {} 个文件详见 assertions.json", scan.files.len() - 10);
    }
    for r in &pf_reports {
        println!("  📦 含 phonefarm 批次报告: {r}");
    }

    let report = json!({
        "batch_id": batch_id,
        "serial": cfg.serial.clone().unwrap_or_default(),
        "backend": backend,
        "remote": cfg.remote,
        "pattern": pattern_src,
        "wall_ms": t0.elapsed().as_millis() as u64,
        "pulled_files": n,
        "scan": scan,
    });
    let report_path = out_dir.join("assertions.json");
    fs::write(
        &report_path,
        serde_json::to_string_pretty(&report).unwrap_or_default(),
    )
    .map_err(|e| format!("写报告失败: {e}"))?;
    println!(
        "报告: {} ({:.1}s)",
        report_path.display(),
        t0.elapsed().as_secs_f64()
    );
    Ok(0)
}

// ══════════════ 单测 ══════════════

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE_RUN: &str = r"INSTRUMENTATION_STATUS: numtests=2
INSTRUMENTATION_STATUS: stream=
android.content.pm.cts.PackageManagerTest:
INSTRUMENTATION_STATUS: id=AndroidJUnitRunner
INSTRUMENTATION_STATUS: test=testGetPackageInfo
INSTRUMENTATION_STATUS: class=android.content.pm.cts.PackageManagerTest
INSTRUMENTATION_STATUS: current=1
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS: stream=.
INSTRUMENTATION_STATUS: id=AndroidJUnitRunner
INSTRUMENTATION_STATUS: test=testGetPackageInfo
INSTRUMENTATION_STATUS: class=android.content.pm.cts.PackageManagerTest
INSTRUMENTATION_STATUS: current=1
INSTRUMENTATION_STATUS_CODE: 0
INSTRUMENTATION_STATUS: numtests=2
INSTRUMENTATION_STATUS: stream=
INSTRUMENTATION_STATUS: id=AndroidJUnitRunner
INSTRUMENTATION_STATUS: test=testGetApplicationInfo
INSTRUMENTATION_STATUS: class=android.content.pm.cts.PackageManagerTest
INSTRUMENTATION_STATUS: current=2
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS: stack=junit.framework.AssertionFailedError: expected:<1> but was:<2>
	at junit.framework.Assert.fail(Assert.java:50)
	at android.content.pm.cts.PackageManagerTest.testGetApplicationInfo(PackageManagerTest.java:99)
INSTRUMENTATION_STATUS: id=AndroidJUnitRunner
INSTRUMENTATION_STATUS: test=testGetApplicationInfo
INSTRUMENTATION_STATUS: class=android.content.pm.cts.PackageManagerTest
INSTRUMENTATION_STATUS: current=2
INSTRUMENTATION_STATUS_CODE: -2
INSTRUMENTATION_RESULT: stream=

Time: 3.141
There was 1 failure:
FAILURES!!!
INSTRUMENTATION_CODE: -1
";

    #[test]
    fn parser_streams_case_results_before_run_end() {
        let mut p = InstrumentParser::new();
        let mut started = 0;
        let mut finished: Vec<CaseResult> = Vec::new();
        let mut run_end = false;
        for line in SAMPLE_RUN.lines() {
            for ev in p.feed_line(line) {
                match ev {
                    ParseEvent::CaseStarted { .. } => started += 1,
                    ParseEvent::CaseFinished(c) => finished.push(c),
                    ParseEvent::RunFinished { code, tail } => {
                        run_end = true;
                        assert_eq!(code, -1);
                        assert!(
                            tail.iter().any(|t| t.contains("FAILURES!!!")),
                            "整轮尾部要留住 FAILURES 原文"
                        );
                    }
                }
            }
            // 流式性: 第一个用例的 PASS 必须在 RunFinished 之前产出
            if !run_end && finished.len() == 1 {
                assert_eq!(finished[0].verdict, CaseVerdict::PASS);
            }
        }
        assert_eq!(started, 2);
        assert_eq!(finished.len(), 2);
        assert_eq!(
            finished[0].class_name,
            "android.content.pm.cts.PackageManagerTest"
        );
        assert_eq!(finished[0].method, "testGetPackageInfo");
        assert_eq!(finished[0].verdict, CaseVerdict::PASS);
        assert_eq!(finished[1].verdict, CaseVerdict::ASSERTION_FAIL);
        assert!(
            finished[1].stack.contains("AssertionFailedError"),
            "失败用例要带 Java 调用栈"
        );
        assert!(
            finished[1].stack.contains("PackageManagerTest.java:99"),
            "多行 stack 续行要拼全"
        );
        assert!(run_end);
    }

    #[test]
    fn parser_marks_skipped_as_not_run_never_pass() {
        // code -3 (ignored/skipped) → NOT_RUN,绝不计 PASS(同事流程: 跳过不算通过)
        let mut p = InstrumentParser::new();
        let mut out = Vec::new();
        for line in [
            "INSTRUMENTATION_STATUS: test=testSkipped",
            "INSTRUMENTATION_STATUS: class=com.x.FooTest",
            "INSTRUMENTATION_STATUS_CODE: 1",
            "INSTRUMENTATION_STATUS_CODE: -3",
        ] {
            out.extend(p.feed_line(line));
        }
        let fin = out.iter().find_map(|e| match e {
            ParseEvent::CaseFinished(c) => Some(c),
            _ => None,
        });
        assert_eq!(fin.map(|c| c.verdict), Some(CaseVerdict::NOT_RUN));
    }

    #[test]
    fn instrument_command_builds_method_slice() {
        let spec = InstrumentSpec {
            package: "android.content.cts".into(),
            runner: "androidx.test.runner.AndroidJUnitRunner".into(),
            module: None,
            platform: Platform::Android,
            class_or_method: Some(
                "android.content.pm.cts.PackageManagerTest#testGetPackageInfo".into(),
            ),
            timeout_ms: 1,
            idle_timeout_ms: 1,
            env_args: vec![("timeout_msec".into(), "30000".into())],
        };
        let cmd = instrument_command(&spec);
        assert!(cmd.starts_with("am instrument -r -w"));
        assert!(cmd.contains("-e timeout_msec 30000"));
        assert!(
            cmd.contains("-e class android.content.pm.cts.PackageManagerTest#testGetPackageInfo")
        );
        assert!(cmd.ends_with(" android.content.cts/androidx.test.runner.AndroidJUnitRunner"));

        // 相对 runner 补包名;无切片时无 -e class
        let spec2 = InstrumentSpec {
            package: "com.x".into(),
            runner: ".MyRunner".into(),
            module: None,
            platform: Platform::Android,
            class_or_method: None,
            timeout_ms: 0,
            idle_timeout_ms: 0,
            env_args: vec![],
        };
        let cmd2 = instrument_command(&spec2);
        assert!(cmd2.ends_with(" com.x/..MyRunner") || cmd2.ends_with(" com.x/.MyRunner"));
        assert!(!cmd2.contains("-e class"));
    }

    #[test]
    fn log_ts_and_window_slice() {
        let dump = "09-08 10:00:00.000  1000  1000 I early: before window\n\
                    09-08 10:00:05.000  1000  1000 E bridge: deadlock detected\n\
                        at com.bridge.Native.wait(Native method)\n\
                    09-08 10:00:20.000  1000  1000 I late: after window\n\
                    no timestamp header line\n";
        // 窗口 10:00:04 ~ 10:00:06, buffer 0 → 只留中间行与其续行
        let s = slice_log_by_window(
            dump,
            (9, 8, 10.0 * 3600.0 + 4.0),
            (9, 8, 10.0 * 3600.0 + 6.0),
            0.0,
        );
        assert!(s.contains("deadlock detected"));
        assert!(s.contains("Native method"), "无时间戳续行跟随归属");
        assert!(!s.contains("before window"));
        assert!(!s.contains("after window"));
        // 解析不出时间戳的全文 → 如实带警告头
        let s2 = slice_log_by_window("garbage\nlines\n", (1, 1, 0.0), (1, 1, 1.0), 0.0);
        assert!(s2.starts_with("(警告: 日志行无标准时间戳"));
        // 窗口内无行 → 如实说明,不空文件冒充
        let s3 = slice_log_by_window(dump, (9, 9, 0.0), (9, 9, 1.0), 0.0);
        assert!(s3.contains("无日志行"));
    }

    #[test]
    fn junit_maps_verdicts_and_never_counts_pass_wrong() {
        let cases = vec![
            CaseResult { class_name: "C".into(), method: "m1".into(), verdict: CaseVerdict::PASS,
                stack: String::new(), stream: String::new(), raw_lines: vec![], duration_ms: 100 },
            CaseResult { class_name: "C".into(), method: "m2".into(), verdict: CaseVerdict::ASSERTION_FAIL,
                stack: "junit.framework.AssertionFailedError: expected:<1> but was:<2>\n\tat C.m2(C.java:1)".into(),
                stream: String::new(), raw_lines: vec![], duration_ms: 200 },
            CaseResult { class_name: "C".into(), method: "m3".into(), verdict: CaseVerdict::TIMEOUT,
                stack: String::new(), stream: "watchdog".into(), raw_lines: vec![], duration_ms: 90000 },
            CaseResult { class_name: "C".into(), method: "m4".into(), verdict: CaseVerdict::NOT_RUN,
                stack: String::new(), stream: String::new(), raw_lines: vec![], duration_ms: 0 },
        ];
        let x = junit_xml("mod", &cases, 1000);
        assert!(x.contains("tests=\"4\""));
        assert!(x.contains("failures=\"1\""));
        assert!(
            x.contains("errors=\"1\""),
            "TIMEOUT 计入 errors 而非 failures"
        );
        assert!(x.contains("skipped=\"1\""), "NOT_RUN 计 skipped,绝不算通过");
        assert!(x.contains("<failure message=\"junit.framework.AssertionFailedError: expected:&lt;1&gt; but was:&lt;2&gt;\">"));
        assert!(x.contains("verdict=\"PASS\""));
        assert!(x.contains("&lt;"), "XML 转义");
    }

    #[test]
    fn summary_uses_colleague_verdict_literals() {
        let m = ModuleReport {
            module: "m".into(),
            package: "p".into(),
            runner: "r".into(),
            run_verdict: "Completed".into(),
            cases: vec![CaseResult {
                class_name: "C".into(),
                method: "m".into(),
                verdict: CaseVerdict::PASS,
                stack: String::new(),
                stream: String::new(),
                raw_lines: vec![],
                duration_ms: 1,
            }],
            wall_ms: 1,
            retries_used: 0,
            recovery: "VERIFIED".into(),
            crash_bundles: vec![],
        };
        let s = summary_json("b1", "ser", &[m], 10);
        for k in [
            "PASS",
            "ASSERTION_FAIL",
            "ENV_BLOCKED",
            "TIMEOUT",
            "NOT_RUN",
        ] {
            assert!(s["verdicts"].get(k).is_some(), "summary 缺判定键 {k}");
        }
        assert_eq!(s["verdicts"]["PASS"], 1);
        assert_eq!(s["recovery"], "VERIFIED");
        // 全 PASS + VERIFIED → 干净(退出码 0)
        assert!(batch_is_clean(&s));
        // 混入任一 ENV_BLOCKED → 不干净(退出码 1),零用例红线同理
        let m2 = ModuleReport {
            module: "m2".into(),
            package: "p".into(),
            runner: "r".into(),
            run_verdict: "Completed".into(),
            cases: vec![CaseResult {
                class_name: "C".into(),
                method: "x".into(),
                verdict: CaseVerdict::ENV_BLOCKED,
                stack: String::new(),
                stream: String::new(),
                raw_lines: vec![],
                duration_ms: 0,
            }],
            wall_ms: 1,
            retries_used: 0,
            recovery: "VERIFIED".into(),
            crash_bundles: vec![],
        };
        assert!(
            !batch_is_clean(&summary_json("b2", "ser", &[m2], 10)),
            "ENV_BLOCKED 必须让批次判不干净(退出码 1)"
        );
    }

    // An isolated child owns ADB_BIN/AAPT2_BIN. No process-global environment
    // mutation races other tests, and no discovered real transport can be used.
    #[test]
    fn cts_install_completion_and_cache() {
        use std::os::unix::fs::PermissionsExt;
        use std::process::Command;
        const CHILD: &str = "PF_CTS_INSTALL_FIXTURE";
        if let Some(root) = std::env::var_os(CHILD) {
            let root = PathBuf::from(root);
            let apk = root.join("inputs/a quote's package.apk");
            let phone = crate::device::Device::new(
                Some("cts-fake-only".into()),
                root.to_string_lossy().into(),
            );
            let mode = root.join("mode");
            let installed = root.join("installed.apk");
            let log = root.join("transport.jsonl");
            let run = |label: &str, command: Option<&str>| {
                fs::write(&mode, label).unwrap();
                let out = root.join(label);
                deploy_apk(&phone, &apk, command, &mut |_| {}, &out)
            };
            for label in [
                "silent-nonzero",
                "success-nonzero",
                "silent-zero",
                "no-receipt",
                "success-no-package",
                "bad-metadata",
            ] {
                let result = run(label, None);
                assert!(
                    result.is_err(),
                    "{label} must not claim installed: {result:?}"
                );
                assert!(
                    !installed.exists(),
                    "{label} fixture unexpectedly installed"
                );
                println!("{label}: {}", result.unwrap_err());
            }
            let before = fs::read(&log).unwrap();
            assert!(run("custom-blocked", Some("bm install -p {apk}"))
                .unwrap_err()
                .contains("ENV_BLOCKED"));
            assert_eq!(
                fs::read(&log).unwrap(),
                before,
                "unsupported template must not reach transport"
            );
            let hdc = crate::device::Device::new(
                Some("hdc:cts-fake-only".into()),
                root.to_string_lossy().into(),
            );
            assert!(deploy_apk(
                &hdc,
                &apk,
                Some("bm install -p {apk}"),
                &mut |_| {},
                &root.join("hdc-blocked")
            )
            .unwrap_err()
            .contains("ENV_BLOCKED"));
            assert_eq!(
                fs::read(&log).unwrap(),
                before,
                "HDC installer must not launch hdc"
            );

            // A legacy sidecar survives but the package does not. It cannot bypass install.
            fs::write(
                root.join("cache").join("a quote's package.apk.sha256"),
                file_sha256(&apk).unwrap(),
            )
            .unwrap();
            let first = run("good", Some("pm install -r {apk}")).unwrap();
            assert!(first.1);
            assert_eq!(fs::read(&installed).unwrap(), fs::read(&apk).unwrap());
            let install_count = || {
                fs::read_to_string(root.join("install-count"))
                    .unwrap()
                    .lines()
                    .count()
            };
            assert_eq!(
                install_count(),
                6,
                "five attempted negative installs plus one verified install"
            );
            let second = run("reuse-installed", None).unwrap();
            assert!(!second.1);
            assert_eq!(second.0, installed.to_string_lossy());
            assert_eq!(
                install_count(),
                6,
                "actual installed identity permits skipping install"
            );
            fs::remove_file(&installed).unwrap();
            assert!(run("removed-package", None).unwrap().1);
            assert_eq!(
                install_count(),
                7,
                "cached file must not stand in for current package existence"
            );
            assert_eq!(
                fs::read_to_string(root.join("push-count"))
                    .unwrap()
                    .lines()
                    .count(),
                1,
                "verified transfer bytes are reused independently"
            );
            let plans = scan_apk_dir(
                &phone,
                &root.join("inputs"),
                None,
                &mut |_| {},
                &root.join("scan"),
            )
            .unwrap();
            assert_eq!(
                plans.len(),
                1,
                "directory scan must reuse metadata, not guess the quoted filename"
            );
            assert_eq!(plans[0].package, "com.example.cts");
            assert_eq!(
                install_count(),
                7,
                "directory scan rechecks installed bytes without reinstalling"
            );
            let failures = root.join("silent-nonzero/installation");
            let attempt = fs::read_dir(failures)
                .unwrap()
                .next()
                .unwrap()
                .unwrap()
                .path();
            let entries: Vec<_> = fs::read_dir(attempt)
                .unwrap()
                .map(|e| e.unwrap().path())
                .collect();
            let stderr = entries
                .iter()
                .find(|p| {
                    p.file_name()
                        .unwrap()
                        .to_string_lossy()
                        .ends_with("-install.stderr")
                })
                .unwrap();
            assert_eq!(
                fs::read_to_string(stderr).unwrap(),
                "installer diagnostic\n"
            );
            let status = entries
                .iter()
                .find(|p| {
                    p.file_name()
                        .unwrap()
                        .to_string_lossy()
                        .ends_with("-install.json")
                })
                .unwrap();
            let status: Value = serde_json::from_slice(&fs::read(status).unwrap()).unwrap();
            assert_eq!(status["exit_code"], 7);
            // Exercise bounded capture itself with real host processes, including
            // a zero-output command: it still has no installation success receipt.
            let bounded = root.join("bounded");
            fs::create_dir(&bounded).unwrap();
            let mut evidence = InstallEvidence {
                dir: bounded.clone(),
                sequence: 0,
            };
            let timeout = evidence.command(
                "timeout",
                "host sleep fixture",
                || {
                    Command::new("sh")
                        .args(["-c", "exec sleep 2"])
                        .stdout(Stdio::piped())
                        .stderr(Stdio::piped())
                        .spawn()
                },
                Duration::from_millis(25),
            );
            assert!(timeout.unwrap_err().contains("deadline exceeded"));
            let limit = evidence.command(
                "limit",
                "host output limit fixture",
                || {
                    Command::new("sh")
                        .args(["-c", "exec head -c 1056768 /dev/zero"])
                        .stdout(Stdio::piped())
                        .stderr(Stdio::piped())
                        .spawn()
                },
                Duration::from_secs(5),
            );
            assert!(limit.unwrap_err().contains("1 MiB"));
            assert_eq!(
                fs::metadata(bounded.join("02-limit.stdout")).unwrap().len(),
                1024 * 1024
            );
            return;
        }
        let base = std::env::var_os("PF_CTS_QA_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(std::env::temp_dir);
        let root = base.join(format!(
            "cts-install-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(root.join("cache")).unwrap();
        fs::create_dir(root.join("inputs")).unwrap();
        fs::write(
            root.join("inputs/a quote's package.apk"),
            b"synthetic APK bytes, not an installable Android package",
        )
        .unwrap();
        let fixture = root.join("fixture.py");
        fs::write(
            &fixture,
            r##"#!/usr/bin/env python3
import hashlib, json, os, pathlib, subprocess, sys
root = pathlib.Path(os.environ['PF_CTS_INSTALL_FIXTURE'])
mode = (root / 'mode').read_text() if (root / 'mode').exists() else 'good'
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
installed = root / 'installed.apk'
if name == 'aapt2':
    print("package: name='com.example.cts' versionCode='1' versionName='1'")
    sys.exit(9 if mode == 'bad-metadata' else 0)
if name == 'pm':
    if args[:2] == ['list', 'packages']:
        if installed.exists(): print('package:com.example.cts')
    elif args[:2] == ['list', 'instrumentation']:
        if installed.exists(): print('instrumentation:com.example.cts/androidx.test.runner.AndroidJUnitRunner (target=com.example.cts)')
    elif args[0] == 'path':
        if installed.exists(): print('package:' + str(installed))
    elif args[:2] == ['install', '-r']:
        with (root / 'install-count').open('a') as f: f.write(mode + '\n')
        if mode == 'silent-nonzero':
            print('installer diagnostic', file=sys.stderr)
            sys.exit(7)
        if mode == 'success-nonzero':
            print('Success')
            sys.exit(7)
        if mode == 'silent-zero': sys.exit(0)
        if mode != 'success-no-package': installed.write_bytes(pathlib.Path(args[2]).read_bytes())
        print('Success')
    else: sys.exit(90)
    sys.exit(0)
if name == 'sha256sum':
    path = pathlib.Path(args[0])
    display = str(path).replace(str(root / 'cache'), '/data/local/tmp/pf_cts_cache')
    print(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + display)
    sys.exit(0)
assert name == 'adb', name
assert args[:2] == ['-s', 'cts-fake-only'], args
args = args[2:]
with (root / 'transport.jsonl').open('a') as f: f.write(json.dumps(args) + '\n')
def local(value): return value.replace('/data/local/tmp/pf_cts_cache', str(root / 'cache'))
if args[0] == 'push':
    pathlib.Path(local(args[2])).write_bytes(pathlib.Path(args[1]).read_bytes())
    with (root / 'push-count').open('a') as f: f.write('push\n')
elif args[:3] == ['shell', 'stat', '-c']:
    print(pathlib.Path(local(args[4])).stat().st_size)
elif args[0] == 'shell' and len(args) == 2:
    if mode == 'no-receipt' and '\npm install ' in args[1]:
        with (root / 'install-count').open('a') as f: f.write(mode + '\n')
        print('Success')
    else:
        env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'])
        result = subprocess.run(['sh', '-c', local(args[1])], env=env)
        sys.exit(result.returncode)
else: sys.exit(91)
"##,
        )
        .unwrap();
        fs::set_permissions(&fixture, fs::Permissions::from_mode(0o755)).unwrap();
        for name in ["adb", "aapt2", "pm", "sha256sum"] {
            fs::copy(&fixture, root.join(name)).unwrap();
        }
        let output = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "cts::tests::cts_install_completion_and_cache",
                "--nocapture",
            ])
            .env(CHILD, &root)
            .env("ADB_BIN", root.join("adb"))
            .env("AAPT2_BIN", root.join("aapt2"))
            .output()
            .unwrap();
        fs::write(root.join("test.stdout"), &output.stdout).unwrap();
        fs::write(root.join("test.stderr"), &output.stderr).unwrap();
        println!("CTS installation fixture evidence: {}", root.display());
        assert!(
            output.status.success(),
            "child failed: {}\n{}\nevidence {}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr),
            root.display()
        );
        // Explicit QA output, and all failed runs, remain reviewable.
        if std::env::var_os("PF_CTS_QA_DIR").is_none() {
            fs::remove_dir_all(root).unwrap();
        }
    }

    #[test]
    fn pm_instrumentation_and_aapt_parsing() {
        let s = "instrumentation:android.content.cts/androidx.test.runner.AndroidJUnitRunner (target=android.content.cts)\n\
                 instrumentation:com.x.test/com.x.MyRunner (target=com.x)\n";
        let v = parse_instrumentation_list(s);
        assert_eq!(v.len(), 2);
        assert_eq!(v[0].1, "androidx.test.runner.AndroidJUnitRunner");
        assert_eq!(v[1].2, "com.x");
        let aapt = "package: name='com.x.test' versionCode='1' versionName='1.0' platformBuildVersionName='16'";
        assert_eq!(parse_aapt_package(aapt).as_deref(), Some("com.x.test"));
    }

    #[test]
    fn module_arg_and_profile_plan() {
        assert_eq!(
            parse_module_arg("android.content.cts/androidx.test.runner.AndroidJUnitRunner"),
            Some((
                "android.content.cts".into(),
                "androidx.test.runner.AndroidJUnitRunner".into()
            ))
        );
        assert!(parse_module_arg("norunner").is_none());
        assert!(parse_module_arg("pkg/").is_none());

        let p: CtsProfile = serde_json::from_str(
            r#"{
            "api": "getPackageInfo",
            "module": "CtsContentTestCases",
            "package": "android.content.cts",
            "runner": "androidx.test.runner.AndroidJUnitRunner",
            "cases": ["android.content.pm.cts.PackageManagerTest#testGetPackageInfo"],
            "expected_count": 1,
            "unknown_future_field": {"nested": true}
        }"#,
        )
        .expect("同事 profile 的未知字段必须容忍");
        let plan = plan_from_profile(&p).unwrap();
        assert_eq!(plan.package, "android.content.cts");
        assert_eq!(plan.methods.len(), 1);
        assert!(plan.methods[0].contains('#'), "Class#method 切片原样保留");

        let env: CtsEnvironment = serde_json::from_str(
            r#"{
            "device": "FMR0223C13000649",
            "runtime_version": "12.0.0",
            "abi": "arm64-v8a",
            "cts_version": "16_r1",
            "host": {"note": "future"}
        }"#,
        )
        .expect("environment.json 未知字段必须容忍");
        assert_eq!(env.device_serial.as_deref(), Some("FMR0223C13000649"));
        assert_eq!(env.abi.as_deref(), Some("arm64-v8a"));
    }

    #[test]
    fn telemetry_diff_reports_deltas() {
        let before = json!({"vm_rss_kb": 1000, "fd_count": 50, "threads": 10});
        let after = json!({"vm_rss_kb": 1400, "fd_count": 57, "threads": null});
        let d = telemetry_diff(&before, &after);
        assert_eq!(d["delta"]["vm_rss_kb"], 400);
        assert_eq!(
            d["delta"]["fd_count"], 7,
            "FD 泄漏差值是 A2OH 死锁排查的关键指标"
        );
        assert!(d["delta"]["threads"].is_null(), "缺测如实 null");
    }

    #[test]
    fn batch_state_roundtrip_and_sanitize() {
        let dir = std::env::temp_dir().join(format!("pf_cts_state_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let mut st = BatchState::default();
        st.modules.insert("a/b".into(), "done".into());
        st.save(&dir);
        let loaded = BatchState::load(&dir);
        assert_eq!(loaded.modules.get("a/b").map(String::as_str), Some("done"));
        assert_eq!(sanitize_case_id("com.x.C#m()"), "com.x.C_m__");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 喂一段 runner 输出,返回 (parser, 已收官用例)
    fn feed(script: &str) -> (InstrumentParser, Vec<CaseResult>) {
        let mut p = InstrumentParser::new();
        let mut cases = Vec::new();
        for l in script.lines() {
            for ev in p.feed_line(l) {
                if let ParseEvent::CaseFinished(c) = ev {
                    cases.push(c);
                }
            }
        }
        (p, cases)
    }

    #[test]
    fn reconcile_crash_midcase_env_blocked_and_unaccounted() {
        // 桥崩溃现场: case1 PASS 后 case2 开始,随即 Process crashed,管道正常收官
        let (p, mut cases) = feed(
            "INSTRUMENTATION_STATUS: numtests=3\n\
             INSTRUMENTATION_STATUS: class=a.C\nINSTRUMENTATION_STATUS: test=m1\n\
             INSTRUMENTATION_STATUS_CODE: 1\n\
             INSTRUMENTATION_STATUS: class=a.C\nINSTRUMENTATION_STATUS: test=m1\n\
             INSTRUMENTATION_STATUS_CODE: 0\n\
             INSTRUMENTATION_STATUS: class=a.C\nINSTRUMENTATION_STATUS: test=m2\n\
             INSTRUMENTATION_STATUS_CODE: 1\n\
             INSTRUMENTATION_RESULT: shortMsg=Process crashed.\n\
             INSTRUMENTATION_CODE: 0",
        );
        assert_eq!(cases.len(), 1, "崩溃前只有 case1 收官");
        reconcile_cases(&mut cases, &p, &RunVerdict::Completed, true, true, 500);
        assert_eq!(cases.len(), 3, "在飞用例补记 + numtests 缺额补记");
        assert_eq!(cases[1].verdict, CaseVerdict::ENV_BLOCKED);
        assert_eq!(
            cases[1].id(),
            "a.C#m2",
            "在飞用例必须带真实 Class#method 以便 profile 对账"
        );
        assert_eq!(cases[2].verdict, CaseVerdict::NOT_RUN);
        assert_eq!(cases[2].method, "unaccounted_cases_x1");
    }

    #[test]
    fn reconcile_watchdog_timeout_and_unaccounted() {
        // 看门狗强杀: 在飞用例→TIMEOUT,numtests 缺额→NOT_RUN
        let (p, mut cases) = feed(
            "INSTRUMENTATION_STATUS: numtests=5\n\
             INSTRUMENTATION_STATUS: class=a.H\nINSTRUMENTATION_STATUS: test=hang\n\
             INSTRUMENTATION_STATUS_CODE: 1",
        );
        reconcile_cases(&mut cases, &p, &RunVerdict::IdleTimeout, false, true, 9000);
        assert_eq!(cases.len(), 2);
        assert_eq!(cases[0].verdict, CaseVerdict::TIMEOUT);
        assert_eq!(cases[0].id(), "a.H#hang");
        assert_eq!(cases[1].verdict, CaseVerdict::NOT_RUN);
        assert_eq!(cases[1].method, "unaccounted_cases_x4");
    }

    #[test]
    fn reconcile_clean_run_adds_nothing() {
        // 干净整轮: 宣称=产出,对账零添加(回归守卫: 不得误伤正常轮次)
        let (p, mut cases) = feed(
            "INSTRUMENTATION_STATUS: numtests=1\n\
             INSTRUMENTATION_STATUS: class=a.C\nINSTRUMENTATION_STATUS: test=m1\n\
             INSTRUMENTATION_STATUS_CODE: 1\n\
             INSTRUMENTATION_STATUS: class=a.C\nINSTRUMENTATION_STATUS: test=m1\n\
             INSTRUMENTATION_STATUS_CODE: 0\n\
             INSTRUMENTATION_CODE: -1",
        );
        reconcile_cases(&mut cases, &p, &RunVerdict::Completed, false, true, 100);
        assert_eq!(cases.len(), 1);
        assert_eq!(cases[0].verdict, CaseVerdict::PASS);
    }

    #[test]
    fn reconcile_runner_error_env_blocked() {
        // runner 级错误(包未安装): 零产出必须补记 ENV_BLOCKED,不得静默零用例
        let (p, mut cases) = feed(
            "onError: commandError=true message=INSTRUMENTATION_FAILED: a.b/c.D\n\
             INSTRUMENTATION_STATUS: Error=Unable to find instrumentation info for: ComponentInfo{a.b/c.D}\n\
             INSTRUMENTATION_STATUS: id=ActivityManagerService\n\
             INSTRUMENTATION_STATUS_CODE: -1",
        );
        assert!(cases.is_empty());
        reconcile_cases(&mut cases, &p, &RunVerdict::Completed, false, true, 200);
        assert_eq!(cases.len(), 1);
        assert_eq!(cases[0].verdict, CaseVerdict::ENV_BLOCKED);
        assert_eq!(cases[0].method, "runner_error");
        assert!(cases[0].stream.contains("Unable to find instrumentation"));
    }

    #[test]
    fn reconcile_runner_error_fallback_from_stderr_text() {
        // 设备偶发只露出 stderr 的 INSTRUMENTATION_FAILED(stdout Error= 行丢失),
        // 兜底检出同样必须记 ENV_BLOCKED
        let (p, mut cases) = feed(
            "onError: commandError=true message=INSTRUMENTATION_FAILED: a.b/c.D\n\
             android.util.AndroidException: INSTRUMENTATION_FAILED: a.b/c.D",
        );
        reconcile_cases(&mut cases, &p, &RunVerdict::Completed, false, true, 100);
        assert_eq!(cases.len(), 1);
        assert_eq!(cases[0].verdict, CaseVerdict::ENV_BLOCKED);
        assert!(cases[0].stream.contains("INSTRUMENTATION_FAILED"));
    }

    #[test]
    fn carried_report_from_prev_summary() {
        // 续跑沿用: 历史 summary 中存在的模块可取回报告,不存在则为 None
        let m = ModuleReport {
            module: "a/b".into(),
            package: "a".into(),
            runner: "b".into(),
            run_verdict: "Completed".into(),
            cases: vec![CaseResult {
                class_name: "C".into(),
                method: "m".into(),
                verdict: CaseVerdict::PASS,
                stack: String::new(),
                stream: String::new(),
                raw_lines: vec![],
                duration_ms: 1,
            }],
            wall_ms: 5,
            retries_used: 0,
            recovery: "VERIFIED".into(),
            crash_bundles: vec![],
        };
        let s = summary_json("b1", "ser", std::slice::from_ref(&m), 5);
        let got = carried_report(&s, "a/b").expect("应取回历史报告");
        assert_eq!(got.cases.len(), 1);
        assert_eq!(got.cases[0].verdict, CaseVerdict::PASS);
        assert!(carried_report(&s, "x/y").is_none());
        assert!(carried_report(&json!({}), "a/b").is_none());
    }

    #[test]
    fn reconcile_slice_mode_skips_numtests_synthesis() {
        // 切片轮次(profile): 在飞崩溃记 ENV_BLOCKED,但不做 numtests 缺额合成
        // (缺额由 profile 按名对账补 NOT_RUN,避免双重记账)
        let (p, mut cases) = feed(
            "INSTRUMENTATION_STATUS: numtests=3\n\
             INSTRUMENTATION_STATUS: class=a.C\nINSTRUMENTATION_STATUS: test=m1\n\
             INSTRUMENTATION_STATUS_CODE: 1\n\
             INSTRUMENTATION_RESULT: shortMsg=Process crashed.\n\
             INSTRUMENTATION_CODE: 0",
        );
        reconcile_cases(&mut cases, &p, &RunVerdict::Completed, true, false, 300);
        assert_eq!(cases.len(), 1);
        assert_eq!(cases[0].verdict, CaseVerdict::ENV_BLOCKED);
        assert_eq!(cases[0].id(), "a.C#m1");
    }

    // ── OH 官方 runner(arkxtest OpenHarmonyTestRunner)协议 ──

    const OH_RUN: &str = r"OHOS_REPORT_STATUS: class=com.example.UnitTest
OHOS_REPORT_STATUS: test=assertPass
OHOS_REPORT_STATUS: current=1
OHOS_REPORT_STATUS: numtests=2
OHOS_REPORT_STATUS_CODE: 1
OHOS_REPORT_CODE: 0
OHOS_REPORT_STATUS: class=com.example.UnitTest
OHOS_REPORT_STATUS: test=assertFail
OHOS_REPORT_STATUS: current=2
OHOS_REPORT_STATUS_CODE: 1
OHOS_REPORT_STATUS: stack=junit.framework.AssertionFailedError: expected:<1> but was:<2>
 at com.example.UnitTest.assertFail(UnitTest.java:42)
OHOS_REPORT_CODE: -2
OHOS_REPORT_RESULT: stream=Tests: 2, Init: 0, Fail: 1, Error: 0, Pass: 1
OHOS_REPORT_RESULT_CODE: -1";

    #[test]
    fn oh_protocol_parses_pass_fail_stack_and_run_end() {
        let (p, cases) = feed(OH_RUN);
        assert_eq!(cases.len(), 2, "两条用例各自在 OHOS_REPORT_CODE 到达即收官");
        assert_eq!(cases[0].verdict, CaseVerdict::PASS);
        assert_eq!(cases[0].id(), "com.example.UnitTest#assertPass");
        assert_eq!(cases[1].verdict, CaseVerdict::ASSERTION_FAIL);
        assert!(cases[1].stack.contains("AssertionFailedError"), "断言栈完整保留");
        assert!(cases[1].stack.contains("UnitTest.java:42"), "多行续行拼入栈");
        assert!(p.numtests() == Some(2));
        // 证据行存原始 OH 行,不存归一化行
        assert!(cases[1].raw_lines.iter().any(|l| l.starts_with("OHOS_REPORT_CODE: -2")));
    }

    #[test]
    fn oh_case_error_neg1_counts_as_fail_never_silent() {
        // OH 用例错误(未捕获异常): STATUS_CODE -1 + 带用例名 → 按断言失败记,栈保留
        let (_, cases) = feed(
            "OHOS_REPORT_STATUS: class=a.C\n\
             OHOS_REPORT_STATUS: test=m1\n\
             OHOS_REPORT_STATUS_CODE: 1\n\
             OHOS_REPORT_STATUS: stack=java.lang.NullPointerException: boom\n\
             OHOS_REPORT_STATUS_CODE: -1",
        );
        assert_eq!(cases.len(), 1);
        assert_eq!(cases[0].verdict, CaseVerdict::ASSERTION_FAIL);
        assert!(cases[0].stack.contains("NullPointerException"));
    }

    #[test]
    fn oh_double_finish_deduped() {
        // 部分 OH 版本同一用例既发 OHOS_REPORT_CODE 又发 STATUS_CODE 收官行 → 只记一次
        let (_, cases) = feed(
            "OHOS_REPORT_STATUS: class=a.C\n\
             OHOS_REPORT_STATUS: test=m1\n\
             OHOS_REPORT_STATUS_CODE: 1\n\
             OHOS_REPORT_CODE: -2\n\
             OHOS_REPORT_STATUS_CODE: -2",
        );
        assert_eq!(cases.len(), 1, "双收官不得双记");
    }

    #[test]
    fn oh_android_neg1_unchanged() {
        // Android 既有路径: STATUS_CODE -1 不带用例名/非 OH 行 → 零变化(不记用例)
        let (_, cases) = feed(
            "INSTRUMENTATION_STATUS: class=a.C\n\
             INSTRUMENTATION_STATUS: test=m1\n\
             INSTRUMENTATION_STATUS_CODE: 1\n\
             INSTRUMENTATION_STATUS_CODE: -1",
        );
        assert!(cases.is_empty(), "Android -1 行为不变");
    }

    #[test]
    fn oh_case_code_mapping_table() {
        assert_eq!(oh_case_code("0"), 0);
        assert_eq!(oh_case_code("-1"), -2, "用例错误按断言失败");
        assert_eq!(oh_case_code("-2"), -2);
        assert_eq!(oh_case_code("-3"), -3);
        assert_eq!(oh_case_code("junk"), 99);
    }

    #[test]
    fn oh_command_composition() {
        let spec = InstrumentSpec {
            package: "com.example.ut".into(),
            runner: "OpenHarmonyTestRunner".into(),
            module: Some("entry".into()),
            platform: Platform::Oh,
            class_or_method: Some("a.C#b".into()),
            timeout_ms: 1,
            idle_timeout_ms: 1,
            env_args: vec![("timeout".into(), "30".into())],
        };
        assert_eq!(
            instrument_command(&spec),
            "aa test -b com.example.ut -m entry -s unittest OpenHarmonyTestRunner -s class a.C#b -s timeout 30"
        );
        // Android 默认平台零变化
        let plain = InstrumentSpec {
            package: "com.x".into(),
            runner: "androidx.test.runner.AndroidJUnitRunner".into(),
            module: None,
            platform: Platform::Android,
            class_or_method: None,
            timeout_ms: 1,
            idle_timeout_ms: 1,
            env_args: vec![],
        };
        assert_eq!(
            instrument_command(&plain),
            "am instrument -r -w com.x/androidx.test.runner.AndroidJUnitRunner"
        );
    }

    #[test]
    fn parse_module_plan_android_and_oh() {
        let a = parse_module_plan("com.x/androidx.test.runner.AndroidJUnitRunner").unwrap();
        assert_eq!(a.package, "com.x");
        assert_eq!(a.platform, Platform::Android);
        assert!(a.module.is_none());
        let o = parse_module_plan("oh:com.example.ut/entry/OpenHarmonyTestRunner").unwrap();
        assert_eq!(o.package, "com.example.ut");
        assert_eq!(o.module.as_deref(), Some("entry"));
        assert_eq!(o.runner, "OpenHarmonyTestRunner");
        assert_eq!(o.platform, Platform::Oh);
        assert!(parse_module_plan("oh:bad/2part").is_err());
        assert!(parse_module_plan("no-slash").is_err());
    }

    #[test]
    fn scan_assertions_finds_failures_skips_binary_and_oversize() {
        let root = std::env::temp_dir().join(format!("pf_scan_test_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("res/a")).unwrap();
        std::fs::create_dir_all(root.join("res/img")).unwrap();
        std::fs::write(
            root.join("res/a/junit.xml"),
            "<testsuite tests=\"2\" failures=\"1\">\n<failure message=\"expected 1\">stack</failure>\n</testsuite>",
        )
        .unwrap();
        std::fs::write(
            root.join("res/a/stack.log"),
            "OK line\njunit.framework.AssertionFailedError: boom\n\tat com.x.C.m(C.java:7)",
        )
        .unwrap();
        std::fs::write(root.join("res/img/blob.png"), [0x89u8, 0x50, 0, 1]).unwrap();
        std::fs::write(root.join("res/big.log"), "x Failures: 3".repeat(64)).unwrap();
        let re = Regex::new(DEFAULT_ASSERTION_PATTERN).unwrap();
        let scan = scan_assertions(&root, &re, 256); // big.log 故意超限
        assert_eq!(scan.scanned_files, 2, "png 按扩展名跳, big.log 按尺寸跳");
        assert_eq!(scan.skipped_files, 2);
        assert!(scan.total_matches >= 3);
        let jf = scan.files.iter().find(|f| f.path.ends_with("junit.xml")).unwrap();
        assert!(jf.hits.iter().any(|h| h.text.contains("failures=\"1\"")));
        assert!(jf.hits.iter().any(|h| h.text.contains("<failure")));
        let sl = scan.files.iter().find(|f| f.path.ends_with("stack.log")).unwrap();
        assert!(sl.hits.iter().any(|h| h.line == 2 && h.text.contains("AssertionFailedError")));
        let _ = std::fs::remove_dir_all(&root);
    }
}
