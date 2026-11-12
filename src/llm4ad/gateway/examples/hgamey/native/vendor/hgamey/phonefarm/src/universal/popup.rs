//! 通用弹窗确认器 (Universal Popup Closer)
//!
//! 在各类移动 App 与游戏流程中，弹窗 (活动公告、签到奖励、权限申请、更新提示、评分对话框)
//! 是阻塞主流程推进最频繁的干扰。
//!
//! PopupCloser 统一提供：
//! 1. 结构化 UI 树检测 (通过关键词特征定位确认/关闭控件中心)
//! 2. 纯视觉启发式检测 (检测屏幕居中卡片及其右上角关闭按键/底部动作栏)
//! 3. 智能应对策略 (优先确认领取/优先关闭跳过、连续卡死时的物理返回脱困)

use image::{DynamicImage, GenericImageView};
use crate::device::Node;
use super::action::UniversalAction;

/// 弹窗处理倾向
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PopupPolicy {
    /// 优先确认/领取 (例如日常签到、任务结算、确认弹窗)
    PreferConfirm,
    /// 优先关闭/跳过 (例如推广广告、评价邀请、公告轮播)
    PreferDismiss,
}

/// 识别到的弹窗交互目标
#[derive(Debug, Clone, PartialEq)]
pub struct PopupTarget {
    /// 控件或目标名称
    pub title: String,
    /// 点击位置绝对坐标
    pub x: u32,
    pub y: u32,
    /// 是否为肯定性动作 (确定/领取)
    pub is_affirmative: bool,
}

/// 通用弹窗处理器配置
#[derive(Debug, Clone)]
pub struct PopupCloserConfig {
    pub policy: PopupPolicy,
    /// 是否优先使用手柄按键 (B键关闭/A键确认)
    pub gamepad_enabled: bool,
    /// 连续遭遇同类弹窗未能关闭时的逃逸重试上限
    pub max_stuck_attempts: u32,
}

impl Default for PopupCloserConfig {
    fn default() -> Self {
        Self {
            policy: PopupPolicy::PreferConfirm,
            gamepad_enabled: false,
            max_stuck_attempts: 3,
        }
    }
}

/// 通用弹窗处理器
pub struct PopupCloser {
    cfg: PopupCloserConfig,
    consecutive_popup_ticks: u32,
    last_target: Option<String>,
}

impl PopupCloser {
    pub fn new(cfg: PopupCloserConfig) -> Self {
        Self {
            cfg,
            consecutive_popup_ticks: 0,
            last_target: None,
        }
    }

    /// 核心检测函数：结合 UI 元素树与截屏画面判定是否存在弹窗，并给出对应的解决动作
    pub fn detect_and_resolve(
        &mut self,
        elements: &[Node],
        img: Option<&DynamicImage>,
        screen_w: u32,
        screen_h: u32,
    ) -> Option<UniversalAction> {
        // 1. 优先通过结构化 UI 树匹配标准弹窗控件
        if let Some(target) = self.find_in_elements(elements) {
            return Some(self.resolve_target(target));
        }

        // 2. 若 UI 树为空 (游戏或自绘界面)，通过纯视觉特征进行几何与对比度探测
        if let Some(image) = img {
            if let Some(target) = self.find_in_vision(image, screen_w, screen_h) {
                return Some(self.resolve_target(target));
            }
        }

        // 无弹窗时清空计数
        self.consecutive_popup_ticks = 0;
        self.last_target = None;
        None
    }

