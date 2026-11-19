//! 多设备并行(PARALLEL_SPEC v1.0): 一次命令,多台设备同时各跑各的局。
//! 机制选择(规格原文提到 thread::scope+episode,这里改自进程 fan-out,理由如实记):
//! episode 的输出是全代码约 50 处裸 println!,线程并行满足不了"每线 stdout 带设备前缀"
//! (行为要求③)——除非重写整个打印层,违背"改动最小、不动单局决策逻辑"。自进程方式
//! (current_exe 重入 run 子命令)零改动单局代码即满足全部四条行为要求,崩溃隔离更硬
//! (单局 abort 拖不垮别的设备);run_id 的 PID+序号后缀(任务1)正是它的防撞地基。
//! 同任务并发按方案A拒绝(任务4,lessons/tree 会互踩;合并语义留方案B)。
use std::io::BufRead;

pub struct Job {
    pub task: String,
    pub goal: String,
    pub serial: String,
    pub app: Option<String>,
    pub assert_words: Option<String>,
}

/// `--job "task|goal|serial[|app[|assert]]"` 解析。纯函数供单测。
pub fn parse_job(s: &str) -> Result<Job, String> {
    let p: Vec<&str> = s.split('|').collect();
    if p.len() < 3 || p[..3].iter().any(|x| x.trim().is_empty()) {
        return Err(format!("--job 需要至少 任务|目标|serial 三段,竖线分隔(收到 '{s}')"));
    }
    let opt = |i: usize| p.get(i).map(|x| x.trim()).filter(|x| !x.is_empty()).map(String::from);
    Ok(Job {
        task: p[0].trim().into(),
        goal: p[1].trim().into(),
        serial: p[2].trim().into(),
        app: opt(3),
        assert_words: opt(4),
    })
}

/// 同任务并发检查(方案A): 命中返回拒绝理由。纯函数供单测。
pub fn same_task_conflict(jobs: &[Job]) -> Option<String> {
    for (i, a) in jobs.iter().enumerate() {
        for b in &jobs[i + 1..] {
            if a.task == b.task {
                return Some(format!(
                    "任务 '{}' 同时派给了 {} 和 {}: 同任务并行会互踩 lessons/tree,\
请用不同任务名(如 {}-A / {}-B)分开经验库,或串行跑(并发合并语义留待方案B)",
                    a.task, a.serial, b.serial, a.task, a.task));
            }
        }
    }
    None
}

/// 日志前缀用的设备短名(hdc 长 key 截 8 位)
fn tag_of(serial: &str) -> String {
    match serial.strip_prefix("hdc:") {
        Some(k) => format!("hdc:{}", &k[..k.len().min(8)]),
        None => serial.to_string(),
    }
}

