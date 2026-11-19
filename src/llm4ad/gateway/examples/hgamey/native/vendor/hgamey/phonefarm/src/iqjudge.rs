//! 画质验收判据: 「候选的画面」与「原生画面自己跟自己比」是不是同一水平。
//!
//! 只看全帧 PSNR 有两个毛病 (2026-10-21 用原神 4 帧实测):
//! 1. 场景在动时, 全帧 PSNR 被运动主导: 原生对原生 21-25 dB, 双线性放大对原生 21-25 dB, 分不开;
//!    Megacity 关 MSAA (29.7-30.6 dB) 比同设置重拍 (28.06 dB) 还高, 噪声底门槛判它「画面没变」。
//! 2. 场景静止时, 固定 30 dB 门槛又太松: 双线性 2 倍放大 36.8 dB 也能过。
//!
//! 所以分三个指标, 每个都跟本次实测的原生自噪声比 (不是跟固定数比):
//! - **稳定块 PSNR / 1-SSIM**: 把画面切成块, 只留「对照两两都几乎一样」的块 (默认块 PSNR ≥ 45 dB),
//!   在这些块上比候选和对照。运动区域 (飞的物体、云、水) 自动排掉, 静止的 UI 留着也无妨 (它不会变差)。
//!   PSNR 管亮度/颜色偏移 (SSIM 对 +2 灰阶不敏感), SSIM 管结构 (模糊、锯齿、块效应)。
//! - **细节能量比**: 全帧亮度拉普拉斯平方之和, 候选 / 对照取 log2。不要求像素对齐, 场景在动也能比;
//!   放大变糊 (负) 和锯齿/过锐 (正) 都会偏离 0。
//!
//! 输出 (2026-10-21 起): **不判通过 / 不通过**, 只列三项指标、各自的原生自噪声和参考线
//! (「原生自噪声 + 余量」, 原来的门槛), 离参考线多远, 外加对照表与一句话摘要 (见 `crate::iqsheet`)。
//! 有指标算不了 (稳定块太少 / 对照不足 3 张 / 场景细节自己就乱跳) 就写明算不了。是否接受由人看表决定。
//! 内部的 [`IqVerdict`] 只给 eval 的 `image_policy = "gate"` (调用方自选的「超出参考线就不量性能」) 用。

use serde_json::{json, Value};

/// 判据参数。缺省值的依据见 `_tmp/ruler/image-quality/REPORT.md` (原神 4 帧标定)。
#[derive(Debug, Clone)]
pub(crate) struct IqConfig {
    /// 块边长 (像素)。
    pub tile: usize,
    /// 对照两两之间块 PSNR 都 ≥ 它, 这块才算「没在动」。
    pub stable_db: f64,
    /// 稳定块少于它, 稳定块上的 PSNR/SSIM 不算 (样本太少, 一块动画就能左右结果)。
    pub min_stable_tiles: usize,
    /// PSNR 允许比原生自噪声差多少 dB (与 [`crate::eval::IMAGE_GATE_MARGIN_DB`] 同一口径)。
    pub psnr_margin_db: f64,
    /// 原生自噪声再小, 也只要求候选到这个 PSNR (50 dB ≈ 均方根误差 0.8 个灰阶, 肉眼不可见)。
    pub psnr_cap_db: f64,
    /// 1-SSIM 上限 = 系数 × 原生自噪声的 1-SSIM + 绝对余量。
    pub dssim_factor: f64,
    pub dssim_abs: f64,
    /// 细节能量 |log2 比| 允许比原生自噪声多多少 (0.15 ≈ ±11%)。
    pub detail_margin: f64,
    /// 原生自己跟自己细节能量就差这么多 (0.5 ≈ ±41%): 场景太乱, 细节判据作废。
    pub detail_max_self: f64,
    /// 排除区域 [x0, y0, x1) × [y0, y1) (例如会跳数字的 UI)。碰到它的块整块不算。
    pub exclude: Vec<(usize, usize, usize, usize)>,
}

impl Default for IqConfig {
    fn default() -> Self {
        IqConfig {
            tile: 32,
            stable_db: 45.0,
            min_stable_tiles: 32,
            psnr_margin_db: crate::eval::IMAGE_GATE_MARGIN_DB,
            psnr_cap_db: 50.0,
            dssim_factor: 1.5,
            dssim_abs: 0.0005,
            detail_margin: 0.15,
            detail_max_self: 0.5,
            exclude: Vec::new(),
        }
    }
}

