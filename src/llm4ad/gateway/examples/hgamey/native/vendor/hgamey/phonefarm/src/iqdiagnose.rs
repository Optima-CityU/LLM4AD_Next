//! 画质降质诊断: 不判「过不过」, 而是说清**哪种**降质、**在哪里**, 给模型 (超分 / 插帧) 回炉用。
//!
//! 整条画质链都不判通过 (2026-10-21 起): `phonefarm iq-judge` / `interp-judge` 只列指标、参考线和对照表,
//! 由人选。这里补「哪种、在哪」:
//! 候选与**同一时刻**的原生 (真值) 逐块比, 每块 (默认 32×32) 算一组量, 按规则贴类型, 再按区域汇总。
//!
//! 每块的量 (都是候选 C 对原生 R):
//! - RGB / 亮度 PSNR、块平均 SSIM (亮度, 与 iq-judge 同一实现);
//! - 细节能量 log2 (亮度拉普拉斯平方和之比, 同 iq-judge 口径, 块内算): 负 = 丢细节, 正 = 多出高频;
//! - 各通道平均偏移 C−R, 以及「偏移解释了多少误差」(Σ偏移² / Σ通道 MSE);
//! - 过冲: 在原生有反差处 (邻域 max−min ≥ 24), C 落在 R 半径 3 邻域亮度 [min, max] 之外 (超 4 灰阶) 的像素占比 = 振铃 / 光晕 / 过锐;
//! - 错位: 误差对原生梯度的最小二乘 e ≈ a·∂x + b·∂y, R² 高 = 候选整体平移了 (−a, −b) 像素 (如亚像素相位错);
//! - 网格结构: 误差图 C−R 的横竖梯度按 x mod p、y mod p 分相位, 最大相位 / 其余相位均值 = 块效应 (默认 p = 8);
//! - 插帧另加 (需前帧 P、后帧 N, R = 真值中间帧): 误差对 (P−R, N−R) 的最小二乘拟合 (重影: 候选里混进了前后帧),
//!   C 落在 P、N 半径 6 邻域包络外比真值多出的占比 (运动边缘光晕 / 撕裂, 与 gpuop、I2 同口径),
//!   静止块 (P、R、N 两两 ≥ 45 dB) 是否变坏 (HUD 重影), 序列上候选相对真值的固定偏亮/偏糊 (隔帧闪烁)。
//!
//! 类型 (每块一个主类型 + 若干标签; 块 RGB PSNR ≥ `ok_db` 记为 ok 不贴类型), 主类型按以下先后:
//! 块效应 → 色偏/亮度偏移 → 重影 → 运动边缘光晕 → 错位 → 模糊 → 振铃/过锐 → 锯齿 (平坦区叫噪点) → 其他结构差异。
//! 区域 (每块一个): UI (手给的框, 或 `--ui-auto`: 逐像素各帧不变的强边缘够多) → 运动 (与最像的对照仍差很多) →
//! 边缘 → 纹理 → 平坦。
//!
//! 输出: `diagnose.json` (逐块数组 + 每帧/每组汇总 + 难例清单, 给训练循环用) 和 `summary.md`
//! (人看), 每帧各指标热图与最差块截图 (原生 | 候选 | 差, 自适应放大) 为 PNG。

use crate::iqjudge::{pair_tiles, psnr_of_mse, r4, Shot};
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};

// ---------------------------------------------------------------- 类型与区域

pub(crate) const KINDS: [&str; 11] = [
    "ok",
    "blur",
    "ringing",
    "aliasing",
    "noise",
    "color_shift",
    "blocking",
    "ghosting",
    "motion_halo",
    "misalign",
    "structure",
];
/// 中文名, 与 [`KINDS`] 一一对应。
const KINDS_ZH: [&str; 11] = [
    "无明显降质",
    "模糊/丢细节",
    "振铃/光晕/过锐",
    "锯齿/多出高频",
    "噪点 (平坦区多出高频)",
    "色偏/亮度偏移",
    "块效应/网格",
    "重影 (混入前后帧)",
    "运动边缘光晕/撕裂",
    "错位 (整体偏移)",
    "其他结构差异",
];

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum Kind {
    Ok = 0,
    Blur,
    Ringing,
    Aliasing,
    Noise,
    ColorShift,
    Blocking,
    Ghosting,
    MotionHalo,
    Misalign,
    Structure,
}

impl Kind {
    const ALL: [Kind; 11] = [
        Kind::Ok,
        Kind::Blur,
        Kind::Ringing,
        Kind::Aliasing,
        Kind::Noise,
        Kind::ColorShift,
        Kind::Blocking,
        Kind::Ghosting,
        Kind::MotionHalo,
        Kind::Misalign,
        Kind::Structure,
    ];
    pub(crate) fn name(self) -> &'static str {
        KINDS[self as usize]
    }
    fn bit(self) -> u32 {
        1 << (self as u32)
    }
}

pub(crate) const REGIONS: [&str; 5] = ["ui", "edge", "texture", "flat", "motion"];
const REGIONS_ZH: [&str; 5] = ["UI", "边缘", "纹理", "平坦", "运动 (天空/水/角色等)"];

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum Region {
    Ui = 0,
    Edge,
    Texture,
    Flat,
    Motion,
}

impl Region {
    const ALL: [Region; 5] = [Region::Ui, Region::Edge, Region::Texture, Region::Flat, Region::Motion];
    pub(crate) fn name(self) -> &'static str {
        REGIONS[self as usize]
    }
}

/// 类型以外的附加标签 (位 16 起)。
const T_UI_DAMAGE: u32 = 1 << 16;
const T_STATIC_DAMAGE: u32 = 1 << 17;
const T_FLICKER: u32 = 1 << 18;

fn tag_names(bits: u32) -> Vec<&'static str> {
    let mut v: Vec<&'static str> = Kind::ALL.iter().filter(|k| **k != Kind::Ok && bits & k.bit() != 0).map(|k| k.name()).collect();
    for (b, n) in [(T_UI_DAMAGE, "ui_damage"), (T_STATIC_DAMAGE, "static_damage"), (T_FLICKER, "flicker")] {
        if bits & b != 0 {
            v.push(n);
        }
    }
    v
}

// ---------------------------------------------------------------- 参数

/// 诊断参数。缺省值依据见 `hgamey/_tmp/quality/Q1/REPORT.md`。
#[derive(Debug, Clone)]
pub(crate) struct DiagConfig {
    pub tile: usize,
    /// 块 RGB PSNR ≥ 它记为 ok (同 iq-judge 的稳定块门槛 45 dB)。
    pub ok_db: f64,
    /// 块细节能量 |log2| 超过它算丢细节 / 多出高频 (块内样本少, 比全帧的 0.15 宽)。
    pub detail_thr: f64,
    /// 细节能量的下限 (每像素拉普拉斯平方), 防止平坦块里一点噪声就算成「细节翻倍」。
    pub detail_eps_px: f64,
    /// 偏移解释的误差占比 ≥ 它, 且最大通道偏移 ≥ `offset_min` 灰阶 → 色偏/亮度偏移。
    pub offset_frac: f64,
    pub offset_min: f64,
    /// 过冲: 原生半径 `ring_radius` 邻域亮度包络外 `ring_tol` 灰阶; 占块像素 ≥ `ring_pct` % → 振铃。
    pub ring_radius: usize,
    pub ring_tol: f32,
    pub ring_pct: f64,
    /// 只在原生邻域有反差 (max − min ≥ 它) 的地方数过冲: 振铃长在边缘旁; 平坦区整体抬高是偏移/噪点, 不是振铃。
    pub ring_contrast: f32,
    /// 块效应检测的周期 (像素); 最大相位 / 其余相位 ≥ `grid_ratio` 记块效应。
    pub grid_periods: Vec<usize>,
    pub grid_ratio: f64,
    /// 边界线上误差梯度低于它 (灰阶) 时不判块效应 (误差太小, 相位比没意义)。
    pub grid_min_grad: f64,
    /// 边缘: 亮度前向差 ≥ `edge_grad` 的像素占块 ≥ `edge_frac` → 边缘区。
    pub edge_grad: f32,
    pub edge_frac: f64,
    /// 纹理: 原生每像素拉普拉斯平方 ≥ 它。
    pub texture_lap: f64,
    /// 运动区: 本帧原生与**最像的**对照块亮度 PSNR 仍 < 它 (= 明显在动; 原神相邻帧 21% 的块如此)。
    pub motion_db: f64,
    /// 插帧: min(PSNR(P,R), PSNR(N,R)) < 它的块算「有运动」, 才查重影/光晕 (同 I2 visible_motion_db)。
    pub interp_motion_db: f64,
    /// 错位: 误差对原生梯度的拟合 R² ≥ `shift_r2` 且位移 ≥ `shift_px` 像素。
    pub shift_r2: f64,
    pub shift_px: f64,
    /// 「不动」: 与所有对照块 PSNR ≥ 它 (UI 自动识别、插帧静止块用)。
    pub stable_db: f64,
    pub ui: Vec<(usize, usize, usize, usize)>,
    pub ui_auto: bool,
    /// UI 自动识别: 前向差 ≥ `ui_edge_grad` 且与所有对照差 ≤ `ui_stable_px` 灰阶的像素 ≥ `ui_edge_frac`
    /// (原神 4 帧对人工框: 精确率 0.91, 召回 0.21 —— 人工框含大片半透明底板, 召回按块算偏低)。
    pub ui_edge_grad: f32,
    pub ui_stable_px: f32,
    pub ui_edge_frac: f64,
    // ---- 插帧 (与 gpuop / I2 interp-judge 同口径)
    pub env_radius: usize,
    pub env_tol: f32,
    /// 块内包络外像素比真值多出 ≥ 它个百分点 → 运动边缘光晕。
    pub halo_pct: f64,
    /// 重影: 误差对 (P−R, N−R) 拟合 R² ≥ 它, 且系数 ≥ `ghost_coef`。
    pub ghost_r2: f64,
    pub ghost_coef: f64,
    pub static_margin_db: f64,
    pub static_cap_db: f64,
    /// 序列闪烁: 插帧相对真值的块平均亮度差 / 细节 log2, 各帧同号且均值绝对值 ≥ 它。
    pub flicker_luma: f64,
    pub flicker_detail: f64,
    /// 原生用到而候选缺失的码值 ≥ 它 (任一通道) → 疑似输出量化过粗 (色带)。
    pub missing_levels: usize,
    /// 每帧列出并截图的最差块数。
    pub top: usize,
}

impl Default for DiagConfig {
    fn default() -> Self {
        DiagConfig {
            tile: 32,
            ok_db: 45.0,
            detail_thr: 0.3,
            detail_eps_px: 4.0,
            offset_frac: 0.5,
            offset_min: 1.0,
            ring_radius: 3,
            ring_tol: 4.0,
            ring_pct: 2.0,
            ring_contrast: 24.0,
            grid_periods: vec![8],
            grid_ratio: 1.6,
            grid_min_grad: 1.0,
            edge_grad: 48.0,
            edge_frac: 0.02,
            texture_lap: 60.0,
            motion_db: 30.0,
            interp_motion_db: 35.0,
            shift_r2: 0.5,
            shift_px: 0.15,
            stable_db: 45.0,
            ui: Vec::new(),
            ui_auto: false,
            ui_edge_grad: 32.0,
            ui_stable_px: 1.0,
            ui_edge_frac: 0.02,
            env_radius: 6,
            env_tol: 6.0,
            halo_pct: 1.0,
            ghost_r2: 0.5,
            ghost_coef: 0.2,
            static_margin_db: 1.0,
            static_cap_db: 50.0,
            flicker_luma: 0.5,
            flicker_detail: 0.10,
            missing_levels: 8,
            top: 12,
        }
    }
}

// ---------------------------------------------------------------- 图像

/// RGB8 图像。
#[derive(Clone)]
pub(crate) struct Img {
    pub w: usize,
    pub h: usize,
    pub rgb: Vec<u8>,
}

impl Img {
    pub(crate) fn new(w: usize, h: usize, rgb: Vec<u8>) -> Option<Img> {
        (rgb.len() == w * h * 3 && w > 0 && h > 0).then_some(Img { w, h, rgb })
    }
    fn luma(&self) -> Vec<f32> {
        self.rgb
            .chunks_exact(3)
            .map(|p| (299.0 * p[0] as f32 + 587.0 * p[1] as f32 + 114.0 * p[2] as f32) / 1000.0)
            .collect()
    }
}

/// 读图: PNG/JPEG 走 image 库, 其余按 screencap 裸帧 (RGBA)。
fn load_img(path: &str) -> Result<Img, String> {
    let d = std::fs::read(path).map_err(|e| format!("{path}: {e}"))?;
    if let Ok(img) = image::load_from_memory(&d) {
        let rgb = img.to_rgb8();
        let (w, h) = (rgb.width() as usize, rgb.height() as usize);
        return Img::new(w, h, rgb.into_raw()).ok_or_else(|| format!("{path}: 空图"));
    }
    let (w, h, px) = crate::frameaudit::parse_raw(&d).ok_or_else(|| format!("{path}: 既不是图片也不是 screencap 裸帧"))?;
    let rgb: Vec<u8> = px.chunks_exact(4).flat_map(|p| [p[0], p[1], p[2]]).collect();
    Img::new(w, h, rgb).ok_or_else(|| format!("{path}: 裸帧尺寸不对"))
}

/// 可分离邻域 min/max (半径 r, 边缘钳位), 多张图取并。
fn envelope(imgs: &[&[f32]], w: usize, h: usize, r: usize) -> (Vec<f32>, Vec<f32>) {
    let n = w * h;
    let (mut tlo, mut thi) = (vec![f32::MAX; n], vec![f32::MIN; n]);
    for y in 0..h {
        for x in 0..w {
            let (x0, x1) = (x.saturating_sub(r), (x + r).min(w - 1));
            let (mut lo, mut hi) = (f32::MAX, f32::MIN);
            for a in imgs {
                for v in &a[y * w + x0..=y * w + x1] {
                    lo = lo.min(*v);
                    hi = hi.max(*v);
                }
            }
            tlo[y * w + x] = lo;
            thi[y * w + x] = hi;
        }
    }
    let (mut lo, mut hi) = (vec![0f32; n], vec![0f32; n]);
    for y in 0..h {
        let (y0, y1) = (y.saturating_sub(r), (y + r).min(h - 1));
        for x in 0..w {
            let (mut l, mut u) = (f32::MAX, f32::MIN);
            for yy in y0..=y1 {
                l = l.min(tlo[yy * w + x]);
                u = u.max(thi[yy * w + x]);
            }
            lo[y * w + x] = l;
            hi[y * w + x] = u;
        }
    }
    (lo, hi)
}

