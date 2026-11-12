//! 能力固化存储层(SPEC_EVOLUTION §2.3/§5)。
//! capabilities.jsonl = append-only 事件溯源存储(每任务目录一个 + tasks/_global 一个);
//! 能力 = 经假设层证据筛选、带版本与回退的固化物,当前状态由重放事件物化,不原地改写。
//! 生命周期: candidate →(候选评测有收益)→ adopted →(退步/失效)→ rolledback;
//! 再启用须升 ver 重新走候选,全部历史可审计。
use serde_json::Value;
use std::collections::HashMap;
use std::fs;

/// 能力生命周期状态(物化结果,不落盘)
#[derive(Clone, Debug, PartialEq)]
pub enum CapStatus {
    /// 已登记候选,未经评测不得注入
    Candidate,
    /// 评测有收益,正式启用
    Adopted,
    /// 已回退,即刻停止注入
    Rolledback,
}

/// 能力类别封闭词表(SPEC §2.3):
/// knowledge=检索知识(注入上下文) / skill=结构化技能(动作宏) / tool=可执行测量工具。
/// 物化原样保留不硬过滤(与 hypo 一致的宽容解析),非法值由上层落账前拦截。
#[derive(Clone, Debug)]
pub struct Cap {
    pub id: String,
    pub kind: String,
    pub t: String,
    /// 适用任务;空=全局适用
    pub scope_tasks: Vec<String>,
    /// 适用条件(页面特征),注入时粗匹配
    pub scope_conds: Vec<String>,
    /// 来源(假设 id / 支撑 run 数等),审计留痕
    pub from: Value,
    /// 评测结果(含评分协议版本 proto)
    pub eval: Value,
    pub ver: u64,
    pub status: CapStatus,
}

/// 单任务(或 _global)的能力存储:路径 + 全量事件
pub struct CapStore {
    pub path: String,
    events: Vec<Value>,
}

/// 自动候选判定(SPEC §5 阈值,纯函数): 全胜且证据跨足多局才允许固化
pub fn auto_candidate_ok(win: u32, lose: u32, runs: u32) -> bool {
    win >= 3 && lose == 0 && runs >= 2
}

