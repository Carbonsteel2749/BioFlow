# Phase 4 · PaperState 一致性强化

## 目标

在不接其它板块的前提下，对整篇 `PaperState` 做跨章一致性检查，并自动硬化可安全修复的问题（聚合漂移、局限重复），为阶段 5 live 接入提供更稳的导出包。

## 检查项

| kind | 含义 |
|------|------|
| `missing_section` | `section_order` 有章但无 draft |
| `unexpected_draft` | draft 不在 `section_order` |
| `claim_id_conflict` | 同 `claim_id` 不同 statement |
| `claim/citation/figure_aggregate_drift` | 汇总列表与各章 draft 并集不一致 |
| `duplicate_limitations` | limitations 重复/空白 |
| `orphan_conclusion_evidence` | 结论引用了不存在的上游 claim_id |
| `related_evidence_without_citation` | Related claim 的 evidence 无对应 citation |

## 自动硬化（安全修复）

- 按 `section_order` **重建** `confirmed_claims` / `citations` / `figure_refs`
- **去重** `limitations`
- 报告写入 `consistency_report.json` 与 Conclusion 的 `metadata.consistency`

**不改** V1 契约字段。

## 模式

| 模式 | 行为 |
|------|------|
| `warn`（默认） | 修复可修项；残留问题进 warnings |
| `strict` | 残留问题 → `ConsistencyError` |

## 落点

| 项 | 路径 |
|----|------|
| 模块 | `article_writing/consistency/` |
| Pipeline | 五章跑完后 `harden_paper_state` |
| CLI | `--consistency-mode warn\|strict` |
| 测试 | `tests/test_consistency.py` |

## 验收

```bash
cd article_writing
PYTHONPATH=. pytest -q
PYTHONPATH=. python -m article_writing --run-id phase4 --out outputs/phase4 --consistency-mode strict
# 应存在 consistency_report.json，且 ok=true
```

## 本阶段不做

- live adapters（阶段 5）
- 自动生成新大纲替换五章（仅保留固定 order；扩展大纲仍可选）
- LLM 润色
