//! hwcond: 硬件条件化与功耗遥测 —— 被多条上层通路共享的通用能力。
//!
//! 为什么单独成模块: `bench`(TFLite 标尺) 与 `gpu-op`(Compute Shader 标尺) 都需要
//! 「等冷 → 锁频 → 测 → 立刻解锁 → 回读校验」这同一套设备条件化流程。
//! 金科玉律「上层各通路互不依赖」禁止 gpu-op 去 import bench 的私有函数;
//! 同一段逻辑抄两份又必然随时间漂移, 违反「遥测口径全局统一」。
//! 故按规则把它提为共享能力: 谁都能用, 谁都不拥有。
//!
//! 本模块只做两件事, 都是设备状态的读与受控写:
//!   1. **条件化**: 热区读取 / 冷机门禁 / CPU 调速器与 kgsl 档位与总线下限的锁定与还原;
//!   2. **功耗遥测**: 从 power_supply 电源轨读瞬时功率。
//!
//! 纪律 (与 bench 原有口径完全一致, 代码由 bench.rs 原样迁出):
//!   - 所有 sysfs 写入在退出前恢复原值, 无论中途成功失败;
//!   - 锁前先把快照落到本机状态文件, 进程被杀也能由下次启动回滚;
//!   - 解析全是纯函数, 同一份输入每次重算逐字节一致。

use std::time::{Duration, Instant};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

/// Minimal transport boundary for hardware conditioning under a caller-owned lease.
pub(crate) trait Shell {
    fn shell(&self, cmd: &str, ms: u64) -> String;
    fn shell_write(&self, cmd: &str, ms: u64) -> String { self.shell(cmd, ms) }
    fn check(&self) -> Result<(), String> { Ok(()) }
    fn charge_node(&self, path: &str) -> Result<Option<String>, String> {
        let value=self.shell(&format!("su -c 'cat {path} 2>/dev/null'"),8_000);
        self.check()?;
        Ok((!value.trim().is_empty()).then(||value.trim().to_owned()))
    }
    fn charge_battery(&self) -> Result<BatteryState, String> {
        let state=read_battery_state(self);self.check()?;Ok(state)
    }
}

impl Shell for crate::device::Device {
    fn shell(&self, cmd: &str, ms: u64) -> String {
        crate::device::Device::shell(self, cmd, ms)
    }
}

pub const KGSL: &str = "/sys/class/kgsl/kgsl-3d0";
pub const MTK_GED: &str = "/sys/kernel/ged/hal";
pub const MTK_GPUFREQ: &str = "/proc/gpufreqv2";
pub const CPUFREQ: &str = "/sys/devices/system/cpu/cpufreq";
/// 内存总线 DCVS (Qualcomm bus_dcvs): DDR 与 LLCC 的用户态下限 boost_freq 在跑测期间钉到 hw_max_freq
/// (hw_min_freq 是内核私有只读节点, root 也写不进)。
/// 反例 (2026-09-09): DDR 待机 547MHz 随负载爬升, resize 这类访存算子轮间离散 5.3%; 钉住后消除。
pub const BUS_DCVS: &str = "/sys/devices/system/cpu/bus_dcvs";
pub const BUS_NODES: [&str; 2] = ["DDR", "LLCC"];
/// 热区前缀: SoC 结温 (CPU 各核 / CPU LLC / GPU 子系统); 皮肤温等外壳传感器不参与冷机判定
pub const SOC_ZONE_PREFIXES: [&str; 3] = ["cpu-", "cpullc", "gpuss"];

/// 进度一律走 stderr: `--json` 时 stdout 只有 JSON, 而卡在哪一步必须能被看见
/// (等冷死循环的教训)。tag 由调用方给, 便于分辨是哪条通路在等。
pub fn progress(tag: &str, msg: &str) {
    eprintln!("[{tag}] {msg}");
}

/// 与 lease 共用持久根；解析不创建目录，错误不回退临时目录。
pub fn state_dir() -> Result<PathBuf, String> {
    crate::lease_storage::state_root()
}

/// 锁频前的设备快照落到本机状态文件 (按通路与 serial 区分);
/// 进程被杀 (含宿主机崩溃重启) 留下的锁态由下次启动或 `--unlock` 回滚。
pub fn lock_state_path(tag: &str, serial: &Option<String>) -> Result<PathBuf, String> {
    recovery_path(&state_dir()?, tag, serial.as_deref(),
        &[std::env::temp_dir(), PathBuf::from("/tmp")])
}

fn recovery_path(root: &Path, tag: &str, serial: Option<&str>, old_roots: &[PathBuf]) -> Result<PathBuf, String> {
    let serial = serial.filter(|s| !s.is_empty()).ok_or("recovery state requires a bound serial")?;
    if tag.is_empty() || !tag.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-') {
        return Err("invalid recovery state tag".into());
    }
    let mut encoded = String::new();
    for b in serial.bytes() {
        if b.is_ascii_alphanumeric() { encoded.push(b as char); }
        else { encoded.push_str(&format!("%{b:02X}")); }
    }
    let path = root.join(format!("phonefarm-{tag}-lock-{encoded}.txt"));
    let old: String = serial.chars().map(|c| if c.is_ascii_alphanumeric() { c } else { '_' }).collect();
    let old_name = format!("phonefarm-{tag}-lock-{old}.txt");
    for dir in std::iter::once(root).chain(old_roots.iter().map(PathBuf::as_path)) {
        let dir = match dir.canonicalize() {
            Ok(p) => p,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => dir.to_path_buf(),
            Err(e) => return Err(format!("legacy recovery directory {}: {e}", dir.display())),
        };
        for name in [&old_name, &format!("phonefarm-{tag}-lock-default.txt")] {
            let legacy = dir.join(name);
            if legacy == path { continue; }
            match fs::symlink_metadata(&legacy) {
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {},
                Err(e) => return Err(format!("legacy recovery record {}: {e}", legacy.display())),
                Ok(_) => return Err(format!("legacy recovery record {}; reconcile its device and saved state before using {} (record preserved)", legacy.display(), path.display())),
            }
        }
    }
    Ok(path)
}

fn read_state(path: &Path) -> Result<Option<String>, String> {
    match fs::symlink_metadata(path) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("read recovery record {}: {e}", path.display())),
        Ok(m) if !m.is_file() || m.len() > 65536 =>
            return Err(format!("recovery record is not a bounded regular file: {}", path.display())),
        Ok(_) => {},
    }

    let text = fs::read_to_string(path).map_err(|e| format!("read recovery record {}: {e}", path.display()))?;
    if let Some(record) = text.strip_prefix("phonefarm-recovery@1 ") {
        let (length, body) = record.split_once('\n')
            .ok_or_else(|| format!("incomplete recovery record {}; preserved", path.display()))?;
        if length.parse::<usize>().ok() != Some(body.len()) {
            return Err(format!("truncated recovery record {}; preserved", path.display()));
        }
        Ok(Some(body.into()))
    } else {
        // Existing plaintext records remain recoverable under strict parsing.
        Ok(Some(text))
    }
}

// Persist newly created directories as well as the record. A partial create is
// deliberately retained on error: no device mutation has been allowed yet.
fn ensure_state_parent(dir: &Path) -> std::io::Result<()> {
    if dir.is_dir() { return Ok(()); }
    let parent = dir.parent().ok_or_else(|| std::io::Error::other("state directory has no parent"))?;
    ensure_state_parent(parent)?;
    match fs::create_dir(dir) {
        Ok(()) => {},
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists && dir.is_dir() => {},
        Err(e) => return Err(e),
    }
    File::open(parent)?.sync_all()
}

fn save_state(path: &Path, text: &str) -> Result<(), String> {
    let save = || -> std::io::Result<()> {
        let parent = path.parent().ok_or_else(|| std::io::Error::other("state record has no parent"))?;
        ensure_state_parent(parent)?;
        let mut file = OpenOptions::new().write(true).create_new(true).mode(0o600).open(path)?;

        write!(file, "phonefarm-recovery@1 {}\n{text}", text.len())?;
        file.sync_all()?;
        File::open(parent)?.sync_all()
    };
    save().map_err(|e| format!("cannot persist new recovery record {}: {e}; existing/partial record preserved, device not changed", path.display()))
}

fn clear_state(path: &Path) -> Result<(), String> {
    fs::remove_file(path).or_else(|e| if e.kind() == std::io::ErrorKind::NotFound { Ok(()) } else { Err(e) })
        .and_then(|_| File::open(path.parent().unwrap()).and_then(|p| p.sync_all()))
        .map_err(|e| format!("cannot durably clear recovery record {}: {e}", path.display()))
}

fn clear_restored_state(path: &Path) -> bool {
    match clear_state(path) {
        Ok(()) => true,
        Err(e) => { progress("restore", &e); false },
    }
}

// ══════════════ 设备快照 ══════════════

#[derive(Debug, Clone, PartialEq)]
pub struct CpuPolicy {
    pub path: String,
    pub governor: String,
    pub min_khz: u64,
    pub max_khz: u64,
    pub cur_khz: u64,
    pub cpus: String,
}

#[derive(Debug, Clone, PartialEq)]
/// floor_khz = boost_freq (用户态下限), max_khz = hw_max_freq, cur_khz = cur_freq
pub struct BusNode {
    pub name: String,
    pub floor_khz: u64,
    pub max_khz: u64,
    pub cur_khz: u64,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Snapshot {
    pub cpu: Vec<CpuPolicy>,
    /// bus_dcvs 节点 (DDR / LLCC), 没有该 sysfs 的内核为空
    pub bus: Vec<BusNode>,
    pub gpu_max_level: Option<u32>,
    pub gpu_min_level: Option<u32>,
    pub gpu_thermal_level: Option<u32>,
    pub gpuclk_hz: Option<u64>,
    pub gpu_model: Option<String>,
    /// 按档位索引排列的 GPU 频率表 (Hz), 索引 0 = 最高档
    pub gpu_freqs: Vec<u64>,
    /// Original GED user limits. Effective PPM limits above may also include
    /// thermal/vendor constraints and must never be written back as user limits.
    pub mtk_ged: Option<(u32, u32)>,
    pub mtk_stackclk_hz: Option<u64>,
}

/// 快照命令 (root): 一次 su 往返把 CPU 各簇 / kgsl 档位 / 频率表全采回
pub fn snapshot_cmd() -> String {
    format!(
        "su -c 'for p in {CPUFREQ}/policy*; do echo CPU $p $(cat $p/scaling_governor) $(cat $p/scaling_min_freq) $(cat $p/scaling_max_freq) $(cat $p/scaling_cur_freq) $(cat $p/related_cpus | tr \" \" ,); done; \
K={KGSL}; if [ -r $K/max_pwrlevel ]; then \
echo GPU $(cat $K/max_pwrlevel) $(cat $K/min_pwrlevel) $(cat $K/thermal_pwrlevel) $(cat $K/gpuclk) $(cat $K/gpu_model); \
echo GPUFREQS $(cat $K/gpu_available_frequencies); \
elif [ -r {MTK_GPUFREQ}/gpufreq_status ] && [ -r {MTK_GED}/custom_boost_gpu_freq ] && [ -r {MTK_GED}/custom_upbound_gpu_freq ]; then \
echo MTK_STATUS_BEGIN; cat {MTK_GPUFREQ}/gpufreq_status; echo MTK_STATUS_END; \
echo MTK_GED $(head -n 1 {MTK_GED}/custom_upbound_gpu_freq) $(head -n 1 {MTK_GED}/custom_boost_gpu_freq); \
echo MTK_OPP_BEGIN; cat {MTK_GPUFREQ}/gpu_working_opp_table; echo MTK_OPP_END; fi; \
for b in {}; do d={BUS_DCVS}/$b; [ -d $d ] && echo BUS $b $(cat $d/boost_freq) $(cat $d/hw_max_freq) $(cat $d/cur_freq); done'",
        BUS_NODES.join(" ")
    )
}

pub fn parse_snapshot(text: &str) -> Snapshot {
    let mut s = Snapshot::default();
    let (mut mtk_status, mut mtk_opps) = (false, false);
    for line in text.lines() {
        match line.trim() {
            "MTK_STATUS_BEGIN" => { mtk_status = true; s.gpu_model = Some("MediaTek-Mali".into()); continue; }
            "MTK_STATUS_END" => { mtk_status = false; continue; }
            "MTK_OPP_BEGIN" => { mtk_opps = true; continue; }
            "MTK_OPP_END" => { mtk_opps = false; continue; }
            _ => {}
        }
        let number_after = |key: &str| -> Option<u64> {
            line.split_once(key)?.1.split_whitespace().next()?.trim_end_matches(',').parse().ok()
        };
        if mtk_status {
            if line.starts_with("[PPM Ceiling]") {
                s.gpu_max_level = number_after("LimitIndex:").and_then(|n| n.try_into().ok());
            } else if line.starts_with("[PPM Floor]") {
                s.gpu_min_level = number_after("LimitIndex:").and_then(|n| n.try_into().ok());
            } else if line.starts_with("[GPU   OPP]") {
                s.gpuclk_hz = number_after("Freq:").and_then(|khz| khz.checked_mul(1000));
            } else if line.starts_with("[STACK OPP]") {
                s.mtk_stackclk_hz = number_after("Freq:").and_then(|khz| khz.checked_mul(1000));
            }
            continue;
        }
        if mtk_opps {
            let index = line.split_once(']').and_then(|(s, _)| s.strip_prefix('[')).and_then(|n| n.parse::<usize>().ok());
            if index == Some(s.gpu_freqs.len()) {
                if let Some(hz) = number_after("freq:").and_then(|khz| khz.checked_mul(1000)) {
                    s.gpu_freqs.push(hz);
                }
            }
            continue;
        }
        let cols: Vec<&str> = line.split_whitespace().collect();
        match cols.first().copied() {
            Some("CPU") if cols.len() >= 6 => s.cpu.push(CpuPolicy {
                path: cols[1].to_string(),
                governor: cols[2].to_string(),
                min_khz: cols[3].parse().unwrap_or(0),
                max_khz: cols[4].parse().unwrap_or(0),
                cur_khz: cols[5].parse().unwrap_or(0),
                cpus: cols.get(6).unwrap_or(&"").to_string(),
            }),
            Some("GPU") if cols.len() >= 5 => {
                s.gpu_max_level = cols[1].parse().ok();
                s.gpu_min_level = cols[2].parse().ok();
                s.gpu_thermal_level = cols[3].parse().ok();
                s.gpuclk_hz = cols[4].parse().ok();
                s.gpu_model = cols.get(5).map(|v| v.to_string());
            }
            Some("GPUFREQS") => {
                s.gpu_freqs = cols[1..].iter().filter_map(|v| v.parse().ok()).collect()
            }
            Some("MTK_GED") if cols.len() == 3 => {
                s.mtk_ged = cols[1].parse().ok().zip(cols[2].parse().ok());
            }
            Some("MTK_STACK_HZ") if cols.len() == 2 => {
                s.mtk_stackclk_hz = cols[1].parse().ok();
            }
            Some("BUS") if cols.len() >= 5 => s.bus.push(BusNode {
                name: cols[1].to_string(),
                floor_khz: cols[2].parse().unwrap_or(0),
                max_khz: cols[3].parse().unwrap_or(0),
                cur_khz: cols[4].parse().unwrap_or(0),
            }),
            _ => {}
        }
    }
    s
}

/// 热区行 "type temp_millidegree" → (type, 毫摄氏度)
pub fn parse_thermal(text: &str) -> Vec<(String, i64)> {
    text.lines()
        .filter_map(|l| {
            let mut it = l.split_whitespace();
            let ty = it.next()?;
            let temp: i64 = it.next()?.parse().ok()?;
            Some((ty.to_string(), temp))
        })
        .collect()
}

/// SoC 结温最高的热区 (只看 cpu-/cpullc/gpuss 前缀; 0 或 >=100C 的哨兵值跳过)
pub fn soc_max_c(zones: &[(String, i64)]) -> Option<(String, f64)> {
    zones
        .iter()
        .filter(|(ty, t)| {
            (SOC_ZONE_PREFIXES.iter().any(|p| ty.starts_with(p))
                || matches!(ty.as_str(), "gpu0" | "gpu1" | "soc_max")
                || ty.starts_with("soc-top") || ty.starts_with("soc-bot")) && *t > 0 && *t < 100_000
        })
        .max_by_key(|(_, t)| *t)
        .map(|(ty, t)| (ty.clone(), *t as f64 / 1000.0))
}

/// 目标 MHz → 频率表里最接近的档位索引
pub fn nearest_level(freqs: &[u64], mhz: u64) -> Option<u32> {
    let target = mhz * 1_000_000;
    freqs
        .iter()
        .enumerate()
        .min_by_key(|(_, f)| (**f as i64 - target as i64).abs())
        .map(|(i, _)| i as u32)
}

/// (Max-Min)/Median, 百分比; 样本不足 2 个记 0
pub fn dispersion_pct(vals: &[f64]) -> (f64, f64, f64, f64) {
    let mut v: Vec<f64> = vals.iter().copied().filter(|x| x.is_finite()).collect();
    if v.is_empty() {
        return (0.0, 0.0, 0.0, 0.0);
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = v.len();
    let median = if n % 2 == 1 {
        v[n / 2]
    } else {
        (v[n / 2 - 1] + v[n / 2]) / 2.0
    };
    let (min, max) = (v[0], v[n - 1]);
    let disp = if median > 0.0 && n >= 2 {
        (max - min) / median * 100.0
    } else {
        0.0
    };
    (median, min, max, disp)
}

/// 采样器输出 "gpuclk cpu0 cpu6 ..." 行 → 各列的 (众数, 最小, 最大)
pub fn parse_samples(text: &str) -> Vec<(u64, u64, u64)> {
    let rows: Vec<Vec<u64>> = text
        .lines()
        .map(|l| {
            l.split_whitespace()
                .filter_map(|v| v.parse().ok())
                .collect::<Vec<u64>>()
        })
        .filter(|r| !r.is_empty())
        .collect();
    let Some(width) = rows.iter().map(|r| r.len()).min() else {
        return vec![];
    };
    (0..width)
        .map(|c| {
            let col: Vec<u64> = rows.iter().map(|r| r[c]).collect();
            let mut counts: std::collections::HashMap<u64, usize> = Default::default();
            for v in &col {
                *counts.entry(*v).or_default() += 1;
            }
            let mode = counts
                .iter()
                .max_by_key(|(v, n)| (**n, **v))
                .map(|(v, _)| *v)
                .unwrap_or(0);
            (mode, *col.iter().min().unwrap(), *col.iter().max().unwrap())
        })
        .collect()
}

// ══════════════ 功耗遥测 ══════════════

/// 功率采样口径。两条轨各有各的适用条件, 不可混用。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PowerRail {
    /// USB 输入轨 (`power_supply/usb`): 只读, 不动任何充电状态。
    ///
    /// 量的是**墙上抽走的功率**, 含充电与转换损耗。电池充满且停充时它最接近整机功耗,
    /// 正在充电时会把充电功率一起算进去, 此时不可用于对比。
    Usb,
    /// 电池轨 (`power_supply/battery/power_now`): 量的是设备从电池真实抽走的功率。
    ///
    /// 只有设备**处于放电态**时才有非零读数; 插着 USB 充电时恒为 0。
    Battery,
}

impl PowerRail {
    pub fn as_str(&self) -> &'static str {
        match self {
            PowerRail::Usb => "usb",
            PowerRail::Battery => "battery",
        }
    }
}

