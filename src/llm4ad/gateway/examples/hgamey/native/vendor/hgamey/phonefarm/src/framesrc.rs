//! framesrc: `phonefarm frame-sources` —— 这台设备上有哪些帧率 / 帧时来源, 各自是什么口径。
//!
//! 为什么要它: frame-source 报告 (2026-10-21) 盘出来评测正式用的帧指标只有 root 下 kgsl ftrace
//! 一条来源, 其余几条 (gfxinfo、fpsCount、不带 -PKG 的 SP_daemon) 口径各异却曾被混用。
//! 上机之前先问一句「这台机器能量到哪种帧、归属谁」, 比事后发现数的是整屏帧便宜得多。
//!
//! 口径 (`scope`):
//!   - `app`     目标应用自己的帧 (能进判定表, 带 `frame_source` 标签)
//!   - `app_ui`  目标应用 HWUI 画的界面帧 (游戏画面走 SurfaceView, 不经 HWUI; 只能看 UI 卡顿)
//!   - `global`  整机 / 整屏 (合成器口径, 不归属应用, 不能当游戏帧)
//!
//! 纪律: 只读探测 (`id`、`ls`、`dumpsys`、`hidumper`、`SP_daemon -N 3 -OUT <新文件>`), 不写系统状态,
//! 不删不覆盖设备上任何东西。判读全是纯函数, 由真机夹具钉死。

use serde::Serialize;

use crate::device::Device;
use crate::perfsrc::{self, FpsScope};
use crate::sflatency;
use crate::smartperf;
use crate::telemetry;

/// 一条来源的现状。
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SourceRow {
    /// 来源标识 (与判定表 `frame_source` 标签同名的那几条可进判定表)
    pub id: &'static str,
    /// 实际数的是什么
    pub measures: &'static str,
    /// `app` / `app_ui` / `global`
    pub scope: &'static str,
    /// 能否进判定表 (frame_p50 / frame_p95 / fps_mean)
    pub judge: bool,
    /// `available` / `unavailable` / `needs_app` / `unprobed`
    pub status: &'static str,
    /// 凭什么这么判 (实测证据或未探原因)
    pub evidence: String,
}

/// 设备端探测的原始输出。全部可选: 没探的留空, 判读时如实写「未探」。
#[derive(Debug, Clone, Default)]
pub struct Probes {
    /// `android` / `hdc` / `wda`
    pub platform: String,
    pub pkg: Option<String>,
    /// `id` 与 `su -c id` 的输出
    pub id_out: String,
    pub su_id_out: String,
    /// kgsl 提交事件节点的 `ls` 输出 (存在就打印路径)
    pub kgsl_event: String,
    /// `dumpsys SurfaceFlinger --list`
    pub sf_list: String,
    /// 探到的图层名与它的 `--latency` 原文, 或探图层失败的原因
    pub sf_latency: Option<Result<(String, String), String>>,
    /// `dumpsys gfxinfo <pkg>` 里 Total frames 那几行
    pub gfxinfo: String,
    /// `dumpsys SurfaceFlinger` 的 VSYNC / refresh-rate 行
    pub vsync: String,
    /// `SP_daemon --version`
    pub sp_version: String,
    /// `SP_daemon -N 3 -PKG <pkg> -f` 的 CSV (带包名时才探)
    pub sp_pkg_csv: Option<String>,
    /// `SP_daemon -N 3 -f` 的 CSV (整机口径)
    pub sp_global_csv: Option<String>,
    /// `hidumper -s RenderService -a fpsCount`
    pub rs_fpscount: String,
}

fn uid_is_root(id: &str) -> bool {
    id.split_whitespace()
        .find_map(|w| w.strip_prefix("uid="))
        .and_then(|v| v.split('(').next())
        .and_then(|u| u.parse::<u32>().ok())
        == Some(0)
}

fn row(id: &'static str, measures: &'static str, scope: &'static str, judge: bool, status: &'static str, evidence: String) -> SourceRow {
    SourceRow { id, measures, scope, judge, status, evidence }
}

