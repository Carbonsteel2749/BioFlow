"""Write one section from the archive catalog, not from a fixed file list.

Similar papers, when retrieval returned any, are structure examples only.
Their measurements are not facts for this manuscript. With no papers, the
section role prompt is the only writing guide.
"""

from __future__ import annotations

import re

from article_writing.adapters.archive_catalog import prefer_png_figures
from article_writing.contracts import FigureRef, LiteratureHit, PaperBrief, SectionDraft, SectionId
from article_writing.layout.figures import render_figure_embed
from article_writing.layout.manuscript import hide_catalog_ids
from article_writing.llm.base import LLMClient

SECTION_ROLES: dict[SectionId, str] = {
    SectionId.abstract: (
        "Write one abstract paragraph from the results and discussion already written. "
        "Include what was done, at most three measurements, and one limit. "
        "Do not mention figures or tables."
    ),
    SectionId.introduction: (
        "Write the introduction from the chapters already written. "
        "Open from the analysis in the brief, state the question, then what the results established. "
        "Do not restate every number and do not mention figures."
    ),
    SectionId.methods: (
        "Write the methods in past tense, one paragraph per procedure in this section's facts. "
        "Do not add ethics approval, eligibility, clinical assessment, exposure, or a statistical test."
    ),
    SectionId.results: (
        "Write the results by the blocks in this section's facts. "
        "Each block is one paragraph: state what that block shows, using its numbers, "
        "then cite that block's primary display. "
        "Do not write a paragraph per display, and do not add a block that is absent."
    ),
    SectionId.discussion: (
        "Interpret the results chapter. Say what those measurements can and cannot support, "
        "using this section's facts. Do not paste the results again and do not add a new result."
    ),
    SectionId.conclusion: (
        "Close in one short paragraph from the results and discussion already written. "
        "Do not mention figures and do not add a new claim."
    ),
    SectionId.back_matter: (
        "Name the tables in this section's facts. "
        "Do not restate findings, do not write an abbreviation list, and do not invent a repository."
    ),
}

# The editor still shows IMRaD order. Generation follows the paper: methods and
# results first, then the chapters that have to refer to them.
WRITE_ORDER: tuple[SectionId, ...] = (
    SectionId.methods,
    SectionId.results,
    SectionId.discussion,
    SectionId.introduction,
    SectionId.abstract,
    SectionId.conclusion,
    SectionId.back_matter,
)
_PRIOR_CHAPTERS: dict[SectionId, tuple[SectionId, ...]] = {
    SectionId.methods: (),
    SectionId.results: (SectionId.methods,),
    SectionId.discussion: (SectionId.results,),
    SectionId.introduction: (SectionId.methods, SectionId.results, SectionId.discussion),
    SectionId.abstract: (SectionId.results, SectionId.discussion),
    SectionId.conclusion: (SectionId.results, SectionId.discussion),
    SectionId.back_matter: (),
}
_PRIOR_LIMIT = 1600

# Pictures are shown once, under Results. Other sections stay prose.
_EMBED_SECTIONS = {SectionId.results}
_CITE_SECTIONS = {SectionId.results, SectionId.discussion, SectionId.back_matter}
_DISPLAY_ID = re.compile(r"`?cat_(?:figures|tables)_[A-Za-z0-9_]+`?")
_MARKER = re.compile(r"\[\[[^\]]+\]\]")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_EMBED_LINE = re.compile(
    r"^(?:"
    r"!\[[^\]]*\]\([^)]+\)"
    r"|\*\*(?:Figure|Table)\s+\d+\.\*\*.*"
    r"|> Upstream asset\b.*"
    r")$"
)

_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")


class CatalogComposeError(RuntimeError):
    """The model did not produce a usable section, so the run must stop."""


