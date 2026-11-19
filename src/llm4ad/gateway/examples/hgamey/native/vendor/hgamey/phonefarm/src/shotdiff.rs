//! copyprobe 验收 ②: 截帧逐像素比对 (knobs/gray/compare_shots.py 的移植)。
//!
//! 判读逻辑: 场景永远有微小动画 (待机动作/草摆动), 所以"逐像素不变"的口径是
//! probe vs none 的差异 ≈ none vs none 的差异 (控制对基线), 而不是字面 0。
//! 差异显著大于控制对才算探针改了画面。UID 区域 (右下角) 与屏幕边缘一律排除。

use crate::pyjson::{dumps_indent, py_round, PyVal};
use std::path::Path;

/// 任一通道差 > 8 记为"不同像素"
const THRESH: i32 = 8;

struct Raw {
    w: usize,
    h: usize,
    px: Vec<u8>,
}

fn load_raw(p: &Path) -> Result<Raw, String> {
    let d = std::fs::read(p).map_err(|e| format!("{}: {e}", p.display()))?;
    if d.len() < 12 {
        return Err(format!("{}: 太短, 不像 screencap 裸帧", p.display()));
    }
    let w = u32::from_le_bytes(d[0..4].try_into().unwrap()) as usize;
    let h = u32::from_le_bytes(d[4..8].try_into().unwrap()) as usize;
    let fmt = u32::from_le_bytes(d[8..12].try_into().unwrap());
    if fmt != 1 {
        return Err(format!("{}: 不是 RGBA_8888 (fmt={fmt})", p.display()));
    }
    let size = w.checked_mul(h).and_then(|v| v.checked_mul(4)).and_then(|v| v.checked_add(12));
    if w == 0 || h == 0 || size != Some(d.len()) {
        return Err(format!("{}: 长度不符", p.display()));
    }
    let px = d[12..].to_vec();
    Ok(Raw { w, h, px })
}

/// [x0,y0,x1,y1) 区域内差异像素占比; excl 是要排除的子矩形列表。
fn diff(a: &[u8], b: &[u8], (x0, y0, x1, y1): (usize, usize, usize, usize), w: usize, excl: &[(usize, usize, usize, usize)]) -> f64 {
    if w == 0 { return 0.0; }
    let h = a.len().min(b.len()) / 4 / w;
    let mut total = 0usize;
    let mut diffn = 0usize;
    for y in y0.min(h)..y1.min(h) {
        let row = y * w * 4;
        for x in x0.min(w)..x1.min(w) {
            if excl.iter().any(|(ex0, ey0, ex1, ey1)| *ex0 <= x && x < *ex1 && *ey0 <= y && y < *ey1) {
                continue;
            }
            let o = row + x * 4;
            if (0..3).any(|c| (a[o + c] as i32 - b[o + c] as i32).abs() > THRESH) {
                diffn += 1;
            }
            total += 1;
        }
    }
    if total > 0 { diffn as f64 / total as f64 } else { 0.0 }
}

pub fn report(run: &Path) -> Result<String, String> {
    let mut arms: Vec<String> = std::fs::read_dir(run)
        .map_err(|e| format!("{}: {e}", run.display()))?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.is_dir() && p.join("shot2.raw").exists())
        .filter_map(|p| p.file_name().map(|n| n.to_string_lossy().into_owned()))
        .collect();
    arms.sort();
    if arms.len() < 2 {
        return Err(format!("{} 里可用臂不足: {arms:?}", run.display()));
    }
    let mut imgs: Vec<(String, Vec<u8>)> = Vec::new();
    let mut w = 0usize;
    let mut h = 0usize;
    for a in &arms {
        let r = load_raw(&run.join(a).join("shot2.raw"))?;
        if w != 0 && (w != r.w || h != r.h) {
            return Err("各臂截图尺寸不同; 不做隐式缩放".into());
        }
        w = r.w; h = r.h;
        imgs.push((a.clone(), r.px));
    }
    let mut l = vec![format!("臂: {arms:?}  分辨率: {w}x{h}")];

    // 区域 (按 3200x1440 全屏; UID 在右下角 —— 按比例换算)
    let (sx, sy) = (w as f64 / 3200.0, h as f64 / 1440.0);
    let rr = |a: f64, b: f64, c: f64, d: f64| -> (usize, usize, usize, usize) {
        ((a * sx) as usize, (b * sy) as usize, (c * sx) as usize, (d * sy) as usize)
    };
    let uid = rr(2600.0, 1330.0, 3200.0, 1440.0);
    type Region = (&'static str, (usize, usize, usize, usize), bool);
    let regions: Vec<Region> = vec![
        ("全屏(除UID)", (0, 0, w, h), true),
        ("左上小地图", rr(60.0, 60.0, 500.0, 330.0), false),
        ("右上图标条", rr(2300.0, 30.0, 3180.0, 140.0), false),
        ("右下技能区", rr(2100.0, 700.0, 3180.0, 1330.0), false),
        ("中央3D场景", rr(800.0, 400.0, 2400.0, 1000.0), false),
    ];

    let none: Vec<&String> = arms.iter().filter(|a| a.starts_with("none")).collect();
    let probe: Vec<&String> = arms.iter().filter(|a| a.starts_with("probe")).collect();
    type Pair = (String, String, String);
    let mut pairs: Vec<Pair> = Vec::new();
    if none.len() >= 2 {
        pairs.push(("控制对 none1~none2".into(), none[0].clone(), none[1].clone()));
    }
    for p in &probe {
        if let Some(n) = none.first() {
            pairs.push((format!("{p}~{n}"), (*p).clone(), (*n).clone()));
        }
    }
    let mut report_obj: Vec<(String, PyVal)> = Vec::new();
    for (label, a, b) in &pairs {
        let pa = &imgs.iter().find(|(k, _)| k == a).unwrap().1;
        let pb = &imgs.iter().find(|(k, _)| k == b).unwrap().1;
        let mut row: Vec<(String, PyVal)> = Vec::new();
        let mut cells: Vec<String> = Vec::new();
        for (rname, rect, excl_full) in &regions {
            let excl: &[(usize, usize, usize, usize)] = if *excl_full { std::slice::from_ref(&uid) } else { &[] };
            let v = py_round(diff(pa, pb, *rect, w, excl) * 100.0, 3);
            row.push((rname.to_string(), PyVal::Float(v)));
            cells.push(format!("{rname}={v}%"));
        }
        report_obj.push((label.clone(), PyVal::Obj(row)));
        l.push(format!("{label}: {}", cells.join("  ")));
    }
    let out_path = run.join("shot_diff.json");
    std::fs::write(&out_path, dumps_indent(&PyVal::Obj(report_obj), 2)).map_err(|e| e.to_string())?;
    l.push(format!("落盘: {}", out_path.display()));
    Ok(l.join("\n"))
}

