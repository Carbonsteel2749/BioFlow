"""Deterministic descriptive figure recipes, adapted to BioFlow standard tables."""
from __future__ import annotations

import textwrap
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.cluster.hierarchy import linkage, dendrogram
from scipy.spatial.distance import pdist, squareform

from plot_utils import OKABE_ITO, save_figure, skip


def finish(figure, path, data, caption, **details):
    save_figure(figure, path)
    data.to_csv(path.with_suffix('.data.tsv'), sep='\t', index=False, na_rep='NA')
    path.with_suffix('.caption.md').write_text(caption + '\n\nNo hypothesis tests or significance annotations were performed. Journal-specific compliance is pending review.\n', encoding='utf-8')
    return {'status': 'generated', 'output': path.name, 'data_file': path.with_suffix('.data.tsv').name,
            'caption_file': path.with_suffix('.caption.md').name, **details}


def dot_panel(matrix, names, path, *, title, unit, groups=None, feature_column='feature_id', coverage=False):
    # Matrix values are actual fractions or coverage; area and color encode the same value.
    labels = [textwrap.fill(str(names.get(key, key)), 40) for key in matrix.columns]
    height = max(3.5, sum(label.count('\n') + 1 for label in labels) * .27 + 1.6)
    figure, axis = plt.subplots(figsize=(max(7.2, min(16, len(matrix) * .5 + 4)), height), layout='constrained')
    x, y = np.meshgrid(np.arange(len(matrix)), np.arange(len(matrix.columns)))
    values = matrix.to_numpy().T
    masked = np.isfinite(values) & (values > 0)
    image = axis.scatter(x[masked], y[masked], s=values[masked] * 380, c=values[masked],
                         cmap='viridis', vmin=0, vmax=1, edgecolor='#334155', linewidth=.25)
    # Unquantifiable samples are explicitly labeled N/A, never shown as a zero measurement.
    sample_labels = [f'{s}\n({groups.loc[s]})' if groups is not None else str(s) for s in matrix.index]
    sample_labels = [label + '\nN/A' if not matrix.loc[s].notna().any() else label for s, label in zip(matrix.index, sample_labels)]
    axis.set(xticks=range(len(matrix)), xticklabels=sample_labels, yticks=range(len(labels)),
             yticklabels=labels, xlabel='Sample', title=title, xlim=(-.5, len(matrix)-.5), ylim=(len(labels)-.5, -.5))
    axis.tick_params(axis='x', labelrotation=35); axis.tick_params(axis='y', labelsize=8)
    axis.grid(alpha=.15); axis.set_axisbelow(True)
    figure.colorbar(image, ax=axis, label=unit)
    data = matrix.rename_axis('sample_id').reset_index().melt(id_vars='sample_id', var_name=feature_column, value_name='value')
    if groups is not None:
        data['group'] = data.sample_id.map(groups)
    caption = f'{title}. Color and marker area encode {unit}; no marker means a reported zero for quantifiable samples. N/A means no positive named signal. '
    caption += 'Coverage is bounded 0–1 and is not additive abundance.' if coverage else 'Fractions use the sum of all positive named features in that sample, not just the displayed Top-N. This is relative composition, not absolute microbial load.'
    if feature_column == 'function_id':
        caption += ' HUMAnN community-total reads describe functional potential, not expression or activity and not attribution to a species or MAG.'
    return finish(figure, path, data, caption, unit=unit, marker_encoding='area proportional to value')


