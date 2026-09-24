# 项目进展记录

## 已完成

- [x] 建立 SQLite 元数据存储层，支持单条/批量插入、DOI 更新、关键词检索和标签检索。
- [x] 建立基础打标签流水线，支持 MockTagger 与 QwenTagger 两种路径。
- [x] 建立 PubMed 检索式生成器，支持 strict / balanced / broad 三种模式。
- [x] 建立示例数据插入脚本和打标签脚本。

## 已接入的真实能力

- [x] PubMed E-utilities 检索接口已接入，能够生成查询并抓取文章元数据。
- [x] QwenTagger 已改为优先接入 OpenAI 兼容接口；若服务不可达，可回退到 MockTagger。
- [x] 数据库存储路径已统一到项目根目录下的 literature_db.sqlite3，避免多份 DB 分叉。

## 未完成

- [ ] 真正的 PubMed 增量更新逻辑（按日期/时间窗口继续抓）
- [ ] 真实大模型标签质量评估与结果校验
- [ ] API/前端服务接口封装
- [ ] 检索结果排序、分页优化和多源去重
- [ ] 真实多源（OpenAlex / Semantic Scholar / Crossref）接入
