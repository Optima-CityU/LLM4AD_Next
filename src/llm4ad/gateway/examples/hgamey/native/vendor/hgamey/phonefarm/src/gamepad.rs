//! 虚拟手柄 (Virtual Gamepad / HID) 注入基础设施
//!
//! 基于 Android 内核 `/dev/uhid` 与系统 `hid` 命令通信通道，向系统注入全功能的
//! 虚拟 Xbox 无线手柄 (Xbox Wireless Controller, VID 0x045e, PID 0x02fd)。
//!
//! 具备以下优势：
//! 1. 物理连续摇杆量：左/右摇杆 360 度连续平滑推杆，右摇杆 3D 视角无顿挫转动。
//! 2. 免受触屏屏蔽：部分 3D 游戏在切换至手柄控制后会禁用屏幕触控，本驱动走原生 HID 链路。
//! 3. 守护连接保活：单个长连接进程常驻驱动，杜绝反复插拔导致的"手柄连接异常"断联弹窗。

use serde_json::json;
use std::io::Write;
use std::process::{Child, ChildStdin, Stdio};
use std::thread::sleep;
use std::time::Duration;

/// 摇杆中立位置常量 (16位无符号整数中间值 32767)
pub const AXIS_NEUTRAL: u16 = 0x7fff;
pub const _AXIS_MIN: u16 = 0x0000;
pub const _AXIS_MAX: u16 = 0xffff;

/// 触发器 (LT/RT) 范围常量 (10位精度 0 ~ 1023)
pub const TRIGGER_MIN: u16 = 0;
pub const TRIGGER_MAX: u16 = 1023;

/// 虚拟手柄瞬时状态镜像
#[derive(Debug, Clone)]
pub struct GamepadState {
    pub lx: u16,   // 左摇杆水平 (-1.0 -> 0x0000, 0.0 -> 0x7fff, 1.0 -> 0xffff)
    pub ly: u16,   // 左摇杆垂直 (-1.0 -> 0x0000(上), 0.0 -> 0x7fff, 1.0 -> 0xffff(下))
    pub rx: u16,   // 右摇杆水平 (视角)
    pub ry: u16,   // 右摇杆垂直 (视角)
    pub lt: u16,   // 左扳机 (0 ~ 1023)
    pub rt: u16,   // 右扳机 (0 ~ 1023)
    pub hat: u8,   // 十字方向键 (0=中立, 1=上, 2=右上, 3=右, 4=右下, 5=下, 6=左下, 7=左, 8=左上)
    pub b1: u8,    // 基础按键掩码
    pub b2: u8,    // 菜单与摇杆按键掩码
    pub b3: u8,    // 视图选择按键掩码
}

impl Default for GamepadState {
    fn default() -> Self {
        Self {
            lx: AXIS_NEUTRAL,
            ly: AXIS_NEUTRAL,
            rx: AXIS_NEUTRAL,
            ry: AXIS_NEUTRAL,
            lt: TRIGGER_MIN,
            rt: TRIGGER_MIN,
            hat: 0,
            b1: 0,
            b2: 0,
            b3: 0,
        }
    }
}

impl GamepadState {
    /// 转换为 Android HID 驱动所要求的 17 字节 Report #1
    pub fn to_hid_report(&self) -> Vec<u8> {
        vec![
            1, // Report ID
            (self.lx & 0xff) as u8,
            ((self.lx >> 8) & 0xff) as u8,
            (self.ly & 0xff) as u8,
            ((self.ly >> 8) & 0xff) as u8,
            (self.rx & 0xff) as u8,
            ((self.rx >> 8) & 0xff) as u8,
            (self.ry & 0xff) as u8,
            ((self.ry >> 8) & 0xff) as u8,
            (self.lt & 0xff) as u8,
            ((self.lt >> 8) & 0xff) as u8,
            (self.rt & 0xff) as u8,
            ((self.rt >> 8) & 0xff) as u8,
            self.hat,
            self.b1,
            self.b2,
            self.b3,
        ]
    }
}

/// 虚拟手柄控制器会话
pub struct Gamepad {
    child: Child,
    stdin: Option<ChildStdin>,
    state: GamepadState,
}

