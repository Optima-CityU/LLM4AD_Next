//! phonefarm v0.2 — 记录契约 v1 运行时
//! 用法: phonefarm run --task <任务名> [--serial <设备>] [--endless] [--budget-calls N] [--max-steps N] [--app <包名>] "<目标>"
//!       phonefarm devices
//! --app: 任务的目标应用包名;开局若前台不是它(也不是桌面),先按HOME归位再进循环
//! --serial 带 "hdc:<connect key>" 前缀走 OpenHarmony/hdc 后端,不带前缀=Android/adb(devices 子命令两族并列)
mod autoloop;
mod app_snapshot;
mod bench;
mod bench_session;
mod lease_command;
mod ftrace;
mod gpuop;
mod refbench_timing;
mod shaderplan;
mod gpustat;
mod hwcond;
mod brain;
mod capture;
mod capture_session;
mod capture_worker;
mod cli;
mod collect;
mod decision;
mod ios;
mod cts;
mod experiment;
mod fleet;
mod keepalive;
mod loopreport;
mod loopstat;
mod crosscheck;
mod framecheck;
mod llm;
mod looptrace;
mod render_attribution;
mod sysparam;
mod curtail;
mod vksreport;
mod whitebox;
mod whitebox_refbench;
mod parallel;
mod device;
mod platform;
mod devlock;
mod devlease;
mod fd_lock;
mod lock_handoff;
mod device_registry;
mod host_process;
mod observation;
mod lease_receipt;
mod lease_storage;
mod eval;
mod onboard;
mod qualification;
mod thermal_profile;
mod confirmation_observation;
mod confirmation_control;
mod confirmation_driver;
mod confirmation_transport;
mod fold;
mod gamepad;
mod game_deploy;
mod game_deploy_session;
mod game_deploy_transport;
mod remote_control;
mod runtime_identity;
mod game_initialize;
mod game_scene;
mod game_install_session;
mod apk_metadata;
mod frameaudit;
mod preflight;
mod grayab;
mod graylayer;
mod gray_session;
mod gray_refbench;
mod gray_control;
mod gray_quality;
mod gray_script;
mod gray_eval_session;
mod grayartifact;
mod upopabi;
mod storageadapter;
mod npuop_device;
mod shotdiff;
mod sequence_quality;
mod iqjudge;
mod iqsheet;
mod interpjudge;
mod scorecard;
mod iqdiagnose;
mod hypo;
mod mtools;
mod caps;
mod gpdaemon;
mod perfsrc;
mod framesrc;
mod sflatency;
pub mod pyjson;
mod pyrandom;
mod refbenchreport;
mod smartperf;
mod plugins;
mod runtime;
mod screen;
mod script;
mod selftest;
mod serve;
mod telemetry;
mod vks;
mod tree;
pub mod universal;

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Deserialize, Serialize)]
pub struct HookCfg {
    pub on: String,
    #[serde(default)]
    pub prompt: Option<String>,
    /// 确定性时间点(无模型调用): "budget" | "heal"
    #[serde(default)]
    pub builtin: Option<String>,
    #[serde(default)]
    pub input: Option<Vec<String>>,
    #[serde(default)]
    pub output: Option<String>,
}

#[derive(Deserialize, Serialize, Clone)]
pub struct EvoCfg {
    /// 总开关: false 时假设/检验/注入全部静默旁路,行为与旧版一致(SPEC_EVOLUTION §10)
    #[serde(default = "d_evo_enabled")]
    pub enabled: bool,
    /// hypothesize 模型调用限额(每局)
    #[serde(default = "d_evo_hyp_calls")]
    pub hyp_calls_per_episode: u32,
    /// 检验动作限额(每局)
    #[serde(default = "d_evo_test_actions")]
    pub test_actions_per_episode: u32,
    /// 决策上下文注入假设上限
    #[serde(default = "d_evo_inject_max")]
    pub inject_max: usize,
}
impl Default for EvoCfg {
    fn default() -> Self {
        EvoCfg {
            enabled: d_evo_enabled(),
            hyp_calls_per_episode: d_evo_hyp_calls(),
            test_actions_per_episode: d_evo_test_actions(),
            inject_max: d_evo_inject_max(),
        }
    }
}
fn d_evo_enabled() -> bool { true }
fn d_evo_hyp_calls() -> u32 { 2 } // 局内1次+局末1次(T1/T2 各占一次, SPEC_EVOLUTION v1.1)
fn d_evo_test_actions() -> u32 { 2 }
fn d_evo_inject_max() -> usize { 6 }

#[derive(Deserialize, Serialize)]
pub struct Config {
    #[serde(default = "d_data_dir")]
    pub data_dir: String,
    #[serde(default = "d_max_steps")]
    pub max_steps: u32,
    #[serde(default = "d_settle")]
    pub settle_ms: u64,
    #[serde(default = "d_els_to")]
    pub els_timeout_ms: u64,
    #[serde(default = "d_ban_radius")]
    pub ban_radius: i32,
    #[serde(default = "d_ban_strikes")]
    pub ban_strikes: u32,
    #[serde(default = "d_stall")]
    pub stall_limit: u32,
    #[serde(default = "d_heal_bt")]
    pub heal_ban_threshold: u32,
    #[serde(default = "d_note_max")]
    pub note_max_chars: usize,
    #[serde(default = "d_lesson_max")]
    pub lesson_max_items: usize,
    #[serde(default = "d_window")]
    pub window_pairs: usize,
    #[serde(default = "d_plan_max")]
    pub plan_max: usize,
    /// 设备复活用: 模拟器启动命令(空=只重启adb不重启模拟器)
    #[serde(default)]
    pub emulator_cmd: String,
    /// #21 done预检: 首次done不定局,注入事实给模型一次终态自查(默认开;false=旧行为)
    #[serde(default = "d_done_reflect")]
    pub done_reflect: bool,
    /// 遥测(Telemetry Spec v1.0): 每步只读采集性能账进 log.jsonl,不进模型上下文(默认开)
    #[serde(default = "d_telemetry")]
    pub telemetry: bool,
    /// 重量级遥测明细的采集间隔(步);高频字段每步采
    #[serde(default = "d_tele_interval")]
    pub telemetry_interval: u32,
    #[serde(default)]
    pub prompts: HashMap<String, String>,
    #[serde(default, rename = "hook")]
    pub hooks: Vec<HookCfg>,
    pub providers: Vec<brain::ProviderCfg>,
    /// 持续进化(假设—检验—证据闭环, SPEC_EVOLUTION §10)
    #[serde(default)]
    pub evolution: EvoCfg,
}
fn d_data_dir() -> String { ".".into() }
fn d_max_steps() -> u32 { 12 }
fn d_settle() -> u64 { 1200 }
fn d_els_to() -> u64 { 2500 }
fn d_ban_radius() -> i32 { 30 }
fn d_ban_strikes() -> u32 { 2 }
fn d_stall() -> u32 { 6 }
fn d_heal_bt() -> u32 { 5 }
fn d_note_max() -> usize { 200 }
fn d_lesson_max() -> usize { 20 }
fn d_window() -> usize { 5 }
fn d_plan_max() -> usize { 4 }
fn d_done_reflect() -> bool { true }
fn d_telemetry() -> bool { true }
fn d_tele_interval() -> u32 { 5 }

impl Config {
    pub fn hook_for(&self, on: &str) -> Option<&HookCfg> {
        self.hooks.iter().find(|h| h.on == on)
    }
    pub fn prompt_of(&self, hook: &HookCfg) -> Option<&str> {
        hook.prompt
            .as_deref()
            .and_then(|n| self.prompts.get(n).map(|s| s.as_str()))
    }
}

