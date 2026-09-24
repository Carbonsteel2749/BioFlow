import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIS = ROOT / "bin" / "visualization"

def run(script, args, expected=0):
    result = subprocess.run([sys.executable, str(VIS / script), *map(str, args)], cwd=VIS, capture_output=True, text=True)
    assert result.returncode == expected, result.stderr + result.stdout

def write(path, header, rows):
    path.write_text("\t".join(header) + "\n" + "\n".join("\t".join(map(str,row)) for row in rows) + ("\n" if rows else ""), encoding="utf-8")

TAX = ["sample_id","taxid","taxonomy","taxonomy_rank","taxonomy_system","read_count_or_estimated_reads","abundance","abundance_unit","classification_source","database_profile","database_release"]
FUN = ["sample_id","function_source","function_namespace","function_id","function_name","function_type","abundance","abundance_unit","relative_abundance","normalization_method","evidence_level","analysis_scope","database_profile","database_release"]
MAG = ["cohort_id","mag_id","taxonomy","completeness","contamination"]
ABUND = ["sample_id","mag_id","detected","abundance","abundance_unit","coverage","mapped_reads","mapping_method","detection_threshold","association_meaning"]

def taxonomy_rows():
    return [["S1","1","TaxA","species","NCBI",10,.8,"fraction_of_reads","Kraken2+Bracken","test","r1"],["S1","2","TaxB","species","NCBI",2,.2,"fraction_of_reads","Kraken2+Bracken","test","r1"],["S2","1","TaxA","species","NCBI",4,.4,"fraction_of_reads","Kraken2+Bracken","test","r1"],["S2","2","TaxB","species","NCBI",6,.6,"fraction_of_reads","Kraken2+Bracken","test","r1"]]
def functional_rows(kind, namespace):
    return [["S1","HUMAnN",namespace,"F1","Feature 1",kind,.2 if kind == 'pathway_coverage' else 2,"HUMAnN_reported","","raw","reads","community_total","test","r1"],["S2","HUMAnN",namespace,"F1","Feature 1",kind,.3 if kind == 'pathway_coverage' else 3,"HUMAnN_reported","","raw","reads","community_total","test","r1"]]

