//! One live gray-layer owner; host-only clients publish bounded, immutable actions.
use crate::gray_eval_session::Parent;
use crate::gray_session::Session;
use crate::graylayer::GrayCliAdmission;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::ffi::CString;
use std::fs::{self, File, OpenOptions};
use std::io::{BufReader, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, Instant};

const DESCRIPTOR: &str = "session-control.json";
const CONTROL: &str = "gray-control";
const SCHEMA: &str = "phonefarm/gray-control@1";
const ACTION: &str = "phonefarm/gray-control-action@1";
const RECEIPT: &str = "phonefarm/gray-control-action-result@1";
const LIMIT: usize = 64 * 1024;
const MAX_REQUESTS: usize = 1024;
const FRAME_AGE_SECONDS: i64 = 30;
const ACTIONS: &[&str] = &[
    "capture",
    "sample",
    "quality",
    "tap",
    "swipe",
    "marker",
    "audit",
    "trigger",
    "narrow",
    "take_control",
    "release_control",
    "stop",
];
static STOP: AtomicBool = AtomicBool::new(false);
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug)]
struct Error {
    code: &'static str,
    message: String,
}
type Result<T> = std::result::Result<T, Error>;
fn err(code: &'static str, message: impl Into<String>) -> Error {
    Error {
        code,
        message: message.into(),
    }
}
impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        err(
            if e.kind() == std::io::ErrorKind::NotFound {
                "not_found"
            } else {
                "io_error"
            },
            e.to_string(),
        )
    }
}
fn device(e: String) -> Error {
    err("device_error", e)
}
fn now() -> String {
    chrono::Utc::now().to_rfc3339()
}
fn safe_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
}
fn string<'a>(v: &'a Value, key: &str) -> Result<&'a str> {
    v[key]
        .as_str()
        .ok_or_else(|| err("invalid_request", format!("{key} must be a string")))
}
fn number(v: &Value, key: &str) -> Result<u64> {
    v[key].as_u64().ok_or_else(|| {
        err(
            "invalid_request",
            format!("{key} must be an unsigned integer"),
        )
    })
}

