//! Controlled compiler fixtures validate orchestration, not Android code generation.
#[path = "../upopabi.rs"]
mod upopabi;
use serde_json::Value;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Output, Stdio},
    sync::atomic::{AtomicU64, Ordering},
    time::{Duration, Instant},
};

static SEQ: AtomicU64 = AtomicU64::new(0);
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "phonefarm-artifact-{}-{}",
            std::process::id(),
            SEQ.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        #[cfg(unix)]
        fs::set_permissions(&p, fs::Permissions::from_mode(0o700)).unwrap();
        Self(p)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
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
fn path(p: &Path) -> &str {
    p.to_str().unwrap()
}
fn pystr(p: &Path) -> String {
    serde_json::to_string(path(p)).unwrap()
}
fn executable(p: &Path, body: &str) {
    fs::create_dir_all(p.parent().unwrap()).unwrap();
    fs::write(p,format!("#!/usr/bin/env python3\nimport sys,pathlib,time\nif '--version' in sys.argv:\n print('controlled offline compiler fixture v1');sys.exit(0)\n{body}\n")).unwrap();
    #[cfg(unix)]
    fs::set_permissions(p, fs::Permissions::from_mode(0o700)).unwrap();
}
fn ndk(temp: &Path, glslc_body: Option<&str>, native_body: Option<&str>) -> PathBuf {
    let n = temp.join("fixture-ndk");
    let bin = n.join("toolchains/llvm/prebuilt/linux-x86_64/bin");
    let fixture = temp.join("valid.spv");
    fs::write(
        &fixture,
        upopabi::tests::fixture(0, 16, false, false, false),
    )
    .unwrap();
    let shader = format!("out=pathlib.Path(sys.argv[sys.argv.index('-o')+1]);out.write_bytes(pathlib.Path({}).read_bytes())",pystr(&fixture));
    executable(
        &n.join("shader-tools/linux-x86_64/glslc"),
        glslc_body.unwrap_or(&shader),
    );
    let native = "assert '-ldl' in sys.argv and '-Wl,-z,defs' in sys.argv\nassert any(a.endswith('/npuprobe.cpp') and pathlib.Path(a).is_file() for a in sys.argv)\nout=pathlib.Path(sys.argv[sys.argv.index('-o')+1]);d=bytearray(64);d[:6]=b'\\x7fELF\\x02\\x01';d[16:20]=b'\\x03\\x00\\xb7\\x00';out.write_bytes(d)";
    executable(
        &bin.join("aarch64-linux-android30-clang++"),
        native_body.unwrap_or(native),
    );
    executable(&bin.join("llvm-nm"),"print('000 T vkNegotiateLoaderLayerInterfaceVersion\\n000 T vkGetInstanceProcAddr\\n000 T vkGetDeviceProcAddr\\n000 T vkEnumerateInstanceLayerProperties\\n000 T vkEnumerateInstanceExtensionProperties')");
    executable(&bin.join("llvm-readelf"),"print('0x1 (NEEDED) Shared library: [liblog.so]\\n0x1 (NEEDED) Shared library: [libc.so]')");
    n
}
fn build(temp: &Path, n: &Path) -> Output {
    build_timeout(temp, n, "10")
}
fn build_timeout(temp: &Path, n: &Path, seconds: &str) -> Output {
    cli(&[
        "build",
        "--source",
        path(&temp.join("candidate.comp")),
        "--out",
        path(&temp.join("artifacts")),
        "--ndk",
        path(n),
        "--timeout-seconds",
        seconds,
    ])
}
fn published(temp: &Path) -> Vec<PathBuf> {
    fs::read_dir(temp.join("artifacts"))
        .unwrap()
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .unwrap()
                .to_str()
                .unwrap()
                .starts_with("upop-")
        })
        .collect()
}

#[cfg(unix)]
struct SupervisedOutput {
    output: Output,
    group: i32,
    group_absent_after_launcher: bool,
    outer_accepted: bool,
}

#[cfg(unix)]
fn group_absent(group: i32) -> bool {
    let result = unsafe { libc::kill(-group, 0) };
    result == -1 && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
}