def clustered_panel(matrix, names, path, *, unit, groups=None, coverage=False):
    valid = matrix.dropna(how='all').fillna(0)
    valid = valid.loc[valid.sum(axis=1) > 0, (valid > 0).any(axis=0)]
    if len(valid) < 3 or len(valid.columns) < 2:
        return skip('clustering requires >=3 quantifiable samples and >=2 positive displayed features')
    values = valid.to_numpy()
    geometry = values if coverage else np.sqrt(values)
    sample_tree = linkage(geometry, method='average', metric='euclidean', optimal_ordering=True)
    feature_tree = linkage(geometry.T, method='average', metric='euclidean', optimal_ordering=True)
    height = max(4.8, sum(textwrap.fill(str(names.get(k,k)), 40).count('\n')+1 for k in valid.columns)*.27+2)
    figure = plt.figure(figsize=(max(8, min(16, len(valid)*.5+5)), height), layout='constrained')
    grid = figure.add_gridspec(2, 2, width_ratios=[1, 6], height_ratios=[1, 5])
    top = figure.add_subplot(grid[0,1]); left = figure.add_subplot(grid[1,0]); axis = figure.add_subplot(grid[1,1])
    cols = dendrogram(sample_tree, ax=top, no_labels=True, color_threshold=0, above_threshold_color='#64748b')['leaves']
    rows = dendrogram(feature_tree, ax=left, orientation='left', no_labels=True, color_threshold=0, above_threshold_color='#64748b')['leaves']
    top.set_axis_off(); left.set_axis_off(); left.invert_yaxis()
    ordered = valid.iloc[cols, rows]
    image = axis.imshow(ordered.T, aspect='auto', cmap='cividis', vmin=0, vmax=1, interpolation='nearest')
    sample_labels = [f'{s}\n({groups.loc[s]})' if groups is not None else s for s in ordered.index]
    axis.set(xticks=range(len(ordered)), xticklabels=sample_labels, yticks=range(len(ordered.columns)),
             yticklabels=[textwrap.fill(str(names.get(k,k)),40) for k in ordered.columns], title='Descriptive feature clustering')
    axis.tick_params(axis='x', labelrotation=35); axis.tick_params(axis='y', labelsize=8)
    figure.colorbar(image, ax=axis, label=unit)
    method = 'Euclidean on coverage' if coverage else 'Euclidean on square-root fractions (Hellinger coordinates; no renormalization after Top-N selection)'
    caption = f'Descriptive average-linkage clustering. Geometry: {method}. Heatmap displays {unit}, not z-scores. Both samples and displayed features are clustered. All-zero features and samples with no positive named signal are excluded. Dendrograms describe this table, not a phylogeny or evidence of significant groups.'
    return finish(figure, path, ordered.rename_axis('sample_id').reset_index(), caption,
                  distance=method, linkage='average', unit=unit, excluded_samples=list(matrix.index.difference(valid.index)))


