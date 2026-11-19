//! Bounded executable transport. File output avoids pipe-reader lifetime loopholes.
use crate::devlease::Lease;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    process::Stdio,
    time::{Duration, Instant},
};

pub(crate) fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub(crate) fn bounded(path: &Path, cap: u64) -> Result<Vec<u8>, String> {
    let mut o = OpenOptions::new();
    o.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW);
    }
    let f = o
        .open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    let m = f.metadata().map_err(|e| e.to_string())?;
    if !m.is_file() || m.len() > cap {
        return Err("input/output must be a bounded regular file".into());
    }
    let mut bytes = Vec::new();
    f.take(cap + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > cap {
        return Err("file grew beyond bound".into());
    }
    Ok(bytes)
}
fn stage_file(path: &Path, bytes: &[u8], sync_file: bool, sync_parent: bool) -> Result<(), String> {
    let mut f = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(path)
        .map_err(|e| e.to_string())?;

    f.write_all(bytes).map_err(|e| e.to_string())?;
    if sync_file {
        f.sync_all().map_err(|e| e.to_string())?;
    }
    if sync_parent {
        File::open(path.parent().ok_or("file parent")?)
            .and_then(|d| d.sync_all())
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

pub(crate) fn new_file(path: &Path, bytes: &[u8]) -> Result<(), String> {
    stage_file(path, bytes, true, true)
}
pub(crate) fn journal(dir: &Path, event: Value) -> Result<(), String> {
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(dir.join("journal.jsonl"))
        .map_err(|e| e.to_string())?;
    writeln!(f, "{}", event)
        .and_then(|_| f.sync_all())
        .map_err(|e| e.to_string())?;
    File::open(dir)
        .and_then(|d| d.sync_all())
        .map_err(|e| e.to_string())
}
#[cfg(unix)]
fn group_absent(pid: u32) -> bool {
    (unsafe { libc::kill(-(pid as i32), 0) }) < 0
        && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
}
#[cfg(not(unix))]
fn group_absent(_: u32) -> bool {
    false
}
#[cfg(unix)]
fn stop_group(pid: u32) {
    unsafe {
        libc::kill(-(pid as i32), libc::SIGKILL);
    }
}
#[cfg(not(unix))]
fn stop_group(_: u32) {}
fn stop_and_reap(child: &mut std::process::Child) -> bool {
    stop_group(child.id());
    let _ = child.kill();
    let until = Instant::now() + Duration::from_secs(1);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return true,
            Err(_) => return false,
            Ok(None) if Instant::now() >= until => return false,
            Ok(None) => std::thread::sleep(Duration::from_millis(10)),
        }
    }
}

