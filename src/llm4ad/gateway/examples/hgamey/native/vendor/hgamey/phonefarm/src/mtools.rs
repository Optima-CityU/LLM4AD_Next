//! 候选测量工具的存储与校准(SPEC_EVOLUTION §2.4/§4)。
//! tools.jsonl(tasks/_global 一份) = append-only 事件溯源存储;当前状态由重放事件物化,不原地改写。
//! 证据纪律: 工具输出属 E3(程序断言),但 adopt 事件只能在校准门槛 calib_ok 通过时
//! 由确定性代码落账(验收场景5:误报工具不得进入正式反馈路径);模型提议只产生 propose。
//! 执行器为受限声明式(v1: logrep/xmlassert),输入是调用方给定的只读文本,
//! 绝不碰文件系统与设备,输出固定 out_schema {match:bool, detail:str}。
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;

/// 测量工具生命周期状态(物化结果,不落盘)
#[derive(Clone, Debug, PartialEq)]
pub enum ToolStatus {
    /// 已提出,尚未校准
    Proposed,
    /// 已有校准结果(是否可用由 adopt 事件判定)
    Calibrated,
    /// 校准通过,进入正式反馈路径
    Adopted,
    /// 已退役(终态,须重新 propose 才能再走生命周期)
    Retired,
}

#[derive(Clone, Debug)]
pub struct Tool {
    pub id: String,
    pub kind: String,
    pub def: Value,
    pub status: ToolStatus,
    pub errors: u32,
    pub samples: u32,
    pub limits: String,
}

/// 全局测量工具存储:路径 + 全量事件
pub struct ToolStore {
    pub path: String,
    events: Vec<Value>,
}

/// 校准门槛(纯函数,SPEC §2.4/验收场景5): 样本数达标 && 误判未超限 && 零误报成功。
/// false_success(把非 ok 样本判成 ok)一票否决——误报成功的工具会把失败粉饰成成功,比漏报更危险。
pub fn calib_ok(samples: u32, errors: u32, false_success: u32, min_samples: u32, max_errors: u32) -> bool {
    samples >= min_samples && errors <= max_errors && false_success == 0
}

/// 统计校准样本中的误报成功条数(纯函数): label(真实)!=ok 而 got(工具判)==ok。
pub fn count_false_success(samples: &[Value]) -> u32 {
    samples
        .iter()
        .filter(|s| s["label"].as_str().unwrap_or("") != "ok" && s["got"].as_str().unwrap_or("") == "ok")
        .count() as u32
}

/// detail 截断到 80 字(按字符计,不切 UTF-8 边界)
fn clip80(s: &str) -> String {
    s.chars().take(80).collect()
}

/// logrep 执行器: def.pattern 为子串探针,`|` 分隔多词任中(v1 不引正则,用 contains)。
/// 输入为调用方读好的日志文本;返回固定 schema {match:bool, detail:str}。
pub fn run_logrep(def: &Value, haystack: &str) -> Value {
    let pattern = def["pattern"].as_str().unwrap_or("");
    let words: Vec<&str> = pattern.split('|').map(|w| w.trim()).filter(|w| !w.is_empty()).collect();
    // 空词整体剔除,避免 contains("") 恒真造成误中
    let hit = words.iter().find(|w| haystack.contains(**w));
    let detail = match hit {
        Some(w) => format!("命中词'{w}'"),
        None => format!("未命中任一词[{pattern}]"),
    };
    json!({"match": hit.is_some(), "detail": clip80(&detail)})
}

/// xmlassert 执行器: def.text 必现词 / def.text_absent 禁现词,均查 contains。
/// match = 必现词在(若给)且禁现词不在(若给);返回固定 schema {match:bool, detail:str}。
pub fn run_xmlassert(def: &Value, xml: &str) -> Value {
    let need = def["text"].as_str().unwrap_or("");
    let ban = def["text_absent"].as_str().unwrap_or("");
    let need_ok = need.is_empty() || xml.contains(need);
    let ban_ok = ban.is_empty() || !xml.contains(ban);
    let detail = if !need_ok {
        format!("必现词'{need}'未出现")
    } else if !ban_ok {
        format!("禁现词'{ban}'出现")
    } else {
        let mut parts = Vec::new();
        if !need.is_empty() {
            parts.push(format!("必现词'{need}'在"));
        }
        if !ban.is_empty() {
            parts.push(format!("禁现词'{ban}'不在"));
        }
        if parts.is_empty() {
            "无断言条件".to_string()
        } else {
            parts.join(",")
        }
    };
    json!({"match": need_ok && ban_ok, "detail": clip80(&detail)})
}

