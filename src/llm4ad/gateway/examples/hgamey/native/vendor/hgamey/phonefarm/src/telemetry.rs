//! 遥测层 (Telemetry Spec v1.0): 每步只读采集设备/系统/App/进程性能账,供复盘回放
//! ("哪一步崩了/帧率掉了/内存涨了/网断了")。铁律: 只读采集;采不到留空不装不panic;
//! 不进模型上下文(纯账本,r=telemetry 记录);事件用两次读数差;root 开局探测降级;
//! 高频字段每步采、重量级明细按 telemetry_interval 采。
//! 数据流: device 层把整批命令写成设备端脚本一趟跑完(段间哨兵行分隔),本模块纯函数解析
//! ——每个解析器都被真机夹具(testdata/tele_a_*/tele_oh_*)钉死。
use serde::Serialize;

/// 单步遥测快照。全 Option: None=本步没采/采不到,序列化时直接省略,账本行保持紧凑。
#[derive(Default, Serialize)]
pub struct Telemetry {
    // ── 系统层 ──
    #[serde(skip_serializing_if = "Option::is_none")] pub cpu_total_pct: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub cpu_app_pct: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub load_avg: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")] pub cpu_freq_khz: Option<Vec<i64>>,
    #[serde(skip_serializing_if = "Option::is_none")] pub mem_total_kb: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub mem_avail_kb: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub commit_limit_kb: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub batt_level: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub batt_voltage_mv: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub batt_temp_c: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub batt_current_ua: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub cpu_temp: Option<Vec<(String, f32)>>,
    #[serde(skip_serializing_if = "Option::is_none")] pub gpu_info: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")] pub gpu_freq_mhz: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub gpu_util_pct: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub disk_free_kb: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub disk_total_kb: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub storage_raw: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")] pub wifi_ssid: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")] pub wifi_raw: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")] pub net_conn: Option<i32>,
    // ── 渲染/帧率层 ──
    /// **过渡字段, 将移除**: 与 `hwui_frames` (安卓) 或 `screen_frames` (鸿蒙) 同值, 只为老读者保留。
    /// 两者口径完全不同 (应用 HWUI 界面帧 vs 整屏刷新计数), 新代码请读 [`Telemetry::frame_counter`]。
    #[serde(skip_serializing_if = "Option::is_none")] pub frames_total: Option<i64>,
    /// 安卓 `dumpsys gfxinfo` 的 `Total frames rendered`: 目标应用 HWUI 画的**界面帧**累计数。
    /// 走 SurfaceView 的游戏画面不经 HWUI, 这个数在游戏里基本不涨 —— 不是游戏帧。
    #[serde(skip_serializing_if = "Option::is_none")] pub hwui_frames: Option<i64>,
    /// 鸿蒙 `hidumper -s RenderService -a fpsCount` 的计数: **整屏**刷新帧累计数, 不归属任何应用。
    #[serde(skip_serializing_if = "Option::is_none")] pub screen_frames: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub fps: Option<f32>, // 运行时按两步差值算
    /// `fps` 是按哪个计数器差出来的: `hwui` (安卓界面帧) / `screen` (鸿蒙整屏帧)。没有它 `fps` 不可解释。
    #[serde(skip_serializing_if = "Option::is_none")] pub fps_source: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")] pub janky_pct: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub frame_p50_ms: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub frame_p90_ms: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub frame_p95_ms: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub frame_p99_ms: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub missed_vsync: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub vsync_period_ns: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub refresh_hz: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub layer_count: Option<i32>,
    // ── App 层(前台目标应用) ──
    #[serde(skip_serializing_if = "Option::is_none")] pub pid: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub top_activity: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")] pub app_pss_kb: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub app_mem_raw: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")] pub threads: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub vm_rss_kb: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub vm_hwm_kb: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub anr_count: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub crash_count: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub cold_start_ms: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub batterystats_raw: Option<String>,
    // ── Root 层(开局探测,非 root 全空) ──
    #[serde(skip_serializing_if = "Option::is_none")] pub io_rchar: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub io_wchar: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub io_read_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub io_write_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub fd_count: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub socket_count: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub smaps_rss_kb: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub smaps_pss_kb: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub smaps_shared_dirty_kb: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub smaps_private_dirty_kb: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub smaps_swap_kb: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub proc_net_raw: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")] pub cgroup_raw: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")] pub dmesg_tail: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")] pub tombstone_count: Option<i32>,
    // ── 内存/CPU 压力层(PSI) ──
    #[serde(skip_serializing_if = "Option::is_none")] pub psi_cpu_some10: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub psi_mem_some10: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub psi_mem_full10: Option<f32>,
    // ── 每 App 网络流量 ──
    #[serde(skip_serializing_if = "Option::is_none")] pub uid_rx_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub uid_tx_bytes: Option<i64>,
    // ── 传感器/外设(Android) ──
    #[serde(skip_serializing_if = "Option::is_none")] pub sensors_active: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub sensors_raw: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")] pub location_raw: Option<String>,
    // ── IPC ──
    #[serde(skip_serializing_if = "Option::is_none")] pub ipc_total_count: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub ipc_raw: Option<String>,
    // ── Host 层(运行时白拿,device 不填) ──
    #[serde(skip_serializing_if = "Option::is_none")] pub step_ms: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub api_ms: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub shot_bytes: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")] pub ui_nodes: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")] pub ocr: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")] pub root: Option<bool>,
}

/// 帧计数器的口径标签。
pub const FRAMES_HWUI: &str = "hwui";
pub const FRAMES_SCREEN: &str = "screen";

