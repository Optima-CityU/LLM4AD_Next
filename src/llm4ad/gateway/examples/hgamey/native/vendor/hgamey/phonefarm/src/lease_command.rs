//! Bounded, sequential transport commands under an existing experiment lease.
//! Local process completion never certifies that activity on the device stopped.
use crate::devlease::Lease;
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom};
use std::os::unix::{
    fs::OpenOptionsExt,
    process::{CommandExt, ExitStatusExt},
};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

const OUTPUT_LIMIT: u64 = 8 * 1024 * 1024;
const POLL: Duration = Duration::from_millis(20);
static SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug)]
pub(crate) struct Output {
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
    /// Original bounded bytes, including non-UTF-8 screenshot output.
    pub stdout_path: PathBuf,
}

fn group_gone(pid: u32) -> bool {
    (unsafe { libc::kill(-(pid as i32), 0) == -1 })
        && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
}

fn stop_and_reap(child: &mut Child) -> bool {
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
    let deadline = Instant::now() + Duration::from_secs(1);
    let mut reaped = false;
    loop {
        if !reaped {
            match child.try_wait() {
                Ok(Some(_)) => reaped = true,
                Err(_) => return false,
                Ok(None) => {}
            }
        }
        if reaped && group_gone(child.id()) {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(POLL);
    }
}

fn output_file(path: &Path) -> Result<File, String> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
        .map_err(|e| format!("command output {}: {e}", path.display()))?;

    Ok(file)
}

fn within_limit(file: &File) -> Result<(), String> {
    let size = file
        .metadata()
        .map_err(|e| format!("command output metadata: {e}"))?
        .len();
    if size > OUTPUT_LIMIT {
        return Err("command output limit exceeded".into());
    }
    Ok(())
}

