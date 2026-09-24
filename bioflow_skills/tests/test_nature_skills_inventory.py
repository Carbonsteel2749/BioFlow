from bioflow_skills import list_catalog


def test_all_nature_reference_skills_are_registered_from_project_categories():
    entries = {entry.name: entry for entry in list_catalog()}
    nature_entries = {
        name: entry for name, entry in entries.items()
        if "nature-skills-reference" in entry.tags
    }
    assert len(nature_entries) == 19
    assert "nature-shared" not in nature_entries
    assert all("adapted" in entry.tags for entry in nature_entries.values())
    assert all(entry.input_keys == ("task_context",) for entry in nature_entries.values())
