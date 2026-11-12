#!/bin/sh
# charge_suspend.sh {suspend|restore|status} — 测功耗期间停充, 让整机真由电池供电。
#
# 为什么非停充不可 (实测):
#   插着 USB 时 USB 输入功率里约 46% 是在给电池充电, 而且充电电流随电量**单调衰减**
#   —— 那个衰减会被当成"功耗随时间下降"混进 A/B 比较。电池轨这边同样量不到整机开销,
#   读到的只是充电电流与系统耗电相抵之后的余量。
#
# 节点与判据全部照搬 phonefarm src/hwcond.rs (PR #27 真机验过的那一版), 不另造一套:
#   · 候选节点表与探测顺序一致 —— 本机红魔 NX809J 管用的是 /sys/class/qcom-battery/
#     charging_enabled, 它不在 power_supply class 下, 只看 power_supply 会整个错过
#   · 等 12 秒让电量计跟上 —— 第一版只等 2.5 秒, 把管用的节点误判成"写进去无效"
#   · 电量低于 30% 不停充: 停充期间整机纯靠电池, 电量本来就低时再满载抽几分钟,
#     轻则测到一半关机, 重则过放
#   · on_battery 要两个判据同时成立 —— status 不是 Charging **且** current_now < 0。
#     两个都会单独骗人: 停充后有的内核仍写 "Not charging" 而不是 "Discharging";
#     current_now 在充放平衡的瞬间会过零
#
# 回滚纪律同 knob_sysparam.sh: 先把「改了哪个节点、原值是什么」写进 STATE 再动设备,
# 所以进程中途被杀也能由下一次 restore 回滚。
set -u

STATE=/data/local/tmp/loop_v1_charge.state
B=/sys/class/power_supply/battery
MIN_CAPACITY=30
SETTLE_TRIES=12      # 12 x 1s = 12s, 与 hwcond.rs 的 SUSPEND_SETTLE_MS 一致
                     # (不用 sleep 0.5: Android 的 sh 不保证支持小数秒)
BYPASS_MAX_S=90      # 旁路判定最多等这么久 (电量计读数是平滑过的, 10-11 实测约 45 s 才落到 ~0)
BYPASS_IDLE_UA=50000 # |电池电流| 低于它 = 电池不进不出
MIXED_TRIES=3        # 混合供电判据要连续这么多次读数成立 (与 hwcond.rs 的 MIXED_TRIES 一致)
# 审查 R1 #2: 整个 suspend 的总时限。8 个候选 × (12 s 等放电 + 最多 78 s 等旁路) 本来可达 12 分钟,
# 主机端 (eval.rs CHARGE_SUSPEND_TIMEOUT_S) 的超时只需覆盖这个数 + 一个候选的收尾。
SUSPEND_TOTAL_MAX_S=${SUSPEND_TOTAL_MAX_S:-300}
read _up0 _rest < /proc/uptime 2>/dev/null || _up0=0
T0=${_up0%.*}
elapsed() { read _up _rest < /proc/uptime 2>/dev/null || _up=0; echo $(( ${_up%.*} - T0 )); }

# "<路径> <how>" —— how: one=写1 / zero=写0 / cap=把上限压到当前电量以下
CANDIDATES="/sys/class/power_supply/battery/input_suspend one
/sys/class/power_supply/usb/input_suspend one
/sys/class/qcom-battery/charging_enabled zero
/sys/class/qcom-battery/battery_charging_enabled zero
/sys/class/power_supply/battery/charging_enabled zero
/sys/class/power_supply/battery/battery_charging_enabled zero
/sys/class/power_supply/battery/charge_control_end_threshold cap
/sys/class/power_supply/usb/input_current_limit zero"

bstat() { cat "$B/status" 2>/dev/null; }
bcur()  { cat "$B/current_now" 2>/dev/null; }
bcap()  { cat "$B/capacity" 2>/dev/null; }

abs() { case "$1" in -*) echo "${1#-}" ;; *) echo "$1" ;; esac; }

# 满电空闲: status=Full 且 |电池电流| < BYPASS_IDLE_UA —— 电池不进不出, 整机由 USB 供电。
# 这**不是**放电态 (2026-10-12 实测: Full + current_now=-3000 被当成放电, 不停充也不进旁路,
# 电池轨读数 ≈ 0.01 W 被当成整机功耗交了上去)。
full_idle() {
  st=$(bstat); cur=$(bcur)
  case "$st" in Full|full|FULL) ;; *) return 1 ;; esac
  case "$cur" in ''|*[!0-9-]*) return 1 ;; esac
  [ "$(abs "$cur")" -lt "$BYPASS_IDLE_UA" ] 2>/dev/null
}

