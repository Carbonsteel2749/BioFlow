#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial.distance import pdist, squareform

from plot_utils import OKABE_ITO, TableError, read_standard_table, save_figure, skip, styled_plots, write_provenance
from figure_methods import build_figure_plan
from figure_panels import taxonomy_panels
import matplotlib.pyplot as plt

REQUIRED = {'sample_id', 'taxid', 'taxonomy', 'taxonomy_rank', 'taxonomy_system',
            'read_count_or_estimated_reads', 'abundance', 'abundance_unit',
            'classification_source', 'database_profile', 'database_release'}


def sample_groups(path, samples, group_column):
    if path is None:
        return None
    try:
        metadata = pd.read_csv(path, sep=None, engine='python', dtype=str, keep_default_na=False)
    except Exception as exc:
        raise TableError(f'cannot read sample metadata: {exc}') from exc
    if not {'sample_id', group_column}.issubset(metadata.columns):
        raise TableError(f'metadata requires sample_id and {group_column}')
    if metadata.sample_id.duplicated().any() or (metadata.sample_id == '').any():
        raise TableError('metadata sample_id must be unique and non-empty')
    groups = metadata.set_index('sample_id')[group_column].reindex(samples)
    if groups.isna().any() or groups.eq('').any():
        raise TableError('metadata has missing group assignments for analysis samples')
    return groups


def heatmap(matrix, title, path):
    values = matrix.T
    labels = [textwrap.fill(str(x), 42) for x in values.index]
    height = max(4, sum(label.count('\n') + 1 for label in labels) * .30 + 1.8)
    figure, axis = plt.subplots(figsize=(max(7, len(matrix) * .65 + 4), height), layout='constrained')
    image = axis.imshow(values.to_numpy(), aspect='auto', cmap='cividis', vmin=0, interpolation='nearest')
    axis.set(xticks=range(len(values.columns)), xticklabels=values.columns,
             yticks=range(len(values)), yticklabels=labels,
             title=title, xlabel='Sample')
    axis.tick_params(axis='x', labelrotation=30)
    axis.tick_params(axis='y', labelsize=9)
    figure.colorbar(image, ax=axis, label='Bracken-reported relative abundance (fraction)')
    save_figure(figure, path)


