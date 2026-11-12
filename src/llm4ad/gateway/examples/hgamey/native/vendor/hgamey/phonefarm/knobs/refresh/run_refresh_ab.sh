#!/bin/bash
# run_refresh_ab.sh — O1 实验: 原神锁帧时, 屏幕刷新率高 (120/144 Hz) vs 跟帧率对齐 (60 Hz), 整机功耗与帧时。
#
# 每一组 = 一个游戏帧率上限 + 一对刷新率 (对照 Hz / 改写 Hz), 缺省两组:
#   g60:60:120:60   游戏 60 帧, 对照面板 120 Hz, 改写 60 Hz
#   g30:30:max:60   游戏 30 帧, 对照面板最高档 (本机 144 Hz), 改写 60 Hz
#   (面板没有 30 Hz 模式 —— 本机 dumpsys display 只有 60/90/120/144; 探测会再确认一次)
# 可选: g60a:60:auto:60 (出厂 auto 档 vs 60 Hz), g30b:30:120:60。
#
# 一组的流程:
#   1. 冷启动原神, `phonefarm onboard --enter-only --no-guest --no-consent` 带进大世界 (要人登录就停, 绝不登录),
#      截图存证
#   2. 在游戏界面里改帧率档位 (genshin_fpscap.sh, 缺省 FPSCAP_METHOD=ui: 派蒙→设置→图像→帧率, OCR 核对后点;
#      R2 审查 #10)。FPSCAP_METHOD=file 是旧办法 (停游戏改偏好设置文件里一个数字), 需用户批准, 顺序变成先改后启动
#   3. 设对照刷新率, 读**实际活动模式**核对; 再试一下改写刷新率也能切过去, 切回对照;
#      两档都在**滑动中**再读一次 (R2 审查 #7): 触屏把面板抬上去的话, 测量窗口里的触控负载会让该臂全部无效, 本组停
#   4. 用游戏图层的上屏帧间隔核对游戏帧率上限真的是 30/60 (对不上 = 档位映射不对, 停)
#   5. 设备端起 watch (每秒记游戏图层逐帧上屏时刻, 每 5 秒记实际活动模式/亮度/亮屏)
#   6. `phonefarm eval` kind=sysparam: 改写臂 = refresh_rate_mode 改写档, 对照臂 = 对照档;
#      停充放电态测电池轨功耗, 目标进程 ftrace 帧时, 臂序随机, 精确置换检验 (测量链的现成通路)
#   7. 画面抽查: 游戏站着不动, 对照/改写交错截 screencap 裸帧 (c c k c k c), phonefarm iq-judge
#   8. refresh_analyze.py: 官方判定 + 逐臂「面板刷新率真生效」核对 + 上屏节奏配对检验 → 结论
# 结束 (含 Ctrl-C) 把游戏帧率档位、刷新率、亮度、息屏时间写回记录的原值, 并核对。
#
# 用法:
#   DRY_RUN=1 bash run_refresh_ab.sh            # 不碰手机: 打印全部命令, 生成请求并离线校验
#   MODES_JSON=<probe_*_game/refresh_modes.json> bash run_refresh_ab.sh
# 环境变量:
#   SERIAL PAIRS(8) SECONDS_PER_ARM(30) O1_GROUPS("g60:60:120:60 g30:30:max:60") ORDER_SEED(随机)
#   PF_BIN(integration 的 release 二进制) PF_ROOT(integration worktree) OUT
#   BRIGHTNESS(缺省 = 当前亮度值, 模式改手动) COOL_C(42) TEMP_CAP_C(70) IQ(1) CURTAIL(0)
#   FPSCAP_METHOD(ui|file, 缺省 ui)
#   MODES_JSON (probe_refresh.sh 的产出; 缺省用本机 2026-10 探测记录: 0=auto 1=60 2=90 3=120 4=144)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/refresh_lib.sh"

