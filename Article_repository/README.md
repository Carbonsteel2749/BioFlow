# Article Repository

面向“自闭症 + 肠道菌群”文献场景的结构化仓库。支持从 PubMed 抓取文献、自动打标签、检索浏览，以及可选的 ai-localbase 向量知识库集成。

仓库同时包含两个相互独立的子项目：

- **article_repository（Python）**：文献抓取、自动打标、检索与 Web 展示。
- **ai-localbase（Go + React）**：本地优先的 RAG 知识库系统（文档上传、向量检索、问答、MCP 服务）。

---

## 快速启动（当前环境）

> 当前架构：SQLite 已停用，文献统一入库 ai-localbase（Qdrant + BGE 轻量多语言 embedding）。

### 0. 前置：Ollama 与容器桥接

Ollama 默认只监听 `127.0.0.1:11434`，Docker 后端无法直接访问。需要一条用户态转发（11435 → 11434）：

```bash
# 拉取 embedding 模型（已在宿主机拉取则跳过）
ollama pull paraphrase-multilingual

# 持久化启动转发（0.0.0.0:11435 -> 127.0.0.1:11434）
cd /home/xh/BioFLow/Article_repository
setsid nohup python3 liter/ollama_proxy_11435.py >/tmp/ollama_proxy.log 2>&1 &
```

`.env` 已配置 `OLLAMA_BASE_URL=http://host.docker.internal:11435`、`QDRANT_VECTOR_SIZE=768`，无需再改。

### 0.2 启动中→英机翻服务（检索翻译用，推荐）

检索前要把中文问题翻成英文。大模型翻译在 CPU 上要 ~10s，因此改用本地 OPUS-MT 小模型（约 77M 参数，单句 **100~400ms**）：

```bash
cd /home/xh/BioFLow/Article_repository

# 依赖（首次执行，已装可跳过）
/home/xh/rag_env/bin/python -m pip install sentencepiece fastapi uvicorn

# 后台启动（0.0.0.0:8090）
setsid nohup /home/xh/rag_env/bin/python scripts/mt_service.py >/tmp/mt_service.log 2>&1 &

# 自检 / 健康检查
/home/xh/rag_env/bin/python scripts/mt_service.py --test
curl -fsS http://127.0.0.1:8090/health
```

模型首次运行会从 `hf-mirror.com` 下载（约 300MB，缓存到 `~/.cache/huggingface`）。专业术语由 `scripts/zh_en_glossary.tsv` 词表纠正（当前 119 条术语 + 15 条纠错规则）。

> 机翻服务没启动时不会中断问答：翻译失败会自动回退用原问题检索，检索调试里 `queryTranslation` 显示 `translation_failed`。

### 1. 启动 ai-localbase 全链路（Qdrant + 后端 + 前端）

```bash
cd /home/xh/BioFLow/Article_repository
docker compose -f ai-localbase/docker-compose.dev.yml up -d --build
```

| 服务 | 地址 | 说明 |
|------|------|------|
| **应用入口** | `http://219.224.3.96:8080` | **前端 + API 同源，日常用这个**（后端托管前端构建产物） |
| 前端（开发） | `http://localhost:4173` | Vite 开发服务器，改前端代码时用（热更新，加载较慢） |
| 后端 | `http://localhost:8080` | Go API（`/health`、`/api/knowledge-bases` 等） |
| Qdrant | `http://localhost:6333` | 向量数据库（collection 前缀 `kb_`，768 维 Cosine） |
| 机翻服务 | `http://localhost:8090` | 中→英检索式翻译（`scripts/mt_service.py`） |

### 2. 同步文献到 ai-localbase

```bash
cd /home/xh/BioFLow/Article_repository

# 测试版全量：抓最近 200 篇（默认）
python3 sync_and_upload.py --mode test_full --retmax 200

# 正式版全量：近 20 年全部符合条件的文献
python3 sync_and_upload.py --mode official_full

# 增量：自上次同步时间之后的新文献
python3 sync_and_upload.py --mode incremental

# 带 --reset 清空现有知识库后重建
python3 sync_and_upload.py --mode test_full --reset
```

- 默认关键词：`自闭症`、`肠道菌群`；检索式按 `strict → balanced → broad` 三组依次执行。
- 文献写入知识库「自闭症-肠道菌群文献库」，元数据标签与全文/摘要一并入库。
- 同步状态记录在 `data/pubmed_sync_state.json`（增量模式据此计算起始时间）。

### 3. 验证检索

```bash
curl -X POST http://localhost:8080/api/knowledge-bases/<KB_ID>/retrieval/debug \
  -H 'Content-Type: application/json' \
  -d '{"query":"双歧杆菌对自闭症儿童肠道菌群的调节作用","knowledgeBaseId":"<KB_ID>","topK":5,"searchMode":"hybrid"}'
```

