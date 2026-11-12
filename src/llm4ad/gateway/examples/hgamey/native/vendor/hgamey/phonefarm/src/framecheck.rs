//! 「画面到底动没动」的两个判据。
//!
//! 由 `loop_v1/auto/spin_check.py` 与 `loop_v1/tools/frames_moving.py` 逐行搬过来,
//! 输出字节一致。两者都是纯函数: 输入两张裸帧的字节, 输出一个数, 不碰设备。
//!
//! 为什么需要它们
//! --------------
//! 原神 7.1.0 之后手柄注入不再被游戏接受。失效是**安静的**: `phonefarm script`
//! 照常跑完、退出码正常、ftrace 照样有帧时序 —— 只是画面根本没动, 采到的是静止场景。
//! 这种数据看起来完全正常, 混进 A/B 比较里没有任何一处会报错。
//!
//! 所以负载不能只看"脚本跑完了", 要看**画面真的在变**。
//!
//! 两个统计量, 一个门槛一个参考
//! ----------------------------
//! [`verdict`] 报「变化像素占抽样点的比例」, 带 20% 门槛, 进 report.json, 由 autoloop 用;
//! [`avg_pixel_diff_pct`] 报「平均逐像素绝对差 (%)」, 是给人手查一对帧的 CLI。
//! 两个量不同但互补 —— 前者对变化**范围**敏感, 后者对变化**幅度**敏感 —— 且对同一对
//! 裸帧结论一致: `fixtures/golden/runs_sysparam/genshin1/spin_check/spin_{a,b}.raw` 上分别是
//! 81.77% 与 13.637%, 都远在各自门槛之上。要改判据请两个一起改。
//!
//! `screencap` 原始格式
//! --------------------
//! `adb exec-out screencap` (不带 `-p`) 给的是「小端 32 位宽、高、格式 [, 色彩空间]」
//! 的头 + RGBA 像素。头长度各版本不一样 (12 或 16 字节), 所以不写死 ——
//! 用 `len(data) - w*h*4` 反推, 对不上就如实报错, 不瞎猜。

use crate::pyjson::{py_round, PyVal};
use crate::pyobj;

/// 单通道差值超过这个数才算「这个像素变了」—— 滤掉编码噪声与极轻微的明暗浮动
const CHANNEL_DELTA: i32 = 16;
/// 变化像素占比超过这个数才算「视角在转」。门槛定在 20%: 离两边都远, 不需要精调。
const MOVED_FRAC_GATE: f64 = 0.20;
/// 采样步长 (每 N 个像素看一个)。平移是全画面性质的, 均匀抽样完全够用。
const SAMPLE_STRIDE: usize = 64;
/// 抽样点数的下限。帧太小时自动把步长压下来, 免得退化成只看几个像素。
const MIN_SAMPLES: usize = 1024;

/// 原始 screencap → (宽, 高, RGBA 像素起点)。头长度反推, 不写死。
pub fn parse_screencap(data: &[u8]) -> Result<(u32, u32, &[u8]), String> {
    if data.len() < 16 {
        return Err(format!("screencap 数据太短 ({} 字节)", data.len()));
    }
    let w = u32::from_le_bytes(data[0..4].try_into().unwrap());
    let h = u32::from_le_bytes(data[4..8].try_into().unwrap());
    if !(0 < w && w <= 20000 && 0 < h && h <= 20000) {
        return Err(format!("screencap 头不合理: w={w} h={h}"));
    }
    let want = w as i64 * h as i64 * 4;
    let head = data.len() as i64 - want;
    if head != 12 && head != 16 {
        return Err(format!(
            "screencap 头长度算出来是 {head} 字节 (只认 12/16): w={w} h={h} 数据 {} 字节",
            data.len()
        ));
    }
    Ok((w, h, &data[head as usize..]))
}

/// 两帧之间「变了的像素」占抽样点的比例。返回 (比例, 抽样点数)。
pub fn moved_fraction(a: &[u8], b: &[u8]) -> Result<(f64, usize), String> {
    let (wa, ha, pa) = parse_screencap(a)?;
    let (wb, hb, pb) = parse_screencap(b)?;
    if (wa, ha) != (wb, hb) {
        return Err(format!("两帧尺寸不同: {wa}x{ha} vs {wb}x{hb}"));
    }
    let n = pa.len().min(pb.len()) / 4;
    if n == 0 {
        return Err("帧里一个像素都没有".into());
    }
    // 步长要保证抽到足够多的点。固定步长 64 在 1216x2688 上有 5 万个样本, 绰绰有余;
    // 但帧一小 (测试用的小图, 或某些设备的缩略帧) 就会退化成只抽到个位数个点,
    // 那时一个像素变了就等于 100% 变了。所以按帧大小夹一下, 至少抽 1024 个点。
    let stride = SAMPLE_STRIDE.min(n / MIN_SAMPLES).max(1);
    let mut moved = 0usize;
    let mut total = 0usize;
    let mut i = 0usize;
    while i < n {
        let o = i * 4;
        total += 1;
        // 只看 RGB, 不看 A —— 不透明画面的 A 恒为 255, 带不进任何信息
        if (0..3).any(|c| (pa[o + c] as i32 - pb[o + c] as i32).abs() > CHANNEL_DELTA) {
            moved += 1;
        }
        i += stride;
    }
    Ok((
        if total > 0 {
            moved as f64 / total as f64
        } else {
            0.0
        },
        total,
    ))
}

