//! 原神场景插件 (Genshin Scenario Plugin)
//!
//! 本模块属于专用场景层, 是 `universal::ScenarioPlugin` 的一个实现。
//! 核心引擎不认识原神: 包名、界面特征与操作序列等一切专用知识都只在本插件内生效,
//! 卸下本插件后核心行为不受任何影响。
//!
//! 核心设计理念：
//! 1. 代码是给人看的，只是机器恰好可以运行 (高可读性、模块化状态机与完备注释)。
//! 2. 零人工干预的全自主闭环：
//!    - 自主会话生命周期：按需唤醒设备 -> 冷启动游戏 -> 自动穿过标题大门进入大世界。
//!    - 任务完成/退出时安全收尾：强制停止游戏进程 (杜绝游戏手机息屏保活与后台耗电)，随后自动锁屏休眠。
//!    - 纯宿主机生命周期管理：通过标准 ADB 管道流式传输手柄事件与只读遥测，手机端零常驻服务、零文件落盘。
//! 3. 原生手柄驱动与轻量视觉感知：
//!    - 剧情快速推进：高频识别底部字幕与右下角技能栏状态，注入按键 A 快速跳过对白。
//!    - 分支抉择：侦测右侧选项卡片，自动按 A 确认推进分支。
//!    - 交互触发：视野侦测 NPC、机关、调查提示，注入按键 X 触发交互。
//!    - 攀爬防卡死保护：监测体力消耗，超时主动按 B 松手脱困。
//!    - 任务指引导航：识别指引菱形方位，自动偏转右摇杆寻正朝向，推动左摇杆奔跑接近目标。

use std::thread::sleep;
use std::time::{Duration, Instant};
use image::{DynamicImage, GenericImageView};
use crate::device::Device;
use crate::universal::{FrameContext, InputMode, ScenarioPlugin, UniversalAction};

/// 本插件负责的应用包名。核心引擎不持有、也不得引用此常量。
pub const PACKAGE: &str = "com.miHoYo.Yuanshen";

/// The quest's device operations. Capture supplies owned, bounded transports;
/// the ordinary Device keeps its existing behavior.
pub trait QuestDevice {
    fn shell(&self, cmd: &str, ms: u64) -> String;
    fn screen_image(&self) -> Option<DynamicImage>;
    fn gamepad_press(&self, btn: &str, duration_ms: u64) -> Result<(), String>;
    fn gamepad_stick(&self, stick: &str, x: f32, y: f32, duration_ms: u64) -> Result<(), String>;
    fn gamepad_wander(&self, duration_ms: u64) -> Result<(), String>;
    fn gamepad_reset(&self) -> Result<(), String>;
    fn gamepad_close(&self) -> Result<(), String>;

    fn check(&self) -> Result<(), String> { Ok(()) }

    fn shell_write(&self, cmd: &str, ms: u64) -> Result<(), String> {
        self.shell(cmd, ms);
        Ok(())
    }
}

impl QuestDevice for Device {
    fn shell(&self, cmd: &str, ms: u64) -> String { Device::shell(self, cmd, ms) }
    fn screen_image(&self) -> Option<DynamicImage> { Device::screen_image(self) }
    fn gamepad_press(&self, btn: &str, duration_ms: u64) -> Result<(), String> {
        Device::gamepad_press(self, btn, duration_ms)
    }
    fn gamepad_stick(&self, stick: &str, x: f32, y: f32, duration_ms: u64) -> Result<(), String> {
        Device::gamepad_stick(self, stick, x, y, duration_ms)
    }
    fn gamepad_wander(&self, duration_ms: u64) -> Result<(), String> { Device::gamepad_wander(self, duration_ms) }
    fn gamepad_reset(&self) -> Result<(), String> { Device::gamepad_reset(self) }
    fn gamepad_close(&self) -> Result<(), String> { Device::gamepad_close(self) }
}

/// 原神游戏画面状态分类
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GenshinState {
    /// 登录/标题画面 (天空岛大门，需要点击进入游戏)
    TitleScreen,
    /// 剧情对话播放中 (底部字幕对白，右下角战斗技能栏隐藏)
    DialogueText,
    /// 对话分支选项出现 (右侧出现选项卡片)
    DialogueChoice,
    /// 视野内出现交互提示 (NPC / 采集物 / 机关，出现 X 键提示)
    InteractionPrompt,
    /// 角色处于攀爬状态 (身旁黄色体力圆弧出现)
    Climbing,
    /// 大世界正常探索 / 跑图状态 (右下角技能栏正常显示)
    OpenWorldExplore,
    /// 黑屏 / 过场 CG / 加载门转场中
    LoadingOrCutscene,
}

/// 单步输入策略；普通自动 prepare 与退出清理另按生命周期配置执行。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QuestMode {
    Auto,
    Navigate,
    Dialogue,
    Interact,
}

