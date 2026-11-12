#!/usr/bin/env python3
"""selfsr_ab.py — 自研超分/插帧的两臂 ABBA (冷启动、进大世界、同一套测量)。不改游戏内任何设置。

    python3 -I selfsr/selfsr_ab.py run --serial <S> --out <目录> --a native --b layer [--rounds 6]

臂的种类 (--a / --b):
  native              原生: 不挂层, Game Mode 无干预
  layer               只挂透传层 (gray-layer session --mode probe), 第 2 步用
  hwc:<F>             Android Game Mode 降分辨率 F (cmd game mode custom + cmd game set --downscale F), 放大交给 HWC
  ip:<合成方式>[:<帧率>[:键=值,...]]  挂层并开层内插帧 (interp_mode=dup/dupcur/bm/real, interp_fps=给窗口投的帧率,
                      可选 pacing/stamp/acquire_ms/extra_images/lead_ns → debug.knobs.interp_<键>)
  srip:<F>:<宽>x<高>:<锐化>:<合成方式>[:<帧率>]  超分 + 插帧叠加 (第 5 步)
  <挂层的臂>+gfrc:<三位>  自研层与 G-FRC+ 同开 (例 sr:1:2688x1216:0.8+gfrc:001)
  gfrc:<三位>          厂商 G-FRC+ (game_gfrc_mode 本包位, 例 101 = 只超分, 111 = 超分+插帧), 对标用
  sr:<F>:<宽>x<高>[:<锐化>]  同上降分辨率 F, 再挂层并设 debug.knobs.selfsr=<宽>x<高>[:<锐化>]: 层在送显前把游戏的小图超分到 <宽>x<高>
每臂: (devlock 下) 设好 Game Mode 与 selfsr 属性 → 冷启动进大世界 → 静置 --settle 秒 → --window 秒窗口并行采
  - SurfaceFlinger timestats: 上屏帧率 / 刷新率 / 丢帧
  - ftrace + phonefarm parse-trace: 游戏 GPU 提交 → 游戏渲染帧率 / p95 帧时
  - 截图: 锐度 (拉普拉斯方差), 与本轮第一张 native 截图的 SSIM (跨冷启动场景会有云/时辰差异, 只作参考)
  - --pan: 窗口后录 5 秒屏、期间拖动视角, 量 HUD 区相邻帧平均差 (重影越重越大) 与画面中央的帧间差 (证明在转)
  - --power: 窗口内停充, 每秒读电池电流×电压 = 整机功耗 (mW) 与每个看到的帧的能耗 (mJ); 整轮亮度固定为手动、不改值
  - 游戏图层送显缓冲尺寸 (HWC 源); 挂层臂另读层 marker (层加载、窗口内帧数、selfsr 实际尺寸与计数)
  整轮 (准备、各臂、还原) 共持一个设备租约; gray-layer session 继承同一锁 fd, 挂层并冷启动游戏,
  onboard --enter-only --no-cold 只导航不重启 (重启会丢层), 采完 stop 会话 (会话负责摘层)。
  会话自带的 sample 在本机 ftrace 子进程起不来 (worker exit 76), 所以测量不用它。
收尾 (含 Ctrl-C): Game Mode 恢复原模式并 reset 干预、清 debug.knobs.selfsr、screen_off_timeout 还原, 逐项回读核对。
统计: 每对 (B - A) 配对差, 精确符号翻转检验 + bootstrap 95% 区间。
"""
import argparse, hashlib, json, os, re, shlex, signal, statistics, subprocess, sys, threading, time, uuid
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
# phonefarm 树：作为平台的实验任务运行时由节点给 PF_ROOT；在 phonefarm/loop_v1 老位置直接跑时回退到上两级。
REPO = os.environ.get("PF_ROOT") or os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "gfrc"))
from mali_capture import collect as collect_mali  # noqa: E402
from perfetto_capture import Capture as PerfettoCapture, marker_script  # noqa: E402
from evidence_transfer import download_root_file  # noqa: E402
# 节点任务里 DEVLOCK_LABEL=job-<任务ID>：设备锁标签挂在它下面，看板就能把手机占用对到任务和研究。
LOCK_PREFIX = os.environ["DEVLOCK_LABEL"] + "-" if os.environ.get("DEVLOCK_LABEL") else ""
from gfrc_ab import Dev, parse_timestats, layer_src, sharpness, signflip, boot_ci, set_pkg_bits, lock_fds  # noqa: E402

PKG_DEFAULT = "com.miHoYo.Yuanshen"
RECOVERY_UNKNOWN = 75  # Stop the arm sequence when capture cleanup is unconfirmed.
DEV_TRACE = "/data/local/tmp/selfsr_ftrace_capture.sh"
DEV_POWER = "/data/local/tmp/selfsr_power_sample.sh"
CHG_NODE = "/sys/class/qcom-battery/charging_enabled"   # 高通停充开关; 停充后整机电流全部来自电池
POWER_PRE = 30   # 停充后等电流稳定的秒数: power_sample.sh 在此窗口内每 5s 重写停充、连续 2 次负电流才开始采样
                 # (2026-10-30 高温 A 臂停充失效后加固; 探针 47e21052ace6: 空闲态 6-8s 转放电)
SR_PROP = "debug.knobs.selfsr"
SR_OPTS = ("algo", "direct", "unlimit")   # → debug.knobs.selfsr_<键>; unlimit → debug.knobs.unlimit
UNLIMIT_PROP = "debug.knobs.unlimit"
# 截图 (2688x1216) 右上角那排固定图标: 整屏锐度被场景主导 (冷启动后角色/镜头可能不同), 这块内容各臂相同
HUD_BOX = (1760, 10, 2600, 130)
IP_PROPS = ("debug.knobs.interp", "debug.knobs.interp_mode", "debug.knobs.interp_fps")
IP_TUNE = ("static", "sfovr", "sfwait", "sc_grid", "sc_real_wait", "sc_noreal", "sc_presig", "sc_name", "sc_kick_ns", "sc_gap_ns", "sc_winvote", "sc_xform", "sc_vote", "sc_pacer", "sc_margin_ns", "sc_ring", "pacing", "stamp", "acquire_ms", "extra_images", "lead_ns", "mult", "backend", "sc_dst", "panel", "framepacing",
           "unlimit")
FP_PROP = "vendor.perf.framepacing.enable"   # 高通 perf HAL 的游戏帧节奏; framepacing=<值> 进世界后设, 收尾写回原值   # panel 不是层属性, 见 prepare   # ip 臂第 4 段可设的 debug.knobs.interp_<键>


def parse_kind(s):
    if "+gfrc:" in s:   # <挂层的臂>+gfrc:<三位>  自研层 + 厂商 G-FRC+ 同时开 (G-FRC+ 开着时系统放开限帧)
        base, bits = s.rsplit("+gfrc:", 1)
        k = parse_kind(base)
        if not uses_layer(k) or len(bits) != 3 or not bits.isdigit():
            raise argparse.ArgumentTypeError(f"+gfrc 只接在挂层的臂后面, 三位数字: {s}")
        return {**k, "gfrc": bits}
    p = s.split(":")
    if p[0] in ("native", "layer") and len(p) == 1:
        return {"kind": p[0], "downscale": None, "selfsr": None}
    if p[0] == "layer" and len(p) == 2 and p[1] == "unlimit=1":   # 透传层 + 解开红魔 libgui 进程内限帧
        return {"kind": "layer", "downscale": None, "selfsr": None, "unlimit": True}
    if p[0] == "gfrc" and len(p) == 2 and len(p[1]) == 3:   # gfrc:<画质档><帧率档><总开关>  厂商 G-FRC+ (对标用)
        return {"kind": "gfrc", "downscale": None, "selfsr": None, "gfrc": p[1]}
    if p[0] == "ip" and len(p) in (2, 3, 4):   # ip:<合成方式>[:<投帧率>[:键=值,...]]  层内插帧 (interp.cpp)
        tune = dict(kv.split("=", 1) for kv in p[3].split(",")) if len(p) == 4 and p[3] else {}
        if any(k not in IP_TUNE for k in tune) or ("panel" in tune and tune["panel"] not in PANEL_MODE):
            raise argparse.ArgumentTypeError(f"ip 臂只认这些键: {IP_TUNE}")
        return {"kind": "ip", "downscale": None, "selfsr": None, "interp": p[1], "fps": p[2] if len(p) >= 3 else "",
                "tune": tune}
    if p[0] == "hwc" and len(p) == 2:
        return {"kind": "hwc", "downscale": float(p[1]), "selfsr": None}
    if p[0] == "srip" and len(p) in (5, 6, 7) and "x" in p[2]:   # srip:<F>:<宽>x<高>:<锐化>:<合成方式>[:<投帧率>[:键=值,...]]
        tune = dict(kv.split("=", 1) for kv in p[6].split(",")) if len(p) == 7 and p[6] else {}
        if any(k not in IP_TUNE for k in tune):
            raise argparse.ArgumentTypeError(f"srip 臂的插帧参数只认: {IP_TUNE}")
        return {"kind": "srip", "downscale": float(p[1]), "selfsr": f"{p[2]}:{p[3]}", "interp": p[4],
                "fps": p[5] if len(p) >= 6 else "", "tune": tune}
    if p[0] == "sr" and len(p) in (3, 4, 5) and "x" in p[2]:   # sr:<F>:<宽>x<高>[:<锐化>[:algo=cr,direct=1]]
        opts = dict(kv.split("=", 1) for kv in p[4].split(",")) if len(p) == 5 and p[4] else {}
        if any(k not in SR_OPTS for k in opts):
            raise argparse.ArgumentTypeError(f"sr 臂只认这些键: {SR_OPTS}")
        return {"kind": "sr", "downscale": float(p[1]), "selfsr": ":".join(p[2:4]), "sr_opts": opts}
    raise argparse.ArgumentTypeError(f"不认识的臂: {s}")


def uses_layer(k):
    return k["kind"] in ("layer", "sr", "ip", "srip")


# 挂层会话按独立会话跑: 平台任务里 phonefarm 把带 PF_JOB_ID/PF_JOB_OUT 的会话当节点会话, 要求 --out 就是任务输出目录,
# 而这里每臂一个会话目录。整轮的锁 fd 继续传给每臂, 还原由每臂和收尾的核对负责 (restore.json)。
SESSION_ENV = {k: v for k, v in os.environ.items() if k not in ("PF_JOB_ID", "PF_JOB_OUT", "PF_JOB_LEASE_DIR")}


def pf_json(pf, *args, inp=None, timeout=600):
    r = subprocess.run([pf, *args], input=inp, capture_output=True, text=True, timeout=timeout, env=SESSION_ENV, pass_fds=lock_fds())
    try:
        return r.returncode, json.loads(r.stdout)
    except ValueError:
        return r.returncode, {"raw": r.stdout[-2000:], "err": r.stderr[-2000:]}


def session_action(pf, out, action, params=None):
    _, d = pf_json(pf, "gray-layer", "session-status", "--out", out, "--json")
    c = d.get("controller") or {}
    rid = f"selfsr-{action}-{uuid.uuid4().hex[:8]}"
    req = {"schema": "phonefarm/gray-control-action@1", "request_id": rid, "session_id": d.get("session_id"),
           "actor_id": c.get("actor_id"), "control_epoch": c.get("epoch"), "action": action, "params": params or {}}
    code, rec = pf_json(pf, "gray-layer", "session-action", "--out", out, "--request", "-", "--json",
                        inp=json.dumps(req))
    for _ in range(300):
        if code != 0 or rec.get("status") in ("completed", "failed", "rejected", "unknown"):
            break
        time.sleep(1)
        code, rec = pf_json(pf, "gray-layer", "session-status", "--out", out, "--request-id", rid, "--json")
    return rec


