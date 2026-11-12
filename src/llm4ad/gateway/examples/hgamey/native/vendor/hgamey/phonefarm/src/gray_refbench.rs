//! Known-answer loadOp collection; every device operation belongs to Session.
use crate::{confirmation_transport::new_file, gray_session::{RefbenchArm, RefbenchOutput, RefbenchOutputs, Session}};
use serde_json::{json, Value};
use std::{fs, path::{Path, PathBuf}, time::{Duration, Instant}};

const PACKAGE: &str = "io.github.hgamey.refbench";
const REPORT: &str = "refbench-loadop.json";

struct Options {
    serial: String, out: PathBuf, frames: u32, intensity: u32,
    timeout: u64, so: Option<String>, origin: String,
}
enum Command { Collect(Options), Recover { serial: String, session: PathBuf }, Help }

pub(crate) fn handles(args: &[String]) -> bool {
    matches!(args.first().map(String::as_str), Some("refbench-loadop" | "refbench-recover"))
}

fn parse(args: &[String]) -> Result<Command, String> {
    let recover = args.first().is_some_and(|s| s == "refbench-recover");
    let mut o = Options { serial: String::new(), out: PathBuf::new(), frames: 1800,
        intensity: 8, timeout: 150, so: None, origin: "device".into() };
    let mut session = None;
    let mut help = false;
    let mut it = args.iter().skip(1);
    while let Some(arg) = it.next() {
        if matches!(arg.as_str(), "--help" | "-h") { help = true; continue; }
        if arg == "--json" { continue; }
        if !matches!(arg.as_str(), "--serial" | "--session" | "--out" | "--frames" | "--intensity" | "--timeout-seconds" | "--so" | "--evidence-origin") {
            return Err(format!("unknown refbench option {arg}"));
        }
        let v = it.next().ok_or_else(|| format!("{arg} requires a value"))?;
        if recover && !matches!(arg.as_str(), "--serial" | "--session") { return Err(format!("{arg} is not a recovery option")); }
        match arg.as_str() {
            "--serial" => o.serial = v.clone(),
            "--session" if recover => session = Some(PathBuf::from(v)),
            "--out" => o.out = v.into(),
            "--frames" => o.frames = v.parse().map_err(|_| "frames must be 60..100000")?,
            "--intensity" => o.intensity = v.parse().map_err(|_| "intensity must be 1..64")?,
            "--timeout-seconds" => o.timeout = v.parse().map_err(|_| "timeout-seconds must be 1..600")?,
            "--so" => o.so = Some(v.clone()),
            "--evidence-origin" => o.origin = v.clone(),
            _ => return Err(format!("{arg} is only supported by refbench-recover")),
        }
    }
    if help { return Ok(Command::Help); }
    if o.serial.is_empty() { return Err("refbench requires an explicit --serial".into()); }
    if recover { return Ok(Command::Recover { serial: o.serial, session: session.filter(|p| !p.as_os_str().is_empty()).ok_or("missing --session")? }); }
    if o.out.as_os_str().is_empty() || !(60..=100000).contains(&o.frames) || !(1..=64).contains(&o.intensity)
        || !(1..=600).contains(&o.timeout) || !matches!(o.origin.as_str(), "device" | "synthetic") {
        return Err("refbench requires --out, frames 60..100000, intensity 1..64, timeout-seconds 1..600 and evidence-origin device|synthetic".into());
    }
    Ok(Command::Collect(o))
}

pub(crate) fn admission(args: &[String]) -> Result<crate::graylayer::GrayCliAdmission, String> {
    Ok(match parse(args)? { Command::Help => crate::graylayer::GrayCliAdmission::Help,
        _ => crate::graylayer::GrayCliAdmission::Proceed })
}

fn save(path: &Path, value: &Value) -> Result<(), String> {
    new_file(path, &serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?)
}

fn marker_ready(value: &Value) -> bool { value["layer_loaded"] == true && value["pkg"] == PACKAGE }

fn decoded(output: Option<&RefbenchOutput>) -> Option<Value> {
    output.and_then(|o| serde_json::from_slice(&o.bytes).ok())
}

