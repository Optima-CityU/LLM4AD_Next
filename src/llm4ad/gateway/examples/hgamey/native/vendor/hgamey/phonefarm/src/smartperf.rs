//! smartperf: OpenHarmony 侧的性能采集端 —— HiSmartPerf 的设备端 `SP_daemon` 与 Xpower。
//!
//! 为什么单独成模块: 安卓侧的功耗/帧时取自 sysfs 与 Vulkan 时间戳 (`hwcond` / `gpuop`),
//! 鸿蒙侧没有对应的 sysfs 口径, 官方通道是 HiSmartPerf。两边的采集手段完全不同,
//! 但上层 (`game_opt_loop` 的 `eval_report`) 只认同一组字段。本模块只负责
//! 「鸿蒙设备 → 统一字段」这一段, 归一化后的契约在 `perfsrc`。
//!
//! **命令口径的来源** (全部取自本机 HiSmartPerf-Editor 1.42 与官方文档, 未凭空构造):
//!
//! | 命令 | 出处 |
//! |---|---|
//! | `hdc -t <k> shell "SP_daemon --version"` | HiSmartPerf-Editor `app.asar` 设备探测 |
//! | `hdc -t <k> shell "SP_daemon -deviceinfo"` | 同上 |
//! | `hdc -t <k> shell SP_daemon -clear` | 同上 (采集前清残留) |
//! | `SP_daemon -N <次数> -PKG <包名> -c -g -t -p -r -f` | 官方 SmartPerf-Device 文档的参数表 |
//! | 结果落盘 `/data/local/tmp/data.csv` | 同上 |
//! | `hdc -t <k> shell hidumper -s 1213 -a -b\|-f\|--dumpDb` | `app.asar` Xpower(dubai) 落盘刷写 |
//! | `hdc -t <k> file recv /data/log/xpower/dump/dubai.db <本地>` | 同上 |
//!
//! **单位口径** (官方文档 `SP_daemon` CSV 字段说明):
//!   - `fpsJitters`: 单帧绘制间隔, **纳秒**;
//!   - `currentNow`: **mA**; `voltageNow`: **μV**;
//!   - `shell_front/shell_frame/shell_back/soc_thermal/system_h`: **摄氏度**。
//!   频率类字段 (`cpuFrequ`/`gpuFrequency`/`ddrFrequency`) 在文档与实测报告里单位不自洽
//!   (文档写 Hz, 报告里的 `cpu-c0-max,3628800` 显然是 kHz), 故本模块**不做频率换算**,
//!   只把原值放进 `extras`, 不进统一契约 —— 宁可不给, 不给一个单位错的数。
//!
//! 纪律: 本文件里所有解析都是纯函数, 同一份输入每次重算逐字节一致; 采不到的字段一律
//! `None` 并在 `unavailable` 里写明原因, **绝不填 0** (插 USB 充电时功耗恒为垃圾值,
//! 一个 0.000 W 看起来像数字, 其实是「此刻量不了」)。

use std::collections::BTreeMap;

// ══════════════ 设备侧路径与命令 ══════════════

/// `SP_daemon` 采集结果的落盘路径 (官方文档)。
pub const SP_CSV_REMOTE: &str = "/data/local/tmp/data.csv";
/// Xpower 的落盘数据库 (HiSmartPerf 取功耗就是拉这个文件)。
pub const XPOWER_DB_REMOTE: &str = "/data/log/xpower/dump/dubai.db";
/// Xpower 服务在 hidumper 里的 service id。
pub const XPOWER_SA_ID: u32 = 1213;

/// `SP_daemon` 采集项开关。字段名对齐官方参数表, 不自创。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SpFlags {
    /// `-c` CPU
    pub cpu: bool,
    /// `-g` GPU
    pub gpu: bool,
    /// `-f` FPS (含 fpsJitters 帧间隔)
    pub fps: bool,
    /// `-t` 温度
    pub temp: bool,
    /// `-p` 电流
    pub power: bool,
    /// `-r` 内存
    pub ram: bool,
}

impl Default for SpFlags {
    /// 默认采齐 gpu-op 关心的四类: 帧时、功耗、温度、GPU。
    fn default() -> Self {
        SpFlags { cpu: true, gpu: true, fps: true, temp: true, power: true, ram: false }
    }
}

