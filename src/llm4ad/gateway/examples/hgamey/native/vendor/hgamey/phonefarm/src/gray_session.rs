//! Finite frame-audit capture: original property values, owned files and target exit.
use crate::{
    confirmation_transport::{bounded, journal, new_file, quote as q},
    devlease::{durable_replace, Lease},
    graylayer::{self, LAYER_NAME, LAYER_PROPERTIES, SO, STATE},
};
use serde_json::{json, Map, Value};
use std::{cell::RefCell, collections::HashSet, fs, path::{Path, PathBuf}, time::Duration};

const LEGACY_SCHEMA: &str = "phonefarm/gray-capture-session@1";
const SCHEMA: &str = "phonefarm/gray-capture-session@2";
const CONTROL_RESOURCE_SCHEMA: &str = "phonefarm/gray-capture-session@3";
pub(crate) const LEGACY_CONTROL_PARENT_SCHEMA: &str = "phonefarm/gray-control-resources@1";
pub(crate) const INPUT_CONTROL_PARENT_SCHEMA: &str = "phonefarm/gray-control-resources@2";
pub(crate) const CONTROL_PARENT_SCHEMA: &str = "phonefarm/gray-control-resources@3";
const SCOPE: &str = "gray_capture_owned_installation_properties_target_stopped";
const EVAL_OWNER: &str = "gray_eval_launch";
const EVAL_SCOPE: &str = "gray_eval_launch_owned_resources";
const SCRIPT_OWNER: &str = "gray_eval_script";
const LOADOP_OWNER: &str = "gray_ab_loadop";
const CONTROL_OWNER: &str = "gray_layer_control";
const CONTROL_SCOPE: &str = "gray_layer_control_owned_installation_properties_target_stopped";
const REFBENCH_OWNER: &str = "gray_refbench_loadop";
const WHITEBOX_OWNER: &str = "whitebox_refbench_native";
const REFBENCH_PACKAGE: &str = "io.github.hgamey.refbench";
const REFBENCH_SCOPE: &str = "gray_refbench_loadop_owned_installation_properties_outputs_target_stopped";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum RefbenchArm { BaselineOff, BaselineOn, RewriteOff, RewriteOn }
impl RefbenchArm {
    pub const ALL: [Self; 4] = [Self::BaselineOff, Self::BaselineOn, Self::RewriteOff, Self::RewriteOn];
    pub fn label(self) -> &'static str { match self {
        Self::BaselineOff => "A_noloayer_off", Self::BaselineOn => "B_nolayer_on",
        Self::RewriteOff => "C_rewrite_off", Self::RewriteOn => "D_rewrite_on",
    }}
    pub fn knob(self) -> &'static str { if matches!(self, Self::BaselineOn | Self::RewriteOn) { "on" } else { "off" } }
    pub fn layered(self) -> bool { matches!(self, Self::RewriteOff | Self::RewriteOn) }
    fn index(self) -> u64 { Self::ALL.iter().position(|arm| *arm == self).unwrap() as u64 }
}

pub(crate) struct RefbenchOutput { pub bytes: Vec<u8>, pub source_path: String }
pub(crate) struct RefbenchOutputs {
    pub refbench: Option<RefbenchOutput>,
    pub layer: Option<RefbenchOutput>,
    pub started: Option<RefbenchOutput>,
}
const CAP: u64 = 8 * 1024 * 1024;

pub(crate) struct Session {
    lease: RefCell<Lease>,
    adb: PathBuf,
    pub dir: PathBuf,
    state: RefCell<Value>,
    // A failed device check stops this attempt. A fresh recovery owner can retry
    // known completed operations; persistent pending/uncertain writes cannot.
    stopped: RefCell<Option<String>>,
    known_dirs: RefCell<HashSet<String>>,
}

impl Session {
    pub fn new(lease: Lease, out: &Path, pkg: &str, origin: &str) -> Result<Self, String> {
        Self::new_owned(lease, out, pkg, origin, "standalone", None)
    }

    pub fn new_eval(lease: Lease, out: &Path, pkg: &str, origin: &str, marker: Option<&str>) -> Result<Self, String> {
        Self::new_owned(lease, out, pkg, origin, EVAL_OWNER, marker)
    }
    pub fn new_script(lease: Lease, out: &Path, pkg: &str, origin: &str) -> Result<Self, String> {
        Self::new_owned(lease, out, pkg, origin, SCRIPT_OWNER, None)
    }

    pub fn new_loadop(lease: Lease, out: &Path, pkg: &str, origin: &str) -> Result<Self,String> {
        Self::new_owned(lease,out,pkg,origin,LOADOP_OWNER,None)
    }

    pub fn new_refbench_loadop(lease: Lease, out: &Path, origin: &str) -> Result<Self, String> {
        Self::new_owned(lease, out, REFBENCH_PACKAGE, origin, REFBENCH_OWNER, None)
    }
    pub fn new_whitebox_refbench(lease: Lease, out: &Path) -> Result<Self, String> {
        Self::new_owned(lease, out, REFBENCH_PACKAGE, "device", WHITEBOX_OWNER, None)
    }
    pub fn resume_whitebox_refbench(dir: &Path, serial: &str) -> Result<Self, String> {
        Self::resume_owned(dir, serial, WHITEBOX_OWNER)
    }

    pub fn new_control(lease: Lease, out: &Path, pkg: &str, origin: &str) -> Result<Self, String> {
        Self::new_owned(lease, out, pkg, origin, CONTROL_OWNER, None)
    }

    fn new_owned(lease: Lease, out: &Path, pkg: &str, origin: &str, owner: &str, marker: Option<&str>) -> Result<Self, String> {
        if !graylayer::valid_package(pkg) || !matches!(origin, "device" | "synthetic") {
            return Err("invalid gray capture package or evidence origin".into());
        }
        if marker.is_some_and(|name| !valid_marker(name)) { return Err("launch marker must be a safe, distinct filename in the target files directory".into()); }
        std::fs::create_dir_all(out).map_err(|e| e.to_string())?;
        let token = lease.token().to_owned();
        // The control descriptor publishes this relative directory. Its name
        // must not disclose the internal Lease token used by other producers.
        let name = if owner == CONTROL_OWNER {
            format!("gray-session-{}-{}", std::process::id(), stamp()?)
        } else { format!("gray-{token}") };
        let output_root = out.canonicalize().map_err(|e| e.to_string())?;
        let dir = output_root.join(name);
        std::fs::create_dir(&dir).map_err(|e| e.to_string())?;
        for parent in dir.ancestors() {
            fs::File::open(parent).and_then(|f| f.sync_all()).map_err(|e| e.to_string())?;
        }
        let mut state = json!({"schema":SCHEMA,"serial":lease.serial,"token":token,"package":pkg,
            "completion_owner":owner,"launch_marker":marker,"generation":0,"round_label":null,"base_recovery":null,
            "stage":"new","boot_id":null,"native_dir":null,"properties":{},"files":[],
            "remote":format!("/data/local/tmp/phonefarm-gray-{token}"),
            "staging_created":false,"installation_owned":false,"state_owned":false,
            "write_pending":null,"transport_uncertain":false,"evidence_origin":origin,
            "created_at":chrono::Utc::now().to_rfc3339()});
        if owner == CONTROL_OWNER {
            state["schema"] = json!(CONTROL_RESOURCE_SCHEMA);
            state["control_session_id"] = json!(dir.file_name().and_then(|s| s.to_str()).ok_or("invalid control session directory")?);
            state["control_resources"] = json!({"path":"eval-session.json","schema":CONTROL_PARENT_SCHEMA});
        }
        if let Some(binding) = lease.device_binding() {
            if owner != CONTROL_OWNER { return Err("device binding is only supported by control sessions".into()); }
            state["device_binding"] = json!(binding);
            state["lease_scope"] = json!("device");
        }
        let session = Self { lease:RefCell::new(lease), adb:adb_path()?, dir,
            state:RefCell::new(state), stopped:RefCell::new(None), known_dirs:RefCell::new(HashSet::new()) };
        session.save()?;
        Ok(session)
    }

    pub fn resume(dir: &Path, serial: &str) -> Result<Self, String> {
        Self::resume_owned(dir, serial, "standalone")
    }

    pub fn resume_eval(dir: &Path, serial: &str) -> Result<Self, String> {
        Self::resume_owned(dir, serial, EVAL_OWNER)
    }

    pub fn resume_loadop(dir:&Path,serial:&str)->Result<Self,String> {Self::resume_owned(dir,serial,LOADOP_OWNER)}

    pub fn resume_refbench_loadop(dir: &Path, serial: &str) -> Result<Self, String> {
        Self::resume_owned(dir, serial, REFBENCH_OWNER)
    }

    pub fn resume_control(dir: &Path, serial: &str) -> Result<Self, String> {
        Self::resume_owned(dir, serial, CONTROL_OWNER)
    }

    fn resume_owned(dir: &Path, serial: &str, owner: &str) -> Result<Self, String> {
        let dir = dir.canonicalize().map_err(|e| e.to_string())?;
        let state: Value = serde_json::from_slice(&bounded(&dir.join("session.json"), 1024 * 1024)?).map_err(|e| e.to_string())?;
        validate(&state, &dir, serial)?;
        if owner == EVAL_OWNER {
            if !matches!(completion_owner(&state)?,EVAL_OWNER|SCRIPT_OWNER) {return Err("this session requires its dedicated recovery entry point".into());}
        } else { require_owner(&state, owner)?; }
        pending_clear(&state)?;
        let storage = crate::lease_storage::resolve();
        let lease = if let Some(binding) = state.get("device_binding") {
            let binding = crate::device_registry::parse_binding(&binding.to_string())?;
            Lease::acquire_device_recovery(&storage, &binding, &dir, crate::devlease::wait_timeout()?)?
        } else {
            let (root, legacy) = storage.roots()?;
            Lease::acquire_recovery(&root, &legacy, serial, &dir, crate::devlease::wait_timeout()?)?
        };
        Ok(Self {lease:RefCell::new(lease), adb:adb_path()?, dir, state:RefCell::new(state),
            stopped:RefCell::new(None), known_dirs:RefCell::new(HashSet::new())})
    }

    fn save(&self) -> Result<(), String> {
        durable_replace(&self.dir.join("session.json"), &serde_json::to_vec_pretty(&*self.state.borrow()).map_err(|e| e.to_string())?)
    }
    fn hold(&self) -> Result<(), String> { self.lease.borrow_mut().hold_for_recovery(&self.dir) }
    fn require_eval(&self) -> Result<(), String> { require_parent(&self.state.borrow()) }
    fn package(&self) -> String { self.state.borrow()["package"].as_str().unwrap().into() }
    fn remote(&self) -> String { self.state.borrow()["remote"].as_str().unwrap().into() }
    fn ready(&self) -> Result<(), String> {
        pending_clear(&self.state.borrow())?;
        if let Some(error) = &*self.stopped.borrow() { return Err(error.clone()); }
        Ok(())
    }
    fn stop<T>(&self, error: String) -> Result<T, String> {
        if self.stopped.borrow().is_none() { *self.stopped.borrow_mut() = Some(error.clone()); }
        Err(error)
    }

