//! 脚本与回放模式 (Script & Replay Mode):
//! 确定性执行离线脚本或历史对局轨迹，跳过视觉模型决策回路，零 Token 消耗。
//! 同时保留 phonefarm 完整的 68 项全维度遥测采集 (FPS、CPU、内存、温度、I/O 等)
//! 与标准记录契约落盘 (log.jsonl)，产物可无缝经由 show、stats、last 进行回溯分析。
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// 单个脚本动作步骤
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ScriptStep {
    /// 动作类型: tap | swipe | scroll_down | scroll_up | type | clear | back | home | sleep | launch | stop | shell | loop
    #[serde(alias = "a", alias = "act", alias = "op", alias = "type")]
    pub action: String,

    /// 坐标参数 (tap / swipe 起点)
    #[serde(default)]
    pub x: Option<i32>,
    #[serde(default)]
    pub y: Option<i32>,

    /// 滑动终点坐标
    #[serde(default, alias = "x2", alias = "to_x")]
    pub to_x: Option<i32>,
    #[serde(default, alias = "y2", alias = "to_y")]
    pub to_y: Option<i32>,

    /// 文本输入 (type) 或 动作描述
    #[serde(default, alias = "t", alias = "val", alias = "value")]
    pub text: Option<String>,

    /// 延时时间 (毫秒优先，次选秒)
    #[serde(default, alias = "sleep_ms", alias = "wait_ms", alias = "delay_ms")]
    pub ms: Option<u64>,
    #[serde(default, alias = "s", alias = "seconds")]
    pub sec: Option<f64>,

    /// 目标应用与组件 (launch / stop)
    #[serde(default, alias = "app", alias = "package")]
    pub pkg: Option<String>,
    #[serde(default, alias = "activity", alias = "comp", alias = "ability")]
    pub comp: Option<String>,

    /// Shell 命令 (shell / exec)
    #[serde(default, alias = "command")]
    pub cmd: Option<String>,

    /// 虚拟手柄按键 (gamepad_press / gamepad_down / gamepad_up)
    #[serde(default, alias = "btn", alias = "key")]
    pub button: Option<String>,

    /// 虚拟手柄摇杆与轴向 ("left" / "right")
    #[serde(default, alias = "axis")]
    pub stick: Option<String>,

    /// 持续按压时间 (毫秒)
    #[serde(default, alias = "hold_ms", alias = "duration")]
    pub duration_ms: Option<u64>,

    /// 浮点摇杆/扳机量 (-1.0 ~ 1.0 或 0.0 ~ 1.0)
    #[serde(default, alias = "fx")]
    pub val_x: Option<f32>,
    #[serde(default, alias = "fy")]
    pub val_y: Option<f32>,

    /// 十字方向键 (up, down, left, right, center)
    #[serde(default)]
    pub dpad: Option<String>,

    /// 扳机名称 ("lt" / "rt")
    #[serde(default)]
    pub trigger: Option<String>,

    /// 循环与嵌套动作 (loop / repeat)
    #[serde(default, alias = "repeat")]
    pub count: Option<u32>,
    #[serde(default, alias = "actions", alias = "body")]
    pub steps: Option<Vec<ScriptStep>>,

    /// 是否强制采集重量级遥测明细
    #[serde(default)]
    pub heavy: Option<bool>,

    /// instrumentation runner (instrument 动作, CTS Harness)
    #[serde(default)]
    pub runner: Option<String>,

    /// Class 或 Class#method 切片 (instrument 动作;缺省=整个 runner)
    #[serde(default, alias = "method", alias = "test", alias = "case")]
    pub class_or_method: Option<String>,

    /// 透传 -e key=value 运行器参数 (instrument 动作)
    #[serde(default)]
    pub env_args: Option<Vec<String>>,

    /// 静默超时(毫秒): instrument 管道长时间无输出判定锁死;缺省 90s
    #[serde(default, alias = "idle_ms", alias = "idle_timeout_ms")]
    pub idle_ms: Option<u64>,

    /// OH(stage model)HAP 模块名(aa test -m);Android 设备上忽略
    #[serde(default, alias = "hap", alias = "hap_module")]
    pub module: Option<String>,
}

