//! 评测 kind=whitebox: 白盒载体 (Megacity) 的「改引擎设置」旋钮 —— 阶段 3 的第三层。
//!
//! 两臂唯一的区别是启动 extras: 对照臂用 `workload.launch.extras` (基准), 改写臂在它后面
//! 追加 `payload.knobs` 里的白盒旋钮 (`--es msaa 1` 之类, 旋钮清单见
//! https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/MEGACITY_WHITEBOX_KNOBS.md 与 loop_v1/carriers/megacity/contract/launch.json)。
//! 启动、等标记、采集、功耗、等冷、统计判定全部复用 eval 的 workload.launch 那套
//! (#117), 这里只加白盒特有的三件事:
//!
//! 1. **每臂核对旋钮真的生效**: harness 把每个旋钮的想要值 / 改法 / 回读实际值写进
//!    `megacity_out.json` 的 `knobs`。改写臂里有任一旋钮的改法不是 prop / field / SetActive,
//!    或回读值对不上 → 整组 ABORT (量的不是这组设置); 对照臂的 `knobs` 必须是空的。
//! 2. **画面比对**: 用 `workload.launch.static_extras` (相机不动的 city_static) 分别按
//!    对照 / 改写 / 对照 启动, 同位置截图, 改写对对照算 PSNR, 门槛同 gray
//!    ([`GRAY_MIN_PSNR_DB`], 可由 `payload.image_check.min_psnr_db` 覆盖); 对照对对照给噪声底。
//! 3. **画面必然变化的旋钮只量上限**: msaa / feat_off / shadowres 这类改了画面一定变,
//!    结果里标 `measure_only`, 状态不会是 PASS —— 它们回答的是「这块浪费值多少」, 不是可采纳的优化。

use crate::eval::{
    adb, append_dropped_reason, arm_fan_off, consistency_note, fan_mismatch, fan_still_on, settle_pair, wait_charge_settled, aggregate_metrics, arm_cool_timeout, arm_wait_cool, base_report, cool_summary, judge_runs, push_tools,
    run_one_launch, run_soc_temp_max, start_with_extras, su, unavailable, wait_launch_window, launch_spec,
    psnr_rgba, adb_ok, GRAY_MIN_PSNR_DB, DEV_TMP,
};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::Path;

/// harness 认的旋钮 (与 contract/launch.json 同步; 不收凭空发明的)
pub(crate) const WHITEBOX_KNOBS: [&str; 8] =
    ["msaa", "store_opt", "feat_off", "fog_event", "ssao_src", "ssao_after_opaque", "shadowres", "depth_priming"];
/// 改了画面就一定变的旋钮: 只量上限, 不作为可采纳的优化
pub(crate) const WHITEBOX_QUALITY_CHANGING: [&str; 3] = ["msaa", "feat_off", "shadowres"];
/// 被测窗口采完后, 等 harness 走完路线、落 megacity_out.json 的上限
const OUT_TIMEOUT_S: u64 = 300;
const OUT_FILE: &str = "megacity_out.json";

/// payload.knobs → 校验过的 (旋钮, 值) 与拼好的 extras。值只许安全字符 —— 它要拼进 am start 的命令行。
pub(crate) fn knob_extras(payload: &Value) -> Result<(BTreeMap<String, String>, String), String> {
    let obj = payload["knobs"].as_object().ok_or("kind=whitebox 的 payload.knobs 必须是对象")?;
    if obj.is_empty() {
        return Err("kind=whitebox 的 payload.knobs 至少 1 项".into());
    }
    let mut m = BTreeMap::new();
    for (k, v) in obj {
        if !WHITEBOX_KNOBS.contains(&k.as_str()) {
            return Err(format!("白盒旋钮 {k:?} 不在 harness 已实现的集合 {WHITEBOX_KNOBS:?} 里"));
        }
        let v = match v {
            Value::String(s) => s.clone(),
            Value::Number(n) => n.to_string(),
            Value::Bool(b) => (if *b { "1" } else { "0" }).into(),
            _ => return Err(format!("旋钮 {k} 的值必须是字符串/数字/布尔")),
        };
        if v.is_empty() || v.len() > 64 || !v.chars().all(|c| c.is_ascii_alphanumeric() || "_,.-".contains(c)) {
            return Err(format!("旋钮 {k} 的值 {v:?} 含不允许的字符 (只收字母数字与 _ , . -)"));
        }
        m.insert(k.clone(), v);
    }
    let extras = m.iter().map(|(k, v)| format!("--es {k} {v}")).collect::<Vec<_>>().join(" ");
    Ok((m, extras))
}

