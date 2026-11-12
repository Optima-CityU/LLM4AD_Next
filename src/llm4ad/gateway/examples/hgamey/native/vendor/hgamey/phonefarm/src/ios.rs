//! iOS 设备后端: 经 WebDriverAgent(WDA) 操作**真实 iPhone**。
//!
//! 语义移植自 iPhone-use 的 WDA 客户端层
//! (<https://github.com/zhongerxin/iPhone-use>, MIT, © 2026 ZHONG XIN, commit 08ed5b0):
//! 它的 `server/wda_client.py` / `wda_controller.py` / `wda_apps.py` 证明了
//! 「USB → 本机 loopback 转发 → HTTP → WDA」这条通路可行, 以及各端点的用途取舍。
//! **只借语义, 不搬代码** —— 实现与注释均为本仓库重写。来源登记见 THIRD_PARTY_NOTICES.md。
//!
//! # 能力边界(先说不行的)
//!
//! - iOS 没有 shell、没有 root、没有 sysfs/ftrace —— 三层改动面 **全部 `No`**,
//!   遥测(帧时/功耗/温度)也**没有来源**。所以这个后端**只能看和动**(UI 遍历、截图、
//!   输入、应用启停), **不能做优化闭环的改动面评测**。`layers()` 如实声明这一点,
//!   `perfsrc::for_device` 也为它返回一个「整条通路不可用」的来源而不是冒充安卓。
//! - `revive` / `push_file` / `pull` / `stream_shell` 没有对应能力, 如实失败。
//! - WDA 的 JSON source **不带** 安卓那套 `clickable` / `scrollable` 标志。
//!   本后端的推导口径写在 [`is_actionable`] / [`is_scrollable`] 上, 不藏 ——
//!   推导错了是推导的错, 不是「设备说的」。
//!
//! # 坐标
//!
//! **一律用 WDA 的「点」(point)**, `size()` 也返回点。这与安卓侧的像素语义不同,
//! 但 phonefarm 下游用到的都是「归一化到 0~999 的相对位置」, 单位在归一化时相消。
//! 截图是像素, 但截图只进文件和缩略图, 不参与坐标换算。
//!
//! # 隧道
//!
//! WDA 跑在手机的 8100 端口上, 要经 `iproxy`(libimobiledevice)转到本机。取值顺序:
//!   1. 环境变量 `WDA_URL`(用户自己管隧道时用这个, 最稳);
//!   2. 否则连本机 `127.0.0.1:18100`;
//!   3. 该端口没人听且 serial 带了 udid 时, 自己起 `iproxy` 并托管它的生命周期。
//!
//! WDA 本体需要在手机上先装好并启动(见 iPhone-use 的 setup 流程), 本后端不负责装。

use crate::device::{FullNode, Node, SysState};
use crate::platform::{Backend, Layer, LayerSupport, Support};
use serde_json::{json, Value};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

/// 手机侧 WDA 监听端口(WebDriverAgent 默认)。
pub const WDA_PORT: u16 = 8100;
/// 本机转发端口。与 iPhone-use 的默认值取齐, 免得同一台机器上两条路各转各的。
pub const LOCAL_PORT: u16 = 18100;
/// 单次 HTTP 上限。WDA 拍照与 source 偶尔会慢, 30s 是实测够用的上限。
const HTTP_TIMEOUT_S: u64 = 30;
/// 元素文字截断, 与 `device::Node` 的 els 契约一致(40 字)。
const ELS_TEXT_CAP: usize = 40;
/// 模拟返回手势时, 从左边多少比例处起手。
const BACK_EDGE_FRAC: f64 = 0.02;

pub struct Ios {
    /// 设备 UDID(serial 里 `ios:` 之后的部分)。没有也能跑 —— WDA 只有一台目标时不需要。
    udid: Option<String>,
    /// WDA 的基地址, 不含尾斜杠。
    base: String,
    tmp: String,
    frozen: AtomicBool,
    session: Mutex<Option<String>>,
    size: Mutex<Option<(i32, i32)>>,
    tunnel: Mutex<Option<Child>>,
}

impl Ios {
    pub fn new(udid: Option<String>, tmp: String) -> Self {
        // WDA_URL 优先: 用户自己管隧道时, 我们既不起 iproxy 也不去猜端口。
        let base = std::env::var("WDA_URL")
            .ok()
            .map(|s| s.trim().trim_end_matches('/').to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| format!("http://127.0.0.1:{LOCAL_PORT}"));
        Ios {
            udid,
            base,
            tmp,
            frozen: AtomicBool::new(false),
            session: Mutex::new(None),
            size: Mutex::new(None),
            tunnel: Mutex::new(None),
        }
    }

    fn frozen(&self) -> bool {
        self.frozen.load(Ordering::Relaxed)
    }

    /// 已连上 WDA 且就绪。
    pub fn wda_ready(&self, ms: u64) -> bool {
        self.json("GET", "/status", None, ms / 1000)
            .is_some()
    }

    // ── HTTP(curl 子进程, 与 brain.rs 同一取舍: 绕本机代理指纹, 且不引依赖) ──

    fn json(&self, method: &str, path: &str, body: Option<&Value>, timeout_s: u64) -> Option<Value> {
        let url = format!("{}{}", self.base, path);
        let argv = curl_argv(method, &url, body, timeout_s);
        let out = Command::new(&argv[0]).args(&argv[1..]).output().ok()?;
        if !out.status.success() {
            return None;
        }
        serde_json::from_slice::<Value>(&out.stdout).ok()
    }

    /// WDA session: 首次用到时建, 建失败保持 None 下次重试(不在构造里建 ——
    /// 构造发生在 `platform::open`, 那是纯选择后端的路径, 不该碰设备)。
    fn session_id(&self) -> Option<String> {
        if let Some(s) = self.session.lock().unwrap().clone() {
            return Some(s);
        }
        let mut always = json!({
            "platformName": "iOS",
            // iPhone-use 的经验: singleton test manager 会在多客户端间互相踹掉对方的会话
            "shouldUseSingletonTestManager": false,
        });
        if let Some(u) = &self.udid {
            always["udid"] = json!(u);
        }
        let caps = json!({"capabilities": {"alwaysMatch": always}});
        let v = self.json("POST", "/session", Some(&caps), HTTP_TIMEOUT_S)?;
        let sid = v
            .pointer("/value/sessionId")
            .or_else(|| v.get("sessionId"))
            .and_then(|s| s.as_str())?
            .to_string();
        *self.session.lock().unwrap() = Some(sid.clone());
        Some(sid)
    }

    fn sess_path(&self, tail: &str) -> Option<String> {
        self.session_id().map(|s| format!("/session/{s}{tail}"))
    }