impl SpFlags {
    /// 按官方参数表拼开关串 (顺序固定, 保证命令可复现)。
    pub fn to_args(self) -> String {
        let mut s = String::new();
        for (on, flag) in [
            (self.cpu, "-c"),
            (self.gpu, "-g"),
            (self.fps, "-f"),
            (self.temp, "-t"),
            (self.power, "-p"),
            (self.ram, "-r"),
        ] {
            if on {
                if !s.is_empty() {
                    s.push(' ');
                }
                s.push_str(flag);
            }
        }
        s
    }
}

/// 采集命令: `SP_daemon -N <次数> [-PKG <包名>] <开关>`。
///
/// `-N` 是官方参数表里唯一的必选项 (采集次数, 每次约一秒)。`-PKG` 只在需要
/// 应用级 FPS/RAM 时给; 不给就是整机口径。
pub fn collect_cmd(rounds: u32, pkg: Option<&str>, flags: SpFlags) -> String {
    let mut s = format!("SP_daemon -N {}", rounds.max(1));
    if let Some(p) = pkg {
        let p = p.trim();
        if !p.is_empty() {
            s.push_str(&format!(" -PKG {p}"));
        }
    }
    let f = flags.to_args();
    if !f.is_empty() {
        s.push(' ');
        s.push_str(&f);
    }
    s
}

/// 版本探测命令 (HiSmartPerf 用它判断设备端 SP_daemon 在不在)。
pub fn version_cmd() -> String {
    "SP_daemon --version".into()
}

/// 设备信息命令。
pub fn deviceinfo_cmd() -> String {
    "SP_daemon -deviceinfo".into()
}

/// 清残留命令 (采集前跑一次, 免得读到上一轮的 data.csv)。
pub fn clear_cmd() -> String {
    "SP_daemon -clear".into()
}

/// Xpower 落盘刷写命令三连。
///
/// Xpower 的功耗明细平时在服务内存里, 不刷这三条就拉不到完整的 `dubai.db`。
/// 与 HiSmartPerf 在 hdc 分支上发的三条完全一致。
pub fn xpower_flush_cmds() -> [String; 3] {
    [
        format!("hidumper -s {XPOWER_SA_ID} -a -b"),
        format!("hidumper -s {XPOWER_SA_ID} -a -f"),
        format!("hidumper -s {XPOWER_SA_ID} -a --dumpDb"),
    ]
}

/// Xpower 数据库落盘前先删旧文件, 防止拉回上一轮的陈旧内容。
pub fn xpower_rm_cmd() -> String {
    format!("rm -rf {XPOWER_DB_REMOTE}")
}

// ══════════════ SP_daemon 一次采样 ══════════════

/// `SP_daemon` 的一条采样记录。
///
/// 字段只收官方 CSV 字段说明里列过的那些; 其余原样进 `extras`, 不猜含义也不猜单位。
#[derive(Debug, Clone, Default, PartialEq)]
pub struct SpSample {
    /// `timeStamp` (毫秒)。文档把它写作「与每个采集点对应的时间戳」。
    pub timestamp_ms: Option<i64>,
    /// `fps`
    pub fps: Option<f64>,
    /// `refreshrate` 屏幕刷新率
    pub refresh_rate: Option<f64>,
    /// `fpsJitters` 逐帧绘制间隔, **纳秒**原值
    pub frame_intervals_ns: Vec<f64>,
    /// `currentNow`, **mA**
    pub current_ma: Option<f64>,
    /// `voltageNow`, **μV**
    pub voltage_uv: Option<f64>,
    /// `soc_thermal` / `soc-thermal`, 摄氏度
    pub soc_temp_c: Option<f64>,
    /// `gpu` 热区, 摄氏度
    pub gpu_temp_c: Option<f64>,
    /// `Battery` 热区, 摄氏度
    pub battery_temp_c: Option<f64>,
    /// `shell_frame` 中框温, 摄氏度
    pub shell_frame_temp_c: Option<f64>,
    /// 未归一化的原始键值 (频率、内存、网络等), 保留原值与原名, 不带单位承诺
    pub extras: BTreeMap<String, String>,
}