/// 一次功率采样。
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PowerSample {
    pub volt_uv: i64,
    pub curr_ua: i64,
    /// 设备直接给出的瞬时功率 (微瓦); 没有该节点时为 None
    pub power_uw: Option<i64>,
}

impl PowerSample {
    /// 瓦特。**优先 V x I**, 只有电压或电流缺失时才退回设备直报的 `power_now`。
    ///
    /// 取绝对值: 放电电流在不同内核里有正有负 (有的用负数表示放电),
    /// 功率的**大小**才是我们要的量。
    ///
    /// 为什么不信 `power_now`
    /// ----------------------
    /// 它曾经是首选。2026-09-23 在红魔 NX809J 上与 HiSmartPerf 通路做交叉对比时发现:
    /// 同一时刻 `battery/power_now` 读出 `992195296` (按 μW 折算 992 W),
    /// 而 `voltage_now x current_now` 只有 0.185 W。整段 30 秒窗口里 150 条采样**全部**
    /// 越界, 最大 777 W —— 这个节点在这台机器上给的根本不是 μW。
    ///
    /// 两条通路因此对同一段窗口给出了完全不同的功耗: 我们报 777 W, HiSmartPerf 报 3.09 W。
    /// 差异的唯一来源就是这里 —— HiSmartPerf 的设备端采集器只读
    /// `current_now` / `voltage_now`, 压根不碰 `power_now`。
    ///
    /// `power_now` 是可选节点, 各家内核填得五花八门 (有的 μW, 有的 nW, 有的干脆没初始化);
    /// 而 `voltage_now`(μV) / `current_now`(μA) 的单位在 power_supply class 里是有明确约定的。
    /// 所以口径改成: **能算 V x I 就算 V x I**, 两条通路从此量的是同一个东西。
    ///
    /// 电压或电流为 0 时返回 None, **不再退回 `power_now`** (2026-10-21 审计 #15):
    /// 本机 `power_now` 实测读到过 537393296 与 −61082000, 一旦某条样本电流恰好为 0
    /// 就会把这种数折成几百瓦或负数混进来, 再被逐条越界检查把整组功耗作废。
    /// 电流为 0 只说明「这一刻这条轨没在量东西」, 记成缺测, 不记成任何瓦数。
    pub fn watt(&self) -> Option<f64> {
        if self.volt_uv != 0 && self.curr_ua != 0 {
            return Some((self.volt_uv as f64 / 1.0e6).abs() * (self.curr_ua as f64 / 1.0e6).abs());
        }
        None
    }
}

/// 采样命令: 一次 su 往返读一条轨的电压/电流/功率。
pub fn power_sample_cmd(rail: PowerRail) -> String {
    let p = format!("/sys/class/power_supply/{}", rail.as_str());
    format!(
        "su -c 'echo PWR $(cat {p}/voltage_now 2>/dev/null || echo 0) \
$(cat {p}/current_now 2>/dev/null || echo 0) \
$(cat {p}/power_now 2>/dev/null || echo NA)'"
    )
}

/// 解析 `PWR <voltage_uv> <current_ua> <power_uw|NA>` 行。
pub fn parse_power_samples(text: &str) -> Vec<PowerSample> {
    text.lines()
        .filter_map(|l| {
            let c: Vec<&str> = l.split_whitespace().collect();
            if c.first().copied() != Some("PWR") || c.len() < 3 {
                return None;
            }
            Some(PowerSample {
                volt_uv: c[1].parse().ok()?,
                curr_ua: c[2].parse().ok()?,
                power_uw: c.get(3).and_then(|v| v.parse::<i64>().ok()),
            })
        })
        .collect()
}

/// 一组采样的功率统计 (瓦)。返回 (均值, 中位数, 最小, 最大)。
///
/// 电压或电流为 0 的样本 ([`PowerSample::watt`] 为 None) 不进统计; 一条都算不出时返回 None。
pub fn power_stats(samples: &[PowerSample]) -> Option<(f64, f64, f64, f64)> {
    let mut w: Vec<f64> = samples.iter().filter_map(|s| s.watt()).collect();
    if w.is_empty() {
        return None;
    }
    let mean = w.iter().sum::<f64>() / w.len() as f64;
    w.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = w.len();
    let median = if n % 2 == 1 {
        w[n / 2]
    } else {
        (w[n / 2 - 1] + w[n / 2]) / 2.0
    };
    Some((mean, median, w[0], w[n - 1]))
}

/// 功率读数可不可信。
///
/// 这层判断是必需的: 插着 USB 充电时电池轨恒为 0, 一个 0.000 W 的"实测功耗"
/// 看起来像个数字, 其实是「这条轨此刻量不了」。不做这层, 报告就会拿 0 当结论。
/// 一台手机的整机功率可信区间 (瓦)。
///
/// 这不是"调参", 是物理边界: 手游满载的整机功率在个位数瓦到十几瓦, 上不了 30 W;
/// 低于 50 mW 则意味着这条轨此刻根本没在量东西。**三条采集通路共用这一把尺子** ——
/// 同一个物理量在不同通路上用不同的判据, 迟早会出现"同一台机器 A 通路说可信、
/// B 通路说不可信"的分裂。
pub const POWER_MIN_W: f64 = 0.05;
pub const POWER_MAX_W: f64 = 30.0;

pub fn power_usable(rail: PowerRail, samples: &[PowerSample]) -> Result<(), String> {
    power_usable_with_battery(rail, samples, None)
}

/// 同上, 但带上这一轮的电池状态。**有电池状态就必须传** —— 这是唯一拦得住
/// 「充电态下读数看起来完全合理」的那道闸。
///
/// 为什么可信区间不够: 2026-09-23 实测, 插着 USB 时
///   - 电池轨读出 `0.213 W` / `3.086 W` —— 稳稳落在 0.05..30 W 窗口里;
///   - USB 轨读出 `7.20 W`, 而同刻电池正以 `3.33 W` 在充电 ——
///     也就是说这 7.2 W 里有 46% 是灌进电池的, 根本不是整机在吃的功率。
/// 两个数字都"合理", 区间一个都拦不住。而一个看起来合理的错数比一个越界的错数
/// 危险得多: 越界的会被拦下, 合理的会被下游当成实测功耗拿去做 A/B 裁决。
///
/// 更要命的是充电电流会随电量上升自己衰减 (CC→CV), 是一条**单调漂移** ——
/// 跨时间比较的两个候选之间会凭空多出一个"功耗改善"。
/// (game_opt_loop 的 M1 演化 6.26W→5.94W 就是这么来的, 已撤回。)
pub fn power_usable_with_battery(
    rail: PowerRail,
    samples: &[PowerSample],
    battery: Option<&BatteryState>,
) -> Result<(), String> {
    power_usable_supply(rail, samples, battery, false)
}

/// 「电压或电流为 0、算不出瓦数」的样本最多占这么多; 再多就说明这条轨这段窗口没在量东西。
pub const POWER_MISSING_MAX_FRAC: f64 = 0.1;