impl QuestMode {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "auto" => Ok(Self::Auto),
            "navigate" => Ok(Self::Navigate),
            "dialogue" => Ok(Self::Dialogue),
            "interact" => Ok(Self::Interact),
            _ => Err(format!("无效 quest mode {value:?}，应为 auto|navigate|dialogue|interact")),
        }
    }

    pub fn allows_step(self, state: GenshinState, auto_choice: bool) -> bool {
        if state == GenshinState::DialogueChoice && !auto_choice {
            return false;
        }
        match self {
            Self::Auto => true,
            Self::Navigate => matches!(state, GenshinState::OpenWorldExplore | GenshinState::Climbing),
            Self::Dialogue => matches!(state, GenshinState::DialogueText | GenshinState::DialogueChoice),
            Self::Interact => state == GenshinState::InteractionPrompt,
        }
    }

    pub fn allows_blind_interaction(self) -> bool {
        self == Self::Auto
    }

    pub fn allows_capture_back(self, state: GenshinState, auto_choice: bool) -> bool {
        self == Self::Auto && self.allows_step(state, auto_choice)
    }
}

/// Agent 运行参数配置
#[derive(Debug, Clone)]
pub struct QuestConfig {
    /// step 策略: auto | dialogue | navigate | interact；不限制普通自动 prepare。
    pub mode: String,
    /// 最大运行时长 (秒)
    pub max_seconds: u64,
    /// 遇到分支选项是否自动按 A 确认选择
    pub auto_choice: bool,
    /// 退出时是否强制杀死游戏并自动锁屏
    pub auto_shutdown: bool,
}

impl Default for QuestConfig {
    fn default() -> Self {
        Self {
            mode: "auto".to_string(),
            max_seconds: 1800,
            auto_choice: true,
            auto_shutdown: true,
        }
    }
}

/// 原神全自主剧情与任务 Agent
pub struct GenshinQuestAgent<'a> {
    device: &'a dyn QuestDevice,
    cfg: QuestConfig,
    last_climb: Option<Instant>,
    consecutive_dialogue_ticks: u32,
    consecutive_loading_ticks: u32,
    last_action_time: Instant,
}

impl<'a> GenshinQuestAgent<'a> {
    /// 创建 Agent 实例
    pub fn new(device: &'a dyn QuestDevice, cfg: QuestConfig) -> Self {
        Self {
            device,
            cfg,
            last_climb: None,
            consecutive_dialogue_ticks: 0,
            consecutive_loading_ticks: 0,
            last_action_time: Instant::now(),
        }
    }

    /// 1. 会话预检与环境拉起：唤醒屏幕、解锁并启动游戏直到加载进入大世界
    pub fn ensure_game_ready(&mut self) -> Result<(), String> {
        self.device.check()?;
        println!("[生命周期] 保持会话常亮策略 (svc power stayon true)...");
        self.device.shell_write("svc power stayon true", 3000)?;

        println!("[生命周期] 检查设备状态与屏幕唤醒...");
        let pwr = self.device.shell("dumpsys power | grep mWakefulness", 3000);
        self.device.check()?;
        if !pwr.contains("Awake") {
            println!("[生命周期] 设备处于休眠/息屏状态，正在自主唤醒并解锁屏幕 (KEYCODE_WAKEUP 224)...");
            self.device.shell_write("input keyevent 224 && sleep 0.5 && input swipe 608 2000 608 500", 5000)?;
            sleep(Duration::from_millis(1000));
        }

        // 检查原神进程是否在前台运行
        let window_info = self.device.shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'", 3000);
        self.device.check()?;
        if !window_info.contains(PACKAGE) {
            println!("[生命周期] 原神未在前台运行，正在执行冷启动...");
            self.device.shell_write(&format!("monkey -p {PACKAGE} -c android.intent.category.LAUNCHER 1"), 5000)?;
            self.wait_until_in_world()?;
        } else {
            println!("[生命周期] 原神已在前台运行，检查当前画面状态...");
            // 如果在前台但卡在标题或加载界面，同样执行进门引导
            let img = self.device.screen_image();
            self.device.check()?;
            if let Some(img) = img {
                let (state, _) = self.detect_state(&img);
                if state == GenshinState::TitleScreen || state == GenshinState::LoadingOrCutscene {
                    self.wait_until_in_world()?;
                }
            }
        }

        self.device.check()
    }

    /// 等待原神加载并通过标题大门进入大世界
    fn wait_until_in_world(&mut self) -> Result<(), String> {
        println!("[启动引导] 等待游戏加载并进入大世界...");
        let start = Instant::now();
        let timeout = Duration::from_secs(120);

        while start.elapsed() < timeout {
            self.device.check()?;
            sleep(Duration::from_millis(2500));
            let img = self.device.screen_image();
            self.device.check()?;
            let Some(img) = img else {
                continue;
            };

            let (state, _) = self.detect_state(&img);
            match state {
                GenshinState::OpenWorldExplore | GenshinState::DialogueText | GenshinState::DialogueChoice => {
                    println!("[启动引导] 检测到游戏主界面/剧情已就绪，正式交由 Agent 控制。");
                    return self.device.check();
                }
                GenshinState::TitleScreen => {
                    println!("[启动引导] 检测到标题天空岛大门，注入进门确认动作 (点击/A键)...");
                    // 点击横屏中央区域穿过大门
                    self.device.shell_write("input tap 1344 700", 2000)?;
                    let _ = self.device.gamepad_press("a", 150);
                }
                GenshinState::LoadingOrCutscene => {
                    println!("[启动引导] 场景转场或元素加载中，静默等待...");
                    // 偶尔点击防假死
                    let _ = self.device.gamepad_press("a", 100);
                }
                _ => {
                    // 其他过渡阶段，尝试点击中下部
                    self.device.shell_write("input tap 1344 700", 2000)?;
                }
            }
            self.device.check()?;
        }

        Err("冷启动进入原神大世界超时 (120s)".into())
    }

