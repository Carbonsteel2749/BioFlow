# 旧系统文献采集与入库代码核查

核查日期：2026-11-11。仅阅读源代码、配置项、运行状态与日志摘要；未执行采集、清库或重建索引。

旧项目目录：`/home/xh/BioFLow/Article_repository`。另有 `/home/xh/BioFLow/crawl_pubmed.py`。注意 Linux 上 `BioFLow` 与当前部署目录 `BioFlow-plot` 是不同目录。

## 一、发现的两套链路

| 链路 | 入口 | 实际功能 | 当前证据 |
|---|---|---|---|
| 基础定时采集 | `/home/xh/BioFLow/crawl_pubmed.py` | PubMed ESearch → EFetch XML → SQLAlchemy `articles` 表；代码数据库配置为 PostgreSQL | crontab 每天02:00执行，参数 `gut microbiome autism`、5篇；日志在11月11日仍有更新，最近五条成功计数均为新增0篇 |
| 完整知识库同步 | `Article_repository/sync_and_upload.py` → `article_repository/ingestion/full_sync.py` | 分层 PubMed 检索、PMC全文/摘要回退、标签处理、直接上传 ai-localbase，进一步向量入库 | 完整函数存在，另有 `/api/sync/pubmed` 路由；未发现它对应的定时项或常驻采集进程，不能据此认定定期运行 |

定时项只证明配置和近期执行痕迹，不代表完整链路已经自动运行或成功新增文献。

## 二、采集规则

### PubMed

关键文件：

- `article_repository/search/query_builder.py:115`：关键词分组规则。
- `article_repository/search/query_builder.py:219`：三组检索式生成入口。
- `article_repository/ingestion/sources/pubmed.py:164`：执行检索、分页和解析。

规则：

1. 预置“自闭症 / autism”“肠道菌群 / gut microbiota”的核心词、扩展词、MeSH词；未知关键词按原词处理。
2. 同一概念内用 OR，不同输入概念之间用 AND。
3. strict：标题字段，前三个核心词，英文限制。
4. balanced：标题/摘要字段，全部核心词与前四个扩展词，英文限制。
5. broad：标题/摘要、MeSH及全字段，更多扩展词，不加英文限制。
6. 每次都执行 strict → balanced → broad，合并后按 PMID 去重，按发表日期降序，再截取总数量。虽然函数保留 `mode` 参数，实际抓取主逻辑没有按它只选择一个模式。
7. 每页最多200条；请求之间通常暂停0.4秒；ESearch取PMID，EFetch取XML。
8. 提取 DOI、PMID、PMCID、标题、摘要、作者、日期、期刊、关键词；作者关键词缺失时使用MeSH词补齐。没有DOI时用 `pubmed:PMID` 作标识。
9. 有PMCID时尝试取得PMC正文；失败时回退摘要，并标记 `content_type`。

### PMC开放全文

文件：`article_repository/ingestion/sources/pmc_oa.py:162`、`:173`。

- 检索式追加 `open access[filter]`，通过PMC ESearch / EFetch取得JATS XML。
- 默认仅保留确实取得 `<body>` 正文的文献。
- `include_nonoa=True` 时可保留正文缺失的文献并回退摘要。
- 取得的是XML中的正文文本，不是自动取得PDF；图片、补充材料也没有进入此抓取流程。

### 三种同步模式

文件：`article_repository/ingestion/full_sync.py:110`。

- `test_full`：默认最新200篇匹配记录，无20年限制。
- `official_full`：回溯20年，代码不设置总篇数上限；并不等于已经验证能够突破上游接口的所有大结果集限制。
- `incremental`：从上次记录的最新发表日期往前重叠一天开始检索，默认最多200篇；没有状态时回退测试全量。
- 状态文件：`data/pubmed_sync_state.json`。

OpenAlex、Crossref、Semantic Scholar 的旧适配文件目前只有占位说明。`ingestion/pipeline.py`、独立的 `incremental_sync.py`、`workflows/sync_workflow.py` 也是占位；真正的增量逻辑在 `full_sync.py`。

## 三、入库规则

### ai-localbase 主路径

文件：`article_repository/integration/ai_localbase.py`。

