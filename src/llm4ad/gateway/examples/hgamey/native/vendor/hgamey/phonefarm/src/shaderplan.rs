//! Pure v2 support matrix. Resolved configuration is execution intent, never observation.
use serde_json::{json, Value};

pub struct ShaderPlan {
    pub request: Value,
    pub args: Vec<String>,
    pub resolved: Value,
}
fn positive(v: &Value, field: &str) -> Result<u32, String> {
    v.as_u64()
        .filter(|n| *n > 0 && *n <= u32::MAX as u64)
        .map(|n| n as u32)
        .ok_or_else(|| format!("{field} must be a positive u32"))
}
fn opt_bool(v: &Value, field: &str, default: bool) -> Result<bool, String> {
    if v.is_null() {
        Ok(default)
    } else {
        v.as_bool()
            .ok_or_else(|| format!("{field} must be bool/null"))
    }
}
fn opt_str<'a>(v: &'a Value, field: &str) -> Result<Option<&'a str>, String> {
    if v.is_null() {
        Ok(None)
    } else {
        v.as_str()
            .map(Some)
            .ok_or_else(|| format!("{field} must be string/null"))
    }
}
pub fn build(
    req: &Value,
    cli_serial: Option<&str>,
    cli_rail: Option<&str>,
) -> Result<ShaderPlan, String> {
    let w = &req["workload"];
    let c = &req["conditions"];
    let p = &req["payload"];
    let carrier = w["carrier"].as_str().ok_or("workload.carrier missing")?;
    if !matches!(carrier, "headless" | "refbench") {
        return Err(format!("shader workload.carrier={carrier} unsupported (no closed-game injection/replay implementation)"));
    }
    let allowed = [
        "cool_c",
        "require_root",
        "require_discharging",
        "allow_missing_power",
        "charging_suspend",
        "quality_reference",
        "inject",
        "gpu_level",
        "power_rail",
        "fan",
        "notes",
    ];
    for (k, v) in c.as_object().ok_or("conditions must be object")? {
        if !allowed.contains(&k.as_str()) && !v.is_null() {
            return Err(format!(
                "shader condition {k} unsupported; cannot silently drop requested value"
            ));
        }
    }
    for (k, v) in w.as_object().ok_or("workload must be object")? {
        if !["carrier", "script", "seconds", "frames", "intensity"].contains(&k.as_str())
            && !v.is_null()
        {
            return Err(format!("shader workload.{k} unsupported"));
        }
    }
    // GOL always serializes fan:String. `unknown` states a measurement gap, not a control.
    let fan = opt_str(&c["fan"], "conditions.fan")?;
    if fan.is_some_and(|v| v != "unknown") {
        return Err(
            "shader fan control unsupported; only unknown/null declaration is accepted".into(),
        );
    }
    if opt_bool(&c["inject"], "conditions.inject", false)? {
        return Err("shader inject=true unsupported: only built-in headless/refbench postfx route is implemented".into());
    }
    let injection = resolve_injection(&p["inject"], carrier, p["track"].as_str())?;
    if !opt_bool(&c["require_root"], "conditions.require_root", true)? {
        return Err(
            "shader require_root=false unsupported: frequency control requires root".into(),
        );
    }
    let discharge = opt_bool(
        &c["require_discharging"],
        "conditions.require_discharging",
        false,
    )?;
    let suspend = &c["charging_suspend"];
    if !suspend.is_null() && !suspend.is_object() {
        return Err("charging_suspend must be object/null".into());
    }
    if let Some(obj) = suspend.as_object() {
        for (k, v) in obj {
            if !["enabled", "node"].contains(&k.as_str()) && !v.is_null() {
                return Err(format!("charging_suspend.{k} unsupported"));
            }
        }
    }
    let suspend_enabled = opt_bool(&suspend["enabled"], "charging_suspend.enabled", false)?;
    let allow_missing_power = opt_bool(&c["allow_missing_power"], "conditions.allow_missing_power", false)?;
    if allow_missing_power && (discharge || suspend_enabled) {
        return Err("allow_missing_power cannot weaken required discharge/charging control".into());
    }
    if !suspend["node"].is_null() {
        return Err(
            "charging_suspend.node unsupported: only automatic known-node probing is implemented"
                .into(),
        );
    }
    let cool = c["cool_c"]
        .as_f64()
        .or(req["protocol"]["cool_c"].as_f64())
        .filter(|x| *x > 0.0 && *x < 100.0)
        .ok_or("shader requires explicit cool_c in (0,100)")?;
    let qbase = p["quality_baseline_db"]
        .as_f64()
        .ok_or("shader requires quality_baseline_db")?;
    let qpayload = opt_str(&p["quality_reference"], "payload.quality_reference")?;
    let qcondition = opt_str(&c["quality_reference"], "conditions.quality_reference")?;
    if qpayload.is_some() && qcondition.is_some() && qpayload != qcondition {
        return Err("quality_reference conflicting payload/conditions".into());
    }
    let reference = qpayload.or(qcondition);
    let script = opt_str(&w["script"], "workload.script")?.unwrap_or("");
    let (seconds, frames, intensity) = if carrier == "headless" {
        if !w["frames"].is_null() || !w["intensity"].is_null() {
            return Err("headless does not consume workload.frames/intensity".into());
        }
        if !script.is_empty() {
            return Err("headless does not execute workload.script".into());
        }
        let s = if w["seconds"].is_null() {
            &req["protocol"]["replay_seconds"]
        } else {
            &w["seconds"]
        };
        (Some(positive(s, "workload.seconds")?), None, None)
    } else {
        if !w["seconds"].is_null() {
            return Err("refbench runs frames; workload.seconds unsupported".into());
        }
        if !script.is_empty() && script != "sr_pipeline" {
            return Err("refbench only implements sr_pipeline".into());
        }
        (
            None,
            Some(positive(&w["frames"], "workload.frames")?),
            Some(positive(&w["intensity"], "workload.intensity")?),
        )
    };
    if frames.is_some_and(|n| !(60..=100_000).contains(&n)) {
        return Err(
            "refbench workload.frames must be 60..=100000 (device clamps outside this range)"
                .into(),
        );
    }
    if seconds.is_some_and(|n| n > i32::MAX as u32) {
        return Err("headless seconds exceed runner's signed integer ABI".into());
    }
    if intensity.is_some_and(|n| n > 64) {
        return Err(
            "refbench workload.intensity must be 1..=64 (device clamps larger values)".into(),
        );
    }
    let serial = cli_serial.or(opt_str(&req["serial"], "serial")?);
    if serial.is_some_and(|s| s.starts_with("hdc:")) {
        return Err("shader requires Android/adb+root; hdc is unsupported".into());
    }
    let rail = cli_rail
        .or(opt_str(&c["power_rail"], "conditions.power_rail")?)
        .unwrap_or("usb");
    if !matches!(rail, "usb" | "battery") {
        return Err("power_rail must be usb/battery".into());
    }
    let gpu_level = if c["gpu_level"].is_null() {
        None
    } else {
        Some(
            c["gpu_level"]
                .as_u64()
                .filter(|v| *v <= u32::MAX as u64)
                .ok_or("gpu_level must be u32/null")? as u32,
        )
    };
    let resolved = json!({"carrier":carrier,"seconds":seconds,"frames":frames,"intensity":intensity,
        "script": if carrier == "refbench" { "sr_pipeline" } else { "" },"serial":serial,
        "cool_c":cool,"require_root":true,"require_discharging":discharge,
        "charging_suspend":{"enabled":suspend_enabled,"node":null},"inject":injection,"fan":null,
        "power_rail":rail,"gpu_level":gpu_level,"quality_reference":reference,"allow_missing_power":allow_missing_power});
    let request = json!({"version":1,"candidate_id":req["candidate_id"],"track":p["track"],
        "serial":serial,"package":req["package"],"replay_script":script,"shader_path":p["shader_path"],
        "spirv_path":p["spirv_path"],"budget_ms":p["budget_ms"],"quality_baseline_db":qbase,
        "incumbent_latency_ms":p["incumbent_latency_ms"],"quality_reference":reference,
        // replay_seconds has no consumer on refbench; explicitly recorded null in resolved_config.
        "protocol":{"cool_c":cool,"replay_seconds":seconds.unwrap_or(1),"rounds":req["protocol"]["rounds"],
            "alternating":req["protocol"]["alternating"],"p_threshold":req["protocol"]["p_threshold"]},
        "v2_context":{"conditions_requested":c,"workload_requested":w,"injection_requested":p["inject"],"resolved_config":resolved}});
    let mut args = vec![
        "--json".into(),
        "--carrier".into(),
        carrier.into(),
        "--power-rail".into(),
        rail.into(),
    ];
    if let Some(s) = serial {
        args.extend(["--serial".into(), s.into()]);
    }
    if let Some(n) = frames {
        args.extend(["--frames".into(), n.to_string()]);
    }
    if let Some(n) = intensity {
        args.extend(["--intensity".into(), n.to_string()]);
    }
    if let Some(n) = gpu_level {
        args.extend(["--gpu-level".into(), n.to_string()]);
    }
    if suspend_enabled {
        args.push("--suspend-charging".into());
    }
    if discharge {
        args.push("--require-discharging".into());
    }
    if allow_missing_power { args.push("--allow-missing-power".into()); }
    Ok(ShaderPlan {
        request,
        args,
        resolved,
    })
}