pub(crate) struct Receipt {
    pub code: i32,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
}
pub(crate) struct Transport {
    pub input_hashes: std::collections::BTreeMap<PathBuf, String>,
    pub program: PathBuf,
    pub target: String,
    pub origin: String,
    pub expected_sha: String,
    pub dir: PathBuf,
    pub sequence: u64,
}
impl Transport {
    pub fn run(
        &mut self,
        op: &str,
        args: &[String],
        stdin: Option<&Path>,
        deadline: Duration,
        cap: u64,
        lease: Option<&Lease>,
    ) -> Result<Receipt, String> {
        self.sequence += 1;
        let stem = format!("{:04}-{op}", self.sequence);
        let outputs = self.dir.join("commands");
        fs::create_dir_all(&outputs).map_err(|e| e.to_string())?;
        let out = outputs.join(format!("{stem}.stdout"));
        let err = outputs.join(format!("{stem}.stderr"));
        // Read caller/snapshot stdin exactly once, then use an owned unlinked fd. Hash and child consume the same bytes.
        let input = stdin.map(|p| bounded(p, 8 * 1024 * 1024)).transpose()?;
        let input_sha = input.as_ref().map(|b| sha(b));
        if let Some(path) = stdin {
            require_input(self.input_hashes.get(path), input_sha.as_deref())?;
        }
        let executable_path = outputs.join(format!("{stem}.executable"));
        // macOS cannot exec /dev/fd. A fresh private inode is populated from the owned frozen bytes;
        // no caller pathname is reopened for execution. Local same-uid tampering is outside attestation scope.
        let executable_bytes = bounded(&self.program, 128 * 1024 * 1024)?;
        if sha(&executable_bytes) != self.expected_sha {
            return Err("actual executable snapshot hash differs".into());
        }
        stage_file(&executable_path, &executable_bytes, false, false)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&executable_path, fs::Permissions::from_mode(0o500))
                .map_err(|e| e.to_string())?;
        }
        let input_fd = input
            .as_ref()
            .map(|b| owned_fd(&outputs.join(format!("{stem}.stdin")), b))
            .transpose()?;
        let argv: Vec<String> = [vec!["-s".into(), self.target.clone()], args.to_vec()].concat();
        journal(
            &self.dir,
            json!({"event":"command_intent","operation":op,"argv":argv,"program_sha256":self.expected_sha,
            "stdin_sha256":input_sha,"source":self.origin,"at":chrono::Utc::now().to_rfc3339()}),
        )?;
        let mut c = crate::fd_lock::transport_command(&executable_path);
        c.args(&argv)
            .stdout(Stdio::from(
                OpenOptions::new()
                    .create_new(true)
                    .write(true)
                    .open(&out)
                    .map_err(|e| e.to_string())?,
            ))
            .stderr(Stdio::from(
                OpenOptions::new()
                    .create_new(true)
                    .write(true)
                    .open(&err)
                    .map_err(|e| e.to_string())?,
            ));
        if let Some(f) = input_fd {
            c.stdin(Stdio::from(f));
        } else {
            c.stdin(Stdio::null());
        }
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            c.process_group(0);
        }
        if let Some(lease) = lease { lease.command(&mut c)?; }
        let (_parent_pipe, child_pipe) = crate::fd_lock::transport_lifeline(&mut c)?;
        let spawned = c.spawn();
        drop(child_pipe);
        let mut child = match spawned {
            Ok(c) => c,
            Err(e) => {
                let _ = fs::remove_file(&executable_path);
                return Err(e.to_string());
            }
        };
        let pid = child.id();
        let until = Instant::now() + deadline;
        let result = loop {
            if [&out, &err]
                .iter()
                .any(|p| fs::metadata(p).map_or(true, |m| m.len() > cap))
            {
                break Err("transport output limit exceeded".into());
            }
            if Instant::now() >= until {
                break Err("transport command deadline exceeded".into());
            }
            match child.try_wait() {
                Ok(Some(s)) => {
                    if !group_absent(pid) {
                        break Err(
                            "transport parent exited but owned process group remains live/unknown"
                                .into(),
                        );
                    }
                    break Ok(s.code().unwrap_or(-1));
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(20)),
                Err(e) => break Err(e.to_string()),
            }
        };
        let reaped = result.is_ok() || stop_and_reap(&mut child);
        let _ = fs::remove_file(&executable_path);
        // Killing local processes does not prove that remote activity stopped.
        let group_stopped = reaped && group_absent(pid);
        let code = match &result {
            Ok(n) => Some(*n),
            Err(_) => None,
        };
        journal(
            &self.dir,
            json!({"event":"command_result","operation":op,"code":code,"error":result.as_ref().err(),
            "local_process_reaped":reaped,"local_process_group_absent":group_stopped,"remote_idle_verified":false,"at":chrono::Utc::now().to_rfc3339()}),
        )?;
        let code = result?;
        Ok(Receipt {
            code,
            stdout: bounded(&out, cap)?,
            stderr: bounded(&err, cap)?,
        })
    }
    pub fn shell(
        &mut self,
        op: &str,
        command: &str,
        input: Option<&Path>,
        timeout: u64,
        cap: u64,
        lease: Option<&Lease>,
    ) -> Result<Receipt, String> {
        self.run(
            op,
            &["shell".into(), format!("su -c {}", quote(command))],
            input,
            Duration::from_secs(timeout),
            cap,
            lease,
        )
    }
}
pub(crate) fn quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

// This fd is never reopened by its original path. It is unlinked after the
// child has the same bytes, so durability cannot depend on a directory entry.
fn owned_fd(path: &Path, bytes: &[u8]) -> Result<File, String> {
    let mut f = OpenOptions::new()
        .create_new(true)
        .read(true)
        .write(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    f.write_all(bytes).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        f.set_permissions(fs::Permissions::from_mode(0o400))
            .map_err(|e| e.to_string())?;
    }
    fs::remove_file(path).map_err(|e| e.to_string())?;
    f.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    Ok(f)
}