/// 两帧 → 「视角是否在转」的结论。
pub fn verdict(a: &[u8], b: &[u8]) -> PyVal {
    let (frac, total) = match moved_fraction(a, b) {
        Ok(v) => v,
        Err(e) => {
            return pyobj! { "spinning" => false, "error" => e, "gate" => MOVED_FRAC_GATE }
        }
    };
    pyobj! {
        "spinning" => frac >= MOVED_FRAC_GATE,
        "moved_fraction" => py_round(frac, 4),
        "gate" => MOVED_FRAC_GATE,
        "sampled_pixels" => total,
        "note" => if frac >= MOVED_FRAC_GATE {
            "画面在动, 负载生效"
        } else {
            "两帧几乎一样 —— 负载没有真的转视角。原神 7.1.0 之后手柄注入已失效且失效是安静的, \
请确认用的是触控版负载, 且游戏在大世界探索态"
        },
    }
}

/// 帧文件读不出来时的结论。形状与 [`verdict`] 的错误分支一致 —— 上游靠
/// `spinning: false` + `error` 落证据后退出, 不靠异常。
pub fn read_error(msg: &str) -> PyVal {
    pyobj! { "spinning" => false, "error" => msg, "gate" => MOVED_FRAC_GATE }
}

// ══════════════ 平均逐像素差 (CLI) ══════════════

/// `frames_moving.py` 的头解析: 比 [`parse_screencap`] 宽松, 头长度不限 12/16。
/// 两边故意保持各自原样 —— 这个是给人手查的, 那个是进判定的。
fn load_loose(path: &str) -> Result<(u32, u32, Vec<u8>), String> {
    let b = std::fs::read(path).map_err(|e| format!("{path}: {e}"))?;
    if b.len() < 16 {
        return Err(format!("{path}: 太短, 不像一张裸帧"));
    }
    let w = u32::from_le_bytes(b[0..4].try_into().unwrap());
    let h = u32::from_le_bytes(b[4..8].try_into().unwrap());
    if !(0 < w && w < 20000 && 0 < h && h < 20000) {
        return Err(format!("{path}: 读出的宽高不合理 {w}x{h}"));
    }
    let head = b.len() as i64 - w as i64 * h as i64 * 4;
    if head < 0 {
        return Err(format!(
            "{path}: 字节数 {} 装不下 {w}x{h} 的 RGBA",
            b.len()
        ));
    }
    let px = b[head as usize..].to_vec();
    Ok((w, h, px))
}

/// 两张裸帧的平均逐像素绝对差 (%)。
///
/// 每隔 997 个像素取一个 (质数步长, 避开与屏幕宽度成整除关系导致只采到某几列)。
pub fn avg_pixel_diff_pct(p1: &[u8], p2: &[u8]) -> Result<f64, String> {
    let step = 997 * 4;
    let mut total: i64 = 0;
    let mut n: i64 = 0;
    let end = p1.len().min(p2.len());
    if end >= 4 {
        let mut i = 0usize;
        while i < end - 4 {
            for c in 0..3 {
                total += (p1[i + c] as i64 - p2[i + c] as i64).abs();
            }
            n += 3;
            i += step;
        }
    }
    if n == 0 {
        return Err("没采到任何像素".into());
    }
    Ok(total as f64 / n as f64 / 255.0 * 100.0)
}

const USAGE: &str = "用法: phonefarm frames-moving <raw1> <raw2>";