    fn command(&self, operation: &str, args: &[String], timeout: u64) -> Result<crate::lease_command::Output, String> {
        let args: Vec<String> = [vec!["-s".into(), self.lease.borrow().serial.clone()], args.to_vec()].concat();
        journal(&self.dir, json!({"event":"command_intent","operation":operation,"args":args}))?;
        let result = crate::lease_command::run(&self.lease.borrow(), &self.adb, &args,
            &self.dir.join("commands"), operation, Duration::from_millis(timeout));
        let output = match result {
            Ok(output) => output,
            Err(error) => {
                self.state.borrow_mut()["transport_uncertain"] = json!(true);
                let saved = self.save();
                let held = self.hold();
                return self.stop(format!("{operation}: {error}; uncertainty_saved={}; hold_saved={}", saved.is_ok(), held.is_ok()));
            }
        };
        // These raw transport files exist independently of later result JSON.
        journal(&self.dir, json!({"event":"command_result","operation":operation,
            "code":output.code,"stdout_path":output.stdout_path}))?;
        if output.code != 0 {
            return self.stop(format!("{operation}: exit {}: {}", output.code, output.stderr.trim()));
        }
        Ok(output)
    }
    fn read(&self, operation: &str, command: &str) -> Result<String, String> {
        self.ready()?;
        Ok(self.command(operation, &["shell".into(), command.into()], 30_000)?.stdout)
    }
    fn root_read(&self, operation: &str, command: &str) -> Result<String, String> {
        self.read(operation, &format!("su -c {}", q(command)))
    }
    fn write_update(&self, operation: &str, args: &[String], update: impl FnOnce(&mut Value)) -> Result<(), String> {
        self.mutate(operation, args, 120_000, update).map(|_| ())
    }
    fn mutate(&self, operation: &str, args: &[String], timeout: u64, update: impl FnOnce(&mut Value)) -> Result<crate::lease_command::Output, String> {
        self.ready()?;
        self.hold()?;
        let pending = json!({"operation":operation,"args":args});
        {
            let mut state = self.state.borrow_mut();
            state["write_pending"] = pending.clone();
            if state["schema"] == SCHEMA || state["schema"] == CONTROL_RESOURCE_SCHEMA { state["base_recovery"] = Value::Null; }
        }
        self.save()?;
        let output = self.command(operation, args, timeout)?;
        {
            let mut state = self.state.borrow_mut();
            update(&mut state);
            state["write_pending"] = Value::Null;
        }
        if let Err(error) = self.save() {
            self.state.borrow_mut()["write_pending"] = pending;
            return self.stop(format!("write completion persistence failed: {error}"));
        }
        Ok(output)
    }
    fn write(&self, operation: &str, command: &str) -> Result<(), String> {
        self.write_update(operation, &["shell".into(), command.into()], |_| {})
    }
    fn root_write(&self, operation: &str, command: &str) -> Result<(), String> {
        self.write(operation, &format!("su -c {}", q(command)))
    }
    fn root_update(&self, operation: &str, command: &str, update: impl FnOnce(&mut Value)) -> Result<(), String> {
        self.write_update(operation, &["shell".into(), format!("su -c {}", q(command))], update)
    }

    /// Internal identity only: token must never enter the public descriptor.
    pub fn control_identity(&self) -> Result<Value, String> {
        require_owner(&self.state.borrow(), CONTROL_OWNER)?;
        let state = self.state.borrow();
        let mut identity = json!({"serial":state["serial"],"token":state["token"],"boot_id":state["boot_id"],
            "native_dir":state["native_dir"],"package":state["package"],"session":self.dir,
            "completion_owner":CONTROL_OWNER,"control_session_id":state["control_session_id"],
            "control_parent_schema":state["control_resources"]["schema"],
            "evidence_origin":state["evidence_origin"]});
        if let Some(binding) = state.get("device_binding") {
            identity["device_binding"] = binding.clone();
            identity["lease_scope"] = json!("device");
        }
        Ok(identity)
    }

    pub fn control_ready(&self) -> Result<(), String> {
        require_owner(&self.state.borrow(), CONTROL_OWNER)?;
        self.ready()
    }

    pub fn control_has_resources(&self) -> Result<bool, String> {
        require_owner(&self.state.borrow(), CONTROL_OWNER)?;
        Ok(self.state.borrow()["schema"] == CONTROL_RESOURCE_SCHEMA)
    }

    pub fn eval_identity(&self) -> Result<Value, String> {
        self.require_eval()?;
        let state = self.state.borrow();
        let mut identity = json!({"serial":state["serial"],"token":state["token"],"boot_id":state["boot_id"],
            "native_dir":state["native_dir"],"package":state["package"],"session":self.dir,
            "completion_owner":completion_owner(&state)?,
            "control_parent_schema":state["control_resources"]["schema"],
            "evidence_origin":state["evidence_origin"],"base_recovery":state["base_recovery"]});
        if let Some(binding) = state.get("device_binding") {
            identity["device_binding"] = binding.clone();
            identity["lease_scope"] = json!("device");
        }
        Ok(identity)
    }
    pub fn eval_ready(&self) -> Result<(), String> { self.require_eval()?; self.ready() }
    pub fn eval_transport(&self, operation: &str, args: &[String], timeout_ms: u64, mutation: bool) -> Result<crate::lease_command::Output, String> {
        self.eval_ready()?;
        if mutation {
            if self.state.borrow()["stage"] != "active" { return Err("eval mutations require an active gray installation session".into()); }
            self.mutate(operation, args, timeout_ms, |_| {})
        } else {
            self.command(operation, args, timeout_ms)
        }
    }
    pub fn eval_present(&self, path: &str) -> Result<bool, String> { self.eval_ready()?; self.present(path) }
    pub fn eval_stop_target(&self) -> Result<(), String> {
        self.eval_ready()?;
        if self.state.borrow()["stage"] != "active" { return Err("eval target stop requires an active gray installation session".into()); }
        self.stop_target()
    }
    pub fn eval_launch_marker(&self) -> Result<Option<String>, String> {
        self.eval_ready()?;
        let marker = self.state.borrow()["launch_marker"].as_str().map(str::to_owned);
        let Some(marker) = marker else { return Ok(None); };
        let path = launch_marker_path(&self.package(), &marker);
        if !self.present(&path)? { return Ok(None); }
        self.root_read("launch_marker", &format!("cat {}", q(&path))).map(Some)
    }

    // A missing directory is proved through a successful ancestor listing, not
    // a failed ls/test. Positive directory knowledge only avoids repeated walks;
    // every actual absence decision still uses a fresh successful listing.
    fn entries(&self, dir: &str) -> Result<Option<Vec<String>>, String> {
        if dir != "/" && !self.known_dirs.borrow().contains(dir) {
            let (parent, name) = split_path(dir)?;
            if self.entries(&parent)?.is_none_or(|names| !names.iter().any(|n| n == &name)) {
                return Ok(None);
            }
        }
        let output = self.root_read("directory_listing", &format!("ls -1A -- {}", q(dir)))?;
        self.known_dirs.borrow_mut().insert(dir.into());
        Ok(Some(output.lines().map(str::to_owned).collect()))
    }
    fn present(&self, path: &str) -> Result<bool, String> {
        let (parent, name) = split_path(path)?;
        Ok(self.entries(&parent)?.is_some_and(|names| names.iter().any(|n| n == &name)))
    }
    fn expect_presence(&self, path: &str, expected: bool) -> Result<(), String> {
        if self.present(path)? != expected {
            return self.stop(format!("file presence readback differs: {path}, expected_present={expected}"));
        }
        Ok(())
    }
    fn native_dir(&self) -> Result<String, String> {
        let dump = self.read("native_directory", &format!("dumpsys package {}", q(&self.package())))?;
        let paths: Vec<_> = dump.lines().filter_map(|l| l.trim().strip_prefix("legacyNativeLibraryDir=")).collect();
        if paths.len() != 1 || !graylayer::valid_install_dir(paths[0]) {
            return self.stop("unrecognized current native library directory".into());
        }
        let dir = format!("{}/arm64", paths[0]);
        if self.entries(&dir)?.is_none() { return self.stop("target has no readable arm64 native library directory".into()); }
        Ok(dir)
    }
    fn property(&self, property: &str) -> Result<String, String> {
        let value = self.read("property_readback", &format!("getprop {}", q(property)))?;
        let value = value.strip_suffix('\n').unwrap_or(&value).to_owned();
        if value.contains(['\n', '\r', '\0']) || value.len() > graylayer::PROP_VALUE_MAX {
            return self.stop(format!("invalid property readback: {property}"));
        }
        Ok(value)
    }
    fn set_property(&self, property: &str, value: &str) -> Result<(), String> {
        // debug.* is settable by the shell domain everywhere; some root tools (Honor, su in u:r:kernel) are denied it by init.
        let command = format!("setprop {} {}", q(property), q(value));
        if property.starts_with("debug.") { self.write("property_write", &command)?; } else { self.root_write("property_write", &command)?; }
        if self.property(property)? != value {
            return self.stop(format!("property readback differs: {property}"));
        }
        Ok(())
    }
    fn stop_target(&self) -> Result<(), String> {
        let package = self.package();
        self.write("stop_target", &format!("am force-stop {}", q(&package)))?;
        if self.target_alive()? {
            return self.stop("target process absence not verified".into());
        }
        Ok(())
    }

    pub fn target_alive(&self) -> Result<bool, String> {
        let ps = self.root_read("target_readback", "ps -A -o NAME")?;
        match target_in_process_list(&ps, &self.package()) {
            Ok(alive) => Ok(alive),
            Err(error) => self.stop(error),
        }
    }

    pub fn crash_log(&self) -> Result<String, String> {
        self.root_read("crash_log", "logcat -b crash -d -t 200")
    }

