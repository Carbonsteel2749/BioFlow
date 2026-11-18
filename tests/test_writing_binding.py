import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'article_writing'))


def test_knowledge_base_reaches_actual_adapter():
    from article_writing.adapters import build_adapters
    from article_writing.orchestrator import WritingPipeline
    bundle=build_adapters('live',knowledge_base_id='kb-project-a',literature_url='http://127.0.0.1:8080',literature_headers={'Cookie':'session=test'})
    assert bundle.literature.knowledge_base_id=='kb-project-a'
    assert bundle.literature.backend=='ai-localbase'
    assert bundle.literature._make_ai_localbase().request_headers=={'Cookie':'session=test'}
    pipeline=WritingPipeline(adapter_mode='live',knowledge_base_id='kb-project-b',llm_enabled=False)
    assert pipeline.literature_port.knowledge_base_id=='kb-project-b'


def test_writing_entry_serves_only_api():
    from backend.bioflow_platform.writing_api import app
    paths=[r.path for r in app.routes]
    assert '/api/sessions' in paths
    assert '/' not in paths
    assert '/static' not in paths


def test_unselected_literature_returns_no_other_project_evidence():
    from article_writing.orchestrator import WritingPipeline
    pipeline=WritingPipeline(adapter_mode='live',literature_enabled=False,llm_enabled=False)
    assert pipeline.literature_port.search('test')==[]


def test_writing_request_propagates_project_and_auth(monkeypatch,tmp_path):
    import time
    from fastapi.testclient import TestClient
    from frontend import server
    captured={}
    class Session:
        def as_dict(self):
            return {'session_id':'test'}
    def create(**kwargs):
        captured.update(kwargs)
        return Session()
    monkeypatch.setattr(server,'model_status',lambda config:{'model_available':True})
    monkeypatch.setattr(server.store,'root',tmp_path)
    monkeypatch.setattr(server.store,'create_from_pipeline',create)
    response=TestClient(server.app).post('/api/sessions',json={'knowledge_base_id':'kb-a','use_live_literature':True},headers={'Cookie':'ai_localbase_session=test','X-CSRF-Token':'csrf'})
    assert response.status_code==200
    body=response.json()
    assert body['status']=='running'
    assert body['session_id']
    for _ in range(50):
        if captured:
            break
        time.sleep(0.02)
    assert captured['knowledge_base_id']=='kb-a'
    assert captured['literature_headers']['cookie']=='ai_localbase_session=test'
    assert captured['literature_headers']['x-csrf-token']=='csrf'
    server.store._sessions.pop(body['session_id'],None)


def test_generation_returns_before_the_manuscript_is_ready(monkeypatch,tmp_path):
    import threading
    import time
    from fastapi.testclient import TestClient
    from frontend import server
    from frontend.session_store import WritingSession
    release=threading.Event()
    def create(**kwargs):
        assert release.wait(3)
        return WritingSession(session_id=kwargs['session_id'],run_id='ui-test',title='done',research_question='',sections={},section_order=[],output_dir=str(tmp_path/kwargs['session_id']))
    monkeypatch.setattr(server,'model_status',lambda config:{'model_available':True})
    monkeypatch.setattr(server.store,'root',tmp_path)
    monkeypatch.setattr(server.store,'create_from_pipeline',create)
    client=TestClient(server.app)
    response=client.post('/api/sessions',json={'brief_text':'主题。补充说明'})
    assert response.status_code==200
    body=response.json()
    assert body['status']=='running'
    assert body['progress']=='正在准备稿件'
    assert body['title']=='主题'
    current=client.get('/api/sessions/'+body['session_id']).json()
    assert current['status']=='running'
    release.set()
    ready={}
    for _ in range(50):
        ready=client.get('/api/sessions/'+body['session_id']).json()
        if ready['status']=='ready':
            break
        time.sleep(0.02)
    assert ready['status']=='ready'
    assert ready['progress']=='稿件已生成'
    server.store._sessions.pop(body['session_id'],None)


def test_generation_failure_is_visible_on_the_session(monkeypatch,tmp_path):
    import time
    from fastapi.testclient import TestClient
    from frontend import server
    def create(**kwargs):
        raise RuntimeError('模型没有返回')
    monkeypatch.setattr(server,'model_status',lambda config:{'model_available':True})
    monkeypatch.setattr(server.store,'root',tmp_path)
    monkeypatch.setattr(server.store,'create_from_pipeline',create)
    client=TestClient(server.app)
    body=client.post('/api/sessions',json={'brief_text':'主题'}).json()
    current={}
    for _ in range(50):
        current=client.get('/api/sessions/'+body['session_id']).json()
        if current['status']=='failed':
            break
        time.sleep(0.02)
    assert current['status']=='failed'
    assert '模型没有返回' in current['error']
    server.store._sessions.pop(body['session_id'],None)
