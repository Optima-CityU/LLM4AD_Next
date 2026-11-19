//! 画质输出的统一形状: **只列指标、参考线和对照表, 不判「通过 / 不通过」** (用户 2026-10-21:
//! 「不要『通过』这个指标。都列出来, 让人选就行」)。
//!
//! iq-judge / interp-judge / eval 的 `verdict.quality_gap` / 记分卡画质那一行都用这里的行格式:
//!
//! ```json
//! {"key": "stable_psnr_db", "name": "稳定块 PSNR", "unit": "dB", "better": "higher",
//!  "value": 41.2, "native_self": 47.0, "ref_line": 46.0, "ref_rule": "min(原生自噪声, 50) − 1 dB",
//!  "to_ref_line": -4.8, "note": "..."}
//! ```
//!
//! - `native_self`: 原生自己跟自己比的数 (本次实测的噪声底); `ref_line`: 参考线 (原来的门槛, 现在只是参照)。
//! - `to_ref_line`: 离参考线多远, 带符号, **正 = 在原生一侧, 负 = 在参考线外**; 算不了为 null。
//! - `better`: `higher` / `lower` / `near_zero` (取绝对值比, 如细节能量 log2)。
//!
//! 是否接受由人看对照表 (`sheet`) 决定。程序里唯一还会用到参考线的地方是 eval 的
//! `protocol.image_policy = "gate"` (调用方自己选的「超出参考线就不量性能」的省时规则), 见 `scorecard`。

use serde_json::{json, Value};

/// 哪个方向更接近原生。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Better {
    Higher,
    Lower,
    /// 取绝对值, 越接近 0 越好 (参考线是绝对值上限)。
    NearZero,
}

impl Better {
    fn as_str(self) -> &'static str {
        match self {
            Better::Higher => "higher",
            Better::Lower => "lower",
            Better::NearZero => "near_zero",
        }
    }
    fn zh(self) -> &'static str {
        match self {
            Better::Higher => "越大越接近原生",
            Better::Lower => "越小越接近原生",
            Better::NearZero => "越接近 0 越接近原生",
        }
    }
}

fn r4(x: f64) -> f64 {
    (x * 10000.0).round() / 10000.0
}

/// 离参考线多远 (正 = 原生一侧)。
pub(crate) fn to_ref_line(better: Better, value: f64, ref_line: f64) -> f64 {
    match better {
        Better::Higher => value - ref_line,
        Better::Lower => ref_line - value,
        Better::NearZero => ref_line - value.abs(),
    }
}

/// 一行指标。`value` 为 None = 算不了 (`note` 写原因)。
#[allow(clippy::too_many_arguments)]
pub(crate) fn row(
    key: &str,
    name: &str,
    unit: &str,
    better: Better,
    value: Option<f64>,
    native_self: Option<f64>,
    ref_line: Option<f64>,
    ref_rule: &str,
    note: &str,
) -> Value {
    let d = match (value, ref_line) {
        (Some(v), Some(l)) if v.is_finite() && l.is_finite() => Some(r4(to_ref_line(better, v, l))),
        _ => None,
    };
    let fin = |x: Option<f64>| x.filter(|v| v.is_finite()).map(r4);
    json!({
        "key": key,
        "name": name,
        "unit": unit,
        "better": better.as_str(),
        "value": fin(value),
        "native_self": fin(native_self),
        "ref_line": fin(ref_line),
        "ref_rule": ref_rule,
        "to_ref_line": d,
        "note": if note.is_empty() { Value::Null } else { json!(note) },
    })
}

/// 在参考线外的指标名 (事实陈述, 不是判定)。
pub(crate) fn beyond_ref_line(rows: &[Value]) -> Vec<String> {
    rows.iter()
        .filter(|r| r["to_ref_line"].as_f64().is_some_and(|d| d < 0.0))
        .map(|r| r["name"].as_str().unwrap_or("?").to_string())
        .collect()
}

/// 算不了的指标名。
pub(crate) fn not_computed(rows: &[Value]) -> Vec<String> {
    rows.iter().filter(|r| r["value"].is_null()).map(|r| r["name"].as_str().unwrap_or("?").to_string()).collect()
}

fn fmt_num(v: &Value) -> String {
    match v.as_f64() {
        Some(x) if x.abs() >= 100.0 => format!("{x:.1}"),
        Some(x) if x.abs() >= 1.0 => format!("{x:.2}"),
        Some(x) => format!("{x:.4}"),
        None => "—".into(),
    }
}

fn fmt_cell(r: &Value) -> String {
    if r["value"].is_null() {
        return "算不了".into();
    }
    let v = fmt_num(&r["value"]);
    match r["to_ref_line"].as_f64() {
        Some(d) if d >= 0.0 => format!("{v} (原生一侧 {})", fmt_num(&json!(d))),
        Some(d) => format!("{v} (参考线外 {})", fmt_num(&json!(-d))),
        None => v,
    }
}

