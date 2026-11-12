//! 让大模型从白名单里挑下一组系统参数。
//!
//! 由 `loop_v1/auto/llm.py` 逐行搬过来, 输出字节一致。三条约束与
//! `game_opt_loop/src/engine/generator.rs` 同源:
//!
//! 1. **密钥只读不带走**。从 `secrets.env` 读, 只用于 Authorization 头, 不进日志、
//!    不进 prompt、不进任何归档文件。
//! 2. **失败必须可降级**。LLM 会超时、限流、空回包, 任何失败都只让这一代作废,
//!    由本地变异器补齐 —— 闭环不能因为某个 API 抽风就停摆。
//! 3. **模型只出参数, 不出结论**。好不好由真机 + 置换检验裁决。模型看得到历史每组
//!    参数的实测数字与 p 值, 但没有任何改写判定的途径。
//!
//! HTTP 走 curl 子进程, 与 `brain.rs` 一致 (绕 WAF 指纹问题, 也省一个依赖)。
//! 本地变异器的随机数走 [`crate::pyrandom`] —— 它是 CPython `random` 的逐位复刻,
//! 因为「固定 seed 下产出确定」是这条降级路径写进证据的声明。

use crate::pyjson::{dumps_compact, dumps_sorted_key, loads, PyVal};
use crate::pyrandom::PyRandom;
use crate::sysparam::{validate_candidate, wl_get, Whitelist};
use regex::Regex;
use std::collections::HashSet;

pub struct Provider {
    pub name: &'static str,
    pub url: &'static str,
    pub model: &'static str,
    pub key_env: &'static str,
    pub timeout_s: u32,
    pub thinking_disable: bool,
}

/// 与 generator.rs 的 PROVIDERS 同源, 按优先级排列。
pub const PROVIDERS: [Provider; 4] = [
    Provider {
        name: "glm-4.7",
        url: "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions",
        model: "glm-4.7",
        key_env: "GLM_KEY",
        timeout_s: 180,
        thinking_disable: true,
    },
    Provider {
        name: "kimi-k2.7-code",
        url: "https://api.kimi.com/coding/v1/chat/completions",
        model: "kimi-k2.7-code",
        key_env: "KIMI_KEY",
        timeout_s: 180,
        thinking_disable: true,
    },
    Provider {
        name: "siliconflow-qwen3-coder",
        url: "https://api.siliconflow.cn/v1/chat/completions",
        model: "Qwen/Qwen3-Coder-30B-A3B-Instruct",
        key_env: "SILICONFLOW_KEY",
        timeout_s: 180,
        thinking_disable: false,
    },
    Provider {
        name: "openrouter",
        url: "https://openrouter.ai/api/v1/chat/completions",
        model: "qwen/qwen3-coder",
        key_env: "OPENROUTER_KEY",
        timeout_s: 180,
        thinking_disable: false,
    },
];

pub const SYSTEM_PROMPT: &str = "你是移动端系统性能调优专家。给你一台已 root 的安卓手机的可写系统参数白名单, \
以及历史上每组参数在真机游戏负载下的实测结果。你的任务是挑下一批候选参数组。\n\
硬规矩:\n\
1. 只能用白名单里的参数名, 值只能从给出的取值表里选, 一个字都不能改。\n\
2. 绝不提出任何与温控保护相关的改动 (关热保护、抬温控阈值), 提了会被直接拒绝。\n\
3. 每组 1-4 个参数。参数少一点容易归因, 别一次全改。\n\
4. 各组之间要有明显差异, 不要提交几乎一样的组。\n\
5. 历史里标了「写不进去 (被内核夹回)」的值, 不要再提 —— 取值表列出的是合法值, \
但内核会按当下的热限/上下限把某些值夹回去, 那一组会整组作废。\n\
6. 只输出 JSON, 不要解释、不要 markdown 代码块以外的任何文字。\n\
输出格式 (顶层是数组):\n\
[{\"why\":\"一句话说明这组想验证什么\",\"params\":{\"参数名\":\"值\"}}, ...]";

// ══════════════ 密钥 ══════════════

