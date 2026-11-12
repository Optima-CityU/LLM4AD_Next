#!/bin/sh
# knob_sysparam.sh {apply <plan>|restore|status} — 通用系统参数旋钮。
#
# 与 knob_ddr_boost.sh 同接口 (contract/knob.md 的三态), 区别是「改哪些项」不写死在
# 脚本里, 而是由主机端下发一份 plan 文件。这样大模型每轮挑的一组参数都能用同一个
# 旋钮实现落到设备上, 不必为每组参数生成一个脚本。
#
# plan 文件格式 (制表符分隔, 每行一项):
#   sysfs<TAB>/sys/...<TAB>目标值
#   setting<TAB>system:peak_refresh_rate<TAB>目标值
#   setting<TAB>system:refresh_rate_mode<TAB>目标值
#
# 安全边界 (与主机端白名单重复一遍, 故意冗余 —— 设备端是最后一道):
#   · 路径必须命中 ALLOW 前缀
#   · 路径命中任何 DENY 关键词一律拒绝。温控保护 (thermal / trip_point / cooling /
#     风扇 / bcl) 永远不准动, 哪怕主机端传了下来 —— 拒绝并如实报 KNOB_REFUSE。
#
# 回滚纪律: apply 前把每项原值写进 STATE; restore 逐项写回并回读比对,
# 全部对上才删 STATE。STATE 在 = 处于施加态, 也是崩溃后的恢复依据。
set -u

STATE=/data/local/tmp/loop_v1_sysparam.state

# 允许触碰的路径前缀 (sysfs)
allowed_sysfs() {
  case "$1" in
    /sys/devices/system/cpu/cpufreq/policy*/scaling_min_freq) return 0 ;;
    /sys/devices/system/cpu/cpufreq/policy*/scaling_max_freq) return 0 ;;
    /sys/devices/system/cpu/cpufreq/policy*/scaling_governor)  return 0 ;;
    /sys/devices/system/cpu/cpufreq/policy*/walt/hispeed_load)       return 0 ;;
    /sys/devices/system/cpu/cpufreq/policy*/walt/hispeed_freq)       return 0 ;;
    /sys/devices/system/cpu/cpufreq/policy*/walt/up_rate_limit_us)   return 0 ;;
    /sys/devices/system/cpu/cpufreq/policy*/walt/down_rate_limit_us) return 0 ;;
    /sys/class/kgsl/kgsl-3d0/min_pwrlevel)                     return 0 ;;
    /sys/class/kgsl/kgsl-3d0/max_pwrlevel)                     return 0 ;;
    /sys/class/kgsl/kgsl-3d0/devfreq/min_freq)                 return 0 ;;
    /sys/class/kgsl/kgsl-3d0/devfreq/max_freq)                 return 0 ;;
    /sys/class/kgsl/kgsl-3d0/devfreq/governor)                 return 0 ;;
    /sys/devices/system/cpu/bus_dcvs/DDR/boost_freq)           return 0 ;;
    /sys/devices/system/cpu/bus_dcvs/LLCC/boost_freq)          return 0 ;;
    *) return 1 ;;
  esac
}

# 温控保护相关一律拒绝
denied() {
  case "$1" in
    *thermal*|*trip_point*|*cooling*|*fan*|*tsens*|*bcl*|*throttl*) return 0 ;;
    *) return 1 ;;
  esac
}

allowed_setting() {
  case "$1" in
    system:peak_refresh_rate|system:min_refresh_rate) return 0 ;;
    # 厂商刷新率键 (红魔 NX809J 的 AOSP 两个键是 null, 走这个)。白名单侧
    # whitelist.py 只在探测时真把活动 fps 改掉的那几档才收, 这里放行同一个键。
    system:refresh_rate_mode) return 0 ;;
    *) return 1 ;;
  esac
}

