//! CPython `random.Random` 的逐位复刻 (Mersenne Twister MT19937)。
//!
//! 为什么需要它
//! ------------
//! `loop_v1` 的本地降级变异器 (LLM 全挂时用来补齐候选) 声明「固定 seed 下产出确定,
//! 所以降级路径本身也是可复现的」。这条声明是证据链的一部分: 证据里记着 seed,
//! 拿同一个 seed 重跑就该出同一批候选。Rust 自己的任何随机数实现都出不了那批候选,
//! 所以只能把 CPython 用的那套原样搬过来。
//!
//! 复刻到哪一层
//! ------------
//! 不只是 MT19937 本体, 还有 CPython 在它之上那几层的**确切**做法:
//!
//! * `Random(n)` 的播种: 取 `abs(n)` 的小端 32 位字数组走 `init_by_array`
//!   (字数 = `max(1, ceil(bits/32))`), 不是 `init_genrand`;
//! * `getrandbits(k)`: k<=32 时取一个 32 位数右移 `32-k`; 更大时按**小端**逐字取,
//!   最后一个字再右移;
//! * `_randbelow(n)`: 取 `n.bit_length()` 位, 大于等于 n 就重摇 (拒绝采样),
//!   而不是取模 —— 取模会改变消耗的随机数个数, 后面全错位;
//! * `sample(pop, k)`: n 小时走「池子交换」分支, n 大时走「已选集合」分支,
//!   两条分支消耗的随机数不一样, 分界线 `setsize` 也照搬。
//!
//! 对照向量取自本机 python3 (见测试)。

const N: usize = 624;
const M: usize = 397;
const MATRIX_A: u32 = 0x9908_b0df;
const UPPER_MASK: u32 = 0x8000_0000;
const LOWER_MASK: u32 = 0x7fff_ffff;

pub struct PyRandom {
    mt: [u32; N],
    mti: usize,
}

impl PyRandom {
    /// `random.Random(seed)`, seed 是个整数。
    pub fn new(seed: i64) -> Self {
        // CPython: 用 abs(seed) 的小端 32 位字当 key, 字数 = max(1, ceil(bits/32))
        let n = seed.unsigned_abs();
        let bits = 64 - n.leading_zeros();
        let words = if bits == 0 { 1 } else { ((bits - 1) / 32 + 1) as usize };
        let key: Vec<u32> = (0..words).map(|i| ((n >> (32 * i)) & 0xffff_ffff) as u32).collect();
        let mut r = PyRandom {
            mt: [0; N],
            mti: N + 1,
        };
        r.init_by_array(&key);
        r
    }

    fn init_genrand(&mut self, s: u32) {
        self.mt[0] = s;
        for i in 1..N {
            self.mt[i] = 1812433253u32
                .wrapping_mul(self.mt[i - 1] ^ (self.mt[i - 1] >> 30))
                .wrapping_add(i as u32);
        }
        self.mti = N;
    }

    fn init_by_array(&mut self, key: &[u32]) {
        self.init_genrand(19650218);
        let mut i = 1usize;
        let mut j = 0usize;
        let mut k = N.max(key.len());
        while k > 0 {
            self.mt[i] = (self.mt[i]
                ^ (self.mt[i - 1] ^ (self.mt[i - 1] >> 30)).wrapping_mul(1664525))
                .wrapping_add(key[j])
                .wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= N {
                self.mt[0] = self.mt[N - 1];
                i = 1;
            }
            if j >= key.len() {
                j = 0;
            }
            k -= 1;
        }
        for _ in 0..N - 1 {
            self.mt[i] = (self.mt[i]
                ^ (self.mt[i - 1] ^ (self.mt[i - 1] >> 30)).wrapping_mul(1566083941))
                .wrapping_sub(i as u32);
            i += 1;
            if i >= N {
                self.mt[0] = self.mt[N - 1];
                i = 1;
            }
        }
        self.mt[0] = 0x8000_0000;
    }

    fn genrand_u32(&mut self) -> u32 {
        if self.mti >= N {
            for kk in 0..N - M {
                let y = (self.mt[kk] & UPPER_MASK) | (self.mt[kk + 1] & LOWER_MASK);
                self.mt[kk] = self.mt[kk + M] ^ (y >> 1) ^ if y & 1 != 0 { MATRIX_A } else { 0 };
            }
            for kk in N - M..N - 1 {
                let y = (self.mt[kk] & UPPER_MASK) | (self.mt[kk + 1] & LOWER_MASK);
                self.mt[kk] =
                    self.mt[kk + M - N] ^ (y >> 1) ^ if y & 1 != 0 { MATRIX_A } else { 0 };
            }
            let y = (self.mt[N - 1] & UPPER_MASK) | (self.mt[0] & LOWER_MASK);
            self.mt[N - 1] = self.mt[M - 1] ^ (y >> 1) ^ if y & 1 != 0 { MATRIX_A } else { 0 };
            self.mti = 0;
        }
        let mut y = self.mt[self.mti];
        self.mti += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^ (y >> 18)
    }

