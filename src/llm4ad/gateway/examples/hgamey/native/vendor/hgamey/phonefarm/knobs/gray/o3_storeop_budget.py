#!/usr/bin/env python3
"""o3_storeop_budget.py — 去掉原神 3 处多余写回 (storeOp STORE→DONT_CARE) 的预期效应与可检出性, 纯离线。

做三件事, 全部只读存档:
1. 从 frame-audit 证据 (knobs/evidence/10_frameaudit/analysis.json) 里取规则指定的 3 个附件
   的「确定白存」字节数 → 每帧省下的写回字节 (模型值, 未压缩上限)。
2. 按 帧率 × 每字节能耗 (默认 40–160 pJ/B, LPDDR5X 系统级文献区间, 不是实测) 推算省电。
   这是**推断**, 输出里标 inferred。
3. 用原神历史 A/B 的对照臂 (loop_v1/runs_sysparam/*/ctrl*/metrics.json) 估各指标臂间离散度,
   模拟尺子的判法 (两样本精确/随机置换检验, 双侧, 改善门槛 alpha/K) 算检出率, 给出
   各对数下 80% 检出的最小效应 (MDE80) 和要测出预期效应需要的对数。

用法:
  python3 -I knobs/gray/o3_storeop_budget.py [--repo <phonefarm 根>] [--fps 60] [--json 输出]
"""
import argparse
import glob
import itertools
import json
import math
import os
import random
import statistics as st
import sys

RULE = ["15a51a879df99c32:2", "9d0b4db80bf552b8:0", "9d0b4db80bf552b8:1"]
# 原神 sysparam 历史 A/B: 都是放电态、同一套 run_once 负载 (转视角), 10 臂一组
CTRL_GLOBS = [
    "loop_v1/runs_sysparam/genshin2_run1/gen1/cand1/ctrl*",
    "loop_v1/runs_sysparam/genshin2_run1/gen1/cand3/ctrl*",
    "loop_v1/runs_sysparam/genshin2_run2/gen1/cand1/ctrl*",
]
METRICS = ["power_w_mean", "bw_median", "frame_p95", "gpu_active_mean"]


def saved_bytes_per_frame(analysis_path, rule):
    """规则里每个 (sig, 附件) 在每个 (抓取, 帧) 里的确定白存字节; 返回每帧合计 (取各帧的中位)。"""
    want = {(r.split(":")[0], int(r.split(":")[1])) for r in rule}
    caps = json.load(open(analysis_path))
    per_frame = {}
    per_item = {}
    for cap in caps:
        for f in cap["findings"]:
            if f["rule"] != "dead_store" or not f.get("certain"):
                continue
            sig = f["sig"].split("sig=")[-1].strip()
            key = (sig, f["att"])
            if key not in want:
                continue
            fk = (cap["file"], f["frame"])
            per_frame[fk] = per_frame.get(fk, 0.0) + f["bytes"]
            per_item.setdefault(f"{sig}:{f['att']}", set()).add(f["bytes"])
    missing = [f"{s}:{a}" for s, a in want if f"{s}:{a}" not in per_item]
    if missing:
        raise SystemExit(f"frame-audit 证据里找不到这些确定白存: {missing}")
    totals = sorted(per_frame.values())
    return st.median(totals), {k: sorted(v) for k, v in per_item.items()}, len(totals)


def pooled_sd(repo, metric):
    """各组对照臂的组内方差合并 (组间水平差不算噪声: 一次评测内两臂交替, 只受组内离散影响)。"""
    groups = []
    for g in CTRL_GLOBS:
        xs = []
        for d in sorted(glob.glob(os.path.join(repo, g))):
            try:
                m = json.load(open(os.path.join(d, "metrics.json")))
                xs.append(float(m[metric]))
            except (OSError, KeyError, ValueError, TypeError):
                pass
        if len(xs) >= 2:
            groups.append(xs)
    if not groups:
        return None, []
    num = sum((len(x) - 1) * st.variance(x) for x in groups)
    den = sum(len(x) - 1 for x in groups)
    return math.sqrt(num / den), [round(st.stdev(x), 4) for x in groups]


def perm_p(a, b, splits):
    """两样本均值差的双侧置换 p (splits = 所有/随机的 A 组下标组合)。"""
    pooled = a + b
    n = len(a)
    obs = abs(sum(b) / n - sum(a) / n)
    tot = sum(pooled)
    hits = 0
    for idx in splits:
        sa = sum(pooled[i] for i in idx)
        d = abs((tot - sa) / n - sa / n)
        if d >= obs - 1e-12:
            hits += 1
    return hits / len(splits)


