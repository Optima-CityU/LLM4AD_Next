//! sflatency: 安卓无 root 的显示帧来源 —— `dumpsys SurfaceFlinger --latency <图层>`。
//!
//! 为什么要它: 评测正式的帧指标只有 root 下的 kgsl ftrace 一条来源, 而且它数的是
//! GPU 提交节奏, 不是显示帧 (frame-source 报告 1.1)。荣耀 AAP-AN00 这类没有 root 的机器
//! 在判定链里没有任何帧来源。SurfaceFlinger 按**图层**记每帧的 desiredPresent /
//! actualPresent / frameReady 三个时刻, shell 权限就能读, 图层名带包名 —— 天然归属目标应用,
//! 满足 HGamey/.github#20「显示帧必须能归属目标游戏」。
//!
//! 口径: `actualPresentTime` 相邻两帧之差 = 显示帧间隔。`--latency` 每次只回最近 128 帧,
//! 所以要轮询 (60 fps 时 128 帧约 2.1 s, 120 fps 约 1.07 s; 每 500 ms 读一次有重叠),
//! 合并后按时刻去重。轮询漏掉的空洞 (> 1 s) 不算帧间隔, 单独计数报出。
//!
//! 已知边界: ftrace.rs 文件头记有「`--latency` 在 Android 16 上已失效」—— 那是红魔上的一次
//! 实测, 但 2026-10-21 红魔上 `rehearsal` 的 o1 刷新率实验又用同一条命令拿到了逐帧数据
//! (夹具 `testdata/sf_latency_redmagic_genshin_g60.txt`)。所以这条来源**每台机器都要先探**
//! (`phonefarm frame-sources`), 探到有数才用。荣耀真机上尚未跑过 (未验证)。
//!
//! 纪律: 解析全是纯函数; 采集只读 (`dumpsys`), 不写设备上任何东西。

use crate::device::Device;
use crate::gpustat;
use crate::perfsrc::{self, PerfSnapshot, PerfSource, FRAME_SRC_SF_LATENCY, SRC_ANDROID_SF_LATENCY};

/// 列图层。
pub fn list_cmd() -> &'static str {
    "dumpsys SurfaceFlinger --list"
}

/// 读一个图层最近 128 帧的三元时刻。图层名含 `[` `]` `(` `)` `#`, 要整个单引号括起来。
pub fn latency_cmd(layer: &str) -> String {
    format!("dumpsys SurfaceFlinger --latency '{}'", layer.replace('\'', "'\\''"))
}

/// 从 `--list` 输出里挑目标包的候选图层, 按「最可能有缓冲区」排序:
/// `SurfaceView[<pkg>/...]` 的 `(BLAST Consumer)` 行 > `(BLAST)#` 行 > 其它 SurfaceView 行 > 其它含包名的行。
///
/// 游戏画面走 SurfaceView, 真正 present 缓冲区的是 BLAST 那一层; 父层 `SurfaceView[...]#N` 没有帧。
/// 挑出来的只是候选 —— 哪一层真有数要用 `--latency` 试 ([`probe_layer`]), 不同机型层名不一样。
pub fn candidate_layers(list: &str, pkg: &str) -> Vec<String> {
    let needle = format!("[{pkg}/");
    let mut scored: Vec<(i32, usize, String)> = Vec::new();
    for (i, line) in list.lines().enumerate() {
        let l = line.trim();
        // ColorOS wraps the real name in RequestedLayerState plus metadata.
        // --latency accepts the name itself, not the --list display wrapper.
        let l = l.strip_prefix("RequestedLayerState{").and_then(|s| s.strip_suffix('}'))
            .map(|s| s.split(" parentId=").next().unwrap_or(s))
            .unwrap_or(l);
        if l.is_empty() || !(l.contains(&needle) || l.starts_with(&format!("{pkg}/"))) {
            continue;
        }
        let sv = l.starts_with("SurfaceView[") && l.contains(&needle);
        let score = if sv && l.contains("(BLAST Consumer)") {
            4
        } else if sv && l.contains("(BLAST)#") {
            3
        } else if sv {
            2
        } else if l.contains(&needle) {
            1
        } else {
            0
        };
        scored.push((score, i, l.to_string()));
    }
    // 分高在前; 同分按出现顺序倒序 (后列的通常是更新的实例)
    scored.sort_by(|a, b| b.0.cmp(&a.0).then(b.1.cmp(&a.1)));
    scored.into_iter().map(|(_, _, l)| l).collect()
}

