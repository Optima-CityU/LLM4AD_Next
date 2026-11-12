#!/system/bin/sh
# Metadata only. Differential writes belong to the owned confirmation worker.
set -eu
KGSL=/sys/class/kgsl/kgsl-3d0
CPU=/sys/devices/system/cpu/cpufreq
BUS=/sys/devices/system/cpu/bus_dcvs
rd() { printf '%s=%s\n' "$1" "$(cat "$2" 2>/dev/null || true)"; }
echo '# probe_sysparam v1'
pols=''
for p in "$CPU"/policy*; do
  [ -d "$p" ] || continue
  n=${p##*/policy}; pols="$pols$n,"
done
printf 'cpu.policies=%s\n' "${pols%,}"
for p in "$CPU"/policy*; do
  [ -d "$p" ] || continue
  n=${p##*/policy}; k="cpu.policy$n"
  rd "$k.avail_freqs" "$p/scaling_available_frequencies"
  rd "$k.avail_governors" "$p/scaling_available_governors"
  rd "$k.scaling_min_freq.cur" "$p/scaling_min_freq"
  rd "$k.scaling_max_freq.cur" "$p/scaling_max_freq"
  rd "$k.scaling_governor.cur" "$p/scaling_governor"
done
rd gpu.num_pwrlevels "$KGSL/num_pwrlevels"
rd gpu.min_pwrlevel.cur "$KGSL/min_pwrlevel"
rd gpu.max_pwrlevel.cur "$KGSL/max_pwrlevel"
devfreq=''
for d in "$KGSL/devfreq" /sys/class/devfreq/*kgsl-3d0 /sys/class/devfreq/*kgsl*; do
  [ -d "$d" ] || continue
  devfreq=$d; break
done
printf 'gpu.devfreq_path=%s\n' "$devfreq"
if [ -n "$devfreq" ]; then
  rd gpu.devfreq.avail_freqs "$devfreq/available_frequencies"
  rd gpu.devfreq.avail_governors "$devfreq/available_governors"
  rd gpu.devfreq.min_freq.cur "$devfreq/min_freq"
  rd gpu.devfreq.max_freq.cur "$devfreq/max_freq"
  rd gpu.devfreq.governor.cur "$devfreq/governor"
fi
for n in DDR LLCC; do
  rd "bus.$n.boost_freq.cur" "$BUS/$n/boost_freq"
  rd "bus.$n.hw_min_freq" "$BUS/$n/hw_min_freq"
  rd "bus.$n.hw_max_freq" "$BUS/$n/hw_max_freq"
  rd "bus.$n.avail_freqs" "$BUS/$n/available_frequencies"
done
for key in peak_refresh_rate min_refresh_rate refresh_rate_mode all_refresh_rate; do
  value=$(settings get system "$key")
  printf 'setting.system.%s.cur=%s\nsetting.system.%s.read_exit=0\n' "$key" "$value" "$key"
done
printf 'setting.system.all_refresh_rate=%s\n' "$(settings get system all_refresh_rate)"
printf 'display.modes=%s\n' "$(dumpsys display | grep -oE 'fps=[0-9]+(\.[0-9]+)?|[0-9]+\.[0-9]+ fps' | sort -u | tr '\n' ',' | sed 's/,$//' || true)"
printf 'setting.system.refresh_rate_mode.base_fps=%s\n' "$(dumpsys display | grep -m1 'mActiveSfDisplayMode=' | grep -oE 'peakRefreshRate=[0-9]+' | head -1 | cut -d= -f2 || true)"
echo '# end'
