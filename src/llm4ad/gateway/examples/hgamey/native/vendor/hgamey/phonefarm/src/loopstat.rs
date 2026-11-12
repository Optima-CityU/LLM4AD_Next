//! loop_v1 的统计层: 多轮 summary.json → 离散度 (判据 1) 与两臂对比 (判据 3)。
//!
//! 由 `loop_v1/tools/analyze.py` 逐行搬过来, 输出字节完全一致。
//! 全程无随机数、无分布假设、无时钟, 因此同一批 summary.json 每次跑出的数逐字节一致
//! (判据 5 要的可回放性)。
//!
//! 两个判据各自的口径
//! ------------------
//! 判据 1 离散度 : (max - min) / median, 与 `bench.rs` 的 DISPERSION_LIMIT_PCT
//!                 口径一致 —— 复用既有约定, 不另造一套。
//! 判据 3 显著性 : 不比大小, 给三样东西
//!                 · 效应量  : Cohen's d (合并标准差归一) + Hodges-Lehmann 位移估计
//!                 · p 值    : 精确置换检验。5v5 共 C(10,5)=252 种分组, 全枚举, 不抽样,
//!                             所以没有随机种子问题, 也不依赖正态性。
//!                 · 置信区间: 置换检验反演。对一系列位移 δ, 把 B 臂整体减去 δ 后重做
//!                             置换检验, 收集"不被拒绝"的 δ 区间 —— 这就是精确 95% CI。
//!                             样本只有 5 个时, 这比套 t 分布诚实得多。
//!
//! 为什么要区分 int 与 float
//! -------------------------
//! `bw_median` 在 Python 侧可能是 int 也可能是 float (statistics.median 奇数个 int
//! 返回 int), 而 `round(int, n)` 在 Python 里返回的还是 int。实测 report.json 里
//! 同一个字段两臂一个是 `"median": 3250` 一个是 `"median": 3300.0` —— 这个差别直接
//! 落在字节上, 所以这一层的算术全走 [`Num`], 不能一律当 f64。

use crate::pyjson::{self, dumps, py_round, py_sum, PyVal};
use crate::llm;
use crate::sysparam;
use crate::pyobj;
use std::path::Path;

/// 判据 1/3 都看这几个指标, 顺序即输出里的键顺序。
const METRICS: [&str; 9] = [
    "frame_p50",
    "frame_p95",
    "frame_p99",
    "frame_mean",
    "gpu_active_mean",
    "gpu_active_p95",
    "queue_p50",
    "fps_mean",
    "bw_median",
];

/// 两臂对比固定跑这几项 (与主指标的选择无关)。
const COMPARE_METRICS: [&str; 5] = [
    "frame_p95",
    "frame_p50",
    "frame_mean",
    "gpu_active_mean",
    "bw_median",
];

// ══════════════ Python 的数字语义 ══════════════

/// int 与 float 分开的数字。Python 的算术会按操作数类型决定结果类型,
/// 这里只复刻用得到的那几条规则 (见模块注释)。
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Num {
    Int(i64),
    Float(f64),
}

impl Num {
    pub fn f(self) -> f64 {
        match self {
            Num::Int(i) => i as f64,
            Num::Float(x) => x,
        }
    }
    /// Python `a - b`: 两个 int 相减还是 int, 沾上 float 就是 float。
    fn sub(self, o: Num) -> Num {
        match (self, o) {
            (Num::Int(a), Num::Int(b)) => Num::Int(a - b),
            _ => Num::Float(self.f() - o.f()),
        }
    }
    /// Python `round(x, n)`: int 进 int 出 (**不会**变成 float)。
    fn round(self, n: usize) -> Num {
        match self {
            Num::Int(_) => self,
            Num::Float(x) => Num::Float(py_round(x, n)),
        }
    }
    pub fn from_json(v: &PyVal) -> Option<Num> {
        match v {
            // Python 的 isinstance(x, (int, float)) 对 bool 也成立 (bool 是 int 的子类),
            // 但 summary.json 里这些字段从来不是布尔, 这里不特殊照顾。
            PyVal::Int(i) => Some(Num::Int(*i)),
            PyVal::Float(f) => Some(Num::Float(*f)),
            _ => None,
        }
    }
}

impl From<Num> for PyVal {
    fn from(n: Num) -> PyVal {
        match n {
            Num::Int(i) => PyVal::Int(i),
            Num::Float(x) => PyVal::Float(x),
        }
    }
}

/// `statistics.median`: 奇数个原样返回 (int 还是 int), 偶数个取两中位数的均值 (真除法, 必为 float)。
pub fn median(xs: &[Num]) -> Option<Num> {
    if xs.is_empty() {
        return None;
    }
    let mut s = xs.to_vec();
    s.sort_by(|a, b| a.f().partial_cmp(&b.f()).unwrap_or(std::cmp::Ordering::Equal));
    let n = s.len();
    Some(if n % 2 == 1 {
        s[n / 2]
    } else {
        Num::Float((s[n / 2 - 1].f() + s[n / 2].f()) / 2.0)
    })
}

fn min_of(xs: &[Num]) -> Option<Num> {
    xs.iter()
        .copied()
        .reduce(|a, b| if b.f() < a.f() { b } else { a })
}

fn max_of(xs: &[Num]) -> Option<Num> {
    xs.iter()
        .copied()
        .reduce(|a, b| if b.f() > a.f() { b } else { a })
}

// ══════════════ 基础统计 ══════════════

/// CPython 内置 `sum()` 的逐位复刻。
///
/// 它有三段, 缺一段就会在末位差 1 ulp, 而 `round(x, 4)` 会把这 1 ulp 放大成
/// 不同的字节:
///   1. 开头全是 int → 精确整数累加 (不落 double);
///   2. 碰上第一个 float → 把整数部分转成 double 后**朴素相加**一次, 补偿量清零;
///   3. 之后 → Neumaier 补偿求和 (int 也先转 double 再进这条路)。
///
/// 第 2 段最容易漏。实测 `sum([5230, 3335.9446, 4504.6, 598.3])` 是
/// 13668.844599999999 而不是全程补偿的 13668.8446 —— 差别就出在开头那个 int 上。
#[derive(Default)]
pub struct PySum {
    ints: i128,
    float_started: bool,
    f: f64,
    c: f64,
}

