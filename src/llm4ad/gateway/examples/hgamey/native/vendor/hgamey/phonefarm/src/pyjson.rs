//! Python `json.dumps(..., ensure_ascii=False, indent=1)` 的逐字节复刻。
//!
//! loop_v1 的分析链原来是 Python, 下游 (报告、证据归档、其它 agent 的对照脚本) 已经
//! 按那份输出的确切字节在读。搬到 Rust 时口径不能变: 同一份输入必须出同一串字节,
//! 否则 golden 对照就不是对照, 是"看起来差不多"。
//!
//! 三处 Python 特有、serde_json 给不了的行为, 是这个模块存在的全部理由:
//!   1. 缩进 1 个空格 (serde_json 的 PrettyFormatter 固定 2 个);
//!   2. 浮点走 Python `repr` 的科学计数法阈值 —— 指数 < -4 或 >= 16 才用 `e`,
//!      且指数至少补到两位 (`1e-05`, 不是 ryu 的 `0.00001`);
//!   3. int 与 float 是两种东西 —— `3150` 与 `3150.0` 是不同的字节。
//!      Python `statistics.median` 对奇数个 int 返回 int, 偶数个返回 float,
//!      这个区别直接落在 `bw_median` 上, 不能抹平。
//!
//! 还有 `Infinity` / `NaN`: Python 照写, serde_json 写 `null`。变异系数在退化输入上
//! 真会返回 inf, 所以也得照写。
//!
//! 这些加起来就没法复用 serde_json 的 Serializer 了 (它在 f64 非有限时直接走 write_null,
//! 自定义 Formatter 也拦不住), 于是这里自带一个最小 JSON 写出器。

/// Python 侧 JSON 值。`Int` / `Float` 刻意分开 —— 见模块注释第 3 条。
#[derive(Debug, Clone, PartialEq)]
pub enum PyVal {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    List(Vec<PyVal>),
    /// 对象按插入顺序输出 (Python 3.7+ dict 保序), 不排序。
    Obj(Vec<(String, PyVal)>),
}

impl PyVal {
    /// 对象取键; 不是对象或没这个键都返回 None (等价于 Python 的 `d.get(k)`)。
    pub fn get(&self, k: &str) -> Option<&PyVal> {
        match self {
            PyVal::Obj(kvs) => kvs.iter().find(|(ek, _)| ek == k).map(|(_, v)| v),
            _ => None,
        }
    }
    /// 数字取值, int 与 float 一视同仁。
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            PyVal::Int(i) => Some(*i as f64),
            PyVal::Float(x) => Some(*x),
            _ => None,
        }
    }
    /// 整数取值 (float 按 Python `int()` 那样截断)。
    pub fn as_i64(&self) -> Option<i64> {
        match self {
            PyVal::Int(i) => Some(*i),
            PyVal::Float(x) => Some(*x as i64),
            _ => None,
        }
    }
    /// 只认真正的布尔 —— 不做 Python 那种"非空即真"的泛化。
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            PyVal::Bool(b) => Some(*b),
            _ => None,
        }
    }
    /// Python `str(x)` —— f-string 里插值用的就是它。
    /// 顶层字符串不带引号; 其余都走 [`PyVal::py_repr`]。
    pub fn py_str(&self) -> String {
        match self {
            PyVal::Str(s) => s.clone(),
            other => other.py_repr(),
        }
    }

    /// Python `repr(x)`: `None` / `True` / `False`, 字符串单引号, 容器里套 repr。
    /// `str(dict)` 与 `str(list)` 走的就是这一套, 与 JSON 的写法不一样。
    pub fn py_repr(&self) -> String {
        match self {
            PyVal::Null => "None".to_string(),
            PyVal::Bool(true) => "True".to_string(),
            PyVal::Bool(false) => "False".to_string(),
            PyVal::Int(i) => i.to_string(),
            PyVal::Float(f) => py_repr_f64(*f),
            PyVal::Str(s) => {
                // Python 优先用单引号; 串里有单引号且没有双引号时才换双引号
                let (q, esc) = if s.contains('\'') && !s.contains('"') {
                    ('"', false)
                } else {
                    ('\'', s.contains('\''))
                };
                let mut out = String::new();
                out.push(q);
                for c in s.chars() {
                    match c {
                        '\\' => out.push_str("\\\\"),
                        '\n' => out.push_str("\\n"),
                        '\r' => out.push_str("\\r"),
                        '\t' => out.push_str("\\t"),
                        '\'' if esc => out.push_str("\\'"),
                        c => out.push(c),
                    }
                }
                out.push(q);
                out
            }
            PyVal::List(xs) => format!(
                "[{}]",
                xs.iter().map(|x| x.py_repr()).collect::<Vec<_>>().join(", ")
            ),
            PyVal::Obj(kvs) => format!(
                "{{{}}}",
                kvs.iter()
                    .map(|(k, v)| format!("{}: {}", PyVal::Str(k.clone()).py_repr(), v.py_repr()))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        }
    }
}

