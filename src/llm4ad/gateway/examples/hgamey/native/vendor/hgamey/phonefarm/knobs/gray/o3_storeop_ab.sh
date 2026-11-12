#!/bin/bash
# o3_storeop_ab.sh — 原神去掉 3 处多余写回 (storeOp STORE→DONT_CARE) 的功耗/帧时 A/B
#
# 缺省是 dry-run (DRY_RUN=1): 只做离线校验, 一条 adb 都不发:
#   1. phonefarm eval --validate-only 校验请求 (validate_v2 + 改写解析 + 负载脚本 + 层 .so 带 storeop 开关)
#   2. o3_storeop_budget.py 算预期效应与可检出性 (推断)
#   3. 打印上机时要执行的确切命令
# DRY_RUN=0 才上机 (红魔 NX809J, 有 root):
#   上机前只读检查 (设备在线、root、原神版本与 frame-audit 证据一致、电量 40–99% 且没在充满)
#   → 记下要动的设置原值 (停充节点、debug.vulkan.layers、debug.knobs.*)
#   → phonefarm eval (两臂都挂层, 只差 debug.knobs.storeop; 每臂重启原神 → 点门进大世界 →
#     同位置截两张图 → 转视角负载 30 s, ftrace 认目标进程帧 + ≥5 Hz 电池功耗 + GPU 频率/带宽投票;
#     臂序随机, 停充放电态, 结束恢复充电)
#   → 回读设置, 不等于原值的写回原值 (还原, 不删任何东西)
#   → phonefarm iq-judge 用两臂截图抽查画面
#   → 打印判定要点
#
# 用法:
#   bash knobs/gray/o3_storeop_ab.sh                     # dry-run
#   DRY_RUN=0 bash knobs/gray/o3_storeop_ab.sh           # 上机, 60 帧
#   DRY_RUN=0 FPS=30 INGAME_FPS_CONFIRMED=30 bash ...    # 可选: 30 帧 (游戏内帧率要先人工改成 30, 测完人工改回)
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WT="$(cd "$HERE/../.." && pwd)"                                   # 本 worktree (设备端脚本从这里推)
PF="${PF:-$WT/src/target/release/phonefarm}"
PF_MAIN="${PF_MAIN:-/Users/yao/Desktop/code/agent/hgamey/phonefarm}" # 有构建好的层 .so 的检出, eval 在这里跑
SERIAL="${SERIAL:-91253241019A}"
PKG=com.miHoYo.Yuanshen
FPS="${FPS:-60}"
DRY_RUN="${DRY_RUN:-1}"
REQ="$HERE/requests/o3_storeop3_genshin_${FPS}fps.json"
OUT_BASE="${OUT_BASE:-/Users/yao/Desktop/code/agent/hgamey/_tmp/opt/O3}"
OUT="${OUT:-$OUT_BASE/run_${FPS}fps_$(date +%Y%m%d_%H%M%S)}"
# frame-audit 证据 (10_frameaudit / 11_storeop_check) 抓的版本; 版本变了 pass 特征码可能变
GENSHIN_VER="${GENSHIN_VER:-7.1.0_48052158_48145775}"
ADB="${ADB:-adb}"
# 原神截图 2688x1216 的 UI 区域 (与 frameaudit::ui_rects 同比例): 小地图 / 顶部 / 队伍 / 技能+UID / 聊天+摇杆
IQ_EXCLUDE=(--exclude 0,0,537,389 --exclude 0,0,2688,121 --exclude 2204,121,2688,668 --exclude 1666,668,2688,1216 --exclude 0,851,1666,1216)

die() { echo "停止: $*" >&2; exit 1; }
[ -x "$PF" ] || die "没有 $PF, 先构建: cd $WT/src && cargo build --release"
[ -f "$REQ" ] || die "没有请求文件 $REQ (FPS 只认 60 / 30)"
[ -f "$PF_MAIN/knobs/gray/build/out/libVkLayer_refknobs.so" ] || die "$PF_MAIN 下没有构建好的层 .so"

echo "== 1. 离线校验请求 =="
( cd "$PF_MAIN" && "$PF" eval --request "$REQ" --validate-only ) || die "请求没过校验"

