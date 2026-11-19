//! Typed Android control with one durable PF owner and host-only mailbox clients.
use crate::{
    device_registry::DeviceBinding,
    devlease::Lease,
    game_deploy_transport::{q, Transport},
    host_process,
};
use serde_json::{json, Value};
use std::{
    collections::HashSet,
    ffi::CString,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::fs::OpenOptionsExt,
    },
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};

const STATE: &str = "session.json";
const SCHEMA: &str = "phonefarm/remote-control@1";
const PRIVATE: &str = "phonefarm/remote-control-state@1";
const ACTION: &str = "phonefarm/remote-control-action@1";
const RECEIPT: &str = "phonefarm/remote-control-action-result@1";
const LAUNCH: &str = "phonefarm/remote-control-launch@1";
const RETIREMENT: &str = "phonefarm/remote-control-startup-retirement@1";
const SCOPE: &str = "remote_control_commands_settled";
const LIMIT: usize = 64 * 1024;
const MAX_REQUESTS: usize = 4096;
const INPUT_CONTRACT: &str = "frame-bound-v1";
const FRAME_AGE_SECONDS: i64 = 30;
const ACTIONS: &[&str] = &[
    "screenshot",
    "logcat",
    "diagnostics",
    "tap",
    "swipe",
    "text",
    "key",
    "start_app",
    "stop_app",
    "renew",
    "release",
];
static TEMP: AtomicU64 = AtomicU64::new(0);
static INTERRUPTED: AtomicBool = AtomicBool::new(false);

#[derive(Debug)]
struct Error {
    code: &'static str,
    message: String,
    response: Option<Value>,
}
type Result<T> = std::result::Result<T, Error>;
fn error(code: &'static str, message: impl Into<String>) -> Error {
    Error {
        code,
        message: message.into(),
        response: None,
    }
}
fn io_error(message: impl Into<String>) -> Error {
    error("io_error", message)
}
impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        error(
            if e.kind() == std::io::ErrorKind::NotFound {
                "not_found"
            } else {
                "io_error"
            },
            e.to_string(),
        )
    }
}
fn now() -> String {
    chrono::Utc::now().to_rfc3339()
}
fn id(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
}
fn string<'a>(v: &'a Value, key: &str) -> Result<&'a str> {
    v[key]
        .as_str()
        .ok_or_else(|| error("invalid_request", format!("{key} must be a string")))
}
fn bounded(v: &Value, key: &str, lo: u64, hi: u64) -> Result<u64> {
    v[key]
        .as_u64()
        .filter(|n| (lo..=hi).contains(n))
        .ok_or_else(|| {
            error(
                "invalid_request",
                format!("{key} must be an integer in {lo}..{hi}"),
            )
        })
}
fn keys(v: &Value, allowed: &[&str]) -> Result<()> {
    let object = v
        .as_object()
        .ok_or_else(|| error("invalid_request", "expected a JSON object"))?;
    if let Some(k) = object.keys().find(|k| !allowed.contains(&k.as_str())) {
        return Err(error("invalid_request", format!("unsupported field {k}")));
    }
    Ok(())
}
fn serial_ok(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._:-".contains(&b))
        && !s.starts_with("hdc:")
        && !s.starts_with("ios:")
}
fn record(kind: &str, request_id: &str) -> String {
    format!("{kind}-{request_id}.json")
}