impl IqConfig {
    /// 从请求的 `image_check` 段读覆盖值 (只认数值字段; 不认识的忽略)。
    pub(crate) fn from_request(v: &Value) -> Self {
        let mut c = IqConfig::default();
        let f = |k: &str| v.get(k).and_then(|x| x.as_f64());
        if let Some(x) = f("tile") {
            c.tile = (x as usize).clamp(8, 256);
        }
        if let Some(x) = f("stable_db") {
            c.stable_db = x;
        }
        if let Some(x) = f("min_stable_tiles") {
            c.min_stable_tiles = x.max(1.0) as usize;
        }
        if let Some(arr) = v.get("exclude").and_then(|x| x.as_array()) {
            for r in arr {
                if let Some(q) = r.as_array().filter(|q| q.len() == 4) {
                    let n: Vec<usize> = q.iter().filter_map(|x| x.as_u64().map(|x| x as usize)).collect();
                    if n.len() == 4 && n[0] < n[2] && n[1] < n[3] {
                        c.exclude.push((n[0], n[1], n[2], n[3]));
                    }
                }
            }
        }
        c
    }
}

/// 一张截图预处理后的数据: 亮度 + 高斯局部均值 / 局部二阶矩 (SSIM 用) + 每块细节能量。
pub(crate) struct Shot {
    pub(crate) w: usize,
    pub(crate) h: usize,
    pub(crate) y: Vec<f32>,
    mu: Vec<f32>,
    yy: Vec<f32>,
    /// 每块拉普拉斯平方和 (行优先, tw × th)
    pub(crate) hf: Vec<f64>,
    pub(crate) tile: usize,
}

/// SSIM 用的高斯窗 σ = 1.5, 半径 4 (与 Wang 2004 的 11×11/σ1.5 同一量级)。
fn gauss() -> [f32; 9] {
    let mut g = [0f32; 9];
    let mut s = 0f32;
    for (i, v) in g.iter_mut().enumerate() {
        let x = i as f32 - 4.0;
        *v = (-x * x / (2.0 * 1.5 * 1.5)).exp();
        s += *v;
    }
    g.iter_mut().for_each(|v| *v /= s);
    g
}

/// 可分离高斯滤波, 边缘复制。
fn blur(a: &[f32], w: usize, h: usize) -> Vec<f32> {
    let g = gauss();
    let mut t = vec![0f32; a.len()];
    for y in 0..h {
        let row = &a[y * w..(y + 1) * w];
        for x in 0..w {
            let mut s = 0f32;
            for (k, gk) in g.iter().enumerate() {
                let xx = (x as isize + k as isize - 4).clamp(0, w as isize - 1) as usize;
                s += gk * row[xx];
            }
            t[y * w + x] = s;
        }
    }
    let mut o = vec![0f32; a.len()];
    for y in 0..h {
        for x in 0..w {
            let mut s = 0f32;
            for (k, gk) in g.iter().enumerate() {
                let yy = (y as isize + k as isize - 4).clamp(0, h as isize - 1) as usize;
                s += gk * t[yy * w + x];
            }
            o[y * w + x] = s;
        }
    }
    o
}

impl Shot {
    /// RGBA 像素 (screencap 裸帧去头后的样子)。尺寸不符回 None。
    pub(crate) fn from_rgba(w: usize, h: usize, px: &[u8], tile: usize) -> Option<Shot> {
        Self::from_pixels(w, h, px, 4, tile)
    }

    pub(crate) fn from_rgb(w: usize, h: usize, px: &[u8], tile: usize) -> Option<Shot> {
        Self::from_pixels(w, h, px, 3, tile)
    }

    fn from_pixels(w: usize, h: usize, px: &[u8], ch: usize, tile: usize) -> Option<Shot> {
        if w < tile || h < tile || tile == 0 || px.len() != w * h * ch {
            return None;
        }
        let y: Vec<f32> = px
            .chunks_exact(ch)
            .map(|p| (299.0 * p[0] as f32 + 587.0 * p[1] as f32 + 114.0 * p[2] as f32) / 1000.0)
            .collect();
        Self::from_luma(w, h, y, tile)
    }

    /// 直接给亮度 (插帧判据算「复制前帧 / 前后平均」这类基线时用)。
    pub(crate) fn from_luma(w: usize, h: usize, y: Vec<f32>, tile: usize) -> Option<Shot> {
        if w < tile || h < tile || tile == 0 || y.len() != w * h {
            return None;
        }
        let sq: Vec<f32> = y.iter().map(|v| v * v).collect();
        let mu = blur(&y, w, h);
        let yy = blur(&sq, w, h);
        let (tw, th) = (w / tile, h / tile);
        let mut hf = vec![0f64; tw * th];
        for r in 1..h.saturating_sub(1) {
            if r / tile >= th {
                break;
            }
            for c in 1..w - 1 {
                if c / tile >= tw {
                    break;
                }
                let i = r * w + c;
                let l = 4.0 * y[i] - y[i - 1] - y[i + 1] - y[i - w] - y[i + w];
                hf[(r / tile) * tw + c / tile] += (l * l) as f64;
            }
        }
        Some(Shot { w, h, y, mu, yy, hf, tile })
    }

