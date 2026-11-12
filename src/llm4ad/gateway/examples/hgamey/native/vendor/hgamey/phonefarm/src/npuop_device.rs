//! npuop (NPU 超分) 的研究机布置: `_tmp/npu/N1/ondevice.sh` 第 1–5 步与还原的 Rust 移植,
//! 给 `eval` 的 kind=gray `npuop` 开关复用。
//!
//! 层 (npu/n1-pipeline) 在原神进程里要 dlopen TFLite + QNN 运行库和模型, 这些文件必须在
//! 游戏自己的 nativeLibraryDir 里。研究机上的做法 (PR #60, N1): **游戏的 lib 目录一字不动**,
//! 在 `/data/local/tmp/npuop/run_<时间>/libdir` 里放「原 lib 目录的副本 + 层 + 运行库 + 模型」,
//! 再用 `su -mm mount --bind` 盖到游戏 lib 目录上; 还原 = umount, 原目录自动回来。
//! 副本目录留在手机上不删 (登记在 `/data/local/tmp/npuop/README.txt`)。
//!
//! 另外两处绕过: SELinux 只在内存里放行 appdomain 对 vendor_qdsp_device 的 open
//! (`magiskpolicy --live`, 重启消失, 不撤销 —— deny 会连系统原有的同类权限一起删);
//! 层不开线程 (推理在 present 里同步做), 不需要操作。
//!
//! 纯函数 (文件名、冲突、属性解析、清单核对) 单测; 碰设备的部分只在 eval 里走。

use crate::eval::{adb, adb_ok, resolve_path, su};
use crate::graylayer::{so_supports_npuop, OFF_FAIL_MARK, SO};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

/// 手机上的落点 (只新建, 不删)。
pub(crate) const DEV_BASE: &str = "/data/local/tmp/npuop";
/// 模型在手机上的名字: .tflite 改名成 lib*.so (只有 lib*.so 会被安装器保留权限; 层按这个名字 dlopen)。
pub(crate) const MODEL_DEV_NAME: &str = "libnpusr_model.so";
/// 层 dlopen 的运行库 (TFLite + QNN HTP V81 + libvndksupport 替身), 按文件名在 runtime 目录里找。
pub(crate) const RUNTIME_LIBS: [&str; 9] = [
    "libtensorflowlite_jni.so",
    "libQnnTFLiteDelegate.so",
    "libQnnHtp.so",
    "libQnnHtpV81Stub.so",
    "libQnnSystem.so",
    "libQnnHtpPrepare.so",
    "libQnnHtpV81Skel.so",
    "libQnnHtpV81.so",
    "libvndksupport.so",
];
/// libcdsprpc 及其依赖: 从手机自己的 /vendor/lib64 复制 (与当前 ROM 一致)。
pub(crate) const VENDOR_LIBS: [&str; 12] = [
    "libcdsprpc.so",
    "libc++.so",
    "libvmmem.so",
    "libbase.so",
    "libdmabufheap.so",
    "libcutils.so",
    "vendor.qti.hardware.dsp-V1-ndk.so",
    "android.hardware.common-V2-ndk.so",
    "vendor.qti.hardware.dsp@1.0.so",
    "libutils.so",
    "libhidlbase.so",
    "libhardware.so",
];
/// 布置前记下、还原时逐个设回的属性 (与 N1 的 PROPS 一致)。
pub(crate) const SAVED_PROPS: [&str; 18] = [
    "debug.vulkan.layers",
    "debug.knobs.loadop",
    "debug.knobs.passdump",
    "debug.knobs.copyprobe",
    "debug.knobs.upop",
    "debug.knobs.frameaudit",
    "debug.knobs.frameaudit_ts",
    "debug.knobs.storeop",
    "debug.knobs.npuprobe",
    "debug.knobs.npuop",
    "debug.knobs.npuop_lag",
    "debug.knobs.npuop_wait_ms",
    "debug.knobs.npuop_budget_ms",
    "debug.knobs.npuop_budget_frames",
    "debug.knobs.npuop_init_frame",
    "debug.knobs.npuop_zc",
    "debug.knobs.npuop_ts",
    "debug.knobs.storeop_live",
];
/// 环境变量: 覆盖本机文件的查找。
pub(crate) const ENV_LAYER_SO: &str = "HGAMEY_NPUOP_LAYER_SO";
pub(crate) const ENV_MODEL: &str = "HGAMEY_NPUOP_MODEL";
pub(crate) const ENV_RUNTIME_DIR: &str = "HGAMEY_NPUOP_RUNTIME_DIR";
pub(crate) const ENV_TOOLS_DIR: &str = "HGAMEY_TOOLS_DIR";
/// lib 目录副本之外 /data 还要剩这么多 (KB)。
const FREE_MARGIN_KB: u64 = 400_000;

