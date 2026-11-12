//! Device ownership is a kernel fd. Recovery reports are experiment results, not locks.
use crate::device_registry::DeviceBinding;
use crate::fd_lock::{self, Guard, Scope};
use serde_json::{json, Value};
use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::time::Duration;

pub struct Lease {
    pub(crate) guard: Guard,
    token: String,
    receipt: Option<crate::lease_receipt::JobReceipt>,
    context: Option<PathBuf>,
    recovery_required: bool,
    pub serial: String,
}

pub(crate) fn resolve_scope(
    storage: &crate::lease_storage::Storage,
    serial: &str,
) -> Result<Scope, String> {
    let (_, root) = storage.roots()?;
    resolve_scope_at(&root, serial)
}
pub(crate) fn resolve_scope_at(farm: &Path, serial: &str) -> Result<Scope, String> {
    let (revision, devices) = crate::device_registry::snapshot(farm)?;
    let binding = devices
        .iter()
        .find(|device| device.aliases.iter().any(|alias| alias == serial))
        .map(|device| {
            DeviceBinding {
                schema: "phonefarm/device-binding@1".into(),
                registry_revision: revision,
                device_id: device.device_id.clone(),
                serial: serial.into(),
            }
        });
    let mut scope = Scope::device(serial, binding);
    scope.resolve_alias = true;
    Ok(scope)
}

pub(crate) fn wait_timeout() -> Result<Option<Duration>, String> {
    std::env::var("PHONEFARM_LEASE_TIMEOUT_MS").ok()
        .map(|value| value.parse::<u64>().map(Duration::from_millis)).transpose()
        .map_err(|e| format!("PHONEFARM_LEASE_TIMEOUT_MS: {e}"))
}

impl Lease {
    pub(crate) fn device_binding(&self) -> Option<&DeviceBinding> {
        self.guard.scope.binding.as_ref()
    }
    fn new(
        root: &Path,
        farm: &Path,
        scope: Scope,
        serial: &str,
        label: &str,
        timeout: Option<Duration>,
    ) -> Result<Self, String> {
        let guard = Guard::acquire(root, farm, scope, label, timeout)?;
        Self::from_guard(guard, serial, label)
    }
    pub(crate) fn from_guard(guard: Guard, serial: &str, label: &str) -> Result<Self, String> {
        let receipt = crate::lease_receipt::JobReceipt::from_env(serial, label, Value::Null)?;
        let lease = Self {
            guard,
            token: format!(
                "{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_nanos()
            ),
            receipt,
            context: None,
            recovery_required: false,
            serial: serial.into(),
        };
        lease.report("active")?;
        Ok(lease)
    }
    fn report(&self, state: &str) -> Result<(), String> {
        if let Some(receipt) = &self.receipt {
            receipt.record(
                self.device_binding(),
                self.context.as_deref(),
                state,
                self.recovery_required,
            )?;
        }
        Ok(())
    }
    pub fn acquire(
        root: &Path,
        farm: &Path,
        serial: &str,
        label: &str,
        timeout: impl Into<Option<Duration>>,
    ) -> Result<Self, String> {
        Self::new(
            root,
            farm,
            resolve_scope_at(farm, serial)?,
            serial,
            label,
            timeout.into(),
        )
    }
    pub(crate) fn acquire_device(
        storage: &crate::lease_storage::Storage,
        binding: &DeviceBinding,
        label: &str,
        timeout: impl Into<Option<Duration>>,
    ) -> Result<Self, String> {
        let (root, farm) = storage.roots()?;
        Self::new(
            &root,
            &farm,
            Scope::device(&binding.serial, Some(binding.clone())),
            &binding.serial,
            label,
            timeout.into(),
        )
    }
    pub fn target(requested: Option<&str>, label: &str) -> Result<Self, String> {
        let storage = crate::lease_storage::resolve();
        let (root, farm) = storage.roots()?;
        let binding = crate::device_registry::inherited_binding()?;
        let inherited_serial = fd_lock::inherited()?.and_then(|(scope, _)| scope.serial);
        let serial = crate::device::resolve_serial(
            requested.or(binding.as_ref().map(|b| b.serial.as_str())).or(inherited_serial.as_deref()),
        )
        .ok_or("cannot resolve exactly one device; pass --serial")?;
        let scope = resolve_scope(&storage, &serial)?;
        let timeout = wait_timeout()?;
        Self::new(&root, &farm, scope, &serial, label, timeout)
    }
    pub(crate) fn command(&self, command: &mut std::process::Command) -> Result<(), String> {
        self.guard.command(command)
    }
    pub(crate) fn hold_for_recovery(&mut self, context: &Path) -> Result<(), String> {
        self.context = Some(context.canonicalize().map_err(|e| e.to_string())?);
        self.recovery_required = true;
        self.report("active")
    }
    pub(crate) fn complete_recovery(
        &mut self,
        scope: &str,
        origin: &str,
        evidence: &[&Path],
    ) -> Result<(), String> {
        if scope.trim().is_empty()
            || origin.trim().is_empty()
            || evidence.is_empty()
            || evidence.iter().any(|p| !p.is_absolute() || !p.is_file())
        {
            return Err(
                "recovery completion requires scope, origin, and existing absolute evidence files"
                    .into(),
            );
        }
        if let Some(receipt) = &self.receipt {
            receipt.complete(scope, origin, evidence)?;
        }
        self.recovery_required = false;
        Ok(())
    }
    pub(crate) fn recovery_context(&self) -> Option<&Path> {
        self.context.as_deref()
    }
    pub(crate) fn token(&self) -> &str {
        &self.token
    }
    pub(crate) fn acquire_recovery(
        root: &Path,
        farm: &Path,
        serial: &str,
        context: &Path,
        timeout: impl Into<Option<Duration>>,
    ) -> Result<Self, String> {
        let mut lease = Self::new(root, farm, resolve_scope_at(farm, serial)?.for_recovery(),
            serial, "recovery", timeout.into())?;
        lease.hold_for_recovery(context)?;
        Ok(lease)
    }
    pub(crate) fn acquire_device_recovery(
        storage: &crate::lease_storage::Storage,
        binding: &DeviceBinding,
        context: &Path,
        timeout: impl Into<Option<Duration>>,
    ) -> Result<Self, String> {
        let (root, farm) = storage.roots()?;
        let mut lease = Self::new(
            &root,
            &farm,
            Scope::device(&binding.serial, Some(binding.clone())).for_recovery(),
            &binding.serial,
            "recovery",
            timeout.into(),
        )?;
        lease.hold_for_recovery(context)?;
        Ok(lease)
    }
}
impl Drop for Lease {
    fn drop(&mut self) {
        if let Err(error) = self.report("released") {
            eprintln!("{error}");
        }
    }
}

