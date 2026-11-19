//! Read-only application state at task start. Observation errors are data, not admission.
use crate::device::{parse_aa_dump, parse_bm_bundles, Device};
use serde_json::{json, Value};
use std::{
    collections::BTreeSet,
    time::{Duration, Instant},
};

// Read the foreground and inventory in one shell, retaining each exit status.
const HARMONY_APPS: &str = r#"
part() {
    printf '\n__PF_APP_PART__%s\n' "$1"
    shift
    "$@"
    printf '\n__PF_APP_STATUS__%s\n' "$?"
}
part foreground aa dump -a
part processes ps -A -o NAME
part bundles bm dump -a
"#;

fn response(command: &str, text: &str) -> Result<String, String> {
    let Some((body, status)) = text.rsplit_once("__PF_APP_STATUS__") else {
        return Err(format!(
            "{command}: no completed response (transport unavailable or timed out)"
        ));
    };
    if status.trim() != "0" {
        return Err(format!(
            "{command}: exit {}: {}",
            status.trim(),
            body.trim()
        ));
    }
    Ok(body.trim().to_string())
}

fn read(phone: &Device, command: &str, deadline: Instant) -> Result<String, String> {
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Err(format!("{command}: observation deadline exceeded"));
    }
    // Device::shell already bounds the transport client and detaches adb/hdc servers.
    let text = phone.shell(
        &format!("{command}\nprintf '\\n__PF_APP_STATUS__%s\\n' \"$?\""),
        remaining.as_millis() as u64,
    );
    response(command, &text)
}

fn process_names(processes: &str) -> Result<BTreeSet<String>, String> {
    if !processes.lines().any(|line| line.trim() == "NAME") {
        return Err("ps: process list header unavailable".into());
    }
    Ok(processes
        .lines()
        .skip(1)
        .map(|line| line.trim().split(':').next().unwrap_or("").to_string())
        .collect())
}

fn running_android(phone: &Device, deadline: Instant) -> Result<Vec<String>, String> {
    let names = process_names(&read(phone, "ps -A -o NAME", deadline)?)?;
    Ok(read(phone, "pm list packages -3", deadline)?
        .lines()
        .filter_map(|line| line.strip_prefix("package:"))
        .filter(|name| names.contains(*name))
        .map(str::to_string)
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect())
}

fn part(output: &str, name: &str) -> Result<String, String> {
    let body = output
        .split("__PF_APP_PART__")
        .skip(1)
        .filter_map(|part| part.split_once('\n'))
        .find(|(label, _)| label.trim() == name)
        .map(|(_, body)| body)
        .unwrap_or("");
    response(name, body)
}

fn running_harmony(output: &str) -> Result<Vec<String>, String> {
    let names = process_names(&part(output, "processes")?)?;
    let bundles = parse_bm_bundles(&part(output, "bundles")?);
    // Approximate third-party visibility without expensive per-bundle metadata.
    Ok(bundles
        .into_iter()
        .filter(|name| {
            names.contains(name)
                && !["com.huawei.", "com.ohos.", "ohos."]
                    .iter()
                    .any(|prefix| name.starts_with(prefix))
        })
        .collect())
}

pub(crate) fn snapshot(serial: &str) -> Result<Value, String> {
    let tmp = std::env::temp_dir().join(format!(
        "pf-app-snapshot-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir(&tmp).map_err(|e| e.to_string())?;
    let result = crate::observation::run(
        "app-snapshot",
        &[
            "--serial".into(),
            serial.into(),
            "--tmp".into(),
            tmp.to_string_lossy().into(),
        ],
        &crate::lease_storage::resolve(),
        Duration::from_secs(5),
    )
    .map_err(|error| error.reason)
    .and_then(|output| serde_json::from_slice(&output.stdout).map_err(|e| e.to_string()));
    // The supervisor has reaped the observation group, including timed-out clients.
    let _ = std::fs::remove_dir_all(tmp);
    Ok(result.unwrap_or_else(|error| {
        json!({"serial": serial, "foreground": null,
        "running_third_party": null, "errors": [error]})
    }))
}

pub(crate) fn collect(serial: &str, tmp: &str) -> Result<Value, String> {
    let phone = Device::new(Some(serial.into()), tmp.into());
    let deadline = Instant::now() + Duration::from_secs(4);
    let (foreground, running) = if serial.starts_with("hdc:") {
        let output = phone.shell(
            HARMONY_APPS,
            deadline
                .saturating_duration_since(Instant::now())
                .as_millis() as u64,
        );
        let foreground = part(&output, "foreground").and_then(|text| {
            let (name, _) = parse_aa_dump(&text);
            if name.is_empty() {
                Err("foreground application unavailable".into())
            } else {
                Ok(name)
            }
        });
        (foreground, running_harmony(&output))
    } else {
        let foreground = read(&phone, "dumpsys window", deadline).and_then(|text| {
            crate::fleet::parse_foreground(&text)
                .ok_or_else(|| "foreground application unavailable".into())
        });
        (foreground, running_android(&phone, deadline))
    };
    let mut errors = vec![];
    let foreground = foreground.map_err(|e| errors.push(e)).ok();
    let running = running.map_err(|e| errors.push(e)).ok();
    Ok(
        json!({"serial": serial, "foreground": foreground, "running_third_party": running, "errors": errors}),
    )
}