def test_functional_top_n_keeps_samples_without_selected_positive_features(tmp_path, monkeypatch):
    import importlib.util
    import pandas as pd
    import matplotlib.pyplot as plt
    monkeypatch.syspath_prepend(str(VIS))
    spec = importlib.util.spec_from_file_location('tested_functional_plots', VIS/'functional_plots.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    labels = {}
    def capture(figure, path):
        axis = figure.axes[0]
        labels[path.name] = {'x': [t.get_text() for t in axis.get_xticklabels()], 'y': [t.get_text() for t in axis.get_yticklabels()]}
        plt.close(figure)
    monkeypatch.setattr(module, 'save_figure', capture)
    for kind in ['pathway_coverage', 'ko']:
        rows = functional_rows(kind, 'MetaCyc' if kind == 'pathway_coverage' else 'KO')
        rows[0][6] = .9; rows[1][3] = 'F2'; rows[1][6] = .8
        unknown = list(rows[0]); unknown[0] = 'S3'; unknown[3] = 'UNMAPPED'; unknown[6] = 1
        result = module.render(pd.DataFrame(rows+[unknown], columns=FUN), kind, 1, tmp_path)
        assert labels[f'functional_{kind}_top1_heatmap.png']['y'] == ['S1', 'S2', 'S3']
        assert result['samples_without_named_signal'] == ['S3']
        if kind == 'ko':
            assert labels['functional_ko_top1_composition.png']['x'][-1] == 'S3 (N/A)'

def test_functional_special_rows_are_not_ranked_as_functions_and_coverage_is_not_composition(tmp_path):
    args=[]
    for flag, kind, namespace in [('gene-families','gene_family','UniRef90'),('ko','ko','KO'),('ec','ec','EC'),('pathway-abundance','pathway_abundance','MetaCyc'),('pathway-coverage','pathway_coverage','MetaCyc')]:
        path=tmp_path/f'{flag}.tsv'
        rows=functional_rows(kind,namespace)
        special=list(rows[0]); special[3]='UNMAPPED'; special[6]=999
        write(path,FUN,rows+[special]); args += ['--'+flag,path]
    out=tmp_path/'out'; run('functional_plots.py',args+['--outdir',out])
    result=json.loads((out/'functional_plots.provenance.json').read_text())
    assert result['analysis_version'] == '2.0'
    assert result['plots']['ko']['selected_feature_ids'] == ['F1']
    assert result['plots']['ko']['special_entries']['S1']['UNMAPPED'] == 999
    coverage=result['plots']['pathway_coverage']
    assert coverage['composition']['status'] == 'skipped'
    assert coverage['display_transform'] == 'identity (0-1 coverage)'
    assert not (out/'functional_pathway_coverage_top20_composition.png').exists()

def test_taxonomy_normal_empty_missing_duplicate_illegal_single(tmp_path):
    source=tmp_path/"taxonomy.tsv"; out=tmp_path/"out"; write(source,TAX,taxonomy_rows()); run("taxonomy_plots.py",["--input",source,"--outdir",out]); assert (out/"taxonomy_top20_stacked.png").is_file(); assert (out/"taxonomy_bray_curtis_pcoa.png").is_file()
    write(source,TAX,[]); run("taxonomy_plots.py",["--input",source,"--outdir",out]); assert json.loads((out/"taxonomy_plots.provenance.json").read_text())["plots"]["pcoa"]["status"] == "skipped"
    write(source,TAX[:-1],[]); run("taxonomy_plots.py",["--input",source,"--outdir",out],65)
    write(source,TAX,[taxonomy_rows()[0],taxonomy_rows()[0]]); run("taxonomy_plots.py",["--input",source,"--outdir",out],65)
    row=taxonomy_rows()[0]; row[6]="-1"; write(source,TAX,[row]); run("taxonomy_plots.py",["--input",source,"--outdir",out],65)
    write(source,TAX,[taxonomy_rows()[0]]); run("taxonomy_plots.py",["--input",source,"--outdir",out]); assert json.loads((out/"taxonomy_plots.provenance.json").read_text())["plots"]["pcoa"]["status"] == "skipped"

def test_functional_normal_empty_missing_duplicate_illegal_single_and_no_attribution(tmp_path):
    paths=[]
    for label,kind,space in (("gene","gene_family","UniRef90"),("ko","ko","KO"),("ec","ec","EC"),("path","pathway_abundance","MetaCyc"),("coverage","pathway_coverage","MetaCyc")):
        path=tmp_path/f"{label}.tsv"; write(path,FUN,functional_rows(kind,space)); paths.append(path)
    out=tmp_path/"out"; args=["--gene-families",paths[0],"--ko",paths[1],"--ec",paths[2],"--pathway-abundance",paths[3],"--pathway-coverage",paths[4],"--outdir",out]; run("functional_plots.py",args); assert (out/"functional_ko_top20_heatmap.png").is_file()
    for path,(kind,space) in zip(paths,[("gene_family","UniRef90"),("ko","KO"),("ec","EC"),("pathway_abundance","MetaCyc"),("pathway_coverage","MetaCyc")]): write(path,FUN,[functional_rows(kind,space)[0]])
    run("functional_plots.py",args); assert (out/"functional_pathway_abundance_top20_composition.png").is_file()
    write(paths[0],FUN,[])
    run("functional_plots.py",args); assert json.loads((out/"functional_plots.provenance.json").read_text())["plots"]["gene_families"]["heatmap"]["status"] == "skipped"
    write(paths[0],FUN[:-1],[]); run("functional_plots.py",args,65)
    write(paths[0],FUN,[functional_rows("gene_family","UniRef90")[0]]*2); run("functional_plots.py",args,65)
    bad=functional_rows("gene_family","UniRef90")[0]; bad[6]="bad"; write(paths[0],FUN,[bad]); run("functional_plots.py",args,65)
    bad=functional_rows("gene_family","UniRef90")[0]; bad[3]="F1|TaxA"; write(paths[0],FUN,[bad]); run("functional_plots.py",args,65)

def test_mag_normal_empty_missing_duplicate_illegal_single_and_no_mag(tmp_path):
    mag=tmp_path/"mag.tsv"; abundance=tmp_path/"abundance.tsv"; out=tmp_path/"out"
    write(mag,MAG,[["cohort-A","M1","TaxA",95,2],["cohort-A","M2","TaxB",70,4]]); write(abundance,ABUND,[["S1","M1","true",2,"reported","","","mapping",">0","read mapping"],["S2","M1","true",4,"reported","","","mapping",">0","read mapping"]]); args=["--mag-annotation",mag,"--sample-mag-abundance",abundance,"--outdir",out]; run("mag_plots.py",args); assert (out/"sample_mag_abundance_heatmap.png").is_file()
    write(mag,MAG,[]); run("mag_plots.py",args); assert json.loads((out/"mag_plots.provenance.json").read_text())["plots"]["quality_scatter"]["status"] == "skipped"
    write(mag,MAG[:-1],[]); run("mag_plots.py",args,65)
    write(mag,MAG,[["cohort-A","M1","TaxA",95,2],["cohort-A","M1","TaxA",95,2]]); run("mag_plots.py",args,65)
    write(mag,MAG,[["cohort-A","M1","TaxA",101,2]]); run("mag_plots.py",args,65)
    write(mag,MAG,[["cohort-A","M1","TaxA",95,2]]); write(abundance,ABUND,[["S1","M1","true",2,"reported","","","mapping",">0","read mapping"]]); run("mag_plots.py",args); assert json.loads((out/"mag_plots.provenance.json").read_text())["plots"]["abundance_heatmap"]["status"] == "skipped"