/// 本组旋钮里会必然改变画面的那些
pub(crate) fn quality_changing(knobs: &BTreeMap<String, String>) -> Vec<String> {
    knobs.keys().filter(|k| WHITEBOX_QUALITY_CHANGING.contains(&k.as_str())).cloned().collect()
}

/// 核对一臂的 megacity_out.json。want 为空 = 对照臂 (knobs 必须为空)。
/// 回逐旋钮的核对记录; 有任何一条没生效就 Err。
pub(crate) fn check_knobs(out: &Value, want: &BTreeMap<String, String>) -> Result<Vec<Value>, String> {
    let got = out["knobs"].as_object().cloned().unwrap_or_default();
    if out.get("knobs").is_none() {
        return Err("megacity_out.json 里没有 knobs 段: 装的不是 harness 0.2.0 及以上的包".into());
    }
    if want.is_empty() {
        return if got.is_empty() {
            Ok(vec![])
        } else {
            Err(format!("对照臂的 knobs 不是空的: {:?}", got.keys().collect::<Vec<_>>()))
        };
    }
    let mut rows = Vec::new();
    for (k, v) in want {
        let entries: Vec<(&String, &Value)> = got
            .iter()
            .filter(|(name, _)| *name == k || name.starts_with(&format!("{k}:")) || name.starts_with(&format!("{k}@")))
            .collect();
        if entries.is_empty() {
            return Err(format!("旋钮 {k}={v}: harness 没有记录 (没被处理)"));
        }
        for (name, e) in entries {
            let how = e["how"].as_str().unwrap_or("");
            let ok_how = ["prop", "field", "SetActive"].iter().any(|p| how.starts_with(p));
            if !ok_how {
                return Err(format!("旋钮 {name}={v} 没生效: 改法 {how:?}"));
            }
            rows.push(json!({"knob": name, "want": v, "how": how, "actual": e["actual"]}));
        }
        // 有直接回读的三个, 再对一次实际值 (枚举名不分大小写)
        if matches!(k.as_str(), "msaa" | "store_opt" | "shadowres") {
            let actual = out["graphics"][k.as_str()].as_str().unwrap_or("");
            if !actual.eq_ignore_ascii_case(v) {
                return Err(format!("旋钮 {k}: 想要 {v}, graphics.{k} 回读 {actual:?}"));
            }
        }
    }
    Ok(rows)
}

fn files_dir(pkg: &str) -> String {
    format!("/storage/emulated/0/Android/data/{pkg}/files")
}

/// 被测窗口之后等 harness 走完、落 out json, 读回来
fn wait_out_json(serial: &str, pkg: &str) -> Result<Value, String> {
    let t0 = std::time::Instant::now();
    loop {
        let got = su(serial, &format!("cat {}/{OUT_FILE} 2>/dev/null", files_dir(pkg)), 30);
        let body = crate::graylayer::GrayLayer::adb_stdout(&got).trim().to_string();
        if !body.is_empty() {
            if let Ok(v) = serde_json::from_str::<Value>(&body) {
                return Ok(v);
            }
        }
        if t0.elapsed().as_secs() > OUT_TIMEOUT_S {
            return Err(format!("等了 {OUT_TIMEOUT_S}s 没拿到 {OUT_FILE}: frames 设得太长, 或 harness 没走完"));
        }
        std::thread::sleep(std::time::Duration::from_secs(3));
    }
}

