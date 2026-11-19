//! All device entry points use the fd lease shared with tools/devlock run.
use std::os::unix::process::ExitStatusExt;
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
pub const DEFAULT_LABEL: &str = "phonefarm";
/// 会碰设备、要拿锁的子命令。
const LOCKED: [&str; 24] = [
    "run",
    "onboard",
    "benchmark",
    "parallel",
    "script",
    "bench",
    "gray-ab",
    "gray-layer",
    "autoloop",
    "eval",
    "gpu-op",
    "perf",
    "capture",
    "collect",
    "test-batch",
    "cts-fetch",
    "quest",
    "probe",
    "exec",
    "experiment",
    "selftest",
    "frame-audit",
    "preflight",
    "frame-sources",
];

/// 从参数里剥下来的锁选项。
#[derive(Debug, Clone, PartialEq)]
pub struct LockOpts {
    pub label: Option<String>,
    pub wait: Option<Duration>,
}

/// 这一条命令要不要拿锁。
///
/// 只问用法 (`--help`) 的、以及评测入口拿着一个**不存在**的请求路径来的 (上层探活
/// `eval --request /nonexistent/...` 就是这么问"认不认这个参数"的, 它在读文件那一步
/// 就退出, 碰不到设备) 都不拿 —— 否则别人持锁时连探活都要排队。
pub fn needs_lock(sub: &str, rest: &[String]) -> bool {
    if sub == "game-deploy" {
        return crate::game_deploy::needs_queue(rest).unwrap_or(false);
    }
    if !LOCKED.contains(&sub) {
        return false;
    }
    if rest.iter().any(|a| a == "-h" || a == "--help") {
        return false;
    }
    // Recovery reads the saved serial/context and acquires that scope itself.
    if matches!(sub, "bench" | "capture") && rest.iter().any(|a| a == "--recover") {
        return false;
    }
    if sub == "gray-ab" && rest.first().is_some_and(|a| a == "recover") {
        return false;
    }
    if sub == "gray-layer"
        && matches!(
            rest.first().map(String::as_str),
            Some("session-status" | "session-action" | "session-recover" | "refbench-recover")
        )
    {
        return false;
    }
    // preflight: 只读核查不拿锁; --fix 要写风扇/音量，跟测量一样排队
    if sub == "preflight" {
        return rest.iter().any(|a| a == "--fix");
    }
    // selftest report 只读本地证据目录、不带子命令只打用法, 都不碰设备
    if sub == "selftest" && matches!(rest.first().map(|s| s.as_str()), None | Some("report")) {
        return false;
    }
    // frame-audit: capture / storeop-check 碰设备, analyze 纯离线
    if sub == "frame-audit"
        && !matches!(
            rest.first().map(|s| s.as_str()),
            Some("capture") | Some("storeop-check")
        )
    {
        return false;
    }
    // perf --from-csv: 离线解析已拉回的 SP_daemon CSV, 不碰设备
    if sub == "perf" && rest.iter().any(|a| a == "--from-csv") {
        return false;
    }
    if matches!(sub, "eval" | "gpu-op") {
        // 这两个入口只有两种情况会碰设备: `--unlock` 回滚遗留锁频态, 或 `--request`
        // 指着一个真实存在的文件。其余 (裸调用、缺参数、请求路径不存在) 都在解析
        // 那一步就退出 —— 上层探活 (`phonefarm gpu-op` 裸调看认不认子命令) 不能因为
        // 别人持锁就排两小时队。
        if rest.iter().any(|a| a == "--unlock") {
            return true;
        }
        // eval --validate-only: 只做离线校验, 不碰设备
        if sub == "eval" && rest.iter().any(|a| a == "--validate-only") {
            return false;
        }
        return match rest.iter().position(|a| a == "--request") {
            Some(i) => rest
                .get(i + 1)
                .is_some_and(|p| std::path::Path::new(p).exists()),
            None => false,
        };
    }
    true
}

/// 解析 `45m` / `2h` / `900s` / 裸数字 (按分钟)。
pub fn parse_wait(s: &str) -> Result<Duration, String> {
    let s = s.trim();
    let (num, mult) = match s.chars().last() {
        Some('s') => (&s[..s.len() - 1], 1),
        Some('m') => (&s[..s.len() - 1], 60),
        Some('h') => (&s[..s.len() - 1], 3600),
        _ => (s, 60),
    };
    num.trim()
        .parse::<u64>()
        .ok()
        .and_then(|n| n.checked_mul(mult))
        .map(Duration::from_secs)
        .ok_or_else(|| format!("--lock-wait 看不懂 {s:?} (要 45m / 2h / 900s / 裸数字按分钟)"))
}