on_battery() {
  st=$(bstat); cur=$(bcur)
  case "$st" in Charging|charging|CHARGING) return 1 ;; esac
  case "$cur" in ''|*[!0-9-]*) return 1 ;; esac
  full_idle && return 1
  [ "$cur" -lt 0 ] 2>/dev/null
}

# 满电空闲时按旁路处理: 写充电开关=0 (测量中途电量掉一点也不会补电), 状态文件第三行记 bypass,
# sample_env / env_stats 据此取 USB 轨 |V x I|。按顺序找第一个存在的充电开关节点。
FULL_BYPASS_NODES="/sys/class/qcom-battery/battery_charging_enabled
/sys/class/qcom-battery/charging_enabled
/sys/class/power_supply/battery/battery_charging_enabled
/sys/class/power_supply/battery/charging_enabled"

# 旁路 (bypass): 停充后整机由 USB 直接供电, 电池不进不出 —— status 仍写 Charging,
# 电流趋近 0 而不是变负, on_battery() 永远判不过。10-11 手机节点 (USB-C PD 供电) 上
# 红魔 NX809J 写 qcom-battery/battery_charging_enabled=0 就是这个状态: USB 输入 2.13 A
# 掉到 0.13-0.22 A, 电池 +1.93 A 约 45 s 衰减到 +0.03 A。
# 判据: 写节点前电池在充 (> BYPASS_IDLE_UA) 且 12 s 内电流降了三成以上 (节点确实起作用),
# 或写之前就几乎不进不出; 然后最多等到 BYPASS_MAX_S 让 |I| < BYPASS_IDLE_UA。
# 混合供电 (审查 R1 #2): USB 供电不足时 status 仍写 Charging、电池却在放电 (fix-D 单测锁定的现场:
# Charging、-0.6 A)。on_battery 与旁路判据在这种现场都判不过, 以前会换候选再来、最后 CHARGE_FAIL。
# 判据 (与 hwcond.rs 的 mixed_settled 一致): 写后电池电流 < -BYPASS_IDLE_UA (确实在放电), 且比写前
# 降了至少 |写前| 的三成 (节点确实起作用; 写前 ≈ 0 时只要在放电就算)。连续 MIXED_TRIES 次读数都成立。
mixed_one() {   # $1 = 写前电流 $2 = 当前电流; 成立返回 0
  pre=$1; cur=$2
  case "$pre" in ''|*[!0-9-]*) return 1 ;; esac
  case "$cur" in ''|*[!0-9-]*) return 1 ;; esac
  [ "$cur" -lt "-$BYPASS_IDLE_UA" ] 2>/dev/null || return 1
  apre=$(abs "$pre")
  [ $(( (pre - cur) * 10 )) -ge $((apre * 3)) ] 2>/dev/null
}

# 返回 0 = 旁路成立; 2 = 混合供电成立; 1 = 都不成立 (或总时限到)
bypass_wait() {   # $1 = 写节点前的电流
  pre=$1; cur=$(bcur)
  case "$cur" in ''|*[!0-9-]*) return 1 ;; esac
  apre=$(abs "$pre"); acur=$(abs "$cur")
  bypass_possible=1
  if [ "$apre" -gt "$BYPASS_IDLE_UA" ] 2>/dev/null; then
    [ "$pre" -gt 0 ] 2>/dev/null || bypass_possible=0                # 写之前在放电: 不是旁路的样子
    [ $((cur * 10)) -le $((pre * 7)) ] 2>/dev/null || bypass_possible=0   # 没降三成: 节点没起作用
  fi
  t=$SETTLE_TRIES
  mixed_n=0
  reads=0
  while [ "$t" -lt "$BYPASS_MAX_S" ]; do
    [ "$(elapsed)" -ge "$SUSPEND_TOTAL_MAX_S" ] 2>/dev/null && return 1
    cur=$(bcur); acur=$(abs "$cur")
    [ "$bypass_possible" = 1 ] && [ "$acur" -lt "$BYPASS_IDLE_UA" ] 2>/dev/null && return 0
    if mixed_one "$pre" "$cur"; then
      mixed_n=$((mixed_n + 1))
      [ "$mixed_n" -ge "$MIXED_TRIES" ] && return 2
    else
      mixed_n=0
    fi
    reads=$((reads + 1))
    # 旁路不可能、混合也连一次都没成立: 不用等满 BYPASS_MAX_S
    [ "$bypass_possible" = 0 ] && [ "$mixed_n" = 0 ] && [ "$reads" -ge "$MIXED_TRIES" ] && return 1
    sleep 1; t=$((t + 1))
  done
  return 1
}

