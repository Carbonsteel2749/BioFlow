"""Literature skills adapted from the Nature Skills project."""

from ..common.nature_reference import NatureSkillDefinition, register_nature_definitions


DEFINITIONS = (
    NatureSkillDefinition("nature-academic-search", "literature", "制定文献检索、MeSH 策略与引文影响力核查任务。", ("定义检索问题", "构造可复现检索式", "记录来源和筛选依据"), ("检索结果需标注来源与时间",)),
    NatureSkillDefinition("nature-citation", "literature", "建立论断到文献的映射并核验目标期刊范围的引用。", ("拆分论断", "检索并核验来源", "输出引用映射"), ("不得伪造 DOI、作者或引文",)),
    NatureSkillDefinition("nature-downloader", "literature", "组织合法开放获取或用户已授权的全文获取任务。", ("确认合法访问路径", "记录来源与授权", "保存可追溯材料"), ("仅使用开放获取、出版商 API 或用户授权访问",), "requires_setup"),
    NatureSkillDefinition("nature-literature-pipeline", "literature", "组织多来源文献发现、评分、精读和归档流程。", ("检索", "评分筛选", "精读与归档"), ("评分规则和来源必须可追溯",), "requires_setup"),
    NatureSkillDefinition("nature-paper-card", "literature", "生成单篇论文的方法、证据链、局限与研究启发卡片。", ("提取研究问题与方法", "映射实验到结论", "列出局限"), ("不得超出原文证据",)),
    NatureSkillDefinition("nature-reader", "literature", "生成有原文依据的论文精读或中英对照任务。", ("定位原文、图表和公式", "按请求范围解释", "保留来源定位"), ("不生成不存在于原文的内容",)),
    NatureSkillDefinition("nature-ref-verifier", "literature", "多源核验参考文献字段并输出结构化问题报告。", ("逐条匹配元数据", "比较作者、年份、卷期页码", "标记冲突"), ("无法核验时必须标记待确认",)),
)

register_nature_definitions(DEFINITIONS)
