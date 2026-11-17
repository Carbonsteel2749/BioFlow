import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "article_writing"))
from frontend import server


def test_catalog_and_invalid_selection():
    catalog = server.writing_skills()["items"]
    assert {s["name"] for s in catalog if s["supported"]} == {
        "bio-fact-ledger", "methods-from-provenance", "section-planner", "paragraph-writer", "claim-verifier",
        "nature-writing", "nature-polishing", "nature-reviewer", "nature-response"}
    client = TestClient(server.app)
    for skills, comments in [(["unknown"], ""), (["nature-data"], ""), (["nature-response"], "")]:
        response = client.post("/api/sessions", json={"skills": skills, "reviewer_comments": comments})
        assert response.status_code == 422
    server.validate_writing_skills(server.StartRequest(skills=["nature-response"], reviewer_comments="Explain methods"))


def test_preview_uses_selected_library_and_credentials(monkeypatch):
    captured = {}
    class Port:
        def __init__(self, **kwargs): captured.update(kwargs)
        def search(self, query, top_k): return []
    monkeypatch.setattr(server, "LiveLiteraturePort", Port)
    response = TestClient(server.app).get("/api/literature/preview", params={
        "query": "microbiome", "use_live": True, "knowledge_base_id": "chosen-kb"},
        headers={"authorization": "test-token", "x-ignore": "omit"})
    assert response.status_code == 200
    assert captured["knowledge_base_id"] == "chosen-kb"
    assert captured["request_headers"] == {"authorization": "test-token"}


@pytest.mark.parametrize("selected", [[], ["nature-writing"], ["nature-polishing"], ["nature-writing", "nature-polishing"]])
def test_connected_generation_applies_only_selected_skills(tmp_path, monkeypatch, selected):
    from test_material_intake import package
    from article_writing.orchestrator import pipeline as module
    from article_writing.contracts import SectionId
    root = package(tmp_path)
    captured = {}
    def compose(draft, llm, **kwargs):
        captured[draft.section.value] = kwargs["instructions"]
        draft.markdown = "Descriptive analysis only."
        return draft
    monkeypatch.setattr(module, "compose_section_from_catalog", compose)
    prompts = []
    class Model:
        enabled = True
        def generate(self, prompt, *args, **kwargs):
            prompts.append(prompt)
            return "Descriptive analysis only."
    pipe = module.WritingPipeline(fixtures_dir=ROOT / "article_writing/fixtures",
        adapter_mode="live", analysis_run_dir=str(root), literature_enabled=False,
        react_enabled=False, brief_text="Describe metagenomic profiles", llm=Model(),
        selected_skills=selected)
    result = pipe.run(run_id="skill-test")
    assert captured
    # Material packages write Methods with methods-from-provenance (no model call);
    # the selectable playbooks apply to the model-written chapters.
    assert "methods" not in captured
    assert result.drafts["methods"].metadata["catalog_compose"]["source"] == "methods-from-provenance"
    # Results are written paragraph by paragraph; selected playbooks reach every paragraph prompt.
    results_prompts = " ".join(p for p in prompts if "Section: results" in p)
    assert results_prompts
    assert ("nature-writing playbook" in results_prompts) == ("nature-writing" in selected)
    assert ("nature-polishing playbook" in results_prompts) == ("nature-polishing" in selected)
    assert ("nature-polishing playbook" in captured["back_matter"]) == ("nature-polishing" in selected)
    assert result.drafts["back_matter"].metadata["skills"]["polishing"] == ("nature-polishing" in selected)
