#!/usr/bin/env python3
"""Business-semantic validation for standardized taxonomy and HUMAnN tables."""
from __future__ import annotations
import argparse,csv,json
from pathlib import Path

def write(path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
def rows(path):
    with path.open(encoding="utf-8", newline="") as h:
        reader=csv.DictReader(h, delimiter="\t")
        return reader.fieldnames or [], list(reader)
def taxonomy(a):
    header,data=rows(a.table); required={"sample_id","taxid","taxonomy","taxonomy_rank","taxonomy_system","read_count_or_estimated_reads","abundance","abundance_unit","classification_source","database_profile","database_release"}
    errors=[]
    if set(header)!=required: errors.append("taxonomy table header does not match the standardized schema")
    for row in data:
        if row.get("sample_id") != a.sample: errors.append("sample_id mismatch")
        if row.get("taxonomy_rank") != "species": errors.append("Bracken species table contains a non-species rank")
        if row.get("abundance_unit") != "fraction_of_reads": errors.append("abundance unit must be fraction_of_reads")
        if row.get("classification_source") != "Kraken2+Bracken": errors.append("classification source mismatch")
        if row.get("database_profile") != a.profile or row.get("database_release") != a.release or row.get("taxonomy_system") != a.system: errors.append("database provenance mismatch")
        try:
            if not 0 <= float(row["abundance"]) <= 1: errors.append("abundance is outside [0,1]")
            if float(row["read_count_or_estimated_reads"]) < 0: errors.append("estimated reads is negative")
        except (TypeError,ValueError): errors.append("abundance or estimated reads is not numeric")
    rate=None
    if a.kraken_report.is_file():
        for line in a.kraken_report.read_text(encoding="utf-8",errors="replace").splitlines():
            part=line.split("\t")
            if len(part)>=4 and part[3].strip()=="U":
                try: rate=100-float(part[0]); break
                except ValueError: pass
    status="valid" if not data else "low_classification_rate" if rate is not None and rate < 1 else "classified"
    write(a.output,{"analysis":"taxonomy","status":status,"sample_id":a.sample,"species_rows":len(data),"classified_read_fraction_pct":rate,"database_profile":a.profile,"database_release":a.release,"errors":errors})
    return 65 if errors else 0
def functional(a):
    header,data=rows(a.table); required={"sample_id","function_source","function_namespace","function_id","function_name","function_type","abundance","abundance_unit","relative_abundance","normalization_method","evidence_level","analysis_scope","database_profile","database_release"}; errors=[]
    if set(header)!=required: errors.append("functional table header does not match the standardized schema")
    for row in data:
        if row.get("sample_id")!=a.sample or row.get("function_source")!="HUMAnN": errors.append("sample or source mismatch")
        if row.get("function_namespace")!=a.namespace or row.get("function_type")!=a.kind: errors.append("function namespace or type mismatch")
        if row.get("analysis_scope")!="community_total": errors.append("functional rows must be community_total")
        if "|" in row.get("function_id",""): errors.append("stratified HUMAnN feature leaked into standardized community table")
        if row.get("database_profile")!=a.profile or row.get("database_release")!=a.release: errors.append("database provenance mismatch")
        try:
            if float(row.get("abundance", ""))<0: errors.append("abundance is negative")
        except ValueError: errors.append("abundance is not numeric")
    status="no_unstratified_results" if not data else "valid"
    write(a.output,{"analysis":"functional","status":status,"sample_id":a.sample,"table":a.table.name,"community_total_rows":len(data),"database_profile":a.profile,"database_release":a.release,"errors":errors})
    return 65 if errors else 0
def main():
 p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="mode",required=True)
 t=sub.add_parser("taxonomy"); t.add_argument("--table",type=Path,required=True); t.add_argument("--kraken-report",type=Path,required=True); t.add_argument("--output",type=Path,required=True); t.add_argument("--sample",required=True); t.add_argument("--profile",required=True); t.add_argument("--release",required=True); t.add_argument("--system",required=True)
 f=sub.add_parser("functional"); f.add_argument("--table",type=Path,required=True); f.add_argument("--output",type=Path,required=True); f.add_argument("--sample",required=True); f.add_argument("--profile",required=True); f.add_argument("--release",required=True); f.add_argument("--namespace",required=True); f.add_argument("--kind",required=True)
 a=p.parse_args(); return taxonomy(a) if a.mode=="taxonomy" else functional(a)
if __name__=="__main__": raise SystemExit(main())