def compose_section_from_catalog(
    draft: SectionDraft,
    client: LLMClient,
    *,
    catalog: list[dict[str, str]],
    brief: PaperBrief,
    literature: list[LiteratureHit] | None = None,
    language: str = "en+zh",
    instructions: str = "",
    strict: bool = False,
    known_labels: dict[str, str] | None = None,
    facts: str | None = None,
    prior_prose: str = "",
    translate: bool = True,
) -> SectionDraft:
    """Replace template prose with a catalog-grounded draft. Keep the template on failure."""

    warnings = list(draft.warnings)
    meta = dict(draft.metadata or {})
    catalog = prefer_png_figures(catalog)
    role = SECTION_ROLES.get(draft.section, "Write this section from the catalog only.")
    prompt = _prompt(
        section=draft.section,
        title=draft.title,
        role=role,
        brief=brief,
        literature=literature or [],
        language=language,
        instructions=instructions,
        facts=facts if facts is not None else _fact_sheet(catalog),
        prior_prose=prior_prose,
    )
    system = (
        "You write one section of a scientific paper. "
        "Every number and display must come from the catalog. "
        "Similar papers teach organization only. "
        "Output plain prose only, with no code fence and no Markdown syntax."
    )
    try:
        raw = client.generate(prompt, system=system)
    except Exception as error:  # noqa: BLE001 — strict runs stop; tests may keep the template
        message = f"{draft.section.value}: catalog compose failed: {error}"
        if strict:
            raise CatalogComposeError(message) from error
        warnings.append(f"catalog compose failed; kept template: {error}")
        meta["catalog_compose"] = {"used": False, "error": str(error)[:300]}
        return draft.model_copy(update={"warnings": warnings, "metadata": meta})

    text = _drop_repeated_lines(_clean_markdown(raw))
    if draft.section in _EMBED_SECTIONS:
        labels = _appearance_labels(text, catalog)
    elif draft.section in _CITE_SECTIONS:
        labels = dict(known_labels or {}) or _appearance_labels(text, catalog)
    else:
        labels = {}
    cited = [item for item in catalog if str(item.get("item_id") or "") in labels]
    cited.sort(key=lambda item: _first_pos(text, item) or 0)
    source = text
    text = _apply_labels(text, catalog, labels)
    if draft.section in _EMBED_SECTIONS:
        shown = [item for item in cited if not _unsupported_secondary(source, item)]
        text, figure_refs = _splice_displays(text, shown, labels)
        text, figure_refs = _append_uncited_figures(source, text, catalog, labels, figure_refs)
        text = hide_catalog_ids(text, labels)
        text = _MARKER.sub("", text)
    else:
        figure_refs = []
        if draft.section not in _CITE_SECTIONS:
            text = _MARKER.sub("", text)
            text = _DISPLAY_ID.sub("", text)
            text = hide_catalog_ids(text, {})
        else:
            text = hide_catalog_ids(text, labels)
            text = _MARKER.sub("", text)
        text = remove_embedded_displays(text)
    missing_numbers = _numbers_not_in_catalog(text, catalog)
    if missing_numbers:
        warnings.append(
            "prose contains numbers that are not in the archive catalog: "
            + ", ".join(missing_numbers[:8])
        )
    if translate:
        text = _translate_if_needed(client, text, language)
    meta["catalog_compose"] = {
        "used": True,
        "cited": [str(item.get("item_id")) for item in cited],
        "literature_examples": len(literature or []),
    }
    meta["display_labels"] = labels
    meta["template_markdown_chars"] = len(draft.markdown)
    return draft.model_copy(
        update={
            "markdown": text if text.endswith("\n") else text + "\n",
            "figure_refs": figure_refs or list(draft.figure_refs),
            "warnings": warnings,
            "metadata": meta,
        }
    )


