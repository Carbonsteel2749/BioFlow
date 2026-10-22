# BioFlow Skills 目录

> 本目录对应运行时注册表。新增 Skill 后，需通过 `@register_skill` 注册，并在 `skills/__init__.py` 的导入链中加载；程序启动后目录会自动更新。

| Skill | 分类 | 功能 | 主要输入 | 主要输出 | 版本 |
| --- | --- | --- | --- | --- | --- |
| `check_parameters` | common | 按规则校验参数有效性。 | parameters、rules | is_valid、errors | 1.0.0 |
| `data_summary` | common | 汇总结构化输入的字段名、数据类型与集合长度。 | 任意 payload | fields | 0.1.0 |
| `file_profile` | common | 识别输入文件类型，并返回基础文件信息。 | path | path、suffix、format、exists、size_bytes | 0.1.0 |
| `file_type_identifier` | common | 按文件扩展名识别文件类型。 | file_path | file_type | 1.0.0 |
| `organize_task_info` | common | 将任务信息整理为可读任务单。 | task_info | formatted_string | 1.0.0 |
| `summarize_input` | common | 简要描述字符串、列表、字典等输入。 | data | summary、data_type | 1.0.0 |
| `task_brief` | common | 整理研究目标、约束、输入和预期产物。 | objective | objective、constraints、inputs、expected_artifacts | 0.1.0 |

## 查看动态目录

在 `/home/xh/BioFLow` 下运行：

```bash
python3 -c "from bioflow_skills import print_catalog; print_catalog()"
```

获得可供前端/API 使用的结构化目录：

```python
from bioflow_skills import list_catalog

catalog = [entry.to_dict() for entry in list_catalog()]
```

统一调用 Skill：

```python
from bioflow_skills import run_skill

result = run_skill("file_profile", {"path": "data/example.xlsx"})
```
