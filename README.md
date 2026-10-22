# BioFlow

工作台已提供统一登录与注册，游客点击功能按钮会打开账户窗口。项目和资源归属当前账户；开发者账户可以维护服务配置。开发者项目保留全部含 SRR 或 ERR 编号的 3 条历史任务，其他历史分析记录与旧稿件已归档。

## 启动方法

本包用于现有服务器 `219.224.3.96`，使用 `xh` 账户。代码、项目数据库、原始测序文件、图表和结果归档均在 `BioFlow/` 内，通过相对路径定位。Python、Nextflow、参考数据库、文献服务和 Qwen3-14B 复用服务器已有环境。

```bash
cd /home/xh/BioFlow
bash Start-BioFlow.sh check
bash Start-BioFlow.sh start
bash Start-BioFlow.sh status
```

浏览器打开 `http://219.224.3.96:8800/`。用户名：`developer`；密码：`developer`。关闭 SSH 窗口后服务继续运行。包内分析服务监听 `127.0.0.1:8803`，读取本包的 `BioLLM_RawDataAnalysis/runtime/`；统一入口监听 `8800`。原服务器的分析服务可以继续独立运行。

查看日志和停止本目录启动的服务：

```bash
tail -n 80 runtime/logs/server96.log
bash Start-BioFlow.sh stop
```

`check` 会显示 Python、包内数据、模型和文献服务的检查结果。页面访问需要服务器开放 TCP 8800。文件夹可以放在其他目录，启动脚本自动识别当前根目录。更新时请保留已有的项目状态和数据。

数据目录对应关系：`http://219.224.3.96:8800/http://219.224.3.96:8800/`

```text
BioFlow/runtime/bioflow.sqlite3                         账户、项目、资源关联
BioFlow/runtime/artifact-catalog/                       轻量成果索引
BioFlow/BioLLM_RawDataAnalysis/runtime/incoming/         样本清单和真实 FASTQ
BioFlow/BioLLM_RawDataAnalysis/runtime/outputs/          分析结果、图表和完整归档
BioFlow/BioLLM_RawDataAnalysis/runtime/state/tasks.sqlite3 分析任务、样本和成果登记
BioFlow/runtime/assets/                                项目上传资料
BioFlow/article_writing/outputs/ui-sessions/            写作草稿
```

样本清单中的 reads 路径相对清单所在目录保存；成果索引相对分析输出目录保存；分析数据库以 `@runtime/` 保存路径，在读取时解析到当前目录。启动时按包内校验记录重新绑定样本文件，迁移后保留原任务和数据集关联。完整结果归档通过登录后的项目入口下载。

资料列表读取持久化目录索引，先显示标题、类型、来源和已登记大小。项目文件及成果使用相对路径和存储根目录标识定位。索引后台同步，具体文件在查看或下载时读取、检查；页面显示“打开时检查文件”表示尚未验证当前文件内容。

## Windows 本地启动

推荐 Python 3.12。首次准备环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

仅启动工作台，连接已在运行的后端：

```powershell
.\start.ps1
```

同时启动分析和写作 API：

```powershell
.\start.ps1 -WithServices
```

打开 [BioFlow 工作台](http://127.0.0.1:8800)。窗口中的停止操作会结束该启动器创建的服务。已在运行的后端保持独立管理。

本次环境已准备运行依赖，也可双击根目录 `Start-BioFlow.cmd` 启动工作台、分析 API 与写作 API。

Windows 的分析后端通过 Ubuntu WSL 启动，Python 依赖可在 WSL 中使用 `pip install -r requirements.txt` 安装；本次联调依赖保存在 `.runtime/linux`。完整生信分析仍使用原模块的 Nextflow、Java、算法工具和参考数据库配置。写作模型在工作台“写作 → 模型配置”中设置。

文献知识库继续使用原有 Go + Qdrant 服务。在 Docker 已启动的环境中，仅启动后端与数据库：

```powershell
docker compose -f Article_repository/ai-localbase/docker-compose.dev.yml up -d --build qdrant backend
```

服务位于其他机器时，在启动前设置地址：

```powershell
$env:BIOFLOW_ANALYSIS_URL = 'http://127.0.0.1:8000'
$env:BIOFLOW_WRITING_URL = 'http://127.0.0.1:8765'
$env:BIOFLOW_LITERATURE_URL = 'http://127.0.0.1:8080'
.\start.ps1
```

已设置地址的服务由原有部署管理。统一入口默认监听 `127.0.0.1:8800`。

## 文件结构

```text
BioFlow/
├─ ai4s-interface.html          统一页面结构
├─ frontend/
│  ├─ app.js                   实际接口交互、项目与功能页面
│  ├─ workflows.js             流程节点、参数与端口编辑
│  ├─ enhancements.js          多样本上传与章节自动保存
│  ├─ auth.js                  游客入口与账户窗口
│  ├─ library.js               模型、Skills 与项目概览
│  ├─ studio.css               科研工作台视觉样式
│  ├─ layout.css               统一页面基础布局
│  └─ theme.css                深绿与浅绿主题
├─ backend/bioflow_platform/
│  ├─ app.py                   同源服务转发、项目与 Skills API
│  ├─ auth.py                  密码、会话与登录校验
│  ├─ access.py                资源归属与操作权限
│  ├─ store.py                 项目、任务关联和操作记录
│  └─ writing_api.py           复用写作接口，移除旧页面路由
├─ scripts/
│  ├─ start.py                 统一启动与进程管理
│  └─ linux_analysis.py        WSL 分析 API 入口
├─ tests/                      统一入口集成测试
├─ docs/
│  ├─ INTEGRATION.md           接口对应关系与验证范围
│  └─ archive/                 原界面原型与历史设计资料
├─ runtime/                    项目数据库和启动日志
├─ Article_repository/         文献服务及原有数据
├─ BioLLM_RawDataAnalysis/      分析与绘图服务、原始输入和结果
├─ article_writing/            论文写作业务与稿件
├─ bioflow_skills/              注册能力库
├─ requirements.txt
└─ start.ps1
```

原模块目录、数据、独立 Git 仓库和既有输出路径继续使用。旧前端退出统一启动流程；写作模块 `frontend/` 中的服务、会话与导出代码继续作为后端依赖，静态页面保留作历史源码。

## 使用

登录并创建项目后，在“资料”新建知识库，上传文献；切换“测序数据”上传双端 FASTQ，建立样本清单。在“分析”中提交清单或关联自己的已有任务。图表页读取任务中的真实结果表，生成绘图版本并提供预览和下载。写作页选择分析任务生成七章稿件，再逐章保存、确认、修改和导出。开发者还可选用本地分析包。能力库分别列出模型与 Skills，并提供用途分类；Agent 型能力展示交接任务及原始执行状态。

账户、服务端会话、项目与资源归属保存在 `runtime/bioflow.sqlite3`。同一账户可以把自己的知识库关联到多个项目。

前后端未完全覆盖的功能见 [对接核对报告](docs/FRONTEND_AUDIT.md)，服务器条件与运行步骤见 [服务器运行说明](docs/SERVER_DEPLOYMENT.md)。保留的 3 条 SRR/ERR 任务的原始文件、成果和完整归档已放入包内。

验证统一入口：

```powershell
.\.venv\Scripts\python -m pip install pytest
.\.venv\Scripts\python -m pytest tests -q
```
