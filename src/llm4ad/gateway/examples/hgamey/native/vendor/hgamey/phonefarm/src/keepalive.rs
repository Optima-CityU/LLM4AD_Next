//! keepalive: 农场级设备保活巡检 (SPEC_KEEPALIVE)。
//! 与 quest.rs 的任务级生命周期分工: quest 保电池(退出锁屏), keepalive 保待命(常亮解锁)。
//! 全部动作幂等可重放; 枚举/分辨率/坐标一律现场解析, 不写死任何 serial 或尺寸(AGENTS.md 通用性红线)。
//!
//! 实测契约 (2026-09-08 两台 OH 真机 + 三台 Android 模拟器对拍定论):
//!   - Android 点亮只用 KEYCODE_WAKEUP(224); KEYCODE_POWER 是翻转键, 会把亮屏按灭。
//!   - OH 锁屏应用(KeyGuard)在锁屏界面把息屏覆盖值强写 10000ms, 解锁瞬间又"恢复"冲掉
//!     先前写入——所以 HDC 顺序必须是 wakeup → 上滑解锁 → 等 2s → override 落在最后。
//!   - OH override 不跨重启/锁屏恢复存活, 每轮巡检必须重放。
use serde_json::{json, Value};
use std::io::Write;
use std::time::Duration;
use crate::fleet::parse_hdc_power_on;

const TIMEOUT_NEVER: &str = "2147483647"; // int32 上限; 两族都没有真正的"永不"值
const TIMEOUT_NEVER_MS: i64 = 2147483647;

/// 输出到 stdout; 消费方提前关管(`| head -1`、宿主终止读取)时安静退出,
/// 不让 println! 炸 "failed printing to stdout" 的 panic 栈(E2E T6 实测)
fn emit(text: &str) {
    let mut out = std::io::stdout().lock();
    if out.write_all(text.as_bytes()).is_err() || out.flush().is_err() {
        std::process::exit(0);
    }
}

// ══════════════ 参数解析 ══════════════

pub struct KaArgs {
    serial: Option<String>,
    status: bool,
    watch: Option<u64>, // Some(间隔秒) 常驻守护
    json: bool,
}

fn parse_args(args: &[String]) -> Result<KaArgs, String> {
    let mut ka = KaArgs { serial: None, status: false, watch: None, json: false };
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--serial" => {
                i += 1;
                ka.serial = Some(args.get(i).cloned()
                    .ok_or("--serial 需要设备名(hdc 目标用 hdc:<key> 前缀)")?);
            }
            "--status" => ka.status = true,
            "--json" => ka.json = true,
            "--watch" => {
                // 可选值: 下一个参数能解析成秒数才消费它,否则按默认 300 且留给主循环解释
                ka.watch = Some(match args.get(i + 1).and_then(|v| v.parse::<u64>().ok()) {
                    Some(n) => { i += 1; n.max(5) } // 间隔下限 5s: 0 或过小会把设备通道打爆
                    None => 300,
                });
            }
            other => return Err(format!("无法识别的参数 '{other}'\n用法: phonefarm keepalive [--serial S] [--status] [--watch [间隔秒]] [--json]")),
        }
        i += 1;
    }
    Ok(ka)
}

// ══════════════ 输出解析(纯函数供单测) ══════════════

/// adb devices → 在线 serial 集: 第二列恰为 "device"(跳过 offline/unauthorized)
fn parse_adb_devices(text: &str) -> Vec<String> {
    let mut v = Vec::new();
    for line in text.lines() {
        let cols: Vec<&str> = line.split_whitespace().collect();
        if cols.len() == 2 && cols[1] == "device" {
            v.push(cols[0].to_string());
        }
    }
    v
}

/// hdc list targets → connect key 集: 滤空行与 [Empty] 哨兵(与 devices 子命令同一纪律)
fn parse_hdc_targets(text: &str) -> Vec<String> {
    text.lines().map(|l| l.trim())
        .filter(|l| !l.is_empty() && *l != "[Empty]" && !l.starts_with("[Fail"))
        .map(|l| l.to_string())
        .collect()
}

/// dumpsys power 的 mWakefulness: Awake→true; 其余(Asleep/Dozing)→false; 采不到 None
fn parse_wakefulness(text: &str) -> Option<bool> {
    let i = text.find("mWakefulness=")?;
    let val: String = text[i + "mWakefulness=".len()..].chars()
        .take_while(|c| c.is_ascii_alphabetic()).collect();
    if val.is_empty() { None } else { Some(val == "Awake") }
}

