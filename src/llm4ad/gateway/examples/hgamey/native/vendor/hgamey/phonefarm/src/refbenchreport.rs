//! refbench M0 六条判据汇总。
//!
//! 由 `loop_v1/refbench/refbench_report.py` 搬过来, 输出字节一致。与
//! `phonefarm report` 同一纪律: 不带时间戳、不带绝对路径、无随机数, 同样的输入
//! 每次产出逐字节相同的 report.json。回放自检直接 cmp 本命令的输出。
//!
//! 判定规则先于数据冻结: RULES 与规则文本的 sha256 一并写进报告 —— 事后改规则
//! 哈希对不上, 报告自判无效 (harness v2 判据 5 的种子)。
//!
//! 哈希钉的是哪份文本
//! ------------------
//! 判定规则抽成了 `loop_v1/refbench/rules.json` (数据, 不是代码), 报告里的
//! rules_sha256 就是**这份文件的 sha256** —— 改任何一个阈值, 新报告的哈希跟着变。
//!
//! **口径切换点**: 生成过现存归档证据的旧源码 (ffbf828 版) 原封留在
//! `rules_frozen.py`, 归档 report.json 里的哈希 (eb4b235f…) 对应**它**而不对应
//! rules.json —— 切换点之前的归档在 replay 时会恰好差 rules_sha256 一行, 这是
//! 记录在案的口径切换, 不是回放坏了; 切换点之后新产的证据哈希互相一致。
//!
//! 判据 4 的归因映射 (标准答案在 refbench 的 DESIGN.md §4):
//!   declared=bandwidth → 主因「GPU 计算受限」且 bus vote 显著高 (≥ frag 的 3 倍)
//!   declared=fragment  → 主因「GPU 计算受限」且 bus vote 显著低 (上式另一端)
//!   declared=none      → 主因「限帧器封顶 @面板刷新率」(vsync, 不得无中生有)
//! 带宽维在归因 (phonefarm attribute) 里是正交维不是主因 —— 访存停顿在 kgsl active
//! 里同样计忙, 所以 bandwidth 与 fragment 靠 bus vote 分离, 不靠主因字符串。

use crate::loopreport::snapshot_diff;
use crate::loopstat::{compare, dispersion, drift, fnmatch, median, Num, Run};
use crate::pyjson::{dumps_sorted_key, py_round, PyVal};
use crate::pyobj;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

/// 一轮: (标签, summary, refbench_out, attribution)
type Round = (String, PyVal, PyVal, PyVal);

/// 判定规则冻结在 `loop_v1/refbench/rules.json` (数据, 不是代码)。
/// 报告里的 rules_sha256 就是**这份文件的 sha256**: 改任何一个阈值, 新报告的
/// 哈希跟着变。键序即报告输出顺序, 不要重排。
const RULES_TEXT: &str = include_str!("../loop_v1/refbench/rules.json");

fn rules_sha256() -> String {
    let mut h = Sha256::new();
    h.update(RULES_TEXT.as_bytes());
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// 判定规则。顺序即输出顺序 (JSON 文件里的键序, loads 保序)。
fn rules() -> PyVal {
    match crate::pyjson::loads(RULES_TEXT) {
        Ok(v) => v.get("rules").cloned().unwrap_or(PyVal::Null),
        Err(_) => PyVal::Null,
    }
}

/// 旧版对 `json.dumps(x, sort_keys=True)` 的用法: 同一组旋钮出同一串。
fn knobs_key(v: &PyVal) -> String {
    dumps_sorted_key(v)
}

/// 按目录名排序载入有效轮; 带 INVALID 标记的轮保留在盘但不进样本。
fn load_valid(root: &Path, pattern: &str) -> Vec<Round> {
    let mut dirs: Vec<PathBuf> = std::fs::read_dir(root)
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            p.is_dir()
                && !p.join("INVALID").exists()
                && p.join("summary.json").exists()
                && p.join("refbench_out.json").exists()
                && p.join("attribution.json").exists()
        })
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .map(|n| fnmatch(n, pattern))
                .unwrap_or(false)
        })
        .collect();
    dirs.sort();
    dirs.into_iter()
        .filter_map(|p| {
            let load = |f: &str| -> Option<PyVal> {
                crate::pyjson::loads(&std::fs::read_to_string(p.join(f)).ok()?).ok()
            };
            let summary = load("summary.json")?;
            let rb = load("refbench_out.json")?;
            let attr = load("attribution.json")?;
            Some((p.file_name()?.to_str()?.to_string(), summary, rb, attr))
        })
        .collect()
}

