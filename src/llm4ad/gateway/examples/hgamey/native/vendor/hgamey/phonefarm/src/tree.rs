//! 交互网 tree.json 的运行时读取侧 + 离线重建(rebuild,原 build_tree.py 的 Rust 收编):
//! 页面识别(核心词身份证) + 熟路导航(goto: BFS 只走"熟且可回放"的边,零模型调用)。
//! 网是只读参考不是权威: 任何一跳落点对不上预期页,立即中断把画面交还模型。
use crate::device::Node;
use serde::Deserialize;

#[derive(Deserialize, Clone)]
pub struct Page {
    pub id: i64,
    pub name: String,
    pub core: Vec<String>,
    #[serde(default)]
    pub visits: u64,
}

#[derive(Deserialize, Clone)]
pub struct Edge {
    pub from: i64,
    pub label: String,
    pub to: i64,
    #[serde(default)]
    pub n: u64,
    #[serde(default)]
    pub ripe: bool,
}

#[derive(Deserialize)]
pub struct Tree {
    pub pages: Vec<Page>,
    pub edges: Vec<Edge>,
}

/// 聚类/熟路常量(重建与运行时读取共享,TREE_RUST_SPEC: 单一出处防两处漂移)
const MATCH: f64 = 0.8;        // 画面归页门槛: 核心词 ≥80% 在场
const SHORT: usize = 6;        // 骨架词长度上限(字符数)
const FREQ: u32 = 5;           // 骨架词全局出现次数下限
const CORE_KEEP: f64 = 0.5;    // 核心收紧: ≥50% 成员含该词才留
const EDGE_MIN: u64 = 3;       // 熟路: 至少走过次数
const EDGE_PURITY: f64 = 0.75; // 熟路: 最常见终点占比下限

impl Tree {
    pub fn load(path: &str) -> Option<Tree> {
        let s = std::fs::read_to_string(path).ok()?;
        serde_json::from_str(&s).ok()
    }

    pub fn page(&self, id: i64) -> Option<&Page> {
        self.pages.iter().find(|p| p.id == id)
    }

    /// 页面身份证: 元素短文字(≤6字)命中某页核心词的比例,最高者 ≥MATCH 即认定。
    /// 信息流碎片只会多出文字、不会挤走骨架词, containment 天然抗噪。
    pub fn page_of(&self, els: &[Node]) -> Option<&Page> {
        let texts: std::collections::HashSet<&str> = els
            .iter()
            .map(|n| n.t.trim())
            .filter(|t| t.chars().count() <= 6)
            .collect();
        let mut best: (f64, usize) = (0.0, 0);
        for (i, p) in self.pages.iter().enumerate() {
            if p.core.is_empty() { continue; }
            let hit = p.core.iter().filter(|c| texts.contains(c.trim())).count();
            let score = hit as f64 / p.core.len() as f64;
            if score > best.0 { best = (score, i); }
        }
        (best.0 >= MATCH).then(|| &self.pages[best.1])
    }

    /// 可回放的边: 文字锚定的tap / 返回 / 滚动 / 回桌面。
    /// 点裸坐标(位置会漂)与"等待"(无导航意义)不参与回放。
    fn replayable(e: &Edge) -> bool {
        e.ripe
            && (e.label.starts_with("点[")
                || matches!(e.label.as_str(), "返回" | "上滑" | "下滑" | "回桌面"))
    }

    /// BFS 找熟路;from==to 返回空路(已在目的地)。
    pub fn route(&self, from: i64, to: i64) -> Option<Vec<Edge>> {
        if from == to { return Some(vec![]); }
        let mut prev: std::collections::HashMap<i64, (i64, usize)> = Default::default();
        let mut seen: std::collections::HashSet<i64> = [from].into_iter().collect();
        let mut queue = std::collections::VecDeque::from([from]);
        while let Some(cur) = queue.pop_front() {
            for (ei, e) in self.edges.iter().enumerate() {
                if e.from != cur || !Self::replayable(e) || seen.contains(&e.to) { continue; }
                prev.insert(e.to, (cur, ei));
                if e.to == to {
                    let mut path = Vec::new();
                    let mut node = to;
                    while node != from {
                        let (p, ei) = prev[&node];
                        path.push(self.edges[ei].clone());
                        node = p;
                    }
                    path.reverse();
                    return Some(path);
                }
                seen.insert(e.to);
                queue.push_back(e.to);
            }
        }
        None
    }