    pub(crate) fn tiles(&self) -> (usize, usize) {
        (self.w / self.tile, self.h / self.tile)
    }
}

/// 一对截图的逐块数据: 块 MSE 与块平均 SSIM。
pub(crate) struct PairTiles {
    pub(crate) mse: Vec<f64>,
    pub(crate) ssim: Vec<f64>,
}

pub(crate) fn pair_tiles(a: &Shot, b: &Shot) -> PairTiles {
    let (w, h, t) = (a.w, a.h, a.tile);
    let (tw, th) = a.tiles();
    let xy: Vec<f32> = a.y.iter().zip(&b.y).map(|(p, q)| p * q).collect();
    let sxy_raw = blur(&xy, w, h);
    let (c1, c2) = ((0.01f32 * 255.0).powi(2), (0.03f32 * 255.0).powi(2));
    let mut mse = vec![0f64; tw * th];
    let mut ssim = vec![0f64; tw * th];
    for r in 0..th * t {
        for c in 0..tw * t {
            let i = r * w + c;
            let d = (a.y[i] - b.y[i]) as f64;
            let (mx, my) = (a.mu[i], b.mu[i]);
            let sxx = a.yy[i] - mx * mx;
            let syy = b.yy[i] - my * my;
            let sxy = sxy_raw[i] - mx * my;
            let s = ((2.0 * mx * my + c1) * (2.0 * sxy + c2)) / ((mx * mx + my * my + c1) * (sxx + syy + c2));
            let k = (r / t) * tw + c / t;
            mse[k] += d * d;
            ssim[k] += s as f64;
        }
    }
    let n = (t * t) as f64;
    mse.iter_mut().for_each(|v| *v /= n);
    ssim.iter_mut().for_each(|v| *v /= n);
    PairTiles { mse, ssim }
}

pub(crate) fn psnr_of_mse(mse: f64) -> f64 {
    if mse <= 1e-10 {
        99.0
    } else {
        (10.0 * (255.0f64 * 255.0 / mse).log10()).min(99.0)
    }
}

pub(crate) fn median(mut v: Vec<f64>) -> f64 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = v.len();
    if n == 0 {
        f64::NAN
    } else if n % 2 == 1 {
        v[n / 2]
    } else {
        (v[n / 2 - 1] + v[n / 2]) / 2.0
    }
}

/// 细节能量比 log2(a / b), 只算 `use_tile` 为真的块。
pub(crate) fn detail_log2(a: &Shot, b: &Shot, use_tile: &[bool]) -> f64 {
    let sa: f64 = a.hf.iter().zip(use_tile).filter(|(_, u)| **u).map(|(v, _)| *v).sum();
    let sb: f64 = b.hf.iter().zip(use_tile).filter(|(_, u)| **u).map(|(v, _)| *v).sum();
    if sa <= 0.0 && sb <= 0.0 {
        0.0
    } else {
        (sa.max(1e-9) / sb.max(1e-9)).log2()
    }
}

/// 稳定块上的 (PSNR, 1-SSIM)。
pub(crate) fn on_mask(p: &PairTiles, mask: &[bool]) -> (f64, f64) {
    let n = mask.iter().filter(|m| **m).count().max(1) as f64;
    let mse: f64 = p.mse.iter().zip(mask).filter(|(_, m)| **m).map(|(v, _)| *v).sum::<f64>() / n;
    let ss: f64 = p.ssim.iter().zip(mask).filter(|(_, m)| **m).map(|(v, _)| *v).sum::<f64>() / n;
    (psnr_of_mse(mse), 1.0 - ss)
}

/// 各指标相对参考线的位置 (内部用, 不进输出): 只给 eval 的 gate 规则和画质差距的内部状态用。
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum IqVerdict {
    /// 三项都算得出, 都在参考线原生一侧。
    WithinRef,
    /// 有指标在参考线外 (原因列出是哪几项、各差多少)。
    BeyondRef(String),
    /// 没有指标在参考线外, 但有指标算不了。
    NotComputed(String),
}

pub(crate) fn r4(x: f64) -> f64 {
    (x * 10000.0).round() / 10000.0
}