impl Gamepad {
    /// 在 PC 端通过 ADB 标准输入通道拉起与管理虚拟手柄会话
    /// 全生命周期完全受控于宿主机，手机端不落盘任何脚本或守护服务
    pub fn new(adb_bin: &str, serial: Option<&str>) -> Result<Self, String> {
        Self::new_named(adb_bin, serial, "Xbox Wireless Controller")
    }

    /// Use a session-specific input name when the caller verifies device removal.
    pub fn new_named(adb_bin: &str, serial: Option<&str>, name: &str) -> Result<Self, String> {
        let mut cmd = crate::fd_lock::transport_command(adb_bin);
        if let Some(s) = serial {
            cmd.arg("-s").arg(s);
        }
        cmd.args(["shell", "hid", "-"]);
        cmd.stdin(Stdio::piped());
        cmd.stdout(Stdio::null());
        cmd.stderr(Stdio::null());

        let mut child = cmd.spawn().map_err(|e| format!("启动 ADB hid 注入管道失败: {e}"))?;
        let mut stdin = child.stdin.take().ok_or("无法获取手柄管道的标准输入")?;

        // 注册标准 Xbox Wireless Controller HID 描述符 (通过标准输入下发，纯内存通信)
        const REG_JSON: &str = r#"{"id":1,"command":"register","name":"Xbox Wireless Controller","vid":1118,"pid":765,"bus":"bluetooth","source":"KEYBOARD | GAMEPAD | JOYSTICK","descriptor":[5,1,9,5,161,1,133,1,9,1,161,0,9,48,9,49,21,0,39,255,255,0,0,149,2,117,16,129,2,192,9,1,161,0,9,50,9,53,21,0,39,255,255,0,0,149,2,117,16,129,2,192,5,2,9,197,21,0,38,255,3,149,1,117,10,129,2,21,0,37,0,117,6,149,1,129,3,5,2,9,196,21,0,38,255,3,149,1,117,10,129,2,21,0,37,0,117,6,149,1,129,3,5,1,9,57,21,1,37,8,53,0,70,59,1,102,20,0,117,4,149,1,129,66,117,4,149,1,21,0,37,0,53,0,69,0,101,0,129,3,5,9,25,1,41,15,21,0,37,1,117,1,149,15,129,2,21,0,37,0,117,1,149,1,129,3,5,12,10,36,2,21,0,37,1,149,1,117,1,129,2,21,0,37,0,117,7,149,1,129,3,192,5,15,9,33,133,3,161,2,9,151,21,0,37,1,117,4,149,1,145,2,21,0,37,0,117,4,149,1,145,3,9,112,21,0,37,100,117,8,149,4,145,2,9,80,102,1,16,85,14,21,0,38,255,0,117,8,149,1,145,2,9,167,21,0,38,255,0,117,8,149,1,145,2,101,0,85,0,9,124,21,0,38,255,0,117,8,149,1,145,2,192,5,6,9,32,133,4,21,0,38,255,0,117,8,149,1,129,2,192]}"#;

        let mut registration: serde_json::Value = serde_json::from_str(REG_JSON)
            .map_err(|e| format!("无效的手柄注册模板: {e}"))?;
        registration["name"] = json!(name);
        let registration = serde_json::to_vec(&registration)
            .map_err(|e| format!("编码手柄注册报文失败: {e}"))?;
        stdin.write_all(&registration).map_err(|e| format!("注册虚拟手柄失败: {e}"))?;
        stdin.write_all(b"\n").map_err(|e| format!("写入换行失败: {e}"))?;
        stdin.flush().map_err(|e| format!("刷新注册报文失败: {e}"))?;

        // 等待内核完成设备注册并接入输入子系统
        sleep(Duration::from_millis(150));

        let mut gp = Self {
            child,
            stdin: Some(stdin),
            state: GamepadState::default(),
        };

        // 发送初始中立帧确保设备握手
        gp.send_current_state()?;
        Ok(gp)
    }

    /// 向底层写入当前状态报文
    pub fn send_current_state(&mut self) -> Result<(), String> {
        let rep = self.state.to_hid_report();
        let cmd = json!({
            "id": 1,
            "command": "report",
            "report": rep
        });
        let mut msg = serde_json::to_string(&cmd).map_err(|e| e.to_string())?;
        msg.push('\n');
        let stdin = self.stdin.as_mut().ok_or("手柄管道已关闭")?;
        stdin.write_all(msg.as_bytes()).map_err(|e| format!("写入 HID 报文失败: {e}"))?;
        stdin.flush().map_err(|e| format!("刷新 HID 缓冲失败: {e}"))?;
        Ok(())
    }

