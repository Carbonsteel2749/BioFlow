#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="${BIOLLM_SERVICE_STATE_DIR:-$PROJECT_ROOT/runtime/services}"
BACKEND_HOST="${BIOLLM_HOST:-0.0.0.0}"
BACKEND_PORT="${BIOLLM_PORT:-8000}"
FRONTEND_HOST="${BIOLLM_FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${BIOLLM_FRONTEND_PORT:-5173}"
mkdir -p "$SERVICE_DIR"

is_running() {
  local pid_file="$1" pid
  [[ -s "$pid_file" ]] || return 1
  pid="$(cat "$pid_file")"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

port_is_open() {
  python3 - "$1" <<'PY'
import socket
import sys

sock = socket.socket()
sock.settimeout(0.2)
try:
    result = sock.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0
finally:
    sock.close()
raise SystemExit(0 if result else 1)
PY
}

wait_for_http() {
  local name="$1" pid="$2" url="$3" log_file="$4"
  for _ in $(seq 1 120); do
    if ! kill -0 "$pid" 2>/dev/null; then
      printf '%s exited during startup. Last log lines:\n' "$name" >&2
      tail -40 "$log_file" >&2 || true
      return 1
    fi
    if curl --fail --silent --show-error "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  printf '%s did not become ready within 60 seconds. Last log lines:\n' "$name" >&2
  tail -40 "$log_file" >&2 || true
  return 1
}

stop_process_group() {
  local pid="$1"
  kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
}

start_backend() {
  local pid_file="$SERVICE_DIR/backend.pid" log_file="$SERVICE_DIR/backend.log" pid
  if is_running "$pid_file"; then
    printf 'Backend already running (PID %s).\n' "$(cat "$pid_file")"
    return 0
  fi
  rm -f "$pid_file"
  if port_is_open "$BACKEND_PORT"; then
    printf 'Backend port %s is already in use by an unmanaged process.\n' "$BACKEND_PORT" >&2
    return 1
  fi
  nohup setsid env BIOLLM_HOST="$BACKEND_HOST" BIOLLM_PORT="$BACKEND_PORT" \
    bash "$PROJECT_ROOT/scripts/run_backend.sh" >>"$log_file" 2>&1 </dev/null &
  pid=$!
  printf '%s\n' "$pid" > "$pid_file"
  if ! wait_for_http backend "$pid" "http://127.0.0.1:$BACKEND_PORT/api/health" "$log_file"; then
    stop_process_group "$pid"
    rm -f "$pid_file"
    return 1
  fi
  STARTED_BACKEND=1
  printf 'Backend ready: http://127.0.0.1:%s/api/health (PID %s)\n' "$BACKEND_PORT" "$pid"
}

start_frontend() {
  local pid_file="$SERVICE_DIR/frontend.pid" log_file="$SERVICE_DIR/frontend.log" pid
  if is_running "$pid_file"; then
    printf 'Frontend already running (PID %s).\n' "$(cat "$pid_file")"
    return 0
  fi
  rm -f "$pid_file"
  if port_is_open "$FRONTEND_PORT"; then
    printf 'Frontend port %s is already in use by an unmanaged process.\n' "$FRONTEND_PORT" >&2
    return 1
  fi
  nohup setsid env BIOLLM_FRONTEND_HOST="$FRONTEND_HOST" \
    BIOLLM_FRONTEND_PORT="$FRONTEND_PORT" \
    bash "$PROJECT_ROOT/scripts/run_frontend.sh" >>"$log_file" 2>&1 </dev/null &
  pid=$!
  printf '%s\n' "$pid" > "$pid_file"
  if ! wait_for_http frontend "$pid" "http://127.0.0.1:$FRONTEND_PORT/" "$log_file"; then
    stop_process_group "$pid"
    rm -f "$pid_file"
    return 1
  fi
  printf 'Frontend ready: http://127.0.0.1:%s/ (PID %s)\n' "$FRONTEND_PORT" "$pid"
}

STARTED_BACKEND=0
start_backend
if ! start_frontend; then
  if [[ "$STARTED_BACKEND" == 1 ]]; then
    bash "$PROJECT_ROOT/scripts/stop_all.sh" backend >/dev/null 2>&1 || true
  fi
  exit 1
fi

printf '\nBioLLM is running.\n'
printf 'Open: http://%s:%s/\n' "${BIOLLM_PUBLIC_HOST:-219.224.3.96}" "$FRONTEND_PORT"
printf 'Logs: %s\n' "$SERVICE_DIR"