impl From<f64> for PyVal {
    fn from(v: f64) -> Self {
        PyVal::Float(v)
    }
}
impl From<i64> for PyVal {
    fn from(v: i64) -> Self {
        PyVal::Int(v)
    }
}
impl From<usize> for PyVal {
    fn from(v: usize) -> Self {
        PyVal::Int(v as i64)
    }
}
impl From<bool> for PyVal {
    fn from(v: bool) -> Self {
        PyVal::Bool(v)
    }
}
impl From<String> for PyVal {
    fn from(v: String) -> Self {
        PyVal::Str(v)
    }
}
impl From<&str> for PyVal {
    fn from(v: &str) -> Self {
        PyVal::Str(v.to_string())
    }
}
impl<T: Into<PyVal>> From<Option<T>> for PyVal {
    fn from(v: Option<T>) -> Self {
        match v {
            Some(x) => x.into(),
            None => PyVal::Null,
        }
    }
}
impl<T: Into<PyVal>> From<Vec<T>> for PyVal {
    fn from(v: Vec<T>) -> Self {
        PyVal::List(v.into_iter().map(Into::into).collect())
    }
}

// ══════════════ 读入 ══════════════

/// 把 JSON 文本读成 [`PyVal`]，语义对齐 Python 的 `json.loads`。
///
/// 为什么不用 `serde_json`
/// ----------------------
/// 两条都会破坏"逐字节一致":
///
/// 1. **它的浮点解析不是正确舍入的**。`-3.9348497207249924` 经 serde_json 解析再打印
///    会变成 `-3.934849720724992` —— 差了 1 ulp。Rust 标准库的 `str::parse::<f64>()`
///    与 CPython 用的 dtoa 都是正确舍入, 两者一致; serde_json 自己那套快速路径不是。
///    这不是极端指数才有的事, 十七位有效数字的普通数就会踩到。
/// 2. **它的对象是 BTreeMap, 一解析键就按字典序排好了**, 而 Python dict 保留插入序。
///
/// 数字按 Python 的规矩分流: 没有小数点也没有指数的按 int 收 (超出 i64 的退回 float,
/// Python 那边是大整数, 这条闭环里不会出现)。
pub fn loads(text: &str) -> Result<PyVal, String> {
    let b = text.as_bytes();
    let mut i = 0usize;
    let v = parse_value(b, &mut i, 0)?;
    skip_ws(b, &mut i);
    if i != b.len() {
        return Err(format!("第 {i} 字节之后还有多余内容"));
    }
    Ok(v)
}

fn skip_ws(b: &[u8], i: &mut usize) {
    while *i < b.len() && matches!(b[*i], b' ' | b'\t' | b'\n' | b'\r') {
        *i += 1;
    }
}

fn expect(b: &[u8], i: &mut usize, lit: &str) -> Result<(), String> {
    if b[*i..].starts_with(lit.as_bytes()) {
        *i += lit.len();
        Ok(())
    } else {
        Err(format!("第 {i} 字节处期望 {lit}"))
    }
}

/// 嵌套层数上限, 与 serde_json 的默认值一致。没有这道闸, 一份几十万层的畸形
/// 数组会把进程的栈直接撑爆 (abort), 而不是回一句"请求不合法"。
const MAX_DEPTH: usize = 128;

