# BioFlow AI Skill 库

`bioflow_skills` 是 BioFlow 第二板块的可发现、可注册、可统一调用的
能力库。`bioflow_ml` 保持为独立机器学习算法库；本目录管理文献、写作、
分析、可视化和跨模块通用的 Agent/Python skill。

## 分类结构

```text
skills/
├── common/          # 通用任务与实验记录
├── literature/      # 检索、阅读、引用核验
├── writing/         # 论文写作、润色、审稿、回复
├── analysis/        # 统计报告审查
└── visualization/   # 科研图与学术汇报
```

每个具体 skill 都继承 `BaseSkill`，具有统一的 `SkillInput`、`SkillResult`
和注册信息。Nature Skills 相关能力均为 BioFlow 的本地适配规则，按其业务
分类放置在以上目录中；它们参考
<https://github.com/Yuan1z0825/nature-skills>，但不包含或运行上游文件。

## 查看目录

```bash
python3 -c "from bioflow_skills import print_catalog; print_catalog()"
```

## 调用 Agent 型 skill

```python
from bioflow_skills import run_skill

result = run_skill(
    "nature-writing",
    {"task_context": "基于已验证的结果起草 Methods。"},
)
```

这类 skill 返回标准化 `agent_handoff`；它不会在本进程中擅自执行模型、浏览器
或外部脚本。后续由写作工作流读取该交接任务并执行。