/// 请求里 npuop 的文件参数 (`rewrites[].args`); 都可省, 省了按环境变量 / 缺省位置找。
#[derive(Debug, Clone, Default, PartialEq)]
pub struct NpuopFileArgs {
    pub layer_so: Option<String>,
    pub model: Option<String>,
    pub runtime_dir: Option<String>,
}

/// 本机上定位好的三样东西。
#[derive(Debug, Clone, PartialEq)]
pub struct NpuopFiles {
    pub layer_so: PathBuf,
    pub model: PathBuf,
    /// 顺序同 [`RUNTIME_LIBS`]
    pub runtime: Vec<PathBuf>,
}

impl NpuopFiles {
    /// 定位并核验 (都在碰设备之前): 层 .so 必须带 npuop; 模型必须是 .tflite; 9 个运行库一个不能少。
    pub fn locate(args: &NpuopFileArgs) -> Result<Self, String> {
        let layer_so = pick_path(args.layer_so.as_deref(), ENV_LAYER_SO)
            .or_else(|| resolve_path("knobs/gray/build/out/libVkLayer_refknobs.so"))
            .ok_or("找不到层 .so: 给 args.layer_so, 或设 HGAMEY_NPUOP_LAYER_SO, 或先跑 knobs/gray/build/build_layer.sh")?;
        let bytes = std::fs::read(&layer_so).map_err(|e| format!("读不了层 {}: {e}", layer_so.display()))?;
        if !so_supports_npuop(&bytes) {
            return Err(format!(
                "层 {} 里没有 npuop (二进制里没有 debug.knobs.npuop_wait_ms): 要用 npu/n1-pipeline 分支 R2F 之后构建的层",
                layer_so.display()
            ));
        }
        let model = match pick_path(args.model.as_deref(), ENV_MODEL) {
            Some(m) => m,
            None => newest_model(&tools_dirs()).ok_or(
                "找不到模型: 给 args.model (.tflite), 或设 HGAMEY_NPUOP_MODEL, 或把 genshin_sr_v2_*.tflite 放进 tools/qairt/models",
            )?,
        };
        if model.extension().and_then(|e| e.to_str()) != Some("tflite") {
            return Err(format!("模型 {} 不是 .tflite", model.display()));
        }
        if !model.is_file() {
            return Err(format!("模型 {} 不存在", model.display()));
        }
        let runtime = match pick_path(args.runtime_dir.as_deref(), ENV_RUNTIME_DIR) {
            Some(dir) => runtime_from_flat_dir(&dir)?,
            None => runtime_from_tools(&tools_dirs())?,
        };
        Ok(NpuopFiles { layer_so, model, runtime })
    }

    /// 推到手机上的 (本机路径, 手机上的文件名): 层固定叫 libVkLayer_refknobs.so, 模型叫 libnpusr_model.so。
    pub fn device_names(&self) -> Vec<(PathBuf, String)> {
        let mut v = vec![(self.layer_so.clone(), SO.to_string()), (self.model.clone(), MODEL_DEV_NAME.to_string())];
        v.extend(self.runtime.iter().map(|p| (p.clone(), file_name(p))));
        v
    }

    pub fn to_json(&self) -> Value {
        json!({
            "layer_so": self.layer_so.display().to_string(),
            "model": self.model.display().to_string(),
            "runtime": self.runtime.iter().map(|p| p.display().to_string()).collect::<Vec<_>>(),
        })
    }
}

fn file_name(p: &Path) -> String {
    p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
}

fn pick_path(given: Option<&str>, env: &str) -> Option<PathBuf> {
    let s = given.map(String::from).or_else(|| std::env::var(env).ok())?;
    let s = s.trim();
    if s.is_empty() {
        return None;
    }
    resolve_path(s).or_else(|| Some(PathBuf::from(s)))
}

