//! Diagnostic identity of this executable; never an execution or lease authority.
use serde_json::{json, Value};
use std::{
    env,
    fs::{self, OpenOptions},
    io::{self, Write},
    os::unix::fs::OpenOptionsExt,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

fn known(value: Option<&'static str>) -> Option<&'static str> {
    value.filter(|v| !v.is_empty())
}

pub(crate) fn identity() -> Value {
    json!({
        "schema": "phonefarm/runtime-identity@1",
        "program": env!("CARGO_PKG_NAME"),
        "package_version": env!("CARGO_PKG_VERSION"),
        "build": {
            "source_revision": known(option_env!("HGAMEY_RUNTIME_REVISION")),
            "source_dirty": match option_env!("HGAMEY_RUNTIME_DIRTY") {
                Some("true") => Some(true),
                Some("false") => Some(false),
                _ => None,
            },
            "target": known(option_env!("HGAMEY_RUNTIME_TARGET")),
            "profile": known(option_env!("HGAMEY_RUNTIME_PROFILE")),
        },
        "supported_protocols": [
            "phonefarm/runtime-identity@1",
            "phonefarm/program-start@1",
            "phonefarm/gray-control-gateway@1",
        ],
    })
}

/// Call before threads, argument handling or any child can inherit this path.
/// Failure is diagnostic only and must not change stdout or the command outcome.
pub(crate) fn report_startup() {
    let destination = env::var_os("PF_RUNTIME_REPORT");
    env::remove_var("PF_RUNTIME_REPORT");
    if let (Some(destination), Ok(job_id)) = (destination, env::var("PF_JOB_ID")) {
        if !job_id.is_empty() && job_id.len() <= 256 && !job_id.chars().any(char::is_control) {
            let _ = write_startup(Path::new(&destination), &job_id);
        }
    }
}

fn write_startup(destination: &Path, job_id: &str) -> io::Result<()> {
    if !destination.is_absolute()
        || destination.file_name().and_then(|n| n.to_str()) != Some(".program-runtime.json")
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid runtime report path",
        ));
    }
    let directory = destination.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "runtime report directory is absent",
        )
    })?;
    // The caller supplies an existing out directory. Do not create a new tree.
    let directory_file = fs::File::open(directory)?;
    if !directory_file.metadata()?.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "runtime report parent is not a directory",
        ));
    }
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let temporary = directory.join(format!(
        ".program-runtime-{}-{nonce}.tmp",
        std::process::id()
    ));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temporary)?;
    let result = (|| {
        let record = json!({"schema":"phonefarm/program-start@1", "job_id":job_id,
            "pid":std::process::id(), "identity":identity()});
        serde_json::to_writer(&mut file, &record)?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        // hard_link publishes a complete record only if the destination is absent.
        fs::hard_link(&temporary, destination)?;
        directory_file.sync_all()
    })();
    let _ = fs::remove_file(&temporary);
    result
}