fn parse_value(b: &[u8], i: &mut usize, depth: usize) -> Result<PyVal, String> {
    if depth > MAX_DEPTH {
        return Err(format!("嵌套超过 {MAX_DEPTH} 层"));
    }
    skip_ws(b, i);
    match b.get(*i) {
        None => Err("内容为空".into()),
        Some(b'n') => expect(b, i, "null").map(|_| PyVal::Null),
        Some(b't') => expect(b, i, "true").map(|_| PyVal::Bool(true)),
        Some(b'f') => expect(b, i, "false").map(|_| PyVal::Bool(false)),
        // 本模块的 dumps 会写 NaN / Infinity (Python 照写), 所以 loads 也得认得 ——
        // 不认的话, 一份含 inf 的 summary.json 会被 load_runs 静静丢掉。
        // json.loads 默认也认这三个字面量。
        Some(b'N') => expect(b, i, "NaN").map(|_| PyVal::Float(f64::NAN)),
        Some(b'I') => expect(b, i, "Infinity").map(|_| PyVal::Float(f64::INFINITY)),
        Some(b'-') if b[*i..].starts_with(b"-Infinity") => {
            expect(b, i, "-Infinity").map(|_| PyVal::Float(f64::NEG_INFINITY))
        }
        Some(b'"') => parse_string(b, i).map(PyVal::Str),
        Some(b'[') => {
            *i += 1;
            let mut out = Vec::new();
            skip_ws(b, i);
            if b.get(*i) == Some(&b']') {
                *i += 1;
                return Ok(PyVal::List(out));
            }
            loop {
                out.push(parse_value(b, i, depth + 1)?);
                skip_ws(b, i);
                match b.get(*i) {
                    Some(b',') => *i += 1,
                    Some(b']') => {
                        *i += 1;
                        return Ok(PyVal::List(out));
                    }
                    _ => return Err(format!("第 {i} 字节处数组没收尾")),
                }
            }
        }
        Some(b'{') => {
            *i += 1;
            let mut out: Vec<(String, PyVal)> = Vec::new();
            skip_ws(b, i);
            if b.get(*i) == Some(&b'}') {
                *i += 1;
                return Ok(PyVal::Obj(out));
            }
            loop {
                skip_ws(b, i);
                let k = parse_string(b, i)?;
                skip_ws(b, i);
                if b.get(*i) != Some(&b':') {
                    return Err(format!("第 {i} 字节处缺冒号"));
                }
                *i += 1;
                let v = parse_value(b, i, depth + 1)?;
                // Python dict: 重复键就地覆盖, 不改位置
                match out.iter_mut().find(|(ek, _)| *ek == k) {
                    Some(slot) => slot.1 = v,
                    None => out.push((k, v)),
                }
                skip_ws(b, i);
                match b.get(*i) {
                    Some(b',') => *i += 1,
                    Some(b'}') => {
                        *i += 1;
                        return Ok(PyVal::Obj(out));
                    }
                    _ => return Err(format!("第 {i} 字节处对象没收尾")),
                }
            }
        }
        Some(_) => parse_number(b, i),
    }
}

fn parse_string(b: &[u8], i: &mut usize) -> Result<String, String> {
    if b.get(*i) != Some(&b'"') {
        return Err(format!("第 {i} 字节处期望字符串"));
    }
    *i += 1;
    let mut out = String::new();
    loop {
        let c = *b.get(*i).ok_or("字符串没收尾")?;
        *i += 1;
        match c {
            b'"' => return Ok(out),
            b'\\' => {
                let e = *b.get(*i).ok_or("转义没收尾")?;
                *i += 1;
                match e {
                    b'"' => out.push('"'),
                    b'\\' => out.push('\\'),
                    b'/' => out.push('/'),
                    b'b' => out.push('\u{8}'),
                    b'f' => out.push('\u{c}'),
                    b'n' => out.push('\n'),
                    b'r' => out.push('\r'),
                    b't' => out.push('\t'),
                    b'u' => {
                        let hi = hex4(b, i)?;
                        // 代理对: 高位后面必须跟低位, 才拼得出一个真字符
                        // 高位代理后面必须跟一个**合法的**低位, 才拼得出一个真字符。
                        // 不验低位就直接减 0xDC00 会下溢 (debug 下 panic, release 下回绕)。
                        // Rust 的 String 存不下孤立代理, 这里一律报错而不是造一个坏字符。
                        let ch = if (0xD800..0xDC00).contains(&hi) {
                            if b.get(*i) == Some(&b'\\') && b.get(*i + 1) == Some(&b'u') {
                                let save = *i;
                                *i += 2;
                                let lo = hex4(b, i)?;
                                if (0xDC00..0xE000).contains(&lo) {
                                    char::from_u32(
                                        0x10000 + ((hi - 0xD800) << 10) + (lo - 0xDC00),
                                    )
                                } else {
                                    *i = save;
                                    None
                                }
                            } else {
                                None
                            }
                        } else {
                            char::from_u32(hi)
                        };
                        out.push(ch.ok_or("孤立的 \\u 代理项")?);
                    }
                    other => return Err(format!("不认识的转义 \\{}", other as char)),
                }
            }
            // 裸控制字符在 JSON 字符串里非法 (json.loads 抛), 必须写成 \\u00XX
            c if c < 0x20 => return Err(format!("字符串里有裸控制字符 0x{c:02x}")),
            _ => {
                // 多字节 UTF-8 原样搬过去
                let start = *i - 1;
                let len = utf8_len(c);
                *i = start + len;
                out.push_str(
                    std::str::from_utf8(b.get(start..*i).ok_or("字符串截断")?)
                        .map_err(|_| "不是合法 UTF-8")?,
                );
            }
        }
    }
}

