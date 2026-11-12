#!/usr/bin/env python3
"""O1 刷新率实验的解析与判定 (主机端, 只读本地文件)。

子命令:
  active                 从 stdin 读 `o1_refresh_dev.sh active` 的输出, 打印一行 JSON:
                         {"panel_hz", "render_hz", "sflat_hz", "sf_hz", "src"}
  modes <display.txt> <sf.txt>
                         从 dumpsys display / SurfaceFlinger 全文里抽支持的显示模式, 打印 JSON
  present <lat.txt>      一次 `o1_refresh_dev.sh lat` 的输出 → 上屏帧率与帧间隔 (核对游戏帧率上限)
  near <a> <b> [tol]     |a-b|/b <= tol (缺省 0.03) 退出码 0, 否则 1 (shell 里比 Hz 用)
  analyze --eval-dir D --watch W --cap 60 --ctrl-hz 120 --knob-hz 60   (--ctrl-hz 0 = 对照是 auto 档, 不核对)
          [--report R] [--iq-json J --iq-rc N] [--out result.json]
                         一组 (一个游戏帧率上限) 的结果: 官方判定 (phonefarm eval 回包)
                         + 逐臂「真实面板刷新率」核对 + 上屏节奏 (卡顿) 配对检验 + 画面抽查 → 结论
  selftest               用合成数据自检

上屏节奏的口径 (本脚本自己算, 不进 phonefarm 的判定表):
  数据 = SurfaceFlinger --latency <游戏图层> 的 actualPresentTime (逐帧真正上屏的时刻),
  不是 kgsl 的 GPU 提交节奏。T = 1000/游戏帧率上限。
  jank_pct      : 帧间隔 > 1.4T 的比例 (60 帧档: >23.3 ms; 120 Hz 下掉一帧 = 25 ms, 60 Hz 下 = 33.3 ms, 都算)
  irregular_pct : |帧间隔 - T| > 0.25T 的比例 (面板周期不是 T 的整数分之一时, 比如 144 Hz 放 60 帧, 会在这里冒出来)
  present_p95_ms / present_fps
  两臂按对相减, 精确符号翻转检验 (双侧)。
"""
import itertools
import json
import math
import os
import re
import statistics
import sys

HOLD_MIN = 0.9          # 一臂里实测面板刷新率等于该臂设定值的读数比例下限 (读数多时); 读数少时按个数放宽, 见 hold_min_for
HZ_TOL = 0.03           # 刷新率「相等」的相对容差


def hold_min_for(n):
    """panel_hold 的门槛按读数个数定 (R2 审查 #7): 每臂 30 s 只有约 6 个读数, 固定 0.9 等于「一个都不许错」;
    改成最多允许 max(1, ceil(10% n)) 个读数不等于设定值, 下限 0.5。n ≥ 10 时回到 0.9。"""
    if n <= 0:
        return 1.0
    allow = max(1, math.ceil(0.1 * n))
    return max(0.5, round(1.0 - allow / n, 3))
CAP_TOL = 0.05          # 上屏帧率离游戏上限的相对容差 (超出 = 游戏上限没钉住, 这一组不成立)
JANK_PP_MAX = 0.5       # 卡顿比例允许的平均增加 (百分点)
ALPHA = 0.05


# ══════════════ 读数解析 ══════════════

def _f(rx, s):
    m = re.search(rx, s)
    return float(m.group(1)) if m else None