    fn post_session(&self, tail: &str, body: &Value) -> bool {
        match self.sess_path(tail) {
            Some(p) => self.json("POST", &p, Some(body), HTTP_TIMEOUT_S).is_some(),
            None => false,
        }
    }

    // ── 隧道 ──

    /// 端口没人听且知道 udid 时, 自己起一个 iproxy 并托管。
    /// 只起一次; 起了就一直活到进程结束(`Drop` 里收尸)。
    fn ensure_tunnel(&self) {
        let mut slot = self.tunnel.lock().unwrap();
        if slot.is_some() || self.port_open() {
            return;
        }
        let Some(udid) = self.udid.as_ref() else {
            return; // 没 udid 不猜设备, 让调用方看到真实的连接失败
        };
        let Ok(child) = Command::new("iproxy")
            .args([&LOCAL_PORT.to_string(), &WDA_PORT.to_string(), udid])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
        else {
            return;
        };
        *slot = Some(child);
    }

    /// 隧道是否已经在听(别人起的也算)。只做连接尝试, 不发请求。
    fn port_open(&self) -> bool {
        let Some(addr) = self.base.strip_prefix("http://") else {
            return false;
        };
        let addr = addr.split('/').next().unwrap_or(addr);
        std::net::TcpStream::connect_timeout(
            &match addr.parse() {
                Ok(a) => a,
                Err(_) => return false,
            },
            std::time::Duration::from_millis(300),
        )
        .is_ok()
    }
}

impl Drop for Ios {
    fn drop(&mut self) {
        if let Some(mut c) = self.tunnel.lock().unwrap().take() {
            let _ = c.kill();
            let _ = c.wait();
        }
    }
}

/// curl 命令行(纯函数, 单测钉契约)。与 `brain.rs` 同取舍: HTTP 走 curl 子进程。
fn curl_argv(method: &str, url: &str, body: Option<&Value>, timeout_s: u64) -> Vec<String> {
    let mut v = vec![
        "curl".to_string(),
        "-sS".to_string(),
        "-X".to_string(),
        method.to_string(),
        "--max-time".to_string(),
        timeout_s.max(1).to_string(),
        "-H".to_string(),
        "Content-Type: application/json".to_string(),
        url.to_string(),
    ];
    if let Some(b) = body {
        v.push("-d".to_string());
        v.push(b.to_string());
    }
    v
}

// ───────────────────────── W3C Actions(点按 / 滑动) ─────────────────────────
//
// 用 W3C 而不是 WDA 自家的 `/wda/touch/perform`: 前者是标准, WDA 两种都支持,
// 而标准那套的 payload 形状稳定、可单测。iPhone-use 的 tap/swipe 也是落到这两个语义。

/// 单指触控 W3C pointer source。
fn pointer_actions(steps: Vec<Value>) -> Value {
    json!({
        "actions": [{
            "type": "pointer",
            "id": "finger1",
            "parameters": {"pointerType": "touch"},
            "actions": steps,
        }]
    })
}

fn move_to(x: i32, y: i32, duration_ms: u64) -> Value {
    json!({"type": "pointerMove", "duration": duration_ms, "x": x, "y": y})
}

/// 点按: 移到 → 按下 → 抬起。
pub fn tap_payload(x: i32, y: i32) -> Value {
    pointer_actions(vec![
        move_to(x, y, 0),
        json!({"type": "pointerDown", "button": 0}),
        json!({"type": "pointerUp", "button": 0}),
    ])
}

/// 滑动: 按住后按 duration 匀速拖到终点再抬起。
pub fn swipe_payload(x1: i32, y1: i32, x2: i32, y2: i32, duration_ms: u64) -> Value {
    pointer_actions(vec![
        move_to(x1, y1, 0),
        json!({"type": "pointerDown", "button": 0}),
        move_to(x2, y2, duration_ms.max(1)),
        json!({"type": "pointerUp", "button": 0}),
    ])
}

/// 输入文字。WDA 的 `/wda/keys` 收 `{"value": [...]}`; 逐字符给是为了 Unicode:
/// 整串一次性下去时, 非 ASCII 在硬件键盘路径上会被丢。这也正是 iPhone-use
/// 对中文输入的处理方式。
pub fn keys_payload(text: &str) -> Value {
    json!({"value": text.chars().map(|c| c.to_string()).collect::<Vec<_>>()})
}

// ───────────────────────── 界面树 ─────────────────────────

/// WDA `GET /session/:id/source?format=json` 的原文 → 全量层(文档序 + depth)。
///
/// 形状: `{"value": <节点>}`(有的版本直接给节点)。节点字段:
/// `type` / `name` / `label` / `value` / `rect{x,y,width,height}` / `enabled` /
/// `visible` / `accessible` / `identifier` / `children`。
/// 少数版本把 `x/y/width/height` 平铺在节点上, 这里两种都认。
pub fn parse_wda_source(text: &str) -> Vec<FullNode> {
    let Ok(v) = serde_json::from_str::<Value>(text) else {
        return Vec::new();
    };
    let root = v.get("value").unwrap_or(&v);
    let mut out = Vec::new();
    walk_source(root, 0, &mut out);
    out
}

fn walk_source(v: &Value, depth: u32, out: &mut Vec<FullNode>) {
    match v {
        Value::Array(items) => {
            for it in items {
                walk_source(it, depth, out);
            }
        }
        Value::Object(_) => {
            out.push(node_of(v, depth));
            if let Some(children) = v.get("children").and_then(|c| c.as_array()) {
                for c in children {
                    walk_source(c, depth + 1, out);
                }
            }
        }
        _ => {}
    }
}

fn node_of(v: &Value, depth: u32) -> FullNode {
    let s = |k: &str| {
        v.get(k)
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .trim()
            .to_string()
    };
    let ty = s("type");
    let name = s("name");
    let ident = s("identifier");
    // 文字优先级: label(给人看的) > name > value。安卓侧是 text > content-desc, 同序。
    let t = ["label", "name", "value"]
        .iter()
        .map(|k| s(k))
        .find(|x| !x.is_empty())
        .unwrap_or_default();
    let b = rect_of(v).unwrap_or([0, 0, 0, 0]);
    let enabled = v.get("enabled").and_then(|x| x.as_bool()).unwrap_or(true);
    let visible = v.get("visible").and_then(|x| x.as_bool()).unwrap_or(true);
    let accessible = v.get("accessible").and_then(|x| x.as_bool()).unwrap_or(false);
    // WDA 的 JSON source 不给安卓那套 clickable/scrollable —— 按类型推导, 口径写在下面两个函数上。
    // `accessible`(即 iOS 的 isAccessibilityElement)是门槛: 控件类型 + 无障碍元素 + 可用且可见,
    // 三条都满足才算可点。宁可窄: 推得宽就等于把整棵树都标成可点, 那个标志也就没信息了。
    FullNode {
        t,
        b,
        id: if !ident.is_empty() {
            Some(ident)
        } else if !name.is_empty() {
            Some(name)
        } else {
            None
        },
        class: ty.clone(),
        clickable: enabled && visible && accessible && is_actionable(&ty),
        scrollable: is_scrollable(&ty),
        checkable: is_checkable(&ty),
        checked: is_checked(v),
        depth,
    }
}