    /// 2. 视觉状态感知：从截屏图像中抽取 UI 特征并分类当前游戏所处状态
    pub fn detect_state(&self, img: &DynamicImage) -> (GenshinState, Option<f32>) {
        classify_state(img)
    }
}

/// 纯感知函数: 仅凭一帧画面判定原神所处状态, 不依赖设备与可变状态,
/// 因而可同时服务于独立 Agent 循环与 `ScenarioPlugin` 的单帧决策。
pub fn classify_state(img: &DynamicImage) -> (GenshinState, Option<f32>) {
    GenshinPerception::classify(img)
}

/// 大世界 HUD 是否在场 (小地图, 技能栏): capture 等只读消费者用它做"确实在大世界"的第二道证据
pub fn hud_present(img: &DynamicImage) -> (bool, bool) {
    let (w, h) = img.dimensions();
    (GenshinPerception::check_minimap_active(img, w, h), GenshinPerception::check_combat_ui_active(img, w, h))
}

/// 感知实现体 (无状态)
pub(crate) struct GenshinPerception;

impl GenshinPerception {
    fn classify(img: &DynamicImage) -> (GenshinState, Option<f32>) {
        let (w, h) = img.dimensions();
        if w < 100 || h < 100 {
            return (GenshinState::OpenWorldExplore, None);
        }

        // 检查是否整体黑屏或过场 CG (采样全局平均亮度)
        let mut sample_sum: u64 = 0;
        let mut sample_count: u64 = 0;
        for sx in (w / 4..3 * w / 4).step_by(32) {
            for sy in (h / 4..3 * h / 4).step_by(32) {
                let p = img.get_pixel(sx, sy);
                sample_sum += (p[0] as u64 + p[1] as u64 + p[2] as u64) / 3;
                sample_count += 1;
            }
        }
        let avg_luma = sample_sum / sample_count.max(1);
        if avg_luma < 12 {
            return (GenshinState::LoadingOrCutscene, None);
        }

        // 标题页硬签名 (2026-09-09 修正): 深色夜景版标题门 (avg_luma 不到 80) 靠亮度判不出来, 而右缘白色图标列
        // 会落进技能栏采样窗、蓝色天空又命中任务指引色, 整帧被误判成"大世界+目标偏右"。
        // 右缘五个圆形白图标 (设置/修复/扫码/公告/退出) 位置固定且大世界里不存在, 命中即标题页。
        if Self::check_title_icon_column(img, w, h) && !Self::check_minimap_active(img, w, h) {
            return (GenshinState::TitleScreen, None);
        }

        // 检查标题界面特征：中央门扉光效与天空岛背景
        // 标题界面通常上部明亮天蓝/白云，中下部有"点击进入游戏"
        // 并且此时绝对没有小地图 (左上角) 和技能栏 (右下角)
        let top_left_minimap = Self::check_minimap_active(img, w, h);
        let combat_ui_active = Self::check_combat_ui_active(img, w, h);

        if !top_left_minimap && !combat_ui_active {
            // 左上角没有小地图且右下角没有技能栏，判断是否在标题界面
            // 采样屏幕中心下方区域 (点击进入游戏文本区: x 40%~60%, y 65%~85%)
            let mut center_luma_sum = 0u64;
            let mut center_count = 0u64;
            for cx in ((w as f32 * 0.40) as u32..(w as f32 * 0.60) as u32).step_by(16) {
                for cy in ((h as f32 * 0.65) as u32..(h as f32 * 0.85) as u32).step_by(16) {
                    let p = img.get_pixel(cx, cy);
                    center_luma_sum += (p[0] as u64 + p[1] as u64 + p[2] as u64) / 3;
                    center_count += 1;
                }
            }
            let center_luma = center_luma_sum / center_count.max(1);
            if avg_luma > 80 && center_luma > 100 {
                return (GenshinState::TitleScreen, None);
            }
        }

        // 检查角色身旁黄色攀爬体力槽 (x 52%~65%, y 38%~62%)
        let mut yellow_count = 0;
        let mut stamina_total = 0;
        let s_x_start = (w as f32 * 0.52) as u32;
        let s_x_end = (w as f32 * 0.65) as u32;
        let s_y_start = (h as f32 * 0.38) as u32;
        let s_y_end = (h as f32 * 0.62) as u32;
        for x in (s_x_start..s_x_end).step_by(6) {
            for y in (s_y_start..s_y_end).step_by(6) {
                let p = img.get_pixel(x, y);
                stamina_total += 1;
                if p[0] > 180 && p[1] > 150 && p[2] < 90 {
                    yellow_count += 1;
                }
            }
        }
        let stamina_ratio = yellow_count as f32 / stamina_total.max(1) as f32;
        if stamina_ratio > 0.008 {
            return (GenshinState::Climbing, None);
        }

        // 检查右侧对话分支选项卡片 (白底或发光金色图标: x 60%~85%, y 35%~75%)
        let mut choice_box_pixels = 0;
        let mut choice_total = 0;
        let c_x_start = (w as f32 * 0.60) as u32;
        let c_x_end = (w as f32 * 0.85) as u32;
        let c_y_start = (h as f32 * 0.35) as u32;
        let c_y_end = (h as f32 * 0.75) as u32;
        for x in (c_x_start..c_x_end).step_by(8) {
            for y in (c_y_start..c_y_end).step_by(8) {
                let p = img.get_pixel(x, y);
                choice_total += 1;
                if (p[0] > 220 && p[1] > 220 && p[2] > 220) || (p[0] > 200 && p[1] > 180 && p[2] < 100) {
                    choice_box_pixels += 1;
                }
            }
        }
        let choice_ratio = choice_box_pixels as f32 / choice_total.max(1) as f32;

        // 若技能栏隐藏，判定进入了剧情模式
        if !combat_ui_active {
            if choice_ratio > 0.025 {
                return (GenshinState::DialogueChoice, None);
            } else {
                return (GenshinState::DialogueText, None);
            }
        }

        // 处于大世界探索时，检查中央偏右交互提示 (X 键 / NPC 对话提示: x 56%~72%, y 42%~60%)
        let mut interact_bright = 0;
        let mut interact_total = 0;
        let i_x_start = (w as f32 * 0.56) as u32;
        let i_x_end = (w as f32 * 0.72) as u32;
        let i_y_start = (h as f32 * 0.42) as u32;
        let i_y_end = (h as f32 * 0.60) as u32;
        for x in (i_x_start..i_x_end).step_by(6) {
            for y in (i_y_start..i_y_end).step_by(6) {
                let p = img.get_pixel(x, y);
                interact_total += 1;
                if p[0] > 235 && p[1] > 235 && p[2] > 235 {
                    interact_bright += 1;
                }
            }
        }
        let interact_ratio = interact_bright as f32 / interact_total.max(1) as f32;
        if interact_ratio > 0.005 {
            return (GenshinState::InteractionPrompt, None);
        }

        // 任务指引标记检测 (蓝绿色/金黄色导航菱形)
        let mut marker_x: Option<f32> = None;
        let m_y_start = (h as f32 * 0.15) as u32;
        let m_y_end = (h as f32 * 0.55) as u32;
        let mut max_cyan_score = 0;
        let mut best_x = 0;
        for x in (100..w - 100).step_by(12) {
            let mut col_cyan = 0;
            for y in (m_y_start..m_y_end).step_by(8) {
                let p = img.get_pixel(x, y);
                // 蓝绿色任务指引点特征
                if p[0] < 120 && p[1] > 180 && p[2] > 220 {
                    col_cyan += 1;
                }
            }
            if col_cyan > max_cyan_score && col_cyan >= 3 {
                max_cyan_score = col_cyan;
                best_x = x;
            }
        }
        if max_cyan_score >= 3 {
            marker_x = Some(best_x as f32 / w as f32);
        }

        (GenshinState::OpenWorldExplore, marker_x)
    }

