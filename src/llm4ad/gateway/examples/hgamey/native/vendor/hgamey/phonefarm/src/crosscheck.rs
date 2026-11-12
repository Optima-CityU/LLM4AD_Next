//! 把同一窗口里两条采集通路的产物并排成一张表。
//!
//! 由 `loop_v1/tools/crosscheck_report.py` 逐行搬过来, 输出字节一致。
//! 纯函数: 只读目录里已有的 JSON/文本, 不读时钟、不碰设备。同一份输入每次重算一致。
//!
//! 两条通路量的不是同一个东西, 所以这张表的重点不是「谁更准」, 是**哪些格子本该
//! 对上、哪些格子本来就不可比**:
//!
//!   对得上才合理 —— 功耗与温度: 两边读的是同一批 sysfs 节点
//!     (`/sys/class/power_supply/battery/{current_now,voltage_now}`, `/sys/class/thermal`),
//!     差异只该来自采样时刻与平均窗口, 差得多就是哪边算错了;
//!   本来就不可比 —— 帧时 p95: 我们走 ftrace kgsl 逐帧事件, HiSmartPerf 安卓侧的
//!     实时流每秒只有一个整数 fps, 根本没有逐帧间隔。

use crate::pyjson::{loads, PyVal};
#[cfg(test)]
use crate::pyobj;
use std::path::Path;

/// 手机上物理上说得通的温度区间 (摄氏度)。超出这个范围的不是温度。
const TEMP_MIN_C: f64 = -20.0;
const TEMP_MAX_C: f64 = 150.0;

fn load(path: &Path) -> PyVal {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|t| loads(&t).ok())
        .unwrap_or(PyVal::Obj(vec![]))
}

/// Python 的真值判断: None / 空容器 / 0 / False / 空串都是假。
/// `crosscheck_report.py` 里 `sp = sp or {}` 加 `if not snap` 连起来就是这一条。
fn falsy(v: &PyVal) -> bool {
    match v {
        PyVal::Null => true,
        PyVal::Bool(b) => !b,
        PyVal::Int(i) => *i == 0,
        PyVal::Float(f) => *f == 0.0,
        PyVal::Str(s) => s.is_empty(),
        PyVal::List(xs) => xs.is_empty(),
        PyVal::Obj(kvs) => kvs.is_empty(),
    }
}

/// Python 的 `f"{x:.{nd}f}"`: 非有限值印小写 `nan` / `inf`, Rust 印 `NaN` / `inf`。
fn ffmt(x: f64, nd: usize, sign: bool) -> String {
    if x.is_nan() {
        // Python 对 nan 一律印正号, 不看符号位
        return if sign { "+nan".into() } else { "nan".into() };
    }
    if x.is_infinite() {
        let s = if x > 0.0 {
            if sign { "+inf" } else { "inf" }
        } else {
            "-inf"
        };
        return s.to_string();
    }
    if sign {
        format!("{x:+.*}", nd)
    } else {
        format!("{x:.*}", nd)
    }
}

/// Python `f"{v:.{nd}f}{unit}"`; None 印成「未测到」, 非数字原样 str()。
fn fmt(v: Option<&PyVal>, unit: &str, nd: usize) -> String {
    match v {
        None | Some(PyVal::Null) => "未测到".to_string(),
        Some(PyVal::Int(i)) => format!("{}{unit}", ffmt(*i as f64, nd, false)),
        Some(PyVal::Float(f)) => format!("{}{unit}", ffmt(*f, nd, false)),
        Some(other) => other.py_str(),
    }
}

/// 两个读数的差与相对差。任一侧缺失就没有差可言 —— 不拿 0 当缺失值的替身。
fn delta(a: Option<&PyVal>, b: Option<&PyVal>) -> (String, String) {
    let (Some(a), Some(b)) = (a.and_then(|v| v.as_f64()), b.and_then(|v| v.as_f64())) else {
        return ("—".into(), "—".into());
    };
    let d = a - b;
    if b == 0.0 {
        return (ffmt(d, 3, true), "—".into());
    }
    (ffmt(d, 3, true), format!("{}%", ffmt(100.0 * d / b, 1, true)))
}