/// 只认 `KEY=VALUE` 与 `export KEY=VALUE`, 引号可选。
///
/// 刻意**不**做 shell 展开 —— 这是一份配置不是脚本, `$(...)`、反引号、管道一律当
/// 普通字符, 不给命令注入留口子 (与 generator.rs::parse_secrets 同规则)。
pub fn parse_secrets(text: &str) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for line in text.lines() {
        let mut line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some(rest) = line.strip_prefix("export ") {
            line = rest.trim();
        }
        let Some((k, v)) = line.split_once('=') else {
            continue;
        };
        let mut v = v.trim().to_string();
        // 成对的引号才剥, 单边的原样留下
        let b: Vec<char> = v.chars().collect();
        if b.len() >= 2 && b[0] == b[b.len() - 1] && (b[0] == '"' || b[0] == '\'') {
            v = b[1..b.len() - 1].iter().collect();
        }
        let k = k.trim().to_string();
        match out.iter_mut().find(|(ek, _)| *ek == k) {
            Some(slot) => slot.1 = v,
            None => out.push((k, v)),
        }
    }
    out
}

/// 第一个**存在**的文件就用它。
///
/// 存在但读不出来是错, 不是"当它不存在": 悄悄跳过的后果是整局只靠本地变异器跑,
/// 而日志里只有一句"没有密钥", 查半天才发现是文件权限。Python 那边 open() 直接抛。
pub fn load_keys(paths: &[String]) -> Result<Vec<(String, String)>, String> {
    for p in paths {
        if p.is_empty() || !std::path::Path::new(p).exists() {
            continue;
        }
        let t = std::fs::read_to_string(p).map_err(|e| format!("{p} 读不出来: {e}"))?;
        return Ok(parse_secrets(&t));
    }
    Ok(Vec::new())
}

fn key_of(keys: &[(String, String)], env: &str) -> Option<String> {
    keys.iter()
        .find(|(k, _)| k == env)
        .map(|(_, v)| v.clone())
        .filter(|v| !v.is_empty())
        .or_else(|| std::env::var(env).ok().filter(|v| !v.is_empty()))
}

// ══════════════ prompt ══════════════

pub fn build_prompt(whitelist_desc: &str, history: &[PyVal], n: usize, goal_note: &str) -> String {
    let mut lines: Vec<String> = vec![
        "# 设备与负载".into(),
        "红魔 NX809J (骁龙 canoe / Adreno 840v2, Android 16, 已 root)。".into(),
        "负载: 原神大世界定点匀速转视角 36 秒, 游戏被厂商限帧器钉在 30fps。".into(),
        "".into(),
        "# 判定口径 (已冻结, 你改不了)".into(),
        "看三样: frame_p95 (帧时 95 分位, 越小越好)、fps_mean (越大越好)、".into(),
        "power_w_mean (整机功耗, 越小越好)。每组参数跑 A/B 交替多轮, 精确置换检验。".into(),
        "任一指标显著改善且无任何指标显著变差 = 保留, 否则淘汰。".into(),
        "**目标不是单纯省电** —— 把帧时压下去同样算赢。".into(),
        "".into(),
        "# 可改参数白名单 (只能用这些)".into(),
        whitelist_desc.to_string(),
        "".into(),
    ];
    if !goal_note.is_empty() {
        lines.push(goal_note.to_string());
        lines.push("".into());
    }
    if !history.is_empty() {
        lines.push("# 已经试过的参数组与真机实测结果".into());
        for h in history {
            let params = h.get("params").cloned().unwrap_or(PyVal::Obj(vec![]));
            lines.push(format!("- 参数 {}", dumps_compact(&params)));
            lines.push(format!(
                "  判定 {} — {}",
                h.get("verdict").map(|v| v.py_str()).unwrap_or("None".into()),
                h.get("reason").map(|v| v.py_str()).unwrap_or_default()
            ));
            if let Some(PyVal::List(cs)) = h.get("clamped") {
                for c in cs {
                    lines.push(format!("  ⚠ 这个值写不进去 (被内核夹回): {}", c.py_str().trim()));
                }
            }
            if let Some(PyVal::Obj(pm)) = h.get("per_metric") {
                for (m, d) in pm {
                    let Some(dp) = d.get("diff_pct") else { continue };
                    if matches!(dp, PyVal::Null) {
                        continue;
                    }
                    lines.push(format!(
                        "  {m}: {}% (p={}) {}",
                        dp.py_str(),
                        d.get("p").map(|v| v.py_str()).unwrap_or("None".into()),
                        d.get("status").map(|v| v.py_str()).unwrap_or("None".into())
                    ));
                }
            }
        }
        lines.push("".into());
        lines.push("别再提交与上面雷同的组。从被淘汰的组里学到的方向也请说明在 why 里。".into());
    } else {
        lines.push("# 历史".into());
        lines.push("这是第一代, 还没有任何实测结果。".into());
        lines.push(
            "已知线索: 之前手工验证过把 DDR 与 LLCC 的 boost_freq 钉到硬件上限, \
frame_p95 改善 1.61% (p=0.0079)。说明这台机器上访存下限确实吃紧。"
                .into(),
        );
    }
    lines.push("".into());
    lines.push(format!("# 现在给我 {n} 组候选, 只输出 JSON 数组。"));
    lines.join("\n")
}

