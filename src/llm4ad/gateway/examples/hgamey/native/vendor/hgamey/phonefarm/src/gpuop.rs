//! gpu-op: Compute Shader 算子的真机标尺与 A/B 裁决。
//!
//! 职责边界: 收 eval_request → 本地预检 SPIR-V → 等冷 → 锁频 → A/B/A/B 交替跑测
//! → 采功耗与时延 → Welch t 检验 → 出 eval_report。
//!
//! 与 `bench` 的关系: 两者是并列的标尺, 互不依赖。`bench` 量 TFLite 模型,
//! 本模块量 Vulkan Compute Shader。共用的设备条件化 (等冷/锁频/还原) 与功耗遥测
//! 提在 `hwcond`, 统计提在 `gpustat`。
//!
//! 契约: `game_opt_loop/contracts/eval_request.schema.json` 与 `eval_report.schema.json`。
//! 上游 (game_opt_loop) 负责变异与防爆门禁, 本通路只负责物理采样与统计裁决,
//! **不做任何算法判断** —— 快慢好坏由数字说话。
//!
//! 纪律:
//!   - 不侵入任何游戏进程, 不在设备上留常驻服务或文件残留;
//!   - 所有 sysfs 写入退出前恢复, 无论中途成功失败;
//!   - 判定规则由 eval_request 在看到数据之前带入并冻结, 事后不得调整;
//!   - 量不到的指标如实留空, 绝不用默认值填补。

use crate::gpustat::{self, WelchResult};
use crate::hwcond::{self, PowerRail};
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// 设备侧产物目录。跑完即删, 不留残留。
const REMOTE_DIR: &str = "/data/local/tmp/phonefarm_gpuop";
/// 设备侧 Vulkan 算子 runner 的默认本地路径。
const DEFAULT_RUNNER: &str = "tools/vkop/android_aarch64_vkop_runner";

fn progress(msg: &str) {
    hwcond::progress("gpu-op", msg);
}

// ══════════════ 契约类型 (与 game_opt_loop/contracts 对齐) ══════════════

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
pub struct EvalProtocol {
    pub cool_c: f32,
    pub replay_seconds: u32,
    pub rounds: u32,
    pub alternating: bool,
    pub p_threshold: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvalRequest {
    pub version: u32,
    pub candidate_id: String,
    pub track: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub serial: Option<String>,
    pub package: String,
    pub replay_script: String,
    pub shader_path: String,
    pub spirv_path: String,
    pub budget_ms: f32,
    pub quality_baseline_db: f32,
    /// 在位冠军算子的实测耗时 (ms)。上游没有冠军时省略。
    ///
    /// refbench 载体的 A 臂跑的是**不挂算子**的场景, 所以基线臂的算子耗时
    /// 按定义是 0。没有这个字段的话, "候选比基线快吗" 在该载体上等价于
    /// `正数 < 0` —— 恒假, `is_pareto_improvement` 于是永远回 false,
    /// 哪怕候选确实比上一代冠军便宜一半。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub incumbent_latency_ms: Option<f32>,
    /// 画质真值的来源: 一张 PNG, 或一整个装着真机截帧的目录。
    ///
    /// 缺省 (None) 时退回 runner 内置的程序化图案 —— 那只是没有真实帧时的
    /// 确定性替代品, 高频细节远少于真实游戏画面, 量出来的绝对 dB 偏高约 11 dB
    /// (2026-09-22 实测同一算子: 程序化 42.826 dB vs 真机截帧 31.333 dB)。
    ///
    /// 命令行 `--reference` 优先于本字段。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quality_reference: Option<String>,
    pub protocol: EvalProtocol,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub v2_context: Option<Value>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
pub struct Metrics {
    pub operator_latency_ms: f32,
    /// `None` = 本次没有量到, 不是 0。
    ///
    /// headless 算子标尺量的是算子自身的 GPU 耗时, 帧时 p95 需要真实渲染上下文。
    /// 2026-09-22 真机回的第一份报告这一项就是 null, 而两边当时都声明成 f32,
    /// 上游直接解析失败 —— 契约必须容得下「未测量」这个状态。
    pub fps_p95_ms: Option<f32>,
    pub power_watt: f32,
    pub psnr_db: f32,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
pub struct Verdict {
    pub is_pareto_improvement: bool,
    pub p_value: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Status {
    Pass,
    Slow,
    PoorQuality,
    Crash,
    Reverted,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvalReport {
    pub candidate_id: String,
    pub status: Status,
    pub metrics: Metrics,
    pub verdict: Verdict,
    /// 恒为 "phonefarm": 本通路只产出真机实测数据。
    pub source: String,
    /// 插帧赛道的伪影读数与裁决。其它赛道为 None。
    ///
    /// 这是契约的**附加**字段: 上游用 serde 解析时会忽略不认识的字段,
    /// 所以加它不会破坏已有的 eval_report 消费者。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub artifacts: Option<Artifacts>,
    /// `psnr_db` 是对着哪张 (哪组) 参考图量出来的。
    ///
    /// 绝对 dB 不可跨参考图比较 (SPEC §4.1), 所以报告里不写清楚真值来源,
    /// 那个数字就没法跟别的报告对齐 —— 拿程序化图案的 38.7 dB 去卡真机截帧的
    /// 31.3 dB, 会把一个比基线好 2.19 dB 的算子判成 POOR_QUALITY。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub quality_reference: Option<QualityReference>,
    /// `power_watt` 是在什么供电状态下量的。同样是**加性**可选字段。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub power_source: Option<PowerSource>,
}

/// 这份 `power_watt` 是在什么供电状态下量的。跟着报告走, 让瓦数可回溯。
///
/// **为什么必须记**: 充电态下两条轨量到的都不是整机功耗 —— 电池轨是充放相抵的余量,
/// USB 轨含着灌进电池的那一份 —— 而两个读数都落在可信区间内, 光看数字分辨不出来。
/// 更要命的是充电电流随电量上升自己衰减 (CC→CV), 是一条单调漂移, 跨时间比较的
/// 两个候选之间会凭空多出一个"功耗改善"。报告里不写清楚供电状态, 这种假差异
/// 事后完全无法分辨 —— M1 演化那轮 6.26W→5.94W 就是这么来的, 因为连轨别都没记。
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PowerSource {
    /// `usb` | `battery`
    pub rail: String,
    /// `/sys/class/power_supply/battery/status` 原值
    pub battery_status: String,
    /// `current_now` (μA)。正 = 在往电池里充, 负 = 电池在放电。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub battery_current_now_ua: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub battery_capacity_pct: Option<i64>,
    /// 整机是不是真由电池供电。`false` 时 `power_watt` 必为 NaN (JSON 里是 null)。
    pub on_battery: bool,
    /// 这一轮有没有用 root 停充, 停的是哪个节点。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub charging_suspended_via: Option<String>,
    /// 旁路停充时开跑前的 USB 输入上限检查结论 (不够时以「供电不足」开头)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub usb_supply: Option<String>,
}

/// 画质真值的出处。跟着报告走, 让 dB 可回溯。
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct QualityReference {
    /// `real_frames` = 真机截帧; `procedural` = runner 内置程序化图案。
    pub kind: String,
    /// 本地来源路径 (单张 PNG 或整个目录)。程序化图案时为 None。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    /// 参与平均的帧数。程序化图案记 1。
    pub frames: u32,
    /// 逐帧文件名 (不含目录), 按使用顺序。换了帧集一眼看得出来。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub frame_files: Vec<String>,
    /// 逐帧 PSNR (候选臂), 与 `frame_files` 同序。均值即 `metrics.psnr_db`。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub candidate_psnr_db: Vec<f64>,
    /// 逐帧 PSNR (基线臂), 与 `frame_files` 同序。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub baseline_psnr_db: Vec<f64>,
}

impl QualityReference {
    /// runner 内置程序化图案 —— 没有真实帧时的退路。
    pub fn procedural() -> Self {
        Self {
            kind: "procedural".into(),
            source: None,
            frames: 1,
            frame_files: Vec::new(),
            candidate_psnr_db: Vec::new(),
            baseline_psnr_db: Vec::new(),
        }
    }
}

/// 校验 eval_request 的必填项与取值范围。
///
/// 上游契约写死了防噪声协议 (冷机 <32C / 60s 回放 / A/B 交替 / p<0.01),
/// 这里只检查它**自洽且可执行**, 不替上游改判据。
pub fn validate_request(r: &EvalRequest) -> Result<(), String> {
    if r.version != 1 {
        return Err(format!("不认识的 eval_request 版本 {}", r.version));
    }
    if let Some(c) = &r.v2_context {
        if !["conditions_requested","workload_requested","resolved_config"].iter().all(|k|c[*k].is_object()) {
            return Err("v2_context requires object requested/real resolved config fields".into());
        }
    }
    if r.candidate_id.trim().is_empty() {
        return Err("candidate_id 为空".into());
    }
    if !matches!(r.track.as_str(), "sr" | "frame_gen") {
        return Err(format!("未知赛道 {}", r.track));
    }
    if !(r.budget_ms > 0.0) {
        return Err(format!("budget_ms 必须为正, 收到 {}", r.budget_ms));
    }
    let p = &r.protocol;
    if p.rounds < 2 {
        return Err(format!(
            "protocol.rounds = {} 不足以做两样本检验, 每臂至少要 2 轮",
            p.rounds
        ));
    }
    if !(p.p_threshold > 0.0 && p.p_threshold < 1.0) {
        return Err(format!("p_threshold 必须落在 (0,1), 收到 {}", p.p_threshold));
    }
    if !(p.cool_c > 0.0 && p.cool_c < 100.0) {
        return Err(format!("cool_c 不合理: {}", p.cool_c));
    }
    if p.replay_seconds == 0 {
        return Err("replay_seconds 为 0".into());
    }
    Ok(())
}

// ══════════════ SPIR-V 本地预检 ══════════════

#[derive(Debug, Clone, PartialEq)]
pub struct SpirvInfo {
    pub version_major: u8,
    pub version_minor: u8,
    pub generator: u32,
    pub id_bound: u32,
    pub entry_points: Vec<String>,
    /// OpExecutionMode LocalSize 声明的工作组尺寸
    pub local_size: Option<[u32; 3]>,
    pub bytes: usize,
}

const SPIRV_MAGIC: u32 = 0x0723_0203;
const OP_ENTRY_POINT: u16 = 15;
const OP_EXECUTION_MODE: u16 = 16;
const EXEC_MODEL_GLCOMPUTE: u32 = 5;
const EXEC_MODE_LOCAL_SIZE: u32 = 17;

/// 解析并校验 SPIR-V 字节码。
///
/// 上游 game_opt_loop 已经用 naga 做过防爆门禁, 但**本通路不能假设上游一定跑过**:
/// 一份坏字节码推上真机, 轻则驱动报错, 重则 GPU 挂起要重启设备。
/// 这里只做结构性校验 (魔数/版本/入口点/工作组), 不重复上游的语义分析。
pub fn parse_spirv(bytes: &[u8]) -> Result<SpirvInfo, String> {
    if bytes.len() < 20 {
        return Err(format!("SPIR-V 太短 ({} 字节), 连文件头都不够", bytes.len()));
    }
    if bytes.len() % 4 != 0 {
        return Err(format!("SPIR-V 长度 {} 不是 4 的倍数", bytes.len()));
    }

    let le = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
    let be = u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
    let little = if le == SPIRV_MAGIC {
        true
    } else if be == SPIRV_MAGIC {
        false
    } else {
        return Err(format!(
            "SPIR-V 魔数不对: 期望 0x{SPIRV_MAGIC:08X}, 实际 0x{le:08X}"
        ));
    };

    let words: Vec<u32> = bytes
        .chunks_exact(4)
        .map(|c| {
            let a = [c[0], c[1], c[2], c[3]];
            if little {
                u32::from_le_bytes(a)
            } else {
                u32::from_be_bytes(a)
            }
        })
        .collect();

    let version = words[1];
    let version_major = ((version >> 16) & 0xFF) as u8;
    let version_minor = ((version >> 8) & 0xFF) as u8;
    if version_major != 1 {
        return Err(format!("不支持的 SPIR-V 主版本 {version_major}"));
    }

    let mut info = SpirvInfo {
        version_major,
        version_minor,
        generator: words[2],
        id_bound: words[3],
        entry_points: Vec::new(),
        local_size: None,
        bytes: bytes.len(),
    };

    // 指令流从第 5 个 word 开始
    let mut i = 5usize;
    while i < words.len() {
        let word_count = (words[i] >> 16) as usize;
        let opcode = (words[i] & 0xFFFF) as u16;
        if word_count == 0 {
            return Err(format!("SPIR-V 指令流在 word {i} 处 wordCount=0, 已损坏"));
        }
        if i + word_count > words.len() {
            return Err(format!(
                "SPIR-V 指令流在 word {i} 处越界 (声明 {word_count} words, 只剩 {})",
                words.len() - i
            ));
        }

        match opcode {
            OP_ENTRY_POINT if word_count >= 4 => {
                if words[i + 1] == EXEC_MODEL_GLCOMPUTE {
                    // 入口名是从第 4 个 word 起的 UTF-8 字面量, 以 NUL 结尾
                    let name_words = &words[i + 3..i + word_count];
                    let mut raw = Vec::with_capacity(name_words.len() * 4);
                    for w in name_words {
                        raw.extend_from_slice(&w.to_le_bytes());
                    }
                    let name = raw
                        .split(|b| *b == 0)
                        .next()
                        .map(|s| String::from_utf8_lossy(s).into_owned())
                        .unwrap_or_default();
                    info.entry_points.push(name);
                }
            }
            OP_EXECUTION_MODE if word_count >= 6 => {
                if words[i + 2] == EXEC_MODE_LOCAL_SIZE {
                    info.local_size = Some([words[i + 3], words[i + 4], words[i + 5]]);
                }
            }
            _ => {}
        }
        i += word_count;
    }

    if info.entry_points.is_empty() {
        return Err("SPIR-V 里没有 GLCompute 入口点, 这不是一个 compute shader".into());
    }
    if info.entry_points.len() > 1 {
        return Err(format!(
            "SPIR-V 有 {} 个 compute 入口点 {:?}, 算子插槽只挂载单入口",
            info.entry_points.len(),
            info.entry_points
        ));
    }

    // 工作组总调用数上限: Vulkan 规范下限 128, Adreno/Mali 实际 1024。
    if let Some([x, y, z]) = info.local_size {
        let total = x.saturating_mul(y).saturating_mul(z);
        if total == 0 {
            return Err(format!("工作组尺寸含 0 维: {:?}", info.local_size));
        }
        if total > 1024 {
            return Err(format!(
                "工作组 {:?} = {total} 个调用, 超过硬件上限 1024",
                info.local_size
            ));
        }
    }

    Ok(info)
}

// ══════════════ 评测载体 ══════════════

/// 算子挂在哪里跑。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Carrier {
    /// headless 工装 (`vkop_runner`): 孤立跑 dispatch。
    /// 给得出算子自己的微秒数与 PSNR, 给不出帧时与场景内功耗。
    Headless,
    /// refbench 的 `sr_pipeline` 场景: 算子挂在整条渲染管线尾部。
    /// 给得出真实帧时与场景内整机功耗; 画质不在这里量 ——
    /// refbench 的边界不允许它自带任何测量逻辑。
    Refbench,
}

impl Carrier {
    pub fn as_str(&self) -> &'static str {
        match self {
            Carrier::Headless => "headless",
            Carrier::Refbench => "refbench",
        }
    }
}

/// refbench 的接口常量。来源: `../refbench/contract/launch.json`。
pub mod refbench {
    pub const PACKAGE: &str = "io.github.hgamey.refbench";
    pub const ACTIVITY: &str = "io.github.hgamey.refbench/android.app.NativeActivity";
    pub const SCENE: &str = "sr_pipeline";
    /// 驱动提交线程名。Adreno 驱动从自己的 in-process 线程提交 cmdbatch,
    /// refbench 通过 /proc/self/task/<tid>/comm 改名, 便于按 comm 过滤。
    pub const SUBMIT_COMM: &str = "RefbenchDrv";
    pub const FILES: &str = "/storage/emulated/0/Android/data/io.github.hgamey.refbench/files";
    /// 契约写死每帧一次提交 —— 因此提交间隔可直接当帧间隔。
    /// 原神那种每帧两次提交的必须先自检, 否则帧率会算成两倍。
    pub const SUBMITS_PER_FRAME: u32 = 1;
    pub const REMOTE_SPV: &str = "/data/local/tmp/phonefarm_gpuop/postfx_candidate.spv";
}

// ══════════════ A/B/A/B 调度 ══════════════

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Arm {
    /// A: 基线算子 (当前线上/上一代优胜)
    Baseline,
    /// B: 待测候选算子
    Candidate,
}

impl Arm {
    pub fn as_str(&self) -> &'static str {
        match self {
            Arm::Baseline => "A",
            Arm::Candidate => "B",
        }
    }
}

/// 生成跑测顺序。
///
/// `rounds` 是**每臂**的轮数, 总跑测次数 = rounds * 2。
///
/// 交替 (A/B/A/B) 是防热漂移的关键: 设备跑久了会升温降频, 若先把 A 跑满再跑 B,
/// B 全程都在更热的机器上跑, 温度差会被整包算进算子差异里。交替之后两臂
/// 均匀分布在整段时间上, 热漂移对两臂的影响一阶抵消。
pub fn ab_schedule(rounds: u32, alternating: bool) -> Vec<Arm> {
    let mut out = Vec::with_capacity(rounds as usize * 2);
    if alternating {
        for _ in 0..rounds {
            out.push(Arm::Baseline);
            out.push(Arm::Candidate);
        }
    } else {
        for _ in 0..rounds {
            out.push(Arm::Baseline);
        }
        for _ in 0..rounds {
            out.push(Arm::Candidate);
        }
    }
    out
}

// ══════════════ 设备侧 runner 回包契约 ══════════════

/// 设备侧 Vulkan runner 的一次跑测结果。
///
/// runner 是一个推到 `/data/local/tmp` 的原生可执行文件 (与 `bench` 用
/// `benchmark_model` 同一套路), 它负责: 建 Vulkan 设备 → 分配常驻显存的输入/输出
/// image → 跑 N 次 dispatch, 用 VkQueryPool 时间戳量 GPU 侧耗时 → 回读一次算 PSNR
/// → 往 stdout 打一个 JSON 对象。
///
/// 画面全程不出显存: 这是上游 game_opt_loop 零拷贝红线在设备侧的落地点。
#[derive(Debug, Clone, PartialEq)]
pub struct RunnerReport {
    pub ok: bool,
    /// 每次 dispatch 的 GPU 侧耗时 (微秒)
    pub timing_us: Vec<f64>,
    pub psnr_db: Option<f64>,
    /// 插帧专属: HUD 区域 PSNR。HUD 在屏幕空间静止, 正确插帧应与真值逐像素吻合;
    /// 块匹配把 HUD 当成运动内容时字会被拉成双份, 这个数断崖下跌。
    pub hud_ghost_db: Option<f64>,
    /// 插帧专属: 落在前后帧邻域包络之外的像素占比 (%)。
    /// 出了包络 = 算子凭空造了前后帧都没有的内容, 即拉扯/撕裂。
    pub stretch_pct: Option<f64>,
    pub device_name: Option<String>,
    pub error: Option<String>,
}

impl RunnerReport {
    /// 中位耗时 (毫秒)。
    pub fn median_ms(&self) -> Option<f64> {
        if self.timing_us.is_empty() {
            return None;
        }
        Some(gpustat::percentile(&self.timing_us, 0.5) / 1000.0)
    }
}

// The completion trailer is emitted by the parent shell after runner teardown.
fn completed_runner_command(command: &str) -> String {
    format!("{command}; runner_status=$?; printf '\\nPHONEFARM_VKOP_EXIT=%s\\n' \"$runner_status\"")
}

fn parse_completed_runner_output(output: &str) -> Result<RunnerReport, String> {
    let (body, receipt) = output.trim_end().rsplit_once('\n')
        .ok_or("runner completion receipt is missing")?;
    let status = receipt.strip_prefix("PHONEFARM_VKOP_EXIT=")
        .and_then(|value| value.parse::<u32>().ok())
        .ok_or("runner completion receipt is missing or invalid")?;
    if body.lines().any(|line| line.starts_with("PHONEFARM_VKOP_EXIT=")) {
        return Err("runner completion receipt is duplicated".into());
    }
    let report = parse_runner_output(body);
    if status != 0 {
        // A structured negative result remains candidate failure, never fitness.
        if let Ok(ref failed) = report {
            if !failed.ok { return report; }
        }
        return Err(format!("runner process failed with exit status {status}; JSON metrics rejected"));
    }
    report
}

/// Parse a raw runner JSON payload; execution callers must also verify completion.
pub fn parse_runner_output(stdout: &str) -> Result<RunnerReport, String> {
    let trimmed = stdout.trim();
    if trimmed.is_empty() {
        return Err("runner 回包为空".into());
    }

    // 从**最前面**的 `{` 往后找第一个"长得像回包"的对象。
    // 反过来从尾部找是错的: 嵌套对象 (比如 timing_us 里那个) 自己也是合法 JSON,
    // 用无类型 Value 解析时会先命中它, 于是 ok/samples 全都读空,
    // 一次成功的跑测被读成"没有样本"。
    let looks_like_report =
        |v: &Value| v.get("ok").is_some() || v.get("timing_us").is_some();

    let mut parsed: Option<Value> = None;
    if let Ok(v) = serde_json::from_str::<Value>(trimmed) {
        if looks_like_report(&v) {
            parsed = Some(v);
        }
    }
    if parsed.is_none() {
        let bytes = trimmed.as_bytes();
        for start in (0..bytes.len()).filter(|&i| bytes[i] == b'{') {
            let mut stream =
                serde_json::Deserializer::from_str(&trimmed[start..]).into_iter::<Value>();
            if let Some(Ok(v)) = stream.next() {
                if looks_like_report(&v) {
                    parsed = Some(v);
                    break;
                }
            }
        }
    }
    let v = parsed.ok_or_else(|| {
        format!(
            "runner 回包不是合法 JSON: {}",
            trimmed.chars().take(200).collect::<String>()
        )
    })?;

    let ok = v.get("ok").and_then(Value::as_bool).unwrap_or(false);
    let error = v
        .get("error")
        .and_then(Value::as_str)
        .map(|s| s.to_string());

    let timing_us: Vec<f64> = v
        .pointer("/timing_us/samples")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_f64).collect())
        .unwrap_or_default();

