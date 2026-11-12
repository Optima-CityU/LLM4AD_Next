#!/bin/bash
# genshin_fpscap.sh — 原神 (国服) 游戏内「帧率」档位: 查 / 设 / 还原。不登录。
#
# 两种办法 (FPSCAP_METHOD, 缺省 ui; R2 审查 #10):
#   ui   (缺省) 走游戏自己的设置界面: 派蒙 → 设置 → 图像 → 帧率, 每次点击前截图 + OCR 核对 (o1_fps_ui.py,
#        做法照抄 N3 的 n3_ui.py)。游戏必须已在大世界 (run_refresh_ab.sh 先 onboard 再调 set)。
#        不碰偏好设置文件。还原 = 界面改回原档 + 优雅退出让设置落盘。
#        set 前只读拉一份偏好设置文件记下原 index (fpscap_orig.txt, method=ui), 万一界面还原失败, 经用户
#        批准后可用 FPSCAP_METHOD=file restore 兜底。
#   file 旧办法 (沿用 2026-10-20 NPU 实验, 见 docs/experiments/2026-10-20-npu-sr/set_prefs.sh):
#        游戏停着时只改偏好设置文件里帧率那一项的 index 一个数字, 其余字节原样保留; 用 `cat 新文件 > 原文件`
#        写回 (保留属主和 SELinux 标签)。每次写之前: 整份文件拉回本地存成新文件, 并在手机上 cp -p 一份带日期的
#        副本 ($DEV_TMP/o1_gsprefs_backup_<时间>.xml); 写完回读核对「只有这一个数字不同」。
#        **这是对游戏数据文件的单值改写 (整份 cat 覆盖, 内容只差一个数字), 与「绝不覆盖」有边界, 需用户批准后才用。**
#        用了会在证据目录写 FILE_EDIT_USED.txt, 报告里要单独列出。
#   还原都是把帧率写回记录的原值, 不是拿旧文件整份盖回去。
#
# 档位 (index → 帧率): 2=60 (推断, 有旁证), 1=30 (推断)。可用 FPS_INDEX_30 / FPS_INDEX_60 改。
# 本脚本只核对文件/界面回读; 实际帧率由 run_refresh_ab.sh 进大世界后用上屏帧间隔核对, 对不上就停。
#
# 注意: 偏好设置文件里有账号 UID (__LastUid__), 证据目录只放本机, 不上传。
#
# 用法:
#   bash genshin_fpscap.sh status <证据目录>                 # 只读
#   bash genshin_fpscap.sh set <30|60> <证据目录>            # 首次 set 时记下原 index 到 <证据目录>/fpscap_orig.txt
#   bash genshin_fpscap.sh restore <证据目录>                # 写回 fpscap_orig.txt / fpscap_ui_orig.txt 里的原档
#   FPSCAP_METHOD=file ...                                   # 旧办法 (需用户批准)
# file 办法会先 am force-stop 原神 (游戏在跑时它退出会把内存里的设置写回文件, 改了也白改); ui 办法要求游戏在跑。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/refresh_lib.sh"

PREFS="/data/data/$PKG/shared_prefs/$PKG.v2.playerprefs.xml"
FPS_INDEX_30="${FPS_INDEX_30:-1}"
FPS_INDEX_60="${FPS_INDEX_60:-2}"
FPSCAP_METHOD="${FPSCAP_METHOD:-ui}"
EDIT="python3 -I $HERE/genshin_fps_prefs.py"
UI="python3 -I $HERE/o1_fps_ui.py"

action="${1:-}"; shift || true

pull_prefs() { # <本地新文件>
  new_file "$1"
  if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] adb exec-out su -c 'cat $PREFS' > $1" >&2; return 0; fi
  "$ADB" -s "$SERIAL" exec-out "su -c 'cat $PREFS'" > "$1" </dev/null
  [ -s "$1" ] || die "拉不到 $PREFS"
}

