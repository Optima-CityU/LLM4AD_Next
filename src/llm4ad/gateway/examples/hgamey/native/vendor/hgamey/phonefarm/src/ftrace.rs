//! ftrace: 从 kgsl/adreno 事件里取帧时序与 GPU 忙时。
//!
//! 为什么走 raw ftrace: `SurfaceFlinger --latency` 在 Android 16 上已失效,
//! `gfxinfo` 测不到走 SurfaceView 的应用, Perfetto 的 GPU producer 在本机驱动未注册 ——
//! 三条路都已实测排除 (见 `https://github.com/HGamey/docs/blob/main/repos/phonefarm/loop_v1/README.md`)。raw ftrace 是纯文本、root 可开、
//! 不侵入被测进程, 一次采集同时拿到帧节奏与 GPU 执行时长。
//!
//! 纪律: 解析全是纯函数, 同一份 trace 每次重算逐字节一致;
//! 采集前后的 tracing 状态快照存档并在退出时还原, 无论中途成功失败。

/// GPU 计时 tick 频率。实测 Δticks / Δ墙钟 = 19.21e6, 即 XO 19.2MHz。
pub const GPU_TICK_HZ: f64 = 19_200_000.0;

/// 采集用到的 ftrace 事件。与 `loop_v1/tools/ftrace_capture.sh` 保持一致。
pub const EVENTS: [&str; 4] = [
    "adreno_cmdbatch_submitted",
    "adreno_cmdbatch_retired",
    "kgsl_pwrlevel",
    "kgsl_thermal_constraint",
];

const TRACE_ROOT: &str = "/sys/kernel/tracing";

/// 一条 ftrace 事件行。
#[derive(Debug, Clone, PartialEq)]
pub struct Event {
    pub comm: String,
    pub tid: u64,
    /// 墙钟时间戳 (秒)
    pub ts: f64,
    pub event: String,
    pub rest: String,
}

impl Event {
    /// 从尾串里取一个整数字段 (`ctx=41`, `active=14232` 这种)。
    pub fn kv(&self, key: &str) -> Option<i64> {
        let pat = format!("{key}=");
        let mut from = 0usize;
        while let Some(i) = self.rest[from..].find(&pat) {
            let at = from + i;
            // 必须是词首, 否则 `ctx=` 会命中 `ctx_prio=` 之类
            let head_ok = at == 0
                || !self.rest.as_bytes()[at - 1].is_ascii_alphanumeric()
                    && self.rest.as_bytes()[at - 1] != b'_';
            if head_ok {
                let tail = &self.rest[at + pat.len()..];
                let end = tail
                    .find(|c: char| !(c.is_ascii_digit() || c == '-'))
                    .unwrap_or(tail.len());
                if end > 0 {
                    return tail[..end].parse().ok();
                }
            }
            from = at + pat.len();
        }
        None
    }
}

/// 解析一行 ftrace。
///
/// 行首形如 `  RefbenchDrv-32074   [005] ..... 1044964.694371: event: rest`。
/// comm 里可能带空格与连字符, 所以从**最后一个** `-数字` 处切开, 不能贪心从前找。
pub fn parse_line(line: &str) -> Option<Event> {
    let line = line.trim_end();
    if line.trim_start().starts_with('#') || line.trim().is_empty() {
        return None;
    }
    // 先定位 " [cpu] " 之前的 "comm-tid"
    let br = line.find(" [")?;
    let head = &line[..br];
    let dash = head.rfind('-')?;
    let comm = head[..dash].trim().to_string();
    let tid: u64 = head[dash + 1..].trim().parse().ok()?;

    // 时间戳: "] ..... 1044964.694371: event: rest"
    let after = &line[br..];
    let colon = after.find(além_ts_marker())?;
    let ts_start = after[..colon].rfind(char::is_whitespace)? + 1;
    let ts: f64 = after[ts_start..colon].parse().ok()?;

    let tail = &after[colon + 1..];
    let ev_end = tail.find(':')?;
    let event = tail[..ev_end].trim().to_string();
    let rest = tail[ev_end + 1..].trim().to_string();
    if event.is_empty() {
        return None;
    }
    Some(Event {
        comm,
        tid,
        ts,
        event,
        rest,
    })
}

