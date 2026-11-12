#!/bin/bash
# run_megacity.sh <label> <outdir> <scene> <route> <frames> [capdur]
#
# 驱动 megacity 载体跑一轮: 温度回落轮询 → 推路线 → 启动 → 采集 → 等自退 → 收证据。
# 结构刻意与 loop_v1/refbench/run_refbench.sh 对齐, 只在载体差异处分叉。
#
# 退出码: 0=有效轮  2=硬失败  3=无效轮 (采集窗内有热事件或非干净退出, 证据保留)
# 时序全部用存活/状态轮询, 无时长盲等。
#
# 状态: 已实跑验证 —— 3 轮基线 3/3 有效, frame_p95 离散度 0.987%。
#
# 测试条件(影响可比性, 别混批):
#   - 红魔内置风扇全程开启
#   - 设备共享, 外面套 devlock:
#       /private/tmp/claude-501/devlock run wb-megacity -- bash run_megacity.sh ...
set -euo pipefail

LABEL="${1:?用法: run_megacity.sh <label> <outdir> <scene> <route> <frames> [capdur]}"
OUTDIR="${2:?缺输出目录}"
SCENE="${3:?缺 scene (city_flythrough|city_static)}"
ROUTE="${4:?缺 route 名}"
FRAMES="${5:?缺 frames}"
CAPDUR="${6:-12}"

SERIAL="${MEGACITY_SERIAL:-91253241019A}"
PKG=com.unity.megacity.metro
ACT=com.unity.megacity.MegacityMetro
WARMUP="${MEGACITY_WARMUP:-600}"
SETTLE="${MEGACITY_SETTLE:-7200}"
RESSCALE="${MEGACITY_RESSCALE:-1.0}"
VSYNC="${MEGACITY_VSYNC:-off}"

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
TOOLS="$ROOT/loop_v1/tools"
. "$TOOLS/pf_bin.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
COOL_TO="${MEGACITY_COOL_MC:-45000}"   # 45°C。refbench 用的 40000 在这台设备上够不到:
                                       # 连续测试烤过之后风扇下空闲底温就在 42-44°C,
                                       # 硬等只是白耗 12 分钟有界轮询然后照跑不误。

ashell() { adb -s "$SERIAL" shell "$@" </dev/null; }

mkdir -p "$OUTDIR"
APPFILES="/storage/emulated/0/Android/data/$PKG/files"

# 0) 路线在 APK 里(StreamingAssets), 不能 push —— 实测 push 进 app 外部目录的文件
#    应用读不到(scoped storage 下属主是 shell)。这里只留仓库那份作事后核对:
#    包里用的是哪条路线, 由输出 json 的 route.sha256 说了算。
ROUTE_SRC="$HERE/routes/$ROUTE.json"
[ -f "$ROUTE_SRC" ] || { echo "[$LABEL] 仓库里没有 $ROUTE.json, 无法核对包内路线"; exit 2; }
EXPECT_SHA=$(shasum -a 256 "$ROUTE_SRC" | cut -d" " -f1)

# 1) 温度回落轮询 (有界 360×2s), 保证每轮起点热态一致
T=999999
for _ in $(seq 1 360); do
  T=$(ashell "su -c 'cat /sys/class/kgsl/kgsl-3d0/temp'" 2>/dev/null | tr -d '\r' || true)
  [ -n "$T" ] && [ "$T" -le "$COOL_TO" ] && break
  sleep 2
done
echo "[$LABEL] 起跑 gpu_temp=${T}mC"

# 2) 轮内起始快照 + 清场
ashell "su -c 'sh /data/local/tmp/device_snapshot.sh'" > "$OUTDIR/snap_at_run.txt" 2>&1
ashell "am force-stop $PKG" >/dev/null 2>&1 || true
ashell "rm -f $APPFILES/megacity_out.json $APPFILES/megacity_started" >/dev/null 2>&1 || true

# 3) 录音权限双保险。Vivox 会弹权限框, 框一出应用被挂起、协程停摆 —— 首轮就栽在这。
#    harness 现在切单机模式绕开了 Vivox, 这里再授一次, 幂等, 不会因为已授权而失败。
ashell "pm grant $PKG android.permission.RECORD_AUDIO" >/dev/null 2>&1 || true
cp "$ROUTE_SRC" "$OUTDIR/route.json"

# 4) 启动并等渲染真正进入被测窗口 (harness 预热结束才落 megacity_started;
#    同样不用 logcat —— 这台设备的 logcat 会整个哑掉)
ashell "am start -W -n $PKG/$ACT \
  --es scene $SCENE --es run_id $LABEL --es frames $FRAMES --es route $ROUTE \
  --es warmup $WARMUP --es settle $SETTLE --es resscale $RESSCALE --es vsync $VSYNC" > "$OUTDIR/am.log" 2>&1

# 静置 + 预热可能很久(城市装完要时间), 上界放宽到 600×0.5s = 300s
STARTED=0
for _ in $(seq 1 600); do
  M=$(ashell "cat $APPFILES/megacity_started 2>/dev/null" | tr -d '\r' || true)
  [ -n "$M" ] && { STARTED=1; break; }
  P=$(ashell "pidof $PKG" 2>/dev/null | tr -d '\r' || true)
  [ -z "$P" ] && { echo "[$LABEL] 应用在进入被测窗口前就退了"; break; }
  sleep 0.5
