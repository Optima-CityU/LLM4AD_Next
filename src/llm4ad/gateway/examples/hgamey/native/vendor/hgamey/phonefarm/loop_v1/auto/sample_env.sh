#!/bin/sh
# sample_env.sh <时长s> <热区间隔s, 整数> [停止标志文件] — 设备端执行。整机功耗与结温的采样。
#
# 与 ftrace 采集并行跑: ftrace 给帧时序, 这里给「这一轮到底费了多少电、烫到多少度」。
# 项目目标不是单纯省电, 但功耗与温度是判定「更好」的两个必要项, 所以每轮都要量。
#
# 只读 /sys/class/power_supply 与 /sys/class/thermal, 不碰任何充电控制、
# 不改任何热策略。输出每行一条样本, 主机端 env_stats_window() 纯函数解析。
#
#   PWR <uptime> <batt_uv> <batt_ua> <batt_uw|NA> <usb_uv> <usb_ua>      (每 PWR_IVL 秒, 缺省 0.2)
#   ENV <uptime> <batt_uv> <batt_ua> <batt_uw|NA> <usb_uv> <usb_ua> <热区> <毫摄氏度>   (每 <热区间隔> 秒)
#   GPU <uptime> <kgsl max_pwrlevel> <kgsl gpuclk Hz>      (跟 ENV 同一节拍, 读得到时才有)
#
# v2 (2026-10-21, 审计 #3 #4):
# - 电流电压单独一个快循环 (≥5 Hz), 只用 shell 内建 read, 不起子进程。v1 每个样本都把所有热区
#   cat 一遍, 实测间隔 2.4–3.5 秒, 每臂只有约 17 个点。
# - 热区列表开头扫一次 (类型不会变), 之后只读选中热区的 temp, 而且只在 ENV 节拍读。
# - 按 /proc/uptime 计时 (v1 按「次数 × 间隔」计, 循环本身的耗时不算, 实际时长远超 <时长>)。
# - 给了停止标志文件时, 它一出现就停 (ftrace_capture.sh 采集结束后才写 trace 输出文件, 拿它当
#   标志), 不再在负载结束后接着采空闲段。开始时标志文件已经存在 (上一轮残留) 就不认它, 只按时长停。
# - 主机端按 trace 的 capture_start/end_uptime 截窗, 并丢掉第一个点 (见 sysparam::env_stats_window)。
set -u

DUR="${1:-30}"
IVL="${2:-2}"
B=/sys/class/power_supply/battery
U=/sys/class/power_supply/usb
KGSL=/sys/class/kgsl/kgsl-3d0

PWR_IVL="${PWR_IVL:-0.2}"
# 审查 R1 #7: toybox/mksh 的 sleep 不保证支持小数秒。不支持时 sleep 立刻出错返回, 快循环会空转占满一核
# (两臂功耗都抬高、每秒几千行), 所以先试一次, 不行就退回整秒; 头部记的是实际生效的间隔。
sleep "$PWR_IVL" 2>/dev/null || PWR_IVL=1
echo "#sample_env v2 dur=$DUR interval=$IVL power_interval=$PWR_IVL stop=${3:-none}"
echo "#battery_status=$(cat $B/status 2>/dev/null)"
echo "#battery_capacity=$(cat $B/capacity 2>/dev/null)"
echo "#charge_suspended=$( [ -f /data/local/tmp/loop_v1_charge.state ] && echo yes || echo no)"
# 停充后的供电方式 (charge_suspend.sh 写在状态文件第三行): battery = 电池放电; bypass = USB 直供、电池不进不出
echo "#charge_mode=$(sed -n 3p /data/local/tmp/loop_v1_charge.state 2>/dev/null)"
# 主动散热风扇状态: 风扇自身耗电会进功耗读数, 所以每一轮都留一行证据, 好复核
# 「同一组对照的两臂是同一风扇状态」。只读, 从不写。
# 只取**设定档**类节点 (enable / level / pwm), 不取转速读数 (fan_speed_count、
# hwmon 的 fan1_input 都是实时转速): 转速每轮都在抖, 把它拼进这行的话, 主机端
# 那个「两臂风扇状态是否一致」的字符串相等比较会永远为假, 等于白记。
fan_line=""
for f in /sys/kernel/fan/*enable* /sys/kernel/fan/*level* /sys/kernel/fan/*pwm* \
         /sys/class/hwmon/hwmon*/pwm1; do
  [ -f "$f" ] || continue
  fan_line="$fan_line$(basename "$f")=$(cat "$f" 2>/dev/null),"
