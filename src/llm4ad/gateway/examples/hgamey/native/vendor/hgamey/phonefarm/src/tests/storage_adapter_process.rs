//! Real Naga bytecode plus cross-process freeze/import tests. Tool fixtures are not Android builds.
use serde_json::Value;
#[cfg(unix)]
use std::os::unix::fs::{symlink, PermissionsExt};
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Output},
    sync::atomic::{AtomicU64, Ordering},
};
static SEQ: AtomicU64 = AtomicU64::new(0);
const SOURCE: &str = include_str!("fixtures/storage_sr.comp");
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "phonefarm-adapter-{}-{}",
            std::process::id(),
            SEQ.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        Self(p)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn path(p: &Path) -> &str {
    p.to_str().unwrap()
}
fn cli(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_phonefarm"))
        .arg("gray-artifact")
        .args(args)
        .env_remove("REFBENCH_NDK")
        .env_remove("ANDROID_SDK_ROOT")
        .output()
        .unwrap()
}
fn success(out: Output) -> Value {
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    serde_json::from_slice(&out.stdout).unwrap()
}
fn failure(out: Output) -> String {
    assert_eq!(out.status.code(), Some(2));
    let text = String::from_utf8_lossy(&out.stderr).to_string();
    assert!(serde_json::from_str::<Value>(&text).is_ok());
    text
}
fn naga_spirv() -> Vec<u8> {
    let mut front = naga::front::glsl::Frontend::default();
    let m = front
        .parse(
            &naga::front::glsl::Options::from(naga::ShaderStage::Compute),
            SOURCE,
        )
        .unwrap();
    let info = naga::valid::Validator::new(
        naga::valid::ValidationFlags::all(),
        naga::valid::Capabilities::all(),
    )
    .validate(&m)
    .unwrap();
    naga::back::spv::write_vec(&m, &info, &naga::back::spv::Options::default(), None)
        .unwrap()
        .iter()
        .flat_map(|w| w.to_le_bytes())
        .collect()
}
fn setup(t: &Path) -> PathBuf {
    fs::write(t.join("candidate.comp"), SOURCE).unwrap();
    fs::write(t.join("candidate.spv"), naga_spirv()).unwrap();
    adapt(t)
}
fn adapt(t: &Path) -> PathBuf {
    let v = success(cli(&[
        "adapt-storage",
        "--source",
        path(&t.join("candidate.comp")),
        "--spirv",
        path(&t.join("candidate.spv")),
        "--out",
        path(&t.join("adaptations")),
    ]));
    assert_eq!(v["deployable"], false);
    assert_eq!(v["source_spirv_association"], "caller_supplied_unverified");
    PathBuf::from(v["path"].as_str().unwrap())
}
fn verify(dir: &Path) -> Output {
    cli(&["verify-adaptation", "--artifact", path(dir)])
}
fn executable(p: &Path, body: &str) {
    fs::create_dir_all(p.parent().unwrap()).unwrap();
    fs::write(p,format!("#!/usr/bin/env python3\nimport sys,pathlib\nif '--version' in sys.argv:\n print('controlled compiler orchestration fixture');sys.exit(0)\n{body}\n")).unwrap();
    #[cfg(unix)]
    fs::set_permissions(p, fs::Permissions::from_mode(0o700)).unwrap();
}
fn ndk(t: &Path) -> PathBuf {
    let n = t.join("fixture-ndk");
    let bin = n.join("toolchains/llvm/prebuilt/linux-x86_64/bin");
    let copy = serde_json::to_string(path(&t.join("candidate.spv"))).unwrap();
    let calls = serde_json::to_string(path(&t.join("compiler_inputs.log"))).unwrap();
    executable(&n.join("shader-tools/linux-x86_64/glslc"),&format!("inp=pathlib.Path(sys.argv[sys.argv.index('-o')-1]);assert inp.name=='copyprobe_copy.comp','storage source must not be recompiled';pathlib.Path({calls}).open('a').write(inp.name+'\\n');pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_bytes(pathlib.Path({copy}).read_bytes())"));
    executable(&bin.join("aarch64-linux-android30-clang++"),"out=pathlib.Path(sys.argv[sys.argv.index('-o')+1]);d=bytearray(64);d[:6]=b'\\x7fELF\\x02\\x01';d[16:20]=b'\\x03\\x00\\xb7\\x00';out.write_bytes(d)");
    executable(&bin.join("llvm-nm"),"print('000 T vkNegotiateLoaderLayerInterfaceVersion\\n000 T vkGetInstanceProcAddr\\n000 T vkGetDeviceProcAddr\\n000 T vkEnumerateInstanceLayerProperties\\n000 T vkEnumerateInstanceExtensionProperties')");
    executable(&bin.join("llvm-readelf"),"print('0x1 (NEEDED) Shared library: [liblog.so]\\n0x1 (NEEDED) Shared library: [libc.so]')");
    n
}
fn build(t: &Path, adapted: &Path, n: &Path) -> Output {
    cli(&[
        "build",
        "--adapted-artifact",
        path(adapted),
        "--out",
        path(&t.join("artifacts")),
        "--ndk",
        path(n),
        "--timeout-seconds",
        "10",
    ])
}
fn rewrite(p: &Path, f: impl FnOnce(&mut Value)) {
    let mut v: Value = serde_json::from_slice(&fs::read(p).unwrap()).unwrap();
    f(&mut v);
    fs::write(p, serde_json::to_vec_pretty(&v).unwrap()).unwrap();
}

