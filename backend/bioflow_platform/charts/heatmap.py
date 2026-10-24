from .common import matrix
def draw(rows,fig,ax,params):
    from scipy.cluster.hierarchy import linkage,leaves_list
    x,keys=matrix(rows)
    if x.shape[0]>150 or x.shape[1]>150:raise ValueError('聚类热图最多展示 150×150，请先筛选矩阵')
    r=leaves_list(linkage(x,method='average',metric='euclidean'));c=leaves_list(linkage(x.T,method='average',metric='euclidean'))
    image=ax.imshow(x[r][:,c],aspect='auto',cmap='viridis');fig.colorbar(image,ax=ax,label='输入数值')
    ax.set_xticks(range(len(c)),[keys[i] for i in c],rotation=60,ha='right');ax.set_yticks(range(len(r)),[rows[i]['sample'] for i in r]);ax.set(title='Clustered heatmap',xlabel='Feature',ylabel='Sample')
