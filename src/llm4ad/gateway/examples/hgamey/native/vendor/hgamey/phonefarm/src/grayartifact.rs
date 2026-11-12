//! Offline, fail-closed candidate artifact builder. No device or model dependencies.
use crate::upopabi;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc, Arc,
};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const LAYER_NAME: &str = "libVkLayer_refknobs.so";
const SOURCE_LIMIT: u64 = 4 * 1024 * 1024;
const SPV_LIMIT: u64 = 16 * 1024 * 1024;
const FILE_LIMIT: u64 = 128 * 1024 * 1024;
const TOOL_LIMIT: u64 = 256 * 1024 * 1024;
const OUTPUT_LIMIT: usize = 1024 * 1024;
const EXPORTS: [&str; 5] = [
    "vkNegotiateLoaderLayerInterfaceVersion",
    "vkGetInstanceProcAddr",
    "vkGetDeviceProcAddr",
    "vkEnumerateInstanceLayerProperties",
    "vkEnumerateInstanceExtensionProperties",
];
#[derive(Deserialize)]
struct LayerBuild {
    sources: Vec<String>,
    inputs: Vec<String>,
    compile_flags: Vec<String>,
    link_flags: Vec<String>,
}
fn layer_build() -> LayerBuild {
    serde_json::from_str(include_str!("../knobs/gray/build/layer_build.json"))
        .expect("checked-in layer build configuration")
}
const ADAPTATION_INPUTS: [(&str, &str); 3] = [
    ("original_candidate.spv", "original.spv"),
    ("adaptation_descriptor.json", "descriptor.json"),
    ("adaptation_manifest.json", "manifest.json"),
];

fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn read_limit(p: &Path, limit: u64) -> Result<Vec<u8>, String> {
    let f = open_regular(p)?;
    if !f.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("input must be a regular file".into());
    }
    let mut bytes = Vec::new();
    f.take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > limit {
        return Err(format!("file exceeds {limit} byte limit: {}", p.display()));
    }
    Ok(bytes)
}
fn read(p: &Path) -> Result<Vec<u8>, String> {
    read_limit(p, FILE_LIMIT)
}
fn file_hash(p: &Path) -> Result<String, String> {
    let mut f = open_regular(p)?;
    if !f.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("input must be a regular file".into());
    }
    let mut hasher = Sha256::new();
    let mut total = 0u64;
    let mut buf = [0; 65536];
    loop {
        let n = f.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        total += n as u64;
        if total > TOOL_LIMIT {
            return Err(format!(
                "hash input exceeds {TOOL_LIMIT} bytes: {}",
                p.display()
            ));
        }
        hasher.update(&buf[..n]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}
fn open_regular(p: &Path) -> Result<fs::File, String> {
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW);
    }
    let f = options
        .open(p)
        .map_err(|e| format!("{}: {e}", p.display()))?;
    if !f.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("input must be a regular file".into());
    }
    Ok(f)
}
fn write(p: &Path, bytes: &[u8]) -> Result<(), String> {
    fs::write(p, bytes).map_err(|e| format!("{}: {e}", p.display()))
}
fn json_bytes(v: &impl Serialize) -> Result<Vec<u8>, String> {
    serde_json::to_vec_pretty(v).map_err(|e| e.to_string())
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum ProcessSupervision {
    #[default]
    OwnHelperGroups,
    InheritedGroup,
}
impl ProcessSupervision {
    fn is_standalone(&self) -> bool {
        *self == Self::OwnHelperGroups
    }
    fn validate(self) -> Result<(), String> {
        if self.is_standalone() {
            return Ok(());
        }
        #[cfg(unix)]
        {
            if unsafe { libc::getpgrp() } != std::process::id() as i32 {
                return Err("inherited-group requires phonefarm to lead a fresh isolated process group owned by an outer supervisor".into());
            }
            Ok(())
        }
        #[cfg(not(unix))]
        Err("inherited-group requires a Unix process supervisor".into())
    }
    fn artifact_state(self) -> &'static str {
        if self.is_standalone() {
            "built_offline"
        } else {
            "built_offline_outer_quiescence_required"
        }
    }
}

