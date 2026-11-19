//! loop_v1 的收口: 把五条判据的证据凑成一份可复现的结论。
//!
//! 由 `loop_v1/tools/report.py` 逐行搬过来, 输出字节完全一致。
//! **刻意不带时间戳、不带绝对路径、不用随机数**, 所以同样的输入每次产出逐字节相同的
//! report.json —— 判据 5 的回放自检直接 `cmp` 这份文件。
//!
//! 用法:
//!   phonefarm report --baseline 'runs/night*' --knob 'runs/knob*' \
//!                    --snap-before runs/snap_before.txt --snap-after runs/snap_final.txt \
//!                    [--primary frame_p95]

use crate::loopstat::{compare, describe, load_runs, Run};
use crate::looptrace::attribute;
use crate::pyjson::{dumps, PyVal};
use crate::pyobj;

/// 判据 1
const DISPERSION_GATE_PCT: f64 = 5.0;
/// 单调比超过这个值就认定为系统性漂移而非抖动
const MONOTONIC_GATE: f64 = 0.90;
/// 判据 3
const ALPHA: f64 = 0.05;

const COMPARE_METRICS: [&str; 5] = [
    "frame_p95",
    "frame_p50",
    "frame_mean",
    "gpu_active_mean",
    "bw_median",
];

/// 判据 4: 逐行比对进入前 / 退出后的设备快照。
pub fn snapshot_diff(before: &str, after: &str) -> PyVal {
    let (Ok(a), Ok(b)) = (
        std::fs::read_to_string(before),
        std::fs::read_to_string(after),
    ) else {
        return pyobj! { "checked" => false, "reason" => "快照文件缺失" };
    };
    // Python 的 str.splitlines(): 末尾换行不产生空行
    let a: Vec<&str> = a.split_inclusive('\n').map(|l| l.trim_end_matches('\n')).collect();
    let b: Vec<&str> = b.split_inclusive('\n').map(|l| l.trim_end_matches('\n')).collect();
    let mut diffs = Vec::new();
    for i in 0..a.len().max(b.len()) {
        let la = a.get(i).copied().unwrap_or("<缺行>");
        let lb = b.get(i).copied().unwrap_or("<缺行>");
        if la != lb {
            diffs.push(pyobj! { "line" => i + 1, "before" => la, "after" => lb });
        }
    }
    pyobj! {
        "checked" => true,
        "n_lines" => a.len(),
        "n_diff" => diffs.len(),
        "identical" => diffs.is_empty(),
        "diffs" => PyVal::List(diffs.into_iter().take(20).collect()),
    }
}

fn criterion_1(base: &PyVal, primary: &str) -> PyVal {
    let Some(m) = base.get("metrics").and_then(|v| v.get(primary)) else {
        return pyobj! { "pass" => false, "reason" => format!("基线里没有指标 {primary}") };
    };
    let disp = m.get("dispersion_pct").and_then(|v| v.as_f64());
    let d = m.get("drift").cloned().unwrap_or(PyVal::Null);
    // 漂移单独判: 离散度合格但单调比贴近 1, 说明只是恰好没散开, 负载并不稳
    let drift_flag = d
        .get("monotonic_frac")
        .and_then(|v| v.as_f64())
        .is_some_and(|f| f >= MONOTONIC_GATE);
    let disp_ok = disp.is_some_and(|x| x < DISPERSION_GATE_PCT);
    pyobj! {
        "pass" => disp_ok && !drift_flag,
        "metric" => primary,
        "dispersion_pct" => disp.map(PyVal::Float).unwrap_or(PyVal::Null),
        "gate_pct" => DISPERSION_GATE_PCT,
        "values" => m.get("values").cloned().unwrap_or(PyVal::Null),
        "drift" => d,
        "systematic_drift_flagged" => drift_flag,
        "note" => if disp_ok && !drift_flag {
            "离散度合格且无单调漂移".to_string()
        } else if drift_flag {
            "存在逐轮单调漂移, 即使离散度达标也不算可重复".to_string()
        } else {
            // Python 的 f-string 走 str(), None 印成 "None" 而不是 JSON 的 null
            format!("离散度 {}% 超过 {}% 门槛",
                    disp.map(PyVal::Float).unwrap_or(PyVal::Null).py_str(),
                    PyVal::Float(DISPERSION_GATE_PCT).py_str())
        },
    }
}

