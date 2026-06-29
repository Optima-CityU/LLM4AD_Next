#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  printf '%s\n' \
    'Usage: TAG=v1.0.0 ./start.sh [start|stop|remove|upgrade] [--mirrors REGISTRY] [--debug] [--dry-run]' \
    '' \
    'Manage the image-based deployment.' \
    '' \
    'Commands:' \
    '  start    Pull required images, then start services. Default command.' \
    '  stop     Stop compose services and backend runtime containers.' \
    '  remove   Remove compose services and backend runtime containers.' \
    '  upgrade  Pull required images, stop runtime containers, then recreate services.' \
    '' \
    'Options:' \
    '  --mirrors REGISTRY  Use a specific image registry namespace, for example docker.io/noah2012' \
    '  --debug    Include compose.deploy.debug.yml and the debug profile.' \
    '  --dry-run  Print commands without running them.' \
    '  -h, --help Show this help.' \
    '' \
    'Environment variables:' \
    '  TAG                           Image tag to deploy, default: VERSION from docker/version' \
    '  SWR_REGISTRY                  Image registry namespace, default: DOCKER_REGISTRY from docker/version' \
    '  VERSION_FILE                  Version config file, default: version' \
    '  COMPOSE_VERSION               Docker Compose version to auto-install, default: v2.32.4' \
    '  EXTRA_BACKEND_RUNTIME_IMAGES  Extra whitespace-separated images to pull'
}

