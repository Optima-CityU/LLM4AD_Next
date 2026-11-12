//! Configured paths need no layout initialization or retired-domain fences.
use serde_json::{json, Value};
use std::{fs, path::PathBuf, process::Command};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "pf-storage-paths-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&root).unwrap();
        Self(root)
    }
    fn pf(&self) -> Command {
        let mut c = Command::new(env!("CARGO_BIN_EXE_phonefarm"));
        c.env("PHONEFARM_STATE_DIR", self.0.join("state"))
            .env_remove("DEVLOCK_DIR")
            .env_remove("PHONEFARM_LEASE_DIR")
            .env_remove("PF_LOCK_FDS")
            .env_remove("PF_LOCK_SCOPE")
            .env_remove("PF_JOB_DEVICE_BINDING")
            .env_remove("PF_JOB_ACTOR")
            .env_remove("PF_JOB_ID")
            .env_remove("PF_JOB_LEASE_DIR");
        c
    }
    fn output(&self, args: &[&str]) -> Value {
        let output = self.pf().args(args).output().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        serde_json::from_slice(&output.stdout).unwrap()
    }
    fn take(&self) {
        let output = self
            .pf()
            .args([
                "lease",
                "--serial",
                "fake-a",
                "--timeout-ms",
                "0",
                "--",
                "/usr/bin/true",
            ])
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn clean_paths_are_read_only_until_first_lock_without_initialization() {
    let f = Fixture::new();
    let status = f.output(&["lease", "status", "--json"]);
    assert_eq!(status["status"], "clear", "{status}");
    assert!(
        !f.0.join("state").exists(),
        "status must not initialize storage"
    );
    f.take();
    let request = f.0.join("device.json");
    fs::write(
        &request,
        json!({"expected_revision":0,"device":{
        "device_id":"a","label":"A","aliases":["fake-a","wifi-a"],"enabled":true}})
        .to_string(),
    )
    .unwrap();
    f.output(&[
        "lease",
        "devices-set",
        "--request",
        request.to_str().unwrap(),
        "--json",
    ]);
    assert_eq!(
        f.output(&["lease", "resolve", "--serial", "wifi-a", "--json"])["device_id"],
        "a"
    );
    f.take();
}

#[test]
fn obsolete_layout_files_do_not_block_or_get_rewritten() {
    let f = Fixture::new();
    let root = f.0.join("state/locks");
    fs::create_dir_all(root.join("v2/unrecognized")).unwrap();
    for name in ["storage.json", "domain.json", "devlock.d"] {
        fs::write(root.join(name), b"obsolete layout evidence").unwrap();
    }
    let result = f
        .pf()
        .env("DEVLOCK_DIR", &root)
        .args([
            "lease",
            "--serial",
            "fake-a",
            "--timeout-ms",
            "0",
            "--",
            "/usr/bin/true",
        ])
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    for name in ["storage.json", "domain.json", "devlock.d"] {
        assert_eq!(
            fs::read(root.join(name)).unwrap(),
            b"obsolete layout evidence"
        );
    }
}
