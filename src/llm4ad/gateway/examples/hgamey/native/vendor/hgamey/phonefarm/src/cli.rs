//! CLI 查看层 (CLI Spec v1.0): 万事万物一条命令可取——硬盘上任何 log/产物,CLI 打印;
//! 数据全在盘上,CLI 只是"发现路径"。只读原则: 查看命令只读盘,不跑设备不烧 token 不改状态
//! (probe 跑设备但只读约定;exec 高危,必须 --yes 显式确认)。
//! 局ID 模糊匹配: 前缀即可,多命中报候选;--task 默认=最近有局的任务;全命令支持 --json。
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

const SCHEMA_MD: &str = r#"# log.jsonl 记录契约 (schema)

每行一个 JSON 对象,`r` 字段标记类型。一局 = tasks/<任务>/runs/<局ID>/log.jsonl。

| r | 含义 | 主要字段 |
|---|---|---|
| goal | 局目标原文 | t=目标文本 |
| screen | 每步观测 | n=步号 els=[{t,b}] pkg activity ime_shown img xml ocr(备胎标记) |
| act | 执行的动作 | n a=动作名 x y x2 y2 text what by ms |
| diff | 动作后的画面差异 | n d="+[新增] -[消失]"或rejected(驳回理由) |
| note | 模型自写便签 | t |
| raw | 模型回包原文 | n hook=step/done/verdict by ms t=原文 |
| hook | 系统判定/事件 | kind=orient/assert/verdict/ctx_stat/oscill/webview_back2/heal/budget/tree/done_reflect/... |
| probe | 探针问答 | n a=inspect/find/get_state/history q ans |
| lesson | 复盘写入的经验 | id t born win lose |
| telemetry | 每步性能快照(平铺字段) | n seq heavy + 系统/渲染/App/Root/PSI/流量/传感器/IPC/Host 各层(采不到即缺席) |
| app_event | 遥测差值事件 | kind=crash/anr/fd_growth/net_conn n from to pkg |
| trace | 深度产物指针(按需抓取) | kind n file=trace/stepN.*.txt |
| start | 局开跑标记(status 活性判定的地基) | pid ts |
| end | 局级收官结论 | stop achieved steps exec_steps calls tokens wall_ms done_claim |

跨局产物: tree.json(交互网:pages/edges/steps_total) lessons.jsonl(经验库) campaign*.tsv(评测账)。
截图 stepN.jpg 与原始UI树 stepN.xml.gz(Android)/stepN.json(OH) 同目录,是账本的地面真值。
"#;

// ══════════════ 路径与数据发现 ══════════════

/// 仓库根的 tasks 目录(尊重 phonefarm.toml 的 data_dir,读不到用当前目录)。
/// PF_TASKS_ROOT 环境变量可整体覆盖(单测注入用,避免 chdir 的进程级竞态)。
pub(crate) fn data_root() -> PathBuf {
    if let Ok(o) = std::env::var("PF_TASKS_ROOT") {
        return PathBuf::from(o);
    }
    let dir = std::fs::read_to_string("phonefarm.toml").ok()
        .and_then(|s| toml::from_str::<Value>(&s).ok())
        .and_then(|v| v.get("data_dir").and_then(|d| d.as_str().map(String::from)))
        .unwrap_or_else(|| ".".into());
    PathBuf::from(dir.trim_end_matches('/')).join("tasks")
}

fn list_tasks() -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(data_root()).ok().into_iter().flatten()
        .filter_map(|e| e.ok())
        .filter(|e| e.path().join("runs").is_dir() || e.path().is_dir())
        .filter_map(|e| e.file_name().to_str().map(String::from))
        .filter(|n| !n.starts_with('.') && !n.starts_with('_'))
        .collect();
    v.sort();
    v
}

fn runs_of(task: &str) -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(data_root().join(task).join("runs"))
        .ok().into_iter().flatten().filter_map(|e| e.ok())
        .filter_map(|e| e.file_name().to_str().map(String::from))
        .filter(|n| !n.starts_with('.'))
        .collect();
    v.sort();
    v
}

/// 最近有局的任务(--task 缺省值): 全任务里 run 目录名(时间戳)最大的
fn latest_task() -> Option<String> {
    list_tasks().into_iter()
        .filter_map(|t| runs_of(&t).pop().map(|r| (r, t)))
        .max()
        .map(|(_, t)| t)
}

/// 局ID 模糊解析: 前缀命中;唯一命中返回 (任务, 局ID, 局目录);多命中列候选。
fn resolve_run(id: &str, task: Option<&str>) -> Result<(String, String, PathBuf), String> {
    let tasks = match task {
        Some(t) => vec![t.to_string()],
        None => list_tasks(),
    };
    let mut hits: Vec<(String, String)> = Vec::new();
    for t in &tasks {
        for r in runs_of(t) {
            if r.starts_with(id) {
                hits.push((t.clone(), r));
            }
        }
    }
    match hits.len() {
        0 => Err(format!("没有以 '{id}' 开头的局{}", task.map(|t| format!("(任务 {t})")).unwrap_or_default())),
        1 => {
            let (t, r) = hits.remove(0);
            let p = data_root().join(&t).join("runs").join(&r);
            Ok((t, r, p))
        }
        _ => {
            let list: Vec<String> = hits.iter().take(10)
                .map(|(t, r)| format!("  {r}  (任务 {t})")).collect();
            Err(format!("'{id}' 命中 {} 局,补长前缀二选一:\n{}", hits.len(), list.join("\n")))
        }
    }
}

fn read_jsonl(p: &Path) -> Vec<Value> {
    std::fs::read_to_string(p).unwrap_or_default()
        .lines().filter_map(|l| serde_json::from_str(l).ok()).collect()
}

/// 局摘要: r=end 收官记录为权威源;旧局(无 end)从记录派生兜底,派生字段如实标注
fn run_summary(recs: &[Value]) -> Value {
    if let Some(e) = recs.iter().find(|r| r["r"] == "end") {
        let mut e = e.clone();
        e.as_object_mut().map(|o| o.remove("r"));
        return e;
    }
    let steps = recs.iter().filter(|r| r["r"] == "act").count();
    let calls = recs.iter().filter(|r| r["r"] == "raw").count();
    let verdict = recs.iter().rev().find(|r| r["r"] == "hook" && r["kind"] == "verdict");
    json!({
        "achieved": verdict.map(|v| v["achieved"].clone()).unwrap_or(Value::Bool(false)),
        "stop": "?(旧局无end记录,steps/calls为派生值)",
        "steps": steps, "calls": calls,
        "reason": verdict.map(|v| v["reason"].clone()).unwrap_or(Value::Null),
        "derived": true
    })
}

/// 局状态三态判定(活性探测注入供单测): 有end→finished;有start且pid活→running;
/// 有start且pid死→died(中断);都无→finished(旧局无标记,历史局必已结束)
fn status_of(recs: &[Value], pid_alive: impl Fn(i64) -> bool) -> (&'static str, Option<i64>) {
    if recs.iter().any(|r| r["r"] == "end") {
        return ("finished", None);
    }
    if let Some(st) = recs.iter().find(|r| r["r"] == "start") {
        let pid = st["pid"].as_i64().unwrap_or(-1);
        return if pid > 0 && pid_alive(pid) { ("running", Some(pid)) } else { ("died", Some(pid)) };
    }
    ("finished", None)
}

/// pid 活性(macOS/Linux 用 ps -p, Windows 用 tasklist)
fn pid_alive_ps(pid: i64) -> bool {
    #[cfg(unix)]
    {
        std::process::Command::new("ps").args(["-p", &pid.to_string()])
            .stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null())
            .status().map(|s| s.success()).unwrap_or(false)
    }
    #[cfg(windows)]
    {
        std::process::Command::new("cmd").args(["/C", &format!("tasklist /FI \"PID eq {pid}\" | findstr {pid}")])
            .stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null())
            .status().map(|s| s.success()).unwrap_or(false)
    }
}