fn numeric(summary: &PyVal, metric: &str) -> Vec<Num> {
    summary.get(metric).and_then(Num::from_json).into_iter().collect()
}

fn disp_drift(ctrl: &[Round], metric: &str) -> PyVal {
    let xs: Vec<Num> = ctrl.iter().flat_map(|(_, s, _, _)| numeric(s, metric)).collect();
    let disp = dispersion(&xs);
    pyobj! {
        "metric" => metric,
        "values" => PyVal::List(xs.iter().map(|x| PyVal::from(*x)).collect()),
        "dispersion_pct" => disp.map(|d| PyVal::Float(py_round(d * 100.0, 4))),
        "drift" => drift(&xs),
    }
}

/// 负载可重复: 门限指标 (中位帧时) 离散 < 0.5%, 且无系统性漂移 (斜率+单调联合判)。
/// frame_mean / frame_p95 一并报出作对照 (p95 是与原神对照的口径, 但它含平台尾抖动,
/// 不作门限)。漂移判定: 斜率显著 (|slope|>=gate) 或 强单调 (mono>=gate 且斜率非平) 才算。
fn criterion_1(ctrl: &[Round]) -> PyVal {
    let gm = "frame_p50";
    let rep: Vec<(String, PyVal)> = ["frame_p50", "frame_mean", "frame_p95"]
        .iter()
        .map(|m| (m.to_string(), disp_drift(ctrl, m)))
        .collect();
    let get = |m: &str| rep.iter().find(|(k, _)| k == m).map(|(_, v)| v).unwrap();
    let gated = get(gm);
    let disp_pct = gated.get("dispersion_pct").and_then(|v| v.as_f64());
    let d = gated.get("drift").cloned().unwrap_or(PyVal::Null);
    let slope = d
        .get("slope_pct_per_run")
        .and_then(|v| v.as_f64())
        .map(|v| v.abs())
        .unwrap_or(0.0);
    let mono = d.get("monotonic_frac").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let drift_flag = slope >= 0.15 || (mono >= 0.9 && slope >= 0.05);
    let ok = disp_pct.is_some_and(|x| x < 0.5) && !drift_flag;
    let observed: Vec<(String, PyVal)> = rep
        .iter()
        .map(|(m, v)| {
            (
                m.clone(),
                pyobj! {
                    "dispersion_pct" => v.get("dispersion_pct").cloned().unwrap_or(PyVal::Null),
                    "slope_pct_per_run" => v.get("drift").and_then(|d| d.get("slope_pct_per_run")).cloned().unwrap_or(PyVal::Null),
                    "monotonic_frac" => v.get("drift").and_then(|d| d.get("monotonic_frac")).cloned().unwrap_or(PyVal::Null),
                    "values" => v.get("values").cloned().unwrap_or(PyVal::Null),
                },
            )
        })
        .collect();
    pyobj! {
        "pass" => ok,
        "gated_metric" => gm,
        "dispersion_pct" => gated.get("dispersion_pct").cloned().unwrap_or(PyVal::Null),
        "gate_pct" => 0.5,
        "systematic_drift_flagged" => drift_flag,
        "drift" => d,
        "drift_slope_gate_pct" => 0.15,
        "observed_all_metrics" => PyVal::Obj(observed),
        "note" => format!(
            "负载可重复性以中位帧时判 (平台尾抖动不计入负载确定性); frame_p95 离散 {}% 如实报出作原神对照 (原神深夜 1.37% / 跨黄昏 5.35%)。漂移以最小二乘斜率判, 非仅单调比。",
            get("frame_p95").get("dispersion_pct").map(|v| v.py_str()).unwrap_or("None".into())
        ),
        "runs" => ctrl.iter().map(|(l, ..)| l.clone()).collect::<Vec<_>>(),
    }
}

