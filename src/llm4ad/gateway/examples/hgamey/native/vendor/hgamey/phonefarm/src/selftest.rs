//! `phonefarm selftest` —— 判定链的已知答案真机自检 (refbench)。
//!
//! 为什么要有
//! ----------
//! harness 的价值在于结论可信。本地静态检查有红队语料, 但真机判定这一侧此前没有
//! 「答案事先知道」的考题: 判定链说 KEEP / REJECT 时, 没人知道它在真机噪声下到底多准。
//! 这里出四类考题, 答案由构造给定, 看判定链答得对不对:
//!
//! | 考题     | 构造                                         | 应当的判定                         |
//! |----------|----------------------------------------------|------------------------------------|
//! | `aa`     | 两臂装同一份字节码                           | 不判 KEEP, 也不报显著变差          |
//! | `better` | B 臂少跑若干工作组 (帧时按标定幅度变好)     | KEEP (检出率随幅度上升 → MDE)      |
//! | `worse`  | B 臂多跑若干工作组                           | REJECT 且列出显著变差的指标        |
//! | `cheat`  | B 臂少跑 + 采样降分辨率 (帧时好、画面糙)    | 画面判据 POOR_QUALITY              |
//!
//! 负载: refbench `sr_pipeline` 的 postfx 插槽装 `../refbench/selftest/load.spv`。
//! 两臂是同一份字节码, 只改特化常量的缺省值 (见那份 .comp 的头注释) —— 代码路径
//! 逐字节相同, 差别只在「多少个工作组跑重循环」这一个整数上。不改 refbench 的 apk。
//!
//! 判定一律走现有链, 本模块不写任何统计:
//!   · 帧时/帧率/功耗: [`loopstat::compare`] (精确置换) + [`sysparam::decide`]
//!     —— 与 `autoloop` 同一份代码, 同一口径 (alpha_win = 0.05/K, alpha_regression = 0.05);
//!   · 画面: `phonefarm eval --request` (kind=shader → gpu-op 的画质地板), 子进程调用。
//!
//! 设备侧每臂的流程与 `eval` / `autoloop` 的 run_one 同构 (等冷 → 起负载 → ftrace +
//! sample_env 并行采集 → 解析), 区别只是负载换成 refbench。判定输入落盘后可离线重算:
//! `phonefarm selftest report --root <目录>` 不碰设备, 同样的证据每次出同样的结论。
//!
//! 证据体积: 一臂的原始 ftrace 约 30 MB (大头是显示驱动的 sde_evtlog), 解析器只读
//! `#` 元数据行与含 `adreno_cmdbatch` / `kgsl_` 的行 —— 设备端先按同一条件过滤再拉回,
//! 本地 gzip 存档。过滤后重算的 summary 与原始 trace 逐字节相同 (解析器自己就是这么预筛的)。

use crate::autoloop;
use crate::device::Device;
use crate::eval::{adb, adb_ok, adb_path, push_tools, repo_root, snapshot, su, DEV_TMP};
use crate::gpuop::refbench as rb;
use crate::hwcond;
use crate::loopreport::snapshot_diff;
use crate::loopstat;
use crate::looptrace;
use crate::pyjson::{dumps, py_round, PyVal};
use crate::sysparam::{self, DecideCtx};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

const REMOTE_DIR: &str = "/data/local/tmp/phonefarm_selftest";

// ══════════════ SPIR-V 特化常量改写 ══════════════

const SPV_MAGIC: u32 = 0x0723_0203;
const OP_DECORATE: u32 = 71;
const DEC_SPEC_ID: u32 = 1;
const OP_SPEC_CONSTANT: u32 = 50;

pub const SPEC_HEAVY_WG: u32 = 0;
pub const SPEC_ITERS: u32 = 1;
pub const SPEC_DOWNS: u32 = 2;
/// 输出 1920x1080、工作组 16x16: 120 x 68 = 8160 个工作组, 前 8040 个是满的
/// (最后一行只有 8 行像素有效)。重循环工作组数不超过它, 每个都是同样多的活。
pub const FULL_WG: u32 = 8040;

fn spv_words(spv: &[u8]) -> Result<Vec<u32>, String> {
    if spv.len() < 20 || !spv.len().is_multiple_of(4) {
        return Err(format!("SPIR-V 长度 {} 不合法", spv.len()));
    }
    let w: Vec<u32> = spv
        .chunks_exact(4)
        .map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect();
    if w[0] != SPV_MAGIC {
        return Err("不是小端 SPIR-V (魔数不对)".into());
    }
    Ok(w)
}

/// 遍历指令流, 对每条指令回调 (起始下标, 操作码, 字数)。指令长度非法即报错。
fn walk(w: &[u32], mut f: impl FnMut(usize, u32, usize)) -> Result<(), String> {
    let mut i = 5;
    while i < w.len() {
        let wc = (w[i] >> 16) as usize;
        let op = w[i] & 0xffff;
        if wc == 0 || i + wc > w.len() {
            return Err(format!("第 {i} 字处指令长度 {wc} 非法"));
        }
        f(i, op, wc);
        i += wc;
    }
    Ok(())
}

/// SpecId → OpSpecConstant 所在下标。每个 SpecId 必须恰好对应一条 32 位 OpSpecConstant。
fn spec_slots(w: &[u32]) -> Result<Vec<(u32, usize)>, String> {
    let mut ids: Vec<(u32, u32)> = Vec::new(); // (spec, result id)
    walk(w, |i, op, wc| {
        if op == OP_DECORATE && wc >= 4 && w[i + 2] == DEC_SPEC_ID {
            ids.push((w[i + 3], w[i + 1]));
        }
    })?;
    let mut out = Vec::new();
    for &(spec, id) in &ids {
        if ids.iter().filter(|(s, _)| *s == spec).count() != 1 {
            return Err(format!("SpecId {spec} 被声明了不止一次"));
        }
        let mut hits = Vec::new();
        walk(w, |i, op, wc| {
            if op == OP_SPEC_CONSTANT && wc == 4 && w[i + 2] == id {
                hits.push(i + 3);
            }
        })?;
        if hits.len() != 1 {
            return Err(format!(
                "SpecId {spec} (%{id}) 对应的 32 位 OpSpecConstant 有 {} 处 (应恰好 1 处)",
                hits.len()
            ));
        }
        out.push((spec, hits[0]));
    }
    Ok(out)
}

/// 读出全部 32 位特化常量的缺省值 (SpecId 升序)。
pub fn read_spec(spv: &[u8]) -> Result<Vec<(u32, u32)>, String> {
    let w = spv_words(spv)?;
    let mut v: Vec<(u32, u32)> = spec_slots(&w)?.into_iter().map(|(s, i)| (s, w[i])).collect();
    v.sort();
    Ok(v)
}

/// 改写特化常量的缺省值。要改的 SpecId 不存在就整份拒绝 —— 宁可跑不起来,
/// 也不能装上一份「以为改了其实没改」的负载 (那样 A/B 就悄悄变成了 A/A)。
pub fn patch_spec(spv: &[u8], sets: &[(u32, u32)]) -> Result<Vec<u8>, String> {
    let mut w = spv_words(spv)?;
    let slots = spec_slots(&w)?;
    for &(spec, val) in sets {
        let Some(&(_, idx)) = slots.iter().find(|(s, _)| *s == spec) else {
            return Err(format!("模块里没有 SpecId {spec}"));
        };
        w[idx] = val;
    }
    Ok(w.iter().flat_map(|x| x.to_le_bytes()).collect())
}

fn sha256_hex(b: &[u8]) -> String {
    let d = Sha256::digest(b);
    d.iter().map(|x| format!("{x:02x}")).collect()
}

// ══════════════ 负载与考题 ══════════════

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Load {
    pub heavy_wg: u32,
    pub iters: u32,
    pub downs: u32,
}

impl Load {
    pub fn name(&self) -> String {
        format!("wg{}_it{}_ds{}", self.heavy_wg, self.iters, self.downs)
    }
    pub fn to_json(self) -> Value {
        json!({ "heavy_wg": self.heavy_wg, "iters": self.iters, "downs": self.downs })
    }
    pub fn from_json(v: &Value) -> Option<Load> {
        Some(Load {
            heavy_wg: v["heavy_wg"].as_u64()? as u32,
            iters: v["iters"].as_u64()? as u32,
            downs: v["downs"].as_u64()? as u32,
        })
    }
    pub fn bytes(&self, template: &[u8]) -> Result<Vec<u8>, String> {
        if self.heavy_wg > FULL_WG {
            return Err(format!("heavy_wg {} 超过满工作组数 {FULL_WG}", self.heavy_wg));
        }
        if self.iters == 0 || self.downs == 0 {
            return Err("iters / downs 必须 >= 1".into());
        }
        patch_spec(
            template,
            &[(SPEC_HEAVY_WG, self.heavy_wg), (SPEC_ITERS, self.iters), (SPEC_DOWNS, self.downs)],
        )
    }
}

/// 帧时 ≈ t0 + k·heavy_wg 的线性标定 (同 iters / 同场景强度下)。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Calib {
    pub t0_ms: f64,
    pub k_ms_per_wg: f64,
}

impl Calib {
    pub fn t(&self, wg: u32) -> f64 {
        self.t0_ms + self.k_ms_per_wg * wg as f64
    }
    /// 让帧时相对 A 臂变化 `pct`% (负 = 变快) 所需的 B 臂重循环工作组数。
    pub fn wg_for(&self, a_wg: u32, pct: f64) -> Result<u32, String> {
        if self.k_ms_per_wg <= 0.0 {
            return Err(format!("标定斜率 {} 不为正: 重循环没在帧时上留下痕迹", self.k_ms_per_wg));
        }
        let target = self.t(a_wg) * (1.0 + pct / 100.0);
        let wg = ((target - self.t0_ms) / self.k_ms_per_wg).round();
        if wg < 0.0 || wg > FULL_WG as f64 {
            return Err(format!("帧时变化 {pct}% 需要 heavy_wg={wg}, 超出 0..={FULL_WG}"));
        }
        Ok(wg as u32)
    }
    /// 按标定模型, B 臂相对 A 臂的预期帧时变化 (%)。
    pub fn expected_pct(&self, a_wg: u32, b_wg: u32) -> f64 {
        (self.t(b_wg) - self.t(a_wg)) / self.t(a_wg) * 100.0
    }
    pub fn to_json(self) -> Value {
        json!({ "t0_ms": self.t0_ms, "k_ms_per_wg": self.k_ms_per_wg })
    }
    pub fn from_json(v: &Value) -> Option<Calib> {
        Some(Calib { t0_ms: v["t0_ms"].as_f64()?, k_ms_per_wg: v["k_ms_per_wg"].as_f64()? })
    }
}

/// 最小二乘拟合 帧时 = t0 + k·wg。少于两个不同的 wg 拟合不了。
pub fn fit_calib(points: &[(u32, f64)]) -> Option<Calib> {
    let n = points.len() as f64;
    if points.len() < 2 {
        return None;
    }
    let mx = points.iter().map(|p| p.0 as f64).sum::<f64>() / n;
    let my = points.iter().map(|p| p.1).sum::<f64>() / n;
    let sxx: f64 = points.iter().map(|p| (p.0 as f64 - mx).powi(2)).sum();
    if sxx == 0.0 {
        return None;
    }
    let sxy: f64 = points.iter().map(|p| (p.0 as f64 - mx) * (p.1 - my)).sum();
    let k = sxy / sxx;
    Some(Calib { t0_ms: my - k * mx, k_ms_per_wg: k })
}

#[derive(Clone, Debug, PartialEq)]
pub struct Case {
    pub id: String,
    /// aa / better / worse
    pub kind: String,
    /// 构造给定的帧时变化 (%, 负 = 变快)。aa 为 0; 用工作组数直接给出且没有标定时为 None
    pub nominal_pct: Option<f64>,
    pub a: Load,
    pub b: Load,
}

impl Case {
    pub fn to_json(&self) -> Value {
        json!({
            "id": self.id, "kind": self.kind, "nominal_pct": self.nominal_pct,
            "a": self.a.to_json(), "b": self.b.to_json(),
        })
    }
    pub fn from_json(v: &Value) -> Option<Case> {
        Some(Case {
            id: v["id"].as_str()?.to_string(),
            kind: v["kind"].as_str()?.to_string(),
            nominal_pct: v["nominal_pct"].as_f64(),
            a: Load::from_json(&v["a"])?,
            b: Load::from_json(&v["b"])?,
        })
    }
}

fn pct_tag(p: f64) -> String {
    format!("{:.2}", p.abs()).trim_end_matches('0').trim_end_matches('.').replace('.', "p")
}

/// 考题清单: `aa*4,better:1*2,worse:2,better-wg:50`。
///
/// `better:<pct>` / `worse:<pct>` 是帧时变化幅度, 要标定换成工作组数;
/// `better-wg:<n>` / `worse-wg:<n>` 直接给工作组数差。`*k` 是重复 k 次 (各自独立跑)。
pub fn parse_cases(spec: &str, base: Load, calib: Option<&Calib>) -> Result<Vec<Case>, String> {
    let mut out: Vec<Case> = Vec::new();
    for item in spec.split(',').map(str::trim).filter(|s| !s.is_empty()) {
        let (body, reps) = match item.split_once('*') {
            Some((b, r)) => (b, r.parse::<u32>().map_err(|_| format!("重复次数不对: {item}"))?),
            None => (item, 1),
        };
        let (kind, arg) = match body.split_once(':') {
            Some((k, a)) => (k, Some(a)),
            None => (body, None),
        };
        let (kind, b, nominal, tag) = match (kind, arg) {
            ("aa", None) => ("aa", base, Some(0.0), "aa".to_string()),
            ("better" | "worse", Some(a)) => {
                let pct: f64 = a.parse().map_err(|_| format!("幅度不是数: {item}"))?;
                if pct.is_nan() || pct <= 0.0 {
                    return Err(format!("幅度必须为正 (方向由 better/worse 给): {item}"));
                }
                let c = calib.ok_or(format!("{item}: 按帧时幅度出题要先标定 (--calib)"))?;
                let signed = if kind == "better" { -pct } else { pct };
                let wg = c.wg_for(base.heavy_wg, signed)?;
                (kind, Load { heavy_wg: wg, ..base }, Some(c.expected_pct(base.heavy_wg, wg)), format!("{kind}{}", pct_tag(pct)))
            }
            ("better-wg" | "worse-wg", Some(a)) => {
                let d: u32 = a.parse().map_err(|_| format!("工作组数不对: {item}"))?;
                let k = if kind == "better-wg" { "better" } else { "worse" };
                let wg = if k == "better" {
                    base.heavy_wg.checked_sub(d).ok_or(format!("{item}: 减过头了"))?
                } else {
                    base.heavy_wg + d
                };
                if wg > FULL_WG {
                    return Err(format!("{item}: heavy_wg {wg} 超过 {FULL_WG}"));
                }
                (k, Load { heavy_wg: wg, ..base }, calib.map(|c| c.expected_pct(base.heavy_wg, wg)), format!("{k}wg{d}"))
            }
            _ => return Err(format!("不认识的考题 {item} (aa / better:<pct> / worse:<pct> / better-wg:<n> / worse-wg:<n>)")),
        };
        for _ in 0..reps {
            let n = out.iter().filter(|c| c.id.starts_with(&format!("{tag}_"))).count() + 1;
            out.push(Case { id: format!("{tag}_{n}"), kind: kind.to_string(), nominal_pct: nominal, a: base, b });
        }
    }
    if out.is_empty() {
        return Err("考题清单为空".into());
    }
    Ok(out)
}