def ssim(png_a, png_b):
    """灰度 SSIM (8x8 不重叠块的均值), 两图尺寸不同时把 b 缩放到 a。"""
    from PIL import Image
    import numpy as np
    a = Image.open(png_a).convert("L")
    b = Image.open(png_b).convert("L")
    if b.size != a.size:
        b = b.resize(a.size, Image.BICUBIC)
    x = np.asarray(a, dtype=np.float64)
    y = np.asarray(b, dtype=np.float64)
    h, w = (x.shape[0] // 8) * 8, (x.shape[1] // 8) * 8
    x = x[:h, :w].reshape(h // 8, 8, w // 8, 8)
    y = y[:h, :w].reshape(h // 8, 8, w // 8, 8)
    mx, my = x.mean(axis=(1, 3)), y.mean(axis=(1, 3))
    vx, vy = x.var(axis=(1, 3)), y.var(axis=(1, 3))
    cxy = ((x - mx[:, None, :, None]) * (y - my[:, None, :, None])).mean(axis=(1, 3))
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    s = ((2 * mx * my + c1) * (2 * cxy + c2)) / ((mx ** 2 + my ** 2 + c1) * (vx + vy + c2))
    return float(s.mean())


def game_mode(dev, pkg, downscale, orig_mode):
    """设 Game Mode 干预; downscale=None 表示无干预。返回核对信息。"""
    if downscale is None or downscale >= 1:   # >=1: 不降分辨率 (满分辨率超分只换放大)
        dev.shell(f"cmd game reset {pkg}")
        if orig_mode and orig_mode not in ("unknown", ""):
            dev.shell(f"cmd game mode {orig_mode} {pkg}")
        cfg = dev.shell(f"cmd game list-configs {pkg}")
        return {"downscale": downscale, "ok": "downscale" not in cfg.lower() or "no intervention" in cfg.lower(), "configs": cfg[-300:]}
    o1 = dev.shell(f"cmd game mode custom {pkg}")
    o2 = dev.shell(f"cmd game set --downscale {downscale} {pkg}")
    cfg = dev.shell(f"cmd game list-configs {pkg}")
    return {"downscale": downscale, "ok": f"{downscale}" in cfg, "configs": cfg[-300:], "out": (o1 + o2)[-200:]}


def current_mode(dev, pkg):
    t = dev.shell(f"cmd game list-modes {pkg}")
    return t.split("current mode:")[1].split(",")[0].strip() if "current mode:" in t else "unknown"


def timestats_layer(text, needle):
    """timestats 里名字含 needle 的图层的 totalFrames (取帧数最多的那个) 与全局 displayOnTime。"""
    g = {"displayOnTime": None, "layer_frames": None}
    m = re.search(r"^displayOnTime = (\d+)", text, re.M)
    g["displayOnTime"] = int(m.group(1)) if m else None
    for blk in text.split("layerName = ")[1:]:
        if needle in blk.splitlines()[0]:
            f = re.search(r"^totalFrames = (\d+)", blk, re.M)
            if f and int(f.group(1)) > (g["layer_frames"] or 0):
                g["layer_frames"] = int(f.group(1))
    return g


PAN_HUD = (0.80, 0.70, 0.995, 0.97)   # 右下技能按钮区 (横屏比例 x0,y0,x1,y1): 不透明、固定, 转视角时背景在它周围动
PAN_BG = (0.30, 0.25, 0.70, 0.75)     # 画面中央: 转视角时全是运动


def pan_resolution(dev, receipt):
    """Observe positive screen dimensions through the existing checked Dev transport.

    Args:
        dev: Existing leased device; no new device or transport owner is created.
        receipt: Attempt-owned JSON path retaining raw probe output and failures.

    Returns:
        Observed landscape width and height, honoring the last physical/override size.

    Raises:
        RuntimeError: Three bounded probes produced no valid resolution. Recording
            and input must not start when the dimensions remain unknown.
    """
    path, observations = Path(receipt), []
    path.parent.mkdir(parents=True, exist_ok=True)

    def save():
        path.write_text(json.dumps({"command": "wm size", "attempts": observations}, indent=2))

    for attempt in range(3):
        observation = {"attempt": attempt + 1, "at": time.time()}
        try:
            output = dev.adb("shell wm size", timeout=10, check=True)
            observation["stdout"] = output
            sizes = re.findall(r"(?m)^\s*(?:(?:Physical|Override) size:\s*)?(\d+)x(\d+)\s*$", output)
            if sizes:
                width, height = map(int, sizes[-1])
                if width > 0 and height > 0:
                    observation["observed_size"] = [width, height]
                    observations.append(observation)
                    save()
                    return max(width, height), min(width, height)
            observation["error"] = "wm size returned no positive screen dimensions"
        except (OSError, RuntimeError, subprocess.SubprocessError, TimeoutError) as error:
            observation["error"] = str(error)
            observation["error_type"] = type(error).__name__
        observations.append(observation)
        save()
        if attempt < 2:
            try:
                dev.adb("wait-for-device", timeout=10, check=True)
            except (OSError, RuntimeError, subprocess.SubprocessError, TimeoutError) as error:
                observation["wait_error"] = str(error)
                save()
            time.sleep(1)
    raise RuntimeError(f"Pan resolution unavailable after three checked wm size probes; see {path}")


def pan_probe(dev, a, name, out):
    """录 PAN_SECONDS 秒屏, 期间在右半屏横向来回拖视角; 返回 HUD 区与中央区的相邻帧平均差 (0..255) 和录到的帧率。"""
    import numpy as np
    W, H = pan_resolution(dev, os.path.join(out, "traces", f"{name}_wm-size.json"))
    # Some Android builds deny shell-owned recordings even though root tracing
    # works. Keep both creation and retrieval under the existing root policy.
    remote = f"/data/local/tmp/llm4ad_selfsr_pan_{uuid.uuid4().hex}.mp4"
    local = os.path.join(out, "traces", f"{name}_pan.mp4")
    Path(local + ".transfer.json").write_text(json.dumps({"remote": remote, "status": "recording"}))
    log_path = os.path.join(out, "traces", f"{name}_pan.stderr")
    log = open(log_path, "w")
    rec = subprocess.Popen(["adb", "-s", a.serial, "shell", "su", "-c",
                            shlex.quote(f"screenrecord --time-limit 5 --bit-rate 40000000 {remote}")],
                           stdout=subprocess.DEVNULL, stderr=log, pass_fds=lock_fds())
    time.sleep(0.8)
    y = int(H * 0.45)
    for i in range(4):   # 原神: 右半屏空白处拖动 = 转视角
        x0, x1 = (int(W * 0.55), int(W * 0.85)) if i % 2 == 0 else (int(W * 0.85), int(W * 0.55))
        dev.shell(f"input swipe {x0} {y} {x1} {y} 700")
    downloaded = False
    try:
        rec.wait(timeout=30)
        if rec.returncode:
            return {"pan_error": f"screenrecord exited {rec.returncode}; see {log_path}"}
        download_root_file(dev, remote, local)
        downloaded = True
    finally:
        if rec.poll() is None:
            rec.terminate()
            rec.wait(timeout=10)
        log.close()
        if downloaded:
            dev.su(f"rm -f {shlex.quote(remote)}")

    def crop_diffs(box):
        # 逐帧从 ffmpeg 管道读、边读边算相邻帧差: 整段 600 帧一次读进来转 float 要几 GB, 16 GB 的节点会被系统 SIGKILL
        vw, vh = map(int, subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                                          "-of", "csv=p=0", local], capture_output=True, text=True, pass_fds=lock_fds()).stdout.strip().split(","))
        x, yy = int(box[0] * vw) // 2 * 2, int(box[1] * vh) // 2 * 2
        w, h = (int(box[2] * vw) - x) // 2 * 2, (int(box[3] * vh) - yy) // 2 * 2
        w2, h2 = w // 2, h // 2   # 再缩一半: 量的是平均差, 不需要全分辨率
        p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", local, "-vf", f"crop={w}:{h}:{x}:{yy},scale={w2}:{h2},format=gray",
                              "-f", "rawvideo", "-"], stdout=subprocess.PIPE, pass_fds=lock_fds())
        n, prev, d = 0, None, []
        while True:
            buf = p.stdout.read(w2 * h2)
            if len(buf) < w2 * h2:
                break
            cur = np.frombuffer(buf, np.uint8).astype(np.int16)
            if prev is not None:
                d.append(float(np.abs(cur - prev).mean()))
            prev, n = cur, n + 1
        p.wait()
        return n, np.array(d)

    try:
        hf, hd = crop_diffs(PAN_HUD)
        _, bd = crop_diffs(PAN_BG)
        moving = bd > 1.0   # 只算画面确实在动的相邻帧 (拖动前后静止段不算)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", local],
                                   capture_output=True, text=True, pass_fds=lock_fds()).stdout.strip() or 0)
        return {"hud_flicker": round(float(hd[moving].mean()), 3) if moving.any() else None,
                "pan_bg_motion": round(float(bd[moving].mean()), 3) if moving.any() else None,
                "pan_moving_frames": int(moving.sum()), "pan_rec_fps": round(hf / dur, 1) if dur else None}
    except (ValueError, IndexError) as e:
        return {"pan_error": repr(e)[:200]}


