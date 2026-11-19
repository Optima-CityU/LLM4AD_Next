//! 通用动作定义与执行抽象 (Universal Action & Actuator)
//!
//! 提供跨应用与跨游戏统一的输入操作协议：
//! - 触控指令 (点击、滑动、长按)
//! - 虚拟手柄指令 (按键、摇杆平移)
//! - 系统按键与文本注入 (返回键、Home键、字符输入)
//! - 流控指令 (等待、完成标识)

use std::thread::sleep;
use std::time::Duration;
use crate::device::Device;

/// 通用操作输入方式枚举
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InputMode {
    /// 纯触控模式 (适用于常规移动 App、纯触屏自绘手游)
    TouchOnly,
    /// 虚拟手柄模式 (适用于声明支持 Xbox/HID 标准手柄的游戏)
    GamepadOnly,
    /// 混合模式 (优先手柄，界面点击回退至触控)
    Hybrid,
}

/// 统一动作协议
#[derive(Debug, Clone, PartialEq)]
pub enum UniversalAction {
    /// 触控点击：指定屏幕绝对像素坐标 (x, y)
    Tap {
        x: u32,
        y: u32,
        label: Option<String>,
    },
    /// 归一化触控点击：坐标范围为 0 ~ 999 (便于模型与不同分辨率适配)
    NormalizedTap {
        nx: u32,
        ny: u32,
        label: Option<String>,
    },
    /// 触控滑动：起点 (x1, y1) 到终点 (x2, y2)，带持续毫秒数
    Swipe {
        x1: u32,
        y1: u32,
        x2: u32,
        y2: u32,
        duration_ms: u64,
    },
    /// 虚拟手柄按键点击：如 "a", "b", "x", "y", "lb", "rb" 等
    GamepadButton {
        button: String,
        hold_ms: u64,
    },
    /// 虚拟手柄摇杆推动：stick="left"|"right", x, y 取值 -1.0 ~ 1.0
    GamepadStick {
        stick: String,
        x: f32,
        y: f32,
        duration_ms: u64,
    },
    /// 系统硬件/按键事件：如 KEYCODE_BACK (4), KEYCODE_HOME (3) 等
    Keycode {
        code: u32,
        label: Option<String>,
    },
    /// 文本注入 (用于表单填写或输入框打字)
    TypeText {
        text: String,
    },
    /// 延迟等待 (用于等待动画加载或转场稳定)
    Wait {
        ms: u64,
    },
    /// 任务完成或终止
    Done {
        reason: String,
    },
}

impl UniversalAction {
    /// 在物理/虚拟设备上执行当前动作
    pub fn execute(&self, device: &Device) -> Result<(), String> {
        match self {
            UniversalAction::Tap { x, y, label } => {
                if let Some(lbl) = label {
                    println!("[Universal Actuator] 执行点击 ({}, {}): {}", x, y, lbl);
                } else {
                    println!("[Universal Actuator] 执行点击 ({}, {})", x, y);
                }
                device.tap(*x as i32, *y as i32);
                sleep(Duration::from_millis(150));
                Ok(())
            }
            UniversalAction::NormalizedTap { nx, ny, label } => {
                let (w, h) = device.size();
                let real_x = (*nx as f64 / 999.0 * w.max(1) as f64).round() as i32;
                let real_y = (*ny as f64 / 999.0 * h.max(1) as f64).round() as i32;
                if let Some(lbl) = label {
                    println!("[Universal Actuator] 执行归一化点击 [{}, {}] -> ({}, {}): {}", nx, ny, real_x, real_y, lbl);
                } else {
                    println!("[Universal Actuator] 执行归一化点击 [{}, {}] -> ({}, {})", nx, ny, real_x, real_y);
                }
                device.tap(real_x, real_y);
                sleep(Duration::from_millis(150));
                Ok(())
            }
            UniversalAction::Swipe { x1, y1, x2, y2, duration_ms } => {
                println!("[Universal Actuator] 执行滑动 ({}, {}) -> ({}, {}), 耗时 {}ms", x1, y1, x2, y2, duration_ms);
                device.swipe(*x1 as i32, *y1 as i32, *x2 as i32, *y2 as i32);
                sleep(Duration::from_millis(*duration_ms + 100));
                Ok(())
            }
            UniversalAction::GamepadButton { button, hold_ms } => {
                println!("[Universal Actuator] 注入手柄按键 '{}', 保持 {}ms", button, hold_ms);
                device.gamepad_press(button, *hold_ms)?;
                sleep(Duration::from_millis(50));
                Ok(())
            }
            UniversalAction::GamepadStick { stick, x, y, duration_ms } => {
                println!("[Universal Actuator] 推动手柄 '{}' 摇杆 ({:.2}, {:.2}), 持续 {}ms", stick, x, y, duration_ms);
                device.gamepad_stick(stick, *x, *y, *duration_ms)?;
                sleep(Duration::from_millis(50));
                Ok(())
            }
            UniversalAction::Keycode { code, label } => {
                if let Some(lbl) = label {
                    println!("[Universal Actuator] 发送按键事件 {} ({})", code, lbl);
                } else {
                    println!("[Universal Actuator] 发送按键事件 {}", code);
                }
                device.shell(&format!("input keyevent {code}"), 3000);
                sleep(Duration::from_millis(150));
                Ok(())
            }
            UniversalAction::TypeText { text } => {
                println!("[Universal Actuator] 输入文本: '{}'", text);
                device.shell(&format!("input text '{}'", text.replace('\'', "\\'")), 3000);
                sleep(Duration::from_millis(200));
                Ok(())
            }
            UniversalAction::Wait { ms } => {
                println!("[Universal Actuator] 等待 {}ms...", ms);
                sleep(Duration::from_millis(*ms));
                Ok(())
            }
            UniversalAction::Done { reason } => {
                println!("[Universal Actuator] 任务结束: {}", reason);
                Ok(())
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_action_creation() {
        let tap = UniversalAction::Tap { x: 100, y: 200, label: Some("确定".into()) };
        match tap {
            UniversalAction::Tap { x, y, label } => {
                assert_eq!(x, 100);
                assert_eq!(y, 200);
                assert_eq!(label, Some("确定".into()));
            }
            _ => panic!("类型不匹配"),
        }
    }

    #[test]
    fn test_normalized_tap_conversion() {
        let ntap = UniversalAction::NormalizedTap { nx: 500, ny: 500, label: None };
        if let UniversalAction::NormalizedTap { nx, ny, .. } = ntap {
            let (w, h) = (1080u32, 2400u32);
            let real_x = (nx as f64 / 999.0 * w as f64).round() as u32;
            let real_y = (ny as f64 / 999.0 * h as f64).round() as u32;
            assert_eq!(real_x, 541);
            assert_eq!(real_y, 1201);
        }
    }
}
