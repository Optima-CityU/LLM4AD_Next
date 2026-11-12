# validate.py — 用 numpy 参考逐级核对插帧 GPU 侧 shader (由 harness 跑) 的输出, 并算对真值的 PSNR。
# 参考实现与 I1 的 tools/interp/scripts 保持同一定义 (缩小 = tf.image.resize antialias 三角核; 放大 = 半像素双线性;
# 采样 = border 夹边的双线性; 路线 B 的块匹配/平滑按本批定的参数: 1/4 分辨率、8x8 块、±16、3x3 中值 + 5x5 二项式)。
# 用法 (要有 TensorFlow 跑 TFLite 模型):
#   python -I validate.py --interp-root <tools/interp> --harness <可执行> --spv <spv 目录> --work <临时目录> --out <json>
import argparse, json, os, subprocess, sys
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--interp-root", required=True)
ap.add_argument("--harness", required=True)
ap.add_argument("--spv", required=True)
ap.add_argument("--work", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--reps", type=int, default=5)
ap.add_argument("--names", default="pan32,zoom_hud,pan96")
args = ap.parse_args()
os.makedirs(args.work, exist_ok=True)
ROOT = args.interp_root
TOL = 1e-3          # 像素量按 0..1 归一化计 (= 0..255 刻度上的 0.255); 光流/亮度/运动向量这类中间量按其自身单位计
HUD = [(20, 140, 30, 420), (20, 110, 1700, 2120), (880, 960, 820, 1320)]   # (y0,y1,x0,x1), 同 make_pairs.py


def psnr(a, b):
    return float(10 * np.log10(255.0 ** 2 / np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)))


def hud_psnr(a, b):
    return psnr(np.concatenate([a[y0:y1, x0:x1].reshape(-1, 3) for y0, y1, x0, x1 in HUD]),
                np.concatenate([b[y0:y1, x0:x1].reshape(-1, 3) for y0, y1, x0, x1 in HUD]))


def hud_mask(H, W):
    m = np.zeros((H, W), bool)
    for y0, y1, x0, x1 in HUD:
        m[y0:y1, x0:x1] = True
    return m


# ---------- 缩小: TF scale_and_translate 三角核 (tf.image.resize bilinear antialias=True) ----------
def tri_weights(dst, src):
    """返回 [dst, src] 的权重矩阵 (float32 计算, 与 TF/shader 同公式)"""
    inv = np.float32(src) / np.float32(dst)
    ks = max(inv, np.float32(1))
    Wm = np.zeros((dst, src), np.float64)
    for x in range(dst):
        sf = (np.float32(x) + np.float32(0.5)) * inv
        s0 = int(np.clip(np.ceil(sf - ks - np.float32(0.5)), 0, src - 1))
        s1 = int(np.clip(np.floor(sf + ks - np.float32(0.5)), 0, src - 1))
        idx = np.arange(s0, s1 + 1)
        kp = (idx.astype(np.float32) + np.float32(0.5) - sf) / ks
        w = np.maximum(0, 1 - np.abs(kp)).astype(np.float32)
        if w.sum() > 0:
            w = w / w.sum()
        Wm[x, s0:s1 + 1] = w
    return Wm


def aa_down(img, h, w):
    H, W = img.shape[:2]
    Wy, Wx = tri_weights(h, H), tri_weights(w, W)
    x = img.astype(np.float64)
    t = np.tensordot(Wy, x, axes=(1, 0))            # [h,W,c]  (先纵向再横向, 两次矩阵乘; 三元 einsum 不加 optimize 会是 O(hHWw))
    return np.einsum("hWc,wW->hwc", t, Wx, optimize=True)


# ---------- 放大: 半像素双线性 (tf.image.resize bilinear / torch align_corners=False) ----------
def up_axis(dst, src, pos=None):
    """目标位置 pos (缺省 = arange(dst)) 在源轴 (src 格, 每格 cell=dst/src) 上的 (i0, i1, a)"""
    if pos is None:
        pos = np.arange(dst, dtype=np.float32)
    s = np.maximum((pos.astype(np.float32) + np.float32(0.5)) * (np.float32(src) / np.float32(dst)) - np.float32(0.5), 0)
    f = np.floor(s)
    i0 = np.minimum(f.astype(np.int64), src - 1)
    i1 = np.minimum(i0 + 1, src - 1)
    return i0, i1, (s - f).astype(np.float32)