#[test]
fn real_naga_cli_freezes_reuses_and_replays_exact_bytecode() {
    let temp = Temp::new();
    let dir = setup(&temp.0);
    let v = success(verify(&dir));
    assert_eq!(
        v["descriptor"]["changes"]["output_shared_original_type"],
        true
    );
    assert_eq!(v["descriptor"]["changes"]["image_reads_to_fetch"], 1);
    assert_eq!(adapt(&temp.0), dir);
    assert_eq!(
        fs::read(dir.join("original.spv")).unwrap(),
        fs::read(temp.0.join("candidate.spv")).unwrap()
    );
    assert_eq!(
        success(cli(&["inspect", "--spirv", path(&dir.join("adapted.spv"))]))["scope"],
        "abi_only"
    );
    fs::write(
        temp.0.join("candidate.comp"),
        "// caller-supplied mismatched source: association must remain unverified\n",
    )
    .unwrap();
    let other = adapt(&temp.0);
    assert_ne!(other, dir);
    assert_eq!(
        success(verify(&other))["source_spirv_association"],
        "caller_supplied_unverified"
    );
}
#[test]
fn imported_builder_archives_origin_and_never_recompiles_storage_candidate() {
    let temp = Temp::new();
    let dir = setup(&temp.0);
    let n = ndk(&temp.0);
    let result = success(build(&temp.0, &dir, &n));
    let artifact = PathBuf::from(result["artifact"].as_str().unwrap());
    assert_eq!(result["deployable"], false);
    let manifest: Value =
        serde_json::from_slice(&fs::read(artifact.join("manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["checks"]["candidate_route"], "adapted_spirv");
    let origin = &manifest["descriptor"]["adaptation"];
    for key in ["device_validation", "semantic_equivalence", "qualification"] {
        assert_eq!(origin[key], "not_run");
    }
    assert_eq!(origin["deployable"], false);
    assert_eq!(
        origin["source_spirv_association"],
        "caller_supplied_unverified"
    );
    assert_eq!(
        fs::read(artifact.join("candidate.spv")).unwrap(),
        fs::read(dir.join("adapted.spv")).unwrap()
    );
    assert_eq!(
        fs::read(artifact.join("original_candidate.spv")).unwrap(),
        fs::read(dir.join("original.spv")).unwrap()
    );
    for (stored, original) in [
        ("adaptation_descriptor.json", "descriptor.json"),
        ("adaptation_manifest.json", "manifest.json"),
    ] {
        assert_eq!(
            fs::read(artifact.join(stored)).unwrap(),
            fs::read(dir.join(original)).unwrap()
        );
    }
    assert_eq!(
        fs::read_to_string(temp.0.join("compiler_inputs.log")).unwrap(),
        "copyprobe_copy.comp\n"
    );
    assert!(fs::read_to_string(artifact.join("candidate_compile.log"))
        .unwrap()
        .contains("NOT recompiled"));
    success(cli(&["verify", "--artifact", path(&artifact)]));
    rewrite(&artifact.join("manifest.json"), |v| {
        v["checks"]["candidate_route"] = Value::String("compiled_glsl".into())
    });
    assert!(failure(cli(&["verify", "--artifact", path(&artifact)]))
        .contains("route metadata mismatch"));
    rewrite(&artifact.join("manifest.json"), |v| {
        v["checks"]["candidate_route"] = Value::String("adapted_spirv".into())
    });
    assert_eq!(success(build(&temp.0, &dir, &n))["reused"], true);
    fs::write(artifact.join("original_candidate.spv"), b"tampered").unwrap();
    assert!(failure(cli(&["verify", "--artifact", path(&artifact)])).contains("hash mismatch"));
}
#[test]
fn tampering_and_status_forgery_do_not_reach_native_tools() {
    for name in [
        "original.comp",
        "original.spv",
        "adapted.spv",
        "descriptor.json",
    ] {
        let temp = Temp::new();
        let dir = setup(&temp.0);
        fs::write(dir.join(name), b"tampered").unwrap();
        assert!(failure(verify(&dir)).contains("hash mismatch"));
        let n = ndk(&temp.0);
        failure(build(&temp.0, &dir, &n));
        assert!(!temp.0.join("compiler_inputs.log").exists());
        assert!(!temp.0.join("artifacts").exists());
    }
    for (key, val) in [
        ("deployable", Value::Bool(true)),
        ("semantic_equivalence", Value::String("passed".into())),
        ("source_spirv_association", Value::String("verified".into())),
    ] {
        let temp = Temp::new();
        let dir = setup(&temp.0);
        rewrite(&dir.join("manifest.json"), |v| v[key] = val);
        assert!(failure(verify(&dir)).contains("metadata invalid"));
    }
}
#[test]
fn hash_reforging_cannot_bypass_deterministic_transformation() {
    use sha2::{Digest, Sha256};
    let temp = Temp::new();
    let dir = setup(&temp.0);
    let mut changed = fs::read(dir.join("adapted.spv")).unwrap();
    changed[8] ^= 1;
    fs::write(dir.join("adapted.spv"), &changed).unwrap();
    let digest = format!("{:x}", Sha256::digest(&changed));
    rewrite(&dir.join("manifest.json"), |v| {
        v["files"]["adapted.spv"] = Value::String(digest)
    });
    assert!(failure(verify(&dir)).contains("replay mismatch"));
}
#[test]
fn mutually_exclusive_sources_and_missing_toolchain_fail_explicitly() {
    let temp = Temp::new();
    let dir = setup(&temp.0);
    failure(cli(&[
        "build",
        "--source",
        path(&temp.0.join("candidate.comp")),
        "--adapted-artifact",
        path(&dir),
        "--out",
        path(&temp.0.join("artifacts")),
    ]));
    assert!(!temp.0.join("artifacts").exists());
    failure(cli(&["adapt-storage", "--source", "x", "--source", "y"]));
    failure(cli(&[
        "verify-adaptation",
        "--artifact",
        path(&dir),
        "--out",
        "x",
    ]));
    assert!(failure(build(&temp.0, &dir, &temp.0.join("missing-ndk"))).contains("missing tool"));
    for e in fs::read_dir(temp.0.join("artifacts")).unwrap() {
        assert!(!e.unwrap().path().join("manifest.json").exists());
    }
}
#[cfg(unix)]
#[test]
fn symlinks_extra_files_nonregular_and_oversize_inputs_are_rejected() {
    let temp = Temp::new();
    let dir = setup(&temp.0);
    let linked = temp.0.join("linked");
    symlink(&dir, &linked).unwrap();
    failure(verify(&linked));
    fs::write(dir.join("extra"), "untracked").unwrap();
    failure(verify(&dir));
    fs::remove_file(dir.join("extra")).unwrap();
    fs::remove_file(dir.join("adapted.spv")).unwrap();
    symlink(temp.0.join("candidate.spv"), dir.join("adapted.spv")).unwrap();
    failure(verify(&dir));
    let fifo = temp.0.join("fifo");
    let cpath = std::ffi::CString::new(path(&fifo)).unwrap();
    assert_eq!(unsafe { libc::mkfifo(cpath.as_ptr(), 0o600) }, 0);
    failure(cli(&[
        "adapt-storage",
        "--source",
        path(&fifo),
        "--spirv",
        path(&temp.0.join("candidate.spv")),
        "--out",
        path(&temp.0.join("outputs")),
    ]));
    fs::write(
        temp.0.join("candidate.comp"),
        vec![b' '; 4 * 1024 * 1024 + 1],
    )
    .unwrap();
    assert!(failure(cli(&[
        "adapt-storage",
        "--source",
        path(&temp.0.join("candidate.comp")),
        "--spirv",
        path(&temp.0.join("candidate.spv")),
        "--out",
        path(&temp.0.join("outputs"))
    ]))
    .contains("exceeds"));
    assert!(!temp.0.join("outputs").exists());
    fs::write(temp.0.join("candidate.comp"), SOURCE).unwrap();
    fs::File::create(temp.0.join("candidate.spv"))
        .unwrap()
        .set_len(16 * 1024 * 1024 + 1)
        .unwrap();
    assert!(failure(cli(&[
        "adapt-storage",
        "--source",
        path(&temp.0.join("candidate.comp")),
        "--spirv",
        path(&temp.0.join("candidate.spv")),
        "--out",
        path(&temp.0.join("outputs"))
    ]))
    .contains("exceeds"));
}
