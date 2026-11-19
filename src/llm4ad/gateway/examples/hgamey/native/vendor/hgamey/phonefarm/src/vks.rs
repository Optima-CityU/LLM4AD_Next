//! Vulkan-Samples 载体的三道纯函数闸: 挑提交线程、判就绪、两侧对账。
//!
//! 由 `loop_v1/carriers/vulkan-samples/` 下的 `pick_comm.py` / `ready.py` /
//! `crosscheck.py` 逐行搬过来, 输出字节一致。三个都不碰设备, 只读已有的文本。
//!
//! 为什么这三道闸缺一不可 (2026-09-23 本机实测)
//! --------------------------------------------
//! 样例启动后窗口要约 9.6s 才被系统真正合成。在那之前 present 不被节流, 样例自己数出
//! 约 2080 fps, 而 kgsl 里一条 GPU 提交都没有 —— **日志很好看, 其实画的不是那回事**。
//! 而且合成状态在一次运行里还会来回切, 所以"等够多少秒"不是个稳定判据。
//!
//!   [`pick`]       : 提交线程名由 GameActivity / 驱动决定, 不同样例不同系统都可能不一样,
//!                    写死等于埋雷 —— 数一遍分布取第一名, 并把占比报出来让调用方自己看。
//!   [`ready`]      : 帧率序列里那一**级阶跃**才是稳定判据, 不是秒数。
//!   [`crosscheck`] : 采完之后再用两个来源完全独立的量互相对账兜底。

use crate::pyjson::{dumps_compact, dumps_indent, loads, median_f64, py_round, PyVal};
use crate::pyobj;
use regex::Regex;

/// ftrace 行首 (只到 event 为止, 后面的尾串这里用不上)
const LINE_RE: &str =
    r"^\s*(?P<comm>.+?)-(?P<tid>\d+)\s+\[(?P<cpu>\d+)\]\s+\S+\s+(?P<ts>\d+\.\d+):\s+(?P<event>\w+):";

// ══════════════ pick_comm ══════════════

/// 数一遍 `adreno_cmdbatch_submitted` 按 comm 的分布, 取最多的那个。
///
/// 第一名不是压倒性的 (比如占比 < 80%) 说明这个负载的提交是多线程发的, 调用方应当
/// 停下来看清楚, 而不是默默按第一名解析 —— 所以完整分布一起打出来。
pub fn pick(text: &str, event: &str) -> PyVal {
    let re = Regex::new(LINE_RE).expect("LINE_RE");
    // 插入序 + 计数: Counter.most_common 的并列名次按先出现的排, 这里靠稳定排序还原
    let mut counts: Vec<(String, i64)> = Vec::new();
    for line in text.lines() {
        let Some(m) = re.captures(line) else { continue };
        if &m["event"] != event {
            continue;
        }
        let comm = m["comm"].trim().to_string();
        match counts.iter_mut().find(|(k, _)| *k == comm) {
            Some(slot) => slot.1 += 1,
            None => counts.push((comm, 1)),
        }
    }
    let total: i64 = counts.iter().map(|(_, n)| n).sum();
    if total == 0 {
        return pyobj! {
            "comm" => PyVal::Null, "share" => 0.0, "dist" => PyVal::Obj(vec![]), "total" => 0i64
        };
    }
    // 稳定排序: 计数降序, 并列保持先出现的在前 (与 Counter.most_common 一致)
    counts.sort_by_key(|(_, n)| std::cmp::Reverse(*n));
    let (comm, n) = counts[0].clone();
    pyobj! {
        "comm" => comm,
        "share" => py_round(n as f64 / total as f64, 4),
        "dist" => PyVal::Obj(counts.iter().take(8).map(|(k, v)| (k.clone(), PyVal::Int(*v))).collect()),
        "total" => total,
    }
}

// ══════════════ ready ══════════════

/// 出现过阶跃下降才算"之前在空转"
const STEP: f64 = 1.8;
/// 最后几个样本已经平下来
const FLAT: f64 = 0.15;
const MIN_SAMPLES: usize = 5;

