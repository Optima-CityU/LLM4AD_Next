#!/bin/bash
# Existing-owner quality client. See --help; this entry never installs a layer.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "${1:-}" = begin ]; then
  . "$HERE/gray_mode_admission.sh"
  gray_mode_admission "${2:-}" || exit $?
fi
exec python3 "$HERE/genshin_quality.py" "$@"
