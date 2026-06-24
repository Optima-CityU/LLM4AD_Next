#!/usr/bin/env bash
# 维护页开关（在 docker/maintenance-gateway/ 目录下运行）。
#
#   ./maint.sh up      启动维护网关容器（首次部署 / 开机后）
#   ./maint.sh on      开启维护页（部署前执行）
#   ./maint.sh off     关闭维护页（部署完成后执行）
#   ./maint.sh status  查看当前状态
#   ./maint.sh down    停止并移除维护网关容器
#
# 原理：on 即创建标志文件 flag/MAINTENANCE，容器内 nginx 检测到后对所有请求
# 返回维护页（见 nginx.conf）；off 即删除。flag 为 bind 挂载，宿主直接读写。
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
FLAG="flag/MAINTENANCE"
COMPOSE=(docker compose -f compose.yml)

reload_nginx() {
  # 容器在线才 reload；不在线时标志文件已就绪，下次启动即生效。
  if "${COMPOSE[@]}" ps --status running edge | grep -q edge; then
    "${COMPOSE[@]}" exec edge nginx -s reload
  fi
}

case "${1:-}" in
  up)
    "${COMPOSE[@]}" up -d
    printf '维护网关已启动（占用宿主 80 端口）\n'
    ;;
  down)
    "${COMPOSE[@]}" down
    printf '维护网关已停止\n'
    ;;
  on)
    mkdir -p flag
    touch "$FLAG"
    reload_nginx
    printf '维护页已开启\n'
    ;;
  off)
    rm -f "$FLAG"
    reload_nginx
    printf '维护页已关闭\n'
    ;;
  status)
    if [[ -f "$FLAG" ]]; then
      printf '维护开关：维护中\n'
    else
      printf '维护开关：正常服务\n'
    fi
    "${COMPOSE[@]}" ps edge 2>/dev/null || true
    ;;
  *)
    printf 'Usage: %s [up|on|off|status|down]\n' "${0##*/}" >&2
    exit 2
    ;;
esac
