//! 系统参数闭环的两块纯逻辑: 「哪些参数准改、准改成什么」与「这组参数保留还是淘汰」。
//!
//! 由 `loop_v1/auto/whitelist.py` 与 `loop_v1/auto/verdict.py` 逐行搬过来, 输出字节一致。
//! 全程不读设备、不读时钟、无随机数 —— 输入 probe 文本与对比统计, 输出白名单与判定。
//!
//! 白名单不是写死的常量表
//! ----------------------
//! 它是 `probe_sysparam.sh` 真机探测结果的函数: 每个候选参数要同时满足
//! 「节点存在 + 可写 + 写了真生效」才进白名单, 三关缺一不可。探不到的、写了被内核
//! 退回的, 一律不进 —— 宁可少改, 不可假改。
//!
//! 另有一条不看探测结果的硬规矩: **温控保护相关的任何节点永远不进白名单**。
//! 关热保护、抬温控阈值能立刻换来漂亮数字, 但那是拿硬件安全换指标, 不是优化。
//! [`DENY_KEYWORDS`] 在这里拦一道, `knob_sysparam.sh` 在设备端再拦一道。
//!
//! 判定看三样, 不是只看省电
//! ------------------------
//! | 指标         | 方向     | 来源                       |
//! |--------------|----------|----------------------------|
//! | frame_p95    | 越小越好 | ftrace 帧时序 (帧时稳定性) |
//! | fps_mean     | 越大越好 | ftrace 帧时序 (帧率)       |
//! | power_w_mean | 越小越好 | power_supply 轨 (整机功耗) |
//!
//! 系统参数不动画面, 所以画质天然不变, 不必量。温度不作为「更好」的加分项,
//! 只作为**上限**: 超过 temp_cap 这一轮直接作废并还原, 不参与比较。
//!
//! 多重比较: 一次看 3 个指标, 按 0.05 判「显著改善」会把假阳性抬到约 14%。
//! 所以「改善」一侧用 Bonferroni 收紧到 alpha/K, 「变差」一侧仍用 0.05 不收紧
//! —— 两侧故意不对称: 宁可漏掉一个真改善, 也不要把一个真退化放过去。

use crate::pyjson::{py_round, py_sum, PyVal};
use crate::pyobj;
use regex::Regex;

/// 温控保护关键词。命中即拒, 不看探测结果, 不看大模型怎么说。
pub const DENY_KEYWORDS: [&str; 7] = [
    "thermal",
    "trip_point",
    "cooling",
    "fan",
    "tsens",
    "bcl",
    "throttl",
];

/// 生效判定: 只有这一种结论算「写了真生效且守得住」。
///
/// 注意 `contested` **不在**里面 —— 那是「写进去了, 但十几秒后被厂商管家改回去」。
/// 守不住的节点当不了旋钮: 施加臂跑到一半参数就没了, 量到的不是那组参数。
const EFFECT_OK: [&str; 1] = ["live"];
/// 部分节点当前值已经是探测值, 无法做差异写测试 —— 可写 + 有合法取值表即可进,
/// 每轮还有 snap_at_run.txt 复核实际生效, 所以不会放过假生效。
const EFFECT_SOFT: [&str; 1] = ["skipped_same_value"];

pub(crate) const ALPHA: f64 = 0.05;
/// 手机整机功耗的合理区间 (瓦)。本机 battery/power_now 读出过 777W —— 单位有误,
/// 这种数不进任何均值, 只如实记录并标 plausible=False。
const PLAUSIBLE_W: (f64, f64) = (0.05, 30.0);
/// (指标, 方向) —— 方向 "lower" = 越小越好
const PRIMARY_METRICS: [(&str, &str); 3] = [
    ("frame_p95", "lower"),
    ("fps_mean", "higher"),
    ("power_w_mean", "lower"),
];
const RULE_VERSION: &str = "sysparam_v1";

// ══════════════ probe 文本 ══════════════

/// 保序的 key=value 表 (Python dict 语义: 重复键就地覆盖, 不改位置)。
/// 顺序要紧 —— 厂商刷新率那一段要按 probe 里出现的顺序遍历。
#[derive(Default, Clone)]
pub struct ProbeMap(Vec<(String, String)>);

impl ProbeMap {
    pub fn get(&self, k: &str) -> Option<&str> {
        self.0.iter().find(|(ek, _)| ek == k).map(|(_, v)| v.as_str())
    }
    /// Python `d.get(k, "")` / `d.get(k) or ""`
    fn s(&self, k: &str) -> &str {
        self.get(k).unwrap_or("")
    }
    fn set(&mut self, k: &str, v: &str) {
        match self.0.iter_mut().find(|(ek, _)| ek == k) {
            Some(slot) => slot.1 = v.to_string(),
            None => self.0.push((k.to_string(), v.to_string())),
        }
    }
    pub fn iter(&self) -> impl Iterator<Item = (&str, &str)> {
        self.0.iter().map(|(k, v)| (k.as_str(), v.as_str()))
    }
}

/// `probe_sysparam.sh` 的 key=value 文本 → 表。`#` 开头是注释。
pub fn parse_probe(text: &str) -> ProbeMap {
    let mut out = ProbeMap::default();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((k, v)) = line.split_once('=') else {
            continue;
        };
        out.set(k.trim(), v.trim());
    }
    out
}

// ══════════════ 白名单 ══════════════

/// 一个参数准写哪些值。
#[derive(Clone, Debug, PartialEq)]
pub struct Spec {
    /// "sysfs" | "setting"
    pub kind: String,
    /// 设备端路径 (sysfs 绝对路径, 或 "<ns>:<key>")
    pub path: String,
    /// 允许的取值列表, 大模型只能从里面挑
    pub values: Vec<String>,
    /// 探测时的原值
    pub current: String,
    /// 用于跨项约束检查 (同 group 的 min/max 要成对合法)
    pub group: Option<String>,
    /// 这一项是凭什么进来的 —— 证据里直接看得到, 不必回头翻 probe.txt
    pub effect: String,
}

/// 按插入顺序排列的白名单。
pub type Whitelist = Vec<(String, Spec)>;

pub fn wl_get<'a>(wl: &'a Whitelist, pid: &str) -> Option<&'a Spec> {
    wl.iter().find(|(k, _)| k == pid).map(|(_, v)| v)
}

fn ints(s: &str) -> Vec<i64> {
    let mut v: Vec<i64> = s
        .replace(',', " ")
        .split_whitespace()
        .filter_map(|t| t.parse::<i64>().ok())
        .collect();
    v.sort_unstable();
    v.dedup();
    v
}

/// 从 dumpsys 抓来的刷新率文本里取出档位。
///
/// 不同 Android 版本的措辞不一样 ("120.00001 fps" / "fps=120" / "60.000004"),
/// 所以只认数字本身, 四舍五入到整数再去重 —— 120.00001 与 120 是同一档。
pub fn fps_list(s: &str) -> Vec<i64> {
    let re = Regex::new(r"\d+(?:\.\d+)?").expect("fps re");
    let mut v: Vec<i64> = re
        .find_iter(s)
        .filter_map(|m| m.as_str().parse::<f64>().ok())
        // Python 的 round() 是 banker's rounding, py_round(x, 0) 走同一条规则
        .map(|x| py_round(x, 0) as i64)
        .filter(|x| *x > 0)
        .collect();
    v.sort_unstable();
    v.dedup();
    v
}

pub fn denied(path: &str) -> bool {
    let low = path.to_lowercase();
    DENY_KEYWORDS.iter().any(|k| low.contains(k))
}

/// 可写 + (生效测试通过 或 这台机器上没有可供差异测试的第二个值)。
///
/// 差异写测试分两步看: 写进去 1 秒后回读 (写得进吗), 再等 10 秒回读 (守得住吗)。
/// 守不住的报 `contested`, 不进白名单 —— 红魔的厂商温控/性能管家会在游戏过程中主动压
/// `scaling_max_freq` 与 `kgsl.max_pwrlevel`, 只看 1 秒回读会把它们误判成可用。
///
/// `probe_sysparam.sh` 对每个 sysfs 候选都会做差异写测试, 但有些节点在当前设备状态下
/// 找不到合法的第二个值可试 (例如当前值已经是唯一能写的那个), 这时 `.effect` 缺失。
/// 这种情况放行, 但把 `effect` 原样记进白名单 spec, 证据里看得到哪几项是
/// 「未经差异测试」进来的 —— 不含糊过去。
/// settings 类 (刷新率) 没有差异测试, 靠每轮的 snap_at_run.txt 复核实际生效。
fn usable(probe: &ProbeMap, key: &str) -> bool {
    if probe.get(&format!("{key}.writable")) != Some("yes") {
        return false;
    }
    match probe.get(&format!("{key}.effect")) {
        None => true,
        Some(e) => EFFECT_OK.contains(&e) || EFFECT_SOFT.contains(&e),
    }
}

fn effect_of(probe: &ProbeMap, key: &str) -> String {
    probe
        .get(&format!("{key}.effect"))
        .unwrap_or("untested")
        .to_string()
}

struct Builder<'a> {
    probe: &'a ProbeMap,
    wl: Whitelist,
}

impl Builder<'_> {
    #[allow(clippy::too_many_arguments)]
    fn add(
        &mut self,
        pid: &str,
        kind: &str,
        path: &str,
        values: Vec<String>,
        current: &str,
        group: Option<&str>,
        probe_key: Option<&str>,
    ) {
        if denied(path) || denied(pid) {
            return;
        }
        let values: Vec<String> = values.into_iter().filter(|v| !v.is_empty()).collect();
        if values.len() < 2 {
            return;
        }
        let spec = Spec {
            kind: kind.to_string(),
            path: path.to_string(),
            values,
            current: current.to_string(),
            group: group.map(String::from),
            effect: match probe_key {
                Some(k) => effect_of(self.probe, k),
                None => "n/a(setting)".to_string(),
            },
        };
        match self.wl.iter_mut().find(|(k, _)| k == pid) {
            Some(slot) => slot.1 = spec,
            None => self.wl.push((pid.to_string(), spec)),
        }
    }
}

fn strs(xs: &[i64]) -> Vec<String> {
    xs.iter().map(|x| x.to_string()).collect()
}

/// probe → 白名单。
pub fn build_whitelist(probe: &ProbeMap) -> Whitelist {
    let mut b = Builder {
        probe,
        wl: Vec::new(),
    };

    // ── CPU 各簇 ──
    for pol in probe.s("cpu.policies").split(',') {
        let pol = pol.trim();
        if pol.is_empty() {
            continue;
        }
        let k = format!("cpu.policy{pol}");
        let base = format!("/sys/devices/system/cpu/cpufreq/policy{pol}");
        let freqs = ints(probe.s(&format!("{k}.avail_freqs")));
        if !freqs.is_empty() {
            for which in ["scaling_min_freq", "scaling_max_freq"] {
                let key = format!("{k}.{which}");
                if usable(probe, &key) {
                    let cur = probe.s(&format!("{key}.cur")).to_string();
                    b.add(
                        &key,
                        "sysfs",
                        &format!("{base}/{which}"),
                        strs(&freqs),
                        &cur,
                        Some(&format!("{k}.freq")),
                        Some(&key),
                    );
                }
            }
        }
        let govs: Vec<String> = probe
            .s(&format!("{k}.avail_governors"))
            .split_whitespace()
            .map(String::from)
            .collect();
        let gkey = format!("{k}.scaling_governor");
        if !govs.is_empty() && usable(probe, &gkey) {
            let cur = probe.s(&format!("{gkey}.cur")).to_string();
            b.add(
                &gkey,
                "sysfs",
                &format!("{base}/scaling_governor"),
                govs,
                &cur,
                None,
                Some(&gkey),
            );
        }
        // walt 冲高规则与升降频速率 (探测脚本只在 governor=walt 时报)。取值是一组离散档, 当前值一定在内:
        // hispeed_load 百分比、hispeed_freq 用本簇可用频点、rate_limit 微秒。
        for (t, vals) in [
            ("hispeed_load", (60..=100).step_by(5).map(|v: i64| v.to_string()).collect::<Vec<_>>()),
            ("hispeed_freq", strs(&freqs)),
            ("up_rate_limit_us", [0i64, 500, 1000, 2000, 5000, 10000].iter().map(|v| v.to_string()).collect()),
            ("down_rate_limit_us", [0i64, 500, 1000, 2000, 5000, 10000].iter().map(|v| v.to_string()).collect()),
        ] {
            let key = format!("{k}.walt.{t}");
            let cur = probe.s(&format!("{key}.cur")).to_string();
            if cur.is_empty() || vals.is_empty() || !usable(probe, &key) {
                continue;
            }
            let mut vals = vals;
            if !vals.contains(&cur) {
                vals.push(cur.clone());
            }
            b.add(&key, "sysfs", &format!("{base}/walt/{t}"), vals, &cur, None, Some(&key));
        }
    }

    // ── GPU ──
    let kgsl = "/sys/class/kgsl/kgsl-3d0";
    let nlv: i64 = probe.s("gpu.num_pwrlevels").parse().unwrap_or(0);
    if nlv > 1 {
        // kgsl 语义: 0 = 最快档, 数字越大越慢。min_pwrlevel 是「最慢准到哪」,
        // max_pwrlevel 是「最快准到哪」, 因此恒有 max_pwrlevel <= min_pwrlevel。
        let lv: Vec<i64> = (0..nlv).collect();
        for which in ["min_pwrlevel", "max_pwrlevel"] {
            let key = format!("gpu.{which}");
            if usable(probe, &key) {
                let cur = probe.s(&format!("{key}.cur")).to_string();
                b.add(
                    &key,
                    "sysfs",
                    &format!("{kgsl}/{which}"),
                    strs(&lv),
                    &cur,
                    Some("gpu.pwrlevel"),
                    Some(&key),
                );
            }
        }
    }
    let gfreqs = ints(probe.s("gpu.devfreq.avail_freqs"));
    if !gfreqs.is_empty() {
        for which in ["min_freq", "max_freq"] {
            let key = format!("gpu.devfreq.{which}");
            if usable(probe, &key) {
                let cur = probe.s(&format!("{key}.cur")).to_string();
                b.add(
                    &key,
                    "sysfs",
                    &format!("{kgsl}/devfreq/{which}"),
                    strs(&gfreqs),
                    &cur,
                    Some("gpu.devfreq"),
                    Some(&key),
                );
            }
        }
    }
    let ggov: Vec<String> = probe
        .s("gpu.devfreq.avail_governors")
        .split_whitespace()
        .map(String::from)
        .collect();
    if !ggov.is_empty() && usable(probe, "gpu.devfreq.governor") {
        let cur = probe.s("gpu.devfreq.governor.cur").to_string();
        b.add(
            "gpu.devfreq.governor",
            "sysfs",
            &format!("{kgsl}/devfreq/governor"),
            ggov,
            &cur,
            None,
            Some("gpu.devfreq.governor"),
        );
    }

    // ── 总线 DDR / LLCC 下限 (已验证过的那一条就在这里) ──
    for n in ["DDR", "LLCC"] {
        let key = format!("bus.{n}.boost_freq");
        if !usable(probe, &key) {
            continue;
        }
        let mut vals = ints(probe.s(&format!("bus.{n}.avail_freqs")));
        if vals.is_empty() {
            let lo = probe.s(&format!("bus.{n}.hw_min_freq"));
            let hi = probe.s(&format!("bus.{n}.hw_max_freq"));
            vals = ints(&format!("{lo} {hi}"));
        }
        let cur = probe.s(&format!("{key}.cur")).to_string();
        b.add(
            &key,
            "sysfs",
            &format!("/sys/devices/system/cpu/bus_dcvs/{n}/boost_freq"),
            strs(&vals),
            &cur,
            None,
            Some(&key),
        );
    }

    // ── 刷新率 ──
    // 两条路都试: AOSP 的 peak/min_refresh_rate, 以及厂商自己的 refresh_rate_mode。
    // 本机 (红魔 NX809J) AOSP 那两个键是 null, 走的是 refresh_rate_mode。
    let modes = fps_list(probe.s("display.modes"));
    for (pid, skey) in [
        ("setting.system.peak_refresh_rate", "peak_refresh_rate"),
        ("setting.system.min_refresh_rate", "min_refresh_rate"),
    ] {
        let cur = probe.s(&format!("setting.system.{skey}.cur")).to_string();
        // Known unset is reversible only with the controlled driver's actual read/hold/restore receipts.
        let known_unset = cur == "null" && probe.s(&format!("{pid}.read_exit")) == "0"
            && usable(probe,pid) && probe.s(&format!("{pid}.effect_restored")) == "yes";
        if cur.is_empty() || (cur == "null" && !known_unset) || modes.is_empty() {
            continue;
        }
        b.add(
            pid,
            "setting",
            &format!("system:{skey}"),
            strs(&modes),
            &cur,
            Some("refresh"),
            None,
        );
    }

    // 厂商键的取值语义没有文档, 所以不按 all_refresh_rate 的下标猜, 只认探测时
    // **真的把活动刷新率改掉了**的那几档 —— 探测脚本逐档写进去看 SurfaceFlinger
    // 的活动模式 fps 跟不跟着变, 结果落在 refresh_rate_mode.mode<i>_fps 行里。
    let rrm_cur = probe.s("setting.system.refresh_rate_mode.cur").to_string();
    if !rrm_cur.is_empty() && rrm_cur != "null" && is_digits(&rrm_cur) {
        // base_fps 必须是个真数字。探不到活动刷新率 (dumpsys 措辞不同 / 没有
        // mActiveSfDisplayMode 那一行) 时 base_fps 与各档 fps 都是空, 这时候一档都
        // 不能收 —— 没有 fps 证据就收下去, 等于在按下标猜, 正是这里不干的事。
        let base_fps = probe.s("setting.system.refresh_rate_mode.base_fps").to_string();
        let mut live = vec![rrm_cur.clone()];
        if is_digits(&base_fps) {
            let rb_re = Regex::new(r"readback=([^)\s]*)").expect("readback re");
            const PRE: &str = "setting.system.refresh_rate_mode.mode";
            for (k, v) in probe.iter() {
                let Some(rest) = k.strip_prefix(PRE).and_then(|r| r.strip_suffix("_fps")) else {
                    continue;
                };
                if !is_digits(rest) {
                    continue;
                }
                let fps = v.split_whitespace().next().unwrap_or("");
                // 回读整串精确比对 (不能用子串: readback=1 会命中 readback=10),
                // 再要求活动刷新率确实是个数字且与基准不同 = 这一档真生效
                let rb_ok = rb_re
                    .captures(v)
                    .is_some_and(|c| c.get(1).map(|m| m.as_str()) == Some(rest));
                if rb_ok && is_digits(fps) && fps != base_fps {
                    live.push(rest.to_string());
                }
            }
        }
        let mut uniq = live.clone();
        uniq.sort();
        uniq.dedup();
        if uniq.len() > 1 {
            uniq.sort_by_key(|s| s.parse::<i64>().unwrap_or(0));
            b.add(
                "setting.system.refresh_rate_mode",
                "setting",
                "system:refresh_rate_mode",
                uniq,
                &rrm_cur,
                Some("refresh"),
                None,
            );
        }
    }

    b.wl
}

/// Python `str.isdigit()` 在这里的用法: 非空且全是 ASCII 数字。
fn is_digits(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|c| c.is_ascii_digit())
}

// ══════════════ 校验 / plan / 说明 ══════════════

