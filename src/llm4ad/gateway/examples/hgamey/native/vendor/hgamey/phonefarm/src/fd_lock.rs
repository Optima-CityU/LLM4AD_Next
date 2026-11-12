//! One kernel lock protocol. Owner JSON is display data, never admission state.
use crate::device_registry::DeviceBinding;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::os::fd::{AsRawFd, RawFd};
use std::os::unix::{process::CommandExt, fs::OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

pub(crate) fn open_file(path: &Path, mode: u32) -> std::io::Result<File> {
    OpenOptions::new().read(true).write(true).create(true).truncate(false)
        .mode(mode).custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC).open(path)
}

pub(crate) const FDS: &str = "PF_LOCK_FDS";
pub(crate) const SCOPE: &str = "PF_LOCK_SCOPE";

#[derive(Clone, Debug, Serialize, Deserialize)]
pub(crate) struct Scope {
    pub key: Option<String>,
    pub serial: Option<String>,
    pub binding: Option<DeviceBinding>,
    // Only serial-derived scopes follow the registry during acquisition.
    // An explicitly saved binding keeps its original device identity.
    #[serde(skip)]
    pub resolve_alias: bool,
    // Only explicit restoration entry points may use a disabled device.
    #[serde(skip)]
    recovery: bool,
}
impl Scope {
    pub fn global() -> Self {
        Self {
            key: None,
            serial: None,
            binding: None,
            resolve_alias: false,
            recovery: false,
        }
    }
    pub fn device(serial: &str, binding: Option<DeviceBinding>) -> Self {
        Self {
            key: Some(
                binding
                    .as_ref()
                    .map(|b| b.device_id.clone())
                    .unwrap_or_else(|| serial.into()),
            ),
            serial: Some(serial.into()),
            binding,
            resolve_alias: false,
            recovery: false,
        }
    }
    pub fn for_recovery(mut self) -> Self {
        self.recovery = true;
        self
    }
    pub(crate) fn check_enabled(&self, farm: &Path) -> Result<(), String> {
        if self.recovery || self.serial.is_none() {
            return Ok(());
        }
        let (_, devices) = crate::device_registry::snapshot(farm)?;
        let device = devices.iter().find(|device| match &self.binding {
            Some(binding) => device.device_id == binding.device_id,
            None => device.aliases.iter().any(|alias| Some(alias) == self.serial.as_ref()),
        });
        if device.is_some_and(|device| !device.enabled) {
            return Err(crate::device_registry::DISABLED_DEVICE.into());
        }
        Ok(())
    }
    fn contains(&self, other: &Self) -> bool {
        self.key.is_none() || self.key == other.key
    }
}

pub(crate) fn device_path(root: &Path, key: &str) -> PathBuf {
    root.join("by-device-id")
        .join(format!("{:x}.lock", Sha256::digest(key.as_bytes())))
}

fn acquire_file(
    path: &Path,
    shared: bool,
    deadline: Option<Instant>,
    poll_cancel: &mut dyn FnMut() -> Result<(), String>,
) -> Result<File, String> {
    let file = crate::fd_lock::open_file(path, 0o666)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    loop {
        poll_cancel()?;
        match if shared {
            file.try_lock_shared()
        } else {
            file.try_lock()
        } {
            Ok(()) => return Ok(file),
            Err(std::fs::TryLockError::WouldBlock) => {
                if deadline.is_some_and(|until| Instant::now() >= until) {
                    return Err(format!("device lock busy: {}", path.display()));
                }
                std::thread::sleep(Duration::from_millis(25));
            }
            Err(error) => return Err(format!("{}: {error}", path.display())),
        }
    }
}

