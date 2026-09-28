#!/bin/sh
set -eu

mkdir -p "$HOME" "$LLM4AD_WORKSPACE_ROOT"
exec node /app/cloudcli/dist-server/server/index.js