// ══════════════ 判定 (纯函数, 只读证据) ══════════════

/// ABBA 次序与 eval / autoloop 一致: 奇数轮先 knob 后 ctrl, 偶数轮反过来。
pub fn arm_order(round: usize) -> [&'static str; 2] {
    if round % 2 == 1 {
        ["knob", "ctrl"]
    } else {
        ["ctrl", "knob"]
    }
}

fn read_json(p: &Path) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(p).ok()?).ok()
}

/// 一臂的证据: (标签, {summary, env, arm})。
pub type ArmRec = (String, Value);

fn load_arm(case_dir: &Path, label: &str) -> Option<ArmRec> {
    let d = case_dir.join(label);
    let summary = read_json(&d.join("summary.json"))?;
    let env = read_json(&d.join("env.json")).unwrap_or(Value::Null);
    let arm = read_json(&d.join("arm.json")).unwrap_or(Value::Null);
    Some((label.to_string(), json!({ "summary": summary, "env": env, "arm": arm })))
}

/// 判定视图: summary 展平再并上 env 的功耗/温度 (eval / autoloop 同一个 run_view)。
fn runs_view(rs: &[ArmRec]) -> Vec<loopstat::Run> {
    rs.iter().map(|(l, m)| (l.clone(), crate::eval::run_view(m))).collect()
}

const JUDGE_METRICS: [&str; 3] = ["frame_p95", "fps_mean", "power_w_mean"];

/// 前 `n` 轮的两臂 (ctrl = A 对照, knob = B 考题臂)。缺任何一臂返回 Err(缺的标签)。
pub fn arms_prefix(case_dir: &Path, n: usize) -> Result<(Vec<ArmRec>, Vec<ArmRec>), String> {
    let (mut a, mut b) = (Vec::new(), Vec::new());
    for i in 1..=n {
        for arm in arm_order(i) {
            let label = format!("{arm}{i}");
            let rec = load_arm(case_dir, &label).ok_or(label)?;
            if arm == "knob" {
                b.push(rec);
            } else {
                a.push(rec);
            }
        }
    }
    Ok((a, b))
}

/// 一臂的负载是否真的生效 (算子装上、干净跑完)。有逐项 checks 时按 checks 算 ——
/// 2026-10-21 之前的 arm.json 把 capture_ok 并进了 apply_ok, 这样旧证据也能分开重判。
pub fn arm_apply_ok(m: &Value) -> bool {
    match m["arm"]["checks"].as_object().filter(|o| !o.is_empty()) {
        Some(o) => o.values().all(|v| v == &json!(true)),
        None => m["arm"]["apply_ok"].as_bool() == Some(true),
    }
}

/// 一臂的采集窗够不够 (没记这一项的旧证据算够)。
pub fn arm_capture_ok(m: &Value) -> bool {
    m["arm"]["capture_ok"].as_bool() != Some(false)
}

/// 剔除采集失败的那几对 (两臂同轮, 按 arms_prefix 的顺序一一对应)。回 (A, B, 被剔除的对的标签)。
/// 只剔这一对, 不让一臂采集失败把整道题作废 (审计 #16)。
pub fn drop_capture_failed(a: &[ArmRec], b: &[ArmRec]) -> (Vec<ArmRec>, Vec<ArmRec>, Vec<String>) {
    let (mut ka, mut kb, mut dropped) = (Vec::new(), Vec::new(), Vec::new());
    for (x, y) in a.iter().zip(b.iter()) {
        if arm_capture_ok(&x.1) && arm_capture_ok(&y.1) {
            ka.push(x.clone());
            kb.push(y.clone());
        } else {
            dropped.push(format!("{}+{}", y.0, x.0));
        }
    }
    (ka, kb, dropped)
}

/// 走现有判定链: loopstat::compare + sysparam::decide。
///
/// `scale`: 只给注入法用 —— 把 B 臂帧时乘上 (1+δ)、帧率除以 (1+δ), 模拟一个
/// 已知幅度的效应叠在真实噪声上。真考题传 None。
pub fn judge_arms(
    a: &[ArmRec],
    b: &[ArmRec],
    temp_cap_c: f64,
    snapshot_ok: bool,
    scale: Option<f64>,
) -> PyVal {
    let scaled: Vec<ArmRec>;
    let b = match scale {
        None => b,
        Some(f) => {
            scaled = b
                .iter()
                .map(|(l, m)| {
                    let mut m = m.clone();
                    if let Some(x) = m["summary"]["frame_p95"].as_f64() {
                        m["summary"]["frame_p95"] = json!(x * f);
                    }
                    if let Some(x) = m["summary"]["fps_mean"].as_f64() {
                        m["summary"]["fps_mean"] = json!(x / f);
                    }
                    (l.clone(), m)
                })
                .collect();
            &scaled
        }
    };
    let (ap, bp) = (runs_view(a), runs_view(b));
    let comparisons = PyVal::Obj(
        JUDGE_METRICS
            .iter()
            .map(|m| {
                // 注入法扫效应只要判定 (decide 不读 ci95), 省掉置换区间; 真机考题照常出区间
                let c = if scale.is_some() { loopstat::compare_no_ci(&ap, &bp, m) } else { loopstat::compare(&ap, &bp, m) };
                (m.to_string(), c)
            })
            .collect(),
    );
    let all: Vec<&ArmRec> = a.iter().chain(b.iter()).collect();
    // 功耗是否进判定: 与 eval / autoloop 的 auto 口径一致 —— 两臂全程放电态且量到了
    let power_available = !all.is_empty()
        && all.iter().all(|(_, m)| m["env"]["power_w_mean"].as_f64().is_some())
        && sysparam::power_group_ok(all.iter().map(|(_, m)| &m["env"]));
    let apply_ok = all.iter().all(|(_, m)| arm_apply_ok(m));
    let temp_max = all
        .iter()
        .filter_map(|(_, m)| m["env"]["soc_temp_max_c"].as_f64())
        .reduce(f64::max);
    let mut d = sysparam::decide(
        &comparisons,
        &DecideCtx {
            temp_max_c: temp_max.map(PyVal::Float).unwrap_or(PyVal::Null),
            temp_cap_c: PyVal::Float(temp_cap_c),
            apply_ok,
            snapshot_identical: snapshot_ok,
            power_available,
        },
    );
    if let PyVal::Obj(kv) = &mut d {
        kv.push(("power_in_verdict".into(), PyVal::Bool(power_available)));
        kv.push(("n_pairs".into(), PyVal::Int(a.len().min(b.len()) as i64)));
    }
    d
}

fn pv(v: &PyVal) -> Value {
    serde_json::from_str(&dumps(v).replace("NaN", "null").replace("Infinity", "null")).unwrap_or(Value::Null)
}

/// 本组设备快照核对 (与 autoloop 同一口径): 本组不碰任何快照里的键, 所以只要求
/// 快照确实比过了; 严格逐行差异照记 (热态派生的那几行会随温度变, 不算留痕)。
fn snapshot_check(case_dir: &Path) -> (bool, Value) {
    let sd = snapshot_diff(
        &case_dir.join("snap_before.txt").to_string_lossy(),
        &case_dir.join("snap_after.txt").to_string_lossy(),
    );
    let cd = autoloop::classify_diff(&sd, Some(&[]));
    let checked = matches!(cd.get("checked"), Some(PyVal::Bool(true)));
    let ours = matches!(cd.get("ours_identical"), Some(PyVal::Bool(true)));
    (checked && ours, pv(&cd))
}

/// 一道考题在前 n 轮上的判定 + 按考题类别的对错。
pub fn judge_case(case_dir: &Path, n: usize, temp_cap_c: f64) -> Value {
    let Some(case) = read_json(&case_dir.join("case.json")).and_then(|v| Case::from_json(&v)) else {
        return json!({ "n": n, "verdict": "ABORT", "reason": "case.json 缺失或不可解析" });
    };
    let (a, b) = match arms_prefix(case_dir, n) {
        Ok(x) => x,
        Err(missing) => {
            return json!({
                "n": n, "verdict": "ABORT", "outcome": "abort",
                "reason": format!("前 {n} 轮缺 {missing} (该臂没跑成或本组中途作废)"),
            })
        }
    };
    let (a, b, capture_dropped) = drop_capture_failed(&a, &b);
    if a.is_empty() {
        return json!({
            "n": n, "verdict": "ABORT", "outcome": "abort",
            "reason": format!("前 {n} 轮每一对都有一臂采集失败 (capture_ok=false), 全部剔除"),
            "capture_dropped_pairs": capture_dropped,
        });
    }
    let (snap_ok, _) = snapshot_check(case_dir);
    let d = pv(&judge_arms(&a, &b, temp_cap_c, snap_ok, None));
    let mut out = d.clone();
    out["n"] = json!(n);
    // 采集失败只剔除那一对, 剔了哪几对如实列出 (n_pairs 是剔除后实际进判定的对数)
    out["capture_dropped_pairs"] = json!(capture_dropped);
    out["outcome"] = json!(classify(&case.kind, &d));
    // 只标出来、不改判定: GPU 档位在采集窗里被改过的臂 (见 read_pwr)
    let changed: Vec<String> = a
        .iter()
        .chain(b.iter())
        .filter(|(_, m)| m["arm"]["pwr"]["changed"] == json!(true))
        .map(|(l, _)| l.clone())
        .collect();
    out["pwr_changed_arms"] = json!(changed);
    out
}

/// 判定结果按考题类别归类。
///
/// - aa:     keep → 「误判改善」; 有显著变差 → 「误判变差」; 其余 → 「正确」
/// - better: keep → 「检出」; 有显著变差 → 「反判」; 其余 → 「漏检」
/// - worse:  有显著变差 → 「检出」; keep → 「反判」; 其余 → 「漏检」
pub fn classify(kind: &str, d: &Value) -> &'static str {
    let verdict = d["verdict"].as_str().unwrap_or("ABORT");
    if verdict == "ABORT" {
        return "abort";
    }
    let keep = verdict == "KEEP";
    let regressed = d["regressions"].as_array().is_some_and(|r| !r.is_empty());
    match kind {
        "aa" => {
            if keep {
                "false_keep"
            } else if regressed {
                "false_regression"
            } else {
                "correct"
            }
        }
        "better" => {
            if keep {
                "detected"
            } else if regressed {
                "reversed"
            } else {
                "missed"
            }
        }
        "worse" => {
            if regressed {
                "detected"
            } else if keep {
                "reversed"
            } else {
                "missed"
            }
        }
        _ => "unknown",
    }
}

/// 注入法: 在 A/A 考题的真实噪声上叠一个已知效应 (B 臂帧时 ×(1-δ)), 逐个 n 轮滑窗走
/// 同一条判定链, 数 KEEP 的比例。δ 网格 0.25% 一档到 10%。
///
/// 这只是对真机检出率曲线的平滑补充 (假设效应是乘性的、不改变噪声与功耗),
/// 报告里与真机已知幅度考题的实测检出率并排给出, 两者对不上就以实测为准。
pub fn injection_curve(aa_dirs: &[PathBuf], n: usize, temp_cap_c: f64) -> Value {
    // 每道 A/A 考题取所有 n 轮滑窗 (起点 1..=P-n+1)
    let mut windows: Vec<(Vec<ArmRec>, Vec<ArmRec>, bool)> = Vec::new();
    for dir in aa_dirs {
        let p = completed_pairs(dir);
        if p < n {
            continue;
        }
        let Ok((a_all, b_all)) = arms_prefix(dir, p) else { continue };
        let (snap_ok, _) = snapshot_check(dir);
        for s in 0..=(p - n) {
            let (a, b, _) = drop_capture_failed(&a_all[s..s + n], &b_all[s..s + n]);
            if !a.is_empty() {
                windows.push((a, b, snap_ok));
            }
        }
    }
    if windows.is_empty() {
        return json!({ "n": n, "windows": 0, "curve": [], "mde80_pct": null });
    }
    let mut curve = Vec::new();
    let mut mde: Option<f64> = None;
    for step in 0..=40 {
        let delta = step as f64 * 0.25;
        let f = 1.0 - delta / 100.0;
        let keeps = windows
            .iter()
            .filter(|(a, b, ok)| {
                let d = judge_arms(a, b, temp_cap_c, *ok, Some(f));
                matches!(d.get("verdict"), Some(PyVal::Str(s)) if s == "KEEP")
            })
            .count();
        let rate = keeps as f64 / windows.len() as f64;
        if mde.is_none() && delta > 0.0 && rate >= 0.8 {
            mde = Some(delta);
        }
        curve.push(json!({ "delta_pct": delta, "keep": keeps, "rate": py_round(rate, 3) }));
    }
    json!({ "n": n, "windows": windows.len(), "curve": curve, "mde80_pct": mde })
}

/// 本组跑完了几轮 (两臂都在才算一轮)。
pub fn completed_pairs(case_dir: &Path) -> usize {
    let mut i = 0;
    while load_arm(case_dir, &format!("knob{}", i + 1)).is_some()
        && load_arm(case_dir, &format!("ctrl{}", i + 1)).is_some()
    {
        i += 1;
    }
    i
}

fn case_dirs(root: &Path) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = std::fs::read_dir(root)
        .map(|it| {
            it.filter_map(|e| e.ok().map(|e| e.path()))
                .filter(|p| p.join("case.json").is_file())
                .collect()
        })
        .unwrap_or_default();
    v.sort();
    v
}