/// A screenshot is a try-lock on one phone. Busy phones still allow lightweight reads.
pub(crate) struct EphemeralObservation {
    _guard: Guard,
}
impl EphemeralObservation {
    pub(crate) fn acquire(
        storage: &crate::lease_storage::Storage,
        serial: &str,
    ) -> Result<Self, String> {
        let (root, farm) = storage.roots()?;
        let guard = Guard::acquire(
            &root,
            &farm,
            resolve_scope(storage, serial)?,
            "observation",
            Some(Duration::ZERO),
        )?;
        for fd in &guard.fds {
            let flags = unsafe { libc::fcntl(*fd, libc::F_GETFD) };
            if flags < 0
                || unsafe { libc::fcntl(*fd, libc::F_SETFD, flags & !libc::FD_CLOEXEC) } < 0
            {
                return Err(std::io::Error::last_os_error().to_string());
            }
        }
        Ok(Self { _guard: guard })
    }
}

pub(crate) fn durable_replace(path: &Path, bytes: &[u8]) -> Result<(), String> {
    use std::io::Write;
    let parent = path.parent().ok_or("record parent missing")?;
    let tmp = parent.join(format!(
        ".lease-write-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|e| e.to_string())?
            .as_nanos()
    ));
    let mut f = File::options()
        .create_new(true)
        .write(true)
        .open(&tmp)
        .map_err(|e| e.to_string())?;

    f.write_all(bytes)
        .and_then(|_| f.sync_all())
        .map_err(|e| e.to_string())?;
    fs::rename(&tmp, path).map_err(|e| e.to_string())?;
    File::open(parent)
        .and_then(|d| d.sync_all())
        .map_err(|e| e.to_string())
}

#[path = "lease_scopes.rs"]
mod scopes;
pub(crate) fn storage_status(storage: &crate::lease_storage::Storage) -> Value {
    scopes::status(storage)
}

