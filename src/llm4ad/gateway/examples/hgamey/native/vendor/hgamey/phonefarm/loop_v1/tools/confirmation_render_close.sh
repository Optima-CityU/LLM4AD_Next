#!/system/bin/sh
# Fixed helper-only closure checker; no control/ftrace writes and no adoption.
set -eu
D=$1; TOKEN=$2; BOOT=$3; RUN=$4; MODE=${5:-verify}
case "$D" in "/data/local/tmp/phonefarm-confirmation-$TOKEN-reader") ;; *) exit 70 ;; esac
case "$TOKEN:$RUN" in *[!a-zA-Z0-9_:-]*) exit 70 ;; esac
case "$MODE" in verify|remove) ;; *) exit 70 ;; esac
[ "$(cat /proc/sys/kernel/random/boot_id)" = "$BOOT" ] || exit 72
[ "$(cat "$D/owner")" = "$TOKEN $BOOT" ] || exit 71
[ "$(cat "$D/session.closed/owner")" = "$TOKEN $BOOT $RUN" ] || exit 86
[ ! -e "$D/worker.lock" ] || exit 85
[ "$(cat "$D/$RUN/session_closed.txt")" = "SESSION_REVOKED $TOKEN $BOOT $RUN" ] || exit 86
[ "$(cat "$D/$RUN/quiescence.txt")" = OWNED_GROUPS_ABSENT ] || exit 77
complete() {
 [ -f "$1" ] && [ ! -L "$1" ] && [ -s "$1" ] || return 1
 n=$(wc -c < "$1" | tr -d '[:space:]'); [ "$n" -le 8192 ] || return 1
 last=$(tail -c 1 "$1" && printf x); [ "$last" = "$(printf '\nx')" ]
}
absent() {
 case "$1" in ''|*[!0-9]*) return 1 ;; esac
 [ "$1" -gt 0 ] && [ "$1" -le 2147483647 ] && [ ! -d "/proc/$1" ] || return 1
 if error=$(/system/bin/kill -0 -- "-$1" 2>&1); then return 1; fi
 case "$error" in *"No such process"*) ;; *) return 1 ;; esac
}
# An interrupted preinstall may have no initial child/master. Reconciliation's
# own master is mandatory; any present initial ledger must be complete/owned.
for name in render_initial "$RUN"; do
 R="$D/$name"
 [ -d "$R" ] || { [ "$name" = render_initial ] && continue; exit 77; }
 complete "$R/master.tsv" || exit 77
 awk -v t="$TOKEN" -v b="$BOOT" -v r="$name" 'NF!=5 || $1!=t || $2!=b || $3!~/^[0-9]+$/ || $3+0<=0 || $3+0>2147483647 || $4!~/^[0-9]+$/ || $4+0<=0 || $5!=r {bad=1} END {exit (bad || NR!=1)}' "$R/master.tsv" || exit 77
 IFS=' ' read -r ot ob pid ticks or < "$R/master.tsv"; absent "$pid" || exit 77
 if [ -e "$R/children.tsv" ] || [ -L "$R/children.tsv" ]; then
  complete "$R/children.tsv" || exit 77
  awk -F '\t' -v s="$D/render_ownership.sh" 'NF!=4 || $1!="render_reader" || $2!~/^[0-9]+$/ || $2+0<=0 || $2+0>2147483647 || $3!~/^[0-9]+$/ || $3+0<=0 || $4!=s {bad=1} END {exit (bad || NR!=1)}' "$R/children.tsv" || exit 77
  IFS="$(printf '\t')" read -r tag pid ticks script < "$R/children.tsv"; absent "$pid" || exit 77
 fi
 for pending in "$R"/launch-*.pending; do [ ! -e "$pending" ] && [ ! -L "$pending" ] || exit 77; done
 done
if [ "$MODE" = remove ]; then
 # Check owner/boot again immediately before removal of this exact unique path.
 [ "$(cat "$D/owner")" = "$TOKEN $BOOT" ] && [ "$(cat /proc/sys/kernel/random/boot_id)" = "$BOOT" ] || exit 71
 rm -rf "$D"; [ ! -e "$D" ] && [ ! -L "$D" ] || exit 77
 printf 'HELPER_REMOVED %s %s %s\n' "$TOKEN" "$BOOT" "$RUN"
else printf 'HELPER_CLOSED %s %s %s\n' "$TOKEN" "$BOOT" "$RUN"; fi
