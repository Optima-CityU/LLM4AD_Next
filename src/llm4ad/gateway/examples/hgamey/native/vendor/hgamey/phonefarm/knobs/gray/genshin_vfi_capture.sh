#!/bin/bash
# genshin_vfi_capture.sh — 原神逐帧无损采集 (插帧/超分的训练与评测数据)。驱动层的 framedump 档
# (layer/framedump.cpp): 在 vkQueuePresentKHR 处把每一张送显图原样读回落盘, 带帧号与时刻。
#
# 用法 (在 Mac 上跑, 设备是红魔 NX809J, 要 root):
#   genshin_vfi_capture.sh preflight            只读: 机型、root、/data 剩余空间、屏幕尺寸、刷新率、层是否已编
#   genshin_vfi_capture.sh build                编层 (带 framedump)
#   genshin_vfi_capture.sh mount                挂 framedump 档并启动原神 (enable_layer.sh framedump)
#   genshin_vfi_capture.sh scene <场景> [take]  抓一段: still|slow_pan|fast_turn|run|combat|ui_open
#   genshin_vfi_capture.sh all [take]           六个场景依次各抓一段
#   genshin_vfi_capture.sh list                 只读: 设备上已有哪些段、各多大、是否完成
#   genshin_vfi_capture.sh pull [段名...]       流式拉回 Mac (tar), 本地已有的段跳过; 不删设备上的任何东西
#   genshin_vfi_capture.sh off                  摘层 (enable_layer.sh off; 抓到的帧不动)
#
# 环境变量:
#   SERIAL   adb 序列号, 缺省 91253241019A (红魔)
#   N        每段 present 数, 缺省 600 (原神锁 60 帧时 = 10 秒, 约 5.0 GB 原始数据)
#   SLOTS    读回槽数 (写盘跟不上时的缓冲), 缺省 8
#   AUTO     1 = 用 adb input 自动做动作 (坐标见下, 部分未在真机核对); 0 = 提示操作员手动做, 回车开抓
#   DEST     Mac 上的落点, 缺省 /Users/yao/Desktop/code/agent/hgamey/_tmp/interp/I3/data
#   坐标 (屏幕像素, input 坐标系):
#     CAM_X0 CAM_X1 CAM_Y   拖屏转镜头, 缺省 900 2000 400 (来自 workload_spin_touch_v1.json, 实测能转镜头)
#     PAIMON_X PAIMON_Y     派蒙菜单, 缺省 190 90 (来自 genshin_quality.sh, 实测)
#     JOY_X JOY_Y           左下摇杆中心, 缺省 330 780 (未验证)
#     ATK_X ATK_Y           普攻键, 缺省 2050 880 (未验证)
#     SKILL_X SKILL_Y       元素战技键, 缺省 1830 900 (未验证)
#
# 前提: 原神已在大世界 (本脚本不登录、不点任何账号相关的界面); 游戏内帧率设 60。
# 抓完用 vfi/raw2png.py 转无损 PNG, vfi/make_triplets.py 出 (t, t+1, t+2) 三元组清单。
set -uo pipefail

SERIAL="${SERIAL:-91253241019A}"
PKG=com.miHoYo.Yuanshen
HERE="$(cd "$(dirname "$0")" && pwd)"
DEVBASE="/data/data/$PKG/files/i3dump"
DEST="${DEST:-/Users/yao/Desktop/code/agent/hgamey/_tmp/interp/I3/data}"
N="${N:-600}"
SLOTS="${SLOTS:-8}"
AUTO="${AUTO:-0}"
CAM_X0="${CAM_X0:-900}"; CAM_X1="${CAM_X1:-2000}"; CAM_Y="${CAM_Y:-400}"
PAIMON_X="${PAIMON_X:-190}"; PAIMON_Y="${PAIMON_Y:-90}"
JOY_X="${JOY_X:-330}"; JOY_Y="${JOY_Y:-780}"
ATK_X="${ATK_X:-2050}"; ATK_Y="${ATK_Y:-880}"
SKILL_X="${SKILL_X:-1830}"; SKILL_Y="${SKILL_Y:-900}"
FRAME_BYTES=$((2141 * 969 * 4))     # 渲染精度「低」时的送显尺寸 (passdump 实测); 只用于预估, 真实值看 session.json
ashell() { adb -s "$SERIAL" shell "$@" </dev/null; }
sush() { ashell "su -c '$1'"; }

secs() { echo $(( (N + 59) / 60 )); }   # 本段大约几秒 (按 60 帧)

