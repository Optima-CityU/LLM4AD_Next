//! 决策原语：Jev 风格的 typed decisions，零 Token 本地可跑。
//!
//! 语义移植自 FluidUse 的决策模型运行时与 `PublishedCoreMLManager`
//! (<https://github.com/FluidInference/FluidUse>, Apache-2.0, commit 9384e70):
//! 「一个 state + 一组 typed question → 一组 typed answer」这个协议形状,
//! 以及桥接层的会话纪律(保序 / 一次一个 / 超时即杀 / 杀掉即 closed)。
//! **只借语义, 不搬代码** —— FluidUse 是 Swift + Core ML, 本仓库是 Rust。
//! 来源登记见 THIRD_PARTY_NOTICES.md。
//!
//! # 为什么是「协议 + 侧车」而不是「把模型搬进 Rust」
//!
//! FluidUse 的模型是 Core ML 包, 只能在 Apple 侧用 Core ML 运行时加载。Rust 里
//! 重建等价推理需要引入 ONNX 之类的运行时 —— 那是给这个仓库加一个新的大依赖,
//! 而且换不来任何确定性优势。所以这里做的是**协议那一半**:
//!
//! ```text
//!   phonefarm ──(stdin/stdout, 一行一个 JSON)──► 侧车进程
//!       │                                          │
//!       │  本文件: 协议 / 会话纪律 / 资产校验        │  可以是 FluidUse 的 Swift 桥,
//!       │                                          │  也可以是任何说得来这套 JSON 的东西
//!       └──────────────────────────────────────────┘
//! ```
//!
//! 侧车是**外部的**, 本仓库不随附、不构建、不下载它。
//!
//! # 三种回答的语义(与 FluidUse 的 typed decisions 对齐)
//!
//! | 类型 | 问什么 | 答什么 |
//! | --- | --- | --- |
//! | `choice` | 从 N 个选项里选一个 | 选中的 option label + 概率 |
//! | `score` | 在一个有序档位量表上打分 | 期望分(0~1) |
//! | `noul` | 一个「是/否」的判断题 | 为「是」的概率 |
//!
//! # 本地降级不是模型
//!
//! [`LocalDecider`] 是**降级占位**, 不是模型, 不是「本地小模型」。
//! 它的概率是**均匀的**(choice 给 1/n), 没有校准, 不冒充置信度。
//! 它存在的唯一理由: `llm.rs` 写明「失败必须可降级」—— 云端抽风时闭环不能停摆。
//! 任何拿它的概率当结论用的代码都是错的; 要结论必须走判定那一侧。
//!
//! 可复现性: 随机数走 [`crate::pyrandom`](CPython `random` 的逐位复刻),
//! 同一个请求 + 同一个 seed → 逐位相同的答案。这是能写进证据的性质。

//! # 子命令
//!
//! ```bash
//! phonefarm decide --local --seed 7 --request 请求.json      # 零 Token 降级
//! phonefarm decide --sidecar "python3 -m fluiduse_bridge" --request 请求.json
//! phonefarm decide --verify 模型目录 --manifest 清单.json     # 只校验资产, 不推理
//! ```

use crate::pyrandom::PyRandom;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::time::Duration;

/// 协议版本。握手时对不上就拒绝 —— 免得两边各说各话还以为通了。
pub const PROTOCOL: u32 = 1;
/// 侧车 stderr 尾部保留量(与 FluidUse 的 16 KB 取齐)。
const STDERR_TAIL_BYTES: usize = 16 * 1024;

// ══════════════ 协议 ══════════════

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Option_ {
    pub label: String,
    #[serde(default)]
    pub description: String,
}

/// 一个问题。`type` 决定后面跟哪些字段。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum Question {
    /// 从 `options` 里选一个。
    Choice {
        id: String,
        instructions: String,
        options: Vec<Option_>,
    },
    /// 在 `levels`(有序)上打一个分。
    Score {
        id: String,
        instructions: String,
        levels: Vec<String>,
    },
    /// 是/否概率。
    Noul { id: String, instructions: String },
}