/// The typed GOL contract uses Inject{via,layer}; old bool false/null means no extra route.
/// A native injection is implemented only by refbench's SR postfx scene, never a black-box hook.
fn resolve_injection(v: &Value, carrier: &str, track: Option<&str>) -> Result<Value, String> {
    if v.is_null() || v == &json!(false) {
        return Ok(json!(false));
    }
    if v == &json!(true) {
        return Err(
            "shader inject=true unsupported: use typed carrier_native route for refbench SR".into(),
        );
    }
    let obj = v
        .as_object()
        .ok_or("payload.inject must be Inject{via,layer}/false/null")?;
    for (k, val) in obj {
        if !["via", "layer"].contains(&k.as_str()) && !val.is_null() {
            return Err(format!("payload.inject.{k} unsupported"));
        }
    }
    let via = opt_str(&v["via"], "payload.inject.via")?.ok_or("payload.inject.via missing")?;
    if via != "carrier_native" {
        return Err(format!(
            "shader inject.via={via} unsupported: no Vulkan-layer shader injection implementation"
        ));
    }
    if !v["layer"].is_null() {
        return Err("carrier_native does not consume inject.layer".into());
    }
    if carrier != "refbench" || track != Some("sr") {
        return Err("carrier_native only implements refbench sr_pipeline/sr postfx; no headless/game/frame_gen injection".into());
    }
    Ok(json!({"via":"carrier_native","layer":null}))
}

