"""Discover fixed report deliverables missing from upstream metadata."""
import hashlib,json,threading,uuid
from . import assets
CACHE={};LOCK=threading.Lock()
NAMES={'species_abundance.tsv':'taxonomy.species_abundance','gene_families.tsv':'functional.gene_families','pathway_abundance.tsv':'functional.pathway_abundance','read_counts.tsv':'qc.read_counts'}

def digest(path):
    stat=path.stat();key=(str(path),stat.st_size,stat.st_mtime_ns)
    with LOCK:
        if key not in CACHE:
            h=hashlib.sha256()
            with path.open('rb') as f:
                for block in iter(lambda:f.read(4*1024**2),b''):h.update(block)
            CACHE[key]=h.hexdigest()
        return CACHE[key]

def report_outputs(tid,records):
    base=(assets.analysis_root()/'runtime/outputs'/tid).resolve()
    known={(assets.relative_reference(r.get('path') or r.get('source_file',''),'/runtime/outputs/') or '').removeprefix(tid+'/') for r in records if r.get('path') or r.get('source_file')}
    additions=[]
    for table_dir in base.glob('nodes/*/tables'):
        summary=table_dir.parent/'report/summary.json'
        try:meta=json.loads(summary.read_text())
        except (OSError,ValueError):continue
        if meta.get('task_id')!=tid:continue
        for name,kind in NAMES.items():
            p=(table_dir/name).resolve()
            if not p.is_relative_to(base) or not p.is_file() or not p.stat().st_size:continue
            rel=p.relative_to(base).as_posix()
            if rel in known:continue
            additions.append({'artifact_id':str(uuid.uuid5(uuid.NAMESPACE_URL,tid+':'+rel)),'task_id':tid,'node_id':'report','producer':'bioflow.report-discovery','artifact_type':kind,'file_name':name,'source_file':rel,'path':str(p),'size_bytes':p.stat().st_size,'sha256':digest(p),'status':'active','media_type':'text/tab-separated-values','sample_id':None,'metadata':{'origin':'report_discovery','relative_path':rel,'scope':'cohort'}})
    return additions