    /// 标题页右缘白色圆形图标列: 5 个图标中心 x=91%, y=47%/57%/68%/79%/89%, 各取 3.4%x6% 的窗, 白覆盖 > 25% 记命中,
    /// 至少 4 个命中才算 (大世界右缘的队伍头像是彩色圆, 且位置不同)
    pub(crate) fn check_title_icon_column(img: &DynamicImage, w: u32, h: u32) -> bool {
        let mut hits = 0;
        for yc in [0.469f32, 0.572, 0.679, 0.786, 0.888] {
            let x_start = (w as f32 * 0.894) as u32;
            let x_end = (w as f32 * 0.928) as u32;
            let y_start = (h as f32 * (yc - 0.03)) as u32;
            let y_end = (h as f32 * (yc + 0.03)) as u32;
            let mut white = 0;
            let mut total = 0;
            for x in (x_start..x_end.min(w)).step_by(3) {
                for y in (y_start..y_end.min(h)).step_by(3) {
                    let p = img.get_pixel(x, y);
                    total += 1;
                    if p[0] > 200 && p[1] > 200 && p[2] > 200 {
                        white += 1;
                    }
                }
            }
            if total > 0 && white as f32 / total as f32 > 0.25 {
                hits += 1;
            }
        }
        hits >= 4
    }

