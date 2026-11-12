//! Receiver-owned heldout collection. Device execution requires --execute; fake is permanently synthetic.
use crate::{
    confirmation_control as control, confirmation_observation as observation,
    confirmation_transport::{bounded, journal, new_file, quote, sha, Transport},
    devlease::Lease,
    qualification::{Goal, Policy},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};
#[path = "confirmation_helper_diagnostics.rs"]
mod helper_diagnostics;
#[path = "confirmation_helper_native.rs"]
mod helper_native;
#[path = "confirmation_control_probe.rs"]
mod control_probe;
#[path = "confirmation_render_reader.rs"]
mod render_reader;
#[path = "confirmation_fixed_tools.rs"]
mod fixed_tools;
#[path = "confirmation_temperature.rs"]
pub(crate) mod temperature;
const CAP: u64 = 128 * 1024 * 1024;
const WORKER: &str = include_str!("../loop_v1/tools/confirmation_worker.sh");
const BOOTSTRAP: &str = include_str!("../loop_v1/tools/confirmation_bootstrap.sh");
const SNAPSHOT: &str = include_str!("../loop_v1/tools/device_snapshot.sh");
const PROBE: &str = include_str!("../loop_v1/tools/confirmation_probe_readonly.sh");
const ENV: &str = include_str!("../loop_v1/auto/sample_env.sh");
const COLD: &str = r#"set -eu
hot=0; zone=''
for z in /sys/class/thermal/thermal_zone*; do
 ty=$(cat "$z/type" 2>/dev/null || true)
 case "$ty" in cpu-*|cpullc*|gpuss*) ;; *) continue ;; esac
 t=$(cat "$z/temp")
 case "$t" in ''|*[!0-9]*) exit 80 ;; esac
 [ "$t" -gt 0 ] && [ "$t" -lt 100000 ] || exit 80
 if [ "$t" -gt "$hot" ]; then hot=$t; zone=$ty; fi
done
[ -n "$zone" ] || exit 80
printf '%s %s\n' "$zone" "$hot"
"#;
fn require(ok: bool, why: &str) -> Result<(), String> {
    if ok {
        Ok(())
    } else {
        Err(why.into())
    }
}
/// Safe-point pause: a flag file in the session directory or the job directory
/// holds the next phase without killing the process; removing it resumes.
fn pause_flag_paths(dir: &Path) -> Vec<PathBuf> {
    let mut paths = vec![dir.join("pause.requested")];
    if let Some(out) = std::env::var_os("PF_JOB_OUT") {
        if let Some(job) = Path::new(&out).parent() {
            paths.push(job.join("pause.requested"));
        }
    }
    paths
}

/// Block between phases while any pause flag exists; record both edges.
fn maybe_pause(dir: &Path) -> Result<(), String> {
    maybe_pause_with_poll(dir, Duration::from_secs(2))
}

fn maybe_pause_with_poll(dir: &Path, poll: Duration) -> Result<(), String> {
    let paths = pause_flag_paths(dir);
    if !paths.iter().any(|p| p.exists()) {
        return Ok(());
    }
    save_json(
        dir,
        "paused.json",
        &json!({"schema":"phonefarm/session-pause@1","paused_at":now(),
                "resume":"remove pause.requested from the session or job directory"}),
    )?;
    while paths.iter().any(|p| p.exists()) {
        std::thread::sleep(poll);
    }
    save_json(dir, "resumed.json", &json!({"schema":"phonefarm/session-pause@1","resumed_at":now()}))?;
    Ok(())
}