/// 把 `--lock-label X` / `--lock-wait D` 从子命令参数里剥掉
/// (子命令各自的解析器不认识它们)。`args[0]` 是子命令名, 不动。
pub fn strip_lock_args(args: &mut Vec<String>) -> Result<LockOpts, String> {
    let mut o = LockOpts {
        label: None,
        wait: None,
    };
    let mut kept = Vec::with_capacity(args.len());
    let mut it = std::mem::take(args).into_iter();
    let sub = it.next().unwrap_or_default();
    if !sub.is_empty() {
        kept.push(sub.clone());
    }
    while let Some(a) = it.next() {
        // Gray admission and execution must see the same option values. A
        // serial/path named --no-devlock must not disable the queue gate.
        let gray_value = match sub.as_str() {
            "gray-layer" => matches!(
                a.as_str(),
                "--serial"
                    | "--so"
                    | "--mode"
                    | "--package"
                    | "--out"
                    | "--max-seconds"
                    | "--actor-id"
                    | "--evidence-origin"
                    | "--power-policy"
                    | "--request-id"
                    | "--request"
                    | "--session"
                    | "--frames"
                    | "--intensity"
                    | "--timeout-seconds"
                    | "--device-binding"
            ),
            "gray-ab" => matches!(
                a.as_str(),
                "--serial" | "--out" | "--pairs" | "--mode" | "--evidence-origin"
            ),
            "game-deploy" => matches!(
                a.as_str(),
                "--serial" | "--request" | "--session" | "--out" | "--device-binding"
            ),
            _ => false,
        };
        if gray_value {
            let value = it.next().ok_or_else(|| format!("{a} requires a value"))?;
            kept.extend([a, value]);
            continue;
        }
        match a.as_str() {
            "--lock-label" => {
                o.label = Some(it.next().ok_or("--lock-label 要一个名字")?);
            }
            "--lock-wait" => {
                o.wait = Some(parse_wait(&it.next().ok_or("--lock-wait 要一个时长")?)?);
            }
            _ => kept.push(a),
        }
    }
    *args = kept;
    Ok(o)
}

pub enum Gate {
    Proceed,
    Exit(i32),
}
pub fn gate(args: &mut Vec<String>) -> Gate {
    let result = (|| -> Result<Gate, String> {
        let opts = strip_lock_args(args)?;
        let label = opts
            .label
            .or_else(|| std::env::var("DEVLOCK_LABEL").ok())
            .unwrap_or_else(|| DEFAULT_LABEL.into());
        // These producers validate their artifact before Lease::target acquires.
        // Keep their existing no-device/no-lease preflight behavior.
        if args
            .first()
            .is_some_and(|sub| matches!(sub.as_str(), "eval" | "gpu-op"))
        {
            std::env::set_var("DEVLOCK_LABEL", &label);
            if let Some(wait) = opts.wait {
                std::env::set_var("PHONEFARM_LEASE_TIMEOUT_MS", wait.as_millis().to_string());
            }
            return Ok(Gate::Proceed);
        }
        let storage = crate::lease_storage::resolve();
        let (root, farm) = storage.roots()?;
        let mut serials = Vec::new();
        for (i, arg) in args.iter().enumerate() {
            if arg == "--serial" {
                serials.extend(args.get(i + 1).cloned());
            } else if let Some(serial) = arg.strip_prefix("--serial=") {
                serials.push(serial.into());
            }
        }
        if serials.is_empty()
            && args
                .first()
                .is_some_and(|a| matches!(a.as_str(), "eval" | "gpu-op"))
        {
            if let Some(path) = args
                .iter()
                .position(|a| a == "--request")
                .and_then(|i| args.get(i + 1))
            {
                let request: serde_json::Value =
                    serde_json::from_slice(&std::fs::read(path).map_err(|e| e.to_string())?)
                        .map_err(|e| e.to_string())?;
                serials.extend(request["serial"].as_str().map(str::to_owned));
            }
        }
        if serials.is_empty() {
            serials
                .extend(crate::device_registry::inherited_binding()?.map(|binding| binding.serial));
        }
        if serials.is_empty() {
            serials.extend(crate::fd_lock::inherited()?.and_then(|(scope, _)| scope.serial));
        }
        // Multiple explicit devices require farm scope. Missing explicit scope stays farm-wide.
        let scope = match serials
            .first()
            .filter(|first| serials.iter().all(|serial| serial == *first))
        {
            Some(serial) => crate::devlease::resolve_scope(&storage, serial)?,
            None => {
                for serial in &serials {
                    crate::devlease::resolve_scope(&storage, serial)?.check_enabled(&farm)?;
                }
                crate::fd_lock::Scope::global()
            }
        };
        let guard = crate::fd_lock::Guard::acquire(&root, &farm, scope, &label, opts.wait)?;
        if !guard.owns() {
            return Ok(Gate::Proceed);
        }
        let mut command = Command::new(std::env::current_exe().map_err(|e| e.to_string())?);
        command.args(args.iter());
        guard.command(&mut command)?;
        Ok(Gate::Exit(wait_control_child(&mut command)))
    })();
    match result {
        Ok(result) => result,
        Err(error) => {
            eprintln!("{error}");
            println!(
                "{}",
                serde_json::json!({"schema":"phonefarm/devlock-error@1", "stage":"admission",
                "code":"device_lock_unavailable","error":error,"command":args.first(),"command_started":false,"exit_code":2})
            );
            Gate::Exit(2)
        }
    }
}