- `ensure_knowledge_base`（约260行）：指定ID、配置ID/名称、按名称查找或创建知识库。
- `article_to_text`（176行）：生成“标题 + 向量正文”，正文优先全文、为空时用摘要。
- `upload_article_document`（193行）：没有MCP配置时直接走REST；有MCP配置时先尝试MCP，失败回退REST。
- REST上传（276行）：`POST /api/knowledge-bases/{id}/documents`，multipart文件字段 `file`，文件为TXT。
- MCP小文本：`upload_text_document`；超过256KB时走暂存上传再 `register_staged_upload`。
- 目前完整作者、期刊、分类标签等没有作为结构化字段随这条TXT上传请求提交。`_article_metadata()` 虽有定义，但这条主路径没有调用它。

### 知识库内部索引

源码根目录：`ai-localbase/backend`。

`internal/service/app_service.go:1714` 的 `IndexDocument`：

文件取文 → 预处理 → 父子/语义分块 → 向量模型 → Qdrant写入 → 文档状态变成 `indexed`，保存文档记录及分块数。

- `RegisterStagedUpload`：约531行。
- `buildDocumentChunks`：约1668行。
- `AddDocument`：约1890行；新增文档ID并保存状态。
- 原始文件路径、文档列表和向量索引在这一层维护。
- 上传成功/抓取成功不能直接等同于内容已经完成索引，需读取文档状态。

### SQLite保留代码

`storage/metadata_store.py` 是实际SQLite存储实现，包含DOI唯一约束及冲突更新；`repository.py`、`indexes.py` 保留批量写入、检索和索引代码。

但当前 `run_pubmed_sync()` 没有调用这套SQLite写入。`save_pubmed_articles()`、`save_oa_articles()` 明确跳过SQLite，只有打印和返回数量。不能因磁盘上存在SQLite文件，就认定当前采集仍在写它。

## 四、接回新前端前需要修正的点

1. **增量日期水位有问题。** 当前状态记录 `last_sync_date=2026-09-23`、`latest_publish_time=2026-12-31`，后者晚于核查日。增量会从未来日期开始，影响结果。
2. **上传失败仍推进水位。** 同步最后按全部抓取记录保存状态；`articles_saved` 写的是抓取列表长度，不是实际上传成功数，可能跳过失败项；增量默认截断200篇，也缺少积压结果补齐机制。
3. **标签主路径未调用Qwen。** `_tag_articles()` 使用 `MockTagger` 关键词规则；`QwenTagger` 虽存在，但未被此入口调用。规则还会在无证据时填“动物模型”“饮食干预”等默认值，不能当成可靠科学标注。
4. **向量库跨批次去重不足。** 采集器按PMID做单次去重，旧SQLite可按DOI更新；但直接上传向量库会创建新文档ID，没有看到主同步流程中的DOI上传登记表，重跑和增量重叠可能产生重复文档。
5. **结构化题录没有与向量文档完整绑定。** 作者、年份、期刊、标签及源标识应独立保存，并关联知识库文档ID，才能支撑新前端行式文献库。
6. **新平台账户登记未衔接。** 旧脚本直接向8080写知识库，绕过当前BioFlow的用户/项目归属登记。只读检查发现底层有2个知识库，其中1个登记到developer，另1个未登记到该账户，因而不会自动显示在当前前端。
7. **月度调度函数不可直接依赖。** `schedule_monthly_increment()` 使用 `schedule.every().month`；当前环境检查 `hasattr(...,'month')` 为False。实际存在的是另一脚本的每日cron任务。
8. **基础cron脚本也有解析隐患。** 通过XML叶节点本身的真假值检查标题/作者等，可能把有文本的叶节点误判为空；标题为空又会跳过入库。它也没有日期水位或明确的最新发表排序。近期新增0篇不能简单解释成没有新文献。
9. **`--reset` 会清空知识库。** 当前实现遍历并删除接口暴露的知识库及其向量集合，不能作为普通用户的“重新检索”动作接入。

## 五、与当前前端的关系

当前前端联网检索使用Crossref题录，并通过RIS保存个人文献条目；以上PubMed分层检索、PMC开放正文采集和全文向量入库链路尚未接回新前端。

最值得复用的是 `query_builder.py`、`pubmed.py`、`pmc_oa.py`、`full_sync.py`、`integration/ai_localbase.py`。接回时需要补上可追踪的采集任务、DOI/PMID去重、失败重试、题录与文件分开保存，以及当前用户/项目/个人文献库的归属登记。
