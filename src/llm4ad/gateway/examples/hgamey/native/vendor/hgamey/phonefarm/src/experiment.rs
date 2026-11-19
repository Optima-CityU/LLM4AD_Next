//! 实验框架 (SPEC_EVOLUTION §7): A/B/C 三臂对比 —— A 无跨局经验 / B 冻结经验 / C 持续演进。
//!
//! 分层纪律: spec 解析、交错排程、台账去重、报告聚合全是纯函数,单测钉死;
//! 子进程跑局、快照/清场等文件副作用集中在后半部分的执行函数,单测不跑真子进程。
//!
//! 隔离实现 (§7.2): 每臂独立状态根 tasks/_exp/<exp-id>/<arm>/,子进程经
//! PF_TASKS_ROOT 钉到臂根;臂根即一个独立 tasks 根,任务目录直接在其下。
//! 快照在实验启动时从主 tasks 根拷贝(任务级四件 + _global 三件),B 臂每局前恢复,
//! A 臂每局前清空,C 臂不动(持续演进)。

use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

/// 跨局经验四件(任务级): 注入源文件不在就等于注入关闭(SPEC_EVOLUTION §7.1 实现路径)
const EXP_FILES: [&str; 4] = ["lessons.jsonl", "hypotheses.jsonl", "capabilities.jsonl", "tree.json"];
/// 全局域没有 tree.json,只有前三件
const EXP_FILES_GLOBAL: [&str; 3] = ["lessons.jsonl", "hypotheses.jsonl", "capabilities.jsonl"];

// ══════════════ spec 解析(纯) ══════════════

#[derive(Deserialize, Clone, Debug)]
pub struct TaskSpec {
    pub task: String,
    pub goal: String,
    #[serde(default)]
    pub app: Option<String>,
    #[serde(default)]
    pub assert: Option<String>,
}

#[derive(Deserialize, Clone, Debug)]
pub struct AppReset {
    pub pkg: String,
    #[serde(default)]
    pub clear: bool,
}

#[derive(Deserialize, Clone, Debug)]
pub struct ExpSpec {
    pub id: String,
    /// 臂子集(缺省 A/B/C 全臂)
    #[serde(default = "d_arms")]
    pub arms: Vec<String>,
    #[serde(default)]
    pub adapt_tasks: Vec<TaskSpec>,
    #[serde(default)]
    pub eval_tasks: Vec<TaskSpec>,
    pub rounds: u32,
    pub budget_calls: u32,
    #[serde(default = "d_max_steps")]
    pub max_steps: u32,
    #[serde(default)]
    pub serial: Option<String>,
    pub token_cap: u64,
    /// 单臂连续设备级失败上限(§7.6,缺省 3)
    #[serde(default = "d_stall")]
    pub stall_device_failures: u32,
    /// 评分协议版本(必填,§7.3 钉住;v1 实现只认 "v1")
    pub scoring_proto: String,
    #[serde(default = "d_delta")]
    pub min_meaningful_delta: f64,
    /// 设备初态声明(§7.2);v1 只记录不执行 clear,无法控制因素入报告
    #[serde(default)]
    pub app_reset: Option<AppReset>,
}
fn d_arms() -> Vec<String> { vec!["A".into(), "B".into(), "C".into()] }
fn d_max_steps() -> u32 { 12 }
fn d_stall() -> u32 { 3 }
fn d_delta() -> f64 { 0.15 }

impl ExpSpec {
    /// 全部任务(适应任务在前,评测任务在后;排程的任务序按此固定)
    pub fn all_tasks(&self) -> Vec<&TaskSpec> {
        self.adapt_tasks.iter().chain(self.eval_tasks.iter()).collect()
    }
}

pub fn parse_spec(text: &str) -> Result<ExpSpec, String> {
    let s: ExpSpec = toml::from_str(text)
        .map_err(|e| format!("spec.toml 解析失败: {e}(scoring_proto 为必填字段)"))?;
    validate_spec(&s)?;
    Ok(s)
}

fn validate_spec(s: &ExpSpec) -> Result<(), String> {
    if s.id.trim().is_empty() {
        return Err("spec 的 id 为空".into());
    }
    if s.id.contains('/') || s.id.contains('\\') || s.id.contains("..") {
        return Err(format!("spec id '{}' 含路径分隔符或 '..',拒绝(要拼进磁盘路径)", s.id));
    }
    // §7.3: 协议版本钉住;本实现只落地 v1 口径,其它版本直接拒绝而不是静默跑歪
    if s.scoring_proto != "v1" {
        return Err(format!(
            "scoring_proto '{}' 不受支持: 本实现只有 v1 评分口径,协议升级须独立评审后再接",
            s.scoring_proto));
    }
    if s.arms.is_empty() {
        return Err("spec 的 arms 为空(至少一臂)".into());
    }
    let mut seen = HashSet::new();
    for a in &s.arms {
        if !matches!(a.as_str(), "A" | "B" | "C") {
            return Err(format!("未知臂 '{a}'(臂词表固定 A|B|C,§7.1)"));
        }
        if !seen.insert(a.as_str()) {
            return Err(format!("臂 '{a}' 重复声明"));
        }
    }
    if s.adapt_tasks.is_empty() && s.eval_tasks.is_empty() {
        return Err("adapt_tasks 与 eval_tasks 全空,没有可跑的任务".into());
    }
    for t in s.adapt_tasks.iter().chain(s.eval_tasks.iter()) {
        if t.task.trim().is_empty() || t.task.contains('/') || t.task.contains('\\') || t.task.contains("..") {
            return Err(format!("任务名 '{}' 非法(空或含路径分隔符)", t.task));
        }
        if t.goal.trim().is_empty() {
            return Err(format!("任务 '{}' 缺 goal", t.task));
        }
    }
    if s.rounds == 0 {
        return Err("rounds 必须 >= 1".into());
    }
    if s.budget_calls == 0 {
        return Err("budget_calls 必须 >= 1".into());
    }
    if s.max_steps == 0 {
        return Err("max_steps 必须 >= 1".into());
    }
    if s.token_cap == 0 {
        return Err("token_cap 必须 >= 1".into());
    }
    if s.stall_device_failures == 0 {
        return Err("stall_device_failures 必须 >= 1".into());
    }
    if !(s.min_meaningful_delta > 0.0 && s.min_meaningful_delta <= 1.0) {
        return Err(format!("min_meaningful_delta 须在 (0,1],收到 {}", s.min_meaningful_delta));
    }
    Ok(())
}

// ══════════════ 交错排程(纯) ══════════════

/// 一局的最小调度单元: 第几轮 × 第几个任务 × 哪一臂
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Slot {
    pub round: u32,
    pub task_idx: usize,
    pub arm: String,
}