/// 主判定。`ctrls` = 同设置 (原生) 重拍, 至少 2 张, 建议 ≥ 4; `knobs` = 候选, 至少 1 张。
/// 候选应夹在对照之间拍 (对照 → 候选 → 对照): 实测候选在所有对照之后/之前拍时, 场景漂移会让空改动也落在参考线外。
pub(crate) fn judge(ctrls: &[Shot], knobs: &[Shot], cfg: &IqConfig) -> (IqVerdict, Value) {
    let mut rep = json!({"n_ctrl": ctrls.len(), "n_knob": knobs.len()});
    if ctrls.len() < 2 || knobs.is_empty() {
        let why = format!("对照 {} 张 / 候选 {} 张: 至少要 2 张对照 (建议 4 张) 和 1 张候选", ctrls.len(), knobs.len());
        finish_rep(&mut rep, Vec::new(), vec![why.clone()]);
        return (IqVerdict::NotComputed(why), rep);
    }
    let (w, h) = (ctrls[0].w, ctrls[0].h);
    if ctrls.iter().chain(knobs).any(|s| s.w != w || s.h != h || s.tile != cfg.tile) {
        let why = "截图尺寸不一致 (或块边长与配置不符), 比不了".to_string();
        finish_rep(&mut rep, Vec::new(), vec![why.clone()]);
        return (IqVerdict::NotComputed(why), rep);
    }
    let (tw, th) = ctrls[0].tiles();
    let t = cfg.tile;
    // 碰到排除区的块不算
    let usable: Vec<bool> = (0..tw * th)
        .map(|k| {
            let (x0, y0) = ((k % tw) * t, (k / tw) * t);
            !cfg.exclude.iter().any(|(ex0, ey0, ex1, ey1)| x0 < *ex1 && x0 + t > *ex0 && y0 < *ey1 && y0 + t > *ey0)
        })
        .collect();

    // 对照两两
    let mse_stable = 255.0f64 * 255.0 / 10f64.powf(cfg.stable_db / 10.0);
    let mut ctrl_pairs: Vec<PairTiles> = Vec::new();
    let mut mask = usable.clone();
    let mut self_detail = 0f64;
    for i in 0..ctrls.len() {
        for j in i + 1..ctrls.len() {
            let p = pair_tiles(&ctrls[i], &ctrls[j]);
            for (m, e) in mask.iter_mut().zip(&p.mse) {
                *m = *m && *e <= mse_stable;
            }
            self_detail = self_detail.max(detail_log2(&ctrls[i], &ctrls[j], &usable).abs());
            ctrl_pairs.push(p);
        }
    }
    let n_stable = mask.iter().filter(|m| **m).count();
    rep["tiles"] = json!({"tile_px": t, "total": tw * th, "usable": usable.iter().filter(|u| **u).count(),
        "stable": n_stable, "stable_db": cfg.stable_db});

    // 候选对每张对照
    let mut cross_detail: Vec<f64> = Vec::new();
    let mut cross_fr: Vec<(f64, f64)> = Vec::new();
    for k in knobs {
        let mut dets = Vec::new();
        let (mut ps, mut ds) = (Vec::new(), Vec::new());
        for c in ctrls {
            dets.push(detail_log2(k, c, &usable));
            if n_stable > 0 {
                let (p, d) = on_mask(&pair_tiles(k, c), &mask);
                ps.push(p);
                ds.push(d);
            }
        }
        // 每张候选取对各对照的中位数 (一张对照撞上动画不左右结果)
        cross_detail.push(median(dets));
        if n_stable > 0 {
            cross_fr.push((median(ps), median(ds)));
        }
    }

    use crate::iqsheet::{row, Better};
    let mut rows: Vec<Value> = Vec::new();
    let mut beyond: Vec<String> = Vec::new();
    let mut missing: Vec<String> = Vec::new();

    // ① 细节能量
    let detail_rule = format!("±(原生自噪声 + {:.2})", cfg.detail_margin);
    if self_detail <= cfg.detail_max_self {
        // 多张候选取离 0 最近的那张 (与 eval::judge_image 取「最好的一次」同口径)
        let v = cross_detail.iter().cloned().fold(f64::INFINITY, |a, b| a.min(b.abs()));
        let signed = cross_detail.iter().cloned().min_by(|a, b| a.abs().partial_cmp(&b.abs()).unwrap()).unwrap_or(0.0);
        let lim = self_detail + cfg.detail_margin;
        let note = if signed < 0.0 { "负 = 变糊/丢细节" } else { "正 = 多出高频: 锯齿/过锐/噪点" };
        rows.push(row("detail_log2", "细节能量比 log2", "", Better::NearZero, Some(signed), Some(self_detail), Some(lim), &detail_rule, note));
        if v > lim {
            beyond.push(format!("细节能量比 log2 = {signed:+.3} ({}), 参考线 ±{lim:.3}", if signed < 0.0 { "变糊/丢细节" } else { "多出高频" }));
        }
    } else {
        let why = format!("原生自己跟自己细节能量就差 log2 {self_detail:.2} (> {:.2}), 场景太乱", cfg.detail_max_self);
        rows.push(row("detail_log2", "细节能量比 log2", "", Better::NearZero, None, Some(self_detail), None, &detail_rule, &why));
        missing.push(why);
    }

    // ②③ 稳定块 PSNR / 1-SSIM
    let psnr_rule = format!("min(原生自噪声, {:.0}) − {:.1} dB", cfg.psnr_cap_db, cfg.psnr_margin_db);
    let dssim_rule = format!("{:.1} × 原生自噪声 + {}", cfg.dssim_factor, cfg.dssim_abs);
    let stable_missing = if ctrls.len() < 3 {
        Some("对照只有 2 张: 稳定块就是按这一对挑的, 自噪声被低估, 稳定块指标不算 (至少 3 张, 建议 4 张)".to_string())
    } else if n_stable < cfg.min_stable_tiles {
        Some(format!(
            "稳定块只有 {n_stable} 块 (< {}): 场景大部分在动, 稳定块指标没有分辨力 —— 换静止场景 / 固定抓帧时间点",
            cfg.min_stable_tiles
        ))
    } else {
        None
    };
    if let Some(why) = stable_missing {
        rows.push(row("stable_psnr_db", "稳定块 PSNR", "dB", Better::Higher, None, None, None, &psnr_rule, &why));
        rows.push(row("stable_dssim", "稳定块 1-SSIM", "", Better::Lower, None, None, None, &dssim_rule, &why));
        missing.push(why);
    } else {
        let selfs: Vec<(f64, f64)> = ctrl_pairs.iter().map(|p| on_mask(p, &mask)).collect();
        let self_psnr = selfs.iter().map(|s| s.0).fold(f64::INFINITY, f64::min);
        let self_dssim = selfs.iter().map(|s| s.1).fold(0.0, f64::max);
        let best_psnr = cross_fr.iter().map(|s| s.0).fold(f64::NEG_INFINITY, f64::max);
        let best_dssim = cross_fr.iter().map(|s| s.1).fold(f64::INFINITY, f64::min);
        let plim = self_psnr.min(cfg.psnr_cap_db) - cfg.psnr_margin_db;
        let dlim = cfg.dssim_factor * self_dssim + cfg.dssim_abs;
        rows.push(row("stable_psnr_db", "稳定块 PSNR", "dB", Better::Higher, Some(best_psnr), Some(self_psnr), Some(plim), &psnr_rule, "管亮度/颜色偏移"));
        rows.push(row("stable_dssim", "稳定块 1-SSIM", "", Better::Lower, Some(best_dssim), Some(self_dssim), Some(dlim), &dssim_rule, "管结构: 模糊、锯齿、块效应"));
        if best_psnr < plim {
            beyond.push(format!("稳定块 PSNR {best_psnr:.2} dB, 参考线 {plim:.2} dB"));
        }
        if best_dssim > dlim {
            beyond.push(format!("稳定块 1-SSIM {best_dssim:.4}, 参考线 {dlim:.4}"));
        }
    }
    finish_rep(&mut rep, rows, missing.clone());
    let v = if !beyond.is_empty() {
        IqVerdict::BeyondRef(beyond.join("; "))
    } else if missing.is_empty() {
        IqVerdict::WithinRef
    } else {
        IqVerdict::NotComputed(format!("没有指标在参考线外, 但有指标算不了: {}", missing.join("; ")))
    };
    (v, rep)
}

