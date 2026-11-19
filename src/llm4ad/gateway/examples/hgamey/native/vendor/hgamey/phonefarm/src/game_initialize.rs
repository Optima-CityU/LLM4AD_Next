//! An explicit, bounded launcher attempt; new application configuration is retained.
use crate::{
    device_registry::DeviceBinding,
    devlease::Lease,
    game_deploy::{self, InitializeRequest, INITIALIZE_SCOPE},
    game_deploy_session::{digest, package_identity, valid_boot},
    game_deploy_transport::{q, Remote, ShellStderr, Transport},
    game_scene,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::BTreeSet,
    fs::File,
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};

pub(crate) const SCHEMA: &str = "phonefarm/game-initialize-session@1";
static INTERRUPTED: AtomicBool = AtomicBool::new(false);
extern "C" fn interrupt(_: libc::c_int) {
    INTERRUPTED.store(true, Ordering::SeqCst);
}
fn now() -> String {
    chrono::Utc::now().to_rfc3339()
}
fn directory(request: &InitializeRequest) -> String {
    format!("/sdcard/Android/data/{}/files", request.recipe.package)
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct State {
    schema: String,
    request: InitializeRequest,
    token: String,
    phase: String,
    boot_id: Option<String>,
    uid: Option<u64>,
    prior_stopped: Option<bool>,
    component: Option<String>,
    launch_attempted: bool,
    launch_confirmed: bool,
    stop_required: bool,
    stop_attempted: bool,
    stop_completed: bool,
    stop_confirmed: bool,
    write_pending: Option<String>,
    transport_uncertain: bool,
    checks: Value,
    storage: Value,
    application_access: Value,
    stop_observation: Value,
    source_session: Option<PathBuf>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    device_binding: Option<DeviceBinding>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    lease_scope: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    scene: Option<game_scene::State>,
}
impl State {
    fn validate(&self) -> Result<(), String> {
        self.request.validate()?;
        game_deploy::validate_scope(
            self.device_binding.as_ref(),
            self.lease_scope.as_deref(),
            &self.request.serial,
        )?;
        if self.schema
            != if self.scene.is_some() {
                game_scene::SESSION
            } else {
                SCHEMA
            }
            || self.token.is_empty()
            || self.token.len() > 128
            || !self
                .token
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"_-".contains(&c))
            || !matches!(
                self.phase.as_str(),
                "prepared"
                    | "active"
                    | "checked"
                    | "initialized"
                    | "needs_assistance"
                    | "preflight_failed"
                    | "recovery_required"
                    | "observed"
            )
            || self.boot_id.as_ref().is_some_and(|b| !valid_boot(b))
            || self.uid.is_some_and(|u| !(10000..100000).contains(&u))
            || !self.checks.is_object()
            || !self.storage.is_object()
            || !self.application_access.is_object()
            || !self.stop_observation.is_object()
            || self
                .component
                .as_ref()
                .is_some_and(|c| !valid_component(c, &self.request.recipe.package))
            || (self.launch_attempted
                && (self.boot_id.is_none()
                    || self.uid.is_none()
                    || self.prior_stopped != Some(true)
                    || self.component.is_none()))
            || (self.launch_confirmed && !self.launch_attempted)
            || (self.stop_attempted && !self.launch_confirmed)
            || (self.stop_completed && !self.stop_attempted)
            || (self.stop_confirmed && (!self.stop_completed || self.stop_required))
            || (self.launch_attempted && !self.stop_confirmed && !self.stop_required)
            || (self.stop_required && !self.launch_attempted)
            || self.write_pending.as_deref().is_some_and(|p| {
                !matches!(p, "initialize_launch" | "initialize_stop")
                    && !(self.scene.is_some()
                        && matches!(p, "scene_create" | "scene_push" | "scene_publish"))
            })
        {
            return Err("invalid initialization session identity or obligations".into());
        }
        if let Some(scene) = &self.scene {
            scene.validate(&self.request, &self.token)?;
            if (self.launch_attempted && !scene.prepared)
                || self
                    .component
                    .as_deref()
                    .is_some_and(|c| c != game_scene::COMPONENT)
            {
                return Err("scene launch lacks prepared inputs or fixed component".into());
            }
        } else if self.phase == "observed" {
            return Err("scene phase in initialization context".into());
        }
        Ok(())
    }
    fn settled(&self) -> bool {
        !self.transport_uncertain
            && self.write_pending.is_none()
            && (!self.launch_attempted || self.stop_confirmed)
            && !self.stop_required
    }
    fn storage_ready(&self) -> bool {
        self.storage["status"] == "verified"
    }
    fn scope(&self) -> &'static str {
        if self.scene.is_some() {
            game_scene::SCOPE
        } else {
            INITIALIZE_SCOPE
        }
    }
    fn base_report(&self) -> Value {
        let mut value = self.scene.as_ref().map_or_else(
            || game_deploy::initialize_report(&self.request),
            |s| game_scene::report(&s.request),
        );
        game_deploy::report_binding(&mut value, self.device_binding.as_ref());
        value
    }
}

