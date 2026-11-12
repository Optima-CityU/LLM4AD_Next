# n3_ui.py — 原神界面自动化 (adb input + 截图 OCR 核对)
#
# 这是 knobs/gray/npu_exp/n3_ui.py (N3, 分支 npu/n3-experiment) 的复制件, 给 O1 的 o1_fps_ui.py 改游戏内帧率用
# (R2 审查 #10: 不整份覆盖 playerprefs, 走游戏界面)。只改了 raw_to_png 的来源 (内联, 不依赖 n3_phone)。
#
# 原则:
#   1. 每一次点击之前先截图 + OCR, 核对当前画面是预期的那一屏, 再点 OCR 找到的目标 (或配置里标定过的坐标)。
#      对不上就停, 不盲点。
#   2. 绝不登录: 任何一屏出现 forbidden_text (登录/密码/验证码/支付/充值/下载…) 立刻停, 报「原神不在可玩状态」。
#   3. 每一步的截图与 OCR 结果都存盘 (ui/<步骤序号>_<名字>.{raw,png,ocr.json}), 出错时人能看到停在哪一屏。
#
# OCR 用仓库根的 ocr.swift (macOS Vision, 离线), 输出每行 {"t": 文字, "b": [x1,y1,x2,y2]}, 与 input tap 同坐标系。
import json
import os
import struct
import subprocess
import zlib


def raw_dims(d):
    """adb screencap 裸帧: 头 12 或 16 字节 (w,h,fmt[,colorspace]), 之后 RGBA。(同 n3_phone.raw_dims)"""
    if len(d) < 16:
        return None
    w, h, fmt = struct.unpack("<III", d[:12])
    for hdr in (16, 12):
        if len(d) - hdr == w * h * 4:
            return w, h, hdr
    return None


def raw_to_png(raw_path, png_path):
    """裸帧 → PNG (给 OCR 用)。(同 n3_phone.raw_to_png)"""
    d = open(raw_path, "rb").read()
    dims = raw_dims(d)
    if not dims:
        return False
    w, h, hdr = dims
    px = d[hdr:hdr + w * h * 4]
    stride = w * 4
    rows = b"".join(b"\x00" + px[y * stride:(y + 1) * stride] for y in range(h))

    def chunk(t, data):
        c = struct.pack(">I", len(data)) + t + data
        return c + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(rows, 3)) + chunk(b"IEND", b"")
    with open(png_path, "wb") as f:
        f.write(png)
    return True


class UiStop(Exception):
    """界面不对 / 不可玩 / 找不到目标: 停下, 不继续点。"""


