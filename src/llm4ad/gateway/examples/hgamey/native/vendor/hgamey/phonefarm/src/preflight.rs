//! preflight: 开测前的环境不变量核查（可选自动修复）。
//!
//! 这些不变量以前散在 eval / selftest / 人肉脚本里：pair 采集被打断后风扇开了一夜、
//! selftest 前几臂风扇开着导致整组功耗作废重跑。收敛成一条命令，每臂开测前跑一遍
//! （eval 自己的风扇守卫照旧，这条命令是给 agent / 脚本 / 人用的总核查）。
//!
//!   phonefarm preflight [--serial X] [--fix] [--json] [--temp-cap 48]
//!
//! 查什么（owner 政策: 主动风扇常关——噪音；测量纪律: 手机必须静音）:
//!   fan      /sys/kernel/fan/fan_enable == 0（机器没有风扇节点记 na，不算不过）
//!   volume   媒体音量 == 0
//!   thermal  SoC 最高温 < --temp-cap（修不了只能等冷 → 不过）
//!   charging 只报不查（待机就该充电；停充是 eval 每臂自己的事）
//!   screen   只报不查（前台应用/亮屏）
//!
//! 只读探测前租约域必须明确 clear；--fix 全程持有实际强租约。标签不提供绕过许可。
//! 退出码: 0 全过（或 --fix 修好了）；2 有不过；3 设备或租约域不可用。

use crate::device::{self, Device};
use serde::Serialize;

#[derive(Serialize)]
struct Check {
    name: &'static str,
    /// pass / fail / fixed / info / na
    status: String,
    detail: String,
}

#[derive(Serialize)]
struct Out {
    serial: String,
    ok: bool,
    checks: Vec<Check>,
}

fn pass(name: &'static str, detail: impl Into<String>) -> Check {
    Check { name, status: "pass".into(), detail: detail.into() }
}
fn fail(name: &'static str, detail: impl Into<String>) -> Check {
    Check { name, status: "fail".into(), detail: detail.into() }
}
fn info(name: &'static str, detail: impl Into<String>) -> Check {
    Check { name, status: "info".into(), detail: detail.into() }
}

const USAGE: &str = "phonefarm preflight [--serial X] [--fix] [--json] [--temp-cap 48]
开测前的环境不变量核查: 风扇常关(owner 政策) / 媒体静音 / SoC 温度 / 充电与前台(只报)。
--fix 在实际强租约内自动修能修的(关风扇、音量置 0)。只读入口要求租约域空闲。退出码 0 全过 / 2 有不过 / 3 设备或租约域不可用。";

