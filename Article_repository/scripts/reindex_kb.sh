#!/usr/bin/env bash
#
# 重建 ai-localbase 知识库索引（父子文档递归检索 + 文本预处理升级后必做）
#
# 为什么必须重建：
#   1. 分块改为「父文档 / 子文档」两级  -> 块结构与 chunk_id 全变
#   2. payload 新增 parent_id/parent_text -> 旧点没有这些字段，检索无法回填父文档
#   3. 稀疏通道过滤停用词                -> BM25 向量与旧索引不一致
#   4. 正文先清洗再入库                  -> 文本变了，embedding 需要重算
#
# 特点：
#   - 逐文档重建，**不需要停后端**，重建期间服务可正常查询（仅正在重建的那篇短暂缺失）
#   - 可断点续做：已完成的文档记在进度文件里，重跑会跳过
#   - 每篇失败自动重试
#
# 用法：
#   bash scripts/reindex_kb.sh                          # 自动取第一个知识库，重建全部文档
#   bash scripts/reindex_kb.sh <KB_ID>                  # 指定知识库
#   bash scripts/reindex_kb.sh -l 3                     # 只重建前 3 篇（先试跑）
#   bash scripts/reindex_kb.sh <KB_ID> --reset-progress # 忽略进度文件，全部重来
#
set -uo pipefail

API="${AI_LOCALBASE_URL:-http://127.0.0.1:8080}"
API="${API%/}"
LIMIT=0
RESET_PROGRESS=0
KB_ID=""

while [ $# -gt 0 ]; do
  case "$1" in
    -l|--limit) LIMIT="$2"; shift 2 ;;
    --reset-progress) RESET_PROGRESS=1; shift ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) KB_ID="$1"; shift ;;
  esac
done

say()  { printf '%s\n' "$*"; }
fail() { printf '错误：%s\n' "$*" >&2; exit 1; }

# ── 前置检查 ────────────────────────────────────────────────────────────────
if ! curl -fsS -o /dev/null -m 8 "$API/health"; then
  fail "后端不可达：$API/health
请先启动服务：
  cd /home/xh/BioFLow/Article_repository/ai-localbase
  docker compose -f docker-compose.dev.yml up -d backend"
fi

# ── 定位知识库与文档清单 ────────────────────────────────────────────────────
LIST_JSON="$(curl -fsS -m 30 "$API/api/knowledge-bases")" || fail "读取知识库列表失败"