pub fn run_parallel(args: &[String]) -> i32 {
    // ── 解析: --job 段优先;--task+多--serial 形式共享任务(会被方案A拦,留作演示口) ──
    let mut jobs: Vec<Job> = Vec::new();
    let mut serials: Vec<String> = Vec::new();
    let (mut task, mut goal): (Option<String>, Option<String>) = (None, None);
    let (mut budget, mut endless) = (String::from("40"), false);
    let (mut app, mut assert_words): (Option<String>, Option<String>) = (None, None);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--job" => match it.next().map(|v| parse_job(v)) {
                Some(Ok(j)) => jobs.push(j),
                Some(Err(e)) => { eprintln!("{e}"); return 2; }
                None => { eprintln!("--job 缺参数"); return 2; }
            },
            "--serial" => { if let Some(v) = it.next() { serials.push(v.clone()); } }
            "--task" => task = it.next().cloned(),
            "--budget-calls" => { if let Some(v) = it.next() { budget = v.clone(); } }
            "--endless" => endless = true,
            "--app" => app = it.next().cloned(),
            "--assert" => assert_words = it.next().cloned(),
            other if !other.starts_with("--") => goal = Some(other.to_string()),
            other => { eprintln!("parallel 不认识参数 '{other}'"); return 2; }
        }
    }
    if jobs.is_empty() {
        let (Some(t), Some(g)) = (task, goal) else {
            eprintln!("用法: phonefarm parallel --job \"任务|目标|serial[|app[|assert]]\" [--job ...] [--budget-calls N] [--endless]\n或:   phonefarm parallel --task <T> --serial S1 --serial S2 ... \"<目标>\"(同任务形式,方案A会拒绝多设备)");
            return 2;
        };
        for s in serials {
            jobs.push(Job { task: t.clone(), goal: g.clone(), serial: s,
                            app: app.clone(), assert_words: assert_words.clone() });
        }
    }
    if jobs.is_empty() { eprintln!("没有任何 job(--job 或 --serial 至少给一个)"); return 2; }
    if let Some(why) = same_task_conflict(&jobs) {
        eprintln!("⚠ {why}");
        return 2;
    }

    // ── fan-out: 每 job 一个子进程(本程序重入 run),行级转发加 [设备] 前缀 ──
    let exe = match std::env::current_exe() {
        Ok(e) => e,
        Err(e) => { eprintln!("定位自身可执行文件失败: {e}"); return 2; }
    };
    println!("═══ parallel: {} 个 job 同时开跑 ═══", jobs.len());
    let results: Vec<(String, String, Option<i32>, Option<String>)> = std::thread::scope(|sc| {
        let mut handles = Vec::new();
        for job in &jobs {
            let exe = exe.clone();
            let budget = budget.clone();
            handles.push(sc.spawn(move || {
                let tag = tag_of(&job.serial);
                let mut cmd = std::process::Command::new(&exe);
                cmd.args(["run", "--task", &job.task, "--serial", &job.serial,
                          "--budget-calls", &budget]);
                if endless { cmd.arg("--endless"); }
                if let Some(a) = &job.app { cmd.args(["--app", a]); }
                if let Some(w) = &job.assert_words { cmd.args(["--assert", w]); }
                cmd.arg(&job.goal);
                cmd.stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped());
                let mut child = match cmd.spawn() {
                    Ok(c) => c,
                    Err(e) => {
                        eprintln!("[{tag}] 启动失败: {e}");
                        return (job.task.clone(), tag, None, None);
                    }
                };
                let mut summary: Option<String> = None;
                let stderr = child.stderr.take();
                let etag = tag.clone();
                let eh = std::thread::spawn(move || {
                    if let Some(se) = stderr {
                        for l in std::io::BufReader::new(se).lines().map_while(Result::ok) {
                            eprintln!("[{etag}] {l}");
                        }
                    }
                });
                if let Some(so) = child.stdout.take() {
                    for l in std::io::BufReader::new(so).lines().map_while(Result::ok) {
                        println!("[{tag}] {l}");
                        if l.starts_with("summary: ") { summary = Some(l); }
                    }
                }
                let _ = eh.join();
                let code = child.wait().ok().and_then(|st| st.code());
                (job.task.clone(), tag, code, summary)
            }));
        }
        handles.into_iter().map(|h| h.join().unwrap_or_else(|_| {
            ("?".into(), "?".into(), None, None) // 读线程panic也不拖垮总控
        })).collect()
    });

    // ── 总表: 全部结束统一收账;任一失败(退出码非0/无码)整体非0,单线失败不影响他线 ──
    println!("═══ parallel 总表 ═══");
    let mut worst = 0;
    for (task, tag, code, summary) in &results {
        let c = code.unwrap_or(9);
        if c != 0 { worst = 1; }
        println!("[{tag}] 任务[{task}] 退出码={c} {}", summary.as_deref().unwrap_or("(无summary,看上方日志)"));
    }
    worst
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn job_parse_three_to_five_fields() {
        let j = parse_job("头条|遍历目标|emulator-5554").unwrap();
        assert_eq!((j.task.as_str(), j.serial.as_str()), ("头条", "emulator-5554"));
        assert!(j.app.is_none() && j.assert_words.is_none());
        let j5 = parse_job("t|g|hdc:abc|com.x|词1,词2").unwrap();
        assert_eq!(j5.app.as_deref(), Some("com.x"));
        assert_eq!(j5.assert_words.as_deref(), Some("词1,词2"));
        assert!(parse_job("只有|两段").is_err());
        assert!(parse_job("a||c").is_err(), "空段拒收");
        let j4 = parse_job("t|g|s||word").unwrap();
        assert!(j4.app.is_none() && j4.assert_words.as_deref() == Some("word"), "空app跳过,assert照收");
    }

    #[test]
    fn same_task_rejected_diff_task_allowed() {
        let a = parse_job("T|g|s1").unwrap();
        let b = parse_job("T|g|s2").unwrap();
        let why = same_task_conflict(&[a, b]).unwrap();
        assert!(why.contains("lessons/tree") && why.contains("s1") && why.contains("s2"));
        let c = parse_job("T-A|g|s1").unwrap();
        let d = parse_job("T-B|g|s2").unwrap();
        assert!(same_task_conflict(&[c, d]).is_none(), "不同任务天然安全");
    }

    #[test]
    fn hdc_tag_truncated() {
        assert_eq!(tag_of("hdc:5ce1227d00000000000000000923012c"), "hdc:5ce1227d");
        assert_eq!(tag_of("emulator-5554"), "emulator-5554");
        assert_eq!(tag_of("hdc:ab"), "hdc:ab", "短key不越界");
    }
}
