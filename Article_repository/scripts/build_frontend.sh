#!/usr/bin/env bash
# 构建前端并把产物交给 Go 后端托管。
#
# 好处：完整应用只占 8080 一个端口（前端 + API 同源），
# 不再依赖 Vite 开发服务器的 HMR websocket 与 Host 白名单，
# 远程访问（公网 IP 或 SSH 隧道）都只需要转发一个端口。
#
# 用法：
#   bash scripts/build_frontend.sh
#
# 说明：
#   * 前端在容器 ai-localbase-frontend-dev 内构建（沿用其 node_modules）
#   * 产物复制到 ai-localbase/backend/dist/（该目录已被 gitignore，只保留 .gitkeep）
#   * 后端源码是卷挂载，重启后端即可生效，无需重建镜像
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_CONTAINER="${FRONTEND_CONTAINER:-ai-localbase-frontend-dev}"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-ai-localbase-backend-dev}"
COMPOSE_FILE="$ROOT/ai-localbase/docker-compose.dev.yml"

echo "[1/3] 构建前端（容器 $FRONTEND_CONTAINER）"
if docker exec "$FRONTEND_CONTAINER" sh -lc 'cd /app && npm run build'; then
  :
else
  echo "容器内构建失败，尝试在宿主机构建" >&2
  (cd "$ROOT/ai-localbase/frontend" && npm run build)
fi

echo "[2/3] 复制产物到 backend/dist"
mkdir -p "$ROOT/ai-localbase/backend/dist"
cp -r "$ROOT/ai-localbase/frontend/dist/." "$ROOT/ai-localbase/backend/dist/"
echo "    共 $(find "$ROOT/ai-localbase/backend/dist" -type f | wc -l) 个文件"

echo "[3/3] 重启后端（$BACKEND_CONTAINER）"
docker compose -f "$COMPOSE_FILE" restart backend

for _ in $(seq 1 30); do
  if curl -fsS -m 3 "http://127.0.0.1:8080/health" >/dev/null 2>&1; then
    echo "完成：单端口应用已就绪 -> http://<服务器IP>:8080/"
    exit 0
  fi
  sleep 2
done

echo "后端 60s 内未就绪，请检查：docker logs $BACKEND_CONTAINER" >&2
exit 1
