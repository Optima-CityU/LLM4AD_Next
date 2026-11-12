//! 一帧步骤清单: 抓 (真机) + 拆 (离线)。
//!
//! `phonefarm frame-audit capture`: 挂灰档层的只读 frameaudit 档进原神大世界, 按
//! `debug.knobs.frameaudit_go` 触发层连抓几帧, 把层落盘的 `knobs_frameaudit.json` 拉回来,
//! 收尾摘层、关游戏。层**不改任何渲染**; `--ts` 时层只多在 pass 前后写 GPU 时间戳查询。
//!
//! `phonefarm frame-audit analyze <fa.json>...`: 纯离线。把层记下的事实 (每个 pass 的附件 /
//! loadOp / storeOp / 尺寸 / 格式 / draw 数 / 描述符引用了哪些图, pass 外的拷贝 / 清屏 /
//! dispatch / UNDEFINED 丢弃) 按提交顺序排成时间线, 按「每张图的每个 mip/layer 被谁写、
//! 下一次被谁碰」找出疑似多余的步骤:
//!
//! * **白存** (dead store): pass 把附件 STORE 回显存, 但下一次碰这块内容的是覆盖/丢弃
//!   (CLEAR / DONT_CARE 加载、UNDEFINED 转换、整图拷贝覆盖), 没人读过。
//! * **白读** (useless load): pass 以 LOAD 开场, 但上一次对这块内容的操作是丢弃
//!   (UNDEFINED / DONT_CARE 存), 读回来的本来就是未定义内容。
//! * **清了又 LOAD**: pass 外整图清屏后紧接着一个 LOAD 它的 pass —— 能并成 loadOp CLEAR。
//! * **白清** (dead clear): 清屏之后没人读就被覆盖。
//! * **整个 pass 白画**: 它产出的每一块内容都是白存 (或干脆不存), 且没有 storage 写。
//!
//! 「读」用描述符引用来近似 (绑了 ≠ 采了), 所以是**保守**的: 只会把多余的判成有用,
//! 不会把有用的判成多余。带宽按 tile 架构的模型估: 附件字节 = 宽×高×每像素字节×采样数,
//! LOAD 读一遍、STORE 写一遍; UBWC 压缩与驱动的 sysmem 直绘模式都会让真实值更小,
//! 所以是**未压缩上限**。时间只用层的时间戳实测值, 没有就写不确定。

use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::fmt::Write as _;
use std::path::{Path, PathBuf};

const USAGE: &str = "用法:
  phonefarm frame-audit capture --out <目录> [--ts] [--captures N] [--gap 秒] [--so 本机层.so] [--pkg 包名] [--serial S]
      [--tap \"x,y;x,y\"] [--settle 秒] [--extras \"intent extras\"] [--json] [--evidence-origin device|synthetic]
      (非原神: 不判大世界, 确认层在出帧后依次点击再等 settle 秒; Megacity 例:
       --pkg com.unity.megacity.metro --extras \"--es scene city_static --es frames 100000\")
  phonefarm frame-audit storeop-check --rule <sig:i,...> --out <目录> [--rounds N] [--so 本机层.so] [--serial S] [--json] [--evidence-origin device|synthetic]
      (storeop 开关机制验证: 同一局里现场开/关, 同位置同视角截图逐像素比对 + effective 命中 + 不崩)
  phonefarm frame-audit recover --session <capture会话目录> --serial S [--json]
  phonefarm frame-audit analyze <knobs_frameaudit.json>... [--md 输出.md] [--json 输出.json]";

// ── VkFormat → 每像素字节 (颜色或深度部分, 模板部分) ──
pub(crate) fn fmt_bytes(fmt: u32) -> Option<(f64, f64)> {
    Some(match fmt {
        9..=15 => (1.0, 0.0),                      // R8
        16..=22 => (2.0, 0.0),                     // R8G8
        23..=36 => (3.0, 0.0),                     // R8G8B8 / B8G8R8
        37..=57 => (4.0, 0.0),                     // RGBA8 / BGRA8 / A8B8G8R8
        58..=69 => (4.0, 0.0),                     // A2R10G10B10 / A2B10G10R10
        70..=76 => (2.0, 0.0),                     // R16
        77..=83 => (4.0, 0.0),                     // R16G16
        84..=90 => (6.0, 0.0),                     // R16G16B16
        91..=97 => (8.0, 0.0),                     // R16G16B16A16
        98..=100 => (4.0, 0.0),                    // R32
        101..=103 => (8.0, 0.0),                   // R32G32
        104..=106 => (12.0, 0.0),                  // R32G32B32
        107..=109 => (16.0, 0.0),                  // R32G32B32A32
        122 | 123 => (4.0, 0.0),                   // B10G11R11 / E5B9G9R9
        124 => (2.0, 0.0),                         // D16
        125 => (4.0, 0.0),                         // X8_D24
        126 => (4.0, 0.0),                         // D32
        127 => (0.0, 1.0),                         // S8
        128 => (2.0, 1.0),                         // D16S8
        129 => (3.0, 1.0),                         // D24S8
        130 => (4.0, 1.0),                         // D32S8
        _ => return None,
    })
}

pub(crate) fn fmt_name(fmt: u32) -> String {
    match fmt {
        9 => "R8".into(),
        16 => "RG8".into(),
        37 => "RGBA8".into(),
        43 => "RGBA8_SRGB".into(),
        44 => "BGRA8".into(),
        50 => "BGRA8_SRGB".into(),
        58 => "A2RGB10".into(),
        64 => "A2BGR10".into(),
        76 => "R16F".into(),
        83 => "RG16F".into(),
        97 => "RGBA16F".into(),
        98 => "R32UI".into(),
        100 => "R32F".into(),
        103 => "RG32F".into(),
        109 => "RGBA32F".into(),
        122 => "R11G11B10F".into(),
        124 => "D16".into(),
        125 => "X8D24".into(),
        126 => "D32F".into(),
        127 => "S8".into(),
        128 => "D16S8".into(),
        129 => "D24S8".into(),
        130 => "D32FS8".into(),
        other => format!("fmt{other}"),
    }
}

fn load_name(op: u64) -> &'static str {
    match op {
        0 => "LOAD",
        1 => "CLEAR",
        2 => "DONT_CARE",
        1000400000 => "NONE",
        _ => "?",
    }
}
fn store_name(op: u64) -> &'static str {
    match op {
        0 => "STORE",
        1 => "DONT_CARE",
        1000301000 => "NONE",
        _ => "?",
    }
}

