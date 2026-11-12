//! Real host processes and kernel locks; transport and state roots are isolated.
#![cfg(unix)]
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::os::fd::AsRawFd;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "pf-fd-lock-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(root.join("locks")).unwrap();
        let f = Self(root);
        for (revision, id) in ["a", "b"].into_iter().enumerate() {
            let request = f.0.join("register.json");
            fs::write(&request, json!({"expected_revision": revision, "device": {
                "device_id": id, "label": id, "aliases": [format!("fake-{id}"), format!("wifi-{id}")], "enabled": true
            }}).to_string()).unwrap();
            let out = f
                .pf()
                .args(["lease", "devices-set", "--request"])
                .arg(request)
                .arg("--json")
                .output()
                .unwrap();
            assert!(
                out.status.success(),
                "{}",
                String::from_utf8_lossy(&out.stderr)
            );
        }
        f
    }
    fn pf(&self) -> Command {
        let mut c = Command::new(env!("CARGO_BIN_EXE_phonefarm"));
        c.env("DEVLOCK_DIR", self.0.join("locks"))
            .env("PHONEFARM_LEASE_DIR", self.0.join("devices"))
            .env("PHONEFARM_BIN", env!("CARGO_BIN_EXE_phonefarm"))
            .env_remove("DEVLOCK_LABEL")
            .env_remove("DEVLOCK_INSTANCE_ID")
            .env_remove("PF_JOB_DEVICE_BINDING")
            .env_remove("PF_JOB_ACTOR")
            .env_remove("PF_JOB_ID")
            .env_remove("PF_JOB_LEASE_DIR")
            .env_remove("PF_LOCK_FDS")
            .env_remove("PF_LOCK_SCOPE");
        c
    }
    fn try_device(&self, serial: &str) -> std::process::Output {
        self.pf()
            .args([
                "lease",
                "--serial",
                serial,
                "--timeout-ms",
                "0",
                "--",
                "/usr/bin/true",
            ])
            .output()
            .unwrap()
    }
    fn set_a_enabled(&self, revision: u64, enabled: bool) {
        let request = self.0.join("enable-a.json");
        fs::write(&request, json!({"expected_revision": revision, "device": {
            "device_id": "a", "label": "a", "aliases": ["fake-a", "wifi-a"], "enabled": enabled
        }}).to_string()).unwrap();
        let out = self.pf().args(["lease", "devices-set", "--request"])
            .arg(request).arg("--json").output().unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    }
    fn until(&self, path: &str) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while !self.0.join(path).exists() {
            assert!(
                Instant::now() < deadline,
                "fixture did not reach {path}: {}",
                self.0.display()
            );
            std::thread::sleep(Duration::from_millis(10));
        }
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        if !std::thread::panicking() {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
}
struct Group(Child);
impl Drop for Group {
    fn drop(&mut self) {
        unsafe {
            libc::kill(-(self.0.id() as i32), libc::SIGKILL);
        }
        let _ = self.0.wait();
    }
}

#[test]
fn disabled_registered_device_cannot_acquire_a_lock() {
    let f = Fixture::new();
    f.set_a_enabled(2, false);
    for serial in ["fake-a", "wifi-a"] {
        let out = f.try_device(serial);
        assert!(!out.status.success(), "disabled device acquired a lock through {serial}");
        assert!(String::from_utf8_lossy(&out.stderr)
            .contains("这台手机在登记里停用了；要用请先在登记里启用"));
    }
    let adb = f.0.join("fake-adb");
    fs::write(&adb, "#!/bin/sh\nprintf called > \"$PF_TEST_TRANSPORT_CALLED\"\nexit 42\n").unwrap();
    fs::set_permissions(&adb, fs::Permissions::from_mode(0o755)).unwrap();
    for args in [
        vec!["exec", "--serial", "fake-a", "--yes", "true"],
        vec!["exec", "--serial", "fake-b", "--serial", "fake-a", "--yes", "true"],
        vec!["lease", "--", env!("CARGO_BIN_EXE_phonefarm"), "lease", "--serial", "fake-a", "--", "/usr/bin/true"],
        vec!["lease", "resolve", "--serial", "fake-a", "--json"],
    ] {
        let out = f.pf().args(&args).env("ADB_BIN", &adb)
            .env("PF_TEST_TRANSPORT_CALLED", f.0.join("transport-called")).output().unwrap();
        assert!(!out.status.success(), "disabled device admitted by {args:?}");
        let report = format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
        assert!(report.contains("这台手机在登记里停用了；要用请先在登记里启用"), "{args:?}: {report}");
    }
    assert!(!f.0.join("transport-called").exists(), "disabled device reached adb");
}

