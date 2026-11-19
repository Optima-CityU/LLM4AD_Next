#!/bin/bash
# crosscheck_perfsrc.sh <标签> <输出目录> [refbench|genshin] — 同一段负载窗口内, 两条采集通路并排采一次
#
# 为什么要并排采而不是先后采: 帧率/功耗/温度全都随热状态漂移。先跑 A 再跑 B,
# 两组数字的差里混着「这两分钟机器变热了」, 分不清是通路口径差异还是设备状态差异。
# 所以两条通路**在同一个窗口里同时跑** —— 它们都是只读采集 (一个读 tracefs,
# 一个读 sysfs + 设备端 socket), 互不写对方的东西。
#
#   通路 A (我们的): raw ftrace 的 kgsl 事件 → 逐帧间隔 / GPU 执行时长
#                    + /sys/class/power_supply 电源轨 → 功耗
#                    + /sys/class/thermal → 温度
#   通路 B (HiSmartPerf): 设备端 GamePerfToolCollector + adb forward socket 实时流
#                    → 每秒一条 {fps,gpuUsage,current,voltage,soc,gpuTemp,batTemp,…}
#
# 负载二选一:
#   refbench (默认) — 渲染靶场, 场景确定、每帧一次提交, 帧率口径没有歧义。
#                     优先用它: 原神每帧两次提交, 帧率要先自检, 多一个出错的地方。
#   genshin         — 原神蒙德城定点匀速转视角 (loop_v1 的固定场景)。
set -euo pipefail

LABEL="${1:?用法: crosscheck_perfsrc.sh <标签> <输出目录> [refbench|genshin]}"
OUTDIR="${2:?缺输出目录}"
WORKLOAD="${3:-refbench}"

SERIAL=91253241019A
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pf_bin.sh"
LEAD=6          # 负载起跑后等几秒再开采 (让负载进入稳态)
CAPDUR=30       # 采集窗口, 两条通路共用这一段
export PATH="$PATH:/Users/mac/Library/Android/sdk/platform-tools"

case "$WORKLOAD" in
  refbench)
    PKG=io.github.hgamey.refbench
    COMM=RefbenchDrv
    ;;
  genshin)
    PKG=com.miHoYo.Yuanshen
    COMM=UnityGfxDeviceW
    # 触控版负载。原神 7.1.0 上**手柄注入已失效** ——
    # loop_v1/scripts/workload_spin_v1.json 跑出来是静止画面, 而静止画面照样能采到
    # 帧率/功耗/温度, 报告看着一切正常, 只是量的根本不是"定点转视角"那个负载。
    WL="$ROOT/knobs/gray/workload_spin_touch_v1.json"
    ;;
  *) echo "负载只能是 refbench 或 genshin, 给的是 $WORKLOAD" >&2; exit 2 ;;
esac

mkdir -p "$OUTDIR"
echo "── [$LABEL] 负载 $WORKLOAD ($PKG) · 两通路并排采集 ${CAPDUR}s ──"

adb -s "$SERIAL" shell "su -c 'sh /data/local/tmp/device_snapshot.sh'" > "$OUTDIR/snap_at_run.txt" 2>&1 || true

# ── 整个窗口停充 ──
# 插着 USB 时两条轨量到的都不是整机功耗 (电池轨是充放相抵的余量, USB 轨含着灌进电池的
# 那一份), 所以功耗那一格本来永远对不起来。停充之后两边量的才是同一个东西。
#
# 为什么在这儿统一停一次, 而不是给两个 perf 各加 --suspend-charging:
# 它们是并排跑的, 两个进程会抢同一个节点和同一份状态文件 —— 后一个"恢复"会在
# 前一个还在测的时候把充电打开, 而状态文件被后写的那份覆盖。停充必须是整个窗口一次。
CHG_NODE=""
CHG_SAVED=""
restore_charging() {
  if [ -n "$CHG_NODE" ]; then
    adb -s "$SERIAL" shell "su -c 'echo $CHG_SAVED > $CHG_NODE'" >/dev/null 2>&1
    echo "[$LABEL] 已恢复充电: $CHG_NODE ← $CHG_SAVED"
    CHG_NODE=""
  fi
}
# 任何退出路径都要恢复 —— 中途 Ctrl-C、脚本出错、被杀, 都不能把手机丢在不充电的状态
trap restore_charging EXIT INT TERM

CAP=$(adb -s "$SERIAL" shell 'cat /sys/class/power_supply/battery/capacity' 2>/dev/null | tr -d '\r')
if [ "${CAP:-0}" -lt 30 ]; then
  echo "[$LABEL] 电量 ${CAP}% < 30%, 不停充 (功耗那一格会因充电态而作废)"