pub(crate) struct Session {
    lease: Lease,
    state: State,
    transport: Transport,
    dir: PathBuf,
    out: PathBuf,
}
impl Session {
    pub(crate) fn new(
        lease: Lease,
        request: InitializeRequest,
        out: &Path,
    ) -> Result<Self, String> {
        Self::new_attempt(lease, request, None, out)
    }
    pub(crate) fn new_scene(
        lease: Lease,
        request: game_scene::Request,
        deployment: crate::game_deploy_session::Committed,
        out: &Path,
    ) -> Result<Self, String> {
        let header = request.header();
        let scene = game_scene::State::new(request, deployment, lease.token());
        Self::new_attempt(lease, header, Some(scene), out)
    }
    fn new_attempt(
        lease: Lease,
        request: InitializeRequest,
        scene: Option<game_scene::State>,
        out: &Path,
    ) -> Result<Self, String> {
        let token = lease.token().to_owned();
        let binding = lease.device_binding().cloned();
        let storage = game_deploy::initialize_report(&request)["storage_initialization"].clone();
        Self::from_state(
            lease,
            State {
                schema: if scene.is_some() {
                    game_scene::SESSION
                } else {
                    SCHEMA
                }
                .into(),
                request,
                token,
                phase: "prepared".into(),
                boot_id: None,
                uid: None,
                prior_stopped: None,
                component: None,
                launch_attempted: false,
                launch_confirmed: false,
                stop_required: false,
                stop_attempted: false,
                stop_completed: false,
                stop_confirmed: false,
                write_pending: None,
                transport_uncertain: false,
                checks: json!({}),
                storage,
                application_access: json!({"status":"unknown"}),
                stop_observation: json!({}),
                source_session: None,
                lease_scope: binding.as_ref().map(|_| "device".into()),
                device_binding: binding,
                scene,
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
        std::fs::create_dir(&dir).map_err(|e| e.to_string())?;
        File::open(&dir)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
        File::open(out)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
        let transport = if state.scene.is_some() {
            Transport::guarded(&state.request.serial, &state.token, &dir)?
        } else {
            Transport::new(
                &state.request.serial,
                &state.token,
                &dir,
                ShellStderr::Merged,
            )?
        };
        let session = Self {
            lease,
            state,
            transport,
            dir,
            out: out.into(),
        };
        session.save()?;
        Ok(session)
    }
    fn save(&self) -> Result<(), String> {
        let bytes = serde_json::to_vec_pretty(&self.state).map_err(|e| e.to_string())?;
        if bytes.len()
            > if self.state.scene.is_some() {
                524288
            } else {
                131072
            }
        {
            return Err("attempt state exceeds recovery record bound".into());
        }
        crate::devlease::durable_replace(&self.dir.join("session.json"), &bytes)
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
    fn shell(&mut self, operation: &str, command: &str, seconds: u64) -> Result<Remote, String> {
        if self.state.transport_uncertain {
            return Err(
                "initialization transport uncertain; further device commands refused".into(),
            );
        }
        match self
            .transport
            .shell(&self.lease, operation, command, seconds)
        {
            Ok(value) => Ok(value),
            Err(error) => Err(if error.uncertain {
                self.uncertain(&error.message)
            } else {
                error.message
            }),
        }
    }
    fn read(&mut self, operation: &str, command: &str) -> Result<String, String> {
        let result = self.shell(operation, command, 30)?;
        if result.code != 0 {
            return Err(format!(
                "{operation} failed on device (exit {})",
                result.code
            ));
        }
        Ok(result.stdout.trim().into())
    }
    fn write(&mut self, operation: &str, command: &str) -> Result<Remote, String> {
        self.lease.hold_for_recovery(&self.dir)?;
        self.state.write_pending = Some(operation.into());
        self.save()?;
        let result = self.shell(operation, command, 30)?;
        self.clear_pending()?;
        Ok(result)
    }
    fn clear_pending(&mut self) -> Result<(), String> {
        let pending = self.state.write_pending.take();
        if let Err(error) = self.save() {
            self.state.write_pending = pending;
            return Err(error);
        }
        Ok(())
    }
    fn raw(
        &mut self,
        operation: &str,
        args: Vec<String>,
        seconds: u64,
    ) -> Result<crate::lease_command::Output, String> {
        if self.state.transport_uncertain {
            return Err("attempt transport is uncertain; further commands refused".into());
        }
        self.transport
            .raw(&self.lease, operation, args, seconds)
            .map_err(|e| {
                if e.uncertain {
                    self.uncertain(&e.message)
                } else {
                    e.message
                }
            })
    }
    fn prepare_scene(&mut self) -> Result<(), String> {
        let scene = self.state.scene.clone().ok_or("missing scene state")?;
        if scene.prepare_attempted {
            return Err("scene preparation cannot be replayed".into());
        }
        self.state.scene.as_mut().unwrap().prepare_attempted = true;
        self.save()?;
        let root = q(&scene.runtime_root);
        let marker = q(&format!("{}/.phonefarm-owner", scene.runtime_root));
        let created=self.write("scene_create",&format!("test ! -e {root} && test ! -L {root} && mkdir {root} && printf '%s' {} > {marker} && mkdir {root}/base {root}/base/demos {root}/base/script",q(&self.state.token)))?;
        if created.code != 0 {
            return Err(
                "scene runtime creation failed; any new partial root is intentionally retained"
                    .into(),
            );
        }
        for index in 0..2 {
            if INTERRUPTED.load(Ordering::SeqCst) {
                return Err("scene preparation interrupted before seed".into());
            }
            let asset = scene.request.seed(index);
            let file = game_deploy::source_file(asset)?;
            let before = file.metadata().map_err(|e| e.to_string())?;
            let part = format!("{}/.scene-seed-{index}.part", scene.runtime_root);
            let target = format!("{}/{}", scene.runtime_root, scene.request.seed_path(index));
            self.read("scene_seed_owner",&format!("test -d {root} && test ! -L {root} && test -f {marker} && test ! -L {marker} && test \"$(cat {marker})\" = {} && test ! -e {} && test ! -L {}",q(&self.state.token),q(&part),q(&part)))?;
            self.lease.hold_for_recovery(&self.dir)?;
            self.state.write_pending = Some("scene_push".into());
            self.save()?;
            let pushed = self.raw(
                "scene_push",
                vec![
                    "push".into(),
                    asset.path.to_string_lossy().into_owned(),
                    part.clone(),
                ],
                30,
            )?;
            if pushed.code != 0 {
                return Err(self.uncertain("scene seed push did not confirm transport completion"));
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
                return Err("scene seed source changed while pushing".into());
            }
            let bytes = self.read(
                "scene_seed_size",
                &format!("test -f {0} && test ! -L {0} && wc -c < {0}", q(&part)),
            )?;
            let hash = self.read("scene_seed_sha256", &format!("sha256sum {}", q(&part)))?;
            if bytes.parse::<u64>().ok() != Some(asset.size) || digest(&hash)? != asset.sha256 {
                return Err("received scene seed size/SHA-256 differs".into());
            }
            let published = self.write(
                "scene_publish",
                &format!(
                    "test ! -e {0} && test ! -L {0} && mv {1} {0}",
                    q(&target),
                    q(&part)
                ),
            )?;
            if published.code != 0 {
                return Err("scene seed publication failed; new runtime retained".into());
            }
        }
        let receipt = json!({"schema":"phonefarm/game-scene-seeds@1","root":scene.runtime_root,"token":self.state.token,
            "seeds":(0..2).map(|i|json!({"path":scene.request.seed_path(i),"size":scene.request.seed(i).size,"sha256":scene.request.seed(i).sha256})).collect::<Vec<_>>(),
            "prepared_at":now(),"content_verification":"device_receiver_sha256","retention":"intentional"});
        crate::confirmation_transport::new_file(
            &self.dir.join("scene-seeds.json"),
            &serde_json::to_vec_pretty(&receipt).map_err(|e| e.to_string())?,
        )?;
        self.state.scene.as_mut().unwrap().prepared = true;
        self.save()
    }
    fn observe_scene(&mut self) -> Result<(), String> {
        let scene = self.state.scene.clone().ok_or("missing scene state")?;
        let log_path = q(&format!("{}/base/qconsole.log", scene.runtime_root));
        let output=self.read("scene_log",&format!("if test -f {log_path} && test ! -L {log_path}; then printf 'PF_SCENE_LOG_PRESENT\\n'; head -c 262144 {log_path}; else printf 'PF_SCENE_LOG_MISSING\\n'; fi"))?;
        let log = output.strip_prefix("PF_SCENE_LOG_PRESENT\n");
        let associated = self
            .state
            .scene
            .as_mut()
            .unwrap()
            .observe_log(log.unwrap_or(""));
        crate::confirmation_transport::new_file(
            &self.dir.join("scene-qconsole.txt"),
            log.unwrap_or("").as_bytes(),
        )?;
        let processes = self.processes("scene_started_processes")?;
        let observation = &mut self.state.scene.as_mut().unwrap().observation;
        observation["log_present"] = json!(log.is_some());
        observation["log_capture"] = json!({"path":"game-deploy/scene-qconsole.txt","max_bytes":262144,"full_log":"not_claimed"});
        observation["uid_processes"] =
            json!({"observed":processes,"complete_visibility":"unknown","observed_at":now()});
        self.save()?;
        let captured = self.raw(
            "scene_screenshot",
            vec!["exec-out".into(), "screencap -p".into()],
            20,
        )?;
        if captured.code != 0 {
            return Err(self.uncertain("scene screenshot transport did not confirm completion"));
        }
        let bytes = crate::confirmation_transport::bounded(&captured.stdout_path, 8 * 1024 * 1024)?;
        if bytes.len() < 24 || !bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
            return Err("scene screenshot is not PNG".into());
        }
        let width = u32::from_be_bytes(bytes[16..20].try_into().unwrap());
        let height = u32::from_be_bytes(bytes[20..24].try_into().unwrap());
        if width == 0
            || height == 0
            || width > 8192
            || height > 8192
            || u64::from(width) * u64::from(height) > 24_000_000
        {
            return Err("scene screenshot dimensions exceed bounds".into());
        }
        image::load_from_memory_with_format(&bytes, image::ImageFormat::Png)
            .map_err(|e| format!("scene PNG incomplete: {e}"))?;
        crate::confirmation_transport::new_file(&self.out.join("scene.png"), &bytes)?;
        self.state.scene.as_mut().unwrap().observation["image"] = json!({"path":"scene.png","mime":"image/png","bytes":bytes.len(),"width":width,"height":height,"captured_at":now(),"same_state_truth":false});
        self.save()?;
        if !associated {
            return Err("scene log did not associate the requested demo and paths; effective engine/profile remain unknown".into());
        }
        Ok(())
    }
    fn identity(&mut self, initial: bool, verify_apk: bool) -> Result<bool, String> {
        if self.read("current_user", "am get-current-user")? != "0" {
            return Err("only Android user 0 is supported".into());
        }
        let boot = self.read("boot_id", "cat /proc/sys/kernel/random/boot_id")?;
        if !valid_boot(&boot) {
            return Err("device boot identity unavailable".into());
        }
        if !initial && self.state.boot_id.as_deref() != Some(boot.as_str()) {
            return Err("device boot changed; initialization stop requires review".into());
        }
        let package = self.state.request.recipe.package.clone();
        let paths = self.read("package_path", &format!("pm path --user 0 {}", q(&package)))?;
        let lines: Vec<_> = paths.lines().collect();
        let apk = lines
            .first()
            .and_then(|s| s.strip_prefix("package:"))
            .filter(|s| {
                s.starts_with('/') && s.ends_with("/base.apk") && !s.chars().any(char::is_control)
            });
        if lines.len() != 1 || apk.is_none() {
            return Err("initialization requires exactly one installed base APK".into());
        }
        let apk = apk.unwrap().to_owned();
        let info = self.read(
            "package_identity",
            &format!("dumpsys package {}", q(&package)),
        )?;
        let (version, uid) = package_identity(&info)?;
        if version != self.state.request.recipe.version_code
            || !(10000..100000).contains(&uid)
            || (!initial && self.state.uid != Some(uid))
        {
            return Err("installed version or user 0 application UID differs".into());
        }
        let stopped = user0_stopped(&info)?;
        if !initial && self.state.checks["installed"]["apk_path"].as_str() != Some(apk.as_str()) {
            return Err("installed APK path changed during initialization".into());
        }
        if verify_apk {
            let hash = self.read("apk_sha256", &format!("sha256sum {}", q(&apk)))?;
            if digest(&hash)? != self.state.request.recipe.apk_sha256 {
                return Err("installed APK SHA-256 differs".into());
            }
        }
        let packages = self.read("uid_ownership", "pm list packages -u -U --user 0")?;
        unique_uid(&packages, &package, uid)?;
        if initial {
            self.state.boot_id = Some(boot.clone());
            self.state.uid = Some(uid);
            self.state.prior_stopped = Some(stopped);
        }
        if verify_apk {
            self.state.checks["installed"] = json!({"status":"verified","package":package,"version_code":version,"apk_sha256":self.state.request.recipe.apk_sha256,"apk_path":apk,"uid":uid,"observed_at":now(),"content_verification":"device_sha256"});
        }
        self.state.checks["identity"] = json!({"boot_id":boot,"user":0,"pm_stopped":stopped,"uid_exclusive_in_user0_listing":true,"observed_at":now()});
        self.save()?;
        Ok(stopped)
    }
    fn processes(&mut self, operation: &str) -> Result<Vec<Value>, String> {
        let output = self.read(operation, "ps -A -o UID,PID,NAME")?;
        uid_processes(&output, self.state.uid.ok_or("missing initialization UID")?)
    }
    fn observe_storage(&mut self, deadline: Option<Instant>) -> Result<(), String> {
        let path = directory(&self.state.request);
        let quoted = q(&path);
        let seconds = deadline
            .map(|end| {
                end.saturating_duration_since(Instant::now())
                    .as_secs()
                    .saturating_add(1)
                    .min(30)
            })
            .unwrap_or(30);
        let observed=self.shell("initialization_directory",&format!("if test -L {quoted}; then printf 'symlink\\n'; elif test -d {quoted}; then if test -r {quoted} && test -x {quoted} && test -w {quoted}; then printf 'ready\\n'; else printf 'inaccessible\\n'; fi; elif test -e {quoted}; then printf 'not_directory\\n'; else printf 'not_observed\\n'; fi"),seconds)?;
        if observed.code != 0 {
            return Err(format!(
                "directory observation failed (exit {})",
                observed.code
            ));
        }
        let result = observed.stdout.trim().to_owned();
        let (ready, access) = match result.as_str() {
            "ready" => (true, "read_write"),
            "not_observed" => (false, "not_observed"),
            "inaccessible" => (false, "unavailable"),
            "symlink" | "not_directory" => (false, "invalid_type"),
            _ => return Err("unrecognized resource directory observation".into()),
        };
        self.state.storage = json!({"status":if ready {"verified"} else {"unknown"},"path":path,"shell_access":access,"directory_observation":result,"observed_at":now(),"application_side_effects":"retained"});
        self.state.application_access = json!({"status":"unknown","uid_readable":null,"reason":"application UID access not observed; directory existence is not engine consumption"});
        self.save()?;
        // UID probing is outside the bounded polling window and runs after stop.
        if ready && deadline.is_none() {
            let script=format!("id -u && test -d {quoted} && test ! -L {quoted} && test -r {quoted} && test -x {quoted}");
            let access = self.shell(
                "initialization_uid_read",
                &format!(
                    "run-as {} sh -c {}",
                    q(&self.state.request.recipe.package),
                    q(&script)
                ),
                30,
            )?;
            let uid = access.stdout.trim().parse::<u64>().ok();
            self.state.application_access = json!({"status":"unknown","uid_readable":if access.code==0 && uid==self.state.uid {Some(true)} else {None},"remote_exit":access.code,
                "reason":if access.code==0 && uid==self.state.uid {"UID directory readability only; application SELinux domain and engine consumption not verified"} else {"run-as UID directory access unavailable or not verified"}});
            self.save()?;
        }
        Ok(())
    }
    fn preflight(&mut self) -> Result<(), String> {
        let stopped = self.identity(true, true)?;
        self.observe_storage(None)?;
        if self.state.scene.is_none() && self.state.storage_ready() {
            return Ok(());
        }
        if self.state.storage["shell_access"] == "invalid_type" {
            return Err(
                "resource parent has an unexpected type; automatic initialization refused".into(),
            );
        }
        if !stopped {
            return Err("target was not explicitly stopped before initialization".into());
        }
        let processes = self.processes("initialization_prior_processes")?;
        self.state.checks["prior_uid_processes"] =
            json!({"observed":processes,"complete_visibility":"unknown"});
        self.save()?;
        if !processes.is_empty() {
            return Err("target UID processes observed before initialization".into());
        }
        if let Some(scene) = self.state.scene.clone() {
            if !self.state.storage_ready() {
                return Err(
                    "scene runtime parent is not ready; run explicit initialization first".into(),
                );
            }
            if self.state.boot_id.as_deref() != Some(scene.deployment.boot_id.as_str()) {
                return Err(
                    "source deployment boot differs; scene resource identity needs review".into(),
                );
            }
            let bytes = scene.deployment.verify(|op, cmd| self.read(op, cmd))?;
            self.state.scene.as_mut().unwrap().resources = json!({"status":"verified","root":scene.deployment.remote_root,
                "files":scene.deployment.request.recipe.files,"content_verification":"device_sha256","verified_bytes":bytes,"observed_at":now()});
            self.state.component = Some(game_scene::COMPONENT.into());
            return self.save();
        }
        let components=self.read("initialization_launcher",&format!("cmd package query-activities --components --user 0 -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -p {}",q(&self.state.request.recipe.package)))?;
        self.state.component = Some(launcher_component(
            &components,
            &self.state.request.recipe.package,
        )?);
        self.save()
    }
    fn launch(&mut self) -> Result<(), String> {
        if INTERRUPTED.load(Ordering::SeqCst) {
            return Err("initialization interrupted before launch".into());
        }
        if self.state.launch_attempted {
            return Err("initialization launch cannot be replayed".into());
        }
        self.lease.hold_for_recovery(&self.dir)?;
        self.state.phase = "active".into();
        self.state.launch_attempted = true;
        self.state.stop_required = true;
        self.save()?;
        let component = self
            .state
            .component
            .clone()
            .ok_or("missing launcher component")?;
        let command = if let Some(scene) = &self.state.scene {
            format!(
                "am start -W --user 0 -n {} --es game doom3 --es command {}",
                q(&component),
                q(&scene.command())
            )
        } else {
            format!("am start -W --user 0 -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -n {}",q(&component))
        };
        let output = self.write("initialize_launch", &command)?;
        if output.code != 0 || !launch_ok(&output.stdout) {
            return Err(
                "launcher result was not confirmed Status ok; automatic replay/stop refused".into(),
            );
        }
        self.state.launch_confirmed = true;
        self.save()
    }
    fn stop(&mut self) -> Result<(), String> {
        if self.state.transport_uncertain
            || self.state.write_pending.is_some()
            || !self.state.launch_confirmed
        {
            return Err(
                "launch/command result unknown; automatic initialization stop refused".into(),
            );
        }
        // A historical confirmation cannot survive a failed current recovery readback.
        self.state.stop_confirmed = false;
        self.state.stop_required = true;
        self.save()?;
        if !self.state.stop_completed {
            if self.state.stop_attempted {
                return Err("stop result not confirmed; automatic stop replay refused".into());
            }
            self.identity(false, false)?;
            self.state.stop_attempted = true;
            self.save()?;
            let output = self.write(
                "initialize_stop",
                &format!(
                    "am force-stop --user 0 {}",
                    q(&self.state.request.recipe.package)
                ),
            )?;
            if output.code != 0 || !output.stdout.trim().is_empty() {
                return Err("force-stop did not return unambiguous completion".into());
            }
            self.state.stop_completed = true;
            self.save()?;
        }
        let stopped = self.identity(false, false)?;
        let processes = self.processes("initialization_stopped_processes")?;
        self.state.stop_observation = json!({"pm_stopped":stopped,"observed_uid_processes":processes,"complete_visibility":"unknown","observed_at":now()});
        self.save()?;
        if !stopped || !processes.is_empty() {
            return Err(
                "target stop readback is not confirmed; observed UID processes or PM stopped=false"
                    .into(),
            );
        }
        self.state.stop_confirmed = true;
        self.state.stop_required = false;
        self.save()
    }
    fn apply(&mut self) -> Result<(), String> {
        if self.state.scene.is_some() {
            self.prepare_scene()?;
        }
        self.launch()?;
        let deadline = Instant::now() + Duration::from_secs(self.state.request.recipe.wait_seconds);
        if self.state.scene.is_some() {
            while Instant::now() < deadline && !INTERRUPTED.load(Ordering::SeqCst) {
                std::thread::sleep(
                    Duration::from_millis(100)
                        .min(deadline.saturating_duration_since(Instant::now())),
                );
            }
            let observation = if INTERRUPTED.load(Ordering::SeqCst) {
                Err("scene observation interrupted".into())
            } else {
                self.observe_scene()
            };
            self.stop()?;
            return observation;
        }
        let mut collection_error = None;
        loop {
            if let Err(error) = self.observe_storage(Some(deadline)) {
                collection_error = Some(error);
                break;
            }
            if self.state.storage_ready()
                || Instant::now() >= deadline
                || INTERRUPTED.load(Ordering::SeqCst)
            {
                break;
            }
            std::thread::sleep(
                Duration::from_millis(250).min(deadline.saturating_duration_since(Instant::now())),
            );
        }
        // A known collection failure does not erase the owed target stop.
        self.stop()?;
        if let Some(error) = collection_error {
            return Err(error);
        }
        self.observe_storage(None)
    }
    fn report(&self, phase: &str, error: Option<&str>, settled: bool) -> Value {
        let mut value = self.state.base_report();
        value["phase"] = json!(phase);
        value["error"] = json!(error);
        value["session_path"] = json!("game-deploy");
        value["checks"] = self.state.checks.clone();
        if self.state.checks["installed"].is_object() {
            value["installed"] = self.state.checks["installed"].clone();
        }
        if let Some(scene) = &self.state.scene {
            value["input"]["status"] = json!("verified_before_acquisition");
            value["resources_deployed"] = scene.resources.clone();
            value["runtime_seed"] = json!({"status":if scene.prepared{"prepared"}else if scene.prepare_attempted{"attempted"}else{"not_prepared"},
                "root":scene.runtime_root,"retention":"intentional","seed_receipt":if scene.prepared{Some("game-deploy/scene-seeds.json")}else{None},
                "application_outputs":"may_differ_from_seed"});
            value["scene_observation"] = scene.observation.clone();
        } else {
            value["storage_initialization"] = self.state.storage.clone();
            if phase == "needs_assistance" && !self.state.storage_ready() {
                value["storage_initialization"]["status"] = json!("needs_assistance");
            }
        }
        value["application_access"] = self.state.application_access.clone();
        value["launch"] = json!({"status":if self.state.launch_confirmed {"confirmed"} else if self.state.launch_attempted {"unknown"} else {"not_attempted"},
            "component":self.state.component,"prior_pm_stopped":self.state.prior_stopped,"wait_seconds":self.state.request.recipe.wait_seconds,
            "first_launch_ever":"not_claimed","application_side_effects":"retained"});
        if let Some(scene) = &self.state.scene {
            value["launch"]["requested_command"] = json!(scene.command());
            value["launch"]["effective_command"] = Value::Null;
            value["launch"]["cold_start_reproduced"] = json!("unknown");
        }
        value["stop"] = self.state.stop_observation.clone();
        value["stop"]["status"] = json!(if self.state.stop_confirmed {
            "confirmed"
        } else if !self.state.launch_attempted {
            "not_required"
        } else if self.state.transport_uncertain || self.state.write_pending.is_some() {
            "unknown"
        } else {
            "required"
        });
        value["stop"]["command_completed"] = json!(self.state.stop_completed);
        value["stop"]["all_target_processes_gone"] = json!("unknown");
        value["recovery"] = json!({"status":if settled {"verified"} else {"required"},"scope":self.state.scope(),
            "source_session":self.state.source_session,"context_moved":self.state.source_session.as_ref().map(|_|true),"device_restored":"not_claimed"});
        value
    }
    fn finish(
        &mut self,
        phase: &str,
        error: Option<&str>,
        settled: bool,
        code: i32,
    ) -> Result<(Value, i32), String> {
        if settled && !self.state.settled() {
            return Err("initialization cannot settle while launch/stop obligations remain".into());
        }
        self.state.phase = phase.into();
        self.save()?;
        let mut value = self.report(phase, error, settled);
        game_deploy::save_report(&self.out, &value)?;
        if settled {
            if let Err(error) = self.lease.complete_recovery(
                self.state.scope(),
                "device",
                &[&self.out.join("deployment.json")],
            ) {
                let _ = self.lease.hold_for_recovery(&self.dir);
                value["phase"] = json!("recovery_required");
                value["recovery"]["status"] = json!("required");
                value["error"] = json!(error);
                game_deploy::save_report(&self.out, &value)?;
                return Ok((value, 2));
            }
        }
        Ok((value, code))
    }
    fn finish_observation(&mut self, error: Option<&str>) -> Result<(Value, i32), String> {
        if !self.state.settled() {
            return self.finish("recovery_required", error, false, 2);
        }
        if let Some(scene) = &self.state.scene {
            let observed = scene.observation["status"] == "observed" && error.is_none();
            return self.finish(
                if observed {
                    "observed"
                } else {
                    "needs_assistance"
                },
                error,
                true,
                if observed { 0 } else { 2 },
            );
        }
        let ready = self.state.storage_ready() && error.is_none();
        self.finish(
            if ready {
                "initialized"
            } else {
                "needs_assistance"
            },
            error,
            true,
            if ready { 0 } else { 2 },
        )
    }
    fn publication_failure(&mut self, error: &str) -> (Value, i32) {
        self.state.phase = "recovery_required".into();
        let hold = self.lease.hold_for_recovery(&self.dir).err();
        let save = self.save().err();
        let error = format!(
            "{error}{}{}",
            hold.as_ref()
                .map(|e| format!("; retain: {e}"))
                .unwrap_or_default(),
            save.map(|e| format!("; state: {e}")).unwrap_or_default()
        );
        let mut value = self.report("recovery_required", Some(&error), false);
        if hold.is_some() {
            if let Some(source) = &self.state.source_session {
                let moved = game_deploy::context_moved(
                    &self.lease,
                    &self.state.request.serial,
                    source,
                    &self.dir,
                );
                value["recovery"]["context_moved"] = json!(moved);
                if moved != Some(true) {
                    value["session_path"] = Value::Null;
                }
            }
        }
        if let Err(error) = game_deploy::save_report(&self.out, &value) {
            value["report_write_error"] = json!(error);
        }
        (value, 2)
    }
    pub(crate) fn execute(mut self) -> Result<(Value, i32), String> {
        unsafe {
            libc::signal(libc::SIGINT, interrupt as *const () as libc::sighandler_t);
        }
        match self.execute_inner() {
            Ok(value) => Ok(value),
            Err(error) => Ok(self.publication_failure(&error)),
        }
    }
    fn execute_inner(&mut self) -> Result<(Value, i32), String> {
        if let Err(error) = self.preflight() {
            let settled = self.state.settled();
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
            return self.finish("checked", None, true, 0);
        }
        if self.state.scene.is_none() && self.state.storage_ready() {
            return self.finish("initialized", None, true, 0);
        }
        let result = self.apply();
        self.finish_observation(result.as_ref().err().map(String::as_str))
    }
    pub(crate) fn recover(source: &Path, serial: &str, out: &Path) -> Result<(Value, i32), String> {
        let source = source.canonicalize().map_err(|e| e.to_string())?;
        let bytes = crate::confirmation_transport::bounded(&source.join("session.json"), 524288)?;
        let mut state: State = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
        state.validate()?;
        if state.request.serial != serial {
            return Err("initialization recovery serial differs from source".into());
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
            let moved = game_deploy::context_moved(&session.lease, serial, &source, &session.dir);
            return failed_recovery(&prior, &source, out, &error, moved);
        }
        let result = (|| {
            if session.state.transport_uncertain || session.state.write_pending.is_some() {
                return Err(
                    "unknown initialization command; recovery does not replay launch or stop"
                        .into(),
                );
            }
            if session.state.launch_attempted {
                session.state.stop_confirmed = false;
                session.state.stop_required = true;
                session.save()?;
            }
            // Reacquiring a lease revalidates the APK once, before any owed stop.
            if session.state.boot_id.is_some() {
                session.identity(false, true)?;
            }
            if session.state.launch_attempted {
                session.stop()?;
            }
            if session.state.scene.is_none() && session.state.boot_id.is_some() {
                session.observe_storage(None)?;
            }
            Ok(())
        })();
        if session.state.scene.is_some() && !session.state.launch_attempted && result.is_ok() {
            let check = session.state.request.mode == "check";
            return match session.finish(
                if check { "checked" } else { "needs_assistance" },
                None,
                true,
                if check { 0 } else { 2 },
            ) {
                Ok(value) => Ok(value),
                Err(error) => Ok(session.publication_failure(&error)),
            };
        }
        if !session.state.launch_attempted && result.is_ok() && !session.state.storage_ready() {
            let phase = if session.state.request.mode == "check" {
                "checked"
            } else {
                "preflight_failed"
            };
            return match session.finish(phase, None, true, 0) {
                Ok(value) => Ok(value),
                Err(error) => Ok(session.publication_failure(&error)),
            };
        }
        match session.finish_observation(result.as_ref().err().map(String::as_str)) {
            Ok(value) => Ok(value),
            Err(error) => Ok(session.publication_failure(&error)),
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
    let mut value = state.base_report();
    value["phase"] = json!("recovery_required");
    value["error"] = json!(error);
    // Acquisition/handoff failure cannot assert that the source has no stop obligation.
    value["launch"]["status"] = json!("unknown");
    value["stop"]["status"] = json!("unknown");
    value["session_path"] = if moved == Some(true) {
        json!("game-deploy")
    } else {
        Value::Null
    };
    value["recovery"] = json!({"status":"required","scope":state.scope(),"source_session":source,"context_moved":moved});
    if let Err(error) = game_deploy::save_report(out, &value) {
        value["report_write_error"] = json!(error);
    }
    Ok((value, 2))
}

fn user0_stopped(output: &str) -> Result<bool, String> {
    let rows: Vec<_> = output
        .lines()
        .map(str::trim)
        .filter(|line| line.starts_with("User 0:"))
        .collect();
    if rows.len() != 1 {
        return Err("PM user 0 state missing or ambiguous".into());
    }
    let fields: Vec<_> = rows[0].split_whitespace().collect();
    if fields
        .iter()
        .filter(|f| f.starts_with("installed="))
        .copied()
        .collect::<Vec<_>>()
        != ["installed=true"]
    {
        return Err("target not explicitly installed for user 0".into());
    }
    let stopped: Vec<_> = fields
        .iter()
        .filter_map(|f| f.strip_prefix("stopped="))
        .collect();
    match stopped.as_slice() {
        ["true"] => Ok(true),
        ["false"] => Ok(false),
        _ => Err("PM user 0 stopped state missing or ambiguous".into()),
    }
}
fn unique_uid(output: &str, package: &str, uid: u64) -> Result<(), String> {
    let mut seen = BTreeSet::new();
    let mut owners = BTreeSet::new();
    for line in output.lines() {
        let fields: Vec<_> = line.split_whitespace().collect();
        if fields.len() != 2 {
            return Err("unrecognized user 0 package/UID list".into());
        }
        let name = fields[0]
            .strip_prefix("package:")
            .filter(|v| crate::graylayer::valid_package(v))
            .ok_or("invalid package in UID list")?;
        let actual = fields[1]
            .strip_prefix("uid:")
            .and_then(|v| v.parse::<u64>().ok())
            .ok_or("invalid UID in package list")?;
        if !seen.insert(name) {
            return Err("duplicate package in UID list".into());
        }
        if actual == uid {
            owners.insert(name);
        }
    }
    if !seen.contains("android") || owners != BTreeSet::from([package]) {
        return Err("user 0 package UID ownership is missing, shared or incomplete".into());
    }
    Ok(())
}
fn uid_processes(output: &str, uid: u64) -> Result<Vec<Value>, String> {
    let mut lines = output.lines().filter(|line| !line.trim().is_empty());
    if lines
        .next()
        .map(|s| s.split_whitespace().collect::<Vec<_>>())
        != Some(vec!["UID", "PID", "NAME"])
    {
        return Err("numeric UID/PID/NAME process snapshot unavailable".into());
    }
    let mut pids = BTreeSet::new();
    let mut found = Vec::new();
    for line in lines {
        let fields: Vec<_> = line.split_whitespace().collect();
        if fields.len() < 3 {
            return Err("incomplete numeric process snapshot".into());
        }
        let actual = fields[0]
            .parse::<u64>()
            .map_err(|_| "non-numeric process UID")?;
        let pid = fields[1]
            .parse::<u32>()
            .map_err(|_| "non-numeric process PID")?;
        if pid == 0 || !pids.insert(pid) {
            return Err("invalid or repeated process PID".into());
        }
        if actual == uid {
            found.push(json!({"uid":actual,"pid":pid,"name":fields[2..].join(" ")}));
        }
    }
    if !pids.contains(&1) {
        return Err("process snapshot has no init sentinel; observation unavailable".into());
    }
    Ok(found)
}
fn valid_component(value: &str, package: &str) -> bool {
    value.split_once('/').is_some_and(|(pkg, class)| {
        pkg == package
            && !class.is_empty()
            && class
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"._$".contains(&c))
    })
}
fn launcher_component(output: &str, package: &str) -> Result<String, String> {
    let components: BTreeSet<_> = output
        .lines()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect();
    if components.len() != 1 || !components.iter().all(|s| valid_component(s, package)) {
        return Err("target has no unique MAIN/LAUNCHER component".into());
    }
    Ok(components.first().unwrap().to_string())
}
fn launch_ok(output: &str) -> bool {
    let statuses: Vec<_> = output
        .lines()
        .map(str::trim)
        .filter_map(|s| s.strip_prefix("Status:"))
        .map(str::trim)
        .collect();
    statuses == ["ok"] && !output.lines().any(|s| s.trim_start().starts_with("Error:"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn initialization_admission_requires_explicit_identity_and_observations() {
        let package = "com.example.game";
        assert_eq!(user0_stopped("userId=10042 versionCode=17\n  User 0: installed=true stopped=true notLaunched=true").unwrap(),true);
        assert!(
            user0_stopped("User 0: installed=true\nUser 10: installed=true stopped=true").is_err()
        );
        assert!(user0_stopped("User 0: installed=false stopped=true").is_err());
        unique_uid(
            "package:android uid:1000\npackage:com.example.game uid:10042",
            package,
            10042,
        )
        .unwrap();
        assert!(unique_uid("package:android uid:1000\npackage:com.example.game uid:10042\npackage:com.example.other uid:10042",package,10042).is_err());
        assert!(uid_processes("UID PID NAME\n0 1 init\n2000 44 sh", 10042)
            .unwrap()
            .is_empty());
        assert_eq!(
            uid_processes(
                "UID PID NAME\n0 1 init\n10042 52 non_package_process_name",
                10042
            )
            .unwrap()
            .len(),
            1
        );
        assert!(uid_processes("UID PID NAME", 10042).is_err());
        assert!(uid_processes("UID PID NAME\nroot 1 init", 10042).is_err());
        assert_eq!(
            launcher_component("com.example.game/.Main", package).unwrap(),
            "com.example.game/.Main"
        );
        assert!(
            launcher_component("com.example.game/.Main\ncom.example.game/.Other", package).is_err()
        );
        assert!(!launch_ok("Starting: Intent\nError: denied\nStatus: ok"));
        assert!(launch_ok("Starting: Intent\nStatus: ok\nComplete"));
    }
    #[test]
    fn initialization_state_cannot_settle_an_unknown_or_unstopped_launch() {
        let request:InitializeRequest=serde_json::from_value(json!({"schema":game_deploy::INITIALIZE_REQUEST_SCHEMA,"node_id":"node","game_id":"game","version":"v1","serial":"S","mode":"apply", "recipe":{"schema":game_deploy::INITIALIZE_RECIPE_SCHEMA,"package":"com.example.game","version_code":17,"apk_sha256":"a".repeat(64),"wait_seconds":1},"assets":[]})).unwrap();
        request.validate().unwrap();
        let mut state = State {
            schema: SCHEMA.into(),
            request,
            token: "token".into(),
            phase: "active".into(),
            boot_id: Some("00000000-0000-4000-8000-000000000123".into()),
            uid: Some(10042),
            prior_stopped: Some(true),
            component: Some("com.example.game/.Main".into()),
            launch_attempted: true,
            launch_confirmed: false,
            stop_required: true,
            stop_attempted: false,
            stop_completed: false,
            stop_confirmed: false,
            write_pending: Some("initialize_launch".into()),
            transport_uncertain: true,
            checks: json!({}),
            storage: json!({}),
            application_access: json!({}),
            stop_observation: json!({}),
            source_session: None,
            device_binding: None,
            lease_scope: None,
            scene: None,
        };
        state.validate().unwrap();
        let legacy = serde_json::to_value(&state).unwrap();
        assert!(legacy.get("scene").is_none());
        serde_json::from_value::<State>(legacy)
            .unwrap()
            .validate()
            .unwrap();
        assert!(!state.settled());
        state.transport_uncertain = false;
        state.write_pending = None;
        state.launch_confirmed = true;
        state.stop_attempted = true;
        state.stop_completed = true;
        assert!(!state.settled());
        state.stop_confirmed = true;
        assert!(state.validate().is_err());
        state.stop_required = false;
        state.validate().unwrap();
        assert!(state.settled());
        // A scene uses the same stop obligation, but owns a distinct schema and
        // frozen context. Recovery validation does not reopen input/PK4 files.
        let (mut request, source, _, _) = game_scene::tests::fixture();
        request.deployment.report_path = "/not-reopened/source-deployment.json".into();
        request.deployment.state_path = "/not-reopened/source-session.json".into();
        let mut scene_state = state.clone();
        scene_state.schema = game_scene::SESSION.into();
        scene_state.request = request.header();
        scene_state.component = Some(game_scene::COMPONENT.into());
        scene_state.scene = Some(game_scene::State::new(request, source, &state.token));
        assert!(scene_state.validate().is_err()); // launch requires prepared seeds
        let scene = scene_state.scene.as_mut().unwrap();
        scene.prepare_attempted = true;
        scene.prepared = true;
        scene_state.validate().unwrap();
        assert!(scene_state.settled());
        assert_eq!(scene_state.scope(), game_scene::SCOPE);
        let mut restored: State =
            serde_json::from_value(serde_json::to_value(&scene_state).unwrap()).unwrap();
        restored.validate().unwrap();
        restored.transport_uncertain = true;
        restored.write_pending = Some("scene_push".into());
        assert!(!restored.settled());
        restored.schema = SCHEMA.into();
        assert!(restored.validate().is_err());
        state.request.recipe.wait_seconds = 31;
        assert!(state.validate().is_err());
        state.request.recipe.wait_seconds = 1;
        let mut value = serde_json::to_value(&state.request).unwrap();
        value["recipe"]["component"] = json!("com.example.game/.Other");
        assert!(serde_json::from_value::<InitializeRequest>(value).is_err());
    }
}
