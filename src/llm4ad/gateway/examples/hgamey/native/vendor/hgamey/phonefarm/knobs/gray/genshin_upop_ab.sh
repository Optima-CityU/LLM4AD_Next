#!/bin/bash
# Unsupported upop A/B stays rejected; REF_ARM=1 connects an existing owner.
# Reference no longer installs/restarts/stops a game. See REF_ARM=1 ... --help.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "${REF_ARM:-0}" != "1" ]; then
  . "$HERE/gray_mode_admission.sh"
  gray_mode_admission upop || exit $?
fi
exec python3 "$HERE/genshin_reference.py" "$@"