/// 一次 `--latency` 的解析结果。
#[derive(Debug, Clone, PartialEq, Default)]
pub struct LatencyDump {
    /// 首行的刷新周期 (ns); 文件里拼了多次 dump 时取第一次的
    pub period_ns: Option<i64>,
    /// `(desiredPresent, actualPresent, frameReady)`, 只收已上屏的行
    pub rows: Vec<(i64, i64, i64)>,
    /// 被跳过的未上屏行 (actualPresent 为 0 或 INT64_MAX)
    pub n_pending: usize,
}

/// 解析 `--latency` 输出。宽松口径: 单独一个整数的行 = 刷新周期, 三个整数的行 = 一帧,
/// 其余行 (`LAYER ...` 之类调用方加的标记、空行) 忽略。多次 dump 拼在一起也能读。
pub fn parse_latency(text: &str) -> LatencyDump {
    let mut d = LatencyDump::default();
    for line in text.lines() {
        let p: Vec<&str> = line.split_whitespace().collect();
        match p.as_slice() {
            [one] => {
                if let Ok(v) = one.parse::<i64>() {
                    if d.period_ns.is_none() && v > 0 {
                        d.period_ns = Some(v);
                    }
                }
            }
            [a, b, c] => {
                let (Ok(x), Ok(y), Ok(z)) = (a.parse::<i64>(), b.parse::<i64>(), c.parse::<i64>()) else {
                    continue;
                };
                // 还没上屏的帧: actualPresent 是 0 或 INT64_MAX (有的版本是 1<<62 以上)
                if y <= 0 || y >= (1i64 << 62) {
                    d.n_pending += 1;
                    continue;
                }
                d.rows.push((x, y, z));
            }
            _ => {}
        }
    }
    d
}

/// 多次轮询的 actualPresent 合并: 排序去重。
pub fn merge_presents(dumps: &[LatencyDump]) -> Vec<i64> {
    let mut t: Vec<i64> = dumps.iter().flat_map(|d| d.rows.iter().map(|r| r.1)).collect();
    t.sort_unstable();
    t.dedup();
    t
}

/// 轮询空洞的判据: 相邻两次上屏相隔超过这个数, 不当帧间隔 (是漏采或场景切换)。
pub const GAP_MS: f64 = 1000.0;

/// 显示帧节奏。
#[derive(Debug, Clone, PartialEq)]
pub struct PresentStats {
    pub n_frames: usize,
    pub span_s: f64,
    pub fps_mean: f64,
    pub frame_mean_ms: f64,
    pub frame_p50_ms: f64,
    pub frame_p95_ms: f64,
    pub frame_p99_ms: f64,
    pub frame_max_ms: f64,
    /// 帧间隔 > 1.5 × p50 的比例 (%)
    pub jank_pct: f64,
    /// 被剔掉的 > [`GAP_MS`] 空洞数
    pub n_gaps: usize,
    /// 逐帧间隔 (毫秒), 空洞已剔除
    pub intervals_ms: Vec<f64>,
}

/// 上屏时刻 (已排序去重) → 节奏指标。少于 3 帧 (2 个间隔) 算不出分布, 返回 None。
pub fn present_stats(times_ns: &[i64]) -> Option<PresentStats> {
    let mut t = times_ns.to_vec();
    t.sort_unstable();
    t.dedup();
    if t.len() < 3 {
        return None;
    }
    let mut dts = Vec::with_capacity(t.len());
    let mut gaps = 0usize;
    for w in t.windows(2) {
        let dt = (w[1] - w[0]) as f64 / 1e6;
        if dt > GAP_MS {
            gaps += 1;
        } else {
            dts.push(dt);
        }
    }
    if dts.len() < 2 {
        return None;
    }
    let p50 = gpustat::percentile(&dts, 0.50);
    let jank = dts.iter().filter(|d| **d > 1.5 * p50).count() as f64 / dts.len() as f64 * 100.0;
    let span_s: f64 = dts.iter().sum::<f64>() / 1000.0;
    Some(PresentStats {
        n_frames: dts.len() + 1,
        span_s,
        fps_mean: if span_s > 0.0 { dts.len() as f64 / span_s } else { f64::NAN },
        frame_mean_ms: gpustat::mean(&dts),
        frame_p50_ms: p50,
        frame_p95_ms: gpustat::percentile(&dts, 0.95),
        frame_p99_ms: gpustat::percentile(&dts, 0.99),
        frame_max_ms: dts.iter().cloned().fold(f64::MIN, f64::max),
        jank_pct: jank,
        n_gaps: gaps,
        intervals_ms: dts,
    })
}

