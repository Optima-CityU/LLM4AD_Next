//! Application observations use fake transports, including the HDC client bootstrap.
#![cfg(unix)]
use serde_json::Value;
use std::{fs, os::unix::fs::PermissionsExt, process::Command};

#[test]
fn app_snapshot_records_foreground_and_running_non_system_apps_without_writes() {
    let root = std::env::temp_dir().join(format!("pf-app-snapshot-{}", std::process::id()));
    fs::create_dir_all(&root).unwrap();
    let tool = root.join("adb");
    fs::write(&tool, r#"#!/usr/bin/env python3
import json,os,signal,sys
args=sys.argv[1:]
with open(os.environ['APP_CALLS'],'a') as log: log.write(json.dumps(args)+'\n')
if args==['start-server'] or args==['list','targets']: sys.exit(0)
if 'unavailable' in args: sys.exit('device disconnected')
if 'hung' in args:
    signal.alarm(20)
    with open(os.environ['APP_HUNG_PID'],'w') as record: record.write(str(os.getpid()))
    signal.pause()
command=args[-1]
values={
 'dumpsys window': 'mCurrentFocus=Window{a u0 com.example.game/.Main}',
 'pm list packages -3': 'package:com.example.game\npackage:com.example.megacity\npackage:com.example.stopped',
 'ps -A -o NAME': 'NAME\ncom.example.game\ncom.example.game:renderer\ncom.example.megacity\ncom.ohos.sceneboard',
 'aa dump -a': 'bundle name [com.example.game]\nmain name [Main]\nstate #FOREGROUND',
 'bm dump -a': 'OK!\ncom.example.game\ncom.example.megacity\ncom.example.stopped\ncom.ohos.sceneboard',
}
name=os.path.basename(sys.argv[0])
if name in ('aa','ps','bm'):
    key=' '.join([name,*args])
    assert key in values, ('unexpected device action',name,args)
    print(values[key])
    sys.exit(0)
if '__PF_APP_PART__' in command:
    os.execv('/bin/sh',['sh','-c',command])
key=command.split('\n')[0]
assert key in values, ('unexpected transport action',args)
print(values[key])
print('__PF_APP_STATUS__0')
"#).unwrap();
    fs::set_permissions(&tool, fs::Permissions::from_mode(0o755)).unwrap();
    for name in ["hdc", "aa", "ps", "bm"] {
        std::os::unix::fs::symlink(&tool, root.join(name)).unwrap();
    }
    for serial in ["android-test", "hdc:harmony-test", "unavailable", "hung"] {
        let started = std::time::Instant::now();
        let out = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
            .args(["app-snapshot", "--serial", serial])
            .env("ADB_BIN", &tool)
            .env("HDC_BIN", root.join("hdc"))
            .env(
                "PATH",
                format!("{}:{}", root.display(), std::env::var("PATH").unwrap()),
            )
            .env("TMPDIR", &root)
            .env("APP_CALLS", root.join("calls"))
            .env("APP_HUNG_PID", root.join("hung-pid"))
            .env("DEVLOCK_DIR", root.join("locks"))
            .env("PHONEFARM_LEASE_DIR", root.join("leases"))
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        let value: Value = serde_json::from_slice(&out.stdout).unwrap();
        assert_eq!(value["serial"], serial);
        assert!(
            !root.join("locks").exists(),
            "read-only observation must not acquire a device lock"
        );
        if serial == "unavailable" || serial == "hung" {
            assert!(value["foreground"].is_null(), "{value}");
            assert!(value["running_third_party"].is_null(), "{value}");
            assert!(!value["errors"].as_array().unwrap().is_empty(), "{value}");
            if serial == "hung" {
                assert!(
                    started.elapsed() < std::time::Duration::from_secs(10),
                    "observation stalled the caller"
                );
                let pid = fs::read_to_string(root.join("hung-pid")).unwrap();
                let ps = Command::new("ps")
                    .args(["-o", "stat=", "-p", pid.trim()])
                    .output()
                    .unwrap();
                let state = String::from_utf8_lossy(&ps.stdout);
                assert!(
                    state.trim().is_empty() || state.contains('Z'),
                    "timed-out adb is still running: {state}"
                );
                let lock = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
                    .args([
                        "lease",
                        "--serial",
                        "hung",
                        "--timeout-ms",
                        "0",
                        "--",
                        "/usr/bin/true",
                    ])
                    .env("DEVLOCK_DIR", root.join("locks"))
                    .env("PHONEFARM_LEASE_DIR", root.join("leases"))
                    .output()
                    .unwrap();
                assert!(
                    lock.status.success(),
                    "timed-out observation retained a device lock"
                );
            }
            continue;
        }
        assert_eq!(value["foreground"], "com.example.game", "{value}");
        assert_eq!(
            value["running_third_party"],
            serde_json::json!(["com.example.game", "com.example.megacity"])
        );
        assert_eq!(value["errors"], serde_json::json!([]));
    }
    fs::remove_dir_all(root).unwrap();
}