fn now() -> String {
    chrono::Utc::now().to_rfc3339()
}
fn safe(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 128
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.:-".contains(&b))
}
fn save(dir: &Path, name: &str, bytes: &[u8]) -> Result<Value, String> {
    new_file(&dir.join(name), bytes)?;
    Ok(json!({"path":name,"sha256":sha(bytes)}))
}
fn save_json(dir: &Path, name: &str, v: &Value) -> Result<Value, String> {
    save(
        dir,
        name,
        &serde_json::to_vec_pretty(v).map_err(|e| e.to_string())?,
    )
}
fn read_json(path: &Path) -> Result<Value, String> {
    serde_json::from_slice(&bounded(path, 2 * 1024 * 1024)?).map_err(|e| e.to_string())
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Target {
    kind: String,
    program: PathBuf,
    target: String,
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Pair {
    pair_id: String,
    a_run_id: String,
    b_run_id: String,
    baseline_first: bool,
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Request {
    schema: String,
    transport: Target,
    package: String,
    render_comm: String,
    capture_seconds: u64,
    lead_seconds: u64,
    workload: PathBuf,
    candidate: PathBuf,
    schedule: Vec<Pair>,
    paired_t_assumptions_acknowledged: bool,
    #[serde(default)]
    temperature_profile_id: Option<String>,
    #[serde(default)]
    goal: Option<Goal>,
    #[serde(default)]
    render_attribution_profile_id: Option<String>,
    #[serde(default)]
    render_reader_artifact: Option<PathBuf>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Workload {
    schema: String,
    actions: Vec<Action>,
}
#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum Action {
    Tap {
        x: u32,
        y: u32,
    },
    Swipe {
        x1: u32,
        y1: u32,
        x2: u32,
        y2: u32,
        duration_ms: u32,
    },
    Sleep {
        seconds: u64,
    },
}
fn workload(bytes: &[u8], req: &Request) -> Result<String, String> {
    let w: Workload = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    require(
        w.schema == "phonefarm/deterministic_touch_workload@1"
            && !w.actions.is_empty()
            && w.actions.len() <= 4096,
        "unknown/empty/too long workload",
    )?;
    let mut s = String::from("#!/system/bin/sh\nset -eu\n");
    let mut seconds = 0.;
    for action in w.actions {
        match action {
            Action::Tap { x, y } => {
                require(x <= 16384 && y <= 16384, "tap coordinates out of bounds")?;
                s += &format!("input tap {x} {y}\n");
            }
            Action::Swipe {
                x1,
                y1,
                x2,
                y2,
                duration_ms,
            } => {
                require(
                    [x1, y1, x2, y2].iter().all(|v| *v <= 16384)
                        && (1..=10000).contains(&duration_ms),
                    "swipe out of bounds",
                )?;
                s += &format!("input swipe {x1} {y1} {x2} {y2} {duration_ms}\n");
                seconds += duration_ms as f64 / 1000.;
            }
            Action::Sleep { seconds: n } => {
                require((1..=600).contains(&n), "sleep out of bounds")?;
                s += &format!("sleep {n}\n");
                seconds += n as f64;
            }
        }
    }
    require(
        seconds >= (req.capture_seconds + req.lead_seconds + 3) as f64 && seconds <= 1200.,
        "workload must cover lead+capture+3s and be <=1200s",
    )?;
    // Finite loop-free source. Phone state is checked, not launched or installed implicitly.
    Ok(s)
}
fn request(path: &Path) -> Result<(Request, Vec<u8>, Vec<u8>, Vec<u8>), String> {
    let bytes = bounded(path, 2 * 1024 * 1024)?;
    let req: Request = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    require(
        req.schema == "phonefarm/confirmation_driver_request@1"
            && matches!(req.transport.kind.as_str(), "adb" | "fake"),
        "unknown request/transport schema",
    )?;
    require(
        safe(&req.transport.target)
            && !req.transport.target.starts_with("hdc:")
            && safe(&req.package)
            && safe(&req.render_comm),
        "explicit Android target/package/render thread required",
    )?;
    require(
        (2..=600).contains(&req.capture_seconds)
            && (1..=30).contains(&req.lead_seconds)
            && (5..=32).contains(&req.schedule.len()),
        "capture/lead/pairs outside receiver bounds",
    )?;
    require(
        matches!(
            req.temperature_profile_id
                .as_deref()
                .unwrap_or("legacy_cold_v1"),
            "legacy_cold_v1" | "loaded_steady_v1"
        ),
        "unknown receiver temperature profile ID",
    )?;
    require(
        req.render_attribution_profile_id
            .as_deref()
            .is_none_or(|id| id == crate::render_attribution::PROFILE)
            && (req.render_attribution_profile_id.is_none()
                || req.goal == Some(Goal::RenderCadence)),
        "unknown ownership profile or non-cadence attribution goal",
    )?;
    require(req.render_attribution_profile_id.is_some() == req.render_reader_artifact.is_some(), "queued-origin requires explicit native reader artifact; legacy cannot add it")?;
    if let Some(path)=&req.render_reader_artifact { render_reader::artifact(path)?; }
    let mut ids = BTreeSet::new();
    for (i, p) in req.schedule.iter().enumerate() {
        require(
            p.baseline_first == (i % 2 == 0),
            "schedule must alternate AB/BA from baseline-first",
        )?;
        for id in [&p.pair_id, &p.a_run_id, &p.b_run_id] {
            require(
                safe(id) && !id.starts_with("admission_") && ids.insert(id.clone()),
                "duplicate/unsafe/reserved role ID",
            )?;
        }
    }
    require(
        req.paired_t_assumptions_acknowledged,
        "paired t assumptions must be explicitly acknowledged",
    )?;
    let w = bounded(&req.workload, 2 * 1024 * 1024)?;
    workload(&w, &req)?;
    let a = bounded(&req.candidate, 2 * 1024 * 1024)?;
    observation::validate_driver_artifact(&a)?;
    let artifact: Value = serde_json::from_slice(&a).map_err(|e| e.to_string())?;
    require(
        artifact["payload"]["params"]
            .as_object()
            .is_some_and(|p| !p.is_empty()),
        "empty candidate",
    )?;
    for (id, v) in artifact["payload"]["params"].as_object().unwrap() {
        require(
            !crate::sysparam::denied(id) && v.as_str().is_some_and(safe),
            "forbidden/unsafe control",
        )?;
    }
    bounded(&req.transport.program, CAP)?;
    Ok((req, bytes, w, a))
}
fn roots() -> Result<(PathBuf, PathBuf, Option<Duration>), String> {
    let (root, legacy) = crate::lease_storage::resolve().roots()?;
    Ok((root, legacy, crate::devlease::wait_timeout()?))
}
fn snapshot_source(render: bool) -> String {
    if render { SNAPSHOT.replace("echo \"# end\"", "printf 'ftrace.trace_clock=%s\\n' \"$(cat /sys/kernel/tracing/trace_clock)\"\necho \"# end\"") }
    else { SNAPSHOT.into() }
}
fn start_transport(dir: &Path, req: &Request, phase: &str) -> Result<Transport, String> {
    start_transport_target(dir, &req.transport, phase)
}
fn start_transport_target(dir: &Path, target: &Target, phase: &str) -> Result<Transport, String> {
    let p = dir.join(phase);
    fs::create_dir(&p).map_err(|e| e.to_string())?;
    let program = dir.join("transport.snapshot");
    let mut input_hashes = BTreeMap::new();
    let render = read_json(&dir.join("request.json"))?.get("render_attribution_profile_id").is_some_and(|v| !v.is_null());
    let snapshot = snapshot_source(render);
    for (name, expected) in [
        ("worker.sh", WORKER),
        ("bootstrap.sh", BOOTSTRAP),
        ("snapshot.sh", snapshot.as_str()),
        ("probe_readonly.sh", PROBE),
        ("sample_env.sh", ENV),
        ("cold.sh", COLD),
    ] {
        input_hashes.insert(dir.join(name), sha(expected.as_bytes()));
    }
    if dir.join("inputs.manifest.json").exists() {
        register_inputs(&mut input_hashes, dir)?;
    }
    Ok(Transport {
        input_hashes,
        expected_sha: sha(&bounded(&program, CAP)?),
        program,
        target: target.target.clone(),
        origin: if target.kind == "fake" {
            "synthetic"
        } else {
            "live_device"
        }
        .into(),
        dir: p,
        sequence: 0,
    })
}
fn register_inputs(map: &mut BTreeMap<PathBuf, String>, dir: &Path) -> Result<(), String> {
    let v = read_json(&dir.join("inputs.manifest.json"))?;
    for (name, h) in v["hashes"]
        .as_object()
        .ok_or("missing frozen input hash table")?
    {
        require(safe(name), "unsafe frozen input filename")?;
        map.insert(
            dir.join(name),
            h.as_str().ok_or("invalid frozen digest")?.to_owned(),
        );
    }
    Ok(())
}
fn checked(
    t: &mut Transport,
    op: &str,
    cmd: &str,
    input: Option<&Path>,
    timeout: u64,
    cap: u64,
    lease: &Lease,
) -> Result<Vec<u8>, String> {
    let r = t.shell(op, cmd, input, timeout, cap, Some(lease))?;
    require(
        r.code == 0,
        &format!(
            "{op} exited {}: {}",
            r.code,
            String::from_utf8_lossy(&r.stderr)
        ),
    )?;
    Ok(r.stdout)
}
fn identity(t: &mut Transport, lease: &Lease, package: &str) -> Result<(String, String), String> {
    let b=checked(t,"identity","set -eu; id; getprop ro.serialno; cat /proc/sys/kernel/random/boot_id; command -v setsid; test -x /system/bin/kill; test -r /sys/kernel/tracing/events/kgsl/adreno_cmdbatch_submitted/enable; test -r /sys/kernel/tracing/events/kgsl/adreno_cmdbatch_retired/enable",None,10,65536,lease)?;
    let s = String::from_utf8(b).map_err(|e| e.to_string())?;
    let mut lines = s.lines();
    let root = lines.next().ok_or("missing root")?;
    require(
        root.split_whitespace()
            .filter(|s| s.starts_with("uid="))
            .count()
            == 1
            && root
                .split_whitespace()
                .any(|s| s.starts_with("uid=0(") || s == "uid=0"),
        "root is unknown/unavailable before write",
    )?;
    let serial = lines
        .next()
        .ok_or("missing actual serial")?
        .trim()
        .to_owned();
    let boot = lines.next().ok_or("missing boot")?.trim().to_owned();
    require(
        safe(&serial) && safe(&boot) && lines.next().is_some_and(|s| s.starts_with('/')),
        "unknown serial/boot/setsid",
    )?;
    // Restoration must remain possible after the workload/game itself has crashed.
    // Only reconcile omits the foreground requirement; sampling still requires it.
    if package.is_empty() {
        return Ok((serial, boot));
    }
    let app = checked(
        t,
        "foreground",
        "dumpsys activity activities",
        None,
        10,
        2 * 1024 * 1024,
        lease,
    )?;
    let app = String::from_utf8(app).map_err(|e| e.to_string())?;
    require(
        app.lines()
            .filter(|l| l.contains("mResumedActivity") || l.contains("topResumedActivity"))
            .any(|l| {
                l.split_whitespace()
                    .any(|w| w.starts_with(&format!("{package}/")))
            }),
        "requested game is not the observed foreground activity",
    )?;
    Ok((serial, boot))
}
fn snap_bytes(bytes: Vec<u8>) -> Result<Vec<u8>, String> {
    let s = String::from_utf8(bytes).map_err(|e| e.to_string())?;
    require(
        s.starts_with("# device_snapshot v1\n") && s.lines().last() == Some("# end"),
        "incomplete snapshot script output",
    )?;
    Ok(format!("{}\n#adb_exit=0", s.trim_end()).into_bytes())
}
fn current_snapshot(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    policies: &[String],
) -> Result<Vec<u8>, String> {
    let b = checked(
        t,
        "snapshot",
        "sh",
        Some(&dir.join("snapshot.sh")),
        15,
        2 * 1024 * 1024,
        lease,
    )?;
    let b = snap_bytes(b)?;
    observation::snapshot(
        std::str::from_utf8(&b).map_err(|e| e.to_string())?,
        policies,
    )?;
    Ok(b)
}
fn freeze(dir: &Path, names: &[&str]) -> Result<(), String> {
    let mut hashes = BTreeMap::new();
    for name in names {
        hashes.insert((*name).to_owned(), sha(&bounded(&dir.join(name), CAP)?));
    }
    save_json(
        dir,
        "inputs.manifest.json",
        &json!({"schema":"phonefarm/driver_frozen_inputs@1","hashes":hashes}),
    )?;
    Ok(())
}
fn frozen_names(dir: &Path) -> Result<BTreeSet<String>, String> {
    if read_json(&dir.join("request.json"))?["schema"] == "phonefarm/control_probe_request@1" {
        return control_probe::frozen_names(dir);
    }
    let artifact = read_json(&dir.join("candidate.json"))?;
    let count = artifact["payload"]["params"]
        .as_object()
        .ok_or("candidate params missing")?
        .len();
    require((1..=32).contains(&count), "candidate control count invalid")?;
    let mut names = [
        "worker.sh",
        "bootstrap.sh",
        "snapshot.sh",
        "sample_env.sh",
        "workload.sh",
        "restore.tsv",
        "candidate.plan.tsv",
        "ftrace-current_tracer",
        "ftrace-buffer_size_kb",
        "ftrace-set_event",
        "ftrace-tracing_on",
        "request.json",
        "workload.json",
        "candidate.json",
        "transport.snapshot",
        "probe_readonly.sh",
        "cold.sh",
        "sampling-contract.json",
        "initial.snapshot",
        "state.json",
        "conditions.json",
        "baseline.json",
    ]
    .into_iter()
    .map(String::from)
    .collect::<BTreeSet<_>>();
    for i in 0..count {
        names.insert(format!("probe_{i}.plan.tsv"));
    }
    if read_json(&dir.join("request.json"))?["temperature_profile_id"] == "loaded_steady_v1" {
        names.extend(temperature::frozen_names().iter().map(|s| (*s).to_owned()));
    }
    if read_json(&dir.join("request.json"))?["render_attribution_profile_id"]
        == crate::render_attribution::PROFILE
    {
        names.extend(
            [
                "render_ownership.sh",
                "render_args.tsv",
                "render-initial.raw.tsv",
                "ftrace-trace_clock",
            ]
            .into_iter()
            .map(String::from),
        );
    }
    if read_json(&dir.join("request.json"))?["render_attribution_profile_id"]==crate::render_attribution::PROFILE {names.extend(render_frozen_extra().into_iter().map(String::from));}
    names.extend(fixed_tools::FROZEN_NAMES.map(String::from));
    Ok(names)
}
fn remote_tool_names(dir: &Path) -> Result<BTreeSet<String>, String> {
    let request = read_json(&dir.join("request.json"))?;
    let control = request["schema"] == "phonefarm/control_probe_request@1";
    require(
        control || request["schema"] == "phonefarm/confirmation_driver_request@1",
        "unknown fixed tool purpose",
    )?;
    let count = read_json(&dir.join("candidate.json"))?["payload"]["params"]
        .as_object()
        .ok_or("missing fixed tool control count")?
        .len();
    let preset = request["temperature_profile_id"].as_str();
    fixed_tools::names(
        control,
        matches!(preset, Some("loaded_steady_v1" | "capability_absolute_v1")),
        preset == Some("capability_absolute_v1"),
        count,
        request["render_attribution_profile_id"] == crate::render_attribution::PROFILE,
    )
}
fn bundle_from_state(dir: &Path) -> Result<fixed_tools::Bundle, String> {
    let state = read_json(&dir.join("state.json"))?;
    fixed_tools::validate_frozen(
        dir,
        &remote_tool_names(dir)?,
        state["remote"]
            .as_str()
            .ok_or("missing fixed tool remote")?,
        state["token"].as_str().ok_or("missing fixed tool owner")?,
        state["boot_id"].as_str().ok_or("missing fixed tool boot")?,
    )
}
fn freeze_tool_bundle(dir: &Path, files: &[String]) -> Result<(), String> {
    let names = remote_tool_names(dir)?;
    require(
        files.len() == names.len() && files.iter().cloned().collect::<BTreeSet<_>>() == names,
        "remote install files differ from receiver fixed set",
    )?;
    let state = read_json(&dir.join("state.json"))?;
    let bundle = fixed_tools::compile(
        dir,
        &names,
        state["remote"]
            .as_str()
            .ok_or("missing fixed tool remote")?,
        state["token"].as_str().ok_or("missing fixed tool owner")?,
        state["boot_id"].as_str().ok_or("missing fixed tool boot")?,
    )?;
    save(dir, "tools.manifest.json", &bundle.manifest_bytes)?;
    save(dir, "tools.install.sh", &bundle.installer)?;
    save(dir, "tools.verify.sh", &bundle.verifier)?;
    Ok(())
}
fn verify(dir: &Path) -> Result<(), String> {
    let v = read_json(&dir.join("inputs.manifest.json"))?;
    require(
        v["schema"] == "phonefarm/driver_frozen_inputs@1",
        "unknown frozen inputs",
    )?;
    let hashes = v["hashes"].as_object().ok_or("missing frozen hashes")?;
    require(
        hashes.keys().cloned().collect::<BTreeSet<_>>() == frozen_names(dir)?,
        "incomplete/extra frozen input manifest",
    )?;
    for (name, h) in hashes {
        require(
            safe(name) && h.as_str() == Some(&sha(&bounded(&dir.join(name), CAP)?)),
            "frozen input bytes changed",
        )?;
    }
    bundle_from_state(dir)?;
    Ok(())
}
fn prepared_names(dir: &Path) -> Result<BTreeSet<String>, String> {
    let mut names = frozen_names(dir)?;
    for name in [
        "inputs.manifest.json",
        "probe.txt",
        "admission.json",
        "plan.json",
        "admission-validation.json",
        "prepare-final-restored.snapshot",
    ] {
        names.insert(name.into());
    }
    let count = read_json(&dir.join("candidate.json"))?["payload"]["params"]
        .as_object()
        .unwrap()
        .len();
    for i in 0..count {
        for suffix in [
            "snapshot_before.txt",
            "snapshot_restored.txt",
            "receipt.txt",
        ] {
            names.insert(format!("probe_{i}-{suffix}"));
        }
        if read_json(&dir.join("request.json"))?["temperature_profile_id"] == "loaded_steady_v1" {
            names.insert(format!("pre-probe_{i}-thermal.raw.tsv"));
            names.insert(format!("pre-probe_{i}-thermal.json"));
        }
    }
    for i in 0..3 {
        for suffix in [
            "trace.txt",
            "sampler.log",
            "workload_exit.txt",
            "sampler_exit.txt",
            "capture_exit.txt",
            "restore_exit.txt",
            "root_id.txt",
            "boot_id.txt",
            "snapshot_before.txt",
            "snapshot_at_run.txt",
            "snapshot_after_run.txt",
            "snapshot_restored.txt",
            "run.json",
            "validation.json",
        ] {
            names.insert(format!("admission_{i}-{suffix}"));
        }
        let loaded =
            read_json(&dir.join("request.json"))?["temperature_profile_id"] == "loaded_steady_v1";
        for suffix in if loaded {
            vec![
                "pre-thermal.raw.tsv",
                "pre-thermal.json",
                "thermal_sampler.log",
                "thermal_sampler_exit.txt",
            ]
        } else {
            vec!["cold.json"]
        } {
            names.insert(format!("admission_{i}-{suffix}"));
        }
    }
    if read_json(&dir.join("request.json"))?["render_attribution_profile_id"]==crate::render_attribution::PROFILE {
        names.insert("render-contract.json".into());
        for i in 0..3 {for suffix in ["render-before.raw.tsv","render-after.raw.tsv","render-final.raw.tsv"]{names.insert(format!("admission_{i}-{suffix}"));}}
    }
    Ok(names)
}
fn render_frozen_extra() -> Vec<&'static str> {
    let mut n = render_reader::FILES.to_vec();
    n.extend([
        "render_close.sh",
        "render-preinstall.manifest.json",
        "render-helper.intent.json",
        "render-helper-close.stdout",
        "render-helper-remove.stdout",
        "render-helper-closed.json",
        "render-helper-removed.json",
        "main-install.intent.json",
    ]);
    n
}
fn preinstall_names(dir: &Path) -> Result<BTreeSet<String>, String> {
    let mut n = frozen_names(dir)?;
    for k in fixed_tools::FROZEN_NAMES {
        n.remove(k);
    }
    for k in [
        "sampling-contract.json",
        "conditions.json",
        "baseline.json",
        "render-initial.raw.tsv",
    ] {
        n.remove(k);
    }
    for k in render_frozen_extra() {
        n.remove(k);
    }
    n.extend(render_reader::FILES.into_iter().map(String::from));
    n.insert("render_close.sh".into());
    Ok(n)
}
fn validate_preinstall(dir: &Path, req: &Request) -> Result<Value, String> {
    require(
        req.render_attribution_profile_id.as_deref() == Some(crate::render_attribution::PROFILE)
            && req.render_reader_artifact.is_some(),
        "not a native preinstall profile",
    )?;
    let m = read_json(&dir.join("render-preinstall.manifest.json"))?;
    let h = m["hashes"].as_object().ok_or("preinstall hashes missing")?;
    require(
        m["schema"] == "phonefarm/render_preinstall_inputs@1"
            && m.as_object().is_some_and(|o| o.len() == 2)
            && h.keys().cloned().collect::<BTreeSet<_>>() == preinstall_names(dir)?,
        "incomplete/extra preinstall input manifest",
    )?;
    for (n, s) in h {
        require(
            s.as_str() == Some(&sha(&bounded(&dir.join(n), CAP)?)),
            "preinstall input bytes changed",
        )?;
    }
    render_reader::verify(dir)?;
    let snapshot = snapshot_source(true);
    for (n, s) in [
        ("worker.sh", WORKER),
        ("bootstrap.sh", BOOTSTRAP),
        ("snapshot.sh", snapshot.as_str()),
        ("sample_env.sh", ENV),
        ("probe_readonly.sh", PROBE),
        ("cold.sh", COLD),
        ("render_ownership.sh", crate::render_attribution::COLLECTOR),
    ] {
        require(
            bounded(&dir.join(n), CAP)? == s.as_bytes(),
            "preinstall receiver tool changed",
        )?;
    }
    require(
        bounded(&dir.join("workload.sh"), CAP)?
            == workload(&bounded(&dir.join("workload.json"), 2 * 1024 * 1024)?, req)?.as_bytes()
            && bounded(&dir.join("render_args.tsv"), 65536)?
                == format!("{}\t{}\n", req.package, req.render_comm).as_bytes(),
        "preinstall arguments/workload changed",
    )?;
    let s = read_json(&dir.join("state.json"))?;
    let token = s["token"].as_str().ok_or("preinstall token")?;
    let boot = s["boot_id"].as_str().ok_or("preinstall boot")?;
    require(
        s["schema"] == "phonefarm/confirmation_driver_state@1"
            && safe(token)
            && safe(boot)
            && s["target"] == req.transport.target
            && s["remote"] == format!("/data/local/tmp/phonefarm-confirmation-{token}")
            && s["helper_remote"]
                == format!("/data/local/tmp/phonefarm-confirmation-{token}-reader"),
        "preinstall state identity mismatch",
    )?;
    Ok(s)
}
fn register_preinstall(t: &mut Transport, dir: &Path) -> Result<(), String> {
    for (n, h) in read_json(&dir.join("render-preinstall.manifest.json"))?["hashes"]
        .as_object()
        .ok_or("preinstall hashes")?
    {
        t.input_hashes
            .insert(dir.join(n), h.as_str().ok_or("preinstall digest")?.into());
    }
    Ok(())
}
fn helper_state(state: &Value) -> Result<Value, String> {
    let mut h = state.clone();
    h["remote"] = state
        .get("helper_remote")
        .ok_or("helper remote missing")?
        .clone();
    Ok(h)
}
fn render_initial(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    req: &Request,
    state: &Value,
) -> Result<Vec<u8>, String> {
    let h = helper_state(state)?;
    let files = [
        "worker.sh",
        "bootstrap.sh",
        "render_ownership.sh",
        "render_reader.elf",
        "render_args.tsv",
    ]
    .into_iter()
    .map(String::from)
    .collect::<Vec<_>>();
    save_json(
        dir,
        "render-helper.intent.json",
        &json!({"schema":"phonefarm/render_helper_intent@1","state_sha256":file_ref(dir,"state.json")?["sha256"],"preinstall_sha256":file_ref(dir,"render-preinstall.manifest.json")?["sha256"],"remote":h["remote"],"token":h["token"],"boot_id":h["boot_id"],"files":files}),
    )?;
    install_reader_helper(
        t,
        lease,
        dir,
        h["remote"].as_str().unwrap(),
        h["token"].as_str().unwrap(),
        h["boot_id"].as_str().unwrap(),
        &files,
    )?;
    let done = worker(t, lease, &h, "render_readonly", "render_initial", "", 28)?;
    require(
        done == b"READONLY_COMPLETE\n",
        "native initial reader completion missing",
    )?;
    require(
        pull(t, lease, &h, "render_initial", "render_exit.txt")? == b"0\n"
            && pull(t, lease, &h, "render_initial", "quiescence.txt")? == b"OWNED_GROUPS_ABSENT\n",
        "native reader exit/group unknown",
    )?;
    let raw = pull(t, lease, &h, "render_initial", "render_reader.log")?;
    require(
        raw.len() <= 2 * 1024 * 1024,
        "native initial witness output bound",
    )?;
    // actual witness parser checks the complete task table and all seven formats.
    crate::render_attribution::witness(
        std::str::from_utf8(&raw).map_err(|e| e.to_string())?,
        &req.package,
        &req.render_comm,
    )?;
    Ok(raw)
}
fn close_helper(t: &mut Transport, lease: &Lease, dir: &Path, state: &Value) -> Result<(), String> {
    let h = helper_state(state)?;
    let run = format!(
        "helper_reconcile_{}",
        chrono::Utc::now()
            .timestamp_nanos_opt()
            .ok_or("time overflow")?
    );
    let done = worker(t, lease, &h, "reconcile_render", &run, "", 45)?;
    require(
        done == b"READONLY_RECONCILE_COMPLETE\n",
        "helper reconciliation completion missing",
    )?;
    for (mode, operation, name) in [
        (
            "verify",
            "render_helper_close",
            "render-helper-close.stdout",
        ),
        (
            "remove",
            "render_helper_remove",
            "render-helper-remove.stdout",
        ),
    ] {
        let raw = checked(
            t,
            operation,
            &format!(
                "sh -s -- {} {} {} {} {}",
                quote(h["remote"].as_str().unwrap()),
                quote(h["token"].as_str().unwrap()),
                quote(h["boot_id"].as_str().unwrap()),
                quote(&run),
                quote(mode)
            ),
            Some(&dir.join("render_close.sh")),
            20,
            65536,
            lease,
        )?;
        let expected = format!(
            "{} {} {} {}\n",
            if mode == "verify" {
                "HELPER_CLOSED"
            } else {
                "HELPER_REMOVED"
            },
            h["token"].as_str().unwrap(),
            h["boot_id"].as_str().unwrap(),
            run
        );
        require(
            raw == expected.as_bytes(),
            "helper closure/group/owner receipt unknown",
        )?;
        save(dir, name, &raw)?;
        if mode == "verify" {
            save_json(
                dir,
                "render-helper-closed.json",
                &json!({"schema":"phonefarm/render_helper_closed@1","state_sha256":file_ref(dir,"state.json")?["sha256"],"run_id":run,"stdout":file_ref(dir,name)?,"control_writes":false,"ftrace_writes":false,"eligible":false}),
            )?;
        } else {
            save_json(
                dir,
                "render-helper-removed.json",
                &json!({"schema":"phonefarm/render_helper_removed@1","state_sha256":file_ref(dir,"state.json")?["sha256"],"closed_sha256":file_ref(dir,"render-helper-closed.json")?["sha256"],"stdout":file_ref(dir,name)?,"eligible":false}),
            )?;
        }
    }
    helper_absent(t, lease, state)
}
fn validate_helper_intent(dir: &Path, state: &Value) -> Result<(), String> {
    let h = helper_state(state)?;
    let files = [
        "worker.sh",
        "bootstrap.sh",
        "render_ownership.sh",
        "render_reader.elf",
        "render_args.tsv",
    ]
    .into_iter()
    .map(String::from)
    .collect::<Vec<_>>();
    require(
        read_json(&dir.join("render-helper.intent.json"))?
            == json!({"schema":"phonefarm/render_helper_intent@1","state_sha256":file_ref(dir,"state.json")?["sha256"],"preinstall_sha256":file_ref(dir,"render-preinstall.manifest.json")?["sha256"],"remote":h["remote"],"token":h["token"],"boot_id":h["boot_id"],"files":files}),
        "native helper intent/state mismatch",
    )
}
fn validate_helper_removed(dir: &Path) -> Result<(), String> {
    let s = read_json(&dir.join("state.json"))?;
    let c = read_json(&dir.join("render-helper-closed.json"))?;
    let r = read_json(&dir.join("render-helper-removed.json"))?;
    validate_helper_intent(dir, &s)?;
    if dir.join("main-install.intent.json").exists() {
        require(
            read_json(&dir.join("main-install.intent.json"))?
                == json!({"schema":"phonefarm/main_install_intent@1","state_sha256":file_ref(dir,"state.json")?["sha256"],"helper_removed_sha256":file_ref(dir,"render-helper-removed.json")?["sha256"]}),
            "main intent not bound to native closure",
        )?;
    }
    let run = c["run_id"].as_str().ok_or("helper close run")?;
    require(safe(run), "unsafe helper closure run")?;
    let closed = format!(
        "HELPER_CLOSED {} {} {run}\n",
        s["token"].as_str().ok_or("token")?,
        s["boot_id"].as_str().ok_or("boot")?
    );
    let removed = closed.replace("HELPER_CLOSED", "HELPER_REMOVED");
    require(
        c == json!({"schema":"phonefarm/render_helper_closed@1","state_sha256":file_ref(dir,"state.json")?["sha256"],"run_id":run,"stdout":file_ref(dir,"render-helper-close.stdout")?,"control_writes":false,"ftrace_writes":false,"eligible":false})
            && r == json!({"schema":"phonefarm/render_helper_removed@1","state_sha256":file_ref(dir,"state.json")?["sha256"],"closed_sha256":file_ref(dir,"render-helper-closed.json")?["sha256"],"stdout":file_ref(dir,"render-helper-remove.stdout")?,"eligible":false})
            && bounded(&dir.join("render-helper-close.stdout"), 65536)? == closed.as_bytes()
            && bounded(&dir.join("render-helper-remove.stdout"), 65536)? == removed.as_bytes(),
        "native helper closure binding changed",
    )
}
fn helper_absent(t: &mut Transport, lease: &Lease, state: &Value) -> Result<(), String> {
    let remote = state["helper_remote"].as_str().ok_or("helper remote")?;
    require(
        checked(
            t,
            "render_helper_absent",
            &format!(
                "set -eu; test \"$(cat /proc/sys/kernel/random/boot_id)\" = {}; test ! -e {}; test ! -L {}; printf 'HELPER_ABSENT\\n'",
                quote(state["boot_id"].as_str().ok_or("boot")?),
                quote(remote),
                quote(remote)
            ),
            None,
            10,
            65536,
            lease,
        )? == b"HELPER_ABSENT\n",
        "helper absence unknown",
    )
}
fn reconcile_helper(dir: &Path) -> Result<Value, String> {
    let req: Request =
        serde_json::from_slice(&bounded(&dir.join("request.json"), 2 * 1024 * 1024)?)
            .map_err(|e| e.to_string())?;
    let state = validate_preinstall(dir, &req)?;
    validate_helper_intent(dir, &state)?;
    let events = bounded(&dir.join("journal.jsonl"), 8 * 1024 * 1024)?;
    for l in events.split(|b| *b == b'\n').filter(|l| !l.is_empty()) {
        let e: Value = serde_json::from_slice(l).map_err(|e| e.to_string())?;
        require(
            !matches!(
                e["event"].as_str(),
                Some("remote_install_intent" | "sample_intent")
            ) && !matches!(
                e["operation"].as_str(),
                Some("worker_probe" | "worker_sample" | "worker_control_probe")
            ),
            "helper-only reconciliation forbidden after main intent",
        )?;
    }
    require(
        !dir.join("main-install.intent.json").exists()
            && !dir.join("inputs.manifest.json").exists(),
        "main phase requires full reconciliation",
    )?;
    let (root, legacy, timeout) = roots()?;
    let mut lease = Lease::acquire_recovery(&root, &legacy, &req.transport.target, dir, timeout)?;
    let phase = format!(
        "helper_recovery_{}",
        chrono::Utc::now()
            .timestamp_nanos_opt()
            .ok_or("time overflow")?
    );
    let mut t = start_transport(dir, &req, &phase)?;
    register_preinstall(&mut t, dir)?;
    let actual = identity(&mut t, &lease, "")?;
    require(
        actual.0 == state["serial"] && actual.1 == state["boot_id"],
        "helper recovery actual identity changed",
    )?;
    if dir.join("render-helper-removed.json").exists() {
        validate_helper_removed(dir)?;
        helper_absent(&mut t, &lease, &state)?;
        journal(
            dir,
            json!({"event":"helper_only_reconciliation_verified","old_samples_revalidated":false,"at":now()}),
        )?;
        lease.complete_recovery("helper_retirement_only", &t.origin, &[&dir.join("render-helper-removed.json"), &dir.join("journal.jsonl")])?;
        return Ok(
            json!({"status":if req.transport.kind=="fake"{"SIMULATED_HELPER_RECONCILED"}else{"HELPER_CLOSED_REQUIRES_NEW_SESSION"},"old_samples_revalidated":false,"control_restore_claimed":false,"eligible":false,"real_game_optimization_proven":false,"promotion_allowed":false}),
        );
    }
    let h = helper_state(&state)?;
    for n in [
        "worker.sh",
        "bootstrap.sh",
        "render_ownership.sh",
        "render_reader.elf",
        "render_args.tsv",
    ] {
        require(
            checked(
                &mut t,
                "verify_helper_file",
                &format!("cat {}/{}", quote(h["remote"].as_str().unwrap()), quote(n)),
                None,
                15,
                CAP,
                &lease,
            )? == bounded(&dir.join(n), CAP)?,
            "helper recovery execution bytes unknown",
        )?;
    }
    close_helper(&mut t, &lease, dir, &state)?;
    journal(
        dir,
        json!({"event":"helper_only_reconciliation_verified","old_samples_revalidated":false,"at":now()}),
    )?;
    lease.complete_recovery("helper_retirement_only", &t.origin, &[&dir.join("render-helper-removed.json"), &dir.join("journal.jsonl")])?;
    Ok(
        json!({"status":if req.transport.kind=="fake"{"SIMULATED_HELPER_RECONCILED"}else{"HELPER_CLOSED_REQUIRES_NEW_SESSION"},"old_samples_revalidated":false,"control_restore_claimed":false,"eligible":false,"real_game_optimization_proven":false,"promotion_allowed":false}),
    )
}

fn install_reader_helper(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    remote: &str,
    token: &str,
    boot: &str,
    files: &[String],
) -> Result<(), String> {
    let request: Request = serde_json::from_value(read_json(&dir.join("request.json"))?)
        .map_err(|e| e.to_string())?;
    let state = validate_preinstall(dir, &request)?;
    let helper = helper_state(&state)?;
    let expected = ["worker.sh", "bootstrap.sh", "render_ownership.sh", "render_reader.elf", "render_args.tsv"]
        .map(String::from).into_iter().collect::<BTreeSet<_>>();
    require(
        !dir.join("inputs.manifest.json").exists()
            && !dir.join("main-install.intent.json").exists()
            && helper["remote"] == remote && helper["token"] == token && helper["boot_id"] == boot
            && files.len() == expected.len() && files.iter().cloned().collect::<BTreeSet<_>>() == expected,
        "helper-only installation identity/set/phase changed",
    )?;
    journal(
        dir,
        json!({"event":if remote.ends_with("-reader"){"render_helper_install_intent"}else{"remote_install_intent"},"remote":remote,"token":token,"boot_id":boot,"files":files,"at":now()}),
    )?;
    checked(
        t,
        "install_owner",
        &format!(
            "set -eu; umask 077; mkdir {}; printf '%s\\n' {} > {}/owner; sync",
            quote(remote),
            quote(&format!("{token} {boot}")),
            quote(remote)
        ),
        None,
        10,
        65536,
        lease,
    )?;
    for name in files {
        require(safe(name), "unsafe frozen filename")?;
        let cmd=format!("set -eu; test ! -e {}/session.closed; test \"$(cat {}/owner)\" = {}; test \"$(cat /proc/sys/kernel/random/boot_id)\" = {}; cat > {}/{}; sync",quote(remote),quote(remote),quote(&format!("{token} {boot}")),quote(boot),quote(remote),quote(name));
        checked(
            t,
            "install_file",
            &cmd,
            Some(&dir.join(name)),
            15,
            65536,
            lease,
        )?;
        let read = checked(
            t,
            "verify_remote_file",
            &format!("cat {}/{}", quote(remote), quote(name)),
            None,
            15,
            CAP,
            lease,
        )?;
        require(
            read == bounded(&dir.join(name), CAP)?,
            "remote frozen file bytes differ",
        )?;
    }
    if files.iter().any(|n|n=="render_reader.elf") {
        checked(t,"reader_executable_mode",&format!("set -eu; test \"$(cat {}/owner)\" = {}; test \"$(cat /proc/sys/kernel/random/boot_id)\" = {}; chmod 700 {}/render_reader.elf; test -x {}/render_reader.elf",quote(remote),quote(&format!("{token} {boot}")),quote(boot),quote(remote),quote(remote)),None,10,65536,lease)?;
    }
    Ok(())
}
fn install(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    remote: &str,
    token: &str,
    boot: &str,
    files: &[String],
) -> Result<(), String> {
    verify(dir)?;
    let bundle = bundle_from_state(dir)?;
    require(
        bundle.manifest.remote == remote
            && bundle.manifest.token == token
            && bundle.manifest.boot_id == boot
            && files.len() == bundle.manifest.count
            && files.iter().cloned().collect::<BTreeSet<_>>() == remote_tool_names(dir)?,
        "fixed tool installation identity/set changed",
    )?;
    journal(
        dir,
        json!({"event":if remote.ends_with("-reader"){"render_helper_install_intent"}else{"remote_install_intent"},"remote":remote,"token":token,"boot_id":boot,"files":files,"at":now()}),
    )?;
    let receipt = checked(
        t,
        "install_fixed_tools",
        "sh",
        Some(&dir.join("tools.install.sh")),
        30,
        65536,
        lease,
    )?;
    bundle.accept_install(&receipt)
}
fn verify_remote(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    state: &Value,
) -> Result<(), String> {
    verify(dir)?;
    if state.get("helper_remote").is_some(){validate_helper_removed(dir)?;helper_absent(t,lease,state)?;}
    let bundle = bundle_from_state(dir)?;
    require(
        state == &read_json(&dir.join("state.json"))?,
        "fixed tool verification state changed",
    )?;
    let actual = checked(
        t,
        "verify_fixed_tools",
        "sh",
        Some(&dir.join("tools.verify.sh")),
        30,
        65536,
        lease,
    )?;
    bundle.accept_verify(&actual)
}
fn worker(
    t: &mut Transport,
    lease: &Lease,
    state: &Value,
    mode: &str,
    id: &str,
    extra: &str,
    timeout: u64,
) -> Result<Vec<u8>, String> {
    require(safe(id), "unsafe remote run")?;
    checked(
        t,
        &format!("worker_{mode}"),
        &format!(
            "setsid sh {}/worker.sh {} {} {} {} {} {}",
            quote(state["remote"].as_str().ok_or("remote")?),
            quote(state["remote"].as_str().unwrap()),
            quote(state["token"].as_str().ok_or("token")?),
            quote(state["boot_id"].as_str().ok_or("boot")?),
            quote(mode),
            quote(id),
            extra
        ),
        None,
        timeout,
        2 * 1024 * 1024,
        lease,
    )
}
fn pull(
    t: &mut Transport,
    lease: &Lease,
    state: &Value,
    run: &str,
    file: &str,
) -> Result<Vec<u8>, String> {
    require(safe(run) && safe(file), "unsafe raw pull")?;
    checked(
        t,
        "pull_raw",
        &format!(
            "cat {}/{}/{}",
            quote(state["remote"].as_str().ok_or("remote")?),
            quote(run),
            quote(file)
        ),
        None,
        20,
        CAP,
        lease,
    )
}
fn cold(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    fake: bool,
    stamp: &str,
) -> Result<Value, String> {
    let begin = Instant::now();
    let mut n = 0;
    loop {
        let b = checked(
            t,
            "cold",
            "sh",
            Some(&dir.join("cold.sh")),
            10,
            65536,
            lease,
        )?;
        let s = String::from_utf8(b).map_err(|e| e.to_string())?;
        let w = s.split_whitespace().collect::<Vec<_>>();
        require(w.len() == 2, "malformed actual thermal read")?;
        let c = w[1]
            .parse::<f64>()
            .map_err(|_| "invalid actual temperature")?
            / 1000.;
        require(
            c.is_finite()
                && c > 0.
                && c < 100.
                && (w[0].starts_with("cpu-")
                    || w[0].starts_with("cpullc")
                    || w[0].starts_with("gpuss")),
            "unknown SoC cold source",
        )?;
        if c < 40. {
            return Ok(
                json!({"checked_at":if fake{stamp.to_owned()}else{now()},"zone":w[0],"c":c,"waited_s":begin.elapsed().as_secs()}),
            );
        }
        require(
            begin.elapsed() < Duration::from_secs(600) && (!fake || n < 2),
            "cooldown deadline exceeded",
        )?;
        n += 1;
        std::thread::sleep(Duration::from_secs(if fake { 0 } else { 2 }));
    }
}
fn stamp(fake: bool, phase: &str, index: usize, frozen: Option<&Value>) -> (String, String) {
    if !fake {
        return (now(), String::new());
    }
    let base = if phase == "admission" {
        chrono::Utc::now() - chrono::Duration::hours(3)
    } else {
        chrono::DateTime::parse_from_rfc3339(frozen.unwrap()["registered_at"].as_str().unwrap())
            .unwrap()
            .with_timezone(&chrono::Utc)
    };
    let start = base + chrono::Duration::seconds(10 + index as i64 * 1800);
    (
        start.to_rfc3339(),
        (start + chrono::Duration::seconds(1500)).to_rfc3339(),
    )
}
fn sample(
    t: &mut Transport,
    lease: &Lease,
    dir: &Path,
    req: &Request,
    state: &Value,
    phase: &str,
    id: &str,
    pair: Option<&str>,
    role: &str,
    index: usize,
    frozen: Option<&Value>,
) -> Result<Value, String> {
    verify_remote(t, lease, dir, state)?;
    let actual = identity(t, lease, &req.package)?;
    require(
        actual.0 == state["serial"] && actual.1 == state["boot_id"],
        "identity/boot changed before sample",
    )?;
    let policies: Vec<String> =
        serde_json::from_value(state["policies"].clone()).map_err(|e| e.to_string())?;
    let initial = bounded(&dir.join("initial.snapshot"), 2 * 1024 * 1024)?;
    require(
        current_snapshot(t, lease, dir, &policies)? == initial,
        "original state changed before sample",
    )?;
    let fake = req.transport.kind == "fake";
    let (mut started, virtual_finished) = stamp(fake, phase, index, frozen);
    let loaded = req.temperature_profile_id.as_deref() == Some("loaded_steady_v1");
    let cold = if loaded {
        let thermal = temperature::load_frozen(dir)?;
        temperature::loaded_snapshot(t, lease, dir, &thermal, &format!("{id}-pre"))?
    } else {
        cold(t, lease, dir, fake, &started)?
    };
    if !fake {
        started = now();
    }
    journal(
        dir,
        json!({"event":"sample_intent","host_lease_token":lease.token(),"remote_session_token":state["token"],"run_id":id,"role":role,"phase":phase,"serial":actual.0,"boot_id":actual.1,"restore_graph_sha256":sha(&bounded(&dir.join("restore.tsv"),2*1024*1024)?),"at":now()}),
    )?;
    let envdur = req.lead_seconds + req.capture_seconds + 3;
    let out = worker(
        t,
        lease,
        state,
        "sample",
        id,
        &format!(
            "{} {} {} {}",
            req.capture_seconds, req.lead_seconds, envdur, role
        ),
        1300,
    )?;
    require(
        std::str::from_utf8(&out).is_ok_and(|s| s.trim() == "SAMPLE_COMPLETE"),
        "worker completion receipt missing",
    )?;
    require(
        pull(t, lease, state, id, "quiescence.txt")? == b"OWNED_GROUPS_ABSENT\n",
        "owned children not positively quiescent",
    )?;
    let mut raw = json!({});
    for (key, file) in [
        ("trace", "trace.txt"),
        ("env", "sampler.log"),
        ("workload_exit", "workload_exit.txt"),
        ("sampler_exit", "sampler_exit.txt"),
        ("capture_exit", "capture_exit.txt"),
        ("restore_exit", "restore_exit.txt"),
        ("root_id", "root_id.txt"),
        ("boot_id", "boot_id.txt"),
    ] {
        raw[key] = save(
            dir,
            &format!("{id}-{file}"),
            &pull(t, lease, state, id, file)?,
        )?;
    }
    for key in [
        "snapshot_before",
        "snapshot_at_run",
        "snapshot_after_run",
        "snapshot_restored",
    ] {
        require(
            String::from_utf8(pull(t, lease, state, id, &format!("{key}.exit"))?)
                .map_err(|e| e.to_string())?
                .trim()
                == "0",
            "snapshot process failed",
        )?;
        raw[key] = save(
            dir,
            &format!("{id}-{key}.txt"),
            &snap_bytes(pull(t, lease, state, id, &format!("{key}.txt"))?)?,
        )?;
    }
    if loaded {
        raw["thermal_admission"] = file_ref(dir, &format!("{id}-pre-thermal.raw.tsv"))?;
        for (key, file) in [
            ("thermal_window", "thermal_sampler.log"),
            ("thermal_sampler_exit", "thermal_sampler_exit.txt"),
        ] {
            raw[key] = save(
                dir,
                &format!("{id}-{file}"),
                &pull(t, lease, state, id, file)?,
            )?;
        }
    } else {
        raw["cold"] = save_json(dir, &format!("{id}-cold.json"), &cold)?;
    }
    raw["probe"] = file_ref(dir, "probe.txt")?;
    if req.render_attribution_profile_id.is_some() {
        for (key, file) in [
            ("render_before", "render-before.raw.tsv"),
            ("render_after", "render-after.raw.tsv"),
            ("render_final", "render-final.raw.tsv"),
        ] {
            raw[key] = save(
                dir,
                &format!("{id}-{file}"),
                &pull(t, lease, state, id, file)?,
            )?;
        }
    }
    let finished = if fake { virtual_finished } else { now() };
    let artifact = if role == "baseline" {
        "baseline.json"
    } else {
        "candidate.json"
    };
    let r = json!({"schema":if req.render_attribution_profile_id.is_some(){"phonefarm/run_one_bundle@3"}else if loaded{"phonefarm/run_one_bundle@2"}else{"phonefarm/run_one_bundle@1"},"evidence_origin":if fake{"synthetic"}else{"live_device"},"phase":phase,"run_id":id,"pair_id":pair,"sample_role":role,"device_serial":actual.0,"started_at":started,"finished_at":finished,"conditions":file_ref(dir,"conditions.json")?,"artifact":file_ref(dir,artifact)?,"raw":raw});
    let name = format!("{id}-run.json");
    let reference = save_json(dir, &name, &r)?;
    let grouping = if req.render_attribution_profile_id.is_some() && phase == "confirmation" {
        Some(
            serde_json::from_value::<crate::render_attribution::Frozen>(read_json(
                &dir.join("render-contract.json"),
            )?)
            .map_err(|e| e.to_string())?,
        )
    } else {
        None
    };
    let validity = if let Some(f) = grouping.as_ref() {
        observation::validate_raw_run_with_render(&dir.join(&name), Some(f))?
    } else {
        observation::validate_raw_run(&dir.join(&name))?
    };
    save_json(dir, &format!("{id}-validation.json"), &validity)?;
    if fake {
        require(
            validity["gates"]
                .as_object()
                .is_some_and(|g| g.values().all(|v| *v == true))
                && validity["checks"]
                    .as_array()
                    .is_some_and(|cs| cs.iter().all(|c| c["state"] == "pass"))
                && validity["issues"].as_array().is_some_and(|a| {
                    a.len() == 1
                        && a[0]
                            .as_str()
                            .is_some_and(|s| s.starts_with("synthetic/unknown"))
                }),
            "simulation mechanical sample gates failed (never grants device qualification)",
        )?;
    } else {
        require(
            validity["valid"] == true,
            "raw sample failed receiver validation",
        )?;
    }
    require(
        current_snapshot(t, lease, dir, &policies)? == initial,
        "post-pull restoration changed/unknown",
    )?;
    Ok(reference)
}
fn file_ref(dir: &Path, name: &str) -> Result<Value, String> {
    Ok(json!({"path":name,"sha256":sha(&bounded(&dir.join(name),CAP)?)}))
}
fn plan(dir: &Path, req: &Request, admission: &Value, runs: &[Value]) -> Result<Value, String> {
    let mut artifacts = vec![];
    for r in runs {
        let raw = read_json(&dir.join(r["path"].as_str().unwrap()))?;
        for k in ["trace", "env"] {
            artifacts.push(raw["raw"][k]["sha256"].clone());
        }
        if req.render_attribution_profile_id.is_some() {for k in ["render_before","render_after","render_final"] {artifacts.push(raw["raw"][k]["sha256"].clone());}}
        if req.temperature_profile_id.as_deref() == Some("loaded_steady_v1") {
            for k in ["thermal_admission", "thermal_window"] {
                artifacts.push(raw["raw"][k]["sha256"].clone());
            }
        }
    }
    let mut p = json!({"baseline_artifact_sha256":file_ref(dir,"baseline.json")?["sha256"],"candidate_artifact_sha256":file_ref(dir,"candidate.json")?["sha256"],"conditions_sha256":file_ref(dir,"conditions.json")?["sha256"],"baseline_admission_sha256":admission["sha256"],"exploration_run_ids":["admission_0","admission_1","admission_2"],"exploration_artifact_sha256":artifacts,"pairs":req.schedule.len(),"schedule":req.schedule,"family_alpha":0.05,"paired_t_assumptions_acknowledged":true,"metrics":[{"name":"frame_p95_ms","direction":"lower","scale":"percent","minimum_improvement":1.0,"maximum_regression":1.0},{"name":"fps_mean","direction":"higher","scale":"percent","minimum_improvement":null,"maximum_regression":1.0},{"name":"power_watt","direction":"lower","scale":"percent","minimum_improvement":null,"maximum_regression":1.0}]});
    if req.goal == Some(Goal::RenderCadence) {
        p["metrics"].as_array_mut().ok_or("metrics")?.pop();
    }
    if req.temperature_profile_id.as_deref() == Some("loaded_steady_v1") {
        p["temperature_contract"] = temperature_contract(dir)?;
    }
    Ok(p)
}

fn temperature_contract(dir: &Path) -> Result<Value, String> {
    let thermal = temperature::load_frozen(dir)?;
    Ok(
        json!({"profile_id":"loaded_steady_v1","receiver_policy_version":2,
        "profile_sha256":crate::thermal_profile::profile_sha256(&thermal.profile)?,
        "reference_sha256":file_ref(dir,"thermal_reference.json")?["sha256"],
        "thermal_contract_sha256":file_ref(dir,"thermal-evidence.json")?["sha256"]}),
    )
}
fn receiver_policy(dir: &Path, req: &Request) -> Result<Policy, String> {
    if req.temperature_profile_id.as_deref() == Some("loaded_steady_v1") {
        let tc = temperature_contract(dir)?;
        Ok(Policy::new_loaded(
            req.goal.unwrap_or(Goal::RenderEfficiency),
            req.schedule.len() as u32,
            false,
            tc["profile_sha256"].as_str().ok_or("profile hash")?.into(),
            tc["reference_sha256"]
                .as_str()
                .ok_or("reference hash")?
                .into(),
        ))
    } else {
        Ok(Policy::new(
            req.goal.unwrap_or(Goal::RenderEfficiency),
            req.schedule.len() as u32,
            false,
        ))
    }
}
fn prepare(path: &Path, out: &Path, execute: bool) -> Result<Value, String> {
    let (req, requestbytes, workloadbytes, artifactbytes) = request(path)?;
    if req.transport.kind == "adb" && !execute {
        return Ok(json!({"status":"VALIDATED_ONLY","device_executed":false,"eligible":false}));
    }
    fs::create_dir(out).map_err(|e| format!("session must be new: {e}"))?;
    std::fs::File::open(
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
    save(&out, "request.json", &requestbytes)?;
    save(&out, "workload.json", &workloadbytes)?;
    save(&out, "candidate.json", &artifactbytes)?;
    save(
        &out,
        "transport.snapshot",
        &bounded(&req.transport.program, CAP)?,
    )?;
    let snapshot = snapshot_source(req.render_attribution_profile_id.is_some());
    for (name, s) in [
        ("worker.sh", WORKER),
        ("bootstrap.sh", BOOTSTRAP),
        ("snapshot.sh", snapshot.as_str()),
        ("probe_readonly.sh", PROBE),
        ("sample_env.sh", ENV),
        ("cold.sh", COLD),
    ] {
        save(&out, name, s.as_bytes())?;
    }
    save(
        &out,
        "workload.sh",
        workload(&workloadbytes, &req)?.as_bytes(),
    )?;
    if req.render_attribution_profile_id.is_some() {
        render_reader::snapshot(req.render_reader_artifact.as_deref().ok_or("native artifact required")?, &out)?;
        save(
            &out,
            "render_ownership.sh",
            crate::render_attribution::COLLECTOR.as_bytes(),
        )?;
        save(
            &out,
            "render_args.tsv",
            format!("{}\t{}\n", req.package, req.render_comm).as_bytes(),
        )?;
    }
    let mut policy = Policy::new(
        req.goal.unwrap_or(Goal::RenderEfficiency),
        req.schedule.len() as u32,
        false,
    );
    let mut contract = json!({"schema":"phonefarm/heldout_sampling_contract@1","policy":policy,"request_sha256":sha(&requestbytes),"workload_sha256":sha(&workloadbytes),"candidate_sha256":sha(&artifactbytes),"worker_sha256":sha(WORKER.as_bytes()),"bootstrap_sha256":sha(BOOTSTRAP.as_bytes()),"snapshot_sha256":sha(snapshot.as_bytes()),"probe_sha256":sha(PROBE.as_bytes()),"env_sha256":sha(ENV.as_bytes()),"cold_sha256":sha(COLD.as_bytes()),"transport_sha256":file_ref(&out,"transport.snapshot")?["sha256"],"primary_improvement_percent":1.0,"maximum_regression_percent":1.0,"family_alpha":0.05,"clock_scope":if req.transport.kind=="fake"{"synthetic_virtual_timing"}else{"host_utc_and_device_proc_uptime_separate"}});

    let (root, legacy, timeout) = roots()?;
    let mut lease = Lease::acquire(
        &root,
        &legacy,
        &req.transport.target,
        "confirmation-prepare",
        timeout,
    )?;
    let mut t = start_transport(&out, &req, "prepare-commands")?;
    let (serial, boot) = identity(&mut t, &lease, &req.package)?;
    let loaded = req.temperature_profile_id.as_deref() == Some("loaded_steady_v1");
    if loaded {
        // Freeze the actual loaded restore state only after baseline-only warmup.
        temperature::freeze_loaded(&mut t, &lease, &out, &serial, &boot)?;
    }
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
    let policies: Vec<String> = pm
        .get("cpu.policies")
        .ok_or("missing policy inventory")?
        .split(',')
        .map(|n| format!("policy{}", n.trim()))
        .collect();
    let initial = current_snapshot(&mut t, &lease, &out, &policies)?;
    save(&out, "initial.snapshot", &initial)?;
    let parsed = observation::snapshot(
        std::str::from_utf8(&initial).map_err(|e| e.to_string())?,
        &policies,
    )?;
    let a: Value = serde_json::from_slice(&artifactbytes).map_err(|e| e.to_string())?;
    let graph = control::graph(&a, &probe, &parsed)?;
    require(graph.policies == policies, "readonly inventories disagree")?;
    save(&out, "restore.tsv", graph.restore.as_bytes())?;
    save(&out, "candidate.plan.tsv", graph.candidate.as_bytes())?;
    let mut files = vec![
        "worker.sh",
        "bootstrap.sh",
        "snapshot.sh",
        "sample_env.sh",
        "workload.sh",
        "restore.tsv",
        "candidate.plan.tsv",
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
        let value = parsed[&format!("ftrace.{key}")].replace(',', "\n");
        let name = format!("ftrace-{key}");
        save(&out, &name, format!("{value}\n").as_bytes())?;
        files.push(name);
    }
    if req.render_attribution_profile_id.is_some() {
        let raw_clock = parsed
            .get("ftrace.trace_clock")
            .ok_or("clock restore receipt missing")?;
        let selected = raw_clock
            .split_whitespace()
            .filter(|s| s.starts_with('[') && s.ends_with(']'))
            .collect::<Vec<_>>();
        require(selected.len() == 1, "clock restore receipt unknown")?;
        save(
            &out,
            "ftrace-trace_clock",
            format!("{}\n", selected[0].trim_matches(['[', ']'])).as_bytes(),
        )?;
        files.push("ftrace-trace_clock".into());
    }
    let token = format!(
        "session_{}_{}",
        std::process::id(),
        chrono::Utc::now()
            .timestamp_nanos_opt()
            .ok_or("time overflow")?
    );
    let remote = format!("/data/local/tmp/phonefarm-confirmation-{token}");
    let mut state = json!({"schema":"phonefarm/confirmation_driver_state@1","serial":serial,"boot_id":boot,"target":req.transport.target,"token":token,"remote":remote,"policies":policies,"transport_origin":t.origin});
    if req.render_attribution_profile_id.is_some() { state["helper_remote"]=json!(format!("{remote}-reader")); }
    save_json(&out, "state.json", &state)?;
    for (i, (_, plan)) in graph.probes.iter().enumerate() {
        let name = format!("probe_{i}.plan.tsv");
        save(&out, &name, plan.as_bytes())?;
        files.push(name);
    }
    if req.render_attribution_profile_id.is_some() {
        let mut prefiles=files.clone();
        prefiles.extend(["request.json","workload.json","candidate.json","transport.snapshot","probe_readonly.sh","cold.sh","initial.snapshot","state.json","render_ownership.sh","render_args.tsv","render_close.sh"].into_iter().map(String::from));
        prefiles.extend(render_reader::FILES.iter().map(|n|(*n).to_owned()));
        if loaded { prefiles.extend(temperature::frozen_names().iter().map(|n|(*n).to_owned())); }
        let hashes=prefiles.into_iter().map(|n|Ok((n.clone(),file_ref(&out,&n)?["sha256"].clone()))).collect::<Result<BTreeMap<_,_>,String>>()?;
        save_json(&out,"render-preinstall.manifest.json",&json!({"schema":"phonefarm/render_preinstall_inputs@1","hashes":hashes}))?;
        validate_preinstall(&out,&req)?;
        register_preinstall(&mut t,&out)?;
        lease.hold_for_recovery(&out)?;
        let raw=render_initial(&mut t,&lease,&out,&req,&state)?;
        let initial=crate::render_attribution::witness(std::str::from_utf8(&raw).map_err(|e|e.to_string())?,&req.package,&req.render_comm)?;
        require(initial.scope.boot_id==boot && initial.filters_clear,"initial native render identity/filter scope unsupported")?;
        save(&out,"render-initial.raw.tsv",&raw)?;
        close_helper(&mut t,&lease,&out,&state)?;
        contract["render_attribution_profile_id"]=json!(crate::render_attribution::PROFILE);
        contract["render_collector_sha256"]=json!(sha(crate::render_attribution::COLLECTOR.as_bytes()));
        contract["render_reader_build_sha256"]=file_ref(&out,"render_reader.build.json")?["sha256"].clone();
        contract["render_reader_elf_sha256"]=file_ref(&out,"render_reader.elf")?["sha256"].clone();
    }
    if loaded {
        files.push("thermal_sample.sh".into());
        files.push("thermal_sampler.sh".into());
        policy = receiver_policy(&out, &req)?;
        contract["schema"] = json!("phonefarm/heldout_sampling_contract@2");
        contract["policy"] = serde_json::to_value(&policy).map_err(|e| e.to_string())?;
        contract["temperature_contract"] = temperature_contract(&out)?;
    }
    save_json(&out, "sampling-contract.json", &contract)?;
    let mut c = json!({"schema":if req.render_attribution_profile_id.is_some(){"phonefarm/confirmation_conditions@3"}else if loaded{"phonefarm/confirmation_conditions@2"}else{"phonefarm/confirmation_conditions@1"},"device_serial":serial,"device_boot_id":boot,"snapshot_cpu_policies":policies,"render_comm":req.render_comm,"capture_seconds":req.capture_seconds,"frozen_at":if req.transport.kind=="fake"{(chrono::Utc::now()-chrono::Duration::hours(4)).to_rfc3339()}else{now()},"policy":policy,"sampling_contract_sha256":file_ref(&out,"sampling-contract.json")?["sha256"]});
    if loaded {
        c["thermal_contract"] = file_ref(&out, "thermal-evidence.json")?;
    }
    if req.render_attribution_profile_id.is_some() {
        c["render_attribution"]=json!({"profile_id":crate::render_attribution::PROFILE,"package":req.package,"initial_witness":file_ref(&out,"render-initial.raw.tsv")?});
    }
    save_json(&out, "conditions.json", &c)?;
    save_json(
        &out,
        "baseline.json",
        &json!({"kind":"sysparam","payload":{"params":{},"plan":[],"deny_keywords":crate::sysparam::DENY_KEYWORDS}}),
    )?;
    if req.render_attribution_profile_id.is_some() {
        files.extend(
            ["render_ownership.sh", "render_args.tsv", "render_reader.elf"]
                .into_iter()
                .map(String::from),
        );
    }
    freeze_tool_bundle(&out, &files)?;
    let mut frozenfiles = files.clone();
    frozenfiles.extend(fixed_tools::FROZEN_NAMES.map(String::from));
    frozenfiles.extend(
        [
            "request.json",
            "workload.json",
            "candidate.json",
            "transport.snapshot",
            "probe_readonly.sh",
            "cold.sh",
            "sampling-contract.json",
            "initial.snapshot",
            "state.json",
            "conditions.json",
            "baseline.json",
        ]
        .into_iter()
        .map(String::from),
    );
    if req.render_attribution_profile_id.is_some() {
        frozenfiles.push("render-initial.raw.tsv".into());
        frozenfiles.extend(render_frozen_extra().into_iter().map(String::from));
        save_json(&out,"main-install.intent.json",&json!({"schema":"phonefarm/main_install_intent@1","state_sha256":file_ref(&out,"state.json")?["sha256"],"helper_removed_sha256":file_ref(&out,"render-helper-removed.json")?["sha256"]}))?;
    }
    if loaded {
        frozenfiles.extend(temperature::frozen_names().iter().map(|s| (*s).to_owned()));
    }
    frozenfiles.sort();
    frozenfiles.dedup();
    freeze(
        &out,
        &frozenfiles.iter().map(String::as_str).collect::<Vec<_>>(),
    )?;
    // This durable barrier precedes every remote write, including initial tool installation.
    register_inputs(&mut t.input_hashes, &out)?;
    lease.hold_for_recovery(&out)?;
    install(&mut t, &lease, &out, &remote, &token, &boot, &files)?;
    verify_remote(&mut t, &lease, &out, &state)?;
    let mut actualprobe = probe.trim_end().to_owned() + "\n";
    for (i, _) in graph.probes.iter().enumerate() {
        let id = format!("probe_{i}");
        if loaded {
            let frozen = temperature::load_frozen(&out)?;
            temperature::loaded_snapshot(&mut t, &lease, &out, &frozen, &format!("pre-{id}"))?;
        }
        let done = worker(&mut t, &lease, &state, "probe", &id, "", 45)?;
        require(
            String::from_utf8_lossy(&done).trim() == "PROBE_COMPLETE",
            "probe completion missing",
        )?;
        let mut snaps = vec![];
        for name in ["snapshot_before", "snapshot_restored"] {
            require(
                String::from_utf8_lossy(&pull(
                    &mut t,
                    &lease,
                    &state,
                    &id,
                    &format!("{name}.exit"),
                )?)
                .trim()
                    == "0",
                "probe snapshot failed",
            )?;
            let b = snap_bytes(pull(&mut t, &lease, &state, &id, &format!("{name}.txt"))?)?;
            save(&out, &format!("{id}-{name}.txt"), &b)?;
            snaps.push(b);
        }
        require(
            snaps[0] == initial && snaps[1] == initial,
            "controlled probe did not restore entire snapshot",
        )?;
        let receipt = pull(&mut t, &lease, &state, &id, "probe_receipt.txt")?;
        save(&out, &format!("{id}-receipt.txt"), &receipt)?;
        actualprobe += std::str::from_utf8(&receipt).map_err(|e| e.to_string())?;
    }
    control::verify_artifact(&a, &actualprobe)?;
    save(&out, "probe.txt", actualprobe.as_bytes())?;
    let mut runs = vec![];
    for i in 0..3 {
        runs.push(sample(
            &mut t,
            &lease,
            &out,
            &req,
            &state,
            "admission",
            &format!("admission_{i}"),
            None,
            "baseline",
            i,
            None,
        )?);
    }
    let mut admission_value = json!({"schema":"phonefarm/confirmation_baseline_admission@1","conditions":file_ref(&out,"conditions.json")?,"baseline_artifact":file_ref(&out,"baseline.json")?,"runs":runs});
    if req.render_attribution_profile_id.is_some() {
        let initial = bounded(&out.join("render-initial.raw.tsv"), 2 * 1024 * 1024)?;
        let scope = crate::render_attribution::witness(
            std::str::from_utf8(&initial).map_err(|e| e.to_string())?,
            &req.package,
            &req.render_comm,
        )?
        .scope;
        let mut records = vec![];
        for i in 0..3 {
            let v = read_json(&out.join(format!("admission_{i}-validation.json")))?;
            let (observed, record): (
                crate::render_attribution::Scope,
                crate::render_attribution::Baseline,
            ) = serde_json::from_value(v["render_record"].clone()).map_err(|e| e.to_string())?;
            require(observed == scope, "admission render ownership differs")?;
            records.push(record);
        }
        let frozen = crate::render_attribution::freeze(&scope, records)?;
        save_json(
            &out,
            "render-contract.json",
            &serde_json::to_value(frozen).map_err(|e| e.to_string())?,
        )?;
        admission_value["schema"] = json!("phonefarm/confirmation_baseline_admission@2");
        admission_value["render_contract"] = file_ref(&out, "render-contract.json")?;
    }
    let admission = save_json(&out, "admission.json", &admission_value)?;
    if req.render_attribution_profile_id.is_some() {
        observation::validate_render_admission(&out.join("admission.json"))?;
    }
    let p = plan(&out, &req, &admission, &runs)?;
    save_json(&out, "plan.json", &p)?;
    let qualification =
        observation::validate_admission_for_driver(&out.join("admission.json"), &p, &now());
    if req.transport.kind != "fake" {
        let q = qualification?;
        require(
            q["experiment_allowed"] == true,
            "raw baseline admission rejected",
        )?;
        save_json(&out, "admission-validation.json", &q)?;
    } else {
        save_json(
            &out,
            "admission-validation.json",
            &json!({"status":"SIMULATED_BLOCKED","error":qualification.err(),"eligible":false,"evidence_origin":"synthetic"}),
        )?;
    }
    let final_snapshot = current_snapshot(&mut t, &lease, &out, &policies)?;
    require(final_snapshot == initial, "final prepare restoration unknown")?;
    save(&out, "prepare-final-restored.snapshot", &final_snapshot)?;
    lease.complete_recovery("recorded_controls_and_owned_workload_quiescence", &t.origin,
        &[&out.join("prepare-final-restored.snapshot"), &out.join("journal.jsonl"), &out.join("admission.json")])?;
    let mut committed = BTreeMap::new();
    for entry in fs::read_dir(&out).map_err(|e| e.to_string())? {
        let e = entry.map_err(|e| e.to_string())?;
        if e.file_type().map_err(|e| e.to_string())?.is_file() {
            let n = e.file_name().to_string_lossy().into_owned();
            if n != "journal.jsonl" {
                committed.insert(n, sha(&bounded(&e.path(), CAP)?));
            }
        }
    }
    save_json(
        &out,
        "prepared.manifest.json",
        &json!({"schema":"phonefarm/prepared_session@1","hashes":committed}),
    )?;
    Ok(
        json!({"status":if req.transport.kind=="fake"{"SIMULATED_BLOCKED"}else{"PREPARED"},"session":out,"plan":out.join("plan.json"),"eligible":false,"evidence_origin":t.origin,"real_game_optimization_proven":false,"promotion_allowed":false}),
    )
}
fn validate_contract(dir: &Path, req: &Request, admission_required: bool) -> Result<(), String> {
    let snapshot = snapshot_source(req.render_attribution_profile_id.is_some());
    let c = read_json(&dir.join("sampling-contract.json"))?;
    require(
        c["policy"]
            == serde_json::to_value(receiver_policy(dir, req)?).map_err(|e| e.to_string())?
            && c["primary_improvement_percent"] == 1.0
            && c["maximum_regression_percent"] == 1.0
            && c["family_alpha"] == 0.05,
        "receiver-owned thresholds changed",
    )?;
    for (key, name, expected) in [
        ("worker_sha256", "worker.sh", WORKER),
        ("bootstrap_sha256", "bootstrap.sh", BOOTSTRAP),
        ("snapshot_sha256", "snapshot.sh", snapshot.as_str()),
        ("probe_sha256", "probe_readonly.sh", PROBE),
        ("env_sha256", "sample_env.sh", ENV),
        ("cold_sha256", "cold.sh", COLD),
    ] {
        let b = bounded(&dir.join(name), CAP)?;
        require(
            b == expected.as_bytes() && c[key] == sha(&b),
            "receiver tool bytes/version changed",
        )?;
    }
    for (key, name) in [
        ("request_sha256", "request.json"),
        ("workload_sha256", "workload.json"),
        ("candidate_sha256", "candidate.json"),
        ("transport_sha256", "transport.snapshot"),
    ] {
        require(
            c[key] == file_ref(dir, name)?["sha256"],
            "contract input hash differs",
        )?;
    }
    require(
        bounded(&dir.join("workload.sh"), CAP)?
            == workload(&bounded(&dir.join("workload.json"), 2 * 1024 * 1024)?, req)?.as_bytes(),
        "workload executable differs from frozen source",
    )?;
    let conditions = read_json(&dir.join("conditions.json"))?;
    if req.temperature_profile_id.as_deref() == Some("loaded_steady_v1") {
        require(
            c["schema"] == "phonefarm/heldout_sampling_contract@2"
                && c["temperature_contract"] == temperature_contract(dir)?
                && conditions["schema"]
                    == (if req.render_attribution_profile_id.is_some() {
                        "phonefarm/confirmation_conditions@3"
                    } else {
                        "phonefarm/confirmation_conditions@2"
                    })
                && conditions["thermal_contract"] == file_ref(dir, "thermal-evidence.json")?,
            "loaded receiver contract mismatch",
        )?;
    } else {
        require(
            c["schema"] == "phonefarm/heldout_sampling_contract@1"
                && conditions["schema"]
                    == (if req.render_attribution_profile_id.is_some() {
                        "phonefarm/confirmation_conditions@3"
                    } else {
                        "phonefarm/confirmation_conditions@1"
                    }),
            "legacy receiver contract changed",
        )?;
    }
    if req.render_attribution_profile_id.is_some() {
        if admission_required {observation::validate_render_admission(&dir.join("admission.json"))?;}
        render_reader::verify(dir)?;
        validate_helper_removed(dir)?;
        let raw = bounded(&dir.join("render-initial.raw.tsv"), 2 * 1024 * 1024)?;
        let initial = crate::render_attribution::witness(
            std::str::from_utf8(&raw).map_err(|e| e.to_string())?,
            &req.package,
            &req.render_comm,
        )?;
        require(
            bounded(&dir.join("ftrace-trace_clock"), CAP)?
                == format!("{}\n", initial.selected_clock).as_bytes(),
            "clock restore input differs from initial witness",
        )?;
        require(
            c["render_attribution_profile_id"] == crate::render_attribution::PROFILE
                && c["render_collector_sha256"]
                    == sha(crate::render_attribution::COLLECTOR.as_bytes())
                && c["render_reader_build_sha256"]==file_ref(dir,"render_reader.build.json")?["sha256"]
                && c["render_reader_elf_sha256"]==file_ref(dir,"render_reader.elf")?["sha256"]
                && bounded(&dir.join("render_ownership.sh"), CAP)?
                    == crate::render_attribution::COLLECTOR.as_bytes()
                && bounded(&dir.join("render_args.tsv"), CAP)?
                    == format!("{}\t{}\n", req.package, req.render_comm).as_bytes()
                && conditions["render_attribution"]
                    == json!({"profile_id":crate::render_attribution::PROFILE,"package":req.package,"initial_witness":file_ref(dir,"render-initial.raw.tsv")?}),
            "receiver ownership contract changed",
        )?;
    } else {
        require(
            c.get("render_attribution_profile_id").is_none()
                && conditions.get("render_attribution").is_none(),
            "legacy falsely adds ownership",
        )?;
    }
    require(
        conditions["sampling_contract_sha256"]
            == file_ref(dir, "sampling-contract.json")?["sha256"]
            && conditions["policy"] == c["policy"],
        "conditions do not bind receiver contract",
    )
}
fn prepared(dir: &Path) -> Result<(Request, Value), String> {
    verify(dir)?;
    let manifest = read_json(&dir.join("prepared.manifest.json"))?;
    require(
        manifest["schema"] == "phonefarm/prepared_session@1",
        "prepare incomplete",
    )?;
    require(
        manifest["hashes"]
            .as_object()
            .ok_or("missing prepared hashes")?
            .keys()
            .cloned()
            .collect::<BTreeSet<_>>()
            == prepared_names(dir)?,
        "incomplete/extra prepared evidence manifest",
    )?;
    for (name, h) in manifest["hashes"]
        .as_object()
        .ok_or("missing prepared hashes")?
    {
        require(
            safe(name) && h.as_str() == Some(&sha(&bounded(&dir.join(name), CAP)?)),
            "prepared admission/contract bytes changed",
        )?;
    }
    let req: Request =
        serde_json::from_slice(&bounded(&dir.join("request.json"), 2 * 1024 * 1024)?)
            .map_err(|e| e.to_string())?;
    validate_contract(dir, &req, true)?;
    Ok((req, read_json(&dir.join("state.json"))?))
}
fn collect(dir: &Path, frozen_path: &Path, execute: bool) -> Result<Value, String> {
    let dir = dir.canonicalize().map_err(|e| e.to_string())?;
    require(
        read_json(&dir.join("request.json"))?["temperature_profile_id"] != "capability_absolute_v1",
        "control-only absolute capability session cannot collect performance observations",
    )?;
    let (req, state) = prepared(&dir)?;
    let p = read_json(&dir.join("plan.json"))?;
    let admission = read_json(&dir.join("admission.json"))?;
    require(
        p == plan(
            &dir,
            &req,
            &file_ref(&dir, "admission.json")?,
            admission["runs"]
                .as_array()
                .ok_or("missing admission runs")?,
        )?,
        "registered plan altered receiver-owned metrics or bindings",
    )?;
    let f = observation::validate_registration_for_driver(
        frozen_path,
        &dir.join("conditions.json"),
        &p,
    )?;
    if req.transport.kind == "adb" && !execute {
        return Ok(json!({"status":"VALIDATED_ONLY","device_executed":false,"eligible":false}));
    }
    require(
        !dir.join("collection.started.json").exists(),
        "heldout collection already started; never automatic replay",
    )?;
    let (root, legacy, timeout) = roots()?;
    let mut lease = Lease::acquire(
        &root,
        &legacy,
        &req.transport.target,
        "confirmation-collect",
        timeout,
    )?;
    let mut t = start_transport(&dir, &req, "collect-commands")?;
    let actual = identity(&mut t, &lease, &req.package)?;
    require(
        actual.0 == state["serial"] && actual.1 == state["boot_id"],
        "serial/boot changed across registration gap",
    )?;
    let policies: Vec<String> =
        serde_json::from_value(state["policies"].clone()).map_err(|e| e.to_string())?;
    require(
        current_snapshot(&mut t, &lease, &dir, &policies)?
            == bounded(&dir.join("initial.snapshot"), 2 * 1024 * 1024)?,
        "original state changed across registration gap",
    )?;
    let q = observation::validate_admission_for_driver(
        &dir.join("admission.json"),
        &p,
        f["registered_at"].as_str().ok_or("registration time")?,
    );
    if req.transport.kind != "fake" {
        require(
            q?["experiment_allowed"] == true,
            "admission not valid under registered plan",
        )?;
    }
    // Receiver checked the exact plan before holding recovery and starting collection.
    save_json(&dir, "registered.json", &f)?;
    save_json(
        &dir,
        "collection.started.json",
        &json!({"at":now(),"frozen_plan_sha256":f["sha256"],"source":t.origin}),
    )?;
    lease.hold_for_recovery(&dir)?;
    let mut reports = BTreeMap::new();
    let mut index = 0;
    for pair in &req.schedule {
        maybe_pause(&dir)?;
        let roles = if pair.baseline_first {
            [(&pair.a_run_id, "baseline"), (&pair.b_run_id, "candidate")]
        } else {
            [(&pair.b_run_id, "candidate"), (&pair.a_run_id, "baseline")]
        };
        for (id, role) in roles {
            maybe_pause(&dir)?;
            let r = sample(
                &mut t,
                &lease,
                &dir,
                &req,
                &state,
                "confirmation",
                id,
                Some(&pair.pair_id),
                role,
                index,
                Some(&f),
            )?;
            index += 1;
            let name = format!("{id}-bundle.json");
            save_json(
                &dir,
                &name,
                &json!({"schema":"phonefarm/confirmation_bundle@1","run":r,"baseline_admission":file_ref(&dir,"admission.json")?,"frozen_plan":file_ref(&dir,"registered.json")?}),
            )?;
            let report = format!("{id}-report.json");
            let value = observation::report_for_driver(&dir.join(&name));
            save_json(&dir, &report, &value)?;
            if req.transport.kind != "fake" {
                require(
                    value["status"] == "VALID_OBSERVATION",
                    "real observation reconstruction rejected raw confirmation",
                )?;
            }
            reports.insert(id.clone(), file_ref(&dir, &report)?);
        }
    }
    let pairs=req.schedule.iter().map(|p|json!({"pair_id":p.pair_id,"a_run_id":p.a_run_id,"b_run_id":p.b_run_id,"a_report":reports[&p.a_run_id]["path"],"b_report":reports[&p.b_run_id]["path"],"a_report_sha256":reports[&p.a_run_id]["sha256"],"b_report_sha256":reports[&p.b_run_id]["sha256"]})).collect::<Vec<_>>();
    let samples = json!({"frozen_plan_sha256":f["sha256"],"candidate_artifact_sha256":p["candidate_artifact_sha256"],"conditions_sha256":p["conditions_sha256"],"source_kind":if req.transport.kind=="fake"{"synthetic_fixture"}else{"recorded_receiver_reports"},"pairs":pairs});
    save_json(&dir, "samples.json", &samples)?;
    let final_snapshot = current_snapshot(&mut t, &lease, &dir, &policies)?;
    require(final_snapshot == bounded(&dir.join("initial.snapshot"), 2 * 1024 * 1024)?,
        "final collect restoration unknown")?;
    save(&dir, "collect-final-restored.snapshot", &final_snapshot)?;
    lease.complete_recovery("recorded_controls_and_owned_workload_quiescence", &t.origin,
        &[&dir.join("collect-final-restored.snapshot"), &dir.join("journal.jsonl"), &dir.join("samples.json")])?;
    Ok(
        json!({"status":if req.transport.kind=="fake"{"SIMULATED_BLOCKED"}else{"COLLECTED_OBSERVATIONS"},"samples":dir.join("samples.json"),"eligible":false,"evidence_origin":t.origin,"real_game_optimization_proven":false,"promotion_allowed":false}),
    )
}
fn reconcile(dir: &Path, execute: bool) -> Result<Value, String> {
    require(execute, "explicit reconcile requires --execute")?;
    if read_json(&dir.join("request.json"))?["schema"] == "phonefarm/control_probe_request@1" {
        return control_probe::reconcile(dir);
    }
    let dir = dir.canonicalize().map_err(|e| e.to_string())?;
    if dir.join("render-preinstall.manifest.json").exists() && !dir.join("main-install.intent.json").exists() && !dir.join("inputs.manifest.json").exists() { return reconcile_helper(&dir); }
    verify(&dir)?;
    let req: Request =
        serde_json::from_slice(&bounded(&dir.join("request.json"), 2 * 1024 * 1024)?)
            .map_err(|e| e.to_string())?;
    validate_contract(&dir, &req, false)?;
    let state = read_json(&dir.join("state.json"))?;
    let (root, legacy, timeout) = roots()?;
    let mut lease = Lease::acquire_recovery(&root, &legacy, &req.transport.target, &dir, timeout)?;
    let run = format!(
        "reconcile_{}",
        chrono::Utc::now()
            .timestamp_nanos_opt()
            .ok_or("time overflow")?
    );
    let mut t = start_transport(&dir, &req, &run)?;
    let actual = identity(&mut t, &lease, "")?;
    require(
        actual.0 == state["serial"] && actual.1 == state["boot_id"],
        "reconcile refuses changed actual serial/boot",
    )?;
    verify_remote(&mut t, &lease, &dir, &state)?;
    let out = worker(&mut t, &lease, &state, "reconcile", &run, "", 45)?;
    require(
        String::from_utf8_lossy(&out).trim() == "RECONCILE_COMPLETE",
        "missing reconciliation completion",
    )?;
    require(
        pull(&mut t, &lease, &state, &run, "session_closed.txt")?
            == format!(
                "SESSION_REVOKED {} {} {run}\n",
                state["token"].as_str().ok_or("missing token")?,
                state["boot_id"].as_str().ok_or("missing boot")?
            )
            .as_bytes(),
        "reconciliation session revocation unknown",
    )?;
    require(
        pull(&mut t, &lease, &state, &run, "quiescence.txt")? == b"OWNED_GROUPS_ABSENT\n"
            && String::from_utf8_lossy(&pull(&mut t, &lease, &state, &run, "restore_exit.txt")?)
                .trim()
                == "0"
            && String::from_utf8_lossy(&pull(
                &mut t,
                &lease,
                &state,
                &run,
                "snapshot_restored.exit",
            )?)
            .trim()
                == "0",
        "reconciliation idle/restore/snapshot process receipt failed",
    )?;
    let snapshot = snap_bytes(pull(&mut t, &lease, &state, &run, "snapshot_restored.txt")?)?;
    require(
        snapshot == bounded(&dir.join("initial.snapshot"), 2 * 1024 * 1024)?,
        "reconciliation full snapshot differs",
    )?;
    save(&dir, &format!("{run}-restored.txt"), &snapshot)?;
    journal(
        &dir,
        json!({"event":"reconciliation_verified","at":now(),"actual_serial":actual.0,"boot_id":actual.1,"restore_sha256":sha(&snapshot),"remote_session_revoked":true,"old_samples_revalidated":false,"source":t.origin}),
    )?;
    lease.complete_recovery("recorded_controls_and_revoked_session_quiescence", &t.origin,
        &[&dir.join(format!("{run}-restored.txt")), &dir.join("journal.jsonl")])?;
    Ok(
        json!({"status":if req.transport.kind=="fake"{"SIMULATED_RECONCILED"}else{"RESTORED_REQUIRES_NEW_SESSION"},"old_samples_revalidated":false,"eligible":false,"real_game_optimization_proven":false,"promotion_allowed":false}),
    )
}
pub fn run_confirmation_driver(args: &[String]) -> i32 {
    let mut mode = None;
    let mut req = None;
    let mut out = None;
    let mut session = None;
    let mut frozen = None;
    let mut execute = false;
    let mut err = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "prepare" | "collect" | "reconcile" | "probe-controls" | "diagnose-helper" | "diagnose-helper-native" | "retire-helper-native" if mode.is_none() => {
                mode = Some(args[i].clone())
            }
            "--execute" => execute = true,
            "--request" | "--out" | "--session" | "--frozen" if i + 1 < args.len() => {
                let value = PathBuf::from(&args[i + 1]);
                match args[i].as_str() {
                    "--request" => req = Some(value),
                    "--out" => out = Some(value),
                    "--session" => session = Some(value),
                    _ => frozen = Some(value),
                }
                i += 1;
            }
            _ => err = Some(format!("unknown/incomplete driver argument {}", args[i])),
        }
        i += 1;
    }
    let result = if let Some(e) = err {
        Err(e)
    } else {
        match mode.as_deref(){Some("diagnose-helper-native" | "retire-helper-native") if session.is_some()&&req.is_none()&&out.is_none()&&frozen.is_none()=>helper_native::run(session.as_deref().unwrap(),execute,mode.as_deref()==Some("retire-helper-native")),Some("diagnose-helper") if session.is_some()&&req.is_none()&&out.is_none()&&frozen.is_none()=>helper_diagnostics::diagnose(session.as_deref().unwrap(),execute),Some("probe-controls") if req.is_some()&&out.is_some()&&session.is_none()&&frozen.is_none()=>control_probe::run(req.as_deref().unwrap(),out.as_deref().unwrap(),execute),Some("prepare") if req.is_some()&&out.is_some()&&session.is_none()&&frozen.is_none()=>prepare(req.as_deref().unwrap(),out.as_deref().unwrap(),execute),Some("collect") if session.is_some()&&frozen.is_some()&&req.is_none()&&out.is_none()=>collect(session.as_deref().unwrap(),frozen.as_deref().unwrap(),execute),Some("reconcile") if session.is_some()&&req.is_none()&&out.is_none()&&frozen.is_none()=>reconcile(session.as_deref().unwrap(),execute),_=>Err("use prepare --request R --out NEW | collect --session S --frozen F | reconcile --session S --execute | diagnose-helper --session S --execute".into())}
    };
    match result {
        Ok(v) => {
            println!("{}", v);
            0
        }
        Err(e) => {
            println!(
                "{}",
                json!({"status":"BLOCKED","error":e,"eligible":false,"real_game_optimization_proven":false,"promotion_allowed":false,"recovery":"device writes may have begun; no automatic retry, inspect persistent barrier and explicit reconcile"})
            );
            1
        }
    }
}


#[cfg(test)]
mod safe_pause_tests {
    use super::*;

    /// 安全点暂停：标志存在时阻塞，移除后恢复；两条边都留文件回执。
    #[test]
    fn pause_flag_blocks_and_resume_continues() {
        let dir = std::env::temp_dir().join(format!("pf-safe-pause-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        assert!(maybe_pause(&dir).is_ok());
        assert!(!dir.join("paused.json").exists());
        std::fs::write(dir.join("pause.requested"), b"test").unwrap();
        let d2 = dir.clone();
        let handle = std::thread::spawn(move || {
            maybe_pause_with_poll(&d2, Duration::from_millis(5))
        });
        let deadline = Instant::now() + Duration::from_secs(2);
        let paused = loop {
            if dir.join("paused.json").exists() {
                break true;
            }
            if handle.is_finished() || Instant::now() >= deadline {
                break false;
            }
            std::thread::sleep(Duration::from_millis(5));
        };
        std::fs::remove_file(dir.join("pause.requested")).unwrap();
        handle.join().unwrap().unwrap();
        assert!(paused, "pause did not publish paused.json");
        assert!(dir.join("paused.json").exists());
        assert!(dir.join("resumed.json").exists());
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