fn rect_of(v: &Value) -> Option<[i32; 4]> {
    if let Some(r) = v.get("rect") {
        let g = |k: &str| r.get(k).and_then(|x| x.as_f64()).unwrap_or(0.0).round() as i32;
        let (x, y, w, h) = (g("x"), g("y"), g("width"), g("height"));
        return Some([x, y, x + w, y + h]);
    }
    // 平铺形态: x/y/width/height 直接在节点上
    let any = ["x", "y", "width", "height"]
        .iter()
        .any(|k| v.get(*k).and_then(|x| x.as_f64()).is_some());
    if !any {
        return None;
    }
    let g = |k: &str| v.get(k).and_then(|x| x.as_f64()).unwrap_or(0.0).round() as i32;
    let (x, y, w, h) = (g("x"), g("y"), g("width"), g("height"));
    Some([x, y, x + w, y + h])
}

/// 「这个控件按得动吗」的推导口径。
///
/// 保守: 只认**定义上就是控件**的 XCUIElementType。`Other` / `StaticText` / `Image`
/// 一律不算 —— 安卓侧的 `clickable` 是平台给的, iOS 侧是我们推的, 推得宽就等于
/// 把整棵树都标成可点, 那个标志也就没信息了。
fn is_actionable(ty: &str) -> bool {
    matches!(
        ty,
        "XCUIElementTypeButton"
            | "XCUIElementTypeCell"
            | "XCUIElementTypeSwitch"
            | "XCUIElementTypeToggle"
            | "XCUIElementTypeTextField"
            | "XCUIElementTypeSecureTextField"
            | "XCUIElementTypeTextView"
            | "XCUIElementTypeSearchField"
            | "XCUIElementTypeLink"
            | "XCUIElementTypeSegmentedControl"
            | "XCUIElementTypeSlider"
            | "XCUIElementTypeStepper"
            | "XCUIElementTypeMenuItem"
            | "XCUIElementTypeMenuButton"
            | "XCUIElementTypeTab"
            | "XCUIElementTypeTabBar"
            | "XCUIElementTypePickerWheel"
            | "XCUIElementTypeKey"
    )
}

/// 能滚的类型。安卓侧由 `scrollable` 属性给出, iOS 侧只能按类型认。
fn is_scrollable(ty: &str) -> bool {
    matches!(
        ty,
        "XCUIElementTypeScrollView"
            | "XCUIElementTypeTable"
            | "XCUIElementTypeCollectionView"
            | "XCUIElementTypeWebView"
            | "XCUIElementTypeMap"
            | "XCUIElementTypePicker"
    )
}

fn is_checkable(ty: &str) -> bool {
    matches!(ty, "XCUIElementTypeSwitch" | "XCUIElementTypeToggle")
}

/// 开关的「开/关」。WDA 把开关状态放在 `value` 里, 各版本表述不一, 全认。
fn is_checked(v: &Value) -> bool {
    match v.get("value") {
        Some(Value::Bool(b)) => *b,
        Some(Value::String(s)) => matches!(s.as_str(), "1" | "true" | "on" | "On"),
        Some(Value::Number(n)) => n.as_i64() == Some(1),
        _ => false,
    }
}

/// 全量层 → `els` 契约(flat 文字节点, 40 字截断)。
/// 与安卓侧 `device::els` 的区别: 那边是 dumpsys 给的现成清单, 这边从全量层派生 ——
/// 但**派生规则与安卓的契约对齐**(只取有文字的, 同一个截断长度), 所以下游不用分叉。
pub fn els_from_full(full: &[FullNode]) -> Vec<Node> {
    full.iter()
        .filter(|n| !n.t.is_empty())
        .map(|n| Node {
            t: n.t.chars().take(ELS_TEXT_CAP).collect(),
            b: n.b,
        })
        .collect()
}

// ───────────────────────── 小响应解析(纯函数) ─────────────────────────

/// `GET /window/size` → (宽, 高), 单位是点。
pub fn parse_window_size(v: &Value) -> Option<(i32, i32)> {
    let w = v.pointer("/value/width").and_then(|x| x.as_f64())?;
    let h = v.pointer("/value/height").and_then(|x| x.as_f64())?;
    if w <= 0.0 || h <= 0.0 {
        return None;
    }
    Some((w.round() as i32, h.round() as i32))
}

/// `GET /wda/activeAppInfo` → 前台 bundle id。
pub fn parse_active_app(v: &Value) -> Option<String> {
    let s = v
        .pointer("/value/bundleId")
        .or_else(|| v.get("bundleId"))
        .and_then(|x| x.as_str())?;
    Some(s.to_string())
}

/// `GET /wda/listApps` → [(bundle id, 显示名)]。iOS 没有 component, 第二列是名字,
/// 给「可启动的应用」清单读起来有用。
pub fn parse_apps(v: &Value) -> Vec<(String, String)> {
    let Some(arr) = v.get("value").and_then(|x| x.as_array()) else {
        return Vec::new();
    };
    arr.iter()
        .filter_map(|a| {
            let id = a.get("bundleId").and_then(|x| x.as_str())?;
            let name = a.get("name").and_then(|x| x.as_str()).unwrap_or(id);
            Some((id.to_string(), name.to_string()))
        })
        .collect()
}

/// `GET /screenshot` 的 `value` 是 base64 PNG。解出来给 `image` crate 用。
pub fn b64_decode(s: &str) -> Option<Vec<u8>> {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut rev = [255u8; 256];
    for (i, c) in T.iter().enumerate() {
        rev[*c as usize] = i as u8;
    }
    let mut out = Vec::with_capacity(s.len() / 4 * 3);
    let mut acc = 0u32;
    let mut nbits = 0u32;
    for c in s.bytes() {
        if c == b'=' || c.is_ascii_whitespace() {
            continue;
        }
        let d = rev[c as usize];
        if d == 255 {
            return None; // 非法字符一律拒绝, 不猜
        }
        acc = (acc << 6) | d as u32;
        nbits += 6;
        if nbits >= 8 {
            nbits -= 8;
            out.push((acc >> nbits) as u8);
        }
    }
    Some(out)
}

// ───────────────────────── 真机体检 ─────────────────────────
//
// 本后端唯一真正没验证过的东西是「WDA 的 JSON source 到底长什么样」。把它做成一条命令:
// 插上手机跑一次, 解析器假设了哪些键、这台设备实际给了哪些, 一次说清。
// 这比「代码里写着未验证」有用 —— 后者只能等人主动想起来。