/// 同 [`power_usable_with_battery`], 另带「这一轮有没有停充」。供电判定走 [`classify_supply`],
/// 与 `sysparam::env_stats` 是同一个函数 (2026-10-21 审计 #15: 以前这条路径只认放电态,
/// 满电停充后的 USB 旁路永远判不过)。
///
/// 这条通路一次只采一条轨, 所以只认两种「单轨就是整机」的情形:
/// 电池轨 + 放电态, USB 轨 + 旁路 (已停充, 电池不进不出)。混合供电 (USB 不够, 电池同时在补)
/// 要两轨同刻相加, 单轨量不全, 明确报「供电不足」而不是交一个偏小的数。
pub fn power_usable_supply(
    rail: PowerRail,
    samples: &[PowerSample],
    battery: Option<&BatteryState>,
    charge_suspended: bool,
) -> Result<(), String> {
    if let Some(b) = battery {
        let usb_w = match rail {
            PowerRail::Usb => power_stats(samples).map(|t| t.0),
            PowerRail::Battery => None,
        };
        let mode = classify_supply(&SupplyObs {
            status: &b.status,
            battery_ua: b.current_ua.map(|c| c as f64),
            usb_w,
            charge_suspended,
            bypass_mode: false,
        });
        match (rail, mode) {
            (PowerRail::Battery, SupplyMode::Battery) | (PowerRail::Usb, SupplyMode::Bypass) => {}
            (_, SupplyMode::Mixed) => {
                return Err(format!(
                    "供电不足 (混合供电): {}, USB 输入不够, 电池同时在放电。整机功耗要 USB 与电池两轨\
同一时刻相加, 这条通路只采 {} 一条轨, 量不全。换功率更大的 USB 电源, 或走 eval (sample_env 两轨同采)",
                    b.why_not_on_battery(),
                    rail.as_str()
                ));
            }
            (PowerRail::Usb, SupplyMode::Battery) => {
                return Err("设备此刻由电池供电, USB 轨量不到整机功耗。改用 --power-rail battery".into());
            }
            (PowerRail::Battery, SupplyMode::Bypass) => {
                return Err("旁路供电 (已停充, 整机由 USB 直供, 电池不进不出): 电池轨没有读数。改用 --power-rail usb".into());
            }
            (_, SupplyMode::FullIdle) => {
                return Err(format!(
                    "电池已满且不进不出 ({}): 整机由 USB 供电, 但没停充, 充电器随时会补电。\
用 `--suspend-charging` 停充后按 USB 旁路量",
                    b.why_not_on_battery()
                ));
            }
            (_, SupplyMode::NotDischarging) | (_, SupplyMode::Unknown) => {
                return Err(format!(
                    "{}: {}。要量整机功耗必须让设备处于放电态 —— 拔掉 USB, 或用 `--suspend-charging` 在测量期间停充 (测完自动恢复)",
                    match rail {
                        PowerRail::Battery => "电池轨此刻量不了整机功耗",
                        PowerRail::Usb =>
                            "USB 输入轨此刻量的不是整机功耗 (输入功率里含着给电池充电的那一份)",
                    },
                    b.why_not_on_battery()
                ));
            }
        }
    }
    if samples.is_empty() {
        return Err("没有取到任何功率采样".into());
    }
    let Some((mean, _, _, _)) = power_stats(samples) else {
        return Err(match rail {
            PowerRail::Battery => "电池轨功率恒为 0: 设备正插着 USB 充电。\
                 要量整机功耗需让设备处于放电态 (拔掉充电或停充), 或改用 --power-rail usb"
                .into(),
            PowerRail::Usb => "USB 轨功率恒为 0: 设备未通过 USB 供电 (可能走 Wi-Fi adb)。\
                 改用 --power-rail battery"
                .into(),
        });
    };
    // 电流/电压为 0 的样本不折成任何瓦数 (watt() 为 None), 但占比太高说明这段窗口没在量
    let missing = samples.iter().filter(|s| s.watt().is_none()).count();
    if missing as f64 > POWER_MISSING_MAX_FRAC * samples.len() as f64 {
        return Err(format!(
            "{missing} / {} 条功率样本的电压或电流为 0, 算不出瓦数 (超过 {:.0}%): 这条轨这段窗口没在稳定地量东西。\
不退回 power_now —— 本机那个节点给的不是 μW",
            samples.len(),
            POWER_MISSING_MAX_FRAC * 100.0
        ));
    }
    // 上界必须查, 而且要逐条查。
    //
    // 2026-09-23 实测这台红魔: `/sys/class/power_supply/battery/power_now` 读出
    // `992195296`, 按 μW 折算就是 992 W —— 而同一时刻 current_now x voltage_now 只有
    // 0.185 W。现在 watt() 已不再退回 power_now, 但 V x I 本身越界 (电流节点抽风) 仍要拦。
    //
    // 逐条而不是判均值: 29 条 5 W 里混进一条 300 W, 均值 14.8 W 正好落在窗口内,
    // 判均值就会把一个假数字当实测发出去。
    let out: Vec<f64> = samples.iter().filter_map(|s| s.watt()).filter(|w| !(POWER_MIN_W..=POWER_MAX_W).contains(w)).collect();
    if let Some(bad) = out.first() {
        return Err(format!(
            "{} / {} 条功率读数不在可信区间 {POWER_MIN_W}..{POWER_MAX_W} W (越界样本如 {bad:.3} W): \
要么设备此刻正插着 USB 充电 (供电状态变过, 拿这段窗口做 A/B 对比本来就不成立), \
要么这条轨的电流/电压节点读数异常。不确定就不给数 —— 一个物理上不可能的瓦数比没有数字危险得多",
            out.len(),
            samples.len()
        ));
    }
    if mean <= 0.0 {
        return Err("功率均值不为正: 这条轨此刻没在量东西".into());
    }
    Ok(())
}

// ══════════════ 供电判定 (与 sysparam::env_stats 共用) ══════════════

/// 「USB 在供电」的门限 (W): USB 轨 |V x I| 均值低于它就当 USB 没在出力。
/// 输入挂起后 VBUS 仍在、电流读数抖动在几十 mA 以内, 约合 0.05–0.1 W。
pub const USB_IDLE_W: f64 = 0.1;

/// 一段窗口里整机由谁供电。只有 `Battery` / `Bypass` / `Mixed` 量得到整机功耗。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SupplyMode {
    /// 电池放电, USB 不出力: 电池轨 |V x I| 就是整机功耗
    Battery,
    /// 已停充, USB 直供, 电池不进不出 (|I| < BYPASS_IDLE_UA)
    Bypass,
    /// USB 在供电但不够 (或停充旁路中途 USB 断开), 电池同时在放电:
    /// 整机功耗 = 同一时刻 USB 轨 + 电池轨
    Mixed,
    /// 电池满且不进不出, 但没停充: 充电器随时会补电
    FullIdle,
    /// 在给电池充电 / 判不出放电方向
    NotDischarging,
    /// 读不到电池电流
    Unknown,
}

impl SupplyMode {
    /// 这个供电方式下功耗取哪个口径。旁路与混合是**同一个口径** (两轨同刻相加, 旁路时电池项≈0),
    /// 所以满电停充时同一轮里供电在旁路与电池之间来回跳, 各臂仍是同一口径, 可以相减。
    pub fn rail(&self) -> &'static str {
        match self {
            SupplyMode::Battery => "battery",
            SupplyMode::Bypass | SupplyMode::Mixed => "usb_plus_battery",
            _ => "usb",
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            SupplyMode::Battery => "battery",
            SupplyMode::Bypass => "bypass",
            SupplyMode::Mixed => "mixed",
            SupplyMode::FullIdle => "full_idle",
            SupplyMode::NotDischarging => "not_discharging",
            SupplyMode::Unknown => "unknown",
        }
    }

    pub fn measurable(&self) -> bool {
        matches!(self, SupplyMode::Battery | SupplyMode::Bypass | SupplyMode::Mixed)
    }
}

/// 供电判定的输入。`battery_ua` 是电池电流 (本机约定正 = 充, 负 = 放), 一段窗口时取均值。
#[derive(Debug, Clone, Copy)]
pub struct SupplyObs<'a> {
    pub status: &'a str,
    pub battery_ua: Option<f64>,
    /// USB 轨 |V x I| 均值 (W); 没采 USB 轨时 None
    pub usb_w: Option<f64>,
    /// 这一轮用 root 停了充电
    pub charge_suspended: bool,
    /// 停充脚本判成了 USB 供电的旁路 (charge_suspend.sh 状态文件第三行 bypass)
    pub bypass_mode: bool,
}

/// 「电池确实在放电」: status 不是 Charging, 不是满电空闲, 且电流为负。
/// 与 [`BatteryState::on_battery`] 同一判据 (它就调这个)。
pub fn battery_discharging(status: &str, current_ua: f64) -> bool {
    if status.eq_ignore_ascii_case("Charging") {
        return false;
    }
    if status.eq_ignore_ascii_case("Full") && current_ua.abs() < crate::sysparam::BYPASS_IDLE_UA {
        return false;
    }
    current_ua < 0.0
}

/// 唯一的供电判定。`sysparam::env_stats` (eval / autoloop / selftest 的 sample_env 通路) 与
/// 本模块的单轨通路 (gpu-op / perf) 都走这里。
///
/// 规则 (按顺序):
/// 1. 电池在充 (> BYPASS_IDLE_UA): `NotDischarging`。
/// 2. 停充脚本记了旁路: 电池在放 (< −门限) 是 `Mixed`, 否则 `Bypass`。**不看 status** ——
///    满电停充时 status 在 Full / Charging / Discharging 间跳, 2026-10-12 Megacity 目标运行
///    4 代里 3 代同一轮内一部分臂电池放电、一部分臂 USB 直供; 两者按同一口径
///    (两轨同刻相加) 算, 才不会把整组功耗作废。
/// 3. 电池在放电: USB 也在出力就是 `Mixed`, 否则 `Battery`。
/// 4. 已停充、电池在放但 status 仍写 Charging (USB 限流约 0.49 A 时实测如此): `Mixed`。
/// 5. 已停充、电池不进不出、USB 在出力: `Bypass` (没有旁路标记的停充路径)。
/// 6. 满电空闲没停充: `FullIdle`。其余 `NotDischarging`。
pub fn classify_supply(o: &SupplyObs) -> SupplyMode {
    let thr = crate::sysparam::BYPASS_IDLE_UA;
    let Some(c) = o.battery_ua else {
        return SupplyMode::Unknown;
    };
    let usb_on = o.usb_w.is_some_and(|w| w >= USB_IDLE_W);
    if c > thr {
        return SupplyMode::NotDischarging;
    }
    if o.charge_suspended && o.bypass_mode {
        return if c < -thr { SupplyMode::Mixed } else { SupplyMode::Bypass };
    }
    if battery_discharging(o.status, c) {
        return if usb_on { SupplyMode::Mixed } else { SupplyMode::Battery };
    }
    if o.charge_suspended && c < -thr {
        return SupplyMode::Mixed;
    }
    if o.charge_suspended && c.abs() < thr && usb_on {
        return SupplyMode::Bypass;
    }
    if o.status.eq_ignore_ascii_case("Full") && c.abs() < thr {
        return SupplyMode::FullIdle;
    }
    SupplyMode::NotDischarging
}

// ══════════════ USB 输入上限 (开跑前检查) ══════════════

/// 开跑前判「USB 够不够供整机」的默认需求 (W)。手游满载整机约 3–8 W;
/// 停充后 USB 被限到约 0.49 A (≈ 2.4 W) 时重负载必然要电池补。
pub const USB_SUPPLY_NEED_W: f64 = 6.0;

/// 读 USB 输入上限: `USBLIM <input_current_limit|NA> <current_max|NA> <voltage_now|NA>`。
pub fn usb_supply_cmd() -> String {
    let u = "/sys/class/power_supply/usb";
    format!(
        "su -c 'echo USBLIM $(cat {u}/input_current_limit 2>/dev/null || echo NA) \
$(cat {u}/current_max 2>/dev/null || echo NA) \
$(cat {u}/voltage_now 2>/dev/null || echo NA)'"
    )
}

/// USB 输入上限检查的结果。
#[derive(Debug, Clone, PartialEq)]
pub struct UsbSupplyCheck {
    /// 输入电流上限 (μA): 先取 input_current_limit, 没有再取 current_max
    pub limit_ua: Option<i64>,
    pub volt_uv: Option<i64>,
    /// 上限 x 电压 (W)
    pub max_w: Option<f64>,
    /// 明确判出供电不足
    pub short: bool,
    pub note: String,
}

/// 解析 [`usb_supply_cmd`] 的输出并判够不够 `need_w`。读不到上限时不判 (short=false), 说明里写清。
pub fn usb_supply_check(text: &str, need_w: f64) -> UsbSupplyCheck {
    let line = text.lines().find(|l| l.trim_start().starts_with("USBLIM")).unwrap_or("");
    let c: Vec<&str> = line.split_whitespace().collect();
    let num = |i: usize| c.get(i).and_then(|v| v.parse::<i64>().ok()).filter(|v| *v > 0);
    let limit_ua = num(1).or(num(2));
    let volt_uv = num(3);
    let max_w = match (limit_ua, volt_uv) {
        (Some(i), Some(v)) => Some(i as f64 / 1e6 * v as f64 / 1e6),
        _ => None,
    };
    let (short, note) = match max_w {
        Some(w) if w < need_w => (
            true,
            format!(
                "供电不足: USB 输入上限 {:.2} A x {:.2} V ≈ {w:.2} W, 低于 {need_w:.1} W。负载大时电池会补电, \
功耗按 USB 与电池两轨同刻相加计 (混合口径, 不能跨任务比)",
                limit_ua.unwrap_or(0) as f64 / 1e6,
                volt_uv.unwrap_or(0) as f64 / 1e6
            ),
        ),
        Some(w) => (false, format!("USB 输入上限约 {w:.2} W, 不低于 {need_w:.1} W")),
        None => (false, "读不到 USB 输入上限 (input_current_limit/current_max/voltage_now), 没法开跑前判供电够不够".into()),
    };
    UsbSupplyCheck { limit_ua, volt_uv, max_w, short, note }
}

pub fn read_usb_supply(phone: &crate::device::Device, need_w: f64) -> UsbSupplyCheck {
    usb_supply_check(&phone.shell(&usb_supply_cmd(), 8_000), need_w)
}

// ══════════════ 电池状态与停充 ══════════════

/// 电池侧的一次状态读数。功耗读数算不算数, 全看这个。
#[derive(Debug, Clone, PartialEq, Default)]
pub struct BatteryState {
    /// `/sys/class/power_supply/battery/status`: Charging / Discharging / Full / Not charging
    pub status: String,
    /// `current_now` (μA)。本机约定: **正 = 在往电池里充, 负 = 电池在放电**。
    pub current_ua: Option<i64>,
    /// `capacity` (%)
    pub capacity_pct: Option<i64>,
}

impl BatteryState {
    /// 这一刻整机是不是真由电池供电。
    ///
    /// 两个判据都要看, 因为两个都会单独骗人: `status` 在停充之后有的内核仍写
    /// `Not charging` 而不是 `Discharging`; 而 `current_now` 在充放平衡的瞬间会过零。
    /// 只有「没在充电」且「电流确实是放电方向」才算数。
    pub fn on_battery(&self) -> bool {
        // 状态未知/缺失不能证明在放电, 即便电流为负 (main 侧收紧)。
        let status = self.status.trim().to_ascii_lowercase();
        if !matches!(status.as_str(), "discharging" | "not charging" | "full") {
            return false;
        }
        // 满电空闲 (电池不进不出, 整机由 USB 供电) 不是放电态 —— 与 charge_suspend.sh /
        // sysparam::env_stats 同一判据 ([`battery_discharging`])
        self.current_ua.is_some_and(|c| battery_discharging(&status, c as f64))
    }

    /// 人能读的一句话, 进 `unavailable` 的原因里。
    pub fn why_not_on_battery(&self) -> String {
        match self.current_ua {
            Some(c) if c > 0 => format!(
                "电池状态 {} 且 current_now={c} μA (正=正在充电): 此刻整机由 USB 供电, \
电池轨量到的只是充电电流与系统耗电相抵之后的余量, 不是整机在吃的功率",
                self.status
            ),
            Some(0) | None => format!(
                "电池状态 {}, current_now={}: 判不出是不是放电态",
                self.status,
                self.current_ua.map(|c| c.to_string()).unwrap_or_else(|| "读不到".into())
            ),
            Some(c) => format!("电池状态 {} (current_now={c} μA)", self.status),
        }
    }
}

/// 一次 su 往返把电池状态读齐。
pub fn battery_state_cmd() -> String {
    let p = "/sys/class/power_supply/battery";
    format!(
        "su -c 'echo BAT $(cat {p}/status 2>/dev/null || echo ?) \
$(cat {p}/current_now 2>/dev/null || echo NA) \
$(cat {p}/capacity 2>/dev/null || echo NA)'"
    )
}

/// Parse a battery record while preserving statuses containing whitespace.
pub fn parse_battery_state(text: &str) -> Option<BatteryState> {
    text.lines().find_map(|l| {
        let c: Vec<&str> = l.split_whitespace().collect();
        if c.first().copied() != Some("BAT") || c.len() < 4 {
            return None;
        }
        // The command always emits two trailing numeric fields or NA placeholders.
        let numeric = c.len() - 2;
        Some(BatteryState {
            status: c[1..numeric].join(" "),
            current_ua: c[numeric].parse().ok(),
            capacity_pct: c[numeric + 1].parse().ok().filter(|value| (0..=100).contains(value)),
        })
    })
}

