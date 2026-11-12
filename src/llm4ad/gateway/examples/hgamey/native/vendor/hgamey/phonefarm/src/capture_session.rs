//! Capture owns a worker group; recovery certifies only settings, input and exit policy.
use crate::{
    confirmation_transport::{bounded, journal, new_file, quote as q},
    devlease::Lease,
};
use serde_json::{json, Value};
use std::{
    cell::RefCell,
    fs,
    path::{Path, PathBuf},
    time::Duration,
};

const SCHEMA: &str = "phonefarm/capture-session@1";
/// Some Android builds deny the shell user /proc/bus/input/devices; a rooted
/// device can still answer. Both readers parse the same /proc format.
pub(crate) const INPUT_DEVICES: &str =
    "cat /proc/bus/input/devices 2>/dev/null || su -c 'cat /proc/bus/input/devices'";

pub(crate) struct Session {
    lease: RefCell<Lease>,
    adb: PathBuf,
    pub dir: PathBuf,
    state: RefCell<Value>,
}

impl Session {
    pub fn new(
        lease: Lease,
        out: Option<&str>,
        shutdown: bool,
        origin: &str,
    ) -> Result<Self, String> {
        let parent = match out {
            Some(p) => PathBuf::from(p),
            None => crate::hwcond::state_dir()?.join("capture-sessions"),
        };
        fs::create_dir_all(&parent).map_err(|e| e.to_string())?;
        let dir = parent
            .canonicalize()
            .map_err(|e| e.to_string())?
            .join(format!("capture-{}", lease.token()));
        fs::create_dir(&dir).map_err(|e| e.to_string())?;
        fs::create_dir(dir.join("frames")).map_err(|e| e.to_string())?;
        for p in dir.ancestors() {
            fs::File::open(p)
                .and_then(|f| f.sync_all())
                .map_err(|e| e.to_string())?;
        }
        let state = json!({"schema":SCHEMA,"serial":lease.serial,"token":lease.token(),
            "stage":"prepared","boot_id":null,"stayon":null,"shutdown":shutdown,
            "hid_name":format!("Phonefarm Capture {}",lease.token()),"hid_started":false,"hid_registered":false,
            "write_pending":null,"transport_uncertain":false,"screenshot_file":null,"evidence_origin":origin,
            "created_at":chrono::Utc::now().to_rfc3339()});
        let s = Self {
            lease: RefCell::new(lease),
            adb: crate::device::locate_adb()
                .ok_or("ADB not available")?
                .into(),
            dir,
            state: RefCell::new(state),
        };
        s.save()?;
        Ok(s)
    }

    pub fn resume(dir: &Path, serial: &str) -> Result<Self, String> {
        let dir = dir.canonicalize().map_err(|e| e.to_string())?;
        let state = read_state(&dir)?;
        validate(&state, serial)?;
        let (root, legacy) = crate::lease_storage::resolve().roots()?;
        let lease = Lease::acquire_recovery(&root, &legacy, serial, &dir, crate::devlease::wait_timeout()?)?;
        Ok(Self {
            lease: RefCell::new(lease),
            adb: crate::device::locate_adb()
                .ok_or("ADB not available")?
                .into(),
            dir,
            state: RefCell::new(state),
        })
    }

    fn save(&self) -> Result<(), String> {
        save_state(&self.dir, &self.state.borrow())
    }
    fn hold(&self) -> Result<(), String> {
        self.lease.borrow_mut().hold_for_recovery(&self.dir)
    }

    fn reload(&self) -> Result<(), String> {
        let state = read_state(&self.dir)?;
        validate(&state, &self.lease.borrow().serial)?;
        for key in [
            "token",
            "serial",
            "boot_id",
            "stayon",
            "shutdown",
            "hid_name",
            "evidence_origin",
            "stage",
        ] {
            if state[key] != self.state.borrow()[key] {
                return Err(format!("capture session {key} changed"));
            }
        }
        *self.state.borrow_mut() = state;
        Ok(())
    }

    fn read(&self, operation: &str, command: &str, timeout: u64) -> Result<String, String> {
        let args = vec![
            "-s".into(),
            self.lease.borrow().serial.clone(),
            "shell".into(),
            command.into(),
        ];
        journal(
            &self.dir,
            json!({"event":"command_intent","operation":operation,"args":args}),
        )?;
        let r = crate::lease_command::run(
            &self.lease.borrow(),
            &self.adb,
            &args,
            &self.dir.join("commands"),
            operation,
            Duration::from_millis(timeout),
        );
        journal(
            &self.dir,
            json!({"event":"command_result","operation":operation,
            "code":r.as_ref().ok().map(|o|o.code),"error":r.as_ref().err()}),
        )?;
        let r = r?;
        if r.code != 0 {
            return Err(format!("{operation}: exit {}: {}", r.code, r.stderr.trim()));
        }
        Ok(r.stdout)
    }