/// 界面树形状体检结果。解析器([`parse_wda_source`])假设的每一个键都在这里有账。
#[derive(Debug, Default, Clone, PartialEq)]
pub struct SourceShape {
    /// 解出来的节点总数
    pub nodes: usize,
    /// 带 `type` 的节点数(解析器靠它推导可点/可滚)
    pub with_type: usize,
    /// `label`/`name`/`value` 任一非空——靠它拿到文字
    pub with_text: usize,
    /// 有坐标的节点数(`rect` 或平铺的 x/y/width/height)
    pub with_rect: usize,
    /// 用的是平铺形态(而不是 `rect` 对象)
    pub flat_rect: bool,
    /// 框面积非零的节点数——为 0 说明坐标全塌在原点, 点按会全打在左上角
    pub nonempty_box: usize,
    /// 有 `children` 字段的节点数
    pub with_children: usize,
}

impl SourceShape {
    /// 解析器能不能用这份 source。返回人话的原因清单(**空 = 能用**)。
    pub fn problems(&self) -> Vec<String> {
        let mut v = Vec::new();
        if self.nodes == 0 {
            v.push("一个节点都没解出来: 这台设备的 source 形状与解析器不匹配".into());
            return v;
        }
        if self.with_type == 0 {
            v.push("没有任何节点带 `type`: 可点/可滚的推导会全部落空".into());
        }
        if self.with_text == 0 {
            v.push("没有任何节点带 label/name/value: 界面树拿不到一个字的文字".into());
        }
        if self.with_rect == 0 {
            v.push("没有任何节点带坐标: 尺寸与点按都无从谈起".into());
        } else if self.nonempty_box == 0 {
            v.push("坐标全为零面积: 模型会看到一堆叠在左上角的元素".into());
        }
        v
    }
    pub fn ok(&self) -> bool {
        self.problems().is_empty()
    }
}

/// 从一份 source 原文里数出形状(纯函数, 单测钉契约)。
pub fn analyze_source_shape(text: &str) -> SourceShape {
    let Ok(v) = serde_json::from_str::<Value>(text) else {
        return SourceShape::default();
    };
    let root = v.get("value").unwrap_or(&v);
    let mut s = SourceShape::default();
    shape_walk(root, &mut s);
    s
}

fn shape_walk(v: &Value, s: &mut SourceShape) {
    match v {
        Value::Array(items) => {
            for it in items {
                shape_walk(it, s);
            }
        }
        Value::Object(map) => {
            s.nodes += 1;
            if map.get("type").and_then(|x| x.as_str()).is_some_and(|x| !x.is_empty()) {
                s.with_type += 1;
            }
            let has_text = ["label", "name", "value"]
                .iter()
                .any(|k| map.get(*k).and_then(|x| x.as_str()).is_some_and(|x| !x.trim().is_empty()));
            if has_text {
                s.with_text += 1;
            }
            let has_rect_obj = map.contains_key("rect");
            let has_flat = ["x", "y", "width", "height"].iter().all(|k| map.contains_key(*k));
            if has_rect_obj || has_flat {
                if has_flat && !has_rect_obj {
                    s.flat_rect = true;
                }
                s.with_rect += 1;
                let b = rect_of(v).unwrap_or([0, 0, 0, 0]);
                if b[2] > b[0] && b[3] > b[1] {
                    s.nonempty_box += 1;
                }
            }
            if map.contains_key("children") {
                s.with_children += 1;
            }
            if let Some(children) = map.get("children").and_then(|c| c.as_array()) {
                for c in children {
                    shape_walk(c, s);
                }
            }
        }
        _ => {}
    }
}

fn probe_usage() -> &'static str {
    "用法: phonefarm ios-probe [--serial ios:<udid>] [--json]\n\
     \x20 逐项探 WDA 的每个端点, 并**核实界面树的形状与解析器的假设对不对**。\n\
     \x20 退出码: 0 = 全部就绪; 1 = 有端点不通或形状不匹配; 2 = 用法错"
}