impl Telemetry {
    /// 本步能用来差分算 fps 的帧计数器: `(累计帧数, 口径)`。
    /// 口径是 [`FRAMES_HWUI`] (安卓应用界面帧) 或 [`FRAMES_SCREEN`] (鸿蒙整屏帧); 两者都不是游戏帧。
    pub fn frame_counter(&self) -> Option<(i64, &'static str)> {
        if let Some(n) = self.hwui_frames { return Some((n, FRAMES_HWUI)); }
        if let Some(n) = self.screen_frames { return Some((n, FRAMES_SCREEN)); }
        // 过渡: 老读者手工填的 frames_total (口径未知, 不标)
        self.frames_total.map(|n| (n, "unknown"))
    }
}

/// 哨兵行: 设备端脚本用 `-----PF:<key>-----` 分段,一趟 shell 带回全部数据源
pub fn split_sections(raw: &str) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    let mut cur: Option<String> = None;
    let mut buf = String::new();
    for line in raw.lines() {
        let l = line.trim();
        if let Some(k) = l.strip_prefix("-----PF:").and_then(|s| s.strip_suffix("-----")) {
            if let Some(key) = cur.take() { out.push((key, std::mem::take(&mut buf))); }
            cur = Some(k.to_string());
        } else if cur.is_some() {
            buf.push_str(line);
            buf.push('\n');
        }
    }
    if let Some(key) = cur { out.push((key, buf)); }
    out
}

fn sec<'a>(ss: &'a [(String, String)], k: &str) -> Option<&'a str> {
    ss.iter().find(|(key, v)| key == k && !v.trim().is_empty()).map(|(_, v)| v.as_str())
}

/// 文本里第一个数(可带负号/小数);解析不动原文
fn first_num(s: &str) -> Option<f64> {
    let mut cur = String::new();
    for c in s.chars() {
        if c.is_ascii_digit() || (c == '-' && cur.is_empty()) || (c == '.' && cur.contains(|d: char| d.is_ascii_digit())) {
            cur.push(c);
        } else if cur.chars().any(|d| d.is_ascii_digit()) {
            break;
        } else {
            cur.clear();
        }
    }
    if cur.chars().any(|d| d.is_ascii_digit()) { cur.parse().ok() } else { None }
}

/// "Key: 123 kB" 行取数(/proc/meminfo /proc/<pid>/status smaps_rollup 通用形状)
fn kv_num(s: &str, key: &str) -> Option<i64> {
    s.lines().find(|l| l.trim_start().starts_with(key))
        .and_then(|l| first_num(&l[l.find(key)? + key.len()..]))
        .map(|v| v as i64)
}

/// 裁剪存档用原文(重量级明细按段截断,防账本膨胀)
fn raw_cut(s: &str, max: usize) -> Option<String> {
    let t = s.trim();
    if t.is_empty() { return None; }
    Some(t.chars().take(max).collect())
}

// ══════════════ 平台无关解析(/proc 系,Linux 通用) ══════════════

/// /proc/meminfo → (MemTotal, MemAvailable, CommitLimit) kB
pub fn parse_meminfo(s: &str) -> (Option<i64>, Option<i64>, Option<i64>) {
    (kv_num(s, "MemTotal:"), kv_num(s, "MemAvailable:"), kv_num(s, "CommitLimit:"))
}

/// /proc/pressure/cpu + /proc/pressure/memory 顺序拼接 → (cpu some10, mem some10, mem full10)
pub fn parse_psi(s: &str) -> (Option<f32>, Option<f32>, Option<f32>) {
    let avg10 = |line: &str| line.split("avg10=").nth(1).and_then(first_num).map(|v| v as f32);
    let somes: Vec<&str> = s.lines().filter(|l| l.trim_start().starts_with("some")).collect();
    let fulls: Vec<&str> = s.lines().filter(|l| l.trim_start().starts_with("full")).collect();
    (somes.first().and_then(|l| avg10(l)),
     somes.get(1).and_then(|l| avg10(l)),
     fulls.get(1).and_then(|l| avg10(l)))
}

/// /proc/<pid>/status(已 grep 过滤) → (Threads, VmRSS, VmHWM)
pub fn parse_status(s: &str) -> (Option<i32>, Option<i64>, Option<i64>) {
    (kv_num(s, "Threads:").map(|v| v as i32), kv_num(s, "VmRSS:"), kv_num(s, "VmHWM:"))
}

/// /proc/<pid>/io → (rchar, wchar, read_bytes, write_bytes)
pub fn parse_io(s: &str) -> (Option<i64>, Option<i64>, Option<i64>, Option<i64>) {
    (kv_num(s, "rchar:"), kv_num(s, "wchar:"), kv_num(s, "read_bytes:"), kv_num(s, "write_bytes:"))
}

/// /proc/<pid>/smaps_rollup(内核预聚合,免 awk——OH 设备无 awk 的实测教训)
/// → (Rss, Pss, Shared_Dirty, Private_Dirty, Swap) kB
pub fn parse_smaps_rollup(s: &str) -> (Option<i64>, Option<i64>, Option<i64>, Option<i64>, Option<i64>) {
    (kv_num(s, "Rss:"), kv_num(s, "Pss:"), kv_num(s, "Shared_Dirty:"),
     kv_num(s, "Private_Dirty:"), kv_num(s, "Swap:"))
}

/// 两行各一个整数(fd总数+socket数 / tcp+tcp6 连接数……wc 输出通用)
pub fn parse_two_ints(s: &str) -> (Option<i64>, Option<i64>) {
    let mut it = s.lines().filter_map(first_num);
    (it.next().map(|v| v as i64), it.next().map(|v| v as i64))
}

