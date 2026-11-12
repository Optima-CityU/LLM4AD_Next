//! capture: 原神无 UI 自动巡航截图 (SPEC_SR_LOOP Gate 2)。
//!
//! 只做"拉起游戏 → 复用 genshin 插件的巡航单步 → 抓原始 PNG → 状态过滤/冻结去重 → 落盘 + manifest"。
//! 不改插件决策 (只调用其公开的生命周期与单步接口), 不侵入游戏进程; 帧是 adb screencap 的原始字节, 不重编码。
//! "无 UI" 的落地: 只保留大世界探索态的帧 (无对白/转场/标题/竖屏弹窗), HUD 固定区域由离线切块脚本按
//! SPEC_SR_LOOP §4 的裁剪框排除。门禁驱动: 收满 --frames 张即停, --max-steps 只是安全阀。
use crate::plugins::genshin::{
    classify_state, hud_present, GenshinQuestAgent, GenshinState, QuestConfig, QuestDevice, QuestMode,
};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::io::Write;
use std::time::{Duration, Instant};

/// 冻结判定: 64x36 灰度缩略图与上一张保留帧的平均绝对差 (0..255) 低于此值视为画面冻结 (卡住/暂停)
const DUP_THRESH: f32 = 1.0;
/// 路线分段: 每 N 张保留帧记为一段, 离线切块按段做固定划分 (同段帧不跨 train/val)
const SEGMENT_FRAMES: u32 = 10;
/// 同一非探索态连续卡住这么多步 (公告/弹窗类界面, 插件的 A 键推不动) 就发一次 BACK 键 (Genshin 弹窗通用关闭键)
const STUCK_STEPS_BACK: u32 = 6;
/// --ready-only 模式下最多连续观察多少次 (每次间隔 --settle-ms) 来确认探索态
const READY_ONLY_TRIES: u32 = 8;

fn emit(text: &str) {
    let mut out = std::io::stdout().lock();
    let _ = out.write_all(text.as_bytes()).and_then(|_| out.flush());
}

fn progress(msg: &str) {
    eprintln!("[capture] {msg}");
}

#[derive(Clone, serde::Serialize, serde::Deserialize)]
pub struct CaptureArgs {
    serial: Option<String>,
    out: Option<String>,
    frames: u32,
    /// 有限观察已有画面：探索态+HUD 保留1帧，否则未就绪；不启动游戏或退回巡航。
    ready_only: bool,
    max_steps: u32,
    settle_ms: u64,
    mode: String,
    shutdown: bool,
    json: bool,
    recover: Option<String>,
    evidence_origin: String,
    timeout_s: u64,
    #[serde(default = "default_screenshot_timeout_ms")]
    screenshot_timeout_ms: u64,
}

fn default_screenshot_timeout_ms() -> u64 {
    15_000
}

const USAGE: &str = "用法: phonefarm capture --serial <设备> [--out <目录>] [--frames 200] [--max-steps N] [--settle-ms 800]\n\
      [--mode auto|navigate|dialogue] [--no-shutdown] [--ready-only] [--json] [--timeout-s 3600]\n\
      [--screenshot-timeout-ms 15000] (1..60000; outer --timeout-s still applies)\n\
      phonefarm capture --serial <设备> --recover <session目录> [--json]\n\
      [--evidence-origin device|synthetic]\n\
      --ready-only: 最多观察8次，只取1帧，不启动/导航/强停游戏；恢复原stayon仍可能写设备。\n\
      --mode 只约束采集单步；普通调用仍有自动启动引导，navigate不保证剧情绝不推进。";

