#!/bin/bash
# run_once.sh <标签> <输出目录> [旋钮设置脚本] — 跑一轮"负载 + 采集", 落一份证据
#
# 时序设计:
#   t=0     起 phonefarm script (后台), 负载脚本自己有 3s 静置 + 3×12s 转镜头
#   t=LEAD  起 ftrace 采集, 落在"已经稳定转起来"的窗口内, 避开启动与收尾
#   t=..    等两边都结束, 拉回 trace, 解析成 summary.json
#
# 每轮都是独立目录, 原始 trace 一并归档 —— 判据 5 的离线回放靠的就是这份原始文本。
set -euo pipefail

LABEL="${1:?用法: run_once.sh <标签> <输出目录> [旋钮脚本]}"
OUTDIR="${2:?缺输出目录}"
KNOB="${3:-}"

# 下面五项都可由环境变量覆盖, 缺省值即原先的硬编码值 —— 这样 auto/autoloop.py
# 能在 git worktree 里复用同一个脚本, 不必复制一份。
SERIAL="${SERIAL:-91253241019A}"
PKG="${PKG:-com.miHoYo.Yuanshen}"
ROOT="${ROOT:-/Users/mac/projects/phonefarm}"
WL="${WL:-$ROOT/loop_v1/scripts/workload_spin_v1.json}"
LEAD="${LEAD:-6}"       # 负载起跑后等几秒再开采 (让转镜头进入稳态)
CAPDUR="${CAPDUR:-30}"  # 采集时长, 必须 < 负载剩余时长
# 按脚本自身位置解析二进制: ROOT 可被环境变量指到别处, 那边不一定有这个文件
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pf_bin.sh"
export PATH="$PATH:/Users/mac/Library/Android/sdk/platform-tools"

mkdir -p "$OUTDIR"
echo "── [$LABEL] 开始 ──"

# 旋钮: 有就先施加 (旋钮脚本自己负责幂等与记录)
if [ -n "$KNOB" ]; then
  echo "[$LABEL] 施加旋钮: $KNOB"
  adb -s "$SERIAL" shell "su -c 'sh $KNOB'" 2>&1 | sed "s/^/[$LABEL][旋钮] /"
fi

# 轮内起始快照 (证明旋钮确实生效, 也为回滚核验留底)
adb -s "$SERIAL" shell "su -c 'sh /data/local/tmp/device_snapshot.sh'" > "$OUTDIR/snap_at_run.txt" 2>&1

# 1) 负载后台起跑。不传 --app: script 模式的 --app 只用来开遥测, 每步之后跑 dumpsys meminfo /
#    batterystats 等重活 —— 落在采集窗里就是额外功耗, 还把第一次转镜头推迟到 ~8 s 之后
#    (2026-10-23 红魔实测: 带 --app 3×12 s 的脚本跑了 68 s, 第 3–8 s 镜头一动不动)
( cd "$ROOT" && "$PF" script --task "loop_v1_$LABEL" --serial "$SERIAL" \
    --no-screen "$WL" ) > "$OUTDIR/workload.log" 2>&1 &
WL_PID=$!

# 2) 等负载进入稳态后开采
sleep "$LEAD"
echo "[$LABEL] 采集 ${CAPDUR}s ..."
adb -s "$SERIAL" shell "su -c 'sh /data/local/tmp/ftrace_capture.sh $CAPDUR /data/local/tmp/loop_v1_$LABEL.txt $PKG'" \
    > "$OUTDIR/capture.log" 2>&1

# 3) 等负载收尾
WL_RC=0
wait "$WL_PID" || WL_RC=$?
printf '%s\n' "$WL_RC" > "$OUTDIR/workload_exit.txt"
if [ "$WL_RC" -ne 0 ]; then echo "[$LABEL] 警告: 负载脚本非零退出 rc=$WL_RC"; fi

# 4) 拉回原始证据并解析
adb -s "$SERIAL" pull "/data/local/tmp/loop_v1_$LABEL.txt" "$OUTDIR/trace.txt" >/dev/null 2>&1
adb -s "$SERIAL" shell "rm -f /data/local/tmp/loop_v1_$LABEL.txt" >/dev/null 2>&1

"$PF" parse-trace "$OUTDIR/trace.txt" > "$OUTDIR/summary.json"

# 5) 轮末快照 (与轮内对比, 确认采集器自己没留痕)
adb -s "$SERIAL" shell "su -c 'sh /data/local/tmp/device_snapshot.sh'" > "$OUTDIR/snap_after_run.txt" 2>&1

SZ=$(wc -c < "$OUTDIR/trace.txt" | tr -d ' ')
echo "[$LABEL] 完成: trace ${SZ}B"
python3 -c "
import json,sys
d=json.load(open('$OUTDIR/summary.json'))
print(f\"[$LABEL] fps={d['fps_mean']} spf={d['submits_per_frame']} \"
      f\"p50={d['frame_p50']}ms p95={d['frame_p95']}ms gpu={d['gpu_active_mean']}ms \"
      f\"bw={d['bw_median']} 热事件={d['n_thermal_events']}\")
"