/// PowerManagerService dump 的 OverrideTimeout(毫秒)
fn parse_override_timeout(text: &str) -> Option<i64> {
    let i = text.find("OverrideTimeout=")?;
    let digits: String = text[i + "OverrideTimeout=".len()..].chars()
        .take_while(|c| c.is_ascii_digit()).collect();
    digits.parse().ok()
}

/// RenderService dump 的 activeMode 分辨率: "activeMode: 1200x1920, refreshRate=60"
fn parse_active_mode(text: &str) -> Option<(i32, i32)> {
    let i = text.find("activeMode")?;
    let nums: Vec<i32> = text[i..]
        .split(|c: char| !c.is_ascii_digit())
        .filter(|t| !t.is_empty())
        .filter_map(|t| t.parse().ok())
        .collect();
    if nums.len() >= 2 { Some((nums[0], nums[1])) } else { None }
}

/// 解锁上滑(比例坐标): 底部中点 → 屏幕上沿 30% 处
fn unlock_swipe(w: i32, h: i32) -> (i32, i32, i32, i32) {
    (w / 2, h * 92 / 100, w / 2, h * 30 / 100)
}

// ══════════════ 设备枚举 ══════════════

/// 两族并列,各自 best-effort(工具不在 PATH 该族即空,不报错)——与 devices 子命令同纪律
fn enumerate_devices() -> Vec<String> {
    let mut v = Vec::new();
    if let Some(adb) = crate::device::locate_adb() {
        if let Ok(out) = crate::fd_lock::transport_command(adb).arg("devices").output() {
            v.extend(parse_adb_devices(&String::from_utf8_lossy(&out.stdout)));
        }
    }
    if let Ok(out) = crate::fd_lock::transport_command("hdc").args(["list", "targets"]).output() {
        v.extend(parse_hdc_targets(&String::from_utf8_lossy(&out.stdout))
            .into_iter().map(|k| format!("hdc:{k}")));
    }
    v
}

// ══════════════ 巡检 ══════════════

struct Report {
    serial: String,
    online: bool,
    screen_on: Option<bool>,
    policy_ok: Option<bool>,
    notes: Vec<String>,
}

impl Report {
    fn ok(&self) -> bool {
        self.online && self.screen_on == Some(true) && self.policy_ok == Some(true)
    }
    fn to_json(&self) -> Value {
        json!({"serial": self.serial, "online": self.online,
               "screen_on": self.screen_on, "policy_ok": self.policy_ok,
               "ok": self.ok(), "notes": self.notes})
    }
}

/// 单台巡检入口: 先心跳,再按后端分派。write=false 即 --status 只读(不写任何设备状态)
fn patrol(serial: &str, phone: &crate::device::Device, write: bool) -> Report {
    let mut r = Report { serial: serial.into(), online: false,
                         screen_on: None, policy_ok: None, notes: Vec::new() };
    if !phone.health_check(5000) {
        r.notes.push("无心跳(掉线或未授权)".into());
        return r;
    }
    r.online = true;
    if serial.starts_with("hdc:") {
        patrol_hdc(&mut r, phone, write);
    } else {
        patrol_android(&mut r, phone, write);
    }
    r
}

fn patrol_android(r: &mut Report, phone: &crate::device::Device, write: bool) {
    if write {
        // 顺序即 §3 契约; 每步幂等
        phone.shell("input keyevent KEYCODE_WAKEUP", 5000);
        phone.shell("wm dismiss-keyguard", 5000);
        phone.shell(&format!("settings put system screen_off_timeout {TIMEOUT_NEVER}"), 5000);
        phone.shell("svc power stayon true", 5000);
    }
    r.screen_on = parse_wakefulness(&phone.shell("dumpsys power | grep -m1 mWakefulness=", 6000));
    let timeout = phone.shell("settings get system screen_off_timeout", 5000);
    let stayon = phone.shell("settings get global stay_on_while_plugged_in", 5000);
    let to_ok = timeout.trim() == TIMEOUT_NEVER;
    let so = stayon.trim();
    let so_ok = !so.is_empty() && so != "0" && so != "null";
    r.policy_ok = Some(to_ok && so_ok);
    if !to_ok { r.notes.push(format!("screen_off_timeout={}", timeout.trim())); }
    if !so_ok { r.notes.push(format!("stay_on_while_plugged_in={so}")); }
}

