//! 设备屏幕观战窗口 —— 零 Token，只读。
//!
//! 移植自 iPhone-use 的实时屏幕 widget
//! (<https://github.com/zhongerxin/iPhone-use>, MIT, © 2026 ZHONG XIN, commit 08ed5b0):
//! 它的 `ui/`（手机外壳、设备状态、操作光效、点击/拖动提示、刷新/主屏/截图按钮、
//! 暂停恢复、锁屏态区分）与 `server/wda_screen.py`（屏幕流不落盘、断开与暂停要分开）
//! 定义了这套东西该长什么样。**只借语义, 不搬代码** —— 那边是 TypeScript + Node,
//! 这边是一段自包含 HTML 加一个 std-only 的 HTTP 服务。登记见 THIRD_PARTY_NOTICES.md。
//!
//! # 它为什么存在
//!
//! 自动化跑起来之后看不见设备是最难受的一件事: 局在跑, 你只知道它在跑。
//! 这个窗口把「设备现在长什么样」和「harness 刚才点了哪」放在同一张图上。
//!
//! # 三条纪律
//!
//! 1. **只读**。每帧通过独立观察 worker 取得覆盖设备调用的实际租约；测量占用时暂缓。
//!    帧与帧之间释放租约，不因浏览器保持打开就占用设备。
//! 2. **但它不是免费的**。每次 `screencap` 会动 GPU 与 CPU；只有取得观察租约才采样。
//! 3. **不重放**。流断了就如实显示「断开」并继续重试取帧, 不去补历史帧。
//!    看到的就是当下 —— 与 `collect` 的不重放同源。
//!    只有未过期的观看订阅才触发采集。暂停/隐藏/关闭释放本页订阅；丢失关闭通知时
//!    最多再启动 5 秒采集。已开始的 worker 有 20 秒总上限；超时保留恢复屏障。

use crate::device::Device;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// 自包含的观战页面(单文件, 无外链、无构建步骤)。
const WIDGET_HTML: &str = include_str!("screen_widget.html");
/// 取帧间隔下限。再密就是拿设备当靶子打, 观战不该有那个代价。
const MIN_INTERVAL_MS: u64 = 200;
const DEFAULT_INTERVAL_MS: u64 = 700;
const DEFAULT_PORT: u16 = 8899;
/// 环形保留的动作条数(页面只画最近十几条, 留点余量给 since= 查询)。
const EVENT_KEEP: usize = 64;
const VIEWER_TTL: Duration = Duration::from_secs(5);
const VIEWER_HEARTBEAT_MS: u64 = 1000;
const MAX_VIEWERS: usize = 128;
const CAPTURE_TIMEOUT: Duration = Duration::from_secs(20);

// ══════════════ 共享状态 ══════════════

struct Shared {
    frame: Mutex<Option<Frame>>,
    viewers: Mutex<Viewers>,
    viewer_epoch: String,
    /// 设备像素尺寸(动作坐标按它归一化)
    dev_w: AtomicU64,
    dev_h: AtomicU64,
    serial: Mutex<String>,
    capture: Mutex<CaptureState>,
    interval_ms: u64,
    /// 动作喂给谁: None = 没接(m页面不画涟漪)
    feed: Option<Arc<Feed>>,
}

#[derive(Clone, serde::Serialize)]
struct CaptureState {
    state: &'static str,
    reason: Option<String>,
}

impl CaptureState {
    fn new(state: &'static str) -> Self { Self { state, reason: None } }
}

struct Frame {
    jpeg: Vec<u8>,
    seq: u64,
    at: Instant,
    at_ms: u64,
}

/// A viewer ID is a local subscription handle, not an authentication credential.
/// Only start creates one; a delayed heartbeat cannot resurrect a stopped/expired viewer.
#[derive(Default)]
struct Viewers {
    next_id: u64,
    expires: HashMap<u64, Instant>,
}

impl Viewers {
    fn active(&mut self, now: Instant) -> usize {
        self.expires.retain(|_, until| *until > now);
        self.expires.len()
    }

    fn start(&mut self, now: Instant) -> Option<u64> {
        if self.active(now) >= MAX_VIEWERS {
            return None;
        }
        self.next_id = self.next_id.checked_add(1)?;
        self.expires.insert(self.next_id, now + VIEWER_TTL);
        Some(self.next_id)
    }

    fn heartbeat(&mut self, id: u64, now: Instant) -> bool {
        self.active(now);
        if let Some(until) = self.expires.get_mut(&id) {
            *until = now + VIEWER_TTL;
            true
        } else {
            false
        }
    }

    fn stop(&mut self, id: u64) {
        self.expires.remove(&id);
    }
}

impl Shared {
    fn viewer_id(&self, query: &str) -> Option<u64> {
        query.split('&').find_map(|kv| {
            let value = kv.strip_prefix("id=")?;
            value.strip_prefix(&self.viewer_epoch)?.parse().ok()
        })
    }

    fn fresh_for(&self) -> Duration {
        Duration::from_millis(self.interval_ms.saturating_mul(3).max(2000))
    }

