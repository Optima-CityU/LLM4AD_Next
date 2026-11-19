//! Real supervisor/lease processes with fake transports; never discovers a real device.
#![cfg(unix)]
use serde_json::Value;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "pf-observation-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(root.join("bin")).unwrap();
        fs::create_dir(root.join("locks")).unwrap();
        // Keep real adb/hdc out of PATH while allowing the finite fd-owning transport proxy.
        let python = Command::new("python3").args(["-c", "import sys;print(sys.executable)"]).output().unwrap();
        std::os::unix::fs::symlink(String::from_utf8(python.stdout).unwrap().trim(), root.join("bin/python3")).unwrap();
        script(&root.join("bin/hdc"), "exit 0");
        script(
            &root.join("bin/adb"),
            r#"
if [ "$1" = devices ]; then
  : > "$FIXTURE/started"
  while [ -f "$FIXTURE/hold" ]; do /bin/sleep 0.02; done
  printf 'List of devices attached\n'
  [ ! -f "$FIXTURE/show-device" ] || printf 'fake-observer device model:Fixture\n'
  exit 0
fi
if [ "$3" = shell ]; then
  : > "$FIXTURE/probe-started"
  while [ -f "$FIXTURE/hold-shell" ]; do /bin/sleep 0.02; done
fi
if [ -f "$FIXTURE/slow-shell" ]; then exec /bin/sleep 30; fi
exit 0
"#,
        );
        Self(root)
    }
    fn cmd(&self) -> Command {
        let mut cmd = Command::new(env!("CARGO_BIN_EXE_phonefarm"));
        cmd.env("DEVLOCK_DIR", self.0.join("locks"))
            .env("PHONEFARM_LEASE_DIR", self.0.join("leases"))
            .env("ADB_BIN", self.0.join("bin/adb"))
            .env("PATH", self.0.join("bin"))
            .env("FIXTURE", &self.0)
            .env_remove("DEVLOCK_LABEL")
            .env_remove("PF_JOB_ID")
            .env_remove("PF_JOB_LEASE_DIR");
        cmd
    }
    fn register(&self, serials: &[&str]) {
        let call = |args: &[&str]| {
            let output = self.cmd().args(args).output().unwrap();
            assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        };

        for (revision, serial) in serials.iter().enumerate() {
            let request = self.0.join("register.json");
            fs::write(&request, serde_json::json!({"expected_revision":revision, "device": {
                "device_id":format!("device-{revision}"), "label":serial, "aliases":[serial], "enabled":true}}).to_string()).unwrap();
            call(&["lease", "devices-set", "--request", request.to_str().unwrap(), "--json"]);
        }
    }
    fn fleet(&self) -> std::process::Output {
        self.cmd().args(["fleet", "--json"]).output().unwrap()
    }
    fn status(&self) -> Value {
        serde_json::from_slice(
            &self
                .cmd()
                .args(["lease", "status", "--json"])
                .output()
                .unwrap()
                .stdout,
        )
        .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        if std::thread::panicking() {
            eprintln!("failed observation fixture retained: {}", self.0.display());
        } else {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
}
fn script(path: &Path, body: &str) {
    fs::write(path, format!("#!/bin/sh\n{body}\n")).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
}
fn until(mut check: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while !check() {
        assert!(Instant::now() < deadline, "fixture did not progress");
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn fleet_samples_idle_phone_and_busy_phone_temperature_without_global_lock() {
    let f = Fixture::new();
    f.register(&["fake-a", "fake-b"]);
    script(&f.0.join("bin/adb"), r#"
if [ "$1" = devices ]; then
  printf 'List of devices attached\nfake-a device\nfake-b device\n'; exit 0
fi
serial=$2; shift 2
printf '%s %s\n' "$serial" "$*" >> "$FIXTURE/calls"
if [ "$1" = shell ]; then
  printf '%s\n' '-----PF:props-----' 'Fixture' 'Fixture' '16' '36'
  printf '%s\n' '-----PF:sysbat-----' 'capacity=73' 'temp=390' 'status=Discharging'
  printf '%s\n' '-----PF:thermal-----' 'gpu 43000' '-----PF:end-----'
fi
if [ "$1" = exec-out ] && [ -f "$FIXTURE/hold-shot" ]; then
  : > "$FIXTURE/shot-started"
  while [ -f "$FIXTURE/hold-shot" ]; do /bin/sleep .01; done
fi
"#);
    let shots = f.0.join("shots");
    fs::create_dir(&shots).unwrap();
    let sampled_at = std::time::UNIX_EPOCH + Duration::from_secs(1_700_000_000);
    for serial in ["fake-a", "fake-b"] {
        let cached = shots.join(format!("{serial}.jpg"));
        image::RgbImage::new(16, 16).save(&cached).unwrap();
        fs::File::options().write(true).open(&cached).unwrap().set_modified(sampled_at).unwrap();
    }
    let ready = f.0.join("ready");
    let finish = f.0.join("finish");
    let mut holder = f.cmd().args(["lease", "--serial", "fake-b", "--", "/usr/bin/python3", "-c",
        "import pathlib,sys,time\npathlib.Path(sys.argv[1]).touch()\nwhile not pathlib.Path(sys.argv[2]).exists(): time.sleep(.01)"])
        .arg(&ready).arg(&finish).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
    until(|| ready.exists());
    let output = f.cmd().args(["fleet", "--json", "--screenshot-dir"])
        .arg(f.0.join("shots")).output().unwrap();
    let still_held = holder.try_wait().unwrap().is_none();
    fs::write(&finish, "").unwrap();
    assert!(holder.wait_with_output().unwrap().status.success());
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let value: Value = serde_json::from_slice(&output.stdout).unwrap();
    let devices = value["devices"].as_array().unwrap();
    let a = devices.iter().find(|card| card["serial"] == "fake-a").unwrap();
    let b = devices.iter().find(|card| card["serial"] == "fake-b").unwrap();
    assert_eq!(a["probe_depth"], "full");
    assert_eq!(b["probe_depth"], "light");
    assert_eq!(b["battery"]["level_pct"], 73);
    assert_eq!(b["thermal"]["gpu_c"], 43.0);
    assert_eq!(a["screenshot"]["fresh"], false, "failed screenshot must retain the last image");
    assert_eq!(chrono::DateTime::parse_from_rfc3339(a["screenshot"]["at"].as_str().unwrap()).unwrap().timestamp(), 1_700_000_000);
    assert_eq!(b["screenshot"]["fresh"], false);
    assert_eq!(chrono::DateTime::parse_from_rfc3339(b["screenshot"]["at"].as_str().unwrap()).unwrap().timestamp(), 1_700_000_000);
    assert!(b["screenshot"]["age_s"].as_u64().unwrap() > 60);
    assert_eq!(b["screenshot"].as_object().unwrap().len(), 4);
    assert!(still_held, "sampling must not wait for B to release");
    let calls = fs::read_to_string(f.0.join("calls")).unwrap();
    assert!(calls.contains("fake-a exec-out screencap -p"), "{calls}");
    assert!(!calls.contains("fake-b exec-out"), "{calls}");
    assert!(!f.0.join("locks/v2/global/devlock.d").exists());

    fs::write(f.0.join("hold-shot"), "").unwrap();
    let sampler = f.cmd().args(["fleet", "--json", "--serial", "fake-a", "--screenshot-dir"])
        .arg(f.0.join("shots")).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
    until(|| f.0.join("shot-started").exists());
    let during = f.status();
    let other = f.cmd().args(["lease", "--serial", "fake-b", "--timeout-ms", "0", "--", "/usr/bin/true"])
        .output().unwrap();
    let same = f.cmd().args(["lease", "--serial", "fake-a", "--timeout-ms", "0", "--", "/usr/bin/true"])
        .output().unwrap();
    let binding = f.cmd().args(["lease", "resolve", "--serial", "fake-a", "--json"]).output().unwrap();
    let outer = Command::new("/usr/bin/zsh")
        .arg(concat!(env!("CARGO_MANIFEST_DIR"), "/../tools/devlock"))
        .args(["run", "raw-writer-a", "--", "/usr/bin/true"])
        .env("PHONEFARM_BIN", env!("CARGO_BIN_EXE_phonefarm"))
        .env("DEVLOCK_DIR", f.0.join("locks")).env("PHONEFARM_LEASE_DIR", f.0.join("leases"))
        .env("DEVLOCK_WAIT_SECONDS", "0").env("PF_JOB_DEVICE_BINDING", String::from_utf8(binding.stdout).unwrap())
        .output().unwrap();
    fs::remove_file(f.0.join("hold-shot")).unwrap();
    assert!(sampler.wait_with_output().unwrap().status.success());
    assert!(other.status.success(), "B must remain usable during A's screenshot");
    assert!(!same.status.success(), "A's screenshot must exclude a strong writer");
    assert!(!outer.status.success(), "A's screenshot must also exclude a raw devlock writer");
    assert_eq!(during["device_scopes"]["global"]["status"], "clear");
    assert_eq!(during["device_scopes"]["unknown"], false, "A's observation must not block scheduling B");
}

#[test]
fn enumeration_does_not_take_a_global_lock() {
    let f = Fixture::new();
    fs::write(f.0.join("hold"), "").unwrap();
    let sampler = f
        .cmd()
        .args(["fleet", "--json"])
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    until(|| f.0.join("started").exists());
    assert_eq!(f.status()["status"], "clear");
    assert!(!f.0.join("locks/devlock.d/phonefarm-lease.json").exists());
    let competing = f
        .cmd()
        .args([
            "lease",
            "--serial",
            "fake-writer",
            "--timeout-ms",
            "0",
            "--",
            "/usr/bin/true",
        ])
        .output()
        .unwrap();
    assert!(competing.status.success());
    if Command::new("zsh").arg("-c").arg("exit 0").status().is_ok() {
        let legacy = Command::new("zsh")
            .arg(concat!(env!("CARGO_MANIFEST_DIR"), "/../tools/devlock"))
            .args(["run", "writer", "--", "/usr/bin/true"])
            .env("DEVLOCK_DIR", f.0.join("locks"))
            .env("PHONEFARM_BIN", env!("CARGO_BIN_EXE_phonefarm"))
            .env("DEVLOCK_WAIT_SECONDS", "0")
            .output()
            .unwrap();
        assert_eq!(legacy.status.code(), Some(0));
    }
    fs::remove_file(f.0.join("hold")).unwrap();
    let output = sampler.wait_with_output().unwrap();
    assert_eq!(
        output.status.code(),
        Some(1),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["observation"], "sampled");
    assert_eq!(result["devices"], serde_json::json!([]));
    assert_eq!(f.status()["status"], "clear");
    assert!(!f.0.join("locks/devlock.d").exists());
}

#[test]
fn retired_records_do_not_block_full_observation() {
    let f = Fixture::new();
    fs::write(f.0.join("show-device"), "").unwrap();
    for entry in [
        "devlock.queue",
        "devlock.qmutex",
        "phonefarm-lease.d",
        "devlock.d",
    ] {
        let path = f.0.join("locks").join(entry);
        if entry == "devlock.queue" {
            fs::write(&path, "writer\n").unwrap();
        } else {
            fs::create_dir(&path).unwrap();
        }
        if entry == "devlock.d" {
            fs::write(path.join("owner"), "same since fixture\n").unwrap();
        }
        let output = f
            .cmd()
            .env("DEVLOCK_LABEL", "same")
            .args([
                "fleet",
                "--json",
                "--lock-label",
                "same",
                "--probe-while-locked",
            ])
            .output()
            .unwrap();
        assert_eq!(output.status.code(), Some(0));
        let result: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(result["observation"], "sampled");
        assert_eq!(result["devices"][0]["probe_depth"], "full");
        assert!(f.0.join("started").exists());
        fs::remove_file(f.0.join("started")).unwrap();
        if path.is_dir() {
            fs::remove_dir_all(path).unwrap();
        } else {
            fs::remove_file(path).unwrap();
        }
    }
    let direct = f
        .cmd()
        .args(["__observe-worker", "fleet", "--json"])
        .stdin(Stdio::null())
        .output()
        .unwrap();
    assert_eq!(direct.status.code(), Some(90));
    assert!(!f.0.join("started").exists());
}

#[test]
fn transport_timeout_leaves_no_marker_and_a_later_full_sample_can_retry() {
    let f = Fixture::new();
    f.register(&["fake-observer"]);
    fs::write(f.0.join("show-device"), "").unwrap();
    fs::write(f.0.join("slow-shell"), "").unwrap();
    let output = f
        .cmd()
        .args(["fleet", "--json", "--timeout-ms", "30"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(3));
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["observation"], "error");
    assert!(result["devices"].is_null());
    assert!(!f.0.join("locks/devlock.d/phonefarm-lease.json").exists());
    assert_eq!(f.status()["status"], "clear");
    fs::remove_file(f.0.join("slow-shell")).unwrap();
    let output = f.fleet();
    assert_eq!(output.status.code(), Some(0));
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["observation"], "sampled");
    assert_eq!(result["devices"][0]["probe_depth"], "full");
    assert_eq!(f.status()["status"], "clear");
}

#[test]
fn killed_supervisor_releases_fd_after_worker_exit_without_marker() {
    let f = Fixture::new();
    f.register(&["fake-observer"]);
    fs::write(f.0.join("show-device"), "").unwrap();
    fs::write(f.0.join("hold-shell"), "").unwrap();
    let mut supervisor = f
        .cmd()
        .args(["fleet", "--json"])
        .stdout(Stdio::null())
        .spawn()
        .unwrap();
    until(|| f.0.join("probe-started").exists());
    let competing = f.cmd().args(["lease", "--serial", "fake-observer", "--timeout-ms", "0",
        "--", "/usr/bin/true"]).output().unwrap();
    assert!(!competing.status.success(), "the sampling worker must hold this device");
    assert_eq!(f.status()["device_scopes"]["global"]["status"], "clear");
    assert!(!f.0.join("locks/devlock.d/phonefarm-lease.json").exists());
    supervisor.kill().unwrap();
    supervisor.wait().unwrap();
    until(|| f.status()["status"] == "clear");
    assert!(f.status()["entries"].as_array().unwrap().is_empty());
    fs::remove_file(f.0.join("hold-shell")).unwrap();
    let output = f.fleet();
    assert_eq!(output.status.code(), Some(0));
    assert_eq!(
        serde_json::from_slice::<Value>(&output.stdout).unwrap()["observation"],
        "sampled"
    );
}

#[test]
fn bound_screen_samples_another_device_without_widening_its_worker() {
    let f = Fixture::new();
    let json_call = |args: &[&str]| -> Value {
        let output = f.cmd().args(args).output().unwrap();
        assert!(output.status.success(), "{} {}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        serde_json::from_slice(&output.stdout).unwrap()
    };
    assert_eq!(json_call(&["screen", "capabilities", "--json"])["device_binding_v1"], true);

    for (revision, id, aliases) in [(0, "A", vec!["screen-A"]), (1, "B", vec!["screen-B", "screen-B-wifi"])] {
        let request = f.0.join("register.json");
        fs::write(&request, serde_json::json!({"expected_revision":revision,
            "device":{"device_id":id,"label":id,"aliases":aliases,"enabled":true}}).to_string()).unwrap();
        json_call(&["lease", "devices-set", "--request", request.to_str().unwrap(), "--json"]);
    }
    let a = json_call(&["lease", "resolve", "--serial", "screen-A", "--json"]);
    let b = json_call(&["lease", "resolve", "--serial", "screen-B", "--json"]);
    let alias = json_call(&["lease", "resolve", "--serial", "screen-B-wifi", "--json"]);
    let png = f.0.join("screen.png");
    image::RgbImage::from_fn(128, 128, |x, y| image::Rgb([
        (x * 31 + y * 17) as u8, (x * 13 ^ y * 43) as u8, (x * y + y * 7) as u8,
    ])).save(&png).unwrap();
    assert!(fs::metadata(&png).unwrap().len() > 1000);
    script(&f.0.join("bin/adb"), r#"
[ "$1" = '-s' ] || exit 91
serial=$2; shift 2
printf '%s %s\n' "$serial" "$*" >> "$FIXTURE/calls"
case "$*" in
  'shell wm size') printf 'Physical size: 128x128\n' ;;
  'exec-out screencap -p')
    if [ "$serial" = screen-B ]; then
      : > "$FIXTURE/started-B"
      ticks=0
      while [ -f "$FIXTURE/hold-B" ] && [ "$ticks" -lt 500 ]; do
        /bin/sleep 0.02; ticks=$((ticks + 1))
      done
      [ ! -f "$FIXTURE/hold-B" ] || exit 92
    fi
    /bin/cat "$FIXTURE/screen.png" ;;
  *) exit 93 ;;
esac
"#);
    let capture = |serial: &str, binding: Option<&Value>, name: &str| {
        let mut cmd = f.cmd();
        cmd.args(["screen", "capture", "--serial", serial, "--out"])
            .arg(f.0.join(name)).arg("--json");
        if let Some(binding) = binding { cmd.arg("--device-binding").arg(binding.to_string()); }
        cmd
    };
    let typed = Command::new("/usr/bin/zsh")
        .arg(concat!(env!("CARGO_MANIFEST_DIR"), "/../tools/devlock"))
        .args(["run", "typed-outer", "--", env!("CARGO_BIN_EXE_phonefarm"), "screen", "capture", "--serial", "screen-A", "--out"])
        .arg(f.0.join("typed.png"))
        .args(["--json", "--device-binding"])
        .arg(a.to_string())
        .env("DEVLOCK_DIR", f.0.join("locks"))
        .env("PHONEFARM_LEASE_DIR", f.0.join("leases"))
        .env("ADB_BIN", f.0.join("bin/adb"))
        .env("PATH", format!("{}:{}", f.0.join("bin").display(), std::env::var("PATH").unwrap_or_default()))
        .env("FIXTURE", &f.0)
        .env("PHONEFARM_BIN", env!("CARGO_BIN_EXE_phonefarm"))
        .env("PF_JOB_DEVICE_BINDING", a.to_string())
        .env("DEVLOCK_WAIT_SECONDS", "5")
        .output()
        .unwrap();
    assert!(typed.status.success(), "{} {}", String::from_utf8_lossy(&typed.stdout), String::from_utf8_lossy(&typed.stderr));
    assert!(!f.0.join("locks/v2/global/devlock.d").exists());
    // The only concurrent child is owned here and bounded by the observation worker.
    struct Owned(Option<std::process::Child>);
    impl Drop for Owned {
        fn drop(&mut self) { if let Some(child) = &mut self.0 { let _ = child.kill(); let _ = child.wait(); } }
    }
    fs::write(f.0.join("hold-B"), "").unwrap();
    let mut held = Owned(Some(capture("screen-B", Some(&b), "B").stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap()));
    until(|| f.0.join("started-B").exists());
    let status = f.status();
    let entry = status["entries"].as_array().unwrap().iter().find(|entry| entry["serial"] == "screen-B").unwrap();
    assert_eq!(entry["device_binding"], b);
    assert_eq!(entry["serial"], "screen-B");
    assert_eq!(entry["device_lock_held"], true);
    let worker = entry["holder_pid"].as_i64().unwrap() as i32;
    assert_ne!(worker as u32, held.0.as_ref().unwrap().id(), "the sampling worker itself must hold the fd");
    let output = capture("screen-A", Some(&a), "A").output().unwrap();
    assert!(output.status.success(), "{} {}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["device_binding"], a);
    assert_eq!(result["lease_scope"], "device");
    assert_eq!(result["serial"], "screen-A");
    assert!(f.0.join("A/capture.jpg").is_file());
    let before = fs::read(f.0.join("calls")).unwrap();
    let alias_output = capture("screen-B-wifi", Some(&alias), "alias").output().unwrap();
    assert_eq!(serde_json::from_slice::<Value>(&alias_output.stdout).unwrap()["state"], "deferred");
    assert_eq!(serde_json::from_slice::<Value>(&f.fleet().stdout).unwrap()["observation"], "sampled");
    assert_eq!(fs::read(f.0.join("calls")).unwrap(), before, "refused alias and empty light fleet must not issue device probes");
    fs::remove_file(f.0.join("hold-B")).unwrap();
    let output = held.0.take().unwrap().wait_with_output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    assert_eq!(serde_json::from_slice::<Value>(&output.stdout).unwrap()["device_binding"], b);
    assert!(unsafe { libc::kill(-worker, 0) } < 0, "owned worker group must have exited");
    assert_eq!(f.status()["status"], "clear");
    let output = capture("screen-A", None, "legacy").output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let legacy: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert!(legacy.get("device_binding").is_none() && legacy.get("lease_scope").is_none());
    assert_eq!(f.status()["status"], "clear");
    println!("scoped screen A completed while B held; alias stayed deferred; fleet used light observation; worker PGID {worker} gone");
}
