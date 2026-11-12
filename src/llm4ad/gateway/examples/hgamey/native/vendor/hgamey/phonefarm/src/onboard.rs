//! `phonefarm onboard` — 新游戏零人工接入。
//!
//! 给一个没见过的包名, 自己把游戏带进一个稳定、可重复的测试场景, 生成只转视角的触控
//! 负载, 实测证明它可重复, 最后写一份游戏档案 (game profile)。上层 `game_opt_loop`
//! 读这份档案就能直接跑 `optimize --game <包名>`。
//!
//! ```text
//!   启动 ─→ 截图 + OCR 认画面 ─→ 只做安全点击 (关公告 / 点击进入 / 拒权限 / 同意协议 / 游客)
//!     ↑            │                 碰到 更新 / 登录表单 / 实名 / 付费 → 停, 写明要人做什么
//!     └── 等 / 重启 ┘
//!                  │ 认不出可点的东西
//!                  ↓
//!        进游戏探针: 静置一帧 vs 拖一下视角 vs 拖回来 —— 视角跟着手指转才算「在游戏里」
//!                  ↓
//!        生成负载 (右拖 + 左拖成对, 净转角为零) → 中途抓两帧确认画面真在转
//!                  ↓
//!        找出渲染线程 (ftrace 里这个包发 GPU 提交最多的线程) → 停充 → N 轮实测
//!                  ↓
//!        frame_p95 变异系数 + 画面变化率 → 可重复判定 → profile.json
//! ```
//!
//! 纪律
//! ----
//! * **判据先于数据冻结**: 可重复的门槛是下面的常量, 不随这一次量到什么而变。
//! * **点击只走白名单**: 认不出的按钮一个都不点。凡是带 设置 / 退出 / 账号 / 商店 /
//!   下载 / 更新 / 多人 / 新游戏 / 第三方登录 字样的控件, 哪怕同时命中了「开始」也不点。
//! * **不登录、不付费、不改存档账号设置**。登录表单、实名、验证码、更新下载确认一律停下,
//!   原因写进档案的 `needs_human`, 这一次接入就算「需要人」, 不假装成功。
//! * **负载只拖视角**: 起点在画面中部偏上 (摇杆在左下, 技能键在右下, 都碰不到),
//!   右拖与左拖成对出现, 角色位置由构造保证不变。
//! * 现场还原: 充电恢复 (退出前核对 charging_enabled), 回桌面, 媒体音量核对为 0 (手机必须静音)。
//!
//! 大模型只在 OCR 认不出、探针也说不在游戏里、连续卡住时才问一次 (`--vlm-calls` 封顶),
//! 它给出的点击同样要过白名单, 且点位不能落在任何危险字样的框里。

use crate::eval::{adb, adb_path, push_tools, repo_root, run_one_with, su, DEV_TMP};
use crate::framecheck::{moved_fraction, parse_screencap};
use serde::Serialize;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

// ══════════════ 冻结的判据 ══════════════

/// 可重复的主判据: 各轮 frame_p95 的变异系数上限。已有载体实测: refbench 0.45%,
/// Megacity 0.987%, 原神夜间稳定窗口 1.37%、跨黄昏 5.35%。3% 把前三者放进来、把跨昼夜
/// 那种漂移挡在外面。
pub const REPEAT_P95_CV_MAX: f64 = 0.03;
/// 至少几轮有效数据才谈得上离散度。
pub const MIN_VALID_ROUNDS: usize = 3;
/// 一轮至少要解析出这么多帧 (30 s 窗口 ≈ 10 fps 下限), 少于它说明渲染线程认错了或游戏停了。
pub const MIN_FRAMES_PER_ROUND: i64 = 300;
/// 视角拖动时两帧之间变化像素占比的下限 (与 framecheck 的 20% 门槛同口径)。
pub const SPIN_MOVED_MIN: f64 = 0.20;
/// 温度上限 (SoC 结温, °C)。超过就停止实测, 这一份数据作废。
pub const TEMP_CAP_C: f64 = 70.0;

// ══════════════ OCR 条目与匹配 ══════════════

/// ocr.swift 的一行输出: 文字 + 像素框 [x1,y1,x2,y2] (左上原点, 与 input tap 同坐标系)。
#[derive(Debug, Clone, PartialEq, Serialize, serde::Deserialize)]
pub struct OcrItem {
    pub t: String,
    pub b: [i32; 4],
}

impl OcrItem {
    fn center(&self) -> (i32, i32) {
        ((self.b[0] + self.b[2]) / 2, (self.b[1] + self.b[3]) / 2)
    }
}

/// 归一: 小写、去空白与常见标点。中文按子串比, 英文按词序列比。
fn norm(s: &str) -> String {
    s.to_lowercase()
        .chars()
        .filter(|c| !c.is_whitespace() && !"【】[]()（）<>《》「」『』:：!！.。,，、·•-_~～\"'“”‘’?？>»›".contains(*c))
        .collect()
}

fn tokens(s: &str) -> Vec<String> {
    s.to_lowercase()
        .split(|c: char| !(c.is_ascii_alphanumeric() || c == '\''))
        .filter(|t| !t.is_empty())
        .map(|t| t.replace('\'', ""))
        .collect()
}

fn is_ascii_word(w: &str) -> bool {
    w.is_ascii()
}

/// `w` 作为「上下文词」出现在条目里 (不限长度): 英文按词序列子串, 中文按字子串。
fn mentions(item: &str, w: &str) -> bool {
    if is_ascii_word(w) {
        let it = tokens(item);
        let wt = tokens(w);
        !wt.is_empty() && it.windows(wt.len()).any(|win| win == wt.as_slice())
    } else {
        norm(item).contains(&norm(w))
    }
}

/// `w` 作为「按钮」出现: 条目本身就是这个按钮, 不是一句话里碰巧含有它。
/// 英文: 词序列包含且总词数不超过 w 的词数 + `slack_words`;
/// 中文: 子串包含且总字数不超过 w 的字数 + `slack_chars`。
fn is_button(item: &str, w: &str, slack_chars: usize, slack_words: usize) -> bool {
    if is_ascii_word(w) {
        let it = tokens(item);
        let wt = tokens(w);
        !wt.is_empty()
            && it.len() <= wt.len() + slack_words
            && it.windows(wt.len()).any(|win| win == wt.as_slice())
    } else {
        let n = norm(item);
        let nw = norm(w);
        n.contains(&nw) && n.chars().count() <= nw.chars().count() + slack_chars
    }
}

fn mentions_any(item: &str, ws: &[&str]) -> bool {
    ws.iter().any(|w| mentions(item, w))
}

// ── 词表 ──
//
// 改词表 = 改判据。每一条都写进了单测, 改之前先看测试为什么这么定。

/// 实名 / 身份证: 一律停。
const REALNAME: &[&str] = &["实名", "身份证", "id card", "identity verification"];
/// 更新 / 下载上下文。
const UPDATE_CTX: &[&str] = &[
    "更新", "下载", "新版本", "版本过低", "资源包", "update", "download", "new version",
];
/// 更新提示上的确认按钮 (有它 = 是一个要人拍板的提示, 不是进度条)。
const UPDATE_BUTTONS: &[&str] = &[
    "立即更新", "去更新", "立即下载", "更新", "下载", "确定", "确认", "前往",
    "update now", "download now", "update", "download", "ok", "yes",
];
/// 登录表单的强信号: 出现即停 (有游客入口且允许时点游客)。
const ACCOUNT_STRONG: &[&str] = &[
    "密码", "验证码", "手机号", "请输入账号", "请输入手机", "邮箱",
    "password", "verification code", "phone number", "email address",
];
/// 登录的弱信号: 只有它而没有「点击进入」时才停。
const ACCOUNT_WEAK: &[&str] = &[
    "登录", "登入", "注册", "login", "log in", "sign in", "sign up", "register", "create account",
];
/// 登录页上「更多登录方式」一类的展开按钮: 游客入口常藏在它后面 (10-11 PUBG Mobile:
/// X / Facebook / Phone/Email / More)。只在登录页、且看不到游客入口时点一次。
const LOGIN_MORE: &[&str] = &[
    "更多", "更多登录方式", "其他登录方式", "其他方式", "more", "more options", "other options",
];
/// 登录页的上下文 (第三方登录按钮常带这些词, 而整条又不像一个短按钮)。
const LOGIN_CTX: &[&str] = &["login with", "log in with", "sign in with", "continue with", "登录方式"];
/// 「正在…」类状态文字: 带「下载 / 更新」字样也不是确认按钮。
const IN_PROGRESS: &[&str] = &["正在", "downloading", "updating", "后台下载", "in progress"];
/// 游客入口。
const GUEST: &[&str] = &["游客登录", "游客模式", "游客", "以游客身份", "play as guest", "guest login", "guest"];
/// 权限弹窗上下文与「拒绝」按钮 (拒绝 = 维持默认, 不改设置)。
const PERMISSION_CTX: &[&str] = &["允许", "权限", "访问", "allow", "permission", "access"];
const PERMISSION_DENY: &[&str] = &["拒绝", "不允许", "禁止", "deny", "don't allow", "dont allow", "not now"];
/// 隐私协议上下文与「同意」按钮。
const CONSENT_CTX: &[&str] = &[
    "隐私", "协议", "政策", "个人信息", "privacy", "terms", "policy", "eula", "agreement",
];
const CONSENT_BUTTONS: &[&str] = &[
    "同意并继续", "同意并进入", "我同意", "同意", "接受",
    "agree and continue", "i agree", "agree", "accept all", "accept",
];
/// 「同意」类按钮上不许出现的否定词 (「不同意」「拒绝」「decline」)。
const NEGATION: &[&str] = &["不", "拒绝", "disagree", "decline", "don't", "dont", "not", "reject"];
/// 强关闭: 公告 / 弹窗 / 过场。
const DISMISS_STRONG: &[&str] = &["关闭", "跳过", "我知道了", "知道了", "close", "skip", "got it", "dismiss"];
/// 弱关闭: 只在画面上没有任何风险字样时才点。
const DISMISS_WEAK: &[&str] = &[
    "确定", "确认", "好的", "继续", "下一步", "稍后", "以后再说", "取消",
    "ok", "okay", "continue", "next", "later", "maybe later", "cancel",
];
/// 「点击进入」类 (长短语, 允许较长的条目)。
const START_PHRASES: &[&str] = &[
    "点击进入", "点击开始", "点击屏幕", "点击任意", "轻触屏幕", "轻触开始", "触摸屏幕",
    "tap to start", "tap to play", "tap to continue", "tap anywhere", "tap the screen",
    "touch to start", "touch to continue", "touch anywhere", "touch the screen",
    "press any", "press to start", "click to start",
];
/// 「开始游戏」类 (短按钮, 条目必须基本就是它)。
const START_BUTTONS: &[&str] = &[
    "开始游戏", "进入游戏", "单人游戏", "单人模式", "开始", "进入",
    "single player", "singleplayer", "start game", "play now", "start", "play",
];
/// 加载 / 健康忠告 / 连接中: 等。
const LOADING: &[&str] = &[
    "加载", "载入", "请稍候", "请稍后", "正在", "连接中", "登录中", "初始化", "检查", "校验", "准备",
    "健康游戏忠告", "抵制不良游戏", "适龄提示",
    "loading", "please wait", "connecting", "logging in", "initializing", "checking", "verifying",
    "preparing",
    // 机型自带的游戏空间浮层 (红魔「CUBE能量魔方」): 启动游戏时弹几秒, 等它收起
    "能量魔方",
];
/// 付费上下文 (画面上有它, 弱关闭类按钮一律不点)。
const PAY_CTX: &[&str] = &[
    "充值", "购买", "支付", "付费", "礼包", "特惠", "¥", "￥",
    "purchase", "buy", "pay", "top up", "recharge", "price",
];
/// 永远不点的控件: 条目里只要带这些字样, 哪怕同时命中白名单也不点。
const AVOID: &[&str] = &[
    "设置", "选项", "退出", "删除", "重置", "修复", "登出", "注销", "切换账号", "切换帐号",
    "账号", "帐号", "客服", "反馈", "评分", "评价", "分享", "商店", "商城", "充值", "购买",
    "支付", "付费", "礼包", "下载", "更新", "多人", "联机", "新游戏", "画质", "绑定", "兑换",
    "微信", "微博", "抖音",
    "settings", "setting", "options", "exit", "quit", "delete", "reset", "repair", "logout",
    "log out", "sign out", "switch account", "account", "support", "feedback", "rate us",
    "share", "shop", "store", "buy", "purchase", "pay", "download", "update", "multiplayer",
    "online", "new game", "graphics", "bind", "redeem", "google", "facebook", "apple",
    "twitter", "tiktok", "wechat",
];

fn avoided(item: &str) -> bool {
    mentions_any(item, AVOID)
}

