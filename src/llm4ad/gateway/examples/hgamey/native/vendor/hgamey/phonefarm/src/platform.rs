//! 设备接口: 新增一种设备或平台 = 实现 [`Backend`] + 在 [`open`] 里登记一个 serial 前缀。
//!
//! ```text
//!   调用方 (run / bench / eval / perf / fleet / cts ...)
//!        │  一律持 device::Device
//!        ▼
//!   Device ──► Box<dyn Backend> ──┬─ device::Adb   (Android,      serial 不带前缀)
//!                                 ├─ device::Hdc   (OpenHarmony,  serial = "hdc:<key>")
//!                                 └─ 你的实现      (serial = "<前缀>:<key>")
//! ```
//!
//! [`Backend`] 分两段:
//!
//! - **必须实现**: 连接与 shell、文件进出、截图与界面树、输入、应用启停、遥测。
//!   phonefarm 的 `run` / `bench` / `perf` / `fleet` / `cts` 都只靠这一段。
//! - **可选**(有缺省实现, 缺省 = 「这个平台没有」并如实报错): 端口转发、内存截图、
//!   虚拟手柄。缺省实现返回 `Err` / `None`, 不假装成功。
//!
//! 每个平台还要回答「三层改动面各自能不能开、为什么」([`Backend::layers`]),
//! 这份声明给上层 (game_opt_loop 的摸底与计划) 和人看; 真正开不开仍以真机摸底为准。
//!
//! 现状里还有几处没走这个接口、直接调 adb 的 (测评的采样进程、截图快路、fleet 的
//! 设备枚举、keepalive), 清单与原因见 `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/DEVICES.md`。

use crate::device::{Adb, FullNode, Hdc, Node, SysState};

/// 三层改动面 + 测量。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Layer {
    /// ① 画面算子 (超分 / 插帧 compute shader)。
    Shader,
    /// ② 画面流程改写 (Vulkan layer)。
    Gray,
    /// ③ 系统参数 (sysfs / settings)。
    Sysparam,
    /// 只测不改: 帧时 / 帧率 / 功耗 / 温度。
    Measure,
}

impl Layer {
    pub fn id(self) -> &'static str {
        match self {
            Layer::Shader => "shader",
            Layer::Gray => "gray",
            Layer::Sysparam => "sysparam",
            Layer::Measure => "measure",
        }
    }
}

/// 一层在某个平台上的可行性。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Support {
    /// 已在真机上走通。
    Yes,
    /// 满足 `needs` 时可以 (要不要满足由真机摸底判定)。
    IfMet,
    /// 这个平台上做不了 (原因见 `why`)。
    No,
    /// 没有设备验证过, 不知道。
    Unknown,
}

impl Support {
    pub fn id(self) -> &'static str {
        match self {
            Support::Yes => "yes",
            Support::IfMet => "if_met",
            Support::No => "no",
            Support::Unknown => "unknown",
        }
    }
}

/// 「这一层在这个平台上能不能开」的一条声明。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LayerSupport {
    pub layer: Layer,
    pub support: Support,
    /// 前提 (例如「root」「目标应用已装」)。
    pub needs: &'static str,
    /// 为什么。
    pub why: &'static str,
}

/// 一种设备后端。方法语义与 `device::Adb` 上的同名方法一致 (那是第一个实现, 也是参照)。
pub trait Backend: Send + Sync {
    // ── 身份 ──
    /// 传输名: "adb" / "hdc" / ...  (CTS 日志通道等按它选路)。
    fn transport(&self) -> &'static str;
    /// 平台名: "android" / "openharmony" / ...
    fn platform(&self) -> &'static str;
    /// 三层改动面在这个平台上的可行性声明。
    fn layers(&self) -> Vec<LayerSupport>;

    // ── 连接 ──
    /// done 定局后置位: 之后不再发任何改设备状态的命令。
    fn set_frozen(&self);
    fn health_check(&self, ms: u64) -> bool;
    fn revive(&self, emulator_cmd: &str) -> bool;

    // ── shell 与文件 ──
    fn shell(&self, cmd: &str, ms: u64) -> String;
    fn stream_shell(&self, cmd: &str) -> std::io::Result<std::process::Child>;
    fn push_file(&self, local: &str, remote: &str) -> bool;
    fn pull(&self, remote: &str, local: &str) -> bool;

    // ── 看 ──
    fn els_full(&self, timeout_ms: u64) -> (Vec<Node>, Vec<FullNode>, String, String);
    fn foreground_pkg(&self) -> String;
    fn sys_state(&self) -> SysState;
    fn clipboard(&self) -> Option<String>;
    fn launchable_apps(&self) -> Vec<(String, String)>;
    fn screen(&self, out_jpg: &str) -> Option<(i32, i32)>;
    fn quick_thumb(&self, tag: &str) -> Option<Vec<u8>>;
    fn size(&self) -> (i32, i32);

