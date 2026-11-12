//! gpdaemon: 安卓侧的 HiSmartPerf 通路 —— 设备端 `GamePerfToolCollector` 的推送、握手、
//! 命令构造与实时样本解析。
//!
//! **安卓侧的 HiSmartPerf 不是 `SP_daemon`。** 鸿蒙侧是 `hdc shell SP_daemon -N …` 跑完落盘
//! `data.csv` 再拉回来 (见 `smartperf`); 安卓侧完全不同 —— HiSmartPerf-Editor 往手机
//! `/data/local/tmp` 推一个 native 采集器 `GamePerfToolCollector`, 以 `-authorize` 常驻,
//! 它在设备上监听 TCP, 主机 `adb forward` 过去后用一套文本协议实时拉样本。
//! 两条通路除了「都叫 HiSmartPerf」以外没有任何共用代码, 故各占一个模块。
//!
//! **口径的来源** (全部逐条取自本机 HiSmartPerf-Editor 5.1.0.47, 没有一条是凭空构造的):
//!
//! | 东西 | 出处 |
//! |---|---|
//! | `adb -s <S> shell "pidof GamePerfToolCollector"` → `kill -9` | `app.asar` `dist/electron.js` `startCollector` |
//! | `adb -s <S> shell "LD_LIBRARY_PATH=/data/local/tmp/ /data/local/tmp/GamePerfToolCollector -version"` | 同上 (版本比对, 不一致才重推) |
//! | `adb -s <S> shell getprop ro.product.cpu.abi` → 选 `plugins/Device/Android/<abi>/` | 同上 |
//! | `adb -s <S> push <abi>/{GamePerfToolCollector,libQProfilerInterface.so} /data/local/tmp/` | 同上 |
//! | `adb -s <S> shell chmod 777 /data/local/tmp/GamePerfToolCollector` | 同上 |
//! | `adb -s <S> shell "LD_LIBRARY_PATH=/data/local/tmp/ /data/local/tmp/GamePerfToolCollector -authorize"` | 同上 (常驻, 不返回) |
//! | `adb -s <S> forward tcp:<本地> tcp:<20100..20102>` | `dist/umi.js` `DevSocket` 探测 |
//! | 握手 `0012\|authorize:0;` + `cmd=getVersion;end;` | 同上 |
//! | `cmd=getDeviceInfo;end;` / `cmd=selectPid;para=<pid>;end;` | 同上 |
//! | `cmd=startCollect;para=itemMask:<掩码>,package:<包名>,pid:<pid>,realtimeEnable:1,;end;` | 同上 |
//! | `cmd=stopCollect;para=pcTmp;end;` | 同上 |
//! | 样本 `{fps:%d;refresh:%d;gpuFreq:%d;…}` 的字段名与顺序 | `GamePerfToolCollector` 二进制里的格式串 |
//!
//! **设备端到底读的是什么** (`GamePerfToolCollector` 二进制里的路径字面量):
//! 功耗 `/sys/class/power_supply/{battery,Battery}/{current_now,voltage_now}`,
//! 温度 `/sys/class/thermal` 与 `/sys/devices/virtual/thermal`,
//! GPU `/sys/class/kgsl/kgsl-3d0/{gpubusy,gpuclk,devfreq/cur_freq}` (高通) 或
//! `/sys/class/devfreq/gpufreq/*` (ARM/Maleoon), 帧率 `dumpsys SurfaceFlinger --latency "<图层>"`。
//! **这与 `hwcond` 读的是同一批节点** —— 所以安卓两条通路的功耗/温度本该对得上,
//! 对不上就是哪边算错了, 这正是可以交叉验证的地方。
//!
//! **两条通道, 两种线格式** (2026-09-23 在红魔 NX809J 上实测钉死, 不是照文档抄的):
//!
//!   - **控制通道** (设备端 20100..20102): 请求 `cmd=…;end;`, 应答按 `;end;` 分帧 ——
//!     `value=v1.267;end;` / `ret=0;end;` / `ret=0;info=version:v1.0;gpuType:0;end;`。
//!     **握手的第一条应答是裸的 `1.26\0`** (回 `0012|authorize:0;` 的协议版本),
//!     不带 `value=` 也不带 `;end;`; 紧跟着第二条才是 `value=<采集器版本>;end;`。
//!     HiSmartPerf 自己的日志里这两条也是分两次 receive 收的 —— 只读一次就判协议对不对,
//!     会把正常握手判成「应答不是这个协议」。
//!   - **实时数据通道** (设备端 20103..20105, 另开一条连接, 同样要先握手):
//!     设备端每秒推一条**裸** `{fps:30;refresh:0;…;batTemp:44000;}`,
//!     **既没有 `value=` 前缀也没有 `;end;` 帧尾**。`startCollect` 发在控制通道上,
//!     数据却只从这条通道出来 —— 只连控制通道的话, `startCollect` 回 `ret=0` 一切正常,
//!     然后你一条样本都收不到。
//!
//! **单位口径** (同一时刻与 `hwcond` 直读 sysfs 比对出来的, 见 `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_PERF_SOURCE.md` 第 7 节):
//!
//! | 字段 | 实测 | 结论 |
//! |---|---|---|
//! | `voltage` | 采集器 `4207000` / sysfs `voltage_now` `4207000` | **逐字透传**, μV |
//! | `current` | 采集器 `-87000` / sysfs `current_now` `-66000` (同段窗口, 充电态在抖) | **逐字透传**同一个节点的原值 |
//! | `batTemp` | 采集器 `44000` / `thermal_zone` `battery` `44000` | **毫摄氏度**; 注意**不是** `power_supply/battery/temp` (那个是 `440`, 0.1 °C) |
//! | `gpuTemp` | 采集器 `49200..51200` / `thermal_zone` `gpuss-*` `52300..54300` | **毫摄氏度** |
//!
//! 即电流/电压是把 `/sys/class/power_supply/battery/{current_now,voltage_now}` 原值透传,
//! 与 `hwcond::PowerSample` 读的是同一个节点的同一个量 —— 故本模块直接构造
//! `hwcond::PowerSample` 复用它的 `watt()`, **两条通路连算瓦数的算术都是同一份代码**,
//! 免得「同一个节点被两边按不同单位折算」这种错悄悄溜进对比表。
//!
//! **这条通路拿不到逐帧间隔**: 实时样本里只有每秒一个整数 `fps`, 没有鸿蒙侧的
//! `fpsJitters` (逐帧绘制间隔)。故本模块给得出帧率与「每秒帧时」, 给不出**逐帧** p95 ——
//! 那个只能走 ftrace/Vulkan 时间戳。不拿每秒口径的 p95 冒充逐帧 p95, 见 `perfsrc`。

