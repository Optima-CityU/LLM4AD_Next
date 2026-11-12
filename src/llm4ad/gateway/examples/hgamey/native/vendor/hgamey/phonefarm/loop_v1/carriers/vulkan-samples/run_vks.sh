#!/bin/bash
# run_vks.sh <label> <outdir> <sample> <config_idx> <frames> [capdur]
#
# 驱动 Khronos Vulkan-Samples 的一个 performance 样例跑一轮:
#   温度回落轮询 → 起始快照 → 按固定 config 启动 → 等渲染真正开始 → 采集 →
#   等固定帧数自退 → 收证据 → 解析 + 归因 → 轮内有效性判定
#
# 和 ../refbench/run_refbench.sh 是同一套纪律, 差别只在契约细节 (见 README):
#   · 靶子不是我们写的, 没有 `*_started` 标记 → 用 fps_logger 落进日志文件的第一行
#     "FPS:" 当"渲染已开始"信号 (第一条在进循环后约 0.5s)
#   · 靶子不自报 JSON → clean_exit 由"进程在超时前自己消失"判定
#   · 提交线程名不固定 → 采完当场用 phonefarm vks-pick-comm 从数据里认, 不写死
#
# 退出码: 0=有效轮  2=硬失败  3=无效轮 (热事件 / 非干净退出 / 提交线程不唯一)
set -euo pipefail

LABEL="${1:?用法: run_vks.sh <label> <outdir> <sample> <config_idx> <frames> [capdur]}"
OUTDIR="${2:?缺输出目录}"
SAMPLE="${3:?缺 sample id}"
CONFIG="${4:?缺 config 索引}"
FRAMES="${5:?缺 frames}"
CAPDUR="${6:-12}"

SERIAL="${VKS_SERIAL:-91253241019A}"
PKG=com.khronos.vulkan_samples
ACT="$PKG/.SampleLauncherActivity"
HERE="$(cd "$(dirname "$0")" && pwd)"
TOOLS="$(cd "$HERE/../../tools" && pwd)"
. "$TOOLS/pf_bin.sh"
APPFILES="/storage/emulated/0/Android/data/$PKG/files"
COOL_TO="${VKS_COOL_MC:-40000}"
COMM_SHARE_MIN="${VKS_COMM_SHARE_MIN:-0.80}"
# vsync 缺省关: 面板 120Hz 会把帧时间钉死在 8.3ms, 开关差异就只剩 GPU 忙碌与带宽两项,
# 帧时间这个主指标直接失去分辨力。关掉后出帧速度才反映这一帧真实的 GPU 成本。
VSYNC="${VKS_VSYNC:-OFF}"
# 等待"真实渲染稳态"的上界(秒)。本机窗口合成要约 9.6s, 给 30s 余量。
# 真正的就绪判据在 src/vks.rs 里 (帧率阶跃), 这里只是个上界。
SETTLE="${VKS_SETTLE_S:-30}"

ashell() { adb -s "$SERIAL" shell "$@" </dev/null; }

mkdir -p "$OUTDIR"

# 1) 温度回落轮询 (有界 360×2s), 保证每轮起点热态一致
T=999999
for _ in $(seq 1 360); do
  T=$(ashell "su -c 'cat /sys/class/kgsl/kgsl-3d0/temp'" 2>/dev/null | tr -d '\r' || true)
  [ -n "$T" ] && [ "$T" -le "$COOL_TO" ] && break
  sleep 2
done
echo "[$LABEL] 起跑 gpu_temp=${T}mC sample=$SAMPLE config=$CONFIG vsync=$VSYNC frames=$FRAMES"

# 2) 唤醒 + 解锁。这一步不是保险, 是必须的: 灭屏时 surface 不合成, 样例会以两千多 fps
#    "空跑" —— 日志里 FPS 好看, trace 里一条 GPU 提交都没有(2026-09-23 实测踩过)。
ashell "input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard" >/dev/null 2>&1 || true

# 3) 起始快照 + 清场
ashell "su -c 'sh /data/local/tmp/device_snapshot.sh'" > "$OUTDIR/snap_at_run.txt" 2>&1
ashell "am force-stop $PKG" >/dev/null 2>&1 || true
ashell "rm -f $APPFILES/run.log" >/dev/null 2>&1 || true

# 3) 启动: 开关状态由 --config 钉死, 帧数由 --stop-after-frame 钉死, GUI 关掉
CMD="sample $SAMPLE --config $CONFIG --stop-after-frame $FRAMES --hideui --force-close --vsync $VSYNC --log-fps --log-file $APPFILES/run.log"
echo "$CMD" > "$OUTDIR/cmd.txt"
ashell "am start -n $ACT --es cmd \"$CMD\"" > "$OUTDIR/am.log" 2>&1