PAIRS="${PAIRS:-8}"
SECONDS_PER_ARM="${SECONDS_PER_ARM:-30}"
O1_GROUPS="${O1_GROUPS:-g60:60:120:60 g30:30:max:60}"  # 不能叫 GROUPS: 那是 bash 内置变量, 赋值无效
ORDER_SEED="${ORDER_SEED:-$(( $(date +%s) % 1000000 ))}"
# 缺省用本仓库: 旧 worktree 的工具会改旧锁域、拆掉租约存储的退役围栏
PF_ROOT="${PF_ROOT:-$(cd "$HERE/../.." && pwd)}"
PF_BIN="${PF_BIN:-$PF_ROOT/src/target/release/phonefarm}"
COOL_C="${COOL_C:-42}"
TEMP_CAP_C="${TEMP_CAP_C:-70}"
IQ="${IQ:-1}"
CURTAIL="${CURTAIL:-0}"
SETTLE_S="${SETTLE_S:-3}"
export FPSCAP_METHOD="${FPSCAP_METHOD:-ui}"
OUT="${OUT:-/Users/yao/Desktop/code/agent/hgamey/_tmp/opt/O1/runs/ab_${O1_TS}$([ "$DRY_RUN" = 1 ] && echo _dryrun)}"
WORKLOAD="$PF_ROOT/loop_v1/scripts/workload_spin_touch_v1.json"

[ "$PAIRS" -ge 5 ] 2>/dev/null || die "PAIRS 至少 5 (stat-guards: 5 对时精确置换最小 p=0.0079 < 0.05/3)"
[ -e "$OUT" ] && die "$OUT 已存在, 不覆盖"
mkdir -p "$OUT"
exec > >(tee -a "$OUT/run.log") 2> >(tee -a "$OUT/run.log" >&2)

[ -x "$PF_BIN" ] || die "找不到 phonefarm 二进制 $PF_BIN (先在 $PF_ROOT/src 下 cargo build --release, 或设 PF_BIN)"
[ -f "$WORKLOAD" ] || die "找不到触控负载 $WORKLOAD"
{ "$PF_BIN" iq-judge 2>&1 || true; } | grep -q '用法' || log "警告: $PF_BIN 不认 iq-judge (画面抽查会判「没做」)"

# ── 刷新率档位表 ──
if [ -n "${MODES_JSON:-}" ]; then
  [ -f "$MODES_JSON" ] || die "MODES_JSON=$MODES_JSON 不存在"
  cp "$MODES_JSON" "$OUT/refresh_modes_used.json"
else
  log "没给 MODES_JSON, 用 2026-10 历史探测记录 (桌面场景; 上机前最好先跑 WITH_GAME=1 的 probe_refresh.sh)"
  cat > "$OUT/refresh_modes_used.json" <<'J'
{"scene": "history(loop_v1 probe_sysparam, 桌面)", "original_mode": "0", "all_refresh_rate": "auto,60,90,120,144",
 "hz_to_mode": {"60": "1", "90": "2", "120": "3", "144": "4"}, "max_hz": 144}
J
fi
mode_for() { # <Hz|max|auto> → 档位
  python3 -I - "$OUT/refresh_modes_used.json" "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); want = sys.argv[2]
if want == "auto":
    labels = (d.get("all_refresh_rate") or "").split(",")
    print(labels.index("auto") if "auto" in labels else ""); sys.exit()
if want == "max":
    want = str(int(float(d.get("max_hz") or 0)))
print(d.get("hz_to_mode", {}).get(want, ""))
PY
}
hz_for() { # <Hz|max|auto> → 预期面板 Hz (auto 返回空: 不预设)
  case "$1" in
    auto) echo "" ;;
    max) python3 -I -c 'import json,sys; print(int(float(json.load(open(sys.argv[1]))["max_hz"])))' "$OUT/refresh_modes_used.json" ;;
    *) echo "$1" ;;
  esac
}

