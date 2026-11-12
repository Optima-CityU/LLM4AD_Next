//! Typed scene inputs and evidence. The initialize Session owns all device I/O.
use crate::{
    game_deploy::{self, Asset, InitializeRecipe, InitializeRequest},
    game_deploy_session::Committed,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    ffi::CString,
    fs::File,
    io::Read,
    os::fd::{AsRawFd, FromRawFd},
    path::{Component, Path, PathBuf},
};

pub(crate) const REQUEST: &str = "phonefarm/game-scene-request@1";
pub(crate) const RECIPE: &str = "phonefarm/game-scene-recipe@1";
pub(crate) const REPORT: &str = "phonefarm/game-scene@1";
pub(crate) const SESSION: &str = "phonefarm/game-scene-session@1";
pub(crate) const SCOPE: &str = "owned_scene_attempt_force_stopped_runtime_retained";
pub(crate) const COMPONENT: &str = "com.karin.idTech4Amm/com.n0n3m4.q3e.Q3EMain";
pub(crate) const SEED_LIMIT: u64 = 1024 * 1024;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Renderer {
    pub(crate) opengl: String,
    pub(crate) multithread: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Recipe {
    pub(crate) schema: String,
    pub(crate) adapter: String,
    pub(crate) package: String,
    pub(crate) version_code: u64,
    pub(crate) apk_sha256: String,
    pub(crate) resource_prefix: String,
    pub(crate) demo_name: String,
    pub(crate) demo_asset_name: String,
    pub(crate) compat_asset_name: String,
    pub(crate) renderer: Renderer,
    pub(crate) wait_seconds: u64,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Deployment {
    pub(crate) report_path: PathBuf,
    pub(crate) state_path: PathBuf,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Request {
    pub(crate) schema: String,
    pub(crate) node_id: String,
    pub(crate) game_id: String,
    pub(crate) version: String,
    pub(crate) serial: String,
    pub(crate) mode: String,
    pub(crate) recipe: Recipe,
    pub(crate) assets: Vec<Asset>,
    pub(crate) deployment: Deployment,
}
impl Request {
    pub(crate) fn header(&self) -> InitializeRequest {
        InitializeRequest {
            schema: game_deploy::INITIALIZE_REQUEST_SCHEMA.into(),
            node_id: self.node_id.clone(),
            game_id: self.game_id.clone(),
            version: self.version.clone(),
            serial: self.serial.clone(),
            mode: self.mode.clone(),
            recipe: InitializeRecipe {
                schema: game_deploy::INITIALIZE_RECIPE_SCHEMA.into(),
                package: self.recipe.package.clone(),
                version_code: self.recipe.version_code,
                apk_sha256: self.recipe.apk_sha256.clone(),
                wait_seconds: self.recipe.wait_seconds,
            },
            assets: vec![],
        }
    }
    pub(crate) fn validate(&self) -> Result<(), String> {
        self.header().validate()?;
        let r = &self.recipe;
        if self.schema != REQUEST
            || r.schema != RECIPE
            || r.adapter != "doom3-v73-render-demo-v1"
            || r.package != "com.karin.idTech4Amm"
            || r.version_code != 11073
            || (!r.resource_prefix.is_empty()
                && !game_deploy::valid_resource_path(&r.resource_prefix))
            || r.demo_name.is_empty()
            || r.demo_name.len() > 64
            || !r
                .demo_name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
            || !matches!(r.renderer.opengl.as_str(), "GLES2" | "GLES3.0")
            || r.demo_asset_name == r.compat_asset_name
            || self.assets.len() != 2
        {
            return Err("invalid Doom v73 render scene recipe".into());
        }
        let mut sum = 0u64;
        for name in [&r.demo_asset_name, &r.compat_asset_name] {
            let found: Vec<_> = self.assets.iter().filter(|a| &a.name == name).collect();
            if name.is_empty() || name.chars().any(char::is_control) || found.len() != 1 {
                return Err("scene requires exactly two distinct selected seed assets".into());
            }
            let a = found[0];
            if a.size == 0
                || !game_deploy::valid_hash(&a.sha256)
                || !a.path.is_absolute()
                || a.path
                    .components()
                    .any(|p| matches!(p, Component::ParentDir))
            {
                return Err("invalid scene seed identity/path".into());
            }
            sum = sum.checked_add(a.size).ok_or("seed size overflow")?;
        }
        if sum > SEED_LIMIT {
            return Err("scene seeds exceed 1 MiB total".into());
        }
        Ok(())
    }
    pub(crate) fn prepare_inputs(&mut self, request_path: &Path) -> Result<Committed, String> {
        self.validate()?;
        let parent = request_path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."))
            .canonicalize()
            .map_err(|e| e.to_string())?;
        let (report_path, report) = evidence(&parent, &self.deployment.report_path)?;
        let (state_path, state) = evidence(&parent, &self.deployment.state_path)?;
        if report_path == state_path {
            return Err("deployment evidence files must be distinct".into());
        }
        let report: Value = serde_json::from_slice(&report).map_err(|e| e.to_string())?;
        let source = Committed::from_evidence(&report, &state)?;
        self.bind(&source)?;
        // Only the small selected seeds are read here. APK/PK4 remain in place.
        for asset in &self.assets {
            let file = game_deploy::source_file(asset)?;
            let mut bytes = Vec::new();
            file.take(SEED_LIMIT + 1)
                .read_to_end(&mut bytes)
                .map_err(|e| e.to_string())?;
            if bytes.len() as u64 != asset.size
                || crate::confirmation_transport::sha(&bytes) != asset.sha256
            {
                return Err(format!("scene seed {} size/SHA-256 differs", asset.name));
            }
        }
        self.deployment = Deployment {
            report_path,
            state_path,
        };
        Ok(source)
    }
    pub(crate) fn bind(&self, source: &Committed) -> Result<(), String> {
        source.validate()?;
        let prior = &source.request;
        if prior.node_id != self.node_id
            || prior.game_id != self.game_id
            || prior.serial != self.serial
            || prior.recipe.package != self.recipe.package
            || prior.recipe.version_code != self.recipe.version_code
            || prior.recipe.apk_sha256 != self.recipe.apk_sha256
        {
            return Err("scene source deployment target/engine identity differs".into());
        }
        let base = if self.recipe.resource_prefix.is_empty() {
            "base/".into()
        } else {
            format!("{}/base/", self.recipe.resource_prefix)
        };
        if !prior.recipe.files.iter().any(|f| {
            f.path
                .strip_prefix(&base)
                .is_some_and(|p| !p.contains('/') && p.ends_with(".pk4"))
        }) {
            return Err("scene resource prefix has no declared base/*.pk4".into());
        }
        Ok(())
    }
    pub(crate) fn seed(&self, index: usize) -> &Asset {
        let name = if index == 0 {
            &self.recipe.demo_asset_name
        } else {
            &self.recipe.compat_asset_name
        };
        self.assets
            .iter()
            .find(|a| &a.name == name)
            .expect("validated scene seed")
    }
    pub(crate) fn seed_path(&self, index: usize) -> String {
        if index == 0 {
            format!("base/demos/{}.demo", self.recipe.demo_name)
        } else {
            "base/script/weapon_base.script".into()
        }
    }
}

// Walk relative evidence using directory FDs, so no intermediate symlink can
// escape the request's parent. Parsed bytes are frozen in state before leasing.
fn evidence(parent: &Path, relative: &Path) -> Result<(PathBuf, Vec<u8>), String> {
    let name = relative.to_str().ok_or("non-UTF8 evidence path")?;
    if relative.is_absolute() || !game_deploy::valid_resource_path(name) {
        return Err(
            "deployment evidence must be a restricted path relative to the request file".into(),
        );
    }
    let parts: Vec<_> = name.split('/').collect();
    let mut directory = File::open(parent).map_err(|e| e.to_string())?;
    for (i, part) in parts.iter().enumerate() {
        let name = CString::new(*part).map_err(|e| e.to_string())?;
        let flags = libc::O_RDONLY
            | libc::O_CLOEXEC
            | libc::O_NOFOLLOW
            | libc::O_NONBLOCK
            | if i + 1 < parts.len() {
                libc::O_DIRECTORY
            } else {
                0
            };
        let fd = unsafe { libc::openat(directory.as_raw_fd(), name.as_ptr(), flags) };
        if fd < 0 {
            return Err(format!(
                "deployment evidence unavailable: {}",
                std::io::Error::last_os_error()
            ));
        }
        directory = unsafe { File::from_raw_fd(fd) };
    }
    let meta = directory.metadata().map_err(|e| e.to_string())?;
    if !meta.is_file() || meta.len() > 131072 {
        return Err("deployment evidence must be a regular file <=128 KiB".into());
    }
    let mut bytes = Vec::new();
    directory
        .take(131073)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 131072 {
        return Err("deployment evidence grew beyond 128 KiB".into());
    }
    Ok((parent.join(relative), bytes))
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct State {
    pub(crate) request: Request,
    pub(crate) deployment: Committed,
    pub(crate) runtime_root: String,
    pub(crate) prepare_attempted: bool,
    pub(crate) prepared: bool,
    pub(crate) resources: Value,
    pub(crate) observation: Value,
}
impl State {
    pub(crate) fn new(request: Request, deployment: Committed, token: &str) -> Self {
        let runtime_root = format!(
            "/sdcard/Android/data/{}/files/pf-scene-{token}",
            request.recipe.package
        );
        Self {
            request,
            deployment,
            runtime_root,
            prepare_attempted: false,
            prepared: false,
            resources: json!({"status":"unknown"}),
            observation: json!({"status":"not_attempted"}),
        }
    }
    pub(crate) fn validate(&self, header: &InitializeRequest, token: &str) -> Result<(), String> {
        self.request.validate()?;
        self.request.bind(&self.deployment)?;
        if serde_json::to_value(self.request.header()).map_err(|e| e.to_string())?
            != serde_json::to_value(header).map_err(|e| e.to_string())?
            || self.runtime_root
                != format!(
                    "/sdcard/Android/data/{}/files/pf-scene-{token}",
                    header.recipe.package
                )
            || !self.request.deployment.report_path.is_absolute()
            || !self.request.deployment.state_path.is_absolute()
            || (self.prepared && !self.prepare_attempted)
            || !self.resources.is_object()
            || !self.observation.is_object()
        {
            return Err("invalid scene context/header/runtime identity".into());
        }
        Ok(())
    }
    pub(crate) fn basepath(&self) -> String {
        if self.request.recipe.resource_prefix.is_empty() {
            self.deployment.remote_root.clone()
        } else {
            format!(
                "{}/{}",
                self.deployment.remote_root, self.request.recipe.resource_prefix
            )
        }
    }
    pub(crate) fn command(&self) -> String {
        format!("game.arm +set fs_basepath {} +set fs_savepath {} +set logfile 2 +set developer 1 +set harm_r_openglVersion {} +set r_multithread {} +playDemo {}",
            self.basepath(),self.runtime_root,self.request.recipe.renderer.opengl,if self.request.recipe.renderer.multithread{1}else{0},self.request.recipe.demo_name)
    }
    pub(crate) fn observe_log(&mut self, log: &str) -> bool {
        // These are bounded log observations, never engine or scene identity proof.
        let demo = log.contains("reading a v2 render demo");
        let named = log.contains(&format!("{}.demo", self.request.recipe.demo_name));
        let paths = log.contains(&self.basepath()) && log.contains(&self.runtime_root);
        let fatal = log.lines().any(|line| {
            line.contains("ERROR:") || line.contains("FATAL") || line.contains("Couldn't open")
        });
        let associated = demo && named && paths && !fatal;
        self.observation = json!({"status":if associated{"observed"}else{"needs_assistance"},
            "log_reports_render_demo":demo,"log_names_requested_demo":named,"log_mentions_requested_paths":paths,"log_reports_error":fatal,
            "render_demo_loaded":{"status":"unknown","reason":"log statements do not prove engine consumption and effective configuration"},
            "same_displayed_frame":"unknown","same_game_simulation_state":"unknown","performance_eligible":"not_claimed"});
        associated
    }
}
pub(crate) fn report(request: &Request) -> Value {
    let mut v = game_deploy::initialize_report(&request.header());
    v["schema"] = json!(REPORT);
    v.as_object_mut().unwrap().remove("storage_initialization");
    v["admission"] = json!("bounded_diagnostic_attempt");
    v["input"] = json!({"status":"unknown","seed_bytes":request.assets.iter().fold(0u64,|n,a|n.saturating_add(a.size))});
    v["launcher_configuration"] = json!({"status":"unknown","expected_renderer":request.recipe.renderer,"private_preferences_read":false});
    v["engine_identity"] = json!({"status":"unknown","reason":"installed APK identity does not prove the loaded native library"});
    v["application_write_confinement"] = json!("not_claimed");
    v["old_application_data_restored"] = json!(false);
    v["runtime_seed"] = json!({"status":"not_prepared","retention":"intentional"});
    v["scene_observation"] = json!({"status":"not_attempted","same_displayed_frame":"unknown","same_game_simulation_state":"unknown","performance_eligible":"not_claimed"});
    v["recovery"]["scope"] = json!(SCOPE);
    v
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    pub(crate) fn fixture() -> (Request, Committed, Value, Value) {
        let package = "com.karin.idTech4Amm";
        let request:Request=serde_json::from_value(json!({"schema":REQUEST,"node_id":"node","game_id":"doom","version":"v2","serial":"S","mode":"apply",
            "recipe":{"schema":RECIPE,"adapter":"doom3-v73-render-demo-v1","package":package,"version_code":11073,"apk_sha256":"a".repeat(64),"resource_prefix":"doom3",
                "demo_name":"shot_fixture","demo_asset_name":"demo","compat_asset_name":"compat","renderer":{"opengl":"GLES3.0","multithread":true},"wait_seconds":1},
            "assets":[{"name":"demo","path":"/fixture/demo","size":4,"sha256":crate::confirmation_transport::sha(b"demo")},{"name":"compat","path":"/fixture/compat","size":6,"sha256":crate::confirmation_transport::sha(b"compat")}],
            "deployment":{"report_path":"source-deployment.json","state_path":"source-session.json"}})).unwrap();
        let original = json!({"schema":game_deploy::REQUEST_SCHEMA,"node_id":"node","game_id":"doom","version":"v1","serial":"S","mode":"apply",
            "recipe":{"schema":game_deploy::RECIPE_SCHEMA,"package":package,"version_code":11073,"apk_sha256":"a".repeat(64),"files":[{"asset_name":"pk4","path":"doom3/base/demo00.pk4"}]},
            "assets":[{"name":"pk4","path":"/never-read/pk4","size":3,"sha256":crate::confirmation_transport::sha(b"pk4")}]});
        let root = format!("/sdcard/Android/data/{package}/files/pf-game-deployed");
        let report = json!({"schema":game_deploy::REPORT_SCHEMA,"node_id":"node","game_id":"doom","version":"v1","serial":"S","phase":"committed",
            "installed":{"status":"verified","package":package,"version_code":11073,"apk_sha256":"a".repeat(64)},
            "resources_deployed":{"status":"verified","root":root,"files":original["recipe"]["files"]},
            "recovery":{"status":"verified","scope":game_deploy::SCOPE,"transaction":"committed"}});
        let state = json!({"schema":"phonefarm/game-deploy-session@1","request":original,"token":"deployed","boot_id":"00000000-0000-4000-8000-000000000123",
            "remote_root":root,"phase":"committed","write_pending":null,"transport_uncertain":false,"checks":{},"application_access":{},"source_session":null});
        let source =
            Committed::from_evidence(&report, &serde_json::to_vec(&state).unwrap()).unwrap();
        (request, source, report, state)
    }
    #[test]
    fn scene_inputs_freeze_relative_evidence_and_reject_mismatched_sources() {
        struct Scratch(PathBuf);
        impl Drop for Scratch {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let root = Scratch(std::env::temp_dir().join(format!(
            "pf-scene-inputs-{}-{}",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        )));
        std::fs::create_dir(&root.0).unwrap();
        let (mut request, _, report, state) = fixture();
        for (name, bytes) in [
            (
                "source-deployment.json",
                serde_json::to_vec(&report).unwrap(),
            ),
            ("source-session.json", serde_json::to_vec(&state).unwrap()),
            ("demo", b"demo".to_vec()),
            ("compat", b"compat".to_vec()),
        ] {
            std::fs::write(root.0.join(name), bytes).unwrap();
        }
        for asset in &mut request.assets {
            asset.path = root.0.join(&asset.name);
        }
        let original = request.clone();
        let source = request
            .prepare_inputs(&root.0.join("request.json"))
            .unwrap();
        assert_eq!(source.request.version, "v1");
        assert_eq!(request.version, "v2");
        assert_eq!(
            request.deployment.report_path,
            root.0
                .canonicalize()
                .unwrap()
                .join("source-deployment.json")
        );
        let mut wrong = original.clone();
        wrong.serial = "different".into();
        assert!(wrong
            .prepare_inputs(&root.0.join("request.json"))
            .unwrap_err()
            .contains("identity differs"));
        let mut wrong = original.clone();
        wrong.assets[0].sha256 = "b".repeat(64);
        assert!(wrong
            .prepare_inputs(&root.0.join("request.json"))
            .unwrap_err()
            .contains("SHA-256 differs"));
        let mut wrong = original.clone();
        wrong.deployment.report_path = "../source-deployment.json".into();
        assert!(wrong.prepare_inputs(&root.0.join("request.json")).is_err());
        std::os::unix::fs::symlink(
            root.0.join("source-deployment.json"),
            root.0.join("alias.json"),
        )
        .unwrap();
        let mut wrong = original.clone();
        wrong.deployment.report_path = "alias.json".into();
        assert!(wrong.prepare_inputs(&root.0.join("request.json")).is_err());
        let mut pending = state.clone();
        pending["write_pending"] = json!("push_resource");
        assert!(Committed::from_evidence(&report, &serde_json::to_vec(&pending).unwrap()).is_err());
        let mut foreign = report.clone();
        foreign["resources_deployed"]["root"] = json!("/old/unowned");
        assert!(Committed::from_evidence(&foreign, &serde_json::to_vec(&state).unwrap()).is_err());
        let mut oversized = original.clone();
        oversized.assets[0].size = SEED_LIMIT;
        assert!(oversized.validate().is_err());
        std::fs::remove_file(root.0.join("demo")).unwrap();
        let mut missing = original;
        assert!(missing
            .prepare_inputs(&root.0.join("request.json"))
            .is_err());
    }
    #[test]
    fn scene_logs_do_not_certify_engine_configuration_or_same_state() {
        let (mut request, source, _, _) = fixture();
        request.recipe.demo_name = "shot;exec autoexec".into();
        assert!(request.validate().is_err());
        request.recipe.demo_name = "shot_fixture".into();
        let mut scene = State::new(request, source, "attempt");
        assert!(!scene.observe_log("reading a v2 render demo"));
        let log = format!(
            "{}\n{}\nshot_fixture.demo\nreading a v2 render demo",
            scene.basepath(),
            scene.runtime_root
        );
        assert!(scene.observe_log(&log));
        assert_eq!(scene.observation["render_demo_loaded"]["status"], "unknown");
        assert_eq!(scene.observation["same_displayed_frame"], "unknown");
        let report = report(&scene.request);
        assert_eq!(report["scene_ready"]["status"], "unknown");
        assert_eq!(report["engine_identity"]["status"], "unknown");
        assert!(!scene.observe_log(&(log + "\nERROR: missing map")));
    }
}