#[test]
fn reenabling_a_registered_device_allows_the_same_serial_to_acquire() {
    let f = Fixture::new();
    f.set_a_enabled(2, false);
    f.set_a_enabled(3, true);
    let out = f.try_device("fake-a");
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
}

#[test]
fn an_unregistered_serial_still_acquires_with_a_disabled_device_in_the_registry() {
    let f = Fixture::new();
    f.set_a_enabled(2, false);
    let out = f.try_device("unregistered-serial");
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
}

#[test]
fn first_registration_between_device_and_farm_locks_rekeys_the_waiter() {
    let f = Fixture::new();
    let farm = fs::OpenOptions::new().read(true).write(true)
        .open(f.0.join("locks/phonefarm-bridge.lock")).unwrap();
    farm.lock().unwrap();
    let mut waiter = Group(f.pf().args(["lease", "--serial", "raw-new", "--", "/bin/sh", "-c",
        "touch \"$1/rekey-ready\"; while [ ! -f \"$1/rekey-finish\" ]; do sleep .02; done", "holder"])
        .arg(&f.0).process_group(0).spawn().unwrap());
    let raw_path = f.0.join("devices/by-device-id")
        .join(format!("{:x}.lock", Sha256::digest(b"raw-new")));
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Ok(raw) = fs::OpenOptions::new().read(true).write(true).open(&raw_path) {
            if matches!(raw.try_lock(), Err(std::fs::TryLockError::WouldBlock)) { break; }
        }
        assert!(Instant::now() < deadline, "waiter never acquired its initial raw serial key");
        std::thread::sleep(Duration::from_millis(10));
    }
    let request = f.0.join("register-new.json");
    fs::write(&request, json!({"expected_revision":2,"device":{
        "device_id":"new-device","label":"new","aliases":["raw-new","wifi-new"],"enabled":true
    }}).to_string()).unwrap();
    // The registrar borrows this real farm EX, so registration deterministically
    // commits while the first task is parked between device EX and farm SH.
    let fd = farm.as_raw_fd();
    let mut registrar = f.pf();
    registrar.args(["lease", "devices-set", "--request"]).arg(request).arg("--json")
        .env("PF_LOCK_FDS", fd.to_string())
        .env("PF_LOCK_SCOPE", json!({"key":null,"serial":null,"binding":null}).to_string());
    unsafe { registrar.pre_exec(move || {
        if libc::fcntl(fd, libc::F_SETFD, 0) < 0 { return Err(std::io::Error::last_os_error()); }
        Ok(())
    }); }
    let update = registrar.output().unwrap();
    assert!(update.status.success(), "{}", String::from_utf8_lossy(&update.stderr));
    drop(farm);
    f.until("rekey-ready");
    for serial in ["raw-new", "wifi-new"] {
        assert!(!f.try_device(serial).status.success(), "same phone admitted twice through {serial}");
    }
    assert!(f.try_device("fake-b").status.success(), "another phone must remain usable");
    fs::write(f.0.join("rekey-finish"), b"").unwrap();
    assert!(waiter.0.wait().unwrap().success());
    assert!(f.try_device("wifi-new").status.success());
}

#[test]
fn unregistered_device_scope_survives_nested_tools_devlock() {
    let f = Fixture::new();
    let devlock = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tools/devlock");
    let out = f.pf().args(["lease", "--serial", "raw-device", "--"])
        .arg(devlock).args(["run", "inner", "--", "/bin/sh", "-c", "printf '%s' \"$PF_LOCK_SCOPE\""])
        .output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let scope: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(scope["serial"], "raw-device");
    assert_eq!(scope["key"], "raw-device");
    let widened = f.pf().args(["lease", "--serial", "raw-device", "--"])
        .arg(env!("CARGO_BIN_EXE_phonefarm"))
        .args(["lease", "--serial", "fake-b", "--", "/usr/bin/true"]).output().unwrap();
    assert!(!widened.status.success(), "inherited device scope must not widen");
}

