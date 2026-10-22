# BioFlow 完整部署包

本版使用 `dist/BioFlow.tar.gz`，解压目录为 `BioFlow/`。包内包含保留的三个 SRR/ERR 任务的真实原始测序文件、分析结果、图表、完整归档及项目数据库。启动方法以根目录 `README.md` 为准。

```bash
tar -xzf BioFlow.tar.gz
cd BioFlow
bash Start-BioFlow.sh check
bash Start-BioFlow.sh start
```

现有服务器：`219.224.3.96`，账户 `xh`。网站地址 `http://219.224.3.96:8800/`，登录用户名和密码均为 `developer`。

分析代码与项目文件从包内读取；独立分析 API 监听回环端口 8803，写作服务监听 8765。Python 使用 `/home/xh/BioLLM_RawDataAnalysis/.venv/bin/python`；生信工具、参考数据库、Ollama 与文献服务复用服务器环境。

清单中的 reads 使用相对路径；成果索引使用相对输出路径；SQLite 中的运行路径使用 `@runtime/` 标识。移动文件夹后自动解析到新的根目录，并验证原始文件后恢复数据集绑定。用户项目的关联 ID 保留。

资料列表读取轻量索引，具体文件在查看和下载时读取。完整归档经过登录与项目归属检查，可从项目资料下载。三个原始归档的文件大小分别为 1,261,793,883、8,179,669,416 和 55,123,854 字节。

`MANIFEST.json` 包含逐文件 SHA256 与大小；压缩包旁有 `.sha256` 校验文件。包内数据库快照已清除登录会话。