    /// 上下文本地小地图: 当前页 + 邻页熟路(只列可回放的,模型才知道 goto 能到哪)
    pub fn map_line(&self, cur: i64) -> Option<String> {
        let p = self.page(cur)?;
        let cuts: Vec<&Edge> =
            self.edges.iter().filter(|e| e.from == cur && Self::replayable(e)).collect();
        if cuts.is_empty() { return None; }
        let hops: Vec<String> = cuts.iter().take(6).map(|e| {
            let name = self.page(e.to).map(|q| crate::runtime::tcut(&q.name, 10)).unwrap_or_else(|| "?".into());
            format!("{}→P{}[{}]×{}", e.label, e.to, name, e.n)
        }).collect();
        Some(format!(
            "map: 当前P{}[{}](到访{}次) 熟路: {}",
            cur, crate::runtime::tcut(&p.name, 16), p.visits, hops.join(" ; ")
        ))
    }

    /// goto 目标解析: P5 / 5 / 页面名原文
    pub fn resolve(&self, t: &str) -> Option<i64> {
        let t = t.trim();
        if let Ok(id) = t.trim_start_matches('P').parse::<i64>() {
            if self.page(id).is_some() { return Some(id); }
        }
        self.pages.iter().find(|p| p.name == t).map(|p| p.id)
    }

    /// 探索沙盘: 当前页"没点过的按钮" = 骨架短词(≤6字)里,历史从未从本页以 点[该词] 走出过边的。
    /// v0.6: 输入改为折叠后骨架的普通文字行——卡片内部噪音(点赞数/作者名)已收进折叠头,
    /// 天然出局(#18 根治);≤6字+上限8条的防灌水规则保留。历史已走的按钮自然出局。
    pub fn unexplored(&self, pid: i64, texts: &[&str], blocked: &[String]) -> Vec<String> {
        let tapped: std::collections::HashSet<&str> = self.edges.iter()
            .filter(|e| e.from == pid)
            .filter_map(|e| Tree::tap_text(&e.label))
            .map(str::trim)
            .collect();
        let mut seen: std::collections::HashSet<String> = Default::default();
        let mut out = Vec::new();
        for t in texts {
            let t = t.trim();
            if t.chars().count() > 6 || t.is_empty() || tapped.contains(t)
                || blocked.iter().any(|b| b.trim() == t) { continue; }
            if seen.insert(t.to_string()) {
                out.push(crate::runtime::tcut(t, 8));
                if out.len() >= 8 { break; }
            }
        }
        out
    }

    /// 探索沙盘: 离当前最近的未访问页(BFS 只过熟路,不再深入未访分支)。
    /// 返回 (页id, 跳数),全访问过或不可达返回 None。
    pub fn nearest_unvisited(&self, cur: i64, visited: &std::collections::HashSet<i64>)
        -> Option<(i64, usize)>
    {
        let mut dist: std::collections::HashMap<i64, usize> = Default::default();
        let mut queue = std::collections::VecDeque::from([(cur, 0usize)]);
        dist.insert(cur, 0);
        let mut best: Option<(i64, usize)> = None;
        while let Some((p, d)) = queue.pop_front() {
            if p != cur && !visited.contains(&p) {
                if best.map_or(true, |(_, bd)| d < bd) { best = Some((p, d)); }
                continue; // 未访页只当目的地,不作为中转
            }
            for e in &self.edges {
                if e.from == p && Self::replayable(e) && !dist.contains_key(&e.to) {
                    dist.insert(e.to, d + 1);
                    queue.push_back((e.to, d + 1));
                }
            }
        }
        best
    }