// ══════════════ 回包解析 ══════════════

/// 一组候选: why + 有序的参数表。
#[derive(Debug, Clone, PartialEq)]
pub struct Candidate {
    pub why: String,
    pub params: Vec<(String, String)>,
}

impl Candidate {
    pub fn to_pyval(&self) -> PyVal {
        crate::pyobj! {
            "why" => self.why.clone(),
            "params" => PyVal::Obj(self.params.iter()
                .map(|(k, v)| (k.clone(), PyVal::Str(v.clone()))).collect()),
        }
    }
    pub fn params_pyval(&self) -> PyVal {
        PyVal::Obj(
            self.params
                .iter()
                .map(|(k, v)| (k.clone(), PyVal::Str(v.clone())))
                .collect(),
        )
    }
}

/// 从模型回包里抠出候选数组。纯函数, 容忍 markdown 围栏与前后废话。
pub fn parse_candidates(text: &str, n: usize) -> Vec<Candidate> {
    if text.is_empty() {
        return Vec::new();
    }
    let mut s = text.trim().to_string();
    // (?s) 让 . 跨行, 与 Python 的 re.S 一致
    if let Some(c) = Regex::new(r"(?s)```(?:json)?\s*(.+?)```")
        .expect("fence re")
        .captures(&s)
    {
        s = c[1].trim().to_string();
    }
    let (Some(start), Some(end)) = (s.find('['), s.rfind(']')) else {
        return Vec::new();
    };
    if end <= start {
        return Vec::new();
    }
    let Ok(PyVal::List(arr)) = loads(&s[start..=end]) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for item in arr {
        let PyVal::Obj(_) = item else { continue };
        let Some(PyVal::Obj(params)) = item.get("params") else {
            continue;
        };
        if params.is_empty() {
            continue;
        }
        out.push(Candidate {
            // Python 的 [:300] 按**字符**切, 不是字节
            why: item
                .get("why")
                .map(|v| v.py_str())
                .unwrap_or_default()
                .chars()
                .take(300)
                .collect(),
            params: params.iter().map(|(k, v)| (k.clone(), v.py_str())).collect(),
        });
        if out.len() >= n {
            break;
        }
    }
    out
}

// ══════════════ 模型调用 ══════════════

/// [`chat`] 的过桥版: 日志行随结果一起回, 由调用方重放 —— 过桥没法回调。
pub fn chat_with_log(
    prompt: &str,
    keys: &[(String, String)],
    tmp_dir: &str,
) -> (Option<(String, String)>, Vec<String>) {
    let mut lines: Vec<String> = Vec::new();
    let got = chat(prompt, keys, tmp_dir, |m| lines.push(m.to_string()));
    (got, lines)
}