COMMAND="start"
DEBUG=0
DRY_RUN=0
MIRRORS=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    start|stop|remove|upgrade)
      COMMAND="$1"
      shift
      ;;
    --debug)
      DEBUG=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --mirrors)
      if [[ $# -lt 2 || "${2:0:1}" == "-" ]]; then
        printf 'Missing value for --mirrors\n' >&2
        usage >&2
        exit 2
      fi
      MIRRORS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="${BASH_SOURCE[0]}"
if [[ "$SCRIPT_DIR" == */* ]]; then
  SCRIPT_DIR="${SCRIPT_DIR%/*}"
else
  SCRIPT_DIR="."
fi
cd "$SCRIPT_DIR"

load_version_file() {
  local version_file="${VERSION_FILE:-version}"

  [[ -f "$version_file" ]] || return 0
  # shellcheck disable=SC1090
  . "$version_file"
}

load_version_file
TAG="${TAG:-${VERSION:-latest}}"
SWR_REGISTRY="${MIRRORS:-${SWR_REGISTRY:-${DOCKER_REGISTRY:-docker.io/noah2012}}}"
SWR_REGISTRY="${SWR_REGISTRY%/}"
export TAG
export SWR_REGISTRY

COMPOSE_ARGS=(-f compose.yml -f compose.swr.yml)
if [[ "$DEBUG" -eq 1 ]]; then
  COMPOSE_ARGS+=(-f compose.deploy.debug.yml --profile debug)
fi

RUNTIME_IMAGES=()
DYNAMIC_CONTAINER_PREFIXES=()
COMPOSE_CMD=()
COMPOSE_INSTALL_URL=""
COMPOSE_LAST_ERROR=""

add_runtime_image() {
  local image="$1"
  local existing

  [[ -n "$image" ]] || return 0
  if [[ "${#RUNTIME_IMAGES[@]}" -gt 0 ]]; then
    for existing in "${RUNTIME_IMAGES[@]}"; do
      [[ "$existing" == "$image" ]] && return 0
    done
  fi
  RUNTIME_IMAGES+=("$image")
}

add_dynamic_container_prefix() {
  local prefix="$1"
  local existing

  [[ -n "$prefix" ]] || return 0
  if [[ "${#DYNAMIC_CONTAINER_PREFIXES[@]}" -gt 0 ]]; then
    for existing in "${DYNAMIC_CONTAINER_PREFIXES[@]}"; do
      [[ "$existing" == "$prefix" ]] && return 0
    done
  fi
  DYNAMIC_CONTAINER_PREFIXES+=("$prefix")
}

discover_backend_runtime_images() {
  local constants_file="../src/backend/app/core/constants.py"
  local line image prefix

  [[ -f "$constants_file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^[[:space:]]*[A-Z0-9_]*IMAGE[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
      image="${BASH_REMATCH[1]}"
      add_runtime_image "$image"
    elif [[ "$line" =~ ^[[:space:]]*[A-Z0-9_]*IMAGE[[:space:]]*=[[:space:]]*\'([^\']+)\' ]]; then
      image="${BASH_REMATCH[1]}"
      add_runtime_image "$image"
    elif [[ "$line" =~ ^[[:space:]]*[A-Z0-9_]*CONTAINER_NAME_PREFIX[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
      prefix="${BASH_REMATCH[1]}"
      add_dynamic_container_prefix "$prefix"
    elif [[ "$line" =~ ^[[:space:]]*[A-Z0-9_]*CONTAINER_NAME_PREFIX[[:space:]]*=[[:space:]]*\'([^\']+)\' ]]; then
      prefix="${BASH_REMATCH[1]}"
      add_dynamic_container_prefix "$prefix"
    fi
  done < "$constants_file"
  add_dynamic_container_prefix "code_user-"
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

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

print_compose_install_hint() {
  local url="${COMPOSE_INSTALL_URL:-}"

  printf '\nDocker Compose v2 is required but was not found or is incompatible with this project.\n' >&2
  if [[ -n "$COMPOSE_LAST_ERROR" ]]; then
    printf '\nLast compatibility error:\n%s\n' "$COMPOSE_LAST_ERROR" >&2
  fi
  if [[ -n "$url" ]]; then
    printf 'Automatic download failed. Install it manually:\n' >&2
    printf '  mkdir -p ~/.docker/cli-plugins\n' >&2
    printf '  curl -fL %q -o ~/.docker/cli-plugins/docker-compose\n' "$url" >&2
    printf '  chmod +x ~/.docker/cli-plugins/docker-compose\n' >&2
    printf '  docker compose version\n' >&2
  else
    printf 'Install Docker Compose v2 manually, then rerun this script.\n' >&2
  fi
}

install_docker_compose_plugin() {
  local version="${COMPOSE_VERSION:-v2.32.4}"
  local os arch plugin_dir plugin_path

  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  case "$os" in
    linux)
      ;;
    *)
      printf 'Unsupported OS for automatic Docker Compose install: %s\n' "$os" >&2
      return 1
      ;;
  esac

  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64)
      arch="x86_64"
      ;;
    aarch64|arm64)
      arch="aarch64"
      ;;
    *)
      printf 'Unsupported architecture for automatic Docker Compose install: %s\n' "$arch" >&2
      return 1
      ;;
  esac

  if [[ -z "${HOME:-}" ]]; then
    printf 'HOME is not set; cannot choose Docker CLI plugin directory.\n' >&2
    return 1
  fi

  plugin_dir="${DOCKER_CONFIG:-$HOME/.docker}/cli-plugins"
  plugin_path="$plugin_dir/docker-compose"
  COMPOSE_INSTALL_URL="https://github.com/docker/compose/releases/download/${version}/docker-compose-${os}-${arch}"

  printf 'Docker Compose v2 is missing or incompatible. Installing %s to %s...\n' "$version" "$plugin_path" >&2
  run mkdir -p "$plugin_dir" || return 1
  if command -v curl >/dev/null 2>&1; then
    run curl -fL "$COMPOSE_INSTALL_URL" -o "$plugin_path" || return 1
  elif command -v wget >/dev/null 2>&1; then
    run wget -O "$plugin_path" "$COMPOSE_INSTALL_URL" || return 1
  else
    printf 'Neither curl nor wget is available for automatic download.\n' >&2
    return 1
  fi
  run chmod +x "$plugin_path" || return 1
}

compose_command_supports_project() {
  local output command_display

  if output="$("$@" "${COMPOSE_ARGS[@]}" config -q 2>&1)"; then
    return 0
  fi

  printf -v command_display '%q ' "$@"
  command_display="${command_display% }"
  COMPOSE_LAST_ERROR="Command: ${command_display}
${output}"
  printf 'Skipping incompatible Docker Compose command: %s\n' "$command_display" >&2
  return 1
}

select_compose_command() {
  if "$@" version >/dev/null 2>&1 && compose_command_supports_project "$@"; then
    COMPOSE_CMD=("$@")
    return 0
  fi
  return 1
}

resolve_compose_command() {
  if ! command -v docker >/dev/null 2>&1; then
    die 'Docker CLI is not installed or not in PATH. Please install Docker first.'
  fi

  if select_compose_command docker compose; then
    return 0
  fi

  if command -v docker-compose >/dev/null 2>&1 && select_compose_command docker-compose; then
    return 0
  fi

  if install_docker_compose_plugin; then
    if [[ "$DRY_RUN" -eq 1 ]] || select_compose_command docker compose; then
      COMPOSE_CMD=(docker compose)
      return 0
    fi
  fi

  print_compose_install_hint
  exit 1
}

show_compose_service_images() {
  local output

  if [[ "$DRY_RUN" -eq 1 ]]; then
    run "${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" config --images
    return 0
  fi

  if output="$("${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" config --images 2>&1)"; then
    printf '%s\n' "$output"
    return 0
  fi

  printf 'Current Docker Compose does not support "config --images"; validating compose files instead.\n'
  "${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" config -q
}

pull_required_images() {
  printf 'Image tag: %s\n' "${TAG:-latest}"
  printf 'Compose service images used by this deployment:\n'
  show_compose_service_images

  printf '\nPulling compose service images...\n'
  run "${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" pull

  if [[ "${#RUNTIME_IMAGES[@]}" -gt 0 ]]; then
    printf '\nPulling backend runtime images...\n'
    for image in "${RUNTIME_IMAGES[@]}"; do
      run docker pull "$image"
    done
  fi
}

dynamic_container_ids() {
  local prefix="$1"
  docker ps -aq --filter "name=^/${prefix}"
}

running_dynamic_container_ids() {
  local prefix="$1"
  docker ps -q --filter "name=^/${prefix}"
}

stop_dynamic_containers() {
  local prefix ids=()

  if [[ "${#DYNAMIC_CONTAINER_PREFIXES[@]}" -eq 0 ]]; then
    return 0
  fi

  printf '\nStopping backend runtime containers...\n'
  for prefix in "${DYNAMIC_CONTAINER_PREFIXES[@]}"; do
    if [[ "$DRY_RUN" -eq 1 ]]; then
      run docker stop "\$(docker ps -q --filter name=^/${prefix})"
      continue
    fi

    mapfile -t ids < <(running_dynamic_container_ids "$prefix")
    if [[ "${#ids[@]}" -gt 0 ]]; then
      run docker stop "${ids[@]}"
    fi
  done
}

remove_dynamic_containers() {
  local prefix ids=()

  if [[ "${#DYNAMIC_CONTAINER_PREFIXES[@]}" -eq 0 ]]; then
    return 0
  fi

  printf '\nRemoving backend runtime containers...\n'
  for prefix in "${DYNAMIC_CONTAINER_PREFIXES[@]}"; do
    if [[ "$DRY_RUN" -eq 1 ]]; then
      run docker rm -f "\$(docker ps -aq --filter name=^/${prefix})"
      continue
    fi

    mapfile -t ids < <(dynamic_container_ids "$prefix")
    if [[ "${#ids[@]}" -gt 0 ]]; then
      run docker rm -f "${ids[@]}"
    fi
  done
}

start_services() {
  pull_required_images
  printf '\nStarting services...\n'
  run "${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" up -d
}

stop_services() {
  printf '\nStopping compose services...\n'
  run "${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" stop
  stop_dynamic_containers
}

remove_services() {
  printf '\nRemoving compose services...\n'
  run "${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" down --remove-orphans
  remove_dynamic_containers
}

upgrade_services() {
  pull_required_images
  printf '\nStopping compose services...\n'
  run "${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" stop
  stop_dynamic_containers
  printf '\nRecreating services...\n'
  run "${COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" up -d --remove-orphans
}

discover_backend_runtime_images
resolve_compose_command
if [[ -n "${EXTRA_BACKEND_RUNTIME_IMAGES:-}" ]]; then
  for image in ${EXTRA_BACKEND_RUNTIME_IMAGES}; do
    add_runtime_image "$image"
  done
fi

case "$COMMAND" in
  start)
    start_services
    ;;
  stop)
    stop_services
    ;;
  remove)
    remove_services
    ;;
  upgrade)
    upgrade_services
    ;;
esac
