#!/bin/sh
# device_snapshot.sh — 设备可变状态快照 (判据 4: 退出后逐位相等)
#
# 只读采集本闭环可能触碰的每一处可变状态。输出为稳定排序的 key=value 文本,
# 供进入前 / 退出后两次采集做 diff。任何一行不同即判定"留痕"。
#
# 设计: 一趟 su 往返采完, 段间无哨兵 (每行自带 key), 顺序固定不依赖 shell glob 顺序。
set -u

KGSL=/sys/class/kgsl/kgsl-3d0
BUS=/sys/devices/system/cpu/bus_dcvs
T=/sys/kernel/tracing

rd() {  # rd <key> <path>  — 读不到留空, 不报错
  if [ -r "$2" ]; then
    printf '%s=%s\n' "$1" "$(cat "$2" 2>/dev/null | tr '\n' ' ' | sed 's/ *$//')"
  else
    printf '%s=<unreadable>\n' "$1"
  fi
}

echo "# device_snapshot v1"

# ── ftrace ──
rd ftrace.tracing_on      "$T/tracing_on"
rd ftrace.current_tracer  "$T/current_tracer"
rd ftrace.buffer_size_kb  "$T/buffer_size_kb"
# set_event 逐行排序后单行化, 顺序无关
if [ -r "$T/set_event" ]; then
  printf 'ftrace.set_event=%s\n' "$(sort "$T/set_event" 2>/dev/null | tr '\n' ',' | sed 's/,$//')"
else
  printf 'ftrace.set_event=<unreadable>\n'
fi

# ── GPU (kgsl) ──
rd kgsl.min_pwrlevel      "$KGSL/min_pwrlevel"
rd kgsl.max_pwrlevel      "$KGSL/max_pwrlevel"
rd kgsl.default_pwrlevel  "$KGSL/default_pwrlevel"
rd kgsl.thermal_pwrlevel  "$KGSL/thermal_pwrlevel"
rd kgsl.force_clk_on      "$KGSL/force_clk_on"
rd kgsl.force_rail_on     "$KGSL/force_rail_on"
rd kgsl.force_bus_on      "$KGSL/force_bus_on"
rd kgsl.force_no_nap      "$KGSL/force_no_nap"
rd kgsl.idle_timer        "$KGSL/idle_timer"
rd kgsl.max_gpuclk        "$KGSL/max_gpuclk"
rd kgsl.pwrscale          "$KGSL/pwrscale"
rd kgsl.bus_split         "$KGSL/bus_split"
rd kgsl.perfcounter       "$KGSL/perfcounter"
rd kgsl.devfreq_governor  "$KGSL/devfreq/governor"
rd kgsl.devfreq_min_freq  "$KGSL/devfreq/min_freq"
rd kgsl.devfreq_max_freq  "$KGSL/devfreq/max_freq"

# ── 总线 (DDR / LLCC) ──
rd bus.DDR.boost_freq     "$BUS/DDR/boost_freq"
rd bus.DDR.hw_max_freq    "$BUS/DDR/hw_max_freq"
rd bus.DDR.hw_min_freq    "$BUS/DDR/hw_min_freq"
rd bus.LLCC.boost_freq    "$BUS/LLCC/boost_freq"
rd bus.LLCC.hw_max_freq   "$BUS/LLCC/hw_max_freq"
rd bus.LLCC.hw_min_freq   "$BUS/LLCC/hw_min_freq"

# ── CPU 调速器 (bench 会动, 本闭环不动, 但要证明没动) ──
for p in /sys/devices/system/cpu/cpufreq/policy*; do
  [ -d "$p" ] || continue
  n=$(basename "$p")
  rd "cpu.$n.governor"     "$p/scaling_governor"
  rd "cpu.$n.scaling_max"  "$p/scaling_max_freq"
  rd "cpu.$n.scaling_min"  "$p/scaling_min_freq"
done

# ── 显示 (wm size / density 是候选旋钮) ──
printf 'wm.size=%s\n'    "$(wm size 2>/dev/null | tr '\n' ';' | sed 's/;$//')"
printf 'wm.density=%s\n' "$(wm density 2>/dev/null | tr '\n' ';' | sed 's/;$//')"
printf 'settings.peak_refresh_rate=%s\n' "$(settings get system peak_refresh_rate 2>/dev/null)"
printf 'settings.min_refresh_rate=%s\n'  "$(settings get system min_refresh_rate 2>/dev/null)"
# 厂商刷新率键。probe_sysparam.sh 会逐档写它再还原, knob 也会写它 —— 凡是本闭环
# 写过的键都必须进快照, 否则还原失败在判据 4 里根本看不见。
printf 'settings.refresh_rate_mode=%s\n' "$(settings get system refresh_rate_mode 2>/dev/null)"

echo "# end"
