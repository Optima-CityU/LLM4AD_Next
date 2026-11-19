#!/bin/sh
# knob_ddr_boost.sh {apply|restore|status} — 旋钮: 把内存总线 (DDR + LLCC) 的用户态
# 下限 boost_freq 钉到硬件上限, 强制访存全程跑满带宽。
#
# 为什么选这个旋钮
#   本闭环的归因显示 GPU 每帧活跃 21.7ms、总线投票中位 3150 (上限 4800, 余量 34%),
#   DDR 实跑 3187MHz / 上限 5333MHz。若这 21.7ms 里有相当部分是在等访存, 钉住带宽
#   下限就该把它压下去; 若压不动, 说明是纯算力开销 —— 两种结果都是明确结论。
#   phonefarm 的 bench.rs 早已用同一手法给 TFLite 测量消除总线抖动 (见其 BUS_DCVS 注释),
#   所以这条路径在本机被验证过可写、可回滚。
#
# 回滚纪律
#   apply 时把原值存到 STATE; restore 读回并逐项写回, 再回读比对。
#   STATE 存在即代表"旋钮处于施加态", restore 成功后删除, 因此可重复调用不出错。
set -u

BUS=/sys/devices/system/cpu/bus_dcvs
NODES="DDR LLCC"
STATE=/data/local/tmp/loop_v1_knob_ddr.state

case "${1:-status}" in
  apply)
    if [ -f "$STATE" ]; then
      echo "KNOB_ALREADY_APPLIED (state=$STATE)"
      exit 0
    fi
    : > "$STATE"
    for n in $NODES; do
      cur=$(cat "$BUS/$n/boost_freq" 2>/dev/null)
      max=$(cat "$BUS/$n/hw_max_freq" 2>/dev/null)
      if [ -z "$cur" ] || [ -z "$max" ]; then
        echo "KNOB_SKIP $n (读不到 boost_freq/hw_max_freq)"
        continue
      fi
      echo "$n $cur" >> "$STATE"
      echo "$max" > "$BUS/$n/boost_freq" 2>/dev/null
      back=$(cat "$BUS/$n/boost_freq" 2>/dev/null)
      if [ "$back" = "$max" ]; then
        echo "KNOB_APPLIED $n: $cur -> $back"
      else
        echo "KNOB_FAIL $n: 想写 $max, 回读 $back (原值 $cur)"
      fi
    done
    ;;
  restore)
    if [ ! -f "$STATE" ]; then
      echo "KNOB_NOT_APPLIED (无 $STATE, 无需回滚)"
      exit 0
    fi
    ok=1
    while read -r n v; do
      [ -n "$n" ] || continue
      echo "$v" > "$BUS/$n/boost_freq" 2>/dev/null
      back=$(cat "$BUS/$n/boost_freq" 2>/dev/null)
      if [ "$back" = "$v" ]; then
        echo "KNOB_RESTORED $n: -> $back"
      else
        echo "KNOB_RESTORE_FAIL $n: 想写 $v, 回读 $back"
        ok=0
      fi
    done < "$STATE"
    [ "$ok" = "1" ] && rm -f "$STATE"
    ;;
  status)
    for n in $NODES; do
      printf '%s boost=%s hw_max=%s hw_min=%s cur=%s\n' "$n" \
        "$(cat "$BUS/$n/boost_freq" 2>/dev/null)" \
        "$(cat "$BUS/$n/hw_max_freq" 2>/dev/null)" \
        "$(cat "$BUS/$n/hw_min_freq" 2>/dev/null)" \
        "$(cat "$BUS/$n/cur_freq" 2>/dev/null)"
    done
    [ -f "$STATE" ] && echo "state: 已施加" || echo "state: 未施加"
    ;;
  *)
    echo "用法: knob_ddr_boost.sh {apply|restore|status}" >&2
    exit 2
    ;;
esac