fn parse_args(args: &[String]) -> Result<CaptureArgs, String> {
    let mut a = CaptureArgs {
        serial: None,
        out: None,
        frames: 200,
        max_steps: 0,
        settle_ms: 800,
        mode: "auto".into(),
        shutdown: true,
        json: false,
        ready_only: false,
        recover: None,
        evidence_origin: "device".into(),
        timeout_s: 3600,
        screenshot_timeout_ms: default_screenshot_timeout_ms(),
    };
    let need = |args: &[String], i: usize, name: &str| -> Result<String, String> {
        args.get(i + 1)
            .cloned()
            .ok_or_else(|| format!("{name} 需要一个值\n{USAGE}"))
    };
    let mut i = 0;
    let mut frames_explicit = false;
    while i < args.len() {
        match args[i].as_str() {
            "--serial" => {
                a.serial = Some(need(args, i, "--serial")?);
                i += 1;
            }
            "--out" => {
                a.out = Some(need(args, i, "--out")?);
                i += 1;
            }
            "--frames" => {
                frames_explicit = true;
                a.frames = need(args, i, "--frames")?
                    .parse()
                    .map_err(|_| "--frames 需为正整数")?;
                i += 1;
            }
            "--max-steps" => {
                a.max_steps = need(args, i, "--max-steps")?
                    .parse()
                    .map_err(|_| "--max-steps 需为正整数")?;
                i += 1;
            }
            "--settle-ms" => {
                a.settle_ms = need(args, i, "--settle-ms")?
                    .parse()
                    .map_err(|_| "--settle-ms 需为整数")?;
                i += 1;
            }
            "--mode" => {
                a.mode = need(args, i, "--mode")?;
                i += 1;
            }
            "--no-shutdown" => a.shutdown = false,
            "--ready-only" => a.ready_only = true,
            "--json" => a.json = true,
            "--recover" => {
                a.recover = Some(need(args, i, "--recover")?);
                i += 1;
            }
            "--evidence-origin" => {
                a.evidence_origin = need(args, i, "--evidence-origin")?;
                i += 1;
            }
            "--timeout-s" => {
                a.timeout_s = need(args, i, "--timeout-s")?
                    .parse()
                    .map_err(|_| "--timeout-s 需为整数")?;
                i += 1;
            }
            "--screenshot-timeout-ms" => {
                a.screenshot_timeout_ms = need(args, i, "--screenshot-timeout-ms")?
                    .parse()
                    .map_err(|_| "--screenshot-timeout-ms must be an integer")?;
                i += 1;
            }
            other => return Err(format!("无法识别的参数 '{other}'\n{USAGE}")),
        }
        i += 1;
    }
    if a.serial.is_none() {
        return Err(format!("缺 --serial\n{USAGE}"));
    }
    let mode = QuestMode::parse(&a.mode)?;
    if mode == QuestMode::Interact {
        return Err("capture --mode 只认 auto|navigate|dialogue".into());
    }
    if a.serial
        .as_deref()
        .is_some_and(|s| s.starts_with("hdc:") || s.starts_with("ios:"))
    {
        return Err("capture 只支持 Android/adb 设备".into());
    }
    if !matches!(a.evidence_origin.as_str(), "device" | "synthetic") {
        return Err("--evidence-origin 只认 device|synthetic".into());
    }
    if !(1..=86400).contains(&a.timeout_s) {
        return Err("--timeout-s 必须为 1..86400".into());
    }
    crate::capture_worker::validate_screenshot_timeout_ms(a.screenshot_timeout_ms)?;
    if a.recover.is_some() && a.out.is_some() {
        return Err("--recover 使用已有session，不与 --out 混用".into());
    }
    if a.frames == 0 {
        return Err("--frames 必须 >= 1".into());
    }
    if a.ready_only {
        if frames_explicit && a.frames != 1 {
            return Err("--ready-only 只取1帧，不与 --frames 非1同时使用".into());
        }
        a.frames = 1;
        a.shutdown = false;
    }
    // 安全阀缺省 = 目标帧数的 4 倍步数: 状态过滤与冻结去重都会消耗步数
    if a.max_steps == 0 {
        a.max_steps = a.frames.saturating_mul(4).max(20);
    }
    Ok(a)
}

/// 64x36 灰度缩略图: 冻结判定用
fn thumb(img: &image::DynamicImage) -> Vec<u8> {
    img.resize_exact(64, 36, image::imageops::FilterType::Triangle)
        .to_luma8()
        .into_raw()
}