    /// The gate is shared by production capture and the fake-source tests below.
    /// Do not hold the viewer mutex across a device call: stop must remain responsive.
    fn sample(&self, now: Instant, capture: impl FnOnce() -> Result<Vec<u8>, crate::observation::Failure>) -> bool {
        if self.viewers.lock().unwrap().active(now) == 0 {
            *self.capture.lock().unwrap() = CaptureState::new("idle");
            return false;
        }
        *self.capture.lock().unwrap() = CaptureState::new("capturing");
        match capture() {
            Ok(jpeg) if !jpeg.is_empty() => {
                let mut frame = self.frame.lock().unwrap();
                let seq = frame.as_ref().map_or(1, |f| f.seq + 1);
                *frame = Some(Frame {
                    jpeg,
                    seq,
                    at: Instant::now(),
                    at_ms: now_ms(),
                });
                *self.capture.lock().unwrap() = CaptureState::new("ready");
            }
            result => {
                let failure = result.err().unwrap_or_else(|| capture_error("截图为空"));
                *self.capture.lock().unwrap() = CaptureState {
                    state: failure.state, reason: Some(failure.reason),
                };
            }
        }
        true
    }
}

fn capture_error(reason: impl Into<String>) -> crate::observation::Failure {
    crate::observation::Failure { state: "error", reason: reason.into() }
}

/// Each attempt owns one local directory; read/cleanup happens only after run has
/// observed the worker exit. HTTP handlers never invoke this path.
fn capture_sample(sh: &Shared, tmp: &str, selected: &mut Option<String>) -> Result<Vec<u8>, crate::observation::Failure> {
    let work = std::path::Path::new(tmp).join(format!("screen-frame-{}-{}", std::process::id(),
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
            .map_err(|e| capture_error(e.to_string()))?.as_nanos()));
    std::fs::create_dir(&work).map_err(|e| capture_error(format!("创建截图临时目录失败: {e}")))?;
    let result = (|| {
        let work = work.canonicalize().map_err(|e| capture_error(e.to_string()))?;
        let meta = capture_file(&work, selected.as_deref(),
            sh.dev_w.load(Ordering::Relaxed), sh.dev_h.load(Ordering::Relaxed), None)?;
        let path = work.join("capture.jpg");
        let width = meta["width"].as_u64().unwrap();
        let height = meta["height"].as_u64().unwrap();
        let serial = meta["serial"].as_str().unwrap();
        let jpeg = std::fs::read(path).map_err(|e| capture_error(format!("读取截图失败: {e}")))?;
        sh.dev_w.store(width, Ordering::Relaxed);
        sh.dev_h.store(height, Ordering::Relaxed);
        *sh.serial.lock().unwrap() = serial.into();
        *selected = Some(serial.into());
        if let Some(feed) = &sh.feed {
            feed.dev_w.store(width, Ordering::Relaxed);
            feed.dev_h.store(height, Ordering::Relaxed);
        }
        Ok(jpeg)
    })();
    let _ = std::fs::remove_dir_all(&work);
    result
}

/// Shared by the local screen service and the node's single-frame adapter.
fn capture_file(work: &std::path::Path, serial: Option<&str>, width: u64, height: u64,
    binding: Option<&crate::device_registry::DeviceBinding>)
    -> Result<Value, crate::observation::Failure>
{
    let mut args = vec!["--work-dir".into(), work.to_string_lossy().into_owned(),
        "--width".into(), width.to_string(), "--height".into(), height.to_string()];
    if let Some(serial) = serial { args.extend(["--serial".into(), serial.into()]); }
    let out = crate::observation::run_scoped("screen", &args, &crate::lease_storage::resolve(), CAPTURE_TIMEOUT, binding)?;
    if !out.status.success() {
        return Err(capture_error(format!("截图 worker 失败: {}", String::from_utf8_lossy(&out.stderr).trim())));
    }
    let mut meta: Value = serde_json::from_slice(&out.stdout)
        .map_err(|e| capture_error(format!("截图 worker 回包无效: {e}")))?;
    if meta["jpeg_path"].as_str() != work.join("capture.jpg").to_str() {
        return Err(capture_error("截图 worker 返回了非本次截图路径"));
    }
    for key in ["width", "height"] {
        if meta[key].as_u64().filter(|n| *n > 0 && *n <= i32::MAX as u64).is_none() {
            return Err(capture_error(format!("截图 worker 缺少有效{key}")));
        }
    }
    let observed = meta["serial"].as_str().filter(|v| !v.is_empty())
        .ok_or_else(|| capture_error("截图 worker 缺少实际设备身份"))?;
    if serial.is_some_and(|value| value != observed) {
        return Err(capture_error("截图 worker 设备身份不匹配"));
    }
    if meta["captured_at_ms"].as_u64().filter(|value| *value > 0).is_none() {
        return Err(capture_error("截图 worker 缺少采集时间"));
    }
    if let Some(binding) = binding {
        meta["lease_scope"] = json!("device");
        meta["device_binding"] = json!(binding);
    }
    Ok(meta)
}

fn run_capture(args: &[String]) -> i32 {
    let mut serial = None;
    let mut out = None;
    let mut binding = None;
    let mut it = args.iter();
    while let Some(flag) = it.next() {
        match flag.as_str() {
            "--serial" if serial.is_none() => serial = it.next().cloned(),
            "--out" if out.is_none() => out = it.next().cloned(),
            "--device-binding" if binding.is_none() => {
                let Some(value) = it.next() else { eprintln!("--device-binding requires JSON"); return 2; };
                binding = Some(value.clone());
            }
            "--json" => (),
            _ => { eprintln!("phonefarm screen capture --serial S --out NEW_DIR [--device-binding JSON] --json"); return 2; }
        }
    }
    let result = (|| {
        let serial = serial.filter(|s| !s.trim().is_empty())
            .ok_or_else(|| capture_error("explicit serial is required"))?;
        let binding = binding.as_deref().map(crate::device_registry::parse_binding)
            .transpose().map_err(capture_error)?;
        if binding.as_ref().is_some_and(|binding| binding.serial != serial) {
            return Err(capture_error("screen serial differs from device binding"));
        }
        let out = out.map(std::path::PathBuf::from)
            .ok_or_else(|| capture_error("new output directory is required"))?;
        std::fs::create_dir(&out).map_err(|e| capture_error(format!("new capture directory: {e}")))?;
        let work = out.canonicalize().map_err(|e| capture_error(e.to_string()))?;
        capture_file(&work, Some(&serial), 0, 0, binding.as_ref())
    })();
    match result {
        Ok(mut value) => {
            value["schema"] = json!("phonefarm/screen-capture@1");
            value["state"] = json!("ready");
            value["ownership"] = json!("observation-v1");
            println!("{value}"); 0
        }
        Err(error) => {
            println!("{}", json!({"schema":"phonefarm/screen-capture@1",
                "state":error.state,"reason":error.reason})); 2
        }
    }
}

/// Invoked only by the observation worker after the shared ownership barrier.
pub(crate) fn capture_worker(args: &[String]) -> i32 {
    if !crate::observation::in_worker() {
        eprintln!("screen capture requires an observation worker");
        return 2;
    }
    let capture = || -> Result<Value, String> {
        let mut serial = None;
        let mut work = None;
        let (mut width, mut height) = (0, 0);
        let mut it = args.iter();
        while let Some(flag) = it.next() {
            let value = it.next().ok_or_else(|| format!("missing value for {flag}"))?;
            match flag.as_str() {
                "--serial" => serial = Some(value.clone()),
                "--work-dir" => work = Some(std::path::PathBuf::from(value)),
                "--width" => width = value.parse::<i32>().map_err(|e| e.to_string())?,
                "--height" => height = value.parse::<i32>().map_err(|e| e.to_string())?,
                _ => return Err(format!("unknown capture argument {flag}")),
            }
        }
        let work = work.filter(|p| p.is_absolute() && p.is_dir()).ok_or("capture work directory unavailable")?;
        let serial = crate::device::resolve_serial(serial.as_deref()).ok_or("cannot resolve exactly one screen device")?;
        let phone = Device::new(Some(serial.clone()), work.to_string_lossy().into_owned());
        if width <= 0 || height <= 0 { (width, height) = phone.size(); }
        let path = work.join("capture.jpg");
        phone.screen(&path.to_string_lossy()).ok_or("screen capture failed")?;
        Ok(json!({"jpeg_path":path,"width":width,"height":height,"serial":serial,"captured_at_ms":now_ms()}))
    };
    match capture() {
        Ok(meta) => { println!("{meta}"); 0 }
        Err(e) => { eprintln!("{e}"); 1 }
    }
}

struct Feed {
    events: Mutex<Vec<Value>>,
    seq: AtomicU64,
    dev_w: AtomicU64,
    dev_h: AtomicU64,
}

impl Feed {
    fn push(&self, rec: &Value) {
        let dev_w = self.dev_w.load(Ordering::Relaxed) as f64;
        let dev_h = self.dev_h.load(Ordering::Relaxed) as f64;
        if dev_w == 0.0 || dev_h == 0.0 {
            return; // First subscription has not probed the device dimensions yet.
        }
        let num = |k: &str| rec.get(k).and_then(|v| v.as_f64());
        let a = rec
            .get("a")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if a.is_empty() {
            return;
        }
        let seq = self.seq.fetch_add(1, Ordering::Relaxed) + 1;
        // 坐标一律归一化到 0~1 —— 换算在服务端做, 页面不猜缩放。
        let mut ev = json!({"seq": seq, "a": a});
        if let Some(x) = num("x") {
            ev["x"] = json!((x / dev_w).clamp(0.0, 1.0));
        }
        if let Some(y) = num("y") {
            ev["y"] = json!((y / dev_h).clamp(0.0, 1.0));
        }
        if let Some(x2) = num("x2") {
            ev["x2"] = json!((x2 / dev_w).clamp(0.0, 1.0));
        }
        if let Some(y2) = num("y2") {
            ev["y2"] = json!((y2 / dev_h).clamp(0.0, 1.0));
        }
        for k in ["text", "what"] {
            if let Some(s) = rec.get(k).and_then(|v| v.as_str()) {
                ev[k] = json!(s);
            }
        }
        let mut e = self.events.lock().unwrap();
        e.push(ev);
        if e.len() > EVENT_KEEP {
            let drop = e.len() - EVENT_KEEP;
            e.drain(..drop);
        }
    }
    fn since(&self, after: u64) -> Vec<Value> {
        self.events
            .lock()
            .unwrap()
            .iter()
            .filter(|v| v["seq"].as_u64().unwrap_or(0) > after)
            .cloned()
            .collect()
    }
}

// ══════════════ 子命令 ══════════════

fn usage() -> &'static str {
    "用法: phonefarm screen capture --serial S --out NEW_DIR [--device-binding JSON] --json\n\
     \x20 或 phonefarm screen [--serial <设备>] [--port N] [--interval-ms N]\n\
     \x20                   [--actions <log.jsonl>] [--from-start] [--out <目录>]\n\
     \x20 在浏览器里看设备屏幕 + harness 刚才点了哪(零 Token, 只读)。\n\
     \x20 --interval-ms: 取帧间隔, 缺省 700, 下限 200\n\
     \x20 --actions    : 尾随一份 log.jsonl, 把 {\"r\":\"act\"} 记录画成点击/拖动提示\n\
     \x20 --from-start : 从头读动作(默认为实时, 只读新产生的)\n\
     \x20 --out        : 「存图」按钮把截图写到哪(缺省 <tmp>/screen-shots)\n\
     \x20 暂停/隐藏/关闭释放本页订阅；无观看者不采集，失联订阅 5 秒后过期。\n\
     \x20 screencap 会影响帧时与功耗；每帧取得观察租约，测量占用时自动暂缓。\n\
     \x20 帧间释放租约；单帧 worker 最多 20 秒，超时/执行不确定保留恢复屏障。"
}

