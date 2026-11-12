//! Finite receiver-generated installation input and strict fresh remote verification.
use crate::confirmation_transport::{bounded, quote, sha};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, path::Path};

pub(super) const TEMPLATE: &str = include_str!("../loop_v1/tools/fixed_tool_bundle.sh");
pub(super) const FROZEN_NAMES: [&str; 3] =
    ["tools.manifest.json", "tools.install.sh", "tools.verify.sh"];
const FILE_LIMIT: u64 = 1024 * 1024;
const TOTAL_LIMIT: usize = 4 * 1024 * 1024;
const SCRIPT_LIMIT: usize = 8 * 1024 * 1024;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(super) struct Entry {
    pub name: String,
    pub bytes: u64,
    pub sha256: String,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(super) struct Manifest {
    pub schema: String,
    pub remote: String,
    pub token: String,
    pub boot_id: String,
    pub template_sha256: String,
    pub count: usize,
    pub total_bytes: usize,
    pub files: Vec<Entry>,
}
#[derive(Debug)]
pub(super) struct Bundle {
    pub manifest: Manifest,
    pub manifest_bytes: Vec<u8>,
    pub installer: Vec<u8>,
    pub verifier: Vec<u8>,
}
fn require(ok: bool, why: &str) -> Result<(), String> {
    if ok {
        Ok(())
    } else {
        Err(why.into())
    }
}
fn identity_word(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 128
        && s.bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"_-".contains(&c))
}
fn encoded(bytes: &[u8]) -> String {
    const ABC: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for b in bytes.chunks(3) {
        let bits = ((b[0] as u32) << 16)
            | ((b.get(1).copied().unwrap_or(0) as u32) << 8)
            | b.get(2).copied().unwrap_or(0) as u32;
        out.push(ABC[(bits >> 18) as usize] as char);
        out.push(ABC[((bits >> 12) & 63) as usize] as char);
        out.push(if b.len() > 1 {
            ABC[((bits >> 6) & 63) as usize] as char
        } else {
            '='
        });
        out.push(if b.len() > 2 {
            ABC[(bits & 63) as usize] as char
        } else {
            '='
        });
    }
    out
}
pub(super) fn names(
    control_only: bool,
    loaded: bool,
    capability: bool,
    controls: usize,
    queued: bool,
) -> Result<BTreeSet<String>, String> {
    require(
        (1..=32).contains(&controls) && (!control_only || controls == 1),
        "fixed tool control count invalid",
    )?;
    require(
        !capability || control_only && loaded,
        "capability fixed tools are control-only",
    )?;
    require(
        !queued || !control_only && !capability,
        "queued fixed tools require confirmation purpose",
    )?;
    let mut n = [
        "worker.sh",
        "bootstrap.sh",
        "snapshot.sh",
        "sample_env.sh",
        "restore.tsv",
        "candidate.plan.tsv",
        "ftrace-current_tracer",
        "ftrace-buffer_size_kb",
        "ftrace-set_event",
        "ftrace-tracing_on",
    ]
    .into_iter()
    .map(String::from)
    .collect::<BTreeSet<_>>();
    if !control_only {
        n.insert("workload.sh".into());
    }
    for i in 0..controls {
        n.insert(format!("probe_{i}.plan.tsv"));
    }
    if loaded {
        n.extend(["thermal_sample.sh", "thermal_sampler.sh"].map(String::from));
    }
    if capability {
        n.insert("thermal-purpose.txt".into());
    }
    if queued {
        n.extend(
            [
                "render_ownership.sh",
                "render_args.tsv",
                "render_reader.elf",
                "ftrace-trace_clock",
            ]
            .map(String::from),
        );
    }
    Ok(n)
}
pub(super) fn compile(
    dir: &Path,
    expected: &BTreeSet<String>,
    remote: &str,
    token: &str,
    boot: &str,
) -> Result<Bundle, String> {
    require(
        identity_word(token)
            && identity_word(boot)
            && remote == format!("/data/local/tmp/phonefarm-confirmation-{token}"),
        "fixed tool session identity invalid",
    )?;
    compile_owned(dir, expected, remote, token, boot)
}
fn compile_owned(
    dir: &Path,
    expected: &BTreeSet<String>,
    remote: &str,
    token: &str,
    boot: &str,
) -> Result<Bundle, String> {
    require(
        (11..=49).contains(&expected.len()),
        "fixed tool set size invalid",
    )?;
    let allowed = names(false, true, false, 32, true)?
        .union(&names(true, true, true, 1, false)?)
        .cloned()
        .collect::<BTreeSet<_>>();
    require(
        expected.is_subset(&allowed),
        "nonreceiver file in fixed tool set",
    )?;
    let mut data = Vec::new();
    let mut total = 0usize;
    for name in expected {
        let bytes = bounded(&dir.join(name), FILE_LIMIT)?;
        total = total
            .checked_add(bytes.len())
            .ok_or("fixed tool total overflow")?;
        require(total <= TOTAL_LIMIT, "fixed tool raw total exceeds bound")?;
        data.push((
            Entry {
                name: name.clone(),
                bytes: bytes.len() as u64,
                sha256: sha(&bytes),
            },
            bytes,
        ));
    }
    let manifest = Manifest {
        schema: "phonefarm/fixed_tool_bundle@1".into(),
        remote: remote.into(),
        token: token.into(),
        boot_id: boot.into(),
        template_sha256: sha(TEMPLATE.as_bytes()),
        count: data.len(),
        total_bytes: total,
        files: data.iter().map(|(e, _)| e.clone()).collect(),
    };
    let manifest_bytes = serde_json::to_vec_pretty(&manifest).map_err(|e| e.to_string())?;
    let digest = sha(&manifest_bytes);
    let header = |mode: &str| -> Result<String, String> {
        Ok(format!("#!/system/bin/sh\n#phonefarm_fixed_tools_{mode} v1\n#MANIFEST {}\nD={}\nTOKEN={}\nBOOT={}\nBUNDLE={}\n{}\n",
        serde_json::to_string(&manifest).map_err(|e| e.to_string())?, quote(remote), quote(token), quote(boot), quote(&digest), TEMPLATE))
    };
    let mut installer = header("install")?;
    installer.push_str("prepare_install\n");
    let mut verifier = header("verify")?;
    verifier.push_str("prepare_verify\n");
    for (e, bytes) in &data {
        installer.push_str(&format!(
            "write_fixed {} {} <<'PHONEFARM_FIXED_BUNDLE_DATA'\n",
            quote(&e.name),
            e.bytes
        ));
        for line in encoded(bytes).as_bytes().chunks(76) {
            installer.push_str(std::str::from_utf8(line).map_err(|e| e.to_string())?);
            installer.push('\n');
        }
        installer.push_str("PHONEFARM_FIXED_BUNDLE_DATA\n");
        verifier.push_str(&format!("verify_fixed {} {}\n", quote(&e.name), e.bytes));
    }
    if expected.contains("render_reader.elf") {
        installer.push_str("enable_reader\n");
        verifier.push_str("verify_reader\n");
    }
    installer.push_str("finish_install\n");
    verifier.push_str("finish_verify\n");
    require(
        installer.len() <= SCRIPT_LIMIT && verifier.len() <= SCRIPT_LIMIT,
        "fixed tool encoded input exceeds bound",
    )?;
    Ok(Bundle {
        manifest,
        manifest_bytes,
        installer: installer.into_bytes(),
        verifier: verifier.into_bytes(),
    })
}
pub(super) fn validate_frozen(
    dir: &Path,
    expected: &BTreeSet<String>,
    remote: &str,
    token: &str,
    boot: &str,
) -> Result<Bundle, String> {
    let expected = compile(dir, expected, remote, token, boot)?;
    let actual: Manifest =
        serde_json::from_slice(&bounded(&dir.join("tools.manifest.json"), 65536)?)
            .map_err(|e| e.to_string())?;
    require(
        actual == expected.manifest
            && bounded(&dir.join("tools.manifest.json"), 65536)? == expected.manifest_bytes
            && bounded(&dir.join("tools.install.sh"), SCRIPT_LIMIT as u64)? == expected.installer
            && bounded(&dir.join("tools.verify.sh"), SCRIPT_LIMIT as u64)? == expected.verifier,
        "fixed tools differ from receiver-reconstructed immutable bundle",
    )?;
    Ok(expected)
}
impl Bundle {
    pub(super) fn accept_install(&self, output: &[u8]) -> Result<(), String> {
        let m = &self.manifest;
        require(
            output
                == format!(
                    "INSTALL_COMPLETE\t{}\t{}\t{}\t{}\n",
                    m.token,
                    m.boot_id,
                    sha(&self.manifest_bytes),
                    m.count
                )
                .as_bytes(),
            "fixed tool installation completion unknown",
        )
    }
    pub(super) fn accept_verify(&self, output: &[u8]) -> Result<(), String> {
        let m = &self.manifest;
        let mut expected = format!(
            "#phonefarm_fixed_tools_verification v1\nOWNER\t{} {}\nBOOT\t{}\nBUNDLE\t{}\n",
            m.token,
            m.boot_id,
            m.boot_id,
            sha(&self.manifest_bytes)
        );
        for e in &m.files {
            expected += &format!("FILE\t{}\t{}\t{}\n", e.name, e.bytes, e.sha256);
        }
        expected += &format!("COUNT\t{}\n#end\n", m.count);
        require(
            output == expected.as_bytes(),
            "fixed tool actual full-set verification failed/unknown",
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::confirmation_transport::Transport;
    use std::{
        collections::BTreeMap,
        path::PathBuf,
        sync::atomic::{AtomicU64, Ordering},
        time::Duration,
    };
    static NEXT: AtomicU64 = AtomicU64::new(0);
    struct Fixture {
        root: PathBuf,
        input: PathBuf,
        remote: PathBuf,
        bundle: Bundle,
        transport: Transport,
    }
    impl Fixture {
        fn new() -> Self {
            Self::with_queued(false)
        }
        fn with_queued(queued: bool) -> Self {
            Self::with_profile(true, queued)
        }
        fn with_profile(loaded: bool, queued: bool) -> Self {
            let base = std::env::var_os("PHONEFARM_FIXED_TOOLS_TEST_ARTIFACT_DIR")
                .map(PathBuf::from)
                .unwrap_or_else(std::env::temp_dir);
            std::fs::create_dir_all(&base).unwrap();
            let root = base.join(format!(
                "fixed-tools-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir(&root).unwrap();
            let input = root.join("input");
            std::fs::create_dir(&input).unwrap();
            let remote = root.join("owned-remote");
            let files = names(false, loaded, false, 1, queued).unwrap();
            for name in &files {
                // Nontext bytes exercise the decoder without ever executing the uploaded file.
                let bytes = if name == "worker.sh" || name == "render_reader.elf" {
                    (0..=255).collect::<Vec<u8>>()
                } else {
                    format!("frozen {name}\n").into_bytes()
                };
                std::fs::write(input.join(name), bytes).unwrap();
            }
            let bundle = compile_owned(
                &input,
                &files,
                remote.to_str().unwrap(),
                "owned_token",
                "fake-boot",
            )
            .unwrap();
            std::fs::write(root.join("expected.manifest.json"), &bundle.manifest_bytes).unwrap();
            std::fs::write(root.join("expected.install.sh"), &bundle.installer).unwrap();
            std::fs::write(root.join("expected.verify.sh"), &bundle.verifier).unwrap();
            let program = root.join("host-shell");
            let script = b"#!/bin/sh\nexec /bin/sh -s\n";
            std::fs::write(&program, script).unwrap();
            Self {
                transport: Transport {
                    program,
                    target: "host-fixture".into(),
                    origin: "synthetic".into(),
                    expected_sha: sha(script),
                    dir: root.clone(),
                    sequence: 0,
                    input_hashes: BTreeMap::new(),
                },
                root,
                input,
                remote,
                bundle,
            }
        }
        fn run(
            &mut self,
            bytes: &[u8],
            timeout: Duration,
        ) -> Result<crate::confirmation_transport::Receipt, String> {
            // Only /proc boot identity is mapped. All file/decoder/hash/size/template logic is real host shell.
            let mut script = b"cat() { if [ \"$1\" = /proc/sys/kernel/random/boot_id ]; then printf 'fake-boot\\n'; else command cat \"$@\"; fi; }\n".to_vec();
            script.extend_from_slice(bytes);
            let path = self.root.join(format!("input-{}", self.transport.sequence));
            std::fs::write(&path, &script).unwrap();
            self.transport
                .input_hashes
                .insert(path.clone(), sha(&script));
            self.transport.run(
                "fixed-tools-template",
                &[],
                Some(&path),
                timeout,
                65536,
                None,
            )
        }
        fn install(&mut self) {
            let installer = self.bundle.installer.clone();
            let r = self.run(&installer, Duration::from_secs(15)).unwrap();
            assert_eq!(r.code, 0, "{}", String::from_utf8_lossy(&r.stderr));
            self.bundle.accept_install(&r.stdout).unwrap();
        }
        fn verify(&mut self) -> crate::confirmation_transport::Receipt {
            let verifier = self.bundle.verifier.clone();
            self.run(&verifier, Duration::from_secs(10)).unwrap()
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            if std::env::var_os("PHONEFARM_FIXED_TOOLS_TEST_ARTIFACT_DIR").is_none() {
                let _ = std::fs::remove_dir_all(&self.root);
            }
        }
    }

    #[test]
    fn actual_host_template_installs_exact_bytes_then_checks_complete_actual_set() {
        let mut f = Fixture::new();
        f.install();
        for e in &f.bundle.manifest.files {
            assert_eq!(
                std::fs::read(f.remote.join(&e.name)).unwrap(),
                std::fs::read(f.input.join(&e.name)).unwrap()
            );
        }
        let r = f.verify();
        assert_eq!(r.code, 0);
        f.bundle.accept_verify(&r.stdout).unwrap();
        assert_eq!(f.bundle.manifest.count, 14);
        assert_eq!(f.transport.sequence, 2);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(&f.remote).unwrap().permissions().mode() & 0o777,
                0o700
            );
        }
    }
    #[test]
    fn queued_reader_binary_mode_is_installed_then_freshly_verified_without_repair() {
        use std::os::unix::fs::PermissionsExt;
        for loaded in [false, true] {
            let mut f = Fixture::with_profile(loaded, true);
            f.install();
            let reader = f.remote.join("render_reader.elf");
            assert_eq!(
                std::fs::read(&reader).unwrap(),
                (0..=255).collect::<Vec<u8>>()
            );
            assert_eq!(
                std::fs::metadata(&reader).unwrap().permissions().mode() & 0o777,
                0o700
            );
            assert_eq!(f.bundle.manifest.count, if loaded { 18 } else { 16 });
            let receipt = f.verify();
            assert_eq!(receipt.code, 0);
            f.bundle.accept_verify(&receipt.stdout).unwrap();
            assert_eq!(f.transport.sequence, 2);
            std::fs::set_permissions(&reader, std::fs::Permissions::from_mode(0o600)).unwrap();
            let receipt = f.verify();
            assert_eq!(receipt.code, 93);
            assert!(f.bundle.accept_verify(&receipt.stdout).is_err());
            assert_eq!(
                std::fs::metadata(&reader).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
    }
    #[test]
    fn fresh_actual_verifier_rejects_changed_missing_symlink_and_fifo() {
        let mut f = Fixture::new();
        f.install();
        let file = f.remote.join("worker.sh");
        let original = std::fs::read(&file).unwrap();
        let mut changed = original.clone();
        changed[0] ^= 1;
        std::fs::write(&file, &changed).unwrap();
        let r = f.verify();
        assert_eq!(r.code, 0);
        assert!(f.bundle.accept_verify(&r.stdout).is_err());
        std::fs::write(&file, b"short").unwrap();
        let r = f.verify();
        assert_ne!(r.code, 0);
        assert!(f.bundle.accept_verify(&r.stdout).is_err());
        std::fs::remove_file(&file).unwrap();
        let r = f.verify();
        assert_ne!(r.code, 0);
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(f.input.join("worker.sh"), &file).unwrap();
            let r = f.verify();
            assert_ne!(r.code, 0);
            std::fs::remove_file(&file).unwrap();
            let c = std::ffi::CString::new(file.to_str().unwrap()).unwrap();
            assert_eq!(unsafe { libc::mkfifo(c.as_ptr(), 0o600) }, 0);
            let r = f.verify();
            assert_ne!(r.code, 0);
        }
    }
    #[test]
    fn owner_boot_and_permanent_closed_session_block_real_template() {
        let mut f = Fixture::new();
        f.install();
        let owner = f.remote.join("owner");
        std::fs::write(&owner, b"other_owner fake-boot\n").unwrap();
        let r = f.verify();
        assert_ne!(r.code, 0);
        std::fs::write(&owner, b"owned_token fake-boot\n\n").unwrap();
        let r = f.verify();
        assert_ne!(r.code, 0);
        std::fs::write(&owner, b"owned_token fake-boot\n").unwrap();
        let script = String::from_utf8(f.bundle.verifier.clone())
            .unwrap()
            .replace("BOOT='fake-boot'", "BOOT='changed-boot'");
        let r = f.run(script.as_bytes(), Duration::from_secs(10)).unwrap();
        assert_ne!(r.code, 0);
        std::fs::write(f.remote.join("session.closed"), b"permanently revoked\n").unwrap();
        let r = f.verify();
        assert_eq!(r.code, 86);
        let installer = f.bundle.installer.clone();
        let r = f.run(&installer, Duration::from_secs(10)).unwrap();
        assert_ne!(r.code, 0);
        assert_eq!(
            std::fs::read(f.remote.join("session.closed")).unwrap(),
            b"permanently revoked\n"
        );
    }
    #[test]
    fn strict_receipt_rejects_partial_extra_reordered_wrong_count_and_identity() {
        let mut f = Fixture::new();
        f.install();
        let r = f.verify();
        f.bundle.accept_verify(&r.stdout).unwrap();
        let original = String::from_utf8(r.stdout).unwrap();
        for changed in [
            original.replace("#end\n", ""),
            original.clone() + "EXTRA\n",
            original.replace("COUNT\t14", "COUNT\t13"),
            original.replace("OWNER\towned_token", "OWNER\tunrelated"),
            original.replace("BOOT\tfake-boot", "BOOT\tother-boot"),
            original.replace("FILE\tbootstrap.sh", "FILE\tunknown.sh"),
        ] {
            assert!(f.bundle.accept_verify(changed.as_bytes()).is_err());
        }
        let mut rows = original.lines().collect::<Vec<_>>();
        rows.swap(4, 5);
        assert!(f
            .bundle
            .accept_verify((rows.join("\n") + "\n").as_bytes())
            .is_err());
    }
    #[test]
    fn corrupted_payload_and_timeout_never_complete_installation_or_verification() {
        let mut f = Fixture::new();
        let script = String::from_utf8(f.bundle.installer.clone())
            .unwrap()
            .replacen(
                "<<'PHONEFARM_FIXED_BUNDLE_DATA'\n",
                "<<'PHONEFARM_FIXED_BUNDLE_DATA'\n!invalid!\n",
                1,
            );
        let r = f.run(script.as_bytes(), Duration::from_secs(10)).unwrap();
        assert_ne!(r.code, 0);
        assert!(f.bundle.accept_install(&r.stdout).is_err());
        assert!(f.remote.join("owner").exists());
        let r = f.verify();
        assert_ne!(r.code, 0);
        assert!(f.bundle.accept_verify(&r.stdout).is_err());
        let mut f = Fixture::new();
        let mut script = b"base64() { sleep 10; command base64 \"$@\"; }\n".to_vec();
        script.extend_from_slice(&f.bundle.installer);
        let r = f.run(&script, Duration::from_millis(100));
        assert!(r.is_err());
        let r = f.verify();
        assert_ne!(r.code, 0);
    }
    #[test]
    fn only_registered_names_and_bounded_regular_source_files_can_compile() {
        let f = Fixture::new();
        let mut n = names(false, true, false, 1, false).unwrap();
        n.insert("arbitrary.bin".into());
        assert!(
            compile_owned(&f.input, &n, f.remote.to_str().unwrap(), "t", "b")
                .unwrap_err()
                .contains("nonreceiver")
        );
        assert!(names(false, true, true, 1, false).is_err());
        assert!(names(true, true, false, 2, false).is_err());
        assert!(names(false, false, false, 1, true).is_ok());
        assert!(names(true, true, false, 1, true).is_err());
        assert!(names(true, true, true, 1, true).is_err());
        let n = names(false, true, false, 1, false).unwrap();
        assert!(compile(&f.input, &n, "/other/path", "t", "b").is_err());
        let file = f.input.join("worker.sh");
        std::fs::write(&file, vec![b'x'; FILE_LIMIT as usize + 1]).unwrap();
        assert!(compile_owned(&f.input, &n, f.remote.to_str().unwrap(), "t", "b").is_err());
    }
}