def parse_active(text):
    """`active` 子命令的输出 → 各路读数。panel_hz 取 DisplayManager 记录的 SF 活动模式。"""
    dm = next((l for l in text.splitlines() if l.startswith("DM ") and "mActiveSfDisplayMode" in l), "")
    rr = next((l for l in text.splitlines() if l.startswith("RR ")), "")
    sflat = next((l for l in text.splitlines() if l.startswith("SFLAT")), "")
    sf = "\n".join(l for l in text.splitlines() if l.startswith("SF "))
    panel = None
    src = None
    for key in ("refreshRate", "vsyncRate", "peakRefreshRate", "fps"):
        v = _f(r"\b%s=([0-9]+(?:\.[0-9]+)?)" % key, dm)
        if v:
            panel, src = v, "display." + key
            break
    render = _f(r"mActiveRenderFrameRate[=:]\s*([0-9]+(?:\.[0-9]+)?)", rr)
    per = _f(r"SFLAT\s+([0-9]+)", sflat)
    sflat_hz = 1e9 / per if per else None
    sf_hz = _f(r"refresh-rate\s*:\s*([0-9]+(?:\.[0-9]+)?)", sf)
    if sf_hz is None:
        vp = _f(r"VSYNC period:\s*([0-9]+)\s*ns", sf)
        sf_hz = 1e9 / vp if vp else None
    if sf_hz is None:
        sf_hz = _f(r"activeMode.*?(?:vsyncRate|refreshRate|fps)[=:]\s*([0-9]+(?:\.[0-9]+)?)", sf)
    if panel is None and sf_hz is not None:
        panel, src = sf_hz, "surfaceflinger"
    r = lambda x: None if x is None else round(x, 2)
    return {"panel_hz": r(panel), "render_hz": r(render), "sflat_hz": r(sflat_hz), "sf_hz": r(sf_hz), "src": src}


def parse_modes(display_txt, sf_txt):
    """支持的显示模式。sf_ids = SurfaceFlinger 的模式 id (service call 1035 用这个)。"""
    out = {"dm_modes": [], "sf_modes": [], "sf_ids": []}
    seen = set()
    for m in re.finditer(r"DisplayModeRecord\{mMode=\{id=(\d+), width=(\d+), height=(\d+), fps=([0-9.]+)", display_txt):
        k = ("dm",) + m.groups()
        if k not in seen:
            seen.add(k)
            out["dm_modes"].append({"id": int(m[1]), "w": int(m[2]), "h": int(m[3]), "hz": round(float(m[4]), 2)})
    # SurfaceFlinger 自己的 dump (A14+ 形如 {id=0, hwcId=0, resolution=1216x2688, vsyncRate=120.00 Hz, ...})
    for m in re.finditer(r"\{id=(\d+), hwcId=(\d+), resolution=(\d+)x(\d+), (?:vsyncRate|refreshRate|fps)=([0-9.]+)", sf_txt):
        k = ("sf",) + m.groups()
        if k not in seen:
            seen.add(k)
            out["sf_modes"].append({"id": int(m[1]), "hwc_id": int(m[2]), "w": int(m[3]), "h": int(m[4]),
                                    "hz": round(float(m[5]), 2), "src": "sf_dump"})
    if not out["sf_modes"]:
        # 退路: dumpsys display 里 LocalDisplayAdapter 的 mSfDisplayModes (id 是 SF 的 id)
        blk = display_txt[display_txt.find("mSfDisplayModes"):] if "mSfDisplayModes" in display_txt else ""
        blk = blk[: blk.find("]") + 1] if "]" in blk else blk
        for m in re.finditer(r"DisplayMode\{id=(\d+),[^}]*?width=(\d+), height=(\d+),[^}]*?(?:refreshRate|vsyncRate)=([0-9.]+)", blk):
            k = ("sfd",) + m.groups()
            if k not in seen:
                seen.add(k)
                out["sf_modes"].append({"id": int(m[1]), "w": int(m[2]), "h": int(m[3]),
                                        "hz": round(float(m[4]), 2), "src": "display.mSfDisplayModes"})
    out["sf_ids"] = sorted({x["id"] for x in out["sf_modes"]})
    hz = sorted({x["hz"] for x in out["dm_modes"] + out["sf_modes"]})
    out["hz_supported"] = hz
    return out


