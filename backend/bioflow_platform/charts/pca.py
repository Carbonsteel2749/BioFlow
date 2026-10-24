import numpy as np
from .common import matrix,series
def draw(rows,fig,ax,params):
    x,_=matrix(rows);x-=x.mean(axis=0)
    if params.get('standardize'):
        std=x.std(axis=0);x/=np.where(std>0,std,1)
    u,s,_=np.linalg.svd(x,full_matrices=False)
    if not np.sum(s*s)>0:raise ValueError('所有样本数值相同，无法计算 PCA')
    scores=u[:,:2]*s[:2];ratio=s*s/np.sum(s*s)
    groups={}
    for i,row in enumerate(rows):groups.setdefault(row.get('group') or '全部样本',[]).append(i)
    if len(groups)>30:raise ValueError('PCA 图最多展示 30 个分组')
    for key,indices in groups.items():series(ax,'group:'+key,key,ax.scatter(scores[indices,0],scores[indices,1],s=45))
    ax.set(xlabel=f'PC1 ({ratio[0]:.1%})',ylabel=f'PC2 ({ratio[1]:.1%})',title='PCA')