pub fn run_frames_moving(args: &[String]) -> i32 {
    let files: Vec<&String> = args.iter().filter(|a| !a.starts_with("--")).collect();
    if files.len() < 2 {
        eprintln!("{USAGE}");
        return 2;
    }
    let (a, b) = match (load_loose(files[0]), load_loose(files[1])) {
        (Ok(a), Ok(b)) => (a, b),
        (Err(e), _) | (_, Err(e)) => {
            eprintln!("{e}");
            return 1;
        }
    };
    if (a.0, a.1) != (b.0, b.1) {
        eprintln!("两帧尺寸不同: {}x{} vs {}x{}", a.0, a.1, b.0, b.1);
        return 1;
    }
    match avg_pixel_diff_pct(&a.2, &b.2) {
        Ok(pct) => {
            println!("{pct:.3}");
            0
        }
        Err(e) => {
            eprintln!("{e}");
            1
        }
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

    const W: usize = 64;
    const H: usize = 64;

    fn head(w: u32, h: u32) -> Vec<u8> {
        let mut v = Vec::new();
        v.extend_from_slice(&w.to_le_bytes());
        v.extend_from_slice(&h.to_le_bytes());
        v.extend_from_slice(&1u32.to_le_bytes());
        v
    }

    fn solid(r: u8, g: u8, b: u8) -> Vec<u8> {
        let mut v = head(W as u32, H as u32);
        v.extend(std::iter::repeat_n([r, g, b, 255], W * H).flatten());
        v
    }

    fn spinning(v: &PyVal) -> bool {
        matches!(v.get("spinning"), Some(PyVal::Bool(true)))
    }

    #[test]
    fn identical_frames_are_not_spinning() {
        let f = solid(10, 20, 30);
        let v = verdict(&f, &f);
        assert!(!spinning(&v));
        assert_eq!(v.get("moved_fraction").unwrap().as_f64(), Some(0.0));
        assert!(matches!(v.get("note"), Some(PyVal::Str(s)) if s.contains("7.1.0")));
    }

    #[test]
    fn whole_frame_change_is_spinning() {
        let v = verdict(&solid(10, 20, 30), &solid(200, 210, 220));
        assert!(spinning(&v));
        assert_eq!(v.get("moved_fraction").unwrap().as_f64(), Some(1.0));
    }

    /// 静止场景里草和 UI 时钟也会动几个像素 —— 那不算视角在转。
    #[test]
    fn tiny_animation_does_not_count_as_spinning() {
        let a = solid(10, 20, 30);
        let mut b = a.clone();
        for i in 0..8 {
            // 4096 个像素里只有 8 个大变 (0.2%)
            b[12 + i * 4..12 + i * 4 + 4].copy_from_slice(&[250, 250, 250, 255]);
        }
        assert!(!spinning(&verdict(&a, &b)));
    }

    /// 编码噪声级别的浮动 (每通道 <= 16) 不算变化。
    #[test]
    fn sub_threshold_noise_is_ignored() {
        let v = verdict(&solid(100, 100, 100), &solid(110, 108, 112));
        assert_eq!(v.get("moved_fraction").unwrap().as_f64(), Some(0.0));
    }

    /// 读不懂的帧一律判「没在转」—— 宁可白停一次, 不要放过静止数据。
    #[test]
    fn malformed_capture_reports_error_not_a_pass() {
        let v = verdict(b"", b"");
        assert!(!spinning(&v));
        assert!(v.get("error").is_some());
    }

    /// 头 12 / 16 字节两种都要认, 对不上就报错而不是瞎猜。
    #[test]
    fn header_length_is_derived_not_assumed() {
        let px: Vec<u8> = std::iter::repeat_n([1u8, 2, 3, 255], W * H).flatten().collect();
        let mut with16 = head(W as u32, H as u32);
        with16.extend_from_slice(&0u32.to_le_bytes());
        with16.extend_from_slice(&px);
        let (w, h, p) = parse_screencap(&with16).unwrap();
        assert_eq!((w as usize, h as usize, p.len()), (W, H, px.len()));

        let mut truncated = head(W as u32, H as u32);
        truncated.extend_from_slice(&px[..px.len() - 8]);
        assert!(parse_screencap(&truncated).is_err());
    }

    /// 帧比固定步长还小时要自动加密抽样, 否则一个像素变了就等于 100% 变了。
    #[test]
    fn small_frames_do_not_degenerate_to_one_sample() {
        let (w, h) = (16u32, 16u32);
        let mut a = head(w, h);
        a.extend(std::iter::repeat_n([10u8, 20, 30, 255], 256).flatten());
        let mut b = a.clone();
        b[12..16].copy_from_slice(&[250, 250, 250, 255]); // 256 个里只变 1 个
        let (frac, total) = moved_fraction(&a, &b).unwrap();
        assert_eq!(total, 256); // 全采, 而不是只采 4 个
        assert!((frac - 1.0 / 256.0).abs() < 1e-9);
    }

    /// 对照源: 第一次原神实跑抓的两张真帧 (1216x2688), 与当时旧 Python 版落的
    /// spin_check.json 逐字节比。两个统计量的数也一并钉住 —— 模块注释引的就是它们。
    #[test]
    fn real_frames_match_recorded_golden() {
        let d = repo_root().join("loop_v1/fixtures/golden/runs_sysparam/genshin1/spin_check");
        let (a, b) = (d.join("spin_a.raw"), d.join("spin_b.raw"));
        if !a.exists() || !b.exists() {
            return;
        }
        let (ra, rb) = (std::fs::read(&a).unwrap(), std::fs::read(&b).unwrap());
        let want = std::fs::read_to_string(d.join("spin_check.json")).unwrap();
        assert_eq!(dumps(&verdict(&ra, &rb)), want.trim_end_matches('\n'));

        // 另一条通路 (frames_moving) 对同一对帧: 13.637%
        let (_, _, pa) = parse_screencap(&ra).unwrap();
        let (_, _, pb) = parse_screencap(&rb).unwrap();
        assert_eq!(format!("{:.3}", avg_pixel_diff_pct(pa, pb).unwrap()), "13.637");
    }

    #[test]
    fn avg_diff_needs_at_least_one_sample() {
        assert!(avg_pixel_diff_pct(&[], &[]).is_err());
        assert_eq!(avg_pixel_diff_pct(&[0, 0, 0, 255, 0], &[255, 255, 255, 255, 0]).unwrap(), 100.0);
    }
}