### 检索流程（当前）

1. **语言判断**：问题含中文（或日文/韩文）→ 需要翻译；纯英文问题直接使用，不调用翻译。
2. **快速机翻**：调用本地 OPUS-MT 机翻服务（`scripts/mt_service.py`）把问题翻成英文检索式，并追加命中的领域术语、纠正已知误译。默认模式 `QUERY_TRANSLATION_MODE=mt`；设为 `llm` 可退回用对话模型翻译。
3. **英文检索**：用英文问题做 embedding + BM25 + sparse 三路召回，RRF 融合，再重排、MMR 选取。
4. **中文回答**：命中的英文 chunks 交给大模型，按用户提问语言作答（中文提问 → 中文回答）。

实测延迟（本机 CPU）：缓存命中 **14ms**、新中文问题 **425~603ms**、纯英文问题 **128ms**（对比大模型翻译约 9.8s）。

调试接口会回显 `englishQuery`（实际检索用的英文问题）和 `queryTranslation`（`translated` / `cache_hit` / `already_english` / `translation_failed` 等）。翻译结果带缓存；机翻服务不可用或超时会自动回退用原问题检索，不中断问答。

---

## 主要功能

1. **PubMed 抓取**：数据源为 PubMed E-utilities，主入口 `article_repository/ingestion/sources/pubmed.py`。默认关键词 `["自闭症", "肠道菌群"]`，检索式由 `search/query_builder.py` 按 `strict → balanced → broad` 三组生成并依次执行，全局去重后按发表时间倒序截断。
2. **全文获取**：若 PubMed 记录带 `pmc_id`，尝试从 PMC 抓开放获取全文；拿不到全文则回退摘要，并用 `content_type`（`full_text` / `abstract`）标记。
3. **入库 ai-localbase**：文献不再写 SQLite。`sync_and_upload.py` 把每篇文献拼成「标题(Title) + 向量正文(Vector Body)」上传到 ai-localbase，由其按 embedding 语义相似度切块（目标 450 字符）、embedding、写入 Qdrant。元数据标签（索引词）不进向量正文，避免污染召回。
4. **翻译 + 三路检索 + RRF**：中文问题先快速翻译成英文，再用英文问题召回 dense + BM25 + sparse 三路并用 RRF 融合；配置 `CROSS_ENCODER_URL` 后走多语言 cross-encoder 重排；命中的英文 chunks 交给大模型用中文作答。

## 目录结构

```text
Article_repository/
├── README.md                  # 本文档
├── sync_and_upload.py         # 主入口：抓取 PubMed 并上传 ai-localbase（CLI）
├── pyproject.toml             # Python 包定义
├── .env                       # 环境变量（NCBI_API_KEY、AI_LOCALBASE_URL 等）
├── data/                      # 运行数据（pubmed_sync_state.json 等）
├── liter/                     # 已停用的 SQLite 历史库与空文件存档
│
├── article_repository/        # ===== Python 文献仓库 =====
│   ├── api/                   # FastAPI（app.py / routes.py：仅 /health、/api/sync/pubmed）
│   ├── classification/        # 打标签 tagger（MockTagger / QwenTagger）
│   ├── ingestion/             # 抓取流程
│   │   ├── full_sync.py       # run_pubmed_sync：test_full / official_full / incremental
│   │   └── sources/           # pubmed.py、pmc_oa.py
│   ├── integration/           # ai_localbase.py：上传文献到向量库的 helper
│   ├── search/                # query_builder.py：strict/balanced/broad 检索式
│   └── storage/               # SQLite 兼容层（已停用，仅保留占位）
│
└── ai-localbase/              # ===== 本地向量知识库（Go + React）=====
    ├── backend/               # Go + Gin 后端（Qdrant、三路检索、MCP）
    ├── frontend/              # React + Vite + TS 前端
    ├── docker/                # Dockerfile、nginx.conf
    ├── docs/                  # architecture / getting-started / mcp 等
    ├── docker-compose.yml     # 一键启动（qdrant + backend + frontend）
    ├── docker-compose.dev.yml # 本地开发编排（当前环境使用）
    ├── README.md              # 完整文档导航
    └── .env.example           # 环境变量模板
```

## 数据库与数据布局

> **SQLite 已停用**：文献不再写入本地 SQLite，统一入库 ai-localbase。旧 SQLite 文件与空文件已移入 `liter/` 目录（仅存档，不参与运行）。

