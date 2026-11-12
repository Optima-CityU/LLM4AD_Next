//! 灰层共享合同、诊断与指定目标停用；安装由持久 Session 管理。
//!
//! 机制 (2026-09-23 在 NX809J / Android 16 / user 版 + Magisk root 上逐条实测):
//! 生效只需要两件事, 缺一不可:
//!   1. libVkLayer_refknobs.so 放进**目标应用自己的 nativeLibraryDir**
//!      —— 加载器对每个应用都默认搜这个目录, 所以不需要应用 debuggable
//!   2. `setprop debug.vulkan.layers <层名>`
//!
//! 实测**不需要**: settings put global enable_gpu_debug_layers 那一套
//! (框架侧对非 debuggable 应用直接跳过), 以及 ro.debuggable=1。
//!
//! ⚠ debug.vulkan.layers 是**全局**属性, 会误伤别的应用: 属性置上之后启动的、
//! 自己 lib 目录里没有这个 .so 的 Vulkan 应用 vkCreateInstance 直接失败
//! (实测 cn.nubia.gameassist RenderThread SIGABRT 每 ~2 秒崩溃重启一次)。
//! 持续会话在目标加载后收窄全局属性，并由同一 owner 恢复原值。
//! 旧的挂层后退出入口已退役；`off` 仅处理已登记的历史遗留，不等于恢复会话。

use crate::eval::{adb, adb_ok, adb_path, resolve_path};
use serde::Serialize;
use serde_json::{json, Value};
use std::path::Path;

pub(crate) const LAYER_NAME: &str = "VK_LAYER_refknobs_readonly";
const LOADOP_PROP: &str = "debug.knobs.loadop"; // 1 = 层把 loadOp LOAD 改写成 DONT_CARE
const DUMP_PROP: &str = "debug.knobs.passdump"; // 1 = 层导出 render pass 形状表
const CP_PROP: &str = "debug.knobs.copyprobe"; // 1 = 1:1 拷贝探针 (隐含 passdump 追踪)
const UPOP_PROP: &str = "debug.knobs.upop"; // 1 = 真超分算子 (gen1_loc3)
const NPUPROBE_PROP: &str = "debug.knobs.npuprobe";
/// Android 非只读属性值上限 91 字节 (PROP_VALUE_MAX 92 含结尾 0)。超了 setprop 会失败。
pub(crate) const PROP_VALUE_MAX: usize = 91;
pub(crate) const FA_PROP: &str = "debug.knobs.frameaudit"; // 1 = 一帧步骤清单 (只读, 隐含 passdump)
pub(crate) const FA_TS_PROP: &str = "debug.knobs.frameaudit_ts"; // 1 = 步骤清单另插 GPU 时间戳
pub(crate) const FA_GO_PROP: &str = "debug.knobs.frameaudit_go"; // 改成新编号 = 抓一次
pub(crate) const STOREOP_PROP: &str = "debug.knobs.storeop"; // "<sig>:<i>,..." / "ds" / 0 (#91)
pub(crate) const STOREOP_LIVE_PROP: &str = "debug.knobs.storeop_live"; // 1 = 孪生 pass 现场切换
pub(crate) const STOREOP_ON_PROP: &str = "debug.knobs.storeop_on"; // 现场切换时的开/关
/// 1 = NPU 超分替换原神自己的放大 (npu/n1-pipeline 的层; 隐含 upop, upop 双线性是它的回退)
pub(crate) const NPUOP_PROP: &str = "debug.knobs.npuop";
/// npuop 的调参属性 (层 `read_num_prop` 读; 每一臂都显式写一遍, 不留上一臂的残值)
pub(crate) const NPUOP_TUNING_PROPS: [&str; 7] = [
    "debug.knobs.npuop_lag",
    "debug.knobs.npuop_wait_ms",
    "debug.knobs.npuop_budget_ms",
    "debug.knobs.npuop_budget_frames",
    "debug.knobs.npuop_init_frame",
    "debug.knobs.npuop_zc",
    "debug.knobs.npuop_ts",
];
/// 层 .so 里有这个字符串 = 这个层带 npuop (R2F 之后的 n1-pipeline 构建)。
pub(crate) const NPUOP_SO_SIGNATURE: &[u8] = b"debug.knobs.npuop_wait_ms";
pub(crate) const SO: &str = "libVkLayer_refknobs.so";
pub(crate) const STATE: &str = "/data/local/tmp/knobs_layer_state"; // "把 .so 推进了哪个包"
pub(crate) const LAYER_PROPERTIES: [&str; 12] = [
    "debug.vulkan.layers", LOADOP_PROP, DUMP_PROP, CP_PROP, UPOP_PROP, NPUPROBE_PROP,
    FA_PROP, FA_TS_PROP, FA_GO_PROP, STOREOP_PROP, STOREOP_LIVE_PROP, STOREOP_ON_PROP,
];

pub(crate) const USAGE: &str = "用法: phonefarm gray-layer {status|off [pkg] [--json]} [--serial S]\n挂层使用: phonefarm gray-layer session --mode MODE --package PKG --serial S --out DIR --max-seconds N\n旧 probe/loadop/passdump/frameaudit/frameaudit-ts/target 已退役；旧 --keep-prop 的仅安装不启动语义不再提供，新 session 始终启动目标。\ncopyprobe/upop 当前不支持执行（包括自定义 SO）；工件与历史证据仍可离线分析。\nnpuop 挂层同样退役 (尚未接入持久 Session)；旧 eval npuop 中断留下的挂载用: phonefarm gray-layer npuop-restore <run 目录> [pkg] [--serial S]";