    /// 从 UI 树节点中过滤常见确认/关闭关键词
    fn find_in_elements(&self, elements: &[Node]) -> Option<PopupTarget> {
        let confirm_keywords = [
            "我知道了", "确定", "确认", "领取", "好的", "同意", "开启", "立即查看", "接受",
            "进入游戏", "继续", "完成", "OK", "Confirm", "Claim", "Accept", "Got it", "Allow", "Agree",
        ];
        let dismiss_keywords = [
            "关闭", "跳过", "取消", "以后再说", "暂不", "Cancel", "Close", "Skip", "Dismiss", "Later",
        ];

        let mut best_confirm: Option<PopupTarget> = None;
        let mut best_dismiss: Option<PopupTarget> = None;

        for el in elements {
            let text = el.t.trim();
            if text.is_empty() {
                continue;
            }

            // 过滤对白跳过类提示 (此类由 DialogueSkipper 负责，不作为弹窗确认处理)
            if text.contains("点击") || text.contains("Tap to") || text.contains("任意区域") {
                continue;
            }

            // 计算控件中心坐标
            let cx = ((el.b[0] + el.b[2]) / 2) as u32;
            let cy = ((el.b[1] + el.b[3]) / 2) as u32;

            for &ck in &confirm_keywords {
                let is_match = text == ck || (text.contains(ck) && text.chars().count() <= 6);
                if is_match {
                    // 精确匹配优先级最高
                    if best_confirm.as_ref().map(|b| &b.title != ck).unwrap_or(true) {
                        best_confirm = Some(PopupTarget {
                            title: text.to_string(),
                            x: cx,
                            y: cy,
                            is_affirmative: true,
                        });
                    }
                    break;
                }
            }

            for &dk in &dismiss_keywords {
                let is_match = text == dk || (text.contains(dk) && text.chars().count() <= 6);
                if is_match {
                    if best_dismiss.as_ref().map(|b| &b.title != dk).unwrap_or(true) {
                        best_dismiss = Some(PopupTarget {
                            title: text.to_string(),
                            x: cx,
                            y: cy,
                            is_affirmative: false,
                        });
                    }
                    break;
                }
            }
        }

        match self.cfg.policy {
            PopupPolicy::PreferConfirm => best_confirm.or(best_dismiss),
            PopupPolicy::PreferDismiss => best_dismiss.or(best_confirm),
        }
    }

    /// 纯视觉启发式检测：检测是否存在居中卡片及其右上角关闭按键/底部动作栏
    fn find_in_vision(&self, img: &DynamicImage, w: u32, h: u32) -> Option<PopupTarget> {
        if w < 200 || h < 200 {
            return None;
        }

        // 采样屏幕四角与中央区域的亮度差异
        // 弹窗弹出时，背景通常有半透明黑色遮罩 (蒙层暗化，边缘亮度明显低于中心活动区域)
        let mut edge_luma_sum = 0u64;
        let mut edge_count = 0u64;
        // 采样上部边缘和下部边缘
        for x in (w / 8..w * 7 / 8).step_by(32) {
            let p_top = img.get_pixel(x, h / 12);
            let p_bottom = img.get_pixel(x, h * 11 / 12);
            edge_luma_sum += (p_top[0] as u64 + p_top[1] as u64 + p_top[2] as u64) / 3;
            edge_luma_sum += (p_bottom[0] as u64 + p_bottom[1] as u64 + p_bottom[2] as u64) / 3;
            edge_count += 2;
        }
        let avg_edge_luma = edge_luma_sum / edge_count.max(1);

        // 采样中心卡片区域
        let mut center_luma_sum = 0u64;
        let mut center_count = 0u64;
        for x in (w / 3..w * 2 / 3).step_by(24) {
            for y in (h / 3..h * 2 / 3).step_by(24) {
                let p = img.get_pixel(x, y);
                center_luma_sum += (p[0] as u64 + p[1] as u64 + p[2] as u64) / 3;
                center_count += 1;
            }
        }
        let avg_center_luma = center_luma_sum / center_count.max(1);

        // 蒙层典型特征：中心比边缘明显亮 (中心卡片亮底，四周暗黑遮罩)
        if avg_center_luma > 70 && avg_center_luma > avg_edge_luma + 30 {
            // 中心存在弹窗卡片，按倾向给出操作位置：
            // - 若关闭优先：点击居中卡片的右上角关闭按钮区域 (约 x 72%~82%, y 25%~35%)
            // - 若确认优先：点击卡片底部中央确认按钮区域 (约 x 50%, y 68%~75%)
            return match self.cfg.policy {
                PopupPolicy::PreferConfirm => Some(PopupTarget {
                    title: "视觉检测-居中确认按钮".into(),
                    x: (w as f32 * 0.50) as u32,
                    y: (h as f32 * 0.72) as u32,
                    is_affirmative: true,
                }),
                PopupPolicy::PreferDismiss => Some(PopupTarget {
                    title: "视觉检测-卡片右上角关闭".into(),
                    x: (w as f32 * 0.76) as u32,
                    y: (h as f32 * 0.30) as u32,
                    is_affirmative: false,
                }),
            };
        }

        None
    }

