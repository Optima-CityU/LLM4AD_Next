//! Real CLI subprocess tests. Every serial/bridge/tool is fake; never invoke adb/hdc.
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};
struct Fixture {
    dir: PathBuf,
}
impl Fixture {
    fn new(name: &str) -> Self {
        let dir = std::env::temp_dir().join(format!(
            "pf-{name}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        Self { dir }
    }
    fn cmd(&self) -> Command {
        let mut c = Command::new(env!("CARGO_BIN_EXE_phonefarm"));
        c.current_dir(&self.dir)
            .env("PHONEFARM_LEASE_DIR", self.dir.join("leases"))
            .env("DEVLOCK_DIR", self.dir.join("legacy"))
            .env("PHONEFARM_LEASE_TIMEOUT_MS", "100")
            .env("ADB_BIN", self.dir.join("fake-adb"));
        c
    }
    fn legacy(&self) -> PathBuf {
        self.dir.join("legacy/devlock.d")
    }

}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}
fn wait_for(path: &Path) {
    let end = Instant::now() + Duration::from_secs(5);
    while !path.exists() && Instant::now() < end {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(path.exists(), "{}", path.display());
}
fn worker(f: &Fixture, label: &str, sec: &str, timeout: &str) -> Child {
    // Marker/event script executes on the host only. No phone or model calls.
    let script="import sys,time,pathlib; d=pathlib.Path(sys.argv[1]); l=sys.argv[2]; (d/(l+'.started')).write_text('yes'); p=d/'events'; p.open('a').write(l+' start\\n'); time.sleep(float(sys.argv[3])); p.open('a').write(l+' end\\n')";
    let cwd = f.dir.join(format!("cwd-{label}"));
    std::fs::create_dir_all(&cwd).unwrap();
    f.cmd()
        .current_dir(cwd)
        .args([
            "lease",
            "--serial",
            "fake-device",
            "--timeout-ms",
            timeout,
            "--",
            "python3",
            "-c",
            script,
        ])
        .arg(&f.dir)
        .args([label, sec])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap()
}
fn shader_request() -> Value {
    json!({"version":2,"kind":"shader","candidate_id":"c","serial":"fake-device","package":"refbench",
    "workload":{"carrier":"refbench","script":"sr_pipeline","seconds":null,"frames":370,"intensity":3},
    "conditions":{"cool_c":42,"require_root":true,"require_discharging":true,"charging_suspend":{"enabled":true,"node":null},"inject":false},
    "protocol":{"rounds":4,"alternating":true,"p_threshold":0.05},
    "payload":{"track":"sr","shader_path":"candidate.glsl","spirv_path":"absent.spv","budget_ms":3,"quality_baseline_db":30}})
}
fn minimal_compute_spirv() -> Vec<u8> {
    // Header + one GLCompute OpEntryPoint + OpExecutionMode LocalSize. The
    // entry-point instruction has exactly four words; an interface ID of zero
    // would not be a legal trailing operand.
    let words: [u32; 15] = [
        0x07230203,
        0x00010300,
        0,
        10,
        0,
        (4 << 16) | 15,
        5,
        1,
        0x6e69616d,
        (6 << 16) | 16,
        1,
        17,
        1,
        1,
        1,
    ];
    words.iter().flat_map(|word| word.to_le_bytes()).collect()
}
#[test]
fn two_real_processes_do_not_interleave_across_cwds() {
    let f = Fixture::new("exclusive");
    let mut a = worker(&f, "A", "0.3", "1000");
    wait_for(&f.dir.join("A.started"));
    let mut b = worker(&f, "B", "0.01", "1000");
    assert!(a.wait().unwrap().success());
    assert!(b.wait().unwrap().success());
    assert_eq!(
        std::fs::read_to_string(f.dir.join("events")).unwrap(),
        "A start\nA end\nB start\nB end\n"
    );
    assert!(!f.legacy().exists());
}
#[test]
fn timeout_preserves_other_owner_and_failure_releases() {
    let f = Fixture::new("timeout");
    let mut a = worker(&f, "A", "0.3", "1000");
    wait_for(&f.dir.join("A.started"));
    let mut b = worker(&f, "B", "0", "20");
    assert_eq!(b.wait().unwrap().code(), Some(2));
    assert!(a.try_wait().unwrap().is_none(), "timed-out waiter must not affect the owner");
    assert!(a.wait().unwrap().success());
    let r = f
        .cmd()
        .args([
            "lease",
            "--serial",
            "fake-device",
            "--timeout-ms",
            "20",
            "--",
            "/this-command-does-not-exist",
        ])
        .output()
        .unwrap();
    assert_eq!(r.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&r.stderr).contains("No such file or directory"));
    assert!(!f.legacy().exists());
    let mut c = worker(&f, "C", "0", "50");
    assert!(c.wait().unwrap().success());
}

#[cfg(unix)]
#[test]
fn executable_permission_failure_releases_the_fd() {
    use std::os::unix::fs::PermissionsExt;
    let f = Fixture::new("spawn-permission");
    let program = f.dir.join("nonexecutable");
    std::fs::write(&program, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&program, std::fs::Permissions::from_mode(0o600)).unwrap();
    let r = f
        .cmd()
        .args(["lease", "--serial", "fake-device", "--timeout-ms", "100", "--"])
        .arg(&program)
        .output()
        .unwrap();
    assert_eq!(r.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&r.stderr).contains("Permission denied"));
    assert!(!f.legacy().exists());
    let mut next = worker(&f, "next", "0", "100");
    assert!(next.wait().unwrap().success());
    assert!(!f.legacy().exists());
}