#[derive(Debug, Serialize, Deserialize)]
struct Tool {
    path: PathBuf,
    sha256: String,
    version: String,
    #[serde(skip)]
    supervision: ProcessSupervision,
}
impl Tool {
    fn load(
        p: PathBuf,
        timeout: Duration,
        supervision: ProcessSupervision,
    ) -> Result<Self, String> {
        let path = fs::canonicalize(&p).map_err(|e| {
            format!(
                "missing tool {}: {e}; install an Android NDK explicitly",
                p.display()
            )
        })?;
        let sha256 = file_hash(&path)?;
        let (status, stdout, _) = bounded_process(
            &path,
            &["--version"],
            timeout.min(Duration::from_secs(10)),
            supervision,
        )?;
        if !status.success() {
            return Err(format!("tool --version failed: {}", path.display()));
        }
        let version = String::from_utf8_lossy(&stdout).trim().to_string();
        let t = Self {
            path,
            sha256,
            version,
            supervision,
        };
        t.check()?;
        Ok(t)
    }
    fn check(&self) -> Result<(), String> {
        if file_hash(&self.path)? != self.sha256 {
            return Err(format!(
                "compiler/tool changed during build: {}",
                self.path.display()
            ));
        }
        Ok(())
    }
    fn exec(&self, args: &[&str], log: &Path, timeout: Duration) -> Result<String, String> {
        self.check()?;
        let (status, stdout, stderr) =
            match bounded_process(&self.path, args, timeout, self.supervision) {
                Ok(out) => out,
                Err(e) => {
                    write(log, e.as_bytes())?;
                    return Err(e);
                }
            };
        self.check()?;
        let text = format!(
            "{}\n{}",
            String::from_utf8_lossy(&stdout),
            String::from_utf8_lossy(&stderr)
        );
        write(log, text.as_bytes())?;
        if !status.success() {
            return Err(format!(
                "tool failed ({}): {}; output retained in {}",
                status,
                self.path.display(),
                log.display()
            ));
        }
        Ok(String::from_utf8_lossy(&stdout).into_owned())
    }
}

