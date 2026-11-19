#!/system/bin/sh
# Fixed host-supplied read-only diagnostic; no remote worker is executed.
set -eu
export LC_ALL=C
D=${1:?}; TOKEN=${2:?}; BOOT=${3:?}
case "$TOKEN:$BOOT" in ''|*[!a-zA-Z0-9_:-]*) exit 70 ;; esac
[ "$D" = "/data/local/tmp/phonefarm-confirmation-$TOKEN-reader" ] || exit 70
identity() {
  [ -d "$D" ] && [ ! -L "$D" ] && [ ! -L "$D/worker.lock" ] && [ ! -L "$D/render_initial" ] && [ ! -L "$D/session.closed" ] || exit 71
  boot=$(head -c 129 /proc/sys/kernel/random/boot_id && printf x) || exit 72
  [ "$boot" = "$(printf '%s\nx' "$BOOT")" ] || exit 72
  [ -f "$D/owner" ] && [ ! -L "$D/owner" ] || exit 71
  owner=$(head -c 8193 "$D/owner" && printf x) || exit 71
  [ "$owner" = "$(printf '%s %s\nx' "$TOKEN" "$BOOT")" ] || exit 71
}
identity
TOTAL=0
out() {
  s=$1; n=${#s}; TOTAL=$((TOTAL+n+1)); [ "$TOTAL" -le 2097152 ] || exit 94
  printf '%s\n' "$s"
}
record() {
  label=$1; path=$2; cap=$3
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then out "MISSING$(printf '\t')$label"; return 0; fi
  if [ ! -f "$path" ] || [ -L "$path" ]; then out "UNKNOWN$(printf '\t')$label$(printf '\t')type"; return 0; fi
  size=$(wc -c < "$path" | tr -d '[:space:]') || { out "UNKNOWN$(printf '\t')$label$(printf '\t')read"; return 0; }
  case "$size" in ''|*[!0-9\ ]) out "UNKNOWN$(printf '\t')$label$(printf '\t')size"; return 0 ;; esac
  [ "$size" -le "$cap" ] || { out "UNKNOWN$(printf '\t')$label$(printf '\t')oversize"; return 0; }
  # Read the same fixed file with a finite cap; changes are diagnostic UNKNOWN.
  data=$(head -c "$((cap+1))" "$path" && printf x) || { out "UNKNOWN$(printf '\t')$label$(printf '\t')read"; return 0; }
  data=${data%x}; n=${#data}
  [ "$n" = "$size" ] || { out "UNKNOWN$(printf '\t')$label$(printf '\t')changed_or_nul"; return 0; }
  out "BEGIN$(printf '\t')$label$(printf '\t')$size"
  TOTAL=$((TOTAL+size)); [ "$TOTAL" -le 2097152 ] || exit 94
  printf '%s' "$data"; printf '\n'
  out "END$(printf '\t')$label"
}
# Proc stat has exactly one kernel-generated line; builtin read avoids per-PID forks.
statline() {
  path=$1
  [ -f "$path" ] && [ ! -L "$path" ] || return 1
  STAT=$(head -c 4097 "$path") || return 1
  [ "${#STAT}" -le 4096 ] || return 1
  case "$STAT" in *") "*) ;; *) return 1 ;; esac
  REST=${STAT##*) }; set -f; set -- $REST; set +f
  [ "$#" -ge 20 ] || return 1
  PG=$3; shift 19; TICKS=$1
  case "$PG:$TICKS" in ''|*[!0-9:]*) return 1 ;; esac
  [ -n "$PG" ] && [ -n "$TICKS" ] || return 1
}
out '#phonefarm_helper_diagnostic@1'
record owner "$D/owner" 8192
record closed "$D/session.closed/owner" 8192
record lock_owner "$D/worker.lock/owner" 8192
record master "$D/render_initial/master.tsv" 8192
record children "$D/render_initial/children.tsv" 8192
record pending "$D/render_initial/launch-render_reader.pending" 8192
record permit "$D/render_initial/permit-render_reader" 8192
record reader_log "$D/render_initial/render_reader.log" 524288
record reader_stderr "$D/render_initial/render_reader.stderr" 131072
record reader_exit "$D/render_initial/render_exit.txt" 8192
record quiescence "$D/render_initial/quiescence.txt" 8192
record journal "$D/journal.tsv" 131072
MP=''; CP=''
if [ -f "$D/worker.lock/owner" ] && [ ! -L "$D/worker.lock/owner" ]; then
  row=$(head -c 8193 "$D/worker.lock/owner")
  if [ "${#row}" -le 8192 ]; then
    set -f; set -- $row; set +f
    if [ "$#" = 5 ] && [ "$1" = "$TOKEN" ] && [ "$2" = "$BOOT" ] && [ "$5" = render_initial ]; then
      case "$3:$4" in *[!0-9:]*|:) ;; *) [ "$3" -gt 0 ] && [ "$3" -le 2147483647 ] && [ "$4" -gt 0 ] && MP=$3 ;; esac
    fi
  fi