use std::collections::BTreeMap;

// ══════════════ 设备侧路径与常量 ══════════════

/// 采集器在设备上的落脚点 (HiSmartPerf 写死的目录)。
pub const REMOTE_DIR: &str = "/data/local/tmp";
/// 设备端采集器可执行文件名。
pub const COLLECTOR_BIN: &str = "GamePerfToolCollector";
/// 采集器依赖的 GPU 计数器库, 必须和它一起推。
pub const COLLECTOR_LIB: &str = "libQProfilerInterface.so";

/// **控制**通道的设备端端口候选 (发命令、收回执)。
pub const PORT_FIRST: u16 = 20100;
pub const PORT_LAST: u16 = 20102;
/// **实时数据**通道的设备端端口候选。HiSmartPerf 把这两组分开探
/// (`dist/umi.js`: `[new oe(20100,20102), new oe(20103,20105)]`), 因为它们是两条独立连接。
/// 实测这台机器上采集器一次开五个监听口 (20100/20103/20106/20109/20112),
/// 命令走 20100 而样本只从 20103 出来。
pub const DATA_PORT_FIRST: u16 = 20103;
pub const DATA_PORT_LAST: u16 = 20105;

/// 握手第一帧。`0012` 是长度前缀, `authorize:0` 是免鉴权档位 —— 原样照发,
/// 这四个字符不是我们能自己算的, 是 HiSmartPerf 写死的。
pub const HANDSHAKE: &str = "0012|authorize:0;";

/// 协议的帧尾。样本正文里本身带 `;`, 所以**只能按 `;end;` 切帧**, 不能按 `;` 切。
pub const FRAME_END: &str = ";end;";

// ══════════════ 采集项掩码 ══════════════

/// `startCollect` 的 `itemMask` 位。取自 `dist/umi.js` 里逐位相加的那段,
/// 位序不可自创 —— 设备端按位判开关。
///
/// 整张表都留着 (哪怕本模块只用其中四位): 少一位就没法判断「这个掩码里有没有混进
/// 会落盘的采集项」, 而 `DEFAULT_MASK` 的单测正是靠 `CPU_TRACE` / `ENGINE` 这些位来钉的。
#[allow(dead_code)]
pub mod item {
    pub const FPS: u32 = 1;
    pub const CPU: u32 = 2;
    pub const GPU: u32 = 4;
    pub const MEMORY: u32 = 8;
    pub const TEMP: u32 = 16;
    pub const POWER: u32 = 32;
    pub const NET: u32 = 64;
    pub const CPU_TRACE: u32 = 128;
    pub const CHIP: u32 = 256;
    pub const ENGINE: u32 = 512;
    pub const CPU_FLAME_GRAPH: u32 = 2048;
}

/// 本模块默认采的四类: 帧率、GPU、温度、功耗 —— 正好对齐 `PerfSnapshot` 要填的字段。
///
/// 不开 `CPU_TRACE` / `CPU_FLAME_GRAPH` / `ENGINE`: 前两个会在设备上拉 perfetto/simpleperf
/// 落盘 (有副作用, 且要 python/ndk), 后一个要游戏自己链 Unity profiler —— 都不是只读采集。
pub const DEFAULT_MASK: u32 = item::FPS | item::GPU | item::TEMP | item::POWER;

// ══════════════ 命令构造 (纯函数) ══════════════

/// `pidof` 掉残留采集器 —— 上一轮没退干净的进程会占着端口, 新的起不来。
pub fn cmd_pidof() -> String {
    format!("pidof {COLLECTOR_BIN}")
}

/// 版本探测。HiSmartPerf 用它和内置版本比对, 不一致才重推。
pub fn cmd_version() -> String {
    format!("LD_LIBRARY_PATH={REMOTE_DIR}/ {REMOTE_DIR}/{COLLECTOR_BIN} -version")
}

/// 常驻采集器。**这条命令不会返回** —— 它在前台跑到被 kill 为止, 必须当后台子进程起。
pub fn cmd_authorize() -> String {
    format!("LD_LIBRARY_PATH={REMOTE_DIR}/ {REMOTE_DIR}/{COLLECTOR_BIN} -authorize")
}

/// 推完要给执行位。HiSmartPerf 用的就是 777。
pub fn cmd_chmod() -> String {
    format!("chmod 777 {REMOTE_DIR}/{COLLECTOR_BIN}")
}

pub fn cmd_get_version() -> String {
    "cmd=getVersion;end;".into()
}

pub fn cmd_get_device_info() -> String {
    "cmd=getDeviceInfo;end;".into()
}

pub fn cmd_select_pid(pid: i64) -> String {
    format!("cmd=selectPid;para={pid};end;")
}

/// `startCollect`。参数顺序与结尾那个多余的逗号都照 HiSmartPerf 原样 ——
/// 设备端是按 `,` 切词元再找 `key:value` 的, 少一个逗号就少一个词元。
pub fn cmd_start_collect(mask: u32, pkg: &str, pid: Option<i64>, realtime: bool) -> String {
    let mut s = format!("cmd=startCollect;para=itemMask:{mask},package:{pkg},");
    if let Some(p) = pid {
        if p >= 0 {
            s.push_str(&format!("pid:{p},"));
        }
    }
    if realtime {
        s.push_str("realtimeEnable:1,");
    }
    s.push_str(FRAME_END);
    s
}

/// `stopCollect`。`pcTmp` 是 HiSmartPerf 写死的落盘目录名 (设备端 `/data/local/tmp/GamePerfReport/pcTmp`)。
pub fn cmd_stop_collect() -> String {
    format!("cmd=stopCollect;para=pcTmp{FRAME_END}")
}

// ══════════════ 应答与样本解析 (纯函数) ══════════════

/// 一条实时样本。字段名原样保留设备端的拼写, **不在这里换算单位** ——
/// 换算与可信度判断在 `perfsrc`, 这里只做「字节 → 数字」。
#[derive(Debug, Clone, Default, PartialEq)]
pub struct GpSample {
    pub fields: BTreeMap<String, i64>,
}

impl GpSample {
    pub fn get(&self, k: &str) -> Option<i64> {
        self.fields.get(k).copied()
    }
}