    /// 将识别到的目标转换为执行动作 (含防卡死逃离策略)
    fn resolve_target(&mut self, target: PopupTarget) -> UniversalAction {
        self.consecutive_popup_ticks += 1;
        self.last_target = Some(target.title.clone());

        // 连续多次点击仍未关闭弹窗，触发系统物理返回键脱困
        if self.consecutive_popup_ticks > self.cfg.max_stuck_attempts {
            println!(
                "[PopupCloser] 连续 {} 次处理弹窗 '{}' 未解除，触发物理返回键 (KEYCODE_BACK) 脱困",
                self.consecutive_popup_ticks, target.title
            );
            self.consecutive_popup_ticks = 0;
            return UniversalAction::Keycode {
                code: 4,
                label: Some("KEYCODE_BACK 弹窗逃生".into()),
            };
        }

        if self.cfg.gamepad_enabled {
            if target.is_affirmative {
                UniversalAction::GamepadButton {
                    button: "a".into(),
                    hold_ms: 120,
                }
            } else {
                UniversalAction::GamepadButton {
                    button: "b".into(),
                    hold_ms: 120,
                }
            }
        } else {
            UniversalAction::Tap {
                x: target.x,
                y: target.y,
                label: Some(format!("弹窗处理: {}", target.title)),
            }
        }
    }

    /// 重置状态
    pub fn reset(&mut self) {
        self.consecutive_popup_ticks = 0;
        self.last_target = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_element_popup_detection() {
        let mut closer = PopupCloser::new(PopupCloserConfig {
            policy: PopupPolicy::PreferConfirm,
            gamepad_enabled: false,
            max_stuck_attempts: 3,
        });

        let nodes = vec![
            Node { t: "温馨提示".into(), b: [200, 400, 800, 500] },
            Node { t: "欢迎体验新版本".into(), b: [200, 550, 800, 700] },
            Node { t: "我知道了".into(), b: [350, 800, 650, 900] },
            Node { t: "关闭".into(), b: [750, 380, 820, 440] },
        ];

        let action = closer.detect_and_resolve(&nodes, None, 1080, 2400);
        assert!(action.is_some());
        if let Some(UniversalAction::Tap { x, y, label }) = action {
            assert_eq!(x, 500);
            assert_eq!(y, 850);
            assert!(label.unwrap().contains("我知道了"));
        } else {
            panic!("预期产出点击动作");
        }
    }

    #[test]
    fn test_popup_stuck_escape() {
        let mut closer = PopupCloser::new(PopupCloserConfig {
            policy: PopupPolicy::PreferDismiss,
            gamepad_enabled: false,
            max_stuck_attempts: 2,
        });

        let nodes = vec![
            Node { t: "广告推广".into(), b: [100, 200, 900, 1500] },
            Node { t: "跳过".into(), b: [800, 220, 880, 280] },
        ];

        // 前两次产出普通点击
        let _ = closer.detect_and_resolve(&nodes, None, 1080, 2400);
        let _ = closer.detect_and_resolve(&nodes, None, 1080, 2400);

        // 第三次超过上限，应产出返回键
        let action = closer.detect_and_resolve(&nodes, None, 1080, 2400);
        assert_eq!(
            action,
            Some(UniversalAction::Keycode {
                code: 4,
                label: Some("KEYCODE_BACK 弹窗逃生".into()),
            })
        );
    }

    /// 回归: 计算器等普通应用界面不含任何弹窗, 通用弹窗算子必须完全放行,
    /// 不得把运算符按钮误判成确认/关闭控件而反复点击, 空烧步数预算。
    #[test]
    fn test_no_false_positive_on_plain_app_screen() {
        let mut closer = PopupCloser::new(PopupCloserConfig::default());
        let raw: Vec<(&str, [i32; 4])> = vec![
            ("更多操作", [399, 65, 470, 137]),
            ("没有任何结果", [0, 295, 657, 400]),
            ("AC", [24, 567, 180, 739]),
            ("%", [180, 567, 336, 739]),
            ("÷", [491, 567, 647, 739]),
            ("7", [24, 739, 180, 910]),
            ("×", [491, 739, 647, 910]),
            ("4", [24, 910, 180, 1082]),
            ("-", [491, 910, 647, 1082]),
            ("1", [24, 1082, 180, 1254]),
            ("+", [491, 1082, 647, 1254]),
            ("00", [24, 1254, 180, 1426]),
            ("=", [491, 1254, 647, 1426]),
        ];
        let nodes: Vec<Node> = raw.iter().map(|(t, b)| Node { t: (*t).into(), b: *b }).collect();
        assert_eq!(closer.detect_and_resolve(&nodes, None, 1216, 2688), None);
    }
}