// Actual launcher supervisor: stdout/stderr files cannot keep this wait alive.
// A signal, timeout or live/unknown group is never an accepted build.
#[cfg(unix)]
fn supervised_build(temp: &Path, n: &Path, seconds: &str, isolated: bool) -> SupervisedOutput {
    use std::os::unix::process::CommandExt;
    let stdout = temp.join("supervised.stdout");
    let stderr = temp.join("supervised.stderr");
    let mut command = Command::new(env!("CARGO_BIN_EXE_phonefarm"));
    command
        .args([
            "gray-artifact",
            "build",
            "--source",
            path(&temp.join("candidate.comp")),
            "--out",
            path(&temp.join("artifacts")),
            "--ndk",
            path(n),
            "--timeout-seconds",
            seconds,
            "--process-supervision",
            "inherited-group",
        ])
        .env_remove("REFBENCH_NDK")
        .env_remove("ANDROID_SDK_ROOT")
        .stdin(Stdio::null())
        .stdout(Stdio::from(fs::File::create(&stdout).unwrap()))
        .stderr(Stdio::from(fs::File::create(&stderr).unwrap()));
    if isolated {
        command.process_group(0);
    }
    let mut child = command.spawn().unwrap();
    let group = child.id() as i32;
    let start = Instant::now();
    let status = loop {
        if let Some(s) = child.try_wait().unwrap() {
            break s;
        }
        if start.elapsed() > Duration::from_secs(20) {
            if isolated {
                unsafe {
                    libc::kill(-group, libc::SIGKILL);
                }
            } else {
                child.kill().unwrap();
            }
            let cleanup = Instant::now();
            loop {
                if child.try_wait().unwrap().is_some() {
                    break;
                }
                assert!(
                    cleanup.elapsed() < Duration::from_secs(2),
                    "unreaped controlled launcher"
                );
                std::thread::sleep(Duration::from_millis(5));
            }
            // Timeout cannot accidentally become an accepted status below.
            panic!("supervised fixture exceeded outer wall budget");
        }
        std::thread::sleep(Duration::from_millis(5));
    };
    let absent = isolated && group_absent(group);
    if isolated && !absent {
        unsafe {
            libc::kill(-group, libc::SIGKILL);
        }
        let cleanup = Instant::now();
        while !group_absent(group) && cleanup.elapsed() < Duration::from_secs(2) {
            std::thread::sleep(Duration::from_millis(5));
        }
    }
    SupervisedOutput {
        output: Output {
            status,
            stdout: fs::read(stdout).unwrap(),
            stderr: fs::read(stderr).unwrap(),
        },
        group,
        group_absent_after_launcher: absent,
        outer_accepted: status.success() && absent,
    }
}

#[cfg(unix)]
#[test]
fn inherited_tools_share_the_outer_group_and_artifact_remains_provisional() {
    let temp = Temp::new();
    fs::write(temp.0.join("candidate.comp"), "// controlled fixture\n").unwrap();
    let n = ndk(&temp.0, None, None);
    let record = temp.0.join("tool-groups.tsv");
    for relative in [
        "shader-tools/linux-x86_64/glslc",
        "toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang++",
        "toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-nm",
        "toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf",
    ] {
        let p = n.join(relative);
        let original = fs::read_to_string(&p).unwrap();
        let insert = format!("import sys,pathlib,time\nimport os\nwith open({},'a') as f: f.write(str(os.getpid())+'\\t'+str(os.getpgrp())+'\\n')",pystr(&record));
        fs::write(p, original.replace("import sys,pathlib,time", &insert)).unwrap();
    }
    let result = supervised_build(&temp.0, &n, "10", true);
    assert!(
        result.output.status.success(),
        "{}",
        String::from_utf8_lossy(&result.output.stderr)
    );
    assert!(result.outer_accepted && result.group_absent_after_launcher);
    let groups = fs::read_to_string(record).unwrap();
    assert_eq!(groups.lines().count(), 9);
    for line in groups.lines() {
        let fields: Vec<_> = line.split('\t').collect();
        assert_ne!(fields[0].parse::<i32>().unwrap(), result.group);
        assert_eq!(fields[1].parse::<i32>().unwrap(), result.group);
    }
    let receipt: Value = serde_json::from_slice(&result.output.stdout).unwrap();
    assert_eq!(receipt["requires_outer_quiescence"], true);
    assert_eq!(receipt["outer_quiescence_verified"], false);
    assert_eq!(receipt["deployable"], false);
    let artifact = PathBuf::from(receipt["artifact"].as_str().unwrap());
    // Verify after outer absence still cannot certify that absence on its own.
    let verify = cli(&["verify", "--artifact", path(&artifact)]);
    assert!(verify.status.success());
    let verified: Value = serde_json::from_slice(&verify.stdout).unwrap();
    assert_eq!(verified["state"], "built_offline_outer_quiescence_required");
    assert_eq!(verified["requires_outer_quiescence"], true);
    assert_eq!(verified["outer_quiescence_verified"], false);
    let mut manifest: Value =
        serde_json::from_slice(&fs::read(artifact.join("manifest.json")).unwrap()).unwrap();
    manifest["state"] = "built_offline".into();
    fs::write(
        artifact.join("manifest.json"),
        serde_json::to_vec(&manifest).unwrap(),
    )
    .unwrap();
    assert!(!cli(&["verify", "--artifact", path(&artifact)])
        .status
        .success());
}