#[derive(Clone, Debug, Default)]
pub(crate) struct Img {
    pub w: u32,
    pub h: u32,
    pub fmt: u32,
    pub samples: u32,
    pub mips: u32,
    pub layers: u32,
    pub swap: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub(crate) struct Sub {
    img: u32,
    mip: u32,
    layer: u32,
    /// 0 = 颜色或深度, 1 = 模板。深度和模板的 load/store 各管各的 (实测原神 rp98:
    /// 深度 DONT_CARE 但模板 LOAD), 混成一块会把读模板的 pass 误当覆盖, 进而误报白存。
    asp: u8,
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Acc {
    /// 读了现有内容
    Read,
    /// 不读旧内容就整块重写 / 宣布旧内容作废 (CLEAR/DONT_CARE 加载、整图拷贝目标、清屏)
    Kill,
    /// 内容被丢成未定义 (UNDEFINED 转换、DONT_CARE 存、initialLayout=UNDEFINED)
    Discard,
    /// 把新内容写回显存 (STORE、拷贝/清屏的结果); 带「是谁写的」
    Produce(Producer),
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct Producer {
    ev: usize,
    /// pass 附件下标; 非 pass 事件为 usize::MAX
    att: usize,
}

/// 一个事件 (时间线上的一步)
#[derive(Clone, Debug)]
pub(crate) struct Ev {
    pub frame: usize,
    pub kind: String,
    pub raw: Value,
    pub w: u32,
    pub h: u32,
    pub ms: Option<f64>,
}

pub(crate) struct Timeline {
    pub imgs: HashMap<u32, Img>,
    pub evs: Vec<Ev>,
    pub frame_ends: Vec<usize>, // 第 i 帧最后一个事件之后的下标
    pub presents: Vec<u32>,
    pub ts_period_ns: f64,
    pub ts_enabled: bool,
    pub ts_available: u64,
    pub diag: Value,
    pub swap: (u32, u32),
}

fn u(v: &Value, k: &str) -> u64 {
    v.get(k).and_then(|x| x.as_u64()).unwrap_or(0)
}

impl Timeline {
    pub fn from_json(j: &Value) -> Result<Self, String> {
        if j.get("knob").and_then(|x| x.as_str()) != Some("gray_frameaudit") {
            return Err("不是 frameaudit 落盘文件 (knob != gray_frameaudit)".into());
        }
        let mut imgs = HashMap::new();
        for im in j["images"].as_array().cloned().unwrap_or_default() {
            imgs.insert(
                u(&im, "id") as u32,
                Img {
                    w: u(&im, "w") as u32,
                    h: u(&im, "h") as u32,
                    fmt: u(&im, "fmt") as u32,
                    samples: u(&im, "samples").max(1) as u32,
                    mips: u(&im, "mips").max(1) as u32,
                    layers: u(&im, "layers").max(1) as u32,
                    swap: im["swap"].as_bool().unwrap_or(false),
                },
            );
        }
        let period = j["ts_period_ns"].as_f64().unwrap_or(0.0);
        let mut ts: HashMap<i64, u64> = HashMap::new();
        for p in j["ts"].as_array().cloned().unwrap_or_default() {
            if let (Some(s), Some(t)) = (p[0].as_i64(), p[1].as_u64()) {
                ts.insert(s, t);
            }
        }
        let mut evs = Vec::new();
        let mut frame_ends = Vec::new();
        let mut presents = Vec::new();
        let frames = j["frames"].as_array().cloned().unwrap_or_default();
        if frames.is_empty() {
            return Err("frames 为空: 层没抓到帧".into());
        }
        for (fi, fr) in frames.iter().enumerate() {
            for sub in fr["submits"].as_array().cloned().unwrap_or_default() {
                for cb in sub["cbs"].as_array().cloned().unwrap_or_default() {
                    for e in cb["ev"].as_array().cloned().unwrap_or_default() {
                        let kind = e["k"].as_str().unwrap_or("?").to_string();
                        let ms = match (e["t0"].as_i64(), e["t1"].as_i64()) {
                            (Some(a), Some(b)) if a >= 0 && b >= 0 => match (ts.get(&a), ts.get(&b)) {
                                (Some(&x), Some(&y)) if y >= x && period > 0.0 => {
                                    Some((y - x) as f64 * period / 1e6)
                                }
                                _ => None,
                            },
                            _ => None,
                        };
                        evs.push(Ev {
                            frame: fi,
                            w: u(&e, "w") as u32,
                            h: u(&e, "h") as u32,
                            kind,
                            raw: e,
                            ms,
                        });
                    }
                }
            }
            frame_ends.push(evs.len());
            presents.push(u(fr, "present") as u32);
        }
        Ok(Self {
            imgs,
            evs,
            frame_ends,
            presents,
            ts_period_ns: period,
            ts_enabled: j["ts_enabled"].as_bool().unwrap_or(false),
            ts_available: u(j, "ts_available"),
            diag: j["diag"].clone(),
            swap: (u(&j["swapchain"], "w") as u32, u(&j["swapchain"], "h") as u32),
        })
    }

    fn frame_range(&self, f: usize) -> (usize, usize) {
        let lo = if f == 0 { 0 } else { self.frame_ends[f - 1] };
        (lo, self.frame_ends[f])
    }

    /// 某张图某 mip 的宽高
    fn mip_dims(&self, img: u32, mip: u32) -> Option<(u32, u32)> {
        let im = self.imgs.get(&img)?;
        Some(((im.w >> mip).max(1), (im.h >> mip).max(1)))
    }

    /// 这张图有哪些 aspect: 深度+模板格式两个, 其余一个
    fn aspects(&self, img: u32) -> &'static [u8] {
        match self.imgs.get(&img).and_then(|i| fmt_bytes(i.fmt)) {
            Some((c, st)) if c > 0.0 && st > 0.0 => &[0, 1],
            _ => &[0],
        }
    }

    /// 把 [img, m0, nm, l0, nl] 引用展开成子资源 (255 = 剩余全部)
    fn expand(&self, r: &Value) -> Vec<Sub> {
        let a: Vec<u64> = r.as_array().map(|x| x.iter().filter_map(|y| y.as_u64()).collect()).unwrap_or_default();
        if a.len() < 5 || a[0] == 0 {
            return vec![];
        }
        let img = a[0] as u32;
        let (mips, layers) = self.imgs.get(&img).map(|i| (i.mips, i.layers)).unwrap_or((1, 1));
        let m0 = a[1] as u32;
        let nm = if a[2] >= 255 { mips.saturating_sub(m0).max(1) } else { a[2] as u32 };
        let l0 = a[3] as u32;
        let nl = if a[4] >= 255 { layers.saturating_sub(l0).max(1) } else { a[4] as u32 };
        let mut v = Vec::new();
        for m in m0..m0 + nm.min(16) {
            for l in l0..l0 + nl.min(64) {
                for &asp in self.aspects(img) {
                    v.push(Sub { img, mip: m, layer: l, asp });
                }
            }
        }
        v
    }

    fn sub3(v: &Value) -> Option<Sub> {
        let a = v.as_array()?;
        let img = a.first()?.as_u64()? as u32;
        if img == 0 {
            return None;
        }
        Some(Sub { img, mip: a.get(1)?.as_u64()? as u32, layer: a.get(2)?.as_u64()? as u32, asp: 0 })
    }

    /// 同 sub3, 但带上全部 aspect (拷贝/blit 一次搬深度和模板两块)
    fn subs3(&self, v: &Value) -> Vec<Sub> {
        Self::sub3(v)
            .map(|s| self.aspects(s.img).iter().map(|&asp| Sub { asp, ..s }).collect())
            .unwrap_or_default()
    }

    /// 一个事件按先后顺序对各子资源做了什么
    fn accesses(&self, idx: usize) -> Vec<(Sub, Acc)> {
        let e = &self.evs[idx];
        let r = &e.raw;
        let mut out = Vec::new();
        let reads = |out: &mut Vec<(Sub, Acc)>, key: &str| {
            for x in r[key].as_array().cloned().unwrap_or_default() {
                for s in self.expand(&x) {
                    out.push((s, Acc::Read));
                }
            }
        };
        match e.kind.as_str() {
            "pass" => {
                reads(&mut out, "rd");
                reads(&mut out, "wr");
                let atts = r["att"].as_array().cloned().unwrap_or_default();
                for (ai, a) in atts.iter().enumerate() {
                    let img = u(a, "i") as u32;
                    if img == 0 {
                        continue;
                    }
                    let base = Sub { img, mip: u(a, "m") as u32, layer: u(a, "l") as u32, asp: 0 };
                    let full = self.mip_dims(img, base.mip).map(|(w, h)| e.w >= w && e.h >= h).unwrap_or(true);
                    let role = u(a, "ro");
                    let has_st = fmt_bytes(u(a, "f") as u32).map(|b| b.0 > 0.0 && b.1 > 0.0).unwrap_or(false);
                    // (aspect, loadOp, storeOp): 深度模板格式拆成两路
                    let mut ops = vec![(0u8, if role == 2 { 2 } else { u(a, "lo") }, u(a, "st"))];
                    if has_st {
                        ops.push((1u8, u(a, "slo"), u(a, "sst")));
                    }
                    for (asp, lo, st) in ops {
                        let s = Sub { asp, ..base };
                        if u(a, "il") == 0 {
                            out.push((s, Acc::Discard));
                        }
                        out.push((
                            s,
                            match lo {
                                0 => Acc::Read,
                                1 | 2 if full => Acc::Kill,
                                1 | 2 => Acc::Read, // 只清/丢 render area 的一部分, 其余保留 = 当读
                                _ => Acc::Read,     // NONE: 不碰, 内容保留
                            },
                        ));
                        if st == 0 {
                            out.push((s, Acc::Produce(Producer { ev: idx, att: ai })));
                        } else if st == 1 {
                            out.push((s, Acc::Discard));
                        }
                    }
                }
                for x in r["wr"].as_array().cloned().unwrap_or_default() {
                    for s in self.expand(&x) {
                        out.push((s, Acc::Produce(Producer { ev: idx, att: usize::MAX })));
                    }
                }
            }
            "dispatch" => {
                reads(&mut out, "rd");
                reads(&mut out, "wr");
                for x in r["wr"].as_array().cloned().unwrap_or_default() {
                    for s in self.expand(&x) {
                        out.push((s, Acc::Produce(Producer { ev: idx, att: usize::MAX })));
                    }
                }
            }
            "copy_img" | "blit" | "resolve" | "buf2img" | "img2buf" | "clear_color" | "clear_ds" | "discard" => {
                for s in self.subs3(&r["src"]) {
                    out.push((s, Acc::Read));
                }
                let dsts: Vec<Sub> = match e.kind.as_str() {
                    "clear_color" | "clear_ds" | "discard" => {
                        let d = &r["dst"];
                        let a: Vec<u64> = d.as_array().map(|x| x.iter().filter_map(|y| y.as_u64()).collect()).unwrap_or_default();
                        if a.len() == 3 {
                            self.expand(&json!([a[0], a[1], u(r, "dnm"), a[2], u(r, "dnl")]))
                        } else {
                            vec![]
                        }
                    }
                    "img2buf" => vec![],
                    _ => self.subs3(&r["dst"]),
                };
                let ex = r["ex"].as_array().cloned().unwrap_or_default();
                let (ew, eh) = (ex.first().and_then(|x| x.as_u64()).unwrap_or(0) as u32, ex.get(1).and_then(|x| x.as_u64()).unwrap_or(0) as u32);
                for s in dsts {
                    if e.kind == "discard" {
                        out.push((s, Acc::Discard));
                        continue;
                    }
                    let full = matches!(e.kind.as_str(), "clear_color" | "clear_ds")
                        || self.mip_dims(s.img, s.mip).map(|(w, h)| ew >= w && eh >= h).unwrap_or(false);
                    out.push((s, if full { Acc::Kill } else { Acc::Read }));
                    out.push((s, Acc::Produce(Producer { ev: idx, att: usize::MAX })));
                }
            }
            _ => {}
        }
        out
    }

    fn att_bytes(&self, ev: &Ev, att: &Value) -> (f64, f64, f64, f64) {
        // (颜色/深度 load, 模板 load, 颜色/深度 store, 模板 store) —— 未压缩上限
        let f = u(att, "f") as u32;
        let Some((cb, sb)) = fmt_bytes(f) else { return (0.0, 0.0, 0.0, 0.0) };
        let px = ev.w as f64 * ev.h as f64 * u(att, "s").max(1) as f64;
        let role = u(att, "ro");
        let lo = if role == 2 { 2 } else { u(att, "lo") };
        (
            if lo == 0 { px * cb } else { 0.0 },
            if sb > 0.0 && u(att, "slo") == 0 { px * sb } else { 0.0 },
            if u(att, "st") == 0 { px * cb } else { 0.0 },
            if sb > 0.0 && u(att, "sst") == 0 { px * sb } else { 0.0 },
        )
    }
}

/// 一条「疑似多余」
#[derive(Clone, Debug)]
pub(crate) struct Finding {
    pub rule: &'static str,
    pub frame: usize,
    pub ev: usize,
    pub att: Option<usize>,
    pub bytes: f64,
    pub ms: Option<f64>,
    pub certain: bool,
    pub why: String,
}

/// 事件的稳定身份 (跨帧对齐用): 类型 + rp 序号 + 尺寸 + 该类型在帧内第几次出现
fn ev_sig(ev: &Ev) -> String {
    match ev.kind.as_str() {
        // lead 的写回改写开关按层给的 sig (附件描述哈希) 定位 pass; 有就带上
        "pass" => match ev.raw.get("sig").filter(|v| !v.is_null()) {
            Some(sig) => format!("pass rp{} {}x{} sig={}", u(&ev.raw, "rp"), ev.w, ev.h, sig.as_str().map(|x| x.to_string()).unwrap_or_else(|| sig.to_string())),
            None => format!("pass rp{} {}x{}", u(&ev.raw, "rp"), ev.w, ev.h),
        },
        k => k.to_string(),
    }
}

pub(crate) fn find(t: &Timeline) -> Vec<Finding> {
    // 自检: 有 draw 的 pass 却一处描述符引用都没看到 = 层看不见这个游戏的「读」
    // (例: 描述符走了层没钩的路径)。这时任何「没人读」的结论都是假阳性, 一律不报。
    // 2026-10-11 Megacity (Unity 6) 实测踩到: 未钩描述符模板时 4096² 阴影图被判成白存。
    let draws: u64 = t.evs.iter().filter(|e| e.kind == "pass").map(|e| u(&e.raw, "dr") + u(&e.raw, "dri") + u(&e.raw, "drn")).sum();
    let refs: usize = t.evs.iter().map(|e| e.raw["rd"].as_array().map(|a| a.len()).unwrap_or(0) + e.raw["wr"].as_array().map(|a| a.len()).unwrap_or(0)).sum();
    if draws > 0 && refs == 0 {
        return vec![Finding {
            rule: "reads_invisible",
            frame: 0,
            ev: 0,
            att: None,
            bytes: 0.0,
            ms: None,
            certain: true,
            why: format!("有 {draws} 笔 draw 却没看到任何描述符引用: 层看不见这个游戏的读, 白存/白画/transient 结论全部不可信, 已全部不报 (诊断 {})", t.diag),
        }];
    }
    // 每个子资源的访问序列 (事件下标, 访问)
    let mut seq: BTreeMap<Sub, Vec<(usize, Acc)>> = BTreeMap::new();
    for i in 0..t.evs.len() {
        for (s, a) in t.accesses(i) {
            seq.entry(s).or_default().push((i, a));
        }
        // 帧尾 present = 读送显的那张 swapchain 图
        if let Some(fi) = t.frame_ends.iter().position(|&e| e == i + 1) {
            let p = t.presents[fi];
            if p != 0 {
                // 用越界的虚拟下标 (evs.len()+帧号) 代表 present, 免得和帧内最后一个事件撞号
                seq.entry(Sub { img: p, mip: 0, layer: 0, asp: 0 }).or_default().push((t.evs.len() + fi, Acc::Read));
            }
        }
    }
    let last_frame = t.frame_ends.len() - 1;
    let mut out = Vec::new();
    // pass 的每个 produce 是否白存: ev -> (产出数, 白存数, 不确定数)
    let mut pass_prod: HashMap<usize, (u32, u32, u32)> = HashMap::new();
    for (s, list) in &seq {
        for (k, (i, a)) in list.iter().enumerate() {
            if *i >= t.evs.len() {
                continue; // present
            }
            let ev = &t.evs[*i];
            match a {
                Acc::Produce(p) => {
                    let next = list[k + 1..].iter().find(|(j, _)| j != i).map(|x| x.1);
                    let ent = pass_prod.entry(p.ev).or_insert((0, 0, 0));
                    ent.0 += 1;
                    let (dead, certain) = match next {
                        Some(Acc::Kill) | Some(Acc::Discard) => (true, true),
                        Some(_) => (false, true),
                        // 看到抓帧末尾都没人碰: 至少一整帧没人读才算「疑似」, 否则不判
                        None => (ev.frame < last_frame, false),
                    };
                    if !dead {
                        if next.is_none() {
                            ent.2 += 1;
                        }
                        continue;
                    }
                    ent.1 += 1;
                    if ev.frame == last_frame {
                        continue; // 只在后面还有整帧可看的帧上报
                    }
                    if ev.kind == "pass" && p.att != usize::MAX {
                        let att = &ev.raw["att"][p.att];
                        let (_, _, cs, ss) = t.att_bytes(ev, att);
                        out.push(Finding {
                            rule: "dead_store",
                            frame: ev.frame,
                            ev: *i,
                            att: Some(p.att),
                            bytes: if s.asp == 1 { ss } else { cs },
                            ms: None,
                            certain,
                            why: format!(
                                "附件{} ({} {}) STORE 后{}",
                                p.att,
                                fmt_name(u(att, "f") as u32),
                                img_tag(t, s),
                                if certain { "下一次是覆盖/丢弃, 没人读" } else { "到抓帧结束(≥1 整帧)没人读" }
                            ),
                        });
                    } else if matches!(ev.kind.as_str(), "clear_color" | "clear_ds") {
                        let px = t.mip_dims(s.img, s.mip).map(|(w, h)| w as f64 * h as f64).unwrap_or(0.0);
                        let bpp = t.imgs.get(&s.img).and_then(|im| fmt_bytes(im.fmt)).map(|b| b.0 + b.1).unwrap_or(0.0);
                        out.push(Finding {
                            rule: "dead_clear",
                            frame: ev.frame,
                            ev: *i,
                            att: None,
                            bytes: px * bpp,
                            ms: None,
                            certain,
                            why: format!("清屏 {} 后没人读就被覆盖/丢弃", img_tag(t, s)),
                        });
                    } else if matches!(ev.kind.as_str(), "copy_img" | "blit" | "resolve" | "buf2img") {
                        out.push(Finding {
                            rule: "dead_copy",
                            frame: ev.frame,
                            ev: *i,
                            att: None,
                            bytes: 0.0,
                            ms: None,
                            certain,
                            why: format!("{} 写的 {} 没人读", ev.kind, img_tag(t, s)),
                        });
                    }
                }
                Acc::Read => {
                    // 白读: 只看 pass 的 LOAD 附件
                    if ev.kind != "pass" {
                        continue;
                    }
                    let atts = ev.raw["att"].as_array().cloned().unwrap_or_default();
                    let Some((ai, att)) = atts.iter().enumerate().find(|(_, a)| {
                        u(a, "i") as u32 == s.img
                            && u(a, "m") as u32 == s.mip
                            && u(a, "l") as u32 == s.layer
                            && u(a, if s.asp == 1 { "slo" } else { "lo" }) == 0
                            && u(a, "ro") != 2
                    }) else {
                        continue;
                    };
                    // 只认「这次 Read 就是这个附件的 LOAD」: 同一事件里在它之前没有别的 Read
                    if list[..k].iter().any(|(j, x)| j == i && *x == Acc::Read) {
                        continue;
                    }
                    let prev = list[..k].iter().rev().find(|(j, _)| j != i).copied();
                    let own_discard = list[..k].iter().any(|(j, x)| j == i && *x == Acc::Discard);
                    let (ld0, sl0, _, _) = t.att_bytes(ev, att);
                    let (ld, sl) = if s.asp == 1 { (0.0, sl0) } else { (ld0, 0.0) };
                    if own_discard || matches!(prev, Some((_, Acc::Discard))) {
                        out.push(Finding {
                            rule: "useless_load",
                            frame: ev.frame,
                            ev: *i,
                            att: Some(ai),
                            bytes: ld + sl,
                            ms: None,
                            certain: true,
                            why: format!(
                                "附件{} ({} {}) LOAD, 但{}",
                                ai,
                                fmt_name(u(att, "f") as u32),
                                img_tag(t, s),
                                if own_discard { "本 pass initialLayout=UNDEFINED, 读回来的是未定义内容" } else { "上一步已把它丢成未定义" }
                            ),
                        });
                    } else if let Some((j, Acc::Produce(_))) = prev {
                        if matches!(t.evs[j].kind.as_str(), "clear_color" | "clear_ds") {
                            out.push(Finding {
                                rule: "clear_then_load",
                                frame: ev.frame,
                                ev: *i,
                                att: Some(ai),
                                bytes: 2.0 * (ld + sl),
                                ms: None,
                                certain: true,
                                why: format!("{} 先在 pass 外清屏, 紧接着 LOAD 进来 —— 可并成 loadOp=CLEAR (省一次整图写+读)", img_tag(t, s)),
                            });
                        }
                    }
                }
                _ => {}
            }
        }
    }
    // 整个 pass 白画: 产出全部白存 (或不产出), 且有附件
    for (i, ev) in t.evs.iter().enumerate() {
        if ev.kind != "pass" || ev.frame == last_frame {
            continue;
        }
        let natt = ev.raw["att"].as_array().map(|a| a.len()).unwrap_or(0);
        if natt == 0 {
            continue;
        }
        let (n, dead, _unk) = pass_prod.get(&i).copied().unwrap_or((0, 0, 0));
        // pass 里开了查询 (遮挡查询最常见): 产出是查询结果, 不写附件也不是白画
        let queries = u(&ev.raw, "qo") + u(&ev.raw, "qx");
        if n == dead && queries == 0 {
            let bytes: f64 = ev.raw["att"].as_array().unwrap().iter().map(|a| {
                let (a1, a2, a3, a4) = t.att_bytes(ev, a);
                a1 + a2 + a3 + a4
            }).sum();
            out.push(Finding {
                rule: "dead_pass",
                frame: ev.frame,
                ev: i,
                att: None,
                bytes,
                ms: ev.ms,
                certain: false,
                why: format!(
                    "{} 产出的 {} 块内容{}; draw {} 笔, pass 内无查询 (不确定点: fragment 里写 storage buffer 层看不见{})",
                    ev_sig(ev),
                    n,
                    if n == 0 { "一块都不存回显存" } else { "全部没人读" },
                    u(&ev.raw, "dr") + u(&ev.raw, "dri") + u(&ev.raw, "drn"),
                    if ev.raw.get("qo").is_none() { "; 这份抓取的层还不记查询, 也看不见查询" } else { "" }
                ),
            });
        }
    }
    // 能做成 transient (LAZILY_ALLOCATED, 只活在 tile 上) 的附件: 在所有抓到的帧里,
    // 这块内容从来没被「别的事件」读过 (只在 pass 内当 color/depth/input 用), 且每次进 pass
    // 都是 CLEAR/DONT_CARE 开场。现状若还在 STORE, 写回全部是浪费; 改 transient 连显存都不占。
    let mut by_img: BTreeMap<u32, (bool, Vec<usize>)> = BTreeMap::new(); // img -> (被别人读过, 用到它的 pass)
    for (s, list) in &seq {
        let e = by_img.entry(s.img).or_insert((false, vec![]));
        let mut last_prod: Option<usize> = None;
        for (i, a) in list {
            match a {
                Acc::Produce(p) => last_prod = Some(p.ev),
                Acc::Read => {
                    if last_prod.is_some_and(|p| p != *i) || *i >= t.evs.len() {
                        e.0 = true;
                    }
                    if *i < t.evs.len() && t.evs[*i].kind != "pass" {
                        e.0 = true;
                    }
                }
                _ => {}
            }
            if *i < t.evs.len() && t.evs[*i].kind == "pass" && !e.1.contains(i) {
                e.1.push(*i);
            }
            if *i < t.evs.len() && t.evs[*i].kind != "pass" && !matches!(a, Acc::Discard) {
                e.0 = true; // 被拷贝/清屏/dispatch 碰过, 不当 transient
            }
        }
    }
    for (img, (read, passes)) in by_img {
        if read || passes.is_empty() {
            continue;
        }
        let Some(im) = t.imgs.get(&img) else { continue };
        if im.swap {
            continue;
        }
        // 每次用到它, 加载必须是 CLEAR/DONT_CARE, 且不能被描述符采样
        let ok = passes.iter().all(|&i| {
            let ev = &t.evs[i];
            let as_att = ev.raw["att"].as_array().cloned().unwrap_or_default().into_iter().find(|a| u(a, "i") as u32 == img);
            let sampled = ["rd", "wr"].iter().any(|k| {
                ev.raw[*k].as_array().map(|v| v.iter().any(|x| x[0].as_u64() == Some(img as u64))).unwrap_or(false)
            });
            matches!(as_att, Some(ref a) if u(a, "lo") != 0 || u(a, "ro") == 2) && !sampled
        });
        if !ok {
            continue;
        }
        let first = passes[0];
        let ev = &t.evs[first];
        if ev.frame == last_frame {
            continue;
        }
        let stored: f64 = passes
            .iter()
            .filter(|&&i| t.evs[i].frame == ev.frame)
            .map(|&i| {
                let e2 = &t.evs[i];
                e2.raw["att"].as_array().cloned().unwrap_or_default().iter()
                    .filter(|a| u(a, "i") as u32 == img)
                    .map(|a| { let (_, _, c, s2) = t.att_bytes(e2, a); c + s2 })
                    .sum::<f64>()
            })
            .sum();
        out.push(Finding {
            rule: "transient_ok",
            frame: ev.frame,
            ev: first,
            att: None,
            bytes: stored,
            ms: None,
            certain: true,
            why: format!(
                "{} ({}) 只在 pass 内用、从不被别的步骤读: 可建成 LAZILY_ALLOCATED/transient; 当前每帧仍写回 {:.0} KB",
                img_tag(t, &Sub { img, mip: 0, layer: 0, asp: 0 }),
                fmt_name(im.fmt),
                stored / 1e3
            ),
        });
    }
    out.sort_by(|a, b| a.ev.cmp(&b.ev).then(a.att.cmp(&b.att)));
    out
}

fn img_tag(t: &Timeline, s: &Sub) -> String {
    match t.imgs.get(&s.img) {
        Some(im) => format!(
            "图#{}{}{} {}x{}{}{}{}",
            s.img,
            if im.swap { "(屏幕)" } else { "" },
            if s.asp == 1 { "(模板)" } else { "" },
            im.w,
            im.h,
            if im.samples > 1 { format!(" x{}MSAA", im.samples) } else { String::new() },
            if s.mip > 0 { format!(" mip{}", s.mip) } else { String::new() },
            if im.layers > 1 { format!(" layer{}", s.layer) } else { String::new() }
        ),
        None => format!("图#{}", s.img),
    }
}

/// 帧的步骤总览统计
pub(crate) fn frame_stats(t: &Timeline, f: usize) -> Value {
    let (lo, hi) = t.frame_range(f);
    let mut kinds: BTreeMap<String, u64> = BTreeMap::new();
    let (mut draws, mut load_b, mut store_b, mut ms_sum, mut timed, mut passes) = (0u64, 0.0, 0.0, 0.0, 0u64, 0u64);
    let mut lo_load = 0u64;
    let mut empty_pass = 0u64;
    let mut query_passes = 0u64;
    let mut refs = 0u64;
    for ev in &t.evs[lo..hi] {
        *kinds.entry(ev.kind.clone()).or_default() += 1;
        refs += (ev.raw["rd"].as_array().map(|a| a.len()).unwrap_or(0) + ev.raw["wr"].as_array().map(|a| a.len()).unwrap_or(0)) as u64;
        if ev.kind == "pass" {
            passes += 1;
            let d = u(&ev.raw, "dr") + u(&ev.raw, "dri") + u(&ev.raw, "drn");
            draws += d;
            if d == 0 && u(&ev.raw, "ca") == 0 {
                empty_pass += 1;
            }
            if u(&ev.raw, "qo") + u(&ev.raw, "qx") > 0 {
                query_passes += 1;
            }
            for a in ev.raw["att"].as_array().cloned().unwrap_or_default() {
                let (a1, a2, a3, a4) = t.att_bytes(ev, &a);
                load_b += a1 + a2;
                store_b += a3 + a4;
                if u(&a, "lo") == 0 && u(&a, "ro") != 2 {
                    lo_load += 1;
                }
            }
            if let Some(m) = ev.ms {
                ms_sum += m;
                timed += 1;
            }
        }
    }
    json!({
        "frame": f,
        "events": hi - lo,
        "kinds": kinds,
        "passes": passes,
        "passes_timed": timed,
        "pass_ms_sum": (ms_sum * 1000.0).round() / 1000.0,
        "draws": draws,
        "empty_passes": empty_pass,
        "query_passes": query_passes,
        "descriptor_refs": refs,
        "load_attachments": lo_load,
        "model_load_mb": (load_b / 1e6 * 100.0).round() / 100.0,
        "model_store_mb": (store_b / 1e6 * 100.0).round() / 100.0,
    })
}

fn ev_line(t: &Timeline, i: usize) -> String {
    let ev = &t.evs[i];
    let r = &ev.raw;
    let ms = ev.ms.map(|m| format!("{m:.3}")).unwrap_or_else(|| "-".into());
    match ev.kind.as_str() {
        "pass" => {
            let atts: Vec<String> = r["att"]
                .as_array()
                .cloned()
                .unwrap_or_default()
                .iter()
                .map(|a| {
                    let role = match u(a, "ro") {
                        0 => "C",
                        1 => "D",
                        2 => "R",
                        3 => "I",
                        _ => "-",
                    };
                    let st = fmt_bytes(u(a, "f") as u32).map(|b| b.1 > 0.0).unwrap_or(false);
                    format!(
                        "{}:{}#{}{} {}/{}{}",
                        role,
                        fmt_name(u(a, "f") as u32),
                        u(a, "i"),
                        if u(a, "s") > 1 { format!("x{}", u(a, "s")) } else { String::new() },
                        load_name(u(a, "lo")),
                        store_name(u(a, "st")),
                        if st { format!(" s:{}/{}", load_name(u(a, "slo")), store_name(u(a, "sst"))) } else { String::new() }
                    )
                })
                .collect();
            let (mut ld, mut stb) = (0.0, 0.0);
            for a in r["att"].as_array().cloned().unwrap_or_default() {
                let (a1, a2, a3, a4) = t.att_bytes(ev, &a);
                ld += a1 + a2;
                stb += a3 + a4;
            }
            format!(
                "| {} | pass rp{}{}{} | {}x{} | {} | {} | {} | {:.0} / {:.0} | {} |",
                i,
                u(r, "rp"),
                if r["dyn"].as_bool() == Some(true) { " (dyn)" } else { "" },
                r["sig"].as_str().map(|x| format!("<br>`{x}`")).unwrap_or_default(),
                ev.w,
                ev.h,
                atts.join("<br>"),
                u(r, "dr") + u(r, "dri") + u(r, "drn"),
                r["rd"].as_array().map(|a| a.len()).unwrap_or(0),
                ld / 1e3,
                stb / 1e3,
                ms
            )
        }
        "dispatch" => format!(
            "| {} | dispatch | {:?} | rd {} / wr {} | - | - | - | - |",
            i,
            r["g"].as_array().map(|a| a.iter().filter_map(|x| x.as_u64()).collect::<Vec<_>>()).unwrap_or_default(),
            r["rd"].as_array().map(|a| a.len()).unwrap_or(0),
            r["wr"].as_array().map(|a| a.len()).unwrap_or(0)
        ),
        k => format!(
            "| {} | {} | {}x{} | src {} → dst {} | - | - | - | - |",
            i,
            k,
            r["ex"][0].as_u64().unwrap_or(0),
            r["ex"][1].as_u64().unwrap_or(0),
            Timeline::sub3(&r["src"]).map(|s| img_tag(t, &s)).unwrap_or_else(|| "-".into()),
            Timeline::sub3(&r["dst"]).map(|s| img_tag(t, &s)).unwrap_or_else(|| "-".into())
        ),
    }
}

pub(crate) fn analyze(paths: &[PathBuf]) -> Result<(String, Value), String> {
    let mut md = String::new();
    let mut js = Vec::new();
    for p in paths {
        let text = std::fs::read_to_string(p).map_err(|e| format!("读 {}: {e}", p.display()))?;
        let j: Value = serde_json::from_str(&text).map_err(|e| format!("解析 {}: {e}", p.display()))?;
        let t = Timeline::from_json(&j)?;
        let fs = find(&t);
        let stats: Vec<Value> = (0..t.frame_ends.len()).map(|f| frame_stats(&t, f)).collect();
        // 按规则 × 是否确定 汇总 (每帧平均, 只算有后续整帧可看的帧)
        let nfr = (t.frame_ends.len() - 1).max(1) as f64;
        let mut agg: BTreeMap<String, (u64, f64, f64)> = BTreeMap::new();
        for x in &fs {
            let k = format!("{}{}", x.rule, if x.certain { "" } else { "(疑似)" });
            let e = agg.entry(k).or_default();
            e.0 += 1;
            e.1 += x.bytes;
            e.2 += x.ms.unwrap_or(0.0);
        }
        let _ = writeln!(md, "## {}\n", p.display());
        let _ = writeln!(
            md,
            "swapchain {}x{}，抓到 {} 帧，时间戳 {}（可用 {} 个，周期 {:.3} ns），诊断 {}\n",
            t.swap.0,
            t.swap.1,
            t.frame_ends.len(),
            if t.ts_enabled { "开" } else { "关" },
            t.ts_available,
            t.ts_period_ns,
            t.diag
        );
        let _ = writeln!(md, "### 每帧统计\n\n```json\n{}\n```\n", serde_json::to_string_pretty(&stats).unwrap_or_default());
        let _ = writeln!(md, "### 疑似多余汇总（每帧平均）\n\n| 规则 | 条数/帧 | 模型字节 MB/帧 | 实测 ms/帧 |\n|---|---|---|---|");
        for (k, (n, b, ms)) in &agg {
            let _ = writeln!(md, "| {} | {:.1} | {:.2} | {:.3} |", k, *n as f64 / nfr, b / 1e6 / nfr, ms / nfr);
        }
        let _ = writeln!(md, "\n### 第 0 帧步骤清单\n\n| # | 步骤 | 尺寸 | 附件 (角色:格式#图 load/store) | draw | 采样引用 | 模型读/写 KB | GPU ms |\n|---|---|---|---|---|---|---|---|");
        let (lo, hi) = t.frame_range(0);
        for i in lo..hi {
            let _ = writeln!(md, "{}", ev_line(&t, i));
        }
        let _ = writeln!(md, "\n### 疑似多余明细（第 0 帧）\n\n| # | 规则 | 确定 | 模型 KB | 说明 |\n|---|---|---|---|---|");
        for x in fs.iter().filter(|x| x.frame == 0) {
            let _ = writeln!(md, "| {} | {} | {} | {:.0} | {} |", x.ev, x.rule, if x.certain { "是" } else { "疑似" }, x.bytes / 1e3, x.why);
        }
        let _ = writeln!(md);
        js.push(json!({
            "file": p.display().to_string(),
            "frames": stats,
            "summary_per_frame": agg.iter().map(|(k, (n, b, ms))| json!({"rule": k, "count": *n as f64 / nfr, "model_mb": b / 1e6 / nfr, "ms": ms / nfr})).collect::<Vec<_>>(),
            "findings": fs.iter().map(|x| json!({
                "rule": x.rule, "frame": x.frame, "ev": x.ev, "att": x.att, "sig": ev_sig(&t.evs[x.ev]),
                "bytes": x.bytes, "ms": x.ms, "certain": x.certain, "why": x.why})).collect::<Vec<_>>(),
            "pass_times": t.evs.iter().enumerate().filter(|(_, e)| e.kind == "pass").map(|(i, e)| json!({"ev": i, "frame": e.frame, "sig": ev_sig(e), "ms": e.ms, "draws": u(&e.raw, "dr") + u(&e.raw, "dri") + u(&e.raw, "drn")})).collect::<Vec<_>>(),
        }));
    }
    Ok((md, Value::Array(js)))
}

// ── 真机抓帧 ──

fn parse_taps(s: &str) -> Vec<(u32, u32)> {
    s.split(';')
        .filter_map(|p| {
            let (x, y) = p.split_once(',')?;
            Some((x.trim().parse().ok()?, y.trim().parse().ok()?))
        })
        .collect()
}

// This finite capture owns every transport, including scene input and trigger writes.
fn capture_scene(
    session: &crate::gray_session::Session,
    pkg: &str,
    taps: &[(u32, u32)],
    settle: u64,
) -> Result<(), String> {
    let mut first = None;
    for attempt in 0..40 {
        if let Some(marker) = session.marker()? {
            if marker["frames"].as_u64().is_some() {
                first = Some(marker);
                break;
            }
        }
        if attempt < 39 { std::thread::sleep(std::time::Duration::from_secs(2)); }
    }
    let first = first.ok_or("layer marker did not appear; capture not started")?;
    // Keep the global loader exposure only until this target proves it loaded the layer.
    session.narrow()?;
    if pkg == crate::plugins::genshin::PACKAGE && taps.is_empty() {
        std::thread::sleep(std::time::Duration::from_secs(30));
        session.tap(1342, 651)?;
        for _ in 0..18 {
            let a = session.marker()?.ok_or("layer marker disappeared")?;
            std::thread::sleep(std::time::Duration::from_secs(10));
            let b = session.marker()?.ok_or("layer marker disappeared")?;
            let frames = b["frames"].as_i64().unwrap_or(0) - a["frames"].as_i64().unwrap_or(0);
            let passes = b["render_pass_begins"].as_i64().unwrap_or(0)
                - a["render_pass_begins"].as_i64().unwrap_or(0);
            if frames > 0 && passes / frames >= 25 {
                std::thread::sleep(std::time::Duration::from_secs(settle.min(60)));
                return Ok(());
            }
        }
        return Err("target did not reach the existing world-scene criterion".into());
    }
    for &(x, y) in taps {
        session.tap(x, y)?;
        std::thread::sleep(std::time::Duration::from_secs(5));
    }
    std::thread::sleep(std::time::Duration::from_secs(settle));
    let last = session.marker()?.ok_or("layer marker disappeared")?;
    if last["frames"].as_u64().zip(first["frames"].as_u64()).is_some_and(|(b,a)| b > a) {
        Ok(())
    } else {
        Err("target present count did not advance".into())
    }
}

#[allow(clippy::too_many_arguments)]
fn capture(serial: &str, out: &Path, so: Option<String>, ts: bool, captures: u32, gap: u64, pkg: &str, taps: &[(u32, u32)], settle: u64, extras: Option<String>, origin: &str) -> Result<Value, String> {
    let lease = crate::devlease::Lease::target(Some(serial), "frame-audit-capture")?;
    let session = crate::gray_session::Session::new(lease, out, pkg, origin)?;
    let mut frames = Vec::new();
    let collected: Result<(), String> = (|| {
        session.prepare()?;
        session.install(so.as_deref(), ts, extras.as_deref())?;
        capture_scene(&session, pkg, taps, settle)?;
        for go in 1..=captures {
            let image = session.dir.join(format!("fa_{go}.png"));
            session.screenshot(&image)?;
            session.trigger(go)?;
            let mut audit = None;
            for attempt in 0..45 {
                if let Some(value) = session.audit(go)? { audit = Some(value); break; }
                if attempt < 44 { std::thread::sleep(std::time::Duration::from_secs(2)); }
            }
            let audit = audit.ok_or_else(|| format!("capture trigger {go} produced no matching audit"))?;
            let path = session.dir.join(format!("fa_{go}.json"));
            crate::confirmation_transport::new_file(&path, &serde_json::to_vec_pretty(&audit).map_err(|e| e.to_string())?)?;
            frames.push(json!({"go":go,"file":path,"image":image,"wall":chrono::Utc::now().to_rfc3339()}));
            if go < captures { std::thread::sleep(std::time::Duration::from_secs(gap)); }
        }
        Ok(())
    })();
    let mut error = collected.err();
    let collection = json!({"schema":"phonefarm/gray-capture-collection@1","serial":serial,"package":pkg,
        "session":session.dir,"evidence_origin":origin,"frames":frames,"error":error,
        "request":{"timestamp_queries":ts,"captures":captures,"gap_s":gap,"taps":taps,
            "settle_s":settle,"launch_extras":extras,"layer_source":so},
        "collection_ok":error.is_none(),"game_state_restored":false});
    let record = session.dir.join("collection.json");
    if let Err(e) = crate::confirmation_transport::new_file(&record, &serde_json::to_vec_pretty(&collection).unwrap()) {
        error = Some(match error {Some(prior)=>format!("{prior}; collection evidence: {e}"),None=>e});
    }
    // No local evidence failure may skip the safe recovery attempt.
    let recovery = match session.finish(error.as_deref()) {
        Ok(v) => v,
        Err(e) => json!({"status":"required","error":e,"session":session.dir}),
    };
    let meta = session.dir.join("capture_meta.json");
    let mut report = json!({"schema":"phonefarm/gray-capture@1","serial":serial,"package":pkg,
        "out":session.dir,"session":session.dir,"collection":record,"frames":frames,
        "collection_ok":error.is_none(),"error":error,"recovery":recovery,
        "capture_meta_path":meta,"game_state_restored":false,"evidence_origin":origin});
    if let Err(e) = crate::confirmation_transport::new_file(&meta, &serde_json::to_vec_pretty(&report).unwrap()) {
        report["report_persistence_error"] = json!(e);
    }
    Ok(report)
}

fn capture_exit(report: &Value) -> i32 {
    if report["recovery"]["status"] != "reported_complete" || report["report_persistence_error"].is_string() { 2 }
    else if report["collection_ok"] == true { 0 } else { 1 }
}

// ── storeop 开关的画面验证 ──

/// screencap 裸帧: 头 12 或 16 字节 (w, h, fmt[, colorspace]) + RGBA
pub(crate) fn parse_raw(d: &[u8]) -> Option<(usize, usize, &[u8])> {
    if d.len() < 12 {
        return None;
    }
    let w = u32::from_le_bytes(d[0..4].try_into().ok()?) as usize;
    let h = u32::from_le_bytes(d[4..8].try_into().ok()?) as usize;
    let n = w * h * 4;
    let off = if d.len() >= 16 + n { 16 } else if d.len() >= 12 + n { 12 } else { return None };
    Some((w, h, &d[off..off + n]))
}

/// 两张同尺寸截图在区域内的差异: (任一通道差 > 8 的像素占比 %, 平均每通道绝对差, 最大通道差)。
/// excl = 排除的矩形 (UI)。
pub(crate) fn pixel_diff(a: &[u8], b: &[u8], w: usize, rect: (usize, usize, usize, usize), excl: &[(usize, usize, usize, usize)]) -> (f64, f64, u8) {
    let (x0, y0, x1, y1) = rect;
    let (mut n, mut nd, mut sum, mut mx) = (0u64, 0u64, 0u64, 0u8);
    for y in y0..y1 {
        for x in x0..x1 {
            if excl.iter().any(|&(ex0, ey0, ex1, ey1)| x >= ex0 && x < ex1 && y >= ey0 && y < ey1) {
                continue;
            }
            let o = (y * w + x) * 4;
            let mut big = false;
            for c in 0..3 {
                let dd = (a[o + c] as i16 - b[o + c] as i16).unsigned_abs() as u8;
                sum += dd as u64;
                mx = mx.max(dd);
                if dd > 8 {
                    big = true;
                }
            }
            if big {
                nd += 1;
            }
            n += 1;
        }
    }
    if n == 0 {
        return (0.0, 0.0, 0);
    }
    (nd as f64 * 100.0 / n as f64, sum as f64 / (n * 3) as f64, mx)
}

/// 按比例给出要排除的 UI 区域 (左上小地图、顶部图标条、右侧队伍、右下技能/UID、左下聊天/状态)
fn ui_rects(w: usize, h: usize) -> Vec<(usize, usize, usize, usize)> {
    let f = |a: f64, b: f64, c: f64, d: f64| ((a * w as f64) as usize, (b * h as f64) as usize, (c * w as f64) as usize, (d * h as f64) as usize);
    vec![
        f(0.0, 0.0, 0.20, 0.32),  // 小地图
        f(0.0, 0.0, 1.0, 0.10),   // 顶部图标条 / 网络延迟数字
        f(0.82, 0.10, 1.0, 0.55), // 右侧队伍头像
        f(0.62, 0.55, 1.0, 1.0),  // 右下技能区 + UID
        f(0.0, 0.70, 0.62, 1.0),  // 左下聊天 / 血条 / 摇杆
    ]
}

struct StoreopFrame {
    width: usize,
    height: usize,
    rgba: Vec<u8>,
}

impl StoreopFrame {
    fn from_png(path: &Path) -> Result<Self, String> {
        let bytes = crate::confirmation_transport::bounded(path, 8 * 1024 * 1024)?;
        let mut reader = image::ImageReader::with_format(std::io::Cursor::new(bytes), image::ImageFormat::Png);
        let mut limits = image::Limits::default();
        limits.max_alloc = Some(64 * 1024 * 1024);
        reader.limits(limits);
        let frame = reader.decode().map_err(|e| format!("PNG decode failed at {}: {e}", path.display()))?;
        let (width, height) = (frame.width() as usize, frame.height() as usize);
        let size = width.checked_mul(height).and_then(|n| n.checked_mul(4)).ok_or("frame size overflow")?;
        if size == 0 || size > 64 * 1024 * 1024 { return Err("decoded frame exceeds RGBA limit".into()); }
        Ok(Self { width, height, rgba: frame.to_rgba8().into_raw() })
    }
}

/// Decode lossless PNG through the same bounded path used by storeop, then
/// expose the existing framecheck 12-byte raw header followed by RGBA8 pixels.
pub(crate) fn png_raw(path: &Path) -> Result<Vec<u8>, String> {
    let frame = StoreopFrame::from_png(path)?;
    let mut raw = Vec::with_capacity(12 + frame.rgba.len());
    raw.extend_from_slice(&(frame.width as u32).to_le_bytes());
    raw.extend_from_slice(&(frame.height as u32).to_le_bytes());
    raw.extend_from_slice(&1u32.to_le_bytes());
    raw.extend_from_slice(&frame.rgba);
    Ok(raw)
}

// Standalone and parent-session collectors share pixel calculation and UI exclusions.
fn storeop_diffs(phases: &[(bool, Vec<StoreopFrame>)]) -> Result<(usize, usize, Vec<Value>), String> {
    let first = phases.first().and_then(|p| p.1.first()).ok_or("no storeop frames")?;
    let (w, h) = (first.width, first.height);
    if phases.iter().any(|(_, frames)| frames.len() != 3 || frames.iter().any(|f| f.width != w || f.height != h)) {
        return Err("storeop frames are incomplete or have different dimensions".into());
    }
    let mut rows = Vec::new();
    let full = (0, 0, w, h);
    let excl = ui_rects(w, h);
    let mut cmp = |label: String, kind: &str, a: (usize, usize), b: (usize, usize)| {
        let (pct, mad, mx) = pixel_diff(&phases[a.0].1[a.1].rgba, &phases[b.0].1[b.1].rgba, w, full, &excl);
        rows.push(json!({"pair":label,"kind":kind,"diff_px_pct":(pct * 1000.0).round() / 1000.0,
            "mean_abs":(mad * 1000.0).round() / 1000.0,"max":mx}));
    };
    for p in 0..phases.len() {
        cmp(format!("段{p}({}) 第1张~第2张", if phases[p].0 { "开" } else { "关" }), "noise_1s", (p, 0), (p, 1));
        if p + 1 < phases.len() { cmp(format!("段{p}~段{}", p + 1), "off_vs_on", (p, 1), (p + 1, 1)); }
        if p + 2 < phases.len() { cmp(format!("段{p}~段{}", p + 2), "same_state", (p, 1), (p + 2, 1)); }
    }
    Ok((w, h, rows))
}

/// Collect using the caller's prepared and installed session. Lease ownership
/// and recovery remain with the caller; appended frame records survive errors.
#[allow(clippy::too_many_arguments)]
pub(crate) fn collect_storeop(session: &crate::gray_session::Session, out: &Path, rule: &str, rounds: u32, pkg: &str, taps: &[(u32, u32)], settle: u64, frames: &mut Vec<Value>) -> Result<Value, String> {
    if rounds == 0 { return Err("storeop rounds must be at least one".into()); }
    let phase_count = rounds.checked_mul(2).and_then(|n| n.checked_add(1)).ok_or("round count overflow")?;
    let first_frame = frames.len();
    capture_scene(session, pkg, taps, settle)?;
    let set_on = |on| -> Result<(), String> {
        session.set_storeop(on)?;
        std::thread::sleep(std::time::Duration::from_secs(3));
        Ok(())
    };
    let mut phases = Vec::new();
    for phase in 0..phase_count {
        let on = phase % 2 == 1;
        set_on(on)?;
        let mut shots = Vec::new();
        for index in 0..3 {
            let path = out.join(format!("phase-{phase}-{index}.png"));
            session.screenshot(&path)?;
            frames.push(json!({"phase":phase,"on":on,"index":index,"image":path,"wall":chrono::Utc::now().to_rfc3339()}));
            shots.push(StoreopFrame::from_png(&path)?);
            std::thread::sleep(std::time::Duration::from_secs(1));
        }
        phases.push((on, shots));
    }
    set_on(true)?;
    session.trigger(1)?;
    let mut audit = None;
    for attempt in 0..45 {
        if let Some(value) = session.audit(1)? { audit = Some(value); break; }
        if attempt < 44 { std::thread::sleep(std::time::Duration::from_secs(2)); }
    }
    let audit = audit.ok_or("enabled storeop trigger produced no matching audit")?;
    let audit_path = out.join("fa_on.json");
    crate::confirmation_transport::new_file(&audit_path, &serde_json::to_vec_pretty(&audit).map_err(|e| e.to_string())?)?;
    set_on(false)?;
    let alive = session.target_alive()?;
    let marker = session.marker()?.ok_or("layer marker disappeared after storeop capture")?;
    let marker_path = out.join("layer_marker.json");
    crate::confirmation_transport::new_file(&marker_path, &serde_json::to_vec_pretty(&marker).map_err(|e| e.to_string())?)?;
    let crash = session.crash_log()?;
    let crash_path = out.join("crash.log");
    crate::confirmation_transport::new_file(&crash_path, crash.as_bytes())?;
    let (w, h, rows) = storeop_diffs(&phases)?;
    let effective = marker["effective"].as_array().cloned().unwrap_or_default();
    let hit = effective.iter().filter(|e| e["begins"].as_u64().unwrap_or(0) > 0).count();
    Ok(json!({"rule":rule,"screen":[w,h],"rounds":rounds,"effective":effective,"effective_hit":hit,
        "game_alive":alive,"crash_log_lines":crash.lines().filter(|l| l.contains(pkg)).count(),
        "frameaudit_on_captured":true,"diffs":rows,"audit":audit_path,"marker":marker_path,"crash_log":crash_path,
        "shot_off":frames[first_frame + 1]["image"],"shot_on":frames[first_frame + 4]["image"],
        "pixel_source":"lossless_png_decoded_rgba8","ui_exclusion":"existing_frameaudit_proportional_regions"}))
}

#[allow(clippy::too_many_arguments)]
fn storeop_check(serial: &str, out: &Path, so: Option<String>, rule: &str, rounds: u32, pkg: &str, taps: &[(u32, u32)], settle: u64, extras: Option<String>, origin: &str) -> Result<Value, String> {
    rounds.checked_mul(2).and_then(|n| n.checked_add(1)).ok_or("round count overflow")?;
    let lease = crate::devlease::Lease::target(Some(serial), "frame-audit-storeop")?;
    let session = crate::gray_session::Session::new(lease, out, pkg, origin)?;
    let mut frames = Vec::new();
    let collected: Result<Value, String> = (|| {
        session.prepare()?;
        session.install_storeop(so.as_deref(), rule, extras.as_deref())?;
        collect_storeop(&session, &session.dir, rule, rounds, pkg, taps, settle, &mut frames)
    })();
    let (result, mut error) = match collected { Ok(v)=>(v,None), Err(e)=>(Value::Null,Some(e)) };
    let collection_path = session.dir.join("collection.json");
    let collection = json!({"schema":"phonefarm/gray-storeop-collection@1","serial":serial,"package":pkg,
        "session":session.dir,"evidence_origin":origin,"frames":frames,"result":result,"error":error,
        "collection_ok":error.is_none(),"request":{"rule":rule,"rounds":rounds,"taps":taps,"settle_s":settle,
            "launch_extras":extras,"layer_source":so}});
    if let Err(e) = crate::confirmation_transport::new_file(&collection_path, &serde_json::to_vec_pretty(&collection).unwrap()) {
        error = Some(match error {Some(prior)=>format!("{prior}; collection evidence: {e}"),None=>e});
    }
    let recovery = match session.finish(error.as_deref()) {
        Ok(v)=>v, Err(e)=>json!({"status":"required","error":e,"session":session.dir}),
    };
    let meta = session.dir.join("storeop_check.json");
    let mut report = json!({"schema":"phonefarm/gray-storeop@1","serial":serial,"package":pkg,
        "out":session.dir,"session":session.dir,"collection":collection_path,"frames":frames,"result":result,
        "collection_ok":error.is_none(),"error":error,"recovery":recovery,"report_path":meta,
        "game_state_restored":false,"evidence_origin":origin});
    // Keep the existing CLI metric fields while exposing the separate collection result.
    if let Some(fields) = result.as_object() {
        for (key, value) in fields { report[key] = value.clone(); }
    }
    if let Err(e) = crate::confirmation_transport::new_file(&meta, &serde_json::to_vec_pretty(&report).unwrap()) {
        report["report_persistence_error"] = json!(e);
    }
    Ok(report)
}

pub fn run_frame_audit(args: &[String]) -> i32 {
    let Some(sub) = args.first() else {
        eprintln!("{USAGE}");
        return 2;
    };
    let rest = &args[1..];
    match sub.as_str() {
        "analyze" => {
            let mut paths = Vec::new();
            let (mut md_out, mut js_out) = (None, None);
            let mut it = rest.iter();
            while let Some(a) = it.next() {
                match a.as_str() {
                    "--md" => md_out = it.next().cloned(),
                    "--json" => js_out = it.next().cloned(),
                    p => paths.push(PathBuf::from(p)),
                }
            }
            if paths.is_empty() {
                eprintln!("{USAGE}");
                return 2;
            }
            match analyze(&paths) {
                Ok((md, js)) => {
                    match md_out {
                        Some(p) => {
                            if let Err(e) = std::fs::write(&p, &md) {
                                eprintln!("写 {p}: {e}");
                                return 1;
                            }
                        }
                        None => print!("{md}"),
                    }
                    if let Some(p) = js_out {
                        if let Err(e) = std::fs::write(&p, serde_json::to_string_pretty(&js).unwrap_or_default()) {
                            eprintln!("写 {p}: {e}");
                            return 1;
                        }
                    }
                    0
                }
                Err(e) => {
                    eprintln!("{e}");
                    1
                }
            }
        }
        "capture" => {
            let mut serial = std::env::var("REFBENCH_SERIAL")
                .unwrap_or_else(|_| std::env::var("SERIAL").unwrap_or_else(|_| "91253241019A".into()));
            let (mut out, mut so, mut ts, mut captures, mut gap) = (None, None, false, 3u32, 20u64);
            let mut pkg = "com.miHoYo.Yuanshen".to_string();
            let (mut taps, mut settle, mut extras) = (Vec::new(), None::<u64>, None::<String>);
            let (mut json_output, mut origin) = (false, "device".to_string());
            let mut it = rest.iter();
            while let Some(a) = it.next() {
                match a.as_str() {
                    "--out" => out = it.next().cloned(),
                    "--so" => so = it.next().cloned(),
                    "--ts" => ts = true,
                    "--captures" => captures = it.next().and_then(|x| x.parse().ok()).unwrap_or(captures),
                    "--gap" => gap = it.next().and_then(|x| x.parse().ok()).unwrap_or(gap),
                    "--pkg" => pkg = it.next().cloned().unwrap_or(pkg),
                    "--serial" => serial = it.next().cloned().unwrap_or(serial),
                    "--tap" => taps = it.next().map(|s| parse_taps(s)).unwrap_or_default(),
                    "--settle" => settle = it.next().and_then(|x| x.parse().ok()),
                    "--extras" => extras = it.next().cloned(),
                    "--json" => json_output = true,
                    "--evidence-origin" => origin = it.next().cloned().unwrap_or_default(),
                    other => {
                        eprintln!("不认识的参数 {other:?}\n{USAGE}");
                        return 2;
                    }
                }
            }
            let settle = settle.unwrap_or(if pkg == "com.miHoYo.Yuanshen" { 15 } else { 60 });
            let Some(out) = out else {
                eprintln!("{USAGE}");
                return 2;
            };
            if !matches!(origin.as_str(), "device" | "synthetic") {
                eprintln!("--evidence-origin must be device or synthetic"); return 2;
            }
            match capture(&serial, Path::new(&out), so, ts, captures.max(1), gap, &pkg, &taps, settle, extras, &origin) {
                Ok(report) => {
                    if json_output { println!("{}", serde_json::to_string_pretty(&report).unwrap()); }
                    else {
                        println!("采集结果: {}, 恢复: {}, 证据 {}", report["collection_ok"], report["recovery"]["status"], report["out"]);
                        if let Some(error) = report["error"].as_str() { eprintln!("{error}"); }
                        if let Some(error) = report["recovery"]["error"].as_str() { eprintln!("{error}"); }
                        if let Some(error) = report["report_persistence_error"].as_str() { eprintln!("{error}"); }
                    }
                    capture_exit(&report)
                }
                Err(e) => {
                    if json_output { println!("{}", json!({"error":e,"collection_ok":false,"recovery":{"status":"unknown"}})); }
                    else { eprintln!("{e}"); }
                    2
                }
            }
        }
        "recover" => {
            let (mut dir, mut serial, mut json_output) = (None, None, false);
            let mut it = rest.iter();
            while let Some(arg) = it.next() {
                match arg.as_str() {
                    "--session" => dir = it.next().cloned(),
                    "--serial" => serial = it.next().cloned(),
                    "--json" => json_output = true,
                    other => { eprintln!("不认识的参数 {other:?}\n{USAGE}"); return 2; }
                }
            }
            let (Some(dir), Some(serial)) = (dir, serial) else { eprintln!("{USAGE}"); return 2; };
            let result = crate::gray_session::Session::resume(Path::new(&dir), &serial).and_then(|s| s.finish(None));
            match result {
                Ok(value) => {
                    if json_output { println!("{}", serde_json::to_string_pretty(&value).unwrap()); }
                    else {
                        println!("恢复状态 {}，范围 {}；原件 {}", value["status"], value["scope"], value["report_path"]);
                        if let Some(error) = value["error"].as_str() { eprintln!("{error}"); }
                    }
                    if value["status"] == "reported_complete" { 0 } else { 2 }
                }
                Err(e) => {
                    if json_output { println!("{}", json!({"status":"required","session":dir,"error":e})); }
                    else { eprintln!("{e}"); }
                    2
                }
            }
        }
        "storeop-check" => {
            let mut serial = std::env::var("REFBENCH_SERIAL")
                .unwrap_or_else(|_| std::env::var("SERIAL").unwrap_or_else(|_| "91253241019A".into()));
            let (mut out, mut so, mut rule, mut rounds) = (None, None, None, 2u32);
            let mut pkg = "com.miHoYo.Yuanshen".to_string();
            let (mut taps, mut settle, mut extras) = (Vec::new(), None::<u64>, None::<String>);
            let mut origin = "device".to_string();
            let mut it = rest.iter();
            while let Some(a) = it.next() {
                match a.as_str() {
                    "--out" => out = it.next().cloned(),
                    "--so" => so = it.next().cloned(),
                    "--rule" => rule = it.next().cloned(),
                    "--rounds" => rounds = it.next().and_then(|x| x.parse().ok()).unwrap_or(rounds),
                    "--pkg" => pkg = it.next().cloned().unwrap_or(pkg),
                    "--serial" => serial = it.next().cloned().unwrap_or(serial),
                    "--tap" => taps = it.next().map(|s| parse_taps(s)).unwrap_or_default(),
                    "--settle" => settle = it.next().and_then(|x| x.parse().ok()),
                    "--extras" => extras = it.next().cloned(),
                    "--json" => {}, // This command already returns JSON by default.
                    "--evidence-origin" => origin = it.next().cloned().unwrap_or_default(),
                    other => {
                        eprintln!("不认识的参数 {other:?}\n{USAGE}");
                        return 2;
                    }
                }
            }
            let settle = settle.unwrap_or(if pkg == "com.miHoYo.Yuanshen" { 20 } else { 60 });
            let (Some(out), Some(rule)) = (out, rule) else {
                eprintln!("{USAGE}");
                return 2;
            };
            if !matches!(origin.as_str(), "device" | "synthetic") {
                eprintln!("--evidence-origin must be device or synthetic"); return 2;
            }
            match storeop_check(&serial, Path::new(&out), so, &rule, rounds.max(1), &pkg, &taps, settle, extras, &origin) {
                Ok(report) => {
                    println!("{}", serde_json::to_string_pretty(&report).unwrap());
                    capture_exit(&report)
                }
                Err(e) => {
                    println!("{}", json!({"error":e,"collection_ok":false,"recovery":{"status":"unknown"}}));
                    2
                }
            }
        }
        _ => {
            eprintln!("{USAGE}");
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 两帧的最小时间线:
    ///   pass A: 画 #1 (RGBA8 100x100, CLEAR/STORE) + 深度 #2 (D24S8, CLEAR/STORE)
    ///   pass B: 采样 #1, 画到屏幕 #9 (DONT_CARE/STORE)
    /// 深度 #2 存了没人读, 下一帧又 CLEAR → 白存; #1 被 B 读 → 有用。
    /// 第二帧 pass C: LOAD #3, 而 #3 前一步被 UNDEFINED 丢弃 → 白读。
    fn fixture() -> Value {
        let pass_a = json!({"k":"pass","rp":1,"w":100,"h":100,"dr":5,"t0":0,"t1":1,
            "att":[{"i":1,"m":0,"l":0,"f":37,"s":1,"lo":1,"st":0,"slo":2,"sst":1,"il":0,"fl":5,"ro":0},
                   {"i":2,"m":0,"l":0,"f":129,"s":1,"lo":1,"st":0,"slo":1,"sst":0,"il":0,"fl":3,"ro":1}]});
        let pass_b = json!({"k":"pass","rp":2,"w":100,"h":100,"dr":1,"rd":[[1,0,1,0,1]],
            "att":[{"i":9,"m":0,"l":0,"f":37,"s":1,"lo":2,"st":0,"slo":2,"sst":1,"il":0,"fl":1000001002,"ro":0}]});
        let disc = json!({"k":"discard","dst":[3,0,0],"dnm":1,"dnl":1});
        let pass_c = json!({"k":"pass","rp":3,"w":50,"h":50,"dr":2,
            "att":[{"i":3,"m":0,"l":0,"f":97,"s":1,"lo":0,"st":0,"slo":2,"sst":1,"il":2,"fl":5,"ro":0}]});
        let pass_d = json!({"k":"pass","rp":4,"w":100,"h":100,"dr":1,"rd":[[3,0,1,0,1]],
            "att":[{"i":9,"m":0,"l":0,"f":37,"s":1,"lo":0,"st":0,"slo":2,"sst":1,"il":2,"fl":1000001002,"ro":0}]});
        let frame = |evs: Vec<Value>| json!({"frame":1,"present":9,"submits":[{"q":0,"cbs":[{"cb":0,"ev":evs}]}]});
        json!({
            "knob":"gray_frameaudit","version":1,"swapchain":{"w":100,"h":100,"fmt":37},
            "ts_enabled":true,"ts_period_ns":1000.0,"ts_available":2,"diag":{},
            "images":[{"id":1,"w":100,"h":100,"d":1,"fmt":37,"samples":1,"mips":1,"layers":1,"swap":false},
                      {"id":2,"w":100,"h":100,"d":1,"fmt":129,"samples":1,"mips":1,"layers":1,"swap":false},
                      {"id":3,"w":50,"h":50,"d":1,"fmt":97,"samples":1,"mips":1,"layers":1,"swap":false},
                      {"id":9,"w":100,"h":100,"d":1,"fmt":37,"samples":1,"mips":1,"layers":1,"swap":true}],
            "ts":[[0,1000],[1,3000]],
            "frames":[frame(vec![pass_a.clone(), pass_b.clone(), disc.clone(), pass_c.clone(), pass_d.clone()]),
                      frame(vec![pass_a, pass_b, disc, pass_c, pass_d])]
        })
    }

    #[test]
    fn fmt_table_covers_genshin_formats() {
        for f in [37u32, 58, 76, 97, 100, 122, 124, 129, 130] {
            assert!(fmt_bytes(f).is_some(), "fmt {f}");
        }
        assert_eq!(fmt_bytes(129), Some((3.0, 1.0)));
    }

    #[test]
    fn finds_dead_depth_store_and_useless_load() {
        let t = Timeline::from_json(&fixture()).unwrap();
        assert_eq!(t.evs.len(), 10);
        assert_eq!(t.evs[0].ms, Some(2.0)); // (3000-1000) 个 tick × 1000ns = 2ms
        let fs = find(&t);
        let dead: Vec<_> = fs.iter().filter(|x| x.rule == "dead_store" && x.frame == 0).collect();
        // 深度 #2 白存: 深度 3B + 模板 1B, aspect 分开记两条, 合计 40000 字节; 颜色 #1 被读, 不算
        assert_eq!(dead.len(), 2, "{fs:#?}");
        assert!(dead.iter().all(|x| x.att == Some(1) && x.certain));
        let tot: f64 = dead.iter().map(|x| x.bytes).sum();
        assert!((tot - 40000.0).abs() < 1e-6);
        let ul: Vec<_> = fs.iter().filter(|x| x.rule == "useless_load").collect();
        assert_eq!(ul.len(), 2, "两帧各一次: {fs:#?}");
        assert!((ul[0].bytes - 50.0 * 50.0 * 8.0).abs() < 1e-6);
        // 屏幕图被 present 读, 永远不算白存
        assert!(!fs.iter().any(|x| x.why.contains("(屏幕)") && x.rule == "dead_store"));
        // 最后一帧不报白存 (后面没有整帧可看)
        assert!(!fs.iter().any(|x| x.rule == "dead_store" && x.frame == 1));
    }

    #[test]
    fn depth_only_used_in_pass_is_transient_candidate() {
        let t = Timeline::from_json(&fixture()).unwrap();
        let fs = find(&t);
        let tr: Vec<_> = fs.iter().filter(|x| x.rule == "transient_ok").collect();
        assert_eq!(tr.len(), 1, "{fs:#?}");
        assert!(tr[0].why.contains("图#2"));
        assert!((tr[0].bytes - 40000.0).abs() < 1e-6);
    }

    /// 原神 rp103 -> rp98 实测形态: 前者 STORE 模板, 后者深度 DONT_CARE 但模板 LOAD。
    /// 模板那份写回是有人读的, 不能报白存 (aspect 不拆开时会误报)。
    #[test]
    fn stencil_load_keeps_stencil_store_alive() {
        let mut j = fixture();
        for f in j["frames"].as_array_mut().unwrap() {
            let evs = f["submits"][0]["cbs"][0]["ev"].as_array_mut().unwrap();
            let reader = json!({"k":"pass","rp":9,"w":100,"h":100,"dr":1,
                "att":[{"i":2,"m":0,"l":0,"f":129,"s":1,"lo":2,"st":1000301000,"slo":0,"sst":1000301000,"il":1000117000,"fl":1000117000,"ro":1}]});
            evs.insert(1, reader);
        }
        let t = Timeline::from_json(&j).unwrap();
        let fs = find(&t);
        let dead: Vec<_> = fs.iter().filter(|x| x.rule == "dead_store" && x.frame == 0).collect();
        assert_eq!(dead.len(), 1, "只剩深度那份白存: {fs:#?}");
        assert!((dead[0].bytes - 30000.0).abs() < 1e-6);
        assert!(!fs.iter().any(|x| x.rule == "transient_ok" && x.why.contains("图#2")), "{fs:#?}");
    }

    /// 只测深度、不写回任何附件、但开了遮挡查询的 pass (原神 rp112 的形态) 不算白画
    #[test]
    fn occlusion_query_pass_is_not_dead() {
        let mut j = fixture();
        for f in j["frames"].as_array_mut().unwrap() {
            let evs = f["submits"][0]["cbs"][0]["ev"].as_array_mut().unwrap();
            let q = json!({"k":"pass","rp":7,"w":100,"h":100,"dr":11,"qo":11,"qx":0,
                "att":[{"i":2,"m":0,"l":0,"f":129,"s":1,"lo":0,"st":1000301000,"slo":0,"sst":1000301000,"il":3,"fl":3,"ro":1}]});
            evs.insert(1, q.clone());
            let mut q2 = q;
            q2["qo"] = json!(0);
            q2["rp"] = json!(8);
            evs.insert(2, q2);
        }
        let t = Timeline::from_json(&j).unwrap();
        let fs = find(&t);
        let dead_eps: Vec<usize> = fs.iter().filter(|x| x.rule == "dead_pass" && x.frame == 0).map(|x| x.ev).collect();
        assert!(!dead_eps.contains(&1), "带遮挡查询的不报: {fs:#?}");
        assert!(dead_eps.contains(&2), "不带查询的照报: {fs:#?}");
        assert_eq!(frame_stats(&t, 0)["query_passes"], json!(1));
    }

    #[test]
    fn needed_load_is_not_flagged() {
        // #3 改成上一帧有人 STORE 过 (去掉 discard) → LOAD 是需要的
        let mut j = fixture();
        for f in j["frames"].as_array_mut().unwrap() {
            let evs = f["submits"][0]["cbs"][0]["ev"].as_array_mut().unwrap();
            evs.retain(|e| e["k"] != "discard");
        }
        let t = Timeline::from_json(&j).unwrap();
        let fs = find(&t);
        assert!(fs.iter().all(|x| !(x.rule == "useless_load" && x.frame == 1)), "{fs:#?}");
    }

    #[test]
    fn clear_then_load_detected() {
        let mut j = fixture();
        let clear = json!({"k":"clear_color","dst":[3,0,0],"dnm":1,"dnl":1,"ex":[50,50,1]});
        for f in j["frames"].as_array_mut().unwrap() {
            let evs = f["submits"][0]["cbs"][0]["ev"].as_array_mut().unwrap();
            for e in evs.iter_mut() {
                if e["k"] == "discard" {
                    *e = clear.clone();
                }
            }
        }
        let t = Timeline::from_json(&j).unwrap();
        let fs = find(&t);
        assert!(fs.iter().any(|x| x.rule == "clear_then_load"), "{fs:#?}");
    }

    #[test]
    fn dead_pass_when_nothing_read() {
        // pass B 不再采样 #1, 屏幕也不 present → pass A 两块产出都白存 → 整 pass 白画
        let mut j = fixture();
        for f in j["frames"].as_array_mut().unwrap() {
            let evs = f["submits"][0]["cbs"][0]["ev"].as_array_mut().unwrap();
            evs[1]["rd"] = json!([]);
        }
        let t = Timeline::from_json(&j).unwrap();
        let fs = find(&t);
        assert!(fs.iter().any(|x| x.rule == "dead_pass" && x.ev == 0), "{fs:#?}");
    }

    #[test]
    fn analyze_renders_markdown() {
        let dir = std::env::temp_dir().join(format!("fa_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("fa.json");
        std::fs::write(&p, fixture().to_string()).unwrap();
        let (md, js) = analyze(&[p]).unwrap();
        assert!(md.contains("dead_store"));
        assert!(md.contains("| 0 | pass rp1 |"));
        assert_eq!(js[0]["frames"].as_array().unwrap().len(), 2);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn raw_header_12_or_16() {
        let mut d = vec![];
        d.extend_from_slice(&2u32.to_le_bytes());
        d.extend_from_slice(&1u32.to_le_bytes());
        d.extend_from_slice(&1u32.to_le_bytes());
        d.extend_from_slice(&[1, 2, 3, 255, 4, 5, 6, 255]);
        assert_eq!(parse_raw(&d).unwrap().2, &[1, 2, 3, 255, 4, 5, 6, 255]);
        let mut d16 = d[..12].to_vec();
        d16.extend_from_slice(&0u32.to_le_bytes());
        d16.extend_from_slice(&[1, 2, 3, 255, 4, 5, 6, 255]);
        assert_eq!(parse_raw(&d16).unwrap().2, &[1, 2, 3, 255, 4, 5, 6, 255]);
    }

    #[test]
    fn pixel_diff_counts_and_excludes() {
        // 4x1: 第 0 像素差 20 (算不同), 第 1 像素差 5 (不算), 第 3 像素差 100 但被排除
        let a = vec![0u8; 16];
        let mut b = vec![0u8; 16];
        b[0] = 20;
        b[4] = 5;
        b[12] = 100;
        let (pct, mad, mx) = pixel_diff(&a, &b, 4, (0, 0, 4, 1), &[(3, 0, 4, 1)]);
        assert!((pct - 100.0 / 3.0).abs() < 1e-9);
        assert!((mad - 25.0 / 9.0).abs() < 1e-9);
        assert_eq!(mx, 20);
    }

    #[test]
    fn storeop_comparison_rejects_missing_or_different_frames() {
        let frame = || StoreopFrame { width: 2, height: 2, rgba: vec![0; 16] };
        let mut phases = vec![(false, vec![frame(), frame(), frame()]),
            (true, vec![frame(), frame(), frame()]), (false, vec![frame(), frame(), frame()])];
        let (w, h, rows) = storeop_diffs(&phases).unwrap();
        assert_eq!((w, h, rows.len()), (2, 2, 6));
        assert!(rows.iter().all(|row| row["diff_px_pct"] == 0.0));
        phases[1].1[0].width = 1;
        assert!(storeop_diffs(&phases).is_err());
        phases[1].1[0].width = 2;
        phases[1].1.pop();
        assert!(storeop_diffs(&phases).is_err());
    }

    #[test]
    fn taps_parse() {
        assert_eq!(parse_taps("1342,651; 10,20"), vec![(1342, 651), (10, 20)]);
        assert!(parse_taps("").is_empty());
        assert_eq!(parse_taps("x,1;3,4"), vec![(3, 4)]);
    }

    /// 有 draw 却零描述符引用: 不报任何「没人读」, 只报 reads_invisible
    #[test]
    fn invisible_reads_suppress_dead_findings() {
        let mut j = fixture();
        for f in j["frames"].as_array_mut().unwrap() {
            for e in f["submits"][0]["cbs"][0]["ev"].as_array_mut().unwrap() {
                e.as_object_mut().unwrap().remove("rd");
            }
        }
        let t = Timeline::from_json(&j).unwrap();
        let fs = find(&t);
        assert_eq!(fs.len(), 1, "{fs:#?}");
        assert_eq!(fs[0].rule, "reads_invisible");
        assert_eq!(frame_stats(&t, 0)["descriptor_refs"], json!(0));
    }

    #[test]
    fn rejects_non_audit_json() {
        assert!(Timeline::from_json(&json!({"knob":"gray_readonly_probe"})).is_err());
    }
}