static CONTROL_INTERRUPTED: AtomicBool = AtomicBool::new(false);
extern "C" fn control_interrupted(_: libc::c_int) {
    CONTROL_INTERRUPTED.store(true, Ordering::SeqCst);
}
struct ControlInterrupt(libc::sigaction);
impl Drop for ControlInterrupt {
    fn drop(&mut self) {
        unsafe {
            libc::sigaction(libc::SIGINT, &self.0, std::ptr::null_mut());
        }
    }
}

pub(crate) fn wait_control_child(command: &mut Command) -> i32 {
    CONTROL_INTERRUPTED.store(false, Ordering::SeqCst);
    let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
    let mut previous: libc::sigaction = unsafe { std::mem::zeroed() };
    action.sa_sigaction = control_interrupted as *const () as usize;
    unsafe {
        libc::sigemptyset(&mut action.sa_mask);
    }
    if unsafe { libc::sigaction(libc::SIGINT, &action, &mut previous) } != 0 {
        eprintln!(
            "cannot supervise gray control cancellation: {}",
            std::io::Error::last_os_error()
        );
        return 2;
    }
    let _interrupt = ControlInterrupt(previous);
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            eprintln!("cannot start gray control owner: {error}");
            return 2;
        }
    };
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                return status
                    .code()
                    .unwrap_or_else(|| 128 + status.signal().unwrap_or(0))
            }
            Err(error) => {
                eprintln!("cannot wait for gray control owner: {error}");
                return 2;
            }
            Ok(None) => {}
        }
        if CONTROL_INTERRUPTED.swap(false, Ordering::SeqCst) {
            // Group cancellation already reaches the child. Forwarding also
            // supports a caller that signals only this wrapper's PID. Repeated
            // SIGINT is an idempotent stop request, never forced escalation.
            unsafe {
                libc::kill(child.id() as i32, libc::SIGINT);
            }
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

#[cfg(test)]
mod tests {

    /// storeop-check 也碰设备: 不拿锁的话 --lock-label 不会被剥掉, 子命令解析直接报错退出 (2026-10-11 真机踩到)
    #[test]
    fn frame_audit_device_subcommands_take_lock() {
        let v = |x: &[&str]| x.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert!(needs_lock("frame-audit", &v(&["capture", "--out", "x"])));
        assert!(needs_lock(
            "frame-audit",
            &v(&["storeop-check", "--rule", "a:1"])
        ));
        assert!(!needs_lock("frame-audit", &v(&["analyze", "f.json"])));
        assert!(!needs_lock(
            "frame-audit",
            &v(&["recover", "--session", "session", "--serial", "S"])
        ));
        assert!(!needs_lock(
            "gray-ab",
            &v(&["recover", "--session", "session", "--serial", "S"])
        ));
        assert!(needs_lock(
            "gray-ab",
            &v(&["--mode", "loadop", "--out", "x"])
        ));
        assert!(!needs_lock("frame-audit", &v(&[])));
    }

    use super::*;

    fn v(xs: &[&str]) -> Vec<String> {
        xs.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn lock_flags_are_stripped_before_the_subcommand_sees_them() {
        let mut a = v(&[
            "eval",
            "--request",
            "r.json",
            "--lock-label",
            "core",
            "--json",
            "--lock-wait",
            "10m",
        ]);
        let o = strip_lock_args(&mut a).unwrap();
        assert_eq!(a, v(&["eval", "--request", "r.json", "--json"]));
        assert_eq!(o.label.as_deref(), Some("core"));
        assert_eq!(o.wait, Some(Duration::from_secs(600)));
        for sub in ["gray-layer", "gray-ab"] {
            let mut gray = v(&[sub, "--serial", "--no-devlock", "--lock-label", "--help"]);
            let options = strip_lock_args(&mut gray).unwrap();
            assert_eq!(gray, v(&[sub, "--serial", "--no-devlock"]));
            assert_eq!(options.label.as_deref(), Some("--help"));
        }
        let mut gray = v(&[
            "gray-ab",
            "--out",
            "--lock-wait",
            "--mode",
            "loadop",
            "--no-devlock",
        ]);
        strip_lock_args(&mut gray).unwrap();
        assert_eq!(
            gray,
            v(&[
                "gray-ab",
                "--out",
                "--lock-wait",
                "--mode",
                "loadop",
                "--no-devlock"
            ])
        );
    }

    #[test]
    fn a_dangling_lock_flag_is_an_error() {
        assert!(strip_lock_args(&mut v(&["eval", "--lock-label"])).is_err());
        assert!(strip_lock_args(&mut v(&["eval", "--lock-wait", "soon"])).is_err());
    }

    #[test]
    fn wait_durations_parse_like_the_budget() {
        assert_eq!(parse_wait("45m").unwrap(), Duration::from_secs(2700));
        assert_eq!(parse_wait("2h").unwrap(), Duration::from_secs(7200));
        assert_eq!(parse_wait("900s").unwrap(), Duration::from_secs(900));
        assert_eq!(parse_wait("5").unwrap(), Duration::from_secs(300));
        assert!(parse_wait("18446744073709551615h").is_err());
    }

    #[test]
    fn only_device_touching_subcommands_take_the_lock() {
        assert!(!needs_lock(
            "bench",
            &v(&["--serial", "S", "--recover", "session"])
        ));
        assert!(!needs_lock(
            "capture",
            &v(&["--serial", "S", "--recover", "session"])
        ));
        assert!(needs_lock(
            "capture",
            &v(&["--serial", "S", "--frames", "1"])
        ));
        assert!(needs_lock("bench", &v(&["--serial", "S", "--model", "m"])));
        assert!(needs_lock("gray-layer", &v(&["off"])));
        assert!(needs_lock("perf", &v(&["--serial", "S"])));
        assert!(needs_lock("selftest", &v(&["run", "--out", "d"])));
        assert!(!needs_lock("selftest", &v(&["report", "--root", "d"])));
        assert!(!needs_lock("selftest", &v(&[])));
        for s in [
            "fleet",
            "devices",
            "keepalive",
            "serve",
            "parse-trace",
            "analyze",
            "report",
        ] {
            assert!(!needs_lock(s, &[]), "{s}");
        }
        assert!(!needs_lock("gpu-op", &v(&["--help"])));
    }

    /// game_opt_loop 的 harness 探活是**裸调** `phonefarm gpu-op` (看认不认这个子命令)。
    /// 它在解析参数那一步就退出, 碰不到设备; 曾经因为要锁而在别人持锁时排队两小时,
    /// 把 game_opt_loop 的 cargo test 整个挂住。
    #[test]
    fn a_bare_or_requestless_eval_call_does_not_queue() {
        assert!(!needs_lock("gpu-op", &[]));
        assert!(!needs_lock("eval", &[]));
        assert!(!needs_lock("gpu-op", &v(&["--serial", "S", "--json"])));
        assert!(!needs_lock("eval", &v(&["--request"])));
    }

    /// 上层探活拿一个不存在的请求路径问 eval 认不认 `--request`: 它碰不到设备,
    /// 不该在别人持锁时被挡在队里。真请求 (文件在) 照样要锁。
    #[test]
    fn an_eval_probe_with_a_missing_request_does_not_queue() {
        assert!(!needs_lock(
            "eval",
            &v(&["--request", "/nonexistent/x.json", "--json"])
        ));
        let f = std::env::temp_dir().join(format!("pf_devlock_req_{}.json", std::process::id()));
        std::fs::write(&f, "{}").unwrap();
        assert!(needs_lock("eval", &v(&["--request", &f.to_string_lossy()])));
        std::fs::remove_file(&f).ok();
        // --unlock 回滚锁频态是写设备
        assert!(needs_lock("gpu-op", &v(&["--serial", "S", "--unlock"])));
    }

    /// perf --from-csv 只读盘上的 CSV, 不拿锁; frame-sources 是只读探测但碰设备, 要拿锁
    #[test]
    fn perf_from_csv_is_offline_and_frame_sources_locks() {
        assert!(!needs_lock("perf", &v(&["--from-csv", "x.csv", "--json"])));
        assert!(needs_lock(
            "perf",
            &v(&["--serial", "S", "--source", "sflatency"])
        ));
        assert!(needs_lock("frame-sources", &v(&["--serial", "S"])));
    }

    /// eval --validate-only 是离线 dry-run: 请求文件在也不排队拿锁
    #[test]
    fn eval_validate_only_does_not_queue() {
        let existing = concat!(env!("CARGO_MANIFEST_DIR"), "/Cargo.toml");
        assert!(!needs_lock(
            "eval",
            &v(&["--request", existing, "--validate-only"])
        ));
        assert!(needs_lock("eval", &v(&["--request", existing])));
    }
}