    if ok && timing_us.is_empty() {
        return Err("runner 报告成功却没有任何时延样本".into());
    }

    Ok(RunnerReport {
        ok,
        timing_us,
        psnr_db: v.get("psnr_db").and_then(Value::as_f64),
        hud_ghost_db: v.pointer("/artifacts/hud_ghost_db").and_then(Value::as_f64),
        stretch_pct: v.pointer("/artifacts/stretch_pct").and_then(Value::as_f64),
        device_name: v
            .pointer("/device/name")
            .and_then(Value::as_str)
            .map(|s| s.to_string()),
        error,
    })
}

// ══════════════ 冻结判定规则 ══════════════

/// 插帧赛道的伪影判据。
///
/// 这里只比候选臂与基线臂 (同场 A/B)。插帧画质的验收 (对原生 2 倍帧率真值逐帧比、HUD 逐像素、
/// 最差运动块、包络外像素对真值自噪声、序列闪烁) 在 `interpjudge` / `phonefarm interp-judge`。
///
/// 为什么不用绝对阈值: 这两个量和参考场景强相关 (运动幅度、HUD 面积、纹理复杂度
/// 都会改变绝对值), 跟 PSNR 的绝对 dB 一样不可跨场景搬。所以判据是
/// **相对本场基线臂不得明显变差** —— A/B 本就同场同素材跑, 这个比较才有意义。
///
/// 容差是工程判断, 不是测量结果: HUD 重影 3 dB, 拉扯 1 个百分点。
/// 实测参考: 干净算子 hud_ghost 99.0 dB / stretch 0.00%,
/// 故意做坏的对照组 5.32 dB / 55.72% —— 真出问题时差距是数量级的, 不在容差附近。
#[derive(Debug, Clone, Copy)]
pub struct ArtifactGates {
    pub hud_ghost_margin_db: f64,
    pub stretch_margin_pct: f64,
}

impl Default for ArtifactGates {
    fn default() -> Self {
        Self {
            hud_ghost_margin_db: 3.0,
            stretch_margin_pct: 1.0,
        }
    }
}

/// 一次伪影裁决的结果。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Artifacts {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hud_ghost_db: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stretch_pct: Option<f64>,
    /// 相对基线臂是否出现了明显的伪影退化。
    pub regressed: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

/// 拿候选与基线的伪影读数做裁决。两边都有读数才谈得上比较。
pub fn judge_artifacts(
    gates: &ArtifactGates,
    cand_ghost: Option<f64>,
    base_ghost: Option<f64>,
    cand_stretch: Option<f64>,
    base_stretch: Option<f64>,
) -> Artifacts {
    let mut reason: Option<String> = None;

    if let (Some(c), Some(b)) = (cand_ghost, base_ghost) {
        if c < b - gates.hud_ghost_margin_db {
            reason = Some(format!(
                "HUD 重影: {c:.2} dB 比基线 {b:.2} dB 差了 {:.2} dB (容差 {:.1})",
                b - c,
                gates.hud_ghost_margin_db
            ));
        }
    }
    if reason.is_none() {
        if let (Some(c), Some(b)) = (cand_stretch, base_stretch) {
            if c > b + gates.stretch_margin_pct {
                reason = Some(format!(
                    "拉扯果冻: {c:.2}% 的像素落在前后帧包络之外, 基线只有 {b:.2}% (容差 {:.1})",
                    gates.stretch_margin_pct
                ));
            }
        }
    }

    Artifacts {
        hud_ghost_db: cand_ghost,
        stretch_pct: cand_stretch,
        regressed: reason.is_some(),
        reason,
    }
}

/// 判定所需的全部输入。构造之后不可变 —— 判据在看到数据之前就定死了。
#[derive(Debug, Clone, Copy)]
pub struct FrozenRules {
    pub budget_ms: f32,
    pub quality_baseline_db: f32,
    pub p_threshold: f64,
    /// 在位冠军耗时。见 `EvalRequest::incumbent_latency_ms`。
    pub incumbent_latency_ms: Option<f32>,
}

impl FrozenRules {
    pub fn from_request(r: &EvalRequest) -> Self {
        Self {
            budget_ms: r.budget_ms,
            quality_baseline_db: r.quality_baseline_db,
            p_threshold: r.protocol.p_threshold,
            incumbent_latency_ms: r.incumbent_latency_ms,
        }
    }
}

/// 由实测数据与冻结规则给出结论。
///
/// 判定次序是有讲究的, 不能换:
///   1. **崩了** → CRASH。没有数据可谈。
///   2. **超预算** → SLOW。算子自己就超了单帧硬预算, 后面的都不用看。
///   3. **画质掉基线** → POOR_QUALITY。§红线: 画质是约束不是目标,
///      严禁用模糊换帧率。这一条排在显著性前面 —— 哪怕"显著地更快",
///      掉了画质也是不合格品。
///   4. **过** → PASS。
///
/// `is_pareto_improvement` 只在「统计显著 且 确实更快」时为真。
/// 不显著就是不显著: 快了 0.3% 但 p=0.4, 那是噪声, 不是改进。
pub fn decide(
    rules: &FrozenRules,
    candidate: &Metrics,
    baseline: Option<&Metrics>,
    welch: Option<&WelchResult>,
    crashed: bool,
) -> (Status, Verdict) {
    // p 值取不到时记 1.0 —— 「未检验」, 不伪造一个漂亮的小数字。
    let p_value = welch.map(|w| w.p_value).unwrap_or(1.0);

    if crashed {
        return (
            Status::Crash,
            Verdict {
                is_pareto_improvement: false,
                p_value,
            },
        );
    }

    if candidate.operator_latency_ms > rules.budget_ms {
        return (
            Status::Slow,
            Verdict {
                is_pareto_improvement: false,
                p_value,
            },
        );
    }

    if candidate.psnr_db < rules.quality_baseline_db {
        return (
            Status::PoorQuality,
            Verdict {
                is_pareto_improvement: false,
                p_value,
            },
        );
    }

    // 显著性只有在有基线对照时才谈得上。
    let significant = welch
        .map(|w| w.significant(rules.p_threshold))
        .unwrap_or(false);
    // "更快" 要跟谁比:
    //   上游带了在位冠军耗时 → 跟冠军比 (refbench 载体下唯一说得通的问法:
    //     基线臂根本没挂算子, 它的算子耗时是 0)。
    //   没带 → 退回跟基线臂比 (headless 载体: A 臂跑的就是上一代算子)。
    let reference_ms = rules
        .incumbent_latency_ms
        .or_else(|| baseline.map(|b| b.operator_latency_ms));
    let faster = reference_ms
        .map(|r| candidate.operator_latency_ms < r)
        .unwrap_or(false);

    (
        Status::Pass,
        Verdict {
            is_pareto_improvement: significant && faster,
            p_value,
        },
    )
}

// ══════════════ 参数 ══════════════

#[derive(Debug, Clone)]
pub struct GpuOpArgs {
    pub request_path: Option<String>,
    pub serial: Option<String>,
    pub as_json: bool,
    pub runner_bin: Option<String>,
    pub power_rail: PowerRail,
    /// 测量期间 root 停充, 测完自动恢复。默认关 (有副作用的写操作)。
    pub suspend_charging: bool,
    pub require_discharging: bool,
    /// 评测载体。
    pub carrier: Carrier,
    /// refbench 场景的 fragment 负载强度。
    pub intensity: u32,
    /// refbench 每臂渲染帧数。
    pub frames: u32,
    pub out_dir: Option<String>,
    /// 画质真值参考帧 (PNG/JPEG)。缺省用 runner 内置的程序化图案。
    pub reference: Option<String>,
    pub cool_timeout_s: u64,
    pub gpu_level: Option<u32>,
    pub unlock_only: bool,
    pub inspect_hardware: bool,
    pub allow_missing_power: bool,
    /// refbench 载体跑完之后, 补一次画质测量。
    ///
    /// refbench 只量帧时与瓦数 —— 它是个渲染靶场, 按边界约定不带任何测量逻辑,
    /// 拿不出 PSNR。于是 refbench 载体下 `psnr_db` 恒为 null, 上游的画质硬底线
    /// **一次都不会触发**。一个永远不触发的门禁比没有门禁更糟: 它让人以为画质
    /// 被守住了。
    ///
    /// 补测为什么不需要 A/B/A/B + t 检验: 画质对 (着色器, 参考帧) 是**确定性**的,
    /// 同一份输入跑一百遍是同一个 dB。A/B/A/B、等冷、锁频、Welch 检验那一整套
    /// 是用来对付**热漂移**的, 而热漂移只污染时延和功耗, 污染不了算术。
    /// 所以这里两臂各跑一次就够, 不等冷不锁频 —— 省下来的是几分钟真机时间。
    pub quality_pass: bool,
}

const USAGE: &str = "用法: phonefarm gpu-op --request <eval_request.json> [--serial S] [--json]\n\
\x20                    [--runner <本地 runner 路径>] [--power-rail usb|battery]\n\
\x20                    [--carrier headless|refbench] [--out 目录]\n\
\x20                    [--reference <参考帧.png | 参考帧目录>]  画质真值; 目录则整组取均值\n\
\x20                    [--intensity N] [--frames N] [--cool-timeout-s N] [--gpu-level N]\n\
\x20                    [--no-quality-pass]  refbench 载体下跳过画质补测 (画质硬底线随之失守)\n\
\x20      phonefarm gpu-op --serial S --unlock        回滚遗留的锁频态\n\
\x20      phonefarm gpu-op --serial S --inspect-hardware --json  只读检查 GPU 接口，不运行负载或写设备\n\
\n\
Compute Shader 算子的真机标尺: 等冷 → 锁频 → A/B/A/B 交替跑测 → Welch t 检验 → eval_report。\n\
契约见 game_opt_loop/contracts/{eval_request,eval_report}.schema.json。";

pub fn parse_args(args: &[String]) -> Result<GpuOpArgs, String> {
    let mut a = GpuOpArgs {
        request_path: None,
        serial: None,
        as_json: false,
        runner_bin: None,
        power_rail: PowerRail::Usb,
        suspend_charging: false,
        require_discharging: false,
        carrier: Carrier::Headless,
        intensity: 6,
        frames: 3000,
        out_dir: None,
        reference: None,
        cool_timeout_s: 600,
        gpu_level: None,
        unlock_only: false,
        inspect_hardware: false,
        allow_missing_power: false,
        // 缺省开: 画质是硬底线, 关掉它必须是个显式动作。
        quality_pass: true,
    };
    let mut it = args.iter();
    while let Some(k) = it.next() {
        match k.as_str() {
            "--request" => a.request_path = it.next().cloned(),
            "--serial" => a.serial = it.next().cloned(),
            "--json" => a.as_json = true,
            "--runner" => a.runner_bin = it.next().cloned(),
            "--out" => a.out_dir = it.next().cloned(),
            "--reference" => a.reference = it.next().cloned(),
            "--carrier" => {
                a.carrier = match it.next().map(|s| s.as_str()) {
                    Some("headless") => Carrier::Headless,
                    Some("refbench") => Carrier::Refbench,
                    other => {
                        return Err(format!("--carrier 只能是 headless 或 refbench, 收到 {other:?}"))
                    }
                }
            }
            "--intensity" => {
                a.intensity = it
                    .next()
                    .and_then(|v| v.parse().ok())
                    .ok_or("--intensity 需要正整数")?
            }
            "--frames" => {
                a.frames = it
                    .next()
                    .and_then(|v| v.parse().ok())
                    .ok_or("--frames 需要正整数")?
            }
            "--unlock" => a.unlock_only = true,
            "--inspect-hardware" => a.inspect_hardware = true,
            "--allow-missing-power" => a.allow_missing_power = true,
            "--suspend-charging" => a.suspend_charging = true,
            "--require-discharging" => a.require_discharging = true,
            "--no-quality-pass" => a.quality_pass = false,
            "--cool-timeout-s" => {
                a.cool_timeout_s = it
                    .next()
                    .and_then(|v| v.parse().ok())
                    .ok_or("--cool-timeout-s 需要一个正整数")?
            }
            "--gpu-level" => {
                a.gpu_level = Some(
                    it.next()
                        .and_then(|v| v.parse().ok())
                        .ok_or("--gpu-level 需要一个档位索引")?,
                )
            }
            "--power-rail" => {
                a.power_rail = match it.next().map(|s| s.as_str()) {
                    Some("usb") => PowerRail::Usb,
                    Some("battery") => PowerRail::Battery,
                    other => return Err(format!("--power-rail 只能是 usb 或 battery, 收到 {other:?}")),
                }
            }
            other => return Err(format!("不认识的参数 {other}\n\n{USAGE}")),
        }
    }
    if a.inspect_hardware && (a.unlock_only || a.request_path.is_some() || a.suspend_charging) {
        return Err("hardware inspection cannot be combined with request, unlock or charging writes".into());
    }
    if a.allow_missing_power && (a.require_discharging || a.suspend_charging) {
        return Err("allow-missing-power cannot weaken required discharge or charging control".into());
    }
    if !a.unlock_only && !a.inspect_hardware && a.request_path.is_none() {
        return Err(format!("缺少 --request\n\n{USAGE}"));
    }
    Ok(a)
}