pub fn read_battery_state(phone: &(impl Shell + ?Sized)) -> BatteryState {
    parse_battery_state(&phone.shell(&battery_state_cmd(), 8_000)).unwrap_or_default()
}

/// 电量低于这个百分比就不许停充。
///
/// 停充期间整机纯靠电池，跑一轮标尺就是几分钟的满载放电; 电量本来就低的时候再抽一把,
/// 轻则测到一半关机 (这一轮白跑), 重则把电池拖进过放。30% 是留给「一轮测完还能撑到恢复充电」的余量。
pub const MIN_CAPACITY_FOR_SUSPEND_PCT: i64 = 30;

/// 停充后等设备真的转成放电态的上限。
///
/// 为什么要这么久: `status` 是**立刻**翻成 `Discharging` 的, 但电量计的 `current_now`
/// 要几秒才跟上 —— 2026-09-23 实测停充后三秒内还在报充电方向的
/// `+1072000 / +982000`, 第三秒才翻成 `-325000`。等不够就会把一个明明生效了的节点
/// 判成"写进去无效", 然后去试下一个候选 (本机第一版 2.5 秒就是这么误判的)。
pub const SUSPEND_SETTLE_MS: u64 = 12_000;

/// 停充用哪个 sysfs 节点 —— 不同内核给的不一样, 所以是**探出来的**, 不是写死的。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SuspendHow {
    /// 写 `1` 挂起输入 (老 qpnp-smb 的 `input_suspend`)
    WriteOne,
    /// 写 `0` 关掉充电 (`charging_enabled` / `input_current_limit`)
    WriteZero,
    /// 把充电上限百分比压到**当前电量以下** (新 pmic-glink 的
    /// `charge_control_end_threshold`; 标准 Linux 电池 ABI)
    CapBelowCapacity,
}

/// 一个候选停充节点。顺序即探测顺序: 语义越明确的排越前。
pub struct ChargeCtlNode {
    pub path: &'static str,
    pub how: SuspendHow,
}

pub const CHARGE_CTL_CANDIDATES: &[ChargeCtlNode] = &[
    // 老 Qualcomm qpnp-smb: 语义最直接, 就是「挂起输入」
    ChargeCtlNode { path: "/sys/class/power_supply/battery/input_suspend", how: SuspendHow::WriteOne },
    ChargeCtlNode { path: "/sys/class/power_supply/usb/input_suspend", how: SuspendHow::WriteOne },
    // 高通 qcom-battery 的厂商开关。**本机红魔 NX809J 实测能停充的就是这一条** ——
    // 它不在 power_supply class 下, 而在自己的 class 里, 只看 power_supply 会整个错过。
    ChargeCtlNode { path: "/sys/class/qcom-battery/charging_enabled", how: SuspendHow::WriteZero },
    ChargeCtlNode { path: "/sys/class/qcom-battery/battery_charging_enabled", how: SuspendHow::WriteZero },
    // 各家自定义的开关
    ChargeCtlNode { path: "/sys/class/power_supply/battery/charging_enabled", how: SuspendHow::WriteZero },
    ChargeCtlNode { path: "/sys/class/power_supply/battery/battery_charging_enabled", how: SuspendHow::WriteZero },
    // 新 pmic-glink (本机红魔 NX809J 就是这一类): 标准电池养护 ABI
    ChargeCtlNode { path: "/sys/class/power_supply/battery/charge_control_end_threshold", how: SuspendHow::CapBelowCapacity },
    // 兜底: 把 USB 输入电流限到 0。放最后 —— 它动的是输入侧, 影响面最大
    ChargeCtlNode { path: "/sys/class/power_supply/usb/input_current_limit", how: SuspendHow::WriteZero },
];

/// 停充守卫。**进出快照一致**是它唯一的职责。
///
/// 纪律与锁频那套完全一致 (`Lock`):
///   1. 先把「改了哪个节点、原值是什么」落到本机状态文件, **再**动设备 ——
///      进程中途被杀也能由下次启动回滚 (`recover_stale_charge_ctl`);
///   2. 写完必须**回读确认停充真的生效** (电流转负), 没生效就地还原并换下一个候选;
///   3. 调用方必须 restore；Drop 只警告并保留恢复记录。
pub struct ChargeSuspend {
    path: String,
    saved: String,
    state_path: std::path::PathBuf,
    restored: bool,
    /// 停充后的供电方式: `Battery` (电池放电)、`Bypass` (USB 直供, 电池不进不出) 或
    /// `Mixed` (USB 供电不足, 电池同时在放电; 审查 R1 #2)
    mode: SupplyMode,
}

/// 旁路判定最多等多久 (毫秒)。电量计读数是平滑过的, 2026-10-11 实测停充后约 45 s
/// 电池电流才落到 ~0; 与 charge_suspend.sh 的 BYPASS_MAX_S 一致。
pub const BYPASS_MAX_MS: u64 = 90_000;

/// 写停充节点后, 这一串电池电流读数能不能判成「旁路成立」(与 charge_suspend.sh 的
/// `bypass_wait` 同一判据): 写之前在充 (> 门限) 时, 第一次读数要比写之前降三成以上
/// (节点确实起作用); 写之前在放电 (< −门限) 不是旁路的样子; 然后某一刻 |I| < 门限。
pub fn bypass_settled(pre_ua: Option<i64>, after_ua: &[i64]) -> bool {
    let thr = crate::sysparam::BYPASS_IDLE_UA;
    let Some(first) = after_ua.first() else { return false };
    if let Some(pre) = pre_ua {
        if (pre.abs() as f64) > thr {
            if pre < 0 || first * 10 > pre * 7 {
                return false;
            }
        }
    }
    after_ua.iter().any(|c| (c.abs() as f64) < thr)
}

/// 审查 R1 #2: 混合供电现场 (USB 供电不足, status 仍写 Charging, 电池却在放电; fix-D 单测锁定的
/// 就是 Charging、−0.6 A) 的停充成功判据, 与 charge_suspend.sh 的 `mixed_one` 同一判据:
/// 写后电池电流 < −BYPASS_IDLE_UA (确实在放电), 且比写前降了至少 |写前| 的三成 (节点确实起作用;
/// 写前 ≈ 0 时只要在放电就算)。`on_battery` (status 不是 Charging) 与 `bypass_settled` (|I| → 0)
/// 在这种现场都判不过, 以前会换候选再来、最后 CHARGE_FAIL。
pub fn mixed_settled(pre_ua: Option<i64>, after_ua: i64) -> bool {
    let thr = crate::sysparam::BYPASS_IDLE_UA;
    if (after_ua as f64) >= -thr {
        return false;
    }
    let Some(pre) = pre_ua else { return false };
    ((pre - after_ua) as f64) * 10.0 >= (pre.abs() as f64) * 3.0
}

/// 混合判据要连续几次读数都成立 (电量计读数会抖)。与 charge_suspend.sh 的 MIXED_TRIES 一致。
pub const MIXED_TRIES: usize = 3;

/// 这一串写后读数的末尾是否连续 [`MIXED_TRIES`] 次满足 [`mixed_settled`]。
pub fn mixed_streak(pre_ua: Option<i64>, after_ua: &[i64]) -> bool {
    after_ua.len() >= MIXED_TRIES && after_ua.iter().rev().take(MIXED_TRIES).all(|c| mixed_settled(pre_ua, *c))
}

/// 状态文件的格式: 第一行节点路径, 第二行原值。两行都在才算一份有效的遗留状态。
pub fn charge_ctl_state_text(path: &str, saved: &str) -> String {
    format!("{path}\n{saved}\n")
}

pub fn parse_charge_ctl_state(text: &str) -> Option<(String, String)> {
    let mut it = text.lines();
    let path = it.next()?.trim();
    let saved = it.next()?.trim();
    if !CHARGE_CTL_CANDIDATES.iter().any(|n| n.path == path)
        || saved.parse::<i64>().is_err() || it.next().is_some() {
        return None;
    }
    Some((path.to_string(), saved.to_string()))
}

/// 给一个候选节点算「写什么值表示停充」。
///
/// `CapBelowCapacity` 要当前电量: 把上限压到**当前电量以下**才会停充;
/// 压到电量以上等于没压 —— 本机出厂就是 `end_threshold=80` 而电量 91% 照充不误,
/// 正是这个道理。
pub fn suspend_value(how: SuspendHow, capacity_pct: Option<i64>) -> Option<String> {
    match how {
        SuspendHow::WriteOne => Some("1".into()),
        SuspendHow::WriteZero => Some("0".into()),
        SuspendHow::CapBelowCapacity => {
            let cap = capacity_pct?;
            // 压到电量以下但不低于 1: 留一格避免某些内核把 0 当成「没设上限」
            Some(((cap - 5).clamp(1, 99)).to_string())
        }
    }
}

impl ChargeSuspend {
    /// 停充。失败时尝试恢复；恢复未确认则保留记录并停止。
    ///
    /// `state_path` 用 `lock_state_path("charge", serial)`, 与锁频的状态文件分开放 ——
    /// 两者互不相干, 混在一份文件里会互相覆盖。
    pub fn apply(
        phone: &impl Shell,
        state_path: &std::path::Path,
    ) -> Result<ChargeSuspend, String> {
        Self::apply_with_hooks(phone,state_path,|_,_,_|Ok(()),||Ok(()),||Ok(()))
    }

    /// The owner binds the durable record before the first write. Rollback hooks
    /// distinguish a still-owned record from an explicitly verified candidate.
    pub(crate) fn apply_with_hooks(
        phone:&impl Shell, state_path:&Path,
        mut before_write:impl FnMut(&str,&str,&str)->Result<(),String>,
        mut before_rollback:impl FnMut()->Result<(),String>,
        mut after_rollback:impl FnMut()->Result<(),String>,
    )->Result<Self,String> {
        phone.check()?;
        let bat = phone.charge_battery()?;
        if let Some(cap) = bat.capacity_pct {
            if cap < MIN_CAPACITY_FOR_SUSPEND_PCT {
                return Err(format!(
                    "电量只有 {cap}%, 低于停充下限 {MIN_CAPACITY_FOR_SUSPEND_PCT}%: 不停充。\
停充期间整机纯靠电池, 电量本来就低时再满载抽几分钟, 轻则测到一半关机, 重则过放"
                ));
            }
        } else {
            return Err("读不到电池电量, 不敢停充".into());
        }
        if bat.on_battery() {
            return Err("设备本来就在放电态, 不需要停充".into());
        }

        let mut tried: Vec<String> = Vec::new();
        for node in CHARGE_CTL_CANDIDATES {
            let Some(saved)=phone.charge_node(node.path)? else {continue;};
            let Some(want) = suspend_value(node.how, bat.capacity_pct) else { continue };

            // 先落状态文件再动设备: 中途被杀也能回滚
            let text = charge_ctl_state_text(node.path, &saved);
            if parse_charge_ctl_state(&text).is_none() {
                return Err(format!("停充节点 {} 原值无法安全保存: {saved:?}", node.path));
            }
            save_state(state_path, &text)?;
            before_write(node.path,&saved,&want)?;
            phone.check()?;
            phone.shell_write(&format!("su -c 'echo {want} > {}'", node.path), 8_000);

            phone.check()?;
            // 回读确认**停充真的生效** —— 写进去不代表内核认。
            // 本机 charge_control_end_threshold 出厂就是 80 而电量 91% 照充不误, 就是活例。
            let mut ok=false;
            for _ in 0..(SUSPEND_SETTLE_MS/500).max(1) {
                std::thread::sleep(Duration::from_millis(500));
                if phone.charge_battery()?.on_battery() {ok=true;break;}
            }
            if ok {
                progress("charge", &format!("已停充: {} {saved} → {want}", node.path));
                return Ok(ChargeSuspend {
                    path: node.path.to_string(),
                    saved,
                    state_path: state_path.to_path_buf(),
                    restored: false,
                    mode: SupplyMode::Battery,
                });
            }
            // 没转成放电态, 再看是不是旁路 (满电机器停充后 USB 直供、电池不进不出 ——
            // 审计 #15: 以前这里只认放电态, 满电时 gpu-op --suspend-charging 必以退出码 2 退出)
            let mut seen: Vec<i64> = Vec::new();
            let polls = ((BYPASS_MAX_MS - SUSPEND_SETTLE_MS) / 1_000).max(1);
            for i in 0..polls {
                if let Some(c) = read_battery_state(phone).current_ua {
                    seen.push(c);
                    if bypass_settled(bat.current_ua, &seen) {
                        break;
                    }
                    // 混合供电 (审查 R1 #2): 连续几次读数都在放电且比写前降了三成, 不用等到 |I| → 0
                    if mixed_streak(bat.current_ua, &seen) {
                        break;
                    }
                    // 第一次读数就没降三成: 节点没起作用, 不用再等
                    if i == 0 && !bypass_settled(bat.current_ua, &[c, 0]) && !mixed_settled(bat.current_ua, c) {
                        break;
                    }
                }
                std::thread::sleep(Duration::from_millis(1_000));
            }
            if mixed_streak(bat.current_ua, &seen) && !bypass_settled(bat.current_ua, &seen) {
                progress(
                    "charge",
                    &format!("已停充 (混合供电: USB 供电不足, 电池同时在放电): {} {saved} → {want}", node.path),
                );
                return Ok(ChargeSuspend {
                    path: node.path.to_string(),
                    saved,
                    state_path: state_path.to_path_buf(),
                    restored: false,
                    mode: SupplyMode::Mixed,
                });
            }
            if bypass_settled(bat.current_ua, &seen) {
                progress(
                    "charge",
                    &format!("已停充 (旁路: USB 直供, 电池不进不出): {} {saved} → {want}", node.path),
                );
                return Ok(ChargeSuspend {
                    path: node.path.to_string(),
                    saved,
                    state_path: state_path.to_path_buf(),
                    restored: false,
                    mode: SupplyMode::Bypass,
                });
            }
            // 没生效: 就地还原, 试下一个
            let mut candidate = ChargeSuspend {
                path: node.path.into(), saved, state_path: state_path.into(), restored: false,
                // 只用于就地回滚, 供电方式无意义
                mode: SupplyMode::Battery,
            };
            before_rollback()?;
            if !candidate.restore_checked(phone)? {
                return Err(format!("停充候选 {} 无效且未确认恢复；停止尝试，核查 {}", node.path, state_path.display()));
            }
            after_rollback()?;
            tried.push(format!("{} (写 {want} 无效)", node.path));
        }
        Err(if tried.is_empty() {
            "这台机器没有任何已知的停充节点".into()
        } else {
            format!("试过的停充节点都没让设备转成放电态: {}", tried.join("; "))
        })
    }

