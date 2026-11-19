//! Capability-only endpoint: no game, workload, baseline or performance qualification.
use super::*;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProbeRequest {
    schema: String,
    transport: Target,
    candidate: PathBuf,
    #[serde(default)]
    temperature_profile_id: Option<String>,
}
fn parse(bytes: &[u8]) -> Result<ProbeRequest, String> {
    let r: ProbeRequest = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    require(
        r.schema == "phonefarm/control_probe_request@1"
            && matches!(r.transport.kind.as_str(), "adb" | "fake")
            && safe(&r.transport.target)
            && !r.transport.target.starts_with("hdc:")
            && r.transport.program.is_absolute()
            && r.candidate.is_absolute(),
        "explicit Android control probe request required",
    )?;
    require(
        matches!(
            r.temperature_profile_id
                .as_deref()
                .unwrap_or("legacy_cold_v1"),
            "legacy_cold_v1" | "loaded_steady_v1" | "capability_absolute_v1"
        ),
        "unknown receiver temperature profile ID",
    )?;
    Ok(r)
}
fn candidate(bytes: &[u8]) -> Result<Value, String> {
    let a: Value = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    require(
        a["kind"] == "sysparam"
            && a["payload"]["params"]
                .as_object()
                .is_some_and(|p| p.len() == 1),
        "capability probe requires exactly one sysparam endpoint",
    )?;
    Ok(a)
}
pub(super) fn frozen_names(dir: &Path) -> Result<BTreeSet<String>, String> {
    let req = parse(&bounded(&dir.join("request.json"), 2 * 1024 * 1024)?)?;
    candidate(&bounded(&dir.join("candidate.json"), 2 * 1024 * 1024)?)?;
    let mut names = [
        "worker.sh",
        "bootstrap.sh",
        "snapshot.sh",
        "sample_env.sh",
        "probe_readonly.sh",
        "cold.sh",
        "restore.tsv",
        "candidate.plan.tsv",
        "probe_0.plan.tsv",
        "ftrace-current_tracer",
        "ftrace-buffer_size_kb",
        "ftrace-set_event",
        "ftrace-tracing_on",
        "request.json",
        "candidate.json",
        "transport.snapshot",
        "probe-contract.json",
        "initial.snapshot",
        "state.json",
    ]
    .into_iter()
    .map(String::from)
    .collect::<BTreeSet<_>>();
    if matches!(
        req.temperature_profile_id.as_deref(),
        Some("loaded_steady_v1" | "capability_absolute_v1")
    ) {
        for name in temperature::frozen_names() {
            names.insert((*name).into());
        }
    }
    if req.temperature_profile_id.as_deref() == Some("capability_absolute_v1") {
        names.insert("thermal-purpose.txt".into());
    }
    names.extend(fixed_tools::FROZEN_NAMES.map(String::from));
    Ok(names)
}
fn validate(dir: &Path) -> Result<ProbeRequest, String> {
    verify(dir)?;
    let r = parse(&bounded(&dir.join("request.json"), 2 * 1024 * 1024)?)?;
    let c = read_json(&dir.join("probe-contract.json"))?;
    require(
        c["schema"] == "phonefarm/control_probe_contract@1"
            && c["scope"] == "configuration_readback_only"
            && c["hold_seconds"] == 11
            && c["eligible"] == false,
        "unknown control probe receiver contract",
    )?;
    for (k, n, s) in [
        ("worker_sha256", "worker.sh", WORKER),
        ("bootstrap_sha256", "bootstrap.sh", BOOTSTRAP),
        ("snapshot_sha256", "snapshot.sh", SNAPSHOT),
        ("probe_sha256", "probe_readonly.sh", PROBE),
        ("cold_sha256", "cold.sh", COLD),
        ("env_sha256", "sample_env.sh", ENV),
    ] {
        require(
            c[k] == sha(s.as_bytes()) && bounded(&dir.join(n), CAP)? == s.as_bytes(),
            "control probe trusted tool bytes differ",
        )?;
    }
    for (k, n) in [
        ("request_sha256", "request.json"),
        ("candidate_sha256", "candidate.json"),
        ("transport_sha256", "transport.snapshot"),
    ] {
        require(
            c[k] == sha(&bounded(&dir.join(n), CAP)?),
            "control probe input binding differs",
        )?;
    }
    require(
        c["temperature_profile_id"]
            == r.temperature_profile_id
                .as_deref()
                .unwrap_or("legacy_cold_v1"),
        "control temperature preset differs",
    )?;
    match r.temperature_profile_id.as_deref() {
        Some("loaded_steady_v1") => {
            temperature::load_frozen(dir)?;
        }
        Some("capability_absolute_v1") => {
            temperature::load_capability_frozen(dir)?;
        }
        _ => {}
    }
    Ok(r)
}
fn close(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    state: &Value,
    run: &str,
) -> Result<Value, String> {
    let actual = identity(t, lease, "")?;
    require(
        actual.0 == state["serial"] && actual.1 == state["boot_id"],
        "probe closure identity/boot changed",
    )?;
    verify_remote(t, lease, dir, state)?;
    let out = worker(t, lease, state, "reconcile_controls", run, "", 45)?;
    require(
        out == b"RECONCILE_COMPLETE\n",
        "control closure completion missing",
    )?;
    let closure = pull(t, lease, state, run, "session_closed.txt")?;
    require(
        closure
            == format!(
                "SESSION_REVOKED {} {} {run}\n",
                state["token"].as_str().ok_or("token")?,
                state["boot_id"].as_str().ok_or("boot")?
            )
            .as_bytes(),
        "permanent session revocation unknown",
    )?;
    let quiescence = pull(t, lease, state, run, "quiescence.txt")?;
    let restore_exit = pull(t, lease, state, run, "restore_exit.txt")?;
    let snapshot_exit = pull(t, lease, state, run, "snapshot_restored.exit")?;
    require(
        quiescence == b"OWNED_GROUPS_ABSENT\n" && restore_exit == b"0\n" && snapshot_exit == b"0\n",
        "control closure stop/restore process receipt failed",
    )?;
    let snap = snap_bytes(pull(t, lease, state, run, "snapshot_restored.txt")?)?;
    require(
        snap == bounded(&dir.join("initial.snapshot"), 2 * 1024 * 1024)?,
        "control closure complete snapshot differs",
    )?;
    let refs = json!({"session_closed":save(dir,&format!("{run}-session_closed.txt"),&closure)?,
        "quiescence":save(dir,&format!("{run}-quiescence.txt"),&quiescence)?,
        "restore_exit":save(dir,&format!("{run}-restore_exit.txt"),&restore_exit)?,
        "snapshot_exit":save(dir,&format!("{run}-snapshot_restored.exit"),&snapshot_exit)?,
        "snapshot_restored":save(dir,&format!("{run}-snapshot_restored.txt"),&snap)?});
    journal(
        dir,
        json!({"event":"control_session_closed","at":now(),"actual_serial":actual.0,
        "boot_id":actual.1,"scope":"configuration_readback_only","receipts":refs}),
    )?;
    Ok(refs)
}
pub(super) fn run(path: &Path, out: &Path, execute: bool) -> Result<Value, String> {
    let bytes = bounded(path, 2 * 1024 * 1024)?;
    let req = parse(&bytes)?;
    let artifact_bytes = bounded(&req.candidate, 2 * 1024 * 1024)?;
    let a = candidate(&artifact_bytes)?;
    let capability = req.temperature_profile_id.as_deref() == Some("capability_absolute_v1");
    let loaded = matches!(
        req.temperature_profile_id.as_deref(),
        Some("loaded_steady_v1" | "capability_absolute_v1")
    );
    if req.transport.kind == "adb" && !execute {
        return Ok(
            json!({"status":"VALIDATED_ONLY","scope":"configuration_readback_only","device_executed":false,"eligible":false}),
        );
    }
    fs::create_dir(out).map_err(|e| format!("control session must be new: {e}"))?;
    fs::File::open(
        out.parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new(".")),
    )
    .and_then(|f| f.sync_all())
    .map_err(|e| e.to_string())?;
    let out = out.canonicalize().map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&out, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())?;
    }
    for (n, b) in [
        ("request.json", bytes.as_slice()),
        ("candidate.json", artifact_bytes.as_slice()),
    ] {
        save(&out, n, b)?;
    }
    save(
        &out,
        "transport.snapshot",
        &bounded(&req.transport.program, CAP)?,
    )?;
    for (n, s) in [
        ("worker.sh", WORKER),
        ("bootstrap.sh", BOOTSTRAP),
        ("snapshot.sh", SNAPSHOT),
        ("sample_env.sh", ENV),
        ("probe_readonly.sh", PROBE),
        ("cold.sh", COLD),
    ] {
        save(&out, n, s.as_bytes())?;
    }
    save_json(
        &out,
        "probe-contract.json",
        &json!({"schema":"phonefarm/control_probe_contract@1",
        "scope":"configuration_readback_only","hold_seconds":11,"temperature_profile_id":req.temperature_profile_id.as_deref().unwrap_or("legacy_cold_v1"),"eligible":false,
        "request_sha256":sha(&bytes),"candidate_sha256":sha(&artifact_bytes),
        "transport_sha256":sha(&bounded(&out.join("transport.snapshot"),CAP)?),
        "worker_sha256":sha(WORKER.as_bytes()),"bootstrap_sha256":sha(BOOTSTRAP.as_bytes()),
        "snapshot_sha256":sha(SNAPSHOT.as_bytes()),"probe_sha256":sha(PROBE.as_bytes()),
        "env_sha256":sha(ENV.as_bytes()),"cold_sha256":sha(COLD.as_bytes())}),
    )?;
    let (root, legacy, timeout) = roots()?;
    let mut lease = Lease::acquire(
        &root,
        &legacy,
        &req.transport.target,
        "control-probe",
        timeout,
    )?;
    let mut t = start_transport_target(&out, &req.transport, "probe-commands")?;
    let (serial, boot) = identity(&mut t, &lease, "")?;
    // Cold is checked before expensive inventory, again immediately before each control probe.
    if !loaded {
        cold(&mut t, &lease, &out, req.transport.kind == "fake", &now())?;
    }
    // Loaded restore ownership starts at the steady baseline state, before any remote write.
    let temperature = if loaded {
        Some(if capability {
            temperature::freeze_capability(&mut t, &lease, &out, &serial, &boot)?
        } else {
            temperature::freeze_loaded(&mut t, &lease, &out, &serial, &boot)?
        })
    } else {
        None
    };
    let probe = checked(
        &mut t,
        "readonly_probe",
        "sh",
        Some(&out.join("probe_readonly.sh")),
        15,
        2 * 1024 * 1024,
        &lease,
    )?;
    let probe = String::from_utf8(probe).map_err(|e| e.to_string())?;
    let pm = crate::sysparam::parse_probe(&probe);
    let policies = pm
        .get("cpu.policies")
        .ok_or("missing CPU inventory")?
        .split(',')
        .map(|n| format!("policy{}", n.trim()))
        .collect::<Vec<_>>();
    let initial = current_snapshot(&mut t, &lease, &out, &policies)?;
    save(&out, "initial.snapshot", &initial)?;
    let parsed = observation::snapshot(
        std::str::from_utf8(&initial).map_err(|e| e.to_string())?,
        &policies,
    )?;
    let graph = control::graph(&a, &probe, &parsed)?;
    require(
        graph.probes.len() == 1 && graph.policies == policies,
        "one probe / consistent CPU inventory required",
    )?;
    save(&out, "restore.tsv", graph.restore.as_bytes())?;
    save(&out, "candidate.plan.tsv", graph.candidate.as_bytes())?;
    save(&out, "probe_0.plan.tsv", graph.probes[0].1.as_bytes())?;
    let mut files = vec![
        "worker.sh",
        "bootstrap.sh",
        "snapshot.sh",
        "sample_env.sh",
        "restore.tsv",
        "candidate.plan.tsv",
        "probe_0.plan.tsv",
    ]
    .into_iter()
    .map(String::from)
    .collect::<Vec<_>>();
    for key in [
        "current_tracer",
        "buffer_size_kb",
        "set_event",
        "tracing_on",
    ] {
        let n = format!("ftrace-{key}");
        save(
            &out,
            &n,
            format!("{}\n", parsed[&format!("ftrace.{key}")].replace(',', "\n")).as_bytes(),
        )?;
        files.push(n);
    }
    let token = format!(
        "control_{}_{}",
        std::process::id(),
        chrono::Utc::now().timestamp_nanos_opt().ok_or("time")?
    );
    let remote = format!("/data/local/tmp/phonefarm-confirmation-{token}");
    let state = json!({"schema":"phonefarm/control_probe_state@1","scope":"configuration_readback_only",
        "serial":serial,"boot_id":boot,"target":req.transport.target,"token":token,"remote":remote,"policies":policies,"transport_origin":t.origin});
    save_json(&out, "state.json", &state)?;
    if loaded {
        files.push("thermal_sample.sh".into());
        // Complete the shared frozen thermal tool set; this endpoint never starts a sampler.
        files.push("thermal_sampler.sh".into());
        if capability {
            files.push("thermal-purpose.txt".into());
        }
    }
    freeze_tool_bundle(&out, &files)?;
    let names = frozen_names(&out)?;
    freeze(&out, &names.iter().map(String::as_str).collect::<Vec<_>>())?;
    validate(&out)?;
    register_inputs(&mut t.input_hashes, &out)?;
    // Includes installation: there are no device writes before this durable ownership barrier.
    lease.hold_for_recovery(&out)?;
    install(&mut t, &lease, &out, &remote, &token, &boot, &files)?;
    verify_remote(&mut t, &lease, &out, &state)?;
    let cold_receipt = if let Some(ref thermal) = temperature {
        if capability {
            temperature::capability_snapshot(&mut t, &lease, &out, thermal, "pre-control")?
        } else {
            temperature::loaded_snapshot(&mut t, &lease, &out, thermal, "pre-control")?
        }
    } else {
        cold(&mut t, &lease, &out, req.transport.kind == "fake", &now())?
    };
    require(
        current_snapshot(&mut t, &lease, &out, &policies)? == initial,
        "state drift before probe",
    )?;
    let actual = identity(&mut t, &lease, "")?;
    require(
        actual.0 == state["serial"] && actual.1 == state["boot_id"],
        "identity changed before control write",
    )?;
    let done = worker(&mut t, &lease, &state, "control_probe", "probe_0", "", 45)?;
    require(done == b"PROBE_COMPLETE\n", "probe completion missing")?;
    let mut refs = json!({});
    for n in [
        "snapshot_before",
        "snapshot_at_run",
        "snapshot_after_run",
        "snapshot_restored",
    ] {
        let ex = pull(&mut t, &lease, &state, "probe_0", &format!("{n}.exit"))?;
        require(ex == b"0\n", "probe snapshot exit failed")?;
        let b = snap_bytes(pull(
            &mut t,
            &lease,
            &state,
            "probe_0",
            &format!("{n}.txt"),
        )?)?;
        observation::snapshot(
            std::str::from_utf8(&b).map_err(|e| e.to_string())?,
            &policies,
        )?;
        if n == "snapshot_before" || n == "snapshot_restored" {
            require(b == initial, "probe full restoration differs")?;
        }
        refs[n] = save(&out, &format!("probe_0-{n}.txt"), &b)?;
        refs[format!("{n}_exit")] = save(&out, &format!("probe_0-{n}.exit"), &ex)?;
    }
    let receipt = pull(&mut t, &lease, &state, "probe_0", "probe_receipt.txt")?;
    refs["readback_receipt"] = save(&out, "probe_0-receipt.txt", &receipt)?;
    let actualprobe = probe + std::str::from_utf8(&receipt).map_err(|e| e.to_string())?;
    control::verify_artifact(&a, &actualprobe)?;
    save(&out, "probe.txt", actualprobe.as_bytes())?;
    let thermal_hold = if let Some(ref thermal) = temperature {
        let raw = pull(&mut t, &lease, &state, "probe_0", "thermal_hold.raw.tsv")?;
        save(&out, "control-hold-thermal.raw.tsv", &raw)?;
        temperature::assess_hold(
            &out,
            std::str::from_utf8(&raw).map_err(|e| e.to_string())?,
            thermal,
        )?
    } else {
        Value::Null
    };
    let closure = close(&mut t, &lease, &out, &state, "closed")?;
    let report = json!({"schema":"phonefarm/control_probe_report@1",
        "status":if req.transport.kind=="fake"{"SIMULATED_CONTROL_RESTORED"}else{"CONTROL_RESTORED"},
        "scope":"configuration_readback_only","evidence_origin":t.origin,"actual_serial":serial,"boot_id":boot,
        "hold_seconds":11,"temperature_profile_id":req.temperature_profile_id.as_deref().unwrap_or("legacy_cold_v1"),"temperature":thermal_hold,"cold":if capability{Value::Null}else{cold_receipt.clone()},"thermal_admission":if capability{cold_receipt}else{Value::Null},"temperature_reference_purpose":if capability{"diagnostic_only_no_comparison"}else{"existing_preset"},"receipts":refs,"closure":closure,
        "eligible":false,"experiment_eligible":false,"real_game_optimization_proven":false,"promotion_allowed":false,
        "actual_clock_sustained_proven":false,"power_window_verified":false});
    save_json(&out, "control-report.json", &report)?;
    lease.complete_recovery("configuration_readback_only", &t.origin,
        &[&out.join("control-report.json"), &out.join("journal.jsonl")])?;
    Ok(report)
}
pub(super) fn reconcile(dir: &Path) -> Result<Value, String> {
    let dir = dir.canonicalize().map_err(|e| e.to_string())?;
    let req = validate(&dir)?;
    let state = read_json(&dir.join("state.json"))?;
    let (root, legacy, timeout) = roots()?;
    let mut lease = Lease::acquire_recovery(&root, &legacy, &req.transport.target, &dir, timeout)?;
    let run = format!(
        "reconcile_{}",
        chrono::Utc::now().timestamp_nanos_opt().ok_or("time")?
    );
    let mut t = start_transport_target(&dir, &req.transport, &run)?;
    close(&mut t, &lease, &dir, &state, &run)?;
    lease.complete_recovery("configuration_readback_only", &t.origin,
        &[&dir.join(format!("{run}-snapshot_restored.txt")), &dir.join("journal.jsonl")])?;
    Ok(
        json!({"status":if req.transport.kind=="fake"{"SIMULATED_RECONCILED"}else{"RESTORED_REQUIRES_NEW_SESSION"},
        "scope":"configuration_readback_only","old_samples_revalidated":false,"eligible":false,
        "real_game_optimization_proven":false,"promotion_allowed":false}),
    )
}