/// 汇总: 逐题判定 (n = 5 / 8 / 10 前缀) + 混淆矩阵 + 检出率表 + A/A 噪声 + 注入法 MDE + 时间账。
pub fn report(root: &Path, ns: &[usize]) -> Value {
    let plan = read_json(&root.join("plan.json")).unwrap_or(Value::Null);
    let temp_cap = plan["conditions"]["temp_cap_c"].as_f64().unwrap_or(70.0);
    let dirs = case_dirs(root);
    let mut cases = Vec::new();
    let mut arm_secs: Vec<f64> = Vec::new();
    let mut aa_dirs = Vec::new();
    for dir in &dirs {
        let case = read_json(&dir.join("case.json")).unwrap_or(Value::Null);
        let kind = case["kind"].as_str().unwrap_or("?").to_string();
        let p = completed_pairs(dir);
        if kind == "aa" {
            aa_dirs.push(dir.clone());
        }
        let mut per_n = Vec::new();
        for &n in ns {
            if n <= p.max(1) && n >= 2 {
                let j = judge_case(dir, n, temp_cap);
                per_n.push(json!({
                    "n": n,
                    "verdict": j["verdict"], "outcome": j["outcome"], "reason": j["reason"],
                    "wins": j["wins"], "regressions": j["regressions"],
                    "power_in_verdict": j["power_in_verdict"], "temp_max_c": j["temp_max_c"],
                    "capture_dropped_pairs": j["capture_dropped_pairs"], "n_pairs": j["n_pairs"],
                    "frame_p95": metric_brief(&j, "frame_p95"),
                    "fps_mean": metric_brief(&j, "fps_mean"),
                    "power_w_mean": metric_brief(&j, "power_w_mean"),
                }));
            }
        }
        for i in 1..=p {
            for arm in ["knob", "ctrl"] {
                if let Some(s) = read_json(&dir.join(format!("{arm}{i}")).join("arm.json"))
                    .and_then(|v| v["seconds_total"].as_f64())
                {
                    arm_secs.push(s);
                }
            }
        }
        let (_, snap) = snapshot_check(dir);
        cases.push(json!({
            "id": case["id"], "kind": kind, "nominal_pct": case["nominal_pct"],
            "a": case["a"], "b": case["b"], "pairs_completed": p,
            "aborted": read_json(&dir.join("abort.json")),
            "snapshot": { "ours_identical": snap["ours_identical"], "strict_identical": snap["strict_identical"], "strict_n_diff": snap["strict_n_diff"] },
            "judged": per_n,
        }));
    }

    // 混淆矩阵: 真值 (aa/better/worse) × 判定 (KEEP / REJECT+显著变差 / REJECT 无信号 / ABORT)
    let mut matrices = Vec::new();
    let mut detection = Vec::new();
    for &n in ns {
        let mut m: Vec<(String, [usize; 4])> = Vec::new();
        let mut det: Vec<(String, Option<f64>, usize, usize)> = Vec::new(); // (kind+nominal, nominal, hit, total)
        for c in &cases {
            let Some(j) = c["judged"].as_array().and_then(|v| v.iter().find(|x| x["n"] == json!(n))) else {
                continue;
            };
            let kind = c["kind"].as_str().unwrap_or("?").to_string();
            let col = match (j["verdict"].as_str(), j["regressions"].as_array().is_some_and(|r| !r.is_empty())) {
                (Some("KEEP"), _) => 0,
                (Some("REJECT"), true) => 1,
                (Some("REJECT"), false) => 2,
                _ => 3,
            };
            match m.iter_mut().find(|(k, _)| *k == kind) {
                Some((_, row)) => row[col] += 1,
                None => {
                    let mut row = [0; 4];
                    row[col] += 1;
                    m.push((kind.clone(), row));
                }
            }
            if col == 3 {
                continue; // ABORT 不进检出率分母: 没量成不是证据
            }
            let nominal = c["nominal_pct"].as_f64().map(|x| py_round(x, 2));
            let key = format!("{kind} {}", nominal.map(|x| format!("{x:+.2}%")).unwrap_or_else(|| c["b"]["heavy_wg"].to_string()));
            let hit = match kind.as_str() {
                "aa" => (j["outcome"] != json!("correct")) as usize,
                _ => (j["outcome"] == json!("detected")) as usize,
            };
            match det.iter_mut().find(|(k, ..)| *k == key) {
                Some(e) => {
                    e.2 += hit;
                    e.3 += 1;
                }
                None => det.push((key, nominal, hit, 1)),
            }
        }
        det.sort_by(|x, y| x.0.split(' ').next().cmp(&y.0.split(' ').next()).then(
            x.1.map(f64::abs).partial_cmp(&y.1.map(f64::abs)).unwrap_or(std::cmp::Ordering::Equal)));
        matrices.push(json!({
            "n": n,
            "columns": ["KEEP", "REJECT(有显著变差)", "REJECT(无显著信号)", "ABORT"],
            "rows": m.iter().map(|(k, r)| json!({ "truth": k, "counts": r })).collect::<Vec<_>>(),
        }));
        detection.push(json!({
            "n": n,
            "note": "aa 行的 rate 是误判率 (KEEP 或报显著变差); better/worse 行是检出率",
            "rows": det.iter().map(|(k, nom, hit, tot)| json!({
                "case": k, "nominal_pct": nom, "hits": hit, "total": tot,
                "rate": py_round(*hit as f64 / *tot as f64, 3),
            })).collect::<Vec<_>>(),
        }));
    }

    // A/A 噪声: 每轮指标的离散 (同一负载两臂合并)
    let mut noise = Vec::new();
    for dir in &aa_dirs {
        let p = completed_pairs(dir);
        if let Ok((a, b)) = arms_prefix(dir, p) {
            let all: Vec<ArmRec> = a.into_iter().chain(b).collect();
            let runs = runs_view(&all);
            let d = loopstat::describe(&runs, &dir.file_name().unwrap_or_default().to_string_lossy());
            let pick = |m: &str| {
                let x = d.get("metrics").and_then(|ms| ms.get(m)).map(pv).unwrap_or(Value::Null);
                json!({ "mean": x["mean"], "dispersion_pct": x["dispersion_pct"], "cv_pct": cv_pct(&x["values"]), "drift": x["drift"] })
            };
            noise.push(json!({
                "case": dir.file_name().map(|s| s.to_string_lossy().to_string()),
                "runs": p * 2,
                "frame_p95": pick("frame_p95"), "fps_mean": pick("fps_mean"), "frame_p50": pick("frame_p50"),
            }));
        }
    }

    let injection: Vec<Value> = ns.iter().map(|&n| injection_curve(&aa_dirs, n, temp_cap)).collect();
    let arm_mean = (!arm_secs.is_empty()).then(|| arm_secs.iter().sum::<f64>() / arm_secs.len() as f64);
    let time_cost: Vec<Value> = ns
        .iter()
        .map(|&n| {
            json!({
                "n": n,
                "minutes_per_experiment": arm_mean.map(|s| py_round(s * 2.0 * n as f64 / 60.0, 1)),
                "mde80_injection_pct": injection.iter().find(|x| x["n"] == json!(n)).map(|x| x["mde80_pct"].clone()),
            })
        })
        .collect();

    json!({
        "selftest_version": 1,
        "plan": plan,
        "calib": read_json(&root.join("calib.json")),
        "rule": pv(&sysparam::rule_doc(&PyVal::Float(temp_cap), &PyVal::Int(5), true, "selftest: 功耗是否进判定按每组实测放电态自动定")),
        "cases": cases,
        "confusion": matrices,
        "detection": detection,
        "aa_noise": noise,
        "injection": injection,
        "time_cost": { "arm_seconds_mean": arm_mean.map(|s| py_round(s, 1)), "per_n": time_cost },
        "quality": read_json(&root.join("quality.json")),
    })
}

fn cv_pct(values: &Value) -> Value {
    let xs: Vec<f64> = values.as_array().map(|a| a.iter().filter_map(|v| v.as_f64()).collect()).unwrap_or_default();
    if xs.len() < 2 {
        return Value::Null;
    }
    let m = xs.iter().sum::<f64>() / xs.len() as f64;
    let var = xs.iter().map(|x| (x - m).powi(2)).sum::<f64>() / (xs.len() - 1) as f64;
    if m == 0.0 {
        return Value::Null;
    }
    json!(py_round(var.sqrt() / m.abs() * 100.0, 3))
}

fn metric_brief(j: &Value, m: &str) -> Value {
    let x = &j["per_metric"][m];
    if x.is_null() {
        return Value::Null;
    }
    json!({ "status": x["status"], "diff_pct": x["diff_pct"], "p": x["p"] })
}

/// 人读版。
pub fn render(r: &Value) -> String {
    let mut s = String::new();
    let p = &r["plan"]["conditions"];
    s.push_str(&format!(
        "refbench 判定链自检  (负载 {} | 场景强度 {} | iters {} | A 臂 heavy_wg {} | 采集 {}s 预热 {}s | 等冷 {}C | 停充 {} | 锁频 {})\n",
        r["plan"]["template_sha256"].as_str().map(|x| &x[..12.min(x.len())]).unwrap_or("?"),
        p["intensity"], p["iters"], p["base_wg"], p["capdur_s"], p["warmup_s"], p["cool_c"],
        p["charge_suspend"], p["lock_freq"],
    ));
    if let Some(c) = r["calib"].as_object() {
        s.push_str(&format!(
            "标定: 帧时(p50) = {:.4} ms + {:.6} ms × heavy_wg\n",
            c.get("t0_ms").and_then(|v| v.as_f64()).unwrap_or(f64::NAN),
            c.get("k_ms_per_wg").and_then(|v| v.as_f64()).unwrap_or(f64::NAN)
        ));
    }
    s.push_str("\n逐题 (n=轮数/臂):\n");
    for c in r["cases"].as_array().into_iter().flatten() {
        for j in c["judged"].as_array().into_iter().flatten() {
            s.push_str(&format!(
                "  {:<14} n={:<2} 预期 {:>7}  p95 {:>7}% (p={:<8}) fps {:>7}% (p={:<8}) 功耗 {:>7}% (p={:<8}) → {:<6} {}\n",
                c["id"].as_str().unwrap_or("?"),
                j["n"],
                c["nominal_pct"].as_f64().map(|x| format!("{x:+.2}%")).unwrap_or_else(|| "-".into()),
                j["frame_p95"]["diff_pct"], j["frame_p95"]["p"],
                j["fps_mean"]["diff_pct"], j["fps_mean"]["p"],
                j["power_w_mean"]["diff_pct"], j["power_w_mean"]["p"],
                j["verdict"].as_str().unwrap_or("?"),
                j["outcome"].as_str().unwrap_or(""),
            ));
            if let Some(v) = j["pwr_changed_arms"].as_array().filter(|v| !v.is_empty()) {
                s.push_str(&format!("      ⚠ GPU 档位在采集中被改过的臂: {}\n", v.iter().filter_map(|x| x.as_str()).collect::<Vec<_>>().join(" ")));
            }
        }
    }
    s.push_str("\n混淆矩阵 (行=真值, 列=KEEP / REJECT有显著变差 / REJECT无信号 / ABORT):\n");
    for m in r["confusion"].as_array().into_iter().flatten() {
        s.push_str(&format!("  n={}\n", m["n"]));
        for row in m["rows"].as_array().into_iter().flatten() {
            s.push_str(&format!("    {:<7} {}\n", row["truth"].as_str().unwrap_or("?"), row["counts"]));
        }
    }
    s.push_str("\n检出率 (aa 行是误判率):\n");
    for d in r["detection"].as_array().into_iter().flatten() {
        for row in d["rows"].as_array().into_iter().flatten() {
            s.push_str(&format!(
                "  n={:<2} {:<16} {}/{} = {}\n",
                d["n"], row["case"].as_str().unwrap_or("?"), row["hits"], row["total"], row["rate"]
            ));
        }
    }
    s.push_str("\nA/A 噪声 (每轮指标, 两臂合并):\n");
    for x in r["aa_noise"].as_array().into_iter().flatten() {
        s.push_str(&format!(
            "  {:<10} {} 轮  p95 CV {}%  p50 CV {}%  fps CV {}%  p95 漂移 {}\n",
            x["case"].as_str().unwrap_or("?"), x["runs"],
            x["frame_p95"]["cv_pct"], x["frame_p50"]["cv_pct"], x["fps_mean"]["cv_pct"],
            x["frame_p95"]["drift"],
        ));
    }
    s.push_str("\n注入法 MDE (在 A/A 噪声上叠已知效应, 检出率 >= 80% 的最小帧时变化) 与时间账:\n");
    for t in r["time_cost"]["per_n"].as_array().into_iter().flatten() {
        let inj = r["injection"].as_array().and_then(|v| v.iter().find(|x| x["n"] == t["n"]));
        s.push_str(&format!(
            "  n={:<2} 每次评测约 {} 分钟  MDE80 {}%  (滑窗 {} 个)\n",
            t["n"], t["minutes_per_experiment"], t["mde80_injection_pct"],
            inj.map(|x| x["windows"].clone()).unwrap_or(Value::Null),
        ));
    }
    if let Some(q) = r["quality"].as_object() {
        s.push_str("\n画面判据 (eval --request kind=shader → gpu-op 画质地板):\n");
        for (k, v) in q {
            if let Some(o) = v.as_object() {
                s.push_str(&format!(
                    "  {:<8} status={} 期望={} psnr={} 基线psnr={} pareto={}\n",
                    k,
                    o.get("status").cloned().unwrap_or(Value::Null),
                    o.get("expected").cloned().unwrap_or(Value::Null),
                    o.get("psnr_db").cloned().unwrap_or(Value::Null),
                    o.get("baseline_psnr_db").cloned().unwrap_or(Value::Null),
                    o.get("is_pareto_improvement").cloned().unwrap_or(Value::Null),
                ));
            }
        }
    }
    s
}

// ══════════════ 设备侧 ══════════════