/// 纯函数: 探测输出 → 来源表。
pub fn assess(p: &Probes) -> Vec<SourceRow> {
    match p.platform.as_str() {
        "hdc" => assess_oh(p),
        "wda" => vec![row("none", "iOS 侧没有任何帧来源", "global", false, "unavailable", "iOS 后端没有性能来源 (perfsrc::IosNoSource)".into())],
        _ => assess_android(p),
    }
}

fn assess_android(p: &Probes) -> Vec<SourceRow> {
    let mut out = Vec::new();
    let pkg_hint = || p.pkg.clone().unwrap_or_else(|| "<--app 包名>".into());

    // ① kgsl ftrace: 评测正式口径
    let root_direct = uid_is_root(&p.id_out);
    let root_su = uid_is_root(&p.su_id_out);
    let has_kgsl = p.kgsl_event.contains("adreno_cmdbatch_submitted");
    let (st, ev) = if (root_direct || root_su) && has_kgsl {
        ("available", format!("root ({}) 且 kgsl 事件节点在: {}", if root_direct { "adb shell 即 uid 0" } else { "su" }, p.kgsl_event.trim()))
    } else if !(root_direct || root_su) {
        ("unavailable", format!("没有 root (`id` → {}; `su -c id` → {})", p.id_out.trim().lines().next().unwrap_or("<空>"), p.su_id_out.trim().lines().next().unwrap_or("<空>")))
    } else {
        ("unavailable", format!("有 root 但没有 kgsl 的 adreno_cmdbatch_submitted 事件 (非高通 GPU?): {}", p.kgsl_event.trim()))
    };
    out.push(row("kgsl_ftrace", "目标进程渲染线程向 GPU 提交命令的节奏 (looptrace, 按 target_tids 认帧); 不是显示帧", "app", true, st, ev));

    // ② SurfaceFlinger --latency: 无 root 的显示帧
    let (st, ev) = match (&p.pkg, &p.sf_latency) {
        (None, _) => ("needs_app", "显示帧按图层归属, 要 --app 包名才能挑图层".to_string()),
        (Some(_), None) => ("unprobed", "没探 (设备端 dumpsys 没跑)".to_string()),
        (Some(_), Some(Err(e))) => ("unavailable", e.clone()),
        (Some(_), Some(Ok((layer, text)))) => {
            let d = sflatency::parse_latency(text);
            match sflatency::present_stats(&sflatency::merge_presents(std::slice::from_ref(&d))) {
                Some(st) => (
                    "available",
                    format!(
                        "图层 {layer}: 一次 --latency 读到 {} 帧已上屏, p50 {:.2} ms, 折合 {:.1} fps, 刷新周期 {} ns",
                        st.n_frames, st.frame_p50_ms, st.fps_mean, d.period_ns.unwrap_or(0)
                    ),
                ),
                None => ("unavailable", format!("图层 {layer}: --latency 只有 {} 帧已上屏 / {} 帧未上屏, 算不出节奏 (该层此刻没在出帧, 或此版本 --latency 已失效)", d.rows.len(), d.n_pending)),
            }
        }
    };
    out.push(row("sf_latency", "SurfaceFlinger 目标图层每帧的 actualPresentTime 间隔 (显示帧); 无需 root", "app", true, st, ev));

    // ③ gfxinfo: HWUI 界面帧
    let (st, ev) = match &p.pkg {
        None => ("needs_app", "要 --app 包名".to_string()),
        Some(_) => {
            let (frames, _, _, _) = telemetry::parse_gfxinfo_a(&p.gfxinfo);
            match frames {
                Some(n) => ("available", format!("Total frames rendered = {n} (这是 HWUI 界面帧, 游戏画面走 SurfaceView 时它基本不涨)")),
                None => ("unavailable", format!("dumpsys gfxinfo {} 没有 Total frames 行 (应用没起, 或没有 HWUI 窗口)", pkg_hint())),
            }
        }
    };
    out.push(row("gfxinfo_hwui", "目标应用 HWUI 画的界面帧累计数 (Telemetry.hwui_frames); 不是游戏帧", "app_ui", false, st, ev));

    // ④ VSYNC: 只能当刷新率
    let (hz, period) = {
        let (period, hz) = telemetry::parse_vsync_a(&p.vsync);
        (hz, period)
    };
    let (st, ev) = match (hz, period) {
        (Some(h), _) => ("available", format!("refresh-rate {h:.2} Hz")),
        (None, Some(pn)) => ("available", format!("VSYNC period {pn} ns")),
        _ => ("unavailable", "dumpsys SurfaceFlinger 里没有 VSYNC period / refresh-rate 行".to_string()),
    };
    out.push(row("sf_vsync", "面板刷新周期; 只能当刷新率, 不是帧", "global", false, st, ev));

    // ⑤ HiSmartPerf 安卓采集器: 要往设备推常驻进程, 这里不探
    out.push(row(
        "android_smartperf",
        "GamePerfToolCollector 每秒一个整数 fps (按包名找图层); 没有逐帧间隔",
        "app",
        false,
        "unprobed",
        "要往设备推采集器并常驻 (有副作用), frame-sources 不探; 用 `perf --source smartperf --app <包名>` 单独试".into(),
    ));
    out
}