fn read_output(mut file: File) -> Result<String, String> {
    within_limit(&file)?;
    file.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    let mut bytes = Vec::new();
    file.take(OUTPUT_LIMIT + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > OUTPUT_LIMIT {
        return Err("command output grew beyond limit".into());
    }
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

/// The caller retains the recovery hold and serializes commands using this lease.
/// `dir` keeps the raw stdout/stderr files; no executable snapshot or hash is made.
pub(crate) fn run(
    lease: &Lease,
    program: &Path,
    args: &[String],
    dir: &Path,
    operation: &str,
    timeout: Duration,
) -> Result<Output, String> {
    supervise(lease, program, args, dir, operation, timeout, false)
}

/// Same-executable workers must call worker_guard before doing any device work.
/// Their inherited stdin is only a parent-liveness pipe, never application input.
pub(crate) fn run_worker(
    lease: &Lease,
    program: &Path,
    args: &[String],
    dir: &Path,
    operation: &str,
    timeout: Duration,
) -> Result<Output, String> {
    supervise(lease, program, args, dir, operation, timeout, true)
}

/// A verified same-executable worker retains the liveness pipe and keeps ADB in
/// its recorded group. The ordinary transport API remains unchanged for callers
/// that have their own producer supervision.
pub(crate) fn run_guarded_transport(
    lease: &Lease,
    program: &Path,
    args: &[String],
    dir: &Path,
    operation: &str,
    timeout: Duration,
) -> Result<Output, String> {
    let mut worker_args = vec!["__lease-transport-worker".into(), program.to_string_lossy().into_owned()];
    worker_args.extend_from_slice(args);
    run_worker(lease, &std::env::current_exe().map_err(|e| e.to_string())?,
        &worker_args, dir, operation, timeout)
}

/// Internal only: authority is checked before a transport can be spawned.
pub(crate) fn transport_worker(args: &[String]) -> i32 {
    if let Err(e) = worker_guard() {
        eprintln!("transport worker: {e}");
        return 90;
    }
    let Some(program) = args.first() else { return 90; };
    // No new session/group: the verified worker's watchdog owns this process too.
    match crate::fd_lock::transport_command(program).args(&args[1..]).stdin(Stdio::null()).status() {
        Ok(status) => status.code().unwrap_or_else(|| 128 + status.signal().unwrap_or(0)),
        Err(e) => { eprintln!("transport could not start: {e}"); 90 }
    }
}

fn supervise(
    lease: &Lease,
    program: &Path,
    args: &[String],
    dir: &Path,
    operation: &str,
    timeout: Duration,
    worker: bool,
) -> Result<Output, String> {
    std::fs::create_dir_all(dir).map_err(|e| format!("command output directory: {e}"))?;
    let stem = format!(
        "{}-{:06}",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    );
    let out = output_file(&dir.join(format!("{stem}.stdout")))?;
    let err = output_file(&dir.join(format!("{stem}.stderr")))?;
    let mut command = if worker { Command::new(program) } else { crate::fd_lock::transport_command(program) };
    command.args(args).stdout(out.try_clone().map_err(|e| e.to_string())?)
        .stderr(err.try_clone().map_err(|e| e.to_string())?).process_group(0);
    let deadline = Instant::now().checked_add(timeout).ok_or("command timeout is too large")?;
    lease.command(&mut command)?;
    if worker {
        let deadline_ms = std::time::SystemTime::now().checked_add(timeout)
            .ok_or("worker deadline is too large")?.duration_since(std::time::UNIX_EPOCH).map_err(|e| e.to_string())?.as_millis();
        // The token binds a worker to the session its parent created under
        // this lease; the inherited lock fd remains the device authority.
        command.stdin(Stdio::piped()).env("PHONEFARM_WORKER_SERIAL", &lease.serial)
            .env("PHONEFARM_WORKER_TOKEN", lease.token())
            .env("PHONEFARM_WORKER_DEADLINE_MS", deadline_ms.to_string());
    } else { command.stdin(Stdio::null()); }
    let lifeline = if worker { None } else { Some(crate::fd_lock::transport_lifeline(&mut command)?) };
    let mut child = command.spawn().map_err(|e| format!("{operation}: command could not spawn: {e}"))?;

    let _parent_pipe = lifeline.map(|(parent, child)| { drop(child); parent });
    let result: Result<ExitStatus, String> = loop {
        if let Err(error) = within_limit(&out).and_then(|_| within_limit(&err)) { break Err(error); }
        if Instant::now() >= deadline {
            break Err("command deadline exceeded".into());
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                let grace = Instant::now() + Duration::from_millis(100);
                while !group_gone(child.id()) && Instant::now() < grace {
                    std::thread::sleep(POLL);
                }
                if !group_gone(child.id()) {
                    break Err("command exited but its process group remains live/unknown".into());
                }
                break Ok(status);
            }
            Ok(None) => std::thread::sleep(POLL),
            Err(e) => break Err(format!("command wait failed: {e}")),
        }
    };
    let gone = result.is_ok() || stop_and_reap(&mut child);
    let status = result.map_err(|e| {
        format!("{operation}: {e}; local_group_gone={gone}; remote activity not verified stopped")
    })?;
    Ok(Output {
        code: status
            .code()
            .unwrap_or_else(|| 128 + status.signal().unwrap_or(0)),
        stdout: read_output(out)?,
        stderr: read_output(err)?,
        stdout_path: dir.join(format!("{stem}.stdout")),
    })
}

pub(crate) fn worker_remaining() -> Result<Duration, String> {
    crate::fd_lock::inherited()?.ok_or("worker requires inherited task lock")?;
    let ms: u64 = std::env::var("PHONEFARM_WORKER_DEADLINE_MS").map_err(|e| e.to_string())?
        .parse().map_err(|e| format!("worker deadline: {e}"))?;
    std::time::UNIX_EPOCH.checked_add(Duration::from_millis(ms)).ok_or("worker deadline is too large")?
        .duration_since(std::time::SystemTime::now()).map_err(|_| "worker deadline elapsed".into())
}

/// Called once by the internal worker entry point, before opening transports.
/// A dead or suspended parent cannot leave the verified worker group unbounded.
pub(crate) fn worker_guard() -> Result<(), String> {
    let remaining = worker_remaining()?;
    let group = std::process::id() as i32;
    let stop = move || unsafe {
        libc::kill(-group, libc::SIGKILL);
        libc::_exit(137);
    };
    std::thread::Builder::new()
        .name("lease-worker-lifeline".into())
        .spawn(move || {
            let mut byte = [0];
            let _ = std::io::stdin().read(&mut byte);
            stop();
        })
        .map_err(|e| format!("worker lifeline guard: {e}"))?;
    std::thread::Builder::new()
        .name("lease-worker-deadline".into())
        .spawn(move || {
            std::thread::sleep(remaining);
            stop();
        })
        .map_err(|e| format!("worker deadline guard: {e}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    struct Scratch(std::path::PathBuf);
    impl Scratch {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "pf-command-test-{}-{}",
                std::process::id(),
                SEQUENCE.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
        fn lease(&self) -> Lease {
            Lease::acquire(
                &self.0.join("devices"),
                &self.0.join("legacy"),
                "fake-command",
                "test",
                Duration::from_millis(100),
            )
            .unwrap()
        }

    }
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn command_preserves_output() {
        let scratch = Scratch::new();
        let lease = scratch.lease();
        let result = run(
            &lease,
            Path::new("/bin/sh"),
            &[
                "-c".into(),
                "printf output; printf diagnostic >&2; exit 7".into(),
            ],
            &scratch.0.join("commands"),
            "fake",
            Duration::from_secs(2),
        )
        .unwrap();
        assert_eq!(
            (result.code, result.stdout.as_str(), result.stderr.as_str()),
            (7, "output", "diagnostic")
        );
    }

    #[test]
    fn worker_keeps_liveness_pipe_until_timeout() {
        let scratch = Scratch::new();
        let lease = scratch.lease();
        let error = run_worker(
            &lease,
            Path::new("/bin/sh"),
            &[
                "-c".into(),
                "test -n \"$PHONEFARM_WORKER_SERIAL\" || exit 3; IFS= read -r unused; exit 4"
                    .into(),
            ],
            &scratch.0.join("commands"),
            "worker-pipe",
            Duration::from_millis(250),
        )
        .unwrap_err();
        assert!(error.contains("deadline exceeded"), "{error}");
    }

    #[test]
    fn worker_receives_the_lease_token_and_serial() {
        let scratch = Scratch::new();
        let lease = scratch.lease();
        let result = run_worker(
            &lease,
            Path::new("/bin/sh"),
            &[
                "-c".into(),
                "printf '%s %s' \"$PHONEFARM_WORKER_TOKEN\" \"$PHONEFARM_WORKER_SERIAL\"".into(),
            ],
            &scratch.0.join("commands"),
            "worker-identity",
            Duration::from_secs(2),
        )
        .unwrap();
        assert_eq!(
            (result.code, result.stdout),
            (0, format!("{} {}", lease.token(), lease.serial))
        );
    }

    #[test]
    fn timeout_and_remaining_group_cannot_complete_recovery() {
        let scratch = Scratch::new();
        let context = scratch.0.join("recovery.json");
        fs::write(&context, "{}").unwrap();
        let mut lease = scratch.lease();
        lease.hold_for_recovery(&context).unwrap();
        let error = run(
            &lease,
            Path::new("/bin/sleep"),
            &["5".into()],
            &scratch.0.join("commands"),
            "timeout",
            Duration::from_millis(250),
        )
        .unwrap_err();
        assert!(error.contains("deadline exceeded"), "{error}");
        let error = run(
            &lease,
            Path::new("/bin/sh"),
            &["-c".into(), "/bin/sleep 5 & exit 0".into()],
            &scratch.0.join("commands"),
            "descendant",
            Duration::from_secs(2),
        )
        .unwrap_err();
        assert!(error.contains("process group remains"), "{error}");
        assert_eq!(lease.recovery_context(), Some(context.as_path()));
        drop(lease);
        drop(scratch.lease());
    }
}
