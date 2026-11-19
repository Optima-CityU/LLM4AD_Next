//! 灰档 loadop A/B 采集；未支持的 copyprobe 在设备动作前拒绝。
//!
//! 两臂**都挂层**, 唯一差别是属性: ctrl = 层+改写关 (只读档), knob = 层+改写开。
//! 要判的是**这条改写**值不值, 不是"挂层这件事"值不值; 两臂都挂层才能把层本身的
//! 开销消掉, 差值才干净地对应改写这一个改动。
//!
//! 每臂都要重启原神 —— loadOp 是 render pass **创建期**烘进去的, 属性也是
//! CreateInstance 时读的, 跑起来之后切不动。
//!
//! 原神里**不按 A 键、不按 BACK**: 全程只在登录页点一次"门", 负载只在画面空白处拖视角。

use serde_json::{json, Value};
use std::path::{Path, PathBuf};

/// 登录页那扇"门" (画面坐标, 点一次进游戏)
const GATE_X: u32 = 1342;
const GATE_Y: u32 = 651;
/// 大世界判定: 每帧 render pass 数 >= 25 (登录/加载 ≈ 12-17, 大世界 ≈ 30, 余量很大)
const IN_WORLD_PPF: i64 = 25;

/// 二进制安全的 `adb exec-out screencap` (PNG 之外的原始 RGBA)。
pub(crate) fn screencap_raw_bin(serial: &str) -> Vec<u8> {
    crate::fd_lock::transport_command(crate::eval::adb_path())
        .args(["-s", serial, "exec-out", "screencap"])
        .output()
        .map(|o| o.stdout)
        .unwrap_or_default()
}

const LOADOP_PROTOCOL: &str = "gray_ab_loadop_whole_round_charge_v1";
const LOADOP_SECONDS: u64 = 30;
pub(crate) const USAGE: &str = "用法: phonefarm gray-ab [--pairs N] [--mode loadop] [--serial S] --out <目录> [--json] [--evidence-origin device|synthetic]\n       phonefarm gray-ab recover --session DIR --serial S [--json]\ncopyprobe 当前不支持执行。";

fn save_json(path: &Path, value: &Value) -> Result<(), String> {
    crate::confirmation_transport::new_file(path, &serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?)
}

fn recover_loadop(args: &[String]) -> Result<(Value, i32), String> {
    let (mut dir, mut serial) = (None, None);
    let mut it = args.iter();
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--session" => dir = it.next(),
            "--serial" => serial = it.next(),
            "--json" => {},
            _ => return Err(format!("unknown gray-ab recover argument: {arg}")),
        }
    }
    let parent = crate::gray_eval_session::Parent::resume_loadop(
        Path::new(dir.ok_or("missing --session")?), serial.ok_or("missing --serial")?)?;
    let report = parent.finish(Some("explicit same-context recovery; original collection is unchanged"))?;
    let code = if report["status"] == "reported_complete" { 0 } else { 2 };
    Ok((report, code))
}

fn owned_screen(parent: &crate::gray_eval_session::Parent, path: &Path, plan: &crate::gray_script::Plan) -> Result<(u32, u32), String> {
    parent.base.screenshot(path)?;
    let raw = crate::frameaudit::png_raw(path)?;
    let (w, h, _) = crate::framecheck::parse_screencap(&raw)?;
    plan.validate_screen(w, h)?;
    Ok((w, h))
}

fn scene_counts(marker: &Value) -> Option<(u64, u64)> {
    // Current layer evidence uses readonly_stats; older artifacts used the root.
    let stats = if marker["readonly_stats"].is_object() { &marker["readonly_stats"] } else { marker };
    Some((stats["frames"].as_u64()?, stats["render_pass_begins"].as_u64()?))
}

