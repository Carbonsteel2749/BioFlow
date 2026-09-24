#!/usr/bin/env python3
"""Turn MetaWRAP quant_bins matrices into one evidence-backed sample/MAG row per value."""
from __future__ import annotations
import argparse,csv,json,os,re,subprocess
from pathlib import Path
FIELDS=["sample_id","mag_id","detected","abundance","abundance_unit","coverage","mapped_reads","mapping_method","detection_threshold","evidence_source","association_meaning","provenance_id"]
FASTA_SUFFIXES=(".fasta",".fna",".fa")
def normalize_mag_id(value):
 cleaned=value.strip()
 lower=cleaned.lower()
 for suffix in FASTA_SUFFIXES:
  if lower.endswith(suffix):return cleaned[:-len(suffix)]
 return cleaned
def aliases(row):
 values={row["sample_id"]}
 for field in ("read1","read2"):
  name=os.path.basename(row[field]); values.add(name); values.add(re.sub(r"\.(fastq|fq)(\.gz)?$","",name,flags=re.I)); values.add(re.sub(r"([_.-]R?[12])$","",re.sub(r"\.(fastq|fq)(\.gz)?$","",name,flags=re.I)))
 return {x.lower():row["sample_id"] for x in values if x}
def main():
 p=argparse.ArgumentParser();p.add_argument("--source",type=Path,required=True);p.add_argument("--sample-manifest",type=Path,required=True);p.add_argument("--database-manifest",type=Path,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--task-id",required=True);a=p.parse_args()
 mapping={}
 for row in csv.DictReader(a.sample_manifest.open(encoding="utf-8")):mapping.update(aliases(row))
 rows=list(csv.reader(a.source.open(encoding="utf-8",errors="replace"),delimiter="\t")); a.output.parent.mkdir(parents=True,exist_ok=True)
 with a.output.open("w",newline="",encoding="utf-8") as out:
  w=csv.DictWriter(out,fieldnames=FIELDS,delimiter="\t");w.writeheader()
  if not rows:return
  header=rows[0]
  for row in rows[1:]:
   if not row:continue
   mag=normalize_mag_id(row[0])
   for index,label in enumerate(header[1:],1):
    sample=mapping.get(label.strip().lower())
    if not sample or index>=len(row):continue
    value=row[index].strip()
    try:detected=float(value)>0
    except ValueError:detected=False
    w.writerow({"sample_id":sample,"mag_id":mag,"detected":str(detected).lower(),"abundance":value,"abundance_unit":"tool_reported_abundance","coverage":"","mapped_reads":"","mapping_method":"MetaWRAP quant_bins","detection_threshold":"> 0 tool-reported abundance","evidence_source":"MetaWRAP bin quantification","association_meaning":"MAG detected in sample by read mapping","provenance_id":f"{a.task_id}:mag_quantification"})
 data=json.loads(a.database_manifest.read_text(encoding="utf-8")); repo=Path(__file__).resolve().parents[3]
 revision=subprocess.run(["git","rev-parse","HEAD"],cwd=repo,capture_output=True,text=True).stdout.strip() or "unavailable"
 version=subprocess.run(["metawrap","--version"],capture_output=True,text=True).stdout.strip() if shutil.which("metawrap") else "unavailable"
 (a.output.with_suffix(".provenance.json")).write_text(json.dumps({"workflow_revision":revision,"task_or_run_id":a.task_id,"tool_name":"MetaWRAP quant_bins","tool_version":version,"database_profile":data["database_profile"],"database_manifest_sha256":data["resolved_manifest_sha256"],"input_files":[str(a.source),str(a.sample_manifest)],"output_files":[str(a.output)]},indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":
 import shutil
 main()
