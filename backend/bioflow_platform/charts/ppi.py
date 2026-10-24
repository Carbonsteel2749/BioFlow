import numpy as np
from .common import required,number
def draw(rows,fig,ax,params):
    required(rows,'source','target');nodes=sorted({r[k] for r in rows for k in ('source','target')})
    if len(nodes)>150 or len(rows)>1000:raise ValueError('网络预览最多 150 个节点和 1000 条边')
    coordinates={n:(np.cos(i*2*np.pi/len(nodes)),np.sin(i*2*np.pi/len(nodes))) for i,n in enumerate(nodes)}
    for row in rows:
        weight=number(row,'weight') if row.get('weight') else 1
        if weight<0:raise ValueError('互作权重不能为负')
        a,b=coordinates[row['source']],coordinates[row['target']];ax.plot([a[0],b[0]],[a[1],b[1]],color='#b6cabb',lw=min(4,.5+weight),zorder=1)
    ax.scatter([p[0] for p in coordinates.values()],[p[1] for p in coordinates.values()],s=100,color='#397653',zorder=2)
    for n,(x,y) in coordinates.items():ax.text(x*1.1,y*1.1,n,ha='center',va='center',fontsize=8)
    ax.set(title='PPI · imported interactions',xticks=[],yticks=[],xlim=(-1.3,1.3),ylim=(-1.3,1.3));ax.set_aspect('equal')