/// 对基线每一轮独立归因, 要求结论一致 —— 一轮一个说法就不叫归因。
fn criterion_2(base_runs: &[Run]) -> Result<PyVal, String> {
    let attrs = base_runs
        .iter()
        .map(|r| attribute(&r.1))
        .collect::<Result<Vec<_>, _>>()?;
    let verdicts: Vec<String> = attrs
        .iter()
        .map(|a| match a.get("verdict") {
            Some(PyVal::Str(s)) => s.clone(),
            _ => String::new(),
        })
        .collect();
    let mut uniq = verdicts.clone();
    uniq.sort();
    uniq.dedup();
    let consistent = uniq.len() == 1;
    let first = |k: &str| {
        attrs
            .first()
            .and_then(|a| a.get(k).cloned())
            .unwrap_or(PyVal::Null)
    };
    Ok(pyobj! {
        "pass" => consistent && !verdicts.is_empty() && verdicts[0] != "无单一主因",
        "verdicts_per_run" => verdicts.clone(),
        "consistent" => consistent,
        "verdict" => if consistent && !verdicts.is_empty() { PyVal::Str(verdicts[0].clone()) } else { PyVal::Null },
        "evidence" => first("evidence"),
        "actionable" => first("actionable"),
        "measures_run1" => first("measures"),
        "bandwidth_run1" => first("bandwidth"),
    })
}

fn criterion_3(c: &PyVal) -> PyVal {
    if let Some(e) = c.get("error") {
        return pyobj! { "pass" => false, "reason" => e.clone() };
    }
    let f = |k: &str| c.get(k).and_then(|v| v.as_f64()).unwrap_or(0.0);
    let improved = f("diff_mean") < 0.0; // 帧时间越小越好
    let sig = f("perm_p_two_sided") < ALPHA;
    let ci = c.get("ci95").cloned().unwrap_or(PyVal::Null);
    let ci_excludes_zero = match &ci {
        PyVal::List(v) if v.len() == 2 => {
            let (lo, hi) = (v[0].as_f64().unwrap_or(0.0), v[1].as_f64().unwrap_or(0.0));
            hi < 0.0 || lo > 0.0
        }
        _ => false,
    };
    let get = |k: &str| c.get(k).cloned().unwrap_or(PyVal::Null);
    pyobj! {
        "pass" => improved && sig && ci_excludes_zero,
        "metric" => get("metric"),
        "baseline_mean" => get("a_mean"),
        "knob_mean" => get("b_mean"),
        "diff_mean" => get("diff_mean"),
        "diff_pct" => get("diff_pct"),
        "effect_size_cohens_d" => get("cohens_d"),
        "hodges_lehmann" => get("hodges_lehmann"),
        "ci95" => ci,
        "ci_excludes_zero" => ci_excludes_zero,
        "perm_p_two_sided" => get("perm_p_two_sided"),
        "perm_enumerated" => get("perm_enumerated"),
        "alpha" => ALPHA,
        "direction" => if improved { "改善" } else { "变差或无变化" },
    }
}

const USAGE: &str = "用法: phonefarm report --baseline <glob> [--knob <glob>] \
[--snap-before 文件] [--snap-after 文件] [--replay-result 文件] [--primary 指标]";

