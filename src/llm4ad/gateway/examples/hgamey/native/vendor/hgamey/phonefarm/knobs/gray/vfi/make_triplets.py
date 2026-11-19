# make_triplets.py — 从一段 framedump 的 index.csv 出插帧三元组清单 (输入两帧 + 真值中间帧)。
# 用法: python3 -I make_triplets.py <段目录> <输出根目录> [--stride 1,2] [--tol 0.25]
#                                  [--frames <raw2png 出的 frames_*.csv>] [--vimeo <PNG 目录>]
#
# 规则 (原神锁 60 帧时):
#   stride=1: (t, t+1, t+2) —— 输入 t 与 t+2 = 30 帧流的相邻两帧, 真值 t+1 = 60 帧流里夹在中间的那帧。
#             这就是「30 插到 60」。
#   stride=2: (t, t+2, t+4) —— 输入间隔 66.7 ms, 运动更大, 相当于「15 插到 30」, 用作难例/增广。
#   「60 插到 120」要游戏真跑 120 帧才有真值; 原神在这台机上最高 60 (推断), 所以不出这一档。
# 一个三元组算有效, 必须:
#   1. t .. t+2s 之间每一个 present 都抓到了 (帧号连续、没有 SKIP/DROP/WRITE_FAILED);
#   2. 两半的时间间隔接近: |Δ1-Δ2| / 平均 <= tol, Δ = 两帧 present 时刻差 (CLOCK_MONOTONIC)。
#      present 时刻不等于游戏内的模拟时刻, 但原神按帧间隔推进模拟, 两者偏差在一帧之内 (推断);
#      间隔不均的三元组, 真值就不在正中间, 训练时会教坏模型, 所以剔掉;
#   3. 若给了 --frames: 真值帧不能与任一输入帧逐字节相同 (游戏重复送显同一帧时会出现)。
# eval=1 标记互不重叠的一组 (t 是 2s 的倍数), 评测只用这一组, 避免相邻三元组共享帧导致样本相关。
# --vimeo 时另建 <输出根目录>/<段名>/vimeo_s<stride>/<序号>/im{1,2,3}.png 软链接 (不复制数据)。
import csv, json, os, statistics, sys, time


def main():
    argv = sys.argv[1:]
    opts = {"--stride": "1,2", "--tol": "0.25", "--frames": None, "--vimeo": None}
    for k in list(opts):
        if k in argv:
            i = argv.index(k); opts[k] = argv[i + 1]; del argv[i:i + 2]
    seg, out_root = argv[0].rstrip("/"), argv[1]
    name = os.path.basename(seg)
    strides = [int(x) for x in opts["--stride"].split(",")]
    tol = float(opts["--tol"])

    rows = list(csv.DictReader(open(os.path.join(seg, "index.csv"))))
    by_frame = {int(r["frame"]): r for r in rows}
    ok = {f for f, r in by_frame.items() if r["file"].endswith(".raw")}
    t_ns = {f: int(r["t_ns"]) for f, r in by_frame.items()}
    sha = {}
    if opts["--frames"]:
        for r in csv.DictReader(open(opts["--frames"])):
            sha[int(r["frame"])] = r["sha1"]

    dts = [t_ns[f] - t_ns[f - 1] for f in sorted(t_ns) if f - 1 in t_ns]
    nominal = statistics.median(dts) if dts else 0
    out = os.path.join(out_root, name)
    os.makedirs(out, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    report = {"segment": name, "presents": len(rows), "captured": len(ok),
              "median_present_dt_ms": round(nominal / 1e6, 3) if nominal else None,
              "fps_from_median": round(1e9 / nominal, 2) if nominal else None, "tol": tol, "strides": {}}
    for s in strides:
        reasons = {"gap": 0, "uneven_dt": 0, "dup": 0}
        trip = []
        frames = sorted(ok)
        for t in frames:
            span = range(t, t + 2 * s + 1)
            if not all(f in ok for f in span):
                reasons["gap"] += 1
                continue
            d1 = t_ns[t + s] - t_ns[t]
            d2 = t_ns[t + 2 * s] - t_ns[t + s]
            m = (d1 + d2) / 2
            if m <= 0 or abs(d1 - d2) / m > tol:
                reasons["uneven_dt"] += 1
                continue
            if sha and (sha.get(t + s) in (sha.get(t), sha.get(t + 2 * s))):
                reasons["dup"] += 1
                continue
            trip.append((t, t + s, t + 2 * s, d1, d2))
        fn = os.path.join(out, f"triplets_s{s}_{stamp}.csv")
        with open(fn, "x", newline="") as f:
            w = csv.writer(f)
            w.writerow(["in0_frame", "gt_frame", "in1_frame", "in0", "gt", "in1", "dt0_ms", "dt1_ms", "eval"])
            for a, b, c, d1, d2 in trip:
                w.writerow([a, b, c, f"f{a:010d}", f"f{b:010d}", f"f{c:010d}",
                            round(d1 / 1e6, 3), round(d2 / 1e6, 3), int(a % (2 * s) == 0)])
        n_eval = sum(1 for a, *_ in trip if a % (2 * s) == 0)
        report["strides"][s] = {"triplets": len(trip), "eval_nonoverlap": n_eval, "rejected": reasons, "file": fn}
        if opts["--vimeo"]:
            vd = os.path.join(out, f"vimeo_s{s}_{stamp}")
            os.makedirs(vd)
            for i, (a, b, c, *_r) in enumerate(trip):
                d = os.path.join(vd, f"{i:05d}")
                os.makedirs(d)
                for nm, fr in (("im1", a), ("im2", b), ("im3", c)):
                    os.symlink(os.path.abspath(os.path.join(opts["--vimeo"], f"f{fr:010d}.png")),
                               os.path.join(d, nm + ".png"))
    with open(os.path.join(out, f"triplets_report_{stamp}.json"), "x") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