impl ToolStore {
    /// 读盘重放;文件不存在=空存储(零扰动)
    pub fn load(path: &str) -> ToolStore {
        let events = fs::read_to_string(path)
            .map(|s| {
                s.lines()
                    .filter_map(|l| serde_json::from_str::<Value>(l).ok())
                    .filter(|v| v["r"] == "tool")
                    .collect()
            })
            .unwrap_or_default();
        ToolStore { path: path.into(), events }
    }

    /// 追加一个事件(落盘+入内存)。文件不存在时先写 {"v":1} 头。
    pub fn append(&mut self, v: Value) {
        let line = serde_json::to_string(&v).unwrap_or_default();
        if line.is_empty() {
            return;
        }
        if !std::path::Path::new(&self.path).exists() {
            let _ = fs::write(&self.path, "{\"v\":1}\n");
        }
        if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(&self.path) {
            use std::io::Write;
            let _ = writeln!(f, "{line}");
        }
        self.events.push(v);
    }

    pub fn events(&self) -> &[Value] {
        &self.events
    }

    /// 生成该存储内的下一个空闲 id(前缀+数字,如 m3)
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

    /// 物化全部工具(按提出顺序)
    pub fn tools(&self) -> Vec<Tool> {
        let mut m: HashMap<String, Tool> = HashMap::new();
        let mut order: Vec<String> = Vec::new();
        for e in &self.events {
            if e["r"] != "tool" {
                continue;
            }
            let op = e["op"].as_str().unwrap_or("");
            let id = e["id"].as_str().unwrap_or("").to_string();
            if id.is_empty() {
                continue;
            }
            match op {
                "propose" => {
                    if !m.contains_key(&id) {
                        order.push(id.clone());
                    }
                    // 同 id 重新 propose(修订定义): 状态复位,校准计数清零,重走生命周期
                    m.insert(
                        id.clone(),
                        Tool {
                            id: id.clone(),
                            kind: e["kind"].as_str().unwrap_or("").into(),
                            def: e["def"].clone(),
                            status: ToolStatus::Proposed,
                            errors: 0,
                            samples: 0,
                            limits: String::new(),
                        },
                    );
                }
                "calibrate" => {
                    if let Some(t) = m.get_mut(&id) {
                        // 最新一条 calibrate 全量覆盖校准结果(重校即重判)
                        t.errors = e["errors"].as_u64().unwrap_or(0) as u32;
                        t.samples = e["samples"].as_array().map(|a| a.len() as u32).unwrap_or(0);
                        t.limits = e["limits"].as_str().unwrap_or("").into();
                        if t.status == ToolStatus::Proposed {
                            t.status = ToolStatus::Calibrated;
                        }
                    }
                }
                "adopt" => {
                    if let Some(t) = m.get_mut(&id) {
                        // retire 是终态:已退役工具不得被 adopt 复活,须重新 propose
                        if t.status != ToolStatus::Retired {
                            t.status = ToolStatus::Adopted;
                        }
                    }
                }
                "retire" => {
                    if let Some(t) = m.get_mut(&id) {
                        t.status = ToolStatus::Retired;
                    }
                }
                _ => {}
            }
        }
        order.into_iter().filter_map(|id| m.get(&id).cloned()).collect()
    }

    /// 当前正式反馈路径上的工具(Adopted;退役为终态故天然排除 Retired)
    pub fn adopted(&self) -> Vec<Tool> {
        self.tools().into_iter().filter(|t| t.status == ToolStatus::Adopted).collect()
    }