#[cfg(unix)]
#[test]
fn inherited_mode_requires_isolation_explicit_tools_and_build_subcommand() {
    let temp = Temp::new();
    fs::write(temp.0.join("candidate.comp"), "// source\n").unwrap();
    let n = ndk(&temp.0, None, None);
    let result = supervised_build(&temp.0, &n, "10", false);
    assert!(!result.output.status.success());
    assert!(String::from_utf8_lossy(&result.output.stderr).contains("fresh isolated process group"));
    assert!(!temp.0.join("artifacts").exists());
    for args in [
        vec![
            "build",
            "--source",
            path(&temp.0.join("candidate.comp")),
            "--out",
            path(&temp.0.join("artifacts")),
            "--process-supervision",
            "inherited-group",
        ],
        vec![
            "build",
            "--source",
            "missing",
            "--out",
            "missing",
            "--process-supervision",
            "unknown",
        ],
        vec![
            "inspect",
            "--spirv",
            "missing",
            "--process-supervision",
            "inherited-group",
        ],
    ] {
        assert!(!cli(&args).status.success());
    }
    let result = supervised_build(&temp.0, &temp.0.join("missing-ndk"), "10", true);
    assert_eq!(result.output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&result.output.stderr).contains("missing tool"));
    assert!(published(&temp.0).is_empty());
}

#[cfg(unix)]
#[test]
fn inherited_caps_kill_the_launcher_group_without_publishing_success() {
    for (kind,body) in [
        ("compile_hang","time.sleep(30)"),
        ("compile_spam","while True: print('x'*65536,flush=True)"),
        ("open_pipe_child","import subprocess;subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);sys.exit(0)"),
        ("output_file","out=pathlib.Path(sys.argv[sys.argv.index('-o')+1]);out.write_bytes(bytes(17*1024*1024))"),
        ("version_hang","time.sleep(30)"),
    ] {
        let temp=Temp::new();
        fs::write(temp.0.join("candidate.comp"),"// source\n").unwrap();
        let n=ndk(&temp.0,Some(body),None);
        if kind=="version_hang" {
            fs::write(n.join("shader-tools/linux-x86_64/glslc"),"#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n").unwrap();
        }
        let result=supervised_build(&temp.0,&n,"2",true);
        assert!(!result.output.status.success(),"{kind}");
        assert!(!result.outer_accepted,"{kind}");
        assert!(published(&temp.0).is_empty(),"{kind}");
        use std::os::unix::process::ExitStatusExt;
        assert_eq!(result.output.status.signal(),Some(libc::SIGKILL),"{kind}");
    }
}

