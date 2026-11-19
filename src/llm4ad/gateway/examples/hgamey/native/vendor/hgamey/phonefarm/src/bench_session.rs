//! One bench's owned transport, remote workers and scoped recovery evidence.
use crate::{devlease::Lease, hwcond::{self, Shell}};
use crate::confirmation_transport::{journal, quote as q};
use serde_json::{json, Value};
use std::{cell::RefCell, fs, path::{Path, PathBuf}, time::Duration};

const WORKER: &str = include_str!("../loop_v1/tools/confirmation_worker.sh");
const BOOTSTRAP: &str = include_str!("../loop_v1/tools/confirmation_bootstrap.sh");
const SCHEMA: &str = "phonefarm/bench-session@1";

pub(crate) struct Session {
    lease: RefCell<Lease>,
    program: PathBuf,
    pub dir: PathBuf,
    state: RefCell<Value>,
    shell_error: RefCell<Option<String>>,
}

impl Session {
    pub fn new(lease: Lease, out: Option<&str>, keep: bool, origin: &str) -> Result<Self, String> {
        let parent = match out {
            Some(p) => PathBuf::from(p),
            None => hwcond::state_dir()?.join("bench-sessions"),
        };
        fs::create_dir_all(&parent).map_err(|e| e.to_string())?;
        let parent = parent.canonicalize().map_err(|e| e.to_string())?;
        let token = lease.token().to_string();
        let dir = parent.join(format!("bench-{token}"));
        fs::create_dir(&dir).map_err(|e| e.to_string())?;
        for p in dir.ancestors() {
            fs::File::open(p).and_then(|f| f.sync_all()).map_err(|e| e.to_string())?;
        }
        let program = crate::device::locate_adb().ok_or("ADB not available")?;
        let state = json!({"schema":SCHEMA, "serial":lease.serial, "token":token,
            "remote":format!("/data/local/tmp/phonefarm-confirmation-bench-{token}"),
            "boot_id":null, "root":false, "stage":"prepared", "keep":keep, "control_command_pending":false,
            "evidence_origin":origin, "created_at":chrono::Utc::now().to_rfc3339()});
        let s = Self {lease:RefCell::new(lease), program:program.into(), dir,
            state:RefCell::new(state), shell_error:RefCell::new(None)};
        s.save()?;
        Ok(s)
    }

    pub fn resume(dir: &Path, serial: &str) -> Result<Self, String> {
        let dir = dir.canonicalize().map_err(|e| e.to_string())?;
        let state: Value = serde_json::from_slice(&crate::confirmation_transport::bounded(&dir.join("session.json"), 65536)?)
            .map_err(|e| e.to_string())?;
        let token = state["token"].as_str().ok_or("missing bench token")?;
        if state["schema"] != SCHEMA || state["serial"] != serial
            || token.is_empty() || !token.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
            || state["remote"] != format!("/data/local/tmp/phonefarm-confirmation-bench-{token}")
            || !matches!(state["evidence_origin"].as_str(), Some("device" | "synthetic"))
            || !matches!(state["stage"].as_str(), Some("prepared" | "install_pending" | "installed" | "running"))
            || !state["root"].is_boolean() || !state["keep"].is_boolean()
            || !state["control_command_pending"].is_boolean()
        { return Err("invalid or mismatched bench session identity".into()); }
        let (root, legacy) = crate::lease_storage::resolve().roots()?;
        let lease = Lease::acquire_recovery(&root, &legacy, serial, &dir, crate::devlease::wait_timeout()?)?;
        Ok(Self {lease:RefCell::new(lease), program:crate::device::locate_adb().ok_or("ADB not available")?.into(),
            dir, state:RefCell::new(state), shell_error:RefCell::new(None)})
    }

    fn save(&self) -> Result<(), String> {
        crate::devlease::durable_replace(&self.dir.join("session.json"), &serde_json::to_vec_pretty(&*self.state.borrow()).unwrap())
    }

    pub fn hold(&self) -> Result<(), String> {
        self.lease.borrow_mut().hold_for_recovery(&self.dir)
    }

