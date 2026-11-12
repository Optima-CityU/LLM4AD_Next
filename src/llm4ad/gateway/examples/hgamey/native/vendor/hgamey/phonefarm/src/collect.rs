//! 有界滚动采集 — 组合动作原语, 零 Token。
//!
//! 语义移植自 iPhone-use 的 `pua_scroll_find` / `pua_collect_list`
//! (https://github.com/zhongerxin/iPhone-use, MIT, © 2026 ZHONG XIN, commit 08ed5b0)。
//! **只借语义, 不搬代码** —— 实现与注释均为本仓库重写; 来源登记见 THIRD_PARTY_NOTICES.md,
//! 契约见 https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_OBSERVATION_BUDGET.md。
//!
//! 三件事:
//!   1. **有界滚动**: 一次调用最多滑 max_swipes 屏, 某一屏不再产生新内容立即停, 绝不盲滑;
//!   2. **重叠读取 + 去重**: 相邻两屏必然有重叠条目, 按文字全等去重, 不重复记账;
//!   3. **覆盖边界如实回报**: 停在预算还是停在列表末尾, 说清楚 —— 不假装采完了。
//!
//! 与 VLM 通路的区别: 这条路不调用任何模型, 产出的是**清单**, 不是**结论**。
//! 它不做判定、不写经验库、不碰判定口径 —— 那仍然只归 `loopstat` 那一侧。

use crate::device::Device;
use crate::fold::{fold, Line};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::time::Duration;

/// 有界: 默认最多滑 12 屏(一屏大约 8~12 个条目, 够覆盖常规列表)
const DEFAULT_MAX_SWIPES: u32 = 12;
/// 每滑一屏后的稳定等待: 列表惯性滚动没停就读, 会把半张卡片记成两屏
const DEFAULT_SETTLE_MS: u64 = 600;
/// 清单条目上限: 防止病态页面把内存和 stdout 撑爆
const DEFAULT_MAX_ITEMS: usize = 400;
/// 元素树读取上限(与 runtime 的采集节奏对齐: 正常 <1s, 8s 是坏设备兜底)
const ELS_TIMEOUT_MS: u64 = 8_000;
/// 单屏入账行数上限(折叠投影本已很紧, 这是病态树的保险丝)
const FRAME_LINE_CAP: usize = 200;

fn usage() -> &'static str {
    "用法: phonefarm collect [--serial <设备>] [--query \"目标文字\"] [--max-swipes N]\n\
     \x20                [--max-items N] [--settle-ms MS] [--json]\n\
     \x20 --query: 给定时为「滚动查找」——命中即停并回报该条目位置; 不给为「列表采集」\n\
     \x20 --max-swipes: 有界上限, 默认 12(超出即如实标记未覆盖, 不假装采完)\n\
     \x20 零 Token: 全程不调用任何模型"
}

#[derive(Default, Debug)]
struct Opts {
    serial: Option<String>,
    query: Option<String>,
    max_swipes: u32,
    max_items: usize,
    settle_ms: u64,
    json: bool,
}

fn parse_opts(args: &[String]) -> Result<Opts, String> {
    let mut o = Opts {
        max_swipes: DEFAULT_MAX_SWIPES,
        max_items: DEFAULT_MAX_ITEMS,
        settle_ms: DEFAULT_SETTLE_MS,
        ..Default::default()
    };
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--serial" => o.serial = it.next().cloned(),
            "--query" => {
                o.query = it.next().cloned().filter(|s| !s.trim().is_empty());
            }
            "--max-swipes" => {
                o.max_swipes = it
                    .next()
                    .and_then(|v| v.parse().ok())
                    .ok_or("--max-swipes 需要一个整数")?;
            }
            "--max-items" => {
                o.max_items = it
                    .next()
                    .and_then(|v| v.parse().ok())
                    .ok_or("--max-items 需要一个整数")?;
            }
            "--settle-ms" => {
                o.settle_ms = it
                    .next()
                    .and_then(|v| v.parse().ok())
                    .ok_or("--settle-ms 需要一个毫秒数")?;
            }
            "--json" => o.json = true,
            "-h" | "--help" => return Err(String::new()),
            other => return Err(format!("未知参数 {other}")),
        }
    }
    Ok(o)
}