/// 可能放着 `tools/` (qairt SDK、npu-handoff) 的目录: HGAMEY_TOOLS_DIR, 以及仓库根往上两三级的 tools/。
fn tools_dirs() -> Vec<PathBuf> {
    let mut v = Vec::new();
    if let Ok(d) = std::env::var(ENV_TOOLS_DIR) {
        if !d.trim().is_empty() {
            v.push(PathBuf::from(d.trim()));
        }
    }
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().map(Path::to_path_buf);
    for up in [2usize, 3] {
        let mut d = root.clone();
        for _ in 0..up {
            d = d.and_then(|p| p.parent().map(Path::to_path_buf));
        }
        if let Some(d) = d {
            v.push(d.join("tools"));
        }
    }
    v
}

/// tools/qairt/models 下最新的 genshin_sr_v2_*.tflite。
fn newest_model(tools: &[PathBuf]) -> Option<PathBuf> {
    let mut best: Option<(std::time::SystemTime, PathBuf)> = None;
    for t in tools {
        let Ok(rd) = std::fs::read_dir(t.join("qairt").join("models")) else { continue };
        for e in rd.flatten() {
            let n = e.file_name().to_string_lossy().into_owned();
            if !(n.starts_with("genshin_sr_v2_") && n.ends_with(".tflite")) {
                continue;
            }
            let m = e.metadata().and_then(|m| m.modified()).ok()?;
            if best.as_ref().is_none_or(|(t, _)| m > *t) {
                best = Some((m, e.path()));
            }
        }
    }
    best.map(|(_, p)| p)
}

/// 一个扁平目录里放齐 9 个运行库。
fn runtime_from_flat_dir(dir: &Path) -> Result<Vec<PathBuf>, String> {
    let mut out = Vec::new();
    for n in RUNTIME_LIBS {
        let p = dir.join(n);
        if !p.is_file() {
            return Err(format!("运行库目录 {} 里缺 {n}", dir.display()));
        }
        out.push(p);
    }
    Ok(out)
}

/// N1 的布局: tools/npu-handoff/{lib,stub} + tools/qairt/sdk/qairt/<版本>/lib/{aarch64-android,hexagon-v81/unsigned}。
fn runtime_from_tools(tools: &[PathBuf]) -> Result<Vec<PathBuf>, String> {
    for t in tools {
        let ho = t.join("npu-handoff");
        let Some(qnn) = newest_qairt_lib(&t.join("qairt").join("sdk").join("qairt")) else { continue };
        let at = |n: &str| -> PathBuf {
            match n {
                "libtensorflowlite_jni.so" => ho.join("lib").join(n),
                "libvndksupport.so" => ho.join("stub").join(n),
                "libQnnHtpV81Skel.so" | "libQnnHtpV81.so" => qnn.join("hexagon-v81").join("unsigned").join(n),
                _ => qnn.join("aarch64-android").join(n),
            }
        };
        let paths: Vec<PathBuf> = RUNTIME_LIBS.iter().map(|n| at(n)).collect();
        if paths.iter().all(|p| p.is_file()) {
            return Ok(paths);
        }
    }
    Err(format!(
        "找不到 NPU 运行库 ({}): 给 args.runtime_dir (扁平目录放齐这 9 个), 或设 HGAMEY_NPUOP_RUNTIME_DIR / HGAMEY_TOOLS_DIR",
        RUNTIME_LIBS.join(", ")
    ))
}

fn newest_qairt_lib(sdk: &Path) -> Option<PathBuf> {
    let mut vers: Vec<PathBuf> = std::fs::read_dir(sdk).ok()?.flatten().map(|e| e.path()).filter(|p| p.join("lib").is_dir()).collect();
    vers.sort();
    vers.pop().map(|p| p.join("lib"))
}

/// 要放进 lib 目录副本的全部文件名 (推的 + 从 /vendor 复制的)。与游戏自己的库重名就停, 绝不覆盖。
pub(crate) fn name_conflicts(pre_ls: &[String], names: &[String]) -> Vec<String> {
    names.iter().chain(VENDOR_LIBS.iter().map(|s| *s).map(String::from).collect::<Vec<_>>().iter()).filter(|n| pre_ls.iter().any(|x| x == *n)).cloned().collect::<std::collections::BTreeSet<_>>().into_iter().collect()
}

/// `k=v` 行 → (k, v); 空键跳过, 值可以为空。
pub(crate) fn parse_props(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter_map(|l| {
            let (k, v) = l.trim().split_once('=')?;
            let k = k.trim();
            (!k.is_empty()).then(|| (k.to_string(), v.trim().to_string()))
        })
        .collect()
}

