#!/bin/bash
# probe_refresh.sh — 红魔 NX809J 上「怎么改屏幕刷新率、改了是不是真生效」的探测。只读 + 可还原。
#
# 做什么:
#   1. 只读收集: dumpsys display / SurfaceFlinger 全文、支持的显示模式、当前活动模式,
#      名字里带 refresh/fps/frame/game 的 settings 键与系统属性、cmd display / cmd game 的帮助与现状。
#   2. 逐个方法试写, 每次都看**实际活动模式** (DisplayManager 记录的 SF 活动模式、
#      SF --latency 的刷新周期、SF 自己的 refresh-rate 行三路), 不看设置值本身:
#        M1 厂商键 settings system refresh_rate_mode (取值表 system:all_refresh_rate)
#        M2 AOSP settings system peak_refresh_rate / min_refresh_rate
#           —— 只在原值不是 null 时试; 原值 null 时还原只能 delete, 按铁律跳过
#        M3 cmd display set-user-preferred-display-mode —— 不试写: 本机没有用户偏好时, 还原要
#           clear-user-preferred-display-mode (等于删键)。只记帮助与现值
#        M4 service call SurfaceFlinger 1035 i32 <SF 模式 id> (调试后门, 内存态不落盘);
#           撤销 = 1035 i32 -1, 再把厂商键拨一下让 DisplayManager 重新下发策略, 读数核对回到原样
#        M5 cmd game (GameManager 帧率干预) —— 缺省只读; TRY_GAMEMODE=1 才试写,
#           还原用 cmd game reset (原状态是「没有干预」, reset 回到原状态)
#   3. WITH_GAME=1 时, 原神在前台 (大世界) 再把 M1/M4 测一遍, 并读「auto 档在游戏里实际跑多少 Hz」
#      以及触屏时会不会被抬频 (touch boost)。游戏要事先由人或 run_refresh_ab.sh 带进大世界;
#      本脚本不启动游戏、不登录。
#   4. 结尾把所有写过的东西写回记录的原值, 并核对实际活动模式回到原样。
#
# 产出 (新目录 $OUT):
#   raw/*.txt            原始 dump
#   modes.json           支持的模式 (SF id 与 Hz)
#   probe.tsv            逐项: 场景 方法 写入值 设置回读 面板Hz 渲染Hz SF周期Hz 是否生效
#   refresh_modes.json   给 run_refresh_ab.sh 用: 厂商键各档在「游戏前台」时实际对应的面板 Hz
#   restore_check.txt    还原核对
#
# 用法:
#   DRY_RUN=1 bash probe_refresh.sh                  # 不碰手机, 只打印会执行的命令
#   bash probe_refresh.sh                            # 桌面/当前前台下探测
#   WITH_GAME=1 bash probe_refresh.sh                # 原神已在大世界前台时探测 (推荐两种都跑)
# 环境变量: SERIAL OUT WITH_GAME(0) TRY_1035(1) TRY_GAMEMODE(0) SETTLE_S(3)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/refresh_lib.sh"

WITH_GAME="${WITH_GAME:-0}"
TRY_1035="${TRY_1035:-1}"
TRY_GAMEMODE="${TRY_GAMEMODE:-0}"
SETTLE_S="${SETTLE_S:-3}"
OUT="${OUT:-/Users/yao/Desktop/code/agent/hgamey/_tmp/opt/O1/runs/probe_${O1_TS}$([ "$WITH_GAME" = 1 ] && echo _game)}"
[ -e "$OUT" ] && die "$OUT 已存在, 不覆盖"
mkdir -p "$OUT/raw"
TSV="$OUT/probe.tsv"
printf 'scene\tmethod\twrite\tsetting_readback\tpanel_hz\trender_hz\tsflat_hz\tsf_hz\teffective\n' > "$TSV"
SCENE=$([ "$WITH_GAME" = 1 ] && echo game || echo idle)

preflight_device
push_dev_tool

# ── 0. 原值 (先记下来, 再动任何东西) ──
dev info > "$OUT/raw/info.txt"
O_RRM=$(sget system refresh_rate_mode)
O_PEAK=$(sget system peak_refresh_rate)
O_MIN=$(sget system min_refresh_rate)
O_ALL=$(sget system all_refresh_rate)
if [ "$DRY_RUN" = 1 ]; then  # dry-run 读不到原值: 填本机历史值让流程走完
  O_RRM=${O_RRM:-0}; O_PEAK=${O_PEAK:-null}; O_MIN=${O_MIN:-null}; O_ALL=${O_ALL:-auto,60,90,120,144}