/// 每块亮度 MSE (只看亮度, 给对照/前后帧判「动没动」用, 比 SSIM 便宜)。
fn tile_mse(a: &[f32], b: &[f32], w: usize, tw: usize, th: usize, t: usize) -> Vec<f64> {
    let mut m = vec![0f64; tw * th];
    for r in 0..th * t {
        for c in 0..tw * t {
            let d = (a[r * w + c] - b[r * w + c]) as f64;
            m[(r / t) * tw + c / t] += d * d;
        }
    }
    let n = (t * t) as f64;
    m.iter_mut().for_each(|v| *v /= n);
    m
}

/// 误差图在周期 p 上的网格结构 (横、竖两向平均)。
///
/// 每个方向: 逐条线 (列/行) 算误差图跨该线的平均梯度; 对每个相位 φ, 落在 φ 上的线叫「边界线」。
/// 比值 = 边界线梯度 (块内取**最小**那条, 全帧取中位) / 非边界线平均, 取最大的相位。
/// 用最小值是为了要求**每一条**边界线都高 —— 单条运动边缘或单条物体轮廓只占一条线, 不会被当成网格。
/// 块内的线包括跨到右/下邻块的那一条 (块边长 16、周期 8 时有 2 条边界线)。
/// 返回 (比值, 边界线平均梯度)。
fn grid_ratio(e: &[f32], w: usize, h: usize, x0: usize, y0: usize, x1: usize, y1: usize, p: usize, use_min: bool) -> (f64, f64) {
    // 横向: 列 x 表示 x 与 x+1 之间
    let xe = (x1 + 1).min(w);
    let ye = (y1 + 1).min(h);
    let mut col = Vec::new();
    for x in x0..xe.saturating_sub(1) {
        let mut acc = 0f64;
        for y in y0..y1 {
            let i = y * w + x;
            acc += (e[i + 1] - e[i]).abs() as f64;
        }
        col.push((x, acc / (y1 - y0).max(1) as f64));
    }
    let mut row = Vec::new();
    for y in y0..ye.saturating_sub(1) {
        let mut acc = 0f64;
        for x in x0..x1 {
            let i = y * w + x;
            acc += (e[i + w] - e[i]).abs() as f64;
        }
        row.push((y, acc / (x1 - x0).max(1) as f64));
    }
    let dir = |lines: &[(usize, f64)]| -> (f64, f64) {
        let mut best = (1.0f64, 0.0f64);
        for phi in 0..p {
            let bnd: Vec<f64> = lines.iter().filter(|(i, _)| i % p == phi).map(|(_, v)| *v).collect();
            let oth: Vec<f64> = lines.iter().filter(|(i, _)| i % p != phi).map(|(_, v)| *v).collect();
            if bnd.is_empty() || oth.is_empty() {
                continue;
            }
            let b = if use_min {
                bnd.iter().cloned().fold(f64::INFINITY, f64::min)
            } else {
                crate::iqjudge::median(bnd.clone())
            };
            let o = oth.iter().sum::<f64>() / oth.len() as f64;
            let r = if o > 1e-9 { b / o } else if b > 1e-9 { 99.0 } else { 1.0 };
            if r > best.0 {
                best = (r, bnd.iter().sum::<f64>() / bnd.len() as f64);
            }
        }
        best
    };
    let (rh, gh) = dir(&col);
    let (rv, gv) = dir(&row);
    ((rh + rv) / 2.0, (gh + gv) / 2.0)
}

/// 二元最小二乘 e ≈ a·u + b·v, 输入 [Σu², Σv², Σuv, Σe·u, Σe·v, Σe²], 返回 (a, b, R²)。
fn fit2(s: &[f64; 6]) -> Option<(f64, f64, f64)> {
    if s[5] <= 1e-9 || s[0] + s[1] <= 1e-9 {
        return None;
    }
    let det = s[0] * s[1] - s[2] * s[2];
    let (a, b) = if det.abs() > 1e-6 * (s[0] * s[1]).max(1e-9) {
        ((s[3] * s[1] - s[4] * s[2]) / det, (s[4] * s[0] - s[3] * s[2]) / det)
    } else if s[0] >= s[1] {
        (s[3] / s[0], 0.0)
    } else {
        (0.0, s[4] / s[1])
    };
    let rss = (s[5] - (a * s[3] + b * s[4])).max(0.0);
    Some((a, b, 1.0 - rss / s[5]))
}

// ---------------------------------------------------------------- 每块统计

#[derive(Clone, Debug, Default)]
pub(crate) struct TileStat {
    pub mse_rgb: f64,
    pub mse_luma: f64,
    pub ssim: f64,
    pub dlog: f64,
    pub off: [f64; 3],
    pub off_frac: f64,
    pub over_pct: f64,
    pub grid: f64,
    pub grid_period: usize,
    pub edge_frac: f64,
    pub ref_lap: f64,
    /// 本帧原生与各对照 (其他时刻原生) 的块亮度 PSNR: 最像的一张 / 最不像的一张; 没有对照为 None。
    pub ctrl_best_db: Option<f64>,
    pub ctrl_worst_db: Option<f64>,
    /// 插帧: min(PSNR(P,R), PSNR(N,R)) — 前后帧到真值变了多少。
    pub interp_db: Option<f64>,
    /// 误差 ≈ a·∂x R + b·∂y R 的拟合 (a, b, R²): 候选相对原生整体错位 (−a, −b) 像素。
    pub shift: Option<(f64, f64, f64)>,
    pub luma_diff: f64,
    /// 低频误差: 块内各 8×8 小块亮度误差均值的均方根 (灰阶)。平坦区里它高 = 色带/缓变渐变被台阶化。
    pub lf_err: f64,
    /// 插帧: (α, β, R²) — 误差 ≈ α(P−R) + β(N−R)。
    pub ghost: Option<(f64, f64, f64)>,
    pub halo_excess: f64,
    pub is_static: bool,
    pub region: Option<Region>,
    pub kind: Option<Kind>,
    pub tags: u32,
}

impl TileStat {
    fn psnr_rgb(&self) -> f64 {
        psnr_of_mse(self.mse_rgb)
    }
}

/// 一帧的诊断。
pub(crate) struct FrameDiag {
    pub tw: usize,
    pub th: usize,
    pub tiles: Vec<TileStat>,
    pub global: Value,
}

/// 一对 (原生, 候选) 的输入。插帧时 `prev`/`next` 为前后原生帧, `ref` 为真值中间帧。
pub(crate) struct FrameInput<'a> {
    pub r: &'a Img,
    pub c: &'a Img,
    pub ctrls: Vec<&'a Img>,
    pub prev: Option<&'a Img>,
    pub next: Option<&'a Img>,
}

pub(crate) fn analyze(fi: &FrameInput, cfg: &DiagConfig) -> Result<FrameDiag, String> {
    let (w, h, t) = (fi.r.w, fi.r.h, cfg.tile);
    let same = |i: &Img| i.w == w && i.h == h;
    if !same(fi.c) || !fi.ctrls.iter().all(|i| same(i)) || !fi.prev.map_or(true, same) || !fi.next.map_or(true, same) {
        return Err(format!("尺寸不一致 (原生 {w}×{h}, 候选 {}×{})", fi.c.w, fi.c.h));
    }
    if fi.prev.is_some() != fi.next.is_some() {
        return Err("插帧模式要同时给前帧和后帧".into());
    }
    let sr = Shot::from_rgb(w, h, &fi.r.rgb, t).ok_or("原生图比块还小")?;
    let sc = Shot::from_rgb(w, h, &fi.c.rgb, t).ok_or("候选图比块还小")?;
    let (tw, th) = sr.tiles();
    let pt = pair_tiles(&sc, &sr);
    let (yr, yc) = (&sr.y, &sc.y);
    let n_t = tw * th;
    let npx = (t * t) as f64;
    let mut tiles = vec![TileStat::default(); n_t];

    // 通道 MSE / 偏移, 过冲, 边缘
    let (lo, hi) = envelope(&[yr], w, h, cfg.ring_radius);
    let mut mse_c = vec![[0f64; 3]; n_t];
    let mut sum_c = vec![[0f64; 3]; n_t];
    let mut over = vec![0f64; n_t];
    let mut edge = vec![0f64; n_t];
    let mut ui_edge = vec![0f64; n_t];
    let mut ldiff = vec![0f64; n_t];
    let mut sh = vec![[0f64; 6]; n_t]; // gx², gy², gxgy, e·gx, e·gy, e²
    for y in 0..th * t {
        for x in 0..tw * t {
            let i = y * w + x;
            let k = (y / t) * tw + x / t;
            for ch in 0..3 {
                let d = fi.c.rgb[i * 3 + ch] as f64 - fi.r.rgb[i * 3 + ch] as f64;
                mse_c[k][ch] += d * d;
                sum_c[k][ch] += d;
            }
            ldiff[k] += (yc[i] - yr[i]) as f64;
            {
                let (l, rr) = (if x > 0 { i - 1 } else { i }, if x + 1 < w { i + 1 } else { i });
                let (u, dn) = (if y > 0 { i - w } else { i }, if y + 1 < h { i + w } else { i });
                let gxc = ((yr[rr] - yr[l]) * 0.5) as f64;
                let gyc = ((yr[dn] - yr[u]) * 0.5) as f64;
                let ee = (yc[i] - yr[i]) as f64;
                let a = &mut sh[k];
                a[0] += gxc * gxc;
                a[1] += gyc * gyc;
                a[2] += gxc * gyc;
                a[3] += ee * gxc;
                a[4] += ee * gyc;
                a[5] += ee * ee;
            }
            if hi[i] - lo[i] >= cfg.ring_contrast && (yc[i] > hi[i] + cfg.ring_tol || yc[i] < lo[i] - cfg.ring_tol) {
                over[k] += 1.0;
            }
            let gx = if x + 1 < w { (yr[i + 1] - yr[i]).abs() } else { 0.0 };
            let gy = if y + 1 < h { (yr[i + w] - yr[i]).abs() } else { 0.0 };
            let g = gx.max(gy);
            if g >= cfg.edge_grad {
                edge[k] += 1.0;
            }
        }
    }
    // 误差图 (亮度)
    let e: Vec<f32> = yc.iter().zip(yr.iter()).map(|(c, r)| c - r).collect();

    // 对照: 本帧原生与其他时刻原生的块 PSNR (区分运动区 / 不动区)
    let ylum: Vec<Vec<f32>> = fi.ctrls.iter().map(|c| c.luma()).collect();
    let ctrl_mse: Vec<Vec<f64>> = ylum.iter().map(|yy| tile_mse(yy, yr, w, tw, th, t)).collect();

    // 插帧
    let interp = match (fi.prev, fi.next) {
        (Some(p), Some(n)) => Some((p.luma(), n.luma())),
        _ => None,
    };
    let mut ghost = vec![None; n_t];
    let mut halo = vec![0f64; n_t];
    let mut pn_mse: Vec<(f64, f64, f64)> = vec![(0.0, 0.0, 0.0); n_t];
    if let Some((yp, yn)) = &interp {
        let (elo, ehi) = envelope(&[yp, yn], w, h, cfg.env_radius);
        let mut s = vec![[0f64; 6]; n_t]; // d1², d2², d1d2, e·d1, e·d2, e²
        let (mut oc, mut ot) = (vec![0f64; n_t], vec![0f64; n_t]);
        for y in 0..th * t {
            for x in 0..tw * t {
                let i = y * w + x;
                let k = (y / t) * tw + x / t;
                let (d1, d2, ee) = ((yp[i] - yr[i]) as f64, (yn[i] - yr[i]) as f64, e[i] as f64);
                let a = &mut s[k];
                a[0] += d1 * d1;
                a[1] += d2 * d2;
                a[2] += d1 * d2;
                a[3] += ee * d1;
                a[4] += ee * d2;
                a[5] += ee * ee;
                let outside = |v: f32| v > ehi[i] + cfg.env_tol || v < elo[i] - cfg.env_tol;
                if outside(yc[i]) {
                    oc[k] += 1.0;
                }
                if outside(yr[i]) {
                    ot[k] += 1.0;
                }
            }
        }
        let mpt = tile_mse(yp, yr, w, tw, th, t);
        let mnt = tile_mse(yn, yr, w, tw, th, t);
        let mpn = tile_mse(yp, yn, w, tw, th, t);
        for k in 0..n_t {
            pn_mse[k] = (mpt[k], mnt[k], mpn[k]);
            halo[k] = 100.0 * (oc[k] - ot[k]) / npx;
            if s[k][0] + s[k][1] > npx {
                ghost[k] = fit2(&s[k]);
            }
        }
    }

    // UI 自动识别: 逐像素「各帧完全不变 (≤ ui_stable_px 灰阶) 的强边缘」。3D 画面有 TAA 抖动/光影/摆动,
    // 边缘像素帧间总会变一点; UI 在屏幕空间直接画, 字和图标的边缘逐像素不变。半透明 UI 底板后面的画面在动也不影响。
    if cfg.ui_auto {
        let mut others: Vec<&[f32]> = ylum.iter().map(|v| v.as_slice()).collect();
        if let Some((yp, yn)) = &interp {
            others.push(yp);
            others.push(yn);
        }
        if !others.is_empty() {
            for y in 0..th * t {
                for x in 0..tw * t {
                    let i = y * w + x;
                    let gx = if x + 1 < w { (yr[i + 1] - yr[i]).abs() } else { 0.0 };
                    let gy = if y + 1 < h { (yr[i + w] - yr[i]).abs() } else { 0.0 };
                    if gx.max(gy) >= cfg.ui_edge_grad && others.iter().all(|o| (o[i] - yr[i]).abs() <= cfg.ui_stable_px) {
                        ui_edge[(y / t) * tw + x / t] += 1.0;
                    }
                }
            }
        }
    }
    let mut ui_n = 0usize;
    for k in 0..n_t {
        let (tx, ty) = (k % tw, k / tw);
        let (x0, y0) = (tx * t, ty * t);
        let ts = &mut tiles[k];
        ts.mse_luma = pt.mse[k];
        ts.ssim = pt.ssim[k];
        ts.mse_rgb = mse_c[k].iter().sum::<f64>() / (3.0 * npx);
        for ch in 0..3 {
            ts.off[ch] = sum_c[k][ch] / npx;
        }
        let tot: f64 = mse_c[k].iter().sum::<f64>() / npx;
        ts.off_frac = if tot > 1e-9 { ts.off.iter().map(|o| o * o).sum::<f64>() / tot } else { 0.0 };
        ts.over_pct = 100.0 * over[k] / npx;
        ts.edge_frac = edge[k] / npx;
        ts.ref_lap = sr.hf[k] / npx;
        let eps = cfg.detail_eps_px * npx;
        ts.dlog = ((sc.hf[k] + eps) / (sr.hf[k] + eps)).log2();
        ts.luma_diff = ldiff[k] / npx;
        ts.shift = fit2(&sh[k]);
        // 块效应: 取第一个超门槛的周期 (倍数周期也会超, 报最小的), 否则报最大比值
        let (mut best, mut bp) = (1.0f64, 0usize);
        for &p in &cfg.grid_periods {
            if p < 2 || p > t {
                continue;
            }
            let (r, g) = grid_ratio(&e, w, h, x0, y0, x0 + t, y0 + t, p, true);
            if g < cfg.grid_min_grad {
                continue;
            }
            if r >= cfg.grid_ratio && best < cfg.grid_ratio {
                best = r;
                bp = p;
            } else if best < cfg.grid_ratio && r > best {
                best = r;
                bp = p;
            }
        }
        ts.grid = best;
        ts.grid_period = bp;
        // 对照: 最像的那张 (判「在动」) 与最不像的那张 (判「完全不动」)
        let cps: Vec<f64> = ctrl_mse.iter().map(|m| psnr_of_mse(m[k])).collect();
        if !cps.is_empty() {
            ts.ctrl_best_db = Some(cps.iter().cloned().fold(f64::MIN, f64::max));
            ts.ctrl_worst_db = Some(cps.iter().cloned().fold(f64::MAX, f64::min));
        }
        if interp.is_some() {
            let (a, b, c) = pn_mse[k];
            ts.interp_db = Some(psnr_of_mse(a).min(psnr_of_mse(b)));
            ts.is_static = psnr_of_mse(a) >= cfg.stable_db && psnr_of_mse(b) >= cfg.stable_db && psnr_of_mse(c) >= cfg.stable_db;
            ts.ghost = ghost[k];
            ts.halo_excess = halo[k];
        }

        // 区域
        let in_box = cfg.ui.iter().any(|(bx0, by0, bx1, by1)| {
            let ox = (x0 + t).min(*bx1) as isize - x0.max(*bx0) as isize;
            let oy = (y0 + t).min(*by1) as isize - y0.max(*by0) as isize;
            ox > 0 && oy > 0 && (ox * oy) as f64 >= 0.5 * npx
        });
        let auto_ui = cfg.ui_auto && ui_edge[k] / npx >= cfg.ui_edge_frac;
        ts.region = Some(if in_box || auto_ui {
            ui_n += 1;
            Region::Ui
        } else if ts.ctrl_best_db.map_or(false, |c| c < cfg.motion_db) || ts.interp_db.map_or(false, |c| c < cfg.motion_db) {
            Region::Motion
        } else if ts.edge_frac >= cfg.edge_frac {
            Region::Edge
        } else if ts.ref_lap >= cfg.texture_lap {
            Region::Texture
        } else {
            Region::Flat
        });

        // 类型
        classify(ts, cfg, interp.is_some());
    }
    let _ = ui_n;

    // 全帧量
    let sum_mse: f64 = tiles.iter().map(|s| s.mse_rgb).sum();
    let sum_luma: f64 = tiles.iter().map(|s| s.mse_luma).sum();
    let mean_ssim = tiles.iter().map(|s| s.ssim).sum::<f64>() / n_t as f64;
    let (er, ec): (f64, f64) = (sr.hf.iter().sum(), sc.hf.iter().sum());
    let off: Vec<f64> = (0..3).map(|ch| tiles.iter().map(|s| s.off[ch]).sum::<f64>() / n_t as f64).collect();
    let over_pct = tiles.iter().map(|s| s.over_pct).sum::<f64>() / n_t as f64;
    let mut grid = Map::new();
    for p in [2usize, 4, 8, 16, 32, 64] {
        if p <= tw * t && p <= th * t {
            let (r, g) = grid_ratio(&e, w, h, 0, 0, tw * t, th * t, p, false);
            grid.insert(p.to_string(), json!({"ratio": r4(r), "boundary_grad": r4(g)}));
        }
    }
    let mut gs = [0f64; 6];
    for a in &sh {
        for j in 0..6 {
            gs[j] += a[j];
        }
    }
    // 低频误差 (8×8 小块均值)
    for (k, ts) in tiles.iter_mut().enumerate() {
        let (x0, y0) = tile_rect(k, tw, t);
        let mut acc = 0f64;
        let mut n = 0f64;
        for by in (0..t).step_by(8) {
            for bx in (0..t).step_by(8) {
                let mut m = 0f64;
                let mut c = 0f64;
                for y in y0 + by..(y0 + by + 8).min(y0 + t) {
                    for x in x0 + bx..(x0 + bx + 8).min(x0 + t) {
                        m += e[y * w + x] as f64;
                        c += 1.0;
                    }
                }
                let m = m / c.max(1.0);
                acc += m * m;
                n += 1.0;
            }
        }
        ts.lf_err = (acc / n.max(1.0)).sqrt();
    }
    // 输出色阶: 原生用到 (≥ 十万分之一像素) 而候选一次都没出现的码值 = 输出量化太粗 (如 int8 输出步长 > 1 灰阶)
    let mut hr = vec![[0u64; 256]; 3];
    let mut hc = vec![[0u64; 256]; 3];
    for (i, (a, b)) in fi.r.rgb.iter().zip(&fi.c.rgb).enumerate() {
        hr[i % 3][*a as usize] += 1;
        hc[i % 3][*b as usize] += 1;
    }
    let min_cnt = ((w * h) as f64 * 1e-5).ceil().max(1.0) as u64;
    let used = |hh: &[u64; 256]| hh.iter().filter(|v| **v > 0).count();
    let missing: Vec<usize> = (0..3).map(|ch| (0..256).filter(|v| hr[ch][*v] >= min_cnt && hc[ch][*v] == 0).count()).collect();
    let levels = json!({
        "used_ref": (0..3).map(|ch| used(&hr[ch])).collect::<Vec<_>>(),
        "used_cand": (0..3).map(|ch| used(&hc[ch])).collect::<Vec<_>>(),
        "missing_in_cand": missing,
        "quantization_suspect": missing.iter().any(|m| *m >= cfg.missing_levels),
    });
    let gshift = fit2(&gs).map(|(a, b, r2)| json!({"dx": r4(-a), "dy": r4(-b), "r2": r4(r2)}));
    let mut global = json!({
        "psnr_rgb": r4(psnr_of_mse(sum_mse / n_t as f64)),
        "psnr_luma": r4(psnr_of_mse(sum_luma / n_t as f64)),
        "ssim": r4(mean_ssim),
        "detail_log2": r4(if er > 0.0 { (ec.max(1e-9) / er).log2() } else { 0.0 }),
        "offset_rgb": off.iter().map(|v| r4(*v)).collect::<Vec<_>>(),
        "overshoot_pct": r4(over_pct),
        "grid": Value::Object(grid),
        "shift_px": gshift,
        "levels": levels,
        "lowfreq_err": r4((tiles.iter().map(|s| s.lf_err * s.lf_err).sum::<f64>() / n_t as f64).sqrt()),
    });
    if interp.is_some() {
        let halo_g = tiles.iter().map(|s| s.halo_excess).sum::<f64>() / n_t as f64;
        global["halo_excess_pct"] = json!(r4(halo_g));
        global["luma_diff"] = json!(r4(tiles.iter().map(|s| s.luma_diff).sum::<f64>() / n_t as f64));
        global["static_tiles"] = json!(tiles.iter().filter(|s| s.is_static).count());
    }
    Ok(FrameDiag { tw, th, tiles, global })
}