#[test]
fn unregistered_device_scope_reaches_nested_perf_without_discovery() {
    use std::os::unix::fs::PermissionsExt;
    let f = Fixture::new();
    let adb = f.0.join("fake-adb");
    fs::write(&adb, "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FAKE_ADB_CALLS\"\nexit 1\n").unwrap();
    fs::set_permissions(&adb, fs::Permissions::from_mode(0o700)).unwrap();
    let calls = f.0.join("adb-calls");
    let out = f.pf().env("ADB_BIN", adb).env("FAKE_ADB_CALLS", &calls)
        .args(["lease", "--serial", "raw-device", "--"])
        .arg(env!("CARGO_BIN_EXE_phonefarm"))
        .args(["perf", "--rounds", "1", "--json"]).output().unwrap();
    assert!(calls.exists(), "perf never reached fake adb: {}", String::from_utf8_lossy(&out.stderr));
    let calls = fs::read_to_string(calls).unwrap();
    assert!(calls.lines().all(|line| line.starts_with("-s raw-device ")), "{calls}");
    assert!(!out.status.success(), "fake device has no heartbeat; no measurement may be invented");
}

#[test]
fn another_actor_can_acquire_immediately_after_task_end_but_not_during_task() {
    let f = Fixture::new();
    let mut owner = Group(
        f.pf()
            .env("PF_JOB_ACTOR", "actor-a")
            .args([
                "lease",
                "--serial",
                "fake-a",
                "--",
                "/bin/sh",
                "-c",
                "touch \"$1/actor-ready\"; while [ ! -f \"$1/actor-finish\" ]; do sleep .02; done",
                "holder",
            ])
            .arg(&f.0)
            .process_group(0)
            .spawn()
            .unwrap(),
    );
    f.until("actor-ready");
    let take = || {
        f.pf()
            .env("PF_JOB_ACTOR", "actor-b")
            .args([
                "lease",
                "--serial",
                "wifi-a",
                "--timeout-ms",
                "0",
                "--",
                "/usr/bin/true",
            ])
            .output()
            .unwrap()
    };
    assert!(
        !take().status.success(),
        "another actor entered during the task"
    );
    fs::write(f.0.join("actor-finish"), b"").unwrap();
    assert!(owner.0.wait().unwrap().success());
    let acquired = take();
    assert!(
        acquired.status.success(),
        "{}",
        String::from_utf8_lossy(&acquired.stderr)
    );
    // Existing cooldown files are historical data, not admission state.
    let lock = fs::read_dir(f.0.join("devices/by-device-id"))
        .unwrap()
        .map(|item| item.unwrap().path())
        .find(|path| path.extension().is_some_and(|ext| ext == "lock"))
        .unwrap();
    let old = lock.with_extension("cooldown.json");
    fs::write(&old, b"old cooldown evidence").unwrap();
    assert!(take().status.success());
    assert_eq!(fs::read(&old).unwrap(), b"old cooldown evidence");
}