    pub fn prepare(&self) -> Result<(), String> {
        self.ready()?;
        if self.state.borrow()["stage"] != "new" { return Err("gray capture already prepared".into()); }
        let boot = self.read("boot_identity", "cat /proc/sys/kernel/random/boot_id")?;
        let boot = boot.trim();
        if boot.is_empty() || !boot.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-') {
            return self.stop("invalid device boot identity".into());
        }
        let native = self.native_dir()?;
        for path in [STATE.to_string(), format!("{native}/{SO}"), self.remote()] {
            if self.present(&path)? { return self.stop(format!("pre-existing installation/session path; refusing overwrite: {path}")); }
        }
        let mut properties = Map::new();
        for property in LAYER_PROPERTIES {
            properties.insert(property.into(), json!(self.property(property)?));
        }
        if properties["debug.vulkan.layers"] != "" {
            return self.stop("global Vulkan layer is already enabled; reconcile it before capture".into());
        }
        let token = self.state.borrow()["token"].as_str().unwrap().to_owned();
        let mut files = Vec::new();
        let paths = session_output_paths(&self.state.borrow())?;
        for path in paths {
            let (parent, name) = split_path(&path)?;
            let backup = format!("{parent}/.phonefarm-gray-{token}-{name}.bak");
            if self.present(&backup)? { return self.stop(format!("backup already exists: {backup}")); }
            files.push(json!({"path":path,"backup":backup,"original_present":self.present(&path)?,
                "acquired":false,"backup_done":false,"restored":false,"archive":null}));
        }
        let mut state = self.state.borrow_mut();
        state["boot_id"] = json!(boot); state["native_dir"] = json!(native);
        state["properties"] = Value::Object(properties); state["files"] = json!(files);
        state["stage"] = json!("prepared");
        drop(state);
        self.save()
    }

    pub fn install(&self, so: Option<&str>, ts: bool, extras: Option<&str>) -> Result<(), String> {
        self.install_mode(so, ts, extras, None, true, None)
    }

    pub fn install_storeop(&self, so: Option<&str>, rule: &str, extras: Option<&str>) -> Result<(), String> {
        let rule = storeop_rule(rule)?;
        self.install_mode(so, false, extras, Some(&rule), true, None)
    }
    pub fn install_script(&self, so: Option<&str>) -> Result<(), String> {
        require_owner(&self.state.borrow(), SCRIPT_OWNER)?;
        self.install_mode(so, false, None, None, false, None)
    }

    pub fn install_loadop(&self,so:Option<&str>)->Result<(),String> {
        require_owner(&self.state.borrow(),LOADOP_OWNER)?;
        self.install_mode(so,false,None,None,false,None)
    }

    pub fn install_control(&self, so: Option<&str>, mode: &str) -> Result<(), String> {
        require_owner(&self.state.borrow(), CONTROL_OWNER)?;
        if let Some(rejection) = graylayer::mode_admission_rejection(mode, so.is_some()) {
            return Err(rejection.to_string());
        }
        let properties = control_properties(mode)?;
        self.install_mode(so, false, None, None, false, Some(properties))
    }

    fn install_mode(&self, so: Option<&str>, ts: bool, extras: Option<&str>, storeop: Option<&str>, frameaudit: bool, control: Option<[&str; 12]>) -> Result<(), String> {
        self.ready()?;
        if self.state.borrow()["stage"] != "prepared" { return Err("gray capture must be prepared before install".into()); }
        let local = so.map(str::to_owned).unwrap_or_else(graylayer::layer_so_path);
        let local = crate::eval::resolve_path(&local).ok_or("layer SO not found")?;
        let metadata = fs::metadata(&local).map_err(|e| e.to_string())?;
        if !metadata.is_file() || metadata.len() == 0 { return Err("layer SO must be a nonempty regular file".into()); }
        let extras = intent_extras(extras.unwrap_or(""))?;
        let component = self.launch_component()?;
        self.activate_outputs()?;
        self.install_owned_layer(&local)?;
        for (index, property) in LAYER_PROPERTIES.into_iter().enumerate().filter(|(_, p)| *p != "debug.vulkan.layers") {
            let value = if let Some(values) = &control { values[index] } else { match property {
                "debug.knobs.passdump" if completion_owner(&self.state.borrow())? != LOADOP_OWNER => "1",
                "debug.knobs.frameaudit" if frameaudit => "1",
                "debug.knobs.frameaudit_ts" if ts => "1",
                graylayer::STOREOP_PROP => storeop.unwrap_or("0"),
                graylayer::STOREOP_LIVE_PROP if storeop.is_some() => "1",
                _ => "0",
            }};
            self.set_property(property, value)?;
        }
        self.set_property("debug.vulkan.layers", LAYER_NAME)?;
        self.launch_target(&component, &extras)
    }

    // Activation owns the target/output obligations even before a layer exists.
    fn activate_outputs(&self) -> Result<(), String> {
        self.ready()?;
        if self.state.borrow()["stage"] != "prepared" { return Err("gray outputs must be prepared before activation".into()); }
        self.hold()?;
        self.state.borrow_mut()["stage"] = json!("active");
        if let Err(error) = self.save() { return self.stop(format!("active intent persistence failed: {error}")); }
        self.stop_target()?;
        let files = self.state.borrow()["files"].as_array().unwrap().clone();
        for (index, file) in files.iter().enumerate() {
            let path = file["path"].as_str().unwrap();
            let backup = file["backup"].as_str().unwrap();
            self.expect_presence(path, file["original_present"] == true)?;
            self.expect_presence(backup, false)?;
            if file["original_present"] == true {
                self.root_update("backup_history", &format!("mv -- {} {}", q(path), q(backup)), |state| {
                    state["files"][index]["backup_done"] = json!(true);
                    state["files"][index]["acquired"] = json!(true);
                })?;
                self.expect_presence(path, false)?; self.expect_presence(backup, true)?;
            } else {
                // Preparation-time absence alone does not establish ownership.
                // Acquire only after the target stopped and this fresh check.
                self.state.borrow_mut()["files"][index]["acquired"] = json!(true);
                if let Err(error) = self.save() { return self.stop(format!("output ownership persistence failed: {error}")); }
            }
        }
        Ok(())
    }

    fn install_owned_layer(&self, local: &Path) -> Result<(), String> {
        self.ready()?;
        let state = self.state.borrow();
        if state["stage"] != "active" || ["installation_owned", "state_owned", "staging_created"].iter().any(|key| state[*key] != false)
            || state["files"].as_array().is_none_or(|files| files.iter().any(|f| f["acquired"] != true || f["restored"] == true)) {
            return Err("layer installation requires active outputs and no existing owned installation".into());
        }
        drop(state);
        let remote = self.remote();
        // adb push runs as shell; a root-owned 0755 directory would reject it.
        self.write_update("create_staging", &["shell".into(), format!("mkdir -- {}", q(&remote))], |s| s["staging_created"] = json!(true))?;
        self.write_update("push_layer", &["push".into(), local.to_string_lossy().into(), format!("{remote}/{SO}")], |_| {})?;
        let installed = format!("{}/{SO}", self.state.borrow()["native_dir"].as_str().unwrap());
        self.root_update("install_layer", &format!("cp -- {} {}", q(&format!("{remote}/{SO}")), q(&installed)), |s| s["installation_owned"] = json!(true))?;
        self.root_write("layer_mode", &format!("chmod 755 {}", q(&installed)))?;
        self.root_write("layer_owner", &format!("chown system:system {}", q(&installed)))?;
        self.root_write("layer_context", &format!("chcon u:object_r:apk_data_file:s0 {}", q(&installed)))?;
        self.expect_presence(&installed, true)?;
        let mode = self.root_read("layer_metadata", &format!("stat -c '%a %u %g' {}", q(&installed)))?;
        let context = self.root_read("layer_context_readback", &format!("ls -Zd -- {}", q(&installed)))?;
        if mode.trim() != "755 1000 1000" || !context.split_whitespace().any(|s| s == "u:object_r:apk_data_file:s0") {
            return self.stop("installed layer mode/owner/context readback differs".into());
        }
        let registration = format!("{}|{}", self.package(), self.state.borrow()["native_dir"].as_str().unwrap());
        self.root_update("register_layer", &format!("printf '%s\\n' {} > {}", q(&registration), q(STATE)), |s| s["state_owned"] = json!(true))?;
        if self.root_read("registration_readback", &format!("cat {}", q(STATE)))?.trim_end_matches('\n') != registration {
            return self.stop("layer registration readback differs".into());
        }
        Ok(())
    }

    fn launch_component(&self) -> Result<String, String> {
        let activity = self.read("resolve_activity", &format!("cmd package resolve-activity --brief {}", q(&self.package())))?;
        let component = activity.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("").trim();
        if component.split_once('/').is_none_or(|(pkg, class)| pkg != self.package() || class.is_empty()
            || !class.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'$'))) {
            return Err("could not resolve a launch component for the target package".into());
        }
        Ok(component.into())
    }
    fn launch_target(&self, component: &str, extras: &[String]) -> Result<(), String> {
        let extras = extras.iter().map(|arg| q(arg)).collect::<Vec<_>>().join(" ");
        self.write("launch_target", &format!("am start -n {} {extras}", q(component)))
    }

    pub fn restart_eval(&self, label: &str, rule: Option<&str>, extras: &str) -> Result<(), String> {
        require_owner(&self.state.borrow(), EVAL_OWNER)?;
        self.restart_mode(label, rule, false, extras)
    }
    pub fn restart_script(&self, label: &str, layered: bool, loadop: bool, storeop: &str) -> Result<(), String> {
        require_owner(&self.state.borrow(), SCRIPT_OWNER)?;
        self.restart_mode(label, layered.then_some(storeop), loadop && layered, "")
    }
    pub fn restart_loadop(&self,label:&str,rewrite:bool)->Result<(),String> {
        require_owner(&self.state.borrow(),LOADOP_OWNER)?;
        self.restart_mode(label,Some("0"),rewrite,"")
    }

    pub fn start_refbench_arm(&self, arm: RefbenchArm, frames: u32, intensity: u32, so: Option<&str>) -> Result<(), String> {
        require_owner(&self.state.borrow(), REFBENCH_OWNER)?;
        refbench_next_arm(&self.state.borrow(), arm)?;
        self.ready()?;
        if !(60..=100000).contains(&frames) || !(1..=64).contains(&intensity) {
            return Err("refbench frames must be 60..100000 and intensity 1..64".into());
        }
        let extras = intent_extras(&format!("--es scene bw_pingpong --es run_id {} --es frames {frames} --es intensity {intensity} --es knob.loadop {}", arm.label(), arm.knob()))?;
        let local = if arm == RefbenchArm::RewriteOff {
            let path = so.map(str::to_owned).unwrap_or_else(graylayer::layer_so_path);
            let path = crate::eval::resolve_path(&path).ok_or("layer SO not found")?;
            let metadata = fs::metadata(&path).map_err(|e| e.to_string())?;
            if !metadata.is_file() || metadata.len() == 0 { return Err("layer SO must be a nonempty regular file".into()); }
            Some(path)
        } else { None };
        let state = self.state.borrow().clone();
        let native = state["native_dir"].as_str().ok_or("refbench is not prepared")?;
        if self.read("refbench_boot", "cat /proc/sys/kernel/random/boot_id")?.trim() != state["boot_id"].as_str().unwrap_or("") {
            return self.stop("device boot changed before refbench arm".into());
        }
        if self.native_dir()? != native { return self.stop("target installation changed before refbench arm".into()); }
        if arm == RefbenchArm::RewriteOn {
            if state["installation_owned"] != true || state["state_owned"] != true {
                return Err("last refbench arm requires the original owned installation".into());
            }
            self.check_registration()?;
            self.expect_presence(&format!("{native}/{SO}"), true)?;
            if self.property("debug.vulkan.layers")? != "" { return self.stop("previous refbench layer has not been narrowed".into()); }
        } else {
            // A/B are genuinely uninstalled; C proves the same condition before
            // its first install. Merely turning loadop off is not this baseline.
            if state["installation_owned"] != false || state["state_owned"] != false || state["staging_created"] != false {
                return Err("unlayered refbench arm already owns an installation".into());
            }
            self.expect_presence(&format!("{native}/{SO}"), false)?;
            self.expect_presence(STATE, false)?;
            if self.property("debug.vulkan.layers")? != "" { return self.stop("refbench unlayered condition is not clear".into()); }
        }
        let component = self.launch_component()?;
        if arm == RefbenchArm::BaselineOff {
            self.activate_outputs()?;
        } else {
            if self.target_alive()? { return Err("previous refbench target has not exited; no next arm launched".into()); }
            self.stop_target()?;
            self.clear_round_outputs()?;
        }
        {
            let mut state = self.state.borrow_mut();
            for file in state["files"].as_array_mut().unwrap() { file["archive"] = Value::Null; }
            state["generation"] = json!(arm.index());
            state["round_label"] = json!(arm.label());
        }
        if let Err(error) = self.save() { return self.stop(format!("refbench arm intent persistence failed: {error}")); }
        journal(&self.dir, json!({"event":"refbench_arm","label":arm.label(),"layered":arm.layered(),
            "frames":frames,"intensity":intensity,"knob_loadop":arm.knob(),"extras":extras}))?;
        if let Some(local) = local { self.install_owned_layer(&local)?; }
        if arm.layered() {
            for (property, value) in LAYER_PROPERTIES.into_iter().zip(control_properties("loadop")?) {
                if property != "debug.vulkan.layers" { self.set_property(property, value)?; }
            }
            self.set_property("debug.vulkan.layers", LAYER_NAME)?;
        }
        self.launch_target(&component, &extras)
    }

    pub fn refbench_outputs(&self) -> Result<RefbenchOutputs, String> {
        require_owner(&self.state.borrow(), REFBENCH_OWNER)?;
        self.ready()?;
        if self.state.borrow()["stage"] != "active" || self.state.borrow()["round_label"].is_null() {
            return Err("refbench outputs require a started arm".into());
        }
        Ok(RefbenchOutputs {
            refbench: self.refbench_raw([launch_marker_path(REFBENCH_PACKAGE, "refbench_out.json")])?,
            layer: self.refbench_raw(output_paths(REFBENCH_PACKAGE).into_iter().filter(|p| p.contains("knobs_layer_out")))?,
            started: self.refbench_raw([launch_marker_path(REFBENCH_PACKAGE, "refbench_started")])?,
        })
    }

    pub fn start_whitebox_refbench_arm(&self, index: u64, frames: u32, intensity: u32, quality: bool) -> Result<String, String> {
        require_owner(&self.state.borrow(), WHITEBOX_OWNER)?;
        self.ready()?;
        let state = self.state.borrow().clone();
        if index >= 130 || !(60..=100000).contains(&frames) || !(1..=64).contains(&intensity)
            || quality != (index < 2)
            || (index == 0 && (state["stage"] != "prepared" || !state["round_label"].is_null()))
            || (index > 0 && (state["stage"] != "active" || state["generation"].as_u64().and_then(|v|v.checked_add(1)) != Some(index))) {
            return Err("invalid whitebox arm sequence/configuration".into());
        }
        if state["installation_owned"] != false || state["state_owned"] != false || state["staging_created"] != false {
            return Err("native whitebox must not own an injected layer".into());
        }
        if self.property("debug.vulkan.layers")? != "" {return self.stop("unexpected global Vulkan layer".into());}
        self.expect_presence(STATE, false)?;
        self.expect_presence(&format!("{}/{}",state["native_dir"].as_str().ok_or("missing native dir")?,SO),false)?;
        if self.native_dir()? != state["native_dir"].as_str().unwrap()
            || self.read("whitebox_boot","cat /proc/sys/kernel/random/boot_id")?.trim() != state["boot_id"].as_str().unwrap() {
            return self.stop("whitebox installation/boot changed".into());
        }
        if index == 0 {self.activate_outputs()?;} else {
            if self.target_alive()? {return Err("previous whitebox arm still running".into());}
            self.stop_target()?; self.clear_round_outputs()?;
        }
        let on = index % 2 == 1;
        let label = format!("wb_{index}_{}",if on {"on"}else{"off"});
        {
            let mut state=self.state.borrow_mut();
            for file in state["files"].as_array_mut().unwrap(){file["archive"]=Value::Null;}
            state["generation"]=json!(index);state["round_label"]=json!(label);
        }
        self.save()?;
        let extras=intent_extras(&format!("--es scene bw_pingpong --es run_id {label} --es frames {frames} --es intensity {intensity} --es knob.loadop {} --es capture.bw_hash {} --es capture.gpu_timestamps {}",
            if on {"on"}else{"off"},if quality {"on"}else{"off"},if quality {"off"}else{"on"}))?;
        let component=self.launch_component()?;
        self.launch_target(&component,&extras)?;
        Ok(label)
    }

    pub fn whitebox_refbench_output(&self,name:&str)->Result<Option<RefbenchOutput>,String>{
        require_owner(&self.state.borrow(),WHITEBOX_OWNER)?;
        if !matches!(name,"refbench_out.json"|"refbench_started"|"refbench_gpu_timestamps.json"|"refbench_bw_hash.json"){
            return Err("unknown whitebox output".into());
        }
        self.refbench_raw([launch_marker_path(REFBENCH_PACKAGE,name)])
    }

    pub fn finish_whitebox_refbench(&self,error:Option<&str>)->Result<Value,String>{
        self.finish_owned(error,WHITEBOX_OWNER)
    }

    fn refbench_raw(&self, paths: impl IntoIterator<Item=String>) -> Result<Option<RefbenchOutput>, String> {
        for path in paths {
            if !self.present(&path)? { continue; }
            let output = self.command("refbench_output", &["shell".into(), format!("su -c {}", q(&format!("cat {}", q(&path))))], 30_000)?;
            return Ok(Some(RefbenchOutput { bytes: bounded(&output.stdout_path, CAP)?, source_path: path }));
        }
        Ok(None)
    }

    fn restart_mode(&self, label: &str, rule: Option<&str>, loadop: bool, extras: &str) -> Result<(), String> {
        self.eval_ready()?;
        if !valid_label(label) { return Err("invalid eval round label".into()); }
        let rule = rule.map(storeop_rule).transpose()?;
        let extras = intent_extras(extras)?;
        let state = self.state.borrow();
        if state["stage"] != "active" || state["installation_owned"] != true || state["state_owned"] != true
            || state["files"].as_array().is_none_or(|files| files.iter().any(|f| f["acquired"] != true || f["restored"] == true)) {
            return Err("eval restart requires the original active installation and owned outputs".into());
        }
        let generation = state["generation"].as_u64().ok_or("missing eval generation")?.checked_add(1).ok_or("eval generation overflow")?;
        let expected_boot = state["boot_id"].as_str().unwrap().to_owned();
        let expected_native = state["native_dir"].as_str().unwrap().to_owned();
        drop(state);
        if self.read("restart_boot", "cat /proc/sys/kernel/random/boot_id")?.trim() != expected_boot {
            return self.stop("device boot changed before eval restart".into());
        }
        if self.native_dir()? != expected_native { return self.stop("target installation changed before eval restart".into()); }
        self.check_registration()?;
        self.expect_presence(&format!("{expected_native}/{SO}"), true)?;
        let component = self.launch_component()?;
        self.stop_target()?;
        self.narrow()?;
        self.clear_round_outputs()?;
        {
            let mut state = self.state.borrow_mut();
            for file in state["files"].as_array_mut().unwrap() { file["archive"] = Value::Null; }
            state["generation"] = json!(generation);
            state["round_label"] = json!(label);
        }
        if let Err(error) = self.save() { return self.stop(format!("round transition persistence failed: {error}")); }
        journal(&self.dir, json!({"event":"eval_round","generation":generation,"label":label,"rule":rule}))?;
        for property in LAYER_PROPERTIES.into_iter().filter(|p| *p != "debug.vulkan.layers") {
            let value = match property {
                "debug.knobs.passdump" if rule.is_some() && completion_owner(&self.state.borrow())? != LOADOP_OWNER => "1",
                "debug.knobs.loadop" if loadop => "1",
                graylayer::STOREOP_PROP => rule.as_deref().unwrap_or("0"),
                _ => "0",
            };
            self.set_property(property, value)?;
        }
        self.set_property("debug.vulkan.layers", if rule.is_some() { LAYER_NAME } else { "" })?;
        self.launch_target(&component, &extras)
    }

    fn clear_round_outputs(&self) -> Result<(), String> {
        let files = self.state.borrow()["files"].as_array().unwrap().clone();
        for (index, file) in files.iter().enumerate() {
            let path = file["path"].as_str().unwrap();
            self.expect_presence(file["backup"].as_str().unwrap(), file["original_present"] == true)?;
            if self.present(path)? {
                self.archive(index, path)?;
                self.root_update("clear_round_output", &format!("rm -f -- {}", q(path)), |s| s["files"][index]["archive"] = Value::Null)?;
                self.expect_presence(path, false)?;
            }
        }
        Ok(())
    }

    pub fn narrow(&self) -> Result<(), String> { self.set_property("debug.vulkan.layers", "") }
    pub fn set_storeop(&self, on: bool) -> Result<(), String> { self.set_property(graylayer::STOREOP_ON_PROP, if on { "1" } else { "0" }) }
    pub fn trigger(&self, go: u32) -> Result<(), String> { self.set_property(graylayer::FA_GO_PROP, &go.to_string()) }
    pub fn tap(&self, x: u32, y: u32) -> Result<(), String> { self.write("tap", &format!("input tap {x} {y}")) }
    pub fn swipe(&self, x1: u32, y1: u32, x2: u32, y2: u32, duration_ms: u32) -> Result<(), String> {
        if !(1..=10_000).contains(&duration_ms) { return Err("swipe duration must be 1..10000 ms".into()); }
        self.mutate("swipe", &["shell".into(), format!("input swipe {x1} {y1} {x2} {y2} {duration_ms}")],
            u64::from(duration_ms) + 30_000, |_| {}).map(|_| ())
    }

    pub fn display_rotation(&self) -> Result<u32, String> {
        rotation_from_input(&self.read("display_rotation", "dumpsys input")?)
    }


    fn json_output(&self, audit: bool, go: Option<u32>) -> Result<Option<Value>, String> {
        for path in output_paths(&self.package()).into_iter().filter(|p| p.contains("knobs_frameaudit") == audit) {
            if !self.present(&path)? { continue; }
            let text = self.root_read("capture_json", &format!("cat {}", q(&path)))?;
            let value: Value = serde_json::from_str(&text).map_err(|e| format!("invalid capture JSON at {path}: {e}"))?;
            if go.is_none_or(|expected| value["go"].as_u64() == Some(expected.into())) { return Ok(Some(value)); }
        }
        Ok(None)
    }
    pub fn marker(&self) -> Result<Option<Value>, String> { self.json_output(false, None) }
    pub fn audit(&self, go: u32) -> Result<Option<Value>, String> { self.json_output(true, Some(go)) }
    pub fn screenshot(&self, dst: &Path) -> Result<(), String> {
        new_file(dst, &self.screenshot_bytes()?)
    }
    pub fn screenshot_bytes(&self) -> Result<Vec<u8>, String> {
        self.ready()?;
        let output = self.command("screenshot", &["exec-out".into(), "screencap".into(), "-p".into()], 30_000)?;
        let bytes = bounded(&output.stdout_path, CAP)?;
        if !bytes.starts_with(b"\x89PNG\r\n\x1a\n") { return Err("screenshot is not a PNG".into()); }
        Ok(bytes)
    }

    fn archive(&self, index: usize, path: &str) -> Result<(), String> {
        let prior = self.state.borrow()["files"][index]["archive"].as_str().map(str::to_owned);
        if let Some(prior) = prior {
            let meta = fs::symlink_metadata(&prior).map_err(|e| format!("recorded archive unavailable: {e}"))?;
            if !meta.is_file() || meta.len() > CAP { return Err("recorded archive is not a bounded regular file".into()); }
            bounded(Path::new(&prior), CAP).map_err(|e| format!("recorded archive unreadable: {e}"))?;
            return Ok(());
        }
        let output = self.command("archive_output", &["shell".into(), format!("su -c {}", q(&format!("cat {}", q(path))))], 30_000)?;
        let generation = self.state.borrow()["generation"].as_u64().unwrap_or(0);
        let name = format!("output-g{generation}-{index}-{}-{}.raw", std::process::id(), stamp()?);
        let destination = self.dir.join(name);
        new_file(&destination, &bounded(&output.stdout_path, CAP)?)?;
        journal(&self.dir, json!({"event":"output_archived","generation":generation,"index":index,"remote":path,"archive":destination}))?;
        self.state.borrow_mut()["files"][index]["archive"] = json!(destination);
        self.save()
    }
    fn restore_file(&self, index: usize) -> Result<(), String> {
        let file = self.state.borrow()["files"][index].clone();
        if file["acquired"] != true { return Ok(()); }
        let path = file["path"].as_str().unwrap(); let backup = file["backup"].as_str().unwrap();
        let original = file["original_present"] == true;
        if file["restored"] == true {
            self.expect_presence(path, original)?; self.expect_presence(backup, false)?;
            return Ok(());
        }
        self.expect_presence(backup, original)?;
        if self.present(path)? {
            self.archive(index, path)?;
            self.root_write("remove_capture_output", &format!("rm -f -- {}", q(path)))?;
            self.expect_presence(path, false)?;
        }
        if original {
            self.root_update("restore_history", &format!("mv -- {} {}", q(backup), q(path)), |s| s["files"][index]["restored"] = json!(true))?;
        } else {
            self.state.borrow_mut()["files"][index]["restored"] = json!(true); self.save()?;
        }
        self.expect_presence(path, original)?; self.expect_presence(backup, false)
    }
    fn restore(&self) -> Result<Value, String> {
        validate(&self.state.borrow(), &self.dir, &self.lease.borrow().serial)?;
        pending_clear(&self.state.borrow())?;
        if self.state.borrow()["stage"] != "active" {
            return Ok(json!({"scope":"no_device_mutations","no_device_mutations":true,"target_stopped":null,"property_values_restored":null}));
        }
        self.ready()?;
        let boot = self.read("recovery_boot", "cat /proc/sys/kernel/random/boot_id")?;
        if self.state.borrow()["boot_id"].as_str() != Some(boot.trim()) { return self.stop("device boot changed; original values cannot be applied automatically".into()); }
        let expected_native = self.state.borrow()["native_dir"].as_str().ok_or("missing native directory")?.to_owned();
        if expected_native != self.native_dir()? { return self.stop("target installation directory changed; recovery refused".into()); }
        let uninstalled_refbench = matches!(completion_owner(&self.state.borrow())?,REFBENCH_OWNER|WHITEBOX_OWNER) && self.state.borrow()["installation_owned"] == false;
        if uninstalled_refbench {
            self.expect_presence(&format!("{expected_native}/{SO}"), false)?;
        }
        let state_owned = self.state.borrow()["state_owned"] == true;
        if self.present(STATE)? {
            if !state_owned { return self.stop("unowned layer registration appeared; recovery refused".into()); }
            self.check_registration()?;
        }
        self.stop_target()?;
        self.narrow()?;
        let mut local_errors = Vec::new();
        let count = self.state.borrow()["files"].as_array().unwrap().len();
        for index in 0..count {
            if let Err(error) = self.restore_file(index) {
                // Artifact storage errors need not prevent other known-safe
                // cleanup. Device/identity uncertainty always stops immediately.
                self.ready()?;
                local_errors.push(error);
            }
        }
        let installed = format!("{}/{SO}", self.state.borrow()["native_dir"].as_str().unwrap());
        if self.state.borrow()["installation_owned"] == true {
            self.root_write("remove_layer", &format!("rm -f -- {}", q(&installed)))?;
            self.expect_presence(&installed, false)?;
        }
        if state_owned && self.present(STATE)? {
            self.check_registration()?;
            self.root_write("remove_registration", &format!("rm -f -- {}", q(STATE)))?;
        }
        self.expect_presence(STATE, false)?;
        let staging_created = self.state.borrow()["staging_created"] == true;
        if staging_created && self.present(&self.remote())? {
            self.root_write("remove_staging_layer", &format!("rm -f -- {}", q(&format!("{}/{SO}", self.remote()))))?;
            let entries = self.entries(&self.remote())?.ok_or("staging directory disappeared during recovery")?;
            if !entries.is_empty() { return self.stop("unexpected staging files; directory preserved".into()); }
            self.root_write("remove_staging_directory", &format!("rmdir -- {}", q(&self.remote())))?;
        }
        self.expect_presence(&self.remote(), false)?;
        let originals = self.state.borrow()["properties"].clone();
        for property in LAYER_PROPERTIES { self.set_property(property, originals[property].as_str().ok_or("missing original property")?)?; }
        if !local_errors.is_empty() { return Err(local_errors.join("; ")); }
        let (_, scope) = local_recovery_contract(completion_owner(&self.state.borrow())?);
        Ok(json!({"scope":scope,"target_stopped":true,
            "app_check":"force-stop succeeded; exact package/package:child names absent from ps NAME",
            "property_values_restored":true,"owned_installation_absent":true,
            "acquired_historical_files_restored":true,"owned_staging_absent":true,"game_state_restored":false}))
    }

    fn check_registration(&self) -> Result<(), String> {
        let registration = self.root_read("recovery_registration", &format!("cat {}", q(STATE)))?;
        let expected = format!("{}|{}", self.package(), self.state.borrow()["native_dir"].as_str().unwrap());
        if registration.trim_end_matches('\n') != expected {
            return self.stop("layer registration no longer matches this session".into());
        }
        Ok(())
    }

    pub fn finish(&self, error: Option<&str>) -> Result<Value, String> {
        self.finish_owned(error, "standalone")
    }

    pub fn finish_refbench_loadop(&self, error: Option<&str>) -> Result<Value, String> {
        self.finish_owned(error, REFBENCH_OWNER)
    }

    pub fn finish_control(&self, error: Option<&str>) -> Result<Value, String> {
        self.finish_owned(error, CONTROL_OWNER)
    }

    fn finish_owned(&self, error: Option<&str>, owner: &str) -> Result<Value, String> {
        require_local_completion(&self.state.borrow(), owner)?;
        let mut value = self.restore_report(error)?;
        if value["status"] == "verified" {
            let report_path = PathBuf::from(value["report_path"].as_str().ok_or("missing recovery report path")?);
            self.lease.borrow_mut().complete_recovery(value["scope"].as_str().unwrap(), self.state.borrow()["evidence_origin"].as_str().unwrap(), &[&report_path])?;
            value["status"] = json!("reported_complete");
        }
        Ok(value)
    }

    pub fn restore_for_eval(&self, error: Option<&str>) -> Result<Value, String> {
        self.require_eval()?;
        self.state.borrow_mut()["base_recovery"] = Value::Null;
        self.save()?;
        let value = self.restore_report(error)?;
        if value["status"] == "verified" {
            self.state.borrow_mut()["base_recovery"] = value["report_path"].clone();
            if let Err(error) = self.save() {
                self.state.borrow_mut()["base_recovery"] = Value::Null;
                self.hold()?;
                return Err(format!("base recovery evidence persistence failed: {error}"));
            }
        }
        Ok(value)
    }

    pub fn complete_eval(&self, parent_report: &Path) -> Result<(), String> {
        self.require_eval()?;
        pending_clear(&self.state.borrow())?;
        let state = self.state.borrow().clone();
        let base_path = PathBuf::from(state["base_recovery"].as_str().ok_or("gray base has not been verified since the last mutation")?);
        let base = read_local_report(&base_path, &self.dir)?;
        let control = completion_owner(&state)? == CONTROL_OWNER;
        let base_schema = if control { "phonefarm/gray-control-recovery@1" } else { "phonefarm/gray-capture-recovery@1" };
        let base_scope = if control { CONTROL_SCOPE } else { SCOPE };
        if base["schema"] != base_schema || base["status"] != "verified"
            || (base["scope"] != base_scope && base["scope"] != "no_device_mutations")
            || !report_matches(&base, &state, &self.dir) {
            return Err("current base recovery report is not verified for this session".into());
        }
        if base["scope"] != "no_device_mutations" { self.ready()?; }
        let parent = read_local_report(parent_report, &self.dir)?;
        let (schema, scope) = parent_recovery_contract(completion_owner(&state)?)?;
        if parent["schema"] != schema || parent["status"] != "verified"
            || parent["scope"] != scope || parent["base_recovery"] != state["base_recovery"]
            || !report_matches(&parent, &state, &self.dir) {
            return Err("parent recovery report is not verified for this gray eval session".into());
        }
        self.lease.borrow_mut().complete_recovery(scope, state["evidence_origin"].as_str().unwrap(), &[parent_report, &base_path])
    }

    fn restore_report(&self, error: Option<&str>) -> Result<Value, String> {
        let (schema, default_scope) = local_recovery_contract(completion_owner(&self.state.borrow())?);
        let checks = self.restore();
        let scope = checks.as_ref().ok().and_then(|v| v["scope"].as_str()).unwrap_or(default_scope).to_owned();
        let report_path = self.dir.join(format!("recovery-{}-{}.json", std::process::id(), stamp()?));
        let value = json!({"schema":schema,"status":if checks.is_ok(){"verified"}else{"required"},
            "scope":scope,"serial":self.lease.borrow().serial,"token":self.state.borrow()["token"],
            "completion_owner":completion_owner(&self.state.borrow())?,"package":self.package(),"session":self.dir,
            "checks":checks.as_ref().ok(),"error":checks.as_ref().err(),"experiment_error":error,
            "evidence_origin":self.state.borrow()["evidence_origin"],"game_state_restored":false,
            "report_path":report_path,"at":chrono::Utc::now().to_rfc3339()});
        if checks.is_err() { self.hold()?; }
        new_file(&report_path, &serde_json::to_vec_pretty(&value).map_err(|e| e.to_string())?)?;
        Ok(value)
    }
}