pub fn run_report(args: &[String]) -> i32 {
    let mut baseline = None;
    let mut knob = None;
    let mut snap_before = None;
    let mut snap_after = None;
    let mut replay_result = None;
    let mut primary = "frame_p95".to_string();
    // argparse 既吃 `--k v` 也吃 `--k=v`, 两种都收
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let (flag, inline) = match a.split_once('=') {
            Some((f, v)) => (f, Some(v.to_string())),
            None => (a.as_str(), None),
        };
        let mut take = || inline.clone().or_else(|| it.next().cloned());
        match flag {
            "--baseline" => baseline = take(),
            "--knob" => knob = take(),
            "--snap-before" => snap_before = take(),
            "--snap-after" => snap_after = take(),
            "--replay-result" => replay_result = take(),
            "--primary" => primary = take().unwrap_or(primary),
            other => {
                eprintln!("不认识的参数 {other}\n{USAGE}");
                return 2;
            }
        }
    }
    let Some(baseline) = baseline else {
        eprintln!("{USAGE}");
        return 2;
    };

    let base_runs = load_runs(&baseline);
    if base_runs.is_empty() {
        eprintln!("基线里一轮都没有: {baseline}");
        return 1;
    }
    let base = describe(&base_runs, &baseline);
    let c2 = match criterion_2(&base_runs) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("归因失败: {e}");
            return 1;
        }
    };

    let mut out: Vec<(String, PyVal)> = vec![
        ("goal".into(), PyVal::Str("GOAL v1 — 最小可信闭环".into())),
        ("primary_metric".into(), PyVal::Str(primary.clone())),
        ("baseline".into(), base.clone()),
        (
            "criterion_1_workload_repeatable".into(),
            criterion_1(&base, &primary),
        ),
        ("criterion_2_attribution".into(), c2),
    ];

    if let Some(knob) = knob {
        let knob_runs = load_runs(&knob);
        if !knob_runs.is_empty() {
            out.push(("knob_arm".into(), describe(&knob_runs, &knob)));
            let cmps: Vec<(String, PyVal)> = COMPARE_METRICS
                .iter()
                .map(|m| (m.to_string(), compare(&base_runs, &knob_runs, m)))
                .collect();
            let primary_cmp = cmps.iter().find(|(k, _)| *k == primary).map(|(_, v)| v.clone());
            let Some(pc) = primary_cmp else {
                // 少一条判据的报告比没有报告更危险 —— summary 会显示 criteria_evaluated: 2
                // 却仍然退出 0。旧版在这里是 KeyError 崩掉, 这里照样不出报告。
                eprintln!("主指标 {primary} 不在对比指标里 (只能是 {})", COMPARE_METRICS.join(" / "));
                return 1;
            };
            out.push(("comparison".into(), PyVal::Obj(cmps)));
            out.push((
                "criterion_3_statistically_significant".into(),
                criterion_3(&pc),
            ));
        }
    }

    if let (Some(b), Some(a)) = (&snap_before, &snap_after) {
        let sd = snapshot_diff(b, a);
        let identical = matches!(sd.get("identical"), Some(PyVal::Bool(true)));
        let mut kvs = vec![("pass".to_string(), PyVal::Bool(identical))];
        if let PyVal::Obj(o) = sd {
            kvs.extend(o);
        }
        out.push(("criterion_4_no_residue".into(), PyVal::Obj(kvs)));
    }

    if let Some(p) = replay_result {
        if let Ok(code) = std::fs::read_to_string(&p) {
            let code = code.trim().to_string();
            out.push((
                "criterion_5_offline_replay".into(),
                pyobj! { "pass" => code == "0", "exit_code" => code },
            ));
        }
    }

    // 总判定
    let crits: Vec<(String, PyVal)> = out
        .iter()
        .filter(|(k, _)| k.starts_with("criterion_"))
        .map(|(k, v)| (k.clone(), v.clone()))
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
            "criteria_total" => 5i64,
            "criteria_evaluated" => crits.len(),
            "criteria_passed" => crits.iter().filter(|(_, v)| passed(v)).count(),
            "all_pass" => crits.len() == 5 && crits.iter().all(|(_, v)| passed(v)),
            "per_criterion" => PyVal::Obj(per),
        },
    ));

    println!("{}", dumps(&PyVal::Obj(out)));
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::{Path, PathBuf};

    fn repo_root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
    }

    #[test]
    fn snapshot_diff_reports_missing_files() {
        let v = snapshot_diff("/nonexistent/a", "/nonexistent/b");
        assert_eq!(v.get("checked"), Some(&PyVal::Bool(false)));
    }

    /// 缺行用 `<缺行>` 占位, 差异最多留 20 条 —— 两条都直接落在 report.json 字节上。
    #[test]
    fn snapshot_diff_pads_and_caps() {
        let d = std::env::temp_dir().join(format!("pf-snap-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let (a, b) = (d.join("a.txt"), d.join("b.txt"));
        std::fs::write(&a, "x\n").unwrap();
        std::fs::write(&b, (0..30).map(|i| format!("l{i}\n")).collect::<String>()).unwrap();
        let v = snapshot_diff(a.to_str().unwrap(), b.to_str().unwrap());
        assert_eq!(v.get("n_lines"), Some(&PyVal::Int(1)));
        assert_eq!(v.get("n_diff"), Some(&PyVal::Int(30)));
        assert_eq!(v.get("identical"), Some(&PyVal::Bool(false)));
        let PyVal::List(diffs) = v.get("diffs").unwrap() else {
            panic!()
        };
        assert_eq!(diffs.len(), 20);
        assert_eq!(diffs[1].get("before"), Some(&PyVal::Str("<缺行>".into())));
        let _ = std::fs::remove_dir_all(&d);
    }

    /// 末尾换行不该多出一个空行 —— 否则每份快照都会凭空多一行差异。
    #[test]
    fn splitlines_drops_trailing_newline() {
        let d = std::env::temp_dir().join(format!("pf-snap2-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        let (a, b) = (d.join("a.txt"), d.join("b.txt"));
        std::fs::write(&a, "x\ny\n").unwrap();
        std::fs::write(&b, "x\ny").unwrap();
        let v = snapshot_diff(a.to_str().unwrap(), b.to_str().unwrap());
        assert_eq!(v.get("n_lines"), Some(&PyVal::Int(2)));
        assert_eq!(v.get("identical"), Some(&PyVal::Bool(true)));
        let _ = std::fs::remove_dir_all(&d);
    }

    /// 对照源: `loop_v1/fixtures/golden/runs/report.json` —— 判据 5 的回放自检直接 cmp 的就是这份。
    #[test]
    fn report_matches_recorded_golden() {
        let runs = repo_root().join("loop_v1/fixtures/golden/runs");
        let want = std::fs::read_to_string(runs.join("report.json")).unwrap();
        let base_runs = load_runs(&format!("{}/ctrl*", runs.display()));
        let knob_runs = load_runs(&format!("{}/knob*", runs.display()));
        assert_eq!((base_runs.len(), knob_runs.len()), (5, 5));
        // report.cmd 用的是相对 glob "ctrl*" / "knob*", title 就是那串原文
        let base = describe(&base_runs, "ctrl*");
        let mut out: Vec<(String, PyVal)> = vec![
            ("goal".into(), PyVal::Str("GOAL v1 — 最小可信闭环".into())),
            ("primary_metric".into(), PyVal::Str("frame_p95".into())),
            ("baseline".into(), base.clone()),
            ("criterion_1_workload_repeatable".into(), criterion_1(&base, "frame_p95")),
            ("criterion_2_attribution".into(), criterion_2(&base_runs).unwrap()),
            ("knob_arm".into(), describe(&knob_runs, "knob*")),
        ];
        let cmps: Vec<(String, PyVal)> = COMPARE_METRICS
            .iter()
            .map(|m| (m.to_string(), compare(&base_runs, &knob_runs, m)))
            .collect();
        let pc = cmps.iter().find(|(k, _)| k == "frame_p95").unwrap().1.clone();
        out.push(("comparison".into(), PyVal::Obj(cmps)));
        out.push(("criterion_3_statistically_significant".into(), criterion_3(&pc)));
        let sd = snapshot_diff(
            runs.join("snap_before.txt").to_str().unwrap(),
            runs.join("snap_final.txt").to_str().unwrap(),
        );
        let identical = matches!(sd.get("identical"), Some(PyVal::Bool(true)));
        let mut kvs = vec![("pass".to_string(), PyVal::Bool(identical))];
        if let PyVal::Obj(o) = sd {
            kvs.extend(o);
        }
        out.push(("criterion_4_no_residue".into(), PyVal::Obj(kvs)));
        let code = std::fs::read_to_string(runs.join("replay_exit.txt")).unwrap().trim().to_string();
        out.push((
            "criterion_5_offline_replay".into(),
            pyobj! { "pass" => code == "0", "exit_code" => code },
        ));
        let crits: Vec<(String, PyVal)> = out
            .iter()
            .filter(|(k, _)| k.starts_with("criterion_"))
            .map(|(k, v)| (k.clone(), v.clone()))
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
                "criteria_total" => 5i64,
                "criteria_evaluated" => crits.len(),
                "criteria_passed" => crits.iter().filter(|(_, v)| passed(v)).count(),
                "all_pass" => crits.len() == 5 && crits.iter().all(|(_, v)| passed(v)),
                "per_criterion" => PyVal::Obj(per),
            },
        ));
        assert_eq!(dumps(&PyVal::Obj(out)), want.trim_end_matches('\n'));
    }
}