    /// 检测右下角战斗技能栏是否处于激活状态
    pub(crate) fn check_combat_ui_active(img: &DynamicImage, w: u32, h: u32) -> bool {
        let mut combat_white = 0;
        let mut combat_total = 0;
        let x_start = (w as f32 * 0.82) as u32;
        let x_end = (w as f32 * 0.98) as u32;
        let y_start = (h as f32 * 0.72) as u32;
        let y_end = (h as f32 * 0.98) as u32;
        for x in (x_start..x_end).step_by(10) {
            for y in (y_start..y_end).step_by(10) {
                let p = img.get_pixel(x, y);
                combat_total += 1;
                if p[0] > 200 && p[1] > 200 && p[2] > 200 {
                    combat_white += 1;
                }
            }
        }
        let combat_ratio = combat_white as f32 / combat_total.max(1) as f32;
        combat_ratio > 0.005
    }

    /// 检测左上角小地图区域是否处于激活状态
    pub(crate) fn check_minimap_active(img: &DynamicImage, w: u32, h: u32) -> bool {
        let mut border_pixels = 0;
        let mut total = 0;
        let x_start = (w as f32 * 0.03) as u32;
        let x_end = (w as f32 * 0.15) as u32;
        let y_start = (h as f32 * 0.03) as u32;
        let y_end = (h as f32 * 0.22) as u32;
        for x in (x_start..x_end).step_by(8) {
            for y in (y_start..y_end).step_by(8) {
                let p = img.get_pixel(x, y);
                total += 1;
                // 小地图白色外边框与图标
                if p[0] > 180 && p[1] > 180 && p[2] > 180 {
                    border_pixels += 1;
                }
            }
        }
        let ratio = border_pixels as f32 / total.max(1) as f32;
        ratio > 0.02
    }

}

impl<'a> GenshinQuestAgent<'a> {
    /// 3. 单步决策与手柄动作执行
    pub fn step(&mut self) -> Result<(), String> {
        let mode = QuestMode::parse(&self.cfg.mode)?;
        self.device.check()?;
        let img = self.device.screen_image();
        self.device.check()?;
        let Some(img) = img else {
            sleep(Duration::from_millis(500));
            return self.device.check();
        };

        let (state, marker_x) = self.detect_state(&img);
        let result = self.execute_state(mode, state, marker_x);
        if result.is_ok() && !mode.allows_step(state, self.cfg.auto_choice) {
            // 独立 quest 循环也要在等待人工/状态变化时避免紧循环截图。
            sleep(Duration::from_millis(250));
        }
        result
    }

    fn execute_state(&mut self, mode: QuestMode, state: GenshinState, marker_x: Option<f32>) -> Result<(), String> {
        self.device.check()?;
        if !mode.allows_step(state, self.cfg.auto_choice) {
            println!("  [策略等待] {:?} 不对 {:?} 发送单步输入", mode, state);
            return self.device.check();
        }
        match state {
            GenshinState::TitleScreen => {
                self.consecutive_loading_ticks = 0;
                println!("  [进门引导] 处于登录标题画面 -> 注入按键 A 穿过大门进入游戏");
                self.device.gamepad_press("a", 150)?;
                sleep(Duration::from_millis(800));
            }
            GenshinState::DialogueText => {
                self.consecutive_loading_ticks = 0;
                self.consecutive_dialogue_ticks += 1;
                println!("  [剧情模式] 正在播放对话字幕 (连续第 {} 帧) -> 注入按键 A 快速推进", self.consecutive_dialogue_ticks);
                self.device.gamepad_press("a", 120)?;
                sleep(Duration::from_millis(250));
            }
            GenshinState::DialogueChoice => {
                self.consecutive_loading_ticks = 0;
                self.consecutive_dialogue_ticks += 1;
                println!("  [剧情模式] 检测到对话分支选项 -> 注入按键 A 确认选中分支");
                self.device.gamepad_press("a", 150)?;
                sleep(Duration::from_millis(400));
            }
            GenshinState::InteractionPrompt => {
                self.consecutive_loading_ticks = 0;
                self.consecutive_dialogue_ticks = 0;
                println!("  [交互模式] 检测到 NPC / 机关可交互提示 -> 注入按键 X 触发对话/调查");
                self.device.gamepad_press("x", 150)?;
                sleep(Duration::from_millis(600));
            }
            GenshinState::Climbing => {
                self.consecutive_loading_ticks = 0;
                println!("  [状态保护] 角色正处于攀爬消耗体力中 -> 下发按键 B 脱离爬墙");
                let now = Instant::now();
                if let Some(prev) = self.last_climb {
                    if now.duration_since(prev).as_secs() >= 3 {
                        // 攀爬卡死超过3秒，主动松手下落并向后移动脱困
                        self.device.gamepad_press("b", 150)?;
                        self.device.gamepad_stick("left", 0.0, 1.0, 400)?;
                        self.last_climb = None;
                    }
                } else {
                    self.last_climb = Some(now);
                }
                sleep(Duration::from_millis(400));
            }
            GenshinState::OpenWorldExplore => {
                self.consecutive_loading_ticks = 0;
                self.consecutive_dialogue_ticks = 0;
                self.last_climb = None;

                if let Some(mx) = marker_x {
                    // 视野中检测到任务指引标，根据偏移量分级调节右摇杆视角
                    if mx < 0.35 {
                        println!("  [跑图导航] 任务目标大幅偏左 ({:.1}%) -> 大幅左偏视角", mx * 100.0);
                        self.device.gamepad_stick("right", -0.85, 0.0, 350)?;
                    } else if mx < 0.44 {
                        println!("  [跑图导航] 任务目标轻微偏左 ({:.1}%) -> 轻微左偏视角", mx * 100.0);
                        self.device.gamepad_stick("right", -0.50, 0.0, 200)?;
                    } else if mx > 0.65 {
                        println!("  [跑图导航] 任务目标大幅偏右 ({:.1}%) -> 大幅右偏视角", mx * 100.0);
                        self.device.gamepad_stick("right", 0.85, 0.0, 350)?;
                    } else if mx > 0.56 {
                        println!("  [跑图导航] 任务目标轻微偏右 ({:.1}%) -> 轻微右偏视角", mx * 100.0);
                        self.device.gamepad_stick("right", 0.50, 0.0, 200)?;
                    } else {
                        println!("  [跑图导航] 目标正居中 ({:.1}%) -> 全速向前奔跑推进 (LS + B)", mx * 100.0);
                        self.device.gamepad_stick("left", 0.0, -1.0, 1600)?;
                        self.device.gamepad_press("b", 120)?;
                    }
                } else {
                    // 未直接看到任务点，平稳防烧屏巡航并搜索交互
                    println!("  [大世界巡航] 保持大世界走位与环视侦测");
                    self.device.gamepad_wander(3500)?;
                    if mode.allows_blind_interaction() {
                        self.device.gamepad_press("x", 120)?; // auto 盲交互周围掉落物/采集点
                    }
                }
            }
            GenshinState::LoadingOrCutscene => {
                self.consecutive_loading_ticks += 1;
                println!("  [场景转场] 画面暗转或过场加载中 (连续第 {} 帧) -> 等待并推进对白", self.consecutive_loading_ticks);
                // 偶尔注入按键 A 快速跳过转场字幕与对话卡片
                let _ = self.device.gamepad_press("a", 120);

                // 若连续黑屏超过 10 帧 (超 10 秒)，发送幂等唤醒键 (KEYCODE_WAKEUP 224) 确保屏幕被点亮，绝不触发翻转熄屏
                if self.consecutive_loading_ticks >= 10 && self.consecutive_loading_ticks % 5 == 0 {
                    println!("  [转场保活] 发送幂等点亮信号 (KEYCODE_WAKEUP 224)...");
                    self.device.shell_write("input keyevent 224", 2000)?;
                }
                sleep(Duration::from_millis(1000));
            }
        }

        self.last_action_time = Instant::now();
        self.device.check()
    }