pub fn run_preflight(args: &[String]) -> i32 {
    let mut serial: Option<String> = None;
    let mut fix = false;
    let mut json = false;
    let mut temp_cap = 48.0f64;
    let mut it = args.iter().peekable();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--serial" => serial = it.next().cloned(),
            "--fix" => fix = true,
            "--json" => json = true,
            "--temp-cap" => {
                temp_cap = it.next().and_then(|v| v.parse().ok()).unwrap_or(48.0);
            }
            "-h" | "--help" => {
                println!("{USAGE}");
                return 0;
            }
            other => {
                eprintln!("preflight: 不认识的参数 {other}\n{USAGE}");
                return 2;
            }
        }
    }

    if !fix && !crate::observation::in_worker() {
        return match crate::observation::run("preflight", args,
            &crate::lease_storage::resolve(), std::time::Duration::from_secs(45)) {
            Ok(output) => {
                use std::io::Write;
                let _ = std::io::stdout().write_all(&output.stdout);
                let _ = std::io::stderr().write_all(&output.stderr);
                output.status.code().unwrap_or(3)
            }
            Err(e) => {
                if json { println!("{}", serde_json::json!({"observation":e.state,"reason":e.reason})); }
                eprintln!("preflight {}: {}", e.state, e.reason);
                3
            }
        };
    }
    // main's outer devlock is coordination; the actual strong lease protects --fix.
    let _lease = if fix {
        match crate::devlease::Lease::target(serial.as_deref(), "preflight-fix") {
            Ok(lease) => Some(lease),
            Err(e) => { eprintln!("preflight: {e}"); return 3; }
        }
    } else {
        None
    };
    let Some(serial) = _lease.as_ref().map(|l| l.serial.clone())
        .or_else(|| device::resolve_serial(serial.as_deref())) else {
        eprintln!("preflight: 判不出用哪台设备（零台/多台在线，--serial 指定）");
        return 3;
    };
    // run_timeout 把 stdout 写进 <tmp>/_cmd.out —— 目录不存在就读不回任何东西
    let tmpd = std::env::temp_dir().join("phonefarm-preflight");
    let _ = std::fs::create_dir_all(&tmpd);
    let tmp = tmpd.display().to_string();
    let phone = Device::new(Some(serial.clone()), tmp);

    let mut checks: Vec<Check> = Vec::new();

    // ── fan：owner 政策常关（噪音；且风扇自耗电进功耗读数）──
    let fan_node = "/sys/kernel/fan/fan_enable";
    let fan = phone.shell(&format!("cat {fan_node} 2>/dev/null || su -c 'cat {fan_node}'"), 8000);
    let fan = fan.trim();
    if fan.is_empty() {
        checks.push(Check { name: "fan", status: "na".into(), detail: "读不到风扇节点（这台机器没有风扇，或要 root）".into() });
    } else if fan == "0" {
        checks.push(pass("fan", "风扇关着"));
    } else if fix {
        phone.shell(&format!("su -c 'echo 0 > {fan_node}'"), 8000);
        let after = phone.shell(&format!("cat {fan_node}"), 8000);
        if after.trim() == "0" {
            checks.push(Check { name: "fan", status: "fixed".into(), detail: format!("风扇曾是 {fan} 档开着，已关") });
        } else {
            checks.push(fail("fan", format!("风扇 {fan}，写 0 没生效")));
        }
    } else {
        checks.push(fail("fan", format!("风扇开着（fan_enable={fan}）——--fix 关掉")));
    }

    // ── volume：测量纪律静音 ──
    {
        let v = phone.shell("cmd media_session volume --stream 3 --get", 8000);
        let vol = v
            .split("volume is ")
            .nth(1)
            .and_then(|s| s.split_whitespace().next())
            .and_then(|s| s.parse::<i64>().ok());
        match vol {
            Some(0) => checks.push(pass("volume", "媒体音量 0")),
            Some(n) if fix => {
                phone.shell("cmd audio set-volume 3 0", 8000);
                let v2 = phone.shell("cmd media_session volume --stream 3 --get", 8000);
                let ok = v2.split("volume is ").nth(1).and_then(|s| s.split_whitespace().next()) == Some("0");
                checks.push(if ok {
                    Check { name: "volume", status: "fixed".into(), detail: format!("媒体音量曾是 {n}，已静音") }
                } else {
                    fail("volume", format!("媒体音量 {n}，置 0 没生效"))
                });
            }
            Some(n) => checks.push(fail("volume", format!("媒体音量 {n}（测量必须静音）——--fix 置 0"))),
            None => checks.push(Check { name: "volume", status: "na".into(), detail: "读不到媒体音量".into() }),
        }
    }

    // ── thermal：SoC 结温（修不了，只能等冷）。cpu-hw-trip-* 这类区报的是跳变阈值
    //    (105°C) 不是温度，口径与 fleet 一致走 hwcond::soc_max_c（按前缀 + 跳哨兵值）──
    let zones = phone.shell(
        "for z in /sys/class/thermal/thermal_zone*; do echo \"$(cat $z/type 2>/dev/null) $(cat $z/temp 2>/dev/null)\"; done",
        8000,
    );
    let zones = crate::hwcond::parse_thermal(&zones);
    match crate::hwcond::soc_max_c(&zones) {
        None => checks.push(Check { name: "thermal", status: "na".into(), detail: "读不到 SoC 温度区".into() }),
        Some((ty, c)) => checks.push(if c < temp_cap {
            pass("thermal", format!("SoC 最高 {c:.1}°C ({ty}) < {temp_cap:.0}°C"))
        } else {
            fail("thermal", format!("SoC 最高 {c:.1}°C ({ty}) ≥ 上限 {temp_cap:.0}°C，先等冷"))
        }),
    }

    // ── charging / screen：只报不查（screen 要 dumpsys，轻量档跳过）──
    let bat = phone.shell("cat /sys/class/power_supply/battery/status 2>/dev/null", 8000);
    let ce = phone.shell("su -c 'cat /sys/class/qcom-battery/charging_enabled' 2>/dev/null", 8000);
    checks.push(info(
        "charging",
        format!("电池 {}，charging_enabled={}", bat.trim(), ce.trim()),
    ));
    {
        let focus = phone.shell("dumpsys window 2>/dev/null | grep -m1 mCurrentFocus", 8000);
        let focus = focus.trim().rsplit(['/', '}']).nth(1).unwrap_or("?").to_string();
        checks.push(info("screen", format!("前台 {focus}")));
    }

    let ok = checks.iter().all(|c| c.status != "fail");
    let out = Out { serial, ok, checks };
    if json {
        println!("{}", serde_json::to_string_pretty(&out).unwrap());
    } else {
        for c in &out.checks {
            let mark = match c.status.as_str() {
                "pass" => "✓",
                "fixed" => "✓(已修)",
                "fail" => "✗",
                "na" => "—",
                _ => "·",
            };
            println!("{mark} {:<9} {}", c.name, c.detail);
        }
        println!("preflight: {}", if ok { "全过，可以开测" } else { "有不过——先处理（--fix 能修风扇/音量）" });
    }
    if ok { 0 } else { 2 }
}