def sc_layer_fps(dev, windows=6, raw_path=None):
    """插帧子图层 (selfsr_interp) 的实际上屏帧率: SF --latency 的 actual-present 列, 每段 1 s (缓冲只存 128 帧)。
    timestats 不收这个子图层, 只能这样量。返回 {fps, interval_p50_ms, interval_p95_ms} 或 {}。"""
    out = dev.shell("N=$(dumpsys SurfaceFlinger --list | grep -o '[^{ ]*selfsr_interp#[0-9]*' | tail -1); [ -n \"$N\" ] || exit 0; "
                    f"for i in $(seq {windows}); do dumpsys SurfaceFlinger --latency-clear >/dev/null; sleep 1; "
                    "echo ===; dumpsys SurfaceFlinger --latency \"$N\"; done; echo '### mode'; "
                    "dumpsys SurfaceFlinger | grep -iE 'activeMode|renderRate|vsyncRate|desiredMode' | head -12; "
                    "settings get system refresh_rate_mode", timeout=windows * 4 + 30)
    if raw_path:
        open(raw_path, "w").write(out)
    out = out.split("### mode")[0]
    fps, iv = [], []
    for blk in out.split("===")[1:]:
        ts = sorted(int(r[1]) for r in (ln.split() for ln in blk.splitlines()) if len(r) == 3 and 0 < int(r[1]) < 9e18)
        if len(ts) > 2:
            fps.append((len(ts) - 1) / ((ts[-1] - ts[0]) / 1e9))
            iv += [(b - x) / 1e6 for x, b in zip(ts, ts[1:])]
    if not fps:
        return {}
    iv.sort()
    return {"fps": round(statistics.mean(fps), 2), "interval_p50_ms": round(iv[len(iv) // 2], 2),
            "interval_p95_ms": round(iv[int(len(iv) * 0.95)], 2)}


# 窗口后抓 3 s SF 的 atrace (gfx), 数红魔 waitframe 的三个痕迹: commit 等 fence 没 signal 的缓冲 / 整帧跳过 / 缓冲被后一张顶掉
SF_MARKS = {"sf_fence_unsignaled": "fence unsignaled", "sf_not_composited": "onCommitNotComposited", "sf_release_pending": "releasePendingBuffer"}


def sf_atrace(dev, name, out, secs=3):
    txt = dev.su(f"atrace -b 65536 -t {secs} gfx view", timeout=secs + 30) or ""
    open(os.path.join(out, "traces", f"{name}_atrace.txt"), "w").write(txt)
    r = {k: round(txt.count(v) / secs, 1) for k, v in SF_MARKS.items()}
    # 等的是谁的缓冲: fence unsignaled 行前后常带图层名, 留几行样本
    r["sf_fence_samples"] = [ln.strip()[-160:] for ln in txt.splitlines() if "fence unsignaled" in ln][:5]
    return r


def measure(dev, a, name, out):
    dev_trace = f"/data/local/tmp/selfsr_trace_{name}.txt"
    ts_cmd = ("dumpsys SurfaceFlinger --timestats -disable -clear >/dev/null; "
              "dumpsys SurfaceFlinger --timestats -enable >/dev/null; "
              f"sleep {a.window}; dumpsys SurfaceFlinger --timestats -dump -maxlayers 8; "
              "dumpsys SurfaceFlinger --timestats -disable >/dev/null")
    recorder = None
    recording = False
    if getattr(a, "perfetto", False):
        recorder = PerfettoCapture(dev, a.pkg, a.window, Path(out) / "traces" / f"{name}_perfetto", name)
        recording = recorder.start()
        if not recording and recorder.state.get("recovery_unknown"):
            return {"error": "Perfetto startup cleanup is unconfirmed", "recovery_unknown": True,
                    "perfetto": recorder.state}
        if recording:
            start_marker = marker_script(recorder.state["marker"], "B")
            end_marker = marker_script(recorder.state["marker"], "E")
            ts_cmd = ("dumpsys SurfaceFlinger --timestats -disable -clear >/dev/null; "
                      "dumpsys SurfaceFlinger --timestats -enable >/dev/null; "
                      f"{start_marker}; sleep {a.window}; {end_marker}; "
                      "dumpsys SurfaceFlinger --timestats -disable >/dev/null; "
                      "dumpsys SurfaceFlinger --timestats -dump -maxlayers 8")
    try:
        mali = collect_mali(dev, a.serial, a.pkg, a.window,
                            os.path.join(out, "traces", f"{name}_mali"),
                            power_profile=getattr(a, "power_profile", "disabled"),
                            **({"perfetto_marker": recorder.state["marker"]} if recording else {}))
        if mali is not None:
            if mali.get("recovery_unknown") or mali.get("error"):
                return {"error": mali.get("error") or "Mali telemetry cleanup is unconfirmed",
                        "recovery_unknown": bool(mali.get("recovery_unknown")),
                        "mali_telemetry": {k: v for k, v in mali.items() if k != "timestats"}}
            ts_text = mali["timestats"]
            power = {}
            game = {}
        else:
            tr = subprocess.Popen(f"adb -s {shlex.quote(a.serial)} shell su -c "
                                  + shlex.quote(shlex.quote(f"sh {DEV_TRACE} {a.window} {dev_trace} {a.pkg}")),
                                  shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=lock_fds())
            pw = None
            if a.power:   # 先停充、等电流稳定, 再开 timestats 窗口, 两者对齐
                pw = subprocess.Popen(f"adb -s {shlex.quote(a.serial)} shell su -c "
                                      + shlex.quote(shlex.quote(f"sh {DEV_POWER} {a.window} {POWER_PRE}")),
                                      shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, pass_fds=lock_fds())
                time.sleep(POWER_PRE)
            ts_text = dev.su(ts_cmd, timeout=a.window + 60) if recording else dev.shell(ts_cmd, timeout=a.window + 60)
            tr.wait(timeout=a.window + 90)
            power = {}
            if pw is not None:
                rows = [ln.split() for ln in pw.communicate(timeout=a.window + 60)[0].splitlines()]
                rows = [[int(x) for x in r] for r in rows if len(r) == 3 and all(x.lstrip("-").isdigit() for x in r)]
                mw = [-c * v / 1e9 for c, v, _ in rows]   # uA × uV → mW; 放电为负
                power = {"power_mw": round(statistics.mean(mw), 1) if mw else None, "power_samples": len(mw),
                         "power_charging_seen": any(c > 0 for c, _, _ in rows),
                         "bat_temp_c": round(statistics.mean(t for _, _, t in rows) / 10, 1) if rows else None}
                open(os.path.join(out, "traces", f"{name}_power.txt"), "w").write("\n".join(" ".join(map(str, r)) for r in rows) + "\n")
    finally:
        if recorder is not None:
            recorder.stop()
    if recorder is not None and recorder.state.get("recovery_unknown"):
        return {"error": "Perfetto capture cleanup is unconfirmed", "recovery_unknown": True,
                "perfetto": recorder.state}
    if recorder is not None:
        recorder.analyze()
    ts = parse_timestats(ts_text, a.pkg)
    ts_sc = timestats_layer(ts_text, "selfsr_interp")
    sc = sc_layer_fps(dev, raw_path=os.path.join(out, "traces", f"{name}_sclat.txt"))
    sfa = sf_atrace(dev, name, out) if a.sf_atrace else {}
    if mali is None:
        local_trace = os.path.join(out, "traces", f"{name}.txt")
        dev.adb(f"exec-out su -c {shlex.quote('cat ' + dev_trace)} > {shlex.quote(local_trace)}")
        dev.su(f"rm -f {dev_trace}")
        pt = subprocess.run([a.phonefarm, "parse-trace", local_trace], capture_output=True, text=True, pass_fds=lock_fds())
        try:
            game = json.loads(pt.stdout)
        except ValueError:
            game = {}
    fg = dev.shell("dumpsys window | grep mCurrentFocus")
    sf = dev.shell("dumpsys SurfaceFlinger")
    src = layer_src(sf, a.pkg)
    # 游戏图层在 SF 里的帧率票 / HWC 合成方式: 插帧时上屏率被谁限住要看这里
    keep = [ln for i, ln in enumerate(sf.splitlines()) if a.pkg[:12] in ln or "Frame Rate" in ln or "frameRate" in ln
            or "FrameRateOverride" in ln or "refresh" in ln.lower()][:80]
    open(os.path.join(out, "traces", f"{name}_sf.txt"), "w").write("\n".join(keep) + "\n")
    shot = os.path.join(out, "shots", f"{name}.png")
    dev.adb(f"exec-out screencap -p > {shlex.quote(shot)}")
    if getattr(a, "video", 0) > 0:   # 窗口后录屏: 实况素材 (画质/视频对比), 游戏仍在跑; 不进任何指标
        remote = "/data/local/tmp/selfsr_arm_video.mp4"
        try:
            subprocess.run(["adb", "-s", a.serial, "shell", "screenrecord", "--time-limit", str(int(a.video)),
                            "--bit-rate", "40000000", remote], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=a.video + 60, pass_fds=lock_fds())
            dev.adb(f"pull {remote} {shlex.quote(os.path.join(out, 'traces', f'{name}_video.mp4'))}")
            dev.shell(f"rm -f {remote}")
        except (subprocess.SubprocessError, OSError) as e:
            open(os.path.join(out, "traces", f"{name}_video.err"), "w").write(f"录屏失败: {e}\n")
    pan = pan_probe(dev, a, name, out) if a.pan else {}
    span = (ts.get("displayOnTime") or a.window * 1000) / 1000
    extras = {**mali.get("metrics", {}), "mali_telemetry": {k: v for k, v in mali.items() if k != "timestats"}} if mali is not None else {}
    if recorder is not None:
        extras["perfetto"] = recorder.state
    return {**sfa, **extras, "in_game": a.pkg in fg,
            "display_fps": round(ts["layer_frames"] / span, 2) if ts.get("layer_frames") else None,
            "display_hz": ts.get("display_hz"), "missed_frames": ts.get("missedFrames"),
            "display_fps_sc": sc.get("fps") or (round(ts_sc["layer_frames"] / ((ts_sc.get("displayOnTime") or a.window * 1000) / 1000), 2)
                                                if ts_sc.get("layer_frames") else None),
            "sc_interval_p95_ms": sc.get("interval_p95_ms"),
            # 看到的画面的帧率: 子图层插帧时游戏图层被盖住, 取子图层; 否则取游戏图层
            # (--latency 量不到时退回 timestats 里子图层的帧数, 再没有才取游戏图层)
            "seen_fps": sc.get("fps") or (round(ts_sc["layer_frames"] / ((ts_sc.get("displayOnTime") or a.window * 1000) / 1000), 2)
                                          if ts_sc.get("layer_frames") else None)
                        or (round(ts["layer_frames"] / span, 2) if ts.get("layer_frames") else None),
            "game_fps": game.get("fps_mean"), "game_frame_p95_ms": game.get("frame_p95"),
            "gpu_active_ms": game.get("gpu_active_mean"),
            "out_res": src, "sharp_full": sharpness(shot), "sharp_hud": sharpness(shot, HUD_BOX), "shot": shot, **power, **pan}


def uptime_ns(dev):
    """手机的 CLOCK_MONOTONIC (≈ /proc/uptime, 不含休眠), 与层 CSV 里的时刻同一个钟。"""
    try:
        return int(float(dev.shell("cat /proc/uptime").split()[0]) * 1e9)
    except (ValueError, IndexError):
        return None


def interp_csv_path(pkg):
    return f"/storage/emulated/0/Android/data/{pkg}/files/knobs_interp_frames.csv"


def interp_csv_size(dev, a):
    """CSV 只追加不截断, 子图层后端 (backend=sc) 也不往里写: 窗口前记下大小, 窗口后只读这之后追加的行,
    否则读到的是以前某次运行的尾巴 (2026-10-28 第 7 组每个插帧臂都拿到同一段旧数据)。"""
    s = dev.su(f"stat -c %s {shlex.quote(interp_csv_path(a.pkg))}").strip()
    return int(s) if s.isdigit() else 0


def interp_window(dev, a, name, out, t0, t1, since=0):
    """层的 knobs_interp_frames.csv 里 [t0, t1] 内实际上屏的帧: 每秒几张真实帧 / 合成帧, 上屏间隔。
    since: 窗口开始前 CSV 的字节数 (interp_csv_size), 只看这之后追加的行。"""
    import statistics as st
    path = interp_csv_path(a.pkg)
    local = os.path.join(out, "traces", f"{name}_interp.csv")
    dev.adb(f"exec-out su -c {shlex.quote('cat ' + path)} > {shlex.quote(local)}")
    data = open(local, "rb").read()
    if len(data) < since:   # 文件被换过, 整个都是新的
        since = 0
    if len(data) == since:
        return {"status": "no_new_rows", "csv_bytes": len(data)}
    rows = []
    for ln in data[since:].decode(errors="replace").splitlines():
        f = ln.strip().split(",")
        if len(f) >= 8 and f[0] == "timing":
            try:
                rows.append((int(f[7]), f[2]))
            except ValueError:
                pass
    # /proc/uptime 是 BOOTTIME, 层的时刻是 CLOCK_MONOTONIC (差了累计休眠), 对不齐;
    # 改为以 CSV 里最后一条上屏时刻为终点往前取一个测量窗口 (CSV 在测量结束后才拉, 每 64 行落一次盘)
    if not rows:
        return {"status": "no_rows"}
    end = max(r[0] for r in rows)
    span = (t1 - t0) / 1e9 if (t0 and t1 and t1 > t0) else float(a.window)
    win = sorted(r for r in rows if end - span * 1e9 <= r[0] <= end)
    if len(win) < 2:
        return {"status": "no_samples", "rows": len(rows)}
    iv = [(b[0] - a_[0]) / 1e6 for a_, b in zip(win, win[1:])]
    return {"span_s": round(span, 2), "displayed_per_s": round(len(win) / span, 2),
            "real_per_s": round(sum(1 for r in win if r[1] == "real") / span, 2),
            "synth_per_s": round(sum(1 for r in win if r[1] == "synth") / span, 2),
            "interval_p50_ms": round(st.median(iv), 2), "interval_max_ms": round(max(iv), 2)}


def layer_absent(dev, pkg):
    lib = dev.su(f"ls -d /data/app/*/{pkg}-*/lib/arm64").strip().splitlines()
    has_so = any(dev.su(f"ls {shlex.quote(d)} | grep -c libVkLayer_refknobs").strip() not in ("0", "") for d in lib)
    prop = dev.prop("debug.vulkan.layers")
    return (not has_so) and prop == "", {"so_present": has_so, "debug.vulkan.layers": prop}


def enter_by_script(a, out, name, cold):
    """--enter-script: 给 onboard 认不出「已在游戏里」的游戏用的固定进门脚本。
    步骤用 ; 分隔: s:<秒> 等待, t:<x>,<y> 点屏幕 (横屏下的比例坐标)。cold 时先冷启动游戏。"""
    dev = Dev(a.serial)
    log = []
    if cold:
        dev.shell(f"am force-stop {a.pkg}")
        dev.shell(f"monkey -p {a.pkg} -c android.intent.category.LAUNCHER 1")
    for step in a.enter_script.split(";"):
        op, _, arg = step.strip().partition(":")
        if op == "s":
            time.sleep(float(arg))
        elif op == "t":
            fx, fy = (float(v) for v in arg.split(","))
            m = re.search(r" cur=(\d+)x(\d+)", dev.shell("dumpsys window displays | grep -m1 ' cur='"))
            w, h = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            if not w:
                return 1, "读不到屏幕尺寸"
            dev.shell(f"input tap {int(fx * w)} {int(fy * h)}")
        log.append(step)
    fg = dev.shell("dumpsys activity activities | grep -m1 -E 'topResumedActivity|mResumedActivity'")
    os.makedirs(os.path.join(out, "onboard"), exist_ok=True)
    json.dump({"steps": log, "foreground": fg.strip()}, open(os.path.join(out, "onboard", f"{name}.json"), "w"), ensure_ascii=False)
    return (0, "") if a.pkg in fg else (1, f"脚本走完但前台不是 {a.pkg}: {fg.strip()[:200]}")


def capture_recovery_pending(out):
    """Check durable capture obligations shared with leased child processes."""
    if any(Path(out).glob("onboard/*_world/recovery-pending.json")):
        return True
    for folder in Path(out).glob("traces/*_perfetto"):
        try:
            receipt = json.loads((folder / "perfetto-cleanup.json").read_text())
            if receipt.get("cleanup_confirmed") is not True or receipt.get("recovery_unknown") is not False:
                return True
        except (OSError, ValueError, AttributeError):
            return True
    for folder in Path(out).glob("traces/*_mali"):
        try:
            cleanup = json.loads((folder / "mali-cleanup.json").read_text())
        except (OSError, ValueError):
            return True
        if (not isinstance(cleanup, dict) or cleanup.get("cleanup_confirmed") is not True
                or cleanup.get("recovery_unknown") is not False):
            return True
    return False


def verify_game_world(a, out, name):
    """Retain a recovery obligation until capture returns a conclusive receipt."""
    if a.pkg != PKG_DEFAULT:
        return 1, f"World verification only supports {PKG_DEFAULT}; capture has no package option"
    pending = Path(out) / "onboard" / (name + "_world") / "recovery-pending.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    with pending.open("w") as stream:
        json.dump({"recovery_unknown": True}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    code, error = _verify_game_world(a, out, name)
    if code != RECOVERY_UNKNOWN:
        pending.unlink()
    return code, error


def _verify_game_world(a, out, name):
    """Require a captured Genshin exploration frame before measuring an arm.

    Args:
        a: Parsed runner arguments, including the phonefarm path and serial.
        out: Run output directory.
        name: Arm name used to separate capture evidence.

    Returns:
        A pair containing the exit code and a diagnostic message.
    """
    if a.pkg != PKG_DEFAULT:
        return 1, f"World verification only supports {PKG_DEFAULT}; capture has no package option"
    args = [a.phonefarm, "capture", "--serial", a.serial,
            "--out", os.path.join(out, "onboard", name + "_world"),
            "--ready-only", "--json", "--screenshot-timeout-ms", "45000", "--timeout-s", "120"]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=180, pass_fds=lock_fds())
    except subprocess.TimeoutExpired as e:
        return RECOVERY_UNKNOWN, f"World verification capture failed; recovery unknown: {e}"
    except OSError as e:
        return 1, f"World verification capture failed: {e}"
    try:
        report = json.loads(r.stdout)
    except ValueError:
        return RECOVERY_UNKNOWN, f"World verification capture returned invalid JSON; recovery unknown (exit={r.returncode}): {r.stderr[-600:]}"
    if not isinstance(report, dict):
        return RECOVERY_UNKNOWN, "World verification capture summary must be a JSON object; recovery unknown"
    owned = os.path.realpath(os.path.join(out, "onboard", name + "_world"))
    visited = set()

    def read_evidence(path):
        """Read bounded JSON belonging to this arm's capture output."""
        if not isinstance(path, str) or not path:
            raise ValueError("missing evidence path")
        path = os.path.realpath(path if os.path.isabs(path) else os.path.join(owned, path))
        if os.path.commonpath((owned, path)) != owned or path in visited:
            raise ValueError("unrelated or repeated evidence path")
        visited.add(path)
        with open(path, encoding="utf-8") as stream:
            value = json.loads(stream.read(1024 * 1024 + 1))
        if not isinstance(value, dict):
            raise ValueError("capture evidence must be an object")
        return value

    # Current phonefarm emits a flat report. Also accept explicit envelopes,
    # retaining every recovery result instead of trusting the innermost frame.
    reports = [report]
    try:
        for _ in range(4):
            if "frames" in report or (report.get("ok") is False and report.get("error")):
                break
            if "report" in report:
                report = report["report"]
                if not isinstance(report, dict):
                    raise ValueError("missing capture report")
            elif report.get("report_path"):
                report = read_evidence(report["report_path"])
            else:
                break
            reports.append(report)
    except (OSError, ValueError, TypeError) as e:
        return RECOVERY_UNKNOWN, f"World verification evidence unavailable; recovery unknown ({type(e).__name__})"
    recoveries = [item["recovery"] for item in reports if "recovery" in item]
    if not recoveries or any(not isinstance(item, dict) or item.get("status") != "reported_complete"
                             or item.get("error") not in (None, "") for item in recoveries):
        return RECOVERY_UNKNOWN, f"World verification capture recovery is not reported_complete: {report.get('error') or r.stderr[-600:]}"
    if "frames" not in report:
        # A worker file alone is insufficient: require completed outer recovery,
        # successful worker exit and a path confined to this capture's output.
        session = next((item.get("session") for item in reversed(reports) if item.get("session")), None)
        try:
            worker = read_evidence(os.path.join(session, "worker-result.json"))
        except (OSError, ValueError, TypeError) as e:
            return 1, f"World verification frame evidence unavailable ({type(e).__name__})"
        if (worker.get("schema") != "phonefarm/capture-worker-result@1"
                or type(worker.get("exit_code")) is not int or worker["exit_code"] != 0
                or worker.get("error") is not None or not isinstance(worker.get("report"), dict)):
            return 1, "World verification worker did not complete successfully"
        serial = next((item.get("serial") for item in recoveries if item.get("serial")), None)
        if serial is not None and worker.get("serial") != serial:
            return 1, "World verification worker identity differs from capture recovery"
        report = worker["report"]
        reports.append(report)
        if "recovery" in report and (not isinstance(report["recovery"], dict)
                                    or report["recovery"].get("status") != "reported_complete"
                                    or report["recovery"].get("error") not in (None, "")):
            return RECOVERY_UNKNOWN, "World verification worker reports incomplete recovery"
    # Upstream's internal `kept` counter is serialized as `frames`, not `kept`.
    frames = report.get("frames")
    failed_envelope = any(item.get("ok") is False or item.get("error") not in (None, "")
                          or item.get("report_error") not in (None, "") for item in reports)
    if (r.returncode != 0 or failed_envelope or report.get("ok") is not True or report.get("ready_only") is not True
            or type(frames) is not int or frames != 1 or report.get("stop_reason") != "target_reached"):
        return r.returncode or 1, (f"World verification failed (exit={r.returncode}, ok={report.get('ok')}, "
                                  f"frames={frames}, stop_reason={report.get('stop_reason')}): {r.stderr[-600:]}")
    return 0, ""


def onboard(a, out, name, cold):
    """Enter the game and verify the world when requested or using fixed taps."""
    scripted = bool(getattr(a, "enter_script", None))
    if scripted:
        code, err = enter_by_script(a, out, name, cold)
    else:
        args = [a.phonefarm, "onboard", "--package", a.pkg, "--serial", a.serial, "--enter-only", "--no-guest",
                "--out", os.path.join(out, "onboard", name), "--vlm-calls", str(a.vlm_calls)]
        if not cold:
            # Navigation inherits the lease and must not restart a layered game.
            args += ["--no-cold"]
        r = subprocess.run(args, capture_output=True, text=True, timeout=900, pass_fds=lock_fds())
        code, err = r.returncode, r.stderr[-600:]
    if code != 0:
        return code, err
    # Foreground membership after fixed taps does not prove world readiness.
    if scripted or a.verify_game_world:
        return verify_game_world(a, out, name)
    return code, err


PANEL_KEYS = ("refresh_rate_mode",)
# 红魔 system refresh_rate_mode: 1/2/3/4 = 60/90/120/144 Hz, 0 = 自动。2026-10-24 panel_probe.sh 实测原神在前台时
# 设 4 屏幕跑 144.5 Hz; AOSP 的 min/peak_refresh_rate 设 144 无效 (仍 60 Hz)。
PANEL_MODE = {"60": 1, "90": 2, "120": 3, "144": 4}
PANEL_ORIG = {"path": None}   # <输出目录>/panel_orig.json: 开跑前的原值 (run 写, prepare/restore 读)


# sfovr=<Hz>: 进世界后用 SF 后门 (事务 1039) 给游戏 uid 下帧率覆盖, 优先于按图层投票算出的覆盖;
# 红魔上游戏主图层投 60 时, SF 的 isVsyncValid 按 60 放行同 uid 的子图层事务。传 0 删除覆盖。
SFOVR_UID = {"uid": None}
# sfwait=off: 进世界后关红魔 SF 的 waitframe (dumpsys SurfaceFlinger --fpsControl_waitframe_off)。它在 commit 里每 1 ms
# 轮询一次游戏图层缓冲的 fence, 一等 7~8 ms, 下一个 vsync 因 backpressure 整帧跳过, 子图层缓冲两张撞车被顶掉
SFWAIT_OFF = {"on": False}


def set_sfovr(dev, uid, hz):
    dev.su(f"service call SurfaceFlinger 1039 i32 {int(uid)} f {float(hz)}")


# debug.* 属性用 shell 身份写: 荣耀的 su (u:r:kernel) 被 init 拒写 debug.*, shell 域在所有机器上都能写。
def restore_panel(dev):
    # 收尾可能在另一个进程 (_locked), 不知道下过哪个 uid: 按 SF 里现有的 Backdoor 条目逐个删
    dump = dev.su("dumpsys SurfaceFlinger | grep -A8 '^FrameRateOverrides'")
    back = dump.split("Backdoor=", 1)[1] if "Backdoor=" in dump else ""
    for uid in re.findall(r"\{(\d+), [0-9.]+ Hz\}", back.split("\n\n", 1)[0]):
        set_sfovr(dev, uid, 0)
    SFOVR_UID["uid"] = None
    # sfwait=off 关掉的 ZTE waitframe 写回开 (红魔出厂是开的: atrace 里 commit 每 1 ms 轮询游戏缓冲的 fence)
    # 收尾可能在另一个进程, 不知道关没关过: ZTE 机器上一律写回开 (别的机器没有这个开关, 不发)
    if SFWAIT_OFF["on"] or dev.prop("ro.vendor.feature.zte_feature_ifas") == "true":
        dev.su("dumpsys SurfaceFlinger --fpsControl_waitframe_on >/dev/null")
        SFWAIT_OFF["on"] = False
    try:
        orig = json.load(open(PANEL_ORIG["path"]))
    except (OSError, ValueError, TypeError):
        return
    for key in PANEL_KEYS:
        v = orig.get(key)
        dev.shell(f"settings delete system {key}" if v in (None, "null") else f"settings put system {key} {v}")
    if orig.get(FP_PROP) not in (None, "") and dev.prop(FP_PROP) != orig[FP_PROP]:
        dev.su(f"setprop {FP_PROP} {shlex.quote(orig[FP_PROP])}")


def gfrc_after_enter(dev, a, k):
    """进世界后写本包 G-FRC+ 位并回读厂商属性核对。游戏助手会记住上次开过 G-FRC+, 启动时自己再打开
    (全局设置只是镜像, 启动前写 000 管不住), 所以每一臂都在进世界后明确写一次: G-FRC+ 臂写目标位, 其它臂写 000。
    返回 (属性, 错误或 None)。本机没有 G-FRC+ (属性不存在) 时什么都不做。"""
    if dev.prop("vendor.gpp.frc.enable") in ("", None):
        return {}, None
    bits = k.get("gfrc") or "000"
    cur = dev.shell("settings get global game_gfrc_mode").strip()
    if cur in ("", "null"):
        return {}, None
    dev.shell(f"settings put global game_gfrc_mode {shlex.quote(set_pkg_bits(cur, a.pkg, bits))}")
    time.sleep(8)
    props = {p: dev.prop(f"vendor.gpp.{p}") for p in ("gfrc.interp.rate", "gfrc.upscale.ratio", "frc.enable")}
    want_up = bits[2] == "1" and bits[0] != "0"
    want_ip = bits[2] == "1" and bits[1] != "0"
    if ((props["gfrc.upscale.ratio"] not in ("", "0")) != want_up) or ((props["gfrc.interp.rate"] not in ("", "0")) != want_ip):
        return props, f"G-FRC+ 没按 {bits} 生效: {props}"
    return props, None


def prepare(a, k, orig_mode):
    """在设备锁下调用: Game Mode 与 selfsr 属性。"""
    dev = Dev(a.serial)
    dev.shell(f"am force-stop {a.pkg}")
    gm = game_mode(dev, a.pkg, k["downscale"], orig_mode)
    dev.shell(f"setprop {SR_PROP} {shlex.quote(k['selfsr'] or '')}")
    # 厂商 G-FRC+: 只改 settings global game_gfrc_mode 里本包那一项; 收尾写回开跑前原值 (orig.json)
    cur = dev.shell("settings get global game_gfrc_mode").strip()
    if cur not in ("", "null"):
        # 先一律关; G-FRC+ 要在进世界之后再开才生效 (见 arm_plain)
        dev.shell(f"settings put global game_gfrc_mode {shlex.quote(set_pkg_bits(cur, a.pkg, '000'))}")
    so = k.get("sr_opts") or {}
    dev.shell("; ".join(f"setprop debug.knobs.selfsr_{o} {shlex.quote(so.get(o, ''))}" for o in SR_OPTS if o != "unlimit"))
    unl = k.get("unlimit") or so.get("unlimit") == "1" or (k.get("tune") or {}).get("unlimit") == "1"
    dev.shell(f"setprop {UNLIMIT_PROP} {'1' if unl else shlex.quote('')}")
    mode = k.get("interp")
    dev.shell(f"setprop {IP_PROPS[0]} {'1' if mode else shlex.quote('')}; setprop {IP_PROPS[1]} {shlex.quote(mode or '')}; "
           f"setprop {IP_PROPS[2]} {shlex.quote(k.get('fps') or '')}")
    tune = k.get("tune") or {}
    dev.shell("; ".join(f"setprop debug.knobs.interp_{t} {shlex.quote(str(tune.get(t, {'static': '0', 'sc_name': 'none'}.get(t, ''))))}" for t in IP_TUNE if t not in ("panel", "framepacing", "unlimit", "sfovr", "sfwait")))
    # panel=<Hz>: 屏幕刷新率 (红魔 refresh_rate_mode), 收尾 (restore) 按开跑前记下的原值写回
    # 刷新率在进世界之后才设 (arm_layer): 原神启动时游戏助手会把它压回 60 Hz
    restore_panel(dev)
    return {"game_mode": gm, "selfsr_prop": dev.prop(SR_PROP), "interp_props": [dev.prop(p) for p in IP_PROPS]}


def batt_temp_c(dev):
    try:
        return int(re.search(r"temperature: (\d+)", dev.shell("dumpsys battery")).group(1)) / 10
    except (AttributeError, ValueError):
        return None


def wait_batt_temp(dev, want_c, rising, max_s=1200):
    """等电池温度到达 want_c (rising=True 等升到, False 等降到), 每 5 秒读一次, 超时返回最后的温度。"""
    t0 = time.time()
    last = None
    while time.time() - t0 < max_s:
        last = batt_temp_c(dev)
        if last is not None and (last >= want_c if rising else last <= want_c):
            break
        time.sleep(5)
    return last


# 进世界后设屏幕刷新率 (红魔 refresh_rate_mode), 回读 SF 周期确认; arm_layer 与 hold 探测共用。返回错误文本或 None
def set_panel(dev, k, rec):
    panel = str((k.get("tune") or {}).get("panel"))
    want = str(PANEL_MODE[panel])
    attempts = []
    for attempt in range(3):
        if attempt > 0 or dev.shell("settings get system refresh_rate_mode").strip() == want:
            # 值没变 settings 不发变更通知, 屏幕停在游戏自己要的刷新率 (残留原值时真机 60 Hz): 先写 0 再写目标。
            # 测量前的切换偶尔一次不生效；只在准备阶段有界重试，不触碰测量窗口或候选参数。
            dev.shell("settings put system refresh_rate_mode 0")
            time.sleep(1)
        dev.shell(f"settings put system refresh_rate_mode {want}")
        time.sleep(4)
        per = dev.shell("dumpsys SurfaceFlinger | grep -m1 mPeriod")
        rec["panel_period"] = per.strip()
        attempts.append(per.strip())
        m = re.search(r"\(([0-9.]+) fps\)", per)
        if m and abs(float(m.group(1)) - float(panel)) <= 3:
            rec["panel_attempts"] = attempts
            return None
    rec["panel_attempts"] = attempts
    return f"屏幕没切到 {panel} Hz（3 次尝试）: {per.strip()}"


def hold_probe(dev, a, name, out, k):
    """探测模式 (--hold N): 进世界/设好刷新率后不开测量窗口, 每秒采样厂商控制器结果、SF 周期/范围、电池与前台,
    查「测量窗口内屏幕被压回 60 Hz」的触发条件与对抗手段 (作废的 6 对 srip 臂都是这个现象)。
    环境变量 (按阶段设, 平台任务里传不进环境就用这个):
      HOLD_TAP=<秒>    每 N 秒在屏幕右侧空白点一下 (测输入事件是否阻止降档; 坐标自动取当前横屏右侧中点)
      HOLD_REASSERT=1  屏幕掉离目标刷新率时重设 refresh_rate_mode 0→目标 (测重设能不能守住、能守多久)
      HOLD_SEEN=<秒>   每 N 秒抽 2s 插帧子图层 SF latency 出 seen fps/间隔分位 (长稳双峰中途复发检测, 写 traces/<名>_seen.tsv)
    每秒一行写 traces/<名>_hold.tsv (中断也保住已采的); 返回汇总进 arms.jsonl。"""
    panel = str((k.get("tune") or {}).get("panel") or "")
    target = str(PANEL_MODE[panel]) if panel in PANEL_MODE else None
    tap_every = int(os.environ.get("HOLD_TAP") or 0)
    reassert = os.environ.get("HOLD_REASSERT") == "1"
    tap_x = tap_y = 0
    if tap_every:   # 点屏坐标: 当前显示方向的右侧中点 (原神 HUD 空白处, 单击不放技能不转视角)
        m = re.search(r" cur=(\d+)x(\d+)", dev.shell("dumpsys window displays | grep -m1 ' cur='") or "")
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            tap_x, tap_y = (int(w * 0.92), int(h * 0.5)) if w > h else (int(w * 0.5), int(h * 0.92))
    # 一次 adb 采全: 控制器结果在 global (system 里没有); 范围取 SF 收到的 primaryRanges (refresh_probe.sh 同源)
    sample = ('c=$(settings get global ScreenRefreshRateController_resultFps); '
              'm=$(settings get system refresh_rate_mode); '
              'p=$(dumpsys SurfaceFlinger 2>/dev/null | grep -m1 mPeriod); '
              'd=$(dumpsys SurfaceFlinger --displays 2>/dev/null); '
              'hz=$(echo "$d" | grep -m1 -o "activeMode={[^}]*vsyncRate=[0-9.]*" | grep -o "[0-9.]*$"); '
              'r=$(echo "$d" | grep -m1 -o "primaryRanges={physical=\\[[^]]*\\]" | sed "s/.*\\[//; s/\\]//; s/ Hz//g"); '
              'printf "%s|%s|%s|%s|%s\\n" "$c" "$m" "$p" "$hz" "$r"')
    tsv = os.path.join(out, "traces", f"{name}_hold.tsv")
    rows = ["wall\tt\tctrl_fps\tmode\tmperiod\tactive_hz\trange\tbat\tfocus\ttap\treassert\tok"]
    drops, reasserts = [], 0
    last_ok, hz, ctrl = True, None, ""
    fg_lost_since = None
    started = time.monotonic()
    next_tap = tap_every
    next_focus = 0.0
    seen_every = int(os.environ.get("HOLD_SEEN") or 0)   # >0: 每 N 秒抽 2s 子图层 latency (长稳双峰复发检测)
    stsv = os.path.join(out, "traces", f"{name}_seen.tsv")
    seen_hdr = False
    next_seen = float(seen_every)
    while True:
        iter_started = time.monotonic()
        elapsed = iter_started - started
        if elapsed >= float(a.hold):
            break
        i = int(elapsed)   # TSV 的 t 是实际经过秒数；采样慢于 1s 时不再把循环次数误当墙钟
        tapped = rea = 0
        if tap_every and elapsed >= next_tap:
            dev.shell(f"input tap {tap_x} {tap_y}")
            tapped = 1
            next_tap += tap_every
        if seen_every and elapsed >= next_seen:
            next_seen += seen_every
            try:   # 图层不在 (native/无插帧) 时 SF --list grep 空, 命令 exit 0 秒回, 返回 {} 不写行
                sc = sc_layer_fps(dev, 2)
                if sc.get("fps"):
                    if not seen_hdr:
                        open(stsv, "w").write("wall\tt\tfps\tinterval_p50_ms\tinterval_p95_ms\n")
                        seen_hdr = True
                    open(stsv, "a").write(
                        f"{time.time():.0f}\t{int(elapsed)}\t{sc['fps']}\t{sc.get('interval_p50_ms','')}\t{sc.get('interval_p95_ms','')}\n")
            except Exception as e:   # 采样失败不终止 hold, 记最后一行错误便于事后查
                open(stsv, "a").write(f"{time.time():.0f}\t{int(elapsed)}\tERROR\t{repr(e)[:100]}\n")
        vals = (dev.shell(sample, timeout=30) or "").strip().split("|")
        vals += [""] * (5 - len(vals))
        ctrl, mode, mper, active, rng = (v.strip() for v in vals[:5])
        m = re.search(r"\(([0-9.]+) fps\)", mper)
        hz = float(m.group(1)) if m else (float(active) if active.replace(".", "", 1).isdigit() else None)
        ok = None
        if hz is not None and panel:
            ok = abs(hz - float(panel)) <= 3   # True/False; None = 这秒没读到刷新率 (P1 实测教训: 不能用 x or None, False 会被塌成 None)
        if ok is not None:
            if not ok and last_ok:
                drops.append(i)   # 一次连续掉落记一次 (开始时刻)
            if not ok and reassert and target:
                dev.shell("settings put system refresh_rate_mode 0")
                time.sleep(0.5)
                dev.shell(f"settings put system refresh_rate_mode {target}")
                reasserts += 1
                rea = 1
            last_ok = bool(ok)
        bat = " ".join((dev.shell("dumpsys battery | grep -E 'status:|level:|temperature:' | tr '\\n' ' '", timeout=30) or "").split())[:90]   # 电池温度是掉落假设核心变量, 每秒采
        foc = ""
        if elapsed >= next_focus:   # 前台约 5 秒一次
            next_focus += 5
            foc = (dev.shell("dumpsys activity activities | grep -m1 -E 'topResumedActivity'", timeout=30) or "").strip()[-90:]
            if os.environ.get("HOLD_REQUIRE_FG") == "1":
                if a.pkg not in foc:
                    if fg_lost_since is None:
                        fg_lost_since = i
                    elif i - fg_lost_since >= 60:
                        # 长采样若已离开游戏，后续面板读数不能冒充挂层游戏数据；保留已采 TSV 并判无效。
                        open(tsv, "w").write("\n".join(rows) + "\n")
                        return {"hold_s": round(time.monotonic() - started, 3), "panel": panel or None,
                                "hold_drops_at_s": drops, "hold_reasserts": reasserts,
                                "hold_end_hz": hz, "hold_end_ctrl": ctrl,
                                "error": f"游戏连续离开前台 ≥{i - fg_lost_since}s (t={fg_lost_since}s 起, top={foc})"}
                else:
                    fg_lost_since = None
        rows.append("\t".join([time.strftime("%H:%M:%S"), str(i), ctrl, mode, mper, active, rng, bat, foc,
                               str(tapped), str(rea), "" if ok is None else str(int(bool(ok)))]))
        open(tsv, "w").write("\n".join(rows) + "\n")
        sleep_left = 1.0 - (time.monotonic() - iter_started)
        if sleep_left > 0:
            time.sleep(sleep_left)
    return {"hold_s": round(time.monotonic() - started, 3), "panel": panel or None,
            "hold_drops_at_s": drops, "hold_reasserts": reasserts,
            "hold_end_hz": hz, "hold_end_ctrl": ctrl}


def arm_plain(a, out, name, k, orig_mode):
    """不挂层的臂 (整臂在 devlock 下被调用)。"""
    dev = Dev(a.serial)
    rec = {"prep": prepare(a, k, orig_mode)}
    code, err = onboard(a, out, name, cold=True)
    if code != 0:
        rec["error"] = f"onboard: {err}"
        if code == RECOVERY_UNKNOWN:
            rec["recovery_unknown"] = True
        return rec
    rec["gfrc_props"], err = gfrc_after_enter(dev, a, k)
    if err:
        rec["error"] = err
        return rec
    if getattr(a, "hold", 0) > 0:   # hold 探测: 无层臂也可用 HOLD_PANEL=120 指定刷新率 (对照: 掉落和层有没有关)
        hp = os.environ.get("HOLD_PANEL", "")
        if hp and not (k.get("tune") or {}).get("panel"):
            k = {**k, "tune": {**(k.get("tune") or {}), "panel": hp}}
            err = set_panel(dev, k, rec)
            if err:
                rec["error"] = err
                return rec
    time.sleep(a.settle)
    if getattr(a, "hold", 0) > 0:
        rec.update(hold_probe(dev, a, name, out, k))
    else:
        rec.update(measure(dev, a, name, out))
    if rec.get("recovery_unknown") or capture_recovery_pending(out):
        rec["recovery_unknown"] = True
        return rec
    dev.shell(f"am force-stop {a.pkg}")
    return rec


def keep120(a, k, name, out, events, stop):
    """测量窗口内的看门狗 (HOLD_KEEP=1): 电池温度向上穿 ~43°C 时厂商控制器会把屏幕压回 60 Hz
    (hold 探测结论: 触发是穿越事件, 高温之上/降温中不掉, 掉后不自己回来)。每秒读 SF mPeriod,
    偏离目标 >3 Hz 就重设 refresh_rate_mode 0→目标 (探测三 P1 实测: 重设 2 次, 之后稳定到结束)。
    每秒一行写 traces/<名>_keep.tsv, 重设时刻记进 events。"""
    dev = Dev(a.serial)
    panel = str((k.get("tune") or {}).get("panel"))
    target = str(PANEL_MODE[panel])
    tsv = os.path.join(out, "traces", f"{name}_keep.tsv")
    rows = ["wall\thz\taction"]
    while not stop["run"]:
        hz, act = None, ""
        try:
            per = dev.shell("dumpsys SurfaceFlinger | grep -m1 mPeriod", timeout=20)
            m = re.search(r"\(([0-9.]+) fps\)", per or "")
            hz = float(m.group(1)) if m else None
            if hz is not None and abs(hz - float(panel)) > 3:
                dev.shell("settings put system refresh_rate_mode 0")
                time.sleep(0.5)
                dev.shell(f"settings put system refresh_rate_mode {target}")
                act = "reassert"
                events.append({"t": time.time(), "hz": hz})
        except Exception:
            pass
        rows.append(f"{time.strftime('%H:%M:%S')}\t{hz if hz is not None else ''}\t{act}")
        try:
            open(tsv, "w").write("\n".join(rows) + "\n")
        except OSError:
            pass
        time.sleep(1)


def arm_layer(a, out, name, k, note, locked):
    dev = Dev(a.serial)
    rec = {"layer_path": os.path.abspath(a.so) if a.so else None, "layer_sha256": None}
    if a.so:
        try:
            with open(a.so, "rb") as library:
                rec["layer_sha256"] = hashlib.file_digest(library, "sha256").hexdigest()
        except OSError as e:
            rec["error"] = f"Cannot identify the selected layer: {e}"
            return rec
    r = locked("prep", name, k)
    rec["prep"] = r.get("prep")
    if r.get("error"):
        rec["error"] = r["error"]
        return rec
    sdir = os.path.join(out, "sessions", name)
    log = open(sdir + ".log", "w")
    cmd = [a.phonefarm, "gray-layer", "session", "--mode", "probe", "--package", a.pkg, "--serial", a.serial,
           "--out", sdir, "--max-seconds", str(a.session_seconds)]
    if a.so:
        cmd += ["--so", a.so]
    if a.keep_layer_prop:
        cmd += ["--keep-prop"]
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=SESSION_ENV, pass_fds=lock_fds())
    st = None
    try:
        phase = None
        for _ in range(450):
            time.sleep(2)
            if proc.poll() is not None:
                break
            code, s = pf_json(a.phonefarm, "gray-layer", "session-status", "--out", sdir, "--json")
            phase = s.get("phase") if code == 0 else None
            if phase == "ready":
                break
        if phase != "ready":
            rec["error"] = f"session not ready (phase={phase}, exit={proc.poll()})"
            return rec
        code, err = onboard(a, out, name, cold=False)
        if code != 0:
            rec["error"] = f"onboard --no-cold: {err}"
            if code == RECOVERY_UNKNOWN:
                rec["recovery_unknown"] = True
            return rec
        rec["gfrc_props"], err = gfrc_after_enter(dev, a, k)
        if err:
            rec["error"] = err
            return rec
        panel = (k.get("tune") or {}).get("panel")
        if panel and getattr(a, "hold", 0) > 0:
            warm = float(os.environ.get("HOLD_WARM") or 0)
            if warm > 0:   # 探测: 掉落发生在电池温度向上穿过 ~43°C 时 — 等穿过后再设 panel, 避开穿越事件本身
                t_c = wait_batt_temp(dev, warm, rising=True, max_s=900)
                rec["warm_wait_c"] = t_c
        if panel:   # 进世界后设刷新率 (红魔 refresh_rate_mode), 回读 SF 刷新周期确认 (set_panel)
            err = set_panel(dev, k, rec)
            if err:
                rec["error"] = err
                return rec
        fpv = (k.get("tune") or {}).get("framepacing")
        if fpv is not None:   # 进世界后设高通帧节奏开关, 回读核对
            dev.su(f"setprop {FP_PROP} {shlex.quote(str(fpv))}")
            rec["framepacing_now"] = dev.prop(FP_PROP)
        ovr = (k.get("tune") or {}).get("sfovr")
        if ovr:
            m = re.search(r"uid:(\d+)", dev.shell(f"pm list packages -U {a.pkg}"))
            if not m:
                rec["error"] = "sfovr: 读不到游戏 uid"
                return rec
            SFOVR_UID["uid"] = int(m.group(1))
            set_sfovr(dev, SFOVR_UID["uid"], ovr)
            rec["sfovr"] = dev.su("dumpsys SurfaceFlinger | grep -A6 '^FrameRateOverrides'").strip()
        if (k.get("tune") or {}).get("sfwait") == "off":
            SFWAIT_OFF["on"] = True
            rec["sfwait"] = dev.su("dumpsys SurfaceFlinger --fpsControl_waitframe_off").strip()[-200:]
        time.sleep(a.settle)
        code, s = pf_json(a.phonefarm, "gray-layer", "session-status", "--out", sdir, "--json")
        if proc.poll() is not None or s.get("phase") != "ready":
            rec["error"] = f"会话在测量前已结束 (phase={s.get('phase')}, stop={s.get('stop_reason')})"
            return rec

        # REROLL=1: 窗口前探测子图层呈现节律 (srip-cadence: 冷启动随机定型双稳, 症状臂 88-90fps / 干净臂 116-119)。
        # fps<100 判症状档 → rec["reroll"]=True 提前返回 (finally 摘层清场), 上层丢弃本次冷启动重掷;
        # 干净则只留 reroll_probe 记录继续测量。探测拿不到 fps (子图层不在/命令失败) 与症状同路, 预算耗尽照常开窗口。
        # REROLL_LAST=1 (上层在预算最后一次尝试设置): 症状也只留记录、照常开窗口拿完整数据 (fa95b1ed8796 教训:
        # 预算用尽的臂提前返回会缺窗口指标, 症状判定只能靠探测值)。
        if os.environ.get("REROLL") == "1" and k["kind"] == "srip" and getattr(a, "hold", 0) <= 0:
            sc = sc_layer_fps(dev, 3, raw_path=os.path.join(out, "traces", f"{name}_probe.txt"))
            rec["reroll_probe"] = {"fps": sc.get("fps"), "interval_p95_ms": sc.get("interval_p95_ms"),
                                   "clean": bool(sc.get("fps") is not None and sc["fps"] >= 100)}
            if not rec["reroll_probe"]["clean"] and os.environ.get("REROLL_LAST") != "1":
                rec["reroll"] = True
                return rec

        def marker():
            m = session_action(a.phonefarm, sdir, "marker")
            try:
                return json.load(open(os.path.join(sdir, m["result"]["path"])))
            except Exception:
                return {}

        m0 = marker()
        if getattr(a, "hold", 0) > 0:   # hold 探测: 不开测量窗口, 每秒采样刷新率 (查 120→60 掉落); finally 照常摘层
            rec.update(hold_probe(dev, a, name, out, k))
            m1 = marker()
            # 长会话必须在开始和结束都证明层加载；只在结束取 marker 会把会话到期误报成「从未加载」。
            rec["layer_loaded"] = bool(m0.get("layer_loaded") and m1.get("layer_loaded"))
            rec["unlimit"] = m1.get("unlimit")
            if k["kind"] in ("sr", "srip"):
                rec["selfsr_initial"] = m0.get("selfsr") or {}
                rec["selfsr"] = m1.get("selfsr") or {}
                rec["selfsr_upscaled_in_hold"] = ((rec["selfsr"].get("upscaled") or 0)
                                                   - (rec["selfsr_initial"].get("upscaled") or 0))
                if (rec["selfsr"].get("upscaled") or 0) <= 0 or rec["selfsr_upscaled_in_hold"] <= 0:
                    rec["error"] = f"层在 hold 窗口内没有超分 (initial={rec['selfsr_initial']}, final={rec['selfsr']})"
            return rec

        csv0 = interp_csv_size(dev, a) if k["kind"] in ("ip", "srip") else 0
        t0 = uptime_ns(dev)
        keep_events, keep_stop = [], {"run": False}
        kthr = None
        if panel and os.environ.get("HOLD_KEEP") == "1":   # 窗口内看门狗: 掉落立刻重设 (keep120, 探测三 P1 验证)
            kthr = threading.Thread(target=keep120, args=(a, k, name, out, keep_events, keep_stop), daemon=True)
            kthr.start()
        rec.update(measure(dev, a, name, out))
        t1 = uptime_ns(dev)
        if panel:   # 窗口结束再回读一次: 系统可能在窗口中途把刷新率压回去 (真机: 开始 120 Hz, 窗口内降到 60, 子图层只上屏 60)
            per = dev.shell("dumpsys SurfaceFlinger | grep -m1 mPeriod")
            rec["panel_period_end"] = per.strip()
            m = re.search(r"\(([0-9.]+) fps\)", per)
            if not m or abs(float(m.group(1)) - float(panel)) > 3:
                time.sleep(3)   # 看门狗可能正卡在重设中间, 给一个周期重读
                per = dev.shell("dumpsys SurfaceFlinger | grep -m1 mPeriod")
                rec["panel_period_end"] = per.strip()
                m = re.search(r"\(([0-9.]+) fps\)", per)
            # HOLD_KEEP 在场时窗口内可能有「穿 43°C → 掉 60 → 重设回 120」的短暂凹陷:
            # 结束时 mPeriod 在目标上、且发生过重设, timestats display_hz 读到 60 也不作废 (凹陷秒数记 keep_dips, 逐秒在 keep.tsv)
            dip = bool(kthr is not None and keep_events)
            if not m or abs(float(m.group(1)) - float(panel)) > 3 or (rec.get("display_hz") not in (None, int(panel)) and not dip):
                rec["error"] = f"测量窗口内屏幕没保持 {panel} Hz: 结束时 {per.strip()}, timestats {rec.get('display_hz')} Hz"
            if dip:
                rec["keep_dips"] = len(keep_events)
        keep_stop["run"] = True
        if kthr is not None:
            kthr.join(timeout=10)
        m1 = marker()
        if k["kind"] in ("ip", "srip"):   # 窗口内层回读的实际上屏 (会话累计的环形统计混着加载期, 不可用)
            rec["interp_window"] = interp_window(dev, a, name, out, t0, t1, since=csv0)
            iw = rec["interp_window"]
            rec["displayed_per_s"] = iw.get("displayed_per_s")
        rec["layer_loaded"] = bool(m0.get("layer_loaded") and m1.get("layer_loaded"))
        f0 = (m0.get("readonly_stats") or {}).get("frames")
        f1 = (m1.get("readonly_stats") or {}).get("frames")
        rec["layer_frames_in_window"] = (f1 - f0) if (f0 is not None and f1 is not None) else None
        if k["kind"] in ("sr", "srip"):
            s0, s1 = m0.get("selfsr") or {}, m1.get("selfsr") or {}
            rec["selfsr"] = s1
            up = (s1.get("upscaled") or 0) - (s0.get("upscaled") or 0)
            rec["selfsr_upscaled_in_window"] = up
            # 层的 marker 每 300 帧落一次盘, 游戏帧率低时窗口内可能一次没落: 判「在超分」看累计次数
            if (s1.get("upscaled") or 0) <= 0:
                rec["error"] = f"层没在超分 (selfsr={s1})"
        rec["unlimit"] = m1.get("unlimit")
        if k["kind"] in ("ip", "srip"):
            ipj = m1.get("interp") or {}
            rec["interp"] = ipj
            lat = ipj.get("real_present_to_display_ns") or {}
            # 真实帧 present→上屏 (整个会话累计); 插帧带来的延迟 = 与 ip:real 对照臂之差
            rec["display_latency_p50_ms"] = round(lat["p50"] / 1e6, 3) if lat.get("p50") else None
            sd = ipj.get("sc_real_sched_delay_ns") or {}
            # 子图层后端: 真实帧被排到「present 调用之后多久」上屏 (提前量 + 合成帧占的半个周期), 是插帧加的延迟的下限
            rec["sc_added_delay_p50_ms"] = round(sd["p50"] / 1e6, 2) if sd.get("p50") else None
        if not rec["layer_loaded"]:
            rec["error"] = "层没加载 (marker layer_loaded 不为真)"
    finally:
        if rec.get("recovery_unknown") or capture_recovery_pending(out):
            # The native owner retains its inherited lease and bounded lifecycle.
            # Do not stop, terminate, or restore over an unconfirmed capture worker.
            rec.update(recovery_unknown=True, session_exit=proc.poll(), session_stop="deferred",
                       layer_removed=False)
            note(f"{name}: capture recovery unknown; leaving the native session owner intact")
        else:
            if proc.poll() is None:
                st = session_action(a.phonefarm, sdir, "stop")
            try:
                proc.wait(timeout=300)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=60)
            rec["session_exit"] = proc.returncode
            rec["session_stop"] = (st or {}).get("status")
            dev.shell(f"am force-stop {a.pkg}")
            ok, info = layer_absent(dev, a.pkg)
            rec["layer_removed"] = ok
            rec["layer_removed_info"] = info
            note(f"{name}: 会话退出 {proc.returncode}, 层已摘除 {ok} {info}")
    return rec


