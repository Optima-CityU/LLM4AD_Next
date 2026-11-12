//! 判定的「记分卡」与三处去掉的一票否决 (judge/v1-scorecard)。
//!
//! 1. **画质不判通过, 只列数** (2026-10-21 用户: 「不要『通过』这个指标。都列出来, 让人选就行」):
//!    `verdict.quality_gap` 是指标行 + 参考线 + 对照表 + 摘要 (见 `crate::iqsheet`), 没有通过 / 不通过字段。
//!    `protocol.image_policy` 只决定参考线要不要拿来省上机时间:
//!    - `"gate"` (缺省): 有指标在参考线外就 ABORT, 不量性能 (调用方自选的省时规则, 原行为)。
//!    - `"report"`: 给基于模型的超分 / 插帧候选用。画质照列, 性能照判。性能有显著改善而画质有指标
//!      在参考线外 / 算不了 → 状态 `KEEP_WITH_QUALITY_GAP` (留给人看表选), 不是 PASS。
//! 2. **锁帧时帧时变长不一定算退化**: 见 [`capped_budget_check`]。
//! 3. **臂内悄悄回退**: 旋钮自报回退 / 部分施加的那一臂记「未按设计施加」, 连同同一轮的对照臂
//!    一起拿出统计, 见 [`knob_application`]。
//! 4. **记分卡** `verdict.scorecard`: 每个维度一行 (帧时 / 功耗 / 画质差距 / 测量有效性),
//!    只有安全 (结温上限、崩溃) 和「没测成」让整个结果无效, 见 [`build`]。

use serde_json::{json, Value};

// ══════════════ 1. 画质策略 ══════════════

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ImagePolicy {
    /// 画面不过门槛 = 画面出错, 不量 / 不判性能 (原行为)。
    Gate,
    /// 画面判据只记差距, 不否决性能判定 (基于模型的超分 / 插帧)。
    Report,
}

impl ImagePolicy {
    /// `protocol.image_policy`; 缺省 gate。写了别的值 → Err (校验阶段拒收, 不碰设备)。
    pub(crate) fn from_request(req: &Value) -> Result<Self, String> {
        match req["protocol"].get("image_policy") {
            None | Some(Value::Null) => Ok(ImagePolicy::Gate),
            Some(v) => match v.as_str() {
                Some("gate") => Ok(ImagePolicy::Gate),
                Some("report") => Ok(ImagePolicy::Report),
                _ => Err(format!("protocol.image_policy 只认 \"gate\" / \"report\", 收到 {v}")),
            },
        }
    }

    pub(crate) fn as_str(self) -> &'static str {
        match self {
            ImagePolicy::Gate => "gate",
            ImagePolicy::Report => "report",
        }
    }

    pub(crate) fn is_report(self) -> bool {
        self == ImagePolicy::Report
    }
}

/// 状态: 性能过了, 画面差距没关闭 (只在 image_policy=report 时出现)。
pub(crate) const KEEP_WITH_QUALITY_GAP: &str = "KEEP_WITH_QUALITY_GAP";

/// 画质相对参考线的内部状态 (只用来定 status, 不进输出)。
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum GapState {
    /// 各指标都算得出且都在参考线原生一侧。
    Closed,
    /// 有指标在参考线外。
    Open(String),
    /// 有指标算不了 / 没比 (截图失败、场景太乱、没有静态画面)。
    Unverified(String),
}

impl GapState {
    pub(crate) fn reason(&self) -> Option<&str> {
        match self {
            GapState::Closed => None,
            GapState::Open(r) | GapState::Unverified(r) => Some(r),
        }
    }
}

/// `verdict.quality_gap`: 指标行 (`metrics`) + 对照表 (`sheet`) + 摘要 (`summary`), 不判通过。
/// `rows` = 画质指标行 (见 [`image_rows`]); `detail` = 画面比对原样 (iq-judge 报告 / PSNR / storeop-check)。
/// `state` 只用来补「算不了」的原因, 不输出成通过与否。
pub(crate) fn quality_gap_json(policy: ImagePolicy, state: &GapState, source: &str, rows: Vec<Value>, detail: Value) -> Value {
    let mut nc: Vec<String> = Vec::new();
    if let GapState::Unverified(r) = state {
        nc.push(r.clone());
    }
    let mut beyond = crate::iqsheet::beyond_ref_line(&rows);
    if let (GapState::Open(r), true) = (state, beyond.is_empty()) {
        // 没有数值行的来源 (storeop-check 现场开关): 原因原样列出
        beyond.push(r.clone());
    }
    json!({
        "policy": policy.as_str(),
        "source": source,
        "metrics": rows,
        "beyond_ref_line": beyond,
        "not_computed": nc,
        "summary": crate::iqsheet::summary(&rows, &nc),
        "sheet": crate::iqsheet::sheet(&[("候选".to_string(), rows.clone())]),
        "detail": detail,
        "diagnose": Value::Null,
        "diagnose_note": "逐块看哪种降质、在哪: 对 evidence_dir 里的截图离线跑 phonefarm iq-diagnose",
    })
}

/// 画质指标行: iq-judge 的三项 (稳定块 PSNR / 1-SSIM / 细节能量) + 全帧 PSNR (对同设置重拍噪声底)。
/// `iq_rep` = iq-judge 报告 (没有就 Null); 全帧 PSNR 的参考线 = 噪声底 − `margin_db`。
pub(crate) fn image_rows(iq_rep: &Value, psnr_used: Option<f64>, noise_floor: Option<f64>, margin_db: f64) -> Vec<Value> {
    let mut rows: Vec<Value> = iq_rep["metrics"].as_array().cloned().unwrap_or_default();
    if psnr_used.is_some() || noise_floor.is_some() {
        rows.push(crate::iqsheet::row(
            "frame_psnr_db",
            "全帧 PSNR (同位置截图)",
            "dB",
            crate::iqsheet::Better::Higher,
            psnr_used,
            noise_floor,
            noise_floor.map(|n| n - margin_db),
            &format!("同设置重拍噪声底 − {margin_db:.1} dB"),
            "场景在动时被运动主导, 只作参照",
        ));
    }
    rows
}