    /// 改的是哪个节点 (进报告, 让「这轮到底动过什么」可回溯)。
    pub fn node(&self) -> &str {
        &self.path
    }

    /// 停充后的供电方式 (`Battery` 或 `Bypass`)。
    pub fn mode(&self) -> SupplyMode {
        self.mode
    }

    /// 恢复充电并清掉状态文件; 返回是否确认恢复。
    pub fn restore(&mut self, phone: &impl Shell) -> bool {
        self.restore_checked(phone).unwrap_or(false)
    }
    pub(crate) fn restore_checked(&mut self, phone: &impl Shell) -> Result<bool,String> {
        phone.check()?;
        if self.restored {return Ok(true);}
        phone.shell_write(&format!("su -c 'echo {} > {}'", self.saved, self.path),8_000);
        phone.check()?;
        let back=phone.charge_node(&self.path)?;
        if back.as_deref()==Some(&self.saved) {
            clear_state(&self.state_path)?;
            self.restored=true;
        } else {
            progress("charge",&format!("恢复充电读回不符: {} 期望 {} 实得 {:?}; 保留 {}",self.path,self.saved,back,self.state_path.display()));
        }
        Ok(self.restored)
    }
    /// Reconstruct only a caller-bound obligation. A missing record is accepted
    /// solely after that owner durably entered restoration; the write/readback
    /// still runs and record disappearance alone never proves recovery.
    pub(crate) fn from_bound_record(state_path:&Path,path:&str,saved:&str,restoration_started:bool)->Result<Self,String> {
        if parse_charge_ctl_state(&charge_ctl_state_text(path,saved)).is_none() {return Err("invalid bound charging values".into());}
        match charge_record(state_path)? {
            Some(actual) if actual==(path.to_owned(),saved.to_owned())=>{},
            None if restoration_started=>{},
            _=>return Err("charging record differs from the current parent binding".into()),
        }
        // Recovery reconstructs only the restore obligation; the supply mode observed
        // when charging was suspended is not part of the bound record.
        Ok(Self{path:path.into(),saved:saved.into(),state_path:state_path.into(),restored:false,mode:SupplyMode::Battery})
    }

}

impl Drop for ChargeSuspend {
    fn drop(&mut self) {
        // Drop 里拿不到 phone。真正的还原在 restore(); 这里只负责让「忘了调 restore」
        // 这件事不会无声无息 —— 状态文件还在, 下次启动 recover_stale_charge_ctl 会兜底。
        if !self.restored {
            progress(
                "charge",
                &format!(
                    "停充守卫被丢弃时还没还原 ({}): 状态文件 {} 留着, 下次启动会回滚",
                    self.path,
                    self.state_path.display()
                ),
            );
        }
    }
}

pub(crate) fn charge_record(path:&Path)->Result<Option<(String,String)>,String> {
    read_state(path)?.map(|text|parse_charge_ctl_state(&text).ok_or_else(||format!("invalid charge recovery record {}; preserved",path.display()))).transpose()
}

/// 回滚上次遗留的停充状态 (进程被杀没来得及还原)。返回 `None` 表示没有遗留。
pub fn recover_stale_charge_ctl(
    phone: &impl Shell,
    state_path: &std::path::Path,
) -> Result<Option<bool>, String> {
    let Some(text) = read_state(state_path)? else { return Ok(None) };
    let (path, saved) = parse_charge_ctl_state(&text)
        .ok_or_else(|| format!("invalid charge recovery record {}; preserved", state_path.display()))?;
    progress("charge", &format!("发现上次遗留的停充状态 {path}, 先恢复充电"));
    phone.shell_write(&format!("su -c 'echo {saved} > {path}'", ), 8_000);
    let back = phone.shell(&format!("su -c 'cat {path} 2>/dev/null'"), 8_000);
    let ok = back.trim() == saved;
    if ok {
        clear_state(state_path)?;
    }
    Ok(Some(ok))
}

// ══════════════ 设备操作 ══════════════

pub fn root_ok(phone: &impl Shell) -> bool {
    phone.shell("su -c id", 8000).contains("uid=0(")
}

pub fn read_snapshot(phone: &impl Shell) -> Snapshot {
    parse_snapshot(&phone.shell(&snapshot_cmd(), 15000))
}

pub fn read_thermal(phone: &impl Shell) -> Vec<(String, i64)> {
    // Conditioning callers already require root. ColorOS may expose the zone
    // directories while denying shell access to both type and temp files.
    parse_thermal(&phone.shell(
        "su -c 'for z in /sys/class/thermal/thermal_zone*; do echo \"$(cat $z/type) $(cat $z/temp)\"; done 2>/dev/null'",
        15000,
    ))
}

pub fn read_power(phone: &impl Shell, rail: PowerRail) -> Option<PowerSample> {
    parse_power_samples(&phone.shell(&power_sample_cmd(rail), 8000))
        .into_iter()
        .next()
}

/// 冷机门禁: SoC 结温最高热区 < cool_c 才放行; 超时即报错(安全阀, 不是时间预设)
pub fn wait_cool(
    tag: &str,
    phone: &impl Shell,
    cool_c: f64,
    timeout_s: u64,
) -> Result<(String, f64, u64), String> {
    let t0 = Instant::now();
    loop {
        let (zone, c) =
            soc_max_c(&read_thermal(phone)).ok_or("读不到任何 cpu-/gpuss 热区, 无法做冷机判定")?;
        if c < cool_c {
            return Ok((zone, c, t0.elapsed().as_secs()));
        }
        if t0.elapsed() > Duration::from_secs(timeout_s) {
            return Err(format!("等冷超时 {timeout_s}s: {zone}={c:.1}C 仍 >= {cool_c}C"));
        }
        progress(
            tag,
            &format!(
                "等冷: {zone}={c:.1}C >= {cool_c}C, 3s 后重测 (已等 {}s)",
                t0.elapsed().as_secs()
            ),
        );
        std::thread::sleep(Duration::from_secs(3));
    }
}

pub struct Lock {
    before: Snapshot,
    state_path: std::path::PathBuf,
    applied: bool,
}

/// 快照 → 状态文件文本 (与 snapshot_cmd 输出同形, parse_snapshot 可直接回读)
pub fn snapshot_text(s: &Snapshot) -> String {
    let mut t = String::new();
    for p in &s.cpu {
        t.push_str(&format!(
            "CPU {} {} {} {} {} {}\n",
            p.path, p.governor, p.min_khz, p.max_khz, p.cur_khz, p.cpus
        ));
    }
    if let (Some(mx), Some(mn)) = (s.gpu_max_level, s.gpu_min_level) {
        t.push_str(&format!(
            "GPU {} {} {} {} {}\n",
            mx,
            mn,
            s.gpu_thermal_level.unwrap_or(0),
            s.gpuclk_hz.unwrap_or(0),
            s.gpu_model.clone().unwrap_or_default()
        ));
    }
    if !s.gpu_freqs.is_empty() {
        t.push_str(&format!(
            "GPUFREQS {}\n",
            s.gpu_freqs
                .iter()
                .map(|f| f.to_string())
                .collect::<Vec<_>>()
                .join(" ")
        ));
    }
    for b in &s.bus {
        t.push_str(&format!(
            "BUS {} {} {} {}\n",
            b.name, b.floor_khz, b.max_khz, b.cur_khz
        ));
    }
    if let Some((ceiling, floor)) = s.mtk_ged {
        t.push_str(&format!("MTK_GED {ceiling} {floor}\n"));
    }
    if let Some(hz) = s.mtk_stackclk_hz {
        t.push_str(&format!("MTK_STACK_HZ {hz}\n"));
    }
    t
}

// Telemetry tolerates absent/malformed fields; recovery must not invent a value
// or restore only the parseable prefix of a damaged record.
fn recovery_snapshot(text: &str) -> Result<Snapshot, String> {
    let s = parse_snapshot(text);
    if snapshot_text(&s) != text || s.cpu.is_empty()
        || s.gpu_max_level.is_none() || s.gpu_min_level.is_none() {
        return Err("incomplete or malformed lock snapshot".into());
    }
    if let Some((ceiling, floor)) = s.mtk_ged {
        if s.gpu_model.as_deref() != Some("MediaTek-Mali") || s.gpu_freqs.is_empty()
            || s.gpu_freqs.len() > 256 || s.gpu_freqs.iter().any(|hz| *hz == 0)
            || ceiling as usize >= s.gpu_freqs.len() || floor as usize >= s.gpu_freqs.len() {
            return Err("incomplete MediaTek GED recovery limits/frequency table".into());
        }
    } else if s.gpu_model.as_deref() == Some("MediaTek-Mali") {
        return Err("MediaTek snapshot lacks original GED user limits".into());
    }
    let mut paths = std::collections::HashSet::new();
    for p in &s.cpu {
        let suffix = p.path.strip_prefix(&format!("{CPUFREQ}/policy"));
        if !suffix.is_some_and(|v| !v.is_empty() && v.bytes().all(|b| b.is_ascii_digit()))
            || !paths.insert(&p.path) || p.governor.is_empty()
            || !p.governor.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-') {
            return Err("invalid CPU recovery node/governor".into());
        }
    }
    let mut buses = std::collections::HashSet::new();
    if s.bus.iter().any(|b| !BUS_NODES.contains(&b.name.as_str()) || !buses.insert(&b.name)) {
        return Err("invalid/duplicate bus recovery node".into());
    }
    Ok(s)
}

fn restored_snapshot_matches(before: &Snapshot, after: &Snapshot) -> bool {
    before.cpu.len() == after.cpu.len()
        && before.cpu.iter().all(|b| after.cpu.iter().any(|a| a.path == b.path && a.governor == b.governor))
        && before.bus.len() == after.bus.len()
        && before.bus.iter().all(|b| after.bus.iter().any(|a| a.name == b.name && a.floor_khz == b.floor_khz))
        && after.mtk_ged == before.mtk_ged
        && if before.mtk_ged.is_some() {
            after.gpu_model == before.gpu_model
        } else {
            after.gpu_max_level == before.gpu_max_level && after.gpu_min_level == before.gpu_min_level
        }
}

fn restored_text_matches(before: &Snapshot, text: &str) -> bool {
    // The telemetry parser defaults malformed bus integers to zero. A zero
    // readback can only certify restore if the device actually returned it.
    let buses_valid = text.lines().all(|line| {
        let c: Vec<_> = line.split_whitespace().collect();
        c.first().copied() != Some("BUS")
            || (c.len() == 5 && c[2..].iter().all(|v| v.parse::<u64>().is_ok()))
    });
    buses_valid && restored_snapshot_matches(before, &parse_snapshot(text))
}

/// 把快照里的调速器与 kgsl 档位写回设备; 返回回读是否一致
pub fn restore_snapshot(phone: &impl Shell, before: &Snapshot) -> bool {
    let mut cmd = String::from("su -c '");
    for p in &before.cpu {
        cmd.push_str(&format!("echo {} > {}/scaling_governor; ", p.governor, p.path));
    }
    // 恢复顺序: 先 min(数值大) 再 max, 与 kgsl 钳位规则一致
    if let Some((ceiling, floor)) = before.mtk_ged {
        cmd.push_str(&ged_write_limits(before, ceiling, floor));
    } else if let (Some(mx), Some(mn)) = (before.gpu_max_level, before.gpu_min_level) {
        cmd.push_str(&format!(
            "K={KGSL}; echo {mn} > $K/min_pwrlevel; echo {mx} > $K/max_pwrlevel; "
        ));
    }
    for b in &before.bus {
        cmd.push_str(&format!(
            "echo {} > {BUS_DCVS}/{}/boost_freq; ",
            b.floor_khz, b.name
        ));
    }
    cmd.push_str("echo RESTORED'");
    let out = phone.shell_write(&cmd, 15000);
    if !out.contains("RESTORED") {
        return false;
    }
    restored_text_matches(before, &phone.shell(&snapshot_cmd(), 15000))
}

/// 上次异常退出遗留的锁态: 状态文件在就按它回滚。返回 Some(是否回滚成功); 无遗留 None
pub fn recover_stale_lock(
    tag: &str,
    phone: &impl Shell,
    state_path: &std::path::Path,
) -> Result<Option<bool>, String> {
    let Some(text) = read_state(state_path)? else { return Ok(None) };
    let before = recovery_snapshot(&text)
        .map_err(|e| format!("invalid lock recovery record {}: {e}; preserved", state_path.display()))?;
    progress(
        tag,
        &format!("发现上次遗留的锁频状态 {}, 先回滚", state_path.display()),
    );
    let ok = restore_snapshot(phone, &before);
    if ok {
        clear_state(state_path)?;
    }
    Ok(Some(ok))
}

impl Lock {
    pub fn apply(
        phone: &impl Shell,
        state_path: &std::path::Path,
        before: Snapshot,
        level: u32,
    ) -> Result<Lock, String> {
        // 先落状态文件再动设备: 中途被杀也能回滚
        let text = snapshot_text(&before);
        recovery_snapshot(&text).map_err(|e| format!("不能保存完整锁频恢复快照: {e}; 未改设备"))?;
        if before.mtk_ged.is_some() && level as usize >= before.gpu_freqs.len() {
            return Err("MediaTek GPU level is outside the working OPP table; device not changed".into());
        }
        save_state(state_path, &text)?;
        let mut cmd = String::from("su -c '");
        for p in &before.cpu {
            cmd.push_str(&format!("echo performance > {}/scaling_governor; ", p.path));
        }
        // 先 max 再 min 再 max: kgsl 的 store 互相钳位 (max<=min, min>=max), 这个顺序对任意目标档都成立
        if before.mtk_ged.is_some() {
            cmd.push_str(&ged_write_limits(&before, level, level));
        } else {
            cmd.push_str(&format!(
                "K={KGSL}; echo {level} > $K/max_pwrlevel; echo {level} > $K/min_pwrlevel; echo {level} > $K/max_pwrlevel; "
            ));
        }
        // 总线钉在 hw_max_freq: 访存算子的时延不再随 DDR/LLCC DCVS 漂移
        for b in &before.bus {
            cmd.push_str(&format!(
                "echo {} > {BUS_DCVS}/{}/boost_freq; ",
                b.max_khz, b.name
            ));
        }
        cmd.push_str("echo LOCKED'");
        let out = phone.shell_write(&cmd, 15000);
        let lock = Lock {
            before,
            state_path: state_path.to_path_buf(),
            applied: true,
        };
        if !out.contains("LOCKED") {
            let restored = lock.restore(phone);
            return Err(format!("锁频命令未执行完: {}; 恢复确认={restored}; 状态路径 {}", out.trim(), state_path.display()));
        }
        let after = read_snapshot(phone);
        let cpu_ok = !after.cpu.is_empty() && after.cpu.iter().all(|p| p.governor == "performance");
        let gpu_ok = gpu_locked_at(&after, level)
            && (lock.before.mtk_ged.is_some() == after.mtk_ged.is_some());
        let bus_ok = after.bus.len() == lock.before.bus.len()
            && after.bus.iter().all(|b| b.floor_khz == b.max_khz);
        if !cpu_ok || !gpu_ok || !bus_ok {
            let restored = lock.restore(phone);
            return Err(format!(
                "锁频回读不符: cpu_governor={:?} gpu(max,min)=({:?},{:?}) 目标档 {level} bus={:?}; 恢复确认={restored}; 状态路径 {}",
                after.cpu.iter().map(|p| p.governor.as_str()).collect::<Vec<_>>(),
                after.gpu_max_level,
                after.gpu_min_level,
                after.bus.iter().map(|b| format!("{}:{}/{}", b.name, b.floor_khz, b.max_khz)).collect::<Vec<_>>(),
                state_path.display()
            ));
        }
        Ok(lock)
    }

