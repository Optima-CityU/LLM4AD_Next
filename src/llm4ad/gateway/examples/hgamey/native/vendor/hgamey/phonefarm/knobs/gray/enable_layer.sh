#!/bin/bash
# Legacy installation is retired. Diagnostics and known-target deactivation
# delegate to phonefarm; this wrapper never invokes adb or installs a layer.
set -euo pipefail

usage() {
  cat <<'HELP'
Usage: enable_layer.sh {status|off [PACKAGE]} --serial SERIAL [--json]
PHONEFARM_BIN selects the executable (default: phonefarm).
An explicitly set REFBENCH_SERIAL supplies the default serial; --serial overrides it.
Legacy probe/loadop/passdump/frameaudit/frameaudit-ts/target/framedump/interp are retired.
Use: phonefarm gray-layer session --mode MODE --package PKG --serial S --out DIR --max-seconds N
The old --keep-prop installed without launching; the new session always launches.
copyprobe/upop remain unsupported, including custom artifacts.
HELP
}
fail() { echo "$*" >&2; exit 2; }

serial="${REFBENCH_SERIAL:-}"
custom=false
keep=false
json=false
help=false
positional=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --serial|--so)
      [ "$#" -ge 2 ] || fail "$1 requires a value"
      if [ "$1" = --serial ]; then serial="$2"; else custom=true; fi
      shift 2 ;;
    --keep-prop) keep=true; shift ;;
    --json) json=true; shift ;;
    --help|-h) help=true; shift ;;
    -*) fail "unknown option: $1" ;;
    *) positional+=("$1"); shift ;;
  esac
done
[ "${#positional[@]}" -le 2 ] || fail 'expected one mode and at most one package'
mode="${positional[0]:-}"
case "$mode" in
  probe|loadop|passdump|frameaudit|frameaudit-ts|target|framedump|interp|copyprobe|upop|status|off|'') ;;
  *) fail "unknown mode: $mode" ;;
esac
if [ "$help" = true ]; then usage; exit 0; fi
[ -n "$mode" ] || { usage >&2; exit 2; }
[ "$mode" != target ] || [ "${#positional[@]}" -eq 2 ] || fail 'target requires a package'
[ "$mode" != status ] || [ "${#positional[@]}" -eq 1 ] || fail 'status does not accept a package'
case "$mode" in
  # framedump/interp (layer/framedump.cpp, layer/interp.cpp) were install-and-exit modes too;
  # they stay in the layer but need a persistent Session owner before they can be mounted again.
  probe|loadop|passdump|frameaudit|frameaudit-ts|target|framedump|interp)
    printf '{"schema":"phonefarm/gray-legacy-mount-retirement@1","requested_mode":"%s","status":"retired","reason":"persistent_owner_required","execution_started":false,"device_commands_started":false,"replacement":"phonefarm gray-layer session --mode MODE --package PKG --serial S --out DIR --max-seconds N","keep_prop_compatibility":"legacy_install_without_launch_is_not_supported"}\n' "$mode"
    exit 2 ;;
  copyprobe|upop)
    source=default
    reason=missing_source_layout_target_compute_contract
    if [ "$custom" = true ]; then source=custom; reason=artifact_capability_unverified; fi
    printf '{"schema":"phonefarm/gray-mode-admission@1","requested_mode":"%s","artifact_source":"%s","status":"unsupported","reason":"%s","execution_started":false,"device_commands_started":false,"collection_ok":false,"recovery_status":"not_started","job_recovery_complete":false,"game_state_restored":false}\n' "$mode" "$source" "$reason"
    exit 2 ;;
esac
[ "$custom" = false ] && [ "$keep" = false ] || fail '--so and --keep-prop apply only to retired mount modes'
[ -n "$serial" ] || fail 'status/off requires --serial or explicit REFBENCH_SERIAL'
args=(gray-layer "$mode")
if [ "${#positional[@]}" -eq 2 ]; then args+=("${positional[1]}"); fi
args+=(--serial "$serial")
if [ "$json" = true ]; then args+=(--json); fi
exec "${PHONEFARM_BIN:-phonefarm}" "${args[@]}"