#[test]
fn actual_eval_route_writes_consumed_plan_and_honest_abort_without_phone() {
    let f = Fixture::new("v2");
    let req = shader_request();
    let path = f.dir.join("request.json");
    std::fs::write(&path, req.to_string()).unwrap();
    // Missing local SPIR-V aborts before any device/root/lease operation.
    let r = f
        .cmd()
        .args(["eval", "--request"])
        .arg(&path)
        .arg("--out")
        .arg(f.dir.join("out"))
        .output()
        .unwrap();
    assert_eq!(r.status.code(), Some(2));
    let report: Value = serde_json::from_slice(&r.stdout).unwrap();
    assert_eq!(report["status"], "ABORT");
    assert_eq!(report["conditions_observed"]["root_verified"], Value::Null);
    assert_eq!(report["resolved_config"]["carrier"], "refbench");
    assert_eq!(report["resolved_config"]["frames"], 370);
    assert_eq!(report["conditions_requested"], req["conditions"]);
    assert_eq!(report["qualification"]["eligible"], false);
    assert_eq!(report["measurement_scope"], "refbench_measurement_only");
    let adapted = std::fs::read_dir(f.dir.join("out"))
        .unwrap()
        .map(|e| e.unwrap().path())
        .find(|p| {
            p.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("_eval_v1_request_")
        })
        .unwrap();
    let plan: Value = serde_json::from_slice(&std::fs::read(adapted).unwrap()).unwrap();
    assert_eq!(
        plan["v2_context"]["resolved_config"]["charging_suspend"]["enabled"],
        true
    );
    assert!(!f.legacy().exists());
}
#[test]
fn actual_typed_gol_request_reaches_native_plan_and_blocks_unimplemented_layer_without_device() {
    // Produced with GOL's real Request::Serialize, including required fan:String and Inject object.
    // This is an interface fixture; it contains no phone evidence.
    let f = Fixture::new("gol-typed");
    let mut req: Value = serde_json::from_str(include_str!(
        "../../examples/gol_shader_native_request.json"
    ))
    .unwrap();
    let path = f.dir.join("request.json");
    std::fs::write(&path, req.to_string()).unwrap();
    let out = f.dir.join("out");
    let r = f
        .cmd()
        .args(["eval", "--request"])
        .arg(&path)
        .arg("--out")
        .arg(&out)
        .output()
        .unwrap();
    let report: Value = serde_json::from_slice(&r.stdout).unwrap();
    assert_eq!(r.status.code(), Some(2)); // absent local SPIR-V, not an unsupported GOL envelope
    assert_eq!(report["status"], "ABORT");
    assert_eq!(report["resolved_config"]["inject"]["via"], "carrier_native");
    assert_eq!(report["resolved_config"]["frames"], 370);
    assert_eq!(report["conditions_requested"]["fan"], "unknown");
    assert_eq!(report["conditions_observed"]["fan"], Value::Null);
    assert_eq!(report["injection_requested"], req["payload"]["inject"]);
    assert_eq!(report["qualification"]["eligible"], false);
    assert!(!f.dir.join("leases").exists());
    // A layer hook has no implemented shader consumer, even if the producer advertises it.
    req["payload"]["inject"]["via"] = json!("vulkan_layer");
    std::fs::write(&path, req.to_string()).unwrap();
    let r = f
        .cmd()
        .args(["eval", "--request"])
        .arg(&path)
        .output()
        .unwrap();
    let report: Value = serde_json::from_slice(&r.stdout).unwrap();
    assert_eq!(report["status"], "ABORT");
    assert_eq!(report["resolved_config"], Value::Null);
    assert!(report["verdict"]["reason"]
        .as_str()
        .unwrap()
        .contains("inject.via=vulkan_layer unsupported"));
    assert!(!f.dir.join("leases").exists());
    assert!(!f.legacy().exists());
}
#[test]
fn unsupported_injection_returns_specific_abort_before_artifact_or_device() {
    let f = Fixture::new("unsupported");
    let mut req = shader_request();
    req["conditions"]["inject"] = json!(true);
    let path = f.dir.join("request.json");
    std::fs::write(&path, req.to_string()).unwrap();
    let r = f
        .cmd()
        .args(["eval", "--request"])
        .arg(path)
        .output()
        .unwrap();
    let report: Value = serde_json::from_slice(&r.stdout).unwrap();
    assert_eq!(report["status"], "ABORT");
    assert!(report["verdict"]["reason"]
        .as_str()
        .unwrap()
        .contains("inject=true unsupported"));
    assert!(!f.legacy().exists());
}


