"""Build bundled gallery examples from deterministic synthetic inputs."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if sys.platform!='win32':sys.path.insert(0,str(ROOT/'.runtime/linux'))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import importlib
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--only',nargs='*',default=[]);args=parser.parse_args()
rng=np.random.default_rng(24)
out=ROOT/'frontend/chart-previews';out.mkdir(exist_ok=True)
rows={}
rows['pca']=[dict(sample=f'S{i+1}',group=['Control','Treatment','Recovery'][i//12],**{f'G{j}':float(rng.normal((i//12)*2,1.1)) for j in range(10)}) for i in range(36)]
rows['volcano']=[dict(gene=f'G{i}',log2fc=float(rng.normal(0,1.6)),pvalue=float(10**(-rng.uniform(.05,6)))) for i in range(650)]
rows['heatmap']=[dict(sample=f'S{i+1:02}',**{f'G{j+1:02}':float(rng.normal((i//6-1)*(-1 if j<8 else 1),.7)) for j in range(16)}) for i in range(18)]
terms=['Immune response','Cell adhesion','Signal transduction','Lipid metabolism','DNA repair','Cell cycle','Oxidative stress','Transport']
rows['enrichment']=[dict(term=t,ratio=float(rng.uniform(.08,.4)),pvalue=float(10**(-rng.uniform(1.5,5))),count=int(rng.integers(6,40))) for t in terms]
x=np.linspace(0,1,240);es=.58*np.sin(np.pi*x)*np.exp(-2*x)-.22*np.sin(np.pi*x)**2
rows['gsea']=[dict(position=i+1,es=float(v),hit=int(i in {3,8,15,23,29,35,48,62,81,110,150,180,210})) for i,v in enumerate(es)]
rows['roc']=[dict(label=i%2,score=float(rng.normal((i%2)*sep,1)),model=name) for name,sep in [('Model A',1.8),('Model B',1.15)] for i in range(220)]
proteins=['TP53','MDM2','BRCA1','RAD51','AKT1','EGFR','MYC','PTEN','CDK2','BCL2','JUN','STAT3']
rows['ppi']=[dict(source=proteins[i],target=proteins[(i+1)%12],weight=.8) for i in range(12)]+[dict(source='TP53',target=p,weight=.9) for p in proteins[2:9]]
rows['venn']=[dict(set=name,member=f'G{i}') for name,a,b in [('Control',0,60),('Treatment',25,90),('Recovery',45,110)] for i in range(a,b)]
rows['go']=[dict(term=t,count=35-i*3,ontology=['BP','CC','MF'][i//3]) for i,t in enumerate(terms)]
rows['sankey']=[dict(source=a,target=b,value=int(rng.integers(8,35))) for a in ['Control','Treatment','Recovery'] for b in ['Metabolism','Signaling','Immunity']]
rows['stacked']=[dict(sample=f'S{i}',group=f'Group {i//3+1}',taxon=f'Taxon {j+1}',abundance=float(rng.uniform(.05,1))) for i in range(12) for j in range(7)]
rows['shannon']=[dict(depth=float(d),group=name,shannon=float(scale*(1-np.exp(-d/1400))),lower=float(max(0,scale*(1-np.exp(-d/1400))-.09)),upper=float(scale*(1-np.exp(-d/1400))+.09)) for name,scale in [('Group A',1.6),('Group B',2.1),('Group C',2.6)] for d in np.linspace(100,10000,25)]
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.spines.top':False,'axes.spines.right':False,'axes.prop_cycle':plt.cycler(color=['#246750','#83bda5','#c59d65','#7195ac']),'savefig.facecolor':'white'})
from matplotlib import font_manager
font=Path('/mnt/c/Windows/Fonts/msyh.ttc')
if font.is_file():
    font_manager.fontManager.addfont(str(font));plt.rcParams['font.family']=font_manager.FontProperties(fname=str(font)).get_name()
for chart,data in rows.items():
    if args.only and chart not in args.only:continue
    fig,ax=plt.subplots(figsize=(6,4),layout='constrained')
    importlib.import_module('backend.bioflow_platform.charts.'+chart).draw(data,fig,ax,{})
    ax.set_title('')
    handles,labels=ax.get_legend_handles_labels()
    if handles and not getattr(ax,'_bioflow_custom_legend',False):ax.legend(handles,labels,fontsize=7,frameon=False)
    fig.savefig(out/(chart+'.png'),dpi=120)
    plt.close(fig)
    print(chart)
