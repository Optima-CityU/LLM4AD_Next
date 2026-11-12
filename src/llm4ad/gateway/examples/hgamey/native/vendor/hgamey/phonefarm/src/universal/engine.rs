//! 通用状态机调度引擎 (Universal Engine)
//!
//! 将通用弹窗确认器 (PopupCloser)、通用对话跳过器 (DialogueSkipper)
//! 与通用导航寻路器 (NavWalker) 组织为分层优先级状态机：
//!
//! 优先级阶梯：
//! 1. 场景插件拦截层 (`ScenarioPlugin::intercept`)：专用场景特有的画面语义先于通用规则处理。
//! 2. 弹窗阻断层：先关弹窗与确认日常奖励，杜绝界面遮挡。
//! 3. 剧情对话层：遇对白/CG/分支选择快速推进，跳过无效等待。
//! 4. 导航跑图层：朝向目标奔跑，触发脱困看门狗。
//! 5. 场景插件兜底层 (`ScenarioPlugin::decide`)：通用算子未拦截时的场景专属策略。
//! 6. 业务决策层 (高层大模型)：当规则、插件与状态机都无法确定时，交付外部大模型决策。
//!
//! 专用性纪律：
//! 本引擎不得内置任何具体应用的包名、界面文案或业务流程假设；一切专用场景经插件接入。
//!
//! 零 Token 纪律：
//! 绝大多数非时间敏感型流程 (弹窗/对白/直线寻路) 均由本地规则在毫秒级内自动解决，
//! 仅在真正面临全新业务场景时才调用大模型，极大降低 API 消耗。

use image::DynamicImage;
use crate::device::{Device, Node};
use super::action::UniversalAction;
use super::popup::{PopupCloser, PopupCloserConfig};
use super::dialogue::{DialogueSkipper, DialogueSkipperConfig};
use super::nav::{NavWalker, NavWalkerConfig};
use super::plugin::{FrameContext, PluginRegistry, ScenarioPlugin};

/// 引擎单帧决策结论
#[derive(Debug, Clone, PartialEq)]
pub enum EngineDecision {
    /// 由通用弹窗处理器接管并解决
    HandledByPopup {
        action: UniversalAction,
    },
    /// 由通用对话跳过器接管并解决
    HandledByDialogue {
        action: UniversalAction,
    },
    /// 由通用导航寻路器接管并推进
    HandledByNavigation {
        actions: Vec<UniversalAction>,
    },
    /// 由当前生效的场景插件接管 (拦截档或兜底档)
    HandledByPlugin {
        plugin: String,
        action: UniversalAction,
    },
    /// 通用状态机未拦截，需交由高层业务逻辑或大模型决策
    YieldToTask,
}

/// 状态机运行统计指标
#[derive(Debug, Default, Clone)]
pub struct EngineStats {
    pub total_ticks: u64,
    pub popup_resolved_ticks: u64,
    pub dialogue_resolved_ticks: u64,
    pub nav_resolved_ticks: u64,
    pub plugin_resolved_ticks: u64,
    pub yield_ticks: u64,
}

/// 通用调度引擎
pub struct UniversalEngine {
    pub popup_closer: PopupCloser,
    pub dialogue_skipper: DialogueSkipper,
    pub nav_walker: NavWalker,
    pub plugins: PluginRegistry,
    pub stats: EngineStats,
}

impl UniversalEngine {
    /// 使用默认配置创建通用引擎
    pub fn new(gamepad_enabled: bool) -> Self {
        let popup_cfg = PopupCloserConfig {
            gamepad_enabled,
            ..Default::default()
        };
        let dialogue_cfg = DialogueSkipperConfig {
            gamepad_enabled,
            ..Default::default()
        };
        let nav_cfg = NavWalkerConfig {
            gamepad_enabled,
            ..Default::default()
        };

        Self {
            popup_closer: PopupCloser::new(popup_cfg),
            dialogue_skipper: DialogueSkipper::new(dialogue_cfg),
            nav_walker: NavWalker::new(nav_cfg),
            plugins: PluginRegistry::new(),
            stats: EngineStats::default(),
        }
    }

    /// 注册场景插件。核心自身不认识任何具体应用，专用能力只能由此进入。
    pub fn register_plugin(&mut self, plugin: Box<dyn ScenarioPlugin>) {
        self.plugins.register(plugin);
    }

    /// 依据前台包名切换生效插件，返回新激活的插件名
    pub fn activate_plugin_for(&mut self, package: &str) -> Option<String> {
        self.plugins.activate_for(package)
    }

    /// 单步周期感知与判定 (纯计算，不直接下发 IO，便于单元测试与沙盒仿真)
    ///
    /// 无场景上下文的简化入口：等价于以空包名构造 `FrameContext`，此时插件不参与。
    pub fn evaluate(
        &mut self,
        elements: &[Node],
        img: Option<&DynamicImage>,
        screen_w: u32,
        screen_h: u32,
    ) -> EngineDecision {
        let ctx = FrameContext {
            package: "",
            activity: "",
            elements,
            img,
            screen_w,
            screen_h,
            step: 0,
            last_rejected: false,
        };
        self.evaluate_frame(&ctx)
    }

