# raw2png.py — 把 framedump 抓到的一段原始帧转成无损 PNG, 顺带出每帧的统计 (重复帧、相邻帧差)。
# 用法: python3 -I raw2png.py <段目录(含 session.json/index.csv)> <输出根目录> [--jobs 8] [--rotate auto|0|90|180|270]
# 输出: <输出根目录>/<段名>/f<帧号>.png, 以及 frames_<时间戳>.csv / convert_<时间戳>.json。
# 不覆盖: 已有的 PNG 跳过; 统计文件每次用新名字。
#   R8G8B8A8 / B8G8R8A8: 8 位 PNG; alpha 全是 255 就存 RGB, 否则存 RGBA (都无损)
#   A2B10G10R10: 16 位 RGB PNG (10 位值左移 6 位, 无损可逆), alpha 2 位另记
# --rotate auto 按 session.json 的 pre_transform 转回屏幕朝向 (方向是推断, 未在真机核对;
# 有疑问时先用 0 出几张看)。
import csv, hashlib, json, os, struct, sys, time, zlib
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from PIL import Image


def write_png16_rgb(path, a):   # a: HxWx3 uint16
    h, w, _ = a.shape
    raw = np.empty((h, 1 + w * 6), np.uint8)
    raw[:, 0] = 0
    raw[:, 1:] = a.astype(">u2").view(np.uint8).reshape(h, w * 6)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw.tobytes(), 6)) + chunk(b"IEND", b"")
    with open(path, "xb") as f:
        f.write(png)


def decode(buf, w, h, fmt):
    """返回 (图像数组, 模式, alpha 是否恒为不透明)"""
    if fmt in (37, 43, 44, 50):  # R8G8B8A8_{UNORM,SRGB}, B8G8R8A8_{UNORM,SRGB}
        a = np.frombuffer(buf, np.uint8).reshape(h, w, 4)
        if fmt in (44, 50):
            a = a[:, :, [2, 1, 0, 3]]
        opaque = bool((a[:, :, 3] == 255).all())
        return (a[:, :, :3] if opaque else a), ("RGB" if opaque else "RGBA"), opaque
    if fmt == 64:  # A2B10G10R10_UNORM_PACK32: R 在低 10 位
        p = np.frombuffer(buf, "<u4").reshape(h, w)
        rgb = np.stack([(p >> 0) & 1023, (p >> 10) & 1023, (p >> 20) & 1023], -1).astype(np.uint16) << 6
        return rgb, "RGB16", bool(((p >> 30) == 3).all())
    raise ValueError(f"不认识的 vk_format {fmt}")


def rot_k(pre_transform, rotate):
    if rotate != "auto":
        return int(rotate) // 90
    # VkSurfaceTransformFlagBitsKHR: 1 IDENTITY, 2 ROTATE_90, 4 ROTATE_180, 8 ROTATE_270
    # 应用按 preTransform 预先转过画面; 转回屏幕朝向 = 反方向转。方向未在真机核对。
    return {1: 0, 2: 1, 4: 2, 8: 3}.get(pre_transform, 0)


def one(args):
    src, dst, w, h, fmt, k = args
    with open(src, "rb") as f:
        buf = f.read()
    a, mode, opaque = decode(buf, w, h, fmt)
    if k:
        a = np.ascontiguousarray(np.rot90(a, k))
    sha = hashlib.sha1(np.ascontiguousarray(a).tobytes()).hexdigest()
    small = a[::8, ::8, :3].astype(np.float32)
    if not os.path.exists(dst):
        if mode == "RGB16":
            write_png16_rgb(dst, a)
        else:
            with open(dst, "xb") as f:
                Image.fromarray(a, mode).save(f, format="PNG", compress_level=6)
    return sha, opaque, mode, small


def main():
    argv = sys.argv[1:]
    jobs, rotate = 8, "auto"
    if "--jobs" in argv:
        i = argv.index("--jobs"); jobs = int(argv[i + 1]); del argv[i:i + 2]
    if "--rotate" in argv:
        i = argv.index("--rotate"); rotate = argv[i + 1]; del argv[i:i + 2]
    seg, out_root = argv[0].rstrip("/"), argv[1]
    sess = json.load(open(os.path.join(seg, "session.json")))
    w, h, fmt = sess["w"], sess["h"], sess["vk_format"]
    k = rot_k(sess.get("pre_transform", 1), rotate)
    name = os.path.basename(seg)
    out = os.path.join(out_root, name)
    os.makedirs(out, exist_ok=True)
    rows = [r for r in csv.DictReader(open(os.path.join(seg, "index.csv"))) if r["file"].endswith(".raw")]
    rows = [r for r in rows if os.path.exists(os.path.join(seg, r["file"]))]   # 没拉全的帧不算
    tasks = [(os.path.join(seg, r["file"]), os.path.join(out, r["file"][:-4] + ".png"), w, h, fmt, k) for r in rows]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    t0 = time.time()
    prev_sha, prev_small = None, None
    n_dup = 0
    opaque_all = True
    modes = set()
    with open(os.path.join(out, f"frames_{stamp}.csv"), "x", newline="") as fo, ProcessPoolExecutor(jobs) as ex:
        wr = csv.writer(fo)
        wr.writerow(["frame", "png", "sha1", "dup_of_prev", "mad_prev_8x"])
        for r, (sha, opaque, mode, small) in zip(rows, ex.map(one, tasks, chunksize=4)):
            dup = prev_sha == sha
            n_dup += dup
            mad = float(np.abs(small - prev_small).mean()) if prev_small is not None and prev_small.shape == small.shape else ""
            wr.writerow([r["frame"], r["file"][:-4] + ".png", sha, int(dup), mad])
            prev_sha, prev_small = sha, small
            opaque_all &= opaque
            modes.add(mode)
    rep = {"segment": name, "frames": len(tasks), "w": w, "h": h, "vk_format": fmt, "rot90_k": k,
           "png_modes": sorted(modes), "alpha_all_opaque": opaque_all, "exact_duplicates_of_prev": n_dup,
           "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(out, f"convert_{stamp}.json"), "x") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False))


if __name__ == "__main__":
    main()
