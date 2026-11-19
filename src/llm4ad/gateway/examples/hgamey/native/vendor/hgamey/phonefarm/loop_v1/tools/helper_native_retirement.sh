#!/system/bin/sh
# Receiver-owned finite helper-only dead retirement. Original tools remain unchanged.
set -eu
export LC_ALL=C
D=$1; TOKEN=$2; BOOT=$3; RUN=$4; MODE=$5
case "$TOKEN:$BOOT:$RUN" in ''|*[!a-zA-Z0-9_:-]*) exit 70 ;; esac
[ "$D" = "/data/local/tmp/phonefarm-confirmation-$TOKEN-reader" ] || exit 70
case "$MODE" in inspect|close|remove) ;; *) exit 70 ;; esac
R="$D/${RUN}_$MODE"
identity() {
 [ -d "$D" ] && [ ! -L "$D" ] && [ ! -L "$D/render_initial" ] && [ ! -L "$D/worker.lock" ] && [ ! -L "$D/session.closed" ] || exit 71
 [ "$(cat /proc/sys/kernel/random/boot_id)" = "$BOOT" ] || exit 72
 [ -f "$D/owner" ] && [ ! -L "$D/owner" ] && [ "$(cat "$D/owner")" = "$TOKEN $BOOT" ] || exit 71
}
# Exact current bytes; caller cannot select any filename or hash.
verify_files() {
__FIXED_FILES__
 [ -x "$D/render_reader.elf" ] || exit 73
}
complete() {
 [ -f "$1" ] && [ ! -L "$1" ] && [ -s "$1" ] || return 1
 n=$(wc -c < "$1" | tr -d '[:space:]'); [ "$n" -le 8192 ] || return 1
 last=$(tail -c 1 "$1" && printf x); [ "$last" = "$(printf '\nx')" ]
}
# No live-stop authority: a reused PID is deliberately also rejected.
absent() {
 case "$1" in ''|*[!0-9]*) return 1 ;; esac
 [ "$1" -gt 0 ] && [ "$1" -le 2147483647 ] && [ ! -d "/proc/$1" ] || return 1
 if error=$(/system/bin/kill -0 -- "-$1" 2>&1); then return 1; fi
 case "$error" in *"No such process"*) ;; *) return 1 ;; esac
}
stat() {
 IFS= read -r STAT < "/proc/$1/stat" || return 1
 [ "${#STAT}" -le 4095 ] || return 1
 case "$STAT" in "$1 ("*") "*) ;; *) return 1 ;; esac
 REST=${STAT##*) }; set -f; set -- $REST; set +f
 [ "$#" -ge 20 ] || return 1
 PG=$3; shift 19; TICKS=$1
 case "$PG:$TICKS" in ''|*[!0-9:]*) return 1 ;; esac
 [ "$PG" -gt 0 ] && [ "$TICKS" -gt 0 ] || return 1
}
identity; verify_files
for pending in "$D/render_initial"/launch-*.pending; do [ ! -e "$pending" ] && [ ! -L "$pending" ] || exit 77; done
complete "$D/render_initial/master.tsv" && complete "$D/render_initial/children.tsv" && complete "$D/render_initial/permit-render_reader" || exit 77
IFS=' ' read -r mt mb MP MS mr extra < "$D/render_initial/master.tsv"
[ "$mt $mb $mr" = "$TOKEN $BOOT render_initial" ] && [ -z "$extra" ] || exit 77
IFS="$(printf '\t')" read -r tag CP CS script extra < "$D/render_initial/children.tsv"
[ "$tag" = render_reader ] && [ "$script" = "$D/render_ownership.sh" ] && [ -z "$extra" ] || exit 77
case "$MP:$MS:$CP:$CS" in ''|*[!0-9:]*) exit 77 ;; esac
[ "$MP" -gt 0 ] && [ "$MS" -gt 0 ] && [ "$CP" -gt 0 ] && [ "$CS" -gt 0 ] && [ "$MP" != "$CP" ] || exit 77
[ "$(wc -l < "$D/render_initial/master.tsv" | tr -d '[:space:]')" = 1 ] && [ "$(wc -l < "$D/render_initial/children.tsv" | tr -d '[:space:]')" = 1 ] || exit 77
[ "$(cat "$D/render_initial/permit-render_reader")" = "$TOKEN $BOOT" ] || exit 77
if [ "$MODE" = remove ]; then
 [ "$(cat "$D/session.closed/owner")" = "$TOKEN $BOOT $RUN" ] && [ ! -e "$D/worker.lock" ] || exit 86
 complete "$D/${RUN}_close/actor.tsv" && complete "$D/${RUN}_close/child.tsv" || exit 77
 IFS=' ' read -r at ab AP AS AG ar < "$D/${RUN}_close/actor.tsv"
 [ "$at $ab $ar" = "$TOKEN $BOOT ${RUN}_close" ] && [ "$AP" = "$AG" ] || exit 77
 IFS=' ' read -r nt NP NS NG nh < "$D/${RUN}_close/child.tsv"
 [ "$nt" = native_reader ] && [ "$NP" = "$NG" ] && [ "$nh" = __ELF_SHA__ ] || exit 77
 CLOSE_NP=$NP
 __PRIOR_CHECKS__
 absent "$AP" && absent "$CLOSE_NP" || exit 77
 [ -d "$D/session.closed" ] && [ ! -L "$D/session.closed" ] && [ -d "$D/${RUN}_close" ] && [ ! -L "$D/${RUN}_close" ] || exit 77
 for path in "$D/session.closed"/* "$D/session.closed"/.[!.]* "$D/session.closed"/..?*; do
  [ -e "$path" ] || { [ ! -L "$path" ] || exit 77; continue; }
  [ "$path" = "$D/session.closed/owner" ] && [ -f "$path" ] && [ ! -L "$path" ] || exit 77
 done
 complete "$D/session.closed/owner" || exit 77
 n=0
 for path in "$D/${RUN}_close"/* "$D/${RUN}_close"/.[!.]* "$D/${RUN}_close"/..?*; do
  [ -e "$path" ] || { [ ! -L "$path" ] || exit 77; continue; }
  [ ! -L "$path" ] || exit 77
  n=$((n+1)); [ "$n" -le 10 ] || exit 77
  case "${path##*/}" in start.pipe) [ -p "$path" ] || exit 77 ;;
  actor.tsv|child.tsv|requests.bin|runner.stdout|runner.stderr|native.stdout|native.stderr|native.exit|closed.txt) [ -f "$path" ] || exit 77 ;;
  *) exit 77 ;; esac
 done
 [ "$n" = 10 ] && [ "$(cat "$D/${RUN}_close/native.exit")" = 0 ] && [ "$(cat "$D/${RUN}_close/closed.txt")" = "CLOSED $TOKEN $BOOT $RUN" ] || exit 77
