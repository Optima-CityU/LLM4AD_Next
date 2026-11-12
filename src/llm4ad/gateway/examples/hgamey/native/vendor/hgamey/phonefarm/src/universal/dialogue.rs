//! 通用对话跳过器 (Universal Dialogue Skipper)
//!
//! 在 RPG、文字冒险游戏、剧情向手游以及常规 App 的新手引导中，
//! 对白文字、剧情过场和分支选项往往需要大量机械点击推进。
//!
//! DialogueSkipper 提供：
//! 1. 剧情对白状态识别 (字幕底栏、隐藏的主界面控制栏)
//! 2. 分支选项定位与自动抉择 (多分支自动点选目标项或首项)
//! 3. 节奏控制与防假死 (平滑按键节流，避免输入堵塞)

use image::{DynamicImage, GenericImageView};
use crate::device::Node;
use super::action::UniversalAction;

/// 剧情对话具体类型
#[derive(Debug, Clone, PartialEq)]
pub enum DialogueType {
    /// 连续文本字幕 (需要快速点击或按 A 推进)
    Subtitles(String),
    /// 分支选项卡片 (需要从候选列表中做选择)
    Choices(Vec<ChoiceOption>),
    /// 纯过场转场 (暗转/CG动画)
    Cutscene,
}

/// 分支选项信息
#[derive(Debug, Clone, PartialEq)]
pub struct ChoiceOption {
    pub text: String,
    pub x: u32,
    pub y: u32,
}

/// 对话跳过器配置
#[derive(Debug, Clone)]
pub struct DialogueSkipperConfig {
    /// 是否在遇到多分支时自动确认选择
    pub auto_choice: bool,
    /// 默认选取的选项序号 (0 表示第一项)
    pub default_choice_index: usize,
    /// 是否启用手柄按键 (按 A 推进/确认)
    pub gamepad_enabled: bool,
    /// 推进跳过单次节流延迟 (毫秒)
    pub throttle_ms: u64,
}

impl Default for DialogueSkipperConfig {
    fn default() -> Self {
        Self {
            auto_choice: true,
            default_choice_index: 0,
            gamepad_enabled: false,
            throttle_ms: 200,
        }
    }
}

/// 通用对话跳过器
pub struct DialogueSkipper {
    cfg: DialogueSkipperConfig,
    consecutive_dialogue_ticks: u32,
}

impl DialogueSkipper {
    pub fn new(cfg: DialogueSkipperConfig) -> Self {
        Self {
            cfg,
            consecutive_dialogue_ticks: 0,
        }
    }

    /// 核心检测函数：结合 UI 元素与图像画面识别是否处于对话/剧情中
    pub fn detect(
        &self,
        elements: &[Node],
        img: Option<&DynamicImage>,
        screen_w: u32,
        screen_h: u32,
    ) -> Option<DialogueType> {
        // 1. 优先从 UI 树中检测选项或跳过按钮
        if let Some(dialogue) = self.detect_from_elements(elements) {
            return Some(dialogue);
        }

        // 2. 图像特征探测 (用于游戏自绘界面)
        if let Some(image) = img {
            return self.detect_from_vision(image, screen_w, screen_h);
        }

        None
    }

    /// 根据检测到的剧情状态给出下一步推进动作
    pub fn step(&mut self, dialogue: DialogueType) -> UniversalAction {
        self.consecutive_dialogue_ticks += 1;

        match dialogue {
            DialogueType::Choices(choices) => {
                if self.cfg.auto_choice && !choices.is_empty() {
                    let idx = self.cfg.default_choice_index.min(choices.len() - 1);
                    let target = &choices[idx];
                    println!(
                        "[DialogueSkipper] 选中分支选项 [{}/{}]: '{}'",
                        idx + 1, choices.len(), target.text
                    );
                    if self.cfg.gamepad_enabled {
                        UniversalAction::GamepadButton {
                            button: "a".into(),
                            hold_ms: 120,
                        }
                    } else {
                        UniversalAction::Tap {
                            x: target.x,
                            y: target.y,
                            label: Some(format!("选择分支: {}", target.text)),
                        }
                    }
                } else {
                    UniversalAction::Wait { ms: 300 }
                }
            }
            DialogueType::Subtitles(summary) => {
                println!(
                    "[DialogueSkipper] 推进对话字幕 (第 {} 步): {}",
                    self.consecutive_dialogue_ticks, summary
                );
                if self.cfg.gamepad_enabled {
                    UniversalAction::GamepadButton {
                        button: "a".into(),
                        hold_ms: 100,
                    }
                } else {
                    // 点击屏幕中下部字幕区域推进
                    UniversalAction::NormalizedTap {
                        nx: 500,
                        ny: 800,
                        label: Some("推进字幕".into()),
                    }
                }
            }
            DialogueType::Cutscene => {
                println!("[DialogueSkipper] 过场动画中，轻微交互尝试跳过");
                if self.cfg.gamepad_enabled {
                    UniversalAction::GamepadButton {
                        button: "a".into(),
                        hold_ms: 80,
                    }
                } else {
                    UniversalAction::NormalizedTap {
                        nx: 500,
                        ny: 500,
                        label: Some("跳过动画".into()),
                    }
                }
            }
        }
    }