/// 探一个图层: 读一次 `--latency`, 至少 2 帧已上屏才算「有数」。
pub fn probe_layer(phone: &Device, layer: &str) -> LatencyDump {
    parse_latency(&phone.shell(&latency_cmd(layer), 10_000))
}

/// 在候选里找第一个真有帧的图层。返回 (图层名, 首次 dump)。
pub fn find_layer(phone: &Device, pkg: &str, explicit: Option<&str>) -> Result<(String, LatencyDump), String> {
    let cands: Vec<String> = match explicit {
        Some(l) => vec![l.to_string()],
        None => candidate_layers(&phone.shell(list_cmd(), 10_000), pkg),
    };
    if cands.is_empty() {
        return Err(format!("`{}` 里没有含 {pkg} 的图层: 应用不在前台, 或还没建出 SurfaceView", list_cmd()));
    }
    let mut tried = Vec::new();
    for c in &cands {
        let d = probe_layer(phone, c);
        if d.rows.len() >= 2 {
            return Ok((c.clone(), d));
        }
        tried.push(format!("{c} ({} 帧已上屏, {} 帧未上屏)", d.rows.len(), d.n_pending));
    }
    Err(format!(
        "{} 个候选图层都读不到已上屏的帧 (`--latency` 在这台机器上可能已失效, 或该图层此刻没在出帧): {}",
        cands.len(),
        tried.join("; ")
    ))
}

/// 轮询 `seconds` 秒, 每 [`POLL_MS`] 读一次。
pub const POLL_MS: u64 = 500;

pub fn sample(phone: &Device, layer: &str, seconds: u32) -> Vec<LatencyDump> {
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(seconds.max(1) as u64);
    let mut out = Vec::new();
    loop {
        out.push(probe_layer(phone, layer));
        if std::time::Instant::now() >= deadline {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(POLL_MS));
    }
    out
}

/// 纯函数: 多次 dump + 图层名 → 归一化快照。只出帧, 功耗与温度如实缺失。
pub fn summarize(dumps: &[LatencyDump], layer: &str) -> PerfSnapshot {
    let mut snap = PerfSnapshot::empty(SRC_ANDROID_SF_LATENCY);
    snap.sample_count = dumps.len();
    let mut meta = std::collections::BTreeMap::new();
    meta.insert("layer".to_string(), layer.to_string());
    meta.insert("fps_scope".to_string(), "app".to_string());
    if let Some(p) = dumps.iter().find_map(|d| d.period_ns) {
        meta.insert("vsync_period_ns".to_string(), p.to_string());
        if p > 0 {
            meta.insert("refresh_hz".to_string(), format!("{:.2}", 1e9 / p as f64));
        }
    }
    let times = merge_presents(dumps);
    meta.insert("n_present_times".to_string(), times.len().to_string());
    match present_stats(&times) {
        Some(st) => {
            snap.fps = Some(st.fps_mean);
            snap.frame_time_mean_ms = Some(st.frame_mean_ms);
            snap.fps_p95_ms = Some(st.frame_p95_ms);
            meta.insert("n_gaps".to_string(), st.n_gaps.to_string());
            meta.insert("frame_max_ms".to_string(), format!("{:.3}", st.frame_max_ms));
            snap.frame_judge =
                perfsrc::frame_judge_from_intervals(&st.intervals_ms, Some(st.fps_mean), FRAME_SRC_SF_LATENCY);
        }
        None => {
            let why = format!("图层 {layer} 合并后只有 {} 个上屏时刻, 算不出帧节奏", times.len());
            for f in ["fps", "frame_time_mean_ms", "fps_p95_ms", "frame_judge"] {
                snap.unavailable.push(perfsrc::Unavailable { field: f.into(), reason: why.clone() });
            }
        }
    }
    let only_frames = "这条通路只量显示帧 (dumpsys SurfaceFlinger --latency), 不出功耗与温度";
    for f in ["power_watt", "soc_temp_c", "gpu_temp_c", "battery_temp_c"] {
        snap.unavailable.push(perfsrc::Unavailable { field: f.into(), reason: only_frames.into() });
    }
    snap.meta = Some(meta);
    snap
}

