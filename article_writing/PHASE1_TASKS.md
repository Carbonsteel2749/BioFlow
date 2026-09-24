# 第一阶段任务 · 6 人分工

## 阶段目标

在**不依赖**其它板块真实服务的前提下：

1. 契约字段稳定、大家按同一 schema 开发  
2. fixtures / mock 可加载  
3. 五章都能产出符合 `SectionDraft` 的草稿  
4. 一键导出 `writing_bundle.json` + 五章 markdown  
5. `pytest` 通过  

**本阶段不做：** 真 RAG、真 ReAct 多步循环、真 LLM 精修、接 live 文献/分析 API；**不安装** PaperQA / STORM。

验收命令：

```bash
cd article_writing
PYTHONPATH=. pytest -q
PYTHONPATH=. python -m article_writing --run-id phase1 --out outputs/phase1
```

## 必读文档

| 文档 | 谁先读 |
|------|--------|
| [docs/CONTRACTS_V1.md](docs/CONTRACTS_V1.md) | **全员**（字段以它为准） |
| [docs/TERMINOLOGY.md](docs/TERMINOLOGY.md) | A/B/C 必读；其余建议 |
| [docs/REFERENCES_PHASE1.md](docs/REFERENCES_PHASE1.md) | **A/B/C 必读**（只读外部 README） |
| [docs/SCHEMA_CHANGELOG.md](docs/SCHEMA_CHANGELOG.md) | 改字段时必更新 |
| [docs/PHASE1_SIX_WRITERS.md](docs/PHASE1_SIX_WRITERS.md) | **把原 C/D/E 三任务拆成 6 人写章节时用** |

> 若组内是「6 人只写论文章节/串联」（A/B 接口与假数据已就绪），请直接按 [PHASE1_SIX_WRITERS.md](docs/PHASE1_SIX_WRITERS.md) 派活，一人一章 + 一人串联。

---

## 成员 A · 契约与文档（接口负责人）

**负责目录：** `article_writing/contracts.py`、`docs/CONTRACTS_V1.md`、`docs/SCHEMA_CHANGELOG.md`、术语/导读文档维护

**任务：**

- [x] 确认并锁定 V1 字段（见 [CONTRACTS_V1.md](docs/CONTRACTS_V1.md)）
- [x] 发布「字段变更需 A 审批」规则（见 CONTRACTS_V1 文首 + README）
- [x] 提供对外对齐话术（文献 `LiteratureHit` / 分析 `AnalysisBundle` / 可视化 `WritingBundle`）
- [x] 完成 A/B/C 外部导读与术语对照（[REFERENCES_PHASE1.md](docs/REFERENCES_PHASE1.md)、[TERMINOLOGY.md](docs/TERMINOLOGY.md)）
- [ ] 持续审查触及 `contracts.py` 的 PR；变更必须写 [SCHEMA_CHANGELOG.md](docs/SCHEMA_CHANGELOG.md)
- [ ] 保证改字段后 `tests/test_contracts.py` 仍绿
- [ ] 按导读完成 PaperQA + STORM README 阅读自检（导读文末 checklist）

**禁止：** 私自改字段不通知；实现复杂 LLM/ReAct；引入 PaperQA/STORM 依赖

---

## 成员 B · Fixtures 与 Mock Adapters

**负责目录：** `fixtures/`、`article_writing/adapters/`

**任务：**

- [x] 阅读 [REFERENCES_PHASE1.md](docs/REFERENCES_PHASE1.md) + [TERMINOLOGY.md](docs/TERMINOLOGY.md)（文献 hit 元数据部分）
- [x] 维护/丰富假数据：`paper_brief.json`、`analysis_bundle.json`、`literature_hits.json`（字段 ⊆ V1；文献增至 5 条）
- [x] 保证 `MockBriefPort` / `MockAnalysisPort` / `MockLiteraturePort` 行为稳定（经 V1 `model_validate`）
- [x] `search(query)` 分词打分排序（见 `mock_literature.py`）
- [x] 「如何替换为 live」说明：[`adapters/README.md`](article_writing/adapters/README.md)、[`fixtures/README.md`](fixtures/README.md)
- [x] `build_adapters(mode=mock|live)` + `Live*Port` 桩：后续联调只改 adapter，不改五章
- [x] `tests/test_adapters.py` 覆盖 mock / live-not-ready / 注入 ports

