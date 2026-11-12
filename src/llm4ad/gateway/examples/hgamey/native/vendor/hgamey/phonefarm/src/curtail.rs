//! 确定性截断 (curtailment): 剩下的轮次无论量出什么都改不了判定时, 当场停。
//!
//! 判定规则 (eval sysparam / autoloop 的 sysparam_v1, 规则先于数据冻结):
//! KEEP ⟺ 至少一个指标「显著改善」(方向对且 p < alpha_win) 且没有指标「显著变差」。
//! p 是 n_a v n_b 精确置换检验 (均值差, 双侧, 全枚举), 见 [`loopstat::perm_p_two_sided`]。
//!
//! 为什么能提前停, 而且判定与跑满**逐字相同**
//! ------------------------------------------
//! 设已量到的对照臂值 K_A、候选臂值 K_B, 两臂各还剩若干轮没跑 (取值任意实数)。
//! 以「候选更大更好」的指标为例 (更小更好的对称), 跑满时它要算「显著改善」,
//! 最终均值差必须 > 0。此时取 K = K_A ∪ K_B 的任意 |K_B| 元子集 T', 只要
//! sum(T') ≥ sum(K_B), 分组 T = T' ∪ {候选臂未跑的那几轮} 的组和就 ≥ 实测候选臂组和,
//! 于是 |均值差_T| ≥ |均值差_实测|, 在置换检验里算一次命中; 两臂等大时 T 的补集同样命中,
//! 且与前者互不重合。所以
//!
//! ```text
//!     p_跑满 ≥ f · c / C(N_a + N_b, N_b)      c = 满足 sum(T') ≥ sum(K_B) 的 T' 个数
//!                                              f = 2 (两臂等大) 或 1
//! ```
//!
//! 右边只用到已量到的值, 与没跑的那几轮取什么无关。若它 (按判定同样的 6 位舍入)
//! 已经 ≥ alpha_win, 这个指标跑满也拿不到「显著改善」。**所有指标都拿不到** →
//! KEEP 不可达 → 跑满必然不是 KEEP → 现在停, 判定与跑满相同, 由构造保证。
//!
//! 反过来, KEEP 永远不能提前锁定: 剩下的轮次总能把均值差拉回 0 (p = 1)。
//! 所以这里只截「必然不保留」的那一侧。这不是序贯检验, 没有 alpha 消耗, 错误率不变 ——
//! 停下来时给出的判定, 就是跑满会给出的判定。
//!
//! 浮点: 上面的论证在精确算术下成立。实现里只把「恰好就是 K_B 本身」和「组和超出
//! 一个相对 1e-9 余量」的 T' 计进 c (近似平局一律不计), 让下界只会偏小 (更保守);
//! 判定侧的命中容差是 1e-12, 对帧时/帧率/瓦数这种量级的数绰绰有余。

use crate::loopstat::{self, Run};
use crate::pyjson::py_round;

/// 一条「显著改善」检验: 指标、方向、它的 alpha_win。
#[derive(Debug, Clone)]
pub struct WinTest {
    pub metric: String,
    pub lower_is_better: bool,
    pub alpha_win: f64,
}

impl WinTest {
    pub fn new(metric: &str, lower_is_better: bool, alpha_win: f64) -> Self {
        Self {
            metric: metric.to_string(),
            lower_is_better,
            alpha_win,
        }
    }
}

/// C(n, k), 用 f64 (n ≤ 60 量级时精确到整数)。
fn comb(n: usize, k: usize) -> f64 {
    if k > n {
        return 0.0;
    }
    let k = k.min(n - k);
    let mut c = 1.0f64;
    for i in 1..=k {
        c = c * (n - k + i) as f64 / i as f64;
    }
    c.round()
}

