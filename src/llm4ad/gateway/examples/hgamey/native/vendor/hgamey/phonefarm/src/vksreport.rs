//! Vulkan-Samples 两臂对照的判读面 (纯函数链, 不碰设备)。
//!
//! 由 `loop_v1/carriers/vulkan-samples/vks_report.py` 搬过来, 输出字节一致。
//! 只做一件事: 把 `phonefarm analyze` 已经算好的统计量, 对上**这个开关在上游源码里
//! 到底改了什么**, 然后如实说"测出来了 / 没测出来", 不做二次统计。
//!
//! KNOBS / METRIC_MEANING 抽成了 `loop_v1/carriers/vulkan-samples/rules.json`
//! (数据, 不是代码), 报告头里的判读哈希就是**这份文件的 sha256** —— 改任何一条
//! 档位语义, 新报告的哈希跟着变。
//!
//! **口径切换点**: 生成过现存 report.txt 的旧判读源码 (2edf058 版) 原封留在
//! `rules_frozen.py`, 归档 report.txt 里的哈希 (ef6202a5…) 对应**它**而不对应
//! rules.json —— 切换点之前的归档在重算时会恰好差哈希一行, 这是记录在案的口径
//! 切换, 不是重算坏了 (见 src/refbenchreport.rs 的模块注释, 同一套办法)。

use crate::pyjson::{loads, PyVal};
use crate::pyobj;
use regex::Regex;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

/// 判读规则冻结在 `loop_v1/carriers/vulkan-samples/rules.json`。
/// 报告头里的判读哈希就是**这份文件的 sha256**。
const RULES_TEXT: &str = include_str!("../loop_v1/carriers/vulkan-samples/rules.json");