impl SpSample {
    /// 这条采样折算出的整机瞬时功率 (瓦)。
    ///
    /// `|mA| / 1000 * |μV| / 1e6`。取绝对值: 放电电流在不同内核里有正有负。
    /// 缺电压或缺电流一律 `None` —— 少一半就算不出功率, 不拿另一半凑。
    pub fn watt(&self) -> Option<f64> {
        let (ma, uv) = (self.current_ma?, self.voltage_uv?);
        if ma == 0.0 || uv == 0.0 {
            return None;
        }
        Some((ma.abs() / 1000.0) * (uv.abs() / 1.0e6))
    }

    /// 逐帧间隔换算成毫秒 (纳秒 → 毫秒)。
    pub fn frame_intervals_ms(&self) -> Vec<f64> {
        self.frame_intervals_ns.iter().map(|ns| ns / 1.0e6).collect()
    }
}

/// 整机功率的可信区间 (瓦)。超出即判为「此刻量不了」而不是一个数。
///
/// 下界 0.05 W: 手机再省电也不会只吃 50 mW, 读到更小说明这条轨没通。
/// 上界 30 W: 顶配快充也就这个量级; 2026-09-18 实测插着 USB 时读到过
/// 电流 −724805 mA / 功率 −3053229 mW (折合三千瓦), 那是充电态下的垃圾值,
/// 必须被挡在契约外。
pub const WATT_PLAUSIBLE: (f64, f64) = (0.05, 30.0);

/// 一组采样的功率是否可信。可信才返回均值, 否则给出人能读的原因。
///
/// **逐条判**, 不是判均值: 29 条 5 W 里混进一条 300 W 的充电毛刺, 均值 15.2 W
/// 正好落在窗口内, 就会把一个假数字当实测发出去。
/// 只要有一条越界就整组作废 —— 越界意味着这段窗口里供电状态变过,
/// 拿它做 A/B 对比本来就不成立。
pub fn power_watt_of(samples: &[SpSample]) -> Result<f64, String> {
    let w: Vec<f64> = samples.iter().filter_map(|s| s.watt()).collect();
    if w.is_empty() {
        return Err("SP_daemon 没给出 currentNow/voltageNow (未开 -p, 或该机型不上报电流)".into());
    }
    let (lo, hi) = WATT_PLAUSIBLE;
    let bad: Vec<f64> = w.iter().copied().filter(|v| !(lo..=hi).contains(v)).collect();
    if !bad.is_empty() {
        return Err(format!(
            "{} / {} 条功率读数不在可信区间 {lo}..{hi} W (越界样本如 {:.3} W): \
             设备多半正插着 USB 充电 (充电态下 Xpower/SP_daemon 的电流是垃圾值), \
             需切 Wi-Fi 连接并拔掉充电再测",
            bad.len(),
            w.len(),
            bad[0]
        ));
    }
    Ok(w.iter().sum::<f64>() / w.len() as f64)
}

// ══════════════ 解析器 (全部纯函数) ══════════════

/// 把一格数值解析成 f64; 空格/空串/`NA`/`-` 都算「没有」。
fn num(v: &str) -> Option<f64> {
    let v = v.trim();
    if v.is_empty() || v.eq_ignore_ascii_case("na") || v == "-" {
        return None;
    }
    v.parse::<f64>().ok()
}

/// 帧间隔串 → 纳秒数组。
///
/// 分隔符实测有 `;;` (HiSmartPerf 实时流) 与 `;` 两种, 按单个 `;` 切再滤空即可两种通吃。
fn split_jitters(v: &str) -> Vec<f64> {
    v.split(';').filter_map(num).collect()
}

/// 把一个 `SP_daemon` 字段名 + 值塞进采样。识别不了的原样进 `extras`。
fn put_field(s: &mut SpSample, key: &str, val: &str) {
    let k = key.trim().trim_matches('"');
    match k {
        "timeStamp" | "timestamp" => s.timestamp_ms = num(val).map(|v| v as i64),
        "fps" => s.fps = num(val),
        "refreshrate" | "refreshRate" => s.refresh_rate = num(val),
        "fpsJitters" => s.frame_intervals_ns = split_jitters(val),
        "currentNow" => s.current_ma = num(val),
        "voltageNow" => s.voltage_uv = num(val),
        "soc_thermal" | "soc-thermal" => s.soc_temp_c = num(val),
        "gpu" => s.gpu_temp_c = num(val),
        "Battery" => s.battery_temp_c = num(val),
        "shell_frame" => s.shell_frame_temp_c = num(val),
        _ => {
            let v = val.trim();
            if !v.is_empty() {
                s.extras.insert(k.to_string(), v.to_string());
            }
        }
    }
}