/// 输出统一形状: metrics (指标行) / not_computed / summary / sheet。不写通过与否。
fn finish_rep(rep: &mut Value, rows: Vec<Value>, missing: Vec<String>) {
    let extra: Vec<String> = if rows.is_empty() { missing.clone() } else { Vec::new() };
    rep["summary"] = json!(crate::iqsheet::summary(&rows, &extra));
    rep["sheet"] = json!(crate::iqsheet::sheet(&[("候选 (最好的一张)".to_string(), rows.clone())]));
    rep["beyond_ref_line"] = json!(crate::iqsheet::beyond_ref_line(&rows));
    rep["metrics"] = json!(rows);
    rep["not_computed"] = json!(missing);
}

/// 读一张截图: PNG/JPEG 走 image 库, 其余按 screencap 裸帧。
pub(crate) fn load(path: &str, tile: usize) -> Result<Shot, String> {
    let d = std::fs::read(path).map_err(|e| format!("{path}: {e}"))?;
    if let Ok(img) = image::load_from_memory(&d) {
        let rgb = img.to_rgb8();
        let (w, h) = (rgb.width() as usize, rgb.height() as usize);
        return Shot::from_rgb(w, h, rgb.as_raw(), tile).ok_or_else(|| format!("{path}: 尺寸太小"));
    }
    let (w, h, px) = crate::frameaudit::parse_raw(&d).ok_or_else(|| format!("{path}: 既不是图片也不是 screencap 裸帧"))?;
    Shot::from_rgba(w, h, px, tile).ok_or_else(|| format!("{path}: 尺寸太小"))
}

