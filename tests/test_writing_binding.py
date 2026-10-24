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


def test_writing_request_propagates_project_and_auth(monkeypatch):
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
    monkeypatch.setattr(server.store,'create_from_pipeline',create)
    response=TestClient(server.app).post('/api/sessions',json={'knowledge_base_id':'kb-a','use_live_literature':True},headers={'Cookie':'ai_localbase_session=test','X-CSRF-Token':'csrf'})
    assert response.status_code==200
    assert captured['knowledge_base_id']=='kb-a'
    assert captured['literature_headers']['cookie']=='ai_localbase_session=test'
    assert captured['literature_headers']['x-csrf-token']=='csrf'
