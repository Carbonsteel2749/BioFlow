"""Planning contracts for reusable research methods and result dataflow."""

GUIDANCE = """
新增研究能力优先用于需要协变量、批量或多表的任务。上面的连续关联工具只适用于未调整关联；需要校正时不能用它们替代以下方法。
join_tables: dataset_id/right_dataset_id为两份表，left_key/right_key为明确的一对一标识；metadata可用登记索引，普通表不能用自动行号猜测对应。
baseline_table: dataset_id、fields、group_by/group_values、baseline_categorical，报告原始缺失分母和未调整检验。需要调整基线p时请明确用通用程序建模，不能把未调整p称为调整后。
partial_correlation/adjusted_regression: metadata_id指定协变量来源；dataset_id指定暴露，outcome_dataset_id指定结局，为空则用样本表。predictors/outcomes为真实字段/特征列表，矩阵一侧不选列表代表全部特征。
covariates真实协变量；categorical_covariates是其中按类别编码的列，references可指定参考水平。不存在的协变量不能跳过。需要独立观测时程序会请用户确认，不能擅自确认。
partial_correlation默认correlation_method=spearman为秩先变换的偏Spearman。用户要旧脚本“先线性残差再Spearman”时才residual_spearman，二者不能混称。
adjusted_regression支持standard_error=HC3/classical、standardize、transform/outcome_transform=none/log1p/clr。CLR在完整成分上变换后再筛候选，物种与菌株不能重复累加。
默认adjustment=BH、correction_family=all（全家族）或outcome（每结局一族）。用户明确要求不校正才none，不能将原始p当作q。
关联输出associations表：predictor/outcome/effect/metric/n/ci_low/ci_high/p/q/status，analysis_audit记录排除与协变量编码。
Step.inputs引用前面结果，例如{"data":{"step_id":"s2","output":"joined"}}，并在depends_on声明对应步骤。join_tables输出joined/join_audit；关联工具输出associations/analysis_audit；differential输出differential（feature为索引，p/q/median_difference等为列）。
partial_correlation或adjusted_regression可inputs.data使用合并表，此时不设置dataset_id/outcome_dataset_id；可inputs.candidates引用差异结果，以candidate_column=feature、q≤alpha筛候选。
差异筛选后全样本回归只引用candidates，不引用cohort_from；分组与全样本分析须保留各自范围。
association_heatmap和effect_plot用inputs.data接associations；effect_plot.effect_style=forest要求真实CI，无CI时显式lollipop；外部结果用result_predictor/result_outcome/result_effect/result_p/result_q/result_ci_low/result_ci_high映射列，effect_metric标真实含义。
association_network用inputs.left和inputs.right接相邻两层关联表，network_stages命名三层，自动匹配中间层并按q筛选。旧sankey是两层图，不能冒充三层网络。
functional_mode=community要求真实群落总通路；stratified为物种分层特征；只有用户明确同意代理口径时才contribution_sum，必须说明不是HUMAnN群落总量。
参考旧仓库包含LEfSe、p-only、较少协变量与分层合计代理，不能冒充论文ANCOM-BC2/BH方法。当前无R执行器，不得规划不存在的ANCOMBC/LEfSe，不能用普通检验冒充。
"""

GUIDANCE += """
先从研究目标设计可回答的问题，再选择工具或编写程序。多实体、多环节目标必须填写research_design：
{aims:[{id:'a1',question:'具体要回答什么',relation:'association/contribution/prediction/comparison/description',step_ids:['s1']}],synthesis_step:'实际综合结果的步骤ID',interpretation:'本次能解释到什么程度'}。
研究问题不是工具菜单，必须围绕用户点名的变量及其关系；不能附加无关分析，不强制枚举所有两两关系。
多个问题的结果需在综合步骤通过Step.inputs真正汇合，depends_on仅表示执行顺序，不代表读取结果；综合可由custom_analysis按本次方案编写，不能受限于某种固定层数图。
例如菌种—通路—元素—临床评分：依据真实数据区分丰度统计关联、物种分层通路的来源贡献、元素与评分关联；分别检验后综合证据。不能把分层行自带的菌种标签当作已检验的菌种—通路关系，不能把显著性交集称为中介链。
用户未要求因果或中介时以探索性关联解释；当前数据不能回答的环节要在方案中明确口径或请用户选择，不能用文字描述掩盖缺失计算。
优先让准确适用的科学函数承担计算，工具只是可调用能力；需要特定多表连接、条件模型、交集、四层或更多层可视化时，按确认方法编写custom_analysis，并显式声明结果输入/输出和验收要求。
outputs.min_rows只限定实际需要的输出结构，显著结果表允许0行；不得凭空要求至少200行或至少若干显著特征。没有显著关系是合法分析结论，不能用放宽阈值或改变检验范围来凑图。
program要求对应可检验的研究事实；同一requirement_id允许多个check，所有条件必须通过。要求应涵盖真实样本范围、实际方法、校正家族和数据血缘，不以单一行数充当科研正确性。
"""
GUIDANCE += """
检验有效但没有显著结果、没有共同显著交集、没有满足条件的完整路径属于合法的空结果，须保留原阈值、输出空表并明确说明；不能抛出错误强迫修复到有显著性。可视化可显示明确的无发现状态，严禁补造边。
旧关联工具taxa_association/functional_association/diversity_association声明associations和matched_observations；associations保留feature索引和rho/p/q，同时含predictor/outcome/effect/metric/status便于后续复用；没有CI不可画森林图。table_correlation声明correlation与observations，结果列为x/y/n/rho/p，引用时明确映射。
"""
GUIDANCE += """
Step.inputs 与 program.inputs 完全不同：Step.inputs是上游结果绑定{别名:{step_id,output}}；program.inputs只列此程序需要读取的原始文件ID，禁止填步骤ID、文件名或别名。只读取上游结果的综合程序可program.inputs=[]，数据从results[别名]读取；至少有一种真实输入。
"""
GUIDANCE += """
跨结果表的连接必须验证实际ID格式、键唯一性和匹配率；同一对象的来源编码不同（例如完整分类路径和分层行的物种标签）时，在program.method中明确ID提取规则，并输出原标签→标准键映射或匹配审计。未匹配不等于无显著关联，不能填0掩盖；不使用未确认的模糊匹配，不把多个对象合并凑数。
"""