    // ── 动 ──
    fn tap(&self, x: i32, y: i32);
    fn swipe(&self, x: i32, y: i32, x2: i32, y2: i32);
    fn scroll_down(&self);
    fn scroll_up(&self);
    fn type_text(&self, text: &str);
    fn clear_field(&self);
    fn back(&self);
    fn home(&self);

    // ── 应用 ──
    fn force_stop(&self, pkg: &str);
    /// 冷启动毫秒; 采不到为 None。
    fn launch(&self, pkg: &str, comp: &str) -> Option<i64>;

    // ── 遥测 ──
    fn telemetry_setup(&self, pkg: &str) -> bool;
    fn telemetry(&self, heavy: bool, pkg: &str) -> crate::telemetry::Telemetry;

    // ── 可选: 缺省 = 这个平台没有 ──
    fn screen_image(&self) -> Option<image::DynamicImage> {
        None
    }
    fn forward(&self, _local: u16, _remote: u16) -> Result<(), String> {
        Err(format!("{} 后端未实现端口转发", self.transport()))
    }
    fn forward_remove(&self, _local: u16) {}
    fn gamepad_press(&self, _btn: &str, _duration_ms: u64) -> Result<(), String> {
        Err(no_gamepad(self.transport()))
    }
    fn gamepad_down(&self, _btn: &str) -> Result<(), String> {
        Err(no_gamepad(self.transport()))
    }
    fn gamepad_up(&self, _btn: &str) -> Result<(), String> {
        Err(no_gamepad(self.transport()))
    }
    fn gamepad_stick(&self, _stick: &str, _x: f32, _y: f32, _duration_ms: u64) -> Result<(), String> {
        Err(no_gamepad(self.transport()))
    }
    fn gamepad_trigger(&self, _trigger: &str, _val: f32, _duration_ms: u64) -> Result<(), String> {
        Err(no_gamepad(self.transport()))
    }
    fn gamepad_dpad(&self, _dir: &str, _duration_ms: u64) -> Result<(), String> {
        Err(no_gamepad(self.transport()))
    }
    /// 没有手柄就没有要复位的东西: 缺省成功。
    fn gamepad_reset(&self) -> Result<(), String> {
        Ok(())
    }
    fn gamepad_close(&self) -> Result<(), String> {
        Ok(())
    }
    fn gamepad_wander(&self, _duration_ms: u64) -> Result<(), String> {
        Err(no_gamepad(self.transport()))
    }
}

fn no_gamepad(transport: &str) -> String {
    format!("{} 设备暂不支持虚拟手柄注入", transport.to_uppercase())
}

/// serial → 后端。新平台在这里登记前缀; 不带已登记前缀的 (含 None) 一律 Android adb,
/// 与接口化之前一字不差。
pub fn open(serial: Option<String>, tmp: String) -> Box<dyn Backend> {
    match serial {
        Some(s) if s.starts_with("hdc:") => {
            let key = s["hdc:".len()..].trim().to_string();
            Box::new(Hdc::new(if key.is_empty() { None } else { Some(key) }, tmp))
        }
        // iOS: 构造不碰设备(隧道与 session 都是首次用到时才建), 所以这条分支与
        // 上面两条一样是纯选择。
        Some(s) if s.starts_with("ios:") => {
            let udid = s["ios:".len()..].trim().to_string();
            Box::new(crate::ios::Ios::new(
                if udid.is_empty() { None } else { Some(udid) },
                tmp,
            ))
        }
        other => Box::new(Adb::new(other, tmp)),
    }
}

/// Android 上三层的可行性。依据: https://github.com/HGamey/docs/blob/main/repos/phonefarm/knobs/gray/FEASIBILITY.md 的真机结论,
/// 与 game_opt_loop optimize 在 NX809J 上的实跑。
pub const ANDROID_LAYERS: [LayerSupport; 4] = [
    LayerSupport {
        layer: Layer::Sysparam,
        support: Support::Yes,
        needs: "root",
        why: "sysfs / settings 写入要 root; 白名单靠真机「写入→读回→还原」探出来, 写了被内核弹回的不进白名单",
    },
    LayerSupport {
        layer: Layer::Gray,
        support: Support::Yes,
        needs: "root + 目标应用已装",
        why: "挂 Vulkan 层要 setprop debug.vulkan.layers 并把 .so 放进目标应用自己的 nativeLibraryDir, 两件都要 root; settings gpu_debug_* 那一套在实测机上不生效",
    },
    LayerSupport {
        layer: Layer::Shader,
        support: Support::IfMet,
        needs: "白盒载体 (refbench), 或 ② 的挂层通路",
        why: "算子要有挂载点: 白盒直接挂进管线; 黑盒游戏只能经 ② 的 Vulkan 层注入",
    },
    LayerSupport {
        layer: Layer::Measure,
        support: Support::Yes,
        needs: "功耗要停充 (root); ftrace 要 root",
        why: "帧时走 SurfaceFlinger/ftrace kgsl, 功耗走 sysfs 电源轨; 不停充时功耗如实为 null",
    },
];