/// 窗口中点直读的 `/sys/class/thermal` → [(类型, 摄氏度)], 按出现顺序去重。
///
/// 内核这里的单位是毫摄氏度; 但不同热区偶有直接给摄氏度的, 故按量级判:
/// 大于 1000 视为毫摄氏度。
///
/// 两类值必须丢掉, 否则对比表里会混进根本不是温度的数字:
///
///   - **0 不是 0 摄氏度**, 是这台机器没有这个传感器;
///   - 折算完落在 -20..150 °C 之外的: 掉线的射频热区回 `-273000` (绝对零度哨兵),
///     而 `vbat` 这种压根不是热区的节点回的是**毫伏** (`4200` → 会被当成 4.2 °C)。
///     按量级猜单位本来就只能猜个大概, 物理区间是兜底的那道闸。
pub fn thermal_zones(path: &Path) -> Vec<(String, f64)> {
    let Ok(bytes) = std::fs::read(path) else {
        return Vec::new();
    };
    let text = String::from_utf8_lossy(&bytes);
    let mut out: Vec<(String, f64)> = Vec::new();
    for line in text.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() != 2 {
            continue;
        }
        let (name, raw) = (parts[0], parts[1]);
        let Ok(v) = raw.parse::<i64>() else { continue };
        if v == 0 {
            continue;
        }
        // 名字里写明是电压的, 直接不收: `vbat` 回的是毫伏 (`4200`), 折算完是 4.2,
        // 物理区间拦不住它 —— 量级判不出来的只能按名字判。
        let low = name.to_lowercase();
        if ["vbat", "volt", "vph"].iter().any(|k| low.contains(k)) {
            continue;
        }
        let c = if v.unsigned_abs() > 1000 {
            v as f64 / 1000.0
        } else {
            v as f64
        };
        if !(TEMP_MIN_C..=TEMP_MAX_C).contains(&c) {
            continue;
        }
        // Python 的 setdefault: 同名热区只留第一次见到的那个值
        if !out.iter().any(|(k, _)| k == name) {
            out.push((name.to_string(), c));
        }
    }
    out
}

/// 表里的一行: (名称, HiSmartPerf 侧读数, 我们的读数, 单位, 说明)
type Row = (String, Option<PyVal>, Option<PyVal>, &'static str, &'static str);

fn unavailable_lines(snap: &PyVal, tag: &str) -> Vec<String> {
    if falsy(snap) {
        return vec![format!("  {tag}: 这条通路整份产物都没读到")];
    }
    match snap.get("unavailable") {
        Some(PyVal::List(us)) => us
            .iter()
            .map(|u| {
                format!(
                    "  {tag} {}: {}",
                    u.get("field").map(|v| v.py_str()).unwrap_or("None".into()),
                    u.get("reason").map(|v| v.py_str()).unwrap_or("None".into())
                )
            })
            .collect(),
        _ => Vec::new(),
    }
}

/// Python 的 `str.ljust(w)` / `>{w}` 都是按**字符数**排版的。
fn ljust(s: &str, w: usize) -> String {
    let n = s.chars().count();
    format!("{s}{}", " ".repeat(w.saturating_sub(n)))
}
fn rjust(s: &str, w: usize) -> String {
    let n = s.chars().count();
    format!("{}{s}", " ".repeat(w.saturating_sub(n)))
}