**禁止：** import `Article_repository` / `bioflow` 真实分析代码；增减 V1 未登记字段

**联调约定（已写入 adapters README）：** 外组 JSON 必须在 adapter 内映射为 V1；`WritingPipeline` 只收 `AdapterBundle` / Port。

---

## 成员 C · Introduction + Related Work 空壳

**负责目录：** `article_writing/sections/introduction.py`、`related_work.py`

**任务：**

- [ ] 阅读 [REFERENCES_PHASE1.md](docs/REFERENCES_PHASE1.md) + [TERMINOLOGY.md](docs/TERMINOLOGY.md)（STORM pre-writing、PaperQA evidence）
- [ ] Introduction：从 `PaperBrief` 生成可读 markdown（可仍是模板）
- [ ] Related work：把 `literature` hits 写成条目列表，并填充 `citations` + 带 `evidence_ids` 的 `claims`
- [ ] 缺 brief / 缺文献时写入 `warnings`
- [ ] 各加至少 1 个单测（可读 `fixtures`，断言 markdown 非空、citations 数量）

**禁止：** 实现完整 ReAct（阶段 3）；随意改 contracts；安装 PaperQA/STORM

---

## 成员 D · Data & Method + Result 空壳

**负责目录：** `article_writing/sections/data_method.py`、`result.py`

**任务：**

- [ ] 浏览 [CONTRACTS_V1.md](docs/CONTRACTS_V1.md) 中 `AnalysisBundle` / `SectionDraft` 两节
- [ ] Data & Method：渲染 `analysis.methods`（结构化即可）
- [ ] Result：渲染 `key_findings` / `metrics`，每个 finding 尽量挂 `evidence_ids`；带上 `figure_refs`
- [ ] 缺 analysis 时 `warnings` 明确
- [ ] 各加至少 1 个单测

**禁止：** 直接读原始表达矩阵；调用 ML 库；用文献 RAG 编造结果数字

---

## 成员 E · Conclusion + Orchestrator

**负责目录：** `article_writing/sections/conclusion.py`、`article_writing/orchestrator/`

**任务：**

- [ ] 浏览 [CONTRACTS_V1.md](docs/CONTRACTS_V1.md) 中 `PaperState` / 默认 `section_order`
- [ ] Conclusion：汇总 `PaperState.confirmed_claims` + limitations
- [ ] `WritingPipeline`：按固定顺序跑五章并 `state.add_draft`
- [ ] 保证后章能读到前章 claims（Conclusion 依赖）
- [ ] 补 `tests/test_pipeline.py` 场景（例如改顺序仍导出完整）

**禁止：** 在 orchestrator 里写死章节正文逻辑

---

## 成员 F · Export + 测试基建 + 协作 README

**负责目录：** `article_writing/export/`、`tests/`、进度勾选、`outputs/.gitkeep`

**任务：**

- [ ] 按 [CONTRACTS_V1.md §11](docs/CONTRACTS_V1.md) 确认导出物字段
- [ ] 确认落盘：`writing_bundle.json`、`paper_state.json`、`sections/*.md`
- [ ] 文档写清：可视化板块应读哪些字段（可链到 CONTRACTS_V1）
- [ ] 统一测试命令；合并前跑全量 pytest
- [ ] 汇总每日「能否跑通 demo」状态

**禁止：** 改五章业务逻辑抢 C/D/E 的活（可提 PR 建议）

---

## 协作规则

1. **A 已锁 V1 schema**；缺字段 → 提需求给 A，禁止静默改。  
2. 所有人只依赖 **B 的 fixtures/mock**。  
3. A/B/C 完成外部 README 导读自检后再深入改 Related/fixtures。  
4. 每人 PR 尽量只动自己目录。  
5. 合并标准：相关测试绿 + demo 导出五章文件存在。

## 当前骨架状态

仓库已提供可运行骨架（空壳 + fixtures + pipeline + 基础测试）+ **V1 契约文档**。组员在骨架上迭代完善自己负责部分。