impl CapStore {
    /// 读盘重放;文件不存在=空存储(零扰动)
    pub fn load(path: &str) -> CapStore {
        let events = fs::read_to_string(path)
            .map(|s| {
                s.lines()
                    .filter_map(|l| serde_json::from_str::<Value>(l).ok())
                    .filter(|v| v["r"] == "cap")
                    .collect()
            })
            .unwrap_or_default();
        CapStore { path: path.into(), events }
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

    /// 生成该存储内的下一个空闲 id(前缀+数字,如 c4)
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

    /// 物化全部能力。
    /// candidate 登记;adopt 仅当 ver 匹配当前 candidate 版本时转 Adopted;
    /// rollback 转 Rolledback;同 id 再次 candidate 且 ver 更大 → 覆盖为新的 Candidate(版本链)。
    pub fn caps(&self) -> Vec<Cap> {
        let mut m: HashMap<String, Cap> = HashMap::new();
        let mut order: Vec<String> = Vec::new();
        for e in &self.events {
            if e["r"] != "cap" { continue; }
            let op = e["op"].as_str().unwrap_or("");
            let id = e["id"].as_str().unwrap_or("").to_string();
            if id.is_empty() { continue; }
            match op {
                "candidate" => {
                    let ver = e["ver"].as_u64().unwrap_or(1);
                    // 版本链: 仅当 ver 更大才覆盖(升 ver 重新候选);
                    // 同版/旧版的重复 candidate(如中断重试)不扰动当前状态
                    if m.get(&id).map(|old| ver <= old.ver).unwrap_or(false) { continue; }
                    if !m.contains_key(&id) { order.push(id.clone()); }
                    m.insert(id.clone(), Cap {
                        id: id.clone(),
                        kind: e["kind"].as_str().unwrap_or("knowledge").into(),
                        t: e["t"].as_str().unwrap_or("").into(),
                        scope_tasks: e["scope"]["tasks"].as_array()
                            .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
                            .unwrap_or_default(),
                        scope_conds: e["scope"]["conds"].as_array()
                            .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
                            .unwrap_or_default(),
                        from: e["from"].clone(),
                        eval: e["eval"].clone(),
                        ver,
                        status: CapStatus::Candidate,
                    });
                }
                "adopt" => {
                    let ver = e["ver"].as_u64().unwrap_or(1);
                    if let Some(c) = m.get_mut(&id) {
                        // 仅当 ver 匹配当前 candidate 版本时生效:
                        // 过期版本的 adopt 不得误伤新版本,已回退的也不得复活
                        if c.status == CapStatus::Candidate && c.ver == ver {
                            c.status = CapStatus::Adopted;
                        }
                    }
                }
                "rollback" => {
                    if let Some(c) = m.get_mut(&id) {
                        // 回退针对当前版本即刻生效;事件上的 ver 仅审计留痕
                        c.status = CapStatus::Rolledback;
                    }
                }
                _ => {}
            }
        }
        order.into_iter().filter_map(|id| m.get(&id).cloned()).collect()
    }

    /// 只回已启用(Adopted)的能力
    pub fn adopted(&self) -> Vec<Cap> {
        self.caps().into_iter().filter(|c| c.status == CapStatus::Adopted).collect()
    }

    /// 指定任务当前可注入的能力: Adopted 且 scope_tasks 为空(全局)或含该任务
    pub fn injectable(&self, task: &str) -> Vec<Cap> {
        self.adopted().into_iter()
            .filter(|c| c.scope_tasks.is_empty() || c.scope_tasks.iter().any(|t| t == task))
            .collect()
    }

    /// 决策上下文注入段(SPEC §2.3): 任务过滤 + 适用条件粗匹配 + 上限。
    /// conds 匹配照抄 hypo: activity/page 任一出现在 conds 中(或 conds 为空)即视为适用。
    pub fn inject_line(&self, task: &str, activity: &str, page_name: &str, max: usize) -> String {
        let ctx = format!("{activity}|{page_name}").to_lowercase();
        let mut s = String::new();
        let mut n = 0usize;
        for c in self.injectable(task) {
            if n >= max { break; }
            let applicable = c.scope_conds.is_empty()
                || c.scope_conds.iter().any(|cd| !cd.is_empty() && ctx.contains(&cd.to_lowercase()));
            if !applicable { continue; }
            s.push_str(&format!("cap#{}(ver{})[{}]: {}\n", c.id, c.ver, c.kind, c.t));
            n += 1;
        }
        s
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn tmp_path(tag: &str) -> String {
        let p = std::env::temp_dir().join(format!("pf-caps-test-{}-{}.jsonl", tag, std::process::id()));
        let _ = fs::remove_file(&p);
        p.to_string_lossy().to_string()
    }

    fn candidate_event(id: &str, ver: u64) -> Value {
        json!({"r":"cap","op":"candidate","id":id,"kind":"knowledge",
            "t":"设置列表About项在最底部需下滑3屏",
            "scope":{"tasks":["设置-关于手机"],"conds":["列表可滚动"]},
            "from":{"hyp":"h9","runs":3},"eval":{"proto":"v1","win":3,"lose":0},"ver":ver})
    }

    #[test]
    fn candidate_materialize_fields() {
        let p = tmp_path("materialize");
        let mut s = CapStore::load(&p);
        assert!(s.caps().is_empty());
        s.append(candidate_event("c1", 1));
        let caps = s.caps();
        assert_eq!(caps.len(), 1);
        let c = &caps[0];
        assert_eq!(c.id, "c1");
        assert_eq!(c.kind, "knowledge");
        assert_eq!(c.t, "设置列表About项在最底部需下滑3屏");
        assert_eq!(c.scope_tasks, vec!["设置-关于手机"]);
        assert_eq!(c.scope_conds, vec!["列表可滚动"]);
        assert_eq!(c.from["hyp"], "h9");
        assert_eq!(c.eval["win"], 3);
        assert_eq!(c.ver, 1);
        assert_eq!(c.status, CapStatus::Candidate);
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn adopt_requires_matching_ver() {
        let p = tmp_path("adoptver");
        let mut s = CapStore::load(&p);
        s.append(candidate_event("c1", 1));
        // ver 不匹配: adopt ver2 对当前 candidate ver1 不得生效
        s.append(json!({"r":"cap","op":"adopt","id":"c1","ver":2}));
        assert_eq!(s.caps()[0].status, CapStatus::Candidate);
        assert!(s.adopted().is_empty());
        // ver 匹配才生效
        s.append(json!({"r":"cap","op":"adopt","id":"c1","ver":1,"at":{"run":"r1","ts":0}}));
        assert_eq!(s.caps()[0].status, CapStatus::Adopted);
        assert_eq!(s.adopted().len(), 1);
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn version_chain_recandidate() {
        let p = tmp_path("chain");
        let mut s = CapStore::load(&p);
        s.append(candidate_event("c1", 1));
        s.append(json!({"r":"cap","op":"adopt","id":"c1","ver":1}));
        s.append(json!({"r":"cap","op":"rollback","id":"c1","ver":1,"why":"新初态下连续2次失效"}));
        assert_eq!(s.caps()[0].status, CapStatus::Rolledback);
        // 同 id 升 ver 重新候选 → 覆盖为新的 Candidate
        s.append(candidate_event("c1", 2));
        let c = &s.caps()[0];
        assert_eq!(c.ver, 2);
        assert_eq!(c.status, CapStatus::Candidate);
        // 旧版本的 adopt(ver1)不得作用于新版本
        s.append(json!({"r":"cap","op":"adopt","id":"c1","ver":1}));
        assert_eq!(s.caps()[0].status, CapStatus::Candidate);
        s.append(json!({"r":"cap","op":"adopt","id":"c1","ver":2}));
        assert_eq!(s.caps()[0].status, CapStatus::Adopted);
        // 同版重复 candidate(中断重试)不扰动已启用状态
        s.append(candidate_event("c1", 2));
        assert_eq!(s.caps()[0].status, CapStatus::Adopted);
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn rollback_excludes_injection() {
        let p = tmp_path("rollback");
        let mut s = CapStore::load(&p);
        s.append(candidate_event("c1", 1));
        s.append(json!({"r":"cap","op":"adopt","id":"c1","ver":1}));
        assert_eq!(s.injectable("设置-关于手机").len(), 1);
        s.append(json!({"r":"cap","op":"rollback","id":"c1","ver":1,"why":"失效","ev":{"rec":"r9"}}));
        assert!(s.injectable("设置-关于手机").is_empty(), "回退后即刻停止注入");
        assert!(s.inject_line("设置-关于手机", "com.android.settings.Main", "设置", 6).is_empty());
        assert_eq!(s.caps().len(), 1, "事件物化仍保留全部历史");
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn auto_candidate_thresholds() {
        assert!(!auto_candidate_ok(2, 0, 2), "win 不足 3");
        assert!(auto_candidate_ok(3, 0, 2), "达阈值");
        assert!(!auto_candidate_ok(3, 1, 3), "有败绩");
        assert!(!auto_candidate_ok(3, 0, 1), "证据未跨 2 局");
        assert!(auto_candidate_ok(4, 0, 3));
        assert!(!auto_candidate_ok(0, 0, 0));
    }

    #[test]
    fn inject_line_scope_conds_and_cap() {
        let p = tmp_path("inject");
        let mut s = CapStore::load(&p);
        // 全局能力(无 scope),无条件
        s.append(json!({"r":"cap","op":"candidate","id":"c1","kind":"knowledge","t":"全局通用经验","ver":1}));
        s.append(json!({"r":"cap","op":"adopt","id":"c1","ver":1}));
        // 任务限定 + 条件限定
        s.append(json!({"r":"cap","op":"candidate","id":"c2","kind":"skill","t":"只在关于手机页用",
            "scope":{"tasks":["设置-关于手机"],"conds":["Settings"]},"ver":1}));
        s.append(json!({"r":"cap","op":"adopt","id":"c2","ver":1}));
        // 条件不匹配当前页
        s.append(json!({"r":"cap","op":"candidate","id":"c3","kind":"knowledge","t":"只在桌面用",
            "scope":{"conds":["launcher"]},"ver":1}));
        s.append(json!({"r":"cap","op":"adopt","id":"c3","ver":1}));
        // 未 adopt 的 candidate 不注入
        s.append(json!({"r":"cap","op":"candidate","id":"c4","kind":"tool","t":"未启用","ver":1}));

        let line = s.inject_line("设置-关于手机", "com.android.settings.Main", "设置", 6);
        assert!(line.contains("cap#c1(ver1)[knowledge]: 全局通用经验"), "{line}");
        assert!(line.contains("cap#c2(ver1)[skill]: 只在关于手机页用"), "任务与条件都匹配: {line}");
        assert!(!line.contains("c3"), "条件不匹配不注入: {line}");
        assert!(!line.contains("c4"), "候选未启用不注入: {line}");
        // 任务不匹配: c2 的 scope_tasks 不含其他任务
        let other = s.inject_line("别的任务", "com.android.settings.Main", "设置", 6);
        assert!(!other.contains("c2"), "任务不匹配不注入: {other}");
        assert!(other.contains("c1"), "全局能力任意任务可注入: {other}");
        // 上限
        let capped = s.inject_line("设置-关于手机", "com.android.settings.Main", "设置", 1);
        assert_eq!(capped.matches("cap#").count(), 1, "注入上限生效");
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn next_id_sequences() {
        let p = tmp_path("ids");
        let mut s = CapStore::load(&p);
        assert_eq!(s.next_id("c"), "c1");
        s.append(candidate_event("c6", 1));
        assert_eq!(s.next_id("c"), "c7");
        assert_eq!(s.next_id("m"), "m1");
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn file_reload_persist() {
        let p = tmp_path("reload");
        {
            let mut s = CapStore::load(&p);
            s.append(candidate_event("c1", 1));
            s.append(json!({"r":"cap","op":"adopt","id":"c1","ver":1,"at":{"run":"r1","ts":0}}));
        }
        // 落盘可重载,物化状态一致
        let s2 = CapStore::load(&p);
        assert_eq!(s2.events().len(), 2);
        let caps = s2.caps();
        assert_eq!(caps.len(), 1);
        assert_eq!(caps[0].status, CapStatus::Adopted);
        // 文件头必须是 {"v":1}
        let raw = fs::read_to_string(&p).unwrap();
        assert!(raw.starts_with("{\"v\":1}\n"));
        let _ = fs::remove_file(&p);
    }
}
