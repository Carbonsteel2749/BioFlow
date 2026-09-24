from bioflow_skills import SkillInput, get_registry, list_catalog, run_skill


def test_builtin_skills_are_registered() -> None:
    names = {entry.name for entry in list_catalog()}
    assert {"file_profile", "data_summary", "task_brief"}.issubset(names)


def test_runner_executes_builtin_skill() -> None:
    result = run_skill("data_summary", {"samples": ["S1", "S2"], "group": "case"})
    assert result.succeeded
    assert result.data["fields"]["samples"]["length"] == 2
    assert result.metadata["runner"] == "bioflow_skills"


def test_runner_returns_failure_for_unknown_skill() -> None:
    result = run_skill("does_not_exist")
    assert result.failed


def test_task_brief_requires_objective() -> None:
    result = run_skill("task_brief", SkillInput(payload={}))
    assert result.failed
    assert get_registry().has("task_brief")