#[test]
fn killing_owner_keeps_child_fd_exclusive_until_the_child_exits() {
    let f = Fixture::new();
    let script = r#"import json,os,sys,time
from pathlib import Path
root=Path(sys.argv[1])
fds=[int(fd) for fd in os.environ.get('PF_LOCK_FDS','').split(',') if fd]
for fd in fds: os.fstat(fd)
(root/'fds.json.tmp').write_text(json.dumps(fds))
os.replace(root/'fds.json.tmp', root/'fds.json')
while not (root/'finish').exists(): time.sleep(.01)
"#;
    let mut owner = Group(
        f.pf()
            .args(["lease", "--serial", "fake-a", "--", "python3", "-c", script])
            .arg(&f.0)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()
            .unwrap(),
    );
    f.until("fds.json");
    let fds: Vec<i32> = serde_json::from_slice(&fs::read(f.0.join("fds.json")).unwrap()).unwrap();
    owner.0.kill().unwrap();
    owner.0.wait().unwrap();
    let while_child_alive = f.try_device("wifi-a");
    let other = f.try_device("fake-b");
    fs::write(f.0.join("finish"), "").unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    let after_child = loop {
        let result = f.try_device("fake-a");
        if result.status.success() || Instant::now() >= deadline {
            break result;
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    assert_eq!(
        fds.len(),
        2,
        "device command must inherit the actual device and farm descriptors"
    );
    assert!(
        !while_child_alive.status.success(),
        "same alias entered while the original child was alive"
    );
    assert!(
        other.status.success(),
        "other device blocked: {}",
        String::from_utf8_lossy(&other.stderr)
    );
    assert!(
        after_child.status.success(),
        "last fd close must release, without marker recovery: {}",
        String::from_utf8_lossy(&after_child.stderr)
    );
}

#[test]
fn old_recovery_records_never_block_a_new_fd_owner() {
    let f = Fixture::new();
    let marker = f.0.join("locks/v2/global/devlock.d");
    fs::create_dir_all(&marker).unwrap();
    let bytes = json!({"format": 1, "producer": "phonefarm", "serial": "fake-a", "recovery_required": true});
    fs::write(marker.join("phonefarm-lease.json"), bytes.to_string()).unwrap();
    fs::write(marker.join("owner"), "old owner since fixture\n").unwrap();
    let out = f.try_device("fake-a");
    assert!(
        out.status.success(),
        "old recovery record blocked kernel acquisition: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert_eq!(
        serde_json::from_slice::<Value>(&fs::read(marker.join("phonefarm-lease.json")).unwrap())
            .unwrap(),
        bytes
    );
}

#[test]
fn adb_and_hdc_daemons_do_not_inherit_task_descriptors() {
    // Other libtest threads can fork while this fixture writes its executable,
    // briefly copying the write handle and causing ETXTBSY. Keep fixture creation
    // and the actual daemon/fd assertions in their own process, without retries.
    if std::env::var_os("PF_FD_DAEMON_TEST_CHILD").is_none() {
        let output = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "adb_and_hdc_daemons_do_not_inherit_task_descriptors", "--nocapture"])
            .env("PF_FD_DAEMON_TEST_CHILD", "1").output().unwrap();
        assert!(output.status.success(), "{}\n{}", String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr));
        return;
    }
    use std::os::unix::fs::PermissionsExt;
    for name in ["adb", "hdc"] {
        let f = Fixture::new();
        let program = f.0.join(format!("fake-{name}"));
        fs::write(&program, r#"#!/usr/bin/env python3
import json,os,signal,sys,time
from pathlib import Path
root=Path(os.environ['DAEMON_TEST_ROOT'])
args=sys.argv[1:]
if args[:1]==['-p']: args=args[1:]
if args[0]=='kill-server':
    if (root/'server.pid').exists():
        os.kill(int((root/'server.pid').read_text()),signal.SIGKILL)
        (root/'server.pid').unlink()
    sys.exit(0)
if (root/'server.pid').exists(): sys.exit(0)
ready,notify=os.pipe()
pid=os.fork()
if pid:
    os.close(notify)
    assert os.read(ready,1)==b'1'
    sys.exit(0)
os.close(ready)
os.setsid()
signal.alarm(15)
held=[]
for fd in range(3,256):
    try:
        st=os.fstat(fd)
        if any((st.st_dev,st.st_ino)==(p.stat().st_dev,p.stat().st_ino) for p in (root/'devices'/'by-device-id').glob('*.lock')): held.append(fd)
    except OSError: pass
for fd in range(3): os.close(fd)
(root/'server.pid').write_text(str(os.getpid()))
(root/'daemon-fds.json.tmp').write_text(json.dumps(held))
os.replace(root/'daemon-fds.json.tmp', root/'daemon-fds.json')
os.write(notify,b'1')
os.close(notify)
time.sleep(15)
"#).unwrap();
        fs::set_permissions(&program, fs::Permissions::from_mode(0o755)).unwrap();
        Command::new(&program)
            .arg("kill-server")
            .env("DAEMON_TEST_ROOT", &f.0)
            .status()
            .unwrap();
        let command = format!("test -n \"$PF_LOCK_FDS\" && {name} start-server");
        let output = f
            .pf()
            .env("ADB_BIN", &program)
            .env("HDC_BIN", &program)
            .env("DAEMON_TEST_ROOT", &f.0)
            .args([
                "lease", "--serial", "fake-a", "--", "/bin/sh", "-c", &command,
            ])
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        f.until("daemon-fds.json");
        let inherited: Vec<i32> =
            serde_json::from_slice(&fs::read(f.0.join("daemon-fds.json")).unwrap()).unwrap();
        let next = f.try_device("fake-a");
        Command::new(&program)
            .arg("kill-server")
            .env("DAEMON_TEST_ROOT", &f.0)
            .status()
            .unwrap();
        assert!(
            inherited.is_empty(),
            "{name} daemon inherited {inherited:?}"
        );
        assert!(
            next.status.success(),
            "{name} server retained the device lock"
        );
    }
}