/// Only the old install-and-exit commands are retired. The same mode names
/// remain available to the persistent control Session.
fn legacy_mount_rejection(mode: &str) -> Option<Value> {
    if !matches!(mode, "probe" | "loadop" | "passdump" | "frameaudit" | "frameaudit-ts" | "target" | "npuop") { return None; }
    Some(json!({
        "schema":"phonefarm/gray-legacy-mount-retirement@1",
        "requested_mode":mode,
        "status":"retired",
        "reason":"persistent_owner_required",
        "execution_started":false,
        "device_commands_started":false,
        "replacement":"phonefarm gray-layer session --mode MODE --package PKG --serial S --out DIR --max-seconds N",
        "keep_prop_compatibility":"legacy_install_without_launch_is_not_supported"
    }))
}

/// Capability rejection is independent of a device, lock or artifact path.
pub(crate) fn mode_admission_rejection(mode: &str, custom: bool) -> Option<Value> {
    if !matches!(mode, "copyprobe" | "upop") { return None; }
    Some(json!({
        "schema":"phonefarm/gray-mode-admission@1",
        "requested_mode":mode,
        "artifact_source":if custom { "custom" } else { "default" },
        "status":"unsupported",
        "reason":if custom { "artifact_capability_unverified" } else { "missing_source_layout_target_compute_contract" },
        "execution_started":false,
        "device_commands_started":false,
        "collection_ok":false,
        "recovery_status":"not_started",
        "job_recovery_complete":false,
        "game_state_restored":false
    }))
}

const DEACTIVATION_SCOPE: &str = "known_target_layer_deactivated";

#[derive(Debug, Serialize)]
pub struct DeactivationCommand {
    pub command: String,
    /// Unmodified transport output, including its exit marker.
    pub output: String,
}

#[derive(Debug, Serialize)]
pub struct DeactivationEvidence {
    pub scope: &'static str,
    pub serial: String,
    pub package: String,
    pub recorded_installation: Option<String>,
    pub recorded_installation_absent: Option<bool>,
    pub state_absent: bool,
    pub processes_absent: bool,
    pub properties_disabled: bool,
    pub original_properties_restored: bool,
    pub unregistered_installations_verified: bool,
    pub game_state_restored: bool,
    pub job_recovery_complete: bool,
    pub commands: Vec<DeactivationCommand>,
}

#[derive(Debug, Serialize)]
pub struct DeactivationFailure {
    pub scope: &'static str,
    pub serial: String,
    pub package: Option<String>,
    pub error: String,
    pub game_state_restored: bool,
    pub job_recovery_complete: bool,
    pub commands: Vec<DeactivationCommand>,
}

impl std::fmt::Display for DeactivationEvidence {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: 已核验目标进程停止及灰层属性停用；{}。范围仅为指定目标摘层，不代表游戏状态或整个任务恢复。",
            self.package, if self.recorded_installation_absent == Some(true) {
                "登记的安装文件及 state 已缺席"
            } else { "没有安装登记，未验证未登记的安装文件" })
    }
}
impl std::fmt::Display for DeactivationFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "目标灰层停用未获核验: {}", self.error)
    }
}

pub(crate) fn deactivation_json(result: &Result<DeactivationEvidence, DeactivationFailure>) -> Value {
    match result {
        Ok(evidence) => json!({"schema":"phonefarm/gray-layer-deactivation@1", "status":"verified", "evidence":evidence}),
        Err(error) => json!({"schema":"phonefarm/gray-layer-deactivation@1", "status":"failed", "error":error}),
    }
}

pub(crate) fn record_deactivation(
    dir: &Path,
    phase: &str,
    result: &Result<DeactivationEvidence, DeactivationFailure>,
) -> Result<Value, String> {
    if phase.is_empty() || !phase.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_')) {
        return Err("invalid deactivation evidence phase".into());
    }
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let path = dir.canonicalize().map_err(|e| e.to_string())?.join(format!(
        "deactivation-{phase}-{}-{}.json", std::process::id(),
        chrono::Utc::now().timestamp_nanos_opt().ok_or("evidence timestamp unavailable")?));
    let mut value = deactivation_json(result);
    value["evidence_path"] = json!(path);
    crate::confirmation_transport::new_file(&path, &serde_json::to_vec_pretty(&value).map_err(|e| e.to_string())?)?;
    Ok(value)
}

/// 「去掉多余写回」的属性串: 每项 (pass 特征码前缀, 附件下标) → "<sig>:<i>,…"。
///
/// 特征码前缀要 ≥8 位十六进制 (层只认这么长的前缀, 短了容易撞上别的 pass), 全串不能超过
/// Android 属性值上限 —— 超了 setprop 静默失败, 层读到空串就当成关, 那一臂就成了对照臂。
pub fn storeop_spec(items: &[(String, u32)]) -> Result<String, String> {
    if items.is_empty() {
        return Ok("0".into());
    }
    let mut parts = Vec::new();
    for (sig, att) in items {
        let sig = sig.trim().to_ascii_lowercase();
        if sig.len() < 8 || sig.len() > 16 || !sig.chars().all(|c| c.is_ascii_hexdigit()) {
            return Err(format!("pass 特征码前缀要 8–16 位十六进制, 收到 {sig:?}"));
        }
        if *att > 31 {
            return Err(format!("附件下标 {att} 不合理"));
        }
        parts.push(format!("{sig}:{att}"));
    }
    parts.sort();
    parts.dedup();
    let s = parts.join(",");
    if s.len() > PROP_VALUE_MAX {
        return Err(format!(
            "写回改写指定串 {} 字节, 超过属性值上限 {PROP_VALUE_MAX}: 少放几项, 或把特征码前缀截短到 8 位",
            s.len()
        ));
    }
    Ok(s)
}

