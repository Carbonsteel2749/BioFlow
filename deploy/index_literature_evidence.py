"""Manual rebuild of semantic evidence only; paper search vectors and source text stay intact."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.bioflow_platform.store import Store
from backend.bioflow_platform.shared_literature import initialize,embed
from backend.bioflow_platform.literature_evidence import index_paper


async def run(args):
    store=Store(args.database);initialize(store)
    with store.connect() as db:ids=[r[0] for r in db.execute("SELECT id FROM public_papers WHERE status='active' AND quality IN ('abstract','fulltext') ORDER BY id")]
    results=[]
    for identifier in ids:
        result=await index_paper(store,identifier,embed);results.append(result);print(json.dumps(result),flush=True)
    Path(args.report).write_text(json.dumps(results,indent=2));print('completed',len(results))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--report',required=True)
    asyncio.run(run(p.parse_args()))
