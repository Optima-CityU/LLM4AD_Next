#!/bin/bash
# run_anki_sponza.sh <label> <outdir> <campath> <render_scaling> <postfx> <frames> [capdur]
#
# anki-sponza 载体的专属驱动面, 形状照抄 loop_v1/refbench/run_refbench.sh:
# 温度回落轮询 → 启动 → 采集 raw ftrace kgsl → 等自退 → 收证据。
# 采集/解析/归因全部复用 ../../tools/ 的纯函数链, 这里不做任何统计。
#
# 退出码: 0=有效轮  2=硬失败  3=无效轮 (采集窗内有热事件或非干净退出, 证据保留)
# 时序全部用存活/状态轮询, 无时长盲等。
#
# ⚠ 状态: **未在设备上跑通过** —— APK 还没构建出来 (见 ../README.md「当前状态」)。
#   本文件与 ../contract/launch.json 一起冻形状, 等 APK 落地后第一件事就是验它。
set -euo pipefail

LABEL="${1:?用法: run_anki_sponza.sh <label> <outdir> <campath> <render_scaling> <postfx> <frames> [capdur]}"
OUTDIR="${2:?缺输出目录}"
CAMPATH="${3:?缺 campath (orbit|static)}"
SCALING="${4:?缺 render_scaling}"
POSTFX="${5:?缺 postfx (on|off)}"
FRAMES="${6:?缺 frames}"
CAPDUR="${7:-12}"

SERIAL="${ANKI_SERIAL:-91253241019A}"
PKG=org.anki.Sponza
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"   # -> phonefarm 仓库根
TOOLS="$ROOT/loop_v1/tools"
. "$TOOLS/pf_bin.sh"
COOL_TO="${ANKI_COOL_MC:-40000}"
POSTFX_SPV="${ANKI_POSTFX_SPV:-/data/local/tmp/candidate.spv}"

ashell() { adb -s "$SERIAL" shell "$@" </dev/null; }

mkdir -p "$OUTDIR"
APPFILES="/storage/emulated/0/Android/data/$PKG/files"

# 1) 温度回落轮询 (有界: 360 次 x 2s), 保证每轮起点热态一致
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
ashell "rm -f $APPFILES/anki_sponza_out.json $APPFILES/anki_sponza_started" >/dev/null 2>&1 || true

# 3) 启动并等渲染真正开始
#    靶子进渲染循环前落 anki_sponza_started 标记; 不用 logcat —— 这台设备的
#    logcat 会整个哑掉, 进度信号全走文件系统 (和 refbench 同一条纪律)。
EXTRA_POSTFX=""
[ "$POSTFX" = "on" ] && EXTRA_POSTFX="--es knob.postfx on --es postfx.shader $POSTFX_SPV"
# shellcheck disable=SC2086
ashell "am start -W -n $PKG/android.app.NativeActivity \
  --es run_id $LABEL --es frames $FRAMES --es campath $CAMPATH \
  --es render_scaling $SCALING $EXTRA_POSTFX" > "$OUTDIR/am.log" 2>&1

STARTED=0
for _ in $(seq 1 60); do
  M=$(ashell "cat $APPFILES/anki_sponza_started 2>/dev/null" | tr -d '\r' || true)
  [ -n "$M" ] && { STARTED=1; break; }
  sleep 0.5
done
[ "$STARTED" = 1 ] || { echo "[$LABEL] 应用未进入渲染"; ashell "am force-stop $PKG" || true; exit 2; }

# 4) 稳态窗口内采集 (ftrace_capture.sh 自带四项状态存档还原)
ashell "su -c 'sh /data/local/tmp/ftrace_capture.sh $CAPDUR /data/local/tmp/anki_$LABEL.txt'" > "$OUTDIR/capture.log" 2>&1
adb -s "$SERIAL" pull "/data/local/tmp/anki_$LABEL.txt" "$OUTDIR/trace.txt" >/dev/null
ashell "rm -f /data/local/tmp/anki_$LABEL.txt" || true

# 5) 等应用渲染完固定帧数自退 (存活轮询, 有界 180s)
EXITED=0
for _ in $(seq 1 180); do
  P=$(ashell "pidof $PKG" 2>/dev/null | tr -d '\r' || true)
  [ -z "$P" ] && { EXITED=1; break; }
  sleep 1
done
[ "$EXITED" = 1 ] || { echo "[$LABEL] 应用超时未自退"; ashell "am force-stop $PKG" || true; exit 2; }

# 6) 收证据: 靶子自报 JSON + 轮末快照 + 解析
adb -s "$SERIAL" pull "$APPFILES/anki_sponza_out.json" "$OUTDIR/anki_sponza_out.json" >/dev/null
ashell "su -c 'sh /data/local/tmp/device_snapshot.sh'" > "$OUTDIR/snap_after_run.txt" 2>&1
"$PF" parse-trace "$OUTDIR/trace.txt" --comm AnkiDrv > "$OUTDIR/summary.json"

# 7) 轮内有效性判定: 采集窗有热事件 / 非干净退出 → 无效 (证据保留, 不进样本)
NT=$(python3 -c "import json;print(json.load(open('$OUTDIR/summary.json'))['n_thermal_events'])")
CLEAN=$(python3 -c "import json;print(json.load(open('$OUTDIR/anki_sponza_out.json'))['clean_exit'])")
if [ "$NT" != "0" ] || [ "$CLEAN" != "True" ]; then
  echo "n_thermal=$NT clean_exit=$CLEAN" > "$OUTDIR/INVALID"
  echo "[$LABEL] 无效轮: 热事件=$NT clean=$CLEAN"
  exit 3
fi

python3 - "$OUTDIR/summary.json" "$LABEL" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"[{sys.argv[2]}] fps={d['fps_mean']} spf={d['submits_per_frame']} "
      f"p50={d['frame_p50']}ms p95={d['frame_p95']}ms gpu={d['gpu_active_mean']}ms bw={d['bw_median']}")
PYEOF