/// Pin the out directory. Every child access walks directory descriptors with
/// O_NOFOLLOW, including the final file; no check-then-follow mailbox paths.
struct Store {
    root: File,
    path: PathBuf,
}
impl Store {
    fn open(path: &Path) -> Result<Self> {
        if fs::symlink_metadata(path)?.file_type().is_symlink() {
            return Err(err("invalid_request", "out must not be a symlink"));
        }
        let path = path.canonicalize()?;
        let root = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(&path)?;
        Ok(Self { root, path })
    }
    fn parent(&self, rel: &str) -> Result<(File, CString)> {
        let mut parts = Vec::new();
        for part in Path::new(rel).components() {
            match part {
                Component::Normal(s) => parts.push(
                    CString::new(s.as_encoded_bytes())
                        .map_err(|_| err("invalid_request", "NUL path"))?,
                ),
                _ => {
                    return Err(err(
                        "invalid_request",
                        "path must be relative and contain no traversal",
                    ))
                }
            }
        }
        let name = parts
            .pop()
            .ok_or_else(|| err("invalid_request", "empty path"))?;
        let mut dir = self.root.try_clone()?;
        for part in parts {
            let fd = unsafe {
                libc::openat(
                    dir.as_raw_fd(),
                    part.as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                )
            };
            if fd < 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            dir = unsafe { File::from_raw_fd(fd) };
        }
        Ok((dir, name))
    }
    fn open_file(&self, rel: &str, flags: i32) -> Result<File> {
        let (dir, name) = self.parent(rel)?;
        let fd = unsafe {
            libc::openat(
                dir.as_raw_fd(),
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
            return Err(err(
                "invalid_request",
                "control record is not a regular file",
            ));
        }
        Ok(file)
    }
    fn read(&self, rel: &str) -> Result<Value> {
        self.read_limit(rel, LIMIT)
    }
    fn read_limit(&self, rel: &str, limit: usize) -> Result<Value> {
        let file = self.open_file(rel, libc::O_RDONLY)?;
        if file.metadata()?.len() > limit as u64 {
            return Err(err("invalid_request", "control record exceeds read limit"));
        }
        let mut bytes = Vec::new();
        file.take((limit + 1) as u64).read_to_end(&mut bytes)?;
        if bytes.len() > limit {
            return Err(err(
                "invalid_request",
                "control record grew beyond read limit",
            ));
        }
        serde_json::from_slice(&bytes)
            .map_err(|e| err("invalid_request", format!("invalid control JSON: {e}")))
    }
    fn optional(&self, rel: &str) -> Result<Option<Value>> {
        match self.read(rel) {
            Ok(v) => Ok(Some(v)),
            Err(e) if e.code == "not_found" => Ok(None),
            Err(e) => Err(e),
        }
    }
    fn write(&self, rel: &str, value: &Value, replace: bool) -> Result<()> {
        let bytes = serde_json::to_vec_pretty(value).map_err(|e| err("io_error", e.to_string()))?;
        self.write_bytes(rel, &bytes, replace)
    }
    fn write_bytes(&self, rel: &str, bytes: &[u8], replace: bool) -> Result<()> {
        let (dir, name) = self.parent(rel)?;
        let tmp = CString::new(format!(
            ".control-{}-{}",
            std::process::id(),
            TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ))
        .unwrap();
        let fd = unsafe {
            libc::openat(
                dir.as_raw_fd(),
                tmp.as_ptr(),
                libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                0o600,
            )
        };
        if fd < 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        let mut file = unsafe { File::from_raw_fd(fd) };
        let saved = (|| -> Result<()> {
            file.write_all(bytes)?;
            file.sync_all()?;
            let code = unsafe {
                if replace {
                    libc::renameat(
                        dir.as_raw_fd(),
                        tmp.as_ptr(),
                        dir.as_raw_fd(),
                        name.as_ptr(),
                    )
                } else {
                    libc::linkat(
                        dir.as_raw_fd(),
                        tmp.as_ptr(),
                        dir.as_raw_fd(),
                        name.as_ptr(),
                        0,
                    )
                }
            };
            if code != 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            dir.sync_all()?;
            Ok(())
        })();
        unsafe {
            libc::unlinkat(dir.as_raw_fd(), tmp.as_ptr(), 0);
        }
        saved
    }
    fn mkdir(&self, rel: &str) -> Result<()> {
        let (dir, name) = self.parent(rel)?;
        if unsafe { libc::mkdirat(dir.as_raw_fd(), name.as_ptr(), 0o700) } != 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        dir.sync_all()?;
        Ok(())
    }
    fn lock(&self, name: &str, wait: Duration) -> Result<File> {
        let file = self.open_file(&format!("{CONTROL}/{name}.lock"), libc::O_RDWR)?;
        let until = Instant::now() + wait;
        loop {
            match file.try_lock() {
                Ok(()) => return Ok(file),
                Err(std::fs::TryLockError::WouldBlock) if Instant::now() < until => {
                    std::thread::sleep(Duration::from_millis(10))
                }
                Err(std::fs::TryLockError::WouldBlock) => {
                    return Err(err("conflict", format!("{name} is busy")))
                }
                Err(e) => return Err(err("io_error", e.to_string())),
            }
        }
    }
    fn live(&self) -> Result<bool> {
        match self.lock("owner", Duration::ZERO) {
            Ok(_) => Ok(false),
            Err(e) if e.code == "conflict" => Ok(true),
            Err(e) => Err(e),
        }
    }
    fn requests(&self) -> Result<Vec<Value>> {
        // Directory entries are obtained from the pinned descriptor, then each
        // request is independently opened under it without following symlinks.
        let (dir, name) = self.parent(&format!("{CONTROL}/requests/_"))?;
        drop(name);
        let dup = unsafe { libc::dup(dir.as_raw_fd()) };
        if dup < 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        let stream = unsafe { libc::fdopendir(dup) };
        if stream.is_null() {
            unsafe {
                libc::close(dup);
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
            if name.starts_with('.') {
                continue;
            }
            names.push(name);
            if names.len() > MAX_REQUESTS {
                unsafe {
                    libc::closedir(stream);
                }
                return Err(err("invalid_request", "too many control requests"));
            }
        }
        unsafe {
            libc::closedir(stream);
        }
        let mut values = Vec::new();
        for name in names {
            let id = name
                .strip_suffix(".json")
                .filter(|s| safe_id(s))
                .ok_or_else(|| err("invalid_request", "invalid mailbox filename"))?;
            let value = self.read(&format!("{CONTROL}/requests/{name}"))?;
            if value["request"]["request_id"] != id {
                return Err(err("invalid_request", "request identity mismatch"));
            }
            number(&value, "sequence")?;
            values.push(value);
        }
        values.sort_by_key(|v| v["sequence"].as_u64().unwrap());
        if values
            .windows(2)
            .any(|p| p[0]["sequence"] == p[1]["sequence"])
        {
            return Err(err("invalid_request", "duplicate mailbox sequence"));
        }
        Ok(values)
    }
}

#[derive(Default)]
struct Args {
    command: String,
    out: Option<PathBuf>,
    session: Option<PathBuf>,
    serial: Option<String>,
    binding: Option<crate::device_registry::DeviceBinding>,
    package: Option<String>,
    mode: String,
    so: Option<String>,
    actor: String,
    origin: String,
    power_policy: String,
    seconds: u64,
    request: Option<String>,
    request_id: Option<String>,
    keep: bool,
    help: bool,
}
pub fn handles(args: &[String]) -> bool {
    args.first().is_some_and(|s| {
        matches!(
            s.as_str(),
            "session" | "session-status" | "session-action" | "session-recover"
        )
    })
}
fn parse(args: &[String]) -> Result<Args> {
    if !handles(args) {
        return Err(err("invalid_request", "unknown gray control entry"));
    }
    let mut a = Args {
        command: args[0].clone(),
        actor: "agent".into(),
        origin: "device".into(),
        power_policy: "observe".into(),
        seconds: 900,
        ..Args::default()
    };
    let mut serial_count = 0;
    let mut i = 1;
    while i < args.len() {
        let key = args[i].as_str();
        if matches!(key, "--help" | "-h") {
            a.help = true;
            i += 1;
            continue;
        }
        if key == "--json" {
            i += 1;
            continue;
        }
        if key == "--keep-prop" && a.command == "session" {
            a.keep = true;
            i += 1;
            continue;
        }
        let allowed = match a.command.as_str() {
            "session" => matches!(
                key,
                "--out"
                    | "--serial"
                    | "--package"
                    | "--mode"
                    | "--so"
                    | "--actor-id"
                    | "--evidence-origin"
                    | "--max-seconds"
                    | "--power-policy"
                    | "--device-binding"
            ),
            "session-status" => matches!(key, "--out" | "--request-id"),
            "session-action" => matches!(key, "--out" | "--request"),
            "session-recover" => matches!(key, "--session" | "--serial"),
            _ => false,
        };
        if !allowed {
            return Err(err("invalid_request", format!("unknown option {key}")));
        }
        i += 1;
        let value = args
            .get(i)
            .ok_or_else(|| err("invalid_request", format!("{key} requires a value")))?;
        match key {
            "--out" => a.out = Some(value.into()),
            "--session" => a.session = Some(value.into()),
            "--serial" => { a.serial = Some(value.clone()); serial_count += 1; }
            "--device-binding" => {
                if a.binding.is_some() { return Err(err("invalid_request", "duplicate device binding")); }
                a.binding = Some(crate::device_registry::parse_binding(value)
                    .map_err(|e| err("invalid_request", e))?);
            }
            "--package" => a.package = Some(value.clone()),
            "--mode" => a.mode = value.clone(),
            "--so" => a.so = Some(value.clone()),
            "--actor-id" => a.actor = value.clone(),
            "--evidence-origin" => a.origin = value.clone(),
            "--power-policy" => a.power_policy = value.clone(),
            "--request" => a.request = Some(value.clone()),
            "--request-id" => a.request_id = Some(value.clone()),
            "--max-seconds" => {
                a.seconds = value
                    .parse()
                    .map_err(|_| err("invalid_request", "max-seconds must be an integer"))?
            }
            _ => unreachable!(),
        }
        i += 1;
    }
    if a.help {
        return Ok(a);
    }
    if a.command == "session-recover" {
        if a.session.is_none() || a.serial.is_none() {
            return Err(err(
                "invalid_request",
                "recover requires --session and --serial",
            ));
        }
    } else if a.out.is_none() {
        return Err(err("invalid_request", "--out is required"));
    }
    if a.command == "session" {
        if a.binding.as_ref().is_some_and(|b| serial_count != 1 || a.serial.as_deref() != Some(&b.serial)) {
            return Err(err("invalid_request", "device binding requires exactly one matching explicit serial"));
        }
        if a.serial.as_ref().is_none_or(|s| s.is_empty())
            || a.package
                .as_ref()
                .is_none_or(|p| !crate::graylayer::valid_package(p))
        {
            return Err(err(
                "invalid_request",
                "session requires explicit --serial and valid --package",
            ));
        }
        if !(1..=3600).contains(&a.seconds)
            || !safe_id(&a.actor)
            || !matches!(a.origin.as_str(), "device" | "synthetic")
            || !matches!(a.power_policy.as_str(), "observe" | "battery")
        {
            return Err(err(
                "invalid_request",
                "invalid budget, actor, evidence origin or power policy",
            ));
        }
        if !matches!(
            a.mode.as_str(),
            "probe"
                | "target"
                | "passdump"
                | "loadop"
                | "frameaudit"
                | "frameaudit-ts"
                | "copyprobe"
                | "upop"
        ) {
            return Err(err(
                "invalid_request",
                "an explicit supported --mode is required",
            ));
        }
    }
    if a.command == "session-action" && a.request.is_none() {
        return Err(err("invalid_request", "--request is required"));
    }
    if a.request_id.as_ref().is_some_and(|id| !safe_id(id)) {
        return Err(err("invalid_request", "invalid request ID"));
    }
    Ok(a)
}
pub(crate) fn uses_device_binding(args: &[String]) -> bool {
    parse(args).is_ok_and(|a| !a.help && a.command == "session" && a.binding.is_some())
}
pub fn admission(args: &[String]) -> std::result::Result<GrayCliAdmission, String> {
    let a = parse(args).map_err(|e| e.message)?;
    if a.help {
        return Ok(GrayCliAdmission::Help);
    }
    Ok(if a.command == "session" {
        crate::graylayer::mode_admission_rejection(&a.mode, a.so.is_some())
            .map(GrayCliAdmission::Reject)
            .unwrap_or(GrayCliAdmission::Proceed)
    } else {
        GrayCliAdmission::Proceed
    })
}
pub fn run(args: &[String]) -> i32 {
    let result = (|| -> Result<(Value, i32)> {
        let a = parse(args)?;
        if a.help {
            return Ok((
                json!({"usage":"gray-layer session --mode MODE --package PKG --serial S --out DIR [--max-seconds 900] [--power-policy observe|battery]; session-status --out DIR [--request-id ID] --json; session-action --out DIR --request FILE|- --json; session-recover --session DIR --serial S --json"}),
                0,
            ));
        }
        if a.command == "session" {
            if let Some(v) = crate::graylayer::mode_admission_rejection(&a.mode, a.so.is_some()) {
                return Ok((v, 2));
            }
        }
        match a.command.as_str() {
            "session" => start(&a),
            "session-recover" => recover(&a),
            "session-status" => {
                let store = Store::open(a.out.as_ref().unwrap())?;
                let d = descriptor(&store)?;
                Ok((
                    if let Some(id) = a.request_id {
                        receipt(&store, &d, &id)?
                    } else {
                        observed(&store, d)?
                    },
                    0,
                ))
            }
            "session-action" => {
                let store = Store::open(a.out.as_ref().unwrap())?;
                let req = read_request(a.request.as_ref().unwrap())?;
                Ok((publish(&store, &req)?, 0))
            }
            _ => unreachable!(),
        }
    })();
    match result {
        Ok((v, code)) => {
            println!("{v}");
            code
        }
        Err(e) => {
            println!(
                "{}",
                json!({"schema":"phonefarm/gray-control-error@1","code":e.code,"error":e.message})
            );
            2
        }
    }
}

fn descriptor(store: &Store) -> Result<Value> {
    let mut d = store.read(DESCRIPTOR)?;
    if d["schema"] != SCHEMA || d["producer"] != "phonefarm" {
        return Err(err("invalid_request", "unrecognized control descriptor"));
    }
    let sid = string(&d, "session_id")?;
    let rel = string(&d, "session_path")?;
    if !safe_id(sid) || !sid.starts_with("gray-session-") || sid != rel {
        return Err(err("invalid_request", "invalid session identity/path"));
    }
    let m = store.read(&format!("{rel}/session.json"))?;
    if !matches!(
        m["schema"].as_str(),
        Some("phonefarm/gray-capture-session@2" | "phonefarm/gray-capture-session@3")
    ) || m["completion_owner"] != "gray_layer_control"
        || m["control_session_id"] != sid
        || m["serial"] != d["serial"]
        || m["package"] != d["package"]
        || m.get("device_binding") != d.get("device_binding")
        || m.get("lease_scope") != d.get("lease_scope")
    {
        return Err(err(
            "invalid_request",
            "descriptor does not match its control session",
        ));
    }
    if d["action_capabilities"]["sample"] == 2 && m["schema"] != "phonefarm/gray-capture-session@3"
    {
        return Err(err(
            "invalid_request",
            "finite input capability requires a bound resource parent",
        ));
    }
    if m["schema"] == "phonefarm/gray-capture-session@3" {
        let parent_schema = crate::gray_session::control_parent_schema(&m["control_resources"])
            .map_err(|e| err("invalid_request", e))?;
        if d["action_capabilities"]["sample"] == 2
            && !crate::gray_session::control_parent_supports_input(parent_schema)
        {
            return Err(err(
                "invalid_request",
                "finite input capability does not match its resource parent",
            ));
        }
        let parent = store.read_limit(&format!("{rel}/eval-session.json"), 8 * 1024 * 1024)?;
        if parent["schema"] != parent_schema
            || ["token", "serial", "completion_owner", "device_binding", "lease_scope"]
                .iter()
                .any(|k| parent["identity"][*k] != m[*k])
        {
            return Err(err(
                "invalid_request",
                "control resource parent is missing or mismatched",
            ));
        }
        if d.get("power").is_some() {
            d["power"] = crate::gray_eval_session::control_power_snapshot(&parent)
                .map_err(|e| err("invalid_request", e))?;
        }
    }
    if !d["job_id"].is_null()
        && !d["job_id"]
            .as_str()
            .is_some_and(crate::lease_receipt::valid_job_id)
    {
        return Err(err("invalid_request", "invalid descriptor job ID"));
    }
    if let Some(label) = d["latest_quality"]["label"].as_str() {
        if !safe_id(label) {
            return Err(err("invalid_request", "invalid quality label"));
        }
        let path = format!("{CONTROL}/quality/{label}/quality.json");
        let quality = store.read(&path)?;
        crate::gray_quality::validate(&quality, &d, label, &path)
            .map_err(|e| err("invalid_request", e))?;
        d["latest_quality"] = crate::gray_quality::summary(&quality);
    }
    Ok(d)
}
fn observed(store: &Store, mut d: Value) -> Result<Value> {
    let live = store.live()?;
    d["live_owner"] = json!(live);
    if !live && d["phase"] != "closed" {
        d["phase"] = json!("recovery_required");
        d["accepting"] = json!(false);
        d["allowed_actions"] = json!([]);
        d["recovery"]["status"] = json!("required");
        d["recovery"]["error"] = json!("owner is not running; reconnect cannot replay actions");
    }
    Ok(d)
}
fn read_request(path: &str) -> Result<Value> {
    let mut bytes = Vec::new();
    if path == "-" {
        std::io::stdin()
            .take((LIMIT + 1) as u64)
            .read_to_end(&mut bytes)?;
    } else {
        File::open(path)?
            .take((LIMIT + 1) as u64)
            .read_to_end(&mut bytes)?;
    }
    if bytes.len() > LIMIT {
        return Err(err("invalid_request", "request exceeds 64 KiB"));
    }
    let v = serde_json::from_slice(&bytes).map_err(|e| err("invalid_request", e.to_string()))?;
    validate_request(&v)?;
    Ok(v)
}
fn exact(v: &Value, keys: &[&str]) -> Result<()> {
    let o = v
        .as_object()
        .ok_or_else(|| err("invalid_request", "expected object"))?;
    if o.len() != keys.len() || !keys.iter().all(|k| o.contains_key(*k)) {
        return Err(err(
            "invalid_request",
            format!("expected fields: {}", keys.join(",")),
        ));
    }
    Ok(())
}
fn validate_request(r: &Value) -> Result<()> {
    exact(
        r,
        &[
            "schema",
            "request_id",
            "session_id",
            "actor_id",
            "control_epoch",
            "action",
            "params",
        ],
    )?;
    if r["schema"] != ACTION {
        return Err(err("invalid_request", "unknown action schema"));
    }
    for key in ["request_id", "session_id", "actor_id"] {
        if !safe_id(string(r, key)?) {
            return Err(err("invalid_request", format!("invalid {key}")));
        }
    }
    number(r, "control_epoch")?;
    let p = &r["params"];
    match string(r, "action")? {
        "capture" | "marker" | "narrow" | "release_control" | "stop" => exact(p, &[])?,
        "quality" => {
            exact(p, &["op", "label"])?;
            if !matches!(string(p, "op")?, "begin" | "checkpoint" | "finish")
                || !safe_id(string(p, "label")?)
            {
                return Err(err(
                    "invalid_request",
                    "quality requires begin/checkpoint/finish and a 1–64 character safe label",
                ));
            }
        }
        "sample" => {
            let mut fields = vec!["seconds"];
            if p.get("render_comm").is_some() {
                fields.push("render_comm");
            }
            if p.get("input").is_some() {
                fields.extend(["input", "frame_id"]);
                if !safe_id(string(p, "frame_id")?) {
                    return Err(err("invalid_request", "invalid sample frame_id"));
                }
            }
            exact(p, &fields)?;
            crate::gray_script::Plan::control(p.get("input"), number(p, "seconds")?)
                .map_err(|e| err("invalid_request", e))?;
            if let Some(comm) = p.get("render_comm") {
                let comm = comm
                    .as_str()
                    .ok_or_else(|| err("invalid_request", "render_comm must be a string"))?;
                if comm.is_empty() || comm.len() > 64 || comm.chars().any(char::is_control) {
                    return Err(err(
                        "invalid_request",
                        "render_comm must be 1..64 bytes without control characters",
                    ));
                }
            }
        }
        "tap" => {
            exact(p, &["x", "y", "frame_id"])?;
            coordinates(p, &["x", "y"])?;
            if !safe_id(string(p, "frame_id")?) {
                return Err(err("invalid_request", "invalid frame_id"));
            }
        }
        "swipe" => {
            exact(p, &["x1", "y1", "x2", "y2", "duration_ms", "frame_id"])?;
            coordinates(p, &["x1", "y1", "x2", "y2"])?;
            if !(1..=10000).contains(&number(p, "duration_ms")?) || !safe_id(string(p, "frame_id")?)
            {
                return Err(err("invalid_request", "invalid swipe duration/frame_id"));
            }
        }
        "audit" | "trigger" => {
            exact(p, &["go"])?;
            if !(1..=u32::MAX as u64).contains(&number(p, "go")?) {
                return Err(err("invalid_request", "go must be 1..u32::MAX"));
            }
        }
        "take_control" => {
            exact(p, &["kind", "ttl_seconds"])?;
            if !matches!(string(p, "kind")?, "agent" | "human")
                || !(1..=300).contains(&number(p, "ttl_seconds")?)
            {
                return Err(err("invalid_request", "invalid control kind/TTL"));
            }
        }
        _ => return Err(err("invalid_request", "unsupported action")),
    }
    Ok(())
}
fn coordinates(p: &Value, keys: &[&str]) -> Result<()> {
    for key in keys {
        if number(p, key)? > u32::MAX as u64 {
            return Err(err("invalid_request", "coordinate exceeds u32"));
        }
    }
    Ok(())
}
fn receipt_path(id: &str, kind: &str) -> String {
    format!("{CONTROL}/{kind}/{id}.json")
}
fn initial_receipt(envelope: &Value) -> Value {
    let r = &envelope["request"];
    json!({"schema":RECEIPT,"session_id":r["session_id"],"request_id":r["request_id"],"sequence":envelope["sequence"],"status":"accepted","requested_at":envelope["requested_at"],"started_at":null,"finished_at":null,"result":null,"error":null})
}
fn receipt(store: &Store, d: &Value, id: &str) -> Result<Value> {
    if !safe_id(id) {
        return Err(err("invalid_request", "invalid request ID"));
    }
    let e = store.read(&receipt_path(id, "requests"))?;
    if e["request"]["session_id"] != d["session_id"] {
        return Err(err("invalid_request", "request belongs to another session"));
    }
    if let Some(v) = store.optional(&receipt_path(id, "results"))? {
        return Ok(v);
    }
    let mut v = store
        .optional(&receipt_path(id, "started"))?
        .unwrap_or_else(|| initial_receipt(&e));
    if !store.live()? {
        // Re-read after observing a stopped owner, so a completed action wins.
        if let Some(done) = store.optional(&receipt_path(id, "results"))? {
            return Ok(done);
        }
        if let Some(started) = store.optional(&receipt_path(id, "started"))? {
            v = started;
        }
        let started = v["status"] == "started";
        v["status"] = json!(if started { "unknown" } else { "not_executed" });
        v["error"] = json!(if started {
            "owner stopped without an action result; do not replay"
        } else {
            "owner stopped before starting this request"
        });
    }
    Ok(v)
}
fn sample_capability(d: &Value, r: &Value) -> Result<()> {
    if r["action"] != "sample" {
        return Ok(());
    }
    if !d["allowed_actions"]
        .as_array()
        .is_some_and(|a| a.iter().any(|v| v == "sample"))
    {
        return Err(err(
            "invalid_request",
            "this session does not support sampling",
        ));
    }
    if r["params"].get("input").is_some() && d["action_capabilities"]["sample"] != 2 {
        return Err(err(
            "invalid_request",
            "this session does not support finite input sampling; start a newer control session",
        ));
    }
    Ok(())
}
fn quality_capability(d: &Value, r: &Value) -> Result<()> {
    if r["action"] == "quality"
        && (d["action_capabilities"]["quality"] != 1
            || !d["allowed_actions"]
                .as_array()
                .is_some_and(|a| a.iter().any(|v| v == "quality")))
    {
        return Err(err(
            "invalid_request",
            "this owner does not support quality checkpoints; start a newer control session",
        ));
    }
    Ok(())
}
fn sample_budget(deadline: Instant, plan: &crate::gray_script::Plan) -> Result<()> {
    let required_ms = plan.duration_ms().max((plan.seconds + 10) * 1000);
    if deadline.saturating_duration_since(Instant::now()) < Duration::from_millis(required_ms) {
        return Err(err(
            "budget_exhausted",
            "remaining session budget does not cover the full input plan and sampling window",
        ));
    }
    Ok(())
}
fn publish(store: &Store, r: &Value) -> Result<Value> {
    validate_request(r)?;
    let _publisher = store.lock("publisher", Duration::from_secs(2))?;
    let d = descriptor(store)?;
    if r["session_id"] != d["session_id"] {
        return Err(err("conflict", "session_id mismatch"));
    }
    let id = string(r, "request_id")?;
    if let Some(prior) = store.optional(&receipt_path(id, "requests"))? {
        if prior["request"] != *r {
            return Err(err("conflict", "request ID already has different content"));
        }
        return receipt(store, &d, id);
    }
    if !store.live()? {
        return Err(err("owner_unavailable", "control owner is not running"));
    }
    if d["accepting"] != true {
        return Err(err("session_closed", "session is not accepting actions"));
    }
    sample_capability(&d, r)?;
    quality_capability(&d, r)?;
    let pending = store.requests()?;
    if pending.iter().any(|e| e["request"]["action"] == "stop") {
        return Err(err("session_closed", "stop is already queued"));
    }
    if pending.len() >= MAX_REQUESTS || (pending.len() >= MAX_REQUESTS - 1 && r["action"] != "stop")
    {
        return Err(err(
            "session_closed",
            "request budget reached; one slot is reserved for stop",
        ));
    }
    let seq = number(
        &store.read(&format!("{CONTROL}/publisher.json"))?,
        "next_sequence",
    )?;
    let next = seq
        .checked_add(1)
        .ok_or_else(|| err("io_error", "sequence exhausted"))?;
    store.write(
        &format!("{CONTROL}/publisher.json"),
        &json!({"next_sequence":next}),
        true,
    )?;
    let e = json!({"sequence":seq,"requested_at":now(),"request":r});
    store.write(&receipt_path(id, "requests"), &e, false)?;
    Ok(initial_receipt(&e))
}

extern "C" fn interrupted(_: libc::c_int) {
    STOP.store(true, Ordering::SeqCst);
}
struct Interrupt {
    previous: libc::sigaction,
}
impl Interrupt {
    fn install() -> Result<Self> {
        STOP.store(false, Ordering::SeqCst);
        let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
        let mut previous = unsafe { std::mem::zeroed() };
        action.sa_sigaction = interrupted as *const () as usize;
        unsafe {
            libc::sigemptyset(&mut action.sa_mask);
        }
        if unsafe { libc::sigaction(libc::SIGINT, &action, &mut previous) } != 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        Ok(Self { previous })
    }
}
impl Drop for Interrupt {
    fn drop(&mut self) {
        unsafe {
            libc::sigaction(libc::SIGINT, &self.previous, std::ptr::null_mut());
        }
    }
}

fn create_control(store: &Store) -> Result<()> {
    for rel in [
        CONTROL,
        "gray-control/requests",
        "gray-control/started",
        "gray-control/results",
        "gray-control/artifacts",
        "gray-control/quality",
    ] {
        store.mkdir(rel)?;
    }
    for name in ["owner", "publisher"] {
        let file = store.open_file(
            &format!("{CONTROL}/{name}.lock"),
            libc::O_CREAT | libc::O_EXCL | libc::O_RDWR,
        )?;
        file.sync_all()?;
    }
    store.write(
        &format!("{CONTROL}/publisher.json"),
        &json!({"next_sequence":1}),
        false,
    )
}
fn job_identity(store: &Store) -> Result<Value> {
    match (
        std::env::var_os("PF_JOB_ID"),
        std::env::var_os("PF_JOB_OUT"),
    ) {
        (None, None) => Ok(Value::Null),
        (Some(id), Some(out)) => {
            let id = id
                .to_str()
                .filter(|s| crate::lease_receipt::valid_job_id(s))
                .ok_or_else(|| {
                    err(
                        "invalid_request",
                        "PF_JOB_ID must be 12 lowercase hex characters",
                    )
                })?;
            if Path::new(&out).canonicalize()? != store.path {
                return Err(err(
                    "invalid_request",
                    "--out must match PF_JOB_OUT for a node session",
                ));
            }
            Ok(json!(id))
        }
        _ => Err(err(
            "invalid_request",
            "PF_JOB_ID and PF_JOB_OUT must be supplied together",
        )),
    }
}
fn allowed(mode: &str) -> Value {
    json!(ACTIONS
        .iter()
        .filter(|a| !matches!(**a, "trigger" | "audit")
            || matches!(mode, "frameaudit" | "frameaudit-ts"))
        .copied()
        .collect::<Vec<_>>())
}
fn save_descriptor(store: &Store, d: &mut Value) -> Result<()> {
    d["updated_at"] = json!(now());
    store.write(DESCRIPTOR, d, true)
}
fn start(a: &Args) -> Result<(Value, i32)> {
    crate::gray_eval_session::admit_control_power(a.serial.as_ref().unwrap(), &a.power_policy)
        .map_err(device)?;
    let out = a.out.as_ref().unwrap();
    std::fs::create_dir_all(out)?;
    let store = Store::open(out)?;
    let job = job_identity(&store)?;
    if store.optional(DESCRIPTOR)?.is_some() {
        return Err(err("conflict", "out already belongs to a control session"));
    }
    create_control(&store)?;
    let _owner = store.lock("owner", Duration::ZERO)?;
    let _interrupt = Interrupt::install()?;
    let lease = if let Some(binding) = &a.binding {
        crate::devlease::Lease::acquire_device(&crate::lease_storage::resolve(), binding,
            "gray-layer-control", crate::devlease::wait_timeout().map_err(device)?)
    } else {
        crate::devlease::Lease::target(a.serial.as_deref(), "gray-layer-control")
    }.map_err(device)?;
    let binding = lease.device_binding().cloned();
    let base = Session::new_control(lease, &store.path, a.package.as_ref().unwrap(), &a.origin)
        .map_err(device)?;
    let parent = Parent::new_control(base, &a.power_policy).map_err(device)?;
    let base = &parent.base;
    let sid = base
        .dir
        .file_name()
        .and_then(|s| s.to_str())
        .filter(|s| safe_id(s))
        .ok_or_else(|| err("io_error", "invalid public session directory"))?;
    let deadline = Instant::now() + Duration::from_secs(a.seconds);
    let mut d = json!({"schema":SCHEMA,"producer":"phonefarm","session_id":sid,"session_path":sid,"job_id":job,
        "serial":a.serial,"package":a.package,"mode":a.mode,"phase":"starting","accepting":false,
        "controller":{"actor_id":a.actor,"kind":"agent","epoch":1,"expires_at":null},"allowed_actions":allowed(&a.mode),"action_capabilities":{"sample":2,"quality":1},"latest_quality":null,
        "power":parent.control_power_state().map_err(device)?,"latest_image":null,"recovery":{"status":"not_reported","session_path":sid,"reports":[],"error":null},
        "created_at":now(),"updated_at":now(),"deadline_at":(chrono::Utc::now()+chrono::Duration::seconds(a.seconds as i64)).to_rfc3339(),
        "max_seconds":a.seconds,"frame_max_age_seconds":FRAME_AGE_SECONDS,"keep_prop":a.keep,"stop_reason":null});
    if let Some(binding) = &binding {
        d["device_binding"] = json!(binding);
        d["lease_scope"] = json!("device");
    }
    store.write(DESCRIPTOR, &d, false)?;
    let preparation = (|| -> Result<()> {
        base.prepare().map_err(device)?;
        if STOP.load(Ordering::SeqCst) || Instant::now() >= deadline {
            return Err(err("stopped", "stopped before installation"));
        }
        base.install_control(a.so.as_deref(), &a.mode)
            .map_err(device)?;
        if !a.keep {
            let until = (Instant::now() + Duration::from_secs(80)).min(deadline);
            loop {
                if STOP.load(Ordering::SeqCst) || Instant::now() >= deadline {
                    return Err(err("stopped", "stopped while waiting for layer marker"));
                }
                if base.marker().map_err(device)?.is_some() {
                    base.narrow().map_err(device)?;
                    break;
                }
                if Instant::now() >= until {
                    return Err(err(
                        "device_error",
                        "layer marker was not observed within 80 seconds",
                    ));
                }
                std::thread::sleep(Duration::from_millis(200));
            }
        }
        Ok(())
    })();
    let (stop_request, failure) = match preparation {
        Ok(()) => {
            d["phase"] = json!("ready");
            d["accepting"] = json!(true);
            save_descriptor(&store, &mut d)?;
            serve(&store, &parent, &mut d, deadline, &a.actor)
        }
        Err(e) => {
            let message = public_text(&base, &e.message);
            d["stop_reason"] = json!(message);
            (
                None,
                if e.code == "stopped" {
                    None
                } else {
                    Some(message)
                },
            )
        }
    };
    finish(
        &store,
        &base,
        Some(&parent),
        &mut d,
        stop_request.as_ref(),
        failure.as_deref(),
    )
}

/// Returns only at an action boundary. SIGINT never interrupts the independently
/// supervised transport process group; that bounded call must report first.
fn serve(
    store: &Store,
    parent: &Parent,
    d: &mut Value,
    deadline: Instant,
    original_actor: &str,
) -> (Option<Value>, Option<String>) {
    let base = &parent.base;
    let mut used_go = HashSet::new();
    loop {
        if STOP.load(Ordering::SeqCst) || Instant::now() >= deadline {
            d["stop_reason"] = json!(if STOP.load(Ordering::SeqCst) {
                "SIGINT"
            } else {
                "budget_expired"
            });
            return (None, None);
        }
        let cycle = (|| -> Result<Option<Value>> {
            base.control_ready().map_err(device)?;
            if expire_control(d, original_actor)? {
                save_descriptor(store, d)?;
            }
            let pending = store.requests()?;
            // Stop is a lifecycle request, not another delayed input. The loop
            // handles one action per turn, rechecking this at each boundary.
            if let Some(envelope) = pending.iter().find(|e| e["request"]["action"] == "stop") {
                let id = string(&envelope["request"], "request_id")?;
                if store.optional(&receipt_path(id, "results"))?.is_none() {
                    if store.optional(&receipt_path(id, "started"))?.is_some() {
                        return Err(err("unknown", "stop was already started without a result"));
                    }
                    let mut record = initial_receipt(envelope);
                    record["status"] = json!("started");
                    record["started_at"] = json!(now());
                    store.write(&receipt_path(id, "started"), &record, false)?;
                    d["stop_reason"] = json!("requested");
                    return Ok(Some(record));
                }
            }
            for envelope in pending {
                let r = &envelope["request"];
                validate_request(r)?;
                let id = string(r, "request_id")?;
                if store.optional(&receipt_path(id, "results"))?.is_some() {
                    continue;
                }
                if store.optional(&receipt_path(id, "started"))?.is_some() {
                    return Err(err(
                        "unknown",
                        "started action has no result; refusing replay",
                    ));
                }
                if STOP.load(Ordering::SeqCst) || Instant::now() >= deadline {
                    return Ok(None);
                }
                let mut record = initial_receipt(&envelope);
                record["status"] = json!("started");
                record["started_at"] = json!(now());
                store.write(&receipt_path(id, "started"), &record, false)?;
                if r["action"] == "stop" {
                    d["stop_reason"] = json!("requested");
                    return Ok(Some(record));
                }
                d["phase"] = json!("executing");
                save_descriptor(store, d)?;
                let answer = execute(
                    store,
                    parent,
                    d,
                    r,
                    original_actor,
                    &mut used_go,
                    deadline,
                    number(&envelope, "sequence")?,
                );
                let health = base.control_ready().and_then(|_| {
                    if r["action"] == "sample" && d["phase"] == "measuring" {
                        parent.control_health()
                    } else {
                        Ok(())
                    }
                });
                record["finished_at"] = json!(now());
                match answer {
                    Ok(value) => {
                        record["status"] = json!(if health.is_err() {
                            "unknown"
                        } else if value
                            .get("collection")
                            .is_some_and(|c| c["status"] == "failed")
                        {
                            "failed"
                        } else {
                            "completed"
                        });
                        record["result"] = value;
                        if r["action"] == "sample" {
                            record["result"]["resource_health"] = json!({"status":if health.is_ok(){"ready"}else{"required"},"error":health.as_ref().err().map(|e|public_text(base,e))});
                            d["latest_sample"] = record["result"].clone();
                        }
                    }
                    Err(e) => {
                        record["status"] = json!(if health.is_ok() { "failed" } else { "unknown" });
                        record["error"] =
                            json!({"code":e.code,"message":public_text(base,&e.message)});
                    }
                }
                store.write(&receipt_path(id, "results"), &record, false)?;
                if r["action"] == "sample" {
                    d["active_sample"] = Value::Null;
                }
                if let Err(error) = health {
                    return Err(device(error));
                }
                d["phase"] = json!("ready");
                save_descriptor(store, d)?;
                break;
            }
            Ok(None)
        })();
        match cycle {
            Ok(Some(stop)) => return (Some(stop), None),
            Err(e) => return (None, Some(public_text(base, &e.message))),
            Ok(None) => {}
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}
fn bump_epoch(d: &mut Value) -> Result<u64> {
    let e = number(&d["controller"], "epoch")?
        .checked_add(1)
        .ok_or_else(|| err("io_error", "control epoch exhausted"))?;
    d["controller"]["epoch"] = json!(e);
    Ok(e)
}
fn expire_control(d: &mut Value, original: &str) -> Result<bool> {
    let Some(expires) = d["controller"]["expires_at"].as_str() else {
        return Ok(false);
    };
    let expires = chrono::DateTime::parse_from_rfc3339(expires)
        .map_err(|_| err("invalid_request", "invalid controller expiry"))?;
    if chrono::Utc::now() < expires {
        return Ok(false);
    }
    let epoch = bump_epoch(d)?;
    d["controller"] = json!({"actor_id":original,"kind":"agent","epoch":epoch,"expires_at":null});
    Ok(true)
}
fn require_controller(d: &Value, r: &Value) -> Result<()> {
    if d["controller"]["epoch"] != r["control_epoch"] {
        return Err(err(
            "conflict",
            "control epoch changed; refresh session status",
        ));
    }
    if d["controller"]["actor_id"] != r["actor_id"] {
        return Err(err(
            "conflict",
            "another actor currently owns input control",
        ));
    }
    Ok(())
}
fn execute(
    store: &Store,
    parent: &Parent,
    d: &mut Value,
    r: &Value,
    original: &str,
    used_go: &mut HashSet<u32>,
    deadline: Instant,
    sequence: u64,
) -> Result<Value> {
    let base = &parent.base;
    if r["session_id"] != d["session_id"] {
        return Err(err("conflict", "request session mismatch"));
    }
    expire_control(d, original)?;
    let action = string(r, "action")?;
    let id = string(r, "request_id")?;
    let p = &r["params"];
    if matches!(
        action,
        "tap" | "swipe" | "trigger" | "narrow" | "release_control" | "sample" | "quality"
    ) {
        require_controller(d, r)?;
    }
    match action {
        "quality" => {
            quality_capability(d, r)?;
            execute_quality(store, base, d, r)
        }
        "sample" => {
            sample_capability(d, r)?;
            let seconds = number(p, "seconds")?;
            let plan = crate::gray_script::Plan::control(p.get("input"), seconds)
                .map_err(|e| err("invalid_request", e))?;
            sample_budget(deadline, &plan)?;
            let input_kind = if p.get("input").is_some() {
                "finite"
            } else {
                "idle"
            };
            let initial_frame = if input_kind == "finite" {
                let image = input_image(d, p)?;
                let w = u32::try_from(number(image, "width")?)
                    .map_err(|_| err("invalid_request", "invalid image width"))?;
                let h = u32::try_from(number(image, "height")?)
                    .map_err(|_| err("invalid_request", "invalid image height"))?;
                plan.validate_screen(w, h)
                    .map_err(|e| err("invalid_request", e))?;
                image.clone()
            } else {
                Value::Null
            };
            let label = format!("sample_{sequence}");
            let relative = format!("{CONTROL}/artifacts/{label}");
            let out = store.path.join(&relative);
            store.mkdir(&relative)?;
            d["phase"] = json!("measuring");
            d["active_sample"] = json!({"request_id":id,"seconds":seconds,"input_kind":input_kind,"planned_input_ms":plan.duration_ms(),"started_at":now(),"path":relative});
            save_descriptor(store, d)?;
            store.write(&format!("{relative}/sample-request.json"), &json!({
                "schema":"phonefarm/gray-control-sample-request@1", "request_id":id,"power_policy":d["power"]["policy"],
                "input_kind":input_kind,"planned_input_ms":plan.duration_ms(),"initial_frame":initial_frame,
            }), false)?;
            let collection = parent.sample_current(&label, &plan, input_kind, &out, || {
                require_controller(d, r).map_err(|e| e.message)?;
                if input_kind == "finite" {
                    verify_image(
                        base,
                        &initial_frame,
                        &out.join("input-check.png"),
                        Some(&out.join("input-frame.json")),
                    )
                    .map_err(|e| e.message)?;
                }
                sample_budget(deadline, &plan).map_err(|e| e.message)
            });
            let (trace_ok, sampler_ok, input) = match collection {
                Ok(result) => result,
                Err(error) => {
                    store.write(
                        &format!("{relative}/collection-error.json"),
                        &json!({"error":public_text(base,&error)}),
                        false,
                    )?;
                    (false, false, Value::Null)
                }
            };
            d["power"] = parent.control_power_state().map_err(device)?;
            // Observation only: the target is not stopped or restarted here.
            let alive = base.target_alive().unwrap_or(false);
            let origin = base.control_identity().map_err(device)?["evidence_origin"]
                .as_str()
                .ok_or_else(|| device("missing evidence origin".into()))?
                .to_owned();
            let run = crate::eval::summarize_control_sample(
                &out,
                &label,
                seconds,
                trace_ok,
                sampler_ok,
                input,
                alive,
                &origin,
                p["render_comm"].as_str(),
            )
            .map_err(device)?;
            let artifacts=run["artifacts"].as_array().ok_or_else(||device("sample result has no artifacts".into()))?.iter().map(|a|json!({"name":a["name"],"path":format!("{relative}/{}",a["path"].as_str().unwrap_or(""))})).collect::<Vec<_>>();
            let mut collection = run["collection"].clone();
            if let Some(error) = collection["error"].as_str() {
                collection["error"] = json!(
                    public_text(base, error).replace(&format!("{}/", store.path.display()), "")
                );
            }
            let mut power = run["power"].clone();
            if let Some(error) = power["error"].as_str() {
                power["error"] = json!(public_text(base, error));
            }
            let result = json!({"path":format!("{relative}/run.json"),"collection":collection,"requested_seconds":run["requested_seconds"],
                "input_kind":input_kind,"planned_input_ms":plan.duration_ms(),"initial_frame":initial_frame,"power":power,
                "metrics":run["metrics"],"metric_provenance":run["metric_provenance"],"limitations":run["limitations"],"artifacts":artifacts});
            d["latest_sample"] = result.clone();
            d["active_sample"] = Value::Null;
            save_descriptor(store, d)?;
            Ok(result)
        }
        "take_control" => {
            if d["controller"]["epoch"] != r["control_epoch"] {
                return Err(err("conflict", "control epoch changed"));
            }
            let same = d["controller"]["actor_id"] == r["actor_id"]
                && d["controller"]["kind"] == p["kind"];
            let epoch = if same {
                number(&d["controller"], "epoch")?
            } else {
                bump_epoch(d)?
            };
            let expires = if p["kind"] == "human" {
                json!((chrono::Utc::now()
                    + chrono::Duration::seconds(number(p, "ttl_seconds")? as i64))
                .to_rfc3339())
            } else {
                Value::Null
            };
            d["controller"] = json!({"actor_id":r["actor_id"],"kind":p["kind"],"epoch":epoch,"expires_at":expires});
            save_descriptor(store, d)?;
            Ok(json!({"controller":d["controller"]}))
        }
        "release_control" => {
            let epoch = bump_epoch(d)?;
            d["controller"] =
                json!({"actor_id":original,"kind":"agent","epoch":epoch,"expires_at":null});
            save_descriptor(store, d)?;
            Ok(json!({"controller":d["controller"]}))
        }
        "capture" => {
            let path = format!("{CONTROL}/artifacts/capture-{id}.png");
            let (image, _) = capture_image(store, base, id, &path)?;
            d["latest_image"] = image.clone();
            save_descriptor(store, d)?;
            Ok(json!({"input_available":!image["rotation"].is_null(),"image":image}))
        }
        "tap" | "swipe" => {
            check_frame(store, base, d, r)?;
            if action == "tap" {
                base.tap(number(p, "x")? as u32, number(p, "y")? as u32)
                    .map_err(device)?;
            } else {
                base.swipe(
                    number(p, "x1")? as u32,
                    number(p, "y1")? as u32,
                    number(p, "x2")? as u32,
                    number(p, "y2")? as u32,
                    number(p, "duration_ms")? as u32,
                )
                .map_err(device)?;
            }
            Ok(json!({"action":action,"params":p,"control_epoch":d["controller"]["epoch"]}))
        }
        "narrow" => {
            base.narrow().map_err(device)?;
            Ok(json!({"global_layer_property_cleared":true,"target_layer_unloaded":false}))
        }
        "trigger" => {
            frameaudit_mode(d)?;
            let go = number(p, "go")? as u32;
            if used_go.contains(&go) {
                return Err(err(
                    "conflict",
                    "go was already triggered in this session; use a new go",
                ));
            }
            base.trigger(go).map_err(device)?;
            used_go.insert(go);
            Ok(json!({"go":go,"triggered":true,"capture_complete":false}))
        }
        "marker" | "audit" => {
            let value = if action == "marker" {
                base.marker().map_err(device)?
            } else {
                frameaudit_mode(d)?;
                let go = number(p, "go")? as u32;
                if !used_go.contains(&go) {
                    return Err(err(
                        "conflict",
                        "audit requires a successful trigger with this go in the current session",
                    ));
                }
                base.audit(go).map_err(device)?
            };
            let Some(value) = value else {
                return Err(err(
                    "not_found",
                    "requested gray evidence is not available yet",
                ));
            };
            let path = format!("{CONTROL}/artifacts/{id}.json");
            store.write(&path, &value, false)?;
            Ok(
                json!({"path":path,"observed_at":now(),"go":p.get("go"),"kind":action,"new_capture_claimed":action=="audit"}),
            )
        }
        _ => Err(err("invalid_request", "unsupported executable action")),
    }
}
fn frameaudit_mode(d: &Value) -> Result<()> {
    if !matches!(d["mode"].as_str(), Some("frameaudit" | "frameaudit-ts")) {
        return Err(err(
            "invalid_request",
            "trigger/audit require a frameaudit mode",
        ));
    }
    Ok(())
}
fn capture_image(
    store: &Store,
    base: &Session,
    id: &str,
    path: &str,
) -> Result<(Value, image::DynamicImage)> {
    let before_rotation = base.display_rotation().ok();
    let bytes = base.screenshot_bytes().map_err(device)?;
    let captured_at = now();
    store.write_bytes(path, &bytes, false)?;
    drop(bytes);
    let decoded =
        crate::gray_quality::read_png(BufReader::new(store.open_file(path, libc::O_RDONLY)?))
            .map_err(device)?;
    let after_rotation = base.display_rotation().ok();
    let rotation = if before_rotation == after_rotation {
        after_rotation
    } else {
        None
    };
    base.control_ready().map_err(device)?;
    Ok((
        json!({"path":path,"request_id":id,"captured_at":captured_at,"width":decoded.width(),"height":decoded.height(),
        "rotation":rotation,"format":"png","lossless":true}),
        decoded,
    ))
}
fn save_quality(store: &Store, d: &mut Value, manifest: &Value, replace: bool) -> Result<()> {
    store.write(string(manifest, "path")?, manifest, replace)?;
    d["latest_quality"] = crate::gray_quality::summary(manifest);
    save_descriptor(store, d)
}
fn execute_quality(store: &Store, base: &Session, d: &mut Value, r: &Value) -> Result<Value> {
    let label = string(&r["params"], "label")?;
    let op = string(&r["params"], "op")?;
    let id = string(r, "request_id")?;
    let directory = format!("{CONTROL}/quality/{label}");
    let path = format!("{directory}/quality.json");
    let prior = store.optional(&path)?;
    if prior.is_none() && op != "begin" {
        return Err(err("conflict", "quality label has not begun"));
    }
    let mut manifest = prior
        .clone()
        .unwrap_or_else(|| crate::gray_quality::new_manifest(d, label, &path, &now()));
    crate::gray_quality::validate(&manifest, d, label, &path).map_err(|e| err("conflict", e))?;
    let attempt_dir = format!("{directory}/attempts/{id}");
    let attempt_path = format!("{attempt_dir}/attempt.json");
    crate::gray_quality::start_attempt(&mut manifest, r, &attempt_path, &now())
        .map_err(|e| err("conflict", e))?;
    if prior.is_none() {
        store.mkdir(&directory)?;
        store.mkdir(&format!("{directory}/attempts"))?;
    }
    store.mkdir(&attempt_dir)?;
    save_quality(store, d, &manifest, prior.is_some())?;
    let acquisition = (|| -> Result<()> {
        let original = format!("{attempt_dir}/original.png");
        let (image, decoded) = capture_image(store, base, id, &original)?;
        manifest["active_attempt"]["image"] = image.clone();
        d["latest_image"] = image;
        save_quality(store, d, &manifest, true)?;
        let (bytes, width, height) =
            crate::gray_quality::half_preview(&decoded).map_err(|e| err("preview_error", e))?;
        let preview = format!("{attempt_dir}/preview-half.png");
        store.write_bytes(&preview, &bytes, false)?;
        manifest["active_attempt"]["preview"] = json!({"path":preview,"width":width,"height":height,"format":"png",
            "derived":true,"method":"stride2_rgb_even_coordinates","source_path":original});
        save_quality(store, d, &manifest, true)?;
        if op == "finish" {
            let marker = base
                .marker()
                .map_err(device)?
                .ok_or_else(|| err("not_found", "quality finish marker is not available"))?;
            let marker_path = format!("{attempt_dir}/marker.json");
            store.write(&marker_path, &marker, false)?;
            manifest["active_attempt"]["marker"] =
                json!({"path":marker_path,"observed_at":now(),"kind":"marker"});
            save_quality(store, d, &manifest, true)?;
            crate::gray_quality::validate_marker(&marker, string(d, "package")?)
                .map_err(|e| err("invalid_evidence", e))?;
        }
        Ok(())
    })();
    let error = acquisition
        .err()
        .map(|e| json!({"code":e.code,"message":public_text(base,&e.message)}));
    let attempt = crate::gray_quality::finish_attempt(&mut manifest, error, &now())
        .map_err(|e| err("io_error", e))?;
    store.write(&attempt_path, &attempt, false)?;
    save_quality(store, d, &manifest, true)?;
    Ok(
        json!({"quality":crate::gray_quality::summary(&manifest),"image":attempt["image"],
        "collection":{"status":if attempt["status"]=="completed"{"complete"}else{"failed"},"error":attempt["error"]["message"]}}),
    )
}
fn png_dimensions(path: &Path) -> Result<(u32, u32)> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)?;
    let decoded = crate::gray_quality::read_png(BufReader::new(file)).map_err(device)?;
    Ok((decoded.width(), decoded.height()))
}
fn frame_fresh(image: &Value) -> Result<()> {
    let at = chrono::DateTime::parse_from_rfc3339(string(image, "captured_at")?)
        .map_err(|_| err("conflict", "invalid capture timestamp"))?;
    let age = chrono::Utc::now()
        .signed_duration_since(at)
        .num_milliseconds();
    if !(0..=FRAME_AGE_SECONDS * 1000).contains(&age) {
        return Err(err(
            "conflict",
            "capture is stale; capture again before input",
        ));
    }
    Ok(())
}
fn input_image<'a>(d: &'a Value, p: &Value) -> Result<&'a Value> {
    let image = &d["latest_image"];
    if image.is_null() || image["request_id"] != p["frame_id"] {
        return Err(err(
            "conflict",
            "frame_id must reference the latest capture",
        ));
    }
    frame_fresh(image)?;
    number(image, "rotation").map_err(|_| {
        err(
            "conflict",
            "image orientation is unknown; input is unavailable",
        )
    })?;
    number(image, "width")?;
    number(image, "height")?;
    Ok(image)
}
fn verify_image(
    base: &Session,
    image: &Value,
    verify: &Path,
    evidence: Option<&Path>,
) -> Result<()> {
    frame_fresh(image)?;
    base.screenshot(verify).map_err(device)?;
    let (w, h) = png_dimensions(verify)?;
    let rotation = base.display_rotation().map_err(device)?;
    let matches = image["width"] == w && image["height"] == h && image["rotation"] == rotation;
    if let Some(path) = evidence {
        let value = json!({"schema":"phonefarm/gray-input-frame@1","source_image":image,
            "verification_image":"input-check.png","observed_at":now(),
            "observed":{"width":w,"height":h,"rotation":rotation},"geometry_matches":matches});
        crate::confirmation_transport::new_file(path, &serde_json::to_vec_pretty(&value).unwrap())
            .map_err(device)?;
    }
    if !matches {
        return Err(err(
            "conflict",
            "display geometry changed; capture again before input",
        ));
    }
    frame_fresh(image)
}
fn check_frame(store: &Store, base: &Session, d: &Value, r: &Value) -> Result<()> {
    let p = &r["params"];
    let image = input_image(d, p)?;
    let width = number(image, "width")?;
    let height = number(image, "height")?;
    let points = if r["action"] == "tap" {
        vec![(number(p, "x")?, number(p, "y")?)]
    } else {
        vec![
            (number(p, "x1")?, number(p, "y1")?),
            (number(p, "x2")?, number(p, "y2")?),
        ]
    };
    if points.iter().any(|(x, y)| *x >= width || *y >= height) {
        return Err(err(
            "invalid_request",
            "coordinates are outside the captured image",
        ));
    }
    let verify = store.path.join(format!(
        "{CONTROL}/artifacts/input-check-{}.png",
        string(r, "request_id")?
    ));
    verify_image(base, image, &verify, None)
}