/// 样例是否已经进入"真实渲染"稳态。
///
/// 判据两条同时成立:
///   1. 出现过阶跃下降: `max(全部样本) / median(最后 3 个) >= STEP`
///   2. 已经稳下来:     最后 3 个样本的 `(max-min)/median <= FLAT`
///
/// 从没空转过的样例 (例如开着 vsync 跑的 hello_triangle) 永远不满足第 1 条,
/// 所以调用方必须给这个轮询一个上界, 超时就照常往下走 —— 采完还有对账那道数据侧兜底。
pub fn ready(fps: &[f64]) -> bool {
    if fps.len() < MIN_SAMPLES {
        return false;
    }
    let tail = &fps[fps.len() - 3..];
    let Some(m) = median_f64(tail) else {
        return false;
    };
    if m <= 0.0 {
        return false;
    }
    let (lo, hi) = (
        tail.iter().copied().fold(f64::INFINITY, f64::min),
        tail.iter().copied().fold(f64::NEG_INFINITY, f64::max),
    );
    if (hi - lo) / m > FLAT {
        return false;
    }
    fps.iter().copied().fold(f64::NEG_INFINITY, f64::max) / m >= STEP
}

// ══════════════ crosscheck ══════════════

/// 提交速率 / 应用帧率 的可接受区间。下界拦"应用在数帧但 GPU 没活干";
/// 上界拦"trace 里混进了别的进程的提交"。一帧 1~4 次提交都在带内。
const RATIO_MIN: f64 = 0.5;
const RATIO_MAX: f64 = 6.0;

/// 例: `[2026-09-23 21:15:28.490] [logger] [info] FPS: 1922.7`
const LOG_LINE_RE: &str = r"^\[(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+\].*FPS: (?P<fps>[\d.]+)";
const FPS_RE: &str = r"FPS: ([\d.]+)";

fn parse_fps(s: &str) -> Result<f64, String> {
    // Python 的 float() 在 "1.2.3" 这种上抛 ValueError 把整个脚本打掉 —— 这里照样报错,
    // 不拿一个猜出来的数往下走
    s.parse().map_err(|_| format!("日志里的帧率不是数字: {s:?}"))
}

/// 返回 (采样, 是否真的按窗口过滤过)。窗口内一个样本都没有时退回全量, 并如实标注。
///
/// 窗口边界是**设备墙钟字符串**, 按字典序比 —— `YYYY-MM-DD HH:MM:SS` 这个格式上
/// 字典序就是时间序, 不必解析成时间。
pub fn fps_in_window(log_text: &str, window: &PyVal) -> Result<(Vec<f64>, bool), String> {
    let (lo, hi) = (window.get("cap_start"), window.get("cap_end"));
    // Python 的 `window and window.get(...)`: 空字符串也算没有
    let truthy = |v: Option<&PyVal>| matches!(v, Some(PyVal::Str(s)) if !s.is_empty());
    if truthy(lo) && truthy(hi) {
        let (lo, hi) = (lo.unwrap().py_str(), hi.unwrap().py_str());
        let re = Regex::new(LOG_LINE_RE).expect("LOG_LINE_RE");
        let mut picked = Vec::new();
        for line in log_text.lines() {
            let Some(m) = re.captures(line.trim()) else {
                continue;
            };
            let ts = &m["ts"];
            if lo.as_str() <= ts && ts <= hi.as_str() {
                picked.push(parse_fps(&m["fps"])?);
            }
        }
        if !picked.is_empty() {
            return Ok((picked, true));
        }
    }
    let re = Regex::new(FPS_RE).expect("FPS_RE");
    let mut all = Vec::new();
    for c in re.captures_iter(log_text) {
        all.push(parse_fps(&c[1])?);
    }
    Ok((all, false))
}