def parse_latency_rows(lines):
    """--latency 输出 → (周期 ns, [(desired, actual, ready)])。跳过未上屏 (INT64_MAX / 0) 的行。"""
    period = None
    rows = []
    for l in lines:
        p = l.split()
        if not p:
            continue
        if len(p) == 1 and p[0].isdigit() and period is None:
            period = int(p[0])
            continue
        if len(p) == 3 and all(x.isdigit() for x in p):
            d, a, r = (int(x) for x in p)
            if a <= 0 or a >= (1 << 62):
                continue
            rows.append((d, a, r))
    return period, rows


def present_stats(times_ns, cap):
    """上屏时刻 → 节奏指标。times_ns 已排序去重。"""
    t = sorted(set(times_ns))
    if len(t) < 10:
        return None
    dts = [(b - a) / 1e6 for a, b in zip(t, t[1:])]
    # 大于 1 秒的空洞 = 轮询漏帧或场景切换, 不当一帧间隔
    dts = [x for x in dts if x < 1000]
    if not dts:
        return None
    T = 1000.0 / cap
    span = (t[-1] - t[0]) / 1e9
    srt = sorted(dts)
    p95 = srt[min(len(srt) - 1, int(math.ceil(0.95 * len(srt))) - 1)]
    return {
        "n_frames": len(t),
        "present_fps": round((len(t) - 1) / span, 3) if span > 0 else None,
        "present_p50_ms": round(statistics.median(dts), 3),
        "present_p95_ms": round(p95, 3),
        "present_max_ms": round(max(dts), 3),
        "jank_pct": round(100.0 * sum(1 for x in dts if x > 1.4 * T) / len(dts), 3),
        "irregular_pct": round(100.0 * sum(1 for x in dts if abs(x - T) > 0.25 * T) / len(dts), 3),
        "interval_sd_ms": round(statistics.pstdev(dts), 3),
    }


def parse_watch(text):
    """watch 日志 → 逐秒块。返回 dict(lat=[(poll_uptime, [actual_ns...])], heavy=[{...}], offsets=[s])。"""
    lat, heavy, offsets = [], [], []
    cur_t = None
    lines = text.splitlines()
    i = 0
    hv = None
    while i < len(lines):
        l = lines[i]
        if l.startswith("T "):
            cur_t = float(l.split()[1])
        elif l.startswith("LAT_BEGIN"):
            blk = []
            i += 1
            while i < len(lines) and not lines[i].startswith("LAT_END"):
                blk.append(lines[i])
                i += 1
            per, rows = parse_latency_rows(blk)
            lat.append((cur_t, per, [a for _, a, _ in rows]))
        elif l.startswith("MONO "):
            m = re.search(r"now at (\d+) nsecs", l)
            hv = {"mono_ns": int(m.group(1)) if m else None, "text": []}
        elif l.startswith("UPH ") and hv is not None:
            hv["uptime"] = float(l.split()[1])
            if hv["mono_ns"]:
                offsets.append(hv["uptime"] - hv["mono_ns"] / 1e9)
        elif hv is not None and l[:3] in ("DM ", "RR ", "SFL", "SF ", "UP ", "BR ", "BRD", "WAK", "SET"):
            hv["text"].append(l)
            if l.startswith("SET "):
                a = parse_active("\n".join(hv["text"]))
                a["uptime"] = hv.get("uptime")
                a["set"] = l[4:].strip()
                a["wake"] = next((x.split("=", 1)[1] for x in hv["text"] if x.startswith("WAKE ") and "=" in x), None)
                a["brightness"] = next((x[3:] for x in hv["text"] if x.startswith("BR ")), None)
                heavy.append(a)
                hv = None
        i += 1
    return {"lat": lat, "heavy": heavy, "offsets": offsets}


def trace_window(path):
    """phonefarm 的 trace.txt 头部 #loop_v1_meta capture_start_uptime / capture_end_uptime。"""
    a = b = None
    try:
        with open(path, "rb") as f:
            head = f.read(64 * 1024).decode("utf-8", "replace")
    except OSError:
        return None
    for l in head.splitlines():
        if not l.startswith("#"):
            break
        if l.startswith("#loop_v1_meta"):
            for tok in l.split():
                if tok.startswith("capture_start_uptime="):
                    a = float(tok.split("=", 1)[1])
                elif tok.startswith("capture_end_uptime="):
                    b = float(tok.split("=", 1)[1])
    return (a, b) if a is not None and b is not None and b > a else None