/// 找设备侧 runner 二进制。
///
/// 相对路径必须同时相对**当前目录**与 **phonefarm 可执行文件所在目录**去找。
/// 只按 CWD 找会有一类只在集成时才暴露的坑: 手工跑都在 phonefarm 目录下,
/// 一切正常; 换成上游 game_opt_loop 调起来, CWD 变成它自己的仓库根,
/// `tools/vkop/...` 就指到了一个不存在的地方 —— 而错误信息只会说"找不到 runner",
/// 看不出是路径基准的问题。
fn locate_runner(a: &GpuOpArgs) -> Result<String, String> {
    let exe_rel = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.join(DEFAULT_RUNNER)))
        .map(|p| p.to_string_lossy().into_owned());

    let cands: Vec<String> = a
        .runner_bin
        .iter()
        .cloned()
        .chain(std::env::var("PF_VKOP_RUNNER").ok())
        .chain(std::iter::once(DEFAULT_RUNNER.to_string()))
        .chain(exe_rel)
        .collect();
    for c in &cands {
        if std::fs::metadata(c).map(|m| m.is_file()).unwrap_or(false) {
            return Ok(c.clone());
        }
    }
    Err(format!(
        "找不到设备侧 Vulkan 算子 runner (试过 {cands:?})。\n\
         该二进制负责在真机上建 Vulkan 设备、分配常驻显存的输入/输出 image、\n\
         用 VkQueryPool 时间戳量 dispatch 的 GPU 侧耗时并算 PSNR。\n\
         用 NDK (aarch64-linux-androidNN-clang++) 编出 arm64-v8a 二进制后, \n\
         放到 tools/vkop/ 或用 --runner / PF_VKOP_RUNNER 指路。\n\
         回包契约见 src/gpuop.rs 的 RunnerReport 与 https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_GPU_OP.md §6。"
    ))
}

// ══════════════ 主流程 ══════════════

fn battery_evidence(b: &hwcond::BatteryState) -> Value {
    let known = matches!(b.status.trim().to_ascii_lowercase().as_str(),
        "charging" | "discharging" | "not charging" | "full");
    let discharge = if known && b.current_ua.is_some_and(|c| c != 0) { Some(b.on_battery()) } else { None };
    serde_json::json!({"status":b.status,"current_now_ua":b.current_ua,
        "capacity_pct":b.capacity_pct,"on_battery":discharge,"source":"battery_sysfs"})
}