/// 按 `;end;` 把收到的字节流切成完整帧, 返回 (帧列表, 尚未收全的残余)。
///
/// 必须留残余: TCP 不保证一次 read 正好是一帧, 半帧直接丢掉就会稳定漏采样。
pub fn split_frames(buf: &str) -> (Vec<String>, String) {
    let mut frames = Vec::new();
    let mut rest = buf;
    while let Some(i) = rest.find(FRAME_END) {
        frames.push(rest[..i].to_string());
        rest = &rest[i + FRAME_END.len()..];
    }
    (frames, rest.to_string())
}

/// 一帧的载荷: `value=<正文>` → `Some(正文)`; `ret=<码>…` → `None` (那是命令回执, 不是数据)。
pub fn frame_payload(frame: &str) -> Option<&str> {
    // 在帧内**任意位置**找 `value=`, 不是只认开头: 握手的两条应答常常在同一次 read 里到达,
    // 于是第一帧长这样 —— `1.26\0value=v1.267`。只认开头就会把版本号整条漏掉,
    // 然后握手退化成「只拿到鉴权应答」。
    let i = frame.find("value=")?;
    Some(&frame[i + "value=".len()..])
}

/// 实时样本正文 `{k:v;k:v;…}` → 数字表。
///
/// 只收**能解析成整数**的键: 设备端在同一条正文里还塞了 `gpuType:arm` 这种字符串值,
/// 把它当 0 收进来就会凭空多出一个「测量值」。解析不了的键直接丢, 不猜。
pub fn parse_sample(payload: &str) -> Option<GpSample> {
    let body = payload.trim();
    let body = body.strip_prefix('{').unwrap_or(body);
    let body = body.strip_suffix('}').unwrap_or(body);
    let mut s = GpSample::default();
    for tok in body.split(';') {
        let tok = tok.trim();
        if tok.is_empty() {
            continue;
        }
        let Some((k, v)) = tok.split_once(':') else { continue };
        let (k, v) = (k.trim(), v.trim());
        if k.is_empty() {
            continue;
        }
        if let Ok(n) = v.parse::<i64>() {
            s.fields.insert(k.to_string(), n);
        }
    }
    if s.fields.is_empty() {
        None
    } else {
        Some(s)
    }
}

/// **实时数据通道**的切分: 设备端吐的是裸 `{k:v;…}`, 既没有 `value=` 前缀也没有 `;end;` 帧尾
/// (那是控制通道的格式)。所以按花括号配对切, 不能复用 `split_frames`。
///
/// 返回 (完整记录, 残余)。残余只留「已经开了头但还没收到 `}`」的那半条 ——
/// 开头之前的零碎字节直接丢, 否则一段噪声就能让缓冲区无限涨。
pub fn split_records(buf: &str) -> (Vec<String>, String) {
    let mut out = Vec::new();
    let mut rest = buf;
    loop {
        let Some(a) = rest.find('{') else {
            // 连个记录开头都没有: 没什么可留的
            return (out, String::new());
        };
        match rest[a..].find('}') {
            Some(b) => {
                out.push(rest[a..a + b + 1].to_string());
                rest = &rest[a + b + 1..];
            }
            // 开了头没收全: 留着等下一段
            None => return (out, rest[a..].to_string()),
        }
    }
}

/// 实时数据通道的字节流 → 样本列表 + 残余。
pub fn parse_stream(buf: &str) -> (Vec<GpSample>, String) {
    let (records, rest) = split_records(buf);
    (records.iter().filter_map(|r| parse_sample(r)).collect(), rest)
}

/// `getVersion` 的应答里取采集器版本: `value=v1.267;end;` → `v1.267`。
///
/// 握手的第一条应答是裸的 `1.26\0` (协议版本, 不带 `value=` 也不带 `;end;`),
/// 这里**不认它** —— 认了就会把协议版本当成采集器版本记进 `meta`。
pub fn parse_version(buf: &str) -> Option<String> {
    let (frames, _) = split_frames(buf);
    frames.iter().find_map(|f| {
        let p = frame_payload(f)?.trim();
        if p.starts_with('{') || p.is_empty() {
            return None;
        }
        Some(p.trim_end_matches('\0').to_string())
    })
}

/// 握手的第一条裸应答看着像不像协议版本 (`1.26\0`)。
pub fn looks_like_auth_ack(buf: &str) -> bool {
    let t = buf.trim().trim_end_matches('\0').trim();
    !t.is_empty()
        && t.chars().next().is_some_and(|c| c.is_ascii_digit())
        && t.chars().all(|c| c.is_ascii_digit() || c == '.')
}

/// `getDeviceInfo` 的应答 → 键值表。
///
/// 实测线格式是 `ret=0;info=version:v1.0;gpuType:0;end;` —— 载荷挂在 `info=` 后面,
/// **不是** `value=`。值多是字符串, 故按字符串收, 原样进 `meta`, 不解释不换算。
pub fn parse_device_info(frame: &str) -> BTreeMap<String, String> {
    let mut m = BTreeMap::new();
    // `ret=<码>;info=<载荷>` —— 取 info= 之后的全部
    let Some(i) = frame.find("info=") else { return m };
    let body = &frame[i + "info=".len()..];
    let body = body.trim();
    let body = body.strip_prefix('{').unwrap_or(body);
    let body = body.strip_suffix('}').unwrap_or(body);
    for tok in body.split(';') {
        let tok = tok.trim();
        if tok.is_empty() {
            continue;
        }
        if let Some((k, v)) = tok.split_once(':') {
            let (k, v) = (k.trim(), v.trim());
            if !k.is_empty() && !v.is_empty() {
                m.insert(k.to_string(), v.to_string());
            }
        }
    }
    m
}

// ══════════════ 单位口径 ══════════════

/// 一条样本 → 一条 `hwcond::PowerSample`。
///
/// 采集器把 `/sys/class/power_supply/battery/{current_now,voltage_now}` 的原值**逐字透传**
/// (2026-09-23 实测: 采集器 `voltage:4207000` 与 sysfs `voltage_now` 逐字一致),
/// 也就是与 `hwcond` 读的是同一个节点的同一个量。故这里直接构造 `PowerSample`,
/// **复用它的 `watt()`** —— 两条通路连算瓦数的算术都是同一份代码, 对比表里若还有差,
/// 那就只可能来自采样时刻, 不可能来自「两边按不同单位折算了同一个节点」。
///
/// 采不到任一半边就是 `None`, **不拿 0 当功率** —— 一个 0.000 W 看着像数字,
/// 其实是「这条轨此刻量不了」。
pub fn sample_power(s: &GpSample) -> Option<crate::hwcond::PowerSample> {
    let (c, v) = (s.get("current")?, s.get("voltage")?);
    if c == 0 || v == 0 {
        return None;
    }
    Some(crate::hwcond::PowerSample { volt_uv: v, curr_ua: c, power_uw: None })
}