#[derive(Clone, Debug)]
pub struct Cond {
    pub serial: String,
    pub template: PathBuf,
    pub intensity: u32,
    pub iters: u32,
    pub base_wg: u32,
    pub frames: u32,
    pub warmup_s: u64,
    pub capdur_s: u64,
    pub cool_c: f64,
    pub cool_timeout_s: u64,
    pub temp_cap_c: f64,
    pub charge_suspend: bool,
    pub lock_freq: bool,
    /// 锁频时钉在哪一档 (缺省: 锁频前的 max_pwrlevel)。档位号越大频率越低。
    pub lock_level: Option<u32>,
    /// 审计 #9: 只钉 GPU 档位 (kgsl min/max_pwrlevel), CPU 调速器与总线不动 (与 --lock-freq 互斥)。
    /// 2026-10-21 run1/run2 实测同一配置帧率在 231–347 fps 之间跳台阶, 就是档位没受控。
    pub lock_gpu_level: Option<u32>,
    /// 等冷时还要等 GPU 热限档 (kgsl thermal_pwrlevel) 回到 <= 这一档。None = 不等。
    ///
    /// 2026-10-21 红魔实测: SoC 结温 2 秒内就回到 45C 以下, 但 thermal_pwrlevel 一组里从 0
    /// 爬到 4、6、7 不回落, 同一负载帧率随之 347 → 306 → 258 台阶式下降; 一轮两臂落在不同档时
    /// (ctrl 6 / knob 4) 方向直接判反。只看结温的等冷管不住这个。
    /// 缺省 4: 空闲 60 秒后实测多停在 4–6, 等回 0 会每臂等到超时。
    pub cool_tpl: Option<u32>,
}

impl Cond {
    fn to_json(&self) -> Value {
        json!({
            "serial": self.serial, "intensity": self.intensity, "iters": self.iters,
            "base_wg": self.base_wg, "frames": self.frames, "warmup_s": self.warmup_s,
            "capdur_s": self.capdur_s, "cool_c": self.cool_c, "cool_timeout_s": self.cool_timeout_s,
            "temp_cap_c": self.temp_cap_c, "charge_suspend": self.charge_suspend,
            "fan": "off",
            "lock_freq": self.lock_freq, "lock_level": self.lock_level, "lock_gpu_level": self.lock_gpu_level, "scene": rb::SCENE,
            "cool_tpl": self.cool_tpl,
        })
    }
}

fn log(msg: &str) {
    eprintln!("[selftest {}] {msg}", chrono::Local::now().format("%H:%M:%S"));
}

fn sh(serial: &str, cmd: &str, timeout: u64) -> String {
    adb(serial, &["shell", cmd], timeout)
}

fn strip_exit(out: &str) -> &str {
    match out.rfind("#adb_exit=") {
        Some(i) => &out[..i],
        None => out,
    }
}

/// 设备侧的会话: 把要还原的东西 (停充 / 风扇 / 前台) 收在一个 guard 里, 任何返回路径都还原。
struct Session {
    serial: String,
    dev: Device,
    charge_suspended: bool,
    lock_state: PathBuf,
}

fn fan_state_path(serial: &str) -> Result<PathBuf, String> {
    hwcond::lock_state_path("selftest-fan", &Some(serial.to_string()))
}

const KGSL: &str = "/sys/class/kgsl/kgsl-3d0";
const FAN_EN: &str = "/sys/kernel/fan/fan_enable";
const FAN_LV: &str = "/sys/kernel/fan/fan_speed_level";
const CHARGE_STATE: &str = "/data/local/tmp/loop_v1_charge.state";

/// 手机必须静音 (owner 规定)。媒体音量 (stream 3) 不是 0 就写 0; 返回读到的原值。
/// selftest 本身不出声, 这里只是收场核对, 防止别的流程留下的音量被带过去。
fn ensure_muted(serial: &str) -> String {
    let got = strip_exit(&sh(serial, "cmd media_session volume --stream 3 --get", 15)).to_string();
    // 回包形如 "volume is 0 in range [0..15]"
    let cur = got.split_whitespace().skip_while(|w| *w != "is").nth(1).unwrap_or("").to_string();
    if cur != "0" {
        sh(serial, "cmd audio set-volume 3 0", 15);
        log(&format!("媒体音量原为 {cur:?}, 已调到 0 (手机必须静音)"));
    }
    cur
}

fn write_fan_off(serial: &str) {
    su(serial, &format!("echo 0 > {FAN_EN}"), 15);
}

/// 设备上几项「改了要还原」的现值, 开场与收场各记一份进证据。
fn device_state(serial: &str) -> Value {
    let out = su(
        serial,
        &format!(
            "for f in {FAN_EN} {FAN_LV} /sys/class/qcom-battery/charging_enabled \
/sys/class/kgsl/kgsl-3d0/max_pwrlevel /sys/class/kgsl/kgsl-3d0/min_pwrlevel \
/sys/class/kgsl/kgsl-3d0/thermal_pwrlevel /sys/class/power_supply/battery/capacity; do echo \"$f=$(cat $f 2>/dev/null)\"; done; \
[ -f {CHARGE_STATE} ] && echo charge_state_file=present || echo charge_state_file=absent"
        ),
        20,
    );
    let mut m = serde_json::Map::new();
    for line in strip_exit(&out).lines() {
        if let Some((k, v)) = line.trim().split_once('=') {
            let k = k.rsplit('/').next().unwrap_or(k);
            m.insert(k.to_string(), json!(v));
        }
    }
    Value::Object(m)
}

impl Session {
    fn open(c: &Cond) -> Result<Session, String> {
        let lock_state = hwcond::lock_state_path("selftest", &Some(c.serial.clone()))?;
        let fan_state = fan_state_path(&c.serial)?;
        match std::fs::symlink_metadata(&fan_state) {
            Ok(_) => return Err(format!("遗留风扇状态 {} 的恢复方式未知, 保留原件并停止", fan_state.display())),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(format!("检查遗留风扇状态 {}: {e}", fan_state.display())),
        }
        let dev = Device::new(Some(c.serial.clone()), std::env::temp_dir().to_string_lossy().into_owned());
        if !hwcond::root_ok(&dev) {
            return Err("设备无 root: ftrace 与停充都需要 root".into());
        }
        if hwcond::recover_stale_lock("selftest", &dev, &lock_state)? == Some(false) {
            return Err("上次遗留的锁频态回滚失败, 拒绝继续部署或测量".into());
        }
        let pm = sh(&c.serial, &format!("pm list packages {}", rb::PACKAGE), 20);
        if !pm.contains(&format!("package:{}", rb::PACKAGE)) {
            return Err(format!("设备上没装 {} (见 refbench README 出包)", rb::PACKAGE));
        }
        push_tools(&c.serial)?;
        sh(&c.serial, "input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard", 15);
        // 前台若留着别的应用 (实测: 原神留在前台 60 fps 持续提交), 等冷期间它一直在发热、
        // refbench 退出后它又回到前台。回桌面让它进后台暂停渲染; 不杀进程、不动它的任何设置
        let top = sh(&c.serial, "dumpsys activity activities | grep -m1 topResumedActivity", 15);
        let top = strip_exit(&top).trim().to_string();
        if !top.is_empty() && !top.contains(rb::PACKAGE) && !top.to_lowercase().contains("launcher") {
            sh(&c.serial, "input keyevent KEYCODE_HOME", 15);
            log(&format!("前台原是 {top} —— 回桌面 (不杀进程)"));
        }
        let mut s = Session {
            serial: c.serial.clone(),
            dev,
            charge_suspended: false,
            lock_state,
        };
        // 锁频遗留已在部署前恢复。停充原值由设备端 charge_suspend.sh 另行保存。
        // 风扇一律不开 (owner 规定: 噪音)。开场无条件写 0 —— 旧版本 --fan-on 被杀/宕机后
        // 可能把风扇留在开着 (10-11 19:40 节点宕机那次就是), 不管谁留的都在这里关掉
        let fan_before = strip_exit(&su(&c.serial, &format!("cat {FAN_EN}"), 15)).trim().to_string();
        write_fan_off(&c.serial);
        if fan_before != "0" {
            log(&format!("风扇原为 enable={fan_before}, 已关 (风扇一律不开)"));
        }
        if su(&c.serial, &format!("[ -f {CHARGE_STATE} ] && echo STALE"), 15).contains("STALE") {
            let out = su(&c.serial, &format!("sh {DEV_TMP}/charge_suspend.sh restore"), 60);
            log(&format!("发现上次遗留的停充态, 先恢复充电: {}", strip_exit(&out).trim().lines().last().unwrap_or("")));
        }
        log(&format!("开场设备状态: {}", device_state(&c.serial)));
        // 不锁频时的测量条件是「GPU 频率上限为缺省 0 档」。别的通路被打断会把 max_pwrlevel
        // 留在中间档 (10-11 实测见过 17 / 12 / 3), 不归位的话整组都在降频下量。
        if !c.lock_freq {
            let mp = su(&c.serial, &format!("cat {KGSL}/max_pwrlevel"), 15);
            let mp = strip_exit(&mp).trim().to_string();
            if adb_ok(&su(&c.serial, "true", 10)) && !mp.is_empty() && mp != "0" {
                su(&c.serial, &format!("echo 0 > {KGSL}/max_pwrlevel"), 15);
                let now = su(&c.serial, &format!("cat {KGSL}/max_pwrlevel"), 15);
                log(&format!("GPU max_pwrlevel 遗留为 {mp}, 归位到 0 (回读 {})", strip_exit(&now).trim()));
            }
        }

        if c.charge_suspend {
            let out = su(&c.serial, &format!("sh {DEV_TMP}/charge_suspend.sh suspend"), 90);
            if adb_ok(&out) && out.contains("CHARGE_SUSPENDED") {
                s.charge_suspended = true;
                log("停充生效 (功耗按放电态进判定)");
            } else {
                log(&format!("停充没成 (功耗将不进判定): {}", strip_exit(&out).trim().lines().last().unwrap_or("")));
            }
        }
        Ok(s)
    }

    fn close(&mut self) -> Value {
        sh(&self.serial, &format!("am force-stop {}", rb::PACKAGE), 15);
        let mut v = json!({});
        if self.charge_suspended {
            let out = su(&self.serial, &format!("sh {DEV_TMP}/charge_suspend.sh restore"), 60);
            v["charge_restore"] = json!(strip_exit(&out).trim());
            self.charge_suspended = false;
        }
        write_fan_off(&self.serial);
        v["media_volume"] = json!(ensure_muted(&self.serial));
        match hwcond::recover_stale_lock("selftest", &self.dev, &self.lock_state) {
            Ok(Some(false)) => v["lock_restore"] = json!("FAILED"),
            Err(e) => {
                v["lock_restore"] = json!("FAILED");
                v["lock_restore_error"] = json!(e);
            }
            Ok(_) => {}
        }
        v["device_after"] = device_state(&self.serial);
        log(&format!("现场还原: {v}"));
        v
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        if self.charge_suspended {
            self.close();
        }
    }
}

/// 把一个负载写到本地 (证据) 并推上设备, 设备端回读 sha256 核对。返回设备侧路径。
fn deploy_load(c: &Cond, template: &[u8], load: &Load, local_dir: &Path) -> Result<String, String> {
    let bytes = load.bytes(template)?;
    let sha = sha256_hex(&bytes);
    std::fs::create_dir_all(local_dir).map_err(|e| e.to_string())?;
    let local = local_dir.join(format!("{}.spv", load.name()));
    std::fs::write(&local, &bytes).map_err(|e| e.to_string())?;
    let remote = format!("{REMOTE_DIR}/{}.spv", load.name());
    sh(&c.serial, &format!("mkdir -p {REMOTE_DIR}"), 15);
    let p = adb(&c.serial, &["push", &local.to_string_lossy(), &remote], 60);
    if !adb_ok(&p) {
        return Err(format!("推送 {} 失败: {p}", local.display()));
    }
    sh(&c.serial, &format!("chmod 755 {REMOTE_DIR}; chmod 644 {remote}"), 15);
    let got = sh(&c.serial, &format!("sha256sum {remote}"), 15);
    if !got.contains(&sha) {
        return Err(format!("设备上 {remote} 的 sha256 与本地不符: {}", strip_exit(&got).trim()));
    }
    Ok(remote)
}

fn now_str() -> String {
    chrono::Local::now().format("%Y-%m-%dT%H:%M:%S").to_string()
}