/// 性能判定之后定最终状态。`perf_keep` = 统计判定「有显著改善且无显著退化」。
///
/// - 画面差距关闭 → PASS / REJECT (原行为)。
/// - report 下性能过了而差距没关闭 (不通过或判不了) → KEEP_WITH_QUALITY_GAP。
/// - gate 下性能过了而画面判不了 → REJECT (原行为: 增益要求画面确认不变)。
///
/// 不是 PASS 的结果一律把 `is_pareto_improvement` 置 false (性能结论留在 `would_keep`),
/// 免得只看 `is_pareto_improvement` 的上层把它当成可采纳的增益。
pub(crate) fn finalize_status(report: &mut Value, perf_keep: bool, policy: ImagePolicy, state: &GapState, gap: Value) {
    report["verdict"]["quality_gap"] = gap;
    let stat_reason = report["verdict"]["reason"].as_str().unwrap_or("").to_string();
    let status = match (perf_keep, state) {
        (false, _) => "REJECT",
        (true, GapState::Closed) => "PASS",
        (true, _) if policy.is_report() => KEEP_WITH_QUALITY_GAP,
        (true, _) => "REJECT",
    };
    report["status"] = json!(status);
    if status != "PASS" {
        report["verdict"]["is_pareto_improvement"] = json!(false);
    }
    if perf_keep && status != "PASS" {
        report["verdict"]["would_keep"] = json!(true);
        let why = state.reason().unwrap_or("");
        report["verdict"]["reason"] = json!(if status == KEEP_WITH_QUALITY_GAP {
            format!("性能有显著改善; 画质有指标在参考线外或算不了 ({why}): 画质数字与对照表见 verdict.quality_gap, 由人选 (统计结果: {stat_reason})")
        } else {
            report["verdict"]["image_unverified"] = json!(why);
            format!("性能有显著改善, 但画质有指标算不了 ({why}); image_policy=gate 下不记为增益 (统计结果: {stat_reason})")
        });
    }
}

// ══════════════ 2. 锁帧时的帧预算 ══════════════

/// p95 帧时允许超出帧预算 (1000/上限) 的比例。
pub(crate) const CAP_BUDGET_MARGIN: f64 = 0.05;
/// 帧率守住上限: 候选臂 fps 均值 ≥ 上限 × (1 − 这个)。与 looptrace 认上限的容差同为 2%。
pub(crate) const CAP_FPS_HOLD_TOL: f64 = 0.02;
/// 丢帧比例 (1 − fps/上限) 候选臂比对照臂多出的上限。
pub(crate) const CAP_MISSED_FRAC_TOL: f64 = 0.005;
/// 长帧 (p99) 候选臂相对对照臂允许多出的比例 (p99 本身还在帧预算内时不看这一项)。
pub(crate) const CAP_P99_TOL: f64 = 0.10;

fn mean_of(runs: &[Value], k: &str) -> Option<f64> {
    let xs: Vec<f64> = runs.iter().filter_map(|r| r["summary"][k].as_f64()).collect();
    (!xs.is_empty()).then(|| xs.iter().sum::<f64>() / xs.len() as f64)
}

fn max_of(runs: &[Value], k: &str) -> Option<f64> {
    runs.iter().filter_map(|r| r["summary"][k].as_f64()).reduce(f64::max)
}

fn r3(x: f64) -> f64 {
    (x * 1000.0).round() / 1000.0
}

/// 负载被帧率上限钉住 (`cap` fps) 时, 候选臂的帧还放不放得进帧预算。
///
/// 三条都满足才算放得进 (`ok = true`), 这时 frame_p95 变长不算退化:
/// - 每一轮候选臂 p95 ≤ 1000/cap × (1 + [`CAP_BUDGET_MARGIN`]);
/// - 候选臂 fps 均值守住上限 (≥ cap × (1 − [`CAP_FPS_HOLD_TOL`]));
/// - 丢帧不增加: 丢帧比例 (1 − fps/cap) 不比对照臂多 [`CAP_MISSED_FRAC_TOL`] 以上;
///   p99 (长帧) 若超出帧预算, 不比对照臂长 [`CAP_P99_TOL`] 以上 (没有 p99 的旧数据不看这一项)。
///
/// 回包里给出帧预算用掉多少 (`headroom_used_pct` = p95 / 帧预算 × 100), 两臂都给。
pub(crate) fn capped_budget_check(cap: f64, knob_runs: &[Value], ctrl_runs: &[Value]) -> Value {
    let budget = 1000.0 / cap;
    let limit = budget * (1.0 + CAP_BUDGET_MARGIN);
    let mut fails: Vec<String> = Vec::new();
    let knob_p95_max = max_of(knob_runs, "frame_p95");
    let (knob_p95, ctrl_p95) = (mean_of(knob_runs, "frame_p95"), mean_of(ctrl_runs, "frame_p95"));
    match knob_p95_max {
        Some(p) if p <= limit => {}
        Some(p) => fails.push(format!("候选臂 p95 最长 {p:.2} ms > 帧预算 {budget:.2} ms × {:.2}", 1.0 + CAP_BUDGET_MARGIN)),
        None => fails.push("候选臂没有 frame_p95".into()),
    }
    let (kf, cf) = (mean_of(knob_runs, "fps_mean"), mean_of(ctrl_runs, "fps_mean"));
    match kf {
        Some(f) if f >= cap * (1.0 - CAP_FPS_HOLD_TOL) => {}
        Some(f) => fails.push(format!("候选臂 fps {f:.2} 没守住上限 {cap} (容差 {:.0}%)", CAP_FPS_HOLD_TOL * 100.0)),
        None => fails.push("候选臂没有 fps_mean".into()),
    }
    let missed = |f: Option<f64>| f.map(|f| (1.0 - f / cap).max(0.0));
    let (km, cm) = (missed(kf), missed(cf));
    if let (Some(k), Some(c)) = (km, cm) {
        if k > c + CAP_MISSED_FRAC_TOL {
            fails.push(format!("丢帧比例 {:.2}% 比对照臂 {:.2}% 多 (容差 {:.1} 个百分点)", k * 100.0, c * 100.0, CAP_MISSED_FRAC_TOL * 100.0));
        }
    } else {
        fails.push("丢帧比例算不了 (缺 fps_mean)".into());
    }
    let (k99, c99) = (mean_of(knob_runs, "frame_p99"), mean_of(ctrl_runs, "frame_p99"));
    let p99_note = match (k99, c99) {
        (Some(k), _) if k <= limit => "候选臂 p99 也在帧预算内".to_string(),
        (Some(k), Some(c)) if k <= c * (1.0 + CAP_P99_TOL) => format!("p99 {k:.2} ms 不比对照臂 {c:.2} ms 长 {:.0}% 以上", CAP_P99_TOL * 100.0),
        (Some(k), Some(c)) => {
            fails.push(format!("长帧变多: 候选臂 p99 {k:.2} ms 比对照臂 {c:.2} ms 长 {:.0}% 以上", CAP_P99_TOL * 100.0));
            String::new()
        }
        _ => "没有 p99, 长帧只按丢帧比例看".to_string(),
    };
    let used = |p: Option<f64>| p.map(|p| r3(p / budget * 100.0));
    json!({
        "ok": fails.is_empty(),
        "cap_fps": cap,
        "budget_ms": r3(budget),
        "limit_ms": r3(limit),
        "margin": CAP_BUDGET_MARGIN,
        "knob_p95_ms_mean": knob_p95.map(r3),
        "knob_p95_ms_max": knob_p95_max.map(r3),
        "ctrl_p95_ms_mean": ctrl_p95.map(r3),
        "headroom_used_pct": used(knob_p95_max),
        "ctrl_headroom_used_pct": used(ctrl_p95),
        "knob_missed_frac": km.map(r3),
        "ctrl_missed_frac": cm.map(r3),
        "p99_note": p99_note,
        "fails": fails,
    })
}

