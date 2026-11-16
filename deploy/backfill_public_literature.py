"""One-off resumable PubMed backfill. No scheduler and no library reset."""
import argparse
import asyncio
import calendar
from datetime import date,timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import httpx
from backend.bioflow_platform.store import Store
from backend.bioflow_platform.shared_literature import initialize,embed,doi
from backend.bioflow_platform.literature_evidence import index_paper

RULE_VERSION='asd-gut-english-20y-v1'
QUERY='''("Autism Spectrum Disorder"[MeSH Terms] OR "Autistic Disorder"[MeSH Terms] OR autis*[Title/Abstract] OR asperger*[Title/Abstract]) AND
("Gastrointestinal Microbiome"[MeSH Terms] OR "Probiotics"[MeSH Terms] OR "Prebiotics"[MeSH Terms] OR "Fecal Microbiota Transplantation"[MeSH Terms] OR
((gut[Title/Abstract] OR intestin*[Title/Abstract] OR gastrointestin*[Title/Abstract] OR fecal[Title/Abstract] OR faecal[Title/Abstract]) AND
(microbiot*[Title/Abstract] OR microbiom*[Title/Abstract] OR dysbios*[Title/Abstract] OR flora[Title/Abstract] OR bacteri*[Title/Abstract] OR metabolit*[Title/Abstract])) OR
probiotic*[Title/Abstract] OR prebiotic*[Title/Abstract] OR synbiotic*[Title/Abstract] OR "gut-brain"[Title/Abstract]) AND english[Language]'''
DISEASE=re.compile(r'\b(?:autis\w*|asperger\w*)\b',re.I)
GUT=re.compile(r'\b(?:gut|intestin\w*|gastrointestin\w*|fecal|faecal|microbiot\w*|microbiom\w*|dysbios\w*|probiotic\w*|prebiotic\w*|synbiotic\w*)\b',re.I)


def text(node):return ''.join(node.itertext()).strip() if node is not None else ''


def dates(article):
    result=[]
    nodes=list(article.findall('ArticleDate'))
    journal=article.find('Journal/JournalIssue/PubDate')
    if journal is None:journal=article.find('Book/PubDate')
    if journal is not None:nodes.append(journal)
    for node in nodes:
        year=text(node.find('Year'));month=text(node.find('Month'));day=text(node.find('Day'))
        if not year:
            match=re.search(r'\b(19|20)\d{2}\b',text(node.find('MedlineDate')))
            if match:year=match.group()
        if not year:continue
        try:
            y=int(year)
            if not month:result.append((date(y,1,1),date(y,12,31),'year'));continue
            m=int(month) if month.isdigit() else list(calendar.month_abbr).index(month[:3].title())
            if not day:result.append((date(y,m,1),date(y,m,calendar.monthrange(y,m)[1]),'month'));continue
            d=date(y,m,int(day));result.append((d,d,'day'))
        except (ValueError,IndexError):continue
    if not result:return None
    earliest=min(r[0].year for r in result)
    return min((r for r in result if r[0].year==earliest),key=lambda r:((r[1]-r[0]).days,r[0]))


def parse_records(xml):
    root=ET.fromstring(xml)
    if root.find('.//ERROR') is not None:raise ValueError(text(root.find('.//ERROR')))
    records=[]
    for record in root:
        book=record.tag=='PubmedBookArticle'
        if record.tag not in {'PubmedArticle','PubmedBookArticle'}:continue
        a=record.find('BookDocument' if book else 'MedlineCitation/Article')
        if a is None:continue
        pmid=text(record.find('BookDocument/PMID' if book else 'MedlineCitation/PMID'))
        if not pmid:continue
        ids={n.get('IdType'):text(n) for n in (a.findall('ArticleIdList/ArticleId') if book else record.findall('PubmedData/ArticleIdList/ArticleId'))}
        published=dates(a)
        abstract='\n\n'.join(((n.get('Label')+': ') if n.get('Label') else '')+text(n) for n in a.findall('Abstract/AbstractText') if text(n))
        records.append({'pmid':pmid,'doi':doi(ids.get('doi','')),'pmcid':ids.get('pmc',''),
            'title':text(a.find('ArticleTitle')),'abstract':abstract,
            'authors':'; '.join(' '.join(filter(None,[text(n.find('ForeName')),text(n.find('LastName'))])) or text(n.find('CollectiveName')) for n in a.findall('AuthorList/Author')),
            'journal':text(a.find('Book/BookTitle' if book else 'Journal/Title')),'languages':[text(n) for n in a.findall('Language')],
            'publication_types':[text(n) for n in a.findall('PublicationType' if book else 'PublicationTypeList/PublicationType')],
            'record_type':'book_chapter' if book else 'journal_article',
            'correction_links':[n.get('RefType','') for n in record.findall('MedlineCitation/CommentsCorrectionsList/CommentsCorrections')],
            'mesh':[text(n) for n in record.findall('MedlineCitation/MeshHeadingList/MeshHeading/DescriptorName')],
            'year':str(published[0].year) if published else '',
            'publication_date':published[0].isoformat() if published else '',
            'publication_date_end':published[1].isoformat() if published else '',
            'date_precision':published[2] if published else '',
            'url':'https://doi.org/'+doi(ids['doi']) if ids.get('doi') else 'https://pubmed.ncbi.nlm.nih.gov/'+pmid+'/'})
    return records