def _prompt(
    *,
    section: SectionId,
    title: str,
    role: str,
    brief: PaperBrief,
    literature: list[LiteratureHit],
    language: str,
    instructions: str,
    facts: str,
    prior_prose: str,
) -> str:
    papers = _literature_frame(literature)
    written = prior_prose.strip() or "(none yet)"
    prompt = (
        f"Section: {section.value} ({title}).\n"
        "You are writing one paper in order. Write only this section, in English. "
        "A later step translates it.\n\n"
        f"Section role:\n{role}\n\n"
        f"Brief title: {brief.title or '(none)'}\n"
        f"Research question: {brief.research_question or '(none)'}\n"
        "The research question only frames this paper. It is not a measurement.\n\n"
        f"Chapters already written:\n{written}\n\n"
        f"{papers}\n\n"
        "This section's facts (do not use measurements or procedures that are absent here "
        "and absent from the chapters already written):\n"
        f"{facts}\n\n"
        "Write connected prose. Do not use Markdown headings, bold, lists, or code fences. "
        "Do not print catalog ids, file names, or backtick codes. "
        "Do not repeat a chapter already written. "
        "Do not add a disease, cohort, hypothesis, method, test, or number that these facts do not contain. "
        + (
            "When a sentence uses a figure or table, cite it only as [[short name]] from this section's facts. "
            "Do not write Figure or Table numbers yourself. "
            if section in {SectionId.results, SectionId.back_matter}
            else "You may name a Figure or Table number already used in the results chapter. Do not insert an image. "
            if section == SectionId.discussion
            else "Do not cite or insert figures or tables in this section. "
        )
        + "Do not invent files, statistics, or conclusions that are not in these facts."
    )
    if "zh" not in language.lower():
        prompt += " Do not write a Chinese version."
    extra = (instructions or "").strip()
    if extra:
        prompt += f"\n\nAdditional writing rules:\n{extra}"
    return prompt


def _literature_frame(literature: list[LiteratureHit]) -> str:
    if not literature:
        return (
            "No similar papers were retrieved. "
            "Follow the section role and the fact sheet."
        )
    lines = [
        "Similar papers were retrieved. Use them only as examples of how a section is organized. "
        "Do not copy their measurements, sample sizes, or conclusions.",
    ]
    for hit in literature[:5]:
        year = f" ({hit.year})" if hit.year else ""
        lines.append(f"- {hit.title}{year}".strip())
    return "\n".join(lines)


def _clean_markdown(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:markdown|md)?\s*", "", cleaned, count=1, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip() + "\n"


def facts_for_section(section: SectionId, catalog: list[dict[str, str]]) -> str:
    """Facts that belong to one chapter. The same measurement is not given to every chapter."""

    if section == SectionId.methods:
        body = _fact_groups(catalog, {"parameter"})
        versions = _fact_groups(
            [item for item in catalog if _is_version_table(item)],
            {"table"},
        )
        if versions:
            body = f"{body}\n\n{versions}".strip()
        if not body:
            return (
                "No procedure, software, or parameter is named in this package. "
                "Say that in one sentence. Do not add ethics approval, eligibility, "
                "clinical assessment, exposure, or a statistical test."
            )
        return body + "\n\nWrite only these procedures. Do not copy measurements into the methods."
    if section == SectionId.results:
        return _results_facts(catalog)
    if section == SectionId.discussion:
        body = _fact_groups(catalog, {"conclusion"})
        if not body:
            return (
                "The package has no separate interpretation. "
                "Discuss only what the results chapter already wrote. "
                "Do not add a disease, a statistical test, or a new measurement."
            )
        return body
    if section == SectionId.back_matter:
        body = _fact_groups(
            [item for item in catalog if str(item.get("kind") or "") == "table" and not _is_version_table(item)],
            {"table"},
        )
        if not body:
            return (
                "This package lists no tables. Say that in one sentence. "
                "Do not write an abbreviation list or invent a repository."
            )
        return body + "\n\nName these tables. Do not restate the results and do not write an abbreviation list."
    if section == SectionId.abstract:
        return (
            "Use the results and discussion already written. "
            "One paragraph: what was done, at most three measurements already stated in the results, "
            "and one limit already stated in the discussion. Do not mention figures."
        )
    if section == SectionId.introduction:
        return (
            "Use the chapters already written. "
            "Open from the analysis described by the brief, state the question, "
            "then what the results established. Do not repeat the results paragraph and do not mention figures."
        )
    if section == SectionId.conclusion:
        return (
            "Use the results and discussion already written. "
            "One short paragraph. Do not mention figures and do not add a measurement."
        )
    return _fact_sheet(catalog)