/// 跑一臂。Err = 基础设施失败 (没起来 / 没自退 / 没拉回), 调用方可重试一次;
/// 量到了但算子没生效等情况落在 arm.json 的 apply_ok=false 里, 由判定链按 ABORT 处理。
fn run_arm(s: &Session, c: &Cond, label: &str, dir: &Path, load: &Load, remote_spv: &str) -> Result<Value, String> {
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    // 输出文件先打开, 避免锁频成功后因本地创建失败提前返回而漏掉还原。
    let env_path = dir.join("env.txt");
    let env_file = std::fs::File::create(&env_path).map_err(|e| format!("创建 {}: {e}", env_path.display()))?;
    let serial = &c.serial;
    let t0 = Instant::now();
    let started_at = now_str();

    // 1) 等冷。超时不作废 (与 autoloop 一致: ABBA 交替让残余热漂移两臂均摊), 如实记录
    let cool = match hwcond::wait_cool("selftest", &s.dev, c.cool_c, c.cool_timeout_s) {
        Ok((z, t, w)) => json!({ "zone": z, "c": t, "waited_s": w, "timeout": false }),
        Err(e) => json!({ "error": e, "timeout": true, "waited_s": c.cool_timeout_s }),
    };
    // 1b) 等 GPU 热限档回落 (见 Cond::cool_tpl)。超时同样不作废, 如实记录
    let cool_tpl = match c.cool_tpl {
        None => Value::Null,
        Some(max) => {
            let t1 = Instant::now();
            let first = read_pwr(serial);
            let mut last = first.clone();
            let mut timeout = false;
            while tpl_cool_enough(&last, max) == Some(false) {
                if t1.elapsed().as_secs() >= c.cool_timeout_s {
                    timeout = true;
                    break;
                }
                std::thread::sleep(Duration::from_secs(3));
                last = read_pwr(serial);
            }
            json!({ "max": max, "tpl_before": first.get("thermal_pwrlevel"), "tpl_after": last.get("thermal_pwrlevel"),
                    "waited_s": t1.elapsed().as_secs(), "timeout": timeout })
        }
    };
    let cool_s = t0.elapsed().as_secs_f64();
    let pwr_start = read_pwr(serial);

    // 2) 锁频 (可选): 只包住跑测, 与 gpu-op 同一做法 —— 先锁再等冷永远等不下来
    let lock = if c.lock_freq {
        let snap = hwcond::read_snapshot(&s.dev);
        let level = c.lock_level.unwrap_or(snap.gpu_max_level.unwrap_or(0));
        Some(hwcond::Lock::apply(&s.dev, &s.lock_state, snap, level).map_err(|e| format!("锁频失败: {e}"))?)
    } else {
        None
    };
    // 审计 #9: 只锁 GPU 档位的档 (CPU/总线不动), 同样只包住跑测
    let gpu_lock = match (c.lock_freq, c.lock_gpu_level) {
        (false, Some(level)) => Some(crate::eval::GpuLevelLock::apply(serial, level, dir).map_err(|e| format!("GPU 档位锁失败: {e}"))?),
        _ => None,
    };
    let unlock = |lock: &Option<hwcond::Lock>| -> bool {
        let a = lock.as_ref().map(|l| l.restore(&s.dev)).unwrap_or(true);
        let b = gpu_lock.as_ref().map(|g| g.restore()["restored"].as_bool() == Some(true)).unwrap_or(true);
        a && b
    };

    // 3) 清场 + 起负载
    let files = rb::FILES;
    sh(serial, &format!("am force-stop {}; rm -f {files}/refbench_started {files}/refbench_out.json", rb::PACKAGE), 20);
    let am = sh(
        serial,
        &format!(
            "am start -W -n {} --es scene {} --es run_id {label} --es frames {} --es intensity {} --es knob.postfx on --es postfx.shader {remote_spv}",
            rb::ACTIVITY, rb::SCENE, c.frames, c.intensity
        ),
        60,
    );
    std::fs::write(dir.join("am.log"), &am).ok();
    let mut started = false;
    for _ in 0..60 {
        if sh(serial, &format!("cat {files}/refbench_started 2>/dev/null"), 10).contains(label) {
            started = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    if !started {
        sh(serial, &format!("am force-stop {}", rb::PACKAGE), 15);
        let restored = unlock(&lock);
        return Err(format!("{label}: refbench 没起跑 (30s 内未见 refbench_started); 锁频恢复={restored}"));
    }
    std::thread::sleep(Duration::from_secs(c.warmup_s));

    // 4) 采集: sample_env 后台 + ftrace 前台 (同 eval::run_one)
    let sampler = crate::fd_lock::transport_command(adb_path())
        .args(["-s", serial, "shell"])
        // 第三个参数: ftrace_capture.sh 采完才写 trace_raw.txt, 一出现采样就停 (审计 #3); 时长只是上限
        .arg(format!("su -c 'sh {DEV_TMP}/sample_env.sh {} 1 {REMOTE_DIR}/trace_raw.txt'", c.capdur_s + 20))
        .stdout(env_file)
        .stderr(std::process::Stdio::null())
        .spawn();
    let raw = format!("{REMOTE_DIR}/trace_raw.txt");
    let flt = format!("{REMOTE_DIR}/trace_kgsl.txt");
    let cap = su(serial, &format!("sh {DEV_TMP}/ftrace_capture.sh {} {raw} {}", c.capdur_s, rb::PACKAGE), c.capdur_s + 90);
    std::fs::write(dir.join("capture.log"), &cap).ok();
    let pwr_end = read_pwr(serial);
    // 只留解析器会读的行 (与 looptrace::parse 的预筛同一条件), 30 MB → 几百 KB
    su(serial, &format!("grep -E \"^#|adreno_cmdbatch|kgsl_\" {raw} > {flt}; rm -f {raw}; chmod 644 {flt}"), 60);
    let local_trace = dir.join("trace.kgsl.txt");
    let pulled = adb(serial, &["pull", &flt, &local_trace.to_string_lossy()], 60);
    su(serial, &format!("rm -f {flt}"), 15);
    if let Ok(mut ch) = sampler {
        let deadline = Instant::now() + Duration::from_secs(30);
        loop {
            match ch.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) if Instant::now() > deadline => {
                    let _ = ch.kill();
                    let _ = ch.wait();
                    break;
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(200)),
                Err(_) => break,
            }
        }
    }

    // 5) 等自退 (存活轮询), 收自报 JSON
    let mut exited = false;
    for _ in 0..240 {
        if strip_exit(&sh(serial, &format!("pidof {}", rb::PACKAGE), 10)).trim().is_empty() {
            exited = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    let unlocked = unlock(&lock);
    if !exited {
        sh(serial, &format!("am force-stop {}", rb::PACKAGE), 15);
        return Err(format!("{label}: refbench 120s 内没自退 (frames={} 过大?)", c.frames));
    }
    let out_local = dir.join("refbench_out.json");
    adb(serial, &["pull", &format!("{files}/refbench_out.json"), &out_local.to_string_lossy()], 30);
    if !adb_ok(&pulled) || !local_trace.is_file() {
        return Err(format!("{label}: trace 没拉回来: {}", strip_exit(&pulled).trim()));
    }
    if !unlocked {
        return Err(format!("{label}: 锁频还原回读不符"));
    }

    // 6) 解析 (与 parse-trace 同一函数) + 功耗温度 (与 eval/autoloop 同一函数)
    let text = std::fs::read(&local_trace).map_err(|e| e.to_string())?;
    let summary = looptrace::parse_with_spf(&String::from_utf8_lossy(&text), rb::SUBMIT_COMM, Some(1)).summarize();
    std::fs::write(dir.join("summary.json"), dumps(&summary)).map_err(|e| e.to_string())?;
    // 功耗只取 trace 采集窗内的样本, 丢第一个点 (审计 #3)
    let env = sysparam::env_stats_window(
        &std::fs::read_to_string(&env_path).unwrap_or_default(),
        sysparam::capture_window_from_trace(&String::from_utf8_lossy(&text)),
    );
    std::fs::write(dir.join("env.json"), dumps(&env)).map_err(|e| e.to_string())?;
    gzip_replace(&local_trace)?;

    // 7) 生效核对: 自报 JSON 必须说清「装的就是这份算子、干净跑完了」
    let rbo = read_json(&out_local).unwrap_or(Value::Null);
    let checks = json!({
        "clean_exit": rbo["clean_exit"] == json!(true),
        "postfx_active": rbo["postfx"]["active"] == json!(true),
        "shader_path": rbo["postfx"]["shader_path"] == json!(remote_spv),
        "run_id": rbo["run_id"] == json!(label),
        "frames_submitted": rbo["frames_submitted"] == json!(c.frames),
    });
    let apply_ok = checks.as_object().map(|o| o.values().all(|v| v == &json!(true))).unwrap_or(false);
    let sm = pv(&summary);
    let span = sm["span_s"].as_f64().unwrap_or(0.0);
    let (capture_ok, expected_s) = capture_window(
        span,
        sm["n_frames"].as_u64().unwrap_or(0),
        sm["fps_mean"].as_f64().unwrap_or(0.0),
        c.frames,
        c.capdur_s,
    );
    let arm = json!({
        "label": label, "load": load.to_json(), "remote_spv": remote_spv,
        "started_at": started_at, "cool": cool, "cool_tpl": cool_tpl, "cool_s": py_round(cool_s, 1),
        "checks": checks, "capture_ok": capture_ok, "span_s": span,
        "expected_s": py_round(expected_s, 2),
        // 审计 #16: 两件事分开记 —— apply_ok 只看负载自报 (算子装上、干净跑完),
        // capture_ok 是采集窗够不够。以前合成一个 apply_ok, 采集失败被记成「旋钮未全部生效」,
        // 一臂失败整题作废; 现在采集失败只剔除那一对 (见 drop_capture_failed)
        "apply_ok": apply_ok,
        "seconds_total": py_round(t0.elapsed().as_secs_f64(), 1),
        "lock_freq": c.lock_freq,
        "lock_gpu_level": c.lock_gpu_level,
        "pwr": { "start": pwr_start, "end": pwr_end, "changed": pwr_changed(&pwr_start, &pwr_end) },
    });
    std::fs::write(dir.join("arm.json"), serde_json::to_string_pretty(&arm).unwrap_or_default()).ok();
    log(&format!(
        "{label} {}: fps={} p50={} p95={} W={} T={} 用时 {:.0}s (等冷 {:.0}s){}",
        load.name(), sm["fps_mean"], sm["frame_p50"], sm["frame_p95"],
        pv(&env)["power_w_mean"], pv(&env)["soc_temp_max_c"],
        t0.elapsed().as_secs_f64(), cool_s,
        if apply_ok && capture_ok { String::new() } else { format!(" [未量成: {checks} capture_ok={capture_ok}]") }
    ));
    Ok(json!({ "summary": sm, "env": pv(&env), "arm": arm }))
}

/// 采集窗够不够长 —— 分母取这一臂自己的实际时长, 不取配置的采集上限。
///
/// 采集窗 `capdur` 只是个上限: 负载按帧数结束, 实际时长 = frames / fps。
/// 以前一律要求 span ≥ capdur × 0.8 (10s → 8s), 而高帧率负载 (refbench wg0 跑到 344 fps)
/// 跑满 3000 帧只用了 6.5 秒 —— 于是每一臂都被判「未量成」, 标定一个有效点都拟合不出来,
/// 已知答案的考题一道也判不成。真没采到 (n_frames=0, 或 span 远低于应有时长) 仍然不算。
///
/// 返回 (是否算量成, 应有时长)。
fn capture_window(span_s: f64, n_frames: u64, fps: f64, frames: u32, capdur_s: u64) -> (bool, f64) {
    let cap = capdur_s as f64;
    let expected = if fps > 0.0 && frames > 0 {
        (frames as f64 / fps).min(cap)
    } else {
        cap
    };
    let enough_samples = n_frames >= 120 && span_s >= 1.0;
    let covered_run = n_frames as f64 * 2.0 >= frames as f64 || span_s >= cap * 0.8;
    (enough_samples && covered_run, expected)
}

/// GPU 频率档位的现值: max/min/thermal pwrlevel 与当前频率。
///
/// 厂商性能服务 (perf2-hal / zperfcubeservice) 会在测量中途改 max_pwrlevel (实测写 0 后约 10 s
/// 又回到 17), 改了就等于换了测量条件。每臂起跑前与采集结束各记一份, 不一样就标出来。
pub fn read_pwr(serial: &str) -> Value {
    let out = su(serial, &format!(
        "for f in max_pwrlevel min_pwrlevel thermal_pwrlevel gpuclk; do echo \"$f=$(cat {KGSL}/$f 2>/dev/null)\"; done"
    ), 15);
    parse_pwr(strip_exit(&out))
}

pub fn parse_pwr(text: &str) -> Value {
    let mut m = serde_json::Map::new();
    for line in text.lines() {
        if let Some((k, v)) = line.trim().split_once('=') {
            if !k.is_empty() && !v.is_empty() {
                m.insert(k.to_string(), json!(v));
            }
        }
    }
    Value::Object(m)
}

/// GPU 热限档是否已回落到 <= `max`。读不到 / 不是数 = None (不知道, 调用方不等)。
pub fn tpl_cool_enough(pwr: &Value, max: u32) -> Option<bool> {
    let v = pwr.get("thermal_pwrlevel")?.as_str()?.trim().parse::<u32>().ok()?;
    Some(v <= max)
}

/// 档位在这一臂里变没变 (只看档位, 当前频率本来就随负载变)。读不到算「不知道」, 不算变了。
pub fn pwr_changed(start: &Value, end: &Value) -> Option<bool> {
    let keys = ["max_pwrlevel", "min_pwrlevel", "thermal_pwrlevel"];
    if keys.iter().any(|k| start.get(*k).is_none() || end.get(*k).is_none()) {
        return None;
    }
    Some(keys.iter().any(|k| start[*k] != end[*k]))
}

fn gzip_replace(p: &Path) -> Result<(), String> {
    let mut raw = Vec::new();
    std::fs::File::open(p).and_then(|mut f| f.read_to_end(&mut raw)).map_err(|e| e.to_string())?;
    let gz = PathBuf::from(format!("{}.gz", p.display()));
    let mut enc = flate2::write::GzEncoder::new(
        std::fs::File::create(&gz).map_err(|e| e.to_string())?,
        flate2::Compression::default(),
    );
    enc.write_all(&raw).map_err(|e| e.to_string())?;
    enc.finish().map_err(|e| e.to_string())?;
    std::fs::remove_file(p).map_err(|e| e.to_string())
}

/// 这一臂是不是「没跑完 / 没采到」这类基础设施问题 (不是算子没生效)。
///
/// 2026-10-21 实测, 一组 8 道考题里两道各坏了 1 臂, 整道 16 臂判 ABORT:
/// - worsewg800_1/knob8: 算子自报全部生效、跑完, trace 却只采到 0.03 秒 (8 帧), capture_ok=false;
/// - betterwg300_1/knob8: refbench 跑到 2311/6200 帧被打断 (clean_exit=false)。
/// 两种都与量到的数值无关, 与「没起跑 / 没拉回」一样重试一次, 不偏。
/// 算子本身没装上 (postfx_active / shader_path / run_id 有 false) 不重试, 照旧交给判定链 ABORT。
pub fn infra_failed(v: &Value) -> bool {
    let arm = &v["arm"];
    let ck = &arm["checks"];
    let knob_applied = ["postfx_active", "shader_path", "run_id"].iter().all(|k| ck[*k] == json!(true));
    let ran_through = ck["clean_exit"] == json!(true) && ck["frames_submitted"] == json!(true);
    knob_applied && (!ran_through || arm["capture_ok"] == json!(false))
}

fn run_arm_retry(s: &Session, c: &Cond, label: &str, dir: &Path, load: &Load, remote: &str) -> Result<Value, String> {
    let first = match run_arm(s, c, label, dir, load, remote) {
        Ok(v) if infra_failed(&v) => Err(format!(
            "{label}: 没跑完或没采到 (checks={} capture_ok={} span_s={})",
            v["arm"]["checks"], v["arm"]["capture_ok"], v["arm"]["span_s"]
        )),
        other => other,
    };
    match first {
        Ok(v) => Ok(v),
        Err(e) => {
            // 基础设施失败重试一次 (不看量到的数, 所以不偏): 旧目录挪开留证
            log(&format!("{e} —— 重试一次"));
            let keep = dir.with_file_name(format!("{}.failed", dir.file_name().unwrap_or_default().to_string_lossy()));
            let _ = std::fs::rename(dir, &keep);
            run_arm(s, c, label, dir, load, remote)
        }
    }
}

// ══════════════ 子命令 ══════════════

const USAGE: &str = "用法:
  phonefarm selftest calibrate --out <目录> [--levels 0,4000,8000] [--rounds 2] [条件...]
  phonefarm selftest run --out <目录> --cases <清单> [--only 编号,..] [--pairs 5] [--calib calib.json] [--budget-min 40] [条件...]
  phonefarm selftest quality --out <目录> [--pct 5] [--downs 2] [--calib calib.json] [条件...]
  phonefarm selftest report --root <目录> [--ns 5,8,10] [--json]

考题清单: aa*4,better:1*2,worse:2 (帧时幅度%, 需标定) 或 better-wg:50 (直接给工作组数差)
条件: [--serial S] [--template ../refbench/selftest/load.spv] [--intensity 1] [--iters 64]
      [--base-wg 4000] [--frames N] [--warmup 2] [--capdur 10] [--cool-c 45] [--cool-timeout 240]
      [--cool-tpl 4 | --no-cool-tpl] [--temp-cap 70] [--no-charge-suspend] [--lock-freq] [--lock-level N (隐含 --lock-freq)]
      [--lock-gpu-level N (只钉 kgsl min/max_pwrlevel, 每臂锁、跑完还原; 与 --lock-freq 互斥)]

判定一律走现有链 (loopstat::compare + sysparam::decide; 画面走 eval --request kind=shader),
本命令不另写统计。证据与判定输入全部落盘, report 不碰设备、可离线重算。";

struct Args {
    sub: String,
    out: Option<PathBuf>,
    cases: Option<String>,
    only: Option<Vec<String>>,
    pairs: usize,
    calib: Option<PathBuf>,
    budget_min: f64,
    levels: Vec<u32>,
    rounds: usize,
    ns: Vec<usize>,
    json: bool,
    pct: f64,
    downs: u32,
    cond: Cond,
    frames_given: bool,
}

fn default_serial() -> String {
    std::env::var("REFBENCH_SERIAL").unwrap_or_else(|_| "91253241019A".into())
}

fn parse_list<T: std::str::FromStr>(s: &str) -> Result<Vec<T>, String> {
    s.split(',').map(|x| x.trim().parse::<T>().map_err(|_| format!("列表项不对: {x}"))).collect()
}

fn parse_args(args: &[String]) -> Result<Args, String> {
    let mut it = args.iter();
    let sub = it.next().cloned().ok_or(USAGE)?;
    let mut a = Args {
        sub,
        out: None,
        cases: None,
        only: None,
        pairs: 5,
        calib: None,
        budget_min: 40.0,
        levels: vec![0, 4000, 8000],
        rounds: 2,
        ns: vec![5, 8, 10],
        json: false,
        pct: 5.0,
        downs: 2,
        cond: Cond {
            serial: default_serial(),
            template: repo_root().join("../refbench/selftest/load.spv"),
            intensity: 1,
            iters: 64,
            base_wg: 4000,
            frames: 3000,
            warmup_s: 2,
            capdur_s: 10,
            cool_c: 45.0,
            cool_timeout_s: 240,
            temp_cap_c: 70.0,
            charge_suspend: true,
            lock_freq: false,
            lock_level: None,
            lock_gpu_level: None,
            cool_tpl: Some(4),
        },
        frames_given: false,
    };
    let num = |v: Option<&String>, k: &str| -> Result<f64, String> {
        v.ok_or(format!("{k} 缺值"))?.parse::<f64>().map_err(|_| format!("{k} 不是数"))
    };
    while let Some(k) = it.next() {
        match k.as_str() {
            "--out" | "--root" => a.out = it.next().map(PathBuf::from),
            "--cases" => a.cases = it.next().cloned(),
            "--only" => a.only = it.next().map(|v| v.split(',').map(|x| x.trim().to_string()).filter(|x| !x.is_empty()).collect()),
            "--pairs" => a.pairs = num(it.next(), k)? as usize,
            "--calib" => a.calib = it.next().map(PathBuf::from),
            "--budget-min" => a.budget_min = num(it.next(), k)?,
            "--levels" => a.levels = parse_list(it.next().ok_or("--levels 缺值")?)?,
            "--rounds" => a.rounds = num(it.next(), k)? as usize,
            "--ns" => a.ns = parse_list(it.next().ok_or("--ns 缺值")?)?,
            "--json" => a.json = true,
            "--pct" => a.pct = num(it.next(), k)?,
            "--downs" => a.downs = num(it.next(), k)? as u32,
            "--serial" => a.cond.serial = it.next().cloned().ok_or("--serial 缺值")?,
            "--template" => a.cond.template = it.next().map(PathBuf::from).ok_or("--template 缺值")?,
            "--intensity" => a.cond.intensity = num(it.next(), k)? as u32,
            "--iters" => a.cond.iters = num(it.next(), k)? as u32,
            "--base-wg" => a.cond.base_wg = num(it.next(), k)? as u32,
            "--frames" => {
                a.cond.frames = num(it.next(), k)? as u32;
                a.frames_given = true;
            }
            "--warmup" => a.cond.warmup_s = num(it.next(), k)? as u64,
            "--capdur" => a.cond.capdur_s = num(it.next(), k)? as u64,
            "--cool-c" => a.cond.cool_c = num(it.next(), k)?,
            "--cool-timeout" => a.cond.cool_timeout_s = num(it.next(), k)? as u64,
            "--cool-tpl" => a.cond.cool_tpl = Some(num(it.next(), k)? as u32),
            "--no-cool-tpl" => a.cond.cool_tpl = None,
            "--temp-cap" => a.cond.temp_cap_c = num(it.next(), k)?,
            "--no-charge-suspend" => a.cond.charge_suspend = false,
            "--fan-on" => return Err("--fan-on 已取消: 风扇一律不开 (owner 规定), 需要降温靠等冷".into()),
            "--lock-freq" => a.cond.lock_freq = true,
            "--lock-level" => {
                a.cond.lock_level = Some(num(it.next(), k)? as u32);
                a.cond.lock_freq = true;
            }
            "--lock-gpu-level" => a.cond.lock_gpu_level = Some(num(it.next(), k)? as u32),
            other => return Err(format!("不认识的参数 {other}\n\n{USAGE}")),
        }
    }
    if a.cond.lock_freq && a.cond.lock_gpu_level.is_some() {
        return Err("--lock-gpu-level 与 --lock-freq/--lock-level 互斥 (后者已含 GPU 档位)".into());
    }
    Ok(a)
}

pub fn run_selftest(args: &[String]) -> i32 {
    let a = match parse_args(args) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{e}");
            return 2;
        }
    };
    let res = match a.sub.as_str() {
        "report" => cmd_report(&a),
        "calibrate" => cmd_calibrate(a),
        "run" => cmd_run(a),
        "quality" => cmd_quality(a),
        _ => Err(USAGE.to_string()),
    };
    match res {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("{e}");
            2
        }
    }
}