| 位置 | 用途 |
|------|------|
| ai-localbase Qdrant collection（`kb_<知识库ID>`） | 文献向量索引（768 维，dense + BM25 + sparse 三路） |
| `ai-localbase/backend/data/app-state.json` | 知识库、文档、模型配置（JSON） |
| `ai-localbase/backend/data/uploads/` | 上传文献原文（txt） |
| `data/pubmed_sync_state.json` | PubMed 同步状态（增量模式的起始时间） |
| `liter/` | 已停用的 SQLite 历史库与空文件存档 |

---

## 访问前端（重要）

本项目运行在**远程服务器**上。你本地浏览器里的 `localhost` 指向你自己的电脑，不是服务器，因此直接打开 `http://localhost:4173` 会失败。

### 方式一：公网 IP 直连（最简单，推荐）

后端已托管前端构建产物，**前端和 API 同源，一个端口就够**：

```
http://219.224.3.96:8080/
```

不需要任何隧道。服务器上 8080 绑定在 `0.0.0.0` 且无本地防火墙，已验证可从外部直接打开并能正常加载知识库（50 篇 / 1314 chunk）与会话历史。

> 前端改动后需要重新构建才生效：
> ```bash
> bash scripts/build_frontend.sh      # 构建 + 复制产物 + 重启后端
> ```

### 方式二：SSH 隧道（转发被网络策略拦截时用）

只需转发 **8080** 一个端口。**必须在你本机（Windows/Mac/Linux）的终端执行**：

```bash
ssh -N -L 8080:127.0.0.1:8080 <用户名>@219.224.3.96
```

然后在本地浏览器打开 `http://localhost:8080/`。

> 常见误区：如果这条命令是在 VS Code 连上服务器之后的终端里执行的，那等于服务器自己连自己，你本机依然打不开。

### 方式三：VS Code 端口转发

VS Code 的「端口 / PORTS」面板 → **Forward a Port** → 填 `8080`，然后浏览器打开 `http://localhost:8080/`。

### 验证服务已启动

```bash
curl -fsS http://127.0.0.1:8080/health                            # 后端
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/  # 前端（应 200）
curl -fsS http://127.0.0.1:8090/health                            # 机翻服务
```

> 仍可访问 `http://219.224.3.96:4173/`（Vite 开发服务器），但它是按需加载几百个模块的开发构建，
> 首次打开明显更慢，且改动代码才会热更新；日常使用建议走 8080。

## 同步模式

| 模式 | 命令 | 说明 |
|------|------|------|
| 测试版全量 | `python3 sync_and_upload.py --mode test_full --retmax 200` | 抓最近 200 篇（默认） |
| 正式版全量 | `python3 sync_and_upload.py --mode official_full` | 近 20 年全部符合条件的文献 |
| 增量 | `python3 sync_and_upload.py --mode incremental` | 自上次同步时间之后的新文献 |
| 重置重建 | 加 `--reset` | 先清空现有知识库再入库 |

常用参数：`--retmax`（篇数）、`--kb`（知识库 ID）、`--kb-url`（后端地址）、`--keywords`（关键词）、`--oa`（额外抓 PMC-OA 全文篇数）。

## 查看数据

- **知识库列表**：`GET http://127.0.0.1:8080/api/knowledge-bases`
- **知识库健康**：`GET http://127.0.0.1:8080/api/knowledge-bases/<KB_ID>/health`（返回 documentCount / chunkCount / vectorCount / failedCount 等）
- **Qdrant Dashboard**：浏览器打开 `http://localhost:6333/dashboard`（默认仅监听 `127.0.0.1`），collection 名形如 `${QDRANT_COLLECTION_PREFIX}${knowledgeBaseId}`（默认前缀 `kb_`）。
- **文献上传原文**：`ai-localbase/backend/data/uploads/`
- **同步状态**：`data/pubmed_sync_state.json`

---

## 环境变量

| 变量 | 说明 |
|------|------|
| `NCBI_API_KEY` | PubMed E-utilities API Key，提高请求限额（当前已配置） |
| `AI_LOCALBASE_URL` | ai-localbase 后端地址，默认 `http://localhost:8080` |
| `AI_LOCALBASE_KB_NAME` / `AI_LOCALBASE_KB_ID` | 目标知识库名称 / ID |
| `QDRANT_VECTOR_SIZE` | 向量维度（当前 768，与 `paraphrase-multilingual` 一致） |
| `OLLAMA_BASE_URL` | 后端访问 Ollama 的地址（当前 `http://host.docker.internal:11435`，走 11435 桥接） |
| `ENABLE_HYBRID_SEARCH` | 三路混合召回开关（当前 `true`） |
| `ENABLE_QUERY_TRANSLATION` | 检索问题翻译开关（当前 `true`）：中文问题先翻成英文再检索 |
| `QUERY_TRANSLATION_MODE` | `mt`（默认，走本地机翻服务，百毫秒级）或 `llm`（复用对话模型，CPU 上约 10s） |
| `QUERY_TRANSLATION_BASE_URL` | 机翻服务地址；Docker 内默认 `http://host.docker.internal:8090` |
| `QUERY_TRANSLATION_MODEL` / `QUERY_TRANSLATION_PROVIDER` / `QUERY_TRANSLATION_API_KEY` | 仅 `llm` 模式使用；留空则复用对话模型配置 |
| `QUERY_TRANSLATION_TIMEOUT_SECONDS` | 翻译超时秒数（默认 20）；超时或失败自动回退用原问题检索 |
| `CROSS_ENCODER_URL` / `SPARSE_ENCODER_URL` | 可选的重排/稀疏编码服务地址，留空则走本地回退 |