/// 一条样本 → 瓦特。
pub fn sample_watt(s: &GpSample) -> Option<f64> {
    sample_power(s).and_then(|p| p.watt())
}

/// 温度字段名 → 统一契约里的热区。对应关系取自 `dist/umi.js` 的安卓↔鸿蒙映射表。
pub const TEMP_SOC: &str = "soc";
pub const TEMP_GPU: &str = "gpuTemp";
pub const TEMP_BATTERY: &str = "batTemp";

/// 一个温度读数是不是「测量结果」, 是的话折成摄氏度。
///
/// 单位是**毫摄氏度** —— 2026-09-23 实测: 采集器 `batTemp:44000` 与
/// `/sys/class/thermal` 里名为 `battery` 的热区读数 `44000` 逐字一致。
/// (注意不是 `/sys/class/power_supply/battery/temp`, 那个是 `440`, 0.1 °C 制;
/// 按它折算会把 44 °C 算成 4400 °C。)
///
/// `0` 不是 0 摄氏度, 是「这台机器没有这个传感器」—— 这台红魔的
/// `soc` / `cpuTemp` / `shellFrame` 全是 0, 因为它根本没有采集器要找的那几个热区名。
/// 设备端对缺失的热区照样回 0, 当成摄氏度发出去比不给更糟。
pub fn temp_c(s: &GpSample, key: &str) -> Option<f64> {
    match s.get(key) {
        Some(0) | None => None,
        Some(v) => Some(v as f64 / 1000.0),
    }
}

// ══════════════ 设备侧会话 ══════════════

use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::{Duration, Instant};

use crate::device::Device;

/// 本机转发端口的起点。HiSmartPerf 自己用 12348/12349, 这里避开它 ——
/// 万一 HiSmartPerf-Editor 正开着, 抢同一个端口两边都采不成。
pub const LOCAL_PORT_FIRST: u16 = 12448;
/// 最多往后试几个本机端口 (上一轮没撤干净的转发会占住端口)。
pub const LOCAL_PORT_TRIES: u16 = 8;

/// 采集器起来到能应答握手之间的等待上限。
pub const DAEMON_READY_MS: u64 = 12_000;

/// 设了这个环境变量就把实时数据通道的**原始字节**追加落盘。
///
/// 不是调试残留: 两条通路的读数对不上时, 唯一能把「口径差异」与「窗口边缘效应」
/// 分开的证据就是逐秒原始序列 —— 设备端每秒吐一个**整数** fps, 窗口首尾那两个
/// 不完整的秒照样各算一条样本, 会把均值系统性地往下拉。
pub const RAW_DUMP_ENV: &str = "PHONEFARM_GPD_RAW";

/// 等一条控制命令回执的上限。
pub const CMD_ACK_MS: u64 = 2_000;

/// 杀残留采集器最多重试几轮。
pub const KILL_WAIT_ROUNDS: u32 = 5;
/// 每轮之间给内核回收监听 socket 的宽限。
pub const KILL_SETTLE_MS: u64 = 600;

/// 握手时单次 read 的超时。
pub const HANDSHAKE_READ_MS: u64 = 1_500;
/// 握手整体的超时。设备端分两次应答 (裸协议版本 + 带帧尾的采集器版本),
/// 要读到第二条为止, 所以这个值必须大于单次 read 超时。
pub const HANDSHAKE_TOTAL_MS: u64 = 3_000;

/// 设备端采集器所在的 abi 目录 → 本机 HiSmartPerf-Editor 里的插件路径。
pub const PLUGIN_DIR: &str =
    "/Applications/HiSmartPerf-Editor.app/Contents/Resources/plugins/Device/Android";

/// 一次采集会话: 推采集器 → 常驻 → 转发 → 握手 → 拉样本 → 收摊。
///
/// **收摊是 `Drop` 里做的**, 不是「记得调 close」: 中途任何一步出错都必须撤掉端口转发、
/// 杀掉设备上的常驻进程。漏一次, 下一轮就会撞上一个占着端口的僵尸采集器,
/// 而症状是「握手超时」, 跟真正的故障长得一模一样。
pub struct Session<'a> {
    phone: &'a Device,
    /// 控制通道: 发 `cmd=…;end;`, 收 `value=…` / `ret=…` 回执
    ctl: TcpStream,
    /// 实时数据通道: 只收裸 `{…}` 样本。`startCollect` 发在控制通道上,
    /// 样本却只从这条出来 —— 少连这一条, 命令全部 `ret=0` 但一条样本都收不到。
    data: TcpStream,
    ctl_port: u16,
    data_port: u16,
    daemon: Option<std::process::Child>,
    /// 设备端采集器版本 (`getVersion` 的应答), 进 `meta`
    pub version: Option<String>,
    /// 数据通道未消费完的半条记录
    buf: String,
}

/// 设备上有没有这台机器 abi 对应的采集器文件, 没有就从本机 HiSmartPerf-Editor 推一份。
///
/// 与 HiSmartPerf 同一口径: 先比版本, 一致就不动设备; 不一致 (或根本没有) 才推。
/// 不无条件重推 —— 一次 push 是几百 KB 的写盘, 每轮采集都来一遍纯属糟蹋设备。
pub fn ensure_collector(phone: &Device, plugin_dir: &str) -> Result<String, String> {
    let have = phone.shell(&cmd_version(), 10_000).trim().to_string();
    if have.starts_with('v') {
        return Ok(have);
    }
    let abi = phone.shell("getprop ro.product.cpu.abi", 8_000).trim().to_string();
    if abi.is_empty() {
        return Err("读不到 ro.product.cpu.abi, 判不出该推哪个 abi 的采集器".into());
    }
    let dir = std::path::Path::new(plugin_dir).join(&abi);
    let bin = dir.join(COLLECTOR_BIN);
    let lib = dir.join(COLLECTOR_LIB);
    if !bin.exists() || !lib.exists() {
        return Err(format!(
            "本机 HiSmartPerf-Editor 里没有 {abi} 的采集器 ({} / {}); \
安卓侧 HiSmartPerf 通路要靠它推到设备上, 装一个 HiSmartPerf-Editor 再来",
            bin.display(),
            lib.display()
        ));
    }
    if !phone.push_file(&bin.to_string_lossy(), &format!("{REMOTE_DIR}/{COLLECTOR_BIN}")) {
        return Err(format!("推 {COLLECTOR_BIN} 失败"));
    }
    if !phone.push_file(&lib.to_string_lossy(), &format!("{REMOTE_DIR}/{COLLECTOR_LIB}")) {
        return Err(format!("推 {COLLECTOR_LIB} 失败"));
    }
    phone.shell(&cmd_chmod(), 8_000);
    let v = phone.shell(&cmd_version(), 10_000).trim().to_string();
    if v.starts_with('v') {
        Ok(v)
    } else {
        Err(format!("采集器推上去了但跑不起来 (-version 回: {v:?})"))
    }
}