fn utf8_len(c: u8) -> usize {
    match c {
        0x00..=0x7F => 1,
        0xC0..=0xDF => 2,
        0xE0..=0xEF => 3,
        _ => 4,
    }
}

fn hex4(b: &[u8], i: &mut usize) -> Result<u32, String> {
    let s = std::str::from_utf8(b.get(*i..*i + 4).ok_or("\\u 后不足四位")?)
        .map_err(|_| "\\u 后不是 ASCII")?;
    *i += 4;
    u32::from_str_radix(s, 16).map_err(|_| "\\u 后不是十六进制".into())
}

/// 按 JSON 语法收一个数字。
///
/// 刻意**严格**: `+5`、`01`、`5.`、`.5` 都是非法 JSON, `json.loads` 会抛。宽松放过
/// 的后果是一份畸形的模型回包不再整份作废, 而是漏一组候选到设备上。
fn parse_number(b: &[u8], i: &mut usize) -> Result<PyVal, String> {
    let start = *i;
    let bad = |i: usize| Err(format!("第 {i} 字节处不是一个合法数字"));
    if b.get(*i) == Some(&b'-') {
        *i += 1;
    }
    // 整数部分: 单个 0, 或非 0 开头的一串
    match b.get(*i) {
        Some(b'0') => *i += 1,
        Some(c) if c.is_ascii_digit() => {
            while b.get(*i).is_some_and(|c| c.is_ascii_digit()) {
                *i += 1;
            }
        }
        _ => return bad(start),
    }
    // 前导零后面不许再跟数字 (01 非法)
    if b.get(*i).is_some_and(|c| c.is_ascii_digit()) {
        return bad(start);
    }
    let mut is_float = false;
    if b.get(*i) == Some(&b'.') {
        *i += 1;
        if !b.get(*i).is_some_and(|c| c.is_ascii_digit()) {
            return bad(start);
        }
        while b.get(*i).is_some_and(|c| c.is_ascii_digit()) {
            *i += 1;
        }
        is_float = true;
    }
    if matches!(b.get(*i), Some(b'e') | Some(b'E')) {
        *i += 1;
        if matches!(b.get(*i), Some(b'+') | Some(b'-')) {
            *i += 1;
        }
        if !b.get(*i).is_some_and(|c| c.is_ascii_digit()) {
            return bad(start);
        }
        while b.get(*i).is_some_and(|c| c.is_ascii_digit()) {
            *i += 1;
        }
        is_float = true;
    }
    let s = std::str::from_utf8(&b[start..*i]).map_err(|_| "数字不是 ASCII")?;
    if !is_float {
        if let Ok(n) = s.parse::<i64>() {
            return Ok(PyVal::Int(n));
        }
    }
    // 标准库的解析是正确舍入的 —— serde_json 那套快速路径不是, 见 loads 的注释
    s.parse::<f64>()
        .map(PyVal::Float)
        .map_err(|_| format!("解析不了的数字 {s}"))
}

/// 按插入顺序建对象。`pyobj!{ "a" => 1i64, "b" => x }`
#[macro_export]
macro_rules! pyobj {
    ($($k:expr => $v:expr),* $(,)?) => {
        $crate::pyjson::PyVal::Obj(vec![ $( ($k.to_string(), $crate::pyjson::PyVal::from($v)) ),* ])
    };
}