def single_sample_diversity_summary(sample, values, excluded_count):
    from matplotlib.patches import FancyBboxPatch
    from plot_utils import RENDER_OPTIONS

    width = RENDER_OPTIONS.get('width_inches') or 9
    height = RENDER_OPTIONS.get('height_inches') or 4.4
    font_scale = RENDER_OPTIONS.get('font_size', 10) / 10
    figure = plt.figure(figsize=(width, height), layout='constrained')
    grid = figure.add_gridspec(4, 2, height_ratios=[.55, 1, 1, .45])
    labels = []

    def label(axis, x, y, text, fontsize, box_width, box_height, **kwargs):
        artist = axis.text(x, y, text, fontsize=fontsize, va='center',
                           transform=axis.transAxes, in_layout=False, **kwargs)
        labels.append((artist, text, fontsize, box_width, box_height))

    header = figure.add_subplot(grid[0, :])
    header.set_axis_off()
    label(header, .01, .78, 'Alpha diversity · single-sample summary', 13, .98, .42,
          fontweight='bold')
    identity = str(sample)
    if 'group' in values:
        identity += f" · Group: {values['group']}"
    label(header, .01, .27, identity, 9, .98, .45)
    cards = [
        ('shannon', 'Shannon diversity', 'Natural logarithm; within-sample species proportions.'),
        ('simpson_1_minus_d', 'Simpson diversity (1-D)', '1 − sum(p²); within-sample species proportions.'),
        ('observed_species', 'Observed species', 'Detected with current database/filtering;\nnot total ecological richness.'),
        ('pielou_evenness', 'Pielou evenness', 'Shannon / ln(observed species).\nNA with fewer than two detected species.'),
    ]
    for position, (metric, title, note) in enumerate(cards):
        axis = figure.add_subplot(grid[1 + position // 2, position % 2])
        axis.set_axis_off()
        axis.add_patch(FancyBboxPatch((.01, .02), .98, .96, transform=axis.transAxes,
                      boxstyle='round,pad=0.008,rounding_size=0.035',
                      facecolor='#f8fafc', edgecolor='#dbe3ec', linewidth=.8))
        value = values[metric]
        display = ('NA' if pd.isna(value) else str(int(value)) if metric == 'observed_species'
                   else f'{0.0 if value == 0 else value:.3f}')
        label(axis, .05, .80, title, 10, .90, .25, fontweight='bold')
        label(axis, .05, .48, display, 23, .90, .35, fontweight='bold', color='#0072B2')
        label(axis, .05, .16, note, 8, .90, .24, color='#475569')
    footer = figure.add_subplot(grid[3, :])
    footer.set_axis_off()
    note = 'n=1 quantifiable sample · descriptive only · no between-group inference.'
    if excluded_count:
        note += f'\nExcluded {excluded_count} zero-total sample(s); retained as NA in the evidence table.'
    label(footer, .01, .55, note, 9, .98, .80, color='#475569')
    # Fit against final physical dimensions, not an intermediate default canvas.
    # Each label has a disjoint region; long IDs remain complete in the TSV/caption.
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    fitted_sizes = []
    for artist, text, base_size, box_width, box_height in labels:
        region = artist.axes.get_window_extent(renderer)
        max_width, max_height = region.width * box_width * .95, region.height * box_height * .95
        size = base_size * font_scale
        for _ in range(60):
            columns = max(1, int(max_width / (size * figure.dpi / 72 * .56)))
            artist.set_text('\n'.join(textwrap.fill(line, columns) for line in text.splitlines()))
            artist.set_fontsize(size)
            bounds = artist.get_window_extent(renderer)
            if bounds.width <= max_width and bounds.height <= max_height:
                break
            size *= .94
        fitted_sizes.append(round(size, 3))
        # The shared exporter applies the requested base-font multiplier once.
        artist.set_fontsize(size / font_scale)
    return figure, {'text_fit': 'within disjoint label regions at requested export size',
                    'font_sizes_points': fitted_sizes, 'physical_size_inches': [width, height]}


def diversity(matrix, path, groups=None, style='auto'):
    totals = matrix.sum(axis=1)
    proportions = matrix.div(totals.replace(0, np.nan), axis=0)
    positive = proportions.where(proportions > 0)
    shannon = -(positive * np.log(positive)).sum(axis=1, min_count=1)
    observed = (matrix > 0).sum(axis=1)
    data = pd.DataFrame({'shannon': shannon,
                         'simpson_1_minus_d': 1 - (proportions ** 2).sum(axis=1, min_count=1),
                         'observed_species': observed,
                         'pielou_evenness': shannon / np.log(observed.where(observed > 1)),
                         'abundance_total': totals,
                         'status': np.where(totals > 0, 'valid', 'not_quantifiable')})
    if groups is not None:
        data['group'] = groups
    data.index.name = 'sample_id'
    data.to_csv(path.with_suffix('.data.tsv'), sep='\t', na_rep='NA')
    valid = data[data.status == 'valid']
    grouped = groups is not None
    names = list(dict.fromkeys(valid['group'])) if grouped else list(valid.index)
    counts = valid.groupby('group').size().to_dict() if grouped else {}
    plot_type = 'group_points' if grouped else 'sample_points'
    fallback = None
    if grouped and counts and min(counts.values()) >= 10:
        plot_type = 'group_violin_points' if style == 'violin' else 'group_box_points'
        if style == 'points':
            plot_type = 'group_points'
    elif style in ('violin', 'box'):
        fallback = 'insufficient within-group replicates (display policy: >=10); individual points retained'
    metrics = ['shannon', 'simpson_1_minus_d', 'observed_species', 'pielou_evenness']
    titles = ['Shannon diversity (natural log)', 'Simpson diversity (1-D)',
              'Observed positive species features', 'Pielou evenness']
    point_count = 0
    metric_counts = {}
    summary_layout = None
    if len(valid) == 1:
        plot_type = 'single_sample_summary'
        if style in ('violin', 'box'):
            fallback = 'single quantifiable sample; a distribution cannot be estimated from one observation'
        sample = valid.index[0]
        figure, summary_layout = single_sample_diversity_summary(sample, valid.iloc[0], int((totals <= 0).sum()))
        name = valid.iloc[0]['group'] if grouped else sample
        metric_counts = {metric: {name: int(pd.notna(valid.iloc[0][metric]))} for metric in metrics}
    else:
        figure, axes = plt.subplots(2, 2, figsize=(max(8, len(names) * .6 + 5), 7), layout='constrained')
        for panel, (axis, metric, title) in enumerate(zip(axes.flat, metrics, titles)):
            panel_counts = {}
            for position, name in enumerate(names):
                values = (valid.loc[valid['group'] == name, metric] if grouped else valid.loc[[name], metric]).dropna()
                point_count += len(values)
                panel_counts[name] = len(values)
                color = OKABE_ITO[position % len(OKABE_ITO)]
                if plot_type == 'group_box_points' and len(values) >= 10:
                    axis.boxplot([values.to_numpy()], positions=[position], widths=.5, patch_artist=True,
                                 showfliers=False, boxprops={'facecolor': color, 'alpha': .2}, medianprops={'color': '#334155'})
                elif plot_type == 'group_violin_points' and len(values) >= 10 and values.nunique() > 1:
                    bodies = axis.violinplot([values.to_numpy()], positions=[position], widths=.7, showextrema=False, showmedians=True)
                    for body in bodies['bodies']:
                        body.set_facecolor(color); body.set_alpha(.2)
                offsets = np.linspace(-.12, .12, len(values)) if len(values) > 1 else np.zeros(len(values))
                axis.scatter(position + offsets, values, color=color, s=55, edgecolor='white', linewidth=.7,
                             zorder=3, marker=['o', 's', '^', 'D'][position % 4])
            metric_counts[metric] = panel_counts
            labels = [f'{name}\n(n={panel_counts[name]})' if grouped else name for name in names]
            axis.set(xticks=range(len(names)), xticklabels=labels, ylabel=title, title=f'{chr(65+panel)}  {title}')
            axis.tick_params(axis='x', labelrotation=25)
            axis.set_xlim(-.5, max(.5, len(names) - .5))
            axis.set_ylim(bottom=0, top=1.05 if metric.startswith('simpson') or metric == 'pielou_evenness' else None)
            axis.grid(axis='y', alpha=.18); axis.set_axisbelow(True)
            if valid.empty:
                axis.text(.5, .5, 'No quantifiable samples', transform=axis.transAxes, ha='center')
        figure.suptitle('Alpha diversity · full species matrix', fontsize=13, fontweight='bold')
    save_figure(figure, path)
    display_caption = ('One quantifiable sample (n=1): compact numeric summary, descriptive only; no between-group inference. '
                       f"Sample: {valid.index[0]}. "
                       + (f"Group label: {valid.iloc[0]['group']}; not a replicated group. " if grouped else '')
                       if len(valid) == 1 else 'Each point is one sample. ')
    path.with_suffix('.caption.md').write_text(
        'Alpha diversity calculated on the full species matrix, normalized within each positive sample. '
        'Shannon uses natural logarithms; Simpson is 1−Σp²; observed features count positive reported species '
        'under the current classification database and input filtering, not total or extrapolated ecological richness; '
        'Pielou is Shannon/log(observed) and undefined (NA) with fewer than two observed features. '
        + display_caption + f'Excluded zero-total samples: {int((totals <= 0).sum())}; retained as NA in the evidence table. '
        'Group summaries use a display policy of at least 10 samples per group; '
        'box whiskers use 1.5 IQR, with all individual points retained; violin density is a display estimate. '
        'No hypothesis tests or significance annotations were performed.\n', encoding='utf-8')
    return {'matrix': 'full_species_matrix', 'normalization': 'positive sample divided by its species abundance total',
            'shannon_log_base': 'e', 'simpson_definition': '1 - sum(p_i^2)',
            'observed_species_definition': 'positive species features; not extrapolated richness',
            'alpha_plot_type': plot_type, 'group_sample_counts': counts, 'style_fallback_reason': fallback,
            'displayed_metrics': metrics, 'displayed_point_count': point_count,
            'quantifiable_sample_count': len(valid), 'input_sample_count': len(data),
            'summary_layout': summary_layout,
            'metric_sample_counts': metric_counts,
            'statistical_tests': 'not_performed', 'excluded_zero_total_samples': list(data.index[totals <= 0]),
            'data_file': path.with_suffix('.data.tsv').name}


def pcoa(matrix, path, groups=None):
    proportions = matrix.div(matrix.sum(axis=1), axis=0)
    distances = squareform(pdist(proportions.to_numpy(), metric='braycurtis'))
    pd.DataFrame(distances, index=matrix.index, columns=matrix.index).to_csv(
        path.parent / 'taxonomy_bray_curtis.distances.tsv', sep='\t', index_label='sample_id')
    n = len(matrix)
    centered = np.eye(n) - np.ones((n, n)) / n
    eigenvalues, eigenvectors = np.linalg.eigh(-.5 * centered @ (distances ** 2) @ centered)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues, eigenvectors = eigenvalues[order], eigenvectors[:, order]
    tolerance = max(1., np.max(np.abs(eigenvalues))) * 1e-10
    positive = np.where(eigenvalues > tolerance, eigenvalues, 0.)
    coordinates = eigenvectors[:, :2] * np.sqrt(positive[:2])
    total = positive.sum()
    dimensions = int((positive > 0).sum())
    data = pd.DataFrame(coordinates, index=matrix.index, columns=['PC1', 'PC2'])
    data.index.name = 'sample_id'
    if groups is not None:
        data['group'] = groups.reindex(matrix.index)
    data.to_csv(path.with_suffix('.data.tsv'), sep='\t')
    pd.DataFrame({'axis': np.arange(1, n+1), 'eigenvalue': eigenvalues,
                  'positive_variance_fraction': positive / total if total else positive}).to_csv(
        path.parent / 'taxonomy_bray_curtis.eigenvalues.tsv', sep='\t', index=False)
    figure, axis = plt.subplots(figsize=(7, 5), layout='constrained')
    names = list(dict.fromkeys(data['group'])) if groups is not None else list(data.index)
    for index, name in enumerate(names):
        subset = data[data['group'] == name] if groups is not None else data.loc[[name]]
        axis.scatter(subset.PC1, subset.PC2, s=85, color=OKABE_ITO[index % len(OKABE_ITO)],
                     marker=['o', 's', '^', 'D'][index % 4], edgecolor='white', linewidth=.8,
                     label=f'{name} (n={len(subset)})' if groups is not None else name, zorder=3)
    if n <= 15:
        for index, (label, row) in enumerate(data.iterrows()):
            axis.annotate(label, (row.PC1, row.PC2), xytext=(6, 9 if index % 2 else -15), textcoords='offset points', fontsize=8)
    labels = [f'PC{i+1} ({positive[i]/total*100:.1f}% of positive eigenvalues)' if total else f'PC{i+1}' for i in range(2)]
    axis.set(xlabel=labels[0], ylabel=labels[1], title='Bray-Curtis PCoA · full species matrix')
    axis.axhline(0, color='#cbd5e1', lw=.6); axis.axvline(0, color='#cbd5e1', lw=.6)
    axis.margins(.2)
    if dimensions < 2:
        axis.set_title(f'Bray-Curtis PCoA · only {dimensions} positive dimension(s)\nNo evidence for a two-dimensional cluster structure')
        axis.set_ylim(-.05, .05)
        axis.set_yticks([0])
        axis.set_ylabel('PC2 (no positive eigenvalue)')
    if groups is not None:
        axis.legend(loc='upper left', bbox_to_anchor=(1.02, 1))
    save_figure(figure, path)
    return {'distance': 'Bray-Curtis on within-sample normalized full species matrix',
            'positive_dimensions': dimensions, 'sample_count': n,
            'negative_eigenvalue_sum': float(np.abs(eigenvalues[eigenvalues < -tolerance]).sum()),
            'positive_eigenvalue_sum': float(total), 'negative_eigenvalue_policy': 'reported; omitted from real-valued coordinates',
            'confidence_ellipse': 'not_computed', 'statistical_tests': 'not_performed',
            'data_file': path.with_suffix('.data.tsv').name}


@styled_plots
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--outdir', type=Path, required=True)
    parser.add_argument('--top-n', type=int, default=20)
    parser.add_argument('--low-abundance-threshold', type=float, default=.01)
    parser.add_argument('--sample-metadata', type=Path)
    parser.add_argument('--metadata-sha256', help='Expected SHA-256 of the immutable sample metadata snapshot')
    parser.add_argument('--group-column', default='group')
    parser.add_argument('--alpha-style', choices=['auto', 'points', 'box', 'violin'], default='auto')
    parser.add_argument('--ai-advice', action='store_true', help='Optional bounded local-model chart advice; timeout falls back to rules')
    parser.add_argument('--extended-plots', choices=['true','false'], default='true')
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    provenance = {'analysis': 'taxonomy_plots', 'analysis_version': '3.0', 'input': str(args.input),
                  'top_n': args.top_n, 'low_abundance_threshold': args.low_abundance_threshold,
                  'abundance_unit': 'fraction_of_reads',
                  'abundance_denominator': 'source Bracken fraction; diversity normalized over named species, not all sequenced reads',
                  'metadata_sha256': None,
                  'plots': {}}
    try:
        if args.metadata_sha256 and not args.sample_metadata:
            raise TableError('metadata SHA-256 requires a sample metadata file')
        if args.sample_metadata:
            try:
                digest = hashlib.sha256(args.sample_metadata.read_bytes()).hexdigest()
            except OSError as exc:
                raise TableError(f'cannot read sample metadata: {exc}') from exc
            if args.metadata_sha256 and digest != args.metadata_sha256.lower():
                raise TableError('sample metadata SHA-256 does not match the immutable snapshot')
            provenance['metadata_sha256'] = digest
        if not 1 <= args.top_n <= 100 or not np.isfinite(args.low_abundance_threshold) or not 0 <= args.low_abundance_threshold <= 1:
            raise TableError('top-n must be 1..100 and low abundance threshold must be 0..1')
        frame = read_standard_table(args.input, REQUIRED, ['sample_id', 'taxonomy_system', 'taxid'])
        if not frame.empty:
            if set(frame.abundance_unit) != {'fraction_of_reads'}:
                raise TableError('taxonomy abundance_unit must be fraction_of_reads')
            if set(frame.taxonomy_rank) != {'species'}:
                raise TableError('taxonomy plot input must contain species-rank rows only')
            for column in ['taxonomy_system', 'database_profile', 'database_release']:
                if frame[column].nunique() != 1:
                    raise TableError(f'incompatible {column} values cannot be compared')
            if (frame.sample_id == '').any() or (frame.taxid == '').any():
                raise TableError('sample_id and species taxid must be non-empty')
            frame['feature_key'] = frame.taxonomy_system + ':' + frame.taxid
            if frame.groupby('feature_key').taxonomy.nunique().max() > 1:
                raise TableError('one taxonomy identifier has conflicting names')
        groups = sample_groups(args.sample_metadata, sorted(frame.sample_id.unique()), args.group_column)
    except TableError as exc:
        provenance.update(status='failed', reason=str(exc))
        write_provenance(args.outdir / 'taxonomy_plots.provenance.json', provenance)
        return 65
    if frame.empty:
        provenance.update(status='completed', plots={name: skip('input table has no species rows') for name in ('stacked', 'heatmap', 'diversity', 'pcoa')})
        write_provenance(args.outdir / 'taxonomy_plots.provenance.json', provenance)
        return 0
    full = frame.pivot(index='sample_id', columns='feature_key', values='abundance').fillna(0.)
    means = full.mean(axis=0)
    selected = means[means >= args.low_abundance_threshold].sort_values(ascending=False, kind='stable').head(args.top_n).index
    display = full.loc[:, selected].copy()
    omitted = full.loc[:, ~full.columns.isin(selected)].sum(axis=1)
    names = frame.drop_duplicates('feature_key').set_index('feature_key').taxonomy
    display.columns = [f'{names[key]} [{key}]' for key in display.columns]
    if (omitted > 0).any():
        display['Other (display only; not used for diversity)'] = omitted
    if display.shape[1] == 0:
        display['No classified species'] = 0.
    stacked = args.outdir / 'taxonomy_top20_stacked.png'
    figure, axis = plt.subplots(figsize=(max(8, len(display) * .7 + 4), 5.5), layout='constrained')
    display.plot(kind='bar', stacked=True, ax=axis, colormap='tab20', width=.65, edgecolor='white', linewidth=.3)
    axis.set(ylabel='Bracken-reported relative abundance (fraction)', xlabel='Sample', title=f'Species composition · Top-{args.top_n} plus Other')
    axis.tick_params(axis='x', labelrotation=25)
    axis.legend(loc='upper left', bbox_to_anchor=(1.02, 1), fontsize=7)
    save_figure(figure, stacked)
    heat = args.outdir / 'taxonomy_species_heatmap.png'
    heatmap(display, 'Species composition · display matrix', heat)
    alpha = args.outdir / 'taxonomy_alpha_diversity.png'
    provenance['diversity'] = diversity(full, alpha, groups, args.alpha_style)
    provenance['plots'] = {name: {'status': 'generated', 'output': path.name} for name, path in [('stacked', stacked), ('heatmap', heat), ('diversity', alpha)]}
    valid = full.loc[full.sum(axis=1) > 0]
    profile = {'sample_count':len(valid), 'feature_count':int((valid > 0).any(axis=0).sum()),
               'group_counts': groups.reindex(valid.index).value_counts().to_dict() if groups is not None else {}}
    provenance['figure_plan'] = build_figure_plan('taxonomy', profile, ai_advice=args.ai_advice and args.extended_plots == 'true')
    if args.extended_plots == 'true':
        provenance['plots'].update(taxonomy_panels(full, names, args.outdir, provenance['figure_plan'], args.top_n, groups))
    else:
        provenance['figure_plan']['extended_plots'] = 'disabled_by_user'
    if len(valid) < 2:
        provenance['plots']['pcoa'] = skip('Bray-Curtis PCoA requires at least two quantifiable samples')
    else:
        output = args.outdir / 'taxonomy_bray_curtis_pcoa.png'
        provenance['ordination'] = pcoa(valid, output, groups)
        provenance['plots']['pcoa'] = {'status': 'generated', 'output': output.name}
    provenance.update(status='completed', sample_count=len(full), full_species_count=len(full.columns), selected_taxa=list(selected),
                      database_profile=frame.database_profile.iloc[0], database_release=frame.database_release.iloc[0],
                      taxonomy_system=frame.taxonomy_system.iloc[0],
                      missingness_policy='absent species rows are zero detections; zero-total samples are not quantifiable',
                      sample_metadata=str(args.sample_metadata) if args.sample_metadata else None)
    write_provenance(args.outdir / 'taxonomy_plots.provenance.json', provenance)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