def _is_version_table(item: dict[str, str]) -> bool:
    """Software and database version tables belong in methods, not in results."""

    blob = f"{item.get('relative_path', '')} {item.get('label', '')}".lower().replace("_", " ")
    return "provenance" in blob or "software version" in blob or "database version" in blob


_BLOCK_RANK = {
    "qc": 0,
    "quality": 0,
    "quality_control": 0,
    "host": 1,
    "host_depletion": 1,
    "taxonomy": 2,
    "taxonomic": 2,
    "diversity": 3,
    "functional": 4,
    "function": 4,
    "functions": 4,
    "pathway": 5,
    "pathways": 5,
    "reports": 6,
    "interpretation": 7,
    "tables": 8,
}


def _results_facts(catalog: list[dict[str, str]]) -> str:
    """One paragraph per package block, with one primary display per measurement."""

    mention = " ".join(
        str(item.get("excerpt") or "")
        for item in catalog
        if str(item.get("kind") or "") in {"summary", "conclusion"}
    )
    grouped: dict[str, list[dict[str, str]]] = {}
    order: list[str] = []
    for item in catalog:
        kind = str(item.get("kind") or "")
        if kind == "table" and _is_version_table(item):
            continue
        if kind not in {"summary", "conclusion", "table", "figure"}:
            continue
        block = _block_name(str(item.get("relative_path") or ""))
        if block not in grouped:
            grouped[block] = []
            order.append(block)
        grouped[block].append(item)
    if not grouped:
        return "This package states no measurements or displays. Say that in one sentence."

    ranked = sorted(order, key=lambda name: (_BLOCK_RANK.get(name.lower(), 50), order.index(name)))
    parts: list[str] = []
    for block in ranked:
        items = grouped[block]
        figures = _primary_figures(
            [item for item in items if str(item.get("kind") or "") == "figure"],
            mention,
        )
        lines: list[str] = []
        for item in items:
            kind = str(item.get("kind") or "")
            if kind in {"summary", "conclusion"}:
                excerpt = " ".join(str(item.get("excerpt") or "").split())
                if excerpt:
                    lines.append(f"- {excerpt}")
            elif kind == "table":
                name = " ".join(str(item.get("label") or "").split()) or "untitled"
                excerpt = " ".join(str(item.get("excerpt") or "").split())
                detail = excerpt if excerpt and excerpt != name else "shown in the package"
                lines.append(f"- [[{name}]]: {detail}")
        for item in figures:
            name = " ".join(str(item.get("label") or "").split()) or "untitled"
            lines.append(f"- primary display [[{name}]]")
        if lines:
            parts.append(f"Block {block}:\n" + "\n".join(lines))
    if not parts:
        return "This package states no measurements or displays. Say that in one sentence."
    parts.append(
        "Write one paragraph per block, with a blank line between paragraphs. "
        "State what the block shows, using only the numbers listed in that block, "
        "then cite each primary display in that block as [[short name]]. "
        "A block may list more than one primary display when they are different measurements; "
        "cite each once in that same paragraph. "
        "If the research question names a block that is listed, open with that block. "
        "Do not cite a display that is not listed. "
        "Do not write one paragraph per display. "
        "Do not write that a result was visualized, plotted, displayed, or contained in a file. "
        "Do not mention how many files, rows, or records the package contains. "
        "Do not cite software versions or database versions. "
        "Do not explain the procedure. "
        "You may state the pattern those numbers show. "
        "Do not add a comparison, test, disease, or cause that these facts do not state."
    )
    return "\n\n".join(parts)


def _block_name(relative_path: str) -> str:
    parts = [
        part
        for part in relative_path.replace("\\", "/").split("/")
        if part and part.lower() not in {"figures", "figure"}
    ]
    if not parts:
        return "package"
    if len(parts) == 1:
        return "package" if "." in parts[0] else parts[0]
    return parts[0]


def _item_words(item: dict[str, str]) -> set[str]:
    blob = f"{item.get('label', '')} {item.get('relative_path', '')}".lower().replace("_", " ")
    return set(re.findall(r"[a-z0-9]+", blob))