/// 一屏入账(纯函数, 单测钉契约)。
///
/// 返回 `(本次新增条数, 命中查询时的 (文字, 框))`。
/// - 去重键 = trim 后的文字全等; 空文字不入账。
/// - 命中查询时**先于去重**判定: 目标文字已经见过也要回报命中位置。
/// - 超出 max_items 后仍继续去重计数(新增条数照实), 只是不再入 items。
fn ingest(
    lines: &[Line],
    seen: &mut HashSet<String>,
    items: &mut Vec<Value>,
    query: Option<&str>,
    frame: u32,
    swipe: u32,
    max_items: usize,
) -> (usize, Option<Value>) {
    let mut produced = 0usize;
    for l in lines.iter().take(FRAME_LINE_CAP) {
        let t = l.t.trim();
        if t.is_empty() {
            continue;
        }
        if let Some(q) = query {
            if t.contains(q) {
                return (
                    produced,
                    Some(json!({"text": t, "box": l.b, "frame": frame, "swipe": swipe})),
                );
            }
        }
        if seen.insert(t.to_string()) {
            produced += 1;
            if items.len() < max_items {
                items.push(json!({"text": t, "box": l.b, "frame": frame}));
            }
        }
    }
    (produced, None)
}

/// 元素树不可用时的退回源: els 的文字清单(与折叠投影同一入账通道)
fn lines_from_els(els: &[crate::device::Node]) -> Vec<Line> {
    els.iter()
        .filter(|n| !n.t.trim().is_empty())
        .map(|n| Line {
            t: n.t.clone(),
            b: n.b,
            kind: crate::fold::Kind::Plain,
        })
        .collect()
}