/// 给一块贴类型。规则见模块注释。
fn classify(ts: &mut TileStat, cfg: &DiagConfig, interp: bool) {
    let mut tags = 0u32;
    let degraded = ts.psnr_rgb() < cfg.ok_db;
    let region = ts.region.unwrap_or(Region::Flat);
    // 插帧静止块 (HUD/不动背景): 与 I2 判据 1 同口径, 不管 ok_db
    if interp && ts.is_static {
        let native = ts.interp_db.unwrap_or(99.0).min(cfg.static_cap_db) - cfg.static_margin_db;
        if psnr_of_mse(ts.mse_luma) < native {
            tags |= T_STATIC_DAMAGE;
        }
    }
    if !degraded {
        ts.kind = Some(Kind::Ok);
        ts.tags = tags;
        return;
    }
    let textured = ts.ref_lap >= 0.25 * cfg.texture_lap || ts.edge_frac >= 0.5 * cfg.edge_frac;
    let off_max = ts.off.iter().fold(0f64, |a, b| a.max(b.abs()));
    let moving = ts.interp_db.map_or(false, |c| c < cfg.interp_motion_db);
    if ts.off_frac >= cfg.offset_frac && off_max >= cfg.offset_min {
        tags |= Kind::ColorShift.bit();
    }
    if ts.grid_period > 0 && ts.grid >= cfg.grid_ratio {
        tags |= Kind::Blocking.bit();
    }
    if interp && moving {
        if let Some((a, b, r2)) = ts.ghost {
            if r2 >= cfg.ghost_r2 && a.max(b) >= cfg.ghost_coef {
                tags |= Kind::Ghosting.bit();
            }
        }
        if ts.halo_excess >= cfg.halo_pct {
            tags |= Kind::MotionHalo.bit();
        }
    }
    if let Some((a, b, r2)) = ts.shift {
        if textured && r2 >= cfg.shift_r2 && (a * a + b * b).sqrt() >= cfg.shift_px {
            tags |= Kind::Misalign.bit();
        }
    }
    if ts.dlog <= -cfg.detail_thr && textured {
        tags |= Kind::Blur.bit();
    }
    if ts.over_pct >= cfg.ring_pct {
        tags |= Kind::Ringing.bit();
    }
    if ts.dlog >= cfg.detail_thr {
        tags |= if region == Region::Flat { Kind::Noise.bit() } else { Kind::Aliasing.bit() };
    }
    if region == Region::Ui {
        tags |= T_UI_DAMAGE;
    }
    let order = [
        Kind::Blocking,
        Kind::ColorShift,
        Kind::Ghosting,
        Kind::MotionHalo,
        Kind::Misalign,
        Kind::Blur,
        Kind::Ringing,
        Kind::Aliasing,
        Kind::Noise,
    ];
    // 都不够门槛时, 细节变化过半门槛 (= iq-judge 全帧口径 0.15) 仍归模糊/锯齿 (轻度), 剩下的才叫其他结构差异
    let mild = if ts.dlog <= -0.5 * cfg.detail_thr && textured {
        Kind::Blur
    } else if ts.dlog >= 0.5 * cfg.detail_thr {
        if region == Region::Flat { Kind::Noise } else { Kind::Aliasing }
    } else {
        Kind::Structure
    };
    let kind = order.iter().copied().find(|k| tags & k.bit() != 0).unwrap_or(mild);
    ts.kind = Some(kind);
    ts.tags = tags | kind.bit();
}

// ---------------------------------------------------------------- 汇总

/// 按主类型汇总: 块数、误差占比 (块 MSE 之和的占比)。
fn type_share(tiles: &[&TileStat]) -> Value {
    let tot: f64 = tiles.iter().map(|s| s.mse_rgb).sum::<f64>().max(1e-12);
    let mut m = Map::new();
    for k in Kind::ALL {
        let sel: Vec<&&TileStat> = tiles.iter().filter(|s| s.kind == Some(k)).collect();
        if sel.is_empty() {
            continue;
        }
        let e: f64 = sel.iter().map(|s| s.mse_rgb).sum();
        m.insert(k.name().into(), json!({"tiles": sel.len(), "err_share": r4(e / tot)}));
    }
    Value::Object(m)
}

fn primary_of(share: &Value) -> String {
    share
        .as_object()
        .and_then(|m| {
            m.iter()
                .filter(|(k, _)| k.as_str() != "ok")
                .max_by(|a, b| {
                    let fa = a.1["err_share"].as_f64().unwrap_or(0.0);
                    let fb = b.1["err_share"].as_f64().unwrap_or(0.0);
                    fa.partial_cmp(&fb).unwrap_or(std::cmp::Ordering::Equal)
                })
                .map(|(k, _)| k.clone())
        })
        .unwrap_or_else(|| "none".into())
}

/// 按区域汇总。`total_mse` 为整组块 MSE 之和 (算误差占比)。
fn region_breakdown(tiles: &[&TileStat], hf: Option<(&[f64], &[f64])>) -> Value {
    let tot: f64 = tiles.iter().map(|s| s.mse_rgb).sum::<f64>().max(1e-12);
    let mut m = Map::new();
    for rg in Region::ALL {
        let idx: Vec<usize> = (0..tiles.len()).filter(|i| tiles[*i].region == Some(rg)).collect();
        if idx.is_empty() {
            continue;
        }
        let n = idx.len() as f64;
        let sel: Vec<&TileStat> = idx.iter().map(|i| tiles[*i]).collect();
        let mse: f64 = sel.iter().map(|s| s.mse_rgb).sum::<f64>();
        let degraded = sel.iter().filter(|s| s.kind != Some(Kind::Ok)).count();
        let share = type_share(&sel);
        let mut v = json!({
            "tiles": idx.len(),
            "psnr_rgb": r4(psnr_of_mse(mse / n)),
            "ssim": r4(sel.iter().map(|s| s.ssim).sum::<f64>() / n),
            "detail_log2_median": r4(crate::iqjudge::median(sel.iter().map(|s| s.dlog).collect())),
            "offset_rgb": (0..3).map(|c| r4(sel.iter().map(|s| s.off[c]).sum::<f64>() / n)).collect::<Vec<_>>(),
            "overshoot_pct": r4(sel.iter().map(|s| s.over_pct).sum::<f64>() / n),
            "lowfreq_err": r4((sel.iter().map(|s| s.lf_err * s.lf_err).sum::<f64>() / n).sqrt()),
            "degraded_frac": r4(degraded as f64 / n),
            "err_share": r4(mse / tot),
            "primary": primary_of(&share),
            "types": share,
        });
        if let Some((hc, hr)) = hf {
            let (c, r): (f64, f64) = (idx.iter().map(|i| hc[*i]).sum(), idx.iter().map(|i| hr[*i]).sum());
            if r > 0.0 {
                v["detail_log2"] = json!(r4((c.max(1e-9) / r).log2()));
            }
        }
        m.insert(rg.name().into(), v);
    }
    Value::Object(m)
}

// ---------------------------------------------------------------- 画图

type Rgb = [u8; 3];

