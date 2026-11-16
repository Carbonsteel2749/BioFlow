from test_research_workspace import client,upload
from fastapi.testclient import TestClient
from backend.bioflow_platform import app as gateway

def test_ris_reference_is_saved_with_metadata_deduplicated_and_owned(client):
    body=b'TY  - JOUR\nTI  - Gut microbiome reference\nAU  - Researcher A\nPY  - 2025\nDO  - 10.1000/reference-test\nER  -\n'
    first=upload(client,'reference.ris',body,'literature')
    second=upload(client,'reference.ris',body,'literature')
    assert first['metadata']['personal_document_id']==second['metadata']['personal_document_id']
    papers=client.get('/api/library/literature').json()['items']
    assert len(papers)==1
    paper=papers[0]
    assert paper['metadata']['title']=='Gut microbiome reference'
    assert paper['metadata']['authors']=='Researcher A'
    assert paper['metadata']['year']=='2025'
    assert paper['metadata']['doi']=='10.1000/reference-test'
    assert paper['name'].endswith('.ris')
    assert client.get('/api/library/literature/'+paper['id']+'/download').content==body
    other=TestClient(gateway.app)
    r=other.post('/api/auth/register',json={'username':'reference_other','password':'developer'})
    other.headers['X-BioFlow-CSRF']=r.json()['user']['csrf']
    assert other.get('/api/library/literature').json()['items']==[]
    assert other.get('/api/library/literature/'+paper['id']+'/download').status_code==404