/// 两张缩略图的平均绝对差 (纯函数, 单测)
pub fn thumb_diff(a: &[u8], b: &[u8]) -> f32 {
    if a.is_empty() || a.len() != b.len() {
        return 255.0;
    }
    a.iter()
        .zip(b)
        .map(|(x, y)| (*x as i32 - *y as i32).abs() as f32)
        .sum::<f32>()
        / a.len() as f32
}

fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(bytes);
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

fn state_name(s: GenshinState) -> &'static str {
    match s {
        GenshinState::TitleScreen => "title",
        GenshinState::DialogueText => "dialogue",
        GenshinState::DialogueChoice => "choice",
        GenshinState::InteractionPrompt => "interaction",
        GenshinState::Climbing => "climbing",
        GenshinState::OpenWorldExplore => "explore",
        GenshinState::LoadingOrCutscene => "loading",
    }
}

pub fn run_capture(args: &[String]) -> i32 {
    let mut args = std::iter::once("capture".to_string()).chain(args.iter().cloned()).collect();
    if let Err(e) = crate::devlock::strip_lock_args(&mut args) {
        eprintln!("{e}");
        return 2;
    }
    args.remove(0);
    if args.iter().any(|s| matches!(s.as_str(), "--help" | "-h")) {
        emit(&format!("{USAGE}\n"));
        return 0;
    }
    let mut a = match parse_args(&args) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{e}");
            return 2;
        }
    };
    let session = if let Some(dir) = &a.recover {
        crate::capture_session::Session::resume(
            std::path::Path::new(dir),
            a.serial.as_deref().unwrap(),
        )
    } else {
        crate::devlease::Lease::target(a.serial.as_deref(), "capture").and_then(|lease| {
            a.serial = Some(lease.serial.clone());
            crate::capture_session::Session::new(
                lease,
                a.out.as_deref(),
                a.shutdown,
                &a.evidence_origin,
            )
        })
    };
    let session = match session {
        Ok(s) => s,
        Err(e) => {
            emit(&format!("{}\n", json!({"v":1,"ok":false,"error":e})));
            return 2;
        }
    };
    let result = if a.recover.is_some() {
        Ok((json!({"v":1,"ok":true,"recovery_only":true}), 0))
    } else {
        a.out = Some(session.dir.join("frames").to_string_lossy().into());
        session
            .prepare()
            .and_then(|_| session.run(&a, Duration::from_secs(a.timeout_s)))
    };
    let recovery = session.finish(result.as_ref().err().map(String::as_str));
    let (mut report, mut code) =
        result.unwrap_or_else(|e| (json!({"v":1,"ok":false,"error":e}), 2));
    report["session"] = json!(session.dir);
    match recovery {
        Ok(v) => report["recovery"] = v,
        Err(e) => {
            report["recovery"] = json!({"status":"required","error":e});
            report["ok"] = json!(false);
            code = 2;
        }
    }
    let path = session.dir.join(format!(
        "report-{}-{}.json",
        std::process::id(),
        chrono::Utc::now().timestamp_nanos_opt().unwrap()
    ));
    report["report_path"] = json!(path);
    if let Err(e) = crate::confirmation_transport::new_file(&path, report.to_string().as_bytes()) {
        eprintln!("capture report persistence failed: {e}");
        code = 2;
        report["report_error"] = json!(e);
        report["ok"] = json!(false);
    }
    if a.json {
        emit(&format!(
            "{}\n",
            serde_json::to_string_pretty(&report).unwrap()
        ));
    } else {
        emit(&format!(
            "capture: ok={}, recovery={}, session={}\n",
            report["ok"],
            report["recovery"]["status"],
            session.dir.display()
        ));
    }
    code
}

