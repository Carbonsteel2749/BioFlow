# 代码绘图（待当前分析结束后启用）

支持 Python `.py` 与 R `.r` / `.R`，输入 CSV/TSV；生成 PNG 后在页面预览、下载。

Python 示例：
```python
import os
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
x = pd.read_csv(os.environ['BIOFLOW_INPUT'])
plt.plot(x.iloc[:, 0], x.iloc[:, 1])
plt.savefig(Path(os.environ['BIOFLOW_OUTPUT']) / 'figure.png', dpi=180)
```

R 示例：
```r
x <- read.csv(Sys.getenv('BIOFLOW_INPUT'))
png(file.path(Sys.getenv('BIOFLOW_OUTPUT'), 'figure.png'), width=1200, height=800)
plot(x[[1]], x[[2]])
dev.off()
```

当前仅开发者账户运行代码。执行使用 Bubblewrap 隔离：无网络、只读工具环境、独立任务目录；最长60秒，代码512KB、输入20MB、单文件输出25MB，预览最多12张PNG。绘图库缺失或代码报错显示在预览区域。

已验证服务器 Python/Matplotlib 与 R 4.5.3 能在隔离环境生成 PNG。R 环境位于 `/home/xh/.local/share/mamba/envs/biollm-humann`，已安装 ggplot2。

用户选择等待“PRJEB33098 九样本宏基因组测试”完成后启用。接口源码已接入，当前运行进程尚未加载；前端显示待启用。确认没有活动分析后，按原部署方式重启服务即可加载，不应在分析运行时重启。