/// 还原属性的 shell: 逐个设回运行前的值 (原来为空就设空; Android 属性不能真正删除)。
pub(crate) fn restore_props_cmd(pre: &[(String, String)]) -> String {
    pre.iter().map(|(k, v)| format!("setprop {k} \"{v}\"")).collect::<Vec<_>>().join("; ")
}

/// 两份 `ls` 清单一样 (不看顺序)。
pub(crate) fn listing_matches(a: &[String], b: &[String]) -> bool {
    let mut x: Vec<&str> = a.iter().map(String::as_str).filter(|s| !s.is_empty()).collect();
    let mut y: Vec<&str> = b.iter().map(String::as_str).filter(|s| !s.is_empty()).collect();
    x.sort();
    y.sort();
    x == y
}

fn lines_of(out: &str) -> Vec<String> {
    strip(out).lines().map(|l| l.trim().to_string()).filter(|l| !l.is_empty()).collect()
}

/// su() 输出尾部的 `#adb_exit=N` 剥掉。
fn strip(out: &str) -> &str {
    match out.rfind("#adb_exit=") {
        Some(i) => &out[..i],
        None => out,
    }
}

/// 布置好的状态 (还原要用的全部东西)。
#[derive(Debug, Clone)]
pub struct NpuopMount {
    pub serial: String,
    pub pkg: String,
    /// 手机上这一轮的目录 `/data/local/tmp/npuop/run_<时间>`
    pub run_dir: String,
    /// 游戏的 nativeLibraryDir (被盖住的那个)
    pub libdir: String,
    pub pre_props: Vec<(String, String)>,
    pub pre_ls: Vec<String>,
    pub files: Value,
}

