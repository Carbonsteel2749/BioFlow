#!/usr/bin/env python3
"""Private helpers for visualization nodes; deliberately independent of common.py."""
from __future__ import annotations
import json
import hashlib
from functools import wraps
import shutil
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

class TableError(ValueError):
    pass

PLOT_MODULE_VERSION = "scientific-plots-v4.0"
FIGURE_SKILL: dict = {}
RENDER_OPTIONS: dict = {}
OKABE_ITO = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#333333"]


def styled_plots(function):
    """Invoke the pinned installed Skill helper within a process-local style context."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        from skill_helpers.scipilot_setup_style import setup_style
        helper = Path(__file__).parent / 'skill_helpers/scipilot_setup_style.py'
        with plt.rc_context():
            # Legacy QC renderers explicitly call tight_layout after colorbars.
            # Preserve that engine; new adapters opt into constrained layout.
            applied = setup_style(journal='general', lang='en', use_sciplots=False, constrained_layout=False)
            from matplotlib import font_manager
            available = {font.name for font in font_manager.fontManager.ttflist}
            fonts = ['DejaVu Sans'] + sorted(name for name in available if 'CJK' in name)
            plt.rcParams.update({'font.sans-serif': fonts, 'legend.frameon': False,
                                 'axes.titleweight': 'bold', 'axes.labelcolor': '#334155',
                                 'text.color': '#1e293b', 'figure.facecolor': 'white'})
            FIGURE_SKILL.clear()
            FIGURE_SKILL.update({
                'skill': 'scipilot-figure-skill', 'skill_version': '2.1',
                'source': 'https://github.com/Haojae/scipilot-figure-skill',
                'source_install_commit': '0193f8415a2354a7014135ffd2d5bb554975a4d2',
                'instructions_sha256': '79e7438109306d40638728ba8adaadf7312d486215189b320e157ab2e382adcc',
                'helper_invoked': 'setup_style.setup_style',
                'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest(),
                'applied_style': applied,
                'font_fallbacks': fonts,
                'visual_model_review': 'not_performed',
                'review_limitation': 'configured Qwen3:14b has no vision capability; programmatic checks are not visual certification',
            })
            return function(*args, **kwargs)
    return wrapped

def read_standard_table(path: Path, required: set[str], keys: list[str], abundance: str = "abundance") -> pd.DataFrame:
    if not path.is_file():
        raise TableError(f"input TSV does not exist: {path}")
    try:
        frame = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    except Exception as exc:
        raise TableError(f"cannot read TSV: {exc}") from exc
    missing = sorted(required - set(frame.columns))
    if missing:
        raise TableError(f"missing required columns: {', '.join(missing)}")
    if frame.empty:
        return frame
    if frame.duplicated(keys).any():
        raise TableError(f"duplicate primary key rows for: {', '.join(keys)}")
    values = pd.to_numeric(frame[abundance], errors="coerce")
    if values.isna().any() or (values < 0).any() or ~np.isfinite(values).all():
        raise TableError(f"{abundance} must contain finite non-negative numbers")
    frame[abundance] = values.astype(float)
    return frame

def write_provenance(path: Path, payload: dict) -> None:
    """Write provenance plus local redraw sidecars from normalized inputs only."""
    payload.setdefault("plot_module_version", PLOT_MODULE_VERSION)
    if RENDER_OPTIONS:
        payload.setdefault('render_options', dict(RENDER_OPTIONS))
    payload.setdefault('guidance_sources', [{'title': 'Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents',
        'url': 'https://arxiv.org/abs/2609.00065', 'doi': '10.48550/arXiv.2609.00065',
        'role': 'scientific-visualization design guidance; not statistical evidence'}])
    if FIGURE_SKILL:
        payload.setdefault('figure_skill', dict(FIGURE_SKILL))
    payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    input_paths = payload.get("inputs", {})
    if "input" in payload:
        analysis_key = str(payload.get("analysis", "taxonomy")).removesuffix("_plots")
        input_paths = {analysis_key: payload["input"], "taxonomy": payload["input"], **input_paths}
    if not isinstance(input_paths, dict):
        return
    def generated(value):
        if isinstance(value, dict):
            if value.get("status") == "generated" and isinstance(value.get("output"), str):
                yield value["output"]
            for child in value.values():
                yield from generated(child)
    for name in sorted(set(generated(payload.get("plots", {})))):
        if not name.endswith(".png"):
            continue
        stem = Path(name).stem
        if stem.startswith("taxonomy_"):
            key = "taxonomy"
        elif stem.startswith("sample_mag_"):
            key = "sample_mag_abundance"
        elif stem.startswith("mag_"):
            key = "mag_annotation"
        else:
            key = next((candidate for candidate in input_paths if candidate in stem), None)
        source = Path(str(input_paths.get(key, "")))
        if source.is_file():
            shutil.copyfile(source, path.parent / (stem + ".source.tsv"))
        parameters = {key: value for key, value in payload.items() if key not in {"plots", "inputs"}}
        (path.parent / (stem + ".parameters.json")).write_text(json.dumps(parameters, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        caption_path = path.parent / (stem + '.caption.md')
        if not caption_path.exists():
            caption_path.write_text("# " + stem + "\n\nGenerated locally from the normalized source table. Units, transformations, sample scope and applicability are recorded in " + path.name + " and " + stem + ".parameters.json. Missing values are not silently changed to zero.\n", encoding="utf-8")

def save_figure(figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if RENDER_OPTIONS:
        width, height = figure.get_size_inches()
        figure.set_size_inches(RENDER_OPTIONS.get('width_inches') or width,
                               RENDER_OPTIONS.get('height_inches') or height)
        from matplotlib.text import Text
        scale = RENDER_OPTIONS.get('font_size', 10) / 10
        for text in figure.findobj(Text):
            text.set_fontsize(text.get_fontsize() * scale)
        palette = RENDER_OPTIONS.get('palette', 'default')
        if palette != 'default':
            for axes in figure.axes:
                for artist in [*axes.images, *axes.collections]:
                    if hasattr(artist, 'get_array') and artist.get_array() is not None:
                        artist.set_cmap(palette)
    if figure.get_layout_engine() is None:
        # Legacy callers create colorbars before applying a layout engine.
        # Switching those grids to constrained layout can create zero ratios.
        figure.tight_layout()
    figure.canvas.draw()
    width, height = map(float, figure.get_size_inches())
    renderer = figure.canvas.get_renderer()
    bounds = figure.bbox
    outside = []
    hidden_ticks = set()
    for axes in figure.axes:
        for axis in (axes.xaxis, axes.yaxis):
            lower, upper = sorted(axis.get_view_interval())
            for tick in axis.get_major_ticks() + axis.get_minor_ticks():
                if not lower <= tick.get_loc() <= upper:
                    hidden_ticks.update((id(tick.label1), id(tick.label2)))
    from matplotlib.text import Text
    for item in figure.findobj(Text):
        if id(item) in hidden_ticks or not item.get_visible() or not item.get_text().strip():
            continue
        box = item.get_window_extent(renderer)
        if box.x0 < bounds.x0 - 2 or box.y0 < bounds.y0 - 2 or box.x1 > bounds.x1 + 2 or box.y1 > bounds.y1 + 2:
            outside.append(item.get_text())
    # Tight cropping silently changes the requested publication width.
    with plt.rc_context({'savefig.bbox': None}):
        for suffix in (".png", ".svg", ".pdf"):
            figure.savefig(path.with_suffix(suffix), dpi=300, facecolor='white', bbox_inches=None)
    from PIL import Image
    with Image.open(path.with_suffix('.png')) as exported:
        pixels = list(exported.size)
    path.with_suffix('.qa.json').write_text(json.dumps({
        'physical_size_inches': [width, height], 'dpi': 300, 'png_pixels': pixels,
        'formats': ['png', 'svg', 'pdf'], 'matplotlib_version': matplotlib.__version__,
        'text_outside_canvas': outside, 'visual_review': 'not_performed',
        'render_options': dict(RENDER_OPTIONS),
        'journal_compliance': 'not_certified',
        'limitations': 'Geometry checks do not detect every overlap or establish statistical validity.'
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    plt.close(figure)

def skip(reason: str) -> dict:
    return {"status": "skipped", "reason": reason, "outputs": []}

def top_with_other(frame: pd.DataFrame, feature: str, value: str, top_n: int, other_label: str = "Other (non-Top-N)") -> tuple[pd.DataFrame, list[str]]:
    ranked = frame.groupby(feature, as_index=True)[value].mean().sort_values(ascending=False)
    top = ranked.head(top_n).index.tolist()
    output = frame.copy()
    output[feature] = np.where(output[feature].isin(top), output[feature], other_label)
    return output.groupby([column for column in output.columns if column not in (value,)], as_index=False)[value].sum(), top

def abundance_matrix(frame: pd.DataFrame, sample: str, feature: str, value: str) -> pd.DataFrame:
    return frame.pivot(index=sample, columns=feature, values=value).fillna(0.0)
