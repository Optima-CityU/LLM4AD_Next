//! Actual host processes and CLI; the explicit fake serial never resolves a device.
#![cfg(unix)]
use serde_json::{json, Value};
use std::path::PathBuf;
use std::process::{Command, Output};

struct Fixture {
    dir: PathBuf,
    retain: bool,
}
impl Fixture {
    fn new(label: &str) -> Self {
        let root = std::env::var_os("PHONEFARM_BACKPORT_FIXTURE_ROOT");
        let retain = root.is_some();
        let dir = root
            .map(PathBuf::from)
            .unwrap_or_else(std::env::temp_dir)
            .join(format!(
                "lease-backport-{label}-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
        std::fs::create_dir_all(&dir).unwrap();
        Self { dir, retain }
    }
    fn status(&self) -> Value {
        let output = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
            .current_dir(&self.dir)
            .env("DEVLOCK_DIR", self.dir.join("legacy"))
            .args(["lease", "status", "--json"])
            .output()
            .unwrap();
        assert!(output.status.success());
        serde_json::from_slice(&output.stdout).unwrap()
    }
    fn save(&self, name: &str, r: &Output) {
        std::fs::write(
            self.dir.join(format!("{name}.json")),
            json!({
                "exit":r.status.code(), "stdout":String::from_utf8_lossy(&r.stdout),
                "stderr":String::from_utf8_lossy(&r.stderr), "device_called":false
            })
            .to_string(),
        )
        .unwrap();
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        if !self.retain {
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }
}
#[test]
fn status_without_serial_leaves_missing_roots_missing() {
    let f = Fixture::new("readonly-status");
    let mut status = f.status();
    let storage = status.as_object_mut().unwrap().remove("storage").unwrap();
    assert_eq!(storage["schema_version"], 1);
    assert_eq!(storage["error"], Value::Null);
    assert_eq!(storage["devlock_dir"], json!(f.dir.canonicalize().unwrap().join("legacy")));
    assert_eq!(status["status"], "clear");
    assert_eq!(status["bridge_held"], false);
    assert_eq!(status["entries"], json!([]));
    assert_eq!(status["error"], Value::Null);
    assert_eq!(status["device_scopes"]["global"]["status"], "clear");
    assert_eq!(std::fs::read_dir(&f.dir).unwrap().count(), 0);
}

#[test]
fn preflight_fix_preserves_results_and_ignores_retired_recovery_records() {
    use std::os::unix::fs::PermissionsExt;
    let f = Fixture::new("preflight-fix");
    let fake = f.dir.join("fake-adb");
    std::fs::write(&fake, r#"#!/usr/bin/env python3
import pathlib, sys
root=pathlib.Path(__file__).parent
cmd=' '.join(sys.argv[1:])
with (root/'fake-calls').open('a') as log: log.write(cmd+'\n')
if 'echo 0 > /sys/kernel/fan/fan_enable' in cmd: (root/'fan-fixed').touch()
elif 'fan_enable' in cmd: print('0' if (root/'fan-fixed').exists() else '1')
elif 'set-volume 3 0' in cmd: (root/'volume-fixed').touch()
elif 'media_session volume' in cmd: print('volume is '+('0' if (root/'volume-fixed').exists() else '2'))
elif '/sys/class/thermal' in cmd: print('cpu-0 40000')
"#).unwrap();
    std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
    let devlock = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join("tools/devlock");
    let command = || {
        let mut c = Command::new(env!("CARGO_BIN_EXE_phonefarm"));
        c.current_dir(&f.dir).env("DEVLOCK_DIR", f.dir.join("legacy"))
            .env("PHONEFARM_LEASE_DIR", f.dir.join("leases"))
            .env("PHONEFARM_LEASE_TIMEOUT_MS", "50")
            .env("DEVLOCK_BIN", &devlock).env("ADB_BIN", &fake)
            .args(["preflight", "--fix", "--serial", "fake-device", "--json", "--lock-wait", "0s"]);
        c
    };
    let fixed = command().output().unwrap();
    f.save("fix", &fixed);
    assert!(fixed.status.success(), "{}", String::from_utf8_lossy(&fixed.stderr));
    assert!(f.dir.join("fan-fixed").exists());
    assert!(f.dir.join("volume-fixed").exists());
    assert_eq!(f.status()["status"], "clear");
    let nested = f.dir.join("legacy/phonefarm-lease.d");
    std::fs::create_dir(&nested).unwrap();
    let marker = nested.join("phonefarm-lease.json");
    std::fs::write(&marker, r#"{"format":1,"recovery_required":true}"#).unwrap();
    let before = std::fs::read(&marker).unwrap();
    let repeated = command().output().unwrap();
    f.save("repeated", &repeated);
    assert!(repeated.status.success(), "{}", String::from_utf8_lossy(&repeated.stderr));
    let calls = std::fs::read(f.dir.join("fake-calls")).unwrap();
    assert_eq!(std::fs::read(marker).unwrap(), before);

    // Invalid storage fails before lock waiting or any probe.
    let start = std::time::Instant::now();
    let invalid = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
        .env("DEVLOCK_DIR", "relative-invalid")
        .env("DEVLOCK_BIN", &devlock).env("ADB_BIN", &fake)
        .args(["preflight", "--fix", "--serial", "fake-device"]).output().unwrap();
    assert_eq!(invalid.status.code(), Some(2));
    assert!(start.elapsed() < std::time::Duration::from_secs(5));
    assert_eq!(std::fs::read(f.dir.join("fake-calls")).unwrap(), calls);
}