/// thermal_zone 的 type/温度值 交替行(毫摄氏度或分摄氏度,>1000 视为毫度) → [(名, ℃)]
pub fn parse_thermal(s: &str) -> Option<Vec<(String, f32)>> {
    let lines: Vec<&str> = s.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
    let mut v = Vec::new();
    let mut i = 0;
    while i + 1 < lines.len() {
        if let Some(raw) = first_num(lines[i + 1]) {
            if lines[i].parse::<f64>().is_err() {
                let c = if raw.abs() >= 1000.0 { raw / 1000.0 } else { raw / 10.0 };
                if (-40.0..150.0).contains(&c) { v.push((lines[i].to_string(), c as f32)); }
                i += 2;
                continue;
            }
        }
        i += 1;
    }
    if v.is_empty() { None } else { Some(v) }
}

/// cpufreq 值行(Android 直读 sysfs;仅收 ≥100MHz 的可信 kHz——模拟器无 cpufreq 时读到的
/// 杂散小数字如实丢弃,留空)
pub fn parse_cpufreq_lines(s: &str) -> Option<Vec<i64>> {
    let v: Vec<i64> = s.lines().filter_map(first_num).map(|f| f as i64)
        .filter(|f| *f >= 100_000).collect();
    if v.is_empty() { None } else { Some(v) }
}

/// df -k 输出行 → (可用 kB, 总量 kB)
pub fn parse_df(s: &str) -> (Option<i64>, Option<i64>) {
    for l in s.lines() {
        let nums: Vec<i64> = l.split_whitespace().filter_map(|t| t.parse().ok()).collect();
        if nums.len() >= 3 {
            return (Some(nums[2]), Some(nums[0])); // 1K-blocks Used Available
        }
    }
    (None, None)
}

// ══════════════ Android 侧解析 ══════════════

/// dumpsys cpuinfo(已 grep Load/TOTAL/目标包) → (load_avg, TOTAL%, 目标包%)
pub fn parse_cpuinfo_a(s: &str, pkg: &str) -> (Option<String>, Option<f32>, Option<f32>) {
    let load = s.lines().find(|l| l.trim_start().starts_with("Load:"))
        .map(|l| l.trim_start().trim_start_matches("Load:").trim().to_string());
    let total = s.lines().find(|l| l.contains("TOTAL:"))
        .and_then(first_num).map(|v| v as f32);
    let app = (!pkg.is_empty()).then(|| {
        s.lines().find(|l| l.contains(&format!("/{pkg}")))
            .and_then(first_num).map(|v| v as f32)
    }).flatten();
    (load, total, app)
}

/// dumpsys battery → (level, 电压mV, 温度℃)。Android 的 temperature 是分摄氏度(250=25.0℃)
pub fn parse_battery_a(s: &str) -> (Option<i32>, Option<i32>, Option<f32>) {
    (kv_num(s, "level:").map(|v| v as i32),
     kv_num(s, "voltage:").map(|v| v as i32),
     kv_num(s, "temperature:").map(|v| v as f32 / 10.0))
}

/// dumpsys gfxinfo(已 grep 关键行) → (帧数累计, Janky%, p50/p90/p95/p99 ms, Missed Vsync)。
/// Janky 优先非 legacy 行(新版更真)。
#[allow(clippy::type_complexity)]
pub fn parse_gfxinfo_a(s: &str) -> (Option<i64>, Option<f32>, [Option<f32>; 4], Option<i64>) {
    let frames = s.lines().find(|l| l.contains("Total frames rendered:"))
        .and_then(first_num).map(|v| v as i64);
    let janky = s.lines().find(|l| l.contains("Janky frames:") && !l.contains("legacy"))
        .and_then(|l| l.split('(').nth(1)).and_then(first_num).map(|v| v as f32);
    let pct = |k: &str| s.lines().find(|l| l.contains(k)).and_then(|l| first_num(&l[l.find(k)? + k.len()..])).map(|v| v as f32);
    let p = [pct("50th percentile:"), pct("90th percentile:"), pct("95th percentile:"), pct("99th percentile:")];
    let missed = s.lines().find(|l| l.contains("Number Missed Vsync:"))
        .and_then(|l| first_num(&l[l.find("Vsync:").unwrap() + 6..])).map(|v| v as i64);
    (frames, janky, p, missed)
}

/// am start -W 输出 → (WaitTime ms, TotalTime ms)
pub fn parse_coldstart_a(s: &str) -> (Option<i64>, Option<i64>) {
    (kv_num(s, "WaitTime:"), kv_num(s, "TotalTime:"))
}

/// dumpsys activity 的 topResumedActivity 行 → "pkg/Activity全名"
pub fn parse_topact_a(s: &str) -> Option<String> {
    let l = s.lines().find(|l| l.contains("topResumedActivity"))?;
    let tok = l.split_whitespace().find(|t| t.contains('/') && t.contains('.'))?;
    Some(tok.trim_end_matches('}').to_string())
}

/// dumpsys SurfaceFlinger 的 VSYNC 行 → (period ns, 刷新率 Hz)
pub fn parse_vsync_a(s: &str) -> (Option<i64>, Option<f32>) {
    let period = s.lines().find(|l| l.contains("VSYNC period:"))
        .and_then(|l| first_num(&l[l.find("VSYNC period:").unwrap() + 13..])).map(|v| v as i64);
    let hz = s.lines().find(|l| l.contains("refresh-rate")).and_then(first_num).map(|v| v as f32);
    (period, hz)
}

