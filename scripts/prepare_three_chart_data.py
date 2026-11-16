from pathlib import Path
import json,csv,hashlib
import numpy as np
import openpyxl
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--source',default=r'D:/迅雷下载/BioFlow_前三类复现资料_20261024');parser.add_argument('--output',default='outputs/three-charts-20261029');args=parser.parse_args()
SOURCE=Path(args.source)
OUT=Path(args.output);OUT.mkdir(parents=True,exist_ok=True)
wb=openpyxl.load_workbook(SOURCE/'R1/tables/SourceData.xlsx',read_only=True,data_only=True)
def rows(name):
    data=list(wb[name].values);return [list(r) for r in data if r and r[0] is not None]
merged=rows('16S_merged_asv');header=merged[0];metadata={str(r[0]):{'group':str(r[1]),'treatment':str(r[2]),'week':str(r[3])} for r in merged[1:]}
assert len(metadata)==len(merged)-1
stacked=[['sample','group','taxon','abundance']]
for row in merged[1:]:
    sample=str(row[0]);m=metadata[sample];group=' / '.join([m['group'],m['treatment'],m['week']])
    values=np.array([float(v) for v in row[4:]],dtype=float)
    assert np.isfinite(values).all() and (values>=0).all() and values.sum()>0
    for taxon,v in zip(header[4:],values/values.sum()):stacked.append([sample,group,str(taxon),float(v)])
with (SOURCE/'R1/inputs/Fig4a_metabolites.csv').open(encoding='utf-8-sig') as f:matrix=list(csv.reader(f))
with (SOURCE/'R1/inputs/Fig4a_metadata.csv').open(encoding='utf-8-sig') as f:meta={r['sample']:r for r in csv.DictReader(f)}
assert len(meta)==len(matrix)-1==39
pca=[['sample','group','label',*matrix[0][1:]]]
for row in matrix[1:]:
    m=meta[row[0]];values=[float(v) for v in row[1:]];assert len(values)==150 and np.isfinite(values).all()
    pca.append([row[0],m['group'],m['label'],*values])
raw=rows('16S_full_asv_table_gnoto')
# The raw workbook has an empty top-left header; preserve it explicitly.
raw_all=list(wb['16S_full_asv_table_gnoto'].values);feature_names=raw_all[0][1:]
raw=[list(r) for r in raw_all[1:] if r[0] is not None]
counts=np.array([[float(v) for v in r[1:]] for r in raw]);assert np.isfinite(counts).all() and (counts>=0).all() and np.equal(counts,np.floor(counts)).all()
counts=counts.astype(np.int64);samples=[str(r[0]) for r in raw]
assert len(set(samples))==len(samples) and all(s in metadata for s in samples)
assert set(samples)==set(metadata)
totals=counts.sum(axis=1);assert (totals>0).all();maximum=int(min(totals));depths=np.unique(np.geomspace(min(100,maximum),maximum,20).round().astype(int))
rng=np.random.default_rng(20261029);repeats=50;bootstrap=500
groups={}
for i,sample in enumerate(samples):
    m=metadata[sample];groups.setdefault(' / '.join([m['group'],m['treatment'],m['week']]),[]).append(i)
shannon=[['depth','group','shannon','lower','upper','n_samples']]
for depth in depths:
    entropies=[]
    for count in counts:
        draws=rng.multivariate_hypergeometric(count,int(depth),size=repeats)
        proportions=draws/float(depth);logs=np.zeros_like(proportions,dtype=float);np.log(proportions,out=logs,where=proportions>0)
        entropies.append(-(proportions*logs).sum(axis=1))
    entropies=np.asarray(entropies)
    for group,indices in groups.items():
        estimates=entropies[indices].mean(axis=1);mean=float(estimates.mean())
        boot=estimates[rng.integers(0,len(indices),size=(bootstrap,len(indices)))].mean(axis=1)
        lower,upper=np.quantile(boot,[.025,.975]);lower=float(lower);upper=float(upper);assert lower<=mean<=upper
        shannon.append([int(depth),group,mean,lower,upper,len(indices)])
result={'stacked':stacked,'pca':pca,'shannon':shannon}
(OUT/'prepared-data.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
manifest={'source_workbook':str(SOURCE/'R1/tables/SourceData.xlsx'),'source_sha256':hashlib.sha256((SOURCE/'R1/tables/SourceData.xlsx').read_bytes()).hexdigest(),'stacked_samples':len(metadata),'stacked_taxa':len(header)-4,'pca_samples':len(pca)-1,'pca_features':150,'asv_features':counts.shape[1],'shannon_groups':len(groups),'group_samples':{g:len(v) for g,v in groups.items()},'depths':[int(v) for v in depths],'subsamples_per_sample_per_depth':repeats,'bootstrap_replicates':bootstrap,'random_seed':20261029,'shannon_log_base':'e','band_method':'95% percentile bootstrap interval of mean rarefied Shannon across sample IDs within each group; each sample Shannon estimated from 50 draws without replacement. No extra rarefaction Monte Carlo uncertainty in interval.','group_definition':'original Group / Treatment / Collection_week'}
(OUT/'data-provenance.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(manifest,ensure_ascii=False))