/// 静态场景截两张 (相隔 1 秒), 回 (宽, 高, 原始 RGBA)。每张都落盘成 PNG:
/// 2026-10-19 Megacity 那轮没存图, 事后想换指标重判都没有原始数据。
fn static_shots(serial: &str, pkg: &str, extras: &str, marker: Option<&str>, label: &str, outdir: &Path) -> Result<Vec<(u32, u32, Vec<u8>)>, String> {
    std::fs::create_dir_all(outdir).map_err(|e| e.to_string())?;
    start_with_extras(serial, pkg, extras, marker, &[OUT_FILE], outdir)?;
    let r = wait_launch_window(serial, pkg, label, marker);
    let shots = if r.is_ok() {
        std::thread::sleep(std::time::Duration::from_secs(3));
        let mut v = Vec::new();
        for _ in 0..2 {
            v.push(crate::grayab::screencap_raw_bin(serial));
            std::thread::sleep(std::time::Duration::from_secs(1));
        }
        v
    } else {
        vec![]
    };
    let _ = adb(serial, &["shell", &format!("am force-stop {pkg}")], 30);
    r?;
    let parsed: Vec<(u32, u32, Vec<u8>)> = shots
        .into_iter()
        .filter_map(|d| crate::frameaudit::parse_raw(&d).map(|(w, h, px)| (w as u32, h as u32, px.to_vec())))
        .collect();
    for (n, shot) in parsed.iter().enumerate() {
        crate::eval::save_png(&outdir.join(format!("shot{n}.png")), shot);
    }
    Ok(parsed)
}

