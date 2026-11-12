//! Execute the actual heldout driver through explicit synthetic transport. No device/API calls.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    path::PathBuf,
    process::Command,
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex, MutexGuard,
    },
    time::Instant,
};
static NEXT: AtomicU64 = AtomicU64::new(0);
static CLI_RECEIPT: AtomicU64 = AtomicU64::new(0);
// Each fixture drives real subprocesses, durable writes and device process lifetimes.
// Default libtest concurrency turns protocol regression into host I/O stress.
// Keep concurrency *inside* a fixture (including competing owners) unchanged.
static DRIVER_SLOT: Mutex<()> = Mutex::new(());
fn sha(b: &[u8]) -> String {
    format!("{:x}", Sha256::digest(b))
}
struct F {
    dir: PathBuf,
    started: Instant,
    _slot: Option<MutexGuard<'static, ()>>,
}
impl F {
    fn local() -> Self {
        Self::bare(Instant::now(), None)
    }
    fn bare(started: Instant, slot: Option<MutexGuard<'static, ()>>) -> Self {
        let base = std::env::var_os("PHONEFARM_DRIVER_TEST_ARTIFACT_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(std::env::temp_dir);
        let dir = base.join(format!(
            "confirmation-driver-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(dir.join("receipts")).unwrap();
        // The driver canonicalizes its session. Keep independent producer inputs
        // on the same root (/var and /private/var are aliases on macOS).
        let dir = std::fs::canonicalize(dir).unwrap();
        Self {
            dir,
            started,
            _slot: slot,
        }
    }
    fn new(all: bool) -> Self {
        let queued = Instant::now();
        // A prior failed test must not poison admission for unrelated cases.
        let slot = DRIVER_SLOT.lock().unwrap_or_else(|e| e.into_inner());
        let started = Instant::now();
        let f = Self::bare(started, Some(slot));
        let dir = &f.dir;
        f.write(
            "host-fixture.json",
            &json!({
                "test": std::thread::current().name(),
                "started_at": chrono::Utc::now().to_rfc3339(),
                "admission_wait_ms": started.duration_since(queued).as_millis(),
                "admission": "single_host_driver_fixture",
            }),
        );
        std::fs::write(
            dir.join("fake-adb"),
            include_str!("support/fake_confirmation_adb.py"),
        )
        .unwrap();
        std::fs::write(
            dir.join("render-origin.raw.tsv"),
            include_str!("support/render_origin.synthetic.raw.tsv"),
        )
        .unwrap();
        std::fs::write(
            dir.join("trace_fixture.txt"),
            include_str!("../../loop_v1/fixtures/trace_slice.txt"),
        )
        .unwrap();
        let mut params = vec![(
            "bus.DDR.boost_freq",
            "300",
            "sysfs",
            "/sys/devices/system/cpu/bus_dcvs/DDR/boost_freq",
        )];
        if all {
            params.extend([
                (
                    "bus.LLCC.boost_freq",
                    "300",
                    "sysfs",
                    "/sys/devices/system/cpu/bus_dcvs/LLCC/boost_freq",
                ),
                (
                    "cpu.policy0.scaling_min_freq",
                    "300",
                    "sysfs",
                    "/sys/devices/system/cpu/cpufreq/policy0/scaling_min_freq",
                ),
                (
                    "cpu.policy0.scaling_max_freq",
                    "600",
                    "sysfs",
                    "/sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq",
                ),
                (
                    "cpu.policy0.scaling_governor",
                    "performance",
                    "sysfs",
                    "/sys/devices/system/cpu/cpufreq/policy0/scaling_governor",
                ),
                (
                    "gpu.devfreq.min_freq",
                    "300",
                    "sysfs",
                    "/sys/class/kgsl/kgsl-3d0/devfreq/min_freq",
                ),
                (
                    "gpu.devfreq.max_freq",
                    "600",
                    "sysfs",
                    "/sys/class/kgsl/kgsl-3d0/devfreq/max_freq",
                ),
                (
                    "gpu.devfreq.governor",
                    "performance",
                    "sysfs",
                    "/sys/class/kgsl/kgsl-3d0/devfreq/governor",
                ),
                (
                    "gpu.min_pwrlevel",
                    "3",
                    "sysfs",
                    "/sys/class/kgsl/kgsl-3d0/min_pwrlevel",
                ),
                (
                    "gpu.max_pwrlevel",
                    "1",
                    "sysfs",
                    "/sys/class/kgsl/kgsl-3d0/max_pwrlevel",
                ),
                (
                    "setting.system.peak_refresh_rate",
                    "60",
                    "setting",
                    "system:peak_refresh_rate",
                ),
                (
                    "setting.system.min_refresh_rate",
                    "60",
                    "setting",
                    "system:min_refresh_rate",
                ),
                (
                    "setting.system.refresh_rate_mode",
                    "1",
                    "setting",
                    "system:refresh_rate_mode",
                ),
            ]);
        }
        params.sort_by_key(|(id, _, _, _)| {
            (
                !(id.ends_with("max_freq")
                    || id.ends_with("max_pwrlevel")
                    || id.contains("peak_refresh")),
                id.to_string(),
            )
        });
        let mut values = json!({});
        let mut plan = vec![];
        for (id, val, kind, path) in params {
            values[id] = json!(val);
            plan.push(json!({"kind":kind,"path":path,"value":val}));
        }
        f.write(
            "candidate.json",
            &json!({"kind":"sysparam","payload":{"params":values,"plan":plan,"deny_keywords":[]}}),
        );
        f.write("workload.json",&json!({"schema":"phonefarm/deterministic_touch_workload@1","actions":[{"type":"tap","x":100,"y":100},{"type":"swipe","x1":100,"y1":100,"x2":500,"y2":100,"duration_ms":10},{"type":"sleep","seconds":6}]}));
        f.write("request.json",&json!({"schema":"phonefarm/confirmation_driver_request@1","transport":{"kind":"fake","program":f.dir.join("fake-adb"),"target":"fake-target"},"package":"com.example.game","render_comm":"UnityGfxDeviceW","capture_seconds":2,"lead_seconds":1,"workload":f.dir.join("workload.json"),"candidate":f.dir.join("candidate.json"),"paired_t_assumptions_acknowledged":true,"schedule":(0..5).map(|i|json!({"pair_id":format!("p{i}"),"a_run_id":format!("a{i}"),"b_run_id":format!("b{i}"),"baseline_first":i%2==0})).collect::<Vec<_>>()}));
        f
    }
    fn write(&self, n: &str, v: &Value) {
        std::fs::write(self.dir.join(n), serde_json::to_vec_pretty(v).unwrap()).unwrap();
    }
    fn read(&self, n: &str) -> Value {
        serde_json::from_slice(&std::fs::read(self.dir.join(n)).unwrap()).unwrap()
    }
    fn receipt(&self) -> Value {
        std::fs::read_dir(self.dir.join("receipts")).unwrap()
            .map(|p| serde_json::from_slice::<Value>(&std::fs::read(p.unwrap().path()).unwrap()).unwrap())
            .max_by_key(|v| v["updated_at"].as_str().unwrap().to_owned()).unwrap()
    }
    fn assert_released(&self) {
        let output = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
            .args(["lease", "--serial", "fake-target", "--timeout-ms", "0", "--", "/usr/bin/true"])
            .env("PHONEFARM_LEASE_DIR", self.dir.join("lease"))
            .env("DEVLOCK_DIR", self.dir.join("legacy"))
            .output().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    }
    fn assert_recovery_required(&self) {
        let receipt = self.receipt();
        assert_eq!(receipt["state"], "released", "{receipt}");
        assert_eq!(receipt["recovery"]["status"], "required", "{receipt}");
        self.assert_released();
    }
    fn command(&self, args: &[&str], fail: &str) -> Command {
        let mut c = Command::new(env!("CARGO_BIN_EXE_phonefarm"));
        c.arg("confirmation-driver")
            .args(args)
            .current_dir(&self.dir)
            .env("PHONEFARM_FAKE_DEVICE_DIR", &self.dir)
            .env("PHONEFARM_LEASE_DIR", self.dir.join("lease"))
            .env("DEVLOCK_DIR", self.dir.join("legacy"))
            .env("PHONEFARM_LEASE_TIMEOUT_MS", "100")
            .env("PHONEFARM_FAKE_FAIL", fail)
            .env("PF_JOB_ID", "0123456789ab")
            .env("PF_JOB_LEASE_DIR", self.dir.join("receipts"));
        c
    }
    fn call(&self, args: &[&str], fail: &str) -> (i32, Value) {
        let mut command = self.command(args, fail);
        let o = self.output(args[0], &mut command);
        let v = serde_json::from_slice(&o.stdout).unwrap_or_else(|_| {
            panic!(
                "{} {}",
                String::from_utf8_lossy(&o.stdout),
                String::from_utf8_lossy(&o.stderr)
            )
        });
        (o.status.code().unwrap_or(-1), v)
    }
    fn output(&self, label: &str, command: &mut Command) -> std::process::Output {
        let prefix = format!(
            "host-cli-{}-{label}",
            CLI_RECEIPT.fetch_add(1, Ordering::Relaxed)
        );
        let argv = std::iter::once(command.get_program())
            .chain(command.get_args())
            .map(|v| v.to_string_lossy().into_owned())
            .collect::<Vec<_>>();
        let environment = command
            .get_envs()
            .map(|(k, v)| {
                (
                    k.to_string_lossy().into_owned(),
                    v.map(|s| s.to_string_lossy().into_owned()),
                )
            })
            .collect::<std::collections::BTreeMap<_, _>>();
        let mut receipt = json!({"argv":argv,
            "cwd":command.get_current_dir(),"environment_overrides":environment,
            "started_at":chrono::Utc::now().to_rfc3339(),"state":"started"});
        self.write(&format!("{prefix}.exit.json"), &receipt);
        let start = Instant::now();
        let result = command.output();
        receipt["elapsed_ms"] = json!(start.elapsed().as_millis());
        receipt["finished_at"] = json!(chrono::Utc::now().to_rfc3339());
        receipt["state"] = json!("finished");
        match &result {
            Ok(output) => {
                std::fs::write(self.dir.join(format!("{prefix}.stdout")), &output.stdout).unwrap();
                std::fs::write(self.dir.join(format!("{prefix}.stderr")), &output.stderr).unwrap();
                receipt["exit"] = json!(output.status.code());
            }
            Err(error) => receipt["spawn_error"] = json!(error.to_string()),
        }
        self.write(&format!("{prefix}.exit.json"), &receipt);
        result.unwrap()
    }
    fn prepare(&self, fail: &str) -> (i32, Value) {
        self.call(
            &["prepare", "--request", "request.json", "--out", "session"],
            fail,
        )
    }
    fn freeze(&self) {
        // Exact typed order is supplied by actual GOL when env points to it; portable fallback mirrors its contract.
        if let Some(bin) = std::env::var_os("PHONEFARM_DRIVER_GOL_BIN") {
            let mut command = Command::new(bin);
            command
                .args(["confirm", "freeze", "--plan"])
                .arg(self.dir.join("session/plan.json"))
                .arg("--out")
                .arg(self.dir.join("frozen.json"));
            let o = self.output("gol-freeze", &mut command);
            assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
            return;
        }
        let p = self.read("session/plan.json");
        let registered = chrono::Utc::now().to_rfc3339();
        let names = [
            "baseline_artifact_sha256",
            "candidate_artifact_sha256",
            "conditions_sha256",
            "baseline_admission_sha256",
            "exploration_run_ids",
            "exploration_artifact_sha256",
            "pairs",
            "schedule",
            "family_alpha",
            "paired_t_assumptions_acknowledged",
            "metrics",
        ];
        let plantext = format!(
            "{{{}}}",
            names
                .iter()
                .map(|n| format!("{}:{}", serde_json::to_string(n).unwrap(), p[*n]))
                .collect::<Vec<_>>()
                .join(",")
        );
        // Nested structs also have a fixed field order.
        let schedule = p["schedule"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| {
                format!(
                    "{{\"pair_id\":{},\"a_run_id\":{},\"b_run_id\":{},\"baseline_first\":{}}}",
                    s["pair_id"], s["a_run_id"], s["b_run_id"], s["baseline_first"]
                )
            })
            .collect::<Vec<_>>()
            .join(",");
        let metrics=p["metrics"].as_array().unwrap().iter().map(|m|format!("{{\"name\":{},\"direction\":{},\"scale\":{},\"minimum_improvement\":{},\"maximum_regression\":{}}}",m["name"],m["direction"],m["scale"],m["minimum_improvement"],m["maximum_regression"])).collect::<Vec<_>>().join(",");
        let mut plantext = plantext
            .replace(&p["schedule"].to_string(), &format!("[{schedule}]"))
            .replace(&p["metrics"].to_string(), &format!("[{metrics}]"));
        if let Some(tc) = p.get("temperature_contract") {
            let names = [
                "profile_id",
                "receiver_policy_version",
                "profile_sha256",
                "reference_sha256",
                "thermal_contract_sha256",
            ];
            let text = format!(
                "{{{}}}",
                names
                    .iter()
                    .map(|n| format!("{}:{}", serde_json::to_string(n).unwrap(), tc[*n]))
                    .collect::<Vec<_>>()
                    .join(",")
            );
            plantext.pop();
            plantext += &format!(",\"temperature_contract\":{text}}}");
        }
        let hash = sha(format!(
            "[\"game_opt_loop/confirmation_plan@1\",{},{}]",
            serde_json::to_string(&registered).unwrap(),
            plantext
        )
        .as_bytes());
        self.write("frozen.json",&json!({"schema":"game_opt_loop/confirmation_plan@1","registered_at":registered,"plan":p,"sha256":hash}));
    }
    fn collect(&self) -> (i32, Value) {
        self.call(
            &["collect", "--session", "session", "--frozen", "frozen.json"],
            "",
        )
    }
    fn delayed_old_worker(&self) -> std::process::Output {
        let log = std::fs::read_to_string(self.dir.join("argv.jsonl")).unwrap();
        let old: Value = log
            .lines()
            .map(|l| serde_json::from_str::<Value>(l).unwrap())
            .find(|v| {
                v["command"].as_str().unwrap().contains("/worker.sh ")
                    && v["command"].as_str().unwrap().contains("'probe'")
            })
            .unwrap();
        Command::new("python3")
            .arg(self.dir.join("fake-adb"))
            .args(
                old["argv"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_str().unwrap()),
            )
            .env("PHONEFARM_FAKE_DEVICE_DIR", &self.dir)
            .env("PHONEFARM_FAKE_FAIL", "")
            .output()
            .unwrap()
    }
}
impl Drop for F {
    fn drop(&mut self) {
        let failed = std::thread::panicking();
        // Do not erase the command journal needed to diagnose a failed assertion.
        // Avoid panicking again if the original failure was a filesystem error.
        let receipt = json!({"test":std::thread::current().name(),
            "failed":failed,"elapsed_ms":self.started.elapsed().as_millis(),
            "finished_at":chrono::Utc::now().to_rfc3339()});
        let _ = std::fs::write(
            self.dir.join("host-fixture-finish.json"),
            receipt.to_string(),
        );
        if failed {
            eprintln!(
                "confirmation driver failure evidence retained: {}",
                self.dir.display()
            );
        } else if std::env::var_os("PHONEFARM_DRIVER_TEST_ARTIFACT_DIR").is_none() {
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }
}
#[test]
fn complete_actual_driver_all_control_categories_retains_synthetic_and_cannot_replay() {
    let f = F::new(true);
    let (code, v) = f.prepare("");
    assert_eq!(code, 0, "{v}");
    assert_eq!(v["status"], "SIMULATED_BLOCKED");
    assert!(!f.dir.join("legacy/devlock.d").exists());
    let cmd = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
    assert!(cmd.contains("setsid sh"));
    assert_eq!(
        std::fs::read_to_string(f.dir.join("session/probe.txt"))
            .unwrap()
            .matches("effect_restored=yes")
            .count(),
        13
    );
    for i in 0..3 {
        let r = f.read(&format!("session/admission_{i}-run.json"));
        assert_eq!(r["evidence_origin"], "synthetic");
        let validation = f.read(&format!("session/admission_{i}-validation.json"));
        assert_eq!(validation["valid"], false);
        assert!(validation["gates"]
            .as_object()
            .unwrap()
            .values()
            .all(|v| *v == true));
    }
    // Changing original caller files after prepare cannot alter the frozen workload/candidate execution.
    f.write("candidate.json", &json!({"mutated":true}));
    f.write("workload.json", &json!({"mutated":true}));
    f.freeze();
    // A self-consistent but incomplete hash map must not bypass frozen remote-tool coverage.
    let original_inputs = std::fs::read(f.dir.join("session/inputs.manifest.json")).unwrap();
    let original_prepared = std::fs::read(f.dir.join("session/prepared.manifest.json")).unwrap();
    let original_argv = std::fs::read(f.dir.join("argv.jsonl")).unwrap();
    let mut inputs = f.read("session/inputs.manifest.json");
    inputs["hashes"]
        .as_object_mut()
        .unwrap()
        .remove("worker.sh");
    f.write("session/inputs.manifest.json", &inputs);
    let mut prepared = f.read("session/prepared.manifest.json");
    prepared["hashes"]["inputs.manifest.json"] = json!(sha(&std::fs::read(
        f.dir.join("session/inputs.manifest.json")
    )
    .unwrap()));
    f.write("session/prepared.manifest.json", &prepared);
    let (code, v) = f.collect();
    assert_eq!(code, 1, "{v}");
    assert_eq!(
        std::fs::read(f.dir.join("argv.jsonl")).unwrap(),
        original_argv
    );
    std::fs::write(f.dir.join("session/inputs.manifest.json"), &original_inputs).unwrap();
    let mut prepared: Value = serde_json::from_slice(&original_prepared).unwrap();
    prepared["hashes"]
        .as_object_mut()
        .unwrap()
        .remove("admission_0-trace.txt");
    f.write("session/prepared.manifest.json", &prepared);
    let (code, v) = f.collect();
    assert_eq!(code, 1, "{v}");
    assert_eq!(
        std::fs::read(f.dir.join("argv.jsonl")).unwrap(),
        original_argv
    );
    std::fs::write(
        f.dir.join("session/prepared.manifest.json"),
        original_prepared,
    )
    .unwrap();
    let (code, v) = f.collect();
    assert_eq!(code, 0, "{v}");
    assert_eq!(v["status"], "SIMULATED_BLOCKED");
    let s = f.read("session/samples.json");
    assert_eq!(s["source_kind"], "synthetic_fixture");
    assert_eq!(s["pairs"].as_array().unwrap().len(), 5);
    for p in s["pairs"].as_array().unwrap() {
        for side in ["a", "b"] {
            let r = f.read(&format!(
                "session/{}",
                p[format!("{side}_report")].as_str().unwrap()
            ));
            assert_eq!(r["status"], "BLOCKED");
            assert_eq!(r["qualification"]["eligible"], false);
            assert_eq!(r["real_game_optimization_proven"], false);
            assert_eq!(r["promotion_allowed"], false);
            let id = p[format!("{side}_run_id")].as_str().unwrap();
            let o = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
                .args(["confirmation-observation", "--bundle"])
                .arg(f.dir.join(format!("session/{id}-bundle.json")))
                .arg("--out")
                .arg(f.dir.join(format!("{id}-reconstructed.json")))
                .output()
                .unwrap();
            assert!(
                !o.status.success(),
                "synthetic raw producer cannot become qualified"
            );
            assert_eq!(f.read(&format!("{id}-reconstructed.json")), r);
        }
    }
    if let Some(bin) = std::env::var_os("PHONEFARM_DRIVER_GOL_BIN") {
        let o = Command::new(bin)
            .args(["confirm", "judge", "--frozen"])
            .arg(f.dir.join("frozen.json"))
            .arg("--samples")
            .arg(f.dir.join("session/samples.json"))
            .arg("--out")
            .arg(f.dir.join("gol-report.json"))
            .output()
            .unwrap();
        std::fs::write(f.dir.join("gol-judge.stdout"), &o.stdout).unwrap();
        std::fs::write(f.dir.join("gol-judge.stderr"), &o.stderr).unwrap();
        f.write("gol-judge.exit.json", &json!({"exit":o.status.code()}));
        assert!(!o.status.success(), "synthetic confirmation must not PASS");
        let r = f.read("gol-report.json");
        assert_eq!(r["verdict"], "BLOCKED");
        assert_eq!(r["claim_scope"], "none");
        assert_eq!(r["real_game_optimization_proven"], false);
        assert_eq!(r["promotion_allowed"], false);
        assert!(r["report_hashes"].as_object().unwrap().len() == 10, "{r}");
    }
    let (code, v) = f.collect();
    assert_eq!(code, 1);
    assert!(v["error"].as_str().unwrap().contains("already started"));
}
#[test]
fn real_validation_without_execute_invokes_no_transport() {
    let f = F::new(false);
    let mut r = f.read("request.json");
    r["transport"]["kind"] = json!("adb");
    f.write("request.json", &r);
    let (c, v) = f.prepare("");
    assert_eq!(c, 0, "{v}");
    assert_eq!(v["status"], "VALIDATED_ONLY");
    assert!(!f.dir.join("argv.jsonl").exists());
    assert!(!f.dir.join("session").exists());
}
#[test]
fn unknown_root_blocks_before_any_write() {
    let f = F::new(false);
    let (c, v) = f.prepare("root");
    assert_eq!(c, 1, "{v}");
    let s = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
    assert!(!s.contains("mkdir"));
    assert!(!f.dir.join("legacy/devlock.d").exists());
}
#[test]
fn every_failed_raw_gate_stops_and_preserves_uncertainty() {
    for fail in [
        "probe", "cold", "workload", "env", "restore", "active", "power",
    ] {
        let f = F::new(false);
        let (c, v) = f.prepare(fail);
        assert_eq!(c, 1, "{fail}: {v}");
        f.assert_recovery_required();
        assert!(!f.dir.join("session/prepared.manifest.json").exists());
        let (c, _) = f.call(
            &["prepare", "--request", "request.json", "--out", "another"],
            "",
        );
        assert_eq!(c, 0, "a failed prior run must not leave a lock barrier: {fail}");
    }
}
#[test]
fn explicit_reconcile_never_validates_failed_old_sample() {
    let f = F::new(false);
    assert_eq!(f.prepare("workload").0, 1);
    // A crashed game must not prevent restoring positively owned control state.
    let (c, v) = f.call(
        &["reconcile", "--session", "session", "--execute"],
        "foreground",
    );
    assert_eq!(c, 0, "{v}");
    assert_eq!(v["old_samples_revalidated"], false);
    assert!(!f.dir.join("legacy/devlock.d").exists());
    assert!(!f.dir.join("session/prepared.manifest.json").exists());
    let state = std::fs::read(f.dir.join("fake-state.json")).unwrap();
    assert_eq!(f.delayed_old_worker().status.code(), Some(86));
    assert_eq!(std::fs::read(f.dir.join("fake-state.json")).unwrap(), state);
}
#[test]
fn uncertain_session_closure_keeps_failed_result_and_delayed_command_revoked() {
    let f = F::new(false);
    assert_eq!(f.prepare("probe").0, 1);
    let (c, v) = f.call(
        &["reconcile", "--session", "session", "--execute"],
        "closure_unknown",
    );
    assert_eq!(c, 1, "{v}");
    f.assert_recovery_required();
    assert_eq!(f.delayed_old_worker().status.code(), Some(86));
    // A missing closure receipt cannot be filled by replaying recovery on a tombstoned session.
    assert_eq!(
        f.call(&["reconcile", "--session", "session", "--execute"], "")
            .0,
        1
    );
    f.assert_recovery_required();
}
#[test]
fn retired_pending_record_does_not_block_real_reconciliation() {
    let f = F::new(false);
    assert_eq!(f.prepare("probe").0, 1);
    let marker = f.dir.join("legacy/devlock.d/phonefarm-lease.json");
    std::fs::create_dir_all(marker.parent().unwrap()).unwrap();
    let old = json!({"command_pending":true,"pending_operation":"unbound-before-spawn","recovery_required":true});
    f.write("legacy/devlock.d/phonefarm-lease.json", &old);
    let (c, v) = f.call(&["reconcile", "--session", "session", "--execute"], "");
    assert_eq!(c, 0, "{v}");
    assert_eq!(v["status"], "SIMULATED_RECONCILED");
    assert_eq!(v["old_samples_revalidated"], false);
    assert_eq!(f.read("legacy/devlock.d/phonefarm-lease.json"), old);
    f.assert_released();
}
#[test]
fn local_parent_exit_with_lingering_closed_pipe_child_is_not_success() {
    let f = F::new(false);
    let (c, v) = f.prepare("closed_pipes_child");
    assert_eq!(c, 1);
    assert!(
        v["error"].as_str().unwrap().contains("process group"),
        "{v}"
    );
}
#[test]
fn boot_changed_reconcile_preserves_recovery_requirement() {
    let f = F::new(false);
    assert_eq!(f.prepare("probe").0, 1);
    let (c, v) = f.call(&["reconcile", "--session", "session", "--execute"], "boot");
    assert_eq!(c, 1, "{v}");
    f.assert_recovery_required();
}
#[test]
fn per_device_lease_excludes_other_cwd_and_releases_completed_command() {
    use std::time::{Duration, Instant};
    let f = F::new(false);
    let started = f.dir.join("lease-started");
    let done = f.dir.join("lease-done");
    let script = "echo start > \"$1\"; while [ ! -f \"$2\" ]; do sleep .05; done";
    let mut a = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
        .args([
            "lease",
            "--serial",
            "fake-target",
            "--timeout-ms",
            "100",
            "--",
            "/bin/sh",
            "-c",
            script,
            "lease-test",
        ])
        .arg(&started)
        .arg(&done)
        .current_dir(&f.dir)
        .env("PHONEFARM_LEASE_DIR", f.dir.join("lease"))
        .env("DEVLOCK_DIR", f.dir.join("legacy"))
        .spawn()
        .unwrap();
    let until = Instant::now() + Duration::from_secs(5);
    while !started.exists() {
        assert!(a.try_wait().unwrap().is_none());
        assert!(Instant::now() < until);
        std::thread::sleep(Duration::from_millis(20));
    }
    let competing = || {
        Command::new(env!("CARGO_BIN_EXE_phonefarm"))
            .args([
                "lease",
                "--serial",
                "fake-target",
                "--timeout-ms",
                "100",
                "--",
                "/bin/sh",
                "-c",
                "exit 0",
            ])
            .current_dir(f.dir.parent().unwrap())
            .env("PHONEFARM_LEASE_DIR", f.dir.join("lease"))
            .env("DEVLOCK_DIR", f.dir.join("legacy"))
            .output()
            .unwrap()
    };
    assert!(!competing().status.success());
    std::fs::write(&done, "done").unwrap();
    let until = Instant::now() + Duration::from_secs(5);
    loop {
        if let Some(status) = a.try_wait().unwrap() {
            assert!(status.success());
            break;
        }
        assert!(Instant::now() < until);
        std::thread::sleep(Duration::from_millis(20));
    }
    assert!(!f.dir.join("legacy/devlock.d").exists());
    assert!(competing().status.success());
    assert!(!f.dir.join("legacy/devlock.d").exists());
}

fn control_request(f: &F, transport_kind: &str) {
    f.write(
        "control-request.json",
        &json!({"schema":"phonefarm/control_probe_request@1",
        "transport":{"kind":transport_kind,"program":f.dir.join("fake-adb"),"target":"fake-target"},
        "candidate":f.dir.join("candidate.json")}),
    );
}
#[test]
fn capability_only_endpoint_has_no_game_inputs_no_sampling_and_closes_fake_session() {
    let f = F::new(false);
    control_request(&f, "fake");
    let (code, v) = f.call(
        &[
            "probe-controls",
            "--request",
            "control-request.json",
            "--out",
            "control-session",
        ],
        "",
    );
    assert_eq!(code, 0, "{v}");
    assert_eq!(v["status"], "SIMULATED_CONTROL_RESTORED");
    assert_eq!(v["evidence_origin"], "synthetic");
    for key in [
        "eligible",
        "experiment_eligible",
        "real_game_optimization_proven",
        "promotion_allowed",
        "actual_clock_sustained_proven",
        "power_window_verified",
    ] {
        assert_eq!(v[key], false, "{key}");
    }
    for n in [
        "plan.json",
        "baseline.json",
        "samples.json",
        "workload.json",
        "conditions.json",
    ] {
        assert!(!f.dir.join("control-session").join(n).exists(), "{n}");
    }
    let argv = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
    assert!(!argv.contains("dumpsys activity activities"));
    assert!(!argv.contains(" sample "));
    assert!(argv.contains("control_probe"));
    assert!(argv.contains("reconcile_controls"));
    assert!(!f.dir.join("legacy/devlock.d").exists());
    let (code, _) = f.call(
        &[
            "collect",
            "--session",
            "control-session",
            "--frozen",
            "missing.json",
        ],
        "",
    );
    assert_eq!(code, 1); // Capability sessions cannot be upgraded into confirmation collection.
}
#[test]
fn capability_adb_without_execute_and_extra_game_fields_do_not_call_transport() {
    let f = F::new(false);
    control_request(&f, "adb");
    let (code, v) = f.call(
        &[
            "probe-controls",
            "--request",
            "control-request.json",
            "--out",
            "never-created",
        ],
        "",
    );
    assert_eq!(code, 0);
    assert_eq!(v["status"], "VALIDATED_ONLY");
    assert!(!f.dir.join("never-created").exists());
    assert!(!f.dir.join("argv.jsonl").exists());
    let mut req = f.read("control-request.json");
    req["package"] = json!("com.android.launcher");
    f.write("control-request.json", &req);
    let (code, _) = f.call(
        &[
            "probe-controls",
            "--request",
            "control-request.json",
            "--out",
            "never-created",
            "--execute",
        ],
        "",
    );
    assert_eq!(code, 1);
    assert!(!f.dir.join("argv.jsonl").exists());
}
#[test]
fn capability_root_hot_gate_precedes_install_and_failure_keeps_recovery() {
    for failure in ["root", "cold", "probe", "restore", "closure_unknown"] {
        let f = F::new(false);
        control_request(&f, "fake");
        let (code, v) = f.call(
            &[
                "probe-controls",
                "--request",
                "control-request.json",
                "--out",
                "control-session",
            ],
            failure,
        );
        assert_eq!(code, 1, "{failure}: {v}");
        assert_eq!(v["eligible"], false);
        let argv = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
        if failure == "root" || failure == "cold" {
            assert!(!argv.contains("#phonefarm_fixed_tools_install v1"));
            assert!(!f.dir.join("legacy/devlock.d").exists());
        } else {
            f.assert_recovery_required();
            if failure == "probe" {
                let (code, v) = f.call(
                    &["reconcile", "--session", "control-session", "--execute"],
                    "",
                );
                assert_eq!(code, 0, "{v}");
                assert_eq!(v["status"], "SIMULATED_RECONCILED");
                assert!(!f.dir.join("legacy/devlock.d").exists());
            }
        }
    }
}
#[test]
fn capability_missing_frozen_tool_cannot_reconcile() {
    let f = F::new(false);
    control_request(&f, "fake");
    assert_eq!(
        f.call(
            &[
                "probe-controls",
                "--request",
                "control-request.json",
                "--out",
                "control-session"
            ],
            "probe"
        )
        .0,
        1
    );
    let manifest_path = f.dir.join("control-session/inputs.manifest.json");
    let mut manifest: Value =
        serde_json::from_slice(&std::fs::read(&manifest_path).unwrap()).unwrap();
    manifest["hashes"]
        .as_object_mut()
        .unwrap()
        .remove("worker.sh");
    std::fs::write(
        &manifest_path,
        serde_json::to_vec_pretty(&manifest).unwrap(),
    )
    .unwrap();
    let before = std::fs::read(f.dir.join("control-session/state.json")).unwrap();
    let (code, v) = f.call(
        &["reconcile", "--session", "control-session", "--execute"],
        "",
    );
    assert_eq!(code, 1, "{v}");
    assert!(v["error"].as_str().unwrap().contains("manifest"));
    assert_eq!(
        std::fs::read(f.dir.join("control-session/state.json")).unwrap(),
        before
    );
}

#[test]
fn loaded_capability_uses_typed_warmup_at_80c_still_never_experiment_qualified() {
    let f = F::new(false);
    control_request(&f, "fake");
    let mut req = f.read("control-request.json");
    req["temperature_profile_id"] = json!("loaded_steady_v1");
    f.write("control-request.json", &req);
    let (code, v) = f.call(
        &[
            "probe-controls",
            "--request",
            "control-request.json",
            "--out",
            "loaded",
        ],
        "",
    );
    assert_eq!(code, 0, "{v}");
    assert_eq!(v["temperature_profile_id"], "loaded_steady_v1");
    assert_eq!(v["eligible"], false);
    assert_eq!(v["evidence_origin"], "synthetic");
    assert_eq!(v["temperature"]["assessment"]["compliant"], true);
    let profile = f.read("loaded/thermal_profile.json");
    let cpu = profile["sensors"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["domain"] == "cpu")
        .unwrap();
    assert_eq!(cpu["operating_cap_c"], 90.0);
    let argv = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
    assert!(!argv.contains("dumpsys activity activities"));
    assert!(!f.dir.join("loaded/plan.json").exists());
    assert!(!f.dir.join("legacy/devlock.d").exists());
}
#[test]
fn loaded_capability_missing_typed_sensor_and_severe_hal_cannot_install_or_write() {
    for fail in ["thermal_missing", "thermal_severe"] {
        let f = F::new(false);
        control_request(&f, "fake");
        let mut req = f.read("control-request.json");
        req["temperature_profile_id"] = json!("loaded_steady_v1");
        f.write("control-request.json", &req);
        let (code, v) = f.call(
            &[
                "probe-controls",
                "--request",
                "control-request.json",
                "--out",
                "loaded",
            ],
            fail,
        );
        assert_eq!(code, 1, "{fail}: {v}");
        assert!(!f.dir.join("legacy/devlock.d").exists());
        let argv = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
        assert!(!argv.contains("#phonefarm_fixed_tools_install v1"));
    }
}

#[test]
fn loaded_prepare_freezes_real_raw_contract_and_three_independent_windows_but_fixture_is_blocked() {
    let f = F::new(false);
    let mut req = f.read("request.json");
    req["temperature_profile_id"] = json!("loaded_steady_v1");
    f.write("request.json", &req);
    let (code, v) = f.prepare("");
    assert_eq!(code, 0, "{v}");
    assert_eq!(v["status"], "SIMULATED_BLOCKED");
    let c = f.read("session/conditions.json");
    assert_eq!(c["schema"], "phonefarm/confirmation_conditions@2");
    assert_eq!(c["policy"]["version"], 2);
    assert!(c["policy"]["cool_c"].is_null());
    assert!(c["policy"]["temp_cap_c"].is_null());
    for i in 0..3 {
        let raw = f.read(&format!("session/admission_{i}-run.json"));
        assert_eq!(raw["schema"], "phonefarm/run_one_bundle@2");
        assert!(raw["raw"].get("cold").is_none());
        assert!(raw["raw"]["thermal_admission"].is_object());
        assert!(raw["raw"]["thermal_window"].is_object());
    }
    let plan = f.read("session/plan.json");
    assert_eq!(plan["temperature_contract"]["receiver_policy_version"], 2);
    assert_eq!(
        plan["exploration_artifact_sha256"]
            .as_array()
            .unwrap()
            .len(),
        12
    );
    assert!(!f.dir.join("legacy/devlock.d").exists());
}
#[test]
fn loaded_whole_thermal_failure_keeps_failed_result_without_sampling_further_baselines() {
    for fail in [
        "thermal_window_short",
        "thermal_window_hot",
        "thermal_sampler_exit",
    ] {
        let f = F::new(false);
        let mut req = f.read("request.json");
        req["temperature_profile_id"] = json!("loaded_steady_v1");
        f.write("request.json", &req);
        let (code, v) = f.prepare(fail);
        assert_eq!(code, 1, "{fail}:{v}");
        f.assert_recovery_required();
        assert!(!f.dir.join("session/admission_1-run.json").exists());
        assert!(!f.dir.join("session/plan.json").exists());
    }
}

#[test]
fn loaded_cadence_fixed_tools_prepare_freezes_metrics_and_freshly_rechecks_each_baseline() {
    let f = F::new(false);
    let mut req = f.read("request.json");
    req["temperature_profile_id"] = json!("loaded_steady_v1");
    req["goal"] = json!("render_cadence");
    f.write("request.json", &req);
    let (code, v) = f.prepare("power");
    assert_eq!(code, 0, "{v}");
    assert_eq!(v["status"], "SIMULATED_BLOCKED");
    assert_eq!(v["eligible"], false);
    let c = f.read("session/conditions.json");
    assert_eq!(c["policy"]["goal"], "render_cadence");
    assert_eq!(
        c["policy"]["required_metrics"],
        json!(["frame_p95", "fps_mean"])
    );
    assert_eq!(
        f.read("session/plan.json")["metrics"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert!(!f.dir.join("legacy/devlock.d").exists());

    let manifest = f.read("session/tools.manifest.json");
    assert_eq!(manifest["count"], 14);
    assert_eq!(manifest["files"].as_array().unwrap().len(), 14);
    let raw =
        std::fs::read_to_string(f.dir.join("session/prepare-commands/journal.jsonl")).unwrap();
    let events = raw
        .lines()
        .map(|s| serde_json::from_str::<Value>(s).unwrap())
        .collect::<Vec<_>>();
    let intents = events
        .iter()
        .filter(|v| v["event"] == "command_intent")
        .collect::<Vec<_>>();
    assert_eq!(
        intents
            .iter()
            .filter(|v| v["operation"] == "install_fixed_tools")
            .count(),
        1
    );
    // One full acceptance after install; each of the three baselines independently re-verifies actual files.
    assert_eq!(
        intents
            .iter()
            .filter(|v| v["operation"] == "verify_fixed_tools")
            .count(),
        4
    );
    assert!(!intents.iter().any(|v| matches!(
        v["operation"].as_str(),
        Some("install_file" | "install_owner" | "verify_remote_file")
    )));
    let remote = f.read("fake-state.json");
    let d = f.read("session/state.json")["remote"]
        .as_str()
        .unwrap()
        .to_owned();
    for file in manifest["files"].as_array().unwrap() {
        let name = file["name"].as_str().unwrap();
        let bytes = remote["files"][format!("{d}/{name}")]
            .as_str()
            .unwrap()
            .chars()
            .map(|c| u8::try_from(u32::from(c)).expect("fake remote Latin-1 byte code point"))
            .collect::<Vec<_>>();
        assert_eq!(bytes, std::fs::read(f.dir.join("session").join(name)).unwrap(), "{name}");
        assert_eq!(file["sha256"], sha(&bytes));
        assert_eq!(file["bytes"], bytes.len());
    }
    for i in 0..3 {
        assert!(f
            .dir
            .join(format!("session/admission_{i}-run.json"))
            .exists());
    }
}
#[test]
fn unsupported_display_goal_rejects_before_transport() {
    let f = F::new(false);
    let mut req = f.read("request.json");
    req["goal"] = json!("display_smoothness");
    f.write("request.json", &req);
    let (code, _) = f.prepare("");
    assert_eq!(code, 1);
    assert!(!f.dir.join("argv.jsonl").exists());
}

#[test]
fn loaded_cadence_driver_freeze_ten_arms_and_actual_judge_cannot_promote_synthetic() {
    let f = F::new(false);
    let mut req = f.read("request.json");
    req["temperature_profile_id"] = json!("loaded_steady_v1");
    req["goal"] = json!("render_cadence");
    f.write("request.json", &req);
    assert_eq!(f.prepare("").0, 0);
    f.freeze();
    let prepared_bytes = std::fs::read(f.dir.join("session/prepared.manifest.json")).unwrap();
    let mut prepared = f.read("session/prepared.manifest.json");
    assert!(prepared["hashes"]
        .get("pre-probe_0-thermal.raw.tsv")
        .is_some());
    prepared["hashes"]
        .as_object_mut()
        .unwrap()
        .remove("pre-probe_0-thermal.raw.tsv");
    f.write("session/prepared.manifest.json", &prepared);
    let (code, bad) = f.collect();
    assert_eq!(code, 1, "{bad}");
    assert!(bad["error"]
        .as_str()
        .unwrap()
        .contains("prepared evidence manifest"));
    assert!(!f.dir.join("session/collection.started.json").exists());
    std::fs::write(f.dir.join("session/prepared.manifest.json"), prepared_bytes).unwrap();
    let (code, v) = f.collect();
    assert_eq!(code, 0, "{v}");
    assert_eq!(v["status"], "SIMULATED_BLOCKED");
    let samples = f.read("session/samples.json");
    let mut seen = std::collections::BTreeSet::new();
    for pair in samples["pairs"].as_array().unwrap() {
        for side in ["a", "b"] {
            let report = f.read(&format!(
                "session/{}",
                pair[format!("{side}_report")].as_str().unwrap()
            ));
            assert_eq!(report["schema"], "phonefarm/confirmation_observation@2");
            assert_eq!(report["status"], "BLOCKED");
            assert_eq!(report["qualification"]["version"], 2);
            assert_eq!(report["qualification"]["eligible"], false);
            assert_eq!(report["qualification"]["evidence_origin"], "synthetic");
            assert_eq!(report["qualification"]["policy"]["version"], 2);
            assert_eq!(report["sample_qualification"]["valid_observation"], false);
            assert_eq!(
                report["sample_qualification"]["gates"]["baseline_admission_verified"],
                false
            );
            assert!(report["issues"].as_array().unwrap().iter().any(|v| v
                .as_str()
                .unwrap_or("")
                .contains("baseline admission arm not independently valid")));
            assert_eq!(
                report["confirmation_binding"]["thermal_contract"]["profile_id"],
                "loaded_steady_v1"
            );
            for key in ["thermal_admission_sha256", "thermal_window_sha256"] {
                assert!(seen.insert(
                    report["confirmation_binding"]["raw_artifacts"][key]
                        .as_str()
                        .unwrap()
                        .to_owned()
                ));
            }
        }
    }
    assert_eq!(seen.len(), 20);
    if let Some(bin) = std::env::var_os("PHONEFARM_DRIVER_GOL_BIN") {
        let mut cmd = Command::new(bin);
        cmd.args(["confirm", "judge", "--frozen"])
            .arg(f.dir.join("frozen.json"))
            .arg("--samples")
            .arg(f.dir.join("session/samples.json"))
            .arg("--out")
            .arg(f.dir.join("loaded-judge.json"));
        let out = f.output("loaded-gol-judge", &mut cmd);
        assert!(!out.status.success());
        let r = f.read("loaded-judge.json");
        assert_eq!(r["verdict"], "BLOCKED");
        assert_eq!(r["promotion_allowed"], false);
        assert_eq!(r["real_game_optimization_proven"], false);
        assert_eq!(r["report_hashes"].as_object().unwrap().len(), 10);
    }
}

#[test]
fn loaded_restore_target_is_post_warmup_state_before_remote_writes_for_probe_and_prepare() {
    for capability in [true, false] {
        let f = F::new(false);
        let dir = if capability { "loaded" } else { "session" };
        let (code, v) = if capability {
            control_request(&f, "fake");
            let mut req = f.read("control-request.json");
            req["temperature_profile_id"] = json!("loaded_steady_v1");
            f.write("control-request.json", &req);
            f.call(
                &[
                    "probe-controls",
                    "--request",
                    "control-request.json",
                    "--out",
                    dir,
                ],
                "loaded_cpu_drift",
            )
        } else {
            let mut req = f.read("request.json");
            req["temperature_profile_id"] = json!("loaded_steady_v1");
            req["goal"] = json!("render_cadence");
            f.write("request.json", &req);
            f.prepare("loaded_cpu_drift")
        };
        assert_eq!(code, 0, "capability={capability}: {v}");
        let device = f.read("fake-state.json");
        assert!(device["snapshot_before_warmup"]
            .as_str()
            .unwrap()
            .contains("cpu.policy0.scaling_max=1000"));
        let initial = std::fs::read_to_string(f.dir.join(dir).join("initial.snapshot")).unwrap();
        assert!(initial.contains("cpu.policy0.scaling_max=600"));
        let fields = |raw: &str| {
            raw.lines()
                .filter(|line| !line.starts_with('#'))
                .map(String::from)
                .collect::<std::collections::BTreeSet<_>>()
        };
        assert_eq!(
            fields(&initial),
            fields(device["snapshot"].as_str().unwrap())
        );
        let argv = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
        let commands: Vec<Value> = argv
            .lines()
            .map(|s| serde_json::from_str(s).unwrap())
            .collect();
        let warmup = commands
            .iter()
            .position(|v| v["stdin"].as_str().unwrap().matches("sh -c").count() >= 6)
            .unwrap();
        let snapshot = commands
            .iter()
            .position(|v| v["stdin"].as_str().unwrap().contains("# device_snapshot"))
            .unwrap();
        let installation = commands
            .iter()
            .position(|v| {
                v["command"].as_str().unwrap() == "sh"
                    && v["stdin"]
                        .as_str()
                        .unwrap()
                        .contains("#phonefarm_fixed_tools_install v1")
            })
            .unwrap();
        assert!(warmup < snapshot && snapshot < installation);
    }
}

#[test]
fn loaded_prepare_checks_fresh_profile_after_install_before_first_control_write() {
    let f = F::new(false);
    let mut req = f.read("request.json");
    req["temperature_profile_id"] = json!("loaded_steady_v1");
    req["goal"] = json!("render_cadence");
    f.write("request.json", &req);
    let (code, v) = f.prepare("thermal_hot_before_probe");
    assert_eq!(code, 1, "{v}");
    assert_eq!(v["status"], "BLOCKED");
    f.assert_recovery_required();
    let argv = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
    assert!(argv.contains("mkdir "));
    assert!(!argv.contains("'probe' 'probe_0'"));
    assert!(!f.dir.join("session/admission_0-run.json").exists());
}

#[test]
fn fixed_tools_partial_install_or_corruption_never_runs_probe_and_reconcile_keeps_recovery_requirement() {
    for fail in [
        "batch_partial_install",
        "batch_corrupt_remote",
        "batch_truncated_verify",
    ] {
        let f = F::new(false);
        let (code, v) = f.prepare(fail);
        assert_eq!(code, 1, "{fail}: {v}");
        assert_eq!(v["status"], "BLOCKED");
        f.assert_recovery_required();
        assert!(!f.dir.join("session/admission_0-run.json").exists());
        let argv = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
        assert!(!argv.contains("'probe' 'probe_0'"));
        let (code, v) = f.call(
            &["reconcile", "--session", "session"],
            if fail == "batch_truncated_verify" {
                fail
            } else {
                ""
            },
        );
        assert_eq!(code, 1, "{fail}: {v}");
        assert_eq!(v["status"], "BLOCKED");
        f.assert_recovery_required();
        let actual = f.read("fake-state.json");
        let remote = f.read("session/state.json")["remote"]
            .as_str()
            .unwrap()
            .to_owned();
        assert!(actual["files"].get(format!("{remote}/owner")).is_some());
        assert!(actual["files"]
            .get(format!("{remote}/session.closed"))
            .is_none());
    }
}

#[test]
fn fixed_tools_collect_and_reconcile_do_not_reuse_old_install_acceptance() {
    let f = F::new(false);
    let (code, v) = f.prepare("");
    assert_eq!(code, 0, "{v}");
    f.freeze();
    let mut device = f.read("fake-state.json");
    let remote = f.read("session/state.json")["remote"]
        .as_str()
        .unwrap()
        .to_owned();
    let key = format!("{remote}/worker.sh");
    let old = device["files"][&key].as_str().unwrap().to_owned();
    device["files"][&key] = json!("X".to_owned() + &old[1..]);
    f.write("fake-state.json", &device);
    let (code, v) = f.collect();
    assert_eq!(code, 1, "{v}");
    assert_eq!(v["status"], "BLOCKED");
    assert!(!f.dir.join("session/a0-run.json").exists());
    f.assert_recovery_required();
    let before = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
    assert!(!before.contains("'sample' 'a0'"));
    let (code, v) = f.call(&["reconcile", "--session", "session"], "");
    assert_eq!(code, 1, "{v}");
    f.assert_recovery_required();
    let actual = f.read("fake-state.json");
    assert!(actual["files"]
        .get(format!("{remote}/session.closed"))
        .is_none());
}

#[test]
fn actual_remote_prepare_probe_rechecks_thermal_before_apply_without_transport() {
    // Execute the exact production case body with host-owned stubs. No Android device.
    let source = include_str!("../../loop_v1/tools/confirmation_worker.sh");
    let body = source
        .split("  probe|control_probe)\n")
        .nth(1)
        .unwrap()
        .split("    ;;\n  sample)")
        .next()
        .unwrap();
    for (mode, thermal_exit, should_apply) in [
        ("probe", 82, false),
        ("probe", 0, true),
        ("control_probe", 82, false),
    ] {
        let f = F::local();
        let worker_dir = f.dir.join("worker");
        std::fs::create_dir_all(worker_dir.join("probe_0")).unwrap();
        std::fs::write(
            worker_dir.join("thermal_sample.sh"),
            format!("#!/bin/sh\nprintf 'UPTIME\t0\nHAL_STATUS\t3\n'\nexit {thermal_exit}\n"),
        )
        .unwrap();
        std::fs::write(
            worker_dir.join("probe_0.plan.tsv"),
            "gpu.min_pwrlevel\tsysfs\t/mock/min_pwrlevel\t3\n",
        )
        .unwrap();
        let script = format!(
            r#"set -eu
D=$1; R="$D/probe_0"; RUN=probe_0; MODE=$2
intent() {{ :; }}
mkdir() {{ :; }}
snapshot() {{ :; }}
cleanup_failure() {{ :; }}
apply_plan() {{ printf 'applied\n' > "$D/applied"; }}
read_value() {{ printf '3\n'; }}
sleep() {{ :; }}
thermal_point_uptime() {{ printf '0\n'; }}
thermal_uptime() {{ printf '0\n'; }}
thermal_deadline() {{ printf '%s\n' "$2"; }}
thermal_wait_until() {{ :; }}
restore_controls() {{ :; }}
restore_trace() {{ :; }}
release_master() {{ :; }}
{body}"#
        );
        let mut cmd = Command::new("sh");
        cmd.arg("-c")
            .arg(script)
            .arg("owned-probe-check")
            .arg(&worker_dir)
            .arg(mode);
        let out = f.output("actual-worker-thermal-gate", &mut cmd);
        assert_eq!(
            worker_dir.join("applied").exists(),
            should_apply,
            "mode={mode}: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        assert_eq!(out.status.success(), should_apply);
        if !should_apply {
            let raw =
                std::fs::read_to_string(worker_dir.join("probe_0/thermal_hold.raw.tsv")).unwrap();
            assert!(
                raw.contains("HAL_STATUS\t3"),
                "failed sample raw output must remain diagnostic evidence"
            );
            assert_eq!(out.status.code(), Some(82));
        }
    }
}

#[test]
fn absolute_capability_accepts_diagnostic_fluctuation_without_any_performance_upgrade() {
    let f = F::new(false);
    control_request(&f, "fake");
    let mut req = f.read("control-request.json");
    req["temperature_profile_id"] = json!("capability_absolute_v1");
    f.write("control-request.json", &req);
    let (code, v) = f.call(
        &[
            "probe-controls",
            "--request",
            "control-request.json",
            "--out",
            "absolute",
        ],
        "capability_fluctuating",
    );
    assert_eq!(code, 0, "{v}");
    assert_eq!(v["status"], "SIMULATED_CONTROL_RESTORED");
    assert_eq!(v["temperature"]["stable_reference_required"], false);
    assert_eq!(v["temperature"]["completion"]["samples"], 4);
    assert_eq!(
        v["temperature"]["diagnostic_reference"]["schema"],
        "phonefarm/thermal_capability_diagnostics@1"
    );
    assert_eq!(
        v["temperature_reference_purpose"],
        "diagnostic_only_no_comparison"
    );
    for key in [
        "eligible",
        "experiment_eligible",
        "real_game_optimization_proven",
        "promotion_allowed",
        "actual_clock_sustained_proven",
    ] {
        assert_eq!(v[key], false);
    }
    assert_eq!(v["evidence_origin"], "synthetic");
    assert!(!f.dir.join("absolute/plan.json").exists());
    assert!(!f.dir.join("legacy/devlock.d").exists());
    let (code, refusal) = f.call(
        &[
            "collect",
            "--session",
            "absolute",
            "--frozen",
            "missing.json",
        ],
        "",
    );
    assert_eq!(code, 1);
    assert!(refusal["error"]
        .as_str()
        .unwrap()
        .contains("control-only absolute capability"));
    drop(f); // Release admission before starting the independent refusal fixture.
    let f = F::new(false);
    let mut r = f.read("request.json");
    r["temperature_profile_id"] = json!("capability_absolute_v1");
    f.write("request.json", &r);
    let (code, v) = f.prepare("");
    assert_eq!(code, 1, "{v}");
    assert!(v["error"].as_str().unwrap().contains("temperature profile"));
    assert!(!f.dir.join("argv.jsonl").exists());
}
#[test]
fn absolute_capability_bad_evidence_and_unknown_restore_still_block() {
    for fail in [
        "thermal_missing",
        "thermal_wrong_unit",
        "thermal_severe",
        "thermal_override",
        "thermal_short_admission",
        "control_hold_hot",
        "control_hold_severe",
        "control_hold_incomplete",
        "restore",
        "closure_unknown",
    ] {
        let f = F::new(false);
        control_request(&f, "fake");
        let mut r = f.read("control-request.json");
        r["temperature_profile_id"] = json!("capability_absolute_v1");
        f.write("control-request.json", &r);
        let (code, v) = f.call(
            &[
                "probe-controls",
                "--request",
                "control-request.json",
                "--out",
                "absolute",
            ],
            fail,
        );
        assert_eq!(code, 1, "{fail}: {v}");
        assert_eq!(v["eligible"], false);
        assert!(!f.dir.join("absolute/control-report.json").exists());
        if fail.starts_with("control_hold") || ["restore", "closure_unknown"].contains(&fail) {
            f.assert_recovery_required();
        } else {
            assert!(!f.dir.join("absolute/initial.snapshot").exists(), "{fail}");
            assert!(!f.dir.join("legacy/devlock.d").exists(), "{fail}");
        }
    }
}
#[test]
fn old_loaded_profile_still_rejects_three_point_one_degree_range_before_control() {
    let f = F::new(false);
    control_request(&f, "fake");
    let mut r = f.read("control-request.json");
    r["temperature_profile_id"] = json!("loaded_steady_v1");
    f.write("control-request.json", &r);
    let (code, v) = f.call(
        &[
            "probe-controls",
            "--request",
            "control-request.json",
            "--out",
            "loaded",
        ],
        "thermal_drift_31",
    );
    assert_eq!(code, 1, "{v}");
    assert!(v["error"].as_str().unwrap().contains("thermally stable"));
    assert!(!f.dir.join("loaded/initial.snapshot").exists());
    assert!(!f.dir.join("legacy/devlock.d").exists());
}

fn origin_fixture() -> F {
    let f = F::new(false);
    let mut r = f.read("request.json");
    r["goal"] = json!("render_cadence");
    r["render_attribution_profile_id"] = json!("kgsl_queued_origin_spf_v1");
    let ad=f.dir.join("reader-artifact");std::fs::create_dir(&ad).unwrap();
    let source=include_bytes!("../../tools/render-collector/reader.c");
    let mut elf=vec![0_u8;64];elf[..6].copy_from_slice(b"\x7fELF\x02\x01");elf[16]=3;elf[18]=183;
    std::fs::write(ad.join("source.c"),source).unwrap();std::fs::write(ad.join("reader.elf"),&elf).unwrap();
    for n in ["compiler.stdout","compiler.stderr"]{std::fs::write(ad.join(n),[]).unwrap();}
    f.write("reader-artifact/build.json",&json!({"schema":"phonefarm/render_reader_build@1","status":"BUILT","target":"aarch64-linux-android30","ndk_revision":"28.2.13676358","flags":["-std=c11","-O2","-Wall","-Wextra","-Werror","-fPIE","-pie","-Wl,-z,relro,-z,now"],"source_sha256":sha(source),"elf_sha256":sha(&elf),"compiler":{"path":"/synthetic/compiler","sha256":"a".repeat(64),"clang_path":"/synthetic/clang","clang_sha256":"b".repeat(64),"package_sha256":"c".repeat(64)},"execution":{"exit":0,"stdout_sha256":sha(&[]),"stderr_sha256":sha(&[]),"process_group_absent":true},"source_authentication":"local_build_receipt_not_independent_authentication","device_run":false,"installed_kernel_source_verified":false,"game_proof":false}));
    r["render_reader_artifact"]=json!(ad);

    f.write("request.json", &r);
    f
}
#[test]
fn queued_origin_actual_prepare_freeze_collect_retains_synthetic_boundary() {
    let f = origin_fixture();
    let (code, p) = f.prepare("");
    assert_eq!(code, 0, "{p}");
    assert_eq!(p["status"], "SIMULATED_BLOCKED");
    let c = f.read("session/render-contract.json");
    assert_eq!(c["submits_per_group"], 2);
    assert_eq!(c["caller_tids"], json!([32074]));
    assert_eq!(
        f.read("session/conditions.json")["schema"],
        "phonefarm/confirmation_conditions@3"
    );
    f.freeze();
    let (code, out) = f.collect();
    assert_eq!(code, 0, "{out}");
    assert_eq!(out["status"], "SIMULATED_BLOCKED");
    assert_eq!(out["real_game_optimization_proven"], false);
    let samples = f.read("session/samples.json");
    assert_eq!(samples["source_kind"], "synthetic_fixture");
    if let Some(bin) = std::env::var_os("PHONEFARM_DRIVER_GOL_BIN") {
        let mut cmd = Command::new(bin);
        cmd.args(["confirm", "judge", "--frozen"])
            .arg(f.dir.join("frozen.json"))
            .arg("--samples")
            .arg(f.dir.join("session/samples.json"))
            .arg("--out")
            .arg(f.dir.join("origin-judge.json"));
        let out = f.output("origin-gol-judge", &mut cmd);
        assert!(!out.status.success());
        let j = f.read("origin-judge.json");
        assert_ne!(j["status"], "PASS");
        assert_eq!(j["real_game_optimization_proven"], false);
        assert_eq!(j["promotion_allowed"], false);
    }
}
#[test]
fn queued_origin_legacy_thermal_request_keeps_existing_native_route() {
    let f = origin_fixture();
    let mut r = f.read("request.json");
    r["temperature_profile_id"] = json!("legacy_cold_v1");
    f.write("request.json", &r);
    let (code, p) = f.prepare("");
    assert_eq!(code, 0, "{p}");
    assert_eq!(p["status"], "SIMULATED_BLOCKED");
    assert_eq!(f.read("session/tools.manifest.json")["count"], 16);
    assert_eq!(f.read("session/conditions.json")["schema"], "phonefarm/confirmation_conditions@3");
    assert!(!f.dir.join("session/thermal_sample.sh").exists());
    assert!(f.dir.join("session/render-contract.json").exists());
    assert!(f.dir.join("session/render-helper-removed.json").exists());
}
#[test]
fn queued_origin_foreign_unmatched_loss_never_prepare() {
    for fail in ["origin_foreign", "origin_unmatched", "origin_loss"] {
        let f = origin_fixture();
        let (code, out) = f.prepare(fail);
        assert_eq!(code, 1, "{fail} {out}");
        assert!(!f.dir.join("session/prepared.manifest.json").exists());
    }
}
#[test]
fn queued_origin_frozen_scale_and_epoch_changes_block_heldout() {
    for fail in ["origin_spf1", "origin_spf3", "origin_epoch", "origin_tail_scope"] {
        let f = origin_fixture();
        assert_eq!(f.prepare("").0, 0);
        f.freeze();
        let (code, out) = f.call(
            &[
                "collect",
                "--session",
                "session",
                "--frozen",
                "frozen.json",
                "--execute",
            ],
            fail,
        );
        assert_eq!(code, 1, "{fail} {out}");
        assert!(!f.dir.join("session/samples.json").exists());
    }
}
#[test]
fn queued_origin_rehashed_frozen_contract_does_not_hide_raw_contradiction() {
    let f = origin_fixture();
    assert_eq!(f.prepare("").0, 0);
    f.freeze();
    let mut c = f.read("session/render-contract.json");
    c["submits_per_group"] = json!(3);
    f.write("session/render-contract.json", &c);
    // Outer prepared hashes are also rehashed to exercise receiver reconstruction, not integrity only.
    let bytes = std::fs::read(f.dir.join("session/render-contract.json")).unwrap();
    let mut a = f.read("session/admission.json");
    a["render_contract"]["sha256"] = json!(sha(&bytes));
    f.write("session/admission.json", &a);
    let mut manifest = f.read("session/prepared.manifest.json");
    for name in ["render-contract.json", "admission.json"] {
        manifest["hashes"][name] = json!(sha(
            &std::fs::read(f.dir.join("session").join(name)).unwrap()
        ));
    }
    f.write("session/prepared.manifest.json", &manifest);
    let (code, out) = f.collect();
    assert_eq!(code, 1, "{out}");
    assert!(!f.dir.join("session/samples.json").exists());
}


#[test]
fn queued_origin_native_artifact_rejects_before_transport() {
    for fail in ["missing", "source", "elf", "extra", "proof", "symlink"] {
        let f = origin_fixture();
        match fail {
            "missing" => {
                let mut r = f.read("request.json");
                r.as_object_mut().unwrap().remove("render_reader_artifact");
                f.write("request.json", &r);
            }
            "source" => {
                std::fs::write(f.dir.join("reader-artifact/source.c"), b"different source").unwrap()
            }
            "elf" => std::fs::write(f.dir.join("reader-artifact/reader.elf"), b"wrong").unwrap(),
            "extra" => std::fs::write(f.dir.join("reader-artifact/failure.json"), b"{}").unwrap(),
            "proof" => {
                let mut r = f.read("reader-artifact/build.json");
                r["game_proof"] = json!(true);
                f.write("reader-artifact/build.json", &r);
            }
            "symlink" => {
                std::fs::rename(f.dir.join("reader-artifact"), f.dir.join("reader-real")).unwrap();
                std::os::unix::fs::symlink(
                    f.dir.join("reader-real"),
                    f.dir.join("reader-artifact"),
                )
                .unwrap();
            }
            _ => unreachable!(),
        }
        let (code, r) = f.prepare("");
        assert_eq!(code, 1, "{fail} {r}");
        assert!(!f.dir.join("argv.jsonl").exists());
        assert!(!f.dir.join("session").exists());
    }
}
#[test]
fn queued_origin_helper_failure_has_only_helper_recovery() {
    let f = origin_fixture();
    let (code, r) = f.prepare("helper_interrupt");
    assert_eq!(code, 1, "{r}");
    f.assert_recovery_required();
    assert!(!f.dir.join("session/main-install.intent.json").exists());
    assert!(!f.dir.join("session/inputs.manifest.json").exists());
    let before = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
    assert!(!before.contains("'probe' 'probe_0'"));
    assert!(!before.contains("'sample'"));
    let (code, r) = f.call(&["reconcile", "--session", "session", "--execute"], "");
    assert_eq!(code, 0, "{r}");
    assert_eq!(r["status"], "SIMULATED_HELPER_RECONCILED");
    assert_eq!(r["control_restore_claimed"], false);
    assert!(!f.dir.join("legacy/devlock.d/phonefarm-lease.json").exists());
    assert!(!f.dir.join("session/prepared.manifest.json").exists());
    let after = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
    assert!(after.contains("reconcile_render"));
    assert!(!after.contains("'reconcile'"));
}
#[test]
fn queued_origin_helper_unknown_or_main_intent_preserves_recovery_requirement() {
    for fail in ["helper_group_unknown", "main_intent"] {
        let f = origin_fixture();
        assert_eq!(f.prepare("helper_read_failure").0, 1);
        if fail == "main_intent" {
            f.write(
                "session/main-install.intent.json",
                &json!({"schema":"phonefarm/main_install_intent@1"}),
            );
        }
        let (code, r) = f.call(
            &["reconcile", "--session", "session", "--execute"],
            if fail == "main_intent" { "" } else { fail },
        );
        assert_eq!(code, 1, "{fail} {r}");
        f.assert_recovery_required();
        assert!(!f.dir.join("session/render-helper-removed.json").exists());
    }
}
#[test]
fn queued_origin_main_failure_requires_full_restoration() {
    let f = origin_fixture();
    assert_eq!(f.prepare("probe").0, 1);
    assert!(f.dir.join("session/main-install.intent.json").exists());
    assert!(f.dir.join("session/render-helper-removed.json").exists());
    let (code, r) = f.call(&["reconcile", "--session", "session", "--execute"], "");
    assert_eq!(code, 0, "{r}");
    assert_eq!(r["status"], "SIMULATED_RECONCILED");
    let argv = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
    assert!(argv.contains("'reconcile'"));
    assert!(!f.dir.join("session/prepared.manifest.json").exists());
}
#[test]
fn actual_helper_closure_checker_rejects_ambiguous_groups_and_ledgers() {
    for failure in [
        "",
        "owner",
        "live_pid",
        "unknown_group",
        "pending",
        "malformed_master",
        "wrong_child",
        "remove",
    ] {
        let f = F::local();
        let d = f.dir.join("phonefarm-confirmation-token-reader");
        let run = "helper_reconcile_1";
        std::fs::create_dir_all(d.join(run)).unwrap();
        std::fs::create_dir(d.join("session.closed")).unwrap();
        std::fs::create_dir(d.join("proc")).unwrap();
        std::fs::write(d.join("boot"), b"fake-boot\n").unwrap();
        std::fs::write(
            d.join("owner"),
            if failure == "owner" {
                b"other fake-boot\n".as_slice()
            } else {
                b"token fake-boot\n".as_slice()
            },
        )
        .unwrap();
        std::fs::write(
            d.join("session.closed/owner"),
            format!("token fake-boot {run}\n"),
        )
        .unwrap();
        std::fs::write(
            d.join(run).join("session_closed.txt"),
            format!("SESSION_REVOKED token fake-boot {run}\n"),
        )
        .unwrap();
        std::fs::write(d.join(run).join("quiescence.txt"), b"OWNED_GROUPS_ABSENT\n").unwrap();
        std::fs::write(
            d.join(run).join("master.tsv"),
            if failure == "malformed_master" {
                format!("token fake-boot 900001\n")
            } else {
                format!("token fake-boot 900001 123 {run}\n")
            },
        )
        .unwrap();
        if failure == "live_pid" {
            std::fs::create_dir(d.join("proc/900001")).unwrap();
        }
        if failure == "pending" {
            std::fs::write(
                d.join(run).join("launch-render_reader.pending"),
                b"unowned\n",
            )
            .unwrap();
        }
        if failure == "wrong_child" {
            std::fs::write(
                d.join(run).join("children.tsv"),
                b"workload\t900002\t123\t/wrong\n",
            )
            .unwrap();
        }
        let shim = d.join("kill-shim");
        std::fs::write(
            &shim,
            if failure == "unknown_group" {
                b"#!/bin/sh\nprintf 'Permission denied\n' >&2\nexit 1\n".as_slice()
            } else {
                b"#!/bin/sh\nprintf 'No such process\n' >&2\nexit 1\n".as_slice()
            },
        )
        .unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&shim, std::fs::Permissions::from_mode(0o700)).unwrap();
        let script = include_str!("../../loop_v1/tools/confirmation_render_close.sh")
            .replace("/data/local/tmp/", &format!("{}/", f.dir.display()))
            .replace(
                "/proc/sys/kernel/random/boot_id",
                &format!("{}/boot", d.display()),
            )
            .replace("/proc/$1", &format!("{}/proc/$1", d.display()))
            .replace("/system/bin/kill", shim.to_str().unwrap());
        let mut cmd = Command::new("sh");
        cmd.args(["-c", &script, "owned-closure"]).arg(&d).args([
            "token",
            "fake-boot",
            run,
            if failure == "remove" {
                "remove"
            } else {
                "verify"
            },
        ]);
        let o = f.output("actual-helper-closure", &mut cmd);
        assert_eq!(
            o.status.success(),
            failure.is_empty() || failure == "remove",
            "{failure}: {}",
            String::from_utf8_lossy(&o.stderr)
        );
        assert_eq!(d.exists(), failure != "remove");
    }
}

#[test]
fn actual_readonly_worker_failure_never_calls_control_or_trace_restore() {
    let source = include_str!("../../loop_v1/tools/confirmation_worker.sh");
    let body = source
        .split("  render_readonly)\n")
        .nth(1)
        .unwrap()
        .split("    ;;\n  reconcile_render)")
        .next()
        .unwrap();
    let cleanup = source
        .split("cleanup_failure() {\n")
        .nth(1)
        .unwrap()
        .split("\n}\nacquire_master()")
        .next()
        .unwrap();
    for (child_exit, group_known) in [(0, true), (81, true), (0, false)] {
        let f = F::local();
        let d = f.dir.join("readonly-worker");
        std::fs::create_dir(&d).unwrap();
        std::fs::write(
            d.join("render_args.tsv"),
            b"com.example.game\tUnityGfxDeviceW\n",
        )
        .unwrap();
        let script = format!(
            r#"set -eu
D=$1; R="$D/render_initial"; MODE=render_readonly; MASTER='synthetic host master'
intent() {{ :; }}
sync() {{ :; }}
start_child() {{ sh -c 'exit {child_exit}' & CHILD_PID=$!; }}
quiescent() {{ {}; }}
stop_owned() {{ printf stopped > "$D/stop-called"; return 0; }}
restore_trace() {{ printf unsafe > "$D/restore-called"; }}
restore_controls() {{ printf unsafe > "$D/restore-called"; }}
release_master() {{ printf released > "$D/released"; }}
cleanup_failure() {{
{cleanup}
}}
{body}"#,
            if group_known { "true" } else { "false" }
        );
        let mut cmd = Command::new("sh");
        cmd.args(["-c", &script, "owned-readonly-callback"]).arg(&d);
        let o = f.output("actual-readonly-worker", &mut cmd);
        assert_eq!(
            o.status.success(),
            child_exit == 0 && group_known,
            "{}",
            String::from_utf8_lossy(&o.stderr)
        );
        assert!(!d.join("restore-called").exists());
        assert_eq!(d.join("released").exists(), child_exit == 0 && group_known);
        if child_exit != 0 || !group_known {
            assert!(d.join("stop-called").exists());
        }
    }
}

#[test]
fn helper_recovery_diagnostic_records_only_and_never_claims_recovery() {
    let f = origin_fixture();
    assert_eq!(f.prepare("helper_interrupt").0, 1);
    for fail in ["", "diagnostic_partial", "diagnostic_boot"] {
        let before = f.read("fake-state.json");
        let (code, r) = f.call(
            &["diagnose-helper", "--session", "session", "--execute"],
            fail,
        );
        assert_eq!(code, 0, "{fail}: {r}");
        assert_eq!(r["status"], "UNKNOWN");
        assert_eq!(r["diagnostic_complete"], fail.is_empty());
        for key in [
            "eligible",
            "recovery_completed",
            "remote_idle_verified",
            "control_restore_claimed",
            "promotion_allowed",
            "real_game_optimization_proven",
            "remote_worker_executed",
        ] {
            assert_eq!(r[key], false);
        }
        f.assert_recovery_required();
        assert_eq!(
            f.read("fake-state.json"),
            before,
            "diagnostic must not mutate synthetic remote state"
        );
        let argv = std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
        assert!(argv.contains("# Fixed host-supplied read-only diagnostic"));
        let issued_worker_recovery = argv.lines().any(|line| {
            let row: Value = serde_json::from_str(line).unwrap();
            let cmd = row["command"].as_str().unwrap();
            cmd.starts_with("setsid sh ") && cmd.contains("reconcile_render")
        });
        assert!(!issued_worker_recovery);
        assert!(!f.dir.join("session/render-helper-removed.json").exists());
        let diagnostic = PathBuf::from(r["diagnostic_dir"].as_str().unwrap());
        assert!(diagnostic.join("result.json").exists());
        assert!(diagnostic.join("raw.stdout").exists());
    }
}
#[test]
fn helper_recovery_diagnostic_rejects_main_or_changed_sources_before_transport() {
    let f = origin_fixture();
    assert_eq!(f.prepare("helper_interrupt").0, 1);
    let original_render_args = std::fs::read(f.dir.join("session/render_args.tsv")).unwrap();
    for fail in ["main", "dangling_main", "changed", "noexecute"] {
        if fail == "main" {
            f.write(
                "session/main-install.intent.json",
                &json!({"schema":"phonefarm/main_install_intent@1"}),
            );
        }
        #[cfg(unix)]
        if fail == "dangling_main" {
            std::os::unix::fs::symlink(
                f.dir.join("absent-main-marker"),
                f.dir.join("session/main-install.intent.json"),
            )
            .unwrap();
        }
        if fail == "changed" {
            std::fs::write(f.dir.join("session/render_args.tsv"), "foreign\tthread\n").unwrap();
        }
        let before = std::fs::read(f.dir.join("argv.jsonl")).unwrap();
        let args = if fail == "noexecute" {
            vec!["diagnose-helper", "--session", "session"]
        } else {
            vec!["diagnose-helper", "--session", "session", "--execute"]
        };
        let (code, r) = f.call(&args, "");
        assert_eq!(code, 1, "{fail}: {r}");
        assert_eq!(r["status"], "BLOCKED");
        assert_eq!(std::fs::read(f.dir.join("argv.jsonl")).unwrap(), before);
        f.assert_recovery_required();
        if matches!(fail, "main" | "dangling_main") {
            std::fs::remove_file(f.dir.join("session/main-install.intent.json")).unwrap();
        }
        if fail == "changed" {
            std::fs::write(f.dir.join("session/render_args.tsv"), &original_render_args).unwrap();
        }
    }
}

#[test]
fn native_dead_retirement_requires_complete_current_proofs_and_preserves_unknown_result() {
    for fail in ["", "native_partial", "native_live", "native_epoch", "native_files", "native_post"] {
        let f=origin_fixture();assert_eq!(f.prepare("helper_interrupt").0,1);
        let (code,r)=f.call(&["retire-helper-native","--session","session","--execute"],fail);
        assert_eq!(code,0,"{fail}: {r}");
        let success=fail.is_empty();assert_eq!(r["retirement_completed"],success);assert_eq!(r["recovery_required"],!success);
        f.assert_released();
        for key in ["eligible","real_game_optimization_proven","promotion_allowed","control_restore_claimed","old_samples_revalidated","live_process_stopped","old_tools_changed"] {assert_eq!(r[key],false);}
        if success {assert_eq!(r["status"],"SIMULATED_HELPER_CLOSED");assert_eq!(r["stages"].as_array().unwrap().len(),2);}
        else {assert_eq!(r["status"],"UNKNOWN");}
        let events=std::fs::read_to_string(f.dir.join("argv.jsonl")).unwrap();
        let commands=events.lines().map(|s|serde_json::from_str::<Value>(s).unwrap()).collect::<Vec<_>>();
        assert!(commands.iter().all(|v|!v["command"].as_str().unwrap().contains("reconcile_render")));
        if fail=="native_post" {assert!(f.read("fake-state.json")["files"].as_object().unwrap().keys().any(|k|k.ends_with("/session.closed")),"closed without post proof stays barrier");assert!(!commands.iter().any(|v|v["command"].as_str().unwrap().ends_with("'remove'")));}
    }
}
#[test]
fn native_helper_noexecute_main_or_changed_frozen_inputs_refuse_before_transport() {
    let f = origin_fixture();
    assert_eq!(f.prepare("helper_interrupt").0, 1);
    // These cases refuse before transport, unlike an UNKNOWN retirement outcome,
    // so the unchanged failed session is safe to reuse.
    for kind in ["noexecute", "main", "source"] {
        if kind == "main" {
            f.write(
                "session/main-install.intent.json",
                &json!({"unexpected":true}),
            );
        }
        if kind == "source" {
            std::fs::write(f.dir.join("session/render_reader.source.c"), "changed").unwrap();
        }
        let before = std::fs::read(f.dir.join("argv.jsonl")).unwrap();
        let args = if kind == "noexecute" {
            vec!["retire-helper-native", "--session", "session"]
        } else {
            vec!["retire-helper-native", "--session", "session", "--execute"]
        };
        let (code, r) = f.call(&args, "");
        assert_eq!(code, 1, "{kind}: {r}");
        assert_eq!(r["status"], "BLOCKED");
        assert_eq!(std::fs::read(f.dir.join("argv.jsonl")).unwrap(), before);
        f.assert_recovery_required();
        if kind == "main" {
            std::fs::remove_file(f.dir.join("session/main-install.intent.json")).unwrap();
        }
    }
}

#[test]
fn killing_task_group_reaps_separate_transport_group_and_releases_lock() {
    use std::os::unix::process::CommandExt;
    use std::time::Duration;
    let f = F::new(false);
    let mut command = f.command(&["prepare", "--request", "request.json", "--out", "session"], "host_parent_killed");
    let mut child = command.process_group(0).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).spawn().unwrap();
    let ready = f.dir.join("transport-group");
    let deadline = Instant::now() + Duration::from_secs(5);
    while !ready.exists() {
        assert!(child.try_wait().unwrap().is_none());
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(10));
    }
    let transport: i32 = std::fs::read_to_string(ready).unwrap().parse().unwrap();
    unsafe { libc::kill(-(child.id() as i32), libc::SIGKILL); }
    child.wait().unwrap();
    let until = Instant::now() + Duration::from_secs(3);
    let released = loop {
        let o = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
            .args(["lease", "--serial", "fake-target", "--timeout-ms", "0", "--", "/usr/bin/true"])
            .env("DEVLOCK_DIR", f.dir.join("legacy")).env("PHONEFARM_LEASE_DIR", f.dir.join("lease")).output().unwrap();
        if o.status.success() { break true; }
        if Instant::now() >= until { break false; }
        std::thread::sleep(Duration::from_millis(20));
    };
    unsafe { libc::kill(-transport, libc::SIGKILL); }
    assert!(released, "killing the original task left a separate transport group holding the fd");
    assert!(f.dir.join("transport-term").exists(), "transport cleanup must send TERM before KILL");
}