fn started(outputs: &RefbenchOutputs, arm: RefbenchArm) -> bool {
    outputs.started.as_ref().and_then(|o| std::str::from_utf8(&o.bytes).ok())
        .is_some_and(|s| s.trim() == format!("bw_pingpong {}", arm.label()))
}

/// Keep application facts, rewrite expectations and resource recovery separate.
fn qualify(arm: RefbenchArm, frames: u32, intensity: u32, app: Option<&Value>, layer: Option<&Value>) -> (Value, Value) {
    let Some(app) = app.filter(|v| v.is_object()) else {
        return (json!({"status":"unavailable","reason":"refbench JSON missing or invalid"}),
                json!({"status":"unavailable","reason":"application facts unavailable"}));
    };
    let parameters = app["contract_version"] == 1 && app["run_id"] == arm.label() && app["scene"] == "bw_pingpong"
        && app["params"]["frames"] == frames && app["params"]["intensity"] == intensity
        && app["effective_knobs"]["loadop"] == arm.knob();
    let clean = parameters && app["frames_submitted"] == frames && app["clean_exit"] == true;
    let completion = json!({"status":if clean {"passed"} else {"failed"},"parameters_match":parameters,
        "reported_frames":app["frames_submitted"],"reported_clean_exit":app["clean_exit"],"app_version":app["app_version"]});
    let expected_op = if arm.knob() == "on" { "DONT_CARE" } else { "LOAD" };
    let passes = app["passes"].as_array().map(|items| items.iter().filter(|v| v["name"].as_str().is_some_and(|s| s.starts_with("ping_"))).collect::<Vec<_>>());
    let pass_match = passes.is_some_and(|p| p.len() == intensity as usize && p.iter().all(|v| v["load_op"] == expected_op));
    if !arm.layered() {
        return (completion, json!({"status":if parameters && pass_match && layer.is_none() {"passed"}else{"failed"},
            "application_load_ops_match":pass_match,"expected_load_op":expected_op,"expected_layer":"absent"}));
    }
    let Some(layer) = layer.filter(|v| marker_ready(v)) else {
        return (completion, json!({"status":"unavailable","reason":"current layer marker missing or invalid"}));
    };
    let begins = layer["effective"].as_array().and_then(|entries| entries.iter().try_fold(0u64, |total, e| {
        if e["field"] != "loadOp" || e["from"] != "LOAD" || e["to"] != "DONT_CARE" { return None; }
        total.checked_add(e["begins"].as_u64()?)
    }));
    let layer_valid = layer["knob"] == "gray_loadop_dontcare" && layer["failed"].as_array().is_some_and(Vec::is_empty)
        && layer["readonly_stats"]["frames"].as_u64().is_some_and(|n| n > 0)
        && layer["readonly_stats"]["render_pass_begins"].as_u64().is_some_and(|n| n > 0);
    let expected = if arm.knob() == "off" { begins.is_some_and(|n| n > 0) && layer["unavailable_reason"].is_null() }
        else { begins == Some(0) && layer["unavailable_reason"].is_string() };
    (completion, json!({"status":if parameters && pass_match && layer_valid && expected {"passed"}else{"failed"},
        "application_load_ops_match":pass_match,"expected_load_op":expected_op,"effective_begins":begins,
        "unavailable_reason":layer["unavailable_reason"],"creation_records_may_be_unused":true}))
}

fn persist_output(out: &Path, name: &str, suffix: &str, value: Option<&RefbenchOutput>, empty_absent: bool) -> Result<Value, String> {
    let path = out.join(format!("{name}.{suffix}"));
    if let Some(value) = value {
        new_file(&path, &value.bytes)?;
        Ok(json!({"status":"collected","path":path,"source_path":value.source_path,"bytes":value.bytes.len()}))
    } else if empty_absent {
        new_file(&path, b"")?;
        Ok(json!({"status":"confirmed_absent","path":path,"bytes":0}))
    } else { Ok(json!({"status":"missing","path":null})) }
}

