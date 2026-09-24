# fixtures

假上游数据（阶段 1 起）。字段必须符合 `docs/CONTRACTS_V1.md`。

阶段 2 证据绑定的**坏样例**在 `tests/test_evidence.py` 内构造，不污染下列主 fixtures。

| 文件 | 对应模型 | 用途 |
|------|----------|------|
| `paper_brief.json` | `PaperBrief` | 研究背景 / 课题简介 |
| `analysis_bundle.json` | `AnalysisBundle` | 假分析结论（方法、发现、指标、图表） |
| `literature_hits.json` | `List[LiteratureHit]` | 假文献列表 |
| `figures/*` | 被 `figure_refs.path` 引用 | 占位图/表路径 |
| `biollm_metagenome/package/` | BioLLM 结果包夹具 | live `AnalysisPort` 映射回归 |
| `biollm_metagenome/paper_brief.json` | `PaperBrief` | 与上面宏基因组包配对的课题简介 |

## 自检

```bash
cd article_writing
PYTHONPATH=. python -m article_writing --validate-fixtures
```

## 替换为 live 时

不必删 fixtures。把 `adapters.mode` / `--adapter-mode` 改为 `live`，并实现 `Live*Port`。  
fixtures 仍可用于回归测试。