impl Question {
    pub fn id(&self) -> &str {
        match self {
            Question::Choice { id, .. } | Question::Score { id, .. } | Question::Noul { id, .. } => id,
        }
    }
    pub fn instructions(&self) -> &str {
        match self {
            Question::Choice { instructions, .. }
            | Question::Score { instructions, .. }
            | Question::Noul { instructions, .. } => instructions,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DecisionRequest {
    pub state: String,
    pub questions: Vec<Question>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum Answer {
    Choice {
        id: String,
        selected: String,
        /// 该选项的概率。**本地降级给的是均匀值, 不是校准过的置信度。**
        p: f64,
    },
    Score {
        id: String,
        /// 0~1 的期望分。
        expected: f64,
    },
    Noul {
        id: String,
        /// 为「是」的概率。
        p: f64,
    },
}

impl Answer {
    pub fn id(&self) -> &str {
        match self {
            Answer::Choice { id, .. } | Answer::Score { id, .. } | Answer::Noul { id, .. } => id,
        }
    }
}

/// 请求校验。**在发出去之前**拒掉结构上就不成立的请求 ——
/// 一个没有选项的 choice、一个只有一个档位的 score、一个重复的 id,
/// 都不该消耗一次侧车调用(更不该消耗一次模型调用)。
pub fn validate(req: &DecisionRequest) -> Result<(), String> {
    if req.questions.is_empty() {
        return Err("questions 为空: 没有问题要问".into());
    }
    let mut seen = std::collections::HashSet::new();
    for q in &req.questions {
        if q.id().trim().is_empty() {
            return Err("question.id 为空".into());
        }
        if !seen.insert(q.id()) {
            return Err(format!("question.id 重复: {}", q.id()));
        }
        if q.instructions().trim().is_empty() {
            return Err(format!("{}: instructions 为空", q.id()));
        }
        match q {
            Question::Choice { options, id, .. } => {
                if options.len() < 2 {
                    return Err(format!("{id}: choice 至少要两个选项(收到 {})", options.len()));
                }
                if options.iter().any(|o| o.label.trim().is_empty()) {
                    return Err(format!("{id}: option.label 不能为空"));
                }
            }
            Question::Score { levels, id, .. } => {
                if levels.len() < 2 {
                    return Err(format!("{id}: score 至少要两个档位(收到 {})", levels.len()));
                }
            }
            Question::Noul { .. } => {}
        }
    }
    Ok(())
}

/// 待答条目在请求里的顺序 + 类型。用于**保序**回填:
/// 侧车按自己的顺序回也无所谓, 最终答案一律按请求顺序重排。
fn expected_shape(req: &DecisionRequest) -> Vec<(&str, &'static str)> {
    req.questions
        .iter()
        .map(|q| {
            let t = match q {
                Question::Choice { .. } => "choice",
                Question::Score { .. } => "score",
                Question::Noul { .. } => "noul",
            };
            (q.id(), t)
        })
        .collect()
}

/// 把侧车回的答案对回请求。
///
/// 三条硬规则(对应 FluidUse 的行为说明):
///   1. **保序**: 答案按请求里的问题顺序排列, 不按侧车给的顺序;
///   2. **不臆造**: 缺的答案是错, 不是「用默认值补上」。缺一条整批作废 ——
///      半份答案比没有答案更危险, 下游会以为那是完整的;
///   3. **不串型**: 类型对不上(choice 回成 score)也是错。
pub fn align_answers(req: &DecisionRequest, got: Vec<Answer>) -> Result<Vec<Answer>, String> {
    let want = expected_shape(req);
    if got.len() != want.len() {
        return Err(format!("答案条数不符: 问 {} 条, 回 {} 条", want.len(), got.len()));
    }
    let mut by_id: std::collections::HashMap<String, Answer> = std::collections::HashMap::new();
    for a in got {
        if by_id.insert(a.id().to_string(), a).is_some() {
            return Err(format!("答案 id 重复: {}", by_id.keys().last().unwrap()));
        }
    }
    let mut out = Vec::with_capacity(want.len());
    for (id, ty) in want {
        let a = by_id
            .remove(id)
            .ok_or_else(|| format!("缺答案: {id}"))?;
        let at = match &a {
            Answer::Choice { .. } => "choice",
            Answer::Score { .. } => "score",
            Answer::Noul { .. } => "noul",
        };
        if at != ty {
            return Err(format!("{id}: 问的是 {ty}, 回的是 {at}"));
        }
        out.push(a);
    }
    Ok(out)
}

// ══════════════ 决策器 ══════════════

pub trait Decider {
    /// 进 JSON 的来源标识, 让上层一眼看出这份答案是哪条通路给的。
    fn source(&self) -> &'static str;
    fn decide(&mut self, req: &DecisionRequest) -> Result<Vec<Answer>, String>;
}

// ── 本地降级(不是模型) ──

/// 零 Token 的**降级占位**。见文件头: 概率是均匀的, 没有校准, 不冒充置信度。
///
/// 用来回答「云端不可用时闭环怎么继续」, 不是用来回答「这个参数组好不好」。
pub struct LocalDecider {
    seed: i64,
}

impl LocalDecider {
    pub fn new(seed: i64) -> Self {
        LocalDecider { seed }
    }

    /// 请求内容定种子: 同一个请求 + 同一个基准 seed → 逐位相同的答案。
    fn rng_for(&self, req: &DecisionRequest) -> PyRandom {
        let mut h = Sha256::new();
        h.update(self.seed.to_le_bytes());
        h.update(req.state.as_bytes());
        for q in &req.questions {
            h.update(q.id().as_bytes());
            h.update([0]);
            h.update(q.instructions().as_bytes());
            h.update([0]);
        }
        let d = h.finalize();
        PyRandom::new(i64::from_le_bytes(d[..8].try_into().expect("sha256 前 8 字节")))
    }
}

impl Decider for LocalDecider {
    fn source(&self) -> &'static str {
        "local_degraded"
    }

    fn decide(&mut self, req: &DecisionRequest) -> Result<Vec<Answer>, String> {
        validate(req)?;
        let mut rng = self.rng_for(req);
        Ok(req
            .questions
            .iter()
            .map(|q| match q {
                Question::Choice { id, options, .. } => {
                    let i = rng.randbelow(options.len() as u64) as usize;
                    Answer::Choice {
                        id: id.clone(),
                        selected: options[i].label.clone(),
                        // 均匀: 1/n。**不是**置信度, 也不打算是。
                        p: 1.0 / options.len() as f64,
                    }
                }
                Question::Score { id, levels, .. } => {
                    let i = rng.randbelow(levels.len() as u64) as usize;
                    Answer::Score {
                        id: id.clone(),
                        expected: if levels.len() <= 1 {
                            0.0
                        } else {
                            i as f64 / (levels.len() - 1) as f64
                        },
                    }
                }
                Question::Noul { id, .. } => Answer::Noul {
                    id: id.clone(),
                    // 0~1 之间取一个值。64 位取满再归一, 避免低位偏好。
                    p: (rng.getrandbits(53) as f64) / ((1u64 << 53) as f64),
                },
            })
            .collect())
    }
}

// ── 侧车(把 Core ML 那一半接进来的地方) ──

/// 一行一个 JSON 的侧车进程。
///
/// 协议:
/// ```text
///   → {"op":"hello","protocol":1}
///   ← {"op":"ready","protocol":1}
///   → {"op":"decide","state":"…","questions":[…]}
///   ← {"op":"answers","answers":[…]}
///   ← {"op":"error","message":"…"}        (模型侧单次失败, 会话仍然可用)
/// ```
///
/// 会话纪律(照 FluidUse 的桥接层, 因为那些坑是真踩出来的):
///   - **一次一个, 按调用顺序**: 并发调用排队, 不交错写读;
///   - **启动超时**独立于**单次请求超时**;
///   - **超时或取消即杀会话**: SIGTERM, 等 1s, 再 SIGKILL; 之后 `closed`,
///     后续调用一律 `.closed` 而不是重放 —— 重放一个状态不明的请求比失败更坏;
///   - **模型侧的错误不杀会话**: `{"op":"error"}` 只让这一次失败;
///   - **stderr 单独收**, 只留尾部, 启动失败与超时都把尾巴带出来。
pub struct SidecarDecider {
    child: Child,
    stdin: ChildStdin,
    lines: Receiver<String>,
    stderr_tail: std::sync::Arc<std::sync::Mutex<VecDeque<u8>>>,
    stderr_done: std::sync::Arc<std::sync::atomic::AtomicBool>,
    request_timeout: Duration,
    closed: bool,
    source: &'static str,
}

impl SidecarDecider {
    pub fn start(cmd: &str, startup_timeout: Duration, request_timeout: Duration) -> Result<Self, String> {
        let mut child = Command::new("sh")
            .args(["-c", cmd])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("侧车启动失败: {e}"))?;
        let stdin = child.stdin.take().expect("stdin 已 pipe");
        let stdout = child.stdout.take().expect("stdout 已 pipe");
        let stderr = child.stderr.take().expect("stderr 已 pipe");

        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                match line {
                    Ok(l) => {
                        if tx.send(l).is_err() {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }
        });
        let tail = std::sync::Arc::new(std::sync::Mutex::new(VecDeque::new()));
        let stderr_done = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let tail_w = std::sync::Arc::clone(&tail);
        let done_w = std::sync::Arc::clone(&stderr_done);
        let stderr_thread = std::thread::spawn(move || {
            let mut r = BufReader::new(stderr);
            let mut buf = [0u8; 1024];
            loop {
                match std::io::Read::read(&mut r, &mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        let mut t = tail_w.lock().unwrap();
                        for b in &buf[..n] {
                            if t.len() >= STDERR_TAIL_BYTES {
                                t.pop_front();
                            }
                            t.push_back(*b);
                        }
                    }
                }
            }
            done_w.store(true, std::sync::atomic::Ordering::Relaxed);
        });
        let stderr_done_handle = stderr_done;

        let mut s = SidecarDecider {
            child,
            stdin,
            lines: rx,
            stderr_tail: tail,
            stderr_done: stderr_done_handle,
            request_timeout,
            closed: false,
            source: "sidecar",
        };
        // 握手: 对不上就直接判死, 不拖到第一次 decide 才报错
        let hello = serde_json::json!({"op": "hello", "protocol": PROTOCOL}).to_string();
        s.send(&hello)?;
        match s.recv(startup_timeout) {
            Ok(line) => {
                let v: serde_json::Value = serde_json::from_str(&line)
                    .map_err(|e| format!("握手的回包不是 JSON: {e}"))?;
                if v.get("op").and_then(|o| o.as_str()) != Some("ready") {
                    return Err(format!("握手失败: 期望 op=ready, 收到 {}", line.trim()));
                }
                let p = v.get("protocol").and_then(|p| p.as_u64()).unwrap_or(0);
                if p != PROTOCOL as u64 {
                    return Err(format!("协议版本不符: 侧车 {p}, 本端 {PROTOCOL}"));
                }
                Ok(s)
            }
            Err(e) => Err(format!("{e}{}", s.stderr_hint())),
        }
    }

    /// 死亡/断连路径先等 stderr 读取线程排空尾部（EOF 后立即置位），消除尾部竞态。
    fn wait_stderr_tail(&self, ms: u64) {
        let deadline = std::time::Instant::now() + std::time::Duration::from_millis(ms);
        while !self.stderr_done.load(std::sync::atomic::Ordering::Relaxed)
            && std::time::Instant::now() < deadline
        {
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    fn stderr_hint(&self) -> String {
        let t = self.stderr_tail.lock().unwrap();
        if t.is_empty() {
            return String::new();
        }
        let s: String = t.iter().map(|&b| b as char).collect();
        format!("\n侧车 stderr 尾部:\n{}", s.trim_end())
    }

    fn send(&mut self, line: &str) -> Result<(), String> {
        if self.closed {
            return Err("会话已关闭(超时或取消后需要重开)".into());
        }
        writeln!(self.stdin, "{line}")
            .and_then(|_| self.stdin.flush())
            .map_err(|e| {
                self.closed = true;
                self.wait_stderr_tail(1000);
                format!("写入侧车失败: {e}{}", self.stderr_hint())
            })
    }

    fn recv(&mut self, timeout: Duration) -> Result<String, String> {
        match self.lines.recv_timeout(timeout) {
            Ok(l) => Ok(l),
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.kill();
                Err(format!("侧车超过 {}s 未回{}(会话已杀, 之后一律 closed)", timeout.as_secs(), self.stderr_hint()))
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                self.closed = true;
                self.wait_stderr_tail(1000);
                Err(format!("侧车已退出{}", self.stderr_hint()))
            }
        }
    }

    /// SIGTERM → 等 1s → SIGKILL。与 FluidUse 的处理顺序一致:
    /// 先给机会让侧车把 Core ML 的模型句柄放掉, 不放就强杀。
    fn kill(&mut self) {
        self.closed = true;
        unsafe {
            libc_kill(self.child.id() as i32, 15);
        }
        for _ in 0..10 {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                return;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for SidecarDecider {
    fn drop(&mut self) {
        if !self.closed {
            self.kill();
        }
    }
}

// SIGTERM。只为了「先礼后兵」这一步, 不为别的 —— 为此引一个 libc crate 不划算,
// 所以这里直接声明外部符号。
extern "C" {
    #[link_name = "kill"]
    fn libc_kill(pid: i32, sig: i32) -> i32;
}

impl Decider for SidecarDecider {
    fn source(&self) -> &'static str {
        self.source
    }

    fn decide(&mut self, req: &DecisionRequest) -> Result<Vec<Answer>, String> {
        validate(req)?;
        let payload = serde_json::json!({
            "op": "decide",
            "state": req.state,
            "questions": req.questions,
        })
        .to_string();
        self.send(&payload)?;
        let line = self.recv(self.request_timeout)?;
        let v: serde_json::Value =
            serde_json::from_str(&line).map_err(|e| format!("侧车回包不是 JSON: {e}"))?;
        match v.get("op").and_then(|o| o.as_str()) {
            // 模型侧单次失败: 只让这一次失败, 会话继续可用
            Some("error") => Err(format!(
                "侧车报告错误: {}",
                v.get("message").and_then(|m| m.as_str()).unwrap_or("(无 message)")
            )),
            Some("answers") => {
                let got: Vec<Answer> = serde_json::from_value(
                    v.get("answers").cloned().unwrap_or(serde_json::Value::Null),
                )
                .map_err(|e| format!("答案解不出来: {e}"))?;
                align_answers(req, got)
            }
            other => Err(format!("侧车回了未知 op: {other:?}")),
        }
    }
}

// ══════════════ 资产校验 ══════════════

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ManifestEntry {
    pub path: String,
    /// 小写十六进制 SHA-256。
    pub sha256: String,
    #[serde(default)]
    pub bytes: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Manifest {
    /// 来源标识(仓库 + revision), 只给人看与写进证据。
    #[serde(default)]
    pub source: String,
    pub files: Vec<ManifestEntry>,
}

/// 按 manifest 校验一个目录。返回问题清单(**空 = 全部通过**)。
///
/// 语义来自 FluidUse 的 pinned manifest: 资产必须**可校验、可复现**,
/// 换了 revision 就重新生成 manifest, 而不是「下下来就信」。
/// 文件缺失、尺寸不符、哈希不符都各算一条问题 —— 不合并、不降级。
pub fn verify_manifest(dir: &std::path::Path, m: &Manifest) -> Vec<String> {
    let mut problems = Vec::new();
    for e in &m.files {
        let p = dir.join(&e.path);
        let data = match std::fs::read(&p) {
            Ok(d) => d,
            Err(err) => {
                problems.push(format!("{}: 读不到 ({err})", e.path));
                continue;
            }
        };
        if let Some(want) = e.bytes {
            if data.len() as u64 != want {
                problems.push(format!(
                    "{}: 大小不符 (期望 {want} 字节, 实际 {})",
                    e.path,
                    data.len()
                ));
                continue;
            }
        }
        let got = hex(&Sha256::digest(&data));
        if !got.eq_ignore_ascii_case(&e.sha256) {
            problems.push(format!("{}: SHA-256 不符 (期望 {}, 实际 {got})", e.path, e.sha256));
        }
    }
    problems
}

fn hex(b: &[u8]) -> String {
    let mut s = String::with_capacity(b.len() * 2);
    for x in b {
        s.push_str(&format!("{x:02x}"));
    }
    s
}

// ══════════════ 子命令 ══════════════

fn usage() -> &'static str {
    "用法: phonefarm decide [--local [--seed N] | --sidecar \"命令\"] [--request FILE|-] [--json]\n\
     \x20     phonefarm decide --verify <目录> --manifest <清单.json>\n\
     \x20 --request: 决策请求 JSON; 缺省从 stdin 读 (- 同义)\n\
     \x20 --local  : 零 Token 降级占位。**它不是模型**: 概率是均匀的, 没有校准\n\
     \x20 --sidecar: 一行一个 JSON 的外部进程(把 Core ML 那一半接进来的地方)\n\
     \x20 --verify : 只按 manifest 校验资产(SHA-256/尺寸), 不推理"
}

pub fn run_decide(args: &[String]) -> i32 {
    let mut sidecar: Option<String> = None;
    let mut seed: i64 = 0;
    let mut local = false;
    let mut request: Option<String> = None;
    let mut json = false;
    let mut verify_dir: Option<std::path::PathBuf> = None;
    let mut manifest_path: Option<std::path::PathBuf> = None;

    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--local" => local = true,
            "--json" => json = true,
            "--seed" => match it.next().and_then(|v| v.parse::<i64>().ok()) {
                Some(v) => seed = v,
                None => {
                    eprintln!("--seed 需要一个整数\n{}", usage());
                    return 2;
                }
            },
            "--sidecar" => match it.next() {
                Some(v) if !v.trim().is_empty() => sidecar = Some(v.clone()),
                _ => {
                    eprintln!("--sidecar 需要一个命令\n{}", usage());
                    return 2;
                }
            },
            "--request" => request = it.next().cloned(),
            "--verify" => verify_dir = it.next().map(std::path::PathBuf::from),
            "--manifest" => manifest_path = it.next().map(std::path::PathBuf::from),
            "-h" | "--help" => {
                println!("{}", usage());
                return 2;
            }
            other => {
                eprintln!("未知参数 {other}\n{}", usage());
                return 2;
            }
        }
    }

    // 只校验资产: 不推理、不碰模型
    if let Some(dir) = verify_dir {
        let Some(mp) = manifest_path else {
            eprintln!("--verify 必须与 --manifest 一起用\n{}", usage());
            return 2;
        };
        let text = match std::fs::read_to_string(&mp) {
            Ok(t) => t,
            Err(e) => {
                eprintln!("读不到 manifest {}: {e}", mp.display());
                return 2;
            }
        };
        let m: Manifest = match serde_json::from_str(&text) {
            Ok(m) => m,
            Err(e) => {
                eprintln!("manifest 解析失败: {e}");
                return 2;
            }
        };
        let problems = verify_manifest(&dir, &m);
        if problems.is_empty() {
            println!("资产校验通过: {} 项", m.files.len());
            return 0;
        }
        eprintln!("资产校验不通过: {} 项问题", problems.len());
        for p in &problems {
            eprintln!("  {p}");
        }
        return 1;
    }

    let raw = match request.as_deref() {
        None | Some("-") => {
            let mut s = String::new();
            if let Err(e) = std::io::Read::read_to_string(&mut std::io::stdin(), &mut s) {
                eprintln!("读 stdin 失败: {e}");
                return 2;
            }
            s
        }
        Some(p) => match std::fs::read_to_string(p) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("读不到请求 {p}: {e}");
                return 2;
            }
        },
    };
    let req: DecisionRequest = match serde_json::from_str(&raw) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("请求解析失败: {e}");
            return 2;
        }
    };
    if let Err(e) = validate(&req) {
        // 结构不成立的请求在发出去之前就拒 —— 不该消耗一次侧车调用
        eprintln!("请求不合法: {e}");
        return 2;
    }

    let outcome: Result<(&'static str, Vec<Answer>), String> = if let Some(cmd) = sidecar {
        match SidecarDecider::start(&cmd, Duration::from_secs(600), Duration::from_secs(300)) {
            Ok(mut d) => {
                let s = d.source();
                d.decide(&req).map(|a| (s, a))
            }
            Err(e) => Err(e),
        }
    } else {
        if !local {
            eprintln!("要么 --local, 要么 --sidecar(见 --help)\n{}", usage());
            return 2;
        }
        LocalDecider::new(seed)
            .decide(&req)
            .map(|a| ("local_degraded", a))
    };

    match outcome {
        Ok((src, answers)) => {
            if json {
                println!(
                    "{}",
                    serde_json::json!({"source": src, "answers": answers})
                );
            } else {
                println!("来源: {src}");
                for a in &answers {
                    match a {
                        Answer::Choice { id, selected, p } => {
                            println!("  {id}: choice = {selected} (p={p:.4})")
                        }
                        Answer::Score { id, expected } => println!("  {id}: score = {expected:.4}"),
                        Answer::Noul { id, p } => println!("  {id}: noul = {p:.4}"),
                    }
                }
                if src == "local_degraded" {
                    println!("(降级占位: 概率是均匀的, 不是校准过的置信度; 结论必须走判定那一侧)");
                }
            }
            0
        }
        Err(e) => {
            eprintln!("决策失败: {e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req() -> DecisionRequest {
        DecisionRequest {
            state: "客户为订单 #4471 被重复扣款, 没有人回复。今天就要退款。".into(),
            questions: vec![
                Question::Choice {
                    id: "want".into(),
                    instructions: "客户想要什么?".into(),
                    options: vec![
                        Option_ { label: "refund".into(), description: "退款".into() },
                        Option_ { label: "status".into(), description: "查单".into() },
                    ],
                },
                Question::Score {
                    id: "anger".into(),
                    instructions: "客户有多生气?".into(),
                    levels: vec!["calm".into(), "annoyed".into(), "angry".into()],
                },
                Question::Noul {
                    id: "churn".into(),
                    instructions: "客户会不会流失?".into(),
                },
            ],
        }
    }

    #[test]
    fn validate_rejects_structurally_impossible_requests() {
        let mut r = req();
        r.questions[0] = Question::Choice {
            id: "want".into(),
            instructions: "x".into(),
            options: vec![Option_ { label: "only".into(), description: String::new() }],
        };
        assert!(validate(&r).unwrap_err().contains("至少要两个选项"));

        let mut r2 = req();
        r2.questions[1] = Question::Score {
            id: "anger".into(),
            instructions: "x".into(),
            levels: vec!["only".into()],
        };
        assert!(validate(&r2).unwrap_err().contains("至少要两个档位"));

        let mut r3 = req();
        r3.questions[2] = Question::Noul { id: "want".into(), instructions: "x".into() };
        assert!(validate(&r3).unwrap_err().contains("重复"));

        assert!(validate(&DecisionRequest { state: "s".into(), questions: vec![] })
            .unwrap_err()
            .contains("questions 为空"));
        assert!(validate(&req()).is_ok());
    }

    #[test]
    fn align_is_order_preserving_not_order_following() {
        let r = req();
        // 侧车倒序回: 对齐后必须回到请求顺序
        let got = vec![
            Answer::Noul { id: "churn".into(), p: 0.9 },
            Answer::Score { id: "anger".into(), expected: 0.5 },
            Answer::Choice { id: "want".into(), selected: "refund".into(), p: 0.9 },
        ];
        let out = align_answers(&r, got).unwrap();
        assert_eq!(
            out.iter().map(|a| a.id()).collect::<Vec<_>>(),
            vec!["want", "anger", "churn"]
        );
    }

    #[test]
    fn align_refuses_missing_extra_wrong_type_and_duplicates() {
        let r = req();
        // 少一条: 整批作废, 不用默认值补
        let missing = vec![Answer::Choice { id: "want".into(), selected: "refund".into(), p: 1.0 }];
        assert!(align_answers(&r, missing).unwrap_err().contains("条数不符"));
        // 类型串了
        let wrong = vec![
            Answer::Score { id: "want".into(), expected: 0.5 },
            Answer::Score { id: "anger".into(), expected: 0.5 },
            Answer::Noul { id: "churn".into(), p: 0.5 },
        ];
        assert!(align_answers(&r, wrong).unwrap_err().contains("问的是 choice"));
        // id 重复
        let dup = vec![
            Answer::Choice { id: "want".into(), selected: "refund".into(), p: 1.0 },
            Answer::Choice { id: "want".into(), selected: "status".into(), p: 1.0 },
            Answer::Noul { id: "churn".into(), p: 0.5 },
        ];
        assert!(align_answers(&r, dup).is_err());
        // 有多的也会被条数挡下
        let extra = vec![
            Answer::Choice { id: "want".into(), selected: "refund".into(), p: 1.0 },
            Answer::Score { id: "anger".into(), expected: 0.5 },
            Answer::Noul { id: "churn".into(), p: 0.5 },
            Answer::Noul { id: "ghost".into(), p: 0.5 },
        ];
        assert!(align_answers(&r, extra).unwrap_err().contains("条数不符"));
    }

    #[test]
    fn local_decider_is_reproducible_and_honest_about_probability() {
        let r = req();
        let a = LocalDecider::new(7).decide(&r).unwrap();
        let b = LocalDecider::new(7).decide(&r).unwrap();
        assert_eq!(a, b, "同 seed 同请求必须逐位相同");
        let c = LocalDecider::new(8).decide(&r).unwrap();
        assert_ne!(a, c, "换 seed 应该可能不同");

        // 均匀概率就是 1/n —— 不是置信度, 也不假装是
        match &a[0] {
            Answer::Choice { p, .. } => assert!((p - 0.5).abs() < 1e-12),
            other => panic!("第 0 条应当是 choice: {other:?}"),
        }
        // score 落在 [0,1] 且是对齐档位的等差点
        match &a[1] {
            Answer::Score { expected, .. } => {
                assert!((0.0..=1.0).contains(expected));
                assert!([0.0, 0.5, 1.0].iter().any(|v| (v - expected).abs() < 1e-12));
            }
            other => panic!("第 1 条应当是 score: {other:?}"),
        }
        match &a[2] {
            Answer::Noul { p, .. } => assert!((0.0..1.0).contains(p)),
            other => panic!("第 2 条应当是 noul: {other:?}"),
        }
    }

    #[test]
    fn local_decider_picks_only_declared_options() {
        let r = req();
        for seed in 0..50 {
            let out = LocalDecider::new(seed).decide(&r).unwrap();
            if let Answer::Choice { selected, .. } = &out[0] {
                assert!(selected == "refund" || selected == "status", "跑出了未声明的选项 {selected}");
            }
        }
    }

    #[test]
    fn protocol_serializes_with_a_type_tag() {
        let r = req();
        let j = serde_json::to_value(&r).unwrap();
        assert_eq!(j["questions"][0]["type"], "choice");
        assert_eq!(j["questions"][1]["type"], "score");
        assert_eq!(j["questions"][2]["type"], "noul");
        let back: DecisionRequest = serde_json::from_value(j).unwrap();
        assert_eq!(back, r);
        // 答案同理
        let a = Answer::Choice { id: "x".into(), selected: "y".into(), p: 0.5 };
        assert_eq!(serde_json::to_value(&a).unwrap()["type"], "choice");
    }

    // ── 侧车: 用 sh 假侧车验会话纪律(真模型不在本仓库, 但纪律是可测的) ──

    const OK_SIDECAR: &str = r#"
while IFS= read -r line; do
  case "$line" in
    *'"op":"hello"'*) echo '{"op":"ready","protocol":1}' ;;
    *'"op":"decide"'*) echo '{"op":"answers","answers":[{"type":"noul","id":"churn","p":0.25}]}' ;;
  esac