fn require_input(expected: Option<&String>, actual: Option<&str>) -> Result<(), String> {
    if expected.is_some_and(|e| Some(e.as_str()) == actual) {
        Ok(())
    } else {
        Err("stdin bytes differ from frozen execution hash or unregistered input".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    fn temp() -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "phonefarm-transport-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        p
    }
    fn local(dir: &Path, script: &str, input: Option<(&Path, &str)>) -> Transport {
        let program = dir.join("frozen-tool");
        new_file(&program, script.as_bytes()).unwrap();
        let mut hashes = std::collections::BTreeMap::new();
        if let Some((p, s)) = input {
            hashes.insert(p.to_owned(), sha(s.as_bytes()));
        }
        Transport {
            program,
            target: "local-test".into(),
            origin: "synthetic".into(),
            expected_sha: sha(script.as_bytes()),
            dir: dir.to_owned(),
            sequence: 0,
            input_hashes: hashes,
        }
    }
    #[test]
    fn child_consumes_frozen_stdin_even_after_original_is_replaced() {
        let dir = temp();
        let p = dir.join("input");
        new_file(&p, b"frozen bytes").unwrap();
        let script = format!(
            "#!/bin/sh\nprintf 'changed bytes' > {}\ncat\n",
            quote(p.to_str().unwrap())
        );
        let mut t = local(&dir, &script, Some((&p, "frozen bytes")));
        let r = t
            .run("copy", &[], Some(&p), Duration::from_secs(3), 1024, None)
            .unwrap();
        assert_eq!(r.code, 0);
        assert_eq!(r.stdout, b"frozen bytes");
        assert_eq!(bounded(&p, 1024).unwrap(), b"changed bytes");
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn changed_frozen_stdin_never_starts_transport() {
        let dir = temp();
        let p = dir.join("input");
        new_file(&p, b"changed").unwrap();
        let mut t = local(&dir, "#!/bin/sh\necho unexpected\n", Some((&p, "expected")));
        let err = t
            .run("copy", &[], Some(&p), Duration::from_secs(1), 1024, None)
            .err()
            .unwrap();
        assert!(err.contains("stdin bytes"));
        assert!(!dir.join("commands/0001-copy.stdout").exists());
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn recovery_evidence_reader_never_observes_partial_published_bytes() {
        let dir = temp();
        let bytes = vec![b'x'; 1024 * 1024];
        for i in 0..8 {
            let p = dir.join(format!("{i}.permit"));
            let reader_path = p.clone();
            let expected = bytes.clone();
            let reader = std::thread::spawn(move || {
                let until = Instant::now() + Duration::from_secs(5);
                loop {
                    match fs::read(&reader_path) {
                        Ok(b) => {
                            assert_eq!(b, expected);
                            return;
                        }
                        Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
                        Err(e) => panic!("{e}"),
                    }
                    assert!(Instant::now() < until);
                    std::thread::yield_now();
                }
            });
            crate::devlease::durable_replace(&p, &bytes).unwrap();
            reader.join().unwrap();
        }
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn timeout_and_output_cap_fail_closed() {
        for (script, duration, cap) in [
            ("#!/bin/sh\nsleep 30\n", Duration::from_millis(100), 1024),
            (
                "#!/bin/sh\ni=0; while [ $i -lt 10000 ]; do printf x; i=$((i+1)); done\n",
                Duration::from_secs(3),
                8,
            ),
        ] {
            let dir = temp();
            let mut t = local(&dir, script, None);
            let start = Instant::now();
            assert!(t.run("bounded", &[], None, duration, cap, None).is_err());
            assert!(start.elapsed() < Duration::from_secs(5));
            fs::remove_dir_all(dir).unwrap();
        }
    }
    #[test]
    #[cfg(unix)]
    fn fifo_input_is_rejected_without_waiting() {
        use std::ffi::CString;
        let dir = temp();
        let p = dir.join("fifo");
        let c = CString::new(p.to_str().unwrap()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(c.as_ptr(), 0o600) }, 0);
        let start = Instant::now();
        assert!(bounded(&p, 64).is_err());
        assert!(start.elapsed() < Duration::from_secs(1));
        fs::remove_dir_all(dir).unwrap();
    }
}
