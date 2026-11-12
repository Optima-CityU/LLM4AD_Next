//! 假设—检验—证据闭环的存储层(SPEC_EVOLUTION §2.1/2.2)。
//! hypotheses.jsonl = append-only 事件溯源存储;当前状态由重放事件物化,不原地改写。
//! 证据纪律: support/against 只能由确定性代码在 pred outcome 链接时落账(E3/E4),
//! 模型调用(hypothesize/reflect)只能 propose / retract-建议,不得自己给自己记战功。
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::fs;

/// 假设生命周期状态(物化结果,不落盘)
#[derive(Clone, Debug, PartialEq)]
pub enum Status {
    /// 已提出,尚无 freq 证据
    Candidate,
    /// 有 freq 证据且未撤回
    Active,
    Retracted,
    Superseded,
}

#[derive(Clone, Debug)]
pub struct Hyp {
    pub id: String,
    pub q: String,
    pub t: String,
    pub conds: Vec<String>,
    pub status: Status,
    pub win: u32,
    pub lose: u32,
}

#[derive(Clone, Debug)]
pub struct PredOutcome {
    pub observed: String,
    /// pass=产出判别性证据(含区分出对错) / inconclusive=通道失效无结论 / fail=检验动作自身未能执行
    pub assert: String,
    pub revises: Vec<(String, String)>, // (hyp_id, "support"|"against")
}

#[derive(Clone, Debug)]
pub struct Pred {
    pub id: String,
    pub hyps: Vec<String>,
    pub expect: Vec<(String, String)>,
    pub test: Value,
    pub page_hint: String,
    pub outcome: Option<PredOutcome>,
}

/// 单任务(或 _global)的假设存储:路径 + 全量事件
pub struct Store {
    pub path: String,
    events: Vec<Value>,
}

/// 合法断言词表(封闭集合,SPEC §3.4)。返回 Some(词) 或 None
pub fn assert_word_ok(w: &str) -> bool {
    matches!(w, "diff_none" | "diff_not_none" | "probe_found" | "probe_not_found" | "rejected" | "not_rejected")
        || w.starts_with("probe_ans_contains:")
}