done
echo "#fan_state=$(echo "$fan_line" | sed 's/,$//')"

STOP="${3:-}"
PWR_IVL="${PWR_IVL:-0.2}"

# 停止标志: 开始时就已存在的不认 (只读判断, 不删任何文件)
stop_armed=0
if [ -n "$STOP" ] && [ ! -e "$STOP" ]; then stop_armed=1; fi

# SoC 结温热区 (cpu-* / cpullc* / gpuss*), 与 phonefarm hwcond.rs 的 soc_max_c 同口径。只扫一次。
ZONES=""
for z in /sys/class/thermal/thermal_zone*; do
  ty=""
  read ty 2>/dev/null < "$z/type" || continue
  case "$ty" in
    cpu-*|cpullc*|gpuss*) ZONES="$ZONES $z:$ty" ;;
  esac
done

# 时间用 /proc/uptime 的整秒与百分秒分开算差值, 避开 mksh 32 位整数与前导 0 被当八进制
read up0 _rest < /proc/uptime
s0=${up0%.*}; c0=${up0#*.}; c0=${c0#0}; [ -n "$c0" ] || c0=0
dur_cs=$((DUR * 100))
ivl_cs=$((IVL * 100))
next_env=0

while :; do
  read up _rest < /proc/uptime
  s1=${up%.*}; c1=${up#*.}; c1=${c1#0}; [ -n "$c1" ] || c1=0
  el=$(( (s1 - s0) * 100 + c1 - c0 ))
  [ "$el" -ge "$dur_cs" ] && break
  if [ "$stop_armed" = 1 ] && [ -e "$STOP" ]; then
    echo "#stop_seen uptime=$up"
    break
  fi
  bv=0; bi=0; bp=NA; uv=0; ui=0
  read bv 2>/dev/null < $B/voltage_now || bv=0
  read bi 2>/dev/null < $B/current_now || bi=0
  read bp 2>/dev/null < $B/power_now || bp=NA
  read uv 2>/dev/null < $U/voltage_now || uv=0
  read ui 2>/dev/null < $U/current_now || ui=0
  [ -n "$bv" ] || bv=0; [ -n "$bi" ] || bi=0; [ -n "$bp" ] || bp=NA
  [ -n "$uv" ] || uv=0; [ -n "$ui" ] || ui=0
  if [ "$el" -ge "$next_env" ]; then
    hot_t=0; hot_z=none
    for zt in $ZONES; do
      z=${zt%%:*}; ty=${zt#*:}
      t=""
      read t 2>/dev/null < "$z/temp" || continue
      case "$t" in ''|*[!0-9-]*) continue ;; esac
      [ "$t" -gt 0 ] 2>/dev/null || continue
      [ "$t" -lt 100000 ] 2>/dev/null || continue
      if [ "$t" -gt "$hot_t" ] 2>/dev/null; then hot_t=$t; hot_z=$ty; fi
    done
    printf 'ENV %s %s %s %s %s %s %s %s\n' "$up" "$bv" "$bi" "$bp" "$uv" "$ui" "$hot_z" "$hot_t"
    # GPU 频率上限档与当前频率: 厂商性能服务 (perf2-hal / zperfcubeservice) 会在约 10 秒内
    # 把 max_pwrlevel 改掉, 两臂/两次运行的 GPU 上限可能不一样。逐样本记下来, 主机端核对。
    # 读不到 (节点不存在/没权限) 时整行不输出, 旧解析器只认 ENV 行, 不受影响。
    gl=""; gc=""
    read gl 2>/dev/null < $KGSL/max_pwrlevel
    read gc 2>/dev/null < $KGSL/gpuclk
    [ -n "$gl" ] && [ -n "$gc" ] && printf 'GPU %s %s %s\n' "$up" "$gl" "$gc"
    next_env=$((el + ivl_cs))
  else
    printf 'PWR %s %s %s %s %s %s\n' "$up" "$bv" "$bi" "$bp" "$uv" "$ui"
  fi
  sleep "$PWR_IVL"
done
echo "#battery_status_after=$(cat $B/status 2>/dev/null)"
echo "#sample_env done"