    /// 解析按键名称为对应的位掩码与字节索引
    fn parse_button_mask(btn: &str) -> Option<(usize, u8)> {
        match btn.trim().to_lowercase().as_str() {
            "a" => Some((1, 0x01)),
            "b" => Some((1, 0x02)),
            "x" => Some((1, 0x08)),
            "y" => Some((1, 0x10)),
            "lb" | "l1" => Some((1, 0x40)),
            "rb" | "r1" => Some((1, 0x80)),
            "start" | "menu" => Some((2, 0x08)),
            "mode" | "xbox" | "home" => Some((2, 0x10)),
            "thumbl" | "l3" => Some((2, 0x20)),
            "thumbr" | "r3" => Some((2, 0x40)),
            "select" | "back" | "view" => Some((3, 0x01)),
            _ => None,
        }
    }

    /// 按下按键 (保持按下状态，不松开)
    pub fn button_down(&mut self, btn: &str) -> Result<(), String> {
        let b = btn.trim().to_lowercase();
        if b == "lt" || b == "l2" {
            self.state.lt = TRIGGER_MAX;
            return self.send_current_state();
        }
        if b == "rt" || b == "r2" {
            self.state.rt = TRIGGER_MAX;
            return self.send_current_state();
        }
        if let Some((idx, mask)) = Self::parse_button_mask(&b) {
            match idx {
                1 => self.state.b1 |= mask,
                2 => self.state.b2 |= mask,
                3 => self.state.b3 |= mask,
                _ => {}
            }
            self.send_current_state()
        } else {
            Err(format!("未知的手柄按键: {btn}"))
        }
    }

    /// 释放按键
    pub fn button_up(&mut self, btn: &str) -> Result<(), String> {
        let b = btn.trim().to_lowercase();
        if b == "lt" || b == "l2" {
            self.state.lt = TRIGGER_MIN;
            return self.send_current_state();
        }
        if b == "rt" || b == "r2" {
            self.state.rt = TRIGGER_MIN;
            return self.send_current_state();
        }
        if let Some((idx, mask)) = Self::parse_button_mask(&b) {
            match idx {
                1 => self.state.b1 &= !mask,
                2 => self.state.b2 &= !mask,
                3 => self.state.b3 &= !mask,
                _ => {}
            }
            self.send_current_state()
        } else {
            Err(format!("未知的手柄按键: {btn}"))
        }
    }

    /// 点击按键并在指定延时后自动释放
    pub fn press(&mut self, btn: &str, duration_ms: u64) -> Result<(), String> {
        self.button_down(btn)?;
        sleep(Duration::from_millis(duration_ms.max(20)));
        self.button_up(btn)
    }

    /// 设置摇杆坐标 (stick="left" 或 "right", x/y 浮点范围 -1.0 到 1.0)
    pub fn set_stick(&mut self, stick: &str, x: f32, y: f32) -> Result<(), String> {
        let map_axis = |val: f32| -> u16 {
            let clamped = val.clamp(-1.0, 1.0);
            let raw = (clamped * 32767.0 + 32767.0).round();
            (raw as i64).clamp(0, 65535) as u16
        };

        match stick.trim().to_lowercase().as_str() {
            "left" | "l" | "ls" => {
                self.state.lx = map_axis(x);
                self.state.ly = map_axis(y);
            }
            "right" | "r" | "rs" => {
                self.state.rx = map_axis(x);
                self.state.ry = map_axis(y);
            }
            other => return Err(format!("未知的摇杆名称: {other} (有效值: left, right)")),
        }

        self.send_current_state()
    }

    /// 设置扳机深度 (0.0 ~ 1.0)
    pub fn set_trigger(&mut self, trigger: &str, val: f32) -> Result<(), String> {
        let raw = (val.clamp(0.0, 1.0) * 1023.0).round() as u16;
        match trigger.trim().to_lowercase().as_str() {
            "lt" | "l2" | "left" => self.state.lt = raw,
            "rt" | "r2" | "right" => self.state.rt = raw,
            other => return Err(format!("未知的扳机名称: {other}")),
        }
        self.send_current_state()
    }