fn cmd_report(a: &Args) -> Result<(), String> {
    let root = a.out.clone().ok_or("缺 --root")?;
    let r = report(&root, &a.ns);
    std::fs::write(root.join("report.json"), serde_json::to_string_pretty(&r).unwrap_or_default()).ok();
    if a.json {
        println!("{}", serde_json::to_string_pretty(&r).unwrap_or_default());
    } else {
        print!("{}", render(&r));
    }
    Ok(())
}

fn read_template(c: &Cond) -> Result<Vec<u8>, String> {
    let t = std::fs::read(&c.template).map_err(|e| format!("读不了负载模板 {}: {e}", c.template.display()))?;
    let spec = read_spec(&t)?;
    for id in [SPEC_HEAVY_WG, SPEC_ITERS, SPEC_DOWNS] {
        if !spec.iter().any(|(s, _)| *s == id) {
            return Err(format!("负载模板缺 SpecId {id} —— 不是 refbench/selftest/load.spv?"));
        }
    }
    Ok(t)
}

fn load_calib(a: &Args) -> Result<Option<Calib>, String> {
    match &a.calib {
        None => Ok(None),
        Some(p) => {
            let v = read_json(p).ok_or(format!("读不了标定 {}", p.display()))?;
            Ok(Some(Calib::from_json(&v["calib"]).ok_or("标定文件缺 calib.t0_ms / k_ms_per_wg")?))
        }
    }
}

/// frames 没显式给时按标定推: 最快那一臂的帧率 × (预热 + 采集 + 4s 余量)。
fn auto_frames(a: &mut Args, calib: Option<&Calib>, min_wg: u32) {
    if a.frames_given {
        return;
    }
    if let Some(c) = calib {
        let fps = 1000.0 / c.t(min_wg).max(0.5);
        let f = fps * (a.cond.warmup_s + a.cond.capdur_s + 4) as f64;
        a.cond.frames = ((f / 100.0).ceil() * 100.0) as u32;
    }
}

fn cmd_calibrate(mut a: Args) -> Result<(), String> {
    let out = a.out.clone().ok_or("缺 --out")?;
    std::fs::create_dir_all(&out).map_err(|e| e.to_string())?;
    let template = read_template(&a.cond)?;
    if !a.frames_given {
        a.cond.frames = 3000;
    }
    let c = a.cond.clone();
    let mut s = Session::open(&c)?;
    let mut remotes = Vec::new();
    for &wg in &a.levels {
        let l = Load { heavy_wg: wg, iters: c.iters, downs: 1 };
        remotes.push((l, deploy_load(&c, &template, &l, &out.join("loads"))?));
    }
    let mut points = Vec::new();
    let mut runs = Vec::new();
    for r in 1..=a.rounds {
        // 每轮把档位顺序反一下, 让热漂移不总落在同一档上
        let order: Vec<usize> = if r % 2 == 1 { (0..remotes.len()).collect() } else { (0..remotes.len()).rev().collect() };
        for i in order {
            let (l, remote) = &remotes[i];
            let label = format!("cal_wg{}_{r}", l.heavy_wg);
            match run_arm_retry(&s, &c, &label, &out.join(&label), l, remote) {
                Ok(v) => {
                    if v["arm"]["apply_ok"] == json!(true) && v["arm"]["capture_ok"] != json!(false) {
                        if let Some(p50) = v["summary"]["frame_p50"].as_f64() {
                            points.push((l.heavy_wg, p50));
                        }
                    }
                    runs.push(json!({ "label": label, "heavy_wg": l.heavy_wg, "frame_p50": v["summary"]["frame_p50"],
                        "frame_p95": v["summary"]["frame_p95"], "fps_mean": v["summary"]["fps_mean"],
                        "power_w_mean": v["env"]["power_w_mean"], "seconds_total": v["arm"]["seconds_total"], "apply_ok": v["arm"]["apply_ok"],
                        "capture_ok": v["arm"]["capture_ok"] }));
                }
                Err(e) => runs.push(json!({ "label": label, "error": e })),
            }
        }
    }
    let restore = s.close();
    let fit = fit_calib(&points);
    let resid: Vec<f64> = fit
        .map(|f| points.iter().map(|(w, y)| py_round((y - f.t(*w)) / f.t(*w) * 100.0, 3)).collect())
        .unwrap_or_default();
    let doc = json!({
        "metric": "frame_p50", "conditions": c.to_json(), "template_sha256": sha256_hex(&template),
        "calib": fit.map(|f| f.to_json()), "residual_pct": resid, "runs": runs, "restore": restore,
    });
    std::fs::write(out.join("calib.json"), serde_json::to_string_pretty(&doc).unwrap_or_default()).map_err(|e| e.to_string())?;
    println!("{}", serde_json::to_string_pretty(&doc).unwrap_or_default());
    fit.map(|_| ()).ok_or("标定拟合不出来 (有效点不足两个不同档位)".into())
}

