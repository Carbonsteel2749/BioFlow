#!/usr/bin/env bash
# 启动 BioFlow 各模块服务（统一入口 + 论文撰写 + 数据分析 + 机翻）。
#
# 已在运行的服务会跳过，可重复执行。重启机器后跑一次即可。
#
# 注意：文献知识库（ai-localbase）由 docker compose 管理，本脚本只做状态检查。
#
# 用法：bash scripts/start_services.sh
set -uo pipefail

BIOFLow=/home/xh/BioFLow
BIOLLM=/home/xh/BioLLM_RawDataAnalysis
PY=/home/xh/BioFLow/.venv/bin/python
UNIFIED_PORT="${UNIFIED_FRONTEND_PORT:-5180}"

port_open() { curl -fsS -o /dev/null -m 3 "http://127.0.0.1:$1$2" 2>/dev/null; }

# ── 1. 中→英机翻服务（论文/文献检索的查询翻译）─────────────────────────────
if port_open 8090 /health; then
  echo "[skip] 机翻服务 :8090 已在运行"
else
  echo "[start] 机翻服务 :8090"
  setsid nohup /home/xh/rag_env/bin/python \
    "$BIOFLow/Article_repository/scripts/mt_service.py" > /tmp/mt_service.log 2>&1 &
  sleep 18
fi

# ── 2. 论文撰写前端 ────────────────────────────────────────────────────────
if port_open 8765 /api/health; then
  echo "[skip] 论文撰写前端 :8765 已在运行"
else
  echo "[start] 论文撰写前端 :8765"
  (cd "$BIOFLow/article_writing" && setsid nohup env PYTHONPATH="$BIOFLow/article_writing" \
    "$PY" -m uvicorn frontend.server:app --host 0.0.0.0 --port 8765 > /tmp/aw_frontend.log 2>&1 &)
  sleep 12
fi

# ── 3. 数据分析（BioLLM：后端 :8000 只绑本机，前端 :5173 对外）──────────────
# 后端保持 127.0.0.1（安全，不经外网直连）；前端 Vite 把 /api 代理到后端，
# 所以外部浏览器只需访问 5173，统一入口也用 5173/api/health 探活。
if port_open 8000 /api/health; then
  echo "[skip] 分析后端 :8000 已在运行"
else
  echo "[start] 分析后端 :8000"
  (cd "$BIOLLM" && setsid nohup bash scripts/run_backend.sh > /tmp/biollm_backend.log 2>&1 &)
  sleep 25
fi

if port_open 5173 /api/health; then
  echo "[skip] 分析前端 :5173 已在运行"
else
  echo "[start] 分析前端 :5173 (0.0.0.0)"
  (cd "$BIOLLM" && setsid nohup env BIOLLM_FRONTEND_HOST=0.0.0.0 \
    bash scripts/run_frontend.sh > /tmp/biollm_frontend.log 2>&1 &)
  sleep 15
fi

# ── 4. 统一入口工作台（前端已构建则直接 preview）────────────────────────────
if port_open "$UNIFIED_PORT" /; then
  echo "[skip] 统一入口 :$UNIFIED_PORT 已在运行"
else
  echo "[start] 统一入口 :$UNIFIED_PORT"
  if [ ! -d "$BIOFLow/unified_frontend/dist" ]; then
    echo "        未找到 dist，先构建…"
    (cd "$BIOFLow/unified_frontend" && npm run build)
  fi
  (cd "$BIOFLow/unified_frontend" && setsid nohup npm run preview > /tmp/unified_frontend.log 2>&1 &)
  sleep 8
fi

echo
echo "── 状态 ──────────────────────────────────────────"
for s in "统一入口:$UNIFIED_PORT:/" "论文撰写:8765:/api/health" "文献知识库:8080:/health" \
         "分析前端:5173:/api/health" "分析后端:8000:/api/health" "机翻服务:8090:/health"; do
  name=${s%%:*}; rest=${s#*:}; port=${rest%%:*}; path=${rest#*:}
  if port_open "$port" "$path"; then
    printf "  %-12s :%-5s OK\n" "$name" "$port"
  else
    printf "  %-12s :%-5s 未就绪\n" "$name" "$port"
  fi
done

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "── 访问地址（把 <IP> 换成 $IP 或公网 IP）─────────"
echo "  统一入口：   http://<IP>:$UNIFIED_PORT/          ← 从这里进入，跳转各模块"
echo "  文献知识库： http://<IP>:8080/                  （前端 + API 同源）"
echo "  数据分析：   http://<IP>:5173/"
echo "  论文撰写：   http://<IP>:8765/"
echo
echo "提示：三个模块的「打开模块」链接由浏览器当前主机名推导，"
echo "      所以无论用 IP 还是域名访问统一入口，都能正确跳转。"
echo "      不要用 127.0.0.1 打开统一入口——那样子模块链接会指向你自己的电脑。"
