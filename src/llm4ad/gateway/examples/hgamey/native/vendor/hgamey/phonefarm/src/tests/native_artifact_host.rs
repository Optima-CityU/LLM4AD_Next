use serde_json::Value;
use std::{fs, path::PathBuf, process::Command};

#[test]
fn host_native_counters_and_actual_layer_hooks() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../knobs/gray");
    let temp = std::env::temp_dir().join(format!(
        "phonefarm-native-artifact-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir(&temp).unwrap();
    let cxx = std::env::var("CXX").unwrap_or_else(|_| "c++".into());
    let exe = temp.join("stats-test");
    let output = Command::new(&cxx)
        .args(["-std=c++17", "-Wall", "-Wextra", "-Werror"])
        .arg(root.join("tests/artifact_stats_test.cpp"))
        .arg("-o")
        .arg(&exe)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let output = Command::new(&exe).output().unwrap();
    assert!(output.status.success());
    let stats: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(stats["queue_submitted"], 8);
    let headers = std::env::var("VULKAN_HEADERS_DIR").map(PathBuf::from).unwrap_or_else(|_|PathBuf::from("/Applications/DevEco-Studio.app/Contents/sdk/default/openharmony/native/sysroot/usr/include"));
    if !headers.join("vulkan/vulkan.h").exists() {
        eprintln!("Full layer host-hook test not run: set VULKAN_HEADERS_DIR to existing Vulkan headers. Counter header test passed.");
        fs::remove_dir_all(&temp).unwrap();
        return;
    }
    // Isolate just Vulkan headers; do not compile against an OHOS sysroot on macOS.
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(headers.join("vulkan"), temp.join("vulkan")).unwrap();
        if headers.join("vk_video").exists() {
            std::os::unix::fs::symlink(headers.join("vk_video"), temp.join("vk_video")).unwrap();
        }
    }
    fs::write(temp.join("copy_spv.h"),"alignas(4) static const unsigned char kCopySpv[]={3,2,35,7};static const unsigned int kCopySpvLen=4;alignas(4) static const unsigned char kUpopSpv[]={3,2,35,7};static const unsigned int kUpopSpvLen=4;alignas(4) static const unsigned char kNpuPackSpv[]={3,2,35,7};static const unsigned int kNpuPackSpvLen=4;alignas(4) static const unsigned char kNpuUnpackSpv[]={3,2,35,7};static const unsigned int kNpuUnpackSpvLen=4;").unwrap();
    for (dynamic, fixture) in [(false, true), (true, true), (false, false), (true, false)] {
        if dynamic {
            fs::write(temp.join("artifact_metadata.h"),"#define RK_DYNAMIC_ARTIFACT_METADATA 1\nstatic const char kArtifactId[]=\"upop-host-fixture\";static const char kCandidateSourceSha256[]=\"source-fixture\";static const char kCandidateSpirvSha256[]=\"spv-fixture\";\n").unwrap();
        }
        let mut cmd = Command::new(&cxx);
        cmd.args(["-std=c++17", "-fno-exceptions", "-fno-rtti", "-I"])
            .arg(&temp)
            .arg("-I")
            .arg(root.join("tests/stubs"));
        if dynamic {
            cmd.arg("-DRK_DYNAMIC_UPOP_BUILD=1");
        }
        if fixture {
            cmd.arg("-DRK_LIFETIME_HOST_FIXTURE=1");
        }
        let exe = temp.join(format!("layer-{dynamic}-{fixture}-test"));
        let output = cmd
            .arg(root.join("tests/layer_artifact_test.cpp"))
            // main 的层把 present 钩子接到 npuprobe (build_layer.sh 一起编), 宿主测试同样链进来
            .arg(root.join("layer/npuprobe.cpp"))
            // ruler 侧 framedump 档 (build_layer.sh 一起编), 宿主测试同样链进来
            .arg(root.join("layer/framedump.cpp"))
            // interp (I6 层内多 present), build_layer.sh 一起编
            .arg(root.join("layer/interp.cpp"))
            // selfsr (送显前超分), build_layer.sh 一起编; 没有 selfsr_spv.h 时模块自己降级
            .arg(root.join("layer/selfsr.cpp"))
            .arg("-o")
            .arg(&exe)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let output = Command::new(&exe).output().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let marker: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(
            marker["artifact_id"],
            if dynamic {
                "upop-host-fixture"
            } else {
                "legacy-fixed-upop"
            }
        );
        assert_eq!(marker["deployable"], false);
        assert_eq!(
            marker["native_marker"]["upop_artifact"]["ownership_scope"],
            "recording_generation_per_dispatch"
        );
        assert_eq!(
            marker["native_marker"]["upop_artifact"]["evaluation_eligible"],
            false
        );
        assert_eq!(
            marker["native_marker"]["upop_artifact"]["gpu_completion_proven"],
            false
        );
        if fixture {
            assert!(marker["ownership_cases"].as_u64().unwrap() >= 39);
            assert!(marker["dispatch_recorded"].as_u64().unwrap() >= 141);
        } else {
            assert_eq!(marker["dispatch_recorded"], 0);
            assert_eq!(marker["ownership_cases"], 1);
        }
    }
    let android_fixture = Command::new(&cxx)
        .args([
            "-std=c++17",
            "-D__ANDROID__=1",
            "-DRK_LIFETIME_HOST_FIXTURE=1",
            "-fsyntax-only",
            "-I",
        ])
        .arg(&temp)
        .arg("-I")
        .arg(root.join("tests/stubs"))
        .arg(root.join("layer/vk_layer_refknobs.cpp"))
        .output()
        .unwrap();
    assert!(!android_fixture.status.success());
    assert!(String::from_utf8_lossy(&android_fixture.stderr)
        .contains("Host lifetime contracts are forbidden"));
    fs::remove_file(temp.join("artifact_metadata.h")).unwrap();
    let output = Command::new(&cxx)
        .args([
            "-std=c++17",
            "-DRK_DYNAMIC_UPOP_BUILD=1",
            "-fsyntax-only",
            "-I",
        ])
        .arg(&temp)
        .arg("-I")
        .arg(root.join("tests/stubs"))
        .arg(root.join("layer/vk_layer_refknobs.cpp"))
        .output()
        .unwrap();
    assert!(
        !output.status.success(),
        "dynamic build must fail without metadata"
    );
    fs::remove_dir_all(temp).unwrap();
}