fn assess_oh(p: &Probes) -> Vec<SourceRow> {
    let mut out = Vec::new();
    let sp_present = perfsrc::version_looks_present(&p.sp_version);

    // ① SP_daemon -PKG: 应用帧
    let (st, ev) = if !sp_present {
        ("unavailable", format!("SP_daemon --version → {}", p.sp_version.trim().lines().next().unwrap_or("<空>")))
    } else {
        match (&p.pkg, &p.sp_pkg_csv) {
            (None, _) => ("needs_app", "要 --app 包名 (-PKG); 华为 W1 实测 -PKG 只数目标应用的帧".to_string()),
            (Some(_), None) => ("unprobed", "没探 (设备端 SP_daemon 没跑)".to_string()),
            (Some(_), Some(csv)) => {
                let snap = perfsrc::summarize_harmony_scoped(&smartperf::parse_sp_csv(csv), FpsScope::App);
                match (&snap.frame_judge, snap.fps) {
                    (Some(j), _) => ("available", format!("{} 个采样点, fps 均值 {:.1}, 逐帧 p50 {:.2} ms / p95 {:.2} ms ({} 帧)", snap.sample_count, j.fps_mean, j.frame_p50, j.frame_p95, j.n_frames)),
                    (None, _) => {
                        let why = snap.unavailable.iter().find(|u| u.field == "fps").map(|u| u.reason.clone()).unwrap_or_else(|| "没有逐帧间隔".into());
                        if snap.sample_count == 0 {
                            ("unavailable", "SP_daemon 跑了但没有可解析的采样".to_string())
                        } else {
                            ("unavailable", format!("{} 个采样点但进不了判定表: {why}", snap.sample_count))
                        }
                    }
                }
            }
        }
    };
    out.push(row("sp_daemon_pkg", "SP_daemon -PKG 的每秒 fps 与逐帧间隔 fpsJitters (应用帧, W1 实测后台为 0)", "app", true, st, ev));

    // ② SP_daemon 不带 -PKG: 整机口径
    let (st, ev) = if !sp_present {
        ("unavailable", "没有 SP_daemon".to_string())
    } else {
        match &p.sp_global_csv {
            None => ("unprobed", "没探".to_string()),
            Some(csv) => {
                let samples = smartperf::parse_sp_csv(csv);
                let n_num = samples.iter().filter(|s| s.fps.is_some()).count();
                if samples.is_empty() {
                    ("unavailable", "没有可解析的采样".to_string())
                } else if n_num == 0 {
                    ("unavailable", format!("{} 个采样点 fps 全是 NA (华为 W1 同样: 不带 -PKG 不出数)", samples.len()))
                } else {
                    ("available", format!("{} 个采样点有 fps, 但这是整机合成口径, 不能当应用帧", n_num))
                }
            }
        }
    };
    out.push(row("sp_daemon_global", "SP_daemon 不带 -PKG 的 fps (整机合成口径)", "global", false, st, ev));

    // ③ RenderService fpsCount: 整屏
    let (hz, cnt) = telemetry::parse_fpscount_oh(&p.rs_fpscount);
    let (st, ev) = match cnt {
        Some(n) => ("available", format!("fpsCount = {n}{} (整屏刷新计数, Telemetry.screen_frames)", hz.map(|h| format!(", {h:.0} Hz")).unwrap_or_default())),
        None => ("unavailable", format!("hidumper -s RenderService -a fpsCount 没有内容 (shell 权限下只回标题行): {}", p.rs_fpscount.trim().lines().next().unwrap_or("<空>"))),
    };
    out.push(row("rs_fpscount", "RenderService 整屏刷新帧累计数; 不归属应用", "global", false, st, ev));
    out
}

