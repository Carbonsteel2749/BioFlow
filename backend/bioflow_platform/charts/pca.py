import numpy as np
import matplotlib.pyplot as plt
from .common import matrix,series
def draw(rows,fig,ax,params):
    x,_=matrix(rows)
    if params.get('pca_log1p',False):
        if np.any(x<0):raise ValueError('log1p 转换要求特征数值非负')
        x=np.log1p(x)
    x-=x.mean(axis=0)
    if params.get('standardize'):
        std=x.std(axis=0);x/=np.where(std>0,std,1)
    u,s,_=np.linalg.svd(x,full_matrices=False)
    if not np.sum(s*s)>0:raise ValueError('所有样本数值相同，无法计算 PCA')
    scores=u[:,:2]*s[:2];ratio=s*s/np.sum(s*s)
    groups={}
    for i,row in enumerate(rows):groups.setdefault(row.get('group') or '全部样本',[]).append(i)
    if len(groups)>30:raise ValueError('PCA 图最多展示 30 个分组')
    colors=plt.get_cmap(params.get('series_palette','tab20'))(np.linspace(0,1,len(groups)))
    for (key,indices),color in zip(groups.items(),colors):series(ax,'group:'+key,key,ax.scatter(scores[indices,0],scores[indices,1],s=params.get('pca_point_size',45),color=color))
    if params.get('pca_show_labels',False):
        for row,point in zip(rows,scores):ax.annotate(row.get('label') or row['sample'],point,xytext=(4,4),textcoords='offset points')
    ax.set(xlabel=f'PC1 ({ratio[0]:.1%})',ylabel=f'PC2 ({ratio[1]:.1%})',title='PCA')
