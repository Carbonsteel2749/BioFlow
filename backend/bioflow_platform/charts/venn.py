from matplotlib.patches import Circle
from .common import required,series
def draw(rows,fig,ax,params):
    required(rows,'set','member');sets={}
    for r in rows:sets.setdefault(r['set'],set()).add(r['member'])
    if len(sets) not in {2,3}:raise ValueError('韦恩图需要 2 或 3 个集合')
    names=list(sets);positions=[(-.45,0),(.45,0),(0,.7)];colors=['#558d70','#6e91b5','#c8a464']
    for i,name in enumerate(names):
        circle=Circle(positions[i],.85,color=colors[i],alpha=.35);ax.add_patch(circle);series(ax,'set:'+name,name,circle)
    all_members=set.union(*sets.values());counts={mask:0 for mask in range(1,2**len(names))}
    for member in all_members:counts[sum(2**i for i,n in enumerate(names) if member in sets[n])]+=1
    spots={1:(-.85,-.05),2:(.85,-.05),3:(0,-.3),4:(0,1.05),5:(-.4,.5),6:(.4,.5),7:(0,.3)} if len(names)==3 else {1:(-.7,0),2:(.7,0),3:(0,0)}
    for mask,where in spots.items():ax.text(*where,str(counts[mask]),ha='center',va='center',fontsize=14)
    ax.set(title='Venn · schematic areas',xlim=(-1.5,1.5),ylim=(-1,1.7),xticks=[],yticks=[]);ax.set_aspect('equal')