def taxonomy_panels(full, names, outdir, plan, top_n, groups=None):
    valid = full.loc[full.sum(axis=1) > 0]
    fractions = valid.div(valid.sum(axis=1), axis=0)
    selected = fractions.mean().sort_values(ascending=False, kind='stable').head(top_n).index
    plots = {name: skip(reason) for name, reason in plan['skipped_recipes'].items()}
    chosen = set(plan['selected_recipes'])
    if 'rank_abundance' in chosen:
        figure, axis = plt.subplots(figsize=(7.2, 4.5), layout='constrained')
        records = []
        for i, (sample, row) in enumerate(fractions.iterrows()):
            ranked = row[row > 0].sort_values(ascending=False, kind='stable')
            rank = np.arange(1, len(ranked)+1)
            color = OKABE_ITO[i % len(OKABE_ITO)]
            axis.plot(rank, ranked.to_numpy(), color=color, lw=1.2, marker=['o','s','^','D'][i%4],
                      markersize=3, label=str(sample) if len(fractions) <= 12 else None, alpha=.8)
            records.extend({'sample_id':sample, 'rank':int(r), 'feature_id':feature, 'relative_abundance':float(value)}
                           for r,(feature,value) in zip(rank,ranked.items()))
        axis.set(xlabel='Species rank within sample', ylabel='Within-classified-species fraction (log10 scale)',
                 yscale='log', title='Rank–abundance · dominance and evenness')
        axis.grid(alpha=.15)
        if len(fractions) <= 12: axis.legend(fontsize=7, loc='upper left', bbox_to_anchor=(1.01,1))
        plots['rank_abundance'] = finish(figure,outdir/'taxonomy_rank_abundance.png',pd.DataFrame(records),
            'Rank–abundance curves use all positive named species, sorted separately within each sample. The y-axis is logarithmic; zero features are excluded only from the log curve. Fractions are normalized over classified species. Curves describe dominance and evenness, not absolute abundance or inferred richness.')
    if 'dominant_species_dot' in chosen:
        plots['dominant_species_dot'] = dot_panel(fractions.reindex(full.index).loc[:,selected], names,
            outdir/'taxonomy_dominant_species_dot.png', title=f'Dominant species · Top-{top_n}',
            unit='Within-classified-species fraction', groups=groups)
    if 'distance_heatmap' in chosen:
        distances = pd.DataFrame(squareform(pdist(fractions,metric='braycurtis')), index=valid.index, columns=valid.index)
        figure, axis = plt.subplots(figsize=(max(5.5,min(12,len(valid)*.4+3)),max(4.5,min(12,len(valid)*.4+2))), layout='constrained')
        image = axis.imshow(distances, cmap='cividis', vmin=0, vmax=1, interpolation='nearest')
        labels = [f'{s} ({groups.loc[s]})' if groups is not None else s for s in valid.index]
        axis.set(xticks=range(len(valid)),xticklabels=labels,yticks=range(len(valid)),yticklabels=labels,title='Full-species Bray–Curtis distances')
        axis.tick_params(axis='x',labelrotation=65,labelsize=8); axis.tick_params(axis='y',labelsize=8)
        figure.colorbar(image,ax=axis,label='Bray–Curtis dissimilarity (0–1)')
        plots['distance_heatmap'] = finish(figure,outdir/'taxonomy_bray_curtis_distance_heatmap.png',distances.rename_axis('sample_id').reset_index(),
            'Pairwise Bray–Curtis dissimilarity on within-sample normalized full species profiles, independent of Top-N display filtering. Zero-total samples are not quantifiable and excluded. This is a distance visualization, not a test of group separation.')
    if 'clustered_heatmap' in chosen:
        plots['clustered_heatmap'] = clustered_panel(fractions.loc[:,selected], names,
            outdir/'taxonomy_clustered_heatmap.png',unit='Within-classified-species fraction',groups=groups)
    if 'prevalence_abundance' in chosen:
        data = pd.DataFrame({'feature_id': fractions.columns, 'prevalence':(valid>0).mean().to_numpy(),
                             'mean_relative_abundance':fractions.mean().to_numpy(), 'quantifiable_sample_count':len(valid)})
        positive = data.mean_relative_abundance > 0
        figure, axis = plt.subplots(figsize=(7.2,4.8),layout='constrained')
        axis.scatter(data.loc[positive,'prevalence'],data.loc[positive,'mean_relative_abundance'], color=OKABE_ITO[0],alpha=.7,edgecolor='white',lw=.5)
        axis.set(xlabel='Observed prevalence (fraction of quantifiable samples)',ylabel='Mean within-classified-species fraction (log10 scale)',yscale='log',xlim=(-.03,1.03),title='Detection frequency and relative abundance')
        axis.grid(alpha=.15)
        plots['prevalence_abundance'] = finish(figure,outdir/'taxonomy_prevalence_abundance.png',data,
            'Prevalence is the fraction of quantifiable samples with positive reported species abundance. Mean includes reported zeros within the validated table. This describes detections under this database and classifier, not a validated core microbiome or disease association.')
    return plots


def functional_panels(frame, label, top_n, outdir, plan, samples, groups=None):
    if frame.empty:
        return {'feature_dot': skip('no positive named functions')}
    full = frame.pivot(index='sample_id',columns='function_id',values='abundance').reindex(samples).fillna(0)
    coverage = label == 'pathway_coverage'
    matrix = full if coverage else full.div(full.sum(axis=1).replace(0,np.nan),axis=0)
    selected = matrix.mean().sort_values(ascending=False,kind='stable').head(top_n).index
    names = frame.drop_duplicates('function_id').set_index('function_id').function_name.to_dict()
    names = {key: f'{key}: {value}' if value and value != key else key for key,value in names.items()}
    unit = 'Pathway coverage (0–1)' if coverage else 'Within-named-functions fraction'
    result = {}
    if 'feature_dot' in plan['selected_recipes']:
        result['feature_dot'] = dot_panel(matrix.loc[:,selected],names,outdir/f'functional_{label}_feature_dot.png',
            title=f'{label} · named features',unit=unit,groups=groups,feature_column='function_id',coverage=coverage)
    else:
        result['feature_dot'] = skip(plan['skipped_recipes'].get('feature_dot','not selected by advisory plan'))
    if 'functional_clustered_heatmap' in plan['selected_recipes']:
        result['clustered_heatmap'] = clustered_panel(matrix.loc[:,selected],names,outdir/f'functional_{label}_clustered_heatmap.png',unit=unit,groups=groups,coverage=coverage)
    else:
        result['clustered_heatmap'] = skip(plan['skipped_recipes'].get('functional_clustered_heatmap','not selected by advisory plan'))
    return result