# ── 组的顺序随机 (种子记下来) ──
read -r -a GLIST <<< "$(python3 -I -c 'import random,sys; g=sys.argv[2].split(); random.Random(int(sys.argv[1])).shuffle(g); print(" ".join(g))' "$ORDER_SEED" "$O1_GROUPS")"
NG=${#GLIST[@]}
EST_MIN=$(( NG * (6 + (2 * PAIRS * (SECONDS_PER_ARM + 100)) / 60 + 5 + 2) ))
log "组顺序 (种子 $ORDER_SEED): ${GLIST[*]}; 每组 $PAIRS 对; 预计约 ${EST_MIN} 分钟 (每臂约 $((SECONDS_PER_ARM + 100)) 秒含等冷与收尾, 每组首臂可能多等 5 分钟)"
{
  echo "order_seed=$ORDER_SEED"; echo "groups=${GLIST[*]}"; echo "pairs=$PAIRS"; echo "seconds_per_arm=$SECONDS_PER_ARM"
  echo "pf_bin=$PF_BIN"; echo "pf_head=$(git -C "$PF_ROOT" rev-parse --short HEAD 2>/dev/null)"
  echo "o1_head=$(git -C "$HERE" rev-parse --short HEAD 2>/dev/null)"; echo "estimate_min=$EST_MIN"
} > "$OUT/plan.txt"

preflight_device
push_dev_tool

# ── 原值 (先记, 再动) ──
O_RRM=$(sget system refresh_rate_mode)
O_BMODE=$(sget system screen_brightness_mode)
O_BVAL=$(sget system screen_brightness)
O_SOT=$(sget system screen_off_timeout)
CAPAC=$(dsh "cat /sys/class/power_supply/battery/capacity")
{
  echo "refresh_rate_mode=$O_RRM"; echo "screen_brightness_mode=$O_BMODE"; echo "screen_brightness=$O_BVAL"
  echo "screen_off_timeout=$O_SOT"; echo "battery_capacity=$CAPAC"
  echo "active=$(active_json)"
} > "$OUT/original_state.txt"
dev info > "$OUT/device_info.txt"
bash "$HERE/genshin_fpscap.sh" status "$OUT/prefs" > "$OUT/genshin_fps_before.txt" 2>&1
log "原值: $(tr '\n' ' ' < "$OUT/original_state.txt") 原神: $(cat "$OUT/genshin_fps_before.txt" | tail -1)"
if [ "$DRY_RUN" != 1 ]; then
  case "$O_RRM" in ""|null) die "refresh_rate_mode 原值为空: 改了以后只能 delete 才回得去, 不跑";; esac
  case "$O_BVAL" in ""|null) die "screen_brightness 原值为空, 不跑";; esac
  [ "${CAPAC:-0}" -ge 40 ] 2>/dev/null || die "电量 ${CAPAC}% < 40%: 全程停充放电约 1.5 小时, 先充电"
  [ "$CAPAC" -ge 100 ] && log "警告: 电量 100% —— 测量链要求先放到 100% 以下 (满电时停充可能读不到放电电流)"
fi
if [ "$DRY_RUN" = 1 ]; then  # dry-run 读不到原值, 填占位让流程走完
  O_RRM=${O_RRM:-0}; O_BMODE=${O_BMODE:-1}; O_BVAL=${O_BVAL:-128}; O_SOT=${O_SOT:-60000}
fi
BRIGHTNESS="${BRIGHTNESS:-$O_BVAL}"