// ══════════════ 3. 旋钮自报的施加程度 ══════════════

/// 缺省门槛: 候选臂里真正按设计处理过的帧低于这个比例 → 这一臂「未按设计施加」。
pub(crate) const MIN_APPLIED_FRAC: f64 = 0.95;

/// `payload.min_applied_frac` (0, 1], 缺省 [`MIN_APPLIED_FRAC`]。
pub(crate) fn min_applied_frac(req: &Value) -> f64 {
    req["payload"]["min_applied_frac"].as_f64().filter(|x| *x > 0.0 && *x <= 1.0).unwrap_or(MIN_APPLIED_FRAC)
}

/// 读层 marker (knobs_layer_out.json) 里旋钮自报的施加情况。
///
/// 认三种自报:
/// - `npuop` 段 (npu/n1-pipeline 的层): `counts.unpack` = 用上 NPU 结果的帧,
///   分母 = 自初始化那帧起的全部帧 (`readonly_stats.frames − npuop.init_at_frame`)。
///   `phase=wait_init` (一直没初始化) 记 0; `phase=disabled` (永久回退) 之后的帧不在 counts 里,
///   按分母算自然被扣掉。`reason` / `notes` 原样带上。
/// - `copyprobe` 段 `disabled: true`: 超分 / 拷贝算子中途停用, 之后的帧没处理, 比例不明 → 记 0。
/// - 通用约定: 任何顶层对象带 `applied_frames` + `total_frames` (可选 `fallback_reason` / `reason` / `notes`)。
///
/// 回 `{"knobs": [...], "applied_frac_min": f | null, "ok": bool, "reasons": [...]}`。
/// 没有任何自报 → `applied_frac_min = null`, `ok = true` (没有证据说它回退了; require_hit 另管「一次都没命中」)。
pub(crate) fn knob_application(marker: &Value, min_frac: f64) -> Value {
    let mut rows: Vec<Value> = Vec::new();
    let frames = marker["readonly_stats"]["frames"].as_f64();
    if let Some(np) = marker.get("npuop").filter(|v| v.is_object()) {
        let phase = np["phase"].as_str().unwrap_or("");
        let processed = np["counts"]["unpack"].as_f64().unwrap_or(0.0);
        let init_at = np["init_at_frame"].as_f64().unwrap_or(0.0);
        let (frac, total) = match (phase, frames) {
            ("wait_init", f) => (Some(0.0), f),
            (_, Some(f)) => {
                let total = (f - init_at).max(0.0);
                (if total > 0.0 { Some((processed / total).min(1.0)) } else { Some(0.0) }, Some(total))
            }
            (_, None) => {
                // 没有总帧数: 退到 NPU 帧 / (NPU 帧 + 双线性帧); 永久回退之后的帧数不明, 回退过就记 0
                let bil = np["counts"]["bilinear"].as_f64().unwrap_or(0.0);
                let t = processed + bil;
                let f = if phase == "disabled" { Some(0.0) } else if t > 0.0 { Some(processed / t) } else { None };
                (f, Some(t))
            }
        };
        let notes = np["notes"].clone();
        let mut reason = np["reason"].as_str().map(String::from);
        if phase == "wait_init" && reason.is_none() {
            reason = Some("NPU 一直没初始化 (phase=wait_init)".into());
        }
        rows.push(json!({
            "knob": "npuop", "phase": phase, "processed_frames": processed, "total_frames": total,
            "applied_frac": frac.map(r3), "fallback_reason": reason, "notes": notes,
            "counts": np["counts"],
        }));
    }
    if let Some(cp) = marker.get("copyprobe").filter(|v| v.is_object()) {
        if cp["disabled"].as_bool() == Some(true) {
            rows.push(json!({
                "knob": format!("copyprobe:{}", cp["mode"].as_str().unwrap_or("?")),
                "applied_frac": 0.0, "fallback_reason": "算子中途停用 (copyprobe.disabled=true), 之后的帧没处理, 比例不明按 0 记",
                "copies": cp["copies"],
            }));
        }
    }
    if let Some(obj) = marker.as_object() {
        for (k, v) in obj {
            let (Some(a), Some(t)) = (v["applied_frames"].as_f64(), v["total_frames"].as_f64()) else { continue };
            let frac = if t > 0.0 { Some((a / t).min(1.0)) } else { None };
            rows.push(json!({
                "knob": k, "processed_frames": a, "total_frames": t, "applied_frac": frac.map(r3),
                "fallback_reason": v.get("fallback_reason").or_else(|| v.get("reason")).cloned().unwrap_or(Value::Null),
                "notes": v.get("notes").cloned().unwrap_or(Value::Null),
            }));
        }
    }
    let fracs: Vec<f64> = rows.iter().filter_map(|r| r["applied_frac"].as_f64()).collect();
    let min = fracs.iter().cloned().reduce(f64::min);
    let mut reasons: Vec<String> = Vec::new();
    for r in &rows {
        match r["applied_frac"].as_f64() {
            Some(f) if f < min_frac => reasons.push(format!(
                "{}: 只有 {:.1}% 的帧按设计处理 (门槛 {:.0}%){}",
                r["knob"].as_str().unwrap_or("?"),
                f * 100.0,
                min_frac * 100.0,
                r["fallback_reason"].as_str().map(|s| format!(", 回退原因: {s}")).unwrap_or_default()
            )),
            None => reasons.push(format!("{}: 自报了施加情况但算不出比例", r["knob"].as_str().unwrap_or("?"))),
            _ => {}
        }
    }
    json!({"knobs": rows, "applied_frac_min": min.map(r3), "min_applied_frac": min_frac, "ok": reasons.is_empty(), "reasons": reasons})
}