# ══════════════ 统计 ══════════════

def sign_flip_p(diffs):
    """配对差的精确符号翻转检验, 双侧。n ≤ 16。"""
    d = [x for x in diffs if x is not None]
    n = len(d)
    if n == 0:
        return None
    obs = abs(sum(d))
    hit = 0
    tot = 0
    for signs in itertools.product((1, -1), repeat=n):
        tot += 1
        if abs(sum(s * x for s, x in zip(signs, d))) >= obs - 1e-12:
            hit += 1
    return hit / tot


# ══════════════ 一组的判定 ══════════════

def arm_dirs(eval_dir):
    out = {}
    for name in sorted(os.listdir(eval_dir)) if os.path.isdir(eval_dir) else []:
        m = re.fullmatch(r"(knob|ctrl)(\d+)", name)
        if m and os.path.isdir(os.path.join(eval_dir, name)):
            out[(m[1], int(m[2]))] = os.path.join(eval_dir, name)
    return out


def arm_view(adir, watch, cap, want_hz):
    w = trace_window(os.path.join(adir, "trace.txt"))
    v = {"dir": adir, "window": w, "want_hz": want_hz}
    try:
        v["kgsl_summary"] = {k: json.load(open(os.path.join(adir, "summary.json"))).get(k)
                             for k in ("fps_mean", "frame_p50", "frame_p95", "frame_p99", "n_frames")}
    except (OSError, ValueError):
        v["kgsl_summary"] = None
    if not w:
        v["error"] = "trace.txt 里没有采集窗口"
        return v
    a, b = w
    # 面板刷新率: 窗口内的完整读数
    hs = [h for h in watch["heavy"] if h.get("uptime") is not None and a <= h["uptime"] <= b]
    pan = [h["panel_hz"] for h in hs if h.get("panel_hz")]
    v["panel_samples"] = pan
    v["render_samples"] = [h["render_hz"] for h in hs if h.get("render_hz")]
    v["panel_hold"] = (round(sum(1 for x in pan if abs(x - want_hz) <= HZ_TOL * want_hz) / len(pan), 3)
                       if pan and want_hz and want_hz > 0 else None)
    v["panel_n"] = len(pan)
    v["hold_min"] = hold_min_for(len(pan))
    v["wake"] = sorted({h.get("wake") for h in hs if h.get("wake")})
    v["brightness"] = sorted({h.get("brightness") for h in hs if h.get("brightness")})
    # SF --latency 第一行的周期, 每秒一个
    sfl = [1e9 / p for t, p, _ in watch["lat"] if t is not None and a <= t <= b and p]
    v["sflat_hz_median"] = round(statistics.median(sfl), 2) if sfl else None
    # 上屏时刻: 有单调钟/开机钟偏移就按帧自己的时刻截窗, 否则按首次被轮询到的时刻
    off = statistics.median(watch["offsets"]) if watch["offsets"] else None
    seen = {}
    for t, _, acts in watch["lat"]:
        for x in acts:
            seen.setdefault(x, t)
    if off is not None:
        times = [x for x in seen if a <= x / 1e9 + off <= b]
        v["clock"] = "mono+offset"
    else:
        times = [x for x, t in seen.items() if t is not None and a <= t <= b + 1.0]
        v["clock"] = "first_seen_poll"
    v["present"] = present_stats(times, cap)
    return v