pub fn run_shot_diff(args: &[String]) -> i32 {
    let Some(d) = args.iter().find(|a| !a.starts_with("--")) else {
        eprintln!("用法: phonefarm shot-diff <run目录>");
        return 2;
    };
    match report(Path::new(d)) {
        Ok(t) => {
            println!("{t}");
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

    fn raw(w: usize, h: usize, fill: impl Fn(usize, usize) -> [u8; 3]) -> Vec<u8> {
        let mut d = Vec::new();
        d.extend_from_slice(&(w as u32).to_le_bytes());
        d.extend_from_slice(&(h as u32).to_le_bytes());
        d.extend_from_slice(&1u32.to_le_bytes());
        for y in 0..h {
            for x in 0..w {
                let [r, g, b] = fill(x, y);
                d.extend_from_slice(&[r, g, b, 255]);
            }
        }
        d
    }

    #[test]
    fn diff_counts_only_pixels_above_threshold() {
        let a = vec![100u8; 12];
        // 每通道 +8 以内 = 噪声, 不算变化
        let b = vec![108u8; 12];
        assert_eq!(diff(&a, &b, (0, 0, 1, 1), 1, &[]), 0.0);
        // +9 就算
        let c = vec![109u8; 12];
        assert_eq!(diff(&a, &c, (0, 0, 1, 1), 1, &[]), 1.0);
    }

    #[test]
    fn excluded_regions_are_skipped() {
        let mut a = vec![100u8; 2 * 4];
        let mut b = a.clone();
        // 两个像素都变了, 但排除掉第二个
        a[4] = 200;
        b[4] = 0;
        a[0] = 200;
        b[0] = 0;
        let v = diff(&a, &b, (0, 0, 2, 1), 2, &[(1, 0, 2, 1)]);
        assert_eq!(v, 1.0); // 只有第一个像素参与
    }

    #[test]
    fn wrong_format_is_rejected() {
        let mut d = raw(2, 2, |_, _| [1, 2, 3]);
        d[8..12].copy_from_slice(&2u32.to_le_bytes()); // 不是 RGBA_8888
        let r = std::env::temp_dir().join("pf-shot-bad.raw");
        std::fs::write(&r, &d).unwrap();
        assert!(load_raw(&r).is_err());
        let _ = std::fs::remove_file(&r);
    }

    /// 端到端: 两臂 (none 控制对 + probe), probe 差异显著大于控制对才算改了画面。
    #[test]
    fn report_ranks_probe_against_control() {
        let d = std::env::temp_dir().join(format!("pf-shot-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        let base = raw(4, 4, |_, _| [50, 50, 50]);
        for name in ["none1", "none2", "probe1"] {
            let dir = d.join(name);
            std::fs::create_dir_all(&dir).unwrap();
            let mut px = base.clone();
            // probe 臂全画面不同; none 控制对只有轻微噪声
            if name == "probe1" {
                for v in px.iter_mut().skip(12).step_by(4) {
                    *v = 200;
                }
            } else {
                for v in px.iter_mut().skip(12).step_by(4) {
                    *v = 52;
                }
            }
            std::fs::write(dir.join("shot2.raw"), &px).unwrap();
        }
        let out = report(&d).unwrap();
        assert!(out.contains("控制对 none1~none2"), "{out}");
        let j: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(d.join("shot_diff.json")).unwrap()).unwrap();
        let ctrl = j["控制对 none1~none2"]["全屏(除UID)"].as_f64().unwrap();
        let probe = j["probe1~none1"]["全屏(除UID)"].as_f64().unwrap();
        assert!(probe > ctrl * 10.0, "probe {probe} 应远大于控制对 {ctrl}");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn landscape_and_portrait_are_safe() {
        for (w, h) in [(8, 3), (3, 8)] {
            let root = std::env::temp_dir().join(format!("pf-shot-dims-{}-{w}-{h}", std::process::id()));
            for name in ["none1", "none2", "probe1"] {
                let dir = root.join(name);
                std::fs::create_dir_all(&dir).unwrap();
                std::fs::write(dir.join("shot2.raw"), raw(w, h, |_, _| [40, 40, 40])).unwrap();
            }
            assert!(report(&root).unwrap().contains(&format!("{w}x{h}")));
            let p = root.join("none1/shot2.raw");
            std::fs::write(&p, raw(w, h, |_, _| [40, 40, 40])[..13].to_vec()).unwrap();
            assert!(load_raw(&p).is_err());
            std::fs::remove_dir_all(root).unwrap();
        }
    }
}