/// 设备端探测 (只读)。
pub fn collect(phone: &Device, pkg: Option<&str>) -> Probes {
    let mut p = Probes { platform: phone.backend_name().to_string(), pkg: pkg.map(str::to_string), ..Default::default() };
    match p.platform.as_str() {
        "hdc" => {
            p.sp_version = phone.shell(&smartperf::version_cmd(), 10_000);
            if perfsrc::version_looks_present(&p.sp_version) {
                let ts = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
                // -OUT 写新文件 (W1 实测: 不带后缀的 CSV), 不动设备上原有的 data.csv
                if let Some(pkg) = pkg {
                    let out = format!("/data/local/tmp/pf_framesrc_{ts}_pkg");
                    phone.shell(&format!("SP_daemon -N 3 -PKG {pkg} -f -OUT {out}"), 20_000);
                    p.sp_pkg_csv = Some(phone.shell(&format!("cat {out}"), 10_000));
                }
                let out = format!("/data/local/tmp/pf_framesrc_{ts}_global");
                phone.shell(&format!("SP_daemon -N 3 -f -OUT {out}"), 20_000);
                p.sp_global_csv = Some(phone.shell(&format!("cat {out}"), 10_000));
            }
            p.rs_fpscount = phone.shell("hidumper -s RenderService -a fpsCount 2>&1 | head -20", 15_000);
        }
        "wda" => {}
        _ => {
            p.id_out = phone.shell("id", 8_000);
            p.su_id_out = phone.shell("su -c id 2>/dev/null", 8_000);
            p.kgsl_event = phone.shell(
                "for d in /sys/kernel/tracing /sys/kernel/debug/tracing; do \
                 ls -d $d/events/kgsl/adreno_cmdbatch_submitted 2>/dev/null || su -c \"ls -d $d/events/kgsl/adreno_cmdbatch_submitted\" 2>/dev/null; done",
                10_000,
            );
            p.sf_list = phone.shell(sflatency::list_cmd(), 10_000);
            p.vsync = phone.shell("dumpsys SurfaceFlinger 2>/dev/null | grep -m2 -E 'VSYNC period|refresh-rate'", 15_000);
            if let Some(pkg) = pkg {
                p.gfxinfo = phone.shell(
                    &format!("dumpsys gfxinfo {pkg} 2>/dev/null | grep -E 'Total frames rendered|Janky frames|th percentile|Number Missed Vsync'"),
                    15_000,
                );
                let mut res: Result<(String, String), String> = Err(String::new());
                let cands = sflatency::candidate_layers(&p.sf_list, pkg);
                if cands.is_empty() {
                    res = Err(format!("`{}` 里没有含 {pkg} 的图层: 应用不在前台, 或还没建出 SurfaceView", sflatency::list_cmd()));
                } else {
                    let mut tried = Vec::new();
                    for c in &cands {
                        let text = phone.shell(&sflatency::latency_cmd(c), 10_000);
                        let d = sflatency::parse_latency(&text);
                        if d.rows.len() >= 3 {
                            res = Ok((c.clone(), text));
                            break;
                        }
                        tried.push(format!("{c} ({} 已上屏 / {} 未上屏)", d.rows.len(), d.n_pending));
                    }
                    if res.is_err() {
                        res = Err(format!("{} 个候选图层都读不到已上屏的帧: {}", cands.len(), tried.join("; ")));
                    }
                }
                p.sf_latency = Some(res);
            }
        }
    }
    p
}

/// 人读的表。
pub fn render_text(platform: &str, pkg: Option<&str>, rows: &[SourceRow]) -> String {
    let mut s = format!("平台 {platform}  目标 {}\n", pkg.unwrap_or("(未给 --app)"));
    s.push_str("  来源               口径     进判定表  状态         数的是什么\n");
    for r in rows {
        s.push_str(&format!(
            "  {:<18} {:<8} {:<9} {:<12} {}\n      {}\n",
            r.id,
            r.scope,
            if r.judge { "是" } else { "否" },
            r.status,
            r.measures,
            r.evidence
        ));
    }
    s
}

