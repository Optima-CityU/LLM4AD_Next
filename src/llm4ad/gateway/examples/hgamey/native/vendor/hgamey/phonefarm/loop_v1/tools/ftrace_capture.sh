#!/bin/sh
# ftrace_capture.sh <duration_s> <out_path> [包名] — 设备端执行, 需 root
#
# 给了包名时, 在采集开始和结束各记一次该进程的 pid 与全部线程号 (取并集), 写进
# `#loop_v1_meta target_pid=... target_tids=...`。线程名不认进程 (两个 Unity 应用的渲染线程
# 同名, 截断的 `<...>` 谁都可能), 解析器靠这份线程号只收目标应用自己的提交。
#
# 采一段 ftrace 文本, 同时拿到:
#   帧节奏  : adreno_cmdbatch_submitted/retired (游戏渲染线程的 GPU 提交与退役)
#   GPU 归因: retired 事件里的 start/retire 时间戳 = 该次提交的真实 GPU 执行时长
#   带宽    : kgsl_buslevel 的 avg_bw (直接观测, 不是推断)
#   频率/热 : kgsl_pwrlevel / kgsl_gpu_frequency / kgsl_thermal_constraint / kgsl_clock_throttling
#
# 纪律: 优先在私有 ftrace 实例 (instances/phonefarm_kgsl) 里采, 只开本脚本的事件,
#       采完 rmdir 整个实例 —— 不碰全局 tracing 的任何状态。
#       为什么: 全局 buffer 上别人开着的事件 (2026-10-11 实测: 系统 perfetto 配置让
#       sched_* / sde_* 等 49 个事件常开, 全局每 CPU 只有 8 MB 且 65536 写不进去) 会在
#       一两秒内把环形缓冲冲满, 10 s 的采集只剩最后约 1 s, 帧数为 0 也不报错。
#       内核不支持实例时退回全局 buffer, 走原来的四项存档/还原
#       (tracing_on / current_tracer / buffer_size_kb / set_event), 只关自己开的事件。
#       每次采集把各 CPU 的 overrun (被冲掉的事件数) 写进 trace 头的注释行, 便于事后核对。
set -u

DUR="${1:-10}"
OUT="${2:-/data/local/tmp/loop_v1_trace.txt}"
PKG="${3:-}"

# 目标进程的 pid 与线程号 (逗号分隔)。没给包名或进程不在时为空。
target_pid() { [ -n "$PKG" ] && pidof "$PKG" 2>/dev/null | cut -d' ' -f1; }
target_tids() { [ -n "$1" ] && ls "/proc/$1/task" 2>/dev/null; }
ROOT=/sys/kernel/tracing
INST=$ROOT/instances/phonefarm_kgsl
if [ -d "$INST" ] || mkdir "$INST" 2>/dev/null; then
  T=$INST; MODE=instance
else
  T=$ROOT; MODE=global
fi

# 本脚本需要的事件 (kgsl 子系统)
EVENTS="adreno_cmdbatch_submitted adreno_cmdbatch_retired kgsl_buslevel kgsl_pwrlevel kgsl_gpu_frequency kgsl_thermal_constraint kgsl_clock_throttling kgsl_bcl_clock_throttling"

# ── 存档 ──
SAVE_ON=$(cat "$T/tracing_on" 2>/dev/null)
SAVE_TRACER=$(cat "$T/current_tracer" 2>/dev/null)
SAVE_BUF=$(cat "$T/buffer_size_kb" 2>/dev/null)
SAVE_EVENTS=$(cat "$T/set_event" 2>/dev/null)

# 记录哪些事件是"我开的"(原本没开的才算), 退出时只关这些
MINE=""
for e in $EVENTS; do
  if ! echo "$SAVE_EVENTS" | grep -qx "kgsl:$e"; then
    MINE="$MINE $e"
  fi
done

restore() {
  if [ "$MODE" = instance ]; then
    echo 0 > "$T/tracing_on" 2>/dev/null
    for e in $EVENTS; do echo 0 > "$T/events/kgsl/$e/enable" 2>/dev/null; done
    echo > "$T/trace" 2>/dev/null
    rmdir "$T" 2>/dev/null
    return
  fi
  echo 0 > "$T/tracing_on" 2>/dev/null
  for e in $MINE; do
    echo 0 > "$T/events/kgsl/$e/enable" 2>/dev/null
  done
  [ -n "$SAVE_TRACER" ] && echo "$SAVE_TRACER" > "$T/current_tracer" 2>/dev/null
  [ -n "$SAVE_BUF" ]    && echo "$SAVE_BUF"    > "$T/buffer_size_kb" 2>/dev/null
  echo > "$T/trace" 2>/dev/null
  [ -n "$SAVE_ON" ]     && echo "$SAVE_ON"     > "$T/tracing_on" 2>/dev/null
}
trap 'restore' EXIT INT TERM