/// 进度: `45%` / `12.3MB/1.2GB` / `3.4MB/s`。单独一个大小 (「需要下载 1.2GB」) 不是进度,
/// 是提示文案的一部分。
fn is_progress(item: &str) -> bool {
    let re = regex::Regex::new(
        r"(?i)(\d{1,3}(\.\d+)?\s*%)|(\d+(\.\d+)?\s*(kb|mb|gb)\s*/\s*\d+(\.\d+)?\s*(kb|mb|gb))|(\d+(\.\d+)?\s*(kb|mb)\s*/\s*s\b)",
    )
    .unwrap();
    re.is_match(item)
}

// ══════════════ 画面分类 (纯函数) ══════════════

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Screen {
    NotForeground,
    Permission,
    Update,
    RealName,
    Account,
    Consent,
    Popup,
    Start,
    Loading,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "do", rename_all = "snake_case")]
pub enum Act {
    Tap { x: i32, y: i32, label: String, rule: String },
    Wait { why: String },
    /// 认不出可点的东西 → 跑进游戏探针。
    Probe,
    /// 前台不是目标应用 → 重新拉起。
    Relaunch,
    /// 需要人: 登录 / 实名 / 更新 / 付费 / 认不出。
    Stop { reason: String },
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Decision {
    pub screen: Screen,
    pub act: Act,
}

/// 点击策略。缺省两项都开: 主人认可「游客模式即可进入」的游戏优先, 隐私协议不点
/// 同意就进不去任何一个新装的游戏。两项都会原样记进档案的 entry.policy。
#[derive(Debug, Clone, Copy, Serialize)]
pub struct Policy {
    pub allow_guest: bool,
    pub allow_consent: bool,
}

impl Default for Policy {
    fn default() -> Self {
        Policy { allow_guest: true, allow_consent: true }
    }
}

/// 按 `words` 的先后 (优先级) 找第一个是按钮、且不在避让表里的条目。
fn find_button<'a>(
    items: &'a [OcrItem],
    words: &[&'static str],
    slack_chars: usize,
    slack_words: usize,
    banned: &[(i32, i32)],
) -> Option<(&'a OcrItem, &'static str)> {
    for w in words {
        for it in items {
            if avoided(&it.t) || near_banned(it, banned) || mentions_any(&it.t, LOADING) {
                continue;
            }
            if is_button(&it.t, w, slack_chars, slack_words) {
                return Some((it, *w));
            }
        }
    }
    None
}

fn near_banned(it: &OcrItem, banned: &[(i32, i32)]) -> bool {
    let (x, y) = it.center();
    banned.iter().any(|(bx, by)| (x - bx).abs() < 40 && (y - by).abs() < 40)
}

fn any_mention(items: &[OcrItem], ws: &[&str]) -> bool {
    items.iter().any(|it| mentions_any(&it.t, ws))
}

fn tap(it: &OcrItem, rule: &str, word: &str) -> Act {
    let (x, y) = it.center();
    Act::Tap { x, y, label: it.t.clone(), rule: format!("{rule}:{word}") }
}

/// 一帧画面 → 下一步做什么。
///
/// `fg_ok`: 前台是不是目标包; `fg_pkg`: 实际前台包名 (权限弹窗在系统包里);
/// `banned`: 点过但画面没变的位置, 不再点第二次。
pub fn classify(items: &[OcrItem], fg_ok: bool, fg_pkg: &str, policy: Policy, banned: &[(i32, i32)]) -> Decision {
    let d = |screen, act| Decision { screen, act };

    if !fg_ok {
        let sys_dialog = fg_pkg.contains("permissioncontroller") || fg_pkg.contains("packageinstaller");
        if sys_dialog {
            if let Some((it, w)) = find_button(items, PERMISSION_DENY, 3, 1, banned) {
                return d(Screen::Permission, tap(it, "permission_deny", w));
            }
            return d(
                Screen::Permission,
                Act::Stop { reason: format!("系统弹窗 ({fg_pkg}) 上找不到「拒绝」, 不敢替人选") },
            );
        }
        return d(Screen::NotForeground, Act::Relaunch);
    }

    if any_mention(items, REALNAME) {
        return d(Screen::RealName, Act::Stop { reason: "实名认证 / 身份证信息: 需要人".into() });
    }

    let progress = items.iter().any(|it| is_progress(&it.t));
    if any_mention(items, UPDATE_CTX) {
        let has_btn = items.iter().any(|it| {
            !mentions_any(&it.t, IN_PROGRESS) && UPDATE_BUTTONS.iter().any(|w| is_button(&it.t, w, 2, 1))
        });
        if has_btn {
            return d(
                Screen::Update,
                Act::Stop { reason: "更新 / 下载提示 (要确认才会继续): 不替人点, 需要人或 core 线处理".into() },
            );
        }
        if progress {
            return d(Screen::Loading, Act::Wait { why: "资源下载 / 更新进度中".into() });
        }
    }

    let strong_account = any_mention(items, ACCOUNT_STRONG);
    if strong_account {
        if policy.allow_guest {
            if let Some((it, w)) = find_button(items, GUEST, 4, 2, banned) {
                return d(Screen::Account, tap(it, "guest", w));
            }
        }
        return d(Screen::Account, Act::Stop { reason: "登录表单 (账号 / 密码 / 验证码): 需要人登录".into() });
    }

    if any_mention(items, PERMISSION_CTX) {
        if let Some((it, w)) = find_button(items, PERMISSION_DENY, 3, 1, banned) {
            return d(Screen::Permission, tap(it, "permission_deny", w));
        }
    }

    if any_mention(items, CONSENT_CTX) {
        let agree = CONSENT_BUTTONS.iter().find_map(|w| {
            items.iter().find(|it| {
                is_button(&it.t, w, 3, 1)
                    && !mentions_any(&it.t, NEGATION)
                    && !avoided(&it.t)
                    && !near_banned(it, banned)
            }).map(|it| (it, *w))
        });
        if let Some((it, w)) = agree {
            if policy.allow_consent {
                return d(Screen::Consent, tap(it, "consent", w));
            }
            return d(Screen::Consent, Act::Stop { reason: "隐私协议要同意 (策略不允许自动同意)".into() });
        }
    }

    if let Some((it, w)) = find_button(items, DISMISS_STRONG, 3, 1, banned) {
        return d(Screen::Popup, tap(it, "dismiss", w));
    }

    if let Some((it, w)) = find_button(items, START_PHRASES, 12, 4, banned) {
        return d(Screen::Start, tap(it, "start", w));
    }
    // 短按钮必须整条就是它 (「开始挑战」「Player 1」都不是开始按钮); 长短语在词表里单列
    if let Some((it, w)) = find_button(items, START_BUTTONS, 0, 0, banned) {
        return d(Screen::Start, tap(it, "start", w));
    }

    let weak_account = items.iter().any(|it| {
        !mentions_any(&it.t, LOADING)
            && (ACCOUNT_WEAK.iter().any(|w| is_button(&it.t, w, 4, 2)) || mentions_any(&it.t, LOGIN_CTX))
    });
    if weak_account {
        if policy.allow_guest {
            if let Some((it, w)) = find_button(items, GUEST, 4, 2, banned) {
                return d(Screen::Account, tap(it, "guest", w));
            }
            // 看不到游客入口: 展开「更多」看一眼 (点过没反应会被拉黑, 不会反复点)
            if let Some((it, w)) = find_button(items, LOGIN_MORE, 2, 1, banned) {
                return d(Screen::Account, tap(it, "login_more", w));
            }
        }
        return d(Screen::Account, Act::Stop { reason: "只有登录入口, 没有游客 / 点击进入: 需要人登录".into() });
    }

    let risky = any_mention(items, PAY_CTX) || any_mention(items, UPDATE_CTX);
    if !risky {
        if let Some((it, w)) = find_button(items, DISMISS_WEAK, 2, 1, banned) {
            return d(Screen::Popup, tap(it, "dismiss_weak", w));
        }
    }

    if progress || any_mention(items, LOADING) {
        return d(Screen::Loading, Act::Wait { why: "加载 / 连接 / 健康忠告".into() });
    }

    d(Screen::Unknown, Act::Probe)
}

/// 进游戏探针的判定: 视角跟着手指转 (swing 大), 静置时画面基本不动 (idle 小),
/// 拖回去之后明显比拖过去时更接近原样 (ret ≤ 0.75·swing)。三条同时成立才算「在游戏里」。
///
/// 第三条的 0.75 来自 10-11 原神冷启动的误判: 启动过场 (加上机型的游戏空间浮层) 在拖动
/// 期间整屏换画面, 量到 idle 0.00 / swing 0.99 / ret 0.97 —— 「拖回来」几乎没回来,
/// 画面变是它自己在变, 不是跟着手指。真在大世界里同一台机量到 swing 0.73 / ret 0.42。
pub fn ingame_verdict(idle: f64, swing: f64, ret: f64) -> bool {
    swing >= SPIN_MOVED_MIN && swing >= idle * 1.5 + 0.05 && ret <= swing * RETURN_RATIO_MAX
}

/// 拖回来之后剩下的变化占拖过去时的比例上限 (见 `ingame_verdict`)。
pub const RETURN_RATIO_MAX: f64 = 0.75;

// ══════════════ 可重复判定 (纯函数) ══════════════

#[derive(Debug, Clone, Default, Serialize)]
pub struct RoundStat {
    pub frame_p95: Option<f64>,
    pub frame_p50: Option<f64>,
    pub fps_mean: Option<f64>,
    pub n_frames: i64,
    pub power_w: Option<f64>,
    pub soc_temp_max_c: Option<f64>,
    /// 这一轮负载前后两帧的变化占比 (视角回没回到原处)。
    pub return_fraction: Option<f64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Repeatability {
    pub verdict: &'static str,
    pub reasons: Vec<String>,
    pub warnings: Vec<String>,
    pub valid_rounds: usize,
    pub frame_p95_cv: Option<f64>,
    pub frame_p50_cv: Option<f64>,
    pub fps_mean_cv: Option<f64>,
    pub power_w_cv: Option<f64>,
    pub monotone_p95: bool,
}

/// 样本变异系数 std(n-1)/mean。少于 2 个或均值非正时 None。
pub fn cv(xs: &[f64]) -> Option<f64> {
    if xs.len() < 2 {
        return None;
    }
    let n = xs.len() as f64;
    let mu = xs.iter().sum::<f64>() / n;
    if mu <= 0.0 {
        return None;
    }
    let var = xs.iter().map(|x| (x - mu).powi(2)).sum::<f64>() / (n - 1.0);
    Some(var.sqrt() / mu)
}

fn round6(x: f64) -> f64 {
    (x * 1e6).round() / 1e6
}

fn monotone(xs: &[f64]) -> bool {
    xs.len() >= 4
        && (xs.windows(2).all(|w| w[1] > w[0]) || xs.windows(2).all(|w| w[1] < w[0]))
}

pub fn judge(rounds: &[RoundStat], spin_ok: bool, over_temp: bool) -> Repeatability {
    let valid: Vec<&RoundStat> = rounds
        .iter()
        .filter(|r| r.frame_p95.is_some() && r.n_frames >= MIN_FRAMES_PER_ROUND)
        .collect();
    let col = |f: fn(&RoundStat) -> Option<f64>| -> Vec<f64> { valid.iter().filter_map(|r| f(r)).collect() };
    let p95 = col(|r| r.frame_p95);
    let p50 = col(|r| r.frame_p50);
    let fps = col(|r| r.fps_mean);
    let pw = col(|r| r.power_w);
    let p95_cv = cv(&p95);

    let mut reasons = Vec::new();
    let mut warnings = Vec::new();
    if !spin_ok {
        reasons.push(format!("负载没让画面转起来 (拖动中两帧变化 < {:.0}%)", SPIN_MOVED_MIN * 100.0));
    }
    if over_temp {
        reasons.push(format!("SoC 超过 {TEMP_CAP_C} °C, 实测中止"));
    }
    if valid.len() < MIN_VALID_ROUNDS {
        reasons.push(format!(
            "有效轮数 {} < {MIN_VALID_ROUNDS} (每轮要解析出 ≥ {MIN_FRAMES_PER_ROUND} 帧)",
            valid.len()
        ));
    }
    match p95_cv {
        Some(c) if c > REPEAT_P95_CV_MAX => reasons.push(format!(
            "frame_p95 变异系数 {:.2}% > {:.0}%",
            c * 100.0,
            REPEAT_P95_CV_MAX * 100.0
        )),
        None if valid.len() >= MIN_VALID_ROUNDS => reasons.push("frame_p95 算不出变异系数".into()),
        _ => {}
    }
    let mono = monotone(&p95);
    if mono {
        warnings.push("frame_p95 逐轮单调漂移 (温度或场景随时间变化), 跑 A/B 时必须交替".into());
    }
    let rets: Vec<f64> = valid.iter().filter_map(|r| r.return_fraction).collect();
    if !rets.is_empty() {
        let m = rets.iter().sum::<f64>() / rets.len() as f64;
        if m >= 0.5 {
            warnings.push(format!(
                "负载前后画面变化 {:.0}%: 视角没回到原处 (相机惯性 / 自动回正 / 场景在动), 每轮起点不完全相同",
                m * 100.0
            ));
        }
    }
    Repeatability {
        verdict: if reasons.is_empty() { "PASS" } else { "FAIL" },
        reasons,
        warnings,
        valid_rounds: valid.len(),
        frame_p95_cv: p95_cv.map(round6),
        frame_p50_cv: cv(&p50).map(round6),
        fps_mean_cv: cv(&fps).map(round6),
        power_w_cv: cv(&pw).map(round6),
        monotone_p95: mono,
    }
}

// ══════════════ 负载 ══════════════

/// 一组拖动几何 (占屏幕宽高的比例)。
#[derive(Debug, Clone, Copy, Serialize)]
pub struct SwipeGeom {
    pub x0: f64,
    pub x1: f64,
    pub y: f64,
    pub ms: u64,
}

/// 依次尝试的几何。起点都在画面中部偏上: 摇杆在左下、技能键在右下、小地图在左上、
/// 菜单在右上, 中部是几乎所有 3D 游戏留给拖视角的区域。
pub const GEOMS: [SwipeGeom; 3] = [
    SwipeGeom { x0: 0.40, x1: 0.70, y: 0.40, ms: 10_000 },
    SwipeGeom { x0: 0.30, x1: 0.80, y: 0.40, ms: 10_000 },
    SwipeGeom { x0: 0.40, x1: 0.70, y: 0.55, ms: 10_000 },
];

fn px(g: SwipeGeom, w: u32, h: u32) -> (i32, i32, i32) {
    (
        (g.x0 * w as f64).round() as i32,
        (g.x1 * w as f64).round() as i32,
        (g.y * h as f64).round() as i32,
    )
}

/// 生成负载: 静置 3 s, 然后右拖 / 左拖成对, 一共 `pairs` 对。净转角为零, 每轮起点相同。
/// 单次拖动 ≤ 12 s (script 的 exec 超时是 15 s)。
pub fn workload_json(g: SwipeGeom, w: u32, h: u32, pairs: usize) -> Value {
    let (x0, x1, y) = px(g, w, h);
    let mut steps = vec![json!({"action": "sleep", "ms": 3000})];
    for _ in 0..pairs {
        steps.push(json!({"action": "exec", "cmd": format!("input swipe {x0} {y} {x1} {y} {}", g.ms)}));
        steps.push(json!({"action": "exec", "cmd": format!("input swipe {x1} {y} {x0} {y} {}", g.ms)}));
    }
    steps.push(json!({"action": "sleep", "ms": 1000}));
    Value::Array(steps)
}

/// 负载要覆盖 run_once 的采集窗口: 起跑后 LEAD=6 s 开采, 采 `cap_s` 秒, 再留 4 s 余量。
pub fn pairs_for(cap_s: u64, g: SwipeGeom) -> usize {
    let need_ms = (6 + cap_s + 4) * 1000;
    let pair_ms = 2 * g.ms;
    (need_ms.saturating_sub(3000)).div_ceil(pair_ms).max(1) as usize
}

// ══════════════ 渲染线程 ══════════════

/// 在 ftrace 文本里数这个包的线程发出的 GPU 提交, 取最多的线程名。
/// `tids`: 目标进程的线程号集合。返回 (线程名, 占比, 包内提交总数, 全部提交总数)。
pub fn pick_render_comm(trace: &str, tids: &[i64]) -> (Option<String>, f64, i64, i64) {
    let re = regex::Regex::new(
        r"^\s*(?P<comm>.+?)-(?P<tid>\d+)\s+\[\d+\]\s+\S+\s+\d+\.\d+:\s+adreno_cmdbatch_submitted:",
    )
    .unwrap();
    let mut counts: Vec<(String, i64)> = Vec::new();
    let (mut mine, mut all) = (0i64, 0i64);
    for line in trace.lines() {
        let Some(m) = re.captures(line) else { continue };
        all += 1;
        let tid: i64 = m["tid"].parse().unwrap_or(-1);
        if !tids.contains(&tid) {
            continue;
        }
        mine += 1;
        let c = m["comm"].trim().to_string();
        match counts.iter_mut().find(|(k, _)| *k == c) {
            Some(s) => s.1 += 1,
            None => counts.push((c, 1)),
        }
    }
    counts.sort_by_key(|(_, n)| std::cmp::Reverse(*n));
    match counts.first() {
        Some((c, n)) => (Some(c.clone()), *n as f64 / mine.max(1) as f64, mine, all),
        None => (None, 0.0, mine, all),
    }
}

// ══════════════ 设备助手 ══════════════

struct Dev {
    serial: String,
}

impl Dev {
    fn sh(&self, cmd: &str, timeout: u64) -> String {
        let out = adb(&self.serial, &["shell", cmd], timeout);
        out.rsplit_once("#adb_exit=").map(|(a, _)| a.to_string()).unwrap_or(out)
    }
    fn su(&self, cmd: &str, timeout: u64) -> String {
        let out = su(&self.serial, cmd, timeout);
        out.rsplit_once("#adb_exit=").map(|(a, _)| a.to_string()).unwrap_or(out)
    }
    fn cap(&self) -> Vec<u8> {
        crate::fd_lock::transport_command(adb_path())
            .args(["-s", &self.serial, "exec-out", "screencap"])
            .output()
            .map(|o| o.stdout)
            .unwrap_or_default()
    }
    fn tap(&self, x: i32, y: i32) {
        self.sh(&format!("input tap {x} {y}"), 10);
    }
    fn swipe(&self, x0: i32, y0: i32, x1: i32, y1: i32, ms: u64) {
        self.sh(&format!("input swipe {x0} {y0} {x1} {y1} {ms}"), ms / 1000 + 10);
    }
    fn swipe_bg(&self, x0: i32, y0: i32, x1: i32, y1: i32, ms: u64) -> Option<std::process::Child> {
        crate::fd_lock::transport_command(adb_path())
            .args(["-s", &self.serial, "shell", &format!("input swipe {x0} {y0} {x1} {y1} {ms}")])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .ok()
    }
    fn foreground(&self) -> String {
        parse_focus(&self.sh("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'", 8))
    }
    fn pid(&self, pkg: &str) -> Option<i64> {
        self.sh(&format!("pidof {pkg}"), 6).split_whitespace().next()?.parse().ok()
    }
}

/// `cmd media_session volume --stream 3 --get` 的输出里取当前音量 ("volume is 0 in range [0..15]")。
pub fn parse_volume(s: &str) -> Option<u32> {
    let i = s.find("volume is ")? + "volume is ".len();
    s[i..].split_whitespace().next()?.parse().ok()
}

/// 手机必须保持静音 (owner 规矩): 媒体音量 (stream 3) 不是 0 就压回 0。返回核对记录。
/// 本流程本身从不调音量; 这一步防的是游戏或别的流程把它带上来。
fn ensure_muted(dev: &Dev) -> Value {
    let before = parse_volume(&dev.sh("cmd media_session volume --stream 3 --get", 10));
    if before != Some(0) {
        dev.sh("cmd audio set-volume 3 0", 10);
    }
    let after = parse_volume(&dev.sh("cmd media_session volume --stream 3 --get", 10));
    json!({"stream3_before": before, "stream3_after": after})
}

fn wait_child(c: Option<std::process::Child>, secs: u64) {
    if let Some(mut c) = c {
        let deadline = Instant::now() + Duration::from_secs(secs);
        while matches!(c.try_wait(), Ok(None)) {
            if Instant::now() > deadline {
                let _ = c.kill();
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        let _ = c.wait();
    }
}

/// `dumpsys window` 焦点行 → 包名。
pub fn parse_focus(s: &str) -> String {
    for key in ["mCurrentFocus", "mFocusedApp"] {
        for line in s.lines().filter(|l| l.contains(key)) {
            let Some(i) = line.find(" u0 ") else { continue };
            let tok: String = line[i + 4..]
                .chars()
                .take_while(|c| *c != '/' && *c != '}' && *c != ' ')
                .collect();
            if tok.contains('.') && tok.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_') {
                return tok;
            }
        }
    }
    String::new()
}

/// `cmd package resolve-activity --brief` 的输出 → 启动组件。
pub fn parse_component(s: &str, pkg: &str) -> Option<String> {
    s.lines()
        .map(str::trim)
        .rfind(|l| l.starts_with(&format!("{pkg}/")))
        .map(str::to_string)
}

/// 裸帧 → RGB 图。
fn to_rgb(raw: &[u8]) -> Option<image::RgbImage> {
    let (w, h, px) = parse_screencap(raw).ok()?;
    let n = (w * h * 4) as usize;
    if px.len() < n {
        return None;
    }
    let rgba = image::RgbaImage::from_raw(w, h, px[..n].to_vec())?;
    Some(image::DynamicImage::ImageRgba8(rgba).to_rgb8())
}

/// 画面是不是一块纯色 (黑屏过场 / 白屏): 抽样亮度标准差很小。
fn near_uniform(img: &image::RgbImage) -> bool {
    let (w, h) = img.dimensions();
    let mut v = Vec::new();
    let step = ((w * h) / 4000).max(1);
    let mut i = 0u32;
    while i < w * h {
        let p = img.get_pixel(i % w, i / w);
        v.push(0.299 * p[0] as f64 + 0.587 * p[1] as f64 + 0.114 * p[2] as f64);
        i += step;
    }
    let mu = v.iter().sum::<f64>() / v.len().max(1) as f64;
    let var = v.iter().map(|x| (x - mu).powi(2)).sum::<f64>() / v.len().max(1) as f64;
    var.sqrt() < 6.0
}

fn save_jpg(img: &image::RgbImage, path: &Path, width: u32) {
    let (w, h) = img.dimensions();
    let out = if w > width {
        image::imageops::resize(img, width, (h as f64 * width as f64 / w as f64) as u32, image::imageops::FilterType::Triangle)
    } else {
        img.clone()
    };
    if let Some(p) = path.parent() {
        let _ = std::fs::create_dir_all(p);
    }
    let _ = out.save_with_format(path, image::ImageFormat::Jpeg);
}

/// 找 / 编 OCR 工具 (仓库根的 ./ocr, 源码 ocr.swift)。
fn ocr_bin() -> Result<PathBuf, String> {
    // --ocr 给的现成可执行 (手机节点上用 pf-node 跑本机构建时, 连同它一起上传)
    if let Some(p) = std::env::var_os("PF_OCR_BIN").map(PathBuf::from) {
        if p.is_file() {
            return Ok(p);
        }
    }
    let root = repo_root();
    let bin = root.join("ocr");
    if bin.is_file() {
        return Ok(bin);
    }
    let src = root.join("ocr.swift");
    let st = Command::new("swiftc")
        .args(["-O", &src.to_string_lossy(), "-o", &bin.to_string_lossy()])
        .status()
        .map_err(|e| format!("编不了 OCR 工具 (swiftc): {e}"))?;
    if st.success() && bin.is_file() {
        Ok(bin)
    } else {
        Err(format!("编不了 OCR 工具: swiftc 退出 {st}"))
    }
}

/// 像一句话的 OCR 条目 (对话框正文): 英文 ≥ 4 个词, 或中文 ≥ 8 个字。
fn sentence_like(t: &str) -> bool {
    tokens(t).len() >= 4 || norm(t).chars().filter(|c| !c.is_ascii()).count() >= 8
}

/// 第二遍 OCR 要放大看的区域: 把「一句话」的条目按上下相邻聚成块 (对话框正文),
/// 每块往下多看 4 行 (按钮通常在正文下方)、上面 1 行、左右各 15% 外加 5% 屏宽。
/// 整屏 OCR 对正文下面的小按钮 (10-11 Megacity 的「OK」) 会漏, 裁出来单独认就认得出。
/// 返回 [x1,y1,x2,y2] (像素, 已截到屏幕内); 超过半屏的块不要 (那不是对话框)。
pub fn zoom_regions(items: &[OcrItem], w: u32, h: u32) -> Vec<[i32; 4]> {
    let mut sent: Vec<[i32; 4]> = items.iter().filter(|i| sentence_like(&i.t)).map(|i| i.b).collect();
    sent.sort_by_key(|b| b[1]);
    let mut blocks: Vec<([i32; 4], i32)> = Vec::new(); // (并集框, 行高)
    for b in sent {
        let lh = (b[3] - b[1]).max(1);
        if let Some((u, l)) = blocks.last_mut() {
            let x_overlap = b[0] < u[2] && b[2] > u[0];
            if x_overlap && b[1] - u[3] <= 2 * (*l).max(lh) {
                *u = [u[0].min(b[0]), u[1].min(b[1]), u[2].max(b[2]), u[3].max(b[3])];
                *l = (*l).max(lh);
                continue;
            }
        }
        blocks.push((b, lh));
    }
    let (w, h) = (w as i32, h as i32);
    blocks
        .into_iter()
        .map(|(u, lh)| {
            let mx = (u[2] - u[0]) * 15 / 100 + w / 20;
            [(u[0] - mx).max(0), (u[1] - lh).max(0), (u[2] + mx).min(w), (u[3] + 4 * lh).min(h)]
        })
        .filter(|r| (r[2] - r[0]) as i64 * (r[3] - r[1]) as i64 * 2 <= w as i64 * h as i64)
        .collect()
}

/// 对 `zoom_regions` 的每块裁图放大 1.5 倍再认一遍, 坐标换回整屏; 和第一遍重复的 (中心落在
/// 已有框里) 不要。
fn zoom_ocr(bin: &Path, img: &image::RgbImage, items: &[OcrItem], tmp: &Path) -> Vec<OcrItem> {
    let (w, h) = img.dimensions();
    let mut out = Vec::new();
    for r in zoom_regions(items, w, h) {
        let (cw, ch) = ((r[2] - r[0]) as u32, (r[3] - r[1]) as u32);
        if cw < 8 || ch < 8 {
            continue;
        }
        let crop = image::imageops::crop_imm(img, r[0] as u32, r[1] as u32, cw, ch).to_image();
        let big = image::imageops::resize(&crop, cw * 3 / 2, ch * 3 / 2, image::imageops::FilterType::CatmullRom);
        if big.save_with_format(tmp, image::ImageFormat::Jpeg).is_err() {
            continue;
        }
        for mut it in run_ocr(bin, tmp) {
            for k in 0..4 {
                let off = if k % 2 == 0 { r[0] } else { r[1] };
                it.b[k] = it.b[k] * 2 / 3 + off;
            }
            let (cx, cy) = it.center();
            let dup = items.iter().chain(out.iter()).any(|e: &OcrItem| cx >= e.b[0] && cx <= e.b[2] && cy >= e.b[1] && cy <= e.b[3]);
            if !dup {
                out.push(it);
            }
        }
    }
    let _ = std::fs::remove_file(tmp);
    out
}

fn run_ocr(bin: &Path, img: &Path) -> Vec<OcrItem> {
    let Ok(o) = Command::new(bin).arg(img).output() else { return vec![] };
    String::from_utf8_lossy(&o.stdout)
        .lines()
        .filter_map(|l| serde_json::from_str::<OcrItem>(l).ok())
        .collect()
}

// ══════════════ 大模型兜底 ══════════════

struct Vlm {
    brain: crate::brain::Brain,
    left: u32,
    used: u32,
}

impl Vlm {
    fn new(calls: u32) -> Option<Vlm> {
        if calls == 0 {
            return None;
        }
        let root = repo_root();
        let text = std::fs::read_to_string(root.join("phonefarm.toml")).ok()?;
        let v: toml::Value = toml::from_str(&text).ok()?;
        let provs: Vec<crate::brain::ProviderCfg> = v
            .get("providers")?
            .as_array()?
            .iter()
            .filter_map(|p| toml::Value::try_into::<crate::brain::ProviderCfg>(p.clone()).ok())
            .filter(|p| p.vision)
            .collect();
        if let Ok(s) = std::fs::read_to_string(root.join("secrets.env")) {
            for (k, val) in crate::llm::parse_secrets(&s) {
                if std::env::var(&k).map(|x| x.trim().is_empty()).unwrap_or(true) {
                    std::env::set_var(&k, val);
                }
            }
        }
        if provs.is_empty() {
            return None;
        }
        let tmp = std::env::temp_dir().join(format!("pf-onboard-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&tmp);
        Some(Vlm { brain: crate::brain::Brain::new(provs, tmp.to_string_lossy().into()), left: calls, used: 0 })
    }

    /// 问一次: 这是什么画面, 有没有一个安全的点击。回包里的点击还要过 [`vet_vlm_tap`]。
    fn ask(&mut self, img: &Path, items: &[OcrItem]) -> Option<Value> {
        if self.left == 0 {
            return None;
        }
        self.left -= 1;
        self.used += 1;
        let sys = "你在帮一个自动化测试程序把手机游戏带进可以操作视角的游戏场景。\
只允许建议这几类点击: 关闭公告/弹窗 (X 图标或关闭字样)、跳过过场动画、点「点击进入/开始游戏」、点击屏幕继续。\
绝不建议: 登录、注册、付费、设置、退出、改存档、多人模式、下载更新。\
只输出一个 JSON 对象, 不要别的文字: \
{\"state\":\"popup|start|loading|login|update|ingame|other\",\"tap\":{\"x\":0到999,\"y\":0到999} 或 null,\"label\":\"要点的东西是什么\",\"why\":\"一句话\"}。\
坐标按截图宽高归一化到 0-999。";
        let texts: Vec<String> = items.iter().take(30).map(|i| i.t.clone()).collect();
        let user = format!("截图上 OCR 认出的文字: {}\n这个画面是什么? 有没有一个安全的点击能往游戏里推进?", texts.join(" | "));
        let img_s = img.to_string_lossy().to_string();
        let out = self.brain.call(sys, &user, &[img_s.as_str()], 300, 0).ok()?;
        let t = out.text;
        let (i, j) = (t.find('{')?, t.rfind('}')?);
        let mut v: Value = serde_json::from_str(&t[i..=j]).ok()?;
        v["by"] = json!(out.by);
        Some(v)
    }
}

/// 大模型给的点击过一遍白名单: 标签必须是关闭 / 跳过 / 开始类, 且不碰避让字样;
/// 点位不能落在任何危险字样的 OCR 框里 (框外扩 20 px)。
pub fn vet_vlm_tap(label: &str, x: i32, y: i32, items: &[OcrItem]) -> Result<(), String> {
    let l = label.to_lowercase();
    let icon = ["x", "×", "✕", "close icon", "关闭按钮", "关闭图标", "叉"].iter().any(|k| l.contains(k));
    let safe_word = DISMISS_STRONG.iter().chain(START_PHRASES).chain(START_BUTTONS).any(|w| mentions(label, w));
    if !(icon || safe_word) {
        return Err(format!("标签「{label}」不是关闭 / 跳过 / 开始类"));
    }
    if avoided(label) || mentions_any(label, ACCOUNT_WEAK) || mentions_any(label, PAY_CTX) {
        return Err(format!("标签「{label}」带危险字样"));
    }
    for it in items {
        let danger = avoided(&it.t) || mentions_any(&it.t, ACCOUNT_WEAK) || mentions_any(&it.t, PAY_CTX)
            || mentions_any(&it.t, ACCOUNT_STRONG);
        if danger && x >= it.b[0] - 20 && x <= it.b[2] + 20 && y >= it.b[1] - 20 && y <= it.b[3] + 20 {
            return Err(format!("点位落在「{}」上", it.t));
        }
    }
    Ok(())
}

// ══════════════ 进门 ══════════════

struct EnterCfg {
    pkg: String,
    component: String,
    cold: bool,
    policy: Policy,
    max_steps: usize,
    timeout_s: u64,
    out: PathBuf,
    vlm_calls: u32,
}

#[derive(Debug, Clone, Serialize)]
struct EnterResult {
    status: &'static str, // INGAME / NEEDS_HUMAN / FAILED
    reason: Option<String>,
    steps: Vec<Value>,
    taps: usize,
    seconds: f64,
    screen_wh: (u32, u32),
    probe: Option<Value>,
    vlm_calls: u32,
    needs_human_shot: Option<String>,
}

fn mf(a: &[u8], b: &[u8]) -> Option<f64> {
    moved_fraction(a, b).ok().map(|(f, _)| (f * 1e4).round() / 1e4)
}

/// 进游戏探针: 静置 1.5 s 一对帧, 拖过去一帧, 拖回来一帧。
fn probe_ingame(dev: &Dev, w: u32, h: u32) -> Value {
    let g = SwipeGeom { x0: 0.40, x1: 0.70, y: 0.40, ms: 1000 };
    let (x0, x1, y) = px(g, w, h);
    let a = dev.cap();
    std::thread::sleep(Duration::from_millis(1500));
    let b = dev.cap();
    dev.swipe(x0, y, x1, y, g.ms);
    std::thread::sleep(Duration::from_millis(600));
    let c = dev.cap();
    dev.swipe(x1, y, x0, y, g.ms);
    std::thread::sleep(Duration::from_millis(600));
    let d = dev.cap();
    let (idle, swing, ret) = (mf(&a, &b), mf(&b, &c), mf(&b, &d));
    let ingame = match (idle, swing, ret) {
        (Some(i), Some(s), Some(r)) => ingame_verdict(i, s, r),
        _ => false,
    };
    json!({"idle": idle, "swing": swing, "ret": ret, "ingame": ingame,
           "swipe": [x0, y, x1, y, g.ms]})
}

fn launch(dev: &Dev, component: &str) {
    dev.sh(
        &format!("am start -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -f 0x10200000 -n {component}"),
        30,
    );
}

fn enter(dev: &Dev, cfg: &EnterCfg) -> EnterResult {
    let t0 = Instant::now();
    let shots = cfg.out.join("shots");
    let _ = std::fs::create_dir_all(&shots);
    let ocr_img = cfg.out.join("_ocr.jpg");
    let mut res = EnterResult {
        status: "FAILED",
        reason: None,
        steps: vec![],
        taps: 0,
        seconds: 0.0,
        screen_wh: (0, 0),
        probe: None,
        vlm_calls: 0,
        needs_human_shot: None,
    };
    let ocr = match ocr_bin() {
        Ok(b) => b,
        Err(e) => {
            res.reason = Some(e);
            return res;
        }
    };
    let mut vlm = Vlm::new(cfg.vlm_calls);

    dev.sh("input keyevent 224", 5); // 亮屏 (已亮时无副作用)
    if cfg.cold {
        dev.sh(&format!("am force-stop {}", cfg.pkg), 15);
        std::thread::sleep(Duration::from_secs(2));
    }
    launch(dev, &cfg.component);
    std::thread::sleep(Duration::from_secs(4));

    let mut banned: Vec<(i32, i32)> = Vec::new();
    let mut last_tap: Option<(i32, i32, Vec<u8>)> = None;
    let mut relaunches = 0;
    let mut unknown_streak = 0;
    let mut center_tapped = false;
    let mut log = std::fs::File::create(cfg.out.join("entry_log.jsonl")).ok();

    for n in 0..cfg.max_steps {
        if t0.elapsed().as_secs() > cfg.timeout_s {
            res.reason = Some(format!("{} s 内没进到游戏里", cfg.timeout_s));
            break;
        }
        let fg = dev.foreground();
        let raw = dev.cap();
        let Some(img) = to_rgb(&raw) else {
            std::thread::sleep(Duration::from_secs(2));
            continue;
        };
        let (w, h) = img.dimensions();
        res.screen_wh = (w, h);

        // 上一次点击之后画面几乎没变: 这个位置拉黑, 不点第二次。画面明显变了 (关掉了一个
        // 盖在上面的框): 之前拉黑的位置作废 —— 10-11 Megacity 的「SINGLE PLAYER」先被提示框挡住、
        // 点了没反应被拉黑, 框关掉之后就再也不点它了
        if let Some((tx, ty, before)) = last_tap.take() {
            match mf(&before, &raw) {
                Some(f) if f < 0.02 => banned.push((tx, ty)),
                Some(f) if f >= 0.10 => banned.clear(),
                _ => {}
            }
        }

        let _ = img.save_with_format(&ocr_img, image::ImageFormat::Jpeg);
        let mut items = run_ocr(&ocr, &ocr_img);
        let fg_ok = fg == cfg.pkg;
        let uniform = near_uniform(&img);
        let mut dec = classify(&items, fg_ok, &fg, cfg.policy, &banned);
        // 认不出可点的东西时, 把对话框正文附近裁出来放大再认一遍 (小按钮整屏认会漏)
        let mut zoomed = 0;
        if fg_ok && !uniform && dec.act == Act::Probe {
            let extra = zoom_ocr(&ocr, &img, &items, &cfg.out.join("_zoom.jpg"));
            if !extra.is_empty() {
                zoomed = extra.len();
                items.extend(extra);
                dec = classify(&items, fg_ok, &fg, cfg.policy, &banned);
            }
        }
        if fg_ok && uniform && dec.act == Act::Probe {
            dec = Decision { screen: Screen::Loading, act: Act::Wait { why: "纯色画面 (黑屏过场)".into() } };
        }
        let shot = shots.join(format!("{n:02}_{}.jpg", serde_json::to_value(dec.screen).unwrap().as_str().unwrap_or("x")));
        save_jpg(&img, &shot, 960);

        let mut rec = json!({
            "n": n, "t_s": (t0.elapsed().as_secs_f64() * 10.0).round() / 10.0,
            "fg": fg, "screen": dec.screen, "act": dec.act,
            "ocr": items.iter().take(12).map(|i| i.t.clone()).collect::<Vec<_>>(),
            "zoom_items": zoomed,
            "shot": shot.file_name().map(|s| s.to_string_lossy().to_string()),
        });

        match dec.act.clone() {
            Act::Stop { reason } => {
                res.status = "NEEDS_HUMAN";
                res.reason = Some(reason);
                res.needs_human_shot = Some(shot.to_string_lossy().into());
                push_log(&mut log, &rec);
                res.steps.push(rec);
                break;
            }
            Act::Tap { x, y, .. } => {
                dev.tap(x, y);
                res.taps += 1;
                rec["at"] = json!([round6(x as f64 / w as f64), round6(y as f64 / h as f64)]);
                last_tap = Some((x, y, raw));
                unknown_streak = 0;
                push_log(&mut log, &rec);
                res.steps.push(rec);
                std::thread::sleep(Duration::from_secs(3));
            }
            Act::Wait { .. } => {
                unknown_streak = 0;
                push_log(&mut log, &rec);
                res.steps.push(rec);
                std::thread::sleep(Duration::from_secs(4));
            }
            Act::Relaunch => {
                relaunches += 1;
                push_log(&mut log, &rec);
                res.steps.push(rec);
                if relaunches > 3 {
                    res.reason = Some(format!("应用反复不在前台 (前台是 {fg}), 可能闪退"));
                    break;
                }
                launch(dev, &cfg.component);
                std::thread::sleep(Duration::from_secs(5));
            }
            Act::Probe => {
                let p = probe_ingame(dev, w, h);
                rec["probe"] = p.clone();
                let ingame = p["ingame"].as_bool().unwrap_or(false);
                push_log(&mut log, &rec);
                res.steps.push(rec);
                if ingame {
                    // 探针说在游戏里: 再等 8 s 让流式加载落定, 复测一次确认不是一闪而过
                    std::thread::sleep(Duration::from_secs(8));
                    let p2 = probe_ingame(dev, w, h);
                    if p2["ingame"].as_bool().unwrap_or(false) {
                        res.status = "INGAME";
                        res.probe = Some(json!({"first": p, "confirm": p2}));
                        break;
                    }
                }
                unknown_streak += 1;
                if unknown_streak >= 3 {
                    // 连续三次认不出也不在游戏里: 问一次大模型, 不行再试一次点屏幕中央, 再不行停
                    let mut acted = false;
                    if let Some(v) = vlm.as_mut() {
                        if let Some(ans) = v.ask(&shot, &items) {
                            let mut vrec = json!({"n": n, "vlm": ans.clone()});
                            let state = ans["state"].as_str().unwrap_or("");
                            if matches!(state, "login" | "update") {
                                vrec["vetted"] = json!("停: 模型认为是登录 / 更新画面");
                                push_log(&mut log, &vrec);
                                res.steps.push(vrec);
                                res.status = "NEEDS_HUMAN";
                                res.reason = Some(format!("大模型认为是 {state} 画面: {}", ans["why"].as_str().unwrap_or("")));
                                res.needs_human_shot = Some(shot.to_string_lossy().into());
                                break;
                            }
                            if let (Some(tx), Some(ty)) = (ans["tap"]["x"].as_f64(), ans["tap"]["y"].as_f64()) {
                                let x = (tx / 1000.0 * w as f64) as i32;
                                let y = (ty / 1000.0 * h as f64) as i32;
                                let label = ans["label"].as_str().unwrap_or("");
                                match vet_vlm_tap(label, x, y, &items) {
                                    Ok(()) if !banned.iter().any(|(bx, by)| (x - bx).abs() < 40 && (y - by).abs() < 40) => {
                                        let before = dev.cap();
                                        dev.tap(x, y);
                                        res.taps += 1;
                                        acted = true;
                                        vrec["vetted"] = json!("点了");
                                        vrec["at"] = json!([round6(x as f64 / w as f64), round6(y as f64 / h as f64)]);
                                        last_tap = Some((x, y, before));
                                    }
                                    Ok(()) => vrec["vetted"] = json!("拒: 这个位置点过没反应"),
                                    Err(e) => vrec["vetted"] = json!(format!("拒: {e}")),
                                }
                            }
                            push_log(&mut log, &vrec);
                            res.steps.push(vrec);
                        }
                    }
                    if !acted && !center_tapped {
                        center_tapped = true;
                        acted = true;
                        let (x, y) = ((w / 2) as i32, (h / 2) as i32);
                        dev.tap(x, y);
                        res.taps += 1;
                        let r = json!({"n": n, "fallback": "点屏幕中央一次 (无字的「点击继续」画面)", "at": [0.5, 0.5]});
                        push_log(&mut log, &r);
                        res.steps.push(r);
                    }
                    if !acted {
                        res.status = "NEEDS_HUMAN";
                        res.reason = Some("卡在认不出的画面: OCR 没有可点的字, 拖视角也不转".into());
                        res.needs_human_shot = Some(shot.to_string_lossy().into());
                        break;
                    }
                    unknown_streak = 0;
                    std::thread::sleep(Duration::from_secs(3));
                }
            }
        }
    }
    if res.status == "FAILED" && res.reason.is_none() {
        res.reason = Some(format!("{} 步内没进到游戏里", cfg.max_steps));
    }
    res.seconds = (t0.elapsed().as_secs_f64() * 10.0).round() / 10.0;
    res.vlm_calls = vlm.map(|v| v.used).unwrap_or(0);
    let _ = std::fs::remove_file(&ocr_img);
    res
}

fn push_log(f: &mut Option<std::fs::File>, v: &Value) {
    use std::io::Write;
    if let Some(f) = f.as_mut() {
        let _ = writeln!(f, "{v}");
    }
}

// ══════════════ 负载校准 ══════════════

/// 用第一对拖动校准: 拖动中 2 s 与 6 s 各抓一帧 (画面在转), 拖回来后再抓一帧 (回没回原处)。
fn spin_check(dev: &Dev, g: SwipeGeom, w: u32, h: u32) -> Value {
    let (x0, x1, y) = px(g, w, h);
    let pre = dev.cap();
    let child = dev.swipe_bg(x0, y, x1, y, g.ms);
    std::thread::sleep(Duration::from_secs(2));
    let a = dev.cap();
    std::thread::sleep(Duration::from_secs(4));
    let b = dev.cap();
    wait_child(child, g.ms / 1000 + 10);
    dev.swipe(x1, y, x0, y, g.ms);
    std::thread::sleep(Duration::from_millis(800));
    let post = dev.cap();
    let moved = mf(&a, &b);
    json!({
        "geom": g, "px": [x0, x1, y],
        "moved_fraction": moved, "return_fraction": mf(&pre, &post),
        "spinning": moved.is_some_and(|m| m >= SPIN_MOVED_MIN),
    })
}

// ══════════════ 渲染线程 / 图形接口 ══════════════

fn discover_render(dev: &Dev, pkg: &str, g: SwipeGeom, w: u32, h: u32, out: &Path) -> Value {
    let Some(pid) = dev.pid(pkg) else {
        return json!({"error": "pidof 拿不到进程号"});
    };
    let tid_txt = dev.su(&format!("ls /proc/{pid}/task"), 10);
    let tids: Vec<i64> = tid_txt.split_whitespace().filter_map(|t| t.parse().ok()).collect();
    let (x0, x1, y) = px(g, w, h);
    let child = dev.swipe_bg(x0, y, x1, y, 6000);
    let path = format!("{DEV_TMP}/onboard_comm.txt");
    dev.su(&format!("sh {DEV_TMP}/ftrace_capture.sh 4 {path} {pkg}"), 60);
    wait_child(child, 20);
    dev.swipe(x1, y, x0, y, 6000);
    let local = out.join("comm_trace.txt");
    let lp = local.to_string_lossy().to_string();
    adb(&dev.serial, &["pull", path.as_str(), lp.as_str()], 60);
    dev.su(&format!("rm -f {path}"), 10);
    let text = std::fs::read_to_string(&local).unwrap_or_default();
    let (comm, share, mine, all) = pick_render_comm(&text, &tids);
    let _ = std::fs::remove_file(&local);

    // 图形接口: 看进程映射里装了哪家驱动。两个都在时如实写 both, 不猜。
    let maps = dev.su(
        &format!("grep -cE \"vulkan[.]adreno|libvulkan_adreno\" /proc/{pid}/maps; grep -cE \"libGLESv2_adreno\" /proc/{pid}/maps"),
        10,
    );
    let nums: Vec<i64> = maps.split_whitespace().filter_map(|t| t.parse().ok()).collect();
    let (vk, gl) = (nums.first().copied().unwrap_or(0), nums.get(1).copied().unwrap_or(0));
    let api = match (vk > 0, gl > 0) {
        (true, false) => "vulkan",
        (false, true) => "gles",
        (true, true) => "both_loaded",
        _ => "unknown",
    };
    json!({
        "pid": pid, "n_threads": tids.len(), "render_comm": comm, "share": round6(share),
        "pkg_submits": mine, "all_submits": all,
        "graphics_api": api, "maps_vulkan_icd": vk, "maps_gles_driver": gl,
    })
}

// ══════════════ 实测 ══════════════

struct ChargeGuard<'a> {
    dev: &'a Dev,
    active: bool,
    log: Vec<String>,
}

impl ChargeGuard<'_> {
    fn restore(&mut self) {
        if self.active {
            let o = self.dev.su(&format!("sh {DEV_TMP}/charge_suspend.sh restore"), 60);
            self.log.push(o.trim().lines().last().unwrap_or("").to_string());
            self.active = false;
        }
    }
}

impl Drop for ChargeGuard<'_> {
    fn drop(&mut self) {
        self.restore();
    }
}

fn f(v: &Value, k: &str) -> Option<f64> {
    v.get(k).and_then(Value::as_f64)
}

#[derive(Clone, Copy)]
struct MeasureCfg<'a> {
    pkg: &'a str,
    workload: &'a Path,
    cap_s: u64,
    rounds: usize,
    comm: Option<&'a str>,
    out: &'a Path,
    suspend: bool,
}

/// charge_suspend.sh suspend 的输出里有没有「真转成放电态」: CHARGE_SUSPENDED (写节点后判成放电)
/// 或 CHARGE_ALREADY_ON_BATTERY。CHARGE_FAIL / CHARGE_SKIP 都算没停住 —— 档案里不能写成停了。
pub fn suspend_took(out: &str) -> bool {
    out.lines().any(|l| l.starts_with("CHARGE_SUSPENDED") || l.starts_with("CHARGE_ALREADY_ON_BATTERY") || l.starts_with("CHARGE_ALREADY_SUSPENDED"))
}

fn measure(dev: &Dev, m: &MeasureCfg<'_>) -> (Vec<RoundStat>, Vec<Value>, bool, Value) {
    let MeasureCfg { pkg, workload, cap_s, rounds, comm, out, suspend } = *m;
    let mut stats = Vec::new();
    let mut raw = Vec::new();
    let mut over = false;
    let mut guard = ChargeGuard { dev, active: false, log: vec![] };
    let mut suspended = false;
    if suspend {
        let o = dev.su(&format!("sh {DEV_TMP}/charge_suspend.sh suspend"), 90);
        suspended = suspend_took(&o);
        // 没生效时脚本已就地还原; 这里仍让守卫在收尾跑一次 restore (无状态文件时是空操作)
        guard.active = true;
        guard.log.extend(o.trim().lines().map(str::to_string));
    }
    let short = pkg.rsplit('.').next().unwrap_or("game");
    for i in 0..rounds {
        let pre = dev.cap();
        let label = format!("onboard_{short}_r{i}");
        let dir = out.join("rounds").join(format!("r{i}"));
        let r = run_one_with(&dev.serial, &label, &dir, cap_s, workload, pkg, comm, None);
        std::thread::sleep(Duration::from_millis(1500));
        let post = dev.cap();
        // 原始 trace 压缩留档 (判据可离线重放, 但一轮十几 MB, 不压磁盘吃不消)
        gzip_in_place(&dir.join("trace.txt"));
        match r {
            Ok(m) => {
                let s = &m["summary"];
                let e = &m["env"];
                let st = RoundStat {
                    frame_p95: f(s, "frame_p95"),
                    frame_p50: f(s, "frame_p50"),
                    fps_mean: f(s, "fps_mean"),
                    n_frames: s["n_frames"].as_i64().unwrap_or(0),
                    power_w: f(e, "power_w_mean"),
                    soc_temp_max_c: f(e, "soc_temp_max_c"),
                    return_fraction: mf(&pre, &post),
                };
                println!(
                    "  轮 {i}: p95={:?} ms  fps={:?}  帧数={}  功耗={:?} W  SoC峰值={:?} °C  回位变化={:?}",
                    st.frame_p95, st.fps_mean, st.n_frames, st.power_w, st.soc_temp_max_c, st.return_fraction
                );
                let hot = st.soc_temp_max_c.is_some_and(|t| t > TEMP_CAP_C);
                raw.push(json!({"round": i, "summary": s, "env": e, "return_fraction": st.return_fraction}));
                stats.push(st);
                if hot {
                    over = true;
                    println!("  SoC 超过 {TEMP_CAP_C} °C, 停止实测");
                    break;
                }
            }
            Err(e) => {
                println!("  轮 {i}: 失败 {e}");
                raw.push(json!({"round": i, "error": e}));
                stats.push(RoundStat::default());
            }
        }
        std::thread::sleep(Duration::from_secs(5));
    }
    guard.restore();
    let charging = dev.su("cat /sys/class/qcom-battery/charging_enabled", 10).trim().to_string();
    let charge = json!({"requested": suspend, "suspended": suspended, "log": guard.log.clone(), "charging_enabled_after": charging});
    (stats, raw, over, charge)
}

fn gzip_in_place(p: &Path) {
    use std::io::Write;
    let Ok(data) = std::fs::read(p) else { return };
    let gz = p.with_extension("txt.gz");
    if let Ok(file) = std::fs::File::create(&gz) {
        let mut enc = flate2::write::GzEncoder::new(file, flate2::Compression::default());
        if enc.write_all(&data).is_ok() && enc.finish().is_ok() {
            let _ = std::fs::remove_file(p);
        }
    }
}

// ══════════════ 档案 ══════════════

fn pitfalls(enter: &EnterResult, spin: &Value, rep: &Repeatability, render: &Value) -> Vec<Value> {
    let mut v = Vec::new();
    let count = |rule: &str| {
        enter.steps.iter().filter(|s| s["act"]["rule"].as_str().is_some_and(|r| r.starts_with(rule))).count()
    };
    let n_dismiss = count("dismiss");
    if n_dismiss > 0 {
        v.push(json!({"id": "startup_popups", "symptom": format!("冷启动有公告 / 提示弹窗 (本次关了 {n_dismiss} 个)"),
                      "workaround": "进门流程按白名单自动关闭; 测量前必须确认已在游戏里 (onboard --enter-only)"}));
    }
    if count("consent") > 0 {
        v.push(json!({"id": "consent_clicked", "symptom": "首次启动要同意隐私协议, 本次已自动同意",
                      "workaround": "记在 entry.steps; 不想自动同意时用 --no-consent"}));
    }
    if count("guest") > 0 {
        v.push(json!({"id": "guest_login", "symptom": "本次通过游客入口进入, 游客数据可能随清数据 / 换机丢失",
                      "workaround": "不要清应用数据; 游客账号的进度不当作测试条件的一部分"}));
    }
    if let Some(p) = enter.probe.as_ref().and_then(|p| p["confirm"]["idle"].as_f64()) {
        if p >= 0.05 {
            v.push(json!({"id": "dynamic_idle_scene", "symptom": format!("无输入时画面也在变 ({:.0}% 像素): NPC / 天气 / 粒子", p * 100.0),
                          "workaround": "离散度会偏大; A/B 必须交替, 判定靠置换检验而不是单轮对比"}));
        }
    }
    if spin["return_fraction"].as_f64().is_some_and(|r| r >= 0.5) {
        v.push(json!({"id": "camera_not_returning", "symptom": "右拖再左拖之后视角回不到原处 (相机惯性或自动回正)",
                      "workaround": "每轮起点略有不同; 以多轮 frame_p95 离散度为准"}));
    }
    if rep.monotone_p95 {
        v.push(json!({"id": "monotone_drift", "symptom": "frame_p95 逐轮单调漂移 (温度 / 昼夜 / 场景随时间变)",
                      "workaround": "A/B 交替跑; 原神这类有昼夜的游戏选光照稳定的时段"}));
    }
    if let Some(c) = render["render_comm"].as_str() {
        if c != crate::looptrace::DEFAULT_RENDER_COMM {
            v.push(json!({"id": "render_thread_not_unity", "symptom": format!("渲染线程是 {c}, 不是 Unity 缺省的 UnityGfxDeviceW"),
                          "workaround": "评测请求的 workload.render_comm 要带上它, 否则解析出零帧"}));
        }
    }
    v.push(json!({"id": "ingame_settings_unread", "symptom": "游戏内帧率 / 画质设置工具读不到, 改了它基线就作废",
                  "workaround": "conditions.ingame_fps_setting 由人填; 换设置后重跑 onboard"}));
    v
}

fn mean(xs: &[f64]) -> Option<f64> {
    (!xs.is_empty()).then(|| round6(xs.iter().sum::<f64>() / xs.len() as f64))
}

// ══════════════ 给评测用的进门 ══════════════

/// 评测请求带 `workload.enter == "onboard"` 时用: 每臂重启游戏之后, 用同一套白名单状态机
/// 把游戏带回游戏场景 (不冷启动、不问大模型)。进不去就返回原因, 调用方据此作废这一轮。
pub fn enter_scene(serial: &str, pkg: &str, out: &Path) -> Result<Value, String> {
    let dev = Dev { serial: serial.to_string() };
    let comp_txt = dev.sh(
        &format!("cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER {pkg}"),
        10,
    );
    let component = parse_component(&comp_txt, pkg).ok_or(format!("{pkg} 没有可启动的 Activity"))?;
    std::fs::create_dir_all(out).map_err(|e| e.to_string())?;
    let r = enter(
        &dev,
        &EnterCfg {
            pkg: pkg.to_string(),
            component,
            cold: false,
            policy: Policy::default(),
            max_steps: 40,
            timeout_s: 300,
            out: out.to_path_buf(),
            vlm_calls: 0,
        },
    );
    let v = serde_json::to_value(&r).unwrap_or(Value::Null);
    std::fs::write(out.join("enter.json"), serde_json::to_string_pretty(&v).unwrap_or_default()).ok();
    if r.status == "INGAME" {
        Ok(json!({"status": r.status, "taps": r.taps, "seconds": r.seconds}))
    } else {
        Err(format!("{}: {}", r.status, r.reason.unwrap_or_default()))
    }
}

// ══════════════ CLI ══════════════

const USAGE: &str = "用法: phonefarm onboard --package <包名> [--serial S] [--out 目录]
        [--rounds 5] [--seconds 30] [--max-steps 60] [--timeout 600]
        [--enter-only] [--no-cold] [--no-guest] [--no-consent] [--no-charge-suspend]
        [--vlm-calls 4] [--keep-foreground] [--ocr <ocr 可执行>]
  完整接入: 冷启动 → 进门 → 进游戏探针 → 生成负载 → 校准 → 渲染线程 → N 轮实测 → profile.json
  --enter-only: 只把游戏带进游戏场景 (不冷启动, 不测), 退出码 0=在游戏里 3=需要人 1=失败";

struct Args {
    pkg: String,
    serial: Option<String>,
    out: Option<PathBuf>,
    rounds: usize,
    seconds: u64,
    max_steps: usize,
    timeout: u64,
    enter_only: bool,
    cold: bool,
    policy: Policy,
    suspend: bool,
    vlm_calls: u32,
    keep_fg: bool,
}

fn parse_args(a: &[String]) -> Result<Args, String> {
    let mut r = Args {
        pkg: String::new(),
        serial: None,
        out: None,
        rounds: 5,
        seconds: 30,
        max_steps: 60,
        timeout: 600,
        enter_only: false,
        cold: true,
        policy: Policy::default(),
        suspend: true,
        vlm_calls: 4,
        keep_fg: false,
    };
    let mut it = a.iter();
    let num = |v: Option<&String>, k: &str| -> Result<u64, String> {
        v.and_then(|s| s.parse().ok()).ok_or(format!("{k} 要一个正整数"))
    };
    while let Some(x) = it.next() {
        match x.as_str() {
            "--package" | "--pkg" => r.pkg = it.next().cloned().unwrap_or_default(),
            "--serial" => r.serial = it.next().cloned(),
            "--out" => r.out = it.next().map(PathBuf::from),
            "--rounds" => r.rounds = num(it.next(), "--rounds")? as usize,
            "--seconds" => r.seconds = num(it.next(), "--seconds")?,
            "--max-steps" => r.max_steps = num(it.next(), "--max-steps")? as usize,
            "--timeout" => r.timeout = num(it.next(), "--timeout")?,
            "--vlm-calls" => r.vlm_calls = num(it.next(), "--vlm-calls")? as u32,
            "--enter-only" => r.enter_only = true,
            "--no-cold" => r.cold = false,
            "--no-guest" => r.policy.allow_guest = false,
            "--no-consent" => r.policy.allow_consent = false,
            "--no-charge-suspend" => r.suspend = false,
            "--keep-foreground" => r.keep_fg = true,
            "--ocr" => {
                let p = it.next().ok_or("--ocr 要一个路径")?;
                std::env::set_var("PF_OCR_BIN", p);
            }
            "--json" => {}
            o => return Err(format!("不认识的参数 {o}\n{USAGE}")),
        }
    }
    if r.pkg.is_empty() {
        return Err(format!("缺 --package\n{USAGE}"));
    }
    if r.enter_only {
        r.cold = false;
    }
    Ok(r)
}

fn onboard_hdc_guidance(pkg: &str) -> String {
    format!("onboard 目前只支持 Android；鸿蒙请用 aa start -b {pkg} -a <mainAbility> 启动，mainAbility 从 bm dump -n {pkg} 读")
}

pub fn run_onboard(args: &[String]) -> i32 {
    let a = match parse_args(args) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("{e}");
            return 2;
        }
    };
    let Some(serial) = crate::device::resolve_serial(a.serial.as_deref()) else {
        eprintln!("没有唯一在线的设备, 用 --serial 指定");
        return 2;
    };
    if serial.starts_with("hdc:") {
        eprintln!("{}", onboard_hdc_guidance(&a.pkg));
        return 2;
    }
    let dev = Dev { serial: serial.clone() };
    let ts = chrono::Local::now().format("%Y%m%d-%H%M%S").to_string();
    let out = a.out.clone().unwrap_or_else(|| {
        repo_root().join("tasks").join(format!("onboard_{}", a.pkg)).join(&ts)
    });
    if let Err(e) = std::fs::create_dir_all(&out) {
        eprintln!("建不了输出目录 {}: {e}", out.display());
        return 1;
    }

    // ── 0. 包在不在, 启动组件 ──
    let path = dev.sh(&format!("pm path {}", a.pkg), 10);
    if !path.contains("package:") {
        eprintln!("设备上没装 {}", a.pkg);
        return 1;
    }
    let comp_txt = dev.sh(
        &format!("cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER {}", a.pkg),
        10,
    );
    let Some(component) = parse_component(&comp_txt, &a.pkg) else {
        eprintln!("{} 没有可启动的 Activity: {comp_txt}", a.pkg);
        return 1;
    };
    let version = dev
        .sh(&format!("dumpsys package {} | grep -m1 versionName", a.pkg), 15)
        .trim()
        .trim_start_matches("versionName=")
        .to_string();
    println!("目标 {} ({version}) 组件 {component} 设备 {serial}", a.pkg);

    // ── 1. 进门 ──
    println!("[1] 进门 ({})...", if a.cold { "冷启动" } else { "不杀进程, 拉到前台" });
    let ecfg = EnterCfg {
        pkg: a.pkg.clone(),
        component: component.clone(),
        cold: a.cold,
        policy: a.policy,
        max_steps: a.max_steps,
        timeout_s: a.timeout,
        out: out.clone(),
        vlm_calls: a.vlm_calls,
    };
    let entered = enter(&dev, &ecfg);
    for s in &entered.steps {
        println!("  {}", s);
    }
    println!(
        "  → {} ({} 步, {} 次点击, {} s){}",
        entered.status,
        entered.steps.len(),
        entered.taps,
        entered.seconds,
        entered.reason.as_ref().map(|r| format!(": {r}")).unwrap_or_default()
    );
    let enter_json = serde_json::to_value(&entered).unwrap_or(Value::Null);
    std::fs::write(out.join("enter.json"), serde_json::to_string_pretty(&enter_json).unwrap()).ok();

    if a.enter_only {
        let mute = ensure_muted(&dev);
        println!("{}", json!({"status": entered.status, "reason": entered.reason, "out": out, "mute": mute}));
        return match entered.status {
            "INGAME" => 0,
            "NEEDS_HUMAN" => 3,
            _ => 1,
        };
    }

    let (w, h) = entered.screen_wh;
    let mut needs_human: Vec<Value> = Vec::new();
    let mut spin = Value::Null;
    let mut render = Value::Null;
    let mut geom = GEOMS[0];
    let mut rep: Option<Repeatability> = None;
    let mut rounds_raw: Vec<Value> = Vec::new();
    let mut stats: Vec<RoundStat> = Vec::new();
    let mut charge = Value::Null;
    let mut env_conditions = Value::Null;
    let wl_path = out.join("workload.json");

    if entered.status != "INGAME" {
        needs_human.push(json!({"stage": "enter", "reason": entered.reason, "screenshot": entered.needs_human_shot}));
    } else {
        // ── 2. 负载校准 ──
        println!("[2] 负载校准 (拖动中两帧变化 ≥ {:.0}%)...", SPIN_MOVED_MIN * 100.0);
        let mut tries = Vec::new();
        for g in GEOMS {
            let s = spin_check(&dev, g, w, h);
            println!("  {s}");
            let ok = s["spinning"].as_bool().unwrap_or(false);
            tries.push(s.clone());
            if ok {
                geom = g;
                spin = s;
                break;
            }
        }
        if spin.is_null() {
            spin = json!({"spinning": false, "tries": tries});
        } else {
            spin["tries"] = json!(tries);
        }
        let spin_ok = spin["spinning"].as_bool().unwrap_or(false);
        let pairs = pairs_for(a.seconds, geom);
        let wl = workload_json(geom, w, h, pairs);
        std::fs::write(&wl_path, serde_json::to_string_pretty(&wl).unwrap()).ok();

        if !spin_ok {
            needs_human.push(json!({"stage": "workload", "reason": "三组拖动几何都没让画面转起来: 这个游戏可能不是拖屏幕转视角"}));
        } else {
            // ── 3. 渲染线程 ──
            println!("[3] 找渲染线程...");
            if let Err(e) = push_tools(&serial) {
                eprintln!("  推设备端脚本失败: {e}");
                return 1;
            }
            render = discover_render(&dev, &a.pkg, geom, w, h, &out);
            println!("  {render}");
            let comm = render["render_comm"].as_str().map(str::to_string);
            if comm.is_none() {
                needs_human.push(json!({"stage": "render_thread", "reason": "ftrace 里没有这个包发出的 GPU 提交"}));
            } else {
                // ── 4. 实测 ──
                println!("[4] {} 轮实测 (每轮采 {} s, 停充={})...", a.rounds, a.seconds, a.suspend);
                let mcfg = MeasureCfg {
                    pkg: &a.pkg,
                    workload: &wl_path,
                    cap_s: a.seconds,
                    rounds: a.rounds,
                    comm: comm.as_deref(),
                    out: &out,
                    suspend: a.suspend,
                };
                let (st, raw, over, ch) = measure(&dev, &mcfg);
                let r = judge(&st, spin_ok, over);
                println!("  可重复判定: {} {:?}", r.verdict, r.reasons);
                env_conditions = raw
                    .iter()
                    .rev()
                    .find_map(|x| x.get("env").cloned())
                    .unwrap_or(Value::Null);
                stats = st;
                rounds_raw = raw;
                charge = ch;
                rep = Some(r);
            }
        }
    }

    // ── 5. 档案 ──
    let rep_v = rep.clone().unwrap_or_else(|| judge(&[], false, false));
    let col = |f: fn(&RoundStat) -> Option<f64>| -> Vec<f64> { stats.iter().filter_map(f).collect() };
    let fps = col(|r| r.fps_mean);
    let p50 = col(|r| r.frame_p50);
    let p95 = col(|r| r.frame_p95);
    let pw = col(|r| r.power_w);
    let fps_cap = mean(&p50).and_then(crate::looptrace::detect_fps_cap);
    let entry_steps: Vec<Value> = entered
        .steps
        .iter()
        .filter(|s| s["act"]["do"] == "tap" || s.get("fallback").is_some() || s["vetted"] == "点了")
        .map(|s| {
            json!({"screen": s["screen"], "label": s["act"]["label"], "rule": s["act"]["rule"],
                   "at": s["at"], "via": if s.get("vlm").is_some() { "vlm" } else if s.get("fallback").is_some() { "center_fallback" } else { "ocr_rule" }})
        })
        .collect();
    let status = if !needs_human.is_empty() {
        "NEEDS_HUMAN"
    } else if rep_v.verdict == "PASS" {
        "PASS"
    } else {
        "FAIL"
    };
    let mut profile = json!({
        "profile_version": 1,
        "status": status,
        "package": a.pkg,
        "version_name": version,
        "created_at": chrono::Local::now().to_rfc3339(),
        "device": {"serial": serial, "screen": [w, h]},
        "launch": {"component": component, "cold_start": a.cold},
        "entry": {
            "policy": a.policy,
            "replay": "policy",
            "replay_note": "复现时重跑同一套白名单状态机 (phonefarm onboard --enter-only), 不盲放点击坐标: 公告每天不一样",
            "status": entered.status,
            "seconds": entered.seconds,
            "taps": entered.taps,
            "steps": entry_steps,
            "ingame_probe": entered.probe,
            "vlm_calls": entered.vlm_calls,
        },
        "workload": {
            "script": "workload.json",
            "seconds": a.seconds,
            "input": "touch",
            "kind": "camera_swipe_pairs",
            "geom": geom,
            "spin_check": spin,
            "render_comm": render["render_comm"],
            "graphics_api": render["graphics_api"],
            "render_discovery": render,
        },
        "criteria": {
            "frame_p95_cv_max": REPEAT_P95_CV_MAX,
            "min_valid_rounds": MIN_VALID_ROUNDS,
            "min_frames_per_round": MIN_FRAMES_PER_ROUND,
            "spin_moved_min": SPIN_MOVED_MIN,
            "ingame_return_ratio_max": RETURN_RATIO_MAX,
            "temp_cap_c": TEMP_CAP_C,
        },
        "repeatability": {
            "rounds": stats.len(),
            "per_round": stats,
            "judge": rep_v,
        },
        "baseline": {
            "fps_mean": mean(&fps),
            "frame_p50_ms": mean(&p50),
            "frame_p95_ms": mean(&p95),
            "power_w": mean(&pw),
            "fps_cap": fps_cap,
        },
        "conditions": {
            "charging": charge,
            "fan_state": env_conditions.get("fan_state"),
            "power_source": env_conditions.get("power_source"),
            "battery_capacity_pct": env_conditions.get("battery_capacity_pct"),
            "ingame_fps_setting": Value::Null,
            "ingame_fps_setting_note": "工具读不到, 由人填; 改了它这份基线作废",
            "local_time": chrono::Local::now().format("%H:%M").to_string(),
        },
        "pitfalls": pitfalls(&entered, &spin, &rep_v, &render),
        "needs_human": needs_human,
        "human_interventions": 0,
        "evidence": {"dir": out, "rounds": rounds_raw},
    });
    if charge["requested"].as_bool() == Some(true) && charge["suspended"].as_bool() != Some(true) {
        if let Some(pf) = profile["pitfalls"].as_array_mut() {
            pf.push(json!({"id": "charge_not_suspended",
                "symptom": "停充没生效: 写过的候选节点都没让电池状态离开 Charging, 功耗按规矩记 null",
                "workaround": "看 conditions.charging.log 与各轮 env.txt; 停充修好前别拿这台机的功耗做判定"}));
        }
    }
    if !a.keep_fg {
        dev.sh("input keyevent 3", 5); // 回桌面
    }
    let mute = ensure_muted(&dev);
    println!("  静音核对: {mute}");
    profile["conditions"]["mute"] = mute;
    let pp = out.join("profile.json");
    std::fs::write(&pp, serde_json::to_string_pretty(&profile).unwrap()).ok();
    println!("档案: {}  状态: {status}", pp.display());
    match status {
        "PASS" => 0,
        "NEEDS_HUMAN" => 3,
        _ => 4,
    }
}