# 4) 等渲染真正开始 —— 日志里出现第一条 FPS 行才算
#    (这台设备的 logcat 会整个哑掉, 所以进度信号一律走文件系统, 与 refbench 同)
STARTED=0
for _ in $(seq 1 120); do
  M=$(ashell "grep -c FPS: $APPFILES/run.log 2>/dev/null" | tr -d '\r' || true)
  [ -n "$M" ] && [ "$M" != "0" ] && { STARTED=1; break; }
  sleep 0.5
done
[ "$STARTED" = 1 ] || {
  ashell "cat $APPFILES/run.log 2>/dev/null" > "$OUTDIR/run.log" 2>&1 || true
  echo "[$LABEL] 应用未进入渲染"; ashell "am force-stop $PKG" || true; exit 2; }

# 4b) 等"真实渲染稳态" —— 不是等固定秒数, 是等帧率序列里那一级阶跃下降 (见 src/vks.rs)。
#     有界 SETTLE 秒; 超时也照常往下走, 因为第 8 步的 crosscheck 会兜底判无效。
READY=0
for _ in $(seq 1 "$SETTLE"); do
  ashell "cat $APPFILES/run.log 2>/dev/null" > "$OUTDIR/run_pre.log" 2>/dev/null || true
  if "$PF" vks-ready "$OUTDIR/run_pre.log"; then READY=1; break; fi
  sleep 1
done
echo "[$LABEL] 进入稳态: ready=$READY"

# 4c) 风扇状态存证。红魔的主动散热风扇自身耗电会进功耗读数, 而它**不在** device_snapshot.sh
#     的 38 行快照里, 所以单独采一份。同一组对照里两臂必须是同一风扇状态 ——
#     vks-report 会跨轮比对, 不一致就报警, 避免把"风扇开/关"混进臂间差异。
python3 - "$(ashell "su -c 'cat /sys/kernel/fan/fan_enable'" 2>/dev/null | tr -d '\r')" \
         "$(ashell "su -c 'cat /sys/kernel/fan/fan_speed_level'" 2>/dev/null | tr -d '\r')" \
         > "$OUTDIR/fan.json" <<'PYFAN'
import json, sys
en = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "" else None
lv = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "" else None
json.dump({"fan_enable": en, "fan_speed_level": lv}, sys.stdout)
PYFAN
echo "[$LABEL] 风扇: $(cat "$OUTDIR/fan.json")"

# 4d) 面板刷新率存证。开着 vsync 时应用帧率就是它, 本机面板自适应刷新, 各轮不一样的话
#     逐帧指标就没有可比性。run_ab.sh 会在整批期间把它钉死, 这里逐轮记下实际值。
printf '{"min_refresh_rate":"%s","peak_refresh_rate":"%s"}\n' \
  "$(ashell "settings get system min_refresh_rate" 2>/dev/null | tr -d '\r')" \
  "$(ashell "settings get system peak_refresh_rate" 2>/dev/null | tr -d '\r')" > "$OUTDIR/display.json"

# 5) 稳态窗口内采集 (ftrace_capture.sh 自带四项状态存档还原)
#    采集窗的**设备墙钟**边界要记下来: 日志里的 FPS 行带的是设备本地时间, 而 trace 里
#    的是 ftrace 时钟, 两者对不上。记下边界后对账才能只拿同一段时间的
#    FPS 样本去和 trace 对账 —— 否则拿整段运行的中位数去比 12 秒的窗口, 本来好的轮
#    也会被判掉(2026-09-23 实测就是这么误杀了好几轮)。
CAP_T0=$(ashell "date '+%Y-%m-%d %H:%M:%S'" | tr -d '\r')
ashell "su -c 'sh /data/local/tmp/ftrace_capture.sh $CAPDUR /data/local/tmp/vks_$LABEL.txt'" > "$OUTDIR/capture.log" 2>&1
CAP_T1=$(ashell "date '+%Y-%m-%d %H:%M:%S'" | tr -d '\r')
printf '{"cap_start":"%s","cap_end":"%s"}\n' "$CAP_T0" "$CAP_T1" > "$OUTDIR/window.json"
adb -s "$SERIAL" pull "/data/local/tmp/vks_$LABEL.txt" "$OUTDIR/trace.txt" >/dev/null
ashell "rm -f /data/local/tmp/vks_$LABEL.txt" || true

# 6) 等自退, 有界 240s。
#    判据用框架自己在收尾时落的那行 "Total device memory leaked", **不用 pidof**:
#    Android 在所有 activity 结束后会把进程留成 cached process, pidof 仍然有值,
#    拿它当结束信号会每轮空等满 240s 再判成无效 (2026-09-23 实测踩过)。
EXITED=0
for _ in $(seq 1 240); do
  M=$(ashell "grep -c 'Total device memory leaked' $APPFILES/run.log 2>/dev/null" | tr -d '\r' || true)
  [ -n "$M" ] && [ "$M" != "0" ] && { EXITED=1; break; }
  sleep 1
