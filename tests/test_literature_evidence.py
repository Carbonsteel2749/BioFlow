import asyncio
import json
from backend.bioflow_platform import literature_evidence as e
from backend.bioflow_platform.shared_literature import evidence,fit_sources,initialize,save_paper
from backend.bioflow_platform.store import Store


def row(text,quality='abstract'):
    return {'id':'paper','metadata':json.dumps({'title':'Autism and gut microbiota','doi':'10.test/evidence'}),'quality':quality,'content':text}


def test_scientific_abbreviations_decimals_and_source_offsets():
    text='  Dr. Smith studied L. reuteri and E. coli (p = 0.05). This was compared with placebo, e.g. in mice.\n\nHowever, effects in humans remain uncertain.'
    units=e.sentences(text);values=[text[u['start']:u['end']] for u in units]
    assert len(values)==3
    assert values[0]=='Dr. Smith studied L. reuteri and E. coli (p = 0.05).'
    assert values[1].endswith('e.g. in mice.')
    blocks=e.chunks(text)
    assert all(b['text']==text[b['start']:b['end']] for b in blocks)
    assert any('However, effects in humans remain uncertain.' in b['text'] and 'placebo' in b['text'] for b in blocks)


def test_long_sentence_is_never_cut_to_fit_a_character_window():
    text='Microbiota '+('associated mechanisms '*100)+'were investigated. However, clinical benefit remains uncertain.'
    blocks=e.chunks(text)
    assert blocks[0]['text']==text
    result=evidence([row(text)],'microbiota')[0]['passages'][0]
    assert result['text']==text and result['offset']==0 and result['end']==len(text)


def test_semantic_topic_changes_use_vectors_and_keep_complete_sentences():
    parts=['Microbial pathways were investigated using detailed functional characterization across several independent cohorts. '*3,
           'Related metabolic mechanisms were evaluated with additional experimental measurements. '*3,
           'Clinical education priorities were assessed through interviews and community surveys. '*3]
    text=''.join(parts);units=e.sentences(text)
    vectors=[[1.,0.]]*6+[[0.,1.]]*3
    blocks=e.chunks(text,units,vectors)
    assert len(blocks)>=2 and all(b['method']=='sentence_semantic' for b in blocks)
    chosen=e.select(text,'education interviews',blocks,[0.,1.])[0]
    assert 'education' in chosen['text']
    assert chosen['text']==text[chosen['offset']:chosen['end']]
    assert chosen['offset']==0 or text[chosen['offset']-1].isspace()


def test_model_budget_selects_only_whole_original_sentences():
    text='A controlled experiment measured microbial metabolites. '+('Additional mechanisms were examined in mice. '*30)
    sources=evidence([row(text)],'microbial metabolites')
    fitted=fit_sources(sources,'microbial metabolites',budget=230)
    assert fitted
    for p in fitted[0]['passages']:
        assert p['text']==text[p['offset']:p['end']]
        assert p['text'].endswith('.')
        assert p['offset']==0 or text[p['offset']-1].isspace()


def test_semantic_index_cache_is_invalidated_by_a_source_edit(tmp_path):
    store=Store(tmp_path/'evidence.sqlite3');initialize(store)
    pid=save_paper(store,{'title':'Gut microbiota','doi':'10.test/source'},'Microbial functions were studied. Effects were observed.','abstract')
    calls=[]
    async def embed(texts):calls.append(texts);return [[1.,0.] for _ in texts]
    assert asyncio.run(e.index_paper(store,pid,embed))['status']=='indexed'
    assert asyncio.run(e.index_paper(store,pid,embed))['status']=='cached'
    assert len(calls)==1
    with store.connect() as db:db.execute('UPDATE public_papers SET content=? WHERE id=?',('Updated findings were reported.',pid))
    assert asyncio.run(e.index_paper(store,pid,embed))['status']=='indexed'
    assert len(calls)==2


def test_headings_stay_with_the_following_evidence_and_chinese_sentences_are_whole():
    text='Results\nProbiotics changed microbial metabolites. Clinical benefit was not established.\n\n菌群组成发生变化。然而，临床效果仍需验证。'
    blocks=e.chunks(text)
    assert blocks[0]['text'].startswith('Results\nProbiotics')
    chinese=[text[u['start']:u['end']] for u in e.sentences(text) if u['start']>=text.index('菌群')]
    assert chinese==['菌群组成发生变化。','然而，临床效果仍需验证。']


def test_budget_never_removes_a_qualification_from_its_claim():
    text='An apparent clinical benefit was observed. However, the small sample prevents a reliable conclusion.'
    units=e.coherent_units(text)
    assert len(units)==1 and text[units[0]['start']:units[0]['end']]==text
    sources=evidence([row(text)],'clinical benefit')
    assert fit_sources(sources,'benefit',budget=215)==[]


def test_short_abstract_includes_findings_and_limitations_not_just_the_background():
    text='Probiotics have been studied in autism.\nSeveral trials measured gastrointestinal and behavioral symptoms.\nGastrointestinal outcomes improved. However, behavioral evidence was inconsistent.'
    selected=evidence([row(text)],'autism probiotics')[0]['passages'][0]
    assert selected['text']==text and selected['method']=='complete_abstract'
    assert 'However, behavioral evidence was inconsistent.' in selected['text']