/// 真机体检: 一条命令把本后端依赖的每个环节都探一遍。
///
/// 存在理由: 这个后端是照 WDA 的**文档形状**写的, 没过真机。与其在文档里写
/// 「尚未验证」等人想起来, 不如给一条命令 —— 插上手机跑一次就知道行不行、哪一环不行。
pub fn run_ios_probe(args: &[String]) -> i32 {
    let mut serial: Option<String> = None;
    let mut json = false;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--json" => json = true,
            "--serial" => serial = it.next().cloned(),
            "-h" | "--help" => {
                println!("{}", probe_usage());
                return 2;
            }
            other => {
                eprintln!("未知参数 {other}\n{}", probe_usage());
                return 2;
            }
        }
    }
    let udid = serial
        .as_deref()
        .map(|s| s.strip_prefix("ios:").unwrap_or(s))
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty());
    let tmp = std::env::var("PHONEFARM_TMP").unwrap_or_else(|_| "/tmp/phonefarm".to_string());
    let me = Ios::new(udid, tmp);

    let mut steps: Vec<Value> = Vec::new();
    let mut bad = false;
    let mut remedy: Vec<String> = Vec::new();
    let step = |name: &str, ok: bool, detail: Value, steps: &mut Vec<Value>| {
        steps.push(json!({"step": name, "ok": ok, "detail": detail}));
        ok
    };

    // 1. 隧道 + WDA 活着
    me.ensure_tunnel();
    let status = me.json("GET", "/status", None, 5);
    if !step("tunnel_and_status", status.is_some(), json!({"base": me.base}), &mut steps) {
        bad = true;
        remedy.push("WDA 没应答。依次查: ①手机上 WebDriverAgentRunner 已启动并保持运行; ②USB 连着且已信任本机; ③iproxy 在转(或直接设 WDA_URL=http://127.0.0.1:<port>)".into());
    }

    // 2. 会话
    let sid = me.session_id();
    if !step("session", sid.is_some(), json!({"sessionId": sid}), &mut steps) {
        bad = true;
        remedy.push("建不了 WDA 会话。确认 WDA 版本支持 W3C capabilities, 且设备没有开发者模式/信任问题".into());
    }

    // 3. 屏幕尺寸(点)
    let mut wh = json!(null);
    if let Some(p) = me.sess_path("/window/size") {
        wh = me
            .json("GET", &p, None, 10)
            .and_then(|v| parse_window_size(&v))
            .map(|(w, h)| json!({"w": w, "h": h}))
            .unwrap_or(json!(null));
    }
    if !step("window_size", wh != json!(null), wh.clone(), &mut steps) {
        bad = true;
    }

    // 4. 界面树: **本后端唯一真正没验证过的假设**
    let mut shape_json = json!(null);
    let mut shape_ok = false;
    if let Some(p) = me.sess_path("/source?format=json") {
        if let Some(v) = me.json("GET", &p, None, 20) {
            let raw = v.to_string();
            let s = analyze_source_shape(&raw);
            shape_ok = s.ok();
            shape_json = json!({
                "nodes": s.nodes,
                "with_type": s.with_type,
                "with_text": s.with_text,
                "with_rect": s.with_rect,
                "flat_rect": s.flat_rect,
                "nonempty_box": s.nonempty_box,
                "with_children": s.with_children,
                "problems": s.problems(),
                "matches_parser": shape_ok,
            });
        }
    }
    if !step("ui_source_shape", shape_ok, shape_json.clone(), &mut steps) {
        bad = true;
        remedy.push("界面树与解析器的假设不符 —— 这是 https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/DEVICES.md 里标的那个未验证项。把上面 detail 的 problems 抄进 issue, 或改 `parse_wda_source` / `analyze_source_shape` 对齐这台设备的形状".into());
    }

    // 5. 前台应用 / 应用清单
    let fg = me.foreground_pkg();
    if !step("active_app", !fg.is_empty(), json!({"bundleId": fg}), &mut steps) {
        bad = true;
    }
    let apps = me.launchable_apps();
    if !step("list_apps", !apps.is_empty(), json!({"count": apps.len()}), &mut steps) {
        bad = true;
    }

    // 6. 截图 + base64 解码(解码器是本仓库自己写的, 也要核)
    let shot = me.json("GET", "/screenshot", None, 30).and_then(|v| {
        let b64 = v.get("value").and_then(|x| x.as_str())?;
        let bytes = b64_decode(b64)?;
        let png = bytes.starts_with(&[0x89, 0x50, 0x4e, 0x47]);
        Some(json!({"bytes": bytes.len(), "png_magic": png}))
    });
    if !step("screenshot", shot.is_some(), shot.unwrap_or(json!(null)), &mut steps) {
        bad = true;
    }

    if json {
        println!(
            "{}",
            json!({"ok": !bad, "steps": steps, "remedy": remedy, "unverified_assumption": "WDA JSON source 形状"})
        );
    } else {
        println!("iOS 后端真机体检 (WDA base = {})", me.base);
        for s in &steps {
            let mark = if s["ok"].as_bool().unwrap_or(false) { "✓" } else { "✗" };
            println!("  {mark} {}", s["step"].as_str().unwrap_or("?"));
            if let Some(pr) = s["detail"]["problems"].as_array() {
                for p in pr {
                    println!("      - {}", p.as_str().unwrap_or(""));
                }
            }
        }
        if bad {
            println!("\n怎么修:");
            for r in &remedy {
                println!("  • {r}");
            }
        } else {
            println!("\n全部就绪。界面树的形状与解析器的假设一致 —— 可以把 https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/DEVICES.md 里那条「尚未上真机验证」改掉了。");
        }
    }
    if bad { 1 } else { 0 }
}

// ───────────────────────── Backend ─────────────────────────

/// iOS 上三层改动面的可行性。
///
/// 依据: 现代 iOS 商用机**没有 root、没有 shell**(`Backend::shell` 在这一侧没有实现),
/// 因此 ③ 系统参数与 ② 灰档(挂 Vulkan 层)都无从谈起; iOS 用 Metal 不用 Vulkan,
/// ① 的白盒载体(refbench)也没有 iOS 版。
/// 测量一栏给 `Unknown` 而不是 `No`: WDA **不提供**帧时与功耗(这一条是确定的),
/// 但设备端是否存在别的可采来源(如 Instruments 的通道)**没有验证过**, 不知道就说不知道。
pub const IOS_LAYERS: [LayerSupport; 4] = [
    LayerSupport {
        layer: Layer::Sysparam,
        support: Support::No,
        needs: "root + shell",
        why: "iOS 商用机拿不到 root, WDA 也不提供 shell; 没有可写 sysfs 的通道",
    },
    LayerSupport {
        layer: Layer::Gray,
        support: Support::No,
        needs: "Vulkan 层加载通路 + 可写应用目录",
        why: "iOS 图形栈是 Metal, 没有 Vulkan layer 注入点; 往 .app 内写 .so 同样要越狱",
    },
    LayerSupport {
        layer: Layer::Shader,
        support: Support::No,
        needs: "白盒载体或 ② 的挂层通路",
        why: "refbench 没有 iOS 版; 黑盒游戏依赖 ② (不可用)",
    },
    LayerSupport {
        layer: Layer::Measure,
        support: Support::Unknown,
        needs: "设备端性能来源",
        why: "WDA 不提供帧时与功耗(确定); 是否存在别的可采来源未验证过 —— 不知道就说不知道",
    },
];