/// dumpsys wifi(已 grep) → SSID;原文另存
pub fn parse_wifi_ssid_a(s: &str) -> Option<String> {
    let l = s.lines().find(|l| l.contains("SSID:"))?;
    let after = &l[l.find("SSID:")? + 5..];
    let q: Vec<&str> = after.split('"').collect();
    q.get(1).map(|v| v.to_string())
}

/// dumpsys diskstats → (Data-Free kB, Data 总量 kB)
pub fn parse_diskstats_a(s: &str) -> (Option<i64>, Option<i64>) {
    let l = s.lines().find(|l| l.trim_start().starts_with("Data-Free:"));
    match l {
        Some(l) => {
            let nums: Vec<i64> = l.split(|c: char| !c.is_ascii_digit())
                .filter(|t| !t.is_empty()).filter_map(|t| t.parse().ok()).collect();
            (nums.first().copied(), nums.get(1).copied())
        }
        None => (None, None),
    }
}

/// dumpsys sensorservice(已 grep) → 活动传感器数
pub fn parse_sensors_a(s: &str) -> Option<i32> {
    s.lines().find(|l| l.contains("active-count"))
        .and_then(|l| first_num(&l[l.find("active-count")? + 12..])).map(|v| v as i32)
}

/// 设备端 awk 汇总的 uid 流量行 "rx:N tx:N"
pub fn parse_netsum_a(s: &str) -> (Option<i64>, Option<i64>) {
    let g = |k: &str| s.lines().find(|l| l.contains(k))
        .and_then(|l| first_num(&l[l.find(k)? + k.len()..])).map(|v| v as i64);
    (g("rx:"), g("tx:"))
}

/// dumpsys meminfo <pkg> → TOTAL PSS kB(第一个 TOTAL 行的首数,单位已是 kB)
pub fn parse_meminfo_app_a(s: &str) -> Option<i64> {
    s.lines().find(|l| l.trim_start().starts_with("TOTAL") || l.contains("TOTAL PSS:"))
        .and_then(first_num).map(|v| v as i64)
}

// ══════════════ OpenHarmony 侧解析 ══════════════

/// hidumper --cpuusage → (load_avg字符串, Total%)
pub fn parse_cpuusage_oh(s: &str) -> (Option<String>, Option<f32>) {
    let load = s.lines().find(|l| l.contains("Load average:"))
        .map(|l| l[l.find("Load average:").unwrap() + 13..].split(';').next().unwrap_or("").trim().to_string())
        .filter(|v| !v.is_empty());
    let total = s.lines().find(|l| l.trim_start().starts_with("Total:"))
        .and_then(first_num).map(|v| v as f32);
    (load, total)
}

/// hidumper --cpufreq: "cmd is: cat ...cpuN...cpuinfo_cur_freq" 与数值行成对 → 每核当前 kHz
pub fn parse_cpufreq_oh(s: &str) -> Option<Vec<i64>> {
    let mut v = Vec::new();
    let mut cur_pending = false;
    for l in s.lines() {
        let t = l.trim();
        if t.starts_with("cmd is:") {
            cur_pending = t.contains("cpuinfo_cur_freq") || t.contains("scaling_cur_freq");
        } else if cur_pending {
            if let Some(f) = first_num(t) {
                if f >= 100_000.0 { v.push(f as i64); }
                cur_pending = false;
            }
        }
    }
    if v.is_empty() { None } else { Some(v) }
}

/// hidumper -s BatteryService -a -i → (capacity, 电压mV(原值µV), 温度℃(分度), 电流µA)
pub fn parse_battery_oh(s: &str) -> (Option<i32>, Option<i32>, Option<f32>, Option<i64>) {
    (kv_num(s, "capacity:").map(|v| v as i32),
     kv_num(s, "voltage:").map(|v| (v / 1000) as i32),
     kv_num(s, "temperature:").map(|v| v as f32 / 10.0),
     kv_num(s, "nowCurrent:"))
}

/// hidumper -s RenderService -a fpsCount: "Refresh Rate:60, Count:3497" → (Hz, 累计帧)
/// OH devfreq 的 GPU 当前频率（`/sys/class/devfreq/gpufreq/cur_freq`，单位 Hz）→ MHz
pub fn parse_gpufreq_oh(s: &str) -> Option<i64> {
    first_num(s).filter(|v| *v > 0.0).map(|v| (v / 1_000_000.0).round() as i64)
}

/// OH GPU 利用率节点（`.../gpu_utilisation`，形如「Gpu utilisation : 46」，单位 %）
pub fn parse_gpuutil_oh(s: &str) -> Option<f32> {
    let l = s.lines().find(|l| l.to_ascii_lowercase().contains("utilisation") || l.trim().parse::<f64>().is_ok())?;
    let v = first_num(l.rsplit(':').next().unwrap_or(l))? as f32;
    (0.0..=100.0).contains(&v).then_some(v)
}

pub fn parse_fpscount_oh(s: &str) -> (Option<f32>, Option<i64>) {
    let l = s.lines().find(|l| l.contains("Refresh Rate:") && l.contains("Count:"));
    match l {
        Some(l) => {
            let hz = first_num(&l[l.find("Refresh Rate:").unwrap() + 13..]).map(|v| v as f32);
            let cnt = first_num(&l[l.find("Count:").unwrap() + 6..]).map(|v| v as i64);
            (hz, cnt)
        }
        None => (None, None),
    }
}