/// 内核侧提交速率与应用侧自报帧率的对账。
///
/// 两个必须踩对的细节:
///
/// 1. **比提交速率, 不比 fps_mean。** `parse-trace` 会自检"每帧几次提交"(spf) 再用
///    提交速率/spf 得到 fps; 这个自检在本载体上实测会把 spf 认成 3, 于是 fps_mean 刚好
///    是应用自报帧率的 1/3, 一轮完全正常的数据会被判成"对不上"。这里把 spf 乘回去
///    还原成提交速率, 再问一个更弱也更诚实的问题: **提交速率是不是应用帧率的一个
///    小整数倍**。判的是数量级而不是整数精度 —— 实测一轮完全干净的数据比值是 1.41,
///    两侧计数窗口本来就不是同一个口径, 要求贴近整数会把好轮判掉。
/// 2. **只取采集窗内的 FPS 样本。** trace 只有 12 秒而 run.log 覆盖整段运行, 拿整段的
///    中位数去比 12 秒的窗口, 遇上运行中途切换合成状态就会误杀好轮。
pub fn crosscheck(log_text: &str, summary: &PyVal, window: &PyVal) -> Result<PyVal, String> {
    let (fps, windowed) = fps_in_window(log_text, window)?;
    let trace_fps_raw = summary.get("fps_mean").cloned().unwrap_or(PyVal::Null);
    let trace_fps = trace_fps_raw.as_f64();
    // Python 的 `or 1`: null 与 0 都退回 1
    let spf = match summary.get("submits_per_frame").and_then(|v| v.as_f64()) {
        Some(v) if v != 0.0 => v,
        _ => 1.0,
    };
    // Python 的 `trace_fps * spf`: 两个都是 int 就还是 int, 沾上 float 才是 float。
    // 注意 `or 1` 兜底出来的 1 也是 int —— spf 只有在原值是个非零 float 时才是 float。
    let spf_is_int = !matches!(summary.get("submits_per_frame"), Some(PyVal::Float(f)) if *f != 0.0);
    let submit_rate = trace_fps.map(|f| f * spf);
    let submit_rate_val = match (&trace_fps_raw, spf_is_int, submit_rate) {
        (PyVal::Int(a), true, _) => PyVal::Int(a * spf as i64),
        (_, _, Some(v)) => PyVal::Float(py_round(v, 3)),
        (_, _, None) => PyVal::Null,
    };
    let log_fps = median_f64(&fps);

    let mut ratio: Option<f64> = None;
    let mut in_band = false;
    // Python 的 `if log_fps and submit_rate is not None`: log_fps 为 0 也算假
    if let (Some(l), Some(sr)) = (log_fps.filter(|v| *v != 0.0), submit_rate) {
        let r = sr / l;
        ratio = Some(r);
        in_band = (RATIO_MIN..=RATIO_MAX).contains(&r);
    }
    Ok(pyobj! {
        "log_fps_median" => log_fps,
        "windowed" => windowed,
        "trace_fps_mean" => trace_fps_raw.clone(),
        // spf 原样回写: summary 里是 int 就该写 int
        "submits_per_frame" => match summary.get("submits_per_frame") {
            Some(v) if !matches!(v, PyVal::Null) && v.as_f64().is_some_and(|x| x != 0.0) => v.clone(),
            _ => PyVal::Int(1),
        },
        "trace_submit_rate" => submit_rate_val,
        "ratio_to_log_fps" => ratio.map(|v| py_round(v, 4)),
        "band" => vec![RATIO_MIN, RATIO_MAX],
        "in_band" => in_band,
        "n_fps_samples" => fps.len(),
    })
}

// ══════════════ 子命令 ══════════════

fn read_lossy(path: &str) -> Result<String, String> {
    std::fs::read(path)
        .map(|b| String::from_utf8_lossy(&b).to_string())
        .map_err(|e| format!("{path}: {e}"))
}

pub fn run_pick_comm(args: &[String]) -> i32 {
    let Some(path) = args.iter().find(|a| !a.starts_with("--")) else {
        eprintln!("用法: phonefarm vks-pick-comm <trace.txt>");
        return 2;
    };
    let text = match read_lossy(path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("{e}");
            return 1;
        }
    };
    let out = pick(&text, "adreno_cmdbatch_submitted");
    println!("{}", dumps_indent(&out, 2));
    // 一条提交都没数到、或者认出来的名字是空串 = 这份 trace 说明不了任何事,
    // 用退出码让调用方停下来。Python 那边是 `0 if out["comm"] else 1`, 空串也算假。
    match out.get("comm") {
        Some(PyVal::Str(c)) if !c.is_empty() => 0,
        _ => 1,
    }
}