// ══════════════ 测试 ══════════════

#[cfg(test)]
mod tests {
    use super::*;

    fn it(t: &str, x: i32, y: i32) -> OcrItem {
        OcrItem { t: t.into(), b: [x - 20, y - 10, x + 20, y + 10] }
    }
    fn cls(items: &[OcrItem]) -> Decision {
        classify(items, true, "com.x.game", Policy::default(), &[])
    }
    fn tapped(d: &Decision) -> Option<&str> {
        match &d.act {
            Act::Tap { label, .. } => Some(label),
            _ => None,
        }
    }

    #[test]
    fn genshin_like_door_taps_tap_to_enter_not_logout() {
        let d = cls(&[it("退出", 2500, 80), it("点击进入", 1340, 1000), it("UID: 173165141", 2500, 1180)]);
        assert_eq!(d.screen, Screen::Start);
        assert_eq!(tapped(&d), Some("点击进入"));
    }

    #[test]
    fn in_game_hud_text_leads_to_probe() {
        let d = cls(&[
            it("等待到两天后(8:00-12:00)", 300, 270),
            it("烟绯", 1750, 190),
            it("Lv.80", 800, 830),
            it("20340 / 26755", 1000, 830),
            it("UID: 173165141", 1800, 890),
            it("44 ms", 1860, 95),
        ]);
        assert_eq!(d.act, Act::Probe);
    }