pub(crate) fn inherited() -> Result<Option<(Scope, Vec<RawFd>)>, String> {
    let Some(value) = std::env::var_os(FDS) else {
        return Ok(None);
    };
    let raw = value.to_str().ok_or("lock descriptors are not UTF-8")?;
    let fds: Vec<RawFd> = raw
        .split(',')
        .map(|value| value.parse::<RawFd>().map_err(|e| e.to_string()))
        .collect::<Result<_, _>>()?;
    for fd in &fds {
        if unsafe { libc::fcntl(*fd, libc::F_GETFD) } < 0 {
            return Err(format!(
                "inherited lock fd {fd} was closed; pass it through the subprocess launcher"
            ));
        }
    }
    let scope = serde_json::from_str(&std::env::var(SCOPE).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    Ok(Some((scope, fds)))
}

pub(crate) struct Guard {
    pub scope: Scope,
    files: Vec<File>,
    pub fds: Vec<RawFd>,
    label: String,
    farm: PathBuf,
}
impl Guard {
    pub fn acquire(
        device_root: &Path,
        farm_root: &Path,
        requested: Scope,
        label: &str,
        timeout: Option<Duration>,
    ) -> Result<Self, String> {
        Self::acquire_interruptible(device_root, farm_root, requested, label, timeout, &mut || Ok(()))
    }
    pub fn acquire_interruptible(
        device_root: &Path,
        farm_root: &Path,
        mut requested: Scope,
        label: &str,
        timeout: Option<Duration>,
        poll_cancel: &mut dyn FnMut() -> Result<(), String>,
    ) -> Result<Self, String> {
        poll_cancel()?;
        requested.check_enabled(farm_root)?;
        if let Some((scope, fds)) = inherited()? {
            if !scope.contains(&requested) {
                return Err("task lock does not cover the requested device/global scope; submit a global task for multiple devices".into());
            }
            return Ok(Self {
                scope: if scope.key.is_none() {
                    scope
                } else {
                    requested
                },
                files: Vec::new(),
                fds,
                label: label.into(),
                farm: farm_root.into(),
            });
        }
        std::fs::create_dir_all(farm_root).map_err(|e| e.to_string())?;
        let deadline = timeout.and_then(|duration| Instant::now().checked_add(duration));
        let farm = farm_root.join("phonefarm-bridge.lock");
        let files = loop {
            let mut files = Vec::new();
            if let Some(key) = &requested.key {
                let path = device_path(device_root, key);
                std::fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
                files.push(acquire_file(&path, false, deadline, poll_cancel)?);
            }
            files.push(acquire_file(&farm, requested.key.is_some(), deadline, poll_cancel)?);
            if requested.resolve_alias {
                let mut current = crate::devlease::resolve_scope_at(farm_root, requested.serial.as_deref().unwrap())?;
                current.recovery = requested.recovery;
                let changed = current.key != requested.key;
                requested = current;
                if changed {
                    // A registrar held farm EX between resolution and farm SH.
                    // Close both old fds before retrying the canonical key.
                    drop(files);
                    continue;
                }
            }
            requested.check_enabled(farm_root)?;
            break files;
        };
        let info_path = requested
            .key
            .as_ref()
            .map(|key| device_path(device_root, key))
            .unwrap_or(farm)
            .with_extension("owner.json");
        let info = json!({"scope": requested, "label": label, "pid": std::process::id(),
            "job_id": std::env::var("PF_JOB_ID").ok(), "actor": std::env::var("PF_JOB_ACTOR").ok(),
            "since": chrono::Utc::now().to_rfc3339()});
        if let Err(error) = fs::write(&info_path, info.to_string()) {
            eprintln!("lock owner display {}: {error}", info_path.display());
        }
        let fds: Vec<_> = files.iter().map(AsRawFd::as_raw_fd).collect();
        Ok(Self {
            scope: requested,
            files,
            fds,
            label: label.into(),
            farm: farm_root.into(),
        })
    }
    /// Only a task child inherits ownership; transport daemons never do.
    pub fn command(&self, cmd: &mut std::process::Command) -> Result<(), String> {
        task_transports(cmd, &self.farm)?;
        cmd.env(
            FDS,
            self.fds
                .iter()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
                .join(","),
        )
        .env(
            SCOPE,
            serde_json::to_string(&self.scope).expect("scope serialization"),
        )
        .env("DEVLOCK_LABEL", &self.label);
        if let Some(binding) = &self.scope.binding {
            cmd.env(
                "PF_JOB_DEVICE_BINDING",
                serde_json::to_string(binding).expect("binding serialization"),
            );
        }
        let fds = self.fds.clone();
        unsafe {
            cmd.pre_exec(move || {
                for fd in &fds {
                    let flags = libc::fcntl(*fd, libc::F_GETFD);
                    if flags < 0 || libc::fcntl(*fd, libc::F_SETFD, flags & !libc::FD_CLOEXEC) < 0 {
                        return Err(std::io::Error::last_os_error());
                    }
                }
                Ok(())
            });
        }
        Ok(())
    }
    pub fn owns(&self) -> bool {
        !self.files.is_empty()
    }
}
// Dropping files closes our copies. LOCK_UN would unlock copies still held by children.

/// Raw shell/Python tasks also use the transport boundary. The two symlinks are
/// launchers, not ownership records; they do not participate in acquisition.
fn task_transports(cmd: &mut std::process::Command, farm: &Path) -> Result<(), String> {
    if std::env::var_os("PF_TRANSPORT_PATH").is_some() {
        return Ok(());
    }
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let bin = farm.join("transport-bin").join(format!(
        "{:x}",
        Sha256::digest(exe.as_os_str().as_encoded_bytes())
    ));
    fs::create_dir_all(&bin).map_err(|e| e.to_string())?;
    for name in ["adb", "hdc"] {
        match std::os::unix::fs::symlink(&exe, bin.join(name)) {
            Ok(()) => (),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(error) => return Err(error.to_string()),
        }
    }
    let path = std::env::var_os("PATH").unwrap_or_default();
    let adb = crate::device::locate_adb().unwrap_or_else(|| "adb".into());
    let hdc = std::env::var("HDC_BIN").unwrap_or_else(|_| "hdc".into());
    cmd.env("PF_TRANSPORT_PATH", &path)
        .env("PF_TRANSPORT_ADB", &adb)
        .env("PF_TRANSPORT_HDC", hdc)
        .env("ADB_BIN", bin.join("adb"))
        .env("HDC_BIN", bin.join("hdc"));
    let paths = std::iter::once(bin).chain(std::env::split_paths(&path));
    cmd.env(
        "PATH",
        std::env::join_paths(paths).map_err(|e| e.to_string())?,
    );
    Ok(())
}

pub(crate) fn transport_entry() -> Option<i32> {
    let name = std::env::args_os()
        .next()
        .and_then(|path| PathBuf::from(path).file_name().map(|name| name.to_owned()))?;
    let key = match name.to_str()? {
        "adb" => "PF_TRANSPORT_ADB",
        "hdc" => "PF_TRANSPORT_HDC",
        _ => return None,
    };
    std::env::var_os(key)?;
    let mut cmd = transport_command(name);
    cmd.args(std::env::args_os().skip(1)).env(
        "PATH",
        std::env::var_os("PF_TRANSPORT_PATH").unwrap_or_default(),
    );
    // The proxy stays in the task group and holds the lock until the finite client exits.
    Some(crate::devlock::wait_control_child(&mut cmd))
}

/// A separately supervised transport group dies when its parent pipe closes.
/// The actual adb/hdc client cannot inherit either this pipe or the lock fds.
pub(crate) fn transport_lifeline(
    command: &mut std::process::Command,
) -> Result<
    (
        std::os::unix::net::UnixStream,
        std::os::unix::net::UnixStream,
    ),
    String,
> {
    let (parent, child) = std::os::unix::net::UnixStream::pair().map_err(|e| e.to_string())?;
    let fd = child.as_raw_fd();
    command.env("PF_TRANSPORT_LIFELINE", fd.to_string());
    unsafe {
        command.pre_exec(move || {
            if libc::fcntl(fd, libc::F_SETFD, 0) < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    Ok((parent, child))
}

/// A transport server must never retain the task's descriptors. The caller owns
/// the lease while the client runs, and the task group owns client cleanup.
pub(crate) fn transport_command(program: impl AsRef<std::ffi::OsStr>) -> std::process::Command {
    // The finite proxy retains the task fds; Python closes them for the actual
    // client, so a server fork cannot retain them. Reset kills the whole group.
    const PROXY: &str = r#"import fcntl,os,signal,subprocess,sys,threading,time
BOOTSTRAP = r'''import os,signal,subprocess,sys,threading
client=subprocess.Popen(sys.argv[1:],stdin=subprocess.DEVNULL,close_fds=True)
def stop(*unused): client.kill()
for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,stop)
def watch():
    os.read(0,1)
    stop()
threading.Thread(target=watch,daemon=True).start()
try:
    code=client.wait(timeout=12)
except subprocess.TimeoutExpired:
    client.kill()
    client.wait()
    print('hdc bootstrap timed out',file=sys.stderr)
    sys.exit(124)
sys.exit(code if code>=0 else 128-code)
'''
child=None
def forward(signum,frame):
    if child is not None:
        try: os.kill(child.pid,signum)
        except ProcessLookupError: pass
for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,forward)
lifeline=os.environ.pop('PF_TRANSPORT_LIFELINE',None)
if lifeline is not None:
    def watch():
        os.read(int(lifeline),1)
        os.killpg(os.getpgrp(),signal.SIGTERM)
        time.sleep(1)
        os.killpg(os.getpgrp(),signal.SIGKILL)
    threading.Thread(target=watch,daemon=True).start()
env=dict(os.environ)
for name in ('PF_LOCK_FDS','PF_LOCK_SCOPE'): env.pop(name,None)
hdc,program,*args=sys.argv[1:]
if hdc=='1':
    # HDC's ProgramMutex uses a POSIX record lock here. Check that live lock,
    # without trusting/storing a PID or launching an extra client on the hot path.
    tmp=next((env[k] for k in ('TMPDIR','TMP','TEMP','TEMPDIR') if k in env),'/tmp')
    running=False
    try:
        with open(tmp.rstrip('/')+'/.HDCServer.pid','rb') as mutex:
            try: fcntl.lockf(mutex,fcntl.LOCK_SH|fcntl.LOCK_NB)
            except BlockingIOError: running=True
    except FileNotFoundError: pass
    # HDC forks its server without setsid. Enumerate outside the caller's group
    # using the same wrapper/environment for credentials as the real request.
    # -p keeps the finite client from spawning a server inside the caller's group.
    if not running:
        # This supervisor survives caller-group cleanup. Its deadline/EOF handling
        # kills only the finite bootstrap client, never the server it forked.
        child=subprocess.Popen([sys.executable,'-c',BOOTSTRAP,program,'list','targets'],
                               env=env,close_fds=True,start_new_session=True,
                               stdin=subprocess.PIPE,stdout=subprocess.DEVNULL)
        code=child.wait()
        child.stdin.close()
        if code: sys.exit(code if code>=0 else 128-code)
    args.insert(0,'-p')
child=subprocess.Popen([program,*args],env=env,close_fds=True)
code=child.wait()
sys.exit(code if code>=0 else 128-code)
"#;
    let program = program.as_ref();
    let hdc = program == "hdc" || std::env::var_os("HDC_BIN").as_deref() == Some(program);
    let actual = if std::env::var_os("PF_TRANSPORT_PATH").is_some() {
        if program == "adb" || std::env::var_os("ADB_BIN").as_deref() == Some(program) {
            std::env::var_os("PF_TRANSPORT_ADB")
        } else if program == "hdc" || std::env::var_os("HDC_BIN").as_deref() == Some(program) {
            std::env::var_os("PF_TRANSPORT_HDC")
        } else {
            None
        }
    } else {
        None
    };
    let mut command = std::process::Command::new("python3");
    command
        .args(["-c", PROXY])
        .arg(if hdc { "1" } else { "0" })
        .arg(actual.as_deref().unwrap_or(program));
    if let Some(path) = std::env::var_os("PF_TRANSPORT_PATH") {
        command.env("PATH", path);
    }
    command
}

/// GOL receives these same open-file descriptions, allowing its human-pause path
/// to drop and reacquire the lock without reimplementing acquisition or cooldown.
pub(crate) fn handoff(args: &[String]) -> i32 {
    use std::io::Read;
    use std::os::fd::FromRawFd;
    let result = (|| -> Result<(), String> {
        let fd: RawFd = args
            .first()
            .ok_or("missing handoff socket")?
            .parse()
            .map_err(|e| format!("handoff socket: {e}"))?;
        let mut socket = unsafe { std::os::unix::net::UnixStream::from_raw_fd(fd) };
        unsafe {
            libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC);
        }
        let mut serial = None;
        let mut label = "game_opt_loop";
        let mut timeout = None;
        let mut it = args[1..].iter();
        while let Some(arg) = it.next() {
            match arg.as_str() {
                "--serial" => serial = it.next().map(String::as_str),
                "--label" => label = it.next().ok_or("missing label")?,
                "--timeout-ms" => {
                    timeout = Some(Duration::from_millis(
                        it.next()
                            .ok_or("missing timeout")?
                            .parse()
                            .map_err(|e| format!("timeout: {e}"))?,
                    ))
                }
                _ => return Err(format!("unknown handoff argument {arg}")),
            }
        }
        let storage = crate::lease_storage::resolve();
        let (root, farm) = storage.roots()?;
        let scope = serial
            .map(|serial| crate::devlease::resolve_scope(&storage, serial))
            .transpose()?
            .unwrap_or_else(Scope::global);
        let guard = Guard::acquire(&root, &farm, scope, label, timeout)?;
        let payload = serde_json::to_vec(&guard.scope).map_err(|e| e.to_string())?;
        crate::lock_handoff::send(&mut socket, &guard.fds, &payload).map_err(|e| e.to_string())?;
        let mut byte = [0];
        socket.read(&mut byte).map_err(|e| e.to_string())?;
        Ok(())
    })();
    match result {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("{error}");
            2
        }
    }
}

/// Read-only status probes do not create an inode or trust leftover owner JSON.
pub(crate) fn held(path: &Path, shared: bool) -> Result<bool, String> {
    let file = match File::options().read(true).write(true).open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(format!("{}: {error}", path.display())),
    };
    match if shared {
        file.try_lock_shared()
    } else {
        file.try_lock()
    } {
        Ok(()) => Ok(false),
        Err(std::fs::TryLockError::WouldBlock) => Ok(true),
        Err(error) => Err(format!("{}: {error}", path.display())),
    }
}
