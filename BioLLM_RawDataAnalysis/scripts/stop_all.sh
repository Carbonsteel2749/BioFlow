#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="${BIOLLM_SERVICE_STATE_DIR:-$PROJECT_ROOT/runtime/services}"
target="${1:-all}"

stop_service() {
  local name="$1" pid_file="$SERVICE_DIR/$1.pid" pid
  if [[ ! -s "$pid_file" ]]; then
    printf '%s is not running.\n' "$name"
    return 0
  fi
  pid="$(cat "$pid_file")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 40); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$pid_file"
  printf '%s stopped.\n' "$name"
}

case "$target" in
  backend) stop_service backend ;;
  frontend) stop_service frontend ;;
  all) stop_service frontend; stop_service backend ;;
  *) printf 'usage: %s [all|backend|frontend]\n' "$0" >&2; exit 64 ;;
esac