    /// 单帧完整判定 (带场景上下文，插件参与优先级阶梯)
    pub fn evaluate_frame(&mut self, ctx: &FrameContext) -> EngineDecision {
        let elements = ctx.elements;
        let img = ctx.img;
        let (screen_w, screen_h) = (ctx.screen_w, ctx.screen_h);
        self.stats.total_ticks += 1;

        // 0. 优先级零：场景插件拦截 (专用语义先于通用规则)
        if let Some(plugin) = self.plugins.active_mut() {
            if let Some(action) = plugin.intercept(ctx) {
                let name = plugin.name().to_string();
                self.stats.plugin_resolved_ticks += 1;
                return EngineDecision::HandledByPlugin { plugin: name, action };
            }
        }

        // 1. 优先级一：检查并处理弹窗
        if let Some(popup_action) = self.popup_closer.detect_and_resolve(elements, img, screen_w, screen_h) {
            self.stats.popup_resolved_ticks += 1;
            return EngineDecision::HandledByPopup { action: popup_action };
        }

        // 2. 优先级二：检查并处理剧情对白 / 分支卡片
        if let Some(dialogue_state) = self.dialogue_skipper.detect(elements, img, screen_w, screen_h) {
            let dialogue_action = self.dialogue_skipper.step(dialogue_state);
            self.stats.dialogue_resolved_ticks += 1;
            return EngineDecision::HandledByDialogue { action: dialogue_action };
        }

        // 3. 优先级三：检查导航目标循迹与防卡死脱困 (仅在视觉检测到任务路标时触发)
        if let Some(image) = img {
            if let Some(marker_x) = NavWalker::scan_waypoint_marker(image) {
                if let Some(escape_actions) = self.nav_walker.check_and_unstuck() {
                    self.stats.nav_resolved_ticks += 1;
                    return EngineDecision::HandledByNavigation { actions: escape_actions };
                }
                let nav_actions = self.nav_walker.step_steer_and_move(Some(marker_x));
                self.stats.nav_resolved_ticks += 1;
                return EngineDecision::HandledByNavigation { actions: nav_actions };
            }
        }

        // 4. 优先级四：场景插件兜底 (通用算子未拦截，交给插件的场景专属策略)
        if let Some(plugin) = self.plugins.active_mut() {
            if let Some(action) = plugin.decide(ctx) {
                let name = plugin.name().to_string();
                self.stats.plugin_resolved_ticks += 1;
                return EngineDecision::HandledByPlugin { plugin: name, action };
            }
        }

        // 5. 优先级五：规则、插件与状态机都无法决定，放行至上层大模型
        self.stats.yield_ticks += 1;
        EngineDecision::YieldToTask
    }

    /// 在真实设备上执行单步闭环感知与下发
    pub fn step_device(
        &mut self,
        device: &Device,
        elements: &[Node],
        img: Option<&DynamicImage>,
    ) -> Result<EngineDecision, String> {
        let (w, h) = device.size();
        let (w, h) = (w.max(1) as u32, h.max(1) as u32);
        let decision = self.evaluate(elements, img, w, h);

        match &decision {
            EngineDecision::HandledByPopup { action } => {
                println!("[UniversalEngine] 触发弹窗处理管线");
                action.execute(device)?;
            }
            EngineDecision::HandledByDialogue { action } => {
                println!("[UniversalEngine] 触发剧情对白跳过管线");
                action.execute(device)?;
            }
            EngineDecision::HandledByNavigation { actions } => {
                println!("[UniversalEngine] 触发通用寻路导航管线 ({} 步动作)", actions.len());
                for a in actions {
                    a.execute(device)?;
                }
            }
            EngineDecision::HandledByPlugin { plugin, action } => {
                println!("[UniversalEngine] 场景插件 '{plugin}' 接管本帧");
                action.execute(device)?;
            }
            EngineDecision::YieldToTask => {
                // 不执行操作，放行
            }
        }

        Ok(decision)
    }

