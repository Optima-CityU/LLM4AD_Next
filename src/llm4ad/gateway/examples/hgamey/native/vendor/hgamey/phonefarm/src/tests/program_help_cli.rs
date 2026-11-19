//! The node's help read runs a document command, never a device command.
use std::process::Command;

#[test]
fn help_entry_preserves_focused_gray_usage() {
    for command in ["gray-layer", "gray-ab"] {
        let direct = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
            .args([command, "--help"]).output().unwrap();
        let read = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
            .args(["help", command]).output().unwrap();
        assert!(read.status.success(), "{}", String::from_utf8_lossy(&read.stderr));
        assert_eq!(read.stdout, direct.stdout);
        assert!(read.stderr.is_empty());
    }
}

#[test]
fn help_entry_prints_usage_for_device_commands_without_running_them() {
    for (command, expected) in [("devices", "devices"), ("eval", "--request"),
                                ("game-deploy", "phonefarm game-deploy"), ("serve", "serve")] {
        let output = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
            .args(["help", command]).env("ADB_BIN", "/no-help-device-access/adb")
            .output().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        assert!(output.stderr.is_empty());
        assert!(String::from_utf8_lossy(&output.stdout).contains(expected));
    }
}