pub fn run_screen(args: &[String]) -> i32 {
    if args.first().map(String::as_str) == Some("capture") {
        return run_capture(&args[1..]);
    }
    if args == ["capabilities", "--json"] {
        println!("{}", json!({"schema":"phonefarm/screen-capabilities@1",
            "capture_v1":true,"device_binding_v1":true,"ownership":"observation-v1","worker_timeout_seconds":20}));
        return 0;
    }
    let mut serial: Option<String> = None;
    let mut port = DEFAULT_PORT;
    let mut interval_ms = DEFAULT_INTERVAL_MS;
    let mut actions: Option<String> = None;
    let mut from_start = false;
    let mut out_dir: Option<String> = None;

    let mut it = args.iter();
    while let Some(a) = it.next() {
        let need = |it: &mut std::slice::Iter<String>, flag: &str| -> Option<String> {
            it.next().cloned().or_else(|| {
                eprintln!("{flag} 需要一个值");
                None
            })
        };
        match a.as_str() {
            "--serial" => serial = need(&mut it, "--serial"),
            "--port" => {
                port = need(&mut it, "--port")
                    .and_then(|v| v.parse().ok())
                    .unwrap_or(0);
                if port == 0 {
                    eprintln!("--port 需要一个端口号\n{}", usage());
                    return 2;
                }
            }
            "--interval-ms" => {
                interval_ms = need(&mut it, "--interval-ms")
                    .and_then(|v| v.parse().ok())
                    .unwrap_or(MIN_INTERVAL_MS)
                    .max(MIN_INTERVAL_MS);
            }
            "--actions" => actions = need(&mut it, "--actions"),
            "--from-start" => from_start = true,
            "--out" => out_dir = need(&mut it, "--out"),
            "-h" | "--help" => {
                println!("{}", usage());
                return 2;
            }
            other => {
                eprintln!("未知参数 {other}\n{}", usage());
                return 2;
            }
        }
    }

    let tmp = std::env::var("PHONEFARM_TMP").unwrap_or_else(|_| "/tmp/phonefarm".to_string());
    let _ = std::fs::create_dir_all(&tmp);
    let shots = out_dir.unwrap_or_else(|| format!("{tmp}/screen-shots"));
    let _ = std::fs::create_dir_all(&shots);

    // Bind before starting workers; a failed bind must not leave a sampler behind.
    let listener = match TcpListener::bind(("127.0.0.1", port)) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("端口 {port} 起不来: {e}");
            return 1;
        }
    };
    let feed = actions.as_ref().map(|_| {
        Arc::new(Feed {
            events: Mutex::new(Vec::new()),
            seq: AtomicU64::new(0),
            dev_w: AtomicU64::new(0),
            dev_h: AtomicU64::new(0),
        })
    });

    let sh = Arc::new(Shared {
        frame: Mutex::new(None),
        viewers: Mutex::new(Viewers::default()),
        viewer_epoch: format!(
            "{:x}-{:x}-",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ),
        dev_w: AtomicU64::new(0),
        dev_h: AtomicU64::new(0),
        serial: Mutex::new(serial.clone().unwrap_or_else(|| "自动".into())),
        capture: Mutex::new(CaptureState::new("idle")),
        interval_ms,
        feed: feed.clone(),
    });

    // ── 采集线程: 唯一碰设备的线程 ──
    {
        let sh = Arc::clone(&sh);
        let tmp = tmp.clone();
        std::thread::spawn(move || {
            let mut selected = serial;
            loop {
                let sampled = sh.sample(Instant::now(), || capture_sample(&sh, &tmp, &mut selected));
                std::thread::sleep(Duration::from_millis(if sampled {
                    sh.interval_ms
                } else {
                    100
                }));
            }
        });
    }

    // ── 动作线程: 尾随 log.jsonl, 把动作喂给页面 ──
    if let (Some(path), Some(feed)) = (actions.clone(), feed.clone()) {
        std::thread::spawn(move || {
            let mut pos = 0u64;
            if !from_start {
                pos = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
            }
            loop {
                // Do not consume --from-start records before the first viewer has
                // supplied device dimensions for coordinate normalization.
                if feed.dev_w.load(Ordering::Relaxed) == 0
                    || feed.dev_h.load(Ordering::Relaxed) == 0
                {
                    std::thread::sleep(Duration::from_millis(100));
                    continue;
                }
                if let Ok(mut f) = std::fs::File::open(&path) {
                    let len = f.metadata().map(|m| m.len()).unwrap_or(0);
                    if len < pos {
                        // 文件被换过(新的一局), 从头读 —— 不追旧文件的位置
                        pos = 0;
                    }
                    if len > pos {
                        use std::io::Seek;
                        if f.seek(std::io::SeekFrom::Start(pos)).is_ok() {
                            let mut br = BufReader::new(&mut f);
                            let mut line = String::new();
                            while br.read_line(&mut line).unwrap_or(0) > 0 {
                                if let Ok(v) = serde_json::from_str::<Value>(line.trim()) {
                                    if v.get("r").and_then(|r| r.as_str()) == Some("act") {
                                        feed.push(&v);
                                    }
                                }
                                line.clear();
                            }
                            pos = br.stream_position().unwrap_or(len);
                        }
                    }
                }
                std::thread::sleep(Duration::from_millis(300));
            }
        });
    }

    // ── HTTP: std-only, 够用就好 ──
    println!("设备屏幕观战窗口: http://127.0.0.1:{port}/");
    println!(
        "  等待观看订阅, 取帧间隔 {interval_ms}ms{}",
        match &actions {
            Some(p) => format!(
                ", 动作源 {p}{}",
                if from_start { " (从头)" } else { " (实时)" }
            ),
            None => ", 未接动作源(不画点击提示)".into(),
        }
    );
    println!(
        "  每帧持有观察租约，测量占用时暂缓；帧间释放。截图超时保留恢复屏障。"
    );
    println!("  Ctrl-C 退出。");

    for conn in listener.incoming() {
        match conn {
            Ok(c) => {
                let sh = Arc::clone(&sh);
                let shots = shots.clone();
                std::thread::spawn(move || {
                    let _ = handle(c, &sh, &shots);
                });
            }
            Err(_) => continue,
        }
    }
    0
}