pub fn report(dir: &Path) -> String {
    let ft = load(&dir.join("summary.json")); // 我们的: ftrace 逐帧
    let sysfs = load(&dir.join("perf_sysfs.json")); // 我们的: sysfs 电源轨
    let sp = load(&dir.join("perf_smartperf.json")); // HiSmartPerf 安卓通路
    let zones = thermal_zones(&dir.join("thermal_mid.txt"));

    let mut l: Vec<String> = vec!["═══ 两通路并排 ═══".into()];
    let cond = dir.join("test_conditions.txt");
    if cond.exists() {
        l.push("  测试条件 (两条通路共享同一套, 因为是同一窗口并排采的):".into());
        if let Ok(b) = std::fs::read(&cond) {
            for line in String::from_utf8_lossy(&b).lines() {
                if !line.trim().is_empty() {
                    l.push(format!("    {}", line.trim_end()));
                }
            }
        }
    }
    let count = |v: &PyVal| match v.get("sample_count") {
        Some(x) => x.py_str(),
        None => "0".to_string(),
    };
    l.push(format!("  HiSmartPerf 采样点 {} 条 (每秒一条)", count(&sp)));
    l.push(format!("  sysfs 电源轨采样点 {} 条 (每 200ms 一条)", count(&sysfs)));
    l.push(format!(
        "  ftrace 帧数 {} 帧",
        ft.get("n_frames").map(|v| v.py_str()).unwrap_or("—".into())
    ));
    l.push(String::new());

    let mut rows: Vec<Row> = vec![
        // 帧率: 两边都给得出, 但口径不同 (kgsl 提交 vs SurfaceFlinger 图层)
        ("帧率 fps".into(), sp.get("fps").cloned(), ft.get("fps_mean").cloned(), "fps", "口径不同: 见下"),
        (
            "帧时均值 ms".into(),
            sp.get("frame_time_mean_ms").cloned(),
            ft.get("frame_mean").cloned(),
            "ms",
            "HiSmartPerf 侧是 1000/每秒fps 反推, 非逐帧测量",
        ),
        (
            "帧时 p95 ms".into(),
            sp.get("fps_p95_ms").cloned(),
            ft.get("frame_p95").cloned(),
            "ms",
            "不可比: 安卓侧实时流没有逐帧间隔",
        ),
        (
            "整机功耗 W".into(),
            sp.get("power_watt").cloned(),
            sysfs.get("power_watt").cloned(),
            "W",
            "同源 (battery/current_now x voltage_now), 本该对上",
        ),
    ];
    for (field, label, cands) in [
        ("soc_temp_c", "SoC 温度 C", ["soc_thermal", "soc-thermal", "soc"].as_slice()),
        ("gpu_temp_c", "GPU 温度 C", ["gpu", "gpuss-0", "gpu-thermal", "gpuss"].as_slice()),
        ("battery_temp_c", "电池温度 C", ["Battery", "battery", "batt_therm"].as_slice()),
    ] {
        let ours = cands
            .iter()
            .find_map(|c| zones.iter().find(|(k, _)| k == c).map(|(_, v)| PyVal::Float(*v)));
        rows.push((
            label.into(),
            sp.get(field).cloned(),
            ours,
            "C",
            "同源 (/sys/class/thermal), 本该对上",
        ));
    }

    let w = rows.iter().map(|r| r.0.chars().count()).max().unwrap_or(0);
    l.push(format!(
        "{} │ {} │ {} │ {} │ {} │ 说明",
        ljust("指标", w),
        rjust("HiSmartPerf", 14),
        rjust("我们的", 14),
        rjust("差", 10),
        rjust("相对", 8)
    ));
    l.push("─".repeat(w + 78));
    for (name, a, b, unit, note) in &rows {
        let (dv, dp) = delta(a.as_ref(), b.as_ref());
        l.push(format!(
            "{} │ {} │ {} │ {} │ {} │ {note}",
            ljust(name, w),
            rjust(&fmt(a.as_ref(), unit, 3), 14),
            rjust(&fmt(b.as_ref(), unit, 3), 14),
            rjust(&dv, 10),
            rjust(&dp, 8)
        ));
    }

    l.push(String::new());
    l.push("── 各通路自报的「这个字段为什么没有」 ──".into());
    l.extend(unavailable_lines(&sp, "HiSmartPerf"));
    l.extend(unavailable_lines(&sysfs, "sysfs"));

    if !zones.is_empty() {
        l.push(String::new());
        l.push("── 窗口中点直读的热区 (非零者) ──".into());
        let mut names: Vec<&(String, f64)> = zones.iter().collect();
        names.sort_by(|a, b| a.0.cmp(&b.0));
        for (k, v) in names {
            l.push(format!("  {k}: {v:.1} C"));
        }
    }

    let raw = dir.join("gp_realtime.txt");
    if raw.exists() {
        let mut fps: Vec<i64> = Vec::new();
        if let Ok(b) = std::fs::read(&raw) {
            let text = String::from_utf8_lossy(&b);
            for rec in text.split('}') {
                let Some(i) = rec.find("fps:") else { continue };
                let v = rec[i + 4..].split(';').next().unwrap_or("").trim();
                // Python 的 lstrip("-").isdigit(): 前导负号可以有, 其余必须全是数字
                let t = v.trim_start_matches('-');
                if !t.is_empty() && t.bytes().all(|c| c.is_ascii_digit()) {
                    if let Ok(n) = v.parse::<i64>() {
                        fps.push(n);
                    }
                }
            }
        }
        if !fps.is_empty() {
            l.push(String::new());
            l.push("── HiSmartPerf 逐秒 fps 原始序列 ──".into());
            l.push(format!(
                "  {}",
                fps.iter().map(|v| v.to_string()).collect::<Vec<_>>().join(" ")
            ));
            let body: &[i64] = if fps.len() > 2 { &fps[1..fps.len() - 1] } else { &fps };
            // 用 i128 累加: thermal/gp_realtime 都是 `2>&1` 重定向出来的, 内容可以是任何东西
            let mean = |xs: &[i64]| xs.iter().map(|v| *v as i128).sum::<i128>() as f64 / xs.len() as f64;
            l.push(format!(
                "  首尾两秒是不完整的秒, 照样各算一条样本 —— 去掉之后均值 {:.3} (全量 {:.3})",
                mean(body),
                mean(&fps)
            ));
        }
    }

    if let Some(PyVal::Obj(meta)) = sp.get("meta") {
        if !meta.is_empty() {
            l.push(String::new());
            l.push("── HiSmartPerf 侧采集元数据 ──".into());
            let mut ks: Vec<&(String, PyVal)> = meta.iter().collect();
            ks.sort_by(|a, b| a.0.cmp(&b.0));
            for (k, v) in ks {
                l.push(format!("  {k}: {}", v.py_str()));
            }
        }
    }

    l.join("\n")
}