fn lerp(a: Rgb, b: Rgb, t: f64) -> Rgb {
    let f = |x: u8, y: u8| (x as f64 + (y as f64 - x as f64) * t).round().clamp(0.0, 255.0) as u8;
    [f(a[0], b[0]), f(a[1], b[1]), f(a[2], b[2])]
}

/// 顺序色阶 0..1: 浅黄 → 橙 → 深红。小于 0.02 不上色。
fn seq_color(v: f64) -> Option<Rgb> {
    if !(v >= 0.02) {
        return None;
    }
    let stops: [Rgb; 5] = [[255, 255, 178], [254, 204, 92], [253, 141, 60], [240, 59, 32], [189, 0, 38]];
    let x = v.min(1.0) * 4.0;
    let i = (x.floor() as usize).min(3);
    Some(lerp(stops[i], stops[i + 1], x - i as f64))
}

/// 发散色阶 −1..1: 蓝 (负) ← 白 → 红 (正)。|v| < 0.05 不上色。
fn div_color(v: f64) -> Option<Rgb> {
    if !(v.abs() >= 0.05) {
        return None;
    }
    let (w, b, r): (Rgb, Rgb, Rgb) = ([247, 247, 247], [33, 102, 172], [178, 24, 43]);
    Some(if v < 0.0 { lerp(w, b, (-v).min(1.0)) } else { lerp(w, r, v.min(1.0)) })
}

const KIND_COLORS: [Rgb; 11] = [
    [0, 0, 0],
    [66, 133, 244],
    [234, 67, 53],
    [251, 188, 5],
    [255, 112, 67],
    [171, 71, 188],
    [0, 172, 193],
    [124, 179, 66],
    [240, 98, 146],
    [121, 85, 72],
    [158, 158, 158],
];
const REGION_COLORS: [Rgb; 5] = [[171, 71, 188], [234, 67, 53], [251, 188, 5], [66, 133, 244], [124, 179, 66]];

/// 缩略底图: 原生亮度, 每块缩成 8×8 (块边长 < 8 时按原尺寸)。
struct Thumb {
    w: usize,
    h: usize,
    s: usize,
    gray: Vec<u8>,
}

fn thumb(r: &Img, tw: usize, th: usize, t: usize) -> Thumb {
    let s = (t / 8).max(1);
    let cell = t / s;
    let (w, h) = (tw * cell, th * cell);
    let y = r.luma();
    let mut gray = vec![0u8; w * h];
    for ty in 0..h {
        for tx in 0..w {
            let mut acc = 0f32;
            for dy in 0..s {
                for dx in 0..s {
                    acc += y[(ty * s + dy) * r.w + tx * s + dx];
                }
            }
            gray[ty * w + tx] = (acc / (s * s) as f32) as u8;
        }
    }
    Thumb { w, h, s, gray }
}

/// 在暗化的底图上按块上色 (α = 0.7), 存 PNG。
fn heat_png(path: &Path, th_img: &Thumb, tw: usize, t: usize, color: impl Fn(usize) -> Option<Rgb>) -> Result<(), String> {
    let cell = t / th_img.s;
    let mut px = vec![0u8; th_img.w * th_img.h * 3];
    for y in 0..th_img.h {
        for x in 0..th_img.w {
            let g = (th_img.gray[y * th_img.w + x] as f64 * 0.45) as u8;
            let k = (y / cell) * tw + x / cell;
            let c = match color(k) {
                Some(c) => lerp([g, g, g], c, 0.7),
                None => [g, g, g],
            };
            px[(y * th_img.w + x) * 3..(y * th_img.w + x) * 3 + 3].copy_from_slice(&c);
        }
    }
    save_rgb(path, th_img.w, th_img.h, px)
}

fn save_rgb(path: &Path, w: usize, h: usize, px: Vec<u8>) -> Result<(), String> {
    let img = image::RgbImage::from_raw(w as u32, h as u32, px).ok_or("像素数不对")?;
    img.save(path).map_err(|e| format!("{}: {e}", path.display()))
}

/// 最差块截图: 原生 | 候选 | |差|×4, 块外留半块上下文, 最近邻放大到约 256 像素宽。
/// `mids` 为夹在原生与差图之间的候选 (最后一张参与求差), 如 [基线, 候选]。
/// 差图增益自适应: 截图内最大通道差放大到约 160 灰阶, 夹在 [4, 64] 倍; 返回所用倍数。
fn crop_png(path: &Path, r: &Img, mids: &[&Img], x: usize, y: usize, t: usize) -> Result<u32, String> {
    let c = *mids.last().ok_or("没有候选")?;
    let np = mids.len() + 2;
    let pad = t / 2;
    let (x0, y0) = (x.saturating_sub(pad), y.saturating_sub(pad));
    let (x1, y1) = ((x + t + pad).min(r.w), (y + t + pad).min(r.h));
    let (cw, ch) = (x1 - x0, y1 - y0);
    let k = (256 / cw.max(1)).max(1);
    let sep = 4;
    let pw = cw * k;
    let (ow, oh) = (pw * np + sep * (np - 1), ch * k);
    let mut dmax = 0i32;
    for sy in y0..y1 {
        for sx in x0..x1 {
            let i = (sy * r.w + sx) * 3;
            for ch_ in 0..3 {
                dmax = dmax.max((c.rgb[i + ch_] as i32 - r.rgb[i + ch_] as i32).abs());
            }
        }
    }
    let gain = (160 / dmax.max(1)).clamp(4, 64);
    let mut px = vec![40u8; ow * oh * 3];
    for yy in 0..oh {
        for xx in 0..ow {
            let panel = xx / (pw + sep);
            let lx = xx - panel * (pw + sep);
            if lx >= pw {
                continue;
            }
            let (sx, sy) = (x0 + lx / k, y0 + yy / k);
            let i = (sy * r.w + sx) * 3;
            let mut v = [0u8; 3];
            for ch_ in 0..3 {
                v[ch_] = if panel == 0 {
                    r.rgb[i + ch_]
                } else if panel <= mids.len() {
                    mids[panel - 1].rgb[i + ch_]
                } else {
                    ((c.rgb[i + ch_] as i32 - r.rgb[i + ch_] as i32).abs() * gain).min(255) as u8
                };
            }
            // 差图那格画出块的范围 (黄框)
            if panel == np - 1 {
                let on_x = (sx == x || sx + 1 == x + t) && sy >= y && sy < y + t;
                let on_y = (sy == y || sy + 1 == y + t) && sx >= x && sx < x + t;
                if (on_x && (lx % k == 0 || lx % k == k - 1)) || (on_y && (yy % k == 0 || yy % k == k - 1)) {
                    v = [255, 220, 0];
                }
            }
            px[(yy * ow + xx) * 3..(yy * ow + xx) * 3 + 3].copy_from_slice(&v);
        }
    }
    save_rgb(path, ow, oh, px)?;
    Ok(gain as u32)
}

// ---------------------------------------------------------------- 整组运行

/// 一组候选 (同一种方法) 对各自时刻的原生。
pub(crate) struct Group {
    pub label: String,
    pub refs: Vec<String>,
    pub cands: Vec<String>,
}

fn tile_arrays(d: &FrameDiag) -> Value {
    let f = |g: &dyn Fn(&TileStat) -> f64| d.tiles.iter().map(|s| r4(g(s))).collect::<Vec<_>>();
    let mean_mse = (d.tiles.iter().map(|s| s.mse_rgb).sum::<f64>() / d.tiles.len().max(1) as f64).max(1e-12);
    let mut v = json!({
        "psnr_rgb": f(&|s| s.psnr_rgb()),
        "ssim": f(&|s| s.ssim),
        "detail_log2": f(&|s| s.dlog),
        "off_r": f(&|s| s.off[0]),
        "off_g": f(&|s| s.off[1]),
        "off_b": f(&|s| s.off[2]),
        "overshoot_pct": f(&|s| s.over_pct),
        "grid_ratio": f(&|s| s.grid),
        "lowfreq_err": f(&|s| s.lf_err),
        "region": d.tiles.iter().map(|s| s.region.map_or(3, |r| r as u32)).collect::<Vec<_>>(),
        "type": d.tiles.iter().map(|s| s.kind.map_or(0, |k| k as u32)).collect::<Vec<_>>(),
        "tags": d.tiles.iter().map(|s| s.tags).collect::<Vec<_>>(),
        "train_weight": f(&|s| (s.mse_rgb / mean_mse).clamp(0.25, 8.0)),
        "shift_dx": f(&|s| s.shift.map_or(0.0, |g| -g.0)),
        "shift_dy": f(&|s| s.shift.map_or(0.0, |g| -g.1)),
        "shift_r2": f(&|s| s.shift.map_or(0.0, |g| g.2)),
    });
    if d.tiles.iter().any(|s| s.ghost.is_some() || s.halo_excess != 0.0) {
        v["ghost_r2"] = json!(f(&|s| s.ghost.map_or(0.0, |g| g.2)));
        v["ghost_prev"] = json!(f(&|s| s.ghost.map_or(0.0, |g| g.0)));
        v["ghost_next"] = json!(f(&|s| s.ghost.map_or(0.0, |g| g.1)));
        v["halo_excess_pct"] = json!(f(&|s| s.halo_excess));
        v["luma_diff"] = json!(f(&|s| s.luma_diff));
    }
    v
}

fn tile_rect(k: usize, tw: usize, t: usize) -> (usize, usize) {
    ((k % tw) * t, (k / tw) * t)
}

/// 序列闪烁 (插帧): 每块各帧的亮度差 / 细节 log2 同号且均值够大。返回 (块标记, JSON)。
fn flicker(diags: &[FrameDiag], cfg: &DiagConfig) -> (Vec<bool>, Value) {
    let n_t = diags[0].tiles.len();
    let nf = diags.len() as f64;
    let mut mark = vec![false; n_t];
    for k in 0..n_t {
        let ld: Vec<f64> = diags.iter().map(|d| d.tiles[k].luma_diff).collect();
        let dl: Vec<f64> = diags.iter().map(|d| d.tiles[k].dlog).collect();
        let consistent = |v: &[f64], thr: f64| {
            let m = v.iter().sum::<f64>() / nf;
            let same = v.iter().filter(|x| x.signum() == m.signum() && x.abs() > 0.25 * thr).count() as f64;
            m.abs() >= thr && same >= 0.75 * nf
        };
        let flat = diags[0].tiles[k].region == Some(Region::Flat);
        mark[k] = consistent(&ld, cfg.flicker_luma) || (!flat && consistent(&dl, cfg.flicker_detail));
    }
    let fl: Vec<f64> = diags.iter().map(|d| d.global["luma_diff"].as_f64().unwrap_or(0.0)).collect();
    let fd: Vec<f64> = diags.iter().map(|d| d.global["detail_log2"].as_f64().unwrap_or(0.0)).collect();
    let ml = crate::iqjudge::median(fl.clone());
    let md = crate::iqjudge::median(fd.clone());
    let seq = ml.abs() >= cfg.flicker_luma || md.abs() >= cfg.flicker_detail;
    let n = mark.iter().filter(|m| **m).count();
    (
        mark,
        json!({
            "frames": diags.len(),
            "frame_luma_diff": fl.iter().map(|v| r4(*v)).collect::<Vec<_>>(),
            "frame_detail_log2": fd.iter().map(|v| r4(*v)).collect::<Vec<_>>(),
            "median_luma_diff": r4(ml), "median_detail_log2": r4(md),
            "sequence_flicker": seq,
            "flicker_tiles": n,
            "flicker_tile_frac": r4(n as f64 / n_t as f64),
            "note": "插帧与原生帧交替显示: 插帧整体偏亮/偏糊 = 以半帧率来回变 (闪烁)。门槛同 I2 interp-judge 的 flicker_luma / flicker_detail_log2",
        }),
    )
}