    fn ready(&self) -> Result<(), String> {
        if self.state.borrow()["transport_uncertain"] != false
            || !self.state.borrow()["write_pending"].is_null()
        {
            return Err("capture command/input creation is unresolved; independent reconciliation required before recovery writes or completion".into());
        }
        Ok(())
    }

    fn write(&self, operation: &str, command: &str) -> Result<(), String> {
        self.ready()?;
        self.hold()?;
        self.state.borrow_mut()["write_pending"] = json!(operation);
        self.save()?;
        self.read(operation, command, 8000)?;
        self.state.borrow_mut()["write_pending"] = Value::Null;
        if let Err(e) = self.save() {
            self.state.borrow_mut()["write_pending"] = json!(operation);
            return Err(e);
        }
        Ok(())
    }

    pub fn prepare(&self) -> Result<(), String> {
        let boot = self.read("boot", "cat /proc/sys/kernel/random/boot_id", 8000)?;
        let boot = boot.trim();
        if boot.is_empty() || !boot.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-') {
            return Err("invalid boot identity".into());
        }
        let stayon = self.read(
            "stayon_snapshot",
            "settings get global stay_on_while_plugged_in",
            8000,
        )?;
        if !valid_stayon(stayon.trim()) {
            return Err("unreadable original stay-on setting".into());
        }
        self.state.borrow_mut()["boot_id"] = json!(boot);
        self.state.borrow_mut()["stayon"] = json!(stayon.trim());
        self.save()
    }

    pub fn run(
        &self,
        args: &crate::capture::CaptureArgs,
        timeout: Duration,
    ) -> Result<(Value, i32), String> {
        new_file(
            &self.dir.join("request.json"),
            &serde_json::to_vec_pretty(args).map_err(|e| e.to_string())?,
        )?;
        self.hold()?;
        self.state.borrow_mut()["stage"] = json!("active");
        self.save()?;
        let program = std::env::current_exe().map_err(|e| e.to_string())?;
        let result = crate::lease_command::run_worker(
            &self.lease.borrow(),
            &program,
            &["__capture-worker".into(), self.dir.to_string_lossy().into()],
            &self.dir.join("worker-output"),
            "capture-worker",
            timeout,
        );
        self.reload()?;
        let result = result?;
        let value = self.worker_result()?;
        if value["exit_code"] != result.code {
            return Err("capture worker exit does not match completion record".into());
        }
        if let Some(e) = value["error"].as_str() {
            return Err(e.into());
        }
        if !matches!(result.code, 0 | 1) {
            return Err(format!("capture worker exit {}", result.code));
        }
        Ok((value["report"].clone(), result.code))
    }

    pub fn finish(&self, experiment_error: Option<&str>) -> Result<Value, String> {
        let checks: Result<Value, String> = (|| {
            self.reload()?;
            self.ready()?;
            if self.state.borrow()["stage"] == "prepared" {
                return Ok(json!({"scope":"no_device_mutations"}));
            }
            // A watchdog/crash cannot be cleared by a stale manifest whose
            // uncertainty update failed. Completion is written after all
            // worker device work and local input cleanup have returned.
            self.worker_result()?;
            let boot = self.read("recovery_boot", "cat /proc/sys/kernel/random/boot_id", 8000)?;
            if self.state.borrow()["boot_id"].as_str() != Some(boot.trim()) {
                return Err("capture device boot changed; automatic restore refused".into());
            }
            let screenshot = screenshot_file(&self.state.borrow())?.map(str::to_owned);
            if let Some(path) = screenshot {
                self.write("remove_screenshot", &format!("rm -f {path} && test ! -e {path}"))?;
                self.state.borrow_mut()["screenshot_file"] = Value::Null;
                self.save()?;
            }
            let mut input_absent = Value::Null;
            if self.state.borrow()["hid_started"] == true {
                if self.state.borrow()["hid_registered"] != true {
                    return Err("capture HID registration never confirmed".into());
                }
                let name = self.state.borrow()["hid_name"]
                    .as_str()
                    .unwrap()
                    .to_string();
                let mut absent = false;
                for attempt in 0..4 {
                    let devices =
                        self.read("input_readback", INPUT_DEVICES, 8000)?;
                    absent = input_absent_from(&devices, &name)?;
                    if absent {
                        break;
                    }
                    if attempt < 3 {
                        std::thread::sleep(Duration::from_millis(250));
                    }
                }
                if !absent {
                    return Err(
                        "capture-owned HID is still registered; input recovery required".into(),
                    );
                }
                input_absent = json!(true);
            }
            let original = self.state.borrow()["stayon"]
                .as_str()
                .ok_or("missing stay-on snapshot")?
                .to_string();
            let restore = if original == "null" {
                "settings delete global stay_on_while_plugged_in".into()
            } else {
                format!(
                    "settings put global stay_on_while_plugged_in {}",
                    q(&original)
                )
            };
            self.write("restore_stayon", &restore)?;
            let actual = self.read(
                "stayon_readback",
                "settings get global stay_on_while_plugged_in",
                8000,
            )?;
            if actual.trim() != original {
                return Err("stay-on restoration readback differs from the saved value".into());
            }
            let shutdown = self.state.borrow()["shutdown"] == true;
            let mut policy =
                json!({"requested":"keep_running","app_stopped":null,"screen_asleep":null});
            if shutdown {
                self.write(
                    "stop_target",
                    &format!("am force-stop {}", q(crate::plugins::genshin::PACKAGE)),
                )?;
                let processes = self.read("target_readback", "ps -A -o NAME", 8000)?;
                if !target_absent(&processes, crate::plugins::genshin::PACKAGE)? {
                    return Err(
                        "target package process names still present after force-stop".into(),
                    );
                }
                self.write("sleep_screen", "input keyevent 223")?;
                let mut asleep = false;
                for attempt in 0..4 {
                    let power = self.read("screen_readback", "dumpsys power", 8000)?;
                    asleep = power.lines().any(|l| l.trim() == "mWakefulness=Asleep");
                    if asleep {
                        break;
                    }
                    if attempt < 3 {
                        std::thread::sleep(Duration::from_millis(250));
                    }
                }
                if !asleep {
                    return Err("screen sleep policy not confirmed".into());
                }
                policy = json!({"requested":"stop_and_sleep","app_stopped":true,
                    "app_check":"matching package process names absent","screen_asleep":true});
            }
            Ok(
                json!({"scope":"capture_input_stayon_and_exit_policy","input_absent":input_absent,
                "input_used":self.state.borrow()["hid_started"],"stayon_restored":true,"exit_policy":policy,
                "game_state_restored":false}),
            )
        })();
        let evidence = self.dir.join(format!(
            "recovery-{}-{}.json",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let scope = checks
            .as_ref()
            .ok()
            .and_then(|c| c["scope"].as_str())
            .unwrap_or("capture_session")
            .to_owned();
        let mut value = json!({"schema":"phonefarm/capture-recovery@1","status":if checks.is_ok(){"verified"}else{"required"},
            "scope":scope,"serial":self.lease.borrow().serial,"session":self.dir,"checks":checks.as_ref().ok(),
            "error":checks.as_ref().err(),"experiment_error":experiment_error,"evidence":evidence,
            "evidence_origin":self.state.borrow()["evidence_origin"],"at":chrono::Utc::now().to_rfc3339()});
        new_file(
            &evidence,
            &serde_json::to_vec_pretty(&value).map_err(|e| e.to_string())?,
        )?;
        if let Err(e) = checks {
            self.hold()?;
            return Err(format!("{e}; recovery evidence {}", evidence.display()));
        }
        self.lease.borrow_mut().complete_recovery(
            &scope,
            self.state.borrow()["evidence_origin"].as_str().unwrap(),
            &[&evidence],
        )?;
        value["status"] = json!("reported_complete");
        Ok(value)
    }

    fn worker_result(&self) -> Result<Value, String> {
        let value: Value =
            serde_json::from_slice(&bounded(&self.dir.join("worker-result.json"), 1024 * 1024)?)
                .map_err(|e| e.to_string())?;
        let state = self.state.borrow();
        if value["schema"] != "phonefarm/capture-worker-result@1"
            || value["token"] != state["token"]
            || value["serial"] != state["serial"]
            || !matches!(value["exit_code"].as_i64(), Some(0..=2))
            || !(value["error"].is_string() || value["error"].is_null())
        {
            return Err("capture worker completion is unrecognized; recovery required".into());
        }
        Ok(value)
    }
}

pub(crate) fn read_state(dir: &Path) -> Result<Value, String> {
    serde_json::from_slice(&bounded(&dir.join("session.json"), 65536)?).map_err(|e| e.to_string())
}
pub(crate) fn save_state(dir: &Path, state: &Value) -> Result<(), String> {
    crate::devlease::durable_replace(
        &dir.join("session.json"),
        &serde_json::to_vec_pretty(state).map_err(|e| e.to_string())?,
    )
}
fn valid_stayon(value: &str) -> bool {
    value == "null"
        || (!value.is_empty()
            && value.bytes().all(|b| b.is_ascii_digit())
            && value.parse::<u32>().is_ok())
}
/// Accept only a file owned by this capture token, never an arbitrary saved path.
pub(crate) fn screenshot_file(state: &Value) -> Result<Option<&str>, String> {
    if state["screenshot_file"].is_null() {
        return Ok(None);
    }
    let token = state["token"].as_str().ok_or("missing capture token")?;
    if token.is_empty() || !token.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-') {
        return Err("invalid capture screenshot token".into());
    }
    let path = state["screenshot_file"].as_str().ok_or("invalid capture screenshot path")?;
    let prefix = format!("/data/local/tmp/phonefarm_capture_{token}_");
    let sequence = path.strip_prefix(&prefix).and_then(|s| s.strip_suffix(".png"));
    if !sequence.is_some_and(|s| !s.is_empty() && s.bytes().all(|c| c.is_ascii_digit())) {
        return Err("screenshot file does not belong to this capture".into());
    }
    Ok(Some(path))
}