/// hidumper --mem <pid> 分类表 → Total 行的 Pss kB(无 Total 行时按分类列求和)
pub fn parse_mem_app_oh(s: &str) -> Option<i64> {
    if let Some(l) = s.lines().find(|l| l.trim_start().starts_with("Total")) {
        if let Some(v) = first_num(l) { return Some(v as i64); }
    }
    let mut sum = 0i64;
    let mut hit = false;
    for l in s.lines() {
        let t = l.trim();
        // 分类行形如 "AnonPage other  70677  4  47752 ..." 首数为该类 Pss
        if t.starts_with("GL ") || t.starts_with("Graph") || t.contains("heap")
            || t.starts_with(".") || t.starts_with("AnonPage") || t.starts_with("stack")
            || t.starts_with("FilePage") || t.starts_with("dev ") {
            if let Some(v) = first_num(t) { sum += v as i64; hit = true; }
        }
    }
    if hit { Some(sum) } else { None }
}

/// hidumper -s RenderService -a surface → 图层数(" surface [名]" 行计数)
pub fn parse_surface_oh(s: &str) -> Option<i32> {
    let c = s.lines().filter(|l| l.trim_start().starts_with("surface [")).count();
    if c > 0 { Some(c as i32) } else { None }
}

/// hidumper -e --list → 故障记录数("no records found."=0;否则记录行计数)
pub fn parse_faultlog_oh(s: &str) -> Option<i32> {
    let t = s.trim();
    if t.is_empty() { return None; }
    if t.contains("no records found") { return Some(0); }
    Some(t.lines().filter(|l| !l.trim().is_empty() && !l.contains("---")).count() as i32)
}

/// hidumper --net 头部 → (Received Bytes, Sent Bytes)
pub fn parse_net_oh(s: &str) -> (Option<i64>, Option<i64>) {
    (kv_num(s, "Received Bytes:"), kv_num(s, "Sent Bytes:"))
}

/// hidumper --ipc -a --stat → 全进程 TotalCount 求和
pub fn parse_ipc_oh(s: &str) -> Option<i64> {
    let mut sum = 0i64;
    let mut hit = false;
    for l in s.lines() {
        if l.trim_start().starts_with("TotalCount:") {
            if let Some(v) = first_num(l) { sum += v as i64; hit = true; }
        }
    }
    if hit { Some(sum) } else { None }
}

// ══════════════ 组装: 设备端脚本输出 → Telemetry ══════════════

/// Android: 分段原始输出(fast 每步;heavy 段按间隔并入同一批输出)→ 快照
pub fn from_android(raw: &str, pkg: &str) -> Telemetry {
    let ss = split_sections(raw);
    let mut t = Telemetry::default();
    if let Some(s) = sec(&ss, "pid") { t.pid = first_num(s).map(|v| v as i32); }
    if let Some(s) = sec(&ss, "meminfo") {
        let (a, b, c) = parse_meminfo(s);
        (t.mem_total_kb, t.mem_avail_kb, t.commit_limit_kb) = (a, b, c);
    }
    if let Some(s) = sec(&ss, "psi") {
        (t.psi_cpu_some10, t.psi_mem_some10, t.psi_mem_full10) = parse_psi(s);
    }
    if let Some(s) = sec(&ss, "cpuinfo") {
        let (l, tot, app) = parse_cpuinfo_a(s, pkg);
        (t.load_avg, t.cpu_total_pct, t.cpu_app_pct) = (l, tot, app);
    }
    if let Some(s) = sec(&ss, "battery") {
        let (l, v, c) = parse_battery_a(s);
        (t.batt_level, t.batt_voltage_mv, t.batt_temp_c) = (l, v, c);
    }
    if let Some(s) = sec(&ss, "cpufreq") { t.cpu_freq_khz = parse_cpufreq_lines(s); }
    if let Some(s) = sec(&ss, "thermal") { t.cpu_temp = parse_thermal(s); }
    if let Some(s) = sec(&ss, "gfxinfo") {
        let (f, j, p, m) = parse_gfxinfo_a(s);
        t.hwui_frames = f; t.frames_total = f; t.janky_pct = j; t.missed_vsync = m;
        [t.frame_p50_ms, t.frame_p90_ms, t.frame_p95_ms, t.frame_p99_ms] = p;
    }
    if let Some(s) = sec(&ss, "topact") { t.top_activity = parse_topact_a(s); }
    if let Some(s) = sec(&ss, "status") {
        let (th, rss, hwm) = parse_status(s);
        (t.threads, t.vm_rss_kb, t.vm_hwm_kb) = (th, rss, hwm);
    }
    if let Some(s) = sec(&ss, "nettcp") {
        let (a, b) = parse_two_ints(s);
        t.net_conn = match (a, b) { (Some(x), Some(y)) => Some((x + y) as i32), (Some(x), None) => Some(x as i32), _ => None };
    }
    if let Some(s) = sec(&ss, "crashcnt") { t.crash_count = first_num(s).map(|v| v as i32); }
    if let Some(s) = sec(&ss, "anrcnt") { t.anr_count = first_num(s).map(|v| v as i32); }
    if let Some(s) = sec(&ss, "io") {
        let (rc, wc, rb, wb) = parse_io(s);
        (t.io_rchar, t.io_wchar, t.io_read_bytes, t.io_write_bytes) = (rc, wc, rb, wb);
    }
    if let Some(s) = sec(&ss, "fd") {
        let (a, b) = parse_two_ints(s);
        (t.fd_count, t.socket_count) = (a.map(|v| v as i32), b.map(|v| v as i32));
    }
    // ── heavy 段 ──
    if let Some(s) = sec(&ss, "meminfo_app") {
        t.app_pss_kb = parse_meminfo_app_a(s);
        t.app_mem_raw = raw_cut(s, 900);
    }
    if let Some(s) = sec(&ss, "smaps") {
        let (rss, pss, sd, pd, sw) = parse_smaps_rollup(s);
        (t.smaps_rss_kb, t.smaps_pss_kb, t.smaps_shared_dirty_kb, t.smaps_private_dirty_kb, t.smaps_swap_kb) = (rss, pss, sd, pd, sw);
    }
    if let Some(s) = sec(&ss, "vsync") { (t.vsync_period_ns, t.refresh_hz) = parse_vsync_a(s); }
    if let Some(s) = sec(&ss, "layers") { t.layer_count = first_num(s).map(|v| v as i32); }
    if let Some(s) = sec(&ss, "wifi") {
        t.wifi_ssid = parse_wifi_ssid_a(s);
        t.wifi_raw = raw_cut(s, 500);
    }
    if let Some(s) = sec(&ss, "diskstats") { (t.disk_free_kb, t.disk_total_kb) = parse_diskstats_a(s); }
    if let Some(s) = sec(&ss, "df") {
        if t.disk_free_kb.is_none() {
            let (av, tot) = parse_df(s);
            (t.disk_free_kb, t.disk_total_kb) = (av, tot);
        }
    }
    if let Some(s) = sec(&ss, "netsum") { (t.uid_rx_bytes, t.uid_tx_bytes) = parse_netsum_a(s); }
    if let Some(s) = sec(&ss, "sensors") {
        t.sensors_active = parse_sensors_a(s);
        t.sensors_raw = raw_cut(s, 500);
    }
    if let Some(s) = sec(&ss, "location") { t.location_raw = raw_cut(s, 400); }
    if let Some(s) = sec(&ss, "gpu") { t.gpu_info = raw_cut(s, 300); }
    if let Some(s) = sec(&ss, "procnet") { t.proc_net_raw = raw_cut(s, 500); }
    if let Some(s) = sec(&ss, "cgroup") { t.cgroup_raw = raw_cut(s, 400); }
    if let Some(s) = sec(&ss, "dmesg") { t.dmesg_tail = raw_cut(s, 800); }
    if let Some(s) = sec(&ss, "tombstones") { t.tombstone_count = first_num(s).map(|v| v as i32); }
    if let Some(s) = sec(&ss, "batterystats") { t.batterystats_raw = raw_cut(s, 700); }
    t
}

