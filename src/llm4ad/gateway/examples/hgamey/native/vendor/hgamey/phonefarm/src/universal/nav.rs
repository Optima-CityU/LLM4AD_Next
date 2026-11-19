//! 通用导航寻路器 (Universal Nav Walker)
//!
//! 在大世界游戏跑图、RPG 任务导航以及常规 App 的长列表/信息流遍历中，
//! 移动与寻路是核心动作。
//!
//! NavWalker 统一提供：
//! 1. 目标导向循迹 (根据标记方位角自动微调视角并全速奔跑推进)
//! 2. 漫游探索与巡航 (无明确引导时平稳环视与巡检周边)
//! 3. 列表滚动遍历 (用于 App 菜单或长页面内容搜寻)
//! 4. 障碍脱困防卡死看门狗 (检测视野与位移停滞，自动注入后撤、跳跃与变向动作)

use std::time::{Duration, Instant};
use image::{DynamicImage, GenericImageView};
use super::action::UniversalAction;

/// 导航工作模式
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NavMode {
    /// 3D/2.5D 自由视角循迹模式 (通过手柄双摇杆或触屏滑动控制)
    FreeMovement3D,
    /// 2D 俯视/横版移动模式 (纯平面坐标平移)
    TopDown2D,
    /// 列表/流式页面滚动模式 (垂直或水平滑动)
    ListScroll,
}

/// 寻路导航配置
#[derive(Debug, Clone)]
pub struct NavWalkerConfig {
    pub mode: NavMode,
    /// 是否优先使用手柄摇杆控制
    pub gamepad_enabled: bool,
    /// 连续卡死触发脱困判定时长 (秒)
    pub stuck_timeout_secs: u64,
}

impl Default for NavWalkerConfig {
    fn default() -> Self {
        Self {
            mode: NavMode::FreeMovement3D,
            gamepad_enabled: false,
            stuck_timeout_secs: 4,
        }
    }
}

/// 通用导航寻路器
pub struct NavWalker {
    cfg: NavWalkerConfig,
    last_progress_time: Instant,
    stuck_counter: u32,
}

impl NavWalker {
    pub fn new(cfg: NavWalkerConfig) -> Self {
        Self {
            cfg,
            last_progress_time: Instant::now(),
            stuck_counter: 0,
        }
    }

    /// 根据视野中目标方位偏离度 (marker_x: 0.0 ~ 1.0) 给出转向与推进动作
    pub fn step_steer_and_move(&mut self, marker_x: Option<f32>) -> Vec<UniversalAction> {
        let mut actions = Vec::new();

        if let Some(mx) = marker_x {
            self.last_progress_time = Instant::now();
            self.stuck_counter = 0;

            if mx < 0.35 {
                // 大幅偏左
                println!("[NavWalker] 目标偏左 ({:.1}%) -> 大幅左偏视角", mx * 100.0);
                if self.cfg.gamepad_enabled {
                    actions.push(UniversalAction::GamepadStick {
                        stick: "right".into(),
                        x: -0.85,
                        y: 0.0,
                        duration_ms: 350,
                    });
                } else {
                    // 屏幕右半侧向右滑 (相当于视角向左转)
                    actions.push(UniversalAction::Swipe {
                        x1: 1600, y1: 700, x2: 2000, y2: 700, duration_ms: 300,
                    });
                }
            } else if mx < 0.44 {
                // 轻微偏左
                println!("[NavWalker] 目标轻微偏左 ({:.1}%) -> 微调视角", mx * 100.0);
                if self.cfg.gamepad_enabled {
                    actions.push(UniversalAction::GamepadStick {
                        stick: "right".into(),
                        x: -0.45,
                        y: 0.0,
                        duration_ms: 200,
                    });
                } else {
                    actions.push(UniversalAction::Swipe {
                        x1: 1700, y1: 700, x2: 1900, y2: 700, duration_ms: 200,
                    });
                }
            } else if mx > 0.65 {
                // 大幅偏右
                println!("[NavWalker] 目标偏右 ({:.1}%) -> 大幅右偏视角", mx * 100.0);
                if self.cfg.gamepad_enabled {
                    actions.push(UniversalAction::GamepadStick {
                        stick: "right".into(),
                        x: 0.85,
                        y: 0.0,
                        duration_ms: 350,
                    });
                } else {
                    actions.push(UniversalAction::Swipe {
                        x1: 2000, y1: 700, x2: 1600, y2: 700, duration_ms: 300,
                    });
                }
            } else if mx > 0.56 {
                // 轻微偏右
                println!("[NavWalker] 目标轻微偏右 ({:.1}%) -> 微调视角", mx * 100.0);
                if self.cfg.gamepad_enabled {
                    actions.push(UniversalAction::GamepadStick {
                        stick: "right".into(),
                        x: 0.45,
                        y: 0.0,
                        duration_ms: 200,
                    });
                } else {
                    actions.push(UniversalAction::Swipe {
                        x1: 1900, y1: 700, x2: 1700, y2: 700, duration_ms: 200,
                    });
                }
            } else {
                // 正居中：全速向前奔跑推进
                println!("[NavWalker] 目标居中 ({:.1}%) -> 全速前进奔跑", mx * 100.0);
                if self.cfg.gamepad_enabled {
                    actions.push(UniversalAction::GamepadStick {
                        stick: "left".into(),
                        x: 0.0,
                        y: -1.0,
                        duration_ms: 1500,
                    });
                    // 按加速/疾跑按键
                    actions.push(UniversalAction::GamepadButton {
                        button: "b".into(),
                        hold_ms: 100,
                    });
                } else {
                    // 左下角虚拟摇杆向前推
                    actions.push(UniversalAction::Swipe {
                        x1: 450, y1: 850, x2: 450, y2: 600, duration_ms: 1200,
                    });
                }
            }
        } else {
            // 无明确标记，执行平稳漫游与巡检
            println!("[NavWalker] 未捕获特定目标 -> 执行环境环视漫游");
            if self.cfg.gamepad_enabled {
                actions.push(UniversalAction::GamepadStick {
                    stick: "right".into(),
                    x: 0.40,
                    y: 0.0,
                    duration_ms: 600,
                });
                actions.push(UniversalAction::GamepadStick {
                    stick: "left".into(),
                    x: 0.0,
                    y: -0.6,
                    duration_ms: 800,
                });
            } else {
                actions.push(UniversalAction::Swipe {
                    x1: 1800, y1: 700, x2: 1500, y2: 700, duration_ms: 400,
                });
            }
        }

        actions
    }