    /// 4. 退出清理与安全锁屏：杀死游戏进程，关闭手柄输入，自动将手机熄屏休眠
    pub fn shutdown_and_lock(&mut self) {
        println!("[生命周期] 正在复位手柄并释放输入通道...");
        let _ = self.device.gamepad_reset();
        let _ = self.device.gamepad_close();

        println!("[生命周期] 恢复系统正常休眠策略 (svc power stayon false)...");
        let _ = self.device.shell_write("svc power stayon false", 3000);

        println!("[生命周期] 强制退出原神进程 (杜绝游戏息屏挂机与后台消耗)...");
        let _ = self.device.shell_write(&format!("am force-stop {PACKAGE}"), 5000);
        sleep(Duration::from_millis(800));

        println!("[生命周期] 执行幂等锁屏休眠 (KEYCODE_SLEEP 223)...");
        let _ = self.device.shell_write("input keyevent 223", 3000);
    }

    /// 只读性能遥测采集 (内存与温度)
    pub fn sample_telemetry(&self) -> (u64, f32) {
        let mem_str = self.device.shell(&format!("dumpsys meminfo {PACKAGE} | grep -E 'TOTAL PSS:|TOTAL:'"), 2000);
        let pss_kb = mem_str.split_whitespace().nth(2).and_then(|v| v.parse::<u64>().ok()).unwrap_or(0);
        let bat_str = self.device.shell("dumpsys battery | grep temperature", 2000);
        let temp_c = bat_str.split_whitespace().nth(1).and_then(|v| v.parse::<f32>().ok()).map(|t| t / 10.0).unwrap_or(0.0);
        (pss_kb / 1024, temp_c)
    }

