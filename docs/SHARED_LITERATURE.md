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

## 2026-11-12：证据边界与语义优化

此前证据按 600 字符窗口、450 字符步长直接切片，会出现半个词和半句话。现改为：

- 短摘要作为自然完整单元优先保留，避免只抽到背景而遗漏结果和局限性。
- 长摘要和正文先按原始段落、完整句子确定边界；使用原有 multilingual 模型的句向量，在主题变化处组织语义段。长度是软限制，不切开句子。
- 处理科学缩写、菌名首字母、`e.g.`、`et al.`、小数和中英文句号；保留 heading 与随后内容、转折句与前面的论断。
- 证据选择结合词法与语义匹配；背景或方法段命中时补充结果证据。模型上下文按论文分配预算，只选择完整语义单元，展示的引用文字与实际送入模型的文字一致。
- “查看证据”提供“查看完整摘要／查看已入库原文”。全文只在点击后读取；原文起止位置和内容标识随证据返回。

新增 `backend/bioflow_platform/literature_evidence.py`；句向量及分段缓存在 SQLite `public_evidence_index` 表，按内容摘要与版本校验。管理员审核或发布新文献后后台构建；索引未就绪时仍使用完整段落和句子。此次为 21 篇可用摘要／正文构建了证据索引；2 篇仅题录不参与问答。原论文检索向量和原文没有重写。

手动重建仅证据索引：

```bash
cd /home/xh/BioFlow-plot
/home/xh/BioLLM_RawDataAnalysis/.venv/bin/python deploy/index_literature_evidence.py \
  --database runtime/bioflow.sqlite3 --report runtime/evidence-semantic-index-20261112.json
```

验证：20 项边界、完整性、缓存、权限与原功能测试通过；六组问题优化前后前十篇论文及顺序完全相同。目标论文排名分别为 1、1、1、7、1、1，这是保持检索结果的回归证据，不代表已完成全面相关性评测。实际问答 4 篇引用均通过原文区间和完整句子边界核验，系统综述的行为结局局限性仍在证据中；首段约 2.5 秒，约 7.6 秒完整结束。浏览器已核对 Neuronutrition 同一文献的完整引用和摘要展开。

运行记录：`runtime/evidence-search-before.json`、`runtime/evidence-search-after.json`、`runtime/evidence-qa-after-20261112.json`、`runtime/evidence-acceptance-20261112.json`。仅更新 8804 独立入口，原分析服务和定时抓取设置保持原状。

## 2026-11-12：医学术语优先与实体检索

截图原问题中的木糖醇曾因未被词表识别而交给通用 MT，被错译为 caramel，神经递质也出现错误译词。此次先匹配受控医学术语，再仅翻译剩余普通限定，保护名称不被通用翻译覆盖。

- 新增 `backend/bioflow_platform/medical_terms.json`，按化合物、菌种、基因、神经科学和模型等类别维护标准词、同义词、展示名与实体优先级。木糖醇对应 xylitol，神经递质对应 neurotransmitter，突触结构对应 synaptic structure。英文名称可核对 [Xylitol MeSH](https://www.ncbi.nlm.nih.gov/mesh/68014993) 和[目标原始文献](https://pubmed.ncbi.nlm.nih.gov/42300458/)。
- 小鼠 mouse、大鼠 rat 与 animal 分开；保留 5-HTP／serotonin、L／D 乳酸及化合物连接符的差别。最长术语先匹配，基因标识不会误匹配到更长编号。
- 不再删除未知医学名称内部的“的、能”等字。对检测到但未确认的药物或医学词，提示补充英文；问答在名称未确认时停止生成，避免依据错误检索词作答。词表需持续维护，不代表已覆盖所有医学名词。
- 具体化合物、菌种和基因在题名、摘要中的精确匹配优先于泛化主题，补充文献仍可返回。使用缓存复用论文词法特征，且每次仍读取当前公共条目，移除操作即时生效。
- 页面新增“医学术语核对”，列出中文原词与标准英文；普通检索、联网检索和问答使用同一份查询表示，附带术语版本。

41 项术语、排序、权限、证据和原功能测试通过。实际浏览器使用原问题“在遗传性自闭症小鼠模型中，木糖醇是否会影响肠道菌群、神经递质和突触结构？”：术语不再出现 caramel／neurotransmittural；首条命中 DOI `10.1039/d6fo01006g`，问答正确引用该文献并识别 Chd8 小鼠模型。该次已热身的检索约 1.4 秒，不代表所有首次查询耗时。
