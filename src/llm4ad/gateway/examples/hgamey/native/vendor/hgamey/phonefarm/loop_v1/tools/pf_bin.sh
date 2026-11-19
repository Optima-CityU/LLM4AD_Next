#!/bin/bash
# pf_bin.sh — 解析 phonefarm 二进制路径, 供 loop_v1 各载体/回放脚本 source。
#
# 解析与归因已经从 Python 搬进二进制 (phonefarm parse-trace / attribute), 所以这些
# 脚本都得先知道二进制在哪。找不到就当场报错退出, 不静默继续 —— 否则会落下一份
# 空的 summary.json, 后面整条证据链都是假的。
#
# 顺序: PF_BIN 显式覆盖 > 仓库根已构建的 ./phonefarm > src/target 下的 cargo 产物。
# worktree 里跑实验时 PF_BIN 最有用: 指到哪个 worktree 的二进制就用哪个。
_pf_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PF=""
if [ -n "${PF_BIN:-}" ]; then
  # 显式覆盖也要验在不在、能不能跑: 写错一个字母就静默落一份空 summary.json,
  # 那会一路伪装成"判据 5 字节不一致", 查起来比当场报错贵得多。
  if [ ! -x "$PF_BIN" ]; then
    echo "PF_BIN 指向的不是可执行文件: $PF_BIN" >&2
    exit 1
  fi
  PF="$PF_BIN"
else
  for _c in "$_pf_root/phonefarm" \
            "$_pf_root/src/target/release/phonefarm" \
            "$_pf_root/src/target/debug/phonefarm"; do
    if [ -x "$_c" ]; then PF="$_c"; break; fi
  done
fi
if [ -z "$PF" ]; then
  echo "找不到 phonefarm 二进制: 先 (cd $_pf_root/src && cargo build --release), 或设 PF_BIN=<路径>" >&2
  exit 1
fi
export PF
unset _pf_root _c
