//! Real CLI boundary tests: no phone, no model, no secret-bearing environment.
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};
static NEXT: AtomicU64 = AtomicU64::new(0);
struct Scratch(PathBuf);
impl Scratch {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "phonefarm-qualification-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&p).unwrap();
        Self(p)
    }
    fn write(&self, name: &str, v: &Value) -> PathBuf {
        let p = self.0.join(name);
        std::fs::write(&p, serde_json::to_vec(v).unwrap()).unwrap();
        p
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
fn cmd(t: &Scratch) -> Command {
    let mut c = Command::new(env!("CARGO_BIN_EXE_phonefarm"));
    c.current_dir(&t.0)
        .env("PHONEFARM_LEASE_DIR", t.0.join("leases"))
        .env("DEVLOCK_DIR", t.0.join("legacy"))
        // Use the real typed owner/instance protocol against this test's isolated lock root.
        .env(
            "DEVLOCK_BIN",
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .join("tools/devlock"),
        )
        .env("PHONEFARM_LEASE_TIMEOUT_MS", "150");
    c
}
fn fixture() -> Value {
    // Hypothetical canonical collector metadata exercises logic; never device validation.
    let mut e: Value =
        serde_json::from_str(include_str!("../../examples/qualification_ready.json")).unwrap();
    e["evidence_origin"] = json!("recorded_device");
    for arm in ["baseline", "control", "candidate"] {
        for r in e[arm].as_array_mut().unwrap() {
            r["frame_evidence"]["source"] = json!("raw_ftrace_kgsl");
        }
    }
    e
}
fn report(o: &Output) -> Value {
    serde_json::from_slice(&o.stdout).unwrap_or_else(|e| {
        panic!(
            "{e}: stdout={} stderr={}",
            String::from_utf8_lossy(&o.stdout),
            String::from_utf8_lossy(&o.stderr)
        )
    })
}
fn request() -> Value {
    json!({"version":2,"kind":"sysparam","candidate_id":"offline-boundary","package":"com.example.game","workload":{"carrier":"game_replay","script":null,"seconds":30},"conditions":{"cool_c":40,"temp_cap_c":60},"protocol":{"rounds":5,"alternating":true,"p_threshold":0.05},"payload":{"params":{}}})
}
#[test]
fn qualify_replays_same_evidence_and_blocks_display_proxy() {
    let t = Scratch::new();
    let p = t.write("evidence.json", &fixture());
    let run = || {
        cmd(&t)
            .args(["qualify", "--from"])
            .arg(&p)
            .arg("--json")
            .output()
            .unwrap()
    };
    let a = run();
    let b = run();
    assert!(a.status.success());
    assert_eq!(a.stdout, b.stdout);
    assert_eq!(report(&a)["eligible"], true);
    let mut e = fixture();
    e["candidate"][0]["frame_evidence"]["semantics"] = json!("display_frames");
    let p = t.write("display.json", &e);
    let o = cmd(&t).args(["qualify", "--from"]).arg(p).output().unwrap();
    assert_eq!(o.status.code(), Some(1));
    assert_eq!(report(&o)["eligible"], false);
}
#[test]
fn actual_eval_offline_route_freezes_target_without_optimization_pass() {
    let t = Scratch::new();
    let req = t.write("request.json", &request());
    let e = t.write("evidence.json", &fixture());
    let o = cmd(&t)
        .args(["eval", "--request"])
        .arg(&req)
        .arg("--qualify-from")
        .arg(&e)
        .arg("--json")
        .output()
        .unwrap();
    assert!(o.status.success());
    let r = report(&o);
    assert_eq!(r["qualification_only"], true);
    assert_eq!(r["qualification"]["eligible"], true);
    assert_eq!(r["status"], "ABORT");
    assert_eq!(r["verdict"]["is_pareto_improvement"], false);
    let mut input = fixture();
    input["candidate"][0]["env"]["power_w_mean"] = Value::Null;
    let e = t.write("missing-power.json", &input);
    let o = cmd(&t)
        .args(["eval", "--request"])
        .arg(req)
        .arg("--qualify-from")
        .arg(e)
        .output()
        .unwrap();
    assert_eq!(o.status.code(), Some(1));
    assert_eq!(report(&o)["qualification"]["eligible"], false);
}
#[cfg(unix)]
#[test]
fn integrated_offline_shader_precedes_live_support_and_lease_gates() {
    // The merge must preserve offline replay before both the shader plan and any device lease.
    // Use the real typed GOL envelope, an unsupported live hook and an existing foreign lock.
    let t = Scratch::new();
    let mut req: Value = serde_json::from_str(include_str!(
        "../../examples/gol_shader_native_request.json"
    ))
    .unwrap();
    req["conditions"]["temp_cap_c"] = json!(60);
    req["payload"]["inject"]["via"] = json!("vulkan_layer");
    let req = t.write("request.json", &req);
    let evidence = t.write("evidence.json", &fixture());
    let lock = t.0.join("legacy/devlock.d");
    std::fs::create_dir_all(&lock).unwrap();
    let owner = b"abandoned-experiment-unknown-remote-state\n";
    std::fs::write(lock.join("owner"), owner).unwrap();
    let adb = fake_adb(&t);
    let log = t.0.join("adb.log");
    let out = t.0.join("out");
    let o = cmd(&t)
        .args(["eval", "--request"])
        .arg(&req)
        .arg("--qualify-from")
        .arg(evidence)
        .arg("--out")
        .arg(&out)
        // Model a child already admitted by the typed outer gate. The foreign
        // marker below must remain an inner-lease barrier, not an outer fixture.
        .env("DEVLOCK_BIN", "/usr/bin/true")
        .env("DEVLOCK_INSTANCE_ID", "0123456789abcdef0123456789abcdef")
        .env("ADB_BIN", &adb)
        .env("HDC_BIN", &adb)
        .env("QUALIFICATION_ADB_LOG", &log)
        .output()
        .unwrap();
    let r = report(&o);
    assert!(o.status.success());
    assert_eq!(r["qualification_only"], true);
    assert_eq!(r["qualification"]["eligible"], true); // hypothetical logic only
    assert_eq!(r["status"], "ABORT");
    assert_eq!(r["verdict"]["is_pareto_improvement"], false);
    assert!(!out.exists());
    assert!(!log.exists());
    assert!(!t.0.join("leases").exists());
    assert_eq!(std::fs::read(lock.join("owner")).unwrap(), owner);
    // Removing only --qualify-from is a live request, so unsupported fields must ABORT first.
    let o = cmd(&t)
        .args(["eval", "--request"])
        .arg(req)
        .arg("--out")
        .arg(&out)
        .env("DEVLOCK_BIN", "/usr/bin/true")
        .env("DEVLOCK_INSTANCE_ID", "0123456789abcdef0123456789abcdef")
        .env("ADB_BIN", adb)
        .env("QUALIFICATION_ADB_LOG", &log)
        .output()
        .unwrap();
    let r = report(&o);
    assert_eq!(o.status.code(), Some(2));
    assert_eq!(r["status"], "ABORT");
    assert_eq!(r["qualification"]["eligible"], false);
    assert!(r["verdict"]["reason"]
        .as_str()
        .unwrap()
        .contains("unsupported"));
    assert!(!out.exists());
    assert!(!log.exists());
    assert!(!t.0.join("leases").exists());
    assert_eq!(std::fs::read(lock.join("owner")).unwrap(), owner);
}
#[cfg(unix)]
fn fake_adb(t: &Scratch) -> PathBuf {
    use std::os::unix::fs::PermissionsExt;
    let p = t.0.join("adb");
    std::fs::write(&p,"#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$QUALIFICATION_ADB_LOG\"\ncase \"$*\" in\n  *get-state*) echo device;;\n  *'su -c id'*) echo 'uid=2000(shell) gid=2000(shell)';;\n  *) exit 0;;\nesac\n").unwrap();
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
    p
}
#[cfg(unix)]
fn assert_only_read_root(log: &Path) {
    let s = std::fs::read_to_string(log).unwrap();
    assert!(s.contains("su -c id"));
    assert!(!s.contains("push"));
    assert!(!s.contains("probe_sysparam"));
    assert!(!s.contains("charge_suspend"));
    assert!(!s.contains("knob_sysparam"));
}
#[cfg(unix)]
#[test]
fn actual_eval_nonroot_stops_before_push_or_writable_probe() {
    let t = Scratch::new();
    let req = t.write("request.json", &request());
    let adb = fake_adb(&t);
    let log = t.0.join("adb.log");
    let o = cmd(&t)
        .args(["eval", "--request"])
        .arg(req)
        .args(["--serial", "fixture-device", "--out"])
        .arg(t.0.join("out"))
        .env("ADB_BIN", adb)
        .env("QUALIFICATION_ADB_LOG", &log)
        .output()
        .unwrap();
    assert!(o.status.success());
    let r = report(&o);
    assert_eq!(r["status"], "ABORT");
    assert_eq!(r["qualification"]["eligible"], false);
    assert_eq!(r["qualification"]["probe_allowed"], false);
    assert_only_read_root(&log);
}
#[cfg(unix)]
#[test]
fn actual_autoloop_nonroot_stops_before_probe_or_model() {
    let t = Scratch::new();
    let adb = fake_adb(&t);
    let log = t.0.join("adb.log");
    let out = t.0.join("out");
    let o = cmd(&t)
        .args(["autoloop", "--serial", "fixture-device", "--out"])
        .arg(&out)
        .env("ADB_BIN", adb)
        .env("QUALIFICATION_ADB_LOG", &log)
        .output()
        .unwrap();
    assert_eq!(o.status.code(), Some(1));
    assert_only_read_root(&log);
    let q: Value =
        serde_json::from_slice(&std::fs::read(out.join("qualification.json")).unwrap()).unwrap();
    assert_eq!(q["eligible"], false);
    assert!(!out.join("gen1").exists());
}

#[test]
fn published_synthetic_fixture_cannot_qualify_real_claim() {
    let t = Scratch::new();
    let e: Value =
        serde_json::from_str(include_str!("../../examples/qualification_ready.json")).unwrap();
    let p = t.write("synthetic.json", &e);
    let o = cmd(&t).args(["qualify", "--from"]).arg(p).output().unwrap();
    assert_eq!(o.status.code(), Some(1));
    let r = report(&o);
    assert_eq!(r["eligible"], false);
    assert_eq!(r["evidence_origin"], "synthetic");
}
#[test]
fn eval_refuses_generator_relaxing_alpha() {
    let t = Scratch::new();
    let mut r = request();
    r["protocol"]["p_threshold"] = json!(1.0);
    let p = t.write("bad-alpha.json", &r);
    let e = t.write("e.json", &fixture());
    let o = cmd(&t)
        .args(["eval", "--request"])
        .arg(p)
        .arg("--qualify-from")
        .arg(e)
        .output()
        .unwrap();
    assert_eq!(o.status.code(), Some(2));
}

#[cfg(unix)]
#[test]
fn actual_probe_failed_restore_cannot_report_reverted() {
    use std::os::unix::fs::PermissionsExt;
    let t = Scratch::new();
    let p = t.0.join("adb-root-fixture");
    let log = t.0.join("adb.log");
    let counter = t.0.join("snapshot.count");
    let stub = r#"#!/bin/sh
printf '%s\n' "$*" >> "$QUALIFICATION_ADB_LOG"
case "$*" in
 *'su -c id'*) echo 'uid=0(root) gid=0(root)';;
 *TRACE_AVAILABLE*) echo TRACE_AVAILABLE;;
 *device_snapshot.sh*)
   n=0; [ -f "$QUALIFICATION_SNAPSHOT_COUNT" ] && n=$(cat "$QUALIFICATION_SNAPSHOT_COUNT")
   n=$((n+1)); printf '%s\n' "$n" > "$QUALIFICATION_SNAPSHOT_COUNT"
   echo "cpu.policy0.scaling_min=$n";;
 *probe_sysparam.sh*)
   echo cpu.policies=0
   echo 'cpu.policy0.avail_freqs=300000 1000000'
   echo cpu.policy0.scaling_min_freq.cur=300000
   echo cpu.policy0.scaling_min_freq.writable=yes
   echo cpu.policy0.scaling_min_freq.effect=live;;
 *) exit 0;;
