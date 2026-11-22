"""Project-specific schema and readiness, without exposing individual clinical records."""

import hashlib
from functools import lru_cache

import numpy as np
import pandas as pd

from .data import active, load_frame, mapping_suggestions


@lru_cache(maxsize=1)
def example_hashes():
    from .examples import frames, raw

    tax, metadata, functional = frames()
    return {
        hashlib.sha256(raw(f, index)).hexdigest()
        for f, index in [(tax, True), (metadata, False), (functional, True)]
    }


def describe(project_id, datasets=None):
    datasets = datasets if datasets is not None else active(project_id)
    fields = []
    metadata = next((d for d in datasets if d["data_type"] == "metadata"), None)
    reference = next((d for d in datasets if d["data_type"] == "taxonomy"), None)
    if reference is None:
        reference = next((d for d in datasets if d["data_type"] == "functional"), None)
    if metadata:
        from .understanding import describe as field_profile

        meanings = {f["name"]: f for f in field_profile(metadata)["fields"]}
        frame = load_frame(metadata)
        if reference:
            frame = frame.loc[frame.index.intersection(reference["samples"])]
        for name in frame:
            text = frame[name].astype(str).str.strip().replace("", np.nan)
            numeric = pd.to_numeric(text, errors="coerce").replace([np.inf, -np.inf], np.nan)
            fields.append(
                {
                    "name": name,
                    "numeric_count": int(numeric.notna().sum()),
                    "missing_count": int(text.isna().sum()),
                    "unique_count": int(text.nunique()),
                    "continuous": meanings[name]["role"]
                    not in {"categorical", "ordinal", "identifier", "subject", "time"}
                    and bool(numeric.notna().sum() >= 5 and numeric.nunique() >= 3),
                }
                | {
                    k: meanings[name][k]
                    for k in ("meaning", "unit", "encoding", "role", "confirmed")
                }
            )
    return {
        "fields": fields,
        "contains_example_data": any(d["source_sha256"] in example_hashes() for d in datasets),
        "datasets": [
            {
                "id": d["id"],
                "filename": d["filename"],
                "type": d["data_type"],
                "revision": d["revision"],
                "samples": len(d["samples"]),
                "features": d["features"],
                "mapping_required": mapping_suggestions(project_id, d)["needs_confirmation"],
                "description": d.get("understanding", {}).get("description", ""),
                "fields": __import__("yanji.understanding", fromlist=["describe"]).describe(d)[
                    "fields"
                ]
                if d["data_type"] in {"table", "metadata"}
                else [],
            }
            for d in datasets
        ],
        "reference_samples": len(reference["samples"]) if reference else 0,
        "matched_metadata": len(set(reference["samples"]) & set(metadata["samples"]))
        if reference and metadata
        else 0,
    }
