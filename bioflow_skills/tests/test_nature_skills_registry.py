from bioflow_skills import list_catalog, run_skill


def test_nature_skills_are_listed_in_the_public_catalog():
    entries = {entry.name: entry for entry in list_catalog()}
    assert "nature-writing" in entries
    assert entries["nature-writing"].category == "writing"
    assert "agent" in entries["nature-writing"].tags


def test_nature_agent_skill_returns_a_safe_handoff_not_a_fake_execution():
    result = run_skill("nature-writing", {"task_context": "draft Methods from validated results"})
    assert result.status == "skipped"
    assert result.data["execution_mode"] == "agent"
    assert result.data["payload"]["task_context"].startswith("draft Methods")
    assert result.data["reference"] == "https://github.com/Yuan1z0825/nature-skills"
    assert result.data["maturity"] == "full_spec"
    assert result.data["routing_axes"]
    assert result.data["output_contract"]
    assert result.data["quality_checks"]


def test_nature_skills_keep_the_project_category_layout():
    entries = {entry.name: entry.category for entry in list_catalog()}
    assert entries["nature-writing"] == "writing"
    assert entries["nature-reader"] == "literature"
    assert entries["nature-statistics"] == "analysis"
    assert entries["nature-figure"] == "visualization"
    assert entries["nature-experiment-log"] == "common"