pub fn run_gpu_op(args: &[String]) -> i32 {
    let mut a = match parse_args(args) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("{e}");
            return 2;
        }
    };

    let out_dir = a
        .out_dir
        .clone()
        .unwrap_or_else(|| std::env::temp_dir().join("phonefarm_gpuop").to_string_lossy().into_owned());
    if let Err(e) = std::fs::create_dir_all(&out_dir) {
        eprintln!("建不了输出目录 {out_dir}: {e}");
        return 2;
    }
    a.out_dir = Some(out_dir.clone());
    if a.inspect_hardware {
        let lease = match crate::devlease::Lease::target(a.serial.as_deref(), "gpu-inspect") {
            Ok(v) => v, Err(e) => { eprintln!("{e}"); return 2; }
        };
        let phone = crate::device::Device::new(Some(lease.serial.clone()), out_dir);
        let root = hwcond::root_ok(&phone);
        let snapshot = if root { hwcond::read_snapshot(&phone) } else { hwcond::Snapshot::default() };
        let backend = hwcond::gpu_control_backend(&snapshot);
        println!("{}", serde_json::json!({
            "scope":"read_only_hardware_inspection","serial":lease.serial,"root_verified":root,
            "gpu_control_backend":backend,"gpu_model":snapshot.gpu_model,
            "gpu_frequency_hz":snapshot.gpuclk_hz,"mtk_stack_frequency_hz":snapshot.mtk_stackclk_hz,
            "gpu_effective_ceiling_index":snapshot.gpu_max_level,"gpu_effective_floor_index":snapshot.gpu_min_level,
            "ged_user_limits":snapshot.mtk_ged,"opp_frequencies_hz":snapshot.gpu_freqs,
            "soc_temperature":hwcond::soc_max_c(&hwcond::read_thermal(&phone)),
            "performance_measured":false,"control_write_tested":false
        }));
        return if root && backend != "unavailable" { 0 } else { 2 };
    }
    if a.unlock_only {
        let _lease = match crate::devlease::Lease::target(a.serial.as_deref(), "gpu-op-unlock") {
            Ok(v) => v, Err(e) => { eprintln!("{e}"); return 2; }
        };
        a.serial = Some(_lease.serial.clone());
        let state_path = match hwcond::lock_state_path("gpuop", &a.serial) {
            Ok(v) => v, Err(e) => { eprintln!("{e}"); return 2; }
        };
        let phone = crate::device::Device::new(a.serial.clone(), out_dir.clone());
        return match hwcond::recover_stale_lock("gpu-op", &phone, &state_path) {
            Ok(Some(true)) => {
                progress("遗留锁频态已回滚");
                0
            }
            Ok(Some(false)) => {
                eprintln!("回滚失败, 设备可能仍处于锁频态");
                2
            }
            Ok(None) => {
                progress("没有遗留的锁频态");
                0
            }
            Err(e) => { eprintln!("{e}"); 2 }
        };
    }

    // ---- 读契约 ----
    let path = a.request_path.clone().unwrap();
    let raw = match std::fs::read_to_string(&path) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("读不了 {path}: {e}");
            return 2;
        }
    };
    let req: EvalRequest = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("{path} 不符合 eval_request 契约: {e}");
            return 2;
        }
    };
    if let Err(e) = validate_request(&req) {
        eprintln!("eval_request 校验失败: {e}");
        return 2;
    }
    // 每臂 4 轮是生产口径。轮次少于此照样跑, 但结论的说服力会明显变薄:
    // 每臂 2 轮时 Welch 的自由度低到个位数, 单轮的一次热漂移就能翻转判定。
    // 不拦, 但必须说出来 —— 免得薄样本的 p 值被当成厚样本的 p 值用。
    const PRODUCTION_ROUNDS: u32 = 4;
    if req.protocol.rounds < PRODUCTION_ROUNDS {
        progress(&format!(
            "注意: 每臂仅 {} 轮, 低于生产口径 {}。样本偏薄, p 值与功耗差的说服力相应下降",
            req.protocol.rounds, PRODUCTION_ROUNDS
        ));
    }

    progress(&format!(
        "候选 {} | 赛道 {} | 预算 {:.2} ms | 画质基线 {:.3} dB | 每臂 {} 轮 | p<{}",
        req.candidate_id,
        req.track,
        req.budget_ms,
        req.quality_baseline_db,
        req.protocol.rounds,
        req.protocol.p_threshold
    ));

    // ---- 本地预检 SPIR-V: 坏字节码绝不推上真机 ----
    let spv = match std::fs::read(&req.spirv_path) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("读不了 SPIR-V {}: {e}", req.spirv_path);
            return 2;
        }
    };
    let info = match parse_spirv(&spv) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("SPIR-V 预检未通过, 拒绝下发真机: {e}");
            return 2;
        }
    };
    progress(&format!(
        "SPIR-V 预检通过: v{}.{} | {} 字节 | 入口 {:?} | 工作组 {:?}",
        info.version_major, info.version_minor, info.bytes, info.entry_points, info.local_size
    ));

    // Resolve/pin request serial before every device command; retain lease through restoration.
    let requested = a.serial.clone().or_else(|| req.serial.clone());
    let _lease = match crate::devlease::Lease::target(requested.as_deref(), "gpu-op") {
        Ok(v) => v, Err(e) => { eprintln!("{e}"); return 2; }
    };
    a.serial = Some(_lease.serial.clone());
    let state_path = match hwcond::lock_state_path("gpuop", &a.serial) {
        Ok(v) => v, Err(e) => { eprintln!("{e}"); return 2; }
    };
    let charge_state = match hwcond::lock_state_path("charge", &a.serial) {
        Ok(v) => v, Err(e) => { eprintln!("{e}"); return 2; }
    };
    let phone = crate::device::Device::new(a.serial.clone(), out_dir.clone());

    // ---- root 与 runner ----
    if !hwcond::root_ok(&phone) {
        eprintln!("设备无 root: 等冷/锁频/功耗遥测都需要 root");
        return 2;
    }
    // 必须先处理遗留状态, 再停充、部署或开始新一轮条件化。
    match hwcond::recover_stale_lock("gpu-op", &phone, &state_path) {
        Ok(Some(false)) => {
            eprintln!("上次遗留的锁频态回滚失败, 拒绝继续部署或测量");
            return 2;
        }
        Ok(_) => {}
        Err(e) => { eprintln!("{e}"); return 2; }
    };
    match hwcond::recover_stale_charge_ctl(&phone, &charge_state) {
        Ok(Some(false)) => {
            eprintln!("stale charging state restoration failed"); return 2;
        }
        Ok(_) => {}
        Err(e) => { eprintln!("{e}"); return 2; }
    }
    // refbench 载体不需要 headless 工装来跑主测 —— 算子由 refbench 自己装载。
    // 之前这里无条件去找 runner, 于是 refbench 载体也被一个用不到的二进制卡死。
    //
    // 但画质补测要用它: refbench 拿不出 PSNR, 补测就只能落在 headless runner 上。
    // 找不到 runner 时 refbench 不该整次失败 —— 主测 (帧时/瓦数) 照跑,
    // 只是画质这一项如实留空, 并把原因喊出来。
    let needs_runner = a.carrier == Carrier::Headless;
    let runner = match locate_runner(&a) {
        Ok(v) => {
            progress(&format!("设备侧 runner: {v}"));
            v
        }
        Err(e) => {
            if needs_runner {
                eprintln!("{e}");
                return 2;
            }
            progress(&format!("画质补测不可用 (找不到 runner): {e}"));
            String::new()
        }
    };
    let quality_pass = a.quality_pass && a.carrier == Carrier::Refbench && !runner.is_empty();

    // ---- 停充 ----
    // 遗留停充已在部署前处理, 与本次是否要求停充无关。
    let mut suspend = None;
    if a.suspend_charging {
        match hwcond::ChargeSuspend::apply(&phone, &charge_state) {
            Ok(g) => {
                hwcond::progress("gpu-op", &format!("测量期间停充: {}", g.node()));
                suspend = Some(g);
            }
            Err(e) => {
                eprintln!("停充失败: {e}");
                return 2;
            }
        }
    }
    // 从这里往下任何一条 return 都必须先恢复充电 —— 宏包住, 免得漏掉哪一条
    macro_rules! bail {
        ($code:expr) => {{
            if let Some(mut g) = suspend.take() {
                if !g.restore(&phone) {
                    eprintln!("退出时充电恢复未确认 ({}); 状态文件保留, 需要恢复", g.node());
                    return 2;
                }
            }
            return $code;
        }};
    }

    // ---- 功耗轨可用性先探一次: 量不了就当场说, 不要跑完 10 分钟再报 0 W ----
    let battery = hwcond::read_battery_state(&phone);
    let probe: Vec<_> = (0..3)
        .filter_map(|_| {
            let s = hwcond::read_power(&phone, a.power_rail);
            std::thread::sleep(std::time::Duration::from_millis(200));
            s
        })
        .collect();
    // 供电判定与 sample_env 通路同一个函数 (hwcond::classify_supply); 停充了就认旁路
    let power_ok = hwcond::power_usable_supply(a.power_rail, &probe, Some(&battery), suspend.is_some());
    // 开跑前查 USB 输入上限: 不够就明确说「供电不足」, 不让它静默变成功耗缺测
    let usb_supply = suspend.as_ref().filter(|g| g.mode() == hwcond::SupplyMode::Bypass).map(|_| {
        let c = hwcond::read_usb_supply(&phone, hwcond::USB_SUPPLY_NEED_W);
        hwcond::progress("gpu-op", &c.note);
        c.note
    });
    let power_source = PowerSource {
        rail: a.power_rail.as_str().to_string(),
        battery_status: battery.status.clone(),
        battery_current_now_ua: battery.current_ua,
        battery_capacity_pct: battery.capacity_pct,
        on_battery: battery.on_battery(),
        charging_suspended_via: suspend.as_ref().map(|g| g.node().to_string()),
        usb_supply,
    };
    let power_unavailable = match optional_power_result(power_ok, a.allow_missing_power) {
        Ok(reason) => reason,
        Err(e) => { eprintln!("功耗采样不可用: {e}"); bail!(2); }
    };
    if let Some(reason) = &power_unavailable {
        progress(&format!("功耗不可用，按显式请求仅测时延/画质，power_watt=null: {reason}"));
    }

    // ---- 部署 ----
    if a.carrier == Carrier::Refbench {
        let installed = phone.shell(
            &format!("pm list packages {} 2>/dev/null", refbench::PACKAGE),
            15_000,
        );
        if !installed.contains(refbench::PACKAGE) {
            eprintln!(
                "设备上没装 refbench ({})。出包: cd ../refbench && bash build/build.sh, \n\
                 再 adb install -r build/out/refbench.apk",
                refbench::PACKAGE
            );
            bail!(2);
        }
        phone.shell(
            &format!("mkdir -p $(dirname {})", refbench::REMOTE_SPV),
            10_000,
        );
        if !phone.push_file(&req.spirv_path, refbench::REMOTE_SPV) {
            eprintln!("推送候选 SPIR-V 到 {} 失败", refbench::REMOTE_SPV);
            bail!(2);
        }
        phone.shell(&format!("chmod 644 {}", refbench::REMOTE_SPV), 10_000);
        progress(&format!(
            "载体 refbench: 场景 {} | 每臂 {} 帧 | intensity {} | 每帧 {} 次提交",
            refbench::SCENE,
            a.frames,
            a.intensity,
            refbench::SUBMITS_PER_FRAME
        ));
    }

    // ---- 部署: runner + 候选/基线 SPIR-V (headless 载体) ----
    if a.carrier == Carrier::Headless {
    if let Err(e) = deploy(&phone, &runner, &req) {
        eprintln!("部署失败: {e}");
        bail!(2);
    }
    }

    // ---- 参考帧: 有就转成裸 RGBA8 推上去, 没有就用 runner 内置的程序化图案 ----
    //
    // 来源优先级: 命令行 --reference > eval_request.quality_reference > 程序化图案。
    // 命令行在前是因为它是人手动指定的一次性覆盖; 契约字段是上游的常设配置。
    let ref_source = a
        .reference
        .clone()
        .or_else(|| req.quality_reference.clone());
    let mut remote_references: Vec<String> = Vec::new();
    let mut quality_reference = QualityReference::procedural();
    if let Some(src) = ref_source.as_ref() {
        let mut frames = match resolve_reference_frames(src) {
            Ok(f) => f,
            Err(e) => {
                eprintln!("参考帧不可用: {e}");
                cleanup(&phone);
                bail!(2);
            }
        };
        // 只准备**会被读到**的那几张。每张要解码、裁剪、写 8.3 MB 裸 RGBA
        // 再 adb push 上去; 推一批没人读的帧既慢又平白多出一堆失败面 ——
        // 一次推送失败会为着没人看的帧把整轮作废。
        //   headless: 画质走主测内联, 只读第一张;
        //   refbench + 关了画质补测: 一张都不读 (refbench 自己不碰参考帧)。
        let wanted = if a.carrier == Carrier::Headless {
            1
        } else if quality_pass {
            frames.len()
        } else {
            0
        };
        if wanted < frames.len() {
            progress(&format!(
                "参考帧 {} 张, 本次载体只会读到 {} 张, 其余不推",
                frames.len(),
                wanted
            ));
            frames.truncate(wanted);
        }
        let mut names = Vec::new();
        for (i, frame) in frames.iter().enumerate() {
            let raw = std::path::Path::new(&out_dir).join(format!("reference_{i:02}.rgba"));
            let (w, h) = match prepare_reference(&frame.to_string_lossy(), 1920, 1080, &raw) {
                Ok(v) => v,
                Err(e) => {
                    eprintln!("参考帧不可用: {e}");
                    cleanup(&phone);
                    bail!(2);
                }
            };
            let remote = format!("{REMOTE_DIR}/reference_{i:02}.rgba");
            if !phone.push_file(&raw.to_string_lossy(), &remote) {
                eprintln!("推送参考帧 {} 失败", frame.display());
                cleanup(&phone);
                bail!(2);
            }
            progress(&format!(
                "画质真值 {}/{}: {} ({w}x{h} 中心裁剪到 1920x1080)",
                i + 1,
                frames.len(),
                frame.display()
            ));
            names.push(
                frame
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_else(|| frame.display().to_string()),
            );
            remote_references.push(remote);
        }
        quality_reference = QualityReference {
            kind: "real_frames".into(),
            source: Some(src.clone()),
            frames: names.len() as u32,
            frame_files: names,
            candidate_psnr_db: Vec::new(),
            baseline_psnr_db: Vec::new(),
        };
    } else {
        progress(
            "画质真值: runner 内置程序化图案 (非真实游戏帧; 绝对 PSNR 不可与换了参考图之后的数字比较)",
        );
    }
    // headless 主测循环内联量画质, 只用帧集的第一张: 那条循环跑的是 A/B/A/B,
    // 每张参考帧都跑一遍等于把整轮真机时间乘以帧数, 而该载体的主指标是时延,
    // 不是画质。帧集平均只在画质补测里做 —— 那一步本来就只跑两次。
    let inline_reference = remote_references.first().cloned();

    // ---- A/B/A/B 交替跑测 ----
    let schedule = ab_schedule(req.protocol.rounds, req.protocol.alternating);
    let mut lat_a: Vec<f64> = Vec::new();
    let mut lat_b: Vec<f64> = Vec::new();
    let mut pwr_a: Vec<f64> = Vec::new();
    let mut pwr_b: Vec<f64> = Vec::new();
    let mut fps_a: Vec<f64> = Vec::new();   // 每轮的帧时 p95 (ms)
    let mut fps_b: Vec<f64> = Vec::new();
    let mut busy_a: Vec<f64> = Vec::new();  // 每轮的每帧 GPU 忙时均值 (ms)
    let mut busy_b: Vec<f64> = Vec::new();
    let mut psnr_a: Vec<f64> = Vec::new();
    let mut ghost_a: Vec<f64> = Vec::new();
    let mut ghost_b: Vec<f64> = Vec::new();
    let mut stretch_a: Vec<f64> = Vec::new();
    let mut stretch_b: Vec<f64> = Vec::new();
    let mut psnr_b: Vec<f64> = Vec::new();
    let mut observations: Vec<Value> = Vec::new();
    let mut crashed = false;
    let mut fatal: Option<String> = None;

    for (i, arm) in schedule.iter().enumerate() {
        progress(&format!(
            "第 {}/{} 轮 [{}] 等冷 < {:.1}C ...",
            i + 1,
            schedule.len(),
            arm.as_str(),
            req.protocol.cool_c
        ));

        // 每轮都重新等冷: 上一轮把机器跑热了, 不等就把热漂移算进算子差异。
        let (zone, start_c, waited_s) = match hwcond::wait_cool(
            "gpu-op", &phone, req.protocol.cool_c as f64, a.cool_timeout_s,
        ) {
            Ok(v) => v,
            Err(e) => { fatal = Some(format!("等冷失败: {e}")); break; }
        };
        let battery_before = hwcond::read_battery_state(&phone);
        if a.require_discharging && !battery_before.on_battery() {
            fatal = Some("required discharge not verified before arm".into()); break;
        }

        // 锁频只包住跑测那几秒 (bench 的实测教训: 先锁再等冷永远等不下来)
        let snap = hwcond::read_snapshot(&phone);
        let level = a
            .gpu_level
            .or(snap.gpu_max_level)
            .unwrap_or(0);
        let lock = match hwcond::Lock::apply(&phone, &state_path, snap.clone(), level) {
            Ok(l) => l,
            Err(e) => {
                fatal = Some(format!("锁频失败: {e}"));
                break;
            }
        };

        let locked = hwcond::read_snapshot(&phone);
        if req.v2_context.is_some() && !hwcond::gpu_locked_at(&locked, level) {
            let restored = lock.restore(&phone);
            fatal = Some(format!("frequency control actual readback unavailable/mismatch; restored={restored}")); break;
        }
        observations.push(serde_json::json!({"arm":arm.as_str(),"index":i,
            "start_temp_c":start_c,"thermal_zone":zone,"waited_s":waited_s,
            "battery_before":battery_evidence(&battery_before),"battery_after":null,
            "frequency_control_readback":{"gpu_max_level":locked.gpu_max_level,
                "gpu_min_level":locked.gpu_min_level,"gpuclk_hz":locked.gpuclk_hz,
                "backend":hwcond::gpu_control_backend(&locked),"ged_user_limits":locked.mtk_ged,
                "mtk_stackclk_hz":locked.mtk_stackclk_hz},
            "restored":null,"frames_submitted":null,"postfx_active":null}));

        // ── refbench 载体 ──
        if a.carrier == Carrier::Refbench {
            let (res, power) = run_refbench_arm(&phone, *arm, &a, a.power_rail, locked.mtk_ged.is_some(), i, power_unavailable.is_none());
            let restored = lock.restore(&phone);
            let battery_after = hwcond::read_battery_state(&phone);
            let last = observations.last_mut().unwrap();
            last["restored"] = serde_json::json!(restored);
            last["battery_after"] = battery_evidence(&battery_after);
            last["frames_submitted"] = serde_json::json!(res.frames_submitted);
            last["postfx_active"] = serde_json::json!(res.postfx_active);
            last["timing_source"] = serde_json::json!(res.timing_source);
            last["refbench_config_readback"] = serde_json::json!({"frames":res.frames_config,
                "intensity":res.intensity,"scene":res.scene,"run_id":res.run_id,
                "capture_mode":res.capture_mode,"performance_eligible":res.performance_eligible});
            if a.require_discharging && !battery_after.on_battery() {
                fatal = Some("required discharge not verified after arm".into()); break;
            }
            if !restored {
                fatal = Some("锁频态回读与锁前不一致, 设备状态未能 100% 还原".into());
                break;
            }
            if let Err(e) = verify_refbench_performance_scope(&res) {
                fatal = Some(e); break;
            }
            if req.v2_context.is_some() {
                if let Err(e) = verify_refbench_conditions(&res, *arm, a.frames, a.intensity) {
                    fatal = Some(e); break;
                }
            }
            if let Err(e) = verify_refbench_timing(&res, locked.mtk_ged.is_some()) {
                fatal = Some(e); break;
            }
            if let Some(e) = res.error {
                if *arm == Arm::Baseline {
                    fatal = Some(format!("基线臂失败: {e}"));
                    break;
                }
                progress(&format!("  [{}] 候选臂失败: {e}", arm.as_str()));
                crashed = true;
                continue;
            }
            if !res.clean_exit || res.intervals_ms.len() < 8 {
                fatal = Some(format!(
                    "本轮样本不可用: clean_exit={} 帧间隔样本 {} 个",
                    res.clean_exit,
                    res.intervals_ms.len()
                ));
                break;
            }

            let p95 = gpustat::percentile(&res.intervals_ms, 0.95);
            let busy = if res.gpu_busy_ms.is_empty() {
                f64::NAN
            } else {
                gpustat::mean(&res.gpu_busy_ms)
            };
            let w = if power_unavailable.is_none() { hwcond::power_stats(&power).map(|(mean, _, _, _)| mean) } else { None };
            match arm {
                Arm::Baseline => {
                    fps_a.push(p95);
                    if busy.is_finite() { busy_a.push(busy); }
                    if let Some(w) = w { pwr_a.push(w); }
                }
                Arm::Candidate => {
                    fps_b.push(p95);
                    if busy.is_finite() { busy_b.push(busy); }
                    if let Some(w) = w { pwr_b.push(w); }
                }
            }
            progress(&format!(
                "  [{}] 帧时 p95 {:.3} ms | GPU 忙 {:.3} ms/帧 | {} 帧{}",
                arm.as_str(),
                p95,
                busy,
                res.frames_submitted.unwrap_or(0),
                w.map(|w| format!(" | {w:.2} W")).unwrap_or_default()
            ));
            continue;
        }

        let (out, power) = run_one(&phone, *arm, &req, a.power_rail, inline_reference.as_deref(), power_unavailable.is_none());
        let restored = lock.restore(&phone);
        let battery_after = hwcond::read_battery_state(&phone);
        let last = observations.last_mut().unwrap();
        last["restored"] = serde_json::json!(restored);
        last["battery_after"] = battery_evidence(&battery_after);
        if a.require_discharging && !battery_after.on_battery() {
            fatal = Some("required discharge not verified after arm".into()); break;
        }
        if !restored {
            fatal = Some("锁频态回读与锁前不一致, 设备状态未能 100% 还原".into());
            break;
        }

        match parse_completed_runner_output(&out) {
            Ok(r) if r.ok => {
                let Some(ms) = r.median_ms() else {
                    fatal = Some("runner 未给出时延".into());
                    break;
                };
                // Completion was checked before accepting any emitted metrics.
                last["runner_exit_status"] = serde_json::json!(0);
                // Keep the per-arm measurements, not only rounded progress text.
                last["operator_latency_ms"] = serde_json::json!(ms);
                last["psnr_db"] = serde_json::json!(r.psnr_db);
                let w = if power_unavailable.is_none() { hwcond::power_stats(&power).map(|(mean, _, _, _)| mean) } else { None };
                match arm {
                    Arm::Baseline => {
                        lat_a.push(ms);
                        if let Some(w) = w { pwr_a.push(w); }
                        // 基线臂的画质也要收: 它是**同一张参考图上**的画质地板。
                        // 绝对 dB 不可跨参考图比较 (实测: 同一算子在程序化图上
                        // 42.83 dB, 换成真机截帧只有 31.33 dB), 所以地板必须
                        // 来自本次同场测出来的基线, 不能用别处搬来的常数。
                        if let Some(q) = r.psnr_db { psnr_a.push(q); }
                        if let Some(v) = r.hud_ghost_db { ghost_a.push(v); }
                        if let Some(v) = r.stretch_pct { stretch_a.push(v); }
                    }
                    Arm::Candidate => {
                        lat_b.push(ms);
                        if let Some(w) = w { pwr_b.push(w); }
                        if let Some(q) = r.psnr_db { psnr_b.push(q); }
                        if let Some(v) = r.hud_ghost_db { ghost_b.push(v); }
                        if let Some(v) = r.stretch_pct { stretch_b.push(v); }
                    }
                }
                progress(&format!(
                    "  [{}] {:.3} ms{}",
                    arm.as_str(),
                    ms,
                    w.map(|w| format!(" | {w:.2} W")).unwrap_or_default()
                ));
            }
            Ok(r) => {
                // 候选把 GPU 跑崩了是一个结论, 不是一个错误。
                progress(&format!("  [{}] runner 失败: {:?}", arm.as_str(), r.error));
                if *arm == Arm::Candidate {
                    crashed = true;
                } else {
                    fatal = Some(format!("基线臂都跑不起来: {:?}", r.error));
                    break;
                }
            }
            Err(e) => {
                fatal = Some(format!("runner 回包不可解析: {e}"));
                break;
            }
        }
    }

    // ---- 画质补测 (refbench 载体) ----
    //
    // 放在 A/B 之后、cleanup 之前: 主测期间设备要么在等冷要么在锁频跑分,
    // 插进去会把热状态搅乱; 跑完了再补则完全不影响已经落袋的帧时与瓦数。
    if quality_pass && fatal.is_none() {
        match run_quality_pass(&phone, &runner, &req, &remote_references) {
            Ok((qb, qc)) => {
                if let Some(v) = qb.psnr_db { psnr_a.push(v); }
                if let Some(v) = qb.hud_ghost_db { ghost_a.push(v); }
                if let Some(v) = qb.stretch_pct { stretch_a.push(v); }
                if let Some(v) = qc.psnr_db { psnr_b.push(v); }
                if let Some(v) = qc.hud_ghost_db { ghost_b.push(v); }
                if let Some(v) = qc.stretch_pct { stretch_b.push(v); }
                quality_reference.baseline_psnr_db = qb.psnr_per_frame.clone();
                quality_reference.candidate_psnr_db = qc.psnr_per_frame.clone();
                progress(&format!(
                    "画质补测 ({} 张参考帧取均值): 基线 {} | 候选 {}",
                    quality_reference.frames.max(1),
                    qb.psnr_db.map(|v| format!("{v:.3} dB")).unwrap_or_else(|| "未测到".into()),
                    qc.psnr_db.map(|v| format!("{v:.3} dB")).unwrap_or_else(|| "未测到".into()),
                ));
            }
            // 补测失败不作废主测: 帧时与瓦数是真跑出来的, 不该被画质这一步连坐。
            // 但画质就如实留空 —— 绝不拿基线值或默认值顶上。
            Err(e) => progress(&format!("画质补测失败, 该项如实留空: {e}")),
        }
    }

    cleanup(&phone);

    if let Some(e) = fatal {
        eprintln!("{e}");
        bail!(2);
    }

    // ---- 统计裁决 ----
    //
    // 两个载体的主指标不是同一个量, 检验的对象也就不同:
    //   headless: lat_* 是每轮的 dispatch 中位耗时 —— 直接就是算子耗时;
    //   refbench: 算子挂在整条管线上, 分不出"只属于它"的那一段。
    //             主指标改成**帧时 p95**, 算子的代价用 A/B 的每帧 GPU 忙时之差表达。
    let (sample_a, sample_b) = if a.carrier == Carrier::Refbench {
        (fps_a.clone(), fps_b.clone())
    } else {
        (lat_a.clone(), lat_b.clone())
    };
    let welch = gpustat::welch_t_test(&sample_a, &sample_b);
    let rules = FrozenRules::from_request(&req);

    // refbench 载体下的"算子耗时" = 每帧 GPU 忙时的 A/B 之差, 即算子的**边际**开销。
    // 直接拿 B 臂的整帧 GPU 忙时当算子耗时是错的 —— 那里面绝大部分是场景 pass。
    let cand_lat = if a.carrier == Carrier::Refbench {
        if busy_a.is_empty() || busy_b.is_empty() {
            f32::NAN
        } else {
            (gpustat::mean(&busy_b) - gpustat::mean(&busy_a)) as f32
        }
    } else if lat_b.is_empty() {
        f32::NAN
    } else {
        gpustat::mean(&lat_b) as f32
    };
    let base = if (a.carrier == Carrier::Refbench && fps_a.is_empty())
        || (a.carrier == Carrier::Headless && lat_a.is_empty())
    {
        None
    } else {
        Some(Metrics {
            operator_latency_ms: if a.carrier == Carrier::Refbench {
                0.0  // 基线臂按定义不含算子
            } else {
                gpustat::mean(&lat_a) as f32
            },
            fps_p95_ms: if fps_a.is_empty() {
                None
            } else {
                Some(gpustat::mean(&fps_a) as f32)
            },
            power_watt: gpustat::mean(&pwr_a) as f32,
            psnr_db: if psnr_a.is_empty() { f32::NAN } else { gpustat::mean(&psnr_a) as f32 },
        })
    };
    let candidate = Metrics {
        operator_latency_ms: cand_lat,
        // headless 算子标尺给不出帧时 p95 (没有渲染上下文), 留 None 不填 0;
        // refbench 载体下这里是真实测量值。
        fps_p95_ms: if fps_b.is_empty() {
            None
        } else {
            Some(gpustat::mean(&fps_b) as f32)
        },
        power_watt: if pwr_b.is_empty() { f32::NAN } else { gpustat::mean(&pwr_b) as f32 },
        psnr_db: if psnr_b.is_empty() { f32::NAN } else { gpustat::mean(&psnr_b) as f32 },
    };

    // 画质地板: 优先用本次同场测出来的基线臂画质。
    // 契约里带来的 quality_baseline_db 是在别的参考图上得到的, 只作兜底。
    let mut rules = rules;
    if let Some(b) = base.as_ref() {
        if b.psnr_db.is_finite() {
            progress(&format!(
                "画质地板改用本场基线实测 {:.3} dB (契约携带的 {:.3} dB 来自另一张参考图, 仅作兜底)",
                b.psnr_db, rules.quality_baseline_db
            ));
            rules.quality_baseline_db = b.psnr_db;
        }
    }

    // 插帧伪影裁决。PSNR 是全图平均, 对这两类伪影极不敏感 ——
    // HUD 只占几个百分点面积, 拉扯只发生在运动边界; 全图 PSNR 看着还行,
    // 人眼已经无法忍受。所以单独判, 并且单独一票否决。
    let mean_opt = |v: &Vec<f64>| if v.is_empty() { None } else { Some(gpustat::mean(v)) };
    let artifacts = judge_artifacts(
        &ArtifactGates::default(),
        mean_opt(&ghost_b),
        mean_opt(&ghost_a),
        mean_opt(&stretch_b),
        mean_opt(&stretch_a),
    );
    if let Some(why) = artifacts.reason.as_ref() {
        progress(&format!("伪影门禁不通过 -> POOR_QUALITY: {why}"));
    }

    let (mut status, verdict) = decide(&rules, &candidate, base.as_ref(), welch.as_ref(), crashed);
    // 伪影退化按画质不合格处理。不新增 status 取值: 契约枚举是两仓库共用的,
    // 单方面加一个值会让上游解析不了。
    if artifacts.regressed && status == Status::Pass {
        status = Status::PoorQuality;
    }

    let report = EvalReport {
        candidate_id: req.candidate_id.clone(),
        status,
        metrics: candidate,
        verdict,
        source: "phonefarm".into(),
        artifacts: if artifacts.hud_ghost_db.is_some() || artifacts.stretch_pct.is_some() {
            Some(artifacts)
        } else {
            None
        },
        // 一个 dB 都没量到就别报真值出处: 参考帧摆在那儿没被用过
        // (画质补测关了 / 跑崩了), 报出来会让人以为那些 null 是对着它得出的。
        quality_reference: if psnr_a.is_empty() && psnr_b.is_empty() {
            None
        } else {
            Some(quality_reference)
        },
        power_source: Some(power_source),
    };

    // 打印之前先恢复充电: 打印失败也不能把手机丢在不充电的状态里
    if let Some(mut g) = suspend.take() {
        if !g.restore(&phone) {
            eprintln!("恢复充电后回读不符 ({}); 本轮 ABORT", g.node());
            return 2;
        }
    }


    if a.as_json {
        let mut output = serde_json::to_value(&report).unwrap_or(Value::Null);
        output["baseline_metrics"] = serde_json::json!(base);
        output["power_unavailable"] = serde_json::json!(power_unavailable);
        output["allow_missing_power"] = serde_json::json!(a.allow_missing_power);
        if let Some(context) = &req.v2_context {
            output["version"] = serde_json::json!(2);
            output["kind"] = serde_json::json!("shader");
            output["conditions_requested"] = context["conditions_requested"].clone();
            output["workload_requested"] = context["workload_requested"].clone();
            output["injection_requested"] = context["injection_requested"].clone();
            let mut resolved = context["resolved_config"].clone();
            resolved["serial"] = serde_json::json!(a.serial);
            resolved["carrier"] = serde_json::json!(a.carrier.as_str());
            resolved["frames"] = if a.carrier == Carrier::Refbench { serde_json::json!(a.frames) } else { Value::Null };
            resolved["intensity"] = if a.carrier == Carrier::Refbench { serde_json::json!(a.intensity) } else { Value::Null };
            resolved["seconds"] = if a.carrier == Carrier::Headless { serde_json::json!(req.protocol.replay_seconds) } else { Value::Null };
            resolved["require_discharging"] = serde_json::json!(a.require_discharging);
            resolved["charging_suspend"]["enabled"] = serde_json::json!(a.suspend_charging);
            resolved["power_rail"] = serde_json::json!(a.power_rail.as_str());
            resolved["allow_missing_power"] = serde_json::json!(a.allow_missing_power);
            resolved["gpu_level"] = serde_json::json!(a.gpu_level);
            resolved["quality_reference"] = serde_json::json!(ref_source);
            output["resolved_config"] = resolved;
            output["measurement_scope"] = serde_json::json!(if a.carrier==Carrier::Headless {"operator_benchmark_only"} else {"refbench_measurement_only"});
            output["qualification"] = crate::shaderplan::qualification_scope(Some(a.carrier.as_str()));
            output["conditions_observed"] = serde_json::json!({"root_verified":true,"fan":null,
                "arms":observations,"restored":true,"charging_suspended_via":report.power_source.as_ref().and_then(|p| p.charging_suspended_via.as_deref()),
                "power_probe":report.power_source,"power_window_verified":false});
            output["metric_provenance"] = serde_json::json!({"fps_p95_ms":{
                "source":if a.carrier==Carrier::Refbench {
                    observations.first().and_then(|v|v["timing_source"].as_str()).unwrap_or("raw_ftrace_kgsl_submit_proxy")
                } else {"unavailable"},
                "is_display_timing":false},"operator_latency_ms":{
                    "source":if a.carrier==Carrier::Headless {"vulkan_dispatch_timestamp"}
                        else if observations.first().and_then(|v|v["timing_source"].as_str())==Some(crate::refbench_timing::SOURCE) {
                            "refbench_gpu_queue_elapsed_ab_delta"
                        } else {"kgsl_gpu_busy_ab_delta"},
                    "is_isolated_operator":a.carrier==Carrier::Headless}});
        }
        println!("{}", serde_json::to_string_pretty(&output).unwrap_or_default());
    } else {
        println!("{}", render_text(&report, base.as_ref(), welch.as_ref()));
    }

    match status {
        Status::Pass => 0,
        _ => 1,
    }
}

