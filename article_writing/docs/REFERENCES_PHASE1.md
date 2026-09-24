# Phase 1 外部阅读导读（只读 README，不拉代码）

> 适用对象：**成员 A / B / C**（D/E/F 可选浏览）  
> 原则：统一术语与设计直觉；**禁止**本阶段 `pip install` PaperQA/STORM，禁止 submodule / 复制其源码进仓库。

---

## 必读清单

| 优先级 | 项目 | 链接 | 读什么 | 预计时间 |
|--------|------|------|--------|----------|
| P0 | PaperQA2 | https://github.com/Future-House/paper-qa | README：项目定位、PaperQA2 Algorithm（Search → Gather Evidence → Generate Answer）、与 LangChain 差异说明 | 30–45 min |
| P0 | STORM | https://github.com/stanford-oval/storm | README：Overview、Pre-writing vs Writing、四大模块（Knowledge Curation / Outline / Article Generation / Polishing） | 30–45 min |
| P1 | AutoSurvey（可选） | https://github.com/AutoSurveys/AutoSurvey | README：分节 survey 生成流程即可 | 15–20 min |
| P1 | paper-reader（可选） | https://github.com/jeffchen006/paper-reader | README：Related Work 检索→生成流水线 | 15 min |

读完后请打开同目录 [TERMINOLOGY.md](TERMINOLOGY.md)，用我们自己的字段名说话。

---

## 分角色阅读重点

### 成员 A（接口法官）

- PaperQA：证据如何带 citation；回答如何「有出处」  
- STORM：长文如何先 outline 再分节写  
- 产出：对照 [CONTRACTS_V1.md](CONTRACTS_V1.md)，确认我们字段够用；缺字段走变更流程，不要静默改代码

### 成员 B（fixtures / adapters）

- PaperQA：一篇「检索到的文献」通常含哪些元数据（title/abstract/doi…）  
- paper-reader（可选）：hit 列表如何缓存/去重（概念层即可）  
- 产出：fixtures 字段与 `LiteratureHit` / `AnalysisBundle` 对齐，不引入真 API

### 成员 C（Introduction + Related work）

- STORM：**Pre-writing**（提问、收集参考、出大纲）对 Intro/Related 的启发  
- PaperQA：**Gather Evidence → 再生成**；Related 禁止无文献空写  
- AutoSurvey / paper-reader（可选）：Related 分主题组织的写法  
- 产出：空壳章节仍用模板，但 `citations` / `claims.evidence_ids` 语义与术语表一致

---

## 学什么 / 不学什么

| 学（概念） | 不学 / 本阶段不做 |
|------------|-------------------|
| 先证据后写作 | 安装 PaperQA 当依赖 |
| 引用可追溯（cite_id / snippet） | 复制 STORM 整仓目录 |
| 长文先大纲再填节 | 用 STORM 生成 Result 数字 |
| 分节只吃本节相关上下文 | 一键生成整篇 SCI LaTeX |
| Related 靠检索文献 | Intro/Related 无文献硬写 |

---

## 读后自检（每人打勾）

- [ ] 能用我们自己的词解释 PaperQA 的「evidence」对应什么  
- [ ] 能用我们自己的词解释 STORM 的「pre-writing / outline」对应什么  
- [ ] 清楚 Result/Method **不**走纯文献 RAG 路径  
- [ ] 没有向 `requirements.txt` 添加 PaperQA / knowledge-storm  

术语对照见 [TERMINOLOGY.md](TERMINOLOGY.md)。