def up_bilinear(x, H, W, ys=None, xs=None):
    """x: [h,w,c] -> [H,W,c] (或在给定的连续目标位置 ys, xs 上)"""
    h, w = x.shape[:2]
    x0, x1, ax = up_axis(W, w, xs)
    y0, y1, ay = up_axis(H, h, ys)
    ax = ax[None, :, None]; ay = ay[:, None, None]
    top = x[y0][:, x0] * (1 - ax) + x[y0][:, x1] * ax
    bot = x[y1][:, x0] * (1 - ax) + x[y1][:, x1] * ax
    return top * (1 - ay) + bot * ay


def warp_abs(img, xs, ys):
    """在绝对连续坐标 (xs, ys) 处双线性取值, 坐标夹到 [0, size-1] (I1 warp_np)"""
    H, W = img.shape[:2]
    xs = np.clip(xs, 0, W - 1); ys = np.clip(ys, 0, H - 1)
    x0 = np.floor(xs).astype(np.int64); y0 = np.floor(ys).astype(np.int64)
    x1 = np.minimum(x0 + 1, W - 1); y1 = np.minimum(y0 + 1, H - 1)
    ax = (xs - x0)[..., None].astype(np.float32); ay = (ys - y0)[..., None].astype(np.float32)
    im = img.astype(np.float32)
    top = im[y0, x0] * (1 - ax) + im[y0, x1] * ax
    bot = im[y1, x0] * (1 - ax) + im[y1, x1] * ax
    return top * (1 - ay) + bot * ay


def warp_np(img, fx, fy):
    H, W = fx.shape
    return warp_abs(img, np.arange(W)[None, :] + fx, np.arange(H)[:, None] + fy)


# ---------- 路线 A 参考 (= I1 convert_eval.gpu_side + HUD 直通) ----------
def route_a_ref(f0, f1, flow, mask, t=0.5, hud_on=False):
    H, W = f0.shape[:2]; h, w = flow.shape[:2]
    up = up_bilinear(flow, H, W) * np.array([W / w, H / h, W / w, H / h], np.float32)
    m = 1 / (1 + np.exp(-up_bilinear(mask, H, W)))
    out = warp_np(f0, up[..., 0], up[..., 1]) * m + warp_np(f1, up[..., 2], up[..., 3]) * (1 - m)
    if hud_on:
        near = f1 if t >= 0.5 else f0
        out = np.where(hud_mask(H, W)[..., None], near.astype(np.float32), out)
    return out   # 取整前的浮点值; 输出 = np.clip(out + 0.5, 0, 255) 向下取整


# ---------- 路线 B 参考 ----------
BS, R, UP = 8, 16, 4


def luma_q(img):
    H, W = img.shape[:2]
    x = img.astype(np.int64)
    L = 77 * x[..., 0] + 150 * x[..., 1] + 29 * x[..., 2]
    qh, qw = (H + 3) // 4, (W + 3) // 4
    L = np.pad(L, ((0, qh * 4 - H), (0, qw * 4 - W)), mode="edge")
    return L.reshape(qh, 4, qw, 4).sum((1, 3))