def analyze(eval_dir, watch_path, cap, ctrl_hz, knob_hz, report_path=None, iq_json=None, iq_rc=None):
    watch = parse_watch(open(watch_path, encoding="utf-8", errors="replace").read()) if watch_path else {
        "lat": [], "heavy": [], "offsets": []}
    dirs = arm_dirs(eval_dir)
    rounds = sorted({i for _, i in dirs})
    arms = {}
    for (kind, i), d in dirs.items():
        arms[f"{kind}{i}"] = arm_view(d, watch, cap, knob_hz if kind == "knob" else ctrl_hz)
    res = {"cap": cap, "ctrl_hz": ctrl_hz, "knob_hz": knob_hz, "arms": arms, "problems": []}

    # ── 官方判定 (phonefarm eval) ──
    rep = None
    if report_path and os.path.exists(report_path):
        try:
            rep = json.load(open(report_path))
        except ValueError:
            res["problems"].append("eval 回包不是 JSON")
    if rep:
        v = rep.get("verdict", {}) or {}
        res["official"] = {
            "status": rep.get("status"),
            "reason": v.get("reason"),
            "wins": v.get("wins"),
            "regressions": v.get("regressions"),
            "alpha_win": v.get("alpha_win"),
            "reachable": v.get("reachable"),
            "per_metric": v.get("per_metric"),
            "power_usable": (rep.get("power_source") or {}).get("excluded_reason") is None
            and "power_w_mean" in (v.get("per_metric") or {}),
            "power_source": rep.get("power_source"),
            "restored": (rep.get("conditions_observed") or {}).get("restored"),
        }
    else:
        res["official"] = None
        res["problems"].append("没有 eval 回包 (report.json)")

    # ── 逐对: 面板刷新率真生效 + 游戏上限钉住 + 节奏 ──
    pairs = []
    for i in rounds:
        k, c = arms.get(f"knob{i}"), arms.get(f"ctrl{i}")
        if not k or not c:
            continue
        ok = True
        why = []
        for name, a in (("knob", k), ("ctrl", c)):
            if not a["want_hz"] or a["want_hz"] <= 0:
                pass  # 对照是 auto 档: 不预设面板 Hz, 只记录读到的分布 (panel_samples)
            elif a.get("panel_hold") is None:
                ok = False
                why.append(f"{name}{i} 窗口内没有面板刷新率读数")
            elif a["panel_hold"] < a["hold_min"]:
                ok = False
                why.append(f"{name}{i} 实测面板刷新率只有 {a['panel_hold']:.0%} 的读数等于设定 {a['want_hz']} Hz "
                           f"(门槛 {a['hold_min']:.0%}, 共 {a['panel_n']} 个读数, 读到 {sorted(set(a['panel_samples']))})")
            p = a.get("present")
            if not p or not p.get("present_fps"):
                ok = False
                why.append(f"{name}{i} 没有上屏帧数据")
            elif abs(p["present_fps"] - cap) > CAP_TOL * cap:
                ok = False
                why.append(f"{name}{i} 上屏帧率 {p['present_fps']} 不在游戏上限 {cap}±{CAP_TOL:.0%} 内")
            if a.get("wake") and a["wake"] != ["Awake"]:
                ok = False
                why.append(f"{name}{i} 窗口内亮屏状态 {a['wake']}")
        pairs.append({"round": i, "valid": ok, "why": why})
    res["pairs"] = pairs
    good = [p["round"] for p in pairs if p["valid"]]
    res["n_pairs_valid"] = len(good)
    if len(good) < len(pairs):
        res["problems"].append(f"{len(pairs) - len(good)} 对没通过「面板刷新率真生效 / 游戏上限钉住」核对")

    pacing = {}
    for met in ("jank_pct", "irregular_pct", "present_p95_ms", "interval_sd_ms"):
        diffs = [arms[f"knob{i}"]["present"][met] - arms[f"ctrl{i}"]["present"][met] for i in good]
        pacing[met] = {
            "ctrl_mean": round(statistics.mean(arms[f"ctrl{i}"]["present"][met] for i in good), 3) if good else None,
            "knob_mean": round(statistics.mean(arms[f"knob{i}"]["present"][met] for i in good), 3) if good else None,
            "diff_mean": round(statistics.mean(diffs), 3) if diffs else None,
            "p": sign_flip_p(diffs) if diffs else None,
        }
    res["pacing"] = pacing
    jk = pacing["jank_pct"]
    p95 = pacing["present_p95_ms"]
    pacing_worse = bool(good) and (
        (jk["diff_mean"] is not None and jk["diff_mean"] > 0 and jk["p"] is not None and jk["p"] < ALPHA)
        or (jk["diff_mean"] is not None and jk["diff_mean"] > JANK_PP_MAX)
        or (p95["diff_mean"] is not None and p95["diff_mean"] > 0 and p95["p"] is not None and p95["p"] < ALPHA)
    )
    res["pacing_ok"] = (not pacing_worse) if good else None

    # ── 画面抽查 ──
    iq = None
    if iq_json and os.path.exists(iq_json):
        try:
            iq = json.load(open(iq_json))
        except ValueError:
            iq = None
    iq_state = {0: "通过", 1: "不通过", 3: "判不了"}.get(iq_rc, "没做")
    res["image"] = {"iq_judge": iq_state, "rc": iq_rc, "detail": iq,
                    "note": "screencap 取的是合成后、送面板之前的帧, 刷新率不同本应完全一样; "
                            "这项只能抓到「游戏因刷新率改变而改了画质设置」一类问题, 看不到面板本身的显示差异 (拖影、闪烁)"}

    # ── 结论 ──
    off = res["official"] or {}
    status = off.get("status")
    wins = off.get("wins") or []
    regs = off.get("regressions") or []
    min_pairs = 5
    if status not in ("PASS", "REJECT"):
        concl = "无效: 测量没跑完 (" + str(off.get("reason")) + ")"
    elif off.get("restored") is False:
        concl = "无效: 设置没还原干净, 数据不可信"
    elif len(good) < min_pairs:
        concl = f"无效: 只有 {len(good)} 对通过刷新率/帧率核对 (至少要 {min_pairs} 对)"
    elif "power_w_mean" not in (off.get("per_metric") or {}):
        concl = "没量成: 功耗没进判定表 (供电口径不一致或不在放电态), 不等于没收益"
    elif regs or pacing_worse:
        concl = "变差: " + ", ".join(regs + (["上屏节奏 (卡顿)"] if pacing_worse else []))
    elif "power_w_mean" in wins and iq_rc == 0:
        concl = "增益: 功耗显著下降, 帧时与上屏节奏无显著变差, 画面抽查通过"
    elif "power_w_mean" in wins:
        concl = f"性能达标但画面未确认 (iq-judge {iq_state}): 不能算确认的增益"
    else:
        concl = "没有显著收益"
    res["conclusion"] = concl
    return res