fn cmd_run(mut a: Args) -> Result<(), String> {
    let out = a.out.clone().ok_or("缺 --out")?;
    // 结构性防护 (放在建目录、写 plan.json 之前, 免得拒跑还留下一份 pairs 不对的 plan):
    // 样本量不够时精确置换检验的最小可达 p 就比 alpha 还大 ——
    // 这种配置在数学上不可能判出显著改善, 跑再久也只是把「什么都没学到」重算一遍。
    // 2026-10-21 实测: --pairs 3 时每题 3v3, 最小双侧 p = 2/C(6,3) = 0.1 > alpha_win 0.025,
    // 于是一道预期 −14.7% 帧时的考题也被判成「没有任何指标显著改善」。
    // 功耗进不进判定要等数据 (两臂是否全程放电态), 开跑前按最严的 alpha/3 算
    // (alpha/2 与 alpha/3 下最少都是 5 对, 这里取严只是不给「功耗进表」留漏洞)
    let alpha_win = crate::sysparam::ALPHA / crate::loopstat::MAX_WIN_METRICS as f64;
    crate::loopstat::ensure_resolvable(a.pairs, alpha_win, "--pairs")?;
    std::fs::create_dir_all(&out).map_err(|e| e.to_string())?;
    let template = read_template(&a.cond)?;
    let calib = load_calib(&a)?;
    let base = Load { heavy_wg: a.cond.base_wg, iters: a.cond.iters, downs: 1 };
    let cases = parse_cases(a.cases.as_deref().ok_or("缺 --cases")?, base, calib.as_ref())?;
    let min_wg = cases.iter().map(|c| c.b.heavy_wg.min(c.a.heavy_wg)).min().unwrap_or(0);
    auto_frames(&mut a, calib.as_ref(), min_wg);
    let c = a.cond.clone();

    // 判定条件先于数据冻结。续跑 (目录里已有 plan.json) 时条件必须一字不差, 否则拒绝混跑
    let cond_json = c.to_json();
    let plan = json!({
        "selftest_version": 1, "template": c.template.display().to_string(),
        "template_sha256": sha256_hex(&template), "conditions": cond_json, "pairs": a.pairs,
        "calib": calib.map(|x| x.to_json()),
        "rule": pv(&sysparam::rule_doc(&PyVal::Float(c.temp_cap_c), &PyVal::Int(a.pairs as i64), true,
            "功耗是否进判定按每组两臂是否全程放电态自动定 (同 eval/autoloop auto 口径)")),
    });
    let plan_path = out.join("plan.json");
    if let Some(old) = read_json(&plan_path) {
        if old["conditions"] != plan["conditions"] || old["template_sha256"] != plan["template_sha256"] || old["pairs"] != plan["pairs"] {
            return Err(format!("{} 已有不同条件的 plan.json, 拒绝混跑 (换个 --out)", out.display()));
        }
    } else {
        std::fs::write(&plan_path, serde_json::to_string_pretty(&plan).unwrap_or_default()).map_err(|e| e.to_string())?;
    }

    // --only: 分段跑 (每段一个新目录, 本地再合并成一个根给 report)。考题编号由完整清单决定,
    // 所以各段要给同一份 --cases, 只挑本段要跑的编号; plan.json 各段一字不差, 合并时可核对
    if let Some(only) = &a.only {
        if let Some(bad) = only.iter().find(|id| !cases.iter().any(|c| &c.id == *id)) {
            return Err(format!("--only 里的 {bad} 不在 --cases 展开的考题里"));
        }
    }
    let todo: Vec<&Case> = cases
        .iter()
        .filter(|cs| a.only.as_ref().is_none_or(|o| o.contains(&cs.id)))
        .filter(|cs| !out.join(&cs.id).join("case_done.json").is_file())
        .collect();
    log(&format!("考题 {} 道, 待跑 {} 道, 每道 {}v{}, frames={}", cases.len(), todo.len(), a.pairs, a.pairs, c.frames));
    if todo.is_empty() {
        return cmd_report(&a);
    }
    let mut s = Session::open(&c)?;
    let t_session = Instant::now();
    let mut arm_secs: Vec<f64> = Vec::new();
    let mut deployed: Vec<(Load, String)> = Vec::new();
    for case in todo {
        // 时间预算: 估不下一整道就不开 (单次持锁 <= 45 分钟)
        let est_arm = if arm_secs.is_empty() { 40.0 } else { arm_secs.iter().sum::<f64>() / arm_secs.len() as f64 };
        let need = est_arm * 2.0 * a.pairs as f64 / 60.0;
        let used = t_session.elapsed().as_secs_f64() / 60.0;
        if used + need > a.budget_min {
            log(&format!("预算 {} 分钟已用 {:.1}, 下一道约需 {:.1} 分钟, 停在这里 (续跑同一 --out 即可)", a.budget_min, used, need));
            break;
        }
        let dir = out.join(&case.id);
        if dir.exists() {
            // 上次中途断掉的残局: 挪开留证, 从头跑
            let mut k = 1;
            while dir.with_file_name(format!("{}.incomplete{k}", case.id)).exists() {
                k += 1;
            }
            std::fs::rename(&dir, dir.with_file_name(format!("{}.incomplete{k}", case.id))).map_err(|e| e.to_string())?;
        }
        std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
        let mut cj = case.to_json();
        cj["pairs"] = json!(a.pairs);
        std::fs::write(dir.join("case.json"), serde_json::to_string_pretty(&cj).unwrap_or_default()).map_err(|e| e.to_string())?;
        let mut remote = |l: &Load| -> Result<String, String> {
            if let Some((_, r)) = deployed.iter().find(|(x, _)| x == l) {
                return Ok(r.clone());
            }
            let r = deploy_load(&c, &template, l, &out.join("loads"))?;
            deployed.push((*l, r.clone()));
            Ok(r)
        };
        let (ra, rb_) = (remote(&case.a)?, remote(&case.b)?);
        log(&format!("── {} ({}): A {} vs B {} 预期 {:?}%", case.id, case.kind, case.a.name(), case.b.name(), case.nominal_pct.map(|x| py_round(x, 3))));
        std::fs::write(dir.join("snap_before.txt"), snapshot(&c.serial)).ok();
        let t_case = Instant::now();
        let mut abort: Option<String> = None;
        'rounds: for i in 1..=a.pairs {
            for arm in arm_order(i) {
                let (l, r) = if arm == "knob" { (&case.b, &rb_) } else { (&case.a, &ra) };
                let label = format!("{arm}{i}");
                match run_arm_retry(&s, &c, &label, &dir.join(&label), l, r) {
                    Ok(v) => {
                        if let Some(x) = v["arm"]["seconds_total"].as_f64() {
                            arm_secs.push(x);
                        }
                        let t = v["env"]["soc_temp_max_c"].as_f64();
                        if t.is_some_and(|t| t > c.temp_cap_c) {
                            abort = Some(format!("{label} SoC 结温 {t:?}C 超过上限 {}C", c.temp_cap_c));
                            break 'rounds;
                        }
                    }
                    Err(e) => {
                        abort = Some(e);
                        break 'rounds;
                    }
                }
            }
        }
        std::fs::write(dir.join("snap_after.txt"), snapshot(&c.serial)).ok();
        if let Some(r) = &abort {
            std::fs::write(dir.join("abort.json"), serde_json::to_string_pretty(&json!({ "reason": r })).unwrap_or_default()).ok();
            log(&format!("{} 中途作废: {r}", case.id));
        }
        let verdict = judge_case(&dir, completed_pairs(&dir).min(a.pairs), c.temp_cap_c);
        std::fs::write(dir.join("verdict.json"), serde_json::to_string_pretty(&verdict).unwrap_or_default()).ok();
        std::fs::write(
            dir.join("case_done.json"),
            serde_json::to_string_pretty(&json!({ "finished_at": now_str(), "minutes": py_round(t_case.elapsed().as_secs_f64() / 60.0, 2), "aborted": abort })).unwrap_or_default(),
        )
        .ok();
        log(&format!("{} → {} ({}) {}", case.id, verdict["verdict"], verdict["outcome"], verdict["reason"]));
        if abort.as_deref().is_some_and(|r| r.contains("超过上限")) {
            break; // 太热了就停, 不拿手机硬扛
        }
    }
    let restore = s.close();
    std::fs::write(out.join(format!("session_{}.json", chrono::Local::now().format("%Y%m%d_%H%M%S"))),
        serde_json::to_string_pretty(&json!({ "minutes": py_round(t_session.elapsed().as_secs_f64() / 60.0, 1), "restore": restore })).unwrap_or_default()).ok();
    cmd_report(&a)
}