# 开跑前查 USB 输入上限 (审计 #5): 旁路时整机全靠 USB, 上限不够 (实测停充后被限到约 0.49 A)
# 负载一大电池就会补电。不够就明确打「供电不足」, 主机端 sysparam::usb_supply_note 收进回包。
# 只读, 不改任何节点。需求默认 6 W, 可用环境变量 USB_NEED_MW 改 (毫瓦, 整数)。
USB_NEED_MW=${USB_NEED_MW:-6000}
usb_limit_check() {
  U=/sys/class/power_supply/usb
  lim=$(cat $U/input_current_limit 2>/dev/null)
  case "$lim" in ''|*[!0-9]*|0) lim=$(cat $U/current_max 2>/dev/null) ;; esac
  uv=$(cat $U/voltage_now 2>/dev/null)
  case "$lim" in ''|*[!0-9]*|0) echo "CHARGE_USB_LIMIT 读不到 USB 输入上限 (input_current_limit/current_max), 没法开跑前判供电够不够"; return 0 ;; esac
  case "$uv" in ''|*[!0-9]*|0) echo "CHARGE_USB_LIMIT limit_ua=$lim 读不到 USB 电压, 没法开跑前判供电够不够"; return 0 ;; esac
  mw=$(( (lim / 1000) * (uv / 1000) / 1000 ))
  echo "CHARGE_USB_LIMIT limit_ua=$lim usb_uv=$uv max_mw=$mw need_mw=$USB_NEED_MW"
  if [ "$mw" -lt "$USB_NEED_MW" ] 2>/dev/null; then
    echo "CHARGE_SUPPLY_LOW 供电不足: USB 输入上限约 ${mw} mW, 低于 ${USB_NEED_MW} mW。负载大时电池会补电, 功耗按 USB 与电池两轨同刻相加计 (混合口径, 不能跨任务比)"
  fi
}

report_state() {
  printf 'CHARGE_STATE status=%s current_now=%s capacity=%s on_battery=%s\n' \
    "$(bstat)" "$(bcur)" "$(bcap)" "$(on_battery && echo yes || echo no)"
}