/// 安卓来源: SurfaceFlinger 图层的显示帧。无需 root。
pub struct AndroidSfLatency {
    /// 目标包名 (必填: 图层按它挑)
    pub pkg: Option<String>,
    /// 显式图层名; 不给按包名从 `--list` 里挑并逐个试
    pub layer: Option<String>,
}

impl PerfSource for AndroidSfLatency {
    fn id(&self) -> &'static str {
        SRC_ANDROID_SF_LATENCY
    }

    fn collect(&self, phone: &Device, rounds: u32) -> PerfSnapshot {
        let Some(pkg) = self.pkg.as_deref().filter(|p| !p.trim().is_empty()) else {
            let mut snap = PerfSnapshot::empty(SRC_ANDROID_SF_LATENCY);
            snap.unavailable.push(perfsrc::Unavailable {
                field: perfsrc::FIELD_PIPELINE.into(),
                reason: "sflatency 通路要 --app 包名: 显示帧按图层归属, 没有图层就没有口径".into(),
            });
            return snap;
        };
        let (layer, first) = match find_layer(phone, pkg, self.layer.as_deref()) {
            Ok(x) => x,
            Err(e) => {
                let mut snap = PerfSnapshot::empty(SRC_ANDROID_SF_LATENCY);
                snap.unavailable.push(perfsrc::Unavailable { field: perfsrc::FIELD_PIPELINE.into(), reason: e });
                return snap;
            }
        };
        let mut dumps = vec![first];
        dumps.extend(sample(phone, &layer, rounds));
        summarize(&dumps, &layer)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 红魔 + 原神, 游戏帧率上限 60、面板 120 Hz, 2026-10-21 rehearsal 的 o1 刷新率实验实采
    /// (`dumpsys SurfaceFlinger --latency` 读两次, 相隔约 23 s)。
    const G60: &str = include_str!("testdata/sf_latency_redmagic_genshin_g60.txt");

    #[test]
    fn parses_the_real_redmagic_dump_and_skips_pending_rows() {
        let d = parse_latency(G60);
        assert_eq!(d.period_ns, Some(8_333_333), "面板 120 Hz");
        // 两次 dump 各 128 行, 未上屏的行不收
        assert!(d.rows.len() + d.n_pending == 256, "{} + {}", d.rows.len(), d.n_pending);
        assert!(d.rows.len() >= 200, "已上屏 {}", d.rows.len());
        assert!(d.rows.iter().all(|r| r.1 > 0 && r.1 < (1 << 62)));
    }

    #[test]
    fn genshin_at_60_cap_on_a_120hz_panel_presents_every_16_7ms() {
        let d = parse_latency(G60);
        let t = merge_presents(std::slice::from_ref(&d));
        let st = present_stats(&t).expect("够帧");
        // 夹具实测: 两次读相隔约 23 s, 中间是一个 > 1 s 的空洞 (不当帧间隔);
        // 248 个间隔里 239 个 16.7 ms、8 个 33.3 ms (掉了一个 vsync), 所以均值帧率约 58 不是 60
        assert!(st.n_frames > 128 && st.n_frames <= 256, "{}", st.n_frames);
        assert!((st.frame_p50_ms - 16.667).abs() < 0.2, "p50 {}", st.frame_p50_ms);
        assert!(st.frame_p95_ms < 17.5, "p95 {}", st.frame_p95_ms);
        assert!(st.fps_mean > 57.5 && st.fps_mean < 59.0, "fps {}", st.fps_mean);
        assert_eq!(st.n_gaps, 1, "两次读之间的 23 s 空洞");
        assert_eq!(st.intervals_ms.iter().filter(|d| **d > 30.0).count(), 8, "8 次掉帧 (33.3 ms)");
        assert!(st.jank_pct > 3.0 && st.jank_pct < 3.5, "jank {}", st.jank_pct);
    }

    #[test]
    fn summary_enters_the_judge_table_tagged_sf_latency() {
        let d = parse_latency(G60);
        let s = summarize(&[d], "SurfaceView[com.miHoYo.Yuanshen/x](BLAST)#0");
        let j = s.frame_judge.expect("有逐帧数据就该进判定表");
        assert_eq!(j.frame_source, FRAME_SRC_SF_LATENCY);
        assert!((j.frame_p50 - 16.667).abs() < 0.2);
        assert_eq!(s.fps.map(|v| v.round()), Some(58.0), "8 次掉帧, 见上一条");
        let m = s.meta.unwrap();
        assert_eq!(m.get("fps_scope").map(String::as_str), Some("app"));
        assert_eq!(m.get("refresh_hz").map(String::as_str), Some("120.00"));
        assert!(s.power_watt.is_none() && s.unavailable.iter().any(|u| u.field == "power_watt"));
    }

    #[test]
    fn pending_and_zero_rows_and_gaps_are_not_frames() {
        let txt = "16666666\n0\t0\t0\n100\t9223372036854775807\t100\n1000000000\t1000000000\t1000000000\n\
                   1016666666\t1016666666\t1016666666\n1033333332\t1033333332\t1033333332\n\
                   5000000000\t5000000000\t5000000000\n5016666666\t5016666666\t5016666666\n";
        let d = parse_latency(txt);
        assert_eq!(d.n_pending, 2);
        assert_eq!(d.rows.len(), 5);
        let st = present_stats(&merge_presents(&[d])).unwrap();
        assert_eq!(st.n_gaps, 1, "4 s 的空洞不是帧间隔");
        assert_eq!(st.intervals_ms.len(), 3);
        assert!((st.frame_p50_ms - 16.667).abs() < 0.01);
        // 两次读重叠的帧按时刻去重
        let d2 = parse_latency(txt);
        let dd = parse_latency(txt);
        assert_eq!(merge_presents(&[d2, dd]).len(), 5);
    }

    #[test]
    fn too_few_frames_is_none_not_a_number() {
        assert!(present_stats(&[1, 2]).is_none());
        assert!(present_stats(&[]).is_none());
        let s = summarize(&[parse_latency("16666666\n")], "x");
        assert!(s.fps.is_none() && s.frame_judge.is_none());
        assert!(s.unavailable.iter().any(|u| u.field == "fps"));
    }

    #[test]
    fn candidate_layers_prefer_the_blast_buffer_layer_of_the_package() {
        let list = "Wallpaper BBQ wrapper#0\n\
                    com.miHoYo.Yuanshen/com.miHoYo.GetMobileInfo.MainActivity#0\n\
                    SurfaceView[com.miHoYo.Yuanshen/com.miHoYo.GetMobileInfo.MainActivity]#0\n\
                    SurfaceView[com.miHoYo.Yuanshen/com.miHoYo.GetMobileInfo.MainActivity](BLAST)#0\n\
                    SurfaceView[com.miHoYo.Yuanshen/com.miHoYo.GetMobileInfo.MainActivity](BLAST)#0(BLAST Consumer)0\n\
                    SurfaceView[com.other.app/x.Main](BLAST)#0\n\
                    StatusBar#0\n";
        let c = candidate_layers(list, "com.miHoYo.Yuanshen");
        assert_eq!(c.len(), 4);
        assert!(c[0].ends_with("(BLAST Consumer)0"), "{}", c[0]);
        assert!(c[1].ends_with("(BLAST)#0"), "{}", c[1]);
        assert!(c[2].starts_with("SurfaceView[") && c[2].ends_with("]#0"));
        assert!(c[3].starts_with("com.miHoYo"));
        assert!(c.iter().all(|l| !l.contains("com.other.app")));
        assert!(candidate_layers(list, "com.nothing").is_empty());
    }

    #[test]
    fn latency_cmd_quotes_the_layer_name() {
        assert_eq!(
            latency_cmd("SurfaceView[a/b](BLAST)#0"),
            "dumpsys SurfaceFlinger --latency 'SurfaceView[a/b](BLAST)#0'"
        );
    }

    #[test]
    fn coloros_requested_layer_wrappers_are_not_latency_names() {
        let list = "RequestedLayerState{SurfaceView[com.test.game/Main](BLAST)#196 parentId=195}\nRequestedLayerState{SurfaceView[com.test.game/Main]#195 parentId=194 relativeParentId=191 z=-2}\nRequestedLayerState{SurfaceView[com.test.game.fake/Main](BLAST)#200 parentId=199}\n";
        assert_eq!(candidate_layers(list, "com.test.game"), vec![
            "SurfaceView[com.test.game/Main](BLAST)#196",
            "SurfaceView[com.test.game/Main]#195",
        ]);
    }
}
