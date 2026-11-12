#!/usr/bin/env python3
"""o1_fps_ui.py — 在原神游戏界面里改「帧率」档位 (30 / 60), 不碰 playerprefs 文件。

R2 审查 #10: genshin_fpscap.sh 以前用 `cat 新文件 > 原文件` 整份覆盖偏好设置文件, 与「绝不覆盖」有边界;
这里改走游戏自己的设置界面 (派蒙 → 设置 → 图像 → 帧率), 做法和截图/OCR 核对逻辑照抄 N3 的 n3_ui.py
(同目录的复制件): 每次点击前截图 + OCR 核对当前屏, 对不上就停, 不盲点; 出现登录/支付/下载等字样立刻停。

前置: 游戏已在大世界 (run_refresh_ab.sh 用 phonefarm onboard 带进去), 屏幕坐标系 = adb screencap 像素。
OCR 用仓库根的 ocr.swift (macOS Vision, 离线), 首次用 swiftc 编到 <证据目录>/tools/ocr。

用法:
  python3 -I o1_fps_ui.py set <30|60> <证据目录>      # 改到该档, 首次记原档到 <证据目录>/fpscap_ui_orig.txt
  python3 -I o1_fps_ui.py restore <证据目录>         # 改回 fpscap_ui_orig.txt 里的原档, 然后优雅退出让设置落盘
  python3 -I o1_fps_ui.py probe <证据目录>           # 只打开图像设置页截图 + OCR 出「帧率」那一行的选项, 不改
输出最后一行:
  FPSUI_SET label=<档> orig_label=<原档|?> control=<buttons|dropdown> readback=<回读|None>
  FPSUI_RESTORED label=<原档>  /  FPSUI_RESTORE_FAIL <原因>  /  FPSUI_STOP <原因>
退出码: 0 成功; 3 界面不对/不可玩 (已停, 没盲点); 4 还原失败; 2 用法错。
环境变量: SERIAL (缺省 91253241019A) PKG ADB DRY_RUN(1 = 不碰手机, 只打印) O1_UI_CONFIG (覆盖界面文字/坐标的 JSON)
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from n3_ui import Ui, UiStop, raw_dims  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
# 界面文字/坐标: 与 N3 的 n3_config.json ui 段相同 (红魔 NX809J 2688x1216, 国服)。null 的坐标由 ui-probe 标定后填。
DEFAULT_UI = {
    "forbidden_text": ["登录", "密码", "验证码", "手机号", "注册", "实名", "支付", "充值", "下载资源", "正在下载"],
    "gate_text": ["点击进入"],
    "popup_close_text": ["点击空白处关闭", "点击空白区域关闭", "点击任意位置关闭", "点击屏幕继续"],
    "paimon_xy": [190, 90],
    "paimon_menu_text": ["冒险等阶", "世界等级", "编辑资料"],
    "settings_xy": None,
    "settings_text": ["设置"],
    "settings_page_text": ["图像", "音频", "控制"],
    "graphics_tab_text": "图像",
    "scroll_swipe": [1700, 900, 1700, 450, 500],
    "scroll_max": 4,
    "close_xy": None,
    "exit_xy": None,
    "quit_dialog_text": ["退出游戏", "是否退出"],
    "confirm_text": ["确认", "确定"],
    "fps_row_text": "帧率",
}


class Phone:
    """只给 n3_ui.Ui 用的最小设备层: 截图 / 点 / 按键 / 滑 / pidof。全部经 adb, 逐条写 cmd.log。"""
    dry = False

    def __init__(self, serial, pkg, logf):
        self.serial, self.pkg, self.logf = serial, pkg, logf
        self.adb_bin = os.environ.get("ADB", "adb")

    def _log(self, s):
        with open(self.logf, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + s + "\n")

    def sh(self, cmd, timeout=60):
        self._log("adb shell " + cmd)
        try:
            p = subprocess.run([self.adb_bin, "-s", self.serial, "shell", cmd], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return ""
        return p.stdout.decode("utf-8", "replace").replace("\r", "").strip()

    def sleep(self, s):
        time.sleep(s)

    def pidof(self):
        return self.sh(f"pidof {self.pkg}")

    def screencap(self, host_path):
        self._log(f"screencap -> {host_path}")
        try:
            with open(host_path, "wb") as f:
                p = subprocess.run([self.adb_bin, "-s", self.serial, "exec-out", "screencap"],
                                   stdout=f, stdin=subprocess.DEVNULL, timeout=30)
        except subprocess.TimeoutExpired:
            return False
        return p.returncode == 0 and raw_dims(open(host_path, "rb").read()) is not None

    def tap(self, x, y):
        self.sh(f"input tap {int(x)} {int(y)}")

    def key(self, k):
        self.sh(f"input keyevent {k}")

    def swipe(self, x1, y1, x2, y2, ms=400):
        self.sh(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {ms}")


def ocr_bin(out):
    dst = os.path.join(out, "tools", "ocr")
    if os.path.exists(dst):
        return dst
    src = os.path.join(REPO, "ocr.swift")
    if not os.path.exists(src):
        raise SystemExit(f"FPSUI_STOP 找不到 {src} (OCR 工具源码)")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    p = subprocess.run(["swiftc", "-O", src, "-o", dst], capture_output=True, timeout=600)
    if p.returncode != 0:
        raise SystemExit("FPSUI_STOP 编不出 OCR 工具: " + p.stderr.decode("utf-8", "replace")[-400:])
    return dst


def load_cfg():
    ui = dict(DEFAULT_UI)
    p = os.environ.get("O1_UI_CONFIG")
    if p:
        ui.update(json.load(open(p, encoding="utf-8")))
    return {"ui": ui}


def make(out):
    os.makedirs(out, exist_ok=True)
    serial = os.environ.get("SERIAL", "91253241019A")
    pkg = os.environ.get("PKG", "com.miHoYo.Yuanshen")
    cfg = load_cfg()
    phone = Phone(serial, pkg, os.path.join(out, "fps_ui_cmd.log"))
    uidir = os.path.join(out, "ui_" + time.strftime("%Y%m%d-%H%M%S"))
    log = lambda s: print("  " + s, file=sys.stderr)
    ui = Ui(phone, cfg, uidir, ocr_bin(out), log)
    return phone, ui, cfg["ui"]


def in_world_or_stop(phone, ui):
    if not phone.pidof():
        raise UiStop("原神进程不在 (要先启动并进大世界; 本脚本不启动游戏、不登录)")
    ui.dismiss_popups()


def set_fps(label, out):
    phone, ui, c = make(out)
    in_world_or_stop(phone, ui)
    ui.open_graphics()
    r = ui.set_option(c["fps_row_text"], label)
    ui.close_to_world()
    orig = r.get("orig_label")
    rec = os.path.join(out, "fpscap_ui_orig.txt")
    if not os.path.exists(rec):   # 只在第一次记原档
        with open(rec, "x", encoding="utf-8") as f:
            f.write(f"orig_fps_label={orig if orig else '?'}\nset_to={label}\nt={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    print(f"FPSUI_SET label={label} orig_label={orig if orig else '?'} control={r.get('control')} readback={r.get('readback')}")
    if r.get("control") == "dropdown" and r.get("readback") not in (None, label):
        print(f"FPSUI_STOP 下拉框回读 {r.get('readback')} ≠ {label}")
        return 3
    return 0


def restore_fps(out):
    rec = os.path.join(out, "fpscap_ui_orig.txt")
    if not os.path.exists(rec):
        print("FPSUI_RESTORED (本次没在界面里改过帧率, 不用还原)")
        return 0
    orig = next((l.split("=", 1)[1].strip() for l in open(rec, encoding="utf-8") if l.startswith("orig_fps_label=")), "?")
    if orig in ("", "?"):
        print("FPSUI_RESTORE_FAIL 原档没读到 (set 时控件是横排按钮, OCR 读不出选中态): 请人工在游戏里核对帧率档")
        return 4
    phone, ui, c = make(out)
    try:
        in_world_or_stop(phone, ui)
        ui.open_graphics()
        r = ui.set_option(c["fps_row_text"], orig)
        ui.close_to_world()
        quit_ok = ui.graceful_quit()   # 优雅退出让设置落盘
    except UiStop as e:
        print(f"FPSUI_RESTORE_FAIL {e}")
        return 4
    print(f"FPSUI_RESTORED label={orig} readback={r.get('readback')} graceful_quit={quit_ok}")
    return 0


def probe(out):
    phone, ui, c = make(out)
    in_world_or_stop(phone, ui)
    ui.open_graphics()
    row, items = ui.find_row(c["fps_row_text"])
    vals = [i["t"] for i in ui.row_values(items, row)]
    ui.close_to_world()
    print(f"FPSUI_PROBE row={c['fps_row_text']} values={vals}")
    return 0


def main(argv):
    if os.environ.get("DRY_RUN") == "1":
        print(f"[dry-run] o1_fps_ui.py {' '.join(argv)} (不碰手机)", file=sys.stderr)
        if argv and argv[0] == "set":
            print(f"FPSUI_SET label={argv[1] if len(argv) > 1 else '?'} orig_label=? control=dry readback=None")
        elif argv and argv[0] == "restore":
            print("FPSUI_RESTORED (dry-run)")
        return 0
    try:
        if len(argv) == 3 and argv[0] == "set" and argv[1] in ("30", "60"):
            return set_fps(argv[1], argv[2])
        if len(argv) == 2 and argv[0] == "restore":
            return restore_fps(argv[1])
        if len(argv) == 2 and argv[0] == "probe":
            return probe(argv[1])
    except UiStop as e:
        print(f"FPSUI_STOP {e}")
        return 3
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