const USAGE: &str = "用法: phonefarm crosscheck-report <轮目录>";

pub fn run_crosscheck_report(args: &[String]) -> i32 {
    let Some(d) = args.iter().find(|a| !a.starts_with("--")) else {
        eprintln!("{USAGE}");
        return 2;
    };
    println!("{}", report(Path::new(d)));
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("pf-cc-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// 0 不是 0 度 (没这个传感器)、-273000 是掉线哨兵、vbat 回的是毫伏。
    #[test]
    fn thermal_zones_drop_non_temperatures() {
        let d = tmp("zones");
        std::fs::write(
            d.join("t.txt"),
            "soc_thermal 45600\ngpuss-0 0\nrf-pa0 -273000\nvbat 4200\nbatt_therm 31\n\
cpu-1-0 42000\nsoc_thermal 99000\nbad line here\nnotanumber x\n",
        )
        .unwrap();
        let z = thermal_zones(&d.join("t.txt"));
        assert_eq!(
            z,
            vec![
                ("soc_thermal".to_string(), 45.6),
                ("batt_therm".to_string(), 31.0),
                ("cpu-1-0".to_string(), 42.0),
            ]
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn delta_refuses_to_invent_a_zero() {
        assert_eq!(delta(None, Some(&PyVal::Float(1.0))), ("—".into(), "—".into()));
        assert_eq!(delta(Some(&PyVal::Null), Some(&PyVal::Float(1.0))), ("—".into(), "—".into()));
        // 除数为 0 时给得出差, 给不出相对差
        assert_eq!(
            delta(Some(&PyVal::Float(2.0)), Some(&PyVal::Float(0.0))),
            ("+2.000".into(), "—".into())
        );
        assert_eq!(
            delta(Some(&PyVal::Float(1.5)), Some(&PyVal::Float(1.0))),
            ("+0.500".into(), "+50.0%".into())
        );
    }

    #[test]
    fn missing_readings_say_so_instead_of_printing_zero() {
        assert_eq!(fmt(None, "W", 3), "未测到");
        assert_eq!(fmt(Some(&PyVal::Null), "W", 3), "未测到");
        assert_eq!(fmt(Some(&PyVal::Float(1.25)), "W", 3), "1.250W");
        assert_eq!(fmt(Some(&PyVal::Int(3)), "", 3), "3.000");
    }

    /// Python 的 `if not snap` 连 null / [] / 0 / "" 一起算「整份没读到」。
    #[test]
    fn falsy_snapshot_still_says_the_path_died() {
        for v in [
            PyVal::Null,
            PyVal::Obj(vec![]),
            PyVal::List(vec![]),
            PyVal::Int(0),
            PyVal::Bool(false),
            PyVal::Str(String::new()),
        ] {
            assert_eq!(
                unavailable_lines(&v, "X"),
                vec!["  X: 这条通路整份产物都没读到".to_string()],
                "{v:?}"
            );
        }
        // 非空但没有 unavailable 字段: 不是"整份没读到", 只是没有要说明的
        assert!(unavailable_lines(&pyobj! { "a" => 1i64 }, "X").is_empty());
    }

    /// Python 印小写 nan / inf, 且 nan 一律带正号。
    #[test]
    fn non_finite_formats_like_python() {
        assert_eq!(ffmt(f64::NAN, 3, false), "nan");
        assert_eq!(ffmt(f64::NAN, 3, true), "+nan");
        assert_eq!(ffmt(-f64::NAN, 3, true), "+nan");
        assert_eq!(ffmt(f64::INFINITY, 3, false), "inf");
        assert_eq!(ffmt(f64::INFINITY, 3, true), "+inf");
        assert_eq!(ffmt(f64::NEG_INFINITY, 1, true), "-inf");
        assert_eq!(fmt(Some(&PyVal::Float(f64::NAN)), "W", 3), "nanW");
        let (d, p) = delta(Some(&PyVal::Float(f64::NAN)), Some(&PyVal::Float(1.0)));
        assert_eq!((d.as_str(), p.as_str()), ("+nan", "+nan%"));
    }

    /// i64::MIN 上 abs() 会 panic —— thermal_mid.txt 是 `2>&1` 重定向出来的,
    /// 内容可以是任何东西, 不能让一行垃圾把进程打掉。
    #[test]
    fn extreme_integers_do_not_panic() {
        let d = tmp("extreme");
        std::fs::write(
            d.join("t.txt"),
            format!("a {}
b {}
c 45600
", i64::MIN, i64::MAX),
        )
        .unwrap();
        assert_eq!(thermal_zones(&d.join("t.txt")), vec![("c".to_string(), 45.6)]);
        let _ = std::fs::remove_dir_all(&d);
    }

    /// 对照源: 旧 Python 版在同一份合成产物上的输出 (fixtures/crosscheck/)。
    #[test]
    fn report_matches_recorded_golden() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("loop_v1/fixtures/crosscheck");
        let mut checked = 0;
        let mut dirs: Vec<std::path::PathBuf> = std::fs::read_dir(&root)
            .expect("fixtures/crosscheck 不在")
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.is_dir())
            .collect();
        dirs.sort();
        for d in dirs {
            let want = d.join("crosscheck.txt");
            if !want.exists() {
                continue;
            }
            assert_eq!(
                report(&d),
                std::fs::read_to_string(&want).unwrap().trim_end_matches('\n'),
                "{} 不一致",
                d.display()
            );
            checked += 1;
        }
        assert!(checked >= 3, "只比到 {checked} 份, 太少");
    }
}