/// A shader bench PASS only passes its operator checks. Neither route proves a game improvement.
pub fn qualification_scope(carrier: Option<&str>) -> Value {
    json!({"stage":"experiment","eligible":false,"can_claim_improvement":false,
        "claim_scope":if carrier==Some("headless") {"operator_benchmark_only"} else {"refbench_measurement_only"},
        "status":"INELIGIBLE","blockers":["no validated game-level display/present measurement",
            "power_window_verified=false: boundary samples cannot certify a whole power window",
            "no attested paired game quality sequence/deployment evidence"]})
}

pub fn abort(req: &Value, reason: &str, resolved: Option<&Value>) -> Value {
    json!({"version":2,"kind":"shader","candidate_id":req["candidate_id"],"status":"ABORT",
        "source":"phonefarm","metrics":{"operator_latency_ms":null,"fps_p95_ms":null,"power_watt":null,"psnr_db":null},
        "verdict":{"is_pareto_improvement":false,"p_value":1.0,"reason":reason},
        "conditions_requested":req["conditions"],"workload_requested":req["workload"],"injection_requested":req["payload"]["inject"],
        "resolved_config":resolved,"conditions_observed":{"arms":[],"fan":null,"root_verified":null,"restored":null,"power_window_verified":false},
        "qualification":qualification_scope(req["workload"]["carrier"].as_str()),
        "measurement_scope":if req["workload"]["carrier"]=="headless" {"operator_benchmark_only"} else {"refbench_measurement_only"},
        "unavailable":[reason]})
}