pub fn run_collect(args: &[String]) -> i32 {
    let opts = match parse_opts(args) {
        Ok(o) => o,
        Err(e) => {
            if !e.is_empty() {
                eprintln!("{e}");
            }
            eprintln!("{}", usage());
            return 2;
        }
    };

    let tmp = std::env::var("PHONEFARM_TMP")
        .unwrap_or_else(|_| "/tmp/phonefarm".to_string());
    let _ = std::fs::create_dir_all(&tmp);
    let phone = Device::new(opts.serial.clone(), tmp);
    let (w, h) = phone.size();

    let mut seen: HashSet<String> = HashSet::new();
    let mut items: Vec<Value> = Vec::new();
    let mut frames = 0u32;
    let mut swipes = 0u32;
    let mut hit: Option<Value> = None;
    let mut pkg = String::new();

    // 停止原因由循环自己交出来: 每条出口都显式说明为什么停 —— 覆盖边界不许含糊。
    let stop = 'scan: loop {
        let (els, full, fg, _xml) = phone.els_full(ELS_TIMEOUT_MS);
        frames += 1;
        if !fg.is_empty() {
            pkg = fg;
        }
        // 折叠投影优先(零模型参与的确定性投影); 全量层为空时退回 els 清单。
        // 注意: 折叠投影**永不藏交互把手**, 所以采集到的清单与模型看到的骨架一致。
        let mut lines = fold(&full);
        if lines.is_empty() {
            lines = lines_from_els(&els);
        }

        let (produced, found_hit) = ingest(
            &lines,
            &mut seen,
            &mut items,
            opts.query.as_deref(),
            frames,
            swipes,
            opts.max_items,
        );
        if let Some(hitv) = found_hit {
            hit = Some(hitv);
            break 'scan "found";
        }
        if produced == 0 {
            // 这一屏没有任何新内容: 到底了, 或者页面在转圈/加载中。
            // 两种都不该继续滑 —— 盲滑是 iPhone-use 明确禁止的失败模式。
            break 'scan "end_of_list";
        }
        if items.len() >= opts.max_items {
            break 'scan "max_items";
        }
        if swipes >= opts.max_swipes {
            break 'scan "max_swipes";
        }
        // 滑一屏: 从屏幕下方 78% 划到 28%(内容上移 = 列表向下滚动)
        phone.swipe(w / 2, (h as f64 * 0.78) as i32, w / 2, (h as f64 * 0.28) as i32);
        swipes += 1;
        std::thread::sleep(Duration::from_millis(opts.settle_ms));
    };

    // 覆盖边界: 只有「找到」与「到底了」算覆盖完整; 撞预算一律如实标未覆盖。
    let complete = matches!(stop, "found" | "end_of_list");
    let out = json!({
        "query": opts.query,
        "found": hit.is_some(),
        "hit": hit,
        "swipes": swipes,
        "frames": frames,
        "items": items,
        "item_count": seen.len(),
        "coverage": {
            "complete": complete,
            "stop": stop,
            "max_swipes": opts.max_swipes,
            "note": if complete { "已覆盖整屏列表" } else { "撞到有界上限, 列表下方可能还有未采集内容" }
        },
        "device": {"w": w, "h": h, "pkg": pkg}
    });

    if opts.json {
        println!("{out}");
    } else if let Some(q) = &opts.query {
        match &out["hit"] {
            Value::Null => println!(
                "未找到「{q}」(滑了 {swipes} 屏, 看了 {frames} 屏, 清单 {})",
                seen.len()
            ),
            v => println!(
                "找到「{}」在第{}屏 滑{}次 位置{:?}",
                v["text"].as_str().unwrap_or(""),
                v["frame"].as_u64().unwrap_or(0),
                v["swipe"].as_u64().unwrap_or(0),
                v["box"]
            ),
        }
        if !complete {
            println!("注意: {}", out["coverage"]["note"].as_str().unwrap_or(""));
        }
    } else {
        println!("采集 {} 条(滑 {} 屏 / 看 {} 屏, 停在 {stop})", seen.len(), swipes, frames);
        for it in &items {
            println!(
                "{} {:?}",
                it["text"].as_str().unwrap_or(""),
                it["box"]
            );
        }
        if !complete {
            println!("注意: {}", out["coverage"]["note"].as_str().unwrap_or(""));
        }
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fold::Kind;

    fn line(t: &str, x: i32) -> Line {
        Line { t: t.to_string(), b: [x, 0, x + 10, 10], kind: Kind::Plain }
    }

    #[test]
    fn dedupes_overlapping_pages() {
        let mut seen = HashSet::new();
        let mut items = Vec::new();
        // 第一屏
        let (n1, _) = ingest(&[line("A", 1), line("B", 2)], &mut seen, &mut items, None, 1, 0, 100);
        // 第二屏与第一屏重叠: 只有 C 是新的
        let (n2, _) = ingest(&[line("B", 2), line("C", 3)], &mut seen, &mut items, None, 2, 1, 100);
        assert_eq!((n1, n2), (2, 1));
        assert_eq!(seen.len(), 3);
        assert_eq!(items.len(), 3); // 重叠的 B 没有重复入账
    }

    #[test]
    fn finds_query_even_if_already_seen() {
        let mut seen = HashSet::new();
        let mut items = Vec::new();
        ingest(&[line("目标项", 1), line("别的", 2)], &mut seen, &mut items, None, 1, 0, 100);
        // 已经见过, 但查询命中必须回报位置
        let (_, hit) = ingest(&[line("目标项", 1)], &mut seen, &mut items, Some("目标"), 2, 1, 100);
        let hit = hit.expect("必须回报命中");
        assert_eq!(hit["text"], "目标项");
        assert_eq!(hit["frame"], 2);
        assert_eq!(hit["swipe"], 1);
    }

    #[test]
    fn blank_lines_are_not_ingested() {
        let mut seen = HashSet::new();
        let mut items = Vec::new();
        let (n, _) = ingest(&[line("   ", 1), line("", 2)], &mut seen, &mut items, None, 1, 0, 100);
        assert_eq!(n, 0);
        assert!(seen.is_empty());
    }

    #[test]
    fn respects_max_items_but_still_counts() {
        let mut seen = HashSet::new();
        let mut items = Vec::new();
        let (n, _) = ingest(
            &[line("A", 1), line("B", 2), line("C", 3)],
            &mut seen, &mut items, None, 1, 0, 2,
        );
        assert_eq!(n, 3, "新增条数照实, 不受 items 上限影响");
        assert_eq!(items.len(), 2, "items 受上限保护");
        assert_eq!(seen.len(), 3);
    }

    #[test]
    fn opts_reject_bad_numbers() {
        let e = parse_opts(&["--max-swipes".into(), "abc".into()]).unwrap_err();
        assert!(e.contains("--max-swipes"));
        let o = parse_opts(&["--query".into(), "  ".into()]).unwrap();
        assert!(o.query.is_none(), "空白查询视为未提供");
    }
}