impl PySum {
    pub fn push(&mut self, x: Num) {
        if !self.float_started {
            match x {
                Num::Int(v) => {
                    self.ints += v as i128;
                    return;
                }
                Num::Float(v) => {
                    // 第二段: 朴素相加一次, 补偿从这里才起算
                    self.float_started = true;
                    self.f = self.ints as f64 + v;
                    return;
                }
            }
        }
        let v = x.f();
        let t = self.f + v;
        self.c += if self.f.abs() >= v.abs() {
            (self.f - t) + v
        } else {
            (v - t) + self.f
        };
        self.f = t;
    }
    pub fn total(&self) -> f64 {
        if self.float_started {
            self.f + self.c
        } else {
            self.ints as f64
        }
    }
}

/// CPython `sum()` 的一次性版本。
pub fn sum_nums(xs: &[Num]) -> f64 {
    let mut s = PySum::default();
    for x in xs {
        s.push(*x);
    }
    s.total()
}

/// `sum(xs)/len(xs)` —— 真除法, 结果必为 float。
pub fn mean(xs: &[Num]) -> f64 {
    sum_nums(xs) / xs.len() as f64
}

/// (max-min)/median, 返回比例 (0.05 = 5%)。中位数为 0 或样本不足时返回 None。
pub fn dispersion(xs: &[Num]) -> Option<f64> {
    if xs.len() < 2 {
        return None;
    }
    let m = median(xs)?.f();
    if m == 0.0 {
        return None;
    }
    Some((max_of(xs)?.f() - min_of(xs)?.f()) / m)
}

/// 检测逐轮单调漂移 —— 把"系统性漂移"和"随机抖动"分开。
///
/// 离散度只说"散得有多开", 说不出"是不是一直往一个方向走"。可重复负载要求后者为零:
/// 5 轮里如果每轮都比上一轮高, 那不是噪声, 是有个外生变量在动 (光照、温度、内存压力)。
///
/// 给两个量:
///   slope_per_run : 最小二乘斜率 (单位/轮), 以及它占均值的百分比
///   monotonic     : 相邻递增的比例。5 个点 4 个间隔, 全增 = 1.0, 全减 = 0.0,
///                   纯噪声期望 0.5。偏离 0.5 越远越像系统性漂移。
pub fn drift(xs: &[Num]) -> PyVal {
    let n = xs.len();
    if n < 3 {
        return pyobj! {
            "slope_per_run" => PyVal::Null,
            "slope_pct_per_run" => PyVal::Null,
            "monotonic_frac" => PyVal::Null,
        };
    }
    let mx = py_sum((0..n).map(|i| i as f64)) / n as f64;
    let my = mean(xs);
    let den: f64 = py_sum((0..n).map(|i| (i as f64 - mx).powi(2)));
    let slope = if den != 0.0 {
        py_sum((0..n).map(|i| (i as f64 - mx) * (xs[i].f() - my))) / den
    } else {
        0.0
    };
    let ups = (1..n).filter(|&i| xs[i].f() > xs[i - 1].f()).count();
    pyobj! {
        "slope_per_run" => py_round(slope, 5),
        "slope_pct_per_run" => if my != 0.0 { PyVal::Float(py_round(slope / my * 100.0, 4)) } else { PyVal::Null },
        "monotonic_frac" => py_round(ups as f64 / (n - 1) as f64, 3),
    }
}

fn var(xs: &[Num]) -> f64 {
    if xs.len() < 2 {
        return 0.0;
    }
    let m = mean(xs);
    py_sum(xs.iter().map(|x| (x.f() - m).powi(2))) / (xs.len() - 1) as f64
}

/// (mean_b - mean_a) / 合并标准差。合并标准差为 0 时无定义。
pub fn cohens_d(a: &[Num], b: &[Num]) -> Option<f64> {
    let (na, nb) = (a.len(), b.len());
    if na < 2 || nb < 2 {
        return None;
    }
    let sp2 = ((na - 1) as f64 * var(a) + (nb - 1) as f64 * var(b)) / (na + nb - 2) as f64;
    if sp2 <= 0.0 {
        return None;
    }
    Some((mean(b) - mean(a)) / sp2.sqrt())
}

/// 两样本 HL 估计 = 所有跨组差 (b_j - a_i) 的中位数。抗离群, 与置换检验同族。
pub fn hodges_lehmann(a: &[Num], b: &[Num]) -> Option<Num> {
    let diffs: Vec<Num> = a.iter().flat_map(|x| b.iter().map(move |y| y.sub(*x))).collect();
    median(&diffs)
}

// ══════════════ 精确置换检验 ══════════════