    /// 设置十字方向键 (dir: "up", "down", "left", "right", "center"/"none" 等)
    pub fn set_dpad(&mut self, dir: &str) -> Result<(), String> {
        self.state.hat = match dir.trim().to_lowercase().as_str() {
            "up" | "u" => 1,
            "up_right" | "ur" => 2,
            "right" | "r" => 3,
            "down_right" | "dr" => 4,
            "down" | "d" => 5,
            "down_left" | "dl" => 6,
            "left" | "l" => 7,
            "up_left" | "ul" => 8,
            "center" | "none" | "release" | "" => 0,
            other => return Err(format!("未知的十字键方向: {other}")),
        };
        self.send_current_state()
    }

    /// 重置所有摇杆、扳机与按键为中立状态
    pub fn reset(&mut self) -> Result<(), String> {
        self.state = GamepadState::default();
        self.send_current_state()
    }

    /// 巡航漫步动作 (Wander): 专为大世界防烧屏与探索压测设计的组合原语。
    /// 在指定持续时间内，平滑推行左摇杆行走、偏转右摇杆旋转 3D 视角，并随机触发跳跃/冲刺。
    pub fn wander(&mut self, duration_ms: u64) -> Result<(), String> {
        use std::time::Instant;
        let t0 = Instant::now();
        let total = Duration::from_millis(duration_ms);

        let mut step_count = 0u32;
        while t0.elapsed() < total {
            step_count += 1;
            // 依据随机相位构造平滑向量
            let seed = (step_count as f32) * 0.73;
            let move_x = seed.cos() * 0.85;
            let move_y = seed.sin() * 0.85;
            let cam_x = (seed * 1.3).sin() * 0.65;
            let cam_y = (seed * 0.5).cos() * 0.25;

            self.set_stick("left", move_x, move_y)?;
            self.set_stick("right", cam_x, cam_y)?;

            // 偶尔触发跳跃 (A键) 或冲刺 (RT) 保持动作多样性
            if step_count % 7 == 0 {
                self.button_down("a")?;
                sleep(Duration::from_millis(100));
                self.button_up("a")?;
            } else if step_count % 11 == 0 {
                self.button_down("rt")?;
                sleep(Duration::from_millis(150));
                self.button_up("rt")?;
            } else {
                sleep(Duration::from_millis(120));
            }
        }

        self.reset()
    }
}

impl Drop for Gamepad {
    fn drop(&mut self) {
        // 尝试发送中立状态并关闭标准输入以让系统卸载虚拟设备
        let _ = self.reset();
        // EOF lets `hid` unregister the device and the adb client exit itself.
        // `child` is the transport proxy: SIGKILL would orphan that client in
        // the caller's process group, so escalate through SIGTERM, which the
        // proxy forwards, before killing it.
        drop(self.stdin.take());
        let exited = |child: &mut Child, ms: u64| {
            let deadline = std::time::Instant::now() + Duration::from_millis(ms);
            loop {
                match child.try_wait() {
                    Ok(Some(_)) | Err(_) => return true,
                    Ok(None) if std::time::Instant::now() >= deadline => return false,
                    Ok(None) => sleep(Duration::from_millis(20)),
                }
            }
        };
        if exited(&mut self.child, 3000) {
            return;
        }
        unsafe { libc::kill(self.child.id() as i32, libc::SIGTERM) };
        if !exited(&mut self.child, 1000) {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn drop_waits_for_the_transport_client_to_exit() {
        let dir = std::env::temp_dir().join(format!("pf-gamepad-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let adb = dir.join("adb");
        let mark = dir.join("client-exited");
        // Like `adb shell hid -`: unregister after stdin EOF, then exit.
        std::fs::write(&adb, format!("#!/bin/sh\ncat >/dev/null\nsleep 0.3\ntouch '{}'\n", mark.display())).unwrap();
        std::fs::set_permissions(&adb, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
        let gamepad = Gamepad::new_named(adb.to_str().unwrap(), Some("fake"), "test pad").unwrap();
        drop(gamepad);
        let exited = mark.exists();
        let _ = std::fs::remove_dir_all(&dir);
        assert!(exited, "the adb client must have exited when the gamepad is dropped");
    }
}