def make_locked(a, out, orig_mode):
    """在整轮设备租约下调本脚本 _locked (prep/plain/restore)。"""
    def locked(what, name, k_str):
        cmd = [sys.executable, "-I", os.path.abspath(__file__), "_locked",
               "--what", what, "--name", name, "--kind", k_str,
               "--orig-mode", orig_mode, "--serial", a.serial, "--pkg", a.pkg, "--out", out,
               "--settle", str(a.settle), "--window", str(a.window), "--phonefarm", a.phonefarm,
               "--vlm-calls", str(a.vlm_calls)]
        cmd += ["--power-profile", getattr(a, "power_profile", "disabled")]
        if a.verify_game_world:
            cmd += ["--verify-game-world"]
        if getattr(a, "hold", 0) > 0:   # hold 探测: 无层臂的 arm_plain 在 _locked 子进程里跑, 要把 --hold 带过去
            cmd += ["--hold", str(a.hold)]
        if getattr(a, "perfetto", False):
            cmd += ["--perfetto"]
        if a.power:
            cmd += ["--power"]
        if a.pan:
            cmd += ["--pan"]
        if getattr(a, "video", 0):
            cmd += ["--video", str(a.video)]
        if a.enter_script:
            cmd += ["--enter-script", a.enter_script]
        # hold 探测的 plain 臂在子进程里跑 hold_probe: 时限要罩住 hold+进世界+收尾 (56f5676318a5 教训:
        # 硬编码 1800 在 hold=1800 时必超时, SIGTERM 杀掉后 arms.jsonl 缺该臂整轮作废)
        to = 1800 if getattr(a, "hold", 0) <= 0 else int(a.hold * 1.5) + 600
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=to, pass_fds=lock_fds())
        line = next((l for l in r.stdout.splitlines() if l.startswith("ARM_JSON ")), None)
        return json.loads(line[9:]) if line else {"error": f"{what} exit {r.returncode}: {r.stderr[-400:]}"}
    return locked