/// 跑一组, 写图, 返回 JSON。`out` 为本组目录 (已建好)。
fn run_group(
    g: &Group,
    imgs: &std::collections::HashMap<String, Img>,
    ctrl: &[String],
    prev: &[String],
    next: &[String],
    cfg: &DiagConfig,
    out: &Path,
) -> Result<Value, String> {
    let t = cfg.tile;
    let mut diags: Vec<FrameDiag> = Vec::new();
    let mut hfs: Vec<(Vec<f64>, Vec<f64>)> = Vec::new();
    for (i, (rp, cp)) in g.refs.iter().zip(&g.cands).enumerate() {
        let r = &imgs[rp];
        let c = &imgs[cp];
        // 对照: 显式给了就用; 否则其他时刻的原生 (多张 ref 时)
        let ctrls: Vec<&Img> = if !ctrl.is_empty() {
            ctrl.iter().map(|p| &imgs[p]).collect()
        } else {
            g.refs.iter().enumerate().filter(|(j, p)| *j != i && *p != rp).map(|(_, p)| &imgs[p]).collect()
        };
        let fi = FrameInput {
            r,
            c,
            ctrls,
            prev: prev.get(i).map(|p| &imgs[p]),
            next: next.get(i).map(|p| &imgs[p]),
        };
        let d = analyze(&fi, cfg).map_err(|e| format!("{rp} vs {cp}: {e}"))?;
        let sr = Shot::from_rgb(r.w, r.h, &r.rgb, t).ok_or("图太小")?;
        let sc = Shot::from_rgb(c.w, c.h, &c.rgb, t).ok_or("图太小")?;
        hfs.push((sc.hf.clone(), sr.hf.clone()));
        diags.push(d);
    }
    let interp = !prev.is_empty();
    let mut seq = Value::Null;
    if interp && diags.len() >= 2 {
        let (mark, v) = flicker(&diags, cfg);
        for d in diags.iter_mut() {
            for (k, m) in mark.iter().enumerate() {
                if *m {
                    d.tiles[k].tags |= T_FLICKER;
                }
            }
        }
        seq = v;
    }

    let mut frames = Vec::new();
    let mut hard = Vec::new();
    for (i, d) in diags.iter().enumerate() {
        let (r, c) = (&imgs[&g.refs[i]], &imgs[&g.cands[i]]);
        let fdir = out.join(format!("f{i}"));
        std::fs::create_dir_all(&fdir).map_err(|e| format!("{}: {e}", fdir.display()))?;
        let tref: Vec<&TileStat> = d.tiles.iter().collect();
        let share = type_share(&tref);
        let regions = region_breakdown(&tref, Some((&hfs[i].0, &hfs[i].1)));
        // 最差块
        let mut order: Vec<usize> = (0..d.tiles.len()).filter(|k| d.tiles[*k].kind != Some(Kind::Ok)).collect();
        order.sort_by(|a, b| d.tiles[*b].mse_rgb.partial_cmp(&d.tiles[*a].mse_rgb).unwrap_or(std::cmp::Ordering::Equal));
        let mut worst = Vec::new();
        for (rank, k) in order.iter().take(cfg.top).enumerate() {
            let s = &d.tiles[*k];
            let (x, y) = tile_rect(*k, d.tw, t);
            let kind = s.kind.unwrap_or(Kind::Structure);
            let name = format!("worst_{:02}_{}_x{x}_y{y}.png", rank + 1, kind.name());
            let gain = crop_png(&fdir.join(&name), r, &[c], x, y, t)?;
            let mut wv = json!({
                "rank": rank + 1, "tile": k, "x": x, "y": y, "w": t, "h": t,
                "type": kind.name(), "tags": tag_names(s.tags), "region": s.region.map(|r| r.name()),
                "psnr_rgb": r4(s.psnr_rgb()), "ssim": r4(s.ssim), "detail_log2": r4(s.dlog),
                "offset_rgb": s.off.iter().map(|v| r4(*v)).collect::<Vec<_>>(),
                "overshoot_pct": r4(s.over_pct), "grid_ratio": r4(s.grid), "grid_period": s.grid_period,
                "crop": format!("{}/f{i}/{name}", g.label), "diff_gain": gain,
            });
            if let Some((a, b, r2)) = s.shift {
                wv["shift"] = json!({"dx": r4(-a), "dy": r4(-b), "r2": r4(r2)});
            }
            if let Some((a, b, r2)) = s.ghost {
                wv["ghost"] = json!({"prev": r4(a), "next": r4(b), "r2": r4(r2)});
                wv["halo_excess_pct"] = json!(r4(s.halo_excess));
            }
            worst.push(wv);
        }
        // 难例: 每种类型取误差最大的若干块 (给训练循环挑补丁)
        for kd in Kind::ALL.iter().filter(|k| **k != Kind::Ok) {
            let mut ks: Vec<usize> = (0..d.tiles.len()).filter(|k| d.tiles[*k].kind == Some(*kd)).collect();
            ks.sort_by(|a, b| d.tiles[*b].mse_rgb.partial_cmp(&d.tiles[*a].mse_rgb).unwrap_or(std::cmp::Ordering::Equal));
            for k in ks.iter().take(8) {
                let (x, y) = tile_rect(*k, d.tw, t);
                hard.push(json!({"frame": i, "ref": g.refs[i], "cand": g.cands[i], "type": kd.name(),
                    "region": d.tiles[*k].region.map(|r| r.name()), "x": x, "y": y, "w": t, "h": t,
                    "psnr_rgb": r4(d.tiles[*k].psnr_rgb())}));
            }
        }
        // 热图
        let tb = thumb(r, d.tw, d.th, t);
        let ts = &d.tiles;
        heat_png(&fdir.join("heat_psnr.png"), &tb, d.tw, t, |k| seq_color((50.0 - ts[k].psnr_rgb()) / 25.0))?;
        heat_png(&fdir.join("heat_dssim.png"), &tb, d.tw, t, |k| seq_color((1.0 - ts[k].ssim) / 0.1))?;
        heat_png(&fdir.join("heat_detail.png"), &tb, d.tw, t, |k| div_color(ts[k].dlog / 1.5))?;
        heat_png(&fdir.join("heat_offset.png"), &tb, d.tw, t, |k| {
            let o = ts[k].off;
            (o.iter().any(|v| v.abs() >= 0.5)).then(|| {
                let f = |v: f64| (128.0 + v * 20.0).clamp(0.0, 255.0) as u8;
                [f(o[0]), f(o[1]), f(o[2])]
            })
        })?;
        heat_png(&fdir.join("heat_overshoot.png"), &tb, d.tw, t, |k| seq_color(ts[k].over_pct / 10.0))?;
        heat_png(&fdir.join("heat_grid.png"), &tb, d.tw, t, |k| {
            if ts[k].grid_period > 0 { seq_color((ts[k].grid - 1.0) / 2.0) } else { None }
        })?;
        heat_png(&fdir.join("heat_lowfreq.png"), &tb, d.tw, t, |k| seq_color(ts[k].lf_err / 1.0))?;
        heat_png(&fdir.join("types.png"), &tb, d.tw, t, |k| ts[k].kind.filter(|k| *k != Kind::Ok).map(|k| KIND_COLORS[k as usize]))?;
        heat_png(&fdir.join("regions.png"), &tb, d.tw, t, |k| ts[k].region.map(|r| REGION_COLORS[r as usize]))?;
        if interp {
            heat_png(&fdir.join("heat_ghost.png"), &tb, d.tw, t, |k| {
                ts[k].ghost.filter(|g| g.0.max(g.1) >= cfg.ghost_coef).and_then(|g| seq_color(g.2))
            })?;
            heat_png(&fdir.join("heat_halo.png"), &tb, d.tw, t, |k| seq_color(ts[k].halo_excess / 10.0))?;
            heat_png(&fdir.join("heat_static_damage.png"), &tb, d.tw, t, |k| {
                (ts[k].tags & T_STATIC_DAMAGE != 0).then_some([234, 67, 53])
            })?;
        }
        let mut fv = json!({
            "index": i, "ref": g.refs[i], "cand": g.cands[i],
            "global": d.global, "primary": primary_of(&share), "type_share": share,
            "regions": regions, "worst": worst, "tiles": tile_arrays(d), "dir": format!("{}/f{i}", g.label),
        });
        if interp {
            fv["prev"] = json!(prev[i]);
            fv["next"] = json!(next[i]);
        }
        frames.push(fv);
    }

    // 整组汇总: 所有帧所有块合并
    let all: Vec<&TileStat> = diags.iter().flat_map(|d| d.tiles.iter()).collect();
    let share = type_share(&all);
    let regions = region_breakdown(&all, None);
    let nf = diags.len() as f64;
    let gmean = |key: &str| r4(diags.iter().map(|d| d.global[key].as_f64().unwrap_or(0.0)).sum::<f64>() / nf);
    let off_mean: Vec<f64> =
        (0..3).map(|c| r4(diags.iter().map(|d| d.global["offset_rgb"][c].as_f64().unwrap_or(0.0)).sum::<f64>() / nf)).collect();
    let grid_mean: Map<String, Value> = ["2", "4", "8", "16", "32", "64"]
        .iter()
        .filter_map(|p| {
            let v: Vec<f64> = diags.iter().filter_map(|d| d.global["grid"][*p]["ratio"].as_f64()).collect();
            (!v.is_empty()).then(|| (p.to_string(), json!(r4(v.iter().sum::<f64>() / v.len() as f64))))
        })
        .collect();
    let mut gsum = json!({
        "frames": diags.len(),
        "psnr_rgb_mean": gmean("psnr_rgb"), "ssim_mean": gmean("ssim"), "detail_log2_mean": gmean("detail_log2"),
        "overshoot_pct_mean": gmean("overshoot_pct"),
        "offset_rgb_mean": off_mean,
        "grid_ratio_mean": grid_mean,
        "primary": primary_of(&share), "type_share": share, "regions": regions,
    });
    if interp {
        gsum["halo_excess_pct_mean"] = json!(gmean("halo_excess_pct"));
        gsum["static_damage_tiles"] = json!(all.iter().filter(|s| s.tags & T_STATIC_DAMAGE != 0).count());
        gsum["flicker"] = seq;
    }
    // 各帧都坏的位置: 块 MSE 在本帧前 10% 的次数 = 帧数
    let n_t = diags[0].tiles.len();
    let mut hits = vec![0usize; n_t];
    for d in &diags {
        let mut v: Vec<f64> = d.tiles.iter().map(|s| s.mse_rgb).collect();
        v.sort_by(|a, b| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));
        let cut = v[(n_t / 10).min(n_t - 1)];
        for (k, s) in d.tiles.iter().enumerate() {
            if s.mse_rgb >= cut && s.kind != Some(Kind::Ok) {
                hits[k] += 1;
            }
        }
    }
    let persistent: Vec<usize> = (0..n_t).filter(|k| hits[*k] == diags.len()).collect();
    gsum["persistent_worst_tiles"] = json!(persistent.len());
    // 组级热图: 各帧块 MSE 平均
    let d0 = &diags[0];
    let tb = thumb(&imgs[&g.refs[0]], d0.tw, d0.th, t);
    let mean_psnr: Vec<f64> = (0..n_t).map(|k| psnr_of_mse(diags.iter().map(|d| d.tiles[k].mse_rgb).sum::<f64>() / nf)).collect();
    heat_png(&out.join("mean_psnr.png"), &tb, d0.tw, t, |k| seq_color((50.0 - mean_psnr[k]) / 25.0))?;
    heat_png(&out.join("persistent_worst.png"), &tb, d0.tw, t, |k| (hits[k] == diags.len()).then_some([240, 59, 32]))?;
    Ok(json!({
        "label": g.label, "summary": gsum, "frames": frames, "hard_examples": hard,
        "persistent_worst": persistent.iter().map(|k| { let (x, y) = tile_rect(*k, d0.tw, t); json!([x, y, t, t]) }).collect::<Vec<_>>(),
    }))
}

fn fmt_share(share: &Value, n: usize) -> String {
    let mut v: Vec<(String, f64, u64)> = share
        .as_object()
        .map(|m| {
            m.iter()
                .filter(|(k, _)| k.as_str() != "ok")
                .map(|(k, x)| (k.clone(), x["err_share"].as_f64().unwrap_or(0.0), x["tiles"].as_u64().unwrap_or(0)))
                .collect()
        })
        .unwrap_or_default();
    v.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    v.iter().take(n).map(|(k, s, t)| format!("{} {:.0}% ({t} 块)", zh_kind(k), s * 100.0)).collect::<Vec<_>>().join("、")
}

fn zh_kind(k: &str) -> &'static str {
    KINDS.iter().position(|x| *x == k).map(|i| KINDS_ZH[i]).unwrap_or("无")
}

fn zh_region(k: &str) -> &'static str {
    REGIONS.iter().position(|x| *x == k).map(|i| REGIONS_ZH[i]).unwrap_or("?")
}