    /// 5. 持续运行主循环：全流程编排执行
    pub fn run_loop(&mut self) -> Result<(), String> {
        QuestMode::parse(&self.cfg.mode)?;
        println!("════ 启动原神剧情过关与跑图 Agent (Genshin Quest Agent) ════");
        println!("模式: {} | 最大运行时长: {}s | 退出自动锁屏: {}", self.cfg.mode, self.cfg.max_seconds, self.cfg.auto_shutdown);
        println!("特性: 全自主冷启动唤醒 + 零Token轻量视觉感知 + 宿主机原生手柄注入 + 安全退出锁屏");
        println!("────────────────────────────────────────────────────────────");

        // 阶段一：自主会话预检与环境拉起
        if let Err(e) = self.ensure_game_ready() {
            eprintln!("[错误] 游戏唤醒/拉起失败: {e}");
            if self.cfg.auto_shutdown {
                self.shutdown_and_lock();
            }
            return Err(e);
        }

        // 创建任务日志目录 (符合 phonefarm 账本规范)
        let now_str = chrono::Local::now().format("%Y%m%d_%H%M%S").to_string();
        let task_dir = format!("tasks/genshin_quest_{now_str}");
        let _ = std::fs::create_dir_all(&task_dir);
        let telem_file = format!("{task_dir}/telemetry.jsonl");

        // 阶段二：自主循环决策推进
        let start = Instant::now();
        let mut step_count = 0;
        let mut last_telemetry_time = Instant::now();

        while start.elapsed().as_secs() < self.cfg.max_seconds {
            step_count += 1;
            print!("[步数 #{:03}]", step_count);
            if let Err(e) = self.step() {
                eprintln!("  [警告: 单步执行异常: {e}]");
                sleep(Duration::from_millis(500));
            }

            // 每 30 秒执行一次轻量只读遥测采样并写入账本
            if last_telemetry_time.elapsed().as_secs() >= 30 {
                last_telemetry_time = Instant::now();
                let (pss_mb, temp_c) = self.sample_telemetry();
                println!("  [实时遥测] 耗时: {:.0}s | 内存: {} MB | 电池温度: {:.1}C", start.elapsed().as_secs_f32(), pss_mb, temp_c);
                let row = serde_json::json!({
                    "ts_ms": chrono::Utc::now().timestamp_millis(),
                    "elapsed_s": start.elapsed().as_secs(),
                    "step": step_count,
                    "pss_mb": pss_mb,
                    "temp_c": temp_c,
                });
                if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&telem_file) {
                    use std::io::Write;
                    let _ = writeln!(f, "{}", row);
                }
            }
        }

        println!("────────────────────────────────────────────────────────────");
        println!("[完成] 原神任务/剧情 Agent 运行完成 (共执行 {} 步, 耗时 {:.1}s)", step_count, start.elapsed().as_secs_f32());

        // 阶段三：安全收尾与锁屏
        if self.cfg.auto_shutdown {
            self.shutdown_and_lock();
        }

        Ok(())
    }
}

// ══════════════════════════════════════════════════════════════
// ScenarioPlugin 实现: 让原神以插件身份接入通用引擎的优先级阶梯
// ══════════════════════════════════════════════════════════════

/// 插件名 (进日志与遥测归因)
pub const NAME: &str = "genshin";

/// 原神场景插件
///
/// 与 `GenshinQuestAgent` 的分工:
/// - `GenshinQuestAgent` 是独立长跑 Agent, 自持循环与生命周期, 供 `phonefarm quest` 使用;
/// - `GenshinPlugin` 是单帧决策体, 挂进通用引擎, 供 `phonefarm run` 在遇到原神画面时复用同一套感知。
///
/// 两者共享纯感知函数 `classify_state`, 不重复实现界面判定。
pub struct GenshinPlugin {
    /// 连续判定为剧情态的帧数 (用于节流日志与后续策略扩展)
    consecutive_dialogue_ticks: u32,
}

impl GenshinPlugin {
    pub fn new() -> Self {
        Self { consecutive_dialogue_ticks: 0 }
    }
}

impl Default for GenshinPlugin {
    fn default() -> Self {
        Self::new()
    }
}

impl ScenarioPlugin for GenshinPlugin {
    fn name(&self) -> &str {
        NAME
    }

    fn matches(&self, package: &str) -> bool {
        package == PACKAGE
    }

    /// 原神是纯自绘 3D 画面, 触屏在手柄接入后会被游戏屏蔽, 故走手柄链路。
    fn input_mode(&self) -> InputMode {
        InputMode::GamepadOnly
    }

    /// 剧情态与标题门在通用算子之前处理: 这两类画面的 UI 树为空,
    /// 通用弹窗/对白算子无从判定, 必须由本插件的像素感知接管。
    fn intercept(&mut self, ctx: &FrameContext) -> Option<UniversalAction> {
        let img = ctx.img?;
        let (state, _marker_x) = classify_state(img);
        match state {
            GenshinState::TitleScreen => {
                self.consecutive_dialogue_ticks = 0;
                Some(UniversalAction::GamepadButton { button: "a".into(), hold_ms: 150 })
            }
            GenshinState::DialogueText | GenshinState::DialogueChoice => {
                self.consecutive_dialogue_ticks += 1;
                Some(UniversalAction::GamepadButton { button: "a".into(), hold_ms: 120 })
            }
            GenshinState::InteractionPrompt => {
                self.consecutive_dialogue_ticks = 0;
                Some(UniversalAction::GamepadButton { button: "x".into(), hold_ms: 120 })
            }
            GenshinState::LoadingOrCutscene => {
                self.consecutive_dialogue_ticks = 0;
                Some(UniversalAction::Wait { ms: 800 })
            }
            // 大世界探索与攀爬交由通用导航算子与上层决策处理
            GenshinState::OpenWorldExplore | GenshinState::Climbing => None,
        }
    }
}

#[cfg(test)]
mod plugin_tests {
    use super::*;

