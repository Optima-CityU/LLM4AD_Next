//! Declared device identities only. Registration never changes execution scope.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::fs::{self, OpenOptions};
use std::io::Read;
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

const SCHEMA: &str = "phonefarm/device-registry@1";
const CURRENT: &str = "device-registry.json";
const LIMIT: u64 = 64 * 1024;
const MAX_REVISION: u64 = (1_u64 << 53) - 1;
const BINDING_SCHEMA: &str = "phonefarm/device-binding@1";
pub(crate) const DISABLED_DEVICE: &str = "这台手机在登记里停用了；要用请先在登记里启用";

#[derive(Debug)]
struct Error {
    code: &'static str,
    message: String,
}
type Result<T> = std::result::Result<T, Error>;
fn error(code: &'static str, message: impl Into<String>) -> Error {
    Error {
        code,
        message: message.into(),
    }
}
fn unavailable(message: impl ToString) -> Error {
    error("unavailable", message.to_string())
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Device {
    pub device_id: String,
    pub label: String,
    pub aliases: Vec<String>,
    pub enabled: bool,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub(crate) struct DeviceBinding {
    pub schema: String,
    pub registry_revision: u64,
    pub device_id: String,
    pub serial: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    expected_revision: u64,
    device: Device,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Registry {
    schema: String,
    revision: u64,
    devices: Vec<Device>,
    updated_at: String,
    identity_basis: String,
    physical_identity_verified: bool,
    execution_scope: String,
}

pub(crate) fn valid_device_id(value: &str) -> std::result::Result<(), String> {
    let id = value.as_bytes();
    if id.is_empty()
        || id.len() > 64
        || !id[0].is_ascii_alphanumeric()
        || !id
            .iter()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-'))
    {
        return Err("device_id must be 1..64 ASCII letters/digits/underscore/hyphen, starting with a letter or digit".into());
    }
    Ok(())
}
fn valid_serial(serial: &str) -> std::result::Result<(), String> {
    if serial.is_empty()
        || serial.len() > 256
        || serial.chars().any(|c| c.is_whitespace() || c.is_control())
    {
        return Err(
            "serial must contain 1..256 bytes without whitespace or control characters".into(),
        );
    }
    Ok(())
}
fn valid_device(device: &Device) -> std::result::Result<(), String> {
    valid_device_id(&device.device_id)?;
    if !(1..=128).contains(&device.label.chars().count())
        || device.label.chars().any(char::is_control)
    {
        return Err("label must contain 1..128 characters without control characters".into());
    }
    if !(1..=32).contains(&device.aliases.len()) {
        return Err("aliases must contain 1..32 serial strings".into());
    }
    let mut seen = HashSet::new();
    for alias in &device.aliases {
        valid_serial(alias)?;
        if !seen.insert(alias) {
            return Err("aliases must not repeat within one device".into());
        }
    }
    Ok(())
}
fn unique_aliases(devices: &[Device]) -> std::result::Result<(), String> {
    let mut seen = HashSet::new();
    for device in devices {
        for alias in &device.aliases {
            if !seen.insert(alias) {
                return Err("an alias is already assigned to another registered device (including disabled devices)".into());
            }
        }
    }
    Ok(())
}
fn parse_request(bytes: &[u8]) -> Result<Request> {
    if bytes.len() as u64 > LIMIT {
        return Err(error("invalid_request", "request exceeds 64 KiB"));
    }
    let request: Request = serde_json::from_slice(bytes)
        .map_err(|e| error("invalid_request", format!("invalid registry request: {e}")))?;
    if request.expected_revision > MAX_REVISION {
        return Err(error("invalid_request", "expected_revision exceeds 2^53-1"));
    }
    valid_device(&request.device).map_err(|e| error("invalid_request", e))?;
    Ok(request)
}
fn parse_registry(bytes: &[u8]) -> Result<Registry> {
    let registry: Registry = serde_json::from_slice(bytes)
        .map_err(|e| unavailable(format!("invalid registry record: {e}")))?;
    if registry.schema != SCHEMA
        || !(1..=MAX_REVISION).contains(&registry.revision)
        || registry.identity_basis != "operator_declared"
        || registry.physical_identity_verified
        || registry.execution_scope != "global"
        || registry.devices.is_empty()
        || registry.devices.len() > 128
        || chrono::DateTime::parse_from_rfc3339(&registry.updated_at).is_err()
    {
        return Err(unavailable("registry envelope is invalid or unsupported"));
    }
    for device in &registry.devices {
        valid_device(device).map_err(unavailable)?;
    }
    if registry
        .devices
        .windows(2)
        .any(|p| p[0].device_id >= p[1].device_id)
    {
        return Err(unavailable(
            "registry device IDs are duplicated or not sorted",
        ));
    }
    unique_aliases(&registry.devices).map_err(unavailable)?;
    Ok(registry)
}
fn exists(path: &Path) -> Result<bool> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(e) => Err(unavailable(e)),
    }
}
fn check_directory(path: &Path) -> Result<()> {
    if !fs::symlink_metadata(path).map_err(unavailable)?.is_dir() {
        return Err(unavailable(format!(
            "not a regular directory: {}",
            path.display()
        )));
    }
    Ok(())
}
fn read_bounded(path: &Path) -> Result<Option<Vec<u8>>> {
    if !exists(path)? {
        return Ok(None);
    }

    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC)
        .open(path)
        .map_err(unavailable)?;
    let metadata = file.metadata().map_err(unavailable)?;
    if !metadata.is_file() || metadata.len() > LIMIT {
        return Err(unavailable(format!(
            "not a bounded regular record: {}",
            path.display()
        )));
    }
    let mut bytes = Vec::new();
    file.take(LIMIT + 1)
        .read_to_end(&mut bytes)
        .map_err(unavailable)?;
    if bytes.len() as u64 > LIMIT {
        return Err(unavailable("record exceeds 64 KiB"));
    }
    Ok(Some(bytes))
}
fn read_current(root: &Path) -> Result<Option<Registry>> {
    if exists(root)? {
        check_directory(root)?;
    }
    read_bounded(&root.join(CURRENT))?
        .map(|bytes| parse_registry(&bytes))
        .transpose()
}
pub(crate) fn snapshot(root: &Path) -> std::result::Result<(u64, Vec<Device>), String> {
    Ok(read_current(root).map_err(|e| e.message)?.map_or_else(
        || (0, Vec::new()),
        |registry| (registry.revision, registry.devices),
    ))
}
fn valid_binding(binding: &DeviceBinding) -> std::result::Result<(), String> {
    if binding.schema != BINDING_SCHEMA || !(1..=MAX_REVISION).contains(&binding.registry_revision)
    {
        return Err("unsupported or invalid device binding identity".into());
    }
    valid_device_id(&binding.device_id)?;
    valid_serial(&binding.serial)
}
pub(crate) fn parse_binding(raw: &str) -> std::result::Result<DeviceBinding, String> {
    if raw.len() > 4096 {
        return Err("device binding exceeds 4 KiB".into());
    }
    let binding =
        serde_json::from_str(raw).map_err(|e| format!("invalid device binding JSON: {e}"))?;
    valid_binding(&binding)?;
    Ok(binding)
}
pub(crate) fn inherited_binding() -> std::result::Result<Option<DeviceBinding>, String> {
    match std::env::var("PF_JOB_DEVICE_BINDING") {
        Ok(value) => Ok(Some(parse_binding(&value)?)),
        Err(std::env::VarError::NotPresent) => Ok(None),
        Err(_) => Err("PF_JOB_DEVICE_BINDING is not UTF-8".into()),
    }
}

