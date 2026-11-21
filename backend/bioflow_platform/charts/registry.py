VERSION='1.0.0'
CHARTS=[
 {'id':'pca','name':'PCA 图','category':'样本关系与降维','columns':'sample,feature_1,feature_2（可选 group）','example':'sample,feature_1,feature_2,group\nS1,1,2,A\nS2,2,1,A\nS3,5,7,B\nS4,7,5,B\n','description':'行是样本，列是数值特征；中心化后计算 PCA，可选标准化。'},
 {'id':'volcano','name':'火山图','category':'差异分析','columns':'gene,log2fc,pvalue','example':'gene,log2fc,pvalue\nG1,2,0.001\nG2,-3,0.002\nG3,0.2,0.5\n','description':'导入已有差异结果；pvalue 为本次选定的 P 值或校正后 P 值，不重新进行差异检验。'},
 {'id':'heatmap','name':'聚类热图','category':'样本关系与降维','columns':'sample,feature_1,feature_2…','example':'sample,G1,G2,G3\nS1,1,2,3\nS2,2,1,4\nS3,8,7,2\n','description':'数值矩阵；欧氏距离、平均连接进行描述性行列聚类。'},
 {'id':'enrichment','name':'富集气泡图','category':'功能富集','columns':'term,ratio,pvalue,count','example':'term,ratio,pvalue,count\nPathway A,0.2,0.001,10\nPathway B,0.1,0.03,5\n','description':'展示已有富集结果，气泡大小为条目计数，颜色为 -log10(P)。'},
 {'id':'gsea','name':'GSEA 图','category':'功能富集','columns':'position,es（可选 hit）','example':'position,es,hit\n1,0.2,1\n2,0.4,1\n3,0.3,0\n4,0.1,0\n5,0,0\n','description':'展示导入的排序位置与运行富集分数；不推断 NES 或显著性。'},
 {'id':'roc','name':'ROC 曲线图','category':'预测与评价','columns':'label,score（可选 model）','example':'label,score,model\n0,0.1,Model A\n1,0.8,Model A\n0,0.4,Model A\n1,0.6,Model A\n','description':'真实二分类标签与连续预测分数；计算 ROC 与 AUC，不自动训练模型。'},
 {'id':'ppi','name':'PPI 网络图','category':'网络与关联','columns':'source,target（可选 weight）','example':'source,target,weight\nTP53,MDM2,0.9\nTP53,BRCA1,0.8\nBRCA1,RAD51,0.7\n','description':'展示已有蛋白互作边表；圆形布局，保留来源边信息，不自动查询互作数据库。'},
 {'id':'venn','name':'韦恩图','category':'集合与组成','columns':'set,member','example':'set,member\nA,G1\nA,G2\nB,G2\nB,G3\n','description':'2–3 个集合；显示精确交集数量，示意圆面积不代表集合大小。'},
 {'id':'go','name':'GO 富集直方图','category':'功能富集','columns':'term,count（可选 ontology）','example':'term,count,ontology\nimmune response,12,BP\nmembrane,8,CC\nbinding,5,MF\n','description':'按 BP/CC/MF 分类展示已有 GO 富集条目计数。'},
 {'id':'sankey','name':'桑基图','category':'网络与关联','columns':'source,target,value','example':'source,target,value\nGroup A,Pathway 1,10\nGroup A,Pathway 2,5\nGroup B,Pathway 1,8\n','description':'展示两层来源—去向的非负流量；每条带宽按输入数值绘制。'},
 {'id':'lefse','name':'LEfSe LDA 图','category':'差异分析','columns':'taxon,lda_score,group','example':'taxon,lda_score,group\nFeature A,3.8,Control\nFeature B,3.1,Treatment\nFeature C,2.6,Control\nFeature D,2.2,Treatment\n','description':'展示已有 LEfSe 分析结果；横轴为 LDA score，颜色表示富集组别，不自行执行差异检验或计算 LDA 分数。'},
]

def get(identifier):
    return next((c for c in CHARTS if c['id']==identifier),None)