else
  # 候选顺序与 hwcond::CHARGE_CTL_CANDIDATES 一致; 本机实测管用的是 qcom-battery 那条
  for n in /sys/class/qcom-battery/charging_enabled \
           /sys/class/power_supply/battery/input_suspend \
           /sys/class/power_supply/battery/charging_enabled; do
    cur=$(adb -s "$SERIAL" shell "su -c 'cat $n 2>/dev/null'" 2>/dev/null | tr -d '\r')
    [ -z "$cur" ] && continue
    want=0; [ "${n##*/}" = "input_suspend" ] && want=1
    adb -s "$SERIAL" shell "su -c 'echo $want > $n'" >/dev/null 2>&1
    # 等电量计跟上: status 立刻翻, current_now 要几秒 (实测第三秒才翻符号)
    ok=NO
    for _ in 1 2 3 4 5 6 7 8; do
      sleep 1.5
      st=$(adb -s "$SERIAL" shell 'cat /sys/class/power_supply/battery/status' 2>/dev/null | tr -d '\r')
      cu=$(adb -s "$SERIAL" shell 'cat /sys/class/power_supply/battery/current_now' 2>/dev/null | tr -d '\r')
      if [ "$st" != "Charging" ] && [ "${cu:-1}" -lt 0 ]; then ok=YES; break; fi
    done
    if [ "$ok" = YES ]; then
      CHG_NODE="$n"; CHG_SAVED="$cur"
      echo "[$LABEL] 已停充: $n $cur → $want (status=$st current_now=$cu)"
      break
    fi
    adb -s "$SERIAL" shell "su -c 'echo $cur > $n'" >/dev/null 2>&1
  done
  [ -z "$CHG_NODE" ] && echo "[$LABEL] 警告: 没能停充, 功耗那一格会因充电态而作废"
fi

# 主动散热风扇的状态。红魔的风扇没有 sysfs 转速节点 (vendor HAL 里, /sys 下找不到 fan/rpm),
# 能读到的只有这几个 settings 键 —— 但它必须记: 风扇开不开直接决定热状态,
# 而热状态决定帧率与功耗。两条通路是并排采的, 风扇对两边的影响一致;
# 可**跨轮**比较时若一轮开一轮关, 差值里混的就全是风扇。
{
  for k in fan_state_of_manual fan_state_of_mode fan_state_of_charging \
           liquid_cooling_off_on liquid_cooling_main_switch; do
    printf '%s=%s\n' "$k" "$(adb -s "$SERIAL" shell settings get system "$k" 2>/dev/null | tr -d '\r')"
  done
  printf 'battery_status=%s\n' \
    "$(adb -s "$SERIAL" shell 'cat /sys/class/power_supply/battery/status' 2>/dev/null | tr -d '\r')"
  printf 'battery_capacity_pct=%s\n' "${CAP:-?}"
  printf 'charging_suspended_via=%s\n' "${CHG_NODE:-(未停充)}"
} > "$OUTDIR/test_conditions.txt" 2>&1 || true

# 1) 负载后台起跑
if [ "$WORKLOAD" = refbench ]; then
  # 帧数要够铺满 LEAD + CAPDUR, 而且要按**最快**的帧率算, 不是按屏幕刷新率算:
  # sr_pipeline 在这台机器上实测跑到 ~470 fps (它不等 vsync), 按 120 fps 估的
  # 4320 帧只够跑 9 秒 —— 采集窗口还没过半应用就退了, HiSmartPerf 一条样本都收不到,
  # 而报出来的症状是「设备端采集器没给出任何实时样本」, 看着像通路坏了。
  FRAMES=$(( (LEAD + CAPDUR) * 1000 ))
  adb -s "$SERIAL" shell "am force-stop $PKG" >/dev/null 2>&1 || true
  ( adb -s "$SERIAL" shell "am start -W -n $PKG/android.app.NativeActivity \
      --es scene sr_pipeline --es run_id xcheck_$LABEL --es frames $FRAMES \
      --es intensity 1.0 --es knob.postfx off" ) > "$OUTDIR/workload.log" 2>&1
  WL_PID=""
else
  [ -f "$WL" ] || { echo "找不到负载脚本 $WL" >&2; exit 2; }
  # 不传 --app: 那只是开 script 的遥测, 遥测本身耗电 (见 run_once.sh)
  ( cd "$ROOT" && "$PF" script --task "xcheck_$LABEL" --serial "$SERIAL" \
      --no-screen "$WL" ) > "$OUTDIR/workload.log" 2>&1 &
  WL_PID=$!
fi

sleep "$LEAD"

