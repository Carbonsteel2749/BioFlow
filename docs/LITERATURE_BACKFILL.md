# 近二十年英文文献一次性回填

2026-11-12 用户授权后台抓取。范围固定为 **2006-11-12 至 2026-11-12**，按 PubMed 发表日期检索；本任务没有创建或恢复定时抓取。

## 范围与处理

- PubMed：ASD／自闭症／Asperger 主题，结合肠道微生物、肠脑轴、益生菌、益生元、粪菌移植等主题；要求英文。
- 使用 MeSH 与题名／摘要组合召回，逐年分片。若分片超过 9999 条继续二分日期，逐条登记 PMID，避免 PubMed 单次枚举上限导致遗漏。
- EFetch 获取题录、完整摘要、作者、期刊、DOI、PMID/PMCID、语言、发表日期及精度、研究类型与撤稿关联。XML 行内标签使用 `itertext()` 保留文字。
- 自动纳入要求明确 ASD 与肠道主题、合格英文与时间、可用摘要。主题或日期不明确、书籍章节、勘误／撤稿、内容不足的记录放入待复核。候选数不是最终公共文献数。
- DOI 优先、PMID 回退去重；已有条目不重复插入，保留已有正文、向量、移除和待复核状态。新发现的撤稿或关注声明会将原活跃条目转入复核。
- 通过 PMC OAI-PMH 官方接口取得允许复用的正文，保存来源和许可；`cannotDisseminateFormat` 视为全文不可用，正常保留摘要。其他失败记录到任务队列，支持续跑。
- 题录入库后分阶段补充全文、论文向量和完整句子／语义证据索引。索引完成前仍可进行词法检索和完整句子证据提取。

接口依据：[NCBI E-utilities 参数](https://www.ncbi.nlm.nih.gov/sites/books/NBK25499/)、[PubMed 枚举上限说明](https://www.nlm.nih.gov/pubs/techbull/so22/so22_updated_pubmed_e_utilities.html)、[PMC OAI-PMH](https://pmc.ncbi.nlm.nih.gov/tools/oai/)。网络请求串行限速并重试；索引按小批次、低优先级后台执行。

## 运行与检查

服务器工作目录：`/home/xh/BioFlow-plot`，分支 `plot`。入口：`deploy/backfill_public_literature.py`。

启动或从断点续跑同一任务：

```bash
cd /home/xh/BioFlow-plot
/home/xh/BioLLM_RawDataAnalysis/.venv/bin/python deploy/backfill_public_literature.py
```

查看状态：

```bash
/home/xh/BioLLM_RawDataAnalysis/.venv/bin/python deploy/backfill_public_literature.py --status
```

任务目录：`runtime/crawls/asd-gut-2006-11-12_2026-11-12/`。

- `config.json`：时间窗口、完整检索式和规则版本。
- `queue.sqlite3`：逐篇及各阶段检查点；重新启动跳过已完成部分。
- `progress.json`：阶段、PID、候选数、题录完成、新增公共／待复核、去重、全文和索引进度、错误数。
- `run.log`：运行日志。
- `raw/`：PubMed XML 及取得的 PMC 原件。

阶段依次为 `discovery`、`metadata`、`fulltext`、`index`，最后为 `completed` 或 `completed_with_errors`。`failed` 表示任务退出，原因在状态和日志中，修复后使用同一命令续跑。没有使用未来发表日期推进增量水位。

同一批任务运行时重复启动会返回现有任务；全局文件锁阻止重复回填。任务独立于网站主管进程，不需要重启 8800、8804 或分析服务。

运行前已备份数据库，文件名为 `runtime/backups/before-20y-backfill-日期时间.sqlite3`。初次全窗口探测返回 1983 条候选；最终以逐年去重和任务实际报告为准。

测试：`tests/test_literature_backfill.py`，涵盖英文／主题／撤稿过滤、日期边界、行内文本、无 DOI、去重、已有文献保护、断点队列、PMC 正文与许可、书籍章节显式复核。