    #[test]
    fn update_prompt_stops_but_progress_waits() {
        let d = cls(&[it("检测到新版本, 需要下载 1.2GB 资源", 1300, 500), it("确定", 1300, 800), it("取消", 1000, 800)]);
        assert_eq!(d.screen, Screen::Update, "带大小的提示文案不是进度");
        let d = cls(&[it("发现新版本, 是否更新?", 1300, 500), it("确定", 1300, 800)]);
        assert_eq!(d.screen, Screen::Update);
        assert!(matches!(d.act, Act::Stop { .. }));
        let d = cls(&[it("正在下载资源 45%", 1300, 1000)]);
        assert_eq!(d.screen, Screen::Loading);
        let d = cls(&[it("下载中 120.5MB/1.2GB", 1300, 1000)]);
        assert_eq!(d.screen, Screen::Loading);
        assert!(!is_progress("需要下载 1.2GB"));
    }

    #[test]
    fn login_form_stops_and_guest_is_taken_when_allowed() {
        let form = [it("请输入手机号", 1300, 400), it("验证码", 1300, 500), it("登录", 1300, 700)];
        assert!(matches!(cls(&form).act, Act::Stop { .. }));
        let mut with_guest = form.to_vec();
        with_guest.push(it("游客登录", 1300, 900));
        let d = cls(&with_guest);
        assert_eq!(tapped(&d), Some("游客登录"));
        let no_guest = classify(&with_guest, true, "p", Policy { allow_guest: false, allow_consent: true }, &[]);
        assert!(matches!(no_guest.act, Act::Stop { .. }));
    }