// Flat immutable request/result names keep every operation anchored to one directory
// descriptor. No client-supplied path is followed below that root.
struct Store {
    root: File,
    path: PathBuf,
}
impl Store {
    fn open(path: &Path) -> Result<Self> {
        if !path.is_absolute() || fs::symlink_metadata(path)?.file_type().is_symlink() {
            return Err(error(
                "invalid_request",
                "session must be an absolute, non-symlink directory",
            ));
        }
        let path = path.canonicalize()?;
        let root = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(&path)?;
        Ok(Self { root, path })
    }
    fn name(name: &str) -> Result<CString> {
        if name.is_empty() || name.contains('/') || name == "." || name == ".." {
            return Err(error("invalid_request", "invalid session filename"));
        }
        CString::new(name).map_err(|_| error("invalid_request", "NUL filename"))
    }
    fn file(&self, name: &str, flags: i32) -> Result<File> {
        let name = Self::name(name)?;
        let fd = unsafe {
            libc::openat(
                self.root.as_raw_fd(),
                name.as_ptr(),
                flags | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                0o600,
            )
        };
        if fd < 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        let file = unsafe { File::from_raw_fd(fd) };
        if !file.metadata()?.is_file() {
            return Err(io_error("session record is not a regular file"));
        }
        Ok(file)
    }
    fn read(&self, name: &str) -> Result<Value> {
        let file = self.file(name, libc::O_RDONLY)?;
        if file.metadata()?.len() > LIMIT as u64 {
            return Err(io_error("session record exceeds 64 KiB"));
        }
        let mut bytes = Vec::new();
        file.take((LIMIT + 1) as u64).read_to_end(&mut bytes)?;
        if bytes.len() > LIMIT {
            return Err(io_error("session record grew beyond its limit"));
        }
        serde_json::from_slice(&bytes).map_err(|e| io_error(format!("invalid session JSON: {e}")))
    }
    fn optional(&self, name: &str) -> Result<Option<Value>> {
        match self.read(name) {
            Ok(v) => Ok(Some(v)),
            Err(e) if e.code == "not_found" => Ok(None),
            Err(e) => Err(e),
        }
    }
    fn bytes(&self, name: &str, bytes: &[u8], replace: bool) -> Result<()> {
        let target = Self::name(name)?;
        let temp = format!(
            ".remote-{}-{}",
            std::process::id(),
            TEMP.fetch_add(1, Ordering::Relaxed)
        );
        let temp_c = Self::name(&temp)?;
        let mut file = self.file(&temp, libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL)?;
        let result = (|| -> Result<()> {
            file.write_all(bytes)?;
            file.sync_all()?;
            let rc = unsafe {
                if replace {
                    libc::renameat(
                        self.root.as_raw_fd(),
                        temp_c.as_ptr(),
                        self.root.as_raw_fd(),
                        target.as_ptr(),
                    )
                } else {
                    libc::linkat(
                        self.root.as_raw_fd(),
                        temp_c.as_ptr(),
                        self.root.as_raw_fd(),
                        target.as_ptr(),
                        0,
                    )
                }
            };
            if rc != 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            self.root.sync_all()?;
            Ok(())
        })();
        unsafe {
            libc::unlinkat(self.root.as_raw_fd(), temp_c.as_ptr(), 0);
        }
        result
    }
    fn write(&self, name: &str, value: &Value, replace: bool) -> Result<()> {
        self.bytes(
            name,
            &serde_json::to_vec(value).map_err(|e| io_error(e.to_string()))?,
            replace,
        )
    }
    fn lock(&self, name: &str, wait: Duration) -> Result<File> {
        let file = self.file(name, libc::O_RDWR)?;
        let until = Instant::now() + wait;
        loop {
            match file.try_lock() {
                Ok(()) => return Ok(file),
                Err(std::fs::TryLockError::WouldBlock) if Instant::now() < until => {
                    std::thread::sleep(Duration::from_millis(10))
                }
                Err(std::fs::TryLockError::WouldBlock) => {
                    return Err(error("conflict", format!("{name} is busy")))
                }
                Err(e) => return Err(io_error(e.to_string())),
            }
        }
    }
    fn names(&self) -> Result<Vec<String>> {
        // openat(".") supplies a fresh directory offset; dup would share it.
        let fd = unsafe {
            libc::openat(
                self.root.as_raw_fd(),
                c".".as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC,
            )
        };
        if fd < 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        let stream = unsafe { libc::fdopendir(fd) };
        if stream.is_null() {
            unsafe {
                libc::close(fd);
            }
            return Err(std::io::Error::last_os_error().into());
        }
        let mut names = Vec::new();
        loop {
            let entry = unsafe { libc::readdir(stream) };
            if entry.is_null() {
                break;
            }
            let name = unsafe { std::ffi::CStr::from_ptr((*entry).d_name.as_ptr()) }
                .to_string_lossy()
                .into_owned();
            if name != "." && name != ".." {
                names.push(name);
            }
            if names.len() > MAX_REQUESTS * 16 {
                unsafe {
                    libc::closedir(stream);
                }
                return Err(io_error("remote record budget exceeded"));
            }
        }
        unsafe {
            libc::closedir(stream);
        }
        Ok(names)
    }
    fn requests(&self) -> Result<Vec<Value>> {
        let mut values = Vec::new();
        for name in self
            .names()?
            .into_iter()
            .filter(|n| n.starts_with("request-"))
        {
            let request_id = name
                .strip_prefix("request-")
                .and_then(|s| s.strip_suffix(".json"))
                .filter(|s| id(s))
                .ok_or_else(|| io_error("invalid request filename"))?;
            let value = self.read(&name)?;
            if value["request"]["request_id"] != request_id {
                return Err(io_error("request filename/identity mismatch"));
            }
            validate_request(&value["request"])?;
            bounded(&value, "sequence", 1, MAX_REQUESTS as u64)?;
            values.push(value);
            if values.len() > MAX_REQUESTS {
                return Err(io_error("remote request budget exceeded"));
            }
        }
        values.sort_by_key(|v| v["sequence"].as_u64().unwrap());
        if values
            .windows(2)
            .any(|pair| pair[0]["sequence"] == pair[1]["sequence"])
        {
            return Err(io_error("duplicate request sequence"));
        }
        Ok(values)
    }
}
fn owner_identity(state: &Value) -> Result<host_process::Identity> {
    let o = &state["owner"];
    Ok(host_process::Identity {
        pid: bounded(o, "pid", 1, i32::MAX as u64)? as u32,
        start: string(o, "start")?.into(),
        host_boot_id: string(o, "host_boot_id")?.into(),
    })
}
fn saved_binding(record: &Value) -> Result<Option<DeviceBinding>> {
    match (record.get("device_binding"), record.get("lease_scope")) {
        (None, None) => Ok(None),
        (Some(value), Some(scope)) if scope == "device" => {
            let binding = crate::device_registry::parse_binding(&value.to_string()).map_err(io_error)?;
            if record["serial"] != binding.serial {
                return Err(io_error("remote device binding differs from original serial"));
            }
            Ok(Some(binding))
        }
        _ => Err(io_error("remote device binding/scope is incomplete")),
    }
}
fn attach_scope(value: &mut Value, binding: Option<&DeviceBinding>) {
    if let Some(binding) = binding {
        value["device_binding"] = json!(binding);
        value["lease_scope"] = json!("device");
    }
}
fn scoped_result(mut value: Value, original: &Value) -> Result<Value> {
    attach_scope(&mut value, saved_binding(original)?.as_ref());
    Ok(value)
}
/// PF reports actual fd ownership. Known B activity does not change A's scope.
fn scope_status<'a>(observed: &'a Value, binding: Option<&DeviceBinding>) -> Result<&'a str> {
    let status = if let Some(binding) = binding {
        let scopes = &observed["device_scopes"];
        if scopes["schema"] != "phonefarm/device-scopes@1"
            || scopes["unknown"] != false
            || scopes["global"]["status"] != "clear" {
            return Err(io_error("remote device scope/global ownership is unavailable"));
        }
        let devices = scopes["devices"].as_array().ok_or_else(|| io_error("missing device scope entries"))?;
        let mut matching = devices.iter().filter(|d| d["device_id"] == binding.device_id);
        let target = matching.next().ok_or_else(|| io_error("original device scope is missing"))?;
        if matching.next().is_some() { return Err(io_error("ambiguous device scope")); }
        target["status"].as_str()
    } else {
        observed["status"].as_str()
    };
    match status {
        Some(status @ ("clear" | "held" | "blocked")) => Ok(status),
        _ => Err(io_error("lease scope is unknown; remote release is unverified")),
    }
}
fn state(store: &Store) -> Result<Value> {
    let value = store.read(STATE)?;
    if value["schema"] != PRIVATE
        || !id(string(&value, "session_id")?)
        || !serial_ok(string(&value, "serial")?)
        || value["context"] != json!(store.path)
        || !matches!(
            value["phase"].as_str(),
            Some("starting" | "ready" | "closing" | "closed" | "recovery_required")
        )
    {
        return Err(io_error("remote session identity/schema mismatch"));
    }
    owner_identity(&value)?;
    saved_binding(&value)?;
    Ok(value)
}
fn live(store: &Store, state: &Value) -> Result<bool> {
    let running = host_process::inspect(&owner_identity(state)?).map_err(io_error)?
        == host_process::InstanceState::Alive;
    let held = match store.lock("owner.lock", Duration::ZERO) {
        Ok(_) => false,
        Err(e) if e.code == "conflict" => true,
        Err(e) => return Err(e),
    };
    if running && !held && state["phase"] != "closed" {
        return Err(io_error(
            "remote owner identity is alive without its owner lock",
        ));
    }
    if held && !running {
        return Err(io_error("remote owner lock holder is unverified"));
    }
    Ok(running && held)
}
fn owner_actions(s: &Value) -> Vec<&'static str> {
    ACTIONS.iter().copied().filter(|action| {
        s.get("action_capabilities").map_or(!matches!(*action, "logcat" | "diagnostics"), |declared| {
            declared.as_array().is_some_and(|items| items.iter().any(|v| v == action))
        })
    }).collect()
}
fn public(store: &Store, s: &Value) -> Result<Value> {
    let running = live(store, s)?;
    let phase = if s["phase"] == "closed" {
        if running {
            "closing"
        } else {
            "closed"
        }
    } else if !running {
        "recovery_required"
    } else if s["phase"] == "ready"
        && store
            .requests()?
            .iter()
            .any(|e| e["request"]["action"] == "release")
    {
        // Publication already freezes new requests. The running finite action
        // may not have returned to the owner loop to save phase=closing yet.
        "closing"
    } else {
        string(s, "phase")?
    };
    scoped_result(
        json!({"schema":SCHEMA,"session_id":s["session_id"],"serial":s["serial"],"phase":phase,
        "live_owner":running,"capabilities":owner_actions(s),"created_at":s["created_at"],"deadline":s["deadline"],
        "ttl_seconds":s["ttl_seconds"],"last_request_id":s["last_request_id"],"latest_image":s["latest_image"],
        "input_contract":s["input_contract"],"input_frame_id":s["input_frame_id"],
        "frame_max_age_seconds":if s["input_contract"] == INPUT_CONTRACT {json!(FRAME_AGE_SECONDS)} else {Value::Null},
        "stop_reason":s["stop_reason"],"error":s["error"],"recovery":{"required":phase=="recovery_required",
        "scope":SCOPE,"game_state_restored":false,"device_verified":false,"report":s["recovery_report"]}}),
        s,
    )
}
fn startup_status(store: &Store) -> Result<Value> {
    let mut proof = store.read("startup.json")?;
    if proof["schema"] != "phonefarm/remote-control-startup@1"
        || proof["state"] != "not_started"
        || proof["not_started"] != true
        || proof["device_io_started"] != false
        || proof["context"] != json!(store.path)
        || !serial_ok(string(&proof, "serial")?)
    {
        return Err(io_error("invalid startup-not-started evidence"));
    }
    saved_binding(&proof)?;
    let observed = host_process::inspect(&owner_identity(&proof)?);
    proof["live_owner"] = match observed {
        Ok(value) => json!(value == host_process::InstanceState::Alive),
        Err(e) => {
            proof["host_error"] = json!(e);
            Value::Null
        }
    };
    Ok(proof)
}
fn launch(store: &Store) -> Result<Value> {
    let v = store.optional("launch.json")?.ok_or_else(|| error("recovery_required",
        "startup launch identity is missing; cannot retire this acquisition or reconstruct its owner identity"))?;
    if v["schema"] != LAUNCH
        || v["context"] != json!(store.path)
        || !id(string(&v, "launch_id")?)
        || !serial_ok(string(&v, "serial")?)
    {
        return Err(io_error("startup launch identity/schema mismatch"));
    }
    owner_identity(&v)?;
    saved_binding(&v)?;
    Ok(v)
}
fn startup_records(store: &Store, launch: &Value) -> Result<Option<Value>> {
    // A request, started receipt, transport directory, or any unexplained file
    // excludes this narrow retirement path. Never erase evidence to qualify.
    for name in store.names()? {
        if ![
            "owner.lock",
            "publisher.lock",
            "launch.json",
            STATE,
            "startup-retired.json",
        ]
        .contains(&name.as_str())
        {
            return Err(error(
                "recovery_required",
                format!(
                    "startup retirement refuses action/transport or unexplained evidence: {name}"
                ),
            ));
        }
    }
    let s = if store.optional(STATE)?.is_some() {
        Some(state(store)?)
    } else {
        None
    };
    if let Some(s) = &s {
        if s["phase"] != "starting"
            || s["session_id"] != launch["launch_id"]
            || s["serial"] != launch["serial"]
            || s["owner"] != launch["owner"]
            || s["device_binding"] != launch["device_binding"]
            || s["lease_scope"] != launch["lease_scope"]
            || s["transport_uncertain"] != false
            || !s["pending_request"].is_null()
            || !s["last_request_id"].is_null()
            || !s["latest_image"].is_null()
        {
            return Err(error("recovery_required", "session progressed beyond unstarted acquisition; startup retirement is unavailable"));
        }
    }
    Ok(s)
}
fn retired_status(store: &Store, proof: &Value) -> Result<Value> {
    let launch = launch(store)?;
    let original = startup_records(store, &launch)?;
    if proof["schema"] != RETIREMENT
        || proof["original_launch"] != launch
        || proof["original_state"] != json!(original)
        || proof["context"] != json!(store.path)
        || proof["serial"] != launch["serial"]
        || proof["original_outcome"] != "unknown"
        || proof["origin"] != "operator_assertion"
        || proof["device_verified"] != false
        || proof["lease_modified"] != false
    {
        return Err(io_error("invalid startup retirement evidence"));
    }
    if host_process::inspect(&owner_identity(&launch)?).map_err(io_error)?
        == host_process::InstanceState::Alive
    {
        return Err(error("conflict", "original startup owner is still alive"));
    }
    scoped_result(
        json!({"schema":SCHEMA,"session_id":launch["launch_id"],"serial":launch["serial"],
        "phase":"closed","live_owner":false,"capabilities":[],"original_outcome":"unknown",
        "stop_reason":"operator_reconciled_startup","recovery":{"required":false,
        "scope":"remote_startup_acquisition_retired","origin":"operator_assertion","device_verified":false,
        "lease_modified":false,"game_state_restored":false,"report":"startup-retired.json"}}),
        &launch,
    )
}
fn session_status(store: &Store) -> Result<Value> {
    if let Some(proof) = store.optional("startup-retired.json")? {
        return retired_status(store, &proof);
    }
    if store.optional("startup.json")?.is_some() {
        return startup_status(store);
    }
    if store.optional(STATE)?.is_some() {
        return public(store, &state(store)?);
    }
    let launch = launch(store)?;
    let running = live(store, &launch)?;
    scoped_result(
        json!({"schema":SCHEMA,"session_id":launch["launch_id"],"serial":launch["serial"],
        "phase":if running { "starting" } else { "recovery_required" },"live_owner":running,
        "capabilities":[],"original_outcome":"unknown","recovery":{"required":!running,
        "device_verified":false,"game_state_restored":false}}),
        &launch,
    )
}
// Caller holds owner.lock exclusively and publisher.lock. This only retires the
// host acquisition reference; it never acquires, clears, or modifies any lease.
fn retire_startup(
    store: &Store,
    serial: &str,
    note: &str,
    observe: impl FnOnce() -> Value,
) -> Result<Value> {
    let launch = launch(store)?;
    if launch["serial"] != serial {
        return Err(error("conflict", "recovery serial mismatch"));
    }
    if host_process::inspect(&owner_identity(&launch)?).map_err(io_error)?
        == host_process::InstanceState::Alive
    {
        return Err(error("conflict", "original startup owner is still alive"));
    }
    if note.trim().is_empty() || note.len() > 4096 {
        return Err(error(
            "invalid_request",
            "startup retirement requires an independent verification note",
        ));
    }
    if let Some(proof) = store.optional("startup-retired.json")? {
        return retired_status(store, &proof);
    }
    let original = startup_records(store, &launch)?;
    let observed = observe();
    let binding = saved_binding(&launch)?;
    if scope_status(&observed, binding.as_ref())? != "clear"
        || (binding.is_none() && (observed["bridge_held"] != false
            || !observed["error"].is_null()
            || !observed["entries"].as_array().is_some_and(Vec::is_empty))) {
        return Err(error("recovery_required", "startup retirement requires the original lease scope clear; independently verify device idle and reconcile its abandoned lease first; this command clears no lease"));
    }
    let proof = json!({"schema":RETIREMENT,"context":store.path,"serial":serial,"original_launch":launch,
        "original_state":original,"original_outcome":"unknown","origin":"operator_assertion",
        "device_verified":false,"lease_modified":false,"game_state_restored":false,
        "note":note,"at":now(),"lease_observation":observed});
    store.write("startup-retired.json", &proof, false)?;
    retired_status(store, &proof)
}
fn initial(e: &Value) -> Value {
    json!({"schema":RECEIPT,"session_id":e["request"]["session_id"],"request_id":e["request"]["request_id"],
        "sequence":e["sequence"],"status":"accepted","requested_at":e["requested_at"],"started_at":null,"finished_at":null,"result":null,"error":null})
}
fn receipt(store: &Store, s: &Value, request_id: &str) -> Result<Value> {
    if !id(request_id) {
        return Err(error("invalid_request", "invalid request_id"));
    }
    let e = store.read(&record("request", request_id))?;
    if e["request"]["session_id"] != s["session_id"] {
        return Err(io_error("receipt session mismatch"));
    }
    if let Some(done) = store.optional(&record("result", request_id))? {
        return Ok(done);
    }
    let mut r = store
        .optional(&record("started", request_id))?
        .unwrap_or_else(|| initial(&e));
    if !live(store, s)? {
        if let Some(done) = store.optional(&record("result", request_id))? {
            return Ok(done);
        }
        let started = store.optional(&record("started", request_id))?;
        if let Some(started) = started {
            r = started;
        }
        r["status"] = json!(if r["status"] == "started" {
            "unknown"
        } else {
            "not_executed"
        });
        r["error"] = json!("owner stopped without a result; do not replay a started action");
    }
    Ok(r)
}

