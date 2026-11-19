#!/system/bin/sh
# Owned remote worker. All paths/values originate in frozen receiver plans.
set -eu
D=${1:?owned directory}; TOKEN=${2:?token}; BOOT=${3:?boot}; MODE=${4:?mode}; RUN=${5:-none}
case "$D" in /data/local/tmp/phonefarm-confirmation-*) ;; *) exit 70 ;; esac
case "$TOKEN:$RUN" in *[!a-zA-Z0-9_:-]*) exit 70 ;; esac
[ "$(cat "$D/owner")" = "$TOKEN $BOOT" ] || exit 71
[ "$(cat /proc/sys/kernel/random/boot_id)" = "$BOOT" ] || exit 72
case "$MODE" in verify_bench|verify_gray_groups) ;; *) [ ! -e "$D/session.closed" ] || exit 86 ;; esac
# A capability diagnostic session cannot enter any performance sampling mode.
if [ -e "$D/thermal-purpose.txt" ]; then
  [ -f "$D/thermal-purpose.txt" ] && [ "$(cat "$D/thermal-purpose.txt")" = capability_absolute_v1 ] || exit 87
  case "$MODE" in control_probe|reconcile_controls) ;; *) exit 87 ;; esac
fi
R="$D/$RUN"
# Gray sampling owns one trace instance; it never falls back to global tracing.
if [ "$MODE" = gray_sample ] || [ "$MODE" = gray_script_sample ] || [ "$MODE" = gray_control_sample ]; then
  if [ "$MODE" = gray_script_sample ] || [ "$MODE" = gray_control_sample ]; then
    [ "$#" = 8 ] || exit 70
    gray_qualify=$8
    case "$gray_qualify" in 0|1) ;; *) exit 70 ;; esac
  else [ "$#" = 7 ] || exit 70; fi
  gray_seconds=$6; gray_instance=$7
  case "$gray_seconds" in ''|*[!0-9]*) exit 70 ;; esac
  [ "$gray_seconds" -ge 1 ] && [ "$gray_seconds" -le 86400 ] || exit 70
  [ "$MODE" != gray_script_sample ] || [ "$gray_seconds" -le 594 ] || exit 70
  if [ "$MODE" = gray_control_sample ]; then
    [ "$gray_seconds" -le 120 ] && [ "$gray_qualify" = 0 ] || exit 70
    [ -f "$D/workload-$RUN.sh" ] && [ ! -L "$D/workload-$RUN.sh" ] || exit 70
  fi
  [ "$gray_instance" = "/sys/kernel/tracing/instances/phonefarm_gray_$TOKEN" ] || exit 70
  [ -d "$gray_instance" ] && [ ! -L "$gray_instance" ] || exit 70
