# 宏基因组前处理工作包验收说明（A—D）

## 1. 交付范围

本验收说明覆盖宏基因组流程前半段：输入校验（A）→ 原始数据质控（B）→ 数据清洗（C）→ 去除人宿主序列（D）。各模块统一使用 `workflow/bin/lib/common.sh` 提供的日志、状态 JSON 与断点续跑机制。

| 工作包 | 模块 | 主要实现文件 | 规范化产物 |
|---|---|---|---|
| A | 输入校验 | `validate.sh`、`validate_manifest.py`、`validate.nf` | `manifest.validated.csv` |
| B | FastQC 原始质控 | `fastqc.sh`、`fastqc.nf` | R1/R2 各一份 HTML 和 ZIP 报告 |
| C | fastp 清洗 | `fastp.sh`、`fastp.nf`、`main.nf` | 两份 clean FASTQ、fastp JSON、fastp HTML |
| D | 去人宿主 | `host_depletion.sh`、`host_depletion.nf`、`main.nf`、`run_pipeline.sh` | 两份 host-removed FASTQ、指标 JSON |

## 2. 工作包 A：输入校验

输入清单必须使用精确表头：

```text
sample_id,read1,read2
```

已实现的校验包括：

- `sample_id` 只允许字母、数字、点、下划线和短横线，且不重复；
- 相对路径按 manifest 所在目录解析，并在校验后的 manifest 中规范化为绝对路径；
- R1/R2 文件必须存在、可读、非空，扩展名限于 `.fastq`、`.fq`、`.fastq.gz`、`.fq.gz`；
- gzip 文件以流式方式读取，可发现截断和 CRC 损坏；
- FASTQ 记录检查 `@` 头、`+` 分隔行、序列/质量长度一致性；
- 双端 read ID、mate 标记和记录数必须一致；
- 日志和状态 JSON 记录失败样本、失败阶段与明确原因；
- `--resume` 仅在成功状态和有效 manifest 同时存在时跳过，否则重新校验。

## 3. 工作包 B：FastQC 原始质控

每个样本的 R1、R2 分别运行 FastQC，产生：

```text
<r1_stem>_fastqc.html
<r1_stem>_fastqc.zip
<r2_stem>_fastqc.html
<r2_stem>_fastqc.zip
```

已实现功能：

- Nextflow 的 `task.cpus` 正确传递给 FastQC `--threads`；
- 检查 `fastqc`、`python3`、`gzip` 依赖以及 gzip 输入完整性；
- 以安全的参数数组执行命令，路径包含空格或 shell 特殊字符时不发生命令注入；
- ZIP 使用 Python 标准库校验，必须可读取且包含 `summary.txt`、`fastqc_data.txt`；
- 工具缺失、命令失败、输入损坏或报告缺失时，日志和状态 JSON 给出具体阶段与原因；
- `--resume` 仅在四份报告均完整时跳过。

## 4. 工作包 C：fastp 清洗

默认参数面向宏基因组双端 reads，均可通过 Nextflow 参数覆盖：

```text
qualified_quality_phred=20
unqualified_percent_limit=40
n_base_limit=5
length_required=50
cut_front=true
cut_tail=true
cut_window_size=4
cut_mean_quality=20
trim_poly_g=true
correction=false
detect_adapter_for_pe=true
```

模块执行双端接头识别、低质量过滤、短序列过滤和 N 碱基控制，输出：

```text
<sample>.R1.clean.fastq.gz
<sample>.R2.clean.fastq.gz
<sample>.fastp.json
<sample>.fastp.html
```

产物先写入临时目录，全部校验成功后才移动到正式输出目录。校验内容包括两个 gzip FASTQ、HTML，以及包含 `summary.before_filtering.total_reads` 与 `summary.after_filtering.total_reads` 的 JSON。日志记录清洗前 reads、清洗后 reads 和保留比例；`--resume` 会在任一产物缺失或损坏时重新执行。

## 5. 工作包 D：去除人宿主序列

默认模式为 `strict_both_unmapped`：Bowtie2 的 SAM 输出只在标准输出流中传给 `samtools view -f 12 -F 2304` 和 `samtools fastq`；只有 R1、R2 均未比对人基因组的 read pair 才保留。流程不会写入或发布 SAM、BAM、CRAM 文件，以降低医院数据中人源信息长期留存的风险。

兼容模式 `concordant_unmapped` 使用 Bowtie2 `--un-conc-gz`，语义是“未形成一致性比对的 pair”，不等同于严格“双端均未比对”，因此不作为默认策略。

每个样本输出：

```text
<sample>.R1.host_removed.fastq.gz
<sample>.R2.host_removed.fastq.gz
<sample>.host_depletion.metrics.json
```

指标 JSON 包含输入、保留、去除的双端 reads 数和比例，Bowtie2 汇总比对率、过滤模式、索引前缀、预设及工具版本。脚本检查索引分片完整性、输入/输出 FASTQ 配对关系、工具依赖和阈值；`--resume` 只有在成功状态、两份 gzip FASTQ 和指标 JSON 均有效时才会跳过。

人基因组索引必须放在项目代码目录外，例如：

```text
/home/xh/databases/host/GRCh38/GRCh38
```

并通过 `--host-index` 或 Nextflow 参数 `--host_index` 传入。`workflow/run_pipeline.sh` 支持转发 `--host-filter-mode`、`--host-min-retained-pairs`、`--host-max-removed-pct` 和 `--host-bowtie2-preset`。

## 6. 已完成的离线验证

已通过脚本级模拟测试覆盖：

- 正常双端 FASTQ 的校验、FastQC、fastp 和去宿主处理；
- manifest 表头错误、重复样本、缺失文件、损坏 gzip、R1/R2 不配对；
- FastQC、fastp、Bowtie2/samtools 缺失或命令失败；
- FastQC/fastp/去宿主输出缺失、损坏或不配对；
- 去宿主索引不完整；
- 完整输出的 `--resume` 跳过，以及输出被删除后的重新执行；
- 包含空格和 shell 特殊字符的路径不发生命令注入；
- 去宿主默认严格模式不产生 SAM/BAM/CRAM 文件。

已运行并通过的脚本级测试包括：

```bash
bash workflow/tests/test_preprocessing_validate.sh
bash workflow/tests/test_preprocessing_fastqc.sh
bash workflow/tests/test_preprocessing_fastp.sh
bash workflow/tests/test_preprocessing_host_depletion.sh
bash workflow/tests/test_command_templates.sh
bash workflow/tests/test_shell_templates.sh
python -m pytest workflow/tests -q
```

## 7. 环境限制与服务器验收

当前开发环境未安装真实 FastQC、fastp、Bowtie2、samtools、Nextflow 或外部 GRCh38 索引，因此真实生信工具的端到端运行未在本地执行。对应的 Nextflow 烟雾测试已准备；环境缺少依赖时会明确输出 `SKIP`，不会伪造成功。

服务器验收前应准备：Java 17、Nextflow、FastQC、fastp、Bowtie2、samtools，以及代码目录外的完整 GRCh38 Bowtie2 索引。随后使用公开或人工构造的小型双端 FASTQ，完整运行 A→B→C→D，并至少制造索引缺失、工具失败和结果文件删除三类故障，确认日志、状态 JSON 和 `--resume` 行为符合预期。