preflight() {
  echo "== 设备"
  adb -s "$SERIAL" get-state || { echo "设备不在线" >&2; return 1; }
  echo "model=$(ashell getprop ro.product.model | tr -d '\r')  android=$(ashell getprop ro.build.version.release | tr -d '\r')"
  echo "root: $(sush id | tr -d '\r')"
  echo "wm size: $(ashell wm size | tr -d '\r' | tr '\n' ' ')"
  echo "刷新率: $(ashell "dumpsys display | grep -m3 -iE 'mActiveSfDisplayMode|refreshRate='" | tr -d '\r' | tr '\n' ' ' | cut -c1-300)"
  echo "== 空间 (抓下来的帧只增不删, 设备上会一直留着)"
  local avail_kb
  avail_kb=$(sush "df -k /data | tail -1" | awk '{print $4}' | tr -d '\r')
  echo "/data 剩余 ${avail_kb} KB"
  if [ -n "$avail_kb" ]; then
    echo "按 2141x969 RGBA8 每帧 $FRAME_BYTES 字节: 还能存约 $(( avail_kb * 1024 / FRAME_BYTES )) 帧 (层会再留 4 GB 余量)"
    echo "每段 N=$N 帧约 $(( N * FRAME_BYTES / 1000000 )) MB"
  fi
  echo "== 已有的段"
  list
  echo "== 层"
  ls -la "$HERE/build/out/libVkLayer_refknobs.so" 2>/dev/null || echo "层还没编: $0 build"
  echo "== 原神"
  echo "pid=$(ashell pidof $PKG | tr -d '\r')  (空 = 没在跑)"
  echo "layer 开关: $(bash "$HERE/enable_layer.sh" status 2>/dev/null | tr '\n' ' ')"
}

build() {
  REFBENCH_NDK="${REFBENCH_NDK:-/Users/yao/Desktop/code/agent/tools/ndk/android-ndk-r28c}" bash "$HERE/build/build_layer.sh"
}

do_mount() {
  bash "$HERE/enable_layer.sh" framedump "$PKG" || return 1
  echo "原神已重启并挂上层。手动进到大世界后再抓 (本脚本不点登录/开门)。"
  echo "核对: adb -s $SERIAL shell su -c 'logcat -b main -d' | grep 'framedump:'"
}

# 设备上某段是否结束: 输出 summary / refused / 空
seg_state() {
  sush "if [ -f $DEVBASE/$1/summary.json ]; then echo summary; elif [ -f $DEVBASE/$1/refused.json ]; then echo refused; fi" | tr -d '\r'
}

# ── 各场景的动作 (AUTO=1 时在抓帧同时执行; 时长按本段秒数) ──
act_still()     { sleep "$(secs)"; }
act_slow_pan()  { ashell "input swipe $CAM_X0 $CAM_Y $CAM_X1 $CAM_Y $(( $(secs) * 1000 ))"; }
act_fast_turn() {
  local end=$(( $(date +%s) + $(secs) )) dir=0
  while [ "$(date +%s)" -lt "$end" ]; do
    if [ $dir = 0 ]; then ashell "input swipe $CAM_X0 $CAM_Y $CAM_X1 $CAM_Y 180"; dir=1
    else ashell "input swipe $CAM_X1 $CAM_Y $CAM_X0 $CAM_Y 180"; dir=0; fi
    sleep 0.4
  done
}
act_run()       { ashell "input swipe $JOY_X $JOY_Y $JOY_X $(( JOY_Y - 160 )) $(( $(secs) * 1000 ))"; }
act_combat() {
  local end=$(( $(date +%s) + $(secs) )) k=0
  while [ "$(date +%s)" -lt "$end" ]; do
    if [ $((k % 6)) = 0 ]; then ashell "input tap $SKILL_X $SKILL_Y"; else ashell "input tap $ATK_X $ATK_Y"; fi
    k=$((k + 1)); sleep 0.25
  done
}
act_ui_open() {
  sleep 1
  ashell "input tap $PAIMON_X $PAIMON_Y"     # 打开派蒙菜单 (全屏 UI + 背景模糊过渡)
  sleep $(( $(secs) / 2 ))
  ashell "input keyevent 4"                  # 返回, 关菜单 (关闭过渡也在段内)
  sleep $(( $(secs) / 2 ))
}
hint() {
  case "$1" in
    still)     echo "角色站定, 不碰屏幕 (草/水/云照常动)";;
    slow_pan)  echo "右半屏缓慢匀速拖动转镜头, 约 $(secs) 秒转一小段";;
    fast_turn) echo "右半屏快速来回甩镜头";;
    run)       echo "推摇杆让角色一直向前跑, 镜头不动";;
    combat)    echo "对着敌人或空地连续普攻 + 放元素战技 (特效、粒子、闪光)";;
    ui_open)   echo "开抓后 1 秒点开派蒙菜单, 停一会儿再关掉";;
  esac
}