/// 时间戳与事件名之间的分隔符。抽成函数只是为了让 `parse_line` 读起来不那么密。
fn além_ts_marker() -> char {
    ':'
}

/// 一次采集里抽出的帧时序与 GPU 忙时。
#[derive(Debug, Clone, Default, PartialEq)]
pub struct FrameStats {
    /// 提交次数 (= 帧数, 前提是被测应用每帧一次提交)
    pub submits: usize,
    /// 相邻两次提交的间隔 (毫秒)
    pub intervals_ms: Vec<f64>,
    /// 每次提交对应的 GPU 忙时 (毫秒), 由 retired 事件的 `active` ticks 换算
    pub gpu_busy_ms: Vec<f64>,
}

/// 从 trace 文本里抽帧时序。
///
/// `comm` 是提交线程名 —— refbench 把驱动的匿名提交线程改名成 `RefbenchDrv`
/// (Adreno 驱动从自己的 in-process 线程提交 cmdbatch, 不是调用方线程)。
///
/// **前提: 被测应用每帧只提交一次。** refbench 的契约里写死了 `submits_per_frame: 1`,
/// 所以这里直接把提交间隔当帧间隔。原神那种每帧两次提交的必须先做自检
/// (见 `looptrace.rs` 的 `detect_submits_per_frame`),
/// 直接拿提交间隔当帧间隔会得出"帧率翻倍"的错误结论。
pub fn frame_stats(trace: &str, comm: &str) -> FrameStats {
    let mut submit_ts: Vec<f64> = Vec::new();
    let mut ctxs: Vec<i64> = Vec::new();
    let mut retired: Vec<(i64, f64)> = Vec::new(); // (ctx, active_ms)

    for line in trace.lines() {
        let Some(e) = parse_line(line) else { continue };
        match e.event.as_str() {
            "adreno_cmdbatch_submitted" if e.comm == comm => {
                submit_ts.push(e.ts);
                if let Some(c) = e.kv("ctx") {
                    if !ctxs.contains(&c) {
                        ctxs.push(c);
                    }
                }
            }
            "adreno_cmdbatch_retired" => {
                if let (Some(c), Some(active)) = (e.kv("ctx"), e.kv("active")) {
                    retired.push((c, active as f64 / GPU_TICK_HZ * 1000.0));
                }
            }
            _ => {}
        }
    }

    // retired 事件由 GMU 线程发出, 不带被测应用的 comm; 按 ctx 归属回去。
    let gpu_busy_ms: Vec<f64> = retired
        .into_iter()
        .filter(|(c, _)| ctxs.contains(c))
        .map(|(_, ms)| ms)
        .collect();

    submit_ts.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let intervals_ms: Vec<f64> = submit_ts
        .windows(2)
        .map(|w| (w[1] - w[0]) * 1000.0)
        .filter(|v| v.is_finite() && *v > 0.0)
        .collect();

    FrameStats {
        submits: submit_ts.len(),
        intervals_ms,
        gpu_busy_ms,
    }
}

// ══════════════ 设备侧采集 ══════════════

/// 采集期间接管 tracing 状态, Drop 时还原。
///
/// 存档 → 改 → 还原 的纪律和 `hwcond::Lock` 一致: 采集器异常退出也不能把设备
/// 留在开着 tracing 的状态 (那会持续写 ring buffer, 影响后续任何一轮测量)。
pub struct TraceSession<'a> {
    phone: &'a crate::device::Device,
    saved_on: String,
    saved_tracer: String,
    active: bool,
}