fi
{
  echo "refresh_rate_mode=$O_RRM"
  echo "peak_refresh_rate=$O_PEAK"
  echo "min_refresh_rate=$O_MIN"
  echo "all_refresh_rate=$O_ALL"
} > "$OUT/original.txt"
A0=$(active_json)
echo "active_before=$A0" >> "$OUT/original.txt"
log "原值: refresh_rate_mode=$O_RRM peak=$O_PEAK min=$O_MIN all=$O_ALL 活动模式=$A0"
if [ "$WITH_GAME" = 1 ] && [ "$DRY_RUN" != 1 ]; then
  dsh "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'" | grep -q "$PKG" \
    || die "WITH_GAME=1 但 $PKG 不在前台 (先把游戏带进大世界; 本脚本不启动游戏、不登录)"
fi

# 写过什么就登记什么, 退出时 (含中断) 一律写回原值
TOUCHED_RRM=0; TOUCHED_AOSP=0; TOUCHED_1035=0; TOUCHED_GAME=0
restore_all() {
  local rc=$?
  trap '' INT TERM   # 还原期间第二次 Ctrl-C 不许打断 (R2 审查 #16)
  trap - EXIT
  log "还原 (Ctrl-C 已屏蔽到还原结束)..."
  {
    if [ "$TOUCHED_1035" = 1 ]; then
      echo "sf1035 撤销: $(dev sf1035 -1)"
    fi
    if [ "$TOUCHED_AOSP" = 1 ]; then
      sput system peak_refresh_rate "$O_PEAK"
      sput system min_refresh_rate "$O_MIN"
    fi
    if [ "$TOUCHED_GAME" = 1 ]; then
      echo "cmd game reset: $(dsu "cmd game reset $PKG" 2>&1)"
    fi
    # 厂商键: 先拨到另一档再拨回原值, 让 DisplayManager 重新下发策略 (清掉 1035 留下的 SF 策略)
    if [ "$TOUCHED_RRM" = 1 ] || [ "$TOUCHED_1035" = 1 ]; then
      if [ -n "$O_RRM" ] && [ "$O_RRM" != null ]; then
        alt=$([ "$O_RRM" = 1 ] && echo 0 || echo 1)
        sput system refresh_rate_mode "$alt"; sleep "$SETTLE_S"
        sput system refresh_rate_mode "$O_RRM"; sleep "$SETTLE_S"
      fi
    fi
    echo "readback refresh_rate_mode=$(sget system refresh_rate_mode) (原 $O_RRM)"
    echo "readback peak_refresh_rate=$(sget system peak_refresh_rate) (原 $O_PEAK)"
    echo "readback min_refresh_rate=$(sget system min_refresh_rate) (原 $O_MIN)"
    A1=$(active_json)
    echo "active_after=$A1"
    echo "active_before=$A0"
    p0=$(json_get "$A0" panel_hz); p1=$(json_get "$A1" panel_hz)
    if [ "$DRY_RUN" = 1 ]; then echo "RESTORE dry-run"
    elif [ -n "$p0" ] && [ -n "$p1" ] && hz_near "$p1" "$p0"; then echo "RESTORE_OK 面板 ${p1} Hz = 探测前 ${p0} Hz"
    else echo "RESTORE_CHECK 面板 ${p1} Hz, 探测前 ${p0} Hz —— 不一致 (auto 档下可能是正常的自适应; 若之前用过 1035, 重启可清掉它的内存态)"; fi
  } > "$OUT/restore_check.txt" 2>&1
  cat "$OUT/restore_check.txt" >&2
  exit $rc
}
trap restore_all EXIT
trap 'exit 130' INT TERM