#[test]
fn eval_sysparam_entry_respects_existing_manual_lease_before_device_access() {
    let f = Fixture::new("sysparam-held");
    let mut owner = worker(&f, "owner", "1", "1000");
    wait_for(&f.dir.join("owner.started"));
    let mut req = shader_request();
    req["kind"] = json!("sysparam");
    req["payload"] = json!({"params":{}});
    req["workload"] = json!({"carrier":"game_replay","script":null,"seconds":30});
    // ruler stat-guards: sysparam 走精确置换检验, rounds=4 时最小可达 p (2/70) 够不着 alpha/3,
    // 收端在校验阶段就拒收 (同样不碰设备)。这里要测的是租约, 给一个判得出显著的轮数。
    req["protocol"]["rounds"] = json!(6);
    let path = f.dir.join("request.json");
    std::fs::write(&path, req.to_string()).unwrap();
    let r = f
        .cmd()
        .args(["eval", "--lock-wait", "0s", "--request"])
        .arg(path)
        .output()
        .unwrap();
    assert_eq!(r.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&r.stderr).contains("device lock busy"));
    assert!(owner.wait().unwrap().success());
}

#[test]
fn gpuop_adapter_and_perf_cannot_write_through_a_manual_lock() {
    let f = Fixture::new("gpu-held");
    let mut owner = worker(&f, "owner", "1", "1000");
    wait_for(&f.dir.join("owner.started"));
    let spv = f.dir.join("local.spv");
    std::fs::write(&spv, minimal_compute_spirv()).unwrap();
    let mut req = shader_request();
    req["payload"]["spirv_path"] = json!(spv);
    let path = f.dir.join("request.json");
    std::fs::write(&path, req.to_string()).unwrap();
    let r = f
        .cmd()
        .args(["eval", "--lock-wait", "0s", "--request"])
        .arg(path)
        .output()
        .unwrap();
    assert_eq!(r.status.code(), Some(2));
    let report: Value = serde_json::from_slice(&r.stdout).unwrap();
    assert_eq!(report["status"], "ABORT");
    let stderr = String::from_utf8_lossy(&r.stderr);
    assert!(stderr.contains("SPIR-V 预检通过"), "{stderr}");
    assert!(stderr.contains("device lock busy"), "{stderr}");
    assert_eq!(report["conditions_observed"]["root_verified"], Value::Null);
    let r = f
        .cmd()
        .args(["perf", "--lock-wait", "0s", "--serial", "fake-device", "--suspend-charging"])
        .output()
        .unwrap();
    assert_eq!(r.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&r.stderr).contains("device lock busy"));
    assert!(owner.wait().unwrap().success());
}

#[test]
fn malformed_spirv_is_rejected_by_preflight_before_lease_or_device() {
    let f = Fixture::new("malformed-spirv");
    let mut bytes = minimal_compute_spirv();
    // The first instruction claims wordCount=0 rather than the legal four words.
    bytes[20..24].copy_from_slice(&15u32.to_le_bytes());
    let spv = f.dir.join("malformed.spv");
    std::fs::write(&spv, bytes).unwrap();
    let mut req = shader_request();
    req["payload"]["spirv_path"] = json!(spv);
    let path = f.dir.join("request.json");
    std::fs::write(&path, req.to_string()).unwrap();
    let r = f
        .cmd()
        .args(["eval", "--request"])
        .arg(path)
        .output()
        .unwrap();
    assert_eq!(r.status.code(), Some(2));
    let report: Value = serde_json::from_slice(&r.stdout).unwrap();
    assert_eq!(report["status"], "ABORT");
    assert_eq!(report["conditions_observed"]["root_verified"], Value::Null);
    let stderr = String::from_utf8_lossy(&r.stderr);
    assert!(stderr.contains("SPIR-V 预检未通过, 拒绝下发真机"), "{stderr}");
    assert!(stderr.contains("wordCount=0"), "{stderr}");
    assert!(!f.dir.join("leases").exists());
    assert!(!f.legacy().exists());
}