fn adb_path() -> Result<PathBuf, String> { crate::device::locate_adb().map(PathBuf::from).ok_or("ADB not available".into()) }
fn stamp() -> Result<i64, String> { chrono::Utc::now().timestamp_nanos_opt().ok_or("timestamp unavailable".into()) }
fn split_path(path: &str) -> Result<(String, String), String> {
    if !graylayer::valid_install_dir(path) { return Err("invalid absolute remote path".into()); }
    let (parent, name) = path.rsplit_once('/').ok_or("remote path has no parent")?;
    Ok((if parent.is_empty() { "/" } else { parent }.into(), name.into()))
}
fn output_paths(package: &str) -> Vec<String> {
    ["knobs_layer_out", "knobs_frameaudit"].into_iter().flat_map(|name| [
        format!("/storage/emulated/0/Android/data/{package}/files/{name}.json"),
        format!("/data/data/{package}/{name}.json"),
        format!("/data/local/tmp/{name}.{package}.json"),
    ]).collect()
}
fn valid_label(value: &str) -> bool {
    !value.is_empty() && value.len() <= 128 && value.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-' | b'.'))
}
fn valid_marker(name: &str) -> bool {
    valid_label(name) && name.as_bytes().first().is_some_and(|b| b.is_ascii_alphanumeric() || *b == b'_')
        && !matches!(name, "knobs_layer_out.json" | "knobs_frameaudit.json")
}
fn launch_marker_path(package: &str, marker: &str) -> String {
    format!("/storage/emulated/0/Android/data/{package}/files/{marker}")
}
fn session_output_paths(state: &Value) -> Result<Vec<String>, String> {
    let package = state["package"].as_str().ok_or("missing gray package")?;
    let mut paths = output_paths(package);
    if let Some(marker) = state["launch_marker"].as_str() {
        if !valid_marker(marker) { return Err("invalid gray launch marker filename".into()); }
        paths.push(launch_marker_path(package, marker));
    }
    if completion_owner(state)? == REFBENCH_OWNER {
        paths.extend(["refbench_out.json", "refbench_started"].map(|name| launch_marker_path(package, name)));
    }
    if completion_owner(state)? == WHITEBOX_OWNER {
        paths.extend(["refbench_out.json","refbench_started","refbench_gpu_timestamps.json","refbench_bw_hash.json"].map(|name|launch_marker_path(package,name)));
    }
    Ok(paths)
}
fn completion_owner(state: &Value) -> Result<&str, String> {
    match state["schema"].as_str() {
        Some(LEGACY_SCHEMA) if state.get("completion_owner").is_none() || state["completion_owner"] == "standalone" => Ok("standalone"),
        Some(SCHEMA) => match state["completion_owner"].as_str() {
            Some(owner @ ("standalone" | EVAL_OWNER | SCRIPT_OWNER | LOADOP_OWNER | CONTROL_OWNER | REFBENCH_OWNER | WHITEBOX_OWNER)) => Ok(owner),
            _ => Err("gray session has no recognized completion owner".into()),
        },
        Some(CONTROL_RESOURCE_SCHEMA) if state["completion_owner"] == CONTROL_OWNER
            && control_parent_schema(&state["control_resources"]).is_ok() => Ok(CONTROL_OWNER),
        _ => Err("unsupported gray session schema or completion owner".into()),
    }
}
/// Validates the base's durable binding, rather than inferring capabilities from
/// the currently installed binary. C2A parents remain idle-only on recovery.
pub(crate) fn control_parent_schema(binding:&Value)->Result<&'static str,String> {
    for schema in [LEGACY_CONTROL_PARENT_SCHEMA,INPUT_CONTROL_PARENT_SCHEMA,CONTROL_PARENT_SCHEMA] {
        if *binding==json!({"path":"eval-session.json","schema":schema}) {return Ok(schema);}
    }
    Err("unrecognized control resource parent binding".into())
}
pub(crate) fn control_parent_supports_input(schema:&str)->bool {
    matches!(schema,INPUT_CONTROL_PARENT_SCHEMA|CONTROL_PARENT_SCHEMA)
}
fn require_owner(state: &Value, expected: &str) -> Result<(), String> {
    let owner = completion_owner(state)?;
    if owner != expected { return Err(format!("gray session belongs to {owner}; use its dedicated recovery entry point")); }
    Ok(())
}
fn require_parent(state: &Value) -> Result<(), String> {
    if completion_owner(state)? == CONTROL_OWNER && state["schema"] != CONTROL_RESOURCE_SCHEMA {
        return Err("legacy control session has no resource parent".into());
    }
    parent_recovery_contract(completion_owner(state)?).map(|_| ())
}
fn require_local_completion(state:&Value,owner:&str)->Result<(),String> {
    require_owner(state,owner)?;
    if state["schema"]==CONTROL_RESOURCE_SCHEMA {return Err("control session requires its bound resource parent before completion".into());}
    Ok(())
}
pub(crate) fn parent_recovery_contract(owner: &str) -> Result<(&'static str, &'static str), String> {
    match owner {
        EVAL_OWNER => Ok(("phonefarm/gray-eval-launch-recovery@1", EVAL_SCOPE)),
        SCRIPT_OWNER => Ok(("phonefarm/gray-eval-script-recovery@1", "gray_eval_script_owned_resources")),
        LOADOP_OWNER => Ok(("phonefarm/gray-ab-loadop-recovery@1", "gray_ab_loadop_owned_resources")),
        CONTROL_OWNER => Ok(("phonefarm/gray-control-resources-recovery@1", "gray_control_owned_resources_installation_properties_target_stopped")),
        _ => Err("gray session is not owned by an eval parent".into()),
    }
}
fn read_local_report(path: &Path, dir: &Path) -> Result<Value, String> {
    if path.parent() != Some(dir) { return Err("recovery report must be directly within the session directory".into()); }
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !metadata.is_file() { return Err("recovery report is not a regular file".into()); }
    serde_json::from_slice(&bounded(path, 1024 * 1024)?).map_err(|e| format!("invalid recovery report: {e}"))
}
fn local_recovery_contract(owner: &str) -> (&'static str, &'static str) {
    match owner {
        CONTROL_OWNER => ("phonefarm/gray-control-recovery@1", CONTROL_SCOPE),
        REFBENCH_OWNER => ("phonefarm/gray-refbench-loadop-recovery@1", REFBENCH_SCOPE),
        WHITEBOX_OWNER => ("phonefarm/whitebox-refbench-recovery@1", "whitebox_refbench_owned_outputs_target_stopped"),
        _ => ("phonefarm/gray-capture-recovery@1", SCOPE),
    }
}
fn refbench_next_arm(state: &Value, arm: RefbenchArm) -> Result<(), String> {
    let index = arm.index();
    let ordered = if index == 0 {
        state["stage"] == "prepared" && state["round_label"].is_null() && state["generation"] == 0
    } else {
        state["stage"] == "active" && state["generation"] == index - 1
            && state["round_label"] == RefbenchArm::ALL[index as usize - 1].label()
            && state["files"].as_array().is_some_and(|files| !files.is_empty()
                && files.iter().all(|file| file["acquired"] == true && file["restored"] == false))
    };
    if ordered { Ok(()) } else { Err("refbench arms must run once in A/B/C/D order; partial attempts only support recovery".into()) }
}
fn report_matches(report: &Value, state: &Value, dir: &Path) -> bool {
    ["serial", "token", "evidence_origin"].iter().all(|key| report[*key] == state[*key])
        && report["session"].as_str().is_some_and(|path| Path::new(path) == dir)
        && report["game_state_restored"] == false
}
fn pending_clear(state: &Value) -> Result<(), String> {
    if state["transport_uncertain"] != false || state.get("write_pending") != Some(&Value::Null) {
        Err("gray capture has an unresolved command/write; independent reconciliation is required before further device operations".into())
    } else { Ok(()) }
}