/// Python `round(x, ndigits)`: 按**二进制真值**定点舍入, 平局取偶, 再取最近的 double。
///
/// Rust 的 `{:.*}` 走 flt2dec 精确模式, 舍入规则与 CPython 的 `_Py_dg_dtoa` 一致
/// (实测 2.675→2.67、33.53595→33.5359、0.125→0.12 全对得上), 所以"格式化到 n 位再
/// 解析回来"就是 Python `round` 的语义, 不是近似。
pub fn py_round(x: f64, ndigits: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{:.*}", ndigits, x).parse().unwrap_or(x)
}

/// Python `repr(float)`。
///
/// 与 ryu (serde_json 用的那个) 只在两处不同, 都在这里摆平:
///   · 何时转科学计数法: Python 是指数 < -4 或 >= 16, ryu 是 < -5;
///   · 指数位数: Python 至少两位 (`1e-05` / `1e+16`), ryu 不补。
/// 尾数本身两边都是最短可往返表示, 一致。
pub fn py_repr_f64(x: f64) -> String {
    if x.is_nan() {
        return "NaN".to_string();
    }
    if x.is_infinite() {
        return if x > 0.0 { "Infinity" } else { "-Infinity" }.to_string();
    }
    // `{:e}` 给最短可往返的科学计数法, 从中取十进制指数
    let sci = format!("{:e}", x);
    let (mant, exp) = match sci.split_once('e') {
        Some((m, e)) => (m, e.parse::<i32>().unwrap_or(0)),
        None => return sci,
    };
    if (-4..16).contains(&exp) {
        // 定点区间: Display 在这个区间只出定点形式, 整数补 `.0`
        let s = format!("{x}");
        if s.contains('.') {
            s
        } else {
            format!("{s}.0")
        }
    } else {
        let sign = if exp < 0 { '-' } else { '+' };
        format!("{mant}e{sign}{:02}", exp.abs())
    }
}