/// `phonefarm frame-sources [--serial S] [--app 包名] [--json]`
///
/// 退出码: 0 = 至少一条能进判定表的来源可用; 1 = 都不可用; 2 = 参数 / 设备不可用。
pub fn run(args: &[String]) -> i32 {
    let mut serial: Option<String> = None;
    let mut pkg: Option<String> = None;
    let mut json = false;
    let mut it = args.iter();
    while let Some(k) = it.next() {
        match k.as_str() {
            "--serial" => serial = it.next().cloned(),
            "--app" | "--pkg" => pkg = it.next().cloned(),
            "--json" => json = true,
            "-h" | "--help" => {
                eprintln!("用法: phonefarm frame-sources [--serial S] [--app 包名] [--json]");
                return 2;
            }
            other => {
                eprintln!("frame-sources: 未知参数 {other}");
                return 2;
            }
        }
    }
    let tmp = std::env::temp_dir().join(format!("phonefarm-framesrc-{}", std::process::id()));
    if let Err(e) = std::fs::create_dir_all(&tmp) {
        eprintln!("建不了临时目录 {}: {e}", tmp.display());
        return 2;
    }
    let phone = Device::new(serial, tmp.to_string_lossy().to_string());
    if !phone.health_check(8_000) {
        eprintln!("设备无心跳 (devices 里不是在线态, 或未指定 --serial)");
        return 2;
    }
    let probes = collect(&phone, pkg.as_deref());
    let rows = assess(&probes);
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "platform": probes.platform, "app": pkg, "sources": rows
            }))
            .unwrap_or_default()
        );
    } else {
        print!("{}", render_text(&probes.platform, pkg.as_deref(), &rows));
    }
    if rows.iter().any(|r| r.judge && r.status == "available") {
        0
    } else {
        1
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const W1_PKG: &str = include_str!("testdata/sp_daemon_w1_genshin_fg_pkg.csv");
    const W1_GLOBAL: &str = include_str!("testdata/sp_daemon_w1_genshin_fg_global.csv");
    const W1_HOME_PKG: &str = include_str!("testdata/sp_daemon_w1_home_pkg.csv");
    const G60: &str = include_str!("testdata/sf_latency_redmagic_genshin_g60.txt");
    const A_GFX: &str = include_str!("testdata/tele_a_gfxinfo.txt");
    const A_VSYNC: &str = include_str!("testdata/tele_a_vsync.txt");
    const O_FPS: &str = include_str!("testdata/tele_oh_fpscount.txt");

    fn by_id<'a>(rows: &'a [SourceRow], id: &str) -> &'a SourceRow {
        rows.iter().find(|r| r.id == id).unwrap_or_else(|| panic!("没有 {id}"))
    }

    /// 华为 EMA-AL00U (鸿蒙 6.1, 无 root), 2026-10-21 W1 实测: 原神前台 -PKG 有数、不带 -PKG 全 NA、
    /// hidumper RenderService 在 shell 下只回标题行。
    #[test]
    fn huawei_w1_only_sp_daemon_pkg_can_enter_the_judge_table() {
        let p = Probes {
            platform: "hdc".into(),
            pkg: Some("com.miHoYo.Yuanshen".into()),
            sp_version: "1.0.3".into(),
            sp_pkg_csv: Some(W1_PKG.into()),
            sp_global_csv: Some(W1_GLOBAL.into()),
            rs_fpscount: "-------------------------------[ability]-------------------------------\n".into(),
            ..Default::default()
        };
        let rows = assess(&p);
        let pk = by_id(&rows, "sp_daemon_pkg");
        assert_eq!((pk.status, pk.judge, pk.scope), ("available", true, "app"));
        assert!(pk.evidence.contains("fps 均值 30.0"), "{}", pk.evidence);
        let g = by_id(&rows, "sp_daemon_global");
        assert_eq!((g.status, g.judge, g.scope), ("unavailable", false, "global"));
        assert!(g.evidence.contains("NA"), "{}", g.evidence);
        let rs = by_id(&rows, "rs_fpscount");
        assert_eq!((rs.status, rs.judge), ("unavailable", false));
    }

    /// 同一台华为, 原神退到后台: -PKG 的 fps 全 0 —— 来源在、但目标没在出帧, 要说清楚。
    #[test]
    fn huawei_w1_background_app_is_reported_as_not_drawing() {
        let p = Probes {
            platform: "hdc".into(),
            pkg: Some("com.miHoYo.Yuanshen".into()),
            sp_version: "1.0.3".into(),
            sp_pkg_csv: Some(W1_HOME_PKG.into()),
            ..Default::default()
        };
        let pk = assess(&p).into_iter().find(|r| r.id == "sp_daemon_pkg").unwrap();
        assert_eq!(pk.status, "unavailable");
        assert!(pk.evidence.contains("没在出帧"), "{}", pk.evidence);
    }

    #[test]
    fn harmony_fpscount_fixture_is_a_global_screen_counter() {
        let p = Probes { platform: "hdc".into(), sp_version: "not found".into(), rs_fpscount: O_FPS.into(), ..Default::default() };
        let rows = assess(&p);
        let rs = by_id(&rows, "rs_fpscount");
        assert_eq!((rs.status, rs.scope, rs.judge), ("available", "global", false));
        assert!(rs.evidence.contains("3497"), "{}", rs.evidence);
        assert_eq!(by_id(&rows, "sp_daemon_pkg").status, "unavailable");
    }

    /// 无 root 安卓 (荣耀那类): kgsl 不可用, sf_latency 有数就能进判定表, gfxinfo 只是界面帧。
    /// sf_latency 原文是红魔实采 (荣耀上尚未跑过), 判读逻辑与机型无关。
    #[test]
    fn android_without_root_gets_display_frames_from_sf_latency() {
        let p = Probes {
            platform: "android".into(),
            pkg: Some("com.miHoYo.Yuanshen".into()),
            id_out: "uid=2000(shell) gid=2000(shell)".into(),
            su_id_out: String::new(),
            kgsl_event: String::new(),
            sf_latency: Some(Ok(("SurfaceView[com.miHoYo.Yuanshen/x](BLAST)#0".into(), G60.into()))),
            gfxinfo: A_GFX.into(),
            vsync: A_VSYNC.into(),
            ..Default::default()
        };
        let rows = assess(&p);
        let k = by_id(&rows, "kgsl_ftrace");
        assert_eq!(k.status, "unavailable");
        assert!(k.evidence.contains("没有 root"), "{}", k.evidence);
        let sf = by_id(&rows, "sf_latency");
        assert_eq!((sf.status, sf.judge, sf.scope), ("available", true, "app"));
        assert!(sf.evidence.contains("p50 16.6"), "{}", sf.evidence);
        let g = by_id(&rows, "gfxinfo_hwui");
        assert_eq!((g.status, g.judge, g.scope), ("available", false, "app_ui"));
        assert!(g.evidence.contains("284"), "{}", g.evidence);
        assert_eq!(by_id(&rows, "sf_vsync").judge, false);
        assert_eq!(by_id(&rows, "android_smartperf").status, "unprobed");
        assert!(rows.iter().any(|r| r.judge && r.status == "available"));
    }

    #[test]
    fn android_with_root_and_kgsl_but_no_app_still_needs_app_for_layer_sources() {
        let p = Probes {
            platform: "android".into(),
            pkg: None,
            id_out: "uid=2000(shell)".into(),
            su_id_out: "uid=0(root) gid=0(root)".into(),
            kgsl_event: "/sys/kernel/tracing/events/kgsl/adreno_cmdbatch_submitted".into(),
            ..Default::default()
        };
        let rows = assess(&p);
        assert_eq!(by_id(&rows, "kgsl_ftrace").status, "available");
        assert_eq!(by_id(&rows, "sf_latency").status, "needs_app");
        assert_eq!(by_id(&rows, "gfxinfo_hwui").status, "needs_app");
    }

    #[test]
    fn ios_has_no_frame_source_and_text_render_lists_every_row() {
        let rows = assess(&Probes { platform: "wda".into(), ..Default::default() });
        assert_eq!(rows.len(), 1);
        assert!(!rows[0].judge);
        let t = render_text("wda", None, &rows);
        assert!(t.contains("none") && t.contains("未给 --app"));
    }
}
