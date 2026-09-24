# article_writing

BioFLow 板块 3：论文撰写（按生信论文 IMRaD 框架生成初稿）。

默认骨架见 [docs/PAPER_FRAMEWORK.md](docs/PAPER_FRAMEWORK.md)：  
**Abstract → Introduction → Methods → Results → Discussion → Conclusions（独立）→ Back Matter**。

## 文档

| 文档 | 说明 |
|------|------|
| [docs/PAPER_FRAMEWORK.md](docs/PAPER_FRAMEWORK.md) | **生成文章的固定框架与各部分内容** |
| [docs/CONTRACTS_V1.md](docs/CONTRACTS_V1.md) | 字段规范 |
| [docs/TERMINOLOGY.md](docs/TERMINOLOGY.md) | PaperQA / STORM 术语对照 |
| [PHASE1_TASKS.md](PHASE1_TASKS.md)～[PHASE4_TASKS.md](PHASE4_TASKS.md) | 历史阶段任务 |

## 框架与数据流

```text
fixtures/live → adapters → (ReAct 文献取证) → sections(IMRaD七章)
  → evidence → consistency → export
```

章节：`abstract → introduction → methods → results → discussion → conclusion → back_matter`

## 运行

```bash
cd article_writing
pip install -r requirements.txt
PYTHONPATH=. python3 -m article_writing --run-id demo --out outputs/demo
# 接 BioLLM 真实结果（描述性方法/结果；课题简介另给 JSON）
PYTHONPATH=. python3 -m article_writing --adapter-mode live --no-react --no-llm \
  --analysis-run-dir /path/to/extracted-package \
  --brief-path /path/to/paper_brief.json \
  --out outputs/live-biollm
# 可选：本地 Ollama 润色（推荐 qwen3:14b）
PYTHONPATH=. python3 -m article_writing --run-id demo-llm --out outputs/demo-llm --llm
PYTHONPATH=. pytest -q
```

### LLM 润色（可选）

默认仍是**模板起草**；加 `--llm` 后做**中英双语**期刊化润色，并贯连 Methods↔Results↔Conclusions。  
**禁止**改动上游模块给出的数据、结论、图注与图片路径；数字/`lit_*`/`fig_*`/`![]()` 锚点丢失则退回模板。

导出排版产物：
- `manuscript.md`：整篇拼接
- `figures/`：上游图复制到正文相对路径
- `sections/*.md`：分章（Results 内已插入图）

| 推荐 | 说明 |
|------|------|
| **Ollama + `qwen3:14b`** | 本机已可用；数据不出机器；`configs/example.yaml` 默认此项 |
| DeepSeek / OpenAI | `--llm-provider deepseek` 或 `openai` |

额外写作要求：`--llm-instructions "..."`（追加到默认双语/防幻觉规则）。

流程：`模板草稿（含插图） → LLM 双语润色 → evidence → consistency → 排版导出`

## 前端操作界面

独立目录：[frontend/](frontend/)（绿白主题工作台）。

```bash
pip install -r frontend/requirements.txt
PYTHONPATH=. python3 -m uvicorn frontend.server:app --host 127.0.0.1 --port 8765
# 打开 http://127.0.0.1:8765/
```

流程：分章生成 → 逐章手写/对话修改并确认 → 统一排版导出（**Markdown / Word / PDF**）。