/// npuop 的调参 (对应 debug.knobs.npuop_*; 缺省值与层里 read_num_prop 的缺省一致)。
#[derive(Debug, Clone, PartialEq)]
pub struct NpuopProps {
    /// 1 = 等本帧打包 (晚 1 帧, 串行); 2 = 等上一帧 (晚 2 帧, NPU 与 GPU 并行)
    pub lag: u8,
    /// 等栅栏的预算 (ms), 超了该帧不推理
    pub wait_ms: f64,
    /// 等待+推理预算 (ms); 连续 budget_frames 帧超预算 → 永久回退双线性
    pub budget_ms: f64,
    pub budget_frames: u32,
    /// 第几帧之后做一次初始化 (约 360 ms 卡顿)
    pub init_frame: u64,
    /// 零拷贝 (张量直指映射内存)
    pub zc: bool,
    /// pack / unpack 写 GPU 时间戳
    pub ts: bool,
}

impl Default for NpuopProps {
    fn default() -> Self {
        NpuopProps { lag: 2, wait_ms: 2.0, budget_ms: 12.0, budget_frames: 30, init_frame: 300, zc: true, ts: true }
    }
}

impl NpuopProps {
    /// 要写的属性 (名, 值), 顺序同 [`NPUOP_TUNING_PROPS`]。`on` = false 时 npuop=0 而调参仍写缺省值。
    pub fn prop_pairs(&self, on: bool) -> Vec<(String, String)> {
        let p = if on { self.clone() } else { NpuopProps::default() };
        let b = |x: bool| if x { "1" } else { "0" };
        let vals = [
            p.lag.to_string(),
            fmt_num(p.wait_ms),
            fmt_num(p.budget_ms),
            p.budget_frames.to_string(),
            p.init_frame.to_string(),
            b(p.zc).to_string(),
            b(p.ts).to_string(),
        ];
        let mut out = vec![(NPUOP_PROP.to_string(), b(on).to_string())];
        out.extend(NPUOP_TUNING_PROPS.iter().zip(vals).map(|(k, v)| (k.to_string(), v)));
        out
    }

    /// `setprop a b; setprop c d; …`
    pub fn setprop_cmd(&self, on: bool) -> String {
        self.prop_pairs(on)
            .into_iter()
            .map(|(k, v)| format!("setprop {k} {v}"))
            .collect::<Vec<_>>()
            .join("; ")
    }
}

/// 属性值里的数字: 整数不带小数点, 否则最多两位小数 (与层 read_num_prop 的 strtod 兼容)。
fn fmt_num(x: f64) -> String {
    if x.fract() == 0.0 {
        format!("{}", x as i64)
    } else {
        let s = format!("{x:.2}");
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    }
}

/// 层 .so 带不带 npuop (按二进制里的属性名字符串判)。
pub(crate) fn so_supports_npuop(bytes: &[u8]) -> bool {
    bytes.windows(NPUOP_SO_SIGNATURE.len()).any(|w| w == NPUOP_SO_SIGNATURE)
}

/// 失败标记: npuop_device 的还原回包里表示「这一步确实失败了」的唯一标记。
pub(crate) const OFF_FAIL_MARK: &str = "警告";

pub struct GrayLayer {
    serial: String,
}

impl GrayLayer {
    pub fn new(serial: &str) -> Self {
        Self { serial: serial.to_string() }
    }

    fn ashell(&self, cmd: &str, timeout: u64) -> String {
        adb(&self.serial, &["shell", cmd], timeout)
    }

    /// adb 输出剥掉尾部的 `#adb_exit=N` 标记。**判定空与否必须用这个**:
    /// cat 失败时 stdout 为空但尾部带着 `#adb_exit=1`, 直接判空会误判成
    /// "marker 出现了" (真机实测: 不存在的包被当成层已加载)。
    pub(crate) fn adb_stdout(out: &str) -> &str {
        match out.rfind("#adb_exit=") {
            Some(i) => &out[..i],
            None => out,
        }
    }

    fn getprop(&self, prop: &str) -> String {
        self.ashell(&format!("getprop {prop}"), 15)
            .trim_end_matches("#adb_exit=0")
            .trim()
            .to_string()
    }

    pub fn status(&self) -> String {
        let mut l = Vec::new();
        l.push(format!(
            "debug.vulkan.layers = [{}]",
            self.getprop("debug.vulkan.layers")
        ));
        for p in [LOADOP_PROP, DUMP_PROP, CP_PROP, UPOP_PROP, FA_PROP, FA_TS_PROP, FA_GO_PROP, STOREOP_PROP, STOREOP_LIVE_PROP, STOREOP_ON_PROP, NPUOP_PROP] {
            l.push(format!("{p}  = [{}]", self.getprop(p)));
        }
        for p in NPUOP_TUNING_PROPS {
            l.push(format!("{p}  = [{}]", self.getprop(p)));
        }
        let st = Self::adb_stdout(
            &self.ashell(&format!("su -c 'cat {STATE} 2>/dev/null'"), 15),
        )
        .trim()
        .to_string();
        if st.is_empty() {
            l.push("state = <无>  (未施加)".into());
        } else {
            let dir = st.split_once('|').map(|x| x.1).unwrap_or("");
            l.push(format!("state = {st}  (处于施加态)"));
            l.push(self.ashell(&format!("su -c 'ls -laZ \"{dir}/{SO}\" 2>&1'"), 15));
        }
        l.join("\n")
    }