push_prefs() { # <本地文件> <证据目录>   (file 办法: 单值改写, 写前手机上留带日期的副本)
  local ts dst bak
  ts=$(date +%Y%m%d-%H%M%S)
  dst="$DEV_TMP/o1_gsprefs_${ts}_$RANDOM.xml"
  bak="$DEV_TMP/o1_gsprefs_backup_${ts}_$RANDOM.xml"
  if [ "$DRY_RUN" = 1 ]; then echo "[dry-run] adb push $1 $dst; su -c 'cp -p $PREFS $bak; cat $dst > $PREFS'" >&2; return 0; fi
  [ -n "$(dsh "ls $dst 2>/dev/null")" ] && die "$dst 已存在"
  [ -n "$(dsh "ls $bak 2>/dev/null")" ] && die "$bak 已存在"
  "$ADB" -s "$SERIAL" push "$1" "$dst" </dev/null >/dev/null || die "push 失败"
  dsu "cp -p $PREFS $bak" || die "手机上留副本 $bak 失败, 不写"
  dsu "cmp $PREFS $bak && echo same" | grep -q same || die "副本 $bak 与原文件不一致, 不写"
  echo "FILE_EDIT $(date '+%Y-%m-%d %H:%M:%S') prefs 单值改写; 改前副本 手机:$bak 本机:$1" >> "$2/FILE_EDIT_USED.txt"
  dsu "cat $dst > $PREFS" || die "写回 $PREFS 失败"
  dsu "ls -lZ $PREFS" >&2
}

stop_game() { dsh "am force-stop $PKG" >/dev/null; sleep 2; }

idx_of() { $EDIT get "$1" | sed -n 's/^fps_index=\([0-9]*\).*/\1/p'; }