impl Backend for Ios {
    // ── 身份 ──
    fn transport(&self) -> &'static str {
        "wda"
    }
    fn platform(&self) -> &'static str {
        "ios"
    }
    fn layers(&self) -> Vec<LayerSupport> {
        IOS_LAYERS.to_vec()
    }

    // ── 连接 ──
    fn set_frozen(&self) {
        self.frozen.store(true, Ordering::Relaxed);
    }
    fn health_check(&self, ms: u64) -> bool {
        self.ensure_tunnel();
        self.wda_ready(ms)
    }
    fn revive(&self, _emulator_cmd: &str) -> bool {
        // 没有「重启模拟器」这一说, 能做的只有把隧道重来一遍
        self.tunnel.lock().unwrap().take();
        self.ensure_tunnel();
        self.wda_ready(5_000)
    }

    // ── shell 与文件: iOS 没有这些通道, 如实失败, 不假装 ──
    fn shell(&self, _cmd: &str, _ms: u64) -> String {
        String::new()
    }
    fn stream_shell(&self, _cmd: &str) -> std::io::Result<std::process::Child> {
        Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "iOS 后端没有 shell(WDA 不提供)",
        ))
    }
    fn push_file(&self, _local: &str, _remote: &str) -> bool {
        false
    }
    fn pull(&self, _remote: &str, _local: &str) -> bool {
        false
    }

    // ── 看 ──
    fn els_full(&self, timeout_ms: u64) -> (Vec<Node>, Vec<FullNode>, String, String) {
        let pkg = self.foreground_pkg();
        self.ensure_tunnel();
        let secs = (timeout_ms / 1000).max(1);
        let Some(path) = self.sess_path("/source?format=json") else {
            return (Vec::new(), Vec::new(), pkg, String::new());
        };
        let Some(v) = self.json("GET", &path, None, secs) else {
            return (Vec::new(), Vec::new(), pkg, String::new());
        };
        let raw = v.to_string();
        let full = parse_wda_source(&raw);
        let els = els_from_full(&full);
        (els, full, pkg, raw)
    }

    fn foreground_pkg(&self) -> String {
        self.ensure_tunnel();
        self.json("GET", "/wda/activeAppInfo", None, 10)
            .and_then(|v| parse_active_app(&v))
            .unwrap_or_default()
    }

    fn sys_state(&self) -> SysState {
        // iOS 没有 Activity 概念(activity 留空), 软键盘弹没弹也不从这条通道拿 ——
        // 不知道就 None, 不装。
        SysState {
            pkg: self.foreground_pkg(),
            activity: String::new(),
            ime: None,
        }
    }

    fn clipboard(&self) -> Option<String> {
        // WDA 的 getPasteboard 需要会话带相应权限, 读不到就是 None(不猜、不缓存旧值)
        self.ensure_tunnel();
        let path = self.sess_path("/wda/getPasteboard")?;
        let v = self.json("POST", &path, Some(&json!({})), 10)?;
        v.get("value").and_then(|x| x.as_str()).map(String::from)
    }

    fn launchable_apps(&self) -> Vec<(String, String)> {
        self.ensure_tunnel();
        self.json("GET", "/wda/listApps", None, 20)
            .map(|v| parse_apps(&v))
            .unwrap_or_default()
    }

    fn screen(&self, out_jpg: &str) -> Option<(i32, i32)> {
        self.ensure_tunnel();
        let v = self.json("GET", "/screenshot", None, HTTP_TIMEOUT_S)?;
        let b64 = v.get("value").and_then(|x| x.as_str())?;
        let png = b64_decode(b64)?;
        let img = image::load_from_memory(&png).ok()?;
        let (w, h) = (img.width() as i32, img.height() as i32);
        // 与安卓侧一致: 落成 JPEG(下游的缩略图与差异判定都吃 JPEG)
        crate::device::resample_and_save_jpeg(&img, w as u32, 90, out_jpg)?;
        Some((w, h))
    }

    fn quick_thumb(&self, tag: &str) -> Option<Vec<u8>> {
        let path = format!("{}/thumb-{tag}-{}.jpg", self.tmp, std::process::id());
        self.screen(&path)?;
        let g = crate::device::thumb_gray(&path, &self.tmp, tag);
        let _ = std::fs::remove_file(&path);
        g
    }

    fn size(&self) -> (i32, i32) {
        if let Some(s) = *self.size.lock().unwrap() {
            return s;
        }
        self.ensure_tunnel();
        let mut s = (0, 0);
        if let Some(path) = self.sess_path("/window/size") {
            if let Some(v) = self.json("GET", &path, None, 10) {
                if let Some(wh) = parse_window_size(&v) {
                    s = wh;
                }
            }
        }
        if s.0 > 0 {
            *self.size.lock().unwrap() = Some(s);
        }
        s
    }

    // ── 动 ──
    fn tap(&self, x: i32, y: i32) {
        if self.frozen() {
            return;
        }
        self.ensure_tunnel();
        if self.session_id().is_none() {
            return;
        }
        let _ = self.post_session("/actions", &tap_payload(x, y));
    }

    fn swipe(&self, x: i32, y: i32, x2: i32, y2: i32) {
        if self.frozen() {
            return;
        }
        self.ensure_tunnel();
        if self.session_id().is_none() {
            return;
        }
        // 350ms 与安卓侧 `input swipe` 的默认节拍对齐
        let _ = self.post_session("/actions", &swipe_payload(x, y, x2, y2, 350));
    }

    fn scroll_down(&self) {
        let (w, h) = self.size();
        self.swipe(w / 2, (h as f64 * 0.75) as i32, w / 2, (h as f64 * 0.30) as i32);
    }

    fn scroll_up(&self) {
        let (w, h) = self.size();
        self.swipe(w / 2, (h as f64 * 0.30) as i32, w / 2, (h as f64 * 0.75) as i32);
    }

    fn type_text(&self, text: &str) {
        if self.frozen() {
            return;
        }
        self.ensure_tunnel();
        if self.session_id().is_none() {
            return;
        }
        let _ = self.post_session("/wda/keys", &keys_payload(text));
    }

    /// 清空焦点输入框。
    ///
    /// iOS 没有「清空」原语。做法: 先点一下末尾把光标放到最后, 再连发退格。
    /// 退格走 `/wda/keys` 的 `\u{8}`(XCUIKeyboardKeyDelete)。
    /// 局限写在明面上: **不知道当前框有多长**, 所以按固定次数退格(和安卓侧
    /// `clear_field` 的连发 DEL 同一思路); 预填很长的框可能清不干净。
    fn clear_field(&self) {
        if self.frozen() {
            return;
        }
        self.ensure_tunnel();
        if self.session_id().is_none() {
            return;
        }
        let _ = self.post_session("/wda/keys", &keys_payload(&"\u{8}".repeat(CLEAR_BACKSPACES)));
    }

    /// iOS 没有系统返回键。用**左边缘交互式返回手势**代替 ——
    /// 这是 iOS 上「返回上一页」的通用手势, 但只在支持它的页面上有效(全屏游戏里无效)。
    fn back(&self) {
        if self.frozen() {
            return;
        }
        let (w, h) = self.size();
        let x0 = (w as f64 * BACK_EDGE_FRAC) as i32;
        self.swipe(x0, h / 2, (w as f64 * 0.6) as i32, h / 2);
    }

    fn home(&self) {
        if self.frozen() {
            return;
        }
        self.ensure_tunnel();
        if self.session_id().is_none() {
            return;
        }
        let _ = self.post_session("/wda/homescreen", &json!({}));
    }

    // ── 应用 ──
    fn force_stop(&self, pkg: &str) {
        if self.frozen() {
            return;
        }
        self.ensure_tunnel();
        if self.session_id().is_none() {
            return;
        }
        let _ = self.json(
            "POST",
            "/wda/apps/terminate",
            Some(&json!({"bundleId": pkg})),
            HTTP_TIMEOUT_S,
        );
    }

    /// 冷启动毫秒: iOS 这条通路上量不到(WDA 不回报启动耗时), 如实 `None`。
    /// 需要启动耗时的评测不该走这个后端。
    fn launch(&self, pkg: &str, _comp: &str) -> Option<i64> {
        if self.frozen() {
            return None;
        }
        self.ensure_tunnel();
        if self.session_id().is_none() {
            return None;
        }
        let _ = self.json(
            "POST",
            "/wda/apps/launch",
            Some(&json!({"bundleId": pkg})),
            HTTP_TIMEOUT_S,
        );
        None
    }

    // ── 遥测: 没有来源。返回默认(全空)而不是编数字 ──
    fn telemetry_setup(&self, _pkg: &str) -> bool {
        false
    }
    fn telemetry(&self, _heavy: bool, _pkg: &str) -> crate::telemetry::Telemetry {
        crate::telemetry::Telemetry::default()
    }

    fn screen_image(&self) -> Option<image::DynamicImage> {
        self.ensure_tunnel();
        let v = self.json("GET", "/screenshot", None, HTTP_TIMEOUT_S)?;
        let png = b64_decode(v.get("value").and_then(|x| x.as_str())?)?;
        image::load_from_memory(&png).ok()
    }
}