/// OpenHarmony: 分段原始输出 → 快照。root 权限由设备层探测后写入。
pub fn from_oh(raw: &str) -> Telemetry {
    let ss = split_sections(raw);
    let mut t = Telemetry::default();
    if let Some(s) = sec(&ss, "pid") { t.pid = first_num(s).map(|v| v as i32); }
    if let Some(s) = sec(&ss, "meminfo") {
        let (a, b, c) = parse_meminfo(s);
        (t.mem_total_kb, t.mem_avail_kb, t.commit_limit_kb) = (a, b, c);
    }
    if let Some(s) = sec(&ss, "psi") {
        (t.psi_cpu_some10, t.psi_mem_some10, t.psi_mem_full10) = parse_psi(s);
    }
    if let Some(s) = sec(&ss, "cpuusage") {
        let (l, tot) = parse_cpuusage_oh(s);
        (t.load_avg, t.cpu_total_pct) = (l, tot);
    }
    if let Some(s) = sec(&ss, "battery") {
        let (l, v, c, cur) = parse_battery_oh(s);
        (t.batt_level, t.batt_voltage_mv, t.batt_temp_c, t.batt_current_ua) = (l, v, c, cur);
    }
    if let Some(s) = sec(&ss, "cpufreq") { t.cpu_freq_khz = parse_cpufreq_oh(s); }
    if let Some(s) = sec(&ss, "thermal") { t.cpu_temp = parse_thermal(s); }
    if let Some(s) = sec(&ss, "gpufreq") { t.gpu_freq_mhz = parse_gpufreq_oh(s); }
    if let Some(s) = sec(&ss, "gpuutil") { t.gpu_util_pct = parse_gpuutil_oh(s); }
    if let Some(s) = sec(&ss, "fpscount") {
        let (hz, cnt) = parse_fpscount_oh(s);
        (t.refresh_hz, t.screen_frames, t.frames_total) = (hz, cnt, cnt);
    }
    if let Some(s) = sec(&ss, "status") {
        let (th, rss, hwm) = parse_status(s);
        (t.threads, t.vm_rss_kb, t.vm_hwm_kb) = (th, rss, hwm);
    }
    if let Some(s) = sec(&ss, "nettcp") {
        let (a, b) = parse_two_ints(s);
        t.net_conn = match (a, b) { (Some(x), Some(y)) => Some((x + y) as i32), (Some(x), None) => Some(x as i32), _ => None };
    }
    if let Some(s) = sec(&ss, "faultcnt") { t.crash_count = parse_faultlog_oh(s); }
    if let Some(s) = sec(&ss, "io") {
        let (rc, wc, rb, wb) = parse_io(s);
        (t.io_rchar, t.io_wchar, t.io_read_bytes, t.io_write_bytes) = (rc, wc, rb, wb);
    }
    if let Some(s) = sec(&ss, "fd") {
        let (a, b) = parse_two_ints(s);
        (t.fd_count, t.socket_count) = (a.map(|v| v as i32), b.map(|v| v as i32));
    }
    // ── heavy 段 ──
    if let Some(s) = sec(&ss, "mem_app") {
        t.app_pss_kb = parse_mem_app_oh(s);
        t.app_mem_raw = raw_cut(s, 900);
    }
    if let Some(s) = sec(&ss, "smaps") {
        let (rss, pss, sd, pd, sw) = parse_smaps_rollup(s);
        (t.smaps_rss_kb, t.smaps_pss_kb, t.smaps_shared_dirty_kb, t.smaps_private_dirty_kb, t.smaps_swap_kb) = (rss, pss, sd, pd, sw);
    }
    if let Some(s) = sec(&ss, "gles") { t.gpu_info = raw_cut(s, 300); }
    if let Some(s) = sec(&ss, "surface") { t.layer_count = parse_surface_oh(s); }
    if let Some(s) = sec(&ss, "storage") { t.storage_raw = raw_cut(s, 500); }
    if let Some(s) = sec(&ss, "df") {
        let (av, tot) = parse_df(s);
        (t.disk_free_kb, t.disk_total_kb) = (av, tot);
    }
    if let Some(s) = sec(&ss, "net") { (t.uid_rx_bytes, t.uid_tx_bytes) = parse_net_oh(s); }
    if let Some(s) = sec(&ss, "ipc") {
        t.ipc_total_count = parse_ipc_oh(s);
        t.ipc_raw = raw_cut(s, 400);
    }
    if let Some(s) = sec(&ss, "procnet") { t.proc_net_raw = raw_cut(s, 500); }
    if let Some(s) = sec(&ss, "cgroup") { t.cgroup_raw = raw_cut(s, 400); }
    if let Some(s) = sec(&ss, "dmesg") { t.dmesg_tail = raw_cut(s, 800); }
    t
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn oh_gpu_freq_and_util() {
        assert_eq!(parse_gpufreq_oh("329000000\n"), Some(329));
        assert_eq!(parse_gpufreq_oh(""), None);
        assert_eq!(parse_gpuutil_oh("Gpu utilisation : 46\n"), Some(46.0));
        assert_eq!(parse_gpuutil_oh("permission denied"), None);
        let raw = "-----PF:gpufreq-----\n850000000\n-----PF:gpuutil-----\nGpu utilisation : 7\n";
        let t = from_oh(raw);
        assert_eq!((t.gpu_freq_mhz, t.gpu_util_pct), (Some(850), Some(7.0)));
    }

    const A_CPU: &str = include_str!("testdata/tele_a_cpuinfo.txt");
    const A_BAT: &str = include_str!("testdata/tele_a_battery.txt");
    const A_GFX: &str = include_str!("testdata/tele_a_gfxinfo.txt");
    const A_MEMINFO: &str = include_str!("testdata/tele_a_meminfo.txt");
    const A_MEMAPP: &str = include_str!("testdata/tele_a_meminfo_app.txt");
    const A_STATUS: &str = include_str!("testdata/tele_a_status.txt");
    const A_IO: &str = include_str!("testdata/tele_a_io.txt");
    const A_SMAPS: &str = include_str!("testdata/tele_a_smaps.txt");
    const A_PSI: &str = include_str!("testdata/tele_a_psi.txt");
    const A_COLD: &str = include_str!("testdata/tele_a_coldstart.txt");
    const A_TOP: &str = include_str!("testdata/tele_a_topact.txt");
    const A_WIFI: &str = include_str!("testdata/tele_a_wifi.txt");
    const A_VSYNC: &str = include_str!("testdata/tele_a_vsync.txt");
    const A_DISK: &str = include_str!("testdata/tele_a_diskstats.txt");
    const A_SENS: &str = include_str!("testdata/tele_a_sensors.txt");
    const O_CPU: &str = include_str!("testdata/tele_oh_cpuusage.txt");
    const O_FREQ: &str = include_str!("testdata/tele_oh_cpufreq.txt");
    const O_BAT: &str = include_str!("testdata/tele_oh_battery.txt");
    const O_THERM: &str = include_str!("testdata/tele_oh_thermal.txt");
    const O_FPS: &str = include_str!("testdata/tele_oh_fpscount.txt");
    const O_MEMAPP: &str = include_str!("testdata/tele_oh_mem_app.txt");
    const O_SURF: &str = include_str!("testdata/tele_oh_surface.txt");
    const O_FAULT: &str = include_str!("testdata/tele_oh_faultlog.txt");
    const O_NET: &str = include_str!("testdata/tele_oh_net.txt");
    const O_SMAPS: &str = include_str!("testdata/tele_oh_smaps.txt");
    const O_IPC: &str = include_str!("testdata/tele_oh_ipc.txt");

    #[test]
    fn android_parsers_pinned_by_fixtures() {
        // 真机(模拟器 root)输出逐字段钉死
        let (load, total, app) = parse_cpuinfo_a(A_CPU, "com.ss.android.article.news");
        assert_eq!(load.as_deref(), Some("0.08 / 0.33 / 0.35"));
        assert_eq!(total, Some(5.3));
        assert_eq!(app, Some(3.8));
        let (lvl, mv, tc) = parse_battery_a(A_BAT);
        assert_eq!((lvl, mv), (Some(100), Some(5000)));
        assert_eq!(tc, Some(25.0), "temperature 250 = 25.0℃(分摄氏度)");
        let (frames, janky, p, missed) = parse_gfxinfo_a(A_GFX);
        assert_eq!(frames, Some(284));
        assert_eq!(janky, Some(3.17), "取非legacy的Janky行");
        assert_eq!(p, [Some(20.0), Some(25.0), Some(32.0), Some(129.0)]);
        assert_eq!(missed, Some(5));
        let (mt, ma, cl) = parse_meminfo(A_MEMINFO);
        assert_eq!(mt, Some(2531992));
        assert_eq!(ma, Some(1314816));
        assert!(cl.is_some(), "CommitLimit 在 meminfo 后段");
        assert_eq!(parse_meminfo_app_a(A_MEMAPP).is_some(), true, "dumpsys meminfo TOTAL行");
        let (th, rss, hwm) = parse_status(A_STATUS);
        assert_eq!(th, Some(292));
        assert_eq!(rss, Some(822156));
        assert_eq!(hwm, Some(829268));
        let (rc, _, rb, wb) = parse_io(A_IO);
        assert_eq!(rc, Some(89235405));
        assert_eq!(rb, Some(279113728));
        assert_eq!(wb, Some(28897280));
        let (r, pss, _, pd, _) = parse_smaps_rollup(A_SMAPS);
        assert_eq!(r, Some(819636));
        assert_eq!(pss, Some(693584));
        assert!(pd.is_some());
        let (pc, pm, pf) = parse_psi(A_PSI);
        assert_eq!(pc, Some(0.09));
        assert_eq!(pm, Some(0.19));
        assert_eq!(pf, Some(0.19));
        let (w, tt) = parse_coldstart_a(A_COLD);
        assert_eq!((w, tt), (Some(2322), Some(2310)));
        assert_eq!(parse_topact_a(A_TOP).as_deref(),
            Some("com.ss.android.article.news/com.ss.android.newmedia.activity.browser.BrowserActivity"));
        assert_eq!(parse_wifi_ssid_a(A_WIFI).as_deref(), Some("AndroidWifi"));
        let (vp, hz) = parse_vsync_a(A_VSYNC);
        assert_eq!(vp, Some(16666666));
        assert_eq!(hz, Some(60.0));
        let (df, dt) = parse_diskstats_a(A_DISK);
        assert_eq!(df, Some(4335408));
        assert_eq!(dt, Some(10167132));
        assert_eq!(parse_sensors_a(A_SENS), Some(2));
    }

    #[test]
    fn oh_parsers_pinned_by_fixtures() {
        let (load, total) = parse_cpuusage_oh(O_CPU);
        assert_eq!(load.as_deref(), Some("11.2 / 11.2 / 11.2"));
        assert_eq!(total, Some(11.28));
        let freqs = parse_cpufreq_oh(O_FREQ).unwrap();
        assert!(freqs.contains(&2704000), "cpu7 当前频率 2.7GHz");
        let (cap, mv, tc, cur) = parse_battery_oh(O_BAT);
        assert_eq!(cap, Some(85));
        assert_eq!(mv, Some(4190), "voltage 4190000µV → 4190mV");
        assert_eq!(tc, Some(20.0));
        assert_eq!(cur, Some(0));
        let temps = parse_thermal(O_THERM).unwrap();
        assert!(temps.iter().any(|(n, c)| n == "soc-thmzone" && (*c - 52.84).abs() < 0.01),
            "soc 52840 毫摄氏度 → 52.84℃");
        let (hz, cnt) = parse_fpscount_oh(O_FPS);
        assert_eq!(hz, Some(60.0));
        assert_eq!(cnt, Some(3497));
        assert!(parse_mem_app_oh(O_MEMAPP).unwrap() > 0, "分类表求出 Pss");
        assert_eq!(parse_surface_oh(O_SURF), Some(1), "RosenWeb 一层");
        assert_eq!(parse_faultlog_oh(O_FAULT), Some(0), "'no records found.'=0 崩溃");
        let (rx, tx) = parse_net_oh(O_NET);
        assert_eq!((rx, tx), (Some(0), Some(0)), "设备未联网如实为0");
        let (r, p, _, _, _) = parse_smaps_rollup(O_SMAPS);
        assert_eq!(r, Some(380480));
        assert_eq!(p, Some(171378));
        assert_eq!(parse_ipc_oh(O_IPC), Some(0), "各进程 TotalCount 求和");
    }

    #[test]
    fn sectioned_assembly_and_graceful_empty() {
        // 哨兵分段组装 + 采不到留空(空输入→全None,序列化紧凑)
        let raw = format!(
            "-----PF:pid-----\n1640\n-----PF:psi-----\n{A_PSI}\n-----PF:battery-----\n{A_BAT}\n-----PF:gfxinfo-----\n{A_GFX}");
        let t = from_android(&raw, "com.ss.android.article.news");
        assert_eq!(t.pid, Some(1640));
        assert_eq!(t.batt_level, Some(100));
        assert_eq!(t.hwui_frames, Some(284), "安卓 gfxinfo 是 HWUI 界面帧");
        assert_eq!(t.screen_frames, None, "安卓没有整屏计数");
        assert_eq!(t.frames_total, Some(284), "过渡字段与 hwui_frames 同值");
        assert_eq!(t.frame_counter(), Some((284, FRAMES_HWUI)));
        assert!(t.mem_total_kb.is_none(), "没采的段留空");
        let empty = from_android("", "x");
        let j = serde_json::to_value(&empty).unwrap();
        assert_eq!(j.as_object().unwrap().len(), 0, "全None序列化为空对象,账本零负担");
        let t2 = from_oh(&format!("-----PF:fpscount-----\n{O_FPS}\n-----PF:faultcnt-----\n{O_FAULT}"));
        assert_eq!(t2.screen_frames, Some(3497), "鸿蒙 fpsCount 是整屏刷新计数");
        assert_eq!(t2.hwui_frames, None, "鸿蒙没有 HWUI 计数");
        assert_eq!(t2.frames_total, Some(3497), "过渡字段与 screen_frames 同值");
        assert_eq!(t2.frame_counter(), Some((3497, FRAMES_SCREEN)));
        let j2 = serde_json::to_value(&t2).unwrap();
        assert!(j2.get("screen_frames").is_some() && j2.get("hwui_frames").is_none(), "账本里两个口径分开落键");
        assert_eq!(t2.crash_count, Some(0));
        assert_eq!(t2.root, None, "原始输出不含权限信息,由设备层写入");
    }
}
