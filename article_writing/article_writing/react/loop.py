"""Deterministic Related-Work ReAct loop over LiteraturePort.

Inspired by PaperQA (search → gather evidence → generate) and STORM
(multi-perspective / multi-query pre-writing), but implemented as a thin,
LLM-free planner so Phase 3 stays mock-friendly and dependency-light.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from article_writing.adapters.base import LiteraturePort
from article_writing.contracts import LiteratureHit, PaperBrief
from article_writing.react.tools import tool_get, tool_search
from article_writing.react.trajectory import ReactStep, ReactTrajectory


@dataclass
class ReactGatherResult:
    hits: list[LiteratureHit] = field(default_factory=list)
    trajectory: ReactTrajectory = field(
        default_factory=lambda: ReactTrajectory(goal="related_work")
    )


def build_search_queries(brief: PaperBrief, seed_query: str = "") -> list[str]:
    """STORM-like multi-query plan derived only from PaperBrief + seed query."""

    queries: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        cleaned = " ".join((value or "").split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            queries.append(cleaned)
            seen.add(key)

    _add(seed_query)
    _add(brief.research_question)
    for keyword in brief.keywords:
        _add(keyword)
    if brief.organism_or_system and brief.data_modality:
        _add(f"{brief.organism_or_system} {brief.data_modality.replace('_', ' ')}")
    else:
        _add(brief.organism_or_system)
        _add(brief.data_modality.replace("_", " ") if brief.data_modality else "")
    _add(brief.title)
    return queries


def run_related_work_react(
    *,
    brief: PaperBrief,
    literature_port: LiteraturePort,
    seed_query: str = "",
    max_steps: int = 8,
    search_top_k: int = 3,
) -> ReactGatherResult:
    """Run Thought/Action/Observation cycles, then return deduplicated hits."""

    max_steps = max(1, int(max_steps))
    # Reserve room for optional get + mandatory finish within the step budget.
    query_budget = max(1, max_steps - 2)
    queries = build_search_queries(brief, seed_query=seed_query)[:query_budget]
    trajectory = ReactTrajectory(
        goal="Gather literature evidence for Related Work",
        seed_query=seed_query,
        max_steps=max_steps,
    )

    gathered: dict[str, LiteratureHit] = {}
    step_no = 0
    did_inspect = False

    # Phase A: multi-query search (gather).
    for query in queries:
        if step_no >= max_steps:
            break
        step_no += 1
        thought = (
            f"Search literature for Related Work query {step_no}: {query!r}."
        )
        hits, observation = tool_search(literature_port, query, top_k=search_top_k)
        paper_ids = [hit.paper_id for hit in hits if hit.paper_id]
        for hit in hits:
            if hit.paper_id and hit.paper_id not in gathered:
                gathered[hit.paper_id] = hit
        trajectory.add_step(
            ReactStep(
                step=step_no,
                thought=thought,
                action="search",
                action_input={"query": query, "top_k": search_top_k},
                observation=observation,
                paper_ids=paper_ids,
            )
        )

    # Phase B: inspect one top paper for an evidence snippet (gather evidence).
    if gathered and step_no < max_steps:
        step_no += 1
        inspect_id = next(iter(gathered))
        thought = (
            f"Inspect paper {inspect_id!r} to gather an evidence snippet "
            "before drafting Related Work."
        )
        hit, observation = tool_get(literature_port, inspect_id)
        if hit is not None and hit.paper_id:
            gathered[hit.paper_id] = hit
        trajectory.add_step(
            ReactStep(
                step=step_no,
                thought=thought,
                action="get",
                action_input={"paper_id": inspect_id},
                observation=observation,
                paper_ids=[inspect_id] if hit is not None else [],
            )
        )
        did_inspect = True

    # Phase C: finish → caller generates the section draft from gathered hits.
    if step_no < max_steps or not trajectory.steps or trajectory.steps[-1].action != "finish":
        step_no += 1
        if gathered:
            thought = (
                "Enough literature evidence was gathered via LiteraturePort; "
                "draft Related Work from the collected records."
            )
            finish_reason = "gathered_and_inspected" if did_inspect else "gathered"
            observation = f"finish with {len(gathered)} unique paper(s)"
        else:
            thought = (
                "No literature was retrieved from LiteraturePort; stop ReAct and "
                "let Related Work degrade with an empty evidence set."
            )
            finish_reason = "no_hits"
            observation = "finish with 0 papers"
        trajectory.add_step(
            ReactStep(
                step=step_no,
                thought=thought,
                action="finish",
                action_input={"n_hits": len(gathered)},
                observation=observation,
                paper_ids=list(gathered),
            )
        )
        trajectory.finished = True
        trajectory.finish_reason = finish_reason
    else:
        trajectory.finished = True
        trajectory.finish_reason = trajectory.finish_reason or "max_steps"

    return ReactGatherResult(hits=list(gathered.values()), trajectory=trajectory)