#[cfg(unix)]
#[test]
fn inherited_timeout_does_not_log_into_a_full_unread_stderr_before_killing() {
    use std::os::{
        fd::FromRawFd,
        unix::process::{CommandExt, ExitStatusExt},
    };
    let temp = Temp::new();
    fs::write(temp.0.join("candidate.comp"), "// source\n").unwrap();
    let n = ndk(&temp.0, None, None);
    fs::write(
        n.join("shader-tools/linux-x86_64/glslc"),
        "#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n",
    )
    .unwrap();
    let mut fds = [-1; 2];
    assert_eq!(unsafe { libc::pipe(fds.as_mut_ptr()) }, 0);
    let _reader = unsafe { fs::File::from_raw_fd(fds[0]) };
    let writer = unsafe { fs::File::from_raw_fd(fds[1]) };
    for fd in fds {
        assert_eq!(
            unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) },
            0
        );
    }
    let original = unsafe { libc::fcntl(fds[1], libc::F_GETFL) };
    assert!(original >= 0);
    assert_eq!(
        unsafe { libc::fcntl(fds[1], libc::F_SETFL, original | libc::O_NONBLOCK) },
        0
    );
    let bytes = [b'x'; 8192];
    let mut filled = 0;
    loop {
        let count = unsafe { libc::write(fds[1], bytes.as_ptr().cast(), bytes.len()) };
        if count < 0 {
            assert_eq!(
                std::io::Error::last_os_error().raw_os_error(),
                Some(libc::EAGAIN)
            );
            break;
        }
        filled += count;
        assert!(filled <= 8 * 1024 * 1024, "pipe fill must be bounded");
    }
    assert!(filled > 0);
    assert_eq!(unsafe { libc::fcntl(fds[1], libc::F_SETFL, original) }, 0);
    let mut child = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
        .args([
            "gray-artifact",
            "build",
            "--source",
            path(&temp.0.join("candidate.comp")),
            "--out",
            path(&temp.0.join("artifacts")),
            "--ndk",
            path(&n),
            "--timeout-seconds",
            "1",
            "--process-supervision",
            "inherited-group",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::from(writer))
        .process_group(0)
        .spawn()
        .unwrap();
    let start = Instant::now();
    let mut forced = false;
    let status = loop {
        if let Some(s) = child.try_wait().unwrap() {
            break s;
        }
        if start.elapsed() > Duration::from_secs(4) {
            forced = true;
            unsafe {
                libc::kill(-(child.id() as i32), libc::SIGKILL);
            }
            let cleanup = Instant::now();
            break loop {
                if let Some(s) = child.try_wait().unwrap() {
                    break s;
                }
                assert!(
                    cleanup.elapsed() < Duration::from_secs(2),
                    "unreaped launcher"
                );
                std::thread::sleep(Duration::from_millis(5));
            };
        }
        std::thread::sleep(Duration::from_millis(5));
    };
    assert!(
        !forced,
        "unread stderr blocked the timeout's own group kill"
    );
    assert_eq!(status.signal(), Some(libc::SIGKILL));
    assert!(published(&temp.0).is_empty());
}

#[cfg(unix)]
#[test]
fn outer_supervisor_rejects_closed_pipe_descendants_after_launcher_success() {
    let temp = Temp::new();
    fs::write(temp.0.join("candidate.comp"), "// source\n").unwrap();
    let body="out=pathlib.Path(sys.argv[sys.argv.index('-o')+1]);d=bytearray(64);d[:6]=b'\\x7fELF\\x02\\x01';d[16:20]=b'\\x03\\x00\\xb7\\x00';out.write_bytes(d)\nimport subprocess\nsubprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)";
    let n = ndk(&temp.0, None, Some(body));
    let result = supervised_build(&temp.0, &n, "10", true);
    assert!(
        result.output.status.success(),
        "{}",
        String::from_utf8_lossy(&result.output.stderr)
    );
    assert!(!result.group_absent_after_launcher);
    assert!(
        !result.outer_accepted,
        "successful launcher cannot certify child group absence"
    );
    let receipt: Value = serde_json::from_slice(&result.output.stdout).unwrap();
    assert_eq!(receipt["outer_quiescence_verified"], false);
    assert_eq!(receipt["requires_outer_quiescence"], true);
    assert_eq!(receipt["deployable"], false);
}

#[test]
fn inspect_is_a_real_cross_process_spirv_gate() {
    let temp = Temp::new();
    let file = temp.0.join("valid.spv");
    fs::write(&file, upopabi::tests::fixture(0, 16, false, false, false)).unwrap();
    let out = cli(&["inspect", "--spirv", path(&file)]);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert_eq!(
        serde_json::from_slice::<Value>(&out.stdout).unwrap()["scope"],
        "abi_only"
    );
    fs::write(&file, upopabi::tests::fixture(0, 16, false, true, false)).unwrap();
    assert!(!cli(&["inspect", "--spirv", path(&file)]).status.success());
}

