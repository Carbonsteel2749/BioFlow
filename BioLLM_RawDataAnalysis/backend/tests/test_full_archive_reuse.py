import os
from pathlib import Path
import tarfile

import pytest

from backend.app.config import Settings
from backend.app.models import TaskRepository
from backend.app.services.artifacts import ArtifactService
from backend.app.services.figure_workbench import FigureWorkbench
from backend.app.services.full_archive import FullArchiveService


@pytest.fixture
def archive_run(tmp_path, monkeypatch):
    if not Path('/proc').is_dir():
        # Windows unit test only: production's process check remains Linux-only.
        monkeypatch.setattr('backend.app.services.full_archive._process_check', lambda *_: None)
    if not hasattr(os, 'O_DIRECTORY'):
        monkeypatch.setattr('backend.app.services.full_archive.sync_directory', lambda *_: None)
        monkeypatch.setattr('backend.app.services.archive_naming.sync_directory', lambda *_: None)
        # Windows rejects fsync on an archive reopened read-only.
        monkeypatch.setattr(os, 'fsync', lambda *_: None)
    settings = Settings(tmp_path / 'inputs', tmp_path / 'runtime', tmp_path / 'main.nf')
    settings.prepare()
    task = 'fixture-archive'
    manifest = settings.input_root / 'samples.csv'
    manifest.write_text('sample_id,read1,read2\nexample,r1.fastq,r2.fastq\n')
    for mate in (1, 2):
        (settings.input_root / f'r{mate}.fastq').write_text(f'@toy/{mate}\nA\n+\nI\n')
    work = settings.state_root / 'work' / task
    work.mkdir()
    member = work / 'answer.tsv'
    member.write_bytes(b'feature\t1\n')
    output = settings.state_root / 'outputs' / task / 'deliverables'
    output.mkdir(parents=True)
    (settings.state_root / 'logs' / task).mkdir()
    report = work / 'report.html'
    report.write_text('complete original report')
    with tarfile.open(output / f'{task}.tar.gz', 'w:gz') as tar:
        tar.add(report, arcname='report.html')
    repo = TaskRepository(settings.database_path)
    repo.initialize()
    repo.create_task(task, str(manifest), [])
    with repo._connect() as db:
        db.execute("UPDATE tasks SET status='completed' WHERE id=?", (task,))
    return FullArchiveService(settings, repo), task, member


def test_unchanged_archive_is_not_replaced(archive_run):
    service, task, _ = archive_run
    first = service.build(task)
    archive = Path(first['archive_path'])
    original = archive.stat()
    second = service.build(task)
    assert archive.stat().st_ino == original.st_ino
    assert archive.stat().st_mtime_ns == original.st_mtime_ns
    assert second == first
    service.verify(archive)


@pytest.mark.skipif(os.name != 'posix', reason='requires POSIX change time semantics')
def test_source_edit_with_same_size_and_mtime_rebuilds(archive_run):
    service, task, source = archive_run
    first = service.build(task)
    st = source.stat()
    source.write_bytes(b'feature\t2\n')
    os.utime(source, ns=(st.st_atime_ns, st.st_mtime_ns))
    archive = Path(service.build(task)['archive_path'])
    service.verify(archive)
    with tarfile.open(archive, 'r:gz') as tar:
        assert tar.extractfile('intermediates/work/answer.tsv').read() == b'feature\t2\n'
        assert tar.extractfile('report.html').read() == b'complete original report'
    assert first['archive_path'] == str(archive)


def test_damaged_gzip_trailer_is_not_reused_or_overwritten(archive_run):
    service, task, _ = archive_run
    archive = Path(service.build(task)['archive_path'])
    original = archive.read_bytes()
    damaged = original[:-1] + bytes([original[-1] ^ 1])
    archive.write_bytes(damaged)
    with pytest.raises(ValueError, match='integrity'):
        service.build(task)
    assert archive.read_bytes() == damaged


def test_repeated_figure_archive_job_keeps_same_package(archive_run):
    service, task, _ = archive_run
    root = service.settings.state_root / 'outputs'
    (root / task / 'figure_workbench').mkdir()
    workbench = FigureWorkbench(service.settings, service.repository, ArtifactService(None, root), None)
    workbench.initialize()
    workbench._insert(task, 'first', 'archive', 'archive', {})
    workbench._run_archive(task, 'first')
    assert workbench.get(task, 'first')['status'] == 'succeeded'
    archive = Path(service.repository.get_task(task)['result_archive'])
    original = archive.stat()
    workbench._insert(task, 'second', 'archive', 'archive', {})
    workbench._run_archive(task, 'second')
    assert workbench.get(task, 'second')['status'] == 'succeeded'
    assert archive.stat().st_ino == original.st_ino


def test_published_archive_remains_usable_if_receipt_write_fails(archive_run, monkeypatch):
    service, task, source = archive_run
    service.build(task)
    source.write_bytes(b'feature\t200\n')

    def fail_receipt(*_):
        raise OSError('simulated unavailable receipt storage')

    with monkeypatch.context() as failure:
        failure.setattr('backend.app.services.full_archive._save_verified_snapshot', fail_receipt)
        archive = Path(service.build(task)['archive_path'])
        service.verify(archive)

    recovered = service.build(task)
    assert recovered['archive_path'] == str(archive)
    with tarfile.open(archive, 'r:gz') as tar:
        assert tar.extractfile('intermediates/work/answer.tsv').read() == b'feature\t200\n'
    assert service.build(task) == recovered
