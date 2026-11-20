"""Bridge from validated workflow tables to pinned shared BioFlow figure methods."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile


PROJECT = Path(__file__).resolve().parents[4]
PACKAGES = ('bioflow_skills', 'bioflow_ml')
STANDALONE = 'standalone'
SKILL_MODULE = 'bioflow_skills.skills.visualization.metagenome_visualization'
ALPHA_MODULE = 'bioflow_ml.microbiome_analysis.diversity'
SKILL_VERSION = '0.2.0'


class SharedTaxonomyError(ValueError):
    pass


def source_snapshot(root: Path) -> dict[str, str]:
    """Inspect only Python source in the two installed sibling packages."""
    installed = [root / name for name in PACKAGES if (root / name).is_dir()]
    if not installed:
        if (root / 'deploy' / 'server96.py').is_file():
            raise SharedTaxonomyError('integrated BioFlow requires the installed shared taxonomy libraries')
        return {}
    if len(installed) != len(PACKAGES):
        raise SharedTaxonomyError('shared taxonomy requires both bioflow_skills and bioflow_ml')
    files = {}
    for package in installed:
        physical = package.resolve()
        # A deployment may link the top-level algorithm package to the
        # maintained shared library. Task input never supplies this path.
        if not (physical / '__init__.py').is_file() or (package.name != 'bioflow_ml' and physical != package):
            raise SharedTaxonomyError(f'invalid shared taxonomy package: {package.name}')
        for path in sorted(physical.rglob('*')):
            relative = Path(package.name) / path.relative_to(physical)
            if any(part in {'tests', '__pycache__'} or part.startswith('.') for part in relative.parts):
                continue
            if path.resolve() != path:
                raise SharedTaxonomyError(f'shared taxonomy source may not be a symlink: {relative.as_posix()}')
            if path.suffix == '.py' and path.is_file():
                files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def source_pin(files: dict[str, str]) -> str:
    if not files:
        return STANDALONE
    encoded = json.dumps(files, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


class SharedTaxonomy:
    def __init__(self, root: Path, files: dict[str, str]):
        self.root = root
        self.files = files
        self.pin = source_pin(files)
        self._package_roots = {name: (root / name).resolve() for name in PACKAGES}
        self._verified_packages = {}

    def _module(self, name):
        relative = name.replace('.', '/') + '.py'
        package_name, inner = relative.split('/', 1)
        expected = self._package_roots[package_name] / inner
        if relative not in self.files or not expected.is_file() or (self.root / relative).resolve() != expected:
            raise SharedTaxonomyError(f'missing pinned shared taxonomy module: {name}')
        if hashlib.sha256(expected.read_bytes()).hexdigest() != self.files[relative]:
            raise SharedTaxonomyError(f'shared taxonomy source changed during rendering: {name}')
        # Use the two packages shipped with this BioFlow checkout, not an
        # unrelated package with the same name from site-packages or PYTHONPATH.
        sys.path.insert(0, str(self.root))
        already_loaded = sys.modules.get(package_name)
        previous_cache = sys.pycache_prefix
        previous_write = sys.dont_write_bytecode
        try:
            if already_loaded is not None and self._verified_packages.get(package_name) is not already_loaded:
                raise SharedTaxonomyError(f'shared taxonomy package was imported before source verification: {package_name}')
            # -B alone still reads old .pyc files. An empty cache plus -B
            # means the isolated renderer imports from the pinned source.
            with tempfile.TemporaryDirectory(prefix='bioflow-taxonomy-import-') as empty_cache:
                sys.pycache_prefix = empty_cache
                sys.dont_write_bytecode = True
                module = importlib.import_module(name)
                self._verified_packages[package_name] = sys.modules[package_name]
        except ImportError as exc:
            raise SharedTaxonomyError(f'cannot import {name}; install this checkout’s plotting dependencies: {exc}') from exc
        finally:
            sys.pycache_prefix = previous_cache
            sys.dont_write_bytecode = previous_write
            sys.path.pop(0)
        if Path(module.__file__).resolve() != expected:
            raise SharedTaxonomyError(f'unexpected shared taxonomy module origin: {name}')
        return module, self.files[relative]

    def _function(self, module_name, name):
        module, digest = self._module(module_name)
        function = getattr(module, name, None)
        code = getattr(function, '__code__', None)
        if code is None or Path(code.co_filename).resolve() != Path(module.__file__).resolve():
            raise SharedTaxonomyError(f'shared taxonomy requires {module_name}.{name} in the pinned source')
        return function, digest

    def draw_stacked(self, axis, display, *, top_n, task_id, input_sha256):
        view = 'composition_stacked_bar'
        run, planner_sha = self._function('bioflow_skills.runner', 'run_skill')
        draw, renderer_sha = self._function(SKILL_MODULE, 'draw_species_composition')
        plan = run('metagenome-visualization', {
            'task_context': 'standardized BioFlow species abundance table',
            'task_id': task_id, 'profile_type': 'bracken',
            'abundance_unit': 'fraction_of_reads', 'table_role': 'species_abundance',
            'abundance_table': 'sha256:' + input_sha256, 'requested_views': [view],
        }, version=SKILL_VERSION)
        if not plan.succeeded or plan.version != SKILL_VERSION:
            raise SharedTaxonomyError('shared metagenome-visualization rejected the species composition plan')
        contract = plan.data['visualization_plan']['input_contract']
        if (contract['measurement'], contract.get('abundance_unit')) != ('relative_abundance', 'fraction_of_reads'):
            raise SharedTaxonomyError('shared species composition plan does not preserve the Bracken fraction scale')
        if [spec['name'] for spec in plan.data['figure_specifications']] != [view]:
            raise SharedTaxonomyError('shared metagenome-visualization did not approve the requested view')
        draw(axis, display, top_n=top_n)
        return {'provider': 'bioflow_shared', 'library_source_sha256': self.pin,
                'input_sha256': input_sha256, 'abundance_unit': 'fraction_of_reads',
                'normalization': 'none', 'other_policy': 'sum omitted reported species, not a complement to one',
                'planner': {'function': f'{run.__module__}.{run.__qualname__}', 'source_sha256': planner_sha,
                            'skill': plan.skill, 'version': plan.version, 'view': view,
                            'skill_source_sha256': renderer_sha},
                'renderer': {'function': f'{draw.__module__}.{draw.__qualname__}',
                             'version': sys.modules[SKILL_MODULE].SPECIES_COMPOSITION_RENDERER_VERSION,
                             'source_sha256': renderer_sha}}

    def alpha(self, matrix, *, input_sha256):
        metrics = ('observed_features', 'shannon', 'simpson')
        calculate, digest = self._function(ALPHA_MODULE, 'alpha_diversity')
        values = calculate(matrix, metrics=metrics)
        return values, {'provider': 'bioflow_shared', 'library_source_sha256': self.pin,
                        'input_sha256': input_sha256, 'matrix': 'full_species_matrix',
                        'normalization': 'divide each quantifiable sample by its total reported species fraction',
                        'calculation': {'function': f'{calculate.__module__}.{calculate.__qualname__}',
                                        'source_sha256': digest, 'version': 'sha256:' + digest,
                                        'metrics': list(metrics)},
                        'pielou': 'BioFlow: Shannon/log(observed species) when observed > 1'}

    def preflight(self):
        for module_name, function in (
            ('bioflow_skills.runner', 'run_skill'),
            (SKILL_MODULE, 'draw_species_composition'),
            (ALPHA_MODULE, 'alpha_diversity'),
        ):
            self._function(module_name, function)
        registry, _ = self._module('bioflow_skills.registry')
        if not registry.get_registry().has('metagenome-visualization', SKILL_VERSION):
            raise SharedTaxonomyError(f'shared taxonomy requires metagenome-visualization@{SKILL_VERSION}')

    def verify(self):
        if any((self.root / name).resolve() != path for name, path in self._package_roots.items()) or \
                source_pin(source_snapshot(self.root)) != self.pin:
            raise SharedTaxonomyError('shared taxonomy sources changed during rendering')


def resolve_shared_taxonomy(expected_pin: str | None = None):
    files = source_snapshot(PROJECT)
    pin = source_pin(files)
    if expected_pin is not None and expected_pin != pin:
        raise SharedTaxonomyError('shared taxonomy source pin changed after workflow submission')
    return SharedTaxonomy(PROJECT, files) if files else None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Print the taxonomy renderer cache identity.')
    parser.add_argument('--fingerprint', action='store_true', required=True)
    parser.parse_args()
    try:
        installed = resolve_shared_taxonomy()
        if installed is not None:
            installed.preflight()
            installed.verify()
        print(installed.pin if installed is not None else STANDALONE)
    except (SharedTaxonomyError, OSError) as exc:
        parser.exit(65, f'{exc}\n')