fn loadop_world(parent: &crate::gray_eval_session::Parent, out: &Path, plan: &crate::gray_script::Plan) -> Result<(), String> {
    use std::time::{Duration, Instant};
    let start = Instant::now();
    loop {
        if !parent.base.target_alive()? { return Err("target exited before layer readiness".into()); }
        if parent.base.marker()?.as_ref().and_then(scene_counts).is_some() { break; }
        if start.elapsed().as_secs() >= 80 { return Err("current layer marker unavailable".into()); }
        std::thread::sleep(Duration::from_secs(2));
    }
    parent.base.narrow()?;
    std::thread::sleep(Duration::from_secs(30));
    let (w, h) = owned_screen(parent, &out.join("gate.png"), plan)?;
    if GATE_X >= w || GATE_Y >= h { return Err("scene entry coordinate outside actual screenshot".into()); }
    parent.base.tap(GATE_X, GATE_Y)?;
    for _ in 0..18 {
        let before = parent.base.marker()?.as_ref().and_then(scene_counts).ok_or("scene counters absent")?;
        std::thread::sleep(Duration::from_secs(10));
        if !parent.base.target_alive()? { return Err("target exited while waiting for world".into()); }
        let after = parent.base.marker()?.as_ref().and_then(scene_counts).ok_or("scene counters absent")?;
        if let (Some(df), Some(dr)) = (after.0.checked_sub(before.0), after.1.checked_sub(before.1)) {
            if df > 0 && dr / df >= IN_WORLD_PPF as u64 { return Ok(()); }
        }
    }
    Err("world scene counter condition not reached".into())
}

fn loadop_power(run: &Value, charge: &Value) -> Result<Value, String> {
    let env = &run["env"];
    let valid = env["on_battery"] == true && env["power_valid"] == true
        && env["power_window_verified"] == true && env["trace_window_verified"] == true
        && env["n_samples"].as_u64().is_some_and(|n| n > 0);
    let mut snap = crate::perfsrc::PerfSnapshot::empty("android_sysfs");
    snap.sample_count = env["n_samples"].as_u64().unwrap_or(0) as usize;
    snap.power_watt = if valid { env["power_w_mean"].as_f64() } else { None };
    if env["thermal_window_verified"] == true { snap.soc_temp_c = env["soc_temp_mean_c"].as_f64(); }
    for (field, reason) in [
        ("fps", "frame timing is recorded separately in summary.json"),
        ("frame_time_mean_ms", "frame timing is recorded separately in summary.json"),
        ("fps_p95_ms", "frame timing is recorded separately in summary.json"),
        ("gpu_temp_c", "not sampled by the owned environment sampler"),
        ("battery_temp_c", "not sampled by the owned environment sampler"),
    ] { snap.unavailable.push(crate::perfsrc::Unavailable { field: field.into(), reason: reason.into() }); }
    if snap.soc_temp_c.is_none() { snap.unavailable.push(crate::perfsrc::Unavailable { field: "soc_temp_c".into(), reason: "complete thermal window unavailable".into() }); }
    if snap.power_watt.is_none() { snap.unavailable.push(crate::perfsrc::Unavailable { field: "power_watt".into(), reason: "battery discharge or complete sampler/trace window unverified".into() }); }
    snap.meta = Some(std::collections::BTreeMap::from([
        ("power_rail".into(), "battery".into()), ("protocol".into(), LOADOP_PROTOCOL.into()),
        ("on_battery".into(), (env["on_battery"] == true).to_string()),
        ("battery_status".into(), env["battery_status"].as_str().unwrap_or("unknown").into()),
        ("scope".into(), "whole sampler window including lead/tail; covers trace capture window".into()),
    ]));
    let mut report = serde_json::to_value(snap).map_err(|e| e.to_string())?;
    report["protocol"] = json!(LOADOP_PROTOCOL);
    report["window"] = json!({"duration_s":env["sample_duration_s"],"interval_s":env["sample_interval_s"],
        "first_uptime":env["sample_first_uptime"],"last_uptime":env["sample_last_uptime"],
        "trace_window_verified":env["trace_window_verified"],"power_window_verified":env["power_window_verified"]});
    report["charging"] = charge.clone();
    report["legacy_charge_header_used"] = json!(false);
    Ok(report)
}

