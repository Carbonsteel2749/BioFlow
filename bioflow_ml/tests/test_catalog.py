from bioflow_ml import get_algorithm_catalog, list_algorithms, registry


def test_package_import_loads_builtin_algorithms() -> None:
    names = {spec.name for spec in registry.list_specs()}
    assert {
        "pca",
        "kmeans",
        "hierarchical",
        "tsne",
        "umap",
        "logistic_regression",
        "svm",
        "random_forest",
        "lasso",
        "rfe",
    }.issubset(names)


def test_algorithm_catalog_is_sorted_and_json_friendly() -> None:
    catalog = get_algorithm_catalog()
    assert [entry["name"] for entry in catalog] == sorted(
        entry["name"] for entry in catalog
    )
    assert all(isinstance(entry["tasks"], list) for entry in catalog)
    assert {"name", "version", "tasks", "description"}.issubset(catalog[0])


def test_algorithm_catalog_filters_by_task() -> None:
    entries = list_algorithms(task="classification")
    assert entries
    assert all("classification" in entry.tasks for entry in entries)