    #[test]
    fn third_party_login_buttons_are_never_tapped() {
        let d = cls(&[it("Continue with Google", 1300, 600), it("Continue with Facebook", 1300, 700)]);
        assert!(tapped(&d).is_none());
    }

    #[test]
    fn realname_always_stops() {
        assert_eq!(cls(&[it("请完成实名认证", 1300, 500), it("确定", 1300, 800)]).screen, Screen::RealName);
    }

    #[test]
    fn consent_taps_agree_never_disagree() {
        let d = cls(&[it("用户协议与隐私政策", 1300, 300), it("不同意", 1000, 900), it("同意", 1600, 900)]);
        assert_eq!(tapped(&d), Some("同意"));
        let d = classify(
            &[it("Privacy Policy", 1300, 300), it("Decline", 1000, 900), it("Accept", 1600, 900)],
            true, "p", Policy::default(), &[],
        );
        assert_eq!(tapped(&d), Some("Accept"));
    }

    #[test]
    fn permission_dialog_is_denied() {
        let d = classify(
            &[it("允许“原神”发送通知?", 1300, 400), it("允许", 1300, 700), it("不允许", 1300, 800)],
            false, "com.android.permissioncontroller", Policy::default(), &[],
        );
        assert_eq!(tapped(&d), Some("不允许"));
    }