    /// `getrandbits(k)`, k <= 64 (闭环里用不到更宽的)。
    pub fn getrandbits(&mut self, k: u32) -> u64 {
        if k == 0 {
            return 0;
        }
        if k <= 32 {
            return (self.genrand_u32() >> (32 - k)) as u64;
        }
        // 小端逐字, 最后一个字右移 —— 顺序反了结果就不一样
        let words = (k - 1) / 32 + 1;
        let mut out: u64 = 0;
        let mut left = k;
        for i in 0..words {
            let mut r = self.genrand_u32();
            if left < 32 {
                r >>= 32 - left;
            }
            out |= (r as u64) << (32 * i);
            left = left.saturating_sub(32);
        }
        out
    }

    /// `_randbelow(n)`: 拒绝采样, **不是**取模 —— 取模会改变消耗的随机数个数。
    pub fn randbelow(&mut self, n: u64) -> u64 {
        if n == 0 {
            return 0;
        }
        let k = 64 - n.leading_zeros();
        loop {
            let r = self.getrandbits(k);
            if r < n {
                return r;
            }
        }
    }

    /// `choice(seq)`。空序列在 Python 里抛 IndexError, 这里回 None。
    pub fn choice<'a, T>(&mut self, seq: &'a [T]) -> Option<&'a T> {
        if seq.is_empty() {
            return None;
        }
        seq.get(self.randbelow(seq.len() as u64) as usize)
    }

    /// `sample(population, k)` —— 返回选中的下标, 顺序与 Python 一致。
    ///
    /// 两条分支消耗的随机数不同, 分界线照搬 CPython 的 `setsize`。
    pub fn sample_indices(&mut self, n: usize, k: usize) -> Vec<usize> {
        if k > n {
            return Vec::new();
        }
        // setsize = 21 + (k>5 时 4**ceil(log(k*3, 4)))
        let mut setsize = 21usize;
        if k > 5 {
            let target = (k * 3) as f64;
            let e = (target.ln() / 4f64.ln()).ceil() as u32;
            setsize += 4usize.pow(e);
        }
        let mut result = Vec::with_capacity(k);
        if n <= setsize {
            // 池子交换: 选中的位置用尾部未选项填补
            let mut pool: Vec<usize> = (0..n).collect();
            for i in 0..k {
                let j = self.randbelow((n - i) as u64) as usize;
                result.push(pool[j]);
                pool[j] = pool[n - i - 1];
            }
        } else {
            let mut selected = std::collections::HashSet::new();
            for _ in 0..k {
                let mut j = self.randbelow(n as u64) as usize;
                while selected.contains(&j) {
                    j = self.randbelow(n as u64) as usize;
                }
                selected.insert(j);
                result.push(j);
            }
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 对照向量全部来自本机 python3:
    /// `r = random.Random(seed); [r.getrandbits(k) for k in (1,3,8,32,33,64,7)]`
    #[test]
    fn getrandbits_matches_cpython() {
        let cases: &[(i64, [u64; 7])] = &[
            (0, [1, 3, 194, 3823568514, 1806341205, 17809683713383489082, 65]),
            (1, [0, 4, 216, 3445702192, 3280387012, 2175216119781798972, 63]),
            (7, [0, 7, 38, 1695753998, 2795742288, 15149836622520594227, 68]),
            (11, [0, 6, 143, 3680198571, 8264421517, 8334835209022527425, 65]),
            (3, [0, 4, 139, 560161641, 5883912612, 8744744311366254845, 80]),
            (12345, [0, 5, 2, 3522623596, 7839202253, 15774518201988393282, 47]),
            (
                1099511627783,
                [1, 5, 208, 4028389051, 8353760805, 13015927955237707764, 60],
            ),
        ];
        for (seed, want) in cases {
            let mut r = PyRandom::new(*seed);
            let got: Vec<u64> = [1u32, 3, 8, 32, 33, 64, 7]
                .iter()
                .map(|k| r.getrandbits(*k))
                .collect();
            assert_eq!(&got[..], &want[..], "seed={seed}");
        }
    }

    /// `[r.choice('abcd') x5]`, 然后 `r.sample(range(10), 2)`, `r.sample(range(30), 7)`
    /// —— 后者 n=30 > setsize, 走的是「已选集合」那条分支。
    #[test]
    fn choice_and_sample_match_cpython() {
        let letters = ["a", "b", "c", "d"];
        for (seed, picks, s2, s7) in [
            (7i64, ["c", "b", "d", "a", "a"], [8usize, 1], [11usize, 18, 1, 16, 6, 27, 2]),
            (11, ["d", "d", "d", "b", "b"], [8, 7], [20, 19, 25, 5, 3, 14, 9]),
        ] {
            let mut r = PyRandom::new(seed);
            let got: Vec<&str> = (0..5).map(|_| *r.choice(&letters).unwrap()).collect();
            assert_eq!(got, picks.to_vec(), "seed={seed} choice");
            assert_eq!(r.sample_indices(10, 2), s2.to_vec(), "seed={seed} sample(10,2)");
            assert_eq!(r.sample_indices(30, 7), s7.to_vec(), "seed={seed} sample(30,7)");
        }
    }

    #[test]
    fn degenerate_cases() {
        let mut r = PyRandom::new(1);
        assert_eq!(r.getrandbits(0), 0);
        assert_eq!(r.randbelow(0), 0);
        assert_eq!(r.randbelow(1), 0);
        let empty: [u8; 0] = [];
        assert!(r.choice(&empty).is_none());
        assert!(r.sample_indices(2, 5).is_empty());
        assert_eq!(r.sample_indices(5, 0), Vec::<usize>::new());
    }
}