/// 杀掉设备上还活着的采集器, **并等它真的死透**。
///
/// 开新会话前必做, 而且必须等: 采集器是「挑第一组还空着的端口」来监听的
/// (实测一个实例一次开五个, 步长 3: 20100/20103/20106/…)。`kill -9` 发出去之后
/// 旧进程的 socket 还要一会儿才释放 —— 这段时间里新起的实例会往后挪到 20101/20104,
/// 甚至挪出探测范围。症状是「命令全部 ret=0, 一条样本都收不到」,
/// 跟目标应用没在前台长得一模一样, 极难判。
pub fn kill_stale(phone: &Device) {
    for round in 0..KILL_WAIT_ROUNDS {
        let pids = phone.shell(&cmd_pidof(), 8_000);
        let alive: Vec<&str> =
            pids.split_whitespace().filter(|p| p.chars().all(|c| c.is_ascii_digit())).collect();
        if alive.is_empty() {
            // 进程没了还要再宽限一拍, 让内核把监听 socket 收回去
            if round > 0 {
                std::thread::sleep(Duration::from_millis(KILL_SETTLE_MS));
            }
            return;
        }
        for pid in alive {
            phone.shell(&format!("kill -9 {pid}"), 5_000);
        }
        std::thread::sleep(Duration::from_millis(KILL_SETTLE_MS));
    }
}

impl<'a> Session<'a> {
    /// 开一次会话。`plugin_dir` 单独传是为了让单测能指一个假目录, 不写死本机路径。
    pub fn open(phone: &'a Device, plugin_dir: &str) -> Result<Self, String> {
        let pushed = ensure_collector(phone, plugin_dir)?;
        kill_stale(phone);

        // -authorize 不返回, 必须当后台子进程起。
        let daemon = phone
            .stream_shell(&cmd_authorize())
            .map_err(|e| format!("起不了设备端采集器: {e}"))?;

        match Self::connect_both(phone) {
            Ok((ctl, ctl_port, data, data_port, version)) => {
                crate::hwcond::progress(
                    "gpd",
                    &format!(
                        "控制通道 tcp:{ctl_port} · 数据通道 tcp:{data_port} · 采集器 {}",
                        version.as_deref().unwrap_or("?")
                    ),
                );
                Ok(Session {
                    phone,
                    ctl,
                    data,
                    ctl_port,
                    data_port,
                    daemon: Some(daemon),
                    version: version.or(Some(pushed)),
                    buf: String::new(),
                })
            }
            Err(e) => {
                // 连不上也要把刚起的常驻进程收掉, 否则它会一直挂在设备上
                let mut d = daemon;
                let _ = d.kill();
                let _ = d.wait();
                kill_stale(phone);
                Err(e)
            }
        }
    }

    /// 建两条通道。任一条没建起来就整体失败 —— 只有控制通道的会话是个陷阱:
    /// 命令全部回 `ret=0` 看着一切正常, 然后一条样本都收不到。
    #[allow(clippy::type_complexity)]
    fn connect_both(
        phone: &Device,
    ) -> Result<(TcpStream, u16, TcpStream, u16, Option<String>), String> {
        let (ctl, ctl_port, version) =
            Self::connect_channel(phone, LOCAL_PORT_FIRST, PORT_FIRST, PORT_LAST, "控制")?;
        match Self::connect_channel(
            phone,
            LOCAL_PORT_FIRST + LOCAL_PORT_TRIES,
            DATA_PORT_FIRST,
            DATA_PORT_LAST,
            "实时数据",
        ) {
            Ok((data, data_port, _)) => Ok((ctl, ctl_port, data, data_port, version)),
            Err(e) => {
                phone.forward_remove(ctl_port);
                Err(e)
            }
        }
    }

    /// 建一条通道: 挑一个能用的本机端口 → 逐个探设备端端口 → 握手。
    ///
    /// 本机端口只挑一次: `adb forward` 只对**本机**端口是否可用给结论, 对设备端那头
    /// 监听没监听一概不知道 —— 所以「转发建起来了」不代表「采集器在那个端口上」。
    /// 握手失败换个本机端口重来毫无意义, 只是把同一次失败重做 N 遍; 真正要探的是设备端。
    fn connect_channel(
        phone: &Device,
        local_base: u16,
        remote_first: u16,
        remote_last: u16,
        what: &str,
    ) -> Result<(TcpStream, u16, Option<String>), String> {
        let mut local = None;
        let mut last = String::new();
        for off in 0..LOCAL_PORT_TRIES {
            let p = local_base + off;
            match phone.forward(p, remote_first) {
                Ok(()) => {
                    local = Some(p);
                    break;
                }
                Err(e) => last = format!("forward tcp:{p}: {e}"),
            }
        }
        let Some(local) = local else {
            return Err(format!(
                "{what}通道: 本机 {local_base}..{} 这几个端口都建不了转发 (最后一次: {last}); \
多半是上一轮的 adb forward 没撤干净, `adb forward --list` 看一眼",
                local_base + LOCAL_PORT_TRIES - 1
            ));
        };

        // 采集器刚起来需要一点时间才开始监听, 故整体套一层限时重试。
        let t0 = Instant::now();
        let mut last = String::from("还没试过任何设备端端口");
        while t0.elapsed() < Duration::from_millis(DAEMON_READY_MS) {
            for remote in remote_first..=remote_last {
                if let Err(e) = phone.forward(local, remote) {
                    last = format!("forward tcp:{local}→tcp:{remote}: {e}");
                    continue;
                }
                match Self::handshake(local) {
                    Ok((sock, version)) => return Ok((sock, local, version)),
                    Err(e) => last = format!("tcp:{remote} 握手: {e}"),
                }
            }
            std::thread::sleep(Duration::from_millis(400));
        }
        phone.forward_remove(local);
        Err(format!(
            "{what}通道: 设备端采集器没在 {remote_first}..={remote_last} 上应答 (最后一次: {last})"
        ))
    }

