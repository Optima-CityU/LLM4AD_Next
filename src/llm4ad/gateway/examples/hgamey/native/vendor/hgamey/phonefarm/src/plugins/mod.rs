//! 场景插件层 (Scenario Plugins)
//!
//! 专用场景一律落在本层, 核心引擎 (`universal`, `runtime`) 不得引用本层的任何
//! 包名、界面文案或业务常量。新增一个专用场景 = 在本层新增一个模块并在
//! `register_builtin` 中登记, 核心代码一行不改。

pub mod genshin;

use crate::universal::UniversalEngine;

/// 把所有内置场景插件登记进通用引擎。
///
/// 这是核心与专用场景之间唯一的耦合点: 核心只调用本函数, 不认识具体插件。
pub fn register_builtin(engine: &mut UniversalEngine) {
    engine.register_plugin(Box::new(genshin::GenshinPlugin::new()));
}

/// 当前已登记的内置插件名单 (供 CLI 与诊断输出)
pub fn builtin_names() -> Vec<&'static str> {
    vec![genshin::NAME]
}