done
ashell "am force-stop $PKG" >/dev/null 2>&1 || true
ashell "cat $APPFILES/run.log 2>/dev/null" > "$OUTDIR/run.log" 2>&1 || true
ashell "su -c 'sh /data/local/tmp/device_snapshot.sh'" > "$OUTDIR/snap_after_run.txt" 2>&1
if [ "$EXITED" != 1 ]; then
  ashell "am force-stop $PKG" || true
  echo "clean_exit=False" > "$OUTDIR/INVALID"
  echo "[$LABEL] 无效轮: 超时未自退"
  exit 3
fi

# 7) 认提交线程 → 解析 → 归因
"$PF" vks-pick-comm "$OUTDIR/trace.txt" > "$OUTDIR/comm.json" || {
  echo "no_cmdbatch_events" > "$OUTDIR/INVALID"; echo "[$LABEL] 无效轮: trace 里没有 GPU 提交事件"; exit 3; }
NSUB=$(python3 -c "import json;print(json.load(open('$OUTDIR/comm.json'))['total'])")
# 采集窗内的提交数下限: 真渲染时每帧至少一次提交, 12s 怎么也不止几十次。
# 低于这个数说明这一轮根本没在出帧(灭屏空跑 / surface 丢失), 证据保留但不进样本。
if [ "$NSUB" -lt "${VKS_MIN_SUBMITS:-200}" ]; then
  echo "n_submits=$NSUB < ${VKS_MIN_SUBMITS:-200} (采集窗内几乎没有 GPU 提交)" > "$OUTDIR/INVALID"
  echo "[$LABEL] 无效轮: 采集窗内只有 $NSUB 次 GPU 提交"
  exit 3
fi
COMM=$(python3 -c "import json;print(json.load(open('$OUTDIR/comm.json'))['comm'])")
SHARE=$(python3 -c "import json;print(json.load(open('$OUTDIR/comm.json'))['share'])")
OK=$(python3 -c "print(1 if $SHARE >= $COMM_SHARE_MIN else 0)")
if [ "$OK" != "1" ]; then
  echo "comm_share=$SHARE < $COMM_SHARE_MIN (提交线程不唯一)" > "$OUTDIR/INVALID"
  echo "[$LABEL] 无效轮: 提交线程不唯一, 分布见 comm.json"
  exit 3
fi
"$PF" parse-trace "$OUTDIR/trace.txt" --comm "$COMM" > "$OUTDIR/summary.json"
"$PF" attribute "$OUTDIR/summary.json" > "$OUTDIR/attribution.json"

# 8) 交叉校验: 内核侧提交节奏 vs 应用侧自报帧率 (两个独立来源对账, 细节见 src/vks.rs)
"$PF" vks-crosscheck "$OUTDIR/run.log" "$OUTDIR/summary.json" "$OUTDIR/window.json" > "$OUTDIR/crosscheck.json"
WINDOWED=$(python3 -c "import json;print(json.load(open('$OUTDIR/crosscheck.json'))['windowed'])")
OK=$(python3 -c "import json;print(1 if json.load(open('$OUTDIR/crosscheck.json'))['in_band'] else 0)")
RATIO=$(python3 -c "import json;print(json.load(open('$OUTDIR/crosscheck.json'))['ratio_to_log_fps'])")
if [ "$WINDOWED" != "True" ]; then
  # 采集窗内一条 FPS 行都没落下 → 这次对账拿的是整段运行的中位数, 不可信, 不放行
  echo "crosscheck 没能按采集窗过滤 (窗内无 FPS 样本)" > "$OUTDIR/INVALID"
  echo "[$LABEL] 无效轮: 采集窗内没有应用侧帧率样本"
  exit 3
fi
if [ "$OK" != "1" ]; then
  echo "crosscheck 提交速率/应用帧率=$RATIO 不在 [0.5,6.0] 带内 (内核侧与应用侧差一个数量级)" > "$OUTDIR/INVALID"
  echo "[$LABEL] 无效轮: 交叉校验不过 ratio=$RATIO"
  exit 3
fi

# 9) 轮内有效性: 采集窗有热事件 → 无效 (证据保留, 不进样本)
NT=$(python3 -c "import json;print(json.load(open('$OUTDIR/summary.json'))['n_thermal_events'])")
if [ "$NT" != "0" ]; then
  echo "n_thermal=$NT" > "$OUTDIR/INVALID"
  echo "[$LABEL] 无效轮: 热事件=$NT"
  exit 3
fi

python3 - "$OUTDIR/summary.json" "$LABEL" "$COMM" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"[{sys.argv[2]}] comm={sys.argv[3]} fps={d['fps_mean']} spf={d['submits_per_frame']} "
      f"p50={d['frame_p50']}ms p95={d['frame_p95']}ms gpu={d['gpu_active_mean']}ms bw={d['bw_median']}")
PYEOF