/// `phonefarm iq-judge --ctrl a.png,b.png,c.png --knob d.png[,e.png] [--exclude x0,y0,x1,y1]... [--tile 32] [--stable-db 45] [--sheet out.md]`
/// 输出 JSON (指标 + 参考线 + 对照表 + 摘要, 不判通过); 退出码 0 = 算完 (不代表画质如何), 2 = 用法/输入错。
/// 多张候选时对照表每张一列, 外加「最好的一张」一列。
pub fn run_iq_judge(args: &[String]) -> i32 {
    let usage = "用法: phonefarm iq-judge --ctrl a.png,b.png,c.png[,d.png] --knob k1.png[,k2.png] \
[--exclude x0,y0,x1,y1]... [--tile 32] [--stable-db 45] [--sheet out.md]\n  对照 = 同设置 (原生) 重拍, 至少 3 张; 候选夹在对照之间拍\n  \
输出只列指标、原生自噪声、参考线和离参考线多远 (不判通过); --sheet 另存对照表 (Markdown)";
    let mut cfg = IqConfig::default();
    let (mut ctrl, mut knob): (Vec<String>, Vec<String>) = (vec![], vec![]);
    let mut sheet_out: Option<String> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut val = || it.next().cloned().unwrap_or_default();
        match a.as_str() {
            "--ctrl" => ctrl.extend(val().split(',').filter(|s| !s.is_empty()).map(String::from)),
            "--knob" => knob.extend(val().split(',').filter(|s| !s.is_empty()).map(String::from)),
            "--tile" => cfg.tile = val().parse().unwrap_or(cfg.tile).clamp(8, 256),
            "--stable-db" => cfg.stable_db = val().parse().unwrap_or(cfg.stable_db),
            "--sheet" => sheet_out = Some(val()),
            "--exclude" => {
                let n: Vec<usize> = val().split(',').filter_map(|x| x.trim().parse().ok()).collect();
                if n.len() != 4 || n[0] >= n[2] || n[1] >= n[3] {
                    eprintln!("--exclude 要 x0,y0,x1,y1 (x0<x1, y0<y1)\n{usage}");
                    return 2;
                }
                cfg.exclude.push((n[0], n[1], n[2], n[3]));
            }
            _ => {
                eprintln!("不认识的参数 {a}\n{usage}");
                return 2;
            }
        }
    }
    if ctrl.is_empty() || knob.is_empty() {
        eprintln!("{usage}");
        return 2;
    }
    let load_all = |v: &[String]| v.iter().map(|p| load(p, cfg.tile)).collect::<Result<Vec<_>, _>>();
    let (c, k) = match (load_all(&ctrl), load_all(&knob)) {
        (Ok(c), Ok(k)) => (c, k),
        (Err(e), _) | (_, Err(e)) => {
            eprintln!("{e}");
            return 2;
        }
    };
    let (_, mut rep) = judge(&c, &k, &cfg);
    rep["ctrl"] = json!(ctrl);
    rep["knob"] = json!(knob);
    if k.len() > 1 {
        rep["sheet"] = json!(per_knob_sheet(&c, &k, &knob, &cfg, &rep));
    }
    if let Some(p) = &sheet_out {
        if let Err(e) = std::fs::write(p, rep["sheet"].as_str().unwrap_or("")) {
            eprintln!("写不了对照表 {p}: {e}");
            return 2;
        }
    }
    println!("{}", serde_json::to_string_pretty(&rep).unwrap_or_default());
    0
}

/// 多张候选: 「最好的一张」一列 + 每张一列。
fn per_knob_sheet(c: &[Shot], k: &[Shot], names: &[String], cfg: &IqConfig, best: &Value) -> String {
    let rows_of = |r: &Value| r["metrics"].as_array().cloned().unwrap_or_default();
    let mut cols: Vec<(String, Vec<Value>)> = vec![("最好的一张".to_string(), rows_of(best))];
    for (shot, name) in k.iter().zip(names) {
        let one = std::slice::from_ref(shot);
        let (_, r) = judge(c, one, cfg);
        let short = std::path::Path::new(name).file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_else(|| name.clone());
        cols.push((short, rows_of(&r)));
    }
    crate::iqsheet::sheet(&cols)
}

#[cfg(test)]
mod tests {
    use super::*;

    const W: usize = 256;
    const H: usize = 192;