/// Internal entry: inherited worker authority and liveness must precede device work.
pub(crate) fn run_worker(args: &[String]) -> i32 {
    if let Err(e) = crate::lease_command::worker_guard() {
        eprintln!("{e}");
        return 90;
    }
    if args.len() != 1 {
        eprintln!("capture worker requires one session");
        return 90;
    }
    let dir = match std::path::Path::new(&args[0]).canonicalize() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("{e}");
            return 90;
        }
    };
    let result: Result<(Value, i32), String> = (|| {
        let a: CaptureArgs = serde_json::from_slice(&crate::confirmation_transport::bounded(
            &dir.join("request.json"),
            65536,
        )?)
        .map_err(|e| e.to_string())?;
        let state = crate::capture_session::read_state(&dir)?;
        crate::capture_session::validate(
            &state,
            a.serial.as_deref().ok_or("missing capture serial")?,
        )?;
        if a.out.as_deref() != dir.join("frames").to_str() || state["shutdown"] != a.shutdown {
            return Err("capture request differs from session".into());
        }
        let worker = crate::capture_worker::Worker::open(&dir, a.screenshot_timeout_ms)?;
        let captured = capture(&a, &worker, || worker.png());
        let closed = worker.close();
        let captured = captured?;
        closed?;
        Ok(captured)
    })();
    let (mut value, code) = match result {
        Ok((report, code)) => (json!({"report":report,"error":null}), code),
        Err(e) => (json!({"report":null,"error":e}), 2),
    };
    value["schema"] = json!("phonefarm/capture-worker-result@1");
    // A missing authority is recorded as null; the parent then refuses the
    // completion and keeps recovery required instead of losing the result.
    value["token"] = json!(std::env::var("PHONEFARM_WORKER_TOKEN").ok());
    value["serial"] = json!(std::env::var("PHONEFARM_WORKER_SERIAL").ok());
    value["exit_code"] = json!(code);
    if let Err(e) = crate::confirmation_transport::new_file(
        &dir.join("worker-result.json"),
        value.to_string().as_bytes(),
    ) {
        eprintln!("{e}");
        return 2;
    }
    code
}

