"""Execute explicit stages; ASV IDs derive from sequence hashes, not local column order."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess

def fasta(path):
    records={};name=None
    for line in Path(path).read_text().splitlines():
        if line.startswith('>'):
            name=line[1:].strip()
            if not name or name in records:raise ValueError('FASTA identifiers must be unique')
            records[name]=''
        elif line.strip():
            if name is None:raise ValueError('Invalid FASTA')
            records[name]+=line.strip().upper()
    if not records or any(not seq or set(seq)-set('ACGTN-') for seq in records.values()):raise ValueError('Invalid DNA sequences')
    return records

def validate(plan):
    node=plan['node_type'];p=plan['parameters'];i=plan['inputs']
    if node=='amplicon_primers' and not p['primers_removed']:
        import re
        if not all(re.fullmatch('[ACGTRYSWKMBDHVNacgtryswkmbdhvn]{6,100}',p[k]) for k in ['primer_forward','primer_reverse']):raise ValueError('请提供测序方向的前后向 IUPAC 引物序列')
    if node=='amplicon_filter' and not p['primers_removed']:raise ValueError('请先完成并确认引物去除；不能把有引物的 reads 直接送入去噪')
    if node=='amplicon_filter' and p['trunc_len_forward']+p['trunc_len_reverse']<p['expected_amplicon_max_length']+p['min_overlap']:raise ValueError('截断后双端长度不足以覆盖声明的最长扩增片段并保留最小重叠；请核对区域和截断参数')
    if node=='amplicon_merge' and p['min_amplicon_length']>p['max_amplicon_length']:raise ValueError('扩增片段长度下限不能超过上限')
    if node=='amplicon_alignment':return {'sequences':len(fasta(i['sequences']))}
    if node=='amplicon_tree':
        records=fasta(i['alignment'])
        if len(records)<3 or len({len(s) for s in records.values()})!=1:raise ValueError('建树需要至少三条等长比对序列')
    if node=='amplicon_unifrac':
        from bioflow_ml.analysis_nodes import table
        from skbio import TreeNode
        counts=table(i['table'],p['unit']);tree=TreeNode.read(i['tree'],convert_underscores=False)
        if len(tree.children)!=2:raise ValueError('UniFrac 要求明确有根树')
        tips=[t.name for t in tree.tips()]
        if len(set(tips))!=len(tips) or not set(counts.columns).issubset(tips):raise ValueError('树 tip 与 ASV 表不匹配')
        if any(n.length is None or n.length<0 for n in tree.postorder() if n.parent is not None):raise ValueError('UniFrac 树需要非负分支长度')
    return {'node':node,'input_check':'declared files and stage-specific constraints; RDS objects checked by R worker'}

def execute(plan,out,work,scripts):
    p=plan['parameters'];i=plan['inputs'];node=plan['node_type'];validate(plan)
    if node=='amplicon_primers':
        from bioflow_ml.runtime_worker import records
        rows=records(i['manifest']);normalized=[]
        for row in rows:
            if p['primers_removed']:normalized.append(row);continue
            left=out/(row['sample_id']+'.R1.fastq.gz');right=out/(row['sample_id']+'.R2.fastq.gz');log=out/(row['sample_id']+'.cutadapt.json')
            subprocess.run([plan['tools']['cutadapt']['executable'],'-g','^'+p['primer_forward'],'-G','^'+p['primer_reverse'],'--discard-untrimmed','--pair-filter','any','-j',str(p['threads']),'--json',str(log),'-o',str(left),'-p',str(right),row['read1'],row['read2']],check=True)
            stats=json.loads(log.read_text())['read_counts']
            if not stats['input'] or stats['output']/stats['input']<p['min_retained_fraction']:raise ValueError('引物匹配保留率过低，请核对区域、引物方向及原始数据')
            normalized.append({'sample_id':row['sample_id'],'read1':str(left),'read2':str(right)})
        with (out/'manifest.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['sample_id','read1','read2']);writer.writeheader();writer.writerows(normalized)
        (out/'region-check.json').write_text(json.dumps({'declared_region':p['region'],'primer_match_checked':not p['primers_removed'],'primer_removal_user_confirmed':p['primers_removed'],'region_identity_verified':False,'note':'Primer matching and downstream merged-length checks assess consistency; region identity still requires a trusted primer design/reference.'},indent=2))
    elif node=='amplicon_silva':
        record=plan['databases']['16s.silva'];config=work/'silva.dcf';values={**i,**p,'training_fasta':str(Path(record['path'])/record['training_file']),'node':'amplicon_taxonomy','output':str(out)}
        config.write_text('\n'.join(k+': '+str(v) for k,v in values.items())+'\n');subprocess.run([plan['tools']['Rscript']['executable'],str(scripts/'dada2_worker.R'),str(config)],check=True)
    elif node=='amplicon_alignment':
        with (out/'aligned.fasta').open('w') as f:subprocess.run([plan['tools']['mafft']['executable'],'--thread',str(p['threads']),'--auto',i['sequences']],check=True,stdout=f)
        fasta(out/'aligned.fasta')
    elif node=='amplicon_tree':
        from skbio import TreeNode
        with (out/'unrooted.nwk').open('w') as f:subprocess.run([plan['tools']['FastTree']['executable'],'-nt','-gtr',i['alignment']],check=True,stdout=f)
        tree=TreeNode.read(str(out/'unrooted.nwk'),convert_underscores=False);root=tree.root_at_midpoint();root.write(str(out/'rooted.nwk'))
    elif node=='amplicon_unifrac':
        from bioflow_ml.analysis_nodes import table
        from skbio import TreeNode
        from skbio.diversity import beta_diversity
        counts=table(i['table'],p['unit']);metric='weighted_unifrac' if p['weighted'] else 'unweighted_unifrac'
        distance=beta_diversity(metric,counts.to_numpy(dtype='int64'),ids=counts.index.tolist(),taxa=counts.columns.tolist(),tree=TreeNode.read(i['tree'],convert_underscores=False))
        frame=distance.to_data_frame();frame.index.name='sample_id';frame.to_csv(out/'distances.csv')
    else:
        config=work/'amplicon-stage.dcf';config.write_text('\n'.join(k+': '+str(v) for k,v in {**i,**p,'node':node,'output':str(out)}.items())+'\n')
        subprocess.run([plan['tools']['Rscript']['executable'],str(scripts/'amplicon_stages.R'),str(config)],check=True)
        if node=='amplicon_chimera':
            records=fasta(out/'asv_sequences.fasta');mapping={k:'ASV_'+hashlib.sha256(v.encode()).hexdigest() for k,v in records.items()}
            with (out/'asv_table.csv').open(newline='') as f:rows=list(csv.reader(f))
            rows[0]=[mapping.get(k,k) for k in rows[0]]
            with (out/'asv_table.csv').open('w',newline='') as f:csv.writer(f).writerows(rows)
            (out/'asv_sequences.fasta').write_text(''.join('>'+mapping[k]+'\n'+seq+'\n' for k,seq in records.items()))
    (out/'stage-methods.json').write_text(json.dumps({'node':node,'parameters':p,'source_revision_id':plan['source_revision_id'],'extension_revision_id':plan['extension_revision_id'],'rooting':'midpoint assumption; not an outgroup-supported biological root' if node=='amplicon_tree' else None,'real_execution':True},indent=2))