/// Python 列表切片的 repr: `['a', 'b']`
fn list_repr(xs: &[String]) -> String {
    format!(
        "[{}]",
        xs.iter()
            .map(|v| format!("'{v}'"))
            .collect::<Vec<_>>()
            .join(", ")
    )
}

/// Python `int(cand.get(id, wl[id]['current'] or 默认))` 那一串的等价物。
///
/// 解析不了就是错, 不是"取默认值"。默认值一律是**最宽松**的那一端, 拿它兜底等于
/// 把 min<=max / pwrlevel 倒置这些闸门直接关掉 —— Python 那边 `int("abc")` 是抛
/// ValueError 把整组打掉的, 这里照样报错。
fn cross_val(cand: &[(String, String)], wl: &Whitelist, pid: &str, dflt: i64) -> Result<i64, String> {
    if let Some((_, v)) = cand.iter().find(|(k, _)| k == pid) {
        return v.parse().map_err(|_| format!("{pid}={v} 不是整数"));
    }
    match wl_get(wl, pid).map(|s| s.current.as_str()) {
        // Python 的 `or`: 空串是假值, 退回默认
        Some(c) if !c.is_empty() => c
            .parse()
            .map_err(|_| format!("{pid} 的探测原值 {c:?} 不是整数, 无法做跨项约束检查")),
        _ => Ok(dflt),
    }
}

fn cross_val_f(cand: &[(String, String)], wl: &Whitelist, pid: &str, dflt: f64) -> Result<f64, String> {
    if let Some((_, v)) = cand.iter().find(|(k, _)| k == pid) {
        return v.parse().map_err(|_| format!("{pid}={v} 不是数字"));
    }
    match wl_get(wl, pid).map(|s| s.current.as_str()) {
        Some("null") if pid.starts_with("setting.system.") => Ok(dflt),
        Some(c) if !c.is_empty() => c
            .parse()
            .map_err(|_| format!("{pid} 的探测原值 {c:?} 不是数字, 无法做跨项约束检查")),
        _ => Ok(dflt),
    }
}

/// 跨项约束里取不到数就整组作废 —— 把 Err 直接变成"这组不合法"。
macro_rules! cross {
    ($e:expr) => {
        match $e {
            Ok(v) => v,
            Err(why) => return (false, why),
        }
    };
}

/// Python 的 `1 << 62`
const HUGE: i64 = 1 << 62;

/// 校验大模型挑的一组参数。返回 (是否合法, 原因)。
///
/// 三层检查, 任何一层不过整组作废 —— 不做「剔掉违规项后凑合跑」, 因为那样跑出来的
/// 数据对应的不是模型提出的那组参数, 证据会对不上账。
pub fn validate_candidate(cand: &[(String, String)], wl: &Whitelist) -> (bool, String) {
    if cand.is_empty() {
        return (false, "空参数组".into());
    }
    for (pid, val) in cand {
        let Some(spec) = wl_get(wl, pid) else {
            return (false, format!("{pid} 不在白名单"));
        };
        if denied(pid) || denied(&spec.path) {
            return (false, format!("{pid} 命中温控保护关键词"));
        }
        if !spec.values.contains(val) {
            let head: Vec<String> = spec.values.iter().take(6).cloned().collect();
            return (
                false,
                format!("{pid}={val} 不在合法取值表 {}...", list_repr(&head)),
            );
        }
    }

    // 跨项约束: CPU 频率 min <= max
    for (pid, v) in cand {
        if let Some(stem) = pid.strip_suffix(".scaling_min_freq") {
            let mx_id = format!("{stem}.scaling_max_freq");
            let Ok(mn) = v.parse::<i64>() else {
                return (false, format!("{pid}={v} 不是整数"));
            };
            let mx = cross!(cross_val(cand, wl, &mx_id, HUGE));
            if mn > mx {
                return (false, format!("{pid}={mn} 超过同簇 max={mx}"));
            }
        }
    }
    // GPU pwrlevel: 0 最快, 恒有 max_pwrlevel <= min_pwrlevel。
    // 缺省值一律取「最宽松」的那一端 (未知的 max 当 0, 未知的 min 当无穷大),
    // 否则 min_pwrlevel 没进白名单时会把一切合法的 max_pwrlevel 都误判成倒置。
    let has = |k: &str| cand.iter().any(|(p, _)| p == k);
    if has("gpu.max_pwrlevel") || has("gpu.min_pwrlevel") {
        let mx = cross!(cross_val(cand, wl, "gpu.max_pwrlevel", 0));
        let mn = cross!(cross_val(cand, wl, "gpu.min_pwrlevel", HUGE));
        if mx > mn {
            return (
                false,
                format!("gpu.max_pwrlevel={mx} 必须 <= gpu.min_pwrlevel={mn} (0 是最快档)"),
            );
        }
    }
    // GPU devfreq min <= max
    if has("gpu.devfreq.min_freq") || has("gpu.devfreq.max_freq") {
        let mn = cross!(cross_val(cand, wl, "gpu.devfreq.min_freq", 0));
        let mx = cross!(cross_val(cand, wl, "gpu.devfreq.max_freq", HUGE));
        if mn > mx {
            return (false, format!("gpu.devfreq.min_freq={mn} 超过 max={mx}"));
        }
    }
    // 刷新率 min <= peak
    if has("setting.system.min_refresh_rate") || has("setting.system.peak_refresh_rate") {
        let mn = cross!(cross_val_f(cand, wl, "setting.system.min_refresh_rate", 0.0));
        let pk = cross!(cross_val_f(cand, wl, "setting.system.peak_refresh_rate", 1e9));
        if mn > pk {
            return (
                false,
                format!(
                    "min_refresh_rate={} 超过 peak={}",
                    PyVal::Float(mn).py_str(),
                    PyVal::Float(pk).py_str()
                ),
            );
        }
    }
    (true, "ok".into())
}

/// 一组参数 → 设备端 plan 文件文本。
///
/// 输出按 param_id 排序, 所以同一组参数每次生成逐字节相同 (证据可比对)。
/// 写入顺序里把「放宽上限」排在「抬高下限」之前, 少踩一次内核的 min<=max 夹取;
/// knob_sysparam.sh 另有两遍写兜底。
pub fn plan_text(cand: &[(String, String)], wl: &Whitelist) -> Result<String, String> {
    let rank = |pid: &str| -> (u8, String) {
        let first = if pid.ends_with("max_freq")
            || pid.ends_with("max_pwrlevel")
            || pid.contains("peak_refresh")
        {
            0
        } else {
            1
        };
        (first, pid.to_string())
    };
    let mut ids: Vec<&(String, String)> = cand.iter().collect();
    ids.sort_by_key(|(p, _)| rank(p));
    let mut lines = vec!["# loop_v1 sysparam plan".to_string()];
    for (pid, val) in ids {
        // 白名单里没有这一项 = 调用方漏了校验。悄悄少写一行的后果是: 推到设备上的
        // plan 与 result.json 里记的 params 不是同一组, 整份证据对不上账。
        let Some(s) = wl_get(wl, pid) else {
            return Err(format!("{pid} 不在白名单, 生成不了 plan"));
        };
        lines.push(format!("{}\t{}\t{}", s.kind, s.path, val));
    }
    Ok(lines.join("\n") + "\n")
}

/// 给大模型看的白名单说明 (紧凑, 取值表超过 8 个时只给首尾与档数)。
pub fn describe_whitelist(wl: &Whitelist) -> String {
    let mut ids: Vec<&(String, Spec)> = wl.iter().collect();
    ids.sort_by(|a, b| a.0.cmp(&b.0));
    ids.iter()
        .map(|(pid, s)| {
            let v = &s.values;
            let vs = if v.len() <= 8 {
                v.join(", ")
            } else {
                format!(
                    "{} .. {} (共 {} 档: {}, ...)",
                    v[0],
                    v[v.len() - 1],
                    v.len(),
                    v[..3].join(", ")
                )
            };
            format!("- {pid} (当前 {}) 可选: {vs}", s.current)
        })
        .collect::<Vec<_>>()
        .join("\n")
}

impl Spec {
    pub fn to_pyval(&self) -> PyVal {
        pyobj! {
            "kind" => self.kind.clone(),
            "path" => self.path.clone(),
            "values" => self.values.clone(),
            "current" => self.current.clone(),
            "group" => self.group.clone(),
            "effect" => self.effect.clone(),
        }
    }
}

pub fn whitelist_to_pyval(wl: &Whitelist) -> PyVal {
    PyVal::Obj(wl.iter().map(|(k, s)| (k.clone(), s.to_pyval())).collect())
}

// ══════════════ 判定规则 ══════════════

/// n v n 精确置换检验能取到的最小双侧 p 值 = 2 / C(2n, n)。
///
/// pairs 大到 C(2n, n) 超出 double 时, Python 那边是 `2.0 / comb(...)` 抛
/// OverflowError 把整局打掉 (约 pairs >= 515)。这里返回 None 并让调用方据此把
/// reachable 判成假 —— 绝不能把它算成 0.0, 那会让"最小可达 p 值"看着好得离谱。
/// 实际用的 pairs 是个位数, 这条只为别让一个荒唐的入参把进程卡死或骗过判据。
fn min_reachable_p(pairs: i64) -> Option<f64> {
    if pairs < 2 {
        return None;
    }
    // 公式只在 loopstat 一处; 溢出时它返回 0.0, 这里换成 None
    Some(crate::loopstat::min_two_sided_p(pairs as usize)).filter(|p| *p > 0.0)
}

/// 判定指标的方向: `Some(true)` = 越小越好。不在判定表里的指标返回 `None`。
pub(crate) fn lower_is_better(metric: &str) -> Option<bool> {
    PRIMARY_METRICS
        .iter()
        .find(|(m, _)| *m == metric)
        .map(|(_, d)| *d == "lower")
}

fn active_metrics(power_available: bool) -> Vec<(&'static str, &'static str)> {
    PRIMARY_METRICS
        .into_iter()
        .filter(|(m, _)| power_available || *m != "power_w_mean")
        .collect()
}

/// 落盘用的规则快照。autoloop 在采第一组候选数据之前写它。
///
/// `temp_cap_c` / `pairs` 原样回写, 所以收的是 [`PyVal`] 而不是 f64/i64 ——
/// 调用方传 int 46 就该写回 `46`, 传 float 46.0 就该写回 `46.0`。
pub fn rule_doc(temp_cap_c: &PyVal, pairs: &PyVal, power_available: bool, power_note: &str) -> PyVal {
    let metrics = active_metrics(power_available);
    let k = metrics.len();
    let minp = min_reachable_p(pairs.as_i64().unwrap_or(0));
    pyobj! {
        "version" => RULE_VERSION,
        "alpha_regression" => ALPHA,
        "alpha_win_bonferroni" => if k > 0 { PyVal::Float(py_round(ALPHA / k as f64, 6)) } else { PyVal::Null },
        "n_metrics" => k,
        "metrics" => PyVal::List(metrics.iter().map(|(m, d)| pyobj!{ "id" => *m, "better" => *d }).collect()),
        "pairs_per_candidate" => pairs.clone(),
        "min_reachable_p" => minp,
        // 算不出最小可达 p (对数太少, 或大到 C(2n,n) 溢出) 就是"够不着"
        "reachable" => minp.unwrap_or(1.0) < if k > 0 { ALPHA / k as f64 } else { 0.0 },
        "temp_cap_c" => temp_cap_c.clone(),
        "power_in_verdict" => power_available,
        "power_note" => power_note,
        "keep_condition" => "至少一个指标显著改善 (p < alpha_win) 且没有任何指标显著变差 (p < 0.05)",
        "abort_conditions" => vec!["旋钮未全部生效", "SoC 结温超过 temp_cap_c", "轮末快照与轮前不一致"],
    }
}

// ══════════════ 设备遥测解析 ══════════════

/// `sample_env.sh` 输出 → 功耗与温度统计。
///
/// 功耗在本机有两个坑, 都是实测踩出来的, 所以这里**不做任何美化**, 原始量与折算量
/// 一起落盘, 由上层决定用不用:
///
/// 1. **充电态量不到整机功耗**。插着 USB 时 USB 输入功率里有约一半是在给电池充电,
///    且充电电流随电量单调衰减 —— 这个衰减会被当成"功耗随时间下降"混进 A/B 比较。
///    所以 `battery_charging` 为真时 `power_w_mean` 一律 None。
/// 2. **`battery/power_now` 在本机单位是错的**, 读出过 777W。所以不像
///    `hwcond.rs::PowerSample::watt` 那样优先用它: 这里以 |V x I| 为准, `power_now`
///    只作为原始值记录, 并标一个 plausible 位 —— 不合理的数不进任何均值。
///
/// 另外把 `power_rail` / `battery_status` / `current_now` / `voltage_now` 原样留下,
/// 停充测量做好之后可以回头核这批数据。
/// 旁路判定用的「电池不进不出」门限 (µA), 与 charge_suspend.sh 的 BYPASS_IDLE_UA 一致。
pub const BYPASS_IDLE_UA: f64 = 50_000.0;

/// 一组 A/B (或一批基线) 的功耗能不能进判定: 每一轮都量到了可信功耗, 且**同一种口径**
/// (全是电池放电, 或全是 USB 旁路)。两种口径混在一组里 = 这组功耗无效 —— USB 轨含充电器
/// 转换损耗, 和电池轨不是同一个量。旧证据没有 `power_valid` / `power_source` 两个键时,
/// 按旧口径 `on_battery` 认。空集为真 (调用方自己管「一轮都没有」)。
pub fn power_group_ok<'a>(envs: impl IntoIterator<Item = &'a serde_json::Value>) -> bool {
    let mut src: Option<String> = None;
    for e in envs {
        let valid = e["power_valid"].as_bool().unwrap_or_else(|| e["on_battery"].as_bool().unwrap_or(false));
        if !valid {
            return false;
        }
        let s = e["power_source"].as_str().unwrap_or("battery").to_string();
        match &src {
            None => src = Some(s),
            Some(x) if *x != s => return false,
            _ => {}
        }
    }
    true
}

/// 一组 ABBA 的功耗按「对」取舍的结果 (审计 #5)。
///
/// 以前 [`power_group_ok`] 一臂不合格就整组 false, 功耗静默退出判定; 现在只剔除那一对
/// (同一轮的对照臂与候选臂), 剩下的对照常进判定, 被剔的对写进回包。
#[derive(Debug, Clone, Default, PartialEq)]
pub struct PowerPairs {
    /// 每一臂进判定的功耗 (W), 与传入顺序一一对应; None = 这一臂被剔除或没量到
    pub ctrl: Vec<Option<f64>>,
    pub knob: Vec<Option<f64>>,
    /// 这组用的口径 (battery / usb_plus_battery / ...); 一臂都没有时 None
    pub rail: Option<String>,
    /// 各臂口径不一致, 统一改用两轨同刻相加 (power_mixed_w_mean)
    pub unified_to_mixed: bool,
    /// 被剔除的对: (轮次, 从 1 起; 原因)
    pub dropped: Vec<(usize, String)>,
    /// 有臂判出供电不足 (混合供电)
    pub supply_short: bool,
}

impl PowerPairs {
    pub fn kept(&self) -> (usize, usize) {
        (self.ctrl.iter().flatten().count(), self.knob.iter().flatten().count())
    }

    /// 两臂各至少 2 个有效值, 功耗才进判定 (置换检验最少要 2 对 2)。
    pub fn usable(&self) -> bool {
        let (a, b) = self.kept();
        a >= 2 && b >= 2
    }

    /// 人能读的说明: 剔了哪几对、为什么; 进不了判定时为什么。都没有时 None。
    pub fn note(&self) -> Option<String> {
        let mut parts: Vec<String> = Vec::new();
        if self.unified_to_mixed {
            parts.push("各臂供电口径不一致, 统一按 USB 与电池两轨同刻相加计".into());
        }
        if self.supply_short {
            parts.push("供电不足: 有臂 USB 输入不够或中途断开, 电池在补电, 已按两轨同刻相加计".into());
        }
        if !self.dropped.is_empty() {
            let list: Vec<String> = self.dropped.iter().map(|(r, why)| format!("第 {r} 轮 ({why})")).collect();
            parts.push(format!("剔除 {} 对: {}", self.dropped.len(), list.join("; ")));
        }
        if !self.usable() {
            let (a, b) = self.kept();
            parts.push(format!("功耗不进判定: 剔除后对照臂 {a} 个、候选臂 {b} 个有效值, 每臂至少要 2 个"));
        }
        (!parts.is_empty()).then(|| parts.join("。"))
    }

    pub fn to_json(&self) -> serde_json::Value {
        let (a, b) = self.kept();
        serde_json::json!({
            "rail": self.rail,
            "in_verdict": self.usable(),
            "kept_ctrl": a,
            "kept_knob": b,
            "unified_to_mixed": self.unified_to_mixed,
            "supply_short": self.supply_short,
            "dropped": self.dropped.iter().map(|(r, why)| serde_json::json!({"round": r, "reason": why})).collect::<Vec<_>>(),
            "note": self.note(),
        })
    }
}

/// 一臂 env 的功耗: (是否可信, 口径, 值, 两轨相加的值, 不可信的原因)。
fn arm_power(e: &serde_json::Value) -> (bool, String, Option<f64>, Option<f64>, String) {
    let valid = e["power_valid"].as_bool().unwrap_or_else(|| e["on_battery"].as_bool().unwrap_or(false));
    let w = e["power_w_mean"].as_f64();
    let rail = e["power_source"].as_str().unwrap_or("battery").to_string();
    let why = e["power_usable_reason"].as_str().unwrap_or("功耗没量到").to_string();
    (valid && w.is_some(), rail, w, e["power_mixed_w_mean"].as_f64(), why)
}

/// 按对取舍功耗。`ctrl[i]` 与 `knob[i]` 是同一轮的两臂; 多出来的单臂 (中途停下) 自成一组,
/// 合格就留。
///
/// 1. 每臂先看自己可不可信 (`power_valid`, 旧证据按 `on_battery`)。
/// 2. 可信臂的口径不止一种时: 每臂都有 `power_mixed_w_mean` 就统一改用它 (两轨同刻相加,
///    电池放电与旁路在这个口径下是同一个量); 否则取臂数最多的口径, 其余口径的臂当不可信。
/// 3. 一对里任一臂不可信, 剔除这一对 (两臂都不进), 原因写进 `dropped`。
pub fn power_pairs<'a>(ctrl: &[&'a serde_json::Value], knob: &[&'a serde_json::Value]) -> PowerPairs {
    let (nc, nk) = (ctrl.len(), knob.len());
    let n = nc.max(nk);
    let c: Vec<Option<&serde_json::Value>> = (0..n).map(|i| ctrl.get(i).copied()).collect();
    let k: Vec<Option<&serde_json::Value>> = (0..n).map(|i| knob.get(i).copied()).collect();
    let rounds: Vec<usize> = (1..=n).collect();
    let mut out = power_pairs_slots(&c, &k, &rounds);
    out.ctrl.truncate(nc);
    out.knob.truncate(nk);
    out
}