esac
"#;
    std::fs::write(&p, stub).unwrap();
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
    let mut r = request();
    r["payload"]["probe_only"] = json!(true);
    let req = t.write("probe.json", &r);
    let o = cmd(&t)
        .args(["eval", "--request"])
        .arg(req)
        .args(["--serial", "fixture-device", "--out"])
        .arg(t.0.join("out"))
        .env("ADB_BIN", p)
        .env("QUALIFICATION_ADB_LOG", &log)
        .env("QUALIFICATION_SNAPSHOT_COUNT", counter)
        .output()
        .unwrap();
    assert!(o.status.success());
    let r = report(&o);
    assert_eq!(r["status"], "ABORT");
    assert_eq!(r["conditions_observed"]["restored"], false);
    assert_eq!(r["qualification"]["status"], "blocked");
    assert_eq!(r["qualification"]["probe_allowed"], false);
}

#[cfg(unix)]
#[test]
fn concurrent_capability_probes_cannot_borrow_another_devices_root_output() {
    use std::os::unix::fs::PermissionsExt;
    use std::process::Stdio;
    let shared = Scratch::new();
    let t = Scratch::new();
    let other = Scratch::new();
    let stub = shared.0.join("fake-adb-collision");
    std::fs::write(
        &stub,
        r#"#!/bin/sh
case "$*" in
 *'su -c id'*)
  case "$*" in
   *nonroot-fixture*)
    echo 'uid=2000(shell) gid=2000(shell)'
    touch "$COLLISION_DIR/nonroot-started"
    n=0
    while [ ! -f "$COLLISION_DIR/root-trace-written" ]; do
      n=$((n+1)); [ "$n" -lt 500 ] || exit 8; sleep 0.01
    done
    sleep 0.2;;
   *) echo 'uid=0(root) gid=0(root)';;
  esac;;
 *TRACE_AVAILABLE*)
  echo 'uid=0(root) TRACE_AVAILABLE'
  touch "$COLLISION_DIR/root-trace-written";;
 *device_snapshot.sh*) echo 'cpu.policy0.scaling_min=300';;
 *probe_sysparam.sh*) echo 'cpu.policies=0';;
 *) exit 0;;
