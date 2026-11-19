//! Short device observations share the experiment lease and own a bounded worker group.
use crate::{device_registry::DeviceBinding, lease_storage::Storage};
use std::fs::{self, File};
use std::io::{Read, Write};
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Output, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

static WORKER: AtomicBool = AtomicBool::new(false);

pub(crate) fn in_worker() -> bool {
    WORKER.load(Ordering::Relaxed)
}

#[derive(Debug)]
pub(crate) struct Failure {
    pub state: &'static str,
    pub reason: String,
}
impl Failure {
    fn error(reason: impl Into<String>) -> Self {
        Self {
            state: "error",
            reason: reason.into(),
        }
    }
}

struct OutputFile(PathBuf);
impl OutputFile {
    fn new(tag: &str) -> Result<(Self, File), Failure> {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let path =
            std::env::temp_dir().join(format!("pf-observe-{}-{stamp}-{tag}", std::process::id()));
        let file = File::options()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|e| Failure::error(format!("observation output: {e}")))?;
        Ok((Self(path), file))
    }
}
impl Drop for OutputFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

fn group_gone(pid: u32) -> bool {
    (unsafe { libc::kill(-(pid as i32), 0) == -1 })
        && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
}

fn stop_group(child: &mut Child) {
    // Only our still-owned child group; never infer another process identity from a label.
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
    let _ = child.wait();
}

pub(crate) fn run(
    kind: &str,
    args: &[String],
    storage: &Storage,
    timeout: Duration,
) -> Result<Output, Failure> {
    if matches!(kind, "fleet" | "app-snapshot") {
        run_unscoped(kind, args, storage, timeout)
    } else {
        run_scoped(kind, args, storage, timeout, None)
    }
}

fn run_unscoped(kind: &str, args: &[String], storage: &Storage, timeout: Duration) -> Result<Output, Failure> {
    // The worker chooses each phone's scope after enumeration. Enumeration and
    // lightweight telemetry never acquire a device or global lock.
    let (mut cmd, out, err) = worker_command(kind, args, storage, timeout)?;
    supervise_ephemeral(&mut cmd, kind, timeout, &out.0, &err.0)
}

pub(crate) fn run_scoped(
    kind: &str,
    args: &[String],
    storage: &Storage,
    timeout: Duration,
    binding: Option<&DeviceBinding>,
) -> Result<Output, Failure> {
    let serial = binding.map(|binding| binding.serial.as_str()).or_else(|| screen_serial(args));
    let (mut cmd, out_path, err_path) = worker_command(kind, args, storage, timeout)?;
    if let Some(serial) = serial { cmd.env("PHONEFARM_OBSERVATION_SERIAL", serial); }
    supervise_ephemeral(&mut cmd, kind, timeout, &out_path.0, &err_path.0)
}

fn worker_command(
    kind: &str,
    args: &[String],
    storage: &Storage,
    timeout: Duration,
) -> Result<(Command, OutputFile, OutputFile), Failure> {
    let (out_path, out) = OutputFile::new("out")?;
    let (err_path, err) = OutputFile::new("err")?;
    let mut cmd = Command::new(std::env::current_exe().map_err(|e| Failure::error(e.to_string()))?);
    cmd.arg("__observe-worker")
        .arg(kind)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(out)
        .stderr(err)
        .process_group(0)
        .env("PHONEFARM_OBSERVATION_TIMEOUT_MS", timeout.as_millis().to_string())
        .env_remove("DEVLOCK_LABEL")
        .env_remove("PF_JOB_DEVICE_BINDING")
        .env_remove("PF_JOB_ACTOR");
    if let Some(root) = &storage.devlock_dir {
        cmd.env("DEVLOCK_DIR", root);
    }
    if let Some(root) = &storage.device_lease_dir {
        cmd.env("PHONEFARM_LEASE_DIR", root);
    }
    Ok((cmd, out_path, err_path))
}

fn supervise_ephemeral(
    cmd: &mut Command,
    kind: &str,
    timeout: Duration,
    out: &std::path::Path,
    err: &std::path::Path,
) -> Result<Output, Failure> {
    let mut child = cmd.spawn().map_err(|e| Failure::error(format!("observation worker could not start: {e}")))?;
    let start = child.stdin.as_mut()
        .ok_or("worker start pipe unavailable".to_string())
        .and_then(|stdin| stdin.write_all(b"1").map_err(|e| e.to_string()));
    if let Err(e) = start {
        stop_group(&mut child);
        return Err(Failure::error(format!("observation startup failed: {e}")));
    }
    wait_output(&mut child, kind, timeout, out, err)
}