/// 按优先级依次试 provider。返回 (provider 名, 回包文本), 全挂返回 None。
pub fn chat(
    prompt: &str,
    keys: &[(String, String)],
    tmp_dir: &str,
    mut log: impl FnMut(&str),
) -> Option<(String, String)> {
    for p in &PROVIDERS {
        let Some(key) = key_of(keys, p.key_env) else {
            continue;
        };
        let mut body = crate::pyobj! {
            "model" => p.model,
            "messages" => PyVal::List(vec![
                crate::pyobj!{ "role" => "system", "content" => SYSTEM_PROMPT },
                crate::pyobj!{ "role" => "user", "content" => prompt },
            ]),
            "temperature" => 0.8,
            "max_tokens" => 2000i64,
        };
        if p.thinking_disable {
            if let PyVal::Obj(kvs) = &mut body {
                kvs.push((
                    "thinking".into(),
                    crate::pyobj! { "type" => "disabled" },
                ));
            }
        }
        // 密钥与请求体都**不进命令行**: argv 在 ps 输出里人人可见, 而
        // Authorization 头就在里面。curl 的 -K 配置文件同时解决这两件事。
        //
        // 目录每次调用独占且带进程号与序号: 固定文件名的话, 两个并发调用会互相
        // POST 对方的 prompt (于是归档的 prompt.txt 与模型实际答的不是一份),
        // 或者互相删掉请求体; 在 /tmp 这种公共目录上还能被预先放好的符号链接
        // 变成"往任意文件写"。
        let dir = match private_tmp_dir(tmp_dir) {
            Ok(d) => d,
            Err(e) => {
                log(&format!("[llm] 建不了临时目录: {e}"));
                return None;
            }
        };
        let cfg = dir.join("curl.cfg");
        let bp = dir.join("req.json");
        let cfg_text = format!(
            "url = {}\nheader = \"Authorization: Bearer {}\"\nheader = \"Content-Type: application/json\"\ndata = @{}\nmax-time = {}\nsilent\n",
            p.url,
            key,
            bp.display(),
            p.timeout_s
        );
        let wrote = std::fs::write(&bp, dumps_compact(&body))
            .and_then(|_| write_private(&cfg, &cfg_text));
        if let Err(e) = wrote {
            log(&format!("[llm] 写不了请求体临时文件: {e}"));
            let _ = std::fs::remove_dir_all(&dir);
            return None;
        }
        let out = std::process::Command::new("curl")
            .arg("-K")
            .arg(&cfg)
            .output();
        // 配置文件里有密钥, 无论成败都立刻删掉整个目录
        let _ = std::fs::remove_dir_all(&dir);
        let out = match out {
            Ok(o) => o,
            Err(e) => {
                // 只记异常类型与首行, 绝不打印请求头 (含密钥)
                log(&format!("[llm] {} 失败: curl: {}", p.name, clip(&e.to_string(), 120)));
                continue;
            }
        };
        let raw = String::from_utf8_lossy(&out.stdout).to_string();
        let content = loads(&raw)
            .ok()
            .and_then(|v| match v.get("choices") {
                Some(PyVal::List(cs)) => cs.first().cloned(),
                _ => None,
            })
            .and_then(|c| c.get("message").and_then(|m| m.get("content")).cloned());
        match content {
            Some(PyVal::Str(c)) if !c.trim().is_empty() => return Some((p.name.to_string(), c)),
            Some(_) | None if raw.trim().is_empty() => {
                log(&format!("[llm] {} 空回包, 换下一个", p.name));
            }
            _ => log(&format!(
                "[llm] {} 失败: 回包里没有 choices[0].message.content: {}",
                p.name,
                clip(raw.trim(), 120)
            )),
        }
    }
    None
}

fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// 本次调用独占的临时目录, 0700。
fn private_tmp_dir(base: &str) -> std::io::Result<std::path::PathBuf> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static SEQ: AtomicU64 = AtomicU64::new(0);
    let base = if base.is_empty() {
        std::env::temp_dir()
    } else {
        std::path::PathBuf::from(base)
    };
    let d = base.join(format!(
        "phonefarm-llm-{}-{}",
        std::process::id(),
        SEQ.fetch_add(1, Ordering::Relaxed)
    ));
    // 已存在就是有人抢在前面, 换一个名字重来而不是往里写
    if d.exists() {
        std::fs::remove_dir_all(&d)?;
    }
    std::fs::create_dir_all(&d)?;
    set_private(&d)?;
    Ok(d)
}

#[cfg(unix)]
fn set_private(p: &std::path::Path) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o700))
}

#[cfg(not(unix))]
fn set_private(_p: &std::path::Path) -> std::io::Result<()> {
    Ok(())
}

