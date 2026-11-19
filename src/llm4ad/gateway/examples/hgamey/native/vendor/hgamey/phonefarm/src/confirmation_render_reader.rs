//! Fixed-source local native artifact. Integrity/provenance is not authentication.
use super::*;
use std::io::Read;
const SOURCE: &[u8] = include_bytes!("../tools/render-collector/reader.c");
pub(super) const CLOSE: &str = include_str!("../loop_v1/tools/confirmation_render_close.sh");
pub(super) const FILES: [&str; 5] = [
    "render_reader.source.c",
    "render_reader.elf",
    "render_reader.build.json",
    "render_reader.compiler.stdout",
    "render_reader.compiler.stderr",
];
const ARTIFACT_FILES: [&str; 5] = [
    "source.c",
    "reader.elf",
    "build.json",
    "compiler.stdout",
    "compiler.stderr",
];
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Compiler {
    path: String,
    sha256: String,
    clang_path: String,
    clang_sha256: String,
    package_sha256: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Execution {
    exit: i32,
    stdout_sha256: String,
    stderr_sha256: String,
    process_group_absent: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Build {
    schema: String,
    status: String,
    target: String,
    ndk_revision: String,
    flags: Vec<String>,
    source_sha256: String,
    elf_sha256: String,
    compiler: Compiler,
    execution: Execution,
    source_authentication: String,
    device_run: bool,
    installed_kernel_source_verified: bool,
    game_proof: bool,
}
fn digest(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn validate(b: &[Vec<u8>]) -> Result<(), String> {
    let r: Build = serde_json::from_slice(&b[2]).map_err(|e| e.to_string())?;
    require(
        b[0] == SOURCE
            && r.schema == "phonefarm/render_reader_build@1"
            && r.status == "BUILT"
            && r.target == "aarch64-linux-android30"
            && r.ndk_revision == "28.2.13676358"
            && r.flags
                == [
                    "-std=c11",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fPIE",
                    "-pie",
                    "-Wl,-z,relro,-z,now",
                ]
            && r.source_sha256 == sha(&b[0])
            && r.elf_sha256 == sha(&b[1])
            && r.execution.exit == 0
            && r.execution.stdout_sha256 == sha(&b[3])
            && r.execution.stderr_sha256 == sha(&b[4])
            && r.execution.process_group_absent
            && r.source_authentication == "local_build_receipt_not_independent_authentication"
            && !r.device_run
            && !r.installed_kernel_source_verified
            && !r.game_proof,
        "native reader source/build/receipt contract mismatch",
    )?;
    require(
        [
            &r.compiler.sha256,
            &r.compiler.clang_sha256,
            &r.compiler.package_sha256,
        ]
        .iter()
        .all(|s| digest(s))
            && Path::new(&r.compiler.path).is_absolute()
            && Path::new(&r.compiler.clang_path).is_absolute(),
        "native reader compiler provenance missing",
    )?;
    let e = &b[1];
    require(
        e.len() >= 64
            && &e[..6] == b"\x7fELF\x02\x01"
            && u16::from_le_bytes([e[16], e[17]]) == 3
            && u16::from_le_bytes([e[18], e[19]]) == 183,
        "native reader not ELF64LE AArch64 PIE",
    )
}
#[cfg(unix)]
pub(super) fn artifact(path: &Path) -> Result<Vec<Vec<u8>>, String> {
    use std::ffi::CString;
    use std::os::unix::{
        ffi::OsStrExt,
        io::{AsRawFd, FromRawFd},
    };
    let absolute = if path.is_absolute() {
        path.to_owned()
    } else {
        std::env::current_dir()
            .map_err(|e| e.to_string())?
            .join(path)
    };
    let mut dir = std::fs::File::open("/").map_err(|e| e.to_string())?;
    for c in absolute.components() {
        match c {
            std::path::Component::RootDir => {}
            std::path::Component::Normal(n) => {
                let n = CString::new(n.as_bytes()).map_err(|_| "artifact path NUL")?;
                let fd = unsafe {
                    libc::openat(
                        dir.as_raw_fd(),
                        n.as_ptr(),
                        libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                    )
                };
                require(
                    fd >= 0,
                    "native reader artifact path contains unreadable/symlink component",
                )?;
                dir = unsafe { std::fs::File::from_raw_fd(fd) };
            }
            _ => return Err("native reader artifact path escape".into()),
        }
    }
    let copy = unsafe { libc::dup(dir.as_raw_fd()) };
    require(copy >= 0, "artifact directory dup")?;
    let ds = unsafe { libc::fdopendir(copy) };
    if ds.is_null() {
        unsafe { libc::close(copy) };
        return Err("artifact directory inventory".into());
    }
    let mut names = BTreeSet::new();
    let error = loop {
        #[cfg(target_os = "macos")]
        unsafe {
            *libc::__error() = 0;
        }
        #[cfg(not(target_os = "macos"))]
        unsafe {
            *libc::__errno_location() = 0;
        }
        let ent = unsafe { libc::readdir(ds) };
        if ent.is_null() {
            break std::io::Error::last_os_error().raw_os_error() != Some(0);
        }
        let name = unsafe { std::ffi::CStr::from_ptr((*ent).d_name.as_ptr()) }
            .to_str()
            .map_err(|_| "artifact filename UTF8");
        match name {
            Ok("." | "..") => {}
            Ok(n) => {
                names.insert(n.to_owned());
            }
            Err(_) => {
                break true;
            }
        }
        if names.len() > 5 {
            break true;
        }
    };
    require(
        unsafe { libc::closedir(ds) } == 0,
        "artifact directory close failed",
    )?;
    require(
        !error && names == ARTIFACT_FILES.into_iter().map(String::from).collect(),
        "native reader artifact incomplete/extra inventory",
    )?;
    let mut result = vec![];
    for (i, name) in ARTIFACT_FILES.iter().enumerate() {
        let n = CString::new(*name).unwrap();
        let fd = unsafe {
            libc::openat(
                dir.as_raw_fd(),
                n.as_ptr(),
                libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC,
            )
        };
        require(fd >= 0, "native reader artifact file missing/symlink")?;
        let f = unsafe { std::fs::File::from_raw_fd(fd) };
        let cap = if i == 1 { 1024 * 1024 } else { 128 * 1024 };
        let m = f.metadata().map_err(|e| e.to_string())?;
        require(
            m.is_file() && m.len() <= cap,
            "native reader artifact file bound/type",
        )?;
        let mut b = vec![];
        f.take(cap + 1)
            .read_to_end(&mut b)
            .map_err(|e| e.to_string())?;
        require(b.len() as u64 <= cap, "native reader artifact grew")?;
        result.push(b);
    }
    validate(&result)?;
    Ok(result)
}
#[cfg(not(unix))]
pub(super) fn artifact(_: &Path) -> Result<Vec<Vec<u8>>, String> {
    Err("native reader requires nofollow directory capability".into())
}
pub(super) fn snapshot(path: &Path, dir: &Path) -> Result<(), String> {
    let b = artifact(path)?;
    for (n, b) in FILES.iter().zip(b) {
        save(dir, n, &b)?;
    }
    save(dir, "render_close.sh", CLOSE.as_bytes())?;
    Ok(())
}
pub(super) fn verify(dir: &Path) -> Result<(), String> {
    let b = FILES
        .iter()
        .map(|n| bounded(&dir.join(n), 1024 * 1024))
        .collect::<Result<Vec<_>, _>>()?;
    validate(&b)?;
    require(
        bounded(&dir.join("render_close.sh"), 65536)? == CLOSE.as_bytes(),
        "helper closure tool changed",
    )
}
