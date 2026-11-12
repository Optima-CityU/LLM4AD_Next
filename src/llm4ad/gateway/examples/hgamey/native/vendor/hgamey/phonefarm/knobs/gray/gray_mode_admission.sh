#!/bin/bash
# These legacy shell entries select the fixed build/out artifact, not a custom SO.
# OUT in their callers is an evidence directory and does not declare capability.
gray_mode_admission() {
  case "${1:-}" in
    copyprobe|upop)
      printf '{"schema":"phonefarm/gray-mode-admission@1","requested_mode":"%s","artifact_source":"default","status":"unsupported","reason":"missing_source_layout_target_compute_contract","execution_started":false,"device_commands_started":false,"collection_ok":false,"recovery_status":"not_started","job_recovery_complete":false,"game_state_restored":false}\n' "$1"
      return 2 ;;
    *) return 0 ;;
  esac
}