/// OpenHarmony 上三层的可行性。
pub const OPENHARMONY_LAYERS: [LayerSupport; 4] = [
    LayerSupport {
        layer: Layer::Sysparam,
        support: Support::No,
        needs: "root",
        why: "国内鸿蒙商用机拿不到 root, hdc shell 是普通用户, sysfs 频率/调度节点写不进去",
    },
    LayerSupport {
        layer: Layer::Gray,
        support: Support::Unknown,
        needs: "root + 系统级 Vulkan 层加载开关 (未知)",
        why: "OpenHarmony 图形栈是否暴露等价的 layer 注入点没有设备验证过; 即便有, 往目标应用目录放 .so 同样要 root",
    },
    LayerSupport {
        layer: Layer::Shader,
        support: Support::No,
        needs: "白盒载体或 ② 的挂层通路",
        why: "黑盒游戏依赖 ② 的挂层通路 (不可用); refbench 没有鸿蒙版, 没有白盒载体",
    },
    LayerSupport {
        layer: Layer::Measure,
        support: Support::Yes,
        needs: "HiSmartPerf (SP_daemon)",
        why: "帧率/功耗/温度走 HiSmartPerf, 不需要 root; 只能测不能改",
    },
];

// ───────────────────────── 已有两种后端 → 接口 ─────────────────────────
// 方法体都在 device.rs 的 inherent impl 里, 这里只做转交。

macro_rules! forward_required {
    () => {
        fn set_frozen(&self) { Self::set_frozen(self) }
        fn health_check(&self, ms: u64) -> bool { Self::health_check(self, ms) }
        fn revive(&self, emulator_cmd: &str) -> bool { Self::revive(self, emulator_cmd) }
        fn shell(&self, cmd: &str, ms: u64) -> String { Self::shell(self, cmd, ms) }
        fn stream_shell(&self, cmd: &str) -> std::io::Result<std::process::Child> { Self::stream_shell(self, cmd) }
        fn push_file(&self, local: &str, remote: &str) -> bool { Self::push_file(self, local, remote) }
        fn pull(&self, remote: &str, local: &str) -> bool { Self::pull(self, remote, local) }
        fn els_full(&self, timeout_ms: u64) -> (Vec<Node>, Vec<FullNode>, String, String) { Self::els_full(self, timeout_ms) }
        fn foreground_pkg(&self) -> String { Self::foreground_pkg(self) }
        fn sys_state(&self) -> SysState { Self::sys_state(self) }
        fn clipboard(&self) -> Option<String> { Self::clipboard(self) }
        fn launchable_apps(&self) -> Vec<(String, String)> { Self::launchable_apps(self) }
        fn screen(&self, out_jpg: &str) -> Option<(i32, i32)> { Self::screen(self, out_jpg) }
        fn quick_thumb(&self, tag: &str) -> Option<Vec<u8>> { Self::quick_thumb(self, tag) }
        fn size(&self) -> (i32, i32) { Self::size(self) }
        fn tap(&self, x: i32, y: i32) { Self::tap(self, x, y) }
        fn swipe(&self, x: i32, y: i32, x2: i32, y2: i32) { Self::swipe(self, x, y, x2, y2) }
        fn scroll_down(&self) { Self::scroll_down(self) }
        fn scroll_up(&self) { Self::scroll_up(self) }
        fn type_text(&self, text: &str) { Self::type_text(self, text) }
        fn clear_field(&self) { Self::clear_field(self) }
        fn back(&self) { Self::back(self) }
        fn home(&self) { Self::home(self) }
        fn force_stop(&self, pkg: &str) { Self::force_stop(self, pkg) }
        fn launch(&self, pkg: &str, comp: &str) -> Option<i64> { Self::launch(self, pkg, comp) }
        fn telemetry_setup(&self, pkg: &str) -> bool { Self::telemetry_setup(self, pkg) }
        fn telemetry(&self, heavy: bool, pkg: &str) -> crate::telemetry::Telemetry { Self::telemetry(self, heavy, pkg) }
    };
}