    /// 确定性的「游戏画面」: 有边缘、有纹理、有平坦区。
    fn scene(seed: u64) -> Vec<u8> {
        let mut px = vec![0u8; W * H * 3];
        let mut s = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
        for y in 0..H {
            for x in 0..W {
                s = s.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
                let noise = ((s >> 33) % 24) as f32;
                let checker = if (x / 8 + y / 8) % 2 == 0 { 60.0 } else { 170.0 };
                let grad = (x as f32 / W as f32) * 50.0;
                let v = (checker + grad + noise).min(255.0);
                let i = (y * W + x) * 3;
                px[i] = v as u8;
                px[i + 1] = (v * 0.8) as u8;
                px[i + 2] = (255.0 - v) as u8;
            }
        }
        px
    }

    /// 在 (cx, cy) 画一个实心方块 = 场景里移动的物体。
    fn with_object(mut px: Vec<u8>, cx: usize, cy: usize) -> Vec<u8> {
        for y in cy..(cy + 40).min(H) {
            for x in cx..(cx + 40).min(W) {
                let i = (y * W + x) * 3;
                px[i..i + 3].copy_from_slice(&[250, 240, 20]);
            }
        }
        px
    }

    /// 2×2 平均缩小再最近邻放大 = 半分辨率渲染 + 最差的放大。
    fn half_res(px: &[u8]) -> Vec<u8> {
        let mut o = px.to_vec();
        for y in (0..H).step_by(2) {
            for x in (0..W).step_by(2) {
                for c in 0..3 {
                    let s: u32 = [(y, x), (y, x + 1), (y + 1, x), (y + 1, x + 1)]
                        .iter()
                        .map(|(yy, xx)| px[(yy * W + xx) * 3 + c] as u32)
                        .sum();
                    for (yy, xx) in [(y, x), (y, x + 1), (y + 1, x), (y + 1, x + 1)] {
                        o[(yy * W + xx) * 3 + c] = ((s + 2) / 4) as u8;
                    }
                }
            }
        }
        o
    }

    fn shot(px: &[u8]) -> Shot {
        Shot::from_rgb(W, H, px, 32).unwrap()
    }

    fn cfg() -> IqConfig {
        // 测试画面只有 8×6 = 48 块
        IqConfig { min_stable_tiles: 8, ..IqConfig::default() }
    }

    #[test]
    fn identical_static_scene_passes_and_half_res_fails() {
        let base = scene(1);
        let ctrls: Vec<Shot> = (0..4).map(|_| shot(&base)).collect();
        let (v, rep) = judge(&ctrls, &[shot(&base)], &cfg());
        assert_eq!(v, IqVerdict::WithinRef, "{rep}");
        let (v, rep) = judge(&ctrls, &[shot(&half_res(&base))], &cfg());
        match v {
            IqVerdict::BeyondRef(r) => assert!(r.contains("变糊"), "{r}"),
            _ => panic!("半分辨率放大必须落在参考线外: {rep}"),
        }
    }

    /// 静止场景里 +2 灰阶: SSIM 看不出, PSNR 必须抓到。
    #[test]
    fn brightness_offset_is_caught_by_psnr_not_ssim() {
        let base = scene(2);
        let ctrls: Vec<Shot> = (0..4).map(|_| shot(&base)).collect();
        let bright: Vec<u8> = base.iter().map(|v| v.saturating_add(2)).collect();
        let (v, rep) = judge(&ctrls, &[shot(&bright)], &cfg());
        assert!(matches!(v, IqVerdict::BeyondRef(_)), "{rep}");
        let psnr = rep["metrics"].as_array().unwrap().iter().find(|r| r["key"] == "stable_psnr_db").unwrap().clone();
        assert!(psnr["to_ref_line"].as_f64().unwrap() < 0.0, "{rep}");
        assert!(rep["beyond_ref_line"].as_array().unwrap().contains(&json!("稳定块 PSNR")), "{rep}");
        assert!(rep.get("verdict").is_none(), "输出不再有通过与否: {rep}");
        assert!(rep["sheet"].as_str().unwrap().contains("参考线外"), "{rep}");
    }

    /// 场景里有个物体在动: 空改动应在参考线内 (运动块被排掉), 半分辨率应在参考线外。
    /// 这正是全帧 PSNR 做不到的: 两者全帧 PSNR 都被运动主导。
    #[test]
    fn moving_object_is_masked_out() {
        let base = scene(3);
        let at = |x: usize| shot(&with_object(base.clone(), x, 70));
        // 对照: 物体在 20 / 60 / 100 / 140, 候选夹在中间 (80)
        let ctrls = vec![at(20), at(60), at(100), at(140)];
        let (v, rep) = judge(&ctrls, &[at(80)], &cfg());
        assert_eq!(v, IqVerdict::WithinRef, "{rep}");
        assert!(rep["tiles"]["stable"].as_u64().unwrap() < 48, "运动块要被排掉: {rep}");
        let (v, rep) = judge(&ctrls, &[shot(&half_res(&with_object(base.clone(), 80, 70)))], &cfg());
        assert!(matches!(v, IqVerdict::BeyondRef(_)), "{rep}");
    }

