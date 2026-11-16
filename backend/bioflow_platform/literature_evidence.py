"""Source-aligned sentence/paragraph chunks with cached semantic boundaries."""
import hashlib
import json
import math
import re
from collections import Counter
from .literature_query import tokens

VERSION='sentence-semantic-v1'
ABBREVIATIONS={'e.g.','i.e.','et al.','fig.','figs.','dr.','prof.','mr.','mrs.','ms.','vs.','sp.','spp.','no.','nos.','vol.','ref.','refs.','approx.','inc.'}
CONTRAST=re.compile(r'^(?:(?:however|nevertheless|nonetheless|although|despite|in contrast|conversely|but|yet|therefore|these findings|this suggests)\b|然而|但是|尽管|因此)',re.I)


def cosine(a,b):
    if not a or not b or len(a)!=len(b):return 0.0
    return sum(x*y for x,y in zip(a,b))/max(math.sqrt(sum(x*x for x in a)*sum(y*y for y in b)),1e-12)


def digest(text):return hashlib.sha256((VERSION+'\0'+text).encode()).hexdigest()


def sentences(text):
    """Offsets always address the original text, including all inline scientific notation."""
    output=[]
    for paragraph in re.finditer(r'[^\r\n]+',text):
        value=paragraph.group();start=paragraph.start();cursor=0
        for stop in re.finditer(r'[.!?。！？]+[\"\u201d\u2019\)\]]*(?=\s|$|[\u3400-\u9fff])',value):
            end=stop.end();prefix=value[:end].rstrip('"”’)]');tail=value[end:]
            word=re.search(r'(?:[A-Za-z]+\.)+$',prefix)
            abbreviation=any(prefix.lower().endswith(a) for a in ABBREVIATIONS)
            initial=bool(word and re.fullmatch(r'(?:[A-Za-z]\.)+',word.group()))
            if tail.strip() and (abbreviation or initial):continue
            lo=cursor
            while lo<end and value[lo].isspace():lo+=1
            if lo<end:output.append({'start':start+lo,'end':start+end,'paragraph':paragraph.start()})
            cursor=end
        lo=cursor
        while lo<len(value) and value[lo].isspace():lo+=1
        hi=len(value.rstrip())
        if lo<hi:output.append({'start':start+lo,'end':start+hi,'paragraph':paragraph.start()})
    return output


def mean_vector(vectors):
    vectors=[v for v in vectors if v]
    if not vectors:return []
    result=[sum(values)/len(vectors) for values in zip(*vectors)]
    norm=math.sqrt(sum(x*x for x in result)) or 1
    return [x/norm for x in result]


def coherent_units(text):
    output=[]
    for unit in sentences(text):
        if output and CONTRAST.match(text[unit['start']:unit['end']]):output[-1]['end']=unit['end']
        else:output.append(dict(unit))
    return output


def chunks(text,units=None,vectors=None):
    units=sentences(text) if units is None else units;vectors=vectors or []
    result=[];current=[];indices=[]
    def flush():
        if current:
            a,b=current[0]['start'],current[-1]['end']
            result.append({'start':a,'end':b,'text':text[a:b],'vector':mean_vector([vectors[i] for i in indices if i<len(vectors)]),
                'method':'sentence_semantic' if vectors else 'paragraph_sentences'})
    for i,unit in enumerate(units):
        if current:
            value=text[unit['start']:unit['end']];contrast=bool(CONTRAST.match(value))
            paragraph_change=unit['paragraph']!=current[-1]['paragraph']
            prior=text[current[0]['start']:current[-1]['end']]
            heading=len(prior)<120 and len(tokens(prior))<9 and not re.search(r'[.!?。！？][\"”’\)\]]*$',prior)
            semantic_change=bool(vectors and i<len(vectors) and cosine(vectors[i-1],vectors[i])<.48 and len(current)>=2 and current[-1]['end']-current[0]['start']>=250)
            soft_limit=unit['end']-current[0]['start']>1600
            if (paragraph_change or semantic_change or soft_limit) and not contrast and not heading:
                flush();current=[];indices=[]
        current.append(unit);indices.append(i)
    flush();return result


