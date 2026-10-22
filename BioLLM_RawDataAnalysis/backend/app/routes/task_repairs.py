from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ..models import TaskRepository
from ..services.task_repairs import (
    TaskRepairConflict,
    TaskRepairError,
    TaskRepairNotFound,
    TaskRepairService,
)


class RepairDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def create_task_repair_router(
    service: TaskRepairService,
    repository: TaskRepository,
) -> APIRouter:
    router = APIRouter()

    def require_task(task_id: str) -> dict:
        task = repository.get_task(task_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="task not found",
            )
        return task

    @router.get("/api/tasks/{task_id}/repairs")
    def repair_history(task_id: str) -> dict:
        require_task(task_id)
        try:
            return service.summary(task_id)
        except TaskRepairError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    @router.post("/api/tasks/{task_id}/repairs/{version}/decision")
    def repair_decision(
        task_id: str,
        version: int,
        request: RepairDecisionRequest,
    ) -> dict:
        task = require_task(task_id)
        if task["status"] == "cancelled" or task.get("cancel_requested"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="cancelled tasks cannot authorize repairs",
            )
        try:
            attempt = service.confirm(
                task_id,
                version,
                approved=request.approved,
                expected_plan_sha256=request.plan_sha256,
                current_parameters=task.get("parameters") or {},
            )
        except TaskRepairNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except (TaskRepairConflict, TaskRepairError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        if attempt["status"] == "authorized":
            repository.update_task(task_id, retry_allowed=True)
            repository.add_alert(
                task_id,
                "info",
                "用户已确认修复参数；仍需显式请求续跑，系统不会自行启动。",
            )
        elif attempt["status"] == "manual_action_required":
            repository.add_alert(
                task_id,
                "warning",
                "该建议涉及数据库、宿主索引或样本排除，只能由用户手工处理。",
            )
        else:
            repository.update_task(task_id, retry_allowed=False)
        return {"attempt": attempt, "summary": service.summary(task_id)}

    return router