#[cfg(unix)]
fn bounded_process(
    path: &Path,
    args: &[&str],
    timeout: Duration,
    supervision: ProcessSupervision,
) -> Result<(std::process::ExitStatus, Vec<u8>, Vec<u8>), String> {
    use std::os::unix::process::CommandExt;
    supervision.validate()?;
    let mut command = Command::new(path);
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if supervision.is_standalone() {
        command.process_group(0);
    }
    let mut child = command.spawn().map_err(|e| e.to_string())?;
    let group = if supervision.is_standalone() {
        child.id() as i32
    } else {
        std::process::id() as i32
    };
    let overflow = Arc::new(AtomicBool::new(false));
    let (tx, rx) = mpsc::channel();
    let pipes: Vec<Box<dyn Read + Send>> = vec![
        Box::new(child.stdout.take().unwrap()),
        Box::new(child.stderr.take().unwrap()),
    ];
    for (index, mut pipe) in pipes.into_iter().enumerate() {
        let tx = tx.clone();
        let overflow = Arc::clone(&overflow);
        std::thread::spawn(move || {
            let mut bytes = Vec::new();
            let mut buf = [0; 8192];
            let result = loop {
                match pipe.read(&mut buf) {
                    Ok(0) => break Ok(bytes),
                    Ok(n) => {
                        if bytes.len() + n > OUTPUT_LIMIT {
                            overflow.store(true, Ordering::Relaxed);
                            break Err("tool output limit exceeded".to_string());
                        }
                        bytes.extend_from_slice(&buf[..n]);
                    }
                    Err(e) => break Err(e.to_string()),
                }
            };
            let _ = tx.send((index, result));
        });
    }
    drop(tx);
    let start = Instant::now();
    let mut status = None;
    let mut results = BTreeMap::new();
    loop {
        for (index, result) in rx.try_iter() {
            results.insert(index, result);
        }
        if status.is_none() {
            status = match child.try_wait() {
                Ok(s) => s,
                Err(e) => {
                    terminate_build_group(group);
                    return Err(e.to_string());
                }
            };
        }
        let reason = if overflow.load(Ordering::Relaxed) {
            Some("tool output limit exceeded")
        } else if start.elapsed() >= timeout {
            Some("tool timeout exceeded")
        } else if args.windows(2).any(|pair| {
            pair[0] == "-o"
                && fs::metadata(pair[1]).is_ok_and(|m| {
                    m.len()
                        > if pair[1].ends_with(".spv") {
                            SPV_LIMIT
                        } else {
                            FILE_LIMIT
                        }
                })
        }) {
            Some("tool output file size limit exceeded")
        } else {
            None
        };
        if let Some(reason) = reason {
            terminate_build_group(group);
            // Never introduce an unbounded wait after the timeout itself.
            let cleanup = Instant::now();
            while cleanup.elapsed() < Duration::from_millis(500) {
                match child.try_wait() {
                    Ok(Some(_)) | Err(_) => break,
                    Ok(None) => std::thread::sleep(Duration::from_millis(5)),
                }
            }
            return Err(format!(
                "{reason}: {} (limit {}s, {} bytes per stream)",
                path.display(),
                timeout.as_secs(),
                OUTPUT_LIMIT
            ));
        }
        if let Some(status) = status {
            if results.len() == 2 {
                // A compiler returning SUCCESS must have finished its whole process group.
                // Closed pipes alone do not prove that a background writer has stopped.
                let alive = unsafe { libc::kill(-group, 0) };
                let error = std::io::Error::last_os_error();
                if supervision.is_standalone()
                    && (alive == 0 || error.raw_os_error() != Some(libc::ESRCH))
                {
                    terminate_build_group(group);
                    return Err(format!(
                        "tool left background descendants or group status unknown: {}",
                        path.display()
                    ));
                }
                let stdout = results.remove(&0).unwrap()?;
                let stderr = results.remove(&1).unwrap()?;
                // Inherited mode cannot claim this group is absent: phonefarm is
                // still its leader. The artifact explicitly requires outer retirement.
                return Ok((status, stdout, stderr));
            }
        }
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[cfg(unix)]
fn terminate_build_group(group: i32) {
    // Never log before terminating: an unread full stdout/stderr pipe could
    // block the kill itself. In inherited mode the outer durable InFlight and
    // signal receipt provide the failure evidence; this kills phonefarm too.
    unsafe {
        libc::kill(-group, libc::SIGKILL);
    }
}
#[cfg(not(unix))]
fn bounded_process(
    _: &Path,
    _: &[&str],
    _: Duration,
    _: ProcessSupervision,
) -> Result<(std::process::ExitStatus, Vec<u8>, Vec<u8>), String> {
    Err("artifact builds currently require a Unix host for bounded process-group execution".into())
}

#[derive(Debug, Serialize, Deserialize)]
struct Descriptor {
    schema_version: u32,
    target: String,
    abi: upopabi::Abi,
    inputs: BTreeMap<String, String>,
    tools: BTreeMap<String, Tool>,
    shader_flags: Vec<String>,
    native_flags: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    adaptation: Option<Value>,
    #[serde(default, skip_serializing_if = "ProcessSupervision::is_standalone")]
    process_supervision: ProcessSupervision,
}
#[derive(Debug, Serialize, Deserialize)]
struct Manifest {
    schema_version: u32,
    artifact_id: String,
    state: String,
    device_validation: String,
    deployable: bool,
    descriptor: Descriptor,
    files: BTreeMap<String, String>,
    layer_sha256: String,
    checks: Value,
}

fn safe_name(name: &str) -> bool {
    let mut parts = Path::new(name).components();
    matches!(parts.next(), Some(std::path::Component::Normal(_))) && parts.next().is_none()
}
fn metadata(id: &str, inputs: &BTreeMap<String, String>) -> String {
    format!("#define RK_DYNAMIC_ARTIFACT_METADATA 1\nstatic const char kArtifactId[] = \"{id}\";\nstatic const char kCandidateSourceSha256[] = \"{}\";\nstatic const char kCandidateSpirvSha256[] = \"{}\";\n",inputs["candidate.comp"],inputs["candidate.spv"])
}
pub fn verify(dir: &Path) -> Result<Value, String> {
    let directory_metadata = fs::symlink_metadata(dir).map_err(|e| e.to_string())?;
    if !directory_metadata.is_dir() || directory_metadata.file_type().is_symlink() {
        return Err("artifact directory must be a real directory".into());
    }
    if fs::symlink_metadata(dir.join("manifest.json"))
        .map_err(|e| e.to_string())?
        .file_type()
        .is_symlink()
    {
        return Err("manifest symlink unsupported".into());
    }
    let manifest: Manifest =
        serde_json::from_slice(&read(&dir.join("manifest.json"))?).map_err(|e| e.to_string())?;
    if manifest.schema_version != 1
        || manifest.state != manifest.descriptor.process_supervision.artifact_state()
        || manifest.device_validation != "not_run"
        || manifest.deployable
    {
        return Err("unsupported manifest state/schema".into());
    }
    let expected_route = if manifest.descriptor.adaptation.is_some() {
        "adapted_spirv"
    } else {
        "compiled_glsl"
    };
    if (manifest.descriptor.adaptation.is_some()
        || manifest.checks.get("candidate_route").is_some())
        && manifest.checks["candidate_route"] != expected_route
    {
        return Err("candidate build route metadata mismatch".into());
    }
    let descriptor_bytes = json_bytes(&manifest.descriptor)?;
    if manifest.artifact_id != format!("upop-{}", hash(&descriptor_bytes)) {
        return Err("artifact ID does not match frozen descriptor".into());
    }
    let config = layer_build();
    let mut required_inputs: Vec<&str> = vec![
        "candidate.comp",
        "candidate.spv",
        "copyprobe_copy.spv",
        "copy_spv.h",
    ];
    required_inputs.extend(
        config
            .sources
            .iter()
            .chain(&config.inputs)
            .map(String::as_str),
    );
    // v1 artifacts built before the NPU / frame-interpolation integration did not
    // snapshot these later layer inputs. Keep reading them without upgrading their
    // device/deployment claims.
    for late in ["npuprobe.cpp", "npu_rt.h", "framedump.cpp", "framedump.h", "interp.cpp", "interp.h",
                 "selfsr.cpp", "selfsr.h", "selfsr_up.comp", "selfsr_rcas.comp", "selfsr_fused.comp", "interp_bm.cpp", "interp_sc.cpp", "interp_sc.h",
                 "interp_b_luma.comp", "interp_b_bm.comp", "interp_b_smooth.comp", "interp_b_compose.comp", "interp_common.glsl"] {
        if !manifest.descriptor.inputs.contains_key(late) {
            required_inputs.retain(|name| *name != late);
        }
    }
    if manifest.descriptor.adaptation.is_some() {
        required_inputs.extend(ADAPTATION_INPUTS.map(|(name, _)| name));
    }
    if manifest.descriptor.inputs.len() != required_inputs.len()
        || required_inputs
            .iter()
            .any(|n| !manifest.descriptor.inputs.contains_key(*n))
    {
        return Err("descriptor must freeze every native/shader input".into());
    }
    for name in
        required_inputs
            .into_iter()
            .chain(["artifact_metadata.h", "descriptor.json", LAYER_NAME])
    {
        if !manifest.files.contains_key(name) {
            return Err(format!("manifest missing {name}"));
        }
    }
    for (name, expected) in &manifest.files {
        if !safe_name(name)
            || fs::symlink_metadata(dir.join(name))
                .map_err(|e| e.to_string())?
                .file_type()
                .is_symlink()
        {
            return Err("unsafe artifact file path/symlink".into());
        }
        if &file_hash(&dir.join(name))? != expected {
            return Err(format!("artifact hash mismatch: {name}"));
        }
        let limit = if name == "candidate.comp" {
            SOURCE_LIMIT
        } else if name.ends_with(".spv") {
            SPV_LIMIT
        } else {
            FILE_LIMIT
        };
        if fs::metadata(dir.join(name))
            .map_err(|e| e.to_string())?
            .len()
            > limit
        {
            return Err(format!("artifact file exceeds size limit: {name}"));
        }
    }
    for entry in fs::read_dir(dir).map_err(|e| e.to_string())? {
        let name = entry
            .map_err(|e| e.to_string())?
            .file_name()
            .into_string()
            .map_err(|_| "non-UTF8 artifact filename")?;
        if name != "manifest.json" && !manifest.files.contains_key(&name) {
            return Err(format!("untracked artifact file: {name}"));
        }
    }
    if read(&dir.join("descriptor.json"))? != descriptor_bytes {
        return Err("descriptor snapshot mismatch".into());
    }
    for (name, expected) in &manifest.descriptor.inputs {
        if manifest.files.get(name) != Some(expected) {
            return Err(format!("descriptor input mismatch: {name}"));
        }
    }
    if read(&dir.join("artifact_metadata.h"))?
        != metadata(&manifest.artifact_id, &manifest.descriptor.inputs).as_bytes()
    {
        return Err("embedded artifact metadata does not match descriptor".into());
    }
    if manifest.files.get(LAYER_NAME) != Some(&manifest.layer_sha256) {
        return Err("layer hash mismatch".into());
    }
    if upopabi::reflect(&read(&dir.join("candidate.spv"))?)? != manifest.descriptor.abi {
        return Err("reflected ABI mismatch".into());
    }
    if let Some(origin) = &manifest.descriptor.adaptation {
        let summary = crate::storageadapter::verify_snapshots(&adaptation_snapshots(dir)?)?;
        if origin != &summary {
            return Err("adaptation origin mismatch".into());
        }
    }
    elf_arm64(&read(&dir.join(LAYER_NAME))?)?;
    Ok(
        json!({"artifact_id":manifest.artifact_id,"layer_sha256":manifest.layer_sha256,
        "state":manifest.state,"device_validation":manifest.device_validation,"deployable":false,"files_verified":manifest.files.len(),"process_supervision":manifest.descriptor.process_supervision,"requires_outer_quiescence":!manifest.descriptor.process_supervision.is_standalone(),"outer_quiescence_verified":false}),
    )
}

fn elf_arm64(bytes: &[u8]) -> Result<(), String> {
    if bytes.len() < 64
        || &bytes[..4] != b"\x7fELF"
        || bytes[4] != 2
        || bytes[5] != 1
        || u16::from_le_bytes([bytes[16], bytes[17]]) != 3
        || u16::from_le_bytes([bytes[18], bytes[19]]) != 183
    {
        return Err("layer must be an ELF64 little-endian AArch64 shared object".into());
    }
    Ok(())
}
fn pstr(p: &Path) -> Result<&str, String> {
    p.to_str()
        .ok_or_else(|| "non-UTF8 tool path unsupported".into())
}
fn header_array(out: &mut String, name: &str, bytes: &[u8]) {
    out.push_str(&format!(
        "alignas(4) static const unsigned char {name}[] = {{\n"
    ));
    for row in bytes.chunks(16) {
        for b in row {
            out.push_str(&format!("{b},"));
        }
        out.push('\n');
    }
    out.push_str(&format!(
        "}};\nstatic const unsigned int {name}Len = {};\n",
        bytes.len()
    ));
}

fn adaptation_snapshots(dir: &Path) -> Result<BTreeMap<String, Vec<u8>>, String> {
    let mut contents = BTreeMap::new();
    for (stored, original) in ADAPTATION_INPUTS.into_iter().chain([
        ("candidate.comp", "original.comp"),
        ("candidate.spv", "adapted.spv"),
    ]) {
        let limit = if original.ends_with(".comp") {
            SOURCE_LIMIT
        } else if original.ends_with(".spv") {
            SPV_LIMIT
        } else {
            64 * 1024
        };
        contents.insert(original.to_string(), read_limit(&dir.join(stored), limit)?);
    }
    Ok(contents)
}
fn build(
    source: Option<&Path>,
    adaptation: Option<&Path>,
    out: &Path,
    ndk: &Path,
    timeout: Duration,
    supervision: ProcessSupervision,
) -> Result<Value, String> {
    supervision.validate()?;
    // Read once; subsequent compilers only see this owned snapshot.
    let (bytes, imported, origin) = if let Some(dir) = adaptation {
        let (snapshots, summary) = crate::storageadapter::snapshots(dir)?;
        (
            snapshots["original.comp"].clone(),
            Some(snapshots),
            Some(summary),
        )
    } else {
        (
            read_limit(source.ok_or("source missing")?, SOURCE_LIMIT)?,
            None,
            None,
        )
    };
    fs::create_dir_all(out).map_err(|e| e.to_string())?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let stage = out.join(format!(".building-{}-{nonce}", std::process::id()));
    fs::create_dir(&stage).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&stage, fs::Permissions::from_mode(0o700))
            .map_err(|e| e.to_string())?;
    }
    write(&stage.join("candidate.comp"), &bytes)?;
    if let Some(snapshots) = imported {
        write(&stage.join("candidate.spv"), &snapshots["adapted.spv"])?;
        for (stored, original) in ADAPTATION_INPUTS {
            write(&stage.join(stored), &snapshots[original])?;
        }
    }
    let result = build_stage(&stage, ndk, timeout, origin, supervision);
    match result {
        Ok(id) => {
            verify(&stage)?;
            let dest = out.join(&id);
            if dest.exists() {
                let old = verify(&dest)?;
                if old["artifact_id"] != id {
                    return Err("existing artifact identity mismatch".into());
                }
                // Determinism is part of reuse: same descriptor but different native output is not reused.
                if file_hash(&dest.join(LAYER_NAME))? != file_hash(&stage.join(LAYER_NAME))? {
                    return Err(
                        "existing artifact has different layer bytes for the same descriptor"
                            .into(),
                    );
                }
                fs::remove_dir_all(&stage).map_err(|e| e.to_string())?;
                Ok(
                    json!({"artifact":dest,"artifact_id":id,"reused":true,"device_validation":"not_run","deployable":false,"process_supervision":supervision,"requires_outer_quiescence":!supervision.is_standalone(),"outer_quiescence_verified":false}),
                )
            } else {
                fs::rename(&stage, &dest).map_err(|e| e.to_string())?;
                Ok(
                    json!({"artifact":dest,"artifact_id":id,"reused":false,"device_validation":"not_run","deployable":false,"process_supervision":supervision,"requires_outer_quiescence":!supervision.is_standalone(),"outer_quiescence_verified":false}),
                )
            }
        }
        Err(error) => {
            let _ = write(
                &stage.join("failure.json"),
                &json_bytes(
                    &json!({"state":"failed","error":error,"device_validation":"not_run"}),
                )?,
            );
            Err(format!(
                "{error}; failed input/output snapshot retained at {}",
                stage.display()
            ))
        }
    }
}