pub(crate) fn resolve_binding(
    storage: &crate::lease_storage::Storage,
    serial: &str,
) -> std::result::Result<DeviceBinding, String> {
    valid_serial(serial)?;
    let (_, root) = storage.roots()?;
    let (revision, devices) = snapshot(&root)?;
    let device = devices
        .iter()
        .find(|d| d.aliases.iter().any(|alias| alias == serial))
        .ok_or("serial is not a registered device alias")?;
    if !device.enabled {
        return Err(DISABLED_DEVICE.into());
    }
    Ok(DeviceBinding {
        schema: BINDING_SCHEMA.into(),
        registry_revision: revision,
        device_id: device.device_id.clone(),
        serial: serial.into(),
    })
}
fn empty() -> Value {
    json!({"schema":SCHEMA,"revision":0,"devices":[],"updated_at":null,
        "identity_basis":"operator_declared","physical_identity_verified":false,"execution_scope":"global"})
}
fn get(root: &Path) -> Result<Value> {
    match read_current(root)? {
        Some(registry) => serde_json::to_value(registry).map_err(unavailable),
        None => Ok(empty()),
    }
}

fn update_locked(root: &Path, request: Request) -> Result<Value> {
    let current = read_current(root)?;
    let revision = current.as_ref().map_or(0, |r| r.revision);
    if request.expected_revision != revision {
        return Err(error("conflict", format!("expected_revision does not match current revision {revision}; read the registry before any new submission")));
    }
    let mut devices = current
        .as_ref()
        .map_or_else(Vec::new, |r| r.devices.clone());
    match devices.binary_search_by(|d| d.device_id.cmp(&request.device.device_id)) {
        Ok(index) if devices[index] == request.device => {
            return serde_json::to_value(current.as_ref().unwrap()).map_err(unavailable)
        }
        Ok(index) => devices[index] = request.device,
        Err(index) if devices.len() < 128 => devices.insert(index, request.device),
        Err(_) => {
            return Err(error(
                "invalid_request",
                "registry is limited to 128 devices",
            ))
        }
    }
    unique_aliases(&devices).map_err(|e| error("conflict", e))?;
    if revision == MAX_REVISION {
        return Err(error("conflict", "registry revision is exhausted"));
    }
    let next = Registry {
        schema: SCHEMA.into(),
        revision: revision + 1,
        devices,
        updated_at: chrono::Utc::now().to_rfc3339(),
        identity_basis: "operator_declared".into(),
        physical_identity_verified: false,
        execution_scope: "global".into(),
    };
    let mut bytes = serde_json::to_vec(&next).map_err(unavailable)?;
    bytes.push(b'\n');
    if bytes.len() as u64 > LIMIT {
        return Err(error(
            "invalid_request",
            "resulting registry exceeds 64 KiB",
        ));
    }
    crate::devlease::durable_replace(&root.join(CURRENT), &bytes).map_err(unavailable)?;
    serde_json::to_value(next).map_err(unavailable)
}
fn set(root: &Path, device_root: &Path, request: Request) -> Result<Value> {
    let _guard = crate::fd_lock::Guard::acquire(
        device_root,
        root,
        crate::fd_lock::Scope::global(),
        "device registry",
        None,
    )
    .map_err(unavailable)?;
    update_locked(root, request)
}
fn request_bytes(path: &str) -> Result<Vec<u8>> {
    if path != "-" {
        return read_bounded(Path::new(path))
            .map_err(|e| error("invalid_request", e.message))?
            .ok_or_else(|| error("invalid_request", "request file is missing"));
    }
    let mut bytes = Vec::new();
    std::io::stdin()
        .lock()
        .take(LIMIT + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| error("invalid_request", e.to_string()))?;
    Ok(bytes)
}
fn run(args: &[String]) -> Result<Value> {
    if args.first().map(String::as_str) == Some("resolve") {
        let mut serial = None;
        let mut json = false;
        let mut it = args[1..].iter();
        while let Some(arg) = it.next() {
            match arg.as_str() {
                "--serial" if serial.is_none() => {
                    serial =
                        Some(it.next().ok_or_else(|| {
                            error("invalid_request", "--serial requires a value")
                        })?);
                }
                "--json" if !json => json = true,
                _ => {
                    return Err(error(
                        "invalid_request",
                        "expected lease resolve --serial S --json",
                    ))
                }
            }
        }
        let serial = serial.filter(|_| json).ok_or_else(|| {
            error(
                "invalid_request",
                "expected lease resolve --serial S --json",
            )
        })?;
        valid_serial(serial).map_err(|e| error("invalid_request", e))?;
        return serde_json::to_value(
            resolve_binding(&crate::lease_storage::resolve(), serial).map_err(unavailable)?,
        )
        .map_err(unavailable);
    }
    let request = match args.first().map(String::as_str) {
        Some("devices") if args.len() == 2 && args[1] == "--json" => None,
        Some("devices-set") => {
            let mut request = None;
            let mut json = false;
            let mut it = args[1..].iter();
            while let Some(arg) = it.next() {
                match arg.as_str() {
                    "--request" if request.is_none() => {
                        request = Some(
                            it.next()
                                .ok_or_else(|| {
                                    error("invalid_request", "--request requires FILE or -")
                                })?
                                .as_str(),
                        )
                    }
                    "--json" if !json => json = true,
                    _ => {
                        return Err(error(
                            "invalid_request",
                            "expected lease devices-set --request FILE|- --json",
                        ))
                    }
                }
            }
            if !json {
                return Err(error("invalid_request", "devices-set requires --json"));
            }
            Some(parse_request(&request_bytes(request.ok_or_else(
                || error("invalid_request", "devices-set requires --request FILE|-"),
            )?)?)?)
        }
        _ => {
            return Err(error(
                "invalid_request",
                "expected lease devices --json or lease devices-set --request FILE|- --json",
            ))
        }
    };
    let (devices, root) = crate::lease_storage::resolve()
        .roots()
        .map_err(unavailable)?;
    match request {
        Some(request) => set(&root, &devices, request),
        None => get(&root),
    }
}
pub(crate) fn run_cli(args: &[String]) -> i32 {
    match run(args) {
        Ok(value) => {
            println!("{value}");
            0
        }
        Err(e) => {
            println!(
                "{}",
                json!({"schema":"phonefarm/device-registry-error@1","code":e.code,"error":e.message})
            );
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    fn set(root: &Path, request: Request) -> Result<Value> {
        super::set(root, &root.join("devices"), request)
    }
    struct Scratch(PathBuf);
    impl Scratch {
        fn new() -> Self {
            let stamp = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            Self(
                std::env::temp_dir()
                    .join(format!("pf-device-registry-{}-{stamp}", std::process::id())),
            )
        }
    }
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn request(revision: u64, id: &str, aliases: &[&str], enabled: bool) -> Request {
        parse_request(
            &serde_json::to_vec(&json!({"expected_revision":revision,
            "device":{"device_id":id,"label":id,"aliases":aliases,"enabled":enabled}}))
            .unwrap(),
        )
        .unwrap()
    }
    #[test]
    fn original_binding_acquires_after_unrelated_update_and_recovers_without_history() {
        if crate::isolate_fd_test("device_registry::tests::original_binding_acquires_after_unrelated_update_and_recovers_without_history") { return; }
        let s = Scratch::new();
        set(&s.0, request(0, "A", &["usb-A", "wifi-A"], true)).unwrap();
        let storage = crate::lease_storage::Storage::custom(&s.0);
        let binding = resolve_binding(&storage, "usb-A").unwrap();
        let original = serde_json::to_vec(&binding).unwrap();
        set(&s.0, request(1, "B", &["usb-B"], true)).unwrap();
        let lease = crate::devlease::Lease::acquire_device(
            &storage,
            &binding,
            "test",
            std::time::Duration::ZERO,
        )
        .unwrap();
        assert_eq!(lease.serial, "usb-A");
        assert_eq!(lease.device_binding(), Some(&binding));
        drop(lease);
        set(&s.0, request(2, "A", &["usb-A", "wifi-A"], false)).unwrap();
        assert!(
            crate::devlease::Lease::acquire_device(
                &storage,
                &binding,
                "test",
                std::time::Duration::ZERO,
            )
            .err().unwrap().contains(DISABLED_DEVICE),
        );
        let history = s.0.join("device-registry-history");
        if history.exists() {
            fs::remove_dir_all(history).unwrap();
        }
        let recovery = crate::devlease::Lease::acquire_device_recovery(
            &storage,
            &binding,
            &s.0,
            std::time::Duration::ZERO,
        )
        .unwrap();
        assert_eq!(recovery.serial, "usb-A");
        assert_eq!(
            serde_json::to_vec(recovery.device_binding().unwrap()).unwrap(),
            original
        );
        assert_eq!(snapshot(&s.0).unwrap().0, 3);
    }
    #[test]
    fn disabled_device_still_allows_serial_recovery() {
        if crate::isolate_fd_test("device_registry::tests::disabled_device_still_allows_serial_recovery") { return; }
        let s = Scratch::new();
        set(&s.0, request(0, "A", &["usb-A", "wifi-A"], false)).unwrap();
        let storage = crate::lease_storage::Storage::custom(&s.0);
        let (root, farm) = storage.roots().unwrap();
        assert!(crate::devlease::Lease::acquire(&root, &farm, "usb-A", "test", std::time::Duration::ZERO)
            .err().unwrap().contains(DISABLED_DEVICE));
        std::env::set_var("DEVLOCK_DIR", &farm);
        std::env::set_var("PHONEFARM_LEASE_DIR", &root);
        assert!(crate::devlease::Lease::target(Some("usb-A"), "test")
            .err().unwrap().contains(DISABLED_DEVICE));
        let recovery = crate::devlease::Lease::acquire_recovery(
            &root, &farm, "usb-A", &s.0, std::time::Duration::ZERO).unwrap();
        assert_eq!(recovery.serial, "usb-A");
        assert_eq!(recovery.device_binding().unwrap().device_id, "A");
        assert_eq!(snapshot(&s.0).unwrap().0, 1);
    }
    #[test]
    fn binding_resolves_aliases_and_keeps_basic_format_checks() {
        if crate::isolate_fd_test(
            "device_registry::tests::binding_resolves_aliases_and_keeps_basic_format_checks",
        ) {
            return;
        }
        let s = Scratch::new();
        set(&s.0, request(0, "A", &["usb-A", "192.0.2.1:5555"], true)).unwrap();
        let storage = crate::lease_storage::Storage::custom(&s.0);
        let usb = resolve_binding(&storage, "usb-A").unwrap();
        let wifi = resolve_binding(&storage, "192.0.2.1:5555").unwrap();
        assert_eq!(usb.device_id, wifi.device_id);
        assert_eq!(snapshot(&s.0).unwrap().0, usb.registry_revision);
        let original = serde_json::to_string(&usb).unwrap();
        assert_eq!(parse_binding(&original).unwrap(), usb);
        assert!(parse_binding(&(original.clone() + &" ".repeat(4096))).is_err());
        let mut historical = serde_json::to_value(&usb).unwrap();
        historical["domain_id"] = json!("a".repeat(32));
        assert_eq!(parse_binding(&historical.to_string()).unwrap(), usb);
        for (key, value) in [
            ("schema", json!("wrong")),
            ("device_id", json!("../bad")),
            ("serial", json!("a b")),
        ] {
            let mut malformed = historical.clone();
            malformed[key] = value;
            assert!(parse_binding(&malformed.to_string()).is_err());
        }
        set(&s.0, request(1, "A", &["usb-A", "192.0.2.1:5555"], false)).unwrap();
        assert!(resolve_binding(&storage, "usb-A").is_err());
        assert_eq!(get(&s.0).unwrap()["execution_scope"], "global");
    }
    #[test]
    fn registry_cas_and_disabled_aliases() {
        if crate::isolate_fd_test("device_registry::tests::registry_cas_and_disabled_aliases") {
            return;
        }
        let scratch = Scratch::new();
        assert_eq!(get(&scratch.0).unwrap()["revision"], 0);
        assert!(!scratch.0.exists());
        let first = set(
            &scratch.0,
            request(0, "A", &["usb-A", "192.0.2.1:5555"], true),
        )
        .unwrap();
        let bytes = fs::read(scratch.0.join(CURRENT)).unwrap();
        assert_eq!(
            set(
                &scratch.0,
                request(1, "A", &["usb-A", "192.0.2.1:5555"], true)
            )
            .unwrap(),
            first
        );
        for r in [
            request(0, "B", &["usb-B"], true),
            request(1, "B", &["usb-A"], true),
        ] {
            assert_eq!(set(&scratch.0, r).unwrap_err().code, "conflict");
        }
        assert_eq!(fs::read(scratch.0.join(CURRENT)).unwrap(), bytes);
        set(
            &scratch.0,
            request(1, "A", &["usb-A", "192.0.2.1:5555"], false),
        )
        .unwrap();
        assert_eq!(
            set(&scratch.0, request(2, "B", &["usb-A"], true))
                .unwrap_err()
                .code,
            "conflict"
        );
        set(&scratch.0, request(2, "A", &["192.0.2.1:5555"], false)).unwrap();
        let final_value = set(&scratch.0, request(3, "B", &["usb-A"], true)).unwrap();
        assert_eq!(final_value["revision"], 4);
        assert_eq!(final_value["execution_scope"], "global");
        assert_eq!(final_value["physical_identity_verified"], false);
        assert_eq!(
            set(&scratch.0, request(4, "B", &["usb-A"], false)).unwrap()["revision"],
            5
        );
        assert!(!scratch.0.join("device-registry-history").exists());
    }
    // Real process waiting/cancellation and ignored marker/queue records are
    // exercised in tests/fd_lock_protocol.rs.
    #[test]
    fn registry_rejects_invalid_input_but_ignores_old_archive() {
        if crate::isolate_fd_test(
            "device_registry::tests::registry_rejects_invalid_input_but_ignores_old_archive",
        ) {
            return;
        }
        for value in [
            json!({"expected_revision":0,"device":{"device_id":"../bad","label":"bad","aliases":["a"],"enabled":true}}),
            json!({"expected_revision":0,"device":{"device_id":"A","label":"a","aliases":["a","a"],"enabled":true}}),
            json!({"expected_revision":0,"device":{"device_id":"A","label":"a","aliases":["a b"],"enabled":true}}),
            json!({"expected_revision":0,"device":{"device_id":"A","label":"a","aliases":["a"],"enabled":true,"scope":"device"}}),
        ] {
            assert_eq!(
                parse_request(&serde_json::to_vec(&value).unwrap())
                    .err()
                    .unwrap()
                    .code,
                "invalid_request"
            );
        }
        let scratch = Scratch::new();
        set(&scratch.0, request(0, "A", &["usb-A"], true)).unwrap();
        let history = scratch.0.join("device-registry-history");
        fs::create_dir(&history).unwrap();
        fs::write(history.join("1.json"), b"incomplete old archive").unwrap();
        set(&scratch.0, request(1, "B", &["usb-B"], true)).unwrap();
        assert_eq!(get(&scratch.0).unwrap()["revision"], 2);
        assert_eq!(
            fs::read(history.join("1.json")).unwrap(),
            b"incomplete old archive"
        );
        fs::write(scratch.0.join(CURRENT), b"{}\n").unwrap();
        assert_eq!(get(&scratch.0).unwrap_err().code, "unavailable");
        assert_eq!(
            set(&scratch.0, request(2, "A", &["usb-A"], false))
                .unwrap_err()
                .code,
            "unavailable"
        );
        assert_eq!(fs::read(scratch.0.join(CURRENT)).unwrap(), b"{}\n");
    }
}
