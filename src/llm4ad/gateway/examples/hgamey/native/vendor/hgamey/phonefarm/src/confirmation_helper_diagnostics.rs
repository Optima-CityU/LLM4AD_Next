//! Read-only recovery evidence; it never grants idle/restore/qualification.
use super::*;
const TEMPLATE: &str = include_str!("../loop_v1/tools/helper_recovery_diagnostics.sh");
const OUTPUT_CAP: u64 = 2 * 1024 * 1024;

pub(super) fn helper_only(dir: &Path) -> Result<(Request, Value), String> {
    let req: Request =
        serde_json::from_slice(&bounded(&dir.join("request.json"), 2 * 1024 * 1024)?)
            .map_err(|e| e.to_string())?;
    let state = validate_preinstall(dir, &req)?;
    validate_helper_intent(dir, &state)?;
    require(
        ["main-install.intent.json","inputs.manifest.json"].iter().all(|n|
            matches!(fs::symlink_metadata(dir.join(n)),Err(e) if e.kind()==std::io::ErrorKind::NotFound)),
        "helper diagnostic forbidden after main phase",
    )?;
    for line in bounded(&dir.join("journal.jsonl"), 8 * 1024 * 1024)?
        .split(|b| *b == b'\n')
        .filter(|l| !l.is_empty())
    {
        let event: Value = serde_json::from_slice(line).map_err(|e| e.to_string())?;
        require(
            !matches!(
                event["event"].as_str(),
                Some("remote_install_intent" | "sample_intent")
            ) && !matches!(
                event["operation"].as_str(),
                Some("worker_probe" | "worker_sample" | "worker_control_probe")
            ),
            "helper diagnostic forbidden after main intent",
        )?;
    }
    Ok((req, state))
}

/// Marker+exit are necessary, but this operation never establishes quiescence.
fn raw_complete(bytes: &[u8], code: i32, expected_owner: &str) -> bool {
    if code != 0 || bytes.len() as u64 > OUTPUT_CAP || bytes.contains(&0) {
        return false;
    }
    fn line<'a>(rest: &mut &'a [u8]) -> Option<&'a str> {
        let end = rest.iter().position(|b| *b == b'\n')?;
        let value = std::str::from_utf8(&rest[..end]).ok()?;
        *rest = &rest[end + 1..];
        Some(value)
    }
    let mut rest = bytes;
    if line(&mut rest) != Some("#phonefarm_helper_diagnostic@1") {
        return false;
    }
    let mut labels = BTreeSet::new();
    let mut scan = 0;
    let mut selected = BTreeSet::new();
    let mut ended = false;
    while !rest.is_empty() {
        let Some(row) = line(&mut rest) else {
            return false;
        };
        let cols = row.split('\t').collect::<Vec<_>>();
        match cols.as_slice() {
            ["BEGIN", label, size] => {
                let Ok(size) = size.parse::<usize>() else {
                    return false;
                };
                if size
                    > (if *label == "reader_log" {
                        524288
                    } else {
                        131072
                    })
                    || !labels.insert(*label)
                    || rest.len() <= size
                {
                    return false;
                }
                if *label == "owner" && &rest[..size] != expected_owner.as_bytes() {
                    return false;
                }
                rest = &rest[size..];
                if rest.first() != Some(&b'\n') {
                    return false;
                }
                rest = &rest[1..];
                if line(&mut rest) != Some(format!("END\t{label}").as_str()) {
                    return false;
                }
            }
            ["MISSING", label] | ["UNKNOWN", label, _] => {
                if *label == "owner" {
                    return false;
                }
                if !labels.insert(*label) {
                    return false;
                }
            }
            ["SELECTION", tag, "registered", pid]
                if ["master", "child"].contains(tag)
                    && pid
                        .parse::<u32>()
                        .is_ok_and(|n| n > 0 && n <= i32::MAX as u32) =>
            {
                if !selected.insert(*tag) {
                    return false;
                }
            }
            ["SELECTION", tag, "UNKNOWN"] if ["master", "child"].contains(tag) => {
                if !selected.insert(*tag) {
                    return false;
                }
            }
            ["CMDLINE_BASE64", pid, value]
                if pid.parse::<u32>().is_ok_and(|n| n > 0)
                    && value.len() <= 8192
                    && value
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"+/=".contains(&b)) => {}
            ["GROUP_MEMBER", pid, pgid, ticks, _]
                if [pid, pgid, ticks]
                    .iter()
                    .all(|s| s.parse::<u64>().is_ok_and(|n| n > 0)) => {}
            ["SCAN", total, matched]
                if total.parse::<usize>().is_ok_and(|n| n <= 2048)
                    && matched.parse::<usize>().is_ok_and(|n| n <= 256) =>
            {
                scan += 1;
            }
            ["#phonefarm_helper_diagnostic_end@1"] if rest.is_empty() => {
                ended = true;
            }
            _ => return false,
        }
    }
    ended
        && selected.len() == 2
        && scan == 1
        && [
            "owner",
            "closed",
            "lock_owner",
            "master",
            "children",
            "pending",
            "permit",
            "reader_log",
            "reader_stderr",
            "reader_exit",
            "quiescence",
            "journal",
        ]
        .iter()
        .all(|label| labels.contains(label))
}