def select(text,query,blocks,query_vector=None):
    if not blocks:return []
    q=set(tokens(query));bags=[Counter(tokens(b['text'])) for b in blocks]
    counts=Counter(term for bag in bags for term in bag)
    scored=[]
    for i,(block,bag) in enumerate(zip(blocks,bags)):
        lexical=sum(math.log(1+(len(blocks)+1)/(counts[t]+1))*min(bag[t],2) for t in q if bag[t])
        semantic=max(0,cosine(query_vector,block.get('vector')))
        scored.append((lexical+semantic*2,i))
    _,index=max(scored,key=lambda pair:(pair[0],-pair[1]))
    chosen=dict(blocks[index]);chosen.pop('vector',None)
    # Keep a following qualification/conclusion with its preceding evidence.
    if index+1<len(blocks) and CONTRAST.match(blocks[index+1]['text']):
        chosen['end']=blocks[index+1]['end'];chosen['text']=text[chosen['start']:chosen['end']]
    chosen['offset']=chosen.pop('start')
    result=[chosen]
    # A background/methods match alone is weak evidence for an effect question.
    findings=re.compile(r'\b(results?|found|observed|demonstrat\w*|showed|improv\w*|reduc\w*|increas\w*|conclu\w*|limited|uncertain)\b',re.I)
    if not findings.search(chosen['text']):
        for _,other in sorted(scored,reverse=True):
            candidate=blocks[other]
            if other==index or not findings.search(candidate['text']) or not q.intersection(tokens(candidate['text'])):continue
            if candidate['start']<chosen['end'] and candidate['end']>chosen['offset']:continue
            item={k:v for k,v in candidate.items() if k!='vector'};item['offset']=item.pop('start');result.append(item);break
    return sorted(result,key=lambda p:p['offset'])


def initialize(store):
    with store.connect() as db:db.execute('''CREATE TABLE IF NOT EXISTS public_evidence_index(
        paper_id TEXT PRIMARY KEY,content_hash TEXT NOT NULL,version TEXT NOT NULL,
        chunks TEXT NOT NULL,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')


def cached(store,row):
    with store.connect() as db:record=db.execute('SELECT * FROM public_evidence_index WHERE paper_id=?',(row['id'],)).fetchone()
    if record and record['content_hash']==digest(row['content']) and record['version']==VERSION:return json.loads(record['chunks'])
    return chunks(row['content'])


async def index_paper(store,identifier,embed):
    with store.connect() as db:record=db.execute("SELECT * FROM public_papers WHERE id=? AND status='active'",(identifier,)).fetchone()
    if not record or record['quality'] not in {'abstract','fulltext'}:return {'id':identifier,'status':'skipped'}
    row=dict(record);text=row['content'];signature=digest(text)
    with store.connect() as db:old=db.execute('SELECT content_hash FROM public_evidence_index WHERE paper_id=?',(identifier,)).fetchone()
    if old and old['content_hash']==signature:return {'id':identifier,'status':'cached'}
    units=sentences(text);vectors=[]
    for start in range(0,len(units),24):
        batch=units[start:start+24]
        vectors.extend(await embed([text[u['start']:u['end']] for u in batch]))
    if len(vectors)!=len(units):raise ValueError('Sentence embedding count mismatch')
    blocks=chunks(text,units,vectors)
    with store.connect() as db:
        # A concurrent edit must not make a stale index appear current.
        current=db.execute('SELECT content FROM public_papers WHERE id=?',(identifier,)).fetchone()
        if not current or digest(current['content'])!=signature:return {'id':identifier,'status':'changed'}
        db.execute('''INSERT INTO public_evidence_index(paper_id,content_hash,version,chunks) VALUES(?,?,?,?)
            ON CONFLICT(paper_id) DO UPDATE SET content_hash=excluded.content_hash,version=excluded.version,
            chunks=excluded.chunks,updated_at=CURRENT_TIMESTAMP''',(identifier,signature,VERSION,json.dumps(blocks,ensure_ascii=False)))
    return {'id':identifier,'status':'indexed','sentences':len(units),'chunks':len(blocks)}