fn valid_session_directory(dir: &Path, owner: &str, token: &str) -> bool {
    let Some(name) = dir.file_name().and_then(|s| s.to_str()) else { return false; };
    if owner != CONTROL_OWNER { return name == format!("gray-{token}"); }
    name.strip_prefix("gray-session-").and_then(|s| s.split_once('-')).is_some_and(|(pid, nanos)| {
        !pid.is_empty() && !nanos.is_empty() && pid.bytes().all(|b| b.is_ascii_digit())
            && nanos.bytes().all(|b| b.is_ascii_digit())
    })
}

fn control_properties(mode: &str) -> Result<[&'static str; 12], String> {
    if !matches!(mode, "probe" | "target" | "passdump" | "loadop" | "frameaudit" | "frameaudit-ts") {
        return Err(format!("unsupported gray control mode: {mode}"));
    }
    Ok(LAYER_PROPERTIES.map(|property| match property {
        "debug.vulkan.layers" => LAYER_NAME,
        "debug.knobs.loadop" if mode == "loadop" => "1",
        "debug.knobs.passdump" if matches!(mode, "passdump" | "frameaudit" | "frameaudit-ts") => "1",
        "debug.knobs.frameaudit" if matches!(mode, "frameaudit" | "frameaudit-ts") => "1",
        "debug.knobs.frameaudit_ts" if mode == "frameaudit-ts" => "1",
        _ => "0",
    }))
}