/// 检验的实测对象(确定性判定的输入)
pub enum Obs<'a> {
    /// 探针应答文本
    Probe(&'a str),
    /// 动作后的 diff 串
    Diff(&'a str),
}

/// 用断言词比对实测,产出三值: Some(true)=符合预测 / Some(false)=与预测相反 / None=该通道判不了(无结论)
pub fn eval_assert(word: &str, obs: &Obs) -> Option<bool> {
    match (word, obs) {
        ("diff_none", Obs::Diff(d)) => Some(*d == "none"),
        ("diff_not_none", Obs::Diff(d)) => {
            if d.starts_with("rejected") { None } else { Some(*d != "none") }
        }
        ("rejected", Obs::Diff(d)) => Some(d.starts_with("rejected")),
        ("not_rejected", Obs::Diff(d)) => Some(!d.starts_with("rejected")),
        ("probe_found", Obs::Probe(ans)) => {
            if ans.contains("命中") { Some(true) }
            else if ans.contains("未找到") || ans.contains("不在当前屏") { Some(false) }
            else { None } // 无数据(假树/空树) → 无结论
        }
        ("probe_not_found", Obs::Probe(ans)) => {
            if ans.contains("未找到") || ans.contains("不在当前屏") { Some(true) }
            else if ans.contains("命中") { Some(false) }
            else { None }
        }
        (w, Obs::Probe(ans)) if w.starts_with("probe_ans_contains:") => {
            if ans.contains("探针无数据") { None }
            else { Some(ans.contains(w.trim_start_matches("probe_ans_contains:"))) }
        }
        // 探针类断言用在 diff 上(或反之) → 判不了
        _ => None,
    }
}

impl Store {
    /// 读盘重放;文件不存在=空存储(零扰动)
    pub fn load(path: &str) -> Store {
        let events = fs::read_to_string(path)
            .map(|s| {
                s.lines()
                    .filter_map(|l| serde_json::from_str::<Value>(l).ok())
                    .filter(|v| v["r"] == "hyp" || v["r"] == "pred")
                    .collect()
            })
            .unwrap_or_default();
        Store { path: path.into(), events }
    }

    /// 追加一个事件(落盘+入内存)。文件不存在时先写 {"v":1} 头。
    pub fn append(&mut self, v: Value) {
        let line = serde_json::to_string(&v).unwrap_or_default();
        if line.is_empty() { return; }
        if !std::path::Path::new(&self.path).exists() {
            let _ = fs::write(&self.path, "{\"v\":1}\n");
        }
        if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(&self.path) {
            use std::io::Write;
            let _ = writeln!(f, "{line}");
        }
        self.events.push(v);
    }

    pub fn events(&self) -> &[Value] { &self.events }

    /// 生成该存储内的下一个空闲 id(前缀+数字,如 h7/p3)
    pub fn next_id(&self, prefix: &str) -> String {
        let mut maxn = 0u32;
        for e in &self.events {
            if let Some(id) = e["id"].as_str() {
                if let Some(n) = id.strip_prefix(prefix).and_then(|s| s.parse::<u32>().ok()) {
                    maxn = maxn.max(n);
                }
            }
        }
        format!("{prefix}{}", maxn + 1)
    }

    /// 物化全部假设(含 freq 计数,按 ev.rec 幂等去重)
    pub fn hyps(&self) -> Vec<Hyp> {
        let mut m: HashMap<String, Hyp> = HashMap::new();
        let mut order: Vec<String> = Vec::new();
        let mut seen_rec: HashSet<String> = HashSet::new();
        for e in &self.events {
            if e["r"] != "hyp" { continue; }
            let op = e["op"].as_str().unwrap_or("");
            let id = e["id"].as_str().unwrap_or("").to_string();
            if id.is_empty() { continue; }
            match op {
                "propose" => {
                    if !m.contains_key(&id) { order.push(id.clone()); }
                    let entry = m.entry(id.clone()).or_insert(Hyp {
                        id: id.clone(),
                        q: e["q"].as_str().unwrap_or("").into(),
                        t: String::new(),
                        conds: vec![],
                        status: Status::Candidate,
                        win: 0,
                        lose: 0,
                    });
                    entry.q = e["q"].as_str().unwrap_or(&entry.q).into();
                    entry.t = e["t"].as_str().unwrap_or("").into();
                    entry.conds = e["conds"].as_array()
                        .map(|a| a.iter().filter_map(|c| c.as_str().map(String::from)).collect())
                        .unwrap_or_default();
                    // 同 id 重新 propose(如 supersede 链的新版本): 状态复位
                    entry.status = Status::Candidate;
                }
                "support" | "against" => {
                    // 幂等键: pred rec + 假设 id + op 三元组。一条 pred 可同时修订多个解释(各计各的),
                    // 但同一(检验,假设,方向)的重复落账(如中断重试)只计一次。
                    let rec = e["ev"]["rec"].as_str().map(String::from)
                        .unwrap_or_else(|| serde_json::to_string(e).unwrap_or_default());
                    let key = format!("{rec}|{id}|{op}");
                    if seen_rec.insert(key) {
                        if let Some(h) = m.get_mut(&id) {
                            if op == "support" { h.win += 1; } else { h.lose += 1; }
                            if h.status == Status::Candidate { h.status = Status::Active; }
                        }
                    }
                }
                "retract" => {
                    if let Some(h) = m.get_mut(&id) { h.status = Status::Retracted; }
                }
                "supersede" => {
                    if let Some(h) = m.get_mut(&id) { h.status = Status::Superseded; }
                }
                "narrow" => {
                    if let Some(h) = m.get_mut(&id) {
                        h.conds = e["conds"].as_array()
                            .map(|a| a.iter().filter_map(|c| c.as_str().map(String::from)).collect())
                            .unwrap_or_default();
                    }
                }
                _ => {}
            }
        }
        order.into_iter().filter_map(|id| m.get(&id).cloned()).collect()
    }

    /// 物化全部预测(register + 可选 outcome)
    pub fn preds(&self) -> Vec<Pred> {
        let mut m: HashMap<String, Pred> = HashMap::new();
        let mut order: Vec<String> = Vec::new();
        for e in &self.events {
            if e["r"] != "pred" { continue; }
            let op = e["op"].as_str().unwrap_or("");
            let id = e["id"].as_str().unwrap_or("").to_string();
            if id.is_empty() { continue; }
            match op {
                "register" => {
                    if !m.contains_key(&id) { order.push(id.clone()); }
                    let expect = e["expect"].as_object()
                        .map(|o| o.iter().filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string()))).collect())
                        .unwrap_or_default();
                    m.insert(id.clone(), Pred {
                        id: id.clone(),
                        hyps: e["hyps"].as_array()
                            .map(|a| a.iter().filter_map(|h| h.as_str().map(String::from)).collect())
                            .unwrap_or_default(),
                        expect,
                        test: e["test"].clone(),
                        page_hint: e["page_hint"].as_str().unwrap_or("").into(),
                        outcome: None,
                    });
                }
                "outcome" => {
                    if let Some(p) = m.get_mut(&id) {
                        p.outcome = Some(PredOutcome {
                            observed: e["observed"].as_str().unwrap_or("").into(),
                            assert: e["assert"].as_str().unwrap_or("inconclusive").into(),
                            revises: e["revises"].as_array()
                                .map(|a| a.iter().filter_map(|r| {
                                    Some((r["id"].as_str()?.to_string(), r["op"].as_str()?.to_string()))
                                }).collect())
                                .unwrap_or_default(),
                        });
                    }
                }
                _ => {}
            }
        }
        order.into_iter().filter_map(|id| m.get(&id).cloned()).collect()
    }

    /// 已登记未出结果的检验(T3 信号)
    pub fn pending_preds(&self) -> Vec<Pred> {
        self.preds().into_iter().filter(|p| p.outcome.is_none()).collect()
    }

    /// 当前可注入的假设(未撤回未被替代),按 q 分组排序:有证据的在前
    pub fn injectable(&self) -> Vec<Hyp> {
        let mut v: Vec<Hyp> = self.hyps().into_iter()
            .filter(|h| h.status == Status::Candidate || h.status == Status::Active)
            .collect();
        v.sort_by(|a, b| {
            let sa = if a.status == Status::Active { 0 } else { 1 };
            let sb = if b.status == Status::Active { 0 } else { 1 };
            sa.cmp(&sb).then(a.q.cmp(&b.q)).then(a.id.cmp(&b.id))
        });
        v
    }

    /// 决策上下文注入段(SPEC §3.5): 适用条件粗匹配 + 上限 + 证据来源标注。
    /// activity/page 任一出现在 conds 中(或 conds 为空)即视为适用。
    pub fn inject_line(&self, activity: &str, page_name: &str, max: usize) -> String {
        let ctx = format!("{activity}|{page_name}").to_lowercase();
        let mut s = String::new();
        let mut n = 0usize;
        for h in self.injectable() {
            if n >= max { break; }
            let applicable = h.conds.is_empty()
                || h.conds.iter().any(|c| !c.is_empty() && ctx.contains(&c.to_lowercase()));
            if !applicable { continue; }
            let conf = if h.win + h.lose > 0 {
                format!("证据win{}/lose{}", h.win, h.lose)
            } else {
                "待证".to_string()
            };
            let cond = if h.conds.is_empty() { String::new() } else { format!(" 适用[{}]", h.conds.join(",")) };
            s.push_str(&format!("hyp#{}({})[{}]{cond}: {}\n", h.id, h.q, conf, h.t));
            n += 1;
        }
        s
    }

    /// hypothesize 调用的输入材料:当前竞争组物化视图的紧凑文本
    pub fn view_text(&self) -> String {
        let mut s = String::new();
        for h in self.hyps() {
            let st = match h.status {
                Status::Candidate => "候选",
                Status::Active => "活跃",
                Status::Retracted => "已撤回",
                Status::Superseded => "已被替代",
            };
            s.push_str(&format!("{}[{}|{}] win{}/lose{} 适用[{}]: {}\n",
                h.id, h.q, st, h.win, h.lose, h.conds.join(","), h.t));
        }
        for p in self.pending_preds() {
            s.push_str(&format!("{} 待执行检验: {} (期望: {})\n",
                p.id,
                serde_json::to_string(&p.test).unwrap_or_default(),
                p.expect.iter().map(|(k, w)| format!("{k}→{w}")).collect::<Vec<_>>().join(",")));
        }
        s
    }
}