pub fn run_ready(args: &[String]) -> i32 {
    let Some(path) = args.iter().find(|a| !a.starts_with("--")) else {
        eprintln!("用法: phonefarm vks-ready <run.log>");
        return 2;
    };
    // 文件还没出现 = 还没就绪, 不是错
    let Ok(text) = read_lossy(path) else {
        return 1;
    };
    let re = Regex::new(FPS_RE).expect("FPS_RE");
    let mut fps = Vec::new();
    for c in re.captures_iter(&text) {
        match parse_fps(&c[1]) {
            Ok(v) => fps.push(v),
            Err(e) => {
                eprintln!("{e}");
                return 1;
            }
        }
    }
    if ready(&fps) {
        0
    } else {
        1
    }
}

pub fn run_crosscheck(args: &[String]) -> i32 {
    let files: Vec<&String> = args.iter().filter(|a| !a.starts_with("--")).collect();
    if files.len() < 2 {
        eprintln!("用法: phonefarm vks-crosscheck <run.log> <summary.json> [window.json]");
        return 2;
    }
    let (log_text, summary_text) = match (read_lossy(files[0]), read_lossy(files[1])) {
        (Ok(a), Ok(b)) => (a, b),
        (Err(e), _) | (_, Err(e)) => {
            eprintln!("{e}");
            return 1;
        }
    };
    let summary = match loads(&summary_text) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("{}: {e}", files[1]);
            return 1;
        }
    };
    // 窗口读不到或读不懂都当没有 —— 退回全量并在输出里标 windowed=false
    let window = files
        .get(2)
        .and_then(|p| read_lossy(p).ok())
        .and_then(|t| loads(&t).ok())
        .unwrap_or(PyVal::Null);
    match crosscheck(&log_text, &summary, &window) {
        Ok(v) => {
            println!("{}", dumps_compact(&v));
            0
        }
        Err(e) => {
            eprintln!("{e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::{Path, PathBuf};

    fn fixtures() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("loop_v1/fixtures/vks")
    }

    fn trace_line(comm: &str, tid: u32, ev: &str) -> String {
        format!("  {comm}-{tid}   [003] ..... 1045979.109679: {ev}: ctx=41 ts=1\n")
    }

    #[test]
    fn pick_takes_the_busiest_submitter_and_reports_the_share() {
        let mut t = String::new();
        for _ in 0..7 {
            t.push_str(&trace_line("GameThread", 100, "adreno_cmdbatch_submitted"));
        }
        for _ in 0..3 {
            t.push_str(&trace_line("OtherThread", 200, "adreno_cmdbatch_submitted"));
        }
        // 别的事件不算数
        t.push_str(&trace_line("GameThread", 100, "adreno_cmdbatch_retired"));
        let v = pick(&t, "adreno_cmdbatch_submitted");
        assert_eq!(v.get("comm"), Some(&PyVal::Str("GameThread".into())));
        assert_eq!(v.get("share").unwrap().as_f64(), Some(0.7));
        assert_eq!(v.get("total"), Some(&PyVal::Int(10)));
    }

    /// 并列名次按先出现的排 (与 Counter.most_common 一致)。
    #[test]
    fn pick_breaks_ties_by_first_seen() {
        let t = format!(
            "{}{}",
            trace_line("Bbb", 1, "adreno_cmdbatch_submitted"),
            trace_line("Aaa", 2, "adreno_cmdbatch_submitted")
        );
        assert_eq!(
            pick(&t, "adreno_cmdbatch_submitted").get("comm"),
            Some(&PyVal::Str("Bbb".into()))
        );
    }

    #[test]
    fn pick_on_an_empty_trace_says_so() {
        let v = pick("garbage\n", "adreno_cmdbatch_submitted");
        assert_eq!(v.get("comm"), Some(&PyVal::Null));
        assert_eq!(v.get("total"), Some(&PyVal::Int(0)));
        assert_eq!(v.get("share").unwrap().as_f64(), Some(0.0));
    }

    /// 阶跃 + 平稳两条都成立才算就绪。
    #[test]
    fn ready_needs_both_a_step_and_a_flat_tail() {
        // 2080 空转 → 340 稳态: 阶跃 6.1 倍, 尾部平
        assert!(ready(&[2080.0, 2075.0, 2090.0, 341.0, 340.0, 339.0]));
        // 样本不够
        assert!(!ready(&[2080.0, 340.0, 340.0, 340.0]));
        // 从没空转过 (开着 vsync 的样例): 没有阶跃
        assert!(!ready(&[60.0, 60.1, 59.9, 60.0, 60.0, 60.0]));
        // 阶跃有了但尾部还在抖
        assert!(!ready(&[2080.0, 2075.0, 2090.0, 500.0, 340.0, 200.0]));
        // 中位数为 0
        assert!(!ready(&[100.0, 100.0, 100.0, 0.0, 0.0, 0.0]));
    }

    #[test]
    fn crosscheck_prefers_window_samples_but_falls_back_honestly() {
        let log = "[2026-09-23 21:15:20.000] [logger] [info] FPS: 2080.0\n\
[2026-09-23 21:15:28.490] [logger] [info] FPS: 120.0\n\
[2026-09-23 21:15:29.000] [logger] [info] FPS: 121.0\n";
        let summary = pyobj! { "fps_mean" => 118.681, "submits_per_frame" => 1i64 };
        let win = pyobj! { "cap_start" => "2026-09-23 21:15:28", "cap_end" => "2026-09-23 21:15:40" };
        let v = crosscheck(log, &summary, &win).unwrap();
        assert_eq!(v.get("windowed"), Some(&PyVal::Bool(true)));
        assert_eq!(v.get("n_fps_samples"), Some(&PyVal::Int(2)));
        assert_eq!(v.get("log_fps_median").unwrap().as_f64(), Some(120.5));
        assert_eq!(v.get("in_band"), Some(&PyVal::Bool(true)));

        // 窗口内一个样本都没有 → 退回全量并如实标注
        let win2 = pyobj! { "cap_start" => "2030-01-01 00:00:00", "cap_end" => "2030-01-02 00:00:00" };
        let v = crosscheck(log, &summary, &win2).unwrap();
        assert_eq!(v.get("windowed"), Some(&PyVal::Bool(false)));
        assert_eq!(v.get("n_fps_samples"), Some(&PyVal::Int(3)));
        // 没有窗口文件也一样
        assert_eq!(
            crosscheck(log, &summary, &PyVal::Null).unwrap().get("windowed"),
            Some(&PyVal::Bool(false))
        );
    }

    /// spf 要乘回去: 自检把 spf 认成 3 时, fps_mean 恰好是应用帧率的 1/3,
    /// 不还原成提交速率就会把一轮好数据判掉。
    #[test]
    fn crosscheck_compares_submit_rate_not_fps_mean() {
        let log = "FPS: 120.0\nFPS: 120.0\n";
        let v = crosscheck(
            log,
            &pyobj! { "fps_mean" => 40.0, "submits_per_frame" => 3i64 },
            &PyVal::Null,
        )
        .unwrap();
        assert_eq!(v.get("trace_submit_rate").unwrap().as_f64(), Some(120.0));
        assert_eq!(v.get("ratio_to_log_fps").unwrap().as_f64(), Some(1.0));
        assert_eq!(v.get("in_band"), Some(&PyVal::Bool(true)));
        // 应用在数帧而 GPU 没活干: 比值掉到 0 附近, 出带
        let v = crosscheck(
            log,
            &pyobj! { "fps_mean" => 1.0, "submits_per_frame" => 1i64 },
            &PyVal::Null,
        )
        .unwrap();
        assert_eq!(v.get("in_band"), Some(&PyVal::Bool(false)));
    }

    #[test]
    fn crosscheck_without_any_data_stays_null() {
        let v = crosscheck("", &PyVal::Obj(vec![]), &PyVal::Null).unwrap();
        assert_eq!(v.get("log_fps_median"), Some(&PyVal::Null));
        assert_eq!(v.get("trace_fps_mean"), Some(&PyVal::Null));
        assert_eq!(v.get("trace_submit_rate"), Some(&PyVal::Null));
        assert_eq!(v.get("ratio_to_log_fps"), Some(&PyVal::Null));
        assert_eq!(v.get("in_band"), Some(&PyVal::Bool(false)));
        assert_eq!(v.get("submits_per_frame"), Some(&PyVal::Int(1)));
    }

    /// 日志里出现 `FPS: 1.2.3` 这种时 Python 是抛 ValueError 把脚本打掉,
    /// 这里照样报错, 不拿一个猜出来的数往下走。
    #[test]
    fn malformed_fps_is_an_error() {
        assert!(crosscheck("FPS: 1.2.3\n", &PyVal::Obj(vec![]), &PyVal::Null).is_err());
    }

    /// `fps_mean` 与 `submits_per_frame` 都是 int 时, 提交速率也该是 int。
    #[test]
    fn crosscheck_keeps_int_ness_of_passthrough_fields() {
        let v = crosscheck(
            "FPS: 120.0\nFPS: 120.0\n",
            &pyobj! { "fps_mean" => 40i64, "submits_per_frame" => 3i64 },
            &PyVal::Null,
        )
        .unwrap();
        assert_eq!(v.get("trace_fps_mean"), Some(&PyVal::Int(40)));
        assert_eq!(v.get("trace_submit_rate"), Some(&PyVal::Int(120)));
        assert_eq!(v.get("submits_per_frame"), Some(&PyVal::Int(3)));
        // spf 缺失/为 0 时 `or 1` 兜底出来的也是 int
        for spf in [PyVal::Null, PyVal::Int(0), PyVal::Float(0.0)] {
            let v = crosscheck(
                "FPS: 120.0\n",
                &pyobj! { "fps_mean" => 40i64, "submits_per_frame" => spf.clone() },
                &PyVal::Null,
            )
            .unwrap();
            assert_eq!(v.get("trace_submit_rate"), Some(&PyVal::Int(40)), "{spf:?}");
            assert_eq!(v.get("submits_per_frame"), Some(&PyVal::Int(1)), "{spf:?}");
        }
        // 原值是非零 float 时才是 float
        let v = crosscheck(
            "FPS: 120.0\n",
            &pyobj! { "fps_mean" => 40i64, "submits_per_frame" => 2.0 },
            &PyVal::Null,
        )
        .unwrap();
        assert_eq!(v.get("trace_submit_rate"), Some(&PyVal::Float(80.0)));
    }

    /// 对照源: 旧 Python 版在同一份合成输入上的输出 (fixtures/vks/)。
    #[test]
    fn matches_recorded_golden() {
        let d = fixtures();
        let mut checked = 0;
        let mut cases: Vec<PathBuf> = std::fs::read_dir(&d)
            .expect("fixtures/vks 不在")
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.is_dir())
            .collect();
        cases.sort();
        let fps_re = Regex::new(FPS_RE).unwrap();
        for c in cases {
            if c.join("trace.txt").exists() {
                let t = std::fs::read_to_string(c.join("trace.txt")).unwrap();
                let want = std::fs::read_to_string(c.join("comm.json")).unwrap();
                assert_eq!(
                    dumps_indent(&pick(&t, "adreno_cmdbatch_submitted"), 2),
                    want.trim_end_matches('\n'),
                    "{} 的 pick_comm 不一致",
                    c.display()
                );
                checked += 1;
            }
            if c.join("run.log").exists() && c.join("summary.json").exists() {
                let log = std::fs::read_to_string(c.join("run.log")).unwrap();
                let summary = loads(&std::fs::read_to_string(c.join("summary.json")).unwrap()).unwrap();
                let window = std::fs::read_to_string(c.join("window.json"))
                    .ok()
                    .and_then(|t| loads(&t).ok())
                    .unwrap_or(PyVal::Null);
                let want = std::fs::read_to_string(c.join("crosscheck.json")).unwrap();
                assert_eq!(
                    dumps_compact(&crosscheck(&log, &summary, &window).unwrap()),
                    want.trim_end_matches('\n'),
                    "{} 的 crosscheck 不一致",
                    c.display()
                );
                // ready 的结论存成一个字
                let want_ready = std::fs::read_to_string(c.join("ready.txt")).unwrap();
                let fps: Vec<f64> = fps_re
                    .captures_iter(&log)
                    .map(|m| m[1].parse::<f64>().unwrap())
                    .collect();
                assert_eq!(
                    if ready(&fps) { "0" } else { "1" },
                    want_ready.trim(),
                    "{} 的 ready 不一致",
                    c.display()
                );
                checked += 1;
            }
        }
        assert!(checked >= 6, "只比到 {checked} 项, 太少");
    }
}