TOUCHED=0
restore_fpscap() {   # 界面办法要游戏在大世界: 进程没了就先启动 + onboard 再改回; 都不行就报 FPSCAP_RESTORE_FAIL (不自动改文件)
  if [ "$FPSCAP_METHOD" = ui ] && [ -e "$OUT/prefs/fpscap_ui_orig.txt" ] && [ "$DRY_RUN" != 1 ]; then
    if [ -z "$(dsh "pidof $PKG")" ]; then
      echo "fpscap 还原: 游戏进程不在, 先启动并 onboard 进大世界"
      dsh "monkey -p $PKG -c android.intent.category.LAUNCHER 1" >/dev/null; sleep 20
      "$PF_BIN" onboard --package "$PKG" --serial "$SERIAL" --enter-only --no-guest --no-consent --out "$OUT/onboard_restore" \
        > "$OUT/onboard_restore.log" 2>&1 || echo "fpscap 还原: onboard 退出码 $? (见 $OUT/onboard_restore.log)"
      sleep 10
    fi
  fi
  bash "$HERE/genshin_fpscap.sh" restore "$OUT/prefs"
}
restore_all() {
  local rc=$?
  trap '' INT TERM   # 还原期间第二次 Ctrl-C 不许打断 (R2 审查 #16)
  trap - EXIT
  [ "$TOUCHED" = 1 ] || exit $rc
  log "还原到原值 (Ctrl-C 已屏蔽到还原结束)..."
  {
    # 中途被打断时 eval 可能没来得及自己还原: 调测量链自带的还原 (没施加过就是空操作)
    dsu "[ -f $DEV_TMP/knob_sysparam.sh ] && sh $DEV_TMP/knob_sysparam.sh restore"
    dsu "[ -f $DEV_TMP/charge_suspend.sh ] && sh $DEV_TMP/charge_suspend.sh restore"
    [ -n "${WATCH_STOP:-}" ] && dsu "touch $WATCH_STOP"
    restore_fpscap
    sput system refresh_rate_mode "$O_RRM"
    sput system screen_brightness "$O_BVAL"
    sput system screen_brightness_mode "$O_BMODE"
    sput system screen_off_timeout "$O_SOT"
    sleep "$SETTLE_S"
    echo "readback refresh_rate_mode=$(sget system refresh_rate_mode) (原 $O_RRM)"
    echo "readback screen_brightness=$(sget system screen_brightness) (原 $O_BVAL)"
    echo "readback screen_brightness_mode=$(sget system screen_brightness_mode) (原 $O_BMODE)"
    echo "readback screen_off_timeout=$(sget system screen_off_timeout) (原 $O_SOT)"
    echo "active_after=$(active_json)"
  } > "$OUT/restore_check.txt" 2>&1
  cat "$OUT/restore_check.txt" >&2
  if [ "$DRY_RUN" != 1 ]; then
    bad=0
    for k in refresh_rate_mode screen_brightness screen_brightness_mode screen_off_timeout; do
      grep -E "^readback $k=" "$OUT/restore_check.txt" | sed -E 's/^readback [a-z_]+=([^ ]*) \(原 (.*)\)$/\1 \2/' \
        | { read -r a b; [ "$a" = "$b" ] || { echo "RESTORE_FAIL $k: $a ≠ 原 $b"; exit 1; }; } || bad=1
    done
    grep -q 'FPSCAP_RESTORE_FAIL' "$OUT/restore_check.txt" && bad=1
    [ "$bad" = 0 ] && log "RESTORE_OK 全部写回原值" || log "RESTORE_FAIL —— 看 $OUT/restore_check.txt, 人工处理"
    [ -e "$OUT/prefs/FILE_EDIT_USED.txt" ] && log "注意: 本次用了偏好设置文件单值改写 (FPSCAP_METHOD=file), 见 $OUT/prefs/FILE_EDIT_USED.txt, 报告里要单独列出"
  fi
  exit $rc
}
trap restore_all EXIT
trap 'exit 130' INT TERM

# ── 固定亮度、保证不息屏 ──
TOUCHED=1
sput system screen_brightness_mode 0 >/dev/null
sput system screen_brightness "$BRIGHTNESS" >/dev/null
if [ "$DRY_RUN" = 1 ] || [ "${O_SOT:-0}" -lt 1800000 ] 2>/dev/null; then sput system screen_off_timeout 1800000 >/dev/null; fi
log "亮度固定为手动 $BRIGHTNESS (原 模式=$O_BMODE 值=$O_BVAL); 读数: $(dev brightness | tr '\n' ' ')"