/// 对照表 (Markdown)。`columns` = [(列名, 这一列的指标行)], 各列按 `key` 对齐, 参考线取第一列里有的那一行。
/// 单元格: 数值 + 离参考线多远。不写「通过 / 不通过」。
pub(crate) fn sheet(columns: &[(String, Vec<Value>)]) -> String {
    let mut keys: Vec<(String, Value)> = Vec::new();
    for (_, rows) in columns {
        for r in rows {
            let k = r["key"].as_str().unwrap_or("").to_string();
            if !keys.iter().any(|(kk, _)| *kk == k) {
                keys.push((k, r.clone()));
            }
        }
    }
    let mut s = String::from("| 指标 | 单位 | 方向 | 原生自噪声 | 参考线 |");
    for (name, _) in columns {
        s.push_str(&format!(" {name} |"));
    }
    s.push_str("\n|---|---|---|---|---|");
    for _ in columns {
        s.push_str("---|");
    }
    s.push('\n');
    for (k, proto) in &keys {
        let better = match proto["better"].as_str() {
            Some("higher") => Better::Higher,
            Some("lower") => Better::Lower,
            _ => Better::NearZero,
        };
        let refline = if proto["ref_line"].is_null() {
            "—".to_string()
        } else {
            format!("{}{}", if better == Better::NearZero { "±" } else { "" }, fmt_num(&proto["ref_line"]))
        };
        s.push_str(&format!(
            "| {} | {} | {} | {} | {} |",
            proto["name"].as_str().unwrap_or(k),
            proto["unit"].as_str().unwrap_or(""),
            better.zh(),
            fmt_num(&proto["native_self"]),
            refline
        ));
        for (_, rows) in columns {
            let cell = rows.iter().find(|r| r["key"].as_str() == Some(k)).map(fmt_cell).unwrap_or_else(|| "—".into());
            s.push_str(&format!(" {cell} |"));
        }
        s.push('\n');
    }
    s.push_str("\n参考线 = 原来的门槛, 现在只作参照; 「原生一侧 / 参考线外」后面的数是离参考线多远。是否接受由人看表决定。\n");
    s
}

/// 一句话摘要 (只陈述, 不下结论)。
pub(crate) fn summary(rows: &[Value], extra_not_computed: &[String]) -> String {
    let mut parts: Vec<String> = Vec::new();
    for r in rows {
        if r["value"].is_null() {
            continue;
        }
        let name = r["name"].as_str().unwrap_or("?");
        let unit = r["unit"].as_str().unwrap_or("");
        let pos = match r["to_ref_line"].as_f64() {
            Some(d) if d >= 0.0 => format!("原生一侧 {}", fmt_num(&json!(d))),
            Some(d) => format!("参考线外 {}", fmt_num(&json!(-d))),
            None => "无参考线".into(),
        };
        parts.push(format!("{name} {}{unit} (参考线 {}, {pos})", fmt_num(&r["value"]), fmt_num(&r["ref_line"])));
    }
    let mut nc = not_computed(rows);
    nc.extend(extra_not_computed.iter().cloned());
    let mut s = if parts.is_empty() { "没有算出的画质指标".to_string() } else { parts.join("; ") };
    if !nc.is_empty() {
        s.push_str(&format!("。算不了: {}", nc.join("; ")));
    }
    s.push_str("。只列数, 不判通过, 由人对照参考线选。");
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rows_measure_distance_to_reference_line_by_direction() {
        let p = row("p", "PSNR", "dB", Better::Higher, Some(41.0), Some(47.0), Some(46.0), "r", "");
        assert_eq!(p["to_ref_line"], json!(-5.0));
        let d = row("d", "1-SSIM", "", Better::Lower, Some(0.001), Some(0.002), Some(0.0035), "r", "");
        assert!(d["to_ref_line"].as_f64().unwrap() > 0.0);
        let z = row("z", "细节", "", Better::NearZero, Some(-0.6), Some(0.1), Some(0.25), "r", "");
        assert!((z["to_ref_line"].as_f64().unwrap() + 0.35).abs() < 1e-9);
        let n = row("n", "空", "", Better::Higher, None, None, Some(1.0), "r", "稳定块太少");
        assert_eq!(n["to_ref_line"], Value::Null);
        assert_eq!(beyond_ref_line(&[p.clone(), d.clone(), z.clone(), n.clone()]), vec!["PSNR", "细节"]);
        assert_eq!(not_computed(&[p, d, z, n]), vec!["空"]);
    }

    #[test]
    fn sheet_and_summary_have_no_pass_fail_words() {
        let a = vec![row("p", "PSNR", "dB", Better::Higher, Some(41.0), Some(47.0), Some(46.0), "r", "")];
        let b = vec![row("p", "PSNR", "dB", Better::Higher, Some(48.0), Some(47.0), Some(46.0), "r", "")];
        let s = sheet(&[("候选".into(), a.clone()), ("基线".into(), b)]);
        assert!(s.contains("| PSNR | dB |") && s.contains("参考线外 5.00") && s.contains("原生一侧 2.00"), "{s}");
        let sm = summary(&a, &["对照只有 2 张".into()]);
        assert!(sm.contains("参考线外") && sm.contains("对照只有 2 张"), "{sm}");
        for txt in [&s, &sm] {
            assert!(!txt.contains("不通过") && !txt.to_lowercase().contains("pass") && !txt.contains("fail"), "{txt}");
        }
    }
}