#[test]
fn fixture_build_archives_snapshot_checks_reuse_and_tampering() {
    let temp = Temp::new();
    fs::write(
        temp.0.join("candidate.comp"),
        "// exact original source snapshot\n",
    )
    .unwrap();
    let n = ndk(&temp.0, None, None);
    let out = build(&temp.0, &n);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let v: Value = serde_json::from_slice(&out.stdout).unwrap();
    let dir = PathBuf::from(v["artifact"].as_str().unwrap());
    assert_eq!(v["device_validation"], "not_run");
    assert_eq!(
        fs::read(dir.join("candidate.comp")).unwrap(),
        fs::read(temp.0.join("candidate.comp")).unwrap()
    );
    let m: Value = serde_json::from_slice(&fs::read(dir.join("manifest.json")).unwrap()).unwrap();
    assert_eq!(
        m["descriptor"]["abi"]["local_size"],
        serde_json::json!([16, 16, 1])
    );
    assert!(cli(&["verify", "--artifact", path(&dir)]).status.success());
    let again = build(&temp.0, &n);
    assert!(
        again.status.success(),
        "{}",
        String::from_utf8_lossy(&again.stderr)
    );
    assert_eq!(
        serde_json::from_slice::<Value>(&again.stdout).unwrap()["reused"],
        true
    );
    assert!(m["descriptor"]["inputs"]["dispatch_ownership.h"].is_string());
    assert!(m["descriptor"]["inputs"]["npuprobe.cpp"].is_string());
    let ownership = fs::read(dir.join("dispatch_ownership.h")).unwrap();
    fs::write(dir.join("dispatch_ownership.h"), b"changed ownership").unwrap();
    assert!(!cli(&["verify", "--artifact", path(&dir)]).status.success());
    fs::write(dir.join("dispatch_ownership.h"), ownership).unwrap();
    assert!(cli(&["verify", "--artifact", path(&dir)]).status.success());
    fs::write(dir.join("candidate.spv"), b"tampered").unwrap();
    assert!(!cli(&["verify", "--artifact", path(&dir)]).status.success());
    let reuse = build(&temp.0, &n);
    assert!(!reuse.status.success());
    assert!(String::from_utf8_lossy(&reuse.stderr).contains("hash mismatch"));
}

#[test]
fn compiler_failure_missing_tools_wrong_abi_and_snapshot_mutation_publish_nothing() {
    for mode in [
        "missing",
        "compiler_error",
        "storage_abi",
        "mutate_snapshot",
        "tamper_metadata",
    ] {
        let temp = Temp::new();
        fs::write(temp.0.join("candidate.comp"), "// source\n").unwrap();
        let n = match mode {
            "missing" => temp.0.join("not-installed-ndk"),
            "compiler_error" => ndk(&temp.0,Some("print('GLSL rejected',file=sys.stderr);sys.exit(9)"),None),
            "storage_abi" => { let n = ndk(&temp.0,None,None); fs::write(temp.0.join("valid.spv"),upopabi::tests::fixture(0,16,false,true,false)).unwrap(); n },
            "mutate_snapshot" => {
                let body = format!("src=pathlib.Path(sys.argv[sys.argv.index('-o')-1]);src.write_text('changed');out=pathlib.Path(sys.argv[sys.argv.index('-o')+1]);out.write_bytes(pathlib.Path({}).read_bytes())",pystr(&temp.0.join("valid.spv")));
                ndk(&temp.0,Some(&body),None)
            },
            "tamper_metadata" => ndk(&temp.0,None,Some("out=pathlib.Path(sys.argv[sys.argv.index('-o')+1]);(out.parent/'artifact_metadata.h').write_text('wrong');d=bytearray(64);d[:6]=b'\\x7fELF\\x02\\x01';d[16:20]=b'\\x03\\x00\\xb7\\x00';out.write_bytes(d)")),
            _=>unreachable!(),
        };
        let out = build(&temp.0, &n);
        assert!(!out.status.success(), "mode {mode}");
        assert!(published(&temp.0).is_empty(), "mode {mode}");
        let stage = fs::read_dir(temp.0.join("artifacts"))
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();
        assert!(
            stage.join("failure.json").exists(),
            "mode {mode}: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        assert!(
            !stage.join("upop.comp").exists(),
            "must never compile legacy candidate"
        );
    }
}

#[test]
fn compiler_hang_and_spam_are_bounded_and_fail_closed() {
    for body in ["time.sleep(30)", "while True: print('x'*65536,flush=True)",
        "import subprocess;subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);sys.exit(0)",
        "out=pathlib.Path(sys.argv[sys.argv.index('-o')+1]);out.write_bytes(bytes(17*1024*1024))"] {
        let temp = Temp::new();
        fs::write(temp.0.join("candidate.comp"), "// source\n").unwrap();
        let n = ndk(&temp.0, Some(body), None);
        let start = Instant::now();
        let out = build_timeout(&temp.0, &n,"2");
        assert!(!out.status.success());
        assert!(start.elapsed().as_secs() < 10);
        assert!(published(&temp.0).is_empty());
        assert!(
            String::from_utf8_lossy(&out.stderr).contains("limit")
                || String::from_utf8_lossy(&out.stderr).contains("timeout")
        );
    }
}