    #[test]
    fn popup_close_wins_over_start_behind_it() {
        let d = cls(&[it("活动公告", 1300, 200), it("关闭", 2300, 150), it("点击进入", 1340, 1000)]);
        assert_eq!(tapped(&d), Some("关闭"));
    }

    #[test]
    fn weak_ok_is_not_tapped_on_a_pay_screen() {
        let d = cls(&[it("首充礼包 ¥6", 1300, 400), it("确定", 1300, 800)]);
        assert!(tapped(&d).is_none());
        let d = cls(&[it("今日活动已开启", 1300, 400), it("确定", 1300, 800)]);
        assert_eq!(tapped(&d), Some("确定"));
    }

    #[test]
    fn menu_prefers_single_player_and_skips_settings_and_quit() {
        let d = cls(&[it("SETTINGS", 400, 700), it("QUIT", 400, 800), it("MULTIPLAYER", 400, 600), it("SINGLE PLAYER", 400, 500)]);
        assert_eq!(tapped(&d), Some("SINGLE PLAYER"));
        let d = cls(&[it("Player 1", 400, 500)]);
        assert!(tapped(&d).is_none(), "HUD 上的 Player 1 不是开始按钮");
        let d = cls(&[it("PLAY", 1300, 900), it("New Game", 1300, 700)]);
        assert_eq!(tapped(&d), Some("PLAY"));
    }