def _primary_figures(figures: list[dict[str, str]], mention: str) -> list[dict[str, str]]:
    """One non-secondary display per measurement family. A heatmap stays out unless named."""

    mention_words = set(re.findall(r"[a-z0-9]+", mention.lower()))
    chosen: list[dict[str, str]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for item in figures:
        words = _item_words(item)
        families = [family for family in _MEASUREMENT_FAMILIES if words & family]
        if not families:
            continue
        secondary = bool(words & _SECONDARY_VIEWS)
        if secondary and not (words & _SECONDARY_VIEWS & mention_words):
            continue
        key = (_block_name(str(item.get("relative_path") or "")), tuple(sorted(families[0])))
        if key in seen:
            continue
        seen.add(key)
        chosen.append(item)
    return chosen


def _reader_caption(label: str) -> str:
    text = re.sub(r"top\s*(\d+)", r"top \1", label.replace("_", " "), flags=re.I)
    text = " ".join(text.split())
    if not text:
        return label
    return text[0].upper() + text[1:]


def prior_prose_for(section: SectionId, written: dict[str, str]) -> str:
    """Earlier chapters this section is allowed to see, without their figures."""

    chunks: list[str] = []
    for earlier in _PRIOR_CHAPTERS.get(section, ()):
        text = (written.get(earlier.value) or "").strip()
        if not text:
            continue
        prose, _embeds = _split_trailing_embeds(text)
        prose = prose.strip()
        if len(prose) > _PRIOR_LIMIT:
            prose = prose[: _PRIOR_LIMIT - 1] + "…"
        if prose:
            chunks.append(f"{earlier.value}:\n{prose}")
    return "\n\n".join(chunks)


def _fact_sheet(catalog: list[dict[str, str]]) -> str:
    """Study-agnostic sheet: whatever this package states, and nothing else."""

    blocks = [_fact_groups(catalog, {"summary", "conclusion", "parameter", "figure", "table"})]
    blocks.append(
        "Do not add a disease, cohort, hypothesis, ethics approval, statistical test, "
        "method, or result that is not written above. If a usual paper section has nothing "
        "listed for it, leave that topic out."
    )
    return "\n\n".join(block for block in blocks if block)


def _fact_groups(catalog: list[dict[str, str]], kinds: set[str]) -> str:
    groups = [
        ("statements", "Package statements", {"summary", "conclusion"}),
        ("procedures", "Procedures, software, and parameters present in the package", {"parameter"}),
        ("figures", "Figures", {"figure"}),
        ("tables", "Tables", {"table"}),
    ]
    blocks: list[str] = []
    for key, title, group_kinds in groups:
        if not group_kinds & kinds:
            continue
        lines: list[str] = []
        for item in catalog:
            if str(item.get("kind") or "") not in group_kinds or str(item.get("kind") or "") not in kinds:
                continue
            name = " ".join(str(item.get("label") or "").split()) or "untitled"
            excerpt = " ".join(str(item.get("excerpt") or "").split())
            if key in {"figures", "tables"}:
                detail = excerpt if excerpt and excerpt != name else "shown in the package"
                lines.append(f"- [[{name}]]: {detail}")
            elif excerpt:
                lines.append(f"- {excerpt}")
        if lines:
            blocks.append(title + ":\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def _drop_repeated_lines(text: str) -> str:
    """Stop a stalled completion from pasting the same sentence for the rest of the section."""

    kept: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        key = " ".join(line.split())
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        kept.append(line)
    collapsed = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return f"{collapsed}\n" if collapsed else ""


def _first_pos(text: str, item: dict[str, str]) -> int | None:
    """Where this display is first cited, by short name or catalog id."""

    haystack = text or ""
    positions: list[int] = []
    name = " ".join(str(item.get("label") or "").split())
    if name:
        marker = f"[[{name}]]"
        idx = haystack.lower().find(marker.lower())
        if idx >= 0:
            positions.append(idx)
    item_id = str(item.get("item_id") or "")
    if item_id:
        match = re.search(rf"`{re.escape(item_id)}`|(?<![\w/]){re.escape(item_id)}(?!\w)", haystack)
        if match:
            positions.append(match.start())
    return min(positions) if positions else None


def _appearance_labels(text: str, catalog: list[dict[str, str]]) -> dict[str, str]:
    """Number figures and tables in the order a reader meets them."""

    hits = [(pos, item) for item in catalog if (pos := _first_pos(text, item)) is not None]
    hits.sort(key=lambda pair: pair[0])
    labels: dict[str, str] = {}
    figure_number = 0
    table_number = 0
    for _, item in hits:
        item_id = str(item.get("item_id") or "")
        kind = str(item.get("kind") or "")
        if not item_id:
            continue
        if kind == "figure":
            figure_number += 1
            labels[item_id] = f"Figure {figure_number}"
        elif kind == "table":
            table_number += 1
            labels[item_id] = f"Table {table_number}"
    return labels


def _apply_labels(text: str, catalog: list[dict[str, str]], labels: dict[str, str]) -> str:
    ordered = sorted(catalog, key=lambda item: len(str(item.get("label") or "")), reverse=True)
    for item in ordered:
        item_id = str(item.get("item_id") or "")
        replacement = labels.get(item_id, "")
        if not replacement:
            continue
        name = " ".join(str(item.get("label") or "").split())
        if name:
            text = re.sub(re.escape(f"[[{name}]]"), replacement, text, flags=re.I)
        if item_id:
            text = re.sub(
                rf"`{re.escape(item_id)}`|(?<![\w/]){re.escape(item_id)}(?!\w)",
                replacement,
                text,
            )
    return text


def translate_section_text(client: LLMClient, text: str, language: str) -> str:
    """Chinese follows the finished English section and cannot add facts."""

    return _translate_if_needed(client, text, language)


def _translate_if_needed(client: LLMClient, text: str, language: str) -> str:
    """Chinese follows the English section and cannot add facts."""

    if "zh" not in (language or "").lower():
        return text
    if len(_CJK.findall(text or "")) >= 2:
        return text
    prose, embeds = _split_trailing_embeds(text)
    if len(prose.strip()) < 40:
        return text
    translated = client.generate(
        "Translate the following paper section into Chinese. "
        "Keep every number, Figure N, and Table N unchanged. "
        "Do not add measurements, methods, or claims. "
        "Output Chinese prose only.\n\n"
        f"{prose.strip()}",
        system="You translate scientific prose into Chinese without adding facts.",
    )
    chinese = _clean_markdown(translated).strip()
    if not chinese:
        return text
    combined = f"{prose.rstrip()}\n\n中文\n\n{chinese}"
    if embeds.strip():
        combined = f"{combined}\n\n{embeds.strip()}"
    return combined if combined.endswith("\n") else combined + "\n"


def _split_trailing_embeds(text: str) -> tuple[str, str]:
    lines = (text or "").splitlines()
    cut = len(lines)
    while cut > 0 and (not lines[cut - 1].strip() or _EMBED_LINE.match(lines[cut - 1].strip())):
        cut -= 1
    return "\n".join(lines[:cut]), "\n".join(lines[cut:]).strip()


def _splice_displays(
    text: str,
    cited: list[dict[str, str]],
    labels: dict[str, str],
) -> tuple[str, list[FigureRef]]:
    refs: list[FigureRef] = []
    blocks: list[str] = []
    for item in cited:
        kind = str(item.get("kind") or "")
        if kind not in {"figure", "table"}:
            continue
        path = str(item.get("path") or "")
        item_id = str(item.get("item_id") or "")
        label = labels.get(item_id, "")
        if not path or not label or path in text:
            continue
        number = int(label.rsplit(" ", 1)[-1])
        ref = FigureRef(
            figure_id=item_id,
            path=path,
            caption=_reader_caption(str(item.get("label") or label)),
            kind="table" if kind == "table" else "figure",
        )
        refs.append(ref)
        blocks.append(render_figure_embed(ref, display_index=number, include_placeholder=False))
    if not blocks:
        return text, refs
    return text.rstrip() + "\n\n" + "\n".join(blocks), refs


def _append_uncited_figures(
    source: str,
    text: str,
    catalog: list[dict[str, str]],
    labels: dict[str, str],
    refs: list[FigureRef],
) -> tuple[str, list[FigureRef]]:
    """Add a plot the findings discuss, and leave out extra views of the same measurement."""

    next_number = 1
    for value in labels.values():
        parts = str(value).rsplit(" ", 1)
        if len(parts) == 2 and parts[0] == "Figure" and parts[1].isdigit():
            next_number = max(next_number, int(parts[1]) + 1)
    blocks: list[str] = []
    for item in catalog:
        if str(item.get("kind") or "") != "figure":
            continue
        item_id = str(item.get("item_id") or "")
        path = str(item.get("path") or "")
        if not item_id or not path or item_id in labels or path in text:
            continue
        if not _finding_uses_display(source, item):
            continue
        label = f"Figure {next_number}"
        next_number += 1
        labels[item_id] = label
        ref = FigureRef(
            figure_id=item_id,
            path=path,
            caption=_reader_caption(str(item.get("label") or label)),
            kind="figure",
        )
        refs.append(ref)
        blocks.append(render_figure_embed(ref, display_index=next_number - 1, include_placeholder=False))
    if not blocks:
        return text, refs
    return text.rstrip() + "\n\n" + "\n".join(blocks), refs


_MEASUREMENT_FAMILIES = (
    frozenset({"shannon", "simpson", "pielou", "evenness", "alpha", "diversity"}),
    frozenset({"species", "taxon", "taxa", "taxonomy"}),
    frozenset({"gene", "family", "families"}),
    frozenset({"enzyme", "ec"}),
    frozenset({"kegg", "ko", "ortholog", "orthology"}),
    frozenset({"pathway", "pathways"}),
    frozenset({"read", "reads", "q30"}),
)
_SECONDARY_VIEWS = frozenset({"heatmap", "pcoa", "pca", "nmds", "ordination", "bray", "curtis"})


def _unsupported_secondary(prose: str, item: dict[str, str]) -> bool:
    """A heatmap or ordination the findings never mention."""

    blob = f"{item.get('label', '')} {item.get('relative_path', '')}".lower().replace("_", " ")
    words = set(re.findall(r"[a-z0-9]+", blob))
    if not words & _SECONDARY_VIEWS:
        return False
    prose_words = set(re.findall(r"[a-z0-9]+", (prose or "").lower()))
    return not bool(prose_words & _SECONDARY_VIEWS)


def _finding_uses_display(prose: str, item: dict[str, str]) -> bool:
    """A display belongs in the paper when the findings name its measurement.

    A heatmap or ordination is a second view. It stays out unless the findings
    discuss that view. The decision uses the prose and the file name, not an
    instruction to include every plot.
    """

    if _is_version_table(item):
        return False
    blob = f"{item.get('label', '')} {item.get('relative_path', '')}".lower().replace("_", " ")
    words = set(re.findall(r"[a-z0-9]+", blob))
    prose_words = set(re.findall(r"[a-z0-9]+", (prose or "").lower()))
    if words & _SECONDARY_VIEWS and not (prose_words & _SECONDARY_VIEWS):
        return False
    item_families = [family for family in _MEASUREMENT_FAMILIES if words & family]
    if not item_families:
        return False
    return any(family & prose_words for family in item_families)


def remove_embedded_displays(markdown: str) -> str:
    """Drop pasted images and caption lines. Prose citations can stay."""

    kept = [line for line in (markdown or "").splitlines() if not _EMBED_LINE.match(line.strip())]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return f"{text}\n" if text else ""


def _numbers_not_in_catalog(text: str, catalog: list[dict[str, str]]) -> list[str]:
    blob = "\n".join(
        f"{item.get('relative_path', '')} {item.get('excerpt', '')} {item.get('label', '')}"
        for item in catalog
    )
    missing: list[str] = []
    seen: set[str] = set()
    for match in _NUMBER.finditer(text):
        token = match.group(0)
        if token in seen:
            continue
        seen.add(token)
        try:
            if float(token) < 20 and "." not in token:
                continue
        except ValueError:
            continue
        if token not in blob:
            missing.append(token)
    return missing
