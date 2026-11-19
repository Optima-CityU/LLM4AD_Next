#!/usr/bin/env python3
"""从上游场景数据推导相机路线, 而不是手填坐标。

输入: megacity-metro 的 Assets/Scenes/Main/MegacityMetroLevelBounds.unity
输出: route_a.json

为什么要用脚本而不是手写一串数字: 路线是证据链的一部分 —— 输出 json 里会带它的
sha256, 两臂对不上就说明这次对照无效。既然如此, 这串坐标自己也得能被审计和复现,
而不是"某次某人在编辑器里拖出来的"。

route_a 的几何定义, 完全由上游数据 + 三个常数决定:

  1. 城市中心 C = 66 个边界 Point 的形心
  2. 城市半径 R = C 到各 Point 的最大水平距离
  3. 相机绕 C 走一段下降圆弧: 半径 1.8R 起, 高度从出生点高度降到城市顶 + 40,
     全程朝向 C

选这个形状的理由: 相机始终在边界体之外、城市顶之上, 不可能穿进楼里 —— 这条路线
的确定性不依赖"运气好没撞到东西"。同时整座城一直在视野里, 渲染负载是满的, 且
随距离和角度连续变化, 不是一段死板的平移。

用法: python3 make_route_a.py <MegacityMetroLevelBounds.unity> <out.json>
"""
import json
import math
import re
import sys

# 三个常数 —— 路线形状的全部自由度
RADIUS_SCALE = 1.8      # 环绕半径 = 城市半径的多少倍
TOP_CLEARANCE = 40.0    # 终点高度 = 城市最高点 + 这么多
SWEEP_DEG = 270.0       # 总共扫过多少度
N_WAYPOINTS = 16
FOV = 60.0
FAR = 3000.0


def parse_scene(path):
    """把 Unity 场景 YAML 里的 GameObject 名字和 Transform 坐标配起来。"""
    src = open(path, encoding="utf-8", errors="replace").read()
    docs = re.split(r"^--- ", src, flags=re.M)[1:]
    names, transforms = {}, []
    for d in docs:
        head = re.match(r"!u!(\d+) &(\d+)", d)
        if not head:
            continue
        cls, fid = head.group(1), head.group(2)
        if cls == "1":                                  # GameObject
            m = re.search(r"m_Name: (.*)", d)
            if m:
                names[fid] = m.group(1).strip()
        elif cls == "4":                                # Transform
            g = re.search(r"m_GameObject: \{fileID: (\d+)\}", d)
            p = re.search(
                r"m_LocalPosition: \{x: (-?[\d.eE+-]+), y: (-?[\d.eE+-]+), z: (-?[\d.eE+-]+)\}", d)
            if g and p:
                transforms.append((g.group(1), tuple(float(v) for v in p.groups())))
    return [(names.get(fid, "?"), pos) for fid, pos in transforms]


def look_at_euler(eye, target):
    """朝向 target 的欧拉角(度), Unity 左手系 Y-up: yaw 绕 Y, pitch 绕 X(下看为正)。"""
    dx, dy, dz = (target[i] - eye[i] for i in range(3))
    yaw = math.degrees(math.atan2(dx, dz))
    pitch = math.degrees(math.atan2(-dy, math.hypot(dx, dz)))
    return (pitch, yaw, 0.0)


def main():
    scene, out = sys.argv[1], sys.argv[2]
    objs = parse_scene(scene)

    pts = [p for n, p in objs if n.lower().startswith("point")]
    spawns = [p for n, p in objs if n == "SpawnPoint"]
    if len(pts) < 8:
        sys.exit(f"边界点只解析到 {len(pts)} 个, 上游场景结构可能变了 —— 先人工核对, 不要硬跑")
    if not spawns:
        sys.exit("没解析到 SpawnPoint, 上游场景结构可能变了")

    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    cz = sum(p[2] for p in pts) / len(pts)
    center = (cx, cy, cz)
    radius = max(math.hypot(p[0] - cx, p[2] - cz) for p in pts)
    top_y = max(p[1] for p in pts)
    start_y = sum(s[1] for s in spawns) / len(spawns)      # 出生点平均高度
    end_y = top_y + TOP_CLEARANCE

    r = radius * RADIUS_SCALE
    start_ang = math.atan2(spawns[0][2] - cz, spawns[0][0] - cx)   # 从第一个出生点的方位起步

    waypoints = []
    for i in range(N_WAYPOINTS):
        t = i / (N_WAYPOINTS - 1)
        ang = start_ang + math.radians(SWEEP_DEG) * t
        y = start_y + (end_y - start_y) * t
        pos = (cx + r * math.cos(ang), y, cz + r * math.sin(ang))
        waypoints.append({
            "pos": [round(v, 3) for v in pos],
            "rot": [round(v, 3) for v in look_at_euler(pos, center)],
        })

    doc = {
        "_derived_from": {
            "scene": "Assets/Scenes/Main/MegacityMetroLevelBounds.unity",
            "bound_points": len(pts),
            "spawn_points": len(spawns),
            "city_center": [round(v, 3) for v in center],
            "city_radius": round(radius, 3),
            "city_top_y": round(top_y, 3),
            "constants": {
                "radius_scale": RADIUS_SCALE,
                "top_clearance": TOP_CLEARANCE,
                "sweep_deg": SWEEP_DEG,
                "n_waypoints": N_WAYPOINTS,
            },
            "note": "由 make_route_a.py 从上游场景推导, 不要手改。改了就重新生成, 并记住 sha256 会变。",
        },
        "fov": FOV,
        "far": FAR,
        "waypoints": waypoints,
    }
    # 固定键序 + 固定缩进 -> 同一份输入每次生成逐字节一致, sha256 才有意义
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")

    print(f"城市中心 {tuple(round(v,1) for v in center)}  半径 {radius:.1f}  顶 {top_y:.1f}")
    print(f"相机: 环绕半径 {r:.1f}, 高度 {start_y:.1f} -> {end_y:.1f}, 扫 {SWEEP_DEG:.0f}°")
    print(f"写出 {N_WAYPOINTS} 个航点 -> {out}")


if __name__ == "__main__":
    main()