fn collect_arm(session: &Session, out: &Path, arm: RefbenchArm, o: &Options) -> Result<Value, String> {
    let mut latest = None;
    let mut natural = false;
    let mut timed_out = false;
    let mut narrowed = false;
    let run = (|| -> Result<(), String> {
        session.start_refbench_arm(arm, o.frames, o.intensity, o.so.as_deref())?;
        let start = Instant::now();
        loop {
            latest = Some(session.refbench_outputs()?);
            if arm.layered() && !narrowed && decoded(latest.as_ref().unwrap().layer.as_ref()).as_ref().is_some_and(marker_ready) {
                session.narrow()?; narrowed = true;
            }
            if !session.target_alive()? && started(latest.as_ref().unwrap(), arm) {
                // Re-read after observing exit: the final JSON may have been
                // fsynced between the previous read and the process check.
                latest = Some(session.refbench_outputs()?);
                natural = started(latest.as_ref().unwrap(), arm);
                if arm.layered() && !narrowed {
                    if !decoded(latest.as_ref().unwrap().layer.as_ref()).as_ref().is_some_and(marker_ready) {
                        return Err("target exited without a valid current layer marker".into());
                    }
                    session.narrow()?; narrowed = true;
                }
                break;
            }
            if start.elapsed() >= Duration::from_secs(o.timeout) { timed_out = true; break; }
            if arm.layered() && !narrowed && start.elapsed() >= Duration::from_secs(30) {
                return Err("current layer marker did not arrive within 30 seconds".into());
            }
            std::thread::sleep(Duration::from_millis(500));
        }
        Ok(())
    })();
    let app = latest.as_ref().and_then(|v| v.refbench.as_ref());
    let layer = latest.as_ref().and_then(|v| v.layer.as_ref());
    let marker = latest.as_ref().and_then(|v| v.started.as_ref());
    let app_json = decoded(app);
    let layer_json = decoded(layer);
    let (completion, expectation) = qualify(arm, o.frames, o.intensity, app_json.as_ref(), layer_json.as_ref());
    let no_layer = latest.is_some() && layer.is_none();
    let complete = app.is_some() && marker.is_some() && if arm.layered() { layer.is_some() } else { no_layer };
    let artifacts = json!({"refbench":persist_output(out, arm.label(), "refbench.json", app, false)?,
        "layer":persist_output(out, arm.label(), "layer.json", layer, !arm.layered() && no_layer)?,
        "started":persist_output(out, arm.label(), "started", marker, false)?});
    let result = json!({"label":arm.label(),"layer":if arm.layered(){"rewrite"}else{"none"},"knob_loadop":arm.knob(),
        "collection":{"status":if complete {"complete"}else{"incomplete"},"error":run.as_ref().err()},
        "natural_exit":natural,"timed_out":timed_out,"global_narrowed_after_marker":narrowed,
        "clean_completion":completion,"rewrite_expectation":expectation,"artifacts":artifacts});
    save(&out.join(format!("{}.arm.json", arm.label())), &result)?;
    Ok(result)
}

