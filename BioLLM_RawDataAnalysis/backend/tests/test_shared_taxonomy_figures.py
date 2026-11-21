"""Contract tests from taxonomy source table to figures and execution evidence."""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys

import pandas as pd
import pytest


ENGINE = Path(__file__).resolve().parents[2]
VISUALIZATION = ENGINE / 'workflow' / 'bin' / 'visualization'
HEADER = (
    'sample_id\ttaxid\ttaxonomy\ttaxonomy_rank\ttaxonomy_system\t'
    'read_count_or_estimated_reads\tabundance\tabundance_unit\t'
    'classification_source\tdatabase_profile\tdatabase_release\n'
)


def input_table(path):
    # Library normalizations are distinguishable: S1 has 0.5 reported total
    # and 0.3 in the dominant taxon; normalization to 100% would change 0.3.
    rows = [('S1', '1', 'Taxon A', .3), ('S1', '2', 'Taxon B', .1),
            ('S1', '3', 'Taxon C', .1), ('S2', '1', 'Taxon A', .4),
            ('S0', '1', 'Taxon A', 0)]
    path.write_text(HEADER + ''.join(
        f'{sample}\t{taxid}\t{name}\tspecies\tNCBI\t0\t{value}\tfraction_of_reads\tKraken2+Bracken\tfixture\tv1\n'
        for sample, taxid, name, value in rows), encoding='utf-8')
    return path


def run_taxonomy(source, output, *options, script=VISUALIZATION / 'taxonomy_plots.py'):
    return subprocess.run(
        [sys.executable, str(script), '--input', str(source), '--outdir', str(output),
         '--top-n', '1', '--extended-plots', 'false', *options],
        cwd=output.parent, capture_output=True, text=True, encoding='utf-8', timeout=90,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'},
    )


@pytest.fixture(scope='module')
def taxonomy_run(tmp_path_factory):
    directory = tmp_path_factory.mktemp('shared-taxonomy')
    source = input_table(directory / 'species.tsv')
    output = directory / 'figures'
    run = run_taxonomy(source, output)
    assert run.returncode == 0, run.stderr
    evidence = json.loads((output / 'taxonomy_plots.provenance.json').read_text(encoding='utf-8'))
    return source, output, evidence