#[cfg(test)]
mod tests {
    use super::*;
    fn req() -> Value {
        json!({"version":2,"candidate_id":"c","serial":"phone", "package":"refbench",
        "workload":{"carrier":"refbench","script":"sr_pipeline","frames":370,"intensity":3,"seconds":null},
        "conditions":{"cool_c":42,"require_root":true,"require_discharging":true,"charging_suspend":{"enabled":true,"node":null},"inject":false,"quality_reference":"ref"},
        "protocol":{"rounds":4,"alternating":true,"p_threshold":0.05},
        "payload":{"track":"sr","shader_path":"x","spirv_path":"x","budget_ms":3,"quality_baseline_db":30}})
    }
    #[test]
    fn maps_every_consumed_refbench_field() {
        let r = req();
        let p = build(&r, Some("other"), Some("battery")).unwrap();
        assert_eq!(p.request["serial"], "other");
        assert_eq!(p.request["quality_reference"], "ref");
        assert_eq!(p.resolved["frames"], 370);
        assert_eq!(p.resolved["seconds"], Value::Null);
        for flag in [
            "--carrier",
            "refbench",
            "--frames",
            "370",
            "--intensity",
            "3",
            "--suspend-charging",
            "--require-discharging",
            "--power-rail",
            "battery",
        ] {
            assert!(p.args.iter().any(|s| s == flag), "{flag}");
        }
        assert_eq!(
            p.request["v2_context"]["conditions_requested"],
            r["conditions"]
        );
    }
    #[test]
    fn typed_gol_native_route_does_not_turn_unknown_fan_into_observation() {
        let r: Value =
            serde_json::from_str(include_str!("../examples/gol_shader_native_request.json"))
                .unwrap();
        let p = build(&r, None, None).unwrap();
        assert_eq!(p.resolved["inject"]["via"], "carrier_native");
        assert_eq!(p.resolved["fan"], Value::Null);
        assert_eq!(
            p.request["v2_context"]["conditions_requested"]["fan"],
            "unknown"
        );
        assert_eq!(
            p.request["v2_context"]["injection_requested"],
            r["payload"]["inject"]
        );
        for (carrier, track, via, layer) in [
            ("headless", "sr", "carrier_native", Value::Null),
            ("refbench", "frame_gen", "carrier_native", Value::Null),
            ("refbench", "sr", "vulkan_layer", Value::Null),
            ("refbench", "sr", "carrier_native", json!("some-layer")),
        ] {
            let mut unsupported = r.clone();
            unsupported["workload"]["carrier"] = json!(carrier);
            unsupported["payload"]["track"] = json!(track);
            unsupported["payload"]["inject"] = json!({"via":via,"layer":layer});
            assert!(build(&unsupported, None, None).is_err());
        }
    }
    #[test]
    fn rejects_conditions_without_implementation() {
        for (field, v) in [
            ("inject", json!(true)),
            ("temp_cap_c", json!(70)),
            ("fan", json!("on")),
            ("require_root", json!(false)),
        ] {
            let mut r = req();
            r["conditions"][field] = v;
            assert!(build(&r, None, None).is_err(), "{field}");
        }
        let mut r = req();
        r["conditions"]["charging_suspend"]["node"] = json!("/sys/x");
        assert!(build(&r, None, None).is_err());
        r = req();
        r["workload"]["carrier"] = json!("game_replay");
        assert!(build(&r, None, None).is_err());
        r = req();
        r["workload"]["seconds"] = json!(30);
        assert!(build(&r, None, None).is_err());
        r = req();
        r["workload"]["intensity"] = json!(65);
        assert!(build(&r, None, None).is_err());
        for frames in [59, 100001] {
            let mut r = req();
            r["workload"]["frames"] = json!(frames);
            assert!(build(&r, None, None).is_err());
        }
    }
    #[test]
    fn headless_seconds_are_explicit_no_frames_no_defaults() {
        let mut r = req();
        r["workload"] = json!({"carrier":"headless","seconds":17});
        assert_eq!(
            build(&r, None, None).unwrap().request["protocol"]["replay_seconds"],
            17
        );
        r["workload"]["seconds"] = Value::Null;
        assert!(build(&r, None, None).is_err());
        r["workload"]["seconds"] = json!(0);
        assert!(build(&r, None, None).is_err());
    }
    #[test]
    fn unknown_actual_does_not_echo_requested() {
        let r = req();
        let a = abort(&r, "unsupported", None);
        assert_eq!(a["conditions_observed"]["root_verified"], Value::Null);
        assert_ne!(a["conditions_observed"], r["conditions"]);
        assert_eq!(a["verdict"]["is_pareto_improvement"], false);
        assert_eq!(a["qualification"]["eligible"], false);
        assert_eq!(
            qualification_scope(Some("headless"))["claim_scope"],
            "operator_benchmark_only"
        );
    }
}