impl NpuopMount {
    /// 布置: 预检 → 推文件 → 拼副本目录 → 记属性 → magiskpolicy → bind mount → 核对层可见。
    /// 任何一步失败都如实返回; 失败前已 mount 的会在错误里提示用 restore。
    pub fn setup(serial: &str, pkg: &str, files: &NpuopFiles, evidence: &Path) -> Result<Self, String> {
        let ash = |c: &str, t: u64| adb(serial, &["shell", c], t);
        let sush = |c: &str, t: u64| su(serial, c, t);
        // 全局挂载命名空间 (Magisk su -mm): 挂在这里, 之后由 zygote 新起的游戏进程才看得见
        let summ = |c: &str, t: u64| adb(serial, &["shell", &format!("su -mm -c '{c}'")], t);

        // ── 1. 预检 ──
        if !strip(&sush("id", 15)).contains("uid=0") {
            return Err("没有 root (su -c id 不是 uid=0)".into());
        }
        if !strip(&sush("command -v magiskpolicy", 15)).contains("magiskpolicy") {
            return Err("没有 magiskpolicy (SELinux 放行要靠它)".into());
        }
        let legacy = strip(&ash(&format!("dumpsys package {pkg} | grep -m1 legacyNativeLibraryDir"), 30))
            .lines()
            .find_map(|l| l.split_once("legacyNativeLibraryDir=").map(|(_, v)| v.trim().to_string()))
            .unwrap_or_default();
        if legacy.is_empty() {
            return Err(format!("解析不到 {pkg} 的 legacyNativeLibraryDir"));
        }
        let libdir = format!("{legacy}/arm64");
        if !adb_ok(&sush(&format!("test -d {libdir}"), 15)) {
            return Err(format!("{libdir} 不存在 (目标不是 arm64 应用?)"));
        }
        if strip(&summ(&format!("grep -q \" {libdir} \" /proc/mounts && echo m"), 15)).contains('m') {
            return Err(format!("{libdir} 已经是挂载点 (上一轮没还原?): 先 su -mm -c umount {libdir}"));
        }
        let lib_kb: u64 = strip(&sush(&format!("du -sk {libdir}"), 60)).split_whitespace().next().and_then(|x| x.parse().ok()).unwrap_or(0);
        let free_kb: u64 = strip(&sush("df -k /data", 15)).lines().nth(1).and_then(|l| l.split_whitespace().nth(3)).and_then(|x| x.parse().ok()).unwrap_or(0);
        if lib_kb == 0 || free_kb <= lib_kb + FREE_MARGIN_KB {
            return Err(format!("/data 空间不够放 lib 目录副本 (lib {lib_kb} KB, 剩 {free_kb} KB)"));
        }
        let ts = chrono::Local::now().format("%Y%m%d-%H%M%S").to_string();
        let run_dir = format!("{DEV_BASE}/run_{ts}");
        if adb_ok(&sush(&format!("test -e {run_dir}"), 15)) {
            return Err(format!("{run_dir} 已存在, 不覆盖"));
        }
        let lib_label = strip(&sush(&format!("ls -Zd {libdir}"), 15)).split_whitespace().next().unwrap_or("").to_string();
        let file_label = strip(&sush(&format!("ls -Z {libdir}"), 15)).lines().find_map(|l| { let mut it = l.split_whitespace(); let a = it.next()?; it.next()?; Some(a.to_string()) }).unwrap_or_default();
        if !lib_label.starts_with("u:object_r:") || !file_label.starts_with("u:object_r:") {
            return Err(format!("读不到 lib 目录的 SELinux 标签 (dir={lib_label:?} file={file_label:?})"));
        }

        // ── 2. 推文件 (只进新目录); 与游戏自己的库重名就停 ──
        let pre_ls = lines_of(&sush(&format!("ls {libdir}"), 30));
        if pre_ls.is_empty() {
            return Err(format!("{libdir} 列不出文件"));
        }
        let names: Vec<String> = files.device_names().into_iter().map(|(_, n)| n).collect();
        let conflicts = name_conflicts(&pre_ls, &names);
        if !conflicts.is_empty() {
            return Err(format!("游戏 lib 目录里已有 {}, 不覆盖, 停", conflicts.join(", ")));
        }
        let _ = std::fs::create_dir_all(evidence);
        std::fs::write(evidence.join("npuop_pre_libdir_ls.txt"), pre_ls.join("\n")).ok();
        std::fs::write(evidence.join("npuop_pre_libdir_lsZ.txt"), strip(&sush(&format!("ls -laZ {libdir}"), 30))).ok();
        if !adb_ok(&sush(&format!("mkdir -p {run_dir}/push {run_dir}/libdir && chmod 777 {run_dir}/push"), 15)) {
            return Err(format!("建不了 {run_dir}"));
        }
        for (local, dev) in files.device_names() {
            let out = adb(serial, &["push", &local.to_string_lossy(), &format!("{run_dir}/push/{dev}")], 180);
            if !adb_ok(&out) {
                return Err(format!("push {} 失败: {}", local.display(), out.trim()));
            }
        }
        sush(&format!("echo {libdir} > {run_dir}/state.libdir"), 15);

        // ── 3. 拼副本目录: 原目录副本 + 我们的文件 + /vendor 的 libcdsprpc 及依赖 ──
        if !adb_ok(&sush(&format!("cp -a {libdir}/. {run_dir}/libdir/"), 300)) {
            return Err("复制 lib 目录失败".into());
        }
        let copied = lines_of(&sush(&format!("ls {run_dir}/libdir"), 30));
        if !listing_matches(&pre_ls, &copied) {
            return Err(format!("lib 目录副本 {} 个文件 ≠ 原目录 {} 个, 复制不完整", copied.len(), pre_ls.len()));
        }
        if !adb_ok(&sush(&format!("cp {run_dir}/push/*.so {run_dir}/libdir/"), 60)) {
            return Err("复制层/运行库/模型到副本目录失败".into());
        }
        for n in VENDOR_LIBS {
            if !adb_ok(&sush(&format!("cp /vendor/lib64/{n} {run_dir}/libdir/{n}"), 30)) {
                return Err(format!("复制 /vendor/lib64/{n} 失败"));
            }
        }
        if !adb_ok(&sush(
            &format!("chown -R system:system {run_dir}/libdir && chmod 755 {run_dir}/libdir && chmod 755 {run_dir}/libdir/* && chcon {lib_label} {run_dir}/libdir && chcon {file_label} {run_dir}/libdir/*"),
            60,
        )) {
            return Err("副本目录属主/标签设置失败".into());
        }
        let staged = lines_of(&sush(&format!("ls {run_dir}/libdir"), 30));
        let want = pre_ls.len() + names.len() + VENDOR_LIBS.len();
        if staged.len() != want {
            return Err(format!("拼好的目录 {} 个文件, 应为 {want} (原 {} + 推 {} + vendor {})", staged.len(), pre_ls.len(), names.len(), VENDOR_LIBS.len()));
        }
        std::fs::write(evidence.join("npuop_stage_libdir_lsZ.txt"), strip(&sush(&format!("ls -laZ {run_dir}/libdir"), 30))).ok();
        sush(&format!("echo \"{run_dir}: npuop 评测 {ts} 用的 lib 目录副本 ({pkg} lib 目录 + 层 + NPU 运行库 + 模型), bind mount 用, 还原后留存未删\" >> {DEV_BASE}/README.txt"), 15);

        // ── 4. 记下运行前的属性 ──
        let mut pre_props = Vec::new();
        for k in SAVED_PROPS {
            pre_props.push((k.to_string(), strip(&ash(&format!("getprop {k}"), 15)).trim().to_string()));
        }
        let pre_text: String = pre_props.iter().map(|(k, v)| format!("{k}={v}\n")).collect();
        std::fs::write(evidence.join("npuop_pre_props.txt"), &pre_text).ok();
        // 两份留底也放进手机上的 run 目录 (中途断掉时可照 N1 的 ondevice.sh restore <run 目录> 手动还原)
        for f in ["pre_props.txt", "pre_libdir_ls.txt"] {
            let local = evidence.join(format!("npuop_{f}"));
            let _ = adb(serial, &["push", &local.to_string_lossy(), &format!("{run_dir}/push/{f}")], 30);
            sush(&format!("cp {run_dir}/push/{f} {run_dir}/{f}"), 15);
        }

        // ── 5. 研究机绕过: SELinux 放行 + bind mount ──
        if !adb_ok(&sush("magiskpolicy --live \"allow appdomain vendor_qdsp_device chr_file { open read ioctl getattr map }\"", 30)) {
            return Err("magiskpolicy --live 失败 (SELinux 放行没做成)".into());
        }
        if !adb_ok(&summ(&format!("mount --bind {run_dir}/libdir {libdir}"), 30)) {
            return Err("bind mount 失败".into());
        }
        let now = lines_of(&sush(&format!("ls {libdir}"), 30));
        if !now.iter().any(|n| n == SO) || !now.iter().any(|n| n == MODEL_DEV_NAME) {
            let _ = summ(&format!("umount {libdir}"), 30);
            return Err("挂上后 lib 目录里看不到层/模型 (挂载命名空间不对?), 已 umount".into());
        }
        let m = NpuopMount {
            serial: serial.to_string(),
            pkg: pkg.to_string(),
            run_dir,
            libdir,
            pre_props,
            pre_ls,
            files: files.to_json(),
        };
        std::fs::write(evidence.join("npuop_setup.json"), serde_json::to_string_pretty(&m.to_json()).unwrap_or_default()).ok();
        Ok(m)
    }