fn collect(o: Options) -> Result<(Value, i32), String> {
    // Reject conflicting output or unavailable SO before acquiring a device.
    for name in std::iter::once(REPORT.to_owned()).chain(RefbenchArm::ALL.into_iter().flat_map(|arm|
        ["refbench.json", "layer.json", "started", "arm.json"].map(|suffix| format!("{}.{suffix}", arm.label())))) {
        if o.out.join(&name).try_exists().map_err(|e| e.to_string())? { return Err(format!("output already exists: {name}; choose a new out or recover the old session")); }
    }
    let so = o.so.clone().unwrap_or_else(crate::graylayer::layer_so_path);
    let source = crate::eval::resolve_path(&so).ok_or("layer SO not found")?;
    let metadata = fs::metadata(source).map_err(|e| e.to_string())?;
    if !metadata.is_file() || metadata.len() == 0 { return Err("layer SO must be a nonempty regular file".into()); }
    fs::create_dir_all(&o.out).map_err(|e| e.to_string())?;
    let out = o.out.canonicalize().map_err(|e| e.to_string())?;
    let lease = crate::devlease::Lease::target(Some(&o.serial), "gray-refbench-loadop")?;
    let session = Session::new_refbench_loadop(lease, &out, &o.origin)?;
    let mut arms = Vec::new();
    let run = (|| -> Result<(), String> {
        session.prepare()?;
        for arm in RefbenchArm::ALL {
            let result = collect_arm(&session, &out, arm, &o)?;
            let can_continue = result["natural_exit"] == true && result["collection"]["status"] == "complete"
                && result["collection"]["error"].is_null();
            arms.push(result);
            if !can_continue { return Err(format!("{} did not complete naturally with all expected evidence; later arms were not started", arm.label())); }
        }
        Ok(())
    })();
    let recovery = session.finish_refbench_loadop(run.as_ref().err().map(String::as_str))
        .unwrap_or_else(|e| json!({"status":"required","error":e,"session":session.dir}));
    let qualified = arms.len() == 4 && arms.iter().all(|a| a["natural_exit"] == true
        && a["collection"]["status"] == "complete" && a["clean_completion"]["status"] == "passed"
        && a["rewrite_expectation"]["status"] == "passed");
    let code = if recovery["status"] != "reported_complete" { 2 } else if run.is_err() || !qualified { 1 } else { 0 };
    let report = json!({"schema":"phonefarm/gray-refbench-loadop@1","serial":o.serial,"package":PACKAGE,
        "session":session.dir,"evidence_origin":o.origin,"requested":{"frames":o.frames,"intensity":o.intensity,"timeout_seconds":o.timeout},
        "arms":arms,"collection_error":run.err(),"qualified":qualified,"recovery":recovery,
        "performance_measured":false,"game_state_restored":false,"exit_code":code});
    save(&out.join(REPORT), &report)?;
    Ok((report, code))
}

pub(crate) fn run(args: &[String]) -> i32 {
    let result = (|| match parse(args)? {
        Command::Collect(o) => collect(o),
        Command::Recover { serial, session } => {
            let owner = Session::resume_refbench_loadop(&session, &serial)?;
            let report = owner.finish_refbench_loadop(Some("explicit recovery; original experiment facts are unchanged"))?;
            let code = if report["status"] == "reported_complete" { 0 } else { 2 };
            Ok((report, code))
        },
        Command::Help => Ok((json!({"usage":"gray-layer refbench-loadop --serial S --out DIR [--frames 1800] [--intensity 8] [--timeout-seconds 150] [--so FILE] [--json]; refbench-recover --serial S --session DIR --json"}), 0)),
    })();
    match result {
        Ok((value, code)) => { println!("{}", serde_json::to_string_pretty(&value).unwrap()); code },
        Err(error) => { println!("{}", json!({"schema":"phonefarm/gray-refbench-error@1","error":error})); 2 },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_answer_uses_bound_begins_and_echoed_parameters() {
        let mut app = json!({"contract_version":1,"run_id":"D_rewrite_on","scene":"bw_pingpong",
            "params":{"frames":60,"intensity":1},"effective_knobs":{"loadop":"on"},
            "frames_submitted":60,"clean_exit":true,"passes":[{"name":"ping_0","load_op":"DONT_CARE"}]});
        let mut layer = json!({"layer_loaded":true,"pkg":PACKAGE,"knob":"gray_loadop_dontcare","failed":[],
            "readonly_stats":{"frames":60,"render_pass_begins":120},"unavailable_reason":"rewritten pass never bound",
            "effective":[{"field":"loadOp","from":"LOAD","to":"DONT_CARE","begins":0}]});
        let (clean, expected) = qualify(RefbenchArm::RewriteOn, 60, 1, Some(&app), Some(&layer));
        assert_eq!(clean["status"], "passed"); assert_eq!(expected["status"], "passed");
        layer["effective"][0]["begins"] = json!(1);
        assert_eq!(qualify(RefbenchArm::RewriteOn, 60, 1, Some(&app), Some(&layer)).1["status"], "failed");
        app["params"]["frames"] = json!(3600);
        assert_eq!(qualify(RefbenchArm::RewriteOn, 60, 1, Some(&app), Some(&layer)).0["status"], "failed");
    }
}