fn key_code(value: &Value) -> Result<u64> {
    if let Some(v) = value.as_u64().filter(|n| *n <= 400) {
        return Ok(v);
    }
    let key = value
        .as_str()
        .ok_or_else(|| error("invalid_request", "key must be a named key or 0..400"))?
        .to_ascii_lowercase();
    let code = match key.as_str() {
        "back" => 4,
        "home" => 3,
        "recents" | "app_switch" => 187,
        "menu" => 82,
        "enter" => 66,
        "del" | "backspace" => 67,
        "power" => 26,
        "volume_up" => 24,
        "volume_down" => 25,
        "tab" => 61,
        "escape" => 111,
        "wakeup" => 224,
        "sleep" => 223,
        "up" => 19,
        "down" => 20,
        "left" => 21,
        "right" => 22,
        _ => key
            .parse::<u64>()
            .ok()
            .filter(|n| *n <= 400)
            .ok_or_else(|| error("invalid_request", "unsupported key"))?,
    };
    Ok(code)
}
fn validate_package(p: &Value) -> Result<()> {
    let package = string(p, "package")?;
    if package.is_empty() || package.len() > 200
        || !package.bytes().all(|b| b.is_ascii_alphanumeric() || b"._".contains(&b)) {
        return Err(error("invalid_request", "invalid package"));
    }
    Ok(())
}
fn validate_request(r: &Value) -> Result<()> {
    keys(
        r,
        &["schema", "session_id", "request_id", "action", "params"],
    )?;
    if r["schema"] != ACTION || !id(string(r, "session_id")?) || !id(string(r, "request_id")?) {
        return Err(error(
            "invalid_request",
            "invalid action schema/session/request identity",
        ));
    }
    let action = string(r, "action")?;
    let p = &r["params"];
    let coordinate = |k| bounded(p, k, 0, 100_000).map(|_| ());
    match action {
        "screenshot" => { keys(p, &["max_side"])?; if p.get("max_side").is_some() { bounded(p, "max_side", 0, 8192)?; } }
        "logcat" => {
            keys(p, &["lines", "pid"])?;
            if p.get("lines").is_some() { bounded(p, "lines", 1, 2000)?; }
            if p.get("pid").is_some() { bounded(p, "pid", 1, i32::MAX as u64)?; }
        }
        "diagnostics" => {
            keys(p, &["package"])?;
            if p.get("package").is_some() { validate_package(p)?; }
        }
        "tap" => { keys(p, &["x", "y", "frame_id"])?; coordinate("x")?; coordinate("y")?; }
        "swipe" => { keys(p, &["x1", "y1", "x2", "y2", "ms", "frame_id"])?; for k in ["x1", "y1", "x2", "y2"] { coordinate(k)?; } bounded(p, "ms", 1, 10_000)?; }
        "text" => { keys(p, &["text"])?; let text = string(p, "text")?; if text.is_empty() || text.chars().count() > 1000 || text.chars().any(char::is_control) { return Err(error("invalid_request", "text requires 1..1000 visible characters")); } }
        "key" => { keys(p, &["key"])?; key_code(&p["key"])?; }
        "start_app" | "stop_app" => {
            keys(p, if action == "start_app" { &["package", "activity"] } else { &["package"] })?;
            validate_package(p)?;
            if let Some(activity) = p.get("activity") {
                let a = activity.as_str().ok_or_else(|| error("invalid_request", "activity must be a string"))?;
                if a.is_empty() || a.len() > 300 || !a.bytes().all(|b| b.is_ascii_alphanumeric() || b"._$/".contains(&b)) { return Err(error("invalid_request", "invalid activity")); }
            }
        }
        "renew" | "release" => keys(p, &[])?,
        _ => return Err(error("unsupported", "action is not supported by this owner; raw shell/install/files/HDC/video are unavailable")),
    }
    if matches!(action, "tap" | "swipe") && !id(string(p, "frame_id")?) {
        return Err(error("invalid_request", "frame_id must identify a screenshot request"));
    }
    Ok(())
}
fn publish(store: &Store, r: &Value) -> Result<Value> {
    validate_request(r)?;
    let _publisher = store.lock("publisher.lock", Duration::from_secs(2))?;
    let s = state(store)?;
    if r["session_id"] != s["session_id"] {
        return Err(error("conflict", "session_id mismatch"));
    }
    let request_id = string(r, "request_id")?;
    if let Some(prior) = store.optional(&record("request", request_id))? {
        if prior["request"] != *r {
            return Err(error(
                "conflict",
                "request ID already has different content",
            ));
        }
        return receipt(store, &s, request_id);
    }
    if !owner_actions(&s).contains(&string(r, "action")?) {
        return Err(error("unsupported", "action is not supported by this owner; query the original request ID"));
    }
    if matches!(r["action"].as_str(), Some("tap" | "swipe")) && s["input_contract"] != INPUT_CONTRACT {
        return Err(error("input_contract_unavailable", "this owner does not support frame-bound input; query original receipts or close it explicitly"));
    }
    if !live(store, &s)? {
        return Err(error(
            "owner_unavailable",
            "owner is not running; inspect recovery",
        ));
    }
    if s["phase"] != "ready" && !(s["phase"] == "starting" && r["action"] == "release") {
        return Err(error("session_closed", "owner is not accepting actions"));
    }
    let requests = store.requests()?;
    if requests.iter().any(|e| e["request"]["action"] == "release") {
        return Err(error("session_closed", "release is already queued"));
    }
    if requests.len() >= MAX_REQUESTS
        || (requests.len() == MAX_REQUESTS - 1 && r["action"] != "release")
    {
        return Err(error(
            "session_closed",
            "request budget reached; final slot is reserved for release",
        ));
    }
    let seq = requests
        .last()
        .map_or(1, |e| e["sequence"].as_u64().unwrap() + 1);
    let e = json!({"sequence":seq,"requested_at":now(),"request":r});
    store.write(&record("request", request_id), &e, false)?;
    Ok(initial(&e))
}
fn save(store: &Store, s: &mut Value) -> Result<()> {
    s["updated_at"] = json!(now());
    store.write(STATE, s, true)
}
fn shell_command(r: &Value) -> Result<(String, u64)> {
    let p = &r["params"];
    Ok(match string(r, "action")? {
        "logcat" => {
            let mut command = format!("logcat -d -t {} -v threadtime", p["lines"].as_u64().unwrap_or(200));
            if let Some(pid) = p["pid"].as_u64() { command.push_str(&format!(" --pid={pid}")); }
            (command, 20)
        }
        "diagnostics" => {
            // Selected read-only facts, not installation/root/readiness qualification.
            // Per-section prefixes keep even verbose Android dumps bounded on-device.
            let mut command = String::from(r#"printf '[device properties; selected fields, up to 2048 bytes]\n'
(for p in ro.product.manufacturer ro.product.model ro.build.version.release ro.build.version.sdk ro.product.cpu.abilist ro.debuggable; do printf '%s=' "$p"; getprop "$p"; done) 2>&1 | head -c 2048
printf '\n[ADB shell identity; up to 512 bytes; su not probed]\n'
id 2>&1 | head -c 512
printf '\n[display size/density; up to 1024 bytes]\n'
(wm size; wm density) 2>&1 | head -c 1024
printf '\n[foreground activity; selected fields, up to 2048 bytes]\n'
dumpsys activity activities 2>&1 | grep -E 'mResumedActivity|topResumedActivity|ResumedActivity|Permission Denial|Error|not found' | head -c 2048
"#);
            if let Some(package) = p["package"].as_str() {
                command.push_str(&format!(r#"printf '\n[requested package {}; selected fields, up to 2048 bytes]\n'
(pm path --user 0 {}; dumpsys package {} | grep -E 'versionCode=|versionName=|userId=|User 0:|Permission Denial|Error|not found') 2>&1 | head -c 2048
"#, package, q(package), q(package)));
            }
            command.push_str("printf '\n[scope] Selected read-only observations only; empty/failed fields are unknown. ADB command completion does not verify physical connection type, root availability, app readiness or scene state.\n'");
            (command, 20)
        }
        "tap" => (format!("input tap {} {}", p["x"], p["y"]), 10),
        "swipe" => (
            format!(
                "input swipe {} {} {} {} {}",
                p["x1"], p["y1"], p["x2"], p["y2"], p["ms"]
            ),
            12 + p["ms"].as_u64().unwrap() / 1000,
        ),
        "text" => (
            format!(
                "input text {}",
                q(&string(p, "text")?.replace('%', "\\%").replace(' ', "%s"))
            ),
            20,
        ),
        "key" => (format!("input keyevent {}", key_code(&p["key"])?), 10),
        "stop_app" => (format!("am force-stop {}", q(string(p, "package")?)), 20),
        "start_app" => {
            let package = string(p, "package")?;
            let command = if let Some(activity) = p["activity"].as_str() {
                format!(
                    "am start -n {}",
                    q(&if activity.contains('/') {
                        activity.into()
                    } else {
                        format!("{package}/{activity}")
                    })
                )
            } else {
                format!("c=$(cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER {} | tail -n 1); case \"$c\" in */*) am start -n \"$c\";; *) echo 'no launcher activity' >&2; exit 3;; esac", q(package))
            };
            (command, 20)
        }
        _ => return Err(error("unsupported", "not a shell action")),
    })
}
fn rotation(lease: &Lease, transport: &mut Transport) -> Result<Option<u32>> {
    let out = transport.shell(lease, "remote-display-rotation", "dumpsys input", 5)
        .map_err(|e| error(if e.uncertain { "unknown" } else { "io_error" }, e.message))?;
    Ok(if out.code == 0 { crate::gray_session::rotation_from_input(&out.stdout).ok() } else { None })
}

fn capture_image(store: &Store, lease: &Lease, transport: &mut Transport, s: &Value, r: &Value, verify: bool) -> Result<Value> {
    let request_id = string(r, "request_id")?;
    let before = rotation(lease, transport)?;
    let captured_at = now();
    let out = transport
        .raw(
            lease,
            "remote-screenshot",
            vec!["exec-out".into(), "screencap -p".into()],
            20,
        )
        .map_err(|e| error(if e.uncertain { "unknown" } else { "io_error" }, e.message))?;
    if out.code != 0 {
        return Err(error(
            "unknown",
            "ADB screenshot did not confirm successful completion",
        ));
    }
    let bytes = fs::read(&out.stdout_path)?;
    if bytes.len() < 24 || !bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Err(error("unknown", "screenshot is not a complete PNG"));
    }
    let w = u32::from_be_bytes(bytes[16..20].try_into().unwrap());
    let h = u32::from_be_bytes(bytes[20..24].try_into().unwrap());
    if w == 0 || h == 0 || w > 8192 || h > 8192 || u64::from(w) * u64::from(h) > 24_000_000 {
        return Err(error("unknown", "screenshot dimensions exceed limits"));
    }
    let mut img = image::load_from_memory_with_format(&bytes, image::ImageFormat::Png)
        .map_err(|e| error("unknown", format!("invalid PNG: {e}")))?;
    let after = rotation(lease, transport)?;
    let rotation = if before == after { after } else { None };
    let max_side = r["params"]["max_side"].as_u64().unwrap_or(1280) as u32;
    if max_side > 0 && w.max(h) > max_side {
        img = img.resize(max_side, max_side, image::imageops::FilterType::Triangle);
    }
    let mut encoded = std::io::Cursor::new(Vec::new());
    img.write_to(&mut encoded, image::ImageFormat::Png)
        .map_err(|e| io_error(e.to_string()))?;
    if encoded.get_ref().len() > 8 * 1024 * 1024 {
        return Err(io_error("encoded screenshot exceeds 8 MiB"));
    }
    let name = if verify {format!("verification-{request_id}.png")} else {format!("artifact-{request_id}.png")};
    store.bytes(&name, encoded.get_ref(), false)?;
    Ok(
        json!({"exit":0,"relative_path":name,"mime":"image/png","width":img.width(),"height":img.height(),
        "source_width":w,"source_height":h,"original_size":[w,h],"bytes":encoded.get_ref().len(),"captured_at":captured_at,
        "frame_id":request_id,"session_id":s["session_id"],"serial":s["serial"],"rotation":rotation}),
    )
}

fn check_frame(s: &Value, r: &Value) -> Result<()> {
    let image = &s["latest_image"];
    if image.is_null() || image["frame_id"] != r["params"]["frame_id"]
        || image["session_id"] != s["session_id"] || image["serial"] != s["serial"] {
        return Err(error("frame_unavailable", "frame does not identify the latest screenshot in this session"));
    }
    let at = image["captured_at"].as_str().and_then(|v| chrono::DateTime::parse_from_rfc3339(v).ok())
        .ok_or_else(|| error("frame_stale", "frame capture time is unavailable"))?;
    let age = chrono::Utc::now().signed_duration_since(at).num_milliseconds();
    if !(0..=FRAME_AGE_SECONDS * 1000).contains(&age) {
        return Err(error("frame_stale", "capture again before coordinate input"));
    }
    if !image["rotation"].as_u64().is_some_and(|n| n <= 3) {
        return Err(error("frame_orientation_unknown", "capture orientation is unknown"));
    }
    let w = image["source_width"].as_u64().unwrap_or(0);
    let h = image["source_height"].as_u64().unwrap_or(0);
    let points: &[(&str, &str)] = if r["action"] == "tap" { &[("x", "y")] } else {&[("x1", "y1"), ("x2", "y2")]};
    if points.iter().any(|(x,y)| !r["params"][*x].as_u64().is_some_and(|n| n < w)
        || !r["params"][*y].as_u64().is_some_and(|n| n < h)) {
        return Err(error("coordinate_out_of_bounds", "coordinates must be inside the source screenshot"));
    }
    Ok(())
}

fn execute(store: &Store, lease: &Lease, transport: &mut Transport, s: &Value, r: &Value) -> Result<Value> {
    if r["action"] == "screenshot" {
        return capture_image(store, lease, transport, s, r, false);
    }
    let coordinate_input = matches!(r["action"].as_str(), Some("tap" | "swipe"));
    let mut verification = Value::Null;
    if coordinate_input {
        let checked = (|| -> Result<()> {
            check_frame(s, r)?;
            verification = capture_image(store, lease, transport, s, r, true)?;
            if verification["rotation"].is_null() {
                return Err(error("frame_orientation_unknown", "current display orientation is unknown; input was not issued"));
            }
            let source = &s["latest_image"];
            if ["rotation", "source_width", "source_height"].iter().any(|key| source[*key] != verification[*key]) {
                return Err(error("frame_geometry_changed", "display geometry changed; capture again before input"));
            }
            check_frame(s, r)
        })();
        if let Err(e) = checked {
            if matches!(e.code, "frame_unavailable" | "frame_stale" | "frame_orientation_unknown" | "coordinate_out_of_bounds" | "frame_geometry_changed") {
                return Ok(json!({"exit":2,"input_issued":false,"frame_id":r["params"]["frame_id"],
                    "code":e.code,"error":e.message,"verification":verification}));
            }
            return Err(e);
        }
    }
    let (command, seconds) = shell_command(r)?;
    let out = transport
        .shell(
            lease,
            &format!("remote-{}", string(r, "action")?),
            &command,
            seconds,
        )
        .map_err(|e| error(if e.uncertain { "unknown" } else { "io_error" }, e.message))?;
    let mut value = json!({"exit":out.code,"stdout":out.stdout.chars().take(8192).collect::<String>()});
    if matches!(r["action"].as_str(), Some("logcat" | "diagnostics")) {
        value["stdout_truncated"] = json!(out.stdout.chars().nth(8192).is_some());
    }
    if coordinate_input {
        value["input_issued"] = json!(true);
        value["frame_id"] = r["params"]["frame_id"].clone();
        value["verification"] = verification;
    }
    Ok(value)
}
fn stop_pending(store: &Store, message: &str) -> Result<()> {
    for e in store.requests()? {
        let request_id = string(&e["request"], "request_id")?;
        if store.optional(&record("result", request_id))?.is_some()
            || store.optional(&record("started", request_id))?.is_some()
        {
            continue;
        }
        let mut r = initial(&e);
        r["status"] = json!("not_executed");
        r["finished_at"] = json!(now());
        r["error"] = json!(message);
        store.write(&record("result", request_id), &r, false)?;
    }
    Ok(())
}
fn retain(store: &Store, lease: &mut Lease, s: &mut Value, reason: &str) -> Result<()> {
    let hold = lease.hold_for_recovery(&store.path);
    s["phase"] = json!("recovery_required");
    s["transport_uncertain"] = json!(true);
    s["error"] = json!(reason);
    let saved = save(store, s);
    hold.map_err(io_error)?;
    saved
}
fn close(
    store: &Store,
    lease: &mut Lease,
    s: &mut Value,
    reason: &str,
    release: Option<&Value>,
    lost: &AtomicBool,
) -> Result<()> {
    let _publisher = store.lock("publisher.lock", Duration::from_secs(2))?;
    s["phase"] = json!("closing");
    s["stop_reason"] = json!(reason);
    save(store, s)?;
    stop_pending(store, "session is closing")?;
    if lost.load(Ordering::SeqCst) || INTERRUPTED.load(Ordering::SeqCst) {
        return Err(error("unknown", "node lifeline lost during close"));
    }
    if s["transport_uncertain"] != false || !s["pending_request"].is_null() {
        return Err(error("unknown", "session has an unresolved action"));
    }
    for e in store.requests()? {
        let request_id = string(&e["request"], "request_id")?;
        if release.is_some_and(|r| r["request_id"] == request_id) {
            continue;
        }
        if store.optional(&record("started", request_id))?.is_some()
            && store.optional(&record("result", request_id))?.is_none()
        {
            return Err(error("unknown", "started action has no durable result"));
        }
    }
    let proof = json!({"schema":"phonefarm/remote-control-close@1","session_id":s["session_id"],"serial":s["serial"],"token":s["token"],
        "scope":SCOPE,"origin":"device_command_completion","at":now(),"reason":reason,
        "last_request_id":s["last_request_id"],"game_state_restored":false,"app_state_rollback":false});
    store.write("close.json", &proof, false)?;
    if let Some(r) = release {
        let mut r = r.clone();
        r["status"] = json!("completed");
        r["finished_at"] = json!(now());
        r["result"] = json!({"closing":true,"scope":SCOPE,"game_state_restored":false});
        store.write(&record("result", string(&r, "request_id")?), &r, false)?;
    }
    if lost.load(Ordering::SeqCst) || INTERRUPTED.load(Ordering::SeqCst) {
        return Err(error(
            "unknown",
            "node lifeline lost before lease completion",
        ));
    }
    lease
        .complete_recovery(
            SCOPE,
            "device_command_completion",
            &[&store.path.join("close.json")],
        )
        .map_err(io_error)?;
    s["phase"] = json!("closed");
    s["recovery_report"] = json!("close.json");
    if let Err(e) = save(store, s) {
        let _ = lease.hold_for_recovery(&store.path);
        return Err(e);
    }
    Ok(())
}
extern "C" fn interrupted(_: libc::c_int) {
    INTERRUPTED.store(true, Ordering::SeqCst);
}
enum Claim {
    Idle,
    Deadline,
    Action(Value, Value),
    Rejected(String),
}
fn claim(store: &Store, s: &mut Value, completed: &HashSet<String>, end: Instant) -> Result<Claim> {
    // Publication and claiming share this lock through durable started + state.
    // Thus release may interrupt only the one action already claimed before it.
    let _publisher = store.lock("publisher.lock", Duration::from_secs(2))?;
    let next = store
        .requests()?
        .into_iter()
        .filter(|e| !completed.contains(e["request"]["request_id"].as_str().unwrap()))
        .min_by_key(|e| e["request"]["action"] != "release");
    let Some(e) = next else {
        return Ok(Claim::Idle);
    };
    let r = &e["request"];
    let request_id = string(r, "request_id")?;
    if r["session_id"] != s["session_id"] {
        return Err(io_error("mailbox session changed"));
    }
    if store.optional(&record("started", request_id))?.is_some()
        || store.optional(&record("result", request_id))?.is_some()
    {
        return Err(error(
            "unknown",
            "action already started; refusing to replay",
        ));
    }
    let action = string(r, "action")?;
    if matches!(action, "tap" | "swipe") {
        let checked = if s["input_contract"] != INPUT_CONTRACT {
            Err(error("input_contract_unavailable", "owner lacks frame-bound input"))
        } else if s["input_frame_id"] != r["params"]["frame_id"] {
            Err(error("frame_unavailable", "frame is no longer eligible; capture again before input"))
        } else { check_frame(s, r) };
        if let Err(reason) = checked {
            let mut rejected = initial(&e);
            rejected["status"] = json!("not_executed");
            rejected["error_code"] = json!(reason.code);
            rejected["error"] = json!(reason.message);
            rejected["finished_at"] = json!(now());
            store.write(&record("result", request_id), &rejected, false)?;
            return Ok(Claim::Rejected(request_id.to_owned()));
        }
    }
    let budget = if action == "screenshot" {
        34
    } else if matches!(action, "renew" | "release") {
        0
    } else {
        shell_command(r)?.1 + if matches!(action, "tap" | "swipe") {34} else {2}
    };
    if end.saturating_duration_since(Instant::now()) < Duration::from_secs(budget) {
        return Ok(Claim::Deadline);
    }
    let mut result = initial(&e);
    result["status"] = json!("started");
    result["started_at"] = json!(now());
    store.write(&record("started", request_id), &result, false)?;
    if !matches!(action, "renew" | "release" | "logcat" | "diagnostics") {
        s["input_frame_id"] = Value::Null;
    }
    s["last_request_id"] = json!(request_id);
    if action == "release" {
        s["phase"] = json!("closing");
    } else {
        s["pending_request"] = json!(request_id);
    }
    save(store, s)?;
    Ok(Claim::Action(e, result))
}
fn supervise(
    store: &Store,
    lease: &mut Lease,
    s: &mut Value,
    transport: &mut Transport,
    lost: Arc<AtomicBool>,
    ttl: u64,
    max: u64,
) -> Result<()> {
    let end = Instant::now() + Duration::from_secs(max);
    let mut heartbeat = Instant::now();
    let mut completed = HashSet::new();
    loop {
        if lost.load(Ordering::SeqCst) || INTERRUPTED.load(Ordering::SeqCst) {
            return Err(error(
                "unknown",
                "node lifeline lost or owner interrupted; explicit recovery required",
            ));
        }
        if Instant::now() >= end || heartbeat.elapsed() >= Duration::from_secs(ttl) {
            return close(
                store,
                lease,
                s,
                if Instant::now() >= end {
                    "deadline"
                } else {
                    "heartbeat"
                },
                None,
                &lost,
            );
        }
        let (e, mut result) = match claim(store, s, &completed, end)? {
            Claim::Idle => {
                std::thread::sleep(Duration::from_millis(40));
                continue;
            }
            Claim::Deadline => return close(store, lease, s, "deadline", None, &lost),
            Claim::Rejected(request_id) => { completed.insert(request_id); continue; },
            Claim::Action(e, result) => (e, result),
        };
        let r = &e["request"];
        let request_id = string(r, "request_id")?;
        let action = string(r, "action")?;
        if action == "release" {
            return close(store, lease, s, "release", Some(&result), &lost);
        }
        let outcome = if action == "renew" {
            Ok(json!({"renewed":true}))
        } else {
            execute(store, lease, transport, s, r)
        };
        match outcome {
            Ok(value) => {
                result["status"] = json!(if value.get("exit").is_some_and(|n| n != 0) {
                    "failed"
                } else {
                    "completed"
                });
                result["result"] = value;
            }
            Err(e) => {
                // Any failure after started that is not a confirmed device exit
                // keeps the barrier, including an output/receipt publication failure.
                result["status"] = json!("unknown");
                result["error"] = json!(e.message);
                result["finished_at"] = json!(now());
                let _ = store.write(&record("result", request_id), &result, false);
                return Err(e);
            }
        }
        result["finished_at"] = json!(now());
        store.write(&record("result", request_id), &result, false)?;
        if action == "screenshot" {
            s["latest_image"] = result["result"].clone();
            s["input_frame_id"] = if result["result"]["rotation"].is_null() { Value::Null } else {r["request_id"].clone()};
        }
        s["pending_request"] = Value::Null;
        save(store, s)?;
        heartbeat = Instant::now();
        completed.insert(request_id.to_owned());
    }
}

#[derive(Default)]
struct Args {
    command: String,
    serial: Option<String>,
    binding: Option<DeviceBinding>,
    out: Option<PathBuf>,
    session: Option<PathBuf>,
    request: Option<String>,
    request_id: Option<String>,
    ttl: u64,
    max: u64,
    confirm: bool,
    participants: bool,
    note: Option<String>,
}
fn parse(args: &[String]) -> Result<Args> {
    let mut a = Args {
        command: args.first().cloned().unwrap_or_default(),
        ttl: 300,
        max: 3600,
        ..Default::default()
    };
    if !matches!(
        a.command.as_str(),
        "capabilities" | "owner" | "action" | "status" | "recover"
    ) {
        return Err(error("invalid_request", "remote-control capabilities|owner|action|status|recover; owner stdin is a node lifeline"));
    }
    let allowed: &[&str] = match a.command.as_str() {
        "owner" => &[
            "--serial",
            "--device-binding",
            "--out",
            "--ttl-seconds",
            "--max-seconds",
            "--json",
        ],
        "action" => &["--session", "--request", "--json"],
        "status" => &["--session", "--request-id", "--json"],
        "recover" => &[
            "--session",
            "--serial",
            "--confirm-control-stopped",
            "--participants-stopped",
            "--note",
            "--json",
        ],
        _ => &["--json"],
    };
    let mut seen = HashSet::new();
    let mut it = args[1..].iter();
    while let Some(flag) = it.next() {
        if !allowed.contains(&flag.as_str()) || !seen.insert(flag.clone()) {
            return Err(error(
                "invalid_request",
                format!("unsupported or repeated option {flag}"),
            ));
        }
        match flag.as_str() {
            "--json" => {}
            "--confirm-control-stopped" => a.confirm = true,
            "--participants-stopped" => a.participants = true,
            _ => {
                let v = it
                    .next()
                    .ok_or_else(|| error("invalid_request", format!("missing value for {flag}")))?;
                match flag.as_str() {
                    "--serial" => a.serial = Some(v.clone()),
                    "--device-binding" => a.binding = Some(crate::device_registry::parse_binding(v).map_err(|e| error("invalid_request", e))?),
                    "--out" => a.out = Some(v.into()),
                    "--session" => a.session = Some(v.into()),
                    "--request" => a.request = Some(v.clone()),
                    "--request-id" => a.request_id = Some(v.clone()),
                    "--note" => a.note = Some(v.clone()),
                    "--ttl-seconds" => {
                        a.ttl = v
                            .parse()
                            .map_err(|_| error("invalid_request", "invalid TTL"))?
                    }
                    "--max-seconds" => {
                        a.max = v
                            .parse()
                            .map_err(|_| error("invalid_request", "invalid max seconds"))?
                    }
                    _ => unreachable!(),
                }
            }
        }
    }
    match a.command.as_str() {
        "owner" if a.out.is_none() || !a.serial.as_deref().is_some_and(serial_ok) || !(1..=14400).contains(&a.ttl) || !(1..=14400).contains(&a.max) => return Err(error("invalid_request", "owner needs Android --serial, absolute --out and 1..14400 second bounds")),
        "action" if a.session.is_none() || a.request.as_deref() != Some("-") => return Err(error("invalid_request", "action needs --session DIR --request -")),
        "status" if a.session.is_none() => return Err(error("invalid_request", "status needs --session DIR")),
        "recover" if a.session.is_none() || !a.serial.as_deref().is_some_and(serial_ok) || !a.confirm || !a.note.as_ref().is_some_and(|s| !s.trim().is_empty() && s.len() <= 4096) => return Err(error("invalid_request", "recover requires --session DIR --serial S --confirm-control-stopped --note TEXT after independent verification; unbound launch also needs --participants-stopped")),
        _ => {},
    }
    if a.binding.as_ref().is_some_and(|b| Some(b.serial.as_str()) != a.serial.as_deref()) {
        return Err(error("invalid_request", "device binding serial differs from owner target"));
    }
    Ok(a)
}
fn start(a: &Args) -> Result<()> {
    let out = a.out.as_ref().unwrap();
    if !out.is_absolute() {
        return Err(error("invalid_request", "out must be absolute"));
    }
    std::fs::create_dir_all(out)?;
    let store = Store::open(out)?;
    // Session directories are never reused, including an interrupted preparation.
    let owner = store.file("owner.lock", libc::O_CREAT | libc::O_EXCL | libc::O_RDWR)?;
    owner.lock().map_err(|e| io_error(e.to_string()))?;
    store
        .file(
            "publisher.lock",
            libc::O_CREAT | libc::O_EXCL | libc::O_RDWR,
        )?
        .sync_all()?;
    store.root.sync_all()?;
    let serial = a.serial.as_deref().unwrap();
    let identity = host_process::capture(std::process::id()).map_err(io_error)?;
    let mut random = [0u8; 16];
    File::open("/dev/urandom")?.read_exact(&mut random)?;
    let session_id = random
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();
    // Bind the acquisition to a host instance before it can leave a lease.
    // This proves identity only; it never certifies that device I/O did not occur.
    let mut launch = json!({"schema":LAUNCH,"launch_id":session_id,
        "serial":serial,"context":store.path,"created_at":now(),
        "owner":{"pid":identity.pid,"start":identity.start,"host_boot_id":identity.host_boot_id}});
    attach_scope(&mut launch, a.binding.as_ref());
    store.write("launch.json", &launch, false)?;
    // The existing mailbox is available while waiting for the fd, so release
    // can cancel an acquisition without ever touching the device.
    let mut s = json!({"schema":PRIVATE,"session_id":session_id,"serial":serial,"token":null,"context":store.path,
        "owner":{"pid":identity.pid,"start":identity.start,"host_boot_id":identity.host_boot_id},"phase":"starting",
        "created_at":now(),"deadline":null,"ttl_seconds":a.ttl,"max_seconds":a.max,
        "transport_uncertain":false,"pending_request":null,"last_request_id":null,"latest_image":null,
        "input_contract":INPUT_CONTRACT,"action_capabilities":ACTIONS,"input_frame_id":null,"stop_reason":null,"error":null,"recovery_report":null});
    attach_scope(&mut s, a.binding.as_ref());
    store.write(STATE, &s, false)?;
    let storage = crate::lease_storage::resolve();
    let timeout = crate::devlease::wait_timeout().map_err(io_error)?;
    let mut release = None;
    let acquisition = (|| {
        let (root, farm) = storage.roots()?;
        let scope = match &a.binding {
            Some(binding) => crate::fd_lock::Scope::device(serial, Some(binding.clone())),
            None => crate::devlease::resolve_scope(&storage, serial)?,
        };
        let guard = crate::fd_lock::Guard::acquire_interruptible(
            &root, &farm, scope, "remote-control", timeout, &mut || {
                release = store.requests().map_err(|e| e.message)?.into_iter()
                    .find(|e| e["request"]["action"] == "release");
                if release.is_some() { Err("remote release requested".into()) } else { Ok(()) }
            },
        )?;
        Lease::from_guard(guard, serial, "remote-control")
    })();
    if let Some(e) = release {
        // Returning from acquisition has already dropped any partially held fd.
        // No transport was created; the original holder is unaffected.
        let mut result = initial(&e);
        result["status"] = json!("completed");
        result["finished_at"] = json!(now());
        result["result"] = json!({"closing":true,"scope":SCOPE,"game_state_restored":false});
        s["phase"] = json!("closed");
        s["stop_reason"] = json!("release");
        s["last_request_id"] = e["request"]["request_id"].clone();
        save(&store, &mut s)?;
        store.write(&record("result", string(&e["request"], "request_id")?), &result, false)?;
        return Ok(());
    }
    let mut lease = match acquisition {
        Ok(lease) => lease,
        Err(reason) => {
            // Only this exact pre-device-I/O failure may certify not_started.
            // A missing state file or any post-acquisition failure cannot.
            let mut proof = json!({"schema":"phonefarm/remote-control-startup@1","state":"not_started",
                "not_started":true,"serial":serial,"context":store.path,
                "owner":{"pid":identity.pid,"start":identity.start,"host_boot_id":identity.host_boot_id},
                "reason":reason,"device_io_started":false,"at":now()});
            attach_scope(&mut proof, a.binding.as_ref());
            store.write("startup.json", &proof, false)?;
            return Err(Error {
                code: "not_started",
                message: reason,
                response: Some(startup_status(&store)?),
            });
        }
    };
    s["token"] = json!(lease.token());
    s["deadline"] = json!((chrono::Utc::now() + chrono::Duration::seconds(a.max as i64)).to_rfc3339());
    save(&store, &mut s)?;
    lease.hold_for_recovery(&store.path).map_err(io_error)?;
    let result = (|| -> Result<()> {
        let mut transport =
            Transport::guarded(serial, lease.token(), &store.path).map_err(io_error)?;
        let lost = Arc::new(AtomicBool::new(false));
        let watched = Arc::clone(&lost);
        std::thread::Builder::new()
            .name("remote-node-lifeline".into())
            .spawn(move || {
                let mut byte = [0u8];
                let _ = std::io::stdin().read(&mut byte);
                watched.store(true, Ordering::SeqCst);
            })?;
        INTERRUPTED.store(false, Ordering::SeqCst);
        unsafe {
            libc::signal(libc::SIGINT, interrupted as *const () as usize);
            libc::signal(libc::SIGTERM, interrupted as *const () as usize);
        }
        s["phase"] = json!("ready");
        save(&store, &mut s)?;
        println!("{}", public(&store, &s)?);
        std::io::stdout().flush()?;
        supervise(
            &store,
            &mut lease,
            &mut s,
            &mut transport,
            lost,
            a.ttl,
            a.max,
        )
    })();
    if let Err(e) = result {
        let retained = retain(&store, &mut lease, &mut s, &e.message);
        let _ = stop_pending(&store, "owner requires recovery");
        if let Err(saved) = retained {
            return Err(io_error(format!(
                "{}; retention publication: {}",
                e.message, saved.message
            )));
        }
        return Err(e);
    }
    Ok(())
}
fn recover(a: &Args) -> Result<Value> {
    let store = Store::open(a.session.as_ref().unwrap())?;
    let _owner = store.lock("owner.lock", Duration::ZERO)?;
    let _publisher = store.lock("publisher.lock", Duration::from_secs(2))?;
    if store.optional(STATE)?.is_none() || store.optional("startup-retired.json")?.is_some() {
        return retire_startup(
            &store,
            a.serial.as_deref().unwrap(),
            a.note.as_deref().unwrap(),
            || crate::devlease::storage_status(&crate::lease_storage::resolve()),
        );
    }
    let mut s = state(&store)?;
    if s["serial"] != a.serial.as_deref().unwrap() {
        return Err(error("conflict", "recovery serial mismatch"));
    }
    if host_process::inspect(&owner_identity(&s)?).map_err(io_error)?
        == host_process::InstanceState::Alive
    {
        return Err(error("conflict", "original owner is still alive"));
    }
    if s["phase"] == "closed" {
        drop(_owner);
        return public(&store, &s);
    }
    if s["phase"] == "starting" {
        return retire_startup(
            &store,
            a.serial.as_deref().unwrap(),
            a.note.as_deref().unwrap(),
            || crate::devlease::storage_status(&crate::lease_storage::resolve()),
        );
    }
    let storage = crate::lease_storage::resolve();
    let binding = saved_binding(&s)?;
    let stamp = chrono::Utc::now()
        .timestamp_nanos_opt()
        .ok_or_else(|| io_error("time unavailable"))?;
    let evidence_name = format!("recovery-{stamp}.json");
    let evidence = store.path.join(&evidence_name);
    let mut lease = if let Some(binding) = &binding {
        Lease::acquire_device_recovery(&storage, binding, &store.path, crate::devlease::wait_timeout().map_err(io_error)?)
    } else {
        let (root, legacy) = storage.roots().map_err(io_error)?;
        Lease::acquire_recovery(&root, &legacy, a.serial.as_deref().unwrap(), &store.path, crate::devlease::wait_timeout().map_err(io_error)?)
    }.map_err(|e| error("recovery_required", e))?;
    let proof = json!({"schema":"phonefarm/remote-control-recovery@1", "session_id":s["session_id"],
        "serial":s["serial"],"context":store.path,"scope":"remote_control_commands_settled",
        "origin":"operator_assertion","device_verified":false,"game_state_restored":false,
        "app_state_rollback":false,"note":a.note,"participants_stopped":a.participants,
        "pending_request":s["pending_request"],"device_binding":binding,"at":chrono::Utc::now().to_rfc3339()});
    crate::devlease::durable_replace(&evidence, proof.to_string().as_bytes()).map_err(io_error)?;
    stop_pending(&store, "owner stopped before executing this request")?;
    lease
        .complete_recovery(SCOPE, "operator_assertion", &[&evidence])
        .map_err(io_error)?;
    s["phase"] = json!("closed");
    s["stop_reason"] = json!("operator_reconciled");
    s["recovery_report"] = json!(evidence_name);
    // Preserve original pending/uncertain facts and results; recovery is not success/replay.
    if let Err(e) = save(&store, &mut s) {
        let _ = lease.hold_for_recovery(&store.path);
        return Err(e);
    }
    scoped_result(
        json!({"schema":SCHEMA,"session_id":s["session_id"],"serial":s["serial"],"phase":"closed","live_owner":false,
        "recovery":{"required":false,"scope":SCOPE,"origin":"operator_assertion","device_verified":false,
        "game_state_restored":false,"report":evidence_name}}),
        &s,
    )
}
pub(crate) fn run(args: &[String]) -> i32 {
    let result = (|| -> Result<Option<Value>> {
        let a = parse(args)?;
        if a.command == "capabilities" {
            return Ok(Some(
                json!({"schema":"phonefarm/remote-control-capabilities@1","ownership":"strong-lease-v1","device_binding_v1":true,"actions":ACTIONS,"platforms":["android"],"scope":SCOPE,
                    "input_contract":INPUT_CONTRACT,"frame_max_age_seconds":FRAME_AGE_SECONDS}),
            ));
        }
        if a.command == "owner" {
            start(&a)?;
            return Ok(None);
        }
        if a.command == "recover" {
            return recover(&a).map(Some);
        }
        let store = Store::open(a.session.as_ref().unwrap())?;
        if a.command == "status" {
            return match &a.request_id {
                Some(request_id) => receipt(&store, &state(&store)?, request_id).map(Some),
                None => session_status(&store).map(Some),
            };
        }
        let mut bytes = Vec::new();
        std::io::stdin()
            .take((LIMIT + 1) as u64)
            .read_to_end(&mut bytes)?;
        if bytes.len() > LIMIT {
            return Err(error("invalid_request", "request exceeds 64 KiB"));
        }
        let request =
            serde_json::from_slice(&bytes).map_err(|e| error("invalid_request", e.to_string()))?;
        publish(&store, &request).map(Some)
    })();
    match result {
        Ok(Some(v)) => {
            println!("{v}");
            0
        }
        Ok(None) => 0,
        Err(e) => {
            println!(
                "{}",
                e.response.unwrap_or_else(|| json!({"schema":"phonefarm/remote-control-error@1","code":e.code,"error":e.message}))
            );
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Scratch(PathBuf);
    impl Scratch {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "pf-remote-unit-{}-{}",
                std::process::id(),
                TEMP.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self(path.canonicalize().unwrap())
        }
        fn fixture(&self) -> (Store, File, Value) {
            let store = Store::open(&self.0).unwrap();
            let owner = store
                .file("owner.lock", libc::O_CREAT | libc::O_EXCL | libc::O_RDWR)
                .unwrap();
            owner.lock().unwrap();
            store
                .file(
                    "publisher.lock",
                    libc::O_CREAT | libc::O_EXCL | libc::O_RDWR,
                )
                .unwrap();
            let process = host_process::capture(std::process::id()).unwrap();
            let state = json!({"schema":PRIVATE,"session_id":"test-session","serial":"fake-remote","context":store.path,"token":"original-token",
                "owner":{"pid":process.pid,"start":process.start,"host_boot_id":process.host_boot_id},"phase":"ready",
                "transport_uncertain":false,"pending_request":null,"input_contract":INPUT_CONTRACT,"input_frame_id":null});
            store.write(STATE, &state, false).unwrap();
            (store, owner, state)
        }
    }
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn request(request_id: &str, action: &str, params: Value) -> Value {
        json!({"schema":ACTION,"session_id":"test-session","request_id":request_id,"action":action,"params":params})
    }
    #[test]
    fn typed_boundary_rejects_raw_commands_and_unbounded_input() {
        if crate::isolate_fd_test("remote_control::tests::typed_boundary_rejects_raw_commands_and_unbounded_input") { return; }
        for action in ["logcat", "diagnostics"] {
            assert!(ACTIONS.contains(&action));
            let scratch = Scratch::new();
            let (store, _owner, mut legacy) = scratch.fixture();
            assert!(!owner_actions(&legacy).contains(&action));
            let original = request("read-owner", action, json!({}));
            assert_eq!(publish(&store, &original).unwrap_err().code, "unsupported");
            legacy["action_capabilities"] = json!(ACTIONS);
            save(&store, &mut legacy).unwrap();
            assert_eq!(publish(&store, &original).unwrap()["status"], "accepted");
            legacy.as_object_mut().unwrap().remove("action_capabilities");
            save(&store, &mut legacy).unwrap();
            assert_eq!(publish(&store, &original).unwrap()["status"], "accepted");
            legacy["action_capabilities"] = json!(ACTIONS);
            legacy["input_frame_id"] = json!("kept-frame");
            assert!(matches!(claim(&store, &mut legacy, &HashSet::new(),
                Instant::now() + Duration::from_secs(60)).unwrap(), Claim::Action(_, _)));
            assert_eq!(legacy["input_frame_id"], "kept-frame");
        }
        let diagnostics = request("inspect", "diagnostics", json!({}));
        validate_request(&diagnostics).unwrap();
        let (command, budget) = shell_command(&diagnostics).unwrap();
        assert_eq!(budget, 20);
        assert!(command.contains("getprop") && command.contains("id 2>&1")
            && command.contains("wm size; wm density") && command.contains("dumpsys activity activities"));
        assert!(!command.contains("pm path") && !command.contains("su -c"));
        let package = request("inspect-package", "diagnostics", json!({"package":"com.example.game"}));
        validate_request(&package).unwrap();
        assert!(shell_command(&package).unwrap().0.contains("pm path --user 0 'com.example.game'"));
        for params in [json!({"package":"a; id"}), json!({"package":""}),
            json!({"package":null}), json!({"shell":"id"})] {
            assert!(validate_request(&request("bad-diagnostics", "diagnostics", params)).is_err());
        }
        let logcat = request("logs", "logcat", json!({}));
        validate_request(&logcat).unwrap();
        assert_eq!(shell_command(&logcat).unwrap(), ("logcat -d -t 200 -v threadtime".into(), 20));
        let logcat = request("logs-pid", "logcat", json!({"lines":2000,"pid":1234}));
        validate_request(&logcat).unwrap();
        assert_eq!(shell_command(&logcat).unwrap(), ("logcat -d -t 2000 -v threadtime --pid=1234".into(), 20));
        for params in [json!({"lines":0}), json!({"lines":2001}), json!({"pid":0}),
            json!({"pid":"123; id"}), json!({"clear":true})] {
            assert!(validate_request(&request("bad-logcat", "logcat", params)).is_err());
        }
        assert_eq!(
            validate_request(&request("a", "shell", json!({"cmd":"id"})))
                .unwrap_err()
                .code,
            "unsupported"
        );
        assert!(validate_request(&request(
            "a",
            "swipe",
            json!({"x1":0,"y1":0,"x2":2,"y2":3,"ms":10001})
        ))
        .is_err());
        assert!(validate_request(&request("a", "start_app", json!({"package":"a; id"}))).is_err());
        assert!(validate_request(&request("a", "text", json!({"text":"hello\nworld"}))).is_err());
        let text = request("a", "text", json!({"text":"中文'; echo x"}));
        validate_request(&text).unwrap();
        assert_eq!(
            shell_command(&text).unwrap().0,
            "input text '中文'\\'';%secho%sx'"
        );
        assert!(validate_request(&request("a", "tap", json!({"x":1,"y":0}))).is_err());
        validate_request(&request("a", "tap", json!({"x":100000,"y":0,"frame_id":"shot"}))).unwrap();
    }
    #[test]
    fn request_identity_is_immutable_and_release_freezes_publication() {
        if crate::isolate_fd_test("remote_control::tests::request_identity_is_immutable_and_release_freezes_publication") { return; }
        let scratch = Scratch::new();
        let (store, _owner, mut s) = scratch.fixture();
        let tap = request("a", "tap", json!({"x":10,"y":20,"frame_id":"shot"}));
        assert_eq!(publish(&store, &tap).unwrap()["status"], "accepted");
        assert_eq!(publish(&store, &tap).unwrap()["sequence"], 1);
        assert_eq!(
            publish(&store, &request("a", "tap", json!({"x":11,"y":20,"frame_id":"shot"})))
                .unwrap_err()
                .code,
            "conflict"
        );
        publish(&store, &request("release", "release", json!({}))).unwrap();
        assert_eq!(public(&store, &s).unwrap()["phase"], "closing");
        assert_eq!(public(&store, &s).unwrap()["live_owner"], true);
        assert_eq!(
            publish(&store, &request("late", "renew", json!({})))
                .unwrap_err()
                .code,
            "session_closed"
        );
        assert_eq!(store.requests().unwrap().len(), 2);
        assert_eq!(publish(&store, &tap).unwrap()["sequence"], 1);
        let claimed = claim(
            &store,
            &mut s,
            &HashSet::new(),
            Instant::now() + Duration::from_secs(60),
        )
        .unwrap();
        let Claim::Action(e, _) = claimed else {
            panic!("release must be claimed");
        };
        assert_eq!(e["request"]["request_id"], "release");
        assert_eq!(state(&store).unwrap()["phase"], "closing");
        assert!(store.optional(&record("started", "a")).unwrap().is_none());
        stop_pending(&store, "session is closing").unwrap();
        assert_eq!(receipt(&store, &s, "a").unwrap()["status"], "not_executed");
    }
    #[test]
    fn frame_claim_rejects_stale_and_consumes_only_current_eligible_image() {
        if crate::isolate_fd_test("remote_control::tests::frame_claim_rejects_stale_and_consumes_only_current_eligible_image") { return; }
        let scratch = Scratch::new();
        let (store, _owner, mut s) = scratch.fixture();
        let image = json!({"frame_id":"shot","session_id":"test-session","serial":"fake-remote",
            "captured_at":now(),"rotation":1,"source_width":256,"source_height":128,"width":128,"height":64});
        let tap = request("tap", "tap", json!({"x":255,"y":127,"frame_id":"shot"}));
        s["latest_image"] = image.clone();
        s["input_frame_id"] = json!("shot");
        check_frame(&s, &tap).unwrap();
        let outside = request("outside", "tap", json!({"x":256,"y":127,"frame_id":"shot"}));
        assert_eq!(check_frame(&s, &outside).unwrap_err().code, "coordinate_out_of_bounds");
        for (field, value, code) in [
            ("rotation", Value::Null, "frame_orientation_unknown"),
            ("serial", json!("other-device"), "frame_unavailable"),
            ("captured_at", json!((chrono::Utc::now()+chrono::Duration::seconds(10)).to_rfc3339()), "frame_stale"),
        ] {
            s["latest_image"][field] = value;
            assert_eq!(check_frame(&s, &tap).unwrap_err().code, code);
            s["latest_image"] = image.clone();
        }
        s["latest_image"]["captured_at"] = json!((chrono::Utc::now()-chrono::Duration::seconds(31)).to_rfc3339());
        save(&store, &mut s).unwrap();
        publish(&store, &tap).unwrap();
        let end = Instant::now()+Duration::from_secs(100);
        assert!(matches!(claim(&store, &mut s, &HashSet::new(), end).unwrap(), Claim::Rejected(id) if id == "tap"));
        let rejected = receipt(&store, &s, "tap").unwrap();
        assert_eq!(rejected["status"], "not_executed");
        assert_eq!(rejected["error_code"], "frame_stale");
        assert!(store.optional(&record("started", "tap")).unwrap().is_none());
        s["latest_image"] = image;
        save(&store, &mut s).unwrap();
        let next = request("next", "tap", tap["params"].clone());
        publish(&store, &next).unwrap();
        assert!(matches!(claim(&store, &mut s, &HashSet::from(["tap".into()]), end).unwrap(), Claim::Action(..)));
        assert!(state(&store).unwrap()["input_frame_id"].is_null());
        assert_eq!(publish(&store, &next).unwrap()["status"], "started");
        assert_eq!(publish(&store, &tap).unwrap(), rejected);
        s.as_object_mut().unwrap().remove("input_contract");
        save(&store, &mut s).unwrap();
        assert!(public(&store, &s).unwrap()["input_contract"].is_null());
        assert_eq!(publish(&store, &request("old", "tap", tap["params"].clone())).unwrap_err().code, "input_contract_unavailable");
    }
    #[test]
    fn startup_retirement_requires_dead_owner_and_clear_domain_preserves_unknown() {
        if crate::isolate_fd_test("remote_control::tests::startup_retirement_requires_dead_owner_and_clear_domain_preserves_unknown") { return; }
        for (with_state, scoped) in [(false, false), (true, false), (true, true)] {
            let scratch = Scratch::new();
            let (store, _owner, mut s) = scratch.fixture();
            let _publisher = store.lock("publisher.lock", Duration::ZERO).unwrap();
            s["phase"] = json!("starting");
            let binding = crate::device_registry::parse_binding(&json!({
                "schema":"phonefarm/device-binding@1","domain_id":"1".repeat(32),
                "registry_revision":1,"device_id":"A","serial":"fake-remote"
            }).to_string()).unwrap();
            if scoped {
                attach_scope(&mut s, Some(&binding));
                let args = vec!["owner".into(), "--serial".into(), "fake-remote".into(),
                    "--out".into(), store.path.to_string_lossy().into_owned(),
                    "--device-binding".into(), serde_json::to_string(&binding).unwrap()];
                assert_eq!(parse(&args).unwrap().binding, Some(binding.clone()));
                let mut recover = vec!["recover".into(), "--session".into(), store.path.to_string_lossy().into_owned(),
                    "--serial".into(), "fake-remote".into(), "--confirm-control-stopped".into(), "--note".into(), "verified".into()];
                parse(&recover).unwrap();
                recover.extend(["--device-binding".into(), serde_json::to_string(&binding).unwrap()]);
                assert!(parse(&recover).is_err()); // recovery cannot replace the recorded binding
            }
            if with_state {
                save(&store, &mut s).unwrap();
            } else {
                fs::remove_file(store.path.join(STATE)).unwrap();
            }
            let mut clear = json!({"status":"clear","bridge_held":false,"entries":[],"error":null,"storage":{"schema_version":1,"error":null}});
            if scoped {
                clear["status"] = json!("held");
                clear["bridge_held"] = json!(true);
                clear["entries"] = json!([{"scope":"device","device_id":"B","state":"active"}]);
                clear["device_scopes"] = json!({"schema":"phonefarm/device-scopes@1",
                    "registry_revision":2,"unknown":false,
                    "queue_waiting":true,"global":{"status":"clear","error":null},
                    "devices":[{"device_id":"A","status":"clear","error":null},
                        {"device_id":"B","status":"held","error":null}]});
            }
            assert!(
                retire_startup(&store, "fake-remote", "independently verified idle", || {
                    clear.clone()
                })
                .unwrap_err()
                .message
                .contains("launch identity is missing")
            );
            let mut launch = json!({"schema":LAUNCH,"launch_id":s["session_id"],"serial":s["serial"],"context":store.path,"owner":s["owner"]});
            if scoped { attach_scope(&mut launch, Some(&binding)); }
            store.write("launch.json", &launch, false).unwrap();
            assert_eq!(
                retire_startup(&store, "fake-remote", "independently verified idle", || {
                    clear.clone()
                })
                .unwrap_err()
                .code,
                "conflict"
            );
            // Simulate a departed original host instance, with both records bound
            // to that same identity. No device or real lease domain is accessed.
            launch["owner"]["start"] = json!("retired-instance");
            s["owner"] = launch["owner"].clone();
            store.write("launch.json", &launch, true).unwrap();
            if with_state {
                save(&store, &mut s).unwrap();
            }
            let mut busy = clear.clone();
            busy["status"] = json!("held");
            busy["bridge_held"] = json!(true);
            if scoped { busy["device_scopes"]["devices"][0]["status"] = json!("held"); }
            assert_eq!(
                retire_startup(&store, "fake-remote", "independently verified idle", || {
                    busy
                })
                .unwrap_err()
                .code,
                "recovery_required"
            );
            assert!(store.optional("startup-retired.json").unwrap().is_none());
            let retired =
                retire_startup(&store, "fake-remote", "independently verified idle", || {
                    clear
                })
                .unwrap();
            assert_eq!(retired["phase"], "closed");
            if scoped {
                assert_eq!(retired["device_binding"], json!(binding));
                assert_eq!(retired["lease_scope"], "device");
            } else {
                assert!(retired.get("device_binding").is_none());
            }
            assert_eq!(retired["original_outcome"], "unknown");
            assert_eq!(retired["recovery"]["device_verified"], false);
            let proof = store.read("startup-retired.json").unwrap();
            assert_eq!(proof["original_launch"], launch);
            assert_eq!(proof["lease_modified"], false);
            let status = session_status(&store).unwrap();
            assert_eq!(status["phase"], "closed");
            assert_eq!(status["original_outcome"], "unknown");
            assert!(status.get("not_started").is_none());
        }
    }
    #[test]
    fn missing_owner_preserves_started_unknown_and_completed_result() {
        if crate::isolate_fd_test("remote_control::tests::missing_owner_preserves_started_unknown_and_completed_result") { return; }
        let scratch = Scratch::new();
        let (store, owner, mut s) = scratch.fixture();
        for name in ["started", "queued", "completed"] {
            publish(&store, &request(name, "renew", json!({}))).unwrap();
        }
        for name in ["started", "completed"] {
            let e = store.read(&record("request", name)).unwrap();
            let mut r = initial(&e);
            r["status"] = json!("started");
            store.write(&record("started", name), &r, false).unwrap();
            if name == "completed" {
                r["status"] = json!("completed");
                store.write(&record("result", name), &r, false).unwrap();
            }
        }
        // Same PID with a different recorded start is a departed original instance.
        s["owner"]["start"] = json!("retired-instance");
        save(&store, &mut s).unwrap();
        drop(owner);
        assert_eq!(receipt(&store, &s, "started").unwrap()["status"], "unknown");
        assert_eq!(
            receipt(&store, &s, "queued").unwrap()["status"],
            "not_executed"
        );
        assert_eq!(
            receipt(&store, &s, "completed").unwrap()["status"],
            "completed"
        );
        assert_eq!(
            publish(&store, &request("started", "renew", json!({}))).unwrap()["status"],
            "unknown"
        );
        assert_eq!(
            publish(&store, &request("new", "renew", json!({})))
                .unwrap_err()
                .code,
            "owner_unavailable"
        );
    }
    #[test]
    fn unknown_close_preserves_recovery_fact_but_does_not_persist_a_lock() {
        if crate::isolate_fd_test("remote_control::tests::unknown_close_preserves_recovery_fact_but_does_not_persist_a_lock") { return; }
        let scratch = Scratch::new();
        let (store, _owner, mut s) = scratch.fixture();
        let root = scratch.0.join("leases");
        let legacy = scratch.0.join("locks");
        let mut lease =
            Lease::acquire(&root, &legacy, "fake-remote", "remote-test", Duration::ZERO).unwrap();
        lease.hold_for_recovery(&store.path).unwrap();
        s["transport_uncertain"] = json!(true);
        assert!(close(
            &store,
            &mut lease,
            &mut s,
            "release",
            None,
            &AtomicBool::new(false)
        )
        .is_err());
        drop(lease);
        assert_eq!(s["transport_uncertain"], true);
        drop(Lease::acquire(&root, &legacy, "fake-remote", "later", Duration::ZERO).unwrap());
    }
}
