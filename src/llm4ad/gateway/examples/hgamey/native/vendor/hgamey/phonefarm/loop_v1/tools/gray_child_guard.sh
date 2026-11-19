# Sourced only inside a ledgered gray script child. The watchdog never setsid:
# it and any blocked input/screencap remain in that child's existing process group.
gray_uptime() {
  v=$(cut -d' ' -f1 /proc/uptime) || return 1
  awk -v v="$v" 'BEGIN {if(v !~ /^[0-9]+([.][0-9]+)?$/) exit 1; print v}'
}
gray_child_begin() {
  GRAY_TAG=$1; budget=$2; GRAY_LEADER=$$
  case "$GRAY_TAG:$budget:$TOKEN:$BOOT:${R##*/}" in *[!a-zA-Z0-9_:-]*) return 70 ;; esac
  case "$budget" in ''|*[!0-9]*) return 70 ;; esac
  [ "$budget" -ge 1 ] && [ "$budget" -le 640 ] || return 70
  [ -d "$R" ] && [ ! -L "$R" ] && [ "$(cat "$D/owner")" = "$TOKEN $BOOT" ] || return 71
  [ "$(cat /proc/sys/kernel/random/boot_id)" = "$BOOT" ] || return 72
  [ "$(sed 's/^.*) //' /proc/$$/stat | awk '{print $3}')" = "$$" ] || return 73
  GRAY_START=$(gray_uptime) || return 74
  deadline=$(awk -v s="$GRAY_START" -v b="$budget" 'BEGIN {printf "%.6f",s+b}') || return 74
  (
    while now=$(gray_uptime) && awk -v n="$now" -v d="$deadline" 'BEGIN {exit !(n<d)}'; do sleep .1; done
    kill -KILL -- "-$GRAY_LEADER"
  ) &
  GRAY_WATCHDOG=$!
  [ ! -e "$R/$GRAY_TAG-start.txt" ] && [ ! -e "$R/$GRAY_TAG-time.json" ] || return 74
  printf '%s\n' "$GRAY_START" > "$R/$GRAY_TAG-start.txt" && sync
}
gray_child_finish() {
  code=$1
  kill -TERM "$GRAY_WATCHDOG" 2>/dev/null || true
  wait "$GRAY_WATCHDOG" 2>/dev/null || true
  GRAY_END=$(gray_uptime) || return 74
  printf '{"schema":"phonefarm/gray-child-time@1","child":"%s","start":%s,"end":%s,"child_start":%s,"child_end":%s,"exit_code":%s}\n' "$GRAY_TAG" "${GRAY_WINDOW_START:-$GRAY_START}" "${GRAY_WINDOW_END:-$GRAY_END}" "$GRAY_START" "$GRAY_END" "$code" > "$R/$GRAY_TAG-time.json.tmp" && sync && mv "$R/$GRAY_TAG-time.json.tmp" "$R/$GRAY_TAG-time.json" && sync
}
