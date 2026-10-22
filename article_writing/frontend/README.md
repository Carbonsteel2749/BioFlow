# 论文撰写板块 · 前端

独立目录：`article_writing/frontend/`（与 Python 包 `article_writing/` 分离）。

## 功能（第一版）

1. **分章生成**初稿（工作台 → 素材接入 → 分章撰写 → 排版导出）
2. **逐章编辑**：手写改 Markdown，或与本地大模型对话改写
3. **逐章确认**后，才能 **统一排版导出**（Markdown / Word `.docx` / PDF）

主题：绿白。Word / PDF 经本机 **LibreOffice** 从排版 HTML 转换生成。

## 启动

在板块根目录：

```bash
cd /home/xh/BioFLow/article_writing
pip install -r requirements.txt -r frontend/requirements.txt
PYTHONPATH=. python3 -m uvicorn frontend.server:app --host 127.0.0.1 --port 8765
```

浏览器打开：http://127.0.0.1:8765/

对话改写默认走本机 Ollama `qwen3:14b`（与其它 BioFLow 板块一致）。

打开页面后按流程使用：

1. **工作台**：看分析 / 文献 / Qwen 是否在线，打开最近会话  
2. **素材接入**：填 BioLLM 任务或结果包、文献检索词、课题简介 JSON，可先预览再生成  
3. **分章撰写**：逐章编辑、对话润色、确认  
4. **排版导出**：七章都确认后导出 Markdown / Word / PDF  

「用夹具生成样例稿」会填入 `fixtures/biollm_metagenome/`，不依赖真实分析任务。