def test_automatic_stacked_calls_a_shared_renderer_without_changing_denominator(taxonomy_run):
    source, output, evidence = taxonomy_run
    execution = evidence['plots']['stacked'].get('execution')
    assert execution is not None, 'automatic plot did not record an executed shared renderer'
    assert execution['planner']['skill'] == 'metagenome-visualization'
    assert execution['planner']['version'] == '0.2.0'
    assert execution['planner']['view'] == 'composition_stacked_bar'
    assert execution['renderer']['function'].endswith('.draw_species_composition')
    assert execution['input_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert execution['normalization'] == 'none'
    assert execution['abundance_unit'] == 'fraction_of_reads'
    data = pd.read_csv(output / 'taxonomy_top20_stacked.data.tsv', sep='\t').set_index('sample_id')
    assert data.loc['S1'].tolist() == pytest.approx([.3, .2])
    assert data.loc['S0'].sum() == 0
    svg = (output / 'taxonomy_top20_stacked.svg').read_text(encoding='utf-8')
    assert 'Bracken-reported relative abundance (fraction)' in svg
    assert '100%' not in svg


def test_automatic_alpha_uses_full_matrix_with_shared_metrics_and_retains_zero_samples(taxonomy_run):
    source, output, evidence = taxonomy_run
    execution = evidence['plots']['diversity'].get('execution')
    assert execution is not None, 'automatic plot did not record the shared numerical call'
    assert execution['calculation']['function'].endswith('.alpha_diversity')
    assert execution['calculation']['metrics'] == ['observed_features', 'shannon', 'simpson']
    assert execution['input_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    alpha = pd.read_csv(output / 'taxonomy_alpha_diversity.data.tsv', sep='\t').set_index('sample_id')
    shannon = -(.6 * math.log(.6) + 2 * .2 * math.log(.2))
    assert alpha.loc['S1', ['observed_species', 'shannon', 'simpson_1_minus_d', 'pielou_evenness']].tolist() == pytest.approx(
        [3, shannon, .56, shannon / math.log(3)]
    )
    assert alpha.loc['S0', 'status'] == 'not_quantifiable'
    assert pd.isna(alpha.loc['S0', 'shannon'])
    assert pd.isna(alpha.loc['S2', 'pielou_evenness'])


def test_changed_shared_source_pin_fails_without_publishing_plots(tmp_path):
    source = input_table(tmp_path / 'species.tsv')
    output = tmp_path / 'figures'
    run = run_taxonomy(source, output, '--shared-taxonomy-pin', '0' * 64)
    assert run.returncode == 65
    evidence = json.loads((output / 'taxonomy_plots.provenance.json').read_text(encoding='utf-8'))
    assert evidence['status'] == 'failed'
    assert 'source pin changed' in evidence['reason']
    assert not list(output.glob('*.png'))


def test_standalone_workflow_never_claims_shared_execution(tmp_path):
    workflow = tmp_path / 'BioLLM_RawDataAnalysis' / 'workflow'
    script = workflow / 'bin' / 'visualization' / 'taxonomy_plots.py'
    shutil.copytree(VISUALIZATION, script.parent, ignore=shutil.ignore_patterns('__pycache__'))
    (workflow / 'assets').mkdir()
    shutil.copyfile(ENGINE / 'workflow' / 'assets' / 'figure_recipes.json', workflow / 'assets' / 'figure_recipes.json')
    source = input_table(tmp_path / 'species.tsv')
    output = tmp_path / 'figures'
    run = run_taxonomy(source, output, '--shared-taxonomy-pin', 'standalone', script=script)
    assert run.returncode == 0, run.stderr
    evidence = json.loads((output / 'taxonomy_plots.provenance.json').read_text(encoding='utf-8'))
    assert evidence['plots']['stacked']['execution']['provider'] == 'standalone'
    assert evidence['plots']['diversity']['execution']['provider'] == 'standalone'


def test_packaged_platform_includes_both_shared_libraries(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('build_server96', ENGINE.parent / 'deploy' / 'build_server96.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    monkeypatch.setattr(builder, 'ROOT', tmp_path)
    portable = tmp_path / 'BioLLM_RawDataAnalysis' / 'runtime' / 'project-portable.json'
    portable.parent.mkdir(parents=True)
    portable.write_text('{"manifests": []}', encoding='utf-8')
    required_sources = ['bioflow_skills/skills/visualization/metagenome_visualization.py',
                        'bioflow_ml/microbiome_analysis/diversity.py']
    for rel in required_sources:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# tracked Python source\n', encoding='utf-8')
    packaged = {p.relative_to(tmp_path).as_posix() for p in builder.selected()}
    assert set(required_sources) <= packaged


def test_preflight_fails_clearly_for_an_older_shared_renderer(tmp_path):
    workflow = tmp_path / 'BioLLM_RawDataAnalysis' / 'workflow'
    script = workflow / 'bin' / 'visualization' / 'shared_taxonomy.py'
    script.parent.mkdir(parents=True)
    shutil.copyfile(VISUALIZATION / 'shared_taxonomy.py', script)
    for package in ('bioflow_ml', 'bioflow_skills'):
        shutil.copytree(ENGINE.parent / package, tmp_path / package, ignore=shutil.ignore_patterns('tests', '__pycache__'))
    registry = tmp_path / 'bioflow_skills' / 'skills' / 'visualization' / 'metagenome_visualization.py'
    registry.write_text(registry.read_text(encoding='utf-8').replace('def draw_species_composition(', 'def legacy_only('),
                        encoding='utf-8')
    p = subprocess.run([sys.executable, str(script), '--fingerprint'], cwd=tmp_path,
                       capture_output=True, text=True, encoding='utf-8', timeout=60,
                       env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'})
    assert p.returncode == 65
    assert 'draw_species_composition' in p.stderr
    assert p.stdout == ''


@pytest.mark.skipif(os.name != 'posix', reason='production package link is POSIX')
def test_linked_shared_algorithm_uses_and_pins_the_central_source(tmp_path):
    project = tmp_path / 'analysis-site'
    script = project / 'BioLLM_RawDataAnalysis/workflow/bin/visualization/shared_taxonomy.py'
    script.parent.mkdir(parents=True)
    shutil.copyfile(VISUALIZATION / 'shared_taxonomy.py', script)
    shutil.copytree(ENGINE.parent / 'bioflow_skills', project / 'bioflow_skills',
                    ignore=shutil.ignore_patterns('tests', '__pycache__'))
    library = tmp_path / 'team-library' / 'bioflow_ml'
    shutil.copytree(ENGINE.parent / 'bioflow_ml', library, ignore=shutil.ignore_patterns('tests', '__pycache__'))
    (project / 'bioflow_ml').symlink_to(library, target_is_directory=True)

    def preflight():
        return subprocess.run([sys.executable, str(script), '--fingerprint'], cwd=tmp_path,
                              capture_output=True, text=True, encoding='utf-8', timeout=60)

    first = preflight()
    assert first.returncode == 0, first.stderr
    with (library / 'microbiome_analysis' / 'diversity.py').open('a', encoding='utf-8') as stream:
        stream.write('\n# Released central algorithm revision.\n')
    updated = preflight()
    assert updated.returncode == 0, updated.stderr
    assert len(first.stdout.strip()) == len(updated.stdout.strip()) == 64
    assert first.stdout != updated.stdout


def test_shared_library_does_not_reuse_stale_python_bytecode(tmp_path):
    script = tmp_path / 'BioLLM_RawDataAnalysis/workflow/bin/visualization/shared_taxonomy.py'
    script.parent.mkdir(parents=True)
    shutil.copyfile(VISUALIZATION / 'shared_taxonomy.py', script)
    for package in ('bioflow_ml', 'bioflow_skills'):
        shutil.copytree(ENGINE.parent / package, tmp_path / package, ignore=shutil.ignore_patterns('tests', '__pycache__'))
    renderer = tmp_path / 'bioflow_skills/skills/visualization/metagenome_visualization.py'
    current = renderer.read_bytes()
    name = b'draw_species_composition'
    previous = current.replace(b'def ' + name, b'def ' + b'x' * len(name))
    assert previous != current and len(previous) == len(current)
    # A deployment can preserve timestamps and source sizes across revisions.
    timestamp = 1780000000
    renderer.write_bytes(previous)
    os.utime(renderer, (timestamp, timestamp))
    py_compile.compile(str(renderer), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    renderer.write_bytes(current)
    os.utime(renderer, (timestamp, timestamp))
    p = subprocess.run([sys.executable, str(script), '--fingerprint'], cwd=tmp_path,
                       capture_output=True, text=True, encoding='utf-8', timeout=60,
                       env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'})
    assert p.returncode == 0, p.stderr
    assert len(p.stdout.strip()) == 64


def test_stage_catalog_assigns_execution_only_to_the_matching_figure(taxonomy_run, tmp_path):
    _, output, evidence = taxonomy_run
    stage_dir = tmp_path / 'work' / 'fixture' / 'ab' / '0123456789a'
    shutil.copytree(output, stage_dir / 'taxonomy_plots')
    (stage_dir / '.exitcode').write_text('0', encoding='utf-8')
    task_dir = tmp_path / 'outputs' / 'fixture'
    task_dir.mkdir(parents=True)
    (task_dir / 'trace.tsv').write_text(
        'hash\tname\tstatus\texit\nab/012345\tTAXONOMY_PLOTS\tCOMPLETED\t0\n', encoding='utf-8')
    result = subprocess.run([sys.executable, '-c', '''
import json, sys
from pathlib import Path
from backend.app.models import TaskRepository
from backend.app.artifacts import ArtifactRepository
from backend.app.services.artifacts import ArtifactService
from backend.app.services.stage_figures import publish_stage_figures
base = Path(sys.argv[1]).resolve()
tasks = TaskRepository(base / 'tasks.sqlite3')
tasks.initialize()
tasks.create_task('fixture', 'not-an-analysis', ['report'])
artifacts = ArtifactRepository(base / 'tasks.sqlite3')
artifacts.initialize()
service = ArtifactService(artifacts, base / 'outputs')
publish_stage_figures(service, 'fixture')
publish_stage_figures(service, 'fixture')  # Exercises idempotent refresh, too.
with artifacts._connect() as db:
    print(json.dumps({Path(row[0]).name:json.loads(row[1]) for row in
        db.execute("SELECT path, metadata_json FROM artifacts WHERE task_id='fixture'")}))
''', str(tmp_path)], cwd=ENGINE, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    records = json.loads(result.stdout)
    assert records['taxonomy_top20_stacked.png'].get('figure_execution') == evidence['plots']['stacked']['execution']
    assert records['taxonomy_top20_stacked.pdf'].get('figure_execution') == evidence['plots']['stacked']['execution']
    assert records['taxonomy_alpha_diversity.png'].get('figure_execution') == evidence['plots']['diversity']['execution']
    assert 'figure_execution' not in records['taxonomy_species_heatmap.png']
    assert 'execution' not in (records['taxonomy_species_heatmap.png'].get('diversity') or {})


@pytest.mark.parametrize('chart, method', [('stacked', 'draw_species_composition'), ('alpha_diversity', 'alpha_diversity')])
def test_redraw_exports_shared_execution_and_keeps_display_parameters(tmp_path, chart, method):
    source = input_table(tmp_path / 'source.tsv')
    (tmp_path / 'request.json').write_text(json.dumps({
        'data_type': 'taxonomy', 'chart': chart, 'source_artifact_ids': ['fixture-id'],
        'sample_ids': ['S1', 'S2', 'S0'], 'groups': {},
        'parameters': {'top_n': 1, 'font_size': 13, 'width_inches': 9},
        'snapshot_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'job_id': 'redraw-fixture', 'task_id': 'task-fixture',
        'source_sha256': {'fixture-id': 'fixture-digest'},
        'profile': {'sample_count': 2, 'feature_count': 3},
    }), encoding='utf-8')
    p = subprocess.run(
        [sys.executable, '-m', 'backend.app.services.figure_render', str(tmp_path)],
        cwd=ENGINE, capture_output=True, text=True, encoding='utf-8', timeout=90,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'},
    )
    assert p.returncode == 0, p.stderr
    manifest = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    evidence = json.loads((tmp_path / 'rendered' / 'taxonomy_plots.provenance.json').read_text(encoding='utf-8'))
    execution = evidence['plots'][chart].get('execution')
    assert execution is not None, 'redraw did not report an executed shared method'
    call = execution['renderer' if chart == 'stacked' else 'calculation']
    assert call['function'].endswith('.' + method)
    assert manifest['execution'] == execution
    assert manifest['parameters']['top_n'] == 1
    assert manifest['parameters']['font_size'] == evidence['render_options']['font_size'] == 13
    assert manifest['parameters']['width_inches'] == evidence['render_options']['width_inches'] == 9
    artifact_params = tmp_path / 'rendered' / f'taxonomy_{chart}.parameters.json'
    assert json.loads(artifact_params.read_text(encoding='utf-8'))['figure_execution'] == execution
    if chart == 'stacked':
        data = pd.read_csv(tmp_path / 'rendered' / 'taxonomy_stacked.data.tsv', sep='\t')
        assert data.sample_id.tolist() == ['S1', 'S2', 'S0']
        assert data.iloc[0, 1:].tolist() == pytest.approx([.3, .2])
        svg = (tmp_path / 'rendered' / 'taxonomy_stacked.svg').read_text(encoding='utf-8')
        assert 'Species composition · Top-1 plus Other' in svg
        assert 'Other (display only)' in svg


def test_queued_redraw_binds_shared_library_and_refuses_a_different_version(tmp_path):
    task = tmp_path / 'outputs' / 'fixture'
    task.mkdir(parents=True)
    input_table(task / 'species.tsv')
    p = subprocess.run([sys.executable, '-c', '''
import json, sys
from pathlib import Path
from backend.app.config import Settings
from backend.app.models import TaskRepository
from backend.app.artifacts import ArtifactRepository
from backend.app.services.artifacts import ArtifactService
from backend.app.services.figure_workbench import FigureWorkbench
base = Path(sys.argv[1]).resolve()
settings = Settings(base / 'inputs', base, Path('workflow/main.nf'), auto_run=False)
settings.prepare()
tasks = TaskRepository(settings.database_path)
tasks.initialize()
tasks.create_task('fixture', 'not-run', ['report'])
tasks.update_task('fixture', status='completed')
artifacts = ArtifactRepository(settings.database_path)
artifacts.initialize()
service = ArtifactService(artifacts, base / 'outputs')
record = service.register(task_id='fixture', producer='fixture', artifact_type='taxonomy.species_abundance',
    media_type='text/tab-separated-values', path=base / 'outputs/fixture/species.tsv')
class HoldingQueue:
    def submit(self, *args, **kwargs):
        return True
figures = FigureWorkbench(settings, tasks, service, HoldingQueue())
figures.initialize()
job = figures.create('fixture', {'data_type':'taxonomy', 'chart':'alpha_diversity',
    'source_artifact_ids':[record['artifact_id']], 'sample_ids':['S1','S2','S0']})
assert job['status'] == 'queued'
print(json.dumps({'dir':str(figures.job_dir('fixture', job['id']))}))
''', str(tmp_path)], cwd=ENGINE, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert p.returncode == 0, p.stderr
    directory = Path(json.loads(p.stdout)['dir'])
    request_path = directory / 'request.json'
    request = json.loads(request_path.read_text(encoding='utf-8'))
    assert len(request.get('shared_taxonomy_pin', '')) == 64
    request['shared_taxonomy_pin'] = '0' * 64
    request_path.write_text(json.dumps(request), encoding='utf-8')
    run = subprocess.run(
        [sys.executable, '-m', 'backend.app.services.figure_render', str(directory)],
        cwd=ENGINE, capture_output=True, text=True, encoding='utf-8', timeout=90,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'},
    )
    assert run.returncode != 0
    assert 'source pin changed' in run.stderr
    assert not (directory / 'result.json').exists()


@pytest.mark.skipif(not shutil.which('nextflow') or os.name != 'posix', reason='Nextflow local executor required')
def test_nextflow_resume_rebuilds_figures_when_shared_library_changes(tmp_path):
    """Two positive cache hits make a source-change miss meaningful."""
    project = tmp_path / 'bioflow'
    workflow = project / 'BioLLM_RawDataAnalysis' / 'workflow'
    (workflow / 'modules').mkdir(parents=True)
    shutil.copytree(VISUALIZATION, workflow / 'bin' / 'visualization', ignore=shutil.ignore_patterns('__pycache__'))
    (workflow / 'assets').mkdir()
    shutil.copyfile(ENGINE / 'workflow' / 'assets' / 'figure_recipes.json', workflow / 'assets' / 'figure_recipes.json')
    shutil.copyfile(ENGINE / 'workflow' / 'modules' / 'taxonomy_plots.nf', workflow / 'modules' / 'taxonomy_plots.nf')
    for package in ('bioflow_ml', 'bioflow_skills'):
        shutil.copytree(ENGINE.parent / package, project / package,
                        ignore=shutil.ignore_patterns('tests', '__pycache__'))
    source = input_table(workflow / 'species.tsv')
    entry = workflow / 'check.nf'
    entry.write_text("""nextflow.enable.dsl=2
params.task_id = 'cache-fixture'
params.taxonomy_plot_top_n = 1
params.taxonomy_plot_low_abundance_threshold = 0.01
params.taxonomy_alpha_style = 'points'
params.figure_extended_plots = false
params.figure_ai_advice = false
include { TAXONOMY_PLOTS; taxonomyFigurePin } from './modules/taxonomy_plots'
workflow {
    TAXONOMY_PLOTS(file("${projectDir}/species.tsv"), [], '',
        file("${projectDir}/bin/visualization/taxonomy_plots.py"), taxonomyFigurePin(projectDir))
}
""", encoding='utf-8')
    command = ['nextflow', 'run', str(entry), '-work-dir', str(tmp_path / 'work')]
    env = {**os.environ, 'BIOLLM_TAXONOMY_FIGURE_PYTHON': sys.executable,
           'PYTHONDONTWRITEBYTECODE': '1', 'NXF_ANSI_LOG': 'false'}

    def run(label, *, resume):
        trace = tmp_path / f'trace-{label}.tsv'
        result = subprocess.run(command + ['-with-trace', str(trace)] + (['-resume'] if resume else []),
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=150)
        assert result.returncode == 0, result.stderr + '\n' + result.stdout
        rows = pd.read_csv(trace, sep='\t')
        assert len(rows) == 1 and rows.iloc[0]['name'].startswith('TAXONOMY_PLOTS')
        return rows.iloc[0]['status']

    assert run('first', resume=False) == 'COMPLETED'
    assert run('unchanged', resume=True) == 'CACHED'
    method = project / 'bioflow_ml' / 'microbiome_analysis' / 'diversity.py'
    with method.open('a', encoding='utf-8') as f:
        f.write('\n# New installed shared-library revision for cache-invalidation test.\n')
    assert run('changed', resume=True) == 'COMPLETED'
    assert run('changed-unchanged', resume=True) == 'CACHED'
    provenance_files = list((tmp_path / 'work').glob('*/*/taxonomy_plots/taxonomy_plots.provenance.json'))
    assert len(provenance_files) == 2
    evidence = [json.loads(p.read_text(encoding='utf-8')) for p in provenance_files]
    assert {e['input_sha256'] for e in evidence} == {hashlib.sha256(source.read_bytes()).hexdigest()}
    assert len({e['plots']['diversity']['execution']['library_source_sha256'] for e in evidence}) == 2