    /// Stop only the validated target and deactivate our properties. This is
    /// deliberately narrower than restoring a game or completing a job lease.
    pub fn off(&self, expected_pkg: Option<&str>) -> Result<DeactivationEvidence, DeactivationFailure> {
        deactivate(&self.serial, expected_pkg, |command| self.ashell(command, 30))
    }

}

fn shell_quote(text: &str) -> String {
    format!("'{}'", text.replace('\'', "'\\''"))
}

fn root_command(command: &str) -> String {
    format!("su -c {}", shell_quote(&format!("{command} 2>&1")))
}

pub(crate) fn valid_package(package: &str) -> bool {
    !package.is_empty() && package.split('.').all(|part| {
        !part.is_empty() && part.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
            && part.as_bytes()[0].is_ascii_alphabetic()
    })
}

pub(crate) fn valid_install_dir(dir: &str) -> bool {
    dir.starts_with('/') && dir[1..].split('/').all(|part| {
        !part.is_empty() && part != "." && part != ".."
            && !part.chars().any(|c| c.is_control())
    })
}

fn parse_layer_state(text: &str) -> Result<(String, String), String> {
    let line = text.strip_suffix('\n').unwrap_or(text);
    let (package, dir) = line.split_once('|').ok_or("invalid layer state: expected package|directory")?;
    if !valid_package(package) || !valid_install_dir(dir) || dir.contains('|') || line.contains('\n') {
        return Err("invalid layer state package/directory; preserved".into());
    }
    Ok((package.into(), dir.into()))
}

/// The injected shell keeps the same bounded transport as the existing layer
/// calls; one command journal records both preflight and each checked mutation.
fn deactivate(
    serial: &str,
    expected_pkg: Option<&str>,
    mut shell: impl FnMut(&str) -> String,
) -> Result<DeactivationEvidence, DeactivationFailure> {
    let mut commands = Vec::new();
    let mut package = expected_pkg.map(str::to_owned);
    let result: Result<Option<String>, String> = (|| {
        if expected_pkg.is_some_and(|p| !valid_package(p)) {
            return Err("invalid explicit target package".into());
        }
        let mut run = |command: String| -> Result<String, String> {
            let output = shell(&command);
            let success = adb_ok(&output);
            let body = GrayLayer::adb_stdout(&output).to_owned();
            commands.push(DeactivationCommand { command: command.clone(), output });
            if success { Ok(body) } else { Err(format!("command failed or outcome unknown: {command}")) }
        };
        // Missing state must be proved by a successful parent listing. A failed
        // cat/test cannot distinguish absence from transport or permission loss.
        let listing = run(root_command("ls -1A -- /data/local/tmp"))?;
        let state = if listing.lines().any(|name| name == "knobs_layer_state") {
            Some(parse_layer_state(&run(root_command(&format!("cat {}", shell_quote(STATE))))?)?)
        } else { None };
        if let Some((recorded_package, _)) = &state {
            if expected_pkg.is_some_and(|p| p != recorded_package) {
                return Err("explicit target differs from layer state; no cleanup performed".into());
            }
            package = Some(recorded_package.clone());
        }
        let target = package.as_deref().ok_or("no layer state or explicit target; cannot verify a target process stopped")?;
        let layer = run("getprop debug.vulkan.layers 2>&1".into())?;
        if !matches!(layer.trim(), "" | LAYER_NAME) {
            return Err("debug.vulkan.layers contains a foreign layer; no cleanup performed".into());
        }
        let installation = if let Some((_, dir)) = &state {
            let dump = run(format!("dumpsys package {} 2>&1", shell_quote(target)))?;
            let native: Vec<_> = dump.lines().filter_map(|line| {
                line.trim().strip_prefix("legacyNativeLibraryDir=")
            }).collect();
            if native.len() != 1 || !valid_install_dir(native[0]) || format!("{}/arm64", native[0]) != *dir {
                return Err("recorded installation differs from the current target native library directory; preserved".into());
            }
            run(root_command(&format!("ls -1A -- {}", shell_quote(dir))))?;
            Some(format!("{dir}/{SO}"))
        } else { None };

        // All target/path/global-layer checks precede the first device write.
        run(format!("am force-stop {} 2>&1", shell_quote(target)))?;
        let processes = run(root_command("ps -A -o NAME"))?;
        let mut names = processes.lines().map(str::trim).filter(|line| !line.is_empty());
        if names.next() != Some("NAME") {
            return Err("unrecognized process-name readback".into());
        }
        if names.any(|name| name == target || name.starts_with(&format!("{target}:"))) {
            return Err("target process remains after force-stop".into());
        }
        for property in LAYER_PROPERTIES {
            let value = if property == "debug.vulkan.layers" { "" } else { "0" };
            run(root_command(&format!("setprop {property} {}", shell_quote(value))))?;
            if run(format!("getprop {property} 2>&1"))?.trim() != value {
                return Err(format!("property readback differs after deactivation: {property}"));
            }
        }
        if let Some(path) = &installation {
            run(root_command(&format!("rm -f -- {}", shell_quote(path))))?;
            let dir = &state.as_ref().unwrap().1;
            let listing = run(root_command(&format!("ls -1A -- {}", shell_quote(dir))))?;
            if listing.lines().any(|name| name == SO) {
                return Err("recorded layer installation remains after deletion".into());
            }
            // Preserve the only installation record until earlier checks pass.
            run(root_command(&format!("rm -f -- {}", shell_quote(STATE))))?;
        }
        let listing = run(root_command("ls -1A -- /data/local/tmp"))?;
        if listing.lines().any(|name| name == "knobs_layer_state") {
            return Err("layer state still exists or appeared during deactivation".into());
        }
        Ok(installation)
    })();
    match result {
        Ok(installation) => Ok(DeactivationEvidence {
            scope: DEACTIVATION_SCOPE, serial: serial.into(), package: package.unwrap(),
            recorded_installation_absent: installation.as_ref().map(|_| true),
            recorded_installation: installation, state_absent: true, processes_absent: true,
            properties_disabled: true, original_properties_restored: false,
            unregistered_installations_verified: false, game_state_restored: false,
            job_recovery_complete: false, commands,
        }),
        Err(error) => Err(DeactivationFailure {
            scope: DEACTIVATION_SCOPE, serial: serial.into(), package, error, game_state_restored: false,
            job_recovery_complete: false, commands,
        }),
    }
}