const USAGE: &str = "phonefarm v0.2 — 记录契约 v1 运行时
帮助:  help [命令...]                             (只打印用法，不查询设备)
身份:  runtime-info --json                        (本程序构建声明；纯宿主，不查询设备)
跑局:  run --task <T> [--serial S] [--endless] [--budget-calls N] [--max-steps N] [--app P] [--assert \"词1,词2\"] [--perceive ocr] \"<目标>\"
评测:  benchmark --task <T> [--rounds N] [--app P] [--assert ..] [--json] \"<目标>\"
并行:  parallel --job \"任务|目标|serial[|app[|assert]]\" [--job ...] [--budget-calls N] [--endless]
脚本:  script [--task T] [--serial S] [--app P] [--repeat N] [--settle-ms M] [--no-screen] [--detach] <脚本文件或局ID>
插件:  plugins                                   (列出已登记的专用场景插件)
CTS:   test-batch (--profile P.json | --module pkg/runner | --module oh:bundle/module/Runner | --dir APK目录)
       [--environment E.json] [--serial S] [--include 正则] [--exclude 正则] [--resume] [--retry N]
       [--timeout-ms N] [--idle-timeout-ms N] [--heal-script 路径] [--install-cmd 'pm install -r {apk}'] [--out 目录]
       [--detach 后台跑,轮询 <out>/summary.json]   (A2OH CTS 批量挂机执行器,Android/OH 双协议)
取数:  cts-fetch --remote <设备侧结果路径> [--serial S] [--out 目录] [--pattern 正则] [--max-mb N]
       (设备上已有 CTS/XTS 结果 → hdc file recv/adb pull 拉回 + 断言扫描 → assertions.json)
任务:  quest [--mode auto|dialogue|interact|navigate] [--sec N] [--serial S]  (原神场景插件的独立长跑Agent)
设备:  devices | keepalive [--status|--watch [秒]] [--serial S] [--json] | probe --serial <S> \"只读命令\" | exec --serial <S> \"命令\" --yes
       app-snapshot --serial S  (只读：前台与正在运行的第三方应用；读取失败记入 errors)
       fleet [--json] [--serial S] [--screenshot-dir 目录]
       (农场只读快照: 一趟 shell 把每台手机的在线/型号/电量/温度/风扇/亮屏/前台应用/
        保活策略/设备锁读齐, 采不到的字段留空并写明原因。截图默认关, 且别人持锁时不截)
解析:  parse-trace <trace.txt> [--full] [--comm 线程名] [--spf N]   (ftrace 文本 -> 帧时序/GPU 活跃/排队/带宽指标, 纯离线)
       attribute <summary.json>                          (一轮 summary -> 主因判定 + 正交带宽维, 纯离线)
       analyze <A臂glob> [B臂glob] [--metric M]           (离散度 + 漂移 + 精确置换检验 + 置换反演 CI)
       curtail-replay <A臂glob> <B臂glob> [--alpha A] [--metrics m1,m2]  (确定性截断离线回放: 截在哪、判定是否与跑满一致)
       frames-moving <raw1> <raw2>                        (两张 screencap 裸帧的平均逐像素差 %, 判画面动没动)
       crosscheck-report <轮目录>                         (两条采集通路并排对照表, 纯离线)
       refbench-report --root <runs目录>                  (refbench M0 六判据汇总, 规则哈希自证)
       report --baseline <glob> [--knob <glob>] [--snap-before F] [--snap-after F]
              [--replay-result F] [--primary M]          (五条判据汇总成可字节复现的 report.json)
载体:  vks-pick-comm <trace.txt> | vks-ready <run.log> | vks-crosscheck <run.log> <summary.json> [window.json]
       (Vulkan-Samples 的三道闸: 挑提交线程 / 判渲染就绪 / 内核侧与应用侧对账, 全部纯离线)
       vks-report --root <A/B目录> --sample S --config-a N --config-b N   (两臂判读面, 判读哈希自证)
算子:  gpu-op --request <eval_request.json> [--serial S] [--json] [--power-rail usb|battery]
       (Compute Shader 真机标尺: 等冷 + 锁频 + A/B/A/B + Welch t 检验 -> eval_report)
资格:  qualify --from evidence.json --json (纯离线资格评估；通过不等于优化成功)
       confirmation-observation --bundle B.json --out R.json (单轮原始记录验收，始终不授优化结论)
评测:  eval --request <eval_request.json> [--serial S] [--out 目录] [--json]
       eval recover --session DIR --serial S [--json]
       (game_opt_loop 评测唯一入口: v1 扁平转交 gpu-op; v2 信封按 kind 路由
        shader=降级转交 / sysparam=白名单校验+设备端旋钮+精确置换 / gray=knobs 层调用)
接入:  onboard --package <包名> [--serial S] [--out 目录] [--rounds 5] [--seconds 30] [--enter-only]
              [--no-cold] [--no-guest] [--no-consent] [--no-charge-suspend] [--vlm-calls N]
       (新游戏零人工接入: 冷启动 -> OCR 认画面只做白名单点击 -> 进游戏探针 -> 生成转视角负载
        -> 渲染线程 -> N 轮实测可重复 -> profile.json; --enter-only 只把游戏带进游戏场景)
闭环:  autoloop --out <证据目录> [--generations N] [--children N] [--pairs N]
              [--no-llm] [--probe-only] [--baseline-runs N] [--power-in-verdict auto|on|off]
       (系统参数全自动闭环: 白名单探测 -> 冻结规则 -> 大模型/本地变异提名 -> ABBA 真机评测 -> 报告)
自检:  selftest calibrate|run|quality|report --out <证据目录> [--cases aa*4,better:1,worse:2] [--pairs N]
       (判定链已知答案自检: refbench 可控负载 → 现有 compare+decide / eval 画质地板 → 混淆矩阵 + MDE 表)
游戏资源: game-deploy capabilities --json
       game-deploy --request FILE --serial S --out DIR [--device-binding JSON] --json
       game-deploy recover --session SOURCE --serial S --out RECOVERY_OUT --json
灰档:  gray-layer status [--serial S] | off [pkg] [--serial S] [--json]
       gray-layer session --mode MODE --package PKG --serial S --out DIR [--max-seconds 900] [--power-policy observe|battery] [--actor-id agent] [--so 层.so] [--keep-prop]
       gray-layer session-status --out DIR [--request-id ID] --json
       gray-layer session-action --out DIR --request REQUEST.json|- --json
       gray-layer session-recover --session DIR --serial S --json
       gray-layer refbench-loadop --serial S --out DIR [--frames 1800] [--intensity 8] [--timeout-seconds 150] [--so 层.so] [--evidence-origin device|synthetic] [--json]
       gray-layer refbench-recover --session DIR --serial S --json
       旧 probe/loadop/passdump/frameaudit/frameaudit-ts/target 挂层后退出入口已退役；须显式使用 session。
       旧 --keep-prop 仅安装不启动流程不自动迁移；session 始终启动目标，--keep-prop 仅控制 marker 等待/收窄。
       (Vulkan 层挂/摘/查; copyprobe/upop 当前不支持执行，包括自定义 SO；历史证据可离线分析)
       frame-audit capture --out <目录> [--ts] [--captures N] [--gap 秒] [--so 层.so]   (只读层抓原神一帧步骤清单)
       frame-audit recover --session <会话目录> --serial S [--json]   (核验并恢复本次灰层抓帧范围)
       frame-audit analyze <knobs_frameaudit.json>... [--md F] [--json F]           (离线: 步骤总览 + 白存/白读/白清/白画)
工件:  gray-artifact {build (--source sampled.comp | --adapted-artifact DIR) --out DIR [--ndk DIR] [--timeout-seconds 1..120] [--process-supervision own-helper-groups|inherited-group] | inspect --spirv FILE | verify --artifact DIR | adapt-storage --source original.comp --spirv original.spv --out DIR | verify-adaptation --artifact DIR} [--json]
       (离线编译/ABI/摘要门禁; deployable=false, 不构成真机部署资格)
       gray-ab [--pairs N] [--mode loadop] [--serial S] --out <目录>
       gray-ab recover --session DIR --serial S [--json]
       (loadop 支持 --json / --evidence-origin device|synthetic；完整会话停充与恢复，copyprobe 当前不支持执行)
       (灰档 A/B 采集编排: 两臂都挂层只切改写属性, 每臂重启原神进大世界)
画质:  sequence-quality --reference R.json --candidate C.json --rules frozen.json [--out tasks/quality/report.json]
       (配对连续帧 RGB PSNR/色偏/残差变化, 零 Token 离线; PASS 只覆盖冻结指标, 不证明无鬼影)
标尺:  bench --serial <S> --model <PATH.tflite> [--runs 3] [--json] [--limit-ms 4.0] [--metric gpu|invoke] [--gpu-level N] [--no-lock] [--out 目录]
       (端侧 TFLite 模型真机延迟标尺: 锁频+等冷+GPU Delegate 算子日志解析, SPEC_SR_LOOP Gate 0)
性能:  perf [--serial S] [--app 包名] [--rounds 10] [--source sysfs|smartperf|sflatency] [--layer 图层] [--power-rail usb|battery] [--xpower-out 目录] [--json]
       perf --from-csv <本地 data.csv> [--app 包名] [--json]   (离线解析已拉回的 SP_daemon 产物,不碰设备; --app 声明它是 -PKG 采的)
       frame-sources [--serial S] [--app 包名] [--json]
       (这台设备有哪些帧率/帧时来源: kgsl_ftrace / sf_latency / gfxinfo_hwui / sp_daemon_pkg / rs_fpscount ...,
        各自口径 app / app_ui / global、能否进判定表; 只读探测, 退出码 0=有能进判定表的来源)
       (归一化性能快照: 三条通路同一份字段, 采不到的字段是 null 并附原因, 不填 0。
        安卓 --source sysfs(默认): 直读 /sys/class/power_supply 电源轨, 只出功耗;
        安卓 --source smartperf: HiSmartPerf 通路, 往设备推 GamePerfToolCollector 常驻 +
          adb forward socket 实时流, 出帧率/功耗/温度(要 --app 包名; 有副作用, 故不默认);
        安卓 --source sflatency: 无 root 的显示帧, 轮询 dumpsys SurfaceFlinger --latency <目标图层>,
          只出帧 (frame_judge 标 sf_latency); 要 --app 包名, 图层不给就按包名挑;
        鸿蒙(--serial hdc:<key>): HiSmartPerf 的 SP_daemon 落盘 CSV; 带 --app 走 -PKG 应用口径
          (frame_judge 标 sp_daemon_pkg), 不带是整机口径, fps 记缺失, meta.fps_scope 标口径。
        退出码 0=采到/1=没采到/2=通路不可用。--xpower-out 才刷 Xpower 落盘拉回 dubai.db)
采集:  capture --serial <S> [--out 目录] [--frames 200] [--max-steps N] [--settle-ms 800] [--mode auto] [--no-shutdown] [--json]
       (原神无 UI 自动巡航原始帧采集: 只留大世界探索态帧 + manifest 路线分段, SPEC_SR_LOOP Gate 2)
后台:  run/benchmark/script 加 --detach 立即回报局ID后台跑;phonefarm status [<局ID>|--task T] 查 运行中/已结束/中断
查看:  last | runs [--task T] | show <局ID> [--step N|--raw|--hooks|--events|--crashes|--anr|--trace]
       cat <路径> [--head/--tail N] [--grep 词] | stats <局ID> | tasks | tree | lessons | campaign
       hyp | pred | caps [--adopt/--rollback id] | tools [--propose def.json|--retire id]
       experiment <spec.toml> [--arm A|B|C] [--ablate no-active-testing|no-cap-screening] [--resume|--report-only] [--json]  (A/B/C 对比实验,SPEC_EVOLUTION §7)
       export [--task T] --split train|heldout --out <文件> [--redact-config toml]  (训练数据导出)
       schema [--type r类型] | config [--key k]     (查看类全部支持 --json,只读盘不烧token)
服务:  serve [--root 目录]                        (MCP stdio 工具服务,供 octos 等客户端挂载)
设备锁: 碰设备的子命令 (run/onboard/benchmark/parallel/script/bench/gray-ab/gray-layer/autoloop/eval/gpu-op/perf/
       capture/test-batch/cts-fetch/quest/probe/exec/experiment/selftest/frame-sources) 自己拿锁、每 60s 心跳、跑完释放 (tools/devlock)。
       通用参数 [--lock-label 名字 (缺省 $DEVLOCK_LABEL, 再缺省 phonefarm)] [--lock-wait 2h]（缺省一直等到拿到或取消）
       锁已在同一 label 手里 (外层 devlock run / optimize) 时原地执行; 没拿到锁不碰设备, 退出 2";

/// 任务数据根(tasks 目录)解析: PF_TASKS_ROOT 环境变量优先(实验臂隔离/单测注入,
/// 语义与 cli.rs data_root() 一致——指向 tasks 根本身);否则 <data_dir>/tasks。
pub(crate) fn tasks_root(data_dir: &str) -> String {
    if let Ok(o) = std::env::var("PF_TASKS_ROOT") {
        let o = o.trim().trim_end_matches('/');
        if !o.is_empty() {
            return o.to_string();
        }
    }
    format!("{}/tasks", data_dir.trim_end_matches('/'))
}

/// secrets.env 解析(Improve Spec): 只认 `export KEY="v"` / `KEY=v` 形态的行,
/// 等价 source 语义但绝不执行任何命令。纯函数供单测。
fn parse_secrets(text: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for line in text.lines() {
        let l = line.trim();
        if l.is_empty() || l.starts_with('#') { continue; }
        let l = l.strip_prefix("export ").unwrap_or(l).trim();
        let Some((k, v)) = l.split_once('=') else { continue };
        let k = k.trim();
        if k.is_empty() || !k.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') { continue; }
        let v = v.trim().trim_matches('"').trim_matches('\'');
        if v.contains('$') || v.contains('`') { continue; } // 不做任何展开/执行
        out.push((k.to_string(), v.to_string()));
    }
    out
}

/// key 自举: provider 链的 key_env 有缺 → 自动读 ./secrets.env(仅本地);仍全缺 → 警告+格式说明+退出。
/// 用户已 export 的值最高,不覆盖。部分缺失只提示不拦(存活 provider 可接力)。
fn ensure_keys(cfg: &Config) {
    let mut envs: Vec<&str> = Vec::new();
    for p in &cfg.providers {
        if !envs.contains(&p.key_env.as_str()) { envs.push(p.key_env.as_str()); }
    }
    let missing = |es: &[&str]| -> Vec<String> {
        es.iter().filter(|e| std::env::var(e).map(|v| v.trim().is_empty()).unwrap_or(true))
            .map(|e| e.to_string()).collect()
    };
    let mut miss = missing(&envs);
    if !miss.is_empty() {
        if let Ok(text) = std::fs::read_to_string("secrets.env") {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                if let Ok(m) = std::fs::metadata("secrets.env") {
                    if m.permissions().mode() & 0o037 != 0 {
                        println!("(提示: secrets.env 权限过宽,建议 chmod 600 secrets.env)");
                    }
                }
            }
            for (k, v) in parse_secrets(&text) {
                if miss.contains(&k) {
                    std::env::set_var(&k, &v);
                    println!("(secrets: 从 ./secrets.env 读到 {k})");
                }
            }
            miss = missing(&envs);
        }
    }
    if miss.len() == envs.len() {
        eprintln!("⚠ 未配置模型 key(缺: {})。", miss.join(", "));
        eprintln!("请在仓库根创建 secrets.env(格式见 secrets.env.example):");
        eprintln!("    export GLM_KEY=\"你的智谱key(Coding套餐)\"");
        eprintln!("或先 export {}=... 再重跑。", miss.first().map(String::as_str).unwrap_or("GLM_KEY"));
        std::process::exit(2);
    } else if !miss.is_empty() {
        println!("(提示: {} 未配置,对应 provider 将失效,链上其余接力)", miss.join(", "));
    }
}