/// Python `json.dumps` 的字符串转义 (`ensure_ascii=False`):
/// 只转义 `"`、`\` 与 C0 控制字符; `/` 不转义; 非 ASCII 原样输出。
fn write_str(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

fn write_val(out: &mut String, v: &PyVal, depth: usize, ind: usize) {
    match v {
        PyVal::Null => out.push_str("null"),
        PyVal::Bool(true) => out.push_str("true"),
        PyVal::Bool(false) => out.push_str("false"),
        PyVal::Int(i) => out.push_str(&i.to_string()),
        PyVal::Float(f) => out.push_str(&py_repr_f64(*f)),
        PyVal::Str(s) => write_str(out, s),
        PyVal::List(xs) => {
            if xs.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push_str("[\n");
            for (i, x) in xs.iter().enumerate() {
                if i > 0 {
                    out.push_str(",\n");
                }
                out.push_str(&" ".repeat((depth + 1) * ind));
                write_val(out, x, depth + 1, ind);
            }
            out.push('\n');
            out.push_str(&" ".repeat(depth * ind));
            out.push(']');
        }
        PyVal::Obj(kvs) => {
            if kvs.is_empty() {
                out.push_str("{}");
                return;
            }
            out.push_str("{\n");
            for (i, (k, x)) in kvs.iter().enumerate() {
                if i > 0 {
                    out.push_str(",\n");
                }
                out.push_str(&" ".repeat((depth + 1) * ind));
                write_str(out, k);
                out.push_str(": ");
                write_val(out, x, depth + 1, ind);
            }
            out.push('\n');
            out.push_str(&" ".repeat(depth * ind));
            out.push('}');
        }
    }
}

/// `json.dumps(v, ensure_ascii=False, indent=1)` —— 不含结尾换行。
pub fn dumps(v: &PyVal) -> String {
    dumps_indent(v, 1)
}

/// `json.dumps(v, ensure_ascii=False, indent=n)`
pub fn dumps_indent(v: &PyVal, n: usize) -> String {
    let mut out = String::new();
    write_val(&mut out, v, 0, n);
    out
}

/// `json.dumps(v, ensure_ascii=False)` —— 不带 indent 时的紧凑写法,
/// 分隔符是 `", "` 与 `": "` (这是 Python 不给 separators 时的默认值)。
pub fn dumps_compact(v: &PyVal) -> String {
    match v {
        PyVal::List(xs) => format!(
            "[{}]",
            xs.iter().map(dumps_compact).collect::<Vec<_>>().join(", ")
        ),
        PyVal::Obj(kvs) => format!(
            "{{{}}}",
            kvs.iter()
                .map(|(k, x)| {
                    let mut key = String::new();
                    write_str(&mut key, k);
                    format!("{key}: {}", dumps_compact(x))
                })
                .collect::<Vec<_>>()
                .join(", ")
        ),
        other => {
            let mut out = String::new();
            write_val(&mut out, other, 0, 1);
            out
        }
    }
}

/// `json.dumps(v, sort_keys=True, indent=n)` —— 递归排序键后的缩进输出。
/// autoloop 的 whitelist.json / metrics.json 用的就是这一种 (sort_keys=True)。
pub fn dumps_indent_sorted(v: &PyVal, n: usize) -> String {
    let pad = |d: usize| " ".repeat(d * n);
    fn go(v: &PyVal, d: usize, n: usize, pad: &dyn Fn(usize) -> String) -> String {
        match v {
            PyVal::Obj(kvs) => {
                let mut sorted: Vec<&(String, PyVal)> = kvs.iter().collect();
                sorted.sort_by(|a, b| a.0.cmp(&b.0));
                if sorted.is_empty() {
                    return "{}".into();
                }
                let body: Vec<String> = sorted
                    .iter()
                    .map(|(k, x)| {
                        let mut key = String::new();
                        write_str(&mut key, k);
                        format!("{}{}: {}", pad(d + 1), key, go(x, d + 1, n, pad))
                    })
                    .collect();
                format!("{{\n{}\n{}}}", body.join(",\n"), pad(d))
            }
            PyVal::List(xs) => {
                if xs.is_empty() {
                    return "[]".into();
                }
                let body: Vec<String> = xs
                    .iter()
                    .map(|x| format!("{}{}", pad(d + 1), go(x, d + 1, n, pad)))
                    .collect();
                format!("[\n{}\n{}]", body.join(",\n"), pad(d))
            }
            other => {
                let mut out = String::new();
                write_val(&mut out, other, 0, n);
                out
            }
        }
    }
    go(v, 0, n, &pad)
}

/// `json.dumps(v, sort_keys=True)` 的紧凑版 —— 只用来当去重的键, 所以 ASCII 转义
/// 与否无所谓, 要紧的是**同一组参数出同一串**。
pub fn dumps_sorted_key(v: &PyVal) -> String {
    match v {
        PyVal::Obj(kvs) => {
            let mut sorted: Vec<&(String, PyVal)> = kvs.iter().collect();
            sorted.sort_by(|a, b| a.0.cmp(&b.0));
            format!(
                "{{{}}}",
                sorted
                    .iter()
                    .map(|(k, x)| {
                        let mut key = String::new();
                        write_str(&mut key, k);
                        format!("{key}: {}", dumps_sorted_key(x))
                    })
                    .collect::<Vec<_>>()
                    .join(", ")
            )
        }
        PyVal::List(xs) => format!(
            "[{}]",
            xs.iter().map(dumps_sorted_key).collect::<Vec<_>>().join(", ")
        ),
        other => dumps_compact(other),
    }
}

// ══════════════ Python 语义的统计小件 ══════════════

/// CPython (>=3.12) 内置 `sum()` 对浮点走的 Neumaier 补偿求和。
///
/// 这**不是**"更准一点"的可选优化, 而是对齐字节的必需品: 朴素累加与补偿累加在末位
/// 会差 1 ulp, 落到 `round(x, 4)` 上就可能进位到不同的数。实测
/// `sum([1667.8111, 3130.61, 657.0251, 414.0])` 补偿版是 5869.4462, 朴素版是
/// 5869.446199999999 —— 除以 4 之后两者的 repr 就不一样了。
///
/// 注意只有 Python 的 `sum(...)` 走这条路; 手写的 `acc += x` 循环没有补偿, 那种地方
/// 要照旧朴素累加 (parse 里逐帧累积 gpu_active 就是这种)。
pub fn py_sum(xs: impl IntoIterator<Item = f64>) -> f64 {
    let mut s = 0.0f64;
    let mut c = 0.0f64;
    for x in xs {
        let t = s + x;
        // 大的那个做被减数, 免得低位被直接抹掉
        c += if s.abs() >= x.abs() {
            (s - t) + x
        } else {
            (x - t) + s
        };
        s = t;
    }
    s + c
}

/// `statistics.median` 的 int 版: 奇数个返回**整数**, 偶数个返回两中位数的**浮点**均值。
/// 这个 int/float 之分会原样落到 JSON 字节上, 所以必须保住。
pub fn median_ints(xs: &[i64]) -> Option<PyVal> {
    if xs.is_empty() {
        return None;
    }
    let mut s = xs.to_vec();
    s.sort_unstable();
    let n = s.len();
    Some(if n % 2 == 1 {
        PyVal::Int(s[n / 2])
    } else {
        PyVal::Float((s[n / 2 - 1] + s[n / 2]) as f64 / 2.0)
    })
}

/// `statistics.median` 的 float 版。
pub fn median_f64(xs: &[f64]) -> Option<f64> {
    if xs.is_empty() {
        return None;
    }
    let mut s = xs.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = s.len();
    Some(if n % 2 == 1 {
        s[n / 2]
    } else {
        (s[n / 2 - 1] + s[n / 2]) / 2.0
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 对照表全部来自本机 python3 的实际输出 (见 pyjson 模块注释)。
    #[test]
    fn repr_matches_python() {
        let cases: &[(f64, &str)] = &[
            (3150.0, "3150.0"),
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (0.0001, "0.0001"),
            (1e-5, "1e-05"),
            (1.2e-5, "1.2e-05"),
            (1e-7, "1e-07"),
            (5e-5, "5e-05"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1.23e16, "1.23e+16"),
            (1e17, "1e+17"),
            (123456789012345680.0, "1.2345678901234568e+17"),
            (1e22, "1e+22"),
            (33.536, "33.536"),
            (0.1 + 0.2, "0.30000000000000004"),
            (f64::INFINITY, "Infinity"),
            (f64::NEG_INFINITY, "-Infinity"),
        ];
        for (v, want) in cases {
            assert_eq!(&py_repr_f64(*v), want, "repr({v})");
        }
        assert_eq!(py_repr_f64(f64::NAN), "NaN");
    }

    #[test]
    fn round_matches_python() {
        // 都是 Python round() 的实际结果, 包括"看着该进位其实不进"的那几个
        assert_eq!(py_round(2.675, 2), 2.67);
        assert_eq!(py_round(0.125, 2), 0.12);
        assert_eq!(py_round(-2.675, 2), -2.67);
        assert_eq!(py_round(33.53595, 4), 33.5359);
        assert_eq!(py_round(33.53595, 2), 33.54);
        assert_eq!(py_round(1.0000050000001, 4), 1.0);
        assert_eq!(py_round(0.1 + 0.2, 4), 0.3);
        assert_eq!(py_round(1e-5, 4), 0.0);
        assert!(py_round(f64::INFINITY, 4).is_infinite());
    }

    #[test]
    fn dumps_matches_python_indent1() {
        let v = pyobj! {
            "a" => 1i64,
            "b" => pyobj!{ "c" => vec![1i64, 2], "d" => PyVal::Obj(vec![]) },
            "e" => PyVal::List(vec![]),
            "f" => PyVal::Null,
            "g" => "中",
        };
        assert_eq!(
            dumps(&v),
            "{\n \"a\": 1,\n \"b\": {\n  \"c\": [\n   1,\n   2\n  ],\n  \"d\": {}\n },\n \"e\": [],\n \"f\": null,\n \"g\": \"中\"\n}"
        );
    }

    #[test]
    fn str_escape_matches_python() {
        let v = PyVal::Str("a\"b\\c\nd\te/f\u{1}".to_string());
        assert_eq!(dumps(&v), "\"a\\\"b\\\\c\\nd\\te/f\\u0001\"");
    }

    #[test]
    fn median_keeps_int_float_distinction() {
        assert_eq!(median_ints(&[1, 3, 5]), Some(PyVal::Int(3)));
        assert_eq!(median_ints(&[1, 3, 5, 7]), Some(PyVal::Float(4.0)));
        assert_eq!(median_ints(&[]), None);
        assert_eq!(dumps(&median_ints(&[1, 3, 5]).unwrap()), "3");
        assert_eq!(dumps(&median_ints(&[1, 3, 5, 7]).unwrap()), "4.0");
    }

    #[test]
    fn loads_keeps_number_kind_and_insertion_order() {
        let v = loads(r#"{"b":4800,"a":3150.0,"c":[1,2.5,null,true],"d":{},"b":9}"#).unwrap();
        // int/float 之分保住; 键按插入序; 重复键就地覆盖
        assert_eq!(
            dumps(&v),
            "{\n \"b\": 9,\n \"a\": 3150.0,\n \"c\": [\n  1,\n  2.5,\n  null,\n  true\n ],\n \"d\": {}\n}"
        );
    }

    /// serde_json 未开 float_roundtrip 时曾差 1 ulp；目前冻结计划需要启用该 feature。
    /// 自带读入器和开启 roundtrip 的 serde 必须都与 CPython/标准库一致。
    /// sort_keys + indent=1: autoloop 的 whitelist.json / metrics.json 的形状。
    #[test]
    fn dumps_indent_sorted_matches_python() {
        let v = pyobj! {
            "wl" => pyobj!{ "b" => vec![PyVal::Int(1), PyVal::Int(2)],
                            "a" => pyobj!{ "z" => PyVal::Null, "y" => 1.5 } },
        };
        // python3: json.dumps({'wl':{'b':[1,2],'a':{'z':None,'y':1.5}}}, sort_keys=True, indent=1)
        assert_eq!(
            dumps_indent_sorted(&v, 1),
            "{\n \"wl\": {\n  \"a\": {\n   \"y\": 1.5,\n   \"z\": null\n  },\n  \"b\": [\n   1,\n   2\n  ]\n }\n}"
        );
    }

    #[test]
    fn loads_and_roundtrip_serde_are_correctly_rounded() {
        for s in [
            "-3.9348497207249924",
            "45.872057483182004",
            "-1.7043780545809915",
        ] {
            assert_eq!(dumps(&loads(s).unwrap()), s, "{s} 解析后打印不回来");
            let via_serde: f64 = serde_json::from_str::<f64>(s).unwrap();
            assert_eq!(py_repr_f64(via_serde), s, "{s} 的 frozen JSON 往返不应改变浮点位");
        }
    }

    /// 严格到与 `json.loads` 同一条线上: 这几种都是非法 JSON, 放过去的后果是
    /// 一份畸形的模型回包不再整份作废, 而是漏一组候选到设备上。
    #[test]
    fn loads_is_as_strict_as_json_loads() {
        for bad in [
            "+5", "01", "5.", ".5", "-", "1e", "1e+", "--1", "0x10", "1.2.3",
            "\"raw\u{1}ctl\"",
        ] {
            assert!(loads(bad).is_err(), "{bad:?} 居然收了");
        }
        // 这些是合法的
        for ok in ["-0", "0", "0.5", "1e10", "1E-3", "-1.5e+2", "10", "1.0"] {
            assert!(loads(ok).is_ok(), "{ok:?} 居然拒了");
        }
    }

    /// dumps 会写 NaN / Infinity, loads 就得认得回来 —— 不然含 inf 的 summary.json
    /// 会被 load_runs 静静丢掉。
    #[test]
    fn loads_round_trips_non_finite_like_python() {
        assert!(matches!(loads("NaN"), Ok(PyVal::Float(f)) if f.is_nan()));
        assert_eq!(loads("Infinity"), Ok(PyVal::Float(f64::INFINITY)));
        assert_eq!(loads("-Infinity"), Ok(PyVal::Float(f64::NEG_INFINITY)));
        let v = pyobj! { "a" => f64::INFINITY, "b" => f64::NEG_INFINITY };
        assert_eq!(loads(&dumps(&v)).unwrap(), v);
        // 负号后面不是 Infinity 时照旧按数字走
        assert_eq!(loads("-5"), Ok(PyVal::Int(-5)));
    }

    /// 畸形输入要回错误, 不能 panic 或把栈撑爆。
    #[test]
    fn loads_survives_hostile_input() {
        // 高位代理后面跟的不是低位: 减法会下溢, 必须先验
        assert!(loads(r#""\ud83d\ud83d""#).is_err());
        assert!(loads(r#""\ud83d""#).is_err());
        assert!(loads(r#""\ud83dx""#).is_err());
        // 深嵌套: 有上限就回错误, 没上限就 abort
        let deep = format!("{}{}", "[".repeat(200_000), "]".repeat(200_000));
        assert!(loads(&deep).is_err());
        assert!(loads(&format!("{}{}", "[".repeat(100), "]".repeat(100))).is_ok());
    }

    #[test]
    fn loads_handles_escapes_and_rejects_trailing_junk() {
        let v = loads(r#""a\"b\\c\nd\te\u0001\u4e2d\ud83d\ude00""#).unwrap();
        assert_eq!(v, PyVal::Str("a\"b\\c\nd\te\u{1}中😀".into()));
        assert!(loads("{} junk").is_err());
        assert!(loads("[1,2").is_err());
        assert!(loads("").is_err());
    }
}