/// 脚本执行配置
#[derive(Debug, Clone)]
pub struct ScriptRunConfig {
    pub task: String,
    pub script_source: String,
    pub serial: Option<String>,
    pub app: Option<String>,
    pub repeat: u32,
    pub settle_ms: u64,
    pub tele_interval: u32,
    pub no_screen: bool,
    pub data_dir: String,
}

/// 脚本执行结果摘要
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScriptResult {
    pub run_id: String,
    pub stop: String,
    pub steps: usize,
    pub wall_ms: u64,
    pub achieved: bool,
}

/// 解析文本内容为步骤列表。支持:
/// 1. JSON 数组: `[{"action":"tap","x":100,"y":200}, ...]`
/// 2. JSON 对象包装: `{"steps": [...]}` 或 `{"actions": [...]}`
/// 3. JSONL 格式 (包含 log.jsonl 历史轨迹中的 `r="act"` 记录)
/// 4. TOML 格式: `steps = [...]` 或 `[[steps]]`
pub fn parse_script_content(content: &str) -> Result<Vec<ScriptStep>, String> {
    let trimmed = content.trim();
    if trimmed.is_empty() {
        return Err("脚本内容为空".into());
    }

    // 1. 尝试以 JSON 解析
    if trimmed.starts_with('[') {
        if let Ok(steps) = serde_json::from_str::<Vec<ScriptStep>>(trimmed) {
            return Ok(steps);
        }
    } else if trimmed.starts_with('{') {
        // 判断是否是单行 JSON 或对象包装
        if let Ok(val) = serde_json::from_str::<Value>(trimmed) {
            if let Some(arr) = val.get("steps").or_else(|| val.get("actions")) {
                if let Ok(steps) = serde_json::from_value::<Vec<ScriptStep>>(arr.clone()) {
                    return Ok(steps);
                }
            }
        }
    }

    // 2. 尝试以 JSONL 行流解析 (兼容 log.jsonl 的 r="act" 轨迹)
    let mut jsonl_steps = Vec::new();
    let mut is_jsonl = false;
    for line in trimmed.lines() {
        let l = line.trim();
        if l.is_empty() {
            continue;
        }
        if let Ok(val) = serde_json::from_str::<Value>(l) {
            is_jsonl = true;
            // 兼容 log.jsonl 中的 act 记录: {"r":"act","a":"tap","x":...}
            if let Some(r) = val.get("r").and_then(|r| r.as_str()) {
                if r == "act" {
                    if let Ok(step) = serde_json::from_value::<ScriptStep>(val) {
                        jsonl_steps.push(step);
                    }
                }
            } else if val.get("action").is_some() || val.get("a").is_some() || val.get("act").is_some() {
                if let Ok(step) = serde_json::from_value::<ScriptStep>(val) {
                    jsonl_steps.push(step);
                }
            }
        }
    }
    if is_jsonl && !jsonl_steps.is_empty() {
        return Ok(jsonl_steps);
    }

    // 3. 尝试以 TOML 解析
    if let Ok(val) = toml::from_str::<Value>(trimmed) {
        if let Some(arr) = val.get("steps").or_else(|| val.get("actions")) {
            let json_val = serde_json::to_value(arr).map_err(|e| e.to_string())?;
            if let Ok(steps) = serde_json::from_value::<Vec<ScriptStep>>(json_val) {
                return Ok(steps);
            }
        }
    }

    Err("无法识别脚本格式，请提供 JSON 数组、JSONL 动作流或 TOML 步骤表".into())
}

/// 从文件路径或历史对局 ID 中载入脚本
pub fn load_script(source: &str, data_root: &Path) -> Result<Vec<ScriptStep>, String> {
    let p = Path::new(source);
    if p.is_file() {
        let content = fs::read_to_string(p).map_err(|e| format!("读取脚本文件失败 {source}: {e}"))?;
        return parse_script_content(&content);
    }

    // 若不是本地存在的文件，尝试模糊匹配历史局 ID 进行轨迹提取回放
    let tasks_dir = data_root.join("tasks");
    if let Ok(tasks) = fs::read_dir(&tasks_dir) {
        for t in tasks.flatten() {
            let runs_dir = t.path().join("runs");
            if let Ok(runs) = fs::read_dir(&runs_dir) {
                for r in runs.flatten() {
                    let rname = r.file_name().to_string_lossy().to_string();
                    if rname.starts_with(source) {
                        let log_path = r.path().join("log.jsonl");
                        if log_path.is_file() {
                            let content = fs::read_to_string(&log_path)
                                .map_err(|e| format!("读取历史对局日志失败 {}: {e}", log_path.display()))?;
                            let steps = parse_script_content(&content)?;
                            if steps.is_empty() {
                                return Err(format!("历史对局 {rname} 中未找到可执行的动作 (r=act)"));
                            }
                            println!("(从历史对局 {rname} 提取到 {} 个动作作为回放脚本)", steps.len());
                            return Ok(steps);
                        }
                    }
                }
            }
        }
    }

    Err(format!("找不到指定的脚本文件或历史对局 ID: '{source}'"))
}