# ── 回滚的「波及面」 ──
# 写一个节点会把兄弟节点一起改掉: 实测把 cpufreq 的 scaling_governor 切成
# performance 再切回 walt, scaling_max_freq 从 1785600 变成了 1228800 并且再也没回来
# —— 只回滚「我们写过的那几个路径」根本不够, 判据 4 当场报留痕, 一组本来干净的
# 数据 (p95 -7.2%) 就这么作废了。
# 所以每碰一个节点, 就把它整组兄弟节点的原值一起存下来。
siblings() {
  case "$1" in
    /sys/devices/system/cpu/cpufreq/policy*/scaling_min_freq|\
    /sys/devices/system/cpu/cpufreq/policy*/scaling_max_freq|\
    /sys/devices/system/cpu/cpufreq/policy*/scaling_governor)
      d=$(dirname "$1")
      # 顺序就是回滚顺序: governor 会重设 min/max, 所以它必须排在最前
      echo "$d/scaling_governor"
      echo "$d/scaling_max_freq"
      echo "$d/scaling_min_freq"
      ;;
    /sys/class/kgsl/kgsl-3d0/min_pwrlevel|/sys/class/kgsl/kgsl-3d0/max_pwrlevel)
      echo "/sys/class/kgsl/kgsl-3d0/max_pwrlevel"
      echo "/sys/class/kgsl/kgsl-3d0/min_pwrlevel"
      ;;
    *) echo "$1" ;;
  esac
}

read_one() {  # read_one <kind> <path>
  if [ "$1" = "setting" ]; then
    ns=$(echo "$2" | cut -d: -f1); key=$(echo "$2" | cut -d: -f2)
    settings get "$ns" "$key" 2>/dev/null
  else
    cat "$2" 2>/dev/null
  fi
}

write_one() {  # write_one <kind> <path> <value>
  if [ "$1" = "setting" ]; then
    ns=$(echo "$2" | cut -d: -f1); key=$(echo "$2" | cut -d: -f2)
    # 原值是 null / 空 = 这个键本来就没设过。回滚时必须 delete 而不是 put "null",
    # 否则会留下一个字面量 "null" —— 判据 4 的快照会当场抓到这种留痕。
    if [ "$3" = "null" ] || [ -z "$3" ]; then
      settings delete "$ns" "$key" >/dev/null 2>&1
    else
      settings put "$ns" "$key" "$3" 2>/dev/null
    fi
  else
    echo "$3" > "$2" 2>/dev/null
  fi
}