    #[test]
    fn long_sentence_with_start_word_is_not_a_button() {
        let d = cls(&[it("活动将于明日开始, 敬请期待更多精彩内容", 1300, 500)]);
        assert!(tapped(&d).is_none());
    }

    #[test]
    fn banned_positions_are_not_tapped_twice() {
        let items = [it("点击进入", 1340, 1000)];
        let d = classify(&items, true, "p", Policy::default(), &[(1340, 1000)]);
        assert!(tapped(&d).is_none());
    }

    #[test]
    fn not_foreground_relaunches() {
        assert_eq!(classify(&[], false, "com.android.launcher3", Policy::default(), &[]).act, Act::Relaunch);
    }

    #[test]
    fn suspend_counts_only_when_the_device_really_went_on_battery() {
        // 10-11 手机节点上原神那次: 所有候选节点都试过, 仍是 Charging —— 不能记成「停了」
        let fail = "CHARGE_TRY_FAILED /sys/class/qcom-battery/charging_enabled (写 0 无效)\n\
CHARGE_FAIL: 所有候选节点都没能把设备转成放电态\n\
CHARGE_STATE status=Charging current_now=-632000 capacity=100 on_battery=no";
        assert!(!suspend_took(fail));
        assert!(!suspend_took("CHARGE_SKIP: 电量 20% 低于停充下限 30%, 不停充"));
        assert!(suspend_took("CHARGE_SUSPENDED /sys/class/qcom-battery/charging_enabled: 1 -> 0\nCHARGE_STATE status=Discharging current_now=-640000 capacity=100 on_battery=yes"));
        assert!(suspend_took("CHARGE_ALREADY_ON_BATTERY (本来就在放电态, 不需要停充)"));
    }