fn collect_loadop(lease: crate::devlease::Lease, out: &Path, pairs: u32, plan: &crate::gray_script::Plan, origin: &str) -> Result<(Value, i32), String> {
    let serial = lease.serial.clone();
    std::fs::create_dir_all(out).map_err(|e| e.to_string())?;
    let out = out.canonicalize().map_err(|e| e.to_string())?;
    let out = out.as_path();
    // Refuse old arm directories before any device mutation, even for a fresh session.
    for i in 1..=pairs { for prefix in ["ctrl", "knob"] {
        match std::fs::symlink_metadata(out.join(format!("{prefix}{i}"))) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {},
            Err(e) => return Err(e.to_string()),
            Ok(_) => return Err(format!("existing {prefix}{i} evidence; use a new output directory")),
        }
    }}
    let base = crate::gray_session::Session::new_loadop(lease, out, crate::plugins::genshin::PACKAGE, origin)?;
    let parent = crate::gray_eval_session::Parent::new_loadop(base, &json!({"protocol":LOADOP_PROTOCOL,"pairs":pairs}), plan)?;
    let mut arms = Vec::new();
    let collected = (|| -> Result<(), String> {
        let caps = parent.capabilities()?;
        if caps.root != Some(true) || caps.trace_available != Some(true) { return Err("root and isolated ftrace are required".into()); }
        parent.base.prepare()?;
        parent.base.install_loadop(None)?;
        parent.base.eval_stop_target()?;
        parent.deploy()?;
        parent.prepare_charging()?;
        for i in 1..=pairs { for (prefix, rewrite) in [("ctrl", false), ("knob", true)] {
            let label = format!("{prefix}{i}"); let dir = out.join(&label);
            let result = (|| -> Result<Value, String> {
                std::fs::create_dir(&dir).map_err(|e| e.to_string())?;
                parent.admission_ready()?;
                parent.stage(&label)?;
                parent.base.restart_loadop(&label, rewrite)?;
                loadop_world(&parent, &dir, plan)?;
                owned_screen(&parent, &dir.join("scene.png"), plan)?;
                let before = parent.charging_observation()?;
                let run = crate::eval::run_owned_gray_script(&parent, plan, &label, &dir)?;
                let after = parent.charging_observation()?;
                let marker = parent.base.marker()?.ok_or("current arm layer evidence absent")?;
                save_json(&dir.join("knobs_layer_out.json"), &marker)?;
                let power = loadop_power(&run, &json!({"before":before,"after":after}))?;
                save_json(&dir.join("power.json"), &power)?;
                if power["power_watt"].is_null() { return Err("complete battery power window unavailable; raw evidence retained".into()); }
                parent.admission_ready()?;
                let spf = run["summary"]["submits_per_frame"].as_i64();
                let p50 = run["summary"]["frame_p50"].as_f64();
                let advisory = spf == Some(2) && p50.is_some_and(|v| (15.5..=18.0).contains(&v) || (31.0..=36.0).contains(&v));
                Ok(json!({"run":dir.join("run.json"),"power":dir.join("power.json"),"layer":dir.join("knobs_layer_out.json"),
                    "scene_counter_check":true,"legacy_timing_advisory_ok":advisory,"rewrite_effective":marker["effective"]}))
            })();
            arms.push(json!({"arm":label,"status":if result.is_ok(){"collected"}else{"failed"},"rewrite":rewrite,"passdump":0,
                "evidence":result.as_ref().ok(),"error":result.as_ref().err()}));
            result?;
        }}
        Ok(())
    })();
    let collection = json!({"schema":"phonefarm/gray-ab-loadop-attempt@1","session":parent.base.dir,
        "protocol":LOADOP_PROTOCOL,"collection_ok":collected.is_ok(),"arms":arms,"error":collected.as_ref().err()});
    let persistence_error = save_json(&parent.base.dir.join("gray-ab-collection.json"), &collection).err();
    let collection_ok = collected.is_ok();
    let error = match (collected.err(), persistence_error.as_deref()) {
        (Some(e), Some(p)) => Some(format!("{e}; collection persistence: {p}")),
        (None, Some(p)) => Some(format!("collection persistence: {p}")),
        (e, None) => e,
    };
    let recovery = parent.finish(error.as_deref()).unwrap_or_else(|e| json!({"status":"required","error":e,"session":parent.base.dir}));
    let mut code = if persistence_error.is_some() || recovery["status"] != "reported_complete" { 2 } else if error.is_some() { 1 } else { 0 };
    let path = out.join(format!("gray-ab-{}-{}.json", std::process::id(), chrono::Utc::now().timestamp_nanos_opt().unwrap()));
    let mut report = json!({"schema":"phonefarm/gray-ab-collection@1","serial":serial,"mode":"loadop","protocol":LOADOP_PROTOCOL,
        "session":parent.base.dir,"evidence_origin":origin,"collection_ok":collection_ok,"arms":arms,"error":error,"persistence_error":persistence_error,
        "collection":parent.base.dir.join("gray-ab-collection.json"),"recovery":recovery,"recovery_status":recovery["status"],
        "job_recovery_complete":false,"game_state_restored":false,"report_path":path});
    if let Err(error) = save_json(&path, &report) {
        code = 2;
        report["report_path"] = Value::Null;
        report["report_write_error"] = json!({"path":path,"error":error});
    }
    Ok((report, code))
}