/// 写一个只有自己读得了的文件 (里面有密钥)。
fn write_private(p: &std::path::Path, text: &str) -> std::io::Result<()> {
    std::fs::write(p, text)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

// ══════════════ 本地降级变异器 ══════════════

/// 这个参数的取值大小有没有「更高 = 更激进」的物理含义?
///
/// 频率 (Hz/kHz) 和 pwrlevel 档位有: 数字大小直接对应快慢。
/// 厂商枚举没有 —— `refresh_rate_mode` 的 1 是 60Hz 而 0 是 120Hz auto,
/// 按数值往上推会把「降刷新率」当成「更激进」。governor 是字符串, 更没有。
/// 分不清就当没有: 等概率换一个别的值, 比装作知道方向要诚实。
pub fn is_ordinal(pid: &str, kind: &str) -> bool {
    kind == "sysfs" && (pid.contains("freq") || pid.contains("pwrlevel"))
}

/// Python `s.lstrip("-").isdigit()`
fn is_int_literal(s: &str) -> bool {
    let t = s.trim_start_matches('-');
    !t.is_empty() && t.bytes().all(|c| c.is_ascii_digit())
}

/// 本地降级变异器 —— LLM 全挂时闭环照常往下跑。
///
/// 策略: 随机挑 1-2 个白名单参数, 往「更激进」的方向推 (频率类取更高档, pwrlevel
/// 取更快档), 避开历史上已经试过的组, 并且**必须过白名单校验** —— 跨项约束
/// (min<=max 之类) 由 `validate_candidate` 统一把关, 这里不重复实现。
/// 固定 seed 下产出确定, 所以降级路径本身也是可复现的。
pub fn local_mutate(wl: &Whitelist, history: &[PyVal], n: usize, seed: i64) -> Vec<Candidate> {
    let mut rng = PyRandom::new(seed);
    let mut tried: HashSet<String> = history
        .iter()
        .map(|h| dumps_sorted_key(&h.get("params").cloned().unwrap_or(PyVal::Obj(vec![]))))
        .collect();
    let mut pids: Vec<&String> = wl.iter().map(|(k, _)| k).collect();
    pids.sort();
    let mut out: Vec<Candidate> = Vec::new();
    for _ in 0..n * 60 {
        if out.len() >= n {
            break;
        }
        // Python 的 `min(len(pids), rng.choice([1, 2]))`: len 先算, choice 后算 ——
        // 无论 min 取谁, choice 每轮都消耗一个随机数, 顺序不能变
        let want = *rng.choice(&[1usize, 2]).expect("非空");
        let k = pids.len().min(want);
        let picked: Vec<&String> = rng
            .sample_indices(pids.len(), k)
            .into_iter()
            .map(|i| pids[i])
            .collect();
        let mut params: Vec<(String, String)> = Vec::new();
        for pid in picked {
            let Some(spec) = wl_get(wl, pid) else { continue };
            let (vals, cur) = (&spec.values, &spec.current);
            let numeric = !vals.is_empty() && vals.iter().all(|v| is_int_literal(v));
            let cands: Vec<String> = if is_ordinal(pid, &spec.kind) && numeric && is_int_literal(cur)
            {
                let c: i64 = cur.parse().unwrap_or(0);
                // pwrlevel 语义反过来: 0 是最快档, 所以「更激进」= 往小走。
                // 没有更激进的取值就跳过这一项, 不往回退 —— 往回退等于在试一个与
                // 「探索更高性能」意图相反的方向, 那不是变异, 是噪声。
                let up = !pid.contains("pwrlevel");
                vals.iter()
                    .filter(|v| {
                        let x: i64 = v.parse().unwrap_or(0);
                        if up {
                            x > c
                        } else {
                            x < c
                        }
                    })
                    .cloned()
                    .collect()
            } else {
                // 取值是厂商枚举 (如 refresh_rate_mode 的 0/1/2/4) 或 governor 名字,
                // 数值大小没有「更激进」的含义 —— 1 是 60Hz 而 0 是 120Hz auto。
                // 这种只能等概率换一个别的值, 不许假装知道方向。
                vals.iter().filter(|v| *v != cur).cloned().collect()
            };
            if let Some(v) = rng.choice(&cands) {
                params.push((pid.clone(), v.clone()));
            }
        }
        if params.is_empty() {
            continue;
        }
        let cand = Candidate {
            why: "本地变异器降级产出 (LLM 不可用)".into(),
            params,
        };
        let key = dumps_sorted_key(&cand.params_pyval());
        if tried.contains(&key) {
            continue;
        }
        if !validate_candidate(&cand.params, wl).0 {
            continue;
        }
        tried.insert(key);
        out.push(cand);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pyjson::dumps;
    use crate::sysparam::{build_whitelist, parse_probe};

    const PROBE: &str = "cpu.policies=0
cpu.policy0.avail_freqs=300000 1000000 2000000
cpu.policy0.avail_governors=schedutil performance
cpu.policy0.scaling_min_freq.cur=300000
cpu.policy0.scaling_max_freq.cur=2000000
cpu.policy0.scaling_governor.cur=schedutil
cpu.policy0.scaling_min_freq.writable=yes
cpu.policy0.scaling_min_freq.effect=live
cpu.policy0.scaling_max_freq.writable=yes
cpu.policy0.scaling_governor.writable=yes
gpu.num_pwrlevels=4
gpu.min_pwrlevel.cur=3
gpu.max_pwrlevel.cur=0
gpu.min_pwrlevel.writable=yes
gpu.min_pwrlevel.effect=live
gpu.max_pwrlevel.writable=yes
bus.DDR.boost_freq.cur=0
bus.DDR.avail_freqs=200000 3200000 5333000
bus.DDR.boost_freq.writable=yes
bus.DDR.boost_freq.effect=live
setting.system.peak_refresh_rate.cur=120
setting.system.min_refresh_rate.cur=60
display.modes=fps=60,fps=120
";

    fn wl() -> Whitelist {
        build_whitelist(&parse_probe(PROBE))
    }

    #[test]
    fn parses_fenced_json_and_stringifies_values() {
        let out = parse_candidates(
            "好的\n```json\n[{\"why\":\"试试总线\",\"params\":{\"bus.DDR.boost_freq\":\"5333000\"}}]\n```",
            3,
        );
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].params[0].1, "5333000");
        // 数字值按 Python str() 转成字符串
        let out = parse_candidates("[{\"why\":\"x\",\"params\":{\"a\":123}}]", 3);
        assert_eq!(out[0].params[0].1, "123");
    }

    #[test]
    fn drops_malformed_items_and_caps_count() {
        let out = parse_candidates(
            "[{\"params\":{}}, \"junk\", {\"why\":\"a\",\"params\":{\"x\":\"1\"}}, \
{\"why\":\"b\",\"params\":{\"y\":\"2\"}}, {\"why\":\"c\",\"params\":{\"z\":\"3\"}}]",
            2,
        );
        assert_eq!(out.len(), 2);
    }

    #[test]
    fn garbage_returns_empty_not_crash() {
        for bad in ["", "没有 JSON", "[", "[not json]", "{}", "]["] {
            assert!(parse_candidates(bad, 3).is_empty(), "{bad:?}");
        }
    }

    /// why 超长按**字符**截到 300, 不是按字节 —— 中文一刀切在字节上会切出乱码。
    #[test]
    fn why_is_clipped_by_chars() {
        let long = "中".repeat(500);
        let raw = format!("[{{\"why\":\"{long}\",\"params\":{{\"a\":\"1\"}}}}]");
        assert_eq!(parse_candidates(&raw, 1)[0].why.chars().count(), 300);
    }

    #[test]
    fn secrets_take_export_and_quotes_without_shell_expansion() {
        let s = parse_secrets("# c\nexport A=1\nB=\"two\"\nC='three'\nbad line\n");
        assert_eq!(
            s,
            vec![
                ("A".into(), "1".into()),
                ("B".into(), "two".into()),
                ("C".into(), "three".into())
            ]
        );
        // 配置不是脚本: $(...) 一律当普通字符, 不给命令注入留口子
        let s = parse_secrets("K=$(rm -rf /)");
        assert_eq!(s[0].1, "$(rm -rf /)");
    }

    #[test]
    fn ordinal_only_for_freq_and_pwrlevel_sysfs() {
        assert!(is_ordinal("bus.DDR.boost_freq", "sysfs"));
        assert!(is_ordinal("gpu.min_pwrlevel", "sysfs"));
        // refresh_rate_mode 的 1 是 60Hz 而 0 是 120Hz auto —— 数值大小无方向含义
        assert!(!is_ordinal("setting.system.refresh_rate_mode", "setting"));
        assert!(!is_ordinal("cpu.policy0.scaling_governor", "sysfs"));
    }

    #[test]
    fn mutator_produces_valid_distinct_candidates() {
        let wl = wl();
        let out = local_mutate(&wl, &[], 3, 7);
        assert_eq!(out.len(), 3);
        let mut seen = HashSet::new();
        for c in &out {
            assert!(validate_candidate(&c.params, &wl).0, "{:?}", c.params);
            assert!(seen.insert(dumps_sorted_key(&c.params_pyval())), "出了重复组");
        }
    }

    #[test]
    fn mutator_is_deterministic_for_a_given_seed() {
        let wl = wl();
        let a = local_mutate(&wl, &[], 3, 11);
        let b = local_mutate(&wl, &[], 3, 11);
        assert_eq!(a, b);
    }

    #[test]
    fn mutator_avoids_history() {
        let wl = wl();
        let first = local_mutate(&wl, &[], 1, 3);
        let hist = vec![crate::pyobj! { "params" => first[0].params_pyval() }];
        let again = local_mutate(&wl, &hist, 2, 3);
        assert!(
            !again.iter().any(|c| c.params == first[0].params),
            "又提了一次历史里已有的组"
        );
    }

    /// 每组生成的 plan 都必须落在白名单里 —— 30 个 seed 全走一遍。
    #[test]
    fn every_generated_candidate_stays_inside_the_whitelist() {
        let wl = wl();
        for seed in 0..30 {
            for c in local_mutate(&wl, &[], 3, seed) {
                let (ok, why) = validate_candidate(&c.params, &wl);
                assert!(ok, "seed={seed}: {why}");
                let plan = crate::sysparam::plan_text(&c.params, &wl).unwrap();
                for line in plan.lines().filter(|l| !l.starts_with('#')) {
                    let cols: Vec<&str> = line.split('\t').collect();
                    assert_eq!(cols.len(), 3);
                    assert!(cols[0] == "sysfs" || cols[0] == "setting");
                    assert!(!crate::sysparam::denied(cols[1]), "{line}");
                }
            }
        }
    }

    #[test]
    fn prompt_carries_the_clamp_warning_and_system_rules() {
        let hist = vec![crate::pyobj! {
            "params" => crate::pyobj!{ "gpu.min_pwrlevel" => "0" },
            "verdict" => "ABORT",
            "reason" => "旋钮未全部生效",
            "clamped" => vec!["KNOB_FAIL /sys/class/kgsl/kgsl-3d0/min_pwrlevel: 想写 0, 回读 2 (原值 17)"],
        }];
        let p = build_prompt("- gpu.min_pwrlevel (当前 17) 可选: 0 .. 17", &hist, 3, "");
        assert!(p.contains("写不进去"), "{p}");
        assert!(p.contains("回读 2"));
        assert!(SYSTEM_PROMPT.contains("被内核夹回"));
    }

    /// 历史里的参数用紧凑 JSON 印出来 (不是 indent=1), 与旧版一致。
    #[test]
    fn prompt_prints_history_params_compactly() {
        let hist = vec![crate::pyobj! {
            "params" => crate::pyobj!{ "b" => "2", "a" => "1" },
            "verdict" => "REJECT",
            "reason" => "没有任何指标显著改善",
            "per_metric" => crate::pyobj!{
                "frame_p95" => crate::pyobj!{ "diff_pct" => 1.25, "p" => 0.5, "status" => "无显著变化" },
                "fps_mean" => crate::pyobj!{ "diff_pct" => PyVal::Null },
            },
        }];
        let p = build_prompt("wl", &hist, 2, "");
        assert!(p.contains("- 参数 {\"b\": \"2\", \"a\": \"1\"}"), "{p}");
        assert!(p.contains("  frame_p95: 1.25% (p=0.5) 无显著变化"), "{p}");
        // diff_pct 是 null 的那一项不该出现
        assert!(!p.contains("fps_mean:"), "{p}");
        assert!(!p.contains("这是第一代"));
        assert!(build_prompt("wl", &[], 2, "").contains("这是第一代"));
    }

    #[test]
    fn candidate_serializes_in_insertion_order() {
        let c = Candidate {
            why: "x".into(),
            params: vec![("b".into(), "2".into()), ("a".into(), "1".into())],
        };
        assert_eq!(
            dumps(&c.to_pyval()),
            "{\n \"why\": \"x\",\n \"params\": {\n  \"b\": \"2\",\n  \"a\": \"1\"\n }\n}"
        );
    }
}
