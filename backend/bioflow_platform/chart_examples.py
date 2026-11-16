"""Deterministic, chart-specific demonstration files; never task observations."""
import csv,io,math,random
from .charts.registry import get

def example_files(chart):
    entry=get(chart)
    if not entry:raise ValueError('图型不存在')
    rng=random.Random(47);out=io.StringIO();w=csv.writer(out)
    groups='sample,group,batch\n'+''.join(f'S{i+1},{"Control" if i<6 else "Case"},Batch{1+i%2}\n' for i in range(12))
    if chart in {'pca','heatmap'}:
        w.writerow(['sample']+[f'Gene{i+1}' for i in range(8)])
        for i in range(12):w.writerow([f'S{i+1}']+[round(max(.05,rng.gauss(3+(i>=6)*(1 if j<4 else -1)*2,.8)),4) for j in range(8)])
    elif chart=='stacked':
        w.writerow(['sample','taxon','abundance'])
        for i in range(12):
            values=[rng.uniform(.1,1) for _ in range(6)];total=sum(values)
            for j,v in enumerate(values):w.writerow([f'S{i+1}',['Bacteroidaceae','Lachnospiraceae','Ruminococcaceae','Prevotellaceae','Bifidobacteriaceae','Other'][j],round(v/total,5)])
    elif chart=='shannon':
        w.writerow(['depth','group','shannon','lower','upper'])
        for i in range(6):
            for d in [50000,100000,500000,1000000,2500000,5000000,10000000,15000000]:
                y=3+i*.23-.7*math.exp(-d/350000)
                w.writerow([d,f'S{i+1}',round(y,4),round(y-.15*math.exp(-d/700000),4),round(y+.15*math.exp(-d/700000),4)])
    elif chart=='volcano':
        w.writerow(['gene','log2fc','pvalue'])
        for i in range(160):
            x=rng.gauss(0,1.6);w.writerow([f'Gene{i+1}',round(x,3),10**(-abs(x)*rng.uniform(.3,2)-rng.uniform(0,1))])
    elif chart=='enrichment':
        w.writerow(['term','ratio','pvalue','count','direction'])
        for i,name in enumerate(['Immune response','Cell adhesion','Synaptic signaling','Lipid metabolism','Oxidative stress','Membrane transport','Microbial metabolism','Inflammatory response']):w.writerow([name,round(.12+i*.04,2),10**(-1.5-i*.25),5+i*3,'activated' if i%2 else 'suppressed'])
    elif chart=='gsea':
        w.writerow(['position','es','hit']);hits=set(rng.sample(range(1,51),22)+rng.sample(range(51,121),8));score=0
        for i in range(1,121):
            score+=(1/len(hits) if i in hits else -1/(120-len(hits)))
            w.writerow([i,round(score,6),int(i in hits)])
    elif chart=='roc':
        w.writerow(['label','score','model'])
        for name,shift in [('Model A',1.4),('Model B',.9),('Model C',.5)]:
            for label in [0]*35+[1]*35:w.writerow([label,round(1/(1+math.exp(-rng.gauss((label*2-1)*shift,1.2))),5),name])
    elif chart=='ppi':
        w.writerow(['source','target','weight']);names=['TP53','MDM2','BRCA1','RAD51','AKT1','MTOR','EGFR','PIK3CA','MAPK1','STAT3','IL6','TNF','BCL2','CASP3','JUN','FOS'];edges=set()
        for i in range(len(names)):
            for j in [(i+1)%len(names),(i+4)%len(names)]:
                pair=tuple(sorted((i,j)))
                if pair not in edges:edges.add(pair);w.writerow([names[pair[0]],names[pair[1]],round(rng.uniform(.35,.98),2)])
    elif chart=='go':
        w.writerow(['term','count','ontology'])
        terms=['Immune response','Cell adhesion','Metabolic process','Signal transduction','Plasma membrane','Extracellular matrix','Synaptic membrane','Mitochondrion','Protein binding','Enzyme activity','Receptor activity','Transporter activity']
        for i,t in enumerate(terms):w.writerow([t,rng.randint(8,45),['BP','CC','MF'][i//4]])
    elif chart=='venn':
        w.writerow(['set','member'])
        for name,start,end in [('Control',0,85),('Treatment',40,135),('Shared cohort',65,155)]:
            for i in range(start,end):w.writerow([name,f'Gene{i:03}'])
    elif chart=='sankey':
        w.writerow(['source','target','value'])
        for name in ['Bacteroides','Prevotella','Faecalibacterium','Bifidobacterium']:
            for target in ['Carbohydrate metabolism','Amino acid metabolism','Lipid metabolism']:w.writerow([name,target,rng.randint(5,25)])
    elif chart=='boxplot':
        w.writerow(['sample','group','value'])
        for i in range(36):w.writerow([f'S{i+1}','Control' if i<18 else 'Case',round(rng.gauss(3 if i<18 else 4.4,.7),4)])
        groups='sample,group\n'+''.join(f'S{i+1},{"Control" if i<18 else "Case"}\n' for i in range(36))
    else:return entry['example'],groups
    return out.getvalue(),groups
