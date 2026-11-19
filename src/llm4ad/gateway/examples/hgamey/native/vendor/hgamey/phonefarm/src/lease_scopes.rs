//! Read-only lock status. Metadata describes holders; only flock decides occupancy.
use super::*;

fn entry(path: &Path, key: Option<&str>) -> Value {
    let owner = fs::read(path.with_extension("owner.json"))
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .unwrap_or(Value::Null);
    json!({"scope":if key.is_some(){"device"}else{"global"}, "device_id":key,
        "device_binding":owner["scope"]["binding"], "serial":owner["scope"]["serial"],
        "label":owner["label"], "job_id":owner["job_id"], "actor":owner["actor"],
        "holder_pid":owner["pid"], "since":owner["since"], "state":"active",
        "role":"device_lease", "device_lock_held":true, "reason":"kernel lock held"})
}
fn probe(path: &Path, shared: bool, errors: &mut Vec<String>) -> Option<bool> {
    match fd_lock::held(path, shared) {
        Ok(value) => Some(value),
        Err(error) => {
            errors.push(error);
            None
        }
    }
}
fn state(held: Option<bool>) -> &'static str {
    match held {
        Some(true) => "held",
        Some(false) => "clear",
        None => "unknown",
    }
}
pub(super) fn status(storage: &crate::lease_storage::Storage) -> Value {
    let mut errors = Vec::new();
    let mut entries = Vec::new();
    let mut devices = Vec::new();
    let mut bridge_held = None;
    let mut global_held = None;
    let mut revision = None;
    let mut known = std::collections::HashSet::new();
    if let (Some(farm), Some(root)) = (&storage.devlock_dir, &storage.device_lease_dir) {
        let bridge = farm.join("phonefarm-bridge.lock");
        bridge_held = probe(&bridge, false, &mut errors);
        global_held = probe(&bridge, true, &mut errors);
        if global_held == Some(true) {
            entries.push(entry(&bridge, None));
        }
        match crate::device_registry::snapshot(farm) {
            Ok((rev, registered)) => {
                revision = Some(rev);
                for device in registered {
                    let path = fd_lock::device_path(root, &device.device_id);
                    known.insert(path.clone());
                    let held = probe(&path, false, &mut errors);
                    if held == Some(true) {
                        entries.push(entry(&path, Some(&device.device_id)));
                    }
                    devices.push(
                        json!({"device_id":device.device_id,"aliases":device.aliases,
                        "status":state(held),"error":null}),
                    );
                }
            }
            Err(error) => errors.push(error),
        }
        match fs::read_dir(root.join("by-device-id")) {
            Ok(files) => {
                for file in files {
                    let path = match file {
                        Ok(file) => file.path(),
                        Err(error) => {
                            errors.push(error.to_string());
                            continue;
                        }
                    };
                    if path
                        .extension()
                        .is_some_and(|extension| extension == "lock")
                        && !known.contains(&path)
                        && probe(&path, false, &mut errors) == Some(true)
                    {
                        let owner = fs::read(path.with_extension("owner.json"))
                            .ok()
                            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
                            .unwrap_or(Value::Null);
                        entries.push(entry(&path, owner["scope"]["key"].as_str()));
                    }
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
            Err(error) => errors.push(error.to_string()),
        }
    }
    errors.extend(storage.error.iter().cloned());
    json!({"schema_version":1,"status":if errors.is_empty(){state(bridge_held)}else{"unknown"},
        "bridge_held":bridge_held,"entries":entries,
        "error":if errors.is_empty(){None}else{Some(errors.join("; "))}, "storage":storage,
        "device_scopes":{"schema":"phonefarm/device-scopes@1",
            "registry_revision":revision,"global":{"status":state(global_held),"error":null},
            "queue_waiting":false,"unknown":!errors.is_empty(),"devices":devices}})
}