# ══════════════ 自检 ══════════════

def _selftest():
    t = """DM mActiveSfDisplayMode=DisplayMode{id=2, width=1216, height=2688, xDpi=460.0, yDpi=460.0, refreshRate=120.00001, vsyncRate=120.00001, appVsyncOffsetNanos=0, presentationDeadlineNanos=0, peakRefreshRate=120.00001}
RR mActiveRenderFrameRate=60.0
SFLAT 8333333
SF refresh-rate              : 120.00 Hz
UP 1234.5"""
    a = parse_active(t)
    assert a["panel_hz"] == 120.0 and a["render_hz"] == 60.0 and abs(a["sflat_hz"] - 120.0) < 0.01, a
    a2 = parse_active("SF   VSYNC period:  16666666 ns\nSFLAT 16666666")
    assert a2["panel_hz"] == 60.0, a2
    disp = ("mSfDisplayModes=[DisplayMode{id=0, width=1216, height=2688, xDpi=1, yDpi=1, refreshRate=120.00001, vsyncRate=120.00001}, "
            "DisplayMode{id=1, width=1216, height=2688, xDpi=1, yDpi=1, refreshRate=60.000004, vsyncRate=60.000004}]\n"
            "DisplayModeRecord{mMode={id=1, width=1216, height=2688, fps=120.00001, vsync=120.00001")
    m = parse_modes(disp, "")
    assert m["sf_ids"] == [0, 1] and 60.0 in m["hz_supported"], m
    m2 = parse_modes("", "  {id=3, hwcId=3, resolution=1216x2688, vsyncRate=144.00 Hz, dpi=460.00x460.00, group=0}")
    assert m2["sf_ids"] == [3] and m2["sf_modes"][0]["hz"] == 144.0, m2
    # 60 帧上限, 每帧 16.67 ms, 其中 2 帧掉到 33.3 ms
    base = 10**12
    ts = [base + int(i * 16_666_667) for i in range(200)]
    st = present_stats(ts, 60)
    assert st["jank_pct"] == 0 and abs(st["present_fps"] - 60) < 0.1, st
    ts2 = [x for j, x in enumerate(ts) if j not in (50, 120)]
    st2 = present_stats(ts2, 60)
    assert st2["jank_pct"] > 0.9, st2
    assert sign_flip_p([1, 1, 1, 1, 1]) == 2 / 32
    assert sign_flip_p([1, -1]) == 1.0
    # 门槛按读数个数: 6 个读数允许错 1 个 (0.833), 20 个允许错 2 个 (0.9), 2 个允许错 1 个 (0.5), 0 个 = 1.0
    assert hold_min_for(6) == 0.833 and hold_min_for(20) == 0.9 and hold_min_for(2) == 0.5 and hold_min_for(0) == 1.0
    assert hold_min_for(10) == 0.9 and hold_min_for(1) == 0.5 and hold_min_for(100) == 0.9
    # 端到端: 合成一组 eval 证据 + watch 日志
    import tempfile
    d = tempfile.mkdtemp(prefix="o1_selftest_")
    ev = os.path.join(d, "eval")
    lines = ["# o1_refresh_watch v1"]
    up0 = 1000.0
    off = 50.0  # uptime = mono + 50
    rounds = 5
    rep = {"status": "PASS", "verdict": {"wins": ["power_w_mean"], "regressions": [], "reason": "x",
                                         "per_metric": {"power_w_mean": {"p": 0.008}}},
           "power_source": {"excluded_reason": None}, "conditions_observed": {"restored": True}}
    t = up0
    for i in range(1, rounds + 1):
        for kind, hz in (("ctrl", 120.0), ("knob", 60.0)):
            ad = os.path.join(ev, f"{kind}{i}")
            os.makedirs(ad)
            a, b = t + 5, t + 35
            open(os.path.join(ad, "trace.txt"), "w").write(
                f"#loop_v1_meta capture_start_uptime={a} capture_end_uptime={b} duration_req=30\n")
            json.dump({"fps_mean": 59.8, "frame_p95": 17.0}, open(os.path.join(ad, "summary.json"), "w"))
            for s in range(40):
                now = t + s
                lines.append(f"T {now}")
                lines.append("LAT_BEGIN x")
                lines.append(str(int(1e9 / hz)))
                for f in range(60):
                    act = int((now - off - 1 + f / 60.0) * 1e9)
                    lines.append(f"{act - 5} {act} {act - 2}")
                lines.append("LAT_END")
                if s % 5 == 0:
                    lines.append(f"MONO now at {int((now - off) * 1e9)} nsecs")
                    lines.append(f"UPH {now}")
                    lines.append(f"DM mActiveSfDisplayMode=DisplayMode{{id=1, refreshRate={hz}, vsyncRate={hz}}}")
                    lines.append("SFLAT 8333333")
                    lines.append("BR mode=0 value=128 float=null")
                    lines.append("WAKE mWakefulness=Awake")
                    lines.append("SET refresh_rate_mode=3 peak=null min=null")
            t += 60
    wp = os.path.join(d, "watch.txt")
    open(wp, "w").write("\n".join(lines) + "\n")
    rp = os.path.join(d, "report.json")
    json.dump(rep, open(rp, "w"))
    r = analyze(ev, wp, 60, 120.0, 60.0, rp, None, 0)
    assert r["n_pairs_valid"] == 5, (r["pairs"], r["problems"])
    assert r["conclusion"].startswith("增益"), r["conclusion"]
    assert r["arms"]["knob1"]["clock"] == "mono+offset"
    # 一臂面板没切过去 → 那一对无效
    r2 = analyze(ev, wp, 60, 120.0, 90.0, rp, None, 0)
    assert r2["n_pairs_valid"] == 0 and r2["conclusion"].startswith("无效"), r2["conclusion"]
    r3 = analyze(ev, wp, 60, 0.0, 60.0, rp, None, 0)
    assert r3["n_pairs_valid"] == 5, r3["pairs"]
    # 每臂窗口 [t+5, t+35] 里 7 个读数 (每 5 s 一个): 错 1 个 (hold 0.857) 固定 0.9 会判无效, 按个数的门槛 (0.857) 放过;
    # 错 2 个 (0.714) 仍判无效。knob1 的窗口是 1065..1095, DM 行紧跟在 UPH 行后面
    k1 = r["arms"]["knob1"]
    assert k1["panel_n"] == 7 and k1["hold_min"] == 0.857, (k1["panel_n"], k1["hold_min"])
    lines_ = open(wp, encoding="utf-8").read().splitlines()
    for uph in ("UPH 1065.0", "UPH 1070.0"):
        j = next(j for j, l in enumerate(lines_) if l.startswith(uph)) + 1
        assert lines_[j].startswith("DM "), lines_[j]
        lines_[j] = "DM mActiveSfDisplayMode=DisplayMode{id=1, refreshRate=120.0, vsyncRate=120.0}"
        bad = wp + "." + uph.split()[1]
        open(bad, "w", encoding="utf-8").write("\n".join(lines_) + "\n")
        rb = analyze(ev, bad, 60, 120.0, 60.0, rp, None, 0)
        hold, nv = rb["arms"]["knob1"]["panel_hold"], rb["n_pairs_valid"]
        if uph == "UPH 1065.0":
            assert hold == 0.857 and nv == 5, (hold, rb["pairs"])
        else:
            assert hold == 0.714 and nv == 4, (hold, rb["pairs"])
    print("selftest ok")
    return 0


