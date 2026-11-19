#!/usr/bin/env python3
"""vks_report.py — Vulkan-Samples 两臂对照的判读面 (纯函数链, 不碰设备)

只做一件事: 把 ../tools/analyze.py 已经算好的统计量, 对上**这个开关在上游源码里
到底改了什么**, 然后如实说"测出来了 / 没测出来", 不做二次统计。

KNOBS 是从上游样例构造函数里逐行读出来的映射, 连同本文件的 sha256 一起写进报告 ——
事后改了判读口径, 哈希对不上 (和 refbench_report.py 的纪律一致)。

用法:
  python3 vks_report.py --root <A/B 输出目录> --sample render_passes --config-a 0 --config-b 1
"""
from __future__ import annotations
import argparse
import glob
import hashlib
import json
import os
import re
import sys

# ── 开关语义: 逐条来自 Vulkan-Samples 上游源码, 不是推测 ──
# 每项: (人话描述, 预期受影响的主指标, 预期方向 "up"/"down"/None)
# 方向是相对 config A → config B 的变化。None = 上游没给出明确预期, 只报数不判。
KNOBS: dict[str, dict[int, str]] = {
    # samples/performance/render_passes/render_passes.cpp 构造函数
    "render_passes": {
        0: "颜色附件 loadOp=LOAD + 深度附件 storeOp=STORE, 不用 vkCmdClearAttachments",
        1: "颜色附件 loadOp=CLEAR + 深度附件 storeOp=DONT_CARE, 用 vkCmdClearAttachments",
    },
    # samples/performance/subpasses/subpasses.cpp 构造函数
    "subpasses": {
        0: "subpass 合并: G-buffer 留在 tile memory (上游注释 Good settings)",
        1: "两个独立 render pass: G-buffer 走 DRAM 往返",
        2: "关掉 transient attachments: G-buffer 附件变成真实显存分配",
        3: "加大 G-buffer 格式精度",
    },
    # samples/performance/msaa/msaa.cpp 构造函数
    "msaa": {
        0: "单 render pass, MSAA 在 tile 内 resolve",
        1: "开后处理 → 两个 render pass, 走 writeback resolve",
    },
    # samples/performance/afbc/afbc.cpp 构造函数 (Arm AFBC; 在 Adreno 上对应 UBWC 被
    # VK_IMAGE_USAGE_STORAGE_BIT 顶掉, 所以这一项在本机的语义要靠实测说话, 不预判方向)
    "afbc": {
        0: "交换链 image 额外带 STORAGE usage → 压制帧缓冲压缩",
        1: "交换链 image 只带 COLOR_ATTACHMENT usage → 允许帧缓冲压缩",
    },
}

# 主指标与它在这套采集里的口径 (见 src/looptrace.rs 文件头)
METRIC_MEANING = {
    "frame_p95": "帧时间 p95 (ms), 提交节奏推出来的真实出帧",
    "frame_p50": "帧时间中位数 (ms)",
    "gpu_active_mean": "每帧 GPU 真正在跑命令的时间 (ms), 不含排队",
    "bw_median": "kgsl_buslevel 的 avg_bw 中位数, GPU 侧总线带宽投票 (直接观测量)",
}