    pub fn state_path(&self) -> Result<PathBuf, String> {
        let serial = self.lease.borrow().serial.clone();
        match hwcond::lock_state_path("bench", &Some(serial)) {
            Ok(p) => Ok(p),
            Err(e) => { self.hold()?; Err(format!("{e}; recovery session {}", self.dir.display())) }
        }
    }

    pub fn identity(&self, root: bool) -> Result<(), String> {
        let boot = self.checked("identity", "cat /proc/sys/kernel/random/boot_id", 8000)?;
        let boot = boot.trim();
        if boot.is_empty() || !boot.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-') {
            return Err("invalid device boot identity".into());
        }
        self.state.borrow_mut()["boot_id"] = json!(boot);
        self.state.borrow_mut()["root"] = json!(root);
        self.save()
    }

    fn run(&self, op: &str, args: &[String], ms: u64) -> Result<crate::lease_command::Output, String> {
        let args: Vec<_> = [vec!["-s".into(), self.lease.borrow().serial.clone()], args.to_vec()].concat();
        journal(&self.dir, json!({"event":"command_intent", "operation":op, "args":args, "at":chrono::Utc::now().to_rfc3339()}))?;
        let result = crate::lease_command::run(&self.lease.borrow(), &self.program, &args,
            &self.dir.join("commands"), op, Duration::from_millis(ms));
        journal(&self.dir, json!({"event":"command_result", "operation":op,
            "code":result.as_ref().ok().map(|r| r.code), "error":result.as_ref().err(),
            "remote_idle_verified":false, "at":chrono::Utc::now().to_rfc3339()}))?;
        result
    }

    pub fn checked(&self, op: &str, command: &str, ms: u64) -> Result<String, String> {
        let r = self.run(op, &["shell".into(), command.into()], ms)?;
        if r.code != 0 { return Err(format!("{op}: exit {}: {}", r.code, r.stderr.trim())); }
        Ok(r.stdout)
    }

    fn remote_checked(&self, op: &str, command: &str, ms: u64) -> Result<String, String> {
        let command = if self.state.borrow()["root"] == true { format!("su -c {}", q(command)) } else { command.into() };
        self.checked(op, &command, ms)
    }

    pub fn hw<T>(&self, result: Result<T, String>) -> Result<T, String> {
        match self.shell_error.borrow_mut().take() { Some(e) => Err(e), None => result }
    }

    fn controls_ready(&self) -> Result<(), String> {
        if self.state.borrow()["control_command_pending"] != false {
            return Err("hardware write completion is unknown; remote control process needs independent reconciliation before further controls or recovery completion".into());
        }
        Ok(())
    }

    fn shell_result(&self, result: Result<String, String>) -> String {
        match result {
            Ok(s) => s,
            Err(e) => { if self.shell_error.borrow().is_none() { *self.shell_error.borrow_mut() = Some(e); } String::new() }
        }
    }

    pub fn remote(&self) -> String { self.state.borrow()["remote"].as_str().unwrap().into() }
    pub fn workload_uid(&self) -> &'static str { if self.state.borrow()["root"] == true { "root" } else { "shell" } }

    fn push(&self, path: &Path, remote: &str) -> Result<(), String> {
        let r = self.run("push", &["push".into(), path.to_string_lossy().into(), remote.into()], 30_000)?;
        if r.code != 0 { return Err(format!("push {}: exit {} {}", path.display(), r.code, r.stderr.trim())); }
        Ok(())
    }

    fn owned(&self) -> String {
        let s = self.state.borrow();
        format!("test \"$(cat {}/owner)\" = {} && test \"$(cat /proc/sys/kernel/random/boot_id)\" = {}",
            q(s["remote"].as_str().unwrap()), q(&format!("{} {}", s["token"].as_str().unwrap(), s["boot_id"].as_str().unwrap_or(""))),
            q(s["boot_id"].as_str().unwrap_or("")))
    }

    pub fn deploy(&self, bin: &str, model: &str, workload: &str, sampler: &str) -> Result<(), String> {
        self.hold()?;
        self.state.borrow_mut()["stage"] = json!("install_pending"); self.save()?;
        let remote = self.remote();
        let s = self.state.borrow();
        let owner = format!("{} {}", s["token"].as_str().unwrap(), s["boot_id"].as_str().ok_or("missing boot identity")?);
        drop(s);
        self.checked("install", &format!("set -eu; command -v setsid; command -v timeout; test ! -e {d}; mkdir {d}; printf '%s\\n' {owner} > {d}/owner; sync", d=q(&remote), owner=q(&owner)), 15_000)?;
        for (name, text) in [("worker.sh", WORKER), ("bootstrap.sh", BOOTSTRAP), ("workload.sh", workload), ("sample_env.sh", sampler)] {
            let path = self.dir.join(name);
            crate::confirmation_transport::new_file(&path, text.as_bytes())?;
            self.push(&path, &format!("{remote}/{name}"))?;
        }
        self.push(Path::new(bin), &format!("{remote}/benchmark_model"))?;
        self.push(Path::new(model), &format!("{remote}/model.tflite"))?;
        self.checked("executable", &format!("{} && chmod 700 {}/benchmark_model", self.owned(), q(&remote)), 8000)?;
        self.state.borrow_mut()["stage"] = json!("installed"); self.save()
    }

    fn worker(&self, mode: &str, run: &str, ms: u64) -> Result<String, String> {
        let s = self.state.borrow();
        let command = format!("setsid sh {}/worker.sh {} {} {} {} {}", q(s["remote"].as_str().unwrap()),
            q(s["remote"].as_str().unwrap()), q(s["token"].as_str().unwrap()), q(s["boot_id"].as_str().ok_or("missing boot identity")?), q(mode), q(run));
        drop(s);
        self.remote_checked(mode, &command, ms)
    }

    pub fn round(&self, n: u32) -> Result<(String, String, i32), String> {
        self.state.borrow_mut()["stage"] = json!("running"); self.save()?;
        let run = format!("round{n}");
        let done = self.worker("bench", &run, 200_000)?;
        if done.trim() != "BENCH_COMPLETE" { return Err("remote bench lifecycle receipt missing".into()); }
        let get = |name| self.remote_checked("round_evidence", &format!("{} && cat {}/{run}/{name}", self.owned(), q(&self.remote())), 8000);
        if get("quiescence.txt")?.trim() != "OWNED_GROUPS_ABSENT" { return Err("bench processes not confirmed stopped".into()); }
        let code = get("workload_exit.txt")?.trim().parse::<i32>().map_err(|_| "invalid workload exit receipt")?;
        Ok((get("workload.log")?, get("sampler.log")?, code))
    }

    fn close_remote(&self) -> Result<(), String> {
        let remote = self.remote();
        let closed = self.remote_checked("closure_status", &format!("{} && if [ -d {}/session.closed ]; then echo CLOSED; else echo OPEN; fi", self.owned(), q(&remote)), 8000)?;
        match closed.trim() {
            "OPEN" => {
                let run = format!("close{}", std::process::id());
                if self.worker("reconcile_bench", &run, 30_000)?.trim() != "BENCH_RECONCILE_COMPLETE" {
                    return Err("bench session close receipt missing".into());
                }
            }
            "CLOSED" => {},
            _ => return Err("unknown remote session closure state".into()),
        }
        if self.worker("verify_bench", "verify", 15_000)?.trim() != "BENCH_CLOSED_QUIESCENT" {
            return Err("closed bench session is not verifiably quiescent".into());
        }
        if self.state.borrow()["keep"] != true {
            self.remote_checked("remove_inputs", &format!("{} && rm -f {d}/model.tflite {d}/benchmark_model && test ! -e {d}/model.tflite && test ! -e {d}/benchmark_model", self.owned(), d=q(&remote)), 8000)?;
        }
        Ok(())
    }

    /// Experiment outcome and restoration outcome are independent. Never clear a
    /// hold merely because a benchmark produced a result or a transport exited.
    pub fn finish(&self, experiment_error: Option<&str>) -> Result<Value, String> {
        let result: Result<(bool, Option<bool>), String> = (|| {
            self.controls_ready()?;
            if self.state.borrow()["stage"] == "install_pending" {
                return Err("bench installation completion is unknown; reconcile deployment before completing recovery".into());
            }
            let deployed = self.state.borrow()["stage"] != "prepared";
            if deployed { self.close_remote()?; }
            let path = self.state_path()?;
            let present = match fs::symlink_metadata(&path) {
                Ok(_) => true,
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => false,
                Err(e) => return Err(format!("read recovery state {}: {e}", path.display())),
            };
            let controls = if present {
                self.hold()?;
                let actual_boot = self.checked("recovery_identity", "cat /proc/sys/kernel/random/boot_id", 8000)?;
                if self.state.borrow()["boot_id"].as_str() != Some(actual_boot.trim()) { return Err("device boot changed; old control snapshot cannot be applied automatically".into()); }
                self.hw(hwcond::recover_stale_lock("bench", self, &path))?
            } else { None };
            if controls == Some(false) { return Err("bench frequency restoration not confirmed".into()); }
            Ok((deployed, controls))
        })();
        let name = format!("recovery-{}-{}.json", std::process::id(), chrono::Utc::now().timestamp_nanos_opt().unwrap());
        let evidence = self.dir.join(&name);
        let (status, scope) = match &result {
            Ok((true, _)) => ("verified", "bench_controls_owned_processes_and_input_policy"),
            Ok((false, _)) => ("verified", "frequency_controls_only"),
            Err(_) => ("required", "bench_session"),
        };
        let mut value = json!({"schema":"phonefarm/bench-recovery@1", "status":status, "scope":scope,
            "serial":self.lease.borrow().serial, "session":self.dir, "experiment_error":experiment_error,
            "error":result.as_ref().err(), "controls_recovered":result.as_ref().ok().and_then(|v| v.1),
            "remote_session_closed":result.as_ref().is_ok_and(|v| v.0),
            "inputs_retained":self.state.borrow()["keep"], "evidence_origin":self.state.borrow()["evidence_origin"],
            "workload_uid":self.workload_uid(), "evidence":evidence, "at":chrono::Utc::now().to_rfc3339()});
        crate::confirmation_transport::new_file(&evidence, serde_json::to_string_pretty(&value).unwrap().as_bytes())?;
        if let Err(e) = result {
            self.hold()?;
            return Err(format!("{e}; recovery evidence {}", evidence.display()));
        }
        self.lease.borrow_mut().complete_recovery(scope, self.state.borrow()["evidence_origin"].as_str().unwrap(), &[&evidence])?;
        value["status"] = json!("reported_complete");
        Ok(value)
    }
}

impl Shell for Session {
    fn shell(&self, command: &str, ms: u64) -> String {
        // hwcond probes tolerate unavailable optional nodes/root and judge the
        // returned snapshot. Keep exit codes in the journal; lifecycle failures
        // still propagate through hw(), including timeouts and unknown groups.
        self.shell_result(self.controls_ready().and_then(|_| {
            self.run("hwcond", &["shell".into(), command.into()], ms).map(|r| r.stdout)
        }))
    }

    fn shell_write(&self, command: &str, ms: u64) -> String {
        self.shell_result((|| {
            self.controls_ready()?;
            self.hold()?;
            // Publish before launching: killing ADB cannot establish that a
            // remote su command has stopped. A crash/timeout keeps this sticky,
            // including across --recover; ordinary readback cannot clear it.
            self.state.borrow_mut()["control_command_pending"] = json!(true);
            self.save()?;
            let out = self.checked("hwcond_write", command, ms)?;
            self.state.borrow_mut()["control_command_pending"] = json!(false);
            if let Err(e) = self.save() {
                self.state.borrow_mut()["control_command_pending"] = json!(true);
                return Err(e);
            }
            Ok(out)
        })())
    }
}
