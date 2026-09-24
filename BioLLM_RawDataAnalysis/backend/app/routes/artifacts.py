from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..services.artifacts import (
    ArtifactIntegrityError,
    ArtifactService,
    ArtifactValidationError,
)
from ..services.stage_figures import publish_stage_figures, figure_notices


def create_artifact_router(
    service: ArtifactService,
    task_exists: Callable[[str], bool],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/tasks/{task_id}/artifacts")
    def list_artifacts(
        task_id: str,
        artifact_type: str | None = Query(default=None),
        producer: str | None = Query(default=None),
        sample_scope: str | None = Query(default=None),
    ) -> list[dict]:
        if not task_exists(task_id):
            raise HTTPException(status_code=404, detail="task not found")
        try:
            publish_stage_figures(service, task_id)
            return service.list_for_task(
                task_id,
                artifact_type=artifact_type,
                producer=producer,
                sample_scope=sample_scope,
            )
        except ArtifactValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/api/tasks/{task_id}/figure-catalog")
    def figure_catalog(task_id: str) -> list[dict]:
        if not task_exists(task_id):
            raise HTTPException(status_code=404, detail="task not found")
        publish_stage_figures(service, task_id)
        return service.list_for_task(task_id, figures_only=True)

    @router.get('/api/tasks/{task_id}/figure-notices')
    def notices(task_id: str):
        if not task_exists(task_id):
            raise HTTPException(status_code=404, detail='task not found')
        return figure_notices(service, task_id)

    @router.get("/api/artifacts/{artifact_id}")
    def artifact_detail(artifact_id: str) -> dict:
        artifact = service.get(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return artifact

    @router.get("/api/artifacts/{artifact_id}/download")
    def download_artifact(artifact_id: str):
        artifact = service.get(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        try:
            path = service.resolve_download(artifact_id)
        except ArtifactValidationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ArtifactIntegrityError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FileResponse(
            path,
            filename=artifact["file_name"],
            media_type=artifact["media_type"],
            headers={"X-Content-Type-Options": "nosniff"},
        )

    return router