def detect_rate(n, delta, sd, alpha, reps, rng):
    m = 2 * n
    if math.comb(m, n) <= 13000:
        splits = list(itertools.combinations(range(m), n))
    else:
        splits = [tuple(rng.sample(range(m), n)) for _ in range(2000)]
        splits.append(tuple(range(n)))  # 观测划分本身
    ok = 0
    for _ in range(reps):
        a = [rng.gauss(0, sd) for _ in range(n)]
        b = [rng.gauss(-delta, sd) for _ in range(n)]  # 改善 = 变小
        if sum(b) < sum(a) and perm_p(a, b, splits) < alpha:
            ok += 1
    return ok / reps


def mde80_normal(n, sd, alpha):
    z = st.NormalDist().inv_cdf
    return (z(1 - alpha / 2) + z(0.8)) * sd * math.sqrt(2 / n)


def pairs_needed(delta, sd, alpha):
    if delta <= 0:
        return None
    z = st.NormalDist().inv_cdf
    return math.ceil(2 * ((z(1 - alpha / 2) + z(0.8)) * sd / delta) ** 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--pj-low", type=float, default=40.0)
    ap.add_argument("--pj-high", type=float, default=160.0)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--k", type=int, default=3, help="改善一侧 Bonferroni 的指标数 (帧时、帧率、功耗)")
    ap.add_argument("--reps", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261021)
    ap.add_argument("--json", help="结果另存一份 JSON")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    alpha_win = a.alpha / a.k

    fa = os.path.join(a.repo, "knobs/evidence/10_frameaudit/analysis.json")
    bpf, items, nframes = saved_bytes_per_frame(fa, RULE)
    mbps = bpf * a.fps / 1e6
    w_lo, w_hi = bpf * a.fps * a.pj_low * 1e-12, bpf * a.fps * a.pj_high * 1e-12
    out = {
        "rule": ",".join(sorted(RULE)),
        "bytes_per_frame": bpf,
        "bytes_per_item": items,
        "frames_seen": nframes,
        "fps": a.fps,
        "saved_MBps_uncompressed": round(mbps, 1),
        "power_saving_w_inferred": [round(w_lo, 4), round(w_hi, 4)],
        "energy_per_byte_pj_assumed": [a.pj_low, a.pj_high],
        "label": "inferred: 字节数是 frame-audit 的模型值 (未压缩上限, UBWC/直绘只会更小); pJ/B 是文献区间",
        "alpha_win": alpha_win,
        "metrics": {},
    }
    # 各指标的预期效应 (同单位): 功耗 W, 带宽投票 MB/s (bw_median 的单位), 帧时/GPU 忙碌按推断给 0 附近
    expected = {
        "power_w_mean": [w_lo, (w_lo + w_hi) / 2, w_hi],
        "bw_median": [mbps / 2, mbps],  # 压缩 2:1 到未压缩上限 (推断)
    }
    for m in METRICS:
        sd, per_group = pooled_sd(a.repo, m)
        if sd is None:
            out["metrics"][m] = None
            continue
        row = {"pooled_sd": round(sd, 4), "sd_per_group": per_group, "mde80": {}, "detect_rate_at_expected": {}}
        for n in (5, 8, 10, 16):
            row["mde80"][n] = round(mde80_normal(n, sd, alpha_win), 4)
        if m in expected:
            for d in expected[m]:
                key = f"{d:.4g}"
                row["detect_rate_at_expected"][key] = {
                    n: detect_rate(n, d, sd, alpha_win, a.reps, rng) for n in (5, 8, 10)
                }
                row.setdefault("pairs_needed_80pct", {})[key] = pairs_needed(d, sd, alpha_win)
        out["metrics"][m] = row

    p = out["metrics"].get("power_w_mean") or {}
    print(f"规则 {out['rule']}")
    print(f"每帧省写回 {bpf/1e6:.3f} MB (模型值; 证据 {nframes} 帧一致), {a.fps:g} fps → {mbps:.0f} MB/s")
    print(f"推算省电 {w_lo*1000:.1f}–{w_hi*1000:.1f} mW  [inferred, {a.pj_low:g}–{a.pj_high:g} pJ/B]")
    for m in METRICS:
        r = out["metrics"].get(m)
        if not r:
            continue
        print(f"{m}: 组内 SD {r['pooled_sd']}  MDE80(n=5/8/10/16) = {r['mde80']}")
        for k, v in r.get("detect_rate_at_expected", {}).items():
            print(f"    预期效应 {k}: 检出率 {v}  80% 检出需 {r['pairs_needed_80pct'][k]} 对")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