    /// 边标签 → 按钮文字(仅 点[..] 型)
    pub fn tap_text(label: &str) -> Option<&str> {
        label.strip_prefix("点[").and_then(|s| s.strip_suffix(']'))
    }
}

// ────────────── 离线重建(TREE_RUST_SPEC v1.0: build_tree.py 等价迁移,单二进制闭环) ──────────────
// 顺序敏感处逐条对照 python 原文复刻: 聚类按簇序扫描严格大于取首簇;most_common 平局取
// 先插入的终点;pid 按到访次数稳定排序;edges 按 (起点簇号,标签) 排序(UTF-8 字节序=码点序,
// 与 python 字符串排序一致)。唯一有意偏差: 页面命名的同分词序——python 的 set 迭代序受
// 哈希随机化影响本就跨进程不稳定,这里以字典序为平局基准,让输出可复现。

struct StepM {
    run: String,
    els: Vec<(String, Option<[i64; 4]>)>, // (t, b)
    act: serde_json::Value,
    diff: String,
}

/// runs/*/log.jsonl → (screen,act,diff) 三元组(同局按步号排序;无 act 的步不构成边材料)
fn load_runs(task_dir: &str) -> Vec<StepM> {
    let mut files: Vec<String> = std::fs::read_dir(format!("{task_dir}/runs"))
        .ok().into_iter().flatten().filter_map(|e| e.ok())
        .filter_map(|e| e.file_name().to_str().map(String::from))
        .collect();
    files.sort();
    let mut steps = Vec::new();
    for run in files {
        let path = format!("{task_dir}/runs/{run}/log.jsonl");
        let Ok(text) = std::fs::read_to_string(&path) else { continue };
        let mut by_n: std::collections::BTreeMap<i64, [Option<serde_json::Value>; 3]> = Default::default();
        for line in text.lines().filter(|l| !l.trim().is_empty()) {
            let Ok(r) = serde_json::from_str::<serde_json::Value>(line) else { continue };
            let Some(n) = r["n"].as_i64() else { continue };
            let slot = match r["r"].as_str() {
                Some("screen") => 0,
                Some("act") => 1,
                Some("diff") => 2,
                _ => continue,
            };
            by_n.entry(n).or_default()[slot] = Some(r);
        }
        for (_, [scr, act, diff]) in by_n {
            let (Some(scr), Some(act)) = (scr, act) else { continue };
            let els = scr["els"].as_array().cloned().unwrap_or_default().iter().map(|e| {
                let t = e["t"].as_str().unwrap_or("").to_string();
                let b = e["b"].as_array().and_then(|a| {
                    let v: Vec<i64> = a.iter().filter_map(|x| x.as_i64()).collect();
                    (v.len() == 4).then(|| [v[0], v[1], v[2], v[3]])
                });
                (t, b)
            }).collect();
            let d = diff.as_ref().and_then(|d| d["d"].as_str()).unwrap_or("?").to_string();
            steps.push(StepM { run: run.clone(), els, act, diff: d });
        }
    }
    steps
}

/// 动作 → 边标签(与 python act_label 逐分支等价;截断按字符数)
fn act_label(els: &[(String, Option<[i64; 4]>)], a: &serde_json::Value) -> String {
    let cut12 = |t: &str| t.chars().take(12).collect::<String>();
    let kind = a["a"].as_str().unwrap_or("?");
    if kind == "tap" {
        let w = a["what"].as_str().unwrap_or("");
        if let Some(rest) = w.strip_prefix("icon:") {
            return format!("点图标[{}]", cut12(rest));
        }
        let (x, y) = (a["x"].as_i64().unwrap_or(-1), a["y"].as_i64().unwrap_or(-1));
        let mut best: Option<(i64, String)> = None;
        for (t, b) in els {
            if let Some(b) = b {
                if b[0] <= x && x <= b[2] && b[1] <= y && y <= b[3] {
                    let area = (b[2] - b[0]) * (b[3] - b[1]);
                    if best.as_ref().map_or(true, |(ba, _)| area < *ba) {
                        best = Some((area, cut12(t.trim())));
                    }
                }
            }
        }
        return match best {
            Some((_, t)) => format!("点[{t}]"),
            None => format!("点裸({x},{y})"),
        };
    }
    match kind {
        "scroll_up" => "上滑".into(),
        "scroll_down" => "下滑".into(),
        "swipe" => "滑动".into(),
        "back" => "返回".into(),
        "home" => "回桌面".into(),
        "wait" => "等待".into(),
        other => other.to_string(),
    }
}

