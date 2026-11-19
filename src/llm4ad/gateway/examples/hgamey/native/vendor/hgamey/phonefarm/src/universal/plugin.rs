//! 场景插件契约 (Scenario Plugin Contract)
//!
//! 架构铁律: 核心引擎只做通用能力, 不得内置任何具体应用的包名、界面文案或流程假设。
//! 一切专用场景 (某款游戏的剧情推进、某个 App 的特定业务流) 一律以插件形式接入,
//! 插件可自带感知语义与动作策略, 但只能通过本契约与核心交互。
//!
//! 挂载点在通用算子的优先级阶梯中占两档:
//! - `intercept`: 通用算子之前。用于场景特有的、必须抢在通用规则前处理的画面语义。
//! - `decide`:   通用算子之后、大模型之前。用于场景兜底策略, 省下一次视觉模型调用。
//!
//! 插件不实现任何一档时即为纯被动插件, 核心行为与无插件完全一致。

use image::DynamicImage;
use crate::device::{Device, Node};
use super::action::{InputMode, UniversalAction};

/// 单帧感知上下文 (只读快照, 插件不得持有跨帧引用)
pub struct FrameContext<'a> {
    /// 当前前台应用包名
    pub package: &'a str,
    /// 当前前台 Activity (可能为空)
    pub activity: &'a str,
    /// 本帧可见文本元素 (设备像素坐标空间)
    pub elements: &'a [Node],
    /// 本帧截图 (UI 树不可信的自绘界面靠它)
    pub img: Option<&'a DynamicImage>,
    /// 设备物理分辨率
    pub screen_w: u32,
    pub screen_h: u32,
    /// 本局已执行步数
    pub step: u32,
    /// 上一步动作是否被执行层驳回 (坐标封禁/无变化), 插件据此避免原地重试
    pub last_rejected: bool,
}

impl<'a> FrameContext<'a> {
    /// 便捷判定: 本帧是否有任一元素文本包含给定关键词
    pub fn has_text(&self, needle: &str) -> bool {
        self.elements.iter().any(|e| e.t.contains(needle))
    }

    /// 便捷取用: 返回首个文本匹配元素的中心点 (设备像素坐标)
    pub fn center_of(&self, needle: &str) -> Option<(u32, u32)> {
        self.elements.iter().find(|e| e.t.contains(needle)).map(|e| {
            (
                ((e.b[0] + e.b[2]) / 2).max(0) as u32,
                ((e.b[1] + e.b[3]) / 2).max(0) as u32,
            )
        })
    }
}

/// 场景插件契约
///
/// 实现方须自证适用范围 (`matches`), 核心据此在前台应用切换时激活或停用插件。
pub trait ScenarioPlugin: Send {
    /// 插件名 (进日志与遥测, 用于归因某一步是谁决策的)
    fn name(&self) -> &str;

    /// 本插件是否适用于给定前台包名
    fn matches(&self, package: &str) -> bool;

    /// 场景期望的输入方式 (触控 / 虚拟手柄 / 混合)
    fn input_mode(&self) -> InputMode {
        InputMode::TouchOnly
    }

    /// 插件激活时的一次性准备 (冷启动、进入指定界面等)
    fn setup(&mut self, _device: &Device) -> Result<(), String> {
        Ok(())
    }

    /// 通用算子之前的高优先拦截
    fn intercept(&mut self, _ctx: &FrameContext) -> Option<UniversalAction> {
        None
    }

    /// 通用算子未拦截时的场景兜底决策 (在交给大模型之前)
    fn decide(&mut self, _ctx: &FrameContext) -> Option<UniversalAction> {
        None
    }

    /// 插件停用时的收尾 (释放外设、结束进程等)
    fn teardown(&mut self, _device: &Device) -> Result<(), String> {
        Ok(())
    }
}

/// 插件注册表: 核心持有, 按前台包名选出当前生效的插件
#[derive(Default)]
pub struct PluginRegistry {
    plugins: Vec<Box<dyn ScenarioPlugin>>,
    active: Option<usize>,
}

impl PluginRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    /// 注册一个场景插件
    pub fn register(&mut self, plugin: Box<dyn ScenarioPlugin>) {
        self.plugins.push(plugin);
    }

    pub fn is_empty(&self) -> bool {
        self.plugins.is_empty()
    }

    pub fn len(&self) -> usize {
        self.plugins.len()
    }

    /// 依据前台包名切换生效插件, 返回新生效插件名 (无变化时返回 None)
    pub fn activate_for(&mut self, package: &str) -> Option<String> {
        let found = self.plugins.iter().position(|p| p.matches(package));
        if found == self.active {
            return None;
        }
        self.active = found;
        found.map(|i| self.plugins[i].name().to_string())
    }

    /// 当前生效插件的可变引用
    pub fn active_mut(&mut self) -> Option<&mut Box<dyn ScenarioPlugin>> {
        self.active.map(move |i| &mut self.plugins[i])
    }

    /// 当前生效插件名
    pub fn active_name(&self) -> Option<&str> {
        self.active.map(|i| self.plugins[i].name())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct StubPlugin {
        pkg: &'static str,
    }

    impl ScenarioPlugin for StubPlugin {
        fn name(&self) -> &str {
            "stub"
        }
        fn matches(&self, package: &str) -> bool {
            package == self.pkg
        }
        fn decide(&mut self, _ctx: &FrameContext) -> Option<UniversalAction> {
            Some(UniversalAction::Wait { ms: 10 })
        }
    }

    #[test]
    fn test_registry_activates_only_matching_package() {
        let mut reg = PluginRegistry::new();
        reg.register(Box::new(StubPlugin { pkg: "com.example.game" }));

        assert_eq!(reg.activate_for("com.android.calculator2"), None);
        assert!(reg.active_name().is_none());

        assert_eq!(reg.activate_for("com.example.game").as_deref(), Some("stub"));
        assert_eq!(reg.active_name(), Some("stub"));

        // 同一包名重复进入不重复激活
        assert_eq!(reg.activate_for("com.example.game"), None);

        // 切走后插件停用
        assert_eq!(reg.activate_for("com.android.settings"), None);
        assert!(reg.active_name().is_none());
    }

    #[test]
    fn test_empty_registry_is_transparent() {
        let mut reg = PluginRegistry::new();
        assert!(reg.is_empty());
        assert_eq!(reg.activate_for("any.package"), None);
        assert!(reg.active_mut().is_none());
    }
}
