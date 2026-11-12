#!/system/bin/sh
# Read-only typed inventory. Receiver selects supported sensors; no thermal writes.
set -eu
printf '#phonefarm_thermal_metadata v1\n'
printf 'BOOT\t%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
for z in /sys/class/thermal/thermal_zone*; do
  [ -d "$z" ] || continue
  ty=$(cat "$z/type")
  case "$ty" in
    cpu-*|cpullc*) domain=cpu ;;
    gpuss*) domain=gpu ;;
    skin*) domain=skin ;;
    shell*) printf 'EXCLUDED\t%s\t%s\tno_passive_trip_contract\n' "$z/temp" "$ty"; continue ;;
    *) continue ;;
  esac
  printf 'SENSOR\t%s\t%s\t%s\tmilli_celsius\n' "$z/temp" "$ty" "$domain"
  for f in "$z"/trip_point_*_type; do
    [ -f "$f" ] || continue
    kind=$(cat "$f"); raw=$(cat "${f%_type}_temp")
    printf 'TRIP\t%s\t%s\t%s\n' "$z/temp" "$kind" "$raw"
  done
done
printf 'SENSOR\t/sys/class/power_supply/battery/temp\tbattery\tbattery\tdeci_celsius\n'
printf '#end\n'