fn build_stage(
    stage: &Path,
    ndk: &Path,
    timeout: Duration,
    adaptation: Option<Value>,
    supervision: ProcessSupervision,
) -> Result<String, String> {
    let tag = if ndk.join("toolchains/llvm/prebuilt/darwin-x86_64").is_dir() {
        "darwin-x86_64"
    } else {
        "linux-x86_64"
    };
    let bin = ndk.join("toolchains/llvm/prebuilt").join(tag).join("bin");
    let mut tools = BTreeMap::new();
    tools.insert(
        "glslc".into(),
        Tool::load(
            ndk.join("shader-tools").join(tag).join("glslc"),
            timeout,
            supervision,
        )?,
    );
    tools.insert(
        "clang++".into(),
        Tool::load(
            bin.join("aarch64-linux-android30-clang++"),
            timeout,
            supervision,
        )?,
    );
    tools.insert(
        "nm".into(),
        Tool::load(bin.join("llvm-nm"), timeout, supervision)?,
    );
    tools.insert(
        "readelf".into(),
        Tool::load(bin.join("llvm-readelf"), timeout, supervision)?,
    );
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../knobs/gray/layer");
    let mut inputs = BTreeMap::new();
    inputs.insert(
        "candidate.comp".into(),
        file_hash(&stage.join("candidate.comp"))?,
    );
    if let Some(origin) = &adaptation {
        if &crate::storageadapter::verify_snapshots(&adaptation_snapshots(stage)?)? != origin {
            return Err("adaptation snapshot changed before build".into());
        }
        for (stored, _) in ADAPTATION_INPUTS {
            inputs.insert(stored.into(), file_hash(&stage.join(stored))?);
        }
    }
    let config = layer_build();
    for name in config.sources.iter().chain(&config.inputs) {
        let data = read(&root.join(name))?;
        write(&stage.join(name), &data)?;
        inputs.insert(name.clone(), hash(&data));
    }
    let candidate = stage.join("candidate.comp");
    let spv = stage.join("candidate.spv");
    if adaptation.is_none() {
        tools["glslc"].exec(
            &[
                "--target-env=vulkan1.0",
                "-O",
                pstr(&candidate)?,
                "-o",
                pstr(&spv)?,
            ],
            &stage.join("candidate_compile.log"),
            timeout,
        )?;
    } else {
        write(&stage.join("candidate_compile.log"), b"adapted_spirv route: exact verified SPIR-V snapshot used; original storage source was NOT recompiled; source/SPIR-V association caller_supplied_unverified\n")?;
    }
    if file_hash(&candidate)? != inputs["candidate.comp"] {
        return Err("candidate snapshot changed during compile".into());
    }
    let spv_bytes = read_limit(&spv, SPV_LIMIT)?;
    let abi = upopabi::reflect(&spv_bytes)?;
    inputs.insert("candidate.spv".into(), hash(&spv_bytes));
    let copy = stage.join("copyprobe_copy.comp");
    let copyspv = stage.join("copyprobe_copy.spv");
    tools["glslc"].exec(
        &[
            "--target-env=vulkan1.0",
            "-O",
            pstr(&copy)?,
            "-o",
            pstr(&copyspv)?,
        ],
        &stage.join("copy_compile.log"),
        timeout,
    )?;
    let copy_bytes = read_limit(&copyspv, SPV_LIMIT)?;
    inputs.insert("copyprobe_copy.spv".into(), hash(&copy_bytes));
    let mut arrays = String::new();
    header_array(&mut arrays, "kCopySpv", &copy_bytes);
    header_array(&mut arrays, "kUpopSpv", &spv_bytes);
    // The NPU route (npuop) is not part of a dynamic upop candidate: its pack/unpack
    // shaders are built only by build_layer.sh. Empty arrays keep the shared layer
    // source compiling; the layer reports npuop unavailable when Len is 0.
    arrays.push_str("static const unsigned char kNpuPackSpv[] = {0};\nstatic const unsigned int kNpuPackSpvLen = 0;\n");
    arrays.push_str("static const unsigned char kNpuUnpackSpv[] = {0};\nstatic const unsigned int kNpuUnpackSpvLen = 0;\n");
    write(&stage.join("copy_spv.h"), arrays.as_bytes())?;
    inputs.insert("copy_spv.h".into(), hash(arrays.as_bytes()));
    let descriptor = Descriptor {
        schema_version: 1,
        target: "android-arm64-v8a-api30".into(),
        abi,
        inputs,
        tools,
        shader_flags: vec!["--target-env=vulkan1.0".into(), "-O".into()],
        native_flags: config
            .compile_flags
            .iter()
            .cloned()
            .chain(["-DRK_DYNAMIC_UPOP_BUILD=1".into(), "-I<snapshot>".into()])
            .chain(config.link_flags.iter().cloned())
            .collect(),
        adaptation,
        process_supervision: supervision,
    };
    let descriptor_bytes = json_bytes(&descriptor)?;
    let id = format!("upop-{}", hash(&descriptor_bytes));
    write(&stage.join("descriptor.json"), &descriptor_bytes)?;
    let metadata_bytes = metadata(&id, &descriptor.inputs).into_bytes();
    write(&stage.join("artifact_metadata.h"), &metadata_bytes)?;
    let layer = stage.join(LAYER_NAME);
    let sources: Vec<_> = config.sources.iter().map(|name| stage.join(name)).collect();
    let mut args: Vec<_> = config.compile_flags.iter().map(String::as_str).collect();
    args.extend([
        "-DRK_DYNAMIC_UPOP_BUILD=1",
        "-I",
        pstr(stage)?,
        "-o",
        pstr(&layer)?,
    ]);
    for source in &sources {
        args.push(pstr(source)?);
    }
    args.extend(config.link_flags.iter().map(String::as_str));
    descriptor.tools["clang++"].exec(&args, &stage.join("layer_compile.log"), timeout)?;
    let layer_bytes = read(&layer)?;
    elf_arm64(&layer_bytes)?;
    let layer_sha256 = hash(&layer_bytes);
    let symbols = descriptor.tools["nm"].exec(
        &["-D", "--defined-only", pstr(&layer)?],
        &stage.join("exports.log"),
        timeout,
    )?;
    for required in EXPORTS {
        if !symbols.lines().any(|l| {
            let p: Vec<_> = l.split_whitespace().collect();
            p.len() >= 2 && p[p.len() - 2] == "T" && p[p.len() - 1] == required
        }) {
            return Err(format!("layer missing required export {required}"));
        }
    }
    let dynamic = descriptor.tools["readelf"].exec(
        &["-d", pstr(&layer)?],
        &stage.join("dependencies.log"),
        timeout,
    )?;
    let mut needed = Vec::new();
    for line in dynamic.lines().filter(|l| l.contains("NEEDED")) {
        let name = line
            .split_once('[')
            .and_then(|(_, r)| r.split_once(']'))
            .map(|(n, _)| n)
            .ok_or("unparseable ELF dependency")?;
        if !["libc.so", "libm.so", "libdl.so", "liblog.so"].contains(&name) {
            return Err(format!("unexpected non-system ELF dependency: {name}"));
        }
        needed.push(name.to_string());
    }
    for (name, expected) in &descriptor.inputs {
        if file_hash(&stage.join(name))? != *expected {
            return Err(format!("frozen input changed during native build: {name}"));
        }
    }
    for t in descriptor.tools.values() {
        t.check()?;
    }
    if read(&stage.join("artifact_metadata.h"))? != metadata_bytes
        || read(&stage.join("descriptor.json"))? != descriptor_bytes
    {
        return Err("frozen metadata/descriptor changed during native build".into());
    }
    if file_hash(&layer)? != layer_sha256 {
        return Err("native output changed during validation".into());
    }
    let mut files = descriptor.inputs.clone();
    for name in [
        "artifact_metadata.h",
        "descriptor.json",
        LAYER_NAME,
        "candidate_compile.log",
        "copy_compile.log",
        "layer_compile.log",
        "exports.log",
        "dependencies.log",
    ] {
        files.insert(name.into(), file_hash(&stage.join(name))?);
    }
    let candidate_route = if descriptor.adaptation.is_some() {
        "adapted_spirv"
    } else {
        "compiled_glsl"
    };
    let manifest = Manifest {
        schema_version: 1,
        artifact_id: id.clone(),
        state: supervision.artifact_state().into(),
        device_validation: "not_run".into(),
        deployable: false,
        descriptor,
        files,
        layer_sha256,
        checks: json!({"abi_reflected":true,"elf_machine":"AArch64","required_exports":EXPORTS,"needed":needed,"candidate_route":candidate_route}),
    };
    write(&stage.join("manifest.json"), &json_bytes(&manifest)?)?;
    Ok(id)
}