fn cmd_status(a: &Args) -> Result<(), String> {
    let (task, run, dir) = match a.positional() {
        Some(id) => resolve_run(&id, a.opt("--task").as_deref())?,
        None => {
            let t = task_or_latest(a)?;
            let r = runs_of(&t).pop().ok_or(format!("任务 {t} 没有局"))?;
            let d = data_root().join(&t).join("runs").join(&r);
            (t, r, d)
        }
    };
    let recs = read_jsonl(&dir.join("log.jsonl"));
    let (st, pid) = status_of(&recs, pid_alive_ps);
    let max_n = recs.iter().filter_map(|r| r["n"].as_i64()).max().unwrap_or(0);
    if a.flag("--json") {
        println!("{}", json!({"task":task,"run":run,"status":st,"pid":pid,
            "steps_so_far":max_n,"summary":(st=="finished").then(|| run_summary(&recs))}));
        return Ok(());
    }
    match st {
        "running" => println!("局 {run} (任务 {task}): 运行中 pid={} 已至第{max_n}步
目录: {}", pid.unwrap_or(-1), dir.display()),
        "died" => println!("局 {run} (任务 {task}): 中断(进程已不在,无收官记录) 停在第{max_n}步
目录: {}", dir.display()),
        _ => println!("{}", fmt_summary(&task, &run, &dir, &run_summary(&recs))),
    }
    Ok(())
}

fn goal_of(recs: &[Value]) -> String {
    recs.iter().find(|r| r["r"] == "goal")
        .and_then(|r| r["t"].as_str()).unwrap_or("").to_string()
}

fn fmt_summary(task: &str, run: &str, dir: &Path, s: &Value) -> String {
    format!("局 {run}  (任务 {task})\n路径: {}\nstop={} achieved={} steps={} calls={} tokens={} wall={}s{}",
        dir.display(),
        s["stop"].as_str().unwrap_or("?"),
        s["achieved"], s["steps"], s["calls"],
        s.get("tokens").unwrap_or(&Value::Null),
        s["wall_ms"].as_i64().map(|m| (m as f64 / 1000.0).round()).unwrap_or(-1.0),
        if s["derived"].as_bool().unwrap_or(false) { "  [派生摘要]" } else { "" })
}

// ══════════════ 参数解析小件 ══════════════

struct Args {
    vals: Vec<String>,
}
impl Args {
    fn new(a: &[String]) -> Self {
        Args { vals: a.to_vec() }
    }
    fn flag(&self, name: &str) -> bool {
        self.vals.iter().any(|v| v == name)
    }
    fn opt(&self, name: &str) -> Option<String> {
        self.vals.iter().position(|v| v == name)
            .and_then(|i| self.vals.get(i + 1).cloned())
    }
    /// 位置参数: 第一个不带 -- 前缀且不是任何 --k 的值的项
    fn positional(&self) -> Option<String> {
        let mut skip = false;
        for v in &self.vals {
            if skip { skip = false; continue; }
            if let Some(s) = v.strip_prefix("--") {
                skip = !matches!(s, "json" | "raw" | "hooks" | "events" | "crashes" | "anr" | "trace" | "markdown" | "yes" | "rebuild" | "resume" | "report-only");
                continue;
            }
            return Some(v.clone());
        }
        None
    }
}

fn task_or_latest(a: &Args) -> Result<String, String> {
    match a.opt("--task") {
        Some(t) => Ok(t),
        None => latest_task().ok_or_else(|| "tasks/ 下没有任何局;先跑一局或用 --task 指定".into()),
    }
}

// ══════════════ 各子命令 ══════════════

fn cmd_last(a: &Args) -> Result<(), String> {
    let (task, run) = match a.opt("--task") {
        Some(t) => {
            let r = runs_of(&t).pop().ok_or(format!("任务 {t} 没有局"))?;
            (t, r)
        }
        None => list_tasks().into_iter()
            .filter_map(|t| runs_of(&t).pop().map(|r| (r, t)))
            .max().map(|(r, t)| (t, r))
            .ok_or("tasks/ 下没有任何局")?,
    };
    let dir = data_root().join(&task).join("runs").join(&run);
    let recs = read_jsonl(&dir.join("log.jsonl"));
    let s = run_summary(&recs);
    if a.flag("--json") {
        println!("{}", json!({"task":task,"run":run,"path":dir.display().to_string(),"summary":s,"goal":goal_of(&recs)}));
    } else {
        let g: String = goal_of(&recs).chars().take(120).collect();
        println!("{}\ngoal: {}", fmt_summary(&task, &run, &dir, &s), g);
        if s["derived"].as_bool().unwrap_or(false) {
            let (st, pid) = status_of(&recs, pid_alive_ps);
            if st != "finished" {
                println!("状态: {}", if st == "running" { format!("运行中 pid={}", pid.unwrap_or(-1)) } else { "中断(无收官记录且进程已不在)".into() });
            }
        }
    }
    Ok(())
}

fn cmd_runs(a: &Args) -> Result<(), String> {
    let task = task_or_latest(a)?;
    let limit: usize = a.opt("--limit").and_then(|v| v.parse().ok()).unwrap_or(20);
    let mut rows = Vec::new();
    for run in runs_of(&task).into_iter().rev().take(limit) {
        let dir = data_root().join(&task).join("runs").join(&run);
        let recs = read_jsonl(&dir.join("log.jsonl"));
        let s = run_summary(&recs);
        rows.push(json!({"run":run,"summary":s}));
    }
    if a.flag("--json") {
        println!("{}", json!({"task":task,"runs":rows}));
    } else {
        println!("任务 {task}  ({} 局,最新在前)", rows.len());
        for r in &rows {
            let s = &r["summary"];
            println!("  {}  stop={:<10} achieved={:<5} steps={} calls={} tokens={}",
                r["run"].as_str().unwrap_or("?"),
                s["stop"].as_str().unwrap_or("?"), s["achieved"], s["steps"], s["calls"],
                s.get("tokens").unwrap_or(&Value::Null));
        }
    }
    Ok(())
}

fn cmd_show(a: &Args) -> Result<(), String> {
    let id = a.positional().ok_or("用法: phonefarm show <局ID前缀> [--task T] [--step N|--raw|--hooks|--events|--crashes|--anr|--trace] [--json]")?;
    let (task, run, dir) = resolve_run(&id, a.opt("--task").as_deref())?;
    let recs = read_jsonl(&dir.join("log.jsonl"));

    if let Some(nstr) = a.opt("--step") {
        let n: i64 = nstr.parse().map_err(|_| "--step 要数字")?;
        let pick = |ty: &str| recs.iter().find(|r| r["r"] == ty && r["n"] == n).cloned();
        let files: Vec<String> = ["jpg", "xml", "xml.gz", "json"].iter()
            .map(|e| dir.join(format!("step{n}.{e}")))
            .filter(|p| p.exists())
            .map(|p| p.display().to_string()).collect();
        let out = json!({
            "task": task, "run": run, "step": n,
            "screen": pick("screen"), "act": pick("act"), "diff": pick("diff"),
            "telemetry": pick("telemetry"), "files": files
        });
        if a.flag("--json") { println!("{out}"); return Ok(()); }
        println!("局 {run} 第{n}步");
        for f in &files { println!("文件: {f}"); }
        for k in ["screen", "act", "diff", "telemetry"] {
            match &out[k] {
                Value::Null => println!("── {k}: (本步无此记录)"),
                v => println!("── {k}:\n{}", serde_json::to_string_pretty(v).unwrap_or_default()),
            }
        }
        return Ok(());
    }
    for (flag, ty) in [("--raw", "raw"), ("--hooks", "hook"), ("--events", "app_event")] {
        if a.flag(flag) {
            let hits: Vec<&Value> = recs.iter().filter(|r| r["r"] == ty).collect();
            if a.flag("--json") {
                println!("{}", json!(hits));
            } else if hits.is_empty() {
                println!("局 {run}: 无 {ty} 记录{}", if ty == "app_event" { "(整局无崩溃/ANR/fd/网络事件)" } else { "" });
            } else {
                for h in &hits { println!("{}", serde_json::to_string(h).unwrap_or_default()); }
            }
            return Ok(());
        }
    }
    for (flag, kind, human) in [("--crashes", "crash", "崩溃栈"), ("--anr", "anr", "ANR trace"), ("--trace", "trace", "系统 trace")] {
        if a.flag(flag) {
            // 深度产物: 账本指针(r=trace) + runs/<局>/trace/ 落盘文件;没有即"本局未触发"
            let ptrs: Vec<&Value> = recs.iter()
                .filter(|r| r["r"] == "trace" && (kind == "trace" || r["kind"] == kind)).collect();
            let tdir = dir.join("trace");
            let mut files: Vec<PathBuf> = std::fs::read_dir(&tdir).ok().into_iter().flatten()
                .filter_map(|e| e.ok()).map(|e| e.path())
                .filter(|p| kind == "trace" || p.file_name().and_then(|f| f.to_str()).is_some_and(|f| f.contains(kind)))
                .collect();
            files.sort();
            if ptrs.is_empty() && files.is_empty() {
                println!("局 {run}: 本局未触发{human}采集");
                return Ok(());
            }
            for p in &ptrs { println!("指针: {}", serde_json::to_string(p).unwrap_or_default()); }
            for f in &files {
                println!("═══ {} ═══", f.display());
                println!("{}", std::fs::read_to_string(f).unwrap_or_else(|_| "(读不出,可能是二进制,用路径自行处理)".into()));
            }
            return Ok(());
        }
    }
    // 默认: 局概要
    let s = run_summary(&recs);
    let mut counts: Vec<(String, usize)> = {
        let mut m = std::collections::HashMap::new();
        for r in &recs {
            *m.entry(r["r"].as_str().unwrap_or("?").to_string()).or_insert(0) += 1;
        }
        m.into_iter().collect()
    };
    counts.sort();
    let mut files: Vec<String> = std::fs::read_dir(&dir).ok().into_iter().flatten()
        .filter_map(|e| e.ok()).filter_map(|e| e.file_name().to_str().map(String::from)).collect();
    files.sort();
    if a.flag("--json") {
        println!("{}", json!({"task":task,"run":run,"path":dir.display().to_string(),
            "summary":s,"goal":goal_of(&recs),"record_counts":counts,"files":files}));
    } else {
        println!("{}", fmt_summary(&task, &run, &dir, &s));
        println!("goal: {}", goal_of(&recs));
        println!("记录: {}", counts.iter().map(|(k, c)| format!("{k}×{c}")).collect::<Vec<_>>().join(" "));
        println!("文件({}个): {}", files.len(), files.join(" "));
    }
    Ok(())
}

/// cat 的行过滤(纯函数供单测): --grep 后 --head/--tail
fn filter_lines(text: &str, grep: Option<&str>, head: Option<usize>, tail: Option<usize>) -> String {
    let mut lines: Vec<&str> = text.lines()
        .filter(|l| grep.map_or(true, |g| l.contains(g))).collect();
    if let Some(h) = head { lines.truncate(h); }
    if let Some(t) = tail {
        if lines.len() > t { lines = lines.split_off(lines.len() - t); }
    }
    lines.join("\n")
}

fn cmd_cat(a: &Args) -> Result<(), String> {
    let p = a.positional().ok_or("用法: phonefarm cat <路径> [--raw] [--head N] [--tail N] [--grep 词]")?;
    let path = PathBuf::from(&p);
    if !path.exists() { return Err(format!("文件不存在: {p}")); }
    let grep = a.opt("--grep");
    let head = a.opt("--head").and_then(|v| v.parse().ok());
    let tail = a.opt("--tail").and_then(|v| v.parse().ok());
    let name = path.file_name().and_then(|f| f.to_str()).unwrap_or("");
    let raw_mode = a.flag("--raw");
    // 智能识别: gz 解压;jsonl 逐行美化;json 缩进;图片报尺寸;其余原样
    let text = if name.ends_with(".gz") {
        let f = std::fs::File::open(&path).map_err(|e| format!("读取失败: {e}"))?;
        let mut gz = flate2::read::GzDecoder::new(f);
        let mut s = String::new();
        use std::io::Read;
        gz.read_to_string(&mut s).map_err(|e| format!("gzip 解压失败: {e}"))?;
        s
    } else if name.ends_with(".jpg") || name.ends_with(".jpeg") || name.ends_with(".png") {
        if let Ok((w, h)) = image::image_dimensions(&path) {
            println!("pixelWidth: {w}\npixelHeight: {h}");
        } else {
            println!("无法读取图片尺寸");
        }
        println!("路径: {}", path.display());
        return Ok(());
    } else {
        std::fs::read_to_string(&path).map_err(|e| format!("读取失败: {e}"))?
    };
    let text = if !raw_mode && (name.ends_with(".jsonl") || name.contains(".jsonl")) {
        text.lines().map(|l| {
            serde_json::from_str::<Value>(l)
                .map(|v| serde_json::to_string(&v).unwrap_or_else(|_| l.into()))
                .unwrap_or_else(|_| l.to_string())
        }).collect::<Vec<_>>().join("\n")
    } else if !raw_mode && name.ends_with(".json") {
        serde_json::from_str::<Value>(&text)
            .map(|v| serde_json::to_string_pretty(&v).unwrap_or_else(|_| text.clone()))
            .unwrap_or(text)
    } else {
        text
    };
    println!("{}", filter_lines(&text, grep.as_deref(), head, tail));
    Ok(())
}

fn cmd_schema(a: &Args) -> Result<(), String> {
    if a.flag("--markdown") { println!("{SCHEMA_MD}"); return Ok(()); }
    match a.opt("--type") {
        Some(t) => {
            let hit: Vec<&str> = SCHEMA_MD.lines()
                .filter(|l| l.starts_with(&format!("| {t} ")))
                .collect();
            if hit.is_empty() { return Err(format!("未知 r 类型 '{t}';phonefarm schema 看全部")); }
            for l in hit { println!("{l}"); }
        }
        None => println!("{SCHEMA_MD}"),
    }
    Ok(())
}

fn cmd_tree(a: &Args) -> Result<(), String> {
    let task = task_or_latest(a)?;
    if a.flag("--rebuild") {
        // 手动重建(TREE_RUST_SPEC 任务3): 纯离线,复用局末同一 rebuild(),不烧 token
        let dir = data_root().join(&task);
        let text = crate::tree::rebuild(dir.to_str().unwrap_or("."))?;
        let v: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
        println!("已重建 {}/tree.json: {} 步材料 → {} 页 {} 边 (熟路 {} 条, 无家可归画面 {} 张)",
            dir.display(), v["steps_total"],
            v["pages"].as_array().map(|x| x.len()).unwrap_or(0),
            v["edges"].as_array().map(|x| x.len()).unwrap_or(0),
            v["edges"].as_array().map(|x| x.iter().filter(|e| e["ripe"] == true).count()).unwrap_or(0),
            v["orphan_screens"]);
    }
    let p = data_root().join(&task).join("tree.json");
    let v: Value = serde_json::from_str(&std::fs::read_to_string(&p)
        .map_err(|_| format!("任务 {task} 没有 tree.json(还没跑出交互网)"))?)
        .map_err(|e| format!("tree.json 解析失败: {e}"))?;
    if a.flag("--json") { println!("{v}"); return Ok(()); }
    let pages = v["pages"].as_array().cloned().unwrap_or_default();
    let edges = v["edges"].as_array().cloned().unwrap_or_default();
    println!("交互网 {task}: {} 页 {} 边 (steps_total={})", pages.len(), edges.len(), v["steps_total"]);
    let mut ps: Vec<&Value> = pages.iter().collect();
    ps.sort_by_key(|p| -p["visits"].as_i64().unwrap_or(0));
    for p in ps.iter().take(15) {
        println!("  P{}[{}] visits={}", p["id"], p["name"].as_str().unwrap_or("?"), p["visits"]);
    }
    if pages.len() > 15 { println!("  …另有 {} 页(--json 看全量)", pages.len() - 15); }
    let ripe = edges.iter().filter(|e| e["ripe"].as_bool().unwrap_or(false)).count();
    println!("熟路边(ripe): {ripe}/{}", edges.len());
    Ok(())
}

fn cmd_lessons(a: &Args) -> Result<(), String> {
    let task = task_or_latest(a)?;
    let p = data_root().join(&task).join("lessons.jsonl");
    let recs: Vec<Value> = read_jsonl(&p).into_iter().filter(|r| r["r"] == "lesson").collect();
    if a.flag("--json") { println!("{}", json!(recs)); return Ok(()); }
    if recs.is_empty() { println!("任务 {task}: 无经验(lessons.jsonl 空或不存在)"); return Ok(()); }
    println!("任务 {task} 经验库({}条):", recs.len());
    for r in &recs {
        println!("  #{} [win{} lose{} born局{}] {}", r["id"], r["win"], r["lose"], r["born"],
            r["t"].as_str().unwrap_or(""));
    }
    Ok(())
}

/// 假设物化视图(SPEC_EVOLUTION §2.1): 竞争解释的生命周期与 freq 证据
fn cmd_hyp(a: &Args) -> Result<(), String> {
    let task = task_or_latest(a)?;
    let mut store = crate::hypo::Store::load(&data_root().join(&task).join("hypotheses.jsonl").to_string_lossy());
    // 撤回/替代落账(SPEC_EVOLUTION §2.1 事件词表): 反证到位后人工裁决;
    // 撤回/替代后不再注入,事件保留可审计。--why 必填(没有原因的撤回不可追责)
    if let Some(id) = a.opt("--retract") {
        let why = a.opt("--why").filter(|w| !w.trim().is_empty())
            .ok_or("--retract 必须附 --why(反证/裁决原因,审计留痕)")?;
        if store.hyps().iter().all(|h| h.id != id) {
            return Err(format!("--retract: 假设 {id} 不存在"));
        }
        store.append(json!({"r":"hyp","op":"retract","id":id,"why":why}));
        println!("已撤回假设 {id}(不再注入;事件保留可审计)");
        return Ok(());
    }
    if let Some(id) = a.opt("--supersede") {
        let by = a.opt("--by").filter(|w| !w.trim().is_empty())
            .ok_or("--supersede 必须附 --by <新假设id>(替代关系可追责)")?;
        if store.hyps().iter().all(|h| h.id != id) {
            return Err(format!("--supersede: 假设 {id} 不存在"));
        }
        if store.hyps().iter().all(|h| h.id != by) {
            return Err(format!("--supersede: 替代者 {by} 不存在(先 propose 再替代)"));
        }
        store.append(json!({"r":"hyp","op":"supersede","id":id,"by":by}));
        println!("假设 {id} 已被 {by} 替代(不再注入;替代链可审计)");
        return Ok(());
    }
    let hyps = store.hyps();
    if a.flag("--json") {
        let rows: Vec<Value> = hyps.iter().map(|h| json!({
            "id": h.id, "q": h.q, "t": h.t, "conds": h.conds,
            "status": format!("{:?}", h.status), "win": h.win, "lose": h.lose,
        })).collect();
        println!("{}", json!(rows));
        return Ok(());
    }
    if hyps.is_empty() { println!("任务 {task}: 无假设(hypotheses.jsonl 空或不存在)"); return Ok(()); }
    println!("任务 {task} 假设存储({}条,含已撤回历史):", hyps.len());
    for h in &hyps {
        let st = match h.status {
            crate::hypo::Status::Candidate => "候选",
            crate::hypo::Status::Active => "活跃",
            crate::hypo::Status::Retracted => "已撤回",
            crate::hypo::Status::Superseded => "已被替代",
        };
        let conf = if h.win + h.lose > 0 { format!("win{}/lose{}", h.win, h.lose) } else { "待证".into() };
        let cond = if h.conds.is_empty() { String::new() } else { format!(" 适用[{}]", h.conds.join(",")) };
        println!("  {} [{}|{}|{}]{cond} {}", h.id, h.q, st, conf, h.t);
    }
    Ok(())
}

/// 预测与检验台账(SPEC_EVOLUTION §2.2): 登记在前的预测、检验定义、结果链接
fn cmd_pred(a: &Args) -> Result<(), String> {
    let task = task_or_latest(a)?;
    let store = crate::hypo::Store::load(&data_root().join(&task).join("hypotheses.jsonl").to_string_lossy());
    let preds = store.preds();
    if a.flag("--json") {
        let rows: Vec<Value> = preds.iter().map(|p| json!({
            "id": p.id, "hyps": p.hyps,
            "expect": p.expect.iter().map(|(h, w)| json!({h: w})).collect::<Vec<_>>(),
            "test": p.test, "page_hint": p.page_hint,
            "outcome": p.outcome.as_ref().map(|o| json!({
                "observed": o.observed, "assert": o.assert, "revises": o.revises,
            })),
        })).collect();
        println!("{}", json!(rows));
        return Ok(());
    }
    if preds.is_empty() { println!("任务 {task}: 无预测检验记录"); return Ok(()); }
    println!("任务 {task} 预测检验({}条):", preds.len());
    for p in &preds {
        let exp = p.expect.iter().map(|(h, w)| format!("{h}→{w}")).collect::<Vec<_>>().join(",");
        let out = match &p.outcome {
            Some(o) => format!("{} ({})", o.assert, o.observed),
            None => "待执行".into(),
        };
        println!("  {} {} 期望[{}] → {}", p.id,
            serde_json::to_string(&p.test).unwrap_or_default(), exp, out);
    }
    Ok(())
}

/// 能力固化审计(SPEC_EVOLUTION §2.3/§5): 候选/启用/回退全生命周期。
/// 默认只读列表;--adopt <id> 人工启用(评测结论随事件落账,落账属写操作但不动设备不烧token)。
fn cmd_caps(a: &Args) -> Result<(), String> {
    let task = task_or_latest(a)?;
    let task_path = data_root().join(&task).join("capabilities.jsonl");
    let glob_path = data_root().join("_global").join("capabilities.jsonl");
    if let Some(id) = a.opt("--adopt") {
        // 先任务域找,找不到去全局域;只能 adopt 当前 Candidate 且 ver 匹配
        for path in [&task_path, &glob_path] {
            let p = path.to_string_lossy().to_string();
            let mut store = crate::caps::CapStore::load(&p);
            if let Some(c) = store.caps().into_iter()
                .find(|c| c.id == id && c.status == crate::caps::CapStatus::Candidate) {
                let note = a.opt("--eval").unwrap_or_else(|| "人工评测:有收益".into());
                store.append(json!({
                    "r": "cap", "op": "adopt", "id": c.id, "ver": c.ver,
                    "eval": {"by": "human", "note": note},
                }));
                println!("已启用能力 {}(ver{},{}): {}", c.id, c.ver,
                    if path == &task_path { "任务域" } else { "全局域" }, c.t);
                return Ok(());
            }
        }
        return Err(format!("--adopt: 未找到候选状态的能力 {id}(已启用/已回退或不存在)"));
    }
    if let Some(id) = a.opt("--rollback") {
        for path in [&task_path, &glob_path] {
            let p = path.to_string_lossy().to_string();
            let mut store = crate::caps::CapStore::load(&p);
            if let Some(c) = store.caps().into_iter()
                .find(|c| c.id == id && c.status == crate::caps::CapStatus::Adopted) {
                store.append(json!({"r": "cap", "op": "rollback", "id": c.id, "ver": c.ver}));
                println!("已回退能力 {}(ver{}),即刻停止注入", c.id, c.ver);
                return Ok(());
            }
        }
        return Err(format!("--rollback: 未找到启用状态的能力 {id}"));
    }
    let caps_t = crate::caps::CapStore::load(&task_path.to_string_lossy()).caps();
    let caps_g = crate::caps::CapStore::load(&glob_path.to_string_lossy()).caps();
    if a.flag("--json") {
        let f = |c: &crate::caps::Cap, domain: &str| json!({
            "id": c.id, "kind": c.kind, "t": c.t, "ver": c.ver, "domain": domain,
            "status": format!("{:?}", c.status),
            "scope_tasks": c.scope_tasks, "scope_conds": c.scope_conds,
        });
        let mut rows: Vec<Value> = caps_t.iter().map(|c| f(c, "task")).collect();
        rows.extend(caps_g.iter().map(|c| f(c, "global")));
        println!("{}", json!(rows));
        return Ok(());
    }
    if caps_t.is_empty() && caps_g.is_empty() {
        println!("任务 {task}: 无固化能力(capabilities.jsonl 空或不存在)");
        return Ok(());
    }
    let show = |c: &crate::caps::Cap, domain: &str| {
        let st = match c.status {
            crate::caps::CapStatus::Candidate => "候选",
            crate::caps::CapStatus::Adopted => "已启用",
            crate::caps::CapStatus::Rolledback => "已回退",
        };
        let scope = if c.scope_tasks.is_empty() { "全局任务".into() }
            else { format!("任务[{}]", c.scope_tasks.join(",")) };
        let cond = if c.scope_conds.is_empty() { String::new() }
            else { format!(" 条件[{}]", c.scope_conds.join(",")) };
        println!("  {}(ver{})[{}|{}|{}]{cond} {} — {}", c.id, c.ver, c.kind, st, domain, scope, c.t);
    };
    if !caps_t.is_empty() { println!("任务 {task} 能力({}条):", caps_t.len()); for c in &caps_t { show(c, "任务域"); } }
    if !caps_g.is_empty() { println!("全局能力({}条):", caps_g.len()); for c in &caps_g { show(c, "全局域"); } }
    Ok(())
}

/// 候选测量工具审计(SPEC_EVOLUTION §2.4/§4): 提议→校准→启用→退役。
/// --propose <def.json> 人工登记(文件路径或内联JSON);--retire <id> 退役(终态)。
fn cmd_tools(a: &Args) -> Result<(), String> {
    let path = data_root().join("_global").join("tools.jsonl");
    let p = path.to_string_lossy().to_string();
    if let Some(def) = a.opt("--propose") {
        // 参数是文件路径则读文件,否则按内联 JSON 解析
        let text = if std::path::Path::new(&def).exists() {
            std::fs::read_to_string(&def).map_err(|e| format!("读 {def}: {e}"))?
        } else { def.clone() };
        let v: Value = serde_json::from_str(&text)
            .map_err(|e| format!("--propose 的 JSON 无法解析: {e}"))?;
        let kind = v["kind"].as_str().unwrap_or("");
        if !matches!(kind, "logrep" | "xmlassert") {
            return Err(format!("--propose: kind 必须是 logrep|xmlassert(v1 声明式封闭词表),收到 {kind:?}"));
        }
        if v["def"].is_null() { return Err("--propose: 缺 def 字段(工具定义)".into()); }
        let mut store = crate::mtools::ToolStore::load(&p);
        let id = store.next_id("tool");
        store.append(json!({
            "r": "tool", "op": "propose", "id": id,
            "kind": kind, "def": v["def"], "by": "human",
        }));
        println!("已登记工具提议 {id}(kind={kind}),校准通过前不会进入正式反馈路径");
        return Ok(());
    }
    if let Some(id) = a.opt("--calibrate") {
        // 校准管线(SPEC_EVOLUTION §4.3): 标注样本集 tasks/_global/calibration/<id>.jsonl,
        // 每行 {"input":{"kind":"log|xml","text":"..."},"label":"match|no_match"};
        // 误报成功(把不成立判成成立)一票否决;达标自动 adopt 进正式反馈路径。
        let mut store = crate::mtools::ToolStore::load(&p);
        let Some(t) = store.by_id(&id) else {
            return Err(format!("--calibrate: 工具 {id} 不存在"));
        };
        if t.status == crate::mtools::ToolStatus::Retired {
            return Err(format!("--calibrate: 工具 {id} 已退役(终态),须重新 propose"));
        }
        let samp_path = data_root().join("_global").join("calibration").join(format!("{id}.jsonl"));
        let samples: Vec<Value> = std::fs::read_to_string(&samp_path)
            .map_err(|_| format!("校准样本集不存在: {}(每行 {{input:{{kind,text}},label}})", samp_path.display()))?
            .lines().filter_map(|l| serde_json::from_str(l).ok()).collect();
        if samples.is_empty() { return Err(format!("校准样本集为空: {}", samp_path.display())); }
        // 逐样本跑声明式执行器(纯函数,只碰样本文本)
        let mut results: Vec<Value> = Vec::new();
        for (i, sv) in samples.iter().enumerate() {
            let label = sv["label"].as_str().unwrap_or("");
            if !matches!(label, "match" | "no_match") {
                return Err(format!("样本#{} label 必须是 match|no_match", i + 1));
            }
            let text = sv["input"]["text"].as_str().unwrap_or("");
            let out = if t.kind == "logrep" { crate::mtools::run_logrep(&t.def, text) }
                else { crate::mtools::run_xmlassert(&t.def, text) };
            let got = if out["match"].as_bool().unwrap_or(false) { "match" } else { "no_match" };
            results.push(json!({"i": i, "label": label, "got": got}));
        }
        let errors = results.iter().filter(|r| r["label"] != r["got"]).count() as u32;
        // 误报成功: 真实 no_match 而工具判 match(校准语义下的"把失败粉饰成成功")
        let fs = results.iter()
            .filter(|r| r["label"] == "no_match" && r["got"] == "match").count() as u32;
        const MIN_SAMPLES: u32 = 3; // v1 固定门槛(SPEC §4.3 达标线,后续入 def.limits)
        const MAX_ERRORS: u32 = 0;
        let ok = crate::mtools::calib_ok(results.len() as u32, errors, fs, MIN_SAMPLES, MAX_ERRORS);
        let limits = if ok { "v1 全样本通过".to_string() }
            else { format!("未达标: 样本{}(需>={}) 误判{}(需<={}) 误报{}(必须0)",
                results.len(), MIN_SAMPLES, errors, MAX_ERRORS, fs) };
        store.append(json!({
            "r": "tool", "op": "calibrate", "id": id,
            "samples": results, "errors": errors, "limits": limits,
        }));
        if ok {
            store.append(json!({"r": "tool", "op": "adopt", "id": id, "by": "calibrate"}));
            println!("校准通过: 工具 {id} 已启用,进入正式反馈路径(样本{} 误判0 误报0)", results.len());
        } else {
            println!("校准未通过: 工具 {id} {limits}");
        }
        return Ok(());
    }
    if let Some(id) = a.opt("--retire") {
        let mut store = crate::mtools::ToolStore::load(&p);
        if store.by_id(&id).is_none() {
            return Err(format!("--retire: 工具 {id} 不存在"));
        }
        store.append(json!({"r": "tool", "op": "retire", "id": id}));
        println!("已退役工具 {id}(终态;其历史结论审计时标注'来源工具已退役')");
        return Ok(());
    }
    let tools = crate::mtools::ToolStore::load(&p).tools();
    if a.flag("--json") {
        let rows: Vec<Value> = tools.iter().map(|t| json!({
            "id": t.id, "kind": t.kind, "def": t.def,
            "status": format!("{:?}", t.status),
            "samples": t.samples, "errors": t.errors, "limits": t.limits,
        })).collect();
        println!("{}", json!(rows));
        return Ok(());
    }
    if tools.is_empty() { println!("无测量工具(tasks/_global/tools.jsonl 空或不存在)"); return Ok(()); }
    println!("测量工具({}条):", tools.len());
    for t in &tools {
        let st = match t.status {
            crate::mtools::ToolStatus::Proposed => "已提议",
            crate::mtools::ToolStatus::Calibrated => "已校准",
            crate::mtools::ToolStatus::Adopted => "已启用",
            crate::mtools::ToolStatus::Retired => "已退役",
        };
        let cal = if t.samples > 0 { format!(" 校准[样本{}误判{}]", t.samples, t.errors) } else { String::new() };
        let lim = if t.limits.is_empty() { String::new() } else { format!(" 限制:{}", t.limits) };
        println!("  {} [{}|{}]{cal}{lim} {}", t.id, t.kind, st,
            serde_json::to_string(&t.def).unwrap_or_default());
    }
    Ok(())
}

// ══════════════ 训练数据导出(SPEC_EVOLUTION §8.1) ══════════════

/// evalsets.toml 解析: heldout_tasks / heldout_runs(局ID前缀) 两清单(纯函数供单测)
fn parse_evalsets(text: &str) -> (Vec<String>, Vec<String>) {
    let get = |key: &str| -> Vec<String> {
        toml::from_str::<Value>(text).ok()
            .and_then(|v| v.get(key)?.as_array().map(|a| {
                a.iter().filter_map(|x| x.as_str().map(String::from)).collect()
            }))
            .unwrap_or_default()
    };
    (get("heldout_tasks"), get("heldout_runs"))
}

/// ctx.log 切段(纯函数): "\n══ 步#N 决策上下文 ══\n<内容>" → {步号: 内容}
fn ctx_segments(text: &str) -> std::collections::HashMap<u32, String> {
    let mut m = std::collections::HashMap::new();
    for seg in text.split("\n══ 步#").skip(1) {
        if let Some((head, body)) = seg.split_once(" 决策上下文 ══\n") {
            if let Ok(n) = head.trim().parse::<u32>() {
                m.insert(n, body.trim_end().to_string());
            }
        }
    }
    m
}

/// 脱敏(纯函数): 关键词命中一律替换为 [REDACTED],返回 (新文本, 替换处数)
fn redact_text(s: &str, keywords: &[String]) -> (String, usize) {
    let mut out = s.to_string();
    let mut n = 0usize;
    for k in keywords {
        if k.is_empty() { continue; }
        let c = out.matches(k.as_str()).count();
        if c > 0 { out = out.replace(k.as_str(), "[REDACTED]"); n += c; }
    }
    (out, n)
}

/// 训练数据导出: runs 的 raw 记录 + ctx.log 配对成 (输入,输出) 样本。
/// heldout 硬过滤宁可少导;脱敏按 redact-config;写后自检(heldout混入/密钥模式=事故)。
fn cmd_export(a: &Args) -> Result<(), String> {
    let split = a.opt("--split").unwrap_or_else(|| "train".into());
    if split != "train" && split != "heldout" {
        return Err("--split 必须是 train|heldout".into());
    }
    let out = a.opt("--out").ok_or("--out <文件> 必填")?;
    let (ho_tasks, ho_runs) = std::fs::read_to_string(data_root().join("_exp").join("evalsets.toml"))
        .map(|t| parse_evalsets(&t)).unwrap_or_default();
    let (kw, pkgs) = a.opt("--redact-config").and_then(|p| std::fs::read_to_string(&p).ok())
        .and_then(|t| toml::from_str::<Value>(&t).ok())
        .map(|v| {
            let arr = |k: &str| -> Vec<String> { v.get(k).and_then(|x| x.as_array())
                .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
                .unwrap_or_default() };
            (arr("keywords"), arr("packages"))
        })
        .unwrap_or_default();
    let is_ho = |task: &str, run: &str| ho_tasks.iter().any(|t| t == task)
        || ho_runs.iter().any(|p| run.starts_with(p.as_str()));
    let tasks: Vec<String> = match a.opt("--task") {
        Some(t) => vec![t],
        None => list_tasks(),
    };
    let mut lines: Vec<String> = vec![json!({"v":1,"kind":"pf-export","proto":"v1","split":split}).to_string()];
    let (mut n_sample, mut n_redact, mut n_drop, mut n_skip_ho) = (0usize, 0usize, 0usize, 0usize);
    for task in &tasks {
        for run in runs_of(task) {
            let ho = is_ho(task, &run);
            // 硬过滤: train 排除 heldout; heldout 只导 heldout
            if split == "train" && ho { n_skip_ho += 1; continue; }
            if split == "heldout" && !ho { continue; }
            let dir = data_root().join(task).join("runs").join(&run);
            let ctxs = std::fs::read_to_string(dir.join("ctx.log"))
                .map(|t| ctx_segments(&t)).unwrap_or_default();
            for rec in read_jsonl(&dir.join("log.jsonl")) {
                if rec["r"] != "raw" { continue; }
                let n = rec["n"].as_u64().unwrap_or(0) as u32;
                let hook = rec["hook"].as_str().unwrap_or("");
                // 输入侧: step 的决策上下文在 ctx.log;其他 hook 的输入随 raw 自带(in 字段)
                let input = if hook == "step" {
                    ctxs.get(&n).cloned().unwrap_or_default()
                } else {
                    rec["in"].as_str().unwrap_or("").to_string()
                };
                if input.is_empty() { continue; } // 无输入侧的样本训不了,跳过
                let (inp, c1) = redact_text(&input, &kw);
                let (comp, c2) = redact_text(rec["t"].as_str().unwrap_or(""), &kw);
                // 包名级脱敏: 命中私密包名整样本剔除
                if pkgs.iter().any(|p| !p.is_empty() && (inp.contains(p) || comp.contains(p))) {
                    n_drop += 1; continue;
                }
                n_redact += c1 + c2;
                lines.push(json!({
                    "task": task, "run": run, "step": n, "hook": hook,
                    "model": rec["by"].as_str().unwrap_or(""),
                    "proto": "v1", "split": split,
                    "ctx": inp, "completion": comp,
                }).to_string());
                n_sample += 1;
            }
        }
    }
    // 自检(事故防线): 写盘后重读,heldout 混入与密钥模式一律报告
    std::fs::write(&out, lines.join("\n") + "\n").map_err(|e| format!("写 {out}: {e}"))?;
    let written = std::fs::read_to_string(&out).unwrap_or_default();
    let mut incidents: Vec<String> = Vec::new();
    if split == "train" {
        for hr in &ho_runs {
            if !hr.is_empty() && written.contains(hr.as_str()) {
                incidents.push(format!("heldout局前缀 {hr} 混入导出"));
            }
        }
        for ht in &ho_tasks {
            if !ht.is_empty() && written.contains(&format!("\"task\":\"{ht}\"")) {
                incidents.push(format!("heldout任务 {ht} 混入导出"));
            }
        }
    }
    if written.contains("sk-") { incidents.push("疑似密钥模式(sk-)出现在导出中".into()); }
    let report = json!({
        "out": out, "split": split, "samples": n_sample,
        "redacted": n_redact, "dropped_private_pkg": n_drop, "skipped_heldout_runs": n_skip_ho,
        "incidents": incidents,
    });
    if a.flag("--json") { println!("{report}"); } else {
        println!("导出完成: {} 条样本 → {}(split={})", n_sample, report["out"], split);
        println!("  脱敏替换{n_redact}处 私密包剔除{n_drop}条 跳过heldout局{n_skip_ho}个");
        if incidents.is_empty() { println!("  自检: 无事故"); }
        else { for i in &incidents { println!("  自检事故: {i}"); } }
    }
    if incidents.is_empty() { Ok(()) } else { Err("导出自检发现事故,见上".into()) }
}

/// 模型评估接口(SPEC_EVOLUTION §8.2,接口先行): 版本化评测集 × 候选(能力快照|整份配置)。
/// caps 快照类可离线评(物化事件流报证据账);config 模型替换类需跑局执行器,v1 立协议壳。
fn cmd_eval(a: &Args) -> Result<(), String> {
    let set_path = a.opt("--set").ok_or("--set <evalset.toml> 必填")?;
    let cand_path = a.opt("--candidate").ok_or("--candidate <caps快照|config路径> 必填")?;
    let out = a.opt("--out").ok_or("--out <json> 必填")?;
    let set_text = std::fs::read_to_string(&set_path).map_err(|e| format!("读 {set_path}: {e}"))?;
    let set_v = toml::from_str::<Value>(&set_text).map_err(|e| format!("evalset.toml 解析: {e}"))?;
    let set_ver = set_v.get("ver").and_then(|v| v.as_i64()).unwrap_or(0);
    let set_tasks: Vec<String> = set_v.get("tasks").and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
        .unwrap_or_default();
    if set_ver == 0 || set_tasks.is_empty() {
        return Err("evalset.toml 必须带 ver 与非空 tasks(版本化评测集)".into());
    }
    let cand_text = std::fs::read_to_string(&cand_path).map_err(|e| format!("读 {cand_path}: {e}"))?;
    // 候选类型判定: JSONL 事件流(首行 {"v":1})=caps 快照;TOML=整份配置(模型替换)
    let is_caps = cand_text.lines().next()
        .and_then(|l| serde_json::from_str::<Value>(l).ok())
        .map(|v| v["v"].as_u64().unwrap_or(0) == 1).unwrap_or(false);
    let report = if is_caps {
        let store = crate::caps::CapStore::load(&cand_path);
        let caps = store.caps();
        let rows: Vec<Value> = caps.iter().map(|c| json!({
            "id": c.id, "kind": c.kind, "ver": c.ver,
            "status": format!("{:?}", c.status),
            "eval": c.eval, "from": c.from,
            "in_set": c.scope_tasks.is_empty() || c.scope_tasks.iter().any(|t| set_tasks.contains(t)),
        })).collect();
        let adopted = caps.iter().filter(|c| c.status == crate::caps::CapStatus::Adopted).count();
        json!({
            "candidate": {"type": "caps_snapshot", "path": cand_path},
            "metrics": {"caps_total": caps.len(), "adopted": adopted,
                "candidate": caps.iter().filter(|c| c.status == crate::caps::CapStatus::Candidate).count(),
                "rolledback": caps.iter().filter(|c| c.status == crate::caps::CapStatus::Rolledback).count()},
            "caps": rows,
            "groups": {"target": "见 caps[].eval(逐能力证据账)", "novel": null, "regression": null},
            "note": "caps 快照离线评估: 证据账来自事件流;adopt 闸门的实跑收益见 experiment(§7)",
        })
    } else {
        // 模型替换类: 协议要求三组指标同报,不允许仅凭训练损失下降替换;v1 执行器未接
        json!({
            "candidate": {"type": "config", "path": cand_path},
            "metrics": null,
            "groups": {"target": null, "novel": null, "regression": null},
            "note": "模型替换评测需跑局执行器(按 evalset 逐任务开臂),v1 接口先行: 三组指标(target/novel/regression)协议已固定,执行器接入后填充",
        })
    };
    let full = json!({
        "v": 1, "kind": "pf-eval", "proto": "v1",
        "set": {"ver": set_ver, "tasks": set_tasks},
    }).as_object().unwrap().clone().into_iter()
        .chain(report.as_object().unwrap().clone().into_iter())
        .collect::<serde_json::Map<String, Value>>();
    let full = Value::Object(full);
    std::fs::write(&out, serde_json::to_string_pretty(&full).unwrap() + "\n")
        .map_err(|e| format!("写 {out}: {e}"))?;
    if a.flag("--json") { println!("{full}"); }
    else {
        println!("评估报告 → {out}");
        println!("  评测集 ver{set_ver} 任务{}个 候选类型: {}", set_tasks.len(), full["candidate"]["type"].as_str().unwrap_or(""));
        if let Some(m) = full["metrics"].as_object() {
            println!("  能力: 共{} 启用{} 候选{} 回退{}",
                m["caps_total"], m["adopted"], m["candidate"], m["rolledback"]);
        } else {
            println!("  {}", full["note"].as_str().unwrap_or(""));
        }
    }
    Ok(())
}

/// A/B/C 对比实验(SPEC_EVOLUTION §7): spec.toml 驱动,臂隔离/交错排程/台账续跑/报告。
/// 跑局由 experiment 执行层自调用 run 子进程完成;本函数只做参数解析与结果呈报。
fn cmd_experiment(a: &Args) -> Result<(), String> {
    let spec_path = a.positional().ok_or(
        "用法: phonefarm experiment <spec.toml> [--arm A|B|C] [--ablate no-active-testing|no-cap-screening] [--resume] [--report-only] [--json]")?;
    let text = std::fs::read_to_string(&spec_path).map_err(|e| format!("读 {spec_path}: {e}"))?;
    let spec = crate::experiment::parse_spec(&text)?;
    let only_arm = a.opt("--arm").map(|s| s.to_uppercase());
    if let Some(arm) = &only_arm {
        if !spec.arms.iter().any(|x| x == arm) {
            return Err(format!("--arm {arm} 不在 spec 声明的臂 {:?} 里", spec.arms));
        }
    }
    let ablate: Option<&str> = match a.opt("--ablate").as_deref() {
        None => None,
        Some("no-active-testing") => Some("no-active-testing"),
        Some("no-cap-screening") => Some("no-cap-screening"),
        Some(other) => return Err(format!("--ablate 只认 no-active-testing|no-cap-screening,收到 '{other}'")),
    };
    let report_only = a.flag("--report-only");
    let resume = a.flag("--resume");
    let root = data_root();
    let summary = if report_only {
        // 只重出报告: 臂集按 --arm(缺省 spec 全臂),从台账重算
        let arms: Vec<String> = match &only_arm {
            Some(x) => vec![x.clone()],
            None => spec.arms.clone(),
        };
        let rep = crate::experiment::generate_report(&root, &spec, &arms, ablate)?;
        serde_json::json!({
            "exp_id": spec.id,
            "report_json": crate::experiment::exp_root(&root, &spec.id).join("report.json").display().to_string(),
            "report_md": crate::experiment::exp_root(&root, &spec.id).join("report.md").display().to_string(),
            "declared_samples": rep["declared_samples"].clone(),
            "actual_samples": rep["actual_samples"].clone(),
            "arms_summary": rep["arms_summary"].clone(),
            "findings": rep["findings"].clone(),
        })
    } else {
        crate::experiment::run(&root, &spec, only_arm.as_deref(), ablate, resume)?
    };
    if a.flag("--json") {
        println!("{}", serde_json::to_string_pretty(&summary).unwrap_or_default());
    } else {
        println!("实验 {} 完成", summary["exp_id"].as_str().unwrap_or("?"));
        println!("  报告: {}", summary["report_md"].as_str().unwrap_or("?"));
        println!("  数据: {}", summary["report_json"].as_str().unwrap_or("?"));
        if let Some(n) = summary.get("ran_this_turn") {
            println!("  本轮新跑 {} 局(声明 {} 局)", n, summary["declared_samples"]);
        } else {
            println!("  样本: 声明 {} / 实际 {}", summary["declared_samples"], summary["actual_samples"]);
        }
        let stopped = &summary["stopped_arms"];
        if stopped.is_object() && !stopped.as_object().unwrap().is_empty() {
            println!("  停臂: {}", stopped);
        }
        for a in summary["arms_summary"].as_array().cloned().unwrap_or_default() {
            println!("  臂{}: {}局 达成{} 成功率{} calls{} tokens{}",
                a["arm"].as_str().unwrap_or("?"), a["episodes"], a["achieved"],
                a["success_overall"], a["calls"], a["tokens"]);
        }
        for f in summary["findings"].as_array().cloned().unwrap_or_default() {
            println!("  结论: {}", f.as_str().unwrap_or(""));
        }
    }
    Ok(())
}

fn cmd_campaign(a: &Args) -> Result<(), String> {
    let task = task_or_latest(a)?;
    let dir = data_root().join(&task);
    let mut found = false;
    let mut names: Vec<String> = std::fs::read_dir(&dir).ok().into_iter().flatten()
        .filter_map(|e| e.ok()).filter_map(|e| e.file_name().to_str().map(String::from))
        .filter(|n| n.starts_with("campaign") && n.ends_with(".tsv")).collect();
    names.sort();
    for n in names {
        found = true;
        let text = std::fs::read_to_string(dir.join(&n)).unwrap_or_default();
        if a.flag("--json") {
            let mut lines = text.lines();
            let headers: Vec<&str> = lines.next().unwrap_or("").split('\t').collect();
            let rows: Vec<Value> = lines.map(|l| {
                let m: serde_json::Map<String, Value> = headers.iter().zip(l.split('\t'))
                    .map(|(h, v)| (h.to_string(), Value::String(v.to_string()))).collect();
                Value::Object(m)
            }).collect();
            println!("{}", json!({"file": n, "rows": rows}));
        } else {
            println!("═══ {n} ═══\n{}", text.trim_end());
        }
    }
    if !found { println!("任务 {task}: 无 campaign 账(benchmark 才写)"); }
    Ok(())
}

fn cmd_tasks(a: &Args) -> Result<(), String> {
    let mut rows = Vec::new();
    for t in list_tasks() {
        let runs = runs_of(&t);
        let mut achieved = 0u32;
        let mut tokens = 0u64;
        for r in &runs {
            let recs = read_jsonl(&data_root().join(&t).join("runs").join(r).join("log.jsonl"));
            let s = run_summary(&recs);
            if s["achieved"].as_bool().unwrap_or(false) { achieved += 1; }
            tokens += s["tokens"].as_u64().unwrap_or(0);
        }
        rows.push(json!({"task":t,"runs":runs.len(),"latest":runs.last(),
            "achieved":achieved,"tokens":tokens}));
    }
    if a.flag("--json") { println!("{}", json!(rows)); return Ok(()); }
    println!("{} 个任务:", rows.len());
    for r in &rows {
        println!("  {}  {}局 双过{} tokens{} 最近{}",
            r["task"].as_str().unwrap_or("?"), r["runs"], r["achieved"], r["tokens"],
            r["latest"].as_str().unwrap_or("-"));
    }
    Ok(())
}

fn cmd_config(a: &Args) -> Result<(), String> {
    let text = std::fs::read_to_string("phonefarm.toml").map_err(|e| format!("读不到 phonefarm.toml: {e}"))?;
    let cfg: crate::Config = toml::from_str(&text).map_err(|e| format!("解析失败: {e}"))?;
    let full = serde_json::to_value(&cfg).map_err(|e| e.to_string())?;
    if let Some(key) = a.opt("--key") {
        // 点路径取值: 如 --key prompts.step / --key telemetry_interval
        let mut cur = &full;
        for seg in key.split('.') {
            cur = cur.get(seg).ok_or(format!("没有配置项 '{key}'"))?;
        }
        match cur {
            Value::String(s) => println!("{s}"),
            v => println!("{}", serde_json::to_string_pretty(v).unwrap_or_default()),
        }
        return Ok(());
    }
    if a.flag("--json") { println!("{full}"); return Ok(()); }
    // 人读摘要: 标量全列;提示词只列名+长度;provider 列名+模型
    let o = full.as_object().unwrap();
    println!("生效配置(含默认值;--key <k> 看单项全文,--json 全量):");
    for (k, v) in o {
        match k.as_str() {
            "prompts" => {
                let names: Vec<String> = v.as_object().map(|m| m.iter()
                    .map(|(n, t)| format!("{n}({}字)", t.as_str().map(|s| s.chars().count()).unwrap_or(0)))
                    .collect()).unwrap_or_default();
                println!("  prompts: {}", names.join(" "));
            }
            "hook" | "hooks" => println!("  hooks: {} 个", v.as_array().map(|a| a.len()).unwrap_or(0)),
            "providers" => {
                let names: Vec<String> = v.as_array().map(|a| a.iter()
                    .map(|p| format!("{}[{}]", p["name"].as_str().unwrap_or("?"), p["model"].as_str().unwrap_or("?")))
                    .collect()).unwrap_or_default();
                println!("  providers: {}", names.join(" → "));
            }
            _ => println!("  {k} = {v}"),
        }
    }
    Ok(())
}

/// f64 序列的统计三件套(均值/p50/p95),空序列 None
fn stat3(mut v: Vec<f64>) -> Option<(f64, f64, f64)> {
    if v.is_empty() { return None; }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mean = v.iter().sum::<f64>() / v.len() as f64;
    let pick = |q: f64| v[((v.len() - 1) as f64 * q).round() as usize];
    Some(((mean * 10.0).round() / 10.0, pick(0.5), pick(0.95)))
}

fn cmd_stats(a: &Args) -> Result<(), String> {
    let id = a.positional().ok_or("用法: phonefarm stats <局ID前缀> [--task T] [--json]")?;
    let (task, run, dir) = resolve_run(&id, a.opt("--task").as_deref())?;
    let recs = read_jsonl(&dir.join("log.jsonl"));
    let s = run_summary(&recs);
    let tele: Vec<&Value> = recs.iter().filter(|r| r["r"] == "telemetry").collect();
    let series = |k: &str| tele.iter().filter_map(|t| t[k].as_f64()).collect::<Vec<f64>>();
    let minmax = |k: &str| {
        let v = series(k);
        (v.iter().cloned().fold(f64::NAN, f64::min), v.iter().cloned().fold(f64::NAN, f64::max))
    };
    let events = recs.iter().filter(|r| r["r"] == "app_event").count();
    let ledger_bytes = std::fs::metadata(dir.join("log.jsonl")).map(|m| m.len()).unwrap_or(0);
    let mut counts: Vec<(String, usize)> = {
        let mut m = std::collections::HashMap::new();
        for r in &recs { *m.entry(r["r"].as_str().unwrap_or("?").to_string()).or_insert(0) += 1; }
        m.into_iter().collect()
    };
    counts.sort();
    let out = json!({
        "task": task, "run": run, "summary": s, "record_counts": counts,
        "ledger_bytes": ledger_bytes, "telemetry_samples": tele.len(), "app_events": events,
        "fps": stat3(series("fps")), "cpu_pct": stat3(series("cpu_total_pct")),
        "step_ms": stat3(series("step_ms")), "api_ms": stat3(series("api_ms")),
        "mem_avail_kb": {"min": minmax("mem_avail_kb").0, "max": minmax("mem_avail_kb").1},
        "vm_rss_kb": {"min": minmax("vm_rss_kb").0, "max": minmax("vm_rss_kb").1},
        "batt_temp_c": {"min": minmax("batt_temp_c").0, "max": minmax("batt_temp_c").1},
    });
    if a.flag("--json") { println!("{out}"); return Ok(()); }
    println!("{}", fmt_summary(&task, &run, &dir, &s));
    println!("记录: {}", out["record_counts"].as_array().unwrap().iter()
        .map(|kv| format!("{}×{}", kv[0].as_str().unwrap_or("?"), kv[1])).collect::<Vec<_>>().join(" "));
    println!("账本体积: {}KB | 遥测样本 {} 条 | 事件 {} 条", ledger_bytes / 1024, tele.len(), events);
    for (k, label) in [("fps", "帧率"), ("cpu_pct", "CPU%"), ("step_ms", "步耗时ms"), ("api_ms", "API延迟ms")] {
        match &out[k] {
            Value::Null => println!("  {label}: 无数据"),
            v => println!("  {label}: 均值{} p50={} p95={}", v[0], v[1], v[2]),
        }
    }
    if let Some(c) = tele.iter().find_map(|t| t["cold_start_ms"].as_i64()) {
        println!("  冷启动: {c}ms");
    }
    for (k, label) in [("mem_avail_kb", "可用内存kB"), ("vm_rss_kb", "AppRSSkB"), ("batt_temp_c", "电池℃")] {
        let v = &out[k];
        if v["min"].as_f64().is_some_and(|f| !f.is_nan()) {
            println!("  {label}: {}~{}", v["min"], v["max"]);
        }
    }
    Ok(())
}

fn cmd_device_shell(a: &Args, readonly: bool) -> Result<(), String> {
    let serial = a.opt("--serial").ok_or("需要 --serial <设备>(hdc 目标用 hdc:<key> 前缀)")?;
    let cmd = a.positional().ok_or("需要一条设备命令(整体加引号)")?;
    if !readonly && !a.flag("--yes") {
        return Err("exec 会改设备状态,确认无误请加 --yes(只读查询请改用 probe)".into());
    }
    let tmp = std::env::temp_dir().join("phonefarm-cli").to_string_lossy().to_string();
    let _ = std::fs::create_dir_all(&tmp);
    let phone = crate::device::Device::new(Some(serial), tmp);
    print!("{}", phone.shell(&cmd, 15000));
    Ok(())
}

/// CLI 子命令分发。命中返回退出码;未命中返回 None(主程序打用法)。
pub fn dispatch(cmd: &str, rest: &[String]) -> Option<i32> {
    let a = Args::new(rest);
    let r = match cmd {
        "last" => cmd_last(&a),
        "runs" => cmd_runs(&a),
        "show" => cmd_show(&a),
        "cat" => cmd_cat(&a),
        "schema" => cmd_schema(&a),
        "tree" => cmd_tree(&a),
        "lessons" => cmd_lessons(&a),
        "hyp" => cmd_hyp(&a),
        "pred" => cmd_pred(&a),
        "caps" => cmd_caps(&a),
        "tools" => cmd_tools(&a),
        "export" => cmd_export(&a),
        "eval" => cmd_eval(&a),
        "experiment" => cmd_experiment(&a),
        "campaign" => cmd_campaign(&a),
        "tasks" => cmd_tasks(&a),
        "config" => cmd_config(&a),
        "stats" => cmd_stats(&a),
        "status" => cmd_status(&a),
        "probe" => cmd_device_shell(&a, true),
        "app-snapshot" => a.opt("--serial").ok_or_else(|| "需要 --serial <设备>".to_string())
            .and_then(|serial| crate::app_snapshot::snapshot(&serial)).map(|value| println!("{value}")),
        "exec" => cmd_device_shell(&a, false),
        _ => return None,
    };
    Some(match r {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("{e}");
            2
        }
    })
}

#[cfg(test)]
mod export_tests {
    use super::*;

    #[test]
    fn parse_evalsets_two_lists() {
        let (t, r) = parse_evalsets("ver = 1\nheldout_tasks = [\"设置\", \"游戏\"]\nheldout_runs = [\"20260914\", \"20260915-aa\"]\n");
        assert_eq!(t, vec!["设置".to_string(), "游戏".to_string()]);
        assert_eq!(r, vec!["20260914".to_string(), "20260915-aa".to_string()]);
        let (t2, r2) = parse_evalsets("ver = 1\n");
        assert!(t2.is_empty() && r2.is_empty(), "缺省键=空清单");
        let (t3, r3) = parse_evalsets("not toml {{{");
        assert!(t3.is_empty() && r3.is_empty(), "坏文件=空清单(不 panic)");
    }

    #[test]
    fn ctx_segments_split() {
        let text = "\n══ 步#1 决策上下文 ══\n第一步内容\n多行\n\n══ 步#2 决策上下文 ══\n第二步内容\n";
        let m = ctx_segments(text);
        assert_eq!(m.get(&1).unwrap(), "第一步内容\n多行");
        assert_eq!(m.get(&2).unwrap(), "第二步内容");
        assert!(m.get(&3).is_none());
        assert!(ctx_segments("").is_empty());
    }

    #[test]
    fn redact_text_counts() {
        let (s, n) = redact_text("密码是123,别问密码", &["密码".to_string()]);
        assert_eq!(s, "[REDACTED]是123,别问[REDACTED]");
        assert_eq!(n, 2);
        let (s2, n2) = redact_text("干净文本", &["密码".to_string(), "".to_string()]);
        assert_eq!(s2, "干净文本");
        assert_eq!(n2, 0, "空关键词不炸");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("pf_cli_test_{name}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(d.join("tasks/演示/runs/20260831-100000")).unwrap();
        std::fs::create_dir_all(d.join("tasks/演示/runs/20260831-110000")).unwrap();
        d
    }

    #[test]
    fn run_summary_prefers_end_record_and_derives_for_old() {
        // 新局: r=end 权威;旧局: act/raw/verdict 派生 + 如实标注 derived
        let recs: Vec<Value> = vec![
            json!({"r":"act","n":1}), json!({"r":"raw","n":1}),
            json!({"r":"end","stop":"done","achieved":true,"steps":5,"calls":7,"tokens":123,"wall_ms":9000}),
        ];
        let s = run_summary(&recs);
        assert_eq!(s["stop"], "done");
        assert_eq!(s["tokens"], 123);
        assert!(s.get("derived").is_none());
        let old: Vec<Value> = vec![
            json!({"r":"act","n":1}), json!({"r":"act","n":2}),
            json!({"r":"raw","n":1}), json!({"r":"raw","n":2}), json!({"r":"raw","n":3}),
            json!({"r":"hook","kind":"verdict","achieved":true,"reason":"ok"}),
        ];
        let s2 = run_summary(&old);
        assert_eq!(s2["steps"], 2);
        assert_eq!(s2["calls"], 3);
        assert_eq!(s2["achieved"], true);
        assert_eq!(s2["derived"], true, "旧局派生要如实标注");
    }

    #[test]
    fn fuzzy_run_resolution_in_scratch_dir() {
        // env 注入数据根,不 chdir(chdir 是进程级状态,会与并行测试的相对路径互相打翻)
        let d = scratch("fuzzy");
        std::env::set_var("PF_TASKS_ROOT", d.join("tasks").as_os_str());
        // 前缀多命中 → 报候选;补长唯一 → 命中;无命中 → 报错
        let many = resolve_run("20260831", None);
        assert!(many.is_err() && many.unwrap_err().contains("命中 2 局"));
        let one = resolve_run("20260831-11", None).unwrap();
        assert_eq!(one.1, "20260831-110000");
        assert_eq!(one.0, "演示");
        assert!(resolve_run("2027", None).is_err());
        // 空数据降级: 指到空 tasks 目录,latest_task 如实 None 不 panic
        let empty = d.join("empty_tasks");
        std::fs::create_dir_all(&empty).unwrap();
        std::env::set_var("PF_TASKS_ROOT", empty.as_os_str());
        assert!(latest_task().is_none());
        std::env::remove_var("PF_TASKS_ROOT");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn cat_filter_and_schema_cover_all_types() {
        let t = "a1\nb2\na3\nb4\na5";
        assert_eq!(filter_lines(t, Some("a"), None, None), "a1\na3\na5");
        assert_eq!(filter_lines(t, Some("a"), Some(2), None), "a1\na3");
        assert_eq!(filter_lines(t, None, None, Some(2)), "a5".to_string().replace("a5", "b4\na5"));
        // schema 覆盖账本里全部 r 类型(含遥测三件)
        for ty in ["goal", "screen", "act", "diff", "raw", "hook", "probe", "lesson",
                   "telemetry", "app_event", "end", "trace", "note"] {
            assert!(SCHEMA_MD.contains(&format!("| {ty} ")), "schema 缺 {ty}");
        }
    }

    #[test]
    fn status_three_states() {
        // end→finished;start+活pid→running;start+死pid→died;无标记旧局→finished
        let fin = vec![json!({"r":"end","stop":"done"})];
        assert_eq!(status_of(&fin, |_| true).0, "finished");
        let me = std::process::id() as i64;
        let run = vec![json!({"r":"start","pid":me})];
        assert_eq!(status_of(&run, |p| p == me).0, "running");
        assert_eq!(status_of(&run, |_| false), ("died", Some(me)));
        let old: Vec<Value> = vec![json!({"r":"goal"})];
        assert_eq!(status_of(&old, |_| true).0, "finished", "旧局无标记按已结束");
        // detach 参数剥离: 只去 --detach,其余原样(含顺序)
        let args: Vec<String> = ["run","--task","T","--detach","目标"].iter().map(|s| s.to_string()).collect();
        assert_eq!(crate::strip_detach(&args), vec!["run","--task","T","目标"]);
    }

    #[test]
    fn stat3_percentiles() {
        let (mean, p50, p95) = stat3(vec![1.0, 2.0, 3.0, 4.0, 100.0]).unwrap();
        assert_eq!(p50, 3.0);
        assert_eq!(p95, 100.0);
        assert!((mean - 22.0).abs() < 0.1);
        assert!(stat3(vec![]).is_none(), "空序列如实 None");
    }
}
