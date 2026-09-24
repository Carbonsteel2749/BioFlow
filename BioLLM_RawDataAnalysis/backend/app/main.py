from fastapi import FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from .artifacts import ArtifactRepository
from .services.interpretation import InterpretationService, create_interpretation_router
from .services.log_cleanup import LogCleanupService, create_log_cleanup_router
from .config import Settings
from .models import TaskRepository
from .routes.artifacts import create_artifact_router
from .routes.datasets import create_dataset_router
from .services.datasets import DatasetService
from .services.results_catalog import ResultsCatalog, create_results_router
from .routes.workflows import create_workflow_router
from .schemas import (
    CreateTaskRequest,
    CreateUploadedManifestRequest,
    RetryDecisionRequest,
    RenameTaskRequest,
)
from .services.artifacts import ArtifactService
from .services.previews import MULTIQC_CSP, PreviewService
from .services.runner import TaskRunner
from .services.tasks import TaskService, TaskValidationError
from .services.task_cleanup import TaskCleanupService, create_cleanup_router
from .services.workflow_ai import WorkflowAIService
from .services.workflow_templates import WorkflowTemplateService
from .services.workflow_runs import DynamicWorkflowRunner, WorkflowRunService
from .services.uploads import UploadService
from .workflows.state import WorkflowRunRepository


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.prepare()
    repository = TaskRepository(settings.database_path)
    repository.initialize()
    repository.pause_inflight_tasks()
    service = TaskService(settings, repository)
    runner = TaskRunner(settings, repository)
    upload_service = UploadService(settings, repository)
    preview_service = PreviewService(settings, repository)
    workflow_template_service = WorkflowTemplateService(
        settings.state_root / "workflow_state",
        settings.input_root,
    )
    workflow_ai_service = WorkflowAIService(
        settings.state_root / "workflow_ai_proposals",
        registry=workflow_template_service.registry,
        base_url=settings.ollama_url,
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )
    workflow_run_repository = WorkflowRunRepository(settings.database_path)
    workflow_run_repository.initialize()
    workflow_run_service = WorkflowRunService(
        settings,
        service,
        workflow_run_repository,
        registry=workflow_template_service.registry,
    )
    dynamic_workflow_runner = DynamicWorkflowRunner(
        settings,
        workflow_run_repository,
    )
    artifact_repository = ArtifactRepository(settings.database_path)
    artifact_repository.initialize()
    artifact_service = ArtifactService(
        artifact_repository,
        settings.state_root / "outputs",
    )
    dataset_service = DatasetService(settings, repository)
    dataset_service.initialize()

    def task_exists(task_id: str) -> bool:
        return (
            service.get_task(task_id) is not None
            or workflow_run_repository.get_run(task_id) is not None
        )

    app = FastAPI(title="BioLLM Metagenomics API", version="0.1.0")
    app.state.settings = settings
    app.state.repository = repository
    app.state.service = service
    app.state.runner = runner
    app.state.upload_service = upload_service
    app.state.preview_service = preview_service
    app.state.workflow_template_service = workflow_template_service
    app.state.workflow_ai_service = workflow_ai_service
    app.state.workflow_run_repository = workflow_run_repository
    app.state.workflow_run_service = workflow_run_service
    app.state.dynamic_workflow_runner = dynamic_workflow_runner
    app.state.artifact_repository = artifact_repository
    app.state.artifact_service = artifact_service
    app.state.dataset_service = dataset_service
    app.include_router(create_dataset_router(dataset_service))
    app.include_router(create_results_router(ResultsCatalog(repository, artifact_service)))
    app.include_router(create_artifact_router(artifact_service, task_exists))
    app.include_router(create_interpretation_router(InterpretationService(settings, repository)))
    cleanup_service = TaskCleanupService(settings, repository, runner)
    app.include_router(create_cleanup_router(cleanup_service))
    app.include_router(create_log_cleanup_router(LogCleanupService(cleanup_service)))
    app.include_router(
        create_workflow_router(
            workflow_template_service,
            workflow_ai_service,
            workflow_run_service,
            dynamic_workflow_runner,
            auto_run=settings.auto_run,
        )
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/capabilities")
    def capabilities() -> dict:
        return service.capabilities()

    @app.post("/api/uploads/files", status_code=status.HTTP_201_CREATED)
    def upload_fastq(file: UploadFile = File(...)) -> dict:
        try:
            return upload_service.store(file.filename, file.file)
        except TaskValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            file.file.close()

    @app.post("/api/uploads/manifests", status_code=status.HTTP_201_CREATED)
    def create_uploaded_manifest(request: CreateUploadedManifestRequest) -> dict:
        try:
            manifest, sample_count = upload_service.create_manifest(request.files)
        except TaskValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        dataset_id = dataset_service.register_manifest(manifest, source='uploaded', name=request.dataset_name)
        return {"manifest_path": str(manifest), "sample_count": sample_count, "dataset_id": dataset_id}

    @app.post("/api/tasks", status_code=status.HTTP_201_CREATED)
    def create_task(request: CreateTaskRequest) -> dict:
        try:
            if request.dataset_id:
                try:
                    selected = dataset_service.reuse(request.dataset_id)
                except KeyError as exc:
                    raise TaskValidationError('数据集不存在') from exc
                if selected['manifest_path'] != request.manifest_path:
                    raise TaskValidationError('数据集与样本清单不匹配')
            task = service.create_task(
                request.manifest_path,
                request.parameters.model_dump(mode="json"),
            )
            if request.name is not None:
                repository.update_task(task['id'], name=request.name)
                task = service.get_task(task['id'])
        except TaskValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        dataset_service.register_manifest(task['manifest_path'])
        if settings.auto_run:
            runner.submit(task["id"])
        return task

    @app.get("/api/tasks")
    def list_tasks() -> list[dict]:
        return service.list_tasks()

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        task = service.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    @app.get("/api/tasks/{task_id}/logs")
    def get_log(task_id: str, step: str = Query(...)) -> dict[str, str]:
        try:
            text = service.read_log(task_id, step)
        except TaskValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="log not found") from exc
        return {"step": step, "text": text}

    @app.patch('/api/tasks/{task_id}')
    def rename_task(task_id: str, request: RenameTaskRequest) -> dict:
        with repository._connect() as connection:
            if not connection.execute('UPDATE tasks SET name=? WHERE id=?', (request.name, task_id)).rowcount:
                raise HTTPException(404, 'task not found')
        return service.get_task(task_id)

    @app.post("/api/tasks/{task_id}/retry")
    def retry_task(task_id: str) -> dict:
        try:
            task = service.retry_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if settings.auto_run:
            runner.submit(task_id)
        return task

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str) -> dict:
        try:
            task = service.cancel_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        runner.cancel(task_id)
        return task

    @app.post("/api/tasks/{task_id}/retry-decision")
    def set_retry_decision(task_id: str, request: RetryDecisionRequest) -> dict:
        try:
            return service.set_retry_decision(task_id, request.allowed, request.reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/tasks/{task_id}/preview")
    def result_preview(task_id: str, sample_id: str | None = None) -> dict:
        try:
            return preview_service.preview(task_id, sample_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="result preview is not available"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/tasks/{task_id}/reports/multiqc")
    def multiqc_report(task_id: str):
        try:
            report = preview_service.multiqc_report(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="MultiQC report is not available"
            ) from exc
        return FileResponse(
            report,
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Security-Policy": MULTIQC_CSP,
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/tasks/{task_id}/results")
    def download_result(task_id: str):
        try:
            archive = service.result_archive(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="result archive is not available") from exc
        except TaskValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return FileResponse(archive, filename=archive.name, media_type="application/gzip")

    return app