#[test]
#[cfg(target_os = "linux")]
fn real_adb_first_server_start_does_not_keep_the_device_locked() {
    let f = Fixture::new();
    // A private port: never stop a developer's server or contact a real phone.
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port().to_string();
    drop(listener);
    let adb = std::env::var("PHONEFARM_TEST_ADB").unwrap_or_else(|_| "adb".into());
    let stop = || {
        Command::new(&adb)
            .args(["-P", &port, "kill-server"])
            .env("ADB_MDNS_AUTO_CONNECT", "0")
            .output()
            .expect("install adb to run the server inheritance regression")
    };
    stop();
    let output = f
        .pf()
        .env("ADB_BIN", &adb)
        .env("ADB_MDNS_AUTO_CONNECT", "0")
        .env("ANDROID_USER_HOME", f.0.join("android"))
        .args([
            "lease",
            "--serial",
            "fake-a",
            "--",
            "/bin/sh",
            "-c",
            "test -n \"$PF_LOCK_FDS\" && adb -P \"$1\" devices",
            "adb-test",
            &port,
        ])
        .output()
        .unwrap();
    let next = f.try_device("wifi-a");
    stop();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("daemon started successfully"),
        "test must actually start a fresh server: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        next.status.success(),
        "real adb server retained the task lock: {}",
        String::from_utf8_lossy(&next.stderr)
    );
}

#[test]
fn inherited_scope_cannot_widen_and_closed_fds_are_reported() {
    let f = Fixture::new();
    let script = r#"import os,socket,subprocess,sys
pf=os.environ['PHONEFARM_BIN']
fds=tuple(int(fd) for fd in os.environ['PF_LOCK_FDS'].split(','))
for scope in (['--serial','wifi-a'],['--serial','fake-b'],[]):
    p=subprocess.run([pf,'lease',*scope,'--timeout-ms','0','--','/usr/bin/true'],pass_fds=fds,capture_output=True)
    assert (p.returncode==0)==(scope in (['--serial','wifi-a'],[])),(scope,p.stderr)
left,right=socket.socketpair()
p=subprocess.run([pf,'__lease-handoff',str(right.fileno())],pass_fds=(*fds,right.fileno()),capture_output=True)
assert p.returncode!=0 and b'does not cover' in p.stderr,p.stderr
p=subprocess.run([pf,'lease','--serial','fake-a','--','/usr/bin/true'],capture_output=True)
assert p.returncode!=0 and b'was closed' in p.stderr,p.stderr
"#;
    let out = f
        .pf()
        .args(["lease", "--serial", "fake-a", "--", "python3", "-c", script])
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(f.try_device("fake-a").status.success());
}