scene() {
  local sc="$1" take="${2:-1}" label st i limit
  case "$sc" in still|slow_pan|fast_turn|run|combat|ui_open) ;; *) echo "未知场景 $sc" >&2; return 2;; esac
  [ -n "$(ashell pidof $PKG | tr -d '\r')" ] || { echo "原神没在跑" >&2; return 1; }
  label="${sc}_t${take}_$(date +%Y%m%d_%H%M%S)"
  sush "setprop debug.knobs.framedump_n $N; setprop debug.knobs.framedump_slots $SLOTS" >/dev/null
  echo "── $label: $(hint "$sc")"
  if [ "$AUTO" != 1 ]; then
    read -r -p "   准备好后按回车开抓 (开抓后马上按上面做, 共约 $(secs) 秒)..." _ </dev/tty
  fi
  sush "setprop debug.knobs.framedump_go $label" >/dev/null
  if [ "$AUTO" = 1 ]; then "act_$sc"; fi
  # 等层写 summary.json (写盘可能比 present 慢, 留足时间)
  limit=$(( $(secs) * 6 + 60 ))
  for i in $(seq 1 "$limit"); do
    st=$(seg_state "$label")
    [ -n "$st" ] && break
    sleep 1
  done
  case "$st" in
    summary) echo "   完成: $(sush "cat $DEVBASE/$label/summary.json" | tr -d '\r')";;
    refused) echo "   层拒抓: $(sush "cat $DEVBASE/$label/refused.json" | tr -d '\r')"; return 1;;
    *)       echo "   ${limit}s 内没等到结束标记。查: adb -s $SERIAL shell su -c 'logcat -b main -d' | grep framedump"; return 1;;
  esac
}

all() {
  local take="${1:-1}" sc
  for sc in still slow_pan fast_turn run combat ui_open; do scene "$sc" "$take" || echo "   ($sc 没成, 继续下一个)"; done
}

list() {
  sush "cd $DEVBASE 2>/dev/null && for d in *; do [ -d \"\$d\" ] || continue; \
        s=\$(du -sk \"\$d\" | cut -f1); n=\$(ls \"\$d\" | grep -c raw); \
        if [ -f \"\$d/summary.json\" ]; then t=done; elif [ -f \"\$d/refused.json\" ]; then t=refused; else t=open; fi; \
        echo \"\$d \$t frames=\$n kb=\$s\"; done" | tr -d '\r'
}

pull() {
  local raw="$DEST/raw" segs s n_dev n_loc
  mkdir -p "$raw"
  if [ $# -gt 0 ]; then segs="$*"; else segs=$(list | awk '$2=="done"{print $1}'); fi
  for s in $segs; do
    if [ -e "$raw/$s" ]; then echo "跳过 $s (本地已有, 不覆盖)"; continue; fi
    echo "拉 $s ..."
    # su 下 tar 打包成流, 不在设备上留中间文件; 本地 tar -k 不覆盖任何已有文件
    adb -s "$SERIAL" exec-out "su -c 'tar -C $DEVBASE -cf - $s'" | tar -x -k -C "$raw" -f -
    n_dev=$(sush "ls $DEVBASE/$s | grep -c raw" | tr -d '\r')
    n_loc=$(ls "$raw/$s" 2>/dev/null | grep -c raw)
    echo "   设备 $n_dev 帧, 本地 $n_loc 帧 $( [ "$n_dev" = "$n_loc" ] && echo 一致 || echo 不一致!)"
    echo "$(date +%FT%T) $s dev=$n_dev local=$n_loc" >> "$DEST/pull_log.txt"
  done
}

case "${1:-}" in
  preflight) preflight ;;
  build) build ;;
  mount) do_mount ;;
  scene) shift; scene "$@" ;;
  all) shift; all "$@" ;;
  list) list ;;
  pull) shift; pull "$@" ;;
  off) bash "$HERE/enable_layer.sh" off ;;
  *) sed -n '2,32p' "$0"; exit 2 ;;
esac