/// 按字典序枚举 `0..n` 中取 k 个的所有组合, 逐个交给 `f`。
/// 与 Python `itertools.combinations` 同序、同元素顺序 —— 组内保持升序索引,
/// 求和顺序因此一致, 浮点结果才能逐位对上。
fn for_each_combination(n: usize, k: usize, mut f: impl FnMut(&[usize])) {
    if k > n {
        return;
    }
    let mut idx: Vec<usize> = (0..k).collect();
    loop {
        f(&idx);
        if k == 0 {
            return;
        }
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

/// n 对 n 的精确双侧置换检验里可能达到的**最小** p 值: 2 / C(2n, n)。
///
/// 用途是结构性防护: p 值不可能比这个更小, 所以当它已经不满足 alpha 时,
/// 这个样本量下任何效应量都判不出「显著」。2026-10-21 实测到的一次事故就是
/// 3v3 (min p = 0.1) 去判 alpha_win = 0.025, 一道预期 −14.7% 的考题被判成「无变化」。
pub fn min_two_sided_p(n_per_arm: usize) -> f64 {
    if n_per_arm == 0 {
        return 1.0;
    }
    // C(2n, n) 用 f64 连乘, n 很大时自然很大 —— 这里只用于几十以内, 精度够
    let n2 = 2 * n_per_arm;
    let mut c = 1.0_f64;
    for i in 0..n_per_arm {
        c = c * (n2 - i) as f64 / (i + 1) as f64;
        if !c.is_finite() {
            // C(2n,n) 超出 double (n 约 >= 515): 真值小到 0 附近, 提前返回别把进程卡在大循环里
            return 0.0;
        }
    }
    (2.0 / c).min(1.0)
}

/// 「改善」一侧最严会收紧到几个指标: 帧时 p95、平均帧率, 再加上可能进判定的整机功耗。
/// 功耗进不进判定要等数据出来 (两臂是否全程放电态) 才知道, 开跑前的防护只能按最严的算。
pub const MAX_WIN_METRICS: usize = 3;

/// n v n 精确置换检验要判得出 p < `alpha_win`, 每臂至少要几轮。
/// 64 以内找不到就返回 None (alpha_win 小得离谱, 不是这里该猜的)。
pub fn min_pairs_for(alpha_win: f64) -> Option<usize> {
    (2..=64).find(|n| min_two_sided_p(*n) < alpha_win)
}

/// 结构性防护: n v n 的样本量在数学上判不出 p < `alpha_win` 时拒绝开跑。
///
/// `what` 是给人看的出处 (如 `autoloop --pairs`), 报错里会告诉调用方最少要给几轮。
/// selftest / eval / autoloop 都走这一个函数, 不各自再算一遍 2/C(2n,n)。
pub fn ensure_resolvable(n_per_arm: usize, alpha_win: f64, what: &str) -> Result<(), String> {
    let p_min = min_two_sided_p(n_per_arm);
    if p_min < alpha_win {
        return Ok(());
    }
    let need = match min_pairs_for(alpha_win) {
        Some(k) => format!("{what} 至少给 {k}"),
        None => format!("alpha_win={alpha_win} 太小, 64 轮以内都够不着, 先检查显著性门槛"),
    };
    Err(format!(
        "{n_per_arm}v{n_per_arm} 的样本量判不出显著改善: 精确置换检验的最小双侧 p = 2/C({},{}) = {p_min:.4}, \
而显著改善要求 p < {alpha_win:.4}。{need}, 否则这次的结论只能是「没有显著变化」, 跑多久都一样",
        2 * n_per_arm,
        n_per_arm,
    ))
}

/// 均值差的精确双侧置换 p 值。
///
/// 把 a+b 合并后穷举所有"取 len(a) 个作为 A 组"的分法, 统计 |均值差| 不小于
/// 实测值的比例。返回 (p, 枚举总数)。
pub fn perm_p_two_sided(a: &[Num], b: &[Num]) -> (f64, usize) {
    let mut pool: Vec<Num> = Vec::with_capacity(a.len() + b.len());
    pool.extend_from_slice(a);
    pool.extend_from_slice(b);
    let na = a.len();
    let obs = (mean(b) - mean(a)).abs();
    let n = pool.len();
    let mut total = 0usize;
    let mut hit = 0usize;
    let mut in_a = vec![false; n];
    for_each_combination(n, na, |combo| {
        in_a.iter_mut().for_each(|v| *v = false);
        for &i in combo {
            in_a[i] = true;
        }
        // 两组各自按池内升序累加 —— 与 Python 里
        // `mean([pool[i] for i in idx if i in cs])` 的元素顺序、类型提升都一致
        let (mut sa, mut sb) = (PySum::default(), PySum::default());
        let mut nb = 0usize;
        for (i, &v) in pool.iter().enumerate() {
            if in_a[i] {
                sa.push(v);
            } else {
                sb.push(v);
                nb += 1;
            }
        }
        total += 1;
        // 浮点容差: 等于观测值的那些分法应当计入 (置换检验惯例)
        let d = (sb.total() / nb as f64 - sa.total() / na as f64).abs();
        if d >= obs - 1e-12 {
            hit += 1;
        }
    });
    (hit as f64 / total as f64, total)
}

/// 置换检验反演求 (mean_b - mean_a) 的 (1-alpha) 置信区间。
///
/// 对候选位移 δ, 检验 "b - δ 与 a 同分布"; 所有不被拒绝的 δ 构成 CI。
/// 在一个足够宽的网格上扫描 (覆盖观测差的 ±4 倍全距), 取首尾。
/// 网格是固定的等分点, 因此结果确定, 不含随机。
pub fn perm_ci(a: &[Num], b: &[Num], alpha: f64, steps: usize) -> Option<(f64, f64)> {
    let obs = mean(b) - mean(a);
    let all: Vec<Num> = a.iter().chain(b.iter()).copied().collect();
    let hi = max_of(&all)?.f();
    let lo = min_of(&all)?.f();
    // Python 的 `(max - min) or 1.0`: 全距为 0 时退回 1.0
    let spread = if hi - lo == 0.0 { 1.0 } else { hi - lo };
    let (lo_bound, hi_bound) = (obs - 4.0 * spread, obs + 4.0 * spread);
    let mut accepted: Vec<f64> = Vec::new();
    // `y - d` 里 d 一定是 float, 所以位移后的 B 臂整列都是 float
    let mut shifted = vec![Num::Float(0.0); b.len()];
    for i in 0..=steps {
        // 与 Python 的 `lo + (hi-lo)*i/steps` 同一个运算顺序
        let d = lo_bound + (hi_bound - lo_bound) * i as f64 / steps as f64;
        for (s, y) in shifted.iter_mut().zip(b) {
            *s = Num::Float(y.f() - d);
        }
        if perm_p_two_sided(a, &shifted).0 > alpha {
            accepted.push(d);
        }
    }
    if accepted.is_empty() {
        return None;
    }
    Some((
        accepted.iter().copied().fold(f64::INFINITY, f64::min),
        accepted.iter().copied().fold(f64::NEG_INFINITY, f64::max),
    ))
}

// ══════════════ 载入 ══════════════

/// 一轮: (标签, summary.json 的内容)
pub type Run = (String, PyVal);

/// Python `glob.glob` 的最小复刻: 逐路径段匹配 `*` / `?` / `[...]`,
/// `*` 不跨 `/`, 也不匹配以 `.` 开头的名字。结果按路径字符串排序 (调用方的 `sorted()`)。
fn glob_paths(pattern: &str) -> Vec<String> {
    let absolute = pattern.starts_with('/');
    let segs: Vec<&str> = pattern
        .trim_start_matches('/')
        .split('/')
        .filter(|s| !s.is_empty())
        .collect();
    let mut cur: Vec<String> = vec![if absolute { "/".into() } else { String::new() }];
    for seg in segs {
        let mut next = Vec::new();
        let magic = seg.contains(['*', '?', '[']);
        for base in &cur {
            if !magic {
                let joined = join(base, seg);
                if Path::new(&joined).exists() {
                    next.push(joined);
                }
                continue;
            }
            let dir = if base.is_empty() { "." } else { base.as_str() };
            let Ok(rd) = std::fs::read_dir(dir) else {
                continue;
            };
            let mut hits: Vec<String> = rd
                .filter_map(|e| e.ok())
                .filter_map(|e| e.file_name().to_str().map(String::from))
                // glob 的老规矩: 通配符不吃隐藏文件
                .filter(|n| !n.starts_with('.') && fnmatch(n, seg))
                .map(|n| join(base, &n))
                .collect();
            hits.sort();
            next.extend(hits);
        }
        cur = next;
    }
    cur.sort();
    cur
}

fn join(base: &str, name: &str) -> String {
    match base {
        "" => name.to_string(),
        "/" => format!("/{name}"),
        b => format!("{b}/{name}"),
    }
}

/// 单个路径段的通配匹配: `*` 任意串、`?` 单字符、`[abc]` / `[!abc]` 字符类。
pub fn fnmatch(name: &str, pat: &str) -> bool {
    let (n, p): (Vec<char>, Vec<char>) = (name.chars().collect(), pat.chars().collect());
    fn go(n: &[char], p: &[char]) -> bool {
        match p.first() {
            None => n.is_empty(),
            Some('*') => (0..=n.len()).any(|i| go(&n[i..], &p[1..])),
            Some('?') => !n.is_empty() && go(&n[1..], &p[1..]),
            Some('[') => {
                let Some(close) = p.iter().position(|c| *c == ']').filter(|i| *i > 1) else {
                    // 没有闭合方括号时按字面量处理 (与 fnmatch 一致)
                    return !n.is_empty() && n[0] == '[' && go(&n[1..], &p[1..]);
                };
                if n.is_empty() {
                    return false;
                }
                let (neg, set) = match p[1] {
                    '!' => (true, &p[2..close]),
                    _ => (false, &p[1..close]),
                };
                let mut hit = false;
                let mut i = 0;
                while i < set.len() {
                    if i + 2 < set.len() && set[i + 1] == '-' {
                        if set[i] <= n[0] && n[0] <= set[i + 2] {
                            hit = true;
                        }
                        i += 3;
                    } else {
                        if set[i] == n[0] {
                            hit = true;
                        }
                        i += 1;
                    }
                }
                hit != neg && go(&n[1..], &p[close + 1..])
            }
            Some(c) => !n.is_empty() && n[0] == *c && go(&n[1..], &p[1..]),
        }
    }
    go(&n, &p)
}

/// 模式命中目录就取其下的 summary.json, 命中文件就直接用。
/// 标签取该文件父目录的名字 —— 与 Python `basename(dirname(f))` 一致。
pub fn load_runs(pattern: &str) -> Vec<Run> {
    let mut out = Vec::new();
    for p in glob_paths(pattern) {
        let f = if Path::new(&p).is_dir() {
            join(&p, "summary.json")
        } else {
            p.clone()
        };
        if !Path::new(&f).exists() {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&f) else {
            continue;
        };
        let Ok(v) = pyjson::loads(&text) else {
            continue;
        };
        let label = Path::new(&f)
            .parent()
            .and_then(|d| d.file_name())
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_string();
        out.push((label, v));
    }
    out
}

// ══════════════ 描述与对比 ══════════════

pub(crate) fn column(runs: &[Run], metric: &str) -> Vec<Num> {
    runs.iter()
        .filter_map(|r| r.1.get(metric).and_then(Num::from_json))
        .collect()
}

pub fn describe(runs: &[Run], title: &str) -> PyVal {
    let mut metrics: Vec<(String, PyVal)> = Vec::new();
    for m in METRICS {
        let xs = column(runs, m);
        if xs.is_empty() {
            continue;
        }
        let disp = dispersion(&xs);
        metrics.push((
            m.to_string(),
            pyobj! {
                "values" => PyVal::List(xs.iter().map(|x| PyVal::from(*x)).collect()),
                "median" => median(&xs).map(|v| v.round(4)),
                "mean" => py_round(mean(&xs), 4),
                "min" => min_of(&xs).map(|v| v.round(4)),
                "max" => max_of(&xs).map(|v| v.round(4)),
                "dispersion_pct" => disp.map(|d| py_round(d * 100.0, 3)),
                "drift" => drift(&xs),
            },
        ));
    }
    pyobj! {
        "title" => title,
        "n_runs" => runs.len(),
        "runs" => runs.iter().map(|r| r.0.clone()).collect::<Vec<_>>(),
        "metrics" => PyVal::Obj(metrics),
    }
}

pub fn compare(a_runs: &[Run], b_runs: &[Run], metric: &str) -> PyVal {
    compare_with(a_runs, b_runs, metric, true)
}

/// 同 [`compare`], 但不算置换置信区间 (`ci95` 为 null)。
///
/// 判定 (`sysparam::decide`) 只读 p 值与均值方向, 不读 `ci95`; 区间要再做 401 次精确置换,
/// 10v10 时一次就是 7400 万次排列。selftest 注入法要在同一份数据上扫几十档效应,
/// 只要判定结论, 走这条。p 值与其余字段和 [`compare`] 逐字段相同。
pub fn compare_no_ci(a_runs: &[Run], b_runs: &[Run], metric: &str) -> PyVal {
    compare_with(a_runs, b_runs, metric, false)
}

fn compare_with(a_runs: &[Run], b_runs: &[Run], metric: &str, with_ci: bool) -> PyVal {
    let (a, b) = (column(a_runs, metric), column(b_runs, metric));
    if a.len() < 2 || b.len() < 2 {
        return pyobj! { "metric" => metric, "error" => "样本不足" };
    }
    let (p, total) = perm_p_two_sided(&a, &b);
    let ci = if with_ci { perm_ci(&a, &b, 0.05, 400) } else { None };
    let (ma, mb) = (mean(&a), mean(&b));
    pyobj! {
        "metric" => metric,
        "a_values" => PyVal::List(a.iter().map(|x| PyVal::from(*x)).collect()),
        "b_values" => PyVal::List(b.iter().map(|x| PyVal::from(*x)).collect()),
        "a_mean" => py_round(ma, 4),
        "b_mean" => py_round(mb, 4),
        "diff_mean" => py_round(mb - ma, 4),
        "diff_pct" => if ma != 0.0 { PyVal::Float(py_round((mb - ma) / ma * 100.0, 3)) } else { PyVal::Null },
        "hodges_lehmann" => hodges_lehmann(&a, &b).map(|v| v.round(4)),
        "cohens_d" => cohens_d(&a, &b).map(|d| py_round(d, 4)),
        "perm_p_two_sided" => py_round(p, 6),
        "perm_enumerated" => total,
        "ci95" => match ci {
            Some((lo, hi)) => PyVal::List(vec![PyVal::Float(py_round(lo, 4)), PyVal::Float(py_round(hi, 4))]),
            None => PyVal::Null,
        },
        "significant_at_0.05" => p < 0.05,
    }
}

// ══════════════ 子命令 ══════════════

const USAGE: &str = "用法: phonefarm analyze <A臂glob> [B臂glob] [--metric M]";

pub fn run_analyze(args: &[String]) -> i32 {
    if args.is_empty() {
        eprintln!("{USAGE}");
        return 2;
    }
    let mut metric = "frame_p95".to_string();
    if let Some(i) = args.iter().position(|a| a == "--metric") {
        if let Some(v) = args.get(i + 1) {
            metric = v.clone();
        }
    }
    let a_runs = load_runs(&args[0]);
    if a_runs.is_empty() {
        eprintln!("没找到任何 summary.json: {}", args[0]);
        return 1;
    }
    let mut out = vec![("arm_a".to_string(), describe(&a_runs, &args[0]))];

    // 与 Python 版同一套取法: argv[2:] 里所有不以 `--` 开头的词, 第一个当 B 臂。
    // 注意这会把 `--metric` 的**取值**也算进来 —— 旧版就是这个行为, 这里照搬不改,
    // 免得同一条命令在新旧两版下出不同的报告。
    let pos: Vec<&String> = args[1..].iter().filter(|x| !x.starts_with("--")).collect();
    if let Some(bpat) = pos.first() {
        let b_runs = load_runs(bpat);
        if !b_runs.is_empty() {
            out.push(("arm_b".to_string(), describe(&b_runs, bpat)));
            out.push((
                "comparison".to_string(),
                PyVal::Obj(
                    COMPARE_METRICS
                        .iter()
                        .map(|m| (m.to_string(), compare(&a_runs, &b_runs, m)))
                        .collect(),
                ),
            ));
            out.push(("primary_metric".to_string(), PyVal::Str(metric)));
        }
    }
    println!("{}", dumps(&PyVal::Obj(out)));
    0
}

// ══════════════ 给尚未搬迁的 Python 用的过渡通道 ══════════════

/// 从请求里取「有序的 [键, 值] 对列表」。
///
/// 为什么不用 JSON 对象: serde_json 默认用 BTreeMap 存对象, 一解析键就按字典序排好了,
/// 而 Python 的 dict 是插入序 —— 候选参数的遍历顺序决定了多处违规时先报哪一条,
/// 白名单的插入顺序也影响不了别的但一样该守住。所以过桥一律走 pair 列表。
fn pairs_arg(req: &PyVal, key: &str) -> Vec<(String, String)> {
    let Some(PyVal::List(a)) = req.get(key) else {
        return Vec::new();
    };
    a.iter()
        .filter_map(|e| {
            let PyVal::List(p) = e else { return None };
            let PyVal::Str(k) = p.first()? else { return None };
            Some((k.clone(), p.get(1)?.py_str()))
        })
        .collect()
}

/// 从请求里还原白名单 (同样走有序 pair 列表)。
fn wl_arg(req: &PyVal) -> sysparam::Whitelist {
    let Some(PyVal::List(a)) = req.get("wl") else {
        return Vec::new();
    };
    let text = |v: Option<&PyVal>| match v {
        Some(PyVal::Str(s)) => s.clone(),
        _ => String::new(),
    };
    a.iter()
        .filter_map(|e| {
            let PyVal::List(p) = e else { return None };
            let PyVal::Str(pid) = p.first()? else { return None };
            let s = p.get(1)?;
            Some((
                pid.clone(),
                sysparam::Spec {
                    kind: text(s.get("kind")),
                    path: text(s.get("path")),
                    values: match s.get("values") {
                        Some(PyVal::List(v)) => v.iter().map(|x| x.py_str()).collect(),
                        _ => Vec::new(),
                    },
                    current: text(s.get("current")),
                    group: match s.get("group") {
                        Some(PyVal::Str(g)) => Some(g.clone()),
                        _ => None,
                    },
                    effect: text(s.get("effect")),
                },
            ))
        })
        .collect()
}

/// `phonefarm loopstat`: 从 stdin 读一个 JSON 请求, 往 stdout 写一个 JSON 结果。
///
/// 只为迁移期存在。`report.py` / `refbench_report.py` / `autoloop.py` 还在把
/// analyze 当库用, 但口径只能有一份 —— 与其让 Python 侧留一套副本慢慢漂移,
/// 不如让它们都转调这里。等它们各自搬完, 这个子命令跟着 `loop_v1/tools/pybridge.py`
/// 一起删掉。
pub fn run_loopstat(_args: &[String]) -> i32 {
    let mut buf = String::new();
    if std::io::Read::read_to_string(&mut std::io::stdin(), &mut buf).is_err() {
        eprintln!("读不到 stdin");
        return 1;
    }
    let req = match pyjson::loads(&buf) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("请求不是合法 JSON: {e}");
            return 1;
        }
    };
    let op = match req.get("op") {
        Some(PyVal::Str(s)) => s.clone(),
        _ => String::new(),
    };
    let arr = |k: &str| -> &[PyVal] {
        match req.get(k) {
            Some(PyVal::List(a)) => a.as_slice(),
            _ => &[],
        }
    };
    let nums = |k: &str| -> Vec<Num> { arr(k).iter().filter_map(Num::from_json).collect() };
    let runs = |k: &str| -> Vec<Run> {
        arr(k)
            .iter()
            .filter_map(|e| {
                let PyVal::List(p) = e else { return None };
                let PyVal::Str(label) = p.first()? else { return None };
                Some((label.clone(), p.get(1)?.clone()))
            })
            .collect()
    };
    let s = |k: &str| match req.get(k) {
        Some(PyVal::Str(v)) => v.clone(),
        _ => String::new(),
    };

    let out = match op.as_str() {
        "mean" => PyVal::Float(mean(&nums("xs"))),
        "dispersion" => dispersion(&nums("xs")).into(),
        "drift" => drift(&nums("xs")),
        "load_runs" => PyVal::List(
            load_runs(&s("pattern"))
                .into_iter()
                .map(|(l, v)| PyVal::List(vec![PyVal::Str(l), v]))
                .collect(),
        ),
        "describe" => describe(&runs("runs"), &s("title")),
        "compare" => compare(&runs("a"), &runs("b"), &s("metric")),
        "snapshot_diff" => crate::loopreport::snapshot_diff(&s("before"), &s("after")),
        // ── 系统参数闭环 (src/sysparam.rs) ──
        "build_whitelist" => sysparam::whitelist_to_pyval(&sysparam::build_whitelist(
            &sysparam::parse_probe(&s("probe_text")))),
        "describe_whitelist" => PyVal::Str(sysparam::describe_whitelist(&wl_arg(&req))),
        "validate_candidate" => {
            let (ok, why) = sysparam::validate_candidate(&pairs_arg(&req, "cand"), &wl_arg(&req));
            PyVal::List(vec![PyVal::Bool(ok), PyVal::Str(why)])
        }
        "plan_text" => match sysparam::plan_text(&pairs_arg(&req, "cand"), &wl_arg(&req)) {
            Ok(t) => PyVal::Str(t),
            Err(e) => {
                eprintln!("{e}");
                return 1;
            }
        },
        // 数字原样带过去 (int 进 int 出), 不在桥上做类型提升
        "rule_doc" => sysparam::rule_doc(
            req.get("temp_cap_c").unwrap_or(&PyVal::Null),
            req.get("pairs").unwrap_or(&PyVal::Null),
            req.get("power_available").and_then(|v| v.as_bool()).unwrap_or(false),
            &s("power_note")),
        "env_stats" => sysparam::env_stats(&s("text")),
        // 取窗口径: trace 只需给头部 (#loop_v1_meta 行), 用来离线重算已存的臂
        "env_stats_window" => sysparam::env_stats_window(
            &s("text"), sysparam::capture_window_from_trace(&s("trace_head"))),
        // ── 模型侧 (src/llm.rs) ──
        "load_keys" => {
            match llm::load_keys(&arr("paths").iter().map(|p| p.py_str()).collect::<Vec<_>>()) {
                Ok(kv) => PyVal::Obj(kv.into_iter().map(|(k, v)| (k, PyVal::Str(v))).collect()),
                Err(e) => {
                    eprintln!("{e}");
                    return 1;
                }
            }
        }
        "build_prompt" => PyVal::Str(llm::build_prompt(
            &s("whitelist_desc"),
            match req.get("history") { Some(PyVal::List(h)) => h, _ => &[] },
            req.get("n").and_then(|v| v.as_i64()).unwrap_or(0) as usize,
            &s("goal_note"))),
        "parse_candidates" => PyVal::List(
            llm::parse_candidates(&s("text"), req.get("n").and_then(|v| v.as_i64()).unwrap_or(0) as usize)
                .iter().map(|c| c.to_pyval()).collect()),
        "local_mutate" => PyVal::List(
            llm::local_mutate(
                &wl_arg(&req),
                match req.get("history") { Some(PyVal::List(h)) => h, _ => &[] },
                req.get("n").and_then(|v| v.as_i64()).unwrap_or(0) as usize,
                req.get("seed").and_then(|v| v.as_i64()).unwrap_or(0),
            ).iter().map(|c| c.to_pyval()).collect()),
        "llm_chat" => {
            let keys: Vec<(String, String)> = match req.get("keys") {
                Some(PyVal::Obj(kvs)) => kvs.iter().map(|(k, v)| (k.clone(), v.py_str())).collect(),
                _ => Vec::new(),
            };
            let tmp = if s("tmp_dir").is_empty() {
                std::env::temp_dir().to_string_lossy().to_string()
            } else {
                s("tmp_dir")
            };
            let (got, log) = llm::chat_with_log(&s("prompt"), &keys, &tmp);
            pyobj! {
                "provider" => got.as_ref().map(|(p, _)| p.clone()),
                "content" => got.as_ref().map(|(_, c)| c.clone()),
                "log" => log,
            }
        }
        // 画面动没动: 裸帧走文件路径, 不塞进 JSON (13MB 一张, base64 过桥不划算)
        // 读不到裸帧也按"没在转"回一个带 error 的结论, 而不是非零退出 ——
        // 上游 autoloop 靠这个 dict 落证据再退出 3, 抛异常会把那条路绕掉。
        "spin_verdict" => match (std::fs::read(s("frame_a")), std::fs::read(s("frame_b"))) {
            (Ok(a), Ok(b)) => crate::framecheck::verdict(&a, &b),
            (Err(e), _) | (_, Err(e)) => crate::framecheck::read_error(&e.to_string()),
        },
        "decide" => sysparam::decide(
            &req.get("comparisons").cloned().unwrap_or(PyVal::Null),
            &sysparam::DecideCtx {
                temp_max_c: req.get("temp_max_c").cloned().unwrap_or(PyVal::Null),
                temp_cap_c: req.get("temp_cap_c").cloned().unwrap_or(PyVal::Null),
                apply_ok: req.get("apply_ok").and_then(|v| v.as_bool()).unwrap_or(false),
                snapshot_identical: req.get("snapshot_identical").and_then(|v| v.as_bool()).unwrap_or(false),
                power_available: req.get("power_available").and_then(|v| v.as_bool()).unwrap_or(false),
            }),
        other => {
            eprintln!("不认识的 op: {other}");
            return 2;
        }
    };
    println!("{}", dumps(&out));
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn repo_root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
    }

    fn n(xs: &[f64]) -> Vec<Num> {
        xs.iter().map(|x| Num::Float(*x)).collect()
    }

    /// 5v5 精确置换检验就是 C(10,5)=252 种分法, 一种不多一种不少。    /// 样本量能判到什么: 3v3 的最小 p 是 0.1, 对 alpha_win=0.025 根本不够。
    #[test]
    fn min_two_sided_p_gates_the_sample_size() {
        assert!((min_two_sided_p(3) - 0.1).abs() < 1e-12, "{}", min_two_sided_p(3));
        assert!((min_two_sided_p(5) - 2.0 / 252.0).abs() < 1e-12);
        assert!(min_two_sided_p(5) < 0.025);
        assert!(min_two_sided_p(4) > 0.025, "4v4 的最小 p = 2/70 = 0.0286, 仍不够");
        assert_eq!(min_two_sided_p(0), 1.0);
        // 单调下降
        assert!(min_two_sided_p(10) < min_two_sided_p(8));
    }

    /// 防护函数: 给出最少轮数, 而且对 alpha/2 与 alpha/3 两种 Bonferroni 口径都是 5。
    #[test]
    fn ensure_resolvable_names_the_minimum_pairs() {
        assert_eq!(min_pairs_for(0.05 / 2.0), Some(5));
        assert_eq!(min_pairs_for(0.05 / 3.0), Some(5));
        // p_threshold=0.01 时 (gpu-op 的生产口径) 要 6 轮: 2/C(12,6) = 0.00216 < 0.0033
        assert_eq!(min_pairs_for(0.01 / 3.0), Some(6));
        assert!(ensure_resolvable(5, 0.05 / 3.0, "--pairs").is_ok());
        let e = ensure_resolvable(3, 0.05 / 3.0, "--pairs").unwrap_err();
        assert!(e.contains("3v3") && e.contains("0.1000") && e.contains("--pairs 至少给 5"), "{e}");
        let e = ensure_resolvable(4, 0.025, "protocol.rounds").unwrap_err();
        assert!(e.contains("protocol.rounds 至少给 5"), "{e}");
        assert!(ensure_resolvable(2, 1e-60, "x").unwrap_err().contains("太小"));
        // 0 轮/1 轮也拒
        assert!(ensure_resolvable(0, 0.5, "x").is_err());
        assert!(ensure_resolvable(1, 0.5, "x").is_err());
    }


    #[test]
    fn permutation_enumerates_every_split() {
        let (p, total) = perm_p_two_sided(&n(&[1.0, 2.0, 3.0, 4.0, 5.0]), &n(&[6.0, 7.0, 8.0, 9.0, 10.0]));
        assert_eq!(total, 252);
        assert_eq!(py_round(p, 6), 0.007937);
    }

    #[test]
    fn combinations_match_itertools_order() {
        let mut got = Vec::new();
        for_each_combination(4, 2, |c| got.push(c.to_vec()));
        assert_eq!(
            got,
            vec![
                vec![0, 1],
                vec![0, 2],
                vec![0, 3],
                vec![1, 2],
                vec![1, 3],
                vec![2, 3]
            ]
        );
        let mut count = 0;
        for_each_combination(10, 5, |_| count += 1);
        assert_eq!(count, 252);
    }

    /// int 进 int 出: `round(3250, 4)` 在 Python 里还是 `3250`, 不是 `3250.0`。
    /// report.json 里两臂的 bw_median 一个是 int 一个是 float, 这条不照搬就对不上字节。
    #[test]
    fn int_metrics_stay_int_through_round() {
        let xs = vec![Num::Int(3150), Num::Int(3250), Num::Int(3250), Num::Int(3300), Num::Int(3200)];
        assert_eq!(median(&xs).unwrap().round(4), Num::Int(3250));
        assert_eq!(min_of(&xs).unwrap().round(4), Num::Int(3150));
        assert_eq!(dumps(&PyVal::from(median(&xs).unwrap())), "3250");
        // 均值是真除法, 必为 float
        assert_eq!(dumps(&PyVal::Float(py_round(mean(&xs), 4))), "3230.0");
    }

    /// 跨组差沾上 float 就是 float —— HL 估计因此是 50.0 而不是 50。
    #[test]
    fn hodges_lehmann_promotes_like_python() {
        let a = vec![Num::Int(3150), Num::Int(3250), Num::Int(3250)];
        let b = vec![Num::Float(3250.0), Num::Float(3300.0), Num::Float(3300.0)];
        assert_eq!(dumps(&PyVal::from(hodges_lehmann(&a, &b).unwrap())), "50.0");
        let both_int = vec![Num::Int(1), Num::Int(2), Num::Int(3)];
        assert_eq!(hodges_lehmann(&both_int, &both_int).unwrap(), Num::Int(0));
    }

    /// CPython `sum()` 的三段式: 整数精确段 → 第一个 float 朴素相加 → 此后补偿。
    /// 三个对照值都是本机 python3 的实际输出。
    #[test]
    fn sum_matches_cpython_promotion() {
        // 开头是 int: 那一步不补偿, 结果比全程补偿多出末位误差
        let xs = vec![Num::Int(5230), Num::Float(3335.9446), Num::Float(4504.6), Num::Float(598.3)];
        assert_eq!(sum_nums(&xs), 13668.844599999999);
        // 全是 float: 全程补偿
        let ys = vec![Num::Float(1667.8111), Num::Float(3130.61), Num::Float(657.0251), Num::Float(414.0)];
        assert_eq!(sum_nums(&ys), 5869.4462);
        assert_ne!(sum_nums(&ys), 1667.8111 + 3130.61 + 657.0251 + 414.0);
        // 纯 int: 精确整数求和
        assert_eq!(sum_nums(&[Num::Int(3150), Num::Int(3250), Num::Int(3250), Num::Int(3300), Num::Int(3200)]), 16150.0);
    }

    #[test]
    fn dispersion_and_drift_edge_cases() {
        assert_eq!(dispersion(&n(&[1.0])), None);
        assert_eq!(dispersion(&[Num::Int(0), Num::Int(0)]), None);
        assert_eq!(dumps(&drift(&n(&[1.0, 2.0]))), dumps(&pyobj! {
            "slope_per_run" => PyVal::Null,
            "slope_pct_per_run" => PyVal::Null,
            "monotonic_frac" => PyVal::Null,
        }));
        let d = dumps(&drift(&n(&[1.0, 2.0, 3.0, 4.0, 5.0])));
        assert!(d.contains("\"monotonic_frac\": 1.0"), "{d}");
    }

    #[test]
    fn cohens_d_undefined_when_no_spread() {
        assert_eq!(cohens_d(&n(&[1.0, 1.0]), &n(&[1.0, 1.0])), None);
        assert_eq!(cohens_d(&n(&[1.0]), &n(&[1.0, 2.0])), None);
    }

    #[test]
    fn fnmatch_matches_python_glob_rules() {
        assert!(fnmatch("ctrl1", "ctrl*"));
        assert!(!fnmatch("ctrl1", "knob*"));
        assert!(fnmatch("a", "?"));
        assert!(fnmatch("b", "[abc]"));
        assert!(!fnmatch("d", "[abc]"));
        assert!(fnmatch("d", "[!abc]"));
        assert!(fnmatch("c", "[a-d]"));
        assert!(fnmatch("night1", "night*"));
        assert!(fnmatch("x", "*"));
        assert!(fnmatch("", "*"));
    }

    /// 通配符不吃隐藏目录 —— 否则 `runs/*` 会把 `.DS_Store` 之流也当成一轮。
    #[test]
    fn glob_skips_hidden_entries() {
        let d = std::env::temp_dir().join(format!("pf-glob-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(d.join(".hidden")).unwrap();
        std::fs::create_dir_all(d.join("shown")).unwrap();
        let got = glob_paths(&format!("{}/*", d.display()));
        assert_eq!(got.len(), 1, "{got:?}");
        assert!(got[0].ends_with("shown"));
        let _ = std::fs::remove_dir_all(&d);
    }

    /// 对照源: `loop_v1/fixtures/golden/runs/` 的 21 轮真实录制 —— 旧 Python 版在同一批 summary.json
    /// 上产出的 describe / compare, 与这里逐字节比。
    #[test]
    fn compare_no_ci_differs_only_in_the_interval() {
        let mk = |xs: &[f64]| -> Vec<Run> {
            xs.iter()
                .enumerate()
                .map(|(i, x)| (format!("r{i}"), PyVal::Obj(vec![("frame_p95".to_string(), PyVal::Float(*x))])))
                .collect()
        };
        let (a, b) = (mk(&[10.0, 10.2, 9.9, 10.1, 10.05]), mk(&[9.6, 9.7, 9.65, 9.8, 9.55]));
        let full = compare(&a, &b, "frame_p95");
        let fast = compare_no_ci(&a, &b, "frame_p95");
        let strip = |v: &PyVal| match v {
            PyVal::Obj(kv) => kv.iter().filter(|(k, _)| k != "ci95").cloned().collect::<Vec<_>>(),
            _ => panic!("compare 应返回对象"),
        };
        assert_eq!(strip(&full), strip(&fast));
        assert!(matches!(fast.get("ci95"), Some(PyVal::Null)));
        assert!(!matches!(full.get("ci95"), Some(PyVal::Null)));
    }

    #[test]
    fn analyze_matches_recorded_golden() {
        let dir = repo_root().join("loop_v1/fixtures");
        let want = std::fs::read_to_string(dir.join("analyze_ctrl_knob.json")).unwrap();
        let runs = repo_root().join("loop_v1/fixtures/golden/runs");
        let a = load_runs(&format!("{}/ctrl*", runs.display()));
        let b = load_runs(&format!("{}/knob*", runs.display()));
        assert_eq!(a.len(), 5);
        assert_eq!(b.len(), 5);
        let mut out = vec![("arm_a".to_string(), describe(&a, "ctrl*"))];
        out.push(("arm_b".to_string(), describe(&b, "knob*")));
        out.push((
            "comparison".to_string(),
            PyVal::Obj(
                COMPARE_METRICS
                    .iter()
                    .map(|m| (m.to_string(), compare(&a, &b, m)))
                    .collect(),
            ),
        ));
        out.push(("primary_metric".to_string(), PyVal::Str("frame_p95".into())));
        assert_eq!(dumps(&PyVal::Obj(out)), want.trim_end_matches('\n'));
    }
}
