//! Actual screen HTTP -> observation worker -> fake HDC. No real device transport.
#![cfg(unix)]
use serde_json::Value;
use std::fs;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("screen-observe-{}-{}", std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        fs::create_dir_all(root.join("bin")).unwrap();
        let python = Command::new("python3").args(["-c", "import sys;print(sys.executable)"]).output().unwrap();
        std::os::unix::fs::symlink(String::from_utf8(python.stdout).unwrap().trim(), root.join("bin/python3")).unwrap();
        let hdc = root.join("bin/hdc");
        fs::write(&hdc, r#"#!/bin/sh
printf '%s\n' "$*" >> "$FIXTURE/calls"
[ "$1/$2" != list/targets ] || exit 0
[ "$1" != -p ] || shift
case "$3/$4" in
shell/snapshot_display)
  while [ -f "$FIXTURE/hold" ]; do /bin/sleep 0.02; done
  [ ! -f "$FIXTURE/fail" ] || exit 7
  /bin/cp "$FIXTURE/input.jpg" "$FIXTURE/${6##*/}";;
file/recv) /bin/cp "$FIXTURE/${5##*/}" "$6";;
shell/rm) /bin/rm -f "$FIXTURE/${6##*/}";;
*) exit 9;;
esac
"#).unwrap();
        fs::set_permissions(&hdc, fs::Permissions::from_mode(0o755)).unwrap();
        image::RgbImage::from_pixel(20, 40, image::Rgb([120u8, 30, 60])).save(root.join("input.jpg")).unwrap();
        Self(root)
    }
    fn cmd(&self) -> Command {
        let mut cmd = Command::new(env!("CARGO_BIN_EXE_phonefarm"));
        cmd.env("DEVLOCK_DIR", self.0.join("locks"))
            .env("PHONEFARM_LEASE_DIR", self.0.join("leases"))
            .env("PHONEFARM_TMP", self.0.join("scratch"))
            .env("PATH", self.0.join("bin")).env("FIXTURE", &self.0)
            .env_remove("DEVLOCK_LABEL").env_remove("PF_JOB_ID").env_remove("PF_JOB_LEASE_DIR");
        cmd
    }
    fn start(&self) -> Server {
        let socket = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = socket.local_addr().unwrap().port();
        drop(socket);
        let child = self.cmd().args(["screen", "--serial", "hdc:fake-screen", "--interval-ms", "200", "--port", &port.to_string()])
            .stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap();
        let server = Server { child, port };
        until(|| server.get("/state.json").is_some());
        server
    }
    fn status(&self) -> Value {
        serde_json::from_slice(&self.cmd().args(["lease", "status", "--json"]).output().unwrap().stdout).unwrap()
    }
    fn calls(&self) -> String { fs::read_to_string(self.0.join("calls")).unwrap_or_default() }
}
impl Drop for Fixture { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }
struct Server { child: Child, port: u16 }
impl Server {
    fn request(&self, method: &str, path: &str) -> Option<Value> {
        let mut socket = TcpStream::connect(("127.0.0.1", self.port)).ok()?;
        socket.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
        write!(socket, "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nContent-Length: 0\r\n\r\n", self.port).ok()?;
        let mut response = String::new();
        socket.read_to_string(&mut response).ok()?;
        serde_json::from_str(response.split_once("\r\n\r\n")?.1).ok()
    }
    fn get(&self, path: &str) -> Option<Value> { self.request("GET", path) }
    fn watch(&self) -> String { self.request("POST", "/watch/start").unwrap()["id"].as_str().unwrap().into() }
    fn stop(&self, id: &str) { assert_eq!(self.request("POST", &format!("/watch/stop?id={id}")).unwrap()["ok"], true); }
}
impl Drop for Server { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }
fn until(mut check: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while !check() { assert!(Instant::now() < deadline, "screen fixture did not progress"); std::thread::sleep(Duration::from_millis(20)); }
}

#[test]
fn screen_frame_owns_transport_window_then_pause_stops_new_work() {
    let f = Fixture::new();
    let server = f.start();
    assert!(f.calls().is_empty());
    assert!(!f.0.join("locks").exists(), "no watcher must not acquire a lease");
    fs::write(f.0.join("hold"), "").unwrap();
    let viewer = server.watch();
    until(|| f.calls().contains("snapshot_display"));
    assert_eq!(f.status()["status"], "held");
    let competing = f.cmd().args(["lease", "--serial", "hdc:fake-screen", "--timeout-ms", "0", "--", "/usr/bin/true"]).output().unwrap();
    assert!(!competing.status.success());
    let other = f.cmd().args(["lease", "--serial", "fake-writer", "--timeout-ms", "0", "--", "/usr/bin/true"]).output().unwrap();
    assert!(other.status.success(), "another phone must remain available during the frame");
    fs::remove_file(f.0.join("hold")).unwrap();
    until(|| server.get("/state.json").is_some_and(|s| s["frames"].as_u64().unwrap_or(0) > 0));
    server.stop(&viewer);
    until(|| f.status()["status"] == "clear");
    let state = server.get("/state.json").unwrap();
    assert_eq!((state["width"].as_u64(), state["height"].as_u64()), (Some(20), Some(40)));
    assert_eq!(state["serial"], "hdc:fake-screen");
    let calls = f.calls();
    until(|| server.get("/state.json").is_some_and(|s| s["capture"]["state"] == "idle"));
    assert_eq!(f.calls(), calls);
}

#[test]
fn hdc_failed_snapshot_cannot_download_or_publish_an_old_frame() {
    let f = Fixture::new();
    fs::write(f.0.join("fail"), "").unwrap();
    let server = f.start();
    let viewer = server.watch();
    until(|| server.get("/state.json").is_some_and(|s| s["capture"]["state"] == "error"));
    server.stop(&viewer);
    until(|| f.status()["status"] == "clear");
    let state = server.get("/state.json").unwrap();
    assert_eq!(state["frames"], 0);
    let calls = f.calls();
    assert!(!calls.contains("file recv"), "failed snapshot must not retrieve old data: {calls}");
    let paths: Vec<_> = calls.lines().filter(|s| s.contains("snapshot_display")).map(|s| s.split_whitespace().last().unwrap()).collect();
    assert!(!paths.is_empty());
    assert_eq!(paths.iter().collect::<std::collections::HashSet<_>>().len(), paths.len());
}