/// 画面判据考题: 同一份负载, B 臂少跑重循环 (帧时变好) 再叠采样降分辨率 (画面变糙),
/// 交给 `phonefarm eval --request` (kind=shader) —— 必须判 POOR_QUALITY。
/// 同时出一道对照: 只少跑重循环、不降采样 (画面逐位不变) —— 不得判 POOR_QUALITY。
fn cmd_quality(a: Args) -> Result<(), String> {
    let out = a.out.clone().ok_or("缺 --out")?;
    std::fs::create_dir_all(&out).map_err(|e| e.to_string())?;
    let template = read_template(&a.cond)?;
    let calib = load_calib(&a)?;
    let c = &a.cond;
    let base = Load { heavy_wg: c.base_wg, iters: c.iters, downs: 1 };
    let fast_wg = match &calib {
        Some(k) => k.wg_for(base.heavy_wg, -a.pct)?,
        None => base.heavy_wg - (base.heavy_wg as f64 * a.pct / 100.0) as u32,
    };
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let mut results = serde_json::Map::new();
    for (name, cand, expect) in [
        ("cheat", Load { heavy_wg: fast_wg, downs: a.downs, ..base }, "POOR_QUALITY"),
        ("honest", Load { heavy_wg: fast_wg, downs: 1, ..base }, "not POOR_QUALITY"),
    ] {
        let dir = out.join(format!("quality_{name}"));
        std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
        std::fs::write(dir.join("baseline.spv"), base.bytes(&template)?).map_err(|e| e.to_string())?;
        std::fs::write(dir.join("candidate.spv"), cand.bytes(&template)?).map_err(|e| e.to_string())?;
        let req = json!({
            "version": 2, "kind": "shader", "candidate_id": format!("selftest_{name}"),
            "package": rb::PACKAGE, "serial": c.serial,
            "workload": { "carrier": "headless", "script": "", "seconds": 4 },
            "conditions": { "cool_c": c.cool_c, "temp_cap_c": c.temp_cap_c },
            "protocol": { "rounds": 5, "alternating": true, "p_threshold": 0.05 },
            "payload": {
                "track": "sr", "shader_path": c.template.with_extension("comp").display().to_string(),
                "spirv_path": dir.join("candidate.spv").display().to_string(),
                "budget_ms": 1000.0, "quality_baseline_db": 0.0,
            },
        });
        let req_path = dir.join("request.json");
        std::fs::write(&req_path, serde_json::to_string_pretty(&req).unwrap_or_default()).map_err(|e| e.to_string())?;
        log(&format!("画面判据考题 {name}: 基线 {} vs 候选 {} → eval --request", base.name(), cand.name()));
        let o = Command::new(&exe)
            .args(["eval", "--request"])
            .arg(&req_path)
            .args(["--serial", &c.serial, "--out"])
            .arg(dir.join("evidence"))
            .arg("--json")
            .output()
            .map_err(|e| format!("起不了 eval: {e}"))?;
        std::fs::write(dir.join("eval_stdout.json"), &o.stdout).ok();
        std::fs::write(dir.join("eval_stderr.log"), &o.stderr).ok();
        let rep: Value = serde_json::from_slice(&o.stdout).unwrap_or(Value::Null);
        let status = rep["status"].as_str().unwrap_or("NO_REPORT").to_string();
        let ok = if expect == "POOR_QUALITY" { status == "POOR_QUALITY" } else { status != "POOR_QUALITY" && status != "NO_REPORT" };
        let base_psnr = rep["quality_reference"]["baseline_psnr_db"].clone();
        results.insert(name.into(), json!({
            "expected": expect, "status": status, "pass": ok,
            "baseline_load": base.to_json(), "candidate_load": cand.to_json(),
            "psnr_db": rep["metrics"]["psnr_db"], "baseline_psnr_db": base_psnr,
            "operator_latency_ms": rep["metrics"]["operator_latency_ms"],
            "p_value": rep["verdict"]["p_value"], "is_pareto_improvement": rep["verdict"]["is_pareto_improvement"],
            "exit_code": o.status.code(),
        }));
        log(&format!("{name}: status={status} (期望 {expect}) psnr={}", rep["metrics"]["psnr_db"]));
    }
    let doc = Value::Object(results);
    std::fs::write(out.join("quality.json"), serde_json::to_string_pretty(&doc).unwrap_or_default()).map_err(|e| e.to_string())?;
    println!("{}", serde_json::to_string_pretty(&doc).unwrap_or_default());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 最小模块: OpDecorate %10 SpecId 0 / %11 SpecId 1, OpSpecConstant %uint %10 7 / %11 9
    fn tiny_module() -> Vec<u8> {
        let mut w: Vec<u32> = vec![SPV_MAGIC, 0x0001_0000, 0, 20, 0];
        w.extend([(4 << 16) | OP_DECORATE, 10, DEC_SPEC_ID, 0]);
        w.extend([(4 << 16) | OP_DECORATE, 11, DEC_SPEC_ID, 1]);
        w.extend([(4 << 16) | OP_SPEC_CONSTANT, 2, 10, 7]);
        w.extend([(4 << 16) | OP_SPEC_CONSTANT, 2, 11, 9]);
        w.iter().flat_map(|x| x.to_le_bytes()).collect()
    }

    #[test]
    fn spec_constants_are_read_and_patched_in_place() {
        let m = tiny_module();
        assert_eq!(read_spec(&m).unwrap(), vec![(0, 7), (1, 9)]);
        let p = patch_spec(&m, &[(1, 1234)]).unwrap();
        assert_eq!(read_spec(&p).unwrap(), vec![(0, 7), (1, 1234)]);
        assert_eq!(p.len(), m.len(), "改写只动一个字, 长度不变");
        let diff = m.iter().zip(&p).filter(|(x, y)| x != y).count();
        assert!(diff <= 4);
    }

    #[test]
    fn patching_a_missing_spec_id_is_refused() {
        assert!(patch_spec(&tiny_module(), &[(2, 5)]).is_err());
        assert!(patch_spec(&[0u8; 7], &[]).is_err());
        let mut bad = tiny_module();
        bad[0] = 0;
        assert!(read_spec(&bad).is_err(), "魔数不对就拒");
    }

    #[test]
    fn the_shipped_load_template_has_the_three_knobs() {
        let p = repo_root().join("../refbench/selftest/load.spv");
        let Ok(t) = std::fs::read(&p) else { return }; // refbench 不在同级时跳过
        let spec = read_spec(&t).unwrap();
        assert_eq!(spec, vec![(0, 0), (1, 1), (2, 1)], "缺省值必须是安全值 (不跑重循环、不降采样)");
        let l = Load { heavy_wg: 4000, iters: 64, downs: 1 };
        assert_eq!(read_spec(&l.bytes(&t).unwrap()).unwrap(), vec![(0, 4000), (1, 64), (2, 1)]);
        assert!(Load { heavy_wg: FULL_WG + 1, iters: 1, downs: 1 }.bytes(&t).is_err());
    }

    #[test]
    fn calibration_fits_and_inverts() {
        let c = fit_calib(&[(0, 2.0), (4000, 10.0), (8000, 18.0)]).unwrap();
        assert!((c.t0_ms - 2.0).abs() < 1e-9 && (c.k_ms_per_wg - 0.002).abs() < 1e-12);
        // A 臂 4000 → 10ms; 变快 5% → 9.5ms → (9.5-2)/0.002 = 3750
        assert_eq!(c.wg_for(4000, -5.0).unwrap(), 3750);
        assert_eq!(c.wg_for(4000, 2.0).unwrap(), 4100);
        assert!((c.expected_pct(4000, 3750) + 5.0).abs() < 1e-9);
        assert!(c.wg_for(4000, 300.0).is_err());
        assert!(fit_calib(&[(10, 1.0), (10, 2.0)]).is_none(), "只有一个档位拟合不了");
    }

    #[test]
    fn case_lists_expand_with_ids_and_loads() {
        let base = Load { heavy_wg: 4000, iters: 64, downs: 1 };
        let cal = Calib { t0_ms: 2.0, k_ms_per_wg: 0.002 };
        let cs = parse_cases("aa*2,better:5,worse:2*2,better-wg:50", base, Some(&cal)).unwrap();
        let ids: Vec<&str> = cs.iter().map(|c| c.id.as_str()).collect();
        assert_eq!(ids, ["aa_1", "aa_2", "better5_1", "worse2_1", "worse2_2", "betterwg50_1"]);
        assert_eq!(cs[0].a, cs[0].b, "A/A 两臂同一负载");
        assert_eq!(cs[2].b.heavy_wg, 3750);
        assert_eq!(cs[3].b.heavy_wg, 4100);
        assert_eq!(cs[5].b.heavy_wg, 3950);
        assert!(parse_cases("better:1", base, None).is_err(), "按幅度出题必须先标定");
        assert!(parse_cases("better:-1", base, Some(&cal)).is_err());
        assert!(parse_cases("bogus", base, None).is_err());
        assert_eq!(pct_tag(0.5), "0p5");
    }

    #[test]
    fn abba_order_matches_eval() {
        assert_eq!(arm_order(1), ["knob", "ctrl"]);
        assert_eq!(arm_order(2), ["ctrl", "knob"]);
    }

    fn arm(label: &str, p95: f64, fps: f64, w: f64) -> ArmRec {
        (
            label.into(),
            json!({
                "summary": { "frame_p95": p95, "fps_mean": fps },
                "env": { "on_battery": true, "power_w_mean": w, "soc_temp_max_c": 50.0 },
                "arm": { "apply_ok": true },
            }),
        )
    }

    fn arms(base: f64, shift: f64) -> (Vec<ArmRec>, Vec<ArmRec>) {
        // 5 轮, 轻微噪声 (确定性)
        let noise = [0.0, 0.03, -0.02, 0.01, -0.01];
        let a = (0..5).map(|i| arm(&format!("ctrl{}", i + 1), base * (1.0 + noise[i] / 10.0), 1000.0 / base, 5.0 + noise[i])).collect();
        let b = (0..5)
            .map(|i| {
                let p = base * (1.0 + shift) * (1.0 + noise[4 - i] / 10.0);
                arm(&format!("knob{}", i + 1), p, 1000.0 / p, 5.0 + noise[4 - i])
            })
            .collect();
        (a, b)
    }

    #[test]
    fn the_existing_chain_answers_the_three_known_questions() {
        let (a, b) = arms(10.0, 0.0);
        let d = pv(&judge_arms(&a, &b, 70.0, true, None));
        assert_eq!(d["verdict"], "REJECT");
        assert_eq!(classify("aa", &d), "correct");

        let (a, b) = arms(10.0, -0.05);
        let d = pv(&judge_arms(&a, &b, 70.0, true, None));
        assert_eq!(d["verdict"], "KEEP", "{d}");
        assert_eq!(classify("better", &d), "detected");
        assert_eq!(d["power_in_verdict"], true);

        let (a, b) = arms(10.0, 0.05);
        let d = pv(&judge_arms(&a, &b, 70.0, true, None));
        assert_eq!(d["verdict"], "REJECT");
        assert_eq!(classify("worse", &d), "detected");
        assert_eq!(classify("aa", &d), "false_regression");
    }

    #[test]
    fn injected_scale_moves_only_the_frame_metrics() {
        let (a, b) = arms(10.0, 0.0);
        let d = pv(&judge_arms(&a, &b, 70.0, true, Some(0.95)));
        assert_eq!(d["verdict"], "KEEP");
        assert_eq!(d["per_metric"]["power_w_mean"]["status"], "无显著变化");
    }

    #[test]
    fn hot_or_unapplied_runs_abort_rather_than_count() {
        let (a, mut b) = arms(10.0, -0.05);
        b[0].1["env"]["soc_temp_max_c"] = json!(71.0);
        assert_eq!(pv(&judge_arms(&a, &b, 70.0, true, None))["verdict"], "ABORT");
        let (a, mut b) = arms(10.0, -0.05);
        b[2].1["arm"]["apply_ok"] = json!(false);
        assert_eq!(pv(&judge_arms(&a, &b, 70.0, true, None))["verdict"], "ABORT");
        assert_eq!(classify("better", &json!({"verdict": "ABORT"})), "abort");
    }

    /// 审计 #16: 采集失败不再记成「旋钮未全部生效」, 也不再整题作废, 只剔除那一对。
    #[test]
    fn capture_failure_drops_only_its_pair_and_is_not_an_apply_failure() {
        let (a, mut b) = arms(10.0, -0.05);
        b[2].1["arm"]["capture_ok"] = json!(false);
        assert!(arm_apply_ok(&b[2].1));
        let (ka, kb, dropped) = drop_capture_failed(&a, &b);
        assert_eq!((ka.len(), kb.len()), (4, 4));
        assert_eq!(dropped, vec!["knob3+ctrl3".to_string()]);
        assert!(ka.iter().all(|r| r.0 != "ctrl3") && kb.iter().all(|r| r.0 != "knob3"));
        let d = pv(&judge_arms(&ka, &kb, 70.0, true, None));
        assert_ne!(d["verdict"], "ABORT", "{d}");
        assert_eq!(d["n_pairs"], 4);
        // 旧证据: apply_ok 是合成的 false, 但逐项 checks 全真 → 负载其实生效了
        let old = json!({"arm": {"apply_ok": false, "capture_ok": false,
            "checks": {"clean_exit": true, "postfx_active": true}}});
        assert!(arm_apply_ok(&old));
        assert!(!arm_capture_ok(&old));
        // checks 里真有一项假: 才是负载没生效
        let bad = json!({"arm": {"apply_ok": false, "capture_ok": true,
            "checks": {"clean_exit": true, "postfx_active": false}}});
        assert!(!arm_apply_ok(&bad));
        // 没记 capture_ok 的更旧证据: 算采集够
        assert!(arm_capture_ok(&json!({"arm": {"apply_ok": true}})));
    }

    #[test]
    fn power_only_judged_when_every_run_was_on_battery() {
        let (a, mut b) = arms(10.0, 0.0);
        b[1].1["env"]["on_battery"] = json!(false);
        let d = pv(&judge_arms(&a, &b, 70.0, true, None));
        assert_eq!(d["power_in_verdict"], false);
        assert_eq!(d["alpha_win"], 0.025, "两个指标时 Bonferroni 0.05/2");
    }

    fn write_case(root: &Path, id: &str, kind: &str, shift: f64, pairs: usize) {
        let dir = root.join(id);
        std::fs::create_dir_all(&dir).unwrap();
        let l = Load { heavy_wg: 4000, iters: 64, downs: 1 };
        let case = Case { id: id.into(), kind: kind.into(), nominal_pct: Some(shift * 100.0), a: l, b: l };
        std::fs::write(dir.join("case.json"), case.to_json().to_string()).unwrap();
        std::fs::write(dir.join("snap_before.txt"), "a=1\n").unwrap();
        std::fs::write(dir.join("snap_after.txt"), "a=1\n").unwrap();
        for i in 1..=pairs {
            // 两臂每 5 轮走遍同一组噪声值 (次序不同), A/A 时两臂同分布
            let nc = ((i * 7) % 5) as f64 / 500.0;
            let nk = ((i * 3) % 5) as f64 / 500.0;
            for (arm, f) in [("ctrl", 1.0 + nc), ("knob", (1.0 + shift) * (1.0 + nk))] {
                let d = dir.join(format!("{arm}{i}"));
                std::fs::create_dir_all(&d).unwrap();
                let p = 10.0 * f;
                std::fs::write(d.join("summary.json"), json!({"frame_p95": p, "fps_mean": 1000.0 / p, "frame_p50": p * 0.9}).to_string()).unwrap();
                std::fs::write(d.join("env.json"), json!({"on_battery": true, "power_w_mean": 5.0, "soc_temp_max_c": 50.0}).to_string()).unwrap();
                std::fs::write(d.join("arm.json"), json!({"apply_ok": true, "seconds_total": 30.0}).to_string()).unwrap();
            }
        }
    }

    #[test]
    fn report_is_offline_and_reproducible() {
        let root = std::env::temp_dir().join(format!("pf_selftest_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        write_case(&root, "aa_1", "aa", 0.0, 10);
        write_case(&root, "better5_1", "better", -0.05, 10);
        write_case(&root, "worse5_1", "worse", 0.05, 5);
        // n=10 的精确置换区间在 debug 构建里要跑几分钟, 单测只走 5/8 (口径一样)
        let r1 = report(&root, &[5, 8]);
        let r2 = report(&root, &[5, 8]);
        assert_eq!(r1, r2, "同样的证据每次出同样的结论");
        let m5 = &r1["confusion"][0];
        assert_eq!(m5["n"], 5);
        let row = |t: &str| m5["rows"].as_array().unwrap().iter().find(|r| r["truth"] == t).unwrap()["counts"].clone();
        assert_eq!(row("aa"), json!([0, 0, 1, 0]));
        assert_eq!(row("better"), json!([1, 0, 0, 0]));
        assert_eq!(row("worse"), json!([0, 1, 0, 0]));
        // worse 只跑了 5 轮: n=8 不出判定
        let worse = r1["cases"].as_array().unwrap().iter().find(|c| c["id"] == "worse5_1").unwrap();
        assert_eq!(worse["judged"].as_array().unwrap().len(), 1);
        // 注入法: 10 轮 A/A 的 5 轮滑窗 6 个
        assert_eq!(r1["injection"][0]["windows"], 6);
        assert!(r1["injection"][0]["mde80_pct"].as_f64().is_some());
        assert_eq!(r1["time_cost"]["per_n"][0]["minutes_per_experiment"], 5.0);
        assert!(render(&r1).contains("混淆矩阵"));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn an_interrupted_or_uncaptured_arm_is_retried_but_an_unapplied_knob_is_not() {
        let ok = json!(true);
        let checks = json!({ "clean_exit": ok, "postfx_active": ok, "shader_path": ok, "run_id": ok, "frames_submitted": ok });
        // 10-21 worsewg800_1/knob8: 算子全生效、跑完, trace 只有 0.03 秒
        let lost = json!({ "arm": { "checks": checks, "capture_ok": false, "span_s": 0.0339, "apply_ok": false } });
        assert!(infra_failed(&lost));
        // 10-21 betterwg300_1/knob8: 跑到一半被打断
        let mut cut = checks.clone();
        cut["clean_exit"] = json!(false);
        cut["frames_submitted"] = json!(false);
        assert!(infra_failed(&json!({ "arm": { "checks": cut, "capture_ok": false, "apply_ok": false } })));
        let good = json!({ "arm": { "checks": checks, "capture_ok": true, "apply_ok": true } });
        assert!(!infra_failed(&good));
        // 算子没装上: 不是基础设施问题, 不重试, 让判定链按 ABORT 处理
        let mut bad = checks.clone();
        bad["postfx_active"] = json!(false);
        assert!(!infra_failed(&json!({ "arm": { "checks": bad, "capture_ok": true, "apply_ok": false } })));
        assert!(!infra_failed(&json!({ "arm": {} })));
    }

    #[test]
    fn cooling_waits_for_the_gpu_thermal_level_not_just_temperature() {
        // 10-21 实测: 结温已在 45C 以下, thermal_pwrlevel 仍停在 6 —— 这时不算冷
        let hot = parse_pwr("max_pwrlevel=0\nmin_pwrlevel=17\nthermal_pwrlevel=6\ngpuclk=578000000\n");
        let back = parse_pwr("max_pwrlevel=0\nmin_pwrlevel=17\nthermal_pwrlevel=0\ngpuclk=160000000\n");
        assert_eq!(tpl_cool_enough(&hot, 0), Some(false));
        assert_eq!(tpl_cool_enough(&hot, 6), Some(true));
        assert_eq!(tpl_cool_enough(&back, 0), Some(true));
        // 读不到就是「不知道」, 不能无限等下去
        assert_eq!(tpl_cool_enough(&parse_pwr("max_pwrlevel=0\n"), 0), None);
        assert_eq!(tpl_cool_enough(&parse_pwr("thermal_pwrlevel=x\n"), 0), None);
        // 缺省等回 4 档以内 (空闲 60 秒后实测多停在 4–6, 缺省 0 会每臂等到超时), 也能用参数关掉
        let a = parse_args(&["run".to_string()]).unwrap();
        assert_eq!(a.cond.cool_tpl, Some(4));
        let a = parse_args(&["run".into(), "--no-cool-tpl".into()]).unwrap();
        assert_eq!(a.cond.cool_tpl, None);
        let a = parse_args(&["run".into(), "--cool-tpl".into(), "3".into()]).unwrap();
        assert_eq!(a.cond.cool_tpl, Some(3));
        assert_eq!(a.cond.to_json()["cool_tpl"], json!(3));
    }

    #[test]
    fn pwr_change_is_about_levels_not_the_current_clock() {
        let a = parse_pwr("max_pwrlevel=0\nmin_pwrlevel=17\nthermal_pwrlevel=7\ngpuclk=578000000\n");
        let same = parse_pwr("max_pwrlevel=0\nmin_pwrlevel=17\nthermal_pwrlevel=7\ngpuclk=160000000\n");
        let moved = parse_pwr("max_pwrlevel=17\nmin_pwrlevel=17\nthermal_pwrlevel=7\ngpuclk=160000000\n");
        assert_eq!(pwr_changed(&a, &same), Some(false));
        assert_eq!(pwr_changed(&a, &moved), Some(true));
        assert_eq!(pwr_changed(&a, &parse_pwr("")), None);
    }

    /// 采集窗的判据是「样本够多 + 采到了这一轮的大部分」, 不是墙钟时长。
    ///
    /// 实例数据 (2026-10-21, NX809J): wg0 跑 3000 帧、344 fps, trace 采到 2245 帧 / 6.53 s。
    /// 旧口径要求 span ≥ 10 × 0.8 = 8 s, 于是每一臂都判「未量成」, 标定拟合不出点,
    /// 已知答案的考题一道也判不成 —— 整条自检链在能跑的负载上反而全废。
    #[test]
    fn capture_window_asks_for_enough_samples_not_wall_clock() {
        let (ok, expected) = capture_window(6.53, 2245, 343.99, 3000, 10);
        assert!((expected - 8.72).abs() < 0.01, "{expected}");
        assert!(ok, "2245 帧的样本不该被墙钟判废");
        assert!(!capture_window(6.53, 0, 343.99, 3000, 10).0, "零帧不算");
        assert!(!capture_window(6.53, 50, 343.99, 3000, 10).0, "50 帧不够做统计");
        assert!(!capture_window(0.4, 2245, 343.99, 3000, 10).0, "跨度不到 1 s 不算");
        let (ok, expected) = capture_window(8.5, 255, 30.0, 100000, 10);
        assert!((expected - 10.0).abs() < 1e-9);
        assert!(ok, "低帧率负载跑满采集窗就算量成");
        let (ok, expected) = capture_window(8.5, 300, 0.0, 0, 10);
        assert!((expected - 10.0).abs() < 1e-9);
        assert!(ok);
    }

    #[test]
    fn a_case_missing_an_arm_is_abort_not_evidence() {        let root = std::env::temp_dir().join(format!("pf_selftest_miss_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        write_case(&root, "better5_1", "better", -0.05, 5);
        std::fs::remove_dir_all(root.join("better5_1").join("ctrl3")).unwrap();
        assert_eq!(completed_pairs(&root.join("better5_1")), 2);
        let j = judge_case(&root.join("better5_1"), 5, 70.0);
        assert_eq!(j["verdict"], "ABORT");
        let _ = std::fs::remove_dir_all(&root);
    }
}