/// 展开嵌套循环步骤，生成平铺的线性执行序列
pub fn flatten_steps(raw: &[ScriptStep]) -> Result<Vec<ScriptStep>, String> {
    let mut out = Vec::new();
    expand_recursive(raw, &mut out, 0, 10_000)?;
    Ok(out)
}

fn expand_recursive(
    steps: &[ScriptStep],
    out: &mut Vec<ScriptStep>,
    depth: usize,
    max_total: usize,
) -> Result<(), String> {
    if depth > 10 {
        return Err("脚本嵌套循环深度超过 10 层限制".into());
    }
    for s in steps {
        let act = s.action.trim().to_lowercase();
        if act == "loop" || act == "repeat" {
            let count = s.count.unwrap_or(1);
            let sub = s.steps.as_deref().unwrap_or(&[]);
            for _ in 0..count {
                expand_recursive(sub, out, depth + 1, max_total)?;
                if out.len() > max_total {
                    return Err(format!("脚本步骤总数超过上限 {max_total} 步"));
                }
            }
        } else {
            out.push(s.clone());
            if out.len() > max_total {
                return Err(format!("脚本步骤总数超过上限 {max_total} 步"));
            }
        }
    }
    Ok(())
}

/// 执行脚本主逻辑
pub fn execute_script(cfg: &ScriptRunConfig) -> Result<ScriptResult, String> {
    let t0 = Instant::now();
    let data_root = PathBuf::from(cfg.data_dir.trim_end_matches('/'));

    // 1. 载入并展开脚本
    let raw_steps = load_script(&cfg.script_source, &data_root)?;
    let flat_steps = flatten_steps(&raw_steps)?;
    if flat_steps.is_empty() {
        return Err("脚本不包含任何有效步骤".into());
    }

    // 2. 准备运行目录与账本
    let run_id = match std::env::var("PF_RUN_ID") {
        Ok(v) if !v.trim().is_empty() => v.trim().to_string(),
        _ => crate::runtime::alloc_run_id(),
    };
    let task_dir = data_root.join("tasks").join(&cfg.task);
    let run_dir = task_dir.join("runs").join(&run_id);
    fs::create_dir_all(&run_dir).map_err(|e| format!("创建运行目录失败 {}: {e}", run_dir.display()))?;

    let log_path = run_dir.join("log.jsonl");
    let mut log = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|e| format!("无法创建日志文件 {}: {e}", log_path.display()))?;

    // 写入首行版本契约与开跑标记
    writeln!(log, "{}", json!({"v": 1})).map_err(|e| e.to_string())?;
    writeln!(
        log,
        "{}",
        json!({
            "r": "start",
            "pid": std::process::id(),
            // 与 runtime 那条一致: 记下这一局落在哪台机器上, 判不出记 null
            "serial": crate::device::resolve_serial(cfg.serial.as_deref()),
            "ts": SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
        })
    )
    .map_err(|e| e.to_string())?;
    writeln!(
        log,
        "{}",
        json!({
            "r": "goal",
            "t": format!("script: {} ({} steps × {} rounds)", cfg.script_source, flat_steps.len(), cfg.repeat)
        })
    )
    .map_err(|e| e.to_string())?;

    // 3. 初始化设备通信
    let tmp = std::env::temp_dir()
        .join(format!("phonefarm-script-{}", std::process::id()))
        .to_string_lossy()
        .to_string();
    let _ = fs::create_dir_all(&tmp);
    let phone = crate::device::Device::new(cfg.serial.clone(), tmp);

    // 4. 遥测初始化 (如果指定了 app，开局探测 root 并上载遥测脚本)
    let mut tele_pkg = cfg.app.clone();
    let mut tele_root = false;
    if let Some(pkg) = &tele_pkg {
        tele_root = phone.telemetry_setup(pkg);
    }

    let total_steps = flat_steps.len() * cfg.repeat as usize;
    println!("════ 启动脚本模式 (Script Mode) ════");
    println!("目标任务: {}", cfg.task);
    println!("运行ID:   {}", run_id);
    println!("单轮步数: {} | 轮数: {} | 总执行步: {}", flat_steps.len(), cfg.repeat, total_steps);
    if let Some(pkg) = &tele_pkg {
        println!("监控应用: {} (Root 遥测: {})", pkg, if tele_root { "开启" } else { "未激活" });
    }
    println!("落盘目录: {}", run_dir.display());
    println!("────────────────────────────────────");

    let mut step_n = 0usize;
    let mut tele_cnt = 0u32;
    let mut tele_last_t = Instant::now();
    let mut tele_prev_frames: Option<(i64, Instant)> = None;
    let mut tele_prev_ev: (Option<i32>, Option<i32>, Option<i32>, Option<i32>) = (None, None, None, None);

    for round in 1..=cfg.repeat {
        if cfg.repeat > 1 {
            println!("── 第 {round}/{} 轮 ──", cfg.repeat);
        }
        for step in &flat_steps {
            step_n += 1;
            let act_name = step.action.trim().to_lowercase();

            // 1. 截屏落盘 (默认开启，--no-screen 时跳过以实现极速压测)
            if !cfg.no_screen {
                let img_path = run_dir.join(format!("step{step_n}.jpg"));
                phone.screen(&img_path.to_string_lossy());
            }

            // 2. 写入动作记录 r="act"
            let mut act_val = json!({
                "r": "act",
                "n": step_n,
                "a": act_name,
                "by": "script"
            });
            if let Some(x) = step.x { act_val["x"] = json!(x); }
            if let Some(y) = step.y { act_val["y"] = json!(y); }
            if let Some(x2) = step.to_x { act_val["x2"] = json!(x2); }
            if let Some(y2) = step.to_y { act_val["y2"] = json!(y2); }
            if let Some(t) = &step.text { act_val["text"] = json!(t); }
            if let Some(b) = &step.button { act_val["button"] = json!(b); }
            if let Some(s) = &step.stick { act_val["stick"] = json!(s); }
            if let Some(d) = step.duration_ms { act_val["duration_ms"] = json!(d); }
            if let Some(fx) = step.val_x { act_val["val_x"] = json!(fx); }
            if let Some(fy) = step.val_y { act_val["val_y"] = json!(fy); }
            writeln!(log, "{act_val}").map_err(|e| e.to_string())?;

            // 3. 执行动作
            print!("[{step_n}/{total_steps}] 动作: {act_name}");
            match act_name.as_str() {
                "tap" | "click" => {
                    let x = step.x.unwrap_or(540);
                    let y = step.y.unwrap_or(1170);
                    print!(" ({x}, {y})");
                    phone.tap(x, y);
                }
                "swipe" => {
                    let x1 = step.x.unwrap_or(540);
                    let y1 = step.y.unwrap_or(1500);
                    let x2 = step.to_x.unwrap_or(540);
                    let y2 = step.to_y.unwrap_or(800);
                    print!(" ({x1}, {y1}) -> ({x2}, {y2})");
                    phone.swipe(x1, y1, x2, y2);
                }
                "scroll_down" => {
                    print!(" (下滑)");
                    phone.scroll_down();
                }
                "scroll_up" => {
                    print!(" (上滑)");
                    phone.scroll_up();
                }
                "type" | "input" => {
                    let t = step.text.as_deref().unwrap_or("");
                    print!(" \"{t}\"");
                    phone.type_text(t);
                }
                "clear" => {
                    print!(" (清空输入框)");
                    phone.clear_field();
                }
                "back" => {
                    print!(" (返回)");
                    phone.back();
                }
                "home" => {
                    print!(" (回到桌面)");
                    phone.home();
                }
                "sleep" | "wait" => {
                    let d_ms = step.ms.unwrap_or_else(|| (step.sec.unwrap_or(1.0) * 1000.0) as u64);
                    print!(" 等待 {}ms", d_ms);
                    std::thread::sleep(Duration::from_millis(d_ms));
                }
                "launch" | "start" => {
                    if let Some(pkg) = &step.pkg {
                        print!(" 启动应用 {pkg}");
                        tele_pkg = Some(pkg.clone());
                        tele_root = phone.telemetry_setup(pkg);
                        let comp = step.comp.clone().unwrap_or_else(|| {
                            phone.launchable_apps()
                                .into_iter()
                                .find(|(p, _)| p == pkg)
                                .map(|(_, c)| c)
                                .unwrap_or_default()
                        });
                        phone.launch(pkg, &comp);
                    }
                }
                "stop" | "force_stop" => {
                    if let Some(pkg) = &step.pkg {
                        print!(" 停止应用 {pkg}");
                        phone.force_stop(pkg);
                    }
                }
                "shell" | "exec" => {
                    if let Some(cmd) = &step.cmd {
                        print!(" 执行 shell \"{cmd}\"");
                        let out = phone.shell(cmd, 15000);
                        if !out.trim().is_empty() {
                            print!(" => {}", crate::runtime::tcut(out.trim(), 40));
                        }
                    }
                }
                "gamepad_press" | "gamepad_button" | "pad_press" | "pad_btn" | "pad_button" => {
                    let btn = step.button.as_deref()
                        .or(step.text.as_deref())
                        .unwrap_or("a");
                    let dur = step.duration_ms.or(step.ms).unwrap_or(120);
                    print!(" 按手柄键 {btn} (持续 {dur}ms)");
                    if let Err(e) = phone.gamepad_press(btn, dur) {
                        eprintln!(" [手柄操作失败: {e}]");
                    }
                }
                "gamepad_down" | "pad_down" => {
                    let btn = step.button.as_deref()
                        .or(step.text.as_deref())
                        .unwrap_or("a");
                    print!(" 按下手柄键 {btn}");
                    if let Err(e) = phone.gamepad_down(btn) {
                        eprintln!(" [手柄操作失败: {e}]");
                    }
                }
                "gamepad_up" | "pad_up" => {
                    let btn = step.button.as_deref()
                        .or(step.text.as_deref())
                        .unwrap_or("a");
                    print!(" 松开手柄键 {btn}");
                    if let Err(e) = phone.gamepad_up(btn) {
                        eprintln!(" [手柄操作失败: {e}]");
                    }
                }
                "gamepad_stick" | "pad_stick" => {
                    let stick = step.stick.as_deref().unwrap_or("left");
                    let x = step.val_x
                        .or_else(|| step.x.map(|v| (v as f32) / 1000.0))
                        .unwrap_or(0.0);
                    let y = step.val_y
                        .or_else(|| step.y.map(|v| (v as f32) / 1000.0))
                        .unwrap_or(0.0);
                    let dur = step.duration_ms.or(step.ms).unwrap_or(0);
                    print!(" 推动手柄{stick}摇杆 ({x:.2}, {y:.2}) 保持 {dur}ms");
                    if let Err(e) = phone.gamepad_stick(stick, x, y, dur) {
                        eprintln!(" [手柄操作失败: {e}]");
                    }
                }
                "gamepad_trigger" | "pad_trigger" => {
                    let trig = step.trigger.as_deref()
                        .or(step.button.as_deref())
                        .unwrap_or("rt");
                    let val = step.val_x.unwrap_or(1.0);
                    let dur = step.duration_ms.or(step.ms).unwrap_or(100);
                    print!(" 压下手柄扳机 {trig} 深度 {val:.2} (持续 {dur}ms)");
                    if let Err(e) = phone.gamepad_trigger(trig, val, dur) {
                        eprintln!(" [手柄操作失败: {e}]");
                    }
                }
                "gamepad_dpad" | "pad_dpad" => {
                    let dir = step.dpad.as_deref()
                        .or(step.button.as_deref())
                        .or(step.text.as_deref())
                        .unwrap_or("center");
                    let dur = step.duration_ms.or(step.ms).unwrap_or(120);
                    print!(" 手柄十字键 {dir} (持续 {dur}ms)");
                    if let Err(e) = phone.gamepad_dpad(dir, dur) {
                        eprintln!(" [手柄操作失败: {e}]");
                    }
                }
                "gamepad_reset" | "pad_reset" => {
                    print!(" 复位手柄状态");
                    let _ = phone.gamepad_reset();
                }
                "gamepad_wander" | "pad_wander" => {
                    let dur = step.duration_ms
                        .or(step.ms)
                        .or_else(|| step.sec.map(|s| (s * 1000.0) as u64))
                        .unwrap_or(3000);
                    print!(" 手柄巡航走位与视角旋转 (持续 {}ms)", dur);
                    if let Err(e) = phone.gamepad_wander(dur) {
                        eprintln!(" [手柄漫步失败: {e}]");
                    }
                }
                "instrument" | "am_instrument" => {
                    // CTS Harness 原语: 流式执行 am instrument,双重看门狗,逐用例实时出结果。
                    // 结果以 hook 记录入账(kind=instrument),show --hooks 可回溯。
                    match (&step.pkg, &step.runner) {
                        (Some(pkg), Some(runner)) => {
                            let env_args: Vec<(String, String)> = step.env_args.clone()
                                .unwrap_or_default().into_iter()
                                .filter_map(|kv| kv.split_once('=')
                                    .map(|(k, v)| (k.to_string(), v.to_string())))
                                .collect();
                            // 平台跟设备后端走: hdc → OH(aa test),否则 Android(am instrument)
                            let platform = if phone.backend_name() == "hdc" {
                                crate::cts::Platform::Oh
                            } else {
                                crate::cts::Platform::Android
                            };
                            let spec = crate::cts::InstrumentSpec {
                                package: pkg.clone(),
                                runner: runner.clone(),
                                module: step.module.clone(),
                                platform,
                                class_or_method: step.class_or_method.clone(),
                                timeout_ms: step.ms.unwrap_or(600_000),
                                idle_timeout_ms: step.idle_ms.unwrap_or(90_000),
                                env_args,
                            };
                            print!(" instrument {}", spec.package);
                            if let Some(cm) = &spec.class_or_method {
                                print!(" -e class {cm}");
                            }
                            println!();
                            let run_dir_c = run_dir.clone();
                            let mut sink = move |line: &str| {
                                let p = run_dir_c.join("instrument_stdout.log");
                                if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(p) {
                                    let _ = writeln!(f, "{line}");
                                }
                            };
                            let run = crate::cts::run_instrument(&phone, &spec, &mut sink);
                            let (mut np, mut nf, mut ne, mut nt, mut nnr) = (0, 0, 0, 0, 0);
                            for c in &run.cases {
                                match c.verdict {
                                    crate::cts::CaseVerdict::PASS => np += 1,
                                    crate::cts::CaseVerdict::ASSERTION_FAIL => nf += 1,
                                    crate::cts::CaseVerdict::ENV_BLOCKED => ne += 1,
                                    crate::cts::CaseVerdict::TIMEOUT => nt += 1,
                                    crate::cts::CaseVerdict::NOT_RUN => nnr += 1,
                                }
                            }
                            let _ = writeln!(log, "{}", json!({
                                "r": "hook", "kind": "instrument", "n": step_n,
                                "package": spec.package, "runner": spec.runner,
                                "class_or_method": spec.class_or_method,
                                "run_verdict": run.verdict.as_str(),
                                "PASS": np, "ASSERTION_FAIL": nf, "ENV_BLOCKED": ne,
                                "TIMEOUT": nt, "NOT_RUN": nnr,
                                "process_crashed": run.process_crashed,
                                "wall_ms": run.wall_ms,
                            }));
                            print!(" => {} 用例 PASS{} FAIL{} ENV{} TMO{} NR{}",
                                run.cases.len(), np, nf, ne, nt, nnr);
                        }
                        _ => {
                            eprintln!("警告: instrument 动作需要 pkg 与 runner 字段 (步 {step_n})");
                        }
                    }
                }
                other => {
                    print!(" (未知动作，跳过)");
                    eprintln!("警告: 未识别的动作类型 '{other}' (步 {step_n})");
                }
            }

            // 动作后稳定延时
            if cfg.settle_ms > 0 {
                std::thread::sleep(Duration::from_millis(cfg.settle_ms));
            }

            // 4. 采集遥测并入账
            if let Some(pkg) = &tele_pkg {
                let heavy = step.heavy.unwrap_or(tele_cnt % cfg.tele_interval.max(1) == 0);
                tele_cnt += 1;
                let mut t = phone.telemetry(heavy, pkg);
                let now = Instant::now();
                t.step_ms = Some(now.duration_since(tele_last_t).as_millis() as i64);
                tele_last_t = now;
                if t.root.is_none() {
                    t.root = Some(tele_root);
                }

                // 计算瞬时 FPS
                if let Some((ft, kind)) = t.frame_counter() {
                    if let Some((pf, pt)) = tele_prev_frames {
                        let dt = now.duration_since(pt).as_secs_f32();
                        if dt > 0.3 && ft >= pf {
                            t.fps = Some(((ft - pf) as f32 / dt * 10.0).round() / 10.0);
                            t.fps_source = Some(kind);
                        }
                    }
                    tele_prev_frames = Some((ft, now));
                }

                // 捕获异常事件: Crash / ANR / FD泄露
                let (pc, pa, pfd, pconn) = tele_prev_ev;
                if let (Some(p), Some(c)) = (pc, t.crash_count) {
                    if c > p {
                        writeln!(
                            log,
                            "{}",
                            json!({"r":"app_event","kind":"crash","n":step_n,"from":p,"to":c,"pkg":pkg})
                        )
                        .map_err(|e| e.to_string())?;
                        println!(" [💥 崩溃计数 {p}→{c}]");
                    }
                }
                if let (Some(p), Some(c)) = (pa, t.anr_count) {
                    if c > p {
                        writeln!(
                            log,
                            "{}",
                            json!({"r":"app_event","kind":"anr","n":step_n,"from":p,"to":c,"pkg":pkg})
                        )
                        .map_err(|e| e.to_string())?;
                        println!(" [💥 ANR计数 {p}→{c}]");
                    }
                }
                if let (Some(p), Some(c)) = (pfd, t.fd_count) {
                    if c >= p + 50 {
                        writeln!(
                            log,
                            "{}",
                            json!({"r":"app_event","kind":"fd_growth","n":step_n,"from":p,"to":c,"pkg":pkg})
                        )
                        .map_err(|e| e.to_string())?;
                        println!(" [💥 FD激增 {p}→{c}]");
                    }
                }
                tele_prev_ev = (t.crash_count.or(pc), t.anr_count.or(pa), t.fd_count.or(pfd), t.net_conn.or(pconn));

                // 打印行级性能快照反馈
                let mut stats_line = String::new();
                if let Some(fps) = t.fps {
                    stats_line.push_str(&format!(" | FPS:{fps:.0}"));
                }
                if let Some(cpu) = t.cpu_total_pct {
                    stats_line.push_str(&format!(" | CPU:{cpu:.0}%"));
                }
                if let Some(pss) = t.app_pss_kb {
                    stats_line.push_str(&format!(" | PSS:{:.1}MB", pss as f64 / 1024.0));
                }
                if let Some(temp) = t.batt_temp_c {
                    stats_line.push_str(&format!(" | 电池:{temp:.1}℃"));
                }
                println!("{stats_line}");

                // 写入 telemetry 记录
                if let Ok(v) = serde_json::to_value(&t) {
                    let mut rec = json!({
                        "r": "telemetry",
                        "n": step_n,
                        "seq": step_n as i64,
                        "heavy": heavy
                    });
                    if let (Some(o), Some(vo)) = (rec.as_object_mut(), v.as_object()) {
                        for (k, val) in vo {
                            o.insert(k.clone(), val.clone());
                        }
                    }
                    writeln!(log, "{rec}").map_err(|e| e.to_string())?;
                }
            } else {
                println!();
            }
        }
    }

    // 5. 写入收官记录 r="end"
    let wall_ms = t0.elapsed().as_millis() as u64;
    writeln!(
        log,
        "{}",
        json!({
            "r": "end",
            "stop": "done",
            "achieved": true,
            "steps": step_n,
            "calls": 0,
            "tokens": 0,
            "wall_ms": wall_ms
        })
    )
    .map_err(|e| e.to_string())?;

    println!("────────────────────────────────────");
    println!(
        "summary: run={run_id} stop=done steps={step_n} calls=0 tokens=0 wall={:.1}s achieved=true",
        wall_ms as f64 / 1000.0
    );
    println!("📊 查看遥测指标曲线: phonefarm stats {run_id}");
    println!("🔍 检查单步动作回溯: phonefarm show {run_id}");

    Ok(ScriptResult {
        run_id,
        stop: "done".into(),
        steps: step_n,
        wall_ms,
        achieved: true,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_json_array_script() {
        let content = r#"[
            {"action": "tap", "x": 100, "y": 200},
            {"action": "swipe", "x": 100, "y": 500, "to_x": 100, "to_y": 200},
            {"action": "type", "text": "hello world"},
            {"action": "sleep", "ms": 500}
        ]"#;
        let steps = parse_script_content(content).unwrap();
        assert_eq!(steps.len(), 4);
        assert_eq!(steps[0].action, "tap");
        assert_eq!(steps[0].x, Some(100));
        assert_eq!(steps[0].y, Some(200));
        assert_eq!(steps[1].action, "swipe");
        assert_eq!(steps[1].to_y, Some(200));
        assert_eq!(steps[2].action, "type");
        assert_eq!(steps[2].text.as_deref(), Some("hello world"));
        assert_eq!(steps[3].action, "sleep");
        assert_eq!(steps[3].ms, Some(500));
    }

    #[test]
    fn test_parse_json_object_wrapper() {
        let content = r#"{
            "name": "login_flow",
            "steps": [
                {"a": "home"},
                {"a": "launch", "pkg": "com.test.app"},
                {"a": "clear"}
            ]
        }"#;
        let steps = parse_script_content(content).unwrap();
        assert_eq!(steps.len(), 3);
        assert_eq!(steps[0].action, "home");
        assert_eq!(steps[1].action, "launch");
        assert_eq!(steps[1].pkg.as_deref(), Some("com.test.app"));
        assert_eq!(steps[2].action, "clear");
    }

    #[test]
    fn test_parse_log_jsonl_replay() {
        let content = r#"{"v":1}
{"r":"start","pid":1234,"ts":1000}
{"r":"goal","t":"test goal"}
{"r":"screen","n":1,"img":"step1.jpg"}
{"r":"act","n":1,"a":"tap","x":450,"y":900}
{"r":"diff","n":1,"d":"+[Item]"}
{"r":"act","n":2,"a":"type","text":"query"}
{"r":"act","n":3,"a":"back"}
{"r":"end","stop":"done","achieved":true}
"#;
        let steps = parse_script_content(content).unwrap();
        assert_eq!(steps.len(), 3, "提取 3 个 r=act 动作");
        assert_eq!(steps[0].action, "tap");
        assert_eq!(steps[0].x, Some(450));
        assert_eq!(steps[0].y, Some(900));
        assert_eq!(steps[1].action, "type");
        assert_eq!(steps[1].text.as_deref(), Some("query"));
        assert_eq!(steps[2].action, "back");
    }

    #[test]
    fn test_loop_expansion() {
        let content = r#"[
            {"action": "tap", "x": 10, "y": 20},
            {
                "action": "loop",
                "count": 3,
                "steps": [
                    {"action": "scroll_down"},
                    {"action": "sleep", "ms": 100}
                ]
            },
            {"action": "back"}
        ]"#;
        let raw = parse_script_content(content).unwrap();
        let flat = flatten_steps(&raw).unwrap();
        // 1 tap + 3 * (1 scroll_down + 1 sleep) + 1 back = 1 + 6 + 1 = 8
        assert_eq!(flat.len(), 8);
        assert_eq!(flat[0].action, "tap");
        assert_eq!(flat[1].action, "scroll_down");
        assert_eq!(flat[2].action, "sleep");
        assert_eq!(flat[3].action, "scroll_down");
        assert_eq!(flat[4].action, "sleep");
        assert_eq!(flat[5].action, "scroll_down");
        assert_eq!(flat[6].action, "sleep");
        assert_eq!(flat[7].action, "back");
    }

    #[test]
    fn test_parse_toml_steps() {
        let content = r#"
[[steps]]
action = "launch"
pkg = "com.test.demo"

[[steps]]
action = "tap"
x = 300
y = 600
"#;
        let steps = parse_script_content(content).unwrap();
        assert_eq!(steps.len(), 2);
        assert_eq!(steps[0].action, "launch");
        assert_eq!(steps[0].pkg.as_deref(), Some("com.test.demo"));
        assert_eq!(steps[1].action, "tap");
        assert_eq!(steps[1].x, Some(300));
    }
}
