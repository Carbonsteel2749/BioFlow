"""Relocate operational database paths after upload; original file contents stay intact."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import sqlite3

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    target=(ROOT/'BioLLM_RawDataAnalysis').as_posix()
    origins={'/home/xh/BioLLM_RawDataAnalysis'}
    report=ROOT/'runtime/migration-latest.json'
    if report.exists():
        origins.add(json.loads(report.read_text(encoding='utf-8'))['analysis_root'])
    origins.discard(target)
    if not args.apply:
        print(json.dumps({'mode':'read-only','from':sorted(origins),'to':target},ensure_ascii=False,indent=2));return
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=ROOT/'runtime/archive'/('rebase-'+stamp)
    backup.mkdir(parents=True,exist_ok=True)
    changed=0
    for relative in ['BioLLM_RawDataAnalysis/runtime/state/tasks.sqlite3','runtime/bioflow.sqlite3']:
        path=ROOT/relative
        if not path.is_file():continue
        db=sqlite3.connect(path)
        with sqlite3.connect(backup/(path.stem+'.sqlite3')) as destination:db.backup(destination)
        with db:
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',r[0])]
            for table in tables:
                columns=db.execute(f'PRAGMA table_info("{table}")').fetchall()
                for col in columns:
                    if col[2].upper()!='TEXT':continue
                    for origin in origins:
                        # Historical duplicate dataset registrations retain their own IDs.
                        if table=='datasets' and col[1]=='manifest_path':
                            cursor=db.execute('UPDATE datasets SET manifest_path=replace(manifest_path,?,?) WHERE manifest_path LIKE ? AND NOT EXISTS(SELECT 1 FROM datasets other WHERE other.manifest_path=replace(datasets.manifest_path,?,?))',(origin,target,'%'+origin+'%',origin,target))
                        else:
                            cursor=db.execute(f'UPDATE "{table}" SET "{col[1]}"=replace("{col[1]}",?,?) WHERE "{col[1]}" LIKE ?',(origin,target,'%'+origin+'%'))
                        changed+=cursor.rowcount
        db.close()
    print(json.dumps({'updated_values':changed,'backup':str(backup),'target':target},ensure_ascii=False))


if __name__=='__main__':main()