def classify(p,start,end):
    reasons=[];title=p['title'];body=p.get('abstract','');all_text=title+' '+body
    if 'eng' not in p.get('languages',[]):reasons.append('not_english')
    if not title:reasons.append('missing_title')
    if p.get('record_type')=='book_chapter':reasons.append('book_chapter_review')
    types=' '.join(p.get('publication_types',[])).lower()
    if re.match(r'^(?:author correction|correction|corrigendum|erratum|retraction)',title,re.I) or any(t in types for t in ('published erratum','retraction of publication','retracted publication','expression of concern')) or any(t in {'RetractionIn','ExpressionOfConcernIn'} for t in p.get('correction_links',[])):reasons.append('correction_or_retraction')
    if not p.get('publication_date'):reasons.append('missing_publication_date')
    elif p['publication_date']<start.isoformat() or p['publication_date_end']>end.isoformat():reasons.append('date_needs_review')
    disease_title=bool(DISEASE.search(title) or (re.search(r'\bASD\b',title) and DISEASE.search(body)))
    disease_mesh=any('autis' in t.lower() for t in p.get('mesh',[]))
    gut_title=bool(GUT.search(title));gut_mentions=len(GUT.findall(body))
    # Full-name ASD in title or strong ASD focus plus a gut-focused title.
    focused=(disease_title and (gut_title or gut_mentions>=2)) or (gut_title and disease_mesh and len(DISEASE.findall(body))>=2)
    if not focused:reasons.append('topic_needs_review')
    if len(body.strip())<120:reasons.append('missing_or_short_abstract')
    return ('active' if not reasons else 'quarantine'),reasons


def pmc_body(xml):
    root=ET.fromstring(xml)
    error=root.find('.//{*}error')
    if error is not None:
        if error.get('code') in {'cannotDisseminateFormat','idDoesNotExist','noRecordsMatch'}:return '',{'availability':error.get('code')}
        raise ValueError('Unexpected PMC error: '+error.get('code','unknown'))
    article=root.find('.//{*}article')
    if article is None:return '',{}
    body=article.find('{*}body')
    if body is None:return '',{}
    paragraphs=[text(n) for n in body.iter() if n.tag.rsplit('}',1)[-1] in {'title','p'} and text(n)]
    value='\n\n'.join(paragraphs)
    if len(value)<1000 or (len(value)<2000 and 'full text availab' in value.lower()):return '',{}
    license_node=article.find('.//{*}license')
    return value,{'license':text(license_node),'retrieval':'PMC OAI-PMH pmc format'}


class Requests:
    def __init__(self):self.last=0;self.client=httpx.AsyncClient(timeout=60,trust_env=False,headers={'User-Agent':'BioFlow-LiteratureBackfill/1.0'})
    async def get(self,url,params,allow_status=()):
        for attempt in range(6):
            await asyncio.sleep(max(0,.45-(time.monotonic()-self.last)));self.last=time.monotonic()
            try:
                r=await self.client.get(url,params=params)
                if r.status_code==429 or r.status_code>=500:
                    delay=r.headers.get('Retry-After','')
                    await asyncio.sleep(min(float(delay),60) if delay.isdigit() else min(2**attempt,30));continue
                if r.status_code not in allow_status:r.raise_for_status()
                return r
            except (httpx.TimeoutException,httpx.NetworkError):
                if attempt==5:raise
                await asyncio.sleep(min(2**attempt,30))
        raise RuntimeError('Upstream retries exhausted: '+url)


