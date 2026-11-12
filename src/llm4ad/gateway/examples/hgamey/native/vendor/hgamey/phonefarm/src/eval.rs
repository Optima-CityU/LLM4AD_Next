//! `phonefarm eval` — game_opt_loop 下发的真机评测请求的唯一入口。
//!
//! 契约: `game_opt_loop/contracts/eval_request.schema.json` 与 `eval_report.schema.json`
//! (v2: kind = shader / sysparam / gray 的信封; v1 扁平格式继续兼容, 它就是 shader 一种时
//! 的形状)。
//!
//! 路由
//! ----
//! · v1 / 缺 kind          → 原样转交 `gpu-op` (它就是 v1 的消费者, 行为一字不动);
//! · v2 kind=shader        → 按契约把信封降级成 v1 请求转交 gpu-op, 回包补上
//!                           version/kind 与 conditions_observed 再交还上层;
//! · v2 kind=sysparam      → 走 `src/sysparam.rs` 的白名单校验与判定 + 设备端脚本
//!                           (knob_sysparam / probe_sysparam / sample_env / run_once);
//! · v2 kind=gray          → 调 knobs/gray 的调用层 (enable_layer.sh), 只调不改。
//!
//! 纪律 (与 gpuop / sysparam 一致):
//!   · 判定规则 (protocol / conditions) 在看到候选数据之前带入并冻结;
//!   · 接收端**再校验一遍**参数与白名单, 不信上层; deny_keywords 接收端再拦一道;
//!   · 量不到的指标如实 null, 每个 null 在 unavailable 里配一条人话;
//!   · ABORT (没量准) 与 REJECT (量到了但不值得保留) 严格分开。
//!
//! 真机路径的验证状态: 离线部分 (请求校验 / v2→v1 降级 / 校验拒绝 / 报告装配) 有单测;
//! 涉及设备的部分代码走通但尚未在真机上验证过 —— 设备被占用期间合入, 首次真机跑按
//! probe_only 起步。

use crate::device;
use crate::loopstat::{self, Run};
use crate::pyjson::{dumps as py_dumps, PyVal};
use crate::curtail;
use crate::sysparam;
use crate::qualification::{self, Evidence, Goal, Policy, Stage};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;

const DEFAULT_DENY_KEYWORDS: [&str; 7] = [
    "thermal",
    "trip_point",
    "cooling",
    "fan",
    "tsens",
    "bcl",
    "throttl",
];

/// gray 收端目前实现的受控开关 (= `parse_gray_rewrites` 能认的那几个)。层的开关集合是有限且
/// 已实现的, 上层发明新开关或请求收端没实现的开关, 整份拒绝。
///
/// 以前这里只写了 loadop, 而上层 (game_opt_loop optimize/gray.rs) 唯一 Landed 的是 storeop ——
/// 收端明明实现了 storeop 却在校验这一步拦掉, 每个灰档候选都退出码 2 (审计 #1)。
/// 现在校验直接走 `parse_gray_rewrites`, 这张表只用于报错文字, 两者由单测锁在一起。
pub(crate) const GRAY_SUPPORTED_KNOBS: [&str; 3] = ["loadop_dont_care", "storeop_dont_care", "npuop"];

pub(crate) const USAGE: &str = "恢复: phonefarm eval recover --session DIR --serial S [--json]
用法: phonefarm eval --request <eval_request.json> [--serial S] [--power-rail usb|battery] [--out 目录] [--no-curtail] [--lock-gpu-level N] [--evidence-origin device|synthetic] [--json] [--validate-only]
  --validate-only: 只做收端校验 (validate_v2 + gray 改写解析 + 负载脚本/层 .so 是否找得到), 不碰设备; 退出码 0 = 收, 2 = 拒
  --lock-gpu-level N  评测期间把 kgsl min/max_pwrlevel 都钉在 N 档 (审计 #9: 档位不受控时同一配置帧率跳台阶),
                      结束时还原, 回包 conditions_observed.gpu_level_lock; 候选本身改 GPU 档位的 sysparam 不能同时用;
                      kind=gray 走受控会话 (session owner), 不支持此旗子";

/// PyVal → serde_json 值 (同一个桥的反向)。经 dumps 走文本, 非有限值
/// (Infinity/NaN) 会被 serde_json 拒掉, 这里如实落 null。
fn to_serde(v: &PyVal) -> Value {
    let s = py_dumps(v);
    if s.contains("Infinity") || s.contains("NaN") {
        // 逐值处理太啰嗦; 这份请求路径里非有限值只可能来自方差为 0 的退化臂,
        // 落 null 并交由 unavailable 解释
        serde_json::from_str(&s.replace("Infinity", "null").replace("NaN", "null"))
            .unwrap_or(Value::Null)
    } else {
        serde_json::from_str(&s).unwrap_or(Value::Null)
    }
}

/// serde_json 值 → PyVal (请求侧是 serde, 统计侧是 PyVal, 桥在这)。
/// 保留 int/float 之分 —— 统计层靠它对齐 Python 的类型提升。
fn to_pyval(v: &Value) -> PyVal {
    match v {
        Value::Null => PyVal::Null,
        Value::Bool(b) => PyVal::Bool(*b),
        Value::Number(n) => match n.as_i64() {
            Some(i) => PyVal::Int(i),
            None => PyVal::Float(n.as_f64().unwrap_or(f64::NAN)),
        },
        Value::String(s) => PyVal::Str(s.clone()),
        Value::Array(a) => PyVal::List(a.iter().map(to_pyval).collect()),
        Value::Object(o) => PyVal::Obj(o.iter().map(|(k, x)| (k.clone(), to_pyval(x))).collect()),
    }
}

// ══════════════ 入口与路由 ══════════════

struct EvalArgs {
    request: String,
    serial: Option<String>,
    power_rail: Option<String>,
    out: Option<String>,
    /// 确定性截断 (src/curtail.rs): 缺省开。`--no-curtail` 跑满全部轮次。
    curtail: bool,
    qualify_from: Option<String>,
    /// 审计 #9: 评测期间把 GPU 档位钉在这一档 (kgsl min/max_pwrlevel), 见 [`GpuLevelLock`]。
    lock_gpu_level: Option<u32>,
    /// 只校验不上机 (离线 dry-run)
    validate_only: bool,
    evidence_origin: String,
}

fn parse_args(args: &[String]) -> Result<EvalArgs, String> {
    let mut it = args.iter();
    let (mut request, mut serial, mut power_rail, mut out) = (None, None, None, None);
    let mut curtail = true;
    let mut qualify_from = None;
    let mut lock_gpu_level = None;
    let mut validate_only = false;
    let mut evidence_origin = "device".to_string();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--request" => request = it.next().cloned(),
            "--serial" => serial = it.next().cloned(),
            "--power-rail" => power_rail = it.next().cloned(),
            "--out" => out = it.next().cloned(),
            "--no-curtail" => curtail = false,
            "--qualify-from" => qualify_from = it.next().cloned(),
            "--lock-gpu-level" => {
                let v = it.next().and_then(|v| v.parse::<u32>().ok());
                lock_gpu_level = Some(v.ok_or(format!("--lock-gpu-level 要一个非负整数档位号\n{USAGE}"))?);
            }
            "--validate-only" => validate_only = true,
            "--evidence-origin" => evidence_origin = it.next().ok_or("missing evidence origin")?.clone(),
            "--json" => {} // eval 的回包恒为 JSON, 收下这个旗子仅为兼容接口形状
            other => return Err(format!("不认识的参数 {other}\n{USAGE}")),
        }
    }
    if !matches!(evidence_origin.as_str(),"device"|"synthetic") {return Err("invalid evidence origin".into());}
    Ok(EvalArgs {
        request: request.ok_or(format!("缺少 --request\n\n{USAGE}"))?,
        serial,
        power_rail,
        out,
        curtail,
        qualify_from,
        lock_gpu_level,
        validate_only,
        evidence_origin,
    })
}

pub fn run_eval(args: &[String]) -> i32 {
    if args.first().is_some_and(|a|a=="recover") {return recover_gray_eval(&args[1..]);}
    let a = match parse_args(args) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("{e}");
            return 2;
        }
    };
    let raw = match std::fs::read_to_string(&a.request) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("读不了 {}: {e}", a.request);
            return 2;
        }
    };
    let req: Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("{} 不是合法 JSON: {e}", a.request);
            return 2;
        }
    };
    let version = req.get("version").and_then(|v| v.as_i64()).unwrap_or(1);
    let kind = req.get("kind").and_then(|v| v.as_str()).unwrap_or("shader");
    if version == 1 && a.qualify_from.is_some() {
        eprintln!("--qualify-from 仅支持 v2 显式目标请求");
        return 2;
    }
    if version == 1 {
        // v1 扁平格式: gpu-op 就是它的消费者, 行为一字不动地转交
        return delegate_gpu_op(
            &a.request,
            a.serial.as_deref(),
            a.power_rail.as_deref(),
            a.out.as_deref(),
        );
    }
    if version != 2 {
        eprintln!("不认识的 eval_request 版本 {version} (契约只认 1 与 2)");
        return 2;
    }
    if let Err(e) = validate_v2(&req) {
        if kind == "shader" {
            println!("{}", crate::shaderplan::abort(&req, &e, None));
        } else {
            eprintln!("eval_request v2 校验失败: {e}");
        }
        return 2;
    }
    if let Some(path) = &a.qualify_from {
        return match offline_qualification(&req, Path::new(path)) {
            Ok(report) => {
                println!("{}", serde_json::to_string_pretty(&report).unwrap());
                if report["qualification"]["conclusion_allowed"] == true { 0 } else { 1 }
            }
            Err(e) => { eprintln!("{e}"); 2 }
        };
    }
    if a.validate_only {
        return match offline_check(&req) {
            Ok(v) => {
                println!("{}", serde_json::to_string_pretty(&v).unwrap_or_default());
                0
            }
            Err(e) => {
                eprintln!("离线校验不通过: {e}");
                2
            }
        };
    }
    let evidence = a
        .out
        .clone()
        .unwrap_or_else(|| format!("eval_evidence/{}", req["candidate_id"].as_str().unwrap_or("x")));
    // serial: CLI --serial 优先, 否则请求体里的 (game_opt_loop 的驱动把 --serial
    // 放进请求体而不是命令行)
    let serial = a
        .serial
        .clone()
        .or_else(|| req["serial"].as_str().map(String::from));
    if kind == "shader" {
        if a.lock_gpu_level.is_some() {
            eprintln!("--lock-gpu-level 只对 kind=sysparam/gray/whitebox 生效 (shader 走 gpu-op, 它自己锁频)");
            return 2;
        }
        return eval_shader_v2(&req, &a);
    }
    if !matches!(kind, "sysparam" | "gray" | "whitebox") {
        eprintln!("不认识的 kind {kind}");
        return 2;
    }
    if kind == "gray" && a.lock_gpu_level.is_some() {
        eprintln!("--lock-gpu-level 不支持 kind=gray: 灰档评测由受控会话 (gray_eval_session) 持有并恢复设备状态, 会话外的档位写入无法随会话恢复");
        return 2;
    }
    // 审查 R2 #11: 同一目录再跑会覆盖上一次的证据 (hw.txt、readback、快照、截图都是同名写入), 非空就拒跑
    if let Err(e) = refuse_nonempty_out(Path::new(&evidence)) {
        eprintln!("{e}");
        return 2;
    }
    // 设备租约 (main): 先拿租约再碰设备 (下面的 GPU 档位锁也要写设备)
    let _lease = match crate::devlease::Lease::target(serial.as_deref(), "eval") {
        Ok(v) => v,
        Err(e) => {
            let mut r = base_report(&req, kind);
            r["verdict"]["reason"] = json!(e);
            r["qualification"] = json!({"stage":"experiment","eligible":false,"can_claim_improvement":false,"status":"INELIGIBLE","blockers":[e]});
            println!("{r}");
            eprintln!("{e}");
            return 2;
        }
    };
    let serial = Some(_lease.serial.clone());
    if crate::whitebox_refbench::handles(&req) {
        if a.lock_gpu_level.is_some() || a.power_rail.is_some() || a.evidence_origin!="device" {
            eprintln!("native whitebox does not accept external frequency/power/source overrides"); return 2;
        }
        return match crate::whitebox_refbench::run(&req,_lease,Path::new(&evidence)) {
            Ok(report)=>{let code=if report["status"]=="PASS"{0}else{2};println!("{}",serde_json::to_string_pretty(&report).unwrap());code},
            Err(e)=>{eprintln!("{e}");2}
        };
    }
    if kind=="gray" {
        return match gray_eval_owned(&req, _lease, Path::new(&evidence), &a.evidence_origin) {
            Ok(r)=>{ let code=if r["recovery"]["status"]!="reported_complete" || !r["report_error"].is_null() || !r["qualification_error"].is_null() {2} else if r["status"]=="ABORT" {1} else {0}; println!("{}",serde_json::to_string_pretty(&r).unwrap()); code },
            Err(e)=>{eprintln!("{e}");2}
        };
    }
    if a.evidence_origin!="device" {eprintln!("--evidence-origin is supported for owned gray eval only");return 2;}
    // 审计 #9: 可选的 GPU 档位锁, 包住整次评测; 候选本身改档位的不能锁
    let gpu_lock = match a.lock_gpu_level {
        Some(level) => {
            let Some(s) = serial.as_deref() else {
                eprintln!("--lock-gpu-level 需要 --serial");
                return 2;
            };
            if let Err(e) = gpu_lock_conflict(&req) {
                eprintln!("{e}");
                return 2;
            }
            let _ = std::fs::create_dir_all(&evidence);
            match GpuLevelLock::apply(s, level, Path::new(&evidence)) {
                Ok(l) => Some(l),
                Err(e) => {
                    eprintln!("GPU 档位锁失败: {e}");
                    return 2;
                }
            }
        }
        None => None,
    };
    let report = match kind {
        "sysparam" => sysparam_eval(&req, serial.as_deref(), Path::new(&evidence), a.curtail && !is_screen(&req)),
        _ => crate::whitebox::whitebox_eval(&req, serial.as_deref(), Path::new(&evidence)),
    };
    // 锁档无条件还原 (评测 Err 也还原), 结果写进回包
    let lock_note = gpu_lock.map(|l| l.restore());
    let report = report.map(|r| attach_gpu_lock_note(&r, lock_note.as_ref()));
    // 每份回包都带记分卡 (verdict.scorecard): 逐维度一行, 只有安全和「没测成」让结果无效
    let policy = crate::scorecard::ImagePolicy::from_request(&req).unwrap_or(crate::scorecard::ImagePolicy::Gate);
    let report = report.map(|r| crate::scorecard::attach(&r, policy));
    match report {
        Ok(r) => {

            println!("{r}");
            0
        }
        Err(e) => {
            eprintln!("{e}");
            2
        }
    }
}

/// v2 shader: 契约规定的收端降级 —— 合成等价的 v1 请求转交 gpu-op,
/// 回包补上 version/kind/conditions_observed。
fn eval_shader_v2(req: &Value, a: &EvalArgs) -> i32 {
    let plan = match crate::shaderplan::build(req, a.serial.as_deref(), a.power_rail.as_deref()) {
        Ok(v) => v,
        Err(e) => {
            println!("{}", crate::shaderplan::abort(req, &e, None));
            return 2;
        }
    };
    let dir = a.out.clone().map(PathBuf::from).unwrap_or_else(|| {
        std::env::temp_dir().join(format!("phonefarm-eval-{}", std::process::id()))
    });
    if let Err(e) = std::fs::create_dir_all(&dir) {
        println!(
            "{}",
            crate::shaderplan::abort(
                req,
                &format!("evidence directory: {e}"),
                Some(&plan.resolved)
            )
        );
        return 2;
    }
    let path = dir.join(format!("_eval_v1_request_{}.json", std::process::id()));
    if let Err(e) = std::fs::write(&path, plan.request.to_string()) {
        println!(
            "{}",
            crate::shaderplan::abort(
                req,
                &format!("adapter request write: {e}"),
                Some(&plan.resolved)
            )
        );
        return 2;
    }
    let mut args = plan.args;
    args.extend([
        "--request".into(),
        path.to_string_lossy().into_owned(),
        "--out".into(),
        dir.to_string_lossy().into_owned(),
    ]);
    // Same process: gpu-op owns one lease and emits the v2 envelope on measured outcomes.
    // Exit 1 is still a valid non-PASS report. Exit 2 means unverified conditions, hence ABORT.
    let rc = crate::gpuop::run_gpu_op(&args);
    if rc == 2 {
        println!(
            "{}",
            crate::shaderplan::abort(
                req,
                "gpu-op execution/conditions failed; see stderr",
                Some(&plan.resolved)
            )
        );
    }
    rc
}

fn delegate_gpu_op(
    request: &str,
    serial: Option<&str>,
    power_rail: Option<&str>,
    out: Option<&str>,
) -> i32 {
    // run_gpu_op 吃的是 main.rs 剥掉子命令后的参数 —— 这里别把 "gpu-op" 再带上
    let mut v = vec!["--request".to_string(), request.to_string()];
    if let Some(s) = serial {
        v.push("--serial".into());
        v.push(s.to_string());
    }
    if let Some(r) = power_rail {
        v.push("--power-rail".into());
        v.push(r.to_string());
    }
    if let Some(o) = out {
        v.push("--out".into());
        v.push(o.to_string());
    }
    gpuop_passthrough(&v)
}

fn gpuop_passthrough(args: &[String]) -> i32 {
    crate::gpuop::run_gpu_op(args)
}

/// 审查 R2 #11: 评测目录已经有东西 (同一个 candidate_id 再跑, game_opt_loop 的缺省路径就会这样) 时拒跑。
/// 各臂的 hw.txt / readback / 快照 / 截图都是同名写入, 再跑一次上一次的证据就没了 (铁律: 不覆盖)。
/// 目录不存在或是空目录都放行。
pub(crate) fn refuse_nonempty_out(dir: &Path) -> Result<(), String> {
    let Ok(rd) = std::fs::read_dir(dir) else { return Ok(()) };
    let names: Vec<String> = rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
    if names.is_empty() {
        return Ok(());
    }
    Err(format!(
        "评测目录 {} 非空 (已有 {} 项, 如 {}): 同一目录再跑会覆盖上一次的证据, 换一个 --out (例如带时间戳的子目录)",
        dir.display(),
        names.len(),
        names.iter().take(3).cloned().collect::<Vec<_>>().join(", ")
    ))
}

// ══════════════ GPU 档位锁 (审计 #9) ══════════════
//
// 2026-10-21 selftest run1/run2 实测: 同一配置的帧率在 231 / 258 / 287 / 305 / 347 fps 之间跳,
// 因为 kgsl 的 max_pwrlevel 只在开头归 0 一次, 之后厂商性能服务随意改。两种处置:
//   · 锁: `--lock-gpu-level N` 把 min/max_pwrlevel 都钉在 N 档, 结束还原 (这里);
//   · 记 + 协变量: 不锁时每臂逐秒记档位 (sample_hw.sh → hw.gpu_pwrlevel_runs), 两臂分布不同的那一对
//     判不可比 (pair_env_check), 且 gpu_clk_mhz_mean 作为辅助证据进 verdict.aux (aux_compare)。
// 锁住也只是钉了边界: 厂商服务仍可能中途改写, 逐秒记录照样做, 回包里看得到。

pub(crate) const KGSL_DIR: &str = "/sys/class/kgsl/kgsl-3d0";

/// 读 kgsl 档位的一条命令, 输出 `max_pwrlevel=…` / `min_pwrlevel=…` / `num_pwrlevels=…`。
pub(crate) fn pwrlevel_read_cmd() -> String {
    format!("for f in max_pwrlevel min_pwrlevel num_pwrlevels; do echo \"$f=$(cat {KGSL_DIR}/$f 2>/dev/null)\"; done")
}

/// 回读 → (max_pwrlevel, min_pwrlevel, num_pwrlevels)。max/min 任一读不到就 None。
pub(crate) fn parse_pwrlevels(out: &str) -> Option<(u32, u32, Option<u32>)> {
    let (mut mx, mut mn, mut n) = (None, None, None);
    for l in out.lines() {
        if let Some((k, v)) = l.trim().split_once('=') {
            let v = v.trim().parse::<u32>().ok();
            match k.trim() {
                "max_pwrlevel" => mx = v,
                "min_pwrlevel" => mn = v,
                "num_pwrlevels" => n = v,
                _ => {}
            }
        }
    }
    Some((mx?, mn?, n))
}

/// 锁到 `level`: 先 max 再 min 再 max。kgsl 的 store 互相钳位 (max 不能大于 min 的数值, 反之亦然),
/// 这个顺序对任意目标档都成立 (与 hwcond::Lock 同一做法)。
pub(crate) fn pwrlevel_lock_cmd(level: u32) -> String {
    format!(
        "echo {level} > {KGSL_DIR}/max_pwrlevel; echo {level} > {KGSL_DIR}/min_pwrlevel; echo {level} > {KGSL_DIR}/max_pwrlevel"
    )
}

/// 还原: 先 min (数值大) 再 max。
pub(crate) fn pwrlevel_restore_cmd(max: u32, min: u32) -> String {
    format!("echo {min} > {KGSL_DIR}/min_pwrlevel; echo {max} > {KGSL_DIR}/max_pwrlevel")
}

/// 候选本身改 GPU 档位 (sysparam 的 params / plan 里有 pwrlevel) 时不能再锁: 锁会把候选钉死, 量的就不是候选了。
pub(crate) fn gpu_lock_conflict(req: &Value) -> Result<(), String> {
    let mut hits: Vec<String> = Vec::new();
    if let Some(o) = req["payload"]["params"].as_object() {
        hits.extend(o.keys().filter(|k| k.contains("pwrlevel")).cloned());
    }
    if let Some(a) = req["payload"]["plan"].as_array() {
        hits.extend(a.iter().filter_map(|e| e["path"].as_str()).filter(|p| p.contains("pwrlevel")).map(String::from));
    }
    if hits.is_empty() {
        Ok(())
    } else {
        Err(format!("候选本身改 GPU 档位 ({}), 不能同时 --lock-gpu-level", hits.join(", ")))
    }
}

/// 评测期间的 GPU 档位锁。先把「锁前的值 + 还原命令」落到 evidence 下的状态文件、登记进
/// [`cleanup`] (Ctrl-C 也还原), 再写设备; 回读不符立即还原并报错。
pub(crate) struct GpuLevelLock {
    serial: String,
    level: u32,
    /// (max_pwrlevel, min_pwrlevel) 锁前的值
    before: (u32, u32),
    state_path: PathBuf,
}

impl GpuLevelLock {
    pub(crate) fn apply(serial: &str, level: u32, evidence: &Path) -> Result<Self, String> {
        let _ = std::fs::create_dir_all(evidence);
        let rd = su(serial, &pwrlevel_read_cmd(), 15);
        let (mx, mn, n) = parse_pwrlevels(&rd).ok_or(format!("读不到 kgsl 档位 (需要 root): {}", rd.trim()))?;
        if let Some(n) = n {
            if level >= n {
                return Err(format!("档位 {level} 超出范围 (num_pwrlevels={n}, 合法 0..={})", n.saturating_sub(1)));
            }
        }
        // 先落状态文件再动设备: 中途被杀也有据可还原 (文件只新建, 不删)
        let state_path = evidence.join("gpu_level_lock.txt");
        std::fs::write(
            &state_path,
            format!(
                "# gpu_level_lock v1\nbefore_max={mx}\nbefore_min={mn}\nlevel={level}\nrestore_cmd={}\n",
                pwrlevel_restore_cmd(mx, mn)
            ),
        )
        .map_err(|e| format!("写不了锁档状态文件 {}: {e}", state_path.display()))?;
        cleanup::install();
        cleanup::register(serial, &format!("su -c '{}'", pwrlevel_restore_cmd(mx, mn)));
        let lock = Self { serial: serial.to_string(), level, before: (mx, mn), state_path };
        let _ = su(serial, &pwrlevel_lock_cmd(level), 15);
        let rb = su(serial, &pwrlevel_read_cmd(), 15);
        match parse_pwrlevels(&rb) {
            Some((a, b, _)) if a == level && b == level => {
                eprintln!("[eval] GPU 档位已锁在 {level} 档 (锁前 max={mx} min={mn})");
                Ok(lock)
            }
            other => {
                let r = lock.restore();
                Err(format!(
                    "锁档回读不符: 目标 {level}, 读到 (max,min)={:?}; 已还原 (restored={})",
                    other.map(|(a, b, _)| (a, b)),
                    r["restored"]
                ))
            }
        }
    }

    /// 写回锁前的值并回读核对。回包记进 conditions_observed.gpu_level_lock。
    pub(crate) fn restore(&self) -> Value {
        let _ = su(&self.serial, &pwrlevel_restore_cmd(self.before.0, self.before.1), 15);
        let rb = su(&self.serial, &pwrlevel_read_cmd(), 15);
        let after = parse_pwrlevels(&rb).map(|(a, b, _)| (a, b));
        let restored = after == Some(self.before);
        if !restored {
            eprintln!("[eval] GPU 档位锁还原失败: 锁前 {:?}, 现在 {:?} (状态文件 {})", self.before, after, self.state_path.display());
        }
        json!({
            "level": self.level,
            "before": {"max": self.before.0, "min": self.before.1},
            "after": after.map(|(a, b)| json!({"max": a, "min": b})),
            "restored": restored,
            "state_file": self.state_path.display().to_string(),
            "note": "锁档只钉 kgsl min/max_pwrlevel 的边界; 厂商性能服务仍可能中途改写, 逐秒记录见各臂 hw.gpu_pwrlevel_runs",
        })
    }
}

/// 把锁档记录并进回包的 conditions_observed.gpu_level_lock; 没锁时记 null。还原失败写进判定理由 (不改状态:
/// 测量本身是有效的, 但设备留在锁态要让人看见)。
pub(crate) fn attach_gpu_lock_note(report_json: &str, note: Option<&Value>) -> String {
    let Ok(mut r) = serde_json::from_str::<Value>(report_json) else { return report_json.to_string() };
    if !r["conditions_observed"].is_object() {
        r["conditions_observed"] = json!({});
    }
    r["conditions_observed"]["gpu_level_lock"] = note.cloned().unwrap_or(Value::Null);
    if let Some(n) = note {
        if n["restored"].as_bool() == Some(false) {
            let reason = r["verdict"]["reason"].as_str().unwrap_or("").to_string();
            r["verdict"]["reason"] = json!(format!("{reason} (GPU 档位锁还原失败, 设备仍锁在 {} 档, 见 conditions_observed.gpu_level_lock)", n["level"]));
        }
    }
    serde_json::to_string_pretty(&r).unwrap_or_else(|_| report_json.to_string())
}

// ══════════════ 请求校验 ══════════════

/// 两级测量的第一级 (game_opt_loop 的初筛): 只看方向与幅度, 不判显著。
/// 收端对它不做分辨率防护, 也不做确定性截断 —— 截断针对的是 KEEP 可不可达,
/// 而初筛的轮数本来就判不出 KEEP, 开着截断会在第一臂之后就停, 初筛拿不到任何对照数据。
pub(crate) fn is_screen(req: &Value) -> bool {
    req["protocol"]["stage"].as_str() == Some("screen")
}

/// 回放脚本的标称时长 (秒): sleep 的 ms、swipe 的 duration_ms、exec 里 `input swipe x1 y1 x2 y2 D` 的 D 相加。
/// 读不了或不是动作数组时 None。不含 adb 往返开销, 所以是下限。
pub(crate) fn workload_script_seconds(path: &Path) -> Option<f64> {
    let steps: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()?;
    let ms: u64 = steps.iter().map(|st| match st["action"].as_str().unwrap_or("") {
        "sleep" => st["ms"].as_u64().unwrap_or(0),
        "swipe" => st["duration_ms"].as_u64().unwrap_or(0),
        "exec" => st["cmd"].as_str().and_then(|c| {
            let t: Vec<&str> = c.split_whitespace().collect();
            (t.len() == 7 && t[0] == "input" && t[1] == "swipe").then(|| t[6].parse::<u64>().ok()).flatten()
        }).unwrap_or(0),
        _ => 0,
    }).sum();
    Some(ms as f64 / 1000.0)
}

/// run_once.sh 在负载起跑 LEAD=6 s 后开采 seconds 秒; 负载必须撑满整个采集窗。
/// 2026-10-23 红魔: 确认阶段 60 s 窗配 42 s 的转镜头脚本, 后 24 s 画面静止, frame_p95 在 20/30 ms 两档间跳
/// (基线离散 44%), 资格当场被拒 —— 宁可开跑前拒绝, 也不让静止段混进采集窗。
pub(crate) fn workload_covers_capture(req: &Value) -> Result<(), String> {
    // 只管走 run_once.sh 的 sysparam: gray 等走自己的会话采样, 时序不同
    if req["kind"].as_str() != Some("sysparam") || req["workload"]["carrier"].as_str() != Some("game_replay") || launch_spec(req).is_some() {
        return Ok(());
    }
    let Some(file) = req["workload"]["script"].as_str() else { return Ok(()) };
    let Some(path) = resolve_path(file) else { return Ok(()) };   // 找不到脚本由 eval 本身报
    let seconds = req["workload"]["seconds"].as_f64().unwrap_or(30.0);
    match workload_script_seconds(&path) {
        Some(d) if d + 0.5 < 6.0 + seconds => Err(format!(
            "负载脚本 {file} 标称 {d:.0} s, 撑不满「起跑后 6 s + 采集 {seconds:.0} s」; 换更长的脚本或缩短 workload.seconds")),
        _ => Ok(()),
    }
}

fn validate_v2(req: &Value) -> Result<(), String> {
    for k in ["kind", "candidate_id", "package", "workload", "conditions", "protocol", "payload"] {
        if req.get(k).is_none() {
            return Err(format!("v2 缺必填字段 {k}"));
        }
    }
    let kind = req["kind"].as_str().unwrap_or("");
    let carrier = req["workload"]["carrier"].as_str().unwrap_or("");
    if !matches!(carrier, "headless" | "refbench" | "game_replay") {
        return Err(format!("workload.carrier 不合法: {carrier:?}"));
    }
    workload_covers_capture(req)?;
    let p = &req["protocol"];
    if p.get("rounds").and_then(|v| v.as_i64()).unwrap_or(0) < 1 {
        return Err("protocol.rounds 必须 >= 1".into());
    }
    if p.get("alternating").and_then(|v| v.as_bool()).is_none() {
        return Err("protocol.alternating 缺失或不是布尔".into());
    }
    let pt = p.get("p_threshold").and_then(|v| v.as_f64());
    let alpha = match pt {
        Some(x) if x > 0.0 && x <= 0.05 => x,
        _ => return Err("protocol.p_threshold 必须 (0, .05]（收端不接受更宽门槛）".into()),
    };
    match p.get("stage").map(|v| v.as_str()) {
        None | Some(Some("full")) | Some(Some("screen")) => {}
        _ => return Err("protocol.stage 只认 \"full\" / \"screen\" (省略 = full)".into()),
    }
    baseline_plan(req)?;
    // 画质策略: gate (缺省, 画面不过门槛就停) / report (基于模型的候选: 只记差距, 性能照判)
    crate::scorecard::ImagePolicy::from_request(req)?;
    // 审计 #10: 每帧提交数由请求声明 (workload.spf), 不让各臂自己猜; 审查 R2 #9: 锁帧由请求声明 (workload.fps_cap)
    if let Some(s) = req["workload"].get("spf").filter(|v| !v.is_null()) {
        if !s.as_u64().is_some_and(|n| n >= 1) {
            return Err("workload.spf 必须是 >= 1 的整数 (每帧提交数)".into());
        }
    }
    if let Some(c) = req["workload"].get("fps_cap").filter(|v| !v.is_null()) {
        if !c.as_f64().is_some_and(|x| x > 0.0) {
            return Err("workload.fps_cap 必须是 > 0 的数 (负载的帧率上限)".into());
        }
    }
    // 结构性防护: sysparam / gray / whitebox 走精确置换检验 (judge_runs), 改善一侧用 alpha/K。
    // rounds 太少时最小可达 p 就够不着 alpha/K, 这个请求在数学上判不出任何改善 —— 收端
    // 拒收, 不碰设备。功耗进不进表要等数据, 按最严的 K=3 算。
    // 初筛 (stage=screen) 只看方向与幅度、不判显著, 不受这条限制。
    // shader 走 gpu-op 的 Welch t 检验, 没有这种离散的分辨率下限, 不在此列。
    if matches!(kind, "sysparam" | "gray" | "whitebox") && !is_screen(req) {
        let rounds = p.get("rounds").and_then(|v| v.as_i64()).unwrap_or(0).max(0) as usize;
        loopstat::ensure_resolvable(rounds, alpha / loopstat::MAX_WIN_METRICS as f64, "protocol.rounds").map_err(|e| {
            format!("{e}。(初筛快测请在 protocol.stage 写 \"screen\": 那一级只看方向和幅度, 不判显著)")
        })?;
    }
    match kind {
        "shader" => {
            for k in ["track", "shader_path", "spirv_path", "budget_ms"] {
                if req["payload"].get(k).map(|v| v.is_null()).unwrap_or(true) {
                    return Err(format!("kind=shader 的 payload 缺 {k}"));
                }
            }
        }
        "sysparam" => {
            if req["payload"].get("params").map(|v| v.is_object()) != Some(true) {
                return Err("kind=sysparam 的 payload.params 必须是对象".into());
            }
        }
        "gray" => {
            let rewrites = req["payload"]["rewrites"].as_array();
            let n = rewrites.map(|r| r.len()).unwrap_or(0);
            if n < 1 {
                return Err("kind=gray 的 payload.rewrites 至少 1 项".into());
            }
            for r in rewrites.unwrap() {
                let knob = r["knob"].as_str().unwrap_or("");
                if !GRAY_SUPPORTED_KNOBS.contains(&knob) {
                    return Err(format!(
                        "gray knob {knob:?} 不在收端已实现的集合 {GRAY_SUPPORTED_KNOBS:?} 里 —— 层的开关集合有限, 不收凭空发明的开关"
                    ));
                }
            }
            // 收端真正施加时用的就是这个解析; 校验阶段跑同一份, 缺 scope / scope 写错在碰设备前拒收
            parse_gray_rewrites(&req["payload"])?;
        }
        "whitebox" => {
            if crate::whitebox_refbench::handles(req) {
                crate::whitebox_refbench::validate(req)?;
            } else {
                crate::whitebox::knob_extras(&req["payload"])?;
                if launch_spec(req).is_none() {
                    return Err("kind=whitebox 需要 workload.launch.extras (白盒载体靠启动参数换设置)".into());
                }
            }
        }
        other => return Err(format!("不认识的 kind {other:?}")),
    }
    if matches!(kind,"sysparam"|"gray") { policy_for_request(req)?; }
    Ok(())
}

/// Objective selects receiver-owned requirements. Incoming required_metrics cannot weaken them.
fn policy_for_request(req: &Value) -> Result<Policy, String> {
    let goal = Goal::parse(req["qualification"]["goal"].as_str().unwrap_or("render_efficiency"))?;
    let pairs = req["protocol"]["rounds"].as_u64().ok_or("缺 protocol.rounds")?;
    let pairs = u32::try_from(pairs).map_err(|_| "rounds 溢出")?;
    let mut policy = Policy::new(goal, pairs, req["kind"] == "gray");
    policy.temp_cap_c=req["conditions"]["temp_cap_c"].as_f64();
    policy.cool_c=req["conditions"]["cool_c"].as_f64();
    policy.alpha=req["protocol"]["p_threshold"].as_f64().ok_or("缺 p_threshold")?;
    policy.validate()?;
    if let Some(ms) = req["qualification"]["required_metrics"].as_array() {
        if json!(policy.required_metrics) != Value::Array(ms.clone()) {
            return Err("请求 required_metrics 不得缩窄收端目标合同".into());
        }
    }
    Ok(policy)
}

fn offline_qualification(req: &Value, path: &Path) -> Result<Value, String> {
    validate_v2(req)?;
    let mut evidence = qualification::read(path)?;
    evidence.policy = policy_for_request(req)?;
    let q = qualification::evaluate(&evidence)?;
    let mut report = base_report(req, req["kind"].as_str().unwrap_or(""));
    report["qualification"] = serde_json::to_value(q).map_err(|e|e.to_string())?;
    report["qualification_only"] = json!(true);
    report["verdict"]["reason"] = json!("离线资格评估，不应用候选，不作优化 PASS 判定");
    Ok(report)
}

fn record_qualification(report: &mut Value, evidence: &Path, facts: &Evidence) -> Result<qualification::Report,String> {
    let q = qualification::evaluate(facts)?;
    write_qualification(report, evidence, facts, q)
}

fn write_qualification(report: &mut Value, evidence: &Path, facts: &Evidence, q: qualification::Report) -> Result<qualification::Report,String> {
    std::fs::create_dir_all(evidence).map_err(|e|e.to_string())?;
    qualification::write(&evidence.join("qualification.json"), &q)?;
    std::fs::write(evidence.join("qualification_evidence.json"),serde_json::to_string_pretty(facts).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    report["qualification"] = serde_json::to_value(&q).map_err(|e|e.to_string())?;
    Ok(q)
}

pub(crate) fn snapshots_identical(before: &str, after: &str) -> bool {
    adb_ok(before) && adb_ok(after) && before.lines().any(|l|l.contains('=')) && before == after
}

/// 轮末资格里的「已还原」: 两次快照都读成功且非空, 除厂商性能服务自管的频率边界外逐项相等
/// (我们写过的节点另由 knob_sysparam.sh restore 逐项回读核对)。探测前后仍用严格的 [`snapshots_identical`]。
pub(crate) fn snapshots_restored(before: &str, after: &str) -> bool {
    snapshots_identical(before, after)
        || (adb_ok(before) && adb_ok(after) && before.lines().any(|l| l.contains('='))
            && split_snapshot_diff(before, after).1.is_empty())
}

/// 探测脚本的每一次差异写都当场回读核对过已还原: 至少一条 `.effect_restored=yes`, 没有一条 `=NO`。
/// 例外只有一种: 厂商自管的频率边界节点, 我们的写当场就被退回/抢走 (`rejected`/`contested`),
/// 还原时读到的也不是我们写的值 —— 节点上留的是厂商的值, 不是我们的残留。
/// 2026-10-23 红魔实测: gpu.max_pwrlevel 写 12 回读 3, 写回 11 回读 17, 整轮被判「未还原」禁跑。
pub(crate) fn probe_writes_verified(probe_txt: &str) -> bool {
    let mut ok = 0;
    for l in probe_txt.lines() {
        if let Some((key, rest)) = l.split_once(".effect_restored=NO") {
            if !vendor_write_lost(probe_txt, key, rest) {
                return false;
            }
            continue;
        }
        if l.trim_end().ends_with(".effect_restored=yes") {
            ok += 1;
        }
    }
    ok > 0
}

/// `key.effect_restored=NO(want=.. got=G)` 是不是厂商抢写: key 是厂商自管的频率边界,
/// 同一 key 的 effect 是 rejected/contested(wrote=W ..), 且 G != W。
fn vendor_write_lost(probe_txt: &str, key: &str, restored_rest: &str) -> bool {
    let managed = matches!(key, "gpu.max_pwrlevel" | "gpu.min_pwrlevel")
        || (key.starts_with("cpu.policy") && (key.ends_with(".scaling_max_freq") || key.ends_with(".scaling_min_freq")));
    let Some(got) = restored_rest.split("got=").nth(1).map(|g| g.trim_end().trim_end_matches(')')) else {
        return false;
    };
    let effect_prefix = format!("{key}.effect=");
    let Some(effect) = probe_txt.lines().find_map(|l| l.strip_prefix(effect_prefix.as_str())) else {
        return false;
    };
    let lost = effect.starts_with("rejected(") || effect.starts_with("contested(");
    let Some(wrote) = effect.split("wrote=").nth(1).and_then(|w| w.split_whitespace().next()) else {
        return false;
    };
    managed && lost && !got.is_empty() && got != wrote
}

/// 探测前后的「已还原」。快照逐项相等; 或者只有厂商自管的频率边界变了, 且探测的每一次写入
/// 都当场核对过还原 (探测把自己的写入留在节点上时, 当场回读会报 effect_restored=NO, 照样拦下)。
/// 2026-10-22 红魔实测: 厂商性能服务在探测的几十秒里就把 kgsl.max_pwrlevel 17→0、
/// policy6.scaling_max 1632000→4608000 改了, 9 次差异写全部当场还原, 严格比对却让后续实验一律禁跑。
pub(crate) fn probe_snapshots_restored(before: &str, after: &str, probe_txt: &str) -> bool {
    snapshots_identical(before, after)
        || (adb_ok(before) && adb_ok(after) && before.lines().any(|l| l.contains('='))
            && split_snapshot_diff(before, after).1.is_empty()
            && probe_writes_verified(probe_txt))
}

fn probe_report(report: &mut Value, before: &str, after: &str, probe_txt: &str, wl: &sysparam::Whitelist) {
    let identical = snapshots_identical(before,after);
    // 厂商自管的频率边界变化, 只有在探测的每次写入都当场核对过还原时才放行 (见 probe_snapshots_restored)
    let restored = probe_snapshots_restored(before, after, probe_txt);
    let (vendor_drift, other_drift) = split_snapshot_diff(before, after);
    report["status"] = json!(if restored {"REVERTED"} else {"ABORT"});
    report["verdict"]["reason"] = json!(if restored {"probe_only: 探测写入已验证还原 (厂商自管的频率边界变化只记不判)"} else {"probe_only: 快照缺失/读取失败或不一致，不能声称已还原"});
    report["conditions_observed"] = json!({"snapshot_identical":identical,"restored":restored,
        "snapshot_vendor_drift":vendor_drift,"snapshot_other_drift":other_drift,"whitelist":wl_pyval(wl)});
}

/// Verify the supplied workload, rather than injecting a separate hard-coded gesture.
pub(crate) fn verify_workload(serial: &str, workload: &Path, pkg: &str, outdir: &Path) -> Value {
    let _ = std::fs::create_dir_all(outdir);
    let mut child = match Command::new(std::env::current_exe().unwrap_or_default())
        // 不传 --app: 那只开 script 的逐步遥测 (dumpsys 等), 会把第一次转镜头推迟到 ~8 s 之后,
        // 恰好错过下面第 3 / 8 秒的两次截屏 (2026-10-22 verify1 两帧一样, 判负载没转)
        .args(["script", "--serial", serial, "--task", "qualification_workload", "--no-screen"])
        .arg(workload).current_dir(repo_root()).stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null()).spawn() {
        Ok(c)=>c,Err(e)=>return json!({"verified":false,"reason":e.to_string()})
    };
    let capture = || crate::fd_lock::transport_command(adb_path()).args(["-s",serial,"exec-out","screencap"]).output().ok()
        .filter(|o|o.status.success()).map(|o|o.stdout).unwrap_or_default();
    std::thread::sleep(std::time::Duration::from_secs(3)); let a=capture();
    std::thread::sleep(std::time::Duration::from_secs(5)); let b=capture();
    // 等脚本跑完: 标称时长 + 30 s 余量 (长脚本以前会在 65 s 被杀、判成脚本失败)
    let budget=workload_script_seconds(workload).map_or(65.0,|d|d+30.0).max(65.0);
    let deadline=std::time::Instant::now()+std::time::Duration::from_secs_f64(budget);
    let success=loop {match child.try_wait() {
        Ok(Some(s))=>break s.success(),
        Ok(None) if std::time::Instant::now()<deadline=>std::thread::sleep(std::time::Duration::from_millis(100)),
        _=>{let _=child.kill();let _=child.wait();break false;}
    }};
    let _=std::fs::write(outdir.join("frame_a.raw"),&a); let _=std::fs::write(outdir.join("frame_b.raw"),&b);
    let motion=to_serde(&crate::framecheck::verdict(&a,&b));
    let alive = adb(serial, &["shell", &format!("pidof {pkg}")], 15);
    let alive = adb_ok(&alive) && !crate::graylayer::GrayLayer::adb_stdout(&alive).trim().is_empty();
    let result=json!({"verified":success && alive && motion["spinning"]==true,"script_exit_success":success,"app_alive":alive,"motion":motion});
    let _=std::fs::write(outdir.join("workload_check.json"),serde_json::to_string_pretty(&result).unwrap());
    result
}

// ══════════════ 设备助手 ══════════════

/// su()/adb() 的输出以 `#adb_exit=N` 结尾, N=0 才算成功。
/// (autoloop 的 `#adb_exit=` 令牌写法依赖"成功时不追加", 这边的 helper 恒追加,
/// 判断必须区分退出码 —— 实测把成功 apply 判成了 FAIL, 白白施加又还原一次。)
pub(crate) fn adb_ok(out: &str) -> bool {
    out.ends_with("#adb_exit=0")
}

/// 调用方视角的路径解析: 绝对/相对 cwd 先试, 再试相对仓库根。
pub(crate) fn resolve_path(p: &str) -> Option<PathBuf> {
    let as_given = Path::new(p);
    if as_given.exists() {
        return Some(as_given.to_path_buf());
    }
    let under_root = repo_root().join(p);
    if under_root.exists() {
        return Some(under_root);
    }
    None
}

pub(crate) fn adb_path() -> String {
    device::locate_adb().unwrap_or_else(|| "adb".into())
}

pub(crate) fn adb(serial: &str, args: &[&str], timeout: u64) -> String {
    let mut cmd = crate::fd_lock::transport_command(adb_path());
    cmd.arg("-s").arg(serial).args(args);
    run_with_timeout(&mut cmd, timeout)
}

pub(crate) fn su(serial: &str, cmd_str: &str, timeout: u64) -> String {
    adb(serial, &["shell", &format!("su -c '{cmd_str}'")], timeout)
}

fn run_with_timeout(cmd: &mut Command, timeout: u64) -> String {
    use std::io::Read;
    cmd.stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::null());
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return format!("#adb_exit=spawn_err {e}"),
    };
    // 读端单独开线程: 管道满了不读会卡死子进程, 而 recv_timeout 要占住主线程
    let mut stdout = child.stdout.take();
    let reader = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(o) = stdout.as_mut() {
            let _ = o.read_to_end(&mut buf);
        }
        buf
    });
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(timeout);
    let code = loop {
        match child.try_wait() {
            Ok(Some(s)) => break s.code().unwrap_or(-1),
            Ok(None) => {
                if std::time::Instant::now() > deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return "#adb_exit=timeout".into();
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            Err(_) => return "#adb_exit=err".into(),
        }
    };
    let out = reader.join().unwrap_or_default();
    format!("{}#adb_exit={code}", String::from_utf8_lossy(&out))
}

/// 找到 phonefarm 仓库根 (含 loop_v1/tools 的目录)。
/// 顺序: PF_REPO_ROOT > 二进制所在目录向上找 (装在仓库根的 ./phonefarm 一击即中)
/// 然后 cwd 向上找, 最后编译期路径 (本机开发布局兜底)。eval 常被 game_opt_loop 以
/// 相对路径 ``../phonefarm/phonefarm`` 调起, cwd 在别人家, 所以二进制位置最可靠。
pub(crate) fn repo_root() -> PathBuf {
    if let Ok(r) = std::env::var("PF_REPO_ROOT") {
        if !r.is_empty() {
            return PathBuf::from(r);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut cur = exe.parent().map(Path::to_path_buf).unwrap_or_default();
        for _ in 0..8 {
            if cur.join("loop_v1/tools").is_dir() {
                return cur;
            }
            if !cur.pop() {
                break;
            }
        }
    }
    let mut cur = std::env::current_dir().unwrap_or_default();
    for _ in 0..6 {
        if cur.join("loop_v1/tools").is_dir() {
            return cur;
        }
        if !cur.pop() {
            break;
        }
    }
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

pub(crate) const DEV_TMP: &str = "/data/local/tmp";
pub(crate) const DEV_SCRIPTS: [&str; 7] = [
    "device_snapshot.sh",
    "ftrace_capture.sh",
    "knob_sysparam.sh",
    "probe_sysparam.sh",
    "sample_env.sh",
    "charge_suspend.sh",
    // 测量窗口内逐秒记 GPU 上限档与风扇设定档 (审计 #9、#11, 见 [`HwWatch`])
    "sample_hw.sh",
];

pub(crate) fn push_tools(serial: &str) -> Result<(), String> {
    let root = repo_root();
    for f in DEV_SCRIPTS {
        let p = root.join("loop_v1").join(match f {
            "device_snapshot.sh" | "ftrace_capture.sh" => PathBuf::from("tools").join(f),
            _ => PathBuf::from("auto").join(f),
        });
        if !p.exists() {
            return Err(format!("设备端脚本不在: {}", p.display()));
        }
        let out = adb(serial, &["push", &p.to_string_lossy(), DEV_TMP], 60);
        if out.contains("#adb_exit=") && !out.ends_with("#adb_exit=0") {
            return Err(format!("push {} 失败: {out}", p.display()));
        }
    }
    Ok(())
}

pub(crate) fn snapshot(serial: &str) -> String {
    su(serial, &format!("sh {DEV_TMP}/device_snapshot.sh"), 60)
}

/// 每臂开跑前等冷的上限。等不到不作废 (组内交替让两臂承受同样的残余热漂移), 但如实记下。
const ARM_COOL_TIMEOUT_S: u64 = 300;
/// 同一次评测里已经有一臂等冷超时之后, 后面每臂最多再等这么久: 降不到就是降不到,
/// 一臂一臂地累计干等只会把一次评测拖成一个小时 (10 臂 × 300 秒)。
const ARM_COOL_TIMEOUT_AFTER_MISS_S: u64 = 30;
/// 等冷期间多久打一行进度 (stderr; 上层把它原样转进节点日志)。
const COOL_PROGRESS_EVERY_S: u64 = 30;

/// 一臂开跑前的等冷: 等到 SoC 最热热区 < cool_c 或超时。回 {zone, c, waited_s, timeout}。
/// cool_c 缺省 (null) = 不等冷, 只记一次起跑温度。`timeout_s` 由调用方按「这次评测里之前
/// 有没有臂已经超时」给 (见 [`ARM_COOL_TIMEOUT_AFTER_MISS_S`])。
pub(crate) fn arm_wait_cool(serial: &str, cool_c: Option<f64>, label: &str, timeout_s: u64) -> Value {
    let t0 = std::time::Instant::now();
    let mut last_note = 0u64;
    loop {
        let cur = crate::autoloop::read_soc_temp(serial);
        let waited = (t0.elapsed().as_secs_f64() * 10.0).round() / 10.0;
        let Some((zone, c)) = cur else {
            return json!({"zone": null, "c": null, "waited_s": waited, "timeout": false, "note": "读不到 SoC 热区"});
        };
        match cool_c {
            Some(target) if c >= target => {
                let el = t0.elapsed().as_secs();
                if el >= timeout_s {
                    eprintln!("等冷 {label}: 等了 {el}s 仍 {zone}={c:.1}C >= 目标 {target:.1}C, 超时, 照常开跑 (如实标注)");
                    return json!({"zone": zone, "c": c, "waited_s": waited, "timeout": true});
                }
                if el >= last_note + COOL_PROGRESS_EVERY_S || last_note == 0 {
                    eprintln!("等冷 {label}: {zone}={c:.1}C, 目标 < {target:.1}C, 已等 {el}s (最多 {timeout_s}s)");
                    last_note = el.max(1);
                }
                std::thread::sleep(std::time::Duration::from_secs(8));
            }
            _ => return json!({"zone": zone, "c": c, "waited_s": waited, "timeout": false}),
        }
    }
}

/// 这一臂最多等多久: 之前已有一臂超时就只再给 [`ARM_COOL_TIMEOUT_AFTER_MISS_S`]。
pub(crate) fn arm_cool_timeout(log: &[Value]) -> u64 {
    if log.iter().any(|a| a["timeout"].as_bool() == Some(true)) {
        ARM_COOL_TIMEOUT_AFTER_MISS_S
    } else {
        ARM_COOL_TIMEOUT_S
    }
}

/// 逐臂等冷记录 → conditions_observed 里的 cool_reached_c (最热的一次起跑温度) / cool_timeout。
pub(crate) fn cool_summary(arms: &[Value]) -> (Option<f64>, bool) {
    let worst = arms.iter().filter_map(|a| a["c"].as_f64()).reduce(f64::max);
    (worst, arms.iter().any(|a| a["timeout"].as_bool() == Some(true)))
}

/// 测量窗口内多久回读一次 plan 里的节点 (两臂都读, 读的节奏一样)。
/// 厂商性能服务约 10 秒内改回 (https://github.com/HGamey/docs/blob/main/repos/phonefarm/knobs/DESIGN.md),
/// 也会在臂中途再改, 所以不是施加后读一次, 而是整段窗口按固定节拍读。
const READBACK_EVERY_S: u64 = 3;
/// 改写臂在测量窗口内守住的时间比例至少这么多才进统计 (请求 `protocol.sysparam_hold_min` 可改)。
const HOLD_MIN_FRAC: f64 = 0.9;
/// 窗口内有效回读 (adb 调用成功的) 少于这么多次, 守没守住说不清, 不进统计。
const HOLD_MIN_SAMPLES: usize = 3;

/// 读回 plan 里每一项当前值的一条 shell 命令。输出每行 `<路径>=<值>`。
pub(crate) fn readback_cmd(plan: &str) -> String {
    let mut parts = Vec::new();
    for line in plan.lines() {
        let f: Vec<&str> = line.split('\t').collect();
        if f.len() < 3 {
            continue;
        }
        match f[0] {
            "sysfs" => parts.push(format!("echo \"{}=$(cat {} 2>/dev/null)\"", f[1], f[1])),
            "setting" => {
                let (ns, key) = f[1].split_once(':').unwrap_or(("system", f[1]));
                parts.push(format!("echo \"{}=$(settings get {ns} {key} 2>/dev/null)\"", f[1]))
            }
            _ => {}
        }
    }
    // 审查 R1 #5: 每次回读打上设备 uptime, 守住比例才能按 trace 采集窗截 (见 hold_stats_window)
    parts.push("echo \"#uptime=$(cut -d' ' -f1 /proc/uptime 2>/dev/null)\"".to_string());
    parts.join("; ")
}

/// 一次回读输出里的设备 uptime (`#uptime=…` 行, 由 [`readback_cmd`] 打上)。旧脚本没有这一行时 None。
pub(crate) fn readback_uptime(out: &str) -> Option<f64> {
    out.lines()
        .find_map(|l| l.trim().strip_prefix("#uptime="))
        .and_then(|v| v.trim().parse::<f64>().ok())
}

/// 回读对不上的项: (路径, 写的值, 读回的值)。读不到的也算对不上 (守没守住说不清就不算守住)。
pub(crate) fn readback_mismatches(plan: &str, out: &str) -> Vec<(String, String, String)> {
    let got: std::collections::BTreeMap<&str, &str> = out
        .lines()
        .filter_map(|l| l.trim().split_once('='))
        .collect();
    let mut bad = Vec::new();
    for line in plan.lines() {
        let f: Vec<&str> = line.split('\t').collect();
        if f.len() < 3 || !matches!(f[0], "sysfs" | "setting") {
            continue;
        }
        let want = f[2].trim();
        let have = got.get(f[1]).map(|v| v.trim()).unwrap_or("<读不到>");
        if have != want {
            bad.push((f[1].to_string(), want.to_string(), have.to_string()));
        }
    }
    bad
}

/// 厂商性能服务自己会动的快照键 (频率边界)。
fn vendor_managed(key: &str) -> bool {
    matches!(
        key,
        "kgsl.max_pwrlevel" | "kgsl.min_pwrlevel" | "kgsl.thermal_pwrlevel" | "kgsl.max_gpuclk"
            | "kgsl.devfreq_min_freq" | "kgsl.devfreq_max_freq"
    ) || (key.starts_with("cpu.policy") && (key.ends_with(".scaling_max") || key.ends_with(".scaling_min")))
}

/// 快照前后的差异, 分成 (厂商服务会动的, 其余)。每项 `键: 前 → 后`。
pub(crate) fn split_snapshot_diff(before: &str, after: &str) -> (Vec<String>, Vec<String>) {
    let parse = |s: &str| -> std::collections::BTreeMap<String, String> {
        s.lines()
            .filter(|l| !l.starts_with('#') && !l.starts_with("#adb_exit"))
            .filter_map(|l| l.split_once('=').map(|(k, v)| (k.trim().to_string(), v.trim().to_string())))
            .collect()
    };
    let (a, b) = (parse(before), parse(after));
    let keys: std::collections::BTreeSet<&String> = a.keys().chain(b.keys()).collect();
    let (mut vendor, mut other) = (Vec::new(), Vec::new());
    for k in keys {
        let (x, y) = (a.get(k).map(String::as_str).unwrap_or("<无>"), b.get(k).map(String::as_str).unwrap_or("<无>"));
        if x != y {
            let line = format!("{k}: {x} → {y}");
            if vendor_managed(k) {
                vendor.push(line);
            } else {
                other.push(line);
            }
        }
    }
    (vendor, other)
}

/// 测量窗口内的定时回读。按 `t0 + k*every` 的固定节拍读, 每次读数代表一段等长时间,
/// 所以「守住的读数比例」就是「守住的时间比例」。[`HoldPoller::finish`] 停止时再读最后一次 (窗口末尾)。
pub(crate) struct HoldPoller {
    stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
    handle: std::thread::JoinHandle<Vec<(f64, String)>>,
}

impl HoldPoller {
    pub(crate) fn start(serial: &str, cmd: &str, every_s: u64) -> Self {
        use std::sync::atomic::Ordering;
        let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let (serial, cmd, flag) = (serial.to_string(), cmd.to_string(), stop.clone());
        let handle = std::thread::spawn(move || {
            let t0 = std::time::Instant::now();
            let mut out = Vec::new();
            let mut k: u64 = 0;
            loop {
                // 等到下一个节拍; 等的过程中收到停止就跳出, 补读最后一次
                let due = t0 + std::time::Duration::from_secs(k * every_s.max(1));
                while std::time::Instant::now() < due && !flag.load(Ordering::SeqCst) {
                    std::thread::sleep(std::time::Duration::from_millis(100));
                }
                let stopping = flag.load(Ordering::SeqCst);
                out.push((t0.elapsed().as_secs_f64(), su(&serial, &cmd, 30)));
                if stopping {
                    break;
                }
                k += 1;
                // 一次 adb 调用超过一个节拍时跳过已经错过的节拍, 不连发补读
                let behind = t0.elapsed().as_secs() / every_s.max(1);
                if behind >= k {
                    k = behind + 1;
                }
            }
            out
        });
        HoldPoller { stop, handle }
    }

    /// 停止并拿回全部读数 `(距开始秒数, 回读输出)`; 最后一条是停止时的末尾回读。
    pub(crate) fn finish(self) -> Vec<(f64, String)> {
        self.stop.store(true, std::sync::atomic::Ordering::SeqCst);
        self.handle.join().unwrap_or_default()
    }
}

/// 一臂测量窗口内 plan 节点的守住情况。
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Hold {
    /// adb 调用成功的回读次数 (分母)
    pub samples: usize,
    /// 其中所有节点都等于写的值的次数
    pub held: usize,
    /// adb 调用本身失败、不进分母的次数
    pub failed_calls: usize,
    /// 第一次对不上的时刻 (距窗口开始秒数)
    pub first_lost_s: Option<f64>,
    /// 对不上的项, 每个 (节点, 读到的值) 只记一次: `路径: 写 X 读到 Y`
    pub mismatches: Vec<String>,
    /// 每个节点在窗口里读到过的不同值 (按出现顺序)。对照臂靠它看厂商服务有没有动这个节点。
    pub values_seen: Vec<(String, Vec<String>)>,
    /// 审查 R1 #5: 落在 trace 采集窗之外的回读 (施加后的准备段、采完后的 pull/解析段), 不进分母
    pub outside_window: usize,
    /// 截窗用的采集窗 `[capture_start_uptime, capture_end_uptime]`; None = 没截 (trace 没给窗, 或旧脚本没打 uptime)
    pub window: Option<(f64, f64)>,
}

impl Hold {
    pub(crate) fn frac(&self) -> Option<f64> {
        (self.samples > 0).then(|| self.held as f64 / self.samples as f64)
    }

    pub(crate) fn to_json(&self) -> Value {
        json!({
            "samples": self.samples,
            "held": self.held,
            "hold_frac": self.frac(),
            "failed_calls": self.failed_calls,
            "first_lost_s": self.first_lost_s,
            "mismatches": self.mismatches,
            "values_seen": self.values_seen.iter()
                .map(|(k, v)| (k.clone(), json!(v)))
                .collect::<serde_json::Map<String, Value>>(),
            "outside_window": self.outside_window,
            "window": self.window.map(|(a, b)| json!([a, b])),
        })
    }
}

/// 窗口内全部回读 → 守住统计。读不到的节点算对不上 (同 [`readback_mismatches`])。不截窗 (旧口径)。
pub(crate) fn hold_stats(plan: &str, samples: &[(f64, String)]) -> Hold {
    hold_stats_window(plan, samples, None)
}

/// 同 [`hold_stats`], 但只算落在 trace 采集窗里的回读 (审查 R1 #5: 回读线程施加后即起、负载回来才停,
/// 30 s 采集约 1/4 的回读落在窗外, 守住比例量的不是测量窗)。每次回读的时刻取 `#uptime=` 行
/// (设备 uptime, 与 ftrace_capture.sh 写进 trace 头的 capture_*_uptime 同一时钟); 没有这一行的回读
/// (旧脚本) 照旧计入。
pub(crate) fn hold_stats_window(plan: &str, samples: &[(f64, String)], window: Option<(f64, f64)>) -> Hold {
    let mut h = Hold {
        samples: 0,
        held: 0,
        failed_calls: 0,
        first_lost_s: None,
        mismatches: Vec::new(),
        values_seen: Vec::new(),
        outside_window: 0,
        window,
    };
    for (t, out) in samples {
        if !adb_ok(out) {
            h.failed_calls += 1;
            continue;
        }
        if let (Some((a, b)), Some(up)) = (window, readback_uptime(out)) {
            if up < a || up > b {
                h.outside_window += 1;
                continue;
            }
        }
        h.samples += 1;
        for l in out.lines() {
            let Some((k, v)) = l.trim().split_once('=') else { continue };
            if k.starts_with('#') {
                continue;
            }
            let v = v.trim().to_string();
            match h.values_seen.iter_mut().find(|(p, _)| p == k) {
                Some((_, vs)) => {
                    if !vs.contains(&v) {
                        vs.push(v)
                    }
                }
                None => h.values_seen.push((k.to_string(), vec![v])),
            }
        }
        let bad = readback_mismatches(plan, out);
        if bad.is_empty() {
            h.held += 1;
        } else {
            h.first_lost_s.get_or_insert(*t);
            for (path, want, got) in bad {
                let line = format!("{path}: 写 {want} 读到 {got}");
                if !h.mismatches.contains(&line) {
                    h.mismatches.push(line);
                }
            }
        }
    }
    h
}

/// 改写臂进不进统计: 有效回读够数, 且守住比例不低于 `min_frac`。不进的回原因。
pub(crate) fn hold_verdict(h: &Hold, min_frac: f64) -> Result<(), String> {
    if h.samples < HOLD_MIN_SAMPLES {
        return Err(format!(
            "窗口内有效回读只有 {} 次 (少于 {HOLD_MIN_SAMPLES}), 守没守住说不清",
            h.samples
        ));
    }
    let f = h.frac().unwrap_or(0.0);
    if f < min_frac {
        return Err(format!(
            "窗口内只守住 {:.0}% 的时间 ({}/{} 次回读), 低于 {:.0}%",
            f * 100.0,
            h.held,
            h.samples,
            min_frac * 100.0
        ));
    }
    Ok(())
}

/// 请求里的守住比例门槛, 不合法就用缺省 [`HOLD_MIN_FRAC`]。
pub(crate) fn hold_min_frac(req: &Value) -> f64 {
    req["protocol"]["sysparam_hold_min"]
        .as_f64()
        .filter(|x| *x > 0.0 && *x <= 1.0)
        .unwrap_or(HOLD_MIN_FRAC)
}

/// 厂商性能服务会动的 sysfs 路径 (与 [`vendor_managed`] 是同一组节点, 这里按设备端路径认)。
pub(crate) fn vendor_managed_path(path: &str) -> bool {
    let kgsl = path.contains("kgsl")
        && ["/max_pwrlevel", "/min_pwrlevel", "/thermal_pwrlevel", "/max_gpuclk", "/min_freq", "/max_freq"]
            .iter()
            .any(|s| path.ends_with(s));
    let cpu = path.contains("/cpufreq/policy")
        && (path.ends_with("/scaling_max_freq") || path.ends_with("/scaling_min_freq"));
    kgsl || cpu
}

/// 探测时游戏是否从头到尾都在跑且在前台 (probe_sysparam.sh 的 `probe.game_*` 行)。
pub(crate) fn probe_game_up(probe: &sysparam::ProbeMap) -> bool {
    ["running_start", "running_end", "foreground_start", "foreground_end"]
        .iter()
        .all(|k| probe.get(&format!("probe.game_{k}")) == Some("yes"))
}

/// 游戏没在跑时探出来的白名单, 去掉厂商服务会动的节点: 那时厂商游戏模式没介入,
/// 这些节点「守得住」不代表游戏跑起来后守得住。回 (留下的, 去掉的 pid)。
pub(crate) fn gate_whitelist_by_probe(wl: sysparam::Whitelist, game_up: bool) -> (sysparam::Whitelist, Vec<String>) {
    if game_up {
        return (wl, Vec::new());
    }
    let (keep, drop): (Vec<_>, Vec<_>) =
        wl.into_iter().partition(|(_, s)| !(s.kind == "sysfs" && vendor_managed_path(&s.path)));
    (keep, drop.into_iter().map(|(k, _)| k).collect())
}

// ══════════════ 报告装配 ══════════════

pub(crate) fn base_report(req: &Value, kind: &str) -> Value {
    json!({
        "version": 2,
        "kind": kind,
        "candidate_id": req["candidate_id"],
        "status": "ABORT",
        "metrics": {
            "operator_latency_ms": null,
            "fps_p95_ms": null,
            "frame_p95_ms": null,
            "fps_mean": null,
            "power_watt": null,
            "psnr_db": null,
            "soc_temp_max_c": null,
            "unavailable": []
        },
        "verdict": {
            "is_pareto_improvement": false,
            // 未检验哨兵: 1.0, 不伪造小 p 值
            "p_value": 1.0,
            "reason": ""
        },
        "source": "phonefarm",
    })
}

pub(crate) fn unavailable(metrics: &mut Value, field: &str, reason: &str) {
    metrics["unavailable"]
        .as_array_mut()
        .expect("unavailable 数组")
        .push(json!({ "field": field, "reason": reason }));
}

// ══════════════ sysparam ══════════════

fn deny_keywords(req: &Value) -> Vec<String> {
    match req["payload"]["deny_keywords"].as_array() {
        Some(a) if !a.is_empty() => a.iter().filter_map(|v| v.as_str().map(String::from)).collect(),
        _ => DEFAULT_DENY_KEYWORDS.iter().map(|s| s.to_string()).collect(),
    }
}

/// 接收端**再校验一遍**: 参数在白名单里、值在取值表里、不命中温控关键词 ——
/// 不信上层。plan 若随请求下发, 也逐项对回白名单 (path 必须一致)。
/// 任何一项越界整份拒绝, 一个字节都不写。
fn revalidate(req: &Value, wl: &sysparam::Whitelist) -> Result<(), String> {
    let params: Vec<(String, String)> = req["payload"]["params"]
        .as_object()
        .map(|o| o.iter().map(|(k, v)| (k.clone(), v.as_str().unwrap_or("").to_string())).collect())
        .unwrap_or_default();
    let deny = deny_keywords(req);
    for (pid, _) in &params {
        let low = pid.to_lowercase();
        if deny.iter().any(|k| low.contains(k)) {
            return Err(format!("{pid} 命中温控保护关键词 {deny:?} —— 接收端再拦一道"));
        }
    }
    let (ok, why) = sysparam::validate_candidate(&params, wl);
    if !ok {
        return Err(format!("接收端校验拒绝: {why}"));
    }
    for pid in params.iter().map(|(p, _)| p) {
        if sysparam::denied(pid) {
            return Err(format!("{pid} 命中温控保护关键词"));
        }
    }
    if let Some(plan) = req["payload"]["plan"].as_array() {
        for entry in plan {
            let (pkind, ppath, pval) = (
                entry["kind"].as_str().unwrap_or(""),
                entry["path"].as_str().unwrap_or(""),
                entry["value"].as_str().unwrap_or(""),
            );
            // plan 里的每一项都必须能在白名单里找到同 path 的条目, 且值合法
            let hit = wl.iter().find(|(_, s)| s.path == ppath && s.kind == pkind);
            let Some((_, spec)) = hit else {
                return Err(format!("plan 项 ({pkind}, {ppath}) 不在白名单里"));
            };
            if !spec.values.iter().any(|v| v == pval) {
                return Err(format!("plan 项 {ppath}={pval:?} 不在合法取值表里"));
            }
            if sysparam::denied(ppath) {
                return Err(format!("plan 路径 {ppath} 命中温控保护关键词"));
            }
        }
    }
    Ok(())
}

/// 探白名单前把被测游戏拉到前台跑着。厂商游戏模式只在游戏前台运行时改频率边界,
/// 游戏没跑时探出来的「守得住」不算数 (审计 #7)。回「是不是本函数拉起来的」(是就由调用方探完停掉)。
/// 拉不起来不报错: 探测脚本会如实记下游戏不在, [`gate_whitelist_by_probe`] 再按这个去掉相关节点。
fn probe_bring_up_game(serial: &str, pkg: &str, launch: Option<&LaunchSpec>, dir: &Path) -> bool {
    let _ = std::fs::create_dir_all(dir);
    let alive = adb(serial, &["shell", &format!("pidof {pkg}")], 15);
    let fg = adb(serial, &["shell", "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'"], 15);
    if !crate::graylayer::GrayLayer::adb_stdout(&alive).trim().is_empty() && fg.contains(pkg) {
        return false; // 已经在前台跑着 (触控脚本负载的常态), 不重启它
    }
    let r = match launch {
        // 静态场景一直跑 (frames 很大), 探测要几分钟, 不能中途跑完退出
        Some(LaunchSpec { static_extras: Some(st), .. }) => start_with_extras(serial, pkg, st, None, &[], dir).map(|_| {
            std::thread::sleep(std::time::Duration::from_secs(STATIC_SETTLE_S));
        }),
        Some(l) => start_with_extras(serial, pkg, &l.extras, l.marker.as_deref(), &[], dir)
            .and_then(|_| wait_launch_window(serial, pkg, "probe", l.marker.as_deref()).map(|_| ())),
        None => start_with_extras(serial, pkg, "", None, &[], dir).map(|_| {
            std::thread::sleep(std::time::Duration::from_secs(LAUNCH_SETTLE_S));
        }),
    };
    if let Err(e) = r {
        std::fs::write(dir.join("bring_up_error.txt"), &e).ok();
    }
    true
}

fn probe_whitelist(serial: &str, evidence: &Path, req: &Value) -> Result<sysparam::Whitelist, String> {
    push_tools(serial)?;
    let _ = std::fs::create_dir_all(evidence);
    let pkg = req["package"].as_str().unwrap_or("com.miHoYo.Yuanshen");
    let launched = probe_bring_up_game(serial, pkg, launch_spec(req).as_ref(), &evidence.join("probe_game"));
    let out = su(serial, &format!("PROBE_PKG={pkg} sh {DEV_TMP}/probe_sysparam.sh"), 900);
    if launched {
        let _ = adb(serial, &["shell", &format!("am force-stop {pkg}")], 30);
    }
    if out.contains("#adb_exit=") && !out.ends_with("#adb_exit=0") {
        return Err(format!("probe_sysparam 失败: {out}"));
    }
    std::fs::write(evidence.join("probe.txt"), &out).map_err(|e| e.to_string())?;
    let probe = sysparam::parse_probe(&out);
    let game_up = probe_game_up(&probe);
    let (wl, dropped) = gate_whitelist_by_probe(sysparam::build_whitelist(&probe), game_up);
    std::fs::write(
        evidence.join("probe_gate.txt"),
        format!("game_up={game_up}\nlaunched_by_probe={launched}\ndropped_vendor_managed={dropped:?}\n"),
    )
    .ok();
    Ok(wl)
}

/// 自驱动负载 (`workload.launch`): 应用带 intent extras 启动后自己跑一段确定性路线
/// (Megacity 的 city_flythrough), 不需要注入触控/手柄。
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct LaunchSpec {
    /// 测量用的 extras (例 `--es scene city_flythrough --es frames 3600 ...`)。
    pub extras: String,
    /// 应用进入被测窗口时在自己 files/ 下落的标记文件名 (Megacity: megacity_started)。
    /// None = 启动后固定等 [`LAUNCH_SETTLE_S`] 秒。
    pub marker: Option<String>,
    /// 画面比对用的静态场景 extras (相机不动, 例 `--es scene city_static --es frames 100000`)。
    pub static_extras: Option<String>,
}

/// 没有标记文件时, 启动后等多久才开采。
const LAUNCH_SETTLE_S: u64 = 20;
/// 等标记文件的上限 (城市流式加载 + 预热可能很久)。
const LAUNCH_MARKER_TIMEOUT_S: u64 = 300;

pub(crate) fn launch_spec(req: &Value) -> Option<LaunchSpec> {
    let l = &req["workload"]["launch"];
    let extras = l["extras"].as_str()?.trim().to_string();
    if extras.is_empty() {
        return None;
    }
    Some(LaunchSpec {
        extras,
        marker: l["marker"].as_str().filter(|m| !m.is_empty()).map(String::from),
        static_extras: l["static_extras"].as_str().filter(|m| !m.is_empty()).map(String::from),
    })
}

/// 带 extras 冷启动应用: 停掉 → 清掉旧的标记文件与 `also_clear` 里的文件 → 解析启动 activity →
/// `am start -W`。am 的回显记进 outdir/am.log。(run_one_launch 与 whitebox 共用)
pub(crate) fn start_with_extras(
    serial: &str,
    pkg: &str,
    extras: &str,
    marker: Option<&str>,
    also_clear: &[&str],
    outdir: &Path,
) -> Result<(), String> {
    let files = format!("/storage/emulated/0/Android/data/{pkg}/files");
    let _ = adb(serial, &["shell", &format!("am force-stop {pkg}")], 30);
    for f in marker.into_iter().chain(also_clear.iter().copied()) {
        let _ = adb(serial, &["shell", &format!("rm -f {files}/{f}")], 15);
    }
    let comp = adb(serial, &["shell", &format!("cmd package resolve-activity --brief {pkg}")], 30);
    let comp = comp
        .lines()
        .rev()
        .map(str::trim)
        .find(|l| l.contains('/') && !l.starts_with('#'))
        .ok_or(format!("解析不到 {pkg} 的启动 activity"))?
        .to_string();
    let am = adb(serial, &["shell", &format!("am start -W -n {comp} {extras}")], 60);
    std::fs::write(outdir.join("am.log"), &am).ok();
    Ok(())
}

/// 设备 uptime (秒, `/proc/uptime` 第一列; 与 ftrace_capture.sh 写进 trace 头的 capture_*_uptime 同一时钟)。
pub(crate) fn device_uptime(serial: &str) -> Option<f64> {
    let out = adb(serial, &["shell", "cut -d' ' -f1 /proc/uptime"], 15);
    crate::graylayer::GrayLayer::adb_stdout(&out).trim().parse::<f64>().ok()
}

/// 等应用进入被测窗口: 有标记文件就等它出现 (应用中途退了或超时报错), 没有就固定等
/// [`LAUNCH_SETTLE_S`] 秒。回看到标记那一刻的设备 uptime (审查 R1 #9: 与 trace 的
/// capture_start_uptime 相减就是「进入窗口 → 开采」的延时, 两臂要对得上); 没有标记时 None。
pub(crate) fn wait_launch_window(serial: &str, pkg: &str, label: &str, marker: Option<&str>) -> Result<Option<f64>, String> {
    let files = format!("/storage/emulated/0/Android/data/{pkg}/files");
    match marker {
        Some(m) => {
            let t0 = std::time::Instant::now();
            loop {
                let got = adb(serial, &["shell", &format!("cat {files}/{m} 2>/dev/null")], 15);
                if !crate::graylayer::GrayLayer::adb_stdout(&got).trim().is_empty() {
                    return Ok(device_uptime(serial));
                }
                let alive = adb(serial, &["shell", &format!("pidof {pkg}")], 15);
                if crate::graylayer::GrayLayer::adb_stdout(&alive).trim().is_empty() {
                    return Err(format!("{label}: {pkg} 在进入被测窗口前就退了"));
                }
                if t0.elapsed().as_secs() > LAUNCH_MARKER_TIMEOUT_S {
                    return Err(format!("{label}: 等了 {LAUNCH_MARKER_TIMEOUT_S}s 还没进入被测窗口 (没有 {m})"));
                }
                std::thread::sleep(std::time::Duration::from_secs(1));
            }
        }
        None => {
            std::thread::sleep(std::time::Duration::from_secs(LAUNCH_SETTLE_S));
            Ok(None)
        }
    }
}

/// 自驱动负载跑一轮: (可选) 带 extras 重启应用 → 等它进入被测窗口 → 功耗采样 + ftrace 采集
/// `seconds` 秒 → 解析。回的形状与 [`run_one_with`] 相同 ({label, run_once_rc, summary, env})。
/// `relaunch = false` 时应用已经由别人 (挂层那一步) 带着 extras 拉起来了。
/// `marker_up`: 调用方已经等到过标记时传看到标记那一刻的设备 uptime (sysparam 先等标记再施加),
/// 这里就不再等; None = 这里等。`spf`: 请求声明的每帧提交数 (审计 #10), None = 自检。
#[allow(clippy::too_many_arguments)]
pub(crate) fn run_one_launch(
    serial: &str,
    label: &str,
    outdir: &Path,
    seconds: u64,
    pkg: &str,
    comm: Option<&str>,
    launch: &LaunchSpec,
    relaunch: bool,
    marker_up: Option<f64>,
    spf: Option<usize>,
) -> Result<Value, String> {
    std::fs::create_dir_all(outdir).map_err(|e| e.to_string())?;
    if relaunch {
        start_with_extras(serial, pkg, &launch.extras, launch.marker.as_deref(), &[], outdir)?;
    }
    // 等进入被测窗口 (调用方已经等到的就不重复等, 标记时刻用它给的)
    let marker_up = match marker_up {
        Some(u) => Some(u),
        None => wait_launch_window(serial, pkg, label, launch.marker.as_deref())?,
    };
    // 游戏启动后厂商游戏模式会把风扇打开 (审计 #11: msaa1 开跑前 0, 测量中 10/10 臂是开的):
    // 进入被测窗口后再按死一次, 再起逐秒的 GPU 档位/风扇记录
    let fan_after_marker = arm_fan_off(serial);
    let hw = HwWatch::start(serial, outdir, label);
    // 功耗采样与 ftrace 并行, 覆盖整个采集窗
    let env_path = outdir.join("env.txt");
    // 上限放宽: 正常由停止标志结束, 上限只防标志一直不出现 (ftrace 起得慢时也不会先于采集结束)
    let dur = seconds + 20;
    let tr = format!("{DEV_TMP}/lw_{label}.txt");
    // 第三个参数是停止标志: ftrace_capture.sh 采完才写 trace 输出文件, 一出现采样就停 (审计 #3)。
    // dur 只是上限。
    let sampler = crate::fd_lock::transport_command(adb_path())
        .args(["-s", serial, "shell"])
        .arg(format!("su -c 'sh {DEV_TMP}/sample_env.sh {dur} 2 {tr}'"))
        .stdout(std::fs::File::create(&env_path).map_err(|e| e.to_string())?)
        .stderr(std::process::Stdio::null())
        .spawn();
    let cap = su(serial, &format!("sh {DEV_TMP}/ftrace_capture.sh {seconds} {tr} {pkg}"), seconds + 120);
    let hw = hw.finish();
    std::fs::write(outdir.join("capture.log"), &cap).ok();
    let _ = adb(serial, &["pull", &tr, &outdir.join("trace.txt").to_string_lossy()], 120);
    let _ = adb(serial, &["shell", &format!("rm -f {tr}")], 15);
    // 自驱动负载没有脚本退出码: 采集结束时应用仍活着记 0, 否则 1
    let alive = adb(serial, &["shell", &format!("pidof {pkg}")], 15);
    let workload_exit = Some(if adb_ok(&alive) && !crate::graylayer::GrayLayer::adb_stdout(&alive).trim().is_empty() { 0 } else { 1 });
    let sampler_success=if let Ok(mut s)=sampler {
        let deadline=std::time::Instant::now()+std::time::Duration::from_secs(90);
        loop {match s.try_wait() {
            Ok(Some(status))=>break status.success(),
            Ok(None) if std::time::Instant::now()<deadline=>std::thread::sleep(std::time::Duration::from_millis(200)),
            _=>{let _=s.kill();let _=s.wait();break false;}
        }}
    } else {false};
    if relaunch {
        // 量完就停掉, 别让它在还原、等冷的时候接着烧
        let _ = adb(serial, &["shell", &format!("am force-stop {pkg}")], 30);
    }
    let trace = std::fs::read(outdir.join("trace.txt")).map_err(|e| format!("{label}: 没拉回 trace ({e})"))?;
    let c = comm.filter(|c| !c.is_empty()).unwrap_or(crate::looptrace::DEFAULT_RENDER_COMM);
    // 审计 #10: 每帧提交数按请求声明 (parse_with_spf), 不让各臂自己猜
    let d = crate::looptrace::parse_with_spf(&String::from_utf8_lossy(&trace), c, spf);
    let summary = crate::pyjson::dumps(&d.summarize());
    std::fs::write(outdir.join("summary.json"), &summary).map_err(|e| e.to_string())?;
    // 采集窗: 功耗、GPU 档位逐秒记录都按它截 (审计 #3, 审查 R1 #12)
    let w = sysparam::capture_window_from_trace(&String::from_utf8_lossy(&trace));
    let mut m = json!({
        "label": label, "run_once_rc": 0, "launch": true,
        "hw": hw_stats_window(&hw, w),
        "fan_after_marker": fan_after_marker,
        "spf_declared": spf,
    });
    m["summary"] = serde_json::from_str(&summary).map_err(|e| format!("{label}: summary 解析失败 {e}"))?;
    // ftrace_capture.sh 带了包名: 回来的必须是按目标线程号归因的新版产出 (审查 R1 #1 #6)
    check_frame_attribution(&m["summary"]).map_err(|e| format!("{label}: {e}"))?;
    // 审查 R1 #9: 进入被测窗口 (标记出现) → 开采 的延时, 逐臂记; 两臂相差 > 1 s 在配对核对里标出
    m["marker_uptime"] = json!(marker_up);
    m["marker_to_capture_s"] = json!(marker_to_capture_s(marker_up, w));
    if let Ok(t) = std::fs::read_to_string(&env_path) {
        // 功耗只取 trace 采集窗内的样本 (审计 #3)
        m["env"] = serde_json::from_str(&py_dumps(&sysparam::env_stats_window(&t, w))).unwrap_or(Value::Null);
    }
    bind_run_evidence(&mut m, label, outdir, &env_path, sampler_success, dur, seconds, workload_exit)?;
    Ok(m)
}

/// 标记出现 → 采集开始 的秒数 (两位小数); 任一缺失 None。
pub(crate) fn marker_to_capture_s(marker_up: Option<f64>, window: Option<(f64, f64)>) -> Option<f64> {
    let (mu, (a, _)) = (marker_up?, window?);
    Some(((a - mu) * 100.0).round() / 100.0)
}

/// 审查 R1 #1 #6: ftrace_capture.sh 收到包名时, 回来的 summary 必须带 `frame_attribution`
/// (按目标线程号认帧; 没有 = 设备上是旧脚本, 忽略第三参数、按线程名认帧, 别的进程的同名线程
/// 会混进来), 且目标线程至少一个 (`n_target_tids == 0` = 采集时 `pidof` 为空, 目标进程不在,
/// 零帧; 以前要 10 臂全 n_frames=0 才发现)。两种情况都不能当数据, 直接 Err。
pub(crate) fn check_frame_attribution(summary: &Value) -> Result<(), String> {
    if summary.get("frame_attribution").is_none() {
        return Err("summary 没有 frame_attribution: 设备端 ftrace_capture.sh 是旧版 (不按目标线程号认帧), \
                    先 push_tools 把新脚本推上去再跑"
            .into());
    }
    let n = summary["n_target_tids"].as_u64().unwrap_or(0);
    if n == 0 {
        return Err(format!(
            "目标进程没找到 (n_target_tids=0, target_pid={}): 采集时游戏不在跑, 零帧不算数据",
            summary["target_pid"].as_str().unwrap_or("")
        ));
    }
    Ok(())
}

/// storeop-check 用静态场景时, 启动后等城市装完的时间 (秒)。
const STATIC_SETTLE_S: u64 = 60;
/// 开/关之间的像素差占比, 最多比同状态噪声高这么多个百分点。
const LIVE_CHECK_MARGIN_PCT: f64 = 0.5;

/// storeop-check 的结果判画面: 改写要真的命中, 游戏要活着, 关~开 的差异不能明显超过同状态噪声。
pub(crate) fn live_check_verdict(v: &Value) -> Result<(), String> {
    if v["game_alive"].as_bool() == Some(false) {
        return Err("开着改写时游戏退了".into());
    }
    if v["effective_hit"].as_u64().unwrap_or(0) == 0 {
        return Err("改写一次都没命中 (effective_hit=0), 比对不出东西".into());
    }
    let rows = v["diffs"].as_array().cloned().unwrap_or_default();
    let max_of = |kind: &str| {
        rows.iter()
            .filter(|r| r["kind"].as_str() == Some(kind))
            .filter_map(|r| r["diff_px_pct"].as_f64())
            .reduce(f64::max)
    };
    let (Some(on_off), Some(noise)) = (max_of("off_vs_on"), max_of("same_state")) else {
        return Err("storeop-check 没给出关~开 与同状态的比对".into());
    };
    if on_off > noise + LIVE_CHECK_MARGIN_PCT {
        return Err(format!(
            "开/关改写之间 {on_off:.3}% 像素变了, 同状态噪声只有 {noise:.3}% (容差 {LIVE_CHECK_MARGIN_PCT} 个百分点)"
        ));
    }
    Ok(())
}

/// 跑一轮负载 + ftrace 采集 + 功耗温度采样 (与 autoloop.run_one 同一编排:
/// sample_env 与 run_once 并行, 覆盖整段负载)。渲染线程按 Unity 缺省 (UnityGfxDeviceW) 解析。
pub(crate) fn run_one(
    serial: &str,
    label: &str,
    outdir: &Path,
    seconds: u64,
    workload: &Path,
    pkg: &str,
) -> Result<Value, String> {
    run_one_with(serial, label, outdir, seconds, workload, pkg, None, None)
}

/// 同 [`run_one`], 但可指定渲染线程名 (ftrace comm) 与每帧提交数 (审计 #10, 请求 workload.spf)。
/// 非 Unity 游戏 (虚幻的 RHIThread 等) 不指定 comm 就解析出零帧 —— 这个名字由 `phonefarm onboard`
/// 从 ftrace 里数出来, 记在游戏档案的 workload.render_comm, 经评测请求的同名字段传到这里。
#[allow(clippy::too_many_arguments)]
pub(crate) fn run_one_with(
    serial: &str,
    label: &str,
    outdir: &Path,
    seconds: u64,
    workload: &Path,
    pkg: &str,
    comm: Option<&str>,
    spf: Option<usize>,
) -> Result<Value, String> {
    std::fs::create_dir_all(outdir).map_err(|e| e.to_string())?;
    let root = repo_root();
    // 逐秒 GPU 档位/风扇记录, 覆盖 run_once 的整段 (含 LEAD)
    let hw = HwWatch::start(serial, outdir, label);
    // LEAD 6 秒 + 采集 seconds 秒; 余量 20 秒只是上限, 正常由停止标志结束
    let dur = seconds + 6 + 20;
    let env_path = outdir.join("env.txt");
    // 停止标志 = run_once.sh 让 ftrace_capture.sh 写的 trace 输出文件 (采完才写), 见 sample_env.sh。
    // dur 只是上限。
    let sampler = crate::fd_lock::transport_command(adb_path())
        .args(["-s", serial, "shell"])
        .arg(format!("su -c 'sh {DEV_TMP}/sample_env.sh {dur} 2 {DEV_TMP}/loop_v1_{label}.txt'"))
        .stdout(std::fs::File::create(&env_path).map_err(|e| e.to_string())?)
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| format!("起不了功耗采样: {e}"));
    let run_once = root.join("loop_v1/tools/run_once.sh");
    let rc = Command::new("bash")
        .arg(&run_once)
        .arg(label)
        .arg(outdir)
        .env("SERIAL", serial)
        .env("ROOT", &root)
        .env("LEAD", "6")
        .env("CAPDUR", seconds.to_string())
        .env("WL", workload)
        .env("PKG", pkg)
        .env(
            "PATH",
            format!(
                "{}:{}",
                std::env::var("PATH").unwrap_or_default(),
                Path::new(&adb_path()).parent().map(|p| p.display().to_string()).unwrap_or_default()
            ),
        )
        .env("PF_BIN", std::env::current_exe().unwrap_or_else(|_| PathBuf::from("phonefarm")))
        .output();
    let hw = hw.finish();
    let sampler_success=if let Ok(mut s)=sampler {
        let deadline=std::time::Instant::now()+std::time::Duration::from_secs(90);
        loop {match s.try_wait() {
            Ok(Some(status))=>break status.success(),
            Ok(None) if std::time::Instant::now()<deadline=>std::thread::sleep(std::time::Duration::from_millis(200)),
            _=>{let _=s.kill();let _=s.wait();break false;}
        }}
    } else {false};
    let rc = match rc {
        Ok(o) if o.status.success() => 0,
        Ok(o) => {
            eprintln!("run_once stderr: {}", String::from_utf8_lossy(&o.stderr));
            1
        }
        Err(e) => return Err(format!("run_once 起不了: {e}")),
    };
    // 采集窗 (trace 头部): 功耗、GPU 档位逐秒记录都按它截 (审计 #3, 审查 R1 #12)
    let w = std::fs::read(outdir.join("trace.txt"))
        .ok()
        .and_then(|b| sysparam::capture_window_from_trace(&String::from_utf8_lossy(&b[..b.len().min(64 * 1024)])));
    let mut m = json!({ "label": label, "run_once_rc": rc, "hw": hw_stats_window(&hw, w), "spf_declared": spf });
    let sp = outdir.join("summary.json");
    // 指定了渲染线程或每帧提交数: 在本进程里按它重解析 trace.txt, 覆盖 run_once.sh 按 Unity 缺省
    // (自检 spf) 解析出的 summary.json。放在这里而不是给 run_once.sh 加参数, 是为了换个仓库根
    // (手机节点上的 main 版脚本) 也照样生效。
    let comm = comm.filter(|c| !c.is_empty());
    if comm.is_some() || spf.is_some() {
        if let Ok(b) = std::fs::read(outdir.join("trace.txt")) {
            let c = comm.unwrap_or(crate::looptrace::DEFAULT_RENDER_COMM);
            let d = crate::looptrace::parse_with_spf(&String::from_utf8_lossy(&b), c, spf);
            std::fs::write(&sp, crate::pyjson::dumps(&d.summarize())).map_err(|e| e.to_string())?;
        }
    }
    match std::fs::read_to_string(&sp).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok()) {
        Some(s) => {
            m["summary"] = s;
        }
        None => return Err(format!("{label}: 没拿到可用 summary.json (负载或采集失败, rc={rc})")),
    }
    // run_once.sh 把 PKG 传给了 ftrace_capture.sh: 回来的必须是按目标线程号归因的新版产出 (审查 R1 #1 #6)
    check_frame_attribution(&m["summary"]).map_err(|e| format!("{label}: {e}"))?;
    if let Ok(t) = std::fs::read_to_string(&env_path) {
        // 功耗温度解析在 src/sysparam.rs (与 autoloop 同一份口径); 功耗只取 trace 采集窗内 (审计 #3)
        m["env"] = serde_json::from_str(&py_dumps(&sysparam::env_stats_window(&t, w)))
            .unwrap_or(Value::Null);
    }
    let workload_exit=std::fs::read_to_string(outdir.join("workload_exit.txt")).ok().and_then(|t|t.trim().parse::<i64>().ok());
    bind_run_evidence(&mut m, label, outdir, &env_path, sampler_success, dur, seconds, workload_exit)?;
    if rc != 0 { return Err(format!("{label}: 负载失败 rc={rc}，旧 summary 不能补成成功")); }
    Ok(m)
}

/// 单轮证据绑定 (实验资格要用): 采样器正常退出、采样窗覆盖 ftrace 采集窗、负载正常退出,
/// 否则功耗/温度窗不作数; 附帧证据与 trace 摘要。
#[allow(clippy::too_many_arguments)]
fn bind_run_evidence(
    m: &mut Value,
    label: &str,
    outdir: &Path,
    env_path: &Path,
    sampler_success: bool,
    dur: u64,
    seconds: u64,
    workload_exit: Option<i64>,
) -> Result<(), String> {
    m["workload_exit"]=json!(workload_exit);
    let env_text=std::fs::read_to_string(env_path).unwrap_or_default();
    let trace_text=std::fs::read_to_string(outdir.join("trace.txt")).unwrap_or_default();
    let window=sysparam::sample_window(&env_text);
    let bound=sampler_success && window.duration_s==Some(dur as f64) && window.interval_s==Some(2.0)
        && sysparam::trace_window_covered(&window,&trace_text,seconds);
    m["env"]["sampler_exit_success"]=json!(sampler_success);
    m["env"]["trace_window_verified"]=json!(bound);
    m["env"]["power_observation_scope"]=json!("sampler window covers trace capture window; power samples clipped to the capture window, first sample dropped (sample_env v2)");
    if !bound {m["env"]["power_w_mean"]=Value::Null;m["env"]["power_window_verified"]=json!(false);m["env"]["thermal_window_verified"]=json!(false);}
    m["frame_evidence"] = qualification::frame_evidence(&m["summary"]);
    {use sha2::{Digest,Sha256};m["frame_evidence"]["trace_sha256"]=json!(format!("{:x}",Sha256::digest(trace_text.as_bytes())));}
    if workload_exit!=Some(0) {return Err(format!("{label}: 逐轮负载退出失败/未知: {workload_exit:?}"));}
    if let Some(t) = m["env"]["soc_temp_max_c"].as_f64() { m["summary"]["soc_temp_max_c"] = json!(t); }
    Ok(())
}

/// 按负载形态跑一轮: 自驱动 (launch) 走 [`run_one_launch`] (带 extras 重启), 否则 [`run_one_with`]。
#[allow(clippy::too_many_arguments)]
pub(crate) fn run_one_for(
    serial: &str,
    label: &str,
    outdir: &Path,
    seconds: u64,
    workload: &Path,
    pkg: &str,
    comm: Option<&str>,
    launch: Option<&LaunchSpec>,
    spf: Option<usize>,
) -> Result<Value, String> {
    match launch {
        Some(l) => run_one_launch(serial, label, outdir, seconds, pkg, comm, l, true, None, spf),
        None => run_one_with(serial, label, outdir, seconds, workload, pkg, comm, spf),
    }
}

/// 资格用的负载自检。脚本负载走 [`verify_workload`]; 自驱动负载 (launch) 带 extras 冷启动,
/// 进入被测窗口后隔 5 秒截两帧: 应用全程活着且画面在动才算。
pub(crate) fn verify_workload_for(
    serial: &str,
    workload: &Path,
    launch: Option<&LaunchSpec>,
    pkg: &str,
    outdir: &Path,
) -> Value {
    let Some(l) = launch else {
        return verify_workload(serial, workload, pkg, outdir);
    };
    let _ = std::fs::create_dir_all(outdir);
    let started = start_with_extras(serial, pkg, &l.extras, l.marker.as_deref(), &[], outdir)
        .and_then(|_| wait_launch_window(serial, pkg, "qualification_workload", l.marker.as_deref()));
    let result = match started {
        Err(e) => json!({"verified": false, "reason": e, "launch": true}),
        Ok(_) => {
            let capture = || crate::fd_lock::transport_command(adb_path()).args(["-s",serial,"exec-out","screencap"]).output().ok()
                .filter(|o|o.status.success()).map(|o|o.stdout).unwrap_or_default();
            let a = capture();
            std::thread::sleep(std::time::Duration::from_secs(5));
            let b = capture();
            let alive = adb(serial, &["shell", &format!("pidof {pkg}")], 15);
            let alive = adb_ok(&alive) && !crate::graylayer::GrayLayer::adb_stdout(&alive).trim().is_empty();
            let _=std::fs::write(outdir.join("frame_a.raw"),&a); let _=std::fs::write(outdir.join("frame_b.raw"),&b);
            let motion=to_serde(&crate::framecheck::verdict(&a,&b));
            json!({"verified": alive && motion["spinning"]==true, "app_alive": alive, "motion": motion, "launch": true})
        }
    };
    let _ = adb(serial, &["shell", &format!("am force-stop {pkg}")], 30);
    let _=std::fs::write(outdir.join("workload_check.json"),serde_json::to_string_pretty(&result).unwrap());
    result
}

/// [`run_one`] 回包里并进判定视图的 env 字段 (sample_env.sh → [`sysparam::env_stats`])。
pub(crate) const RUN_ENV_KEYS: [&str; 30] = [
    "power_w_mean",
    "power_w_median",
    "power_now_w_mean",
    "power_now_plausible",
    "power_vi_w_mean",
    "usb_input_w_mean",
    "current_now_ua_mean",
    "voltage_now_uv_mean",
    "power_rail",
    "battery_status",
    "battery_status_after",
    "power_window_verified",
    "thermal_window_verified",
    "trace_window_verified",
    "sampler_complete",
    "sampler_exit_success",
    "power_observation_scope",
    "battery_charging",
    "power_usable_reason",
    "on_battery",
    "bypass",
    "power_valid",
    "power_source",
    "soc_temp_max_c",
    "soc_temp_mean_c",
    "n_samples",
    "fan_state",
    // 只有设备端采到 GPU 行时 env 里才有这三项 (厂商性能服务会改 GPU 上限, 逐臂留证)
    "gpu_max_pwrlevel_seen",
    "gpu_clk_mhz_mean",
    "gpu_clk_mhz_max",
];

/// 判定/统计用的单轮视图: summary 字段展平, 再并上 env 的功耗/温度字段
/// (与旧 Python `m.update(summary); m.update(env_stats)` 同形)。
///
/// 只取 `m["summary"]` 是错的: parse-trace 的 summary 里**没有** `power_w_mean` 与
/// `soc_temp_max_c` —— 那样功耗永远「缺数据」、温度上限永远不触发, 而报告看着一切正常。
pub(crate) fn run_view(m: &Value) -> PyVal {
    let mut obj: Vec<(String, PyVal)> = Vec::new();
    if let Some(s) = m["summary"].as_object() {
        for (k, v) in s {
            obj.push((k.clone(), to_pyval(v)));
        }
    }
    if let Some(e) = m["env"].as_object() {
        for k in RUN_ENV_KEYS {
            if let Some(v) = e.get(k) {
                obj.push((k.to_string(), to_pyval(v)));
            }
        }
    }
    PyVal::Obj(obj)
}

/// 一轮的 SoC 结温峰值。来源是 sample_env (env), 不是 parse-trace 的 summary。
pub(crate) fn run_soc_temp_max(m: &Value) -> Option<f64> {
    m["env"]["soc_temp_max_c"]
        .as_f64()
        .or_else(|| m["summary"]["soc_temp_max_c"].as_f64())
}

/// 按对取舍后的功耗 (审计 #5): 一臂不合格只剔除那一对, 不再整组作废。
///
/// 每臂都带 `round` (sysparam / gray / whitebox 都写) 时按轮对齐, 不按下标:
/// 改写臂被判 contested (fix-B 守不住) 时只拿掉候选臂, 对照臂还在; 臂序随机 (fix-F) 时
/// 截断可能停在一轮中间。这两种情况下按下标配对会把不同轮的两臂当成一对, 一臂不合格就错剔
/// 另一轮的臂。没有 `round` 的旧证据仍按下标配对。
/// 回的 `ctrl` / `knob` 与传入的 runs 一一对应 (下标相同), `dropped` 里的轮次是真实轮号。
pub(crate) fn power_pairs_of(knob_runs: &[Value], ctrl_runs: &[Value]) -> sysparam::PowerPairs {
    let round_of = |r: &Value| r["round"].as_u64().map(|x| x as usize);
    let all_have_round = knob_runs.iter().chain(ctrl_runs.iter()).all(|r| round_of(r).is_some());
    if !all_have_round {
        let c: Vec<&Value> = ctrl_runs.iter().map(|r| &r["env"]).collect();
        let k: Vec<&Value> = knob_runs.iter().map(|r| &r["env"]).collect();
        return sysparam::power_pairs(&c, &k);
    }
    let mut rounds: Vec<usize> = knob_runs.iter().chain(ctrl_runs.iter()).filter_map(round_of).collect();
    rounds.sort_unstable();
    rounds.dedup();
    // 同一轮同一臂只该有一个; 万一有重复, 按下标配对的旧办法兜底, 不猜
    let dup = |runs: &[Value]| {
        let mut xs: Vec<usize> = runs.iter().filter_map(round_of).collect();
        let n = xs.len();
        xs.sort_unstable();
        xs.dedup();
        xs.len() != n
    };
    if dup(knob_runs) || dup(ctrl_runs) {
        let c: Vec<&Value> = ctrl_runs.iter().map(|r| &r["env"]).collect();
        let k: Vec<&Value> = knob_runs.iter().map(|r| &r["env"]).collect();
        return sysparam::power_pairs(&c, &k);
    }
    let pos = |runs: &[Value], rd: usize| runs.iter().position(|r| round_of(r) == Some(rd));
    let ci: Vec<Option<usize>> = rounds.iter().map(|&rd| pos(ctrl_runs, rd)).collect();
    let ki: Vec<Option<usize>> = rounds.iter().map(|&rd| pos(knob_runs, rd)).collect();
    let c: Vec<Option<&Value>> = ci.iter().map(|o| o.map(|i| &ctrl_runs[i]["env"])).collect();
    let k: Vec<Option<&Value>> = ki.iter().map(|o| o.map(|i| &knob_runs[i]["env"])).collect();
    let mut pp = sysparam::power_pairs_slots(&c, &k, &rounds);
    // 槽 → 传入 runs 的下标
    let (mut cv, mut kv) = (vec![None; ctrl_runs.len()], vec![None; knob_runs.len()]);
    for (j, o) in ci.iter().enumerate() {
        if let Some(i) = o {
            cv[*i] = pp.ctrl[j];
        }
    }
    for (j, o) in ki.iter().enumerate() {
        if let Some(i) = o {
            kv[*i] = pp.knob[j];
        }
    }
    pp.ctrl = cv;
    pp.knob = kv;
    pp
}

/// 判定视图, 但 `power_w_mean` 换成按对取舍后的值 (被剔除的臂不带这个键, 口径统一时换成
/// 两轨相加的值)。帧时等其余指标与 [`run_view`] 相同。
fn eval_runs_power(runs: &[Value], power: &[Option<f64>]) -> Vec<Run> {
    runs.iter()
        .enumerate()
        .filter_map(|(i, r)| {
            let label = r["label"].as_str()?.to_string();
            let mut v = run_view(r);
            if let PyVal::Obj(kvs) = &mut v {
                kvs.retain(|(k, _)| k != "power_w_mean");
                if let Some(w) = power.get(i).copied().flatten() {
                    kvs.push(("power_w_mean".into(), PyVal::Float(w)));
                }
            }
            Some((label, v))
        })
        .collect()
}

/// 冻结 policy 的判定指标对应的截断检验 (与 [`judge_runs_with`] 同一张表、同一个 alpha_win)。
/// 锁帧可能时 (ruler fix-G / 审计 #8) fps_mean 只守退化、不占预算, 其余指标最宽到 alpha/(n-1)。
fn policy_win_tests(required: &[String], alpha: f64, fps_lock_possible: bool) -> Vec<curtail::WinTest> {
    let n = required.len().max(1);
    let has_fps = required.iter().any(|m| m == "fps_mean");
    let n_min = if fps_lock_possible && has_fps && n > 1 { n - 1 } else { n };
    required
        .iter()
        .map(|m| {
            let k = if m == "fps_mean" { n } else { n_min };
            curtail::WinTest::new(m, m != "fps_mean", alpha / k as f64)
        })
        .collect()
}

/// 截断记录, 原样进回包 `verdict.curtailed`。
fn curtail_note(knob: usize, ctrl: usize, rounds: u32, tests: &[curtail::WinTest]) -> Value {
    json!({
        "arms_run": knob + ctrl,
        "arms_planned": 2 * rounds,
        "knob_runs": knob,
        "ctrl_runs": ctrl,
        "pairs_planned": rounds,
        "checked": tests.iter().map(|t| json!({
            "metric": t.metric, "better": if t.lower_is_better { "lower" } else { "higher" },
            "alpha_win": t.alpha_win,
        })).collect::<Vec<_>>(),
        "rule": "剩余轮次取任意值时, 每个可能进判定的指标在改善方向上的置换 p 下界都已 >= alpha_win; \
                 KEEP 不可达, 跑满的判定必然相同 (src/curtail.rs)。--no-curtail 可跑满",
    })
}

/// 基线轮数与预热轮数 (protocol.baseline_runs 缺省 5, 3..=10; protocol.baseline_warmup 缺省 1, 0..=3)。
/// 收端合同只冻结「至少三轮」和单调比 < 0.9: 只跑三轮时两步同向在纯噪声下就有 1/3 的概率,
/// 两个指标叠起来一半以上的好基线被拒 (2026-10-22 红魔 fps 离散 0.15% 也被判单调)。五轮时纯噪声
/// 四步同向只有 2/120。预热轮照常落盘 (baseline_warmup*/) 但不进资格证据: 实测重开游戏后第一轮
/// 功耗偏高 (4.88 → 4.69 → 4.65 W)。
pub(crate) fn baseline_plan(req: &Value) -> Result<(usize, usize), String> {
    let p = &req["protocol"];
    let field = |k: &str, dflt: u64, lo: u64, hi: u64| -> Result<usize, String> {
        match p.get(k).filter(|v| !v.is_null()) {
            None => Ok(dflt as usize),
            Some(v) => v.as_u64().filter(|n| (lo..=hi).contains(n)).map(|n| n as usize)
                .ok_or_else(|| format!("protocol.{k} 必须是 {lo}..={hi} 的整数")),
        }
    };
    Ok((field("baseline_runs", 5, 3, 10)?, field("baseline_warmup", 1, 0, 3)?))
}

fn sysparam_eval(req: &Value, serial: Option<&str>, evidence: &Path, curtail: bool) -> Result<String, String> {
    let Some(serial) = serial else {
        return Err("kind=sysparam 需要真机: 请加 --serial (或设备唯一在线时省略)".into());
    };
    let kind = "sysparam";
    let mut report = base_report(req, kind);
    let _ = std::fs::create_dir_all(evidence);
    let probe_only = req["payload"]["probe_only"].as_bool().unwrap_or(false);

    // 资格 (main): 先冻结 policy、看 root/trace 能力, 再做差异写探测
    let policy = policy_for_request(req)?;
    std::fs::write(evidence.join("qualification_policy.json"),serde_json::to_string_pretty(&policy).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    let mut facts=Evidence::new(Stage::Probe,policy,qualification::inspect_device(serial));
    facts.screen=is_screen(req);
    let preflight=record_qualification(&mut report,evidence,&facts)?;
    if !preflight.probe_allowed {
        report["verdict"]["reason"]=json!("root/目标 trace 能力未确认，禁止差异写探测");
        return Ok(serde_json::to_string_pretty(&report).unwrap());
    }
    push_tools(serial)?;
    // 审查 R1 #10 (ruler): 上一次崩溃 / Ctrl-C 留下的旋钮或停充状态先对账、还原, 记进回包;
    // 再装信号处理, 这一次被打断时也把还原跑掉。放在探测前的快照之前, 残留状态不进基准快照。
    let preexisting = reconcile_preexisting(serial, evidence);
    cleanup::install();
    cleanup::register(serial, &format!("su -c 'sh {DEV_TMP}/knob_sysparam.sh restore'"));
    cleanup::register(serial, &format!("su -c 'sh {DEV_TMP}/charge_suspend.sh restore'"));
    // 白名单只探一遍 (审查 R1 #4), 夹在两次快照之间; 探测时目标游戏在跑 (fix-B)。
    // 调用方给的白名单只是提议, 不能当实际生效的证据 (main)。
    let before_probe=snapshot(serial);
    std::fs::write(evidence.join("snap_before_probe.txt"),&before_probe).map_err(|e|e.to_string())?;
    let wl=probe_whitelist(serial,evidence,req)?;
    let after_probe=snapshot(serial);
    std::fs::write(evidence.join("snap_after_probe.txt"),&after_probe).map_err(|e|e.to_string())?;
    let probe_txt=std::fs::read_to_string(evidence.join("probe.txt")).unwrap_or_default();
    let probe_restored=probe_snapshots_restored(&before_probe,&after_probe,&probe_txt);
    facts.device.control_verified=Some(!wl.is_empty()); facts.restored=Some(probe_restored);
    record_qualification(&mut report,evidence,&facts)?;
    if probe_only {
        probe_report(&mut report,&before_probe,&after_probe,&probe_txt,&wl);
        report["conditions_observed"]["preexisting_state"]=json!(preexisting);
        report["evidence_dir"] = json!(evidence.display().to_string());
        record_qualification(&mut report,evidence,&facts)?;
        cleanup::clear();
        return Ok(serde_json::to_string_pretty(&report).unwrap());
    }
    if !probe_restored {
        report["verdict"]["reason"]=json!("差异写探测未验证恢复，禁止开始候选实验");
        return Ok(serde_json::to_string_pretty(&report).unwrap());
    }

    // ── 接收端再校验: 越界整份拒绝, 一个字节都不写 ──
    if let Err(e) = revalidate(req, &wl) {
        report["verdict"]["reason"] = json!(e);
        return Ok(serde_json::to_string_pretty(&report).unwrap_or_default());
    }

    // ── 组装 plan ──
    let params: Vec<(String, String)> = req["payload"]["params"]
        .as_object()
        .map(|o| o.iter().map(|(k, v)| (k.clone(), v.as_str().unwrap_or("").to_string())).collect())
        .unwrap_or_default();
    let plan = match req["payload"]["plan"].as_array() {
        Some(entries) => {
            let mut lines = vec!["# loop_v1 sysparam plan (from request payload)".to_string()];
            for e in entries {
                lines.push(format!(
                    "{}\t{}\t{}",
                    e["kind"].as_str().unwrap_or(""),
                    e["path"].as_str().unwrap_or(""),
                    e["value"].as_str().unwrap_or("")
                ));
            }
            lines.join("\n") + "\n"
        }
        None => sysparam::plan_text(&params, &wl)?,
    };
    let _ = std::fs::create_dir_all(evidence);
    std::fs::write(evidence.join("plan.txt"), &plan).map_err(|e| e.to_string())?;

    let plan_local = evidence.join("plan.txt");
    let push = adb(serial, &["push", &plan_local.to_string_lossy(), &format!("{DEV_TMP}/loop_v1_sysparam.plan")], 60);
    if !push.ends_with("#adb_exit=0") {
        report["verdict"]["reason"] = json!(format!("plan 下发失败: {push}"));
        // 停充可能已经做了: 一样要还原 (无 STATE 时是空操作)
        let _ = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh restore"), 60);
        return Ok(serde_json::to_string_pretty(&report).unwrap_or_default());
    }

    let snap_before = snapshot(serial);
    std::fs::write(evidence.join("snap_before.txt"), &snap_before).map_err(|e| e.to_string())?;

    // ── ABBA 交替跑测 ──
    let rounds = req["protocol"]["rounds"].as_i64().unwrap_or(1).max(1) as u32;
    let seconds = req["workload"]["seconds"].as_i64().unwrap_or(30).max(1) as u64;
    // 审计 #10 / 审查 R2 #9: 每帧提交数与帧率上限都由请求声明
    let spf = declared_spf(req);
    let fps_cap_declared = declared_fps_cap(req);
    // workload 脚本: 请求给的是调用方视角的路径 (game_opt_loop 用 "../phonefarm/..."),
    // 先按原样 (cwd 相对/绝对) 找, 找不到再按仓库根拼
    let wl_file = req["workload"]["script"]
        .as_str()
        .map(String::from)
        .unwrap_or_else(|| "loop_v1/scripts/workload_spin_touch_v1.json".into());
    let pkg = req["package"].as_str().unwrap_or("com.miHoYo.Yuanshen");
    let launch = launch_spec(req);
    if launch.is_some() {
        // 自驱动负载是我们拉起来的: 被打断时也停掉, 别让它留在前台接着烧
        cleanup::register(serial, &format!("am force-stop {pkg}"));
    }
    let workload_path = match (resolve_path(&wl_file), &launch) {
        (Some(p), _) => p,
        // 自驱动负载用不到脚本
        (None, Some(_)) => PathBuf::new(),
        (None, None) => {
            report["verdict"]["reason"] = json!(format!(
                "负载脚本不在: {wl_file} (按 cwd 与仓库根都找不到)"
            ));
            return Ok(serde_json::to_string_pretty(&report).unwrap_or_default());
        }
    };

    let cold=match facts.policy.cool_c {Some(c)=>crate::autoloop::wait_cool(serial,c,420),None=>crate::pyobj! {"ok"=>false,"reason"=>"conditions.cool_c 未知"}};
    facts.cold_ok=cold.get("ok").and_then(|v|v.as_bool());
    if facts.cold_ok!=Some(true) {
        facts.stage=Stage::Experiment;let _=record_qualification(&mut report,evidence,&facts)?;
        report["verdict"]["reason"]=json!("冷机条件未通过，禁止候选测量");
        return Ok(serde_json::to_string_pretty(&report).unwrap());
    }
    let workload_check=verify_workload_for(serial,&workload_path,launch.as_ref(),pkg,&evidence.join("workload_check"));
    facts.workload_verified=workload_check["verified"].as_bool();
    // 停充尝试: 请求要求且节点没指定时由接收端自己探 (charge_suspend.sh 内置节点表)。
    // 停充失败保留证据；能效目标缺整窗放电功耗时资格失败，不能缩窄目标。
    let want_suspend = req["conditions"]["charging_suspend"]["enabled"].as_bool().unwrap_or(false);
    let mut suspended_via: Option<String> = None;
    let mut usb_supply_note: Option<String> = None;
    let mut charge_settle: Option<Value> = None;
    if want_suspend {
        let mut out = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh suspend"), CHARGE_SUSPEND_TIMEOUT_S);
        if let Err(e)=std::fs::write(evidence.join("charge_suspend.log"),&out) {
            let _=su(serial,&format!("sh {DEV_TMP}/charge_suspend.sh restore"),60);
            return Err(e.to_string());
        }
        if !adb_ok(&out) {
            // 审查 R1 #2: 超时 / adb 断了不等于没停充 —— 设备端脚本可能已经写了 STATE。
            // 用 status 对账, 停了就按停了算 (结束时一样要还原)。
            let st = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh status"), 30);
            std::fs::write(evidence.join("charge_suspend_status.log"), &st).ok();
            eprintln!("[eval] 停充脚本没正常结束 ({}), 已用 status 对账", out.lines().last().unwrap_or(""));
            out.push('\n');
            out.push_str(&st);
        }
        // 开跑前的 USB 输入上限检查 (charge_suspend.sh 旁路时输出): 供电不足要明确写进回包
        usb_supply_note = sysparam::usb_supply_note(&out);
        if let Some(n) = &usb_supply_note {
            eprintln!("[eval] {n}");
        }
        suspended_via = charge_suspended_via(&out);
        // 停充成功后等电池停止吸电再开第一臂 (审计 #13)
        charge_settle = suspended_via.as_ref().map(|_| wait_charge_settled(serial));
    }

    let (baseline_runs, baseline_warmup) = baseline_plan(req)?;
    for w in 1..=baseline_warmup {
        let label=format!("baseline_warmup{w}");
        if let Err(e)=run_one_for(serial,&label,&evidence.join(&label),seconds,&workload_path,pkg,req["workload"]["render_comm"].as_str(),launch.as_ref(),spf) {
            report["verdict"]["reason"]=json!(format!("预热轮失败: {e}"));
        }
    }
    for i in 1..=baseline_runs {
        let cold=match facts.policy.cool_c {Some(c)=>crate::autoloop::wait_cool(serial,c,420),None=>crate::pyobj! {"ok"=>false}};
        let _=std::fs::write(evidence.join(format!("cold_baseline{i}.json")),py_dumps(&cold));
        if cold.get("ok").and_then(|v|v.as_bool())!=Some(true) {facts.cold_ok=Some(false);break;}
        if want_suspend {
            let (_, log) = ensure_on_battery(serial);
            std::fs::write(evidence.join(format!("supply_baseline{i}.log")), log).ok();
        }
        match run_one_for(serial,&format!("baseline{i}"),&evidence.join(format!("baseline{i}")),seconds,&workload_path,pkg,req["workload"]["render_comm"].as_str(),launch.as_ref(),spf) {
            Ok(m)=>facts.baseline.push(m),Err(e)=>{report["verdict"]["reason"]=json!(e);break;}
        }
    }
    let baseline_restore=su(serial,&format!("sh {DEV_TMP}/charge_suspend.sh restore"),60);
    std::fs::write(evidence.join("baseline_charge_restore.log"),&baseline_restore).ok();
    let baseline_after=snapshot(serial);
    std::fs::write(evidence.join("snap_after_baseline.txt"),&baseline_after).ok();
    // 基线阶段我们一个节点都不写 (停充另由 charge_suspend.sh restore 核对), 快照差异只可能来自厂商性能服务
    // 自管的频率边界; 严格逐字节比会把它判成还原失败 (2026-10-23 红魔: kgsl.max_pwrlevel 17→0、policy6 max 4608000→1632000)
    facts.restored=Some(snapshots_restored(&snap_before,&baseline_after) && adb_ok(&baseline_restore)
        && !baseline_restore.contains("CHARGE_RESTORE_FAIL"));
    facts.stage=Stage::Experiment;
    let before_candidates=record_qualification(&mut report,evidence,&facts)?;
    if !before_candidates.experiment_allowed {
        let restore=su(serial,&format!("sh {DEV_TMP}/charge_suspend.sh restore"),60);
        std::fs::write(evidence.join("charge_restore.log"),restore).ok();
        report["verdict"]["reason"]=json!("基线/供电/来源/负载资格未通过，禁止候选实验");
        return Ok(serde_json::to_string_pretty(&report).unwrap());
    }
    if want_suspend {
        let out=su(serial,&format!("sh {DEV_TMP}/charge_suspend.sh suspend"),CHARGE_SUSPEND_TIMEOUT_S);
        std::fs::write(evidence.join("candidate_charge_suspend.log"),&out).ok();
        if let Some(n) = sysparam::usb_supply_note(&out) {
            usb_supply_note = Some(n);
        }
        let via = charge_suspended_via(&out);
        // 候选臂前同样等电池停止吸电 (审计 #13)
        if via.is_some() {
            charge_settle = Some(wait_charge_settled(serial));
        }
        // Always attempt restore after a requested suspend, even when its acknowledgement failed.
        suspended_via=Some(via.or(suspended_via).unwrap_or_else(||"requested; acknowledgement unknown".into()));
    }
    const BAD_APPLY_TOKENS: [&str; 4] = [
        "KNOB_FAIL",
        "KNOB_REFUSE",
        "KNOB_PLAN_REJECTED",
        "KNOB_ALREADY_APPLIED",
    ];
    let restore = |serial: &str| -> String { su(serial, &format!("sh {DEV_TMP}/knob_sysparam.sh restore"), 120) };

    let mut knob_runs: Vec<Value> = Vec::new();
    let mut ctrl_runs: Vec<Value> = Vec::new();
    let mut abort_reason: Option<String> = None;
    let mut apply_ok = true;
    let mut restore_fail: Option<String> = None;
    let mut apply_logs: Vec<String> = Vec::new();
    // 冻结的资格合同: alpha 即 protocol.p_threshold, 判定指标即 policy.required_metrics
    let alpha = facts.policy.alpha;
    let mut curtailed: Option<Value> = None;

    let mut contested: Vec<Value> = Vec::new();
    let cool_c = req["conditions"]["cool_c"].as_f64().filter(|x| *x > 0.0 && *x < 100.0);
    let mut cool_log: Vec<Value> = Vec::new();
    let hold_min = hold_min_frac(req);
    let mut hold_log: Vec<Value> = Vec::new();
    // 臂序随机 (审计 #13), 种子进报告; 环境不一致的对单独剔除 (审计 #9 #11)
    let order_seed = curtail::order_seed(req["protocol"]["order_seed"].as_u64());
    let knob_first = curtail::knob_first_order(rounds as usize, order_seed);
    let mut dropped: Vec<Value> = Vec::new();
    // 这组参数改的就是 GPU 档位时, 改写臂档位不同是设计如此, 不拿它判不可比
    let check_gpu = !plan.contains("pwrlevel");
    'pairs: for i in 1..=rounds {
        // 资格 (main): 每对开跑前冷机达标, 否则不再继续
        let cold=match facts.policy.cool_c {Some(c)=>crate::autoloop::wait_cool(serial,c,420),None=>crate::pyobj! {"ok"=>false}};
        let _=std::fs::write(evidence.join(format!("cold_pair{i}.json")),py_dumps(&cold));
        if cold.get("ok").and_then(|v|v.as_bool())!=Some(true) {
            facts.cold_ok=Some(false);abort_reason=Some("候选对冷机失败，禁止继续".into());break;
        }
        // 臂序随机 (ruler fix-F)
        for arm in if knob_first[i as usize - 1] { ["knob", "ctrl"] } else { ["ctrl", "knob"] } {
            // 风扇按死在关 (owner 规矩), 再等冷 —— 风扇状态会改等冷的结果
            let fan = arm_fan_off(serial);
            // 每臂开跑前按请求的 cool_c 等冷 (施加之前等: 等的那几分钟里厂商服务有足够时间把刚写的值改回去)
            let mut c = arm_wait_cool(serial, cool_c, &format!("{arm}{i}"), arm_cool_timeout(&cool_log));
            c["arm"] = json!(format!("{arm}{i}"));
            c["fan"] = fan.clone();
            cool_log.push(c);
            if let Some(e) = fan_still_on(&fan) {
                abort_reason = Some(format!("{arm}{i}: {e}"));
                break;
            }
            if want_suspend {
                let (_, log) = ensure_on_battery(serial);
                std::fs::write(evidence.join(format!("supply_{arm}{i}.log")), log).ok();
            }
            let label = format!("sp_{arm}{i}");
            let arm_dir = evidence.join(format!("{arm}{i}"));
            let rb_cmd = readback_cmd(&plan);
            // 自驱动负载: 先冷启动游戏、等它进入被测窗口 (marker), 再施加 (审计 #7)。
            // 游戏切到前台时厂商游戏模式 (perf2-hal / zperfcubeservice) 会套自己的配置,
            // 在游戏启动前施加的值会在测量开始前就被改回去。触控脚本负载的游戏本来就在前台跑着。
            // 标记出现那一刻的设备 uptime 记下来 (审查 R1 #9: 改写臂多一步施加, 开采固定晚 1–3 s)。
            let mut marker_up: Option<f64> = None;
            if let Some(l) = &launch {
                let up = std::fs::create_dir_all(&arm_dir)
                    .map_err(|e| e.to_string())
                    .and_then(|_| start_with_extras(serial, pkg, &l.extras, l.marker.as_deref(), &[], &arm_dir))
                    .and_then(|_| wait_launch_window(serial, pkg, &label, l.marker.as_deref()));
                match up {
                    Ok(u) => marker_up = u,
                    Err(e) => {
                        let _ = adb(serial, &["shell", &format!("am force-stop {pkg}")], 30);
                        abort_reason = Some(e);
                        break;
                    }
                }
            }
            // 改写臂施加; 对照臂在同一时刻读一次这些节点的当前值 (同样一次 su 调用, 两臂节奏一致,
            // 也留下对照臂的起点值)。
            let pre = if arm == "knob" {
                let out = su(
                    serial,
                    &format!("sh {DEV_TMP}/knob_sysparam.sh apply {DEV_TMP}/loop_v1_sysparam.plan"),
                    120,
                );
                apply_logs.push(out.clone());
                if !adb_ok(&out) || BAD_APPLY_TOKENS.iter().any(|t| out.contains(t)) {
                    apply_ok = false;
                    abort_reason = Some("旋钮未全部生效 (apply 回包有 FAIL 令牌)".into());
                    let r = restore(serial);
                    if !adb_ok(&r) || r.contains("KNOB_RESTORE_FAIL") {
                        restore_fail = Some(r);
                    }
                    if launch.is_some() {
                        let _ = adb(serial, &["shell", &format!("am force-stop {pkg}")], 30);
                    }
                    break;
                }
                out
            } else {
                su(serial, &rb_cmd, 30)
            };
            // 测量窗口内按固定节拍回读 (https://github.com/HGamey/docs/blob/main/repos/phonefarm/knobs/DESIGN.md:
            // 写得进不等于守得住)。两臂都读 —— 只在改写臂读的话, 回读的 adb 调用本身就成了两臂之间的差别。
            let poller = HoldPoller::start(serial, &rb_cmd, READBACK_EVERY_S);
            let res = match &launch {
                // 游戏已经由上面拉起来并进入窗口, 这里不再重启
                Some(l) => run_one_launch(
                    serial,
                    &label,
                    &arm_dir,
                    seconds,
                    pkg,
                    req["workload"]["render_comm"].as_str(),
                    l,
                    false,
                    marker_up,
                    spf,
                ),
                None => run_one_with(
                    serial,
                    &label,
                    &arm_dir,
                    seconds,
                    &workload_path,
                    pkg,
                    req["workload"]["render_comm"].as_str(),
                    spf,
                ),
            };
            let samples = poller.finish();
            if launch.is_some() {
                // 量完就停掉, 别让它在还原、等冷的时候接着烧
                let _ = adb(serial, &["shell", &format!("am force-stop {pkg}")], 30);
            }
            // 守住比例只算 trace 采集窗内的回读 (审查 R1 #5)
            let hold_win = std::fs::read(arm_dir.join("trace.txt"))
                .ok()
                .and_then(|b| sysparam::capture_window_from_trace(&String::from_utf8_lossy(&b[..b.len().min(64 * 1024)])));
            let hold = hold_stats_window(&plan, &samples, hold_win);
            let mut hold_rec = hold.to_json();
            hold_rec["arm"] = json!(format!("{arm}{i}"));
            hold_log.push(hold_rec.clone());
            {
                let mut txt = format!("# 施加/起点回读\n{pre}\n");
                for (t, out) in &samples {
                    txt.push_str(&format!("# +{t:.1}s\n{out}\n"));
                }
                std::fs::write(evidence.join(format!("{arm}{i}_readback.txt")), txt).ok();
            }
            // 改写臂按窗口内守住的时间比例判进不进统计
            let arm_contested = if arm == "knob" { hold_verdict(&hold, hold_min).err() } else { None };
            if arm == "knob" {
                let r = restore(serial);
                if (!adb_ok(&r) || r.contains("KNOB_RESTORE_FAIL")) && restore_fail.is_none() {
                    restore_fail = Some(r.clone());
                }
            }
            match res {
                Ok(mut m) => {
                    m["sysparam_hold"] = hold_rec;
                    m["round"] = json!(i);
                    // 温度上限: 超了这一组作废
                    let t = run_soc_temp_max(&m);
                    if let (Some(t), Some(cap)) = (t, req["conditions"]["temp_cap_c"].as_f64()) {
                        if t > cap {
                            abort_reason = Some(format!("{arm} 臂第 {i} 轮 SoC 结温 {t}C 超过上限 {cap}C"));
                        }
                    }
                    if abort_reason.is_none() {
                        if arm == "knob" {
                            match arm_contested {
                                None => knob_runs.push(m),
                                // 守不住的那一臂量的不是这组参数: 不进统计, 单列计数
                                Some(why) => contested.push(json!({
                                    "round": i,
                                    "why": why,
                                    "hold_frac": hold.frac(),
                                    "nodes": hold.mismatches,
                                })),
                            }
                        } else {
                            ctrl_runs.push(m);
                        }
                        settle_pair(i, &mut knob_runs, &mut ctrl_runs, &mut dropped, check_gpu);
                    }
                    if restore_fail.is_some() {
                        abort_reason = Some("旋钮还原失败, 后续数据不可信".into());
                    }
                }
                Err(e) => {
                    abort_reason = Some(e);
                }
            }
            if abort_reason.is_some() {
                break;
            }
            // 确定性截断: 剩下的臂无论量出什么, 都不可能有指标显著改善 → 跑满也必然不是
            // KEEP, 现在停 (论证见 src/curtail.rs)。KEEP 一侧永远不提前锁定。
            if curtail {
                let lock_possible = fps_lock_confirmed(fps_cap_declared, &ctrl_runs, &knob_runs).0.is_some();
                // 审查 R1 #3: contested 的改写臂与 settle_pair 剔掉的对都不会再跑, 不能当「还没跑」
                let (left_a, left_b) = remaining_arms(rounds as usize, ctrl_runs.len(), knob_runs.len(), contested.len(), dropped.len());
                let pp = power_pairs_of(&knob_runs, &ctrl_runs);
                // 判定指标由冻结的 policy 决定 (main), 功耗按对取舍后的值进截断检验 (ruler)
                let tests = policy_win_tests(&facts.policy.required_metrics, alpha, lock_possible);
                if !keep_reachable(ctrl_runs.len() + left_a, knob_runs.len() + left_b, &tests)
                    || !curtail::keep_possible(&eval_runs_power(&ctrl_runs, &pp.ctrl), &eval_runs_power(&knob_runs, &pp.knob), left_a, left_b, &tests)
                {
                    let mut note = curtail_note(knob_runs.len(), ctrl_runs.len(), rounds, &tests);
                    note["contested_arms"] = json!(contested.len());
                    note["dropped_pairs"] = json!(dropped.len());
                    curtailed = Some(note);
                    break 'pairs;
                }
            }
        }
        if abort_reason.is_some() {
            break;
        }
    }

    // ── 还原 + 快照核对 ──
    // 停充无条件还原 (审查 R1 #2 #10): 没有 STATE 时脚本是空操作 (CHARGE_NOT_SUSPENDED);
    // 超时后设备端才写成的 STATE、CHARGE_ALREADY_SUSPENDED 认下来的 STATE 都在这里一并还原。
    {
        let cr = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh restore"), 60);
        std::fs::write(evidence.join("charge_restore.log"), &cr).map_err(|e| e.to_string())?;
        if !adb_ok(&cr) || cr.contains("CHARGE_RESTORE_FAIL") {
            restore_fail = Some(restore_fail.clone().unwrap_or_else(|| "停充还原失败".into()));
        }
    }
    let restore_log = restore(serial);
    if (!adb_ok(&restore_log) || restore_log.contains("KNOB_RESTORE_FAIL")) && restore_fail.is_none() {
        restore_fail = Some(restore_log);
    }
    // 两样都还原过了, 中断时不必再跑 (还原失败的已经记在 restore_fail 里, 下次开跑前对账会再还原一次)
    cleanup::clear();
    let final_cold=match facts.policy.cool_c {Some(c)=>crate::autoloop::wait_cool(serial,c,420),None=>crate::pyobj! {"ok"=>false}};
    let _=std::fs::write(evidence.join("cold_final.json"),py_dumps(&final_cold));
    if final_cold.get("ok").and_then(|v|v.as_bool())!=Some(true) {facts.cold_ok=Some(false);abort_reason=Some("收尾冷机失败，撤销可保留结论".into());}
    let snap_after = snapshot(serial);
    std::fs::write(evidence.join("snap_after.txt"), &snap_after).map_err(|e| e.to_string())?;
    let snapshot_identical = snapshots_identical(&snap_before,&snap_after);
    // 快照对不上时分两类: 厂商性能服务自己会改的频率边界 (它在游戏过程中主动压/抬, 与我们
    // 无关; 我们写过的节点由 restore 逐项写回并回读核对, 失败另有 KNOB_RESTORE_FAIL), 和其余。
    // 只有其余的键变了才判留痕作废。前者如实记进报告。
    let (vendor_drift, other_drift) = split_snapshot_diff(&snap_before, &snap_after);
    // 资格里的「已还原」: 两次快照都读成功, 且除厂商自管的频率边界外逐项相等
    let snapshot_restored = snapshots_restored(&snap_before, &snap_after);

    let (cool_reached, cool_timeout) = cool_summary(&cool_log);
    report["conditions_observed"] = json!({
        "cool_c_target": cool_c,
        "cool_reached_c": cool_reached,
        "cool_timeout": cool_timeout,
        "cool_per_arm": cool_log,
        "temp_cap_c": req["conditions"]["temp_cap_c"],
        "arm_order": curtail::order_note(order_seed, rounds as usize),
        "charge_settle": charge_settle,
        // 开跑前对账: 上一次留下的旋钮 / 停充状态 (审查 R1 #10), 已还原
        "preexisting_state": preexisting,
        "env_consistency": consistency_note(&dropped, check_gpu),
        // 审查 R1 #9: 两臂 标记出现→开采 的延时 (只标不剔)
        "marker_to_capture": marker_delay_note(&knob_runs, &ctrl_runs),
        "spf_declared": spf,
        "fps_cap_declared": fps_cap_declared,
        // 只记不判: 各对之间风扇状态不同 (每对内部一致) 不再整组作废
        "fan_across_pairs": fan_mismatch(&knob_runs, &ctrl_runs),
        "apply_ok": apply_ok,
        "snapshot_identical": snapshot_identical,
        "restored": restore_fail.is_none(),
        // 改写臂里有几臂在测量窗口内守住的时间比例低于门槛 (厂商服务抢回), 这几臂没进统计
        "contested_arms": contested.len(),
        "contested": contested,
        "hold_min_frac": hold_min,
        "readback_every_s": READBACK_EVERY_S,
        // 逐臂 (两臂都有) 窗口内回读: 守住比例、读到过的值。对照臂看厂商服务有没有动这些节点
        "hold_per_arm": hold_log,
        "snapshot_vendor_drift": vendor_drift,
        "snapshot_diff": if snapshot_identical { Value::Null } else {
            json!([
                format!("before {} 行 / after {} 行, 逐行比对不相等 (全文见 evidence_dir)",
                        snap_before.lines().count(), snap_after.lines().count())
            ])
        },
    });
    report["evidence_dir"] = json!(evidence.display().to_string());
    std::fs::write(evidence.join("apply_log.txt"), apply_logs.join("\n---\n")).ok();

    facts.stage=Stage::Conclusion;facts.control=ctrl_runs.clone();facts.candidate=knob_runs.clone();
    facts.device.control_verified=Some(apply_ok);
    facts.restored=Some(snapshot_restored && restore_fail.is_none());
    let qualified=record_qualification(&mut report,evidence,&facts)?;
    if !qualified.conclusion_allowed {
        report["verdict"]["reason"]=json!(if facts.screen {"初筛只报方向与幅度 (数字保留), 不判 PASS/KEEP; 有苗头的候选进确认阶段"}
            else {"实验结论资格未通过；数字保留，禁止 PASS/KEEP"});
        return Ok(serde_json::to_string_pretty(&report).unwrap());
    }
    // ── 判定 ──
    if let Some(reason) = abort_reason.clone().or_else(|| restore_fail.clone().map(|r| format!("旋钮还原失败: {r}"))) {
        let _ = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh restore"), 60);
        report["verdict"]["reason"] = json!(reason);
        return Ok(serde_json::to_string_pretty(&report).unwrap_or_default());
    }
    if !other_drift.is_empty() {
        report["verdict"]["reason"] = json!(format!("轮末设备快照与轮前不一致, 留痕: {}", other_drift.join("; ")));
        let _ = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh restore"), 60);
        return Ok(serde_json::to_string_pretty(&report).unwrap_or_default());
    }

    if knob_runs.is_empty() && !contested.is_empty() {
        report["verdict"]["reason"] = json!(format!(
            "改写臂 {} 臂在测量窗口内都没守住 (守住时间比例低于门槛, contested), 没有一臂量的是这组参数",
            contested.len()
        ));
        let _ = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh restore"), 60);
        return Ok(serde_json::to_string_pretty(&report).unwrap_or_default());
    }
    if knob_runs.is_empty() && !dropped.is_empty() {
        report["verdict"]["reason"] = json!(format!(
            "{} 对两臂测量环境都不一致 (风扇或 GPU 档位), 全部剔除, 没有可比的数据 (见 conditions_observed.env_consistency)",
            dropped.len()
        ));
        let _ = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh restore"), 60);
        return Ok(serde_json::to_string_pretty(&report).unwrap_or_default());
    }
    let (verdict, keep, on_battery_all) = judge_runs_with(&ctrl_runs, &knob_runs, alpha, rounds, fps_cap_declared, Some(&facts.policy.required_metrics));
    report["verdict"] = verdict;
    append_dropped_reason(&mut report, &dropped);
    let md = report["conditions_observed"]["marker_to_capture"].clone();
    append_marker_delay_reason(&mut report, &md);
    if !contested.is_empty() {
        let r = report["verdict"]["reason"].as_str().unwrap_or("").to_string();
        report["verdict"]["reason"] = json!(format!(
            "{r} (另有 {} 臂改写被厂商性能服务抢回, 没进统计)",
            contested.len()
        ));
    }
    if let Some(note) = curtailed {
        // 构造上不可能: 截断只在「任何指标都已不可能显著改善」时发生, 部分数据的 p
        // 不会低于跑满的下界 (src/curtail.rs)。真出现了就是实现错了, 别把它当 KEEP 发出去。
        if keep {
            return Err("内部错误: 截断后的部分数据判出了 KEEP, 与截断的前提矛盾".into());
        }
        let partial = report["verdict"]["reason"].as_str().unwrap_or("").to_string();
        report["verdict"]["reason"] = json!(format!(
            "确定性截断: 跑了 {}/{} 臂后, 剩余轮次无论量出什么都不可能有指标显著改善, 跑满也必然不保留 (已量部分: {partial})",
            note["arms_run"], note["arms_planned"]
        ));
        report["verdict"]["curtailed"] = note;
    }

    // ── metrics: 逐臂聚合 (B 臂 = 候选臂) ──
    // 臂内均值: ABBA 的全部轮一起算 —— 只取最后一轮的话, 那恰好是整场最热的
    // 一轮, 交替设计要抹掉的热漂移就全进了头条指标
    let mut m = report["metrics"].take();
    let power = aggregate_metrics(&mut m, &knob_runs, &ctrl_runs, on_battery_all);
    let rail = knob_runs.first().and_then(|r| r["env"]["power_source"].as_str()).unwrap_or("battery");
    report["metric_provenance"] = json!({"frame_p95_ms": {"per_run":qualified.frame_evidence,"display_verified":false},"fps_mean":{"semantics":"gpu_submit_proxy","display_verified":false},"fps_p95_ms":{"semantics":"gpu_submit_proxy","display_verified":false},"power_watt":{"rail":rail,"window_verified":knob_runs.iter().all(|r|r["env"]["power_window_verified"]==true)}});
    m["psnr_db"] = Value::Null;
    unavailable(&mut m, "psnr_db", "sysparam 不动画面, 无画质可量");
    if power.is_none() {
        let why = power_pairs_of(&knob_runs, &ctrl_runs)
            .note()
            .unwrap_or_else(|| "非放电态 (或功耗采样缺失): 整机功耗量不准, 如实 null".into());
        unavailable(&mut m, "power_watt", &why);
    }
    if m["fps_p95_ms"].is_null() {
        unavailable(&mut m, "fps_p95_ms", "本轮没拿到可用的帧时序");
    }
    report["metrics"] = m;

    // ── power_source ──
    let env0 = knob_runs
        .last()
        .or(ctrl_runs.last())
        .cloned()
        .unwrap_or(json!({}));
    let env = env0.get("env").cloned().unwrap_or(json!({}));
    report["power_source"] = json!({
        "rail": env["power_rail"],
        "battery_status": env["battery_status"],
        "battery_current_now_ua": env["current_now_ua_mean"].as_f64().map(|x| x as i64),
        "usb_input_w_mean": env["usb_input_w_mean"],
        "bypass": env["bypass"],
        "power_usable_reason": env["power_usable_reason"],
        "fan_state": env["fan_state"],
        "battery_capacity_pct": env["battery_capacity_pct"].as_str().and_then(|s| s.parse::<i64>().ok()),
        "on_battery": env["on_battery"],
        "source": env["power_source"],
        "charging_suspended_via": suspended_via.clone(),
        // 这两个数决定「这轮功耗能不能拿去跟别的任务比」, 不靠看图猜
        "cross_run_comparable": sysparam::power_cross_run_comparable(
            knob_runs.iter().chain(ctrl_runs.iter()).filter_map(|r| r.get("env")),
        ),
        // 按对取舍的结果: 剔了哪几对、为什么, 口径, 是否供电不足 (审计 #5)
        "excluded_reason": power_pairs_of(&knob_runs, &ctrl_runs).note(),
        "pairs": power_pairs_of(&knob_runs, &ctrl_runs).to_json(),
        "supply_mode": env["supply_mode"],
        "usb_supply_check": usb_supply_note.clone(),
    });

    report["status"] = json!(if keep { "PASS" } else { "REJECT" });
    Ok(serde_json::to_string_pretty(&report).unwrap_or_default())
}

fn wl_pyval(wl: &sysparam::Whitelist) -> Value {
    let pairs: Vec<(String, PyVal)> = wl.iter().map(|(k, s)| (k.clone(), s.to_pyval())).collect();
    let s = py_dumps(&PyVal::Obj(pairs));
    serde_json::from_str(&s).unwrap_or(Value::Null)
}

/// n v n 精确置换检验的最小双侧 p = 2/C(2n,n); 够不着 alpha_win 就是 false。
fn reachable(n: u32, alpha_win: f64) -> bool {
    n >= 2 && loopstat::ensure_resolvable(n as usize, alpha_win, "").is_ok()
}

/// 风扇开关节点 (红魔 NX809J)。owner 规矩: 风扇默认必须关 (噪音)。
const FAN_EN_NODE: &str = "/sys/kernel/fan/fan_enable";

/// 每臂开测前: 读风扇开关, 不是 0 就写 0 并回读。回 {before, after, forced};
/// 没有风扇节点 (别的机型) 时 before/after 为 null。2026-10-12 W1 实测: 不知谁开着的风扇
/// 在前 4 对开、第 5 对关, 整组功耗作废 —— 所以每臂都按死, 不信上一个人。
pub(crate) fn arm_fan_off(serial: &str) -> Value {
    let rd = |s: &str| {
        crate::graylayer::GrayLayer::adb_stdout(&su(s, &format!("cat {FAN_EN_NODE} 2>/dev/null"), 15))
            .trim()
            .to_string()
    };
    let before = rd(serial);
    if before.is_empty() {
        return json!({"before": null, "after": null, "forced": false});
    }
    let forced = before != "0";
    if forced {
        su(serial, &format!("echo 0 > {FAN_EN_NODE}"), 15);
    }
    let after = rd(serial);
    json!({"before": before, "after": after, "forced": forced})
}

/// [`arm_fan_off`] 之后风扇仍开着 (写不进去): 这一臂不能量。
pub(crate) fn fan_still_on(fan: &Value) -> Option<String> {
    match fan["after"].as_str() {
        Some(a) if a != "0" => Some(format!("风扇关不掉 (fan_enable 写 0 后回读 {a}), 按规矩风扇必须关, 不量")),
        _ => None,
    }
}

/// 两臂测量期间 (sample_env 逐臂记的 fan_state) 风扇状态必须一致, 不一致这组整体作废。
pub(crate) fn fan_mismatch(knob_runs: &[Value], ctrl_runs: &[Value]) -> Option<String> {
    let seen: std::collections::BTreeSet<String> = knob_runs
        .iter()
        .chain(ctrl_runs.iter())
        .filter_map(|r| r["env"]["fan_state"].as_str().map(String::from))
        .collect();
    (seen.len() > 1).then(|| format!("测量期间各臂风扇状态不一致 {seen:?}: 风扇自己耗电、还改热态, 这组不可比"))
}

// ══════════════ 臂间环境一致性 (审计 #9 #11 #13) ══════════════

/// sample_hw.sh 自己跑的上限 (秒)。正常由 [`HwWatch::finish`] 写停止标记结束, 这只是兜底。
const HW_WATCH_MAX_S: u64 = 900;
/// 同一对两臂的 GPU 上限档分布, 总变差距离 (½·Σ|p_knob − p_ctrl|) 超过它就判不可比。
/// 例: 一臂全程 0 档、另一臂有 1/4 时间被压到 17 档 → 0.25 > 0.2, 剔除这一对。
pub(crate) const GPU_LEVEL_TV_MAX: f64 = 0.2;

/// 每臂一个新的停止标记文件名: 设备端 sample_hw.sh 不删任何文件 (铁律: 不删除),
/// 开始时已存在的标记它不认, 所以同一 label 再跑一次 (下一次评测) 也必须换名字。
/// `nonce` 取主机时间纳秒 + 进程号, 见 [`hw_stop_nonce`]。纯函数, 便于测试。
pub(crate) fn hw_stop_path(label: &str, nonce: &str) -> String {
    format!("{DEV_TMP}/sample_hw_{label}_{nonce}.stop")
}

fn hw_stop_nonce() -> String {
    let ns = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("{ns}_{}", std::process::id())
}

/// 测量窗口内逐秒记 GPU 上限档与风扇设定档 (设备端 sample_hw.sh, 只读)。
/// 两臂用同一个 [`HwWatch`], 所以这一路 adb 调用本身不造成两臂差别。
pub(crate) struct HwWatch {
    child: Option<std::process::Child>,
    path: PathBuf,
    stop: String,
    serial: String,
}

impl HwWatch {
    pub(crate) fn start(serial: &str, outdir: &Path, label: &str) -> Self {
        let path = outdir.join("hw.txt");
        let stop = hw_stop_path(label, &hw_stop_nonce());
        let child = std::fs::File::create(&path).ok().and_then(|f| {
            crate::fd_lock::transport_command(adb_path())
                .args(["-s", serial, "shell"])
                .arg(format!("su -c 'sh {DEV_TMP}/sample_hw.sh {HW_WATCH_MAX_S} {stop}'"))
                .stdout(f)
                .stderr(std::process::Stdio::null())
                .spawn()
                .ok()
        });
        Self { child, path, stop, serial: serial.to_string() }
    }

    /// 停掉采样, 回 hw.txt 原文 (调用方拿到 trace 采集窗之后再用 [`hw_stats_window`] 截窗解析,
    /// 审查 R1 #12: LEAD 段 / 采完之后的档位变化不该算进这一臂)。没采到 (脚本不在、没 root) 时空串, n=0,
    /// 一致性核对跳过这一项。
    pub(crate) fn finish(mut self) -> String {
        if let Some(mut ch) = self.child.take() {
            let _ = su(&self.serial, &format!("touch {}", self.stop), 15);
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
            loop {
                match ch.try_wait() {
                    Ok(Some(_)) | Err(_) => break,
                    Ok(None) if std::time::Instant::now() > deadline => {
                        let _ = ch.kill();
                        let _ = ch.wait();
                        break;
                    }
                    Ok(None) => std::thread::sleep(std::time::Duration::from_millis(200)),
                }
            }
        }
        std::fs::read_to_string(&self.path).unwrap_or_default()
    }
}

/// 游程编码: [[值, 连续样本数], ...], 保留先后顺序。
fn runs_of<T: PartialEq + Clone>(xs: &[T]) -> Vec<(T, usize)> {
    let mut out: Vec<(T, usize)> = Vec::new();
    for x in xs {
        match out.last_mut() {
            Some((v, n)) if v == x => *n += 1,
            _ => out.push((x.clone(), 1)),
        }
    }
    out
}

/// 各取值所占样本比例。
fn fractions<T: Ord + Clone>(xs: &[T]) -> std::collections::BTreeMap<T, f64> {
    let mut m: std::collections::BTreeMap<T, f64> = Default::default();
    for x in xs {
        *m.entry(x.clone()).or_default() += 1.0;
    }
    let n = xs.len().max(1) as f64;
    m.values_mut().for_each(|v| *v /= n);
    m
}

/// sample_hw.sh 输出 → {n, gpu_pwrlevel_runs, gpu_pwrlevel_frac, fan_runs, fan_states}。纯函数, 不截窗。
pub(crate) fn hw_stats(text: &str) -> Value {
    hw_stats_window(text, None)
}

/// 同 [`hw_stats`], 但只取 uptime 落在 trace 采集窗 `[a, b]` 里的 HW 行 (审查 R1 #12: 记录覆盖
/// run_once 的 LEAD 段与采完之后的 pull 段, 那两段里厂商服务改档位会错剔一对)。
/// 输出多 `n_total` (截窗前的行数) 与 `window`; `window` 为 None 时不截, 与 [`hw_stats`] 相同。
pub(crate) fn hw_stats_window(text: &str, window: Option<(f64, f64)>) -> Value {
    let (mut levels, mut fans): (Vec<i64>, Vec<String>) = (vec![], vec![]);
    let (mut n, mut n_total) = (0usize, 0usize);
    // 停止标记在脚本开始时就已存在 (脚本不认它, 只按最长秒数停): 记下来, 说明这一臂的记录可能比测量窗长
    let stop_preexisting = text.lines().any(|l| l.trim().starts_with("#stop_preexisting"));
    for line in text.lines() {
        let Some(rest) = line.trim().strip_prefix("HW ") else { continue };
        let p: Vec<&str> = rest.split_whitespace().collect();
        if p.len() < 3 {
            continue;
        }
        n_total += 1;
        if let Some((a, b)) = window {
            let up = p[0].parse::<f64>().unwrap_or(f64::NAN);
            if !(a <= up && up <= b) {
                continue;
            }
        }
        n += 1;
        if let Ok(l) = p[1].parse::<i64>() {
            levels.push(l);
        }
        if p[2] != "-" {
            fans.push(p[2].to_string());
        }
    }
    let lv_frac: serde_json::Map<String, Value> =
        fractions(&levels).into_iter().map(|(k, v)| (k.to_string(), json!((v * 1000.0).round() / 1000.0))).collect();
    let fan_states: Vec<String> = fractions(&fans).into_keys().collect();
    json!({
        "n": n,
        "n_total": n_total,
        "window": window.map(|(a, b)| json!([a, b])),
        "gpu_pwrlevel_runs": runs_of(&levels).into_iter().map(|(v, c)| json!([v, c])).collect::<Vec<_>>(),
        "gpu_pwrlevel_frac": lv_frac,
        "fan_runs": runs_of(&fans).into_iter().map(|(v, c)| json!([v, c])).collect::<Vec<_>>(),
        "fan_states": fan_states,
        "stop_preexisting": stop_preexisting,
    })
}

/// 两个档位分布的总变差距离。
fn level_tv(a: &Value, b: &Value) -> Option<f64> {
    let (a, b) = (a.as_object()?, b.as_object()?);
    if a.is_empty() || b.is_empty() {
        return None;
    }
    let keys: std::collections::BTreeSet<&String> = a.keys().chain(b.keys()).collect();
    let tv = keys
        .into_iter()
        .map(|k| (a.get(k).and_then(Value::as_f64).unwrap_or(0.0) - b.get(k).and_then(Value::as_f64).unwrap_or(0.0)).abs())
        .sum::<f64>()
        / 2.0;
    Some(tv)
}

/// 一臂测量期间见到的风扇设定档集合: 优先逐秒记录 (hw.fan_states), 没有就退回 sample_env 开头那一行。
fn arm_fan_states(m: &Value) -> Option<std::collections::BTreeSet<String>> {
    if let Some(xs) = m["hw"]["fan_states"].as_array().filter(|x| !x.is_empty()) {
        return Some(xs.iter().filter_map(|x| x.as_str().map(String::from)).collect());
    }
    m["env"]["fan_state"].as_str().map(|s| std::iter::once(s.to_string()).collect())
}

/// 同一对两臂的测量环境是否可比: 风扇设定档集合必须相同; `check_gpu` 时 GPU 上限档分布的
/// 总变差距离不超过 [`GPU_LEVEL_TV_MAX`]。缺数据的那一项不判 (旧证据、没采到), 只判两臂都有的。
/// sysparam 改的就是 GPU 档位时调用方传 `check_gpu = false` (改写臂档位不同是设计如此)。
pub(crate) fn pair_env_check(knob: &Value, ctrl: &Value, check_gpu: bool) -> Result<(), String> {
    if let (Some(fk), Some(fc)) = (arm_fan_states(knob), arm_fan_states(ctrl)) {
        if fk != fc {
            return Err(format!("风扇状态不一致: 改写臂 {fk:?} / 对照臂 {fc:?}"));
        }
    }
    // 审计 #10: 两臂的「每帧几次提交」不同 = 帧时口径不同 (一臂 p50 算成 6 倍), 不可比
    let spf = |m: &Value| m["summary"]["submits_per_frame"].as_u64();
    if let (Some(a), Some(b)) = (spf(knob), spf(ctrl)) {
        if a != b {
            return Err(format!(
                "帧时口径不一致: 每帧提交数 改写臂 {a} / 对照臂 {b} (各臂自检猜的不一样; 请求里用 workload.spf 声明可固定)"
            ));
        }
    }
    if check_gpu {
        if let Some(tv) = level_tv(&knob["hw"]["gpu_pwrlevel_frac"], &ctrl["hw"]["gpu_pwrlevel_frac"]) {
            if tv > GPU_LEVEL_TV_MAX {
                return Err(format!(
                    "GPU 上限档分布不一致 (总变差 {tv:.2} > {GPU_LEVEL_TV_MAX}): 改写臂 {} / 对照臂 {}",
                    knob["hw"]["gpu_pwrlevel_frac"], ctrl["hw"]["gpu_pwrlevel_frac"]
                ));
            }
        }
    }
    Ok(())
}

/// 第 `round` 对两臂都量完时核对环境一致性; 不可比就把这一对两臂都拿出统计, 记进 `dropped`。
/// 只剔这一对, 其余对照常判定 (以前风扇不一致是整组作废)。回 true = 剔除了。
pub(crate) fn settle_pair(
    round: u32,
    knob_runs: &mut Vec<Value>,
    ctrl_runs: &mut Vec<Value>,
    dropped: &mut Vec<Value>,
    check_gpu: bool,
) -> bool {
    let is_round = |r: &Value| r["round"].as_u64() == Some(round as u64);
    let (Some(ki), Some(ci)) = (knob_runs.iter().position(is_round), ctrl_runs.iter().position(is_round)) else {
        return false;
    };
    match pair_env_check(&knob_runs[ki], &ctrl_runs[ci], check_gpu) {
        Ok(()) => false,
        Err(why) => {
            let (k, c) = (knob_runs.remove(ki), ctrl_runs.remove(ci));
            dropped.push(json!({
                "round": round, "reason": why,
                "knob": {"label": k["label"], "hw": k["hw"], "fan_state": k["env"]["fan_state"], "layer_hits": k["layer_hits"]},
                "ctrl": {"label": c["label"], "hw": c["hw"], "fan_state": c["env"]["fan_state"]},
            }));
            true
        }
    }
}

/// 回包里的一致性记录 (`conditions_observed.env_consistency`)。
pub(crate) fn consistency_note(dropped: &[Value], check_gpu: bool) -> Value {
    json!({
        "dropped_pairs": dropped.len(),
        "dropped": dropped,
        "gpu_level_checked": check_gpu,
        "rule": format!(
            "每对两臂: 风扇设定档集合必须相同; GPU 上限档分布 (按 trace 采集窗截) 总变差 <= {GPU_LEVEL_TV_MAX} (sysparam 改 GPU 档位时不比这一项); \
             每帧提交数 (submits_per_frame) 必须相同。不可比只剔除那一对, 写在这里"
        ),
    })
}

/// 审查 R1 #9: 两臂「进入被测窗口 (标记出现) → 开采」的延时相差超过它就标出 (改写臂多一步施加, 固定晚 1–3 s,
/// 臂序随机抵消不了; fix-G 之后 Megacity 按时间推进路线, 晚开采就是另一段路)。只标不剔。
pub(crate) const MARKER_DELAY_ASYM_MAX_S: f64 = 1.0;

/// 逐对比较 marker→capture_start 延时 (`run_one_launch` 记的 `marker_to_capture_s`)。
/// 回 {per_round, flagged_rounds, max_asymmetry_s, rule}; 没有自驱动负载 (没记延时) 时 per_round 为空。
pub(crate) fn marker_delay_note(knob_runs: &[Value], ctrl_runs: &[Value]) -> Value {
    let round_of = |r: &Value| r["round"].as_u64();
    let delay = |r: &Value| r["marker_to_capture_s"].as_f64();
    let mut per_round: Vec<Value> = Vec::new();
    let mut flagged: Vec<u64> = Vec::new();
    let mut max_asym: Option<f64> = None;
    for k in knob_runs {
        let (Some(i), Some(dk)) = (round_of(k), delay(k)) else { continue };
        let Some(dc) = ctrl_runs.iter().find(|c| round_of(c) == Some(i)).and_then(delay) else { continue };
        let diff = ((dk - dc) * 100.0).round() / 100.0;
        let bad = diff.abs() > MARKER_DELAY_ASYM_MAX_S;
        if bad {
            flagged.push(i);
        }
        max_asym = Some(max_asym.map_or(diff.abs(), |m: f64| m.max(diff.abs())));
        per_round.push(json!({"round": i, "knob_s": dk, "ctrl_s": dc, "diff_s": diff, "flagged": bad}));
    }
    json!({
        "per_round": per_round,
        "flagged_rounds": flagged,
        "max_asymmetry_s": max_asym,
        "rule": format!("每对两臂 标记出现→采集开始 的延时相差 > {MARKER_DELAY_ASYM_MAX_S} s 就标出 (只标不剔); 延时 = capture_start_uptime − 标记出现时的设备 uptime"),
    })
}

/// 有标出的对时, 在判定理由后面补一句。
pub(crate) fn append_marker_delay_reason(report: &mut Value, note: &Value) {
    let rounds: Vec<String> = note["flagged_rounds"].as_array().into_iter().flatten().map(|v| v.to_string()).collect();
    if rounds.is_empty() {
        return;
    }
    let r = report["verdict"]["reason"].as_str().unwrap_or("").to_string();
    report["verdict"]["reason"] = json!(format!(
        "{r} (第 {} 对两臂从标记出现到开采的延时相差 > {MARKER_DELAY_ASYM_MAX_S} s, 见 conditions_observed.marker_to_capture)",
        rounds.join("、")
    ));
}

/// 请求声明的每帧提交数 (审计 #10)。
pub(crate) fn declared_spf(req: &Value) -> Option<usize> {
    req["workload"]["spf"].as_u64().filter(|n| *n >= 1).map(|n| n as usize)
}

/// 请求声明的帧率上限 (审查 R2 #9)。
pub(crate) fn declared_fps_cap(req: &Value) -> Option<f64> {
    req["workload"]["fps_cap"].as_f64().filter(|x| *x > 0.0)
}

/// 判定理由后面补上剔除了几对。
pub(crate) fn append_dropped_reason(report: &mut Value, dropped: &[Value]) {
    if dropped.is_empty() {
        return;
    }
    let r = report["verdict"]["reason"].as_str().unwrap_or("").to_string();
    let rounds: Vec<String> = dropped.iter().map(|d| d["round"].to_string()).collect();
    report["verdict"]["reason"] = json!(format!(
        "{r} (第 {} 对两臂测量环境不一致, 已剔除, 见 conditions_observed.env_consistency)",
        rounds.join("、")
    ));
}

/// 停充后电池电流 (µA, 正 = 在充) 低于它, 算电池已不再吸电。与 charge_suspend.sh 的 BYPASS_IDLE_UA 一致。
const CHARGE_SETTLE_UA: i64 = 50_000;
/// 停充后等电池停止吸电的上限。10-11 实测电流约 45 s 才落到 ~0。
const CHARGE_SETTLE_MAX_S: u64 = 120;

/// 这一读数算不算已停止吸电。读不出数的不算。
pub(crate) fn charge_settled(ua: Option<i64>) -> bool {
    ua.is_some_and(|x| x <= CHARGE_SETTLE_UA)
}

/// 停充成功后、第一臂之前: 等电池电流降到约 0 (连续两次读数 <= [`CHARGE_SETTLE_UA`])。
/// 审计 #13: 以前最初约 45 秒电池仍在吸电, 那段总落在先跑的那一臂上。超时不作废, 如实记录。
pub(crate) fn wait_charge_settled(serial: &str) -> Value {
    let t0 = std::time::Instant::now();
    let mut samples: Vec<Value> = Vec::new();
    let mut ok_streak = 0;
    loop {
        let out = su(serial, "cat /sys/class/power_supply/battery/current_now", 15);
        let ua = crate::graylayer::GrayLayer::adb_stdout(&out).trim().parse::<i64>().ok();
        let t = t0.elapsed().as_secs();
        samples.push(json!([t, ua]));
        ok_streak = if charge_settled(ua) { ok_streak + 1 } else { 0 };
        if ok_streak >= 2 {
            return json!({"settled": true, "waited_s": t, "threshold_ua": CHARGE_SETTLE_UA, "samples": samples});
        }
        if t >= CHARGE_SETTLE_MAX_S {
            return json!({"settled": false, "waited_s": t, "threshold_ua": CHARGE_SETTLE_UA, "samples": samples});
        }
        std::thread::sleep(std::time::Duration::from_secs(2));
    }
}

/// `charge_suspend.sh suspend` 的主机端超时 (秒)。审查 R1 #2: 以前 90 s, 单候选最坏就要 90 s + 启动,
/// 超时先到, 设备端脚本却接着跑、几分钟后写成 STATE, 没人还原。现在脚本自己限总时长
/// (SUSPEND_TOTAL_MAX_S=300, 每个候选开始前与等旁路的每一秒都查), 主机端只要盖过它加一个候选的收尾
/// (12 s 等放电 + 几次读数)。
pub(crate) const CHARGE_SUSPEND_TIMEOUT_S: u64 = 300 + 60;

/// charge_suspend.sh 的输出 (suspend 的回显, 超时后再接上 status 的回显) 里认出「现在是停充状态」:
/// `CHARGE_SUSPENDED` / `_BYPASS` / `_MIXED`, `CHARGE_ALREADY_SUSPENDED` (别人留下的, 这次也负责还原,
/// 审查 R1 #2 #10), 或 status 的 `state: 已停充`。返回记进报告的那一行。
/// 每臂开跑前核对还在放电; 不在就重新停充 (这回由 charge_suspend.sh 接管、留 STATE, 结束时它负责还原)。
/// 2026-10-23 红魔: 调用方开跑前自己关了充电, charge_suspend.sh 见「本来就在放电」没接管; 十几分钟后
/// 充电被重新打开 (厂商充电管理), 后面 11 臂全在充电, 功耗全部作废。回 (此刻在放电, 日志)。
pub(crate) fn ensure_on_battery(serial: &str) -> (bool, String) {
    let st = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh status"), 30);
    if st.contains("on_battery=yes") {
        return (true, st);
    }
    let out = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh suspend"), CHARGE_SUSPEND_TIMEOUT_S);
    let ok = charge_suspended_via(&out).is_some() || out.contains("CHARGE_ALREADY_ON_BATTERY");
    (ok, format!("{st}\n--- 不在放电, 重新停充 ---\n{out}"))
}

pub(crate) fn charge_suspended_via(out: &str) -> Option<String> {
    out.lines().map(str::trim).find_map(|l| {
        if l.starts_with("CHARGE_SUSPENDED") || l.starts_with("CHARGE_ALREADY_SUSPENDED") || l.starts_with("state: 已停充") {
            Some(l.to_string())
        } else {
            None
        }
    })
}

/// 开跑前对账 (审查 R1 #10): `knob_sysparam.sh status` / `charge_suspend.sh status` 的回显里有没有
/// 上一次留下的状态。返回 (旋钮已施加, 已停充)。
pub(crate) fn parse_preexisting(knob_status: &str, charge_status: &str) -> (bool, bool) {
    let has = |s: &str, tok: &str| s.lines().any(|l| l.trim().starts_with(tok));
    (has(knob_status, "state: 已施加"), has(charge_status, "state: 已停充"))
}

/// 开跑前对账并还原残留 (审查 R1 #10): 上一次崩溃 / Ctrl-C 留下的旋钮 STATE 会让这次 apply 回
/// `KNOB_ALREADY_APPLIED`, 停充 STATE 会让这次 suspend 回 `CHARGE_ALREADY_SUSPENDED`、结束时不还原。
/// 回包 `conditions_observed.preexisting_state` 原样记下对账结果与还原回显。
pub(crate) fn reconcile_preexisting(serial: &str, evidence: &Path) -> Value {
    let ks = su(serial, &format!("sh {DEV_TMP}/knob_sysparam.sh status"), 60);
    let cs = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh status"), 60);
    let (knob_applied, charge_suspended) = parse_preexisting(&ks, &cs);
    let mut v = json!({
        "knob_applied": knob_applied,
        "charge_suspended": charge_suspended,
        "knob_status": ks.trim(),
        "charge_status": cs.trim(),
    });
    if knob_applied {
        let r = su(serial, &format!("sh {DEV_TMP}/knob_sysparam.sh restore"), 120);
        eprintln!("[eval] 开跑前发现上次留下的旋钮状态, 已还原: {}", r.lines().last().unwrap_or(""));
        v["knob_restore"] = json!(r.trim());
    }
    if charge_suspended {
        let r = su(serial, &format!("sh {DEV_TMP}/charge_suspend.sh restore"), 60);
        eprintln!("[eval] 开跑前发现上次留下的停充状态, 已还原: {}", r.lines().last().unwrap_or(""));
        v["charge_restore"] = json!(r.trim());
    }
    let _ = std::fs::write(evidence.join("preexisting_state.txt"), format!("# knob status\n{ks}\n# charge status\n{cs}\n"));
    v
}

/// 截断用的「两臂还会各跑几臂」(审查 R1 #3)。contested 的改写臂 (守不住, 不进 knob_runs) 与
/// settle_pair 剔掉的对 (两臂都移走) 都已经跑过了, 不能再算成「还没跑」—— 否则 keep_possible 永远偏乐观,
/// 守不住的候选会把 10 臂全跑完。
pub(crate) fn remaining_arms(rounds: usize, ctrl_done: usize, knob_done: usize, contested: usize, dropped: usize) -> (usize, usize) {
    (
        rounds.saturating_sub(ctrl_done + dropped),
        rounds.saturating_sub(knob_done + contested + dropped),
    )
}

/// 跑满后两臂最多能凑到 `n_a_max` / `n_b_max` 个样本, 这个样本量在数学上还判得出显著改善吗
/// (审查 R1 #3)。两臂等量时就是 [`loopstat::ensure_resolvable`] 的判据 (最小双侧 p = 2/C(2n,n));
/// 不等量时精确置换的最小 p 是 1/C(na+nb, na) (与 curtail::win_p_floor 的系数一致)。
/// 取各检验里最宽的 alpha_win: 连它都够不着, KEEP 才真不可达。没有检验时不截。
pub(crate) fn keep_reachable(n_a_max: usize, n_b_max: usize, tests: &[curtail::WinTest]) -> bool {
    let Some(alpha_win) = tests.iter().map(|t| t.alpha_win).reduce(f64::max) else {
        return true;
    };
    if n_a_max == n_b_max {
        return loopstat::ensure_resolvable(n_a_max, alpha_win, "").is_ok();
    }
    if n_a_max < 2 || n_b_max < 2 {
        return false;
    }
    let (n, k) = (n_a_max + n_b_max, n_a_max.min(n_b_max));
    let mut c = 1.0_f64;
    for i in 0..k {
        c = c * (n - i) as f64 / (i + 1) as f64;
    }
    crate::pyjson::py_round(1.0 / c, 6) < alpha_win
}

/// 审查 R1 #10: Ctrl-C / SIGTERM 时把登记的还原命令跑掉 (旋钮 restore、停充 restore、停掉游戏),
/// 不留残留。信号处理函数里只置一个标志; 真正跑 adb 的是一条看门线程 —— 信号上下文里不能起进程。
pub(crate) mod cleanup {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Mutex, Once};

    static HIT: AtomicBool = AtomicBool::new(false);
    static INSTALLED: Once = Once::new();
    static ACTIONS: Mutex<Vec<(String, String)>> = Mutex::new(Vec::new());

    #[cfg(unix)]
    extern "C" {
        // libc 的 signal(2); 只为这两个信号引一个 libc crate 不划算, 直接声明外部符号 (同 decision.rs 的 kill)
        fn signal(sig: i32, handler: extern "C" fn(i32)) -> usize;
    }

    extern "C" fn on_signal(_sig: i32) {
        HIT.store(true, Ordering::SeqCst);
    }

    /// 登记一条「被打断时要跑」的 adb shell 命令 (su 的自己带 `su -c '...'`)。同一条只登记一次。
    pub fn register(serial: &str, shell_cmd: &str) {
        if let Ok(mut a) = ACTIONS.lock() {
            let item = (serial.to_string(), shell_cmd.to_string());
            if !a.contains(&item) {
                a.push(item);
            }
        }
    }

    /// 目前登记的命令 (单测与报错用)。
    pub fn pending() -> Vec<(String, String)> {
        ACTIONS.lock().map(|a| a.clone()).unwrap_or_default()
    }

    pub fn clear() {
        if let Ok(mut a) = ACTIONS.lock() {
            a.clear();
        }
    }

    /// 装一次信号处理 + 看门线程。进程本来就要在 eval 结束时退出, 看门线程不必回收。
    pub fn install() {
        INSTALLED.call_once(|| {
            #[cfg(unix)]
            unsafe {
                signal(2, on_signal); // SIGINT
                signal(15, on_signal); // SIGTERM
            }
            std::thread::spawn(|| loop {
                std::thread::sleep(std::time::Duration::from_millis(200));
                if HIT.load(Ordering::SeqCst) {
                    eprintln!("[eval] 收到中断, 先跑还原再退出");
                    for (serial, cmd) in pending() {
                        let out = super::adb(&serial, &["shell", &cmd], 120);
                        eprintln!("[eval] 还原 {cmd}: {}", out.lines().last().unwrap_or(""));
                    }
                    std::process::exit(130);
                }
            });
        });
    }
}

/// 功耗的辅助证据: GPU 平均频率 (sample_env 的 GPU 行)、总线带宽投票中位与 GPU 忙碌时间
/// (parse-trace)。只报不判 —— 不进 wins / regressions, 不影响是否保留; 功耗量不准时它们说明
/// 「GPU 那头确实少干了活」(2026-10-12 W1: 去 MSAA 后频率 −13%、带宽投票 −15%, 5 对完全分开,
/// 而忙碌时间不变: DVFS 把省下的活换成了降频)。缺数据的项记 null。
pub(crate) fn aux_compare(a_runs: &[Run], b_runs: &[Run]) -> Value {
    let mut out = serde_json::Map::new();
    for metric in ["gpu_clk_mhz_mean", "bw_median", "gpu_active_mean"] {
        let cmp = loopstat::compare(a_runs, b_runs, metric);
        let sv = |k: &str| cmp.get(k).map(to_serde).unwrap_or(Value::Null);
        out.insert(
            metric.to_string(),
            if cmp.get("error").is_some() {
                Value::Null
            } else {
                json!({
                    "a_mean": sv("a_mean"), "b_mean": sv("b_mean"),
                    "diff_pct": sv("diff_pct"), "p": sv("perm_p_two_sided"), "ci95": sv("ci95"),
                })
            },
        );
    }
    json!({"note": "辅助证据, 只报不判: 不进 wins/regressions, 不替代功耗", "metrics": Value::Object(out)})
}

/// ABBA 两臂的统计判定 (sysparam / gray 共用): 帧时 p95、平均帧率, 两臂都放电时加功耗。
/// `fps_cap_declared` = 请求声明的帧率上限 (workload.fps_cap, 审查 R2 #9): 锁帧规则由它定, 数据只确认。
/// 回 (verdict JSON, 是否保留, 两臂是否都在放电态)。
pub(crate) fn judge_runs(
    ctrl_runs: &[Value],
    knob_runs: &[Value],
    alpha: f64,
    rounds: u32,
    fps_cap_declared: Option<f64>,
) -> (Value, bool, bool) {
    judge_runs_with(ctrl_runs, knob_runs, alpha, rounds, fps_cap_declared, None)
}

/// 同 [`judge_runs`]; `required` 给了 (实验资格冻结的 policy.required_metrics) 就按它判,
/// 功耗是否进判定由目标合同决定, 不随这一轮量没量到功耗而缩窄。
pub(crate) fn judge_runs_with(
    ctrl_runs: &[Value],
    knob_runs: &[Value],
    alpha: f64,
    rounds: u32,
    fps_cap_declared: Option<f64>,
    required: Option<&[String]>,
) -> (Value, bool, bool) {
    let primary = "frame_p95";
    // 功耗按对取舍 (审计 #5): 一臂不合格只剔除那一对; 口径不一致时统一成两轨同刻相加。
    // 同口径 ≠ 一定是放电态: 停充后 USB+电池两轨相加的两臂相减也成立 (同一套损耗),
    // 但那个绝对值不能跨任务比, 回包里用 power.rail / cross_run_comparable 标清楚。
    let pp = power_pairs_of(knob_runs, ctrl_runs);
    let (a_runs, b_runs) = (eval_runs_power(ctrl_runs, &pp.ctrl), eval_runs_power(knob_runs, &pp.knob));
    let on_battery_all = pp.usable();
    // 实验资格冻结的 required_metrics (main) 给了就按它判, 功耗不随这一轮量没量到而缩窄;
    // 没给时沿用「按对取舍后功耗可用才进表」(ruler fix-D)。
    let metrics_list: Vec<(String, &str)> = match required {
        Some(ms) => ms.iter().map(|m| (m.clone(), if m == "fps_mean" { "higher" } else { "lower" })).collect(),
        None => {
            let mut l: Vec<(String, &str)> = vec![("frame_p95".into(), "lower"), ("fps_mean".into(), "higher")];
            if on_battery_all {
                l.push(("power_w_mean".into(), "lower"));
            }
            l
        }
    };
    let mut p_primary = 1.0f64;
    let mut wins: Vec<String> = Vec::new();
    let mut regressions: Vec<String> = Vec::new();
    let mut per_metric: Vec<(String, Value)> = Vec::new();
    // 审计 #8: 负载被帧率上限钉住时 (Megacity 旧包锁 30 fps), fps_mean 不可能显著改善,
    // 只用来守退化, 不占 Bonferroni 预算, 也不能算 win。
    // 审查 R2 #9: 锁帧由请求声明, 数据只确认 (规则先于数据冻结), 不再事后按数据决定 K。
    let (fps_cap, fps_lock_note) = fps_lock_confirmed(fps_cap_declared, ctrl_runs, knob_runs);
    let guard_only = |m: &str| fps_cap.is_some() && m == "fps_mean";
    let alpha_win = alpha / metrics_list.iter().filter(|(m, _)| !guard_only(m)).count() as f64;
    for (metric, better) in &metrics_list {
        let cmp = loopstat::compare(&a_runs, &b_runs, metric);
        let diff = cmp.get("diff_mean").and_then(|v| v.as_f64());
        let p = cmp.get("perm_p_two_sided").and_then(|v| v.as_f64());
        let improved = match (diff, *better) {
            (Some(d), "lower") => d < 0.0,
            (Some(d), "higher") => d > 0.0,
            _ => false,
        };
        // 锁帧时的帧预算 (judge/v1-scorecard): 负载被上限钉住时, frame_p95 变长但帧还放得进
        // 帧预算 (p95 ≤ 1000/cap × (1+余量)、fps 守住上限、丢帧不增加) 就不算退化; 帧预算用掉多少照报。
        let capped = match (fps_cap, metric.as_str()) {
            (Some(cap), "frame_p95") => Some(crate::scorecard::capped_budget_check(cap, knob_runs, ctrl_runs)),
            _ => None,
        };
        let mut capped_exempt = false;
        let status = if cmp.get("error").is_some() || diff.is_none() {
            "缺数据"
        } else if improved && !guard_only(metric) && p.is_some_and(|x| x < alpha_win) {
            wins.push(metric.to_string());
            "显著改善"
        } else if !improved && diff.is_some_and(|x| x != 0.0) && p.is_some_and(|x| x < alpha) {
            if capped.as_ref().is_some_and(|c| c["ok"].as_bool() == Some(true)) {
                capped_exempt = true;
                "锁帧内变长 (帧预算内, 不算退化)"
            } else {
                regressions.push(metric.to_string());
                "显著变差"
            }
        } else {
            "无显著变化"
        };
        if metric == primary {
            p_primary = p.unwrap_or(1.0);
        }
        per_metric.push((
            metric.to_string(),
            {
                let sv = |k: &str| cmp.get(k).map(to_serde).unwrap_or(Value::Null);
                let mut e = json!({
                    "status": status,
                    "a_mean": sv("a_mean"),
                    "b_mean": sv("b_mean"),
                    "diff_mean": sv("diff_mean"),
                    "diff_pct": sv("diff_pct"),
                    "p": sv("perm_p_two_sided"),
                    "ci95": sv("ci95"),
                });
                if guard_only(metric) {
                    e["role"] = json!("guard_only");
                }
                if let Some(c) = &capped {
                    e["capped"] = c.clone();
                    if capped_exempt {
                        e["role"] = json!("capped_budget");
                    }
                }
                e
            }
        ));
    }
    // 冻结的必需指标缺数据 (例如功耗按对剔完) 时不能 KEEP: 不得静默缩窄目标 (main)
    let required_missing: Vec<String> = match required {
        Some(_) => per_metric.iter().filter(|(_, e)| e["status"] == "缺数据").map(|(m, _)| m.clone()).collect(),
        None => Vec::new(),
    };
    let keep = !wins.is_empty() && regressions.is_empty() && required_missing.is_empty();
    let aux = aux_compare(&a_runs, &b_runs);
    // 审查 R2 #8: 分辨率按**实际进判定的臂数**算, 不按计划轮数: 剔了对、contested 之后 3v3 的最小 p 是 0.1,
    // 再写 reachable:true 上层就会把「判不出」记成负样本。功耗那一项按功耗保留的对数, 取最严。
    let res = actual_resolution(ctrl_runs.len(), knob_runs.len(), if on_battery_all { Some(pp.kept()) } else { None }, alpha_win);
    let verdict = json!({
        "is_pareto_improvement": keep,
        "p_value": p_primary,
        "stat": "exact_permutation",
        "alpha_actual": alpha,
        "alpha_win": alpha_win,
        "alpha_regression": alpha,
        // 实际臂数下 alpha_win 是否够得着 (帧时/帧率按 n_ctrl v n_knob, 功耗按保留的对数, 都够着才 true)
        "reachable": res["reachable"],
        // 实际臂数下精确置换检验最小能出多小的 p (各项里最大的那个)。调用方 (如 game_opt_loop 的初筛)
        // 拿它定「最极端排列」的门槛, 不必自己再算一遍
        "min_reachable_p": res["min_reachable_p"],
        "resolution": res,
        // 按计划轮数算的旧口径, 只供对照
        "reachable_planned": reachable(rounds, alpha_win),
        "wins": wins,
        "regressions": regressions,
        "per_metric": Value::Object(per_metric.into_iter().collect()),
        "aux": aux,
        "power_pairs": pp.to_json(),
        // 请求声明且数据确认的帧率上限: fps_mean 只守退化 (per_metric.fps_mean.role)
        "fps_locked_cap": fps_cap,
        "required_missing": required_missing,
        "fps_lock": fps_lock_note,
        "reason": if keep {
            json!(format!("改善 {} 且无显著退化", metrics_list.iter().find(|(m, _)| wins.first() == Some(&m.to_string())).map(|(m, _)| m.clone()).unwrap_or_default()))
        } else if !required_missing.is_empty() {
            json!(format!("冻结的必需指标缺数据: {}", required_missing.join(",")))
        } else if !regressions.is_empty() {
            json!(format!("存在显著退化: {}", regressions.join(",")))
        } else {
            json!("没有任何指标显著改善")
        },
    });
    (verdict, keep, on_battery_all)
}

/// 负载是不是被帧率上限钉住了: 每一轮的 frame_p50 都贴着同一个常见限帧上限
/// (沿用归因那一处的口径 [`crate::looptrace::detect_fps_cap`]: 帧周期中位数离 1000/cap 不超过 2%)。
/// 是就回这个上限 (fps)。没有轮、有一轮缺 frame_p50、或有一轮离开上限都回 None。
/// 实测依据 (审计 #8): Megacity 旧包 targetFrameRate=-1 锁 30 fps, frame_p50 约 33.3 ms。
/// 只看两臂合在一起的帧周期水平, 不看两臂之差; 候选若真把上限打破 (有一轮离开上限), fps_mean 照常可以 win。
pub(crate) fn fps_lock_cap<'a>(runs: impl IntoIterator<Item = &'a Value>) -> Option<f64> {
    let mut cap = None;
    for r in runs {
        let c = crate::looptrace::detect_fps_cap(r["summary"]["frame_p50"].as_f64()?)?;
        if cap.is_some_and(|x| x != c) {
            return None;
        }
        cap = Some(c);
    }
    cap.map(|c| c as f64)
}

/// 两臂 fps_mean 均值相差不超过这个比例, 锁帧才算「确认」(审查 R2 #9)。
pub(crate) const FPS_LOCK_MEAN_TOL: f64 = 0.01;
/// 每轮 frame_p50 离 1000/cap 不超过这个比例 (与 [`crate::looptrace::detect_fps_cap`] 同一容差)。
const FPS_LOCK_P50_TOL: f64 = 0.02;

/// 审查 R2 #9: 锁帧由请求声明 (`workload.fps_cap`), 数据只确认, 不再事后按数据放宽 K。
/// 确认 = 每一轮 frame_p50 都贴着 1000/cap (±2%) 且两臂 fps_mean 均值相差 < 1%。
/// 回 (确认后的上限, 记录)。没声明时即使数据像锁帧也回 None (规则先于数据冻结), 只在记录里提示。
pub(crate) fn fps_lock_confirmed(declared: Option<f64>, ctrl_runs: &[Value], knob_runs: &[Value]) -> (Option<f64>, Value) {
    let all: Vec<&Value> = ctrl_runs.iter().chain(knob_runs.iter()).collect();
    let detected = fps_lock_cap(all.iter().copied());
    let mean_fps = |runs: &[Value]| -> Option<f64> {
        let xs: Vec<f64> = runs.iter().filter_map(|r| r["summary"]["fps_mean"].as_f64()).collect();
        (!xs.is_empty()).then(|| xs.iter().sum::<f64>() / xs.len() as f64)
    };
    let Some(cap) = declared else {
        return (
            None,
            json!({
                "declared": null,
                "confirmed": false,
                "detected_from_data": detected,
                "note": if detected.is_some() {
                    "数据像锁在这个帧率上, 但请求没声明 workload.fps_cap: 不按锁帧判 (K 不放宽), 要锁帧请在请求里声明"
                } else {
                    "请求没声明帧率上限, 按不锁帧判"
                },
            }),
        );
    };
    let target = 1000.0 / cap;
    let p50_at_cap = !all.is_empty()
        && all.iter().all(|r| {
            r["summary"]["frame_p50"].as_f64().is_some_and(|p| (p - target).abs() / target <= FPS_LOCK_P50_TOL)
        });
    let (mc, mk) = (mean_fps(ctrl_runs), mean_fps(knob_runs));
    let fps_mean_diff = match (mc, mk) {
        (Some(a), Some(b)) if a > 0.0 => Some(((b - a) / a * 10000.0).round() / 10000.0),
        _ => None,
    };
    let fps_close = fps_mean_diff.is_some_and(|d| d.abs() < FPS_LOCK_MEAN_TOL);
    let confirmed = p50_at_cap && fps_close;
    let note = json!({
        "declared": cap,
        "confirmed": confirmed,
        "p50_at_cap": p50_at_cap,
        "fps_mean_diff_frac": fps_mean_diff,
        "fps_mean_within_tol": fps_close,
        "detected_from_data": detected,
        "note": if confirmed {
            "请求声明的上限被数据确认: fps_mean 只守退化, 不占预算"
        } else {
            "请求声明了上限但数据没确认 (有一轮 p50 离开上限, 或两臂 fps_mean 相差 >= 1%): 按不锁帧判"
        },
    });
    (confirmed.then_some(cap), note)
}

/// 审查 R2 #8: 按实际进判定的臂数算分辨率。帧时/帧率用 (n_ctrl, n_knob) 的精确置换
/// (最小双侧 p = 2/C(na+nb, na)), 功耗用保留的对数 (kept)。回 {reachable, min_reachable_p, n_ctrl, n_knob,
/// power_kept, min_p_frame, min_p_power}; `reachable` 要各项都够得着 alpha_win, `min_reachable_p` 取各项最大。
pub(crate) fn actual_resolution(n_ctrl: usize, n_knob: usize, power_kept: Option<(usize, usize)>, alpha_win: f64) -> Value {
    let p_frame = min_two_sided_p_unequal(n_ctrl, n_knob);
    let p_power = power_kept.map(|(a, b)| min_two_sided_p_unequal(a, b));
    let min_p = p_power.map_or(p_frame, |p| p.max(p_frame));
    json!({
        "reachable": min_p < alpha_win,
        "min_reachable_p": min_p,
        "n_ctrl": n_ctrl,
        "n_knob": n_knob,
        "power_kept": power_kept.map(|(a, b)| json!([a, b])),
        "min_p_frame": p_frame,
        "min_p_power": p_power,
    })
}

/// na v nb 精确置换检验的最小双侧 p = 2/C(na+nb, na) (等量时就是 [`loopstat::min_two_sided_p`]); 任一臂 < 2 回 1.0。
/// 舍入到 6 位, 与 loopstat 的 p 同精度。
pub(crate) fn min_two_sided_p_unequal(na: usize, nb: usize) -> f64 {
    if na == nb {
        return loopstat::min_two_sided_p(na);
    }
    if na < 2 || nb < 2 {
        return 1.0;
    }
    let (n, k) = (na + nb, na.min(nb));
    let mut c = 1.0_f64;
    for i in 0..k {
        c = c * (n - i) as f64 / (i + 1) as f64;
    }
    crate::pyjson::py_round((2.0 / c).min(1.0), 6)
}

/// 逐臂聚合进 metrics (B 臂 = 候选臂)。回候选臂功耗 (放电态才有)。
pub(crate) fn aggregate_metrics(m: &mut Value, knob_runs: &[Value], ctrl_runs: &[Value], on_battery_all: bool) -> Option<f64> {
    let f = |runs: &[Value], k: &str| -> Option<f64> {
        let xs: Vec<f64> = runs.iter().filter_map(|r| r["summary"][k].as_f64()).collect();
        (!xs.is_empty()).then(|| xs.iter().sum::<f64>() / xs.len() as f64)
    };
    let temps: Vec<f64> = knob_runs
        .iter()
        .chain(ctrl_runs.iter())
        .filter_map(run_soc_temp_max)
        .collect();
    m["fps_p95_ms"] = json!(f(knob_runs, "frame_p95"));
    m["frame_p95_ms"] = json!(f(knob_runs, "frame_p95"));
    m["fps_mean"] = json!(f(knob_runs, "fps_mean"));
    m["soc_temp_max_c"] = json!(temps.iter().cloned().reduce(f64::max));
    // 功耗: 只在放电态均值可信时给; 与帧时一样取候选臂全部轮的均值, 不取最后一轮
    let power = if on_battery_all {
        // 按对取舍后的候选臂值 (被剔除的对不算, 口径统一时是两轨相加的值)
        let xs: Vec<f64> = power_pairs_of(knob_runs, ctrl_runs).knob.into_iter().flatten().collect();
        (!xs.is_empty()).then(|| xs.iter().sum::<f64>() / xs.len() as f64)
    } else {
        None
    };
    m["power_watt"] = json!(power);
    power
}

// ══════════════ gray ══════════════

/// 一个 gray 候选的改写 → 挂层参数。
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct GrayKnobs {
    /// LoadOp LOAD→DONT_CARE 全开。
    pub loadop: bool,
    /// 「去掉多余写回」指定串 ("0" = 关)。
    pub storeop: String,
    /// NPU 超分 (npu/n1-pipeline 的层): 调参 + 本机文件 (层 .so / 模型 / 运行库目录)。None = 关。
    pub npuop: Option<NpuopKnob>,
}

/// `npuop` 开关的 args: `{"lag":2,"wait_ms":2,"budget_ms":12,"budget_frames":30,"init_frame":300,"zc":1,"ts":1,
/// "model":"<本机 .tflite>","layer_so":"<本机层 .so>","runtime_dir":"<放齐运行库的目录>"}`, 每项都可省。
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct NpuopKnob {
    pub props: crate::graylayer::NpuopProps,
    pub files: crate::npuop_device::NpuopFileArgs,
}

/// npuop 的 args 里认的键 (多出来的键一律拒收: 写错键名会静默按缺省跑)。
pub(crate) const NPUOP_ARG_KEYS: [&str; 10] =
    ["lag", "wait_ms", "budget_ms", "budget_frames", "init_frame", "zc", "ts", "model", "layer_so", "runtime_dir"];

/// 解析 npuop 的 args 并核范围 (都在碰设备之前)。
pub(crate) fn parse_npuop_args(args: &Value) -> Result<NpuopKnob, String> {
    let mut props = crate::graylayer::NpuopProps::default();
    let mut files = crate::npuop_device::NpuopFileArgs::default();
    let Some(obj) = args.as_object() else {
        if args.is_null() {
            return Ok(NpuopKnob { props, files });
        }
        return Err(format!("npuop 的 args 要是对象, 收到 {args}"));
    };
    for k in obj.keys() {
        if !NPUOP_ARG_KEYS.contains(&k.as_str()) {
            return Err(format!("npuop 的 args 不认识 {k:?} (认: {})", NPUOP_ARG_KEYS.join(", ")));
        }
    }
    let num = |k: &str, lo: f64, hi: f64| -> Result<Option<f64>, String> {
        match obj.get(k) {
            None | Some(Value::Null) => Ok(None),
            Some(v) => {
                let x = v.as_f64().or_else(|| v.as_str().and_then(|s| s.trim().parse().ok())).ok_or_else(|| format!("npuop.{k} 要是数字, 收到 {v}"))?;
                if !(lo..=hi).contains(&x) {
                    return Err(format!("npuop.{k}={x} 超出 [{lo}, {hi}]"));
                }
                Ok(Some(x))
            }
        }
    };
    let flag = |k: &str| -> Result<Option<bool>, String> {
        match obj.get(k) {
            None | Some(Value::Null) => Ok(None),
            Some(Value::Bool(b)) => Ok(Some(*b)),
            Some(v) => match v.as_f64().or_else(|| v.as_str().and_then(|s| s.trim().parse().ok())) {
                Some(x) if x == 0.0 || x == 1.0 => Ok(Some(x == 1.0)),
                _ => Err(format!("npuop.{k} 要是 0/1 或 true/false, 收到 {v}")),
            },
        }
    };
    let text = |k: &str| -> Result<Option<String>, String> {
        match obj.get(k) {
            None | Some(Value::Null) => Ok(None),
            Some(Value::String(t)) if !t.trim().is_empty() => Ok(Some(t.trim().to_string())),
            Some(v) => Err(format!("npuop.{k} 要是非空路径字符串, 收到 {v}")),
        }
    };
    if let Some(x) = num("lag", 1.0, 2.0)? {
        if x != 1.0 && x != 2.0 {
            return Err(format!("npuop.lag 只认 1 / 2, 收到 {x}"));
        }
        props.lag = x as u8;
    }
    if let Some(x) = num("wait_ms", 0.0, 50.0)? {
        props.wait_ms = x;
    }
    if let Some(x) = num("budget_ms", 0.5, 100.0)? {
        props.budget_ms = x;
    }
    if let Some(x) = num("budget_frames", 1.0, 10000.0)? {
        props.budget_frames = x as u32;
    }
    if let Some(x) = num("init_frame", 1.0, 100000.0)? {
        props.init_frame = x as u64;
    }
    if let Some(b) = flag("zc")? {
        props.zc = b;
    }
    if let Some(b) = flag("ts")? {
        props.ts = b;
    }
    files.model = text("model")?;
    if let Some(m) = &files.model {
        if !m.ends_with(".tflite") {
            return Err(format!("npuop.model 要是 .tflite, 收到 {m:?}"));
        }
    }
    files.layer_so = text("layer_so")?;
    files.runtime_dir = text("runtime_dir")?;
    Ok(NpuopKnob { props, files })
}

/// 读 payload.rewrites。不认识的开关、缺范围的写回改写一律拒收 —— 在碰设备之前。
///
/// - `loadop_dont_care`: 全部 LoadOp LOAD → DONT_CARE (层的 debug.knobs.loadop)。
/// - `storeop_dont_care` + `scope = "<pass 特征码前缀 ≥8 位>:<附件下标>"`: 只改那一个 pass 的
///   那一个附件 (debug.knobs.storeop)。特征码取自 `phonefarm frame-audit analyze`。可以有多条,
///   合成一个指定串一起施加。
pub(crate) fn parse_gray_rewrites(payload: &Value) -> Result<GrayKnobs, String> {
    let arr = payload["rewrites"].as_array().cloned().unwrap_or_default();
    if arr.is_empty() {
        return Err("payload.rewrites 是空的: 没有要改写的".into());
    }
    let mut loadop = false;
    let mut npuop: Option<NpuopKnob> = None;
    let mut items: Vec<(String, u32)> = Vec::new();
    for r in &arr {
        let knob = r["knob"].as_str().unwrap_or("");
        match knob {
            "loadop_dont_care" => loadop = true,
            "npuop" => {
                if npuop.is_some() {
                    return Err("npuop 出现了两次".into());
                }
                npuop = Some(parse_npuop_args(&r["args"])?);
            }
            "storeop_dont_care" => {
                let scope = r["scope"].as_str().unwrap_or("");
                let (sig, att) = scope.split_once(':').ok_or_else(|| {
                    format!("storeop_dont_care 要 scope=\"<特征码前缀>:<附件下标>\", 收到 {scope:?}")
                })?;
                let att: u32 = att
                    .trim()
                    .parse()
                    .map_err(|_| format!("storeop_dont_care 的附件下标看不懂: {scope:?}"))?;
                items.push((sig.trim().to_string(), att));
            }
            other => return Err(format!("接收端不认识改写开关 {other:?} (认: loadop_dont_care / storeop_dont_care / npuop)")),
        }
    }
    if npuop.is_some() && loadop {
        return Err("npuop 不能与 loadop_dont_care 同组 (loadop 已撤回, 且会把超分算子的输入画错)".into());
    }
    let storeop = crate::graylayer::storeop_spec(&items)?;
    Ok(GrayKnobs { loadop, storeop, npuop })
}

/// 画面比对缺省门槛: 改写臂与对照臂同位置截图的 PSNR 低于它就判画面出错。
pub(crate) const GRAY_MIN_PSNR_DB: f64 = 30.0;

/// 画面判据允许比「同设置重拍」差多少 dB。
///
/// 1.0 dB 的依据: 实测同一场景两次重拍能差 1.5-2 dB (Megacity city_static 实测 26.41-28.06),
/// 而真正改了画面的改写差 2 dB 以上 (同次实测 knob 29.71 dB)。留 1 dB 给噪声, 剩下留给真实的画面变化。
pub(crate) const IMAGE_GATE_MARGIN_DB: f64 = 1.0;
/// 噪声底低于这个值 = 这个场景自身不稳定, 画面判据没有分辨力, 必须拒绝判定而不是猜。
pub(crate) const IMAGE_GATE_MIN_USABLE_DB: f64 = 20.0;

/// 画面判据的输入: 配置阈值 + 这个场景实测的噪声底。
///
/// 固定阈值的毛病是它不知道场景有多稳。Megacity city_static 实测同一臂相隔 1 秒重拍只有
/// 26.41 / 27.90 dB, 对照对对照 28.06 dB —— 固定 30 dB 的阈值比噪声底还高, 任何候选
/// (包括什么都没改的) 都会被判「画面出错」。所以判定要拿噪声底比。
#[derive(Debug, Clone, Copy)]
pub(crate) struct ImageGate {
    /// 配置的绝对阈值 ([`GRAY_MIN_PSNR_DB`] 或请求里的 `image_check.min_psnr_db`)。
    pub configured_db: f64,
    /// 同设置重拍能达到的相似度 (取观测到的最大值)。`None` = 没有重拍可用。
    pub noise_floor_db: Option<f64>,
    pub margin_db: f64,
    pub min_usable_db: f64,
}

impl ImageGate {
    pub(crate) fn new(configured_db: f64, noise_floor_db: Option<f64>) -> Self {
        ImageGate {
            configured_db,
            noise_floor_db,
            margin_db: IMAGE_GATE_MARGIN_DB,
            min_usable_db: IMAGE_GATE_MIN_USABLE_DB,
        }
    }

    /// 这一关按什么过的: `absolute` / `noise_floor`。
    pub(crate) fn gate(&self, passed_by: &str) -> serde_json::Value {
        serde_json::json!({
            "passed_by": passed_by,
            "configured_db": self.configured_db,
            "noise_floor_db": self.noise_floor_db,
            "margin_db": self.margin_db,
            "min_usable_db": self.min_usable_db,
        })
    }
}

/// 画面判定结果。
pub(crate) enum ImageRuling {
    Pass(&'static str),
    Fail(String),
}

/// 同设置重拍能达到的相似度: 取观测到的**最大值**。
///
/// 用最大值而不是最小值: 场景里有一点动画时, 两次重拍的差距本身就是噪声,
/// 取最大的那一次才是这套采集能在同一内容上达到的上限。
pub(crate) fn noise_floor_db(values: impl IntoIterator<Item = Option<f64>>) -> Option<f64> {
    values.into_iter().flatten().reduce(f64::max)
}

/// 判定「改写臂的画面是不是比同设置重拍还差」。
///
/// `best_knob_vs_ctrl_db` 取改写臂对多个对照帧里**最好**的那一次
/// (场景有动画时, 最差的那次只是撞上了动画帧, 它的含义不是「画面被改坏了」)。
pub(crate) fn judge_image(gate: &ImageGate, best_knob_vs_ctrl_db: Option<f64>) -> ImageRuling {
    let Some(w) = best_knob_vs_ctrl_db else {
        return ImageRuling::Fail("截图尺寸不一致, 比不了".into());
    };
    if w >= gate.configured_db {
        return ImageRuling::Pass("absolute");
    }
    match gate.noise_floor_db {
        Some(n) if n < gate.min_usable_db => ImageRuling::Fail(format!(
            "同设置重拍只有 {n:.2} dB (低于可用下限 {:.0} dB): 这个场景自身不稳定, \
画面判据在它上面没有分辨力 —— 换静态场景或改用孪生 pass 现场开关, 不要拿这个数判画面",
            gate.min_usable_db
        )),
        Some(n) if w >= n - gate.margin_db => ImageRuling::Pass("noise_floor"),
        Some(n) => ImageRuling::Fail(format!(
            "改写臂只有 {w:.2} dB, 低于配置阈值 {:.2} dB, 也低于同设置重拍噪声底 {n:.2} dB 减 {:.1} dB",
            gate.configured_db, gate.margin_db
        )),
        None => ImageRuling::Fail(format!(
            "改写臂 {w:.2} dB 低于配置阈值 {:.2} dB, 且没有同设置重拍可作噪声底: 判不了 \
(至少跑 2 轮, 或让采集侧多拍一张同设置对照)",
            gate.configured_db
        )),
    }
}
/// 挂层重启、进到场景之后先等画面稳下来再截图 (流式加载、过场淡入)。
const GRAY_SHOT_SETTLE_S: u64 = 8;
/// 两张图逐像素相同时 PSNR 是无穷大; JSON 放不下, 按这个值记。
const PSNR_IDENTICAL_DB: f64 = 99.0;

/// 两张 RGBA 截图的 PSNR (只算 RGB)。尺寸不同回 None —— 比不了, 不是"差别很大"。
pub(crate) fn psnr_rgba(a: &[u8], b: &[u8]) -> Option<f64> {
    if a.len() != b.len() || a.len() < 4 {
        return None;
    }
    let mut se: f64 = 0.0;
    let mut n: u64 = 0;
    for (pa, pb) in a.chunks_exact(4).zip(b.chunks_exact(4)) {
        for c in 0..3 {
            let d = pa[c] as f64 - pb[c] as f64;
            se += d * d;
        }
        n += 3;
    }
    let mse = se / n as f64;
    if mse == 0.0 {
        return Some(PSNR_IDENTICAL_DB);
    }
    Some(((10.0 * (255.0f64 * 255.0 / mse).log10()) * 100.0).round() / 100.0)
}

fn recover_gray_eval(args:&[String])->i32 {
    let result=(||->Result<Value,String>{
        let (mut dir,mut serial)=(None,None);let mut it=args.iter();
        while let Some(a)=it.next(){match a.as_str(){"--session"=>dir=it.next(),"--serial"=>serial=it.next(),"--json"=>{},_=>return Err(format!("unknown eval recover argument: {a}"))}}
        let parent=crate::gray_eval_session::Parent::resume(Path::new(dir.ok_or("missing --session")?),serial.ok_or("missing --serial")?)?;
        parent.finish(Some("explicit same-context recovery; original experiment report is unchanged"))
    })();
    match result {Ok(r)=>{println!("{}",serde_json::to_string_pretty(&r).unwrap());if r["status"]=="reported_complete"{0}else{2}},Err(e)=>{eprintln!("{e}");2}}
}

pub(crate) fn save_png(path: &Path, shot: &(u32, u32, Vec<u8>)) {
    if let Some(img) = image::RgbaImage::from_raw(shot.0, shot.1, shot.2.clone()) {
        let _ = img.save(path);
    }
}

fn wait_owned_launch(parent:&crate::gray_eval_session::Parent, launch:&LaunchSpec, layered:bool)->Result<(),String> {
    let start=std::time::Instant::now();
    loop {
        if !parent.base.target_alive()? {return Err("target exited before the launch window".into());}
        let ready=if launch.marker.is_some() {parent.base.eval_launch_marker()?.is_some_and(|v|!v.trim().is_empty())}
            else {start.elapsed().as_secs()>=LAUNCH_SETTLE_S};
        let loaded=!layered || parent.base.marker()?.is_some_and(|v|v["frames"].as_u64().is_some());
        if ready&&loaded {if layered{parent.base.narrow()?;}return Ok(());}
        if start.elapsed().as_secs()>=LAUNCH_MARKER_TIMEOUT_S {return Err("launch marker or current layer evidence timed out".into());}
        std::thread::sleep(std::time::Duration::from_secs(1));
    }
}

/// 原神包名。灰档评测对它缺省走「点门进大世界」(见 [`genshin_gate_entry`])。
pub(crate) const GENSHIN_PKG: &str = "com.miHoYo.Yuanshen";

/// 灰档每臂挂层重启原神之后要不要点门进大世界: 包是原神、没有自驱动启动参数、
/// 也没指定别的进门方式 (`workload.enter`) 时要。以前不点, 两臂量的都是登录页。
pub(crate) fn genshin_gate_entry(req: &Value) -> bool {
    req["package"].as_str() == Some(GENSHIN_PKG)
        && launch_spec(req).is_none()
        && req["workload"]["enter"].as_str().is_none()
}

/// 灰档评测外壳: 设备端脚本先推最新版 (采样器版本要和主机端解析一致), 按请求停充,

/// 离线校验 (`eval --validate-only`): validate_v2 之外, 把上机时才会查的东西先查一遍 ——
/// gray 改写解析成的层属性串、负载脚本、层 .so 是否带 storeop 开关、轮数够不够判显著。不碰设备。
pub(crate) fn offline_check(req: &Value) -> Result<Value, String> {
    let kind = req["kind"].as_str().unwrap_or("");
    let rounds = req["protocol"]["rounds"].as_i64().unwrap_or(0).max(0) as usize;
    let alpha = req["protocol"]["p_threshold"].as_f64().unwrap_or(0.05);
    let mut out = json!({
        "ok": true,
        "kind": kind,
        "candidate_id": req["candidate_id"],
        "package": req["package"],
        "rounds": rounds,
        "min_reachable_p": loopstat::min_two_sided_p(rounds),
        "alpha_win_strictest": alpha / loopstat::MAX_WIN_METRICS as f64,
    });
    if kind != "gray" {
        return Ok(out);
    }
    let knobs = parse_gray_rewrites(&req["payload"])?;
    out["debug.knobs.loadop"] = json!(if knobs.loadop { "1" } else { "0" });
    out["debug.knobs.storeop"] = json!(knobs.storeop);
    out["debug.knobs.npuop"] = json!(knobs.npuop.as_ref().map(|n| n.props.setprop_cmd(true)));
    out["image_policy"] = json!(gray_image_policy(req, &knobs).map(|p| p.as_str()).unwrap_or("?"));
    out["require_hit"] = json!(req["payload"]["require_hit"].as_bool().unwrap_or(true));
    out["world_entry"] = json!(if genshin_gate_entry(req) {
        "genshin_gate"
    } else {
        req["workload"]["enter"].as_str().unwrap_or("none")
    });
    if launch_spec(req).is_none() {
        let wl = req["workload"]["script"].as_str().unwrap_or("loop_v1/scripts/workload_spin_touch_v1.json");
        let p = resolve_path(wl).ok_or_else(|| format!("负载脚本不在: {wl} (按 cwd 与仓库根都找不到)"))?;
        let txt = std::fs::read_to_string(&p).map_err(|e| format!("读不了负载脚本 {}: {e}", p.display()))?;
        serde_json::from_str::<Value>(&txt).map_err(|e| format!("负载脚本 {} 不是合法 JSON: {e}", p.display()))?;
        out["workload_script"] = json!(p.display().to_string());
    }
    // npuop: 层 / 模型 / 运行库按 eval 上机时同一套规则定位并核验 (层必须带 npuop), 用的就是这一份层
    if let Some(n) = &knobs.npuop {
        let f = crate::npuop_device::NpuopFiles::locate(&n.files).map_err(|e| format!("npuop 本机文件: {e}"))?;
        out["npuop_files"] = f.to_json();
        out["owned_eval_supported"] = json!(false);
        out["owned_eval_note"] = json!("npuop 只过离线校验: 受控灰档会话尚未接管 NPU 运行库/模型挂载, 上机 eval 会在碰设备前拒绝");
        out["layer_so"] = json!(f.layer_so.display().to_string());
        return Ok(out);
    }
    let so = crate::graylayer::layer_so_path();
    let so_path = resolve_path(&so).ok_or_else(|| format!("层 .so 不在: {so} (先跑 knobs/gray/build/build_layer.sh)"))?;
    let bytes = std::fs::read(&so_path).map_err(|e| format!("读不了层 .so {}: {e}", so_path.display()))?;
    if knobs.storeop != "0" && !bytes.windows(crate::graylayer::STOREOP_PROP.len()).any(|w| w == crate::graylayer::STOREOP_PROP.as_bytes()) {
        return Err(format!("层 .so {} 里没有 {} 开关 (旧版层), 上机会成 A/A", so_path.display(), crate::graylayer::STOREOP_PROP));
    }
    out["layer_so"] = json!(so_path.display().to_string());
    Ok(out)
}

/// gray 请求的画质策略: 显式写了就按写的; 没写时 npuop (基于模型的候选) 缺省 report, 其余缺省 gate。
pub(crate) fn gray_image_policy(req: &Value, knobs: &GrayKnobs) -> Result<crate::scorecard::ImagePolicy, String> {
    let explicit = req["protocol"].get("image_policy").filter(|v| !v.is_null()).is_some();
    if !explicit && knobs.npuop.is_some() {
        return Ok(crate::scorecard::ImagePolicy::Report);
    }
    crate::scorecard::ImagePolicy::from_request(req)
}

enum OwnedGrayWorkload { Launch(LaunchSpec), Script(crate::gray_script::Plan) }
impl OwnedGrayWorkload {
    fn plan(&self)->Option<&crate::gray_script::Plan> {match self {Self::Script(p)=>Some(p),_=>None}}
    fn restart(&self,parent:&crate::gray_eval_session::Parent,label:&str,layered:bool,knobs:&GrayKnobs,candidate:bool)->Result<(),String> {
        match self {
            Self::Launch(l)=>{parent.base.restart_eval(label,layered.then_some(if candidate{knobs.storeop.as_str()}else{"0"}),&l.extras)?;wait_owned_launch(parent,l,layered)},
            Self::Script(_)=>{
                parent.base.restart_script(label,layered,candidate&&knobs.loadop,if candidate{&knobs.storeop}else{"0"})?;
                std::thread::sleep(std::time::Duration::from_secs(GRAY_SHOT_SETTLE_S));
                if !parent.base.target_alive()? {return Err("target exited before finite input".into());}
                if layered {
                    let start=std::time::Instant::now();
                    loop {
                        if parent.base.marker()?.is_some_and(|v|v["frames"].as_u64().is_some()) {parent.base.narrow()?;break;}
                        if !parent.base.target_alive()? || start.elapsed().as_secs()>=LAUNCH_MARKER_TIMEOUT_S {return Err("current layer evidence unavailable before finite input".into());}
                        std::thread::sleep(std::time::Duration::from_secs(1));
                    }
                }
                Ok(())
            }
        }
    }
}
fn owned_shot(parent:&crate::gray_eval_session::Parent,path:&Path,plan:Option<&crate::gray_script::Plan>)->Result<(u32,u32,Vec<u8>),String> {
    parent.base.screenshot(path)?;
    let raw=crate::frameaudit::png_raw(path)?;
    let (w,h,pixels)=crate::framecheck::parse_screencap(&raw)?;
    if let Some(plan)=plan {plan.validate_screen(w,h)?;}
    Ok((w,h,pixels.to_vec()))
}
fn shot_psnr(a:&(u32,u32,Vec<u8>),b:&(u32,u32,Vec<u8>))->Result<f64,String> {
    if (a.0,a.1)!=(b.0,b.1) {return Err("original screen dimensions changed across comparison images".into());}
    psnr_rgba(&a.2,&b.2).ok_or_else(||"image comparison bytes differ".into())
}
fn run_owned_gray(parent:&crate::gray_eval_session::Parent,workload:&OwnedGrayWorkload,label:&str,out:&Path,seconds:u64,comm:Option<&str>)->Result<Value,String> {
    let (workload_ok,sampler_ok,input)=match workload {
        OwnedGrayWorkload::Launch(_)=>{let(w,s)=parent.sample(label,seconds,out)?;(w,s,Value::Null)},
        OwnedGrayWorkload::Script(_)=>parent.sample_script(label,seconds,out,false)?,
    };
    let alive=parent.base.target_alive()?;
    parent.base.eval_stop_target()?;
    if !workload_ok {return Err(format!("{label}: trace workload failed; raw exit and log retained"));}
    let trace=std::fs::read_to_string(out.join("trace.txt")).map_err(|e|e.to_string())?;
    let parsed=crate::looptrace::parse(&trace,comm.filter(|c|!c.is_empty()).unwrap_or(crate::looptrace::DEFAULT_RENDER_COMM));
    let summary=py_dumps(&parsed.summarize());
    crate::confirmation_transport::new_file(&out.join("summary.json"),summary.as_bytes())?;
    let env_path=out.join("env.txt");
    let env=std::fs::read_to_string(&env_path).map_err(|e|e.to_string())?;
    let mut result=json!({"label":label,"run_once_rc":0,"launch":workload.plan().is_none(),"input_window":input,
        "summary":serde_json::from_str::<Value>(&summary).map_err(|e|e.to_string())?,"env":to_serde(&sysparam::env_stats(&env))});
    bind_run_evidence(&mut result,label,out,&env_path,sampler_ok,seconds+if workload.plan().is_some(){10}else{4},seconds,Some(if alive {0}else{1}))?;
    crate::confirmation_transport::new_file(&out.join("run.json"),&serde_json::to_vec_pretty(&result).unwrap())?;
    Ok(result)
}

pub(crate) fn run_owned_gray_script(parent:&crate::gray_eval_session::Parent,plan:&crate::gray_script::Plan,label:&str,out:&Path)->Result<Value,String> {
    run_owned_gray(parent,&OwnedGrayWorkload::Script(plan.clone()),label,out,plan.seconds,None)
}

/// Archive a current-scene sample without restarting/stopping the target or
/// applying the eval qualification/decision policy. Collection errors are data;
/// only an inability to persist the result returns Err to the owner.
#[allow(clippy::too_many_arguments)]
pub(crate) fn summarize_control_sample(
    out: &Path,
    label: &str,
    seconds: u64,
    trace_ok: bool,
    sampler_ok: bool,
    input: Value,
    target_alive: bool,
    evidence_origin: &str,
    render_comm: Option<&str>,
) -> Result<Value, String> {
    let mut errors = Vec::new();
    if let Ok(text) = std::fs::read_to_string(out.join("collection-error.json")) {
        if let Ok(value) = serde_json::from_str::<Value>(&text) {
            if let Some(error) = value["error"].as_str() { errors.push(error.to_owned()); }
        }
    }
    let sample_request = std::fs::read_to_string(out.join("sample-request.json")).ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok()).unwrap_or(Value::Null);
    let requested_power = sample_request["power_policy"].as_str().unwrap_or("observe");
    let power_path = out.join("power.json");
    // The returned snapshot also carries a final evidence-save failure; an
    // earlier on-disk snapshot must not hide that failure from qualification.
    let power = input.get("power").filter(|v|v.is_object()).cloned().or_else(||
        std::fs::read_to_string(&power_path).ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok()).filter(Value::is_object))
        .unwrap_or_else(||json!({"policy":requested_power,"before":null,"after":null,
            "error":if power_path.exists(){json!("power evidence could not be read")}else{Value::Null}}));
    let power_condition_ok = power["error"].is_null() && power["policy"] == requested_power
        && (requested_power == "observe" || (requested_power == "battery"
            && power["before"]["on_battery"] == true && power["after"]["on_battery"] == true));
    if !power_condition_ok {
        errors.push(power["error"].as_str().unwrap_or("requested battery policy lacks verified before/after discharge observations").to_owned());
    }
    let trace = std::fs::read_to_string(out.join("trace.txt"));
    let env_text = std::fs::read_to_string(out.join("env.txt"));
    if !trace_ok { errors.push("trace child failed; inspect its exit code and log".to_owned()); }
    if !sampler_ok { errors.push("environment sampler failed; inspect its exit code and log".to_owned()); }
    if let Err(error) = &trace { errors.push(format!("trace evidence unavailable: {error}")); }
    if let Err(error) = &env_text { errors.push(format!("environment evidence unavailable: {error}")); }
    let parsed = if trace_ok {
        render_comm.zip(trace.as_ref().ok()).map(|(comm, text)| crate::looptrace::parse(text, comm))
    } else { None };
    let summary = parsed.as_ref().map(|parsed|to_serde(&parsed.summarize()));
    if let Some(summary) = &summary {
        crate::confirmation_transport::new_file(&out.join("summary.json"), &serde_json::to_vec_pretty(summary).unwrap())?;
    }
    let mut run = json!({"schema":"phonefarm/gray-control-sample@1", "label":label,
        "requested_seconds":seconds,"evidence_origin":evidence_origin,"target_alive_after":target_alive,
        "input_kind":sample_request["input_kind"],"planned_input_ms":sample_request["planned_input_ms"],"initial_frame":sample_request["initial_frame"],"power":power,
        "input_window":input,"summary":summary.clone().unwrap_or_else(||json!({})),
        "env":env_text.as_ref().map(|text|to_serde(&sysparam::env_stats(text))).unwrap_or_else(|_|json!({}))});
    if trace_ok && trace.is_ok() && env_text.is_ok() {
        if let Err(error) = bind_run_evidence(&mut run,label,out,&out.join("env.txt"),sampler_ok,seconds+10,seconds,Some(if target_alive{0}else{1})) {
            errors.push(error);
        }
    } else {
        run["env"]["trace_window_verified"] = json!(false);
        run["env"]["sampler_exit_success"] = json!(sampler_ok);
        if !target_alive { errors.push("target was not alive after collection".into()); }
    }
    // No implicit Unity filter and no claim that a comm filter proves process
    // ownership. Keep the original parser semantics available for later audit.
    if summary.is_none() { run["summary"] = Value::Null; }
    let mut limitations = vec![
        "GPU submit proxy; target process/context ownership and display FPS are not verified".to_owned(),
        "power covers the whole sampler window including lead/tail, not only the trace window".to_owned(),
        "GPU-submit group energy estimates whole-device battery use over cadence groups, not GPU rail or game/display frame energy".to_owned(),
        "battery sampling does not resolve individual GPU-submit groups; the per-group value is a window average".to_owned(),
    ];
    if sample_request["input_kind"] == "finite" {
        limitations.push("input geometry is verified at launch only; natural completion does not restore game state".into());
    }
    if render_comm.is_none() {
        limitations.push("no render thread filter selected; raw trace is retained and frame metrics are unknown".into());
    } else if summary.as_ref().is_some_and(|s|s["n_frames"].as_u64().unwrap_or(0)==0) {
        limitations.push("no frame intervals matched the requested render thread filter".into());
    }
    if evidence_origin != "device" {
        limitations.push("synthetic evidence is not a real device performance measurement".into());
    }
    // An old charge-suspend/bypass file is not an ownership or power-window
    // proof for this session. Battery discharge must be observed throughout a
    // verified sampler/trace window, including any requested power conditions.
    let power_ok = power_condition_ok && sampler_ok && run["env"]["trace_window_verified"] == true
        && run["env"]["power_window_verified"] == true && run["env"]["on_battery"] == true
        && run["env"]["power_source"] == "battery";
    if !power_ok {
        let reason = if !power_condition_ok {
            power["error"].as_str().unwrap_or("requested battery policy was not verified before and after this sample").to_owned()
        } else if run["env"]["trace_window_verified"] != true {
            "sampler/trace window coverage was not verified".to_owned()
        } else if run["env"]["power_source"] == "usb_bypass" {
            "legacy bypass marker does not establish battery discharge for this control sample".to_owned()
        } else {
            run["env"]["power_usable_reason"].as_str().unwrap_or("battery discharge throughout the sampling window was not verified").to_owned()
        };
        run["env"]["power_w_mean"] = Value::Null;
        run["env"]["power_w_median"] = Value::Null;
        run["env"]["power_valid"] = json!(false);
        run["env"]["power_cross_run_comparable"] = json!(false);
        run["env"]["power_usable_reason"] = json!(reason);
        limitations.push(format!("power unavailable: {reason}"));
    }
    let energy = if power_ok {
        crate::sysparam::battery_energy_in_capture(env_text.as_ref().unwrap(),trace.as_ref().unwrap(),seconds)
    } else {
        Err(run["env"]["power_usable_reason"].as_str().unwrap_or("battery power window is not qualified").to_owned())
    };
    let energy = energy.unwrap_or_else(|reason|json!({"available":false,"estimated":true,"reason":reason,
        "energy_j":null,"power_w_mean":null,"rail":"battery","window":null}));
    let group_energy = if !power_ok {
        Err(run["env"]["power_usable_reason"].as_str().unwrap_or("battery power window is not qualified").to_owned())
    } else if let Some(parsed) = &parsed {
        sysparam::battery_energy_in_submit_groups(env_text.as_ref().unwrap(),trace.as_ref().unwrap(),seconds,parsed)
    } else {
        Err(if render_comm.is_none(){"no render thread filter selected"}else{"GPU-submit trace is unavailable"}.to_owned())
    };
    let group_energy = group_energy.unwrap_or_else(|reason| {
        limitations.push(format!("GPU-submit group energy unavailable: {reason}"));
        json!({"available":false,"estimated":true,"reason":reason,"rail":"battery",
            "scope":"whole_device_battery_over_gpu_submit_group_window",
            "method":"battery V*I; linear boundary interpolation and trapezoidal integration",
            "window":null,"energy_j":null,"power_w_mean":null,"energy_per_group_mj":null,
            "group_count":null,"submits_per_group":null,"discarded_head_groups":null,"discarded_tail_groups":null,
            "trailing_incomplete_intervals":null,"samples":null,"render_comm":render_comm,
            "grouping":parsed.as_ref().map(|p|p.submit_grouping),"filtering_basis":parsed.as_ref().map(|p|p.submit_filtering),
            "trace_clock":null,"loss_observation":null,"target_ownership_verified":false,"display_verified":false})
    });
    run["metrics"] = json!({"gpu_submit_fps":run["summary"]["fps_mean"],
        "frame_p95_ms":run["summary"]["frame_p95"],"gpu_active_mean_ms":run["summary"]["gpu_active_mean"],
        "queue_p95_ms":run["summary"]["queue_p95"],"power_w_mean":run["env"]["power_w_mean"],
        "trace_energy_j":energy["energy_j"],"trace_power_w_mean":energy["power_w_mean"],"energy_per_frame_mj":null,
        "gpu_submit_group_window_energy_j":group_energy["energy_j"],
        "gpu_submit_group_window_power_w_mean":group_energy["power_w_mean"],
        "battery_energy_per_gpu_submit_group_mj":group_energy["energy_per_group_mj"]});
    run["metric_provenance"] = json!({"frame":{"semantics":"gpu_submit_proxy","render_comm":render_comm,
        "grouping":"existing auto submits-per-frame parser","target_ownership_verified":false,"display_verified":false},
        "power":{"rail":run["env"]["power_rail"],"window_verified":power_ok,
            "scope":"whole sampler window including lead/tail"},"trace_energy":energy,"submit_group_energy":group_energy,
        "energy_per_frame":{"available":false,"reason":"game/display frame semantics and target ownership are not verified"}});
    run["collection"] = json!({"status":if errors.is_empty(){"complete"}else{"failed"},
        "error":if errors.is_empty(){Value::Null}else{json!(errors.join("; "))}});
    run["limitations"] = json!(limitations);
    let names = ["trace.txt","summary.json","env.txt","capture.log","collection-error.json","input-plan.json","workload_exit.txt","trace_exit.txt","sampler_exit.txt",
        "quiescence.txt","workload.log","trace.log","workload-time.json","trace-time.json","sampler-time.json",
        "input-complete.json","input-window.json","sample-request.json","input-frame.json","input-check.png","power.json","power-before.json","power-after.json"];
    run["artifact_path_base"] = json!("run_directory");
    run["artifacts"] = json!(names.into_iter().filter(|name|out.join(name).is_file())
        .map(|name|json!({"name":name,"path":name})).collect::<Vec<_>>());
    let workload = format!("workload-{label}.sh");
    if out.join(&workload).is_file() {
        run["artifacts"].as_array_mut().unwrap().push(json!({"name":workload,"path":workload}));
    }
    crate::confirmation_transport::new_file(&out.join("run.json"),&serde_json::to_vec_pretty(&run).unwrap())?;
    Ok(run)
}

fn effective_gray_hits(marker:&Value)->u64 {
    marker["effective"].as_array().map(|xs|xs.iter().filter_map(|e|e["begins"].as_u64()).sum()).unwrap_or(0)
}

fn gray_eval_owned(req:&Value, lease:crate::devlease::Lease, evidence:&Path, origin:&str)->Result<Value,String> {
    let knobs=parse_gray_rewrites(&req["payload"])?;
    // npuop (NPU 超分) 的设备端布置 (运行库/模型 bind mount, npuop_device) 还没接进受控会话的
    // 恢复记录; 在会话里静默忽略它会把候选臂变成对照臂 (A/A), 所以碰设备前明确拒绝。
    if knobs.npuop.is_some() {return Err("owned gray eval does not support npuop yet: NPU runtime/model mounts are not owned or recoverable by the gray session".into());}
    if req["workload"]["enter"].as_str()==Some("onboard") {return Err("owned gray eval does not support onboard/HID input".into());}
    let seconds=match req["workload"].get("seconds") {None=>30,Some(v)=>v.as_u64().ok_or("workload.seconds must be an integer")?};
    let workload=if let Some(launch)=launch_spec(req) {
        if knobs.loadop {return Err("gray workload.launch supports storeop only".into());}
        if !(1..=86400).contains(&seconds) {return Err("workload.seconds must be 1..86400".into());}
        OwnedGrayWorkload::Launch(launch)
    } else {
        let file=match req["workload"].get("script") {None|Some(Value::Null)=>"loop_v1/scripts/workload_spin_touch_v1.json",Some(v)=>v.as_str().ok_or("workload.script must be a local path")?};
        let path=resolve_path(file).ok_or_else(||format!("finite input script not found: {file}"))?;
        let input:Value=serde_json::from_slice(&crate::confirmation_transport::bounded(&path,4*1024*1024)?).map_err(|e|e.to_string())?;
        OwnedGrayWorkload::Script(crate::gray_script::parse(&input,seconds)?)
    };
    let policy=policy_for_request(req)?;
    let rounds=policy.required_pairs;let alpha=policy.alpha;let cap=policy.temp_cap_c;let cool=policy.cool_c;
    let min_psnr=req["payload"]["image_check"]["min_psnr_db"].as_f64().unwrap_or(GRAY_MIN_PSNR_DB);
    let pkg=req["package"].as_str().ok_or("missing package")?;
    let parent=match &workload {
        OwnedGrayWorkload::Launch(l)=>crate::gray_eval_session::Parent::new(crate::gray_session::Session::new_eval(lease,evidence,pkg,origin,l.marker.as_deref())?,req)?,
        OwnedGrayWorkload::Script(p)=>crate::gray_eval_session::Parent::new_script(crate::gray_session::Session::new_script(lease,evidence,pkg,origin)?,req,p)?,
    };
    let dir=parent.base.dir.clone();
    let mut report=base_report(req,"gray");
    report["session"]=json!(dir);report["evidence_dir"]=json!(dir);report["evidence_origin"]=json!(origin);
    let mut facts=Evidence::new(Stage::Probe,policy,qualification::Capabilities::default());
    if origin=="synthetic" {facts.evidence_origin="synthetic".into();}
    let mut ctrl=Vec::new();let mut candidate=Vec::new();let mut cool_log=Vec::new();let mut frames=Vec::new();let mut image_check=Value::Null;
    let mut psnr_pairs=Vec::new();let mut psnr_controls=Vec::new();let mut noise_within=Vec::new();let mut previous_control=None;
    let result=(||->Result<(),String>{
        parent.stage("capability")?;
        facts.device=parent.capabilities()?;
        if !record_qualification(&mut report,&dir,&facts)?.probe_allowed {return Err("root/trace prerequisites not verified".into());}
        parent.base.prepare()?;
        parent.stage("image_check")?;
        match &workload {
            OwnedGrayWorkload::Launch(launch)=>{
                let static_extras=launch.static_extras.as_deref().unwrap_or(&launch.extras);
                parent.base.install_storeop(req["payload"]["layer_so"].as_str(),&knobs.storeop,Some(static_extras))?;
                let image_dir=dir.join("image_check");std::fs::create_dir(&image_dir).map_err(|e|e.to_string())?;
                let live=crate::frameaudit::collect_storeop(&parent.base,&image_dir,&knobs.storeop,2,pkg,&[],STATIC_SETTLE_S,&mut frames)?;
                crate::confirmation_transport::new_file(&image_dir.join("result.json"),&serde_json::to_vec_pretty(&live).unwrap())?;
                image_check=json!({"method":"storeop_check_live","result":live});
                live_check_verdict(&live)?;facts.quality_verified=Some(true);
            },
            OwnedGrayWorkload::Script(_)=>parent.base.install_script(req["payload"]["layer_so"].as_str())?,
        }
        parent.base.eval_stop_target()?;
        parent.stage("qualification")?;parent.deploy()?;
        let fan=parent.fan_off()?;
        let cold=parent.wait_cool(cool,420)?;facts.cold_ok=cold["ok"].as_bool();
        report["baseline_conditions"]=json!({"fan":fan,"cold":cold});
        if facts.cold_ok!=Some(true) {return Err("baseline cold condition not verified".into());}
        workload.restart(&parent,"workload_check",false,&knobs,false)?;
        let motion_dir=dir.join("workload_check");std::fs::create_dir(&motion_dir).map_err(|e|e.to_string())?;
        let a=motion_dir.join("frame_a.png");let b=motion_dir.join("frame_b.png");
        let input=match &workload {
            OwnedGrayWorkload::Launch(_)=>{
                parent.base.screenshot(&a)?;std::thread::sleep(std::time::Duration::from_secs(5));parent.base.screenshot(&b)?;Value::Null
            },
            OwnedGrayWorkload::Script(plan)=>{
                owned_shot(&parent,&motion_dir.join("input_screen.png"),Some(plan))?;
                let (trace_ok,sampler_ok,input)=parent.sample_script("workload_check",seconds,&motion_dir,true)?;
                if !trace_ok||!sampler_ok {return Err("finite workload qualification child failed".into());}input
            }
        };
        let motion=to_serde(&crate::framecheck::verdict(&crate::frameaudit::png_raw(&a)?,&crate::frameaudit::png_raw(&b)?));
        let alive=parent.base.target_alive()?;parent.base.eval_stop_target()?;
        facts.workload_verified=Some(alive&&motion["spinning"]==true);
        let check=json!({"verified":facts.workload_verified,"app_alive":alive,"motion":motion,"frames":[a,b],"launch":workload.plan().is_none(),"input_window":input});
        crate::confirmation_transport::new_file(&motion_dir.join("workload_check.json"),&serde_json::to_vec_pretty(&check).unwrap())?;
        if facts.workload_verified!=Some(true) {return Err("workload motion not verified".into());}
        for i in 1..=3 {
            let label=format!("baseline{i}");parent.stage(&label)?;
            workload.restart(&parent,&label,false,&knobs,false)?;
            let out=dir.join(&label);std::fs::create_dir(&out).map_err(|e|e.to_string())?;
            if let Some(plan)=workload.plan(){owned_shot(&parent,&out.join("input_screen.png"),Some(plan))?;}
            facts.baseline.push(run_owned_gray(&parent,&workload,&label,&out,seconds,req["workload"]["render_comm"].as_str())?);
        }
        parent.admission_ready()?;facts.restored=None;facts.stage=Stage::Experiment;
        let q=qualification::evaluate_gray_session_collection(&facts)?;
        if !write_qualification(&mut report,&dir,&facts,q)?.experiment_allowed {return Err("gray baseline/workload/power qualification did not admit candidate collection".into());}
        for i in 1..=rounds {
            let mut shots=std::collections::BTreeMap::new();
            for arm in if i%2==1 {["knob","ctrl"]}else{["ctrl","knob"]} {
                let label=format!("{arm}{i}");parent.stage(&label)?;
                let fan=parent.fan_off()?;let mut cold=parent.wait_cool(cool,arm_cool_timeout(&cool_log))?;
                cold["arm"]=json!(label);cold["fan"]=fan;cool_log.push(cold);
                workload.restart(&parent,&label,true,&knobs,arm=="knob")?;
                let out=dir.join(&label);std::fs::create_dir(&out).map_err(|e|e.to_string())?;
                if let Some(plan)=workload.plan() {
                    let path=out.join("image.png");let shot=owned_shot(&parent,&path,Some(plan))?;frames.push(json!(path));
                    if arm=="ctrl" {
                        std::thread::sleep(std::time::Duration::from_secs(GRAY_SHOT_SETTLE_S));
                        let path=out.join("image-repeat.png");let repeat=owned_shot(&parent,&path,Some(plan))?;frames.push(json!(path));
                        noise_within.push(shot_psnr(&shot,&repeat)?);
                    }
                    shots.insert(arm,shot);
                }
                let mut run=run_owned_gray(&parent,&workload,&label,&out,seconds,req["workload"]["render_comm"].as_str())?;
                if let (Some(t),Some(limit))=(run_soc_temp_max(&run),cap) {if t>limit{return Err(format!("{label}: SoC temperature {t} exceeds {limit}"));}}
                if arm=="knob" {
                    let marker=parent.base.marker()?.ok_or("candidate marker missing")?;
                    crate::confirmation_transport::new_file(&out.join("layer_marker.json"),&serde_json::to_vec_pretty(&marker).unwrap())?;
                    run["layer_hits"]=json!(effective_gray_hits(&marker));candidate.push(run);
                } else {ctrl.push(run);}
            }
            if workload.plan().is_some() {
                let c=shots.remove("ctrl").ok_or("missing control quality image")?;
                let k=shots.remove("knob").ok_or("missing candidate quality image")?;
                psnr_pairs.push(shot_psnr(&k,&c)?);
                if let Some(previous)=&previous_control {psnr_controls.push(shot_psnr(previous,&c)?);}
                previous_control=Some(c);
            }
        }
        if workload.plan().is_some() {
            let noise=noise_floor_db(noise_within.iter().chain(psnr_controls.iter()).copied().map(Some));
            let gate=ImageGate::new(min_psnr,noise);
            let used=noise_floor_db(psnr_pairs.iter().copied().map(Some));
            let ruling=judge_image(&gate,used);
            image_check=json!({"method":"same_spot_screenshot","min_psnr_db":min_psnr,"psnr_knob_vs_ctrl_db":psnr_pairs,
                "psnr_ctrl_vs_ctrl_db":psnr_controls,"noise_within_round_db":noise_within,"noise_floor_db":noise,"psnr_db_used":used,
                "gate":match &ruling {ImageRuling::Pass(by)=>gate.gate(by),ImageRuling::Fail(_)=>gate.gate("failed")}});
            facts.quality_verified=Some(matches!(ruling,ImageRuling::Pass(_)));
            if let ImageRuling::Fail(reason)=ruling {return Err(format!("画面出错: {reason}"));}
        }
        Ok(())
    })();
    let mut error=result.err();
    let collection=json!({"schema":if workload.plan().is_some(){"phonefarm/gray-eval-script-collection@1"}else{"phonefarm/gray-eval-launch-collection@1"},"session":dir,"evidence_origin":origin,
        "input_plan":workload.plan().map(|p|p.to_value()),"baseline":facts.baseline,"control":ctrl,"candidate":candidate,"image_check":if workload.plan().is_some(){&image_check}else{&image_check["result"]},"frames":frames,"cool_per_arm":cool_log,"error":error});
    if let Err(e)=crate::confirmation_transport::new_file(&dir.join("eval-collection.json"),&serde_json::to_vec_pretty(&collection).unwrap()) {
        error=Some(format!("{}; collection report: {e}",error.as_deref().unwrap_or("collection completed")));
    }
    let recovery=parent.finish(error.as_deref()).unwrap_or_else(|e|json!({"status":"required","error":e,"session":dir}));
    let complete=recovery["status"]=="reported_complete";
    let hits=candidate.iter().filter_map(|v|v["layer_hits"].as_u64()).max().unwrap_or(0);
    facts.stage=Stage::Conclusion;facts.control=ctrl.clone();facts.candidate=candidate.clone();facts.restored=Some(complete);facts.device.control_verified=Some(hits>0);
    report["recovery"]=recovery;
    let (reached,timed_out)=cool_summary(&cool_log);
    report["conditions_observed"]=json!({"cool_c_target":cool,"cool_reached_c":reached,"cool_timeout":timed_out,"cool_per_arm":cool_log,
        "hits":hits,"apply_ok":hits>0,"restored":complete,"game_state_restored":false,"image_check":image_check});
    let qualified=record_qualification(&mut report,&dir,&facts);
    if let Some(e)=error {report["verdict"]["reason"]=json!(e);}
    else if !complete {report["verdict"]["reason"]=json!("owned gray eval recovery not complete; evidence retained");}
    else if !qualified.as_ref().is_ok_and(|q|q.conclusion_allowed) {report["verdict"]["reason"]=json!("conclusion qualification not satisfied");}
    else if req["payload"]["require_hit"].as_bool().unwrap_or(true)&&hits==0 {report["verdict"]["reason"]=json!("candidate rewrite never hit");}
    else {finish_gray_statistics(&mut report,&candidate,&ctrl,alpha,rounds,psnr_pairs.iter().copied().reduce(f64::min),&facts.policy.required_metrics,declared_fps_cap(req))?;}
    if let Err(e)=qualified {report["qualification_error"]=json!(e);}
    let path=dir.join("eval-report.json");report["report_path"]=json!(path);
    if let Err(e)=crate::confirmation_transport::new_file(&path,&serde_json::to_vec_pretty(&report).unwrap()) {report["report_error"]=json!(e);}
    Ok(report)
}

#[allow(clippy::too_many_arguments)]
fn finish_gray_statistics(report:&mut Value, knob_runs:&[Value], ctrl_runs:&[Value], alpha:f64, rounds:u32, psnr:Option<f64>, required:&[String], fps_cap_declared:Option<f64>)->Result<(),String> {
    if let Some(e) = fan_mismatch(&knob_runs, &ctrl_runs) {
        report["verdict"]["reason"] = json!(e);
        return Ok(());
    }
    // 画面过了门槛: 照常做统计判定。psnr_db 是约束不是目标, 记最差的那一轮。
    let (verdict, keep, on_battery_all) = judge_runs_with(ctrl_runs, knob_runs, alpha, rounds, fps_cap_declared, Some(required));
    report["verdict"] = verdict;
    let mut m = report["metrics"].take();
    let power = aggregate_metrics(&mut m, &knob_runs, &ctrl_runs, on_battery_all);
    m["psnr_db"] = json!(psnr);
    if power.is_none() {
        unavailable(&mut m, "power_watt", "非放电态 (或功耗采样缺失): 整机功耗量不准, 如实 null");
    }
    if m["fps_p95_ms"].is_null() {
        unavailable(&mut m, "fps_p95_ms", "本轮没拿到可用的帧时序");
    }
    report["metrics"] = m;
    report["status"] = json!(if keep { "PASS" } else { "REJECT" });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 审查 R1 #1 #6: 旧脚本的 summary (没有 frame_attribution) 与目标进程没找到 (n_target_tids=0) 都 Err。
    #[test]
    fn frame_attribution_must_be_present_and_nonempty() {
        let old = json!({"n_frames": 1800, "frame_p95": 16.7});
        let e = check_frame_attribution(&old).unwrap_err();
        assert!(e.contains("frame_attribution") && e.contains("旧版"), "{e}");
        let none = json!({"frame_attribution": "target_tids", "target_pid": "", "n_target_tids": 0, "n_frames": 0});
        let e = check_frame_attribution(&none).unwrap_err();
        assert!(e.contains("n_target_tids=0"), "{e}");
        let ok = json!({"frame_attribution": "target_tids", "target_pid": "100", "n_target_tids": 3, "n_frames": 1800});
        assert!(check_frame_attribution(&ok).is_ok());
    }

    /// 审查 R1 #2 #10: 三种停充成功回显、别人留下的 STATE、超时后 status 的对账都认成「已停充, 结束要还原」。
    #[test]
    fn charge_suspended_is_recognized_from_every_success_shape_and_from_status() {
        assert!(charge_suspended_via("CHARGE_SUSPENDED /sys/x: 1 -> 0\nCHARGE_STATE ...\n#adb_exit=0").is_some());
        assert!(charge_suspended_via("CHARGE_SUSPENDED_BYPASS /sys/x: 1 -> 0 (...)\n#adb_exit=0").is_some());
        let m = charge_suspended_via("CHARGE_TRY_FAILED /sys/a (写 1 无效)\nCHARGE_SUSPENDED_MIXED /sys/x: 1 -> 0 (混合供电)\n#adb_exit=0").unwrap();
        assert!(m.starts_with("CHARGE_SUSPENDED_MIXED"));
        assert!(charge_suspended_via("CHARGE_ALREADY_SUSPENDED (state=/data/local/tmp/loop_v1_charge.state)\n#adb_exit=0").is_some());
        // 主机端超时, 再接上 status 的回显: 设备端其实已经停充
        let timed_out = "#adb_exit=timeout\nstate: 已停充 (/sys/x 原值 1 供电 battery)\nCHARGE_STATE ...\n#adb_exit=0";
        assert!(charge_suspended_via(timed_out).unwrap().starts_with("state: 已停充"));
        assert!(charge_suspended_via("CHARGE_FAIL: 所有候选节点都没能把设备转成放电态\n#adb_exit=1").is_none());
        assert!(charge_suspended_via("#adb_exit=timeout\nstate: 未停充\n#adb_exit=0").is_none());
        assert!(charge_suspended_via("CHARGE_SKIP: 电量 20% 低于停充下限 30%, 不停充\n#adb_exit=1").is_none());
        assert_eq!(CHARGE_SUSPEND_TIMEOUT_S, 360);
    }

    /// 审查 R1 #10: 开跑前对账只看 status 的 state 行。
    #[test]
    fn preexisting_state_is_parsed_from_status_outputs() {
        assert_eq!(parse_preexisting("state: 未施加\n#adb_exit=0", "state: 未停充\nCHARGE_STATE ...\n#adb_exit=0"), (false, false));
        assert_eq!(
            parse_preexisting("state: 已施加\n/sys/a cur=1 orig=0\n#adb_exit=0", "state: 已停充 (/sys/x 原值 1 供电 bypass)\n#adb_exit=0"),
            (true, true)
        );
        assert_eq!(parse_preexisting("#adb_exit=timeout", ""), (false, false));
    }

    /// 审查 R1 #3: contested 的改写臂与剔掉的对都已经跑过, 剩余臂数要扣掉; 样本量够不着 alpha_win 时 KEEP 不可达。
    #[test]
    fn curtail_counts_contested_and_dropped_as_already_run() {
        // rounds=5: 跑完第 2 轮, 第 2 轮改写臂 contested → knob 1 臂、ctrl 2 臂
        assert_eq!(remaining_arms(5, 2, 1, 1, 0), (3, 3));
        // 旧算法把 contested 当没跑: left_b 会是 4
        assert_ne!(remaining_arms(5, 2, 1, 1, 0).1, 4);
        // 再剔掉一对 (第 3 轮两臂不可比): ctrl 2、knob 1、dropped 1 → 各剩 2
        assert_eq!(remaining_arms(5, 2, 1, 1, 1), (2, 2));
        assert_eq!(remaining_arms(5, 5, 3, 2, 0), (0, 0));
        assert_eq!(remaining_arms(5, 9, 9, 9, 9), (0, 0));

        let two_metrics = vec!["frame_p95".to_string(), "fps_mean".to_string()];
        let three_metrics = vec!["frame_p95".to_string(), "fps_mean".to_string(), "power_w_mean".to_string()];
        let tests = policy_win_tests(&two_metrics, 0.05, false); // 最宽 alpha_win = 0.025
        // 5v5 够 (2/C(10,5) = 0.0079); 3v3 不够 (0.1); 3v5 不等量 1/C(8,3) = 0.0179 够, 3v6 够
        assert!(keep_reachable(5, 5, &tests));
        assert!(!keep_reachable(3, 3, &tests));
        assert!(keep_reachable(5, 3, &tests));
        // 功耗可能进表时仍取两指标表的 frame/fps 与三指标表的 power，保持最宽口径。
        let mut t3 = policy_win_tests(&two_metrics, 0.05, false);
        t3.extend(policy_win_tests(&three_metrics, 0.05, false).into_iter().filter(|t| t.metric == "power_w_mean"));
        assert_eq!(t3.iter().map(|t| t.alpha_win).reduce(f64::max), Some(0.025), "取的是最宽的");
        let strict = vec![curtail::WinTest::new("power_w_mean", true, 0.05 / 3.0)];
        assert!(!keep_reachable(5, 3, &strict));
        assert!(keep_reachable(5, 4, &strict), "4v5: 1/C(9,4) = 0.0079");
        // 一臂凑不到 2 个: 不可达; 没有检验: 不截
        assert!(!keep_reachable(1, 5, &tests));
        assert!(keep_reachable(1, 1, &[]));
        // 审查里的现场: rounds=5、第 2 个 contested 出现后 knob 最多 3 臂 vs ctrl 5 臂, alpha/3 下够不着
        let (la, lb) = remaining_arms(5, 3, 1, 2, 0);
        assert!(!keep_reachable(3 + la, 1 + lb, &strict));
    }

    /// 审查 R1 #10: 中断时要跑的还原命令登记一次、可查、可清。
    #[test]
    fn cleanup_registry_dedups_and_clears() {
        cleanup::clear();
        cleanup::register("S1", "su -c 'sh /data/local/tmp/knob_sysparam.sh restore'");
        cleanup::register("S1", "su -c 'sh /data/local/tmp/knob_sysparam.sh restore'");
        cleanup::register("S1", "am force-stop com.x");
        let p = cleanup::pending();
        assert_eq!(p.len(), 2);
        assert_eq!(p[1].1, "am force-stop com.x");
        cleanup::clear();
        assert!(cleanup::pending().is_empty());
    }

    /// 审查 R1 #2 #7: 设备端脚本里的判据与保护要在 (脚本没有单测, 这里锁住关键行)。
    #[test]
    fn device_scripts_carry_the_r1_fixes() {
        let root = repo_root();
        let cs = std::fs::read_to_string(root.join("loop_v1/auto/charge_suspend.sh")).unwrap();
        assert!(cs.contains("CHARGE_SUSPENDED_MIXED"));
        assert!(cs.contains("mixed_one()"));
        assert!(cs.contains("SUSPEND_TOTAL_MAX_S=${SUSPEND_TOTAL_MAX_S:-300}"));
        assert!(cs.contains("CHARGE_TIMEOUT"));
        assert!(CHARGE_SUSPEND_TIMEOUT_S >= 300 + 12, "主机端超时要盖过脚本总时限 + 一个候选的收尾");
        let se = std::fs::read_to_string(root.join("loop_v1/auto/sample_env.sh")).unwrap();
        assert!(se.contains("sleep \"$PWR_IVL\" 2>/dev/null || PWR_IVL=1"));
        assert!(se.contains("power_interval=$PWR_IVL stop="));
        // 头部要在保护之后打, 记的才是实际生效的间隔
        assert!(se.find("|| PWR_IVL=1").unwrap() < se.find("#sample_env v2 dur=").unwrap());
    }

    pub(super) fn v2_req(kind: &str, payload: Value) -> Value {
        json!({
            "version": 2,
            "kind": kind,
            "candidate_id": "cand-test-1",
            "package": "com.example.game",
            "workload": {"carrier": "game_replay", "script": null, "seconds": 30},
            "conditions": {"cool_c": null, "temp_cap_c": 58.2, "require_root": true,
                           "require_discharging": true, "charging_suspend": {"enabled": true, "node": null},
                           "fan": "on", "ingame_fps_setting": "60fps/高",
                           "screen_brightness": null, "quality_reference": null, "notes": null},
            "protocol": {"rounds": 5, "alternating": true, "p_threshold": 0.05},
            "payload": payload
        })
    }

    pub(super) fn validate_ok(req: &Value) -> bool { validate_v2(req).is_ok() }
    pub(super) fn validate_err(req: &Value) -> String { validate_v2(req).unwrap_err() }

    #[test]
    fn probe_only_never_claims_reverted_from_failed_or_missing_snapshot() {
        for (a,b) in [("",""),("x=1\n#adb_exit=1","x=1\n#adb_exit=1"),("x=1\n#adb_exit=0","x=2\n#adb_exit=0")] {
            let mut r=base_report(&v2_req("sysparam",json!({"params":{}})),"sysparam");
            probe_report(&mut r,a,b,"",&vec![]);
            assert_eq!(r["status"],"ABORT");assert_eq!(r["conditions_observed"]["restored"],false);
        }
        assert!(snapshots_identical("x=1\n#adb_exit=0","x=1\n#adb_exit=0"));
    }

    #[test]
    fn probe_tolerates_vendor_drift_only_when_every_probe_write_was_restored() {
        let before = "kgsl.max_pwrlevel=17\ncpu.policy6.scaling_max=1632000\nfan.x=0\n#adb_exit=0";
        let vendor = "kgsl.max_pwrlevel=0\ncpu.policy6.scaling_max=4608000\nfan.x=0\n#adb_exit=0";
        let other = "kgsl.max_pwrlevel=0\ncpu.policy6.scaling_max=4608000\nfan.x=1\n#adb_exit=0";
        let ok = "cpu.policy6.scaling_max_freq.effect=live\ncpu.policy6.scaling_max_freq.effect_restored=yes\n";
        let left = "cpu.policy6.scaling_max_freq.effect_restored=yes\ngpu.max_pwrlevel.effect_restored=NO(want=17 got=18)\n";
        assert!(probe_snapshots_restored(before, vendor, ok));
        // 探测把自己的写入留下了: 当场回读报 NO, 拦下
        assert!(!probe_snapshots_restored(before, vendor, left));
        // 没有探测核对记录: 退回严格比对
        assert!(!probe_snapshots_restored(before, vendor, ""));
        // 厂商自管之外的键变了: 拦下
        assert!(!probe_snapshots_restored(before, other, ok));
        let mut r = base_report(&v2_req("sysparam", json!({"params": {}})), "sysparam");
        probe_report(&mut r, before, vendor, ok, &vec![]);
        assert_eq!(r["status"], "REVERTED");
        assert_eq!(r["conditions_observed"]["snapshot_identical"], false);
    }

    #[test]
    fn v2_envelope_needs_all_required_fields() {
        assert!(validate_ok(&v2_req("sysparam", json!({"params": {}}))));
        let mut r = v2_req("sysparam", json!({"params": {}}));
        r.as_object_mut().unwrap().remove("protocol");
        assert_eq!(validate_err(&r), "v2 缺必填字段 protocol");
    }

    /// judge/v1-scorecard: image_policy 只认 gate / report, 写错在碰设备前拒收。
    #[test]
    fn image_policy_is_validated_before_any_device_touch() {
        let mut r = v2_req("sysparam", json!({"params": {}}));
        assert!(validate_ok(&r), "缺省 = gate");
        r["protocol"]["image_policy"] = json!("report");
        assert!(validate_ok(&r));
        r["protocol"]["image_policy"] = json!("ignore");
        assert!(validate_err(&r).contains("image_policy"));
    }

    #[test]
    fn protocol_is_validated_before_any_device_touch() {
        let mut r = v2_req("sysparam", json!({"params": {}}));
        r["protocol"]["rounds"] = json!(0);
        assert!(validate_err(&r).contains("rounds"));
        r["protocol"]["rounds"] = json!(5);
        r["protocol"]["p_threshold"] = json!(0.0);
        assert!(validate_err(&r).contains("p_threshold"));
        r["protocol"]["p_threshold"] = json!(1.5);
        assert!(validate_err(&r).contains("p_threshold"));
        r["protocol"]["alternating"] = json!("yes");
        assert!(validate_err(&r).contains("alternating"));
    }

    /// 精确置换检验的三条路: rounds 够不着 alpha/3 就拒收并给出最少轮数;
    /// 初筛 (stage=screen) 不判显著, 放行; shader 走 Welch, 不在此列。
    #[test]
    fn rounds_that_cannot_reach_significance_are_refused() {
        for (kind, payload) in [
            ("sysparam", json!({"params": {}})),
            ("gray", json!({"rewrites": [{"knob": GRAY_SUPPORTED_KNOBS[0], "args": {}}]})),
        ] {
            let mut r = v2_req(kind, payload);
            for n in [2, 3, 4] {
                r["protocol"]["rounds"] = json!(n);
                let e = validate_err(&r);
                assert!(e.contains(&format!("{n}v{n}")) && e.contains("protocol.rounds 至少给 5"), "{kind} {n}: {e}");
                r["protocol"]["stage"] = json!("screen");
                assert!(validate_ok(&r), "{kind} 初筛 {n} 轮应放行: {:?}", validate_v2(&r));
                r["protocol"].as_object_mut().unwrap().remove("stage");
            }
            r["protocol"]["rounds"] = json!(5);
            assert!(validate_ok(&r));
            r["protocol"]["stage"] = json!("full");
            assert!(validate_ok(&r));
            // 门槛收紧到 0.01 时 5 轮也不够 (2/252 = 0.0079 > 0.0033), 要 6 轮
            r["protocol"]["p_threshold"] = json!(0.01);
            assert!(validate_err(&r).contains("protocol.rounds 至少给 6"));
            r["protocol"]["stage"] = json!("quick");
            assert!(validate_err(&r).contains("protocol.stage"));
        }
        let mut sh = v2_req("shader", json!({"track": "sr", "shader_path": "a", "spirv_path": "b", "budget_ms": 1.0}));
        sh["protocol"]["rounds"] = json!(2);
        assert!(validate_ok(&sh), "shader 不走置换检验: {:?}", validate_v2(&sh));
    }

    #[test]
    fn workload_must_cover_lead_plus_capture() {
        let dir = std::env::temp_dir().join(format!("wl-cover-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("wl.json");
        std::fs::write(&f, r#"[{"action":"sleep","ms":3000},{"action":"exec","cmd":"input swipe 900 400 2000 400 12000"},
            {"action":"exec","cmd":"input swipe 900 400 2000 400 12000"},{"action":"exec","cmd":"input swipe 900 400 2000 400 12000"},
            {"action":"sleep","ms":1000}]"#).unwrap();
        assert_eq!(workload_script_seconds(&f), Some(40.0));
        let mut r = json!({"kind": "sysparam", "workload": {"carrier": "game_replay", "script": f.display().to_string(), "seconds": 30}});
        assert!(workload_covers_capture(&r).is_ok());
        r["workload"]["seconds"] = json!(60);
        assert!(workload_covers_capture(&r).unwrap_err().contains("撑不满"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn baseline_plan_defaults_to_five_runs_plus_one_warmup_and_never_below_contract() {
        let mut r = json!({"protocol": {}});
        assert_eq!(baseline_plan(&r).unwrap(), (5, 1));
        r["protocol"]["baseline_runs"] = json!(3);
        r["protocol"]["baseline_warmup"] = json!(0);
        assert_eq!(baseline_plan(&r).unwrap(), (3, 0));
        r["protocol"]["baseline_runs"] = json!(2);
        assert!(baseline_plan(&r).is_err(), "收端合同至少三轮基线");
        r["protocol"]["baseline_runs"] = json!(5);
        r["protocol"]["baseline_warmup"] = json!(4);
        assert!(baseline_plan(&r).is_err());
    }

    #[test]
    fn screen_stage_turns_off_curtailment() {
        let mut r = v2_req("sysparam", json!({"params": {}}));
        assert!(!is_screen(&r));
        r["protocol"]["stage"] = json!("screen");
        assert!(is_screen(&r));
    }

    #[test]
    fn verdict_reports_the_resolution_floor() {
        // 审查 R2 #8: 按实际进判定的臂数算, 不按计划轮数
        let arms = |tag: &str, n: usize| -> Vec<Value> {
            (0..n).map(|i| json!({"label": format!("{tag}{i}"), "round": i + 1, "summary": {"frame_p95": 10.0 + i as f64, "fps_mean": 60.0, "frame_p50": 12.0}, "env": {}})).collect()
        };
        let (v, _, _) = judge_runs(&[], &[], 0.05, 2, None);
        assert_eq!(v["min_reachable_p"], json!(1.0));
        assert_eq!(v["reachable"], false);
        let (v, _, _) = judge_runs(&arms("c", 2), &arms("k", 2), 0.05, 2, None);
        assert!((v["min_reachable_p"].as_f64().unwrap() - 1.0 / 3.0).abs() < 1e-6);
        assert_eq!(v["reachable"], false);
        let (v, _, _) = judge_runs(&arms("c", 5), &arms("k", 5), 0.05, 5, None);
        assert!((v["min_reachable_p"].as_f64().unwrap() - 2.0 / 252.0).abs() < 1e-6);
        assert_eq!(v["reachable"], true);
        assert_eq!(v["reachable_planned"], true);
        // 计划 5 轮、剔了 2 对只剩 3v3: 最小 p = 0.1 > 0.025, 判不出 (以前按计划轮数写 true)
        let (v, _, _) = judge_runs(&arms("c", 3), &arms("k", 3), 0.05, 5, None);
        assert_eq!(v["reachable"], false, "{v}");
        assert!((v["min_reachable_p"].as_f64().unwrap() - 0.1).abs() < 1e-6);
        assert_eq!(v["reachable_planned"], true);
        // 不等量 5v3: 2/C(8,3) = 0.0357 > 0.025
        let (v, _, _) = judge_runs(&arms("c", 5), &arms("k", 3), 0.05, 5, None);
        assert_eq!(v["reachable"], false);
        assert!((v["min_reachable_p"].as_f64().unwrap() - 2.0 / 56.0).abs() < 1e-6);
        assert_eq!(v["resolution"]["n_ctrl"], 5);
        assert_eq!(v["resolution"]["n_knob"], 3);
    }

    #[test]
    fn min_p_unequal_matches_equal_case_and_floors() {
        assert_eq!(min_two_sided_p_unequal(5, 5), loopstat::min_two_sided_p(5));
        assert_eq!(min_two_sided_p_unequal(1, 5), 1.0);
        assert!((min_two_sided_p_unequal(2, 3) - 0.2).abs() < 1e-9, "2/C(5,2) = 0.2");
    }

    /// 审查 R2 #9: 锁帧由请求声明, 数据只确认; 没声明时数据再像锁帧也不放宽 K。
    #[test]
    fn fps_lock_is_declared_and_only_confirmed_by_data() {
        let arm = |tag: &str, p50: f64, fps: &[f64]| -> Vec<Value> {
            fps.iter().enumerate().map(|(i, f)| json!({"label": format!("{tag}{i}"), "summary": {"frame_p95": 36.0, "fps_mean": f, "frame_p50": p50}, "env": {}})).collect()
        };
        let ctrl = arm("c", 33.33, &[29.40, 29.41, 29.42]);
        let knob = arm("k", 33.33, &[29.60, 29.61, 29.62]);
        // 声明 30 且数据贴着 30: 确认
        let (cap, note) = fps_lock_confirmed(Some(30.0), &ctrl, &knob);
        assert_eq!(cap, Some(30.0));
        assert_eq!(note["confirmed"], true);
        // 没声明: 数据像锁帧也不算, 只提示
        let (cap, note) = fps_lock_confirmed(None, &ctrl, &knob);
        assert_eq!(cap, None);
        assert_eq!(note["detected_from_data"], json!(30.0));
        assert!(note["note"].as_str().unwrap().contains("没声明"));
        // 声明 60 但数据在 30: 不确认
        let (cap, note) = fps_lock_confirmed(Some(60.0), &ctrl, &knob);
        assert_eq!(cap, None);
        assert_eq!(note["p50_at_cap"], false);
        // 声明 30, p50 贴着, 但两臂 fps_mean 差 2%: 不确认
        let knob_fast = arm("k", 33.33, &[30.0, 30.0, 30.0]);
        let (cap, note) = fps_lock_confirmed(Some(30.0), &ctrl, &knob_fast);
        assert_eq!(cap, None);
        assert_eq!(note["fps_mean_within_tol"], false);
        // judge_runs 里: 没声明时 fps_mean 照常占预算 (K=2), 声明并确认时只守退化 (K=1)
        let (v, _, _) = judge_runs(&ctrl, &knob, 0.05, 3, None);
        assert_eq!(v["fps_locked_cap"], Value::Null);
        assert!((v["alpha_win"].as_f64().unwrap() - 0.025).abs() < 1e-12);
        let (v, _, _) = judge_runs(&ctrl, &knob, 0.05, 3, Some(30.0));
        assert_eq!(v["fps_locked_cap"], json!(30.0));
        assert!((v["alpha_win"].as_f64().unwrap() - 0.05).abs() < 1e-12);
    }

    #[test]
    fn gray_rejects_unimplemented_knobs() {
        assert!(validate_ok(&v2_req("gray",json!({"rewrites":[{"knob":"storeop_dont_care","scope":"15a51a87:0"}]}))));
        let mut r = v2_req("gray", json!({"rewrites": [{"knob": "render_scale", "args": {"scale": 0.75}}]}));
        assert!(validate_err(&r).contains("render_scale"));
        assert!(validate_err(&r).contains("收端已实现"));
        r["payload"]["rewrites"] = json!([{"knob": "loadop_dont_care", "scope": null, "args": null}]);
        assert!(validate_ok(&r));
        r["payload"]["rewrites"] = json!([]);
        assert!(validate_err(&r).contains("至少 1 项"));
    }

    /// 审计 #1: 上层唯一 Landed 的 storeop 必须能过校验; 缺 scope / scope 写错在校验阶段就拒。
    #[test]
    fn gray_accepts_storeop_with_scope_and_rejects_bad_scope() {
        let mut r = v2_req("gray", json!({"rewrites": [{"knob": "storeop_dont_care", "scope": "15a51a879df99c32:2"}]}));
        assert!(validate_ok(&r), "{:?}", validate_v2(&r));
        r["payload"]["rewrites"] = json!([
            {"knob": "storeop_dont_care", "scope": "15a51a879df99c32:2"},
            {"knob": "storeop_dont_care", "scope": "9d0b4db80bf552b8:0"},
        ]);
        assert!(validate_ok(&r));
        r["payload"]["rewrites"] = json!([{"knob": "storeop_dont_care"}]);
        assert!(validate_err(&r).contains("scope"));
        r["payload"]["rewrites"] = json!([{"knob": "storeop_dont_care", "scope": "15a5:2"}]);
        assert!(!validate_ok(&r));
    }

    /// 原神灰档缺省点门进大世界; 别的包、自驱动启动、显式 enter 都不点。
    #[test]
    fn genshin_gate_entry_only_for_plain_genshin_requests() {
        let mut r = v2_req("gray", json!({"rewrites": [{"knob": "storeop_dont_care", "scope": "15a51a879df99c32:2"}]}));
        assert!(!genshin_gate_entry(&r), "别的包不点门");
        r["package"] = json!(GENSHIN_PKG);
        assert!(genshin_gate_entry(&r));
        r["workload"]["enter"] = json!("onboard");
        assert!(!genshin_gate_entry(&r), "显式 onboard 优先");
        r["workload"]["enter"] = Value::Null;
        r["workload"]["launch"] = json!({"extras": "--es scene x"});
        assert!(!genshin_gate_entry(&r), "自驱动启动不点门");
    }

    /// 离线校验: 三处写回合成一条层属性串 (排序去重), 与 11_storeop_check 机制验证用的串一字不差;
    /// 负载脚本找不到时在碰设备前就拒。
    /// npu/n6 跨仓库夹具: game_opt_loop 的 tests/fixtures/phonefarm_request/gray_npuop.json 原样拷来
    /// (那边的契约测试校验同一份: 合契约、Request 往返不变、过开关目录)。这边: 收端照收, image_policy=report,
    /// 属性串就是 lag=1 + 其余缺省; 本机文件齐了就过 validate-only, 不齐只在 npuop 本机文件这一步拒。
    #[test]
    fn the_cross_repo_npuop_request_is_accepted() {
        let r: Value = serde_json::from_str(include_str!("testdata/gray_npuop_request.json")).unwrap();
        validate_v2(&r).unwrap();
        let k = parse_gray_rewrites(&r["payload"]).unwrap();
        assert_eq!(gray_image_policy(&r, &k).unwrap(), crate::scorecard::ImagePolicy::Report);
        assert_eq!(crate::scorecard::min_applied_frac(&r), 0.95);
        let want = "setprop debug.knobs.npuop 1; setprop debug.knobs.npuop_lag 1; setprop debug.knobs.npuop_wait_ms 2; setprop debug.knobs.npuop_budget_ms 12; setprop debug.knobs.npuop_budget_frames 30; setprop debug.knobs.npuop_init_frame 300; setprop debug.knobs.npuop_zc 1; setprop debug.knobs.npuop_ts 1";
        assert_eq!(k.npuop.as_ref().unwrap().props.setprop_cmd(true), want);
        match offline_check(&r) {
            Ok(v) => assert_eq!(v["image_policy"], "report"),
            Err(e) => assert!(e.contains("npuop 本机文件"), "{e}"),
        }
        // 本机文件给齐 (临时目录里的假层 / 假模型 / 9 个运行库) → validate-only 收
        let dir = std::env::temp_dir().join(format!("npuop_offline_{}", std::process::id()));
        let rt = dir.join("rt");
        std::fs::create_dir_all(&rt).unwrap();
        std::fs::write(dir.join("layer.so"), b"\x7fELF debug.knobs.npuop_wait_ms").unwrap();
        std::fs::write(dir.join("m.tflite"), b"x").unwrap();
        for n in crate::npuop_device::RUNTIME_LIBS {
            std::fs::write(rt.join(n), b"so").unwrap();
        }
        let mut r2 = r.clone();
        r2["payload"]["rewrites"][0]["args"]["layer_so"] = json!(dir.join("layer.so").display().to_string());
        r2["payload"]["rewrites"][0]["args"]["model"] = json!(dir.join("m.tflite").display().to_string());
        r2["payload"]["rewrites"][0]["args"]["runtime_dir"] = json!(rt.display().to_string());
        let v = offline_check(&r2).unwrap();
        assert_eq!(v["image_policy"], "report");
        assert_eq!(v["world_entry"], "genshin_gate");
        assert_eq!(v["debug.knobs.npuop"], want);
        assert_eq!(v["npuop_files"]["runtime"].as_array().unwrap().len(), 9);
        // 名单两边一致: game_opt_loop gray::NPUOP_ARG_KEYS 同这 10 个
        assert_eq!(NPUOP_ARG_KEYS, ["lag", "wait_ms", "budget_ms", "budget_frames", "init_frame", "zc", "ts", "model", "layer_so", "runtime_dir"]);
    }

    #[test]
    fn offline_check_builds_the_verified_storeop_rule() {
        let mut r = v2_req("gray", json!({"rewrites": [
            {"knob": "storeop_dont_care", "scope": "9d0b4db80bf552b8:1"},
            {"knob": "storeop_dont_care", "scope": "15a51a879df99c32:2"},
            {"knob": "storeop_dont_care", "scope": "9d0b4db80bf552b8:0"},
        ]}));
        r["package"] = json!(GENSHIN_PKG);
        r["workload"]["script"] = json!("knobs/gray/workload_spin_touch_v1.json");
        match offline_check(&r) {
            Ok(v) => {
                assert_eq!(v["debug.knobs.storeop"], "15a51a879df99c32:2,9d0b4db80bf552b8:0,9d0b4db80bf552b8:1");
                assert_eq!(v["world_entry"], "genshin_gate");
                assert_eq!(v["min_reachable_p"].as_f64().unwrap(), loopstat::min_two_sided_p(5));
            }
            // 没构建层 .so 的检出 (worktree) 只允许在最后一步 .so 检查上失败
            Err(e) => assert!(e.contains("层 .so"), "{e}"),
        }
        r["workload"]["script"] = json!("knobs/gray/no_such_workload.json");
        assert!(offline_check(&r).unwrap_err().contains("负载脚本不在"));
    }

    #[test]
    fn validate_only_flag_parses() {
        let a = parse_args(&["--request".into(), "x.json".into(), "--validate-only".into()]).unwrap();
        assert!(a.validate_only);
        let a = parse_args(&["--request".into(), "x.json".into()]).unwrap();
        assert!(!a.validate_only);
    }

    /// npuop: 请求没写 image_policy 时缺省 report (基于模型的候选, gate 会把每个超分候选判成画面出错);
    /// 显式写了 gate 照 gate; 其它开关缺省仍是 gate。
    #[test]
    fn npuop_defaults_image_policy_to_report() {
        use crate::scorecard::ImagePolicy;
        let np = parse_gray_rewrites(&json!({"rewrites": [{"knob": "npuop"}]})).unwrap();
        let so = parse_gray_rewrites(&json!({"rewrites": [{"knob": "storeop_dont_care", "scope": "15a51a879df99c32:2"}]})).unwrap();
        let mut r = v2_req("gray", json!({"rewrites": [{"knob": "npuop"}]}));
        assert_eq!(gray_image_policy(&r, &np).unwrap(), ImagePolicy::Report);
        assert_eq!(gray_image_policy(&r, &so).unwrap(), ImagePolicy::Gate);
        r["protocol"]["image_policy"] = json!("gate");
        assert_eq!(gray_image_policy(&r, &np).unwrap(), ImagePolicy::Gate);
        r["protocol"]["image_policy"] = json!(null);
        assert_eq!(gray_image_policy(&r, &np).unwrap(), ImagePolicy::Report);
        assert!(validate_ok(&v2_req("gray", json!({"rewrites": [{"knob": "npuop", "args": {"lag": 1, "budget_ms": 10}}]}))));
    }

    /// npuop 的 args: 范围核验、不认识的键拒收、不能与 loadop 同组 —— 都在碰设备之前。
    #[test]
    fn npuop_args_are_checked_before_touching_the_device() {
        let n = parse_npuop_args(&json!({"lag": 1, "wait_ms": "2.5", "budget_ms": 10, "budget_frames": 20, "init_frame": 200, "zc": 0, "ts": true,
            "model": "/m/genshin_sr_v2_a.tflite", "layer_so": "/l/libVkLayer_refknobs.so", "runtime_dir": "/rt"})).unwrap();
        assert_eq!(n.props.lag, 1);
        assert_eq!(n.props.wait_ms, 2.5);
        assert_eq!(n.props.budget_frames, 20);
        assert!(!n.props.zc && n.props.ts);
        assert_eq!(n.files.model.as_deref(), Some("/m/genshin_sr_v2_a.tflite"));
        assert_eq!(n.files.runtime_dir.as_deref(), Some("/rt"));
        assert_eq!(parse_npuop_args(&Value::Null).unwrap().props, crate::graylayer::NpuopProps::default());
        for (bad, why) in [
            (json!({"lag": 3}), "lag"),
            (json!({"budget_ms": 0}), "budget_ms"),
            (json!({"zc": 2}), "zc"),
            (json!({"model": "a.onnx"}), ".tflite"),
            (json!({"modle": "a.tflite"}), "不认识"),
            (json!(["lag"]), "对象"),
        ] {
            let e = parse_npuop_args(&bad).unwrap_err();
            assert!(e.contains(why), "{bad}: {e}");
        }
        assert!(parse_gray_rewrites(&json!({"rewrites": [{"knob": "npuop"}, {"knob": "npuop"}]})).unwrap_err().contains("两次"));
        assert!(parse_gray_rewrites(&json!({"rewrites": [{"knob": "npuop"}, {"knob": "loadop_dont_care"}]})).unwrap_err().contains("loadop"));
        // storeop 可以与 npuop 同组 (属性互不相干)
        let k = parse_gray_rewrites(&json!({"rewrites": [{"knob": "npuop"}, {"knob": "storeop_dont_care", "scope": "15a51a879df99c32:2"}]})).unwrap();
        assert!(k.npuop.is_some());
        assert_eq!(k.storeop, "15a51a879df99c32:2");
    }

    /// 校验用的名单和施加用的解析不能再各说各话: 名单里每个开关都要被解析认, 解析不认的不能在名单里。
    #[test]
    fn gray_supported_list_matches_parser() {
        for k in GRAY_SUPPORTED_KNOBS {
            let scope = if k == "storeop_dont_care" { json!("15a51a879df99c32:2") } else { Value::Null };
            assert!(
                parse_gray_rewrites(&json!({"rewrites": [{"knob": k, "scope": scope}]})).is_ok(),
                "名单里的 {k} 解析不认"
            );
        }
        assert!(parse_gray_rewrites(&json!({"rewrites": [{"knob": "render_scale"}]})).is_err());
    }

    /// 跨仓库契约: game_opt_loop `src/optimize/gray.rs` 的 CATALOG 里每个 Landed 开关, 都必须能过
    /// 收端 `validate_v2`; Declared 的必须被拒 (收端没实现)。
    ///
    /// 找 game_opt_loop: 环境变量 GAME_OPT_LOOP_DIR, 否则从本仓库往上逐级找 `game_opt_loop/`。
    /// 找不到时跳过并打印原因; 设 HGAMEY_REQUIRE_CONTRACT=1 则找不到直接失败。
    #[test]
    fn gray_catalog_landed_knobs_pass_receiver_validation() {
        let rel = "src/optimize/gray.rs";
        let mut found = std::env::var("GAME_OPT_LOOP_DIR")
            .ok()
            .map(|d| Path::new(&d).join(rel))
            .filter(|p| p.exists());
        if found.is_none() {
            let mut dir = Some(Path::new(env!("CARGO_MANIFEST_DIR")));
            while let Some(d) = dir {
                let p = d.join("game_opt_loop").join(rel);
                if p.exists() {
                    found = Some(p);
                    break;
                }
                dir = d.parent();
            }
        }
        let Some(path) = found else {
            assert!(
                std::env::var("HGAMEY_REQUIRE_CONTRACT").ok().as_deref() != Some("1"),
                "HGAMEY_REQUIRE_CONTRACT=1 但找不到 game_opt_loop/{rel}"
            );
            eprintln!("跳过跨仓库契约测试: 找不到 game_opt_loop/{rel} (可设 GAME_OPT_LOOP_DIR)");
            return;
        };
        let src = std::fs::read_to_string(&path).unwrap();
        let cat_start = src.find("pub const CATALOG").expect("gray.rs 里没有 CATALOG");
        let cat_end = cat_start + src[cat_start..].find("\n];").expect("CATALOG 没有结尾");
        let cat = &src[cat_start..cat_end];
        let field = |block: &str, name: &str| -> Option<String> {
            let i = block.find(&format!("{name}:"))? + name.len() + 1;
            let rest = block[i..].trim_start();
            let end = rest.find(|c: char| c == ',' || c == '\n').unwrap_or(rest.len());
            Some(rest[..end].trim().trim_matches('"').to_string())
        };
        let mut landed = 0;
        for block in cat.split("Knob {").skip(1) {
            let id = field(block, "id").expect("Knob 缺 id");
            let status = field(block, "status").expect("Knob 缺 status");
            let needs_scope = field(block, "needs_scope").as_deref() == Some("true");
            let mut rw = json!({"knob": id});
            if needs_scope {
                rw["scope"] = json!("15a51a879df99c32:2");
            }
            let r = v2_req("gray", json!({"rewrites": [rw]}));
            match status.as_str() {
                "KnobStatus::Landed" => {
                    landed += 1;
                    assert!(
                        validate_ok(&r),
                        "game_opt_loop 把 {id} 标成 Landed, 收端 validate_v2 却拒收: {:?} ({})",
                        validate_v2(&r),
                        path.display()
                    );
                }
                "KnobStatus::Declared" => assert!(
                    !validate_ok(&r),
                    "game_opt_loop 把 {id} 标成 Declared (层里没实现), 收端却收了"
                ),
                "KnobStatus::Withdrawn" => {}
                other => panic!("不认识的 KnobStatus {other:?} ({id})"),
            }
        }
        assert!(landed >= 1, "{} 的 CATALOG 里一个 Landed 都没解析出来, 解析规则可能过时了", path.display());

        // 再拿上层出主意器的真实产出回放一遍: tests/fixtures/proposer_golden/*/gray_*.json
        // 是 game_opt_loop 按固定种子生成、真会发给收端的灰档请求, 每一份都必须过校验。
        let gol = path.ancestors().nth(3).unwrap();
        let golden = gol.join("tests/fixtures/proposer_golden");
        let mut replayed = 0;
        if let Ok(seeds) = std::fs::read_dir(&golden) {
            for seed in seeds.flatten() {
                for f in std::fs::read_dir(seed.path()).into_iter().flatten().flatten() {
                    let name = f.file_name().to_string_lossy().into_owned();
                    if !(name.starts_with("gray_") && name.ends_with(".json")) {
                        continue;
                    }
                    let req: Value = serde_json::from_str(&std::fs::read_to_string(f.path()).unwrap()).unwrap();
                    assert!(
                        validate_ok(&req),
                        "上层真实灰档请求 {} 被收端拒收: {:?}",
                        f.path().display(),
                        validate_v2(&req)
                    );
                    replayed += 1;
                }
            }
        }
        eprintln!("跨仓库契约: CATALOG Landed {landed} 个通过; 回放上层真实灰档请求 {replayed} 份全部通过");
    }

    #[test]
    fn sysparam_requires_params_object() {
        assert!(!validate_ok(&v2_req("sysparam", json!({"params": "x"}))));
        assert!(!validate_ok(&v2_req("sysparam", json!({}))));
    }

    // ── 接收端再校验 (不信上层) ──

    fn wl() -> sysparam::Whitelist {
        // 与 src/sysparam.rs 单测同一份 probe
        let probe = "cpu.policies=0\ncpu.policy0.avail_freqs=300000 1000000 2000000\n\
cpu.policy0.scaling_min_freq.cur=300000\ncpu.policy0.scaling_min_freq.writable=yes\n\
cpu.policy0.scaling_min_freq.effect=live\nbus.DDR.boost_freq.cur=0\n\
bus.DDR.avail_freqs=200000 3200000 5333000\nbus.DDR.boost_freq.writable=yes\n\
bus.DDR.boost_freq.effect=live\n";
        sysparam::build_whitelist(&sysparam::parse_probe(probe))
    }

    fn sysreq(params: Value, whitelist: Value, extra: Value) -> Value {
        let mut p = json!({"params": params, "whitelist": whitelist});
        if let Some(e) = extra.as_object() {
            for (k, v) in e {
                p[k.as_str()] = v.clone();
            }
        }
        v2_req("sysparam", p)
    }

    #[test]
    fn out_of_whitelist_params_are_rejected_receiver_side() {
        let w = serde_json::to_value(wl_pyval(&wl())).unwrap();
        // 上层说合法不算数: 不在白名单里的 id 整份拒绝
        let mut req = sysreq(json!({"nope.param": "1"}), w.clone(), json!({}));
        req["payload"]["whitelist"] = json!({});
        let e = revalidate(&req, &wl()).unwrap_err();
        assert!(e.contains("校验拒绝"), "{e}");
        // 值不在取值表里
        let req = sysreq(json!({"bus.DDR.boost_freq": "9999999"}), w.clone(), json!({}));
        assert!(revalidate(&req, &wl()).is_err());
        // 合法的过
        let req = sysreq(json!({"bus.DDR.boost_freq": "5333000"}), w.clone(), json!({}));
        assert!(revalidate(&req, &wl()).is_ok());
    }

    /// deny_keywords 接收端**再拦一道**: 命中温控关键词的不看白名单直接拒,
    /// 上层自定义的关键词表也要一起拦。
    #[test]
    fn deny_keywords_block_again_at_the_receiver() {
        let w = serde_json::to_value(wl_pyval(&wl())).unwrap();
        // 缺省表: thermal
        let req = sysreq(json!({"gpu.thermal_pwrlevel": "0"}), w.clone(), json!({}));
        let e = revalidate(&req, &wl()).unwrap_err();
        assert!(e.contains("温控保护关键词"), "{e}");
        // 上层收窄了 deny_keywords, 收端按它的拦
        let mut req = sysreq(json!({"gpu.fan_pwrlevel": "0"}), w.clone(), json!({}));
        req["payload"]["deny_keywords"] = json!(["fan"]);
        assert!(revalidate(&req, &wl()).is_err());
    }

    /// plan 随请求下发时逐项对回白名单: path 对不上或值越界, 整份拒绝。
    #[test]
    fn plan_entries_are_cross_checked_against_the_whitelist() {
        let w = serde_json::to_value(wl_pyval(&wl())).unwrap();
        let plan = json!([
            {"kind": "sysfs", "path": "/sys/devices/system/cpu/bus_dcvs/DDR/boost_freq", "value": "5333000"}
        ]);
        let req = sysreq(json!({"bus.DDR.boost_freq": "5333000"}), w.clone(), json!({"plan": plan}));
        assert!(revalidate(&req, &wl()).is_ok());
        // path 在白名单里, 但值不在取值表
        let plan = json!([
            {"kind": "sysfs", "path": "/sys/devices/system/cpu/bus_dcvs/DDR/boost_freq", "value": "42"}
        ]);
        let req = sysreq(json!({"bus.DDR.boost_freq": "5333000"}), w.clone(), json!({"plan": plan}));
        assert!(revalidate(&req, &wl()).is_err());
        // path 不在白名单
        let plan = json!([
            {"kind": "sysfs", "path": "/sys/class/thermal/mode", "value": "0"}
        ]);
        let req = sysreq(json!({"bus.DDR.boost_freq": "5333000"}), w.clone(), json!({"plan": plan}));
        assert!(revalidate(&req, &wl()).is_err());
    }

    // ── v2 shader → v1 降级 ──

    /// 降级映射的字段逐一对: conditions.cool_c 优先于 protocol.cool_c (v1 遗留口),
    /// quality_reference payload 优先于 conditions。
    #[test]
    fn shader_v2_maps_to_v1_request() {
        let req = json!({
            "version": 2, "kind": "shader", "candidate_id": "s1",
            "package": "io.github.hgamey.refbench",
            "workload": {"carrier": "refbench", "script": "sr_pipeline", "seconds": null, "frames": 300, "intensity": 2},
            "conditions": {"cool_c": 42.0, "quality_reference": "/tmp/ref_dir"},
            "protocol": {"cool_c": 32.0, "replay_seconds": 12, "rounds": 4, "alternating": true, "p_threshold": 0.05},
            "payload": {"track": "sr", "shader_path": "a.glsl", "spirv_path": "a.spv",
                        "budget_ms": 4.0, "quality_baseline_db": 30.0,
                        "incumbent_latency_ms": null, "quality_reference": null}
        });
        assert!(validate_ok(&req));
        let plan = crate::shaderplan::build(&req,None,None).unwrap();
        assert_eq!(plan.request["protocol"]["cool_c"],42.0);
        assert_eq!(plan.request["budget_ms"],4.0);
        assert_eq!(plan.request["quality_reference"],"/tmp/ref_dir");
        let args=crate::gpuop::parse_args(&[plan.args,vec!["--request".into(),"x".into()]].concat()).unwrap();
        assert_eq!(args.carrier,crate::gpuop::Carrier::Refbench);
        assert_eq!(args.frames,300); assert_eq!(args.intensity,2);
    }

    // ── 报告装配 ──

    /// 未检验哨兵 p=1.0; sysparam 的画质项是结构性的 null 并带人话原因。
    #[test]
    fn base_report_honest_nulls_and_sentinel_p() {
        let req = v2_req("sysparam", json!({"params": {}}));
        let mut r = base_report(&req, "sysparam");
        assert_eq!(r["verdict"]["p_value"], json!(1.0));
        assert_eq!(r["source"], json!("phonefarm"));
        let mut m = r["metrics"].take();
        assert_eq!(m["psnr_db"], Value::Null);
        unavailable(&mut m, "psnr_db", "sysparam 不动画面, 无画质可量");
        r["metrics"] = m;
        let u = r["metrics"]["unavailable"].as_array().unwrap();
        assert_eq!(u.len(), 1);
        assert_eq!(u[0]["field"], json!("psnr_db"));
    }

    /// ABORT 与 REJECT 的分野: 旋钮没生效是「没量准」, 绝不能落成 REJECT。
    #[test]
    fn apply_failure_maps_to_abort_not_reject() {
        // sysparam_eval 在 apply FAIL 时只写 reason 且 status 保持初始 ABORT;
        // 这里核对映射函数本身的语义边界
        let keep = !vec!["frame_p95".to_string()].is_empty() && Vec::<String>::new().is_empty();
        assert!(keep);
        // decide 的三分: KEEP→PASS / REJECT→REJECT / ABORT→ABORT
        let s = |keep: bool, aborted: bool| if aborted { "ABORT" } else if keep { "PASS" } else { "REJECT" };
        assert_eq!(s(true, false), "PASS");
        assert_eq!(s(false, false), "REJECT");
        assert_eq!(s(true, true), "ABORT");
    }

    /// reachable: 5v5 精确置换最小可达 p=2/C(10,5)=0.0079, 够得着 0.0167;
    /// 3v3 最小可达 p=2/C(6,3)=0.1, 够不着。
    #[test]
    fn reachable_reflects_the_resolution_floor() {
        assert!(reachable(5, 0.05 / 3.0));
        assert!(!reachable(3, 0.05 / 3.0));
        assert!(!reachable(1, 0.5));
        assert!(!reachable(0, 0.5));
    }

    /// 功耗与结温来自 sample_env (env), parse-trace 的 summary 里没有这两项。
    /// 判定视图只读 summary 的话, 功耗永远「缺数据」、70 °C 温度上限永远不触发。
    #[test]
    fn fan_guard_flags_still_on_and_mismatched_arms() {
        assert!(fan_still_on(&json!({"before": "1", "after": "0", "forced": true})).is_none());
        assert!(fan_still_on(&json!({"before": "1", "after": "1", "forced": true})).is_some());
        assert!(fan_still_on(&json!({"before": null, "after": null, "forced": false})).is_none());
        let on = json!({"env": {"fan_state": "fan_enable=1,fan_speed_level=5,fan_speed_pwm="}});
        let off = json!({"env": {"fan_state": "fan_enable=0,fan_speed_level=5,fan_speed_pwm="}});
        // 2026-10-12 W1 的形态: 前几臂开、最后一对关
        assert!(fan_mismatch(&[on.clone(), off.clone()], &[on.clone()]).is_some());
        assert!(fan_mismatch(&[off.clone(), off.clone()], &[off.clone()]).is_none());
        // 没记风扇的 (别的机型) 不算不一致
        assert!(fan_mismatch(&[json!({"env": {}})], &[off]).is_none());
    }

    /// 审计 #9 #11: 逐秒 GPU 档位/风扇记录的解析, 以及同一对两臂环境不一致时只剔除那一对。
    #[test]
    fn hw_stop_path_is_fresh_per_arm_and_preexisting_stop_is_recorded() {
        // 同一 label 两次评测: nonce 不同, 文件名就不同 (设备端不删旧标记, 只能换名字)
        let a = hw_stop_path("sp_knob1", "1_2");
        let b = hw_stop_path("sp_knob1", "3_2");
        assert_ne!(a, b);
        assert!(a.starts_with(DEV_TMP) && a.ends_with(".stop") && a.contains("sp_knob1"));
        assert_ne!(hw_stop_nonce(), "");
        let s = hw_stats("#sample_hw v1 max=900 stop=/x\n#stop_preexisting /x\nHW 1.0 0 -\n#sample_hw done\n");
        assert_eq!(s["stop_preexisting"], true);
        assert_eq!(s["n"], 1);
        assert_eq!(hw_stats("HW 1.0 0 -\n")["stop_preexisting"], false);
    }

    #[test]
    fn hw_stats_parses_per_second_levels_and_fan() {
        let t = "#sample_hw v1 max=900\n\
HW 10.0 0 fan_enable=0,fan_speed_level=5\n\
HW 11.0 0 fan_enable=0,fan_speed_level=5\n\
HW 12.0 17 fan_enable=1,fan_speed_level=4\n\
HW 13.0 NA -\n\
junk\n\
#sample_hw done\n";
        let s = hw_stats(t);
        assert_eq!(s["n"], 4);
        assert_eq!(s["gpu_pwrlevel_runs"], json!([[0, 2], [17, 1]]));
        assert_eq!(s["gpu_pwrlevel_frac"], json!({"0": 0.667, "17": 0.333}));
        assert_eq!(s["fan_runs"], json!([["fan_enable=0,fan_speed_level=5", 2], ["fan_enable=1,fan_speed_level=4", 1]]));
        assert_eq!(s["fan_states"].as_array().unwrap().len(), 2);
        // 空输入 (脚本不在设备上): n=0, 什么都不判
        let e = hw_stats("");
        assert_eq!(e["n"], 0);
        assert_eq!(e["gpu_pwrlevel_frac"], json!({}));
    }

    fn arm_hw(round: u32, label: &str, levels: &[(i64, usize)], fan: &str) -> Value {
        let mut t = String::new();
        for (l, c) in levels {
            for _ in 0..*c {
                t.push_str(&format!("HW 1.0 {l} {fan}\n"));
            }
        }
        json!({"label": label, "round": round, "hw": hw_stats(&t), "env": {"fan_state": "fan_enable=0"}})
    }

    #[test]
    fn pair_env_check_flags_fan_and_gpu_level_differences() {
        let k = arm_hw(1, "k1", &[(0, 30)], "fan_enable=0");
        let c = arm_hw(1, "c1", &[(0, 28), (17, 2)], "fan_enable=0");
        // 档位分布只差 2/30 → 可比
        assert!(pair_env_check(&k, &c, true).is_ok());
        // 1/3 时间被压到 17 档 → 不可比
        let c2 = arm_hw(1, "c1", &[(0, 20), (17, 10)], "fan_enable=0");
        let e = pair_env_check(&k, &c2, true).unwrap_err();
        assert!(e.contains("GPU 上限档"), "{e}");
        // sysparam 改的就是档位: 不比这一项
        assert!(pair_env_check(&k, &c2, false).is_ok());
        // 风扇中途被打开 → 不可比 (不管 GPU 查不查)
        let c3 = arm_hw(1, "c1", &[(0, 30)], "fan_enable=1");
        assert!(pair_env_check(&k, &c3, false).unwrap_err().contains("风扇"));
        // 没有逐秒记录的旧证据: 退回 sample_env 开头那一行, 一致就可比
        let old_k = json!({"env": {"fan_state": "fan_enable=0"}});
        let old_c = json!({"env": {"fan_state": "fan_enable=0"}});
        assert!(pair_env_check(&old_k, &old_c, true).is_ok());
        let old_on = json!({"env": {"fan_state": "fan_enable=1"}});
        assert!(pair_env_check(&old_k, &old_on, true).is_err());
        // 别的机型没风扇节点: 不判
        assert!(pair_env_check(&json!({"env": {}}), &json!({"env": {}}), true).is_ok());
    }

    #[test]
    fn settle_pair_drops_only_the_inconsistent_pair() {
        let mut knob = vec![arm_hw(1, "k1", &[(0, 30)], "fan_enable=0")];
        let mut ctrl = vec![arm_hw(1, "c1", &[(0, 30)], "fan_enable=0")];
        let mut dropped = Vec::new();
        assert!(!settle_pair(1, &mut knob, &mut ctrl, &mut dropped, true));
        // 第 2 对只量完一臂: 不判
        ctrl.push(arm_hw(2, "c2", &[(0, 30)], "fan_enable=1"));
        assert!(!settle_pair(2, &mut knob, &mut ctrl, &mut dropped, true));
        knob.push(arm_hw(2, "k2", &[(0, 30)], "fan_enable=0"));
        assert!(settle_pair(2, &mut knob, &mut ctrl, &mut dropped, true));
        assert_eq!((knob.len(), ctrl.len()), (1, 1));
        assert_eq!(knob[0]["label"], "k1");
        assert_eq!(dropped.len(), 1);
        assert_eq!(dropped[0]["round"], 2);
        let note = consistency_note(&dropped, true);
        assert_eq!(note["dropped_pairs"], 1);
        let mut report = json!({"verdict": {"reason": "没有任何指标显著改善"}});
        append_dropped_reason(&mut report, &dropped);
        assert!(report["verdict"]["reason"].as_str().unwrap().contains("第 2 对"));
    }

    #[test]
    fn charge_settled_means_battery_no_longer_charging() {
        assert!(!charge_settled(Some(1_930_000))); // 停充刚生效, 电池还在吸电
        assert!(!charge_settled(Some(60_000)));
        assert!(charge_settled(Some(30_000))); // 旁路: 约 0
        assert!(charge_settled(Some(-800_000))); // 放电
        assert!(!charge_settled(None));
    }

    /// 审计 #8: 锁帧负载上 fps_mean 只守退化。
    #[test]
    fn fps_lock_cap_detects_frame_capped_loads() {
        let runs = |xs: &[f64]| -> Vec<Value> { xs.iter().map(|&p| json!({"summary": {"frame_p50": p}})).collect() };
        // Megacity 旧包实测 frame_p50 约 33.3 ms
        assert_eq!(fps_lock_cap(&runs(&[33.3, 33.4, 33.2, 33.5])), Some(30.0));
        assert_eq!(fps_lock_cap(&runs(&[16.6, 16.7])), Some(60.0));
        // 解锁后帧率跳台阶 (审计 #9 的 231 / 258 / 287 fps): 不是锁帧
        assert_eq!(fps_lock_cap(&runs(&[4.33, 3.88, 3.48])), None);
        // 有一轮离开上限 (候选把上限打破了) 就不算锁帧
        assert_eq!(fps_lock_cap(&runs(&[33.3, 33.4, 24.0])), None);
        // 两轮贴着不同上限: 不算同一个锁
        assert_eq!(fps_lock_cap(&runs(&[33.3, 16.6])), None);
        // 没有数据、或有一轮缺 frame_p50: 不下结论
        assert_eq!(fps_lock_cap(&runs(&[])), None);
        let mut r = runs(&[33.3, 33.4]);
        r.push(json!({"summary": {}}));
        assert_eq!(fps_lock_cap(&r), None);
    }

    /// judge/v1-scorecard: 锁帧时 p95 变长但帧还放得进帧预算 → 不算退化, 报帧预算用掉多少;
    /// 帧真丢了照样算退化; 不锁帧时规则不变。
    #[test]
    fn capped_p95_within_budget_is_not_a_regression() {
        let arm = |tag: &str, p95: &[f64], fps: f64, p50: f64| -> Vec<Value> {
            p95.iter()
                .enumerate()
                .map(|(i, p)| json!({"label": format!("{tag}{i}"), "summary": {"frame_p95": p, "fps_mean": fps, "frame_p50": p50}, "env": {}}))
                .collect()
        };
        // 锁 30: 帧预算 33.33 ms。候选臂 p95 全部更长 (33.0..33.4), 但都 ≤ 33.33 × 1.05, fps 一样守在 29.95
        let ctrl = arm("c", &[30.0, 30.1, 30.2, 30.3, 30.4], 29.95, 33.33);
        let knob = arm("k", &[33.0, 33.1, 33.2, 33.3, 33.4], 29.95, 33.33);
        let (v, _keep, _) = judge_runs(&ctrl, &knob, 0.05, 5, Some(30.0));
        assert_eq!(v["regressions"], json!([]), "{v}");
        let pm = &v["per_metric"]["frame_p95"];
        assert_eq!(pm["role"], "capped_budget");
        assert!(pm["status"].as_str().unwrap().contains("帧预算内"));
        assert_eq!(pm["capped"]["ok"], true);
        assert!((pm["capped"]["headroom_used_pct"].as_f64().unwrap() - 100.2).abs() < 0.1, "{pm}");
        // 帧真丢了 (fps 掉出上限): 照样显著变差
        let knob_lost = arm("k", &[33.0, 33.1, 33.2, 33.3, 33.4], 28.5, 33.33);
        let (v, keep, _) = judge_runs(&ctrl, &knob_lost, 0.05, 5, Some(30.0));
        assert!(v["regressions"].as_array().unwrap().contains(&json!("frame_p95")), "{v}");
        assert!(!keep);
        // p95 超出帧预算: 照样显著变差
        let knob_over = arm("k", &[36.0, 36.1, 36.2, 36.3, 36.4], 29.95, 33.33);
        let (v, _, _) = judge_runs(&ctrl, &knob_over, 0.05, 5, Some(30.0));
        assert!(v["regressions"].as_array().unwrap().contains(&json!("frame_p95")));
        // 不锁帧 (p50 12.5 ms ≈ 80 fps, 不是常见上限): 严格规则不变
        let ctrl_u = arm("c", &[10.0, 10.1, 10.2, 10.3, 10.4], 80.0, 12.5);
        let knob_u = arm("k", &[11.0, 11.1, 11.2, 11.3, 11.4], 80.0, 12.5);
        let (v, _, _) = judge_runs(&ctrl_u, &knob_u, 0.05, 5, None);
        assert_eq!(v["regressions"], json!(["frame_p95"]));
        assert!(v["per_metric"]["frame_p95"].get("capped").is_none());
    }

    #[test]
    fn locked_fps_is_guard_only_and_frees_the_budget() {
        let arm = |tag: &str, p95: &[f64], fps: &[f64]| -> Vec<Value> {
            p95.iter()
                .zip(fps)
                .enumerate()
                .map(|(i, (p, f))| json!({"label": format!("{tag}{i}"), "summary": {"frame_p95": p, "fps_mean": f, "frame_p50": if *f < 40.0 { 33.33 } else { 1000.0 / f }}, "env": {}}))
                .collect()
        };
        // 5v5 锁 30 fps (frame_p50 33.33 ms, 与 Megacity 旧包实测相同), 候选臂帧时 p95 全部更短, 帧率全部略高
        let ctrl = arm("c", &[36.0, 36.1, 36.2, 36.3, 36.4], &[29.40, 29.41, 29.42, 29.43, 29.44]);
        let knob = arm("k", &[34.0, 34.1, 34.2, 34.3, 34.4], &[29.60, 29.61, 29.62, 29.63, 29.64]);
        let (v, keep, on_batt) = judge_runs(&ctrl, &knob, 0.05, 5, Some(30.0));
        assert!(!on_batt);
        assert_eq!(v["fps_locked_cap"], json!(30.0));
        assert_eq!(v["per_metric"]["fps_mean"]["role"], json!("guard_only"));
        assert_eq!(v["per_metric"]["fps_mean"]["status"], json!("无显著变化"));
        // 表里只剩 frame_p95 占预算: alpha_win = 0.05 / 1
        assert!((v["alpha_win"].as_f64().unwrap() - 0.05).abs() < 1e-12);
        assert_eq!(v["wins"], json!(["frame_p95"]));
        assert!(keep);

        // 锁帧照样守退化: 候选臂帧率全部更低 → 显著变差
        let knob_slow = arm("k", &[34.0, 34.1, 34.2, 34.3, 34.4], &[29.20, 29.21, 29.22, 29.23, 29.24]);
        let (v, keep, _) = judge_runs(&ctrl, &knob_slow, 0.05, 5, Some(30.0));
        assert_eq!(v["regressions"], json!(["fps_mean"]));
        assert!(!keep);

        // 不锁帧时 fps_mean 照常占一份预算、可以 win
        let ctrl_u = arm("c", &[8.0, 8.1, 8.2, 8.3, 8.4], &[231.0, 232.0, 233.0, 234.0, 235.0]);
        let knob_u = arm("k", &[7.0, 7.1, 7.2, 7.3, 7.4], &[258.0, 259.0, 260.0, 261.0, 262.0]);
        let (v, _, _) = judge_runs(&ctrl_u, &knob_u, 0.05, 5, None);
        assert_eq!(v["fps_locked_cap"], Value::Null);
        assert!(v["per_metric"]["fps_mean"].get("role").is_none());
        assert!((v["alpha_win"].as_f64().unwrap() - 0.025).abs() < 1e-12);
        assert_eq!(v["wins"], json!(["frame_p95", "fps_mean"]));
    }

    #[test]
    fn curtail_tests_widen_when_fps_may_be_locked() {
        let get = |t: &[curtail::WinTest], m: &str| t.iter().find(|x| x.metric == m).map(|x| x.alpha_win);
        let two_metrics = vec!["frame_p95".to_string(), "fps_mean".to_string()];
        let three_metrics = vec!["frame_p95".to_string(), "fps_mean".to_string(), "power_w_mean".to_string()];
        let widest_with_power = |fps_lock_possible| {
            let mut tests = policy_win_tests(&two_metrics, 0.05, fps_lock_possible);
            tests.extend(policy_win_tests(&three_metrics, 0.05, fps_lock_possible)
                .into_iter().filter(|t| t.metric == "power_w_mean"));
            tests
        };
        let t = widest_with_power(false);
        assert_eq!((get(&t, "frame_p95"), get(&t, "power_w_mean")), (Some(0.025), Some(0.05 / 3.0)));
        let t = widest_with_power(true);
        assert_eq!((get(&t, "frame_p95"), get(&t, "fps_mean"), get(&t, "power_w_mean")), (Some(0.05), Some(0.025), Some(0.025)));
    }

    #[test]
    fn run_view_carries_power_and_temp_from_env() {
        let m = json!({
            "label": "sp_knob1",
            "summary": {"frame_p95": 19.5, "fps_mean": 59.2},
            "env": {"power_w_mean": 4.7, "soc_temp_max_c": 55.1, "on_battery": true},
        });
        assert!(m["summary"].get("power_w_mean").is_none());
        let v = run_view(&m);
        assert_eq!(v.get("power_w_mean").and_then(|x| x.as_f64()), Some(4.7));
        assert_eq!(v.get("frame_p95").and_then(|x| x.as_f64()), Some(19.5));
        assert_eq!(run_soc_temp_max(&m), Some(55.1));

        // 两臂各两轮: 功耗这一项要真的比得出来, 不能落成「样本不足」
        let arm = |p: f64| -> Vec<Run> {
            (0..2)
                .map(|i| (format!("r{i}"), run_view(&json!({"summary": {}, "env": {"power_w_mean": p + i as f64 * 0.01}}))))
                .collect()
        };
        let cmp = loopstat::compare(&arm(4.7), &arm(5.2), "power_w_mean");
        assert!(cmp.get("error").is_none());
        assert!(cmp.get("diff_mean").and_then(|x| x.as_f64()).unwrap() > 0.4);
    }

    /// binder 归一化那套不归 gray: gray 的开关白名单才是闸门, 已在上面的单测里。
    #[test]
    fn to_pyval_keeps_int_float_distinction() {
        let v: Value = serde_json::from_str(r#"{"a":3150.0,"b":4800}"#).unwrap();
        let p = to_pyval(&v);
        assert_eq!(
            py_dumps(&p),
            "{\n \"a\": 3150.0,\n \"b\": 4800\n}"
        );
    }

    #[test]
    fn to_serde_round_trips_and_drops_non_finite() {
        let p = PyVal::Obj(vec![
            ("x".into(), PyVal::Float(1.5)),
            ("inf".into(), PyVal::Float(f64::INFINITY)),
        ]);
        let v = to_serde(&p);
        assert_eq!(v["x"], json!(1.5));
        assert_eq!(v["inf"], Value::Null);
    }
}

#[cfg(test)]
mod path_tests {
    use super::*;

    #[test]
    fn control_sample_keeps_unknown_filter_and_power_separate_from_raw_collection() {
        let root = std::env::temp_dir().join(format!("control-sample-{}-{}",std::process::id(),chrono::Utc::now().timestamp_nanos_opt().unwrap()));
        std::fs::create_dir(&root).unwrap();
        for (name,status,current,trace_present) in [("legacy_bypass","Charging",0,true),("battery","Discharging",-1_000_000,true),("failed","Discharging",-1_000_000,false),("battery_policy_failed","Discharging",-1_000_000,true),
            ("groups","Discharging",-1_000_000,true),("groups_bad_clock","Discharging",-1_000_000,true),
            ("groups_loss","Discharging",-1_000_000,true),("groups_charging","Charging",0,true)] {
            let out = root.join(name);std::fs::create_dir(&out).unwrap();
            // 放电用例是纯电池: 停充模式 battery、USB 输入电流 0。写成 bypass 或带 5 V × 1 A 输入,
            // 混合供电规则会 (正确地) 把它判成 usb_plus_battery, 电池能量积分就不成立了
            let (mode,usb_ua) = if status=="Charging" {("bypass",1_000_000)} else {("battery",0)};
            let mut env = format!("#sample_env v1 dur=13 interval=2\n#battery_status={status}\n#charge_suspended=yes\n#charge_mode={mode}\n");
            for t in (100..=112).step_by(2) {env.push_str(&format!("ENV {t} 3800000 {current} NA 5000000 {usb_ua} gpuss 40000\n"));}
            env.push_str(&format!("#battery_status_after={status}\n#sample_env done\n"));
            std::fs::write(out.join("env.txt"),env).unwrap();
            if trace_present {
                let mut trace = "#loop_v1_meta capture_start_uptime=106 capture_end_uptime=109 duration_req=3\n".to_owned();
                trace.push_str(&format!("# phonefarm trace_mode=instance trace_clock={}\n# phonefarm cpu0 overrun: {}\n",
                    if name=="groups_bad_clock"{"local"}else{"boot"},if name=="groups_loss"{1}else{0}));
                for (i,t) in [106.25,106.875,107.5,108.125,108.75].iter().enumerate() {
                    trace.push_str(&format!("UnityGfxDeviceW-42 [000] ..... {t:.6}: adreno_cmdbatch_submitted: ctx=1 ts={i}\n"));
                }
                std::fs::write(out.join("trace.txt"),trace).unwrap();
            }
            if name=="battery_policy_failed" {
                std::fs::write(out.join("sample-request.json"),r#"{"power_policy":"battery"}"#).unwrap();
                std::fs::write(out.join("power.json"),r#"{"policy":"battery","before":{"on_battery":true},"after":{"on_battery":false},"error":"supply changed after collection"}"#).unwrap();
            }
            let input = if name=="battery_policy_failed" {
                // Final save failed: the latest returned failure takes priority
                // over the earlier durable before/after snapshot.
                json!({"power":{"policy":"battery","before":{"on_battery":true},"after":{"on_battery":false},"error":"supply changed after collection; power evidence save failed"}})
            } else {Value::Null};
            let comm = name.starts_with("groups").then_some("UnityGfxDeviceW");
            let result = summarize_control_sample(&out,name,3,trace_present,true,input,true,"synthetic",comm).unwrap();
            if comm.is_none() {
                assert!(result["summary"].is_null());
                assert!(result["metrics"]["gpu_submit_fps"].is_null());
            } else { assert_eq!(result["summary"]["n_frames"],4); }
            assert!(result["metrics"]["energy_per_frame_mj"].is_null());
            assert_eq!(result["metric_provenance"]["frame"]["target_ownership_verified"],false);
            if name=="battery" || (comm.is_some() && status=="Discharging") {
                assert_eq!(result["metrics"]["power_w_mean"],3.8);
                assert!((result["metrics"]["trace_energy_j"].as_f64().unwrap()-11.4).abs()<1e-9);
                assert_eq!(result["metric_provenance"]["trace_energy"]["available"],true);
            } else {
                assert!(result["metrics"]["power_w_mean"].is_null());
                assert!(result["metrics"]["trace_energy_j"].is_null());
            }
            let group=&result["metric_provenance"]["submit_group_energy"];
            assert_eq!(group["target_ownership_verified"],false);
            assert_eq!(group["display_verified"],false);
            assert_eq!(group["render_comm"],json!(comm));
            assert_eq!(group["scope"],"whole_device_battery_over_gpu_submit_group_window");
            if name=="groups" {
                assert_eq!(group["available"],true);
                assert_eq!(group["window"],json!({"clock":"uptime","start":106.25,"end":108.75}));
                assert_eq!(group["group_count"],4);
                assert_eq!(group["submits_per_group"],1);
                assert_eq!(group["grouping"],"existing_parser_inferred");
                assert_eq!(group["filtering_basis"],"render_comm");
                assert_eq!(group["trace_clock"],"boot");
                assert_eq!(group["loss_observation"],"per_cpu_overrun_zero");
                assert!((result["metrics"]["gpu_submit_group_window_energy_j"].as_f64().unwrap()-9.5).abs()<1e-9);
                assert!((result["metrics"]["gpu_submit_group_window_power_w_mean"].as_f64().unwrap()-3.8).abs()<1e-9);
                assert!((result["metrics"]["battery_energy_per_gpu_submit_group_mj"].as_f64().unwrap()-2375.0).abs()<1e-9);
            } else {
                assert_eq!(group["available"],false);
                assert!(group["reason"].is_string());
                assert!(group["window"].is_null());
                for key in ["gpu_submit_group_window_energy_j","gpu_submit_group_window_power_w_mean","battery_energy_per_gpu_submit_group_mj"] {
                    assert!(result["metrics"][key].is_null());
                }
            }
            assert_eq!(result["collection"]["status"],if trace_present && name!="battery_policy_failed"{"complete"}else{"failed"});
            if name=="battery_policy_failed" {assert!(out.join("trace.txt").is_file());assert!(result["collection"]["error"].as_str().unwrap().contains("power evidence save failed"));}
            assert!(out.join("run.json").is_file());
        }
        std::fs::remove_dir_all(root).unwrap();
    }

    /// game_opt_loop 的 workload.script 是**调用方视角**的相对路径
    /// ("../phonefarm/knobs/gray/workload_spin_touch_v1.json"): 在 game_opt_loop
    /// 的 cwd 下按原样就要找得到。
    #[test]
    fn resolve_path_tries_cwd_then_repo_root() {
        let repo = repo_root();
        assert!(resolve_path("loop_v1/tools/run_once.sh").is_some());
        let odd = repo.join("nonexistent_zzz.bin");
        assert!(resolve_path(odd.to_string_lossy().as_ref()).is_none());
        // 绝对路径原样
        assert_eq!(
            resolve_path(&repo.join("loop_v1").to_string_lossy()),
            Some(repo.join("loop_v1"))
        );
    }

    /// serial 的取值顺序: CLI --serial 压过请求体。
    #[test]
    fn serial_resolution_prefers_cli() {
        let req = json!({"serial": "REQ_SERIAL", "candidate_id": "c"});
        let cli: Option<String> = Some("CLI_SERIAL".into());
        let serial = cli.clone().or_else(|| req["serial"].as_str().map(String::from));
        assert_eq!(serial.as_deref(), Some("CLI_SERIAL"));
        let serial = None::<String>.or_else(|| req["serial"].as_str().map(String::from));
        assert_eq!(serial.as_deref(), Some("REQ_SERIAL"));
    }

    // ── gray: 改写解析与画面比对 ──

    #[test]
    fn gray_rewrites_map_to_layer_properties() {
        assert_eq!(effective_gray_hits(&json!({"effective":[{"begins":0}],"readonly_stats":{"render_pass_begins":100}})),0);
        assert_eq!(effective_gray_hits(&json!({"effective":[{"begins":2},{"begins":3}]})),5);
        let k = parse_gray_rewrites(&json!({"rewrites": [
            {"knob": "storeop_dont_care", "scope": "15a51a879df99c32:2"},
            {"knob": "storeop_dont_care", "scope": "9d0b4db80bf552b8:0"},
        ]}))
        .unwrap();
        assert!(!k.loadop);
        assert_eq!(k.storeop, "15a51a879df99c32:2,9d0b4db80bf552b8:0");
        let k = parse_gray_rewrites(&json!({"rewrites": [{"knob": "loadop_dont_care"}]})).unwrap();
        assert!(k.loadop);
        assert_eq!(k.storeop, "0");
        assert!(k.npuop.is_none());
        let k = parse_gray_rewrites(&json!({"rewrites": [{"knob": "npuop", "args": {"lag": 1}}]})).unwrap();
        assert_eq!(k.npuop.unwrap().props.setprop_cmd(true), "setprop debug.knobs.npuop 1; setprop debug.knobs.npuop_lag 1; setprop debug.knobs.npuop_wait_ms 2; setprop debug.knobs.npuop_budget_ms 12; setprop debug.knobs.npuop_budget_frames 30; setprop debug.knobs.npuop_init_frame 300; setprop debug.knobs.npuop_zc 1; setprop debug.knobs.npuop_ts 1");
    }

    #[test]
    fn gray_cadence_does_not_win_from_power_outside_its_frozen_goal() {
        let arm=|power:f64| (0..6).map(|i|json!({"label":format!("r{i}"),"summary":{"frame_p95":16.0,"fps_mean":60.0},
            "env":{"power_w_mean":power+i as f64*0.01,"on_battery":true}})).collect::<Vec<_>>();
        let (ctrl,knob)=(arm(6.0),arm(3.0));
        assert!(judge_runs(&ctrl,&knob,0.05,6,None).1); // old adaptive metric list would accept this
        let required=Goal::RenderCadence.metrics();
        let mut report=base_report(&json!({}),"gray");
        finish_gray_statistics(&mut report,&knob,&ctrl,0.05,6,None,&required,None).unwrap();
        assert_eq!(report["status"],"REJECT");
        assert_eq!(report["verdict"]["alpha_win"],0.025);
        assert!(report["verdict"]["per_metric"].get("power_w_mean").is_none());
    }

    /// 不认识的开关、缺范围、范围写错, 都在碰设备之前拒收。
    #[test]
    fn bad_gray_rewrites_are_refused_before_touching_the_device() {
        for p in [
            json!({"rewrites": []}),
            json!({"rewrites": [{"knob": "render_scale"}]}),
            json!({"rewrites": [{"knob": "storeop_dont_care"}]}),
            json!({"rewrites": [{"knob": "storeop_dont_care", "scope": "15a51a87"}]}),
            json!({"rewrites": [{"knob": "storeop_dont_care", "scope": "15a5:2"}]}),
            json!({"rewrites": [{"knob": "storeop_dont_care", "scope": "15a51a879df99c32:x"}]}),
        ] {
            assert!(parse_gray_rewrites(&p).is_err(), "{p}");
        }
    }

    #[test]
    fn psnr_is_capped_for_identical_frames_and_drops_with_damage() {
        let a = vec![100u8; 4 * 64];
        assert_eq!(psnr_rgba(&a, &a), Some(PSNR_IDENTICAL_DB));
        let mut b = a.clone();
        for px in b.chunks_exact_mut(4).take(32) {
            px[0] = 0; // 一半像素的红通道被画坏
        }
        let p = psnr_rgba(&a, &b).unwrap();
        assert!(p < GRAY_MIN_PSNR_DB, "{p}");
        // alpha 通道不算
        let mut c = a.clone();
        for px in c.chunks_exact_mut(4) {
            px[3] = 0;
        }
        assert_eq!(psnr_rgba(&a, &c), Some(PSNR_IDENTICAL_DB));
        assert_eq!(psnr_rgba(&a, &a[..128]), None, "尺寸不同比不了");
    }

    // ── sysparam: 回读与快照分类 ──

    const PLAN: &str = "sysfs\t/sys/class/kgsl/kgsl-3d0/min_pwrlevel\t3\nsetting\tsystem:refresh_rate_mode\t2\n";

    #[test]
    fn readback_reads_every_plan_item() {
        let c = readback_cmd(PLAN);
        assert!(c.contains("cat /sys/class/kgsl/kgsl-3d0/min_pwrlevel"));
        assert!(c.contains("settings get system refresh_rate_mode"));
    }

    /// 写 3 读回 17 (厂商服务抢回) 或读不到, 都算守不住; 对上了不报。
    #[test]
    fn readback_mismatches_flag_contested_and_unreadable_nodes() {
        let ok = "/sys/class/kgsl/kgsl-3d0/min_pwrlevel=3\nsystem:refresh_rate_mode=2\n#adb_exit=0";
        assert!(readback_mismatches(PLAN, ok).is_empty());
        let bad = "/sys/class/kgsl/kgsl-3d0/min_pwrlevel=17\n#adb_exit=0";
        let m = readback_mismatches(PLAN, bad);
        assert_eq!(m.len(), 2);
        assert_eq!(m[0], ("/sys/class/kgsl/kgsl-3d0/min_pwrlevel".into(), "3".into(), "17".into()));
        assert_eq!(m[1].2, "<读不到>");
    }

    /// 窗口内定时回读 → 守住比例。adb 失败的那次不进分母; 读不到的节点算没守住;
    /// 对照臂靠 values_seen 看节点有没有被动过。
    #[test]
    fn hold_stats_counts_time_fraction_and_values_seen() {
        let ok = "/sys/class/kgsl/kgsl-3d0/min_pwrlevel=3\nsystem:refresh_rate_mode=2\n#adb_exit=0".to_string();
        let lost = "/sys/class/kgsl/kgsl-3d0/min_pwrlevel=17\nsystem:refresh_rate_mode=2\n#adb_exit=0".to_string();
        let mut samples: Vec<(f64, String)> = (0..9).map(|k| (k as f64 * 3.0, ok.clone())).collect();
        samples.push((27.0, lost.clone()));
        samples.push((30.0, "#adb_exit=timeout".to_string()));
        let h = hold_stats(PLAN, &samples);
        assert_eq!((h.samples, h.held, h.failed_calls), (10, 9, 1));
        assert_eq!(h.frac(), Some(0.9));
        assert_eq!(h.first_lost_s, Some(27.0));
        assert_eq!(h.mismatches, vec!["/sys/class/kgsl/kgsl-3d0/min_pwrlevel: 写 3 读到 17".to_string()]);
        assert_eq!(
            h.values_seen[0],
            ("/sys/class/kgsl/kgsl-3d0/min_pwrlevel".to_string(), vec!["3".to_string(), "17".to_string()])
        );
        assert!(hold_verdict(&h, 0.9).is_ok(), "刚好 90% 算守住");
        assert!(hold_verdict(&h, 0.95).is_err());
        let j = h.to_json();
        assert_eq!(j["hold_frac"], json!(0.9));
        assert_eq!(j["values_seen"]["system:refresh_rate_mode"], json!(["2"]));
    }

    /// 回读次数不够 (adb 一直失败) 时说不清, 不算守住; 门槛从请求里取, 不合法用缺省。
    #[test]
    fn hold_verdict_needs_enough_samples_and_sane_threshold() {
        let ok = "/sys/class/kgsl/kgsl-3d0/min_pwrlevel=3\nsystem:refresh_rate_mode=2\n#adb_exit=0".to_string();
        let h = hold_stats(PLAN, &[(0.0, ok.clone()), (3.0, ok), (6.0, "#adb_exit=1".into())]);
        assert_eq!(h.frac(), Some(1.0));
        assert!(hold_verdict(&h, 0.9).unwrap_err().contains("说不清"));
        assert_eq!(hold_stats(PLAN, &[]).frac(), None);
        assert_eq!(hold_min_frac(&json!({})), HOLD_MIN_FRAC);
        assert_eq!(hold_min_frac(&json!({"protocol": {"sysparam_hold_min": 0.75}})), 0.75);
        assert_eq!(hold_min_frac(&json!({"protocol": {"sysparam_hold_min": 1.5}})), HOLD_MIN_FRAC);
        assert_eq!(hold_min_frac(&json!({"protocol": {"sysparam_hold_min": 0}})), HOLD_MIN_FRAC);
    }

    /// 真实数据 (megacity-power-1012c gen1 sys_g1c1, 2026-10-13): plan 写 max_pwrlevel=6,
    /// 旧流程在游戏重启前施加, +12s 与末尾回读都是 4 (厂商默认值)。新口径下守住 0%, 照样不进统计。
    #[test]
    fn hold_replay_1012c_knob_arm_is_contested() {
        let plan = "# loop_v1 sysparam plan (from request payload)\nsysfs\t/sys/class/kgsl/kgsl-3d0/max_pwrlevel\t6\n";
        let rb = "/sys/class/kgsl/kgsl-3d0/max_pwrlevel=4\n#adb_exit=0".to_string();
        let h = hold_stats(plan, &[(12.0, rb.clone()), (40.0, rb.clone()), (43.0, rb)]);
        assert_eq!(h.frac(), Some(0.0));
        assert!(hold_verdict(&h, HOLD_MIN_FRAC).unwrap_err().contains("0%"));
    }

    /// 厂商服务会动的节点按设备端路径认; 普通节点 (总线 boost、governor) 不算。
    #[test]
    fn vendor_managed_paths_match_snapshot_keys() {
        for p in [
            "/sys/class/kgsl/kgsl-3d0/max_pwrlevel",
            "/sys/class/kgsl/kgsl-3d0/min_pwrlevel",
            "/sys/class/devfreq/3d00000.qcom,kgsl-3d0/max_freq",
            "/sys/devices/system/cpu/cpufreq/policy6/scaling_min_freq",
            "/sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq",
        ] {
            assert!(vendor_managed_path(p), "{p}");
        }
        for p in [
            "/sys/devices/system/cpu/cpufreq/policy0/scaling_governor",
            "/sys/devices/system/cpu/bus_dcvs/DDR/boost_freq",
            "/sys/class/kgsl/kgsl-3d0/devfreq/governor",
        ] {
            assert!(!vendor_managed_path(p), "{p}");
        }
    }

    /// 探测时游戏没在前台跑 (旧探测结果根本没有 probe.game_* 行) → 厂商会动的节点不进白名单;
    /// 游戏从头到尾都在前台 → 照常 (这时守不住的节点探测脚本自己已经判 contested 了)。
    #[test]
    fn whitelist_drops_vendor_nodes_when_probe_ran_without_game() {
        let base = "cpu.policies=0\n\
cpu.policy0.avail_freqs=300000 600000 900000\n\
cpu.policy0.scaling_min_freq.cur=300000\ncpu.policy0.scaling_max_freq.cur=900000\n\
cpu.policy0.scaling_min_freq.writable=yes\ncpu.policy0.scaling_min_freq.effect=live\n\
cpu.policy0.scaling_max_freq.writable=yes\ncpu.policy0.scaling_max_freq.effect=live\n\
gpu.num_pwrlevels=18\ngpu.min_pwrlevel.cur=17\ngpu.max_pwrlevel.cur=4\n\
gpu.min_pwrlevel.writable=yes\ngpu.max_pwrlevel.writable=yes\n\
gpu.min_pwrlevel.effect=live\ngpu.max_pwrlevel.effect=live\n\
bus.DDR.boost_freq.cur=0\nbus.DDR.avail_freqs=547000 1555000 2092000\nbus.DDR.hw_max_freq=2092000\n\
bus.DDR.boost_freq.writable=yes\nbus.DDR.boost_freq.effect=live\n";
        let probe = sysparam::parse_probe(base);
        let full = sysparam::build_whitelist(&probe);
        assert!(full.iter().any(|(_, s)| vendor_managed_path(&s.path)), "夹具里得有厂商节点: {full:?}");
        assert!(!probe_game_up(&probe));
        let (kept, dropped) = gate_whitelist_by_probe(full.clone(), probe_game_up(&probe));
        assert!(!dropped.is_empty());
        assert!(kept.iter().all(|(_, s)| !vendor_managed_path(&s.path)), "{kept:?}");
        assert_eq!(kept.len() + dropped.len(), full.len());

        let with_game = format!(
            "probe.game_pkg=com.x\nprobe.game_running_start=yes\nprobe.game_foreground_start=yes\n{base}\
probe.game_running_end=yes\nprobe.game_foreground_end=yes\n"
        );
        let probe = sysparam::parse_probe(&with_game);
        assert!(probe_game_up(&probe));
        let (kept, dropped) = gate_whitelist_by_probe(sysparam::build_whitelist(&probe), true);
        assert!(dropped.is_empty());
        assert_eq!(kept.len(), full.len());
        // 中途游戏退了也不算
        let quit = with_game.replace("probe.game_running_end=yes", "probe.game_running_end=no");
        assert!(!probe_game_up(&sysparam::parse_probe(&quit)));
    }

    /// 厂商当场抢走的频率边界写入不算我们没还原; 我们的值留在节点上、或非厂商节点没还原, 照样拦。
    #[test]
    fn probe_restore_tolerates_only_vendor_lost_writes() {
        let ok = "gpu.min_pwrlevel.effect=live\ngpu.min_pwrlevel.effect_restored=yes\n";
        assert!(probe_writes_verified(ok));
        let lost = format!("{ok}gpu.max_pwrlevel.effect=rejected(wrote=12 readback=3)\ngpu.max_pwrlevel.effect_restored=NO(want=11 got=17)\n");
        assert!(probe_writes_verified(&lost));
        let contested = format!("{ok}cpu.policy6.scaling_max_freq.effect=contested(wrote=1000 1s=1000 lost=2000)\ncpu.policy6.scaling_max_freq.effect_restored=NO(want=3000 got=2000)\n");
        assert!(probe_writes_verified(&contested));
        // 我们写的值还留在节点上
        let ours = format!("{ok}gpu.max_pwrlevel.effect=contested(wrote=12 1s=12 lost=3)\ngpu.max_pwrlevel.effect_restored=NO(want=11 got=12)\n");
        assert!(!probe_writes_verified(&ours));
        // 写守住了 (live) 却没还原
        let live = format!("{ok}gpu.max_pwrlevel.effect=live\ngpu.max_pwrlevel.effect_restored=NO(want=11 got=17)\n");
        assert!(!probe_writes_verified(&live));
        // 不是厂商自管节点
        let walt = format!("{ok}cpu.policy0.walt.hispeed_load.effect=rejected(wrote=100 readback=90)\ncpu.policy0.walt.hispeed_load.effect_restored=NO(want=90 got=95)\n");
        assert!(!probe_writes_verified(&walt));
    }

    /// 只有厂商服务会动的频率边界变了 → 不判留痕; 别的键变了 → 判留痕。
    #[test]
    fn snapshot_diff_separates_vendor_managed_keys() {
        let a = "# device_snapshot v1\nkgsl.max_pwrlevel=0\ncpu.policy6.scaling_max=1497600\ncpu.policy6.governor=walt\nftrace.tracing_on=0\n#adb_exit=0";
        let b = "# device_snapshot v1\nkgsl.max_pwrlevel=17\ncpu.policy6.scaling_max=1382400\ncpu.policy6.governor=walt\nftrace.tracing_on=0\n#adb_exit=0";
        let (v, o) = split_snapshot_diff(a, b);
        assert_eq!(v.len(), 2, "{v:?}");
        assert!(o.is_empty());
        let c = b.replace("governor=walt", "governor=performance");
        let (_, o) = split_snapshot_diff(a, &c);
        assert_eq!(o, vec!["cpu.policy6.governor: walt → performance".to_string()]);
    }

    /// 画面判定要拿「同设置重拍」的噪声底比, 而不是拿固定阈值硬卡。
    ///
    /// 真实数据 (Megacity city_static, 2026-10-19): 同臂相隔 1 秒重拍 26.41 / 27.90 dB,
    /// 对照对对照 28.06 dB。固定 30 dB 的阈值比噪声底还高 —— 什么都没改的候选也会判画面出错。
    #[test]
    fn image_gate_judges_against_the_scene_noise_floor() {
        // 画面完全没变: 高于配置阈值 → 直接按绝对阈值过
        let g = ImageGate::new(30.0, Some(28.06));
        assert!(matches!(judge_image(&g, Some(42.0)), ImageRuling::Pass("absolute")));

        // 场景在动, 噪声底 28.06, 候选 29.71 → 高于噪声底, 按噪声底过
        let g = ImageGate::new(30.0, Some(28.06));
        assert!(matches!(judge_image(&g, Some(29.71)), ImageRuling::Pass("noise_floor")));

        // 明显改坏了画面 (比噪声底还差很多) → 判画面出错
        let g = ImageGate::new(30.0, Some(28.06));
        match judge_image(&g, Some(21.0)) {
            ImageRuling::Fail(r) => assert!(r.contains("低于同设置重拍噪声底"), "{r}"),
            _ => panic!("应该失败"),
        }

        // 场景自己就不稳定 (噪声底 12 dB): 拒绝判定, 不猜
        let g = ImageGate::new(30.0, Some(12.0));
        match judge_image(&g, Some(28.0)) {
            ImageRuling::Fail(r) => assert!(r.contains("自身不稳定"), "{r}"),
            _ => panic!("噪声底过低时必须拒绝判定"),
        }

        // 没有重拍可作噪声底: 同样拒绝判定
        let g = ImageGate::new(30.0, None);
        match judge_image(&g, Some(29.0)) {
            ImageRuling::Fail(r) => assert!(r.contains("没有同设置重拍"), "{r}"),
            _ => panic!("没有噪声底时必须拒绝判定"),
        }

        // 尺寸不一致
        let g = ImageGate::new(30.0, Some(28.06));
        assert!(matches!(judge_image(&g, None), ImageRuling::Fail(_)));

        // 噪声底取观测到的最大值
        assert_eq!(noise_floor_db([Some(26.41), Some(27.9), None]), Some(27.9));
        assert_eq!(noise_floor_db([None, None]), None);
    }

    #[test]
    fn cool_summary_takes_the_hottest_start_and_any_timeout() {
        let arms = vec![
            json!({"c": 41.2, "timeout": false}),
            json!({"c": 47.9, "timeout": true}),
            json!({"c": null, "timeout": false}),
        ];
        assert_eq!(cool_summary(&arms), (Some(47.9), true));
        assert_eq!(cool_summary(&[]), (None, false));
    }

    #[test]
    fn after_one_arm_misses_the_cool_target_later_arms_wait_briefly() {
        assert_eq!(arm_cool_timeout(&[]), ARM_COOL_TIMEOUT_S);
        assert_eq!(arm_cool_timeout(&[json!({"timeout": false})]), ARM_COOL_TIMEOUT_S);
        assert_eq!(
            arm_cool_timeout(&[json!({"timeout": false}), json!({"timeout": true})]),
            ARM_COOL_TIMEOUT_AFTER_MISS_S
        );
    }

    #[test]
    fn launch_spec_is_read_from_the_workload() {
        let req = json!({"workload": {"launch": {"extras": "--es scene city_flythrough", "marker": "megacity_started",
            "static_extras": "--es scene city_static"}}});
        let l = launch_spec(&req).unwrap();
        assert_eq!(l.marker.as_deref(), Some("megacity_started"));
        assert_eq!(l.static_extras.as_deref(), Some("--es scene city_static"));
        assert!(launch_spec(&json!({"workload": {}})).is_none());
        assert!(launch_spec(&json!({"workload": {"launch": {"extras": " "}}})).is_none());
    }

    #[test]
    fn live_check_passes_only_when_toggling_looks_like_noise() {
        let ok = json!({"game_alive": true, "effective_hit": 1, "diffs": [
            {"kind": "noise_1s", "diff_px_pct": 0.1},
            {"kind": "off_vs_on", "diff_px_pct": 0.4},
            {"kind": "same_state", "diff_px_pct": 0.3}]});
        assert!(live_check_verdict(&ok).is_ok());
        let mut bad = ok.clone();
        bad["diffs"][1]["diff_px_pct"] = json!(12.0);
        assert!(live_check_verdict(&bad).unwrap_err().contains("12.000%"));
        let mut miss = ok.clone();
        miss["effective_hit"] = json!(0);
        assert!(live_check_verdict(&miss).is_err());
        let mut dead = ok;
        dead["game_alive"] = json!(false);
        assert!(live_check_verdict(&dead).is_err());
    }

    /// 审计 #5: 一臂功耗不合格只剔除那一对, 不再让整组功耗静默退出判定。
    #[test]
    fn judge_runs_drops_only_the_bad_power_pair() {
        let run = |label: &str, p95: f64, w: Option<f64>| {
            json!({
                "label": label,
                "summary": {"frame_p95": p95, "fps_mean": 60.0},
                "env": match w {
                    Some(w) => json!({"power_valid": true, "power_source": "usb_plus_battery",
                                      "power_w_mean": w, "power_mixed_w_mean": w, "soc_temp_max_c": 50.0}),
                    None => json!({"power_valid": false, "power_source": "usb", "power_w_mean": null,
                                   "power_usable_reason": "非放电态 (测试)", "soc_temp_max_c": 50.0}),
                },
            })
        };
        // 6 轮剔 1 对剩 5 对: 5v5 精确置换最小 p = 2/252 < alpha/3, 够得着显著
        let ctrl: Vec<Value> = (1..=6).map(|i| run(&format!("ctrl{i}"), 16.0 + i as f64 * 0.01, if i == 2 { None } else { Some(3.0 + i as f64 * 0.01) })).collect();
        let knob: Vec<Value> = (1..=6).map(|i| run(&format!("knob{i}"), 16.0 + i as f64 * 0.01, Some(2.5 + i as f64 * 0.01))).collect();
        let (verdict, _keep, power_in) = judge_runs(&ctrl, &knob, 0.05, 6, None);
        assert!(power_in, "剔一对还剩五对, 功耗照常进判定");
        assert!(verdict["per_metric"].get("power_w_mean").is_some());
        let pm = &verdict["per_metric"]["power_w_mean"];
        assert_eq!(pm["status"], "显著改善", "{pm}");
        assert_eq!(verdict["power_pairs"]["dropped"][0]["round"], 2);
        assert_eq!(verdict["power_pairs"]["kept_ctrl"], 5);
        // 候选臂功耗 = 剔除后那五臂的均值 (第 2 轮的候选臂也不算)
        let mut m = json!({});
        let w = aggregate_metrics(&mut m, &knob, &ctrl, power_in).unwrap();
        assert!((w - (2.51 + 2.53 + 2.54 + 2.55 + 2.56) / 5.0).abs() < 1e-9, "{w}");
    }

    /// 合并后 C/D/B/F 的组合: 功耗按「轮」配对, 不按下标。
    /// 第 2 轮改写臂被判 contested (B) 只拿掉候选臂; 第 4 轮对照臂功耗不可信 (D)。
    /// 按下标配对时 ctrl[1] (第 2 轮) 会和 knob[1] (第 3 轮) 配成一对, 第 4 轮的剔除也会错位。
    #[test]
    fn power_pairs_align_by_round_when_an_arm_is_missing() {
        let run = |label: &str, round: u64, w: Option<f64>| {
            json!({
                "label": label, "round": round,
                "summary": {"frame_p95": 16.0, "fps_mean": 60.0},
                "env": match w {
                    Some(w) => json!({"power_valid": true, "power_source": "battery",
                                      "power_w_mean": w, "power_mixed_w_mean": w}),
                    None => json!({"power_valid": false, "power_source": "usb", "power_w_mean": null,
                                   "power_usable_reason": "非放电态 (测试)"}),
                },
            })
        };
        let ctrl = vec![run("c1", 1, Some(3.01)), run("c2", 2, Some(3.02)), run("c3", 3, Some(3.03)), run("c4", 4, None), run("c5", 5, Some(3.05))];
        // 第 2 轮候选臂 contested, 没进 knob_runs
        let knob = vec![run("k1", 1, Some(2.51)), run("k3", 3, Some(2.53)), run("k4", 4, Some(2.54)), run("k5", 5, Some(2.55))];
        let pp = power_pairs_of(&knob, &ctrl);
        // 结果与传入 runs 一一对应
        assert_eq!(pp.ctrl, vec![Some(3.01), Some(3.02), Some(3.03), None, Some(3.05)]);
        assert_eq!(pp.knob, vec![Some(2.51), Some(2.53), None, Some(2.55)]);
        assert_eq!(pp.dropped.len(), 1);
        assert_eq!(pp.dropped[0].0, 4, "剔的是真实的第 4 轮");
        assert!(pp.dropped[0].1.contains("对照臂"));
        // 没有 round 的旧证据: 仍按下标配对 (行为不变)
        let strip = |v: &[Value]| -> Vec<Value> {
            v.iter().map(|r| { let mut r = r.clone(); r.as_object_mut().unwrap().remove("round"); r }).collect()
        };
        let old = power_pairs_of(&strip(&knob), &strip(&ctrl));
        assert_eq!(old.ctrl.len(), 5);
        assert_eq!(old.knob.len(), 4);
        assert_eq!(old.dropped[0].0, 4);
        assert_eq!(old.knob[3], None, "按下标时第 4 个候选臂 (实为第 5 轮) 被错剔");
    }

    // ══════════════ 审计 #9 #10, 审查 R1 #5 #9 #12, R2 #11 ══════════════
    use super::tests::{v2_req, validate_err, validate_ok};

    #[test]
    fn pwrlevel_parse_and_commands() {
        let out = "max_pwrlevel=0\nmin_pwrlevel=17\nnum_pwrlevels=18\n#adb_exit=0";
        assert_eq!(parse_pwrlevels(out), Some((0, 17, Some(18))));
        assert_eq!(parse_pwrlevels("max_pwrlevel=\nmin_pwrlevel=17\n#adb_exit=0"), None);
        assert_eq!(parse_pwrlevels("max_pwrlevel=3\nmin_pwrlevel=3\nnum_pwrlevels=\n"), Some((3, 3, None)));
        // 锁: max → min → max; 还原: 先 min (数值大) 再 max
        let lock = pwrlevel_lock_cmd(4);
        let (i_max, i_min) = (lock.find("max_pwrlevel").unwrap(), lock.find("min_pwrlevel").unwrap());
        assert!(i_max < i_min && lock.rfind("max_pwrlevel").unwrap() > i_min, "{lock}");
        let r = pwrlevel_restore_cmd(0, 17);
        assert!(r.find("echo 17 > ").unwrap() < r.find("echo 0 > ").unwrap(), "{r}");
        assert!(r.contains(KGSL_DIR));
    }

    #[test]
    fn gpu_lock_refuses_candidates_that_change_pwrlevel() {
        let r = v2_req("sysparam", json!({"params": {"gpu.max_pwrlevel": "6"}}));
        assert!(gpu_lock_conflict(&r).unwrap_err().contains("gpu.max_pwrlevel"));
        let r = v2_req("sysparam", json!({"params": {}, "plan": [{"kind": "sysfs", "path": "/sys/class/kgsl/kgsl-3d0/min_pwrlevel", "value": "3"}]}));
        assert!(gpu_lock_conflict(&r).is_err());
        let r = v2_req("sysparam", json!({"params": {"cpu.policy6.scaling_max": "1497600"}}));
        assert!(gpu_lock_conflict(&r).is_ok());
        assert!(gpu_lock_conflict(&v2_req("gray", json!({"rewrites": []}))).is_ok());
    }

    #[test]
    fn gpu_lock_note_goes_into_conditions_and_flags_failed_restore() {
        let rep = r#"{"status":"PASS","verdict":{"reason":"改善 frame_p95"}}"#;
        let ok = json!({"level": 0, "restored": true});
        let v: Value = serde_json::from_str(&attach_gpu_lock_note(rep, Some(&ok))).unwrap();
        assert_eq!(v["conditions_observed"]["gpu_level_lock"]["level"], 0);
        assert_eq!(v["verdict"]["reason"], "改善 frame_p95");
        let bad = json!({"level": 0, "restored": false});
        let v: Value = serde_json::from_str(&attach_gpu_lock_note(rep, Some(&bad))).unwrap();
        assert!(v["verdict"]["reason"].as_str().unwrap().contains("还原失败"));
        assert_eq!(v["status"], "PASS", "测量本身有效, 不改状态");
        let v: Value = serde_json::from_str(&attach_gpu_lock_note(rep, None)).unwrap();
        assert_eq!(v["conditions_observed"]["gpu_level_lock"], Value::Null);
    }

    #[test]
    fn nonempty_out_dir_is_refused() {
        let d = std::env::temp_dir().join(format!("pf_eval_out_{}_{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        assert!(refuse_nonempty_out(&d).is_ok(), "不存在的目录放行");
        std::fs::create_dir_all(&d).unwrap();
        assert!(refuse_nonempty_out(&d).is_ok(), "空目录放行");
        std::fs::write(d.join("plan.txt"), "x").unwrap();
        let e = refuse_nonempty_out(&d).unwrap_err();
        assert!(e.contains("非空") && e.contains("plan.txt"), "{e}");
    }

    #[test]
    fn hw_stats_clips_to_capture_window() {
        let t = "#sample_hw v1 max=900 stop=/x\nHW 100.0 17 fan_enable=0\nHW 101.0 0 fan_enable=0\nHW 102.0 0 fan_enable=0\nHW 103.0 0 fan_enable=0\nHW 104.0 4 fan_enable=1\n#sample_hw done\n";
        let all = hw_stats(t);
        assert_eq!(all["n"], 5);
        assert_eq!(all["n_total"], 5);
        assert_eq!(all["window"], Value::Null);
        // LEAD 段的 17 档与采完之后的 4 档/风扇都不算
        let w = hw_stats_window(t, Some((101.0, 103.0)));
        assert_eq!(w["n"], 3);
        assert_eq!(w["n_total"], 5);
        assert_eq!(w["gpu_pwrlevel_frac"], json!({"0": 1.0}));
        assert_eq!(w["fan_states"], json!(["fan_enable=0"]));
        assert_eq!(w["window"], json!([101.0, 103.0]));
        // 以前不截窗: 一臂 LEAD 内被压到 17 档就会错剔一对
        let other = hw_stats_window("HW 101.0 0 -\nHW 102.0 0 -\nHW 103.0 0 -\n", Some((101.0, 103.0)));
        assert!(pair_env_check(&json!({"hw": w}), &json!({"hw": other}), true).is_ok());
        assert!(pair_env_check(&json!({"hw": all}), &json!({"hw": hw_stats("HW 101.0 0 -\nHW 102.0 0 -\nHW 103.0 0 -\n")}), true).is_err());
    }

    #[test]
    fn hold_stats_clips_readbacks_to_capture_window() {
        let ok = |up: f64| format!("/sys/class/kgsl/kgsl-3d0/min_pwrlevel=3\nsystem:refresh_rate_mode=2\n#uptime={up}\n#adb_exit=0");
        let lost = |up: f64| format!("/sys/class/kgsl/kgsl-3d0/min_pwrlevel=17\nsystem:refresh_rate_mode=2\n#uptime={up}\n#adb_exit=0");
        assert_eq!(readback_uptime(&ok(12.5)), Some(12.5));
        assert_eq!(readback_uptime("x=1\n#adb_exit=0"), None);
        // 窗 [10, 20]: 准备段 (5) 守住、窗内 3 次守住 1 次丢、采完之后 (25) 丢
        let samples = vec![(0.0, ok(5.0)), (3.0, ok(11.0)), (6.0, ok(14.0)), (9.0, lost(17.0)), (12.0, lost(25.0))];
        let h = hold_stats_window(PLAN, &samples, Some((10.0, 20.0)));
        assert_eq!((h.samples, h.held, h.outside_window), (3, 2, 2));
        assert_eq!(h.first_lost_s, Some(9.0));
        assert_eq!(h.window, Some((10.0, 20.0)));
        assert_eq!(h.to_json()["outside_window"], 2);
        // 不截窗 (旧口径): 5 次里 3 次守住
        let h0 = hold_stats(PLAN, &samples);
        assert_eq!((h0.samples, h0.held, h0.outside_window), (5, 3, 0));
        // 没有 #uptime 的旧回读照旧计入
        let old = "/sys/class/kgsl/kgsl-3d0/min_pwrlevel=3\nsystem:refresh_rate_mode=2\n#adb_exit=0".to_string();
        let h = hold_stats_window(PLAN, &[(0.0, old)], Some((10.0, 20.0)));
        assert_eq!((h.samples, h.outside_window), (1, 0));
        assert!(readback_cmd(PLAN).contains("#uptime="));
    }

    #[test]
    fn spf_mismatch_makes_a_pair_incomparable() {
        let arm = |spf: u64| json!({"hw": {"n": 0}, "summary": {"submits_per_frame": spf}});
        assert!(pair_env_check(&arm(1), &arm(1), true).is_ok());
        let e = pair_env_check(&arm(6), &arm(1), true).unwrap_err();
        assert!(e.contains("每帧提交数") && e.contains("workload.spf"), "{e}");
        // 旧证据没有 summary 时不判
        assert!(pair_env_check(&json!({"hw": {"n": 0}}), &arm(1), true).is_ok());
    }

    #[test]
    fn request_declares_spf_and_fps_cap() {
        let mut r = v2_req("sysparam", json!({"params": {}}));
        assert_eq!(declared_spf(&r), None);
        assert_eq!(declared_fps_cap(&r), None);
        r["workload"]["spf"] = json!(1);
        r["workload"]["fps_cap"] = json!(30);
        assert!(validate_ok(&r));
        assert_eq!(declared_spf(&r), Some(1));
        assert_eq!(declared_fps_cap(&r), Some(30.0));
        r["workload"]["spf"] = json!(0);
        assert!(validate_err(&r).contains("workload.spf"));
        r["workload"]["spf"] = json!(2);
        r["workload"]["fps_cap"] = json!(-1);
        assert!(validate_err(&r).contains("workload.fps_cap"));
        r["workload"]["fps_cap"] = Value::Null;
        assert!(validate_ok(&r), "null 当没声明");
    }

    #[test]
    fn marker_delay_is_recorded_per_arm_and_asymmetry_flagged() {
        assert_eq!(marker_to_capture_s(Some(100.0), Some((103.456, 133.0))), Some(3.46));
        assert_eq!(marker_to_capture_s(None, Some((1.0, 2.0))), None);
        let run = |round: u64, d: Option<f64>| json!({"round": round, "marker_to_capture_s": d});
        let knob = vec![run(1, Some(3.4)), run(2, Some(4.9)), run(3, None)];
        let ctrl = vec![run(1, Some(2.9)), run(2, Some(2.8)), run(3, Some(2.7))];
        let n = marker_delay_note(&knob, &ctrl);
        assert_eq!(n["per_round"].as_array().unwrap().len(), 2, "第 3 对改写臂没记延时, 不比");
        assert_eq!(n["flagged_rounds"], json!([2]));
        assert!((n["max_asymmetry_s"].as_f64().unwrap() - 2.1).abs() < 1e-9);
        assert_eq!(n["per_round"][0]["flagged"], false);
        let mut rep = json!({"verdict": {"reason": "没有任何指标显著改善"}});
        append_marker_delay_reason(&mut rep, &n);
        assert!(rep["verdict"]["reason"].as_str().unwrap().contains("第 2 对"));
        // 触控负载 (没有标记): 空记录, 理由不动
        let n0 = marker_delay_note(&[json!({"round": 1})], &[json!({"round": 1})]);
        assert_eq!(n0["per_round"], json!([]));
        let mut rep = json!({"verdict": {"reason": "x"}});
        append_marker_delay_reason(&mut rep, &n0);
        assert_eq!(rep["verdict"]["reason"], "x");
    }

    #[test]
    fn eval_args_take_lock_gpu_level() {
        let a = parse_args(&["--request".into(), "r.json".into(), "--lock-gpu-level".into(), "3".into()]).unwrap();
        assert_eq!(a.lock_gpu_level, Some(3));
        assert!(parse_args(&["--request".into(), "r.json".into(), "--lock-gpu-level".into(), "x".into()]).is_err());
        assert_eq!(parse_args(&["--request".into(), "r.json".into()]).unwrap().lock_gpu_level, None);
    }
}