pub(crate) fn validate(state: &Value, serial: &str) -> Result<(), String> {
    screenshot_file(state)?;
    let token = state["token"].as_str().ok_or("missing capture token")?;
    if state["schema"] != SCHEMA
        || state["serial"] != serial
        || token.is_empty()
        || !token
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-')
        || state["hid_name"] != format!("Phonefarm Capture {token}")
        || !matches!(state["stage"].as_str(), Some("prepared" | "active"))
        || !matches!(
            state["evidence_origin"].as_str(),
            Some("device" | "synthetic")
        )
        || [
            "shutdown",
            "hid_started",
            "hid_registered",
            "transport_uncertain",
        ]
        .iter()
        .any(|k| !state[k].is_boolean())
        || !(state["write_pending"].is_null() || state["write_pending"].is_string())
    {
        return Err("invalid capture session identity or policy".into());
    }
    if state["stage"] == "active"
        && (!state["boot_id"].as_str().is_some_and(|s| {
            !s.is_empty() && s.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-')
        }) || !state["stayon"].as_str().is_some_and(valid_stayon))
    {
        return Err("missing capture recovery baseline".into());
    }
    Ok(())
}
pub(crate) fn input_absent_from(text: &str, name: &str) -> Result<bool, String> {
    if !text.lines().any(|l| l.starts_with("I: Bus="))
        || !text.lines().any(|l| l.starts_with("N: Name="))
    {
        return Err("unrecognized input-device readback".into());
    }
    Ok(!text.lines().any(|l| l == format!("N: Name=\"{name}\"")))
}
fn target_absent(text: &str, package: &str) -> Result<bool, String> {
    let mut lines = text.lines().map(str::trim).filter(|l| !l.is_empty());
    if lines.next() != Some("NAME") {
        return Err("unrecognized process-name readback".into());
    }
    Ok(!lines.any(|l| l == package || l.starts_with(&format!("{package}:"))))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn screenshot_cleanup_accepts_only_this_sessions_file() {
        let mut state = json!({"token":"fixture", "screenshot_file":null});
        assert!(screenshot_file(&state).unwrap().is_none());
        state["screenshot_file"] = json!("/data/local/tmp/phonefarm_capture_fixture_12.png");
        assert!(screenshot_file(&state).unwrap().is_some());
        for path in ["/data/local/tmp/phonefarm_capture_other_12.png",
            "/data/local/tmp/phonefarm_capture_fixture_../other.png",
            "/data/local/tmp/phonefarm_capture_fixture_.png",
            "/data/local/tmp/phonefarm_capture_fixture_1.png; echo bad"] {
            state["screenshot_file"] = json!(path);
            assert!(screenshot_file(&state).is_err(), "{path}");
        }
    }

    #[test]
    fn recovery_readback_requires_known_shape_and_exact_identity() {
        assert!(input_absent_from("", "owned").is_err());
        let input = "I: Bus=0003\nN: Name=\"owned\"\n";
        assert!(!input_absent_from(input, "owned").unwrap());
        assert!(input_absent_from(input, "another").unwrap());
        assert!(target_absent("", "com.game").is_err());
        assert!(!target_absent("NAME\ncom.game:service\n", "com.game").unwrap());
        assert!(target_absent("NAME\ncom.game.other\n", "com.game").unwrap());
        assert!(valid_stayon("null") && valid_stayon("7") && !valid_stayon("unknown"));
    }
}