    pub fn by_id(&self, id: &str) -> Option<Tool> {
        self.tools().into_iter().find(|t| t.id == id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_path(tag: &str) -> String {
        let p = std::env::temp_dir().join(format!("pf-mtools-test-{}-{}.jsonl", tag, std::process::id()));
        let _ = fs::remove_file(&p);
        p.to_string_lossy().to_string()
    }

    #[test]
    fn tool_lifecycle_materialize() {
        let p = tmp_path("lifecycle");
        let mut s = ToolStore::load(&p);
        s.append(json!({"r":"tool","op":"propose","id":"m1","kind":"logrep",
            "def":{"pattern":"ANR in","in":"logcat"},"out_schema":{"match":"bool","detail":"str"}}));
        assert_eq!(s.by_id("m1").unwrap().status, ToolStatus::Proposed);
        // 两次校准: 最新一条全量覆盖 errors/samples/limits
        s.append(json!({"r":"tool","op":"calibrate","id":"m1",
            "samples":[{"run":"r1","label":"fail","got":"fail"}],"errors":1,"limits":"草稿"}));
        s.append(json!({"r":"tool","op":"calibrate","id":"m1",
            "samples":[{"run":"r1","label":"fail","got":"fail"},{"run":"r2","label":"ok","got":"ok"},
                       {"run":"r3","label":"fail","got":"fail"},{"run":"r4","label":"ok","got":"ok"}],
            "errors":0,"limits":"仅对含logcat的局有效"}));
        let t = s.by_id("m1").unwrap();
        assert_eq!(t.status, ToolStatus::Calibrated);
        assert_eq!((t.errors, t.samples), (0, 4), "最新一条 calibrate 全量覆盖");
        assert_eq!(t.limits, "仅对含logcat的局有效");
        s.append(json!({"r":"tool","op":"adopt","id":"m1"}));
        assert_eq!(s.by_id("m1").unwrap().status, ToolStatus::Adopted);
        s.append(json!({"r":"tool","op":"retire","id":"m1","why":"误报率2/5超过阈值"}));
        let t = s.by_id("m1").unwrap();
        assert_eq!(t.status, ToolStatus::Retired);
        assert_eq!(t.kind, "logrep", "退役后定义仍保留可审计");
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn calib_ok_thresholds() {
        // 达标: 样本数够 + 误判未超限 + 零误报成功
        assert!(calib_ok(4, 1, 0, 4, 1));
        assert!(calib_ok(5, 0, 0, 4, 1));
        // 样本不足
        assert!(!calib_ok(3, 0, 0, 4, 1));
        // 误判超限
        assert!(!calib_ok(4, 2, 0, 4, 1));
        // 误报成功一票否决: 即使 errors=0 也不通过(SPEC 验收场景5)
        assert!(!calib_ok(10, 0, 1, 4, 1));
    }

    #[test]
    fn count_false_success_stats() {
        let samples = json!([
            {"run":"r1","label":"fail","got":"fail"},
            {"run":"r2","label":"ok","got":"ok"},
            {"run":"r3","label":"fail","got":"ok"},
            {"run":"r4","label":"none","got":"ok"},
            {"run":"r5","label":"ok","got":"fail"}
        ]);
        // r3/r4 误报成功(真实非ok被判ok);r5 是漏报不算
        assert_eq!(count_false_success(samples.as_array().unwrap()), 2);
        assert_eq!(count_false_success(&[]), 0);
    }

    #[test]
    fn logrep_single_and_multi_word() {
        let log = "12-01 10:00:00 E ActivityManager: ANR in com.example.app\n12-01 10:00:01 I ok";
        // 单词命中
        let r = run_logrep(&json!({"pattern":"ANR in","in":"logcat"}), log);
        assert_eq!(r["match"], true);
        // 多词任中: 第一个不中第二个中
        let r = run_logrep(&json!({"pattern":"FATAL EXCEPTION|ANR in"}), log);
        assert_eq!(r["match"], true);
        // 全不中
        let r = run_logrep(&json!({"pattern":"FATAL EXCEPTION|Watchdog killing"}), log);
        assert_eq!(r["match"], false);
        // 输出固定 schema 且 detail ≤80 字
        assert!(r["match"].is_boolean() && r["detail"].is_string());
        assert!(r["detail"].as_str().unwrap().chars().count() <= 80);
        // 空 pattern 不得误中(contains("") 恒真陷阱)
        let r = run_logrep(&json!({"pattern":""}), log);
        assert_eq!(r["match"], false);
    }

    #[test]
    fn xmlassert_need_and_ban() {
        let xml = r#"<hierarchy><node text="设置" clickable="true"/><node text="WLAN"/></hierarchy>"#;
        // 必现在 + 禁现不在
        let r = run_xmlassert(&json!({"text":"设置","text_absent":"崩溃"}), xml);
        assert_eq!(r["match"], true);
        // 必现不在
        let r = run_xmlassert(&json!({"text":"关于手机"}), xml);
        assert_eq!(r["match"], false);
        // 禁现却在
        let r = run_xmlassert(&json!({"text":"设置","text_absent":"WLAN"}), xml);
        assert_eq!(r["match"], false);
        // 只给禁现且不在
        let r = run_xmlassert(&json!({"text_absent":"广告"}), xml);
        assert_eq!(r["match"], true);
        assert!(r["detail"].as_str().unwrap().chars().count() <= 80);
    }

    #[test]
    fn adopted_excludes_retired() {
        let p = tmp_path("adopted");
        let mut s = ToolStore::load(&p);
        for id in ["m1", "m2"] {
            s.append(json!({"r":"tool","op":"propose","id":id,"kind":"xmlassert","def":{"text":"设置"}}));
            s.append(json!({"r":"tool","op":"calibrate","id":id,"samples":[],"errors":0,"limits":""}));
            s.append(json!({"r":"tool","op":"adopt","id":id}));
        }
        s.append(json!({"r":"tool","op":"retire","id":"m2","why":"误报率2/5超过阈值"}));
        let ad = s.adopted();
        assert_eq!(ad.len(), 1);
        assert_eq!(ad[0].id, "m1");
        // 退役是终态: 后续 adopt 不得复活(须重新 propose 走完整生命周期)
        s.append(json!({"r":"tool","op":"adopt","id":"m2"}));
        assert_eq!(s.by_id("m2").unwrap().status, ToolStatus::Retired);
        assert_eq!(s.adopted().len(), 1);
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn next_id_sequences() {
        let p = tmp_path("ids");
        let mut s = ToolStore::load(&p);
        assert_eq!(s.next_id("m"), "m1");
        s.append(json!({"r":"tool","op":"propose","id":"m2","kind":"logrep","def":{}}));
        assert_eq!(s.next_id("m"), "m3");
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn reload_persist_and_header() {
        let p = tmp_path("reload");
        {
            let mut s = ToolStore::load(&p);
            s.append(json!({"r":"tool","op":"propose","id":"m1","kind":"logrep","def":{"pattern":"x"}}));
            s.append(json!({"r":"tool","op":"calibrate","id":"m1",
                "samples":[{"run":"r1","label":"ok","got":"ok"}],"errors":0,"limits":"L"}));
        }
        // 首行恒为 {"v":1},事件只追加
        let raw = fs::read_to_string(&p).unwrap();
        assert!(raw.starts_with("{\"v\":1}\n"));
        assert_eq!(raw.lines().count(), 3);
        // 重载后物化一致;非 tool 行(版本头)被忽略
        let s2 = ToolStore::load(&p);
        let t = s2.by_id("m1").unwrap();
        assert_eq!(t.status, ToolStatus::Calibrated);
        assert_eq!((t.samples, t.errors, t.limits.as_str()), (1, 0, "L"));
        assert_eq!(s2.events().len(), 2);
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn repropose_resets_lifecycle() {
        let p = tmp_path("repropose");
        let mut s = ToolStore::load(&p);
        s.append(json!({"r":"tool","op":"propose","id":"m1","kind":"logrep","def":{"pattern":"a"}}));
        s.append(json!({"r":"tool","op":"adopt","id":"m1"}));
        // 同 id 重新 propose(修订定义): 状态复位回 Proposed,定义更新,校准计数清零
        s.append(json!({"r":"tool","op":"propose","id":"m1","kind":"logrep","def":{"pattern":"b"}}));
        let t = s.by_id("m1").unwrap();
        assert_eq!(t.status, ToolStatus::Proposed);
        assert_eq!(t.def["pattern"], "b");
        assert_eq!((t.errors, t.samples), (0, 0));
        assert_eq!(s.tools().len(), 1, "同 id 不产生重复条目");
        let _ = fs::remove_file(&p);
    }
}