    /// 从 UI 元素树识别
    fn detect_from_elements(&self, elements: &[Node]) -> Option<DialogueType> {
        let skip_tags = ["点击屏幕继续", "点击任意区域", "跳过剧情", "Tap to continue", "Skip dialogue"];
        for el in elements {
            let t = el.t.trim();
            for &tag in &skip_tags {
                if t.contains(tag) {
                    let cx = ((el.b[0] + el.b[2]) / 2) as u32;
                    let cy = ((el.b[1] + el.b[3]) / 2) as u32;
                    return Some(DialogueType::Choices(vec![ChoiceOption {
                        text: t.to_string(),
                        x: cx,
                        y: cy,
                    }]));
                }
            }
        }
        None
    }

    /// 从视觉特征识别
    fn detect_from_vision(&self, img: &DynamicImage, w: u32, h: u32) -> Option<DialogueType> {
        if w < 200 || h < 200 {
            return None;
        }

        // 1. 检查是否整体黑屏/过场 CG
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
            return Some(DialogueType::Cutscene);
        }

        // 2. 检查右侧多分支卡片特征 (横屏下 x: 60%~85%, y: 35%~75%)
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
                // 分支选项亮底或发光金色文字
                if (p[0] > 220 && p[1] > 220 && p[2] > 220) || (p[0] > 200 && p[1] > 180 && p[2] < 100) {
                    choice_box_pixels += 1;
                }
            }
        }
        let choice_ratio = choice_box_pixels as f32 / choice_total.max(1) as f32;
        if choice_ratio > 0.025 {
            return Some(DialogueType::Choices(vec![ChoiceOption {
                text: "视觉选项卡片".into(),
                x: (w as f32 * 0.72) as u32,
                y: (h as f32 * 0.45) as u32,
            }]));
        }

        // 3. 检查底部字幕条特征 (中下部有白色对白文字与半透明暗底: y 78%~92%)
        let mut text_white_pixels = 0;
        let mut text_total = 0;
        let t_y_start = (h as f32 * 0.78) as u32;
        let t_y_end = (h as f32 * 0.92) as u32;
        for x in (w / 4..3 * w / 4).step_by(8) {
            for y in (t_y_start..t_y_end).step_by(6) {
                let p = img.get_pixel(x, y);
                text_total += 1;
                if p[0] > 225 && p[1] > 225 && p[2] > 225 {
                    text_white_pixels += 1;
                }
            }
        }
        let text_ratio = text_white_pixels as f32 / text_total.max(1) as f32;
        if text_ratio > 0.008 {
            return Some(DialogueType::Subtitles("底部字幕对白".into()));
        }

        None
    }

    /// 重置状态
    pub fn reset(&mut self) {
        self.consecutive_dialogue_ticks = 0;
    }

    /// 获取连续推进次数
    pub fn consecutive_ticks(&self) -> u32 {
        self.consecutive_dialogue_ticks
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_choice_selection() {
        let mut skipper = DialogueSkipper::new(DialogueSkipperConfig {
            auto_choice: true,
            default_choice_index: 0,
            gamepad_enabled: false,
            throttle_ms: 100,
        });

        let choices = DialogueType::Choices(vec![
            ChoiceOption { text: "接受任务".into(), x: 600, y: 400 },
            ChoiceOption { text: "稍后再说".into(), x: 600, y: 550 },
        ]);

        let action = skipper.step(choices);
        assert_eq!(
            action,
            UniversalAction::Tap {
                x: 600,
                y: 400,
                label: Some("选择分支: 接受任务".into()),
            }
        );
        assert_eq!(skipper.consecutive_ticks(), 1);
    }

    #[test]
    fn test_gamepad_subtitle_skip() {
        let mut skipper = DialogueSkipper::new(DialogueSkipperConfig {
            auto_choice: true,
            default_choice_index: 0,
            gamepad_enabled: true,
            throttle_ms: 100,
        });

        let action = skipper.step(DialogueType::Subtitles("派蒙：前面就是蒙德城了！".into()));
        assert_eq!(
            action,
            UniversalAction::GamepadButton {
                button: "a".into(),
                hold_ms: 100,
            }
        );
    }
}
