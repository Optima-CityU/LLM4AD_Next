# refresh_lib.sh — O1 主机端脚本共用的函数 (source 用, 不单独执行)。
#
# 环境变量:
#   SERIAL   设备序列号 (缺省红魔 NX809J 91253241019A)
#   DRY_RUN  1 = 不碰手机: 所有 adb 调用只打印出来, 读数一律当空 (用来核对流程和生成的请求)
#   ADB      adb 路径 (缺省 PATH 里的 adb)
#
# 纪律: 只新建文件, 不删除、不覆盖 (本地落盘一律 new_file 检查; 设备端推送用带时间戳的新文件名)。

SERIAL="${SERIAL:-91253241019A}"
DRY_RUN="${DRY_RUN:-0}"
ADB="${ADB:-adb}"
PKG="${PKG:-com.miHoYo.Yuanshen}"
O1_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
O1_PY="python3 -I $O1_HERE/refresh_analyze.py"
O1_TS="${O1_TS:-$(date +%Y%m%d-%H%M%S)}"
DEV_TMP=/data/local/tmp
DEVSH="$DEV_TMP/o1_refresh_dev_$O1_TS.sh"

log() { echo "[$(date +%H:%M:%S)] $*" >&2; }
die() { log "停: $*"; exit 1; }

# 本地新文件: 已存在就停 (不覆盖)
new_file() { [ -e "$1" ] && die "$1 已存在, 不覆盖"; : ; }

# 设备 shell (非 root)
dsh() {
  if [ "$DRY_RUN" = "1" ]; then echo "[dry-run] adb -s $SERIAL shell $*" >&2; return 0; fi
  "$ADB" -s "$SERIAL" shell "$@" </dev/null | tr -d '\r'
}
# 设备 shell (root)。参数只能是简单词 (不含单引号)。
dsu() {
  if [ "$DRY_RUN" = "1" ]; then echo "[dry-run] adb -s $SERIAL shell su -c '$*'" >&2; return 0; fi
  "$ADB" -s "$SERIAL" shell "su -c '$*'" </dev/null | tr -d '\r'
}
# 调设备端工具
dev() { dsu "sh $DEVSH $*"; }

push_dev_tool() {
  if [ "$DRY_RUN" = "1" ]; then echo "[dry-run] adb push o1_refresh_dev.sh -> $DEVSH" >&2; return 0; fi
  [ -n "$(dsh "ls $DEVSH 2>/dev/null")" ] && die "设备上 $DEVSH 已存在, 不覆盖"
  "$ADB" -s "$SERIAL" push "$O1_HERE/o1_refresh_dev.sh" "$DEVSH" </dev/null >/dev/null || die "推送设备端工具失败"
}

preflight_device() {
  [ "$DRY_RUN" = "1" ] && { log "dry-run: 跳过设备检查"; return 0; }
  "$ADB" -s "$SERIAL" get-state 2>/dev/null | grep -q device || die "设备 $SERIAL 不在线"
  dsu id | grep -q 'uid=0' || die "设备没有 root (su -c id 不是 uid=0)"
}

# 当前活动模式 → JSON 一行
active_json() {
  if [ "$DRY_RUN" = "1" ]; then dev active >/dev/null; echo '{"panel_hz": null, "render_hz": null, "sflat_hz": null, "sf_hz": null, "src": null}'; return; fi
  dev active | $O1_PY active
}
json_get() { python3 -I -c 'import json,sys; v=json.loads(sys.argv[1]).get(sys.argv[2]); print("" if v is None else v)' "$1" "$2"; }
panel_hz() { json_get "$(active_json)" panel_hz; }
hz_near() { $O1_PY near "$1" "$2" "${3:-0.03}"; }

sget() { dev sget "$1" "$2"; }
# 写设置: 拒绝 null/空 (设备端也会拒)
sput() {
  case "${3:-}" in ""|null) die "拒绝写 $1:$2=null/空 (还原只能 delete, 违反铁律)";; esac
  dev sput "$1" "$2" "$3"
}

# 游戏真正上屏的帧率 (连续两次 --latency, 合起来算)
present_fps() { # <输出目录> <cap>
  local d="$1" cap="$2" f
  f="$d/present_check_$(date +%H%M%S).txt"; new_file "$f"
  { dev lat "$PKG"; sleep 2; dev lat "$PKG"; } > "$f"
  [ "$DRY_RUN" = "1" ] && { echo '{"present_fps": null}'; return; }
  $O1_PY present "$f" "$cap"
}