impl<'a> TraceSession<'a> {
    /// 存档当前状态, 清空 buffer, 打开所需事件并开始采集。
    pub fn begin(phone: &'a crate::device::Device, buffer_kb: u32) -> Result<Self, String> {
        let saved = phone.shell(
            &format!("su -c 'cat {TRACE_ROOT}/tracing_on; cat {TRACE_ROOT}/current_tracer'"),
            10_000,
        );
        let mut it = saved.lines();
        let saved_on = it.next().unwrap_or("0").trim().to_string();
        let saved_tracer = it.next().unwrap_or("nop").trim().to_string();

        let enables: String = EVENTS
            .iter()
            .map(|e| format!("echo 1 > {TRACE_ROOT}/events/kgsl/{e}/enable 2>/dev/null; "))
            .collect();
        let cmd = format!(
            "su -c 'echo 0 > {TRACE_ROOT}/tracing_on; echo nop > {TRACE_ROOT}/current_tracer; \
             echo {buffer_kb} > {TRACE_ROOT}/buffer_size_kb 2>/dev/null; \
             echo > {TRACE_ROOT}/trace; {enables} echo 1 > {TRACE_ROOT}/tracing_on; echo TRACE_ON'"
        );
        let out = phone.shell(&cmd, 20_000);
        if !out.contains("TRACE_ON") {
            return Err(format!("打不开 ftrace: {}", out.trim()));
        }
        Ok(Self {
            phone,
            saved_on,
            saved_tracer,
            active: true,
        })
    }

    /// 停止采集并把 trace 文本读回来。
    pub fn stop_and_read(&mut self) -> String {
        self.phone
            .shell(&format!("su -c 'echo 0 > {TRACE_ROOT}/tracing_on'"), 10_000);
        self.active = false;
        self.phone
            .shell(&format!("su -c 'cat {TRACE_ROOT}/trace'"), 120_000)
    }
}

