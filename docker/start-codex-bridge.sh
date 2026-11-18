#!/usr/bin/env bash
# Run as the host user whose Codex CLI is signed in with ChatGPT.
set -Eeuo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
bridge_python="${LOCAL_CODEX_PYTHON:-$project_root/.venv/bin/python}"
bridge_token_file="${LOCAL_CODEX_TOKEN_FILE:-$project_root/docker/.codex-bridge-token}"
if [[ ! -f "$bridge_token_file" ]]; then
  (umask 077; "$bridge_python" -c 'import secrets; print(secrets.token_urlsafe(48))' > "$bridge_token_file")
fi
export PYTHONPATH="$project_root/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$bridge_python" -m llm4ad.frontend.codex_bridge --token-file "$bridge_token_file" "$@"
