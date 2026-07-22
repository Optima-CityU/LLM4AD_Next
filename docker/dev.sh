#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  printf '%s\n' \
    'Usage: ./dev.sh [infra|full|stop|remove|logs|ps] [--dry-run] [service...]' \
    '' \
    'Local Docker helper for contributors.' \
    '' \
    'Commands:' \
    '  infra    Start local infrastructure only for host-run backend/frontend.' \
    '  full     Build and start the full stack from local source with debug ports.' \
    '  stop     Stop containers created by either local mode.' \
    '  remove   Remove containers created by either local mode; keeps bind-mounted data.' \
    '  logs     Follow compose logs. Optional service names are accepted.' \
    '  ps       Show compose service status.' \
    '' \
    'Options:' \
    '  --dry-run  Print commands without running them.' \
    '  -h, --help Show this help.' \
    '' \
    'Before first use:' \
    '  cp .env.develop.local.example .env'
}

COMMAND="${1:-infra}"
if [[ $# -gt 0 ]]; then
  shift
fi

DRY_RUN=0
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ "$COMMAND" == "-h" || "$COMMAND" == "--help" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR="${BASH_SOURCE[0]}"
if [[ "$SCRIPT_DIR" == */* ]]; then
  SCRIPT_DIR="${SCRIPT_DIR%/*}"
else
  SCRIPT_DIR="."
fi
cd "$SCRIPT_DIR"

require_env_file() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi

  if [[ ! -f .env ]]; then
    printf 'Missing docker/.env. Create it first:\n\n' >&2
    printf '  cd docker\n  cp .env.develop.local.example .env\n\n' >&2
    printf 'Then edit required secrets and local paths before starting Docker services.\n' >&2
    exit 2
  fi
}

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

LOCAL_COMPOSE=(-f compose.yml -f compose.override.yml --profile debug)
FULL_COMPOSE=(-f compose.yml -f compose.mindmemos.yml -f compose.mindmemos.debug.yml -f compose.deploy.debug.yml --profile debug)
INFRA_SERVICES=(db redis rustfs adminer mailcatcher code_server_proxy task-runner)

case "$COMMAND" in
  infra)
    require_env_file
    run docker compose "${LOCAL_COMPOSE[@]}" up -d --build "${INFRA_SERVICES[@]}"
    ;;
  full)
    require_env_file
    run docker compose "${FULL_COMPOSE[@]}" up -d --build
    ;;
  stop)
    require_env_file
    run docker compose "${FULL_COMPOSE[@]}" stop
    run docker compose "${LOCAL_COMPOSE[@]}" stop
    ;;
  remove)
    require_env_file
    run docker compose "${FULL_COMPOSE[@]}" down --remove-orphans
    run docker compose "${LOCAL_COMPOSE[@]}" down --remove-orphans
    ;;
  logs)
    require_env_file
    run docker compose "${FULL_COMPOSE[@]}" logs -f "${ARGS[@]}"
    ;;
  ps)
    require_env_file
    run docker compose "${FULL_COMPOSE[@]}" ps
    ;;
  *)
    printf 'Unknown command: %s\n\n' "$COMMAND" >&2
    usage >&2
    exit 2
    ;;
esac