/// 第 `round` 轮两臂都量完后: 候选臂「未按设计施加」就把这一对拿出统计, 记进 `unapplied`。
/// 候选臂的 `knob_application` 由调用方先写进 run。回 true = 剔除了。
pub(crate) fn settle_application(round: u32, knob_runs: &mut Vec<Value>, ctrl_runs: &mut Vec<Value>, unapplied: &mut Vec<Value>) -> bool {
    let is_round = |r: &Value| r["round"].as_u64() == Some(round as u64);
    let (Some(ki), Some(ci)) = (knob_runs.iter().position(is_round), ctrl_runs.iter().position(is_round)) else {
        return false;
    };
    if knob_runs[ki]["knob_application"]["ok"].as_bool() != Some(false) {
        return false;
    }
    let (k, c) = (knob_runs.remove(ki), ctrl_runs.remove(ci));
    unapplied.push(json!({
        "round": round,
        "reason": format!("未按设计施加: {}", k["knob_application"]["reasons"].as_array().map(|a| a.iter().filter_map(|x| x.as_str()).collect::<Vec<_>>().join("; ")).unwrap_or_default()),
        "applied_frac": k["knob_application"]["applied_frac_min"],
        "knob": {"label": k["label"], "layer_hits": k["layer_hits"], "knob_application": k["knob_application"]},
        "ctrl": {"label": c["label"]},
    }));
    true
}

/// `conditions_observed.knob_application`。
pub(crate) fn application_note(unapplied: &[Value], knob_runs: &[Value], min_frac: f64) -> Value {
    let fracs: Vec<f64> = knob_runs.iter().filter_map(|r| r["knob_application"]["applied_frac_min"].as_f64()).collect();
    json!({
        "min_applied_frac": min_frac,
        "dropped_pairs": unapplied.len(),
        "dropped": unapplied,
        "kept_applied_frac_min": fracs.iter().cloned().reduce(f64::min).map(r3),
        "rule": format!("候选臂旋钮自报回退或部分施加 (按设计处理的帧 < {:.0}%) → 这一臂记「未按设计施加」, 连同同一轮对照臂拿出统计, 不判", min_frac * 100.0),
    })
}

// ══════════════ 4. 记分卡 ══════════════

fn status_word(s: Option<&str>) -> &'static str {
    match s {
        Some(x) if x.contains("显著改善") => "better",
        Some(x) if x.contains("显著变差") => "worse",
        Some(x) if x.contains("缺数据") || x.contains("未测") => "not_measured",
        Some(_) => "same",
        None => "not_measured",
    }
}

/// ABORT 理由里哪些属于安全 (结温上限、崩溃 / 进程异常退出)。
pub(crate) fn is_safety_reason(r: &str) -> bool {
    ["结温", "超过上限", "崩溃", "crash", "clean_exit=false", "进程没了", "闪退"].iter().any(|k| r.contains(k))
}

/// gate 策略下画面超出参考线的 ABORT 理由 (不是「没测成」, 是按 gate 停下的)。旧回包的两种前缀也认。
pub(crate) const IMAGE_GATE_PREFIX: &str = "画面超出参考线";
pub(crate) fn is_image_gate_reason(r: &str) -> bool {
    r.starts_with(IMAGE_GATE_PREFIX) || r.starts_with("画面出错") || r.starts_with("画质判据不通过")
}

fn metric_row(dimension: &str, metric: &str, pm: &Value, unavailable_reason: Option<String>) -> Value {
    if pm.is_null() {
        return json!({"dimension": dimension, "metric": metric, "value": null, "unit": "%", "ci95": null,
            "status": "not_measured", "reason": unavailable_reason.unwrap_or_else(|| "判定表里没有这一项".into())});
    }
    let st = pm["status"].as_str();
    let mut status = status_word(st);
    let mut reason = format!("{} (p={}, 候选 {} / 对照 {})", st.unwrap_or("?"), pm["p"], pm["b_mean"], pm["a_mean"]);
    if let Some(c) = pm.get("capped").filter(|c| c.is_object()) {
        let used = c["headroom_used_pct"].as_f64().map(|x| format!("{x:.1}%")).unwrap_or("?".into());
        if pm["role"].as_str() == Some("capped_budget") {
            status = "same";
            reason = format!(
                "锁 {} fps: p95 变长但仍在帧预算内 (帧预算 {} ms 用掉 {used}, fps 守住上限, 丢帧不增加), 不算退化; 原统计: {reason}",
                c["cap_fps"], c["budget_ms"]
            );
        } else {
            reason = format!("{reason}; 锁 {} fps, 帧预算 {} ms 用掉 {used}", c["cap_fps"], c["budget_ms"]);
        }
    }
    json!({"dimension": dimension, "metric": metric, "value": pm["diff_pct"], "unit": "%", "ci95": pm["ci95"],
        "abs": {"candidate": pm["b_mean"], "control": pm["a_mean"], "diff": pm["diff_mean"]},
        "status": status, "reason": reason})
}

