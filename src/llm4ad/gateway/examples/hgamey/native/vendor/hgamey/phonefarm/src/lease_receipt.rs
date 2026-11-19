//! Job association and scoped caller-reported recovery, never whole-device clearance.
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub(crate) fn valid_job_id(id: &str) -> bool {
    id.len() == 12 && id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

pub(crate) struct JobReceipt {
    path: PathBuf,
    value: Mutex<Value>,
}

impl JobReceipt {
    pub(crate) fn from_env(
        serial: &str,
        label: &str,
        recovery_of: Value,
    ) -> Result<Option<Self>, String> {
        match (
            std::env::var_os("PF_JOB_ID"),
            std::env::var_os("PF_JOB_LEASE_DIR"),
        ) {
            (None, None) => Ok(None),
            (Some(id), Some(dir)) => Self::new(
                id.to_str().ok_or("PF_JOB_ID must be UTF-8")?,
                &PathBuf::from(dir),
                serial,
                label,
                recovery_of,
            )
            .map(Some),
            _ => Err("PF_JOB_ID and PF_JOB_LEASE_DIR must be provided together".into()),
        }
    }

    pub(crate) fn new(
        job_id: &str,
        dir: &Path,
        serial: &str,
        label: &str,
        recovery_of: Value,
    ) -> Result<Self, String> {
        if !valid_job_id(job_id) {
            return Err("PF_JOB_ID must contain exactly 12 lowercase hexadecimal characters".into());
        }
        if !dir.is_absolute()
            || !std::fs::symlink_metadata(dir).is_ok_and(|m| m.file_type().is_dir())
        {
            return Err("PF_JOB_LEASE_DIR must be an existing absolute directory".into());
        }
        // A public correlation ID, generated separately from the ownership token.
        let id = format!(
            "lease-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(|e| e.to_string())?
                .as_nanos()
        );
        Ok(Self {
            path: dir.join(format!("{id}.json")),
            value: Mutex::new(json!({"schema":"phonefarm/job-lease@1", "job_id":job_id,
                "lease_id":id, "serial":serial, "label":label, "state":"active",
                "recovery_of":recovery_of, "recovery":{"status":"not_reported", "reason":null,
                    "context":null, "scope":null, "evidence_origin":null, "evidence":[]},
                "updated_at":chrono::Utc::now().to_rfc3339()})),
        })
    }

    pub(crate) fn record(&self, binding: Option<&crate::device_registry::DeviceBinding>,
        context: Option<&Path>, state: &str, required: bool) -> Result<(), String> {
        self.update(|value| {
            value["state"] = json!(state);
            if let Some(binding) = binding {
                value["device_binding"] = json!(binding);
                value["lease_scope"] = json!("device");
            }
            value["recovery"]["context"] = json!(context);
            if required {
                value["recovery"] = json!({"status":"required", "reason":"device_restoration_not_reported",
                    "context":context, "scope":null, "evidence_origin":null, "evidence":[]});
            }
        })
    }

    pub(crate) fn complete(
        &self,
        scope: &str,
        origin: &str,
        evidence: &[&Path],
    ) -> Result<(), String> {
        self.update(|v| {
            v["recovery"]["status"] = json!("reported_complete");
            v["recovery"]["reason"] = Value::Null;
            v["recovery"]["scope"] = json!(scope);
            v["recovery"]["evidence_origin"] = json!(origin);
            v["recovery"]["evidence"] = json!(evidence);
        })
    }

    fn update(&self, update: impl FnOnce(&mut Value)) -> Result<(), String> {
        let mut current = self
            .value
            .lock()
            .map_err(|_| "job receipt mutex poisoned")?;
        let mut value = current.clone();
        update(&mut value);
        value["updated_at"] = json!(chrono::Utc::now().to_rfc3339());
        crate::devlease::durable_replace(&self.path, value.to_string().as_bytes())
            .map_err(|e| format!("job lease receipt {}: {e}", self.path.display()))?;
        *current = value;
        Ok(())
    }
}
