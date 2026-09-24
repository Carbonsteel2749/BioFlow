# 可重复集成测试矩阵

每个通过结论同时验证退出码、结构化状态、日志关键字和真实产物；UI 显示“完成”不是判定条件。

## 公共双端数据夹具

`fixtures/public_metagenomes.tsv` 固定了 ENA 的三个公开 Illumina paired-end soil metagenome runs：`DRR001456`、`DRR001462`、`DRR001463`（study `PRJDB2729`）。在有网络和足够存储的测试机上运行：

```bash
python3 workflow/tests/helpers/prepare_public_metagenomes.py \
  --catalog workflow/tests/fixtures/public_metagenomes.tsv \
  --output workflow/tests/data/public
```

该命令创建 `single-small.csv`（一个样本，10,000 pairs）、`multi-small.csv`（三个样本，各 10,000 pairs）和 `single-medium.csv`（一个样本，250,000 pairs），并写入 SHA-256 的 `fixture-manifest.tsv`。原始和裁剪数据不提交 Git；每次运行均由该 manifest 固定输入身份。

| 场景 | 输入 | 预期状态／退出码 | 日志关键字 | 必须存在的结果 |
| --- | --- | --- | --- |
| 单样本 | `single-small.csv` | `completed` / 0 | `Nextflow orchestration completed` | validated manifest、tar、trace |
| 多样本 | `multi-small.csv` | `completed` / 0 | `sample=<id>` | 三个样本均在 package 表中 |
| 并行 | 两个独立 task-id 和 outdir | 两个均 `completed` / 0 | 两个 task-id 各自的 log | 两个独立 tar 与 work 目录 |
| 中等规模 | `single-medium.csv` | `completed` / 0 | `r1_reads=250000 r2_reads=250000` | manifest、trace、tar |
| 损坏 FASTQ | 非 gzip 内容伪装为 `.gz` | `failed` / 65 | `stage=fastq_gzip` | 无 validated manifest |
| R1/R2 数量不一致 | 配对数不同 | `failed` / 65 | `stage=fastq_count` | 无 validated manifest |
| 读名不配对 | 相同数量但 core read id 不同 | `failed` / 65 | `stage=fastq_pairing` | 无 validated manifest |
| 文件缺失 | manifest 指向不存在文件 | `failed` / 66 | `stage=manifest_file` | 无 validated manifest |
| 数据库缺失 | 缺失 registry profile 或 sentinel | `failed` / 65 | `database preflight failed` | 无 resolved manifest |
| 磁盘不足 | `--min-free-gb` 大于可用空间 | `failed` / 75 | `insufficient_disk_space` | 无 trace、无 tar |
| 工具异常 | `MOCK_FAIL_TOOL=fastp` | workflow / 1；fastp / 42 | `fastp_failed exit_code=42 stage=tool_execution` | 无 tar，fastp status 为 failed |
| 取消／中断 | `POST /api/tasks/{id}/cancel` | `cancelled` / API 200 | `cancelled by user` | 无结果 tar；未启动步骤为 skipped |
| 重新启动／服务器恢复 | 重建 API、复用状态库 | `paused` | `API restarted while task was running` | 原 task、步骤、参数仍可读取 |
| Nextflow resume | 不改输入的 `--resume` | `completed` / 0 | `checkpoint_hit` | 工具调用计数不增加 |
| 数据库 manifest 改变 | 修改 registry release 后 `--resume` | `completed` / 0 | 新 manifest SHA | taxonomy／functional 重跑，fastp 不重跑 |

本地快速测试使用 `test_preprocessing_validate.sh`、`test_full_workflow_integration.sh` 与 backend pytest 的 mock 工具；公共数据测试用于具备真实数据库和工具的专用执行机。