/// 按字典序枚举 `0..n` 取 k 个的组合。
fn for_each_combination(n: usize, k: usize, mut f: impl FnMut(&[usize])) {
    if k > n {
        return;
    }
    let mut idx: Vec<usize> = (0..k).collect();
    loop {
        f(&idx);
        let mut i = k;
        loop {
            if i == 0 {
                return;
            }
            i -= 1;
            if idx[i] != i + n - k {
                break;
            }
            if i == 0 {
                return;
            }
        }
        idx[i] += 1;
        for j in i + 1..k {
            idx[j] = idx[j - 1] + 1;
        }
    }
}

/// 跑满之后, 这个指标在「改善」方向上能拿到的置换 p 的下界 (模块注释里的 f·c/C)。
///
/// `remaining_a` / `remaining_b`: 两臂各还剩几轮没跑。没跑的轮次也可能缺这个指标,
/// 所以对每种可能的最终样本量取最小。最终任一臂不足 2 个样本时 compare 报「样本不足」,
/// 不可能显著 —— 那种样本量不参与取最小; 全部不可能时返回 `None`。
pub fn win_p_floor(
    known_a: &[f64],
    known_b: &[f64],
    remaining_a: usize,
    remaining_b: usize,
    lower_is_better: bool,
) -> Option<f64> {
    let (ka, kb) = (known_a.len(), known_b.len());
    let pool: Vec<f64> = known_a.iter().chain(known_b.iter()).copied().collect();
    let sum_b: f64 = known_b.iter().sum();
    let scale = 1.0 + pool.iter().map(|x| x.abs()).sum::<f64>();
    let margin = 1e-9 * scale;
    // 与 K_B 本身相同的那个组合 = 池里最后 kb 个下标
    let own: Vec<usize> = (ka..ka + kb).collect();
    let mut c = 0usize;
    for_each_combination(ka + kb, kb, |t| {
        if t == own.as_slice() {
            c += 1;
            return;
        }
        let s: f64 = t.iter().map(|&i| pool[i]).sum();
        let as_good = if lower_is_better {
            s <= sum_b - margin
        } else {
            s >= sum_b + margin
        };
        if as_good {
            c += 1;
        }
    });
    let mut best: Option<f64> = None;
    for na in ka..=ka + remaining_a {
        for nb in kb..=kb + remaining_b {
            if na < 2 || nb < 2 {
                continue;
            }
            let f = if na == nb { 2.0 } else { 1.0 };
            let p = (f * c as f64 / comb(na + nb, nb)).min(1.0);
            best = Some(best.map_or(p, |b: f64| b.min(p)));
        }
    }
    best
}

/// 这条检验跑满之后还**可能**显著改善吗。判定侧比的是 6 位舍入后的 p, 这里同样舍入
/// (舍入单调, 下界舍入后仍是下界)。
pub fn win_possible(known_a: &[f64], known_b: &[f64], remaining_a: usize, remaining_b: usize, t: &WinTest) -> bool {
    win_p_floor(known_a, known_b, remaining_a, remaining_b, t.lower_is_better)
        .is_some_and(|p| py_round(p, 6) < t.alpha_win)
}

/// 剩下的轮次还可能把判定翻成 KEEP 吗。`false` = 跑满也必然不是 KEEP, 可以现在停。
///
/// `tests` 要覆盖跑满时**所有可能**进入判定的指标, 各自取它可能用到的最宽的 alpha_win
/// (例如功耗进不进判定要看后面每轮是不是都在放电态: 那就把功耗按 alpha/3、
/// 帧时帧率按 alpha/2 一起放进来 —— 只会让截断更保守)。
pub fn keep_possible(a_runs: &[Run], b_runs: &[Run], remaining_a: usize, remaining_b: usize, tests: &[WinTest]) -> bool {
    tests.iter().any(|t| {
        let a: Vec<f64> = loopstat::column(a_runs, &t.metric).iter().map(|x| x.f()).collect();
        let b: Vec<f64> = loopstat::column(b_runs, &t.metric).iter().map(|x| x.f()).collect();
        win_possible(&a, &b, remaining_a, remaining_b, t)
    })
}

