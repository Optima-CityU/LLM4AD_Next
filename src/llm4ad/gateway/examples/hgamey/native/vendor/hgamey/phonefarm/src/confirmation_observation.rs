//! Read-only reconstruction of one confirmation observation. Never an optimization verdict.
use crate::{autoloop, eval, looptrace, pyjson, qualification, sysparam};
use chrono::{DateTime, FixedOffset};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};

const JSON_LIMIT: u64 = 2 * 1024 * 1024;
const RAW_LIMIT: u64 = 128 * 1024 * 1024;
const TOTAL_LIMIT: u64 = 512 * 1024 * 1024;
fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn digest_ok(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}
fn require(ok: bool, reason: &str) -> Result<(), String> {
    if ok {
        Ok(())
    } else {
        Err(reason.into())
    }
}
fn time(s: &str) -> Result<DateTime<FixedOffset>, String> {
    DateTime::parse_from_rfc3339(s).map_err(|e| format!("invalid driver timestamp: {e}"))
}
fn regular_bytes(path: &Path, limit: u64) -> Result<Vec<u8>, String> {
    let before = std::fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    require(
        before.is_file() && !before.file_type().is_symlink() && before.len() <= limit,
        "evidence must be a bounded regular file",
    )?;
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // A path can be replaced between stat and open. FIFO must not block, symlink must not be followed.
        options.custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW);
    }
    let f = options.open(path).map_err(|e| e.to_string())?;
    let opened = f.metadata().map_err(|e| e.to_string())?;
    require(
        opened.is_file() && opened.len() <= limit,
        "opened evidence is not a bounded regular file",
    )?;
    let mut bytes = Vec::new();
    f.take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    require(
        bytes.len() as u64 <= limit,
        "evidence size limit exceeded during read",
    )?;
    Ok(bytes)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct FileRef {
    path: String,
    sha256: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Bundle {
    schema: String,
    run: FileRef,
    baseline_admission: FileRef,
    frozen_plan: FileRef,
}
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct RenderRegistration {
    profile_id: String,
    package: String,
    initial_witness: FileRef,
}
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct Conditions {
    schema: String,
    device_serial: String,
    device_boot_id: String,
    snapshot_cpu_policies: Vec<String>,
    render_comm: String,
    capture_seconds: u64,
    frozen_at: String,
    policy: qualification::Policy,
    #[serde(default)]
    sampling_contract_sha256: Option<String>,
    #[serde(default)]
    thermal_contract: Option<FileRef>,
    #[serde(default)]
    render_attribution: Option<RenderRegistration>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Artifact {
    kind: String,
    payload: SysparamPayload,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SysparamPayload {
    #[serde(default)]
    why: Option<String>,
    params: BTreeMap<String, String>,
    plan: Vec<PlanEntry>,
    #[serde(default)]
    whitelist: Option<Value>,
    deny_keywords: Vec<String>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PlanEntry {
    kind: String,
    path: String,
    value: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Raw {
    trace: FileRef,
    env: FileRef,
    #[serde(default)]
    render_before: Option<FileRef>,
    #[serde(default)]
    render_after: Option<FileRef>,
    #[serde(default)]
    render_final: Option<FileRef>,
    workload_exit: FileRef,
    sampler_exit: FileRef,
    capture_exit: FileRef,
    restore_exit: FileRef,
    root_id: FileRef,
    boot_id: FileRef,
    #[serde(default)]
    cold: Option<FileRef>,
    #[serde(default)]
    thermal_admission: Option<FileRef>,
    #[serde(default)]
    thermal_window: Option<FileRef>,
    #[serde(default)]
    thermal_sampler_exit: Option<FileRef>,
    probe: FileRef,
    snapshot_before: FileRef,
    snapshot_at_run: FileRef,
    snapshot_after_run: FileRef,
    snapshot_restored: FileRef,
}
fn unique_raw_artifacts(raw: &Raw) -> Vec<&String> {
    let mut hashes = vec![&raw.trace.sha256, &raw.env.sha256];
    if let Some(r) = &raw.thermal_admission {
        hashes.push(&r.sha256);
    }
    if let Some(r) = &raw.thermal_window {
        hashes.push(&r.sha256);
    }
    for r in [&raw.render_before, &raw.render_after, &raw.render_final].into_iter().flatten() { hashes.push(&r.sha256); }
    // Plain exit-code files such as "0\n" legitimately have the same bytes across arms.
    hashes
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Run {
    schema: String,
    evidence_origin: String,
    phase: String,
    run_id: String,
    pair_id: Option<String>,
    sample_role: String,
    device_serial: String,
    started_at: String,
    finished_at: String,
    conditions: FileRef,
    artifact: FileRef,
    raw: Raw,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Cold {
    checked_at: String,
    zone: String,
    c: f64,
    waited_s: u64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Admission {
    schema: String,
    conditions: FileRef,
    baseline_artifact: FileRef,
    runs: Vec<FileRef>,
    #[serde(default)]
    render_contract: Option<FileRef>,
}

// These field orders mirror GOL PR29's serde tuple fingerprint. Value maps must not reorder Plan.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Frozen {
    schema: String,
    registered_at: String,
    plan: Plan,
    sha256: String,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Plan {
    baseline_artifact_sha256: String,
    candidate_artifact_sha256: String,
    conditions_sha256: String,
    baseline_admission_sha256: String,
    exploration_run_ids: Vec<String>,
    exploration_artifact_sha256: Vec<String>,
    pairs: usize,
    schedule: Vec<PlannedPair>,
    family_alpha: f64,
    paired_t_assumptions_acknowledged: bool,
    metrics: Vec<MetricRule>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    temperature_contract: Option<TemperatureContract>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct TemperatureContract {
    profile_id: String,
    receiver_policy_version: u32,
    profile_sha256: String,
    reference_sha256: String,
    thermal_contract_sha256: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FrozenThermalEvidence {
    schema: String,
    actual_serial: String,
    boot_id: String,
    rules_frozen_at: String,
    warmup_started_at: String,
    warmup_finished_at: String,
    metadata_raw: FileRef,
    hal_raw: FileRef,
    warmup_raw: FileRef,
    profile: FileRef,
    reference: FileRef,
    profile_sha256: String,
    reference_sha256: String,
    clock_scope: String,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PlannedPair {
    pair_id: String,
    a_run_id: String,
    b_run_id: String,
    baseline_first: bool,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct MetricRule {
    name: String,
    direction: String,
    scale: String,
    minimum_improvement: Option<f64>,
    maximum_regression: f64,
}

/// Own the bytes once: hashing and parsing use the same read, including repeated file refs.
#[derive(Default)]
struct Reader {
    bytes: BTreeMap<PathBuf, Vec<u8>>,
    hashes: BTreeMap<String, String>,
}
impl Reader {
    fn path(base: &Path, r: &FileRef) -> Result<PathBuf, String> {
        require(
            digest_ok(&r.sha256),
            "file reference requires lowercase SHA-256",
        )?;
        let p = Path::new(&r.path);
        require(
            !r.path.is_empty() && p.components().all(|c| matches!(c, Component::Normal(_))),
            "file reference must be a relative path without traversal",
        )?;
        let mut result = base.to_path_buf();
        for c in p.components() {
            result.push(c);
            let m = std::fs::symlink_metadata(&result)
                .map_err(|e| format!("{}: {e}", result.display()))?;
            require(!m.file_type().is_symlink(), "symlink evidence rejected")?;
        }
        Ok(result)
    }
    fn read(&mut self, base: &Path, r: &FileRef, limit: u64) -> Result<(PathBuf, Vec<u8>), String> {
        let p = Self::path(base, r)?;
        if !self.bytes.contains_key(&p) {
            let m = std::fs::symlink_metadata(&p).map_err(|e| e.to_string())?;
            require(
                m.is_file() && m.len() <= limit,
                "evidence must be a bounded regular file",
            )?;
            require(
                self.bytes.values().map(|b| b.len() as u64).sum::<u64>() + m.len() <= TOTAL_LIMIT,
                "evidence bundle total byte limit exceeded",
            )?;
            let bytes = regular_bytes(&p, limit)?;
            require(bytes.len() as u64 <= limit, "evidence size limit exceeded")?;
            require(
                self.bytes.values().map(|b| b.len() as u64).sum::<u64>() + bytes.len() as u64
                    <= TOTAL_LIMIT,
                "evidence bundle total byte limit exceeded",
            )?;
            self.bytes.insert(p.clone(), bytes);
        }
        let bytes = self.bytes.get(&p).unwrap();
        require(
            bytes.len() as u64 <= limit && hash(bytes) == r.sha256,
            &format!("changed evidence bytes: {}", p.display()),
        )?;
        self.hashes
            .insert(p.display().to_string(), r.sha256.clone());
        Ok((p, bytes.clone()))
    }
    fn json<T: DeserializeOwned>(
        &mut self,
        base: &Path,
        r: &FileRef,
    ) -> Result<(PathBuf, T), String> {
        let (p, bytes) = self.read(base, r, JSON_LIMIT)?;
        let v = serde_json::from_slice(&bytes).map_err(|e| format!("{}: {e}", p.display()))?;
        Ok((p, v))
    }
    fn text(&mut self, base: &Path, r: &FileRef) -> Result<String, String> {
        let (_, b) = self.read(base, r, RAW_LIMIT)?;
        String::from_utf8(b).map_err(|e| e.to_string())
    }
}

fn conditions_valid(c: &Conditions) -> Result<(), String> {
    if let Some(r) = &c.render_attribution {
        require(
            c.schema == "phonefarm/confirmation_conditions@3"
                && r.profile_id == crate::render_attribution::PROFILE
                && !r.package.is_empty()
                && c.policy.goal == qualification::Goal::RenderCadence,
            "invalid target ownership conditions",
        )?;
    }
    require(
        c.sampling_contract_sha256
            .as_ref()
            .is_none_or(|s| digest_ok(s)),
        "invalid sampling contract fingerprint",
    )?;
    let attributed = c.render_attribution.is_some();
    let schema_ok = if attributed {
        c.schema == "phonefarm/confirmation_conditions@3"
    } else {
        (c.policy.version == 1 && c.schema == "phonefarm/confirmation_conditions@1")
            || (c.policy.version == 2 && c.schema == "phonefarm/confirmation_conditions@2")
    };
    require(
        schema_ok
            && ((c.policy.version == 1 && c.thermal_contract.is_none())
                || (c.policy.version == 2
                    && c.thermal_contract
                        .as_ref()
                        .is_some_and(|r| digest_ok(&r.sha256)))),
        "unknown conditions schema",
    )?;
    require(
        !c.device_serial.trim().is_empty()
            && !c.device_boot_id.trim().is_empty()
            && !c.render_comm.trim().is_empty()
            && (2..=600).contains(&c.capture_seconds),
        "known device/boot/render thread and capture duration 2..600 required",
    )?;
    time(&c.frozen_at)?;
    let policies: BTreeSet<_> = c.snapshot_cpu_policies.iter().collect();
    require(
        !policies.is_empty()
            && policies.len() == c.snapshot_cpu_policies.len()
            && policies.len() <= 32
            && policies.iter().all(|s| {
                s.strip_prefix("policy")
                    .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
            }),
        "freeze a known unique CPU policy coverage list",
    )?;
    c.policy.validate()?;
    require(
        c.policy.version == 2 || (c.policy.cool_c.is_some() && c.policy.temp_cap_c.is_some()),
        "temperature conditions unknown",
    )?;
    require(
        !c.policy.require_quality,
        "v1 supports sysparam submit-proxy measurements, not display or quality-changing routes",
    )
}
const SNAPSHOT_FIXED_KEYS: &[&str] = &[
    "ftrace.tracing_on",
    "ftrace.current_tracer",
    "ftrace.buffer_size_kb",
    "ftrace.set_event",
    "kgsl.min_pwrlevel",
    "kgsl.max_pwrlevel",
    "kgsl.default_pwrlevel",
    "kgsl.thermal_pwrlevel",
    "kgsl.force_clk_on",
    "kgsl.force_rail_on",
    "kgsl.force_bus_on",
    "kgsl.force_no_nap",
    "kgsl.idle_timer",
    "kgsl.max_gpuclk",
    "kgsl.pwrscale",
    "kgsl.bus_split",
    "kgsl.perfcounter",
    "kgsl.devfreq_governor",
    "kgsl.devfreq_min_freq",
    "kgsl.devfreq_max_freq",
    "bus.DDR.boost_freq",
    "bus.DDR.hw_max_freq",
    "bus.DDR.hw_min_freq",
    "bus.LLCC.boost_freq",
    "bus.LLCC.hw_max_freq",
    "bus.LLCC.hw_min_freq",
    "wm.size",
    "wm.density",
    "settings.peak_refresh_rate",
    "settings.min_refresh_rate",
    "settings.refresh_rate_mode",
];
pub(crate) fn snapshot(s: &str, policies: &[String]) -> Result<BTreeMap<String, String>, String> {
    require(
        eval::adb_ok(s)
            && s.starts_with("# device_snapshot v1\n")
            && s.lines().any(|l| l == "# end"),
        "snapshot missing completion/ADB receipt",
    )?;
    let mut result: BTreeMap<String, String> = BTreeMap::new();
    let mut ended = false;
    for l in s.lines().skip(1) {
        if l == "# end" {
            require(!ended, "duplicate snapshot end")?;
            ended = true;
            continue;
        }
        if l == "#adb_exit=0" && ended {
            continue;
        }
        require(!ended, "snapshot data after end")?;
        let (k, v) = l.split_once('=').ok_or("malformed snapshot row")?;
        require(
            !k.is_empty() && result.insert(k.into(), v.into()).is_none(),
            "duplicate/empty snapshot key",
        )?;
    }
    for k in [
        "ftrace.tracing_on",
        "ftrace.current_tracer",
        "ftrace.buffer_size_kb",
        "ftrace.set_event",
    ] {
        require(
            result
                .get(k)
                .is_some_and(|v| (k == "ftrace.set_event" || !v.is_empty()) && v != "<unreadable>"),
            "snapshot missing readable tracing restoration domain",
        )?;
    }
    require(
        SNAPSHOT_FIXED_KEYS
            .iter()
            .all(|key| result.contains_key(*key)),
        "snapshot missing fixed device_snapshot v1 domain",
    )?;
    for policy in policies {
        for suffix in ["governor", "scaling_min", "scaling_max"] {
            require(
                result.contains_key(&format!("cpu.{policy}.{suffix}")),
                "snapshot missing declared CPU policy domain",
            )?;
        }
    }
    require(
        matches!(result["ftrace.tracing_on"].as_str(), "0" | "1")
            && result["ftrace.buffer_size_kb"]
                .parse::<u64>()
                .is_ok_and(|n| n > 0),
        "invalid tracing state receipt",
    )?;
    Ok(result)
}
fn control(
    a: &Artifact,
    probe: &str,
    before: &str,
    active: &str,
    after: &str,
    c: &Conditions,
) -> Result<(), String> {
    require(a.kind == "sysparam", "unsupported artifact kind")?;
    let probe_map = sysparam::parse_probe(probe);
    let recorded_policies: BTreeSet<_> = probe_map
        .get("cpu.policies")
        .unwrap_or("")
        .split(',')
        .filter(|s| !s.is_empty())
        .map(|s| format!("policy{s}"))
        .collect();
    require(
        recorded_policies == c.snapshot_cpu_policies.iter().cloned().collect(),
        "probe CPU policy inventory differs from frozen snapshot coverage",
    )?;
    let b = snapshot(before, &c.snapshot_cpu_policies)?;
    let during = snapshot(active, &c.snapshot_cpu_policies)?;
    let end = snapshot(after, &c.snapshot_cpu_policies)?;
    require(
        during == end && b.keys().eq(during.keys()),
        "control snapshot changed/missing between sample boundaries",
    )?;
    let cand: Vec<_> = a
        .payload
        .params
        .iter()
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    if cand.is_empty() {
        return require(
            a.payload.plan.is_empty() && b == during,
            "baseline must not apply hidden controls",
        );
    }
    let wl = sysparam::build_whitelist(&sysparam::parse_probe(probe));
    let (ok, why) = sysparam::validate_candidate(&cand, &wl);
    require(ok, &why)?;
    let proposed = "# loop_v1 sysparam plan\n".to_owned()
        + &a.payload
            .plan
            .iter()
            .map(|e| format!("{}\t{}\t{}\n", e.kind, e.path, e.value))
            .collect::<String>();
    require(
        sysparam::plan_text(&cand, &wl)? == proposed,
        "artifact plan differs from whitelist-resolved params",
    )?;
    // GOL metadata is preserved in the artifact hash but never replaces receiver-owned rules/probe.
    let _ = (
        &a.payload.why,
        &a.payload.whitelist,
        &a.payload.deny_keywords,
    );
    let mut expected = b.clone();
    for (id, value) in &cand {
        let spec = sysparam::wl_get(&wl, id).ok_or("missing whitelist spec")?;
        let key = autoloop::snapshot_key_of(&spec.kind, &spec.path)
            .ok_or("control has no snapshot coverage")?;
        require(
            b.get(&key)
                .is_some_and(|x| !x.is_empty() && x != "<unreadable>"),
            "control prior value unreadable",
        )?;
        expected.insert(key, value.clone());
    }
    for key in autoloop::touched_keys(&cand, &wl) {
        require(
            b.get(&key)
                .is_some_and(|x| !x.is_empty() && x != "<unreadable>"),
            "touched sibling missing/unreadable",
        )?;
    }
    require(
        expected == during,
        "requested control not read back, or hidden state changed",
    )
}
pub(crate) fn trace_bounds(text: &str) -> Option<(f64, f64)> {
    let l = text
        .lines()
        .find(|l| l.starts_with("#loop_v1_meta capture_start_uptime="))?;
    let field = |key: &str| {
        l.split_whitespace()
            .find_map(|s| s.strip_prefix(key))?
            .parse::<f64>()
            .ok()
    };
    let (a, b) = (
        field("capture_start_uptime=")?,
        field("capture_end_uptime=")?,
    );
    (a.is_finite() && b.is_finite() && a >= 0. && b > a).then_some((a, b))
}
struct LoadedRun {
    manifest: Run,
    conditions: Conditions,
    parsed: Value,
    render: Option<(
        crate::render_attribution::Scope,
        crate::render_attribution::Baseline,
    )>,
    gates: BTreeMap<String, bool>,
    issues: Vec<String>,
    checks: Vec<qualification::Check>,
    uptime: (f64, f64),
    env_window: sysparam::SampleWindow,
    start: DateTime<FixedOffset>,
    end: DateTime<FixedOffset>,
}
impl LoadedRun {
    fn valid(&self) -> bool {
        self.issues.is_empty()
            && self.gates.values().all(|x| *x)
            && self.checks.iter().all(|c| c.state == "pass")
    }
}
fn temperature_contract(c: &Conditions) -> Result<Option<TemperatureContract>, String> {
    if c.policy.version == 1 {
        return Ok(None);
    }
    Ok(Some(TemperatureContract {
        profile_id: c
            .policy
            .temperature_profile_id
            .clone()
            .ok_or("loaded profile missing")?,
        receiver_policy_version: 2,
        profile_sha256: c
            .policy
            .temperature_profile_sha256
            .clone()
            .ok_or("loaded profile hash missing")?,
        reference_sha256: c
            .policy
            .thermal_reference_sha256
            .clone()
            .ok_or("loaded reference hash missing")?,
        thermal_contract_sha256: c
            .thermal_contract
            .as_ref()
            .ok_or("loaded raw contract missing")?
            .sha256
            .clone(),
    }))
}
struct LoadedThermal {
    admission_ok: bool,
    window_ok: bool,
    issues: Vec<String>,
}
fn loaded_thermal(
    reader: &mut Reader,
    base: &Path,
    c: &Conditions,
    run: &Run,
    env: &sysparam::SampleWindow,
    capture: (f64, f64),
    start: DateTime<FixedOffset>,
    end: DateTime<FixedOffset>,
) -> Result<LoadedThermal, String> {
    use crate::confirmation_driver::temperature;
    use crate::thermal_profile;
    require(
        run.raw.cold.is_none(),
        "legacy cold receipt cannot replace loaded admission",
    )?;
    let binding = temperature_contract(c)?.ok_or("loaded binding missing")?;
    let contract_ref = c
        .thermal_contract
        .as_ref()
        .ok_or("thermal evidence missing")?;
    let (path, frozen): (_, FrozenThermalEvidence) = reader.json(base, contract_ref)?;
    let frozen_base = path.parent().ok_or("thermal evidence directory missing")?;
    require(
        frozen.schema == "phonefarm/frozen_thermal_evidence@1"
            && frozen.actual_serial == c.device_serial
            && frozen.boot_id == c.device_boot_id
            && frozen.profile_sha256 == binding.profile_sha256
            && frozen.reference_sha256 == binding.reference_sha256
            && frozen.reference.sha256 == binding.reference_sha256,
        "thermal evidence identity/profile/reference binding changed",
    )?;
    let clock_scope = if run.evidence_origin == "synthetic" {
        "synthetic_virtual_timing"
    } else {
        "host_utc_and_device_proc_uptime_separate"
    };
    require(
        frozen.clock_scope == clock_scope,
        "thermal producer clock scope unknown/mixed",
    )?;
    let rules = time(&frozen.rules_frozen_at)?;
    let warm_start = time(&frozen.warmup_started_at)?;
    let warm_end = time(&frozen.warmup_finished_at)?;
    require(
        rules <= warm_start
            && warm_start < warm_end
            && warm_end < time(&c.frozen_at)?
            && time(&c.frozen_at)? < start,
        "thermal rules/reference were not frozen before arm chronology",
    )?;
    let metadata = reader.text(frozen_base, &frozen.metadata_raw)?;
    let hal = reader.text(frozen_base, &frozen.hal_raw)?;
    let warmup = reader.text(frozen_base, &frozen.warmup_raw)?;
    let reconstructed = temperature::reconstruct_raw(&metadata, &hal, &warmup, &c.device_boot_id)?;
    let (_, profile): (_, thermal_profile::Profile) = reader.json(frozen_base, &frozen.profile)?;
    let (_, reference): (_, thermal_profile::ReferenceBand) =
        reader.json(frozen_base, &frozen.reference)?;
    require(
        profile == reconstructed.profile
            && reference == reconstructed.reference
            && thermal_profile::profile_sha256(&profile)? == binding.profile_sha256
            && profile.id == binding.profile_id,
        "stored thermal profile/reference differs from original raw reconstruction",
    )?;
    let warm_last = reference
        .warmup
        .last()
        .ok_or("empty reconstructed warmup")?
        .uptime_s;
    let warm_span = warm_last - reference.warmup.first().ok_or("empty warmup")?.uptime_s;
    require(
        (warm_end - warm_start).num_milliseconds() as f64 / 1000. + 0.01 >= warm_span,
        "thermal warmup device span exceeds retained host window",
    )?;
    let paths = profile
        .sensors
        .iter()
        .map(|s| s.path.clone())
        .collect::<Vec<_>>();
    let admission_ref = run
        .raw
        .thermal_admission
        .as_ref()
        .ok_or("loaded pre-arm raw reading missing")?;
    let admission_raw = reader.text(base, admission_ref)?;
    let admission = temperature::samples(&admission_raw, &paths)?;
    require(
        admission.len() == 1,
        "pre-arm thermal receipt must contain one complete snapshot",
    )?;
    let window_ref = run
        .raw
        .thermal_window
        .as_ref()
        .ok_or("loaded full thermal window missing")?;
    let window_raw = reader.text(base, window_ref)?;
    let (samples, completion) = temperature::parse_window_raw(&window_raw, &paths)?;
    let thermal_exit = reader
        .text(
            base,
            run.raw
                .thermal_sampler_exit
                .as_ref()
                .ok_or("thermal sampler exit missing")?,
        )?
        .trim()
        .parse::<i64>()
        .map_err(|_| "thermal sampler exit malformed")?;
    require(
        samples.len() >= 2 && samples.len() <= 512,
        "loaded thermal window count outside bounds",
    )?;
    let first = samples.first().unwrap().uptime_s;
    let last = samples.last().unwrap().uptime_s;
    let numeric = |key: &str| {
        completion[key]
            .as_f64()
            .filter(|v| v.is_finite())
            .ok_or("thermal completion value missing/nonfinite")
    };
    let dur = numeric("duration_s")?;
    let ivl = numeric("interval_s")?;
    let begin = numeric("start_uptime_s")?;
    let finish = numeric("end_uptime_s")?;
    let count = completion["samples"]
        .as_u64()
        .ok_or("thermal completion count malformed")?;
    let env_first = env.first_uptime.ok_or("ENV first uptime missing")?;
    let env_last = env.last_uptime.ok_or("ENV last uptime missing")?;
    let env_duration = env.duration_s.ok_or("ENV duration missing")?;
    let pre = admission[0].uptime_s;
    let host_span = (end - start).num_milliseconds() as f64 / 1000.;
    let admission_report = thermal_profile::evaluate_snapshot(&profile, &admission[0])?;
    let in_reference = profile.sensors.iter().all(|s| {
        admission_report
            .normalized_c
            .get(&s.path)
            .zip(reference.bands.get(&s.path))
            .is_some_and(|(c, b)| *c >= b.low_c && *c <= b.high_c)
    });
    let admission_ok = admission_report.compliant
        && in_reference
        && warm_last < pre
        && pre <= first
        && pre <= env_first
        && env_first - pre <= 60.;
    let assessed = thermal_profile::evaluate_against_reference(&profile, &reference, &samples)?;
    let window_ok = thermal_exit == 0
        && assessed.compliant
        && env.complete
        && ivl == 1.
        && dur == env_duration + 3.
        && count == samples.len() as u64
        && begin <= first
        && first - begin <= ivl
        && finish >= last
        && finish - last <= 6.
        && last - first >= dur - ivl
        && finish - begin <= dur + 9.
        && first >= pre
        && first <= env_first
        && last >= env_last
        && first <= capture.0
        && last >= capture.1
        && samples
            .windows(2)
            .all(|w| w[1].uptime_s - w[0].uptime_s >= ivl * 0.5)
        && last - first <= host_span
        && finish - begin <= host_span;
    let mut issues = Vec::new();
    if !admission_ok {
        issues.push("loaded pre-arm snapshot/reference/chronology failed".into());
    }
    if !window_ok {
        issues.push("loaded typed thermal readings do not cover the complete ENV/capture window under the frozen reference".into());
    }
    issues.extend(assessed.reasons);
    Ok(LoadedThermal {
        admission_ok,
        window_ok,
        issues,
    })
}
fn load_run(
    reader: &mut Reader,
    base: &Path,
    r: &FileRef,
    frozen_render: Option<&crate::render_attribution::Frozen>,
) -> Result<LoadedRun, String> {
    let (p, run): (_, Run) = reader.json(base, r)?;
    let base = p.parent().ok_or("run has no parent directory")?;
    let (_, c): (_, Conditions) = reader.json(base, &run.conditions)?;
    conditions_valid(&c)?;
    require(
        (run.schema == "phonefarm/run_one_bundle@1" && c.policy.version == 1 && c.render_attribution.is_none())
            || (run.schema == "phonefarm/run_one_bundle@2" && c.policy.version == 2 && c.render_attribution.is_none())
            || (run.schema == "phonefarm/run_one_bundle@3" && c.render_attribution.is_some()),
        "requires raw single-run bundle under its versioned conditions, not aggregate qualification",
    )?;
    if c.policy.version == 1 {
        require(
            run.raw.thermal_admission.is_none()
                && run.raw.thermal_window.is_none()
                && run.raw.thermal_sampler_exit.is_none(),
            "legacy bundle cannot include loaded receipts",
        )?;
    }
    let (_, artifact): (_, Artifact) = reader.json(base, &run.artifact)?;
    let start = time(&run.started_at)?;
    let end = time(&run.finished_at)?;
    require(
        start > time(&c.frozen_at)?
            && end > start
            && (end - start).num_milliseconds() as f64 >= c.capture_seconds as f64 * 1000.,
        "invalid run/conditions chronology",
    )?;
    require(
        run.device_serial == c.device_serial
            && !run.run_id.trim().is_empty()
            && matches!(run.sample_role.as_str(), "baseline" | "candidate")
            && matches!(run.phase.as_str(), "admission" | "confirmation"),
        "run identity/role invalid",
    )?;
    let trace = reader.text(base, &run.raw.trace)?;
    let env = reader.text(base, &run.raw.env)?;
    let root = reader.text(base, &run.raw.root_id)?;
    let boot = reader.text(base, &run.raw.boot_id)?;
    let probe = reader.text(base, &run.raw.probe)?;
    let before = reader.text(base, &run.raw.snapshot_before)?;
    let active = reader.text(base, &run.raw.snapshot_at_run)?;
    let after = reader.text(base, &run.raw.snapshot_after_run)?;
    let restored = reader.text(base, &run.raw.snapshot_restored)?;
    let exit = |reader: &mut Reader, r: &FileRef| -> Result<i64, String> {
        reader
            .text(base, r)?
            .trim()
            .parse()
            .map_err(|_| "invalid saved process exit code".into())
    };
    let workload_exit = exit(reader, &run.raw.workload_exit)?;
    let sampler_exit = exit(reader, &run.raw.sampler_exit)?;
    let capture_exit = exit(reader, &run.raw.capture_exit)?;
    let restore_exit = exit(reader, &run.raw.restore_exit)?;
    let parsed = looptrace::parse(&trace, &c.render_comm);
    let (summary, render): (Value, _) = if let Some(reg) = &c.render_attribution {
        require(
            (run.phase == "admission" && frozen_render.is_none())
                || (run.phase == "confirmation" && frozen_render.is_some()),
            "attributed heldout requires frozen baseline grouping",
        )?;
        let initial = reader.text(base, &reg.initial_witness)?;
        for receipt in [&before, &active, &after, &restored] {
            let snap = snapshot(receipt, &c.snapshot_cpu_policies)?;
            require(
                snap.get("ftrace.trace_clock")
                    .is_some_and(|v| !v.is_empty() && v != "<unreadable>"),
                "origin clock restoration snapshot absent",
            )?;
        }

        let scope =
            crate::render_attribution::witness(&initial, &reg.package, &c.render_comm)?.scope;
        require(
            scope.boot_id == c.device_boot_id,
            "initial render boot differs",
        )?;
        let before = reader.text(
            base,
            run.raw
                .render_before
                .as_ref()
                .ok_or("render before witness absent")?,
        )?;
        let after = reader.text(
            base,
            run.raw
                .render_after
                .as_ref()
                .ok_or("render after witness absent")?,
        )?;
        let final_raw = reader.text(
            base,
            run.raw.render_final.as_ref().ok_or("render final-count witness absent")?,
        )?;
        let (summary, record) =
            crate::render_attribution::analyze(&trace, &before, &after, &final_raw, &scope, frozen_render)?;
        (summary, Some((scope, record)))
    } else {
        require(
            run.raw.render_before.is_none()
                && run.raw.render_after.is_none()
                && run.raw.render_final.is_none()
                && frozen_render.is_none(),
            "legacy cannot carry ownership evidence",
        )?;
        (
            serde_json::from_str(&pyjson::dumps(&parsed.summarize())).map_err(|e| e.to_string())?,
            None,
        )
    };
    let mut env_stats: Value = serde_json::from_str(&pyjson::dumps(&sysparam::env_stats(&env)))
        .map_err(|e| e.to_string())?;
    let soc_zones = env.lines().filter(|l| l.starts_with("ENV ")).all(|l| {
        l.split_whitespace().nth(7).is_some_and(|zone| {
            zone.starts_with("cpu-") || zone.starts_with("cpullc") || zone.starts_with("gpuss")
        })
    });
    env_stats["thermal_window_verified"] =
        json!(soc_zones && env_stats["thermal_window_verified"] == true);
    let uptime = trace_bounds(&trace).ok_or("missing raw trace capture window")?;
    let env_window = sysparam::sample_window(&env);
    let thermal = if c.policy.version == 2 {
        Some(loaded_thermal(
            reader,
            base,
            &c,
            &run,
            &env_window,
            uptime,
            start,
            end,
        )?)
    } else {
        None
    };
    if let Some(t) = &thermal {
        env_stats["thermal_profile_verified"] = json!(t.admission_ok);
        env_stats["temperature_profile_sha256"] = json!(c.policy.temperature_profile_sha256);
        env_stats["thermal_reference_sha256"] = json!(c.policy.thermal_reference_sha256);
        env_stats["thermal_window_verified"] = json!(t.window_ok);
    }
    let host_elapsed = (end - start).num_milliseconds() as f64 / 1000.;
    let window_ok = sampler_exit == 0
        && capture_exit == 0
        && sysparam::trace_window_covered(&env_window, &trace, c.capture_seconds)
        && env_window.duration_s.is_some_and(|dur| dur <= host_elapsed)
        && env_window
            .first_uptime
            .zip(env_window.last_uptime)
            .is_some_and(|(a, b)| b - a <= host_elapsed)
        && summary["span_s"].as_f64().is_some_and(|span| {
            span >= c.capture_seconds as f64 - 1. && span <= uptime.1 - uptime.0 + 0.01
        });
    env_stats["trace_window_verified"] = json!(window_ok);
    let frame = qualification::frame_evidence(&summary);
    let sample = json!({"summary":summary,"env":env_stats,"workload_exit":workload_exit,
        "frame_evidence":frame,"raw_trace_sha256":run.raw.trace.sha256});
    let mut issues = vec![];
    if let Some(t) = &thermal {
        issues.extend(t.issues.clone());
    }
    if run.evidence_origin != "recorded_device" && run.evidence_origin != "live_device" {
        issues
            .push("synthetic/unknown origin cannot validate a recorded device observation".into());
    }
    if boot.trim() != c.device_boot_id {
        issues.push("device boot identity differs from conditions".into());
    }
    let mut gates = BTreeMap::new();
    let uids: Vec<_> = root
        .split_whitespace()
        .filter(|s| s.starts_with("uid="))
        .collect();
    gates.insert(
        "root_verified".into(),
        uids.len() == 1
            && uids
                .first()
                .is_some_and(|s| s.starts_with("uid=0(") || *s == "uid=0"),
    );
    let applied = control(&artifact, &probe, &before, &active, &after, &c);
    if let Err(e) = &applied {
        issues.push(e.clone());
    }
    gates.insert("control_verified".into(), applied.is_ok());
    if let Some(t) = &thermal {
        gates.insert("thermal_profile_verified".into(), t.admission_ok);
        gates.insert("thermal_window_verified".into(), t.window_ok);
    } else {
        let (_, cold): (_, Cold) = reader.json(
            base,
            run.raw.cold.as_ref().ok_or("legacy cold receipt missing")?,
        )?;
        let cold_at = time(&cold.checked_at)?;
        let cold_ok = cold.c.is_finite()
            && cold.c > 0.
            && c.policy.cool_c.is_some_and(|cap| cold.c < cap)
            && (cold.zone.starts_with("cpu-")
                || cold.zone.starts_with("cpullc")
                || cold.zone.starts_with("gpuss"))
            && cold_at <= start
            && (start - cold_at).num_milliseconds() <= 60_000;
        let _ = cold.waited_s;
        gates.insert("cold_verified".into(), cold_ok);
    }
    gates.insert("trace_window_verified".into(), window_ok);
    gates.insert("workload_succeeded".into(), workload_exit == 0);
    gates.insert(
        "restore_verified".into(),
        restore_exit == 0
            && snapshot(&before, &c.snapshot_cpu_policies).is_ok()
            && snapshot(&restored, &c.snapshot_cpu_policies).is_ok()
            && eval::snapshots_identical(&before, &restored),
    );
    let mut checks = vec![];
    qualification::runs_checks(
        &mut checks,
        std::slice::from_ref(&sample),
        "sample",
        &c.policy,
    );
    Ok(LoadedRun {
        render,
        manifest: run,
        conditions: c,
        parsed: sample,
        gates,
        issues,
        checks,
        uptime,
        env_window,
        start,
        end,
    })
}

fn frozen_valid(f: &Frozen, c: &Conditions) -> Result<(), String> {
    require(
        f.schema == "game_opt_loop/confirmation_plan@1"
            && hash(
                &serde_json::to_vec(&(&f.schema, &f.registered_at, &f.plan))
                    .map_err(|e| e.to_string())?,
            ) == f.sha256,
        "frozen plan fingerprint differs from GOL typed contract",
    )?;
    let p = &f.plan;
    require(
        p.temperature_contract == temperature_contract(c)?,
        "frozen thermal identity differs from versioned receiver conditions",
    )?;
    require(
        [
            &p.baseline_artifact_sha256,
            &p.candidate_artifact_sha256,
            &p.conditions_sha256,
            &p.baseline_admission_sha256,
            &f.sha256,
        ]
        .iter()
        .all(|s| digest_ok(s)),
        "invalid frozen SHA-256",
    )?;
    require(
        p.pairs == c.policy.required_pairs as usize
            && p.family_alpha == c.policy.alpha
            && p.paired_t_assumptions_acknowledged
            && p.schedule.len() == p.pairs,
        "frozen pair/alpha contract differs from receiver policy",
    )?;
    let exploration: BTreeSet<_> = p.exploration_run_ids.iter().collect();
    require(
        !exploration.is_empty()
            && exploration.len() == p.exploration_run_ids.len()
            && exploration.iter().all(|s| !s.trim().is_empty()),
        "unique exploration IDs required",
    )?;
    require(
        !p.exploration_artifact_sha256.is_empty()
            && p.exploration_artifact_sha256.iter().all(|s| digest_ok(s)),
        "exploration raw hashes required",
    )?;
    let mut ids = BTreeSet::new();
    let mut pairs = BTreeSet::new();
    for s in &p.schedule {
        require(
            !s.pair_id.trim().is_empty() && pairs.insert(&s.pair_id),
            "duplicate/empty frozen pair",
        )?;
        for id in [&s.a_run_id, &s.b_run_id] {
            require(
                !id.trim().is_empty() && !exploration.contains(id) && ids.insert(id),
                "reused/exploration frozen run ID",
            )?;
        }
    }
    let first = p.schedule.iter().filter(|s| s.baseline_first).count();
    require(
        first.abs_diff(p.pairs - first) <= 1,
        "frozen schedule must be counterbalanced",
    )?;
    let mut names = BTreeSet::new();
    let mut receiver_metrics = BTreeSet::new();
    let mut primary = false;
    require(
        !p.metrics.is_empty() && p.metrics.len() <= 32,
        "known frozen metrics required",
    )?;
    for m in &p.metrics {
        let receiver_name = match m.name.as_str() {
            "fps_mean" => "fps_mean",
            "frame_p95_ms" | "fps_p95_ms" => "frame_p95",
            "power_watt" => "power_w_mean",
            _ => return Err("unsupported frozen metric".into()),
        };
        require(
            receiver_metrics.insert(receiver_name.to_owned()),
            "duplicate aliases of the same receiver metric",
        )?;
        require(
            names.insert(&m.name)
                && matches!(
                    (m.name.as_str(), m.direction.as_str()),
                    ("power_watt" | "frame_p95_ms" | "fps_p95_ms", "lower")
                        | ("fps_mean", "higher")
                )
                && matches!(m.scale.as_str(), "percent" | "absolute")
                && m.maximum_regression.is_finite()
                && m.maximum_regression >= 0.,
            "invalid metric rule",
        )?;
        if let Some(n) = m.minimum_improvement {
            require(
                n.is_finite() && n > 0.,
                "improvement target must be positive finite",
            )?;
            primary |= n > 0.;
        }
        require(
            m.name != "power_watt" || c.policy.goal == qualification::Goal::RenderEfficiency,
            "power target requires the receiver battery efficiency policy",
        )?;
    }
    require(
        receiver_metrics == c.policy.required_metrics.iter().cloned().collect(),
        "frozen plan must include every receiver goal metric; no silent narrowing",
    )?;
    require(primary, "freeze at least one improvement target")
}

struct Admitted {
    qualification: Value,
    capture_end: f64,
    env_end: f64,
    render: Option<crate::render_attribution::Frozen>,
    render_contract_sha256: Option<String>,
}
fn admission(
    reader: &mut Reader,
    base: &Path,
    r: &FileRef,
    f: &Frozen,
    c: &Conditions,
) -> Result<Admitted, String> {
    require(
        r.sha256 == f.plan.baseline_admission_sha256,
        "admission document differs from frozen registration",
    )?;
    let (path, a): (_, Admission) = reader.json(base, r)?;
    let base = path.parent().ok_or("admission has no directory")?;
    require(
        ((a.schema == "phonefarm/confirmation_baseline_admission@1" && c.render_attribution.is_none() && a.render_contract.is_none())
            || (a.schema == "phonefarm/confirmation_baseline_admission@2" && c.render_attribution.is_some() && a.render_contract.is_some()))
            && a.conditions.sha256 == f.plan.conditions_sha256
            && a.baseline_artifact.sha256 == f.plan.baseline_artifact_sha256,
        "admission must bind raw baselines and matching conditions/artifact",
    )?;
    reader.read(base, &a.conditions, JSON_LIMIT)?;
    reader.read(base, &a.baseline_artifact, JSON_LIMIT)?;
    require(
        (3..=32).contains(&a.runs.len()),
        "admission requires 3..32 raw baseline runs",
    )?;
    let registered = time(&f.registered_at)?;
    let mut seen_ids = BTreeSet::new();
    let mut raw_seen = BTreeSet::new();
    let mut baseline = vec![];
    let mut render_records=vec![];
    let mut render_scope=None;
    let mut previous: Option<(DateTime<FixedOffset>, f64, f64)> = None;
    for r in &a.runs {
        let run = load_run(reader, base, r, None)?;
        let m = &run.manifest;
        require(
            run.valid()
                && m.phase == "admission"
                && m.sample_role == "baseline"
                && m.pair_id.is_none()
                && m.conditions.sha256 == a.conditions.sha256
                && m.artifact.sha256 == a.baseline_artifact.sha256
                && run.conditions.device_serial == c.device_serial
                && run.conditions.device_boot_id == c.device_boot_id,
            "baseline admission arm not independently valid or identity changed",
        )?;
        require(
            run.end < registered
                && previous.is_none_or(|(end, uptime,env_end)| run.start > end && run.uptime.0 > uptime
                    && run.env_window.first_uptime.is_some_and(|t|t>env_end)),
            "baselines must have independent host/capture/ENV windows and finish before registration",
        )?;
        require(
            seen_ids.insert(m.run_id.clone()) && f.plan.exploration_run_ids.contains(&m.run_id),
            "baseline IDs reused or absent from frozen exploration exclusion",
        )?;
        for raw in unique_raw_artifacts(&m.raw) {
            require(
                raw_seen.insert(raw.clone()) && f.plan.exploration_artifact_sha256.contains(raw),
                "baseline raw artifacts reused or absent from frozen exploration exclusion",
            )?;
        }
        previous = Some((
            run.end,
            run.uptime.1,
            run.env_window
                .last_uptime
                .ok_or("missing baseline ENV window")?,
        ));
        if let Some((scope, record)) = run.render {
            require(
                render_scope.as_ref().is_none_or(|old| old == &scope),
                "baseline ownership scope differs",
            )?;
            render_scope = Some(scope);
            render_records.push(record);
        }
        baseline.push(run.parsed);
    }
    let render = if let Some(scope) = render_scope {
        let derived = crate::render_attribution::freeze(&scope, render_records)?;
        let (_, saved): (_, crate::render_attribution::Frozen) = reader.json(
            base,
            a.render_contract
                .as_ref()
                .ok_or("render frozen contract absent")?,
        )?;
        require(
            saved == derived,
            "frozen render contract does not reconstruct from baseline raw witnesses",
        )?;
        Some(derived)
    } else {
        require(
            a.render_contract.is_none(),
            "legacy render contract supplied",
        )?;
        None
    };
    let mut e = qualification::Evidence::new(
        qualification::Stage::Experiment,
        c.policy.clone(),
        qualification::Capabilities {
            backend: "adb".into(),
            root: Some(true),
            trace_available: Some(true),
            measurement_available: Some(true),
            control_verified: Some(true),
        },
    );
    // Booleans above/below come only after every raw baseline gate has passed, not the input JSON.
    e.evidence_origin = "recorded_device".into();
    e.workload_verified = Some(true);
    if c.policy.version == 1 {
        e.cold_ok = Some(true);
    } else {
        e.thermal_admission_verified = Some(true);
    }
    e.restored = Some(true);
    e.baseline = baseline;
    let q = qualification::evaluate(&e)?;
    let (_, capture_end, env_end) = previous.ok_or("missing baseline window")?;
    Ok(Admitted {
        qualification: serde_json::to_value(q).map_err(|e| e.to_string())?,
        capture_end,
        env_end,
        render,
        render_contract_sha256: a.render_contract.as_ref().map(|r| r.sha256.clone()),
    })
}

/// Reconstruct the render contract without granting source/experiment qualification.
/// Used for both live and explicitly synthetic sessions; errors never become fake truth.
pub(crate) fn validate_render_admission(path: &Path) -> Result<(), String> {
    let mut reader = Reader::default();
    let bytes = regular_bytes(path, JSON_LIMIT)?;
    let a: Admission = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    let base = path.parent().ok_or("admission directory absent")?;
    require(
        a.schema == "phonefarm/confirmation_baseline_admission@2" && a.runs.len() == 3,
        "origin admission requires three raw baseline runs",
    )?;
    let (_, c): (_, Conditions) = reader.json(base, &a.conditions)?;
    conditions_valid(&c)?;
    require(
        c.render_attribution.is_some(),
        "origin registration missing",
    )?;
    reader.read(base, &a.baseline_artifact, JSON_LIMIT)?;
    let mut scope = None;
    let mut records = vec![];
    let mut ids = BTreeSet::new();
    let mut previous = None;
    for r in &a.runs {
        let loaded = load_run(&mut reader, base, r, None)?;
        let m = &loaded.manifest;
        require(
            m.phase == "admission"
                && m.sample_role == "baseline"
                && m.pair_id.is_none()
                && m.conditions.sha256 == a.conditions.sha256
                && m.artifact.sha256 == a.baseline_artifact.sha256
                && ids.insert(m.run_id.clone()),
            "origin baseline identity/role differs",
        )?;
        if let Some((end, up)) = previous {
            require(
                loaded.start > end && loaded.uptime.0 > up,
                "origin baselines overlap",
            )?;
        }
        previous = Some((loaded.end, loaded.uptime.1));
        let (observed, record) = loaded.render.ok_or("origin baseline record missing")?;
        require(
            scope.as_ref().is_none_or(|old| old == &observed),
            "origin baseline scope changed",
        )?;
        scope = Some(observed);
        records.push(record);
    }
    let frozen = crate::render_attribution::freeze(&scope.ok_or("origin scope absent")?, records)?;
    let (_, saved): (_, crate::render_attribution::Frozen) = reader.json(
        base,
        a.render_contract
            .as_ref()
            .ok_or("origin frozen contract absent")?,
    )?;
    require(
        saved == frozen,
        "frozen render contract does not reconstruct from baseline raw witnesses",
    )
}

fn blank() -> Value {
    json!({"schema":"phonefarm/confirmation_observation@1","source":"phonefarm","status":"BLOCKED",
        "qualification":{"stage":"experiment","eligible":false,"evidence_origin":"unknown","claim_scope":"none"},
        "sample_qualification":{"schema":"phonefarm/confirmation_sample_qualification@1","valid_observation":false,
          "gates":{"root_verified":false,"control_verified":false,"cold_verified":false,"trace_window_verified":false,
          "workload_succeeded":false,"restore_verified":false,"baseline_admission_verified":false},"baseline_admission_sha256":null},
        "confirmation_binding":null,"metrics":{"fps_mean":null,"frame_p95_ms":null,"fps_p95_ms":null,"power_watt":null},
        "measurement_window":null,
        "metric_provenance":{},"issues":[],"checks":[],"artifact_hashes":{},
        "real_game_optimization_proven":false,"promotion_allowed":false,
        "source_attestation":"driver declarations and exact raw bytes checked; device identity, acquisition and chronology not attested",
        "control_validation_scope":"snapshot readback at both sampling boundaries only; sustained control not verified",
        "restore_validation_scope":"byte equality of all device_snapshot v1 fixed domains plus frozen declared CPU policy domains; unreadable untouched fields and unlisted device nodes are not observed",
        "limitations":["GPU submit cadence proxy is not present/display FPS","no quality, deployment or real-game optimization proof",
          "trace_window_verified checks capture uptime metadata and ENV coverage; ftrace event clock/location not verified; submit span is duration only",
          "battery mean is over the complete sampler window covering the trace, not cropped to event timestamps",
          "existing run_once directories need heldout-driver receipts; missing context is BLOCKED"]})
}
fn reconstruct(path: &Path, reader: &mut Reader, out: &mut Value) -> Result<(), String> {
    let metadata = std::fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    require(
        metadata.is_file() && !metadata.file_type().is_symlink() && metadata.len() <= JSON_LIMIT,
        "bundle must be a bounded regular file",
    )?;
    let bytes = regular_bytes(path, JSON_LIMIT)?;
    require(bytes.len() as u64 <= JSON_LIMIT, "bundle too large")?;
    let b: Bundle = serde_json::from_slice(&bytes)
        .map_err(|e| format!("bundle missing driver context or invalid: {e}"))?;
    require(
        b.schema == "phonefarm/confirmation_bundle@1",
        "unknown confirmation bundle schema",
    )?;
    let base = path.parent().unwrap_or(Path::new("."));
    let (_, f): (_, Frozen) = reader.json(base, &b.frozen_plan)?;
    let (run_path,raw_run): (_,Run)=reader.json(base,&b.run)?;
    let run_base=run_path.parent().ok_or("run parent missing")?.to_path_buf();
    let (_,conditions): (_,Conditions)=reader.json(&run_base,&raw_run.conditions)?;
    conditions_valid(&conditions)?;frozen_valid(&f,&conditions)?;
    // Only attributed measurements need baseline grouping before the arm can
    // be interpreted. Preserve structurally checked legacy/loaded diagnostics
    // when their separate admission is later rejected; never relax admission.
    let admitted = if conditions.render_attribution.is_some() {
        Some(admission(reader, base, &b.baseline_admission, &f, &conditions)?)
    } else {
        None
    };
    let run = load_run(reader, base, &b.run, admitted.as_ref().and_then(|a| a.render.as_ref()))?;
    let m = &run.manifest;
    let c = &run.conditions;
    if c.policy.version == 2 {
        out["schema"] = json!("phonefarm/confirmation_observation@2");
        out["sample_qualification"]["schema"] =
            json!("phonefarm/confirmation_sample_qualification@2");
    }
    out["qualification"]["version"] = json!(c.policy.version);
    out["qualification"]["policy"] =
        serde_json::to_value(&c.policy).map_err(|e| e.to_string())?;
    out["qualification"]["evidence_origin"] = json!(if matches!(
        m.evidence_origin.as_str(),
        "recorded_device" | "live_device"
    ) {
        "recorded_device"
    } else {
        m.evidence_origin.as_str()
    });
    out["sample_qualification"]["gates"] = json!(run.gates);
    out["sample_qualification"]["gates"]["baseline_admission_verified"] = json!(false);
    out["sample_qualification"]["baseline_admission_sha256"] = json!(b.baseline_admission.sha256);
    out["checks"] = json!(run.checks);
    out["issues"] = json!(run.issues);
    let power_qualified = c.policy.goal == qualification::Goal::RenderEfficiency
        && run.parsed["env"]["power_rail"] == "battery"
        && run.parsed["env"]["power_window_verified"] == true
        && run.parsed["env"]["on_battery"] == true
        && run.gates["trace_window_verified"];
    let measured_power = if power_qualified {
        qualification::metric(&run.parsed, "power_w_mean")
    } else {
        None
    };
    out["metrics"] = json!({"fps_mean":qualification::metric(&run.parsed,"fps_mean"),
        "frame_p95_ms":qualification::metric(&run.parsed,"frame_p95"),"fps_p95_ms":qualification::metric(&run.parsed,"frame_p95"),
        "power_watt":measured_power});
    let mut frame = run.parsed["frame_evidence"].clone();
    frame["raw_sha256"] = json!(m.raw.trace.sha256);
    out["metric_provenance"] = json!({"fps_mean":frame,"frame_p95_ms":frame,"fps_p95_ms":frame,
        "power_watt":{"source":"battery_sysfs","rail":run.parsed["env"]["power_rail"],
            "qualified_for_comparison":power_qualified,
            "window_verified":run.parsed["env"]["power_window_verified"] == true && run.gates["trace_window_verified"],
            "discharging":run.parsed["env"]["on_battery"] == true,"raw_sha256":m.raw.env.sha256}});
    out["confirmation_binding"] = json!({"pair_id":m.pair_id,"run_id":m.run_id,"sample_role":m.sample_role,
        "aggregation_level":"single_run","artifact_sha256":m.artifact.sha256,"conditions_sha256":m.conditions.sha256,
        "frozen_plan_sha256":f.sha256,"started_at":m.started_at,"finished_at":m.finished_at,
        "raw_artifacts":{"trace_sha256":m.raw.trace.sha256,"env_sha256":m.raw.env.sha256}});
    if let Some(t) = temperature_contract(c)? {
        out["confirmation_binding"]["thermal_contract"] =
            serde_json::to_value(t).map_err(|e| e.to_string())?;
        for (key, r) in [
            ("thermal_admission_sha256", m.raw.thermal_admission.as_ref()),
            ("thermal_window_sha256", m.raw.thermal_window.as_ref()),
            (
                "thermal_sampler_exit_sha256",
                m.raw.thermal_sampler_exit.as_ref(),
            ),
        ] {
            out["confirmation_binding"]["raw_artifacts"][key] =
                json!(r.ok_or("loaded raw binding missing")?.sha256);
        }
    }
    out["measurement_window"] = json!({"schema":"phonefarm/measurement_window@1","device_serial":c.device_serial,
        "boot_id":c.device_boot_id,"capture_start_uptime_s":run.uptime.0,"capture_end_uptime_s":run.uptime.1,
        "env_start_uptime_s":run.env_window.first_uptime,"env_end_uptime_s":run.env_window.last_uptime,
        "env_duration_s":run.env_window.duration_s,"env_interval_s":run.env_window.interval_s,
        "clock_scope":"capture_metadata_and_env_proc_uptime"});
    frozen_valid(&f, c)?;
    require(
        m.phase == "confirmation"
            && run.start > time(&f.registered_at)?
            && m.conditions.sha256 == f.plan.conditions_sha256,
        "run is not a post-registration confirmation arm under frozen conditions",
    )?;
    let selected = if m.sample_role == "baseline" {
        &f.plan.baseline_artifact_sha256
    } else {
        &f.plan.candidate_artifact_sha256
    };
    require(
        &m.artifact.sha256 == selected
            && f.plan.schedule.iter().any(|p| {
                m.pair_id.as_ref() == Some(&p.pair_id)
                    && &m.run_id
                        == if m.sample_role == "baseline" {
                            &p.a_run_id
                        } else {
                            &p.b_run_id
                        }
            }),
        "role/run/pair/artifact differs from frozen schedule",
    )?;
    for raw in unique_raw_artifacts(&m.raw) {
        require(
            !f.plan.exploration_artifact_sha256.contains(raw),
            "confirmation reuses baseline/exploration raw artifact",
        )?;
    }
    let raw_hashes = unique_raw_artifacts(&m.raw);
    require(
        raw_hashes.iter().collect::<BTreeSet<_>>().len() == raw_hashes.len(),
        "independent trace/env/thermal reading raw bytes reused",
    )?;
    let admitted = match admitted {
        Some(admitted) => admitted,
        None => admission(reader, base, &b.baseline_admission, &f, c)?,
    };
    // Attributed grouping depends on prior admission. Every route requires
    // valid admission before it can grant a valid heldout observation.
    require(
        run.uptime.0 > admitted.capture_end && run.env_window.first_uptime.is_some_and(|t|t>admitted.env_end),
        "same-boot confirmation capture uptime and entire ENV window must follow all admission windows",
    )?;
    if let Some((scope, record)) = &run.render {
        out["target_game_attribution"] = crate::render_attribution::diagnostic(scope, record);
        out["sample_qualification"]["gates"]["target_command_origin_verified"] = json!(true);
        out["sample_qualification"]["gates"]["frozen_grouping_verified"] = json!(true);
        out["confirmation_binding"]["render_contract_sha256"] =
            json!(admitted.render_contract_sha256);
        for (key, r) in [
            ("render_before_sha256", m.raw.render_before.as_ref()),
            ("render_after_sha256", m.raw.render_after.as_ref()),
            ("render_final_sha256", m.raw.render_final.as_ref()),
        ] {
            out["confirmation_binding"]["raw_artifacts"][key] =
                json!(r.ok_or("render raw binding absent")?.sha256);
        }
    } else {
        out["target_game_attribution"] = json!({"status":"UNKNOWN","reason":"historical comm-only proxy, no ownership contract"});
    }
    let q = admitted.qualification;
    let admission_ok =
        q["experiment_allowed"] == true && q["eligible"] == false && q["stage"] == "experiment";
    out["baseline_admission_qualification"] = q;
    out["sample_qualification"]["gates"]["baseline_admission_verified"] = json!(admission_ok);
    let valid = run.valid()
        && admission_ok
        && f.plan.metrics.iter().all(|r| {
            out["metrics"][&r.name]
                .as_f64()
                .is_some_and(|v| v.is_finite() && v > 0.)
        });
    if valid {
        out["status"] = json!("VALID_OBSERVATION");
        out["sample_qualification"]["valid_observation"] = json!(true);
        out["qualification"]["claim_scope"] = json!(if c.policy.goal
            == qualification::Goal::RenderEfficiency
        {
            "one recorded sysparam experiment observation; submit-proxy render metrics and verified battery window, no improvement verdict"
        } else {
            "one recorded sysparam experiment observation; submit-proxy cadence only, no display/power/improvement verdict"
        });
    } else {
        out["issues"]
            .as_array_mut()
            .unwrap()
            .push(json!("one or more raw sample/metric/source gates failed"));
    }
    Ok(())
}

pub fn produce(path: &Path) -> Value {
    let mut out = blank();
    let mut reader = Reader::default();
    if let Err(e) = reconstruct(path, &mut reader, &mut out) {
        out["issues"].as_array_mut().unwrap().push(json!(e));
    }
    out["artifact_hashes"] = json!(reader.hashes);
    out
}

pub(crate) fn report_for_driver(path: &Path) -> Value {
    produce(path)
}
pub(crate) fn validate_driver_artifact(bytes: &[u8]) -> Result<(), String> {
    let a: Artifact = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    require(
        a.kind == "sysparam" && a.payload.params.len() <= 32,
        "driver requires bounded sysparam artifact",
    )
}
/// Internal driver uses the same raw algorithms before registration; never a conclusion.
pub(crate) fn validate_raw_run(path: &Path) -> Result<Value, String> {
    validate_raw_run_with_render(path, None)
}
pub(crate) fn validate_raw_run_with_render(
    path: &Path,
    frozen: Option<&crate::render_attribution::Frozen>,
) -> Result<Value, String> {
    let bytes = regular_bytes(path, JSON_LIMIT)?;
    let r = FileRef {
        path: path
            .file_name()
            .ok_or("missing raw bundle filename")?
            .to_string_lossy()
            .into(),
        sha256: hash(&bytes),
    };
    let loaded = load_run(
        &mut Reader::default(),
        path.parent().unwrap_or(Path::new(".")),
        &r,
        frozen,
    )?;
    Ok(
        json!({"render_record":loaded.render,"valid":loaded.valid(),"gates":loaded.gates,"issues":loaded.issues,"checks":loaded.checks,"parsed":loaded.parsed,
        "evidence_origin":loaded.manifest.evidence_origin,"real_game_optimization_proven":false,"promotion_allowed":false}),
    )
}
pub(crate) fn validate_admission_for_driver(
    path: &Path,
    plan: &Value,
    registered_at: &str,
) -> Result<Value, String> {
    let bytes = regular_bytes(path, JSON_LIMIT)?;
    let a: Admission = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    let base = path.parent().unwrap_or(Path::new("."));
    let mut reader = Reader::default();
    let (_, c): (_, Conditions) = reader.json(base, &a.conditions)?;
    conditions_valid(&c)?;
    let mut f = Frozen {
        schema: "game_opt_loop/confirmation_plan@1".into(),
        registered_at: registered_at.into(),
        plan: serde_json::from_value(plan.clone()).map_err(|e| e.to_string())?,
        sha256: String::new(),
    };
    f.sha256 = hash(
        &serde_json::to_vec(&(&f.schema, &f.registered_at, &f.plan)).map_err(|e| e.to_string())?,
    );
    frozen_valid(&f, &c)?;
    let r = FileRef {
        path: path
            .file_name()
            .ok_or("missing admission filename")?
            .to_string_lossy()
            .into(),
        sha256: hash(&bytes),
    };
    Ok(admission(&mut reader, base, &r, &f, &c)?.qualification)
}
pub(crate) fn validate_registration_for_driver(
    path: &Path,
    conditions_path: &Path,
    expected: &Value,
) -> Result<Value, String> {
    let f: Frozen =
        serde_json::from_slice(&regular_bytes(path, JSON_LIMIT)?).map_err(|e| e.to_string())?;
    let c: Conditions = serde_json::from_slice(&regular_bytes(conditions_path, JSON_LIMIT)?)
        .map_err(|e| e.to_string())?;
    conditions_valid(&c)?;
    frozen_valid(&f, &c)?;
    require(
        serde_json::to_value(&f.plan).map_err(|e| e.to_string())? == *expected,
        "registry changed receiver-owned plan",
    )?;
    serde_json::to_value(&f).map_err(|e| e.to_string())
}
pub fn run_confirmation_observation(args: &[String]) -> i32 {
    let mut input = None;
    let mut output = None;
    let mut error = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--bundle" | "--out" if i + 1 < args.len() => {
                let slot = if args[i] == "--bundle" {
                    &mut input
                } else {
                    &mut output
                };
                if slot.replace(PathBuf::from(&args[i + 1])).is_some() {
                    error = Some("duplicate option".into());
                }
                i += 2;
            }
            "--json" => {
                i += 1;
            }
            _ => {
                error = Some(format!("unknown/missing argument: {}", args[i]));
                break;
            }
        }
    }
    let mut out = if let Some(path) = input {
        produce(&path)
    } else {
        error = Some("use confirmation-observation --bundle B.json --out R.json".into());
        blank()
    };
    if let Some(e) = error {
        out = blank();
        out["issues"] = json!([e]);
    }
    let bytes = serde_json::to_vec_pretty(&out).unwrap();
    if let Some(path) = output {
        let saved = (|| -> std::io::Result<()> {
            let mut f = std::fs::OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(path)?;
            f.write_all(&bytes)?;
            f.write_all(b"\n")?;
            f.sync_all()
        })();
        if let Err(e) = saved {
            eprintln!("cannot save immutable observation: {e}");
            return 1;
        }
    } else {
        eprintln!("--out is required to archive the observation");
        return 1;
    }
    println!("{}", String::from_utf8(bytes).unwrap());
    i32::from(out["status"] != "VALID_OBSERVATION")
}
