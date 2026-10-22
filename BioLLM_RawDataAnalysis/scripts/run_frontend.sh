#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -s /home/xh/.nvm/nvm.sh ]]; then
  # shellcheck source=/dev/null
  source /home/xh/.nvm/nvm.sh
fi
cd "$PROJECT_ROOT/frontend"
exec npm run dev -- --host "${BIOLLM_FRONTEND_HOST:-127.0.0.1}" --port "${BIOLLM_FRONTEND_PORT:-5173}"
