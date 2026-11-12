//! Real fleet/task groups and a fake HDC whose server forks without setsid.
#![cfg(unix)]
use serde_json::Value;
use std::fs;
use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixStream;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "pf-hdc-server-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(root.join("bin")).unwrap();
        fs::create_dir(root.join("tmp")).unwrap();
        let python = Command::new("python3")
            .args(["-c", "import sys;print(sys.executable)"])
            .output()
            .unwrap();
        assert!(python.status.success());
        std::os::unix::fs::symlink(
            String::from_utf8(python.stdout).unwrap().trim(),
            root.join("bin/python3"),
        )
        .unwrap();
        let write_script = |name: &str, body: &str| {
            let path = root.join("bin").join(name);
            fs::write(&path, body).unwrap();
            fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
        };
        write_script("adb", "#!/bin/sh\nexit 0\n");
        write_script("hdc", "#!/bin/sh\nexport HOME=\"$HDC_TEST_ROOT/sdk-home\"\nexec \"$HDC_TEST_ROOT/bin/hdc-real\" \"$@\"\n");
        write_script(
            "hdc-real",
            r#"#!/usr/bin/env python3
import fcntl,json,os,signal,socket,sys
from pathlib import Path
root=Path(os.environ['HDC_TEST_ROOT'])
def record(name, data):
    path=root/name
    path.with_suffix('.tmp').write_text(json.dumps(data))
    os.replace(path.with_suffix('.tmp'),path)
def identity():
    return dict(pid=os.getpid(),pgid=os.getpgrp(),sid=os.getsid(0),
                home=os.environ['HOME'],tmpdir=os.environ['TMPDIR'])
args=sys.argv[1:]
# The deployed wrapper's start path lost its SDK HOME; client bootstrap did not.
if args==['start']: os.environ['HOME']=str(root/'wrong-home')
fast=args[:1]==['-p']
if fast: args=args[1:]
if not (root/'server.json').exists():
    if fast: sys.exit('Connect server failed')
    ready,notify=os.pipe()
    pid=os.fork()
    if pid:
        os.close(notify)
        assert os.read(ready,1)==b'1'
        os.close(ready)
    else:
        os.close(ready)
        # Match HDC: stay in the starting client's group, close stdio only.
        null=os.open(os.devnull,os.O_RDWR)
        for fd in range(3): os.dup2(null,fd)
        os.close(null)
        locks={(p.stat().st_dev,p.stat().st_ino) for p in root.rglob('*.lock')}
        inherited=[]
        for fd in range(3,256):
            try:
                st=os.fstat(fd)
                if (st.st_dev,st.st_ino) in locks: inherited.append(fd)
            except OSError: pass
        signal.alarm(60)
        mutex=open(Path(os.environ['TMPDIR'])/'.HDCServer.pid','w')
        fcntl.lockf(mutex,fcntl.LOCK_EX|fcntl.LOCK_NB)
        mutex.write(str(os.getpid()))
        mutex.flush()
        listener=socket.socket(socket.AF_UNIX)
        listener.bind(str(root/'server.sock'))
        listener.listen()
        record('server.json',dict(identity(),lock_fds=inherited))
        os.write(notify,b'1')
        os.close(notify)
        while True:
            with listener.accept()[0] as conn:
                assert conn.recv(1)==b'?'
                conn.sendall(b'!')
if args==['start']: sys.exit(0)
if args==['list','targets']:
    record('enumeration.json',identity())
    if not fast:
        with (root/'bootstrap-calls').open('a') as calls: calls.write('list targets\n')
        record('bootstrap-client.json',identity())
        if (root/'hang-bootstrap').exists():
            signal.alarm(60)
            while True: signal.pause()
    print('fake-hdc')
    sys.exit(0)
record('client.json',identity())
if args==['hold']:
    signal.alarm(60)
    while True: signal.pause()
elif args[:3]==['-t','fake-hdc','shell']:
    print('-----PF:props-----\nFixture\nHUAWEI\nOpenHarmony\n')
    print('-----PF:battery-----\ncapacity: 73\ntemperature: 390\n-----PF:end-----')
"#,
        );
        Self(root)
    }
    fn pf(&self) -> Command {
        let mut c = Command::new(env!("CARGO_BIN_EXE_phonefarm"));
        c.env("DEVLOCK_DIR", self.0.join("locks"))
            .env("PHONEFARM_LEASE_DIR", self.0.join("leases"))
            .env("PATH", self.0.join("bin"))
            .env("ADB_BIN", self.0.join("bin/adb"))
            .env("HDC_BIN", self.0.join("bin/hdc"))
            .env("HOME", self.0.join("caller-home"))
            .env("TMPDIR", self.0.join("tmp"))
            .env("HDC_TEST_ROOT", &self.0);
        for key in [
            "PF_LOCK_FDS",
            "PF_LOCK_SCOPE",
            "PF_JOB_ID",
            "PF_JOB_DEVICE_BINDING",
            "PF_JOB_LEASE_DIR",
            "PF_TRANSPORT_PATH",
            "PF_TRANSPORT_ADB",
            "PF_TRANSPORT_HDC",
        ] {
            c.env_remove(key);
        }
        c
    }
    fn read(&self, name: &str) -> Value {
        serde_json::from_slice(&fs::read(self.0.join(name)).unwrap()).unwrap()
    }
    fn stop_server(&self) {
        let path = self.0.join("server.json");
        if path.exists() {
            let pid = self.read("server.json")["pid"].as_i64().unwrap() as i32;
            unsafe {
                libc::kill(pid, libc::SIGKILL);
            }
            // Synchronize on HDC's mutex being released, not a delay after kill.
            let mutex = fs::File::open(self.0.join("tmp/.HDCServer.pid")).unwrap();
            let mut lock: libc::flock = unsafe { std::mem::zeroed() };
            lock.l_type = libc::F_RDLCK as _;
            lock.l_whence = libc::SEEK_SET as _;
            assert_eq!(
                unsafe { libc::fcntl(mutex.as_raw_fd(), libc::F_SETLKW, &lock) },
                0
            );
            fs::remove_file(path).unwrap();
            fs::remove_file(self.0.join("server.sock")).unwrap();
        }
    }
    fn assert_server_alive(&self) {
        let server = self.read("server.json");
        assert_eq!(server["home"], self.0.join("sdk-home").to_str().unwrap());
        assert_eq!(server["tmpdir"], self.0.join("tmp").to_str().unwrap());
        let mut socket = UnixStream::connect(self.0.join("server.sock")).unwrap();
        socket
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        socket.write_all(b"?").unwrap();
        let mut answer = [0];
        socket.read_exact(&mut answer).unwrap();
        assert_eq!(&answer, b"!");
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        if self.0.join("hang-bootstrap").exists() && self.0.join("bootstrap-client.json").exists() {
            let pid = self.read("bootstrap-client.json")["pid"].as_i64().unwrap() as i32;
            unsafe {
                libc::kill(pid, libc::SIGKILL);
            }
        }
        self.stop_server();
        if std::thread::panicking() {
            eprintln!("failed HDC fixture retained: {}", self.0.display());
        } else {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }
}

