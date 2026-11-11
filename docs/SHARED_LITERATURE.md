# 公共文献库与个人文献库

已确认方案：所有登录用户共用“自闭症与肠道菌群文献库”，可检索、问答、收藏。个人上传保留在个人库；用户提交题录和摘要供审核，管理员通过后进入公共库。定时抓取保持关闭。

## 当前入口与部署

2026-11-11 在服务器 plot 分支部署。新入口：`http://219.224.3.96:8804/`。复用原账户、项目数据库、分析、写作及技能服务，保留技能模块入口；原 8800 进程没有重启，因为 PRJEB33098 九样本分析仍在运行。

`deploy/server96.py` 的下次正常启动使用 `deploy.public_literature_preview:app`，同样提供新版文献库界面；“preview”是独立入口模块的文件名。可用环境变量 `BIOFLOW_GATEWAY_APP` 覆盖该选择。不要为本次文献更新提前停止正在运行的分析任务。

独立入口的启动命令（原 8800 服务正常运行时）：

```bash
cd /home/xh/BioFlow-plot
/home/xh/BioLLM_RawDataAnalysis/.venv/bin/python deploy/start_public_literature_preview.py
```

记录文件：`runtime/public-literature-preview.pid`；日志：`runtime/logs/public-literature-preview.log`。前端新版保存在 `frontend/public-literature.js` 和 `.css`，入口在响应时替换文献模块，原 `project-space.js` 及技能模块保持原样。

## 用户操作

- **检索**：知识库与问答 → 公共文献库 / 联网文献。统一处理医学词汇并显示实际英文检索词；公共论文每页 20 篇，不受问答片段数量限制。
- **问答**：检索后独立选择摘要或正文证据，先显示文献，再逐段返回 Qwen3-14B 回答。题录不作为正文证据；没有证据时明确提示。
- **收藏**：点击公共条目右侧“＋”加入个人库，保存关联和 RIS 题录，不复制全文。联网文献也可保存题录，不要求先建立项目。收藏后的题录能沿用个人库的下载与项目关联功能。
- **个人上传**：沿用个人文献库上传入口，保留账户归属，不自动发布到公共库。
- **提交**：可从个人库或联网结果提交，也可手工填写题录和摘要。个人 PDF 不会随题录自动公开。“我的提交”查看审核结果与说明。
- **管理**：开发者/管理员在“入库审核”查看用户提交和旧库待复核条目，可通过或驳回。公共检索结果提供“移出公共库”；移除后不再参与公共检索和问答，已有个人收藏保留。

## 原 50 条文献的修复

逐条按 DOI 在 PubMed 核验，使用 XML `itertext()` 完整提取标题和摘要，补充作者、年份、期刊、PMID/PMCID 和文献类型。50 条均完成核对。自动纳入规则要求标题明确涉及 ASD，且题名或摘要涉及肠道微生物主题；其余进入人工复核，避免把宽泛背景或勘误默认作为核心证据。

- 23 条进入公共目录：5 条保留可用原文文本，16 条使用修复摘要，2 条仅题录。
- 27 条进入待复核区，保留全部记录，不直接删除。
- 文件名恢复 DOI 时产生的一条中间错误记录已标记 removed，不参与检索或审核清单。
- 旧 Go 库保持原样，没有清库。完整修复清单在 `runtime/public-literature-repair-report.json`；PubMed 核对缓存在 `runtime/public-literature-pubmed-cache.json`。
- 生产数据库导入前备份：`runtime/backups/before-public-library-20261111-230952.sqlite3`。

自动主题规则只做初筛，管理员可在复核区调整；“已核对 DOI”不能代替研究质量评价。仅题录、摘要和全文状态分别显示，只有后两者参与问答证据。

## 检索与推理实现

`backend/bioflow_platform/literature_query.py` 统一中英文术语：ASD/自闭症/孤独症、肠道菌群、FMT、SCFA、益生菌等使用同一表达；词典覆盖的问题直接规范化，复杂中文限定调用原离线翻译模型。失败时保留术语匹配，并显示未识别的中文限定。翻译不是无提示的原句回退。

`shared_literature.py` 对论文题名、摘要、正文、DOI、作者和年份做词法检索，结合现有 multilingual 模型生成的论文向量。论文排序与问答证据窗口独立；个人上传不进入公共候选集。公共目录、收藏关联、提交和管理事件分别存储在 `public_*` 数据表。

旧翻译模型使用已有 `/home/xh/rag_env` 和 Helsinki 模型缓存，通过 `deploy/start_literature_translation.py` 启动；只有翻译服务，没有采集定时器。

文献问答使用独立的本机 GPU 推理服务 `127.0.0.1:11436`，复用 `/snap/ollama/141/lib/ollama`、CUDA v12 库和现有 Qwen3-14B GGUF。`deploy/start_literature_inference.py` 通过 `GGML_BACKEND_PATH` 指向已有 CUDA 后端，启动单并发、4096 上下文服务，不重启原 Ollama。模型模板关闭 thinking，并保留 `/no_think` 指令；该指令见 [Qwen3 官方说明](https://github.com/QwenLM/Qwen3/blob/main/docs/source/inference/transformers.md)。

推理日志：`runtime/logs/public-literature-inference.log`。权重与已安装环境使用服务器路径；项目数据和记录相对项目根目录保存。其他服务器需要另行配置环境位置。

## 验证

权限、个人收藏、审核重放、私有记录隔离、论文分页、无正文拒答、普通用户问答、RIS 收藏、XML 行内文本和 SSE 转换均有隔离测试：`tests/test_shared_literature.py`。同时保留原 `tests/test_literature_qa_scope.py` 的私有库权限回归。

四组固定问题（自闭症与菌群、FMT、儿童 SCFA、益生菌）中英文检索的前 10 篇集合均重合 10 篇。它验证表达一致性，不代表人工标注的相关性准确率。原始结果：`runtime/public-literature-retrieval-after.json`。

真实浏览器已验证每页 20/3 条的分页、英文查询展示、27 条旧文献审核列表、个人库和提交表单。真实模型的首段输出由 CPU 约 167 秒降低到 GPU 约 7 秒，约 13 秒完整结束；完整流式记录保存在 `runtime/public-literature-qa-stream.json`。

最终 12 项隔离与权限回归测试通过。浏览器已验证真实流式问答、编号引用和证据片段查看；8804 入口从用户电脑直接访问返回 200。分页复用查询向量，新增或移除论文仍即时重新读取目录。