def sha256_self() -> str:
    with open(os.path.realpath(__file__), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def load_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# Adreno 驱动发提交的那个线程叫 binder:<pid>_<槽位>: pid 每次启动都不一样, 槽位是
# binder 线程池里恰好轮到哪一个, 两者都不携带口径信息。比较时必须先归一化, 否则
# "每轮线程名都不同"会永远误报成"两臂口径不一致"。真正要拦的是**类别**变了 ——
# 比如从应用自己的提交线程变成了 kgsl_hwsched 或 SurfaceFlinger 的 RenderEngine。
BINDER_RE = re.compile(r"^binder:\d+_\d+$")


def normalize_comm(comm: str | None) -> str:
    if not comm:
        return "<none>"
    return "binder:<应用提交线程>" if BINDER_RE.match(comm) else comm


def collect_comms(root: str) -> dict:
    """每轮认出来的提交线程名(已抹掉 pid) —— 两臂必须是同一类线程, 否则对照不成立。"""
    out: dict[str, list] = {}
    for p in sorted(glob.glob(os.path.join(root, "*", "comm.json"))):
        d = load_json(p)
        out.setdefault(normalize_comm(d.get("comm")), []).append(
            (os.path.basename(os.path.dirname(p)), d.get("share")))
    return out


def collect_fans(root: str) -> dict:
    """每轮的风扇状态 —— 红魔的主动散热风扇自身耗电会进功耗读数, 且它不在 38 行快照里。
    同一组对照的两臂必须是同一风扇状态, 否则"风扇开/关"会混进臂间差异。"""
    out: dict[str, list[str]] = {}
    for p in sorted(glob.glob(os.path.join(root, "*", "fan.json"))):
        d = load_json(p)
        key = f"enable={d.get('fan_enable')} level={d.get('fan_speed_level')}"
        out.setdefault(key, []).append(os.path.basename(os.path.dirname(p)))
    return out


def collect_run_conditions(root: str) -> dict:
    """每轮的两个**会悄悄污染逐帧指标**的条件, 逐轮列出来而不是藏在 summary 里。

    · submits_per_frame: parse-trace 自检出的"每帧几次提交"。所有逐帧指标
      (frame_p50/p95、gpu_active_mean、fps_mean) 都是按它分组算出来的, 它一变,
      这些数的口径就变了。2026-09-23 实测同一个负载的 10 轮里它取过 1 和 6 ——
      取 6 的那轮 fps 被算成 9.9、frame_p50 算成 99.9ms, 其实应用一直稳在 60fps。
    · log_fps_median: 应用自报帧率。开 vsync 时它等于面板刷新率, 而本机面板是
      自适应刷新的, 实测同一批里出现过 60 / 99.5 / 120 三档 —— 帧预算不一样,
      逐帧指标自然没有可比性。
    带宽 bw_median 不经过这两层, 是 kgsl_buslevel 的直接观测量, 不受影响。
    """
    out = []
    for p in sorted(glob.glob(os.path.join(root, "*", "summary.json"))):
        d = os.path.dirname(p)
        s = load_json(p)
        cc_path = os.path.join(d, "crosscheck.json")
        cc = load_json(cc_path) if os.path.exists(cc_path) else {}
        out.append({
            "round": os.path.basename(d),
            "spf": s.get("submits_per_frame"),
            "log_fps": cc.get("log_fps_median"),
            "bw_median": s.get("bw_median"),
        })
    return {"rounds": out,
            "spf_set": sorted({r["spf"] for r in out if r["spf"] is not None}),
            "log_fps_set": sorted({round(r["log_fps"]) for r in out if r["log_fps"]})}


def collect_displays(root: str) -> dict:
    """每轮的面板刷新率设置 —— 开着 vsync 时应用帧率就是它, 各轮不一致则逐帧指标不可比。"""
    out: dict[str, list[str]] = {}
    for p in sorted(glob.glob(os.path.join(root, "*", "display.json"))):
        d = load_json(p)
        key = f"min={d.get('min_refresh_rate')} peak={d.get('peak_refresh_rate')}"
        out.setdefault(key, []).append(os.path.basename(os.path.dirname(p)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--sample", required=True)
    ap.add_argument("--config-a", type=int, required=True)
    ap.add_argument("--config-b", type=int, required=True)
    args = ap.parse_args()

    apath = os.path.join(args.root, "analyze.json")
    if not os.path.exists(apath):
        print(f"缺 {apath} —— 先跑 run_ab.sh", file=sys.stderr)
        return 2
    an = load_json(apath)

    knob = KNOBS.get(args.sample, {})
    print("═" * 78)
    print(f"Vulkan-Samples 两臂对照 · sample={args.sample}")
    print(f"  A 臂 (--config {args.config_a}): {knob.get(args.config_a, '<未登记>')}")
    print(f"  B 臂 (--config {args.config_b}): {knob.get(args.config_b, '<未登记>')}")
    print(f"  判读脚本 sha256: {sha256_self()}")
    print("═" * 78)

    comms = collect_comms(args.root)
    print(f"\n提交线程 (由 pick_comm.py 从 trace 里认出, 非写死):")
    for c, rounds in comms.items():
        shares = sorted(s for _, s in rounds if s is not None)
        print(f"  {c}: {len(rounds)} 轮, 占比 {min(shares):.3f}~{max(shares):.3f}" if shares
              else f"  {c}: {len(rounds)} 轮")
    if len(comms) > 1:
        print("  ⚠ 两臂不是同一个提交线程, 对照不成立, 下面的数不要用")

    fans = collect_fans(args.root)
    if fans:
        print(f"\n测试条件 · 主动散热风扇 (不在 38 行快照里, 单独存证):")
        for state, rounds in fans.items():
            print(f"  {state}: {len(rounds)} 轮")
        if len(fans) > 1:
            print("  ⚠ 各轮风扇状态不一致 —— 风扇自身耗电会进功耗读数, 这批数据不能跨状态比")

    disp = collect_displays(args.root)
    if disp:
        print(f"\n测试条件 · 面板刷新率 (开着 vsync 时它就是应用帧率):")
        for state, rounds in disp.items():
            print(f"  {state}: {len(rounds)} 轮")
        if len(disp) > 1:
            print("  ⚠ 各轮刷新率设置不一致 —— 帧预算不同, 逐帧指标不可比")

    cond = collect_run_conditions(args.root)
    if cond["rounds"]:
        print("\n逐轮条件 (这两项一变, 所有逐帧指标的口径就变了):")
        print("  " + "  ".join(f"{r['round']}:spf={r['spf']},app_fps={round(r['log_fps'], 1) if r['log_fps'] else None}" for r in cond["rounds"]))
        if len(cond["spf_set"]) > 1:
            print(f"  ⚠ submits_per_frame 在同一批里取了 {cond['spf_set']} —— 逐帧指标 "
                  f"(frame_p50/p95, gpu_active_mean, fps_mean) 是按它分组算的, 口径不一致, 不可比。")
        if len(cond["log_fps_set"]) > 1:
            print(f"  ⚠ 应用帧率在同一批里取了 {cond['log_fps_set']} —— 开着 vsync 时它就是面板刷新率, "
                  f"本机面板自适应刷新, 各轮帧预算不同, 逐帧指标没有可比性。")
        if len(cond["spf_set"]) > 1 or len(cond["log_fps_set"]) > 1:
            print("  → 这一批里只有 bw_median 可用: 它是 kgsl_buslevel 的直接观测量, 不经过分帧。")

    a, b = an.get("arm_a", {}), an.get("arm_b", {})
    print(f"\n样本: A={a.get('n_runs')} 轮  B={b.get('n_runs')} 轮")

    # 判据 1 的口径复用: 臂内离散度太大, 再显著的臂间差异也不可信
    print("\n臂内离散度 (max-min)/median:")
    for name, arm in (("A", a), ("B", b)):
        cells = []
        for m in ("frame_p95", "gpu_active_mean", "bw_median"):
            d = arm.get("metrics", {}).get(m, {})
            cells.append(f"{m}={d.get('dispersion_pct')}%")
        print(f"  {name}: " + "  ".join(cells))

    print("\n臂间对照 (精确置换检验, 全枚举, 无随机种子):")
    header = f"  {'指标':<18}{'A 均值':>12}{'B 均值':>12}{'差':>12}{'差%':>9}{'p':>10}  显著"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for m, cmp_ in (an.get("comparison") or {}).items():
        if "error" in cmp_:
            print(f"  {m:<18}{cmp_['error']}")
            continue
        sig = "是" if cmp_["significant_at_0.05"] else "否"
        print(f"  {m:<18}{cmp_['a_mean']:>12}{cmp_['b_mean']:>12}"
              f"{cmp_['diff_mean']:>12}{cmp_['diff_pct']:>9}{cmp_['perm_p_two_sided']:>10}  {sig}")

    print("\n指标口径:")
    for m, why in METRIC_MEANING.items():
        print(f"  {m}: {why}")

    print("\n结论口径: 本文件只判'这套采集有没有把开关的差异测出来', 不判'这个优化值不值得做'。")
    sig_metrics = [m for m, c in (an.get("comparison") or {}).items()
                   if "error" not in c and c["significant_at_0.05"]]
    if sig_metrics:
        print(f"  测出显著差异的指标: {', '.join(sig_metrics)}")
    else:
        print("  没有任何指标在 0.05 上显著 —— 这套采集在该开关上不灵敏, 或轮数不够。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