/// pure 的 2 位小数: python round() 的偶数舍入。整数运算精确判半,cnt/total 恰为半时取偶。
fn round2(cnt: u64, total: u64) -> f64 {
    let num = cnt * 200;
    let q = num / (total * 2);
    let rem = num % (total * 2);
    let scaled = match rem.cmp(&total) {
        std::cmp::Ordering::Less => q,
        std::cmp::Ordering::Greater => q + 1,
        std::cmp::Ordering::Equal => if q % 2 == 0 { q } else { q + 1 },
    };
    scaled as f64 / 100.0
}

/// 离线重建交互网: tasks/<任务>/runs → tree.json(v1 契约不变)。返回写出的 JSON 文本。
pub fn rebuild(task_dir: &str) -> Result<String, String> {
    let steps = load_runs(task_dir);
    if steps.is_empty() {
        return Err(format!("没有可用的 (screen,act,diff) 记录: {task_dir}"));
    }
    // ── 第一遍: 全局词频筛骨架词 ──
    let mut freq: std::collections::HashMap<String, u32> = Default::default();
    for st in &steps {
        for (t, _) in &st.els {
            let t = t.trim();
            if !t.is_empty() && t.chars().count() <= SHORT {
                *freq.entry(t.to_string()).or_insert(0) += 1;
            }
        }
    }
    let sigs: Vec<std::collections::HashSet<String>> = steps.iter().map(|st| {
        st.els.iter().map(|(t, _)| t.trim())
            .filter(|t| !t.is_empty() && t.chars().count() <= SHORT
                && freq.get(*t).copied().unwrap_or(0) >= FREQ)
            .map(String::from).collect()
    }).collect();

    // ── 第二遍: 聚类(簇序扫描,严格大于→首簇优先;入簇后核心按 CORE_KEEP 收紧) ──
    struct Cl {
        core: std::collections::HashSet<String>,
        hist: std::collections::HashMap<String, u32>,
        members: u32,
    }
    let mut pages: Vec<Cl> = Vec::new();
    for sig in &sigs {
        let mut best: (Option<usize>, f64) = (None, 0.0);
        for (i, p) in pages.iter().enumerate() {
            let inter = p.core.iter().filter(|w| sig.contains(*w)).count();
            let c = inter as f64 / (p.core.len().max(1)) as f64;
            if c > best.1 { best = (Some(i), c); }
        }
        match best {
            (Some(i), score) if score >= MATCH => {
                let p = &mut pages[i];
                p.members += 1;
                for t in sig { *p.hist.entry(t.clone()).or_insert(0) += 1; }
                let th = p.members as f64 * CORE_KEEP;
                p.core = p.hist.iter().filter(|(_, c)| **c as f64 >= th)
                    .map(|(t, _)| t.clone()).collect();
            }
            _ => pages.push(Cl {
                core: sig.clone(),
                hist: sig.iter().map(|t| (t.clone(), 1)).collect(),
                members: 1,
            }),
        }
    }
    // 孤页剔除后重派: 每步 → 簇下标(-1 = 无家可归)
    let page_of: Vec<i64> = sigs.iter().map(|sig| {
        let mut hit: (i64, f64) = (-1, 0.0);
        for (i, p) in pages.iter().enumerate() {
            if p.members < 2 { continue; }
            let inter = p.core.iter().filter(|w| sig.contains(*w)).count();
            let c = inter as f64 / (p.core.len().max(1)) as f64;
            if c > hit.1 { hit = (i as i64, c); }
        }
        if hit.1 >= MATCH { hit.0 } else { -1 }
    }).collect();

    // 页面命名: TF-IDF 味道(页内频/全局频)。平局取字典序(见文件头注)
    let name_of = |p: &Cl| -> String {
        let mut ws: Vec<&String> = p.core.iter().collect();
        ws.sort();
        ws.sort_by(|a, b| {
            let sa = p.hist.get(*a).copied().unwrap_or(0) as f64
                / (freq.get(*a).copied().unwrap_or(0) + 1) as f64;
            let sb = p.hist.get(*b).copied().unwrap_or(0) as f64
                / (freq.get(*b).copied().unwrap_or(0) + 1) as f64;
            sb.partial_cmp(&sa).unwrap_or(std::cmp::Ordering::Equal)
        });
        if ws.is_empty() { "(空)".into() }
        else { ws.into_iter().take(4).cloned().collect::<Vec<_>>().join("/") }
    };

    // ── 边: 相邻步(同局) (起点簇,标签) → 终点簇;rejected 跳过;none/无终点记败 ──
    let mut edges: std::collections::BTreeMap<(i64, String), Vec<(i64, u64)>> = Default::default();
    let mut fails: std::collections::HashMap<(i64, String), u64> = Default::default();
    for i in 0..steps.len().saturating_sub(1) {
        if steps[i].run != steps[i + 1].run || page_of[i] < 0 { continue; }
        let label = act_label(&steps[i].els, &steps[i].act);
        let d = &steps[i].diff;
        if d.starts_with("rejected") { continue; }
        if d == "none" || page_of[i + 1] < 0 {
            *fails.entry((page_of[i], label)).or_insert(0) += 1;
            continue;
        }
        let tos = edges.entry((page_of[i], label)).or_default();
        match tos.iter_mut().find(|(t, _)| *t == page_of[i + 1]) {
            Some((_, c)) => *c += 1,
            None => tos.push((page_of[i + 1], 1)), // 插入序保留: 平局取先入(Counter.most_common 同义)
        }
    }

    // ── 产出: id 按到访次数稳定排名;pages 数组按原簇序 ──
    let mut ranked: Vec<usize> = (0..pages.len()).filter(|i| pages[*i].members >= 2).collect();
    ranked.sort_by_key(|i| std::cmp::Reverse(pages[*i].members));
    let pid: std::collections::HashMap<usize, usize> =
        ranked.iter().enumerate().map(|(rank, i)| (*i, rank)).collect();
    let mut orig: Vec<usize> = pid.keys().copied().collect();
    orig.sort();
    let pg_out: Vec<serde_json::Value> = orig.iter().map(|i| {
        let mut core: Vec<&String> = pages[*i].core.iter().collect();
        core.sort();
        serde_json::json!({"id": pid[i], "name": name_of(&pages[*i]),
            "core": core, "visits": pages[*i].members})
    }).collect();
    let edge_out: Vec<serde_json::Value> = edges.iter().map(|((frm, label), tos)| {
        let total: u64 = tos.iter().map(|(_, c)| c).sum();
        let (to, cnt) = tos.iter().fold((tos[0].0, 0u64), |acc, (t, c)| {
            if *c > acc.1 { (*t, *c) } else { acc }
        });
        serde_json::json!({
            "from": pid[&(*frm as usize)], "label": label, "to": pid[&(to as usize)],
            "n": total, "pure": round2(cnt, total),
            "fail": fails.get(&(*frm, label.clone())).copied().unwrap_or(0),
            "ripe": total >= EDGE_MIN && cnt as f64 / total as f64 >= EDGE_PURITY
        })
    }).collect();
    let orphan = page_of.iter().filter(|p| **p < 0).count();
    let tree = serde_json::json!({
        "v": 1, "task": task_dir, "pages": pg_out, "edges": edge_out,
        "steps_total": steps.len(), "orphan_screens": orphan
    });
    let text = serde_json::to_string_pretty(&tree).map_err(|e| e.to_string())?;
    std::fs::write(format!("{task_dir}/tree.json"), &text).map_err(|e| e.to_string())?;
    Ok(text)
}

