"""Clearly synthetic examples, deliberately separate from the user's real study."""

import numpy as np
import pandas as pd

from . import db
from .contracts import ImportOptions
from .data import import_bytes


def frames():
    rng = np.random.default_rng(42)
    samples = [f"DEMO_{i + 1:02}" for i in range(16)]
    features = [
        f"k__Bacteria|p__Demo|g__Example_{i // 2}|s__Example_species_{i + 1}" for i in range(12)
    ]
    values = []
    for i in range(16):
        alpha = (
            np.array([6, 5, 3, 2, 1, 1, 2, 1, 1, 1, 1, 1], dtype=float)
            if i < 8
            else np.array([1, 1, 1, 2, 5, 6, 1, 1, 2, 3, 2, 1], dtype=float)
        )
        values.append(rng.dirichlet(alpha))
    taxonomy = pd.DataFrame(np.array(values).T * 100, index=features, columns=samples)
    taxonomy.index.name = "ID"
    metadata = pd.DataFrame(
        {
            "sample_id": samples,
            "group": ["模拟对照"] * 8 + ["模拟干预"] * 8,
            "subject_id": [f"P{i + 1:02}" for i in range(16)],
        }
    )
    rows = []
    names = []
    for i in range(5):
        total = rng.uniform(0.02, 0.2, 16)
        names.append(f"DEMO-PWY-{i + 1}: simulated pathway {i + 1}")
        rows.append(total)
        for j in range(3):
            names.append(
                f"DEMO-PWY-{i + 1}: simulated pathway {i + 1}|g__Example_{j}.s__Example_species_{j + 1}"
            )
            rows.append(total * rng.uniform(0.1, 0.25, 16))
    functional = pd.DataFrame(rows, index=names, columns=samples)
    functional.index.name = "# Pathway"
    return taxonomy, metadata, functional


def raw(frame, index=True):
    return frame.to_csv(sep="\t", index=index).encode()


def install_example():
    project = db.create(
        "project",
        name="微生物组研究示例",
        description="合成数据，仅用于演示与软件验证，不代表真实生物学发现。",
        active_datasets={},
        study={"group_column": "group", "independent": True, "excluded_samples": []},
        archived=False,
        synthetic=True,
    )
    tax, meta, func = frames()
    import_bytes(
        project["id"], "demo-taxonomy.tsv", raw(tax), ImportOptions(kind="taxonomy", unit="percent")
    )
    import_bytes(
        project["id"], "demo-metadata.tsv", raw(meta, False), ImportOptions(kind="metadata")
    )
    import_bytes(
        project["id"],
        "demo-pathabundance_relab.tsv",
        raw(func),
        ImportOptions(kind="functional", unit="fraction"),
    )
    return db.get(project["id"], "project")