pub fn run(args: &[String]) -> i32 {
    let result = cli(args);
    match result {
        Ok(v) => {
            println!("{}", serde_json::to_string_pretty(&v).unwrap());
            0
        }
        Err(e) => {
            eprintln!("{}", json!({"state":"failed","error":e}));
            2
        }
    }
}
fn cli(args: &[String]) -> Result<Value, String> {
    if matches!(
        args.first().map(String::as_str),
        Some("adapt-storage" | "verify-adaptation")
    ) {
        return crate::storageadapter::cli(args);
    }
    let command = args
        .first()
        .map(String::as_str)
        .ok_or("expected build, inspect, verify, adapt-storage or verify-adaptation")?;
    let mut opts = BTreeMap::new();
    let mut i = 1;
    while i < args.len() {
        if args[i] == "--json" {
            i += 1;
            continue;
        }
        let key = &args[i];
        if ![
            "--source",
            "--out",
            "--ndk",
            "--spirv",
            "--artifact",
            "--timeout-seconds",
            "--adapted-artifact",
            "--process-supervision",
        ]
        .contains(&key.as_str())
        {
            return Err(format!("unknown argument {key}"));
        }
        let val = args
            .get(i + 1)
            .filter(|s| !s.starts_with("--"))
            .ok_or_else(|| format!("missing value for {key}"))?;
        if opts.insert(key.as_str(), val.as_str()).is_some() {
            return Err(format!("duplicate argument {key}"));
        }
        i += 2;
    }
    let get = |k| opts.get(k).copied().ok_or_else(|| format!("missing {k}"));
    let allowed: &[&str] = match command {
        "build" => &[
            "--source",
            "--adapted-artifact",
            "--out",
            "--ndk",
            "--timeout-seconds",
            "--process-supervision",
        ],
        "inspect" => &["--spirv"],
        "verify" => &["--artifact"],
        _ => {
            return Err(
                "expected build, inspect, verify, adapt-storage or verify-adaptation".into(),
            )
        }
    };
    if opts.keys().any(|k| !allowed.contains(k)) {
        return Err("argument does not apply to subcommand".into());
    }
    match command {
        "inspect" => Ok(
            json!({"abi":upopabi::reflect(&read_limit(Path::new(get("--spirv")?),SPV_LIMIT)?)?,"scope":"abi_only"}),
        ),
        "verify" => verify(Path::new(get("--artifact")?)),
        "build" => {
            if opts.contains_key("--source") == opts.contains_key("--adapted-artifact") {
                return Err("build requires exactly one of --source or --adapted-artifact".into());
            }
            let supervision = match opts.get("--process-supervision").copied() {
                None | Some("own-helper-groups") => ProcessSupervision::OwnHelperGroups,
                Some("inherited-group") => ProcessSupervision::InheritedGroup,
                Some(_) => {
                    return Err(
                        "process supervision must be own-helper-groups or inherited-group".into(),
                    )
                }
            };
            if !supervision.is_standalone() && !opts.contains_key("--ndk") {
                return Err("inherited-group requires an explicit --ndk path".into());
            }
            supervision.validate()?;
            let default = std::env::var("REFBENCH_NDK").unwrap_or_else(|_| {
                format!(
                    "{}/ndk/28.2.13676358",
                    std::env::var("ANDROID_SDK_ROOT")
                        .unwrap_or_else(|_| "/opt/homebrew/share/android-commandlinetools".into())
                )
            });
            let seconds: u64 = opts
                .get("--timeout-seconds")
                .copied()
                .unwrap_or("120")
                .parse()
                .map_err(|_| "invalid timeout")?;
            if !(1..=120).contains(&seconds) {
                return Err("timeout must be 1..120 seconds".into());
            }
            build(
                opts.get("--source").map(|s| Path::new(*s)),
                opts.get("--adapted-artifact").map(|s| Path::new(*s)),
                Path::new(get("--out")?),
                Path::new(opts.get("--ndk").copied().unwrap_or(&default)),
                Duration::from_secs(seconds),
                supervision,
            )
        }
        _ => unreachable!(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn malformed_elf_and_strict_arguments() {
        assert!(elf_arm64(&[0; 64]).is_err());
        assert!(cli(&["inspect".into(), "--source".into(), "x".into()]).is_err());
        assert!(cli(&[
            "build".into(),
            "--source".into(),
            "x".into(),
            "--source".into(),
            "y".into()
        ])
        .is_err());
        assert!(!safe_name("../layer"));
        assert!(!safe_name("/layer"));
    }
}