/// 强度阶梯: 帧时间必须跟着负载走, 且没有一档被判「限帧器封顶」(钉死)。
fn criterion_2(steps: &[Round]) -> PyVal {
    let mut pts: Vec<PyVal> = steps
        .iter()
        .map(|(label, s, rb, attr)| {
            pyobj! {
                "label" => label.clone(),
                "intensity" => rb.get("params").and_then(|p| p.get("intensity")).cloned().unwrap_or(PyVal::Null),
                "frame_p50" => s.get("frame_p50").cloned().unwrap_or(PyVal::Null),
                "verdict" => attr.get("verdict").cloned().unwrap_or(PyVal::Null),
            }
        })
        .collect();
    pts.sort_by(|a, b| {
        let k = |p: &PyVal| p.get("intensity").and_then(|v| v.as_f64()).unwrap_or(0.0);
        k(a).partial_cmp(&k(b)).unwrap_or(std::cmp::Ordering::Equal)
    });
    let val = |p: &PyVal, k: &str| p.get(k).and_then(|v| v.as_f64());
    let increasing =
        pts.windows(2)
            .all(|w| match (val(&w[0], "frame_p50"), val(&w[1], "frame_p50")) {
        (Some(a), Some(b)) => b >= a * 1.1,
            _ => false,
        });
    let pinned: Vec<String> = pts
        .iter()
        .filter(|p| matches!(p.get("verdict"), Some(PyVal::Str(v)) if v.starts_with("限帧器封顶")))
        .filter_map(|p| p.get("label").map(|v| v.py_str()))
        .collect();
    let ok = pts.len() >= 3 && increasing && pinned.is_empty();
    pyobj! {
        "pass" => ok,
        "points" => PyVal::List(pts),
        "monotone_with_min_step" => increasing,
        "min_step_pct" => 10.0,
        "pinned_runs" => pinned,
    }
}

fn criterion_3(ctrl: &[Round], knob: &[Round]) -> PyVal {
    let knobs_set = |runs: &[Round]| -> Vec<String> {
        let mut v: Vec<String> = runs
            .iter()
            .map(|(_, _, rb, _)| knobs_key(rb.get("effective_knobs").unwrap_or(&PyVal::Null)))
            .collect();
        v.sort();
        v.dedup();
        v
    };
    let ctrl_knobs = knobs_set(ctrl);
    let knob_knobs = knobs_set(knob);
    let knobs_differ = ctrl_knobs.len() == 1 && knob_knobs.len() == 1 && ctrl_knobs != knob_knobs;
    let a: Vec<Run> = ctrl.iter().map(|(l, s, _, _)| (l.clone(), s.clone())).collect();
    let b: Vec<Run> = knob.iter().map(|(l, s, _, _)| (l.clone(), s.clone())).collect();
    let cmp = compare(&a, &b, "frame_p95");
    let num = |v: &PyVal| matches!(v, PyVal::Int(_) | PyVal::Float(_));
    let diff = cmp.get("diff_mean").cloned().unwrap_or(PyVal::Null);
    let improved = num(&diff) && diff.as_f64().is_some_and(|x| x < 0.0);
    let p = cmp.get("perm_p_two_sided").cloned().unwrap_or(PyVal::Null);
    let sig = num(&p) && p.as_f64().is_some_and(|x| x < 0.05);
    pyobj! {
        "pass" => knobs_differ && improved && sig,
        "effective_knobs_ctrl" => ctrl_knobs,
        "effective_knobs_knob" => knob_knobs,
        "knobs_differ" => knobs_differ,
        "comparison" => cmp,
        "alpha" => 0.05,
    }
}