pub fn run_lease(args: &[String]) -> i32 {
    if matches!(
        args.first().map(String::as_str),
        Some("devices" | "devices-set" | "resolve")
    ) {
        return crate::device_registry::run_cli(args);
    }
    if args.first().map(String::as_str) == Some("status") {
        if args.len() != 2 || args[1] != "--json" {
            eprintln!("phonefarm lease status --json");
            return 2;
        }
        println!("{}", storage_status(&crate::lease_storage::resolve()));
        return 0;
    }
    let split = args.iter().position(|a| a == "--").unwrap_or(args.len());
    let mut serial = None;
    let mut timeout = None;
    let mut label = "lease".to_owned();
    let mut it = args[..split].iter();
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--serial" => serial = it.next().cloned(),
            "--label" => label = it.next().cloned().unwrap_or_default(),
            "--timeout-ms" => match it.next().and_then(|v| v.parse::<u64>().ok()) {
                Some(ms) => timeout = Some(Duration::from_millis(ms)),
                None => {
                    eprintln!("--timeout-ms requires a nonnegative integer");
                    return 2;
                }
            },
            _ => {
                eprintln!("unknown lease flag {arg}");
                return 2;
            }
        }
    }
    let Some(program) = args.get(split + 1) else {
        eprintln!("phonefarm lease [--serial S] [--timeout-ms N] -- COMMAND ...");
        return 2;
    };
    let result: Result<i32, String> = (|| {
        let storage = crate::lease_storage::resolve();
        let (root, farm) = storage.roots()?;
        let binding = crate::device_registry::inherited_binding()?;
        let inherited_serial = fd_lock::inherited()?.and_then(|(scope, _)| scope.serial);
        let serial = serial
            .as_deref()
            .or(binding.as_ref().map(|b| b.serial.as_str()))
            .or(inherited_serial.as_deref());
        let scope = serial
            .map(|s| resolve_scope(&storage, s))
            .transpose()?
            .unwrap_or_else(Scope::global);
        let lease = Lease::new(
            &root,
            &farm,
            scope,
            serial.unwrap_or("@global"),
            &label,
            timeout,
        )?;
        let mut command = std::process::Command::new(program);
        command.args(&args[split + 2..]);
        lease.command(&mut command)?;
        // Stay in the task's original group, so node reset can terminate every descendant.
        Ok(crate::devlock::wait_control_child(&mut command))
    })();
    match result {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
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
            let root = std::env::temp_dir().join(format!(
                "pf-fd-receipt-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            fs::create_dir_all(root.join("receipts")).unwrap();
            Self(root)
        }
        fn lease(&self) -> Lease {
            Lease::acquire(
                &self.0.join("devices"),
                &self.0.join("locks"),
                "fake",
                "unit",
                Duration::ZERO,
            )
            .unwrap()
        }
        fn receipt(&self, lease: &mut Lease) -> PathBuf {
            lease.receipt = Some(
                crate::lease_receipt::JobReceipt::new(
                    "0123456789ab",
                    &self.0.join("receipts"),
                    "fake",
                    "unit",
                    Value::Null,
                )
                .unwrap(),
            );
            lease.report("active").unwrap();
            fs::read_dir(self.0.join("receipts"))
                .unwrap()
                .next()
                .unwrap()
                .unwrap()
                .path()
        }
    }
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    #[test]
    fn job_receipt_release_is_not_device_recovery() {
        if crate::isolate_fd_test("devlease::tests::job_receipt_release_is_not_device_recovery") { return; }
        let scratch = Scratch::new();
        let mut lease = scratch.lease();
        let path = scratch.receipt(&mut lease);
        let token = lease.token().to_owned();
        drop(lease);
        let text = fs::read_to_string(path).unwrap();
        assert!(!text.contains(&token));
        let value: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(value["state"], "released");
        assert_eq!(value["recovery"]["status"], "not_reported");
        drop(scratch.lease());
    }
    #[test]
    fn another_write_requires_another_restoration_report() {
        if crate::isolate_fd_test("devlease::tests::another_write_requires_another_restoration_report") { return; }
        let scratch = Scratch::new();
        let context = scratch.0.join("session.json");
        fs::write(&context, "{}").unwrap();
        let mut lease = scratch.lease();
        let path = scratch.receipt(&mut lease);
        lease.hold_for_recovery(&context).unwrap();
        lease
            .complete_recovery("gray_session", "fixture", &[&context])
            .unwrap();
        let read = || serde_json::from_slice::<Value>(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(read()["recovery"]["status"], "reported_complete");
        lease.hold_for_recovery(&context).unwrap();
        assert_eq!(read()["recovery"]["status"], "required");
        drop(lease);
        assert_eq!(read()["state"], "released");
        assert_eq!(read()["recovery"]["status"], "required");
        assert_eq!(fs::read_to_string(context).unwrap(), "{}");
        drop(scratch.lease());
    }
    #[test]
    fn receipt_failure_does_not_turn_into_a_persistent_lock() {
        if crate::isolate_fd_test("devlease::tests::receipt_failure_does_not_turn_into_a_persistent_lock") { return; }
        let scratch = Scratch::new();
        let context = scratch.0.join("session.json");
        fs::write(&context, "{}").unwrap();
        let mut lease = scratch.lease();
        let path = scratch.receipt(&mut lease);
        fs::remove_file(&path).unwrap();
        fs::create_dir(&path).unwrap();
        assert!(lease.hold_for_recovery(&context).is_err());
        assert!(Lease::acquire(
            &scratch.0.join("devices"),
            &scratch.0.join("locks"),
            "fake",
            "other",
            Duration::ZERO
        )
        .is_err());
        drop(
            Lease::acquire(
                &scratch.0.join("devices"),
                &scratch.0.join("locks"),
                "other-device",
                "other",
                Duration::ZERO,
            )
            .unwrap(),
        );
        drop(lease);
        drop(scratch.lease());
    }
}
