from matplotlib.path import Path
from matplotlib.patches import PathPatch,Rectangle
from .common import required,number,series
def draw(rows,fig,ax,params):
    required(rows,'source','target','value');values=[number(r,'value') for r in rows]
    if min(values)<0 or sum(values)<=0:raise ValueError('流量必须非负，合计大于零')
    left={};right={}
    for r,v in zip(rows,values):left[r['source']]=left.get(r['source'],0)+v;right[r['target']]=right.get(r['target'],0)+v
    if max(len(left),len(right))>30 or len(rows)>200:raise ValueError('桑基图每层最多 30 个节点和 200 条流向')
    scale=.8/sum(values)
    def layout(groups,x):
        offset=.05;pos={}
        for key,total in groups.items():
            pos[key]=offset;ax.add_patch(Rectangle((x,offset),.04,total*scale,color='#3d7f60'));ax.text(x-.02 if x<.5 else x+.06,offset+total*scale/2,key,ha='right' if x<.5 else 'left',va='center',fontsize=8);offset+=total*scale+.15/max(len(groups),1)
        return pos
    a=layout(left,.1);b=layout(right,.85)
    for row,value in zip(rows,values):
        if not value:continue
        y=a[row['source']];z=b[row['target']];h=value*scale
        vertices=[(.14,y),(.42,y),(.57,z),(.85,z),(.85,z+h),(.57,z+h),(.42,y+h),(.14,y+h),(.14,y)]
        codes=[Path.MOVETO,Path.CURVE4,Path.CURVE4,Path.CURVE4,Path.LINETO,Path.CURVE4,Path.CURVE4,Path.CURVE4,Path.CLOSEPOLY]
        ax.add_patch(PathPatch(Path(vertices,codes),facecolor='#79ab8d',alpha=.45,edgecolor='none'));a[row['source']]+=h;b[row['target']]+=h
    ax.set(title='Sankey · source to target',xlim=(-.2,1.2),ylim=(0,1.1),xticks=[],yticks=[])