run_group() { # <name:cap:ctrl:knob> <组序号>
  local spec="$1" gi="$2" name cap c_spec k_spec c_mode k_mode c_hz k_hz G a p
  IFS=: read -r name cap c_spec k_spec <<< "$spec"
  c_mode=$(mode_for "$c_spec"); k_mode=$(mode_for "$k_spec"); c_hz=$(hz_for "$c_spec"); k_hz=$(hz_for "$k_spec")
  G="$OUT/$name"; mkdir -p "$G"
  log "══ 组 $name: 游戏 $cap 帧, 对照 $c_spec (档 $c_mode, ${c_hz:-自适应} Hz) vs 改写 $k_spec (档 $k_mode, $k_hz Hz) ══"
  [ -n "$c_mode" ] && [ -n "$k_mode" ] && [ -n "$k_hz" ] || { log "组 $name: 档位表里找不到对应档, 跳过"; return 1; }

  # 1-2. 进大世界 + 帧率档位 (file 办法要先停游戏改文件再启动; ui 办法进大世界后在界面里改)
  if [ "$FPSCAP_METHOD" = file ]; then
    bash "$HERE/genshin_fpscap.sh" set "$cap" "$OUT/prefs" || { log "组 $name: 改帧率档位失败"; return 1; }
  fi
  dsh "monkey -p $PKG -c android.intent.category.LAUNCHER 1" >/dev/null
  sleep 20
  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] $PF_BIN onboard --package $PKG --serial $SERIAL --enter-only --no-guest --no-consent --out $G/onboard" >&2
  else
    "$PF_BIN" onboard --package "$PKG" --serial "$SERIAL" --enter-only --no-guest --no-consent --out "$G/onboard" \
      > "$G/onboard.log" 2>&1
    case $? in
      0) ;;
      3) log "组 $name: 进游戏要人处理 (登录/协议), 按规矩不登录, 本组停"; return 1 ;;
      *) log "组 $name: 进不了大世界 (见 $G/onboard.log)"; return 1 ;;
    esac
    sleep 15
    "$ADB" -s "$SERIAL" exec-out screencap -p > "$G/in_world.png" </dev/null
  fi
  if [ "$FPSCAP_METHOD" = ui ]; then
    bash "$HERE/genshin_fpscap.sh" set "$cap" "$OUT/prefs" || { log "组 $name: 界面里改帧率档位失败 (截图在 $OUT/prefs/ui_*/), 本组停"; return 1; }
    sleep 5
  fi

  # 3a. 出厂档 (原值, 一般是 auto) 在游戏里实际跑多少 Hz —— 历史探测里原神场景 auto 读到过 60 Hz,
  #     要是游戏里本来就是 60 Hz, 「对齐」对出厂默认没有增益, 只对手动开了高刷的用户有意义
  sput system refresh_rate_mode "$O_RRM" >/dev/null; sleep "$SETTLE_S"
  echo "orig_mode_ingame $(active_json)" >> "$G/mode_checks.txt"
  dsh "input swipe 900 400 2000 400 1500" >/dev/null &
  sleep 1; echo "orig_mode_ingame_touch $(active_json)" >> "$G/mode_checks.txt"; wait
  log "  原档 $O_RRM 在游戏里: $(grep '^orig_mode_ingame ' "$G/mode_checks.txt" | tail -1 | cut -d' ' -f2-)"

  # 3. 刷新率真生效核对 (读实际活动模式)
  sput system refresh_rate_mode "$k_mode" >/dev/null; sleep "$SETTLE_S"
  a=$(active_json); echo "knob_check $a" >> "$G/mode_checks.txt"
  p=$(json_get "$a" panel_hz)
  if [ "$DRY_RUN" != 1 ] && ! { [ -n "$p" ] && hz_near "$p" "$k_hz"; }; then
    log "组 $name: 改写档 $k_mode 写进去, 实际面板 ${p:-?} Hz ≠ $k_hz Hz —— 这个方法在游戏里不生效, 本组停"; return 1
  fi
  sput system refresh_rate_mode "$c_mode" >/dev/null; sleep "$SETTLE_S"
  a=$(active_json); echo "ctrl_check $a" >> "$G/mode_checks.txt"
  p=$(json_get "$a" panel_hz)
  if [ -z "$c_hz" ]; then c_hz="$p"; log "  对照 auto 档在游戏里实际 ${p:-?} Hz, 按它核对"; fi
  if [ "$DRY_RUN" != 1 ] && ! { [ -n "$p" ] && hz_near "$p" "$c_hz"; }; then
    log "组 $name: 对照档 $c_mode 实际面板 ${p:-?} Hz ≠ $c_hz Hz, 本组停"; return 1
  fi
  if [ "$DRY_RUN" != 1 ] && hz_near "$c_hz" "$k_hz"; then
    log "组 $name: 对照与改写实际都是 $c_hz Hz, 没得比 (auto 档在游戏里已经对齐?), 记下结论, 本组不测"
    echo "对照 $c_spec 在游戏里实际 $c_hz Hz = 改写 $k_hz Hz" > "$G/SKIPPED_same_hz.txt"; return 0
  fi
  log "  刷新率核对通过: 对照 $c_hz Hz / 改写 $k_hz Hz"

  # 3b. 滑动中两档守不守得住 (R2 审查 #7): 负载是触控脚本, 触屏抬频的话测量窗口里的读数会让该臂全部无效
  local pt
  for arm_spec in "knob:$k_mode:$k_hz" "ctrl:$c_mode:$c_hz"; do
    IFS=: read -r an am ah <<< "$arm_spec"
    [ "$an" = ctrl ] && [ "$c_spec" = auto ] && continue   # auto 档不预设 Hz, 只记录
    sput system refresh_rate_mode "$am" >/dev/null; sleep "$SETTLE_S"
    dsh "input swipe 900 400 2000 400 2500" >/dev/null &
    sleep 1; a=$(active_json); wait
    echo "${an}_touch_check $a" >> "$G/mode_checks.txt"
    pt=$(json_get "$a" panel_hz)
    if [ "$DRY_RUN" != 1 ] && ! { [ -n "$pt" ] && hz_near "$pt" "$ah"; }; then
      sput system refresh_rate_mode "$c_mode" >/dev/null
      log "组 $name: ${an} 档 $am 静止时 $ah Hz, 滑动中面板读到 ${pt:-?} Hz (触屏抬频) —— 触控负载下这一臂守不住设定刷新率, 本组停"
      echo "$an 档 $am 滑动中 ${pt:-?} Hz ≠ $ah Hz" > "$G/STOPPED_touch_boost.txt"; return 1
    fi
  done
  sput system refresh_rate_mode "$c_mode" >/dev/null; sleep "$SETTLE_S"
  log "  滑动中刷新率核对通过 (改写 $k_hz Hz / 对照 ${c_hz} Hz 都守住)"

  # 4. 游戏帧率上限核对 (上屏帧间隔中位数)
  local pj p50
  pj=$(present_fps "$G" "$cap"); echo "cap_check $pj" >> "$G/mode_checks.txt"
  p50=$(json_get "$pj" present_p50_ms)
  if [ "$DRY_RUN" != 1 ]; then
    { [ -n "$p50" ] && hz_near "$(python3 -c "print(1000/$p50)")" "$cap" 0.05; } \
      || { log "组 $name: 上屏帧间隔中位数 ${p50:-?} ms, 不是 $cap 帧 —— 帧率档位映射可能不对 (FPS_INDEX_${cap}), 本组停"; return 1; }
  fi
  log "  游戏帧率上限核对通过: 上屏帧间隔中位数 ${p50:-?} ms"

  # 5. watch
  local wts WATCH_OUT
  wts=$(date +%Y%m%d-%H%M%S)
  WATCH_OUT="$DEV_TMP/o1_watch_${name}_$wts.txt"; WATCH_STOP="$DEV_TMP/o1_watch_${name}_$wts.stop"
  dsu "nohup sh $DEVSH watch $WATCH_OUT $WATCH_STOP $PKG 5 14400 >/dev/null 2>&1 &"
  sleep 2

  # 6. eval
  local req="$G/request.json"
  new_file "$req"
  python3 -I - "$req" <<PY || { log "生成请求失败"; return 1; }