else
 [ ! -e "$D/session.closed" ] && [ ! -L "$D/session.closed" ] || exit 86
 complete "$D/worker.lock/owner" || exit 77
 [ "$(cat "$D/worker.lock/owner")" = "$mt $mb $MP $MS $mr" ] || exit 77
fi
# Reject any unregistered helper content, including symlinks and unknown runs.
for path in "$D"/* "$D"/.[!.]* "$D"/..?*; do
 [ -e "$path" ] || { [ ! -L "$path" ] || exit 77; continue; }
 [ ! -L "$path" ] || exit 77
 case "${path##*/}" in owner|worker.sh|bootstrap.sh|render_ownership.sh|render_reader.elf|render_args.tsv|journal.tsv) [ -f "$path" ] || exit 77 ;;
 render_initial|worker.lock) [ -d "$path" ] || exit 77 ;;
 session.closed|"${RUN}_close") [ "$MODE" = remove ] || exit 77 ;;
 *) exit 77 ;; esac
done
for path in "$D/render_initial"/* "$D/render_initial"/.[!.]* "$D/render_initial"/..?*; do
 [ -e "$path" ] || { [ ! -L "$path" ] || exit 77; continue; }
 [ -f "$path" ] && [ ! -L "$path" ] || exit 77
 case "${path##*/}" in master.tsv|children.tsv|permit-render_reader|render_reader.log|render_reader.stderr|render_exit.txt|quiescence.txt) ;; *) exit 77 ;; esac