/// 这条采样是不是「一格都没解析出来」。
///
/// 重复表头、分隔符错位的垃圾行都会落成这个形状; 收进结果就会虚报 `sample_count`,
/// 让一份没有数据的文件看起来采到了东西。
/// `order:<n>` 只是记录分隔符, 不算数据 —— 只有它的记录同样是空的。
fn is_blank_sample(s: &SpSample) -> bool {
    let mut probe = s.clone();
    probe.extras.remove("order");
    probe == SpSample::default()
}

/// 解析 `SP_daemon` 落在 `/data/local/tmp/data.csv` 的采集结果。
///
/// **表头驱动**: 先读首行拿列名 → 列序号, 再逐行按列名派发。设备端的列数随
/// `-c/-g/-f/...` 开关和 CPU 核数变化, 写死列序号必然在换一台机器时错位。
pub fn parse_sp_csv(text: &str) -> Vec<SpSample> {
    let mut lines = text.lines().filter(|l| !l.trim().is_empty());
    let Some(header) = lines.next() else {
        return Vec::new();
    };
    let cell = |c: &str| c.trim().trim_matches('"').to_string();
    let cols: Vec<String> = header.split(',').map(cell).collect();
    let mut out = Vec::new();
    for line in lines {
        let cells: Vec<&str> = line.split(',').collect();
        if cells.iter().all(|c| c.trim().is_empty()) {
            continue;
        }
        // 重复表头 (设备端分段落盘时会再写一次) 按内容认, 不靠猜首列叫什么
        if cells.iter().map(|c| cell(c)).eq(cols.iter().cloned()) {
            continue;
        }
        let mut s = SpSample::default();
        for (i, c) in cells.iter().enumerate() {
            let Some(name) = cols.get(i) else { break };
            if name.is_empty() {
                continue;
            }
            put_field(&mut s, name, c);
        }
        if is_blank_sample(&s) {
            continue;
        }
        out.push(s);
    }
    out
}

/// 解析 `SP_daemon` 打到 stdout 的实时数据。
///
/// 口径: `order:<n>` 开一条新记录, 其余 `key=value` 词元 (行首/行内皆可) 归入当前记录。
/// 这个宽松口径是刻意的 —— HiSmartPerf 自己读 `SP_daemon -N 1 -r` 也是按空白切词、
/// 认带 `=` 的那个词元, 不依赖固定的行布局。
pub fn parse_sp_stdout(text: &str) -> Vec<SpSample> {
    let mut out: Vec<SpSample> = Vec::new();
    let mut cur: Option<SpSample> = None;
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        for tok in line.split_whitespace() {
            if let Some(rest) = tok.strip_prefix("order:") {
                if let Some(prev) = cur.take() {
                    out.push(prev);
                }
                let mut s = SpSample::default();
                if !rest.is_empty() {
                    s.extras.insert("order".into(), rest.to_string());
                }
                cur = Some(s);
                continue;
            }
            let Some((k, v)) = tok.split_once('=') else { continue };
            if k.is_empty() {
                continue;
            }
            put_field(cur.get_or_insert_with(SpSample::default), k, v);
        }
    }
    if let Some(last) = cur {
        out.push(last);
    }
    out.retain(|s| !is_blank_sample(s));
    out
}

/// 解析 `SP_daemon -deviceinfo` / `SP_daemon --version` 一类的键值输出。
///
/// 设备端不同版本用 `k=v` 还是 `k: v` 不一, 两种都认; 认不出的行整行丢弃。
pub fn parse_kv_block(text: &str) -> BTreeMap<String, String> {
    let mut m = BTreeMap::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let pair = line
            .split_once('=')
            .or_else(|| line.split_once(':'))
            .map(|(k, v)| (k.trim(), v.trim()));
        if let Some((k, v)) = pair {
            if !k.is_empty() && !v.is_empty() {
                m.insert(k.trim_matches('"').to_string(), v.to_string());
            }
        }
    }
    m
}