    /// 从手机上的 run 目录读回还原所需的状态 (state.libdir / pre_props.txt / pre_libdir_ls.txt),
    /// 给中途断掉的评测手动还原用。只读, 不改手机。
    pub fn from_device(serial: &str, pkg: &str, run_dir: &str) -> Result<Self, String> {
        let run_dir = run_dir.trim_end_matches('/');
        if !run_dir.starts_with(&format!("{DEV_BASE}/run_")) {
            return Err(format!("run 目录要是 {DEV_BASE}/run_<时间>, 收到 {run_dir}"));
        }
        let cat = |f: &str| strip(&su(serial, &format!("cat {run_dir}/{f} 2>/dev/null"), 15)).to_string();
        let libdir = cat("state.libdir").trim().to_string();
        if libdir.is_empty() {
            return Err(format!("{run_dir}/state.libdir 读不到 (不是 npuop 的 run 目录?)"));
        }
        let pre_props = parse_props(&cat("pre_props.txt"));
        if pre_props.is_empty() {
            return Err(format!("{run_dir}/pre_props.txt 读不到, 不知道运行前的属性"));
        }
        let pre_ls = lines_of(&cat("pre_libdir_ls.txt"));
        Ok(NpuopMount { serial: serial.to_string(), pkg: pkg.to_string(), run_dir: run_dir.to_string(), libdir, pre_props, pre_ls, files: Value::Null })
    }

    pub fn to_json(&self) -> Value {
        json!({
            "run_dir": self.run_dir,
            "libdir": self.libdir,
            "pre_props": self.pre_props.iter().map(|(k, v)| json!({"k": k, "v": v})).collect::<Vec<_>>(),
            "pre_libdir_files": self.pre_ls.len(),
            "files": self.files,
            "vendor_libs": VENDOR_LIBS,
            "selinux_note": "magiskpolicy --live 放行只在内存里, 重启消失, 不撤销",
        })
    }