/// `clear_field` 的退格次数。够清空常规输入框, 又不至于把上一个页面也退掉。
const CLEAR_BACKSPACES: usize = 128;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn curl_argv_shape() {
        let a = curl_argv("GET", "http://127.0.0.1:18100/status", None, 10);
        assert_eq!(a[0], "curl");
        assert!(a.contains(&"GET".to_string()));
        assert!(a.windows(2).any(|w| w[0] == "--max-time" && w[1] == "10"));
        assert!(!a.contains(&"-d".to_string()), "无 body 不得带 -d");
        let b = curl_argv("POST", "http://x/y", Some(&json!({"a":1})), 0);
        assert!(b.contains(&"-d".to_string()));
        assert!(b.windows(2).any(|w| w[0] == "--max-time" && w[1] == "1"), "超时不得为 0");
    }

    #[test]
    fn tap_payload_is_w3c_touch() {
        let p = tap_payload(100, 200);
        let src = &p["actions"][0];
        assert_eq!(src["type"], "pointer");
        assert_eq!(src["parameters"]["pointerType"], "touch");
        let steps = src["actions"].as_array().unwrap();
        assert_eq!(steps.len(), 3);
        assert_eq!(steps[0]["type"], "pointerMove");
        assert_eq!(steps[0]["x"], 100);
        assert_eq!(steps[0]["y"], 200);
        assert_eq!(steps[1]["type"], "pointerDown");
        assert_eq!(steps[2]["type"], "pointerUp");
    }

    #[test]
    fn swipe_payload_carries_the_duration() {
        let p = swipe_payload(10, 20, 30, 40, 350);
        let steps = p["actions"][0]["actions"].as_array().unwrap();
        assert_eq!(steps.len(), 4);
        assert_eq!(steps[2]["type"], "pointerMove");
        assert_eq!(steps[2]["duration"], 350, "滑动时长必须落到 payload 上");
        assert_eq!(steps[2]["x"], 30);
        assert_eq!(steps[2]["y"], 40);
        // 0 会被夹成 1, 免得 W3C 把它当成瞬移
        let z = swipe_payload(0, 0, 1, 1, 0);
        assert_eq!(z["actions"][0]["actions"][2]["duration"], 1);
    }

    #[test]
    fn keys_payload_splits_unicode_per_char() {
        // 逐字符: 整串一次性下去时非 ASCII 会被丢
        let p = keys_payload("你好a");
        let v = p["value"].as_array().unwrap();
        assert_eq!(v.len(), 3);
        assert_eq!(v[0], "你");
        assert_eq!(v[2], "a");
    }

    /// 一份 WDA `/source?format=json` 的记录样本(形状取自 WebDriverAgent 的 JSON source:
    /// `{"value": <节点>}`, 节点带 type/name/label/rect/enabled/visible/accessible/children)。
    const SOURCE: &str = r#"{"value":{"type":"XCUIElementTypeApplication","name":"Settings",
      "label":"Settings","rect":{"x":0,"y":0,"width":390,"height":844},
      "enabled":true,"visible":true,"accessible":false,"children":[
        {"type":"XCUIElementTypeTable","name":"","label":"","rect":{"x":0,"y":100,"width":390,"height":700},
         "enabled":true,"visible":true,"accessible":false,"children":[
           {"type":"XCUIElementTypeCell","name":"Wi-Fi","label":"Wi-Fi",
            "rect":{"x":0,"y":150,"width":390,"height":44},
            "enabled":true,"visible":true,"accessible":true,"children":[
              {"type":"XCUIElementTypeStaticText","name":"","label":"Wi-Fi",
               "rect":{"x":20,"y":160,"width":50,"height":24},
               "enabled":true,"visible":true,"accessible":false,"children":[]}
            ]},
           {"type":"XCUIElementTypeSwitch","name":"","label":"","value":"1",
            "rect":{"x":330,"y":200,"width":51,"height":31},
            "enabled":true,"visible":true,"accessible":true,"children":[]}
         ]}
      ]}}"#;

    #[test]
    fn source_parses_into_document_order_with_depth() {
        let full = parse_wda_source(SOURCE);
        // 5 个节点: Application / Table / Cell / StaticText / Switch
        assert_eq!(full.len(), 5, "文档序展开的节点数: {:#?}", full.iter().map(|n| (&n.class, n.depth)).collect::<Vec<_>>());
        assert_eq!(full[0].class, "XCUIElementTypeApplication");
        assert_eq!(full[0].depth, 0);
        assert_eq!(full[1].class, "XCUIElementTypeTable");
        assert_eq!(full[1].depth, 1);
        assert_eq!(full[2].depth, 2);
        assert_eq!(full[3].depth, 3, "StaticText 嵌在 Cell 里");
        // depth 序列必须能过 fold::tree_of 的「最近更浅前驱即父亲」契约
        let (_, roots) = crate::fold::tree_of(&full);
        assert_eq!(roots, vec![0], "只有 Application 是根");
    }

    #[test]
    fn source_reads_boxes_and_flags() {
        let full = parse_wda_source(SOURCE);
        let cell = full.iter().find(|n| n.class == "XCUIElementTypeCell").unwrap();
        assert_eq!(cell.t, "Wi-Fi");
        assert_eq!(cell.b, [0, 150, 390, 194], "rect 换算成 [x1,y1,x2,y2]");
        assert!(cell.clickable, "Cell 是可点的控件类型");
        assert!(cell.scrollable == false);
        let table = full.iter().find(|n| n.class == "XCUIElementTypeTable").unwrap();
        assert!(table.scrollable, "Table 可滚");
        assert!(!table.clickable, "Table 不是可点控件");
        let sw = full.iter().find(|n| n.class == "XCUIElementTypeSwitch").unwrap();
        assert!(sw.checkable && sw.checked, "value=1 即开");
        assert!(sw.clickable);
        // 图片/纯文本容器不标可点(推导保守, 见 is_actionable 的说明)
        let st = full.iter().find(|n| n.class == "XCUIElementTypeStaticText").unwrap();
        assert!(!st.clickable, "StaticText 不算控件");
    }

    #[test]
    fn source_accepts_flat_rect_and_rejects_garbage() {
        // 少数版本把 x/y/width/height 平铺在节点上
        let flat = r#"{"value":{"type":"XCUIElementTypeButton","label":"OK","x":10,"y":20,"width":30,"height":40,"enabled":true,"visible":true,"accessible":true}}"#;
        let full = parse_wda_source(flat);
        assert_eq!(full[0].b, [10, 20, 40, 60]);
        assert!(full[0].clickable);
        // 不是 JSON: 空树, 不是 panic
        assert!(parse_wda_source("<xml/>").is_empty());
        // 没有 rect 也没有平铺坐标: 框全 0, 但节点仍在(如实, 不丢)
        let bare = r#"{"value":{"type":"XCUIElementTypeOther","label":"x"}}"#;
        assert_eq!(parse_wda_source(bare)[0].b, [0, 0, 0, 0]);
    }

    #[test]
    fn els_contract_matches_android_truncation() {
        let long = "字".repeat(60);
        let full = vec![FullNode {
            t: long.clone(),
            b: [1, 2, 3, 4],
            id: None,
            class: "XCUIElementTypeStaticText".into(),
            clickable: false,
            scrollable: false,
            checkable: false,
            checked: false,
            depth: 0,
        }];
        let els = els_from_full(&full);
        assert_eq!(els.len(), 1);
        assert_eq!(els[0].t.chars().count(), ELS_TEXT_CAP, "与安卓 els 的 40 字截断对齐");
        assert_eq!(els[0].b, [1, 2, 3, 4]);
        // 无文字的节点不进 els(与安卓侧同契约)
        let mut f2 = full.clone();
        f2.push(FullNode { t: String::new(), ..full[0].clone() });
        assert_eq!(els_from_full(&f2).len(), 1);
    }

    #[test]
    fn window_size_and_apps_parse() {
        let v = json!({"value": {"width": 390.0, "height": 844.0}});
        assert_eq!(parse_window_size(&v), Some((390, 844)));
        assert_eq!(parse_window_size(&json!({"value": {"width": 0, "height": 844}})), None);
        let a = json!({"value": [{"bundleId": "com.apple.Preferences", "name": "设置"}]});
        assert_eq!(parse_apps(&a), vec![("com.apple.Preferences".to_string(), "设置".to_string())]);
        assert!(parse_apps(&json!({"value": []})).is_empty());
        let act = json!({"value": {"bundleId": "com.apple.mobilesafari", "pid": 42}});
        assert_eq!(parse_active_app(&act).as_deref(), Some("com.apple.mobilesafari"));
        assert_eq!(parse_active_app(&json!({"value": {}})), None);
    }

    #[test]
    fn base64_decoder_roundtrips_and_refuses_garbage() {
        // "hello" 的 base64
        assert_eq!(b64_decode("aGVsbG8=").unwrap(), b"hello");
        // PNG 魔数 + 无填充
        assert_eq!(b64_decode("iVBORw0KGgo").unwrap()[..4], [0x89, 0x50, 0x4e, 0x47]);
        // 换行与空白照吃(WDA 有时会折行)
        assert_eq!(b64_decode("aGVs\nbG8=").unwrap(), b"hello");
        // 非法字符拒绝, 不猜
        assert!(b64_decode("aGVs*bG8=").is_none());
    }

    #[test]
    fn source_shape_matches_the_parser_on_a_good_page() {
        let s = analyze_source_shape(SOURCE);
        assert_eq!(s.nodes, 5);
        assert_eq!(s.with_type, 5, "每个节点都带 type");
        assert_eq!(s.with_text, 4, "Application/Table 的 label 是空的: Application 有 label=Settings…");
        assert_eq!(s.with_rect, 5);
        assert!(!s.flat_rect, "用的是 rect 对象形态");
        assert_eq!(s.nonempty_box, 5, "所有框都有面积");
        assert!(s.ok(), "合格页面不该报问题: {:?}", s.problems());
    }

    #[test]
    fn source_shape_calls_out_each_kind_of_mismatch() {
        // 解析不出东西: 形状不匹配 —— 这正是本后端唯一未验证的那个假设
        let s = analyze_source_shape("<xml/>");
        assert_eq!(s.nodes, 0);
        assert!(!s.ok());
        assert!(s.problems()[0].contains("形状与解析器不匹配"));

        // 有节点但没有任何 type: 可点/可滚的推导全落空
        let s2 = analyze_source_shape(r#"{"value":{"label":"x","rect":{"x":0,"y":0,"width":1,"height":1}}}"#);
        assert!(s2.problems().iter().any(|p| p.contains("`type`")), "{:?}", s2.problems());

        // 有节点但没坐标
        let s3 = analyze_source_shape(r#"{"value":{"type":"XCUIElementTypeButton","label":"x"}}"#);
        assert!(s3.problems().iter().any(|p| p.contains("带坐标")), "{:?}", s3.problems());

        // 坐标全零面积: 元素会全叠在左上角
        let s4 = analyze_source_shape(
            r#"{"value":{"type":"XCUIElementTypeButton","label":"x","rect":{"x":0,"y":0,"width":0,"height":0}}}"#,
        );
        assert!(s4.problems().iter().any(|p| p.contains("零面积")), "{:?}", s4.problems());

        // 平铺形态要被认出来(而不是当成没有坐标)
        let flat = r#"{"value":{"type":"XCUIElementTypeButton","label":"OK","x":1,"y":2,"width":3,"height":4}}"#;
        let s5 = analyze_source_shape(flat);
        assert!(s5.flat_rect && s5.ok(), "{:?}", s5);
    }

    #[test]
    fn source_shape_counts_recursively() {
        // 嵌套: 只数根是不行的, 得下去
        let nested = r#"{"value":{"type":"XCUIElementTypeApplication","rect":{"x":0,"y":0,"width":10,"height":10},"children":[{"type":"XCUIElementTypeButton","label":"a","rect":{"x":0,"y":0,"width":5,"height":5}},{"type":"XCUIElementTypeButton","label":"b","rect":{"x":0,"y":5,"width":5,"height":5}}]}}"#;
        let s = analyze_source_shape(nested);
        assert_eq!(s.nodes, 3);
        assert_eq!(s.with_children, 1);
        assert!(s.ok(), "{:?}", s.problems());
    }

    #[test]
    fn ios_can_only_be_used_to_look_and_touch() {
        let ls = IOS_LAYERS;
        for want in [Layer::Shader, Layer::Gray, Layer::Sysparam, Layer::Measure] {
            let n = ls.iter().filter(|l| l.layer == want).count();
            assert_eq!(n, 1, "{} 的声明有 {n} 条", want.id());
        }
        for l in &ls {
            assert!(!l.why.is_empty() && !l.needs.is_empty(), "{l:?}");
        }
        // 三层改动面全关: iOS 没有 root/shell/Vulkan 注入点
        for l in ls.iter().filter(|l| l.layer != Layer::Measure) {
            assert_eq!(l.support, Support::No, "{l:?}");
        }
        // 测量是未知而不是「不行」—— WDA 不提供是真的, 有没有别的来源没验过
        let m = ls.iter().find(|l| l.layer == Layer::Measure).unwrap();
        assert_eq!(m.support, Support::Unknown);
    }
}