/// HiSmartPerf 游戏报告里的 `general.csv` / `general_upload.csv`: 每行 `键,值`。
///
/// 这份文件是宿主工具落盘的**本次采集元数据** (起始时间、时长、目标帧率、CPU 簇),
/// 不是逐秒数据。拿它来对账「这轮到底测了多久、目标帧率是多少」。
pub fn parse_report_general(text: &str) -> BTreeMap<String, String> {
    let mut m = BTreeMap::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Some((k, v)) = line.split_once(',') {
            let (k, v) = (k.trim(), v.trim());
            if !k.is_empty() {
                m.insert(k.to_string(), v.to_string());
            }
        }
    }
    m
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 2026-09-18 本机用 HiSmartPerf 实测原神 119 秒, 取其中三个采集点的真实读数,
    /// 按 `SP_daemon` 文档的 CSV 列名排布并脱敏 (去掉机型/包名/序列号):
    /// 帧间隔 33.3 ms (锁 30 帧, 两分钟几乎没有一帧偏离)、GPU 频率上报 0 (该机型
    /// 这套组合采不到)、电流 −724805 mA / 电压 4.212 V 是插着 USB 充电时的垃圾值。
    const SP_CSV: &str = include_str!("testdata/sp_daemon_data.csv");
    /// 同一轮实测落盘的 `general.csv` 本体 (删去机型相关的 CPU 簇频率行)。
    const GENERAL: &str = include_str!("testdata/hismartperf_general.csv");

    /// **构造样本, 非真机抓取**: `SP_daemon` 的 stdout 逐行布局本机拿不到 (无鸿蒙真机、
    /// 未装 hdc)。数值取自上面那轮实测, 布局按 HiSmartPerf 读 stdout 的方式 (按空白切词、
    /// 认带 `=` 的词元) 构造。真机验证前, stdout 通路不作为默认口径, 默认读 data.csv。
    const SP_STDOUT_SYNTHETIC: &str = "\
# 构造样本: 布局未上真机验证
order:0
timestamp=1789738596000 fps=29.8 refreshrate=60
fpsJitters=33300000;;33300000
memTotal=15600608
order:1
timestamp=1789738597000 fps=29.9 refreshrate=60
";

    /// **构造样本, 非真机抓取**: `SP_daemon -deviceinfo` 的字段名与布局未经真机确认,
    /// 这里只用来钉住通用 `k=v` / `k: v` 解析行为本身。
    const KV_SYNTHETIC: &str = "\
Version=1.0.3
cpuCoreNum: 8

