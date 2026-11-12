#!/system/bin/sh
# An owned child cannot begin side effects until its durable PID/start/group ledger exists.
set -eu
D=$1; TOKEN=$2; BOOT=$3; R=$4; TAG=$5; SCRIPT=$6; shift 6
n=0
while [ ! -f "$R/permit-$TAG" ]; do
 [ ! -e "$D/session.closed" ] || exit 86
 [ "$(cat "$D/owner")" = "$TOKEN $BOOT" ] || exit 81
 [ "$(cat /proc/sys/kernel/random/boot_id)" = "$BOOT" ] || exit 82
 [ "$n" -lt 100 ] || exit 83
 n=$((n+1)); sleep .05
done
[ "$(cat "$R/permit-$TAG")" = "$TOKEN $BOOT" ] || exit 84
[ ! -e "$D/session.closed" ] || exit 86
case "$TAG" in render_reader) exec timeout 20 sh "$SCRIPT" "$@" ;; *) exec sh "$SCRIPT" "$@" ;; esac
