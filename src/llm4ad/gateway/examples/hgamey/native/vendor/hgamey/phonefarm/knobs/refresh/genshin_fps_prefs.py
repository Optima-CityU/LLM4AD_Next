#!/usr/bin/env python3
"""原神 (国服) 偏好设置里的「帧率」档位: 读 / 改一位数字。

文件: /data/data/com.miHoYo.Yuanshen/shared_prefs/com.miHoYo.Yuanshen.v2.playerprefs.xml
GENERAL_DATA 是 quote(safe='') 编码的 JSON, 其中 globalPerfData.saveItems 里
  entryType 1 = 帧率, entryType 2 = 渲染精度 (见 docs/experiments/2026-10-20-npu-sr/genshin_prefs.py)。

做法沿用 2026-10-20 NPU 实验: 只做文本级替换 (只动那一个 index 数字), 其余字节原样保留,
不重新序列化 (重新序列化会改动浮点数写法)。

档位与帧率的对应 (index -> fps):
  2 = 60  —— 推断: 2026-10-20 备份里 index=2, 当时游戏内设的是 60 帧且实测 59.1-59.6 fps
  1 = 30  —— 推断: 按原神设置项的枚举顺序, 未在本机实测
  上机脚本必须用实测上屏帧率核对, 对不上就停 (run_refresh_ab.sh 的 cap 核对)。

用法:
  python3 -I genshin_fps_prefs.py get <xml>                 # 打印 "fps_index=<n> render_index=<n>"
  python3 -I genshin_fps_prefs.py set <in.xml> <out.xml> <index>
      out.xml 必须是新文件 (已存在就拒绝, 不覆盖)。
  python3 -I genshin_fps_prefs.py same-except-fps <a.xml> <b.xml>
      核对两份文件只在帧率 index 上不同 (退出码 0 = 是)。
"""
import os
import re
import sys
import urllib.parse

ENTRY_FPS = 1
ENTRY_RENDER = 2


def _general(s):
    m = re.search(r'name="GENERAL_DATA">([^<]*)<', s)
    if not m:
        raise SystemExit("找不到 GENERAL_DATA")
    t = urllib.parse.unquote(m.group(1))
    if urllib.parse.quote(t, safe="") != m.group(1):
        raise SystemExit("GENERAL_DATA 编码方式不是 quote(safe=''), 不动")
    return m, t


def _index(t, et):
    hits = re.findall(r'\\"entryType\\":%d,\\"index\\":(\d+)' % et, t)
    if len(hits) != 1:
        return None
    return int(hits[0])


def get(path):
    s = open(path, encoding="utf-8").read()
    _, t = _general(s)
    return _index(t, ENTRY_FPS), _index(t, ENTRY_RENDER)


def set_index(src, dst, idx):
    if os.path.exists(dst):
        raise SystemExit(f"{dst} 已存在, 不覆盖")
    s = open(src, encoding="utf-8").read()
    m, t = _general(s)
    pat = r'(\\"entryType\\":%d,\\"index\\":)(\d+)' % ENTRY_FPS
    t2, n = re.subn(pat, lambda mm: mm.group(1) + str(idx), t)
    if n != 1:
        raise SystemExit(f"帧率项 (entryType {ENTRY_FPS}) 出现 {n} 次, 不改")
    out = s[: m.start(1)] + urllib.parse.quote(t2, safe="") + s[m.end(1):]
    with open(dst, "x", encoding="utf-8") as f:
        f.write(out)
    return get(dst)


def same_except_fps(a, b):
    sa = open(a, encoding="utf-8").read()
    sb = open(b, encoding="utf-8").read()
    ma, ta = _general(sa)
    mb, tb = _general(sb)
    if sa[: ma.start(1)] != sb[: mb.start(1)] or sa[ma.end(1):] != sb[mb.end(1):]:
        return False
    pat = r'(\\"entryType\\":%d,\\"index\\":)(\d+)' % ENTRY_FPS
    norm = lambda t: re.sub(pat, lambda mm: mm.group(1) + "X", t)
    return norm(ta) == norm(tb)


def main(argv):
    if len(argv) >= 2 and argv[0] == "get":
        f, r = get(argv[1])
        print(f"fps_index={'' if f is None else f} render_index={'' if r is None else r}")
        return 0 if f is not None else 1
    if len(argv) == 4 and argv[0] == "set":
        f, r = set_index(argv[1], argv[2], int(argv[3]))
        print(f"fps_index={f} render_index={r}")
        return 0 if f == int(argv[3]) else 1
    if len(argv) == 3 and argv[0] == "same-except-fps":
        ok = same_except_fps(argv[1], argv[2])
        print("same_except_fps=" + ("yes" if ok else "no"))
        return 0 if ok else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
