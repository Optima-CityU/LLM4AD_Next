#!/bin/sh
# knob_framecap.sh {apply|restore|status} — 黑档旋钮骨架: 厂商限帧策略 / 游戏空间性能模式
#
# 为什么最要紧 (DESIGN §3): 红魔上原神被封顶 @30fps, GPU 只用 64.5%。不解开它, 任何降低
# GPU 工作量的优化都换不出帧率 —— 天花板是假的 (判据 6)。
#
# ⚠️ 骨架状态: 具体控制点 (厂商性能模式 setting / 游戏空间 service / perf hint) 尚未在
# NX809J 上确认。设备可用后 (refbench 判定电池不占用设备时) 逐项探明再落实现。现在只保证
# 接口形状与回滚纪律与 knob_ddr_boost.sh 一致, 且在未确认时安全空转 (KNOB_SKIP), 不乱写。
set -u

STATE=/data/local/tmp/knob_framecap.state

# TODO(设备可用后确认并填入): 候选控制点, 逐个验证可写 + 回读 + 对帧率有效
#   settings 层: game.* / 厂商性能模式 secure/system 键
#   service 层: 游戏空间 / GameManager performance mode
#   sysfs 层: 若限帧在内核侧
CANDIDATES=""   # 确认前留空 → apply 全部 KNOB_SKIP, 不改任何系统状态

case "${1:-status}" in
  apply)
    if [ -f "$STATE" ]; then echo "KNOB_ALREADY_APPLIED (state=$STATE)"; exit 0; fi
    if [ -z "$CANDIDATES" ]; then
      echo "KNOB_SKIP framecap: 控制点未在本设备确认, 骨架不写系统状态 (见脚本 TODO)"
      exit 0
    fi
    : > "$STATE"
    # 确认后: 逐项存原值 → 写目标 → 回读比对, 同 knob_ddr_boost.sh
    echo "KNOB_APPLIED framecap (占位)"
    ;;
  restore)
    if [ ! -f "$STATE" ]; then echo "KNOB_NOT_APPLIED (无 $STATE, 无需回滚)"; exit 0; fi
    # 确认后: 读 STATE 回写并回读比对, 成功后删 STATE
    rm -f "$STATE"
    echo "KNOB_RESTORED framecap (占位)"
    ;;
  status)
    [ -f "$STATE" ] && echo "state: 已施加" || echo "state: 未施加"
    echo "candidates_confirmed: $( [ -n "$CANDIDATES" ] && echo yes || echo no )"
    ;;
  *)
    echo "用法: knob_framecap.sh {apply|restore|status}" >&2
    exit 2
    ;;
esac