case "${1:-status}" in
  apply)
    PLAN="${2:-}"
    if [ -z "$PLAN" ] || [ ! -f "$PLAN" ]; then
      echo "KNOB_FAIL plan: 缺 plan 文件 ($PLAN)" >&2
      exit 2
    fi
    if [ -f "$STATE" ]; then
      echo "KNOB_ALREADY_APPLIED (state=$STATE)"
      exit 0
    fi

    # 先全量校验, 有任何一项越界就整份拒绝, 一个字节都不写
    bad=0
    while IFS="$(printf '\t')" read -r kind path val; do
      [ -n "${kind:-}" ] || continue
      case "$kind" in \#*) continue ;; esac
      if denied "$path"; then
        echo "KNOB_REFUSE $path: 命中温控保护关键词, 本旋钮永不触碰"
        bad=1; continue
      fi
      if [ "$kind" = "setting" ]; then
        allowed_setting "$path" || { echo "KNOB_REFUSE $path: 不在 setting 白名单"; bad=1; }
      elif [ "$kind" = "sysfs" ]; then
        allowed_sysfs "$path" || { echo "KNOB_REFUSE $path: 不在 sysfs 白名单"; bad=1; }
        [ -e "$path" ] || { echo "KNOB_REFUSE $path: 节点不存在"; bad=1; }
      else
        echo "KNOB_REFUSE $path: 未知 kind=$kind"; bad=1
      fi
    done < "$PLAN"
    if [ "$bad" = "1" ]; then
      echo "KNOB_PLAN_REJECTED (未写入任何一项)"
      exit 3
    fi

    # 先把回滚依据存齐 (含兄弟节点), **再**动设备 —— 中途被杀也能回滚
    : > "$STATE"
    while IFS="$(printf '\t')" read -r kind path val; do
      [ -n "${kind:-}" ] || continue
      case "$kind" in \#*) continue ;; esac
      if [ "$kind" = "sysfs" ]; then
        for sp in $(siblings "$path"); do
          grep -qF "	$sp	" "$STATE" 2>/dev/null && continue   # 已经存过
          printf '%s\t%s\t%s\n' sysfs "$sp" "$(cat "$sp" 2>/dev/null)" >> "$STATE"
        done
      else
        grep -qF "	$path	" "$STATE" 2>/dev/null && continue
        printf '%s\t%s\t%s\n' "$kind" "$path" "$(read_one "$kind" "$path")" >> "$STATE"
      fi
    done < "$PLAN"

    # 我们要写进去的值另存一份: restore 回读对不上时, 据此分清「我们的改动还在」(真失败)
    # 和「厂商性能服务已经把节点改成了它自己的值」(我们的约束已不在, 不算还原失败)
    grep -v '^#' "$PLAN" > "$STATE.applied"

    # 两遍写: 第一遍可能被 min>max 之类的顺序约束夹回, 第二遍补齐
    pass=1
    while [ "$pass" -le 2 ]; do
      while IFS="$(printf '\t')" read -r kind path val; do
        [ -n "${kind:-}" ] || continue
        case "$kind" in \#*) continue ;; esac
        write_one "$kind" "$path" "$val"
      done < "$PLAN"
      pass=$((pass + 1))
    done

    # 回读比对, 逐项如实自报
    while IFS="$(printf '\t')" read -r kind path val; do
      [ -n "${kind:-}" ] || continue
      case "$kind" in \#*) continue ;; esac
      back=$(read_one "$kind" "$path")
      old=$(grep -F "$path" "$STATE" 2>/dev/null | head -1 | cut -f3)
      if [ "$back" = "$val" ]; then
        echo "KNOB_APPLIED $path: $old -> $back"
      else
        echo "KNOB_FAIL $path: 想写 $val, 回读 $back (原值 $old)"
      fi
    done < "$PLAN"
    ;;

  restore)
    if [ ! -f "$STATE" ]; then
      echo "KNOB_NOT_APPLIED (无 $STATE, 无需回滚)"
      exit 0
    fi
    ok=1
    # STATE 里已经按回滚顺序排好 (governor 在 min/max 之前), 仍走两遍兜住夹取
    pass=1
    while [ "$pass" -le 2 ]; do
      while IFS="$(printf '\t')" read -r kind path val; do
        [ -n "${kind:-}" ] || continue
        write_one "$kind" "$path" "$val"
      done < "$STATE"
      pass=$((pass + 1))
    done
    while IFS="$(printf '\t')" read -r kind path val; do
      [ -n "${kind:-}" ] || continue
      back=$(read_one "$kind" "$path")
      mine=$(grep -F "	$path	" "$STATE.applied" 2>/dev/null | head -1 | cut -f3)
      if [ "$back" = "$val" ]; then
        echo "KNOB_RESTORED $path: -> $back"
      elif [ -n "$mine" ] && [ "$back" = "$mine" ]; then
        echo "KNOB_RESTORE_FAIL $path: 想写 $val, 回读 $back (仍是我们施加的值)"
        ok=0
      else
        # 2026-10-22 红魔实测: 厂商性能服务在写回与回读之间就把 scaling_max_freq 改成了它的动态值
        # (3628800 → 1228800); 节点上已不是我们的值, 我们的约束不在了
        echo "KNOB_RESTORED_VENDOR $path: 写回 $val, 回读 $back (厂商服务已改写, 不是我们施加的 ${mine:-兄弟节点})"
      fi
    done < "$STATE"
    [ "$ok" = "1" ] && rm -f "$STATE" "$STATE.applied"
    [ "$ok" = "1" ] || exit 4
    ;;

  status)
    if [ -f "$STATE" ]; then
      echo "state: 已施加"
      while IFS="$(printf '\t')" read -r kind path val; do
        [ -n "${kind:-}" ] || continue
        printf '%s cur=%s orig=%s\n' "$path" "$(read_one "$kind" "$path")" "$val"
      done < "$STATE"
    else
      echo "state: 未施加"
    fi
    ;;

  *)
    echo "用法: knob_sysparam.sh {apply <plan>|restore|status}" >&2
    exit 2
    ;;
esac