done
absent "$MP" && absent "$CP" || exit 77
[ ! -e "$R" ] && [ ! -L "$R" ] || exit 77
mkdir "$R"
stat "$$" && [ "$PG" = "$$" ] || exit 77
ACT=$$; AST=$TICKS
NP=''; NS=''; child_done=0
cleanup() {
 ec=$?; trap - EXIT HUP INT TERM
 if [ "$child_done" = 0 ] && [ -n "$NP" ] && [ -n "$NS" ]; then
  if stat "$NP" && [ "$PG" = "$NP" ] && [ "$TICKS" = "$NS" ]; then
   /system/bin/kill -KILL -- "-$NP" 2>/dev/null || :
   wait "$NP" 2>/dev/null || :
  fi
 fi
 exit "$ec"
}
trap cleanup EXIT
trap 'exit 77' HUP INT TERM
printf '%s %s %s %s %s %s\n' "$TOKEN" "$BOOT" "$ACT" "$AST" "$ACT" "${RUN}_$MODE" > "$R/actor.tsv"
sync
mkfifo "$R/start.pipe"
# 写端随脚本存活：cap 等提前退出的路径退出时关闭写端，
# 门内读者立刻收到 EOF 退出，而不是永久阻塞（任何平台）。fd3 不用于其他用途。
exec 3>"$R/start.pipe"
printf 'native_reader\n' > "$R/launch.pending"; sync
# The child is a new group and blocks before exec until its exact epoch is durable.
setsid sh -c 'set -eu; IFS= read -r gate < "$1/start.pipe"; [ "$gate" = "$3 $4" ]; exec "$2" < "$1/requests.bin" > "$1/native.stdout" 2> "$1/native.stderr"' sh "$R" "$D/render_reader.elf" "$TOKEN" "$BOOT" > "$R/runner.stdout" 2> "$R/runner.stderr" &
NP=$!; tries=0
while ! { stat "$NP" && [ "$PG" = "$NP" ]; }; do
 tries=$((tries+1)); [ "$tries" -le 20 ] || exit 77; sleep .01