impl Backend for Adb {
    fn transport(&self) -> &'static str { "adb" }
    fn platform(&self) -> &'static str { "android" }
    fn layers(&self) -> Vec<LayerSupport> { ANDROID_LAYERS.to_vec() }
    forward_required!();
    fn screen_image(&self) -> Option<image::DynamicImage> { Adb::screen_image(self) }
    fn forward(&self, local: u16, remote: u16) -> Result<(), String> { Adb::forward(self, local, remote) }
    fn forward_remove(&self, local: u16) { Adb::forward_remove(self, local) }
    fn gamepad_press(&self, btn: &str, duration_ms: u64) -> Result<(), String> { Adb::gamepad_press(self, btn, duration_ms) }
    fn gamepad_down(&self, btn: &str) -> Result<(), String> { Adb::gamepad_down(self, btn) }
    fn gamepad_up(&self, btn: &str) -> Result<(), String> { Adb::gamepad_up(self, btn) }
    fn gamepad_stick(&self, stick: &str, x: f32, y: f32, duration_ms: u64) -> Result<(), String> { Adb::gamepad_stick(self, stick, x, y, duration_ms) }
    fn gamepad_trigger(&self, trigger: &str, val: f32, duration_ms: u64) -> Result<(), String> { Adb::gamepad_trigger(self, trigger, val, duration_ms) }
    fn gamepad_dpad(&self, dir: &str, duration_ms: u64) -> Result<(), String> { Adb::gamepad_dpad(self, dir, duration_ms) }
    fn gamepad_reset(&self) -> Result<(), String> { Adb::gamepad_reset(self) }
    fn gamepad_close(&self) -> Result<(), String> { Adb::gamepad_close(self) }
    fn gamepad_wander(&self, duration_ms: u64) -> Result<(), String> { Adb::gamepad_wander(self, duration_ms) }
}

impl Backend for Hdc {
    fn transport(&self) -> &'static str { "hdc" }
    fn platform(&self) -> &'static str { "openharmony" }
    fn layers(&self) -> Vec<LayerSupport> { OPENHARMONY_LAYERS.to_vec() }
    forward_required!();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn serial_prefix_picks_the_backend() {
        assert_eq!(open(Some("hdc:5ce1227d".into()), "/tmp".into()).transport(), "hdc");
        assert_eq!(open(Some("hdc:".into()), "/tmp".into()).platform(), "openharmony");
        assert_eq!(open(Some("emulator-5554".into()), "/tmp".into()).transport(), "adb");
        assert_eq!(open(None, "/tmp".into()).platform(), "android");
        // iOS 经 WebDriverAgent; udid 可省(WDA 只有一台目标时不需要)
        assert_eq!(open(Some("ios:".into()), "/tmp".into()).transport(), "wda");
        assert_eq!(
            open(Some("ios:00008130-001A2B3C".into()), "/tmp".into()).platform(),
            "ios"
        );
    }

    #[test]
    fn every_platform_answers_for_every_layer_once() {
        for b in [
            open(None, "/tmp".into()),
            open(Some("hdc:x".into()), "/tmp".into()),
            open(Some("ios:x".into()), "/tmp".into()),
        ] {
            let ls = b.layers();
            for want in [Layer::Shader, Layer::Gray, Layer::Sysparam, Layer::Measure] {
                let n = ls.iter().filter(|l| l.layer == want).count();
                assert_eq!(n, 1, "{} 对 {} 的声明有 {n} 条", b.platform(), want.id());
            }
            assert!(ls.iter().all(|l| !l.why.is_empty() && !l.needs.is_empty()));
        }
    }

    /// 商用鸿蒙机没有 root: 三层都动不了, 只能测。
    #[test]
    fn openharmony_can_only_measure() {
        let ls = open(Some("hdc:x".into()), "/tmp".into()).layers();
        for l in &ls {
            match l.layer {
                Layer::Measure => assert_eq!(l.support, Support::Yes),
                _ => assert!(matches!(l.support, Support::No | Support::Unknown), "{l:?}"),
            }
        }
    }

    /// 可选能力的缺省实现如实报「没有」, 不假装成功。
    #[test]
    fn optional_capabilities_default_to_an_honest_no() {
        let oh = open(Some("hdc:x".into()), "/tmp".into());
        assert!(oh.forward(1, 2).unwrap_err().contains("hdc"));
        assert!(oh.gamepad_press("A", 10).unwrap_err().contains("HDC"));
        assert!(oh.gamepad_reset().is_ok());
        assert!(oh.screen_image().is_none());
    }
}
