# adapters

上游对接层。章节与编排器**只依赖** `LiteraturePort` / `AnalysisPort` / `BriefPort`，不直接读其它板块代码。

## 为什么这样设计（方便以后接真模块）

```text
sections / orchestrator
        ↓ 只认 Port ABC + V1 contracts
build_adapters(mode)
        ↓
   mock  ← fixtures/（阶段1）
   live  ← BioLLM 结果包 / 用户 brief / 文献库
```

联调其它模块时：

1. **不要改** `SectionInput` / `SectionDraft` / 五章内部逻辑  
2. 在 live adapter 里把外组 JSON **映射成** V1 模型（见 `docs/CONTRACTS_V1.md`）  
3. `build_adapters(mode="live", ...)` 或向 `WritingPipeline(adapters=...)` 注入自定义 Port  
4. 禁止在 `sections/` 里 `import Article_repository` / `bioflow.modules...`

## 文件

| 文件 | 作用 |
|------|------|
| `base.py` | Port 接口（稳定） |
| `mock_*.py` | 假实现 |
| `fixtures_loader.py` | 读 fixtures 并做 V1 校验 |
| `live_stubs.py` | `LiveAdapterNotReady` |
| `live_analysis.py` | BioLLM 结果包 / tar.gz / API → `AnalysisBundle` |
| `live_brief.py` | 用户 `PaperBrief` JSON |
| `biollm_mapping.py` | 确定性字段映射（不编造结论） |
| `factory.py` | `build_adapters(mode="mock"|"live")` |

## 用法

```python
from article_writing.adapters import build_adapters, validate_fixtures_dir
from article_writing.orchestrator import WritingPipeline

validate_fixtures_dir()  # 成员 B 自检
adapters = build_adapters("mock")
pipeline = WritingPipeline(adapters=adapters)
```

CLI：

```bash
PYTHONPATH=. python -m article_writing --adapter-mode mock --validate-fixtures
PYTHONPATH=. python -m article_writing --adapter-mode mock --out outputs/demo
# 接 BioLLM 已解包结果 + 自己填的课题简介
PYTHONPATH=. python -m article_writing --adapter-mode live --no-react --no-llm \
  --analysis-run-dir /path/to/extracted-package \
  --brief-path /path/to/paper_brief.json \
  --out outputs/live-biollm

# 或本地 tar.gz / 已是 AnalysisBundle 的 JSON
# --analysis-bundle /path/to/task.tar.gz
# --analysis-bundle /path/to/analysis_bundle.json

# 或从 BioLLM API 拉已完成任务
# --analysis-url http://127.0.0.1:8000 --analysis-task-id <task_id>
```

## 接 live 时的映射清单

| Port | 外组应提供 | 映射到 |
|------|------------|--------|
| `LiteraturePort` | **已接**：关键词检索 + `get_by_doi` | `LiteratureHit`（`paper_id = doi`） |
| `AnalysisPort` | BioLLM 结果包 / tar.gz / `GET /api/tasks/{id}/results` | `AnalysisBundle`（描述性指标与方法；不编造组间差异） |
| `BriefPort` | 用户课题 JSON | `PaperBrief` |

### 文献 live（Article_repository）

```bash
# 进程内读 SQLite（默认库：../Article_repository/article_literature.sqlite3）
PYTHONPATH=. python -m article_writing --adapter-mode live --no-react --no-llm \
  --query "autism" --out outputs/live-lit

# 指定库路径
PYTHONPATH=. python -m article_writing --adapter-mode live --literature-db /path/to/article_literature.sqlite3 ...

# 可选：文献库 HTTP 已启动时
PYTHONPATH=. python -m article_writing --adapter-mode live --literature-url http://127.0.0.1:8000 ...
```

`live` 模式：文献走仓库；brief/analysis 未指定路径时仍回退 fixtures。  
提供 `--analysis-run-dir` / `--analysis-bundle` / `--analysis-url`+`--analysis-task-id` 后走 BioLLM 映射。  
`get(paper_id)` 仅支持 **doi**（可带 `doi:` 前缀）；pmid 统一查询暂缓。

字段以 `docs/CONTRACTS_V1.md` 为准；多出来的外组字段放进 `AnalysisBundle.raw` 或丢弃，**不要**改 contracts 塞进章节。