# ── 布置 ──
echo 0 > "$T/tracing_on"
echo nop > "$T/current_tracer"
# 实例的时钟与全局保持一致 (全局常被设成 boot; 实例缺省 local, 时间戳会与 /proc/uptime 对不上)
if [ "$MODE" = instance ]; then
  CLK=$(sed -n 's/.*\[\(.*\)\].*/\1/p' "$ROOT/trace_clock" 2>/dev/null)
  [ -n "$CLK" ] && echo "$CLK" > "$T/trace_clock" 2>/dev/null
fi
# 每 CPU 64MB; 写不进去 (内存不够) 就退到 16MB —— 实例里只有 kgsl 事件, 60s @ 120fps 也只要几 MB
echo 65536 > "$T/buffer_size_kb" 2>/dev/null || echo 16384 > "$T/buffer_size_kb" 2>/dev/null
echo > "$T/trace"

for e in $EVENTS; do
  echo 1 > "$T/events/kgsl/$e/enable" 2>/dev/null || echo "WARN: 无法启用 kgsl:$e" >&2
done

# ── 采集 ──
PID0=$(target_pid); TIDS0=$(target_tids "$PID0")
echo 1 > "$T/tracing_on"
MARK_START=$(cat /proc/uptime | cut -d' ' -f1)
sleep "$DUR"
echo 0 > "$T/tracing_on"
MARK_END=$(cat /proc/uptime | cut -d' ' -f1)
PID1=$(target_pid); TIDS1=$(target_tids "$PID1")
TIDS=$(printf '%s\n%s\n' "$TIDS0" "$TIDS1" | grep -E '^[0-9]+$' | sort -un | tr '\n' ',' | sed 's/,$//')

# ── 落盘 (带元信息头, 供离线回放自证) ──
{
  echo "#loop_v1_meta capture_start_uptime=$MARK_START capture_end_uptime=$MARK_END duration_req=$DUR"
  if [ -n "$PKG" ]; then
    echo "#loop_v1_meta target_pkg=$PKG target_pid=$PID1 target_pid_start=$PID0 target_tids=$TIDS"
  fi
  echo "#loop_v1_meta gpu_model=$(cat /sys/class/kgsl/kgsl-3d0/gpu_model 2>/dev/null)"
  echo "#loop_v1_meta ddr_cur_khz=$(cat /sys/devices/system/cpu/bus_dcvs/DDR/cur_freq 2>/dev/null) ddr_max_khz=$(cat /sys/devices/system/cpu/bus_dcvs/DDR/hw_max_freq 2>/dev/null)"
  echo "#loop_v1_meta ddr_boost_khz=$(cat /sys/devices/system/cpu/bus_dcvs/DDR/boost_freq 2>/dev/null) llcc_boost_khz=$(cat /sys/devices/system/cpu/bus_dcvs/LLCC/boost_freq 2>/dev/null)"
  echo "#loop_v1_meta gpu_busy=$(cat /sys/class/kgsl/kgsl-3d0/gpubusy 2>/dev/null) gpu_clk=$(cat /sys/class/kgsl/kgsl-3d0/gpuclk 2>/dev/null)"
  echo "#loop_v1_meta thermal_pwrlevel=$(cat /sys/class/kgsl/kgsl-3d0/thermal_pwrlevel 2>/dev/null) min_pwrlevel=$(cat /sys/class/kgsl/kgsl-3d0/min_pwrlevel 2>/dev/null)"
  # 不进 loop_v1_meta (解析器不看), 只供人核对: 采在哪个 buffer、每 CPU 多大、冲掉了多少
  echo "# phonefarm trace_mode=$MODE buffer_size_kb=$(cat "$T/buffer_size_kb" 2>/dev/null) trace_clock=$(sed -n 's/.*\[\(.*\)\].*/\1/p' "$T/trace_clock" 2>/dev/null)"
  for c in "$T"/per_cpu/cpu*; do
    echo "# phonefarm $(basename "$c") $(grep -E '^overrun' "$c/stats" 2>/dev/null)"
  done
  cat "$T/trace"
} > "$OUT" 2>/dev/null

LINES=$(wc -l < "$OUT" 2>/dev/null)
echo "CAPTURE_OK lines=$LINES out=$OUT"
# restore 由 trap 执行