esac
"#,
    )
    .unwrap();
    std::fs::set_permissions(&stub, std::fs::Permissions::from_mode(0o755)).unwrap();
    let mut req = request();
    req["payload"]["probe_only"] = json!(true);
    let nonroot_request = t.write("request.json", &req);
    let root_request = other.write("request.json", &req);
    let mut a = cmd(&t)
        .args(["eval", "--request"])
        .arg(nonroot_request)
        .args(["--serial", "nonroot-fixture", "--out"])
        .arg(t.0.join("out"))
        .env("ADB_BIN", &stub)
        .env("COLLISION_DIR", &shared.0)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while !shared.0.join("nonroot-started").exists() {
        if std::time::Instant::now() >= deadline {
            let _ = a.kill();
            let _ = a.wait();
            panic!("nonroot fixture did not begin bounded root probe");
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    let b = cmd(&other)
        .args(["eval", "--request"])
        .arg(root_request)
        .args(["--serial", "root-fixture", "--out"])
        .arg(other.0.join("out"))
        .env("ADB_BIN", &stub)
        .env("COLLISION_DIR", &shared.0)
        .output()
        .unwrap();
    let a = a.wait_with_output().unwrap();
    assert!(b.status.success(), "{}", String::from_utf8_lossy(&b.stderr));
    assert!(a.status.success(), "{}", String::from_utf8_lossy(&a.stderr));
    let root = report(&b);
    let nonroot = report(&a);
    assert_eq!(root["qualification"]["probe_allowed"], true, "{root}");
    assert_eq!(
        nonroot["qualification"]["probe_allowed"], false,
        "{nonroot}"
    );
    assert!(!t.0.join("out/snap_before_probe.txt").exists());
    assert_eq!(nonroot["status"], "ABORT");
}

#[cfg(unix)]
#[test]
fn capability_scratch_reservation_failure_is_unknown_before_transport() {
    let t = Scratch::new();
    let adb = fake_adb(&t);
    let log = t.0.join("adb.log");
    let not_a_directory = t.0.join("not-a-directory");
    std::fs::write(&not_a_directory, b"sentinel").unwrap();
    let p = t.write("request.json", &request());
    let o = cmd(&t)
        .args(["eval", "--request"])
        .arg(p)
        .args(["--serial", "fixture-device", "--out"])
        .arg(t.0.join("out"))
        .env("ADB_BIN", adb)
        .env("QUALIFICATION_ADB_LOG", &log)
        .env("TMPDIR", &not_a_directory)
        .output()
        .unwrap();
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    let r = report(&o);
    assert_eq!(r["status"], "ABORT");
    assert_eq!(r["qualification"]["probe_allowed"], false);
    let root_check = r["qualification"]["checks"].as_array().unwrap()
        .iter().find(|c| c["id"] == "root").unwrap();
    assert_eq!(root_check["state"], "unknown");
    assert!(!log.exists(), "no transport when its output cannot be isolated");
    assert_eq!(std::fs::read(&not_a_directory).unwrap(), b"sentinel");
}