pub(crate) fn rotation_from_input(text: &str) -> Result<u32, String> {
    let mut orientation = None;
    for line in text.lines().map(str::trim) {
        let value = if let Some(value) = line.strip_prefix("SurfaceOrientation:") {
            value
        } else if let Some(viewport) = line.strip_prefix("Viewport INTERNAL:") {
            let field = |name: &str| viewport.split(',').find_map(|part| {
                let (key, value) = part.trim().split_once('=')?;
                (key == name).then_some(value.trim())
            });
            if field("displayId") != Some("0") { continue; }
            match field("isActive") {
                Some("[0]" | "0" | "false") => continue,
                None | Some("[1]" | "1" | "true") => {},
                _ => return Err("invalid viewport activity from dumpsys input".into()),
            }
            field("orientation").ok_or("default display viewport has no orientation")?
        } else {
            continue;
        };
        let rotation = value.trim().parse::<u32>().ok().filter(|n| *n <= 3)
            .ok_or("invalid display orientation from dumpsys input")?;
        if orientation.is_some_and(|prior| prior != rotation) {
            return Err("conflicting display orientations from dumpsys input".into());
        }
        orientation = Some(rotation);
    }
    orientation.ok_or("display rotation unavailable in dumpsys input".into())
}

fn target_in_process_list(text: &str, package: &str) -> Result<bool, String> {
    let mut names = text.lines().map(str::trim).filter(|s| !s.is_empty());
    if names.next() != Some("NAME") { return Err("unrecognized ps NAME output; target liveness unknown".into()); }
    Ok(names.any(|name| name == package || name.starts_with(&format!("{package}:"))))
}

