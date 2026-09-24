# Phase 3 · Related Work ReAct

## 目标

在**不安装 PaperQA / STORM**、不接其它板块真实服务的前提下，为 Related Work 增加可落盘的 Thought → Action → Observation 循环，经 `LiteraturePort`（mock）多步取证后再写草稿，并继续通过阶段 2 证据绑定。

## 设计来源（只学流程）

| 参考 | 学到的点 | 我们的落点 |
|------|----------|-----------|
| PaperQA | Search → Gather Evidence → Generate | `search` / `get` → 草稿生成 |
| STORM | 多视角 / 多 query 预写作 | `build_search_queries(PaperBrief)` |

**禁止：** `pip install paper-qa` / `knowledge-storm`；禁止章节直接 import 外组代码。

## 做了什么

| 项 | 落点 |
|----|------|
| 轨迹模型 | `article_writing/react/trajectory.py` |
| Port 工具薄封装 | `article_writing/react/tools.py` |
| 确定性循环 | `article_writing/react/loop.py`（无 LLM） |
| Pipeline | `react_enabled=True`（默认）；Related 章先跑 ReAct 再写作 |
| 导出 | `react_trajectory.json` + draft.metadata.react |
| CLI | `--react` / `--no-react` / `--react-max-steps` |
| 测试 | `tests/test_react.py` |

V1 契约字段未改；轨迹进 `SectionDraft.metadata` 与独立 JSON。

## 验收

```bash
cd article_writing
PYTHONPATH=. pytest -q
PYTHONPATH=. python -m article_writing --run-id phase3 --out outputs/phase3
# 应存在 outputs/phase3/react_trajectory.json 与五章 markdown
PYTHONPATH=. python -m article_writing --run-id phase3-off --out outputs/phase3-off --no-react
```

## 本阶段不做

- 真 live 文献 API（阶段 5）
- LLM 选择下一步 Action（仍可用确定性规划；LLM 精修另议）
- 用 ReAct 生成 Result 数字
- PaperState 跨章一致性强化（阶段 4）