    #[test]
    fn quest_step_policy_controls_recorded_inputs() {
        use std::cell::RefCell;

        #[derive(Default)]
        struct RecordingDevice(RefCell<Vec<String>>);
        impl QuestDevice for RecordingDevice {
            fn shell(&self, cmd: &str, _ms: u64) -> String {
                self.0.borrow_mut().push(format!("shell:{cmd}"));
                String::new()
            }
            fn screen_image(&self) -> Option<DynamicImage> {
                self.0.borrow_mut().push("screen".into());
                None
            }
            fn gamepad_press(&self, btn: &str, _ms: u64) -> Result<(), String> {
                self.0.borrow_mut().push(format!("press:{btn}"));
                Ok(())
            }
            fn gamepad_stick(&self, stick: &str, _x: f32, _y: f32, _ms: u64) -> Result<(), String> {
                self.0.borrow_mut().push(format!("stick:{stick}"));
                Ok(())
            }
            fn gamepad_wander(&self, _ms: u64) -> Result<(), String> {
                self.0.borrow_mut().push("wander".into());
                Ok(())
            }
            fn gamepad_reset(&self) -> Result<(), String> {
                self.0.borrow_mut().push("reset".into());
                Ok(())
            }
            fn gamepad_close(&self) -> Result<(), String> {
                self.0.borrow_mut().push("close".into());
                Ok(())
            }
        }

        let execute = |name: &str, auto_choice: bool, state, expected: &[&str]| {
            let mode = QuestMode::parse(name).unwrap();
            let device = RecordingDevice::default();
            let cfg = QuestConfig { mode: name.into(), auto_choice, ..QuestConfig::default() };
            let mut agent = GenshinQuestAgent::new(&device, cfg);
            agent.last_climb = Some(Instant::now() - Duration::from_secs(4));
            agent.execute_state(mode, state, None).unwrap();
            assert_eq!(*device.0.borrow(), expected.iter().map(|s| s.to_string()).collect::<Vec<_>>(), "{name} / {state:?}");
        };
        use GenshinState::*;
        for (state, expected) in [
            (TitleScreen, vec!["press:a"]),
            (DialogueText, vec!["press:a"]),
            (DialogueChoice, vec!["press:a"]),
            (InteractionPrompt, vec!["press:x"]),
            (Climbing, vec!["press:b", "stick:left"]),
            (OpenWorldExplore, vec!["wander", "press:x"]),
            (LoadingOrCutscene, vec!["press:a"]),
        ] {
            execute("auto", true, state, &expected);
            assert!(QuestMode::Auto.allows_capture_back(state, true));
        }
        for state in [TitleScreen, DialogueText, DialogueChoice, InteractionPrompt, LoadingOrCutscene] {
            execute("navigate", true, state, &[]);
            assert!(!QuestMode::Navigate.allows_capture_back(state, true));
        }
        execute("navigate", true, OpenWorldExplore, &["wander"]);
        execute("navigate", true, Climbing, &["press:b", "stick:left"]);
        for state in [OpenWorldExplore, Climbing, InteractionPrompt, TitleScreen, LoadingOrCutscene] {
            execute("dialogue", true, state, &[]);
        }
        execute("dialogue", true, DialogueText, &["press:a"]);
        execute("dialogue", true, DialogueChoice, &["press:a"]);
        execute("interact", true, InteractionPrompt, &["press:x"]);
        for state in [TitleScreen, DialogueText, DialogueChoice, Climbing, OpenWorldExplore, LoadingOrCutscene] {
            execute("interact", true, state, &[]);
        }
        for name in ["auto", "dialogue"] {
            execute(name, false, DialogueChoice, &[]);
            assert!(!QuestMode::parse(name).unwrap().allows_capture_back(DialogueChoice, false));
        }
        for mode in [QuestMode::Dialogue, QuestMode::Interact] {
            assert!(!mode.allows_capture_back(DialogueChoice, true));
        }
        for invalid in ["", "typo", "--sec"] {
            assert!(QuestMode::parse(invalid).is_err());
            let device = RecordingDevice::default();
            let cfg = QuestConfig { mode: invalid.into(), ..QuestConfig::default() };
            let mut agent = GenshinQuestAgent::new(&device, cfg);
            assert!(agent.step().is_err());
            assert!(agent.run_loop().is_err());
            assert!(device.0.borrow().is_empty(), "invalid mode must precede device access");
        }
    }

    #[test]
    fn test_plugin_only_claims_its_own_package() {
        let p = GenshinPlugin::new();
        assert!(p.matches(PACKAGE));
        assert!(!p.matches("com.android.calculator2"));
        assert!(!p.matches(""));
    }

    #[test]
    fn test_plugin_uses_gamepad_input_mode() {
        assert_eq!(GenshinPlugin::new().input_mode(), InputMode::GamepadOnly);
    }

    #[test]
    fn test_plugin_yields_without_image() {
        // 无截图时插件不得臆断画面状态, 必须放行
        let mut p = GenshinPlugin::new();
        let ctx = FrameContext {
            package: PACKAGE, activity: "", elements: &[], img: None,
            screen_w: 1216, screen_h: 2688, step: 1, last_rejected: false,
        };
        assert_eq!(p.intercept(&ctx), None);
    }
}