fn patrol_hdc(r: &mut Report, phone: &crate::device::Device, write: bool) {
    if write {
        // 顺序即 §4 契约: override 必须落在解锁之后,否则被 KeyGuard 冲掉
        phone.shell("power-shell wakeup", 5000);
        let scr = phone.shell("hidumper -s RenderService -a screen", 8000);
        match parse_active_mode(&scr) {
            Some((w, h)) => {
                let (x, y1, x2, y2) = unlock_swipe(w, h);
                phone.swipe(x, y1, x2, y2);
                std::thread::sleep(Duration::from_secs(2)); // KeyGuard 恢复写发生在解锁后数秒内
            }
            None => r.notes.push("分辨率解析失败,未上滑(不影响后续 override)".into()),
        }
        phone.shell(&format!("power-shell timeout -o {TIMEOUT_NEVER}"), 5000);
        // 竞态关闭: KeyGuard 的恢复写实测可落在解锁后 3~4s(晚于上面的 override),
        // 把它冲回基准值。等足窗口回读, 被冲就再压一次——override 必须落在
        // KeyGuard 最后一次写之后才稳(E2E 日志实证: 解锁:43 → 恢复写:47)
        std::thread::sleep(Duration::from_secs(2));
        let pm = phone.shell("hidumper -s PowerManagerService -a -a", 10000);
        if parse_override_timeout(&pm) != Some(TIMEOUT_NEVER_MS) {
            phone.shell(&format!("power-shell timeout -o {TIMEOUT_NEVER}"), 5000);
        }
    }
    let scr = phone.shell("hidumper -s RenderService -a screen", 8000);
    r.screen_on = parse_hdc_power_on(&scr);
    let pm = phone.shell("hidumper -s PowerManagerService -a -a", 10000);
    let ov = parse_override_timeout(&pm);
    r.policy_ok = Some(ov == Some(TIMEOUT_NEVER_MS));
    if r.policy_ok != Some(true) {
        r.notes.push(format!("OverrideTimeout={} (KeyGuard 占用或重启后未重放)",
            ov.map(|v| v.to_string()).unwrap_or_else(|| "采不到".into())));
    }
}

// ══════════════ 出口 ══════════════

fn once(ka: &KaArgs) -> i32 {
    let serials = match &ka.serial {
        Some(s) => vec![s.clone()],
        None => enumerate_devices(),
    };
    if serials.is_empty() {
        eprintln!("没有在线设备(adb devices / hdc list targets 均为空)");
        return 2;
    }
    let tmp = std::env::temp_dir()
        .join(format!("phonefarm-keepalive-{}", std::process::id()))
        .to_string_lossy().to_string();
    let _ = std::fs::create_dir_all(&tmp);
    let mut reports = Vec::new();
    for s in &serials {
        let phone = crate::device::Device::new(Some(s.clone()), tmp.clone());
        reports.push(patrol(s, &phone, !ka.status));
    }
    let all_ok = reports.iter().all(|r| r.ok());
    if ka.json {
        emit(&format!("{}\n", json!({"ok": all_ok, "readonly": ka.status,
            "devices": reports.iter().map(|r| r.to_json()).collect::<Vec<_>>()})));
    } else {
        let yn = |b: Option<bool>| match b { Some(true) => "是", Some(false) => "否", None => "?" };
        let mut out = String::new();
        for r in &reports {
            out.push_str(&format!("{} {:<26} 在线={} 亮屏={} 不息屏={}{}\n",
                if r.ok() { "✓" } else { "✗" }, r.serial,
                if r.online { "是" } else { "否" }, yn(r.screen_on), yn(r.policy_ok),
                if r.notes.is_empty() { String::new() } else { format!("  ({})", r.notes.join("; ")) }));
        }
        emit(&out);
    }
    if all_ok { 0 } else { 1 }
}

