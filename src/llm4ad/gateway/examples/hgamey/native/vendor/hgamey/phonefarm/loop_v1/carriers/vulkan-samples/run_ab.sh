#!/bin/bash
# run_ab.sh <sample> <config_A> <config_B> [rounds] [outdir] [frames] [capdur]
#
# 对同一个 Vulkan-Samples 样例的两档开关跑交错 NvN, 交给 phonefarm analyze 出
# 效应量 / 精确置换检验 p 值 / 置换反演 95% CI。
#
# 交错 (A,B,A,B,...) 而不是先跑完 A 再跑 B: 温度、内存压力、后台调度都会沿时间漂移,
# 分块跑会把漂移整块算进臂间差异。交错让漂移对两臂同等作用。
# 无效轮 (热事件 / 非干净退出 / 提交线程不唯一) 就地重试至多 2 次, 证据全部保留。
#
# 退出码: 0=跑完并出报告  2=硬失败
set -uo pipefail

SAMPLE="${1:?用法: run_ab.sh <sample> <config_A> <config_B> [rounds] [outdir] [frames] [capdur]}"
CFG_A="${2:?缺 A 臂 config 索引}"
CFG_B="${3:?缺 B 臂 config 索引}"
ROUNDS="${4:-5}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"          # loop_v1/
OUT="${5:-$ROOT/runs_vks/${SAMPLE}_c${CFG_A}_vs_c${CFG_B}}"
# 缺省 30000 帧不是随手拍的: 本机真实渲染约 340fps, 而每轮要跨过 README §3(b) 的
# 空跑段(约 20000 帧) 再留出 settle + 12s 采集窗。给少了应用会在采集开始前就跑完自退,
# 采到的全是空窗 —— 这正是 2026-09-23 头几轮全判无效的原因。
FRAMES="${6:-30000}"
CAPDUR="${7:-12}"

SERIAL="${VKS_SERIAL:-91253241019A}"
TOOLS="$ROOT/tools"
. "$TOOLS/pf_bin.sh"
ashell() { adb -s "$SERIAL" shell "$@" </dev/null; }

mkdir -p "$OUT"
echo "══ vulkan-samples A/B: $SAMPLE  config $CFG_A vs $CFG_B  ${ROUNDS}v${ROUNDS} → $OUT"

# ── 前置: 设备脚本 + 唤醒 ──
adb -s "$SERIAL" push "$TOOLS/device_snapshot.sh" "$TOOLS/ftrace_capture.sh" /data/local/tmp/ >/dev/null
ashell "chmod 755 /data/local/tmp/device_snapshot.sh /data/local/tmp/ftrace_capture.sh"
ashell "input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard" >/dev/null 2>&1 || true

# ── 风扇状态另存, 退出必还原 (fan 不在 38 行快照内, 与 refbench/run_m0.sh 同) ──
FAN_EN=$(ashell "su -c 'cat /sys/kernel/fan/fan_enable'" 2>/dev/null | tr -d '\r' || true)
FAN_LV=$(ashell "su -c 'cat /sys/kernel/fan/fan_speed_level'" 2>/dev/null | tr -d '\r' || true)
restore_fan() {
  ashell "su -c 'echo ${FAN_EN:-0} > /sys/kernel/fan/fan_enable; echo ${FAN_LV:-4} > /sys/kernel/fan/fan_speed_level'" >/dev/null 2>&1 || true
}
trap restore_fan EXIT
# 风扇已经开着就原样不动(用户可能是手动开的, 挡位是他选的); 只有关着时才由我们打开。
# 要的是"整批对照期间风扇状态恒定", 不是某个特定挡位 —— 风扇自身耗电会进功耗读数,
# 两臂只要同状态就不会污染臂间差异。每轮的实际状态由 run_vks.sh 存进 fan.json。
if [ "${FAN_EN:-0}" = "1" ]; then
  echo "  风扇已开 (enable=$FAN_EN level=$FAN_LV), 保持原状"
else
  echo "  风扇原为关闭, 本批对照期间打开 (level 5), 结束还原"
  ashell "su -c 'echo 1 > /sys/kernel/fan/fan_enable; echo 5 > /sys/kernel/fan/fan_speed_level'" >/dev/null 2>&1 || true
fi

# ── 面板刷新率钉死, 退出必还原 ──
# 开着 vsync 时应用帧率就是面板刷新率, 而本机面板是自适应刷新的(支持 60/90/120/144)。
# 2026-09-23 实测同一批 10 轮里应用帧率出现过 60 / 99.5 / 120 三档 —— 每轮的帧预算
# 都不一样, 逐帧指标(frame_p50/p95、gpu_active_mean)根本没有可比性。
# 所以整批对照期间把它钉死; 原值是 null(自动) 时还原用 settings delete。
REFRESH="${VKS_REFRESH_HZ:-120}"
RR_MIN=$(ashell "settings get system min_refresh_rate" 2>/dev/null | tr -d '\r' || true)
RR_PEAK=$(ashell "settings get system peak_refresh_rate" 2>/dev/null | tr -d '\r' || true)
restore_refresh() {
  for kv in "min_refresh_rate:$RR_MIN" "peak_refresh_rate:$RR_PEAK"; do
    k="${kv%%:*}"; v="${kv#*:}"
    if [ -z "$v" ] || [ "$v" = "null" ]; then
      ashell "settings delete system $k" >/dev/null 2>&1 || true
    else
      ashell "settings put system $k $v" >/dev/null 2>&1 || true
    fi
  done
}
restore_all() { restore_fan; restore_refresh; }
trap restore_all EXIT
echo "  面板刷新率: 原值 min=$RR_MIN peak=$RR_PEAK, 本批钉在 ${REFRESH}Hz, 结束还原"
ashell "settings put system min_refresh_rate $REFRESH; settings put system peak_refresh_rate $REFRESH" >/dev/null 2>&1 || true

one() {   # one <label> <config>
  local label="$1" cfg="$2" try rc
  for try in 1 2 3; do
    rm -rf "$OUT/$label"
    # 状态要当场接住。不能写成 `if run_vks.sh; then ...; fi` 再取 $? ——
    # 条件为假且没有 else 时, `fi` 之后的 $? 按 POSIX 是 0, 硬失败会被误当成无效轮反复重试。
    rc=0
    bash "$HERE/run_vks.sh" "$label" "$OUT/$label" "$SAMPLE" "$cfg" "$FRAMES" "$CAPDUR" || rc=$?
    [ $rc -eq 0 ] && return 0
    [ $rc -eq 2 ] && { echo "  [$label] 硬失败"; return 2; }
    mv "$OUT/$label" "$OUT/${label}_invalid$try" 2>/dev/null || true
    echo "  [$label] 第 $try 次无效, 还剩 $((3-try)) 次重试"
  done
  echo "  [$label] 三次都无效, 放弃该轮"
  return 3
}

for i in $(seq 1 "$ROUNDS"); do
  one "a$i" "$CFG_A" || [ $? -eq 3 ] || exit 2
  one "b$i" "$CFG_B" || [ $? -eq 3 ] || exit 2
done

echo "══ 汇总"
"$PF" analyze "$OUT/a*/summary.json" "$OUT/b*/summary.json" > "$OUT/analyze.json"
"$PF" vks-report --root "$OUT" --sample "$SAMPLE" \
        --config-a "$CFG_A" --config-b "$CFG_B" | tee "$OUT/report.txt"