fn optional_power_result(result: Result<(), String>, allow_missing: bool) -> Result<Option<String>, String> {
    match result {
        Ok(()) => Ok(None),
        Err(reason) if allow_missing => Ok(Some(reason)),
        Err(reason) => Err(reason),
    }
}

/// 一次画质测量最多用多少张参考帧。
///
/// 每张都要转成 1920x1080 裸 RGBA (8.3 MB) 推上设备, 再让两个臂各跑一遍 ——
/// 帧数直接乘在推送量与 runner 调用次数上。上限只是个安全阀:
/// 画质是确定性量, 十几张不同内容的真实帧已经足够把"这个算子在真实高频细节上
/// 表现如何"这件事量稳, 再多是线性烧真机时间。
const MAX_REFERENCE_FRAMES: usize = 32;

/// 解析 `--reference` / `quality_reference` 指向的东西, 得到一组参考帧。
///
/// 允许两种写法:
///   - 一张图片 → 单帧帧集 (旧行为, 原样兼容);
///   - 一个目录 → 目录里的图片**全部**参与, 按文件名排序 (排序而非目录序:
///     目录序随文件系统变, 同一组帧换台机器跑就不是同一个顺序了, 逐帧 PSNR
///     也就对不上号)。
///
/// 为什么要支持一组而不是一张: 单张真机截帧的 PSNR 强烈依赖那一帧拍到了什么 ——
/// 对着天空拍的一帧几乎没有高频细节, 任何算子都能拿高分。一组覆盖不同内容的帧
/// 取平均, 量的才是算子本身。
pub fn resolve_reference_frames(path: &str) -> Result<Vec<std::path::PathBuf>, String> {
    let p = std::path::Path::new(path);
    if p.is_file() {
        return Ok(vec![p.to_path_buf()]);
    }
    if !p.is_dir() {
        return Err(format!("参考帧来源 {path} 既不是文件也不是目录"));
    }
    let is_image = |p: &std::path::Path| {
        matches!(
            p.extension()
                .and_then(|e| e.to_str())
                .map(|e| e.to_ascii_lowercase())
                .as_deref(),
            Some("png" | "jpg" | "jpeg")
        )
    };
    let mut frames: Vec<std::path::PathBuf> = std::fs::read_dir(p)
        .map_err(|e| format!("读不了参考帧目录 {path}: {e}"))?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.is_file() && is_image(p))
        .collect();
    if frames.is_empty() {
        return Err(format!("参考帧目录 {path} 里没有 png/jpg 图片"));
    }
    frames.sort();
    if frames.len() > MAX_REFERENCE_FRAMES {
        let n = frames.len();
        progress(&format!(
            "参考帧目录有 {n} 张, 超过上限 {MAX_REFERENCE_FRAMES}, 按等间隔抽取"
        ));
        frames = even_subset(frames, MAX_REFERENCE_FRAMES);
    }
    Ok(frames)
}

/// 从有序帧列表里等间隔抽 `k` 张 (含首尾)。
///
/// 为什么不是直接取前 k 张: `capture` 的输出是按时间顺序连续编号的
/// (`frame_00000.png`, `frame_00001.png`, ...), 取前 32 张等于把整组帧
/// 压缩到巡航开头那一小段 —— 很可能全是同一个视角同一片场景。
/// 那正好废掉了用帧集的理由: 要的是覆盖不同内容, 不是覆盖同一处的 32 个瞬间。
pub fn even_subset<T>(items: Vec<T>, k: usize) -> Vec<T> {
    let n = items.len();
    if k == 0 {
        return Vec::new();
    }
    if n <= k {
        return items;
    }
    if k == 1 {
        return items.into_iter().take(1).collect();
    }
    // i 从 0 到 k-1 均匀映射到 0..=n-1, 四舍五入。首尾一定取到。
    let keep: std::collections::BTreeSet<usize> = (0..k)
        .map(|i| (i * (n - 1) + (k - 1) / 2) / (k - 1))
        .collect();
    items
        .into_iter()
        .enumerate()
        .filter(|(i, _)| keep.contains(i))
        .map(|(_, v)| v)
        .collect()
}

/// 把参考帧转成 runner 吃的裸 RGBA8, 并推到设备。
///
/// 为什么**中心裁剪**而不是缩放: 参考帧是画质真值。把一张 2688x1216 的真机截帧
/// 缩到 1920x1080, 缩放本身就引入了一次重采样 —— 算子再去"重建"这张已经被
/// 重采样过的图, 量出来的 PSNR 里混进了缩放器的特性, 不再只是算子的画质。
/// 中心裁剪保留原生像素, 真值才是真值。
///
/// 源图小于目标时直接报错, 不做放大: 放大出来的"真值"是假的。
fn prepare_reference(
    local_png: &str,
    out_w: u32,
    out_h: u32,
    dst: &std::path::Path,
) -> Result<(u32, u32), String> {
    let img = image::open(local_png).map_err(|e| format!("解不开参考帧 {local_png}: {e}"))?;
    let (w, h) = (img.width(), img.height());
    if w < out_w || h < out_h {
        return Err(format!(
            "参考帧 {w}x{h} 小于目标 {out_w}x{out_h}: 放大出来的真值是假的, 拒绝使用"
        ));
    }
    let x = (w - out_w) / 2;
    let y = (h - out_h) / 2;
    let rgba = image::imageops::crop_imm(&img.to_rgba8(), x, y, out_w, out_h)
        .to_image()
        .into_raw();
    std::fs::write(dst, &rgba).map_err(|e| format!("写入 {} 失败: {e}", dst.display()))?;
    Ok((w, h))
}

/// 把 runner 与两份 SPIR-V 推到设备。
fn deploy(
    phone: &crate::device::Device,
    runner_local: &str,
    req: &EvalRequest,
) -> Result<(), String> {
    phone.shell(&format!("mkdir -p {REMOTE_DIR}"), 10_000);
    let remote_runner = format!("{REMOTE_DIR}/vkop_runner");
    if !phone.push_file(runner_local, &remote_runner) {
        return Err(format!("推送 {runner_local} 到 {remote_runner} 失败"));
    }
    phone.shell(&format!("chmod 755 {remote_runner}"), 10_000);

    let remote_cand = format!("{REMOTE_DIR}/candidate.spv");
    if !phone.push_file(&req.spirv_path, &remote_cand) {
        return Err(format!("推送候选 SPIR-V 到 {remote_cand} 失败"));
    }
    // 基线取赛道模板编译产物; 上游把它与候选放在同一目录下。
    let baseline = std::path::Path::new(&req.spirv_path)
        .parent()
        .map(|d| d.join("baseline.spv"))
        .ok_or("推不出基线 SPIR-V 路径")?;
    if !baseline.is_file() {
        return Err(format!(
            "找不到基线 SPIR-V {} —— A/B 对照必须有 A 臂",
            baseline.display()
        ));
    }
    let remote_base = format!("{REMOTE_DIR}/baseline.spv");
    if !phone.push_file(&baseline.to_string_lossy(), &remote_base) {
        return Err(format!("推送基线 SPIR-V 到 {remote_base} 失败"));
    }
    Ok(())
}

/// 画质补测的一臂产出。时延一概不收 —— 这一步的数字没有统计意义,
/// 混进主测样本会污染 Welch 检验。
#[derive(Debug, Clone, Default, PartialEq)]
pub struct QualitySample {
    /// 帧集上的均值 (单帧时即那一帧)。
    pub psnr_db: Option<f64>,
    pub hud_ghost_db: Option<f64>,
    pub stretch_pct: Option<f64>,
    /// 逐帧 PSNR, 与参考帧**逐位对齐** (量不到的那张记 `NaN`, 序列化成 null)。
    ///
    /// 均值塌掉的信息在这里留着 —— 一组帧里某一张特别差, 均值看不出来。
    /// 对齐必须靠留空位保持: 把量不到的那张直接删掉的话, 下标就跟
    /// `frame_files` 错开了, 报告里"第 3 张最差"会指到另一张图上。
    pub psnr_per_frame: Vec<f64>,
}

/// 把若干帧的逐帧读数合成一个样本。
///
/// PSNR 取**逐帧 dB 的算术平均**, 不是先平均 MSE 再转 dB: 前者是视频画质评测的
/// 通行口径 (每帧一个分数再平均), 后者会让一张特别糟的帧几乎吃掉整组分数。
/// 这里要回答的是"这个算子在一组真实画面上平均表现如何", 用前者。
///
/// 任何一项在所有帧上都没量到, 就如实留 `None` —— 不拿 0 顶上。
pub fn fold_quality_frames(per_frame: &[QualitySample]) -> QualitySample {
    let mean_of = |pick: fn(&QualitySample) -> Option<f64>| -> Option<f64> {
        let v: Vec<f64> = per_frame.iter().filter_map(pick).collect();
        if v.is_empty() {
            None
        } else {
            Some(gpustat::mean(&v))
        }
    };
    QualitySample {
        psnr_db: mean_of(|s| s.psnr_db),
        hud_ghost_db: mean_of(|s| s.hud_ghost_db),
        stretch_pct: mean_of(|s| s.stretch_pct),
        // NaN 占位而不是删掉: 下标必须跟 frame_files 对得上。
        psnr_per_frame: per_frame
            .iter()
            .map(|s| s.psnr_db.unwrap_or(f64::NAN))
            .collect(),
    }
}

/// 补测迭代次数。画质是确定性的, 1 次就够;
/// 取 3 次是为了让 runner 自己的 warmup/资源初始化走完再出数。
const QUALITY_ITERS: u32 = 3;

/// refbench 载体下的画质补测: headless runner 对**每一张**参考帧各跑一次基线与候选。
///
/// 两臂必须用**同一组**参考帧, 否则 dB 不可比 (实测: 同一算子在程序化图案上
/// 42.83 dB, 换成真机截帧只剩 31.33 dB)。上游的画质地板也因此取本次基线臂的
/// 实测值, 而不是契约里搬来的常数。
///
/// `references` 为空 = 没有真实帧, 退回 runner 内置的程序化图案跑一次。
/// 那是退路不是缺省选择: 合成图案的高频细节远少于真实游戏画面, 绝对 dB 偏高。
fn run_quality_pass(
    phone: &crate::device::Device,
    runner_local: &str,
    req: &EvalRequest,
    references: &[String],
) -> Result<(QualitySample, QualitySample), String> {
    deploy(phone, runner_local, req)?;
    // refbench 主测会把 REMOTE_SPV 下的候选换掉, 但 deploy 推的是 REMOTE_DIR
    // 下另一份, 两者互不影响。
    let one = |spv: &str, reference: Option<&String>| -> Result<QualitySample, String> {
        let ref_arg = reference
            .map(|r| format!(" --reference {r}"))
            .unwrap_or_default();
        let cmd = format!(
            "cd {REMOTE_DIR} && ./vkop_runner --shader {spv} --track {} --iterations {QUALITY_ITERS}{ref_arg} --json 2>&1",
            req.track
        );
        let out = phone.shell(&completed_runner_command(&cmd), 120_000);
        let r = parse_completed_runner_output(&out)?;
        if !r.ok {
            return Err(format!("{spv} 跑失败: {:?}", r.error));
        }
        Ok(QualitySample {
            psnr_db: r.psnr_db,
            hud_ghost_db: r.hud_ghost_db,
            stretch_pct: r.stretch_pct,
            psnr_per_frame: r.psnr_db.into_iter().collect(),
        })
    };

    // 没有真实帧时 `references` 为空, 这里退化成"跑一次, 不带 --reference"。
    let slots: Vec<Option<&String>> = if references.is_empty() {
        vec![None]
    } else {
        references.iter().map(Some).collect()
    };

    // 一张跑挂不作废整臂: 16 张里第 9 张碰上一次 adb 抖动, 不该把另外 15 张
    // 真跑出来的 dB 一起扔掉 —— 那跟"补测失败不作废主测"是同一条道理。
    // 挂掉的那张留 NaN 占位 (下标仍与 frame_files 对齐), 全挂才算这臂失败。
    let arm = |spv: &str| -> Result<QualitySample, String> {
        let mut per_frame = Vec::with_capacity(slots.len());
        let mut failures: Vec<String> = Vec::new();
        for (i, r) in slots.iter().enumerate() {
            match one(spv, *r) {
                Ok(s) => per_frame.push(s),
                Err(e) => {
                    // 报错带上帧序号: 一组帧里坏了哪一张, 日志里要看得出来。
                    failures.push(format!("第 {} 张: {e}", i + 1));
                    per_frame.push(QualitySample::default());
                }
            }
        }
        if failures.len() == slots.len() {
            return Err(failures.join("; "));
        }
        if !failures.is_empty() {
            progress(&format!(
                "{spv} 画质补测有 {}/{} 张没跑出来 (该几张留空, 不参与均值): {}",
                failures.len(),
                slots.len(),
                failures.join("; ")
            ));
        }
        Ok(fold_quality_frames(&per_frame))
    };

    // 基线先跑: 它跑不起来说明工装坏了, 候选的数字也就没有参照。
    let base = arm("baseline.spv").map_err(|e| format!("基线臂 {e}"))?;
    let cand = arm("candidate.spv").map_err(|e| format!("候选臂 {e}"))?;
    Ok(align_arms(base, cand))
}

/// 两臂的均值必须来自**同一批**帧。
///
/// 一臂在第 9 张挂了而另一臂没挂时, 两个均值就落在不同的帧子集上 —— 那时候
/// "候选比基线高 2 dB" 可能只是因为候选少算了一张高频最狠的帧。画质地板取的
/// 就是基线臂均值, 这个偏差会直接变成误判。
///
/// 所以只保留两臂都量到的那些帧参与均值; 被剔掉的位置照旧留 NaN 占位,
/// 逐帧数组仍与 `frame_files` 对齐。
pub fn align_arms(base: QualitySample, cand: QualitySample) -> (QualitySample, QualitySample) {
    let n = base.psnr_per_frame.len().min(cand.psnr_per_frame.len());
    if n == 0 {
        return (base, cand);
    }
    let both = |v: &[f64], i: usize| v.get(i).copied().unwrap_or(f64::NAN);
    let keep: Vec<bool> = (0..n)
        .map(|i| both(&base.psnr_per_frame, i).is_finite() && both(&cand.psnr_per_frame, i).is_finite())
        .collect();
    if keep.iter().all(|k| *k) {
        return (base, cand);
    }
    let mask = |mut s: QualitySample| -> QualitySample {
        s.psnr_per_frame = (0..n)
            .map(|i| {
                if keep[i] {
                    both(&s.psnr_per_frame, i)
                } else {
                    f64::NAN
                }
            })
            .collect();
        let kept: Vec<f64> = s.psnr_per_frame.iter().copied().filter(|v| v.is_finite()).collect();
        s.psnr_db = if kept.is_empty() {
            None
        } else {
            Some(gpustat::mean(&kept))
        };
        s
    };
    (mask(base), mask(cand))
}

/// 设备上不留任何残留。
fn cleanup(phone: &crate::device::Device) {
    phone.shell(&format!("rm -rf {REMOTE_DIR}"), 10_000);
}

/// refbench 一臂的产出。
#[derive(Debug, Clone, Default)]
pub struct RefbenchArm {
    pub frames_submitted: Option<u32>,
    pub frames_config: Option<u32>,
    pub intensity: Option<u32>,
    pub scene: Option<String>,
    pub run_id: Option<String>,
    pub expected_run_id: Option<String>,
    pub clean_exit: bool,
    pub postfx_active: Option<bool>,
    /// Raw declarations retain malformed/null values so they cannot become missing.
    pub capture_mode: Option<Value>,
    pub performance_eligible: Option<Value>,
    /// 帧间隔 (ms)
    pub intervals_ms: Vec<f64>,
    /// 每帧 GPU 忙时 (ms)
    pub gpu_busy_ms: Vec<f64>,
    pub timing_source: Option<String>,
    pub error: Option<String>,
}

impl RefbenchArm {
    fn read_report(&mut self, v: &Value) {
        self.frames_submitted = v.get("frames_submitted").and_then(Value::as_u64)
            .and_then(|n| u32::try_from(n).ok());
        self.frames_config = v.pointer("/params/frames").and_then(Value::as_u64)
            .and_then(|n| u32::try_from(n).ok());
        self.intensity = v.pointer("/params/intensity").and_then(Value::as_u64)
            .and_then(|n| u32::try_from(n).ok());
        self.scene = v["scene"].as_str().map(str::to_string);
        self.run_id = v["run_id"].as_str().map(str::to_string);
        self.clean_exit = v.get("clean_exit").and_then(Value::as_bool).unwrap_or(false);
        self.postfx_active = v.pointer("/postfx/active").and_then(Value::as_bool);
        self.capture_mode = v.get("capture_mode").cloned();
        self.performance_eligible = v.get("performance_eligible").cloned();
    }
}