fi
if [ -f "$D/render_initial/children.tsv" ] && [ ! -L "$D/render_initial/children.tsv" ]; then
  row=$(head -c 8193 "$D/render_initial/children.tsv")
  if [ "${#row}" -le 8192 ]; then
    set -f; set -- $row; set +f
    if [ "$#" = 4 ] && [ "$1" = render_reader ] && [ "$4" = "$D/render_ownership.sh" ]; then
      case "$2:$3" in *[!0-9:]*|:) ;; *) [ "$2" -gt 0 ] && [ "$2" -le 2147483647 ] && [ "$3" -gt 0 ] && CP=$2 ;; esac
    fi
  fi
fi
if [ -n "$MP" ]; then out "SELECTION$(printf '\t')master$(printf '\t')registered$(printf '\t')$MP"; else out "SELECTION$(printf '\t')master$(printf '\t')UNKNOWN"; fi
if [ -n "$CP" ]; then out "SELECTION$(printf '\t')child$(printf '\t')registered$(printf '\t')$CP"; else out "SELECTION$(printf '\t')child$(printf '\t')UNKNOWN"; fi
for pid in "$MP" "$CP"; do
  [ -n "$pid" ] || continue
  record "proc_$pid.stat" "/proc/$pid/stat" 4096
  record "proc_$pid.status" "/proc/$pid/status" 65536
  if [ -f "/proc/$pid/cmdline" ]; then
    size=$(wc -c < "/proc/$pid/cmdline")
    [ "$size" -le 4096 ] || { out "UNKNOWN$(printf '\t')proc_$pid.cmdline$(printf '\t')oversize"; continue; }
    out "CMDLINE_BASE64$(printf '\t')$pid$(printf '\t')$(head -c 4097 "/proc/$pid/cmdline" | base64 | tr -d '\n')"
  else out "MISSING$(printf '\t')proc_$pid.cmdline"; fi
 done
count=0; matched=0
for proc in /proc/[0-9]*; do
  [ -d "$proc" ] || continue
  count=$((count+1)); [ "$count" -le 2048 ] || { out "UNKNOWN$(printf '\t')process_scan$(printf '\t')cap"; exit 94; }
  if ! statline "$proc/stat"; then out "UNKNOWN$(printf '\t')process_scan$(printf '\t')${proc##*/}"; continue; fi
  if { [ -n "$MP" ] && [ "$PG" = "$MP" ]; } || { [ -n "$CP" ] && [ "$PG" = "$CP" ]; }; then
    matched=$((matched+1)); [ "$matched" -le 256 ] || exit 94
    out "GROUP_MEMBER$(printf '\t')${proc##*/}$(printf '\t')$PG$(printf '\t')$TICKS$(printf '\t')$STAT"
  fi
done
out "SCAN$(printf '\t')$count$(printf '\t')$matched"
identity
out '#phonefarm_helper_diagnostic_end@1'