fn emit_owned(result: Result<(Value, i32), String>) -> i32 {
    match result {
        Ok((report, code)) => { println!("{}", serde_json::to_string_pretty(&report).unwrap()); code },
        Err(error) => { eprintln!("{error}"); println!("{}", json!({"error":error,"recovery_status":"not_started_or_unknown"})); 2 },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loadop_power_requires_discharge_and_a_bound_window() {
        let mut run = json!({"env":{"on_battery":true,"power_valid":true,"power_window_verified":true,
            "trace_window_verified":true,"n_samples":20,"power_w_mean":6.0,
            "sample_duration_s":40,"sample_interval_s":2,"sample_first_uptime":98,"sample_last_uptime":136}});
        let good = loadop_power(&run, &json!({"mode":"observed_no_mutation"})).unwrap();
        assert_eq!(good["power_watt"], 6.0);
        assert_eq!(good["sample_count"], 20);
        assert_eq!(good["window"]["duration_s"], 40);
        assert!(good["fps"].is_null());
        run["env"]["trace_window_verified"] = json!(false);
        assert!(loadop_power(&run, &Value::Null).unwrap()["power_watt"].is_null());
        run["env"]["trace_window_verified"] = json!(true);
        run["env"]["on_battery"] = json!(false);
        assert!(loadop_power(&run, &Value::Null).unwrap()["power_watt"].is_null());
    }
}

pub fn run_gray_ab(args: &[String]) -> i32 {
    if args.first().is_some_and(|a| a == "recover") { return emit_owned(recover_loadop(&args[1..])); }
    let mut pairs = 5u32;
    let mut mode = "loadop".to_string();
    let mut serial = std::env::var("SERIAL").unwrap_or_else(|_| "91253241019A".into());
    let mut out = PathBuf::new();
    let mut origin = "device".to_string();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--pairs" => match it.next().and_then(|v| v.parse().ok()) {
                Some(n) => pairs = n,
                None => { eprintln!("--pairs requires a positive integer"); return 2; },
            },
            "--mode" => mode = it.next().cloned().unwrap_or_else(|| "loadop".into()),
            "--serial" => serial = it.next().cloned().unwrap_or(serial),
            "--out" => out = it.next().map(PathBuf::from).unwrap_or_default(),
            "--json" => {},
            "--evidence-origin" => origin = it.next().cloned().unwrap_or_default(),
            "--help" | "-h" => { println!("{USAGE}"); return 0; },
            other => {
                eprintln!("不认识的参数 {other}\n{USAGE}");
                return 2;
            }
        }
    }
    if !matches!(mode.as_str(), "loadop" | "copyprobe") {
        eprintln!("--mode 仅 loadop 可执行；copyprobe 当前不支持，其他模式未知");
        return 2;
    }
    if let Some(rejection) = crate::graylayer::mode_admission_rejection(&mode, false) {
        println!("{}", rejection);
        return 2;
    }
    if out.as_os_str().is_empty() || pairs == 0 || !matches!(origin.as_str(), "device" | "synthetic") {
        eprintln!("gray-ab requires --out, positive --pairs and valid --evidence-origin"); return 2;
    }
    emit_owned((|| {
        let path = crate::eval::resolve_path("knobs/gray/workload_spin_touch_v1.json").ok_or("finite spin-touch workload unavailable")?;
        let input: Value = serde_json::from_slice(&crate::confirmation_transport::bounded(&path, 1024*1024)?).map_err(|e| e.to_string())?;
        let plan = crate::gray_script::parse(&input, LOADOP_SECONDS)?;
        let lease = crate::devlease::Lease::target(Some(&serial), "gray-ab")?;
        collect_loadop(lease, &out, pairs, &plan, &origin)
    })())
}
