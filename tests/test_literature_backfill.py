import json
from datetime import date
from backend.bioflow_platform.store import Store
from backend.bioflow_platform.shared_literature import initialize,save_paper
from deploy.backfill_public_literature import parse_records,classify,merge_paper,pmc_body,job_db

START=date(2006,11,12);END=date(2026,11,12)


def paper(**updates):
    return {'pmid':'123','doi':'10.test/a','title':'Gut microbiota in autism spectrum disorder','abstract':'Gut microbiota was studied in children with autism. Intestinal microbial functions were measured using validated protocols in independent cohorts.',
        'authors':'Researcher A','journal':'Journal','languages':['eng'],'publication_types':['Journal Article'],'mesh':['Autism Spectrum Disorder'],
        'year':'2025','publication_date':'2025-06-01','publication_date_end':'2025-06-01','date_precision':'day','url':'https://doi.org/10.test/a',**updates}


def test_english_domain_scope_and_retraction_filters():
    assert classify(paper(),START,END)==('active',[])
    assert classify(paper(languages=['chi']),START,END)[0]=='quarantine'
    assert 'topic_needs_review' in classify(paper(title='ASD heart surgery outcomes',abstract='Atrial septal defect cases were studied.'),START,END)[1]
    assert 'correction_or_retraction' in classify(paper(publication_types=['Retracted Publication']),START,END)[1]
    assert 'missing_or_short_abstract' in classify(paper(abstract=''),START,END)[1]


def test_parser_preserves_inline_text_and_does_not_require_doi():
    xml='''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>456</PMID><Article><ArticleTitle>Gut <i>microbiota</i> in autism</ArticleTitle><Language>eng</Language><Abstract><AbstractText Label="RESULTS">Before <b>important</b> after.</AbstractText></Abstract><Journal><Title>J</Title><JournalIssue><PubDate><Year>2027</Year></PubDate></JournalIssue></Journal><ArticleDate DateType="Electronic"><Year>2026</Year><Month>10</Month><Day>01</Day></ArticleDate></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'''
    p=parse_records(xml)[0]
    assert p['title']=='Gut microbiota in autism' and p['abstract']=='RESULTS: Before important after.'
    assert p['doi']=='' and p['url']=='https://pubmed.ncbi.nlm.nih.gov/456/'
    assert p['publication_date']=='2026-10-01'


def test_date_boundaries_are_reviewed_without_future_watermark():
    assert 'date_needs_review' in classify(paper(publication_date='2026-12-01',publication_date_end='2026-12-01'),START,END)[1]
    assert 'date_needs_review' in classify(paper(publication_date='2006-11-01',publication_date_end='2006-11-30',date_precision='month'),START,END)[1]
    assert classify(paper(publication_date=START.isoformat(),publication_date_end=START.isoformat()),START,END)[0]=='active'


def test_existing_curated_status_full_text_and_vectors_are_preserved(tmp_path):
    store=Store(tmp_path/'library.sqlite3');initialize(store)
    pid=save_paper(store,paper(),'Existing complete text','fulltext',vector=[1,0])
    with store.connect() as db:db.execute("UPDATE public_papers SET status='removed' WHERE id=?",(pid,))
    found,result=merge_paper(store,paper(),'active',[],'test-run')
    assert found==pid and result=='existing'
    with store.connect() as db:r=db.execute('SELECT * FROM public_papers WHERE id=?',(pid,)).fetchone()
    assert (r['status'],r['content'],r['quality'],r['vector'])==('removed','Existing complete text','fulltext','[1, 0]')


def test_doi_or_pmid_dedup_and_resume_queue(tmp_path):
    store=Store(tmp_path/'library.sqlite3');initialize(store)
    a,_=merge_paper(store,paper(doi=''),'active',[],'test')
    b,_=merge_paper(store,paper(),'active',[],'test')
    assert a==b
    db=job_db(tmp_path)
    with db:
        db.executemany('INSERT OR IGNORE INTO papers(pmid) VALUES(?)',[('1',),('1',),('2',)])
        db.execute('UPDATE papers SET metadata=? WHERE pmid=?',('{}','1'))
    db.close();db=job_db(tmp_path)
    assert [r[0] for r in db.execute('SELECT pmid FROM papers WHERE metadata IS NULL')]==['2']


def test_pmc_oai_uses_article_body_and_keeps_license():
    body='Results from this experiment were reported in full. '*25
    xml=f'<OAI-PMH xmlns="urn:oai"><GetRecord><record><metadata><article><front><permissions><license>CC BY</license></permissions></front><body><sec><title>Results</title><p>{body}</p></sec></body></article></metadata></record></GetRecord></OAI-PMH>'
    content,metadata=pmc_body(xml)
    assert content=='Results\n\n'+body.strip() and metadata['license']=='CC BY'
    assert pmc_body('<OAI-PMH><error code="cannotDisseminateFormat">Not available</error></OAI-PMH>')[0]==''


def test_book_chapter_is_explicitly_reviewed_not_dropped():
    xml='''<PubmedArticleSet><PubmedBookArticle><BookDocument><PMID>789</PMID><ArticleIdList><ArticleId IdType="doi">10.test/chapter</ArticleId></ArticleIdList><Book><BookTitle>Autism</BookTitle><PubDate><Year>2021</Year><Month>08</Month><Day>20</Day></PubDate></Book><ArticleTitle>Gut microbiota and autism</ArticleTitle><Language>eng</Language><Abstract><AbstractText>Intestinal microbiota and gut microbial metabolites were evaluated in autism spectrum disorder in several studies with appropriate scientific methods.</AbstractText></Abstract></BookDocument></PubmedBookArticle></PubmedArticleSet>'''
    p=parse_records(xml)[0]
    assert p['pmid']=='789' and p['doi']=='10.test/chapter' and p['publication_date']=='2021-08-20'
    assert 'book_chapter_review' in classify(p,START,END)[1]
