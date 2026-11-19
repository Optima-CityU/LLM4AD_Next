//! Real CLI stdout tests for subcommand-focused gray help; no device is touched.
use std::process::Command;

fn help_stdout(subcommand: &str) -> String {
    let output = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
        .args([subcommand, "--help"])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{subcommand} --help failed: stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        output.stderr.is_empty(),
        "{subcommand} --help wrote stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).unwrap()
}

fn assert_not_global_usage(stdout: &str) {
    assert!(!stdout.contains("phonefarm v0.2"), "global usage leaked: {stdout}");
    assert!(!stdout.contains("\n跑局:"), "global usage leaked: {stdout}");
}

#[test]
fn gray_layer_help_uses_focused_stdout() {
    let stdout = help_stdout("gray-layer");
    assert!(stdout.starts_with("用法: phonefarm gray-layer "), "{stdout}");
    assert!(stdout.contains("phonefarm gray-layer session --mode MODE"), "{stdout}");
    assert_not_global_usage(&stdout);
}

#[test]
fn gray_ab_help_uses_focused_stdout() {
    let stdout = help_stdout("gray-ab");
    assert!(stdout.starts_with("用法: phonefarm gray-ab [--pairs N]"), "{stdout}");
    assert!(stdout.contains("phonefarm gray-ab recover --session DIR --serial S [--json]"), "{stdout}");
    assert!(stdout.contains("copyprobe 当前不支持执行。"), "{stdout}");
    assert!(!stdout.contains("phonefarm gray-layer session"), "wrong subcommand usage: {stdout}");
    assert_not_global_usage(&stdout);
}
