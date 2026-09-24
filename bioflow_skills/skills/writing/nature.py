"""Writing skills adapted from the Nature Skills project."""

from ..common.nature_reference import NatureSkillDefinition, register_nature_definitions


DEFINITIONS = (
    NatureSkillDefinition(
        "nature-writing", "writing", "根据作者提供的证据起草或重构论文论证、章节和首次投稿材料。",
        ("识别任务、文章类型、章节、语言和目标期刊，默认通用生物医学期刊", "先列出每个主张的证据与边界，再规划段落论证链", "按章节任务起草，结果只保留最短而充分的证据链", "将缺失的事实显式列为待补项而非补写", "交付草稿、主张—证据映射和缺失输入清单"),
        ("不得虚构结果、参数、图表、统计值或文献", "不得把相关性、探索性结果升级为因果结论", "不得将 Nature 风格当作官方投稿规则；期刊现行规定优先"),
        intake_fields=("task_context", "evidence_package", "target_section", "paper_type", "language", "target_journal"),
        routing_axes={"task": ("manuscript", "submission_package"), "paper_type": ("research", "methods", "hypothesis", "algorithmic", "review"), "section": ("abstract", "introduction", "methods", "results", "discussion", "conclusion", "title"), "language": ("en", "zh_to_en"), "journal": ("generic", "nature", "nature_family", "nature_communications")},
        output_contract=("detected routing choices", "draft or argument map", "claim-to-evidence map", "assumptions_or_missing_inputs"),
        quality_checks=("每个数字、统计结论和图表描述可追溯", "主张强度与证据等级一致", "Introduction、Results、Discussion 的研究问题一致", "结果与补充材料分配合理"),
        maturity="full_spec",
    ),
    NatureSkillDefinition(
        "nature-polishing", "writing", "在保留事实、术语和证据边界的条件下润色、翻译或压缩既有学术文本。",
        ("识别文章类型、章节、语言和期刊，默认 generic", "先诊断结构或证据问题，再处理段落逻辑与句子表达", "对于 Results 和 Discussion，核对主文本信息密度与主张边界", "输出修订文本和未能靠语言修复的事实或结构问题"),
        ("不得新增研究结论、数据或引文", "不可用流畅表达掩盖证据不足", "不得改变统计含义、术语或限定语"),
        intake_fields=("task_context", "draft_text", "target_section", "language", "target_journal"),
        routing_axes={"paper_type": ("research", "methods", "hypothesis", "algorithmic", "review"), "section": ("abstract", "introduction", "methods", "results", "discussion", "conclusion", "title"), "language": ("en", "zh_to_en"), "journal": ("generic", "nature", "nature_communications")},
        output_contract=("routing choices", "revised text", "change summary", "unresolved structural or evidence issues"),
        quality_checks=("术语、数值和引用不漂移", "每次修改后复核受影响的主张", "段落目标与所属章节一致"),
        maturity="full_spec",
    ),
    NatureSkillDefinition(
        "nature-reviewer", "writing", "基于给定稿件和证据包进行模拟同行评审，并输出可追溯的问题清单。",
        ("确定审稿范围并冻结同一份稿件与证据包", "从原创性、重要性、广泛读者价值、技术可靠性和可读性评估", "如执行环境可隔离，则用独立上下文分别完成三份评审；否则明确隔离限制", "冻结各报告后再做综合，标记至少两份独立报告重复提出的问题", "按严重程度、阻断性、主张定位和证据定位输出 QA 后的报告"),
        ("不得虚构审稿人身份、实验、引文、图号或行号", "共享上下文不得伪称为互盲审稿", "不得将模拟审稿写成编辑最终决定", "不以凑数量为目的生成问题"),
        intake_fields=("task_context", "manuscript", "evidence_package", "target_journal", "review_scope"),
        routing_axes={"scope": ("full_manuscript", "section", "figures_only"), "reviewers": ("three_blind", "single_bounded")},
        output_contract=("review_setup_and_boundary", "reviewer_reports", "post_review_synthesis", "unsupported_or_not_assessable_items"),
        quality_checks=("每条实质问题有 claim_pointer 和 evidence_pointer", "Major 与 Minor 的严重性一致", "互盲隔离条件真实记录", "综合仅在各报告冻结后进行"),
        maturity="full_spec",
    ),
    NatureSkillDefinition(
        "nature-response", "writing", "起草、审校或修订审稿回复、返修信和逐点修改包。",
        ("从编辑信和审稿意见中提取决定类型、要求文件、期限和可见性规则", "识别 draft、audit、revise、triage、cover_letter 或 revision_package 模式", "为编辑意见和每位审稿人建立逐点追踪表，分别记录行动和已核实的工作状态", "每项修改都映射到稿件位置或 AUTHOR_INPUT_NEEDED 占位", "对回复、修改稿和 cover letter 做一致性审查"),
        ("不得从评论数量或语气推断 Major/Minor Revision", "不得虚构实验、行号、图号、补充材料、编辑要求或已完成修改", "不得将作者回复混入模拟审稿", "稿件改变后必须复核回复中的引用和位置"),
        intake_fields=("task_context", "editor_letter", "reviewer_comments", "manuscript", "verified_change_log", "decision_type"),
        routing_axes={"mode": ("draft", "audit", "revise", "triage", "cover_letter", "revision_package"), "decision": ("minor_revision", "major_revision", "revise_resubmit", "transfer", "unknown")},
        output_contract=("intake_and_decision_summary", "comment_tracker", "point_by_point_responses", "cover_letter_if_requested", "author_input_needed", "package_consistency_report"),
        quality_checks=("每条回复与真实修改或限制对应", "修改稿、干净稿和回复互相一致", "引用的稿件位置在修改后仍有效"),
        maturity="full_spec",
    ),
    NatureSkillDefinition(
        "nature-data", "writing", "起草或审查数据与代码可用性声明、访问路径和 FAIR 元数据。",
        ("确认目标期刊与文章类型", "盘点所有支撑数据集并逐一分类访问路径", "先确定存储库和标识符策略，再起草数据集到位置的映射", "补充数据集引用并审查元数据和 FAIR 要素", "交付可直接粘贴的声明与未解决字段"),
        ("不得编造 DOI、登录号、存储库、许可证、禁运期、伦理审批或访问条件", "没有具体合法限制时不得笼统写 available upon request"),
        intake_fields=("task_context", "dataset_inventory", "code_inventory", "target_journal", "access_restrictions"),
        routing_axes={"access_route": ("public_repository", "controlled_access", "within_paper", "reused_public", "third_party_restricted", "justified_request", "not_applicable")},
        output_contract=("dataset_access_map", "data_availability_statement", "code_availability_statement", "fair_metadata_audit", "unresolved_fields"),
        quality_checks=("每个支撑数据集有明确位置或限制", "所有标识符来自已核验输入", "期刊要求优先"),
        maturity="full_spec",
    ),
    NatureSkillDefinition(
        "researchwrite", "writing", "以证据优先的状态机组织开题报告、研究计划和项目申请的撰写、修订与 QA。",
        ("按输入判定 compose、revise 或 hybrid 模式", "先建立研究硬事实、证据表和论证图，再写章节", "为每一节规定目的、允许主张、禁止主张、输入和验证方式", "按内容诊断、语言清理、自动核验、评分阈值的顺序 QA", "交付当前状态、剩余风险和下一步"),
        ("不得将假设写成已证实结论", "证据缺失、范围失控或专家冲突时应暂停并标记，而非润色掩盖", "不得自动提高主张强度"),
        intake_fields=("task_context", "source_materials", "mode", "scope", "evidence_table"),
        routing_axes={"mode": ("compose", "revise", "hybrid"), "quality_gate": ("proposal", "paper", "internal", "quick")},
        output_contract=("scope", "research_canon", "evidence_table", "argument_map", "section_contracts", "draft_or_revision", "qa_status_and_risks"),
        quality_checks=("论证先于章节", "每个主张有证据或明确状态", "方法可复现、引用与编号一致", "低分维度定向回退且有轮次上限"),
        maturity="full_spec",
    ),
    NatureSkillDefinition(
        "nature-paper-to-patent", "writing", "以论文或发明人材料为依据组织中文技术交底或专利草案。",
        ("识别输入格式、任务模式和发明类型", "为文本、公式、图和代码建立稳定来源编号", "建立特征、术语和证据账本，通过阶段门后再写正式权利要求", "先写权利要求，再让说明书、附图和摘要与其术语和步骤一致", "在交付前运行结构和来源一致性检查"),
        ("不推断发明人、权属、未公开实现细节、优先权或专利有效性", "不将 unsupported 特征写进正式权利要求", "产物不是可专利性、侵权或申请保证意见"),
        intake_fields=("task_context", "source_materials", "task_mode", "invention_type"),
        routing_axes={"source_format": ("pdf", "scanned_pdf", "pasted_text", "mixed"), "task_mode": ("full_draft", "claim_set", "disclosure_analysis", "technical_disclosure", "audit"), "invention_type": ("algorithm", "system", "process_material", "mixed")},
        output_contract=("source_map", "evidence_ledger", "claim_or_disclosure_draft", "to_confirm_items", "validation_report"),
        quality_checks=("每项材料特征可回指来源编号", "正式文件术语和步骤一致", "未支持事实保持待确认状态"),
        maturity="full_spec",
    ),
)

register_nature_definitions(DEFINITIONS)