#[test]
fn a_live_hdc_server_does_not_add_an_enumeration_to_each_command() {
    let f = Fixture::new();
    for _ in 0..2 {
        let output = f.pf().args(["fleet", "--json"]).output().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stdout)
        );
        f.assert_server_alive();
    }
    assert_eq!(
        fs::read_to_string(f.0.join("bootstrap-calls")).unwrap(),
        "list targets\n"
    );
}

#[test]
fn a_stuck_hdc_bootstrap_is_bounded_and_caller_death_only_stops_the_client() {
    for kill_caller in [false, true] {
        let f = Fixture::new();
        fs::write(f.0.join("hang-bootstrap"), "").unwrap();
        let mut task = Task(
            f.pf()
                .args([
                    "lease",
                    "--serial",
                    "hdc:fake-hdc",
                    "--",
                    "/bin/sh",
                    "-c",
                    "hdc hold",
                ])
                .process_group(0)
                .stdout(Stdio::null())
                .stderr(fs::File::create(f.0.join("stderr")).unwrap())
                .spawn()
                .unwrap(),
        );
        let deadline = Instant::now() + Duration::from_secs(5);
        while !f.0.join("bootstrap-client.json").exists() {
            assert!(Instant::now() < deadline, "bootstrap did not start");
            std::thread::sleep(Duration::from_millis(10));
        }
        let pid = f.read("bootstrap-client.json")["pid"].as_i64().unwrap() as i32;
        if !kill_caller {
            let deadline = Instant::now() + Duration::from_secs(18);
            loop {
                if let Some(status) = task.0.try_wait().unwrap() {
                    assert!(!status.success());
                    break;
                }
                assert!(
                    Instant::now() < deadline,
                    "detached bootstrap client has no timeout"
                );
                std::thread::sleep(Duration::from_millis(10));
            }
            assert!(fs::read_to_string(f.0.join("stderr"))
                .unwrap()
                .contains("hdc bootstrap timed out"));
        }
        drop(task);
        let deadline = Instant::now() + Duration::from_secs(5);
        while unsafe { libc::kill(pid, 0) } == 0 {
            assert!(
                Instant::now() < deadline,
                "detached bootstrap client survived caller death"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
        f.assert_server_alive();
        assert_eq!(
            f.pf()
                .args([
                    "lease",
                    "--serial",
                    "hdc:fake-hdc",
                    "--timeout-ms",
                    "0",
                    "--",
                    "/usr/bin/true"
                ])
                .status()
                .unwrap()
                .code(),
            Some(0)
        );
    }
}
struct Task(Child);
impl Drop for Task {
    fn drop(&mut self) {
        unsafe {
            libc::kill(-(self.0.id() as i32), libc::SIGKILL);
        }
        let _ = self.0.wait();
    }
}

#[test]
fn fleet_first_hdc_server_start_leaves_the_observation_group() {
    let f = Fixture::new();
    // Also repeat from a stopped server: a prior sample must not cache readiness.
    for _ in 0..2 {
        let output = f.pf().args(["fleet", "--json"]).output().unwrap();
        let result: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(result["observation"], "sampled", "{result}");
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(result["devices"][0]["serial"], "hdc:fake-hdc");
        assert_eq!(result["devices"][0]["battery"]["temp_c"], 39.0);
        let server = f.read("server.json");
        let client = f.read("client.json");
        assert_ne!(
            server["pgid"], client["pgid"],
            "HDC server remained in the worker group"
        );
        assert_ne!(
            server["sid"], client["sid"],
            "HDC server remained in the worker session"
        );
        assert_eq!(server["lock_fds"], serde_json::json!([]));
        f.assert_server_alive();
        f.stop_server();
    }
}

#[test]
fn killing_a_task_releases_its_lock_but_keeps_the_hdc_server() {
    let f = Fixture::new();
    // Shell launcher path, including an HDC_BIN whose basename is not hdc.
    let hdc = f.0.join("custom-transport");
    fs::rename(f.0.join("bin/hdc"), &hdc).unwrap();
    let mut command = f.pf();
    command
        .env("HDC_BIN", &hdc)
        .args([
            "lease",
            "--serial",
            "hdc:fake-hdc",
            "--",
            "/bin/sh",
            "-c",
            "hdc hold",
        ])
        .process_group(0)
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let task = Task(command.spawn().unwrap());
    let deadline = Instant::now() + Duration::from_secs(5);
    while !f.0.join("client.json").exists() {
        assert!(Instant::now() < deadline, "HDC client did not start");
        std::thread::sleep(Duration::from_millis(10));
    }
    let server = f.read("server.json");
    let client = f.read("client.json");
    assert_eq!(
        client["pgid"],
        task.0.id(),
        "finite client must remain in task cleanup"
    );
    assert_ne!(server["pgid"], client["pgid"]);
    assert_ne!(server["sid"], client["sid"]);
    assert_eq!(server["lock_fds"], serde_json::json!([]));
    let acquire = || {
        f.pf()
            .args([
                "lease",
                "--serial",
                "hdc:fake-hdc",
                "--timeout-ms",
                "0",
                "--",
                "/usr/bin/true",
            ])
            .output()
            .unwrap()
    };
    assert!(!acquire().status.success());
    drop(task);
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let output = f.pf().args(["lease", "status", "--json"]).output().unwrap();
        if serde_json::from_slice::<Value>(&output.stdout).unwrap()["status"] == "clear" {
            break;
        }
        assert!(Instant::now() < deadline, "task fds did not close");
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(acquire().status.success());
    f.assert_server_alive();
    assert_eq!(
        f.read("server.json"),
        server,
        "task cleanup must not restart the shared server"
    );
}