    /// 输出当前统计摘要
    pub fn summary(&self) -> String {
        format!(
            "总帧数: {}, 弹窗处理: {}, 剧情跳过: {}, 导航推进: {}, 插件接管: {}, 业务放行: {}",
            self.stats.total_ticks,
            self.stats.popup_resolved_ticks,
            self.stats.dialogue_resolved_ticks,
            self.stats.nav_resolved_ticks,
            self.stats.plugin_resolved_ticks,
            self.stats.yield_ticks
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_engine_priority_popup_over_dialogue() {
        let mut engine = UniversalEngine::new(false);

        // 同时存在弹窗按钮与对话继续提示
        let nodes = vec![
            Node { t: "温馨提示".into(), b: [300, 400, 700, 500] },
            Node { t: "确定".into(), b: [450, 800, 550, 880] },
            Node { t: "点击屏幕继续".into(), b: [400, 1800, 600, 1900] },
        ];

        let decision = engine.evaluate(&nodes, None, 1080, 2400);
        // 弹窗必须具有最高优先级，优先于对话跳过
        match decision {
            EngineDecision::HandledByPopup { action } => {
                if let UniversalAction::Tap { x, y, label } = action {
                    assert_eq!(x, 500);
                    assert_eq!(y, 840);
                    assert!(label.unwrap().contains("确定"));
                } else {
                    panic!("预期点击动作");
                }
            }
            _ => panic!("预期由弹窗处理器优先接管"),
        }
    }

    #[test]
    fn test_engine_yield_when_no_pattern_matches() {
        let mut engine = UniversalEngine::new(false);
        let nodes = vec![
            Node { t: "商品详情".into(), b: [100, 200, 400, 300] },
            Node { t: "价格: ¥99".into(), b: [100, 350, 400, 450] },
        ];
        let decision = engine.evaluate(&nodes, None, 1080, 2400);
        assert_eq!(decision, EngineDecision::YieldToTask);
        assert_eq!(engine.stats.yield_ticks, 1);
    }

    /// 场景插件的 intercept 档必须抢在通用弹窗算子之前。
    #[test]
    fn test_plugin_intercept_precedes_universal_operators() {
        struct Intercepting;
        impl ScenarioPlugin for Intercepting {
            fn name(&self) -> &str { "intercepting" }
            fn matches(&self, package: &str) -> bool { package == "com.example.app" }
            fn intercept(&mut self, _ctx: &FrameContext) -> Option<UniversalAction> {
                Some(UniversalAction::Keycode { code: 111, label: Some("场景专属".into()) })
            }
        }

        let mut engine = UniversalEngine::new(false);
        engine.register_plugin(Box::new(Intercepting));
        engine.activate_plugin_for("com.example.app");

        // 画面上同时存在标准确认弹窗, 若无插件应由 PopupCloser 接管
        let nodes = vec![
            Node { t: "温馨提示".into(), b: [300, 400, 700, 500] },
            Node { t: "确定".into(), b: [450, 800, 550, 880] },
        ];
        let ctx = FrameContext {
            package: "com.example.app", activity: "", elements: &nodes, img: None,
            screen_w: 1080, screen_h: 2400, step: 1, last_rejected: false,
        };
        match engine.evaluate_frame(&ctx) {
            EngineDecision::HandledByPlugin { plugin, action } => {
                assert_eq!(plugin, "intercepting");
                assert_eq!(action, UniversalAction::Keycode { code: 111, label: Some("场景专属".into()) });
            }
            other => panic!("插件拦截档应优先于通用弹窗算子, 实际: {other:?}"),
        }
    }

    /// 场景插件的 decide 档只在通用算子全部放行后才生效, 且早于大模型。
    #[test]
    fn test_plugin_decide_is_fallback_before_model() {
        struct Fallback;
        impl ScenarioPlugin for Fallback {
            fn name(&self) -> &str { "fallback" }
            fn matches(&self, package: &str) -> bool { package == "com.example.app" }
            fn decide(&mut self, _ctx: &FrameContext) -> Option<UniversalAction> {
                Some(UniversalAction::Wait { ms: 500 })
            }
        }

        let mut engine = UniversalEngine::new(false);
        engine.register_plugin(Box::new(Fallback));
        engine.activate_plugin_for("com.example.app");

        // 普通业务画面: 通用三算子均不拦截
        let nodes = vec![
            Node { t: "商品详情".into(), b: [100, 200, 400, 300] },
            Node { t: "价格: ¥99".into(), b: [100, 350, 400, 450] },
        ];
        let ctx = FrameContext {
            package: "com.example.app", activity: "", elements: &nodes, img: None,
            screen_w: 1080, screen_h: 2400, step: 1, last_rejected: false,
        };
        match engine.evaluate_frame(&ctx) {
            EngineDecision::HandledByPlugin { plugin, action } => {
                assert_eq!(plugin, "fallback");
                assert_eq!(action, UniversalAction::Wait { ms: 500 });
            }
            other => panic!("插件兜底档应在放行大模型之前生效, 实际: {other:?}"),
        }
    }

    /// 未注册插件时引擎行为与插件化之前完全一致 (纯通用核心)。
    #[test]
    fn test_core_is_plugin_agnostic_when_none_registered() {
        let mut engine = UniversalEngine::new(false);
        let nodes = vec![Node { t: "商品详情".into(), b: [100, 200, 400, 300] }];
        let ctx = FrameContext {
            package: "com.example.some.specific.app", activity: "", elements: &nodes, img: None,
            screen_w: 1080, screen_h: 2400, step: 1, last_rejected: false,
        };
        // 核心不认识任何具体包名, 一律放行给上层
        assert_eq!(engine.evaluate_frame(&ctx), EngineDecision::YieldToTask);
    }
}
