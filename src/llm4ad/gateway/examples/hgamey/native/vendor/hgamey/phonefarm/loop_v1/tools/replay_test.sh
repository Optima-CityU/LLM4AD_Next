#!/bin/bash
# replay_test.sh <runs根目录> — 判据 5: 整条链路离线回放, 无设备, 报告字节一致
#
# 做法: 把归档的 trace.txt 重新喂给解析器与分析器, 与首次产出的 summary.json /
# report.json 逐字节比对。任何一处不等即失败。
#
# 这条自检能成立的前提是所有分析代码都是纯函数: 不读时钟、不读设备、不用随机数。
# 置换检验全枚举而非抽样、分位数用固定插值、CI 用固定网格扫描, 都是为了这一条。
set -uo pipefail

ROOT="${1:?用法: replay_test.sh <runs根目录>}"
TOOLS="$(cd "$(dirname "$0")" && pwd)"
. "$TOOLS/pf_bin.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

fail=0
n=0

echo "── 离线回放自检 (不连设备) ──"

# 1) 逐轮重新解析 trace.txt, 比对 summary.json
for d in "$ROOT"/*/; do
  [ -f "$d/trace.txt" ] || continue
  [ -f "$d/summary.json" ] || continue
  n=$((n+1))
  label=$(basename "$d")
  "$PF" parse-trace "$d/trace.txt" > "$TMP/$label.json" 2>"$TMP/$label.err"
  if cmp -s "$TMP/$label.json" "$d/summary.json"; then
    echo "  ✓ $label  summary 字节一致"
  else
    echo "  ✗ $label  summary 不一致:"
    diff <(head -40 "$d/summary.json") <(head -40 "$TMP/$label.json") | head -10 | sed 's/^/      /'
    fail=1
  fi
done

# 2) 重新解析后再跑归因, 比对 attribution.json (若已归档)
for d in "$ROOT"/*/; do
  [ -f "$d/attribution.json" ] || continue
  label=$(basename "$d")
  "$PF" attribute "$d/summary.json" > "$TMP/$label.attr.json" 2>/dev/null
  if cmp -s "$TMP/$label.attr.json" "$d/attribution.json"; then
    echo "  ✓ $label  attribution 字节一致"
  else
    echo "  ✗ $label  attribution 不一致"
    fail=1
  fi
done

# 3) 重新跑两臂分析, 比对 report.json
if [ -f "$ROOT/report.json" ] && [ -f "$ROOT/report.cmd" ]; then
  # report.cmd 记录了产生 report.json 的确切参数, 回放时原样重放
  ( cd "$ROOT" && bash report.cmd ) > "$TMP/report.json" 2>"$TMP/report.err"
  if cmp -s "$TMP/report.json" "$ROOT/report.json"; then
    echo "  ✓ report.json 字节一致"
  else
    echo "  ✗ report.json 不一致"
    diff "$ROOT/report.json" "$TMP/report.json" | head -20 | sed 's/^/      /'
    fail=1
  fi
fi

echo "──"
if [ "$fail" = "0" ]; then
  echo "✅ 回放自检通过: $n 轮 trace 全部可离线重算且字节一致"
else
  echo "❌ 回放自检失败"
fi
exit "$fail"