fn sha256_self() -> String {
    let mut h = Sha256::new();
    h.update(RULES_TEXT.as_bytes());
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// 从冻结的规则文件取判读规则 (键序保序, afbc 里的 `_note` 这类非档位键自然跳过)。
fn load_rules() -> PyVal {
    crate::pyjson::loads(RULES_TEXT).unwrap_or(PyVal::Null)
}

fn knob_desc(rules: &PyVal, sample: &str, cfg: i64) -> String {
    rules
        .get("knobs")
        .and_then(|k| k.get(sample))
        .and_then(|opts| match opts {
            PyVal::Obj(kvs) => kvs
                .iter()
                .find(|(k, _)| k.parse::<i64>().map(|x| x == cfg).unwrap_or(false))
                .map(|(_, v)| v.py_str()),
            _ => None,
        })
        .unwrap_or_else(|| "<未登记>".into())
}

/// Adreno 驱动发提交的那个线程叫 binder:<pid>_<槽位>: pid 每次启动都不一样, 槽位是
/// binder 线程池里恰好轮到哪一个, 两者都不携带口径信息。比较时必须先归一化, 否则
/// "每轮线程名都不同"会永远误报成"两臂口径不一致"。真正要拦的是**类别**变了 ——
/// 比如从应用自己的提交线程变成了 kgsl_hwsched 或 SurfaceFlinger 的 RenderEngine。
pub fn normalize_comm(comm: Option<&str>) -> String {
    let Some(comm) = comm.filter(|c| !c.is_empty()) else {
        return "<none>".into();
    };
    let re = Regex::new(r"^binder:\d+_\d+$").expect("binder re");
    if re.is_match(comm) {
        "binder:<应用提交线程>".into()
    } else {
        comm.to_string()
    }
}

fn load_json(path: &Path) -> Option<PyVal> {
    loads(&std::fs::read_to_string(path).ok()?).ok()
}

/// glob 的最小够用版: 目录下按名字排序, 通配符不吃隐藏文件 (与 loopstat::fnmatch 同规)。
fn sorted_children(root: &Path, tail: &str) -> Vec<(String, PathBuf)> {
    let pat = tail.trim_end_matches('*');
    let mut out: Vec<(String, PathBuf)> = std::fs::read_dir(root)
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .filter_map(|e| {
            let n = e.file_name().to_str()?.to_string();
            (!n.starts_with('.') && n.starts_with(pat)).then(|| (n, e.path()))
        })
        .collect();
    out.sort_by(|a, b| a.1.as_os_str().cmp(b.1.as_os_str()));
    out
}

/// 每轮认出来的提交线程名(已抹掉 pid) —— 两臂必须是同一类线程, 否则对照不成立。
/// (归一化线程名, [(轮名, 占比)])
pub type CommGroups = Vec<(String, Vec<(String, Option<f64>)>)>;

pub fn collect_comms(root: &Path) -> CommGroups {
    let mut out: CommGroups = Vec::new();
    for (dir, _) in sorted_children(root, "*") {
        let Some(d) = load_json(&root.join(&dir).join("comm.json")) else {
            continue;
        };
        let key = normalize_comm(d.get("comm").map(|v| v.py_str()).as_deref());
        let share = d.get("share").and_then(|v| v.as_f64());
        match out.iter_mut().find(|(k, _)| *k == key) {
            Some(slot) => slot.1.push((dir, share)),
            None => out.push((key, vec![(dir, share)])),
        }
    }
    out
}

/// 每轮的风扇状态 —— 红魔的主动散热风扇自身耗电会进功耗读数, 且它不在 38 行快照里。
/// 同一组对照的两臂必须是同一风扇状态, 否则"风扇开/关"会混进臂间差异。
pub fn collect_fans(root: &Path) -> Vec<(String, Vec<String>)> {
    let mut out: Vec<(String, Vec<String>)> = Vec::new();
    for (dir, _) in sorted_children(root, "*") {
        let Some(d) = load_json(&root.join(&dir).join("fan.json")) else {
            continue;
        };
        let key = format!(
            "enable={} level={}",
            d.get("fan_enable").map(|v| v.py_str()).unwrap_or("None".into()),
            d.get("fan_speed_level").map(|v| v.py_str()).unwrap_or("None".into())
        );
        match out.iter_mut().find(|(k, _)| *k == key) {
            Some(slot) => slot.1.push(dir),
            None => out.push((key, vec![dir])),
        }
    }
    out
}

/// 每轮的两个**会悄悄污染逐帧指标**的条件, 逐轮列出来而不是藏在 summary 里。
///
/// · submits_per_frame: parse-trace 自检出的"每帧几次提交"。所有逐帧指标都是按它
///   分组算出来的, 它一变这些数的口径就变。2026-09-23 实测同一个负载的 10 轮里它
///   取过 1 和 6 —— 取 6 的那轮 fps 被算成 9.9、frame_p50 算成 99.9ms。
/// · log_fps_median: 应用自报帧率。开 vsync 时它等于面板刷新率, 而本机面板是自适应
///   刷新的, 实测同一批里出现过 60 / 99.5 / 120 三档 —— 帧预算不一样。
pub fn collect_run_conditions(root: &Path) -> PyVal {
    let mut rounds: Vec<PyVal> = Vec::new();
    for (dir, _) in sorted_children(root, "*") {
        let Some(s) = load_json(&root.join(&dir).join("summary.json")) else {
            continue;
        };
        let cc = load_json(&root.join(&dir).join("crosscheck.json")).unwrap_or(PyVal::Obj(vec![]));
        rounds.push(pyobj! {
            "round" => dir,
            "spf" => s.get("submits_per_frame").cloned().unwrap_or(PyVal::Null),
            "log_fps" => cc.get("log_fps_median").cloned().unwrap_or(PyVal::Null),
            "bw_median" => s.get("bw_median").cloned().unwrap_or(PyVal::Null),
        });
    }
    let mut spf_set: Vec<i64> = rounds
        .iter()
        .filter_map(|r| r.get("spf").and_then(|v| v.as_i64()))
        .collect();
    spf_set.sort();
    spf_set.dedup();
    // Python: sorted({round(x) for ...}) —— banker's rounding 到整数
    let mut log_fps_set: Vec<i64> = rounds
        .iter()
        .filter_map(|r| r.get("log_fps").and_then(|v| v.as_f64()))
        .map(|x| crate::pyjson::py_round(x, 0) as i64)
        .collect();
    log_fps_set.sort();
    log_fps_set.dedup();
    pyobj! {
        "rounds" => PyVal::List(rounds),
        "spf_set" => spf_set,
        "log_fps_set" => log_fps_set,
    }
}

/// 每轮的面板刷新率设置 —— 开着 vsync 时应用帧率就是它, 各轮不一致则逐帧指标不可比。
pub fn collect_displays(root: &Path) -> Vec<(String, Vec<String>)> {
    let mut out: Vec<(String, Vec<String>)> = Vec::new();
    for (dir, _) in sorted_children(root, "*") {
        let Some(d) = load_json(&root.join(&dir).join("display.json")) else {
            continue;
        };
        let key = format!(
            "min={} peak={}",
            d.get("min_refresh_rate").map(|v| v.py_str()).unwrap_or("None".into()),
            d.get("peak_refresh_rate").map(|v| v.py_str()).unwrap_or("None".into())
        );
        match out.iter_mut().find(|(k, _)| *k == key) {
            Some(slot) => slot.1.push(dir),
            None => out.push((key, vec![dir])),
        }
    }
    out
}

/// Python 的 `f"{x:<18}"` / `{x:>12}` 按字符数排版, 数字走 str()。
fn ljust(s: &str, w: usize) -> String {
    format!("{s}{}", " ".repeat(w.saturating_sub(s.chars().count())))
}
fn rjust(s: &str, w: usize) -> String {
    format!("{}{s}", " ".repeat(w.saturating_sub(s.chars().count())))
}
fn field(v: Option<&PyVal>, w: usize) -> String {
    rjust(&v.map(|x| x.py_str()).unwrap_or("None".into()), w)
}

pub fn report(root: &Path, sample: &str, cfg_a: i64, cfg_b: i64) -> Result<String, String> {
    let apath = root.join("analyze.json");
    if !apath.exists() {
        return Err(format!("缺 {} —— 先跑 run_ab.sh", apath.display()));
    }
    let an = load_json(&apath).unwrap_or(PyVal::Obj(vec![]));

    let rules_v = load_rules();
    let mut l: Vec<String> = Vec::new();
    l.push("═".repeat(78));
    l.push(format!("Vulkan-Samples 两臂对照 · sample={sample}"));
    l.push(format!("  A 臂 (--config {cfg_a}): {}", knob_desc(&rules_v, sample, cfg_a)));
    l.push(format!("  B 臂 (--config {cfg_b}): {}", knob_desc(&rules_v, sample, cfg_b)));
    l.push(format!("  判读脚本 sha256: {}", sha256_self()));
    l.push("═".repeat(78));

    let comms = collect_comms(root);
    // 这行的措辞保持旧版原样: 归档的 report.txt 里就是它, 改一个字回放就断
    l.push("\n提交线程 (由 pick_comm.py 从 trace 里认出, 非写死):".into());
    for (c, rounds) in &comms {
        let mut shares: Vec<f64> = rounds.iter().filter_map(|(_, s)| *s).collect();
        shares.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        if !shares.is_empty() {
            l.push(format!(
                "  {c}: {} 轮, 占比 {:.3}~{:.3}",
                rounds.len(),
                shares[0],
                shares[shares.len() - 1]
            ));
        } else {
            l.push(format!("  {c}: {} 轮", rounds.len()));
        }
    }
    if comms.len() > 1 {
        l.push("  ⚠ 两臂不是同一个提交线程, 对照不成立, 下面的数不要用".into());
    }

    let fans = collect_fans(root);
    if !fans.is_empty() {
        l.push("\n测试条件 · 主动散热风扇 (不在 38 行快照里, 单独存证):".into());
        for (state, rounds) in &fans {
            l.push(format!("  {state}: {} 轮", rounds.len()));
        }
        if fans.len() > 1 {
            l.push("  ⚠ 各轮风扇状态不一致 —— 风扇自身耗电会进功耗读数, 这批数据不能跨状态比".into());
        }
    }

    let disp = collect_displays(root);
    if !disp.is_empty() {
        l.push("\n测试条件 · 面板刷新率 (开着 vsync 时它就是应用帧率):".into());
        for (state, rounds) in &disp {
            l.push(format!("  {state}: {} 轮", rounds.len()));
        }
        if disp.len() > 1 {
            l.push("  ⚠ 各轮刷新率设置不一致 —— 帧预算不同, 逐帧指标不可比".into());
        }
    }

    let cond = collect_run_conditions(root);
    let rounds = match cond.get("rounds") {
        Some(PyVal::List(r)) => r,
        _ => &Vec::new(),
    };
    if !rounds.is_empty() {
        l.push("\n逐轮条件 (这两项一变, 所有逐帧指标的口径就变了):".into());
        let cells: Vec<String> = rounds
            .iter()
            .map(|r| {
                let log_fps = r.get("log_fps").cloned().unwrap_or(PyVal::Null);
                let fps_str = match log_fps {
                    PyVal::Null => "None".to_string(),
                    ref v => format!("{:.1}", v.as_f64().unwrap_or(0.0)),
                };
                format!(
                    "{}:spf={},app_fps={}",
                    r.get("round").map(|v| v.py_str()).unwrap_or("None".into()),
                    r.get("spf").map(|v| v.py_str()).unwrap_or("None".into()),
                    fps_str
                )
            })
            .collect();
        l.push(format!("  {}", cells.join("  ")));
        let int_list = |k: &str| -> Vec<i64> {
            match cond.get(k) {
                Some(PyVal::List(xs)) => xs.iter().filter_map(|v| v.as_i64()).collect(),
                _ => Vec::new(),
            }
        };
        let (spf_set, log_fps_set) = (int_list("spf_set"), int_list("log_fps_set"));
        if spf_set.len() > 1 {
            l.push(format!(
                "  ⚠ submits_per_frame 在同一批里取了 {:?} —— 逐帧指标 (frame_p50/p95, gpu_active_mean, fps_mean) 是按它分组算的, 口径不一致, 不可比。",
                spf_set
            ));
        }
        if log_fps_set.len() > 1 {
            l.push(format!(
                "  ⚠ 应用帧率在同一批里取了 {:?} —— 开着 vsync 时它就是面板刷新率, 本机面板自适应刷新, 各轮帧预算不同, 逐帧指标没有可比性。",
                log_fps_set
            ));
        }
        if spf_set.len() > 1 || log_fps_set.len() > 1 {
            l.push("  → 这一批里只有 bw_median 可用: 它是 kgsl_buslevel 的直接观测量, 不经过分帧。".into());
        }
    }

    let arm = |k: &str| an.get(k).cloned().unwrap_or(PyVal::Obj(vec![]));
    let (a, b) = (arm("arm_a"), arm("arm_b"));
    let n_runs = |v: &PyVal| v.get("n_runs").map(|x| x.py_str()).unwrap_or("None".into());
    l.push(format!(
        "\n样本: A={} 轮  B={} 轮",
        n_runs(&a),
        n_runs(&b)
    ));

    // 判据 1 的口径复用: 臂内离散度太大, 再显著的臂间差异也不可信
    l.push("\n臂内离散度 (max-min)/median:".into());
    for (name, arm_v) in [("A", &a), ("B", &b)] {
        let mut cells = Vec::new();
        for m in ["frame_p95", "gpu_active_mean", "bw_median"] {
            let d = arm_v
                .get("metrics")
                .and_then(|ms| ms.get(m))
                .cloned()
                .unwrap_or(PyVal::Obj(vec![]));
            cells.push(format!(
                "{m}={}%",
                d.get("dispersion_pct").map(|v| v.py_str()).unwrap_or("None".into())
            ));
        }
        l.push(format!("  {name}: {}", cells.join("  ")));
    }

    l.push("\n臂间对照 (精确置换检验, 全枚举, 无随机种子):".into());
    let header = format!(
        "  {}{}{}{}{}{}  显著",
        ljust("指标", 18),
        rjust("A 均值", 12),
        rjust("B 均值", 12),
        rjust("差", 12),
        rjust("差%", 9),
        rjust("p", 10)
    );
    l.push(header.clone());
    l.push(format!("  {}", "-".repeat(header.chars().count() - 2)));
    let comparison = an.get("comparison").cloned().unwrap_or(PyVal::Obj(vec![]));
    if let PyVal::Obj(kvs) = &comparison {
        for (m, c) in kvs {
            if let Some(e) = c.get("error") {
                l.push(format!("  {}{}", ljust(m, 18), e.py_str()));
                continue;
            }
            let sig = match c.get("significant_at_0.05") {
                Some(PyVal::Bool(true)) => "是",
                _ => "否",
            };
            l.push(format!(
                "  {}{}{}{}{}{}  {sig}",
                ljust(m, 18),
                field(c.get("a_mean"), 12),
                field(c.get("b_mean"), 12),
                field(c.get("diff_mean"), 12),
                field(c.get("diff_pct"), 9),
                field(c.get("perm_p_two_sided"), 10)
            ));
        }
    }

    l.push("\n指标口径:".into());
    if let Some(PyVal::Obj(mm)) = rules_v.get("metric_meaning") {
        for (m, why) in mm {
            l.push(format!("  {m}: {}", why.py_str()));
        }
    }

    l.push("\n结论口径: 本文件只判'这套采集有没有把开关的差异测出来', 不判'这个优化值不值得做'。".into());
    let sig_metrics: Vec<String> = match &comparison {
        PyVal::Obj(kvs) => kvs
            .iter()
            .filter(|(_, c)| {
                c.get("error").is_none()
                    && matches!(c.get("significant_at_0.05"), Some(PyVal::Bool(true)))
            })
            .map(|(m, _)| m.clone())
            .collect(),
        _ => Vec::new(),
    };
    if !sig_metrics.is_empty() {
        l.push(format!("  测出显著差异的指标: {}", sig_metrics.join(", ")));
    } else {
        l.push("  没有任何指标在 0.05 上显著 —— 这套采集在该开关上不灵敏, 或轮数不够。".into());
    }
    Ok(l.join("\n"))
}

const USAGE: &str =
    "用法: phonefarm vks-report --root <A/B输出目录> --sample <样例名> --config-a <N> --config-b <N>";

pub fn run_vks_report(args: &[String]) -> i32 {
    let (mut root, mut sample) = (None, None);
    let (mut cfg_a, mut cfg_b) = (None, None);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--root" => root = it.next().cloned(),
            "--sample" => sample = it.next().cloned(),
            "--config-a" => cfg_a = it.next().and_then(|v| v.parse().ok()),
            "--config-b" => cfg_b = it.next().and_then(|v| v.parse().ok()),
            other => {
                eprintln!("不认识的参数 {other}\n{USAGE}");
                return 2;
            }
        }
    }
    let (Some(root), Some(sample), Some(a), Some(b)) = (root, sample, cfg_a, cfg_b) else {
        eprintln!("{USAGE}");
        return 2;
    };
    match report(Path::new(&root), &sample, a, b) {
        Ok(t) => {
            println!("{t}");
            0
        }
        Err(e) => {
            eprintln!("{e}");
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vks_root() -> std::path::PathBuf {
        // 这一组 runs_vks 的 JSON/报告留作测试夹具 (trace 本来就不入库)
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("仓库根")
            .join("loop_v1/fixtures/golden/runs_vks/render_passes_c0_vs_c1")
    }

    /// 判读哈希 = 冻结的 rules.json 文件本身的哈希 (口径切换点之后的自证对象)。
    #[test]
    fn rules_hash_is_the_rules_file() {
        let want: String = Sha256::new()
            .chain_update(include_str!("../loop_v1/carriers/vulkan-samples/rules.json"))
            .finalize()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert_eq!(sha256_self(), want);
        // 与切换点前归档的哈希 (对应 rules_frozen.py) 刻意不同
        assert_ne!(
            sha256_self(),
            "ef6202a5d3aa7b5d9596a4978f8a40ac64bffcd36bbf70aff59ab7ca830003d5"
        );
        // 档位语义真的从 JSON 里读出来了
        let rules_v = load_rules();
        assert!(knob_desc(&rules_v, "render_passes", 1).contains("vkCmdClearAttachments"));
        assert_eq!(knob_desc(&rules_v, "render_passes", 9), "<未登记>");
    }

    /// 对照源: 归档的 report.txt 整份重算 (与判读哈希一起逐字节比)。
    /// 证据在主 checkout, 别的机器上没有就跳过, 不算失败。
    #[test]
    fn report_matches_recorded_golden() {
        let root = vks_root();
        let want = root.join("report.txt");
        if !want.exists() {
            eprintln!("跳过: 归档证据不在 {} (异机/新 clone 属正常)", want.display());
            return;
        }
        // 口径切换点: 归档哈希对应切换前判读源码, 新报告哈希对应 rules.json ——
        // 除哈希一行外必须逐字节一致
        let mask = |t: &str| -> String {
            t.lines()
                .filter(|l| !l.contains("判读脚本 sha256"))
                .collect::<Vec<_>>()
                .join("\n")
        };
        assert_eq!(
            mask(&report(&root, "render_passes", 0, 1).unwrap()),
            mask(std::fs::read_to_string(&want).unwrap().trim_end_matches('\n'))
        );
    }

    #[test]
    fn binder_comms_normalize_but_classes_do_not() {
        assert_eq!(normalize_comm(Some("binder:1234_5")), "binder:<应用提交线程>");
        assert_eq!(normalize_comm(Some("kgsl_hwsched")), "kgsl_hwsched");
        assert_eq!(normalize_comm(Some("")), "<none>");
        assert_eq!(normalize_comm(None), "<none>");
    }

    /// 真实证据目录上的几个收集器: comm 抹 pid 后两臂同类、spf/log_fps 集合各归各。
    /// 证据在主 checkout, 别的机器上没有就跳过。
    #[test]
    fn collectors_see_the_recorded_conditions() {
        let root = vks_root();
        if !root.exists() {
            eprintln!("跳过: 归档证据不在 {} (异机/新 clone 属正常)", root.display());
            return;
        }
        let comms = collect_comms(&root);
        assert_eq!(comms.len(), 1, "抹掉 pid 后两臂应是同一类线程: {comms:?}");
        let cond = collect_run_conditions(&root);
        assert!(cond.get("spf_set").is_some());
    }
}
