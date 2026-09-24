from pathlib import Path

import httpx
import pytest

from backend.app.config import Settings
from backend.app.models import TaskRepository
from backend.app.services.runner import TaskRunner
from backend.app.services.runner import _source_error_excerpt


def test_error_excerpt_keeps_actual_cause_after_long_command_output():
    text = "ERROR ~ Error executing process > 'HOST_DEPLETION (S)'\n"
    text += "Command executed:\n" + "  verbose command\n" * 200
    text += "Command output:\n" + "  alignment details\n" * 200
    text += "[ERROR] failure_stage=threshold reason=retained reads violate configured host-depletion thresholds\n"
    text += "Tip: rerun with resume\n"
    result = _source_error_excerpt(text)
    assert 'reason=retained reads violate configured host-depletion thresholds' in result
    assert 'HOST_DEPLETION' in result
    assert len(result) <= 1800


def test_error_excerpt_retains_structured_status_after_nextflow_tip():
    result = _source_error_excerpt("ERROR ~ Error executing process > 'HOST_DEPLETION'\n"
                                  "Command output:\nnormal output\nTip: inspect log\n"
                                  "[ERROR] failure_stage=threshold reason=retained reads violate threshold")
    assert 'reason=retained reads violate threshold' in result


def test_failure_pending_does_not_overwrite_cancelled_task(tmp_path):
    repository = TaskRepository(tmp_path/'state.sqlite3')
    repository.initialize()
    repository.create_task('task', 'samples.csv', ['validate'])
    repository.cancel_task('task')
    repository.publish_failure_pending('task', 'validate', 'error')
    assert repository.get_task('task')['status'] == 'cancelled'


def test_failure_visible_before_optional_model_responds(tmp_path, monkeypatch):
    settings = Settings(input_root=tmp_path/'incoming', state_root=tmp_path/'runtime',
                        workflow_path=tmp_path/'main.nf', auto_run=False)
    settings.prepare()
    manifest = settings.input_root/'samples.csv'
    manifest.write_text('sample_id,read1,read2\nSAMPLE_ALPHA,a,b\n')
    repository = TaskRepository(settings.database_path)
    repository.initialize()
    repository.create_task('task', str(manifest), ['validate','host_depletion'])

    class ObservingDiagnostic:
        def diagnose_failure(self, **kwargs):
            task = repository.get_task('task')
            assert task['status'] == 'paused'
            assert task['retry_allowed'] is False
            assert 'retained reads violate' in task['error_message']
            assert task['steps'][-1]['status'] == 'failed'
            raise httpx.ReadTimeout('timeout')

    runner = TaskRunner(settings, repository, ObservingDiagnostic())
    def failed_workflow(*, runner_log, output_dir, **kwargs):
        repository.update_task('task', current_step='host_depletion')
        runner_log.write_text("ERROR ~ Error executing process > 'HOST_DEPLETION (S)'\n"
                              + 'routine output\n'*1800 + '\nTip: inspect work directory\n')
        (output_dir/'status').mkdir(exist_ok=True)
        (output_dir/'status'/'S.host_depletion.json').write_text(
            '{"status":"failed","exit_code":65,"message":"failure_stage=threshold reason=retained reads violate configured host-depletion thresholds"}')
        return 1
    monkeypatch.setattr(runner, '_run_nextflow', failed_workflow)
    runner.run('task')
    task = repository.get_task('task')
    assert 'retained reads violate' in task['error_message']
    assert task['retry_count'] == 0


@pytest.mark.parametrize("process", ["REPORT", "PIPELINE:REPORT (task)"])
def test_report_input_failure_preserves_source_when_diagnostic_times_out(tmp_path, monkeypatch, process):
    settings = Settings(input_root=tmp_path / "incoming", state_root=tmp_path / "runtime",
                        workflow_path=tmp_path / "main.nf", auto_run=False)
    settings.prepare()
    manifest = settings.input_root / "samples.csv"
    manifest.write_text("sample_id,read1,read2\nPRIVATE_SAMPLE,/secret/a.fastq,/secret/b.fastq\n")
    repository = TaskRepository(settings.database_path)
    repository.initialize()
    repository.create_task("task", str(manifest), ["validate", "functional_annotation", "report"])

    class TimeoutDiagnostic:
        def diagnose_failure(self, **kwargs):
            assert kwargs["step"] == "report"
            assert "PRIVATE_SAMPLE" not in kwargs["log_excerpt"]
            raise httpx.ReadTimeout("diagnostic timeout")

    runner = TaskRunner(settings, repository, TimeoutDiagnostic())

    def failed_workflow(*, output_dir, runner_log, **kwargs):
        repository.update_task("task", current_step="functional_annotation")
        (output_dir / "trace.tsv").write_text(
            "name\tstatus\nVALIDATE_MANIFEST\tCOMPLETED\n"
            "FUNCTIONAL_ANNOTATION (PRIVATE_SAMPLE)\tCOMPLETED\n"
            "FUNCTIONAL_PLOTS (functional-cohort)\tCOMPLETED\n")
        runner_log.write_text(
            f"\x1b[31mERROR ~ Error executing process > '{process}'\x1b[0m\n\n"
            "Caused by:\n  Not a valid path value: 'PRIVATE_SAMPLE'\n"
            "  password=super-secret /home/private/input.fastq\n\n"
            "Tip: inspect the work directory\n -- Check '.nextflow.log' file for details\n")
        return 1

    monkeypatch.setattr(runner, "_run_nextflow", failed_workflow)
    runner.run("task")
    task = repository.get_task("task")
    assert task["status"] == "paused"
    assert task["current_step"] == "report"
    steps = {item["name"]: item for item in task["steps"]}
    assert steps["functional_annotation"]["status"] == "succeeded"
    assert steps["report"]["status"] == "failed"
    assert "Not a valid path value" in task["error_message"]
    assert "诊断" in task["error_message"]
    assert "Not a valid path value" in task["alerts"][-1]["message"]
    for text in (task["error_message"], task["alerts"][-1]["message"]):
        assert "PRIVATE_SAMPLE" not in text
        assert "super-secret" not in text
        assert "/home/private" not in text
        assert "\x1b" not in text


def test_cancellation_during_diagnosis_is_not_overwritten(tmp_path, monkeypatch):
    settings = Settings(input_root=tmp_path / "incoming", state_root=tmp_path / "runtime",
                        workflow_path=tmp_path / "main.nf", auto_run=False)
    settings.prepare()
    manifest = settings.input_root / "samples.csv"
    manifest.write_text("sample_id,read1,read2\nS,a,b\n")
    repository = TaskRepository(settings.database_path)
    repository.initialize()
    repository.create_task("task", str(manifest), ["validate", "report"])

    class CancellingDiagnostic:
        def diagnose_failure(self, **kwargs):
            repository.cancel_task("task")
            raise httpx.ReadTimeout("timeout")

    runner = TaskRunner(settings, repository, CancellingDiagnostic())
    monkeypatch.setattr(runner, "_run_nextflow", lambda **kwargs: 1)
    runner.run("task")
    assert repository.get_task("task")["status"] == "cancelled"