/// 层 .so 的解析: knobs/gray/build/out/ (build_layer.sh 的产物位)。
pub(crate) fn layer_so_path() -> String {
    match resolve_path("knobs/gray/build/out/libVkLayer_refknobs.so") {
        Some(p) => p.to_string_lossy().into_owned(),
        None => "knobs/gray/build/out/libVkLayer_refknobs.so".into(),
    }
}

#[derive(Debug, PartialEq)]
pub(crate) enum GrayCliAdmission { Proceed, Help, Reject(Value) }

#[derive(Default)]
struct LayerArgs {
    positional: Vec<String>,
    serial: Option<String>,
    so: Option<String>,
    json: bool,
    help: bool,
}

fn layer_args(args: &[String]) -> Result<LayerArgs, String> {
    let mut parsed = LayerArgs::default();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--serial" | "--so" => {
                let value = it.next().ok_or_else(|| format!("{a} requires a value"))?.clone();
                if a == "--serial" { parsed.serial = Some(value); } else { parsed.so = Some(value); }
            }
            "--keep-prop" => {}, // Accepted only to explain the retired entry point.
            "--json" => parsed.json = true,
            "--help" | "-h" => parsed.help = true,
            other if other.starts_with('-') => return Err(format!("unknown gray-layer option {other}")),
            _ => parsed.positional.push(a.clone()),
        }
    }
    let restore = parsed.positional.first().is_some_and(|m| m == "npuop-restore");
    if parsed.positional.len() > if restore { 3 } else { 2 } { return Err("gray-layer accepts one mode and at most one package".into()); }
    if let Some(mode) = parsed.positional.first() {
        if !matches!(mode.as_str(), "probe" | "loadop" | "passdump" | "copyprobe" | "upop" | "npuop" | "frameaudit" | "frameaudit-ts" | "target" | "status" | "off" | "npuop-restore") {
            return Err(format!("unknown gray-layer mode {mode}"));
        }
        if mode == "status" && parsed.positional.len() > 1 { return Err("status does not accept a package".into()); }
        if !parsed.help && mode == "target" && parsed.positional.len() < 2 { return Err("target requires a package".into()); }
        if !parsed.help && restore && parsed.positional.len() < 2 { return Err("npuop-restore requires a device run directory".into()); }
    } else if !parsed.help { return Err("gray-layer requires a mode".into()); }
    Ok(parsed)
}

/// Pure argument admission before the legacy queue gate. Consume values exactly
/// as execution does, including values named --help or --no-devlock.
pub(crate) fn cli_admission(sub: &str, args: &[String]) -> Result<GrayCliAdmission, String> {
    if sub == "gray-ab" && args.first().is_some_and(|a| a == "recover") {
        return Ok(GrayCliAdmission::Proceed); // recovery has its own parser and Lease
    }
    let mut stripped = vec![sub.to_owned()];
    stripped.extend_from_slice(args);
    crate::devlock::strip_lock_args(&mut stripped)?;
    let args = &stripped[1..];
    if sub == "gray-layer" {
        if crate::gray_refbench::handles(args) { return crate::gray_refbench::admission(args); }
        if crate::gray_control::handles(args) { return crate::gray_control::admission(args); }
        let parsed = layer_args(args)?;
        if parsed.help { return Ok(GrayCliAdmission::Help); }
        return Ok(mode_admission_rejection(&parsed.positional[0], parsed.so.is_some())
            .or_else(|| legacy_mount_rejection(&parsed.positional[0]))
            .map_or(GrayCliAdmission::Proceed, GrayCliAdmission::Reject));
    }
    let mut mode = "loadop";
    let mut help = false;
    let mut out = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--mode" | "--serial" | "--out" | "--pairs" | "--evidence-origin" => {
                let value = it.next().ok_or_else(|| format!("{a} requires a value"))?;
                match a.as_str() {
                    "--mode" => mode = value,
                    "--out" => out = Some(value),
                    "--pairs" if value.parse::<u32>().ok().filter(|n| *n > 0).is_none() => return Err("--pairs requires a positive integer".into()),
                    "--evidence-origin" if !matches!(value.as_str(), "device" | "synthetic") => return Err("--evidence-origin requires device or synthetic".into()),
                    _ => {},
                }
            }
            "--json" => {},
            "--help" | "-h" => help = true,
            other => return Err(format!("unknown gray-ab option {other}")),
        }
    }
    if !matches!(mode, "loadop" | "copyprobe") { return Err(format!("unknown gray-ab mode {mode}")); }
    if help { return Ok(GrayCliAdmission::Help); }
    if let Some(rejection) = mode_admission_rejection(mode, false) { return Ok(GrayCliAdmission::Reject(rejection)); }
    if out.is_none_or(|v| v.is_empty()) { return Err("gray-ab requires --out".into()); }
    Ok(GrayCliAdmission::Proceed)
}