def block_match(A, B):
    qh, qw = A.shape
    gh, gw = (qh + BS - 1) // BS, (qw + BS - 1) // BS
    Ap = np.pad(A, ((0, gh * BS - qh), (0, gw * BS - qw)), mode="edge")
    Bp = np.pad(B, ((R, gh * BS - qh + R), (R, gw * BS - qw + R)), mode="edge")
    n = 2 * R + 1
    keys = np.empty((n * n, gh, gw), np.int64)
    for c in range(n * n):
        dy, dx = c // n - R, c % n - R
        sh = Bp[R + dy:R + dy + gh * BS, R + dx:R + dx + gw * BS]
        sad = np.abs(Ap - sh).reshape(gh, BS, gw, BS).sum((1, 3))
        keys[c] = (sad << 22) | ((abs(dx) + abs(dy)) << 11) | c     # (SAD, 位移大小, 扫描序) 字典序
    c = keys.argmin(0)
    return np.stack([c % n - R, c // n - R], -1).astype(np.int32)   # [gh,gw,(dx,dy)]


def smooth(mv):
    gh, gw = mv.shape[:2]
    out = np.zeros((gh, gw, 2), np.float32)
    wgt = np.array([1, 4, 6, 4, 1], np.int64)
    for c in range(2):
        p = np.pad(mv[..., c].astype(np.int64), 1, mode="edge")
        med = np.median(np.lib.stride_tricks.sliding_window_view(p, (3, 3)), axis=(-2, -1)).astype(np.int64)
        p = np.pad(med, 2, mode="edge")
        win = np.lib.stride_tricks.sliding_window_view(p, (5, 5))
        acc = (win * (wgt[:, None] * wgt[None, :])).sum((-2, -1))
        out[..., c] = acc.astype(np.float32) * np.float32(UP) / np.float32(256)
    return out


def flow_field(fl, H, W, ow, oh, cell):
    """块网格运动场放大到 NPU 网格 (ow x oh) 的像素中心, 返回 (全分辨率位置 px, py, 运动 v[oh,ow,2])"""
    gh, gw = fl.shape[:2]
    px = (np.arange(ow, dtype=np.float32) + np.float32(0.5)) * (np.float32(W) / np.float32(ow)) - np.float32(0.5)
    py = (np.arange(oh, dtype=np.float32) + np.float32(0.5)) * (np.float32(H) / np.float32(oh)) - np.float32(0.5)
    x0, x1, ax = up_axis(int(gw * cell), gw, px)
    y0, y1, ay = up_axis(int(gh * cell), gh, py)
    ax = ax[None, :, None]; ay = ay[:, None, None]
    top = fl[y0][:, x0] * (1 - ax) + fl[y0][:, x1] * ax
    bot = fl[y1][:, x0] * (1 - ax) + fl[y1][:, x1] * ax
    return px, py, top * (1 - ay) + bot * ay


def prealign(f0, f1, fl, ow, oh, cell, t=0.5):
    H, W = f0.shape[:2]
    px, py, v = flow_field(fl, H, W, ow, oh, cell)
    X = px[None, :]; Y = py[:, None]
    a = warp_abs(f0, X - t * v[..., 0], Y - t * v[..., 1])
    b = warp_abs(f1, X + (1 - t) * v[..., 0], Y + (1 - t) * v[..., 1])
    return a, b


def route_b_ref(f0, f1, fl, res_q, res_scale, res_zp, cell, t=0.5, hud_on=False):
    H, W = f0.shape[:2]
    a, b = prealign(f0, f1, fl, W, H, cell, t)
    r = (res_q.astype(np.float32) - res_zp) * res_scale
    out = (a + b) * 0.5 + up_bilinear(r, H, W) * 255.0
    if hud_on:
        near = f1 if t >= 0.5 else f0
        out = np.where(hud_mask(H, W)[..., None], near.astype(np.float32), out)
    return out   # 取整前的浮点值; 输出 = np.clip(out + 0.5, 0, 255) 向下取整


# ---------- TFLite ----------
import tensorflow as tf  # noqa: E402


def tflite_run(path, inputs):
    it = tf.lite.Interpreter(model_path=path, num_threads=8)
    it.allocate_tensors()
    for d, x in zip(it.get_input_details(), inputs):
        it.set_tensor(d["index"], x)
    it.invoke()
    outs = {}
    for d in it.get_output_details():
        outs[int(d["shape"][-1])] = (it.get_tensor(d["index"])[0], d["quantization"])
    return outs


def run_harness(route, **kv):
    cmd = [args.harness, f"spv={args.spv}", f"route={route}", f"reps={args.reps}"] + [f"{k}={v}" for k, v in kv.items()]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print("harness 失败:", " ".join(cmd), p.stderr, file=sys.stderr); sys.exit(1)
    return json.loads(p.stdout.strip().splitlines()[-1])


def cmp(rep, key, a, b):
    """整数/浮点中间结果直接比: 最大绝对差 <= TOL"""
    d = np.abs(a.astype(np.float64) - b.astype(np.float64))
    rep[key] = {"maxabs": float(d.max()), "mismatch_frac": float((d > TOL).mean()), "pass": bool(d.max() <= TOL)}


def cmp_q(rep, key, g, ref_f):
    """shader 只写 8 位结果, 浮点中间值不出 GPU。判据: GPU 8 位值与参考浮点值取整后的结果相比,
    差最多 1, 且凡是不同的像素, 参考浮点值离取整分界 (x.5) 都不超过 TOL —— 即 GPU 取整前的值与参考的差 <= TOL
    能解释全部差异 (TOL 按 0..1 归一化)。boundary_dist_max = 不同像素里参考值离分界的最大距离 (0..255 刻度, = GPU 浮点误差的下界;
    这是下界不是上界: 取整后相同的像素, 浮点差最多到它离分界的距离, 无法从 8 位输出读出)。"""
    r = np.clip(np.floor(ref_f.astype(np.float64) + 0.5), 0, 255)
    d = np.abs(g.astype(np.float64) - r)
    bad = d > 0
    bd = np.abs(ref_f.astype(np.float64) - np.floor(ref_f.astype(np.float64)) - 0.5)[bad]
    bdm = float(bd.max()) if bd.size else 0.0
    rep[key] = {"maxabs_u8": float(d.max()), "mismatch_frac": float(bad.mean()), "boundary_dist_max": bdm,
                "boundary_dist_max_norm": bdm / 255.0, "pass": bool(d.max() <= 1 and bdm / 255.0 <= TOL)}


def rgba(img):
    return np.concatenate([img, np.full(img.shape[:2] + (1,), 255, np.uint8)], -1)


pairs = np.load(f"{ROOT}/out/pairs.npz")
RIFE = f"{ROOT}/models/rife425lite_lowwarp_576x256_int8.tflite"
ANVIL = f"{ROOT}/models/anvil_s_1072x496_int8.tflite"
report = {"tol": TOL, "cases": {}}
hud_file = f"{args.work}/hud.txt"
with open(hud_file, "w") as f:
    for y0, y1, x0, x1 in HUD:
        f.write(f"{x0} {y0} {x1} {y1}\n")

for n in args.names.split(","):
    f0, f1, gt = pairs[n + "_f0"], pairs[n + "_f1"], pairs[n + "_gt"]
    H, W = f0.shape[:2]
    wd = f"{args.work}/{n}"; os.makedirs(wd, exist_ok=True)
    rgba(f0).tofile(f"{wd}/f0.rgba"); rgba(f1).tofile(f"{wd}/f1.rgba")
    rep = {"W": W, "H": H, "blend_psnr": round(psnr(((f0.astype(np.float32) + f1) / 2 + 0.5).astype(np.uint8), gt), 2)}
    use_hud = n == "zoom_hud"

    # ===== 路线 A =====
    h, w = 256, 576
    d0 = aa_down(f0, h, w); d1 = aa_down(f1, h, w)
    tf0 = tf.image.resize(f0[None].astype(np.float32), (h, w), method="bilinear", antialias=True).numpy()[0]
    rep["a_down_numpy_vs_tf_maxabs"] = float(np.abs(d0 - tf0).max())       # 参考自身对 TF 的核对
    q0 = np.clip(np.round(d0), 0, 255).astype(np.uint8); q1 = np.clip(np.round(d1), 0, 255).astype(np.uint8)
    o = tflite_run(RIFE, [q0[None], q1[None]])
    flow, mask = o[4][0].astype(np.float32), o[1][0].astype(np.float32)
    flow.tofile(f"{wd}/flow.f32"); mask.tofile(f"{wd}/mask.f32")
    ra = {}
    for hud_on in ([False, True] if use_hud else [False]):
        tag = "hud" if hud_on else "nohud"
        tm = run_harness("a", f0=f"{wd}/f0.rgba", f1=f"{wd}/f1.rgba", W=W, H=H, w=w, h=h, flow=f"{wd}/flow.f32", mask=f"{wd}/mask.f32",
                         hud=hud_file if hud_on else "", hud_on=int(hud_on), t=0.5,
                         out_down0=f"{wd}/a_down0.u8", out_down1=f"{wd}/a_down1.u8", out=f"{wd}/a_out_{tag}.rgba")
        g0 = np.fromfile(f"{wd}/a_down0.u8", np.uint8).reshape(h, w, 3)
        g1 = np.fromfile(f"{wd}/a_down1.u8", np.uint8).reshape(h, w, 3)
        cmp_q(ra, "down0", g0, d0); cmp_q(ra, "down1", g1, d1)
        gout = np.fromfile(f"{wd}/a_out_{tag}.rgba", np.uint8).reshape(H, W, 4)[..., :3]
        ref_f = route_a_ref(f0, f1, flow, mask, 0.5, hud_on)
        ref = np.clip(ref_f + 0.5, 0, 255).astype(np.uint8)
        cmp_q(ra, f"out_{tag}", gout, ref_f)
        ra[f"psnr_{tag}"] = round(psnr(gout, gt), 2); ra[f"hud_psnr_{tag}"] = round(hud_psnr(gout, gt), 2)
        ra[f"ref_psnr_{tag}"] = round(psnr(ref, gt), 2)
        ra["timing_ms"] = tm["dispatch_ms"]; ra["device"] = tm["device"]
        # 用 shader 的缩小结果再过一遍模型, 看光流是否随之变化 (缩小若逐字节一致则为 0)
        o2 = tflite_run(RIFE, [g0[None], g1[None]])
        ra["flow_from_gpu_down_vs_ref_maxabs_px"] = float(np.abs(o2[4][0] - flow).max())
    rep["route_a"] = ra

    # ===== 路线 B =====
    ow, oh = 1072, 496
    cell = float(BS * UP)
    L0, L1 = luma_q(f0), luma_q(f1)
    mv = block_match(L0, L1)
    fl = smooth(mv)
    a_ref, b_ref = prealign(f0, f1, fl, ow, oh, cell)
    npuin_ref = np.clip(np.round(np.concatenate([a_ref, b_ref], -1)), 0, 255).astype(np.uint8)
    rb = {"grid": [int(fl.shape[1]), int(fl.shape[0])], "mv_absmax_qpx": int(np.abs(mv).max()),
          "flow_mean_abs_px": [float(np.abs(fl[..., 0]).mean()), float(np.abs(fl[..., 1]).mean())]}
    tm = run_harness("b", f0=f"{wd}/f0.rgba", f1=f"{wd}/f1.rgba", W=W, H=H, ow=ow, oh=oh, cell=cell, t=0.5,
                     out_luma0=f"{wd}/b_luma0.u32", out_luma1=f"{wd}/b_luma1.u32", out_mv=f"{wd}/b_mv.i32",
                     out_flow=f"{wd}/b_flow.f32", out_npuin=f"{wd}/b_npuin.u8")
    qh, qw = L0.shape; gh, gw = mv.shape[:2]
    cmp(rb, "luma0", np.fromfile(f"{wd}/b_luma0.u32", np.uint32).reshape(qh, qw), L0)
    cmp(rb, "luma1", np.fromfile(f"{wd}/b_luma1.u32", np.uint32).reshape(qh, qw), L1)
    gmv = np.fromfile(f"{wd}/b_mv.i32", np.int32).reshape(gh, gw, 2)
    cmp(rb, "mv", gmv, mv)
    gfl = np.fromfile(f"{wd}/b_flow.f32", np.float32).reshape(gh, gw, 2)
    cmp(rb, "flow", gfl, fl)
    gin = np.fromfile(f"{wd}/b_npuin.u8", np.uint8).reshape(oh, ow, 6)
    cmp_q(rb, "npuin", gin, np.concatenate([a_ref, b_ref], -1))
    # NPU: ANVIL-S 半分辨率 int8, 输入用 shader 的预对齐结果
    o = tflite_run(ANVIL, [gin[None]])
    res_q, (rs, rz) = o[3]
    res_q.astype(np.int8).tofile(f"{wd}/b_res.i8")
    for hud_on in ([False, True] if use_hud else [False]):
        tag = "hud" if hud_on else "nohud"
        tm2 = run_harness("b", f0=f"{wd}/f0.rgba", f1=f"{wd}/f1.rgba", W=W, H=H, ow=ow, oh=oh, cell=cell, t=0.5,
                          res=f"{wd}/b_res.i8", res_scale=rs, res_zp=rz, hud=hud_file if hud_on else "", hud_on=int(hud_on),
                          out=f"{wd}/b_out_{tag}.rgba")
        gout = np.fromfile(f"{wd}/b_out_{tag}.rgba", np.uint8).reshape(H, W, 4)[..., :3]
        ref_f = route_b_ref(f0, f1, fl, res_q, rs, rz, cell, 0.5, hud_on)
        ref = np.clip(ref_f + 0.5, 0, 255).astype(np.uint8)
        cmp_q(rb, f"out_{tag}", gout, ref_f)
        rb[f"psnr_{tag}"] = round(psnr(gout, gt), 2); rb[f"hud_psnr_{tag}"] = round(hud_psnr(gout, gt), 2)
        rb[f"ref_psnr_{tag}"] = round(psnr(ref, gt), 2)
        rb["timing_ms"] = {**tm["dispatch_ms"], **tm2["dispatch_ms"]}
    # 只预对齐取平均 (不加残差) 的 PSNR, 对照 I1 的「只块匹配预对齐后平均」
    rb["prealign_blend_psnr"] = round(psnr(np.clip((a_ref + b_ref) / 2 + 0.5, 0, 255).astype(np.uint8),
                                           np.clip(np.round(tf.image.resize(gt[None].astype(np.float32), (oh, ow), antialias=True).numpy()[0]), 0, 255).astype(np.uint8)), 2)
    rep["route_b"] = rb
    report["cases"][n] = rep
    print(n, json.dumps(rep, indent=1), flush=True)

allpass = all(v["pass"] for c in report["cases"].values() for r in ("route_a", "route_b") for v in c[r].values() if isinstance(v, dict) and "pass" in v)
report["all_pass"] = allpass
json.dump(report, open(args.out, "w"), indent=1)
print("ALL_PASS" if allpass else "SOME_FAIL", args.out)
