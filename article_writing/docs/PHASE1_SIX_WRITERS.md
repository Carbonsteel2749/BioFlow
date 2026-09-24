# 写作三任务 → 六人拆分（阶段 1）

对应原先一句话任务：

| 原任务 | 含义 |
|--------|------|
| C | 把背景和相关工作两章从假数据写成草稿 |
| D | 把方法和结果两章从假分析包写成草稿 |
| E | 把五章按顺序串起来，并写结论章 |

现拆成 **6 人**，每人只动自己的目录；假数据已由成员 B 提供，**禁止改** `contracts.py`（找 A）、**禁止改** 别人的章节文件。

共用约定：

- 输入来自 mock：`fixtures/` + `adapters/`（不要 import 其它板块）
- 输出必须符合 `SectionDraft`（见 `docs/CONTRACTS_V1.md`）
- 自测：`PYTHONPATH=. pytest -q`（至少覆盖自己加的测试）
- 假数据够用即可；缺字段找 A，缺假数据找 B

---

## 成员 1 · Introduction（原 C 的一半）

| 项 | 内容 |
|----|------|
| **文件夹** | `article_writing/sections/introduction.py` |
| **测试建议** | `tests/test_introduction.py`（新建） |
| **做什么** | 读 `SectionInput.brief`（`PaperBrief`），生成 Introduction 的 `markdown`；写出至少 1 条 `Claim`；缺 `research_question` 时写入 `warnings` |
| **不做什么** | 不写 Related；不调文献检索；不改 orchestrator |
| **完成标准** | 单跑该章 markdown 非空；有对应单测 |

可参考：`docs/TERMINOLOGY.md` 里 Introduction 一行。

---

## 成员 2 · Related Work（原 C 的一半）

| 项 | 内容 |
|----|------|
| **文件夹** | `article_writing/sections/related_work.py` |
| **测试建议** | `tests/test_related_work.py`（新建） |
| **做什么** | 读 `SectionInput.literature`（假文献列表），写成 Related Work 草稿；填充 `citations`；每条重要陈述用 `Claim.evidence_ids` 挂上 `paper_id`；无文献时 `warnings` |
| **不做什么** | 不上 ReAct / 真检索（阶段 3）；不改 `adapters/` |
| **完成标准** | 有 citations；claims 带 evidence_ids；有单测 |

可参考：`docs/REFERENCES_PHASE1.md`（PaperQA/STORM 只读概念）。

---

## 成员 3 · Data & Method（原 D 的一半）

| 项 | 内容 |
|----|------|
| **文件夹** | `article_writing/sections/data_method.py` |
| **测试建议** | `tests/test_data_method.py`（新建） |
| **做什么** | 读 `SectionInput.analysis.methods`（及 `brief.data_modality`），渲染方法章 markdown；可带上 analysis 里的 `figure_refs`（若与方法相关）；无 analysis 时 `warnings` |
| **不做什么** | 不读原始表达矩阵；不写 Result 发现；不调用 ML |
| **完成标准** | methods 内容出现在正文；有单测 |

---

## 成员 4 · Result（原 D 的一半）

| 项 | 内容 |
|----|------|
| **文件夹** | `article_writing/sections/result.py` |
| **测试建议** | `tests/test_result.py`（新建） |
| **做什么** | 读 `analysis.key_findings` / `metrics` / `figure_refs`；每个 finding 尽量变成带 `evidence_ids` 的 `Claim`；正文引用图表 id/caption |
| **不做什么** | 不用文献 RAG 编数字；不改 Method 章 |
| **完成标准** | findings + metrics 进 markdown；claims/figure_refs 非空（在有假分析包时）；有单测 |

---

## 成员 5 · Conclusion（原 E 的一半）

| 项 | 内容 |
|----|------|
| **文件夹** | `article_writing/sections/conclusion.py` |
| **测试建议** | `tests/test_conclusion.py`（新建） |
| **做什么** | 读传入的 `PaperState`：汇总 `confirmed_claims` + `limitations`（可含 analysis.limitations）；生成 Conclusion markdown；不要重复发明 Result 里没有的数据 |
| **不做什么** | 不负责调五章顺序（那是成员 6）；不改其它四章 |
| **完成标准** | 在 pipeline 跑完后 Conclusion 能看到前文章 claims；有单测（可构造简单 `PaperState`） |

---

## 成员 6 · 串联五章（原 E 的一半）

| 项 | 内容 |
|----|------|
| **文件夹** | `article_writing/orchestrator/`（主改 `pipeline.py`） |
| **兼看** | `article_writing/export/`（确认导出仍可用）；`tests/test_pipeline.py` |
| **做什么** | 保证顺序：`introduction → related_work → data_method → result → conclusion`；每章跑完 `state.add_draft`；Conclusion 能读到前文章状态；`run_and_export` 仍产出五章 md + `writing_bundle.json` |
| **不做什么** | 不在 orchestrator 里写各章正文；不改 contracts |
| **完成标准** | `PYTHONPATH=. python -m article_writing --out outputs/phase1` 导出五章；`test_pipeline` 绿 |

说明文档：`article_writing/adapters/README.md`（只用 mock ports，不要接 live）。

---

## 六人与文件夹一览

```text
成员1  article_writing/sections/introduction.py
成员2  article_writing/sections/related_work.py
成员3  article_writing/sections/data_method.py
成员4  article_writing/sections/result.py
成员5  article_writing/sections/conclusion.py
成员6  article_writing/orchestrator/  (+ 盯 export / test_pipeline)
```

```text
假数据（已有，只读）     fixtures/ + adapters/
字段规范（已有，只读）   contracts.py + docs/CONTRACTS_V1.md
```

---

## 协作顺序建议

1. 1–4 可并行改各自章节  
2. 成员 5 可先写 Conclusion 逻辑，再用成员 6 的 pipeline 联调  
3. 成员 6 最后守「整包能跑通」  
4. 合并前全员：`PYTHONPATH=. pytest -q`

骨架里五章和 pipeline **已有空壳可跑**；六人任务是在空壳上**写实、补测试、补 warnings**，不是从零建文件。