## Python API（可选）

Python FastAPI 服务当前仅保留两个路由，文献主链路已迁移到 `sync_and_upload.py` 直接上传 ai-localbase：

| 接口 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/api/sync/pubmed` | POST | 触发 PubMed 同步（内部调用 `run_full_sync`） |

> 日常使用建议直接运行 `sync_and_upload.py`，无需依赖该 FastAPI 服务。

---

## ai-localbase 使用说明

启动步骤见上方「快速启动」。更多细节见 [`ai-localbase/README.md`](ai-localbase/README.md) 及其 `docs/` 目录。

- **Web UI**：`http://219.224.3.96:8080/`（前端 + API 同源，单端口），可上传文档、查看知识库、运行检索调试、Chat 问答；改前端代码时用开发服务器 `http://219.224.3.96:4173/`。
- **配置模型**：Settings 页配置 Chat（当前环境用 `qwen:7b`，CPU 上 `qwen3:14b` 会超时）与 Embedding（`paraphrase-multilingual`，768 维）。
- **MCP（可选）**：`.env` 开启 `ENABLE_MCP=true` 后，入口为 `GET /mcp`、`GET /mcp/tools`、`POST /mcp`。

## 常见问题

**Q：前端打不开 / `http://localhost:4173` 没反应？**
A：本项目跑在远程服务器上，你本机的 `localhost` 指向你自己的电脑。**直接用 `http://219.224.3.96:8080/` 即可，不需要隧道**；若网络策略拦截，再按「访问前端」一节的 SSH 隧道方式转发 `8080`。注意 `ssh -L` 必须在**你本机**终端执行，不能在 VS Code 连上服务器后的终端里执行。

**Q：`http://219.224.3.96:4173/` 打开很慢？**
A：那是 Vite 开发服务器，按需加载几百个未压缩模块。日常使用走 8080（构建产物，几十个压缩文件）。

**Q：`cd ai-localbase` 报 No such file or directory？**
A：ai-localbase 在项目根目录下。先 `cd /home/xh/BioFLow/Article_repository` 再 `cd ai-localbase`，或直接用绝对路径。

**Q：`Vector dimension error: expected 768, got 1024`？**
A：Qdrant collection 维度与 embedding 模型不一致。更换模型维度后需清空旧 collection（或改 `QDRANT_COLLECTION_PREFIX`）并重建索引。

**Q：后端上传返回 502？**
A：多为 embedding 服务不可达。确认 Ollama 已启动、`11435` 桥接进程存活、容器能访问 `http://host.docker.internal:11435/api/embed`。

**Q：前端提问返回超时 / 502？**
A：本机 GPU 不可用，Chat 模型只能 CPU 推理。已修复：Chat 模型用 `qwen:7b`（不用 `qwen3:14b`），LLM 超时调大到 300 秒，重排用 keyword。CPU 上生成回答约 1~2 分钟属正常。

**Q：上传文献失败但不影响抓取？**
A：设计如此——ai-localbase 上传失败会被捕获并跳过，抓取流程本身不中断。查看日志中的「ai-localbase 上传失败」条目定位原因。

## 当前状态

**已实现：**

- PubMed 抓取（strict → balanced → broad 三组检索式，PMC 全文回退摘要）
- 文献入库 ai-localbase（元数据标签 + 全文/摘要，Qdrant 三路向量）
- 三路召回（dense + BM25 + sparse）+ RRF 融合
- 中文问题保留原文，`paraphrase-multilingual` 跨语言 embedding（768 维）
- 同步模式：`test_full` / `official_full` / `incremental`
- 已入库：200 篇测试版全量（知识库「自闭症-肠道菌群文献库」）

**待完善：**

- OpenAlex / Semantic Scholar / Crossref 多源补充抓取
- cross-encoder 重排服务（`CROSS_ENCODER_URL`）接入实际模型
- 定时调度增量同步
- RAG 综述生成工作流