/// hypothesize 模型输出的解析与校验(纯函数,单测钉契约)。
/// 返回 (q, hyps[(模型局部id, 文本, conds)], 可选 test 定义{kind,a,q/text,expect[(局部id,断言词)]})。
/// 任何字段不合法 → None(调用方丢弃落账,不进证据链)。
pub fn parse_hypothesize(text: &str) -> Option<(String, Vec<(String, String, Vec<String>)>, Option<Value>)> {
    let start = text.find('{')?;
    let end = text.rfind('}')?;
    let v: Value = serde_json::from_str(&text[start..=end]).ok()?;
    let q = v["q"].as_str()?.trim().to_string();
    if q.is_empty() { return None; }
    let hyps_v = v["hyps"].as_array()?;
    if hyps_v.is_empty() || hyps_v.len() > 4 { return None; }
    let mut hyps = Vec::new();
    let mut local_ids = HashSet::new();
    for (i, hv) in hyps_v.iter().enumerate() {
        let lid = hv["id"].as_str().map(String::from).unwrap_or_else(|| format!("x{i}"));
        let t = hv["t"].as_str()?.trim().to_string();
        if t.is_empty() || t.chars().count() > 120 { return None; }
        let conds = hv["conds"].as_array()
            .map(|a| a.iter().filter_map(|c| c.as_str().map(String::from)).collect())
            .unwrap_or_default();
        if !local_ids.insert(lid.clone()) { return None; }
        hyps.push((lid, t, conds));
    }
    let test = if v["test"].is_object() {
        let tv = &v["test"];
        let kind = tv["kind"].as_str()?;
        let (a, qstr) = match kind {
            "probe" => {
                let a = tv["a"].as_str()?;
                if !matches!(a, "find" | "inspect" | "get_state" | "history") { return None; }
                (a.to_string(), tv["q"].as_str().unwrap_or("").to_string())
            }
            "act" => {
                let a = tv["a"].as_str()?;
                if !matches!(a, "wait" | "scroll_up" | "scroll_down") { return None; }
                (a.to_string(), String::new())
            }
            _ => return None,
        };
        let expect_o = tv["expect"].as_object()?;
        if expect_o.is_empty() { return None; }
        let mut expect = Vec::new();
        for (k, wv) in expect_o {
            let w = wv.as_str()?;
            if !assert_word_ok(w) { return None; }
            if !local_ids.contains(k) { return None; } // 期望必须落在已声明的解释上
            expect.push((k.clone(), w.to_string()));
        }
        // 判别性: 至少两个解释的预测不同(全同预测区分不了任何解释)
        let words: HashSet<&String> = expect.iter().map(|(_, w)| w).collect();
        if words.len() < 2 && expect.len() > 1 { return None; }
        Some(json!({"kind": kind, "a": a, "q": qstr, "expect": expect}))
    } else {
        None
    };
    Some((q, hyps, test))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_path(tag: &str) -> String {
        let p = std::env::temp_dir().join(format!("pf-hypo-test-{}-{}.jsonl", tag, std::process::id()));
        let _ = fs::remove_file(&p);
        p.to_string_lossy().to_string()
    }

    #[test]
    fn store_propose_materialize() {
        let p = tmp_path("propose");
        let mut s = Store::load(&p);
        assert!(s.hyps().is_empty());
        s.append(json!({"r":"hyp","op":"propose","id":"h1","q":"q1","t":"定位错误","conds":["设置页"]}));
        s.append(json!({"r":"hyp","op":"propose","id":"h2","q":"q1","t":"通道遗漏"}));
        let h = s.hyps();
        assert_eq!(h.len(), 2);
        assert_eq!(h[0].status, Status::Candidate);
        assert_eq!(h[0].conds, vec!["设置页"]);
        // 落盘可重载
        let s2 = Store::load(&p);
        assert_eq!(s2.hyps().len(), 2);
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn support_against_idempotent_and_active() {
        let p = tmp_path("freq");
        let mut s = Store::load(&p);
        s.append(json!({"r":"hyp","op":"propose","id":"h1","q":"q1","t":"x"}));
        s.append(json!({"r":"hyp","op":"support","id":"h1","ev":{"rec":"p1"}}));
        s.append(json!({"r":"hyp","op":"support","id":"h1","ev":{"rec":"p1"}})); // 重复 rec 不重复计
        s.append(json!({"r":"hyp","op":"against","id":"h1","ev":{"rec":"p2"}}));
        let h = &s.hyps()[0];
        assert_eq!((h.win, h.lose), (1, 1));
        assert_eq!(h.status, Status::Active);
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn one_pred_revises_multiple_hyps() {
        // 回归: 同一检验区分多个解释时,每个解释的 support/against 都必须计入(幂等键三元组)
        let p = tmp_path("multi");
        let mut s = Store::load(&p);
        s.append(json!({"r":"hyp","op":"propose","id":"h1","q":"q","t":"x"}));
        s.append(json!({"r":"hyp","op":"propose","id":"h2","q":"q","t":"y"}));
        s.append(json!({"r":"hyp","op":"support","id":"h1","ev":{"rec":"p1"}}));
        s.append(json!({"r":"hyp","op":"against","id":"h2","ev":{"rec":"p1"}}));
        // 中断重试重复落账同方向事件 → 不重复计
        s.append(json!({"r":"hyp","op":"against","id":"h2","ev":{"rec":"p1"}}));
        let h = s.hyps();
        assert_eq!((h[0].win, h[0].lose), (1, 0));
        assert_eq!((h[1].win, h[1].lose), (0, 1));
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn retract_and_supersede_exclude_injectable() {
        let p = tmp_path("retract");
        let mut s = Store::load(&p);
        s.append(json!({"r":"hyp","op":"propose","id":"h1","q":"q1","t":"错的经验"}));
        s.append(json!({"r":"hyp","op":"propose","id":"h2","q":"q1","t":"对的解释"}));
        s.append(json!({"r":"hyp","op":"retract","id":"h1","why":"反证"}));
        s.append(json!({"r":"hyp","op":"supersede","id":"h2","by":"h3"}));
        assert!(s.injectable().is_empty(), "撤回与被替代的都不得注入");
        assert_eq!(s.hyps().len(), 2, "事件物化仍保留全部历史");
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn inject_line_conditions_and_cap() {
        let p = tmp_path("inject");
        let mut s = Store::load(&p);
        s.append(json!({"r":"hyp","op":"propose","id":"h1","q":"q1","t":"通用解释"}));
        s.append(json!({"r":"hyp","op":"propose","id":"h2","q":"q1","t":"只在设置页","conds":["Settings"]}));
        s.append(json!({"r":"hyp","op":"propose","id":"h3","q":"q1","t":"只在桌面","conds":["launcher"]}));
        let line = s.inject_line("com.android.settings.Main", "设置", 6);
        assert!(line.contains("h1") && line.contains("h2"), "适用条件匹配的要注入: {line}");
        assert!(!line.contains("h3"), "不适用页面的不注入: {line}");
        let capped = s.inject_line("com.android.settings.Main", "设置", 1);
        assert_eq!(capped.matches("hyp#").count(), 1, "注入上限生效");
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn pred_register_outcome_pending() {
        let p = tmp_path("pred");
        let mut s = Store::load(&p);
        s.append(json!({"r":"pred","op":"register","id":"p1","hyps":["h1","h2"],
            "expect":{"h1":"probe_found","h2":"probe_not_found"},
            "test":{"kind":"probe","a":"find","q":"About"},"page_hint":"settings"}));
        assert_eq!(s.pending_preds().len(), 1);
        s.append(json!({"r":"pred","op":"outcome","id":"p1","observed":"命中1处","assert":"pass",
            "revises":[{"id":"h1","op":"support"},{"id":"h2","op":"against"}]}));
        assert!(s.pending_preds().is_empty());
        let preds = s.preds();
        assert_eq!(preds[0].outcome.as_ref().unwrap().assert, "pass");
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn next_id_sequences() {
        let p = tmp_path("ids");
        let mut s = Store::load(&p);
        assert_eq!(s.next_id("h"), "h1");
        s.append(json!({"r":"hyp","op":"propose","id":"h7","q":"q","t":"x"}));
        assert_eq!(s.next_id("h"), "h8");
        assert_eq!(s.next_id("p"), "p1");
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn eval_assert_truth_table() {
        // 探针通道
        assert_eq!(eval_assert("probe_found", &Obs::Probe("'About'命中2处: ...")), Some(true));
        assert_eq!(eval_assert("probe_found", &Obs::Probe("当前屏未找到'About'")), Some(false));
        assert_eq!(eval_assert("probe_found", &Obs::Probe("元素树本步不可信(假树),探针无数据")), None);
        assert_eq!(eval_assert("probe_not_found", &Obs::Probe("当前屏未找到'About'")), Some(true));
        assert_eq!(eval_assert("probe_ans_contains:可点", &Obs::Probe("'x'命中1处: \"x\" [0,0,1,1](可点)")), Some(true));
        assert_eq!(eval_assert("probe_ans_contains:可点", &Obs::Probe("本屏无UI树,探针无数据")), None);
        // diff 通道
        assert_eq!(eval_assert("diff_none", &Obs::Diff("none")), Some(true));
        assert_eq!(eval_assert("diff_none", &Obs::Diff("+[About]")), Some(false));
        assert_eq!(eval_assert("diff_not_none", &Obs::Diff("pixel(12%)")), Some(true));
        assert_eq!(eval_assert("diff_not_none", &Obs::Diff("rejected(空白点击)")), None);
        assert_eq!(eval_assert("rejected", &Obs::Diff("rejected(前科)")), Some(true));
        // 跨通道判不了
        assert_eq!(eval_assert("probe_found", &Obs::Diff("none")), None);
        assert_eq!(eval_assert("diff_none", &Obs::Probe("命中")), None);
    }

    #[test]
    fn parse_hypothesize_valid_full() {
        let text = r#"前言可以忽略 {"q":"q1","hyps":[
            {"id":"a","t":"吸附到了错误坐标","conds":["设置列表"]},
            {"id":"b","t":"diff通道遗漏变化"}],
            "test":{"kind":"probe","a":"find","q":"About",
                    "expect":{"a":"probe_found","b":"probe_not_found"}}} 后缀"#;
        let (q, hyps, test) = parse_hypothesize(text).expect("合法输入必须解析");
        assert_eq!(q, "q1");
        assert_eq!(hyps.len(), 2);
        let t = test.unwrap();
        assert_eq!(t["kind"], "probe");
        assert_eq!(t["expect"].as_array().unwrap().len(), 2);
    }

    #[test]
    fn parse_hypothesize_rejects_bad() {
        // 无判别性(两个解释预测全同)
        assert!(parse_hypothesize(r#"{"q":"q1","hyps":[{"id":"a","t":"x"},{"id":"b","t":"y"}],
            "test":{"kind":"probe","a":"find","q":"A","expect":{"a":"probe_found","b":"probe_found"}}}"#).is_none());
        // 断言词不在封闭表
        assert!(parse_hypothesize(r#"{"q":"q1","hyps":[{"id":"a","t":"x"}],
            "test":{"kind":"probe","a":"find","q":"A","expect":{"a":"maybe_works"}}}"#).is_none());
        // expect 指向未声明的解释
        assert!(parse_hypothesize(r#"{"q":"q1","hyps":[{"id":"a","t":"x"}],
            "test":{"kind":"probe","a":"find","q":"A","expect":{"zzz":"probe_found"}}}"#).is_none());
        // 副作用动作不在白名单(tap 不开放给检验)
        assert!(parse_hypothesize(r#"{"q":"q1","hyps":[{"id":"a","t":"x"}],
            "test":{"kind":"act","a":"tap","expect":{"a":"diff_not_none"}}}"#).is_none());
        // 解释为空 / 过多
        assert!(parse_hypothesize(r#"{"q":"q1","hyps":[]}"#).is_none());
        assert!(parse_hypothesize(r#"{"q":"q1","hyps":[{"t":"1"},{"t":"2"},{"t":"3"},{"t":"4"},{"t":"5"}]}"#).is_none());
        // 无 test 也合法(只提解释)
        assert!(parse_hypothesize(r#"{"q":"q1","hyps":[{"t":"x"},{"t":"y"}]}"#).is_some());
    }
}