def main(argv):
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    c = argv[0]
    if c == "active":
        print(json.dumps(parse_active(sys.stdin.read()), ensure_ascii=False))
        return 0
    if c == "modes" and len(argv) == 3:
        rd = lambda p: open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else ""
        print(json.dumps(parse_modes(rd(argv[1]), rd(argv[2])), ensure_ascii=False))
        return 0
    if c == "present" and len(argv) >= 2:
        cap = float(argv[2]) if len(argv) > 2 else 60.0
        _, rows = parse_latency_rows(open(argv[1], encoding="utf-8", errors="replace").read().splitlines())
        st = present_stats([a for _, a, _ in rows], cap)
        print(json.dumps(st or {"error": "帧太少"}, ensure_ascii=False))
        return 0 if st else 1
    if c == "near" and len(argv) >= 3:
        try:
            a, b = float(argv[1]), float(argv[2])
        except ValueError:
            return 1
        tol = float(argv[3]) if len(argv) > 3 else HZ_TOL
        return 0 if b and abs(a - b) <= tol * b else 1
    if c == "analyze":
        kw = {}
        it = iter(argv[1:])
        for k in it:
            kw[k.lstrip("-").replace("-", "_")] = next(it, None)
        r = analyze(kw["eval_dir"], kw.get("watch"), float(kw["cap"]), float(kw["ctrl_hz"]), float(kw["knob_hz"]),
                    kw.get("report"), kw.get("iq_json"), int(kw["iq_rc"]) if kw.get("iq_rc") not in (None, "") else None)
        txt = json.dumps(r, ensure_ascii=False, indent=1)
        if kw.get("out"):
            if os.path.exists(kw["out"]):
                print(f"{kw['out']} 已存在, 不覆盖", file=sys.stderr)
                return 2
            with open(kw["out"], "x", encoding="utf-8") as f:
                f.write(txt)
        print(r["conclusion"])
        return 0
    if c == "selftest":
        return _selftest()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