echo "== 2. 预期效应与可检出性 (推断) =="
python3 -I "$HERE/o3_storeop_budget.py" --repo "$WT" --fps "$FPS" --reps 200 || die "预算脚本失败"

CMD="cd $PF_MAIN && $PF eval --request $REQ --serial $SERIAL --out $OUT/eval --json > $OUT/report.json"
echo "== 3. 上机命令 =="
echo "  DRY_RUN=0 bash $HERE/o3_storeop_ab.sh"
echo "  其中 eval 一步是:"
echo "  $CMD"
if [ "$DRY_RUN" != "0" ]; then
  echo "dry-run 结束: 没有碰设备。"
  exit 0
fi

# ───────────────────────── 以下上机 ─────────────────────────
[ -e "$OUT" ] && die "$OUT 已存在, 不覆盖"
mkdir -p "$OUT/eval" || die "建不了 $OUT"
ash() { "$ADB" -s "$SERIAL" shell "$@" </dev/null | tr -d '\r'; }

echo "== 4. 上机前只读检查 =="
"$ADB" -s "$SERIAL" get-state 2>/dev/null | grep -q device || die "设备 $SERIAL 不在线"
ash "su -c id" | grep -q "uid=0" || die "没有 root"
VER=$(ash "dumpsys package $PKG" | sed -n 's/.*versionName=\([^ ]*\).*/\1/p' | head -1)
echo "  原神版本: $VER (证据版本 $GENSHIN_VER)"
if [ "$VER" != "$GENSHIN_VER" ] && [ "${ALLOW_VERSION_DRIFT:-0}" != "1" ]; then
  die "原神版本变了, 3 个 pass 特征码可能不再对应。先重跑 frame-audit capture 确认特征码; 确认后用 ALLOW_VERSION_DRIFT=1 再跑 (require_hit 仍会兜底)"
fi
CAP=$(ash "cat /sys/class/power_supply/battery/capacity")
ST=$(ash "cat /sys/class/power_supply/battery/status")
echo "  电量 ${CAP}% 状态 $ST"
case "$CAP" in ''|*[!0-9]*) die "读不到电量" ;; esac
[ "$CAP" -lt 100 ] || die "电量 100%: 先放到 100% 以下再跑 (满电停充会在电池轨/旁路轨之间跳, 功耗作废)"
[ "$CAP" -ge 40 ] || die "电量低于 40%: 20 臂约 1 小时放电, 先充电"
if [ "$CAP" -le 86 ]; then
  echo "  注意: 电量 ≤86%, 一小时放电会跨过 80% —— 厂商充电上限逻辑在 80% 附近会把停充节点写回 1 (10-21 实测), 那几对会因非放电态被剔除"
fi
if [ "$FPS" = "30" ] && [ "${INGAME_FPS_CONFIRMED:-}" != "30" ]; then
  die "30 帧组要先在游戏里把帧率改成 30 并设 INGAME_FPS_CONFIRMED=30 (测完再人工改回 60)"
fi

echo "== 5. 记下要动的设置原值 =="
CE_NODE=/sys/class/qcom-battery/charging_enabled
CE0=$(ash "su -c 'cat $CE_NODE'")
LAYERS0=$(ash "getprop debug.vulkan.layers")
ash "getprop" | grep -E '^\[debug\.knobs\.' > "$OUT/props_before.txt"
{ echo "charging_enabled=$CE0"; echo "debug.vulkan.layers=$LAYERS0"; } > "$OUT/settings_before.txt"
cat "$OUT/settings_before.txt" "$OUT/props_before.txt" | sed 's/^/  /'
[ -z "$LAYERS0" ] || die "debug.vulkan.layers 现在不空 ($LAYERS0): 别的实验可能在挂层, 不跑"

echo "== 6. A/B (约 1 小时) =="
echo "$CMD" > "$OUT/command.txt"
( cd "$PF_MAIN" && "$PF" eval --request "$REQ" --serial "$SERIAL" --out "$OUT/eval" --json ) > "$OUT/report.json" 2> "$OUT/eval.stderr"
RC=$?
echo "  eval 退出码 $RC"

echo "== 7. 还原核对 =="
CE1=$(ash "su -c 'cat $CE_NODE'")
if [ "$CE1" != "$CE0" ]; then
  echo "  停充节点 $CE1 ≠ 原值 $CE0, 写回原值"
  ash "su -c 'echo $CE0 > $CE_NODE'"
  echo "  回读: $(ash "su -c 'cat $CE_NODE'")"
