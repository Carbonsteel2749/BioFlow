#!/usr/bin/env python3
"""Normalize MetaWRAP MAG evidence without assigning a coassembly MAG to a sample."""
from __future__ import annotations
import argparse, csv, json, re, subprocess
from datetime import datetime, timezone
from pathlib import Path

MAG_FIELDS = ["cohort_id","mag_id","taxid","taxonomy","taxonomy_rank","taxonomy_system","classification_confidence","completeness","contamination","quality_flag","genome_size","contig_count","provenance_id"]
FUNCTION_FIELDS = ["cohort_id","mag_id","gene_id","function_source","function_namespace","function_id","function_name","function_type","annotation_confidence","evidence_level","provenance_id"]
FASTA_SUFFIXES=(".fasta",".fna",".fa")
def normalize_mag_id(value):
    cleaned=clean(value)
    lower=cleaned.lower()
    for suffix in FASTA_SUFFIXES:
        if lower.endswith(suffix): return cleaned[:-len(suffix)]
    return cleaned
def fasta_stats(path):
    size=contigs=0
    for line in path.read_text(encoding="utf-8",errors="replace").splitlines():
        if line.startswith("> ") or line.startswith(">"): contigs+=1
        else: size+=len(line.strip())
    return size,contigs
def clean(value): return (value or "").strip()
def rank(taxonomy):
    for prefix, name in (("s__","species"),("g__","genus"),("f__","family"),("o__","order"),("c__","class"),("p__","phylum"),("d__","domain")):
        if prefix in taxonomy: return name
    return ""
def delimited_rows(path):
    lines=[line for line in path.read_text(encoding="utf-8",errors="replace").splitlines() if line.strip() and not line.startswith("#")]
    if not lines: return [], []
    split=(lambda line: line.split("\t")) if "\t" in lines[0] else (lambda line: re.split(r"\s{2,}|\s+",line.strip(),maxsplit=1))
    values=[split(line) for line in lines]; header=[value.lower().replace(" ","_") for value in values[0]]
    return (header, values[1:]) if any(value in header for value in ("bin","bin_id","mag_id","taxonomy","classification")) else ([], values)
def column(header,names,default=None): return next((header.index(name) for name in names if name in header),default)
def taxonomy_rows(path):
    header, rows=delimited_rows(path); bin_col=column(header,("bin","bin_id","mag","mag_id","genome"),0); tax_col=column(header,("taxonomy","classification","gtdb_taxonomy","lineage"),1); taxid_col=column(header,("taxid","taxonomy_id","ncbi_taxid")); conf_col=column(header,("confidence","classification_confidence","support")); result={}
    for row in rows:
        if bin_col is None or tax_col is None or len(row)<=max(bin_col,tax_col): continue
        result[normalize_mag_id(row[bin_col])]={"taxonomy":clean(row[tax_col]),"taxid":clean(row[taxid_col]) if taxid_col is not None and len(row)>taxid_col else "","confidence":clean(row[conf_col]) if conf_col is not None and len(row)>conf_col else ""}
    return result
def quality_rows(path):
    if path is None or not path.is_file(): return {}
    header,rows=delimited_rows(path); bin_col=column(header,("bin","bin_id","mag","mag_id","genome"),0); completeness_col=column(header,("completeness","completeness_%")); contamination_col=column(header,("contamination","contamination_%")); result={}
    if completeness_col is None and contamination_col is None: return result
    for row in rows:
        if bin_col is None or len(row)<=bin_col: continue
        result[normalize_mag_id(row[bin_col])]={"completeness":clean(row[completeness_col]) if completeness_col is not None and len(row)>completeness_col else "","contamination":clean(row[contamination_col]) if contamination_col is not None and len(row)>contamination_col else ""}
    return result