import json, sys
req = {
  "version": 2, "kind": "sysparam",
  "candidate_id": "o1_${name}_${k_hz}hz_vs_${c_hz}hz",
  "serial": "$SERIAL", "package": "$PKG",
  "workload": {"carrier": "game_replay", "script": "$WORKLOAD", "seconds": $SECONDS_PER_ARM, "render_comm": None},
  "conditions": {"cool_c": $COOL_C, "temp_cap_c": $TEMP_CAP_C, "require_root": True, "require_discharging": True,
                 "charging_suspend": {"enabled": True}, "ingame_fps_setting": "$cap",
                 "screen_brightness": "$BRIGHTNESS (手动)",
                 "notes": "O1: 对照 refresh_rate_mode=$c_mode (${c_hz} Hz), 改写 $k_mode (${k_hz} Hz); 实际面板刷新率由 o1 watch 逐臂核对"},
  "protocol": {"rounds": $PAIRS, "alternating": True, "p_threshold": 0.05, "order_seed": $ORDER_SEED + $gi, "stage": "full"},
  "payload": {
    "params": {"setting.system.refresh_rate_mode": "$k_mode"},
    "whitelist": {"setting.system.refresh_rate_mode": {
        "kind": "setting", "path": "system:refresh_rate_mode", "values": sorted({"$c_mode", "$k_mode"}),
        "current": "$c_mode", "group": "refresh", "effect": "live(o1 probe: 实测活动模式跟着变)"}},
    "why": "O1: 游戏锁 ${cap} 帧时把面板从 ${c_hz} Hz 降到 ${k_hz} Hz, 画面帧不变, 看整机功耗"
  }
}
open(sys.argv[1], "x").write(json.dumps(req, ensure_ascii=False, indent=1))
PY
  local rc=0 ev_args=(eval --request "$req" --serial "$SERIAL" --out "$G/eval")
  [ "$CURTAIL" = 1 ] || ev_args+=(--no-curtail)
  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] $PF_BIN ${ev_args[*]} > $G/report.json" >&2
    # 离线校验请求形状: 去掉 serial 后接收端会在碰设备之前做完 validate_v2, 然后报「需要真机」
    python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); d.pop("serial"); open(sys.argv[2],"x").write(json.dumps(d))' "$req" "$G/request_offline_check.json"
    (cd "$PF_ROOT" && "$PF_BIN" eval --request "$G/request_offline_check.json" --out "$G/eval_offline_check") \
      > "$G/offline_check.txt" 2>&1
    log "  请求离线校验: $(tail -1 "$G/offline_check.txt")"
  else
    (cd "$PF_ROOT" && "$PF_BIN" "${ev_args[@]}") > "$G/report.json" 2> "$G/eval.stderr" || rc=$?
    log "  eval 退出码 $rc, 状态 $(json_get "$(cat "$G/report.json" 2>/dev/null || echo '{}')" status)"
  fi

  # 7. 画面抽查 (游戏站着不动, 交错截帧)
  local iq_rc="" shots_c="" shots_k="" seq i=0 m f
  if [ "$IQ" = 1 ]; then
    for seq in c c k c k c; do
      i=$((i + 1))
      m=$([ "$seq" = c ] && echo "$c_mode" || echo "$k_mode")
      sput system refresh_rate_mode "$m" >/dev/null; sleep "$SETTLE_S"
      echo "iq_${i}_$seq $(active_json)" >> "$G/mode_checks.txt"
      f="$G/iq_${i}_$seq.raw"; new_file "$f"
      if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] adb exec-out screencap > $f" >&2
      else "$ADB" -s "$SERIAL" exec-out screencap > "$f" </dev/null; fi
      if [ "$seq" = c ]; then shots_c="${shots_c:+$shots_c,}$f"; else shots_k="${shots_k:+$shots_k,}$f"; fi
    done
    sput system refresh_rate_mode "$c_mode" >/dev/null
    if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] $PF_BIN iq-judge --ctrl $shots_c --knob $shots_k" >&2
    else "$PF_BIN" iq-judge --ctrl "$shots_c" --knob "$shots_k" > "$G/iq.json" 2> "$G/iq.stderr"; iq_rc=$?; fi
    log "  iq-judge 退出码 ${iq_rc:-?} (0=通过 1=不通过 3=判不了)"
  fi

  # 停 watch, 拉回
  dsu "touch $WATCH_STOP"
  if [ "$DRY_RUN" != 1 ]; then
    for _ in $(seq 1 15); do dsu "tail -1 $WATCH_OUT" | grep -q '^# end' && break; sleep 1; done
    "$ADB" -s "$SERIAL" pull "$WATCH_OUT" "$G/watch.txt" </dev/null >/dev/null || log "  拉不回 watch 日志"
  fi
  WATCH_STOP=""

  # 8. 判定
  if [ "$DRY_RUN" != 1 ]; then
    # 对照是 auto 档时不预设面板 Hz (--ctrl-hz 0), 只记录它实际跑到多少
    $O1_PY analyze --eval-dir "$G/eval" --watch "$G/watch.txt" --cap "$cap" \
      --ctrl-hz "$([ "$c_spec" = auto ] && echo 0 || echo "$c_hz")" --knob-hz "$k_hz" \
      --report "$G/report.json" --iq-json "$G/iq.json" --iq-rc "${iq_rc}" --out "$G/result.json" \
      | sed "s/^/  组 $name 结论: /" >&2
  fi
  return 0
}

gi=0
for spec in "${GLIST[@]}"; do
  gi=$((gi + 1))
  run_group "$spec" "$gi" || log "组 $spec 没跑完 (原因见上), 继续下一组"
done
log "全部结束, 证据: $OUT"