pub fn run_keepalive(args: &[String]) -> i32 {
    let ka = match parse_args(args) {
        Ok(k) => k,
        Err(e) => { eprintln!("{e}"); return 2; }
    };
    if let Some(interval) = ka.watch {
        let mut cycle = 0u64;
        loop {
            cycle += 1;
            emit(&format!("── keepalive 第{cycle}轮 (每{interval}s, Ctrl+C 退出) ──\n"));
            let _ = once(&ka); // 单轮失败不退出: 守护的职责是继续盯着
            std::thread::sleep(Duration::from_secs(interval));
        }
    }
    once(&ka)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adb_devices_skips_offline_and_header() {
        let text = "List of devices attached\n\
                    N100CU025C18D000458\tdevice\n\
                    emulator-5554\toffline\n\
                    emulator-5556\tdevice\n\
                    * daemon started successfully\n";
        assert_eq!(parse_adb_devices(text), vec!["N100CU025C18D000458", "emulator-5556"]);
        assert!(parse_adb_devices("List of devices attached\n\n").is_empty());
    }

    #[test]
    fn hdc_targets_filters_sentinels() {
        assert_eq!(parse_hdc_targets("5ce1227d00000000000000000923012c\ndd011a4144363141301012500404ac00\n"),
                   vec!["5ce1227d00000000000000000923012c", "dd011a4144363141301012500404ac00"]);
        assert!(parse_hdc_targets("[Empty]\n").is_empty());
        assert!(parse_hdc_targets("").is_empty());
        assert!(parse_hdc_targets("[Fail]Connect failed\n").is_empty());
    }

    #[test]
    fn wakefulness_and_power_status() {
        assert_eq!(parse_wakefulness("  mWakefulness=Awake\n"), Some(true));
        assert_eq!(parse_wakefulness("  mWakefulness=Asleep\n"), Some(false));
        assert_eq!(parse_wakefulness("  mWakefulness=Dozing\n"), Some(false));
        assert_eq!(parse_wakefulness("(空)"), None);
        let on = "screen[0]: id=0, powerStatus=POWER_STATUS_ON, backlight=223";
        let off = "screen[0]: id=0, powerStatus=POWER_STATUS_OFF, backlight=9";
        assert_eq!(parse_hdc_power_on(on), Some(true));
        assert_eq!(parse_hdc_power_on(off), Some(false));
        assert_eq!(parse_hdc_power_on("(空)"), None);
    }

    #[test]
    fn override_timeout_and_active_mode() {
        // KeyGuard 占用时的 10s 覆盖值也要如实解析(它是"未达标"的判定依据)
        let pm = "ScreenOffTime: Timeout=30000ms  OverrideTimeout=10000ms";
        assert_eq!(parse_override_timeout(pm), Some(10000));
        let pm2 = "ScreenOffTime: Timeout=30000ms  OverrideTimeout=2147483647ms";
        assert_eq!(parse_override_timeout(pm2), Some(2147483647));
        assert_eq!(parse_override_timeout("(空)"), None);
        let scr = "supportedMode[0]: 720x1280, refreshRate=69\nactiveMode: 1200x1920, refreshRate=60";
        // 必须锚在 activeMode 行, 不能采到 supportedMode 的头
        assert_eq!(parse_active_mode(scr), Some((1200, 1920)));
        assert_eq!(parse_active_mode("(空)"), None);
    }

    #[test]
    fn swipe_coords_scale_with_resolution() {
        assert_eq!(unlock_swipe(1200, 1920), (600, 1766, 600, 576));
        assert_eq!(unlock_swipe(720, 1280), (360, 1177, 360, 384));
    }

    #[test]
    fn args_parsing_forms() {
        let ka = parse_args(&[]).unwrap();
        assert!(ka.serial.is_none() && !ka.status && ka.watch.is_none() && !ka.json);
        let ka = parse_args(&["--status".into(), "--json".into()]).unwrap();
        assert!(ka.status && ka.json && ka.watch.is_none());
        let ka = parse_args(&["--serial".into(), "hdc:abc".into()]).unwrap();
        assert_eq!(ka.serial.as_deref(), Some("hdc:abc"));
        // --watch 无值默认 300, 且不吃掉后面的 --json
        let ka = parse_args(&["--watch".into(), "--json".into()]).unwrap();
        assert_eq!(ka.watch, Some(300));
        assert!(ka.json);
        let ka = parse_args(&["--watch".into(), "60".into()]).unwrap();
        assert_eq!(ka.watch, Some(60));
        // 间隔下限 5s
        let ka = parse_args(&["--watch".into(), "0".into()]).unwrap();
        assert_eq!(ka.watch, Some(5));
        assert!(parse_args(&["--bogus".into()]).is_err());
        assert!(parse_args(&["--serial".into()]).is_err());
    }
}
