//! 通用应用与游戏操作基础设施 (Universal Mobile Agent Core)
//!
//! 核心设计理念：
//! 1. 代码是给人看的，只是机器恰好可以运行 (高度可读、模块解耦、强类型状态机)。
//! 2. 跨 App 与跨游戏通用化 (Universal)：
//!    - 无论底层是 Android 原生 UI 树、Web 混合容器还是 Unity/Unreal 纯自绘画面，均提供统一感知与执行接口。
//! 3. 沉淀高频通用三大算子：
//!    - `DialogueSkipper`: 通用对话跳过器 (字幕、对白卡片、分支抉择、CG转场)
//!    - `PopupCloser`: 通用弹窗确认器 (公告、日常签到、权限确认、评价提示、防卡死返回逃逸)
//!    - `NavWalker`: 通用导航寻路器 (3D视野循迹转向、全速奔跑、列表流滚动、地形障碍脱困)
//! 4. 专用场景一律插件化 (`plugin::ScenarioPlugin`)：
//!    - 核心引擎不内置任何具体应用的包名、界面文案或业务流程假设；
//!    - 某款游戏、某个 App 的特定流程以插件形式挂载，可在通用算子前后各占一档优先级。
//! 5. 优先级状态机调度引擎：
//!    - `UniversalEngine`: 串联三大算子与上层业务/模型决策，实现绝大部分非敏感流程 0 Token 本地秒级闭环。

pub mod action;
pub mod popup;
pub mod dialogue;
pub mod nav;
pub mod plugin;
pub mod engine;

pub use action::{InputMode, UniversalAction};
pub use popup::{PopupCloser, PopupCloserConfig, PopupPolicy, PopupTarget};
pub use dialogue::{ChoiceOption, DialogueSkipper, DialogueSkipperConfig, DialogueType};
pub use nav::{NavMode, NavWalker, NavWalkerConfig};
pub use plugin::{FrameContext, PluginRegistry, ScenarioPlugin};
pub use engine::{EngineDecision, EngineStats, UniversalEngine};