/// 人看的 Markdown 摘要。
fn summary_md(rep: &Value) -> String {
    let mut s = String::new();
    let f = |v: &Value| v.as_f64().unwrap_or(f64::NAN);
    s.push_str("# 画质降质诊断 (phonefarm iq-diagnose)\n\n");
    s.push_str(&format!(
        "模式: {}; 块 {} 像素; 块 RGB PSNR ≥ {} dB 记为无明显降质。诊断不判通过与否 (iq-judge / interp-judge 也只列指标与参考线), 只说哪种降质、在哪。\n\n",
        rep["mode"].as_str().unwrap_or(""),
        rep["tile"],
        rep["config"]["ok_db"]
    ));
    s.push_str("## 各组对比\n\n| 组 | PSNR (RGB) | SSIM | 细节 log2 | 平均偏移 R/G/B | 过冲 % | 主要降质 | 降质构成 (按误差占比) |\n|---|---|---|---|---|---|---|---|\n");
    let groups = rep["groups"].as_array().cloned().unwrap_or_default();
    for g in &groups {
        let m = &g["summary"];
        let o = &m["offset_rgb_mean"];
        s.push_str(&format!(
            "| {} | {:.2} | {:.4} | {:+.3} | {:+.2}/{:+.2}/{:+.2} | {:.2} | {} | {} |\n",
            g["label"].as_str().unwrap_or(""),
            f(&m["psnr_rgb_mean"]),
            f(&m["ssim_mean"]),
            f(&m["detail_log2_mean"]),
            f(&o[0]),
            f(&o[1]),
            f(&o[2]),
            f(&m["overshoot_pct_mean"]),
            zh_kind(m["primary"].as_str().unwrap_or("")),
            fmt_share(&m["type_share"], 4)
        ));
    }
    s.push_str("\n## 按区域 (各组所有帧合并)\n\n| 组 | 区域 | 块数 | PSNR | SSIM | 细节 log2 中位 | 过冲 % | 低频误差 | 降质块占比 | 占本组误差 | 区域内主要降质 |\n|---|---|---|---|---|---|---|---|---|---|---|\n");
    for g in &groups {
        if let Some(rm) = g["summary"]["regions"].as_object() {
            for rg in REGIONS {
                if let Some(v) = rm.get(rg) {
                    s.push_str(&format!(
                        "| {} | {} | {} | {:.2} | {:.4} | {:+.3} | {:.2} | {:.3} | {:.0}% | {:.0}% | {} |\n",
                        g["label"].as_str().unwrap_or(""),
                        zh_region(rg),
                        v["tiles"],
                        f(&v["psnr_rgb"]),
                        f(&v["ssim"]),
                        f(&v["detail_log2_median"]),
                        f(&v["overshoot_pct"]),
                        f(&v["lowfreq_err"]),
                        f(&v["degraded_frac"]) * 100.0,
                        f(&v["err_share"]) * 100.0,
                        fmt_share(&v["types"], 2)
                    ));
                }
            }
        }
    }
    for g in groups.iter().filter(|g| !g["vs_baseline"].is_null()) {
        let vb = &g["vs_baseline"];
        let label = g["label"].as_str().unwrap_or("");
        let base = vb["baseline"].as_str().unwrap_or("");
        s.push_str(&format!(
            "\n## `{label}` 对基线 `{base}`\n\n比基线差 > {} dB 且本身已降质 (< {} dB) 的块 {:.1}% ({} / {}); 不论高低只要比基线差的块 {} 块; 比基线好 > {} dB 的块 {:.1}%。\n\n| 区域 | 块数 | 平均差 dB (正 = 比基线好) | 比基线差且已降质 | 比基线差 (任何水平) | 占已降质变差块 |\n|---|---|---|---|---|---|\n",
            vb["worse_db"], rep["config"]["ok_db"], f(&vb["worse_frac"]) * 100.0, vb["worse_tiles"], vb["tiles"], vb["worse_tiles_any_level"], vb["worse_db"], f(&vb["better_frac"]) * 100.0
        ));
        if let Some(rm) = vb["worse_by_region"].as_object() {
            for rg in REGIONS {
                if let Some(v) = rm.get(rg) {
                    s.push_str(&format!(
                        "| {} | {} | {:+.2} | {:.1}% | {:.1}% | {:.0}% |\n",
                        zh_region(rg), v["tiles"], f(&v["mean_delta_db"]), f(&v["worse_frac"]) * 100.0, f(&v["worse_any_level_frac"]) * 100.0, f(&v["share_of_worse"]) * 100.0
                    ));
                }
            }
        }
        if let Some(tm) = vb["worse_by_type"].as_object() {
            let ts: Vec<String> = tm.iter().map(|(k, v)| format!("{} {}", zh_kind(k), v)).collect();
            s.push_str(&format!("\n变差块的类型 (候选自己的诊断): {}\n", ts.join("、")));
        }
        for fr in vb["frames"].as_array().cloned().unwrap_or_default() {
            s.push_str(&format!("\n帧 {}: 平均 {:+.2} dB, 变差 {} 块, [差值热图]({})", fr["index"], f(&fr["mean_delta_db"]), fr["worse_tiles"], fr["heatmap"].as_str().unwrap_or("")));
            let ws: Vec<String> = fr["worst"].as_array().cloned().unwrap_or_default().iter().take(5).map(|w| format!(
                "[({},{}) {} {} {:.1} dB, {:+.1} dB]({})", w["x"], w["y"], zh_region(w["region"].as_str().unwrap_or("")),
                zh_kind(w["type"].as_str().unwrap_or("")), f(&w["psnr"]), f(&w["delta_db"]), w["crop"].as_str().unwrap_or(""))).collect();
            if !ws.is_empty() {
                s.push_str(&format!("; 最差: {}", ws.join(" · ")));
            }
            s.push('\n');
        }
        s.push_str("\n截图四格: 原生 | 基线 | 候选 | 候选与原生 |差| (放大倍数见 JSON diff_gain)。\n");
    }
    for g in &groups {
        let label = g["label"].as_str().unwrap_or("");
        let m = &g["summary"];
        s.push_str(&format!("\n## 组 `{label}`\n\n"));
        let grid = &m["grid_ratio_mean"];
        if let Some(go) = grid.as_object() {
            let gs: Vec<String> = go.iter().map(|(k, v)| format!("{k} 像素 {:.2}", v.as_f64().unwrap_or(0.0))).collect();
            s.push_str(&format!("- 误差的网格相位比 (1 = 无网格结构): {}\n", gs.join(", ")));
        }
        s.push_str(&format!(
            "- 各帧都落在最差 10% 的块: {} 块 (位置图 `{label}/persistent_worst.png`, 平均 PSNR 热图 `{label}/mean_psnr.png`)\n",
            m["persistent_worst_tiles"]
        ));
        if !m["flicker"].is_null() {
            let fl = &m["flicker"];
            s.push_str(&format!(
                "- 序列闪烁: 帧亮度差中位 {:+.3}, 细节 log2 中位 {:+.3}, 判为闪烁: {}; 持续偏移的块 {} 块\n",
                f(&fl["median_luma_diff"]),
                f(&fl["median_detail_log2"]),
                if fl["sequence_flicker"].as_bool() == Some(true) { "是" } else { "否" },
                fl["flicker_tiles"]
            ));
            s.push_str(&format!("- 静止块 (HUD/不动背景) 变坏: {} 块次\n", m["static_damage_tiles"]));
        }
        s.push_str(&format!("![平均 PSNR]({label}/mean_psnr.png)\n"));
        for fr in g["frames"].as_array().cloned().unwrap_or_default() {
            let dir = fr["dir"].as_str().unwrap_or("");
            let gl = &fr["global"];
            s.push_str(&format!(
                "\n### 帧 {} — `{}` 对 `{}`\n\nPSNR {:.2} dB, SSIM {:.4}, 细节 log2 {:+.3}, 偏移 R/G/B {}, 过冲 {:.2}%, 低频误差 {:.3}, 错位 {}, 候选缺失色阶 R/G/B {}{}。主要降质: **{}**; 构成: {}。\n\n",
                fr["index"],
                fr["cand"].as_str().unwrap_or(""),
                fr["ref"].as_str().unwrap_or(""),
                f(&gl["psnr_rgb"]),
                f(&gl["ssim"]),
                f(&gl["detail_log2"]),
                gl["offset_rgb"],
                f(&gl["overshoot_pct"]),
                f(&gl["lowfreq_err"]),
                gl["shift_px"],
                gl["levels"]["missing_in_cand"],
                if gl["levels"]["quantization_suspect"].as_bool() == Some(true) { " (**疑似输出量化过粗**)" } else { "" },
                zh_kind(fr["primary"].as_str().unwrap_or("")),
                fmt_share(&fr["type_share"], 5)
            ));
            s.push_str(&format!(
                "热图: [PSNR]({dir}/heat_psnr.png) · [1−SSIM]({dir}/heat_dssim.png) · [细节 (蓝丢红多)]({dir}/heat_detail.png) · \
[偏移 (颜色=偏向)]({dir}/heat_offset.png) · [过冲]({dir}/heat_overshoot.png) · [网格]({dir}/heat_grid.png) · \
[低频误差/色带]({dir}/heat_lowfreq.png) · [类型]({dir}/types.png) · [区域]({dir}/regions.png)\n\n"
            ));
            s.push_str(&format!("![类型]({dir}/types.png)\n\n| # | 位置 (x,y) | 区域 | 类型 | 标签 | PSNR | SSIM | 细节 | 偏移 R/G/B | 过冲 % | 网格 | 截图 |\n|---|---|---|---|---|---|---|---|---|---|---|---|\n"));
            for wv in fr["worst"].as_array().cloned().unwrap_or_default() {
                let o = &wv["offset_rgb"];
                s.push_str(&format!(
                    "| {} | {},{} | {} | {} | {} | {:.1} | {:.3} | {:+.2} | {:+.1}/{:+.1}/{:+.1} | {:.1} | {:.2}@{} | [图]({}) |\n",
                    wv["rank"],
                    wv["x"],
                    wv["y"],
                    zh_region(wv["region"].as_str().unwrap_or("")),
                    zh_kind(wv["type"].as_str().unwrap_or("")),
                    wv["tags"].as_array().map(|a| a.iter().filter_map(|x| x.as_str()).collect::<Vec<_>>().join(" ")).unwrap_or_default(),
                    f(&wv["psnr_rgb"]),
                    f(&wv["ssim"]),
                    f(&wv["detail_log2"]),
                    f(&o[0]),
                    f(&o[1]),
                    f(&o[2]),
                    f(&wv["overshoot_pct"]),
                    f(&wv["grid_ratio"]),
                    wv["grid_period"],
                    wv["crop"].as_str().unwrap_or("")
                ));
            }
        }
    }
    s.push_str("\n## 图例\n\n- 类型颜色: ");
    let names: Vec<String> = (1..KINDS.len()).map(|i| format!("{} rgb{:?}", KINDS_ZH[i], KIND_COLORS[i])).collect();
    s.push_str(&names.join("; "));
    s.push_str("\n- 区域颜色: ");
    let names: Vec<String> = (0..5).map(|i| format!("{} rgb{:?}", REGIONS_ZH[i], REGION_COLORS[i])).collect();
    s.push_str(&names.join("; "));
    s.push_str("\n- 截图三格: 原生 | 候选 | 各通道 |差| (放大倍数自适应 4–64 倍, 见 JSON 的 diff_gain; 黄框 = 该块)。热图底图为原生亮度 (暗化), 每块 8×8 显示。\n");
    s
}

/// 与基线组逐块比 (如 NPU 模型对双三次): 候选比基线差 `worse_db` 以上的块在哪、是什么类型。
/// 结果写进候选组的 `vs_baseline`, 截图 (原生 | 基线 | 候选 | 候选差×4) 存 `<组>/vs_<基线>/`。
fn compare_baseline(
    gv: &mut [Value],
    base: &str,
    imgs: &std::collections::HashMap<String, Img>,
    groups: &[(String, Vec<String>)],
    refs: &[String],
    cfg: &DiagConfig,
    out: &Path,
) -> Result<(), String> {
    const WORSE_DB: f64 = 1.0;
    let bi = gv.iter().position(|g| g["label"] == json!(base)).ok_or_else(|| format!("--baseline {base}: 没有这个组"))?;
    let bframes = gv[bi]["frames"].clone();
    let t = cfg.tile;
    let arr = |v: &Value| -> Vec<f64> { v.as_array().map(|a| a.iter().map(|x| x.as_f64().unwrap_or(0.0)).collect()).unwrap_or_default() };
    for (gi, g) in gv.iter_mut().enumerate() {
        if gi == bi {
            continue;
        }
        let label = g["label"].as_str().unwrap_or("").to_string();
        let dir = out.join(&label).join(format!("vs_{base}"));
        std::fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        let tw = refs.first().map(|r| imgs[r].w / t).unwrap_or(1);
        let mut frames = Vec::new();
        let (mut n_all, mut n_worse, mut n_better, mut n_any) = (0usize, 0usize, 0usize, 0usize);
        let mut by_region = vec![(0usize, 0usize, 0f64, 0usize); REGIONS.len()];
        let mut by_type = vec![0usize; KINDS.len()];
        for (i, fr) in g["frames"].as_array().cloned().unwrap_or_default().iter().enumerate() {
            let a = arr(&fr["tiles"]["psnr_rgb"]);
            let b = arr(&bframes[i]["tiles"]["psnr_rgb"]);
            let reg = arr(&fr["tiles"]["region"]);
            let typ = arr(&fr["tiles"]["type"]);
            let delta: Vec<f64> = a.iter().zip(&b).map(|(x, y)| x - y).collect();
            let bad: Vec<usize> = (0..delta.len()).filter(|k| delta[*k] < -WORSE_DB && a[*k] < cfg.ok_db).collect();
            let good = (0..delta.len()).filter(|k| delta[*k] > WORSE_DB).count();
            let any: Vec<usize> = (0..delta.len()).filter(|k| delta[*k] < -WORSE_DB).collect();
            n_any += any.len();
            for k in &any {
                by_region[reg[*k] as usize].3 += 1;
            }
            n_all += delta.len();
            n_worse += bad.len();
            n_better += good;
            for k in 0..delta.len() {
                let r = by_region.get_mut(reg[k] as usize).ok_or("区域码越界")?;
                r.0 += 1;
                r.2 += delta[k];
            }
            for k in &bad {
                by_region[reg[*k] as usize].1 += 1;
                by_type[typ[*k] as usize] += 1;
            }
            let mut order = bad.clone();
            order.sort_by(|p, q| delta[*p].partial_cmp(&delta[*q]).unwrap_or(std::cmp::Ordering::Equal));
            // 先列候选本身已降质 (< ok_db) 的变差块, 再补「仍 ≥ ok_db 但比基线差」的块 (如天空色带)
            let mut rest: Vec<usize> = any.iter().copied().filter(|k| !bad.contains(k)).collect();
            rest.sort_by(|p, q| delta[*p].partial_cmp(&delta[*q]).unwrap_or(std::cmp::Ordering::Equal));
            order.extend(rest.into_iter().take(cfg.top.saturating_sub(order.len().min(cfg.top))));
            let mut worst = Vec::new();
            for (rank, k) in order.iter().take(cfg.top).enumerate() {
                let (x, y) = tile_rect(*k, tw, t);
                let name = format!("f{i}_{:02}_{}_x{x}_y{y}.png", rank + 1, KINDS[typ[*k] as usize]);
                let gidx = groups.iter().position(|(l, _)| *l == label).ok_or("组丢了")?;
                let bidx = groups.iter().position(|(l, _)| l == base).ok_or("基线组丢了")?;
                let gain = crop_png(&dir.join(&name), &imgs[&refs[i]], &[&imgs[&groups[bidx].1[i]], &imgs[&groups[gidx].1[i]]], x, y, t)?;
                worst.push(json!({"rank": rank + 1, "tile": k, "x": x, "y": y, "delta_db": r4(delta[*k]),
                    "psnr": r4(a[*k]), "baseline_psnr": r4(b[*k]), "type": KINDS[typ[*k] as usize], "visible": a[*k] < cfg.ok_db,
                    "region": REGIONS[reg[*k] as usize], "crop": format!("{label}/vs_{base}/{name}"), "diff_gain": gain}));
            }
            // 热图: 红 = 比基线差, 蓝 = 比基线好 (±6 dB 满色)
            let tb = thumb(&imgs[&refs[i]], tw, delta.len() / tw.max(1), t);
            heat_png(&dir.join(format!("f{i}_delta.png")), &tb, tw, t, |k| div_color(-delta[k] / 6.0))?;
            frames.push(json!({
                "index": i, "worse_tiles": bad.len(), "worse_tiles_any_level": any.len(), "better_tiles": good, "tiles": delta.len(),
                "mean_delta_db": r4(delta.iter().sum::<f64>() / delta.len().max(1) as f64),
                "delta_db": delta.iter().map(|v| r4(*v)).collect::<Vec<_>>(),
                "heatmap": format!("{label}/vs_{base}/f{i}_delta.png"), "worst": worst,
            }));
        }
        let regions: Map<String, Value> = REGIONS
            .iter()
            .enumerate()
            .filter(|(i, _)| by_region[*i].0 > 0)
            .map(|(i, n)| {
                let (all, worse, sum, anyw) = by_region[i];
                (n.to_string(), json!({"tiles": all, "worse": worse, "worse_frac": r4(worse as f64 / all as f64),
                    "worse_any_level": anyw, "worse_any_level_frac": r4(anyw as f64 / all as f64),
                    "mean_delta_db": r4(sum / all as f64), "share_of_worse": r4(worse as f64 / n_worse.max(1) as f64)}))
            })
            .collect();
        let types: Map<String, Value> =
            KINDS.iter().enumerate().filter(|(i, _)| by_type[*i] > 0).map(|(i, n)| (n.to_string(), json!(by_type[i]))).collect();
        g["vs_baseline"] = json!({
            "baseline": base, "worse_db": WORSE_DB,
            "worse_tiles": n_worse, "worse_tiles_any_level": n_any, "better_tiles": n_better, "tiles": n_all,
            "note": "worse = 比基线差 worse_db 以上且候选块 < ok_db (看得出的降质); worse_any_level = 只要比基线差 worse_db 以上 (含仍 ≥ ok_db 的块)",
            "worse_frac": r4(n_worse as f64 / n_all.max(1) as f64), "better_frac": r4(n_better as f64 / n_all.max(1) as f64),
            "worse_by_region": regions, "worse_by_type": types, "frames": frames,
        });
    }
    Ok(())
}

fn split_list(v: &str) -> Vec<String> {
    v.split(',').map(str::trim).filter(|s| !s.is_empty()).map(String::from).collect()
}

fn parse_rect(v: &str) -> Option<(usize, usize, usize, usize)> {
    let n: Vec<usize> = v.split(',').filter_map(|x| x.trim().parse().ok()).collect();
    (n.len() == 4 && n[0] < n[2] && n[1] < n[3]).then(|| (n[0], n[1], n[2], n[3]))
}

