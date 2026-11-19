#!/system/bin/sh
# Fixed readonly requests; native reader owns byte bounds and terminal marker.
set -eu
LC_ALL=C; export LC_ALL
PKG=${1:?package}; COMM=${2:?render comm}; READER=${3:?native reader}
case "$PKG:$COMM" in *[!a-zA-Z0-9_.:]*) exit 81 ;; esac
case "$READER" in /*) ;; *) exit 81 ;; esac
[ -x "$READER" ]
request() { printf '%s\000%s\000%s\000' "$1" "$2" "$3"; }
block() { request block "$1" "$2"; }
value() { request value "$1" "$2"; }
filter_value() { request filter "$1" "$2"; }
produce() {
value package "$PKG"
block boot /proc/sys/kernel/random/boot_id
block uptime /proc/uptime
block kernel /proc/version
PIDS=$(pidof "$PKG"); set -- $PIDS; [ "$#" -eq 1 ]; PID=$1
case "$PID" in ''|*[!0-9]*) exit 81 ;; esac
value pid "$PID"
value package_uid "$(cmd package list packages -U "$PKG")"
VERSION=$(dumpsys package "$PKG" | sed -n '/^[[:space:]]*versionCode=/p; /^[[:space:]]*versionName=/p')
[ "${#VERSION}" -le 4096 ]; value package_version "$VERSION"
for item in status stat; do block "process_$item" "/proc/$PID/$item"; done
request cmdline process_cmdline "/proc/$PID/cmdline"
count=0
for task in /proc/"$PID"/task/[0-9]*; do
  [ -d "$task" ]; count=$((count+1)); [ "$count" -le 256 ]; TID=${task##*/}
  for item in status stat comm; do block "thread_$item/$TID" "$task/$item"; done
done
[ "$count" -gt 0 ]; value task_count "$count"
count=0
for dev in /sys/class/kgsl/kgsl-*; do
  [ -d "$dev" ]; count=$((count+1)); [ "$count" -le 1 ]; value device "${dev##*/}"
done
[ "$count" -eq 1 ]
for event in adreno_cmdbatch_queued adreno_cmdbatch_submitted kgsl_context_create kgsl_context_detach kgsl_context_destroy; do
  block "format/$event" "/sys/kernel/tracing/events/kgsl/$event/format"
  block "enabled/$event" "/sys/kernel/tracing/events/kgsl/$event/enable"
  block "filter/$event" "/sys/kernel/tracing/events/kgsl/$event/filter"
  block "trigger/$event" "/sys/kernel/tracing/events/kgsl/$event/trigger"
done
for event in sched_process_fork sched_process_exit; do
  block "format/$event" "/sys/kernel/tracing/events/sched/$event/format"
  block "enabled/$event" "/sys/kernel/tracing/events/sched/$event/enable"
  block "filter/$event" "/sys/kernel/tracing/events/sched/$event/filter"
  block "trigger/$event" "/sys/kernel/tracing/events/sched/$event/trigger"
done
block trace_clock /sys/kernel/tracing/trace_clock
filter_value event_pid /sys/kernel/tracing/set_event_pid
filter_value event_notrace_pid /sys/kernel/tracing/set_event_notrace_pid
block tracing_cpumask /sys/kernel/tracing/tracing_cpumask
count=0
for cpu in /sys/kernel/tracing/per_cpu/cpu[0-9]*; do
  [ -d "$cpu" ]; count=$((count+1)); [ "$count" -le 64 ]; block "stats/${cpu##*/}" "$cpu/stats"
done
[ "$count" -gt 0 ]
block boot_end /proc/sys/kernel/random/boot_id
block uptime_end /proc/uptime
request end '' ''
}
produce | "$READER"
