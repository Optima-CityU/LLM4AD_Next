//! Fresh single-APK installation. Committed packages are intentionally retained.
use crate::{
    device_registry::DeviceBinding,
    devlease::Lease,
    game_deploy::{self, InstallRequest, INSTALL_SCOPE},
    game_deploy_session::{digest, package_identity, q, valid_boot, Remote},
    game_deploy_transport::{ShellStderr, Transport, TransportError},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::BTreeSet,
    fs::File,
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
};

pub(crate) const SCHEMA: &str = "phonefarm/game-install-session@1";
static INTERRUPTED: AtomicBool = AtomicBool::new(false);
extern "C" fn interrupt(_: libc::c_int) {
    INTERRUPTED.store(true, Ordering::SeqCst);
}
fn now() -> String {
    chrono::Utc::now().to_rfc3339()
}
fn stage(token: &str) -> String {
    format!("/data/local/tmp/pf-install-{token}")
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct State {
    schema: String,
    request: InstallRequest,
    token: String,
    boot_id: Option<String>,
    phase: String,
    stage_attempted: bool,
    write_pending: Option<String>,
    transport_uncertain: bool,
    create_attempted: bool,
    session_id: Option<u32>,
    session_abandoned: bool,
    commit_attempted: bool,
    commit_confirmed: bool,
    checks: Value,
    source_session: Option<PathBuf>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    device_binding: Option<DeviceBinding>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    lease_scope: Option<String>,
}
impl State {
    fn validate(&self) -> Result<(), String> {
        self.request.validate()?;
        game_deploy::validate_scope(
            self.device_binding.as_ref(),
            self.lease_scope.as_deref(),
            &self.request.serial,
        )?;
        if self.schema != SCHEMA
            || self.token.is_empty()
            || self.token.len() > 128
            || !self
                .token
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
            || !matches!(
                self.phase.as_str(),
                "prepared" | "checked" | "applying" | "committed" | "settled"
            )
            || !self.checks.is_object()
            || self.boot_id.as_ref().is_some_and(|v| !valid_boot(v))
            || self
                .session_id
                .is_some_and(|id| id == 0 || id > i32::MAX as u32)
            || (self.session_id.is_some() && !self.create_attempted)
            || (self.create_attempted && !self.stage_attempted)
            || (self.commit_attempted && (self.session_id.is_none() || self.session_abandoned))
            || (self.commit_confirmed && !self.commit_attempted)
            || (self.phase == "committed" && !self.commit_confirmed)
            || (matches!(self.phase.as_str(), "prepared" | "checked") && self.stage_attempted)
            || (self.session_abandoned && self.session_id.is_none())
            || (self.stage_attempted && self.boot_id.is_none())
        {
            return Err("invalid installation session identity or state".into());
        }
        Ok(())
    }
}
pub(crate) struct Session {
    lease: Lease,
    state: State,
    dir: PathBuf,
    out: PathBuf,
    transport: Transport,
    // Only this live creator may abandon a non-committed session. A recovered
    // numeric session ID alone is not proof of its present service-side owner.
    created_here: bool,
}
impl Session {
    pub(crate) fn new(lease: Lease, request: InstallRequest, out: &Path) -> Result<Self, String> {
        let token = lease.token().to_owned();
        let binding = lease.device_binding().cloned();
        Self::from_state(
            lease,
            State {
                schema: SCHEMA.into(),
                request,
                token,
                boot_id: None,
                phase: "prepared".into(),
                stage_attempted: false,
                write_pending: None,
                transport_uncertain: false,
                create_attempted: false,
                session_id: None,
                session_abandoned: false,
                commit_attempted: false,
                commit_confirmed: false,
                checks: json!({}),
                source_session: None,
                lease_scope: binding.as_ref().map(|_| "device".into()),
                device_binding: binding,
            },
            out,
        )
    }
    fn from_state(lease: Lease, state: State, out: &Path) -> Result<Self, String> {
        state.validate()?;
        if lease.device_binding() != state.device_binding.as_ref() {
            return Err("lease device scope differs from saved transaction".into());
        }
        let dir = out.join("game-deploy");
        let transport = Transport::new(
            &state.request.serial,
            &state.token,
            &dir,
            ShellStderr::Merged,
        )?;
        std::fs::create_dir(&dir).map_err(|e| e.to_string())?;
        File::open(&dir)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
        File::open(out)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
        let session = Self {
            lease,
            state,
            dir,
            out: out.to_owned(),
            transport,
            created_here: false,
        };
        session.save()?;
        Ok(session)
    }
    fn save(&self) -> Result<(), String> {
        crate::devlease::durable_replace(
            &self.dir.join("session.json"),
            &serde_json::to_vec_pretty(&self.state).map_err(|e| e.to_string())?,
        )
    }
    fn uncertain(&mut self, error: &str) -> String {
        self.state.transport_uncertain = true;
        let hold = self.lease.hold_for_recovery(&self.dir).err();
        let save = self.save().err();
        format!(
            "{error}{}{}",
            hold.map(|e| format!("; retain: {e}")).unwrap_or_default(),
            save.map(|e| format!("; state: {e}")).unwrap_or_default()
        )
    }
    fn transport_ready(&self) -> Result<(), String> {
        if self.state.transport_uncertain {
            return Err("installation transport uncertain; further device commands refused".into());
        }
        Ok(())
    }
    fn transport_error(&mut self, error: TransportError) -> String {
        if error.uncertain {
            self.uncertain(&error.message)
        } else {
            error.message
        }
    }
    fn raw(
        &mut self,
        operation: &str,
        args: Vec<String>,
        seconds: u64,
    ) -> Result<crate::lease_command::Output, String> {
        self.transport_ready()?;
        self.transport
            .raw(&self.lease, operation, args, seconds)
            .map_err(|error| self.transport_error(error))
    }
    fn shell(&mut self, operation: &str, command: &str, seconds: u64) -> Result<Remote, String> {
        self.transport_ready()?;
        self.transport
            .shell(&self.lease, operation, command, seconds)
            .map_err(|error| self.transport_error(error))
    }
    fn read(&mut self, operation: &str, command: &str) -> Result<String, String> {
        let output = self.shell(operation, command, 30)?;
        if output.code != 0 {
            return Err(format!(
                "{operation} failed on device (exit {})",
                output.code
            ));
        }
        Ok(output.stdout.trim().to_owned())
    }
    fn intent(&mut self, operation: &str) -> Result<(), String> {
        self.lease.hold_for_recovery(&self.dir)?;
        self.state.write_pending = Some(operation.into());
        self.save()
    }
    fn clear_pending(&mut self) -> Result<(), String> {
        let pending = self.state.write_pending.take();
        if let Err(e) = self.save() {
            self.state.write_pending = pending;
            return Err(e);
        }
        Ok(())
    }
    fn write(&mut self, operation: &str, command: &str, seconds: u64) -> Result<Remote, String> {
        self.intent(operation)?;
        let output = self.shell(operation, command, seconds)?;
        self.clear_pending()?;
        Ok(output)
    }
    fn checked_write(&mut self, operation: &str, command: &str) -> Result<(), String> {
        let output = self.write(operation, command, 30)?;
        if output.code != 0 {
            return Err(format!(
                "{operation} failed on device (exit {})",
                output.code
            ));
        }
        Ok(())
    }
    fn absence(&mut self) -> Result<(), String> {
        let packages = self.read("package_absence", "pm list packages -u --user all")?;
        require_absent(&packages, &self.state.request.recipe.package)?;
        self.state.checks["package_absence"] = json!({"status":"verified","including_retained":true,"users":"all","observed_at":now()});
        self.save()
    }
    fn preflight(&mut self) -> Result<(), String> {
        let metadata = crate::apk_metadata::inspect(
            &self.lease,
            &self.dir.join("apk-metadata"),
            &self.state.request.assets[0],
        )?;
        if metadata["package"] != self.state.request.recipe.package
            || metadata["version_code"].as_u64() != Some(self.state.request.recipe.version_code)
            || metadata["single_base_apk"] != true
            || metadata["shared_uid"] != false
            || metadata["test_only"] != false
        {
            return Err("APK metadata does not match the declared fresh single-APK recipe".into());
        }
        self.state.checks["apk_metadata"] = metadata.clone();
        self.save()?;
        if self.read("current_user", "am get-current-user")? != "0" {
            return Err("only Android user 0 is supported".into());
        }
        let boot = self.read("boot_id", "cat /proc/sys/kernel/random/boot_id")?;
        if !valid_boot(&boot) {
            return Err("device boot identity unavailable".into());
        }
        self.state.boot_id = Some(boot.clone());
        let sdk = self
            .read("device_sdk", "getprop ro.build.version.sdk")?
            .parse::<u64>()
            .map_err(|_| "device SDK unavailable")?;
        if sdk
            < metadata["min_sdk"]
                .as_u64()
                .ok_or("APK minSDK unavailable")?
        {
            return Err("APK minSDK exceeds device SDK".into());
        }
        let abi_text = self.read("device_abis", "getprop ro.product.cpu.abilist")?;
        let abis: Vec<&str> = abi_text.split(',').collect();
        if abis.iter().any(|v| {
            v.is_empty()
                || !v
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
        }) {
            return Err("device ABI list unavailable".into());
        }
        let apk_abis = metadata["abis"]
            .as_array()
            .ok_or("APK ABI metadata unavailable")?;
        if !apk_abis.is_empty()
            && !apk_abis
                .iter()
                .any(|v| v.as_str().is_some_and(|s| abis.contains(&s)))
        {
            return Err("APK native ABI does not match device".into());
        }
        let help = self.read("package_manager_capabilities", "pm help")?;
        require_installer(&help)?;
        self.state.checks["device"] = json!({"user":0,"boot_id":boot,"sdk":sdk,"abis":abis});
        self.state.checks["installer"] =
            json!({"no_replace_flag":"-R","explicit_session_commands":true});
        self.absence()?;
        self.read("install_stage_parent","test -d /data/local/tmp && test ! -L /data/local/tmp && test -r /data/local/tmp && test -x /data/local/tmp && test -w /data/local/tmp")?;
        self.read(
            "install_stage_absent",
            &format!(
                "test ! -e {0} && test ! -L {0}",
                q(&stage(&self.state.token))
            ),
        )?;
        self.save()
    }
    fn boot(&mut self) -> Result<(), String> {
        let current = self.read("recovery_boot", "cat /proc/sys/kernel/random/boot_id")?;
        if self.state.boot_id.as_deref() != Some(current.as_str()) {
            return Err("device rebooted; installation reconciliation requires review".into());
        }
        Ok(())
    }
    fn owner(&mut self) -> Result<(), String> {
        let root = q(&stage(&self.state.token));
        let marker = q(&format!("{}/.phonefarm-owner", stage(&self.state.token)));
        self.read("install_stage_owner",&format!("test -d {root} && test ! -L {root} && test -f {marker} && test ! -L {marker} && test \"$(cat {marker})\" = {}",q(&self.state.token)))?;
        Ok(())
    }
    fn apply(&mut self) -> Result<(), String> {
        if INTERRUPTED.load(Ordering::SeqCst) {
            return Err("installation interrupted before first write".into());
        }
        self.state.phase = "applying".into();
        self.state.stage_attempted = true;
        self.lease.hold_for_recovery(&self.dir)?;
        self.save()?;
        let root = stage(&self.state.token);
        self.checked_write(
            "create_install_stage",
            &format!(
                "mkdir {} && (set -C; printf '%s\\n' {} > {})",
                q(&root),
                q(&self.state.token),
                q(&format!("{root}/.phonefarm-owner"))
            ),
        )?;
        self.owner()?;
        let asset = self.state.request.assets[0].clone();
        let source = game_deploy::source_file(&asset)?;
        let before = source.metadata().map_err(|e| e.to_string())?;
        let temp = format!("{root}/base.apk.part");
        let apk = format!("{root}/base.apk");
        self.intent("push_apk")?;
        let pushed = self.raw(
            "push_apk",
            vec![
                "push".into(),
                asset.path.to_string_lossy().into_owned(),
                temp.clone(),
            ],
            1800,
        )?;
        if pushed.code != 0 {
            return Err(self.uncertain("ADB push did not confirm APK completion"));
        }
        self.clear_pending()?;
        let after = std::fs::symlink_metadata(&asset.path).map_err(|e| e.to_string())?;
        if !after.is_file()
            || before.dev() != after.dev()
            || before.ino() != after.ino()
            || before.len() != after.len()
            || before.mtime() != after.mtime()
            || before.mtime_nsec() != after.mtime_nsec()
        {
            return Err("APK cache source changed while pushing".into());
        }
        let size = self.read(
            "apk_receiver_size",
            &format!("test ! -L {0} && test -f {0} && wc -c < {0}", q(&temp)),
        )?;
        if size.parse::<u64>().ok() != Some(asset.size) {
            return Err("received APK size mismatch".into());
        }
        let hash = self.read("apk_receiver_sha256", &format!("sha256sum {}", q(&temp)))?;
        if digest(&hash)? != asset.sha256 {
            return Err("received APK SHA-256 mismatch".into());
        }
        self.checked_write(
            "publish_apk",
            &format!(
                "test ! -e {0} && test ! -L {0} && mv {1} {0}",
                q(&apk),
                q(&temp)
            ),
        )?;
        if INTERRUPTED.load(Ordering::SeqCst) {
            return Err("installation interrupted before PackageInstaller session".into());
        }
        self.state.create_attempted = true;
        self.save()?;
        let created = self.write(
            "install_create",
            &format!(
                "pm install-create -R --user 0 --pkg {} -S {}",
                q(&self.state.request.recipe.package),
                asset.size
            ),
            60,
        )?;
        if created.code != 0 {
            return Err(
                "PackageInstaller create result does not establish an owned session".into(),
            );
        }
        let id = parse_session_id(&created.stdout)?;
        self.state.session_id = Some(id);
        self.created_here = true;
        self.save()?;
        if INTERRUPTED.load(Ordering::SeqCst) {
            return Err("installation interrupted after session creation".into());
        }
        let written = self.write(
            "install_write",
            &format!(
                "pm install-write -S {} {id} base.apk {}",
                asset.size,
                q(&apk)
            ),
            1800,
        )?;
        if written.code != 0
            || written.stdout.trim() != format!("Success: streamed {} bytes", asset.size)
        {
            return Err("PackageInstaller did not confirm all APK bytes".into());
        }
        if INTERRUPTED.load(Ordering::SeqCst) {
            return Err("installation interrupted before commit".into());
        }
        self.absence()?;
        self.state.commit_attempted = true;
        self.save()?;
        let committed = self.write("install_commit", &format!("pm install-commit {id}"), 300)?;
        if committed.code != 0 || committed.stdout.trim() != "Success" {
            return Err("PackageInstaller commit result unknown or not confirmed; no retry or uninstall permitted".into());
        }
        self.state.commit_confirmed = true;
        self.state.phase = "committed".into();
        self.save()?;
        self.verify_installed()?;
        self.cleanup_stage()?;
        self.state.phase = "settled".into();
        self.save()
    }
    fn verify_installed(&mut self) -> Result<(), String> {
        if !self.state.commit_confirmed {
            return Err("no confirmed PackageInstaller commit".into());
        }
        self.boot()?;
        let package = self.state.request.recipe.package.clone();
        let paths = self.read(
            "installed_apk_path",
            &format!("pm path --user 0 {}", q(&package)),
        )?;
        let lines: Vec<_> = paths.lines().collect();
        let apk = lines
            .first()
            .and_then(|v| v.strip_prefix("package:"))
            .filter(|p| {
                p.starts_with('/') && p.ends_with("/base.apk") && !p.chars().any(char::is_control)
            });
        if lines.len() != 1 || apk.is_none() {
            return Err("installed result is not one base APK".into());
        }
        let apk = apk.unwrap().to_owned();
        let info = self.read(
            "installed_package_identity",
            &format!("dumpsys package {}", q(&package)),
        )?;
        let (version, uid) = package_identity(&info)?;
        if version != self.state.request.recipe.version_code {
            return Err("installed versionCode differs from committed request".into());
        }
        let sha = self.read("installed_apk_sha256", &format!("sha256sum {}", q(&apk)))?;
        if digest(&sha)? != self.state.request.recipe.apk_sha256 {
            return Err("installed APK SHA-256 differs from committed request".into());
        }
        self.state.checks["installed"] = json!({"status":"verified","package":package,"version_code":version,"uid":uid,"apk_path":apk,
            "apk_sha256":self.state.request.recipe.apk_sha256,"observed_at":now(),"signature_verification":"package_installer_at_commit","independent_certificate_audit":"not_performed"});
        self.save()
    }
    fn cleanup_stage(&mut self) -> Result<(), String> {
        if !self.state.stage_attempted {
            return Ok(());
        }
        self.boot()?;
        let root = stage(&self.state.token);
        let absent = self.shell(
            "install_stage_absent",
            &format!("test ! -e {0} && test ! -L {0}", q(&root)),
            30,
        )?;
        if absent.code == 0 {
            return Ok(());
        }
        self.owner()?;
        let listing = self.read(
            "install_stage_inventory",
            &format!("cd {} && find . -mindepth 1 -print", q(&root)),
        )?;
        let entries: BTreeSet<_> = listing
            .lines()
            .map(|v| v.strip_prefix("./").unwrap_or(v))
            .collect();
        if !entries.contains(".phonefarm-owner")
            || entries
                .iter()
                .any(|v| !matches!(*v, ".phonefarm-owner" | "base.apk" | "base.apk.part"))
        {
            return Err("installation stage contains foreign entries".into());
        }
        for entry in entries {
            let file = q(&format!("{root}/{entry}"));
            self.read(
                "install_stage_type",
                &format!("test -f {file} && test ! -L {file}"),
            )?;
        }
        for name in ["base.apk.part", "base.apk"] {
            self.owner()?;
            let file = q(&format!("{root}/{name}"));
            self.checked_write("remove_install_apk",&format!("if test -e {file} || test -L {file}; then test -f {file} && test ! -L {file} && rm {file}; fi"))?;
        }
        self.owner()?;
        self.checked_write(
            "remove_install_stage",
            &format!(
                "rm {} && rmdir {}",
                q(&format!("{root}/.phonefarm-owner")),
                q(&root)
            ),
        )?;
        self.read(
            "install_stage_removed",
            &format!("test ! -e {0} && test ! -L {0}", q(&root)),
        )?;
        Ok(())
    }
    fn settle_uncommitted(&mut self) -> Result<(), String> {
        self.safe_to_reconcile()?;
        if self.state.commit_attempted {
            return Err(
                "commit was attempted; installation result requires explicit review".into(),
            );
        }
        if self.state.create_attempted && !self.state.session_abandoned {
            let id = self.state.session_id.ok_or(
                "install-create did not leave an unambiguous session ID; cannot guess or abandon",
            )?;
            if !self.created_here {
                return Err("uncommitted installer session belongs to a previous process; current ownership cannot be verified, abandon refused".into());
            }
            let abandoned =
                self.write("install_abandon", &format!("pm install-abandon {id}"), 60)?;
            if abandoned.code != 0 || abandoned.stdout.trim() != "Success" {
                return Err("PackageInstaller did not confirm abandonment".into());
            }
            self.state.session_abandoned = true;
            self.save()?;
        }
        self.cleanup_stage()?;
        self.state.phase = "settled".into();
        self.save()
    }
    fn safe_to_reconcile(&self) -> Result<(), String> {
        if self.state.transport_uncertain || self.state.write_pending.is_some() {
            return Err(
                "unknown or pending installation command; automatic reconciliation refused".into(),
            );
        }
        Ok(())
    }
    fn report(&self, phase: &str, error: Option<&str>, status: &str) -> Value {
        let mut value = game_deploy::install_report(&self.state.request);
        game_deploy::report_binding(&mut value, self.state.device_binding.as_ref());
        value["phase"] = json!(phase);
        value["session_path"] = json!("game-deploy");
        value["error"] = json!(error);
        value["checks"] = self.state.checks.clone();
        if self.state.checks["apk_metadata"].is_object() {
            value["apk"] = json!({"status":"verified","scope":"host_metadata_only",
                "metadata":self.state.checks["apk_metadata"]});
        }
        if self.state.checks["installed"].is_object() {
            value["installed"] = self.state.checks["installed"].clone();
        }
        value["installer"] = json!({"session_id":self.state.session_id,"abandoned":self.state.session_abandoned,
            "commit_attempted":self.state.commit_attempted,"commit_confirmed":self.state.commit_confirmed,"package_retained":self.state.commit_confirmed});
        value["recovery"] = json!({"status":status,"scope":INSTALL_SCOPE,"source_session":self.state.source_session,
            "context_moved":self.state.source_session.as_ref().map(|_|true),"device_restored":"not_claimed"});
        value
    }
    fn finish(
        &mut self,
        phase: &str,
        error: Option<&str>,
        settled: bool,
        code: i32,
    ) -> Result<(Value, i32), String> {
        let mut value = self.report(phase, error, if settled { "verified" } else { "required" });
        game_deploy::save_report(&self.out, &value)?;
        if settled {
            if let Err(e) = self.lease.complete_recovery(
                INSTALL_SCOPE,
                "device",
                &[&self.out.join("deployment.json")],
            ) {
                let _ = self.lease.hold_for_recovery(&self.dir);
                value["phase"] = json!("recovery_required");
                value["error"] = json!(e);
                value["recovery"]["status"] = json!("required");
                game_deploy::save_report(&self.out, &value)?;
                return Ok((value, 2));
            }
        }
        Ok((value, code))
    }
    pub(crate) fn execute(mut self) -> Result<(Value, i32), String> {
        unsafe {
            libc::signal(libc::SIGINT, interrupt as *const () as libc::sighandler_t);
        }
        if let Err(error) = self.preflight() {
            let settled = self.safe_to_reconcile().is_ok();
            return self.finish(
                if settled {
                    "preflight_failed"
                } else {
                    "recovery_required"
                },
                Some(&error),
                settled,
                2,
            );
        }
        if self.state.request.mode == "check" {
            self.state.phase = "checked".into();
            self.save()?;
            return self.finish("checked", None, true, 0);
        }
        match self.apply() {
            Ok(()) => self.finish("committed", None, true, 0),
            Err(error) => {
                let restore = if self.state.commit_attempted {
                    Err("commit was attempted; retain package and pending evidence for explicit reconciliation".into())
                } else {
                    self.settle_uncommitted()
                };
                match restore {
                    Ok(()) => self.finish("restored", Some(&error), true, 2),
                    Err(recovery) => {
                        let _ = self.lease.hold_for_recovery(&self.dir);
                        self.finish(
                            "recovery_required",
                            Some(&format!("{error}; recovery: {recovery}")),
                            false,
                            2,
                        )
                    }
                }
            }
        }
    }
    pub(crate) fn recover(source: &Path, serial: &str, out: &Path) -> Result<(Value, i32), String> {
        let source = source.canonicalize().map_err(|e| e.to_string())?;
        let bytes = crate::confirmation_transport::bounded(&source.join("session.json"), 131072)?;
        let mut state: State = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
        state.validate()?;
        if state.request.serial != serial {
            return Err("installation recovery serial differs from source".into());
        }
        let lease = match game_deploy::acquire_context(
            &source,
            serial,
            &bytes,
            state.device_binding.as_ref(),
        ) {
            Ok(lease) => lease,
            Err(error) => return failed_recovery(&state, &source, out, &error, Some(false)),
        };
        state.source_session = Some(source.clone());
        let prior = state.clone();
        let mut session = match Self::from_state(lease, state, out) {
            Ok(v) => v,
            Err(e) => return failed_recovery(&prior, &source, out, &e, Some(false)),
        };
        if let Err(error) = session.lease.hold_for_recovery(&session.dir) {
            let moved = game_deploy::context_moved(&session.lease, serial, &source, &session.dir);
            return failed_recovery(&prior, &source, out, &error, moved);
        }
        let result = (|| {
            session.safe_to_reconcile()?;
            if session.state.commit_confirmed {
                session.verify_installed()?;
                session.cleanup_stage()?;
                session.state.phase = "settled".into();
                session.save()
            } else {
                session.settle_uncommitted()
            }
        })();
        match result {
            Ok(()) => {
                let phase = if session.state.commit_confirmed {
                    "committed"
                } else {
                    "restored"
                };
                session.finish(phase, None, true, 0)
            }
            Err(error) => session.finish("recovery_required", Some(&error), false, 2),
        }
    }
}
fn failed_recovery(
    state: &State,
    source: &Path,
    out: &Path,
    error: &str,
    moved: Option<bool>,
) -> Result<(Value, i32), String> {
    let mut value = game_deploy::install_report(&state.request);
    game_deploy::report_binding(&mut value, state.device_binding.as_ref());
    value["phase"] = json!("recovery_required");
    value["error"] = json!(error);
    value["session_path"] = if moved == Some(true) {
        json!("game-deploy")
    } else {
        Value::Null
    };
    value["recovery"] = json!({"status":"required","scope":INSTALL_SCOPE,"source_session":source,"context_moved":moved});
    if let Err(e) = game_deploy::save_report(out, &value) {
        value["report_write_error"] = json!(e);
    }
    Ok((value, 2))
}
fn require_absent(output: &str, package: &str) -> Result<(), String> {
    let mut android = false;
    for line in output.lines() {
        let name = line
            .strip_prefix("package:")
            .filter(|v| crate::graylayer::valid_package(v))
            .ok_or("package listing is incomplete or contains diagnostics")?;
        if name == package {
            return Err("package already installed or retained; replacement is forbidden".into());
        }
        android |= name == "android";
    }
    if !android {
        return Err("package listing has no Android framework sentinel; absence unknown".into());
    }
    Ok(())
}
fn require_installer(help: &str) -> Result<(), String> {
    let no_replace = help.lines().any(|line| {
        let line = line.trim();
        line.starts_with("-R:") && line.contains("disallow replacement of existing application")
    });
    if !no_replace
        || [
            "install-create",
            "install-write",
            "install-commit",
            "install-abandon",
        ]
        .iter()
        .any(|command| {
            !help
                .lines()
                .any(|line| line.trim_start().starts_with(command))
        })
    {
        return Err(
            "PackageManager does not explicitly advertise -R and required session commands".into(),
        );
    }
    Ok(())
}
fn parse_session_id(output: &str) -> Result<u32, String> {
    output
        .trim()
        .strip_prefix("Success: created install session [")
        .and_then(|v| v.strip_suffix(']'))
        .filter(|v| !v.is_empty() && v.bytes().all(|b| b.is_ascii_digit()))
        .and_then(|v| v.parse::<u32>().ok())
        .filter(|v| *v > 0 && *v <= i32::MAX as u32)
        .ok_or_else(|| "install-create did not return an unambiguous session ID".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn installation_admission_and_results_do_not_guess() {
        let help=" -R: disallow replacement of existing application\n install-create [-S]\n install-write\n install-commit\n install-abandon\n";
        require_installer(help).unwrap();
        assert!(require_installer(&help.replace("-R:", "-r:")).is_err());
        require_absent(
            "package:android\npackage:com.other.game",
            "com.example.game",
        )
        .unwrap();
        assert!(require_absent("", "com.example.game").is_err());
        assert!(require_absent(
            "package:android\nError: user access denied",
            "com.example.game"
        )
        .is_err());
        assert!(require_absent(
            "package:android\npackage:com.example.game",
            "com.example.game"
        )
        .is_err());
        assert_eq!(
            parse_session_id("Success: created install session [17]\n").unwrap(),
            17
        );
        assert!(parse_session_id("Warning\nSuccess: created install session [17]").is_err());
        assert!(parse_session_id("Success: created install session [-1]").is_err());
    }
}