fn close_mailbox(store: &Store, d: &mut Value) -> Result<()> {
    let _publisher = store.lock("publisher", Duration::from_secs(2))?;
    d["accepting"] = json!(false);
    d["allowed_actions"] = json!([]);
    d["phase"] = json!("restoring");
    save_descriptor(store, d)?;
    for e in store.requests()? {
        let id = string(&e["request"], "request_id")?;
        if store.optional(&receipt_path(id, "results"))?.is_some()
            || store.optional(&receipt_path(id, "started"))?.is_some()
        {
            continue;
        }
        let mut result = initial_receipt(&e);
        result["status"] = json!("not_executed");
        result["finished_at"] = json!(now());
        result["error"] = json!("session stopped before executing this request");
        store.write(&receipt_path(id, "results"), &result, false)?;
    }
    Ok(())
}
fn public_text(base: &Session, message: &str) -> String {
    let identity = base.control_identity().unwrap_or(Value::Null);
    let mut result = message.to_owned();
    if let Some(token) = identity["token"].as_str() {
        if !token.is_empty() {
            result = result.replace(token, "[lease]");
        }
    }
    if let Some(path) = base.dir.to_str() {
        result = result.replace(
            path,
            base.dir
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("[session]"),
        );
    }
    result
}
fn public_recovery(
    store: &Store,
    base: &Session,
    d: &Value,
    report: std::result::Result<Value, String>,
) -> Value {
    let mut reports = d["recovery"]["reports"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    match report {
        Ok(report) => {
            let done = report["status"] == "reported_complete";
            let path = report["report_path"]
                .as_str()
                .and_then(|p| Path::new(p).strip_prefix(&store.path).ok())
                .map(|p| p.to_string_lossy().into_owned());
            if let Some(path) = path {
                let value = json!(path);
                if !reports.contains(&value) {
                    reports.push(value);
                }
            }
            json!({"status":if done{"reported_complete"}else{"required"},"session_path":d["session_path"],"scope":report["scope"],"reports":reports,"error":report["error"].as_str().map(|e|public_text(base,e)),"game_state_restored":false,"job_recovery_complete":false})
        }
        Err(error) => {
            json!({"status":"required","session_path":d["session_path"],"reports":reports,"error":public_text(base,&error),"game_state_restored":false,"job_recovery_complete":false})
        }
    }
}
fn finish(
    store: &Store,
    base: &Session,
    parent: Option<&Parent>,
    d: &mut Value,
    stop: Option<&Value>,
    failure: Option<&str>,
) -> Result<(Value, i32)> {
    let mailbox = close_mailbox(store, d);
    let failure = failure
        .map(str::to_owned)
        .or_else(|| mailbox.as_ref().err().map(|e| e.message.clone()));
    let report = if let Some(parent) = parent {
        parent.finish(failure.as_deref())
    } else {
        base.finish_control(failure.as_deref())
    };
    d["recovery"] = public_recovery(store, base, d, report);
    if let Some(parent) = parent {
        d["power"] = parent.control_power_state().map_err(device)?;
    }
    let done = d["recovery"]["status"] == "reported_complete";
    d["phase"] = json!(if done { "closed" } else { "recovery_required" });
    d["accepting"] = json!(false);
    d["allowed_actions"] = json!([]);
    if let Some(error) = failure.as_ref() {
        d["last_error"] = json!(error);
    }
    save_descriptor(store, d)?;
    if let Some(stop) = stop {
        let mut result = stop.clone();
        result["status"] = json!(if done { "completed" } else { "failed" });
        result["finished_at"] = json!(now());
        result["result"] = json!({"recovery":d["recovery"]});
        result["error"] = d["recovery"]["error"].clone();
        store.write(
            &receipt_path(string(stop, "request_id")?, "results"),
            &result,
            false,
        )?;
    }
    mailbox?;
    let mut output = d.clone();
    output["live_owner"] = json!(false);
    Ok((output, if done && failure.is_none() { 0 } else { 2 }))
}
fn recover(a: &Args) -> Result<(Value, i32)> {
    let session = a.session.as_ref().unwrap().canonicalize()?;
    let out = session
        .parent()
        .ok_or_else(|| err("invalid_request", "session has no out parent"))?;
    let store = Store::open(out)?;
    let mut d = descriptor(&store)?;
    if session != store.path.join(string(&d, "session_path")?)
        || d["serial"] != a.serial.as_deref().unwrap()
    {
        return Err(err("conflict", "recovery serial/context mismatch"));
    }
    let _owner = store.lock("owner", Duration::ZERO).map_err(|e| {
        if e.code == "conflict" {
            err(
                "owner_unavailable",
                "live owner must receive stop instead of recover",
            )
        } else {
            e
        }
    })?;
    if d["phase"] == "closed" {
        let mut result = d;
        result["live_owner"] = json!(false);
        return Ok((result, 0));
    }
    let base = Session::resume_control(&session, a.serial.as_ref().unwrap()).map_err(device)?;
    d["stop_reason"] = json!("recovery_requested");
    if base.control_has_resources().map_err(device)? {
        let parent = Parent::resume_control(base).map_err(device)?;
        finish(&store, &parent.base, Some(&parent), &mut d, None, None)
    } else {
        finish(&store, &base, None, &mut d, None, None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let p = std::env::temp_dir().join(format!(
                "gray-control-{}-{}",
                std::process::id(),
                TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&p).unwrap();
            Self(p)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn fixture() -> (Fixture, Store, Value) {
        let temp = Fixture::new();
        let store = Store::open(&temp.0).unwrap();
        create_control(&store).unwrap();
        fs::create_dir(store.path.join("gray-session-test")).unwrap();
        store.write("gray-session-test/session.json",&json!({"schema":"phonefarm/gray-capture-session@2","control_session_id":"gray-session-test","completion_owner":"gray_layer_control","serial":"fake","package":"com.example.fixture"}),false).unwrap();
        let d = json!({"schema":SCHEMA,"producer":"phonefarm","session_id":"gray-session-test","session_path":"gray-session-test","job_id":null,"serial":"fake","package":"com.example.fixture","accepting":true,"phase":"ready","controller":{"actor_id":"agent","kind":"agent","epoch":1,"expires_at":null}});
        store.write(DESCRIPTOR, &d, false).unwrap();
        (temp, store, d)
    }
    fn request(id: &str) -> Value {
        json!({"schema":ACTION,"request_id":id,"session_id":"gray-session-test","actor_id":"agent","control_epoch":1,"action":"capture","params":{}})
    }
    #[test]
    fn mailbox_is_idempotent_ordered_and_does_not_replay_started() {
        if crate::isolate_fd_test("gray_control::tests::mailbox_is_idempotent_ordered_and_does_not_replay_started") { return; }
        let (_temp, store, d) = fixture();
        let owner = store.lock("owner", Duration::ZERO).unwrap();
        let r = request("same");
        assert_eq!(publish(&store, &r).unwrap()["status"], "accepted");
        assert_eq!(publish(&store, &r).unwrap()["sequence"], 1);
        let mut quality = request("quality");
        quality["action"] = json!("quality");
        quality["params"] = json!({"op":"begin","label":"comparison_1"});
        assert_eq!(
            publish(&store, &quality).unwrap_err().code,
            "invalid_request"
        );
        assert_eq!(store.requests().unwrap().len(), 1);
        let mut quality_owner = d.clone();
        quality_owner["allowed_actions"] = json!(["quality"]);
        quality_owner["action_capabilities"] = json!({"quality":1});
        quality_capability(&quality_owner, &quality).unwrap();
        validate_request(&quality).unwrap();
        quality["params"]["label"] = json!("../outside");
        assert!(validate_request(&quality).is_err());
        // Old C1 descriptors remain readable; the newer helper must not enqueue
        // unsupported sample requests into their live mailbox.
        let mut sample = request("sample");
        sample["action"] = json!("sample");
        sample["params"] = json!({"seconds":1});
        assert_eq!(
            publish(&store, &sample).unwrap_err().code,
            "invalid_request"
        );
        let mut input_request = request("finite");
        input_request["action"] = json!("sample");
        input_request["params"] =
            json!({"seconds":1,"frame_id":"frame-1","input":[{"action":"sleep","ms":11000}]});
        validate_request(&input_request).unwrap();
        let mut old = d.clone();
        old["allowed_actions"] = json!(["sample"]);
        store.write(DESCRIPTOR, &old, true).unwrap();
        assert_eq!(
            publish(&store, &input_request).unwrap_err().code,
            "invalid_request"
        );
        assert_eq!(store.requests().unwrap().len(), 1);
        let mut invalid = input_request.clone();
        invalid["params"]
            .as_object_mut()
            .unwrap()
            .remove("frame_id");
        assert!(validate_request(&invalid).is_err());
        invalid = input_request.clone();
        invalid["params"]["input"] = Value::Null;
        assert!(validate_request(&invalid).is_err());
        invalid = input_request.clone();
        invalid["params"].as_object_mut().unwrap().remove("input");
        assert!(validate_request(&invalid).is_err());
        invalid = input_request.clone();
        invalid["params"]["input"][0]["ms"] = json!(10000);
        assert!(validate_request(&invalid).is_err());
        let mut spoofed = old.clone();
        spoofed["action_capabilities"] = json!({"sample":2});
        store.write(DESCRIPTOR, &spoofed, true).unwrap();
        assert!(descriptor(&store).is_err());
        store.write(DESCRIPTOR, &d, true).unwrap();
        let mut manifest = store.read("gray-session-test/session.json").unwrap();
        manifest["schema"] = json!("phonefarm/gray-capture-session@3");
        manifest["control_resources"] =
            json!({"path":"eval-session.json","schema":crate::gray_session::CONTROL_PARENT_SCHEMA});
        store
            .write("gray-session-test/session.json", &manifest, true)
            .unwrap();
        assert!(descriptor(&store).is_err()); // No parent: never fall back to C1.
        manifest["control_resources"]["schema"] =
            json!(crate::gray_session::LEGACY_CONTROL_PARENT_SCHEMA);
        store
            .write("gray-session-test/session.json", &manifest, true)
            .unwrap();
        store
            .write(
                "gray-session-test/eval-session.json",
                &json!({"schema":crate::gray_session::LEGACY_CONTROL_PARENT_SCHEMA,
            "identity":{"serial":"fake","completion_owner":"gray_layer_control"}}),
                false,
            )
            .unwrap();
        store.write(DESCRIPTOR, &old, true).unwrap();
        assert!(descriptor(&store).is_ok()); // Original C2A remains readable.
        assert!(publish(&store, &input_request).is_err());
        store.write(DESCRIPTOR, &spoofed, true).unwrap();
        assert!(descriptor(&store).is_err()); // Capability cannot upgrade an old parent.
        store.write(DESCRIPTOR, &d, true).unwrap();
        manifest["schema"] = json!("phonefarm/gray-capture-session@2");
        store
            .write("gray-session-test/session.json", &manifest, true)
            .unwrap();
        assert!(descriptor(&store).is_ok());
        let mut changed = r.clone();
        changed["action"] = json!("marker");
        assert_eq!(publish(&store, &changed).unwrap_err().code, "conflict");
        store
            .write(
                "gray-control/publisher.json",
                &json!({"next_sequence":8}),
                true,
            )
            .unwrap();
        assert_eq!(publish(&store, &request("later")).unwrap()["sequence"], 8);
        assert_eq!(store.requests().unwrap().len(), 2);
        let mut started = receipt(&store, &d, "same").unwrap();
        started["status"] = json!("started");
        started["started_at"] = json!(now());
        store
            .write(&receipt_path("same", "started"), &started, false)
            .unwrap();
        drop(owner);
        assert_eq!(receipt(&store, &d, "same").unwrap()["status"], "unknown");
        assert_eq!(
            receipt(&store, &d, "later").unwrap()["status"],
            "not_executed"
        );
        assert_eq!(publish(&store, &r).unwrap()["status"], "unknown");
        assert_eq!(
            publish(&store, &request("new")).unwrap_err().code,
            "owner_unavailable"
        );
    }
    #[test]
    fn control_epoch_and_image_freshness_reject_stale_input() {
        let mut d = json!({"controller":{"actor_id":"human","kind":"human","epoch":2,"expires_at":(chrono::Utc::now()-chrono::Duration::seconds(1)).to_rfc3339()}});
        let r = request("tap");
        assert_eq!(require_controller(&d, &r).unwrap_err().code, "conflict");
        assert!(expire_control(&mut d, "agent").unwrap());
        assert_eq!(d["controller"]["epoch"], 3);
        assert_eq!(d["controller"]["actor_id"], "agent");
        let mut current = r;
        current["control_epoch"] = json!(3);
        assert!(require_controller(&d, &current).is_ok());
        let plan =
            crate::gray_script::Plan::control(Some(&json!([{"action":"sleep","ms":30000}])), 1)
                .unwrap();
        assert!(sample_budget(Instant::now() + Duration::from_secs(20), &plan).is_err());
        assert!(sample_budget(Instant::now() + Duration::from_secs(31), &plan).is_ok());
        assert!(frame_fresh(&json!({"captured_at":now()})).is_ok());
        assert!(frame_fresh(
            &json!({"captured_at":(chrono::Utc::now()-chrono::Duration::seconds(31)).to_rfc3339()})
        )
        .is_err());

        // Quality records preserve failed attempts without advancing the stage,
        // and use the current actor/epoch independently at each checkpoint.
        let path = "gray-control/quality/example/quality.json";
        let mut quality = crate::gray_quality::new_manifest(&d, "example", path, &now());
        let mut stage = current;
        stage["action"] = json!("quality");
        stage["params"] = json!({"op":"begin","label":"example"});
        crate::gray_quality::start_attempt(&mut quality, &stage, "attempt-bad.json", &now())
            .unwrap();
        let failed = crate::gray_quality::finish_attempt(
            &mut quality,
            Some(json!({"message":"bad PNG"})),
            &now(),
        )
        .unwrap();
        assert!(quality["phase"].is_null());
        assert_eq!(failed["status"], "failed");
        stage["request_id"] = json!("begin-retry");
        crate::gray_quality::start_attempt(&mut quality, &stage, "attempt-begin.json", &now())
            .unwrap();
        quality["active_attempt"]["image"] = json!({"path":"original.png"});
        quality["active_attempt"]["preview"] = json!({"path":"preview-half.png"});
        let begin = crate::gray_quality::finish_attempt(&mut quality, None, &now()).unwrap();
        assert!(
            crate::gray_quality::start_attempt(&mut quality, &stage, "duplicate.json", &now())
                .is_err()
        );
        stage["params"]["op"] = json!("finish");
        assert!(
            crate::gray_quality::start_attempt(&mut quality, &stage, "too-early.json", &now())
                .is_err()
        );
        stage["params"]["op"] = json!("checkpoint");
        stage["request_id"] = json!("checkpoint-human");
        d["controller"] = json!({"actor_id":"human","kind":"human","epoch":4});
        assert!(require_controller(&d, &stage).is_err());
        stage["actor_id"] = json!("human");
        stage["control_epoch"] = json!(4);
        require_controller(&d, &stage).unwrap();
        crate::gray_quality::start_attempt(&mut quality, &stage, "attempt-checkpoint.json", &now())
            .unwrap();
        quality["active_attempt"]["image"] = json!({"path":"checkpoint.png"});
        quality["active_attempt"]["preview"] = json!({"path":"checkpoint-half.png"});
        crate::gray_quality::finish_attempt(&mut quality, None, &now()).unwrap();
        stage["params"]["op"] = json!("finish");
        stage["request_id"] = json!("finish-missing-marker");
        crate::gray_quality::start_attempt(&mut quality, &stage, "attempt-failed.json", &now())
            .unwrap();
        quality["active_attempt"]["image"] = json!({"path":"failed-original.png"});
        quality["active_attempt"]["preview"] = json!({"path":"failed-half.png"});
        assert!(crate::gray_quality::finish_attempt(&mut quality, None, &now()).is_err());
        let failed_finish = crate::gray_quality::finish_attempt(
            &mut quality,
            Some(json!({"message":"missing marker"})),
            &now(),
        )
        .unwrap();
        assert_eq!(quality["phase"], "checkpointed");
        assert_eq!(failed_finish["image"]["path"], "failed-original.png");
        assert_eq!(quality["stages"]["begin"], begin);
        let marker = json!({"layer_loaded":true,"pkg":"com.example.fixture","readonly_stats":{"frames":10,"render_pass_begins":300}});
        crate::gray_quality::validate_marker(&marker, "com.example.fixture").unwrap();
        assert!(crate::gray_quality::validate_marker(&marker, "other.package").is_err());
        assert!(crate::gray_quality::validate_marker(
            &json!({"frames":10,"render_pass_begins":300}),
            "com.example.fixture"
        )
        .is_err());
        stage["request_id"] = json!("finish-retry");
        crate::gray_quality::start_attempt(&mut quality, &stage, "attempt-finish.json", &now())
            .unwrap();
        quality["active_attempt"]["image"] = json!({"path":"finish.png"});
        quality["active_attempt"]["preview"] = json!({"path":"finish-half.png"});
        quality["active_attempt"]["marker"] = json!({"path":"marker.json"});
        crate::gray_quality::finish_attempt(&mut quality, None, &now()).unwrap();
        assert_eq!(quality["phase"], "collected");
        crate::gray_quality::validate(&quality, &d, "example", path).unwrap();
        assert!(crate::gray_quality::start_attempt(
            &mut quality,
            &stage,
            "duplicate-finish.json",
            &now()
        )
        .is_err());
    }
}