def job_db(directory):
    db=sqlite3.connect(directory/'queue.sqlite3');db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS windows(start TEXT,end TEXT,count INTEGER,PRIMARY KEY(start,end));
      CREATE TABLE IF NOT EXISTS papers(pmid TEXT PRIMARY KEY,metadata TEXT,paper_id TEXT,
        fulltext_done INTEGER NOT NULL DEFAULT 0,index_done INTEGER NOT NULL DEFAULT 0,
        result TEXT,fulltext_saved INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '');''')
    return db


def progress(directory,db,phase,**extra):
    state=json.loads((directory/'progress.json').read_text()) if (directory/'progress.json').exists() else {}
    counts={'discovered':db.execute('SELECT count(*) FROM papers').fetchone()[0],
        'metadata_done':db.execute('SELECT count(*) FROM papers WHERE metadata IS NOT NULL').fetchone()[0],
        'new_public':db.execute("SELECT count(*) FROM papers WHERE result='active'").fetchone()[0],
        'new_review':db.execute("SELECT count(*) FROM papers WHERE result='quarantine'").fetchone()[0],
        'existing':db.execute("SELECT count(*) FROM papers WHERE result='existing'").fetchone()[0],
        'fulltext_checked':db.execute('SELECT count(*) FROM papers WHERE fulltext_done=1').fetchone()[0],
        'fulltext_saved':db.execute('SELECT count(*) FROM papers WHERE fulltext_saved=1').fetchone()[0],
        'index_done':db.execute('SELECT count(*) FROM papers WHERE index_done=1').fetchone()[0],
        'errors':db.execute("SELECT count(*) FROM papers WHERE error!=''").fetchone()[0]}
    state.update({'phase':phase,'pid':os.getpid(),'updated_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'counts':counts,**extra})
    temp=directory/'progress.json.tmp';temp.write_text(json.dumps(state,ensure_ascii=False,indent=2));temp.replace(directory/'progress.json')
    print(json.dumps({'phase':phase,**counts,**extra},ensure_ascii=False),flush=True)


async def discover(requests,db,directory,start,end):
    if db.execute('SELECT 1 FROM windows WHERE start=? AND end=?',(start.isoformat(),end.isoformat())).fetchone():return
    term=QUERY+f' AND ("{start:%Y/%m/%d}"[Date - Publication] : "{end:%Y/%m/%d}"[Date - Publication])'
    response=await requests.get('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi',{'db':'pubmed','term':term,'retmode':'json','retmax':9999,'tool':'BioFlow'})
    data=response.json()['esearchresult']
    if data.get('errorlist'):raise ValueError(str(data['errorlist']))
    count=int(data['count'])
    if count>9999:
        if start==end:raise RuntimeError('Daily PubMed result exceeds safe enumeration limit')
        mid=start+(end-start)//2;await discover(requests,db,directory,start,mid);await discover(requests,db,directory,mid+timedelta(days=1),end)
    else:
        ids=data['idlist']
        if len(ids)!=count:raise ValueError('PubMed enumeration incomplete')
        with db:db.executemany('INSERT OR IGNORE INTO papers(pmid) VALUES(?)',[(p,) for p in ids])
    with db:db.execute('INSERT INTO windows VALUES(?,?,?)',(start.isoformat(),end.isoformat(),count))
    progress(directory,db,'discovery',window=[start.isoformat(),end.isoformat()],window_matches=count)


def merge_paper(store,p,status,reasons,run):
    identity=p['doi'] or 'pmid:'+p['pmid'];identifier=hashlib.sha256(identity.encode()).hexdigest()[:32]
    with store.connect() as db:
        existing=db.execute('SELECT * FROM public_papers WHERE identity=?',(identity,)).fetchone()
        if not existing:
            existing=db.execute("SELECT * FROM public_papers WHERE json_extract(metadata,'$.pmid')=? ORDER BY CASE WHEN status='removed' THEN 1 ELSE 0 END LIMIT 1",(p['pmid'],)).fetchone()
        provenance={'source':'PubMed','pmid':p['pmid'],'backfill_run':run,'rule_version':RULE_VERSION,'review_reasons':reasons}
        p={**p,'scope':'core' if status=='active' else 'review'}
        if existing:
            old=json.loads(existing['metadata']);merged={**p,**old}
            for key,value in p.items():
                if not merged.get(key):merged[key]=value
            prov={**json.loads(existing['provenance']),'backfill_run':run}
            # Existing status/curation, full text, and vectors are preserved.
            db.execute('UPDATE public_papers SET metadata=?,provenance=? WHERE id=?',(json.dumps(merged,ensure_ascii=False),json.dumps(prov),existing['id']))
            if existing['status']=='active' and 'correction_or_retraction' in reasons:
                prov['review_reasons']=reasons
                db.execute("UPDATE public_papers SET status='quarantine',provenance=? WHERE id=?",(json.dumps(prov),existing['id']))
            return existing['id'],'existing'
        quality='abstract' if p['abstract'] else 'metadata'
        db.execute('INSERT INTO public_papers(id,identity,metadata,content,status,quality,provenance) VALUES(?,?,?,?,?,?,?)',
            (identifier,identity,json.dumps(p,ensure_ascii=False),p['abstract'],status,quality,json.dumps(provenance)))
    return identifier,status


async def worker(args,directory):
    import fcntl
    with (ROOT/'runtime/crawls/asd-gut-backfill.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('An ASD/gut backfill is already running')
        os.nice(10);store=Store(ROOT/'runtime/bioflow.sqlite3');initialize(store);db=job_db(directory);requests=Requests()
        with store.connect() as pub:pub.execute("CREATE INDEX IF NOT EXISTS public_papers_pmid ON public_papers(json_extract(metadata,'$.pmid'))")
        config={'start':args.start,'end':args.end,'query':QUERY,'rule_version':RULE_VERSION,'one_off':True}
        (directory/'config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
        progress(directory,db,'starting',date_range=[args.start,args.end],started_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
        try:
            start=date.fromisoformat(args.start);end=date.fromisoformat(args.end)
            for year in range(start.year,end.year+1):await discover(requests,db,directory,max(start,date(year,1,1)),min(end,date(year,12,31)))
            pending=[r[0] for r in db.execute('SELECT pmid FROM papers WHERE metadata IS NULL ORDER BY pmid')]
            for offset in range(0,len(pending),100):
                ids=pending[offset:offset+100]
                response=await requests.get('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi',{'db':'pubmed','id':','.join(ids),'retmode':'xml','tool':'BioFlow'})
                (directory/'raw'/('pubmed-'+hashlib.sha256(','.join(ids).encode()).hexdigest()[:16]+'.xml')).write_text(response.text)
                found=set()
                for p in parse_records(response.text):
                    found.add(p['pmid']);status,reasons=classify(p,start,end);pid,result=merge_paper(store,p,status,reasons,directory.name)
                    with db:db.execute('UPDATE papers SET metadata=?,paper_id=?,result=?,error=? WHERE pmid=?',(json.dumps(p,ensure_ascii=False),pid,result,'',p['pmid']))
                with db:
                    for missing in set(ids)-found:db.execute("UPDATE papers SET error='missing_efetch_record' WHERE pmid=?",(missing,))
                progress(directory,db,'metadata')
            for record in db.execute('SELECT * FROM papers WHERE metadata IS NOT NULL AND fulltext_done=0').fetchall():
                p=json.loads(record['metadata']);pmcid=p.get('pmcid','')
                with store.connect() as pub:target=pub.execute('SELECT * FROM public_papers WHERE id=?',(record['paper_id'],)).fetchone()
                if pmcid and target['status']=='active' and target['quality']!='fulltext':
                    try:
                        response=await requests.get('https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/',{'verb':'GetRecord','identifier':'oai:pubmedcentral.nih.gov:'+pmcid.removeprefix('PMC'),'metadataPrefix':'pmc'},allow_status=(400,))
                        body,license_info=pmc_body(response.text)
                        if body:
                            relative='raw/'+pmcid+'.xml';(directory/relative).write_text(response.text)
                            with store.connect() as pub:
                                prov={**json.loads(target['provenance']),**license_info,'fulltext_cache':(directory.relative_to(ROOT)/relative).as_posix()}
                                pub.execute('UPDATE public_papers SET content=?,quality=?,provenance=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status=?',(body,'fulltext',json.dumps(prov),target['id'],'active'))
                            with db:db.execute('UPDATE papers SET fulltext_saved=1 WHERE pmid=?',(record['pmid'],))
                    except (httpx.HTTPError,RuntimeError,ET.ParseError,ValueError) as exc:
                        with db:db.execute('UPDATE papers SET error=? WHERE pmid=?',('fulltext:'+type(exc).__name__,record['pmid']))
                        progress(directory,db,'fulltext');continue
                with db:db.execute("UPDATE papers SET fulltext_done=1,error=CASE WHEN error LIKE 'fulltext:%' THEN '' ELSE error END WHERE pmid=?",(record['pmid'],))
                progress(directory,db,'fulltext')
            async def gentle_embed(values):
                output=[]
                for start in range(0,len(values),8):
                    output.extend(await embed(values[start:start+8]));await asyncio.sleep(.5)
                return output
            for record in db.execute('SELECT * FROM papers WHERE metadata IS NOT NULL AND index_done=0').fetchall():
                try:
                    with store.connect() as pub:target=pub.execute('SELECT * FROM public_papers WHERE id=?',(record['paper_id'],)).fetchone()
                    if target['status']=='active' and target['quality'] in {'abstract','fulltext'}:
                        if target['vector']=='[]':
                            p=json.loads(target['metadata']);vector=(await gentle_embed([p['title']+'\n'+p.get('abstract','')[:3000]]))[0]
                            with store.connect() as pub:pub.execute('UPDATE public_papers SET vector=? WHERE id=?',(json.dumps(vector),target['id']))
                        await index_paper(store,target['id'],gentle_embed)
                    with db:db.execute("UPDATE papers SET index_done=1,error=CASE WHEN error LIKE 'index:%' THEN '' ELSE error END WHERE pmid=?",(record['pmid'],))
                except (httpx.HTTPError,ValueError,KeyError) as exc:
                    with db:db.execute('UPDATE papers SET error=? WHERE pmid=?',('index:'+type(exc).__name__,record['pmid']))
                progress(directory,db,'index')
            errors=db.execute("SELECT count(*) FROM papers WHERE error!=''").fetchone()[0]
            progress(directory,db,'completed_with_errors' if errors else 'completed',finished_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'))
        except Exception as exc:
            progress(directory,db,'failed',error=type(exc).__name__+': '+str(exc)[:500]);logging.exception('Backfill failed');raise
        finally:await requests.client.aclose();db.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--start',default='2006-11-12');p.add_argument('--end',default='2026-11-12');p.add_argument('--worker',action='store_true');p.add_argument('--status',action='store_true');p.add_argument('--probe',action='store_true');args=p.parse_args()
    start=date.fromisoformat(args.start);end=date.fromisoformat(args.end)
    if start>end:raise ValueError('Invalid date range')
    if args.probe:
        async def probe():
            requests=Requests()
            try:
                term=QUERY+f' AND ("{start:%Y/%m/%d}"[Date - Publication] : "{end:%Y/%m/%d}"[Date - Publication])'
                r=await requests.get('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi',{'db':'pubmed','term':term,'retmode':'json','retmax':0,'tool':'BioFlow'})
                data=r.json()['esearchresult']
                if data.get('errorlist'):raise ValueError(str(data['errorlist']))
                print(json.dumps({'start':args.start,'end':args.end,'candidates':int(data['count']),'query_translation':data.get('querytranslation','')},ensure_ascii=False))
            finally:await requests.client.aclose()
        asyncio.run(probe());return
    directory=ROOT/'runtime/crawls'/('asd-gut-'+args.start+'_'+args.end);(directory/'raw').mkdir(parents=True,exist_ok=True)
    if args.status:
        print((directory/'progress.json').read_text() if (directory/'progress.json').exists() else '{"phase":"not_started"}');return
    if args.worker:asyncio.run(worker(args,directory));return
    previous=json.loads((directory/'progress.json').read_text()) if (directory/'progress.json').exists() else {}
    oldpid=previous.get('pid')
    if oldpid:
        try:
            if str(Path(__file__).resolve()).encode() in (Path('/proc')/str(oldpid)/'cmdline').read_bytes():print(json.dumps(previous));return
        except OSError:pass
    backup=ROOT/'runtime/backups'/('before-20y-backfill-'+time.strftime('%Y%m%d-%H%M%S')+'.sqlite3');backup.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(ROOT/'runtime/bioflow.sqlite3') as source,sqlite3.connect(backup) as target:source.backup(target)
    with (directory/'run.log').open('ab') as log:
        child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker','--start',args.start,'--end',args.end],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    print(json.dumps({'pid':child.pid,'directory':str(directory),'backup':str(backup),'start':args.start,'end':args.end,'one_off':True}))


if __name__=='__main__':main()