    /// 列表页面滑动探索
    pub fn step_scroll(&self, scroll_down: bool, screen_w: u32, screen_h: u32) -> UniversalAction {
        let cx = screen_w / 2;
        if scroll_down {
            // 向下滑动查看下一页 (手势从下往上拉)
            let start_y = (screen_h as f32 * 0.75) as u32;
            let end_y = (screen_h as f32 * 0.25) as u32;
            UniversalAction::Swipe {
                x1: cx, y1: start_y, x2: cx, y2: end_y, duration_ms: 350,
            }
        } else {
            // 向上滑动回看上一页 (手势从上往下拉)
            let start_y = (screen_h as f32 * 0.25) as u32;
            let end_y = (screen_h as f32 * 0.75) as u32;
            UniversalAction::Swipe {
                x1: cx, y1: start_y, x2: cx, y2: end_y, duration_ms: 350,
            }
        }
    }

    /// 脱困策略：检测角色在障碍物/死角卡死时注入逃生动作序列
    pub fn check_and_unstuck(&mut self) -> Option<Vec<UniversalAction>> {
        if self.last_progress_time.elapsed() > Duration::from_secs(self.cfg.stuck_timeout_secs) {
            self.stuck_counter += 1;
            self.last_progress_time = Instant::now();
            println!("[NavWalker] 监测到导航停滞超时，执行第 {} 级脱困动作", self.stuck_counter);

            let mut escape_actions = Vec::new();
            if self.cfg.gamepad_enabled {
                // 1. 后撤拉开距离
                escape_actions.push(UniversalAction::GamepadStick {
                    stick: "left".into(),
                    x: 0.0,
                    y: 1.0,
                    duration_ms: 600,
                });
                // 2. 跳跃脱离地形卡角
                escape_actions.push(UniversalAction::GamepadButton {
                    button: "y".into(),
                    hold_ms: 120,
                });
                // 3. 转向
                escape_actions.push(UniversalAction::GamepadStick {
                    stick: "right".into(),
                    x: 0.8,
                    y: 0.0,
                    duration_ms: 400,
                });
            } else {
                // 触控后退与滑动跳跃
                escape_actions.push(UniversalAction::Swipe {
                    x1: 450, y1: 700, x2: 450, y2: 950, duration_ms: 500,
                });
                escape_actions.push(UniversalAction::Tap {
                    x: 2100, y: 850, label: Some("脱困跳跃".into()),
                });
            }
            return Some(escape_actions);
        }
        None
    }

    /// 从画面视觉中检测任务导航菱形标记方位 (横屏返回 0.0 ~ 1.0 的 x 坐标，竖屏同理)
    pub fn scan_waypoint_marker(img: &DynamicImage) -> Option<f32> {
        let (w, h) = img.dimensions();
        if w < 100 || h < 100 {
            return None;
        }

        let m_y_start = (h as f32 * 0.15) as u32;
        let m_y_end = (h as f32 * 0.55) as u32;
        let mut max_cyan_score = 0;
        let mut best_x = 0;

        for x in (w / 12..w * 11 / 12).step_by(12) {
            let mut col_cyan = 0;
            for y in (m_y_start..m_y_end).step_by(8) {
                let p = img.get_pixel(x, y);
                // 蓝绿色/高光金色任务图标特征
                if (p[0] < 120 && p[1] > 180 && p[2] > 220) || (p[0] > 220 && p[1] > 190 && p[2] < 70) {
                    col_cyan += 1;
                }
            }
            if col_cyan > max_cyan_score && col_cyan >= 3 {
                max_cyan_score = col_cyan;
                best_x = x;
            }
        }

        if max_cyan_score >= 3 {
            Some(best_x as f32 / w as f32)
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_nav_centered_movement() {
        let mut walker = NavWalker::new(NavWalkerConfig {
            mode: NavMode::FreeMovement3D,
            gamepad_enabled: true,
            stuck_timeout_secs: 5,
        });

        // 目标位于正中 50%
        let actions = walker.step_steer_and_move(Some(0.50));
        assert_eq!(actions.len(), 2);
        assert_eq!(
            actions[0],
            UniversalAction::GamepadStick {
                stick: "left".into(),
                x: 0.0,
                y: -1.0,
                duration_ms: 1500,
            }
        );
        assert_eq!(
            actions[1],
            UniversalAction::GamepadButton {
                button: "b".into(),
                hold_ms: 100,
            }
        );
    }

    #[test]
    fn test_scroll_down_generation() {
        let walker = NavWalker::new(NavWalkerConfig::default());
        let act = walker.step_scroll(true, 1080, 2400);
        assert_eq!(
            act,
            UniversalAction::Swipe {
                x1: 540,
                y1: 1800,
                x2: 540,
                y2: 600,
                duration_ms: 350,
            }
        );
    }
}