/// 从一份回包 (sysparam / gray / whitebox) 生成 `verdict.scorecard`。纯函数, 只读回包。
///
/// - `overall = "invalid"`: 只有安全 (结温上限 / 崩溃) 或「没测成」(ABORT 的其余理由)。
/// - `overall = "gated"`: image_policy=gate 下画面没过门槛, 按 gate 停下 (性能没量)。
/// - 其余 `overall = "valid"`: 任何单项变差都只记在那一行, 不让整个结果无效。
pub(crate) fn build(report: &Value, policy: ImagePolicy) -> Value {
    let status = report["status"].as_str().unwrap_or("ABORT");
    let reason = report["verdict"]["reason"].as_str().unwrap_or("").to_string();
    let (overall, invalid_kind) = if status == "ABORT" {
        if is_safety_reason(&reason) {
            ("invalid", Some("safety"))
        } else if is_image_gate_reason(&reason) {
            ("gated", None)
        } else {
            ("invalid", Some("not_measured"))
        }
    } else {
        ("valid", None)
    };
    let pm = &report["verdict"]["per_metric"];
    let unavail = |field: &str| -> Option<String> {
        report["metrics"]["unavailable"].as_array()?.iter().find(|u| u["field"] == field).and_then(|u| u["reason"].as_str()).map(String::from)
    };
    let not_run = |dim: &str, metric: &str| {
        json!({"dimension": dim, "metric": metric, "value": null, "unit": "%", "ci95": null, "status": "not_measured",
            "reason": format!("没有做统计判定 ({reason})")})
    };
    let judged = pm.is_object();
    let frame = if judged { metric_row("frame_time", "frame_p95", &pm["frame_p95"], None) } else { not_run("frame_time", "frame_p95") };
    let fps = if judged { metric_row("frame_rate", "fps_mean", &pm["fps_mean"], None) } else { not_run("frame_rate", "fps_mean") };
    let power = if judged {
        metric_row("power", "power_w_mean", &pm["power_w_mean"], unavail("power_watt"))
    } else {
        not_run("power", "power_w_mean")
    };

    // 画质: 只列数 (指标 + 参考线 + 离参考线多远), 不给 better/worse
    let gap = &report["verdict"]["quality_gap"];
    let image = if gap.is_object() {
        let has_rows = gap["metrics"].as_array().is_some_and(|a| !a.is_empty()) || gap["beyond_ref_line"].as_array().is_some_and(|a| !a.is_empty());
        let why = gap["summary"].as_str().map(String::from).unwrap_or_else(|| {
            // 旧回包 (state/reason 形状)
            gap["reason"].as_str().unwrap_or("").to_string()
        });
        json!({"dimension": "image_quality_gap", "metric": gap["source"], "value": gap["metrics"], "unit": null,
            "ci95": null, "status": if has_rows { "listed" } else { "not_measured" },
            "beyond_ref_line": gap["beyond_ref_line"], "sheet": gap["sheet"], "reason": why})
    } else if overall == "gated" {
        json!({"dimension": "image_quality_gap", "metric": "image_gate", "value": null, "unit": null, "ci95": null,
            "status": "listed", "reason": format!("image_policy=gate (调用方选的省时规则): {reason}")})
    } else if report["verdict"]["measure_only"].as_bool() == Some(true) {
        json!({"dimension": "image_quality_gap", "metric": "measure_only", "value": null, "unit": null, "ci95": null,
            "status": "not_measured", "reason": "只量上限的旋钮: 画面必然变化, 不按画面看"})
    } else {
        json!({"dimension": "image_quality_gap", "metric": null, "value": null, "unit": null, "ci95": null,
            "status": "not_measured", "reason": "本次没有画面比对 (这类改动不比画面, 或没走到那一步)"})
    };

    // 测量有效性
    let co = &report["conditions_observed"];
    let env_dropped = co["env_consistency"]["dropped_pairs"].as_u64().unwrap_or(0);
    let unapplied = co["knob_application"]["dropped_pairs"].as_u64().unwrap_or(0);
    let power_dropped = report["verdict"]["power_pairs"]["dropped"].as_array().map_or(0, |a| a.len()) as u64;
    let validity_value = json!({
        "env_dropped_pairs": env_dropped,
        "unapplied_dropped_pairs": unapplied,
        "power_dropped_pairs": power_dropped,
        "applied_frac_min": co["knob_application"]["kept_applied_frac_min"],
    });
    let validity = match overall {
        "invalid" => json!({"dimension": "measurement_validity", "metric": null, "value": validity_value, "unit": null, "ci95": null,
            "status": "not_measured", "reason": format!("{}: {reason}", if invalid_kind == Some("safety") { "安全" } else { "没测成" })}),
        _ if env_dropped + unapplied + power_dropped > 0 => json!({"dimension": "measurement_validity", "metric": null, "value": validity_value,
            "unit": null, "ci95": null, "status": "worse",
            "reason": format!("剔除了一部分对 (环境不一致 {env_dropped} 对, 未按设计施加 {unapplied} 对, 功耗不可信 {power_dropped} 对), 其余照判")}),
        "gated" => json!({"dimension": "measurement_validity", "metric": null, "value": validity_value, "unit": null, "ci95": null,
            "status": "not_measured", "reason": "按 image_policy=gate 停在画质参考线, 性能没量"}),
        _ => json!({"dimension": "measurement_validity", "metric": null, "value": validity_value, "unit": null, "ci95": null,
            "status": "same", "reason": "各臂按设计测成"}),
    };
    json!({
        "overall": overall,
        "invalid_kind": invalid_kind,
        "invalid_reason": (overall == "invalid").then_some(reason.clone()),
        "status": status,
        "image_policy": policy.as_str(),
        "rows": [frame, power, image, validity, fps],
        "rule": "只有安全 (结温上限、崩溃) 和「没测成」让整个结果无效; 其它任何单项变差只记在那一行。\
                 status: better / same / worse / not_measured; 画质那一行恒为 listed (只列指标、参考线、对照表, 不判通过, 由人选)",
    })
}

