//! Real scoped task -> shell -> PF gray control, with an isolated fake Android.
#![cfg(unix)]
#[test]
fn inherited_job_scope_reaches_layer_attach_status_and_restoration() {
    let result = std::process::Command::new("python3")
        .current_dir(concat!(env!("CARGO_MANIFEST_DIR"), "/../tools/tests"))
        .env("PHONEFARM_TEST_BINARY", env!("CARGO_BIN_EXE_phonefarm"))
        .args(["-m", "unittest", "test_devlock.DevlockBridgeTests.test_gray_control_inherits_job_lane_through_attach_and_status", "-v"])
        .output().unwrap();
    assert!(result.status.success(), "{}{}", String::from_utf8_lossy(&result.stdout), String::from_utf8_lossy(&result.stderr));
    assert!(!String::from_utf8_lossy(&result.stderr).contains("skipped"), "the real devlock path must run");
}