fn capture(
    a: &CaptureArgs,
    phone: &dyn QuestDevice,
    mut read_png: impl FnMut() -> Result<Vec<u8>, String>,
) -> Result<(Value, i32), String> {
    let t_all = Instant::now();
    let serial = a.serial.clone().unwrap();
    let out_dir = std::path::PathBuf::from(a.out.as_ref().ok_or("missing capture output")?);
    let mode = QuestMode::parse(&a.mode)?;
    let cfg = QuestConfig {
        mode: a.mode.clone(),
        max_seconds: u64::MAX,
        auto_choice: true,
        auto_shutdown: false,
    };
    let mut agent = GenshinQuestAgent::new(phone, cfg);
    if !a.ready_only {
        progress("拉起游戏并等待进入大世界 (插件 ensure_game_ready)");
        agent.ensure_game_ready()?;
    }
    phone.check()?;

    let manifest_path = out_dir.join("manifest.jsonl");
    let mut manifest = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&manifest_path)
        .map_err(|e| format!("打不开 manifest {}: {e}", manifest_path.display()))?;
    let mut kept = 0u32;
    let mut steps = 0u32;
    let mut dups = 0u32;
    let mut portrait = 0u32;
    let mut grab_fail = 0u32;
    let mut skipped: BTreeMap<&'static str, u32> = BTreeMap::new();
    let mut no_hud = 0u32;
    let mut backs = 0u32;
    let mut stuck_state: Option<&'static str> = None;
    let mut stuck_n = 0u32;
    let mut prev_thumb: Vec<u8> = Vec::new();
    let mut observations = 0u32;
    if a.ready_only {
        // 两个终点都留在有限观察路径；不因未命中或默认目标而进入巡航。
        for _ in 0..READY_ONLY_TRIES {
            std::thread::sleep(Duration::from_millis(a.settle_ms));
            observations += 1;
            let png = read_png()?;
            let Ok(img) = image::load_from_memory(&png) else {
                grab_fail += 1;
                continue;
            };
            let (w, h) = (img.width(), img.height());
            if w < h {
                portrait += 1;
                continue;
            }
            let (state, _) = classify_state(&img);
            let (minimap, combat) = hud_present(&img);
            if state != GenshinState::OpenWorldExplore || !(minimap && combat) {
                progress(&format!(
                    "ready-only: 状态 {} (小地图={minimap} 技能栏={combat}), 再看",
                    state_name(state)
                ));
                continue;
            }
            let file = format!("frame_{kept:05}.png");
            std::fs::write(out_dir.join(&file), &png).map_err(|e| format!("写不了 {file}: {e}"))?;
            let row = json!({
                "i": kept, "file": file, "ts_ms": chrono::Utc::now().timestamp_millis(), "step": steps,
                "state": state_name(state), "w": w, "h": h, "bytes": png.len(), "sha256": sha256_hex(&png),
                "segment": kept / SEGMENT_FRAMES, "diff_prev": Value::Null, "ready_only": true,
            });
            writeln!(manifest, "{row}").map_err(|e| format!("manifest 写入失败: {e}"))?;
            prev_thumb = thumb(&img);
            kept += 1;
            progress(&format!(
                "ready-only: 已在大世界探索态, 保留 {file} ({w}x{h}), 不走位"
            ));
            break;
        }
    }
    while !a.ready_only && kept < a.frames && steps < a.max_steps {
        steps += 1;
        // 巡航一步: 决策完全在插件里, 这里只负责在两步之间抓帧
        agent.step()?;
        phone.check()?;
        std::thread::sleep(Duration::from_millis(a.settle_ms));
        let png = read_png()?;
        let Ok(img) = image::load_from_memory(&png) else {
            grab_fail += 1;
            continue;
        };
        let (w, h) = (img.width(), img.height());
        if w < h {
            portrait += 1;
            progress(&format!(
                "第{steps}步: 竖屏画面 {w}x{h} (系统弹窗/非游戏), 跳过"
            ));
            continue;
        }
        let (state, _) = classify_state(&img);
        let (minimap, combat) = hud_present(&img);
        if state != GenshinState::OpenWorldExplore || !(minimap && combat) {
            let name = if state == GenshinState::OpenWorldExplore {
                no_hud += 1;
                "explore_no_hud"
            } else {
                state_name(state)
            };
            *skipped.entry(name).or_default() += 1;
            progress(&format!(
                "第{steps}步: 状态 {name} (小地图={minimap} 技能栏={combat}), 跳过"
            ));
            // 同一非探索态卡住 → 发 BACK 关弹窗 (公告/签到类界面插件的 A 键推不动), 有界且计数
            if stuck_state == Some(name) {
                stuck_n += 1;
            } else {
                stuck_state = Some(name);
                stuck_n = 1;
            }
            if stuck_n >= STUCK_STEPS_BACK && mode.allows_capture_back(state, true) {
                backs += 1;
                stuck_n = 0;
                progress(&format!("第{steps}步: {name} 连续 {STUCK_STEPS_BACK} 步未变, 发 BACK 键尝试关闭弹窗 (第{backs}次)"));
                phone.shell_write("input keyevent 4", 3000)?;
            }
            continue;
        }
        stuck_state = None;
        stuck_n = 0;
        let th = thumb(&img);
        let diff = thumb_diff(&prev_thumb, &th);
        if !prev_thumb.is_empty() && diff < DUP_THRESH {
            dups += 1;
            progress(&format!(
                "第{steps}步: 与上一保留帧差 {diff:.2} < {DUP_THRESH}, 画面冻结, 跳过"
            ));
            continue;
        }
        let file = format!("frame_{kept:05}.png");
        std::fs::write(out_dir.join(&file), &png).map_err(|e| format!("写不了 {file}: {e}"))?;
        let row = json!({
            "i": kept, "file": file, "ts_ms": chrono::Utc::now().timestamp_millis(), "step": steps,
            "state": state_name(state), "w": w, "h": h, "bytes": png.len(), "sha256": sha256_hex(&png),
            "segment": kept / SEGMENT_FRAMES, "diff_prev": if prev_thumb.is_empty() { Value::Null } else { json!(diff) },
        });
        writeln!(manifest, "{row}").map_err(|e| format!("manifest 写入失败: {e}"))?;
        prev_thumb = th;
        kept += 1;
        progress(&format!(
            "第{steps}步: 保留 {file} ({w}x{h}, {} KB, 差 {diff:.1}) 进度 {kept}/{}",
            png.len() / 1024,
            a.frames
        ));
    }
    let ok = kept >= a.frames;
    let report = json!({
        "v": 1, "ok": ok, "serial": serial, "out": out_dir.to_string_lossy(), "manifest": manifest_path.to_string_lossy(),
        "frames": kept, "target": a.frames, "steps": steps, "max_steps": a.max_steps,
        "skipped": skipped.values().sum::<u32>(), "skipped_by_state": skipped, "duplicates": dups,
        "portrait": portrait, "grab_failed": grab_fail, "explore_without_hud": no_hud, "back_presses": backs, "segment_frames": SEGMENT_FRAMES, "dup_thresh": DUP_THRESH,
        "mode": a.mode, "shutdown": a.shutdown, "ready_only": a.ready_only, "wall_s": t_all.elapsed().as_secs_f64(),
        "ready_observations": observations,
        "stop_reason": if ok { "target_reached" } else if a.ready_only { "not_ready" } else { "max_steps" },
    });
    Ok((report, if ok { 0 } else { 1 }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn thumb_diff_forms() {
        assert_eq!(thumb_diff(&[], &[1, 2]), 255.0);
        assert_eq!(thumb_diff(&[1, 2], &[1, 2, 3]), 255.0);
        assert_eq!(thumb_diff(&[10, 20, 30], &[10, 20, 30]), 0.0);
        assert!((thumb_diff(&[10, 20, 30], &[13, 20, 33]) - 2.0).abs() < 1e-6);
    }

    #[test]
    fn args_forms() {
        let a = parse_args(&["--serial".into(), "X".into()]).unwrap();
        assert_eq!(
            (a.frames, a.max_steps, a.settle_ms, a.shutdown, a.json),
            (200, 800, 800, true, false)
        );
        let a = parse_args(&[
            "--serial".into(),
            "X".into(),
            "--frames".into(),
            "3".into(),
            "--no-shutdown".into(),
            "--json".into(),
        ])
        .unwrap();
        assert_eq!(
            (a.frames, a.max_steps, a.shutdown, a.json),
            (3, 20, false, true)
        );
        assert!(!a.ready_only, "缺省不是 ready-only");
        let a = parse_args(&[
            "--serial".into(),
            "X".into(),
            "--frames".into(),
            "1".into(),
            "--ready-only".into(),
            "--no-shutdown".into(),
        ])
        .unwrap();
        assert!(a.ready_only && !a.shutdown && a.frames == 1);
        let ready = parse_args(&["--serial".into(), "X".into(), "--ready-only".into()]).unwrap();
        assert_eq!((ready.frames, ready.shutdown), (1, false));
        for extra in [vec!["--ready-only", "--frames", "2"], vec!["--mode", "typo"], vec!["--mode", "interact"]] {
            let mut args = vec!["--serial".into(), "X".into()];
            args.extend(extra.into_iter().map(String::from));
            assert!(parse_args(&args).is_err());
        }
        assert!(parse_args(&[]).is_err());
        assert!(parse_args(&["--serial".into(), "X".into(), "--bogus".into()]).is_err());
    }

    #[test]
    fn screenshot_timeout_defaults_bounds_and_request_roundtrip() {
        let defaults = parse_args(&["--serial".into(), "X".into()]).unwrap();
        assert_eq!(defaults.screenshot_timeout_ms, 15_000);
        for timeout in [1, 15_000, 45_000, 60_000] {
            let args = parse_args(&[
                "--serial".into(), "X".into(), "--ready-only".into(),
                "--timeout-s".into(), "120".into(),
                "--screenshot-timeout-ms".into(), timeout.to_string(),
            ]).unwrap();
            let request = serde_json::to_vec(&args).unwrap();
            let restored: CaptureArgs = serde_json::from_slice(&request).unwrap();
            assert_eq!(restored.screenshot_timeout_ms, timeout);
            assert_eq!(restored.timeout_s, 120);
        }
        for invalid in ["0", "60001", "-1", "1.5", "invalid", "18446744073709551616"] {
            assert!(parse_args(&[
                "--serial".into(), "X".into(),
                "--screenshot-timeout-ms".into(), invalid.into(),
            ]).is_err(), "{invalid}");
        }
        assert!(parse_args(&[
            "--serial".into(), "X".into(), "--screenshot-timeout-ms".into(),
        ]).is_err());
        let mut legacy = serde_json::to_value(defaults).unwrap();
        legacy.as_object_mut().unwrap().remove("screenshot_timeout_ms");
        let restored: CaptureArgs = serde_json::from_value(legacy).unwrap();
        assert_eq!(restored.screenshot_timeout_ms, 15_000);
    }

    #[test]
    fn ready_only_returns_without_starting_or_cruising() {
        struct ObserveOnly;
        impl QuestDevice for ObserveOnly {
            fn shell(&self, _: &str, _: u64) -> String { panic!("unexpected shell access") }
            fn screen_image(&self) -> Option<image::DynamicImage> { panic!("unexpected agent observation") }
            fn gamepad_press(&self, _: &str, _: u64) -> Result<(), String> { panic!("unexpected press") }
            fn gamepad_stick(&self, _: &str, _: f32, _: f32, _: u64) -> Result<(), String> { panic!("unexpected stick") }
            fn gamepad_wander(&self, _: u64) -> Result<(), String> { panic!("unexpected cruise") }
            fn gamepad_reset(&self) -> Result<(), String> { panic!("unexpected reset") }
            fn gamepad_close(&self) -> Result<(), String> { panic!("unexpected HID") }
        }
        let root = std::env::temp_dir().join(format!("pf-capture-policy-{}", std::process::id()));
        for ready in [false, true] {
            let dir = root.join(if ready { "ready" } else { "not-ready" });
            std::fs::create_dir_all(&dir).unwrap();
            let args = parse_args(&[
                "--serial".into(), "X".into(), "--ready-only".into(), "--settle-ms".into(), "0".into(),
                "--out".into(), dir.to_string_lossy().into_owned(),
            ]).unwrap();
            let mut img = image::RgbaImage::from_pixel(512, 256, image::Rgba([0, 0, 0, 255]));
            if ready {
                img = image::RgbaImage::from_pixel(512, 256, image::Rgba([80, 80, 80, 255]));
                for (xs, xe, ys, ye) in [(20, 60, 10, 40), (430, 450, 200, 225)] {
                    for x in xs..xe { for y in ys..ye { img.put_pixel(x, y, image::Rgba([255, 255, 255, 255])); } }
                }
            }
            let mut png = std::io::Cursor::new(Vec::new());
            image::DynamicImage::ImageRgba8(img).write_to(&mut png, image::ImageFormat::Png).unwrap();
            let png = png.into_inner();
            let mut reads = 0;
            let (report, code) = capture(&args, &ObserveOnly, || { reads += 1; Ok(png.clone()) }).unwrap();
            assert_eq!(code, if ready { 0 } else { 1 });
            assert_eq!(report["steps"], 0);
            assert_eq!(report["back_presses"], 0);
            assert_eq!(report["target"], 1);
            assert_eq!(report["frames"], if ready { 1 } else { 0 });
            assert_eq!(reads, if ready { 1 } else { READY_ONLY_TRIES });
            assert_eq!(report["ready_observations"], reads);
            assert_eq!(report["stop_reason"], if ready { "target_reached" } else { "not_ready" });
            if ready { assert_eq!(std::fs::read(dir.join("frame_00000.png")).unwrap(), png); }
        }
        std::fs::remove_dir_all(root).unwrap();
    }
}