pub fn run_gray_layer(args: &[String]) -> i32 {
    if crate::gray_refbench::handles(args) { return crate::gray_refbench::run(args); }
    if crate::gray_control::handles(args) { return crate::gray_control::run(args); }
    let parsed = match layer_args(args) {
        Ok(parsed) => parsed,
        Err(error) => { eprintln!("{error}\n{USAGE}"); return 2; }
    };
    if parsed.help { println!("{USAGE}"); return 0; }
    let LayerArgs { positional, serial, so, json: json_output, .. } = parsed;
    let mode = positional[0].clone();
    if let Some(rejection) = mode_admission_rejection(&mode, so.is_some()).or_else(|| legacy_mount_rejection(&mode)) {
        println!("{rejection}");
        return 2;
    }
    let mut serial = serial.unwrap_or_else(|| std::env::var("REFBENCH_SERIAL")
        .unwrap_or_else(|_| std::env::var("SERIAL").unwrap_or_else(|_| "91253241019A".into())));
    // Status is a read-only plan/status path and does not interrupt the lease owner.
    let _lease = if mode == "status" { None } else {
        match crate::devlease::Lease::target(Some(&serial), "gray-layer") {
            Ok(v) => { serial = v.serial.clone(); Some(v) },
            Err(e) => { eprintln!("{e}"); return 2; }
        }
    };
    if adb_path().is_empty() {
        eprintln!("找不到 adb");
        return 2;
    }
    let gl = GrayLayer::new(&serial);
    match mode.as_str() {
        "status" => {
            println!("{}", gl.status());
            0
        }
        "off" => {
            let result = gl.off(positional.get(1).map(String::as_str));
            if json_output {
                println!("{}", serde_json::to_string_pretty(&deactivation_json(&result)).unwrap());
            } else {
                match &result {
                    Ok(evidence) => println!("{evidence}"),
                    Err(error) => eprintln!("{error}"),
                }
            }
            if result.is_ok() { 0 } else { 1 }
        }
        // 旧 eval 的 npuop 评测中途断掉 (进程被杀) 时手动还原: 摘层 + umount + 属性设回运行前
        "npuop-restore" => {
            let run = &positional[1];
            let pkg = positional.get(2).map(String::as_str).unwrap_or("com.miHoYo.Yuanshen");
            let off = gl.off(Some(pkg));
            match &off {
                Ok(evidence) => println!("{evidence}"),
                Err(error) => eprintln!("{error}"),
            }
            match crate::npuop_device::NpuopMount::from_device(&serial, pkg, run) {
                Ok(m) => {
                    let log = m.restore();
                    println!("{log}");
                    if crate::npuop_device::restore_clean(&log) && off.is_ok() { 0 } else { 1 }
                }
                Err(e) => {
                    eprintln!("{e}");
                    2
                }
            }
        }
        other => {
            eprintln!("不认识的模式 {other:?}\n{USAGE}");
            2
        }
    }
}

/// Called only after admission has accepted these arguments. Values that look
/// like flags must not change whether a device owner enters the queue.
pub(crate) fn needs_queue(sub: &str, args: &[String]) -> bool {
    let mut stripped = vec![sub.to_owned()];
    stripped.extend_from_slice(args);
    if crate::devlock::strip_lock_args(&mut stripped).is_err() { return true; }
    if sub == "gray-layer" && crate::gray_control::uses_device_binding(&stripped[1..]) { return false; }
    !matches!((sub, stripped.get(1).map(String::as_str)),
        ("gray-ab", Some("recover")) |
        ("gray-layer", Some("session-status" | "session-action" | "session-recover" | "refbench-recover")))
}

#[cfg(test)]
mod mode_admission_tests {
    use super::*;

    fn admission(sub: &str, args: &[&str]) -> Result<GrayCliAdmission, String> {
        cli_admission(sub, &args.iter().map(|s| s.to_string()).collect::<Vec<_>>())
    }

    #[test]
    fn gray_admission_consumes_values_and_uses_last_mode() {
        assert_eq!(admission("gray-ab", &["--mode", "copyprobe", "--mode", "loadop", "--out", "upop", "--serial", "--help"]).unwrap(), GrayCliAdmission::Proceed);
        let GrayCliAdmission::Reject(rejected) = admission("gray-ab", &["--mode", "loadop", "--mode", "copyprobe", "--serial", "--no-devlock", "--lock-label", "--help", "--lock-wait", "0s"]).unwrap() else { panic!("last mode was not rejected"); };
        assert_eq!(rejected["requested_mode"], "copyprobe");
        for args in [vec!["loadop", "--serial", "copyprobe", "--so", "--help"], vec!["loadop", "--serial", "--help"]] {
            let GrayCliAdmission::Reject(rejected) = admission("gray-layer", &args).unwrap() else { panic!("value was treated as help"); };
            assert_eq!(rejected["status"], "retired");
        }
        assert_eq!(admission("gray-layer", &["copyprobe", "--help"]).unwrap(), GrayCliAdmission::Help);
        assert_eq!(admission("gray-ab", &["--mode", "copyprobe", "--help"]).unwrap(), GrayCliAdmission::Help);
        assert_eq!(admission("gray-ab", &["recover", "--session", "old", "--serial", "S"]).unwrap(), GrayCliAdmission::Proceed);
        for (sub, args) in [
            ("gray-layer", vec!["upop", "--so"]),
            ("gray-layer", vec!["copyprobe", "--unknown"]),
            ("gray-ab", vec!["--mode", "copyprobe", "--out"]),
            ("gray-ab", vec!["--mode", "copyprobe", "--so", "custom.so"]),
        ] { assert!(admission(sub, &args).is_err(), "accepted {sub} {args:?}"); }
    }