fi
#phonefarm_thermal_clock_begin
thermal_uptime() {
  v=$(cut -d' ' -f1 /proc/uptime) || return 1
  awk -v v="$v" 'BEGIN {if (v !~ /^[0-9]+([.][0-9]+)?$/) exit 1; print v}'
}
thermal_deadline() {
  awk -v a="$1" -v d="$2" 'BEGIN {if (a !~ /^[0-9]+([.][0-9]+)?$/ || d !~ /^[0-9]+([.][0-9]+)?$/) exit 1; printf "%.6f\n", a+d}'
}
thermal_point_uptime() {
  printf '%s\n' "$1" | awk -F '\t' '$1=="UPTIME" {n++; v=$2} END {if (n!=1 || v !~ /^[0-9]+([.][0-9]+)?$/) exit 1; print v}'
}
thermal_wait_until() {
  desired=$1; latest=$2; previous=$3; current=$(thermal_uptime) || return 1
  delay=$(awk -v a="$current" -v d="$desired" -v l="$latest" -v p="$previous" 'BEGIN {if (a !~ /^[0-9]+([.][0-9]+)?$/ || d !~ /^[0-9]+([.][0-9]+)?$/ || l !~ /^[0-9]+([.][0-9]+)?$/ || p !~ /^[0-9]+([.][0-9]+)?$/ || p>d || d>l || a<p || a>l) exit 1; if (d>a) printf "%.6f\n", d-a+0.02; else print 0}') || return 1
  [ "$delay" = 0 ] || sleep "$delay" || return 1
  current=$(thermal_uptime) || return 1
  awk -v a="$current" -v d="$desired" -v l="$latest" 'BEGIN {exit !(a>=d && a<=l)}'
}
#phonefarm_thermal_clock_end
intent() {
  [ ! -e "$D/session.closed" ] || exit 86
  [ "$(cat "$D/owner")" = "$TOKEN $BOOT" ] || exit 71
  [ "$(cat /proc/sys/kernel/random/boot_id)" = "$BOOT" ] || exit 72
  printf '%s\t%s\t%s\t%s\n' "$TOKEN" "$1" "$2" "${3:-}" >> "$D/journal.tsv"
  sync
}
read_value() {
  if [ "$1" = setting ]; then settings get system "${2#system:}"; else cat "$2"; fi
}
write_value() {
  kind=$1; path=$2; value=$3
  case "$path" in *thermal*|*cooling*|*trip_point*|*fan*|*bcl*|*throttl*) exit 73 ;; esac
  case "$kind:$path" in
    sysfs:/sys/devices/system/cpu/cpufreq/policy*/scaling_governor|sysfs:/sys/devices/system/cpu/cpufreq/policy*/scaling_min_freq|sysfs:/sys/devices/system/cpu/cpufreq/policy*/scaling_max_freq|sysfs:/sys/class/kgsl/kgsl-3d0/min_pwrlevel|sysfs:/sys/class/kgsl/kgsl-3d0/max_pwrlevel|sysfs:/sys/class/kgsl/kgsl-3d0/devfreq/governor|sysfs:/sys/class/kgsl/kgsl-3d0/devfreq/min_freq|sysfs:/sys/class/kgsl/kgsl-3d0/devfreq/max_freq|sysfs:/sys/devices/system/cpu/bus_dcvs/DDR/boost_freq|sysfs:/sys/devices/system/cpu/bus_dcvs/LLCC/boost_freq) ;;
    setting:system:peak_refresh_rate|setting:system:min_refresh_rate|setting:system:refresh_rate_mode) ;;
    *) exit 73 ;;
  esac
  intent write "$path" "$value" || return 1
  if [ "$kind" = setting ]; then
    if [ "$value" = null ]; then settings delete system "${path#system:}" >/dev/null; else settings put system "${path#system:}" "$value"; fi
  else printf '%s\n' "$value" > "$path"; fi
}
# Re-read current bounds after governor changes. Legal endpoint order depends on direction.
target_value() {
  wanted=''
  if [ "$PLAN" != - ]; then wanted=$(awk -F '\t' -v p="$2" '$3==p {print $4}' "$PLAN"); fi
  if [ -n "$wanted" ]; then printf '%s' "$wanted"; else awk -F '\t' -v p="$2" '$2==p {print $3}' "$D/restore.tsv"; fi
}
bounds_group() {
  low=$1; high=$2
  grep -F "$(printf '\t')$low$(printf '\t')" "$D/restore.tsv" >/dev/null || return 0
  lo=$(target_value sysfs "$low"); hi=$(target_value sysfs "$high")
  curlo=$(read_value sysfs "$low"); curhi=$(read_value sysfs "$high")
  case "$lo:$hi:$curlo:$curhi" in *[!0-9:]*) return 1 ;; esac
  [ -n "$lo" ] && [ -n "$hi" ] && [ -n "$curlo" ] && [ -n "$curhi" ] || return 1
  [ "$lo" -le "$hi" ] && [ "$curlo" -le "$curhi" ] || return 1
  if [ "$hi" -lt "$curlo" ]; then
    write_value sysfs "$low" "$lo" || return 1
    write_value sysfs "$high" "$hi" || return 1
  else
    write_value sysfs "$high" "$hi" || return 1
    write_value sysfs "$low" "$lo" || return 1
  fi
  [ "$(read_value sysfs "$low")" = "$lo" ] && [ "$(read_value sysfs "$high")" = "$hi" ]
}
refresh_group() {
  grep -F "$(printf '\t')system:peak_refresh_rate$(printf '\t')" "$D/restore.tsv" >/dev/null || return 0
  lo=$(target_value setting system:min_refresh_rate); hi=$(target_value setting system:peak_refresh_rate)
  current=$(read_value setting system:min_refresh_rate)
  if [ "$lo" = null ] || [ "$hi" = null ]; then
    write_value setting system:min_refresh_rate "$lo" || return 1
    write_value setting system:peak_refresh_rate "$hi" || return 1
  else
    [ "$current" != null ] || current=0
    awk -v a="$lo" -v b="$hi" 'BEGIN {exit !(a<=b)}' || return 1
    if awk -v a="$hi" -v b="$current" 'BEGIN {exit !(a<b)}'; then
      write_value setting system:min_refresh_rate "$lo" || return 1
      write_value setting system:peak_refresh_rate "$hi" || return 1
    else
      write_value setting system:peak_refresh_rate "$hi" || return 1
      write_value setting system:min_refresh_rate "$lo" || return 1
    fi
  fi
}
write_graph() {
  PLAN=$1
  while IFS="$(printf '\t')" read -r kind path old; do
    case "$path" in */scaling_governor|*/devfreq/governor) write_value "$kind" "$path" "$(target_value "$kind" "$path")" || return 1 ;; esac
  done < "$D/restore.tsv"
  for base in /sys/devices/system/cpu/cpufreq/policy*; do
    [ -d "$base" ] || continue
    bounds_group "$base/scaling_min_freq" "$base/scaling_max_freq" || return 1
  done
  bounds_group /sys/class/kgsl/kgsl-3d0/devfreq/min_freq /sys/class/kgsl/kgsl-3d0/devfreq/max_freq || return 1
  # KGSL powerlevel numbers run backwards: max is the numeric lower endpoint.
  bounds_group /sys/class/kgsl/kgsl-3d0/max_pwrlevel /sys/class/kgsl/kgsl-3d0/min_pwrlevel || return 1
  refresh_group || return 1
  while IFS="$(printf '\t')" read -r kind path old; do
    [ -n "$kind" ] || continue
    case "$path" in system:peak_refresh_rate|system:min_refresh_rate|*/scaling_governor|*/scaling_min_freq|*/scaling_max_freq|*/devfreq/governor|*/devfreq/min_freq|*/devfreq/max_freq|*/min_pwrlevel|*/max_pwrlevel) continue ;; esac
    write_value "$kind" "$path" "$(target_value "$kind" "$path")" || return 1
  done < "$D/restore.tsv"
  while IFS="$(printf '\t')" read -r kind path old; do
    [ -n "$kind" ] || continue
    [ "$(read_value "$kind" "$path")" = "$(target_value "$kind" "$path")" ] || return 1
  done < "$D/restore.tsv"
}
restore_controls() { write_graph -; }
trace_write() { intent trace_write "$1" "$2" || return 1; printf '%s\n' "$2" > "/sys/kernel/tracing/$1"; }
restore_trace() {
  trace_write tracing_on 0 || return 1
  trace_write current_tracer "$(cat "$D/ftrace-current_tracer")" || return 1
  trace_write buffer_size_kb "$(cat "$D/ftrace-buffer_size_kb")" || return 1
  trace_write set_event '' || return 1
  if [ -f "$D/ftrace-trace_clock" ]; then trace_write trace_clock "$(cat "$D/ftrace-trace_clock")" || return 1; fi
  while IFS= read -r event; do [ -z "$event" ] || { intent trace_event set_event "$event" || return 1; printf '%s\n' "$event" >> /sys/kernel/tracing/set_event || return 1; }; done < "$D/ftrace-set_event"
  trace_write tracing_on "$(cat "$D/ftrace-tracing_on")" || return 1
}
snapshot() {
  name=$1
  if sh "$D/snapshot.sh" > "$R/$name.txt" 2> "$R/$name.stderr"; then echo 0 > "$R/$name.exit"; else code=$?; echo "$code" > "$R/$name.exit"; return "$code"; fi
}
start_ticks() { sed 's/^.*) //' "/proc/$1/stat" 2>/dev/null | awk '{print $20}'; }
group_id() { sed 's/^.*) //' "/proc/$1/stat" 2>/dev/null | awk '{print $3}'; }
start_child() {
  tag=$1; script=$2; shift 2
  intent launch_owned_child "$script" "$tag" || return 1
  printf '%s\t%s\n' "$tag" "$script" > "$R/launch-$tag.pending" || return 1
  sync || return 1
  setsid sh "$D/bootstrap.sh" "$D" "$TOKEN" "$BOOT" "$R" "$tag" "$script" "$@" > "$R/$tag.log" 2> "$R/$tag.stderr" &
  pid=$!; ticks=''; n=0
  while [ "$n" -lt 20 ]; do
    ticks=$(start_ticks "$pid")
    [ -n "$ticks" ] && [ "$(group_id "$pid")" = "$pid" ] && break
    n=$((n+1)); sleep .05
  done
  [ -n "$ticks" ] && [ "$(group_id "$pid")" = "$pid" ] || return 1
  printf '%s\t%s\t%s\t%s\n' "$tag" "$pid" "$ticks" "$script" >> "$R/children.tsv"
  sync || return 1
  intent permit_owned_child "$script" "$tag" || return 1
  [ ! -e "$R/permit-$tag" ] && [ ! -e "$R/permit-$tag.tmp" ] || return 1
  printf '%s\n' "$TOKEN $BOOT" > "$R/permit-$tag.tmp" || return 1
  sync || return 1
  mv "$R/permit-$tag.tmp" "$R/permit-$tag" || return 1
  sync || return 1
  intent clear_pending "$script" "$tag" || return 1
  rm "$R/launch-$tag.pending" || return 1
  sync || return 1
  CHILD_PID=$pid
}
ledger_complete() {
  [ -f "$1" ] && [ ! -L "$1" ] && [ -s "$1" ] || return 1
  size=$(wc -c < "$1" | tr -d '[:space:]') || return 1
  case "$size" in ''|*[!0-9]*) return 1 ;; esac
  [ "$size" -le 8192 ] || return 1
  # Preserve the final newline through command substitution with a sentinel.
  last=$(tail -c 1 "$1" && printf x) || return 1
  [ "$last" = "$(printf '\nx')" ]
}
children_valid() {
  CHILD_ROWS=0
  GRAY_SCRIPT_ROUND=0
  GRAY_WORKLOAD="$D/workload.sh"
  if [ -e "$R/round-kind.txt" ] || [ -L "$R/round-kind.txt" ]; then
    ledger_complete "$R/round-kind.txt" || return 1
    case "$(cat "$R/round-kind.txt")" in
      gray_script_v1) ;;
      gray_control_v1) GRAY_WORKLOAD="$D/workload-${R##*/}.sh" ;;
      *) return 1 ;;
    esac
    GRAY_SCRIPT_ROUND=1
  fi
  [ -e "$R/children.tsv" ] || [ -L "$R/children.tsv" ] || return 0
  ledger_complete "$R/children.tsv" || return 1
  CHILD_ROWS=$(awk -F '\t' -v w="$GRAY_WORKLOAD" -v s="$D/sample_env.sh" -v tr="$D/trace.sh" -v scripted="$GRAY_SCRIPT_ROUND" -v th="$D/thermal_sampler.sh" -v rr="$D/render_ownership.sh" -v mode="$MODE" '
    BEGIN {bench=(mode=="bench" || mode=="gray_sample" || mode=="gray_script_sample" || mode=="gray_control_sample" || mode=="verify_gray_idle" || mode=="verify_gray_groups" || mode=="reconcile_bench" || mode=="verify_bench")}
    scripted && (!bench || mode=="bench" || mode=="gray_sample") {bad=1}
    bench && $1!="workload" && $1!="sampler" && !(scripted && $1=="trace") {bad=1}
    ((mode=="render_readonly" || mode=="reconcile_render") && $1!="render_reader") ||
    ((mode!="render_readonly" && mode!="reconcile_render") && $1=="render_reader") ||
    NF!=4 || ($1!="workload" && $1!="sampler" && $1!="thermal_sampler" && $1!="render_reader" && !(scripted && $1=="trace")) || $2!~/^[0-9]+$/ ||
    $2+0<=0 || $2+0>2147483647 || $3!~/^[0-9]+$/ || $3+0<=0 ||
    ($1=="workload" && $4!=w) || ($1=="sampler" && $4!=s) || ($1=="trace" && $4!=tr) || ($1=="thermal_sampler" && $4!=th) || ($1=="render_reader" && $4!=rr) {bad=1}
    ++tags[$1]>1 || ++pids[$2]>1 {bad=1}
    END {if (bad || NR<1 || NR>((mode=="render_readonly" || mode=="reconcile_render")?1:(bench&&!scripted?2:3))) exit 1; print NR}' "$R/children.tsv") || return 1
}
pending_known() {
  children_valid || return 1
  for pending in "$R"/launch-*.pending; do
    [ -e "$pending" ] || [ -L "$pending" ] || continue
    ledger_complete "$pending" || return 1
    [ -f "$R/children.tsv" ] || return 1
    expected=${pending##*/}; expected=${expected#launch-}; expected=${expected%.pending}
    case "$expected" in workload) script_expected="$GRAY_WORKLOAD" ;; sampler) script_expected="$D/sample_env.sh" ;; trace) [ "$GRAY_SCRIPT_ROUND" = 1 ] || return 1; script_expected="$D/trace.sh" ;; thermal_sampler) script_expected="$D/thermal_sampler.sh" ;; render_reader) script_expected="$D/render_ownership.sh" ;; *) return 1 ;; esac
    awk -F '\t' -v t="$expected" -v s="$script_expected" '
      NF!=2 || $1!=t || $2!=s {bad=1} END {exit (bad || NR!=1)}' "$pending" || return 1
    consumed_pending=0
    while IFS="$(printf '\t')" read -r tag script; do
      matched=$(awk -F '\t' -v t="$tag" -v s="$script" '$1==t && $4==s {n++} END {print n+0}' "$R/children.tsv") || return 1
      [ "$matched" = 1 ] || return 1
      consumed_pending=$((consumed_pending+1))
    done < "$pending"
    [ "$consumed_pending" = 1 ] || return 1
  done
}
group_absent_remote() {
  expected=$1
  case "$MODE" in
    bench|gray_sample|gray_script_sample|gray_control_sample|verify_gray_idle|verify_gray_groups|reconcile_bench|verify_bench)
      # Rootless bench cannot inspect unrelated root processes. ESRCH for the
      # exact negative PGID proves absence; EPERM and unknown errors do not.
      ;;
    *)
      for proc in /proc/[0-9]*; do
        [ -d "$proc" ] || continue
        pg=$(group_id "${proc##*/}")
        if [ -z "$pg" ]; then [ ! -d "$proc" ] || return 1; continue; fi
        [ "$pg" != "$expected" ] || return 1
      done
      ;;
  esac
  if error=$(LC_ALL=C /system/bin/kill -0 -- "-$expected" 2>&1); then return 1; fi
  case "$error" in *"No such process"*) ;; *) return 1 ;; esac
}
quiescent() {
  pending_known || return 1
  [ "$CHILD_ROWS" != 0 ] || return 0
  [ -f "$R/children.tsv" ] && [ ! -L "$R/children.tsv" ] || return 1
  required_child_rows=$CHILD_ROWS; consumed_children=0
  while IFS="$(printf '\t')" read -r tag pid ticks script; do
    if [ -d "/proc/$pid" ]; then return 1; fi
    # A dead leader with a surviving group cannot be safely attributed from PID alone.
    group_absent_remote "$pid" || return 1
    consumed_children=$((consumed_children+1))
  done < "$R/children.tsv"
  [ "$consumed_children" = "$required_child_rows" ]
}
stop_owned() {
  pending_known || return 1
  [ "$CHILD_ROWS" != 0 ] || return 0
  [ -f "$R/children.tsv" ] && [ ! -L "$R/children.tsv" ] || return 1
  required_child_rows=$CHILD_ROWS; consumed_children=0
  while IFS="$(printf '\t')" read -r tag pid ticks script; do
    if [ -d "/proc/$pid" ]; then
      [ "$(start_ticks "$pid")" = "$ticks" ] && [ "$(group_id "$pid")" = "$pid" ] || return 1
      cmd=$(tr '\000' ' ' < "/proc/$pid/cmdline")
      case "$cmd" in *"$script"*) ;; *) return 1 ;; esac
      intent stop_owned "$pid" "$ticks" || return 1
      kill -TERM -- "-$pid" || return 1
      wait "$pid" 2>/dev/null || true
    fi
    consumed_children=$((consumed_children+1))
  done < "$R/children.tsv"
  [ "$consumed_children" = "$required_child_rows" ] || return 1
  n=0; while [ "$n" -lt 50 ]; do quiescent && return 0; n=$((n+1)); sleep .1; done
  return 1
}
apply_plan() {
  restore_controls || return 1
  write_graph "$1"
}
render_prepare() {
  trace_write tracing_on 0 || return 1
  trace_write current_tracer nop || return 1
  trace_write buffer_size_kb 65536 || return 1
  trace_write trace_clock boot || return 1
  trace_write set_event '' || return 1
  for pair in kgsl/adreno_cmdbatch_queued kgsl/adreno_cmdbatch_submitted kgsl/kgsl_context_create kgsl/kgsl_context_detach kgsl/kgsl_context_destroy sched/sched_process_fork sched/sched_process_exit; do
    [ -f "/sys/kernel/tracing/events/$pair/enable" ] || return 1
    trace_write "events/$pair/enable" 1 || return 1
  done
  trace_write trace '' || return 1
  jstart=$(cut -d' ' -f1 /proc/uptime) || return 1
  trace_write tracing_on 1 || return 1
  IFS="$(printf '\t')" read -r pkg comm < "$D/render_args.tsv" || return 1
  timeout 20 sh "$D/render_ownership.sh" "$pkg" "$comm" "$D/render_reader.elf" > "$R/render-before.raw.tsv" || return 1
}
capture() {
  dur=$1
  if [ -f "$D/render_ownership.sh" ]; then
    sleep "$lead" || return 1
    a=$(cut -d' ' -f1 /proc/uptime) || return 1
    sleep "$dur" || return 1
    b=$(cut -d' ' -f1 /proc/uptime) || return 1
    sleep 3 || return 1
    tailend=$(cut -d' ' -f1 /proc/uptime) || return 1
    timeout 20 sh "$D/render_ownership.sh" "$pkg" "$comm" "$D/render_reader.elf" > "$R/render-after.raw.tsv" || return 1
    trace_write tracing_on 0 || return 1
    jend=$(cut -d' ' -f1 /proc/uptime) || return 1
    timeout 20 sh "$D/render_ownership.sh" "$pkg" "$comm" "$D/render_reader.elf" > "$R/render-final.raw.tsv" || return 1
    { printf '#loop_v1_meta capture_start_uptime=%s capture_end_uptime=%s duration_req=%s join_start_uptime=%s tail_end_uptime=%s join_end_uptime=%s\n' "$a" "$b" "$dur" "$jstart" "$tailend" "$jend"; cat /sys/kernel/tracing/trace; } > "$R/trace.txt"
  else
    trace_write tracing_on 0 || return 1
    trace_write current_tracer nop || return 1
    trace_write buffer_size_kb 65536 || return 1
    trace_write trace '' || return 1
    for event in adreno_cmdbatch_submitted adreno_cmdbatch_retired kgsl_buslevel kgsl_pwrlevel kgsl_gpu_frequency kgsl_thermal_constraint kgsl_clock_throttling kgsl_bcl_clock_throttling; do
      if [ -f "/sys/kernel/tracing/events/kgsl/$event/enable" ]; then trace_write "events/kgsl/$event/enable" 1 || return 1; fi
    done
    [ -f /sys/kernel/tracing/events/kgsl/adreno_cmdbatch_submitted/enable ] || return 1
    [ -f /sys/kernel/tracing/events/kgsl/adreno_cmdbatch_retired/enable ] || return 1
    trace_write tracing_on 1 || return 1
    a=$(cut -d' ' -f1 /proc/uptime) || return 1
    sleep "$dur" || return 1
    trace_write tracing_on 0 || return 1
    b=$(cut -d' ' -f1 /proc/uptime) || return 1
    { printf '#loop_v1_meta capture_start_uptime=%s capture_end_uptime=%s duration_req=%s\n' "$a" "$b" "$dur"; cat /sys/kernel/tracing/trace; } > "$R/trace.txt"
  fi
}
cleanup_failure() {
  trap - EXIT INT TERM
  # The absence of an attributable child/group is required before any restore write.
  if stop_owned; then
    case "$MODE" in bench|gray_sample|gray_script_sample|gray_control_sample|reconcile_bench|render_readonly|reconcile_render) : ;; control_probe|reconcile_controls) restore_controls || true ;; *) restore_trace && restore_controls || true ;; esac
  fi
}
acquire_master() {
  [ ! -e "$D/session.closed" ] || return 1
  L="$D/worker.lock"
  if [ -d "$L" ]; then
    case "$MODE" in reconcile|reconcile_controls|reconcile_render|reconcile_bench) ;; *) return 1 ;; esac
    ledger_complete "$L/owner" || return 1
    awk -v t="$TOKEN" -v b="$BOOT" 'NF!=5 || $1!=t || $2!=b {bad=1} END {exit (bad || NR!=1)}' "$L/owner" || return 1
    IFS=' ' read -r oldtoken oldboot oldpid oldticks oldrun < "$L/owner" || return 1
    [ "$oldtoken" = "$TOKEN" ] && [ "$oldboot" = "$BOOT" ] || return 1
    case "$oldpid:$oldticks:$oldrun" in *[!a-zA-Z0-9_:-]*) return 1 ;; esac
    case "$oldpid:$oldticks" in *[!0-9:]*) return 1 ;; esac
    [ "$oldpid" -gt 0 ] && [ "$oldticks" -gt 0 ] && [ -n "$oldrun" ] || return 1
    # A live/reused PID or surviving group is unknown; no recovery writes may race it.
    [ ! -d "/proc/$oldpid" ] && group_absent_remote "$oldpid" || return 1
    intent reconcile_dead_master "$oldpid" "$oldticks" || return 1
    rm "$L/owner" && rmdir "$L" && sync || return 1
  fi
  intent acquire_master "$L" "$$" || return 1
  mkdir "$L" || return 1
  [ ! -e "$D/session.closed" ] || return 1
  ticks=$(start_ticks "$$"); group=$(group_id "$$")
  [ -n "$ticks" ] && [ "$group" = "$$" ] || return 1
  MASTER="$TOKEN $BOOT $$ $ticks $RUN"
  printf '%s\n' "$MASTER" > "$L/owner" || return 1
  sync || return 1
}
release_master() {
  [ "$(cat "$D/worker.lock/owner")" = "$MASTER" ] || return 1
  if [ -e "$D/session.closed" ]; then
    # Closure forbids all normal intents. Only this matching terminal reconcile may unlock.
    case "$MODE" in reconcile|reconcile_controls|reconcile_render|reconcile_bench) ;; *) return 1 ;; esac
    [ "$(cat "$D/session.closed/owner")" = "$TOKEN $BOOT $RUN" ] || return 1
    [ "$(cat "$D/owner")" = "$TOKEN $BOOT" ] && [ "$(cat /proc/sys/kernel/random/boot_id)" = "$BOOT" ] || return 1
    printf '%s\tterminal_release_master\t%s\t%s\n' "$TOKEN" "$D/worker.lock" "$$" >> "$D/journal.tsv"
    sync || return 1
  else
    intent release_master "$D/worker.lock" "$$" || return 1
  fi
  rm "$D/worker.lock/owner" && rmdir "$D/worker.lock" && sync
}
close_session() {
  [ "$(cat "$D/worker.lock/owner")" = "$MASTER" ] || return 1
  intent revoke_session "$D/session.closed" "$RUN" || return 1
  # Existence itself is a permanent fail-closed tombstone, even if the receipt write crashes.
  mkdir "$D/session.closed" || return 1
  sync || return 1
  printf '%s\n' "$TOKEN $BOOT $RUN" > "$D/session.closed/owner" || return 1
  sync || return 1
  printf 'SESSION_REVOKED %s %s %s\n' "$TOKEN" "$BOOT" "$RUN" > "$R/session_closed.txt" || return 1
  sync || return 1
}
verify_owned_children() {
  [ ! -e "$D/worker.lock" ] && [ ! -L "$D/worker.lock" ] || return 85
  for prior in "$D"/*; do
    [ -d "$prior" ] || continue
    [ "$prior" != "$D/session.closed" ] || continue
    R=$prior; quiescent || return 77
    [ "$MODE" = verify_gray_groups ] || input_natural_complete || return 77
  done
}
input_natural_complete() {
  [ "$GRAY_SCRIPT_ROUND" = 1 ] || return 0
  # A durable permit may have admitted input even if the start receipt is absent.
  [ -e "$R/permit-workload" ] || [ -L "$R/permit-workload" ] || return 0
  ledger_complete "$R/input-complete.json" || return 1
  # Generated receipt is a single canonical line. No JSON tool is required on Android.
  awk -v t="$TOKEN" -v b="$BOOT" -v r="${R##*/}" '
    BEGIN {prefix="{\"schema\":\"phonefarm/gray-input-complete@1\",\"token\":\"" t "\",\"boot_id\":\"" b "\",\"run\":\"" r "\","}
    NR!=1 || index($0,prefix)!=1 {bad=1}
    {rest=substr($0,length(prefix)+1); if(rest !~ /^"steps":[0-9]+,"declared_ms":[0-9]+,"start":[0-9]+([.][0-9]+)?,"end":[0-9]+([.][0-9]+)?\}$/) bad=1}
    END {exit (bad || NR!=1)}' "$R/input-complete.json"
}
valid_closed_owner() {
  [ -d "$D/session.closed" ] && [ ! -L "$D/session.closed" ] || return 1
  ledger_complete "$D/session.closed/owner" || return 1
  awk -v t="$TOKEN" -v b="$BOOT" 'NF!=3 || $1!=t || $2!=b || $3!~/^[a-zA-Z0-9_:-]+$/ {bad=1} END {exit (bad || NR!=1)}' "$D/session.closed/owner"
}
# Closed bench verification is read-only and cannot reacquire or clear a lock.
if [ "$MODE" = verify_bench ]; then
  valid_closed_owner || exit 86
  verify_owned_children || exit $?
  echo BENCH_CLOSED_QUIESCENT
  exit 0