/// eval / autoloop 的 ABBA 臂序: 第 i 对奇数先跑候选臂, 偶数先跑对照臂。
/// 返回 `(是否候选臂, 第几轮)` 序列。
pub fn abba_order(pairs: usize) -> Vec<(bool, usize)> {
    (1..=pairs)
        .flat_map(|i| {
            if i % 2 == 1 {
                [(true, i), (false, i)]
            } else {
                [(false, i), (true, i)]
            }
        })
        .collect()
}

/// 臂序随机化 (审计 #13): 以前 31/31 个 case 都是候选臂先跑, 停充后最初那段电池仍在吸电、
/// 应用刚启动那段的系统状态, 永远落在候选臂上, ABBA 抵消不了「第一臂」这个固定偏差。
///
/// 规则 (先于数据冻结): 每两对为一块, 块内用一次抛硬币决定是「候选先, 对照先」还是
/// 「对照先, 候选先」; 轮数是奇数时最后一对单独抛一次。这样第一臂是谁由种子决定,
/// 同时仍保留 ABBA 的逐块平衡 (线性热漂移照样在块内抵消)。
/// 返回每一对「是否候选臂先跑」。同一个种子逐位可复现, 种子要原样记进报告。
pub fn knob_first_order(pairs: usize, seed: u64) -> Vec<bool> {
    let mut rng = SplitMix64(seed);
    let mut out = Vec::with_capacity(pairs);
    while out.len() < pairs {
        let first = rng.next() & 1 == 1;
        out.push(first);
        if out.len() < pairs {
            out.push(!first);
        }
    }
    out
}

/// 按 [`knob_first_order`] 展开的到达顺序: `(是否候选臂, 第几轮)`。
pub fn seeded_order(pairs: usize, seed: u64) -> Vec<(bool, usize)> {
    knob_first_order(pairs, seed)
        .into_iter()
        .enumerate()
        .flat_map(|(k, knob_first)| [(knob_first, k + 1), (!knob_first, k + 1)])
        .collect()
}

/// 臂序种子: 请求给了 `protocol.order_seed` 就用它 (复现用), 否则取当前时间的纳秒数。
pub fn order_seed(req_seed: Option<u64>) -> u64 {
    req_seed.unwrap_or_else(|| {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0)
    })
}

/// 臂序记录, 原样进回包 `conditions_observed.arm_order`。
pub fn order_note(seed: u64, pairs: usize) -> serde_json::Value {
    serde_json::json!({
        "seed": seed,
        "knob_first": knob_first_order(pairs, seed),
        "rule": "每两对一块, 块内抛一次硬币决定先跑哪一臂, 下一对反过来; 奇数轮的最后一对单独抛 (src/curtail.rs knob_first_order)",
    })
}

/// splitmix64: 不引依赖的小随机数源, 只用来定臂序。
struct SplitMix64(u64);

impl SplitMix64 {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }
}

// ══════════════ 离线回放: 拿归档的两臂逐轮数据, 按 ABBA 到达顺序重演截断 ══════════════

const REPLAY_USAGE: &str = "用法: phonefarm curtail-replay <A臂(对照)glob> <B臂(候选)glob> [--alpha 0.05] [--metrics m1,m2,..] [--order-seed N]\n\
    glob 命中目录取其下 summary.json, 命中文件 (如 metrics.json) 直接用; 轮次取标签里的第一个数字。\n\
    缺省指标表: frame_p95 / fps_mean / power_w_mean 里两臂每轮都有的那几个 (方向按 sysparam 判定表)。\n\
    --order-seed: 按回包 conditions_observed.arm_order.seed 的随机臂序重演 (缺省按旧的固定 ABBA)。";

/// 标签里的第一个数字 = 第几轮 (ctrl3 / bw_knob3 / a3_gameup)。
fn round_of(label: &str) -> Option<usize> {
    let d: String = label
        .chars()
        .skip_while(|c| !c.is_ascii_digit())
        .take_while(|c| c.is_ascii_digit())
        .collect();
    d.parse().ok()
}