def cmd_hold(a, out):
    """hold: 逐阶段复现「设好的 120 Hz 被系统压回 60」, 每秒采样 (hold_probe) 找触发条件/对抗手段, 不进统计。
    阶段 --phases 分号分隔: <臂>|panel=120|tap=<秒>|reassert=1|hold=<秒>; 臂写法同 --a/--b。"""
    log = open(os.path.join(out, "run.log"), "a")

    def note(msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    phases = []
    for ph in (a.phases or "").split(";"):
        ph = ph.strip()
        if not ph:
            continue
        parts = ph.split("|")
        spec, opts = parts[0], dict(kv.split("=", 1) for kv in parts[1:] if "=" in kv)
        try:
            parse_kind(spec)
        except argparse.ArgumentTypeError as e:
            note(f"阶段写法不认识: {e}")
            return 2
        phases.append((spec, opts))
    if not phases:
        note("没有可用阶段 (--phases)")
        return 2

    dev = Dev(a.serial)
    # 开跑前清后台 App: am kill-all 只杀后台进程, 不动前台/系统 (2026-11-03 红魔 91253241019A 实测 exit 0、前台焦点不变)。
    # 防上一轮遗留的 App 在后台抢 CPU/GPU/内存, 拉偏两臂功耗与帧率 (事故: megacity 后台残留覆盖了他人基线窗口)。
    dev.shell("am kill-all")
    note("am kill-all: 已清后台 App")
    ok, info = layer_absent(dev, a.pkg)
    if not ok:
        note(f"开跑前设备上已有层残留, 拒绝开始: {info}")
        return 2
    orig_mode = current_mode(dev, a.pkg)
    orig_cfg = dev.shell(f"cmd game list-configs {a.pkg}")
    orig_sr = dev.prop(SR_PROP)
    orig_timeout = dev.shell("settings get system screen_off_timeout").strip()
    json.dump({**{key: dev.shell(f"settings get system {key}").strip() for key in PANEL_KEYS}, FP_PROP: dev.prop(FP_PROP)},
              open(PANEL_ORIG["path"], "w"))
    json.dump({"game_mode": orig_mode, "game_configs": orig_cfg, "selfsr_prop": orig_sr,
               "game_gfrc_mode": dev.shell("settings get global game_gfrc_mode").strip(),
               "screen_off_timeout": orig_timeout}, open(os.path.join(out, "orig.json"), "w"), ensure_ascii=False)
    if "no intervention" not in orig_cfg.lower() and "downscale" in orig_cfg.lower():
        note(f"开跑前本包已有 Game Mode 干预, 拒绝开始 (不覆盖别人的设置): {orig_cfg[-200:]}")
        return 2
    locked = make_locked(a, out, orig_mode)
    FAN = "/sys/kernel/fan"
    fan_orig = None
    if a.fan:
        fo = dev.su(f"cat {FAN}/fan_enable {FAN}/fan_speed_level 2>/dev/null").split()
        if len(fo) == 2:
            fan_orig = ("redmagic", fo[0], fo[1])
            dev.su(f"echo 1 > {FAN}/fan_enable; echo 5 > {FAN}/fan_speed_level")
            note(f"风扇 (红魔): 原值 enable={fo[0]} level={fo[1]}, 本轮开到 1/5")
    dev.shell("settings put system screen_off_timeout 1800000")
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    note(f"hold 探测 {len(phases)} 阶段 (每阶段冷启动进大世界, 设好刷新率后每秒采样): {[p[0] for p in phases]}")
    arms = []
    try:
        for seq, (spec, opts) in enumerate(phases):
            if stop["flag"]:
                note("收到中止信号, 停在这里")
                break
            k = parse_kind(spec)
            name = f"P{seq + 1}"
            a.hold = int(opts.get("hold", "120"))
            os.environ["HOLD_TAP"] = str(opts.get("tap", "0"))
            os.environ["HOLD_REASSERT"] = "1" if opts.get("reassert") == "1" else "0"
            if opts.get("panel"):
                os.environ["HOLD_PANEL"] = str(opts["panel"])
            else:
                os.environ.pop("HOLD_PANEL", None)
            if opts.get("warm"):
                os.environ["HOLD_WARM"] = str(opts["warm"])
            else:
                os.environ.pop("HOLD_WARM", None)
            if opts.get("seen"):   # 每 N 秒抽 2s 子图层 latency (双峰中途复发检测; 无子图层的臂不传)
                os.environ["HOLD_SEEN"] = str(opts["seen"])
            else:
                os.environ.pop("HOLD_SEEN", None)
            if opts.get("cool"):   # 阶段前等电池降到 N °C (制造「即将向上穿越 43°C」的条件; 最多 20 分钟)
                cm = float(opts["cool"])
                dev.shell(f"am force-stop {a.pkg}; input keyevent KEYCODE_SLEEP")
                waited = 0
                for _ in range(120):
                    t_c = batt_temp_c(dev)
                    if t_c is None or t_c <= cm:
                        break
                    time.sleep(10)
                    waited += 10
                dev.shell("input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard")
                note(f"{name}: 等降温 {waited}s (电池到 {batt_temp_c(dev)}°C, 目标 ≤ {cm}°C)")
            dev.shell("input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard")
            note(f"{name}: {spec} hold={a.hold}s tap={os.environ['HOLD_TAP']}s reassert={os.environ['HOLD_REASSERT']}"
                 + (f" panel={os.environ['HOLD_PANEL']}" if os.environ.get("HOLD_PANEL") else ""))
            if uses_layer(k):
                rec = arm_layer(a, out, name, k, note, lambda w, n, _k, _s=spec: locked(w, n, _s))
            else:
                rec = locked("plain", name, spec)
            rec = {"arm": name, "kind": spec, "seq": seq + 1, "opts": opts, **rec, "t": time.time()}
            arms.append(rec)
            open(os.path.join(out, "arms.jsonl"), "a").write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec.get("error"):
                note(f"{name} ({spec}): 作废 — {rec['error']}")
            else:
                note(f"{name} ({spec}): hold {rec.get('hold_s')}s 掉落@{rec.get('hold_drops_at_s')} "
                     f"重设 {rec.get('hold_reasserts')} 次, 结束 {rec.get('hold_end_hz')}Hz ctrl={rec.get('hold_end_ctrl')}")
            if rec.get("recovery_unknown") or capture_recovery_pending(out):
                note(f"{name}: capture recovery unknown; stopping before the next phase")
                break
            if uses_layer(k) and not rec.get("layer_removed", False):
                note(f"{name}: 层没摘干净, 中止 (先人工核对再继续)")
                break
    finally:
        if capture_recovery_pending(out) or any(rec.get("recovery_unknown") for rec in arms):
            # Only the native owner may finish its automatic recovery here.
            json.dump({"recovery_unknown": True, "restoration_deferred": True},
                      open(os.path.join(out, "restore.json"), "w"))
            note("Capture recovery unconfirmed; outer restoration deferred")
        else:
            r = locked("restore", "end", "native")
            if fan_orig:
                dev.su(f"echo {fan_orig[1]} > {FAN}/fan_enable; echo {fan_orig[2]} > {FAN}/fan_speed_level")
                r["fan_restored"] = dev.su(f"cat {FAN}/fan_enable {FAN}/fan_speed_level").split()
            dev.shell(f"settings put system screen_off_timeout {orig_timeout}")
            ok, info = layer_absent(dev, a.pkg)
            json.dump({**r, "layer_absent": ok, "layer_info": info}, open(os.path.join(out, "restore.json"), "w"), ensure_ascii=False)
            note(f"收尾核对: Game Mode {r.get('mode_now')} (原 {orig_mode}), selfsr 属性 '{r.get('selfsr_prop')}', "
                 f"层已不在 {ok}, screen_off_timeout 还原为 {orig_timeout}")
    json.dump({"phases": arms}, open(os.path.join(out, "hold_summary.json"), "w"), ensure_ascii=False, indent=1)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "hold", "_locked"])
    ap.add_argument("--serial", required=True)
    ap.add_argument("--pkg", default=PKG_DEFAULT)
    ap.add_argument("--out", required=True)
    ap.add_argument("--a", type=parse_kind, default=parse_kind("native"))
    ap.add_argument("--b", type=parse_kind, default=parse_kind("layer"))
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--so", default=None, help="层 .so (挂层臂); 缺省用 gray-layer session 自己的")
    ap.add_argument("--so-a", default=None, help="Layer library for A; falls back to --so")
    ap.add_argument("--so-b", default=None, help="Layer library for B; falls back to --so")
    ap.add_argument("--enter-script", default=None,
                    help="onboard 认不出进了游戏时用固定脚本进门: 's:<秒>;t:<x>,<y>;…' (横屏比例坐标), 冷启动由脚本自己做")
    ap.add_argument("--vlm-calls", type=int, default=0,
                    help="Maximum onboard VLM calls (default: 0, disabled)")
    ap.add_argument("--perfetto", action="store_true", help="Capture and analyze the existing marked A/B window")
    ap.add_argument("--power-profile", choices=("disabled", "linux_micro_negative", "oplus_milli_positive"),
                    default="disabled", help="Explicit battery units/sign profile for read-only Mali sampling")
    ap.add_argument("--verify-game-world", action="store_true",
                    help="Require capture --ready-only after entry; always enabled for --enter-script")
    ap.add_argument("--keep-layer-prop", action="store_true",
                    help="整个会话保留 debug.vulkan.layers (Unity 先建一个探测用 VkInstance、再建真正的那种游戏需要; 原神不需要)")
    ap.add_argument("--settle", type=float, default=20)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--hold", type=int, default=0,
                    help="探测模式: 每臂不开测量窗口, 改为每秒采样刷新率 N 秒, 查「120 Hz 窗口内被压回 60」的触发 (hold_probe)")
    ap.add_argument("--phases", default=None,
                    help="hold 模式的阶段列表, 分号分隔: <臂>|panel=120|tap=<秒>|reassert=1|hold=<秒>; 臂写法同 --a/--b")
    ap.add_argument("--session-seconds", type=int, default=1200)
    ap.add_argument("--cool-max", type=float, default=0, help="每臂开跑前电池温度要降到这个 °C 以下 (0 = 不等), 最多等 15 分钟")
    ap.add_argument("--fan", action="store_true", help="整轮开机身风扇最高档 (红魔 /sys/kernel/fan), 收尾写回原值; 没有该节点的机器跳过")
    ap.add_argument("--pan", action="store_true", help="窗口后录屏 + 拖视角, 量 HUD 重影 (原神右下技能区)")
    ap.add_argument("--video", type=int, default=0, help="每臂测量窗口后录屏 N 秒到 traces/<臂>_video.mp4 (画质/视频对比素材, 不进指标; 0=不录)")
    ap.add_argument("--sf-atrace", action="store_true", help="窗口后抓 3 s SF atrace, 每秒数 waitframe 等 fence / 整帧跳过 / 缓冲被顶掉 (要 root)")
    ap.add_argument("--power", action="store_true", help="测量窗口内停充采整机功耗 (高通机 + root, 见 selfsr/power_sample.sh)")
    ap.add_argument("--phonefarm", default=os.environ.get("PHONEFARM_BIN") or os.path.join(REPO, "phonefarm"))
    # _locked 内部用
    ap.add_argument("--what", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--kind", default=None)
    ap.add_argument("--orig-mode", default="standard")
    a = ap.parse_args()
    if a.vlm_calls < 0:
        ap.error("--vlm-calls must be nonnegative")
    if not lock_fds():
        # Own one scope from the first read through the final restoration.
        os.execvp(a.phonefarm, [a.phonefarm, "lease", "--serial", a.serial,
                              "--label", f"{LOCK_PREFIX}selfsr@{a.serial}", "--",
                              sys.executable, "-I", os.path.abspath(__file__), *sys.argv[1:]])
    out = os.path.abspath(a.out)
    PANEL_ORIG["path"] = os.path.join(out, "panel_orig.json")
    for d in ("shots", "traces", "sessions", "onboard"):
        os.makedirs(os.path.join(out, d), exist_ok=True)

    if a.cmd == "_locked":
        k = parse_kind(a.kind)
        if a.what == "plain":
            rec = arm_plain(a, out, a.name, k, a.orig_mode)
        elif a.what == "prep":
            rec = {"prep": prepare(a, k, a.orig_mode)}
            if not rec["prep"]["game_mode"]["ok"]:
                rec["error"] = f"Game Mode 没设上: {rec['prep']['game_mode']}"
        else:  # restore
            dev = Dev(a.serial)
            dev.shell(f"am force-stop {a.pkg}")
            gm = game_mode(dev, a.pkg, None, a.orig_mode)
            dev.shell(f"setprop {SR_PROP} ''")
            dev.shell("; ".join(f"setprop debug.knobs.selfsr_{o} ''" for o in SR_OPTS if o != "unlimit"))
            dev.shell(f"setprop {UNLIMIT_PROP} ''")
            try:
                og = json.load(open(os.path.join(out, "orig.json"))).get("game_gfrc_mode")
            except (OSError, ValueError):
                og = None
            if og not in (None, "", "null"):
                dev.shell(f"settings put global game_gfrc_mode {shlex.quote(og)}")
            dev.shell("; ".join(f"setprop {p} ''" for p in IP_PROPS))
            dev.shell("; ".join(f"setprop debug.knobs.interp_{t} ''" for t in IP_TUNE if t not in ("panel", "framepacing", "unlimit", "sfovr", "sfwait")))
            restore_panel(dev)
            dev.su(f"[ -w {CHG_NODE} ] && echo 1 > {CHG_NODE}")   # 功耗采样被打断时也要恢复充电
            rec = {"game_mode": gm, "mode_now": current_mode(dev, a.pkg), "selfsr_prop": dev.prop(SR_PROP),
                   "game_gfrc_mode": dev.shell("settings get global game_gfrc_mode").strip(),
                   "interp_props": [dev.prop(p) for p in IP_PROPS]}
        print("ARM_JSON " + json.dumps(rec, ensure_ascii=False))
        return 0

    if a.cmd == "hold":
        return cmd_hold(a, out)

    log = open(os.path.join(out, "run.log"), "a")

    def note(msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    raw = sys.argv
    ka = raw[raw.index("--a") + 1] if "--a" in raw else "native"
    kb = raw[raw.index("--b") + 1] if "--b" in raw else "layer"

    dev = Dev(a.serial)
    # 开跑前清后台 App: am kill-all 只杀后台进程, 不动前台/系统 (2026-11-03 红魔 91253241019A 实测 exit 0、前台焦点不变)。
    # 防上一轮遗留的 App 在后台抢 CPU/GPU/内存, 拉偏两臂功耗与帧率 (事故: megacity 后台残留覆盖了他人基线窗口)。
    dev.shell("am kill-all")
    note("am kill-all: 已清后台 App")
    ok, info = layer_absent(dev, a.pkg)
    if not ok:
        note(f"开跑前设备上已有层残留, 拒绝开始: {info}")
        return 2
    orig_mode = current_mode(dev, a.pkg)
    orig_cfg = dev.shell(f"cmd game list-configs {a.pkg}")
    orig_sr = dev.prop(SR_PROP)
    orig_timeout = dev.shell("settings get system screen_off_timeout").strip()
    json.dump({**{key: dev.shell(f"settings get system {key}").strip() for key in PANEL_KEYS}, FP_PROP: dev.prop(FP_PROP)},
              open(PANEL_ORIG["path"], "w"))
    json.dump({"game_mode": orig_mode, "game_configs": orig_cfg, "selfsr_prop": orig_sr,
               "game_gfrc_mode": dev.shell("settings get global game_gfrc_mode").strip(),
               "screen_off_timeout": orig_timeout}, open(os.path.join(out, "orig.json"), "w"), ensure_ascii=False)
    locked = make_locked(a, out, orig_mode)
    if "no intervention" not in orig_cfg.lower() and "downscale" in orig_cfg.lower():
        note(f"开跑前本包已有 Game Mode 干预, 拒绝开始 (不覆盖别人的设置): {orig_cfg[-200:]}")
        return 2
    # 机身风扇: 红魔 /sys/kernel/fan (enable + 档位 1..5); 荣耀 hn_thermal/fan (目标转速, 0 = 关, 最高 fan_max_rpm)
    FAN = "/sys/kernel/fan"
    HFAN = "/sys/devices/virtual/hn_thermal/fan"
    fan_orig = None   # ("redmagic", enable, level) | ("honor", target_rpm)
    if a.fan:
        fo = dev.su(f"cat {FAN}/fan_enable {FAN}/fan_speed_level 2>/dev/null").split()
        ho = dev.su(f"cat {HFAN}/fan_target_rpm {HFAN}/fan_max_rpm 2>/dev/null").split()
        if len(fo) == 2:
            fan_orig = ("redmagic", fo[0], fo[1])
            dev.su(f"echo 1 > {FAN}/fan_enable; echo 5 > {FAN}/fan_speed_level")
            note(f"风扇 (红魔): 原值 enable={fo[0]} level={fo[1]}, 本轮开到 1/5")
        elif len(ho) == 2:
            fan_orig = ("honor", ho[0])
            dev.su(f"echo {ho[1]} > {HFAN}/fan_target_rpm")
            note(f"风扇 (荣耀): 原目标转速 {ho[0]}, 本轮 {ho[1]}")
        else:
            note("风扇: 本机没有已知的风扇节点, 跳过")
    dev.shell("settings put system screen_off_timeout 1800000")
    dev.adb(f"push {shlex.quote(os.path.join(REPO, 'loop_v1/tools/ftrace_capture.sh'))} {DEV_TRACE}", check=True)
    bright_orig = None
    if a.power:
        if dev.su(f"[ -w {CHG_NODE} ] && echo yes").strip() != "yes":
            note(f"--power: 这台机器没有可写的 {CHG_NODE}, 量不了整机功耗")
            return 2
        dev.adb(f"push {shlex.quote(os.path.join(HERE, 'power_sample.sh'))} {DEV_POWER}", check=True)
        # 自动亮度会随环境光变, 两臂功耗就不可比: 整轮固定为手动, 亮度值不动
        bright_orig = (dev.shell("settings get system screen_brightness_mode").strip(),
                       dev.shell("settings get system screen_brightness").strip())
        dev.shell("settings put system screen_brightness_mode 0")
        note(f"功耗: 窗口内停充采样; 亮度固定手动 {bright_orig[1]} (原模式 {bright_orig[0]})")
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    note(f"A={ka} B={kb} rounds={a.rounds} 原 Game Mode={orig_mode}")

    order = []
    for i in range(a.rounds):
        order += [("A", i), ("B", i)] if i % 2 == 0 else [("B", i), ("A", i)]
    arms = []
    ref_shot = None
    shared_so = a.so
    try:
        for seq, (arm, pair) in enumerate(order):
            if stop["flag"]:
                note("收到中止信号, 停在这里")
                break
            k, k_str = (a.a, ka) if arm == "A" else (a.b, kb)
            name = f"{arm}{pair + 1}"
            t_arm_start = time.time()   # 重掷耗时统计 (REROLL) 的臂起点
            cool_wait = 0
            if a.cool_max > 0:   # 等电池降温 (过热时系统高温保护会弹窗挡前台、限制应用运行)
                dev.shell(f"am force-stop {a.pkg}; input keyevent KEYCODE_SLEEP")
                for _ in range(90):
                    try:
                        t_c = int(re.search(r"temperature: (\d+)", dev.shell("dumpsys battery")).group(1)) / 10
                    except (AttributeError, ValueError):
                        break
                    if t_c <= a.cool_max:
                        break
                    time.sleep(10)
                    cool_wait += 10
                if cool_wait:
                    note(f"{name}: 等降温 {cool_wait}s (电池 ≤ {a.cool_max}°C)")
            dev.shell("input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard")   # 无密码锁屏的机器 (荣耀) 也要解锁
            if uses_layer(k):
                # Resolve each side from the original fallback, never the previous arm.
                a.so = (a.so_a if arm == "A" else a.so_b) or shared_so
                # REROLL=1 的重掷循环: arm_layer 窗口前探测到症状档 (rec["reroll"]) 就丢弃本次冷启动重跑整臂,
                # 尝试名加 #rN 后缀 (sessions/traces 分文件), arms.jsonl 只写最后一次 (原名, 配对名不变)。
                dropped, attempt = [], 0
                while True:
                    aname = name if attempt == 0 else f"{name}#r{attempt}"
                    # 预算内最后一次尝试: REROLL_LAST=1 让 arm_layer 症状也照常开窗口 (完整指标), 不再提前返回
                    os.environ["REROLL_LAST"] = "1" if attempt >= int(os.environ.get("REROLL_MAX") or 3) else "0"
                    rec = arm_layer(a, out, aname, k, note, lambda w, n, _k: locked(w, n, k_str))
                    attempt += 1
                    if rec.get("recovery_unknown") or not (rec.get("reroll") and attempt <= int(os.environ.get("REROLL_MAX") or 3)):
                        rec.pop("reroll", None)
                        break
                    dropped.append({"attempt": attempt, "took_s": round(time.time() - t_arm_start, 1),
                                    **(rec.get("reroll_probe") or {})})
                    note(f"{aname}: 呈现症状档 (探测 {rec.get('reroll_probe')}), 丢弃重掷 (第 {attempt} 次)")
                os.environ.pop("REROLL_LAST", None)
                if dropped:
                    rec["reroll_dropped"] = dropped
                    rec["reroll_attempts"] = attempt
            else:
                rec = locked("plain", name, k_str)
            rec = {"arm": name, "side": arm, "kind": k_str, "pair": pair + 1, "seq": seq + 1, **rec, "cool_wait_s": cool_wait,
                   "t": time.time()}
            if a.power and not rec.get("error"):
                if rec.get("power_charging_seen"):
                    rec["error"] = "功耗: 窗口内电池在充电 (停充没生效)"
                elif not rec.get("power_samples"):
                    rec["error"] = "功耗: 没采到样本"
            if rec.get("shot") and os.path.exists(rec["shot"]):
                if k["kind"] == "native" and ref_shot is None and not rec.get("error"):
                    ref_shot = rec["shot"]
                rec["ssim_vs_native_ref"] = ssim(ref_shot, rec["shot"]) if ref_shot else None
            arms.append(rec)
            open(os.path.join(out, "arms.jsonl"), "a").write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec.get("error"):
                note(f"{name} ({k_str}): 作废 — {rec['error']}")
            else:
                timing = (f"GPU UID active {rec.get('gpu_uid_active_pct')}% "
                          f"presentation P95 [{rec.get('display_frame_p95_lower_ms')}, {rec.get('display_frame_p95_upper_ms')}) ms "
                          if rec.get("mali_telemetry") is not None else
                          f"游戏 {rec.get('game_fps')} fps p95 {rec.get('game_frame_p95_ms')} ms ")
                note(f"{name} ({k_str}): 显示 {rec.get('display_fps')} fps @{rec.get('display_hz')}Hz "
                     + timing + f"锐度 {rec.get('sharp_full', 0):.1f} 输出 {rec.get('out_res')}"
                     + (f" 功耗 {rec.get('power_mw')} mW" if rec.get('power_mw') is not None else "")
                     + (f" 层帧数 {rec.get('layer_frames_in_window')}" if uses_layer(k) else "")
                     + (f" 窗口上屏 {rec.get('interp_window')}" if rec.get('interp_window') else "")
                     + (f" 超分 {rec.get('selfsr_upscaled_in_window')} 次 {(rec.get('selfsr') or {}).get('game_extent')}→{(rec.get('selfsr') or {}).get('swap_extent')}" if k['kind'] in ('sr', 'srip') else ""))
            if rec.get("recovery_unknown") or capture_recovery_pending(out):
                note(f"{name}: capture recovery is unknown; stopping before the next arm")
                break
            if uses_layer(k) and not rec.get("layer_removed", False):
                note(f"{name}: 层没摘干净, 中止整轮 (先人工核对再继续)")
                break
    finally:
        if capture_recovery_pending(out) or any(rec.get("recovery_unknown") for rec in arms):
            # Only the native owner may finish its automatic recovery here.
            json.dump({"recovery_unknown": True, "restoration_deferred": True},
                      open(os.path.join(out, "restore.json"), "w"))
            note("Capture recovery unconfirmed; outer restoration deferred")
        else:
            r = locked("restore", "end", "native")
            if fan_orig and fan_orig[0] == "redmagic":
                dev.su(f"echo {fan_orig[1]} > {FAN}/fan_enable; echo {fan_orig[2]} > {FAN}/fan_speed_level")
                r["fan_restored"] = dev.su(f"cat {FAN}/fan_enable {FAN}/fan_speed_level").split()
            elif fan_orig and fan_orig[0] == "honor":
                dev.su(f"echo {fan_orig[1]} > {HFAN}/fan_target_rpm")
                r["fan_restored"] = dev.su(f"cat {HFAN}/fan_target_rpm").split()
            dev.shell(f"settings put system screen_off_timeout {orig_timeout}")
            if a.power:
                dev.su(f"echo 1 > {CHG_NODE}")
                r["charging_enabled"] = dev.su(f"cat {CHG_NODE}").strip()
                if bright_orig and bright_orig[0] in ("0", "1"):
                    dev.shell(f"settings put system screen_brightness_mode {bright_orig[0]}")
            ok, info = layer_absent(dev, a.pkg)
            json.dump({**r, "layer_absent": ok, "layer_info": info}, open(os.path.join(out, "restore.json"), "w"), ensure_ascii=False)
            note(f"收尾核对: Game Mode {r.get('mode_now')} (原 {orig_mode}), selfsr 属性 '{r.get('selfsr_prop')}', "
                 f"层已不在 {ok}, screen_off_timeout 还原为 {orig_timeout}")

    good = [r for r in arms if not r.get("error") and r.get("in_game")
            and (not uses_layer(parse_kind(r["kind"])) or (r.get("layer_loaded") and r.get("layer_removed")))]
    pairs = sorted({r["pair"] for r in good if sum(1 for x in good if x["pair"] == r["pair"]) == 2})
    metrics = ["seen_fps", "display_fps", "display_fps_sc", "sc_added_delay_p50_ms", "sc_interval_p95_ms", "game_fps", "game_frame_p95_ms", "gpu_active_ms", "sharp_full", "sharp_hud", "ssim_vs_native_ref", "missed_frames",
               "display_latency_p50_ms", "displayed_per_s", "power_mw", "mj_per_seen_frame", "bat_temp_c", "hud_flicker", "pan_bg_motion", "pan_rec_fps",
               "gpu_uid_active_total_ms", "gpu_uid_active_pct", "gpu_device_utilization_pct", "gpu_device_frequency_mhz",
               "battery_temperature_c", "battery_supply_vi_product_mw", "display_frame_p95_lower_ms", "display_frame_p95_upper_ms", "display_dropped_frames"]
    for r in good:   # 每个看到的帧的能耗: 插帧把帧率翻倍时, 这个比总功耗更能说明效率
        r["mj_per_seen_frame"] = round(r["power_mw"] / r["seen_fps"], 3) if r.get("power_mw") and r.get("seen_fps") else None
    res = {}
    for m in metrics:
        av = [next(x.get(m) for x in good if x["pair"] == p and x["side"] == "A") for p in pairs]
        bv = [next(x.get(m) for x in good if x["pair"] == p and x["side"] == "B") for p in pairs]
        if not pairs or any(v is None for v in av + bv):
            res[m] = {"status": "not_measured"}
            continue
        d = [y - x for x, y in zip(av, bv)]
        ma = statistics.mean(av)
        res[m] = {"a_mean": round(ma, 4), "b_mean": round(statistics.mean(bv), 4), "diff_mean": round(statistics.mean(d), 4),
                  "diff_pct": round(100 * statistics.mean(d) / ma, 2) if ma else None,
                  "ci95": [round(x, 4) for x in boot_ci(d)] if len(d) > 1 else None, "p": signflip(d), "n_pairs": len(d)}
    report = {"pkg": a.pkg, "a": ka, "b": kb, "rounds": a.rounds, "valid_pairs": len(pairs),
              "dropped_arms": [r["arm"] for r in arms if r not in good],
              "out_res": {side: sorted({tuple(r["out_res"]) for r in good if r["side"] == side and r.get("out_res")}) for side in "AB"},
              "metrics": res, "min_p_possible": 2 / 2 ** len(pairs) if pairs else None}
    json.dump(report, open(os.path.join(out, "report.json"), "w"), ensure_ascii=False, indent=1)
    names = {"seen_fps": "实际上屏帧率 (看到的画面: 有子图层取子图层)", "display_fps": "游戏图层上屏帧率", "display_fps_sc": "子图层上屏帧率 (插帧 sc 后端)", "sc_added_delay_p50_ms": "插帧给真实帧加的排队延迟 p50 (ms)", "sc_interval_p95_ms": "子图层上屏间隔 p95 (ms)", "game_fps": "游戏渲染帧率 (GPU 提交)", "game_frame_p95_ms": "游戏渲染 p95 帧时 (ms)",
             "gpu_active_ms": "每帧 GPU 忙碌 (ms, 均值)",
             "display_latency_p50_ms": "真实帧 present→上屏 p50 (ms)",
             "displayed_per_s": "层回读: 窗口内每秒实际上屏 (真实+合成)",
             "sharp_full": "锐度 (拉普拉斯方差)", "sharp_hud": "右上角固定图标区锐度", "ssim_vs_native_ref": "与第一张原生截图的 SSIM", "missed_frames": "SF 丢帧 / 窗口",
             "power_mw": "整机功耗 (mW, 停充电池放电)", "mj_per_seen_frame": "每个看到的帧的能耗 (mJ)", "bat_temp_c": "窗口内电池温度 (°C)",
             "hud_flicker": "转视角时 HUD 区相邻帧平均差 (越小重影越轻)", "pan_bg_motion": "转视角时画面中央相邻帧平均差 (运动量)",
             "pan_rec_fps": "录屏帧率"}
    names.update({
        "gpu_uid_active_total_ms": "GPU active time for the application UID (ms)",
        "gpu_uid_active_pct": "Application GPU active fraction of the window (%)",
        "gpu_device_utilization_pct": "Whole-device GPU utilization (%)",
        "gpu_device_frequency_mhz": "GPU frequency (MHz)",
        "battery_temperature_c": "Battery temperature (C)",
        "battery_supply_vi_product_mw": "Reported battery supply voltage-current product (mW; pack scope unconfirmed)",
        "display_frame_p95_lower_ms": "Application presentation P95 lower bound (ms)",
        "display_frame_p95_upper_ms": "Application presentation P95 upper bound (ms)",
        "display_dropped_frames": "Application layer dropped frames",
    })
    md = [f"# {ka} (A) vs {kb} (B): {a.pkg}", "",
          f"每臂冷启动进大世界, 有效 {len(pairs)} 对, 精确符号翻转检验 (最小可能 p = {report['min_p_possible']})。"
          f"游戏内设置未改。作废臂: {report['dropped_arms'] or '无'}。送显缓冲: {report['out_res']}", "",
          "| 指标 | A | B | 差 (B-A) | 差% | 95% 区间 | p |", "|---|---|---|---|---|---|---|"]
    for m in metrics:
        r = res[m]
        if r.get("status"):
            md.append(f"| {names[m]} | - | - | - | - | - | 未测成 |")
            continue
        md.append(f"| {names[m]} | {r['a_mean']} | {r['b_mean']} | {r['diff_mean']:+} | {r['diff_pct']}% | {r['ci95']} | {r['p']:.4f} |")
    open(os.path.join(out, "report.md"), "w").write("\n".join(md) + "\n")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