done
"#;

    fn one_noul() -> DecisionRequest {
        DecisionRequest {
            state: "s".into(),
            questions: vec![Question::Noul { id: "churn".into(), instructions: "会在吗".into() }],
        }
    }

    #[test]
    fn sidecar_handshakes_and_answers() {
        let mut d = SidecarDecider::start(OK_SIDECAR, Duration::from_secs(5), Duration::from_secs(5)).unwrap();
        assert_eq!(d.source(), "sidecar");
        let out = d.decide(&one_noul()).unwrap();
        assert_eq!(out, vec![Answer::Noul { id: "churn".into(), p: 0.25 }]);
        // 会话可复用: 第二次调用不需要重新握手
        assert_eq!(d.decide(&one_noul()).unwrap().len(), 1);
    }

    #[test]
    fn sidecar_rejects_wrong_protocol_version() {
        let bad = r#"read -r _; echo '{"op":"ready","protocol":99}'; sleep 5"#;
        let e = match SidecarDecider::start(bad, Duration::from_secs(3), Duration::from_secs(3)) {
            Ok(_) => panic!("协议版本不符时不该握手成功"),
            Err(e) => e,
        };
        assert!(e.contains("协议版本不符"), "{e}");
    }

    #[test]
    fn sidecar_times_out_then_stays_closed() {
        // 握手正常, decide 时挂住不回
        let hang = r#"
read -r line; echo '{"op":"ready","protocol":1}'
while IFS= read -r line; do sleep 30; done
"#;
        let mut d = SidecarDecider::start(hang, Duration::from_secs(3), Duration::from_millis(300)).unwrap();
        let e = d.decide(&one_noul()).unwrap_err();
        assert!(e.contains("未回"), "{e}");
        // 超时之后是 closed, 不是重放 —— 状态不明的请求不能重发
        let e2 = d.decide(&one_noul()).unwrap_err();
        assert!(e2.contains("已关闭"), "{e2}");
    }

    #[test]
    fn sidecar_model_error_keeps_the_session() {
        // 第一次 decide 报 error, 第二次正常回 —— 会话必须还活着
        let s = r#"
n=0
while IFS= read -r line; do
  case "$line" in
    *hello*) echo '{"op":"ready","protocol":1}' ;;
    *decide*) n=$((n+1)); if [ "$n" = "1" ]; then echo '{"op":"error","message":"模型侧炸了"}'; else echo '{"op":"answers","answers":[{"type":"noul","id":"churn","p":0.5}]}'; fi ;;
  esac
