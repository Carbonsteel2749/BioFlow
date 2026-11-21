# BioFlow 八个章节的技能补充记录

日期：2026-11-19。新增 6 个 GitHub 原版技能和 8 个 BioFlow 本地章节适配器，均已发布。

截图中的“待补 Skill”来自章节注册表；仅导入上游 Markdown 不会自动注册 Python 写作器。本轮保留原版固定提交、许可证和资源文件，再实现与现有输入输出协议匹配的适配器。适配器明确标记为本地编写，不冒称 GitHub 同名原包。

## 已导入的 GitHub 原版

| 技能 | 来源 | 固定提交 |
|---|---|---|
| exploratory-data-analysis | [K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/exploratory-data-analysis/SKILL.md) | ec59a29a03d5 |
| database-lookup | [K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/database-lookup/SKILL.md) | ec59a29a03d5 |
| lamindb | [K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/lamindb/SKILL.md) | ec59a29a03d5 |
| datalad | [K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/datalad/SKILL.md) | ec59a29a03d5 |
| get-available-resources | [K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/get-available-resources/SKILL.md) | ec59a29a03d5 |
| scientific-benchmarking | [acgetchell/dotfiles](https://github.com/acgetchell/dotfiles/blob/1eff96244b283a681668e93d666077763cd4794f/agents/.agents/skills/scientific-benchmarking/SKILL.md) | 1eff96244b28 |

## 章节对应关系

| 章节 | 注册名称 | 参考技能 |
|---|---|---|
| 结果与讨论 | `results-discussion-writer` | [scientific-writing](https://github.com/K-Dense-AI/scientific-agent-skills/blob/a1043ef544e3a100c28e70a736b6cf32554d0677/skills/scientific-writing/SKILL.md)、[statistical-analysis](https://github.com/K-Dense-AI/scientific-agent-skills/blob/a1043ef544e3a100c28e70a736b6cf32554d0677/skills/statistical-analysis/SKILL.md)、[exploratory-data-analysis](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/exploratory-data-analysis/SKILL.md) |
| 实现 | `implementation-writer` | [scientific-writing](https://github.com/K-Dense-AI/scientific-agent-skills/blob/a1043ef544e3a100c28e70a736b6cf32554d0677/skills/scientific-writing/SKILL.md)、[get-available-resources](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/get-available-resources/SKILL.md) |
| 基准测试与评估 | `benchmarking-writer` | [scientific-benchmarking](https://github.com/acgetchell/dotfiles/blob/1eff96244b283a681668e93d666077763cd4794f/agents/.agents/skills/scientific-benchmarking/SKILL.md)、[statistical-analysis](https://github.com/K-Dense-AI/scientific-agent-skills/blob/a1043ef544e3a100c28e70a736b6cf32554d0677/skills/statistical-analysis/SKILL.md) |
| 应用示例 | `application-examples-writer` | [lamindb](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/lamindb/SKILL.md)、[database-lookup](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/database-lookup/SKILL.md) |
| 可用性与运行要求 | `availability-requirements-writer` | [get-available-resources](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/get-available-resources/SKILL.md)、[datalad](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/datalad/SKILL.md) |
| 数据来源与构建 | `database-construction-writer` | [database-lookup](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/database-lookup/SKILL.md)、[datalad](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/datalad/SKILL.md)、[lamindb](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/lamindb/SKILL.md) |
| 数据库内容 | `database-content-writer` | [lamindb](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/lamindb/SKILL.md)、[exploratory-data-analysis](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/exploratory-data-analysis/SKILL.md) |
| 数据库使用 | `database-usage-writer` | [database-lookup](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/database-lookup/SKILL.md)、[lamindb](https://github.com/K-Dense-AI/scientific-agent-skills/blob/ec59a29a03d5c40f73ab40878df04860bc00e876/skills/lamindb/SKILL.md) |

## 验收与入口

- 新增章节和原有 Data Descriptor、章节对话相关测试合计 45 项通过。测试使用受控模型替身，覆盖 8 个章节实际注册与调用、缺失材料、虚构数字、虚构引用，以及分析 QC 不被误写为基准测试。未生成或验证一篇完整真实研究论文。
- 6 个原包和 8 个适配器的 164 个已登记文件通过 SHA-256 校验；格式检查无错误。原版按固定提交的 Git blob 校验后导入。
- 截图对应的章节功能位于 `/home/xh/BioFlow-writing`，运行服务为 8766，页面入口为 [章节写作版本](http://219.224.3.96:8806/)。8 个章节在服务接口均返回 `ready`。
- 原有 [总系统技能库](http://219.224.3.96:8800/?skill_center=/skills) 使用共享技能中心，因此也可查询本轮新增包。8800 原有写作界面没有此章节选择功能，本轮未把 8806 的其他开发改动整体部署到 8800。
- “就绪”表示写作器可调用，不代表研究材料完整。缺失材料保留 `AUTHOR_INPUT_NEEDED`；不编造基准结果、应用案例、数据库条目数、许可或 API。模型数字或引用检查失败时返回来源事实并提示核对。
- 写作服务沿用原进程的解释器、参数与环境，仅重启章节版本的 8766 服务。

## 保存与回退

共享技能库：`/home/xh/BioFLow/skill_center/storage`。来源记录：`source-verification-20261119/`。

运行模块：`/home/xh/BioFlow-writing/bioflow_skills/skills/writing/section_extensions.py`。注册入口：同目录 `__init__.py`。

数据库导入前备份：`/home/xh/BioFLow/skill_center/storage/backups/section-skills-20261119/before-import.sqlite`。运行注册入口备份与前后章节目录：`/home/xh/BioFlow-writing/runtime/backups/section-skills-20261119/`。

验收明细见同目录 `acceptance.json`、`import-report.json`、`section-adapters.json` 和 `gateway-verification.json`。