#[test]
fn version_probe_hang_is_bounded_too() {
    let temp = Temp::new();
    fs::write(temp.0.join("candidate.comp"), "// source\n").unwrap();
    let n = ndk(&temp.0, None, None);
    fs::write(
        n.join("shader-tools/linux-x86_64/glslc"),
        "#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n",
    )
    .unwrap();
    let start = Instant::now();
    let out = build_timeout(&temp.0, &n, "2");
    assert!(!out.status.success());
    assert!(start.elapsed().as_secs() < 10);
    assert!(String::from_utf8_lossy(&out.stderr).contains("timeout"));
    assert!(published(&temp.0).is_empty());
}

#[test]
fn compiler_reads_snapshot_even_if_original_source_changes() {
    let temp = Temp::new();
    let original = temp.0.join("candidate.comp");
    fs::write(&original, "// original\n").unwrap();
    let body = format!("src=pathlib.Path(sys.argv[sys.argv.index('-o')-1]);out=pathlib.Path(sys.argv[sys.argv.index('-o')+1]);\nif src.name=='candidate.comp':\n assert src.read_text()=='// original\\n';pathlib.Path({}).write_text('// changed outside snapshot\\n')\nout.write_bytes(pathlib.Path({}).read_bytes())",pystr(&original),pystr(&temp.0.join("valid.spv")));
    let n = ndk(&temp.0, Some(&body), None);
    let out = build(&temp.0, &n);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let dir = PathBuf::from(
        serde_json::from_slice::<Value>(&out.stdout).unwrap()["artifact"]
            .as_str()
            .unwrap(),
    );
    assert_eq!(
        fs::read_to_string(dir.join("candidate.comp")).unwrap(),
        "// original\n"
    );
    assert_ne!(fs::read_to_string(&original).unwrap(), "// original\n");
    fs::write(dir.join("untracked"), "extra").unwrap();
    assert!(!cli(&["verify", "--artifact", path(&dir)]).status.success());
    fs::remove_file(dir.join("untracked")).unwrap();
    #[cfg(unix)]
    {
        let snapshot = dir.join("candidate.comp");
        let bytes = fs::read(&snapshot).unwrap();
        fs::write(&original, bytes).unwrap();
        fs::remove_file(&snapshot).unwrap();
        std::os::unix::fs::symlink(&original, &snapshot).unwrap();
        assert!(!cli(&["verify", "--artifact", path(&dir)]).status.success());
    }
}

#[test]
fn oversize_source_is_rejected_before_tools_or_staging() {
    let temp = Temp::new();
    fs::write(
        temp.0.join("candidate.comp"),
        vec![b' '; 4 * 1024 * 1024 + 1],
    )
    .unwrap();
    let out = build(&temp.0, &temp.0.join("none"));
    assert!(!out.status.success());
    assert!(!temp.0.join("artifacts").exists());
}

#[test]
fn compiler_background_writer_with_closed_pipes_is_rejected() {
    let temp = Temp::new();
    fs::write(temp.0.join("candidate.comp"), "// source\n").unwrap();
    let body = format!("import subprocess\nout=pathlib.Path(sys.argv[sys.argv.index('-o')+1]);out.write_bytes(pathlib.Path({}).read_bytes())\nsubprocess.Popen([sys.executable,'-c',\"import os,time,pathlib;os.close(1);os.close(2);time.sleep(30);pathlib.Path(\"+repr(str(out.parent/'late-write'))+\").write_text('late')\"])\n",pystr(&temp.0.join("valid.spv")));
    let n = ndk(&temp.0, Some(&body), None);
    let out = build_timeout(&temp.0, &n, "3");
    assert!(!out.status.success());
    assert!(
        String::from_utf8_lossy(&out.stderr).contains("background descendants"),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(published(&temp.0).is_empty());
    let stage = fs::read_dir(temp.0.join("artifacts"))
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    assert!(!stage.join("late-write").exists());
}
