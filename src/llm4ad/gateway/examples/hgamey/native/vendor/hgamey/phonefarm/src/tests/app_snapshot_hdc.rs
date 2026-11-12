//! HDC batch coverage, including the measured HUAWEI bundle-detail cost.
#![cfg(unix)]
use serde_json::{json, Value};
use std::{fs, os::unix::fs::PermissionsExt, path::PathBuf, process::Command, time::Instant};

struct Fixture(PathBuf);
impl Fixture {
    fn new(mode: &str) -> Self {
        let root = std::env::temp_dir().join(format!("pf-app-hdc-{}-{mode}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let hdc = root.join("hdc");
        fs::write(
            &hdc,
            r#"#!/usr/bin/env python3
import json,os,sys,time
args=sys.argv[1:]
if args==['list','targets']: sys.exit(0)
assert args[:4]==['-p','-t','harmony-test','shell'],args
with open(os.environ['HDC_CALLS'],'a') as log: log.write(json.dumps(args)+'\n')
# Keep the earlier synthetic round-trip stress case, not a production timing claim.
time.sleep(0.15 if os.environ['APP_MODE']=='scale' else 1.5)
os.execv('/bin/sh',['sh','-c',args[4]])
"#,
        )
        .unwrap();
        fs::set_permissions(&hdc, fs::Permissions::from_mode(0o755)).unwrap();
        let device = root.join("device-command");
        fs::write(&device, r#"#!/usr/bin/env python3
import json,os,pathlib,signal,sys,time
name=pathlib.Path(sys.argv[0]).name
args=[name,*sys.argv[1:]]
mode=os.environ['APP_MODE']
system=([f'com.huawei.hmos.app{i}' for i in range(14)]
        +[f'com.ohos.app{i}' for i in range(4)]+[f'ohos.app{i}' for i in range(4)])
running=[*system,'com.diangroup.myapplication','com.atomicservice.fixture']
bundles=[*running,*[f'com.example.stopped{i}' for i in range(200)]]
processes=['NAME',*running,'com.diangroup.myapplication:renderer',*[f'daemon{i}' for i in range(152)]]
assert len(processes)==178 and len(bundles)==224
assert len(set(p.split(':')[0] for p in processes)&set(bundles))==24
with open(os.environ['DEVICE_CALLS'],'a') as log: log.write(json.dumps(args)+'\n')
if name=='aa':
    assert args==['aa','dump','-a'],args
    if mode=='fail-aa': sys.exit(3)
    foreground='com.diangroup.myapplication' if mode=='scale' else 'com.example.game'
    print('bundle name ['+foreground+']\nmain name [Main]\nstate #FOREGROUND')
elif name=='ps':
    assert args==['ps','-A','-o','NAME'],args
    if mode=='hung':
        pathlib.Path(os.environ['HUNG_PID']).write_text(str(os.getpid()))
        signal.alarm(20)
        signal.pause()
    if mode=='scale':
        time.sleep(0.17)
        print('\n'.join(processes))
    else:
        print('NAME\n  com.example.game\ncom.example.game:renderer\ncom.example.megacity\ncom.ohos.sceneboard')
elif args==['bm','dump','-a']:
    if mode=='fail-bm': sys.exit(9)
    if mode=='scale':
        time.sleep(0.23)
        print('OK!\n'+'\n'.join(bundles))
    else:
        print('OK!\ncom.example.game\ncom.example.megacity\ncom.example.stopped\ncom.ohos.sceneboard')
else:
    assert mode=='scale' and args[:3]==['bm','dump','-n'],args
    bundle=args[3]
    assert bundle in running,args
    time.sleep(0.34)  # Measured per-bundle device work, independent of transport.
    print(bundle+':\n'+json.dumps({'applicationInfo':{'isSystemApp':bundle in system},'padding':'x'*(31*1024)}))
"#).unwrap();
        fs::set_permissions(&device, fs::Permissions::from_mode(0o755)).unwrap();
        for name in ["aa", "ps", "bm"] {
            std::os::unix::fs::symlink(&device, root.join(name)).unwrap();
        }
        Self(root)
    }

    fn run(&self, mode: &str) -> Value {
        let out = Command::new(env!("CARGO_BIN_EXE_phonefarm"))
            .args(["app-snapshot", "--serial", "hdc:harmony-test"])
            .env("HDC_BIN", self.0.join("hdc"))
            .env(
                "PATH",
                format!("{}:{}", self.0.display(), std::env::var("PATH").unwrap()),
            )
            .env("TMPDIR", &self.0)
            .env("HDC_CALLS", self.0.join("hdc-calls"))
            .env("DEVICE_CALLS", self.0.join("device-calls"))
            .env("HUNG_PID", self.0.join("hung-pid"))
            .env("APP_MODE", mode)
            .env("DEVLOCK_DIR", self.0.join("locks"))
            .env("PHONEFARM_LEASE_DIR", self.0.join("leases"))
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        serde_json::from_slice(&out.stdout).unwrap()
    }

    fn calls(&self, name: &str) -> Vec<Value> {
        fs::read_to_string(self.0.join(name))
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

#[test]
fn huawei_sized_inventory_does_not_query_bundle_details() {
    let fixture = Fixture::new("scale");
    let value = fixture.run("scale");
    assert_eq!(
        value["foreground"], "com.diangroup.myapplication",
        "{value}"
    );
    assert_eq!(
        value["running_third_party"],
        json!(["com.atomicservice.fixture", "com.diangroup.myapplication"]),
        "{value}"
    );
    assert_eq!(value["errors"], json!([]), "{value}");
    assert_eq!(fixture.calls("hdc-calls").len(), 1);
    assert_eq!(
        fixture.calls("device-calls"),
        vec![
            json!(["aa", "dump", "-a"]),
            json!(["ps", "-A", "-o", "NAME"]),
            json!(["bm", "dump", "-a"])
        ]
    );
    assert!(!fixture.0.join("locks").exists());
}

#[test]
fn slow_hdc_samples_all_apps_in_one_shell_round_trip() {
    let fixture = Fixture::new("ok");
    let value = fixture.run("ok");
    assert_eq!(value["foreground"], "com.example.game", "{value}");
    assert_eq!(
        value["running_third_party"],
        json!(["com.example.game", "com.example.megacity"]),
        "{value}"
    );
    assert_eq!(value["errors"], json!([]), "{value}");
    assert_eq!(
        fixture.calls("hdc-calls").len(),
        1,
        "all three inventory reads"
    );
    assert_eq!(
        fixture.calls("device-calls"),
        vec![
            json!(["aa", "dump", "-a"]),
            json!(["ps", "-A", "-o", "NAME"]),
            json!(["bm", "dump", "-a"])
        ]
    );
    assert!(!fixture.0.join("locks").exists());
}

#[test]
fn failed_commands_in_the_batch_remain_unavailable_instead_of_empty() {
    for mode in ["fail-aa", "fail-bm"] {
        let fixture = Fixture::new(mode);
        let value = fixture.run(mode);
        if mode == "fail-aa" {
            assert!(value["foreground"].is_null(), "{value}");
            assert_eq!(
                value["running_third_party"],
                json!(["com.example.game", "com.example.megacity"]),
                "{value}"
            );
            assert!(value["errors"].to_string().contains("exit 3"), "{value}");
        } else {
            assert_eq!(value["foreground"], "com.example.game", "{value}");
            assert!(value["running_third_party"].is_null(), "{value}");
            assert!(value["errors"].to_string().contains("exit 9"), "{value}");
        }
        assert_eq!(fixture.calls("hdc-calls").len(), 1);
    }
}

#[test]
fn a_stuck_batched_device_command_is_still_bounded_and_reaped() {
    let fixture = Fixture::new("hung");
    let started = Instant::now();
    let value = fixture.run("hung");
    assert!(
        started.elapsed() < std::time::Duration::from_secs(7),
        "{value}"
    );
    assert!(!value["errors"].as_array().unwrap().is_empty(), "{value}");
    assert!(value["running_third_party"].is_null(), "{value}");
    let pid = fs::read_to_string(fixture.0.join("hung-pid")).unwrap();
    let ps = Command::new("/bin/ps")
        .args(["-o", "stat=", "-p", pid.trim()])
        .output()
        .unwrap();
    let state = String::from_utf8_lossy(&ps.stdout);
    assert!(
        state.trim().is_empty() || state.contains('Z'),
        "device command survived timeout: {state}"
    );
    assert!(!fixture.0.join("locks").exists());
}