/// [`power_pairs`] 的本体, 按「轮」对齐: 第 j 个槽是第 `round_no[j]` 轮, `ctrl[j]` / `knob[j]`
/// 是这一轮的对照臂 / 候选臂, 缺的一臂为 None (中途停下、或候选臂没进统计)。
/// 只有一臂的轮自成一组, 合格就留; 两臂都在时任一臂不可信就剔除这一对。
/// 回的 `ctrl` / `knob` 按槽排列 (长度 = 槽数), `dropped` 里的轮次是 `round_no` 的值。
pub fn power_pairs_slots(
    ctrl: &[Option<&serde_json::Value>],
    knob: &[Option<&serde_json::Value>],
    round_no: &[usize],
) -> PowerPairs {
    let n = ctrl.len().max(knob.len());
    fn slot<'a>(v: &[Option<&'a serde_json::Value>], j: usize) -> Option<&'a serde_json::Value> {
        v.get(j).copied().flatten()
    }
    let present: Vec<&serde_json::Value> = (0..n).flat_map(|j| [slot(ctrl, j), slot(knob, j)]).flatten().collect();
    let supply_short = present.iter().any(|e| e["supply_short"].as_bool() == Some(true) && arm_power(e).0);
    let arms: Vec<_> = present.iter().map(|e| arm_power(e)).collect();
    let mut rails: Vec<(String, usize)> = Vec::new();
    for (ok, r, ..) in &arms {
        if *ok {
            match rails.iter_mut().find(|(x, _)| x == r) {
                Some((_, n)) => *n += 1,
                None => rails.push((r.clone(), 1)),
            }
        }
    }
    let unified_to_mixed = rails.len() > 1 && arms.iter().filter(|a| a.0).all(|a| a.3.is_some());
    let main_rail: Option<String> = if unified_to_mixed {
        Some("usb_plus_battery".into())
    } else {
        // 臂数最多的口径; 平手取先出现的
        rails.iter().fold(None::<&(String, usize)>, |best, x| match best {
            Some(b) if b.1 >= x.1 => Some(b),
            _ => Some(x),
        }).map(|(r, _)| r.clone())
    };
    // 每臂: Ok(值) 或 Err(原因)
    let judge = |e: &serde_json::Value| -> Result<f64, String> {
        let a = arm_power(e);
        if !a.0 {
            return Err(a.4.clone());
        }
        if unified_to_mixed {
            return a.3.ok_or_else(|| "缺两轨相加的值".into());
        }
        match &main_rail {
            Some(m) if *m != a.1 => Err(format!("口径 {} 与本组主口径 {m} 不同, 不能相减", a.1)),
            _ => a.2.ok_or_else(|| a.4.clone()),
        }
    };
    let mut out = PowerPairs {
        ctrl: vec![None; n],
        knob: vec![None; n],
        rail: main_rail.clone(),
        unified_to_mixed,
        dropped: Vec::new(),
        supply_short,
    };
    for j in 0..n {
        let c = slot(ctrl, j).map(&judge);
        let k = slot(knob, j).map(&judge);
        match (c, k) {
            (Some(Ok(a)), Some(Ok(b))) => {
                out.ctrl[j] = Some(a);
                out.knob[j] = Some(b);
            }
            (Some(Ok(a)), None) => out.ctrl[j] = Some(a),
            (None, Some(Ok(b))) => out.knob[j] = Some(b),
            (None, None) => {}
            (c, k) => {
                let mut why: Vec<String> = Vec::new();
                if let Some(Err(e)) = c {
                    why.push(format!("对照臂: {e}"));
                }
                if let Some(Err(e)) = k {
                    why.push(format!("候选臂: {e}"));
                }
                out.dropped.push((round_no.get(j).copied().unwrap_or(j + 1), why.join(" / ")));
            }
        }
    }
    if out.ctrl.iter().chain(out.knob.iter()).all(|v| v.is_none()) && main_rail.is_none() {
        out.rail = None;
    }
    out
}

/// charge_suspend.sh 输出里的开跑前 USB 输入上限检查 (`CHARGE_USB_LIMIT` / `CHARGE_SUPPLY_LOW` 行)
/// → 一句话。供电不足时以「供电不足」开头; 脚本没做这项检查 (旧脚本、非旁路) 时 None。
pub fn usb_supply_note(charge_log: &str) -> Option<String> {
    let low = charge_log.lines().find_map(|l| l.trim().strip_prefix("CHARGE_SUPPLY_LOW").map(|r| r.trim().to_string()));
    if let Some(r) = low {
        return Some(if r.starts_with("供电不足") { r } else { format!("供电不足: {r}") });
    }
    charge_log
        .lines()
        .find_map(|l| l.trim().strip_prefix("CHARGE_USB_LIMIT").map(|r| format!("USB 输入上限检查: {}", r.trim())))
}

/// 功耗进不了判定的原因 —— 直接写进回包, 不让人猜。
///
/// 两种情形要分开: 两臂轨道不一致 (无法相减) 与 两臂都没量到。
pub fn power_group_note<'a>(envs: impl IntoIterator<Item = &'a serde_json::Value>) -> Option<String> {
    let mut src: Option<String> = None;
    let mut n = 0usize;
    for e in envs {
        n += 1;
        let valid = e["power_valid"].as_bool().unwrap_or_else(|| e["on_battery"].as_bool().unwrap_or(false));
        if !valid {
            let why = e["power_usable_reason"].as_str().unwrap_or("功耗没量到");
            return Some(format!("功耗不进判定: {why}"));
        }
        let s = e["power_source"].as_str().unwrap_or("battery").to_string();
        match &src {
            None => src = Some(s),
            Some(x) if *x != s => {
                return Some(format!("功耗不进判定: 两臂轨道不一致 ({x} vs {s}), 不能相减"));
            }
            _ => {}
        }
    }
    if n == 0 {
        return Some("功耗不进判定: 这一轮没有采到环境数据".into());
    }
    None
}

/// 这组数据能不能跨任务比功耗。只有全程电池放电态可以。
///
/// USB 旁路 (停充后拿 USB 轨 |V x I|) 含充电器转换损耗, 它的绝对值不和电池轨同口径;
/// 同一轮的两臂在同一个轨上相减仍然成立, 但拿它去和别的任务的数比就会把损耗算进效果里。
pub fn power_cross_run_comparable<'a>(envs: impl IntoIterator<Item = &'a serde_json::Value>) -> bool {
    let mut any = false;
    for e in envs {
        any = true;
        let ok = match e["power_cross_run_comparable"].as_bool() {
            Some(v) => v,
            // 旧证据没有这个字段: 退回「放电态且量到了」这个同口径判据
            None => {
                e["on_battery"].as_bool() == Some(true)
                    && e["power_w_mean"].as_f64().is_some()
            }
        };
        if !ok {
            return false;
        }
    }
    any
}

#[derive(Debug)]
pub(crate) struct SampleWindow {
    pub complete: bool,
    pub duration_s: Option<f64>,
    pub interval_s: Option<f64>,
    pub first_uptime: Option<f64>,
    pub last_uptime: Option<f64>,
    /// v2 采样器的功耗快循环间隔 (`power_interval=`); v1 为 None
    pub power_interval_s: Option<f64>,
}

/// Done marker, declared protocol and every uptime are required; one lucky row is not a window.
///
/// 两版采样器 (loop_v1/auto/sample_env.sh):
/// - v1: 只有 ENV 行, 按「次数 × 间隔」走满 dur, 行数必须恰为 ceil(dur/interval)。
/// - v2 (ruler fix-C): ENV 行按 /proc/uptime 每 interval 一条, 其间 PWR 快循环行 (≥5 Hz);
///   给了停止标志时在 ftrace 采集结束后提前停 (`#stop_seen`), 主机端再按 trace 采集窗截取。
///   所以 v2 的首末时刻按全部功耗行 (ENV+PWR) 算, 相邻功耗行不超过 2 × interval,
///   没提前停时跨度要走满 dur; 提前停时跨度由 [`trace_window_covered`] 对采集窗核对。
pub(crate) fn sample_window(text:&str)->SampleWindow {
    let header=text.lines().find(|l|l.starts_with("#sample_env v1 ")||l.starts_with("#sample_env v2 "));
    let v2=header.is_some_and(|l|l.starts_with("#sample_env v2 "));
    let field=|key:&str|header
        .and_then(|l|l.split_whitespace().find_map(|t|t.strip_prefix(key)))
        .and_then(|x|x.parse::<f64>().ok()).filter(|x|x.is_finite() && *x>0.0);
    let duration_s=field("dur=");let interval_s=field("interval=");
    let power_interval_s=if v2 {field("power_interval=")} else {None};
    let done=text.lines().rfind(|l|!l.trim().is_empty())==Some("#sample_env done");
    let parse_times=|rows:&[&str]|->Vec<f64>{rows.iter().filter_map(|l|l.split_whitespace().nth(1)?.parse::<f64>().ok())
        .filter(|x|x.is_finite() && *x>=0.0).collect()};
    if v2 {
        let env_rows:Vec<&str>=text.lines().filter(|l|l.starts_with("ENV ")).collect();
        let rows:Vec<&str>=text.lines().filter(|l|l.starts_with("ENV ")||l.starts_with("PWR ")).collect();
        let times=parse_times(&rows);
        let first_uptime=times.first().copied();let last_uptime=times.last().copied();
        let stopped=text.lines().any(|l|l.starts_with("#stop_seen"));
        let complete=duration_s.zip(interval_s).is_some_and(|(dur,ivl)|
            ivl<=dur && rows.len()>=2 && !env_rows.is_empty() && times.len()==rows.len() && done
            && times.windows(2).all(|w|w[1]-w[0]>=0.0 && w[1]-w[0]<=ivl*2.0)
            && (stopped || last_uptime.zip(first_uptime).is_some_and(|(b,a)|b-a>=dur-ivl-0.1)));
        return SampleWindow{complete,duration_s,interval_s,first_uptime,last_uptime,power_interval_s};
    }
    let rows:Vec<&str>=text.lines().filter(|l|l.starts_with("ENV ")).collect();
    let times=parse_times(&rows);
    let first_uptime=times.first().copied();let last_uptime=times.last().copied();
    let complete=duration_s.zip(interval_s).is_some_and(|(dur,ivl)|
        ivl<=dur && rows.len()>=2 && rows.len()==(dur/ivl).ceil() as usize
        && times.len()==rows.len() && done && times.windows(2).all(|w|w[1]-w[0]>=ivl*0.5 && w[1]-w[0]<=ivl*2.0)
        && last_uptime.zip(first_uptime).is_some_and(|(b,a)|b-a>=dur-ivl-0.1));
    SampleWindow{complete,duration_s,interval_s,first_uptime,last_uptime,power_interval_s}
}

/// Capture metadata shares /proc/uptime with ENV; event trace_clock is not assumed.
fn capture_window(trace: &str, expected_capture_s: u64) -> Option<(f64, f64)> {
    let mut lines = trace.lines().filter(|l| l.starts_with("#loop_v1_meta ") && l.contains("capture_start_uptime="));
    let line = lines.next()?;
    if lines.next().is_some() { return None; }
    let field = |key: &str| {
        let mut values = line.split_whitespace().filter_map(|t| t.strip_prefix(key));
        let value = values.next()?.parse::<f64>().ok().filter(|x| x.is_finite())?;
        values.next().is_none().then_some(value)
    };
    let (start,end,dur) = (field("capture_start_uptime=")?,field("capture_end_uptime=")?,field("duration_req=")?);
    (start >= 0.0 && end > start && dur > 0.0 && dur == expected_capture_s as f64
        && end-start >= dur-0.1 && end-start <= dur+5.0).then_some((start,end))
}
/// v2 采样器见到停止标志 (采集结束后才写出的 trace 文件) 就停, 最后一条功耗行与采集结束之间
/// 最多差一个快循环周期加读节点的耗时, 所以末端放宽 2 × power_interval + 0.1 s。
pub(crate) fn trace_window_covered(window:&SampleWindow,trace:&str,expected_capture_s:u64)->bool {
    let tail_slack=window.power_interval_s.map_or(0.0,|p|2.0*p+0.1);
    match (window.first_uptime,window.last_uptime,capture_window(trace,expected_capture_s)) {
        (Some(a),Some(b),Some((start,end))) => window.complete && a<=start && b+tail_slack>=end,
        _=>false,
    }
}

/// The same qualified V*I sequence supplies both capture and group integrals.
fn battery_samples(env: &str) -> Result<Vec<(f64, f64)>, String> {
    let stats = env_stats(env);
    if stats.get("power_window_verified") != Some(&PyVal::Bool(true))
        || stats.get("on_battery") != Some(&PyVal::Bool(true))
        || stats.get("power_source") != Some(&PyVal::Str("battery".into())) {
        return Err("whole sampling window lacks verified battery discharge".into());
    }
    let v2 = env.lines().any(|l| l.starts_with("#sample_env v2 "));
    let samples = env.lines().filter(|l| l.starts_with("ENV ") || (v2 && l.starts_with("PWR "))).map(|line| {
        let fields = line.split_whitespace().collect::<Vec<_>>();
        if fields.len()!=if fields[0]=="ENV" {9} else {7} {return Err("invalid battery sample row".to_owned());}
        let number = |index: usize| fields[index].parse::<f64>().ok().filter(|v| v.is_finite()).ok_or_else(||"invalid battery sample value".to_owned());
        let (time,voltage,current)=(number(1)?,number(2)?,number(3)?);
        let power=(voltage / 1e6 * current / 1e6).abs();
        if time<0.0 || voltage<=0.0 || current>=0.0 || !(crate::hwcond::POWER_MIN_W..=crate::hwcond::POWER_MAX_W).contains(&power) {
            return Err("battery sample is outside the discharge power range".into());
        }
        Ok((time,power))
    }).collect::<Result<Vec<_>,String>>()?;
    if samples.len()<2 || samples.windows(2).any(|w|w[1].0<=w[0].0) {
        return Err("battery sample times must be strictly increasing".into());
    }
    Ok(samples)
}

const BATTERY_INTEGRATION_METHOD: &str = "battery V*I; linear boundary interpolation and trapezoidal integration";

fn integrate_battery(samples: &[(f64,f64)], start: f64, end: f64) -> Result<serde_json::Value,String> {
    if !start.is_finite() || !end.is_finite() || start<0.0 || end<=start || !(end-start).is_finite() {
        return Err("energy window has no finite positive duration".into());
    }
    // Sampling's tail slack is not permission to extrapolate an integral.
    if samples.first().is_none_or(|s|s.0>start) || samples.last().is_none_or(|s|s.0<end) {
        return Err("valid battery samples do not bracket the energy window".into());
    }
    let mut energy=0.0;
    for pair in samples.windows(2) {
        let ((t0,p0),(t1,p1))=(pair[0],pair[1]);
        let (left,right)=(start.max(t0),end.min(t1));
        if right<=left {continue;}
        let power=|time| p0+(p1-p0)*(time-t0)/(t1-t0);
        energy+=(power(left)+power(right))*0.5*(right-left);
    }
    if !energy.is_finite() || !(energy/(end-start)).is_finite() {
        return Err("battery energy arithmetic is not finite".into());
    }
    Ok(serde_json::json!({"available":true,"estimated":true,"reason":null,
        "energy_j":energy,"power_w_mean":energy/(end-start),"rail":"battery",
        "method":BATTERY_INTEGRATION_METHOD,
        "window":{"clock":"uptime","start":start,"end":end},"samples":samples.len()}))
}

pub(crate) fn battery_energy_in_capture(env: &str, trace: &str, seconds: u64) -> Result<serde_json::Value, String> {
    let samples = battery_samples(env)?;
    let (start,end) = capture_window(trace,seconds).ok_or("capture uptime metadata is unavailable or contradictory")?;
    integrate_battery(&samples,start,end)
}

/// These are recorded clock/loss observations, not a target/context witness.
fn submit_clock_and_loss(trace: &str, parsed: &crate::looptrace::Parsed) -> Result<(),String> {
    let clocks = trace.lines().filter(|l|l.starts_with('#')).flat_map(str::split_whitespace)
        .filter_map(|t|t.strip_prefix("trace_clock=")).collect::<Vec<_>>();
    if clocks != ["boot"] {
        return Err("GPU-submit energy requires one explicit boot trace clock observation".into());
    }
    let mut cpus = std::collections::BTreeSet::new();
    for line in trace.lines().filter_map(|l|l.strip_prefix("# phonefarm cpu")) {
        let fields = line.split_whitespace().collect::<Vec<_>>();
        if fields.len()!=3 || fields[0].is_empty() || !fields[0].bytes().all(|b|b.is_ascii_digit())
            || fields[1]!="overrun:" || fields[2]!="0" {
            return Err("GPU-submit trace has missing, invalid or nonzero per-CPU overrun evidence".into());
        }
        let cpu=fields[0].parse::<u32>().map_err(|_|"invalid per-CPU overrun identity")?;
        if !cpus.insert(cpu) { return Err("duplicate per-CPU overrun observation".into()); }
    }
    if cpus.is_empty() {
        return Err("GPU-submit trace lacks per-CPU overrun observations".into());
    }
    if parsed.submit_cpus.iter().any(|cpu|!cpus.contains(cpu)) {
        return Err("GPU-submit trace lacks overrun observations for a selected submit CPU".into());
    }
    Ok(())
}

pub(crate) fn battery_energy_in_submit_groups(
    env: &str, trace: &str, seconds: u64, parsed: &crate::looptrace::Parsed,
) -> Result<serde_json::Value,String> {
    submit_clock_and_loss(trace,parsed)?;
    let capture = capture_window(trace,seconds).ok_or("capture uptime metadata is unavailable or contradictory")?;
    let groups = parsed.complete_group_window(capture)?;
    let samples = battery_samples(env)?;
    let mut value = integrate_battery(&samples,groups.start,groups.end)?;
    let per_group = value["energy_j"].as_f64().unwrap() / groups.group_count as f64 * 1000.0;
    if !per_group.is_finite() { return Err("battery energy per GPU-submit group is not finite".into()); }
    let fields = serde_json::json!({"scope":"whole_device_battery_over_gpu_submit_group_window",
        "group_count":groups.group_count,"submits_per_group":groups.submits_per_group,
        "discarded_head_groups":groups.discarded_head_groups,"discarded_tail_groups":groups.discarded_tail_groups,
        "trailing_incomplete_intervals":groups.trailing_incomplete_intervals,
        "energy_per_group_mj":per_group,"render_comm":parsed.render_comm,
        "grouping":parsed.submit_grouping,"filtering_basis":parsed.submit_filtering,
        "trace_clock":"boot","loss_observation":"per_cpu_overrun_zero",
        "target_ownership_verified":false,"display_verified":false});
    value.as_object_mut().unwrap().extend(fields.as_object().unwrap().clone());
    Ok(value)
}

pub fn env_stats(text: &str) -> PyVal {
    env_stats_impl(text, None)
}

/// [`env_stats`] 的取窗版 (审计 #3): 功耗样本 (ENV/PWR 行) 只取落在 trace 采集窗
/// `[capture_start_uptime, capture_end_uptime]` 里的, 并且整段的第一个功耗样本一律丢掉
/// (采样器刚起、`su` + shell 启动本身在耗电, 实测每臂第一个点都偏高, msaa1 是 7.18–7.20 W)。
/// GPU 行同样截窗 (它是功耗的辅助证据, 空闲段的低频会把均值拉低)。
/// 结温不截窗: 它是作废线 (超温上限), 整段都要看。
///
/// `window` 为 None (trace 里没有 capture 时间) 时只丢第一个点, 并在 `power_window` 里写明没截窗。
/// 输出比 [`env_stats`] 多几个键, 都追加在末尾: `power_window`、`n_power_samples_total`、
/// `n_power_samples_used`、`power_sample_hz`。
pub fn env_stats_window(text: &str, window: Option<(f64, f64)>) -> PyVal {
    env_stats_impl(text, Some(window))
}