/// 给一份回包 JSON 补上 `verdict.scorecard` (已有就不动)。解析不了原样返回。
pub(crate) fn attach(report_json: &str, policy: ImagePolicy) -> String {
    let Ok(mut r) = serde_json::from_str::<Value>(report_json) else {
        return report_json.to_string();
    };
    if !r["verdict"].is_object() {
        r["verdict"] = json!({});
    }
    if r["verdict"].get("scorecard").is_none() {
        r["verdict"]["scorecard"] = build(&r, policy);
    }
    serde_json::to_string_pretty(&r).unwrap_or_else(|_| report_json.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── 1. 画质策略 ──

    #[test]
    fn image_policy_parses_and_defaults_to_gate() {
        assert_eq!(ImagePolicy::from_request(&json!({"protocol": {}})).unwrap(), ImagePolicy::Gate);
        assert_eq!(ImagePolicy::from_request(&json!({"protocol": {"image_policy": "report"}})).unwrap(), ImagePolicy::Report);
        assert_eq!(ImagePolicy::from_request(&json!({"protocol": {"image_policy": null}})).unwrap(), ImagePolicy::Gate);
        assert!(ImagePolicy::from_request(&json!({"protocol": {"image_policy": "off"}})).is_err());
    }

    fn rep_keep() -> Value {
        json!({"status": "ABORT", "verdict": {"is_pareto_improvement": true, "reason": "改善 power_w_mean 且无显著退化"}})
    }

    #[test]
    fn report_policy_with_open_gap_is_never_plain_pass() {
        let mut r = rep_keep();
        let st = GapState::Open("细节能量比 log2 = -0.6 (变糊/丢细节)".into());
        finalize_status(&mut r, true, ImagePolicy::Report, &st, quality_gap_json(ImagePolicy::Report, &st, "iq_judge", vec![], json!({})));
        assert_eq!(r["status"], KEEP_WITH_QUALITY_GAP);
        assert_eq!(r["verdict"]["is_pareto_improvement"], false, "不是 PASS 就不许让上层当成可采纳");
        assert_eq!(r["verdict"]["would_keep"], true);
        assert!(r["verdict"]["quality_gap"].get("state").is_none(), "画质不输出通过与否");
        assert_eq!(r["verdict"]["quality_gap"]["beyond_ref_line"], json!(["细节能量比 log2 = -0.6 (变糊/丢细节)"]));
        assert!(r["verdict"]["reason"].as_str().unwrap().contains("参考线外"));
        // 判不了也不是 PASS
        let mut r = rep_keep();
        let st = GapState::Unverified("稳定块只有 3 块".into());
        finalize_status(&mut r, true, ImagePolicy::Report, &st, quality_gap_json(ImagePolicy::Report, &st, "iq_judge", vec![], json!({})));
        assert_eq!(r["status"], KEEP_WITH_QUALITY_GAP);
        assert_eq!(r["verdict"]["quality_gap"]["not_computed"], json!(["稳定块只有 3 块"]));
        // 差距关闭 → PASS
        let mut r = rep_keep();
        finalize_status(&mut r, true, ImagePolicy::Report, &GapState::Closed, json!({}));
        assert_eq!(r["status"], "PASS");
        assert_eq!(r["verdict"]["is_pareto_improvement"], true);
        // 性能没过 → REJECT, 差距照记
        let mut r = rep_keep();
        let st = GapState::Open("x".into());
        finalize_status(&mut r, false, ImagePolicy::Report, &st, quality_gap_json(ImagePolicy::Report, &st, "iq_judge", vec![], json!({})));
        assert_eq!(r["status"], "REJECT");
        assert!(r["verdict"]["quality_gap"]["summary"].as_str().unwrap().contains("不判通过"));
    }

    #[test]
    fn gate_policy_keeps_old_behaviour_for_unverified_image() {
        let mut r = rep_keep();
        let st = GapState::Unverified("画质判据没跑成".into());
        finalize_status(&mut r, true, ImagePolicy::Gate, &st, quality_gap_json(ImagePolicy::Gate, &st, "iq_judge", vec![], json!({})));
        assert_eq!(r["status"], "REJECT");
        assert_eq!(r["verdict"]["image_unverified"], "画质判据没跑成");
        assert_eq!(r["verdict"]["would_keep"], true);
    }

    // ── 2. 锁帧帧预算 ──

    fn run(p95: f64, fps: f64, p99: Option<f64>) -> Value {
        json!({"summary": {"frame_p95": p95, "fps_mean": fps, "frame_p50": 1000.0 / 30.0, "frame_p99": p99}})
    }

    #[test]
    fn capped_longer_p95_within_budget_is_ok_and_reports_headroom() {
        // 锁 30: 帧预算 33.33 ms。对照 p95 30.0, 候选 33.9 (< 33.33×1.05=35.0), fps 守住 30
        let ctrl = vec![run(30.0, 29.95, Some(33.5)); 4];
        let knob = vec![run(33.9, 29.93, Some(34.6)); 4];
        let c = capped_budget_check(30.0, &knob, &ctrl);
        assert_eq!(c["ok"], true, "{c}");
        assert_eq!(c["budget_ms"], 33.333);
        assert!((c["headroom_used_pct"].as_f64().unwrap() - 101.7).abs() < 0.1, "{c}");
        assert!((c["ctrl_headroom_used_pct"].as_f64().unwrap() - 90.0).abs() < 0.1);
    }

    #[test]
    fn capped_real_frame_loss_still_counts() {
        let ctrl = vec![run(30.0, 29.95, Some(33.5)); 4];
        // p95 超出预算
        assert_eq!(capped_budget_check(30.0, &[run(36.0, 29.9, None)], &ctrl)["ok"], false);
        // p95 在预算内但 fps 掉了 (真丢帧)
        let c = capped_budget_check(30.0, &[run(34.0, 28.8, None)], &ctrl);
        assert_eq!(c["ok"], false);
        assert!(c["fails"].to_string().contains("没守住上限"));
        // fps 还在容差内, 但丢帧比例比对照多 1 个百分点
        let c = capped_budget_check(30.0, &[run(34.0, 29.6, None)], &ctrl);
        assert_eq!(c["ok"], false, "{c}");
        assert!(c["fails"].to_string().contains("丢帧比例"));
        // 长帧变多
        let c = capped_budget_check(30.0, &[run(34.0, 29.95, Some(50.0))], &ctrl);
        assert_eq!(c["ok"], false);
        assert!(c["fails"].to_string().contains("长帧"));
    }

    // ── 3. 施加程度 ──

    fn npu_marker(phase: &str, unpack: u64, bilinear: u64, frames: u64, init_at: u64) -> Value {
        json!({"knob": "gray_readonly_probe", "readonly_stats": {"frames": frames},
            "npuop": {"phase": phase, "reason": if phase == "disabled" { json!("连续 30 帧超预算") } else { Value::Null },
                "init_at_frame": init_at, "notes": ["单帧栅栏超时"],
                "counts": {"pack": unpack, "unpack": unpack, "bilinear": bilinear, "npu_ok": unpack}}})
    }

    #[test]
    fn npu_fallback_marks_arm_invalid_with_fraction() {
        // 初始化在第 300 帧, 之后 3700 帧里 3650 帧用上 NPU: 98.6% → 有效
        let ok = knob_application(&npu_marker("ready", 3650, 50, 4000, 300), 0.95);
        assert_eq!(ok["ok"], true, "{ok}");
        assert!((ok["applied_frac_min"].as_f64().unwrap() - 0.986).abs() < 0.001);
        // 中途永久回退: 之后的帧不在 counts 里, 按总帧数分母照样扣掉 → 2000/3700 = 54%
        let bad = knob_application(&npu_marker("disabled", 2000, 10, 4000, 300), 0.95);
        assert_eq!(bad["ok"], false);
        assert!((bad["applied_frac_min"].as_f64().unwrap() - 0.541).abs() < 0.001);
        let why = bad["reasons"][0].as_str().unwrap();
        assert!(why.contains("54.1%") && why.contains("连续 30 帧超预算"), "{why}");
        assert_eq!(bad["knobs"][0]["notes"][0], "单帧栅栏超时");
        // 一直没初始化
        let never = knob_application(&npu_marker("wait_init", 0, 0, 4000, 0), 0.95);
        assert_eq!(never["ok"], false);
        assert_eq!(never["applied_frac_min"], 0.0);
        // 门槛可调
        assert_eq!(knob_application(&npu_marker("disabled", 2000, 10, 4000, 300), 0.5)["ok"], true);
    }

    #[test]
    fn generic_and_copyprobe_self_reports_are_read() {
        let m = json!({"readonly_stats": {"frames": 1000},
            "interp": {"applied_frames": 900, "total_frames": 1000, "fallback_reason": "光流超时"},
            "copyprobe": {"mode": "upop", "disabled": false}});
        let a = knob_application(&m, 0.95);
        assert_eq!(a["ok"], false);
        assert!(a["reasons"][0].as_str().unwrap().contains("interp") && a["reasons"][0].as_str().unwrap().contains("光流超时"));
        let cp = knob_application(&json!({"copyprobe": {"mode": "upop", "disabled": true}}), 0.95);
        assert_eq!(cp["ok"], false);
        // 没有任何自报: 不判回退
        let none = knob_application(&json!({"effective": [], "readonly_stats": {"frames": 10}}), 0.95);
        assert_eq!(none["ok"], true);
        assert!(none["applied_frac_min"].is_null());
    }

    #[test]
    fn unapplied_arm_is_dropped_with_its_pair() {
        let k = |r: u64, ok: bool| json!({"label": format!("k{r}"), "round": r, "knob_application": {"ok": ok, "applied_frac_min": if ok { 0.99 } else { 0.4 }, "reasons": ["npuop: 只有 40.0% 的帧"]}});
        let c = |r: u64| json!({"label": format!("c{r}"), "round": r});
        let mut knob = vec![k(1, true), k(2, false)];
        let mut ctrl = vec![c(1), c(2)];
        let mut un = Vec::new();
        assert!(!settle_application(1, &mut knob, &mut ctrl, &mut un));
        assert!(settle_application(2, &mut knob, &mut ctrl, &mut un));
        assert_eq!((knob.len(), ctrl.len()), (1, 1));
        assert_eq!(un[0]["round"], 2);
        assert_eq!(un[0]["applied_frac"], 0.4);
        assert!(un[0]["reason"].as_str().unwrap().starts_with("未按设计施加"));
        let note = application_note(&un, &knob, 0.95);
        assert_eq!(note["dropped_pairs"], 1);
        assert_eq!(note["kept_applied_frac_min"], 0.99);
    }

    // ── 4. 记分卡 ──

    fn judged(status: &str, power: &str, frame: Value) -> Value {
        json!({
            "status": status, "metrics": {"unavailable": []},
            "verdict": {"reason": "x", "per_metric": {
                "frame_p95": frame,
                "fps_mean": {"status": "无显著变化", "diff_pct": 0.1, "p": 0.5, "ci95": [-0.1, 0.2], "a_mean": 30, "b_mean": 30},
                "power_w_mean": {"status": power, "diff_pct": -8.0, "p": 0.008, "ci95": [-0.4, -0.2], "a_mean": 4.0, "b_mean": 3.68},
            }},
            "conditions_observed": {"env_consistency": {"dropped_pairs": 0}},
        })
    }

    fn row<'a>(sc: &'a Value, dim: &str) -> &'a Value {
        sc["rows"].as_array().unwrap().iter().find(|r| r["dimension"] == dim).unwrap()
    }

    #[test]
    fn scorecard_has_one_row_per_dimension_and_gap_does_not_invalidate() {
        let mut r = judged(KEEP_WITH_QUALITY_GAP, "显著改善",
            json!({"status": "无显著变化", "diff_pct": 0.5, "p": 0.4, "ci95": [-0.1, 0.3], "a_mean": 33.0, "b_mean": 33.2}));
        let st = GapState::Open("稳定块 PSNR 38 < 41".into());
        let rows = vec![crate::iqsheet::row("stable_psnr_db", "稳定块 PSNR", "dB", crate::iqsheet::Better::Higher, Some(38.0), Some(42.0), Some(41.0), "r", "")];
        r["verdict"]["quality_gap"] = quality_gap_json(ImagePolicy::Report, &st, "iq_judge", rows, json!({}));
        let sc = build(&r, ImagePolicy::Report);
        assert_eq!(sc["overall"], "valid");
        assert_eq!(row(&sc, "power")["status"], "better");
        assert_eq!(row(&sc, "power")["ci95"], json!([-0.4, -0.2]));
        assert_eq!(row(&sc, "frame_time")["status"], "same");
        assert_eq!(row(&sc, "image_quality_gap")["status"], "listed", "画质只列数, 不判好坏");
        assert_eq!(row(&sc, "image_quality_gap")["value"][0]["to_ref_line"], json!(-3.0));
        assert!(row(&sc, "image_quality_gap")["sheet"].as_str().unwrap().contains("参考线外 3.00"));
        assert_eq!(row(&sc, "measurement_validity")["status"], "same");
        for d in ["frame_time", "power", "image_quality_gap", "measurement_validity"] {
            let x = row(&sc, d);
            for k in ["value", "ci95", "status", "reason"] {
                assert!(x.get(k).is_some(), "{d} 缺 {k}");
            }
        }
    }

    #[test]
    fn scorecard_capped_exemption_shows_headroom() {
        let r = judged("PASS", "显著改善", json!({"status": "锁帧内变长 (帧预算内, 不算退化)", "role": "capped_budget",
            "diff_pct": 4.0, "p": 0.01, "ci95": [0.5, 1.5], "a_mean": 32.0, "b_mean": 33.3,
            "capped": {"ok": true, "cap_fps": 30.0, "budget_ms": 33.333, "headroom_used_pct": 99.9}}));
        let sc = build(&r, ImagePolicy::Gate);
        let f = row(&sc, "frame_time");
        assert_eq!(f["status"], "same");
        assert!(f["reason"].as_str().unwrap().contains("99.9%"), "{f}");
    }

    #[test]
    fn scorecard_only_safety_and_unmeasured_invalidate() {
        let abort = |why: &str| json!({"status": "ABORT", "metrics": {"unavailable": []}, "verdict": {"reason": why}});
        let sc = build(&abort("knob 臂第 2 轮 SoC 结温 61C 超过上限 58C"), ImagePolicy::Gate);
        assert_eq!((sc["overall"].as_str(), sc["invalid_kind"].as_str()), (Some("invalid"), Some("safety")));
        let sc = build(&abort("require_hit=true 但改写一次都没命中"), ImagePolicy::Gate);
        assert_eq!((sc["overall"].as_str(), sc["invalid_kind"].as_str()), (Some("invalid"), Some("not_measured")));
        assert_eq!(row(&sc, "frame_time")["status"], "not_measured");
        // gate 下画面门槛停下: 不是「无效」, 画质一行记变差
        let sc = build(&abort("画面超出参考线 (全帧 PSNR): 22 dB < 噪声底"), ImagePolicy::Gate);
        assert_eq!(sc["overall"], "gated");
        assert_eq!(row(&sc, "image_quality_gap")["status"], "listed");
        // 旧回包的前缀也认
        assert_eq!(build(&abort("画面出错: PSNR 22 dB < 噪声底"), ImagePolicy::Gate)["overall"], "gated");
        // 帧时显著变差 + 功耗缺数据: 照样 valid, 只在各自那一行
        let mut r = judged("REJECT", "缺数据", json!({"status": "显著变差", "diff_pct": 9.0, "p": 0.01, "ci95": [1, 2], "a_mean": 16, "b_mean": 17.4}));
        r["verdict"]["per_metric"].as_object_mut().unwrap().remove("power_w_mean");
        r["metrics"]["unavailable"] = json!([{"field": "power_watt", "reason": "非放电态"}]);
        r["conditions_observed"]["knob_application"] = json!({"dropped_pairs": 1, "kept_applied_frac_min": 0.97});
        let sc = build(&r, ImagePolicy::Gate);
        assert_eq!(sc["overall"], "valid");
        assert_eq!(row(&sc, "frame_time")["status"], "worse");
        assert_eq!(row(&sc, "power")["status"], "not_measured");
        assert_eq!(row(&sc, "power")["reason"], "非放电态");
        assert_eq!(row(&sc, "measurement_validity")["status"], "worse");
    }

    #[test]
    fn attach_adds_scorecard_once() {
        let s = attach(r#"{"status":"ABORT","verdict":{"reason":"没拿到 summary"}}"#, ImagePolicy::Gate);
        let v: Value = serde_json::from_str(&s).unwrap();
        assert_eq!(v["verdict"]["scorecard"]["overall"], "invalid");
        assert_eq!(attach("not json", ImagePolicy::Gate), "not json");
    }
}
