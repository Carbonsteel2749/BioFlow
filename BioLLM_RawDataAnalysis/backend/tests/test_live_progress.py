import json

import pytest

from backend.app.models import TaskRepository
from backend.app.services.progress import sync_trace


@pytest.fixture
def case(tmp_path):
    repository = TaskRepository(tmp_path / "tasks.sqlite")
    repository.initialize()
    repository.create_task("task", "samples.csv", ["taxonomy", "functional_annotation", "report"])
    repository.update_task("task", status="running", started_at="2026-09-22T12:00:00Z")
    trace = tmp_path / "trace.tsv"
    (tmp_path / "status").mkdir()
    return repository, trace


def status_file(trace, sample="S01", **changes):
    data = dict(task_id="task", sample_id=sample, step="functional_annotation",
                status="running", started_at="2026-09-22T12:01:00Z", finished_at=None)
    data.update(changes)
    path = trace.parent / "status" / f"{sample}.functional_annotation.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def functional(repository):
    return repository.get_task("task")["steps"][1]


@pytest.mark.parametrize("with_trace", [False, True])
def test_live_status_marks_functional_running_before_trace_completion(case, with_trace):
    repository, trace = case
    if with_trace:
        trace.write_text("name\tstatus\nTAXONOMY (S01)\tCOMPLETED\n")
    status_file(trace)
    sync_trace(repository, "task", trace)
    assert functional(repository)["status"] == "running"
    assert functional(repository)["started_at"] == "2026-09-22T12:01:00Z"
    assert functional(repository)["progress"] is None
    assert repository.get_task("task")["current_step"] == "functional_annotation"


def test_another_running_sample_prevents_premature_completion(case):
    repository, trace = case
    trace.write_text("name\tstatus\nFUNCTIONAL_ANNOTATION (S01)\tCOMPLETED\n")
    status_file(trace, sample="S02")
    sync_trace(repository, "task", trace)
    assert functional(repository)["status"] == "running"
    assert functional(repository)["progress"] is None


@pytest.mark.parametrize("trace_status, expected", [("COMPLETED", "succeeded"), ("FAILED", "failed")])
def test_terminal_trace_wins_over_stale_running_file(case, trace_status, expected):
    repository, trace = case
    status_file(trace)
    trace.write_text(f"name\tstatus\nFUNCTIONAL_ANNOTATION (S01)\t{trace_status}\n")
    sync_trace(repository, "task", trace)
    assert functional(repository)["status"] == expected


@pytest.mark.parametrize("changes", [{"task_id": "other"}, {"started_at": "2026-09-21T00:00:00Z"}, {"started_at": "bad-time"}, {"step": "unknown"}])
def test_unrelated_or_old_attempt_status_is_ignored(case, changes):
    repository, trace = case
    status_file(trace, **changes)
    sync_trace(repository, "task", trace)
    assert functional(repository)["status"] == "pending"


def test_malformed_status_does_not_block_valid_status(case):
    repository, trace = case
    status_file(trace)
    (trace.parent / "status" / "broken.json").write_text('{"status":')
    (trace.parent / "status" / "wrong-type.json").write_text('[]')
    sync_trace(repository, "task", trace)
    assert functional(repository)["status"] == "running"


def test_cancelled_task_is_not_overwritten_by_abort_trace(case):
    repository, trace = case
    trace.write_text("name\tstatus\nFUNCTIONAL_ANNOTATION (S01)\tABORTED\n")
    repository.cancel_task("task")
    before = repository.get_task("task")
    sync_trace(repository, "task", trace)
    assert repository.get_task("task") == before


def test_completion_timestamp_does_not_move_on_each_poll(case):
    repository, trace = case
    trace.write_text("name\tstatus\nFUNCTIONAL_ANNOTATION (S01)\tCOMPLETED\n")
    sync_trace(repository, "task", trace)
    before = functional(repository)
    sync_trace(repository, "task", trace)
    assert functional(repository) == before


def test_cancellation_between_read_and_write_wins(case, monkeypatch):
    repository, trace = case
    trace.write_text("name\tstatus\nFUNCTIONAL_ANNOTATION (S01)\tRUNNING\n")
    real_get = repository.get_task

    def cancel_after_read(task_id):
        snapshot = real_get(task_id)
        repository.cancel_task(task_id)
        return snapshot

    monkeypatch.setattr(repository, "get_task", cancel_after_read)
    sync_trace(repository, "task", trace)
    task = real_get("task")
    assert task["status"] == "cancelled"
    assert task["steps"][1]["status"] == "skipped"
    assert task["current_step"] is None


def test_shell_second_precision_start_is_not_mistaken_for_previous_attempt(case):
    repository, trace = case
    repository.update_task("task", started_at="2026-09-22T12:01:00.500000+00:00")
    status_file(trace, started_at="2026-09-22T12:01:00Z")
    sync_trace(repository, "task", trace)
    assert functional(repository)["status"] == "running"


def test_runner_publishes_running_state_while_child_is_alive(case):
    import sys
    import time
    from concurrent.futures import ThreadPoolExecutor

    from backend.app.config import Settings
    from backend.app.services.runner import TaskRunner

    repository, trace = case
    output = trace.parent
    settings = Settings(input_root=output, state_root=output,
                        workflow_path=output / "main.nf", poll_interval_seconds=0.02)
    runner = TaskRunner(settings, repository)
    child = (
        "import json,pathlib,time\n"
        "p=pathlib.Path('status/S01.functional_annotation.json')\n"
        "p.write_text(json.dumps(dict(task_id='task',sample_id='S01',"
        "step='functional_annotation',status='running',started_at='2026-09-22T12:01:00Z')))\n"
        "deadline=time.monotonic()+5\n"
        "while not pathlib.Path('release').exists() and time.monotonic()<deadline: time.sleep(.02)\n"
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner._run_nextflow, command=[sys.executable, "-c", child],
                             task_id="task", output_dir=output, runner_log=output / "runner.log")
        try:
            deadline = time.monotonic() + 4
            while functional(repository)["status"] != "running" and time.monotonic() < deadline:
                time.sleep(0.02)
            assert functional(repository)["status"] == "running"
            assert repository.get_task("task")["current_step"] == "functional_annotation"
            assert not future.done()
        finally:
            (output / "release").touch()
        assert future.result(timeout=6) == 0