fn storeop_rule(rule: &str) -> Result<String, String> {
    let rule = rule.trim();
    if matches!(rule, "0" | "ds") { return Ok(rule.into()); }
    let items = rule.split(',').map(|item| {
        let (signature, attachment) = item.split_once(':').ok_or("storeop rule requires sig:attachment items, 0 or ds")?;
        let attachment = attachment.trim().parse::<u32>().map_err(|_| "invalid storeop attachment index")?;
        Ok((signature.to_owned(), attachment))
    }).collect::<Result<Vec<_>, &str>>()?;
    graylayer::storeop_spec(&items)
}

fn validate(state: &Value, dir: &Path, serial: &str) -> Result<(), String> {
    let owner = completion_owner(state)?;
    if let Some(value) = state.get("device_binding") {
        let binding = crate::device_registry::parse_binding(&value.to_string())?;
        if owner != CONTROL_OWNER || binding.serial != serial || state["lease_scope"] != "device" {
            return Err("invalid scoped control session identity".into());
        }
    } else if state.get("lease_scope").is_some() {
        return Err("lease scope requires its original device binding".into());
    }
    let token = state["token"].as_str().ok_or("missing gray session token")?;
    let package = state["package"].as_str().ok_or("missing gray session package")?;
    if state["serial"] != serial || !graylayer::valid_package(package)
        || token.is_empty() || !token.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
        || !valid_session_directory(dir, owner, token)
        || (owner == CONTROL_OWNER && state["control_session_id"].as_str() != dir.file_name().and_then(|s| s.to_str()))
        || state["remote"] != format!("/data/local/tmp/phonefarm-gray-{token}")
        || !matches!(state["stage"].as_str(), Some("new" | "prepared" | "active"))
        || !matches!(state["evidence_origin"].as_str(), Some("device" | "synthetic"))
        || state.get("write_pending").is_none()
        || !state["transport_uncertain"].is_boolean()
        || ["staging_created", "installation_owned", "state_owned"].iter().any(|key| !state[*key].is_boolean()) {
        return Err("invalid or mismatched gray session identity".into());
    }
    if owner == REFBENCH_OWNER {
        if package != REFBENCH_PACKAGE || !state["base_recovery"].is_null()
            || (state["round_label"].is_null() && state["generation"] != 0)
            || (!state["round_label"].is_null() && !RefbenchArm::ALL.iter().any(|arm|
                state["round_label"] == arm.label() && state["generation"] == arm.index())) {
            return Err("invalid refbench package or arm sequence".into());
        }
    }
    if owner == WHITEBOX_OWNER {
        let index=state["generation"].as_u64().ok_or("missing whitebox index")?;
        if package!=REFBENCH_PACKAGE || index>=130 || !state["base_recovery"].is_null()
            || state["installation_owned"]!=false || state["state_owned"]!=false || state["staging_created"]!=false
            || (!state["round_label"].is_null() && state["round_label"]!=format!("wb_{index}_{}",if index%2==1{"on"}else{"off"})) {
            return Err("invalid native whitebox ownership/sequence".into());
        }
    }
    if state["schema"] == SCHEMA || state["schema"] == CONTROL_RESOURCE_SCHEMA {
        if state.get("launch_marker").is_none_or(|v| !v.is_null() && v.as_str().is_none_or(|s| !valid_marker(s)))
            || state["generation"].as_u64().is_none()
            || state.get("round_label").is_none_or(|v| !v.is_null() && v.as_str().is_none_or(|s| !valid_label(s)))
            || state.get("base_recovery").is_none_or(|v| !v.is_null() && v.as_str().is_none_or(|p| Path::new(p).parent() != Some(dir)))
            || (matches!(owner, "standalone" | CONTROL_OWNER) && (!state["launch_marker"].is_null() || state["generation"] != 0 || !state["round_label"].is_null() || (state["schema"] != CONTROL_RESOURCE_SCHEMA && !state["base_recovery"].is_null())))
            || (matches!(owner,SCRIPT_OWNER|LOADOP_OWNER|REFBENCH_OWNER|WHITEBOX_OWNER) && !state["launch_marker"].is_null())
            || (state["stage"] != "active" && (state["generation"] != 0 || !state["round_label"].is_null())) {
            return Err("invalid gray session generation, marker or recovery evidence".into());
        }
    } else if !state["launch_marker"].is_null() {
        return Err("legacy gray sessions cannot own a launch marker".into());
    }
    if state["stage"] != "active" && ["staging_created", "installation_owned", "state_owned"].iter().any(|key| state[*key] == true) {
        return Err("nonactive gray session contains device mutations".into());
    }
    if state["stage"] == "new" && (!state["boot_id"].is_null() || !state["native_dir"].is_null()
        || state["files"].as_array().is_none_or(|files| !files.is_empty())
        || state["properties"].as_object().is_none_or(|properties| !properties.is_empty())) {
        return Err("unprepared gray session contains partial baseline state".into());
    }
    if state["stage"] != "new" {
        if state["boot_id"].as_str().is_none_or(|s| s.is_empty() || !s.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-'))
            || state["native_dir"].as_str().is_none_or(|s| !graylayer::valid_install_dir(s))
            || state["properties"].as_object().is_none_or(|m| m.len() != LAYER_PROPERTIES.len())
            || LAYER_PROPERTIES.iter().any(|p| state["properties"][*p].as_str().is_none_or(|s| s.len() > graylayer::PROP_VALUE_MAX || s.contains(['\n', '\r', '\0'])))
            || state["properties"]["debug.vulkan.layers"] != "" {
            return Err("invalid gray session original values".into());
        }
        let files = state["files"].as_array().ok_or("missing gray session files")?;
        let paths = session_output_paths(state)?;
        if files.len() != paths.len() { return Err("invalid gray session file inventory".into()); }
        for (file, path) in files.iter().zip(paths) {
            let (parent, name) = split_path(&path)?;
            if file["path"] != path || file["backup"] != format!("{parent}/.phonefarm-gray-{token}-{name}.bak")
                || ["original_present", "acquired", "backup_done", "restored"].iter().any(|key| !file[*key].is_boolean())
                || (file["backup_done"] == true && (file["acquired"] != true || file["original_present"] != true))
                || (file["restored"] == true && file["acquired"] != true)
                || (file["acquired"] == true && file["original_present"] == true && file["backup_done"] != true)
                || (state["stage"] == "prepared" && (file["acquired"] == true || file["backup_done"] == true || file["restored"] == true)) {
                return Err("invalid gray session file ownership".into());
            }
            if !file["archive"].is_null() && file["archive"].as_str().is_none_or(|p| Path::new(p).parent() != Some(dir)) {
                return Err("invalid gray session output archive".into());
            }
        }
    }
    Ok(())
}

// Intent extras are argv, never a shell fragment. Quotes/backslashes only group
// literal values; substitutions and shell operators have no execution meaning.
fn intent_extras(text: &str) -> Result<Vec<String>, String> {
    let mut words = Vec::new(); let mut word = String::new();
    let mut quote = None; let mut escape = false; let mut started = false;
    for ch in text.chars() {
        if ch == '\0' || ch == '\n' || ch == '\r' { return Err("control character in intent extras".into()); }
        if escape { word.push(ch); escape = false; started = true; continue; }
        if ch == '\\' && quote != Some('\'') { escape = true; started = true; continue; }
        if quote == Some(ch) { quote = None; continue; }
        if quote.is_none() && matches!(ch, '\'' | '"') { quote = Some(ch); started = true; continue; }
        if quote.is_none() && ch.is_whitespace() {
            if started { words.push(std::mem::take(&mut word)); started = false; }
        } else { word.push(ch); started = true; }
    }
    if quote.is_some() || escape { return Err("unfinished quoting in intent extras".into()); }
    if started { words.push(word); }
    let mut cursor = 0;
    while cursor < words.len() {
        let flag = words[cursor].as_str();
        let count = match flag { "--esn" => 2, "--es" | "--ez" | "--ei" | "--el" | "--ef" | "--eu" | "--ecn" => 3,
            _ => return Err(format!("unsupported intent extra {flag}; use typed scalar --es/--ez/--ei/--el/--ef/--eu/--ecn/--esn")) };
        if cursor + count > words.len() || words[cursor + 1].is_empty() { return Err("incomplete intent extra".into()); }
        if count == 3 {
            let value = &words[cursor + 2];
            let valid = match flag { "--ez" => matches!(value.as_str(), "true" | "false"), "--ei" => value.parse::<i32>().is_ok(),
                "--el" => value.parse::<i64>().is_ok(), "--ef" => value.parse::<f32>().is_ok_and(f32::is_finite), _ => true };
            if !valid { return Err(format!("invalid value for intent extra {flag}")); }
        }
        cursor += count;
    }
    Ok(words)
}

#[cfg(test)]
mod tests {
    #[test]
    fn native_whitebox_has_its_own_completion_and_timestamp_hash_outputs() {
        let state=serde_json::json!({"schema":super::SCHEMA,"completion_owner":super::WHITEBOX_OWNER,
            "package":super::REFBENCH_PACKAGE,"launch_marker":null});
        assert!(super::require_local_completion(&state,super::WHITEBOX_OWNER).is_ok());
        assert!(super::require_local_completion(&state,super::REFBENCH_OWNER).is_err());
        let paths=super::session_output_paths(&state).unwrap();
        for name in ["refbench_out.json","refbench_started","refbench_gpu_timestamps.json","refbench_bw_hash.json"] {
            assert!(paths.iter().any(|p|p.ends_with(name)));
        }
        assert_eq!(super::local_recovery_contract(super::WHITEBOX_OWNER).1,"whitebox_refbench_owned_outputs_target_stopped");
    }
    use super::*;
    #[test]
    fn intent_extras_are_literal_typed_arguments() {
        let words = intent_extras("--es scene 'city static' --es literal '$(id);x' --ei count 3 --es empty \"\"").unwrap();
        assert_eq!(words, ["--es", "scene", "city static", "--es", "literal", "$(id);x", "--ei", "count", "3", "--es", "empty", ""]);
        for bad in ["--es x 'unterminated", "--ei n nope", "--ez flag yes", "-n other/Activity", "--es only-key"] {
            assert!(intent_extras(bad).is_err());
        }
    }
    #[test]
    fn pending_write_or_uncertain_transport_is_not_a_recoverable_readback_failure() {
        assert!(pending_clear(&json!({"write_pending":null,"transport_uncertain":false})).is_ok());
        assert!(pending_clear(&json!({"write_pending":{"operation":"setprop"},"transport_uncertain":false})).is_err());
        assert!(pending_clear(&json!({"write_pending":null,"transport_uncertain":true})).is_err());
        assert!(pending_clear(&json!({})).is_err());
    }
    #[test]
    fn storeop_rule_validates_before_install_and_normalizes_with_shared_spec() {
        for rule in ["0", "ds"] { assert_eq!(storeop_rule(rule).unwrap(), rule); }
        assert_eq!(storeop_rule("DEADBEEF:1,12345678:0,deadbeef:1").unwrap(), "12345678:0,deadbeef:1");
        for rule in ["", "1234567:0", "deadbeef:32", "deadbeef:0;id", "deadbeef:0,", "ds:0"] {
            assert!(storeop_rule(rule).is_err(), "accepted {rule:?}");
        }
    }
    #[test]
    fn target_liveness_is_exact_and_malformed_output_is_unknown() {
        assert!(target_in_process_list("NAME\ncom.example.fixture:worker\n", "com.example.fixture").unwrap());
        assert!(!target_in_process_list("NAME\ncom.example.fixture2\n", "com.example.fixture").unwrap());
        assert!(target_in_process_list("", "com.example.fixture").is_err());
    }
    #[test]
    fn refbench_arm_order_refuses_replay_and_skips() {
        let mut state = json!({"stage":"prepared","generation":0,"round_label":null,
            "files":[{"acquired":true,"restored":false}]});
        assert!(refbench_next_arm(&state, RefbenchArm::BaselineOff).is_ok());
        state["stage"] = json!("active");
        // Activation without a completed launch-intent save cannot be replayed.
        assert!(refbench_next_arm(&state, RefbenchArm::BaselineOff).is_err());
        state["round_label"] = json!(RefbenchArm::BaselineOff.label());
        assert!(refbench_next_arm(&state, RefbenchArm::BaselineOn).is_ok());
        assert!(refbench_next_arm(&state, RefbenchArm::BaselineOff).is_err());
        assert!(refbench_next_arm(&state, RefbenchArm::RewriteOff).is_err());
        state["files"][0]["restored"] = json!(true);
        assert!(refbench_next_arm(&state, RefbenchArm::BaselineOn).is_err());
    }
    #[test]
    fn control_modes_do_not_inherit_capture_flags() {
        for (mode, enabled) in [
            ("probe", vec![]), ("target", vec![]),
            ("loadop", vec!["debug.knobs.loadop"]),
            ("passdump", vec!["debug.knobs.passdump"]),
            ("frameaudit", vec!["debug.knobs.passdump", "debug.knobs.frameaudit"]),
            ("frameaudit-ts", vec!["debug.knobs.passdump", "debug.knobs.frameaudit", "debug.knobs.frameaudit_ts"]),
        ] {
            let properties = control_properties(mode).unwrap();
            let actual: Vec<_> = LAYER_PROPERTIES.into_iter().zip(properties)
                .filter_map(|(key, value)| if key == "debug.vulkan.layers" {
                    assert_eq!(value, LAYER_NAME); None
                } else if value == "1" { Some(key) } else { assert_eq!(value, "0"); None }).collect();
            assert_eq!(actual, enabled, "{mode}");
        }
        for mode in ["copyprobe", "upop", "off", "unknown"] { assert!(control_properties(mode).is_err()); }
    }

    #[test]
    fn display_rotation_requires_unambiguous_actual_readback() {
        assert_eq!(rotation_from_input("Touch Input Mapper:\n  SurfaceOrientation: 3\n").unwrap(), 3);
        assert_eq!(rotation_from_input("SurfaceOrientation: 0\nSurfaceOrientation: 0\n").unwrap(), 0);
        for text in ["", "rotation=1", "SurfaceOrientation: 4", "SurfaceOrientation: unknown",
            "SurfaceOrientation: 0\nSurfaceOrientation: 1\n"] {
            assert!(rotation_from_input(text).is_err(), "accepted {text:?}");
        }
    }

    #[test]
    fn display_rotation_reads_android_15_default_viewport() {
        let observed = "Viewport INTERNAL: displayId=0, uniqueId=local:4627039422300187648, port=0, orientation=1, logicalFrame=[0, 0, 2800, 1272], physicalFrame=[0, 0, 2800, 1272], deviceSize=[2800, 1272], isActive=[1]";
        assert_eq!(rotation_from_input(observed).unwrap(), 1);
        assert_eq!(rotation_from_input(&format!("{observed}\n  {observed}\n")).unwrap(), 1);
        assert_eq!(rotation_from_input(&format!("SurfaceOrientation: 1\n{observed}")).unwrap(), 1);
    }

    #[test]
    fn display_rotation_ignores_other_and_inactive_viewports() {
        let other = "Viewport EXTERNAL: displayId=1, orientation=3, isActive=[1]\nViewport INTERNAL: displayId=2, orientation=2, isActive=[1]\nViewport INTERNAL: displayId=0, orientation=3, isActive=[0]";
        assert!(rotation_from_input(other).is_err());
        assert_eq!(rotation_from_input(&format!("{other}\nViewport INTERNAL: displayId=0, orientation=1, isActive=[1]")).unwrap(), 1);
    }

    #[test]
    fn display_rotation_rejects_invalid_or_conflicting_viewports() {
        for text in [
            "Viewport INTERNAL: displayId=0, isActive=[1]",
            "Viewport INTERNAL: displayId=0, orientation=4, isActive=[1]",
            "Viewport INTERNAL: displayId=0, orientation=unknown, isActive=[1]",
            "Viewport INTERNAL: displayId=0, orientation=1, isActive=unknown",
            "SurfaceOrientation: 0\nViewport INTERNAL: displayId=0, orientation=1, isActive=[1]",
            "Viewport INTERNAL: displayId=0, orientation=0\nViewport INTERNAL: displayId=0, orientation=1",
        ] {
            assert!(rotation_from_input(text).is_err(), "accepted {text:?}");
        }
    }

    #[test]
    fn parent_completion_and_marker_ownership_are_explicit() {
        assert!(require_owner(&json!({"schema":LEGACY_SCHEMA}), "standalone").is_ok());
        assert!(completion_owner(&json!({"schema":LEGACY_SCHEMA,"completion_owner":EVAL_OWNER})).is_err());
        assert!(completion_owner(&json!({"schema":SCHEMA})).is_err());
        let parent = json!({"schema":SCHEMA,"completion_owner":EVAL_OWNER,"package":"com.example.fixture","launch_marker":"megacity_started"});
        assert!(require_owner(&parent, "standalone").is_err());
        assert!(require_owner(&parent, EVAL_OWNER).is_ok());
        let script=json!({"schema":SCHEMA,"completion_owner":SCRIPT_OWNER,"package":"com.example.fixture","launch_marker":null});
        assert!(require_parent(&script).is_ok());
        assert!(require_owner(&script,"standalone").is_err());
        assert!(require_owner(&script,EVAL_OWNER).is_err());
        assert_eq!(parent_recovery_contract(SCRIPT_OWNER).unwrap(),("phonefarm/gray-eval-script-recovery@1","gray_eval_script_owned_resources"));
        assert_ne!(parent_recovery_contract(SCRIPT_OWNER).unwrap(),parent_recovery_contract(EVAL_OWNER).unwrap());
        let loadop=json!({"schema":SCHEMA,"completion_owner":LOADOP_OWNER});
        assert!(require_parent(&loadop).is_ok());assert!(require_owner(&loadop,SCRIPT_OWNER).is_err());
        assert_eq!(parent_recovery_contract(LOADOP_OWNER).unwrap(),("phonefarm/gray-ab-loadop-recovery@1","gray_ab_loadop_owned_resources"));
        let refbench=json!({"schema":SCHEMA,"completion_owner":REFBENCH_OWNER,"package":REFBENCH_PACKAGE,"launch_marker":null});
        assert!(require_parent(&refbench).is_err());
        assert!(require_local_completion(&refbench, REFBENCH_OWNER).is_ok());
        assert!(require_owner(&refbench, LOADOP_OWNER).is_err());
        let refbench_paths=session_output_paths(&refbench).unwrap();
        assert_eq!(refbench_paths.len(),8);
        assert_eq!(&refbench_paths[6..], &[
            launch_marker_path(REFBENCH_PACKAGE,"refbench_out.json"),
            launch_marker_path(REFBENCH_PACKAGE,"refbench_started")]);
        assert_eq!(local_recovery_contract(REFBENCH_OWNER),("phonefarm/gray-refbench-loadop-recovery@1",REFBENCH_SCOPE));
        let control = json!({"schema":SCHEMA,"completion_owner":CONTROL_OWNER});
        assert!(require_owner(&control, CONTROL_OWNER).is_ok());
        assert!(require_parent(&control).is_err());
        assert!(require_local_completion(&control, CONTROL_OWNER).is_ok());
        let mut resources=json!({"schema":CONTROL_RESOURCE_SCHEMA,"completion_owner":CONTROL_OWNER,"control_resources":{"path":"eval-session.json","schema":CONTROL_PARENT_SCHEMA}});
        assert!(require_parent(&resources).is_ok());
        assert!(require_local_completion(&resources, CONTROL_OWNER).is_err());
        assert_eq!(control_parent_schema(&resources["control_resources"]).unwrap(),CONTROL_PARENT_SCHEMA);
        assert!(control_parent_supports_input(CONTROL_PARENT_SCHEMA));
        resources["control_resources"]["schema"]=json!(INPUT_CONTROL_PARENT_SCHEMA);
        assert!(require_parent(&resources).is_ok());
        assert!(require_local_completion(&resources, CONTROL_OWNER).is_err());
        assert_eq!(control_parent_schema(&resources["control_resources"]).unwrap(),INPUT_CONTROL_PARENT_SCHEMA);
        assert!(control_parent_supports_input(INPUT_CONTROL_PARENT_SCHEMA));
        resources["control_resources"]["schema"]=json!(LEGACY_CONTROL_PARENT_SCHEMA);
        assert!(require_parent(&resources).is_ok());
        assert!(require_local_completion(&resources, CONTROL_OWNER).is_err());
        assert_eq!(control_parent_schema(&resources["control_resources"]).unwrap(),LEGACY_CONTROL_PARENT_SCHEMA);
        assert!(!control_parent_supports_input(LEGACY_CONTROL_PARENT_SCHEMA));
        assert!(!control_parent_supports_input("phonefarm/gray-control-resources@4"));
        resources["control_resources"]=Value::Null;
        assert!(require_owner(&resources,CONTROL_OWNER).is_err());
        for owner in ["standalone", EVAL_OWNER, SCRIPT_OWNER, LOADOP_OWNER] {
            assert!(require_owner(&control, owner).is_err());
            assert!(!valid_session_directory(Path::new("gray-session-42-123456"), owner, "secret-token"));
        }
        assert!(valid_session_directory(Path::new("gray-session-42-123456"), CONTROL_OWNER, "secret-token"));
        assert!(!valid_session_directory(Path::new("gray-secret-token"), CONTROL_OWNER, "secret-token"));
        let paths = session_output_paths(&parent).unwrap();
        assert_eq!(paths.len(), 7);
        assert_eq!(paths[6], "/storage/emulated/0/Android/data/com.example.fixture/files/megacity_started");
        for name in ["../started", "/tmp/started", "knobs_layer_out.json", "", ".", "started;id"] { assert!(!valid_marker(name)); }
    }
}