IFS=$'\t' read -r KB_ID KB_NAME DOC_TOTAL TOTAL_CHUNKS <<EOF
$(printf '%s' "$LIST_JSON" | KB_ID="$KB_ID" python3 -c '
import json, os, sys
data = json.load(sys.stdin)
want = os.environ.get("KB_ID", "").strip()
items = data.get("items", [])
if not items:
    sys.exit("没有找到任何知识库")
kb = next((k for k in items if k["id"] == want), None) if want else items[0]
if kb is None:
    sys.exit("找不到知识库：" + want)
docs = kb.get("documents", [])
total = sum(int(d.get("chunkCount") or 0) for d in docs)
print("\t".join([kb["id"], kb.get("name", ""), str(len(docs)), str(total)]))
')
EOF

[ -n "${KB_ID:-}" ] || fail "无法解析知识库 ID"

say "知识库：$KB_NAME"
say "  KB ID    : $KB_ID"
say "  文档数   : $DOC_TOTAL"
say "  现有块数 : $TOTAL_CHUNKS"
say "  后端     : $API"
say ""

# ── 进度文件（支持断点续做）────────────────────────────────────────────────
PROGRESS_FILE="/tmp/ai-localbase-reindex-${KB_ID}.done"
if [ "$RESET_PROGRESS" -eq 1 ]; then
  rm -f "$PROGRESS_FILE"
fi
touch "$PROGRESS_FILE"

DOC_IDS="$(printf '%s' "$LIST_JSON" | KB_ID="$KB_ID" python3 -c '
import json, os, sys
want = os.environ["KB_ID"]
data = json.load(sys.stdin)
kb = next(k for k in data.get("items", []) if k["id"] == want)
for d in kb.get("documents", []):
    print(d["id"])
')"

ALL_IDS=()
while IFS= read -r line; do
  [ -n "$line" ] && ALL_IDS+=("$line")
done <<< "$DOC_IDS"

if [ "$LIMIT" -gt 0 ]; then
  ALL_IDS=("${ALL_IDS[@]:0:$LIMIT}")
fi

TOTAL=${#ALL_IDS[@]}
[ "$TOTAL" -gt 0 ] || fail "没有可重建的文档"

SKIPPED=0
DONE=0
FAILED=0
FAILED_IDS=()
STARTED_AT=$(date +%s)

say "开始重建 $TOTAL 篇（已完成过的会自动跳过）…"
say "进度文件：$PROGRESS_FILE"
say ""

index=0
for doc in "${ALL_IDS[@]}"; do
  index=$((index + 1))
  if grep -qxF "$doc" "$PROGRESS_FILE" 2>/dev/null; then
    SKIPPED=$((SKIPPED + 1))
    printf '  [%d/%d] %s  已完成，跳过\n' "$index" "$TOTAL" "$doc"
    continue
  fi

  printf '  [%d/%d] %s  ' "$index" "$TOTAL" "$doc"
  doc_started=$(date +%s)

  ok=0
  for attempt in 1 2 3; do
    code=$(curl -sS -o /tmp/ai-localbase-reindex-last.json -m 3600 \
                -w '%{http_code}' \
                -X POST "$API/api/knowledge-bases/$KB_ID/documents/$doc/reindex" 2>/dev/null)
    if [ "$code" = "200" ]; then ok=1; break; fi
    printf 'http=%s 重试(%d/3)… ' "$code" "$attempt"
    sleep 5
  done

  if [ "$ok" -eq 1 ]; then
    chunks=$(python3 -c '
import json,sys
try:
    d = json.load(open("/tmp/ai-localbase-reindex-last.json"))
    print(d.get("document", {}).get("chunkCount", "?"))
except Exception:
    print("?")
')
    printf 'OK  用时 %ss  块数=%s\n' "$(( $(date +%s) - doc_started ))" "$chunks"
    echo "$doc" >> "$PROGRESS_FILE"
    DONE=$((DONE + 1))
  else
    printf '失败（已重试 3 次）\n'
    FAILED=$((FAILED + 1))
    FAILED_IDS+=("$doc")
  fi
done

TOTAL_SECONDS=$(( $(date +%s) - STARTED_AT ))

say ""
say "── 重建结果 ──────────────────────────────────────"
printf '  成功 %d  跳过 %d  失败 %d  总耗时 %d 分 %d 秒\n' \
  "$DONE" "$SKIPPED" "$FAILED" "$((TOTAL_SECONDS / 60))" "$((TOTAL_SECONDS % 60))"
if [ "$FAILED" -gt 0 ]; then
  say "  失败文档（重跑本脚本会自动重试）："
  for id in "${FAILED_IDS[@]}"; do say "    - $id"; done
fi

# ── 抽样验证：父文档字段是否真的写进去了 ──────────────────────────────────
say ""
say "── 抽样验证 ──────────────────────────────────────"
QDRANT="${QDRANT_URL:-http://127.0.0.1:6333}"
collection="kb_${KB_ID}"
sample_doc="${ALL_IDS[0]}"
curl -fsS -m 30 -X POST "$QDRANT/collections/$collection/points/scroll" \
  -H 'Content-Type: application/json' \
  -d "{\"limit\": 200, \"with_payload\": true, \"filter\": {\"must\": [{\"key\": \"document_id\", \"match\": {\"value\": \"$sample_doc\"}}]}}" \
  2>/dev/null | python3 -c '
import json, sys
try:
    pts = json.load(sys.stdin).get("result", {}).get("points", [])
except Exception:
    print("  （无法读取 Qdrant，跳过验证）")
    raise SystemExit(0)
if not pts:
    print("  （无数据）")
    raise SystemExit(0)
parents = {}
for p in pts:
    pl = p.get("payload", {})
    parents.setdefault(pl.get("parent_id"), []).append(pl)
with_parent = sum(1 for p in pts if p.get("payload", {}).get("parent_id"))
print("  样本文档: %s" % pts[0]["payload"].get("document_id"))
print("  子块 %d 个 / 父块 %d 个" % (len(pts), len(parents)))
print("  带 parent_id 的块: %d/%d %s" % (
    with_parent, len(pts), "✅" if with_parent == len(pts) else "❌（重建未生效？）"))
for pid, kids in list(parents.items())[:3]:
    plen = len(kids[0].get("parent_text", "")) if kids else 0
    print("    父块 %s -> %d 个子块, 父正文 %d 字符, 子块长度 %s" % (
        str(pid)[-12:], len(kids), plen, [len(k["text"]) for k in kids]))
'

say ""
say "建议接着改一下上下文预算（父文档约 1200 字符，否则一个父块就占满预算）："
say "  ai-localbase/.env: RETRIEVAL_MAX_CONTEXT_CHARS=2400"
say "  改完重启后端：docker compose -f docker-compose.dev.yml up -d backend"