/// 从 trace 文本头部的 `#loop_v1_meta capture_start_uptime=… capture_end_uptime=…` 取采集窗。
/// 这一行由 ftrace_capture.sh 写在文件最前面, 所以读到第一行非 `#` 就停, 不扫整个 trace。
pub fn capture_window_from_trace(trace: &str) -> Option<(f64, f64)> {
    let (mut a, mut b) = (None, None);
    for line in trace.lines() {
        if !line.starts_with('#') {
            break;
        }
        if let Some(rest) = line.strip_prefix("#loop_v1_meta") {
            for tok in rest.split_whitespace() {
                match tok.split_once('=') {
                    Some(("capture_start_uptime", v)) => a = v.parse::<f64>().ok(),
                    Some(("capture_end_uptime", v)) => b = v.parse::<f64>().ok(),
                    _ => {}
                }
            }
        }
    }
    match (a, b) {
        (Some(a), Some(b)) if b > a => Some((a, b)),
        _ => None,
    }
}

/// `clip`: None = 旧口径 (全部样本, 不丢第一个点, 输出与以前逐字节相同);
/// Some(w) = 取窗口径 (见 [`env_stats_window`])。
fn env_stats_impl(text: &str, clip: Option<Option<(f64, f64)>>) -> PyVal {
    let in_window = |up: Option<f64>| -> bool {
        match clip {
            Some(Some((a, b))) => up.is_some_and(|u| a <= u && u <= b),
            _ => true,
        }
    };
    let mut first_power_seen = false;
    let (mut n_power_total, mut n_power_used) = (0usize, 0usize);
    // 解析不了的功耗行 (ENV/PWR), 整窗证据要求为 0
    let mut n_malformed = 0usize;
    let (mut vi_watts, mut now_watts, mut usb_watts) = (vec![], vec![], vec![]);
    // 混合口径: 同一条 ENV 样本 (同一时刻) 的 USB 轨 |V x I| + 电池放电功率 (充电为负)。
    // usb_all_watts 含 USB 读数为 0 的样本, 用来判「USB 有没有在出力」。
    let (mut mixed_watts, mut usb_all_watts, mut batt_out_watts): (Vec<f64>, Vec<f64>, Vec<f64>) =
        (vec![], vec![], vec![]);
    let (mut currents, mut voltages): (Vec<f64>, Vec<f64>) = (vec![], vec![]);
    let mut temps: Vec<f64> = vec![];
    let mut status = String::new();
    let mut status_after = String::new();
    let mut capacity: Option<String> = None;
    let mut charge_suspended = false;
    let mut charge_mode = String::new();
    let mut fan_state: Option<String> = None;
    let mut n = 0usize;
    // GPU <uptime> <max_pwrlevel> <gpuclk Hz>: 厂商性能服务会改 GPU 上限, 逐样本核对
    let mut gpu_levels: Vec<i64> = vec![];
    let mut gpu_mhz: Vec<f64> = vec![];

    for line in text.lines() {
        let line = line.trim();
        if let Some(v) = line.strip_prefix("#battery_status=") {
            status = v.trim().to_string();
            continue;
        }
        if let Some(v) = line.strip_prefix("#battery_status_after=") {
            status_after = v.trim().to_string();
            continue;
        }
        if let Some(v) = line.strip_prefix("#battery_capacity=") {
            let v = v.trim();
            capacity = (!v.is_empty()).then(|| v.to_string());
            continue;
        }
        if let Some(v) = line.strip_prefix("#charge_suspended=") {
            charge_suspended = v.trim() == "yes";
            continue;
        }
        if let Some(v) = line.strip_prefix("#charge_mode=") {
            charge_mode = v.trim().to_string();
            continue;
        }
        if let Some(v) = line.strip_prefix("#fan_state=") {
            // 主动散热风扇自己耗电, 会进功耗读数。逐轮记下来, 好复核同一组对照的
            // 两臂是不是同一风扇状态 —— 风扇开关前后的数据不能混着比。
            let v = v.trim();
            fan_state = (!v.is_empty()).then(|| v.to_string());
            continue;
        }
        if let Some(rest) = line.strip_prefix("GPU ") {
            let p: Vec<&str> = rest.split_whitespace().collect();
            if clip.is_some() && !in_window(p.first().and_then(|x| x.parse::<f64>().ok())) {
                continue;
            }
            if let (Some(Ok(l)), Some(Ok(hz))) =
                (p.get(1).map(|x| x.parse::<i64>()), p.get(2).map(|x| x.parse::<i64>()))
            {
                if !gpu_levels.contains(&l) {
                    gpu_levels.push(l);
                }
                if hz > 0 {
                    gpu_mhz.push(hz as f64 / 1e6);
                }
            }
            continue;
        }
        // PWR 行 (sample_env v2 的快循环): 只有功耗字段, 没有结温
        let is_env = line.starts_with("ENV ");
        if !is_env && !line.starts_with("PWR ") {
            continue;
        }
        // ENV <uptime> <batt_uv> <batt_ua> <batt_uw|NA> <usb_uv> <usb_ua> <热区> <毫摄氏度>
        // PWR <uptime> <batt_uv> <batt_ua> <batt_uw|NA> <usb_uv> <usb_ua>
        let parts: Vec<&str> = line.split_whitespace().collect();
        // 整窗证据 (main): 窗内每一条功耗行都要解析得了, 坏行不能悄悄跳过。
        // 计数在解析之前 —— 解析失败的样本照样算采到过 (与旧版一致)
        if is_env {
            n += 1;
        }
        if parts.len() != if is_env { 9 } else { 7 } {
            n_malformed += 1;
            continue;
        }
        let Ok(uptime) = parts[1].parse::<f64>() else {
            n_malformed += 1;
            continue;
        };
        if is_env && clip.is_some() {
            // 结温不截窗, 整段都看 (超温是作废线)
            if let Ok(t) = parts[8].parse::<i64>() {
                if 0 < t && t < 100_000 {
                    temps.push(t as f64 / 1000.0);
                }
            }
        }
        // 取窗口径 (ruler fix-C): 丢掉整段第一个功耗样本, 窗外的不进功耗
        if clip.is_some() {
            n_power_total += 1;
            let first = !first_power_seen;
            first_power_seen = true;
            if first || !in_window(Some(uptime)) {
                continue;
            }
        }
        let (Ok(bv), Ok(bi)) = (parts[2].parse::<i64>(), parts[3].parse::<i64>()) else {
            n_malformed += 1;
            continue;
        };
        let bp = if parts[4] == "NA" {
            None
        } else {
            match parts[4].parse::<i64>() {
                Ok(v) => Some(v),
                Err(_) => {
                    n_malformed += 1;
                    continue;
                }
            }
        };
        let (Ok(uv), Ok(ui)) = (parts[5].parse::<i64>(), parts[6].parse::<i64>()) else {
            n_malformed += 1;
            continue;
        };
        // 旧口径: 结温在功耗字段之后解析, 解析不了整行作废 (与以前一致)
        let t = if clip.is_none() && is_env {
            let Ok(t) = parts[8].parse::<i64>() else {
                n_malformed += 1;
                continue;
            };
            Some(t)
        } else {
            None
        };
        if clip.is_some() {
            n_power_used += 1;
        }

        if bv != 0 {
            voltages.push(bv as f64);
        }
        currents.push(bi as f64);
        if bv != 0 && bi != 0 {
            // Python 的 `bv / 1e6 * bi / 1e6` 是从左往右算的, 即 ((bv/1e6) * bi) / 1e6。
            // 写成 (bv/1e6) * (bi/1e6) 中间舍入位置就不一样, 实测 4 位小数上会差一个数。
            vi_watts.push((bv as f64 / 1e6 * bi as f64 / 1e6).abs());
        }
        if let Some(bp) = bp {
            if bp != 0 {
                now_watts.push((bp as f64).abs() / 1e6);
            }
        }
        if uv != 0 && ui != 0 {
            usb_watts.push((uv as f64 / 1e6 * ui as f64 / 1e6).abs());
        }
        if bv != 0 {
            let usb_w = (uv as f64 / 1e6 * ui as f64 / 1e6).abs();
            // 本机约定电池电流正 = 充、负 = 放: 放电功率为正, 充电为负
            let batt_out = -(bv as f64 / 1e6 * bi as f64 / 1e6);
            usb_all_watts.push(usb_w);
            batt_out_watts.push(batt_out);
            if usb_w + batt_out > 0.0 && (usb_w != 0.0 || bi != 0) {
                mixed_watts.push(usb_w + batt_out);
            }
        }
        if let Some(t) = t {
            if 0 < t && t < 100_000 {
                temps.push(t as f64 / 1000.0);
            }
        }
    }

    let avg = |xs: &[f64]| -> Option<f64> {
        (!xs.is_empty()).then(|| py_round(py_sum(xs.iter().copied()) / xs.len() as f64, 4))
    };

    // 供电判定与 hwcond.rs 的单轨通路是同一个函数 (hwcond::classify_supply, 审计 #15)。
    // on_battery 仍是「电池确实在放电」这一事实 (status 不是 Charging、不是满电空闲、电流为负),
    // 两个判据都要看: 停充之后有的内核仍写 "Not charging"; current_now 在充放平衡时会过零。
    let cur_mean = avg(&currents);
    let charging = status.to_lowercase() == "charging";
    let on_battery = cur_mean.is_some_and(|c| crate::hwcond::battery_discharging(&status, c));

    let now_mean = avg(&now_watts);
    // 手机整机功耗合理区间。777W 这种读数只能说明单位不对, 不能进任何均值。
    let now_plausible = now_mean.is_some_and(|m| PLAUSIBLE_W.0 <= m && m <= PLAUSIBLE_W.1);

    let usb_all_mean = avg(&usb_all_watts);
    let batt_out_mean = avg(&batt_out_watts);
    let mode = crate::hwcond::classify_supply(&crate::hwcond::SupplyObs {
        status: &status,
        battery_ua: cur_mean,
        usb_w: usb_all_mean,
        charge_suspended,
        bypass_mode: charge_mode == "bypass",
    });
    use crate::hwcond::SupplyMode;
    // 旁路 (bypass): 停充后 USB 直供、电池不进不出 (status 常仍写 Charging)
    let bypass = mode == SupplyMode::Bypass;
    // 混合口径的值: 只在电池不在充时有意义 (充电电流随电量衰减的那份漂移不进来)
    let mixed_mean = if mode == SupplyMode::NotDischarging || mode == SupplyMode::Unknown {
        None
    } else {
        avg(&mixed_watts)
    };

    // 整窗证据 (main 侧 qualification): 平均是负的不等于整窗在放电 —— 一段充电能被别段的放电掩盖。
    // 窗内 (取窗口径) 或整段 (旧口径) 每一条功耗行都要解析得了、读数在可信区间;
    // 电池放电态另要前后 status 都是放电、每条电流都为负。旁路/混合 (ruler fix-D) 的 status
    // 会在 Full / Charging / Discharging 间跳, 不看 status, 改看两轨同刻相加的每一条都可信。
    let statuses_ok = |st: &str| matches!(st.to_lowercase().as_str(), "discharging" | "not charging");
    let in_range = |w: &f64| (crate::hwcond::POWER_MIN_W..=crate::hwcond::POWER_MAX_W).contains(w);
    let coverage = sample_window(text);
    let rows_ok = coverage.complete && n > 0 && n_malformed == 0 && !currents.is_empty();
    let power_window_verified = rows_ok
        && match mode {
            SupplyMode::Battery => {
                statuses_ok(&status)
                    && statuses_ok(&status_after)
                    && vi_watts.len() == currents.len()
                    && currents.iter().all(|c| *c < 0.0)
                    && vi_watts.iter().all(in_range)
            }
            SupplyMode::Bypass | SupplyMode::Mixed => {
                mixed_watts.len() == currents.len() && mixed_watts.iter().all(in_range)
            }
            _ => false,
        };

    let (mut power_w_mean, mut power_w_median) = (PyVal::Null, PyVal::Null);
    let reason: String = match mode {
        SupplyMode::Bypass | SupplyMode::Mixed if mixed_watts.is_empty() => "两轨同刻相加需要的电压/电流读数缺失".to_string(),
        SupplyMode::Battery if vi_watts.is_empty() => "读不到 voltage_now/current_now".to_string(),
        SupplyMode::FullIdle => format!(
            "电池已满且不进不出 (status=Full, current_now 均值={}): 整机由 USB 供电, 但没进旁路 \
(charge_suspend.sh 没写状态文件), 量不到可信整机功耗。charge_suspend.sh suspend 会在这种状态下关充电并记旁路",
            PyVal::from(cur_mean).py_str()
        ),
        SupplyMode::NotDischarging | SupplyMode::Unknown => format!(
            "非放电态 (status={}, current_now 均值={}): USB 输入功率里含给电池充电的部分, \
且充电电流随电量单调衰减, 量不到整机功耗。跑之前用 charge_suspend.sh 停充即可",
            if status.is_empty() { "?" } else { &status },
            PyVal::from(cur_mean).py_str()
        ),
        _ if !power_window_verified => format!(
            "整窗{}证据不完整或采样越界 (供电 {}): 需要完整采样窗、前后状态、窗内每条功耗行可解析且在可信区间{}; 旧记录不代填通过",
            if mode == SupplyMode::Battery { "放电" } else { "供电" },
            mode.as_str(),
            if mode == SupplyMode::Battery { "、每条负电流" } else { "" }
        ),
        SupplyMode::Bypass | SupplyMode::Mixed => {
            power_w_mean = PyVal::from(mixed_mean);
            power_w_median = PyVal::Float(py_round(crate::pyjson::median_f64(&mixed_watts).unwrap_or(0.0), 4));
            if bypass {
                "旁路供电 (已停充, 整机由 USB 直供, 电池电流 ≈ 0): 用 USB 轨与电池轨同一时刻相加 \
(电池项 ≈ 0), 含充电器转换损耗; 不能和放电态 (电池轨) 的数跨任务比"
                    .to_string()
            } else {
                format!(
                    "混合供电 (供电不足: USB 输入不够或中途断开, 电池同时在放电, 电池放电均值 {} W, \
USB 均值 {} W): 用 USB 轨与电池轨同一时刻的功率相加; USB 那份含充电器转换损耗, 不能跨任务比",
                    PyVal::from(batt_out_mean.map(|v| py_round(v, 3))).py_str(),
                    PyVal::from(usb_all_mean.map(|v| py_round(v, 3))).py_str()
                )
            }
        }
        SupplyMode::Battery => {
            power_w_mean = PyVal::from(avg(&vi_watts));
            power_w_median = PyVal::Float(py_round(
                crate::pyjson::median_f64(&vi_watts).unwrap_or(0.0),
                4,
            ));
            format!(
                "放电态{}, 用 |V x I| (本机 power_now 单位有误, 不采用)",
                if charge_suspended { "(已停充)" } else { "" }
            )
        }
    };
    let measured = !matches!(power_w_mean, PyVal::Null);
    let rail = if measured || !mode.measurable() { mode.rail() } else { "usb" };

    let mut out = pyobj! {
        "n_samples" => n,
        "battery_status" => status.clone(),
        "battery_status_after" => status_after,
        "power_window_verified" => power_window_verified,
        "thermal_window_verified" => coverage.complete && n>0 && temps.len()==n,
        "sampler_complete" => coverage.complete,
        "sample_duration_s" => coverage.duration_s,
        "sample_interval_s" => coverage.interval_s,
        "sample_first_uptime" => coverage.first_uptime,
        "sample_last_uptime" => coverage.last_uptime,
        "battery_capacity_pct" => capacity,
        "battery_charging" => charging,
        "charge_suspended" => charge_suspended,
        "on_battery" => on_battery,
        "bypass" => bypass,
        "power_valid" => mode.measurable() && measured,
        // battery / usb_plus_battery (旁路与混合同一口径: 两轨同刻相加) / usb (量不到)
        "power_source" => rail,
        // 这条数到底走的哪根轨。以前这里恒写 "battery", 旁路供电的功耗数也带着 "battery" 标签 ——
        // 两个轨的数被同一个名字装着, 跨任务一比就把充电器转换损耗算进了优化效果里。
        "power_rail" => rail,
        // 供电方式明细: battery / bypass / mixed / full_idle / not_discharging / unknown
        "supply_mode" => mode.as_str(),
        // 明确判出供电不足: USB 在 (或本应在) 供电, 电池却在补
        "supply_short" => mode == SupplyMode::Mixed,
        // 能不能跨任务/跨轮比: 只有电池放电态的数才有同一个口径。两轨相加的数只能跟同口径比,
        // 而且含充电器转换损耗。
        "power_cross_run_comparable" => mode == SupplyMode::Battery && measured,
        "fan_state" => fan_state,
        "current_now_ua_mean" => cur_mean,
        "voltage_now_uv_mean" => avg(&voltages),
        "power_now_w_mean" => now_mean,
        "power_now_plausible" => now_plausible,
        "power_vi_w_mean" => avg(&vi_watts),
        "usb_input_w_mean" => avg(&usb_watts),
        "battery_out_w_mean" => batt_out_mean.map(|v| py_round(v, 4)),
        // 两轨同刻相加 (混合口径) 的均值。组里口径不一致时 power_pairs 退回用它统一口径
        "power_mixed_w_mean" => mixed_mean,
        "power_w_mean" => power_w_mean,
        "power_w_median" => power_w_median,
        "power_usable_reason" => reason,
        "soc_temp_max_c" => temps.iter().copied().reduce(f64::max),
        "soc_temp_mean_c" => (!temps.is_empty())
            .then(|| py_round(py_sum(temps.iter().copied()) / temps.len() as f64, 3)),
    };
    // 只有采到 GPU 行时才加这几个键 —— 旧证据 (没有 GPU 行) 解析出来与以前逐字节相同
    if let (PyVal::Obj(kvs), false) = (&mut out, gpu_levels.is_empty()) {
        gpu_levels.sort_unstable();
        kvs.push((
            "gpu_max_pwrlevel_seen".into(),
            PyVal::List(gpu_levels.iter().map(|l| PyVal::Int(*l)).collect()),
        ));
        kvs.push(("gpu_clk_mhz_mean".into(), PyVal::from(avg(&gpu_mhz).map(|v| py_round(v, 1)))));
        kvs.push((
            "gpu_clk_mhz_max".into(),
            PyVal::from(gpu_mhz.iter().copied().reduce(f64::max).map(|v| py_round(v, 1))),
        ));
    }
    // 取窗口径的记录 (旧口径不加, 输出不变)
    if let (PyVal::Obj(kvs), Some(w)) = (&mut out, clip) {
        kvs.push((
            "power_window".into(),
            match w {
                Some((a, b)) => pyobj! {
                    "start_uptime" => a,
                    "end_uptime" => b,
                    "first_sample_dropped" => true,
                },
                None => pyobj! {
                    "start_uptime" => PyVal::Null,
                    "end_uptime" => PyVal::Null,
                    "first_sample_dropped" => true,
                    "note" => "trace 里没有 capture_start/end_uptime, 没截窗, 只丢了第一个点",
                },
            },
        ));
        kvs.push(("n_power_samples_total".into(), PyVal::from(n_power_total)));
        kvs.push(("n_power_samples_used".into(), PyVal::from(n_power_used)));
        kvs.push((
            "power_sample_hz".into(),
            PyVal::from(w.filter(|(a, b)| b > a).map(|(a, b)| py_round(n_power_used as f64 / (b - a), 2))),
        ));
        // 审查 R1 #1 #8: 取窗口径下功耗只有两件事都成立才可信 ——
        //  · env.txt 是 v2 采样器写的 (首行 `#sample_env v2`): 旧 sample_env.sh 不认停止标志,
        //    3 个点/窗、负载结束后接着采空闲段, 数是错的但不报错;
        //  · trace 头给了采集窗: 没窗就是全段 (含采集前准备与收尾), 与截了窗的另一臂差 30–70%,
        //    同组相减没有意义。
        // 这两种情况下 power_w_mean 照算 (离线复核用), 但 power_valid=false, 不进判定。
        let mut invalid: Vec<&str> = Vec::new();
        if !sample_env_v2_header(text) {
            invalid.push("env.txt 首行不是 `#sample_env v2` (设备端 sample_env.sh 是旧版, 没按停止标志截止)");
        }
        if w.is_none() {
            invalid.push("trace 头没有 capture_start/end_uptime, 功耗没截窗");
        }
        if !invalid.is_empty() {
            for (k, v) in kvs.iter_mut() {
                match k.as_str() {
                    "power_valid" | "power_cross_run_comparable" => *v = PyVal::Bool(false),
                    "power_usable_reason" => {
                        if let PyVal::Str(s) = v {
                            *s = format!("{s} [功耗作废: {}]", invalid.join("; "));
                        }
                    }
                    _ => {}
                }
            }
        }
    }
    out
}

