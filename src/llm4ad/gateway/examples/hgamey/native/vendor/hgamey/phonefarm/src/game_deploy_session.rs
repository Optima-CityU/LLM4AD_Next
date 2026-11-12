//! A new resource subtree is the entire device-side transaction. No APK,
//! preferences, existing saves or application lifecycle belong to this scope.
use crate::{
    device_registry::DeviceBinding,
    devlease::Lease,
    game_deploy::{self, Request, SCOPE},
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

const SCHEMA: &str = "phonefarm/game-deploy-session@1";
static INTERRUPTED: AtomicBool = AtomicBool::new(false);
extern "C" fn interrupt(_: libc::c_int) {
    INTERRUPTED.store(true, Ordering::SeqCst);
}
fn now() -> String {
    chrono::Utc::now().to_rfc3339()
}
pub(crate) use crate::game_deploy_transport::{parse_terminal, q, Remote};
fn parent(request: &Request) -> String {
    format!("/sdcard/Android/data/{}/files", request.recipe.package)
}
fn root(request: &Request, token: &str) -> String {
    format!("{}/pf-game-{token}", parent(request))
}
fn marker(root: &str) -> String {
    format!("{root}/.phonefarm-owner")
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct State {
    schema: String,
    request: Request,
    token: String,
    boot_id: Option<String>,
    remote_root: String,
    phase: String,
    write_pending: Option<String>,
    transport_uncertain: bool,
    checks: Value,
    application_access: Value,
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
            || self.remote_root != root(&self.request, &self.token)
            || !matches!(
                self.phase.as_str(),
                "prepared" | "applying" | "committed" | "restored" | "checked"
            )
            || !self.checks.is_object()
            || !self.application_access.is_object()
            || self.boot_id.as_ref().is_some_and(|s| !valid_boot(s))
        {
            return Err("invalid deployment session identity or state".into());
        }
        if self.phase != "prepared" && self.boot_id.is_none() {
            return Err("deployment session has no device boot identity".into());
        }
        Ok(())
    }
}

/// Read-only committed resource identity, borrowed by a later owner. This is
/// evidence, not recovery authority and not another resource Session.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Committed {
    pub(crate) request: Request,
    pub(crate) token: String,
    pub(crate) boot_id: String,
    pub(crate) remote_root: String,
}
impl Committed {
    pub(crate) fn from_evidence(report: &Value, bytes: &[u8]) -> Result<Self, String> {
        let state: State = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
        state.validate()?;
        let request = &state.request;
        if state.phase != "committed"
            || request.mode != "apply"
            || state.transport_uncertain
            || state.write_pending.is_some()
            || report["schema"] != game_deploy::REPORT_SCHEMA
            || report["phase"] != "committed"
            || report["node_id"] != request.node_id
            || report["game_id"] != request.game_id
            || report["version"] != request.version
            || report["serial"] != request.serial
            || report["installed"]["status"] != "verified"
            || report["installed"]["package"] != request.recipe.package
            || report["installed"]["version_code"] != request.recipe.version_code
            || report["installed"]["apk_sha256"] != request.recipe.apk_sha256
            || report["resources_deployed"]["status"] != "verified"
            || report["resources_deployed"]["root"] != state.remote_root
            || report["resources_deployed"]["files"] != json!(request.recipe.files)
            || report["recovery"]["status"] != "verified"
            || report["recovery"]["scope"] != SCOPE
            || report["recovery"]["transaction"] != "committed"
        {
            return Err(
                "source deployment report/state is not one settled committed transaction".into(),
            );
        }
        Ok(Self {
            request: state.request,
            token: state.token,
            boot_id: state
                .boot_id
                .ok_or("committed deployment has no boot identity")?,
            remote_root: state.remote_root,
        })
    }
    pub(crate) fn validate(&self) -> Result<(), String> {
        self.request.validate()?;
        if self.request.mode != "apply"
            || self.token.is_empty()
            || self.token.len() > 128
            || !self
                .token
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
            || !valid_boot(&self.boot_id)
            || self.remote_root != root(&self.request, &self.token)
        {
            return Err("invalid committed deployment identity".into());
        }
        Ok(())
    }
    pub(crate) fn verify(
        &self,
        mut read: impl FnMut(&str, &str) -> Result<String, String>,
    ) -> Result<u64, String> {
        self.validate()?;
        read("scene_resource_owner", &format!("test -d {0} && test ! -L {0} && test -f {1} && test ! -L {1} && test \"$(cat {1})\" = {2}", q(&self.remote_root), q(&marker(&self.remote_root)), q(&self.token)))?;
        let paths = read(
            "scene_resource_inventory",
            &format!("cd {} && find . -mindepth 1 -print", q(&self.remote_root)),
        )?;
        let actual: BTreeSet<_> = paths
            .lines()
            .map(|line| line.strip_prefix("./").unwrap_or(line).to_owned())
            .collect();
        if actual != expected_paths(&self.request, false) {
            return Err("source deployment has missing or foreign entries".into());
        }
        let dirs = directories(&self.request);
        for path in actual {
            let full = q(&format!("{}/{path}", self.remote_root));
            let kind = if dirs.contains(&path) { "d" } else { "f" };
            read(
                "scene_resource_type",
                &format!("test ! -L {full} && test -{kind} {full}"),
            )?;
        }
        let mut bytes = 0u64;
        for resource in &self.request.recipe.files {
            let asset = self.request.asset(&resource.asset_name);
            let path = q(&format!("{}/{}", self.remote_root, resource.path));
            if read("scene_resource_size", &format!("wc -c < {path}"))?
                .trim()
                .parse::<u64>()
                .ok()
                != Some(asset.size)
            {
                return Err("source deployment resource size changed".into());
            }
            if digest(&read(
                "scene_resource_sha256",
                &format!("sha256sum {path}"),
            )?)? != asset.sha256
            {
                return Err("source deployment resource SHA-256 changed".into());
            }
            bytes = bytes
                .checked_add(asset.size)
                .ok_or("resource size overflow")?;
        }
        Ok(bytes)
    }
}
pub(crate) fn valid_boot(value: &str) -> bool {
    value.len() == 36
        && value.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b == b'-'
            } else {
                b.is_ascii_hexdigit()
            }
        })
}
pub(crate) struct Session {
    lease: Lease,
    state: State,
    transport: Transport,
    dir: PathBuf,
    out: PathBuf,
}
impl Session {
    pub(crate) fn new(lease: Lease, request: Request, out: &Path) -> Result<Self, String> {
        let token = lease.token().to_owned();
        let binding = lease.device_binding().cloned();
        let state = State {
            schema: SCHEMA.into(),
            remote_root: root(&request, &token),
            request,
            token,
            boot_id: None,
            phase: "prepared".into(),
            write_pending: None,
            transport_uncertain: false,
            checks: json!({}),
            application_access: json!({"status":"unknown"}),
            source_session: None,
            lease_scope: binding.as_ref().map(|_| "device".into()),
            device_binding: binding,
        };
        Self::from_state(lease, state, out)
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
            ShellStderr::Separate,
        )?;
        std::fs::create_dir(&dir).map_err(|e| e.to_string())?;
        File::open(&dir)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
        File::open(out)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
        let value = Self {
            lease,
            state,
            transport,
            dir,
            out: out.to_owned(),
        };
        value.save()?;
        Ok(value)
    }
    fn save(&self) -> Result<(), String> {
        crate::devlease::durable_replace(
            &self.dir.join("session.json"),
            &serde_json::to_vec_pretty(&self.state).map_err(|e| e.to_string())?,
        )
    }
    fn uncertain(&mut self, reason: &str) -> String {
        self.state.transport_uncertain = true;
        // hold sets preserve_marker before checking ownership. Neither a failed
        // receipt nor a failed local state write may turn uncertainty into free.
        let hold = self.lease.hold_for_recovery(&self.dir).err();
        let save = self.save().err();
        format!(
            "{reason}{}{}",
            hold.map(|e| format!("; retain: {e}")).unwrap_or_default(),
            save.map(|e| format!("; state: {e}")).unwrap_or_default()
        )
    }
    fn transport_ready(&self) -> Result<(), String> {
        if self.state.transport_uncertain {
            return Err(
                "deployment transport is uncertain; no further device commands permitted".into(),
            );
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
        let result = self.shell(operation, command, 30)?;
        if result.code != 0 {
            return Err(format!(
                "{operation} failed on device (exit {})",
                result.code
            ));
        }
        Ok(result.stdout.trim().to_owned())
    }
    fn write(&mut self, operation: &str, command: &str) -> Result<(), String> {
        self.lease.hold_for_recovery(&self.dir)?;
        self.state.write_pending = Some(operation.into());
        self.save()?;
        let result = self.shell(operation, command, 30)?;
        self.clear_pending()?;
        if result.code != 0 {
            return Err(format!(
                "{operation} failed on device (exit {})",
                result.code
            ));
        }
        Ok(())
    }
    fn clear_pending(&mut self) -> Result<(), String> {
        let prior = self.state.write_pending.take();
        if let Err(e) = self.save() {
            self.state.write_pending = prior;
            return Err(e);
        }
        Ok(())
    }
    fn preflight(&mut self) -> Result<(), String> {
        if self.read("current_user", "am get-current-user")? != "0" {
            return Err("only Android user 0 is supported".into());
        }
        let boot = self.read("boot_id", "cat /proc/sys/kernel/random/boot_id")?;
        if !valid_boot(&boot) {
            return Err("device boot identity unavailable".into());
        }
        self.state.boot_id = Some(boot.clone());
        let package = self.state.request.recipe.package.clone();
        let paths = self.read("package_path", &format!("pm path {}", q(&package)))?;
        let lines: Vec<_> = paths.lines().collect();
        let apk = lines
            .first()
            .and_then(|s| s.strip_prefix("package:"))
            .filter(|s| {
                s.starts_with('/') && s.ends_with("/base.apk") && !s.chars().any(char::is_control)
            });
        if lines.len() != 1 || apk.is_none() {
            return Err(
                "installed engine must have exactly one base APK (split APKs unsupported)".into(),
            );
        }
        let apk = apk.unwrap().to_owned();
        let package_info = self.read(
            "package_identity",
            &format!("dumpsys package {}", q(&package)),
        )?;
        let (version, uid) = package_identity(&package_info)?;
        if version != self.state.request.recipe.version_code {
            return Err("installed engine versionCode mismatch".into());
        }
        let hash = self.read("apk_sha256", &format!("sha256sum {}", q(&apk)))?;
        if digest(&hash)? != self.state.request.recipe.apk_sha256 {
            return Err("installed engine APK SHA-256 mismatch".into());
        }
        let directory = parent(&self.state.request);
        self.read(
            "resource_parent",
            &format!(
                "test -d {0} && test ! -L {0} && test -r {0} && test -x {0} && test -w {0}",
                q(&directory)
            ),
        )?;
        self.read(
            "resource_absent",
            &format!("test ! -e {0} && test ! -L {0}", q(&self.state.remote_root)),
        )?;
        self.state.checks = json!({"android_user":0,"boot_id":boot,"package":package,"version_code":version,"uid":uid,"apk_path":apk,
            "apk_sha256":self.state.request.recipe.apk_sha256,"parent":directory,"parent_access":"shell_read_write","new_root_absent":true,"observed_at":now()});
        self.save()
    }
    fn identity(&mut self) -> Result<(), String> {
        let boot = self.read("recovery_boot", "cat /proc/sys/kernel/random/boot_id")?;
        if self.state.boot_id.as_deref() != Some(boot.as_str()) {
            return Err("device rebooted; resource recovery requires manual review".into());
        }
        let p = q(&parent(&self.state.request));
        self.read(
            "recovery_parent",
            &format!("test -d {p} && test ! -L {p} && test -r {p} && test -x {p}"),
        )?;
        Ok(())
    }
    fn owner(&mut self) -> Result<(), String> {
        let r = q(&self.state.remote_root);
        let m = q(&marker(&self.state.remote_root));
        let t = q(&self.state.token);
        self.read("resource_owner", &format!("test -d {r} && test ! -L {r} && test -f {m} && test ! -L {m} && test \"$(cat {m})\" = {t}"))?;
        Ok(())
    }
    fn inventory(&mut self, complete: bool) -> Result<(), String> {
        self.owner()?;
        let paths = self.read(
            "resource_inventory",
            &format!(
                "cd {} && find . -mindepth 1 -print",
                q(&self.state.remote_root)
            ),
        )?;
        let allowed = expected_paths(&self.state.request, !complete);
        let actual: BTreeSet<String> = paths
            .lines()
            .map(|line| line.strip_prefix("./").unwrap_or(line).to_owned())
            .collect();
        if actual.iter().any(|p| !allowed.contains(p)) || (complete && actual != allowed) {
            return Err(
                "resource directory has missing or foreign entries; automatic cleanup refused"
                    .into(),
            );
        }
        for path in actual {
            let full = q(&format!("{}/{path}", self.state.remote_root));
            let kind = if directories(&self.state.request).contains(&path) {
                "d"
            } else {
                "f"
            };
            self.read(
                "resource_type",
                &format!("test ! -L {full} && test -{kind} {full}"),
            )?;
        }
        Ok(())
    }
    fn verify_file(&mut self, path: &str, size: u64, sha: &str) -> Result<(), String> {
        let path = q(path);
        let bytes = self.read(
            "resource_size",
            &format!("test ! -L {path} && test -f {path} && wc -c < {path}"),
        )?;
        if bytes.parse::<u64>().ok() != Some(size) {
            return Err("received resource size mismatch".into());
        }
        let actual = self.read("resource_sha256", &format!("sha256sum {path}"))?;
        if digest(&actual)? != sha {
            return Err("received resource SHA-256 mismatch".into());
        }
        Ok(())
    }
    fn apply(&mut self) -> Result<(), String> {
        self.state.phase = "applying".into();
        self.save()?;
        let r = q(&self.state.remote_root);
        let m = q(&marker(&self.state.remote_root));
        let t = q(&self.state.token);
        self.write(
            "create_resource_root",
            &format!("mkdir {r} && (set -C; printf '%s\\n' {t} > {m})"),
        )?;
        self.owner()?;
        for directory in directories(&self.state.request) {
            self.owner()?;
            self.write(
                "create_resource_directory",
                &format!(
                    "mkdir {}",
                    q(&format!("{}/{directory}", self.state.remote_root))
                ),
            )?;
        }
        let files = self.state.request.recipe.files.clone();
        for (index, file) in files.iter().enumerate() {
            if INTERRUPTED.load(Ordering::SeqCst) {
                return Err("deployment interrupted before next resource".into());
            }
            self.owner()?;
            let asset = self.state.request.asset(&file.asset_name).clone();
            let source = game_deploy::source_file(&asset)?;
            let before = source.metadata().map_err(|e| e.to_string())?;
            let temp = format!("{}/.phonefarm-asset-{index}.part", self.state.remote_root);
            self.read(
                "resource_temp_absent",
                &format!("test ! -e {0} && test ! -L {0}", q(&temp)),
            )?;
            self.lease.hold_for_recovery(&self.dir)?;
            self.state.write_pending = Some("push_resource".into());
            self.save()?;
            let pushed = self.raw(
                "push_resource",
                vec![
                    "push".into(),
                    asset.path.to_string_lossy().into_owned(),
                    temp.clone(),
                ],
                1800,
            )?;
            if pushed.code != 0 {
                return Err(self.uncertain("ADB push did not confirm completion"));
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
                return Err("host cache source changed while pushing".into());
            }
            self.verify_file(&temp, asset.size, &asset.sha256)?;
            self.owner()?;
            let target = format!("{}/{}", self.state.remote_root, file.path);
            self.write(
                "publish_resource",
                &format!(
                    "test ! -e {0} && test ! -L {0} && mv {1} {0}",
                    q(&target),
                    q(&temp)
                ),
            )?;
        }
        self.inventory(true)?;
        let tests = self
            .state
            .request
            .recipe
            .files
            .iter()
            .map(|f| {
                format!(
                    "test -r {}",
                    q(&format!("{}/{}", self.state.remote_root, f.path))
                )
            })
            .collect::<Vec<_>>()
            .join(" && ");
        let access = self.shell(
            "application_uid_read",
            &format!(
                "run-as {} sh -c {}",
                q(&self.state.request.recipe.package),
                q(&tests)
            ),
            30,
        )?;
        self.state.application_access = json!({"status":"unknown","uid_readable":if access.code == 0 {Some(true)} else {None},
            "reason":if access.code == 0 {"UID readability only; application SELinux domain and engine consumption not verified"} else {"run-as unavailable or resources not readable by application UID"},"remote_exit":access.code});
        // Commit is durable before completion. A later recovery verifies this
        // intentional retained tree instead of deleting successfully deployed data.
        self.state.phase = "committed".into();
        self.save()
    }
    fn rollback(&mut self) -> Result<(), String> {
        if self.state.transport_uncertain || self.state.write_pending.is_some() {
            return Err("unknown or pending remote write; automatic recovery refused".into());
        }
        self.identity()?;
        let absent = self.shell(
            "resource_root_absent",
            &format!("test ! -e {0} && test ! -L {0}", q(&self.state.remote_root)),
            30,
        )?;
        if absent.code != 0 {
            self.inventory(false)?;
            let mut files = expected_paths(&self.state.request, true);
            let dirs = directories(&self.state.request);
            for dir in &dirs {
                files.remove(dir);
            }
            files.remove(".phonefarm-owner");
            for file in files {
                self.owner()?;
                let path = q(&format!("{}/{file}", self.state.remote_root));
                self.write("remove_resource_file", &format!("if test -e {path} || test -L {path}; then test ! -L {path} && test -f {path} && rm {path}; fi"))?;
            }
            let mut dirs: Vec<_> = dirs.into_iter().collect();
            dirs.sort_by_key(|d| std::cmp::Reverse(d.len()));
            for dir in dirs {
                self.owner()?;
                let path = q(&format!("{}/{dir}", self.state.remote_root));
                self.write("remove_resource_directory", &format!("if test -e {path} || test -L {path}; then test ! -L {path} && test -d {path} && rmdir {path}; fi"))?;
            }
            self.owner()?;
            self.write(
                "remove_resource_root",
                &format!(
                    "rm {} && rmdir {}",
                    q(&marker(&self.state.remote_root)),
                    q(&self.state.remote_root)
                ),
            )?;
        }
        self.read(
            "resource_root_removed",
            &format!("test ! -e {0} && test ! -L {0}", q(&self.state.remote_root)),
        )?;
        self.state.phase = "restored".into();
        self.save()
    }
    fn verify_committed(&mut self) -> Result<(), String> {
        if self.state.transport_uncertain || self.state.write_pending.is_some() {
            return Err("committed session has uncertain transport".into());
        }
        self.identity()?;
        self.inventory(true)?;
        for file in self.state.request.recipe.files.clone() {
            let asset = self.state.request.asset(&file.asset_name).clone();
            self.verify_file(
                &format!("{}/{}", self.state.remote_root, file.path),
                asset.size,
                &asset.sha256,
            )?;
        }
        Ok(())
    }
    fn report(&self, phase: &str, error: Option<&str>, recovery: &str) -> Value {
        let mut value = game_deploy::report(&self.state.request);
        game_deploy::report_binding(&mut value, self.state.device_binding.as_ref());
        value["phase"] = json!(phase);
        value["error"] = json!(error);
        value["session_path"] = json!("game-deploy");
        value["checks"] = self.state.checks.clone();
        value["application_access"] = self.state.application_access.clone();
        if self.state.checks.get("apk_sha256").is_some() {
            value["installed"] = json!({"status":"verified","package":self.state.request.recipe.package,"version_code":self.state.request.recipe.version_code,"apk_sha256":self.state.request.recipe.apk_sha256,"observed_at":self.state.checks["observed_at"]});
        }
        value["resources_deployed"] = if self.state.phase == "committed" && recovery != "required" {
            json!({"status":"verified","root":self.state.remote_root,"files":self.state.request.recipe.files,"content_verification":"device_receiver_sha256"})
        } else {
            json!({"status":"unknown"})
        };
        value["recovery"] = json!({"status":recovery,"scope":SCOPE,"source_session":self.state.source_session,
            "context_moved":self.state.source_session.as_ref().map(|_|true),"transaction":self.state.phase,"device_restored":"not_claimed"});
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
            let evidence = self.out.join("deployment.json");
            if let Err(e) = self.lease.complete_recovery(SCOPE, "device", &[&evidence]) {
                let _ = self.lease.hold_for_recovery(&self.dir);
                value["phase"] = json!("recovery_required");
                value["recovery"]["status"] = json!("required");
                value["error"] = json!(e);
                game_deploy::save_report(&self.out, &value)?;
                return Ok((value, 2));
            }
            // The persisted report proves the limited checks; reported_complete
            // is carried by the separately durable lease receipt.
        }
        Ok((value, code))
    }
    pub(crate) fn execute(mut self) -> Result<(Value, i32), String> {
        unsafe {
            libc::signal(libc::SIGINT, interrupt as *const () as libc::sighandler_t);
        }
        if let Err(error) = self.preflight() {
            let uncertain = self.state.transport_uncertain || self.state.write_pending.is_some();
            return self.finish(
                if uncertain {
                    "recovery_required"
                } else {
                    "preflight_failed"
                },
                Some(&error),
                !uncertain,
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
                if self.state.phase == "committed" {
                    let _ = self.lease.hold_for_recovery(&self.dir);
                    return self.finish("recovery_required", Some(&error), false, 2);
                }
                match self.rollback() {
                    Ok(()) => self.finish("restored", Some(&error), true, 2),
                    Err(restore) => {
                        let _ = self.lease.hold_for_recovery(&self.dir);
                        self.finish(
                            "recovery_required",
                            Some(&format!("{error}; recovery: {restore}")),
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
        let mut state: State = serde_json::from_slice(&bytes)
            .map_err(|e| format!("invalid deployment session: {e}"))?;
        state.validate()?;
        if state.request.serial != serial {
            return Err("recovery serial differs from source session".into());
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
            Ok(session) => session,
            Err(error) => return failed_recovery(&prior, &source, out, &error, Some(false)),
        };
        if let Err(error) = session.lease.hold_for_recovery(&session.dir) {
            // The new session was saved before reporting its recovery context.
            // Preserve the report failure and issue no device commands.
            let moved = game_deploy::context_moved(&session.lease, serial, &source, &session.dir);
            return failed_recovery(&prior, &source, out, &error, moved);
        }
        let result = match session.state.phase.as_str() {
            "committed" => session.verify_committed(),
            "checked" | "prepared"
                if !session.state.transport_uncertain && session.state.write_pending.is_none() =>
            {
                Ok(())
            }
            _ => session.rollback(),
        };
        match result {
            Ok(()) => {
                let phase = match session.state.phase.as_str() {
                    "prepared" => "preflight_failed".to_owned(),
                    phase => phase.to_owned(),
                };
                session.finish(&phase, None, true, 0)
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
    let mut report = game_deploy::report(&state.request);
    game_deploy::report_binding(&mut report, state.device_binding.as_ref());
    report["phase"] = json!("recovery_required");
    report["error"] = json!(error);
    report["session_path"] = if moved == Some(true) {
        json!("game-deploy")
    } else {
        Value::Null
    };
    report["recovery"] =
        json!({"status":"required","scope":SCOPE,"source_session":source,"context_moved":moved});
    if let Err(error) = game_deploy::save_report(out, &report) {
        report["report_write_error"] = json!(error);
    }
    Ok((report, 2))
}
fn directories(request: &Request) -> BTreeSet<String> {
    let mut dirs = BTreeSet::new();
    for file in &request.recipe.files {
        let mut path = String::new();
        let components: Vec<_> = file.path.split('/').collect();
        for component in &components[..components.len() - 1] {
            if !path.is_empty() {
                path.push('/');
            }
            path.push_str(component);
            dirs.insert(path.clone());
        }
    }
    dirs
}
fn expected_paths(request: &Request, partial: bool) -> BTreeSet<String> {
    let mut paths = directories(request);
    paths.insert(".phonefarm-owner".into());
    for (i, file) in request.recipe.files.iter().enumerate() {
        paths.insert(file.path.clone());
        if partial {
            paths.insert(format!(".phonefarm-asset-{i}.part"));
        }
    }
    paths
}
pub(crate) fn digest(output: &str) -> Result<&str, String> {
    let value = output
        .split_whitespace()
        .next()
        .ok_or("missing SHA-256 output")?;
    if !game_deploy::valid_hash(value) || output.lines().count() != 1 {
        return Err("invalid SHA-256 output".into());
    }
    Ok(value)
}
pub(crate) fn package_identity(output: &str) -> Result<(u64, u64), String> {
    let mut versions = BTreeSet::new();
    let mut uids = BTreeSet::new();
    for item in output.split_whitespace() {
        if let Some(value) = item.strip_prefix("versionCode=") {
            versions.insert(
                value
                    .parse::<u64>()
                    .map_err(|_| "invalid package versionCode")?,
            );
        }
        if let Some(value) = item
            .strip_prefix("userId=")
            .or_else(|| item.strip_prefix("appId="))
        {
            uids.insert(value.parse::<u64>().map_err(|_| "invalid package UID")?);
        }
    }
    if versions.len() != 1 || uids.len() != 1 || uids.contains(&0) {
        return Err("package identity missing or ambiguous".into());
    }
    Ok((*versions.first().unwrap(), *uids.first().unwrap()))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn typed_game_binding_keeps_other_device_available_through_recovery() {
        use std::{fs, process::Command};
        const NAME: &str = "game_deploy_session::tests::typed_game_binding_keeps_other_device_available_through_recovery";
        // Separate, owned processes exercise the real dead-owner recovery rule;
        // no environment changes leak into concurrently running unit tests.
        if let Ok(phase) = std::env::var("PF_GAME_SCOPE_TEST_PHASE") {
            let root = PathBuf::from(std::env::var_os("DEVLOCK_DIR").unwrap());
            let storage = crate::lease_storage::resolve();
            let a = crate::device_registry::resolve_binding(&storage, "scope-A").unwrap();
            let b = crate::device_registry::resolve_binding(&storage, "scope-B").unwrap();
            let request: Request = serde_json::from_value(json!({
                "schema":game_deploy::REQUEST_SCHEMA,"node_id":"fixture","game_id":"game",
                "version":"v1","serial":"scope-A","mode":"check",
                "recipe":{"schema":game_deploy::RECIPE_SCHEMA,"package":"com.example.game",
                    "version_code":1,"apk_sha256":"a".repeat(64),
                    "files":[{"asset_name":"data","path":"base/data"}]},
                "assets":[{"name":"data","path":"/never-read/cache","size":1,"sha256":"b".repeat(64)}]
            })).unwrap();
            if phase == "create" {
                let mut args = vec![
                    "--request".into(),
                    "unused.json".into(),
                    "--serial".into(),
                    "scope-A".into(),
                    "--out".into(),
                    "unused-out".into(),
                ];
                assert!(game_deploy::needs_queue(&args).unwrap());
                args.extend([
                    "--device-binding".into(),
                    serde_json::to_string(&a).unwrap(),
                ]);
                assert!(!game_deploy::needs_queue(&args).unwrap());
                assert!(game_deploy::needs_queue(&vec![
                    "recover".into(),
                    "--session".into(),
                    "old".into(),
                    "--serial".into(),
                    "scope-A".into(),
                    "--out".into(),
                    "new".into(),
                    "--device-binding".into(),
                    serde_json::to_string(&a).unwrap()
                ])
                .is_err());
                let lease =
                    game_deploy::acquire_target("scope-A", "game-deploy", Some(&a)).unwrap();
                let other =
                    game_deploy::acquire_target("scope-B", "game-install", Some(&b)).unwrap();
                let alias =
                    crate::device_registry::resolve_binding(&storage, "scope-A-wifi").unwrap();
                assert!(
                    game_deploy::acquire_target(&alias.serial, "game-scene", Some(&alias)).is_err()
                );
                let mut session = Session::new(lease, request, &root.join("source")).unwrap();
                let saved = serde_json::to_value(&session.state).unwrap();
                assert_eq!(saved["device_binding"], json!(a));
                assert_eq!(saved["lease_scope"], "device");
                session.lease.hold_for_recovery(&session.dir).unwrap();
                assert!(crate::devlease::Lease::acquire_device(&storage, &b, "same-device", std::time::Duration::ZERO).is_err());
            } else {
                assert_eq!(phase, "recover");
                let other =
                    game_deploy::acquire_target("scope-B", "game-initialize", Some(&b)).unwrap();
                let (report, code) = Session::recover(
                    &root.join("source/game-deploy"),
                    "scope-A",
                    &root.join("recovery"),
                )
                .unwrap();
                assert_eq!(code, 0, "{report}");
                assert_eq!(report["device_binding"], json!(a));
                assert_eq!(report["lease_scope"], "device");
                assert_eq!(report["recovery"]["status"], "verified");
                assert!(crate::devlease::Lease::acquire_device(&storage, &b, "same-device", std::time::Duration::ZERO).is_err());
                drop(other);
                let lease = game_deploy::acquire_target("scope-A", "game-deploy", None).unwrap();
                let legacy = Session::new(lease, request, &root.join("legacy")).unwrap();
                let saved = serde_json::to_value(&legacy.state).unwrap();
                assert_eq!(saved["device_binding"], json!(a));
                assert_eq!(legacy.report("prepared", None, "not_reported")["lease_scope"], "device");
            }
            return;
        }
        let root = std::env::temp_dir().join(format!(
            "pf-game-scope-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&root).unwrap();
        fs::write(root.join("device-registry.json"), json!({
            "schema":"phonefarm/device-registry@1","revision":1,"updated_at":now(),
            "identity_basis":"operator_declared","physical_identity_verified":false,"execution_scope":"global",
            "devices":[{"device_id":"A","label":"synthetic A","aliases":["scope-A","scope-A-wifi"],"enabled":true},
                {"device_id":"B","label":"synthetic B","aliases":["scope-B"],"enabled":true}]
        }).to_string()).unwrap();
        for dir in ["source", "recovery", "legacy"] {
            fs::create_dir(root.join(dir)).unwrap();
        }
        let mut source = None;
        for phase in ["create", "recover"] {
            let mut child = Command::new(std::env::current_exe().unwrap());
            child
                .args([NAME, "--exact", "--nocapture"])
                .env("PF_GAME_SCOPE_TEST_PHASE", phase)
                .env("DEVLOCK_DIR", &root)
                .env("PHONEFARM_LEASE_DIR", root.join("devices"))
                .env("PHONEFARM_STATE_DIR", root.join("state"))
                .env("PHONEFARM_LEASE_TIMEOUT_MS", "1000")
                .env("ADB_BIN", "/usr/bin/false");
            for key in [
                "DEVLOCK_INSTANCE_ID",
                "PF_JOB_ID",
                "PF_JOB_LEASE_DIR",
                "PF_JOB_OUT",
                "PF_RECOVERY_OUT",
                "PF_RUNTIME_REPORT",
            ] {
                child.env_remove(key);
            }
            let output = child.output().unwrap();
            fs::write(root.join(format!("{phase}.stdout")), &output.stdout).unwrap();
            fs::write(root.join(format!("{phase}.stderr")), &output.stderr).unwrap();
            assert!(
                output.status.success(),
                "{phase} failed; retained {}: {} {}",
                root.display(),
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            );
            let bytes = fs::read(root.join("source/game-deploy/session.json")).unwrap();
            if let Some(original) = &source {
                assert_eq!(&bytes, original);
            } else {
                source = Some(bytes);
            }
        }
        assert_eq!(
            crate::devlease::storage_status(&crate::lease_storage::Storage::custom(&root))
                ["status"],
            "clear"
        );
        // This is host lease/serialization evidence only: no APK, ADB or game ran.
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn remote_completion_and_engine_identity_are_explicit() {
        let terminal = "PF_GAME_DEPLOY_EXIT:token:1:";
        assert_eq!(
            parse_terminal("bytes\nPF_GAME_DEPLOY_EXIT:token:1:0\n", terminal)
                .unwrap()
                .stdout,
            "bytes"
        );
        assert_eq!(
            parse_terminal("\nPF_GAME_DEPLOY_EXIT:token:1:1\n", terminal)
                .unwrap()
                .code,
            1
        );
        assert!(parse_terminal("bytes", terminal).is_err());
        assert!(parse_terminal("\nPF_GAME_DEPLOY_EXIT:other:1:0\n", terminal).is_err());
        assert_eq!(
            package_identity("userId=10386\n versionCode=11073 minSdk=21").unwrap(),
            (11073, 10386)
        );
        assert!(package_identity("userId=10386\n versionCode=1\n versionCode=2").is_err());
    }
}
