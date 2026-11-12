//! gpustat: A/B 对照的冻结判定统计。
//!
//! 为什么要自带实现: 仓库里此前没有任何显著性检验 —— loop_v1 的判定走 Python/scipy,
//! 而 `gpu-op` 必须是零依赖的 Rust 通路 (跑在没有 Python 的环境里也要能裁决)。
//!
//! 口径固定为 **Welch 两样本 t 检验 (不假设等方差)**。为什么不用 Student 合并方差:
//! A/B 两组是两段不同的 GPU 负载, 方差本来就不同 —— 候选算子更重时轮间抖动也更大。
//! 合并方差在方差不等时会低估 p 值, 也就是把噪声判成显著, 正是本项目最要防的那件事。
//!
//! 判定规则在**看到候选数据之前**就已冻结 (`FrozenRules` 由 eval_request 携带),
//! 事后不得调整。

// ══════════════ 基础统计 ══════════════

pub fn mean(v: &[f64]) -> f64 {
    if v.is_empty() {
        return f64::NAN;
    }
    v.iter().sum::<f64>() / v.len() as f64
}

/// 样本方差 (n-1 分母)。样本数 < 2 时无定义。
pub fn sample_variance(v: &[f64]) -> f64 {
    let n = v.len();
    if n < 2 {
        return f64::NAN;
    }
    let m = mean(v);
    v.iter().map(|x| (x - m) * (x - m)).sum::<f64>() / (n as f64 - 1.0)
}

/// 线性插值分位数 (p 取 0..=1)。
pub fn percentile(v: &[f64], p: f64) -> f64 {
    if v.is_empty() {
        return f64::NAN;
    }
    let mut s: Vec<f64> = v.iter().copied().filter(|x| x.is_finite()).collect();
    if s.is_empty() {
        return f64::NAN;
    }
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    if s.len() == 1 {
        return s[0];
    }
    let p = p.clamp(0.0, 1.0);
    let idx = p * (s.len() - 1) as f64;
    let lo = idx.floor() as usize;
    let hi = idx.ceil() as usize;
    if lo == hi {
        return s[lo];
    }
    s[lo] + (s[hi] - s[lo]) * (idx - lo as f64)
}

// ══════════════ Welch t 检验 ══════════════

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct WelchResult {
    pub n_a: usize,
    pub n_b: usize,
    pub mean_a: f64,
    pub mean_b: f64,
    /// mean_b - mean_a (b 相对 a 的变化量)
    pub delta: f64,
    pub t: f64,
    /// Welch-Satterthwaite 自由度
    pub df: f64,
    /// 双尾 p 值
    pub p_value: f64,
}

impl WelchResult {
    pub fn significant(&self, threshold: f64) -> bool {
        self.p_value.is_finite() && self.p_value < threshold
    }
}

/// Welch 两样本 t 检验。两组各至少 2 个样本; 否则 None。
///
/// 两组方差都为 0 时 t 无定义: 均值相等记 p=1 (没有差异), 不等记 p=0 (确定有差异)。
/// 这不是数学上的 p 值, 而是对退化输入的明确约定 —— 总比返回 NaN 让上层拿去比较强。
pub fn welch_t_test(a: &[f64], b: &[f64]) -> Option<WelchResult> {
    let (na, nb) = (a.len(), b.len());
    if na < 2 || nb < 2 {
        return None;
    }
    let (ma, mb) = (mean(a), mean(b));
    let (va, vb) = (sample_variance(a), sample_variance(b));
    if !va.is_finite() || !vb.is_finite() {
        return None;
    }

    let sa = va / na as f64;
    let sb = vb / nb as f64;
    let se2 = sa + sb;

    if se2 <= 0.0 {
        let same = (mb - ma).abs() < f64::EPSILON;
        return Some(WelchResult {
            n_a: na,
            n_b: nb,
            mean_a: ma,
            mean_b: mb,
            delta: mb - ma,
            t: if same { 0.0 } else { f64::INFINITY },
            df: (na + nb - 2) as f64,
            p_value: if same { 1.0 } else { 0.0 },
        });
    }

    let se = se2.sqrt();
    let t = (mb - ma) / se;
    // Welch-Satterthwaite
    let df = se2 * se2 / (sa * sa / (na as f64 - 1.0) + sb * sb / (nb as f64 - 1.0));
    let p = student_t_two_sided_p(t, df);

    Some(WelchResult {
        n_a: na,
        n_b: nb,
        mean_a: ma,
        mean_b: mb,
        delta: mb - ma,
        t,
        df,
        p_value: p,
    })
}