def attributes(text): return {key:clean(value) for key,value in (part.split("=",1) for part in text.split(";") if "=" in part)}
def gff_functions(directory):
    result={}
    for path in sorted(directory.rglob("*.gff")):
        evidence=[]
        for line in path.read_text(encoding="utf-8",errors="replace").splitlines():
            if not line or line.startswith("#"): continue
            fields=line.split("\t")
            if len(fields)<9: continue
            attrs=attributes(fields[8]); gene_id=attrs.get("ID") or attrs.get("locus_tag") or attrs.get("Name") or ""; name=attrs.get("product") or attrs.get("Name") or attrs.get("gene") or ""; references=[item for item in re.split(r"[,|]",attrs.get("Dbxref",attrs.get("db_xref",""))) if item]
            if references:
                for reference in references: evidence.append((gene_id,reference.split(":",1)[0] if ":" in reference else "raw_dbxref",reference,name or reference))
            elif name: evidence.append((gene_id,"annotation_product",gene_id or name,name))
        result[path.stem]=evidence
    return result
def command_output(command, cwd=None):
    try:
        result=subprocess.run(command,cwd=cwd,capture_output=True,text=True,timeout=10); return (result.stdout or result.stderr).strip() or f"exit_code={result.returncode}"
    except OSError: return "unavailable"
def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--cohort-id",required=True); parser.add_argument("--bins",type=Path,required=True); parser.add_argument("--taxonomy",type=Path,required=True); parser.add_argument("--functions",type=Path,required=True); parser.add_argument("--database-manifest",type=Path,required=True); parser.add_argument("--outdir",type=Path,required=True); parser.add_argument("--task-id",required=True); parser.add_argument("--quality",type=Path); args=parser.parse_args()
    data=json.loads(args.database_manifest.read_text(encoding="utf-8")); database=data["databases"]["mag_annotation"]; taxonomy_system=database["classification"]["taxonomy_system"]; provenance_id=f"{args.task_id}:{args.cohort_id}:mag_annotation"; args.outdir.mkdir(parents=True,exist_ok=True); taxonomies=taxonomy_rows(args.taxonomy); qualities=quality_rows(args.quality); functions=gff_functions(args.functions); bins=sorted({path for pattern in ("*.fa","*.fna","*.fasta") for path in args.bins.glob(pattern)})
    with (args.outdir/"mag_annotation.tsv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=MAG_FIELDS,delimiter="\t"); writer.writeheader()
        for bin_path in bins:
            mag_id=bin_path.stem; genome_size,contig_count=fasta_stats(bin_path); tax=taxonomies.get(mag_id,{}); quality=qualities.get(mag_id,{})
            writer.writerow({"cohort_id":args.cohort_id,"mag_id":mag_id,"taxid":tax.get("taxid",""),"taxonomy":tax.get("taxonomy",""),"taxonomy_rank":rank(tax.get("taxonomy","")),"taxonomy_system":taxonomy_system,"classification_confidence":tax.get("confidence",""),"completeness":quality.get("completeness",""),"contamination":quality.get("contamination",""),"quality_flag":"","genome_size":genome_size,"contig_count":contig_count,"provenance_id":provenance_id})
    with (args.outdir/"mag_function_annotation.tsv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=FUNCTION_FIELDS,delimiter="\t"); writer.writeheader()
        for mag_id,evidence in functions.items():
            for gene_id,namespace,function_id,name in evidence: writer.writerow({"cohort_id":args.cohort_id,"mag_id":mag_id,"gene_id":gene_id,"function_source":"MetaWRAP annotate_bins","function_namespace":namespace,"function_id":function_id,"function_name":name,"function_type":"gene_function","annotation_confidence":"","evidence_level":"MAG GFF annotation","provenance_id":provenance_id})
    revision=command_output(["git","rev-parse","HEAD"],Path(__file__).resolve().parents[3]); payload={"workflow_revision":revision,"task_or_run_id":args.task_id,"cohort_id":args.cohort_id,"tool_name":"MetaWRAP classify_bins/annotate_bins","tool_version":command_output(["metawrap","--version"]),"database_profile":data["database_profile"],"databases":{name:{"database_name":entry["database_name"],"database_release":entry["release"]} for name,entry in database.items()},"taxonomy_system":taxonomy_system,"database_manifest_sha256":data["resolved_manifest_sha256"],"input_files":[str(args.bins),str(args.taxonomy),str(args.functions),str(args.quality) if args.quality else ""],"output_files":[str(args.outdir/"mag_annotation.tsv"),str(args.outdir/"mag_function_annotation.tsv")],"timestamp":datetime.now(timezone.utc).isoformat()}
    (args.outdir/"mag_annotation.provenance.json").write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    return 0
if __name__=="__main__": raise SystemExit(main())