fn wait_output(
    child: &mut Child,
    kind: &str,
    timeout: Duration,
    out: &std::path::Path,
    err: &std::path::Path,
) -> Result<Output, Failure> {
    let deadline = Instant::now() + timeout;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(20)),
            result => {
                stop_group(child);
                return Err(Failure::error(format!("observation timeout/wait failure ({result:?})")));
            }
        }
    };
    let deadline = Instant::now() + Duration::from_millis(300);
    while !group_gone(child.id()) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    if status.code() == Some(75) {
        return Err(Failure { state: "deferred", reason: fs::read_to_string(err).unwrap_or_default() });
    }
    let expected = match kind {
        "fleet" => matches!(status.code(), Some(0 | 1)),
        "preflight" => matches!(status.code(), Some(0 | 2 | 3)),
        "screen" => matches!(status.code(), Some(0 | 1)),
        "app-snapshot" => status.success(),
        _ => false,
    };
    if !expected || !group_gone(child.id()) {
        stop_group(child);
        let diagnostic = fs::read_to_string(err).unwrap_or_default().chars().take(2048).collect::<String>();
        return Err(Failure::error(format!("observation worker/group incomplete ({status}). {}", diagnostic.trim())));
    }
    Ok(Output {
        status,
        stdout: fs::read(out).map_err(|e| Failure::error(e.to_string()))?,
        stderr: fs::read(err).map_err(|e| Failure::error(e.to_string()))?,
    })
}

fn screen_serial(args: &[String]) -> Option<&str> {
    let mut serials = args.chunks_exact(2)
        .filter(|pair| pair[0] == "--serial").map(|pair| pair[1].as_str());
    let serial = serials.next()?;
    serials.next().is_none().then_some(serial)
}

pub(crate) fn run_worker(args: &[String]) -> i32 {
    let verify = || -> Result<(), String> {
        let mut start = [0];
        std::io::stdin()
            .read_exact(&mut start)
            .map_err(|e| e.to_string())?;
        if start != *b"1" {
            return Err("invalid startup barrier".into());
        }
        Ok(())
    };
    if let Err(e) = verify() {
        eprintln!("{e}");
        return 90;
    }
    let Ok(timeout_ms) = std::env::var("PHONEFARM_OBSERVATION_TIMEOUT_MS")
        .unwrap_or_default()
        .parse::<u64>()
    else {
        return 90;
    };
    let group = std::process::id() as i32;
    // The pipe stays open in the supervisor until this child exits. If that
    // supervisor dies, stop our own group even while a transport is stuck.
    std::thread::spawn(move || {
        let mut eof = [0];
        let _ = std::io::stdin().read(&mut eof);
        unsafe {
            libc::kill(-group, libc::SIGKILL);
        }
    });
    // Also bounds a worker whose supervisor is suspended rather than dead.
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(timeout_ms));
        unsafe {
            libc::kill(-group, libc::SIGKILL);
        }
    });
    let _lease = match std::env::var("PHONEFARM_OBSERVATION_SERIAL").ok().map(|serial|
        crate::devlease::EphemeralObservation::acquire(&crate::lease_storage::resolve(), &serial)).transpose() {
        Ok(lease) => lease, Err(error) => { eprintln!("{error}"); return 75; }
    };
    WORKER.store(true, Ordering::Relaxed);
    match args.first().map(String::as_str) {
        Some("fleet") => crate::fleet::run_fleet(&args[1..]),
        Some("preflight") if !args.iter().any(|a| a == "--fix") => {
            crate::preflight::run_preflight(&args[1..])
        }
        Some("screen") => crate::screen::capture_worker(&args[1..]),
        Some("app-snapshot") => match args.get(2).zip(args.get(4)).ok_or("missing serial/tmp".to_string())
            .and_then(|(serial, tmp)| crate::app_snapshot::collect(serial, tmp)) {
                Ok(value) => { println!("{value}"); 0 },
                Err(error) => { eprintln!("{error}"); 1 },
            },
        _ => 90,
    }
}