/// t 分布双尾 p 值: p = I_{df/(df+t^2)}(df/2, 1/2)
pub fn student_t_two_sided_p(t: f64, df: f64) -> f64 {
    if !t.is_finite() {
        return if t.is_nan() { f64::NAN } else { 0.0 };
    }
    if df <= 0.0 || !df.is_finite() {
        return f64::NAN;
    }
    let x = df / (df + t * t);
    betai(df / 2.0, 0.5, x).clamp(0.0, 1.0)
}

/// 正则化不完全贝塔函数 I_x(a, b)。
fn betai(a: f64, b: f64, x: f64) -> f64 {
    if x <= 0.0 {
        return 0.0;
    }
    if x >= 1.0 {
        return 1.0;
    }
    let bt = (ln_gamma(a + b) - ln_gamma(a) - ln_gamma(b) + a * x.ln() + b * (1.0 - x).ln()).exp();
    // 连分式在 x < (a+1)/(a+b+2) 一侧收敛快; 另一侧用对称式换过去
    if x < (a + 1.0) / (a + b + 2.0) {
        bt * betacf(a, b, x) / a
    } else {
        1.0 - bt * betacf(b, a, 1.0 - x) / b
    }
}

/// 不完全贝塔的连分式展开 (修正 Lentz 法)。
fn betacf(a: f64, b: f64, x: f64) -> f64 {
    const MAXIT: usize = 300;
    const EPS: f64 = 3.0e-16;
    const FPMIN: f64 = 1.0e-300;

    let qab = a + b;
    let qap = a + 1.0;
    let qam = a - 1.0;

    let mut c = 1.0;
    let mut d = 1.0 - qab * x / qap;
    if d.abs() < FPMIN {
        d = FPMIN;
    }
    d = 1.0 / d;
    let mut h = d;

    for m in 1..=MAXIT {
        let m_f = m as f64;
        let m2 = 2.0 * m_f;

        // 偶数步
        let aa = m_f * (b - m_f) * x / ((qam + m2) * (a + m2));
        d = 1.0 + aa * d;
        if d.abs() < FPMIN {
            d = FPMIN;
        }
        c = 1.0 + aa / c;
        if c.abs() < FPMIN {
            c = FPMIN;
        }
        d = 1.0 / d;
        h *= d * c;

        // 奇数步
        let aa = -(a + m_f) * (qab + m_f) * x / ((a + m2) * (qap + m2));
        d = 1.0 + aa * d;
        if d.abs() < FPMIN {
            d = FPMIN;
        }
        c = 1.0 + aa / c;
        if c.abs() < FPMIN {
            c = FPMIN;
        }
        d = 1.0 / d;
        let del = d * c;
        h *= del;

        if (del - 1.0).abs() < EPS {
            break;
        }
    }
    h
}

