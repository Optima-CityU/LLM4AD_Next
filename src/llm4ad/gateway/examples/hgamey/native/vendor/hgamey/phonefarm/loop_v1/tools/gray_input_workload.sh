#!/system/bin/sh
# The host replaces only the two marked lines with validated numeric constants/actions.
set -eu
R=${1:?round}; TOKEN=${2:?token}; BOOT=${3:?boot}
D=${R%/*}
# GRAY_PLAN_CONSTANTS
. "$D/gray_child_guard.sh"
gray_child_begin workload "$GRAY_BUDGET"
gray_input_exit() {
  code=$1; trap - EXIT INT TERM
  gray_child_finish "$code" || exit 75
  if [ "$code" = 0 ]; then
    printf '{"schema":"phonefarm/gray-input-complete@1","token":"%s","boot_id":"%s","run":"%s","steps":%s,"declared_ms":%s,"start":%s,"end":%s}\n' "$TOKEN" "$BOOT" "${R##*/}" "$STEPS" "$DECLARED_MS" "$GRAY_START" "$GRAY_END" > "$R/input-complete.json.tmp" && sync && mv "$R/input-complete.json.tmp" "$R/input-complete.json" && sync || exit 75
  fi
  exit "$code"
}
trap 'gray_input_exit $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# GRAY_PLAN_ACTIONS
