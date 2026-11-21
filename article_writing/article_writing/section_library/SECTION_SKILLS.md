# Section library and section skills

论文章节不再写死。章节库和文章类型模板都是可编辑的 JSON：

| 文件 | 作用 |
|---|---|
| `sections.json` | 全部可选章节：标题、所在位置（front/body/back）、写作方式、负责的 Skill、写作先后（`write_rank`）、依赖、是否需要引用 |
| `article_types.json` | 文章类型模板：每种类型默认包含哪些章节、按什么顺序 |

用户生成稿件时可以传 `article_type`（用模板默认章节）或 `sections`（自己勾选并排序的章节列表）。
排版顺序 = 用户给的顺序；写作顺序 = 按 `write_rank` 自动排序（先写有数据的章节，摘要等概括性章节最后写）。

## 三种写作方式（`mode`）

- `builtin`：原有 7 章（abstract / introduction / methods / results / discussion / conclusion / back_matter），沿用现有流程。
- `skill`：由 `skill` 字段指定的 bioflow_skills Skill 撰写。**Skill 未注册时**，稿件里保留占位
  `[SKILL_PENDING: <skill名>] <章节说明>`，并在警告和 `/api/section-library` 的 `status: skill_missing` 中标出。
- `author_input`：系统无法得知的信息（伦理、作者贡献、资助等），固定输出 `[AUTHOR_INPUT_NEEDED: ...]`，不让模型编造。

## 新增一个章节 Skill（给添加 Skill 的同事）

1. 在 `bioflow_skills/skills/writing/` 下实现并 `@register_skill`，`name` 与 `sections.json` 里该章节的 `skill` 字段一致，
   并在 `bioflow_skills/skills/writing/__init__.py` 中导入。
2. **输入**（`SkillInput.payload`，由写作流程统一提供）：

| 键 | 内容 |
|---|---|
| `section` | 章节 id，如 `data_records` |
| `section_spec` | 该章节在 `sections.json` 中的完整配置（含 `purpose`） |
| `brief` | `{title, research_question}` |
| `ledger` | `bio-fact-ledger` 的事实台账（`facts` 列表，每条有 id/label/text/value/source） |
| `cards` | `reference-cards` 的文献卡片（引用标记写成 `[@Key]`） |
| `materials` | 分析结果包的材料检查报告（样本、表格清单、缺失项） |
| `written` | 已经写好的章节 `{section_id: markdown}`（按 write_rank 先写的章节） |
| `model` | 大模型客户端，`generate(prompt, system=None)`；支持时有 `generate_json(prompt, system, schema)`；可能为 `None` |

3. **输出**（`self.success(data=...)`）：

| 键 | 必填 | 内容 |
|---|---|---|
| `markdown` | 是 | 章节正文（不含章节标题）。引用写 `[@Key]`，只能用 `cards` 里的 key |
| `warnings` | 否 | 需要作者复核的问题列表 |
| `citations` | 否 | 本章用到的引用 key |

4. 规则：数字只能来自 `ledger`；系统没有的信息写 `AUTHOR_INPUT_NEEDED`，不要编造；
   可复用已有 Skill：`section-planner` / `paragraph-writer` / `claim-verifier`（逐段写 + 逐句核对）。

注册后无需改写作流程代码，重启写作服务即可生效；`/api/section-library` 中该章节状态会变为 `ready`。