case "${1:-status}" in
  suspend)
    if [ -f "$STATE" ]; then
      echo "CHARGE_ALREADY_SUSPENDED (state=$STATE)"
      report_state
      exit 0
    fi
    cap=$(bcap)
    case "$cap" in
      ''|*[!0-9]*) echo "CHARGE_SKIP: 读不到电池电量, 不敢停充"; report_state; exit 1 ;;
    esac
    if [ "$cap" -lt "$MIN_CAPACITY" ] 2>/dev/null; then
      echo "CHARGE_SKIP: 电量 $cap% 低于停充下限 $MIN_CAPACITY%, 不停充"
      report_state
      exit 1
    fi
    if full_idle; then
      for path in $FULL_BYPASS_NODES; do
        saved=$(cat "$path" 2>/dev/null)
        [ -n "$saved" ] || continue
        printf '%s\n%s\n%s\n' "$path" "$saved" "bypass" > "$STATE"
        echo 0 > "$path" 2>/dev/null
        echo "CHARGE_SUSPENDED_BYPASS $path: $saved -> $(cat "$path" 2>/dev/null) (电池已满且不进不出, 按旁路: 整机由 USB 供电, 关充电防止测量中途补电)"
        usb_limit_check
        report_state
        exit 0
      done
      echo "CHARGE_FAIL: 电池已满空闲, 但找不到能关充电的节点, 旁路不成立"
      report_state
      exit 1
    fi
    if on_battery; then
      echo "CHARGE_ALREADY_ON_BATTERY (本来就在放电态, 不需要停充)"
      report_state
      exit 0
    fi

    # 用 here-doc 而不是管道喂这个循环: 管道会把 while 放进子 shell, 里面的
    # exit 0 只退子 shell, 成功路径就得靠外面再判一次 —— 少一层拐弯少一个坑。
    while IFS=' ' read -r path how; do
      [ -n "${path:-}" ] || continue
      if [ "$(elapsed)" -ge "$SUSPEND_TOTAL_MAX_S" ] 2>/dev/null; then
        echo "CHARGE_TIMEOUT 总时限 ${SUSPEND_TOTAL_MAX_S}s 已到, 不再试 $path 及之后的候选"
        break
      fi
      saved=$(cat "$path" 2>/dev/null)
      [ -n "$saved" ] || continue          # 这台机器没有这个节点
      case "$how" in
        one)  want=1 ;;
        zero) want=0 ;;
        cap)
          # 压到电量以下但不低于 1: 压到电量**以上**等于没压 —— 本机出厂就是
          # end_threshold=80 而电量 91% 照充不误, 正是这个道理
          want=$((cap - 5)); [ "$want" -lt 1 ] && want=1; [ "$want" -gt 99 ] && want=99 ;;
        *) continue ;;
      esac

      # 先落状态文件再动设备 (第三行是停充后的供电方式, sample_env.sh 读它)
      pre_cur=$(bcur)
      printf '%s\n%s\n%s\n' "$path" "$saved" "battery" > "$STATE"
      echo "$want" > "$path" 2>/dev/null

      i=0
      ok=0
      while [ "$i" -lt "$SETTLE_TRIES" ]; do
        sleep 1
        if on_battery; then ok=1; break; fi
        i=$((i + 1))
      done
      if [ "$ok" = "1" ]; then
        echo "CHARGE_SUSPENDED $path: $saved -> $want"
        report_state
        exit 0
      fi
      bypass_wait "$pre_cur"; bw=$?
      if [ "$bw" = 0 ]; then
        printf '%s\n%s\n%s\n' "$path" "$saved" "bypass" > "$STATE"
        echo "CHARGE_SUSPENDED_BYPASS $path: $saved -> $want (整机由 USB 直接供电, 电池电流 $pre_cur -> $(bcur) uA)"
        usb_limit_check
        report_state
        exit 0
      fi
      if [ "$bw" = 2 ]; then
        # 状态文件第三行保持 battery: 电池在放电, sample_env / env_stats 按两轨同刻相加 (混合口径) 计
        printf '%s\n%s\n%s\n' "$path" "$saved" "battery" > "$STATE"
        echo "CHARGE_SUSPENDED_MIXED $path: $saved -> $want (混合供电: USB 供电不足, 电池同时在放电, 电池电流 $pre_cur -> $(bcur) uA, status 仍 $(bstat))"
        usb_limit_check
        report_state
        exit 0
      fi
      # 没生效: 就地还原, 试下一个
      echo "$saved" > "$path" 2>/dev/null
      rm -f "$STATE"
      echo "CHARGE_TRY_FAILED $path (写 $want 无效)"
    done <<CANDS
$CANDIDATES
CANDS

    echo "CHARGE_FAIL: 所有候选节点都没能把设备转成放电态"
    report_state
    exit 1
    ;;

  restore)
    if [ ! -f "$STATE" ]; then
      echo "CHARGE_NOT_SUSPENDED (无 $STATE, 无需还原)"
      report_state
      exit 0
    fi
    path=$(sed -n 1p "$STATE")
    saved=$(sed -n 2p "$STATE")
    if [ -z "$path" ] || [ -z "$saved" ]; then
      echo "CHARGE_RESTORE_FAIL: 状态文件不完整 ($STATE)"
      exit 1
    fi
    echo "$saved" > "$path" 2>/dev/null
    back=$(cat "$path" 2>/dev/null)
    if [ "$back" = "$saved" ]; then
      rm -f "$STATE"
      echo "CHARGE_RESTORED $path: -> $back"
      report_state
    else
      echo "CHARGE_RESTORE_FAIL $path: 想写 $saved, 回读 $back"
      report_state
      exit 1
    fi
    ;;

  status)
    if [ -f "$STATE" ]; then
      echo "state: 已停充 ($(sed -n 1p "$STATE") 原值 $(sed -n 2p "$STATE") 供电 $(sed -n 3p "$STATE"))"
    else
      echo "state: 未停充"
    fi
    usb_limit_check
    report_state
    ;;

  *)
    echo "用法: charge_suspend.sh {suspend|restore|status}" >&2
    exit 2
    ;;
esac