# 2) 同一窗口里并排起三路采集
#    A1: ftrace (逐帧口径的事实来源)
adb -s "$SERIAL" shell "su -c 'sh /data/local/tmp/ftrace_capture.sh $CAPDUR /data/local/tmp/xcheck_$LABEL.txt $PKG'" \
    > "$OUTDIR/capture.log" 2>&1 &
FT_PID=$!

#    A2: 我们的 sysfs 电源轨 (每 200ms 一条, 跑满窗口)
( cd "$ROOT" && ./phonefarm perf --serial "$SERIAL" --source sysfs \
    --power-rail battery --rounds $((CAPDUR * 5)) --json ) \
    > "$OUTDIR/perf_sysfs.json" 2>"$OUTDIR/perf_sysfs.err" &
SYS_PID=$!

#    B: HiSmartPerf 通路 (设备端每秒一条)。同时把原始线格式落盘 ——
#       两边差几个百分点时, 逐秒原始序列是唯一能把「口径差异」与「窗口边缘效应」分开的证据。
( cd "$ROOT" && PHONEFARM_GPD_RAW="$OUTDIR/gp_realtime.txt" \
  ./phonefarm perf --serial "$SERIAL" --source smartperf \
    --app "$PKG" --rounds "$CAPDUR" --json ) \
    > "$OUTDIR/perf_smartperf.json" 2>"$OUTDIR/perf_smartperf.err" &
SP_PID=$!

#    负载真的在动吗: 窗口内抓两帧比像素差。
#    静止画面照样能采到帧率/功耗/温度, 报告看着一切正常 —— 只有这个差异值能把
#    "负载真的在转视角"钉死。抓在窗口前段, 离中点热区读取远一点, 别互相挤。
adb -s "$SERIAL" exec-out screencap > "$OUTDIR/frame_a.raw" 2>/dev/null
sleep 3
adb -s "$SERIAL" exec-out screencap > "$OUTDIR/frame_b.raw" 2>/dev/null
MOVED=$("$PF" frames-moving "$OUTDIR/frame_a.raw" "$OUTDIR/frame_b.raw" 2>/dev/null || echo "?")
echo "[$LABEL] 画面逐像素差 ${MOVED}% (静止画面会接近 0)"
printf 'frame_diff_pct=%s\n' "$MOVED" >> "$OUTDIR/test_conditions.txt"

#    参考: 窗口中点直读一次热区, 用来对 HiSmartPerf 报的温度
sleep $((CAPDUR / 2 - 3))
adb -s "$SERIAL" shell "su -c 'for z in /sys/class/thermal/thermal_zone*; do \
  printf \"%s %s\n\" \"\$(cat \$z/type)\" \"\$(cat \$z/temp)\"; done'" \
    > "$OUTDIR/thermal_mid.txt" 2>&1 || true

# 采集窗口末尾确认负载还活着。跑完就退的载体 (refbench 给定帧数) 会在窗口中途消失,
# 而两条通路对此的报错都是「没采到样本」—— 与通路本身坏掉长得一模一样。记一笔, 省得误判。
printf 'workload_alive_at_window_end=%s\n' \
  "$(adb -s "$SERIAL" shell "pidof $PKG" 2>/dev/null | tr -d '\r' | grep -q '[0-9]' && echo yes || echo NO)" \
  >> "$OUTDIR/test_conditions.txt"

wait "$FT_PID"  || echo "[$LABEL] 警告: ftrace 采集非零退出"
wait "$SYS_PID" || echo "[$LABEL] 提示: sysfs 通路非零退出 (可能是功耗不可信, 看 JSON 的 unavailable)"
wait "$SP_PID"  || echo "[$LABEL] 提示: HiSmartPerf 通路非零退出 (看 JSON 的 unavailable)"
[ -n "$WL_PID" ] && { wait "$WL_PID" || echo "[$LABEL] 警告: 负载脚本非零退出"; }

# 3) 拉回 ftrace 原始证据并解析
adb -s "$SERIAL" pull "/data/local/tmp/xcheck_$LABEL.txt" "$OUTDIR/trace.txt" >/dev/null 2>&1
adb -s "$SERIAL" shell "rm -f /data/local/tmp/xcheck_$LABEL.txt" >/dev/null 2>&1
"$PF" parse-trace "$OUTDIR/trace.txt" --comm "$COMM" > "$OUTDIR/summary.json"

[ "$WORKLOAD" = refbench ] && adb -s "$SERIAL" shell "am force-stop $PKG" >/dev/null 2>&1 || true

# 4) 并排成一张表
"$PF" crosscheck-report "$OUTDIR" | tee "$OUTDIR/crosscheck.txt"