=only_value_no_key
key_without_value=
";

    #[test]
    fn collect_cmd_follows_the_official_flag_table() {
        assert_eq!(
            collect_cmd(10, Some("com.example.demo"), SpFlags::default()),
            "SP_daemon -N 10 -PKG com.example.demo -c -g -f -t -p"
        );
        // 不给包名就是整机口径, 不留空的 -PKG
        assert_eq!(
            collect_cmd(1, None, SpFlags { cpu: false, gpu: false, fps: true, temp: false, power: true, ram: false }),
            "SP_daemon -N 1 -f -p"
        );
        // -N 是必选项, 0 次没有意义, 兜到 1
        assert!(collect_cmd(0, None, SpFlags::default()).starts_with("SP_daemon -N 1 "));
        // 空包名不能拼出一个孤零零的 -PKG
        assert!(!collect_cmd(3, Some("   "), SpFlags::default()).contains("-PKG"));
    }

    #[test]
    fn xpower_commands_match_hismartperf() {
        let c = xpower_flush_cmds();
        assert_eq!(c[0], "hidumper -s 1213 -a -b");
        assert_eq!(c[1], "hidumper -s 1213 -a -f");
        assert_eq!(c[2], "hidumper -s 1213 -a --dumpDb");
        assert_eq!(xpower_rm_cmd(), "rm -rf /data/log/xpower/dump/dubai.db");
        assert_eq!(XPOWER_DB_REMOTE, "/data/log/xpower/dump/dubai.db");
        assert_eq!(SP_CSV_REMOTE, "/data/local/tmp/data.csv");
    }

    #[test]
    fn parses_the_real_genshin_csv_rows() {
        let s = parse_sp_csv(SP_CSV);
        assert_eq!(s.len(), 3);
        assert_eq!(s[0].fps, Some(29.8));
        assert_eq!(s[0].refresh_rate, Some(60.0));
        // 帧间隔 33.3 ms —— 锁 30 帧锁得极稳, 两分钟几乎没有一帧偏离
        let ms = s[0].frame_intervals_ms();
        assert_eq!(ms.len(), 5);
        for v in &ms {
            assert!((v - 33.3).abs() < 1e-9, "帧间隔应为 33.3 ms, 实为 {v}");
        }
        assert_eq!(s[0].gpu_temp_c, Some(45.7));
        assert_eq!(s[0].battery_temp_c, Some(40.7));
        assert_eq!(s[0].timestamp_ms, Some(1789738596000));
        // 认不出的列原样留着, 不丢也不臆造单位
        assert_eq!(s[0].extras.get("gpuFrequency").map(String::as_str), Some("0"));
        assert_eq!(s[0].extras.get("ddrFrequency").map(String::as_str), Some("2627000"));
    }

    #[test]
    fn csv_parsing_is_header_driven_not_position_driven() {
        // 列顺序换过、还多了一列没见过的, 解析结果必须不变
        let reordered = "Battery,fps,whatever_new,fpsJitters\n40.7,29.8,xyz,33300000;;33300000\n";
        let s = parse_sp_csv(reordered);
        assert_eq!(s.len(), 1);
        assert_eq!(s[0].fps, Some(29.8));
        assert_eq!(s[0].battery_temp_c, Some(40.7));
        assert_eq!(s[0].frame_intervals_ms().len(), 2);
        assert_eq!(s[0].extras.get("whatever_new").map(String::as_str), Some("xyz"));
    }

    #[test]
    fn charging_garbage_power_is_rejected_not_averaged_into_a_number() {
        let s = parse_sp_csv(SP_CSV);
        // 单条采样确实能算出一个数 —— 但那是三千瓦级的垃圾值
        let w = s[0].watt().expect("有电流有电压就能算出一个数");
        assert!(w > 1000.0, "充电态读数折合 {w} W");
        // 契约层必须把它挡住, 而不是把 3053 W 写进报告
        let err = power_watt_of(&s).unwrap_err();
        assert!(err.contains("充电"), "原因要说人话: {err}");
    }

    #[test]
    fn a_plausible_discharging_reading_passes_through() {
        // 放电态: 电流 −1200 mA (负号表放电), 电压 4.212 V → 5.05 W
        let mut s = SpSample::default();
        s.current_ma = Some(-1200.0);
        s.voltage_uv = Some(4_212_000.0);
        let w = power_watt_of(std::slice::from_ref(&s)).unwrap();
        assert!((w - 5.0544).abs() < 1e-4, "实得 {w}");
    }

    #[test]
    fn one_charging_spike_invalidates_the_whole_window() {
        // 29 条 5 W + 1 条 300 W, 均值 14.8 W 正好落在窗口内 —— 判均值就会放它过去
        let good = SpSample { current_ma: Some(-1187.0), voltage_uv: Some(4_212_000.0), ..Default::default() };
        let spike = SpSample { current_ma: Some(-71_225.0), voltage_uv: Some(4_212_000.0), ..Default::default() };
        let mut v: Vec<SpSample> = vec![good; 29];
        v.push(spike);
        let mean: f64 = v.iter().filter_map(|s| s.watt()).sum::<f64>() / v.len() as f64;
        assert!((WATT_PLAUSIBLE.0..=WATT_PLAUSIBLE.1).contains(&mean), "均值 {mean} 确实混得过去");
        let err = power_watt_of(&v).unwrap_err();
        assert!(err.contains("1 / 30"), "要说清是几条越界: {err}");
    }

    #[test]
    fn a_repeated_header_does_not_become_a_phantom_sample() {
        let doubled = format!("{SP_CSV}{}", SP_CSV.lines().next().unwrap());
        assert_eq!(parse_sp_csv(&doubled).len(), 3, "重复表头不算一个采样点");
        // 一格都没解析出来的垃圾行同样不该计数
        assert!(parse_sp_csv("fps,refreshrate\n,\n").is_empty());
        assert!(parse_sp_stdout("order:0\norder:1\n").is_empty(), "只有分隔符没有数据 = 没采到");
    }

    #[test]
    fn missing_current_or_voltage_yields_none_never_zero() {
        let mut only_v = SpSample::default();
        only_v.voltage_uv = Some(4_212_000.0);
        assert_eq!(only_v.watt(), None);

        let mut only_i = SpSample::default();
        only_i.current_ma = Some(-1200.0);
        assert_eq!(only_i.watt(), None);

        // 一整组都没有 → 报「没给出」, 不报 0 W
        let err = power_watt_of(&[SpSample::default()]).unwrap_err();
        assert!(err.contains("没给出"), "{err}");
        assert!(power_watt_of(&[]).is_err());
    }

    #[test]
    fn parses_sp_daemon_stdout_records() {
        let s = parse_sp_stdout(SP_STDOUT_SYNTHETIC);
        assert_eq!(s.len(), 2);
        assert_eq!(s[0].extras.get("order").map(String::as_str), Some("0"));
        assert_eq!(s[0].fps, Some(29.8));
        assert_eq!(s[0].timestamp_ms, Some(1789738596000));
        assert_eq!(s[1].fps, Some(29.9));
        // memTotal 这类没进契约的字段照样留得住
        assert_eq!(s[0].extras.get("memTotal").map(String::as_str), Some("15600608"));
    }

    #[test]
    fn stdout_parser_tolerates_key_value_on_the_same_line_as_order() {
        // HiSmartPerf 自己读 SP_daemon -N 1 -r 就是按空白切词、认带 = 的词元
        let s = parse_sp_stdout("order:0 memTotal=11562492 memFree=2201044\n");
        assert_eq!(s.len(), 1);
        assert_eq!(s[0].extras.get("memTotal").map(String::as_str), Some("11562492"));
        assert_eq!(s[0].extras.get("memFree").map(String::as_str), Some("2201044"));
    }

    #[test]
    fn parses_key_value_blocks_in_both_shapes() {
        let m = parse_kv_block(KV_SYNTHETIC);
        assert_eq!(m.get("Version").map(String::as_str), Some("1.0.3"));
        assert_eq!(m.get("cpuCoreNum").map(String::as_str), Some("8"));
        // 缺键或缺值的行整行丢弃, 不造出空键空值
        assert!(!m.contains_key(""));
        assert!(!m.contains_key("key_without_value"));
        assert_eq!(m.len(), 2);
    }

    #[test]
    fn parses_the_real_report_general_csv() {
        let m = parse_report_general(GENERAL);
        assert_eq!(m.get("testDuration").map(String::as_str), Some("119"));
        assert_eq!(m.get("target_fps").map(String::as_str), Some("60"));
        assert_eq!(m.get("report_version").map(String::as_str), Some("v1.6"));
        // GPU 频率上下限都是 0 —— 该机型这套组合根本采不到 GPU 频率
        assert_eq!(m.get("gpu_max_freq").map(String::as_str), Some("0"));
        assert_eq!(m.get("gpu_min_freq").map(String::as_str), Some("0"));
    }

    #[test]
    fn empty_and_junk_input_parses_to_nothing_rather_than_panicking() {
        assert!(parse_sp_csv("").is_empty());
        assert!(parse_sp_csv("fps,refreshrate\n").is_empty());
        assert!(parse_sp_stdout("").is_empty());
        assert!(parse_sp_stdout("no useful tokens here").is_empty());
        assert!(parse_kv_block("").is_empty());
        assert!(parse_report_general("").is_empty());
    }

    #[test]
    fn parsing_is_deterministic() {
        assert_eq!(parse_sp_csv(SP_CSV), parse_sp_csv(SP_CSV));
        assert_eq!(parse_sp_stdout(SP_STDOUT_SYNTHETIC), parse_sp_stdout(SP_STDOUT_SYNTHETIC));
    }
}