done
"#;
        let mut d = SidecarDecider::start(s, Duration::from_secs(5), Duration::from_secs(5)).unwrap();
        let e = d.decide(&one_noul()).unwrap_err();
        assert!(e.contains("模型侧炸了"), "{e}");
        assert_eq!(d.decide(&one_noul()).unwrap().len(), 1, "模型侧错误不该杀会话");
    }

    #[test]
    fn sidecar_death_is_reported_with_stderr_tail() {
        let die = r#"
read -r line
echo 'boom: 模型文件不在' >&2
exit 3
"#;
        let e = match SidecarDecider::start(die, Duration::from_secs(3), Duration::from_secs(3)) {
            Ok(_) => panic!("侧车启动就死, 不该握手成功"),
            Err(e) => e,
        };
        assert!(e.contains("已退出"), "{e}");
        assert!(e.contains("boom"), "启动失败必须把 stderr 尾巴带出来: {e}");
    }

    #[test]
    fn sidecar_refuses_bad_requests_without_calling_it() {
        // 侧车故意回一个空 answers; 请求本身不合法时根本不该发出去
        let mut d = SidecarDecider::start(OK_SIDECAR, Duration::from_secs(5), Duration::from_secs(5)).unwrap();
        let bad = DecisionRequest {
            state: "s".into(),
            questions: vec![Question::Choice {
                id: "x".into(),
                instructions: "i".into(),
                options: vec![Option_ { label: "one".into(), description: String::new() }],
            }],
        };
        assert!(d.decide(&bad).unwrap_err().contains("至少要两个选项"));
        // 会话没被这次拒绝弄坏
        assert_eq!(d.decide(&one_noul()).unwrap().len(), 1);
    }

    // ── 资产校验 ──

    #[test]
    fn manifest_verification_catches_every_kind_of_drift() {
        let dir = std::env::temp_dir().join(format!("pf_manifest_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("ok.bin"), b"hello").unwrap();
        // "hello" 的 sha256
        let hello = hex(&Sha256::digest(b"hello"));
        std::fs::write(dir.join("short.bin"), b"hell").unwrap();

        let m = Manifest {
            source: "test".into(),
            files: vec![
                ManifestEntry { path: "ok.bin".into(), sha256: hello.clone(), bytes: Some(5) },
                ManifestEntry { path: "short.bin".into(), sha256: hello.clone(), bytes: Some(5) },
                ManifestEntry { path: "gone.bin".into(), sha256: hello.clone(), bytes: None },
            ],
        };
        let p = verify_manifest(&dir, &m);
        assert_eq!(p.len(), 2, "{p:?}");
        assert!(p.iter().any(|x| x.contains("short.bin") && x.contains("大小不符")), "{p:?}");
        assert!(p.iter().any(|x| x.contains("gone.bin") && x.contains("读不到")), "{p:?}");
        assert!(!p.iter().any(|x| x.contains("ok.bin")), "合格的文件不该报问题");

        // 全对时返回空 —— 空就是通过, 不给「看起来像成功」的别的东西
        let only_ok = Manifest { source: "t".into(), files: vec![ManifestEntry { path: "ok.bin".into(), sha256: hello, bytes: Some(5) }] };
        assert!(verify_manifest(&dir, &only_ok).is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 真侧车端到端。**默认跳过** —— Core ML 只在 Apple 侧有, 不能塞进必跑集。
    ///
    /// ```bash
    /// make -C tools/decision-sidecar build
    /// PHONEFARM_DECISION_SIDECAR="$(pwd)/tools/decision-sidecar/build/decision-sidecar \
    ///   --model $(pwd)/tools/decision-sidecar/reference.mlmodel" cargo test real_sidecar
    /// ```
    ///
    /// 这条测试的价值在于: 它是唯一会**真的把答案按语义区分开**的测试。
    /// 假侧车只能验协议; 真侧车能验「同一组选项、两段不同的状态, 选出了不同的东西」——
    /// 也就是「这条路真的在做事, 而不是在回一个常量」。
    #[test]
    fn real_sidecar_end_to_end_when_asked() {
        let Ok(cmd) = std::env::var("PHONEFARM_DECISION_SIDECAR") else {
            return; // 没配就不跑: 不能用环境缺失把它变成红灯
        };
        let ask = |state: &str| DecisionRequest {
            state: state.to_string(),
            questions: vec![Question::Choice {
                id: "want".into(),
                instructions: "客户想要什么?".into(),
                options: vec![
                    Option_ { label: "refund".into(), description: "退款".into() },
                    Option_ { label: "order_status".into(), description: "查单".into() },
                    Option_ { label: "technical_help".into(), description: "技术支持".into() },
                ],
            }],
        };
        let mut d = match SidecarDecider::start(&cmd, Duration::from_secs(60), Duration::from_secs(60)) {
            Ok(d) => d,
            Err(e) => panic!("真侧车起不来: {e}"),
        };
        let pick = |d: &mut SidecarDecider, state: &str| match d.decide(&ask(state)).unwrap().remove(0) {
            Answer::Choice { selected, .. } => selected,
            other => panic!("应当是 choice: {other:?}"),
        };
        let a = pick(&mut d, "客户为订单 #4471 被重复扣款，没有人回复。今天就要退款。");
        let b = pick(&mut d, "登录之后一直白屏，重启 App 也没有用，我的订单页面打不开。");
        assert_eq!(a, "refund", "要退款的诉求应当选 refund");
        assert_eq!(b, "technical_help", "白屏打不开应当选 technical_help");
        assert_ne!(a, b, "同一组选项、两段不同的状态, 必须选出不同的东西 —— 否则这条路只是在回常量");
        assert_eq!(
            pick(&mut d, "客户为订单 #4471 被重复扣款，没有人回复。今天就要退款。"),
            a,
            "同一请求重复问必须逐位一致"
        );
    }

    #[test]
    fn manifest_hash_mismatch_is_reported_case_insensitively() {
        let dir = std::env::temp_dir().join(format!("pf_manifest_ci_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("a.bin"), b"x").unwrap();
        let up = hex(&Sha256::digest(b"x")).to_uppercase();
        let m = Manifest { source: "t".into(), files: vec![ManifestEntry { path: "a.bin".into(), sha256: up, bytes: None }] };
        assert!(verify_manifest(&dir, &m).is_empty(), "大小写不该算不符");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