/// §7.2 顺序效应对冲: 轮内逐任务按臂字母序交错(A1B1C1,A2B2C2…),全程确定性。
/// 臂集在函数内排序去重,调用方传乱序也得到同一序列(报告可复查)。
pub fn schedule(arms: &[String], n_tasks: usize, rounds: u32) -> Vec<Slot> {
    let mut arms: Vec<String> = arms.to_vec();
    arms.sort();
    arms.dedup();
    let mut out = Vec::with_capacity(rounds as usize * n_tasks * arms.len());
    for r in 1..=rounds {
        for t in 0..n_tasks {
            for a in &arms {
                out.push(Slot { round: r, task_idx: t, arm: a.clone() });
            }
        }
    }
    out
}

// ══════════════ 台账解读(纯) ══════════════

/// 已完成局的三元组集合(局行带 run_id 字段)
pub fn completed(rows: &[Value]) -> HashSet<(String, String, u32)> {
    rows.iter()
        .filter(|r| r["run_id"].is_string())
        .map(|r| (
            r["arm"].as_str().unwrap_or("").to_string(),
            r["task"].as_str().unwrap_or("").to_string(),
            r["round"].as_u64().unwrap_or(0) as u32,
        ))
        .collect()
}

/// 已停臂集合: 臂 → 停臂原因(token_cap|stall)
pub fn stopped_arms(rows: &[Value]) -> HashMap<String, String> {
    rows.iter()
        .filter(|r| r["event"] == "arm_stop")
        .map(|r| (
            r["arm"].as_str().unwrap_or("").to_string(),
            r["stop_reason"].as_str().unwrap_or("?").to_string(),
        ))
        .collect()
}

/// 逐臂累计 tokens(局行求和,§7.6 预算口径: end 记录 tokens 字段)
pub fn arm_tokens(rows: &[Value]) -> HashMap<String, u64> {
    let mut m: HashMap<String, u64> = HashMap::new();
    for r in rows.iter().filter(|r| r["run_id"].is_string()) {
        *m.entry(r["arm"].as_str().unwrap_or("").to_string()).or_insert(0) +=
            r["tokens"].as_u64().unwrap_or(0);
    }
    m
}

/// --resume 续跑(纯): 去掉已完成三元组与已停臂的剩余槽位
pub fn remaining_slots(
    slots: &[Slot],
    tasks: &[&TaskSpec],
    done: &HashSet<(String, String, u32)>,
    stopped: &HashMap<String, String>,
) -> Vec<Slot> {
    slots.iter()
        .filter(|s| !stopped.contains_key(s.arm.as_str()))
        .filter(|s| !done.contains(&(s.arm.clone(), tasks[s.task_idx].task.clone(), s.round)))
        .cloned()
        .collect()
}

/// token_cap 触顶判定(纯): 累计用量 >= 硬顶即停臂
pub fn token_cap_hit(used: u64, cap: u64) -> bool {
    used >= cap
}

// ══════════════ 子进程输出与局账解读(纯) ══════════════

/// run 子命令 stdout 的 "summary: run=<id> stop=.. achieved=.." 行解析。
/// 取最后一行(容错前面的日志输出),k=v 以空白分隔。
pub fn parse_summary_line(stdout: &str) -> Option<HashMap<String, String>> {
    let line = stdout.lines().rev().find(|l| l.starts_with("summary: "))?;
    Some(
        line["summary: ".len()..]
            .split_whitespace()
            .filter_map(|kv| kv.split_once('='))
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect(),
    )
}

/// 无效尝试率计数(纯,§7.4 口径): 被驳回(rejected 前缀) + 空击(diff=none) 占 act 比例
pub fn invalid_attempts(recs: &[Value]) -> (u64, u64) {
    let acts = recs.iter().filter(|r| r["r"] == "act").count() as u64;
    let invalid = recs.iter()
        .filter(|r| r["r"] == "diff")
        .filter(|r| {
            let d = r["d"].as_str().unwrap_or("");
            d.starts_with("rejected(") || d == "none"
        })
        .count() as u64;
    (invalid, acts)
}

/// pred 检验通过计数(纯,§7.4 口径): hypotheses.jsonl 事件流里 outcome 的 assert=pass 占比
pub fn pred_outcomes(recs: &[Value]) -> (u64, u64) {
    let outcomes: Vec<&Value> = recs.iter()
        .filter(|r| r["r"] == "pred" && r["op"] == "outcome")
        .collect();
    let pass = outcomes.iter().filter(|r| r["assert"] == "pass").count() as u64;
    (pass, outcomes.len() as u64)
}