class Ui:
    def __init__(self, phone, cfg, outdir, ocr_bin, log):
        self.p, self.cfg, self.out, self.ocr_bin, self.log = phone, cfg["ui"], outdir, ocr_bin, log
        self.n = 0
        os.makedirs(outdir, exist_ok=True)

    # ── 看 ──
    def look(self, name):
        self.n += 1
        stem = os.path.join(self.out, f"{self.n:03d}_{name}")
        if self.p.dry:
            items = self.p.ocr_items()
        else:
            if not self.p.screencap(stem + ".raw"):
                raise UiStop(f"截图失败 ({name})")
            raw_to_png(stem + ".raw", stem + ".png")
            r = subprocess.run([self.ocr_bin, stem + ".png"], capture_output=True, timeout=60)
            items = []
            for line in r.stdout.decode("utf-8", "replace").splitlines():
                try:
                    items.append(json.loads(line))
                except ValueError:
                    pass
        with open(stem + ".ocr.json", "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=0)
        bad = [i["t"] for i in items if any(w in i["t"] for w in self.cfg["forbidden_text"])]
        if bad:
            raise UiStop(f"原神不在可玩状态: 画面上出现 {bad} (截图 {stem}.png)。脚本不登录、不点这类界面, 已停止。"
                         f"请人工把原神弄到「点击进入」后能直接进大世界的状态再跑。")
        return items

    @staticmethod
    def find(items, text, exact=True):
        return [i for i in items if (i["t"] == text if exact else text in i["t"])]

    @staticmethod
    def center(i):
        b = i["b"]
        return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2

    def has_any(self, items, texts):
        return any(self.find(items, t, exact=False) for t in texts)

    def tap_item(self, i, why):
        x, y = self.center(i)
        self.log(f"    点 {why}: 「{i['t']}」 @ ({x:.0f},{y:.0f})")
        self.p.tap(x, y)

    def tap_xy(self, xy, why):
        self.log(f"    点 {why} @ {xy}")
        self.p.tap(*xy)

    def wait_for(self, name, texts, tries=6, gap=2.0):
        for _ in range(tries):
            self.p.sleep(gap)
            items = self.look(name)
            if self.has_any(items, texts):
                return items
        raise UiStop(f"等不到「{name}」这一屏 (要看到 {texts} 之一), 截图在 {self.out}")

    def confirm_dialogs(self, name, max_n=2):
        """有「确认/确定」且同屏有「取消」的弹窗就点确认 (设置改动的提示框)。"""
        for _ in range(max_n):
            items = self.look(name)
            ok = [i for t in self.cfg["confirm_text"] for i in self.find(items, t)]
            if ok and self.find(items, "取消"):
                self.tap_item(ok[0], "弹窗确认")
                self.p.sleep(2)
            else:
                return items
        return self.look(name)

    # ── 进门: 只点「点击进入」, 其余一律不点 ──
    def pass_gate(self, timeout_s):
        waited = 0
        while waited < timeout_s:
            items = self.look("gate")
            g = [i for t in self.cfg["gate_text"] for i in self.find(items, t, exact=False)]
            if g:
                self.tap_item(g[0], "进门")
                return True
            self.p.sleep(5)
            waited += 5
        return False

    def dismiss_popups(self):
        """进大世界后的弹窗 (月卡、活动): 只认「点击空白处关闭」一类文字, 点那行字本身。"""
        for _ in range(3):
            items = self.look("world_popup")
            c = [i for t in self.cfg["popup_close_text"] for i in self.find(items, t, exact=False)]
            if not c:
                return
            self.tap_item(c[0], "关弹窗")
            self.p.sleep(3)

    # ── 设置页 ──
    def open_paimon(self):
        self.tap_xy(self.cfg["paimon_xy"], "派蒙")
        return self.wait_for("paimon_menu", self.cfg["paimon_menu_text"])

    def open_graphics(self):
        items = self.open_paimon()
        if self.cfg.get("settings_xy"):
            self.tap_xy(self.cfg["settings_xy"], "设置(标定坐标)")
        else:
            s = [i for t in self.cfg["settings_text"] for i in self.find(items, t)]
            if not s:
                raise UiStop("派蒙菜单里 OCR 找不到「设置」; 跑一次 ui-probe, 看 ui/ 下派蒙菜单截图, "
                             "把齿轮图标坐标填进配置 ui.settings_xy")
            self.tap_item(s[0], "设置")
        items = self.wait_for("settings", self.cfg["settings_page_text"])
        tab = self.find(items, self.cfg["graphics_tab_text"])
        if not tab:
            raise UiStop("设置页里找不到「图像」页签")
        self.tap_item(tab[0], "图像页签")
        self.p.sleep(2)
        return self.look("graphics")

    def _row(self, items, row_text):
        r = self.find(items, row_text)
        return r[0] if r else None

    def find_row(self, row_text):
        items = self.look(f"row_{row_text}")
        for k in range(self.cfg["scroll_max"]):
            r = self._row(items, row_text)
            if r:
                return r, items
            sw = self.cfg["scroll_swipe"]
            self.p.swipe(*sw)
            self.p.sleep(1.5)
            items = self.look(f"row_{row_text}_scroll{k}")
        raise UiStop(f"图像设置里翻了 {self.cfg['scroll_max']} 屏也找不到「{row_text}」这一行")

    def row_values(self, items, row):
        """同一行 (纵向中心差 < 行高) 且在行名右边的文字。"""
        _, ry = self.center(row)
        hh = max(20, (row["b"][3] - row["b"][1]))
        return [i for i in items if i is not row and abs(self.center(i)[1] - ry) < hh and i["b"][0] > row["b"][2]]

    def set_option(self, row_text, label):
        """把「row_text」这一行设成 label。两种控件都认:
        横排按钮 (同一行就有 label) → 直接点; 下拉框 (同一行只有当前值) → 点当前值, 在弹出列表里点 label。
        回读: 下拉框的当前值能 OCR 到时核对; 横排按钮读不出选中态, 以 prefs 和 pass 表为准 (调用方核)。"""
        row, items = self.find_row(row_text)
        same = self.row_values(items, row)
        hit = [i for i in same if i["t"] == label]
        if hit:
            self.tap_item(hit[0], f"{row_text}={label}")
            self.p.sleep(1.5)
            self.confirm_dialogs(f"set_{row_text}_confirm")
            return {"control": "buttons", "readback": None}
        if not same:
            raise UiStop(f"「{row_text}」这一行右边 OCR 不到任何选项/当前值")
        cur = same[0]
        if cur["t"] == label:
            return {"control": "dropdown", "readback": label, "already": True, "orig_label": label}
        self.tap_item(cur, f"{row_text} 下拉(当前 {cur['t']})")
        self.p.sleep(1.5)
        items = self.look(f"dropdown_{row_text}")
        ry = self.center(row)[1]
        opts = [i for i in self.find(items, label) if abs(self.center(i)[1] - ry) > 10]
        if not opts:
            raise UiStop(f"「{row_text}」下拉列表里找不到「{label}」")
        self.tap_item(opts[0], f"{row_text}={label}")
        self.p.sleep(1.5)
        items = self.confirm_dialogs(f"set_{row_text}_confirm")
        r = self._row(items, row_text)
        rb = [i["t"] for i in self.row_values(items, r)] if r else []
        return {"control": "dropdown", "readback": rb[0] if rb else None, "orig_label": cur["t"]}

    def close_to_world(self):
        """关设置、关派蒙菜单回大世界: 默认用返回键, 每按一次都核对。"""
        for k in range(3):
            items = self.look(f"close_{k}")
            if not self.has_any(items, self.cfg["settings_page_text"] + self.cfg["paimon_menu_text"]):
                return True
            if self.cfg.get("close_xy"):
                self.tap_xy(self.cfg["close_xy"], "关闭(标定坐标)")
            else:
                self.p.key("BACK")
            self.p.sleep(2)
        raise UiStop("关不掉设置/派蒙菜单")

    def graceful_quit(self):
        """优雅退出让设置落盘 (#64 教训: 改完设置直接 force-stop 不可靠)。
        大世界里按返回键 → 「退出游戏」确认框 → 点确认; 等进程消失。"""
        self.p.key("BACK")
        self.p.sleep(2)
        items = self.look("quit_dialog")
        if not self.has_any(items, self.cfg["quit_dialog_text"]):
            if self.cfg.get("exit_xy"):
                self.open_paimon()
                self.tap_xy(self.cfg["exit_xy"], "退出(标定坐标)")
                self.p.sleep(2)
                items = self.look("quit_dialog2")
            if not self.has_any(items, self.cfg["quit_dialog_text"]):
                raise UiStop("按返回键没出现退出确认框; 标定 ui.exit_xy (派蒙菜单里的退出图标) 后重跑")
        ok = [i for t in self.cfg["confirm_text"] for i in self.find(items, t)]
        if not ok:
            raise UiStop("退出确认框里找不到「确认」")
        self.tap_item(ok[0], "确认退出")
        for _ in range(15):
            self.p.sleep(2)
            if not self.p.pidof():
                return True
        return False