#[test]
fn reset_cleanup_stops_orphaned_adb_client_and_releases_proxy_fds() {
    use std::os::unix::fs::PermissionsExt;
    let f = Fixture::new();
    let adb = f.0.join("fake-adb");
    fs::write(
        &adb,
        r#"#!/usr/bin/env python3
import os,signal,time
from pathlib import Path
root=Path(os.environ['CLIENT_TEST_ROOT'])
signal.signal(signal.SIGINT,signal.SIG_IGN)
signal.signal(signal.SIGTERM,lambda *_:(root/'term-seen').touch())
signal.alarm(15)
(root/'client-ready').touch()
while True: time.sleep(.01)
"#,
    )
    .unwrap();
    fs::set_permissions(&adb, fs::Permissions::from_mode(0o755)).unwrap();
    let script = r#"import os,subprocess,time
from pathlib import Path
root=Path(os.environ['CLIENT_TEST_ROOT'])
subprocess.Popen(['adb','shell','dumpsys','gfxinfo'],pass_fds=tuple(int(fd) for fd in os.environ['PF_LOCK_FDS'].split(',')))
while not (root/'client-ready').exists():time.sleep(.01)
"#;
    let mut task = Group(
        f.pf()
            .env("ADB_BIN", &adb)
            .env("CLIENT_TEST_ROOT", &f.0)
            .args(["lease", "--serial", "fake-a", "--", "python3", "-c", script])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()
            .unwrap(),
    );
    f.until("client-ready");
    assert!(task.0.wait().unwrap().success());
    assert!(
        !f.try_device("wifi-a").status.success(),
        "live orphan proxy must retain its lock"
    );
    assert!(f.try_device("fake-b").status.success());
    let node = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../harness-ops/node");
    let cleanup = Command::new("python3").args(["-c",
        "import sys;sys.path.insert(0,sys.argv[1]);from job_runtime import terminate_group;print(terminate_group(int(sys.argv[2]),.1))"])
        .arg(node).arg(task.0.id().to_string()).output().unwrap();
    assert!(
        cleanup.status.success(),
        "{}",
        String::from_utf8_lossy(&cleanup.stderr)
    );
    assert!(f.0.join("term-seen").exists());
    let until = Instant::now() + Duration::from_secs(3);
    while !f.try_device("fake-a").status.success() {
        assert!(
            Instant::now() < until,
            "reset group cleanup left the device locked"
        );
        std::thread::sleep(Duration::from_millis(10));
    }
}


#[test]
fn registry_updates_wait_for_the_same_global_lock_and_ignore_old_markers() {
    let f = Fixture::new();
    let root = f.0.join("locks");
    let original = fs::read(root.join("device-registry.json")).unwrap();
    for name in ["devlock.d", "phonefarm-lease.d", "devlock.queue"] {
        fs::write(root.join(name), b"historical evidence").unwrap();
    }
    let mut holder = Group(f.pf().args(["lease", "--serial", "fake-a", "--", "/bin/sh", "-c",
        "touch \"$1/ready\"; while [ ! -f \"$1/finish\" ]; do sleep .02; done", "holder"])
        .arg(&f.0).process_group(0).spawn().unwrap());
    f.until("ready");
    let request = f.0.join("update.json");
    fs::write(&request, json!({"expected_revision":2,"device":{
        "device_id":"b","label":"B updated","aliases":["fake-b","wifi-b"],"enabled":true}}).to_string()).unwrap();
    let spawn = || Group(f.pf().args(["lease", "devices-set", "--request"]).arg(&request)
        .arg("--json").stdout(Stdio::null()).process_group(0).spawn().unwrap());
    let mut cancelled = spawn();
    std::thread::sleep(Duration::from_millis(150));
    assert!(cancelled.0.try_wait().unwrap().is_none());
    cancelled.0.kill().unwrap();
    cancelled.0.wait().unwrap();
    let mut waiter = spawn();
    // The old implementation returned busy at two seconds.
    std::thread::sleep(Duration::from_millis(2200));
    assert!(waiter.0.try_wait().unwrap().is_none(), "registry rejected a live owner instead of waiting");
    assert_eq!(fs::read(root.join("device-registry.json")).unwrap(), original);
    fs::write(f.0.join("finish"), b"").unwrap();
    assert!(holder.0.wait().unwrap().success());
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Some(status) = waiter.0.try_wait().unwrap() { assert!(status.success()); break; }
        assert!(Instant::now() < deadline, "registry did not proceed after owner exit");
        std::thread::sleep(Duration::from_millis(10));
    }
    let updated: Value = serde_json::from_slice(&fs::read(root.join("device-registry.json")).unwrap()).unwrap();
    assert_eq!(updated["revision"], 3);
    for name in ["devlock.d", "phonefarm-lease.d", "devlock.queue"] {
        assert_eq!(fs::read(root.join(name)).unwrap(), b"historical evidence");
    }
    assert!(f.try_device("fake-a").status.success());
}