const USAGE: &str = "用法: phonefarm iq-diagnose --ref 原生0.png,原生1.png,... --cand [组名=]候选0.png,候选1.png,... [--cand 组名=...]... --out 新目录
  [--ctrl 其他时刻原生a.png,b.png]   分区用 (哪里在动 / 自动 UI); 缺省 = 多张 --ref 时每帧用其余 --ref
  [--prev 前帧0,... --next 后帧0,...]  插帧模式: --ref = 真值中间帧, --cand = 插出的帧 (加重影/运动边缘光晕/静止块/闪烁)
  [--baseline 组名]   其他组与它逐块比: 比基线差 1 dB 以上的块在哪、是什么类型 (如 NPU 模型对双三次)
  [--ui x0,y0,x1,y1]... [--ui-auto] [--tile 32] [--top 12] [--ok-db 45] [--grid 8[,16]] [--detail-thr 0.3] [--ring-pct 2]
  --ref 与每组 --cand 一一对应 (同一时刻)。--out 必须不存在或为空 (不覆盖任何文件)。
  输出: diagnose.json (机器读) + summary.md (人读) + 每组每帧热图与最差块截图 PNG。
  退出码: 0 = 诊断完成 (不代表画质如何), 2 = 用法/输入错。";

/// `phonefarm iq-diagnose ...` 见 [`USAGE`]。
pub fn run_iq_diagnose(args: &[String]) -> i32 {
    let mut cfg = DiagConfig::default();
    let (mut refs, mut ctrl, mut prev, mut next) = (vec![], vec![], vec![], vec![]);
    let mut groups: Vec<(String, Vec<String>)> = Vec::new();
    let mut out: Option<PathBuf> = None;
    let mut baseline: Option<String> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut val = || it.next().cloned().unwrap_or_default();
        let num = |s: String, d: f64| s.parse::<f64>().unwrap_or(d);
        match a.as_str() {
            "--ref" => refs.extend(split_list(&val())),
            "--cand" => {
                let v = val();
                let (label, list) = match v.split_once('=') {
                    Some((l, r)) if !l.contains(',') && !l.contains('/') => (l.to_string(), r.to_string()),
                    _ => (format!("cand{}", groups.len()), v.clone()),
                };
                groups.push((label, split_list(&list)));
            }
            "--ctrl" => ctrl.extend(split_list(&val())),
            "--prev" => prev.extend(split_list(&val())),
            "--next" => next.extend(split_list(&val())),
            "--out" => out = Some(PathBuf::from(val())),
            "--baseline" => baseline = Some(val()),
            "--tile" => cfg.tile = (num(val(), 32.0) as usize).clamp(8, 256),
            "--top" => cfg.top = num(val(), 12.0) as usize,
            "--ok-db" => cfg.ok_db = num(val(), cfg.ok_db),
            "--detail-thr" => cfg.detail_thr = num(val(), cfg.detail_thr),
            "--ring-pct" => cfg.ring_pct = num(val(), cfg.ring_pct),
            "--grid" => {
                let p: Vec<usize> = split_list(&val()).iter().filter_map(|x| x.parse().ok()).filter(|p| *p >= 2).collect();
                if !p.is_empty() {
                    cfg.grid_periods = p;
                }
            }
            "--ui" => match parse_rect(&val()) {
                Some(r) => cfg.ui.push(r),
                None => {
                    eprintln!("--ui 要 x0,y0,x1,y1 (x0<x1, y0<y1)\n{USAGE}");
                    return 2;
                }
            },
            "--ui-auto" => cfg.ui_auto = true,
            "-h" | "--help" => {
                println!("{USAGE}");
                return 0;
            }
            _ => {
                eprintln!("不认识的参数 {a}\n{USAGE}");
                return 2;
            }
        }
    }
    let Some(out) = out else {
        eprintln!("缺 --out\n{USAGE}");
        return 2;
    };
    if refs.is_empty() || groups.is_empty() {
        eprintln!("{USAGE}");
        return 2;
    }
    for (l, c) in &groups {
        if c.len() != refs.len() {
            eprintln!("组 {l}: 候选 {} 张, 原生 {} 张, 要一一对应", c.len(), refs.len());
            return 2;
        }
    }
    if (!prev.is_empty() || !next.is_empty()) && (prev.len() != refs.len() || next.len() != refs.len()) {
        eprintln!("插帧模式: --prev / --next 都要和 --ref 一样多 ({} 张)", refs.len());
        return 2;
    }
    let mut labels: Vec<&String> = groups.iter().map(|g| &g.0).collect();
    labels.sort();
    labels.dedup();
    if labels.len() != groups.len() {
        eprintln!("组名重复");
        return 2;
    }
    // 不覆盖: 输出目录必须不存在或为空
    if out.exists() && std::fs::read_dir(&out).map(|mut d| d.next().is_some()).unwrap_or(true) {
        eprintln!("{} 已存在且不为空: 不覆盖, 换一个新目录", out.display());
        return 2;
    }
    if let Err(e) = std::fs::create_dir_all(&out) {
        eprintln!("{}: {e}", out.display());
        return 2;
    }
    let mut imgs = std::collections::HashMap::new();
    let all: Vec<&String> = refs.iter().chain(&ctrl).chain(&prev).chain(&next).chain(groups.iter().flat_map(|g| g.1.iter())).collect();
    for p in all {
        if !imgs.contains_key(p) {
            match load_img(p) {
                Ok(i) => {
                    imgs.insert(p.clone(), i);
                }
                Err(e) => {
                    eprintln!("{e}");
                    return 2;
                }
            }
        }
    }
    let mode = if prev.is_empty() { "sr" } else { "interp" };
    let mut gv = Vec::new();
    for (label, cands) in &groups {
        let g = Group { label: label.clone(), refs: refs.clone(), cands: cands.clone() };
        let gdir = out.join(label);
        if let Err(e) = std::fs::create_dir_all(&gdir) {
            eprintln!("{}: {e}", gdir.display());
            return 2;
        }
        match run_group(&g, &imgs, &ctrl, &prev, &next, &cfg, &gdir) {
            Ok(v) => gv.push(v),
            Err(e) => {
                eprintln!("{e}");
                return 2;
            }
        }
        eprintln!("组 {label} 完成");
    }
    if let Some(b) = &baseline {
        if let Err(e) = compare_baseline(&mut gv, b, &imgs, &groups, &refs, &cfg, &out) {
            eprintln!("{e}");
            return 2;
        }
    }
    let r0 = &imgs[&refs[0]];
    let rep = json!({
        "schema": "phonefarm.iq-diagnose/1",
        "mode": mode,
        "width": r0.w, "height": r0.h, "tile": cfg.tile,
        "grid": [r0.w / cfg.tile, r0.h / cfg.tile],
        "codes": {
            "type": KINDS, "type_zh": KINDS_ZH, "region": REGIONS, "region_zh": REGIONS_ZH,
            "tags_bits": {"type": "第 i 位 = type[i]", "ui_damage": T_UI_DAMAGE, "static_damage": T_STATIC_DAMAGE, "flicker": T_FLICKER},
            "tiles_layout": "每帧 tiles.* 为行优先数组, 长度 grid[0]*grid[1], 第 k 块左上角 = ((k % grid[0]) * tile, (k / grid[0]) * tile)",
            "train_weight": "块 MSE / 本帧平均块 MSE, 截到 [0.25, 8]; 训练时可当逐块损失权重或补丁采样概率",
        },
        "config": {
            "ok_db": cfg.ok_db, "detail_thr": cfg.detail_thr, "offset_frac": cfg.offset_frac, "offset_min": cfg.offset_min,
            "ring_radius": cfg.ring_radius, "ring_tol": cfg.ring_tol, "ring_contrast": cfg.ring_contrast, "ring_pct": cfg.ring_pct,
            "grid_periods": cfg.grid_periods, "grid_ratio": cfg.grid_ratio, "edge_grad": cfg.edge_grad, "edge_frac": cfg.edge_frac,
            "texture_lap": cfg.texture_lap, "motion_db": cfg.motion_db, "stable_db": cfg.stable_db,
            "ui": cfg.ui.iter().map(|r| json!([r.0, r.1, r.2, r.3])).collect::<Vec<_>>(), "ui_auto": cfg.ui_auto, "ui_edge_grad": cfg.ui_edge_grad, "ui_stable_px": cfg.ui_stable_px, "ui_edge_frac": cfg.ui_edge_frac,
            "env_radius": cfg.env_radius, "env_tol": cfg.env_tol, "halo_pct": cfg.halo_pct,
            "ghost_r2": cfg.ghost_r2, "ghost_coef": cfg.ghost_coef, "flicker_luma": cfg.flicker_luma, "flicker_detail": cfg.flicker_detail,
        },
        "refs": refs, "ctrl": ctrl, "prev": prev, "next": next, "baseline": baseline,
        "groups": gv,
    });
    let jp = out.join("diagnose.json");
    let mp = out.join("summary.md");
    if let Err(e) = std::fs::write(&jp, serde_json::to_string_pretty(&rep).unwrap_or_default()) {
        eprintln!("{}: {e}", jp.display());
        return 2;
    }
    if let Err(e) = std::fs::write(&mp, summary_md(&rep)) {
        eprintln!("{}: {e}", mp.display());
        return 2;
    }
    for g in rep["groups"].as_array().into_iter().flatten() {
        let m = &g["summary"];
        println!(
            "{}: PSNR {:.2} dB, 细节 log2 {:+.3}, 主要降质 {} — {}",
            g["label"].as_str().unwrap_or(""),
            m["psnr_rgb_mean"].as_f64().unwrap_or(0.0),
            m["detail_log2_mean"].as_f64().unwrap_or(0.0),
            zh_kind(m["primary"].as_str().unwrap_or("")),
            fmt_share(&m["type_share"], 4)
        );
    }
    println!("写出 {} 与 {}", jp.display(), mp.display());
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    const W: usize = 256;
    const H: usize = 192;
    const UI: (usize, usize, usize, usize) = (160, 128, 224, 176);

    /// 连续场景 (可超采样): 左上纹理, 右上斜条纹 (硬边), 左下平坦渐变, 右下纹理 + UI 框 (深底白字)。
    fn field(x: f32, y: f32, shift: f32) -> f32 {
        let (ix, iy) = (x as usize, y as usize);
        if ix >= UI.0 && ix < UI.2 && iy >= UI.1 && iy < UI.3 {
            // 「字」: 2 像素宽的横竖笔画
            let (lx, ly) = (x - UI.0 as f32, y - UI.1 as f32);
            let stroke = (lx % 12.0 < 2.0 && ly % 16.0 < 12.0) || (ly % 16.0 < 2.0 && lx % 12.0 < 8.0);
            return if stroke { 245.0 } else { 25.0 };
        }
        let xs = x - shift;
        if y < 96.0 && x < 128.0 {
            128.0 + 45.0 * (0.9 * xs).sin() * (0.7 * y).sin() + 25.0 * (0.31 * xs + 0.5 * y).sin()
        } else if y < 96.0 {
            if (xs * 0.37 + y).rem_euclid(12.0) < 6.0 {
                40.0
            } else {
                210.0
            }
        } else if x < 128.0 {
            90.0 + 0.2 * x
        } else {
            120.0 + 35.0 * (0.8 * xs + 0.3 * y).sin() * (0.6 * y).cos()
        }
    }

    /// 渲染: 每像素 ss×ss 采样平均 (ss = 1 即不抗锯齿)。灰度复制成 RGB 再加点色调。
    fn render(ss: usize, shift: f32) -> Img {
        let mut px = vec![0u8; W * H * 3];
        for y in 0..H {
            for x in 0..W {
                let mut a = 0f32;
                for sy in 0..ss {
                    for sx in 0..ss {
                        let fx = x as f32 + (sx as f32 + 0.5) / ss as f32;
                        let fy = y as f32 + (sy as f32 + 0.5) / ss as f32;
                        a += field(fx, fy, shift);
                    }
                }
                let v = a / (ss * ss) as f32;
                let i = (y * W + x) * 3;
                px[i] = v.clamp(0.0, 255.0) as u8;
                px[i + 1] = (v * 0.92 + 8.0).clamp(0.0, 255.0) as u8;
                px[i + 2] = (v * 0.85 + 20.0).clamp(0.0, 255.0) as u8;
            }
        }
        Img::new(W, H, px).unwrap()
    }

    fn map_px(a: &Img, f: impl Fn(usize, usize, usize, f32) -> f32) -> Img {
        let mut o = a.clone();
        for y in 0..H {
            for x in 0..W {
                for c in 0..3 {
                    let i = (y * W + x) * 3 + c;
                    o.rgb[i] = f(x, y, c, a.rgb[i] as f32).round().clamp(0.0, 255.0) as u8;
                }
            }
        }
        o
    }

    fn gblur(a: &Img, s: f32, inside: Option<(usize, usize, usize, usize)>) -> Img {
        let r = (3.0 * s).ceil() as isize;
        let g: Vec<f32> = (-r..=r).map(|i| (-(i * i) as f32 / (2.0 * s * s)).exp()).collect();
        let gs: f32 = g.iter().sum();
        let mut t = vec![0f32; W * H * 3];
        for y in 0..H {
            for x in 0..W {
                for c in 0..3 {
                    let mut acc = 0f32;
                    for (k, gk) in g.iter().enumerate() {
                        let xx = (x as isize + k as isize - r).clamp(0, W as isize - 1) as usize;
                        acc += gk * a.rgb[(y * W + xx) * 3 + c] as f32;
                    }
                    t[(y * W + x) * 3 + c] = acc / gs;
                }
            }
        }
        map_px(a, |x, y, c, v| {
            if let Some((x0, y0, x1, y1)) = inside {
                if !(x >= x0 && x < x1 && y >= y0 && y < y1) {
                    return v;
                }
            }
            let mut acc = 0f32;
            for (k, gk) in g.iter().enumerate() {
                let yy = (y as isize + k as isize - r).clamp(0, H as isize - 1) as usize;
                acc += gk * t[(yy * W + x) * 3 + c];
            }
            acc / gs
        })
    }

    fn cfg() -> DiagConfig {
        DiagConfig { tile: 16, ui: vec![UI], ..DiagConfig::default() }
    }

    fn diag(r: &Img, c: &Img) -> FrameDiag {
        analyze(&FrameInput { r, c, ctrls: vec![], prev: None, next: None }, &cfg()).unwrap()
    }

    fn primary(d: &FrameDiag) -> String {
        primary_of(&type_share(&d.tiles.iter().collect::<Vec<_>>()))
    }

    fn shares(d: &FrameDiag) -> Value {
        type_share(&d.tiles.iter().collect::<Vec<_>>())
    }

    #[test]
    fn identical_is_all_ok() {
        let r = render(4, 0.0);
        let d = diag(&r, &r);
        assert!(d.tiles.iter().all(|s| s.kind == Some(Kind::Ok)));
        assert_eq!(primary(&d), "none");
    }

    #[test]
    fn regions_are_sensible() {
        let r = render(4, 0.0);
        let d = diag(&r, &r);
        let at = |x: usize, y: usize| d.tiles[(y / 16) * d.tw + x / 16].region.unwrap();
        assert_eq!(at(170, 140), Region::Ui);
        assert_eq!(at(200, 40), Region::Edge);
        assert_eq!(at(40, 40), Region::Texture);
        assert_eq!(at(40, 150), Region::Flat);
    }

    #[test]
    fn blur_is_blur() {
        let r = render(4, 0.0);
        let d = diag(&r, &gblur(&r, 1.2, None));
        assert_eq!(primary(&d), "blur", "{}", shares(&d));
        assert!(d.global["detail_log2"].as_f64().unwrap() < -0.5);
        // 纹理区的块都应是模糊
        let tex: Vec<&TileStat> = d.tiles.iter().filter(|s| s.region == Some(Region::Texture)).collect();
        let blur = tex.iter().filter(|s| s.kind == Some(Kind::Blur)).count();
        assert!(blur * 10 >= tex.len() * 9, "{blur}/{}", tex.len());
    }

    #[test]
    fn unsharp_mask_is_ringing() {
        let r = render(4, 0.0);
        let b = gblur(&r, 1.0, None);
        let c = map_px(&r, |x, y, ch, v| v + 1.5 * (v - b.rgb[(y * W + x) * 3 + ch] as f32));
        let d = diag(&r, &c);
        assert_eq!(primary(&d), "ringing", "{}", type_share(&d.tiles.iter().collect::<Vec<_>>()));
        assert!(d.global["overshoot_pct"].as_f64().unwrap() > 1.0);
    }

    #[test]
    fn point_sampling_is_aliasing() {
        let r = render(4, 0.0);
        let c = render(1, 0.0);
        let d = diag(&r, &c);
        assert_eq!(primary(&d), "aliasing", "{}", type_share(&d.tiles.iter().collect::<Vec<_>>()));
        // 锯齿集中在硬边区
        let edge: Vec<&TileStat> = d.tiles.iter().filter(|s| s.region == Some(Region::Edge) && s.kind != Some(Kind::Ok)).collect();
        assert!(!edge.is_empty() && edge.iter().filter(|s| s.kind == Some(Kind::Aliasing)).count() * 2 > edge.len());
    }

    #[test]
    fn colour_shift_is_colour_shift_with_channel_offsets() {
        let r = render(4, 0.0);
        let c = map_px(&r, |_, _, ch, v| v + [6.0, -2.0, 0.0][ch]);
        let d = diag(&r, &c);
        assert_eq!(primary(&d), "color_shift");
        let o = &d.global["offset_rgb"];
        assert!(o[0].as_f64().unwrap() > 5.0 && o[1].as_f64().unwrap() < -1.5 && o[2].as_f64().unwrap().abs() < 0.5, "{o}");
        // 亮度偏移 (三通道一起 +3) 也是这一类
        let d = diag(&r, &map_px(&r, |_, _, _, v| v + 3.0));
        assert_eq!(primary(&d), "color_shift");
    }

    #[test]
    fn ui_only_damage_is_localised_to_ui() {
        let r = render(4, 0.0);
        let c = gblur(&r, 1.0, Some(UI));
        let d = diag(&r, &c);
        let tiles: Vec<&TileStat> = d.tiles.iter().collect();
        let reg = region_breakdown(&tiles, None);
        assert!(reg["ui"]["err_share"].as_f64().unwrap() > 0.95, "{reg}");
        let bad: Vec<&TileStat> = d.tiles.iter().filter(|s| s.kind != Some(Kind::Ok)).collect();
        assert!(!bad.is_empty());
        assert!(bad.iter().all(|s| s.region == Some(Region::Ui) && s.tags & T_UI_DAMAGE != 0));
        assert_eq!(primary(&d), "blur", "{}", shares(&d));
    }

    #[test]
    fn one_pixel_shift_is_misalign() {
        let r = render(4, 0.0);
        let c = map_px(&r, |x, y, ch, v| if x > 0 { r.rgb[(y * W + x - 1) * 3 + ch] as f32 } else { v });
        let d = diag(&r, &c);
        assert_eq!(primary(&d), "misalign", "{}", shares(&d));
        // 整帧位移估计: 向右 ≈ 1 像素 (梯度线性化在硬边上偏小, 只要求方向和量级)
        let dx = d.global["shift_px"]["dx"].as_f64().unwrap();
        assert!(dx > 0.4 && dx < 1.5 && d.global["shift_px"]["dy"].as_f64().unwrap().abs() < 0.2, "{}", d.global);
    }

    #[test]
    fn coarse_output_quantization_is_flagged() {
        let r = render(4, 0.0);
        let d = diag(&r, &r);
        assert_eq!(d.global["levels"]["quantization_suspect"], json!(false));
        // 输出步长 1.2 灰阶 (同 10-20 int8 模型的 out_quant): 部分码值永远出不来
        let c = map_px(&r, |_, _, _, v| (v / 1.2).round() * 1.2);
        let d = diag(&r, &c);
        assert_eq!(d.global["levels"]["quantization_suspect"], json!(true), "{}", d.global["levels"]);
        assert!(d.global["levels"]["missing_in_cand"][0].as_u64().unwrap() >= 8);
    }

    #[test]
    fn per_block_offsets_are_blocking() {
        let r = render(4, 0.0);
        let mut s = 12345u64;
        let mut offs = vec![0f32; (W / 8) * (H / 8)];
        for o in offs.iter_mut() {
            s = s.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            *o = ((s >> 33) % 13) as f32 - 6.0;
        }
        let c = map_px(&r, |x, y, _, v| v + offs[(y / 8) * (W / 8) + x / 8]);
        let d = diag(&r, &c);
        assert_eq!(primary(&d), "blocking", "{}", type_share(&d.tiles.iter().collect::<Vec<_>>()));
        assert!(d.global["grid"]["8"]["ratio"].as_f64().unwrap() > 3.0);
        assert!(d.tiles.iter().filter(|s| s.kind == Some(Kind::Blocking)).all(|s| s.grid_period == 8));
    }

    #[test]
    fn motion_region_comes_from_controls() {
        // 原生在动 (纹理随时间平移), 对照 = 其他时刻原生
        let r = render(2, 0.0);
        let c1 = render(2, 3.0);
        let c2 = render(2, -3.0);
        let d = analyze(&FrameInput { r: &r, c: &r, ctrls: vec![&c1, &c2], prev: None, next: None }, &cfg()).unwrap();
        let at = |x: usize, y: usize| d.tiles[(y / 16) * d.tw + x / 16].region.unwrap();
        assert_eq!(at(40, 40), Region::Motion);
        assert_eq!(at(40, 150), Region::Flat, "平坦渐变不随平移变");
        // UI 自动识别: 不动 + 高反差
        let mut c = cfg();
        c.ui.clear();
        c.ui_auto = true;
        let d = analyze(&FrameInput { r: &r, c: &r, ctrls: vec![&c1, &c2], prev: None, next: None }, &c).unwrap();
        let at = |x: usize, y: usize| d.tiles[(y / 16) * d.tw + x / 16].region.unwrap();
        // UI 框里的块 (逐像素不变的强边缘) 都认出来
        for y in (UI.1..UI.3).step_by(16) {
            for x in (UI.0..UI.2).step_by(16) {
                assert_eq!(at(x, y), Region::Ui, "({x},{y})");
            }
        }
        // 在动的纹理/条纹内部不算 UI (分区交界那几条不动的硬边会被算进去, 合成图特有)
        assert_ne!(at(40, 40), Region::Ui);
        assert_ne!(at(200, 40), Region::Ui);
        assert_ne!(at(40, 150), Region::Ui);
    }

    // ---------------- 插帧

    /// 插帧序列: 纹理背景 + 深色物体向右移 (每帧 6 像素) + 静止 UI 框。
    fn interp_frame(t: f32) -> Img {
        let base = render(2, 0.0);
        let ox = 30.0 + 6.0 * t;
        map_px(&base, |x, y, _, v| {
            let (fx, fy) = (x as f32, y as f32);
            if fx >= ox && fx < ox + 40.0 && (100.0..160.0).contains(&fy) && x < UI.0 {
                20.0
            } else {
                v
            }
        })
    }

    fn interp_diag(c: &Img) -> FrameDiag {
        let (p, r, n) = (interp_frame(0.0), interp_frame(1.0), interp_frame(2.0));
        analyze(&FrameInput { r: &r, c, ctrls: vec![], prev: Some(&p), next: Some(&n) }, &cfg()).unwrap()
    }

    #[test]
    fn interp_blend_and_duplicate_are_ghosting() {
        let (p, n) = (interp_frame(0.0), interp_frame(2.0));
        let blend = map_px(&p, |x, y, c, v| 0.5 * v + 0.5 * n.rgb[(y * W + x) * 3 + c] as f32);
        let d = interp_diag(&blend);
        assert_eq!(primary(&d), "ghosting", "{}", type_share(&d.tiles.iter().collect::<Vec<_>>()));
        // 前后帧都与真值不同的块: 误差 ≈ 0.5(P−T) + 0.5(N−T)
        let gs: Vec<(f64, f64, f64)> = d.tiles.iter().filter(|s| s.kind == Some(Kind::Ghosting)).filter_map(|s| s.ghost).collect();
        assert!(gs.iter().any(|g| (g.0 - 0.5).abs() < 0.1 && (g.1 - 0.5).abs() < 0.1 && g.2 > 0.9), "{gs:?}");
        let d = interp_diag(&p);
        assert_eq!(primary(&d), "ghosting");
        let g = d.tiles.iter().filter(|s| s.kind == Some(Kind::Ghosting)).filter_map(|s| s.ghost).next().unwrap();
        assert!(g.0 > 0.8 && g.1.abs() < 0.2, "复制前帧: α≈1 β≈0, {g:?}");
    }

    #[test]
    fn interp_halo_at_moving_edge() {
        let r = interp_frame(1.0);
        let ox = 36usize;
        // 物体左右边外侧 3 像素白边
        let c = map_px(&r, |x, y, _, v| {
            let band = (x + 3 >= ox && x < ox) || (x >= ox + 40 && x < ox + 43);
            if band && (100..160).contains(&y) { 255.0 } else { v }
        });
        let d = interp_diag(&c);
        assert_eq!(primary(&d), "motion_halo", "{}", type_share(&d.tiles.iter().collect::<Vec<_>>()));
        assert!(d.global["halo_excess_pct"].as_f64().unwrap() > 0.0);
    }

    #[test]
    fn interp_hud_ghost_is_static_damage() {
        let r = interp_frame(1.0);
        // UI 字被拉成双份: 与右移 4 像素的自己平均
        let c = map_px(&r, |x, y, ch, v| {
            if x >= UI.0 + 4 && x < UI.2 && y >= UI.1 && y < UI.3 {
                0.5 * v + 0.5 * r.rgb[(y * W + x - 4) * 3 + ch] as f32
            } else {
                v
            }
        });
        let d = interp_diag(&c);
        let bad: Vec<&TileStat> = d.tiles.iter().filter(|s| s.tags & T_STATIC_DAMAGE != 0).collect();
        assert!(!bad.is_empty());
        assert!(bad.iter().all(|s| s.region == Some(Region::Ui) && s.tags & T_UI_DAMAGE != 0));
        // 正确插帧 (= 真值): 无静止块损坏, 全 ok
        let d = interp_diag(&r);
        assert!(d.tiles.iter().all(|s| s.kind == Some(Kind::Ok) && s.tags == 0));
    }

    #[test]
    fn interp_sequence_flicker() {
        // 4 帧插帧都比真值亮 1.5 灰阶 → 与原生交替显示时闪烁
        let mut diags = Vec::new();
        let mut clean = Vec::new();
        for i in 0..4 {
            let (p, r, n) = (interp_frame(2.0 * i as f32), interp_frame(2.0 * i as f32 + 1.0), interp_frame(2.0 * i as f32 + 2.0));
            let c = map_px(&r, |_, _, _, v| v + 1.5);
            diags.push(analyze(&FrameInput { r: &r, c: &c, ctrls: vec![], prev: Some(&p), next: Some(&n) }, &cfg()).unwrap());
            clean.push(analyze(&FrameInput { r: &r, c: &r, ctrls: vec![], prev: Some(&p), next: Some(&n) }, &cfg()).unwrap());
        }
        let (mark, v) = flicker(&diags, &cfg());
        assert_eq!(v["sequence_flicker"], json!(true), "{v}");
        assert!(mark.iter().filter(|m| **m).count() * 2 > mark.len());
        let (mark, v) = flicker(&clean, &cfg());
        assert_eq!(v["sequence_flicker"], json!(false), "{v}");
        assert!(mark.iter().all(|m| !*m));
    }

    #[test]
    fn cli_writes_json_and_never_overwrites() {
        let dir = std::env::temp_dir().join(format!("iqdiag_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let r = render(4, 0.0);
        let save = |name: &str, i: &Img| {
            let p = dir.join(name);
            image::RgbImage::from_raw(W as u32, H as u32, i.rgb.clone()).unwrap().save(&p).unwrap();
            p.to_string_lossy().to_string()
        };
        let rp = save("r.png", &r);
        let bp = save("b.png", &gblur(&r, 1.2, None));
        let out = dir.join("out");
        let args: Vec<String> = [
            "--ref",
            &rp,
            "--cand",
            &format!("blur={bp}"),
            "--cand",
            &format!("same={rp}"),
            "--baseline",
            "same",
            "--tile",
            "16",
            "--top",
            "3",
            "--out",
            &out.to_string_lossy(),
        ]
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert_eq!(run_iq_diagnose(&args), 0);
        let v: Value = serde_json::from_str(&std::fs::read_to_string(out.join("diagnose.json")).unwrap()).unwrap();
        assert_eq!(v["groups"][0]["summary"]["primary"], json!("blur"));
        assert_eq!(v["groups"][0]["frames"][0]["tiles"]["type"].as_array().unwrap().len(), 16 * 12);
        assert_eq!(v["groups"][0]["frames"][0]["worst"].as_array().unwrap().len(), 3);
        assert!(out.join("summary.md").exists() && out.join("blur/f0/types.png").exists());
        // 基线比较: 模糊组对「原样」组, 纹理区大片变差
        let vb = &v["groups"][0]["vs_baseline"];
        assert_eq!(vb["baseline"], json!("same"));
        assert!(vb["worse_frac"].as_f64().unwrap() > 0.2, "{vb}");
        assert!(out.join("blur/vs_same/f0_delta.png").exists());
        // 第二次写同一目录: 拒绝
        assert_eq!(run_iq_diagnose(&args), 2);
    }
}