fn verify_refbench_performance_scope(r: &RefbenchArm) -> Result<(), String> {
    if r.capture_mode.is_some() || r.performance_eligible.is_some() {
        return Err("refbench capture declarations are outside the ordinary performance contract; quality capture and unknown/malformed modes cannot supply performance samples".into());
    }
    Ok(())
}

fn verify_refbench_timing(r: &RefbenchArm, native: bool) -> Result<(), String> {
    if native && (r.timing_source.as_deref() != Some(crate::refbench_timing::SOURCE)
        || r.intervals_ms.len() < 8 || r.gpu_busy_ms.is_empty()) {
        return Err(format!("Mali refbench timestamp measurement unavailable: {}",
            r.error.as_deref().unwrap_or("missing/insufficient timestamp samples")));
    }
    Ok(())
}

fn verify_refbench_conditions(r: &RefbenchArm, arm: Arm, expected_frames: u32, expected_intensity: u32) -> Result<(), String> {
    verify_refbench_performance_scope(r)?;
    if r.frames_config != Some(expected_frames) || r.intensity != Some(expected_intensity)
        || r.scene.as_deref() != Some(refbench::SCENE)
        || r.run_id.as_deref() != Some(r.expected_run_id.as_deref().unwrap_or(&format!("gpuop_{}",arm.as_str()))) {
        return Err("refbench actual params/scene/run_id missing or differ from requested execution".into());
    }
    if r.frames_submitted != Some(expected_frames) {
        return Err(format!("refbench actual frames {:?} != requested {expected_frames}",r.frames_submitted));
    }
    let expected = arm == Arm::Candidate;
    if r.postfx_active != Some(expected) {
        return Err(format!("refbench postfx actual {:?} != required {expected}",r.postfx_active));
    }
    Ok(())
}