fi
if [ "$MODE" = verify_gray_groups ]; then
  # Group absence permits independent charge restoration, never input completion.
  if [ -e "$D/session.closed" ] || [ -L "$D/session.closed" ]; then valid_closed_owner || exit 86; fi
  verify_owned_children || exit $?
  echo GRAY_GROUPS_QUIESCENT
  exit 0
fi
if [ "$MODE" = verify_gray_idle ]; then
  # Current group absence, not an earlier quiescence.txt, admits the next round.
  verify_owned_children || exit $?
  echo GRAY_IDLE_QUIESCENT
  exit 0
fi
# The host invokes this worker through setsid, so master and its synchronous children own one group.
acquire_master || exit 85
case "$MODE" in
  gray_script_sample|gray_control_sample)
    intent mkdir "$R" ''; mkdir "$R"
    printf '%s\n' "$MASTER" > "$R/master.tsv"
    gray_workload="$D/workload.sh"; gray_kind=gray_script_v1
    if [ "$MODE" = gray_control_sample ]; then gray_workload="$D/workload-$RUN.sh"; gray_kind=gray_control_v1; fi
    printf '%s\n' "$gray_kind" > "$R/round-kind.txt"; sync
    trap 'cleanup_failure' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    start_child trace "$D/trace.sh" "$gray_instance" "$gray_seconds" "$R" 6 "$gray_qualify"; trace_pid=$CHILD_PID
    n=0
    while [ ! -f "$R/trace-ready.txt" ]; do
      [ -d "/proc/$trace_pid" ] && [ "$n" -lt 200 ] || exit 76
      n=$((n+1)); sleep .1
    done
    [ "$(cat "$R/trace-ready.txt")" = TRACE_READY ] || exit 76
    start_child sampler "$D/sample_env.sh" "$((gray_seconds+10))" 2 "$R" "$TOKEN" "$BOOT"; sampler_pid=$CHILD_PID
    start_child workload "$gray_workload" "$R" "$TOKEN" "$BOOT"; workload_pid=$CHILD_PID
    if wait "$workload_pid"; then echo 0 > "$R/workload_exit.txt"; else code=$?; echo "$code" > "$R/workload_exit.txt"; fi
    if wait "$trace_pid"; then echo 0 > "$R/trace_exit.txt"; else code=$?; echo "$code" > "$R/trace_exit.txt"; fi
    if wait "$sampler_pid"; then echo 0 > "$R/sampler_exit.txt"; else code=$?; echo "$code" > "$R/sampler_exit.txt"; fi
    n=0; until quiescent; do [ "$n" -lt 50 ] || exit 76; n=$((n+1)); sleep .1; done
    [ "$CHILD_ROWS" = 3 ] || exit 76
    echo OWNED_GROUPS_ABSENT > "$R/quiescence.txt"; sync
    trap - EXIT INT TERM
    release_master
    # Missing natural input completion is data here, but blocks idle/recovery below.
    echo GRAY_SCRIPT_SAMPLE_COMPLETE
    ;;
  gray_sample)
    intent mkdir "$R" ''; mkdir "$R"
    printf '%s\n' "$MASTER" > "$R/master.tsv"; sync
    trap 'cleanup_failure' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    start_child sampler "$D/sample_env.sh" "$((gray_seconds+4))" 2; sampler_pid=$CHILD_PID
    start_child workload "$D/workload.sh" "$gray_instance" "$gray_seconds" "$R"; workload_pid=$CHILD_PID
    if wait "$workload_pid"; then echo 0 > "$R/workload_exit.txt"; else code=$?; echo "$code" > "$R/workload_exit.txt"; fi
    if wait "$sampler_pid"; then echo 0 > "$R/sampler_exit.txt"; else code=$?; echo "$code" > "$R/sampler_exit.txt"; fi
    quiescent || exit 76
    [ "$CHILD_ROWS" = 2 ] || exit 76
    echo OWNED_GROUPS_ABSENT > "$R/quiescence.txt"; sync
    trap - EXIT INT TERM
    release_master
    # Collection exit codes are data; only known child quiescence completes this mode.
    echo GRAY_SAMPLE_COMPLETE
    ;;
  bench)
    intent mkdir "$R" ''; mkdir "$R"
    printf '%s\n' "$MASTER" > "$R/master.tsv"; sync
    trap 'cleanup_failure' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    start_child sampler "$D/sample_env.sh"
    start_child workload "$D/workload.sh"; workload_pid=$CHILD_PID
    if wait "$workload_pid"; then echo 0 > "$R/workload_exit.txt"; else code=$?; echo "$code" > "$R/workload_exit.txt"; fi
    stop_owned || exit 76
    quiescent || exit 76
    echo OWNED_GROUPS_ABSENT > "$R/quiescence.txt"; sync
    trap - EXIT INT TERM
    release_master
    # Lifecycle completion is independent of the workload's recorded exit code.
    echo BENCH_COMPLETE
    ;;
  reconcile_bench)
    for prior in "$D"/*; do
      [ -d "$prior" ] || continue
      [ "$prior" != "$D/worker.lock" ] || continue
      R=$prior; stop_owned && input_natural_complete || exit 77
    done
    R="$D/$RUN"; intent mkdir "$R" ''; mkdir "$R"
    printf '%s\n' "$MASTER" > "$R/master.tsv"; sync
    echo OWNED_GROUPS_ABSENT > "$R/quiescence.txt"
    close_session
    release_master
    echo BENCH_RECONCILE_COMPLETE
    ;;
  render_readonly)
    intent mkdir "$R" ''; mkdir "$R"
    printf '%s\n' "$MASTER" > "$R/master.tsv"; sync
    trap 'cleanup_failure' EXIT INT TERM
    IFS="$(printf '\t')" read -r pkg comm < "$D/render_args.tsv" || exit 81
    start_child render_reader "$D/render_ownership.sh" "$pkg" "$comm" "$D/render_reader.elf"
    if wait "$CHILD_PID"; then echo 0 > "$R/render_exit.txt"; else code=$?; echo "$code" > "$R/render_exit.txt"; exit "$code"; fi
    quiescent || exit 76
    echo OWNED_GROUPS_ABSENT > "$R/quiescence.txt"
    trap - EXIT INT TERM
    release_master
    echo READONLY_COMPLETE
    ;;
  reconcile_render)
    for prior in "$D"/*; do
      [ -d "$prior" ] || continue
      [ "$prior" != "$D/worker.lock" ] || continue
      R=$prior; stop_owned || exit 77
      for pending in "$R"/launch-*.pending; do [ -e "$pending" ] || continue; intent clear_stopped_pending "$pending" ''; rm "$pending"; sync; done
    done
    R="$D/$RUN"; intent mkdir "$R" ''; mkdir "$R"
    printf '%s\n' "$MASTER" > "$R/master.tsv"; sync
    echo OWNED_GROUPS_ABSENT > "$R/quiescence.txt"
    close_session
    release_master
    echo READONLY_RECONCILE_COMPLETE
    ;;
  probe|control_probe)
    intent mkdir "$R" ''; mkdir "$R"
    trap 'cleanup_failure' EXIT INT TERM
    snapshot snapshot_before
    if [ -f "$D/thermal_sample.sh" ]; then
      if thermal_point=$(sh "$D/thermal_sample.sh"); then :; else thermal_code=$?; printf '%s\n' "$thermal_point" >> "$R/thermal_hold.raw.tsv"; exit "$thermal_code"; fi
      printf '%s\n' "$thermal_point" >> "$R/thermal_hold.raw.tsv"
      thermal_previous=$(thermal_point_uptime "$thermal_point") || exit 81
    fi
    apply_plan "$D/$RUN.plan.tsv"
    if [ -f "$D/thermal_sample.sh" ]; then thermal_hold_start=$(thermal_uptime) || exit 81; fi
    [ "$MODE" != control_probe ] || snapshot snapshot_at_run
    if [ -f "$D/thermal_sample.sh" ]; then
      thermal_wait_until "$(thermal_deadline "$thermal_hold_start" 1)" "$(thermal_deadline "$thermal_previous" 6)" "$thermal_previous" || exit 81
    else sleep 1; fi
    while IFS="$(printf '\t')" read -r key kind path value; do
      [ "$(read_value "$kind" "$path")" = "$value" ] || exit 74
    done < "$D/$RUN.plan.tsv"
    if [ -f "$D/thermal_sample.sh" ]; then
      if thermal_point=$(sh "$D/thermal_sample.sh"); then :; else thermal_code=$?; printf '%s\n' "$thermal_point" >> "$R/thermal_hold.raw.tsv"; exit "$thermal_code"; fi
      printf '%s\n' "$thermal_point" >> "$R/thermal_hold.raw.tsv"
      thermal_previous=$(thermal_point_uptime "$thermal_point") || exit 81
      for thermal_offset in 6 11; do
        thermal_wait_until "$(thermal_deadline "$thermal_hold_start" "$thermal_offset")" "$(thermal_deadline "$thermal_previous" 6)" "$thermal_previous" || exit 81
        if thermal_point=$(sh "$D/thermal_sample.sh"); then :; else thermal_code=$?; printf '%s\n' "$thermal_point" >> "$R/thermal_hold.raw.tsv"; exit "$thermal_code"; fi
        printf '%s\n' "$thermal_point" >> "$R/thermal_hold.raw.tsv"
        thermal_previous=$(thermal_point_uptime "$thermal_point") || exit 81
      done
    else sleep 10; fi
    while IFS="$(printf '\t')" read -r key kind path value; do
      [ "$(read_value "$kind" "$path")" = "$value" ] || exit 75
      printf '%s.writable=yes\n%s.effect=live\n' "$key" "$key" >> "$R/probe_receipt.txt"
      if [ "$path" = system:refresh_rate_mode ]; then
        fps=$(dumpsys display | grep -m1 'mActiveSfDisplayMode=' | grep -oE 'peakRefreshRate=[0-9]+' | head -1 | cut -d= -f2 || true)
        printf 'setting.system.refresh_rate_mode.mode%s_fps=%s (readback=%s)\n' "$value" "$fps" "$(read_value "$kind" "$path")" >> "$R/probe_receipt.txt"
      fi
    done < "$D/$RUN.plan.tsv"
    [ "$MODE" != control_probe ] || snapshot snapshot_after_run
    if [ -f "$D/thermal-purpose.txt" ]; then
      thermal_hold_end=$(thermal_uptime) || exit 81
      printf '#phonefarm_control_hold_complete start=%s end=%s samples=4 profile=capability_absolute_v1\n' "$thermal_hold_start" "$thermal_hold_end" >> "$R/thermal_hold.raw.tsv"
    fi
    restore_controls
    [ "$MODE" = control_probe ] || restore_trace
    snapshot snapshot_restored
    while IFS="$(printf '\t')" read -r key kind path value; do printf '%s.effect_restored=yes\n' "$key" >> "$R/probe_receipt.txt"; done < "$D/$RUN.plan.tsv"
    trap - EXIT INT TERM
    release_master
    echo PROBE_COMPLETE
    ;;
  sample)
    dur=${6:?capture seconds}; lead=${7:?lead}; envdur=${8:?ENV seconds}; role=${9:?role}
    intent mkdir "$R" ''; mkdir "$R"
    trap 'cleanup_failure' EXIT INT TERM
    id > "$R/root_id.txt"; cat /proc/sys/kernel/random/boot_id > "$R/boot_id.txt"
    snapshot snapshot_before
    [ "$role" = baseline ] || apply_plan "$D/candidate.plan.tsv"
    snapshot snapshot_at_run
    if [ -f "$D/render_ownership.sh" ]; then render_prepare || exit 81; fi
    thermal_pid=''
    if [ -f "$D/thermal_sampler.sh" ]; then
      start_child thermal_sampler "$D/thermal_sampler.sh" "$((envdur+3))" 1 "$R/thermal.ready"; thermal_pid=$CHILD_PID
      ready_wait=0
      while [ ! -s "$R/thermal.ready" ]; do
        [ "$ready_wait" -lt 100 ] || exit 81
        kill -0 "$thermal_pid" || exit 81
        ready_wait=$((ready_wait+1)); sleep .1
      done
    fi
    start_child workload "$D/workload.sh"; workload_pid=$CHILD_PID
    start_child sampler "$D/sample_env.sh" "$envdur" 1; sampler_pid=$CHILD_PID
    if [ ! -f "$D/render_ownership.sh" ]; then sleep "$lead"; fi
    if capture "$dur" > "$R/capture.log" 2> "$R/capture.stderr"; then echo 0 > "$R/capture_exit.txt"; else code=$?; echo "$code" > "$R/capture_exit.txt"; exit "$code"; fi
    if wait "$workload_pid"; then echo 0 > "$R/workload_exit.txt"; else code=$?; echo "$code" > "$R/workload_exit.txt"; exit "$code"; fi
    if wait "$sampler_pid"; then echo 0 > "$R/sampler_exit.txt"; else code=$?; echo "$code" > "$R/sampler_exit.txt"; exit "$code"; fi
    if [ -n "$thermal_pid" ]; then
      if wait "$thermal_pid"; then echo 0 > "$R/thermal_sampler_exit.txt"; else code=$?; echo "$code" > "$R/thermal_sampler_exit.txt"; exit "$code"; fi
    fi
    quiescent || exit 76
    echo OWNED_GROUPS_ABSENT > "$R/quiescence.txt"
    restore_trace; snapshot snapshot_after_run
    restore_controls; snapshot snapshot_restored
    echo 0 > "$R/restore_exit.txt"
    trap - EXIT INT TERM
    release_master
    echo SAMPLE_COMPLETE
    ;;
  reconcile|reconcile_controls)
    # Every old run owns a separate process ledger. Unknown identities are never killed.
    for prior in "$D"/*; do
      [ -d "$prior" ] || continue
      [ "$prior" != "$D/worker.lock" ] || continue
      R=$prior; stop_owned || exit 77
    done
    [ "$MODE" = reconcile_controls ] || restore_trace
    restore_controls
    R="$D/$RUN"; intent mkdir "$R" ''; mkdir "$R"; snapshot snapshot_restored
    echo 0 > "$R/restore_exit.txt"
    echo OWNED_GROUPS_ABSENT > "$R/quiescence.txt"
    close_session
    release_master
    echo RECONCILE_COMPLETE
    ;;
  *) exit 70 ;;
esac