    /// 握手: 发鉴权帧 + 问版本, 然后**读到拿着帧尾的那条为止**。
    ///
    /// 设备端分两次应答: 先裸 `1.26\0` (协议版本), 再 `value=<采集器版本>;end;`。
    /// 只读一次就判协议对不对, 会把正常握手判成「应答不是这个协议」——
    /// HiSmartPerf 自己的日志里这两条也是分两次 receive 收的。
    fn handshake(local: u16) -> Result<(TcpStream, Option<String>), String> {
        let sock = TcpStream::connect(("127.0.0.1", local)).map_err(|e| e.to_string())?;
        sock.set_read_timeout(Some(Duration::from_millis(HANDSHAKE_READ_MS))).ok();
        sock.set_write_timeout(Some(Duration::from_millis(2_000))).ok();
        let mut s = sock;
        // 顺序照 HiSmartPerf: 先鉴权帧, 紧跟着问版本。少了鉴权帧设备端直接闭嘴。
        s.write_all(HANDSHAKE.as_bytes()).map_err(|e| e.to_string())?;
        s.write_all(cmd_get_version().as_bytes()).map_err(|e| e.to_string())?;

        let mut seen = String::new();
        let t0 = Instant::now();
        let mut raw = [0u8; 4096];
        while t0.elapsed() < Duration::from_millis(HANDSHAKE_TOTAL_MS) {
            match s.read(&mut raw) {
                Ok(0) => return Err("设备端把连接关了".into()),
                Ok(n) => {
                    seen.push_str(&String::from_utf8_lossy(&raw[..n]));
                    if let Some(v) = parse_version(&seen) {
                        return Ok((s, Some(v)));
                    }
                }
                Err(ref e)
                    if e.kind() == std::io::ErrorKind::WouldBlock
                        || e.kind() == std::io::ErrorKind::TimedOut =>
                {
                    break
                }
                Err(e) => return Err(e.to_string()),
            }
        }
        // 只收到鉴权应答也算握上了 (这台机器的数据通道就只回这一条):
        // 它证明对面确实是这个协议, 只是没吐版本号。
        if looks_like_auth_ack(&seen) {
            return Ok((s, None));
        }
        Err(format!("应答不是这个协议: {seen:?}"))
    }

    /// 发一条控制命令, 并**把回执读干净再返回**。
    ///
    /// 必须读回执: 不读就接着发下一条, 两条命令会挤进同一个 TCP 段, 而设备端一次只解析
    /// 一条 —— 后一条被整条丢掉, 且**不报错**。2026-09-23 实测的症状是:
    /// `selectPid` 回 `ret=0` 一切正常, 紧跟着的 `startCollect` 石沉大海,
    /// 数据通道一个字节都不来。只看 `sample_count: 0` 会以为是目标应用没在前台,
    /// 实际上是命令根本没被执行。(不传 pid 时只发一条命令, 反而是好的 ——
    /// 于是这个 bug 只在「拿得到 pid」时触发, 更难判。)
    fn send_cmd(&mut self, cmd: &str) -> Result<String, String> {
        self.ctl.write_all(cmd.as_bytes()).map_err(|e| format!("发 {cmd:?} 失败: {e}"))?;
        let mut seen = String::new();
        let t0 = Instant::now();
        let mut raw = [0u8; 8192];
        while t0.elapsed() < Duration::from_millis(CMD_ACK_MS) {
            match self.ctl.read(&mut raw) {
                Ok(0) => break,
                Ok(n) => {
                    seen.push_str(&String::from_utf8_lossy(&raw[..n]));
                    if seen.contains(FRAME_END) {
                        return Ok(seen);
                    }
                }
                Err(ref e)
                    if e.kind() == std::io::ErrorKind::WouldBlock
                        || e.kind() == std::io::ErrorKind::TimedOut =>
                {
                    break
                }
                Err(e) => return Err(format!("读 {cmd:?} 的回执失败: {e}")),
            }
        }
        Ok(seen)
    }

    /// 设备信息 (进 `meta`, 原样透传)。
    pub fn device_info(&mut self) -> BTreeMap<String, String> {
        let Ok(text) = self.send_cmd(&cmd_get_device_info()) else { return BTreeMap::new() };
        let (frames, _) = split_frames(&text);
        frames.iter().map(|f| parse_device_info(f)).find(|m| !m.is_empty()).unwrap_or_default()
    }

    /// 开采并收满 `want` 条实时样本 (设备端约每秒一条), 超时就返回已收到的。
    ///
    /// 收不满也照样返回: 上层按 `sample_count` 判, 一条都没有才算这轮没采到。
    /// 这里**不补齐、不插值** —— 没采到的那一秒就是没采到。
    pub fn collect(
        &mut self,
        mask: u32,
        pkg: &str,
        pid: Option<i64>,
        want: usize,
        timeout_ms: u64,
    ) -> Result<Vec<GpSample>, String> {
        if let Some(p) = pid {
            self.send_cmd(&cmd_select_pid(p))?;
        }
        let ack = self.send_cmd(&cmd_start_collect(mask, pkg, pid, true))?;
        if ack.trim().is_empty() {
            return Err("startCollect 没有回执: 设备端多半没解析到这条命令".into());
        }
        self.data.set_read_timeout(Some(Duration::from_millis(2_000))).ok();
        let mut out = Vec::new();
        let mut got_bytes = 0usize;
        let t0 = Instant::now();
        let mut raw = [0u8; 16384];
        while out.len() < want && t0.elapsed() < Duration::from_millis(timeout_ms) {
            match self.data.read(&mut raw) {
                Ok(0) => break,
                Ok(n) => {
                    got_bytes += n;
                    let chunk = String::from_utf8_lossy(&raw[..n]);
                    // 原始线格式落盘 (可选)。交叉对比要能解释「两边差的这几个百分点
                    // 到底是口径差异还是窗口边缘效应」—— 没有逐秒原始序列就只能猜。
                    if let Some(p) = std::env::var_os(RAW_DUMP_ENV) {
                        use std::io::Write as _;
                        if let Ok(mut f) =
                            std::fs::OpenOptions::new().create(true).append(true).open(&p)
                        {
                            let _ = f.write_all(chunk.as_bytes());
                        }
                    }
                    self.buf.push_str(&chunk);
                    let (mut got, rest) = parse_stream(&self.buf);
                    self.buf = rest;
                    out.append(&mut got);
                }
                // 读超时不是故障: 设备端一秒才吐一条, 空读很正常, 继续等到总超时为止
                Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => continue,
                Err(ref e) if e.kind() == std::io::ErrorKind::TimedOut => continue,
                Err(e) => return Err(format!("读实时样本失败: {e}")),
            }
        }
        let _ = self.send_cmd(&cmd_stop_collect());
        // 这条诊断不是调试残留: 「一条样本都没收到」有好几种完全不同的成因
        // (目标应用退了 / 数据通道连错了 / startCollect 被拒), 光看 sample_count=0 分不出来。
        // 收到多少字节、切出多少条记录, 是区分它们最直接的证据。
        crate::hwcond::progress(
            "gpd",
            &format!(
                "数据通道收 {got_bytes} 字节 · 切出 {} 条样本 · 残余 {} 字节",
                out.len(),
                self.buf.len()
            ),
        );
        out.truncate(want);
        Ok(out)
    }
}

