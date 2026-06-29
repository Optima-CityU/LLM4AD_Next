#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_contains() {
  local file="$1"
  local pattern="$2"
  local message="$3"
  grep -Eq "$pattern" "$file" || fail "$message"
}

assert_not_contains() {
  local file="$1"
  local pattern="$2"
  local message="$3"
  if grep -Eq "$pattern" "$file"; then
    fail "$message"
  fi
}

assert_contains nginx.conf '/flag/UPSTREAM_DOWN' \
  'nginx must use the automatic upstream-down flag'
assert_not_contains nginx.conf '/flag/MAINTENANCE' \
  'nginx must not depend on the manual maintenance flag'
assert_contains nginx.conf 'proxy_intercept_errors[[:space:]]+on;' \
  'nginx must intercept upstream error pages'
assert_contains nginx.conf 'error_page[[:space:]]+500[[:space:]]+502[[:space:]]+503[[:space:]]+504[[:space:]]+=[[:space:]]+@maintenance;' \
  'nginx must let browser maintenance pages return the static file status'
assert_contains nginx.conf 'if \(\$request_method !~ \^\(GET\|HEAD\)\$\)' \
  'non-GET/HEAD requests must still return 503 during maintenance'
assert_contains maintenance.html 'headers\.get\("x-maintenance"\)' \
  'maintenance page polling must check the X-Maintenance header'
assert_not_contains maintenance.html 'res\.status && res\.status < 500' \
  'maintenance page polling must not treat 200 + X-Maintenance as recovered'

assert_contains compose.yml '^[[:space:]]+monitor:' \
  'compose must define a monitor sidecar'
assert_contains compose.yml 'http://host\.docker\.internal:18039/' \
  'monitor must probe the frontend homepage by default'
assert_contains compose.yml 'UPSTREAM_DOWN' \
  'monitor must write the automatic upstream-down flag'
assert_contains .gitignore '^/flag/\*$' \
  'runtime flag files must be ignored'
assert_contains .gitignore '^!/flag/\.gitkeep$' \
  'flag/.gitkeep must remain trackable'

printf 'maintenance-gateway config checks passed\n'