/// env.txt 是不是 v2 采样器 (`sample_env.sh` 2026-10-21 版) 写的: 第一个非空行以 `#sample_env v2` 开头。
pub fn sample_env_v2_header(text: &str) -> bool {
    text.lines()
        .map(str::trim)
        .find(|l| !l.is_empty())
        .is_some_and(|l| l.starts_with("#sample_env v2"))
}

// ══════════════ 判定 ══════════════

fn significant(c: &PyVal, alpha: f64) -> bool {
    c.get("perm_p_two_sided")
        .and_then(|v| v.as_f64())
        .is_some_and(|p| p < alpha)
}

/// `comparisons`: {指标: analyze compare 的输出}。返回判定结论。
///
/// comparisons 里 a 臂是对照 (旋钮已还原), b 臂是旋钮臂 —— 与 compare 的参数顺序
/// 一致, diff = b - a。
pub struct DecideCtx {
    /// 温度两项原样回写 (int 进 int 出), 所以收 [`PyVal`]: 旧版对 int 46 写的是
    /// `超过上限 46C` 而不是 `46.0C`, 这个差别直接落在 result.json 的字节上。
    pub temp_max_c: PyVal,
    pub temp_cap_c: PyVal,
    pub apply_ok: bool,
    pub snapshot_identical: bool,
    pub power_available: bool,
}

pub fn decide(comparisons: &PyVal, ctx: &DecideCtx) -> PyVal {
    let required: Vec<String> = active_metrics(ctx.power_available).iter().map(|(m,_)|m.to_string()).collect();
    decide_required(comparisons, ctx, &required)
}