impl Drop for Session<'_> {
    fn drop(&mut self) {
        let _ = self.ctl.shutdown(std::net::Shutdown::Both);
        let _ = self.data.shutdown(std::net::Shutdown::Both);
        self.phone.forward_remove(self.ctl_port);
        self.phone.forward_remove(self.data_port);
        if let Some(mut d) = self.daemon.take() {
            let _ = d.kill();
            let _ = d.wait();
        }
        // adb shell 的子进程被杀不代表设备那头也死了 —— 必须显式再杀一次设备侧进程
        kill_stale(self.phone);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── 命令构造 ──

    #[test]
    fn start_collect_matches_hismartperf_wire_format() {
        // 逐字对齐 dist/umi.js 拼出来的串 (含结尾那个多余逗号)
        assert_eq!(
            cmd_start_collect(53, "com.miHoYo.Yuanshen", Some(1234), true),
            "cmd=startCollect;para=itemMask:53,package:com.miHoYo.Yuanshen,pid:1234,realtimeEnable:1,;end;"
        );
    }

    #[test]
    fn start_collect_without_pid_omits_the_pid_token() {
        // pid 未知时不能发 `pid:-1` —— 设备端会把它当一个真的 pid 去找
        let s = cmd_start_collect(DEFAULT_MASK, "com.x", None, false);
        assert!(!s.contains("pid:"), "{s}");
        assert!(!s.contains("realtimeEnable"), "{s}");
        assert!(s.ends_with(";end;"), "{s}");
    }

    #[test]
    fn negative_pid_is_dropped_not_sent() {
        let s = cmd_start_collect(1, "com.x", Some(-1), true);
        assert!(!s.contains("pid:"), "{s}");
    }

    #[test]
    fn default_mask_is_fps_gpu_temp_power() {
        assert_eq!(DEFAULT_MASK, 1 + 4 + 16 + 32);
        // 不能顺手开上这三个: 它们都会在设备上落盘或要外部工具链, 不是只读采集
        assert_eq!(DEFAULT_MASK & item::CPU_TRACE, 0);
        assert_eq!(DEFAULT_MASK & item::CPU_FLAME_GRAPH, 0);
        assert_eq!(DEFAULT_MASK & item::ENGINE, 0);
    }

    #[test]
    fn two_commands_must_not_be_concatenated_into_one_write() {
        // 这两条如果在同一个 TCP 段里到达, 设备端只解析第一条, startCollect 被整条丢掉
        // 且不报错 —— 2026-09-23 实测症状是数据通道 0 字节。命令自带帧尾不足以救场,
        // 所以纪律在 send_cmd: 每条命令都把回执读干净再发下一条。
        let a = cmd_select_pid(25872);
        let b = cmd_start_collect(DEFAULT_MASK, "io.github.hgamey.refbench", Some(25872), true);
        let glued = format!("{a}{b}");
        // 粘在一起时, 帧尾切分能切出两帧 —— 设备端却不是这么读的, 故不能依赖它
        let (frames, _) = split_frames(&glued);
        assert_eq!(frames.len(), 2, "帧尾本身是切得开的");
        assert!(frames[1].starts_with("cmd=startCollect"), "第二条就是被丢掉的那条");
    }

    #[test]
    fn other_commands_are_verbatim() {
        assert_eq!(cmd_get_version(), "cmd=getVersion;end;");
        assert_eq!(cmd_get_device_info(), "cmd=getDeviceInfo;end;");
        assert_eq!(cmd_select_pid(42), "cmd=selectPid;para=42;end;");
        assert_eq!(cmd_stop_collect(), "cmd=stopCollect;para=pcTmp;end;");
        assert_eq!(HANDSHAKE, "0012|authorize:0;");
    }

    // ── 切帧 ──

    #[test]
    fn frames_split_on_end_marker_not_on_semicolon() {
        // 样本正文里全是分号; 按 `;` 切就会把一条样本碎成十几片
        let (f, rest) = split_frames("value={fps:30;refresh:60;};end;value=v1.28;end;");
        assert_eq!(f, vec!["value={fps:30;refresh:60;}", "value=v1.28"]);
        assert_eq!(rest, "");
    }

    #[test]
    fn half_a_frame_is_kept_as_remainder() {
        // TCP 不保证一次 read 正好一帧; 半帧丢掉就是稳定漏采样
        let (f, rest) = split_frames("value={fps:30;};end;value={fps:2");
        assert_eq!(f.len(), 1);
        assert_eq!(rest, "value={fps:2");
        // 续上下一段后应当补齐, 且不丢那一帧
        let (f2, rest2) = split_frames(&format!("{rest}9;}};end;"));
        assert_eq!(f2, vec!["value={fps:29;}"]);
        assert_eq!(rest2, "");
    }

    // ── 样本解析 ──

    /// 2026-09-23 红魔 NX809J 上原神实跑时, 实时数据通道 (20103) 吐出来的**原样**两条。
    /// 逐字节照抄, 没有整形: 裸 `{…}`, 没有 `value=` 前缀也没有 `;end;` 帧尾。
    /// itemMask 为 53 (帧率+GPU+温度+功耗), 故未开的项 (内存/CPU/网络) 全是 0。
    fn real_sample() -> &'static str {
        "{fps:30;refresh:0;gpuFreq:0;gpuUsage:74;ddrFreq:0;current:0;voltage:4207000;netTx:0;\
netRx:0;appCpu:0;memoryPss:0;swapMemory:0;virtualMemory:0;availableMemory:0;javaHeap:0;\
nativeHeap:0;dalvikHeap:0;dalvikOther:0;stack:0;ashmem:0;gfx:0;otherDev:0;soMmap:0;apkMmap:0;\
jarMmap:0;ttfMmap:0;dexMmap:0;oatMmap:0;artMmap:0;otherMmap:0;EGLMtrack:0;GLMtrack:0;\
otherMtrack:0;unknown:0;cursor:0;totalSwap:0;cluster0freq:0;cluster1freq:0;cpu0usage:0;\
cpu1usage:0;cpu2usage:0;cpu3usage:0;cpu4usage:0;cpu5usage:0;cpu6usage:0;cpu7usage:0;\
shellFrame:0;shellBack:0;soc:0;system:0;cpuTemp:0;gpuTemp:49200;npuTemp:0;batTemp:44000;}\
{fps:30;refresh:0;gpuFreq:0;gpuUsage:73;ddrFreq:0;current:-87000;voltage:4206000;netTx:0;\
netRx:0;appCpu:0;shellFrame:0;shellBack:0;soc:0;system:0;cpuTemp:0;gpuTemp:51200;npuTemp:0;\
batTemp:44000;}"
    }

    #[test]
    fn realtime_sample_parses_into_numbers() {
        let (s, rest) = parse_stream(real_sample());
        assert_eq!(rest, "");
        assert_eq!(s.len(), 2, "两条裸 {{…}} 记录, 没有帧尾也要切得开");
        assert_eq!(s[0].get("fps"), Some(30));
        assert_eq!(s[0].get("gpuUsage"), Some(74));
        assert_eq!(s[0].get("voltage"), Some(4_207_000));
        assert_eq!(s[0].get("gpuTemp"), Some(49_200));
        assert_eq!(s[1].get("current"), Some(-87_000));
        assert_eq!(s[1].get("gpuTemp"), Some(51_200));
    }

    #[test]
    fn non_numeric_values_are_dropped_not_zeroed() {
        // 设备端会在同一条记录里塞 gpuType:qualcomm 这种字符串值;
        // 收成 0 就会凭空多出一个「测量值」
        let (s, _) = parse_stream("{fps:30;gpuType:qualcomm;batTemp:44000;}");
        assert_eq!(s[0].get("gpuType"), None);
        assert!(!s[0].fields.contains_key("gpuType"));
        assert_eq!(s[0].get("fps"), Some(30));
    }

    #[test]
    fn command_receipts_are_not_mistaken_for_samples() {
        // ret= / value= 是控制通道的回执, 不是数据; 收进来就会虚报 sample_count。
        // 数据通道按花括号切, 回执里没有花括号, 天然切不出记录。
        let (s, rest) = parse_stream("ret=0;end;ret=-10001;end;value=v1.267;end;");
        assert!(s.is_empty());
        assert_eq!(rest, "", "没有记录开头的零碎字节要丢掉, 不能让缓冲区无限涨");
    }

    #[test]
    fn version_frame_is_recognised() {
        assert_eq!(parse_version("value=v1.267;end;").as_deref(), Some("v1.267"));
        // 握手第一条是裸的协议版本 `1.26\0`, 不带 value= 也不带帧尾 —— 不能当采集器版本
        assert_eq!(parse_version("1.26\0"), None);
        assert!(looks_like_auth_ack("1.26\0"));
        assert!(!looks_like_auth_ack("value=v1.267;end;"));
        // 两条一起到时, 取的必须是带帧尾的那条
        assert_eq!(parse_version("1.26\0value=v1.267;end;").as_deref(), Some("v1.267"));
    }

    #[test]
    fn device_info_keeps_string_values_verbatim() {
        // 实测线格式: 载荷挂在 info= 后面, 不是 value=
        let m = parse_device_info("ret=0;info=version:v1.0;gpuType:0;");
        assert_eq!(m.get("version").map(String::as_str), Some("v1.0"));
        assert_eq!(m.get("gpuType").map(String::as_str), Some("0"));
        // 没有 info= 的回执不该解析出任何东西
        assert!(parse_device_info("ret=0;").is_empty());
    }

    // ── 单位与「0 不是测量值」 ──

    #[test]
    fn watt_reuses_hwcond_arithmetic_on_the_raw_sysfs_values() {
        // 采集器把 current_now / voltage_now 原值透传, 与 hwcond 读的是同一个节点,
        // 故必须走同一份 watt() —— 不能在这里自己再折算一遍单位。
        let (s, _) = parse_stream(real_sample());
        let p = sample_power(&s[1]).unwrap();
        assert_eq!((p.curr_ua, p.volt_uv), (-87_000, 4_206_000));
        assert_eq!(sample_watt(&s[1]), p.watt());
        // 放电电流是负的; 要的是功率的大小。87 mA x 4.206 V = 0.366 W
        let w = sample_watt(&s[1]).unwrap();
        assert!((w - 0.365_922).abs() < 1e-6, "{w}");
    }

    #[test]
    fn zero_current_or_voltage_is_not_zero_watts() {
        let mut s = GpSample::default();
        s.fields.insert("current".into(), 0);
        s.fields.insert("voltage".into(), 4_212_000);
        assert_eq!(sample_watt(&s), None);
        s.fields.insert("current".into(), -1000);
        s.fields.insert("voltage".into(), 0);
        assert_eq!(sample_watt(&s), None);
    }

    #[test]
    fn missing_power_fields_give_none_not_zero() {
        assert_eq!(sample_watt(&GpSample::default()), None);
    }

    #[test]
    fn a_zero_thermal_zone_is_a_missing_sensor_not_zero_celsius() {
        let (s, _) = parse_stream(real_sample());
        // 这台红魔的 npuTemp / shellFrame / soc / cpuTemp 全回 0 = 它没有采集器要找的那几个热区
        for missing in ["npuTemp", "shellFrame", "cpuTemp", TEMP_SOC] {
            assert_eq!(temp_c(&s[0], missing), None, "{missing} 的 0 不是 0 摄氏度");
        }
        // 毫摄氏度 → 摄氏度。按 power_supply/battery/temp 的 0.1 C 制折算会算成 4400 C
        assert_eq!(temp_c(&s[0], TEMP_GPU), Some(49.2));
        assert_eq!(temp_c(&s[0], TEMP_BATTERY), Some(44.0));
    }

    #[test]
    fn empty_record_is_not_a_sample() {
        assert_eq!(parse_sample("{}"), None);
        assert_eq!(parse_sample("{;;;}"), None);
    }
}