# ── 1. 只读收集 ──
log "只读收集..."
dev dump_display > "$OUT/raw/dumpsys_display.txt"
dev dump_sf > "$OUT/raw/dumpsys_surfaceflinger.txt"
dev sf_list > "$OUT/raw/sf_list.txt"
dev settings_scan > "$OUT/raw/settings_scan.txt"
dev props_scan > "$OUT/raw/props_scan.txt"
dev services_scan > "$OUT/raw/services_scan.txt"
dev cmd_help > "$OUT/raw/cmd_help.txt"
dev game_info "$PKG" > "$OUT/raw/game_info.txt"
dev brightness > "$OUT/raw/brightness.txt"
$O1_PY modes "$OUT/raw/dumpsys_display.txt" "$OUT/raw/dumpsys_surfaceflinger.txt" > "$OUT/modes.json"
log "支持的模式: $(cat "$OUT/modes.json")"

row() { # <method> <write> <setting_readback> <want_hz 或 空>
  local a p r l s eff
  a=$(active_json)
  p=$(json_get "$a" panel_hz); r=$(json_get "$a" render_hz); l=$(json_get "$a" sflat_hz); s=$(json_get "$a" sf_hz)
  eff="?"
  if [ -n "${4:-}" ] && [ -n "$p" ]; then hz_near "$p" "$4" && eff=yes || eff=no; fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$SCENE" "$1" "$2" "$3" "$p" "$r" "$l" "$s" "$eff" >> "$TSV"
  log "  $1 写 $2 → 回读 $3, 面板 ${p:-?} Hz 渲染 ${r:-?} SF周期 ${l:-?} SF ${s:-?} (想要 ${4:-?}) 生效=$eff"
}

row baseline - "$O_RRM" ""

# ── M1 厂商键 refresh_rate_mode ──
if [ -n "$O_RRM" ] && [ "$O_RRM" != null ] && [ -n "$O_ALL" ] && [ "$O_ALL" != null ]; then
  IFS=',' read -r -a LABELS <<< "$O_ALL"
  for i in "${!LABELS[@]}"; do
    TOUCHED_RRM=1
    sput system refresh_rate_mode "$i" >/dev/null
    sleep "$SETTLE_S"
    want="${LABELS[$i]}"; case "$want" in *[!0-9]*) want="";; esac   # auto 档没有固定 Hz
    row "M1.refresh_rate_mode" "$i(${LABELS[$i]})" "$(sget system refresh_rate_mode)" "$want"
    # 游戏前台时加一次触屏, 看会不会被抬频 (原神里右半屏横滑只转镜头)
    if [ "$WITH_GAME" = 1 ]; then
      dsh "input swipe 900 400 2000 400 1500" >/dev/null &
      sleep 1
      row "M1.refresh_rate_mode+touch" "$i(${LABELS[$i]})" "$(sget system refresh_rate_mode)" "$want"
      wait
    fi
  done
  sput system refresh_rate_mode "$O_RRM" >/dev/null; sleep "$SETTLE_S"
  row "M1.restore" "$O_RRM" "$(sget system refresh_rate_mode)" ""
else
  log "M1 跳过: refresh_rate_mode 或 all_refresh_rate 原值为空"
  printf '%s\tM1.refresh_rate_mode\tskip\t原值为空\t\t\t\t\t-\n' "$SCENE" >> "$TSV"
fi

# ── M2 AOSP peak/min_refresh_rate ──
if [ -n "$O_PEAK" ] && [ "$O_PEAK" != null ] && [ -n "$O_MIN" ] && [ "$O_MIN" != null ]; then
  TOUCHED_AOSP=1
  for hz in 60 120; do
    sput system min_refresh_rate "$hz" >/dev/null; sput system peak_refresh_rate "$hz" >/dev/null
    sleep "$SETTLE_S"
    row "M2.peak+min_refresh_rate" "$hz" "$(sget system peak_refresh_rate)/$(sget system min_refresh_rate)" "$hz"
  done
  sput system peak_refresh_rate "$O_PEAK" >/dev/null; sput system min_refresh_rate "$O_MIN" >/dev/null
else
  log "M2 跳过: peak/min_refresh_rate 原值是 null —— 写进去以后只能 delete 才回得去, 按铁律不试"
  printf '%s\tM2.peak+min_refresh_rate\tskip\t原值 null (还原需 delete)\t\t\t\t\t-\n' "$SCENE" >> "$TSV"
fi