done
[ "$STARTED" = 1 ] || { echo "[$LABEL] 应用未进入被测窗口"; ashell "am force-stop $PKG" || true; exit 2; }

# 5) 稳态窗口内采集
ashell "su -c 'sh /data/local/tmp/ftrace_capture.sh $CAPDUR /data/local/tmp/mc_$LABEL.txt'" > "$OUTDIR/capture.log" 2>&1
adb -s "$SERIAL" pull "/data/local/tmp/mc_$LABEL.txt" "$OUTDIR/trace.txt" >/dev/null
ashell "rm -f /data/local/tmp/mc_$LABEL.txt" || true

# 6) 等自退 (存活轮询, 有界 300s —— 帧数多时窗口比 refbench 长)
EXITED=0
for _ in $(seq 1 300); do
  P=$(ashell "pidof $PKG" 2>/dev/null | tr -d '\r' || true)
  [ -z "$P" ] && { EXITED=1; break; }
  sleep 1
done
[ "$EXITED" = 1 ] || { echo "[$LABEL] 应用超时未自退"; ashell "am force-stop $PKG" || true; exit 2; }

# 7) 收证据
adb -s "$SERIAL" pull "$APPFILES/megacity_out.json" "$OUTDIR/megacity_out.json" >/dev/null
ashell "su -c 'sh /data/local/tmp/device_snapshot.sh'" > "$OUTDIR/snap_after_run.txt" 2>&1

# 提交线程名由载体自报, 不写死 —— Unity 的图形线程名不归我们改, 只归我们如实记录
COMM=$(python3 -c "import json;print(json.load(open('$OUTDIR/megacity_out.json'))['render_thread_comm'])")
case "$COMM" in
  UNRESOLVED*) echo "[$LABEL] 载体没能定位图形提交线程: $COMM"; exit 2 ;;
esac
echo "[$LABEL] comm=$COMM"

"$PF" parse-trace "$OUTDIR/trace.txt" --comm "$COMM" > "$OUTDIR/summary.json"
"$PF" attribute "$OUTDIR/summary.json" > "$OUTDIR/attribution.json"

# 8) 轮内有效性判定
EXPECT_SHA="$EXPECT_SHA" python3 - "$OUTDIR" "$LABEL" <<'PYEOF'
import json, sys, os
out, label = sys.argv[1], sys.argv[2]
s = json.load(open(os.path.join(out, "summary.json")))
m = json.load(open(os.path.join(out, "megacity_out.json")))

bad = []
if s["n_thermal_events"] != 0:            bad.append(f"热事件={s['n_thermal_events']}")
if not m["clean_exit"]:                   bad.append("clean_exit=false:" + m.get("abort_reason", "?"))
if m["graphics"]["api"] != "Vulkan":      bad.append("图形 API=" + m["graphics"]["api"] + " (不是 Vulkan, 这包白打了)")

st = m.get("streaming", {})
if st.get("ready") != "yes":
    bad.append("城市没装完 streaming.ready=" + str(st.get("ready")) +
               " settle_frames=" + str(st.get("settle_frames")) +
               " entities=" + str(st.get("entities")))
expect = os.environ.get("EXPECT_SHA", "")
if expect and m["route"]["sha256"] != expect:
    bad.append("包内路线与仓库不一致: 包=" + m["route"]["sha256"][:12] + " 仓库=" + expect[:12])
# pace=time (harness 0.3.0 起的缺省) 按时间跑满 route_seconds, 帧数随帧率变, 不核对帧数;
# 旧包没有 pace 字段, 等同 pace=frame
fp = m.get("frame_pacing", {})
if m["params"].get("pace", "frame") == "frame" and m["frames_rendered"] != m["params"]["frames"]:
    bad.append(f"帧数不足 {m['frames_rendered']}/{m['params']['frames']}")
if m["graphics"].get("target_frame_rate") == -1 and m["params"].get("vsync") == "off":
    bad.append("vsync=off 但 targetFrameRate=-1 (安卓上等于锁 30 fps, 旧包)")

if bad:
    open(os.path.join(out, "INVALID"), "w").write("; ".join(bad) + "\n")
    print(f"[{label}] 无效轮: " + "; ".join(bad))
    sys.exit(3)

print(f"[{label}] fps={s['fps_mean']} spf={s['submits_per_frame']} "
      f"p50={s['frame_p50']}ms p95={s['frame_p95']}ms gpu={s['gpu_active_mean']}ms bw={s['bw_median']} "
      f"route={m['route']['name']}@{m['route']['sha256'][:12]} "
      f"cap: target={fp.get('target_frame_rate')} refresh={fp.get('display_refresh_hz')}Hz "
      f"warmup_fps={fp.get('warmup_observed_fps')} pace={fp.get('pace')} "
      f"entities={m.get('streaming',{}).get('entities')}")
PYEOF