    #[test]
    fn download_in_progress_waits_instead_of_stopping() {
        // 10-11 PUBG Mobile 首次启动: 自动下载资源, 没有要确认的按钮
        let items = vec![
            it("支持后台下载，下载过程中请勿关闭游戏。", 1300, 50),
            it("开启推送权限", 2500, 120),
            it("正在下载", 200, 950),
            it("（65.7MB/134.4MB", 300, 1000),
            it("最小化后仍可在后台下载（下载试听组件资源包后可获取更好的视觉体验）", 800, 1060),
        ];
        let d = classify(&items, true, "com.tencent.ig", Policy::default(), &[]);
        assert!(matches!(d.act, Act::Wait { .. }), "{d:?}");
        // 真的有确认按钮时仍然停
        let mut ask = items.clone();
        ask.push(it("立即下载", 1300, 900));
        let d = classify(&ask, true, "com.tencent.ig", Policy::default(), &[]);
        assert!(matches!(d.act, Act::Stop { .. }), "{d:?}");
    }

    #[test]
    fn login_page_without_guest_opens_more_once_never_third_party() {
        // 10-11 PUBG Mobile 登录页 (OCR 原样, 含错字)
        let items = vec![
            it("X Lagin with X", 840, 930),
            it("f Login with Facebook", 1270, 930),
            it("Phone/Email", 1690, 930),
            it("Mare", 1970, 930),
            it("More", 1970, 930),
            it("帮助", 2600, 120),
            it("语言", 2600, 260),
        ];
        let d = classify(&items, true, "com.tencent.ig", Policy::default(), &[]);
        match &d.act {
            Act::Tap { label, .. } => assert_eq!(label.to_lowercase(), "more", "{d:?}"),
            _ => panic!("应该展开 More: {d:?}"),
        }
        // More 点过没反应被拉黑之后: 停, 不碰第三方登录
        let d = classify(&items, true, "com.tencent.ig", Policy::default(), &[(1970, 930)]);
        assert!(matches!(d.act, Act::Stop { .. }), "{d:?}");
        // 展开后出现游客: 点游客
        let mut g = items.clone();
        g.push(it("Guest", 1970, 700));
        let d = classify(&g, true, "com.tencent.ig", Policy::default(), &[(1970, 930)]);
        match &d.act {
            Act::Tap { label, .. } => assert_eq!(label.to_lowercase(), "guest", "{d:?}"),
            _ => panic!("应该点游客: {d:?}"),
        }
        // 不允许游客时连 More 都不点
        let d = classify(&items, true, "com.tencent.ig", Policy { allow_guest: false, ..Policy::default() }, &[]);
        assert!(matches!(d.act, Act::Stop { .. }), "{d:?}");
    }

    #[test]
    fn zoom_finds_the_dialog_body_not_the_menu() {
        // 10-11 Megacity 冷启动 (2688x1216): 左边菜单, 中间一个只有正文被认出、「OK」漏掉的对话框
        let s = |t: &str, b: [i32; 4]| OcrItem { t: t.into(), b: b.map(|v| v * 28 / 10) };
        let items = vec![
            s("SINGLE PLAYER", [142, 202, 252, 218]),
            s("MULTIPLAYER", [148, 232, 246, 248]),
            s("OPTIONS", [164, 260, 230, 276]),
            s("QUIT", [180, 290, 216, 308]),
            s("TO USE UNITYIS DASHBOARD SERYICES，", [390, 184, 568, 196]),
            s("YOU NEED TO LINKYOUR UNITY", [408, 196, 552, 208]),
            s("PROIECT TOA PROJECT ID.", [418, 210, 540, 222]),
            s("V.15.0", [926, 414, 958, 424]),
        ];
        let r = zoom_regions(&items, 2688, 1216);
        assert_eq!(r.len(), 1, "{r:?}");
        let ok = (480 * 28 / 10, 242 * 28 / 10); // 截图里 OK 的位置
        assert!(ok.0 > r[0][0] && ok.0 < r[0][2] && ok.1 > r[0][1] && ok.1 < r[0][3], "{r:?} 没盖住 OK {ok:?}");
        assert!(r[0][0] > 252 * 28 / 10, "不该把左边菜单裁进来: {r:?}");
        // 只有按钮没有正文: 不放大
        assert!(zoom_regions(&items[..4], 2688, 1216).is_empty());
    }

    #[test]
    fn volume_is_parsed_from_media_session_output() {
        assert_eq!(parse_volume("volume is 0 in range [0..15]"), Some(0));
        assert_eq!(parse_volume("[V] stream 3\nvolume is 7 in range [0..15]\n"), Some(7));
        assert_eq!(parse_volume("Error: no such stream"), None);
    }

    #[test]
    fn ingame_verdict_needs_swing_well_above_idle_and_a_return() {
        assert!(ingame_verdict(0.03, 0.80, 0.25));
        assert!(!ingame_verdict(0.03, 0.10, 0.05), "拖了没动 = 菜单");
        assert!(!ingame_verdict(0.60, 0.70, 0.65), "静置也在大变 = 过场动画");
        assert!(!ingame_verdict(0.02, 0.50, 0.55), "拖回来反而更远 = 不是视角");
        assert!(!ingame_verdict(0.00, 0.9921, 0.9669), "10-11 原神冷启动过场: 拖回来没回来");
        assert!(!ingame_verdict(0.1871, 0.9996, 0.9955), "同上, 复测");
        assert!(ingame_verdict(0.026, 0.7312, 0.4202), "10-11 原神大世界实测");
    }

    fn rs(p95: f64) -> RoundStat {
        RoundStat { frame_p95: Some(p95), frame_p50: Some(16.6), fps_mean: Some(60.0), n_frames: 1800, ..Default::default() }
    }

    #[test]
    fn judge_passes_tight_rounds_and_fails_loose_ones() {
        let r = judge(&[rs(19.3), rs(19.5), rs(19.4), rs(19.6), rs(19.4)], true, false);
        assert_eq!(r.verdict, "PASS", "{:?}", r.reasons);
        let r = judge(&[rs(18.0), rs(19.5), rs(21.0), rs(19.0), rs(20.5)], true, false);
        assert_eq!(r.verdict, "FAIL");
        assert!(r.reasons[0].contains("变异系数"));
    }

    #[test]
    fn judge_fails_without_spin_or_enough_frames_or_over_temp() {
        let ok = [rs(19.3), rs(19.5), rs(19.4)];
        assert_eq!(judge(&ok, false, false).verdict, "FAIL");
        assert_eq!(judge(&ok, true, true).verdict, "FAIL");
        let mut few = ok.to_vec();
        for r in few.iter_mut() {
            r.n_frames = 10;
        }
        assert_eq!(judge(&few, true, false).verdict, "FAIL");
    }

    #[test]
    fn judge_flags_monotone_drift_as_a_warning() {
        let r = judge(&[rs(19.0), rs(19.1), rs(19.2), rs(19.3), rs(19.4)], true, false);
        assert_eq!(r.verdict, "PASS");
        assert!(r.monotone_p95);
        assert!(!r.warnings.is_empty());
    }

    #[test]
    fn workload_is_symmetric_pairs_covering_the_capture_window() {
        let g = GEOMS[0];
        let p = pairs_for(30, g);
        // 3 s 静置 + p 对 × 20 s ≥ 6 + 30 + 4
        assert!(3 + p as u64 * 20 >= 40, "pairs={p}");
        let wl = workload_json(g, 2688, 1216, p);
        let cmds: Vec<&str> = wl.as_array().unwrap().iter().filter_map(|s| s["cmd"].as_str()).collect();
        assert_eq!(cmds.len(), 2 * p);
        assert_eq!(cmds[0], "input swipe 1075 486 1882 486 10000");
        assert_eq!(cmds[1], "input swipe 1882 486 1075 486 10000");
        // 只有拖动, 没有点击 / 按键
        assert!(wl.as_array().unwrap().iter().all(|s| s["action"] == "sleep" || s["action"] == "exec"));
        assert!(cmds.iter().all(|c| c.starts_with("input swipe")));
    }

    #[test]
    fn render_comm_is_picked_among_the_package_threads_only() {
        let line = |c: &str, tid: u32| format!("  {c}-{tid}  [005] ..... 100.000001: adreno_cmdbatch_submitted: ctx=1 ts=2");
        let trace = [
            line("RenderEngine", 900),
            line("RenderEngine", 900),
            line("RenderEngine", 900),
            line("RHIThread", 4242),
            line("RHIThread", 4242),
            line("GameThread", 4243),
        ]
        .join("\n");
        let (c, share, mine, all) = pick_render_comm(&trace, &[4242, 4243]);
        assert_eq!(c.as_deref(), Some("RHIThread"));
        assert_eq!((mine, all), (3, 6));
        assert!((share - 2.0 / 3.0).abs() < 1e-9);
    }

    #[test]
    fn focus_and_component_parsing() {
        assert_eq!(
            parse_focus("  mCurrentFocus=Window{af8c7aa u0 com.miHoYo.Yuanshen/com.miHoYo.GetMobileInfo.MainActivity}"),
            "com.miHoYo.Yuanshen"
        );
        let s = "priority=0 preferredOrder=0 match=0x108000\ncom.unity.megacity.metro/com.unity.megacity.MegacityMetro\n";
        assert_eq!(
            parse_component(s, "com.unity.megacity.metro").as_deref(),
            Some("com.unity.megacity.metro/com.unity.megacity.MegacityMetro")
        );
        assert_eq!(parse_component("No activity found", "a.b"), None);
    }

    #[test]
    fn vlm_taps_are_vetted() {
        let items = [it("商城", 2400, 100), it("活动公告", 1300, 200)];
        assert!(vet_vlm_tap("右上角的 X 关闭图标", 2200, 150, &items).is_ok());
        assert!(vet_vlm_tap("商城入口", 2400, 100, &items).is_err());
        assert!(vet_vlm_tap("X", 2400, 100, &items).is_err(), "点位落在商城上");
        assert!(vet_vlm_tap("登录按钮", 1300, 900, &items).is_err());
    }

    #[test]
    fn cv_matches_sample_definition() {
        let c = cv(&[1.0, 2.0, 3.0]).unwrap();
        assert!((c - 0.5).abs() < 1e-12);
        assert_eq!(cv(&[1.0]), None);
    }

    #[test]
    fn onboard_hdc_gets_android_only_guidance_without_not_installed_error() {
        let message = onboard_hdc_guidance("com.miHoYo.Yuanshen");
        assert!(message.contains("onboard 目前只支持 Android"));
        assert!(message.contains("aa start -b com.miHoYo.Yuanshen -a <mainAbility>"));
        assert!(message.contains("bm dump -n com.miHoYo.Yuanshen"));
        assert!(!message.contains("没装"));
        let out = std::env::temp_dir().join(format!("onboard-hdc-reject-{}", std::process::id()));
        let code = run_onboard(&["--package".into(), "com.miHoYo.Yuanshen".into(),
                                "--serial".into(), "hdc:fake-device".into(), "--out".into(), out.to_string_lossy().into()]);
        assert_eq!(code, 2);
        assert!(!out.exists());
    }
}
