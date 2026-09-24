# Phase 2 · 证据强绑定

## 目标

在不依赖其它板块真实服务、不改 V1 契约字段的前提下，让 **Related / Method / Result / Conclusion** 的 `Claim.evidence_ids` 可检查、可降级、可严格失败。

Introduction 仍允许空 `evidence_ids`（阶段 2 策略）。

## 做了什么

| 项 | 落点 |
|----|------|
| 分章策略 | `article_writing/evidence/policy.py` |
| 校验 + 应用 | `article_writing/evidence/binding.py` |
| Pipeline 钩子 | `WritingPipeline(..., evidence_mode=warn\|strict)` 每章 `run` 后调用 `apply_evidence_binding` |
| CLI | `--evidence-mode warn\|strict` |
| 测试 | `tests/test_evidence.py` |

**不改** `contracts.py` 字段；绑定结果写入：

- `SectionDraft.warnings`（前缀 `evidence binding:`）
- `SectionDraft.metadata["evidence_binding"]`
- 若原 `draft_status==complete` 且绑定失败 → 改为 `degraded`

## 证据 id 可解析池

一条 `evidence_id` 可来自：

- `SectionInput.literature[].paper_id`
- `AnalysisBundle.evidence_ids` / `figure_refs[].figure_id`
- 当前草稿或 `PaperState` 的 `citations` / `figure_refs`
- `PaperState.confirmed_claims[].claim_id`（供 Conclusion 引用上游论断）

## 模式

| 模式 | 行为 |
|------|------|
| `warn`（默认） | 记录 warning，不中断导出 |
| `strict` | 任一绑定问题 → `EvidenceBindingError` |

## 验收

```bash
cd article_writing
PYTHONPATH=. pytest -q
PYTHONPATH=. python -m article_writing --run-id phase2 --out outputs/phase2 --evidence-mode warn
PYTHONPATH=. python -m article_writing --run-id phase2-strict --out outputs/phase2-strict --evidence-mode strict
```

标准 fixtures 下两种模式均应通过。

## 本阶段不做

- 真 RAG / 真检索
- ReAct（阶段 3）
- LLM 精修
- live adapters（阶段 5）
- 把 PaperQA / STORM 装进依赖