case "$action" in
  status)
    D="${1:?缺证据目录}"; mkdir -p "$D"
    f="$D/prefs_status_$(date +%Y%m%d-%H%M%S).xml"
    pull_prefs "$f"
    [ "$DRY_RUN" = 1 ] && { echo "fps_index=?"; exit 0; }
    $EDIT get "$f"
    ;;

  set)
    CAP="${1:?缺 30|60}"; D="${2:?缺证据目录}"; mkdir -p "$D"
    case "$CAP" in 30) want="$FPS_INDEX_30";; 60) want="$FPS_INDEX_60";; *) die "只支持 30 / 60";; esac
    if [ "$FPSCAP_METHOD" = ui ]; then
      # 游戏界面里改 (游戏须已在大世界)。先只读拉一份文件记原 index (兜底还原用), 不写文件
      ts=$(date +%Y%m%d-%H%M%S)
      cur="$D/prefs_before_uiset${CAP}_$ts.xml"
      pull_prefs "$cur"
      if [ "$DRY_RUN" != 1 ] && [ ! -e "$D/fpscap_orig.txt" ]; then
        have=$(idx_of "$cur")
        { echo "orig_fps_index=${have:-?}"; echo "orig_file=$cur"; echo "method=ui"; } > "$D/fpscap_orig.txt"
        log "记下原帧率 index=${have:-?} (文件里的, 只读; 界面原档由 o1_fps_ui.py 记 fpscap_ui_orig.txt)"
      fi
      out=$($UI set "$CAP" "$D" 2> >(sed 's/^/  [fps_ui] /' >&2)); rc=$?
      echo "$out" | tail -1
      [ "$rc" = 0 ] || die "界面改帧率失败 (退出码 $rc): $(echo "$out" | tail -1)"
      exit 0
    fi
    log "FPSCAP_METHOD=file: 改写偏好设置文件 (单值, 需用户批准)"
    stop_game
    ts=$(date +%Y%m%d-%H%M%S)
    cur="$D/prefs_before_set${CAP}_$ts.xml"
    pull_prefs "$cur"
    [ "$DRY_RUN" = 1 ] && { log "dry-run: 会把帧率 index 改成 $want"; exit 0; }
    have=$(idx_of "$cur"); [ -n "$have" ] || die "偏好设置里找不到唯一的帧率项"
    if [ ! -e "$D/fpscap_orig.txt" ]; then
      echo "orig_fps_index=$have" > "$D/fpscap_orig.txt"
      echo "orig_file=$cur" >> "$D/fpscap_orig.txt"
      log "记下原帧率 index=$have"
    fi
    if [ "$have" = "$want" ]; then log "帧率 index 已是 $want ($CAP 帧), 不写"; exit 0; fi
    new="$D/prefs_set${CAP}_$ts.xml"
    $EDIT set "$cur" "$new" "$want" >/dev/null || die "改不了帧率项"
    $EDIT same-except-fps "$cur" "$new" >/dev/null || die "改出来的文件不止帧率一处不同, 不写"
    push_prefs "$new" "$D"
    back="$D/prefs_readback_set${CAP}_$ts.xml"
    pull_prefs "$back"
    cmp -s "$new" "$back" || die "回读与写入不一致"
    log "帧率 index $have → $want ($CAP 帧) 已写入并回读一致"
    ;;

  restore)
    D="${1:?缺证据目录}"
    if [ "$FPSCAP_METHOD" = ui ]; then
      [ -e "$D/fpscap_ui_orig.txt" ] || { echo "FPSCAP_RESTORED (界面里没改过帧率, 不用还原)"; exit 0; }
      out=$($UI restore "$D" 2> >(sed 's/^/  [fps_ui] /' >&2)); rc=$?
      echo "$out" | tail -1
      if [ "$rc" = 0 ]; then echo "FPSCAP_RESTORED (界面)"; exit 0; fi
      echo "FPSCAP_RESTORE_FAIL 界面还原失败 (退出码 $rc)。原档记录: $(tr '\n' ' ' < "$D/fpscap_ui_orig.txt")$([ -e "$D/fpscap_orig.txt" ] && echo " 文件原 index: $(sed -n 's/^orig_fps_index=//p' "$D/fpscap_orig.txt")")。"
      echo "  人工在游戏里改回, 或经用户批准后: FPSCAP_METHOD=file bash genshin_fpscap.sh restore $D"
      exit 4
    fi
    [ -e "$D/fpscap_orig.txt" ] || { log "没有 fpscap_orig.txt: 本次没改过帧率, 不用还原"; exit 0; }
    orig=$(sed -n 's/^orig_fps_index=//p' "$D/fpscap_orig.txt")
    case "$orig" in ""|"?") echo "FPSCAP_RESTORE_FAIL fpscap_orig.txt 里没有可用的原 index"; exit 4;; esac
    log "FPSCAP_METHOD=file: 改写偏好设置文件 (单值, 需用户批准)"
    stop_game
    ts=$(date +%Y%m%d-%H%M%S)
    cur="$D/prefs_before_restore_$ts.xml"
    pull_prefs "$cur"
    [ "$DRY_RUN" = 1 ] && { log "dry-run: 会把帧率 index 写回 $orig"; exit 0; }
    have=$(idx_of "$cur")
    if [ "$have" = "$orig" ]; then echo "FPSCAP_RESTORED index=$orig (本来就是)"; exit 0; fi
    new="$D/prefs_restore_$ts.xml"
    $EDIT set "$cur" "$new" "$orig" >/dev/null || die "改不了帧率项"
    $EDIT same-except-fps "$cur" "$new" >/dev/null || die "还原文件不止帧率一处不同, 不写"
    push_prefs "$new" "$D"
    back="$D/prefs_readback_restore_$ts.xml"
    pull_prefs "$back"
    if [ "$(idx_of "$back")" = "$orig" ]; then echo "FPSCAP_RESTORED index=$orig"
    else echo "FPSCAP_RESTORE_FAIL 想写 $orig, 回读 $(idx_of "$back")"; exit 4; fi
    ;;

  *) sed -n '2,32p' "$0"; exit 2 ;;
esac