impl Drop for TraceSession<'_> {
    fn drop(&mut self) {
        let disables: String = EVENTS
            .iter()
            .map(|e| format!("echo 0 > {TRACE_ROOT}/events/kgsl/{e}/enable 2>/dev/null; "))
            .collect();
        let cmd = format!(
            "su -c 'echo 0 > {TRACE_ROOT}/tracing_on; {disables} \
             echo {} > {TRACE_ROOT}/current_tracer 2>/dev/null; \
             echo > {TRACE_ROOT}/trace; echo {} > {TRACE_ROOT}/tracing_on'",
            self.saved_tracer, self.saved_on
        );
        self.phone.shell(&cmd, 20_000);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // 真实 trace 行 (取自 loop_v1/runs/base1/trace.txt, comm 换成 refbench 的)
    const SUBMIT: &str = "    RefbenchDrv-32074   [003] ..... 1045979.109679: adreno_cmdbatch_submitted: ctx=41 ctx_prio=8 ts=64996 inflight=1 flags=CTX_SWITCH ticks=20083078451840 time=1045979.109676 rb_id=2 r/w=0/0, q_inflight=0 dq_id=-1";
    const RETIRE: &str = "         gmu_f2h-2339    [004] ..... 1045979.110767: adreno_cmdbatch_retired: ctx=41 ctx_prio=8 ts=64996 inflight=0 recovery=none flags=none start=20083078457087 retire=20083078471324 rb_id=2, r/w=0/0, q_inflight=0, dq_id=4294967295, submitted_to_rb=20083078456784 retired_on_gmu=20083078471852 active=14232";

    #[test]
    fn parses_a_real_submit_line() {
        let e = parse_line(SUBMIT).unwrap();
        assert_eq!(e.comm, "RefbenchDrv");
        assert_eq!(e.tid, 32074);
        assert_eq!(e.event, "adreno_cmdbatch_submitted");
        assert!((e.ts - 1045979.109679).abs() < 1e-6);
        assert_eq!(e.kv("ctx"), Some(41));
        assert_eq!(e.kv("inflight"), Some(1));
    }

    #[test]
    fn parses_a_real_retire_line() {
        let e = parse_line(RETIRE).unwrap();
        assert_eq!(e.comm, "gmu_f2h");
        assert_eq!(e.event, "adreno_cmdbatch_retired");
        assert_eq!(e.kv("active"), Some(14232));
        assert_eq!(e.kv("ctx"), Some(41));
    }

    /// `ctx=` 不能命中 `ctx_prio=`; 前缀匹配写错了会把优先级当上下文 id。
    #[test]
    fn key_lookup_matches_whole_words_only() {
        let e = parse_line(SUBMIT).unwrap();
        assert_eq!(e.kv("ctx"), Some(41));
        assert_eq!(e.kv("ctx_prio"), Some(8));
        assert_eq!(e.kv("nosuch"), None);
    }

    /// comm 里可能带连字符, 必须从最后一个 `-数字` 处切。
    #[test]
    fn splits_comm_on_the_last_dash() {
        let l = "  my-render-thread-123   [001] ..... 100.5: adreno_cmdbatch_submitted: ctx=7";
        let e = parse_line(l).unwrap();
        assert_eq!(e.comm, "my-render-thread");
        assert_eq!(e.tid, 123);
    }

    #[test]
    fn skips_headers_and_blanks() {
        assert!(parse_line("# tracer: nop").is_none());
        assert!(parse_line("").is_none());
        assert!(parse_line("   ").is_none());
        assert!(parse_line("garbage without structure").is_none());
    }

    #[test]
    fn frame_intervals_come_from_submit_timestamps() {
        let trace = "\
    RefbenchDrv-1   [000] ..... 100.000000: adreno_cmdbatch_submitted: ctx=41 ts=1
    RefbenchDrv-1   [000] ..... 100.016600: adreno_cmdbatch_submitted: ctx=41 ts=2
    RefbenchDrv-1   [000] ..... 100.033300: adreno_cmdbatch_submitted: ctx=41 ts=3
";
        let s = frame_stats(trace, "RefbenchDrv");
        assert_eq!(s.submits, 3);
        assert_eq!(s.intervals_ms.len(), 2);
        assert!((s.intervals_ms[0] - 16.6).abs() < 0.01);
        assert!((s.intervals_ms[1] - 16.7).abs() < 0.01);
    }

    /// GPU 忙时来自 retired 的 active ticks, 且必须按 ctx 归属回被测应用 ——
    /// retired 事件由 GMU 线程发出, 不带应用的 comm, 不过滤就会把
    /// SurfaceFlinger 等别家的提交一起算进来。
    #[test]
    fn gpu_busy_is_attributed_by_context_not_by_comm() {
        let trace = "\
    RefbenchDrv-1   [000] ..... 100.000000: adreno_cmdbatch_submitted: ctx=41 ts=1
         gmu_f2h-2   [000] ..... 100.001000: adreno_cmdbatch_retired: ctx=41 active=19200
         gmu_f2h-2   [000] ..... 100.002000: adreno_cmdbatch_retired: ctx=99 active=192000
";
        let s = frame_stats(trace, "RefbenchDrv");
        // ctx=99 是别家的, 不算
        assert_eq!(s.gpu_busy_ms.len(), 1);
        assert!((s.gpu_busy_ms[0] - 1.0).abs() < 1e-9, "19200 ticks @19.2MHz = 1ms");
    }

    /// 提交线程名不匹配时什么都不该算出来 —— 宁可空着也不能张冠李戴。
    #[test]
    fn a_wrong_comm_filter_yields_nothing() {
        let trace = "    RefbenchDrv-1   [000] ..... 100.0: adreno_cmdbatch_submitted: ctx=41\n";
        let s = frame_stats(trace, "SomethingElse");
        assert_eq!(s.submits, 0);
        assert!(s.intervals_ms.is_empty());
    }

    #[test]
    fn out_of_order_timestamps_are_sorted_before_differencing() {
        let trace = "\
    RefbenchDrv-1   [000] ..... 100.033300: adreno_cmdbatch_submitted: ctx=41
    RefbenchDrv-1   [000] ..... 100.000000: adreno_cmdbatch_submitted: ctx=41
    RefbenchDrv-1   [000] ..... 100.016600: adreno_cmdbatch_submitted: ctx=41
";
        let s = frame_stats(trace, "RefbenchDrv");
        assert_eq!(s.intervals_ms.len(), 2);
        assert!(s.intervals_ms.iter().all(|v| *v > 0.0), "{:?}", s.intervals_ms);
    }

    #[test]
    fn an_empty_trace_yields_empty_stats() {
        let s = frame_stats("# tracer: nop\n", "RefbenchDrv");
        assert_eq!(s, FrameStats::default());
    }
}