/// ln(Gamma(x)), Lanczos 近似 (g=7, n=9)。x > 0。
fn ln_gamma(x: f64) -> f64 {
    const G: [f64; 9] = [
        0.999_999_999_999_809_93,
        676.520_368_121_885_1,
        -1259.139_216_722_402_8,
        771.323_428_777_653_1,
        -176.615_029_162_140_6,
        12.507_343_278_686_905,
        -0.138_571_095_265_720_12,
        9.984_369_578_019_572e-6,
        1.505_632_735_149_311_6e-7,
    ];
    if x < 0.5 {
        // 反射公式
        return (std::f64::consts::PI / (std::f64::consts::PI * x).sin()).ln() - ln_gamma(1.0 - x);
    }
    let x = x - 1.0;
    let mut a = G[0];
    let t = x + 7.5;
    for (i, g) in G.iter().enumerate().skip(1) {
        a += g / (x + i as f64);
    }
    0.5 * (2.0 * std::f64::consts::PI).ln() + (x + 0.5) * t.ln() - t + a.ln()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: f64, b: f64, tol: f64) -> bool {
        (a - b).abs() <= tol
    }

    #[test]
    fn basic_moments() {
        let v = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0];
        assert!(close(mean(&v), 5.0, 1e-12));
        // 样本方差 (n-1): 32/7
        assert!(close(sample_variance(&v), 32.0 / 7.0, 1e-12));
        assert!(mean(&[]).is_nan());
        assert!(sample_variance(&[1.0]).is_nan());
    }

    #[test]
    fn percentiles_interpolate() {
        let v = [1.0, 2.0, 3.0, 4.0];
        assert!(close(percentile(&v, 0.0), 1.0, 1e-12));
        assert!(close(percentile(&v, 1.0), 4.0, 1e-12));
        assert!(close(percentile(&v, 0.5), 2.5, 1e-12));
        assert!(close(percentile(&[7.0], 0.95), 7.0, 1e-12));
        assert!(percentile(&[], 0.5).is_nan());
    }

    // ---------- ln_gamma 对照已知值 ----------

    #[test]
    fn ln_gamma_matches_known_values() {
        // Gamma(1)=1, Gamma(5)=24, Gamma(0.5)=sqrt(pi)
        assert!(close(ln_gamma(1.0), 0.0, 1e-10));
        assert!(close(ln_gamma(5.0), 24.0f64.ln(), 1e-10));
        assert!(close(
            ln_gamma(0.5),
            std::f64::consts::PI.sqrt().ln(),
            1e-10
        ));
        assert!(close(ln_gamma(10.0), 362880.0f64.ln(), 1e-9));
    }

    // ---------- t 分布 p 值对照标准表 ----------
    //
    // 这些是统计教科书双尾临界值。p 值算错会让整条闭环的显著性判定失去意义,
    // 所以必须对着外部已知值钉死, 不能只测"自洽"。

    #[test]
    fn two_sided_p_matches_the_standard_t_table() {
        // df=10, t=2.228 → p≈0.050 ; t=3.169 → p≈0.010
        assert!(close(student_t_two_sided_p(2.228, 10.0), 0.050, 5e-4));
        assert!(close(student_t_two_sided_p(3.169, 10.0), 0.010, 5e-4));
        // df=20, t=2.086 → p≈0.050 ; t=2.845 → p≈0.010
        assert!(close(student_t_two_sided_p(2.086, 20.0), 0.050, 5e-4));
        assert!(close(student_t_two_sided_p(2.845, 20.0), 0.010, 5e-4));
        // df=1 (柯西), t=12.706 → p≈0.050
        assert!(close(student_t_two_sided_p(12.706, 1.0), 0.050, 5e-4));
        // df 很大时趋近标准正态: t=1.96 → p≈0.05
        assert!(close(student_t_two_sided_p(1.96, 1.0e7), 0.05, 1e-3));
    }

    #[test]
    fn p_value_is_symmetric_and_bounded() {
        for t in [0.5, 1.0, 2.5, 7.0] {
            let p1 = student_t_two_sided_p(t, 8.0);
            let p2 = student_t_two_sided_p(-t, 8.0);
            assert!(close(p1, p2, 1e-12), "t={t} 双尾必须对称");
            assert!((0.0..=1.0).contains(&p1));
        }
        assert!(close(student_t_two_sided_p(0.0, 5.0), 1.0, 1e-12));
    }

    /// p 值必须随 |t| 单调下降 —— 否则判定会在边界上乱跳。
    #[test]
    fn p_value_decreases_monotonically_with_t() {
        let mut prev = 1.1;
        for t in [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0] {
            let p = student_t_two_sided_p(t, 12.0);
            assert!(p < prev, "t={t} 时 p={p} 没有下降 (上一个 {prev})");
            prev = p;
        }
    }

    // ---------- Welch t 检验 ----------

    /// 对照 R: t.test(a, b, var.equal=FALSE)
    /// a = 1,2,3,4,5 ; b = 6,7,8,9,10 → t=5, df=8, p=0.00105...
    #[test]
    fn welch_matches_a_known_reference_case() {
        let a = [1.0, 2.0, 3.0, 4.0, 5.0];
        let b = [6.0, 7.0, 8.0, 9.0, 10.0];
        let r = welch_t_test(&a, &b).unwrap();
        assert!(close(r.mean_a, 3.0, 1e-12));
        assert!(close(r.mean_b, 8.0, 1e-12));
        assert!(close(r.delta, 5.0, 1e-12));
        assert!(close(r.t, 5.0, 1e-9), "t={}", r.t);
        assert!(close(r.df, 8.0, 1e-9), "df={}", r.df);
        assert!(close(r.p_value, 0.001053, 5e-5), "p={}", r.p_value);
        assert!(r.significant(0.01));
    }

    /// 方差不等时 Welch 的自由度必须小于 n_a+n_b-2 —— 这正是它比 Student 保守的地方。
    #[test]
    fn welch_penalises_unequal_variance_with_lower_df() {
        let tight = [10.0, 10.1, 9.9, 10.0, 10.05];
        let loose = [12.0, 8.0, 15.0, 5.0, 11.0];
        let r = welch_t_test(&tight, &loose).unwrap();
        assert!(
            r.df < (tight.len() + loose.len() - 2) as f64,
            "df={} 应当小于 8",
            r.df
        );
        assert!(r.df > 1.0);
    }

    /// 两组统计上没区别时不能判显著 —— 这是防噪声欺骗的正面样本。
    #[test]
    fn overlapping_samples_are_not_significant() {
        let a = [1.00, 1.02, 0.98, 1.01, 0.99, 1.00];
        let b = [1.01, 0.99, 1.00, 1.02, 0.98, 1.01];
        let r = welch_t_test(&a, &b).unwrap();
        assert!(
            !r.significant(0.01),
            "重叠样本被判成显著 (p={})",
            r.p_value
        );
    }

    /// 真实场景: 候选算子确实更慢, 且每组只有 4 轮。必须判得出来。
    #[test]
    fn a_real_regression_is_detected_with_four_rounds_per_arm() {
        let baseline = [1.021, 1.018, 1.025, 1.020];
        let candidate = [1.180, 1.176, 1.184, 1.179];
        let r = welch_t_test(&baseline, &candidate).unwrap();
        assert!(r.delta > 0.0, "候选更慢, delta 应为正");
        assert!(r.significant(0.01), "p={} 没达到 0.01", r.p_value);
    }

    #[test]
    fn too_few_samples_yield_no_verdict() {
        assert!(welch_t_test(&[1.0], &[2.0, 3.0]).is_none());
        assert!(welch_t_test(&[1.0, 2.0], &[3.0]).is_none());
        assert!(welch_t_test(&[], &[]).is_none());
    }

    /// 退化输入 (两组各自零方差) 必须给出明确约定, 不能吐 NaN 让上层拿去比较。
    #[test]
    fn zero_variance_inputs_get_a_defined_verdict() {
        let same = welch_t_test(&[5.0, 5.0, 5.0], &[5.0, 5.0, 5.0]).unwrap();
        assert!(close(same.p_value, 1.0, 1e-12));
        assert!(!same.significant(0.01));

        let diff = welch_t_test(&[5.0, 5.0, 5.0], &[9.0, 9.0, 9.0]).unwrap();
        assert!(close(diff.p_value, 0.0, 1e-12));
        assert!(diff.significant(0.01));
        assert!(close(diff.delta, 4.0, 1e-12));
    }

    /// delta 的符号约定: 正 = b 比 a 大。判定层按这个符号决定"变快还是变慢"。
    #[test]
    fn delta_sign_convention_is_b_minus_a() {
        let r = welch_t_test(&[10.0, 10.0, 10.0, 10.1], &[8.0, 8.0, 8.0, 8.1]).unwrap();
        assert!(r.delta < 0.0, "b 更小时 delta 必须为负");
    }
}