/// 驱动 refbench 跑一臂: 开 ftrace → 拉起场景 → 采功耗 → 等自退 → 收 trace 与回包。
fn run_refbench_arm(
    phone: &crate::device::Device,
    arm: Arm,
    a: &GpuOpArgs,
    rail: PowerRail,
    native_timing: bool,
    sample_index: usize,
    sample_power: bool,
) -> (RefbenchArm, Vec<hwcond::PowerSample>) {
    let mut out = RefbenchArm::default();
    let run_id = if native_timing { format!("gpuop_{}_{sample_index}", arm.as_str()) }
        else { format!("gpuop_{}", arm.as_str()) };
    out.expected_run_id = Some(run_id.clone());
    let started = format!("{}/refbench_started", refbench::FILES);
    let result = format!("{}/refbench_out.json", refbench::FILES);
    let timestamp_result = format!("{}/refbench_gpu_timestamps.json", refbench::FILES);

    // 上一轮的残留会让"起跑了没"和"跑完了没"都判错
    phone.shell(&format!("am force-stop {}", refbench::PACKAGE), 10_000);
    phone.shell(&format!("rm -f {started} {result} {timestamp_result}"), 10_000);

    let mut session = if native_timing { None } else { match crate::ftrace::TraceSession::begin(phone, 65536) {
        Ok(s) => Some(s),
        Err(e) => {
            out.error = Some(format!("ftrace 打不开: {e}"));
            return (out, Vec::new());
        }
    }};

    let knob = match arm {
        Arm::Baseline => "--es knob.postfx off".to_string(),
        Arm::Candidate => format!(
            "--es knob.postfx on --es postfx.shader {}",
            refbench::REMOTE_SPV
        ),
    };
    let cmd = format!(
        "am start -W -n {} --es scene {} --es run_id {run_id} --es frames {} --es intensity {} {knob} --es capture.gpu_timestamps {}",
        refbench::ACTIVITY,
        refbench::SCENE,
        a.frames,
        a.intensity,
        if native_timing { "on" } else { "off" }
    );
    phone.shell(&cmd, 60_000);

    // 轮询起跑标记。logcat 在本机观测到会完全静默, 所以契约用的是这个文件。
    let mut launched = false;
    for _ in 0..60 {
        if phone
            .shell(&format!("ls {started} 2>/dev/null"), 8_000)
            .contains("refbench_started")
        {
            launched = true;
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
    if !launched {
        out.error = Some("refbench 没有起跑 (未见 refbench_started 标记)".into());
        if let Some(session) = session.as_mut() { let _ = session.stop_and_read(); }
        return (out, Vec::new());
    }

    // 边跑边采功耗
    let ticks = 160u32;
    let inner = hwcond::power_sample_cmd(rail)
        .trim_start_matches("su -c '")
        .trim_end_matches('\'')
        .to_string();
    // Explicit timing-only runs must not start an unusable remote power sampler.
    let sampler = if sample_power {
        phone.stream_shell(&format!(
            "su -c 'i=0; while [ $i -lt {ticks} ]; do {inner}; sleep 0.25; i=$((i+1)); done'"
        )).ok()
    } else { None };

    // 等自退。refbench 跑满 frames 后自己结束, 不需要我们杀它。
    let mut exited = false;
    for _ in 0..240 {
        if phone
            .shell(&format!("pidof {} 2>/dev/null", refbench::PACKAGE), 8_000)
            .trim()
            .is_empty()
        {
            exited = true;
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }

    let mut power = Vec::new();
    if let Some(mut child) = sampler {
        let _ = child.kill();
        if let Ok(o) = child.wait_with_output() {
            power = hwcond::parse_power_samples(&String::from_utf8_lossy(&o.stdout));
        }
    }

    if let Some(session) = session.as_mut() {
        let trace = session.stop_and_read();
        let stats = crate::ftrace::frame_stats(&trace, refbench::SUBMIT_COMM);
        out.intervals_ms = stats.intervals_ms;
        out.gpu_busy_ms = stats.gpu_busy_ms;
        out.timing_source = Some("raw_ftrace_kgsl_submit_proxy".into());
    }

    if !exited {
        phone.shell(&format!("am force-stop {}", refbench::PACKAGE), 10_000);
        out.error = Some("refbench 未在预期内自退".into());
        return (out, power);
    }

    // 回包: 它自己说跑了多少帧、干不干净、算子挂上没有
    let raw = phone.shell(&format!("cat {result} 2>/dev/null"), 20_000);
    match serde_json::from_str::<Value>(raw.trim()) {
        Ok(v) => out.read_report(&v),
        Err(e) => out.error = Some(format!("refbench 回包不可解析: {e}")),
    }
    if native_timing && out.error.is_none() {
        let raw = phone.shell(&format!("cat {timestamp_result} 2>/dev/null"), 20_000);
        if let Some(dir) = a.out_dir.as_deref() {
            let path = std::path::Path::new(dir).join(format!("{run_id}_{sample_index}_gpu_timestamps.json"));
            use std::io::Write;
            if let Err(e) = std::fs::OpenOptions::new().write(true).create_new(true).open(&path)
                .and_then(|mut file| file.write_all(raw.as_bytes())) {
                out.error = Some(format!("cannot save refbench GPU timestamp evidence: {e}"));
            }
        }
        match crate::refbench_timing::parse(raw.trim(), &run_id, a.frames) {
            Ok(samples) => {
                out.intervals_ms = samples.intervals_ms;
                out.gpu_busy_ms = samples.gpu_elapsed_ms;
                out.timing_source = Some(crate::refbench_timing::SOURCE.into());
            }
            Err(e) => { out.error = Some(e); }
        }
    }

    // 算子该挂上却没挂上 = 这一臂在测别的东西, 绝不能当成有效样本。
    if arm == Arm::Candidate && out.postfx_active != Some(true) && out.error.is_none() {
        out.error = Some("候选臂的 postfx 没有生效, 这一臂测的不是候选算子".into());
    }
    if arm == Arm::Baseline && out.postfx_active != Some(false) && out.error.is_none() {
        out.error = Some("基线臂却挂上了算子, A/B 被污染".into());
    }

    (out, power)
}

/// 跑一个臂, 同时在设备侧采功耗。
fn run_one(
    phone: &crate::device::Device,
    arm: Arm,
    req: &EvalRequest,
    rail: PowerRail,
    reference: Option<&str>,
    sample_power: bool,
) -> (String, Vec<hwcond::PowerSample>) {
    let spv = match arm {
        Arm::Baseline => "baseline.spv",
        Arm::Candidate => "candidate.spv",
    };

    // 采样上限按回放时长定; adb 通道断开后远端循环也会自行到头, 不留常驻进程。
    let ticks = (req.protocol.replay_seconds as u64 * 4).max(4);
    let inner = hwcond::power_sample_cmd(rail)
        .trim_start_matches("su -c '")
        .trim_end_matches('\'')
        .to_string();
    let sampler_cmd =
        format!("su -c 'i=0; while [ $i -lt {ticks} ]; do {inner}; sleep 0.25; i=$((i+1)); done'");
    let sampler = if sample_power { phone.stream_shell(&sampler_cmd).ok() } else { None };
    if sample_power { std::thread::sleep(std::time::Duration::from_millis(300)); }

    // 两臂必须用同一张参考帧, 否则 PSNR 不可比。
    let ref_arg = reference
        .map(|r| format!(" --reference {r}"))
        .unwrap_or_default();
    let cmd = format!(
        "cd {REMOTE_DIR} && ./vkop_runner --shader {spv} --track {} --seconds {}{ref_arg} --json 2>&1",
        req.track, req.protocol.replay_seconds
    );
    let out = phone.shell(&completed_runner_command(&cmd), (req.protocol.replay_seconds as u64 + 60) * 1000);

    let mut power = Vec::new();
    if let Some(mut child) = sampler {
        let _ = child.kill();
        if let Ok(o) = child.wait_with_output() {
            power = hwcond::parse_power_samples(&String::from_utf8_lossy(&o.stdout));
        }
    }
    (out, power)
}

fn render_text(
    r: &EvalReport,
    base: Option<&Metrics>,
    welch: Option<&WelchResult>,
) -> String {
    let mut s = String::new();
    s.push_str(&format!("候选 {}: {:?}\n", r.candidate_id, r.status));
    s.push_str(&format!(
        "  算子耗时 {:.3} ms",
        r.metrics.operator_latency_ms
    ));
    if let Some(b) = base {
        s.push_str(&format!(" (基线 {:.3} ms)", b.operator_latency_ms));
    }
    s.push('\n');
    if r.metrics.psnr_db.is_finite() {
        s.push_str(&format!("  画质 {:.3} dB", r.metrics.psnr_db));
        // 绝对 dB 不可跨参考图比较, 所以这个数字旁边必须写清楚真值是什么。
        match r.quality_reference.as_ref() {
            Some(q) if q.kind == "real_frames" => s.push_str(&format!(
                " (真机参考帧 x{}: {})\n",
                q.frames,
                q.source.as_deref().unwrap_or("?")
            )),
            _ => s.push_str(" (runner 内置程序化图案, 非真实游戏帧)\n"),
        }
    }
    if r.metrics.power_watt.is_finite() {
        s.push_str(&format!("  整机功耗 {:.3} W\n", r.metrics.power_watt));
    }
    if let Some(w) = welch {
        s.push_str(&format!(
            "  Welch t 检验: t={:.3} df={:.1} p={:.5} (n={}+{})\n",
            w.t, w.df, w.p_value, w.n_a, w.n_b
        ));
    }
    s.push_str(&format!(
        "  Pareto 改进: {}\n",
        if r.verdict.is_pareto_improvement { "是" } else { "否" }
    ));
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_power_requires_opt_in_and_preserves_the_unavailable_reason() {
        assert!(optional_power_result(Err("battery current unknown".into()), false).is_err());
        assert_eq!(optional_power_result(Err("battery current unknown".into()), true).unwrap(), Some("battery current unknown".into()));
        assert_eq!(optional_power_result(Ok(()), true).unwrap(), None);
        let parse = |args: &[&str]| parse_args(&args.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        assert!(parse(&["--request", "r.json", "--allow-missing-power"]).unwrap().allow_missing_power);
        assert!(parse(&["--request", "r.json", "--allow-missing-power", "--require-discharging"]).is_err());
        assert!(parse(&["--request", "r.json", "--allow-missing-power", "--suspend-charging"]).is_err());
    }

    #[test]
    fn mali_missing_timestamp_instrumentation_is_not_a_candidate_crash() {
        let mut r = RefbenchArm::default();
        assert!(verify_refbench_timing(&r, true).is_err());
        r.timing_source = Some(crate::refbench_timing::SOURCE.into());
        r.intervals_ms = vec![16.0; 8];
        r.gpu_busy_ms = vec![2.0; 9];
        assert!(verify_refbench_timing(&r, true).is_ok());
        r.intervals_ms.clear();
        assert!(verify_refbench_timing(&r, true).is_err());
    }

    #[test]
    fn hardware_inspection_never_accepts_a_measurement_or_restore_request() {
        let parse = |args: &[&str]| parse_args(&args.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        assert!(parse(&["--inspect-hardware", "--serial", "phone", "--json"]).unwrap().inspect_hardware);
        for arg in ["--unlock", "--suspend-charging"] {
            assert!(parse(&["--inspect-hardware", arg]).is_err());
        }
        assert!(parse(&["--inspect-hardware", "--request", "x.json"]).is_err());
        assert!(parse(&["--json"]).is_err());
    }

    fn scratch(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("gpuop_ref_{tag}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn write_png(path: &std::path::Path, w: u32, h: u32) {
        image::RgbImage::from_fn(w, h, |x, y| {
            image::Rgb([(x % 256) as u8, (y % 256) as u8, 128])
        })
        .save(path)
        .unwrap();
    }

    /// 中心裁剪而不是缩放: 缩放会把缩放器的特性混进 PSNR,
    /// 量到的就不只是算子画质了。参考帧必须是原生像素。
    #[test]
    fn a_reference_frame_is_centre_cropped_not_rescaled() {
        let dir = scratch("crop");
        let png = dir.join("src.png");
        write_png(&png, 2688, 1216); // NX809J 横屏真机截帧尺寸
        let raw = dir.join("out.rgba");

        let (w, h) = prepare_reference(&png.to_string_lossy(), 1920, 1080, &raw).unwrap();
        assert_eq!((w, h), (2688, 1216));

        let bytes = std::fs::read(&raw).unwrap();
        assert_eq!(bytes.len(), 1920 * 1080 * 4, "必须是裸 RGBA8");

        let x0 = (2688 - 1920) / 2;
        let y0 = (1216 - 1080) / 2;
        assert_eq!(bytes[0], (x0 % 256) as u8, "不是原生像素, 被重采样过");
        assert_eq!(bytes[1], (y0 % 256) as u8, "不是原生像素, 被重采样过");

        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 源图比目标小就报错 —— 放大出来的「真值」是假的, 不能当画质基准。
    #[test]
    fn a_reference_frame_smaller_than_the_target_is_rejected() {
        let dir = scratch("small");
        let png = dir.join("s.png");
        write_png(&png, 1280, 720);
        let raw = dir.join("out.rgba");
        let err = prepare_reference(&png.to_string_lossy(), 1920, 1080, &raw).unwrap_err();
        assert!(err.contains("假的"), "{err}");
        assert!(!raw.exists(), "失败时不该留下半个文件");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_missing_or_corrupt_reference_is_reported() {
        let dir = scratch("bad");
        assert!(prepare_reference("/nonexistent.png", 8, 8, &dir.join("o")).is_err());
        let junk = dir.join("junk.png");
        std::fs::write(&junk, b"not a png").unwrap();
        assert!(prepare_reference(&junk.to_string_lossy(), 8, 8, &dir.join("o")).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn reference_is_optional_on_the_command_line() {
        let a = parse_args(&["--request".into(), "r.json".into()]).unwrap();
        assert!(a.reference.is_none());
        let b = parse_args(&[
            "--request".into(), "r.json".into(),
            "--reference".into(), "frame.png".into(),
        ]).unwrap();
        assert_eq!(b.reference.as_deref(), Some("frame.png"));
    }

    // ---------- 参考帧集 ----------

    /// 单张图原样当成一帧的帧集 —— 旧调用方不受影响。
    #[test]
    fn a_single_file_reference_is_a_one_frame_set() {
        let dir = scratch("one");
        let png = dir.join("a.png");
        write_png(&png, 2688, 1216);
        let got = resolve_reference_frames(&png.to_string_lossy()).unwrap();
        assert_eq!(got, vec![png]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 目录 = 一整组参考帧, 且**按文件名排序**。
    ///
    /// 排序不是洁癖: 目录序随文件系统走, 同一组帧换台机器跑就换了顺序,
    /// 报告里的逐帧 PSNR 也就对不上是哪一张。
    #[test]
    fn a_directory_reference_takes_every_image_in_name_order() {
        let dir = scratch("dirset");
        for name in ["frame_02.png", "frame_00.png", "frame_01.png"] {
            write_png(&dir.join(name), 1920, 1080);
        }
        // 非图片不参与
        std::fs::write(dir.join("manifest.jsonl"), b"{}").unwrap();
        let got = resolve_reference_frames(&dir.to_string_lossy()).unwrap();
        let names: Vec<String> = got
            .iter()
            .map(|p| p.file_name().unwrap().to_string_lossy().into_owned())
            .collect();
        assert_eq!(names, ["frame_00.png", "frame_01.png", "frame_02.png"]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 空目录 / 不存在的路径都要当场说清楚, 不能悄悄退回程序化图案 ——
    /// 那会让一份"用了真机参考帧"的报告其实量的是合成图案。
    #[test]
    fn an_empty_or_missing_reference_source_is_an_error() {
        let dir = scratch("empty");
        let err = resolve_reference_frames(&dir.to_string_lossy()).unwrap_err();
        assert!(err.contains("没有 png/jpg"), "{err}");
        assert!(resolve_reference_frames("/nonexistent/dir").is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// 超过上限时**等间隔**抽, 不是取前 N 张。
    ///
    /// capture 的输出按时间连续编号, 取前 32 张等于把整组帧压缩到巡航开头
    /// 那一小段 —— 很可能全是同一个视角。那正好废掉了用帧集的理由。
    /// (resolve 只看文件名不解码, 所以这里用 8x8 小图就够, 不必烧时间编 1080p。)
    #[test]
    fn an_oversized_reference_directory_is_sampled_evenly() {
        let dir = scratch("cap");
        let n = MAX_REFERENCE_FRAMES * 3;
        for i in 0..n {
            write_png(&dir.join(format!("f_{i:03}.png")), 8, 8);
        }
        let got = resolve_reference_frames(&dir.to_string_lossy()).unwrap();
        assert_eq!(got.len(), MAX_REFERENCE_FRAMES);
        let name = |p: &std::path::Path| p.file_name().unwrap().to_string_lossy().into_owned();
        assert_eq!(name(&got[0]), "f_000.png", "首张必须取到");
        assert_eq!(
            name(got.last().unwrap()),
            format!("f_{:03}.png", n - 1),
            "末张必须取到 —— 否则整组还是偏在前半段"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn even_subset_spreads_across_the_whole_list() {
        assert_eq!(even_subset((0..10).collect(), 4), vec![0, 3, 6, 9]);
        // 要的比有的多 / 刚好一样多: 原样返回。
        assert_eq!(even_subset(vec![1, 2, 3], 5), vec![1, 2, 3]);
        assert_eq!(even_subset(vec![1, 2, 3], 3), vec![1, 2, 3]);
        assert_eq!(even_subset(vec![1, 2, 3], 1), vec![1]);
        assert!(even_subset(vec![1, 2, 3], 0).is_empty());
    }

    /// 帧集的 PSNR 取逐帧 dB 的算术平均, 逐帧值一并留着 ——
    /// 均值看不出"一组帧里某一张特别差"。
    #[test]
    fn a_frame_set_folds_into_the_mean_of_per_frame_db() {
        let s = |psnr: Option<f64>| QualitySample {
            psnr_db: psnr,
            psnr_per_frame: psnr.into_iter().collect(),
            ..Default::default()
        };
        let got = fold_quality_frames(&[s(Some(30.0)), s(Some(40.0)), s(Some(35.0))]);
        assert!((got.psnr_db.unwrap() - 35.0).abs() < 1e-9);
        assert_eq!(got.psnr_per_frame, vec![30.0, 40.0, 35.0]);

        // 中间那张量不到时留 NaN 占位, 不塌缩 —— 否则下标跟 frame_files 错开,
        // "第 3 张最差" 会指到另一张图上。
        let holey = fold_quality_frames(&[s(Some(30.0)), s(None), s(Some(40.0))]);
        assert_eq!(holey.psnr_per_frame.len(), 3);
        assert!(holey.psnr_per_frame[1].is_nan());
        assert!((holey.psnr_db.unwrap() - 35.0).abs() < 1e-9, "均值不该把 NaN 算进去");
    }

    /// 两臂的均值必须来自同一批帧。一臂在某张上挂了, 另一臂那张也要剔掉 ——
    /// 否则"候选比基线高 2 dB"可能只是因为候选少算了一张高频最狠的帧,
    /// 而画质地板取的正是基线臂均值。
    #[test]
    fn the_two_arms_average_over_the_same_frames() {
        let s = |v: Vec<f64>| QualitySample {
            psnr_db: {
                let k: Vec<f64> = v.iter().copied().filter(|x| x.is_finite()).collect();
                if k.is_empty() { None } else { Some(gpustat::mean(&k)) }
            },
            psnr_per_frame: v,
            ..Default::default()
        };
        // 基线三张都有, 候选第 2 张挂了。
        let (b, c) = align_arms(
            s(vec![29.0, 20.0, 29.4]),
            s(vec![31.0, f64::NAN, 31.4]),
        );
        // 剔掉第 2 张之后, 基线均值不该再被那张 20 dB 拉低。
        assert!((b.psnr_db.unwrap() - 29.2).abs() < 1e-9, "{:?}", b.psnr_db);
        assert!((c.psnr_db.unwrap() - 31.2).abs() < 1e-9, "{:?}", c.psnr_db);
        // 位置留着, 仍与 frame_files 对齐。
        assert_eq!(b.psnr_per_frame.len(), 3);
        assert!(b.psnr_per_frame[1].is_nan());

        // 两臂都齐时原样不动。
        let (b2, c2) = align_arms(s(vec![29.0, 29.4]), s(vec![31.0, 31.4]));
        assert_eq!(b2.psnr_per_frame, vec![29.0, 29.4]);
        assert!((c2.psnr_db.unwrap() - 31.2).abs() < 1e-9);
    }

    /// 一项都没量到就如实留空, 不拿 0 顶上 —— 「没量」和「0 dB」是两回事。
    #[test]
    fn a_frame_set_with_nothing_measured_stays_empty() {
        let got = fold_quality_frames(&[QualitySample::default(), QualitySample::default()]);
        assert_eq!(got.psnr_db, None);
        assert_eq!(got.hud_ghost_db, None);
        // 逐帧位置仍在 (与 frame_files 对齐), 只是每个都是 NaN → 序列化成 null。
        assert_eq!(got.psnr_per_frame.len(), 2);
        assert!(got.psnr_per_frame.iter().all(|v| v.is_nan()));
    }

    /// 报告必须写清楚 dB 是对着什么量的: 绝对 dB 不可跨参考图比较,
    /// 不写来源的话 31.3 dB 和 38.7 dB 会被当成同一把尺子上的数。
    #[test]
    fn the_report_states_where_the_quality_truth_came_from() {
        let mut r = a_report();
        r.metrics.psnr_db = 31.333;
        r.quality_reference = Some(QualityReference {
            kind: "real_frames".into(),
            source: Some("tasks/genshin_refframes".into()),
            frames: 12,
            frame_files: vec!["frame_00000.png".into()],
            candidate_psnr_db: vec![31.0, 31.6],
            baseline_psnr_db: vec![29.0, 29.3],
        });
        let text = render_text(&r, None, None);
        assert!(text.contains("真机参考帧 x12"), "{text}");
        assert!(text.contains("tasks/genshin_refframes"), "{text}");

        r.quality_reference = Some(QualityReference::procedural());
        let text = render_text(&r, None, None);
        assert!(text.contains("程序化图案"), "{text}");

        // 契约字段名不能改: 上游按名字解析来源。
        let json = serde_json::to_value(&r).unwrap();
        assert_eq!(json["quality_reference"]["kind"], "procedural");
    }

    /// 上游用契约字段指定参考帧, 不必改命令行。
    #[test]
    fn the_request_can_carry_the_quality_reference() {
        let mut r = a_request();
        assert!(r.quality_reference.is_none(), "缺省不带 = 退回程序化图案");
        r.quality_reference = Some("tasks/genshin_refframes".into());
        let round: EvalRequest =
            serde_json::from_str(&serde_json::to_string(&r).unwrap()).unwrap();
        assert_eq!(
            round.quality_reference.as_deref(),
            Some("tasks/genshin_refframes")
        );
    }

    // ---------- 契约 ----------

    fn a_request() -> EvalRequest {
        EvalRequest {
            version: 1,
            candidate_id: "gen2_llm1".into(),
            track: "sr".into(),
            serial: Some("91253241019A".into()),
            package: "com.example.target".into(),
            replay_script: "sr_replay_60s".into(),
            shader_path: "runs/evo/candidates/gen2_llm1.comp".into(),
            spirv_path: "runs/evo/candidates/gen2_llm1.spv".into(),
            budget_ms: 1.5,
            quality_baseline_db: 37.286,
            incumbent_latency_ms: None,
            quality_reference: None,
            v2_context: None,
            protocol: EvalProtocol {
                cool_c: 32.0,
                replay_seconds: 60,
                rounds: 4,
                alternating: true,
                p_threshold: 0.01,
            },
        }
    }

    fn a_report() -> EvalReport {
        EvalReport {
            candidate_id: "gen2_llm1".into(),
            status: Status::Pass,
            metrics: Metrics {
                operator_latency_ms: 0.477,
                fps_p95_ms: Some(7.358),
                power_watt: 6.507,
                psnr_db: f32::NAN,
            },
            verdict: Verdict {
                is_pareto_improvement: false,
                p_value: 0.005,
            },
            source: "phonefarm".into(),
            artifacts: None,
            quality_reference: None,
            power_source: None,
        }
    }

    #[test]
    fn unknown_battery_observation_is_null_not_request_echo() {
        let b=hwcond::BatteryState {status:"Unknown".into(),current_ua:Some(-100),capacity_pct:Some(80)};
        assert_eq!(battery_evidence(&b)["on_battery"],Value::Null);
    }

    #[test]
    fn v2_refbench_actual_unknown_or_mismatch_cannot_pass_conditions() {
        let mut r=RefbenchArm::default();
        assert!(verify_refbench_conditions(&r,Arm::Baseline,300,3).is_err());
        r.frames_submitted=Some(300);
        assert!(verify_refbench_conditions(&r,Arm::Baseline,300,3).is_err());
        r.postfx_active=Some(false);
        assert!(verify_refbench_conditions(&r,Arm::Baseline,300,3).is_err());
        r.frames_config=Some(300); r.intensity=Some(3); r.scene=Some(refbench::SCENE.into()); r.run_id=Some("gpuop_A".into());
        assert!(verify_refbench_conditions(&r,Arm::Baseline,300,3).is_ok());
        assert!(verify_refbench_conditions(&r,Arm::Candidate,300,3).is_err());
        r.postfx_active=Some(true); r.run_id=Some("gpuop_B".into());
        assert!(verify_refbench_conditions(&r,Arm::Candidate,300,3).is_ok());
        assert!(verify_refbench_conditions(&r,Arm::Candidate,300,4).is_err());
        assert!(verify_refbench_conditions(&r,Arm::Candidate,301,3).is_err());
    }

    #[test]
    fn refbench_quality_capture_or_unknown_declarations_never_supply_performance() {
        let ordinary = serde_json::json!({"frames_submitted":300,"params":{"frames":300,"intensity":3},
            "scene":refbench::SCENE,"run_id":"gpuop_A","clean_exit":true,"postfx":{"active":false}});
        let mut arm = RefbenchArm::default();
        arm.read_report(&ordinary);
        assert!(verify_refbench_performance_scope(&arm).is_ok());
        assert!(verify_refbench_conditions(&arm, Arm::Baseline, 300, 3).is_ok());
        for declarations in [
            serde_json::json!({"capture_mode":"quality_only","performance_eligible":false}),
            serde_json::json!({"performance_eligible":false}),
            serde_json::json!({"performance_eligible":true}),
            serde_json::json!({"capture_mode":"off","performance_eligible":true}),
            serde_json::json!({"capture_mode":"unknown"}),
            serde_json::json!({"capture_mode":null}),
            serde_json::json!({"performance_eligible":null}),
            serde_json::json!({"capture_mode":false,"performance_eligible":"true"}),
        ] {
            let mut report = ordinary.clone();
            report.as_object_mut().unwrap().extend(declarations.as_object().unwrap().clone());
            let mut arm = RefbenchArm::default();
            arm.read_report(&report);
            assert!(arm.clean_exit && arm.frames_submitted == Some(300));
            assert!(verify_refbench_performance_scope(&arm).is_err(), "legacy accepted {report}");
            assert!(verify_refbench_conditions(&arm, Arm::Baseline, 300, 3).is_err(), "v2 accepted {report}");
        }
    }

    /// 上游 game_opt_loop 写出来的那份 JSON 必须能被原样读进来。
    #[test]
    fn deserialises_the_upstream_request_shape() {
        let raw = r#"{
            "version": 1, "candidate_id": "gen2_c3", "track": "sr",
            "serial": "R5CT30ABCDE", "package": "com.miHoYo.Yuanshen",
            "replay_script": "sr_replay_60s",
            "shader_path": "runs/evo/candidates/gen2_c3.comp",
            "spirv_path": "runs/evo/candidates/gen2_c3.spv",
            "budget_ms": 1.5, "quality_baseline_db": 37.286,
            "protocol": {"cool_c": 32.0, "replay_seconds": 60, "rounds": 4,
                         "alternating": true, "p_threshold": 0.01}
        }"#;
        let r: EvalRequest = serde_json::from_str(raw).unwrap();
        assert_eq!(r.candidate_id, "gen2_c3");
        assert_eq!(r.protocol.cool_c, 32.0);
        assert!(r.protocol.alternating);
        assert!(validate_request(&r).is_ok());
    }

    /// serial 是可选字段, 缺了也要能读 (上游省略即"由 phonefarm 自己挑设备")。
    #[test]
    fn serial_is_optional() {
        let raw = r#"{"version":1,"candidate_id":"c","track":"frame_gen",
            "package":"p","replay_script":"s","shader_path":"a","spirv_path":"b",
            "budget_ms":2.5,"quality_baseline_db":34.0,
            "protocol":{"cool_c":32.0,"replay_seconds":60,"rounds":4,
                        "alternating":true,"p_threshold":0.01}}"#;
        let r: EvalRequest = serde_json::from_str(raw).unwrap();
        assert!(r.serial.is_none());
        assert!(validate_request(&r).is_ok());
    }

    #[test]
    fn rejects_malformed_requests() {
        let mut r = a_request();
        r.version = 2;
        assert!(validate_request(&r).is_err());

        let mut r = a_request();
        r.track = "raytracing".into();
        assert!(validate_request(&r).is_err());

        let mut r = a_request();
        r.budget_ms = 0.0;
        assert!(validate_request(&r).is_err());

        // 每臂只跑 1 轮做不了两样本检验
        let mut r = a_request();
        r.protocol.rounds = 1;
        assert!(validate_request(&r).unwrap_err().contains("两样本"));

        let mut r = a_request();
        r.protocol.p_threshold = 1.5;
        assert!(validate_request(&r).is_err());
        let mut r=a_request(); r.v2_context=Some(serde_json::json!("malformed"));
        assert!(validate_request(&r).is_err());
    }

    /// 报告序列化出来的枚举必须是上游契约里的那几个字面量。
    #[test]
    fn report_serialises_to_the_contract_vocabulary() {
        let rep = EvalReport {
            candidate_id: "c".into(),
            status: Status::PoorQuality,
            metrics: Metrics {
                operator_latency_ms: 1.1,
                fps_p95_ms: Some(16.9),
                power_watt: 4.7,
                psnr_db: 36.0,
            },
            verdict: Verdict {
                is_pareto_improvement: false,
                p_value: 0.004,
            },
            source: "phonefarm".into(),
            artifacts: None,
            quality_reference: None,
            power_source: None,
        };
        let v = serde_json::to_value(&rep).unwrap();
        assert_eq!(v["status"], "POOR_QUALITY");
        assert_eq!(v["source"], "phonefarm");
        for s in [Status::Pass, Status::Slow, Status::Crash, Status::Reverted] {
            let j = serde_json::to_value(s).unwrap();
            let t = j.as_str().unwrap();
            assert!(
                ["PASS", "SLOW", "CRASH", "REVERTED"].contains(&t),
                "非法 status 字面量 {t}"
            );
        }
    }

    // ---------- SPIR-V 预检 ----------

    /// 造一个最小但结构合法的 compute SPIR-V (只含 header + OpEntryPoint + OpExecutionMode)。
    fn minimal_spirv(local: [u32; 3], entry: &str, model: u32) -> Vec<u8> {
        let mut w: Vec<u32> = vec![SPIRV_MAGIC, 0x0001_0300, 0, 10, 0];

        // OpEntryPoint: model, id, name...
        let mut name: Vec<u8> = entry.as_bytes().to_vec();
        name.push(0);
        while name.len() % 4 != 0 {
            name.push(0);
        }
        let name_words: Vec<u32> = name
            .chunks_exact(4)
            .map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]]))
            .collect();
        let wc = 3 + name_words.len();
        w.push(((wc as u32) << 16) | OP_ENTRY_POINT as u32);
        w.push(model);
        w.push(1);
        w.extend_from_slice(&name_words);

        // OpExecutionMode: id, LocalSize, x, y, z
        w.push((6u32 << 16) | OP_EXECUTION_MODE as u32);
        w.push(1);
        w.push(EXEC_MODE_LOCAL_SIZE);
        w.extend_from_slice(&local);

        w.iter().flat_map(|x| x.to_le_bytes()).collect()
    }

    #[test]
    fn accepts_a_well_formed_compute_module() {
        let b = minimal_spirv([16, 16, 1], "main", EXEC_MODEL_GLCOMPUTE);
        let i = parse_spirv(&b).unwrap();
        assert_eq!(i.version_major, 1);
        assert_eq!(i.version_minor, 3);
        assert_eq!(i.entry_points, vec!["main".to_string()]);
        assert_eq!(i.local_size, Some([16, 16, 1]));
        assert_eq!(i.bytes, b.len());
    }

    #[test]
    fn rejects_non_spirv_bytes() {
        assert!(parse_spirv(b"not spirv at all, really").is_err());
        assert!(parse_spirv(&[0u8; 8]).is_err());
        // 长度非 4 倍数
        let mut b = minimal_spirv([16, 16, 1], "main", EXEC_MODEL_GLCOMPUTE);
        b.push(0);
        assert!(parse_spirv(&b).is_err());
    }

    /// 顶点/片元着色器不是算子, 挂上去合成层会直接崩。
    #[test]
    fn rejects_a_module_with_no_compute_entry_point() {
        let b = minimal_spirv([1, 1, 1], "main", 0); // 0 = Vertex
        let e = parse_spirv(&b).unwrap_err();
        assert!(e.contains("GLCompute"), "{e}");
    }

    #[test]
    fn rejects_an_oversized_workgroup() {
        let b = minimal_spirv([32, 32, 2], "main", EXEC_MODEL_GLCOMPUTE); // 2048
        let e = parse_spirv(&b).unwrap_err();
        assert!(e.contains("1024"), "{e}");
    }

    #[test]
    fn rejects_a_zero_dimension_workgroup() {
        let b = minimal_spirv([16, 0, 1], "main", EXEC_MODEL_GLCOMPUTE);
        assert!(parse_spirv(&b).is_err());
    }

    /// 截断的字节码必须在越界前被发现, 不能跑飞。
    #[test]
    fn rejects_a_truncated_instruction_stream() {
        let mut b = minimal_spirv([16, 16, 1], "main", EXEC_MODEL_GLCOMPUTE);
        b.truncate(b.len() - 8);
        let e = parse_spirv(&b).unwrap_err();
        assert!(e.contains("越界") || e.contains("入口点"), "{e}");
    }

    #[test]
    fn reads_big_endian_modules() {
        let le = minimal_spirv([8, 8, 1], "main", EXEC_MODEL_GLCOMPUTE);
        let be: Vec<u8> = le
            .chunks_exact(4)
            .flat_map(|c| [c[3], c[2], c[1], c[0]])
            .collect();
        let i = parse_spirv(&be).unwrap();
        assert_eq!(i.local_size, Some([8, 8, 1]));
    }

    // ---------- A/B/A/B 调度 ----------

    #[test]
    fn alternating_schedule_interleaves_the_arms() {
        let s = ab_schedule(3, true);
        assert_eq!(s.len(), 6);
        assert_eq!(
            s,
            vec![
                Arm::Baseline,
                Arm::Candidate,
                Arm::Baseline,
                Arm::Candidate,
                Arm::Baseline,
                Arm::Candidate
            ]
        );
    }

    #[test]
    fn non_alternating_schedule_runs_each_arm_in_a_block() {
        let s = ab_schedule(2, false);
        assert_eq!(
            s,
            vec![Arm::Baseline, Arm::Baseline, Arm::Candidate, Arm::Candidate]
        );
    }

    /// 两臂轮数必须相等 —— 不等会让热漂移的一阶抵消失效。
    #[test]
    fn both_arms_always_get_the_same_number_of_rounds() {
        for rounds in 2..=8 {
            for alt in [true, false] {
                let s = ab_schedule(rounds, alt);
                let a = s.iter().filter(|x| **x == Arm::Baseline).count();
                let b = s.iter().filter(|x| **x == Arm::Candidate).count();
                assert_eq!(a, b, "rounds={rounds} alt={alt}");
                assert_eq!(a, rounds as usize);
            }
        }
    }

    // ---------- runner 回包 ----------

    #[test]
    fn runner_exit_status_precedes_successful_json() {
        let report = r#"{"v":1,"ok":true,"timing_us":{"samples":[850.0],"median":850.0},"psnr_db":41.0,"error":null}"#;
        let completed = format!("{report}\nPHONEFARM_VKOP_EXIT=0\n");
        assert!(parse_completed_runner_output(&completed).unwrap().ok);
        for code in [1, 2, 134, 139] {
            let failed = format!("{report}\nPHONEFARM_VKOP_EXIT={code}\n");
            assert!(parse_completed_runner_output(&failed).is_err(), "accepted exit {code}");
        }
        assert!(parse_completed_runner_output(report).is_err(), "missing completion was accepted");
        assert!(parse_completed_runner_output(&format!("{completed}diagnostic tail")).is_err());
    }

    #[test]
    fn runner_exit_command_retains_child_failure() {
        let command = completed_runner_command("sh -c 'printf \"{\\\"ok\\\":true}\\n\"; exit 134'");
        let output = std::process::Command::new("sh").args(["-c", &command]).output().unwrap();
        let text = String::from_utf8(output.stdout).unwrap();
        assert!(text.trim_end().ends_with("PHONEFARM_VKOP_EXIT=134"), "{text}");
        assert!(parse_completed_runner_output(&text).is_err());
    }

    #[test]
    fn parses_a_successful_runner_report() {
        let out = r#"{"v":1,"ok":true,
            "timing_us":{"samples":[1102.0,1098.5,1105.2],"median":1102.0},
            "psnr_db":38.42,"device":{"name":"Adreno (TM) 840"},"error":null}"#;
        let r = parse_runner_output(out).unwrap();
        assert!(r.ok);
        assert_eq!(r.timing_us.len(), 3);
        assert_eq!(r.psnr_db, Some(38.42));
        assert_eq!(r.device_name.as_deref(), Some("Adreno (TM) 840"));
        assert!((r.median_ms().unwrap() - 1.102).abs() < 1e-9);
    }

    #[test]
    fn picks_the_report_out_of_noisy_stdout() {
        let out = "creating vulkan device\nallocating images\n{\"ok\":true,\"timing_us\":{\"samples\":[900.0,910.0]}}\n";
        let r = parse_runner_output(out).unwrap();
        assert!(r.ok);
        assert_eq!(r.timing_us.len(), 2);
    }

    #[test]
    fn a_failed_runner_report_carries_its_error() {
        let out = r#"{"ok":false,"timing_us":{"samples":[]},"error":"VK_ERROR_DEVICE_LOST"}"#;
        let r = parse_runner_output(out).unwrap();
        assert!(!r.ok);
        assert_eq!(r.error.as_deref(), Some("VK_ERROR_DEVICE_LOST"));
        assert!(r.median_ms().is_none());
    }

    /// 自称成功却没有样本 = 回包有问题, 不能当成 0 延迟。
    #[test]
    fn a_success_with_no_samples_is_rejected() {
        let out = r#"{"ok":true,"timing_us":{"samples":[]}}"#;
        assert!(parse_runner_output(out).is_err());
    }

    #[test]
    fn rejects_empty_or_unparseable_runner_output() {
        assert!(parse_runner_output("").is_err());
        assert!(parse_runner_output("Segmentation fault").is_err());
    }

    // ---------- 插帧伪影门禁 ----------
    //
    // 真机实测四个点 (2026-09-22, NX809J, 合成插帧场景):
    //   干净算子                 hud_ghost 99.00 dB / stretch  0.00%
    //   故意做重影的对照组        hud_ghost  5.32 dB / stretch  0.00%
    //   故意做拉扯的对照组        hud_ghost 99.00 dB / stretch 55.72%
    // 两个对照组各自只触发一个门 —— 说明两项量的是不同的失效模式,
    // 不是换个名字重读一遍 PSNR。

    #[test]
    fn a_clean_operator_trips_neither_artifact_gate() {
        let g = ArtifactGates::default();
        let a = judge_artifacts(&g, Some(99.0), Some(99.0), Some(0.0), Some(0.0));
        assert!(!a.regressed, "{:?}", a.reason);
    }

    /// HUD 重影必须被抓到: 世界在动而 HUD 不动, 块匹配把 HUD 当成运动内容,
    /// 字就被拉成双份 —— 插帧在手游上最刺眼的失效。
    #[test]
    fn hud_ghosting_is_caught() {
        let g = ArtifactGates::default();
        let a = judge_artifacts(&g, Some(5.32), Some(99.0), Some(0.0), Some(0.0));
        assert!(a.regressed);
        assert!(a.reason.as_ref().unwrap().contains("HUD 重影"), "{:?}", a.reason);
    }

    /// 拉扯果冻必须被抓到, 且与重影互不干扰。
    #[test]
    fn stretching_is_caught_independently_of_ghosting() {
        let g = ArtifactGates::default();
        let a = judge_artifacts(&g, Some(99.0), Some(99.0), Some(55.72), Some(0.0));
        assert!(a.regressed);
        assert!(a.reason.as_ref().unwrap().contains("拉扯"), "{:?}", a.reason);
    }

    /// 容差之内的小波动不算退化, 否则每一轮的测量噪声都会变成一次误杀。
    #[test]
    fn small_fluctuations_within_tolerance_are_not_a_regression() {
        let g = ArtifactGates::default();
        assert!(!judge_artifacts(&g, Some(97.0), Some(99.0), Some(0.5), Some(0.0)).regressed);
        assert!(judge_artifacts(&g, Some(95.0), Some(99.0), Some(0.0), Some(0.0)).regressed);
        assert!(judge_artifacts(&g, Some(99.0), Some(99.0), Some(1.6), Some(0.0)).regressed);
    }

    #[test]
    fn beating_the_baseline_is_never_a_regression() {
        let g = ArtifactGates::default();
        assert!(!judge_artifacts(&g, Some(99.0), Some(40.0), Some(0.0), Some(9.0)).regressed);
    }

    /// 缺读数时不下结论 —— SR 赛道没有这两项, 不能因为"没量到"就判失败。
    #[test]
    fn missing_readings_yield_no_verdict() {
        let g = ArtifactGates::default();
        let a = judge_artifacts(&g, None, None, None, None);
        assert!(!a.regressed);
        assert!(a.hud_ghost_db.is_none() && a.stretch_pct.is_none());
        assert!(!judge_artifacts(&g, Some(5.0), None, None, None).regressed);
    }

    #[test]
    fn runner_report_parses_the_artifact_block() {
        let out = r#"{"ok":true,"timing_us":{"samples":[900.0,910.0]},
            "psnr_db":40.5,"artifacts":{"hud_ghost_db":5.318,"stretch_pct":55.721}}"#;
        let r = parse_runner_output(out).unwrap();
        assert_eq!(r.hud_ghost_db, Some(5.318));
        assert_eq!(r.stretch_pct, Some(55.721));
    }

    /// SR 赛道的回包没有 artifacts 段, 解析必须照常。
    #[test]
    fn a_report_without_artifacts_still_parses() {
        let out = r#"{"ok":true,"timing_us":{"samples":[900.0,910.0]},"psnr_db":42.8}"#;
        let r = parse_runner_output(out).unwrap();
        assert!(r.hud_ghost_db.is_none() && r.stretch_pct.is_none());
    }

    // ---------- 冻结判定 ----------

    fn rules() -> FrozenRules {
        FrozenRules {
            budget_ms: 1.5,
            quality_baseline_db: 37.286,
            p_threshold: 0.01,
            incumbent_latency_ms: None,
        }
    }

    fn metrics(lat: f32, psnr: f32) -> Metrics {
        Metrics {
            operator_latency_ms: lat,
            fps_p95_ms: Some(16.9),
            power_watt: 4.7,
            psnr_db: psnr,
        }
    }

    fn welch(p: f64) -> WelchResult {
        WelchResult {
            n_a: 4,
            n_b: 4,
            mean_a: 1.2,
            mean_b: 1.0,
            delta: -0.2,
            t: -5.0,
            df: 6.0,
            p_value: p,
        }
    }

    #[test]
    fn over_budget_is_slow_regardless_of_quality() {
        let (s, v) = decide(&rules(), &metrics(1.9, 40.0), None, Some(&welch(0.001)), false);
        assert_eq!(s, Status::Slow);
        assert!(!v.is_pareto_improvement);
    }

    /// 画质低于基线 → 不合格品。哪怕"显著地更快"也不行。
    #[test]
    fn below_the_quality_floor_is_rejected_even_when_significantly_faster() {
        let base = metrics(1.20, 38.0);
        let cand = metrics(0.80, 36.0);
        let (s, v) = decide(&rules(), &cand, Some(&base), Some(&welch(0.0001)), false);
        assert_eq!(s, Status::PoorQuality);
        assert!(
            !v.is_pareto_improvement,
            "掉画质的算子不能被记成 Pareto 改进"
        );
    }

    #[test]
    fn a_crash_short_circuits_everything() {
        let (s, _) = decide(&rules(), &metrics(0.5, 40.0), None, None, true);
        assert_eq!(s, Status::Crash);
    }

    /// 更快但不显著 = 噪声, 不算改进。这是防硬件噪声欺骗的核心一条。
    #[test]
    fn faster_but_not_significant_is_not_an_improvement() {
        let base = metrics(1.02, 38.0);
        let cand = metrics(1.00, 38.1);
        let (s, v) = decide(&rules(), &cand, Some(&base), Some(&welch(0.42)), false);
        assert_eq!(s, Status::Pass);
        assert!(!v.is_pareto_improvement);
        assert!((v.p_value - 0.42).abs() < 1e-12);
    }

    #[test]
    fn significantly_faster_and_within_quality_is_an_improvement() {
        let base = metrics(1.20, 38.0);
        let cand = metrics(1.00, 38.2);
        let (s, v) = decide(&rules(), &cand, Some(&base), Some(&welch(0.001)), false);
        assert_eq!(s, Status::Pass);
        assert!(v.is_pareto_improvement);
    }

    /// 显著但更慢也不是改进 —— 显著性只说明差异真实, 不说明方向对。
    #[test]
    fn significantly_slower_is_not_an_improvement() {
        let base = metrics(1.00, 38.0);
        let cand = metrics(1.30, 38.1);
        let (s, v) = decide(&rules(), &cand, Some(&base), Some(&welch(0.0005)), false);
        assert_eq!(s, Status::Pass);
        assert!(!v.is_pareto_improvement);
    }

    /// 没做检验时 p 记 1.0 哨兵, 不伪造小 p 值。
    #[test]
    fn a_missing_test_reports_p_as_one() {
        let (_, v) = decide(&rules(), &metrics(1.0, 38.0), None, None, false);
        assert_eq!(v.p_value, 1.0);
        assert!(!v.is_pareto_improvement);
    }

    // ---------- CLI ----------

    #[test]
    fn parses_the_usual_invocation() {
        let a = parse_args(&[
            "--request".into(),
            "r.json".into(),
            "--serial".into(),
            "91253241019A".into(),
            "--json".into(),
            "--power-rail".into(),
            "battery".into(),
        ])
        .unwrap();
        assert_eq!(a.request_path.as_deref(), Some("r.json"));
        assert_eq!(a.serial.as_deref(), Some("91253241019A"));
        assert!(a.as_json);
        assert_eq!(a.power_rail, PowerRail::Battery);
    }

    #[test]
    fn unlock_needs_no_request() {
        let a = parse_args(&["--serial".into(), "S".into(), "--unlock".into()]).unwrap();
        assert!(a.unlock_only);
        assert!(a.request_path.is_none());
    }

    #[test]
    fn rejects_missing_request_and_bad_flags() {
        assert!(parse_args(&["--json".into()]).is_err());
        assert!(parse_args(&["--request".into(), "r.json".into(), "--power-rail".into(), "solar".into()]).is_err());
        assert!(parse_args(&["--nope".into()]).is_err());
    }

    /// refbench 载体: 基线臂不挂算子, 它的算子耗时按定义是 0。
    ///
    /// 不带在位冠军耗时的话, "候选更快吗" 就成了 `正数 < 0` —— 恒假,
    /// `is_pareto_improvement` 永远 false。实跑验证过: 一整轮 5 个候选
    /// p 值全在 1e-7 量级 (测量极显著), 却一个 true 都没有。
    #[test]
    fn against_an_empty_baseline_arm_the_incumbent_decides_what_faster_means() {
        let strong = welch(1e-7);
        // refbench 的基线臂: 算子耗时 0 (没挂算子)。
        let empty_arm = Metrics {
            operator_latency_ms: 0.0,
            fps_p95_ms: Some(7.24),
            power_watt: 6.35,
            psnr_db: 38.7,
        };
        let cand = Metrics {
            operator_latency_ms: 0.29,
            fps_p95_ms: Some(7.28),
            power_watt: 6.41,
            psnr_db: 38.9,
        };

        // 没有冠军可比 → 跟空基线臂比, 只能是 false。这是老行为, 保留它作对照。
        let (s, v) = decide(&rules(), &cand, Some(&empty_arm), Some(&strong), false);
        assert_eq!(s, Status::Pass);
        assert!(
            !v.is_pareto_improvement,
            "跟一个不挂算子的基线比, 任何算子都不可能'更快'"
        );

        // 带上冠军 0.50 ms → 候选 0.29 ms 确实更便宜, 这才是真问题的答案。
        let mut r = rules();
        r.incumbent_latency_ms = Some(0.50);
        let (s, v) = decide(&r, &cand, Some(&empty_arm), Some(&strong), false);
        assert_eq!(s, Status::Pass);
        assert!(v.is_pareto_improvement, "比在位冠军便宜却没判成改进");

        // 冠军比它还便宜 → 不是改进。
        r.incumbent_latency_ms = Some(0.18);
        let (_, v) = decide(&r, &cand, Some(&empty_arm), Some(&strong), false);
        assert!(!v.is_pareto_improvement);

        // 显著性仍是必要条件: 不显著就谈不上改进, 哪怕数字更小。
        r.incumbent_latency_ms = Some(0.50);
        let noise = welch(0.6);
        let (_, v) = decide(&r, &cand, Some(&empty_arm), Some(&noise), false);
        assert!(!v.is_pareto_improvement, "不显著的差异不算改进");
    }

    /// 画质补测缺省开 —— 关掉画质硬底线必须是个显式动作。
    #[test]
    fn the_quality_pass_is_on_unless_explicitly_waived() {
        let on = parse_args(&["--request".into(), "r.json".into()]).unwrap();
        assert!(on.quality_pass);
        let off = parse_args(&[
            "--request".into(),
            "r.json".into(),
            "--no-quality-pass".into(),
        ])
        .unwrap();
        assert!(!off.quality_pass);
    }

    /// 量不到的指标必须序列化成 `null`, 不能变成 0 或 NaN 字面量。
    ///
    /// 上游把 `psnr_db` / `fps_p95_ms` 声明成可空是**踩过两次**才改的:
    /// 两次都是真机头一回回 null 才暴露, 而那一批候选的真实数字就躺在回包里
    /// 被整批打成 eval_error。这条钉死在这里, 免得第三次。
    #[test]
    fn unmeasured_metrics_serialize_as_null_not_as_zero() {
        let m = Metrics {
            operator_latency_ms: 0.29,
            fps_p95_ms: Some(7.28),
            power_watt: 6.34,
            psnr_db: f32::NAN,
        };
        let v: serde_json::Value = serde_json::to_value(&m).unwrap();
        assert!(v["psnr_db"].is_null(), "NaN 没有落成 null: {v}");
        assert_eq!(v["fps_p95_ms"].as_f64().map(|x| (x * 100.0).round()), Some(728.0));

        let none = Metrics {
            operator_latency_ms: 0.29,
            fps_p95_ms: None,
            power_watt: f32::NAN,
            psnr_db: f32::NAN,
        };
        let v: serde_json::Value = serde_json::to_value(&none).unwrap();
        assert!(v["fps_p95_ms"].is_null());
        assert!(v["power_watt"].is_null());
    }

    #[test]
    fn power_rail_defaults_to_the_read_only_usb_rail() {
        let a = parse_args(&["--request".into(), "r.json".into()]).unwrap();
        assert_eq!(a.power_rail, PowerRail::Usb);
    }
}