/// 分位数三件套(纯): (均值, p50, p95),空序列 None
pub fn stat3(mut v: Vec<f64>) -> Option<(f64, f64, f64)> {
    if v.is_empty() {
        return None;
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mean = v.iter().sum::<f64>() / v.len() as f64;
    let pick = |q: f64| v[((v.len() - 1) as f64 * q).round() as usize];
    Some(((mean * 10.0).round() / 10.0, pick(0.5), pick(0.95)))
}

// ══════════════ 报告聚合(纯) ══════════════

/// 单臂机制指标(§7.4 三组里的两组原料;第三组"泛化"= eval 任务成功率,从台账算)
#[derive(Clone, Debug, Default)]
pub struct MechStats {
    pub pred_pass: u64,
    pub pred_total: u64,
    pub invalid: u64,
    pub acts: u64,
}

/// 报告构建(纯): 输入 spec + 台账行 + 逐臂机制统计,输出报告 JSON。
/// 样本<10 只报原始分布不做推断(§7.4);无提升/退步如实写负结果;
/// "机制已实现"与"收益已验证"分开陈述。
pub fn build_report(
    spec: &ExpSpec,
    arms_run: &[String],
    rows: &[Value],
    mech: &HashMap<String, MechStats>,
    ablate: Option<&str>,
) -> Value {
    // 臂序固定字母序(报告可复查,与排程序一致)
    let mut arms_sorted: Vec<String> = arms_run.to_vec();
    arms_sorted.sort();
    arms_sorted.dedup();
    let tasks = spec.all_tasks();
    let eval_names: HashSet<&str> = spec.eval_tasks.iter().map(|t| t.task.as_str()).collect();
    let adapt_names: HashSet<&str> = spec.adapt_tasks.iter().map(|t| t.task.as_str()).collect();
    let declared = spec.rounds as u64 * tasks.len() as u64 * arms_sorted.len() as u64;
    let ep_rows: Vec<&Value> = rows.iter().filter(|r| r["run_id"].is_string()).collect();
    let stopped = stopped_arms(rows);

    // 逐臂聚合
    let mut arm_objs: Vec<Value> = Vec::new();
    let mut overall_rate: HashMap<String, f64> = HashMap::new();
    for arm in &arms_sorted {
        let mine: Vec<&Value> = ep_rows.iter()
            .filter(|r| r["arm"] == arm.as_str())
            .copied()
            .collect();
        let n = mine.len() as u64;
        let won = mine.iter().filter(|r| r["achieved"] == true).count() as u64;
        let in_set = |set: &HashSet<&str>| {
            mine.iter()
                .filter(|r| set.contains(r["task"].as_str().unwrap_or("")))
                .copied()
                .collect::<Vec<&Value>>()
        };
        let rate = |v: &[&Value]| -> Value {
            if v.is_empty() {
                Value::Null
            } else {
                let w = v.iter().filter(|r| r["achieved"] == true).count();
                json!({"won": w, "n": v.len(), "rate": (w as f64 * 1000.0 / v.len() as f64).round() / 1000.0})
            }
        };
        let ad = in_set(&adapt_names);
        let ev = in_set(&eval_names);
        let calls: u64 = mine.iter().map(|r| r["calls"].as_u64().unwrap_or(0)).sum();
        let tokens: u64 = mine.iter().map(|r| r["tokens"].as_u64().unwrap_or(0)).sum();
        let walls: Vec<f64> = mine.iter().filter_map(|r| r["wall_ms"].as_f64()).collect();
        // 失败成本单列(§7.4): 未达成局的 calls/tokens
        let fails: Vec<&Value> = mine.iter().filter(|r| r["achieved"] != true).copied().collect();
        let fail_calls: u64 = fails.iter().map(|r| r["calls"].as_u64().unwrap_or(0)).sum();
        let fail_tokens: u64 = fails.iter().map(|r| r["tokens"].as_u64().unwrap_or(0)).sum();
        if n > 0 {
            overall_rate.insert(arm.clone(), won as f64 / n as f64);
        }
        let m = mech.get(arm).cloned().unwrap_or_default();
        arm_objs.push(json!({
            "arm": arm,
            "episodes": n,
            "achieved": won,
            "success_overall": if n > 0 { json!((won as f64 * 1000.0 / n as f64).round() / 1000.0) } else { Value::Null },
            "success_adapt": rate(&ad),
            "success_eval": rate(&ev),
            "calls": calls,
            "tokens": tokens,
            "wall_ms_stat3": stat3(walls).map(|(a, b, c)| json!([a, b, c])),
            "failed_cost": {"episodes": fails.len(), "calls": fail_calls, "tokens": fail_tokens},
            "pred_pass_rate": if m.pred_total > 0 {
                json!({"pass": m.pred_pass, "total": m.pred_total,
                    "rate": (m.pred_pass as f64 * 1000.0 / m.pred_total as f64).round() / 1000.0})
            } else { Value::Null },
            "invalid_attempt_rate": if m.acts > 0 {
                json!({"invalid": m.invalid, "acts": m.acts,
                    "rate": (m.invalid as f64 * 1000.0 / m.acts as f64).round() / 1000.0})
            } else { Value::Null },
            "stopped": stopped.get(arm).cloned(),
        }));
    }

    // 臂间差异 × 最小有意义幅度(§7.4): 如实对照,不粉饰
    let mut comparisons: Vec<Value> = Vec::new();
    for i in 0..arms_sorted.len() {
        for j in (i + 1)..arms_sorted.len() {
            let (x, y) = (&arms_sorted[i], &arms_sorted[j]);
            match (overall_rate.get(x), overall_rate.get(y)) {
                (Some(&rx), Some(&ry)) => {
                    let d = ((rx - ry) * 1000.0).round() / 1000.0;
                    let meaningful = d.abs() >= spec.min_meaningful_delta;
                    comparisons.push(json!({
                        "pair": [x, y],
                        "delta": d,
                        "abs_ge_min_meaningful_delta": meaningful,
                        "verdict": if !meaningful {
                            format!("差异 {d} 未达最小有意义幅度 {},不作收益结论", spec.min_meaningful_delta)
                        } else if d > 0.0 {
                            format!("{x} 优于 {y}(幅度 {d} >= {})", spec.min_meaningful_delta)
                        } else {
                            format!("{x} 劣于 {y}(幅度 {} >= {})", d.abs(), spec.min_meaningful_delta)
                        },
                    }));
                }
                _ => comparisons.push(json!({"pair": [x, y], "delta": Value::Null,
                    "verdict": "至少一臂零样本,无法比较"})),
            }
        }
    }

    // 样本量纪律(§7.4): 样本<10 只报原始分布
    let small_sample = (ep_rows.len() as u64) < 10;
    let inference = if small_sample {
        format!("总样本 {} < 10,只报原始分布,不做统计推断(p 值/置信区间不适用)", ep_rows.len())
    } else {
        format!("总样本 {},仍建议结合逐局原始结果阅读;v1 不计算 p 值,差异对照只看 min_meaningful_delta", ep_rows.len())
    };

    // 负结果与"已实现≠已验证"声明(§7.4 硬性措辞)
    let mut findings: Vec<String> = Vec::new();
    // pair [A,C] 的 delta 口径是 A-C;"C 相对 A"要取负
    let c_rel_a = comparisons.iter()
        .find(|c| c["pair"] == json!(["A", "C"]))
        .and_then(|c| c["delta"].as_f64())
        .map(|d| -d);
    match c_rel_a {
        Some(d) if d >= spec.min_meaningful_delta => findings.push(format!(
            "C(持续演进) 相对 A(无经验) 总体成功率 +{d},达到最小有意义幅度: 收益方向为正(机制已实现,且本实验观测到收益)")),
        Some(d) if d <= -spec.min_meaningful_delta => findings.push(format!(
            "负结果: C(持续演进) 相对 A(无经验) 总体成功率 {d},退步达到最小有意义幅度;机制已实现但收益未验证,需查假设质量/注入噪声")),
        Some(d) => findings.push(format!(
            "C 相对 A 差异 {d},未达最小有意义幅度 {}: 机制已实现,但本实验样本下收益未验证(不作正向结论)",
            spec.min_meaningful_delta)),
        None => findings.push("A/C 至少一臂零样本,收益对照无法给出".into()),
    }
    if let Some(ab) = ablate {
        findings.push(format!(
            "消融臂标注: {ab}。诚实缺口: v1 仅把消融标记经环境变量传入子进程并落账,runtime 尚未接线该开关;消融结论须人工对照假设事件计数解读,不得当作机制级证据"));
    }
    findings.push("声明拆分: '机制已实现'(假设/检验/固化代码路径在跑)与'收益已验证'(对比差异达最小幅度)是两回事,本报告只在上面的差异对照里谈收益".into());

    // 无法控制的因素(§7.4 如实列出)
    let mut uncontrolled: Vec<String> = vec![
        "目标应用版本未钉住(应用自更新会改变界面真值)".into(),
        "单设备单序列执行,无跨设备复现".into(),
        "模型 provider 限流与网络波动影响步耗时与调用成败".into(),
        "顺序效应仅靠交错排程对冲,未做随机化检验".into(),
    ];
    if let Some(ar) = &spec.app_reset {
        uncontrolled.push(format!(
            "app_reset 声明 pkg={} clear={}: v1 只记录不执行复位(clear 未接线),设备初态漂移属无法控制因素",
            ar.pkg, ar.clear));
    } else {
        uncontrolled.push("spec 未声明 app_reset: 设备初态(应用数据/登录态)跨局漂移未受控".into());
    }

    // 逐任务逐局结果表(可复查)
    let per_task: Vec<Value> = ep_rows.iter().map(|r| json!({
        "round": r["round"], "task": r["task"], "arm": r["arm"],
        "run_id": r["run_id"], "achieved": r["achieved"], "stop": r["stop"],
        "calls": r["calls"], "tokens": r["tokens"], "wall_ms": r["wall_ms"],
        "kind": if eval_names.contains(r["task"].as_str().unwrap_or("")) { "eval" } else { "adapt" },
    })).collect();

    json!({
        "v": 1,
        "kind": "pf-experiment-report",
        "exp_id": spec.id,
        "scoring_proto": spec.scoring_proto,
        "generated_at": chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%z").to_string(),
        "declared_samples": declared,
        "actual_samples": ep_rows.len(),
        "arms": arms_sorted,
        "ablate": ablate,
        "min_meaningful_delta": spec.min_meaningful_delta,
        "budget": {"budget_calls_per_episode": spec.budget_calls, "token_cap": spec.token_cap},
        "inference_note": inference,
        "small_sample_raw_only": small_sample,
        "arms_summary": arm_objs,
        "comparisons": comparisons,
        "findings": findings,
        "uncontrolled_factors": uncontrolled,
        "episodes": per_task,
    })
}

/// 报告 Markdown 渲染(纯): 从 report JSON 出 report.md 文本
pub fn render_md(rep: &Value) -> String {
    let mut s = String::new();
    s.push_str(&format!("# 实验报告 {}\n\n", rep["exp_id"].as_str().unwrap_or("?")));
    s.push_str(&format!("- 评分协议: {}\n", rep["scoring_proto"].as_str().unwrap_or("?")));
    s.push_str(&format!("- 生成时间: {}\n", rep["generated_at"].as_str().unwrap_or("?")));
    s.push_str(&format!("- 样本: 声明 {} / 实际 {}\n", rep["declared_samples"], rep["actual_samples"]));
    s.push_str(&format!("- 推断纪律: {}\n", rep["inference_note"].as_str().unwrap_or("")));
    if let Some(ab) = rep["ablate"].as_str() {
        s.push_str(&format!("- 消融: {ab}(标注落账;runtime v1 未接线,见 findings)\n"));
    }
    s.push_str("\n## 逐臂汇总\n\n");
    s.push_str("| 臂 | 局数 | 达成 | 总体成功率 | adapt | eval | calls | tokens | wall p50/p95(ms) | 失败成本(calls/tokens) | pred通过率 | 无效尝试率 | 停臂 |\n");
    s.push_str("|---|---|---|---|---|---|---|---|---|---|---|---|---|\n");
    for a in rep["arms_summary"].as_array().cloned().unwrap_or_default() {
        let rate_cell = |v: &Value| {
            if v.is_null() {
                "-".into()
            } else {
                format!("{}/{} ({})", v["won"], v["n"], v["rate"])
            }
        };
        let wall = a["wall_ms_stat3"].as_array()
            .map(|v| format!("{}/{}", v[1], v[2]))
            .unwrap_or_else(|| "-".into());
        let pred = if a["pred_pass_rate"].is_null() { "-".into() }
            else { format!("{}/{} ({})", a["pred_pass_rate"]["pass"], a["pred_pass_rate"]["total"], a["pred_pass_rate"]["rate"]) };
        let inv = if a["invalid_attempt_rate"].is_null() { "-".into() }
            else { format!("{}/{} ({})", a["invalid_attempt_rate"]["invalid"], a["invalid_attempt_rate"]["acts"], a["invalid_attempt_rate"]["rate"]) };
        s.push_str(&format!(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {}/{} | {} | {} | {} |\n",
            a["arm"].as_str().unwrap_or("?"), a["episodes"], a["achieved"],
            a["success_overall"].as_f64().map(|f| f.to_string()).unwrap_or_else(|| "-".into()),
            rate_cell(&a["success_adapt"]), rate_cell(&a["success_eval"]),
            a["calls"], a["tokens"], wall,
            a["failed_cost"]["calls"], a["failed_cost"]["tokens"],
            pred, inv,
            a["stopped"].as_str().unwrap_or("-"),
        ));
    }
    s.push_str("\n## 臂间差异对照(min_meaningful_delta 口径)\n\n");
    for c in rep["comparisons"].as_array().cloned().unwrap_or_default() {
        let pair = c["pair"].as_array().cloned().unwrap_or_default();
        s.push_str(&format!("- {} vs {}: delta={} — {}\n",
            pair.first().and_then(|v| v.as_str()).unwrap_or("?"),
            pair.get(1).and_then(|v| v.as_str()).unwrap_or("?"),
            c["delta"], c["verdict"].as_str().unwrap_or("")));
    }
    s.push_str("\n## 结论与声明\n\n");
    for f in rep["findings"].as_array().cloned().unwrap_or_default() {
        s.push_str(&format!("- {}\n", f.as_str().unwrap_or("")));
    }
    s.push_str("\n## 无法控制的因素\n\n");
    for u in rep["uncontrolled_factors"].as_array().cloned().unwrap_or_default() {
        s.push_str(&format!("- {}\n", u.as_str().unwrap_or("")));
    }
    s.push_str("\n## 逐任务逐局结果\n\n");
    s.push_str("| 轮 | 任务 | 类 | 臂 | 局ID | 达成 | stop | calls | tokens | wall(ms) |\n");
    s.push_str("|---|---|---|---|---|---|---|---|---|---|\n");
    for e in rep["episodes"].as_array().cloned().unwrap_or_default() {
        s.push_str(&format!(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |\n",
            e["round"], e["task"].as_str().unwrap_or("?"), e["kind"].as_str().unwrap_or("?"),
            e["arm"].as_str().unwrap_or("?"), e["run_id"].as_str().unwrap_or("?"),
            e["achieved"], e["stop"].as_str().unwrap_or("?"), e["calls"], e["tokens"], e["wall_ms"],
        ));
    }
    s
}

// ══════════════ 以下为执行层(副作用集中;单测只覆盖纯函数与文件预处理) ══════════════

/// 实验目录布局: <tasks根>/_exp/<exp-id>/{ledger.jsonl, snapshot/, A/, B/, C/, report.*}
pub fn exp_root(tasks_root: &Path, id: &str) -> PathBuf {
    tasks_root.join("_exp").join(id)
}
pub fn arm_root(root: &Path, arm: &str) -> PathBuf {
    root.join(arm)
}

fn ledger_path(root: &Path) -> PathBuf {
    root.join("ledger.jsonl")
}

fn read_ledger(root: &Path) -> Vec<Value> {
    std::fs::read_to_string(ledger_path(root))
        .unwrap_or_default()
        .lines()
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect()
}

fn ledger_append(root: &Path, row: Value) -> Result<(), String> {
    use std::io::Write;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(ledger_path(root))
        .map_err(|e| format!("台账打开失败 {}: {e}", ledger_path(root).display()))?;
    writeln!(f, "{}", serde_json::to_string(&row).unwrap_or_default())
        .map_err(|e| format!("台账写入失败: {e}"))
}

/// 把一个任务目录的跨局经验四件拷到目标目录(存在才拷,目录先建)
fn copy_exp_files(src: &Path, dst: &Path, files: &[&str]) -> Result<(), String> {
    for f in files {
        let s = src.join(f);
        if s.exists() {
            std::fs::create_dir_all(dst).map_err(|e| format!("建目录 {}: {e}", dst.display()))?;
            std::fs::copy(&s, dst.join(f))
                .map_err(|e| format!("拷贝 {} → {}: {e}", s.display(), dst.join(f).display()))?;
        }
    }
    Ok(())
}

/// 启动快照(§7.2 冻结面): 主 tasks 根 → snapshot/。
/// 任务级四件 + _global 三件(全局经验同样是注入源,B 臂冻结必须含它)。
fn snapshot_experience(tasks_root: &Path, snap: &Path, spec: &ExpSpec) -> Result<(), String> {
    for t in spec.all_tasks() {
        copy_exp_files(&tasks_root.join(&t.task), &snap.join(&t.task), &EXP_FILES)?;
    }
    copy_exp_files(&tasks_root.join("_global"), &snap.join("_global"), &EXP_FILES_GLOBAL)?;
    Ok(())
}

/// 清掉臂根下全部注入源文件(任务目录与 _global;runs/ 账本不动——账是证据不是注入源)
fn purge_exp_files(arm_dir: &Path) -> Result<(), String> {
    if !arm_dir.is_dir() {
        return Ok(());
    }
    for e in std::fs::read_dir(arm_dir).map_err(|e| format!("读臂根 {}: {e}", arm_dir.display()))? {
        let e = e.map_err(|e| e.to_string())?;
        if !e.path().is_dir() {
            continue;
        }
        for f in EXP_FILES {
            let p = e.path().join(f);
            if p.exists() {
                std::fs::remove_file(&p).map_err(|e2| format!("删 {}: {e2}", p.display()))?;
            }
        }
    }
    Ok(())
}

/// 每局跑前臂预处理(§7.1 臂语义):
/// A 清空注入源(无跨局经验);B 清后从快照恢复(冻结,抹掉上一局写回);C 不动(持续演进)。
fn prepare_arm(arm: &str, arm_dir: &Path, snap: &Path) -> Result<(), String> {
    match arm {
        "A" => purge_exp_files(arm_dir),
        "B" => {
            purge_exp_files(arm_dir)?;
            if snap.is_dir() {
                for e in std::fs::read_dir(snap).map_err(|e| format!("读快照 {}: {e}", snap.display()))? {
                    let e = e.map_err(|e| e.to_string())?;
                    let name = e.file_name().to_string_lossy().to_string();
                    let files: &[&str] = if name == "_global" { &EXP_FILES_GLOBAL } else { &EXP_FILES };
                    copy_exp_files(&e.path(), &arm_dir.join(&name), files)?;
                }
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

/// 逐臂机制统计采集(执行层,读臂根): pred 通过率 + 无效尝试率
fn collect_mech(arm_dir: &Path) -> MechStats {
    let mut m = MechStats::default();
    if !arm_dir.is_dir() {
        return m;
    }
    let read_jsonl = |p: &Path| -> Vec<Value> {
        std::fs::read_to_string(p)
            .unwrap_or_default()
            .lines()
            .filter_map(|l| serde_json::from_str(l).ok())
            .collect()
    };
    if let Ok(dirs) = std::fs::read_dir(arm_dir) {
        for e in dirs.flatten() {
            let dir = e.path();
            if !dir.is_dir() {
                continue;
            }
            let (p, t) = pred_outcomes(&read_jsonl(&dir.join("hypotheses.jsonl")));
            m.pred_pass += p;
            m.pred_total += t;
            if let Ok(runs) = std::fs::read_dir(dir.join("runs")) {
                for r in runs.flatten() {
                    let (inv, acts) = invalid_attempts(&read_jsonl(&r.path().join("log.jsonl")));
                    m.invalid += inv;
                    m.acts += acts;
                }
            }
        }
    }
    m
}

/// 一局结果(执行层产出,落账与预算判定共用)
pub struct EpisodeOut {
    pub run_id: String,
    pub stop: String,
    pub achieved: bool,
    pub calls: u64,
    pub tokens: u64,
    pub wall_ms: u64,
    /// 设备级失败(§7.6 停滞口径): 无 summary 或无 r=end 收官记录;
    /// achieved=false 的任务失败不算(那是模型没跑对,不是设备挂了)
    pub device_failure: bool,
}

/// 子进程跑一局(执行层,单测不调): current_exe 自调用 run 子命令,
/// env PF_TASKS_ROOT 钉臂根(隔离);消融标记经 env 传递(v1 runtime 未接线,报告如实声明)。
fn run_episode(
    arm_dir: &Path,
    t: &TaskSpec,
    spec: &ExpSpec,
    ablate: Option<&str>,
) -> EpisodeOut {
    let fail = |why: &str| EpisodeOut {
        run_id: String::new(),
        stop: format!("SPAWN_FAIL({why})"),
        achieved: false,
        calls: 0,
        tokens: 0,
        wall_ms: 0,
        device_failure: true,
    };
    let exe = match std::env::current_exe() {
        Ok(e) => e,
        Err(e) => return fail(&format!("current_exe: {e}")),
    };
    let mut cmd = std::process::Command::new(exe);
    cmd.arg("run")
        .arg("--task").arg(&t.task)
        .arg("--max-steps").arg(spec.max_steps.to_string())
        .arg("--budget-calls").arg(spec.budget_calls.to_string());
    if let Some(app) = &t.app {
        cmd.arg("--app").arg(app);
    }
    if let Some(a) = &t.assert {
        cmd.arg("--assert").arg(a);
    }
    if let Some(s) = &spec.serial {
        cmd.arg("--serial").arg(s);
    }
    cmd.arg(&t.goal);
    cmd.env("PF_TASKS_ROOT", arm_dir);
    match ablate {
        Some("no-active-testing") => {
            cmd.env("PF_ABLATE_NO_TESTING", "1");
        }
        Some("no-cap-screening") => {
            cmd.env("PF_ABLATE_NO_CAP", "1");
        }
        _ => {}
    }
    let out = match cmd.output() {
        Ok(o) => o,
        Err(e) => return fail(&format!("spawn: {e}")),
    };
    let stdout = String::from_utf8_lossy(&out.stdout);
    let Some(sum) = parse_summary_line(&stdout) else {
        return fail("stdout 无 summary 行(子进程未跑到收官)");
    };
    let run_id = sum.get("run").cloned().unwrap_or_default();
    // 兜底核对: 臂根下该局 log.jsonl 的 r=end 是权威源(记录契约),summary 只是快报
    let end_rec = if !run_id.is_empty() {
        let p = arm_dir.join(&t.task).join("runs").join(&run_id).join("log.jsonl");
        std::fs::read_to_string(p)
            .unwrap_or_default()
            .lines()
            .filter_map(|l| serde_json::from_str::<Value>(l).ok())
            .find(|r| r["r"] == "end")
    } else {
        None
    };
    let pick = |key: &str, end_key: &str| -> String {
        end_rec.as_ref()
            .and_then(|e| e[end_key].as_str().map(String::from)
                .or_else(|| e[end_key].as_u64().map(|v| v.to_string()))
                .or_else(|| e[end_key].as_bool().map(|v| v.to_string())))
            .or_else(|| sum.get(key).cloned())
            .unwrap_or_default()
    };
    let to_u64 = |key: &str, end_key: &str| -> u64 {
        end_rec.as_ref()
            .and_then(|e| e[end_key].as_u64())
            .or_else(|| sum.get(key).and_then(|v| v.parse().ok()))
            .unwrap_or(0)
    };
    let achieved = end_rec.as_ref().and_then(|e| e["achieved"].as_bool())
        .or_else(|| sum.get("achieved").map(|v| v == "true"))
        .unwrap_or(false);
    EpisodeOut {
        run_id,
        stop: pick("stop", "stop"),
        achieved,
        calls: to_u64("calls", "calls"),
        tokens: to_u64("tokens", "tokens"),
        wall_ms: to_u64("wall", "wall_ms"),
        // 有 end 记录=局正常收官(任务失败也算);无 end=设备级失败(崩溃/写账失败/未跑完)
        device_failure: end_rec.is_none(),
    }
}

/// 报告生成(执行层): 从台账 + 臂根现态重算,落 report.md + report.json
pub fn generate_report(tasks_root: &Path, spec: &ExpSpec, arms_run: &[String], ablate: Option<&str>) -> Result<Value, String> {
    let root = exp_root(tasks_root, &spec.id);
    let rows = read_ledger(&root);
    if rows.is_empty() {
        return Err(format!("台账为空: {}(--report-only 需要有已落账的局)", ledger_path(&root).display()));
    }
    let mut mech = HashMap::new();
    for a in arms_run {
        mech.insert(a.clone(), collect_mech(&arm_root(&root, a)));
    }
    let rep = build_report(spec, arms_run, &rows, &mech, ablate);
    std::fs::write(root.join("report.json"), serde_json::to_string_pretty(&rep).unwrap_or_default() + "\n")
        .map_err(|e| format!("写 report.json: {e}"))?;
    std::fs::write(root.join("report.md"), render_md(&rep))
        .map_err(|e| format!("写 report.md: {e}"))?;
    Ok(rep)
}

/// 实验主循环(执行层)。返回给 CLI 的摘要 JSON。
pub fn run(
    tasks_root: &Path,
    spec: &ExpSpec,
    only_arm: Option<&str>,
    ablate: Option<&str>,
    resume: bool,
) -> Result<Value, String> {
    let root = exp_root(tasks_root, &spec.id);
    let snap = root.join("snapshot");
    let prior = read_ledger(&root);
    let fresh = prior.is_empty();
    if !fresh && !resume {
        return Err(format!(
            "实验 {} 已有台账 {} 行({});续跑加 --resume,只重出报告加 --report-only(拒绝重复记账)",
            spec.id,
            prior.len(),
            ledger_path(&root).display()));
    }

    // 臂集: --arm 过滤;排程臂序固定字母序(schedule 内排序)
    let arms: Vec<String> = match only_arm {
        Some(a) => vec![a.to_string()],
        None => spec.arms.clone(),
    };
    let tasks = spec.all_tasks();
    let declared = spec.rounds as u64 * tasks.len() as u64 * arms.len() as u64;

    if fresh {
        // 启动快照(§7.2): B/C 臂的冻结起点;--resume 绝不重拍(快照须全程不变)
        std::fs::create_dir_all(&snap).map_err(|e| format!("建快照目录: {e}"))?;
        snapshot_experience(tasks_root, &snap, spec)?;
        // C 臂起步=快照内容,此后持续演进不回滚;B 臂由 prepare_arm 每局恢复
        for a in &arms {
            let dir = arm_root(&root, a);
            std::fs::create_dir_all(&dir).map_err(|e| format!("建臂根 {}: {e}", dir.display()))?;
            if a == "C" {
                prepare_arm("B", &dir, &snap)?; // C 的起步装载等价于一次快照恢复
            }
        }
        println!("实验 {}: 声明样本 {} 局(臂 {} × 任务 {} × 轮 {}),快照已冻结 → {}",
            spec.id, declared, arms.len(), tasks.len(), spec.rounds, snap.display());
    } else {
        println!("实验 {}: --resume 续跑(台账已有 {} 行,快照保持 {} 不变)",
            spec.id, prior.len(), snap.display());
    }

    let slots_all = schedule(&arms, tasks.len(), spec.rounds);
    let mut done = completed(&prior);
    let mut stopped = stopped_arms(&prior);
    let mut tokens_used = arm_tokens(&prior);
    let mut stall_streak: HashMap<String, u32> = HashMap::new();
    let mut ran_this_turn = 0u64;

    for slot in remaining_slots(&slots_all, &tasks, &done, &stopped) {
        let arm = slot.arm.as_str();
        let t = tasks[slot.task_idx];
        // 循环内停臂守卫: remaining_slots 是循环前快照,
        // 臂中途停(stall/token_cap)后其后续槽位必须跳过,不得重复落账 arm_stop
        if stopped.contains_key(arm) {
            continue;
        }
        // §7.6 预算硬顶: 触 token_cap 停臂落账,本臂后续槽位全跳
        if token_cap_hit(*tokens_used.get(arm).unwrap_or(&0), spec.token_cap) {
            let row = json!({"ts": chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%z").to_string(),
                "arm": arm, "event": "arm_stop", "stop_reason": "token_cap",
                "detail": format!("累计 tokens {} >= cap {}", tokens_used.get(arm).unwrap_or(&0), spec.token_cap)});
            ledger_append(&root, row)?;
            stopped.insert(arm.to_string(), "token_cap".into());
            println!("[{arm}] 触 token_cap,停臂(其余臂继续)");
            continue;
        }
        let dir = arm_root(&root, arm);
        prepare_arm(arm, &dir, &snap)?;
        println!("══ 轮{}/{} 臂{} 任务[{}] ══", slot.round, spec.rounds, arm, t.task);
        let out = run_episode(&dir, t, spec, ablate);
        ledger_append(&root, json!({
            "ts": chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%z").to_string(),
            "arm": arm, "task": t.task, "round": slot.round,
            "run_id": out.run_id, "stop": out.stop, "achieved": out.achieved,
            "calls": out.calls, "tokens": out.tokens, "wall_ms": out.wall_ms,
            "ablate": ablate,
        }))?;
        ran_this_turn += 1;
        done.insert((arm.to_string(), t.task.clone(), slot.round));
        *tokens_used.entry(arm.to_string()).or_insert(0) += out.tokens;
        println!("    → run={} stop={} achieved={} tokens={}{}",
            out.run_id, out.stop, out.achieved, out.tokens,
            if out.device_failure { " [设备级失败]" } else { "" });
        // §7.6 停滞: 连续设备级失败达阈值停臂;任何正常收官局重置计数
        if out.device_failure {
            let c = { let e = stall_streak.entry(arm.to_string()).or_insert(0); *e += 1; *e };
            if c >= spec.stall_device_failures {
                ledger_append(&root, json!({
                    "ts": chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%z").to_string(),
                    "arm": arm, "event": "arm_stop", "stop_reason": "stall",
                    "detail": format!("连续 {c} 局设备级失败(>= {})", spec.stall_device_failures)}))?;
                stopped.insert(arm.to_string(), "stall".into());
                println!("[{arm}] 连续 {c} 局设备级失败,停臂(其余臂继续)");
            }
        } else {
            stall_streak.insert(arm.to_string(), 0);
        }
    }

    let rep = generate_report(tasks_root, spec, &arms, ablate)?;
    Ok(json!({
        "exp_id": spec.id,
        "root": root.display().to_string(),
        "declared_samples": declared,
        "ran_this_turn": ran_this_turn,
        "stopped_arms": stopped,
        "report_json": root.join("report.json").display().to_string(),
        "report_md": root.join("report.md").display().to_string(),
        "arms_summary": rep["arms_summary"].clone(),
        "findings": rep["findings"].clone(),
    }))
}

// ══════════════ 单测(纯函数与文件预处理;不跑真子进程) ══════════════

#[cfg(test)]
mod tests {
    use super::*;

    fn spec_text() -> String {
        r#"
id = "demo"
arms = ["A", "B", "C"]
rounds = 2
budget_calls = 10
token_cap = 100000
scoring_proto = "v1"
min_meaningful_delta = 0.15
adapt_tasks = [
  { task = "设置遍历", goal = "打开WLAN", app = "com.android.settings", assert = "WLAN" },
]
eval_tasks = [
  { task = "拨号保留", goal = "打开拨号盘", app = "com.android.dialer" },
]
"#
        .to_string()
    }

    #[test]
    fn spec_parse_ok_and_defaults() {
        let s = parse_spec(&spec_text()).unwrap();
        assert_eq!(s.id, "demo");
        assert_eq!(s.max_steps, 12, "max_steps 缺省 12");
        assert_eq!(s.stall_device_failures, 3, "停滞阈值缺省 3");
        assert_eq!(s.all_tasks().len(), 2);
        assert!(s.eval_tasks[0].assert.is_none());
    }

    #[test]
    fn spec_reject_bad() {
        // 缺 scoring_proto(必填)
        let bad = spec_text().replace("scoring_proto = \"v1\"\n", "");
        assert!(parse_spec(&bad).is_err(), "缺 scoring_proto 必须拒绝");
        // 协议版本钉住(§7.3): 非 v1 拒绝
        let bad = spec_text().replace("\"v1\"", "\"v2\"");
        assert!(parse_spec(&bad).is_err());
        // id 路径注入
        let bad = spec_text().replace("\"demo\"", "\"../x\"");
        assert!(parse_spec(&bad).is_err());
        // 未知臂
        let bad = spec_text().replace("\"C\"", "\"D\"");
        assert!(parse_spec(&bad).is_err());
        // rounds=0
        let bad = spec_text().replace("rounds = 2", "rounds = 0");
        assert!(parse_spec(&bad).is_err());
        // delta 越界
        let bad = spec_text().replace("0.15", "1.5");
        assert!(parse_spec(&bad).is_err());
        // 任务全空
        let bad = r#"
id = "x"
rounds = 1
budget_calls = 5
token_cap = 100
scoring_proto = "v1"
"#;
        assert!(parse_spec(bad).is_err());
    }

    #[test]
    fn schedule_interleaves_arms_alphabetically() {
        // 乱序输入也得同一序列: 轮内逐任务按臂字母序交错(A1B1C1, A2B2C2…)
        let arms = vec!["C".to_string(), "A".to_string(), "B".to_string()];
        let v = schedule(&arms, 2, 2);
        let key: Vec<(u32, usize, &str)> = v.iter()
            .map(|s| (s.round, s.task_idx, s.arm.as_str())).collect();
        assert_eq!(key, vec![
            (1, 0, "A"), (1, 0, "B"), (1, 0, "C"),
            (1, 1, "A"), (1, 1, "B"), (1, 1, "C"),
            (2, 0, "A"), (2, 0, "B"), (2, 0, "C"),
            (2, 1, "A"), (2, 1, "B"), (2, 1, "C"),
        ]);
        // 单臂过滤场景
        let one = schedule(&["B".to_string()], 2, 1);
        assert_eq!(one.len(), 2);
        assert!(one.iter().all(|s| s.arm == "B"));
    }

    #[test]
    fn ledger_dedup_resume_skips() {
        let rows = vec![
            json!({"arm":"A","task":"设置遍历","round":1,"run_id":"r1","tokens":100,"achieved":true}),
            json!({"arm":"B","task":"设置遍历","round":1,"run_id":"r2","tokens":200,"achieved":false}),
            json!({"arm":"B","event":"arm_stop","stop_reason":"token_cap"}),
        ];
        let done = completed(&rows);
        assert!(done.contains(&("A".into(), "设置遍历".into(), 1)));
        assert_eq!(done.len(), 2);
        let stopped = stopped_arms(&rows);
        assert_eq!(stopped.get("B").unwrap(), "token_cap");
        let toks = arm_tokens(&rows);
        assert_eq!(toks.get("A"), Some(&100));
        assert_eq!(toks.get("B"), Some(&200));

        // 续跑: 已完成三元组跳过 + 停臂整臂跳过
        let spec = parse_spec(&spec_text()).unwrap();
        let tasks = spec.all_tasks();
        let slots = schedule(&spec.arms, tasks.len(), spec.rounds);
        let rest = remaining_slots(&slots, &tasks, &done, &stopped);
        assert!(rest.iter().all(|s| s.arm != "B"), "停臂 B 不再排");
        assert!(!rest.iter().any(|s| s.arm == "A" && s.task_idx == 0 && s.round == 1),
            "已完成 (A,设置遍历,1) 跳过");
        assert!(rest.iter().any(|s| s.arm == "C" && s.round == 2), "C 未做的还在");
    }

    #[test]
    fn token_cap_boundary() {
        assert!(token_cap_hit(100, 100), "触顶即停(>= 语义)");
        assert!(!token_cap_hit(99, 100));
    }

    #[test]
    fn summary_line_parsing() {
        let out = "一些日志\n更多输出\nsummary: run=20260914-101010 stop=done steps=8 calls=9 tokens=4321 wall=12.3s achieved=true\n";
        let m = parse_summary_line(out).unwrap();
        assert_eq!(m.get("run").unwrap(), "20260914-101010");
        assert_eq!(m.get("achieved").unwrap(), "true");
        assert_eq!(m.get("tokens").unwrap(), "4321");
        // 取最后一行 summary(多行时)
        let out2 = "summary: run=old stop=x achieved=false\nsummary: run=new stop=done achieved=true\n";
        assert_eq!(parse_summary_line(out2).unwrap().get("run").unwrap(), "new");
        assert!(parse_summary_line("没有 summary 行").is_none());
    }

    #[test]
    fn mech_counters() {
        let log = vec![
            json!({"r":"act","n":1}), json!({"r":"act","n":2}), json!({"r":"act","n":3}),
            json!({"r":"diff","n":1,"d":"rejected(越界)"}),
            json!({"r":"diff","n":2,"d":"none"}),
            json!({"r":"diff","n":3,"d":"+[新元素]"}),
        ];
        let (inv, acts) = invalid_attempts(&log);
        assert_eq!((inv, acts), (2, 3), "驳回+空击算无效,真变化不算");

        let hyps = vec![
            json!({"r":"pred","op":"register","id":"p1"}),
            json!({"r":"pred","op":"outcome","id":"p1","assert":"pass"}),
            json!({"r":"pred","op":"outcome","id":"p2","assert":"fail"}),
            json!({"r":"pred","op":"outcome","id":"p3","assert":"inconclusive"}),
        ];
        let (pass, total) = pred_outcomes(&hyps);
        assert_eq!((pass, total), (1, 3));
    }

    #[test]
    fn report_small_sample_and_negative_result() {
        // 样本<10 只报原始分布;C 大幅退步时如实写负结果
        let spec = parse_spec(&spec_text()).unwrap();
        let rows = vec![
            json!({"arm":"A","task":"设置遍历","round":1,"run_id":"a1","achieved":true,"calls":5,"tokens":100,"wall_ms":9000,"stop":"done"}),
            json!({"arm":"C","task":"设置遍历","round":1,"run_id":"c1","achieved":false,"calls":9,"tokens":300,"wall_ms":20000,"stop":"stall"}),
        ];
        let mech = HashMap::new();
        let rep = build_report(&spec, &["A".into(), "C".into()], &rows, &mech, Some("no-active-testing"));
        assert_eq!(rep["small_sample_raw_only"], true, "2 局 < 10,只报原始分布");
        assert!(rep["inference_note"].as_str().unwrap().contains("不做统计推断"));
        // A=1/1=1.0, C=0/1=0.0 → delta A-C=+1.0 → "A 优于 C";findings 里 C 相对 A 为负结果
        let cmp = rep["comparisons"].as_array().unwrap();
        assert_eq!(cmp[0]["delta"], 1.0);
        let findings: Vec<&str> = rep["findings"].as_array().unwrap()
            .iter().map(|f| f.as_str().unwrap()).collect();
        assert!(findings.iter().any(|f| f.contains("负结果")), "退步要如实写负结果: {findings:?}");
        assert!(findings.iter().any(|f| f.contains("runtime 尚未接线")), "消融诚实缺口必须声明");
        assert!(findings.iter().any(|f| f.contains("机制已实现")), "已实现≠已验证的拆分声明必须在");
        // 失败成本单列: C 臂失败局 tokens=300
        let c = rep["arms_summary"].as_array().unwrap().iter()
            .find(|a| a["arm"] == "C").unwrap();
        assert_eq!(c["failed_cost"]["tokens"], 300);
        assert_eq!(c["success_overall"], 0.0);
        // markdown 渲染不炸且含关键段
        let md = render_md(&rep);
        assert!(md.contains("## 逐臂汇总") && md.contains("## 逐任务逐局结果"));
        assert!(md.contains("负结果"));
    }

    #[test]
    fn prepare_arm_semantics() {
        // 文件预处理(不跑子进程): A 清空 / B 恢复快照 / C 不动
        let d = std::env::temp_dir().join(format!("pf_exp_test_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        let snap = d.join("snapshot");
        std::fs::create_dir_all(snap.join("任务X")).unwrap();
        std::fs::write(snap.join("任务X/lessons.jsonl"), "{\"r\":\"lesson\"}\n").unwrap();
        std::fs::create_dir_all(snap.join("_global")).unwrap();
        std::fs::write(snap.join("_global/lessons.jsonl"), "{\"r\":\"lesson\",\"g\":1}\n").unwrap();

        // B: 臂根有上一局写回的脏经验 + 局账本 → 恢复后脏经验消失、快照回来、账本不动
        let b = d.join("B");
        std::fs::create_dir_all(b.join("任务X/runs/r1")).unwrap();
        std::fs::write(b.join("任务X/hypotheses.jsonl"), "{\"r\":\"hyp\"}\n").unwrap();
        std::fs::write(b.join("任务X/runs/r1/log.jsonl"), "{\"r\":\"end\"}\n").unwrap();
        prepare_arm("B", &b, &snap).unwrap();
        assert!(b.join("任务X/lessons.jsonl").exists(), "B 恢复快照");
        assert!(!b.join("任务X/hypotheses.jsonl").exists(), "B 抹掉局内写回(快照没有的)");
        assert!(b.join("任务X/runs/r1/log.jsonl").exists(), "局账本是证据,绝不清");
        assert!(b.join("_global/lessons.jsonl").exists(), "全局经验也冻结恢复");

        // A: 一切注入源清空(含 _global),账本留
        let a = d.join("A");
        std::fs::create_dir_all(a.join("_global")).unwrap();
        std::fs::write(a.join("_global/lessons.jsonl"), "x\n").unwrap();
        std::fs::create_dir_all(a.join("任务X")).unwrap();
        std::fs::write(a.join("任务X/tree.json"), "{}").unwrap();
        prepare_arm("A", &a, &snap).unwrap();
        assert!(!a.join("_global/lessons.jsonl").exists(), "A 无跨局经验: 全局也清");
        assert!(!a.join("任务X/tree.json").exists());

        // C: 原样不动(持续演进)
        let c = d.join("C");
        std::fs::create_dir_all(c.join("任务X")).unwrap();
        std::fs::write(c.join("任务X/lessons.jsonl"), "y\n").unwrap();
        prepare_arm("C", &c, &snap).unwrap();
        assert_eq!(std::fs::read_to_string(c.join("任务X/lessons.jsonl")).unwrap(), "y\n");

        let _ = std::fs::remove_dir_all(&d);
    }
}
