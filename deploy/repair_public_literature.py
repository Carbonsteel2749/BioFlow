"""Manual, resumable repair of the audited legacy corpus. Never scheduled automatically."""
import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.bioflow_platform.store import Store
from backend.bioflow_platform.shared_literature import initialize,save_paper,embed

BASE='https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'


def text(element):
    return ''.join(element.itertext()).strip() if element is not None else ''


def parse_xml(xml):
    records={}
    for row in ET.fromstring(xml).findall('.//PubmedArticle'):
        a=row.find('./MedlineCitation/Article')
        if a is None:continue
        ids={i.get('IdType'):text(i) for i in row.findall('./PubmedData/ArticleIdList/ArticleId')}
        identifier=ids.get('doi','').lower()
        if not identifier:continue
        date=a.find('./Journal/JournalIssue/PubDate');year=text(date.find('Year')) if date is not None else ''
        if not year and date is not None:
            match=re.search(r'\d{4}',text(date));year=match.group() if match else ''
        records[identifier]={'doi':identifier,'pmid':text(row.find('./MedlineCitation/PMID')),'pmcid':ids.get('pmc',''),
            'title':text(a.find('ArticleTitle')),'abstract':'\n'.join(text(n) for n in a.findall('./Abstract/AbstractText')),
            'authors':'; '.join(' '.join(filter(None,[text(n.find('ForeName')),text(n.find('LastName'))])) or text(n.find('CollectiveName')) for n in a.findall('./AuthorList/Author')),
            'year':year,'journal':text(a.find('./Journal/Title')),'publication_types':[text(n) for n in a.findall('./PublicationTypeList/PublicationType')]}
    return records


def core(p):
    value=(p['title']+' '+p.get('abstract','')).lower()
    disease=re.search(r'\b(autism|autistic|asd)\b',p['title'].lower())
    gut=re.search(r'\b(gut|intestinal|fecal|faecal|probiotic|prebiotic|microbiota|microbiome|dysbiosis|butyrate|propionat|short.chain fatty)',value)
    return bool(disease and gut)


async def main(args):
    snapshot=json.loads(Path(args.snapshot).read_text());cache_path=Path(args.cache)
    cache=json.loads(cache_path.read_text()) if cache_path.exists() else {};store=Store(args.database);initialize(store)
    identifiers=[r['document']['name'].removesuffix('.txt').replace('_','/').lower() for r in snapshot]
    errors=[]
    async with httpx.AsyncClient(timeout=40,trust_env=False) as client:
        for start in range(0,len(identifiers),10):
            missing=[d for d in identifiers[start:start+10] if d not in cache]
            if not missing:continue
            try:
                term=' OR '.join('"'+d+'"[AID]' for d in missing)
                r=await client.get(BASE+'esearch.fcgi',params={'db':'pubmed','term':term,'retmode':'json','retmax':100,'tool':'BioFlow'});r.raise_for_status()
                ids=r.json()['esearchresult']['idlist'];await asyncio.sleep(.4)
                if ids:
                    r=await client.get(BASE+'efetch.fcgi',params={'db':'pubmed','id':','.join(ids),'retmode':'xml'});r.raise_for_status()
                    parsed=parse_xml(r.text);cache.update({d:parsed[d] for d in missing if d in parsed})
                cache_path.parent.mkdir(parents=True,exist_ok=True);cache_path.write_text(json.dumps(cache,ensure_ascii=False,indent=2))
            except (httpx.HTTPError,ValueError,KeyError,ET.ParseError) as exc:errors.append({'batch':start,'error':str(exc)[:200]})
            await asyncio.sleep(.4)
    report=[]
    for item,identifier in zip(snapshot,identifiers):
        raw=item['detail']['rawContent'];original_title=raw.splitlines()[0].removeprefix('标题(Title):').strip()
        m=cache.get(identifier) or {'doi':identifier,'title':original_title,'abstract':'','authors':'','year':''}
        types=' '.join(m.get('publication_types',[])).lower()
        correction=bool(re.search(r'^(author correction|corrigendum|erratum|retraction)',m['title'],re.I) or 'published erratum' in types or 'retracted publication' in types)
        rawbody=raw.split('(Vector Body):',1)[-1].strip() if '(Vector Body):' in raw else ''
        # Repaired authoritative abstract replaces incomplete XML-derived legacy snippets.
        content=m.get('abstract','');quality='abstract' if content else 'metadata'
        if len(rawbody)>max(4000,len(content)*2) and not item['detail']['diagnostics'].get('rawContentTruncated') and 'Full Text Availability' not in rawbody[:100]:
            content=rawbody;quality='fulltext'
        eligible=core(m) and not correction and identifier in cache
        m['scope']='core' if eligible else 'review';m['url']='https://doi.org/'+identifier
        pid=save_paper(store,m,content,quality,{'legacy_document_id':item['document']['id'],'legacy_kb':'kb-2651f85cf4e8881a0ff10dc4','repaired_from':'PubMed' if identifier in cache else 'legacy_unverified'})
        with store.connect() as db:db.execute('UPDATE public_papers SET status=? WHERE id=?',('active' if eligible else 'quarantine',pid))
        report.append({'id':pid,'doi':identifier,'title':m['title'],'quality':quality,'status':'active' if eligible else 'quarantine','pubmed_verified':identifier in cache,'reason':'correction/retraction' if correction else '' if eligible else 'metadata or topic review required'})
    if args.embeddings:
        with store.connect() as db:rows=db.execute("SELECT id,metadata FROM public_papers WHERE status='active' AND vector='[]'").fetchall()
        for start in range(0,len(rows),4):
            batch=rows[start:start+4]
            try:
                vectors=await embed([(lambda m:m['title']+'\n'+m.get('abstract','')[:3000])(json.loads(r['metadata'])) for r in batch])
                with store.connect() as db:
                    for row,v in zip(batch,vectors):db.execute('UPDATE public_papers SET vector=? WHERE id=?',(json.dumps(v),row['id']))
            except (httpx.HTTPError,KeyError,ValueError) as exc:errors.append({'embedding_batch':start,'error':str(exc)[:200]})
    result={'papers':report,'errors':errors,'active':sum(p['status']=='active' for p in report),'quarantine':sum(p['status']=='quarantine' for p in report)}
    Path(args.report).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='papers'},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--snapshot',required=True);p.add_argument('--database',required=True);p.add_argument('--cache',required=True);p.add_argument('--report',required=True);p.add_argument('--embeddings',action='store_true')
    asyncio.run(main(p.parse_args()))
