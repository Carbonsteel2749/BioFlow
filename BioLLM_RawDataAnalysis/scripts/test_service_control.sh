#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
cleanup() {
  BIOLLM_SERVICE_STATE_DIR="$TMP_ROOT/services" \
    bash "$PROJECT_ROOT/scripts/stop_all.sh" >/dev/null 2>&1 || true
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

read -r backend_port frontend_port < <(
  python3 - <<'PY'
import socket

ports = []
for _ in range(2):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    ports.append(sock.getsockname()[1])
    sock.close()
print(*ports)
PY
)

mkdir -p "$TMP_ROOT/incoming" "$TMP_ROOT/runtime"
printf '{}\n' > "$TMP_ROOT/database.resolved.json"

export BIOLLM_SERVICE_STATE_DIR="$TMP_ROOT/services"
export BIOLLM_INPUT_ROOT="$TMP_ROOT/incoming"
export BIOLLM_STATE_ROOT="$TMP_ROOT/runtime"
export BIOLLM_DATABASE_MANIFEST="$TMP_ROOT/database.resolved.json"
export BIOLLM_AUTO_RUN=0
export BIOLLM_HOST=127.0.0.1
export BIOLLM_FRONTEND_HOST=127.0.0.1
export BIOLLM_PORT="$backend_port"
export BIOLLM_FRONTEND_PORT="$frontend_port"

bash "$PROJECT_ROOT/scripts/start_all.sh"

curl --fail --silent "http://127.0.0.1:$backend_port/api/health" |
  python3 -c 'import json,sys; assert json.load(sys.stdin) == {"status":"ok"}'
curl --fail --silent "http://127.0.0.1:$frontend_port/" | grep -q '<div id="root"></div>'

backend_pid="$(cat "$BIOLLM_SERVICE_STATE_DIR/backend.pid")"
frontend_pid="$(cat "$BIOLLM_SERVICE_STATE_DIR/frontend.pid")"
kill -0 "$backend_pid"
kill -0 "$frontend_pid"

bash "$PROJECT_ROOT/scripts/start_all.sh"
[[ "$(cat "$BIOLLM_SERVICE_STATE_DIR/backend.pid")" == "$backend_pid" ]]
[[ "$(cat "$BIOLLM_SERVICE_STATE_DIR/frontend.pid")" == "$frontend_pid" ]]

bash "$PROJECT_ROOT/scripts/stop_all.sh"
! kill -0 "$backend_pid" 2>/dev/null
! kill -0 "$frontend_pid" 2>/dev/null

echo "service control smoke test passed"
