"""Build a PaperBrief from facts already present in an AnalysisBundle.

Used only when the user did not supply a brief JSON. The result describes the
packaged measurements. It does not invent a cohort, disease, or hypothesis.
"""

from __future__ import annotations

from article_writing.contracts import AnalysisBundle, PaperBrief


def brief_from_topic(text: str) -> PaperBrief:
    """Build a PaperBrief from a paragraph the user typed.

    The paragraph sets the title and the question. It does not add measurements.
    """

    topic = " ".join(str(text or "").split())
    if not topic:
        raise ValueError("研究课题是空的")
    return PaperBrief(
        title=_title_from_topic(topic),
        research_question=topic,
        notes=[
            "The user typed this topic. It sets the title and the question only.",
            "Sample counts, figures, methods, and conclusions still come from the analysis package.",
        ],
    )


def _title_from_topic(topic: str) -> str:
    cut = len(topic)
    for index, char in enumerate(topic):
        if char in "。！？.!?":
            cut = index
            break
    title = topic[:cut].strip(" ，,;；:：")
    if not title:
        title = topic
    if len(title) > 80:
        title = title[:80].rstrip(" ，,;；:：")
    return title


def brief_from_analysis(analysis: AnalysisBundle) -> PaperBrief:
    raw = analysis.raw if isinstance(analysis.raw, dict) else {}
    task_id = str(raw.get("task_id") or "").strip()
    sample_count = _as_int(analysis.metrics.get("sample_count"))
    metagenomic = _is_metagenomic(analysis)
    sample_phrase = f"{sample_count}-sample " if sample_count else ""

    if metagenomic:
        if sample_count:
            title = f"Descriptive shotgun metagenomic profiling of a {sample_count}-sample dataset"
        else:
            title = "Descriptive shotgun metagenomic profiling of the supplied dataset"
        question = (
            "What quality-control, taxonomic, and functional features does this "
            f"{sample_phrase}shotgun metagenomic package report?"
        )
        modality = "shotgun_metagenomics"
        keywords = ["shotgun metagenomics"]
    else:
        title = "Descriptive summary of the supplied analysis package"
        if sample_count:
            title = f"Descriptive summary of a {sample_count}-sample analysis package"
        question = (
            "Which measurements and findings are already reported in this analysis package?"
        )
        modality = "analysis_package"
        keywords = ["analysis package"]

    methods = analysis.methods or {}
    for key, label in (
        ("qc", "quality control"),
        ("taxonomy", "taxonomy"),
        ("functional_annotation", "functional annotation"),
        ("host_depletion", "host depletion"),
    ):
        if str(methods.get(key) or "").strip():
            keywords.append(label)

    notes = [
        "Generated from the analysis package because no paper brief JSON was provided.",
        "The title and research question describe packaged measurements only.",
        "Cohort, disease context, ethics, and study design were not in the package and were left empty.",
    ]
    if task_id:
        notes.append(f"Analysis task: {task_id}")

    return PaperBrief(
        title=title,
        research_question=" ".join(question.split()),
        organism_or_system="not specified in the analysis package",
        data_modality=modality,
        keywords=keywords,
        notes=notes,
    )


def _is_metagenomic(analysis: AnalysisBundle) -> bool:
    if analysis.microbiome_sequencing is not None:
        return True
    methods = analysis.methods or {}
    return any(str(methods.get(key) or "").strip() for key in ("taxonomy", "functional_annotation"))


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None