pub(super) fn diagnose(dir: &Path, execute: bool) -> Result<Value, String> {
    require(
        execute,
        "read-only recovery diagnostic still requires --execute and recovery lease",
    )?;
    let dir = dir.canonicalize().map_err(|e| e.to_string())?;
    let (req, state) = helper_only(&dir)?;
    let (root, legacy, timeout) = roots()?;
    let lease = Lease::acquire_recovery(&root, &legacy, &req.transport.target, &dir, timeout)?;
    let phase = format!(
        "helper_diagnostic_{}",
        chrono::Utc::now()
            .timestamp_nanos_opt()
            .ok_or("time overflow")?
    );
    let out = dir.join(&phase);
    fs::create_dir(&out).map_err(|e| e.to_string())?;
    save(&out, "template.sh", TEMPLATE.as_bytes())?;
    save_json(
        &out,
        "intent.json",
        &json!({"schema":"phonefarm/helper_recovery_diagnostic_intent@1",
        "state_sha256":file_ref(&dir,"state.json")?["sha256"],"preinstall_sha256":file_ref(&dir,"render-preinstall.manifest.json")?["sha256"],
        "helper_intent_sha256":file_ref(&dir,"render-helper.intent.json")?["sha256"],"template_sha256":sha(TEMPLATE.as_bytes()),
        "phase":phase,"remote":state["helper_remote"],"token":state["token"],"boot_id":state["boot_id"],
        "read_only":true,"remote_worker_executed":false,"stop_restore_delete_allowed":false}),
    )?;
    journal(
        &dir,
        json!({"event":"helper_diagnostic_intent","phase":phase,"at":now()}),
    )?;
    let mut t = start_transport(&dir, &req, &format!("{phase}_transport"))?;
    register_preinstall(&mut t, &dir)?;
    t.input_hashes
        .insert(out.join("template.sh"), sha(TEMPLATE.as_bytes()));
    let cmd = format!(
        "timeout 20 sh -s -- {} {} {}",
        quote(state["helper_remote"].as_str().ok_or("helper remote")?),
        quote(state["token"].as_str().ok_or("helper token")?),
        quote(state["boot_id"].as_str().ok_or("helper boot")?)
    );
    let receipt = t.shell(
        "helper_readonly_diagnostic",
        &cmd,
        Some(&out.join("template.sh")),
        30,
        OUTPUT_CAP,
        Some(&lease),
    );
    let (complete, code, error, raw) = match receipt {
        Ok(r) => {
            let complete = raw_complete(
                &r.stdout,
                r.code,
                &format!(
                    "{} {}\n",
                    state["token"].as_str().unwrap(),
                    state["boot_id"].as_str().unwrap()
                ),
            );
            save(&out, "raw.stdout", &r.stdout)?;
            save(&out, "raw.stderr", &r.stderr)?;
            (
                complete,
                Some(r.code),
                None,
                Some(
                    json!({"stdout":file_ref(&out,"raw.stdout")?,"stderr":file_ref(&out,"raw.stderr")?}),
                ),
            )
        }
        Err(e) => (false, None, Some(e), None),
    };
    // Recovery lease Drop preserves its durable marker. Never complete_recovery.
    let result = json!({"schema":"phonefarm/helper_recovery_diagnostic@1","status":"UNKNOWN",
        "diagnostic_complete":complete,"exit_code":code,"error":error,"session":dir,"diagnostic_dir":out,
        "evidence_origin":if req.transport.kind=="fake"{"synthetic"}else{"receiver_read_only"},"raw":raw,
        "eligible":false,"recovery_completed":false,"remote_idle_verified":false,"control_restore_claimed":false,
        "remote_worker_executed":false,"promotion_allowed":false,"real_game_optimization_proven":false,
        "recovery":"Barrier retained. Raw endpoint evidence is not a stop/idle proof; device owner must review exact token/boot/epochs/groups before explicit existing reconciliation. No automatic retry or cleanup."});
    save_json(&out, "result.json", &result)?;
    journal(
        &dir,
        json!({"event":"helper_diagnostic_recorded","phase":phase,"diagnostic_complete":complete,"at":now()}),
    )?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;
    static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    struct Fixture {
        root: PathBuf,
        owned: PathBuf,
        proc: PathBuf,
        script: PathBuf,
    }
    impl Fixture {
        fn new() -> Self {
            let root = std::env::var_os("PHONEFARM_HELPER_DIAGNOSTIC_TEST_ARTIFACT_DIR")
                .map(PathBuf::from)
                .unwrap_or_else(std::env::temp_dir)
                .join(format!(
                    "helper-diag-{}-{}",
                    std::process::id(),
                    NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
                ));
            fs::create_dir(&root).unwrap();
            let root = root.canonicalize().unwrap();
            let owned = root.join("phonefarm-confirmation-session_test-reader");
            let proc = root.join("proc");
            fs::create_dir_all(owned.join("render_initial")).unwrap();
            fs::create_dir_all(owned.join("worker.lock")).unwrap();
            fs::create_dir_all(proc.join("sys/kernel/random")).unwrap();
            fs::write(owned.join("owner"), "session_test boot-test\n").unwrap();
            fs::write(proc.join("sys/kernel/random/boot_id"), "boot-test\n").unwrap();
            fs::write(
                owned.join("worker.lock/owner"),
                "session_test boot-test 100 123 render_initial\n",
            )
            .unwrap();
            fs::write(
                owned.join("render_initial/master.tsv"),
                "session_test boot-test 100 123 render_initial\n",
            )
            .unwrap();
            fs::write(
                owned.join("render_initial/children.tsv"),
                format!(
                    "render_reader\t200\t456\t{}/render_ownership.sh\n",
                    owned.display()
                ),
            )
            .unwrap();
            let script = root.join("diagnostic.sh");
            let s = TEMPLATE
                .replace("/data/local/tmp", root.to_str().unwrap())
                .replace("/proc", proc.to_str().unwrap());
            fs::write(&script, s).unwrap();
            Self {
                root,
                owned,
                proc,
                script,
            }
        }
        fn process(&self, pid: u32, group: u32, ticks: u32) {
            fs::create_dir(self.proc.join(pid.to_string())).unwrap();
            let stat = format!(
                "{pid} (sh with spaces) S 1 {group} 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 {ticks} 0\n"
            );
            fs::write(self.proc.join(format!("{pid}/stat")), stat).unwrap();
            fs::write(
                self.proc.join(format!("{pid}/status")),
                format!("Name:\tsh\nPid:\t{pid}\n"),
            )
            .unwrap();
            fs::write(
                self.proc.join(format!("{pid}/cmdline")),
                b"sh\0render_ownership.sh\0",
            )
            .unwrap();
        }
        fn run(&self) -> std::process::Output {
            let result = Command::new("sh")
                .arg(&self.script)
                .arg(&self.owned)
                .args(["session_test", "boot-test"])
                .output()
                .unwrap();
            if std::env::var_os("PHONEFARM_HELPER_DIAGNOSTIC_TEST_ARTIFACT_DIR").is_some() {
                let id = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                fs::write(self.root.join(format!("run-{id}.stdout")), &result.stdout).unwrap();
                fs::write(self.root.join(format!("run-{id}.stderr")), &result.stderr).unwrap();
                fs::write(self.root.join(format!("run-{id}.json")),json!({"schema":"hgamey/helper_diagnostic_shell_fixture@1","argv":["sh",self.script.to_str().unwrap(),self.owned.to_str().unwrap(),"session_test","boot-test"],"exit":result.status.code(),"template_sha256":sha(TEMPLATE.as_bytes()),"mapped_source_sha256":sha(&fs::read(&self.script).unwrap()),"scope":"actual host shell with synthetic proc metadata; not Android process proof","device_calls":0,"model_api_calls":0}).to_string()).unwrap();
            }
            result
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            if std::env::var_os("PHONEFARM_HELPER_DIAGNOSTIC_TEST_ARTIFACT_DIR").is_none() {
                let _ = fs::remove_dir_all(&self.root);
            }
        }
    }
    #[test]
    fn actual_template_live_dead_and_reused_epoch_remain_diagnostics() {
        let f = Fixture::new();
        f.process(100, 100, 123);
        f.process(200, 200, 999);
        f.process(201, 200, 457);
        let r = f.run();
        assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
        assert!(raw_complete(&r.stdout, 0, "session_test boot-test\n"));
        let s = String::from_utf8(r.stdout).unwrap();
        assert!(s.contains("GROUP_MEMBER\t201\t200\t457"));
        assert!(s.contains("200 (sh with spaces)"));
        fs::remove_dir_all(f.proc.join("100")).unwrap();
        let r = f.run();
        assert!(raw_complete(&r.stdout, 0, "session_test boot-test\n"));
        assert!(String::from_utf8(r.stdout)
            .unwrap()
            .contains("MISSING\tproc_100.stat"));
    }
    #[test]
    fn actual_template_unknown_ledger_and_unreadable_proc_are_visible() {
        let f = Fixture::new();
        fs::write(f.owned.join("render_initial/children.tsv"), "partial").unwrap();
        f.process(200, 200, 456);
        fs::write(f.proc.join("200/stat"), "malformed\n").unwrap();
        let r = f.run();
        assert!(r.status.success());
        assert!(raw_complete(&r.stdout, 0, "session_test boot-test\n"));
        assert!(String::from_utf8(r.stdout)
            .unwrap()
            .contains("UNKNOWN\tprocess_scan\t200"));
    }
    #[test]
    fn actual_template_boot_owner_links_and_oversize_fail_closed() {
        let f = Fixture::new();
        fs::write(f.proc.join("sys/kernel/random/boot_id"), "different\n").unwrap();
        assert_eq!(f.run().status.code(), Some(72));
        fs::write(f.proc.join("sys/kernel/random/boot_id"), "boot-test\n").unwrap();
        fs::write(f.owned.join("owner"), "wrong boot-test\n").unwrap();
        assert_eq!(f.run().status.code(), Some(71));
        fs::write(f.owned.join("owner"), "session_test boot-test\n").unwrap();
        fs::write(
            f.owned.join("render_initial/render_reader.log"),
            vec![b'x'; 524289],
        )
        .unwrap();
        let r = f.run();
        assert!(raw_complete(&r.stdout, 0, "session_test boot-test\n"));
        assert!(String::from_utf8(r.stdout)
            .unwrap()
            .contains("UNKNOWN\treader_log\toversize"));
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(
                f.owned.join("owner"),
                f.owned.join("render_initial/quiescence.txt"),
            )
            .unwrap();
            let r = f.run();
            assert!(String::from_utf8(r.stdout)
                .unwrap()
                .contains("UNKNOWN\tquiescence\ttype"));
        }
    }
    #[test]
    fn actual_template_caps_group_members_and_total_process_inventory() {
        let f = Fixture::new();
        for pid in 200..457 {
            f.process(pid, 200, pid + 100);
        }
        let r = f.run();
        assert_eq!(r.status.code(), Some(94));
        assert!(!raw_complete(&r.stdout, 94, "session_test boot-test\n"));
        let f = Fixture::new();
        for pid in 1..2050 {
            fs::create_dir(f.proc.join(pid.to_string())).unwrap();
        }
        let r = f.run();
        assert_eq!(r.status.code(), Some(94));
        assert!(!raw_complete(&r.stdout, 94, "session_test boot-test\n"));
    }
    #[test]
    fn actual_template_symlinked_owned_parent_cannot_read_outside_session() {
        #[cfg(unix)]
        {
            let f = Fixture::new();
            fs::remove_dir(f.owned.join("worker.lock")).unwrap_err();
            fs::remove_dir_all(f.owned.join("worker.lock")).unwrap();
            std::os::unix::fs::symlink(&f.root, f.owned.join("worker.lock")).unwrap();
            let r = f.run();
            assert_eq!(r.status.code(), Some(71));
            assert!(r.stdout.is_empty());
        }
    }
    #[test]
    fn partial_markers_nonzero_and_invalid_wire_do_not_complete() {
        let f = Fixture::new();
        let r = f.run();
        assert!(raw_complete(&r.stdout, 0, "session_test boot-test\n"));
        assert!(!raw_complete(&r.stdout, 1, "session_test boot-test\n"));
        assert!(!raw_complete(
            &r.stdout[..r.stdout.len() - 1],
            0,
            "session_test boot-test\n"
        ));
        let mut bytes = r.stdout;
        bytes.splice(0..0, b"unexpected\n".iter().copied());
        assert!(!raw_complete(&bytes, 0, "session_test boot-test\n"));
    }
}