pub(crate) fn whitebox_eval(req: &Value, serial: Option<&str>, evidence: &Path) -> Result<String, String> {
    let Some(serial) = serial else {
        return Err("kind=whitebox 需要真机: 请加 --serial (或设备唯一在线时省略)".into());
    };
    let mut report = base_report(req, "whitebox");
    let _ = std::fs::create_dir_all(evidence);
    let done = |r: &Value| Ok(serde_json::to_string_pretty(r).unwrap_or_default());
    let Some(launch) = launch_spec(req) else {
        report["verdict"]["reason"] = json!("kind=whitebox 需要 workload.launch (白盒载体靠启动参数换设置)");
        return done(&report);
    };
    let (knobs, kx) = match knob_extras(&req["payload"]) {
        Ok(x) => x,
        Err(e) => {
            report["verdict"]["reason"] = json!(e);
            return done(&report);
        }
    };
    let qc = quality_changing(&knobs) ;
    let measure_only = !qc.is_empty() || req["payload"]["measure_only"].as_bool().unwrap_or(false);
    // 画质策略 (judge/v1-scorecard): report 下画面判据只记差距, 性能照量照判
    let policy = crate::scorecard::ImagePolicy::from_request(req).unwrap_or(crate::scorecard::ImagePolicy::Gate);
    let mut gap_open: Vec<String> = Vec::new();
    let pkg = req["package"].as_str().unwrap_or("com.unity.megacity.metro").to_string();
    let base_extras = launch.extras.clone();
    let knob_full = format!("{base_extras} {kx}");
    let marker = launch.marker.clone();
    push_tools(serial)?;

    // ── 画面比对 (静态场景, 测量前做: 画面出错就不必量性能) ──
    let min_psnr = req["payload"]["image_check"]["min_psnr_db"].as_f64().unwrap_or(GRAY_MIN_PSNR_DB);
    let mut image = json!({"min_psnr_db": min_psnr, "static_extras": launch.static_extras});
    let mut image_fail: Option<String> = None;
    let mut iq_verdict: Option<crate::iqjudge::IqVerdict> = None;
    if let Some(se) = &launch.static_extras {
        let shot = |extras: &str, tag: &str| static_shots(serial, &pkg, extras, marker.as_deref(), tag, &evidence.join(format!("static_{tag}")));
        let c1 = shot(se, "ctrl1");
        let k = shot(&format!("{se} {kx}"), "knob");
        let c2 = shot(se, "ctrl2");
        match (c1, k, c2) {
            (Ok(c1), Ok(k), Ok(c2)) if !c1.is_empty() && !k.is_empty() && !c2.is_empty() => {
                let p = |a: &(u32, u32, Vec<u8>), b: &(u32, u32, Vec<u8>)| psnr_rgba(&a.2, &b.2).map(|x| (x * 1000.0).round() / 1000.0);
                let knob_vs_ctrl = [p(&k[0], &c1[0]), p(&k[0], &c2[0])];
                let ctrl_vs_ctrl = p(&c1[0], &c2[0]);
                let within = [c1.get(1).and_then(|x| p(&c1[0], x)), k.get(1).and_then(|x| p(&k[0], x))];
                // 画质判据 (稳定块 PSNR/SSIM + 细节能量, 都对原生自噪声): 对照 = 前后两次启动各 2 张 (共 4 张),
                // 候选 = 夹在中间那次启动的 2 张。全帧 PSNR 门槛分不出 MSAA 4→1 (见 iqjudge 模块注释)。
                let iq_cfg = crate::iqjudge::IqConfig::from_request(&req["payload"]["image_check"]);
                let to_shot = |s: &(u32, u32, Vec<u8>)| crate::iqjudge::Shot::from_rgba(s.0 as usize, s.1 as usize, &s.2, iq_cfg.tile);
                let iq_ctrl: Vec<_> = c1.iter().chain(c2.iter()).filter_map(to_shot).collect();
                let iq_knob: Vec<_> = k.iter().filter_map(to_shot).collect();
                let (iq, iq_rep) = crate::iqjudge::judge(&iq_ctrl, &iq_knob, &iq_cfg);
                image["iq"] = iq_rep;
                iq_verdict = Some(iq);
                image["psnr_knob_vs_ctrl"] = json!(knob_vs_ctrl);
                image["psnr_ctrl_vs_ctrl"] = json!(ctrl_vs_ctrl);
                image["psnr_within_1s"] = json!(within);
                // 噪声底 = 同设置重拍能到多少 (对照对对照 + 同臂相隔 1 秒重复拍)
                let floor = crate::eval::noise_floor_db(
                    std::iter::once(ctrl_vs_ctrl).chain(within.iter().copied()),
                );
                let gate = crate::eval::ImageGate::new(min_psnr, floor);
                // 判定用**最好**的那一次; 记录仍用最差的那一次 (口径与之前一致, 不改历史数据含义)
                let worst = knob_vs_ctrl.iter().flatten().cloned().reduce(f64::min);
                let best = crate::eval::noise_floor_db(knob_vs_ctrl.iter().copied());
                report["metrics"]["psnr_db"] = json!(worst);
                image["noise_floor_db"] = json!(floor);
                image["psnr_db_used"] = json!(best);
                match crate::eval::judge_image(&gate, best) {
                    crate::eval::ImageRuling::Pass(by) => {
                        image["gate"] = gate.gate(by);
                    }
                    crate::eval::ImageRuling::Fail(_reason) if measure_only => {
                        // 只量上限的旋钮本来就会改画面, 不按画面判; 噪声底照样记下来
                        image["gate"] = gate.gate("skipped_measure_only");
                    }
                    crate::eval::ImageRuling::Fail(reason) if policy.is_report() => {
                        image["gate"] = gate.gate("failed");
                        gap_open.push(format!("全帧 PSNR: {reason}"));
                    }
                    crate::eval::ImageRuling::Fail(reason) => {
                        image["gate"] = gate.gate("failed");
                        image_fail = Some(format!("{} (全帧 PSNR, image_policy=gate 不量性能): {reason}", crate::scorecard::IMAGE_GATE_PREFIX));
                    }
                }
            }
            (a, b, c) => {
                let errs: Vec<String> = [a.err(), b.err(), c.err()].into_iter().flatten().collect();
                let why = format!("静态场景截图失败: {}", if errs.is_empty() { "截到的图解析不了".into() } else { errs.join("; ") });
                if policy.is_report() {
                    // report: 画面没比成只记「判不了」, 性能照量
                    image["shot_error"] = json!(why);
                } else {
                    image_fail = Some(why);
                }
            }
        }
    } else {
        unavailable(&mut report["metrics"], "psnr_db", "workload.launch 没给 static_extras, 无静态画面可比");
    }
    // 画质指标: gate 下有指标在参考线外 = 不量性能 (与全帧 PSNR 同等处理); 算不了 = 照常量性能, 但不出 PASS
    let mut image_unverified: Option<String> = None;
    match &iq_verdict {
        Some(crate::iqjudge::IqVerdict::BeyondRef(r)) if !measure_only && policy.is_report() => {
            gap_open.push(format!("画质判据: {r}"));
        }
        Some(crate::iqjudge::IqVerdict::BeyondRef(r)) if !measure_only && image_fail.is_none() => {
            image_fail = Some(format!("{} (画质指标, image_policy=gate 不量性能): {r}", crate::scorecard::IMAGE_GATE_PREFIX));
        }
        Some(crate::iqjudge::IqVerdict::NotComputed(r)) => image_unverified = Some(r.clone()),
        None if image_fail.is_none() && launch.static_extras.is_some() => {
            image_unverified = Some("画质判据没跑成".into())
        }
        _ => {}
    }
    if launch.static_extras.is_none() {
        image_unverified = Some("没有静态画面可比 (workload.launch 缺 static_extras)".into());
    }
    if let (None, Some(e)) = (&image_unverified, image["shot_error"].as_str()) {
        image_unverified = Some(e.to_string());
    }
    let gap_state = if !gap_open.is_empty() {
        crate::scorecard::GapState::Open(gap_open.join("; "))
    } else if let Some(r) = &image_unverified {
        crate::scorecard::GapState::Unverified(r.clone())
    } else {
        crate::scorecard::GapState::Closed
    };
    let gap_json = crate::scorecard::quality_gap_json(
        policy,
        &gap_state,
        "iq_judge+psnr",
        crate::scorecard::image_rows(&image["iq"], image["psnr_db_used"].as_f64(), image["noise_floor_db"].as_f64(), crate::eval::IMAGE_GATE_MARGIN_DB),
        json!({"iq": image["iq"], "psnr_db_used": image["psnr_db_used"], "noise_floor_db": image["noise_floor_db"]}),
    );
    report["image_check"] = image;
    if let Some(e) = image_fail {
        report["verdict"]["reason"] = json!(e);
        return done(&report);
    }

    // ── 停充 (与 sysparam 同一套; 失败不致命, 功耗不进判定) ──
    let want_suspend = req["conditions"]["charging_suspend"]["enabled"].as_bool().unwrap_or(false);
    let mut suspended_via: Option<String> = None;
    if want_suspend {
        let out = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh suspend"), 90);
        std::fs::write(evidence.join("charge_suspend.log"), &out).ok();
        if adb_ok(&out) && out.contains("CHARGE_SUSPENDED") {
            suspended_via = out.lines().find(|l| l.contains("node=")).map(|l| l.trim().to_string())
                .or_else(|| Some("charge_suspend.sh".into()));
        }
    }
    // 停充成功后等电池停止吸电再开第一臂 (审计 #13)
    let charge_settle = suspended_via.as_ref().map(|_| wait_charge_settled(serial));
    let restore_charge = |serial: &str| {
        if suspended_via.is_some() {
            let cr = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh restore"), 60);
            std::fs::write(evidence.join("charge_restore.log"), &cr).ok();
        }
    };

    // ── ABBA ──
    let rounds = req["protocol"]["rounds"].as_i64().unwrap_or(1).max(1) as u32;
    let seconds = req["workload"]["seconds"].as_i64().unwrap_or(30).max(1) as u64;
    let alpha = req["protocol"]["p_threshold"].as_f64().unwrap_or(0.05);
    let spf = crate::eval::declared_spf(req);
    let fps_cap_declared = crate::eval::declared_fps_cap(req);
    let cool_c = req["conditions"]["cool_c"].as_f64().filter(|x| *x > 0.0 && *x < 100.0);
    let empty = BTreeMap::new();
    let (mut knob_runs, mut ctrl_runs, mut cool_log, mut checks) = (Vec::new(), Vec::new(), Vec::new(), Vec::new());
    let mut abort: Option<String> = None;
    // 臂序随机 (审计 #13), 种子进报告; 环境不一致的对单独剔除 (审计 #9 #11)
    let order_seed = crate::curtail::order_seed(req["protocol"]["order_seed"].as_u64());
    let knob_first = crate::curtail::knob_first_order(rounds as usize, order_seed);
    let mut dropped: Vec<Value> = Vec::new();
    'pairs: for i in 1..=rounds {
        for arm in if knob_first[i as usize - 1] { ["knob", "ctrl"] } else { ["ctrl", "knob"] } {
            let tag = format!("{arm}{i}");
            let fan = arm_fan_off(serial);
            let mut c = arm_wait_cool(serial, cool_c, &tag, arm_cool_timeout(&cool_log));
            c["arm"] = json!(tag);
            c["fan"] = fan.clone();
            cool_log.push(c);
            if let Some(e) = fan_still_on(&fan) {
                abort = Some(format!("{tag}: {e}"));
                break 'pairs;
            }
            let dir = evidence.join(&tag);
            let _ = std::fs::create_dir_all(&dir);
            let extras = if arm == "knob" { knob_full.clone() } else { base_extras.clone() };
            if let Err(e) = start_with_extras(serial, &pkg, &extras, marker.as_deref(), &[OUT_FILE], &dir) {
                abort = Some(e);
                break 'pairs;
            }
            let arm_launch = crate::eval::LaunchSpec { extras, marker: marker.clone(), static_extras: None };
            let res = run_one_launch(serial, &format!("wb_{tag}"), &dir, seconds, &pkg, req["workload"]["render_comm"].as_str(), &arm_launch, false, None, spf);
            // 采完等 harness 走完路线落 json, 再核对旋钮
            let out = wait_out_json(serial, &pkg);
            let _ = adb(serial, &["shell", &format!("am force-stop {pkg}")], 30);
            let out = match out {
                Ok(o) => o,
                Err(e) => {
                    abort = Some(format!("{tag}: {e}"));
                    break 'pairs;
                }
            };
            std::fs::write(dir.join(OUT_FILE), serde_json::to_string_pretty(&out).unwrap_or_default()).ok();
            match check_knobs(&out, if arm == "knob" { &knobs } else { &empty }) {
                Ok(rows) => checks.push(json!({"arm": tag, "ok": true, "knobs": rows})),
                Err(e) => {
                    checks.push(json!({"arm": tag, "ok": false, "error": e}));
                    abort = Some(format!("{tag}: {e}"));
                    break 'pairs;
                }
            }
            if out["clean_exit"].as_bool() != Some(true) {
                abort = Some(format!("{tag}: harness clean_exit=false ({})", out["abort_reason"]));
                break 'pairs;
            }
            match res {
                Ok(mut m) => {
                    if let (Some(t), Some(cap)) = (run_soc_temp_max(&m), req["conditions"]["temp_cap_c"].as_f64()) {
                        if t > cap {
                            abort = Some(format!("{tag} SoC 结温 {t}C 超过上限 {cap}C"));
                            break 'pairs;
                        }
                    }
                    m["round"] = json!(i);
                    if arm == "knob" { knob_runs.push(m) } else { ctrl_runs.push(m) }
                    // 白盒旋钮不动 GPU 档位, 档位分布照比
                    settle_pair(i, &mut knob_runs, &mut ctrl_runs, &mut dropped, true);
                }
                Err(e) => {
                    abort = Some(e);
                    break 'pairs;
                }
            }
        }
    }
    restore_charge(serial);
    let (cool_reached, cool_timeout) = cool_summary(&cool_log);
    report["conditions_observed"] = json!({
        "cool_c_target": cool_c, "cool_reached_c": cool_reached, "cool_timeout": cool_timeout,
        "cool_per_arm": cool_log, "temp_cap_c": req["conditions"]["temp_cap_c"],
        "knob_extras": kx, "knob_checks": checks,
        "arm_order": crate::curtail::order_note(order_seed, rounds as usize),
        "charge_settle": charge_settle,
        "env_consistency": consistency_note(&dropped, true),
        "marker_to_capture": crate::eval::marker_delay_note(&knob_runs, &ctrl_runs),
        "spf_declared": spf,
        "fps_cap_declared": fps_cap_declared,
        "fan_across_pairs": fan_mismatch(&knob_runs, &ctrl_runs),
    });
    report["evidence_dir"] = json!(evidence.display().to_string());
    if let Some(r) = abort {
        report["verdict"]["reason"] = json!(r);
        return done(&report);
    }

    // ── 判定 ──
    if knob_runs.is_empty() && !dropped.is_empty() {
        report["verdict"]["reason"] = json!(format!(
            "{} 对两臂测量环境都不一致 (风扇或 GPU 档位), 全部剔除, 没有可比的数据 (见 conditions_observed.env_consistency)",
            dropped.len()
        ));
        return done(&report);
    }
    let (verdict, keep, on_battery_all) = judge_runs(&ctrl_runs, &knob_runs, alpha, rounds, fps_cap_declared);
    report["verdict"] = verdict;
    append_dropped_reason(&mut report, &dropped);
    let md = report["conditions_observed"]["marker_to_capture"].clone();
    crate::eval::append_marker_delay_reason(&mut report, &md);
    let mut m = report["metrics"].take();
    let psnr = m["psnr_db"].clone();
    let power = aggregate_metrics(&mut m, &knob_runs, &ctrl_runs, on_battery_all);
    m["psnr_db"] = psnr;
    if power.is_none() {
        unavailable(&mut m, "power_watt", "非放电态 (或功耗采样缺失): 整机功耗量不准, 如实 null");
    }
    report["metrics"] = m;
    let env = knob_runs.last().or(ctrl_runs.last()).and_then(|r| r.get("env")).cloned().unwrap_or(json!({}));
    // 复核用: 电池状态、电池电流、USB 轨读数都带上 (满电空闲时只有 USB 轨可信)
    report["power_source"] = json!({
        "rail": env["power_rail"], "battery_status": env["battery_status"],
        "battery_current_now_ua": env["current_now_ua_mean"], "battery_capacity_pct": env["battery_capacity_pct"],
        "usb_input_w_mean": env["usb_input_w_mean"],
        "on_battery": env["on_battery"], "bypass": env["bypass"], "source": env["power_source"],
        "power_usable_reason": env["power_usable_reason"],
        "fan_state": env["fan_state"],
        "charging_suspended_via": suspended_via,
        // 跨任务可比性: 旁路轨的绝对值含充电器转换损耗, 只能同轨比
        "cross_run_comparable": crate::sysparam::power_cross_run_comparable(
            knob_runs.iter().chain(ctrl_runs.iter()).filter_map(|r| r.get("env")),
        ),
        "excluded_reason": crate::sysparam::power_group_note(
            knob_runs.iter().chain(ctrl_runs.iter()).filter_map(|r| r.get("env")),
        ),
    });
    if measure_only {
        let r = report["verdict"]["reason"].as_str().unwrap_or("").to_string();
        report["verdict"]["measure_only"] = json!(true);
        report["verdict"]["would_keep"] = json!(keep);
        report["verdict"]["reason"] = json!(format!(
            "仅量上限: 旋钮 {} 改了画面必然变化, 不作为可采纳优化 (统计结果: {r})",
            if qc.is_empty() { "payload.measure_only".to_string() } else { qc.join(",") }
        ));
        report["status"] = json!("REJECT");
        report["verdict"]["is_pareto_improvement"] = json!(false);
    } else {
        // 画面差距关闭才可能 PASS; report 下性能过了而差距没关闭 → KEEP_WITH_QUALITY_GAP;
        // gate 下画面没确认不变 → REJECT (原行为)
        crate::scorecard::finalize_status(&mut report, keep, policy, &gap_state, gap_json);
    }
    done(&report)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn knob_extras_validates_and_orders() {
        let (m, x) = knob_extras(&json!({"knobs": {"msaa": 1, "feat_off": "fog,ambientocclusion"}})).unwrap();
        assert_eq!(x, "--es feat_off fog,ambientocclusion --es msaa 1");
        assert_eq!(quality_changing(&m), vec!["feat_off".to_string(), "msaa".to_string()]);
        assert!(knob_extras(&json!({"knobs": {"bogus": "1"}})).is_err());
        assert!(knob_extras(&json!({"knobs": {"msaa": "1; rm -rf /"}})).is_err());
        assert!(knob_extras(&json!({"knobs": {}})).is_err());
        let (m2, _) = knob_extras(&json!({"knobs": {"store_opt": "discard"}})).unwrap();
        assert!(quality_changing(&m2).is_empty());
    }

    fn out(knobs: Value, msaa: &str) -> Value {
        json!({"clean_exit": true, "graphics": {"msaa": msaa, "store_opt": "Auto", "shadowres": "4096"}, "knobs": knobs})
    }

    #[test]
    fn check_knobs_accepts_applied_and_rejects_others() {
        let want: BTreeMap<String, String> = [("msaa".to_string(), "1".to_string())].into();
        let good = out(json!({"msaa": {"want": "1", "how": "prop", "actual": "1"}}), "1");
        assert_eq!(check_knobs(&good, &want).unwrap().len(), 1);
        // 改法是 none → 没生效
        let none = out(json!({"msaa": {"want": "1", "how": "none", "actual": "4"}}), "4");
        assert!(check_knobs(&none, &want).unwrap_err().contains("没生效"));
        // 改法说成功但回读不对
        let mism = out(json!({"msaa": {"want": "1", "how": "prop", "actual": "4"}}), "4");
        assert!(check_knobs(&mism, &want).unwrap_err().contains("回读"));
        // 没记录
        assert!(check_knobs(&out(json!({}), "4"), &want).is_err());
        // 旧包 (没有 knobs 段)
        assert!(check_knobs(&json!({"graphics": {}}), &want).unwrap_err().contains("0.2.0"));
    }

    #[test]
    fn check_knobs_matches_per_feature_entries_and_ctrl_must_be_empty() {
        let want: BTreeMap<String, String> = [("feat_off".to_string(), "fog".to_string())].into();
        let o = out(json!({"feat_off:VolumetricFogRendererFeature": {"want": "off", "how": "SetActive", "actual": "False"}}), "4");
        assert_eq!(check_knobs(&o, &want).unwrap()[0]["knob"], "feat_off:VolumetricFogRendererFeature");
        let none_matched = out(json!({"feat_off": {"want": "fog", "how": "none:no_feature_matched", "actual": "null"}}), "4");
        assert!(check_knobs(&none_matched, &want).is_err());
        let empty = BTreeMap::new();
        assert!(check_knobs(&out(json!({}), "4"), &empty).unwrap().is_empty());
        assert!(check_knobs(&o, &empty).unwrap_err().contains("对照臂"));
    }
}