done
NS=$TICKS
printf 'native_reader %s %s %s %s\n' "$NP" "$NS" "$NP" __ELF_SHA__ > "$R/child.tsv"; sync
# Regular request file is complete before the child is permitted to execute.
request() { printf '%s\000%s\000%s\000' "$1" "$2" "$3"; }
{
 request block owner "$D/owner"
 request block master "$D/render_initial/master.tsv"
 request block children "$D/render_initial/children.tsv"
 request block boot_before /proc/sys/kernel/random/boot_id
 request block actor "/proc/$ACT/stat"
 request block native_child "/proc/$NP/stat"
 count=0
 for path in /proc/[0-9]*; do
  [ -d "$path" ] && [ ! -L "$path" ] || exit 77
  case "${path##*/}" in ''|*[!0-9]*) exit 77 ;; esac
  count=$((count+1)); [ "$count" -le 1024 ] || exit 94
  request block "process_stat/${path##*/}" "$path/stat"
 done
 [ "$count" -gt 0 ] || exit 94
 request value inventory_count "$count"
 request block boot_after /proc/sys/kernel/random/boot_id
 request end '' ''
} > "$R/requests.bin"
sync
printf '%s %s\n' "$TOKEN" "$BOOT" > "$R/start.pipe"
rm "$R/launch.pending"
if wait "$NP"; then child_done=1; printf '0\n' > "$R/native.exit"; else ec=$?; child_done=1; printf '%s\n' "$ec" > "$R/native.exit"; exit 77; fi
absent "$NP" || exit 77
# Native cap/end plus independent finite grammar/stat checks, before any retirement.
awk -v tok="$TOKEN" -v boot="$BOOT" -v mp="$MP" -v cp="$CP" -v ms="$MS" -v cs="$CS" -v sc="$script" -v ap="$ACT" -v ast="$AST" -v np="$NP" -v ns="$NS" -v priorap="${AP:-0}" -v priornp="${CLOSE_NP:-0}" '
 function statline(s,p,expectedpg,expectedstart, a,r,n) {if(length(s)+1>4096 || s !~ /^[0-9]+ \(.*\) [A-Za-z] /) return 0; r=s; sub(/^.*\) /,"",r); n=split(r,a," "); if(n<20 || a[3]!~/^[0-9]+$/ || a[3]+0<=0 || a[20]!~/^[0-9]+$/ || a[20]+0<=0 || s+0!=p+0) return 0; if(expectedpg!="" && (a[3]!=expectedpg || a[20]!=expectedstart)) return 0; if(p==mp || p==cp || a[3]==mp || a[3]==cp || p==priorap || p==priornp || a[3]==priorap || a[3]==priornp) return 0; return 1 }
 NR==1 {if($0!="#phonefarm_render_witness v2") bad=1; next}
 $0=="#end" {if(label!="" || ended) bad=1; ended=1; next}
 /^BEGIN\t/ {if(ended || label!="" || NF!=2 || seen[$2]++) bad=1; label=$2; body=""; lines=0; next}
 /^END\t/ {if(ended || $2!=label || NF!=2) bad=1;
  # block output includes the original LF and the framing LF, value has one LF.
  if(label=="owner") {if(body!=tok " " boot "\n") bad=1}
  else if(label=="master") {if(body!=tok " " boot " " mp " " ms " render_initial\n") bad=1}
  else if(label=="children") {if(body!="render_reader\t" cp "\t" cs "\t" sc "\n") bad=1}
  else if(label=="boot_before" || label=="boot_after") {if(body!=boot "\n") bad=1}
  else if(label=="inventory_count") {if(lines!=1 || body!~/^[0-9]+$/ || body+0<1 || body+0>1024) bad=1; expected=body+0}
  else if(label=="actor" || label=="native_child" || label~/^process_stat\/[0-9]+$/) {s=body; sub(/\n$/, "",s); if(s~/\n/) bad=1; if(label=="actor") {if(!statline(s,ap,ap,ast)) bad=1} else if(label=="native_child") {if(!statline(s,np,np,ns)) bad=1} else {p=label; sub(/^process_stat\//,"",p); if(!statline(s,p,"","")) bad=1; total++}}
  else bad=1; label=""; next}
 {if(ended || label=="") bad=1; if(lines++) body=body "\n"; body=body $0; if(length(body)>65536) bad=1}
 END {if(bad || !ended || total!=expected || !seen["owner"] || !seen["master"] || !seen["children"] || !seen["boot_before"] || !seen["boot_after"] || !seen["actor"] || !seen["native_child"] || !seen["inventory_count"]) exit 77}
' FS='\t' "$R/native.stdout" || exit 77
identity; verify_files; absent "$MP" && absent "$CP" && absent "$NP" || exit 77
stat "$ACT" && [ "$PG" = "$ACT" ] && [ "$TICKS" = "$AST" ] || exit 77
if [ "$MODE" = close ]; then
 [ "$(cat "$D/worker.lock/owner")" = "$mt $mb $MP $MS $mr" ] || exit 77
 for path in "$D/worker.lock"/* "$D/worker.lock"/.[!.]* "$D/worker.lock"/..?*; do
  [ -e "$path" ] || { [ ! -L "$path" ] || exit 77; continue; }
  [ "$path" = "$D/worker.lock/owner" ] && [ -f "$path" ] && [ ! -L "$path" ] || exit 77
 done
 mkdir "$D/session.closed"
 printf '%s %s %s\n' "$TOKEN" "$BOOT" "$RUN" > "$D/session.closed/owner"; sync
 rm "$D/worker.lock/owner"; rmdir "$D/worker.lock"; sync
 printf 'CLOSED %s %s %s\n' "$TOKEN" "$BOOT" "$RUN" > "$R/closed.txt"; sync
fi
cat "$R/native.stdout"
printf 'ACTOR %s %s %s\nCHILD %s %s %s\nSTAGE %s %s\n' "$ACT" "$AST" "$ACT" "$NP" "$NS" "$NP" "$MODE" "$RUN"
if [ "$MODE" = remove ]; then
 identity; absent "$MP" && absent "$CP" && absent "$NP" && absent "$AP" && absent "$CLOSE_NP" || exit 77
 __PRIOR_CHECKS__
 [ "$(cat "$D/session.closed/owner")" = "$TOKEN $BOOT $RUN" ] || exit 77
 # Only the strict helper inventory checked above and this bound operation are removed.
 rm -rf "$D"
 [ ! -e "$D" ] && [ ! -L "$D" ] || exit 77
fi
printf '#phonefarm_native_helper_end@1\n'