/// Required metrics are frozen by objective, never silently removed after measurement failure.
pub fn decide_required(comparisons: &PyVal, ctx: &DecideCtx, required: &[String]) -> PyVal {
    let metrics: Vec<(&str,&str)> = PRIMARY_METRICS.iter().copied().filter(|(m,_)|required.iter().any(|r|r==m)).collect();
    let k = metrics.len().max(1);
    let alpha_win = ALPHA / k as f64;

    // 三条作废线。注意这三条返回的 alpha_win 是**未舍入**的原值 —— 与旧版一致。
    if !ctx.apply_ok {
        return pyobj! {
            "verdict" => "ABORT",
            "reason" => "旋钮未全部生效, 本轮数据不算数",
            "alpha_win" => alpha_win,
        };
    }
    if let (Some(tm), Some(cap)) = (ctx.temp_max_c.as_f64(), ctx.temp_cap_c.as_f64()) {
        if tm > cap {
            return pyobj! {
                "verdict" => "ABORT",
                "reason" => format!("SoC 结温 {}C 超过上限 {}C",
                                    ctx.temp_max_c.py_str(), ctx.temp_cap_c.py_str()),
                "temp_max_c" => ctx.temp_max_c.clone(),
                "alpha_win" => alpha_win,
            };
        }
    }
    if !ctx.snapshot_identical {
        return pyobj! {
            "verdict" => "ABORT",
            "reason" => "轮末设备快照与轮前不一致, 留痕",
            "alpha_win" => alpha_win,
        };
    }

    if metrics.is_empty() || metrics.len() != required.len() {
        return pyobj! { "verdict" => "ABORT", "reason" => "冻结必需指标为空或包含未知项", "alpha_win" => alpha_win };
    }
    if ctx.temp_max_c.as_f64().is_none_or(|t| !t.is_finite() || t <= 0.0)
        || ctx.temp_cap_c.as_f64().is_none_or(|t| !t.is_finite() || t <= 0.0) {
        return pyobj! { "verdict" => "ABORT", "reason" => "温度证据或冻结上限缺失", "alpha_win" => alpha_win };
    }
    for (m,_) in &metrics {
        let valid = comparisons.get(m).is_some_and(|c| c.get("error").is_none()
            && ["diff_mean","a_mean","b_mean","perm_p_two_sided"].iter().all(|k|
                c.get(k).and_then(|v|v.as_f64()).is_some_and(f64::is_finite))
            && c.get("perm_p_two_sided").and_then(|v|v.as_f64()).is_some_and(|p|(0.0..=1.0).contains(&p)));
        if !valid {
            return pyobj! { "verdict" => "ABORT", "reason" => format!("必需指标 {m} 缺失或统计无效，禁止跳过后 KEEP"), "alpha_win" => alpha_win };
        }
    }
    let (mut wins, mut regressions): (Vec<String>, Vec<String>) = (vec![], vec![]);
    let mut detail: Vec<(String, PyVal)> = Vec::new();
    for (m, direction) in &metrics {
        let c = comparisons.get(m);
        let missing = match c {
            None => true,
            Some(c) => {
                c.get("error").is_some()
                    || matches!(c.get("diff_mean"), None | Some(PyVal::Null))
            }
        };
        if missing {
            detail.push((m.to_string(), pyobj! { "status" => "缺数据" }));
            continue;
        }
        let c = c.expect("checked above");
        let diff = c.get("diff_mean").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let improved = if *direction == "lower" {
            diff < 0.0
        } else {
            diff > 0.0
        };
        let status = if improved && significant(c, alpha_win) {
            wins.push(m.to_string());
            "显著改善"
        } else if !improved && diff != 0.0 && significant(c, ALPHA) {
            regressions.push(m.to_string());
            "显著变差"
        } else {
            "无显著变化"
        };
        let g = |k: &str| c.get(k).cloned().unwrap_or(PyVal::Null);
        detail.push((
            m.to_string(),
            pyobj! {
                "status" => status,
                "diff_mean" => g("diff_mean"),
                "diff_pct" => g("diff_pct"),
                "p" => g("perm_p_two_sided"),
                "a_mean" => g("a_mean"),
                "b_mean" => g("b_mean"),
                "ci95" => g("ci95"),
            },
        ));
    }

    let keep = !wins.is_empty() && regressions.is_empty();
    pyobj! {
        "verdict" => if keep { "KEEP" } else { "REJECT" },
        "wins" => wins.clone(),
        "regressions" => regressions.clone(),
        "alpha_win" => py_round(alpha_win, 6),
        "alpha_regression" => ALPHA,
        "temp_max_c" => ctx.temp_max_c.clone(),
        "temp_cap_c" => ctx.temp_cap_c.clone(),
        "per_metric" => PyVal::Obj(detail),
        "reason" => if keep {
            format!("改善 {} 且无显著退化", wins.join(","))
        } else if !regressions.is_empty() {
            format!("存在显著退化: {}", regressions.join(","))
        } else {
            "没有任何指标显著改善".to_string()
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pyjson::dumps;
    use std::path::{Path, PathBuf};

    fn repo_root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
    }

    const PROBE: &str = "# probe_sysparam v1
cpu.policies=0,3
cpu.policy0.avail_freqs=300000 1000000 2000000
cpu.policy0.avail_governors=schedutil performance
cpu.policy0.scaling_min_freq.cur=300000
cpu.policy0.scaling_max_freq.cur=2000000
cpu.policy0.scaling_governor.cur=schedutil
cpu.policy0.scaling_min_freq.writable=yes
cpu.policy0.scaling_min_freq.effect=live
cpu.policy0.scaling_max_freq.writable=yes
cpu.policy0.scaling_governor.writable=yes
cpu.policy3.avail_freqs=500000 3000000
cpu.policy3.scaling_min_freq.cur=500000
cpu.policy3.scaling_min_freq.writable=yes
cpu.policy3.scaling_min_freq.effect=rejected(wrote=3000000 readback=500000)
gpu.num_pwrlevels=4
gpu.min_pwrlevel.cur=3
gpu.max_pwrlevel.cur=0
gpu.min_pwrlevel.writable=yes
gpu.min_pwrlevel.effect=live
gpu.max_pwrlevel.writable=yes
gpu.devfreq.avail_freqs=220000000 1000000000
gpu.devfreq.min_freq.cur=220000000
gpu.devfreq.min_freq.writable=no
bus.DDR.boost_freq.cur=0
bus.DDR.hw_min_freq=200000
bus.DDR.hw_max_freq=5333000
bus.DDR.avail_freqs=200000 3200000 5333000
bus.DDR.boost_freq.writable=yes
bus.DDR.boost_freq.effect=live
setting.system.peak_refresh_rate.cur=120
setting.system.min_refresh_rate.cur=60
display.modes=fps=60,fps=120
thermal.cpu-1-0=42000
";

    fn wl_of(extra: &str) -> Whitelist {
        build_whitelist(&parse_probe(&format!("{PROBE}{extra}")))
    }
    fn has(wl: &Whitelist, pid: &str) -> bool {
        wl_get(wl, pid).is_some()
    }
    fn cand(pairs: &[(&str, &str)]) -> Vec<(String, String)> {
        pairs
            .iter()
            .map(|(a, b)| (a.to_string(), b.to_string()))
            .collect()
    }

    // ── 白名单 ──

    #[test]
    fn walt_tunables_get_in_only_when_writable_and_held() {
        let wl = wl_of("cpu.policy0.walt.hispeed_load.cur=90
cpu.policy0.walt.hispeed_load.writable=yes
cpu.policy0.walt.hispeed_load.effect=live
cpu.policy0.walt.hispeed_freq.cur=1000000
cpu.policy0.walt.hispeed_freq.writable=yes
cpu.policy0.walt.hispeed_freq.effect=contested(wrote=300000 1s=300000 lost=1000000)
cpu.policy0.walt.up_rate_limit_us.cur=0
cpu.policy0.walt.up_rate_limit_us.writable=no
");
        assert!(has(&wl, "cpu.policy0.walt.hispeed_load"));
        let e = wl_get(&wl, "cpu.policy0.walt.hispeed_load").unwrap();
        assert!(e.values.contains(&"100".to_string()) && e.values.contains(&"90".to_string()));
        assert!(e.path.ends_with("/policy0/walt/hispeed_load") && e.current == "90");
        assert!(!has(&wl, "cpu.policy0.walt.hispeed_freq"), "厂商抢回的不收");
        assert!(!has(&wl, "cpu.policy0.walt.up_rate_limit_us"), "写不动的不收");
    }

    #[test]
    fn only_writable_and_effective_nodes_get_in() {
        let wl = wl_of("");
        assert!(has(&wl, "cpu.policy0.scaling_min_freq"));
        // 写了被内核退回 → 不进
        assert!(!has(&wl, "cpu.policy3.scaling_min_freq"));
        // 不可写 → 不进
        assert!(!has(&wl, "gpu.devfreq.min_freq"));
    }

    /// 温控保护相关的东西, 哪怕探测报可写可生效也不准进白名单。
    /// 厂商管家会在十几秒后把值改回去 —— 那种节点 (probe 报 contested) 不能当旋钮。
    #[test]
    fn contested_nodes_stay_out_of_the_whitelist() {
        let contested = wl_of("cpu.policy0.scaling_max_freq.effect=contested(wrote=1000000 readback_1s=1000000 readback_10s=2000000)\n");
        assert!(!has(&contested, "cpu.policy0.scaling_max_freq"));
        // live 的照常进来 (PROBE 里 scaling_max_freq 没有 .effect, 属于未做差异测试那一档)
        let live = wl_of("cpu.policy0.scaling_max_freq.effect=live\n");
        assert!(has(&live, "cpu.policy0.scaling_max_freq"));
    }

    #[test]
    fn thermal_nodes_can_never_enter() {
        let wl = wl_of("gpu.thermal_pwrlevel.writable=yes\ngpu.thermal_pwrlevel.effect=live\n");
        assert!(wl.iter().all(|(k, s)| !k.contains("thermal") && !s.path.contains("thermal")));
    }

    /// 风扇转速是系统参数的一种, 但它不进自动调参白名单 —— 只当测试条件记录。
    #[test]
    fn fan_and_charge_nodes_can_never_enter() {
        let wl = wl_of(
            "fan._sys_kernel_fan_speed.writable=yes\nfan._sys_kernel_fan_speed.effect=live\n\
charge.qcom_battery_charging_enabled.writable=yes\ncharge.qcom_battery_charging_enabled.effect=live\n",
        );
        assert!(wl.iter().all(|(k, s)| {
            !k.contains("fan") && !s.path.to_lowercase().contains("fan")
        }));
        assert!(denied("/sys/kernel/fan/speed"));
        assert!(!validate_candidate(&cand(&[("/sys/kernel/fan/speed", "3")]), &wl).0);
        assert!(!validate_candidate(
            &cand(&[("/sys/class/qcom-battery/charging_enabled", "0")]),
            &wl
        )
        .0);
    }

    /// 取值表只有一个值 = 没什么可调的, 不进白名单。
    #[test]
    fn single_valued_params_are_dropped() {
        let wl = build_whitelist(&parse_probe(
            "cpu.policies=0\ncpu.policy0.avail_freqs=1000000\n\
cpu.policy0.scaling_min_freq.cur=1000000\ncpu.policy0.scaling_min_freq.writable=yes\n",
        ));
        assert!(!has(&wl, "cpu.policy0.scaling_min_freq"));
    }

    #[test]
    fn refresh_rate_values_come_from_display_modes() {
        let wl = wl_of("");
        let s = wl_get(&wl, "setting.system.peak_refresh_rate").unwrap();
        assert_eq!(s.values, vec!["60", "120"]);
        assert_eq!(s.kind, "setting");
        assert_eq!(wl_get(&wl, "bus.DDR.boost_freq").unwrap().values,
                   vec!["200000", "3200000", "5333000"]);
    }

    #[test]
    fn fps_parser_takes_both_dumpsys_wordings() {
        assert_eq!(fps_list("60.000004 fps,144.00002 fps,120.00001 fps"), vec![60, 120, 144]);
        assert_eq!(fps_list("fps=60,fps=120,fps=120"), vec![60, 120]);
        assert!(fps_list("").is_empty());
    }

    /// 厂商键的取值语义没文档, 只认探测时真把活动刷新率改掉的那几档。
    #[test]
    fn vendor_refresh_mode_only_takes_modes_that_really_changed_fps() {
        let wl = wl_of(
            "setting.system.refresh_rate_mode.cur=0
setting.system.refresh_rate_mode.base_fps=120
setting.system.refresh_rate_mode.mode1_fps=60 (readback=1)
setting.system.refresh_rate_mode.mode2_fps=120 (readback=2)
setting.system.refresh_rate_mode.mode3_fps=144 (readback=9)
",
        );
        // mode1 真的把 120 变成了 60 → 收; mode2 fps 没变 → 不收;
        // mode3 fps 变了但回读对不上 (写 3 读回 9) → 不收
        assert_eq!(
            wl_get(&wl, "setting.system.refresh_rate_mode").unwrap().values,
            vec!["0", "1"]
        );
    }

    #[test]
    fn vendor_refresh_mode_is_absent_without_real_evidence() {
        // 一档都没真生效
        assert!(!has(
            &wl_of("setting.system.refresh_rate_mode.cur=0
setting.system.refresh_rate_mode.base_fps=120
setting.system.refresh_rate_mode.mode1_fps=120 (readback=1)
"),
            "setting.system.refresh_rate_mode"
        ));
        // 活动刷新率读不出来 → 一档都不许收, 不能按下标猜
        assert!(!has(
            &wl_of("setting.system.refresh_rate_mode.cur=0
setting.system.refresh_rate_mode.base_fps=
setting.system.refresh_rate_mode.mode1_fps= (readback=1)
setting.system.refresh_rate_mode.mode2_fps= (readback=2)
"),
            "setting.system.refresh_rate_mode"
        ));
        // readback=10 不是 mode 1 的回读 —— 子串匹配会把它当成生效
        assert!(!has(
            &wl_of("setting.system.refresh_rate_mode.cur=0
setting.system.refresh_rate_mode.base_fps=120
setting.system.refresh_rate_mode.mode1_fps=60 (readback=10)
"),
            "setting.system.refresh_rate_mode"
        ));
        // 厂商键回的是 auto 这种符号值时安静跳过, 不能让整条闭环崩在这儿
        assert!(!has(
            &wl_of("setting.system.refresh_rate_mode.cur=auto
setting.system.refresh_rate_mode.base_fps=120
setting.system.refresh_rate_mode.mode1_fps=60 (readback=1)
"),
            "setting.system.refresh_rate_mode"
        ));
    }

    // ── 校验 ──

    #[test]
    fn validate_accepts_legal_and_rejects_the_rest() {
        let wl = wl_of("");
        assert!(validate_candidate(&cand(&[("bus.DDR.boost_freq", "5333000")]), &wl).0);
        assert!(!validate_candidate(&[], &wl).0);

        let (ok, why) = validate_candidate(
            &cand(&[("/sys/class/thermal/thermal_zone0/mode", "disabled")]),
            &wl,
        );
        assert!(!ok && why.contains("不在白名单"), "{why}");

        let (ok, why) = validate_candidate(&cand(&[("bus.DDR.boost_freq", "9999999")]), &wl);
        assert!(!ok && why.contains("不在合法取值表"), "{why}");

        let (ok, why) = validate_candidate(
            &cand(&[
                ("cpu.policy0.scaling_min_freq", "2000000"),
                ("cpu.policy0.scaling_max_freq", "1000000"),
            ]),
            &wl,
        );
        assert!(!ok && why.contains("超过同簇 max"), "{why}");

        // 0 是最快档, 所以 max_pwrlevel 必须 <= min_pwrlevel
        let (ok, why) = validate_candidate(
            &cand(&[("gpu.min_pwrlevel", "0"), ("gpu.max_pwrlevel", "3")]),
            &wl,
        );
        assert!(!ok && why.contains("必须 <="), "{why}");

        assert!(!validate_candidate(
            &cand(&[
                ("setting.system.min_refresh_rate", "120"),
                ("setting.system.peak_refresh_rate", "60"),
            ]),
            &wl
        )
        .0);
    }

    /// 白名单里没有的 id 生成不出 plan —— 悄悄少写一行会让推到设备上的 plan
    /// 与 result.json 里记的 params 不是同一组。
    #[test]
    fn plan_text_refuses_unknown_ids() {
        let wl = wl_of("");
        let e = plan_text(&cand(&[("nope.nope", "1")]), &wl).unwrap_err();
        assert!(e.contains("nope.nope"), "{e}");
    }

    /// 探测原值不是数字时, 跨项约束查不了就整组作废, 不能用"最宽松的默认值"放行。
    #[test]
    fn unparseable_current_fails_the_candidate_not_the_guard() {
        let wl = wl_of("");
        let mut broken = wl.clone();
        for (pid, spec) in broken.iter_mut() {
            if pid == "cpu.policy0.scaling_max_freq" {
                spec.current = "auto".into();
            }
        }
        // 只给 min, max 得从 current 取 —— 那个 current 是坏的
        let (ok, why) = validate_candidate(
            &cand(&[("cpu.policy0.scaling_min_freq", "2000000")]),
            &broken,
        );
        assert!(!ok, "居然放行了");
        assert!(why.contains("scaling_max_freq") && why.contains("auto"), "{why}");
    }

    /// 取值表的报错文本里带 Python 列表 repr, 证据里是逐字节比的。
    #[test]
    fn value_table_error_keeps_python_list_repr() {
        let wl = wl_of("");
        let (_, why) = validate_candidate(&cand(&[("bus.DDR.boost_freq", "9999999")]), &wl);
        assert_eq!(
            why,
            "bus.DDR.boost_freq=9999999 不在合法取值表 ['200000', '3200000', '5333000']..."
        );
    }

    // ── plan ──

    #[test]
    fn plan_is_tab_separated_and_deterministic() {
        let wl = wl_of("");
        let c = cand(&[
            ("cpu.policy0.scaling_min_freq", "1000000"),
            ("cpu.policy0.scaling_max_freq", "2000000"),
            ("setting.system.peak_refresh_rate", "60"),
        ]);
        let mut rev = c.clone();
        rev.reverse();
        let t1 = plan_text(&c, &wl).unwrap();
        assert_eq!(t1, plan_text(&rev, &wl).unwrap()); // 同一组参数, 逐字节相同
        for l in t1.lines().filter(|l| !l.starts_with('#')) {
            assert_eq!(l.split('\t').count(), 3, "{l}");
        }
        // 放宽上限的项排在抬高下限之前, 少踩一次内核的 min<=max 夹取
        assert!(t1.find("scaling_max_freq") < t1.find("scaling_min_freq"));
        assert!(t1.contains("setting\tsystem:peak_refresh_rate\t60"));
    }

    fn complete_stats(text:&str)->PyVal {
        let mut rows:Vec<String>=text.lines().filter(|l|l.starts_with("ENV ")).map(String::from).collect();
        // Explicit test sampler protocol. Single-point arithmetic fixtures get a second identical sample.
        if rows.len()==1 {rows.push(rows[0].clone());}
        let mut lines:Vec<String>=text.lines().filter(|l|!l.starts_with("ENV ") && !l.starts_with("#sample_env")).map(String::from).collect();
        lines.insert(0,format!("#sample_env v1 dur={} interval=2",rows.len()*2));
        for (i,row) in rows.iter().enumerate() {let mut parts:Vec<String>=row.split_whitespace().map(String::from).collect();parts[1]=(100+i*2).to_string();lines.push(parts.join(" "));}
        lines.push("#sample_env done".into());env_stats(&lines.join("\n"))
    }

    // ── 功耗温度解析 ──

    /// 本机 battery/power_now 单位有误 (读出过 777W), 所以以 |V x I| 为准。
    #[test]
    fn discharging_uses_volts_times_amps_not_power_now() {
        let s = complete_stats(
            "#sample_env v1\n#battery_status=Discharging\n#battery_status_after=Discharging\n\
ENV 100.0 4000000 -1500000 777000000 0 0 cpu-1-0 41000\n\
ENV 102.0 4000000 -1500000 777000000 0 0 gpuss-0 43500\n",
        );
        assert_eq!(s.get("power_w_mean").unwrap().as_f64(), Some(6.0));
        assert_eq!(s.get("power_vi_w_mean").unwrap().as_f64(), Some(6.0));
        // power_now 原样记录, 但标成不合理, 不进任何均值
        assert_eq!(s.get("power_now_w_mean").unwrap().as_f64(), Some(777.0));
        assert_eq!(s.get("power_now_plausible"), Some(&PyVal::Bool(false)));
        assert_eq!(s.get("soc_temp_max_c").unwrap().as_f64(), Some(43.5));
        assert_eq!(s.get("n_samples"), Some(&PyVal::Int(2)));
    }

    /// `bv / 1e6 * bi / 1e6` 在 Python 里是从左往右算的, 即 `((bv/1e6) * bi) / 1e6`。
    /// 单个样本上两种写法看不出差别, 平均到四位小数上就差一个数 —— 这四行是实测出来的。
    #[test]
    fn watt_math_follows_python_left_to_right() {
        let s = complete_stats(
            "#battery_status=Discharging
ENV 1.0 4000000 -1500000 NA 0 0 cpu-1-0 41000
ENV 2.0 3800000 -1000000 NA 0 0 cpu-1-0 41000
ENV 3.0 3800000 -1000000 NA 0 0 cpu-1-0 41000
ENV 4.0 3800000 771000 NA 0 0 cpu-1-0 41000
",
        );
        // python3: round(sum(abs(bv/1e6*bi/1e6) for ...)/4, 4) == 4.1325
        assert_eq!(s.get("power_vi_w_mean").unwrap().as_f64(), Some(4.1325));
    }

    #[test]
    fn plausible_power_now_is_still_only_recorded() {
        let s = complete_stats(
            "#battery_status=Discharging\n#battery_status_after=Discharging\nENV 1.0 4000000 -1500000 5000000 0 0 cpu-1-0 40000\n",
        );
        assert_eq!(s.get("power_now_plausible"), Some(&PyVal::Bool(true)));
        assert_eq!(s.get("power_now_w_mean").unwrap().as_f64(), Some(5.0));
        assert_eq!(s.get("power_w_mean").unwrap().as_f64(), Some(6.0)); // 仍用 V x I
    }

    /// 充电态: USB 输入里含给电池充电的部分, 且充电电流随电量单调衰减 ——
    /// 那个衰减会被当成「功耗随时间下降」混进 A/B 比较。所以如实报 None。
    #[test]
    fn charging_makes_power_unusable() {
        let s = complete_stats(
            "#battery_status=Charging\nENV 1.0 4000000 1000000 5000000 5000000 2000000 cpu-1-0 40000\n",
        );
        assert_eq!(s.get("power_w_mean"), Some(&PyVal::Null));
        assert_eq!(s.get("battery_charging"), Some(&PyVal::Bool(true)));
        assert_eq!(s.get("power_source"), Some(&PyVal::Str("usb".into())));
        assert!(matches!(s.get("power_usable_reason"), Some(PyVal::Str(r)) if r.contains("非放电态")));
        assert_eq!(s.get("usb_input_w_mean").unwrap().as_f64(), Some(10.0));
        assert_eq!(s.get("current_now_ua_mean").unwrap().as_f64(), Some(1000000.0));
    }

    #[test]
    fn sentinel_temps_and_missing_fan_are_not_guessed() {
        let s = complete_stats(
            "#battery_status=Discharging\n#battery_status_after=Discharging\nENV 1.0 4000000 -1000000 NA 0 0 cpu-1-0 0\n\
ENV 2.0 4000000 -1000000 NA 0 0 cpu-1-0 200000\n",
        );
        assert_eq!(s.get("soc_temp_max_c"), Some(&PyVal::Null));
        let s = complete_stats("#battery_status=Discharging\n#battery_status_after=Discharging\n#fan_state=\nENV 1.0 4000000 -1000000 NA 0 0 cpu-1-0 41000\n");
        assert_eq!(s.get("fan_state"), Some(&PyVal::Null));
        let s = complete_stats("#battery_status=Discharging\n#battery_status_after=Discharging\n#fan_state=speed=3,fan1_input=4200\nENV 1.0 4000000 -1000000 NA 0 0 cpu-1-0 41000\n");
        assert_eq!(s.get("fan_state"), Some(&PyVal::Str("speed=3,fan1_input=4200".into())));
    }

    /// on_battery 要 status 与电流方向同时成立 —— 两个判据都会单独骗人。
    #[test]
    fn on_battery_needs_both_status_and_current_direction() {
        // 停充后内核仍写 "Not charging", 但电流确实在放 → 算放电态
        let s = complete_stats(
            "#battery_status=Not charging\n#battery_status_after=Not charging\n#battery_capacity=88\n#charge_suspended=yes\n\
ENV 1.0 4000000 -1500000 NA 5000000 100000 cpu-1-0 41000\n",
        );
        assert_eq!(s.get("on_battery"), Some(&PyVal::Bool(true)));
        // 但 USB 同时还在出 0.5 W: 混合供电, 两轨同刻相加 (审计 #5), 不再只取电池轨的 6.0 W
        assert_eq!(s.get("power_source"), Some(&PyVal::Str("usb_plus_battery".into())));
        assert_eq!(s.get("supply_mode"), Some(&PyVal::Str("mixed".into())));
        assert_eq!(s.get("power_w_mean").unwrap().as_f64(), Some(6.5));
        assert_eq!(s.get("battery_capacity_pct"), Some(&PyVal::Str("88".into())));
        // USB 不出力时才是纯电池轨
        let s = complete_stats(
            "#battery_status=Not charging\n#battery_status_after=Not charging\n#battery_capacity=88\n#charge_suspended=yes\n\
ENV 1.0 4000000 -1500000 NA 5000000 0 cpu-1-0 41000\n",
        );
        assert_eq!(s.get("power_source"), Some(&PyVal::Str("battery".into())));
        assert_eq!(s.get("power_w_mean").unwrap().as_f64(), Some(6.0));
        assert!(matches!(s.get("power_usable_reason"), Some(PyVal::Str(r)) if r.contains("已停充")));

        // status 说 Not charging 但电流是正的 = 还在往电池里灌
        let s = complete_stats("#battery_status=Not charging\n#battery_status_after=Not charging\nENV 1.0 4000000 771000 NA 0 0 cpu-1-0 41000\n");
        assert_eq!(s.get("on_battery"), Some(&PyVal::Bool(false)));
        assert_eq!(s.get("power_w_mean"), Some(&PyVal::Null));
        // 充放平衡的瞬间电流过零 —— 判不出放电态就不算
        let s = complete_stats("#battery_status=Not charging\n#battery_status_after=Not charging\nENV 1.0 4000000 0 NA 0 0 cpu-1-0 41000\n");
        assert_eq!(s.get("on_battery"), Some(&PyVal::Bool(false)));
        assert_eq!(s.get("power_w_mean"), Some(&PyVal::Null));
    }

    /// 10-11 手机节点 (USB-C PD) 上实测的旁路供电: 停充后 status 仍是 Charging, 电池电流
    /// 衰减到 ≈0, USB 0.13-0.22 A @ 5.15 V。功耗取 USB 轨, 口径标 usb_bypass。
    #[test]
    fn bypass_takes_usb_rail_power_only_when_the_battery_is_really_idle() {
        let hdr = "#battery_status=Charging\n#battery_capacity=96\n#charge_suspended=yes\n#charge_mode=bypass\n";
        // 整窗证据 (main): 采样协议头、done 标记齐全才给功耗数
        let s = complete_stats(&format!(
            "{hdr}ENV 1.0 4375000 29000 NA 5158000 216000 cpu-1-0 41000\n\
ENV 3.0 4374000 -12000 NA 5141000 197000 cpu-1-0 41000\n"
        ));
        assert_eq!(s.get("bypass"), Some(&PyVal::Bool(true)));
        assert_eq!(s.get("on_battery"), Some(&PyVal::Bool(false)));
        assert_eq!(s.get("power_source"), Some(&PyVal::Str("usb_plus_battery".into())));
        assert_eq!(s.get("power_valid"), Some(&PyVal::Bool(true)));
        let w = s.get("power_w_mean").unwrap().as_f64().unwrap();
        // 两轨同刻相加: USB 减去电池充进去的 (第 1 条 +29 mA), 加上电池放出来的 (第 2 条 −12 mA)
        let want = (5.158 * 0.216 - 4.375 * 0.029 + 5.141 * 0.197 + 4.374 * 0.012) / 2.0;
        assert!((w - want).abs() < 1e-3, "{w} vs {want}");
        // 状态文件说旁路, 但电池其实在充 (+1.9 A): 不算, 功耗 null
        let s = env_stats(&format!("{hdr}ENV 1.0 4436000 1926000 NA 4762000 2134000 cpu-1-0 41000\n"));
        assert_eq!(s.get("bypass"), Some(&PyVal::Bool(false)));
        assert_eq!(s.get("power_w_mean"), Some(&PyVal::Null));
        assert_eq!(s.get("power_source"), Some(&PyVal::Str("usb".into())));
        // 没停充 (没有状态文件) 时电池恰好 ≈0: 不算旁路
        let s = env_stats("#battery_status=Charging\n#charge_suspended=no\n#charge_mode=\n\
ENV 1.0 4375000 10000 NA 5158000 216000 cpu-1-0 41000\n");
        assert_eq!(s.get("bypass"), Some(&PyVal::Bool(false)));
        assert_eq!(s.get("power_w_mean"), Some(&PyVal::Null));
    }

    /// 2026-10-12 W1 实测: 电池满电空闲 (status=Full, current_now ≈ -3 mA, USB 供电)。
    /// 没进旁路时绝不能当放电态交出电池轨的 ~0.01 W; 进了旁路 (charge_suspend.sh 记 bypass)
    /// 则取 USB 轨。
    #[test]
    fn full_and_idle_battery_is_not_discharge() {
        let rows = "ENV 1.0 4412000 -2000 NA 4920000 806000 cpu-0-4-1 44900\n\
ENV 3.0 4412000 -3000 NA 5083000 1009000 cpu-0-0-1 42800\n";
        // 没有状态文件: 不是放电态, 功耗 null, 原因写明满电空闲
        let s = env_stats(&format!("#battery_status=Full\n#battery_capacity=100\n#charge_suspended=no\n#charge_mode=\n{rows}"));
        assert_eq!(s.get("on_battery"), Some(&PyVal::Bool(false)));
        assert_eq!(s.get("power_w_mean"), Some(&PyVal::Null));
        assert_eq!(s.get("power_valid"), Some(&PyVal::Bool(false)));
        assert!(matches!(s.get("power_usable_reason"), Some(PyVal::Str(r)) if r.contains("满")));
        // charge_suspend.sh 判了满电旁路: 取 USB 轨
        let s = complete_stats(&format!("#battery_status=Full\n#battery_capacity=100\n#charge_suspended=yes\n#charge_mode=bypass\n{rows}"));
        assert_eq!(s.get("bypass"), Some(&PyVal::Bool(true)));
        assert_eq!(s.get("power_source"), Some(&PyVal::Str("usb_plus_battery".into())));
        let w = s.get("power_w_mean").unwrap().as_f64().unwrap();
        let want = (4.920 * 0.806 + 4.412 * 0.002 + 5.083 * 1.009 + 4.412 * 0.003) / 2.0;
        assert!((w - want).abs() < 1e-3, "{w} vs {want}");
        // 满电但拔了线在真放电: 仍是放电态
        let s = env_stats("#battery_status=Full\n#charge_suspended=no\n#charge_mode=\n\
ENV 1.0 4400000 -900000 NA 0 0 cpu-0-0-1 42000\n");
        assert_eq!(s.get("on_battery"), Some(&PyVal::Bool(true)));
    }

    /// 轨道标签必须写实际走的那根: 以前旁路数也标成 "battery",
    /// 两个轨的数被同一个名字装着, 跨任务一比就把充电器损耗算进了优化效果。
    #[test]
    fn power_rail_names_the_rail_actually_used() {
        use serde_json::json;
        // 电池轨的功耗要整窗放电证据 (采样协议头、逐条负电流、前后状态、done 标记) 才作数
        let battery = env_stats(
            "#sample_env v1 dur=4 interval=2\n#battery_status=Discharging\n\
ENV 1.0 4000000 -1000000 NA 0 0 cpu-1-0 41000\nENV 3.0 4000000 -1000000 NA 0 0 cpu-1-0 41000\n\
#battery_status_after=Discharging\n#sample_env done\n",
        );
        assert_eq!(battery.get("power_rail"), Some(&PyVal::Str("battery".into())));
        assert_eq!(battery.get("power_cross_run_comparable"), Some(&PyVal::Bool(true)));

        let bypass = complete_stats(
            "#battery_status=Full\n#battery_capacity=100\n#charge_suspended=yes\n#charge_mode=bypass\n\
ENV 1.0 4412000 -2000 NA 4920000 806000 cpu-0-4-1 44900\n",
        );
        assert_eq!(bypass.get("power_source"), Some(&PyVal::Str("usb_plus_battery".into())));
        assert_eq!(bypass.get("power_rail"), Some(&PyVal::Str("usb_plus_battery".into())));
        assert_eq!(bypass.get("power_valid"), Some(&PyVal::Bool(true)), "本轮两臂同轨相减仍成立");
        assert_eq!(
            bypass.get("power_cross_run_comparable"),
            Some(&PyVal::Bool(false)),
            "旁路轨含充电器损耗, 不能拿去跟电池轨的数比"
        );

        let charging = env_stats(
            "#battery_status=Charging\nENV 1.0 4000000 1000000 NA 5000000 2000000 cpu-1-0 40000\n",
        );
        assert_eq!(charging.get("power_rail"), Some(&PyVal::Str("usb".into())));
        assert_eq!(charging.get("power_cross_run_comparable"), Some(&PyVal::Bool(false)));

        // 排除原因要能直接进回包
        let byp_env = json!({"on_battery": false, "power_valid": true, "power_source": "usb_bypass"});
        assert!(!power_cross_run_comparable([&byp_env]), "旁路轨不能跨任务比");
        assert!(power_group_note([&byp_env]).is_none(), "但本轮同轨相减仍成立");
        let bat = json!({"on_battery": true, "power_valid": true, "power_source": "battery",
                         "power_w_mean": 3.84, "power_cross_run_comparable": true});
        assert!(power_cross_run_comparable([&bat]));
        let note = power_group_note([&bat, &byp_env]).unwrap();
        assert!(note.contains("轨道不一致"), "{note}");
        let dead = json!({"on_battery": false, "power_valid": false, "power_source": "usb",
                          "power_usable_reason": "非放电态"});
        assert!(power_group_note([&dead]).unwrap().contains("非放电态"));
    }

    #[test]
    fn mixing_battery_and_bypass_power_in_one_group_invalidates_power() {        use serde_json::json;
        let bat = json!({"on_battery": true, "power_valid": true, "power_source": "battery"});
        let byp = json!({"on_battery": false, "power_valid": true, "power_source": "usb_bypass"});
        let usb = json!({"on_battery": false, "power_valid": false, "power_source": "usb"});
        let old = json!({"on_battery": true}); // 旧证据: 没有 power_valid / power_source
        assert!(power_group_ok([&bat, &bat]));
        assert!(power_group_ok([&byp, &byp]));
        assert!(!power_group_ok([&bat, &byp]), "两种口径混在一组");
        assert!(!power_group_ok([&byp, &usb]));
        assert!(power_group_ok([&old, &bat]), "旧证据按电池放电认");
        assert!(!power_group_ok([&old, &byp]));
        assert!(power_group_ok(std::iter::empty::<&serde_json::Value>()));
    }

    // ── 判定 ──

    fn cmp_of(diff: f64, p: f64) -> PyVal {
        pyobj! {
            "metric" => "m", "diff_mean" => diff, "diff_pct" => diff / 100.0 * 100.0,
            "perm_p_two_sided" => p, "a_mean" => 100.0, "b_mean" => 100.0 + diff,
            "ci95" => PyVal::Null,
        }
    }
    fn base_ctx() -> DecideCtx {
        DecideCtx {
            temp_max_c: PyVal::Float(42.0),
            temp_cap_c: PyVal::Float(46.0),
            apply_ok: true,
            snapshot_identical: true,
            power_available: true,
        }
    }
    fn verdict_of(v: &PyVal) -> &str {
        match v.get("verdict") {
            Some(PyVal::Str(s)) => s,
            _ => "",
        }
    }

    #[test]
    fn capture_energy_clips_to_same_window_and_requires_discharge() {
        let env="#sample_env v1 dur=6 interval=2\n#battery_status=Discharging\nENV 100 4000000 -500000 NA 0 0 cpu-0 40000\nENV 102 4000000 -1000000 NA 0 0 cpu-0 40000\nENV 104 4000000 -1500000 NA 0 0 cpu-0 40000\n#battery_status_after=Discharging\n#sample_env done\n";
        let trace="#loop_v1_meta capture_start_uptime=100.5 capture_end_uptime=103.5 duration_req=3\n";
        let energy=battery_energy_in_capture(env,trace,3).unwrap();
        // Linear P(t) rises 2->6W. Clipping half a second from each end gives
        // 12J over exactly 3s; using the full sample window would give 16J.
        assert!((energy["energy_j"].as_f64().unwrap()-12.0).abs()<1e-9);
        assert!((energy["power_w_mean"].as_f64().unwrap()-4.0).abs()<1e-9);
        assert_eq!(energy["window"]["start"],100.5);
        assert!(battery_energy_in_capture(env,&trace.replace("100.5","99.5").replace("103.5","102.5"),3).is_err());
        assert!(battery_energy_in_capture(&env.replace("-1000000","1000000"),trace,3).is_err());
        // v2 coverage and integration must use the SAME ENV+PWR series. The
        // last ENV is 102; the final PWR alone brackets the 103.5 endpoint.
        let v2="#sample_env v2 dur=13 interval=2 power_interval=0.2 stop=/x\n#battery_status=Discharging\nENV 100 4000000 -1000000 NA 0 0 cpu-0 40000\nPWR 100.2 4000000 -1000000 NA 0 0\nENV 102 4000000 -1000000 NA 0 0 cpu-0 40000\nPWR 103.6 4000000 -1000000 NA 0 0\n#stop_seen\n#battery_status_after=Discharging\n#sample_env done\n";
        let energy=battery_energy_in_capture(v2,trace,3).unwrap();
        assert!((energy["energy_j"].as_f64().unwrap()-12.0).abs()<1e-9);
        assert_eq!(energy["samples"],4);
        let short=v2.replace("PWR 103.6","PWR 103.2");
        assert!(trace_window_covered(&sample_window(&short),trace,3));
        assert!(battery_energy_in_capture(&short,trace,3).is_err(),"coverage slack cannot extrapolate energy");
        assert!(battery_energy_in_capture(&v2.replace("PWR 100.2","PWR 100"),trace,3).is_err());
        assert!(battery_energy_in_capture(v2,&format!("{trace}{trace}"),3).is_err());
    }
    #[test]
    fn window_requires_done_count_span_and_bound_capture() {
        let good="#sample_env v1 dur=6 interval=2\n#battery_status=Discharging\nENV 100 4000000 -1000000 NA 0 0 cpu-1-0 41000\nENV 102 4000000 -1000000 NA 0 0 cpu-1-0 41000\nENV 104 4000000 -1000000 NA 0 0 cpu-1-0 41000\n#battery_status_after=Discharging\n#sample_env done";
        assert!(sample_window(good).complete);
        let trace="#loop_v1_meta capture_start_uptime=100.5 capture_end_uptime=103.5 duration_req=3\n";
        assert!(trace_window_covered(&sample_window(good),trace,3));
        for bad in [good.replace("#sample_env done",""),good.replace("ENV 102 4000000 -1000000 NA 0 0 cpu-1-0 41000\n",""),good.replace("ENV 104","ENV 102.1"),good.replace("ENV 102","ENV broken")] {
            let v=env_stats(&bad);assert!(!sample_window(&bad).complete);assert_eq!(v.get("power_w_mean"),Some(&PyVal::Null));
        }
        assert!(!trace_window_covered(&sample_window(good),trace,30));
        assert!(!trace_window_covered(&sample_window(good),"#loop_v1_meta capture_start_uptime=102 capture_end_uptime=105 duration_req=3",3));
        let one="#sample_env v1 dur=30 interval=2\n#battery_status=Discharging\nENV 100 4000000 -1000000 NA 0 0 cpu-1-0 41000\n#battery_status_after=Discharging\n#sample_env done";
        assert!(!sample_window(one).complete);assert_eq!(env_stats(one).get("power_w_mean"),Some(&PyVal::Null));
    }

    /// 合并 (main 整窗证据 × ruler fix-C v2 采样器): v2 采样器见停止标志提前停时, 窗口完整性按
    /// ENV+PWR 全部功耗行判, 末端对 trace 采集窗放宽两个快循环周期; 没停止标志就要走满 dur。
    #[test]
    fn v2_sampler_window_accepts_stop_marker_and_covers_capture() {
        let v2 = "#sample_env v2 dur=40 interval=2 power_interval=0.2 stop=/x\n#battery_status=Discharging\n\
ENV 100.00 4000000 -1000000 NA 0 0 cpu-1-0 41000\nPWR 100.20 4000000 -1000000 NA 0 0\n\
ENV 102.00 4000000 -1000000 NA 0 0 cpu-1-0 41000\nPWR 103.50 4000000 -1000000 NA 0 0\n\
ENV 104.00 4000000 -1000000 NA 0 0 cpu-1-0 41000\nPWR 104.80 4000000 -1000000 NA 0 0\n\
#stop_seen uptime=104.95\n#battery_status_after=Discharging\n#sample_env done\n";
        let w = sample_window(v2);
        assert!(w.complete);
        assert_eq!((w.first_uptime, w.last_uptime), (Some(100.0), Some(104.8)));
        // 采集窗 [100.5, 105.0], 最后一条功耗行 104.8 在放宽 0.5 s 之内
        let trace = "#loop_v1_meta capture_start_uptime=100.5 capture_end_uptime=105.0 duration_req=4.5\n";
        assert!(trace_window_covered(&w, trace, 4) == false, "duration_req 对不上");
        let trace = "#loop_v1_meta capture_start_uptime=100.5 capture_end_uptime=104.5 duration_req=4\n";
        assert!(trace_window_covered(&w, trace, 4));
        // 没停止标志又没走满 dur: 不完整
        assert!(!sample_window(&v2.replace("#stop_seen uptime=104.95\n", "")).complete);
        // 相邻功耗行间隔超过 2 × interval: 不完整
        assert!(!sample_window(&v2.replace("PWR 103.50", "PWR 107.50").replace("ENV 104.00", "ENV 108.00").replace("PWR 104.80", "PWR 108.80")).complete);
        let s = env_stats_window(v2, Some((100.5, 104.5)));
        assert_eq!(s.get("power_window_verified"), Some(&PyVal::Bool(true)));
        assert_eq!(s.get("power_w_mean").and_then(|v| v.as_f64()), Some(4.0));
    }

    #[test]
    fn required_missing_metric_and_unknown_thermal_block_other_win() {
        let incomplete=pyobj! {"frame_p95"=>cmp_of(-9.0,0.0001),"fps_mean"=>cmp_of(0.0,1.0)};
        assert_eq!(verdict_of(&decide(&incomplete,&base_ctx())),"ABORT");
        let complete=pyobj! {"frame_p95"=>cmp_of(-9.0,0.0001),"fps_mean"=>cmp_of(0.0,1.0),"power_w_mean"=>cmp_of(0.0,1.0)};
        assert_eq!(verdict_of(&decide(&complete,&DecideCtx{temp_max_c:PyVal::Null,..base_ctx()})),"ABORT");
    }

    #[test]
    fn negative_average_does_not_prove_full_window_discharge() {
        let samples="ENV 1.0 4000000 -2000000 NA 0 0 cpu-1-0 41000\nENV 2.0 4000000 500000 NA 0 0 cpu-1-0 41000\n";
        let s=env_stats(&format!("#battery_status=Discharging\n#battery_status_after=Discharging\n{samples}"));
        assert!(s.get("current_now_ua_mean").unwrap().as_f64().unwrap()<0.0);
        assert_eq!(s.get("power_window_verified"),Some(&PyVal::Bool(false)));
        assert_eq!(s.get("power_w_mean"),Some(&PyVal::Null));
    }

    #[test]
    fn old_or_partial_power_evidence_keeps_raw_but_not_verdict_metric() {
        for text in [
            "#battery_status=Discharging\nENV 1 4000000 -1000000 NA 0 0 cpu-1-0 41000\n",
            "#battery_status=Discharging\n#battery_status_after=Charging\nENV 1 4000000 -1000000 NA 0 0 cpu-1-0 41000\n",
            "#battery_status=Discharging\n#battery_status_after=Discharging\nENV 1 4000000 -1000000 NA 0 0 cpu-1-0 41000\nENV 2 NA NA NA 0 0 cpu-1-0 41000\n",
        ] {
            let s=env_stats(text);
            assert!(s.get("power_vi_w_mean").unwrap().as_f64().is_some());
            assert_eq!(s.get("power_w_mean"),Some(&PyVal::Null));
        }
    }

    #[test]
    fn keep_needs_a_significant_win_and_no_regression() {
        let c = pyobj! {
            "frame_p95" => cmp_of(-2.0, 0.001),
            "fps_mean" => cmp_of(0.0, 1.0),
            "power_w_mean" => cmp_of(0.0, 1.0),
        };
        let d = decide(&c, &base_ctx());
        assert_eq!(verdict_of(&d), "KEEP");
        assert_eq!(d.get("wins"), Some(&PyVal::List(vec![PyVal::Str("frame_p95".into())])));
    }

    /// 3 个指标时 alpha_win=0.0167, p=0.03 的改善还不够格; 同样的 p 在「变差」
    /// 一侧就算数 —— 两侧故意不对称。
    #[test]
    fn bonferroni_tightens_only_the_win_side() {
        let c = pyobj! {
            "frame_p95" => cmp_of(-2.0, 0.03),
            "fps_mean" => cmp_of(0.0, 1.0),
            "power_w_mean" => cmp_of(0.0, 1.0),
        };
        let d = decide(&c, &base_ctx());
        assert_eq!(verdict_of(&d), "REJECT");
        assert_eq!(d.get("alpha_win").unwrap().as_f64(), Some(py_round(0.05 / 3.0, 6)));

        let c = pyobj! {
            "frame_p95" => cmp_of(-2.0, 0.001),
            "fps_mean" => cmp_of(-1.0, 0.03),
            "power_w_mean" => cmp_of(0.0, 1.0),
        };
        let d = decide(&c, &base_ctx());
        assert_eq!(verdict_of(&d), "REJECT");
        assert_eq!(d.get("regressions"), Some(&PyVal::List(vec![PyVal::Str("fps_mean".into())])));
    }

    #[test]
    fn power_win_counts_too() {
        let c = pyobj! {
            "frame_p95" => cmp_of(0.0, 1.0),
            "fps_mean" => cmp_of(0.0, 1.0),
            "power_w_mean" => cmp_of(-0.5, 0.005),
        };
        let d = decide(&c, &base_ctx());
        assert_eq!(verdict_of(&d), "KEEP");
        assert_eq!(d.get("wins"), Some(&PyVal::List(vec![PyVal::Str("power_w_mean".into())])));
    }

    #[test]
    fn three_abort_lines_beat_any_improvement() {
        let c = pyobj! { "frame_p95" => cmp_of(-9.0, 0.0001) };
        for ctx in [
            DecideCtx { temp_max_c: PyVal::Float(50.0), ..base_ctx() },
            DecideCtx { snapshot_identical: false, ..base_ctx() },
            DecideCtx { apply_ok: false, ..base_ctx() },
        ] {
            assert_eq!(verdict_of(&decide(&c, &ctx)), "ABORT");
        }
        let d = decide(&c, &DecideCtx { temp_max_c: PyVal::Float(50.0), ..base_ctx() });
        assert!(matches!(d.get("reason"), Some(PyVal::Str(r)) if r.contains("超过上限")));
        // int 进 int 出: 旧版对整数温度写的是 "50C 超过上限 46C", 不是 "50.0C"
        let d = decide(&c, &DecideCtx {
            temp_max_c: PyVal::Int(50), temp_cap_c: PyVal::Int(46), ..base_ctx()
        });
        assert_eq!(d.get("reason"), Some(&PyVal::Str("SoC 结温 50C 超过上限 46C".into())));
    }

    /// 功耗关掉时只剩两个指标 (Bonferroni 放宽到 0.025), 且功耗哪怕显著变差也不算退化
    /// —— 它根本不在判定里, 不能拿一个量不准的数去否决一个真实的帧时改善。
    #[test]
    fn power_out_of_verdict_drops_the_metric_entirely() {
        let ctx = DecideCtx { power_available: false, ..base_ctx() };
        let d = decide(&pyobj! { "frame_p95" => cmp_of(-2.0, 0.02), "fps_mean" => cmp_of(0.0, 1.0) }, &ctx);
        assert_eq!(d.get("alpha_win").unwrap().as_f64(), Some(0.025));
        assert_eq!(verdict_of(&d), "KEEP");

        let d = decide(
            &pyobj! {
                "frame_p95" => cmp_of(-2.0, 0.001),
                "fps_mean" => cmp_of(0.0, 1.0),
                "power_w_mean" => cmp_of(5.0, 0.0001),
            },
            &ctx,
        );
        assert_eq!(verdict_of(&d), "KEEP");
        assert_eq!(d.get("regressions"), Some(&PyVal::List(vec![])));
        assert!(d.get("per_metric").unwrap().get("power_w_mean").is_none());
    }

    #[test]
    fn rule_doc_records_reachability_and_why_power_is_out() {
        let r = rule_doc(&PyVal::Float(46.0), &PyVal::Int(5), false, "功耗不计入判定, 仅记录供参考 (充电态)");
        assert_eq!(r.get("power_in_verdict"), Some(&PyVal::Bool(false)));
        assert_eq!(r.get("n_metrics"), Some(&PyVal::Int(2)));
        assert!(matches!(r.get("power_note"), Some(PyVal::Str(s)) if s.contains("充电态")));
        // 4v4 最小可达 p = 2/C(8,4) = 0.0286, 够不着 alpha_win=0.0167
        assert_eq!(rule_doc(&PyVal::Float(46.0), &PyVal::Int(4), true, "").get("reachable"), Some(&PyVal::Bool(false)));
        // 5v5 最小可达 p = 2/C(10,5) = 0.0079, 够得着
        let r = rule_doc(&PyVal::Float(46.0), &PyVal::Int(5), true, "");
        assert_eq!(r.get("reachable"), Some(&PyVal::Bool(true)));
        assert!((r.get("min_reachable_p").unwrap().as_f64().unwrap() - 2.0 / 252.0).abs() < 1e-12);
        assert_eq!(rule_doc(&PyVal::Float(46.0), &PyVal::Int(1), true, "").get("min_reachable_p"), Some(&PyVal::Null));
        // 对数荒唐地大时 C(2n,n) 溢出 double: 报不出最小可达 p, 也不许判成"够得着"
        // (Python 那边是 OverflowError 把整局打掉)
        let r = rule_doc(&PyVal::Float(46.0), &PyVal::Int(100_000), true, "");
        assert_eq!(r.get("min_reachable_p"), Some(&PyVal::Null));
        assert_eq!(r.get("reachable"), Some(&PyVal::Bool(false)));
        // 整数入参原样回写, 不该被提成浮点
        let r = rule_doc(&PyVal::Int(46), &PyVal::Int(5), true, "");
        assert_eq!(r.get("temp_cap_c"), Some(&PyVal::Int(46)));
        assert_eq!(r.get("pairs_per_candidate"), Some(&PyVal::Int(5)));
    }

    // ── 真机录制对照 ──

    /// autoloop 是 `json.dump(..., sort_keys=True)`, 它**递归**排序, 所以对照前也递归排一遍。
    fn sort_deep(v: PyVal) -> PyVal {
        match v {
            PyVal::Obj(mut kvs) => {
                kvs = kvs.into_iter().map(|(k, x)| (k, sort_deep(x))).collect();
                kvs.sort_by(|a, b| a.0.cmp(&b.0));
                PyVal::Obj(kvs)
            }
            PyVal::List(xs) => PyVal::List(xs.into_iter().map(sort_deep).collect()),
            other => other,
        }
    }

    /// 对照源: 两次真机探测的 probe.txt → whitelist.json (旧 Python 版落的盘)。
    #[test]
    fn whitelist_matches_recorded_golden() {
        let mut checked = 0;
        for run in ["probe3", "genshin1"] {
            let dir = repo_root().join("loop_v1/fixtures/golden/runs_sysparam").join(run);
            let (probe, golden) = (dir.join("probe.txt"), dir.join("whitelist.json"));
            if !probe.exists() || !golden.exists() {
                continue;
            }
            let wl = build_whitelist(&parse_probe(&std::fs::read_to_string(&probe).unwrap()));
            let want = crate::pyjson::loads(&std::fs::read_to_string(&golden).unwrap()).unwrap();
            assert_eq!(
                dumps(&sort_deep(whitelist_to_pyval(&wl))),
                dumps(&sort_deep(want)),
                "{run} 的白名单与录制不一致"
            );
            checked += 1;
        }
        assert_eq!(checked, 2, "两份录制都要比到");
    }

    /// 对照源: 真机跑出来的 result.json。三组候选里 cand1/cand2 在调 decide 之前就被
    /// autoloop 自己拦下了 (温度、旋钮没生效), 只有 cand3 真走到了判定 —— 它留痕作废,
    /// 而且 alpha_win 落的是**未舍入**的 0.016666666666666666, 正好钉住作废那三条线
    /// 与正常路径的取整不一样这件事。
    #[test]
    fn decide_matches_recorded_result() {
        let f = repo_root().join("loop_v1/fixtures/golden/runs_sysparam/genshin1/gen1/cand3/result.json");
        let v = crate::pyjson::loads(&std::fs::read_to_string(&f).unwrap()).unwrap();
        let got = decide(
            v.get("comparisons").unwrap(),
            &DecideCtx {
                temp_max_c: v.get("temp_max_c").cloned().unwrap_or(PyVal::Null),
                temp_cap_c: v.get("temp_cap_c").cloned().unwrap_or(PyVal::Null),
                apply_ok: matches!(v.get("apply_ok"), Some(PyVal::Bool(true))),
                // snapshot_check.ours_identical = false 就是这一轮作废的原因
                snapshot_identical: false,
                power_available: true,
            },
        );
        for k in ["verdict", "reason", "alpha_win"] {
            assert_eq!(
                dumps(got.get(k).unwrap()),
                dumps(v.get(k).unwrap()),
                "{k} 与录制不一致"
            );
        }
        // 留痕这条线走的是未舍入的 alpha_win
        assert_eq!(got.get("alpha_win").unwrap().as_f64(), Some(0.05 / 3.0));
    }

    /// 厂商性能服务会把 GPU max_pwrlevel 改回 17: 逐样本的 GPU 行汇总成「见到过哪些档」
    /// 与频率均值/最大值; 没有 GPU 行的旧证据不多出任何键。
    #[test]
    fn env_stats_records_gpu_cap_changes_only_when_sampled() {
        let s = env_stats(
            "#battery_status=Discharging\n\
ENV 1.0 4000000 -1000000 NA 0 0 cpu-1-0 41000\n\
GPU 1.0 0 900000000\n\
ENV 3.0 4000000 -1000000 NA 0 0 cpu-1-0 41000\n\
GPU 3.0 17 300000000\n\
GPU 5.0 17 bad\n",
        );
        assert_eq!(
            s.get("gpu_max_pwrlevel_seen"),
            Some(&PyVal::List(vec![PyVal::Int(0), PyVal::Int(17)]))
        );
        assert_eq!(s.get("gpu_clk_mhz_mean").and_then(|v| v.as_f64()), Some(600.0));
        assert_eq!(s.get("gpu_clk_mhz_max").and_then(|v| v.as_f64()), Some(900.0));
        assert_eq!(s.get("n_samples").and_then(|v| v.as_i64()), Some(2));
        let old = env_stats("#battery_status=Discharging\nENV 1.0 4000000 -1000000 NA 0 0 cpu-1-0 41000\n");
        assert!(old.get("gpu_max_pwrlevel_seen").is_none());
        assert!(old.get("gpu_clk_mhz_mean").is_none());
    }

    /// 审计 #3: trace 头部的采集窗取得到; 没有就 None; 不往 trace 正文里扫。
    #[test]
    fn capture_window_from_trace_reads_header_only() {
        let t = "#loop_v1_meta capture_start_uptime=100.50 capture_end_uptime=110.52 duration_req=10\n\
#loop_v1_meta gpu_model=Adreno840v2\n\
 <...>-1 [000] 1.0: kgsl_x: a=1\n\
#loop_v1_meta capture_start_uptime=1 capture_end_uptime=2\n";
        assert_eq!(capture_window_from_trace(t), Some((100.5, 110.52)));
        assert_eq!(capture_window_from_trace("#loop_v1_meta gpu_model=x\nbody\n"), None);
        // 起止颠倒或解析不了 = 没有窗口
        assert_eq!(
            capture_window_from_trace("#loop_v1_meta capture_start_uptime=5 capture_end_uptime=4\n"),
            None
        );
    }

    const V2_TEXT: &str = "#sample_env v2 dur=14 interval=2 power_interval=0.2 stop=/x\n\
#battery_status=Discharging\n\
ENV 99.00 4000000 -3000000 NA 0 0 cpu-1-0 45000\n\
GPU 99.00 3 900000000\n\
PWR 99.80 4000000 -7000000 NA 0 0\n\
PWR 100.20 4000000 -1000000 NA 0 0\n\
ENV 100.40 4000000 -1000000 NA 0 0 cpu-1-0 50000\n\
GPU 100.40 3 600000000\n\
PWR 100.60 4000000 -1500000 NA 0 0\n\
PWR 101.00 4000000 -500000 NA 0 0\n\
ENV 101.50 4000000 -100000 NA 0 0 cpu-1-0 48000\n\
GPU 101.50 3 160000000\n\
#stop_seen uptime=101.60\n\
#battery_status_after=Discharging\n\
#sample_env done\n";

    /// 审计 #3 #4: 功耗只取窗内样本 (含 PWR 快循环行), 第一个点不要, 结温整段看, GPU 截窗。
    #[test]
    fn env_stats_window_clips_power_to_capture_window() {
        let s = env_stats_window(V2_TEXT, Some((100.0, 101.0)));
        // 99.00 是第一个点 (丢), 99.80 与 101.50 在窗外;
        // 窗内 100.20 (4 W)、100.40 (4 W)、100.60 (6 W)、101.00 (2 W) → 均值 4 W
        assert_eq!(s.get("n_power_samples_total").and_then(|v| v.as_i64()), Some(7));
        assert_eq!(s.get("n_power_samples_used").and_then(|v| v.as_i64()), Some(4));
        assert_eq!(s.get("power_w_mean").and_then(|v| v.as_f64()), Some(4.0));
        assert_eq!(s.get("power_sample_hz").and_then(|v| v.as_f64()), Some(4.0));
        // 结温不截窗: 最高 50 °C, 均值按三条 ENV 行
        assert_eq!(s.get("soc_temp_max_c").and_then(|v| v.as_f64()), Some(50.0));
        assert_eq!(s.get("soc_temp_mean_c").and_then(|v| v.as_f64()), Some(47.667));
        // GPU 截窗: 只剩 100.40 那一行
        assert_eq!(s.get("gpu_clk_mhz_mean").and_then(|v| v.as_f64()), Some(600.0));
        assert_eq!(s.get("n_samples").and_then(|v| v.as_i64()), Some(3));
        let w = s.get("power_window").unwrap();
        assert_eq!(w.get("start_uptime").and_then(|v| v.as_f64()), Some(100.0));
        assert_eq!(w.get("first_sample_dropped"), Some(&PyVal::Bool(true)));
    }

    /// 没有采集窗时只丢第一个点, 并写明没截窗; 第一个点即使在窗内也丢。
    #[test]
    fn env_stats_window_drops_first_sample() {
        let s = env_stats_window(V2_TEXT, None);
        // 7 个功耗样本去掉第一个 (12 W) → 剩 6 个: 28+4+4+6+2+0.4 = 44.4 → 均值 7.4
        // (99.80 原为 36 W; 整窗证据要求每条都在可信区间 0.05..30 W, 改成 28 W)
        assert_eq!(s.get("n_power_samples_used").and_then(|v| v.as_i64()), Some(6));
        assert_eq!(s.get("power_w_mean").and_then(|v| v.as_f64()), Some(7.4));
        assert!(s.get("power_window").unwrap().get("note").is_some());
        assert_eq!(s.get("power_sample_hz"), Some(&PyVal::Null));
        let early = env_stats_window(V2_TEXT, Some((99.0, 102.0)));
        assert_eq!(early.get("n_power_samples_used").and_then(|v| v.as_i64()), Some(6));
    }

    /// 审查 R1 #1 #8: 没有 v2 头 (旧采样器) 或没有采集窗 (没截窗) 时功耗不可信, 不进判定;
    /// 数照算, 原因写明。两件都齐时不受影响。
    #[test]
    fn env_stats_window_invalidates_power_without_v2_header_or_window() {
        let ok = env_stats_window(V2_TEXT, Some((100.0, 101.0)));
        assert_eq!(ok.get("power_valid"), Some(&PyVal::Bool(true)));
        assert!(!ok.get("power_usable_reason").unwrap().py_str().contains("功耗作废"));

        let no_window = env_stats_window(V2_TEXT, None);
        assert_eq!(no_window.get("power_valid"), Some(&PyVal::Bool(false)));
        assert_eq!(no_window.get("power_cross_run_comparable"), Some(&PyVal::Bool(false)));
        assert!(no_window.get("power_w_mean").and_then(|v| v.as_f64()).is_some(), "数照算, 只是不可信");
        assert!(no_window.get("power_usable_reason").unwrap().py_str().contains("没截窗"));

        let v1 = V2_TEXT.replacen("#sample_env v2", "#sample_env v1", 1);
        let old = env_stats_window(&v1, Some((100.0, 101.0)));
        assert_eq!(old.get("power_valid"), Some(&PyVal::Bool(false)));
        assert!(old.get("power_usable_reason").unwrap().py_str().contains("#sample_env v2"));
        assert!(!sample_env_v2_header(&v1));
        assert!(sample_env_v2_header("\n  #sample_env v2 dur=1\n"));
        assert!(!sample_env_v2_header("#battery_status=x\n#sample_env v2\n"), "必须是首行");
    }

    /// 旧口径 (env_stats) 对没有 PWR 行的证据: 不截窗、不丢点、不加新键 (整窗证据仍要齐, 见 main 的 sample_window)。
    #[test]
    fn env_stats_legacy_unchanged_and_has_no_window_keys() {
        let t = "#battery_status=Discharging\n#battery_status_after=Discharging\n\
ENV 1.0 4000000 -3000000 NA 0 0 cpu-1-0 41000\n\
ENV 3.0 4000000 -1000000 NA 0 0 cpu-1-0 41000\n";
        let s = complete_stats(t);
        assert_eq!(s.get("power_w_mean").and_then(|v| v.as_f64()), Some(8.0));
        assert!(s.get("power_window").is_none());
        assert!(s.get("n_power_samples_used").is_none());
    }

    /// 审计 #5 的现场: 停充后 USB 被限在约 0.49 A, status 仍写 Charging, 电池同时在放。
    /// 以前判成「usb、非放电态」, power_w_mean=null; 现在两轨同刻相加。
    #[test]
    fn usb_limited_plus_battery_is_measured_as_mixed() {
        let s = complete_stats(
            "#battery_status=Charging\n#battery_capacity=90\n#charge_suspended=yes\n#charge_mode=battery\n\
ENV 1.0 4000000 -600000 NA 5000000 490000 cpu-1-0 41000\n\
ENV 3.0 4000000 -800000 NA 5000000 490000 cpu-1-0 41000\n",
        );
        assert_eq!(s.get("supply_mode"), Some(&PyVal::Str("mixed".into())));
        assert_eq!(s.get("supply_short"), Some(&PyVal::Bool(true)));
        assert_eq!(s.get("power_valid"), Some(&PyVal::Bool(true)));
        assert_eq!(s.get("power_source"), Some(&PyVal::Str("usb_plus_battery".into())));
        // (2.45 + 2.4 + 2.45 + 3.2) / 2
        assert_eq!(s.get("power_w_mean").unwrap().as_f64(), Some(5.25));
        assert_eq!(s.get("power_cross_run_comparable"), Some(&PyVal::Bool(false)));
        assert!(matches!(s.get("power_usable_reason"), Some(PyVal::Str(r)) if r.contains("供电不足")));
    }

    /// 2026-10-12 Megacity 目标运行 (1012b gen1 ctrl5 / ctrl1): 满电停充旁路时, 同一轮里有的臂
    /// USB 直供, 有的臂 USB 掉成 0、电池放 0.64 A。两臂必须落在同一个口径上。
    #[test]
    fn bypass_and_battery_arms_in_one_bypass_run_share_one_rail() {
        let hdr = "#battery_status=Full\n#battery_capacity=100\n#charge_suspended=yes\n#charge_mode=bypass\n";
        let usb_arm = complete_stats(&format!("{hdr}ENV 1.0 4412000 0 NA 5000000 566000 cpu-1-0 41000\n"));
        let bat_arm = complete_stats(&format!("{hdr}ENV 1.0 4380000 -640000 NA 0 0 cpu-1-0 41000\n"));
        assert_eq!(usb_arm.get("supply_mode"), Some(&PyVal::Str("bypass".into())));
        assert_eq!(bat_arm.get("supply_mode"), Some(&PyVal::Str("mixed".into())));
        assert_eq!(usb_arm.get("power_source"), bat_arm.get("power_source"));
        assert_eq!(bat_arm.get("power_w_mean").unwrap().as_f64(), Some(2.8032));
        assert_eq!(usb_arm.get("power_w_mean").unwrap().as_f64(), Some(2.83));
    }

    fn penv(valid: bool, rail: &str, w: Option<f64>, mixed: Option<f64>) -> serde_json::Value {
        serde_json::json!({"power_valid": valid, "power_source": rail, "power_w_mean": w,
                           "power_mixed_w_mean": mixed, "power_usable_reason": "非放电态 (测试)"})
    }

    #[test]
    fn one_bad_arm_drops_only_its_pair() {
        let ok = |w| penv(true, "battery", Some(w), Some(w));
        let bad = penv(false, "usb", None, None);
        let ctrl = [ok(3.0), ok(3.1), bad.clone(), ok(3.2)];
        let knob = [ok(2.8), ok(2.9), ok(2.7), ok(2.85)];
        let c: Vec<&serde_json::Value> = ctrl.iter().collect();
        let k: Vec<&serde_json::Value> = knob.iter().collect();
        let pp = power_pairs(&c, &k);
        assert!(pp.usable(), "剔一对剩三对, 照常进判定");
        assert_eq!(pp.kept(), (3, 3));
        assert_eq!(pp.knob[2], None, "被剔那一对的候选臂也不进");
        assert_eq!(pp.dropped.len(), 1);
        assert_eq!(pp.dropped[0].0, 3);
        assert!(pp.dropped[0].1.contains("对照臂") && pp.dropped[0].1.contains("非放电态"));
        let note = pp.note().unwrap();
        assert!(note.contains("剔除 1 对") && note.contains("第 3 轮"), "{note}");
        assert_eq!(pp.to_json()["in_verdict"], true);
        // 剔到每臂不足 2 个: 不进判定, 并说清楚
        let ctrl = [ok(3.0), bad.clone(), bad.clone()];
        let knob = [ok(2.8), ok(2.9), ok(2.7)];
        let pp = power_pairs(&ctrl.iter().collect::<Vec<_>>(), &knob.iter().collect::<Vec<_>>());
        assert!(!pp.usable());
        assert!(pp.note().unwrap().contains("功耗不进判定"));
    }

    #[test]
    fn mixed_rails_are_unified_to_the_two_rail_sum_or_the_majority_rail() {
        // 各臂都带两轨相加值: 统一改用它, 一对都不剔
        let ctrl = [penv(true, "battery", Some(2.8), Some(2.81)), penv(true, "usb_plus_battery", Some(2.9), Some(2.9))];
        let knob = [penv(true, "usb_plus_battery", Some(2.7), Some(2.7)), penv(true, "usb_plus_battery", Some(2.75), Some(2.75))];
        let pp = power_pairs(&ctrl.iter().collect::<Vec<_>>(), &knob.iter().collect::<Vec<_>>());
        assert!(pp.unified_to_mixed && pp.usable());
        assert_eq!(pp.ctrl[0], Some(2.81));
        assert_eq!(pp.rail.as_deref(), Some("usb_plus_battery"));
        // 旧证据 (没有两轨相加值): 取臂数多的口径, 少数口径那一对剔除
        let old_bat = serde_json::json!({"on_battery": true, "power_w_mean": 3.0});
        let byp = penv(true, "usb_bypass", Some(3.3), None);
        let ctrl = [old_bat.clone(), old_bat.clone(), byp.clone()];
        let knob = [old_bat.clone(), old_bat.clone(), old_bat.clone()];
        let pp = power_pairs(&ctrl.iter().collect::<Vec<_>>(), &knob.iter().collect::<Vec<_>>());
        assert!(!pp.unified_to_mixed);
        assert_eq!(pp.rail.as_deref(), Some("battery"));
        assert_eq!(pp.kept(), (2, 2));
        assert!(pp.dropped[0].1.contains("主口径"), "{:?}", pp.dropped);
        // 中途停下多出来的单臂: 合格就留
        let ctrl = [old_bat.clone(), old_bat.clone()];
        let knob = [old_bat.clone(), old_bat.clone(), old_bat.clone()];
        let pp = power_pairs(&ctrl.iter().collect::<Vec<_>>(), &knob.iter().collect::<Vec<_>>());
        assert_eq!(pp.kept(), (2, 3));
        assert!(pp.dropped.is_empty() && pp.note().is_none());
    }

    /// 合并后 C 与 D 的组合顺序: 功耗样本先按 trace 采集窗截取 (C), 截窗后的样本再做供电分类 (D),
    /// 最后在一组里按对剔除 (D)。窗外的充电样本不能影响分类, 窗内的充电样本不能被窗外的放电样本掩盖。
    #[test]
    fn power_is_clipped_then_classified_then_pair_dropped() {
        let hdr = "#sample_env v2 dur=14 interval=2 power_interval=0.2 stop=/x\n#battery_status=Discharging\n\
#charge_suspended=no\n#charge_mode=\n";
        // 窗外 (第一个点与 99.5、101.5) 在充电, 窗内 [100, 101] 纯电池放电 1 A × 4 V
        let good = format!(
            "{hdr}ENV 99.00 4000000 1500000 NA 5000000 2000000 cpu-1-0 45000\n\
PWR 99.50 4000000 1500000 NA 5000000 2000000\n\
PWR 100.20 4000000 -1000000 NA 0 0\n\
PWR 100.60 4000000 -1000000 NA 0 0\n\
ENV 101.00 4000000 -1000000 NA 0 0 cpu-1-0 45000\n\
PWR 101.50 4000000 1500000 NA 5000000 2000000\n#stop_seen\n#battery_status_after=Discharging\n#sample_env done\n"
        );
        // 反过来: 窗外放电, 窗内在充电
        let bad = format!(
            "{hdr}ENV 99.00 4000000 -1000000 NA 0 0 cpu-1-0 45000\n\
PWR 99.50 4000000 -1000000 NA 0 0\n\
PWR 100.20 4000000 1500000 NA 5000000 2000000\n\
PWR 100.60 4000000 1500000 NA 5000000 2000000\n\
ENV 101.00 4000000 1500000 NA 5000000 2000000 cpu-1-0 45000\n\
PWR 101.50 4000000 -1000000 NA 0 0\n#stop_seen\n#battery_status_after=Discharging\n#sample_env done\n"
        );
        let w = Some((100.0, 101.0));
        let g = env_stats_window(&good, w);
        assert_eq!(g.get("supply_mode"), Some(&PyVal::Str("battery".into())), "只看窗内: 放电");
        assert_eq!(g.get("power_valid"), Some(&PyVal::Bool(true)));
        assert_eq!(g.get("power_w_mean").and_then(|v| v.as_f64()), Some(4.0));
        // 不截窗时窗外的充电样本会把它拖成非放电态 —— 说明分类确实在截窗之后
        assert_ne!(env_stats(&good).get("supply_mode"), Some(&PyVal::Str("battery".into())));
        let b = env_stats_window(&bad, w);
        assert_eq!(b.get("supply_mode"), Some(&PyVal::Str("not_discharging".into())), "窗内在充电");
        assert_eq!(b.get("power_valid"), Some(&PyVal::Bool(false)));
        // 按对剔除: 第 2 轮对照臂不可信, 只剔第 2 对
        let to_json = |v: &PyVal| -> serde_json::Value { serde_json::from_str(&crate::pyjson::dumps(v)).unwrap() };
        let (g, b) = (to_json(&g), to_json(&b));
        let ctrl = [&g, &b, &g];
        let knob = [&g, &g, &g];
        let pp = power_pairs(&ctrl, &knob);
        assert_eq!(pp.kept(), (2, 2));
        assert!(pp.usable());
        assert_eq!(pp.dropped.len(), 1);
        assert_eq!(pp.dropped[0].0, 2);
        assert!(pp.dropped[0].1.contains("对照臂"), "{:?}", pp.dropped);
    }

    #[test]
    fn usb_supply_note_comes_from_the_charge_suspend_log() {
        let log = "CHARGE_SUSPENDED_BYPASS /sys/class/qcom-battery/battery_charging_enabled: 1 -> 0\n\
CHARGE_USB_LIMIT limit_ua=490000 usb_uv=5000000 max_mw=2450 need_mw=6000\n\
CHARGE_SUPPLY_LOW 供电不足: USB 输入上限约 2450 mW, 低于 6000 mW。\n";
        assert!(usb_supply_note(log).unwrap().starts_with("供电不足"));
        let log = "CHARGE_USB_LIMIT limit_ua=3000000 usb_uv=9000000 max_mw=27000 need_mw=6000\n";
        assert!(usb_supply_note(log).unwrap().contains("max_mw=27000"));
        assert_eq!(usb_supply_note("CHARGE_SUSPENDED x: 1 -> 0\n"), None);
    }
}
