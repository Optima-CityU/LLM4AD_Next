#!/bin/sh
set -eu
mkdir -p /data/jobs /work/locks /work/state /work/tasks /work/results /work/requests
case "${1:-gateway}" in
  adb)
    shift
    exec /usr/bin/adb "$@"
    ;;
  gateway)
    exec python3 /opt/gateway/service.py
    ;;
  health)
    exec python3 /opt/gateway/health.py
    ;;
  *)
    exec python3 /opt/executor/executor.py "$@"
    ;;
esac