    /// 恢复原值并清掉状态文件; 返回回读是否与锁前一致
    pub fn restore(&self, phone: &impl Shell) -> bool {
        if !self.applied {
            return true;
        }
        restore_snapshot(phone, &self.before) && clear_restored_state(&self.state_path)
    }
}

/// GED indices run from fastest (0) to slowest. Release the floor before
/// changing the ceiling; normal FPSGO/thermal arbitration stays enabled.
fn ged_write_limits(before: &Snapshot, ceiling: u32, floor: u32) -> String {
    let lowest = before.gpu_freqs.len().saturating_sub(1);
    format!("echo {lowest} > {MTK_GED}/custom_boost_gpu_freq; echo {ceiling} > {MTK_GED}/custom_upbound_gpu_freq; echo {floor} > {MTK_GED}/custom_boost_gpu_freq; ")
}

pub fn gpu_locked_at(snapshot: &Snapshot, level: u32) -> bool {
    snapshot.gpu_max_level == Some(level) && snapshot.gpu_min_level == Some(level)
        && snapshot.mtk_ged.map(|limits| limits == (level, level)).unwrap_or(true)
}

pub fn gpu_control_backend(snapshot: &Snapshot) -> &'static str {
    if snapshot.mtk_ged.is_some() { "mediatek_ged" }
    else if snapshot.gpu_max_level.is_some() { "qualcomm_kgsl" }
    else { "unavailable" }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dimensity_ged_limits_are_preserved_for_recovery() {
        let frequencies = (0..52).map(|i| (1_612_000_000u64 - i * 20_000_000).to_string()).collect::<Vec<_>>().join(" ");
        let text = format!("CPU {CPUFREQ}/policy0 schedutil 300000 2000000 800000 0,1\nGPU 35 51 0 338000000 MediaTek-Mali\nGPUFREQS {frequencies}\nMTK_GED 0 51\n");
        let saved = recovery_snapshot(&text).expect("GED user limits must be recoverable");
        assert_eq!(snapshot_text(&saved), text);
        // A thermal limiter may change while the original GED controls are restored.
        assert!(restored_text_matches(&saved, &text.replace("GPU 35 51", "GPU 36 51")));
        assert!(!restored_text_matches(&saved, &text.replace("MTK_GED 0 51", "MTK_GED 0 35")));
    }

    #[test]
    fn dimensity_gpu_temperature_is_part_of_the_cooling_gate() {
        let zones = parse_thermal("cpu-big-core7-0 42000\ngpu0 57000\ngpu1 60000\nbattery 65000\nsoc_max 58000\n");
        assert_eq!(soc_max_c(&zones), Some(("gpu1".into(), 60.0)));
        assert!(soc_max_c(&parse_thermal("gpu0 0\ngpu1 2147483647\n")).is_none());
    }

    #[test]
    fn dimensity_root_only_thermal_nodes_are_not_reported_as_missing() {
        struct RootOnly;
        impl Shell for RootOnly {
            fn shell(&self, cmd: &str, _: u64) -> String {
                if cmd.starts_with("su -c '") { "cpu-big-core7-0 42426\ngpu1 41995\nsoc_max 43238\n".into() }
                else { "\n\n\n".into() }
            }
        }
        assert_eq!(soc_max_c(&read_thermal(&RootOnly)), Some(("soc_max".into(), 43.238)));
    }

    #[test]
    fn dimensity_status_preserves_opp_indices_and_converts_khz_once() {
        let raw = "MTK_STATUS_BEGIN\n[GPU   OPP]      Index: 3, Freq: 338000, Volt: 49375\n[PPM Ceiling]    LimitIndex: 2, Limiter: 3\n[PPM Floor]      LimitIndex: 3, Limiter: 0\nMTK_STATUS_END\nMTK_GED 0 3\nMTK_OPP_BEGIN\n[00] freq: 1508000, volt: 78750\n[01] freq: 1508000, volt: 78750\n[02] freq: 1196000, volt: 70625\n[03] freq: 338000, volt: 49375\nMTK_OPP_END\n";
        let s = parse_snapshot(raw);
        assert_eq!(s.gpuclk_hz, Some(338_000_000));
        assert_eq!(s.gpu_freqs, vec![1_508_000_000, 1_508_000_000, 1_196_000_000, 338_000_000]);
        assert_eq!((s.gpu_max_level, s.gpu_min_level), (Some(2), Some(3)));
        assert_eq!(s.mtk_ged, Some((0, 3)));
        assert_eq!(gpu_control_backend(&s), "mediatek_ged");
        assert!(!gpu_locked_at(&s, 2));
    }

    #[test]
    fn dimensity_lock_restores_user_votes_and_rejects_thermal_clamping() {
        use std::cell::RefCell;
        use std::collections::VecDeque;
        struct Phone { path: PathBuf, reads: RefCell<VecDeque<String>>, writes: RefCell<Vec<String>> }
        impl Shell for Phone {
            fn shell(&self, _: &str, _: u64) -> String { self.reads.borrow_mut().pop_front().unwrap() }
            fn shell_write(&self, cmd: &str, _: u64) -> String {
                assert!(read_state(&self.path).unwrap().unwrap().contains("MTK_GED 0 3"));
                assert!(!cmd.contains(KGSL) && !cmd.contains("fix_custom_freq_volt"));
                self.writes.borrow_mut().push(cmd.into());
                if cmd.contains("echo LOCKED") { "LOCKED".into() } else { "RESTORED".into() }
            }
        }
        let f = RecoveryFixture::new();
        let before = format!("CPU {CPUFREQ}/policy0 schedutil 300000 2000000 800000 0,1\nGPU 2 3 0 338000000 MediaTek-Mali\nGPUFREQS 1508000000 1508000000 1196000000 338000000\nMTK_GED 0 3\n");
        let locked = before.replace("schedutil", "performance").replace("GPU 2 3", "GPU 2 2").replace("MTK_GED 0 3", "MTK_GED 2 2");
        let restored = before.replace("GPU 2 3", "GPU 3 3");
        let phone = Phone { path: f.0.join("ged"), reads: RefCell::new(VecDeque::from([locked.clone(), restored.clone()])), writes: RefCell::new(Vec::new()) };
        let lock = Lock::apply(&phone, &phone.path, recovery_snapshot(&before).unwrap(), 2).unwrap();
        assert!(phone.path.is_file());
        assert!(lock.restore(&phone));
        assert!(!phone.path.exists());
        assert!(phone.writes.borrow()[1].contains(&format!("echo 0 > {MTK_GED}/custom_upbound_gpu_freq")));
        let clamped = locked.replace("GPU 2 2", "GPU 3 3");
        *phone.reads.borrow_mut() = VecDeque::from([clamped, restored]);
        let err = Lock::apply(&phone, &phone.path, recovery_snapshot(&before).unwrap(), 2).err().unwrap();
        assert!(err.contains("回读不符"));
        assert!(!phone.path.exists(), "successful rollback clears the original recovery record");
    }

    // ── 电池状态: 功耗读数算不算数全看它 ──

    #[test]
    fn unknown_battery_status_never_certifies_discharge() {
        for status in ["", "Unknown", "NA", "manufacturer-custom"] {
            assert!(!BatteryState { status:status.into(), current_ua:Some(-200_000), capacity_pct:Some(80) }.on_battery());
        }
    }

    fn bat(status: &str, cur: i64, cap: i64) -> BatteryState {
        BatteryState { status: status.into(), current_ua: Some(cur), capacity_pct: Some(cap) }
    }

    #[test]
    fn on_battery_needs_both_not_charging_and_a_discharge_current() {
        // 两个判据都会单独骗人, 所以都要看
        assert!(bat("Discharging", -44_000, 91).on_battery());
        // status 说 Charging 就一票否决, 哪怕电流方向看着像放电
        assert!(!bat("Charging", -1, 91).on_battery());
        // 停充之后有的内核写 Not charging 而不是 Discharging —— 电流方向说了算
        assert!(bat("Not charging", -120_000, 80).on_battery());
        // 电流是正的 = 在往电池里充
        assert!(!bat("Not charging", 771_000, 80).on_battery());
        // 充放平衡过零的瞬间判不出来, 一律不算放电态
        assert!(!bat("Not charging", 0, 80).on_battery());
        assert!(!BatteryState::default().on_battery());
        // 满电空闲 (2026-10-12 实测 Full + -3000 µA): 不是放电态
        assert!(!bat("Full", -3_000, 100).on_battery());
        // 满电但确实在大电流放电 (拔了线): 算放电态
        assert!(bat("Full", -800_000, 100).on_battery());
    }

    #[test]
    fn charging_invalidates_power_even_when_the_reading_looks_perfectly_normal() {
        // 2026-09-23 实测: 插着 USB 时电池轨 0.213 W / USB 轨 7.20 W, 两个都落在
        // 0.05..30 W 窗口里 —— 区间一个都拦不住, 只有电池状态拦得住。
        let s = PowerSample { volt_uv: 4_207_000, curr_ua: -66_000, power_uw: None };
        assert!((POWER_MIN_W..=POWER_MAX_W).contains(&s.watt().unwrap()), "这个量级本来就合理");
        // 不带电池状态: 照常放行 (旧行为)
        assert!(power_usable(PowerRail::Battery, &[s]).is_ok());
        // 带上充电态: 作废
        let e = power_usable_with_battery(PowerRail::Battery, &[s], Some(&bat("Charging", 771_000, 91)))
            .unwrap_err();
        assert!(e.contains("放电态"), "{e}");
        // USB 轨同理, 而且理由不一样 (输入功率含着充电那一份)
        let e = power_usable_with_battery(PowerRail::Usb, &[s], Some(&bat("Charging", 771_000, 91)))
            .unwrap_err();
        assert!(e.contains("充电"), "{e}");
        // 放电态才算数
        assert!(power_usable_with_battery(
            PowerRail::Battery,
            &[s],
            Some(&bat("Discharging", -66_000, 91))
        )
        .is_ok());
    }

    // ── 停充 ──

    #[test]
    fn suspend_value_depends_on_the_node_semantics() {
        assert_eq!(suspend_value(SuspendHow::WriteOne, Some(91)).as_deref(), Some("1"));
        assert_eq!(suspend_value(SuspendHow::WriteZero, Some(91)).as_deref(), Some("0"));
        // 上限型节点必须压到**当前电量以下** —— 压到电量以上等于没压。
        // 本机出厂 end_threshold=80 而电量 91% 照充不误, 就是这个道理。
        assert_eq!(suspend_value(SuspendHow::CapBelowCapacity, Some(91)).as_deref(), Some("86"));
        assert_eq!(suspend_value(SuspendHow::CapBelowCapacity, Some(31)).as_deref(), Some("26"));
        // 电量未知时给不出上限值, 只能跳过这个候选
        assert_eq!(suspend_value(SuspendHow::CapBelowCapacity, None), None);
    }

    #[test]
    fn suspend_value_never_goes_out_of_percent_range() {
        for cap in 0..=100 {
            let v: i64 = suspend_value(SuspendHow::CapBelowCapacity, Some(cap)).unwrap().parse().unwrap();
            assert!((1..=99).contains(&v), "电量 {cap}% 算出上限 {v}");
            // 除非本来就贴着下限, 否则必须严格低于当前电量, 不然不会停充
            if cap > 1 {
                assert!(v < cap, "电量 {cap}% 算出的上限 {v} 没低于电量, 不会停充");
            }
        }
    }

    #[test]
    fn battery_state_parses_and_survives_missing_nodes() {
        let s = parse_battery_state("BAT Charging 771000 91").unwrap();
        assert_eq!((s.status.as_str(), s.current_ua, s.capacity_pct), ("Charging", Some(771_000), Some(91)));
        // 节点缺失时命令回 NA, 不能解析成 0 —— 0 会被当成「充放平衡」
        let s = parse_battery_state("BAT Discharging NA NA").unwrap();
        assert_eq!((s.current_ua, s.capacity_pct), (None, None));
        assert!(!s.on_battery(), "电流读不到就判不出放电态");
        assert_eq!(parse_battery_state("没有这一行"), None);
    }

    #[test]
    fn battery_state_preserves_multiword_status_and_numeric_columns() {
        for (raw, current, capacity) in [
            ("BAT Not charging 16 48", Some(16), Some(48)),
            ("BAT Not charging -120000 48", Some(-120000), Some(48)),
            ("BAT Not charging NA 48", None, Some(48)),
            ("BAT Not charging 173 NA", Some(173), None),
            ("noise\n  BAT\tNot\tcharging\t-16\t48\r\n", Some(-16), Some(48)),
        ] {
            let state = parse_battery_state(raw).expect("complete battery sample");
            assert_eq!(state.status, "Not charging", "{raw}");
            assert_eq!(state.current_ua, current, "{raw}");
            assert_eq!(state.capacity_pct, capacity, "{raw}");
        }
    }

    #[test]
    fn battery_state_rejects_truncation_and_keeps_unavailable_values_unknown() {
        for raw in ["BAT", "BAT Charging", "BAT Charging -100", "BATTERY Charging -100 48"] {
            assert_eq!(parse_battery_state(raw), None, "{raw}");
        }
        for raw in ["BAT Not charging NA NA", "BAT Full invalid invalid"] {
            let state = parse_battery_state(raw).unwrap();
            assert_eq!(state.current_ua, None);
            assert_eq!(state.capacity_pct, None);
        }
        for raw in ["BAT Not charging 16 173", "BAT Discharging -100 -1"] {
            assert_eq!(parse_battery_state(raw).unwrap().capacity_pct, None, "{raw}");
        }
    }

    #[test]
    fn battery_state_parsed_supply_requires_both_status_and_current() {
        for (raw, on_battery) in [
            ("BAT Not charging -120000 48", true),
            ("BAT Not charging 16 48", false),
            ("BAT Not charging 0 48", false),
            ("BAT Not charging NA 48", false),
            ("BAT Charging -120000 48", false),
            ("BAT Unknown -120000 48", false),
        ] {
            assert_eq!(parse_battery_state(raw).unwrap().on_battery(), on_battery, "{raw}");
        }
    }

    #[test]
    fn charge_ctl_state_file_roundtrips_and_rejects_garbage() {
        let t = charge_ctl_state_text("/sys/class/power_supply/battery/input_suspend", "0");
        let (p, v) = parse_charge_ctl_state(&t).unwrap();
        assert_eq!((p.as_str(), v.as_str()), ("/sys/class/power_supply/battery/input_suspend", "0"));
        // 只有一行 / 空值 / 不是 sysfs 路径的, 一律不认 ——
        // 认了就会拿半份状态去写设备, 比不回滚更糟
        assert_eq!(parse_charge_ctl_state("/sys/class/power_supply/battery/x"), None);
        assert_eq!(parse_charge_ctl_state(""), None);
        assert_eq!(parse_charge_ctl_state("rm -rf /\n0"), None);
    }

    #[test]
    fn the_candidate_that_works_on_this_device_is_in_the_list() {
        // 本机红魔 NX809J (pmic-glink) 实测: power_supply 下的候选一个都停不了充,
        // 真正管用的是 qcom-battery 那条。它不在 power_supply class 下,
        // 只看 power_supply 会整个错过。
        assert!(CHARGE_CTL_CANDIDATES
            .iter()
            .any(|n| n.path == "/sys/class/qcom-battery/charging_enabled"
                && n.how == SuspendHow::WriteZero));
    }

    #[test]
    fn settle_window_outlasts_the_fuel_gauge_lag() {
        // status 立刻翻成 Discharging, 但 current_now 要几秒才跟上:
        // 实测停充后三秒内还在报 +1072000/+982000, 第三秒才翻成 -325000。
        // 窗口短于这个延迟就会把生效了的节点误判成无效。
        assert!(SUSPEND_SETTLE_MS >= 8_000, "实得 {SUSPEND_SETTLE_MS} ms");
    }

    #[test]
    fn every_charge_ctl_candidate_is_a_sysfs_path() {
        // 状态文件回滚时会把这些路径原样拼进 su -c 'echo ... > <path>';
        // 非 sysfs 路径混进来就是往任意位置写
        for n in CHARGE_CTL_CANDIDATES {
            assert!(n.path.starts_with("/sys/"), "{}", n.path);
            assert!(!n.path.contains(' '), "{}", n.path);
        }
    }

    #[test]
    fn low_battery_blocks_suspending_the_charger() {
        // 门槛本身钉死: 停充期间整机纯靠电池, 电量低时再满载抽几分钟会测到一半关机
        assert_eq!(MIN_CAPACITY_FOR_SUSPEND_PCT, 30);
    }

    #[test]
    fn a_garbage_power_now_node_is_rejected_not_reported() {
        // 2026-09-23 红魔 NX809J 实测: battery/power_now 读出 992195296。
        // 按 μW 折算就是 992 W, 而同一时刻 current_now x voltage_now 只有 0.185 W ——
        // 这个节点在这台机器上给的根本不是 μW。加上界之前, 它会当成一次正常实测报上去。
        let s = PowerSample { volt_uv: 4_208_000, curr_ua: -44_000, power_uw: Some(992_195_296) };
        // 改了口径之后, 同一条采样算出的是 V x I 的 0.185 W, 而不再是 992 W
        assert!((s.watt().unwrap() - 0.185_152).abs() < 1e-6, "实得 {:?}", s.watt());
        // V x I 算不出来时不退回 power_now (审计 #15): 不给数, 整窗判「量不了」
        let only_pn = PowerSample { volt_uv: 0, curr_ua: 0, power_uw: Some(992_195_296) };
        assert_eq!(only_pn.watt(), None);
        assert!(power_usable(PowerRail::Battery, &[only_pn]).is_err());
    }

    #[test]
    fn one_out_of_range_sample_invalidates_the_whole_window() {
        // 逐条判, 不是判均值: 29 条 5 W 混进一条 300 W, 均值 14.8 W 正好落在窗口内
        let ok = PowerSample { volt_uv: 4_200_000, curr_ua: -1_190_000, power_uw: None };
        let mut v = vec![ok; 29];
        v.push(PowerSample { volt_uv: 4_200_000, curr_ua: -71_400_000, power_uw: None });
        let (mean, _, _, _) = power_stats(&v).unwrap();
        assert!((POWER_MIN_W..=POWER_MAX_W).contains(&mean), "均值 {mean} 本来就落在窗口内");
        assert!(power_usable(PowerRail::Battery, &v).is_err(), "但整组必须作废");
    }

    #[test]
    fn an_ordinary_load_reading_still_passes() {
        // 别把正常读数也拦了: 1.19 A x 4.2 V = 5.0 W
        let s = PowerSample { volt_uv: 4_200_000, curr_ua: -1_190_000, power_uw: None };
        assert!(power_usable(PowerRail::Battery, &[s]).is_ok());
    }

    // ---------- 快照与热区 (口径由 bench 迁入, 行为必须逐字不变) ----------

    #[test]
    fn snapshot_thermal_and_levels() {
        let snap = parse_snapshot(
            "CPU /sys/devices/system/cpu/cpufreq/policy0 schedutil 300000 2400000 1200000 0,1,2\n\
             GPU 3 17 3 902000000 Adreno840\n\
             GPUFREQS 1200000000 1100000000 1000000000 902000000\n\
             BUS DDR 547000 5333000 547000\n\
             BUS LLCC 300000 1400000 300000\n",
        );
        assert_eq!(snap.cpu.len(), 1);
        assert_eq!(snap.cpu[0].governor, "schedutil");
        assert_eq!(snap.gpu_max_level, Some(3));
        assert_eq!(snap.gpu_min_level, Some(17));
        assert_eq!(snap.gpu_model.as_deref(), Some("Adreno840"));
        assert_eq!(snap.bus.len(), 2);
        assert_eq!(snap.bus[0].max_khz, 5333000);

        // 快照往返: 文本 → 结构 → 文本 → 结构, 必须等价
        let back = parse_snapshot(&snapshot_text(&snap));
        assert_eq!(back.cpu, snap.cpu);
        assert_eq!(back.bus, snap.bus);
        assert_eq!(back.gpu_max_level, snap.gpu_max_level);

        // 热区筛选: 只认 SoC 结温前缀, 跳过 0 与 >=100C 哨兵
        let zones = parse_thermal(
            "cpu-0-0 45000\nskin-therm 38000\ngpuss-0 52000\ncpu-1-0 0\nbattery 99000\nsoc-dummy 150000\n",
        );
        let (zone, c) = soc_max_c(&zones).unwrap();
        assert_eq!(zone, "gpuss-0");
        assert!((c - 52.0).abs() < 1e-9);

        assert_eq!(nearest_level(&snap.gpu_freqs, 902), Some(3));
        assert_eq!(nearest_level(&snap.gpu_freqs, 1190), Some(0));
    }

    #[test]
    fn dispersion_and_samples() {
        let (median, min, max, disp) = dispersion_pct(&[100.0, 104.0, 102.0]);
        assert!((median - 102.0).abs() < 1e-9);
        assert!((min - 100.0).abs() < 1e-9);
        assert!((max - 104.0).abs() < 1e-9);
        assert!((disp - (4.0 / 102.0 * 100.0)).abs() < 1e-9);
        assert_eq!(dispersion_pct(&[]), (0.0, 0.0, 0.0, 0.0));

        let s = parse_samples("900 111 222\n900 111 333\n800 111 222\n");
        assert_eq!(s[0], (900, 800, 900));
        assert_eq!(s[1], (111, 111, 111));
    }

    #[test]
    fn snapshot_cmd_is_single_quoted_root_payload() {
        let c = snapshot_cmd();
        assert!(c.starts_with("su -c '") && c.ends_with('\''));
        assert!(c.contains("max_pwrlevel") && c.contains("gpu_available_frequencies"));
        assert!(c.contains("DDR") && c.contains("LLCC"));
    }

    #[test]
    fn lock_state_path_separates_paths_and_serials() {
        let f = RecoveryFixture::new();
        let root = f.0.join("not-created");
        let path = |tag, serial| recovery_path(&root, tag, Some(serial), &[]).unwrap();
        let a = path("bench", "R5CT30ABCDE");
        let b = path("gpuop", "R5CT30ABCDE");
        let c = path("bench", "other");
        assert_ne!(a, b, "不同通路的锁态文件不能互相覆盖");
        assert_ne!(a, c, "不同设备的锁态文件不能互相覆盖");
        assert_ne!(path("bench", "a-b"), path("bench", "a_b"));
        assert!(path("bench", "127.0.0.1:5555").ends_with("phonefarm-bench-lock-127%2E0%2E0%2E1%3A5555.txt"));
        assert!(recovery_path(&root, "bench", None, &[]).is_err());
        assert!(!root.exists(), "查询不创建目录");
        fs::create_dir(&root).unwrap();
        let legacy = root.join("phonefarm-bench-lock-a_b.txt");
        fs::write(&legacy, "old original").unwrap();
        assert!(recovery_path(&root, "bench", Some("a-b"), &[]).unwrap_err().contains("legacy recovery record"));
        assert_eq!(fs::read_to_string(&legacy).unwrap(), "old original");
        assert!(recovery_path(&f.0.join("new"), "bench", Some("a-b"), &[root]).is_err());
        let unbound = f.0.join("phonefarm-bench-lock-default.txt");
        fs::write(&unbound, "unknown device").unwrap();
        assert!(recovery_path(&f.0, "bench", Some("serial"), &[]).is_err());
        assert!(unbound.is_file());
    }

    struct RecoveryFixture(PathBuf);
    impl RecoveryFixture {
        fn new() -> Self {
            let p = std::env::temp_dir().join(format!("hwcond-{}-{}", std::process::id(),
                std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
            fs::create_dir(&p).unwrap();
            Self(p.canonicalize().unwrap())
        }
    }
    impl Drop for RecoveryFixture {
        fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); }
    }

    #[test]
    fn owned_charge_binds_before_write_and_does_not_treat_errors_as_absence() {
        use std::cell::{Cell,RefCell};
        struct Phone<'a> {path:&'a Path,bound:Cell<bool>,value:Cell<i32>,writes:RefCell<Vec<String>>,permission:bool,uncertain:Cell<bool>,fail_write:bool}
        impl Shell for Phone<'_> {
            fn shell(&self,_:&str,_:u64)->String {panic!("owned charge must use typed optional/battery reads")}
            fn shell_write(&self,cmd:&str,_:u64)->String {
                assert!(self.bound.get());assert!(charge_record(self.path).unwrap().is_some());
                self.writes.borrow_mut().push(cmd.into());self.value.set(if cmd.contains("echo 1 "){1}else{0});
                if self.fail_write{self.uncertain.set(true);}String::new()
            }
            fn check(&self)->Result<(),String> {if self.uncertain.get(){Err("transport completion unknown".into())}else{Ok(())}}
            fn charge_node(&self,path:&str)->Result<Option<String>,String> {
                self.check()?;
                if self.permission{return Err("permission denied".into());}
                if path==CHARGE_CTL_CANDIDATES[0].path{return Ok(None);}
                Ok(Some(self.value.get().to_string()))
            }
            fn charge_battery(&self)->Result<BatteryState,String> {
                self.check()?;Ok(BatteryState{status:if self.value.get()==1{"Discharging"}else{"Charging"}.into(),current_ua:Some(if self.value.get()==1{-100000}else{100000}),capacity_pct:Some(80)})
            }
        }
        let f=RecoveryFixture::new();let path=f.0.join("charge");
        let phone=Phone{path:&path,bound:Cell::new(false),value:Cell::new(0),writes:RefCell::new(Vec::new()),permission:false,uncertain:Cell::new(false),fail_write:false};
        let mut guard=ChargeSuspend::apply_with_hooks(&phone,&path,|node,saved,_|{
            assert_eq!(node,CHARGE_CTL_CANDIDATES[1].path);assert_eq!(charge_record(&path)?,Some((node.into(),saved.into())));phone.bound.set(true);Ok(())
        },||Ok(()),||Ok(())).unwrap();
        assert!(guard.restore_checked(&phone).unwrap());assert!(!path.exists());assert_eq!(phone.writes.borrow().len(),2);
        let denied=Phone{permission:true,..phone};
        assert!(ChargeSuspend::apply(&denied,&path).err().unwrap().contains("permission denied"));
        assert_eq!(denied.writes.borrow().len(),2);assert!(!path.exists());
        let uncertain=Phone{permission:false,fail_write:true,bound:Cell::new(false),..denied};
        assert!(ChargeSuspend::apply_with_hooks(&uncertain,&path,|_,_,_|{uncertain.bound.set(true);Ok(())},||Ok(()),||Ok(())).is_err());
        assert_eq!(uncertain.writes.borrow().len(),3);assert!(charge_record(&path).unwrap().is_some());
    }

    #[test]
    fn recovery_records_never_overwrite_or_hide_io_errors() {
        let f = RecoveryFixture::new();
        let p = f.0.join("nested/state");
        assert_eq!(read_state(&p).unwrap(), None);
        save_state(&p, "original").unwrap();
        assert!(save_state(&p, "replacement").is_err());
        assert_eq!(read_state(&p).unwrap().as_deref(), Some("original"));
        let bytes = fs::read(&p).unwrap();
        fs::write(&p, &bytes[..bytes.len()-1]).unwrap();
        assert!(read_state(&p).unwrap_err().contains("truncated"));
        assert!(p.is_file());
        clear_state(&p).unwrap();
        fs::create_dir(&p).unwrap();
        assert!(read_state(&p).is_err());
        assert!(p.is_dir());
        let blocked = f.0.join("file");
        fs::write(&blocked, "not a directory").unwrap();
        assert!(save_state(&blocked.join("state"), "value").is_err());
        assert_eq!(fs::read_to_string(blocked).unwrap(), "not a directory");
    }

    #[test]
    fn recovery_rejects_partial_snapshot_and_wrong_node_identity() {
        let text = format!("CPU {CPUFREQ}/policy0 schedutil 300 1000 700 0,1\nGPU 0 4 0 500 Adreno\nBUS DDR 100 900 700\n");
        let s = recovery_snapshot(&text).unwrap();
        for damaged in [text.replace("GPU 0", "GPU broken"), text.replace("BUS DDR 100 900 700", "BUS DDR"),
            text.replace("GPU 0 4 0 500 Adreno\n", ""), text.replace("schedutil", "a;reboot")] {
            assert!(recovery_snapshot(&damaged).is_err());
        }
        let mut wrong = s.clone();
        wrong.cpu[0].path = format!("{CPUFREQ}/policy4");
        assert!(!restored_snapshot_matches(&s, &wrong));
        wrong = s.clone();
        wrong.bus[0].name = "LLCC".into();
        assert!(!restored_snapshot_matches(&s, &wrong));
        assert!(restored_snapshot_matches(&s, &s));
        let zero = text.replace("BUS DDR 100", "BUS DDR 0");
        let baseline = recovery_snapshot(&zero).unwrap();
        assert!(restored_text_matches(&baseline, &zero));
        assert!(!restored_text_matches(&baseline, &zero.replace("BUS DDR 0", "BUS DDR unreadable")));
        assert!(parse_charge_ctl_state("/sys/class/power_supply/battery/input_suspend\n0;reboot\n").is_none());
    }

    // ---------- 功耗遥测 ----------

    #[test]
    fn power_prefers_volts_times_amps_over_the_device_reported_wattage() {
        let s = PowerSample {
            volt_uv: 4_400_000,
            curr_ua: 1_000_000,
            power_uw: Some(3_900_000),
        };
        // 口径 2026-09-23 反转过: 能算 V x I 就算 V x I, 不信 power_now。
        // 原因见 watt() 的注释 —— 本机 power_now 给的不是 μW, 会算出几百瓦。
        assert!((s.watt().unwrap() - 4.4).abs() < 1e-9, "实得 {:?}", s.watt());
    }

    #[test]
    fn power_now_is_never_used_when_v_or_i_is_missing() {
        // 审计 #15: 电流为 0 时不再退回 power_now —— 本机实测读到过 537393296 和 −61082000
        let s = PowerSample { volt_uv: 4_400_000, curr_ua: 0, power_uw: Some(537_393_296) };
        assert_eq!(s.watt(), None);
        let s = PowerSample { volt_uv: 4_400_000, curr_ua: 0, power_uw: Some(-61_082_000) };
        assert_eq!(s.watt(), None);
        let none = PowerSample { volt_uv: 0, curr_ua: 0, power_uw: None };
        assert_eq!(none.watt(), None);
        assert!(power_usable(PowerRail::Battery, &[none]).is_err());
    }

    #[test]
    fn power_falls_back_to_volts_times_amps() {
        let s = PowerSample {
            volt_uv: 5_127_000,
            curr_ua: 144_000,
            power_uw: None,
        };
        assert!((s.watt().unwrap() - 5.127 * 0.144).abs() < 1e-6);
    }

    /// 放电电流在不同内核上有正有负, 功率取大小。
    #[test]
    fn power_is_sign_agnostic() {
        let pos = PowerSample { volt_uv: 4_000_000, curr_ua: 500_000, power_uw: None };
        let neg = PowerSample { volt_uv: 4_000_000, curr_ua: -500_000, power_uw: None };
        assert!((pos.watt().unwrap() - neg.watt().unwrap()).abs() < 1e-12);
        assert!((pos.watt().unwrap() - 2.0).abs() < 1e-9);
    }

    #[test]
    fn parses_power_lines_and_tolerates_missing_power_now() {
        let v = parse_power_samples("PWR 5127000 144000 NA\nPWR 4411000 0 0\nnoise\nPWR 4400000 -900000 3960000\n");
        assert_eq!(v.len(), 3);
        assert_eq!(v[0].power_uw, None);
        assert_eq!(v[1].power_uw, Some(0));
        assert_eq!(v[2].power_uw, Some(3_960_000));
    }

    #[test]
    fn power_stats_reports_mean_median_and_range() {
        let s = vec![
            PowerSample { volt_uv: 1_000_000, curr_ua: 1_000_000, power_uw: None }, // 1 W
            PowerSample { volt_uv: 1_000_000, curr_ua: 3_000_000, power_uw: None }, // 3 W
            PowerSample { volt_uv: 1_000_000, curr_ua: 2_000_000, power_uw: None }, // 2 W
        ];
        let (mean, median, min, max) = power_stats(&s).unwrap();
        assert!((mean - 2.0).abs() < 1e-9);
        assert!((median - 2.0).abs() < 1e-9);
        assert!((min - 1.0).abs() < 1e-9);
        assert!((max - 3.0).abs() < 1e-9);
        assert!(power_stats(&[]).is_none());
    }

    /// 这条是防「拿 0 当结论」的闸: 充电中电池轨恒为 0,
    /// 那不是「功耗为零」, 而是「这条轨此刻量不了」。
    #[test]
    fn an_all_zero_rail_is_reported_as_unmeasurable_not_as_zero_watts() {
        let charging = vec![
            PowerSample { volt_uv: 4_411_000, curr_ua: 0, power_uw: Some(0) },
            PowerSample { volt_uv: 4_411_000, curr_ua: 0, power_uw: Some(0) },
        ];
        let err = power_usable(PowerRail::Battery, &charging).unwrap_err();
        assert!(err.contains("充电"), "{err}");

        let usb_off = vec![PowerSample { volt_uv: 0, curr_ua: 0, power_uw: None }];
        assert!(power_usable(PowerRail::Usb, &usb_off).is_err());

        let real = vec![PowerSample { volt_uv: 5_127_000, curr_ua: 144_000, power_uw: None }];
        assert!(power_usable(PowerRail::Usb, &real).is_ok());
        assert!(power_usable(PowerRail::Battery, &[]).is_err());
    }

    #[test]
    fn power_sample_cmd_targets_the_right_rail() {
        assert!(power_sample_cmd(PowerRail::Usb).contains("power_supply/usb/voltage_now"));
        assert!(power_sample_cmd(PowerRail::Battery).contains("power_supply/battery/power_now"));
        assert!(power_sample_cmd(PowerRail::Usb).starts_with("su -c '"));
    }

    // ---------- 供电判定 (审计 #5 / #15) ----------

    fn obs(status: &str, ua: f64, usb_w: Option<f64>, susp: bool, bypass: bool) -> SupplyMode {
        classify_supply(&SupplyObs { status, battery_ua: Some(ua), usb_w, charge_suspended: susp, bypass_mode: bypass })
    }

    #[test]
    fn supply_classification_matches_the_measured_cases() {
        // 放电、USB 不出力: 电池轨
        assert_eq!(obs("Discharging", -900_000.0, Some(0.0), false, false), SupplyMode::Battery);
        // 放电、USB 也在出力: 混合
        assert_eq!(obs("Not charging", -1_500_000.0, Some(0.5), true, false), SupplyMode::Mixed);
        // 2026-10-21 selftest: 停充后 USB 被限到约 0.49 A, status 仍写 Charging, 电池在放
        assert_eq!(obs("Charging", -600_000.0, Some(2.4), true, false), SupplyMode::Mixed);
        // 没停充时同样的读数不认 (充电器随时会变)
        assert_eq!(obs("Charging", -600_000.0, Some(2.4), false, false), SupplyMode::NotDischarging);
        // 满电停充旁路, 电池 ≈ 0
        assert_eq!(obs("Full", -2_000.0, Some(4.0), true, true), SupplyMode::Bypass);
        // 2026-10-12 Megacity: 同一轮里旁路模式下有臂 USB 掉成 0、电池放 0.64 A —— 仍是同一口径
        let flipped = obs("Full", -640_000.0, Some(0.0), true, true);
        assert_eq!(flipped, SupplyMode::Mixed);
        assert_eq!(flipped.rail(), SupplyMode::Bypass.rail());
        // 没有旁路标记的停充 (hwcond 单轨通路): 电池 ≈ 0 且 USB 在出力
        assert_eq!(obs("Charging", 10_000.0, Some(1.0), true, false), SupplyMode::Bypass);
        // 满电空闲没停充
        assert_eq!(obs("Full", -3_000.0, Some(4.0), false, false), SupplyMode::FullIdle);
        // 在充
        assert_eq!(obs("Charging", 771_000.0, Some(7.2), true, true), SupplyMode::NotDischarging);
        assert_eq!(
            classify_supply(&SupplyObs { status: "Charging", battery_ua: None, usb_w: None, charge_suspended: true, bypass_mode: true }),
            SupplyMode::Unknown
        );
        assert!(!SupplyMode::FullIdle.measurable() && SupplyMode::Mixed.measurable());
    }

    #[test]
    fn single_rail_path_accepts_bypass_and_refuses_mixed_with_a_reason() {
        let usb = vec![PowerSample { volt_uv: 5_000_000, curr_ua: 800_000, power_uw: None }; 5];
        // 停充后旁路: USB 轨就是整机
        assert!(power_usable_supply(PowerRail::Usb, &usb, Some(&bat("Charging", 10_000, 100)), true).is_ok());
        // 没停充: 同样的读数不认
        assert!(power_usable_supply(PowerRail::Usb, &usb, Some(&bat("Charging", 10_000, 100)), false).is_err());
        // 停充后电池在补: 单轨量不全, 明确说供电不足
        let e = power_usable_supply(PowerRail::Usb, &usb, Some(&bat("Charging", -600_000, 100)), true).unwrap_err();
        assert!(e.contains("供电不足"), "{e}");
        // 电池不进不出时电池轨量不到整机 (单轨通路看不到 USB, 判不出旁路, 只能拒绝)
        assert!(power_usable_supply(PowerRail::Battery, &usb, Some(&bat("Charging", 10_000, 100)), true).is_err());
        // 放电态 + 电池轨照旧
        let b = vec![PowerSample { volt_uv: 4_000_000, curr_ua: -900_000, power_uw: None }; 5];
        assert!(power_usable_supply(PowerRail::Battery, &b, Some(&bat("Discharging", -900_000, 80)), false).is_ok());
    }

    #[test]
    fn zero_current_samples_are_missing_not_garbage_watts() {
        let ok = PowerSample { volt_uv: 4_000_000, curr_ua: -1_000_000, power_uw: Some(537_393_296) };
        let zero = PowerSample { volt_uv: 4_000_000, curr_ua: 0, power_uw: Some(-61_082_000) };
        // 20 条里 1 条电流为 0: 跳过它, 其余照常, 统计里没有 power_now 的怪数
        let mut v = vec![ok; 19];
        v.push(zero);
        assert!(power_usable(PowerRail::Battery, &v).is_ok());
        let (mean, _, min, max) = power_stats(&v).unwrap();
        assert!((mean - 4.0).abs() < 1e-9 && (min - 4.0).abs() < 1e-9 && (max - 4.0).abs() < 1e-9);
        // 超过一成缺测: 这段窗口不认
        let mut v = vec![ok; 8];
        v.extend([zero, zero]);
        let e = power_usable(PowerRail::Battery, &v).unwrap_err();
        assert!(e.contains("电压或电流为 0"), "{e}");
        assert!(power_stats(&[zero]).is_none());
    }

    #[test]
    fn bypass_needs_the_node_to_have_cut_the_charge_current() {
        // 写之前 +1.93 A, 第一次读数 +0.2 A (降了九成), 后来落到 30 mA: 旁路成立
        assert!(bypass_settled(Some(1_930_000), &[200_000, 90_000, 30_000]));
        // 没降三成: 节点没起作用
        assert!(!bypass_settled(Some(1_930_000), &[1_800_000, 20_000]));
        // 写之前在放电: 不是旁路的样子
        assert!(!bypass_settled(Some(-600_000), &[10_000]));
        // 满电空闲 (写之前就 ≈0): 只要 |I| < 门限
        assert!(bypass_settled(Some(-3_000), &[-2_000]));
        // 一直没落到门限以下
        assert!(!bypass_settled(Some(1_000_000), &[600_000, 400_000, 200_000]));
        assert!(!bypass_settled(Some(1_000_000), &[]));
    }

    /// 审查 R1 #2: 混合供电现场 (Charging、−0.6 A) 的停充判据, 与 charge_suspend.sh 的 mixed_one 一致。
    #[test]
    fn mixed_supply_counts_as_suspended_when_battery_discharges_after_the_write() {
        // 写前 +1.93 A 在充, 写后 −0.6 A 在放电: 降了远超三成
        assert!(mixed_settled(Some(1_930_000), -600_000));
        // 写后只是接近 0 (旁路的样子), 不是混合
        assert!(!mixed_settled(Some(1_930_000), -20_000));
        // 写后仍在充
        assert!(!mixed_settled(Some(1_930_000), 1_500_000));
        // 写前就在放电 −0.6 A: 要再降三成才算节点起了作用 (−0.9 A 算, −0.7 A 不算)
        assert!(mixed_settled(Some(-600_000), -900_000));
        assert!(!mixed_settled(Some(-600_000), -700_000));
        // 写前 ≈ 0: 只要在放电
        assert!(mixed_settled(Some(0), -100_000));
        // 写前读不到: 判不了
        assert!(!mixed_settled(None, -600_000));
        // 要连续 MIXED_TRIES 次; 这两串都不是旁路
        assert!(mixed_streak(Some(1_930_000), &[1_800_000, -500_000, -600_000, -620_000]));
        assert!(!mixed_streak(Some(1_930_000), &[-600_000, -620_000]));
        assert!(!mixed_streak(Some(1_930_000), &[-600_000, 100_000, -620_000, -600_000]));
        assert!(!bypass_settled(Some(1_930_000), &[-500_000, -600_000, -620_000]));
    }

    #[test]
    fn usb_limit_check_reports_supply_short_explicitly() {
        let c = usb_supply_check("USBLIM 490000 NA 5000000\n", USB_SUPPLY_NEED_W);
        assert_eq!(c.limit_ua, Some(490_000));
        assert!((c.max_w.unwrap() - 2.45).abs() < 1e-9);
        assert!(c.short && c.note.starts_with("供电不足"), "{}", c.note);
        // input_current_limit 读不到时退回 current_max
        let c = usb_supply_check("USBLIM NA 3000000 9000000\n", USB_SUPPLY_NEED_W);
        assert_eq!(c.limit_ua, Some(3_000_000));
        assert!(!c.short);
        // 读不到就不判, 但说清楚
        let c = usb_supply_check("garbage", USB_SUPPLY_NEED_W);
        assert!(!c.short && c.note.contains("读不到"));
    }
}
