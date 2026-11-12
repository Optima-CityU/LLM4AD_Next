#!/system/bin/sh
# o1_refresh_dev.sh — O1「屏幕刷新率跟游戏帧率对齐」的设备端小工具 (root 下执行)。
#
# 主机端脚本 (probe_refresh.sh / run_refresh_ab.sh) 把它推到 /data/local/tmp 下
# 带时间戳的新文件名, 再按子命令调用; 引号多的命令都放在这里, 主机端只传简单参数。
#
# 纪律:
#   - 只读子命令不写任何东西。
#   - 写入只有三种: settings put (拒绝写 null/空 —— 那等于 delete, 违反「绝不删除」),
#     SurfaceFlinger 1035 调试强制模式 (内存态, 不落盘), watch 的日志文件 (新文件名, 从不覆盖)。
#   - 不删除任何文件; watch 的停止标志由主机端新建, 本脚本只看它在不在。
#   - 不用小数秒 sleep (Android 的 sh 不保证支持)。
set -u

cmd="${1:-help}"; [ $# -gt 0 ] && shift

# ── 当前真实生效的显示模式 (三路独立读数, 主机端 refresh_analyze.py active 解析) ──
#   DM    : DisplayManager 记录的 SurfaceFlinger 活动模式 (mActiveSfDisplayMode)
#   RR    : 渲染帧率 (mActiveRenderFrameRate, Android 14+ 才有; LTPO 下可能 < 面板刷新率)
#   SFLAT : dumpsys SurfaceFlinger --latency 第一行 = SF 当前用的刷新周期 (ns)
#   SF    : SurfaceFlinger 自己 dump 里的刷新率/VSYNC 周期行 (措辞随版本变, 原样带回)
active() {
  dumpsys display 2>/dev/null | grep -m2 -E 'mActiveSfDisplayMode=|mActiveRenderFrameRate' \
    | sed -e 's/^ */DM /' -e 's/^DM \(mActiveRenderFrameRate\)/RR \1/'
  echo "SFLAT $(dumpsys SurfaceFlinger --latency 2>/dev/null | head -1)"
  dumpsys SurfaceFlinger 2>/dev/null \
    | grep -m6 -iE 'refresh-rate|VSYNC period|activeMode|renderRate|Render rate|active mode' \
    | sed 's/^ */SF /'
  echo "UP $(cut -d' ' -f1 /proc/uptime)"
}

# 被测游戏真正收 buffer 的那个图层名 (BLAST Consumer 优先)
layer_of() {
  pkg="$1"
  l=$(dumpsys SurfaceFlinger --list 2>/dev/null | grep -F "$pkg" | grep -F 'SurfaceView' | grep -F 'BLAST' | head -1)
  [ -n "$l" ] || l=$(dumpsys SurfaceFlinger --list 2>/dev/null | grep -F "$pkg" | grep -F 'SurfaceView' | grep -v -F 'Background' | head -1)
  [ -n "$l" ] || l=$(dumpsys SurfaceFlinger --list 2>/dev/null | grep -F "$pkg" | grep -F 'BLAST' | head -1)
  printf '%s' "$l"
}

brightness() {
  echo "BR mode=$(settings get system screen_brightness_mode 2>/dev/null) value=$(settings get system screen_brightness 2>/dev/null) float=$(settings get system screen_brightness_float 2>/dev/null)"
  dumpsys display 2>/dev/null | grep -m3 -E 'mBrightness=|mScreenBrightnessSetting|mCurrentScreenBrightness' | sed 's/^ */BRD /'
  echo "WAKE $(dumpsys power 2>/dev/null | grep -m1 -oE 'mWakefulness=[A-Za-z]+')"
}

case "$cmd" in
  active) active ;;

  info)
    echo "model=$(getprop ro.product.model)"
    echo "device=$(getprop ro.product.device)"
    echo "fingerprint=$(getprop ro.build.fingerprint)"
    echo "sdk=$(getprop ro.build.version.sdk)"
    echo "battery_capacity=$(cat /sys/class/power_supply/battery/capacity 2>/dev/null)"
    echo "battery_status=$(cat /sys/class/power_supply/battery/status 2>/dev/null)"
    echo "uptime=$(cut -d' ' -f1 /proc/uptime)"
    ;;

  dump_display) dumpsys display 2>/dev/null ;;
  dump_sf) dumpsys SurfaceFlinger 2>/dev/null ;;
  sf_list) dumpsys SurfaceFlinger --list 2>/dev/null ;;

  # 只列名字里带这些词的键 (全量 settings list 里有与本题无关的个人数据, 不带回)
  settings_scan)
    for ns in system secure global; do
      settings list "$ns" 2>/dev/null \
        | grep -iE 'refresh|fps|frame|hz|peak|vrr|ltpo|game|brightness|screen_off|stay_on' \
        | sed "s/^/$ns:/"
    done
    ;;

  props_scan)
    getprop 2>/dev/null | grep -iE 'refresh|fps|frame_rate|framerate|vrr|ltpo|idle_timer|display_mode|game|vsync' ;;

  services_scan)
    service list 2>/dev/null | grep -iE 'nubia|zte|game|display|refresh|perf' ;;

  cmd_help)
    echo "===== cmd display help ====="; cmd display help 2>&1 | head -200
    echo "===== cmd display get-user-preferred-display-mode ====="; cmd display get-user-preferred-display-mode 2>&1 | head -5
    echo "===== cmd display get-active-display-mode-at-start ====="; cmd display get-active-display-mode-at-start 0 2>&1 | head -5
    echo "===== cmd game help ====="; cmd game help 2>&1 | head -120
    ;;

  game_info)
    pkg="$1"
    echo "===== cmd game list-modes ====="; cmd game list-modes "$pkg" 2>&1 | head -20
    echo "===== cmd game list-configs ====="; cmd game list-configs "$pkg" 2>&1 | head -20
    echo "===== dumpsys game ====="; dumpsys game 2>&1 | head -60
    ;;

  sget) settings get "$1" "$2" 2>/dev/null ;;

  # 绝不写 null/空: 原值为 null 的键「还原」只能 delete, 本工具不做
  sput)
    ns="$1"; key="$2"; val="${3:-}"
    if [ -z "$val" ] || [ "$val" = "null" ]; then
      echo "O1_REFUSE sput $ns:$key: 拒绝写 null/空 (等于 delete)"; exit 3
    fi
    settings put "$ns" "$key" "$val" 2>/dev/null
    echo "O1_PUT $ns:$key=$(settings get "$ns" "$key" 2>/dev/null)"
    ;;

  # SurfaceFlinger 调试后门: 强制 SF 的显示模式 id (负数 = 撤掉强制, SF 回到 DisplayManager 的策略)
  sf1035)
    out=$(service call SurfaceFlinger 1035 i32 "$1" 2>&1)
    echo "O1_SF1035 id=$1 reply=$(echo "$out" | tr '\n' ' ')"
    ;;

  layer) layer_of "$1"; echo ;;

  lat)
    l=$(layer_of "$1")
    echo "LAYER $l"
    [ -n "$l" ] && dumpsys SurfaceFlinger --latency "$l" 2>/dev/null
    ;;

  brightness) brightness ;;

  # watch <输出文件> <停止标志> <包名> <重读数间隔(轮)> <最长秒数>
  # 每秒一轮: 目标图层 --latency (逐帧上屏时刻); 每 N 轮一次完整的活动模式/亮度/亮屏读数。
  # 输出文件必须是新文件名: 已存在就拒绝 (不覆盖)。
  watch)
    out="$1"; stop="$2"; pkg="$3"; heavy="${4:-5}"; maxs="${5:-7200}"
    if [ -e "$out" ]; then echo "O1_REFUSE watch: $out 已存在, 不覆盖"; exit 3; fi
    if [ -e "$stop" ]; then echo "O1_REFUSE watch: 停止标志 $stop 一开始就在"; exit 3; fi
    t0=$(cut -d' ' -f1 /proc/uptime | cut -d. -f1)
    i=0; L=""
    {
      echo "# o1_refresh_watch v1 pkg=$pkg heavy_every=$heavy"
      while [ ! -e "$stop" ]; do
        now=$(cut -d' ' -f1 /proc/uptime)
        [ $(( ${now%.*} - t0 )) -gt "$maxs" ] && { echo "# 到最长时限 $maxs s, 停"; break; }
        [ -n "$L" ] || L=$(layer_of "$pkg")
        echo "T $now"
        if [ -n "$L" ]; then
          echo "LAT_BEGIN $L"
          dumpsys SurfaceFlinger --latency "$L" 2>/dev/null
          echo "LAT_END"
          # 图层不在了 (游戏重启) 下一轮重新找
          [ "$(dumpsys SurfaceFlinger --list 2>/dev/null | grep -cF "$L")" -gt 0 ] || L=""
        fi
        if [ $((i % heavy)) -eq 0 ]; then
          echo "MONO $(grep -m1 'now at' /proc/timer_list 2>/dev/null)"
          echo "UPH $(cut -d' ' -f1 /proc/uptime)"
          active
          brightness
          echo "SET refresh_rate_mode=$(settings get system refresh_rate_mode 2>/dev/null) peak=$(settings get system peak_refresh_rate 2>/dev/null) min=$(settings get system min_refresh_rate 2>/dev/null)"
        fi
        i=$((i + 1))
        sleep 1
      done
      echo "# end $(cut -d' ' -f1 /proc/uptime)"
    } > "$out" 2>&1
    ;;

  help|*)
    echo "用法: o1_refresh_dev.sh {active|info|dump_display|dump_sf|sf_list|settings_scan|props_scan|services_scan|cmd_help|game_info <pkg>|sget <ns> <key>|sput <ns> <key> <val>|sf1035 <id>|layer <pkg>|lat <pkg>|brightness|watch <out> <stop> <pkg> [heavy] [maxs]}"
    ;;
esac