    /// 还原: force-stop → 等进程真退 → umount (可能叠了不止一层) → 核对 lib 目录清单 → 属性逐个设回。
    /// 失败只用 [`OFF_FAIL_MARK`] 标; 副本目录留在手机上不删。
    pub fn restore(&self) -> String {
        let serial = self.serial.as_str();
        let ash = |c: &str, t: u64| adb(serial, &["shell", c], t);
        let sush = |c: &str, t: u64| su(serial, c, t);
        let summ = |c: &str, t: u64| adb(serial, &["shell", &format!("su -mm -c '{c}'")], t);
        let mut log = vec![format!("── npuop 还原 ({}) ──", self.run_dir)];
        ash(&format!("am force-stop {}", self.pkg), 30);
        // force-stop 后进程要一会儿才真退, 立刻 umount 会 EBUSY: 先等 pidof 为空 (最多 15 秒)
        let mut gone = false;
        for _ in 0..15 {
            if strip(&ash(&format!("pidof {}", self.pkg), 15)).trim().is_empty() {
                gone = true;
                break;
            }
            std::thread::sleep(std::time::Duration::from_secs(1));
        }
        if !gone {
            log.push(format!("{OFF_FAIL_MARK}: 等了 15 秒 {} 进程还在, umount 可能 EBUSY", self.pkg));
        }
        let is_mounted = || strip(&summ(&format!("grep -q \" {} \" /proc/mounts && echo m", self.libdir), 15)).contains('m');
        for _ in 0..6 {
            if !is_mounted() {
                break;
            }
            if adb_ok(&summ(&format!("umount {}", self.libdir), 30)) {
                log.push(format!("  已 umount {}", self.libdir));
            } else {
                std::thread::sleep(std::time::Duration::from_secs(1));
            }
        }
        if is_mounted() {
            log.push(format!("{OFF_FAIL_MARK}: {} 仍是挂载点, 请手动 su -mm -c umount {}", self.libdir, self.libdir));
        }
        let now = lines_of(&sush(&format!("ls {}", self.libdir), 30));
        if listing_matches(&now, &self.pre_ls) {
            log.push(format!("  lib 目录与运行前一致 ({} 个文件)", now.len()));
        } else {
            log.push(format!("{OFF_FAIL_MARK}: lib 目录清单与运行前不同 (now {} 个, pre {} 个), 请人工核对 npuop_pre_libdir_ls.txt", now.len(), self.pre_ls.len()));
        }
        sush(&restore_props_cmd(&self.pre_props), 30);
        let mut bad = Vec::new();
        for (k, v) in &self.pre_props {
            let got = strip(&ash(&format!("getprop {k}"), 15)).trim().to_string();
            if &got != v {
                bad.push(format!("{k}=[{got}] 应为 [{v}]"));
            }
        }
        if bad.is_empty() {
            log.push(format!("  属性已还原 ({} 个)", self.pre_props.len()));
        } else {
            log.push(format!("{OFF_FAIL_MARK}: 属性没设回: {}", bad.join(", ")));
        }
        log.push(format!("  留在手机上的文件 (不删): {}  (说明见 {DEV_BASE}/README.txt)", self.run_dir));
        log.push("  SELinux 放行规则仍在内存里, 下次重启消失".into());
        log.join("\n")
    }
}