/// 归因可证伪: loop_v1 的结论必须与靶子自报的瓶颈类型按映射一致。
fn criterion_4(bw: &[Round], frag: &[Round], idle: &[Round]) -> PyVal {
    let declared_ok = |runs: &[Round], scene: &str, want: &str| {
        runs.iter().all(|(_, _, rb, _)| {
            rb.get("scene").map(|v| v.py_str()).as_deref() == Some(scene)
                && rb.get("declared_bottleneck").map(|v| v.py_str()).as_deref() == Some(want)
        })
    };
    let declared_sanity = declared_ok(bw, "bw_pingpong", "bandwidth")
        && declared_ok(frag, "frag_alu", "fragment")
        && declared_ok(idle, "idle_cap", "none");
    let verdicts = |runs: &[Round]| -> Vec<PyVal> {
        runs.iter()
            .map(|(_, _, _, a)| a.get("verdict").cloned().unwrap_or(PyVal::Null))
            .collect()
    };
    let bw_verdicts = verdicts(bw);
    let frag_verdicts = verdicts(frag);
    let idle_verdicts = verdicts(idle);
    let all_eq = |vs: &[PyVal], s: &str| vs.iter().all(|v| v.py_str() == s);
    let all_start = |vs: &[PyVal], s: &str| {
        vs.iter()
            .all(|v| matches!(v, PyVal::Str(t) if t.starts_with(s)))
    };
    let bw_all_gpu = all_eq(&bw_verdicts, "GPU 计算受限");
    let frag_all_gpu = all_eq(&frag_verdicts, "GPU 计算受限");
    let idle_all_cap = all_start(&idle_verdicts, "限帧器封顶");

    let votes = |runs: &[Round]| -> Vec<Num> {
        runs.iter().flat_map(|(_, s, _, _)| numeric(s, "bw_median")).collect()
    };
    let med_bw = median(&votes(bw));
    let med_frag = median(&votes(frag));
    // Python 的 `if a and b`: 中位数是 0 也算假, 就给不出分离倍数
    let separation = match (med_bw, med_frag) {
        (Some(a), Some(b)) if a.f() != 0.0 && b.f() != 0.0 => Some(py_round(a.f() / b.f(), 2)),
        _ => None,
    };
    let bus_separated = separation.is_some_and(|x| x >= 3.0);
    let ok = declared_sanity
        && bw.len() >= 2
        && frag.len() >= 2
        && idle.len() >= 2
        && bw_all_gpu
        && frag_all_gpu
        && idle_all_cap
        && bus_separated;
    pyobj! {
        "pass" => ok,
        "mapping" => pyobj! {
            // `%.0f` 印 "3"
            "bandwidth" => "verdict==GPU 计算受限 且 bus vote ≥ frag 的 3 倍",
            "fragment" => "verdict==GPU 计算受限 且 bus vote 为分离的低端",
            "none" => "verdict 以「限帧器封顶」开头",
        },
        "declared_sanity" => declared_sanity,
        "bw_verdicts" => PyVal::List(bw_verdicts),
        "frag_verdicts" => PyVal::List(frag_verdicts),
        "idle_verdicts" => PyVal::List(idle_verdicts),
        "bw_all_gpu" => bw_all_gpu,
        "frag_all_gpu" => frag_all_gpu,
        "idle_all_cap" => idle_all_cap,
        "bus_vote_median_bw" => med_bw,
        "bus_vote_median_frag" => med_frag,
        "bus_separation_x" => separation.map(PyVal::Float),
        "bus_separated" => bus_separated,
        "n_runs" => pyobj! { "bw" => bw.len(), "frag" => frag.len(), "idle" => idle.len() },
    }
}