fi
LAYERS1=$(ash "getprop debug.vulkan.layers")
[ "$LAYERS1" = "$LAYERS0" ] || { echo "  debug.vulkan.layers=[$LAYERS1] ≠ 原值, 写回"; ash "su -c 'setprop debug.vulkan.layers \"$LAYERS0\"'"; }
ash "getprop" | grep -E '^\[debug\.knobs\.' > "$OUT/props_after.txt"
# 每个 debug.knobs.* 写回运行前的值 (运行前没有的属性 Android 删不掉, 写成空串 = 层当作关)
while IFS= read -r line; do
  k=$(echo "$line" | sed 's/^\[\([^]]*\)\].*/\1/')
  now=$(echo "$line" | sed 's/^[^:]*: \[\(.*\)\]$/\1/')
  was=$(grep -F "[$k]:" "$OUT/props_before.txt" | sed 's/^[^:]*: \[\(.*\)\]$/\1/')
  if [ "$now" != "$was" ]; then
    echo "  $k: [$now] → 原值 [$was]"
    ash "su -c 'setprop $k \"$was\"'"
  fi
done < "$OUT/props_after.txt"
ash "getprop" | grep -E '^\[debug\.knobs\.' > "$OUT/props_restored.txt"
diff -q "$OUT/props_before.txt" "$OUT/props_restored.txt" >/dev/null && echo "  debug.knobs.* 已与运行前一致" \
  || echo "  debug.knobs.* 与运行前仍有差别 (见 props_before / props_restored; 新出现的属性已写成空串)"

echo "== 8. 画面抽查 (iq-judge) =="
CTRL=$(ls "$OUT"/eval/shot_ctrl*.png 2>/dev/null | head -6 | paste -sd, -)
KNOB=$(ls "$OUT"/eval/shot_knob*.png 2>/dev/null | grep -v _repeat | head -6 | paste -sd, -)
if [ -n "$CTRL" ] && [ -n "$KNOB" ]; then
  "$PF" iq-judge --ctrl "$CTRL" --knob "$KNOB" "${IQ_EXCLUDE[@]}" > "$OUT/iq_judge.json"
  echo "  iq-judge 退出码 $? (0=画面不变 1=变了 3=判不了), 结果 $OUT/iq_judge.json"
else
  echo "  没有截图 (评测中途停了?), 画面没法抽查"
fi

echo "== 9. 要点 =="
python3 -I - "$OUT/report.json" <<'PY'
import json, sys
try:
    r = json.load(open(sys.argv[1]))
except Exception as e:
    print(f"  报告读不了: {e}"); sys.exit(0)
v, c = r.get("verdict", {}), r.get("conditions_observed", {})
print(f"  status={r.get('status')}  reason={v.get('reason')}")
print(f"  改写命中 hits={c.get('hits')}  摘层干净={c.get('restored')}  充电恢复={c.get('charge_restored')}  进场方式={c.get('world_entry')}")
print(f"  臂序={c.get('arm_order')}  锁帧={v.get('fps_locked_cap')}")
for m, e in (v.get("per_metric") or {}).items():
    print(f"  {m}: A={e.get('a_mean')} B={e.get('b_mean')} Δ={e.get('diff_pct')}% p={e.get('p')} CI={e.get('ci95')} {e.get('status')}")
for m, e in ((v.get("aux") or {}).get("metrics") or {}).items():
    if e:
        print(f"  [机制] {m}: A={e.get('a_mean')} B={e.get('b_mean')} Δ={e.get('diff_pct')}% p={e.get('p')} CI={e.get('ci95')}")
pp = v.get("power_pairs")
if pp:
    print(f"  功耗配对: {json.dumps(pp, ensure_ascii=False)[:300]}")
ic = c.get("image_check") or {}
print(f"  画面: 噪声底={ic.get('noise_floor_db')} dB  改写对对照={ic.get('psnr_knob_vs_ctrl_db')}  门槛={ic.get('gate')}")
PY
echo "证据: $OUT"
[ "$FPS" = "30" ] && echo "提醒: 把游戏内帧率人工改回 60"
exit $RC