# ── M3 cmd display 用户偏好模式 ──
UPREF=$(dsu "cmd display get-user-preferred-display-mode" 2>&1 | head -3 | tr '\n' ' ')
echo "user_preferred_display_mode=$UPREF" >> "$OUT/original.txt"
printf '%s\tM3.cmd_display\tskip\t现值: %s (没有偏好时还原要 clear = 删键, 不试; 帮助见 raw/cmd_help.txt)\t\t\t\t\t-\n' "$SCENE" "$UPREF" >> "$TSV"

# ── M4 SurfaceFlinger 1035 强制模式 ──
if [ "$TRY_1035" = 1 ]; then
  IDS=$(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(" ".join("%s:%s" % (m["id"], m["hz"]) for m in d["sf_modes"]))' "$OUT/modes.json" 2>/dev/null)
  [ "$DRY_RUN" = 1 ] && IDS="0:120 1:60"
  if [ -z "$IDS" ]; then
    log "M4 跳过: 没解析出 SF 模式 id (原文在 raw/)"
    printf '%s\tM4.sf1035\tskip\t没解析出 SF 模式 id\t\t\t\t\t-\n' "$SCENE" >> "$TSV"
  else
    for pair in $IDS; do
      id=${pair%%:*}; hz=${pair##*:}
      TOUCHED_1035=1
      r=$(dev sf1035 "$id")
      sleep "$SETTLE_S"
      row "M4.sf1035" "id=$id(${hz}Hz)" "$(echo "$r" | sed 's/.*reply=//' | tr '\t' ' ')" "$hz"
    done
    dev sf1035 -1 >/dev/null; sleep "$SETTLE_S"
    row "M4.sf1035(-1 撤销)" "-1" "-" ""
  fi
fi

# ── M5 GameManager ──
if [ "$TRY_GAMEMODE" = 1 ]; then
  log "M5: 试 cmd game 帧率干预 (还原 = cmd game reset, 原状态见 raw/game_info.txt)"
  TOUCHED_GAME=1
  for fps in 30 60; do
    out=$(dsu "cmd game set --mode 2 --fps $fps $PKG" 2>&1 | tr '\n' ' ')
    sleep "$SETTLE_S"
    row "M5.cmd_game_set_fps" "$fps" "$out" ""
    [ "$WITH_GAME" = 1 ] && log "    上屏: $(present_fps "$OUT/raw" "$fps")"
  done
  dsu "cmd game reset $PKG" >/dev/null; TOUCHED_GAME=0
else
  printf '%s\tM5.cmd_game\tread-only\t见 raw/game_info.txt (TRY_GAMEMODE=1 才试写)\t\t\t\t\t-\n' "$SCENE" >> "$TSV"
fi

# ── 汇总给 run_refresh_ab.sh 用 ──
python3 -I - "$TSV" "$OUT/modes.json" "$O_RRM" "$O_ALL" "$SCENE" > "$OUT/refresh_modes.json" <<'PY'
import csv, json, sys
tsv, modes, orig, allr, scene = sys.argv[1:6]
rows = list(csv.DictReader(open(tsv), delimiter="\t"))
hz_to_mode, mode_hz = {}, {}
for r in rows:
    if r["method"] == "M1.refresh_rate_mode" and r["panel_hz"]:
        idx = r["write"].split("(")[0]
        mode_hz[idx] = float(r["panel_hz"])
        if r["effective"] == "yes":
            hz_to_mode.setdefault(str(int(round(float(r["panel_hz"])))), idx)
touch = {r["write"].split("(")[0]: r["panel_hz"] for r in rows if r["method"] == "M1.refresh_rate_mode+touch"}
m = json.load(open(modes))
out = {
    "scene": scene,
    "original_mode": orig,
    "all_refresh_rate": allr,
    "mode_panel_hz": mode_hz,          # 每档写进去后实测面板 Hz (auto 档 = 当时自适应到的值)
    "mode_panel_hz_touch": touch,      # 同上, 触屏时
    "hz_to_mode": hz_to_mode,          # 只收「实测面板 Hz = 档名」的档
    "max_hz": max([float(h) for h in hz_to_mode] or [0]) or None,
    "hz_supported": m.get("hz_supported"),
    "sf1035": [r for r in rows if r["method"].startswith("M4")],
}
print(json.dumps(out, ensure_ascii=False, indent=1))
PY
log "完成: $OUT  (probe.tsv / refresh_modes.json)"
column -t -s $'\t' "$TSV" >&2 || cat "$TSV" >&2