fn now_ms() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

// ══════════════ HTTP ══════════════

fn handle(mut c: TcpStream, sh: &Shared, shots: &str) -> std::io::Result<()> {
    c.set_read_timeout(Some(Duration::from_secs(5)))?;
    let mut reader = BufReader::new(c.try_clone()?);
    let mut req_line = String::new();
    if reader.read_line(&mut req_line)? == 0 {
        return Ok(());
    }
    let mut parts = req_line.split_whitespace();
    let method = parts.next().unwrap_or("").to_string();
    let target = parts.next().unwrap_or("/").to_string();
    let mut origin = None;
    let mut host = None;
    let mut cross_site = false;
    loop {
        let mut h = String::new();
        if reader.read_line(&mut h)? == 0 || h == "\r\n" || h == "\n" {
            break;
        }
        if let Some((name, value)) = h.split_once(':') {
            match name.to_ascii_lowercase().as_str() {
                "origin" => origin = Some(value.trim().to_string()),
                "host" => host = Some(value.trim().to_string()),
                "sec-fetch-site" => cross_site = value.trim() == "cross-site",
                _ => {}
            }
        }
    }
    let (path, query) = match target.split_once('?') {
        Some((p, q)) => (p.to_string(), q.to_string()),
        None => (target.clone(), String::new()),
    };
    // Local read-only viewers are not public authentication. Prevent another web
    // origin from creating/renewing subscriptions via a form or sendBeacon.
    if method == "POST"
        && (cross_site
            || origin
                .as_ref()
                .is_some_and(|o| host.as_ref().is_none_or(|h| o != &format!("http://{h}"))))
    {
        return respond(&mut c, 403, "text/plain", b"same-origin request required");
    }

    match (method.as_str(), path.as_str()) {
        ("GET", "/") => {
            let cfg = json!({
                "interval_ms": sh.interval_ms,
                "actions_wired": sh.feed.is_some(),
                "heartbeat_ms": VIEWER_HEARTBEAT_MS,
                "viewer_ttl_ms": VIEWER_TTL.as_millis(),
                "fresh_for_ms": sh.fresh_for().as_millis(),
            });
            // 把配置注入页面(在 <head> 后面插一段, 不改页面的其余部分)
            let html = WIDGET_HTML.replace(
                "<head>",
                &format!("<head>\n<script>window.__PF__={cfg};</script>"),
            );
            respond(&mut c, 200, "text/html; charset=utf-8", html.as_bytes())
        }
        ("POST", "/watch/start") => {
            let id = sh.viewers.lock().unwrap().start(Instant::now());
            let Some(id) = id else {
                return respond(&mut c, 429, "text/plain", b"too many viewers");
            };
            let after_seq = sh.frame.lock().unwrap().as_ref().map_or(0, |f| f.seq);
            respond(
                &mut c,
                200,
                "application/json",
                json!({
                "id": format!("{}{id}", sh.viewer_epoch), "after_seq": after_seq,
                    "ttl_ms": VIEWER_TTL.as_millis(),
                })
                .to_string()
                .as_bytes(),
            )
        }
        ("POST", "/watch/heartbeat" | "/watch/stop") => {
            let Some(id) = sh.viewer_id(&query) else {
                return respond(
                    &mut c,
                    410,
                    "text/plain",
                    b"viewer unknown; subscribe again",
                );
            };
            let mut viewers = sh.viewers.lock().unwrap();
            if path == "/watch/stop" {
                viewers.stop(id); // Idempotent, including an already expired handle.
            } else if !viewers.heartbeat(id, Instant::now()) {
                return respond(
                    &mut c,
                    410,
                    "text/plain",
                    b"viewer expired; subscribe again",
                );
            }
            respond(&mut c, 200, "application/json", b"{\"ok\":true}")
        }
        ("GET", "/frame.jpg") => {
            let frame = sh.frame.lock().unwrap();
            let Some(f) = frame.as_ref() else {
                return respond(&mut c, 503, "text/plain", b"no frame yet");
            };
            let age = f.at.elapsed();
            if age >= sh.fresh_for() || query_u64(&query, "after").is_some_and(|seq| f.seq <= seq) {
                return respond(&mut c, 503, "text/plain", b"waiting for a fresh frame");
            }
            let headers = format!(
                "X-Frame-Seq: {}\r\nX-Frame-Age-Ms: {}\r\nX-Frame-Captured-At: {}\r\n",
                f.seq,
                age.as_millis(),
                f.at_ms
            );
            // Slow viewers must not hold up the producer while they receive a frame.
            let jpeg = f.jpeg.clone();
            drop(frame);
            respond_with_headers(&mut c, 200, "image/jpeg", &jpeg, &headers)
        }
        ("GET", "/state.json") => {
            let active = sh.viewers.lock().unwrap().active(Instant::now());
            let frame = sh.frame.lock().unwrap();
            let age_ms = frame.as_ref().map(|f| f.at.elapsed().as_millis());
            let capture = sh.capture.lock().unwrap().clone();
            let body = json!({
                "serial": sh.serial.lock().unwrap().clone(),
                "width": sh.dev_w.load(Ordering::Relaxed),
                "height": sh.dev_h.load(Ordering::Relaxed),
                "frames": frame.as_ref().map_or(0, |f| f.seq),
                "paused": active == 0,
                "active_viewers": active,
                "viewer_ttl_ms": VIEWER_TTL.as_millis(),
                "interval_ms": sh.interval_ms,
                "last_frame_ms": frame.as_ref().map(|f| f.at_ms),
                "frame_age_ms": age_ms,
                "frame_fresh": age_ms.is_some_and(|age| age < sh.fresh_for().as_millis()),
                "error": if capture.state == "error" { capture.reason.clone() } else { None },
                "capture": capture,
            });
            drop(frame);
            respond(&mut c, 200, "application/json", body.to_string().as_bytes())
        }
        ("GET", "/events.json") => {
            let after = query
                .split('&')
                .find_map(|kv| kv.strip_prefix("since="))
                .and_then(|v| v.parse::<u64>().ok())
                .unwrap_or(0);
            let events = sh.feed.as_ref().map(|f| f.since(after)).unwrap_or_default();
            respond(
                &mut c,
                200,
                "application/json",
                json!({"events": events}).to_string().as_bytes(),
            )
        }
        ("POST", "/screenshot") => {
            let jpeg = sh
                .frame
                .lock()
                .unwrap()
                .as_ref()
                .map(|f| f.jpeg.clone())
                .unwrap_or_default();
            if jpeg.is_empty() {
                return respond(
                    &mut c,
                    200,
                    "application/json",
                    json!({"ok": false, "reason": "还没有帧"})
                        .to_string()
                        .as_bytes(),
                );
            }
            let name = format!("shot-{}.jpg", chrono::Utc::now().format("%Y%m%d-%H%M%S"));
            let p = std::path::Path::new(shots).join(&name);
            let ok = std::fs::write(&p, &jpeg).is_ok();
            respond(
                &mut c,
                200,
                "application/json",
                json!({"ok": ok, "path": if ok { p.display().to_string() } else { String::new() }})
                    .to_string()
                    .as_bytes(),
            )
        }
        _ => respond(&mut c, 404, "text/plain; charset=utf-8", b"not found"),
    }
}