    #[test]
    fn unsupported_modes_reject_before_device_or_artifact_access() {
        for mode in ["copyprobe", "upop"] {
            for custom in [false, true] {
                let mut args = vec![mode, "--serial", "nonexistent-admission-device"];
                if custom { args.extend(["--so", "/nonexistent/admission.so"]); }
                let GrayCliAdmission::Reject(actual) = admission("gray-layer", &args).unwrap() else { panic!("unsupported mode accepted"); };
                assert_eq!(actual, mode_admission_rejection(mode, custom).unwrap());
                assert_eq!(actual["schema"], "phonefarm/gray-mode-admission@1");
                assert_eq!(actual["artifact_source"], if custom { "custom" } else { "default" });
                assert_eq!(actual["reason"], if custom { "artifact_capability_unverified" } else { "missing_source_layout_target_compute_contract" });
                assert_eq!(actual["device_commands_started"], false);
                assert_eq!(actual["recovery_status"], "not_started");
            }
        }
        assert!(mode_admission_rejection("loadop", false).is_none());
    }

    #[test]
    fn legacy_mount_retirement_does_not_disable_owned_sessions() {
        for mode in ["probe", "loadop", "passdump", "frameaudit", "frameaudit-ts", "target"] {
            let GrayCliAdmission::Reject(rejected) = admission("gray-layer", &[mode, "com.example.fixture", "--keep-prop", "--so", "/unavailable.so"]).unwrap() else { panic!("legacy mount accepted"); };
            assert_eq!(rejected, legacy_mount_rejection(mode).unwrap());
            assert_eq!(rejected["status"], "retired");
            assert_eq!(rejected["device_commands_started"], false);
            assert!(mode_admission_rejection(mode, true).is_none());
            assert_eq!(admission("gray-layer", &["session", "--mode", mode, "--package", "com.example.fixture", "--serial", "S", "--out", "unused", "--keep-prop"]).unwrap(), GrayCliAdmission::Proceed);
        }
        assert_eq!(admission("gray-layer", &["status", "--serial", "S"]).unwrap(), GrayCliAdmission::Proceed);
        assert_eq!(admission("gray-layer", &["off", "com.example.fixture", "--serial", "S", "--json"]).unwrap(), GrayCliAdmission::Proceed);
        assert_eq!(admission("gray-layer", &["refbench-loadop", "--serial", "S", "--out", "unused"]).unwrap(), GrayCliAdmission::Proceed);
    }

    #[test]
    fn control_clients_do_not_queue_behind_their_owner() {
        let args = |a: &[&str]| a.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        for command in ["session-status", "session-action", "session-recover", "refbench-recover"] {
            assert!(!needs_queue("gray-layer", &args(&["--lock-label", "control", command, "--out", "--help"])));
        }
        assert!(needs_queue("gray-layer", &args(&["session", "--mode", "passdump", "--serial", "--help", "--out", "--no-devlock"])));
        let mut original = args(&["gray-layer", "session-action", "--request", "--no-devlock", "--out", "--help"]);
        crate::devlock::strip_lock_args(&mut original).unwrap();
        assert_eq!(original, args(&["gray-layer", "session-action", "--request", "--no-devlock", "--out", "--help"]));
        let mut power = args(&["gray-layer", "session", "--power-policy", "--no-devlock"]);
        crate::devlock::strip_lock_args(&mut power).unwrap();
        assert_eq!(power, args(&["gray-layer", "session", "--power-policy", "--no-devlock"]));
        assert!(crate::gray_control::admission(&args(&["session", "--mode", "frameaudit", "--serial", "S", "--package", "com.example.fixture", "--out", "unused", "--power-policy", "unknown"])).is_err());
        let mut refbench = args(&["gray-layer", "refbench-loadop", "--frames", "--no-devlock"]);
        crate::devlock::strip_lock_args(&mut refbench).unwrap();
        assert!(cli_admission("gray-layer", &refbench[1..]).is_err());
        assert_eq!(admission("gray-layer", &["refbench-loadop", "--serial", "S", "--out", "unused", "--frames", "60"]).unwrap(), GrayCliAdmission::Proceed);
        assert!(admission("gray-layer", &["refbench-loadop", "--serial", "S", "--out", "unused", "--frames", "1"]).is_err());
        assert_eq!(admission("gray-layer", &["refbench-recover", "--serial", "S", "--session", "old"]).unwrap(), GrayCliAdmission::Proceed);
    }
}

#[cfg(test)]
mod launch_tests {
    use super::GrayLayer;