/// 还原回包是否干净 (与 graylayer 同一口径: 只认明确的失败标记)。
pub(crate) fn restore_clean(log: &str) -> bool {
    !log.contains(OFF_FAIL_MARK)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn device_names_rename_layer_and_model_only() {
        let f = NpuopFiles {
            layer_so: PathBuf::from("/x/libVkLayer_refknobs-r2f-20261021.so"),
            model: PathBuf::from("/x/genshin_sr_v2_a.tflite"),
            runtime: RUNTIME_LIBS.iter().map(|n| PathBuf::from(format!("/rt/{n}"))).collect(),
        };
        let names: Vec<String> = f.device_names().into_iter().map(|(_, n)| n).collect();
        assert_eq!(names[0], "libVkLayer_refknobs.so");
        assert_eq!(names[1], "libnpusr_model.so");
        assert_eq!(&names[2..], &RUNTIME_LIBS.map(String::from)[..]);
        assert_eq!(names.len(), 2 + RUNTIME_LIBS.len());
    }

    /// 与游戏自己的库重名 (含要从 /vendor 复制的) 一律报出来 —— 绝不覆盖。
    #[test]
    fn name_conflicts_catch_pushed_and_vendor_names() {
        let pre = vec!["libunity.so".to_string(), "libc++.so".to_string(), "libnpusr_model.so".to_string()];
        let c = name_conflicts(&pre, &["libVkLayer_refknobs.so".to_string(), "libnpusr_model.so".to_string()]);
        assert_eq!(c, vec!["libc++.so".to_string(), "libnpusr_model.so".to_string()]);
        assert!(name_conflicts(&["libunity.so".to_string()], &["libVkLayer_refknobs.so".to_string()]).is_empty());
    }

    #[test]
    fn props_round_trip_keeps_empty_values() {
        let pre = parse_props("debug.vulkan.layers=\ndebug.knobs.npuop=1\n\n=junk\ndebug.knobs.npuop_wait_ms=2");
        assert_eq!(pre.len(), 3);
        assert_eq!(pre[0], ("debug.vulkan.layers".to_string(), String::new()));
        let cmd = restore_props_cmd(&pre);
        assert_eq!(cmd, "setprop debug.vulkan.layers \"\"; setprop debug.knobs.npuop \"1\"; setprop debug.knobs.npuop_wait_ms \"2\"");
    }

    #[test]
    fn listing_compare_ignores_order_and_blank_lines() {
        let a = vec!["b.so".to_string(), "a.so".to_string(), String::new()];
        let b = vec!["a.so".to_string(), "b.so".to_string()];
        assert!(listing_matches(&a, &b));
        assert!(!listing_matches(&a, &["a.so".to_string()]));
    }

    #[test]
    fn restore_clean_only_trusts_the_fail_mark() {
        assert!(restore_clean("── npuop 还原 ──\n  已 umount /x\n  属性已还原 (18 个)"));
        assert!(!restore_clean("警告: /x 仍是挂载点"));
    }

    /// 定位: 层 .so 没有 npuop 字符串就拒; 模型不是 .tflite 就拒 —— 都在碰设备之前。
    #[test]
    fn locate_refuses_a_layer_without_npuop_and_a_non_tflite_model() {
        let dir = std::env::temp_dir().join(format!("npuop_locate_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let old = dir.join("old_layer.so");
        std::fs::write(&old, b"\x7fELF debug.knobs.upop").unwrap();
        let e = NpuopFiles::locate(&NpuopFileArgs { layer_so: Some(old.to_string_lossy().into()), ..Default::default() }).unwrap_err();
        assert!(e.contains("没有 npuop"), "{e}");
        let new = dir.join("new_layer.so");
        std::fs::write(&new, b"\x7fELF debug.knobs.npuop_wait_ms").unwrap();
        let bad_model = dir.join("m.onnx");
        std::fs::write(&bad_model, b"x").unwrap();
        let e = NpuopFiles::locate(&NpuopFileArgs {
            layer_so: Some(new.to_string_lossy().into()),
            model: Some(bad_model.to_string_lossy().into()),
            runtime_dir: None,
        })
        .unwrap_err();
        assert!(e.contains(".tflite"), "{e}");
        // 运行库目录缺文件也拒, 并说缺哪个
        let model = dir.join("m.tflite");
        std::fs::write(&model, b"x").unwrap();
        let rt = dir.join("rt");
        std::fs::create_dir_all(&rt).unwrap();
        let e = NpuopFiles::locate(&NpuopFileArgs {
            layer_so: Some(new.to_string_lossy().into()),
            model: Some(model.to_string_lossy().into()),
            runtime_dir: Some(rt.to_string_lossy().into()),
        })
        .unwrap_err();
        assert!(e.contains("libtensorflowlite_jni.so"), "{e}");
        for n in RUNTIME_LIBS {
            std::fs::write(rt.join(n), b"so").unwrap();
        }
        let f = NpuopFiles::locate(&NpuopFileArgs {
            layer_so: Some(new.to_string_lossy().into()),
            model: Some(model.to_string_lossy().into()),
            runtime_dir: Some(rt.to_string_lossy().into()),
        })
        .unwrap();
        assert_eq!(f.runtime.len(), RUNTIME_LIBS.len());
        assert_eq!(f.model, model);
    }
}