#[cfg(test)]
mod rebuild_tests {
    use super::*;

    fn mk(n: i64, els: &str, act: &str, diff: &str) -> Vec<String> {
        vec![
            format!("{{\"r\":\"screen\",\"n\":{n},\"els\":[{els}]}}"),
            format!("{{\"r\":\"act\",\"n\":{n},{act}}}"),
            format!("{{\"r\":\"diff\",\"n\":{n},\"d\":\"{diff}\"}}"),
        ]
    }

    /// 两局材料: 页A(首页/推荐/设置) ⇄ 页B(隐私/通知/返回项),点[设置]走4次成熟路
    #[test]
    fn rebuild_pages_edges_ripe_orphan() {
        let d = std::env::temp_dir().join(format!("pf_tree_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        let pa = "{\"t\":\"首页\",\"b\":[0,0,100,50]},{\"t\":\"推荐\",\"b\":[0,60,100,110]},{\"t\":\"设置\",\"b\":[0,120,100,170]}";
        let pb = "{\"t\":\"隐私\",\"b\":[0,0,100,50]},{\"t\":\"通知\",\"b\":[0,60,100,110]},{\"t\":\"返回项\",\"b\":[0,120,100,170]}";
        let tap = "\"a\":\"tap\",\"x\":50,\"y\":140";
        let back = "\"a\":\"back\"";
        for run in ["r1", "r2"] {
            // 每词各出现 6 次(≥FREQ=5),孤帧词只出现 2 次(<5)自然出局
            let mut lines: Vec<String> = vec![];
            for k in 0..3 {
                lines.extend(mk(k * 2 + 1, pa, tap, "+[隐私]"));
                lines.extend(mk(k * 2 + 2, pb, back, "+[首页]"));
            }
            lines.extend(mk(7, "{\"t\":\"加载中\",\"b\":[0,0,10,10]}", back, "none"));
            let rd = d.join("runs").join(run);
            std::fs::create_dir_all(&rd).unwrap();
            std::fs::write(rd.join("log.jsonl"), lines.join("\n")).unwrap();
        }
        let text = rebuild(d.to_str().unwrap()).unwrap();
        let v: serde_json::Value = serde_json::from_str(&text).unwrap();
        let pages = v["pages"].as_array().unwrap();
        assert_eq!(pages.len(), 2, "A/B 两页,孤帧出局");
        assert_eq!(pages[0]["visits"], 6);
        let edges = v["edges"].as_array().unwrap();
        let tap_e = edges.iter().find(|e| e["label"] == "点[设置]").expect("命中元素文字回显");
        assert_eq!(tap_e["n"], 6);
        assert_eq!(tap_e["pure"], 1.0);
        assert_eq!(tap_e["ripe"], true, "6次同终点=熟路");
        assert_eq!(v["orphan_screens"], 2, "两局各1孤帧");
        // 读取侧兼容: Tree::load 能读回并沿熟路导航
        let t = Tree::load(&format!("{}/tree.json", d.to_str().unwrap())).unwrap();
        assert_eq!(t.pages.len(), 2);
        assert!(t.route(t.pages[0].id, t.pages[1].id).is_some(), "熟路可走");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn rebuild_err_on_empty_and_round2_half_even() {
        let d = std::env::temp_dir().join(format!("pf_tree_empty_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(d.join("runs")).unwrap();
        assert!(rebuild(d.to_str().unwrap()).is_err(), "无材料如实报错");
        let _ = std::fs::remove_dir_all(&d);
        // python round() 偶数舍入: 1/8→0.12, 7/8→0.88, 2/3→0.67
        assert_eq!(round2(1, 8), 0.12);
        assert_eq!(round2(7, 8), 0.88);
        assert_eq!(round2(2, 3), 0.67);
        assert_eq!(round2(3, 4), 0.75);
    }
}