fn query_u64(query: &str, key: &str) -> Option<u64> {
    query.split('&').find_map(|kv| {
        let (k, v) = kv.split_once('=')?;
        (k == key).then(|| v.parse().ok()).flatten()
    })
}

fn respond(c: &mut TcpStream, code: u16, ctype: &str, body: &[u8]) -> std::io::Result<()> {
    respond_with_headers(c, code, ctype, body, "")
}

fn respond_with_headers(
    c: &mut TcpStream,
    code: u16,
    ctype: &str,
    body: &[u8],
    headers: &str,
) -> std::io::Result<()> {
    let reason = match code {
        200 => "OK",
        400 => "Bad Request",
        403 => "Forbidden",
        404 => "Not Found",
        410 => "Gone",
        429 => "Too Many Requests",
        503 => "Service Unavailable",
        _ => "OK",
    };
    let head = format!(
        "HTTP/1.1 {code} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\n\
         Cache-Control: no-store\r\nConnection: close\r\n{headers}\r\n",
        body.len()
    );
    c.write_all(head.as_bytes())?;
    c.write_all(body)?;
    c.flush()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shared() -> Shared {
        Shared {
            frame: Mutex::new(None),
            viewers: Mutex::new(Viewers::default()),
            viewer_epoch: "test-server-".into(),
            dev_w: AtomicU64::new(0),
            dev_h: AtomicU64::new(0),
            serial: Mutex::new("fake-device".into()),
            capture: Mutex::new(CaptureState::new("idle")),
            interval_ms: DEFAULT_INTERVAL_MS,
            feed: None,
        }
    }

    fn sample_count(sh: &Shared, at: Instant, count: &mut usize) -> bool {
        sh.sample(at, || {
            *count += 1;
            Ok(vec![1, 2, 3])
        })
    }

    #[test]
    fn no_viewer_means_no_device_work_then_pause_stops_the_source() {
        let sh = shared();
        let now = Instant::now();
        let mut captures = 0;
        assert!(!sample_count(&sh, now, &mut captures));
        assert!(sh.frame.lock().unwrap().is_none());
        assert_eq!(captures, 0);
        let id = sh.viewers.lock().unwrap().start(now).unwrap();
        assert!(sample_count(&sh, now, &mut captures));
        sh.viewers.lock().unwrap().stop(id);
        assert!(!sample_count(&sh, now, &mut captures));
        assert_eq!(captures, 1);
    }

    #[test]
    fn independent_viewers_do_not_pause_each_other() {
        let sh = shared();
        let now = Instant::now();
        let a = sh.viewers.lock().unwrap().start(now).unwrap();
        let b = sh.viewers.lock().unwrap().start(now).unwrap();
        let mut captures = 0;
        sh.viewers.lock().unwrap().stop(a);
        assert!(sample_count(&sh, now, &mut captures));
        assert_eq!(sh.viewers.lock().unwrap().active(now), 1);
        sh.viewers.lock().unwrap().stop(b);
        assert!(!sample_count(&sh, now, &mut captures));
        assert_eq!(captures, 1);
    }

    #[test]
    fn a_lost_close_expires_and_late_heartbeats_cannot_restart_capture() {
        let sh = shared();
        let now = Instant::now();
        let first = sh.viewers.lock().unwrap().start(now).unwrap();
        let mut captures = 0;
        assert!(sample_count(&sh, now, &mut captures));
        let expired = now + VIEWER_TTL;
        assert!(!sample_count(&sh, expired, &mut captures));
        assert!(!sh.viewers.lock().unwrap().heartbeat(first, expired));
        let second = sh.viewers.lock().unwrap().start(expired).unwrap();
        assert_ne!(first, second);
        assert!(sample_count(&sh, expired, &mut captures));
        sh.viewers.lock().unwrap().stop(second);
        assert!(!sh.viewers.lock().unwrap().heartbeat(second, expired));
        assert!(!sample_count(&sh, expired, &mut captures));
        assert_eq!(captures, 2);
    }

    #[test]
    fn heartbeats_extend_only_their_own_subscription_and_capacity_is_bounded() {
        let mut viewers = Viewers::default();
        let now = Instant::now();
        let a = viewers.start(now).unwrap();
        let _b = viewers.start(now).unwrap();
        assert!(viewers.heartbeat(a, now + Duration::from_secs(4)));
        assert_eq!(viewers.active(now + VIEWER_TTL), 1);
        assert_eq!(viewers.active(now + Duration::from_secs(9)), 0);
        for _ in 0..MAX_VIEWERS {
            assert!(viewers.start(now).is_some());
        }
        assert!(viewers.start(now).is_none());
        assert!(viewers.start(now + VIEWER_TTL).is_some());
    }

    /// Real local HTTP handlers with an in-memory frame source, never Device::new.
    fn request(sh: Arc<Shared>, method: &str, path: &str, origin: Option<&str>) -> String {
        use std::io::Read;
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let addr = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (conn, _) = listener.accept().unwrap();
            handle(conn, &sh, "unused-test-output").unwrap();
        });
        let mut client = TcpStream::connect(addr).unwrap();
        client
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let origin = origin
            .map(|s| format!("Origin: {s}\r\n"))
            .unwrap_or_default();
        write!(
            client,
            "{method} {path} HTTP/1.1\r\nHost: {addr}\r\n{origin}Content-Length: 0\r\n\r\n"
        )
        .unwrap();
        let mut response = String::new();
        client.read_to_string(&mut response).unwrap();
        server.join().unwrap();
        response
    }

    fn response_json(response: &str) -> Value {
        serde_json::from_str(response.split_once("\r\n\r\n").unwrap().1).unwrap()
    }

    #[test]
    fn http_subscription_lifecycle_and_read_requests_do_not_activate_capture() {
        let sh = Arc::new(shared());
        let state = request(sh.clone(), "GET", "/state.json", None);
        assert_eq!(response_json(&state)["active_viewers"], 0);
        assert!(request(sh.clone(), "GET", "/frame.jpg", None).starts_with("HTTP/1.1 503"));
        assert_eq!(sh.viewers.lock().unwrap().active(Instant::now()), 0);
        let started = request(sh.clone(), "POST", "/watch/start", None);
        let body = response_json(&started);
        let id = body["id"].as_str().unwrap();
        let beat = format!("/watch/heartbeat?id={id}");
        assert!(request(sh.clone(), "POST", &beat, None).starts_with("HTTP/1.1 200"));
        let stop = format!("/watch/stop?id={id}");
        assert!(request(sh.clone(), "POST", &stop, None).starts_with("HTTP/1.1 200"));
        assert!(request(sh.clone(), "POST", &stop, None).starts_with("HTTP/1.1 200"));
        assert!(request(sh.clone(), "POST", &beat, None).starts_with("HTTP/1.1 410"));
        assert_eq!(
            response_json(&request(sh.clone(), "GET", "/state.json", None))["active_viewers"],
            0
        );
        assert!(!sample_count(&sh, Instant::now(), &mut 0));
    }

    #[test]
    fn cached_or_old_frames_never_appear_as_fresh_after_reconnect() {
        let sh = Arc::new(shared());
        let now = Instant::now();
        sh.viewers.lock().unwrap().start(now).unwrap();
        sample_count(&sh, now, &mut 0);
        let before = request(sh.clone(), "GET", "/frame.jpg", None);
        assert!(before.contains("X-Frame-Seq: 1\r\n"));
        assert!(before.contains("X-Frame-Age-Ms:"));
        let started = response_json(&request(sh.clone(), "POST", "/watch/start", None));
        assert_eq!(started["after_seq"], 1);
        assert!(request(sh.clone(), "GET", "/frame.jpg?after=1", None).starts_with("HTTP/1.1 503"));
        sh.frame.lock().unwrap().as_mut().unwrap().at = Instant::now() - sh.fresh_for();
        assert!(request(sh.clone(), "GET", "/frame.jpg", None).starts_with("HTTP/1.1 503"));
        let state = response_json(&request(sh, "GET", "/state.json", None));
        assert_eq!(state["frame_fresh"], false);
    }

    #[test]
    fn lease_deferral_and_capture_failure_keep_the_last_frame_and_its_age() {
        let sh = Arc::new(shared());
        let now = Instant::now();
        let viewer = sh.viewers.lock().unwrap().start(now).unwrap();
        sample_count(&sh, now, &mut 0);
        let captured_at = sh.frame.lock().unwrap().as_ref().unwrap().at_ms;
        for state in ["deferred", "error"] {
            assert!(sh.sample(now, || Err(crate::observation::Failure {
                state, reason: "fixture reason".into(),
            })));
            let response = response_json(&request(sh.clone(), "GET", "/state.json", None));
            assert_eq!(response["capture"]["state"], state);
            assert_eq!(response["capture"]["reason"], "fixture reason");
            assert_eq!(response["error"].is_string(), state == "error");
            assert_eq!(response["frames"], 1);
            assert_eq!(response["last_frame_ms"], captured_at);
        }
        sh.viewers.lock().unwrap().stop(viewer);
        assert!(!sh.sample(now, || panic!("no viewer must not acquire ownership")));
        assert_eq!(response_json(&request(sh, "GET", "/state.json", None))["capture"]["state"], "idle");
    }

    #[test]
    fn other_web_origins_cannot_start_sampling() {
        let sh = Arc::new(shared());
        let response = request(
            sh.clone(),
            "POST",
            "/watch/start",
            Some("https://unrelated.example"),
        );
        assert!(response.starts_with("HTTP/1.1 403"));
        assert_eq!(sh.viewers.lock().unwrap().active(Instant::now()), 0);
    }

    #[test]
    fn handles_from_a_previous_server_cannot_stop_or_renew_current_viewers() {
        let sh = Arc::new(shared());
        let id = sh.viewers.lock().unwrap().start(Instant::now()).unwrap();
        for action in ["stop", "heartbeat"] {
            let response = request(
                sh.clone(),
                "POST",
                &format!("/watch/{action}?id=previous-server-{id}"),
                None,
            );
            assert!(response.starts_with("HTTP/1.1 410"));
        }
        assert_eq!(sh.viewers.lock().unwrap().active(Instant::now()), 1);
    }

    #[test]
    fn the_widget_page_is_self_contained() {
        // 不能有外链: 观战窗口在离线设备网里也要能开
        assert!(WIDGET_HTML.contains("<html"), "得是一份完整 HTML");
        assert!(!WIDGET_HTML.contains("http://"), "不得引用外部资源");
        assert!(!WIDGET_HTML.contains("https://"), "不得引用外部资源");
        assert!(!WIDGET_HTML.contains("<script src"), "不得外链脚本");
        assert!(
            !WIDGET_HTML.contains("<link rel=\"stylesheet\""),
            "不得外链样式"
        );
        // 配置注入点必须存在, 否则服务端注不进去
        assert!(WIDGET_HTML.contains("<head>"), "注入点 <head> 必须存在");
        // 三条纪律要写在页面上, 不能只写在 Rust 注释里
        assert!(WIDGET_HTML.contains("screencap"), "页面要提醒它的代价");
        assert!(WIDGET_HTML.contains("只读"), "页面要写明只读");
    }

    fn feed() -> Feed {
        Feed {
            events: Mutex::new(Vec::new()),
            seq: AtomicU64::new(0),
            dev_w: AtomicU64::new(1080),
            dev_h: AtomicU64::new(2340),
        }
    }

    #[test]
    fn action_coordinates_are_normalized_server_side() {
        let f = feed();
        f.push(&json!({"r":"act","a":"tap","x":540,"y":1170}));
        f.push(&json!({"r":"act","a":"swipe","x":0,"y":2340,"x2":1080,"y2":0}));
        let ev = f.since(0);
        assert_eq!(ev.len(), 2);
        // 页面不猜缩放: 服务端已经换成 0~1
        assert_eq!(ev[0]["x"], 0.5);
        assert_eq!(ev[0]["y"], 0.5);
        assert_eq!(ev[1]["x"], 0.0);
        assert_eq!(ev[1]["y"], 1.0);
        assert_eq!(ev[1]["x2"], 1.0);
        assert_eq!(ev[1]["y2"], 0.0);
        assert_eq!(ev[1]["a"], "swipe");
    }

    #[test]
    fn out_of_range_coordinates_are_clamped_not_wrapped() {
        let f = feed();
        // 越界坐标(设备换了分辨率 / 坐标来自另一台机)不能画到框外去
        f.push(&json!({"r":"act","a":"tap","x":99999,"y":-50}));
        let ev = f.since(0);
        assert_eq!(ev[0]["x"], 1.0);
        assert_eq!(ev[0]["y"], 0.0);
    }

    #[test]
    fn only_action_records_become_events_and_seq_is_monotonic() {
        let f = feed();
        f.push(&json!({"r":"hook","kind":"budget"})); // 不是动作
        f.push(&json!({"r":"act"})); // 没有 a: 跳过
        f.push(&json!({"r":"act","a":"back"})); // 有 a 没坐标: 也算一条
        f.push(&json!({"r":"act","a":"type","text":"你好"}));
        let ev = f.since(0);
        assert_eq!(ev.len(), 2, "{ev:?}");
        assert_eq!(ev[0]["a"], "back");
        assert_eq!(ev[1]["text"], "你好");
        // since= 是「严格大于」, 所以拿最后一条的 seq 再问只该拿到空
        let last = ev[1]["seq"].as_u64().unwrap();
        assert!(f.since(last).is_empty());
    }

    #[test]
    fn the_ring_keeps_a_bounded_number_of_events() {
        let f = feed();
        for i in 0..(EVENT_KEEP + 20) {
            f.push(&json!({"r":"act","a":"tap","x":i,"y":i}));
        }
        let ev = f.since(0);
        assert_eq!(
            ev.len(),
            EVENT_KEEP,
            "环形缓冲必须有上限, 否则长时间观战会吃内存"
        );
        // 留下的是最新的那批
        assert!(ev[0]["seq"].as_u64().unwrap() > 1);
    }
}
