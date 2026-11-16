"""Import reviewed staging tables without rewriting existing user/project data."""
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.bioflow_platform.store import Store
from backend.bioflow_platform.shared_literature import initialize


def main():
    store=Store(ROOT/'runtime/bioflow.sqlite3');initialize(store)
    backup=ROOT/'runtime/backups'/('before-public-library-'+time.strftime('%Y%m%d-%H%M%S')+'.sqlite3');backup.parent.mkdir(parents=True,exist_ok=True)
    with store.connect() as source,sqlite3.connect(backup) as target:source.backup(target)
    with store.connect() as db:
        db.execute('ATTACH DATABASE ? AS staged',(str(ROOT/'runtime/public-literature-staging.sqlite3'),))
        # Keep existing moderation decisions on a repeated import.
        db.execute('INSERT OR IGNORE INTO public_papers SELECT * FROM staged.public_papers')
        counts=dict(db.execute('SELECT status,count(*) FROM public_papers GROUP BY status'))
    print(json.dumps({'backup':str(backup),'counts':counts}))


if __name__=='__main__':main()