/// 跑满的判定 (与 eval sysparam / sysparam::decide 同一条规则): 至少一个指标显著改善
/// (方向对且 6 位舍入的 p < alpha/K) 且没有指标显著变差 (方向反且 p < alpha)。
/// (是否 KEEP, 显著改善的指标, 显著变差的指标, 各指标 p)
type Verdict = (bool, Vec<String>, Vec<String>, Vec<(String, f64)>);

fn full_verdict(a: &[Run], b: &[Run], metrics: &[(String, bool)], alpha: f64) -> Verdict {
    let alpha_win = alpha / metrics.len().max(1) as f64;
    let (mut wins, mut regs, mut ps) = (Vec::new(), Vec::new(), Vec::new());
    for (m, lower) in metrics {
        let (ca, cb) = (loopstat::column(a, m), loopstat::column(b, m));
        if ca.len() < 2 || cb.len() < 2 {
            continue;
        }
        let p = py_round(loopstat::perm_p_two_sided(&ca, &cb).0, 6);
        let diff = loopstat::mean(&cb) - loopstat::mean(&ca);
        let improved = if *lower { diff < 0.0 } else { diff > 0.0 };
        if improved && p < alpha_win {
            wins.push(m.clone());
        } else if !improved && diff != 0.0 && p < alpha {
            regs.push(m.clone());
        }
        ps.push((m.clone(), p));
    }
    (!wins.is_empty() && regs.is_empty(), wins, regs, ps)
}