    /// resolve-activity 的真实输出 (2026-10-11 NX809J) 加上 adb() 追加的退出码标记
    #[test]
    fn activity_line_survives_exit_marker() {
        let raw = "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=false\ncom.miHoYo.Yuanshen/com.miHoYo.GetMobileInfo.MainActivity\n#adb_exit=0";
        let act = GrayLayer::adb_stdout(raw).lines().rev().find(|l| !l.trim().is_empty()).unwrap();
        assert_eq!(act.trim(), "com.miHoYo.Yuanshen/com.miHoYo.GetMobileInfo.MainActivity");
    }
}

#[cfg(test)]
mod deactivation_tests {
    use super::*;

    #[test]
    fn state_is_one_target_and_an_absolute_directory() {
        assert_eq!(parse_layer_state("com.example.game|/data/app/~~id/pkg/lib/arm64\n").unwrap(),
            ("com.example.game".into(), "/data/app/~~id/pkg/lib/arm64".into()));
        for invalid in ["", "com.example.game|relative/path", "com.example.game|/data/../app",
            "com.example.game|/data/app\nother|/data/other", "com.example.game;id|/data/app"] {
            assert!(parse_layer_state(invalid).is_err(), "accepted {invalid:?}");
        }
        assert_eq!(shell_quote("a'b"), "'a'\\''b'");
    }

    #[test]
    fn failed_read_and_conflicting_preflight_never_start_cleanup() {
        for fault in ["listing", "target", "foreign_layer", "installation"] {
            let result = deactivate("fixture-serial", Some("com.example.game"), |command| {
                let body = if command.contains("ls -1A -- /data/local/tmp") {
                    if fault == "listing" { return "Permission denied\n#adb_exit=1".into(); }
                    "knobs_layer_state\n"
                } else if command.contains("cat ") {
                    if fault == "target" { "com.example.other|/data/app/pkg/lib/arm64\n" }
                    else { "com.example.game|/data/app/pkg/lib/arm64\n" }
                } else if command.starts_with("getprop ") {
                    if fault == "foreign_layer" { "VK_LAYER_other\n" } else { "\n" }
                } else if command.starts_with("dumpsys ") {
                    "legacyNativeLibraryDir=/data/app/new-version/lib\n"
                } else { panic!("unexpected mutation or command after failed preflight: {command}"); };
                format!("{body}#adb_exit=0")
            });
            let failure = result.unwrap_err();
            assert!(!failure.commands.is_empty());
            assert!(failure.commands.iter().all(|c| !c.command.contains("force-stop")
                && !c.command.contains("setprop") && !c.command.contains("rm -f")));
            assert!(!failure.job_recovery_complete);
            if fault == "listing" {
                assert_eq!(failure.commands[0].output, "Permission denied\n#adb_exit=1");
            }
        }
    }
}

#[cfg(test)]
mod storeop_tests {
    use super::storeop_spec;

    #[test]
    fn spec_joins_sorted_unique_items() {
        let s = storeop_spec(&[
            ("15A51A879DF99C32".into(), 2),
            ("9d0b4db80bf552b8".into(), 0),
            ("15a51a879df99c32".into(), 2),
        ])
        .unwrap();
        assert_eq!(s, "15a51a879df99c32:2,9d0b4db80bf552b8:0");
        assert_eq!(storeop_spec(&[]).unwrap(), "0");
    }

    #[test]
    fn short_or_non_hex_sigs_and_overlong_specs_are_refused() {
        assert!(storeop_spec(&[("1234567".into(), 0)]).is_err());
        assert!(storeop_spec(&[("zzzzzzzz".into(), 0)]).is_err());
        let many: Vec<(String, u32)> = (0..6).map(|i| ("0123456789abcdef".to_string(), i)).collect();
        assert!(storeop_spec(&many).unwrap_err().contains("上限"));
    }
}

#[cfg(test)]
mod npuop_props_tests {
    use super::{so_supports_npuop, NpuopProps, NPUOP_PROP, NPUOP_TUNING_PROPS};

    /// 每一臂都显式写全部 8 个属性: 开的时候是请求的调参, 关的时候 npuop=0 且调参回缺省。
    #[test]
    fn every_npuop_prop_is_written_on_and_off() {
        let p = NpuopProps { lag: 1, wait_ms: 2.5, budget_ms: 10.0, budget_frames: 20, init_frame: 200, zc: false, ts: true };
        let on = p.prop_pairs(true);
        assert_eq!(on.len(), 1 + NPUOP_TUNING_PROPS.len());
        assert_eq!(on[0], (NPUOP_PROP.to_string(), "1".to_string()));
        let get = |k: &str| on.iter().find(|(a, _)| a == k).map(|(_, v)| v.clone()).unwrap();
        assert_eq!(get("debug.knobs.npuop_lag"), "1");
        assert_eq!(get("debug.knobs.npuop_wait_ms"), "2.5");
        assert_eq!(get("debug.knobs.npuop_budget_ms"), "10");
        assert_eq!(get("debug.knobs.npuop_zc"), "0");
        let off = p.prop_pairs(false);
        assert_eq!(off[0].1, "0");
        assert_eq!(off, NpuopProps::default().prop_pairs(false));
        let cmd = p.setprop_cmd(true);
        assert!(cmd.starts_with("setprop debug.knobs.npuop 1; setprop debug.knobs.npuop_lag 1"), "{cmd}");
        assert!(!cmd.contains('\n'));
    }

    #[test]
    fn layer_so_is_recognised_by_its_prop_string() {
        assert!(so_supports_npuop(b"\x7fELF....debug.knobs.npuop_wait_ms\0..."));
        assert!(!so_supports_npuop(b"\x7fELF....debug.knobs.upop\0..."));
    }
}
