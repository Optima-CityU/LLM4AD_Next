#!/system/bin/sh
# Called only as the ledgered workload child of confirmation_worker gray_sample.
# The parent created this exclusive instance and owns its eventual removal.
set -eu
T=${1:?owned trace instance}; DUR=${2:?seconds}; OUT=${3:?owned round directory}
scripted=0
if [ "$#" = 5 ]; then
  [ "$4" = 6 ] || exit 70
  qualify=$5; case "$qualify" in 0|1) ;; *) exit 70 ;; esac
  scripted=1
else [ "$#" = 3 ] || exit 70; fi
case "$T" in /sys/kernel/tracing/instances/phonefarm_gray_*) ;; *) exit 70 ;; esac
token=${T#/sys/kernel/tracing/instances/phonefarm_gray_}
case "$token" in ''|*[!a-zA-Z0-9_:-]*) exit 70 ;; esac
case "$DUR" in ''|*[!0-9]*) exit 70 ;; esac
[ "$DUR" -ge 1 ] && [ "$DUR" -le 86400 ] || exit 70
D=${OUT%/*}; run=${OUT##*/}; name=${D##*/}
[ "$D" = "/data/local/tmp/$name" ] || exit 70
case "$name" in phonefarm-confirmation-*) ;; *) exit 70 ;; esac
case "$run" in ''|*[!a-zA-Z0-9_:-]*) exit 70 ;; esac
[ -d "$T" ] && [ ! -L "$T" ] && [ -d "$OUT" ] && [ ! -L "$OUT" ] || exit 70
for file in trace.txt trace-events.tsv; do
  [ ! -e "$OUT/$file" ] && [ ! -L "$OUT/$file" ] || exit 70
done
if [ "$scripted" = 1 ]; then
  [ "$DUR" -le 594 ] || exit 70
  R=$OUT; TOKEN=$token; BOOT=$(cat /proc/sys/kernel/random/boot_id)
  . "$D/gray_child_guard.sh"
  gray_child_begin trace "$((DUR+36))"
  trap 'code=$?; trap - EXIT INT TERM; gray_child_finish "$code" || exit 75; exit "$code"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
fi

set_checked() {
  printf '%s\n' "$2" > "$T/$1" || return 1
  [ "$(cat "$T/$1")" = "$2" ]
}
read_optional() { cat "$1" 2>/dev/null || true; }
set_checked tracing_on 0
set_checked current_tracer nop
clock=$(cat "$T/trace_clock")
printf '%s\n' "$clock" | tr '[]' '  ' | awk '{for (i=1;i<=NF;i++) if ($i=="boot") found=1} END {exit !found}' || exit 74
printf 'boot\n' > "$T/trace_clock"
[ "$(sed -n 's/.*\[\([^]]*\)\].*/\1/p' "$T/trace_clock")" = boot ] || exit 74
if set_checked buffer_size_kb 65536; then :; else set_checked buffer_size_kb 16384; fi

: > "$OUT/trace-events.tsv"
for event in adreno_cmdbatch_submitted adreno_cmdbatch_retired; do
  [ -f "$T/events/kgsl/$event/enable" ] || exit 74
  set_checked "events/kgsl/$event/enable" 1
  printf '%s\trequired_enabled\n' "$event" >> "$OUT/trace-events.tsv"
done
for event in kgsl_buslevel kgsl_pwrlevel kgsl_gpu_frequency kgsl_thermal_constraint kgsl_clock_throttling kgsl_bcl_clock_throttling; do
  if [ ! -f "$T/events/kgsl/$event/enable" ]; then status=unavailable
  elif set_checked "events/kgsl/$event/enable" 1; then status=enabled
  else status=enable_failed; fi
  printf '%s\t%s\n' "$event" "$status" >> "$OUT/trace-events.tsv"
done
: > "$T/trace"
if [ "$scripted" = 1 ]; then
  if [ "$qualify" = 1 ]; then screencap -p > "$OUT/frame_a.png"; [ -s "$OUT/frame_a.png" ]; fi
  printf 'TRACE_READY\n' > "$OUT/trace-ready.txt"; sync
  n=0
  while [ ! -f "$OUT/workload-start.txt" ]; do
    [ "$n" -lt 200 ] || exit 76
    n=$((n+1)); sleep .1
  done
  input_start=$(cat "$OUT/workload-start.txt")
  wait_from_input() {
    now=$(gray_uptime)
    delay=$(awk -v start="$input_start" -v now="$now" -v offset="$1" 'BEGIN {if(start !~ /^[0-9]+([.][0-9]+)?$/ || now<start) exit 1; d=start+offset-now; printf "%.6f",(d>0?d:0)}')
    sleep "$delay"
  }
  if [ "$qualify" = 1 ]; then
    wait_from_input 5
    screencap -p > "$OUT/frame_b.png"; [ -s "$OUT/frame_b.png" ]
  fi
  wait_from_input 6
fi
set_checked tracing_on 1
start=$(cut -d' ' -f1 /proc/uptime)
GRAY_WINDOW_START=$start
sleep "$DUR"
set_checked tracing_on 0
end=$(cut -d' ' -f1 /proc/uptime)
GRAY_WINDOW_END=$end

{
  printf '#loop_v1_meta capture_start_uptime=%s capture_end_uptime=%s duration_req=%s\n' "$start" "$end" "$DUR"
  printf '#loop_v1_meta gpu_model=%s\n' "$(read_optional /sys/class/kgsl/kgsl-3d0/gpu_model)"
  printf '#loop_v1_meta ddr_cur_khz=%s ddr_max_khz=%s\n' "$(read_optional /sys/devices/system/cpu/bus_dcvs/DDR/cur_freq)" "$(read_optional /sys/devices/system/cpu/bus_dcvs/DDR/hw_max_freq)"
  printf '#loop_v1_meta ddr_boost_khz=%s llcc_boost_khz=%s\n' "$(read_optional /sys/devices/system/cpu/bus_dcvs/DDR/boost_freq)" "$(read_optional /sys/devices/system/cpu/bus_dcvs/LLCC/boost_freq)"
  printf '#loop_v1_meta gpu_busy=%s gpu_clk=%s\n' "$(read_optional /sys/class/kgsl/kgsl-3d0/gpubusy)" "$(read_optional /sys/class/kgsl/kgsl-3d0/gpuclk)"
  printf '#loop_v1_meta thermal_pwrlevel=%s min_pwrlevel=%s max_pwrlevel=%s\n' "$(read_optional /sys/class/kgsl/kgsl-3d0/thermal_pwrlevel)" "$(read_optional /sys/class/kgsl/kgsl-3d0/min_pwrlevel)" "$(read_optional /sys/class/kgsl/kgsl-3d0/max_pwrlevel)"
  printf '# phonefarm trace_mode=instance buffer_size_kb=%s trace_clock=boot instance=%s\n' "$(cat "$T/buffer_size_kb")" "$T"
  while IFS="$(printf '\t')" read -r event status; do
    printf '# phonefarm trace_event=%s status=%s\n' "$event" "$status"
  done < "$OUT/trace-events.tsv"
  for cpu in "$T"/per_cpu/cpu*; do
    [ -d "$cpu" ] || continue
    overrun=$(grep -E '^overrun' "$cpu/stats" 2>/dev/null) || overrun=unavailable
    printf '# phonefarm %s %s\n' "${cpu##*/}" "$overrun"
  done
  cat "$T/trace"
} > "$OUT/trace.txt"
sync
echo GRAY_TRACE_COMPLETE