pub fn run_replay(args: &[String]) -> i32 {
    use crate::pyobj;
    use crate::pyjson::{dumps_indent, PyVal};
    let pos: Vec<&String> = {
        let mut v = Vec::new();
        let mut it = args.iter();
        while let Some(x) = it.next() {
            if x.starts_with("--") {
                it.next();
            } else {
                v.push(x);
            }
        }
        v
    };
    let opt = |k: &str| args.iter().position(|a| a == k).and_then(|i| args.get(i + 1));
    if pos.len() != 2 {
        eprintln!("{REPLAY_USAGE}");
        return 2;
    }
    let alpha: f64 = opt("--alpha").and_then(|v| v.parse().ok()).unwrap_or(0.05);
    let (mut a, mut b) = (loopstat::load_runs(pos[0]), loopstat::load_runs(pos[1]));
    if a.is_empty() || b.is_empty() {
        eprintln!("某一臂没找到任何数据: A={} B={}", a.len(), b.len());
        return 1;
    }
    for r in a.iter().chain(b.iter()) {
        if round_of(&r.0).is_none() {
            eprintln!("标签里没有轮次数字: {}", r.0);
            return 2;
        }
    }
    a.sort_by_key(|r| round_of(&r.0));
    b.sort_by_key(|r| round_of(&r.0));
    let metrics: Vec<(String, bool)> = match opt("--metrics") {
        Some(list) => list
            .split(',')
            .filter_map(|m| Some((m.to_string(), crate::sysparam::lower_is_better(m)?)))
            .collect(),
        None => ["frame_p95", "fps_mean", "power_w_mean"]
            .iter()
            .filter(|m| a.iter().chain(b.iter()).all(|r| r.1.get(m).and_then(loopstat::Num::from_json).is_some()))
            .filter_map(|m| Some((m.to_string(), crate::sysparam::lower_is_better(m)?)))
            .collect(),
    };
    if metrics.is_empty() {
        eprintln!("没有可用的判定指标");
        return 1;
    }
    let pairs = a.len().max(b.len());
    let alpha_win = alpha / metrics.len() as f64;
    let tests: Vec<WinTest> = metrics.iter().map(|(m, l)| WinTest::new(m, *l, alpha_win)).collect();
    let (keep, wins, regs, ps) = full_verdict(&a, &b, &metrics, alpha);

    // 按 ABBA 到达顺序逐臂喂; 某一臂缺轮 (归档里本来就少) 就跳过那一步
    let (mut na, mut nb) = (0usize, 0usize);
    let mut stop: Option<usize> = None;
    let mut partial_keep = false;
    // 2026-10-21 之后的归档按随机臂序跑 (回包 conditions_observed.arm_order.seed);
    // 给了 --order-seed 就按那个顺序重演, 没给按旧的固定 ABBA
    let order = match opt("--order-seed").and_then(|v| v.parse::<u64>().ok()) {
        Some(seed) => seeded_order(pairs, seed),
        None => abba_order(pairs),
    };
    for (k, (is_b, i)) in order.into_iter().enumerate() {
        let have = |arm: &[Run]| arm.iter().any(|r| round_of(&r.0) == Some(i));
        if is_b && have(&b) {
            nb += 1;
        } else if !is_b && have(&a) {
            na += 1;
        } else {
            continue;
        }
        if !keep_possible(&a[..na], &b[..nb], a.len() - na, b.len() - nb, &tests) {
            stop = Some(k + 1);
            partial_keep = full_verdict(&a[..na], &b[..nb], &metrics, alpha).0;
            break;
        }
    }
    // 一致 = 截断了的, 跑满也必然不是 KEEP, 部分数据也判不出 KEEP
    let consistent = stop.is_none() || (!keep && !partial_keep);
    let total = a.len() + b.len();
    let out = pyobj! {
        "a" => pos[0].as_str(), "b" => pos[1].as_str(),
        "n_a" => a.len(), "n_b" => b.len(),
        "metrics" => metrics.iter().map(|(m, l)| format!("{m}({})", if *l { "lower" } else { "higher" })).collect::<Vec<_>>(),
        "alpha_win" => py_round(alpha_win, 6),
        "full_keep" => keep,
        "full_wins" => wins,
        "full_regressions" => regs,
        "full_p" => PyVal::Obj(ps.into_iter().map(|(m, p)| (m, PyVal::Float(p))).collect()),
        "curtailed_after_arms" => stop.map(|x| PyVal::Int(x as i64)).unwrap_or(PyVal::Null),
        "arms_total" => total,
        "arms_saved" => stop.map(|x| total.saturating_sub(x)).unwrap_or(0),
        "consistent" => consistent,
    };
    println!("{}", dumps_indent(&out, 1));
    if consistent { 0 } else { 1 }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::loopstat::{perm_p_two_sided, Num};

    fn nums(xs: &[f64]) -> Vec<Num> {
        xs.iter().map(|x| Num::Float(*x)).collect()
    }

    /// 与判定侧同一套: p 舍入 6 位后跟 alpha_win 比, 方向要对。
    fn full_win(a: &[f64], b: &[f64], lower: bool, alpha_win: f64) -> bool {
        let (p, _) = perm_p_two_sided(&nums(a), &nums(b));
        let diff = b.iter().sum::<f64>() / b.len() as f64 - a.iter().sum::<f64>() / a.len() as f64;
        let improved = if lower { diff < 0.0 } else { diff > 0.0 };
        improved && py_round(p, 6) < alpha_win
    }

    /// 确定性伪随机 (不引依赖, 结果可复现)。
    struct Lcg(u64);
    impl Lcg {
        fn next(&mut self) -> f64 {
            self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            ((self.0 >> 11) as f64) / ((1u64 << 53) as f64)
        }
        fn normal(&mut self) -> f64 {
            // Box-Muller
            let (u, v) = (self.next().max(1e-12), self.next());
            (-2.0 * u.ln()).sqrt() * (2.0 * std::f64::consts::PI * v).cos()
        }
    }

    #[test]
    fn floor_with_nothing_known_is_the_resolution_limit() {
        // 一轮没跑: 下界就是 5v5 的最小可达 p = 2/252
        let p = win_p_floor(&[], &[], 5, 5, true).unwrap();
        assert!((p - 2.0 / 252.0).abs() < 1e-15);
        // 4v4 够不着 0.0167: 一开始就不可能, 当场就能判 (与 rule_doc 的 reachable=false 一致)
        let p4 = win_p_floor(&[], &[], 4, 4, true).unwrap();
        assert!((p4 - 2.0 / 70.0).abs() < 1e-15);
        assert!(!win_possible(&[], &[], 4, 4, &WinTest::new("x", true, 0.05 / 3.0)));
        // 任一臂最终不足 2 个样本: 不可能显著
        assert_eq!(win_p_floor(&[], &[], 1, 5, true), None);
    }

    /// 已知部分只剩一种分法比实测更极端时, 下界 = 2·2/252, 仍够得着 alpha/3。
    #[test]
    fn floor_counts_known_subsets_at_least_as_extreme() {
        // 越小越好: 候选 1.0 比对照 2.0 小 —— 只有它自己 (c=1)
        let p = win_p_floor(&[2.0], &[1.0], 4, 4, true).unwrap();
        assert!((p - 2.0 / 252.0).abs() < 1e-15);
        // 候选 2.0 比对照 1.0 大 (方向反了): 两个单元素子集都不劣于它 (c=2)
        let p = win_p_floor(&[1.0], &[2.0], 4, 4, true).unwrap();
        assert!((p - 4.0 / 252.0).abs() < 1e-15);
        // 两对: 候选两轮都是四个值里最差的 → C(4,2)=6 个子集全都不劣 → 12/252
        let p = win_p_floor(&[1.0, 2.0], &[3.0, 4.0], 3, 3, true).unwrap();
        assert!((p - 12.0 / 252.0).abs() < 1e-15);
        assert!(!win_possible(&[1.0, 2.0], &[3.0, 4.0], 3, 3, &WinTest::new("x", true, 0.05 / 3.0)));
        assert!(win_possible(&[3.0, 4.0], &[1.0, 2.0], 3, 3, &WinTest::new("x", true, 0.05 / 3.0)));
    }

    /// 下界确实是下界: 随机数据、随机补齐, 跑满的真实 p 从不低于下界。
    #[test]
    fn floor_never_exceeds_the_full_p() {
        let mut r = Lcg(7);
        for _ in 0..3000 {
            let n = 5usize;
            let ka = (r.next() * 6.0) as usize; // 0..=5
            let kb = (r.next() * 6.0) as usize;
            let shift = r.normal() * 2.0;
            let a: Vec<f64> = (0..n).map(|_| 10.0 + r.normal()).collect();
            let b: Vec<f64> = (0..n).map(|_| 10.0 + shift + r.normal()).collect();
            for lower in [true, false] {
                let floor = win_p_floor(&a[..ka], &b[..kb], n - ka, n - kb, lower).unwrap();
                let diff = b.iter().sum::<f64>() - a.iter().sum::<f64>();
                let improved = if lower { diff < 0.0 } else { diff > 0.0 };
                if improved {
                    let (p, _) = perm_p_two_sided(&nums(&a), &nums(&b));
                    assert!(p + 1e-12 >= floor, "p={p} < floor={floor} (ka={ka} kb={kb})");
                }
            }
        }
    }

    /// 核心性质: 按 ABBA 逐臂到达, 任何一刻判「KEEP 不可达」而停下, 跑满的判定都不是 KEEP;
    /// 而且截断时手上的部分数据也判不出 KEEP。数据里故意掺强效应, 让 KEEP 真的出现。
    #[test]
    fn curtailment_never_changes_a_verdict() {
        let mut r = Lcg(2026);
        let order = abba_order(5);
        let alpha = 0.05;
        let (mut keeps, mut stops, mut arms_saved) = (0usize, 0usize, 0usize);
        for _ in 0..1500 {
            // 三个指标: 帧时 (越小越好)、帧率 (越大越好)、功耗 (越小越好)
            let dirs = [true, false, true];
            let mut a = vec![vec![0.0; 5]; 3];
            let mut b = vec![vec![0.0; 5]; 3];
            for m in 0..3 {
                let shift = if r.next() < 0.3 { r.normal() * 4.0 } else { 0.0 };
                for i in 0..5 {
                    a[m][i] = 20.0 + r.normal();
                    b[m][i] = 20.0 + shift + r.normal();
                }
            }
            let alpha_win = alpha / 3.0;
            let full_keep = (0..3).any(|m| full_win(&a[m], &b[m], dirs[m], alpha_win))
                && !(0..3).any(|m| {
                    let (p, _) = perm_p_two_sided(&nums(&a[m]), &nums(&b[m]));
                    let diff = b[m].iter().sum::<f64>() - a[m].iter().sum::<f64>();
                    let improved = if dirs[m] { diff < 0.0 } else { diff > 0.0 };
                    !improved && diff != 0.0 && py_round(p, 6) < alpha
                });
            if full_keep {
                keeps += 1;
            }
            let (mut na, mut nb) = (0usize, 0usize);
            for (k, (is_b, _)) in order.iter().enumerate() {
                if *is_b {
                    nb += 1
                } else {
                    na += 1
                }
                let possible = (0..3).any(|m| {
                    win_possible(&a[m][..na], &b[m][..nb], 5 - na, 5 - nb, &WinTest::new("x", dirs[m], alpha_win))
                });
                if !possible {
                    assert!(!full_keep, "截断了一个跑满会 KEEP 的候选");
                    if na >= 2 && nb >= 2 {
                        assert!(
                            !(0..3).any(|m| full_win(&a[m][..na], &b[m][..nb], dirs[m], alpha_win)),
                            "截断时部分数据却显示显著改善"
                        );
                    }
                    stops += 1;
                    arms_saved += order.len() - (k + 1);
                    break;
                }
            }
        }
        // 不是空转: KEEP 真的出现过, 截断也真的发生过
        assert!(keeps > 50, "keeps={keeps}");
        assert!(stops > 300, "stops={stops}");
        assert!(arms_saved > 0);
    }

    #[test]
    fn abba_order_matches_eval_and_autoloop() {
        let o = abba_order(3);
        assert_eq!(o, vec![(true, 1), (false, 1), (false, 2), (true, 2), (true, 3), (false, 3)]);
    }

    #[test]
    fn seeded_order_is_reproducible_balanced_and_not_always_knob_first() {
        // 同一个种子逐位复现
        assert_eq!(knob_first_order(7, 42), knob_first_order(7, 42));
        // 每块两对: 后一对与前一对相反 (ABBA 的逐块平衡还在)
        for seed in 0..200u64 {
            let o = knob_first_order(6, seed);
            assert_eq!(o.len(), 6);
            for k in (0..6).step_by(2) {
                assert_ne!(o[k], o[k + 1], "seed {seed}");
            }
            let s = seeded_order(6, seed);
            assert_eq!(s.len(), 12);
            // 每一对两臂各一次
            for i in 1..=6 {
                assert_eq!(s.iter().filter(|(_, r)| *r == i).count(), 2);
                assert_eq!(s.iter().filter(|(b, r)| *r == i && *b).count(), 1);
            }
        }
        // 第一臂不再永远是候选臂: 200 个种子里两种都要出现, 且大致各半
        let knob_first = (0..200u64).filter(|s| knob_first_order(1, *s)[0]).count();
        assert!((60..=140).contains(&knob_first), "{knob_first}");
        // 奇数轮的最后一对也是抛硬币定的
        assert_eq!(knob_first_order(5, 9).len(), 5);
        let note = order_note(42, 4);
        assert_eq!(note["seed"], 42);
        assert_eq!(note["knob_first"].as_array().unwrap().len(), 4);
        assert_eq!(order_seed(Some(7)), 7);
    }

    #[test]
    fn comb_is_exact_for_small_n() {
        assert_eq!(comb(10, 5), 252.0);
        assert_eq!(comb(8, 4), 70.0);
        assert_eq!(comb(9, 4), 126.0);
        assert_eq!(comb(5, 0), 1.0);
    }
}