/// detach 参数剥离(纯函数供单测): 子进程用同参重入,仅去掉 --detach 本身
fn strip_detach(args: &[String]) -> Vec<String> {
    args.iter().filter(|a| a.as_str() != "--detach").cloned().collect()
}

/// 后台分离自进程: 同参重入(去 --detach),stdout/stderr 归 console 文件,
/// 新进程组免受调用方作业信号牵连(nohup 收编)。返回子进程 pid。
fn spawn_detached(console: &str, envs: &[(&str, &str)]) -> std::io::Result<u32> {
    let args = strip_detach(&std::env::args().skip(1).collect::<Vec<_>>());
    spawn_detached_with(&args, console, envs)
}

/// 同上,但子进程参数由调用方显式给出(test-batch --detach 需注入 --out)
fn spawn_detached_with(args: &[String], console: &str, envs: &[(&str, &str)]) -> std::io::Result<u32> {
    let exe = std::env::current_exe()?;
    let f = std::fs::File::create(console)?;
    let f2 = f.try_clone()?;
    let mut cmd = std::process::Command::new(exe);
    cmd.args(args)
        .stdout(f).stderr(f2).stdin(std::process::Stdio::null());
    for (k, v) in envs {
        cmd.env(k, v);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    Ok(cmd.spawn()?.id())
}

#[cfg(test)]
fn isolate_fd_test(name: &str) -> bool {
    if std::env::var("PF_FD_UNIT_TEST").as_deref() == Ok(name) { return false; }
    // A fork in another libtest thread duplicates even CLOEXEC descriptors until
    // exec. Immediate last-close assertions need a process with only this test.
    let output = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact", name]).env("PF_FD_UNIT_TEST", name).output().unwrap();
    assert!(output.status.success(), "{}\n{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    true
}

fn main() {
    if let Some(code) = fd_lock::transport_entry() { std::process::exit(code); }
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    // A document read, including for commands whose old parser ignores --help.
    if args.first().is_some_and(|arg| arg == "help") {
        let usage = match args.get(1).map(String::as_str) {
            Some("gray-layer") => graylayer::USAGE,
            Some("gray-ab") => grayab::USAGE,
            Some("eval") => eval::USAGE,
            Some("game-deploy") => game_deploy::USAGE,
            _ => USAGE,
        };
        println!("{usage}");
        return;
    }
    runtime_identity::report_startup();
    // 碰设备的子命令先过设备锁 (src/devlock.rs): 没拿到锁不碰设备
    if let Some(sub) = args.first().cloned() {
        let gray = matches!(sub.as_str(), "gray-layer" | "gray-ab");
        let deployment = sub == "game-deploy";
        if gray {
            match graylayer::cli_admission(&sub, &args[1..]) {
                Ok(graylayer::GrayCliAdmission::Help) => {
                    // Admission has already identified true help (not an option value). Route
                    // through the same focused usage as each executor, including lock-flag help.
                    let help = ["--help".to_string()];
                    let code = if sub == "gray-layer" {
                        graylayer::run_gray_layer(&help)
                    } else {
                        grayab::run_gray_ab(&help)
                    };
                    std::process::exit(code);
                }
                Ok(graylayer::GrayCliAdmission::Reject(rejection)) => {
                    println!("{rejection}");
                    std::process::exit(2);
                }
                Err(error) => { eprintln!("{error}"); std::process::exit(2); }
                Ok(graylayer::GrayCliAdmission::Proceed) => {},
            }
        }
        // True help was handled above. A gray option's value named --help must
        // not trigger the legacy gate's token-based help exemption.
        let queue_required = if deployment {
            match game_deploy::needs_queue(&args[1..]) {
                Ok(required) => required,
                Err(error) => { println!("{}", game_deploy::error_value(&error)); std::process::exit(2); }
            }
        } else if gray {
            // Use the same value-aware stripping as admission. Host-only
            // clients must work while their persistent owner holds the lease;
            // recovery enters its retained context directly.
            graylayer::needs_queue(&sub, &args[1..])
        } else {
            devlock::needs_lock(&sub, &args[1..])
        };
        if queue_required {
            if let devlock::Gate::Exit(code) = devlock::gate(&mut args) {
                std::process::exit(code);
            }
        } else if gray || deployment {
            // Admission already checked these flags; keep execution's argument
            // view identical without invoking any lock process.
            if let Err(error) = devlock::strip_lock_args(&mut args) {
                eprintln!("{error}");
                std::process::exit(2);
            }
        }
    }
    match args.first().map(|s| s.as_str()) {
        Some("__lease-handoff") => std::process::exit(fd_lock::handoff(&args[1..])),
        Some("runtime-info") => {
            if args.len() > 2 || args.get(1).is_some_and(|arg| arg != "--json") {
                eprintln!("usage: phonefarm runtime-info --json");
                std::process::exit(2);
            }
            println!("{}", runtime_identity::identity());
        }
        Some("__capture-worker") => {
            std::process::exit(capture::run_worker(&args[1..]));
        }
        Some("__observe-worker") => {
            std::process::exit(observation::run_worker(&args[1..]));
        }
        Some("__lease-transport-worker") => {
            std::process::exit(lease_command::transport_worker(&args[1..]));
        }
        Some("remote-control") => {
            std::process::exit(remote_control::run(&args[1..]));
        }
        Some("lease") => {
            std::process::exit(devlease::run_lease(&args[1..]));
        }
        Some("serve") => {
            // MCP stdio 工具服务(SPEC_MCP_SERVE): octos 等客户端的外部工具契约
            std::process::exit(serve::run_serve(&args[1..]));
        }
        Some("parallel") => {
            // 多设备并行(PARALLEL_SPEC): 自进程 fan-out,行级设备前缀,任一失败整体非0
            std::process::exit(parallel::run_parallel(&args[1..]));
        }
        Some("plugins") => {
            // 专用场景插件清单。核心不认识具体应用,这里列的全部来自 plugins 层登记。
            let names = plugins::builtin_names();
            println!("已登记场景插件 {} 个:", names.len());
            for n in names {
                println!("  {n}");
            }
        }
        Some("fleet") => {
            // 农场只读快照: 一趟把每台手机的状态读齐 (供上层面板消费)
            std::process::exit(fleet::run_fleet(&args[1..]));
        }
        Some("preflight") => {
            // 开测前的环境不变量核查 (风扇常关/静音/温度; --fix 自动修能修的)
            std::process::exit(preflight::run_preflight(&args[1..]));
        }
        Some("devices") => {
            // 两族并列,各自 best-effort(某族工具不在 PATH 就跳过):
            // hdc 目标直接以 "hdc:<key>" 形态给出,拷进 --serial 即用
            if let Some(adb) = device::locate_adb() {
                if let Ok(out) = crate::fd_lock::transport_command(&adb).arg("devices").output() {
                    print!("{}", String::from_utf8_lossy(&out.stdout));
                }
            } else {
                eprintln!("(未找到 adb,仅列 hdc;ADB_BIN=/path/to/adb 可指定)");
            }
            if let Ok(out) = crate::fd_lock::transport_command("hdc").args(["list", "targets"]).output() {
                for l in String::from_utf8_lossy(&out.stdout).lines() {
                    let l = l.trim();
                    if !l.is_empty() && l != "[Empty]" {
                        println!("hdc:{l}");
                    }
                }
            }
            // iOS: 经 WebDriverAgent, serial 形态 "ios:<udid>"。
            // 这里只列 USB 上看得见的设备(libimobiledevice 的 idevice_id);
            // 能不能用还取决于手机上 WDA 是否已装并启动 —— 枚举不替这条说话。
            match std::process::Command::new("idevice_id").arg("-l").output() {
                Ok(out) => {
                    for l in String::from_utf8_lossy(&out.stdout).lines() {
                        let l = l.trim();
                        if !l.is_empty() {
                            println!("ios:{l}");
                        }
                    }
                }
                Err(_) => eprintln!("(未找到 idevice_id, 不列 iOS; 装 libimobiledevice 或设 WDA_URL 直连已有隧道)"),
            }
        }
        Some("run") => {
            let mut serial: Option<String> = None;
            let mut goal = String::new();
            let mut task = String::new();
            let mut endless = false;
            let mut budget: u32 = 40;
            // 步数上限的按局覆盖: 需要多步确定性输入的任务(如逐位敲键盘)
            // 步数下限远高于常规导航类任务, 不该被全局默认值一刀切。
            let mut max_steps_override: Option<u32> = None;
            let mut app: Option<String> = None;
            let mut asserts: Vec<String> = Vec::new();
            let mut detach = false;
            let mut freeze_on_done = false;
            let mut resume_prefix: Option<String> = None;
            let mut it = args[1..].iter();
            while let Some(a) = it.next() {
                match a.as_str() {
                    "--serial" => serial = it.next().cloned(),
                    "--task" => task = it.next().cloned().unwrap_or_default(),
                    "--endless" => endless = true,
                    "--detach" => detach = true,
                    "--freeze-on-done" => freeze_on_done = true,
                    "--resume" => resume_prefix = it.next().cloned(),
                    "--budget-calls" => budget = it.next().and_then(|v| v.parse().ok()).unwrap_or(40),
                    "--max-steps" => max_steps_override = it.next().and_then(|v| v.parse().ok()),
                    "--app" => app = it.next().cloned(),
                    "--perceive" => { if let Some(v) = it.next() { if v.eq_ignore_ascii_case("ocr") { std::env::set_var("PF_PERCEIVE", "ocr"); } } }
                    "--assert" => {
                        // 验收词(可逗号分隔多个,英文/中文逗号都认): 契约式到达断言
                        if let Some(v) = it.next() {
                            for w in v.split(|c| c == ',' || c == '、') {
                                let w = w.trim();
                                if !w.is_empty() { asserts.push(w.to_string()); }
                            }
                        }
                    }
                    _ => goal = a.clone(),
                }
            }
            if task.is_empty() || (goal.is_empty() && resume_prefix.is_none()) {
                eprintln!("用法: phonefarm run --task <任务名> [--serial <设备>] [--endless] [--budget-calls N] [--max-steps N] [--app <包名>] [--assert \"词1,词2\"] [--freeze-on-done] [--perceive ocr] [--resume <局ID前缀>] \"<目标>\"");
                eprintln!("      --resume: 恢复中断局(可省略目标,继承旧局goal;账末动作状态不明时先只读核对)");
                std::process::exit(2);
            }
            let cfg_text = match std::fs::read_to_string("phonefarm.toml") {
                Ok(s) => s,
                Err(e) => { eprintln!("读不到 phonefarm.toml: {e}"); std::process::exit(2); }
            };
            let mut cfg: Config = match toml::from_str(&cfg_text) {
                Ok(c) => c,
                Err(e) => { eprintln!("phonefarm.toml 解析失败: {e}"); std::process::exit(2); }
            };
            if let Some(ms) = max_steps_override {
                if ms == 0 {
                    eprintln!("--max-steps 需为正整数");
                    std::process::exit(2);
                }
                cfg.max_steps = ms;
            }
            if !cfg.prompts.contains_key("step") {
                eprintln!("phonefarm.toml 缺 [prompts].step");
                std::process::exit(2);
            }
            if detach {
                // 先起跑回头取结果: 预分配局ID→建目录→分离子进程→立即回报(取结果走 status/show)
                let id = runtime::alloc_run_id();
                let run_dir = format!("{}/{}/runs/{}", tasks_root(&cfg.data_dir), task, id);
                if std::fs::create_dir_all(&run_dir).is_err() {
                    eprintln!("✗ 建不了运行目录 {run_dir}");
                    std::process::exit(2);
                }
                let console = format!("{run_dir}/console.log");
                match spawn_detached(&console, &[("PF_RUN_ID", &id)]) {
                    Ok(pid) => {
                        println!("已后台起跑: run={id}\n目录: {run_dir}\n控制台: {console}\npid: {pid}\n取结果: phonefarm status {id} | show {id}");
                        std::process::exit(0);
                    }
                    Err(e) => { eprintln!("detach 失败: {e}"); std::process::exit(2); }
                }
            }
            ensure_keys(&cfg);
            // 中断恢复: 解析旧局账本(可继承 goal);局ID前缀无匹配直接拒绝开跑
            let resume = match resume_prefix.as_deref() {
                Some(rp) => {
                    let task_dir = format!("{}/{}", tasks_root(&cfg.data_dir), task);
                    match runtime::load_resume(&task_dir, rp) {
                        Some(r) => Some(r),
                        None => { eprintln!("--resume: 局ID前缀 {rp} 在任务 {task} 下无匹配局"); std::process::exit(2); }
                    }
                }
                None => None,
            };
            if goal.is_empty() {
                if let Some(r) = &resume { goal = r.goal.clone(); }
            }
            let res = runtime::episode(&cfg, &task, &goal, serial, None, endless, budget, app, asserts, freeze_on_done, resume);
            println!("summary: run={} stop={} steps={} calls={} tokens={} wall={:.1}s achieved={}",
                res.run_id, res.stop, res.steps, res.calls, res.tokens,
                res.wall_ms as f64 / 1000.0, res.achieved);
            std::process::exit(if res.achieved { 0 } else { 1 });
        }
        Some("benchmark") => {
            // 自闭环评测: 每轮 体检→复活→轮间清理→跑局→原生指标入 campaign.tsv;--json 出结构化报告
            let mut serial: Option<String> = None;
            let mut goal = String::new();
            let mut task = String::new();
            let mut rounds: u32 = 1;
            let mut budget: u32 = 40;
            let mut app: Option<String> = None;
            let mut asserts: Vec<String> = Vec::new();
            let mut as_json = false;
            let mut detach = false;
            let mut it = args[1..].iter();
            while let Some(a) = it.next() {
                match a.as_str() {
                    "--serial" => serial = it.next().cloned(),
                    "--task" => task = it.next().cloned().unwrap_or_default(),
                    "--detach" => detach = true,
                    "--rounds" => rounds = it.next().and_then(|v| v.parse().ok()).unwrap_or(1),
                    "--budget-calls" => budget = it.next().and_then(|v| v.parse().ok()).unwrap_or(40),
                    "--app" => app = it.next().cloned(),
                    "--perceive" => { if let Some(v) = it.next() { if v.eq_ignore_ascii_case("ocr") { std::env::set_var("PF_PERCEIVE", "ocr"); } } }
                    "--assert" => {
                        if let Some(v) = it.next() {
                            for w in v.split(|c| c == ',' || c == '、') {
                                let w = w.trim();
                                if !w.is_empty() { asserts.push(w.to_string()); }
                            }
                        }
                    }
                    "--json" => as_json = true,
                    _ => goal = a.clone(),
                }
            }
            if goal.is_empty() || task.is_empty() {
                eprintln!("用法: phonefarm benchmark --task <任务名> [--rounds N] [--budget-calls N] [--app <包名>] [--assert \"词1,词2\"] [--json] \"<目标>\"");
                std::process::exit(2);
            }
            let cfg_text = match std::fs::read_to_string("phonefarm.toml") {
                Ok(s) => s,
                Err(e) => { eprintln!("读不到 phonefarm.toml: {e}"); std::process::exit(2); }
            };
            let cfg: Config = match toml::from_str(&cfg_text) {
                Ok(c) => c,
                Err(e) => { eprintln!("phonefarm.toml 解析失败: {e}"); std::process::exit(2); }
            };
            if !cfg.prompts.contains_key("step") {
                eprintln!("phonefarm.toml 缺 [prompts].step");
                std::process::exit(2);
            }
            let task_root_early = format!("{}/tasks/{}", cfg.data_dir.trim_end_matches('/'), task);
            if detach {
                // 多轮评测后台化: console 沿用 campaign_<stamp>.out 命名惯例;进度用 runs/campaign/status --task 轮询
                let _ = std::fs::create_dir_all(&task_root_early);
                let stamp = runtime::alloc_run_id();
                let console = format!("{task_root_early}/campaign_{stamp}.out");
                match spawn_detached(&console, &[]) {
                    Ok(pid) => {
                        println!("已后台起跑 benchmark: 任务[{task}] {rounds}轮\n控制台: {console}\npid: {pid}\n进度: phonefarm status --task {task} | runs --task {task} | campaign --task {task}");
                        std::process::exit(0);
                    }
                    Err(e) => { eprintln!("detach 失败: {e}"); std::process::exit(2); }
                }
            }
            ensure_keys(&cfg);
            let task_root = task_root_early;
            let _ = std::fs::create_dir_all(&task_root);
            let tsv = format!("{task_root}/campaign.tsv");
            if std::fs::metadata(&tsv).is_err() {
                let _ = std::fs::write(&tsv,
                    "round\trun_id\texit\tachieved\tstop\tsteps\tcalls\ttokens\twall_s\n");
            }
            let append = |line: &str| {
                use std::io::Write;
                if let Ok(mut f) = std::fs::OpenOptions::new().append(true).open(&tsv) {
                    let _ = writeln!(f, "{line}");
                }
            };
            let tmp = std::env::temp_dir().join("phonefarm-bench").to_string_lossy().to_string();
            let _ = std::fs::create_dir_all(&tmp);
            let phone = device::Device::new(serial.clone(), tmp);
            let apps = phone.launchable_apps();
            let mut rows: Vec<serde_json::Value> = Vec::new();
            for r in 1..=rounds {
                println!("══ benchmark 第{r}/{rounds}轮 ══");
                // 体检,不行就地复活(原round.sh职责,已收编)
                if !phone.health_check(15000) {
                    phone.revive(&cfg.emulator_cmd);
                    if !phone.health_check(15000) {
                        eprintln!("✗ 设备无响应且复活失败,第{r}轮记EMULATOR_DEAD");
                        append(&format!("{r}\t-\t9\tfalse\tEMULATOR_DEAD\t0\t0\t0\t0"));
                        rows.push(serde_json::json!({"round": r, "exit": 9, "achieved": false, "stop": "EMULATOR_DEAD"}));
                        continue;
                    }
                }
                // 轮间清理: 强停→回桌面→冷启主Activity,统一起跑线
                let mut cold_ms: Option<i64> = None;
                if let Some(pkg) = &app {
                    phone.force_stop(pkg);
                    phone.home();
                    std::thread::sleep(std::time::Duration::from_secs(3));
                    if let Some((_, comp)) = apps.iter().find(|(p, _)| p == pkg) {
                        // 这次启动就是本轮的冷启动: 计时递给局内遥测(orient 不会再触发)
                        cold_ms = phone.launch(pkg, comp);
                    }
                    std::thread::sleep(std::time::Duration::from_secs(6));
                }
                let t0 = std::time::Instant::now();
                let res = runtime::episode(&cfg, &task, &goal, serial.clone(), cold_ms, true, budget, app.clone(), asserts.clone(), false, None);
                let wall = t0.elapsed().as_secs();
                append(&format!(
                    "{r}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{wall}",
                    res.run_id, if res.achieved { 0 } else { 1 }, res.achieved, res.stop,
                    res.steps, res.calls, res.tokens));
                rows.push(serde_json::json!({
                    "round": r, "run_id": res.run_id, "exit": if res.achieved { 0 } else { 1 },
                    "achieved": res.achieved, "stop": res.stop, "steps": res.steps,
                    "calls": res.calls, "tokens": res.tokens, "wall_s": wall
                }));
            }
            if as_json {
                println!("{}", serde_json::to_string_pretty(&rows).unwrap_or_default());
            }
            let last_ok = rows.last().and_then(|r| r["achieved"].as_bool()).unwrap_or(false);
            std::process::exit(if last_ok { 0 } else { 1 });
        }
        Some("script") => {
            // 脚本与回放模式 (Script & Replay Mode): 确定性执行离线脚本或历史对局轨迹，跳过模型决策，零Token消耗
            let mut serial: Option<String> = None;
            let mut script_source = String::new();
            let mut task: Option<String> = None;
            let mut app: Option<String> = None;
            let mut repeat: u32 = 1;
            let mut settle_ms: u64 = 500;
            let mut tele_interval: u32 = 1;
            let mut no_screen = false;
            let mut detach = false;
            let mut it = args[1..].iter();
            while let Some(a) = it.next() {
                match a.as_str() {
                    "--serial" => serial = it.next().cloned(),
                    "--task" => task = it.next().cloned(),
                    "--app" => app = it.next().cloned(),
                    "--repeat" => repeat = it.next().and_then(|v| v.parse().ok()).unwrap_or(1),
                    "--settle-ms" => settle_ms = it.next().and_then(|v| v.parse().ok()).unwrap_or(500),
                    "--tele-interval" => tele_interval = it.next().and_then(|v| v.parse().ok()).unwrap_or(1),
                    "--no-screen" => no_screen = true,
                    "--detach" => detach = true,
                    _ => script_source = a.clone(),
                }
            }
            if script_source.is_empty() {
                eprintln!("用法: phonefarm script [--task <任务名>] [--serial <设备>] [--app <包名>] [--repeat N] [--settle-ms M] [--tele-interval K] [--no-screen] [--detach] <脚本文件或局ID>");
                std::process::exit(2);
            }
            let cfg_data_dir = match std::fs::read_to_string("phonefarm.toml") {
                Ok(s) => toml::from_str::<Config>(&s).map(|c| c.data_dir).unwrap_or_else(|_| ".".into()),
                Err(_) => ".".into(),
            };
            let task_name = task.unwrap_or_else(|| {
                std::path::Path::new(&script_source)
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("script")
                    .to_string()
            });
            if detach {
                let id = runtime::alloc_run_id();
                let run_dir = format!("{}/tasks/{}/runs/{}", cfg_data_dir.trim_end_matches('/'), task_name, id);
                if std::fs::create_dir_all(&run_dir).is_err() {
                    eprintln!("✗ 建不了运行目录 {run_dir}");
                    std::process::exit(2);
                }
                let console = format!("{run_dir}/console.log");
                match spawn_detached(&console, &[("PF_RUN_ID", &id)]) {
                    Ok(pid) => {
                        println!("已后台起跑 script: run={id}\n目录: {run_dir}\n控制台: {console}\npid: {pid}\n取结果: phonefarm status {id} | show {id} | stats {id}");
                        std::process::exit(0);
                    }
                    Err(e) => { eprintln!("detach 失败: {e}"); std::process::exit(2); }
                }
            }
            let run_cfg = script::ScriptRunConfig {
                task: task_name,
                script_source,
                serial,
                app,
                repeat,
                settle_ms,
                tele_interval,
                no_screen,
                data_dir: cfg_data_dir,
            };
            match script::execute_script(&run_cfg) {
                Ok(_) => std::process::exit(0),
                Err(e) => {
                    eprintln!("脚本执行失败: {e}");
                    std::process::exit(1);
                }
            }
        }
        Some("keepalive") => {
            // 农场级设备保活巡检(SPEC_KEEPALIVE): 唤醒+解锁+不息屏, adb/hdc 两族并列
            std::process::exit(keepalive::run_keepalive(&args[1..]));
        }
        Some("bench") => {
            // 端侧模型物理延迟标尺(SPEC_SR_LOOP Gate 0): 锁频+等冷+GPU Delegate 真机秒筛, 纯增量子命令
            std::process::exit(bench::run_bench(&args[1..]));
        }
        Some("interp-judge") => {
            // 插帧验收: 插出的中间帧对原生 2 倍帧率真值, 静止块/运动块/最差块/包络/细节 + 序列闪烁
            std::process::exit(interpjudge::run_interp_judge(&args[1..]));
        }
        Some("iq-diagnose") => {
            // 画质降质诊断: 哪种降质 (模糊/振铃/锯齿/色偏/块效应/重影/光晕/闪烁)、在哪 (逐块热图 + 区域 + 最差块截图)
            std::process::exit(iqdiagnose::run_iq_diagnose(&args[1..]));
        }
        Some("iq-judge") => {
            // 画质验收: 候选与「原生自己跟自己比」同一水平才算画面不变 (稳定块 PSNR/SSIM + 细节能量)
            std::process::exit(iqjudge::run_iq_judge(&args[1..]));
        }
        Some("shot-diff") => {
            // copyprobe 验收 ②: 截帧逐像素比对 (控制对基线)
            std::process::exit(shotdiff::run_shot_diff(&args[1..]));
        }
        Some("sequence-quality") => {
            std::process::exit(sequence_quality::run_sequence_quality(&args[1..]));
        }
        Some("gray-ab") => {
            // 灰档 A/B 采集编排 (两臂都挂层只切属性; 每臂重启原神)
            std::process::exit(grayab::run_gray_ab(&args[1..]));
        }
        Some("frame-audit") => {
            // 一帧步骤清单: capture 真机抓 (只读层) / analyze 离线找多余步骤
            std::process::exit(frameaudit::run_frame_audit(&args[1..]));
        }
        Some("gray-artifact") => std::process::exit(grayartifact::run(&args[1..])),
        Some("game-deploy") => std::process::exit(game_deploy::run(&args[1..])),
        Some("gray-layer") => {
            // knobs/gray 灰档 Vulkan 层的挂/摘/查 (enable_layer.sh 的移植)
            std::process::exit(graylayer::run_gray_layer(&args[1..]));
        }
        Some("selftest") => {
            // 判定链的已知答案真机自检 (refbench): A/A 误判率、已知变好检出率与 MDE、
            // 已知变差必须 REJECT、画质作弊必须被画面判据拦下
            std::process::exit(selftest::run_selftest(&args[1..]));
        }
        Some("autoloop") => {
            // 系统参数全自动闭环 (probe -> 基线 -> 冻结规则 -> 提名 -> ABBA -> 判定 -> 报告)
            std::process::exit(autoloop::run_autoloop(&args[1..]));
        }
        Some("onboard") => {
            // 新游戏零人工接入: 进门 → 进游戏探针 → 生成负载 → 实测可重复 → 游戏档案
            std::process::exit(onboard::run_onboard(&args[1..]));
        }
        Some("qualify") => {
            std::process::exit(qualification::run_qualify(&args[1..]));
        }
        Some("confirmation-driver") => { std::process::exit(confirmation_driver::run_confirmation_driver(&args[1..])); }
        Some("confirmation-observation") => {
            std::process::exit(confirmation_observation::run_confirmation_observation(&args[1..]));
        }
        Some("eval") => {
            // game_opt_loop 评测请求的唯一入口 (v2 信封 shader/sysparam/gray; v1 扁平兼容)
            std::process::exit(eval::run_eval(&args[1..]));
        }
        Some("whitebox-refbench-recover") => std::process::exit(whitebox_refbench::recover(&args[1..])),
        Some("gpu-op") => {
            // Compute Shader 算子的真机标尺与 A/B 裁决: 等冷 + 锁频 + A/B/A/B 交替 +
            // Welch t 检验, 契约对齐 game_opt_loop/contracts。纯增量子命令。
            std::process::exit(gpuop::run_gpu_op(&args[1..]));
        }
        Some("parse-trace") => {
            // ftrace 文本 → 帧时序 + GPU 归因指标。纯离线, 不碰设备, 同一份 trace 逐字节可复现。
            std::process::exit(looptrace::run_parse_trace(&args[1..]));
        }
        Some("vks-report") => {
            // Vulkan-Samples 两臂对照的判读面 (与归档 report.txt 逐字节可复现)
            std::process::exit(vksreport::run_vks_report(&args[1..]));
        }
        Some("vks-pick-comm") => {
            // 从 ftrace 里数出发 GPU 提交的线程名 (Vulkan-Samples 的线程名不是稳定契约)
            std::process::exit(vks::run_pick_comm(&args[1..]));
        }
        Some("vks-ready") => {
            // 样例是否已进入真实渲染稳态 (看帧率序列的阶跃, 不看秒数)。0=就绪 1=还没
            std::process::exit(vks::run_ready(&args[1..]));
        }
        Some("vks-crosscheck") => {
            // 内核侧提交速率与应用侧自报帧率的对账
            std::process::exit(vks::run_crosscheck(&args[1..]));
        }
        Some("refbench-report") => {
            // refbench M0 六条判据汇总 (与归档 report.json 逐字节可复现)
            std::process::exit(refbenchreport::run_refbench_report(&args[1..]));
        }
        Some("crosscheck-report") => {
            // 同一窗口两条采集通路的产物并排成一张表。纯离线, 只读目录里已有的产物。
            std::process::exit(crosscheck::run_crosscheck_report(&args[1..]));
        }
        Some("frames-moving") => {
            // 两张 adb screencap 裸帧的平均逐像素差 (%)。纯离线, 确认"画面真的在动"。
            std::process::exit(framecheck::run_frames_moving(&args[1..]));
        }
        Some("curtail-replay") => {
            // 确定性截断的离线回放: 归档两臂逐轮数据按 ABBA 到达顺序重演, 核对截断不改判定。纯离线。
            std::process::exit(curtail::run_replay(&args[1..]));
        }
        Some("analyze") => {
            // 多轮 summary.json → 离散度 (判据 1) 与两臂对比 (判据 3)。纯离线, 无随机。
            std::process::exit(loopstat::run_analyze(&args[1..]));
        }
        Some("report") => {
            // 五条判据汇总成一份可字节复现的 report.json。纯离线。
            std::process::exit(loopreport::run_report(&args[1..]));
        }
        Some("loopstat") => {
            // 迁移期过渡通道: stdin 收一个 JSON 请求, stdout 出一个 JSON 结果。
            // 尚未搬迁的 Python 靠它复用同一份统计口径, 搬完即删。
            std::process::exit(loopstat::run_loopstat(&args[1..]));
        }
        Some("attribute") => {
            // 一轮 summary.json → "这一帧的时间花在哪类开销上"。纯离线。
            std::process::exit(looptrace::run_attribute(&args[1..]));
        }
        Some("perf") => {
            // 归一化性能快照: 安卓走 sysfs 电源轨, 鸿蒙走 HiSmartPerf(SP_daemon + Xpower),
            // 上层拿到的 JSON 字段完全一致。零 Token, 只读设备。纯增量子命令。
            std::process::exit(perfsrc::run_perf(&args[1..]));
        }
        Some("frame-sources") => {
            // 这台设备上有哪些帧率/帧时来源、各自什么口径 (app / app_ui / global)、能否进判定表。只读探测。
            std::process::exit(framesrc::run(&args[1..]));
        }
        Some("capture") => {
            // 原神无 UI 自动巡航截图(SPEC_SR_LOOP Gate 2): 复用 genshin 插件生命周期与单步, 只抓原始帧, 纯增量子命令
            std::process::exit(capture::run_capture(&args[1..]));
        }
        Some("collect") => {
            // 有界滚动采集(SPEC_OBSERVATION_BUDGET 原语 1): 组合动作, 零 Token。
            // 语义移自 iPhone-use 的 pua_scroll_find / pua_collect_list。
            std::process::exit(collect::run_collect(&args[1..]));
        }
        Some("decide") => {
            // 决策原语(typed decisions: choice/score/noul), 零 Token。
            // 本地那档是降级占位不是模型; 侧车那一档是把 Core ML 接进来的地方。
            std::process::exit(decision::run_decide(&args[1..]));
        }
        Some("ios-probe") => {
            // iOS 后端真机体检: 逐项探 WDA 端点, 并核实界面树形状与解析器的假设对不对。
            // 存在理由: 这个后端是照文档形状写的, 没过真机 —— 给人一条命令去核, 而不是等人想起来。
            std::process::exit(ios::run_ios_probe(&args[1..]));
        }
        Some("screen") => {
            // 设备屏幕观战窗口(SPEC_SCREEN_WIDGET): 零 Token 只读。
            // 每帧短时 ownership，不能让看屏干扰已占有设备的测量。
            std::process::exit(screen::run_screen(&args[1..]));
        }
        Some("test-batch") => {
            // CTS 批量挂机执行器 (CTS Harness Spec): A2OH 桥接环境的 instrument 调度,
            // 双重看门狗 + 掉线自愈 + Crash Bundle + JUnit/summary 报告。纯增量子命令。
            let mut cfg = cts::BatchCfg {
                serial: None,
                apk_dir: None,
                modules: Vec::new(),
                profile: None,
                environment: None,
                include: None,
                exclude: None,
                resume: false,
                retry: 0,
                timeout_ms: 600_000,
                idle_timeout_ms: 90_000,
                heal_script: None,
                install_cmd: None,
                out_dir: None,
            };
            let mut detach = false;
            let mut it = args[1..].iter();
            while let Some(a) = it.next() {
                match a.as_str() {
                    "--serial" => cfg.serial = it.next().cloned(),
                    "--dir" => cfg.apk_dir = it.next().cloned(),
                    "--module" => {
                        if let Some(m) = it.next() { cfg.modules.push(m.clone()); }
                    }
                    "--profile" => cfg.profile = it.next().cloned(),
                    "--environment" | "--env" => cfg.environment = it.next().cloned(),
                    "--include" => cfg.include = it.next().cloned(),
                    "--exclude" => cfg.exclude = it.next().cloned(),
                    "--resume" => cfg.resume = true,
                    "--retry" => cfg.retry = it.next().and_then(|v| v.parse().ok()).unwrap_or(0),
                    "--timeout-ms" => cfg.timeout_ms = it.next().and_then(|v| v.parse().ok()).unwrap_or(600_000),
                    "--idle-timeout-ms" => cfg.idle_timeout_ms = it.next().and_then(|v| v.parse().ok()).unwrap_or(90_000),
                    "--heal-script" => cfg.heal_script = it.next().cloned(),
                    "--install-cmd" => cfg.install_cmd = it.next().cloned(),
                    "--out" => cfg.out_dir = it.next().cloned(),
                    "--detach" => detach = true,
                    _ => {}
                }
            }
            if detach {
                // 后台挂机: 立即回报输出目录;子进程同参重入(去 --detach),console 落盘
                let out = cfg.out_dir.clone().unwrap_or_else(|| {
                    format!("cts-batch-{}", chrono::Utc::now().format("%Y%m%d-%H%M%S"))
                });
                cfg.out_dir = Some(out.clone());
                let _ = std::fs::create_dir_all(&out);
                let console = format!("{out}/console.log");
                let mut child_args = strip_detach(&args);
                if !args.iter().any(|a| a == "--out") {
                    child_args.push("--out".into());
                    child_args.push(out.clone());
                }
                match spawn_detached_with(&child_args, &console, &[]) {
                    Ok(pid) => {
                        println!("后台批次已启动 pid={pid}");
                        println!("输出目录: {out}");
                        println!("跟进: 实时日志 tail -f {console}\n收官总账: cat {out}/summary.json (跑完才有)");
                    }
                    Err(e) => {
                        eprintln!("后台启动失败: {e}");
                        std::process::exit(2);
                    }
                }
                std::process::exit(0);
            }
            match cts::run_batch(&cfg) {
                Ok(code) => std::process::exit(code),
                Err(e) => {
                    eprintln!("test-batch 失败: {e}");
                    std::process::exit(2);
                }
            }
        }
        Some("cts-fetch") => {
            // CTS 结果提取 (SPEC_CTS_HARNESS v1.1): 设备上已有 CTS/XTS 结果 → 拉回 +
            // 断言扫描 → assertions.json。设备侧只读,与 test-batch 互补;纯增量子命令。
            let mut cfg = cts::FetchCfg {
                serial: None,
                remote: String::new(),
                out: None,
                pattern: None,
                max_mb: 16,
            };
            let mut it = args[1..].iter();
            while let Some(a) = it.next() {
                match a.as_str() {
                    "--serial" => cfg.serial = it.next().cloned(),
                    "--remote" => cfg.remote = it.next().cloned().unwrap_or_default(),
                    "--out" => cfg.out = it.next().cloned(),
                    "--pattern" => cfg.pattern = it.next().cloned(),
                    "--max-mb" => cfg.max_mb = it.next().and_then(|v| v.parse().ok()).unwrap_or(16),
                    _ => {}
                }
            }
            if cfg.remote.is_empty() {
                eprintln!("缺必填 --remote <设备侧结果路径>(文件或目录)");
                std::process::exit(2);
            }
            match cts::run_fetch(&cfg) {
                Ok(code) => std::process::exit(code),
                Err(e) => {
                    eprintln!("cts-fetch 失败: {e}");
                    std::process::exit(2);
                }
            }
        }
        Some("quest") => {
            // 原神自主跑图与剧情过关 Agent (Genshin Quest & Dialogue Agent)
            let mut serial: Option<String> = None;
            let mut mode = "auto".to_string();
            let mut max_sec: u64 = 1800;
            let mut auto_shutdown = true;
            let mut it = args[1..].iter();
            while let Some(a) = it.next() {
                match a.as_str() {
                    "--serial" => serial = it.next().cloned(),
                    "--mode" => {
                        let Some(value) = it.next() else {
                            eprintln!("quest --mode 缺少值，应为 auto|dialogue|interact|navigate");
                            std::process::exit(2);
                        };
                        if let Err(e) = plugins::genshin::QuestMode::parse(value) {
                            eprintln!("quest 参数错误: {e}");
                            std::process::exit(2);
                        }
                        mode = value.clone();
                    }
                    "--max-seconds" | "--sec" => max_sec = it.next().and_then(|v| v.parse().ok()).unwrap_or(1800),
                    "--no-shutdown" | "--no-lock" => auto_shutdown = false,
                    _ => {}
                }
            }
            if let Err(e) = plugins::genshin::QuestMode::parse(&mode) {
                eprintln!("quest 参数错误: {e}");
                std::process::exit(2);
            }
            let tmp = std::env::temp_dir().join(format!("phonefarm-quest-{}", std::process::id())).to_string_lossy().to_string();
            let _ = std::fs::create_dir_all(&tmp);
            let phone = device::Device::new(serial.clone(), tmp);
            let cfg = plugins::genshin::QuestConfig {
                mode,
                max_seconds: max_sec,
                auto_choice: true,
                auto_shutdown,
            };
            let mut agent = plugins::genshin::GenshinQuestAgent::new(&phone, cfg);
            match agent.run_loop() {
                Ok(_) => std::process::exit(0),
                Err(e) => {
                    eprintln!("Quest Agent 执行失败: {e}");
                    std::process::exit(1);
                }
            }
        }
        Some(other) => {
            // CLI 查看层(CLI Spec v1.0): 只读盘,不烧 token
            if let Some(code) = cli::dispatch(other, &args[1..]) {
                std::process::exit(code);
            }
            eprintln!("未知子命令 '{other}'");
            eprintln!("{USAGE}");
            std::process::exit(2);
        }
        None => {
            eprintln!("{USAGE}");
            std::process::exit(2);
        }
    }
}
