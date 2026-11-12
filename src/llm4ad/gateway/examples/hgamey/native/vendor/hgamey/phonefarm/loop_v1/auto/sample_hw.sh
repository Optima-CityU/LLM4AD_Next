#!/bin/sh
# sample_hw.sh <最长秒> <停止标记文件> — 设备端执行。测量窗口内逐秒记 GPU 上限档与风扇设定档。
#
# 为什么单独一个脚本 (审计 #9、#11):
#   - 厂商性能服务会在臂中途改 kgsl max_pwrlevel, 同一配置的帧率因此跳台阶;
#   - 游戏启动后风扇会被打开, 而 sample_env.sh 只在开头记一行风扇状态。
#   主机端拿这两条逐秒序列核对「同一对的两臂是不是同一 GPU 档位分布、同一风扇状态」,
#   不一样的那一对判不可比、剔除, 并写进报告。
#
# 只读, 从不写任何节点, 也从不删除任何文件。主机端在测量结束时创建停止标记文件, 本脚本看到后退出。
# 停止标记由主机端每臂用一个新文件名 (见 eval.rs HwWatch); 开始时就已存在的标记不认
# (只读判断, 与 sample_env.sh 同一做法), 这时只按 <最长秒> 停, 并在输出里记一行 #stop_preexisting。
#
#   HW <uptime> <kgsl max_pwrlevel|NA> <风扇设定档串|->
set -u

MAX="${1:-600}"
STOP="${2:-/data/local/tmp/sample_hw.stop}"
KGSL=/sys/class/kgsl/kgsl-3d0

echo "#sample_hw v1 max=$MAX stop=$STOP"
# 停止标记: 开始时就已存在的不认 (只读判断, 不删任何文件)
stop_armed=0
if [ -e "$STOP" ]; then
  echo "#stop_preexisting $STOP"
else
  stop_armed=1
fi
n=0
while [ "$n" -lt "$MAX" ]; do
  if [ "$stop_armed" = 1 ] && [ -e "$STOP" ]; then
    break
  fi
  up=$(cut -d' ' -f1 /proc/uptime 2>/dev/null)
  [ -n "$up" ] || up=0
  gl=$(cat $KGSL/max_pwrlevel 2>/dev/null)
  [ -n "$gl" ] || gl=NA
  # 与 sample_env.sh 同一口径: 只取设定档类节点 (enable / level / pwm), 不取实时转速
  fan_line=""
  for f in /sys/kernel/fan/*enable* /sys/kernel/fan/*level* /sys/kernel/fan/*pwm* \
           /sys/class/hwmon/hwmon*/pwm1; do
    [ -f "$f" ] || continue
    fan_line="$fan_line$(basename "$f")=$(cat "$f" 2>/dev/null),"
  done
  fan_line=$(echo "$fan_line" | sed 's/,$//')
  [ -n "$fan_line" ] || fan_line=-
  echo "HW $up $gl $fan_line"
  n=$((n + 1))
  sleep 1
done
echo "#sample_hw done"