    /// 整个画面都在变: 稳定块判据算不了, 不能因此当成在参考线内。
    #[test]
    fn whole_frame_moving_is_inconclusive_not_pass() {
        let ctrls: Vec<Shot> = (10..14).map(|s| shot(&scene(s))).collect();
        let (v, rep) = judge(&ctrls, &[shot(&scene(20))], &cfg());
        match v {
            IqVerdict::NotComputed(r) => assert!(r.contains("稳定块只有"), "{r}"),
            _ => panic!("整帧都在动必须算不了: {rep}"),
        }
    }

    /// 对照不足 3 张: 稳定块判据不算; 只有 1 张对照: 直接算不了。
    #[test]
    fn too_few_controls() {
        let base = scene(4);
        let (v, _) = judge(&[shot(&base)], &[shot(&base)], &cfg());
        assert!(matches!(v, IqVerdict::NotComputed(_)));
        let (v, rep) = judge(&[shot(&base), shot(&base)], &[shot(&base)], &cfg());
        match v {
            IqVerdict::NotComputed(r) => assert!(r.contains("2 张"), "{r}"),
            _ => panic!("{rep}"),
        }
        // 但细节判据照算: 2 张对照 + 半分辨率候选 → 仍在参考线外
        let (v, rep) = judge(&[shot(&base), shot(&base)], &[shot(&half_res(&base))], &cfg());
        assert!(matches!(v, IqVerdict::BeyondRef(_)), "{rep}");
    }

    /// 锯齿 (隔点取样, 关抗锯齿的样子) 让细节能量变大, 也要抓到。
    #[test]
    fn aliasing_raises_detail_energy() {
        // 先做一张「4x 超采样后解析」的对照 (2×2 平均), 候选为每像素只取 1 个采样
        let hi = scene(5);
        let resolved = half_res(&hi);
        let mut aliased = hi.clone();
        for y in (0..H).step_by(2) {
            for x in (0..W).step_by(2) {
                for c in 0..3 {
                    let v = hi[(y * W + x) * 3 + c];
                    for (yy, xx) in [(y, x), (y, x + 1), (y + 1, x), (y + 1, x + 1)] {
                        aliased[(yy * W + xx) * 3 + c] = v;
                    }
                }
            }
        }
        let ctrls: Vec<Shot> = (0..4).map(|_| shot(&resolved)).collect();
        let (v, rep) = judge(&ctrls, &[shot(&aliased)], &cfg());
        assert!(matches!(v, IqVerdict::BeyondRef(_)), "{rep}");
        let det = rep["metrics"].as_array().unwrap().iter().find(|r| r["key"] == "detail_log2").unwrap().clone();
        assert!(det["value"].as_f64().unwrap() > 0.0, "{rep}");
    }

    #[test]
    fn exclude_rect_drops_tiles() {
        let base = scene(6);
        let ctrls: Vec<Shot> = (0..4).map(|_| shot(&base)).collect();
        let mut c = cfg();
        c.exclude.push((0, 0, 64, 64)); // 2×2 块
        let (v, rep) = judge(&ctrls, &[shot(&base)], &c);
        assert_eq!(v, IqVerdict::WithinRef);
        assert_eq!(rep["tiles"]["usable"], json!(44));
        // UI 区域里的变化不影响判定
        let mut ui = base.clone();
        for y in 0..60 {
            for x in 0..60 {
                ui[(y * W + x) * 3] = 0;
            }
        }
        let (v, rep) = judge(&ctrls, &[shot(&ui)], &c);
        assert_eq!(v, IqVerdict::WithinRef, "{rep}");
    }

    #[test]
    fn config_reads_request_overrides() {
        let c = IqConfig::from_request(&json!({"tile": 16, "stable_db": 40, "exclude": [[0, 0, 100, 50], [5, 5, 1, 1]]}));
        assert_eq!(c.tile, 16);
        assert_eq!(c.stable_db, 40.0);
        assert_eq!(c.exclude, vec![(0, 0, 100, 50)]);
        assert_eq!(IqConfig::from_request(&json!({})).tile, 32);
    }

    #[test]
    fn size_mismatch_is_inconclusive() {
        let a = shot(&scene(7));
        let b = Shot::from_rgb(W / 2, H, &vec![0u8; W / 2 * H * 3], 32).unwrap();
        let (v, _) = judge(&[shot(&scene(7)), shot(&scene(7)), a], &[b], &cfg());
        assert!(matches!(v, IqVerdict::NotComputed(_)));
    }
}