pub fn report(root: &Path) -> String {
    let ctrl = load_valid(root, "bw_ctrl*");
    let knob = load_valid(root, "bw_knob*");
    let steps = load_valid(root, "inten_i*");
    let frag = load_valid(root, "frag*");
    let idle = load_valid(root, "idle*");

    let sd = snapshot_diff(
        &root.join("snap_before.txt").to_string_lossy(),
        &root.join("snap_final.txt").to_string_lossy(),
    );
    let identical = matches!(sd.get("identical"), Some(PyVal::Bool(true)));
    let mut c5 = vec![("pass".to_string(), PyVal::Bool(identical))];
    if let PyVal::Obj(o) = sd {
        c5.extend(o);
    }

    let c6 = match std::fs::read_to_string(root.join("replay_exit.txt")) {
        Ok(t) => {
            let code = t.trim().to_string();
            pyobj! {
                "pass" => code == "0",
                "exit_code" => code,
                "note" => "逐轮 summary/attribution 重算字节一致; 报告自身的重放一致性由 replay_exit2.txt 另证",
            }
        }
        Err(_) => pyobj! { "pass" => false, "reason" => "replay_exit.txt 缺失" },
    };

    let mut out: Vec<(String, PyVal)> = vec![
        ("goal".into(), PyVal::Str("refbench M0 — 白盒基准靶场首轮闭环".into())),
        ("rules".into(), rules()),
        ("rules_sha256".into(), PyVal::Str(rules_sha256())),
        ("criterion_1_repeatable".into(), criterion_1(&ctrl)),
        ("criterion_2_ceiling_real".into(), criterion_2(&steps)),
        ("criterion_3_knob_effective".into(), criterion_3(&ctrl, &knob)),
        ("criterion_4_attribution_falsifiable".into(), criterion_4(&ctrl, &frag, &idle)),
        ("criterion_5_no_residue".into(), PyVal::Obj(c5)),
        ("criterion_6_offline_replay".into(), c6),
    ];

    let crits: Vec<(String, PyVal)> = out
        .iter()
        .filter(|(k, _)| k.starts_with("criterion_"))
        .cloned()
        .collect();
    let passed = |v: &PyVal| matches!(v.get("pass"), Some(PyVal::Bool(true)));
    let mut per: Vec<(String, PyVal)> = crits
        .iter()
        .map(|(k, v)| (k.clone(), PyVal::Bool(passed(v))))
        .collect();
    per.sort_by(|a, b| a.0.cmp(&b.0));
    out.push((
        "summary".into(),
        pyobj! {
            "criteria_total" => 6i64,
            "criteria_passed" => crits.iter().filter(|(_, v)| passed(v)).count(),
            "all_pass" => crits.iter().all(|(_, v)| passed(v)),
            "per_criterion" => PyVal::Obj(per),
        },
    ));
    crate::pyjson::dumps(&PyVal::Obj(out))
}

const USAGE: &str = "用法: phonefarm refbench-report --root <runs目录>";

pub fn run_refbench_report(args: &[String]) -> i32 {
    let mut root: Option<String> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--root" => root = it.next().cloned(),
            other => {
                eprintln!("不认识的参数 {other}\n{USAGE}");
                return 2;
            }
        }
    }
    let Some(root) = root else {
        eprintln!("{USAGE}");
        return 2;
    };
    println!("{}", report(Path::new(&root)));
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn runs_root() -> PathBuf {
        // 归档证据在主 checkout 的 runs_refbench (大 trace 不入库, 但 JSON 都在)
        // <repo>/src -> <repo>(worktree) -> projects/ -> phonefarm/loop_v1/runs_refbench
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(|p| p.parent())
            .map(|p| p.join("phonefarm/loop_v1/runs_refbench"))
            .expect("仓库根")
    }

    /// rules_sha256 = 冻结的 rules.json 文件本身的哈希 (口径切换点之后的自证对象)。
    #[test]
    fn rules_hash_is_the_rules_file() {
        let want: String = Sha256::new()
            .chain_update(include_str!("../loop_v1/refbench/rules.json"))
            .finalize()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert_eq!(rules_sha256(), want);
        // 与切换点前归档的哈希 (对应 rules_frozen.py) 刻意不同
        assert_ne!(
            rules_sha256(),
            "eb4b235f959c54b06e1a86f5cf146cd2da46068bc791a308add7b9234223e91b"
        );
    }

    /// 对照源: runs_refbench/report.json —— 归档时由旧 Python 版落盘, 判据 5/6
    /// 直接 cmp 的就是它。整份 6 判据重算, 逐字节比。
    /// 证据目录在主 checkout (大 trace 不入库), 别的机器上没有就跳过, 不算失败。
    #[test]
    fn report_matches_recorded_golden() {
        let root = runs_root();
        let want = root.join("report.json");
        if !want.exists() {
            eprintln!("跳过: 归档证据不在 {} (异机/新 clone 属正常)", want.display());
            return;
        }
        // 口径切换点: 归档哈希对应切换前源码, 新报告哈希对应 rules.json ——
        // 除 rules_sha256 一行外必须逐字节一致
        let mask = |t: &str| -> String {
            t.lines()
                .filter(|l| !l.contains("rules_sha256"))
                .collect::<Vec<_>>()
                .join("\n")
        };
        assert_eq!(
            mask(&report(&root)),
            mask(std::fs::read_to_string(&want).unwrap().trim_end_matches('\n'))
        );
    }
}
