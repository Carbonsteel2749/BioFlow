from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ..services.workflow_ai import (
    WorkflowAIError,
    WorkflowAIResponseError,
    WorkflowAIService,
    WorkflowAIUnavailable,
    WorkflowProposalNotFoundError,
    WorkflowProposalStateError,
)
from ..services.workflow_templates import (
    ExecutionSnapshotConflictError,
    ExecutionSnapshotIntegrityError,
    TemplateNotFoundError,
    TemplateVersionConflictError,
    WorkflowTemplateError,
    WorkflowTemplateService,
)
from ..services.workflow_runs import (
    DynamicWorkflowRunner,
    WorkflowRunService,
    WorkflowRunValidationError,
)
from ..workflows.graph import WorkflowGraph
from ..workflows.registry import serialize_node_registry


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateTemplateRequest(_StrictRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    template_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )
    workflow: WorkflowGraph


class UpdateTemplateRequest(_StrictRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    base_version: int = Field(ge=1)
    workflow: WorkflowGraph


class CopyTemplateRequest(_StrictRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    source_version: int | None = Field(default=None, ge=1)
    template_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )


class ExecutionSnapshotRequest(_StrictRequest):
    task_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    )
    workflow: WorkflowGraph
    confirmed_risks: list[str] = Field(
        default_factory=list,
        max_length=256,
    )
    input_files: list[str] = Field(min_length=1, max_length=1024)
    database_profile: str = Field(min_length=1, max_length=128)
    resolved_manifest_summary: dict[str, Any]
    compilation_summary: dict[str, Any]


class WorkflowSuggestionRequest(_StrictRequest):
    template_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    )
    version: int | None = Field(default=None, ge=1)
    instruction: str = Field(min_length=1, max_length=4000)


class CreateWorkflowRunRequest(_StrictRequest):
    manifest_path: str = Field(min_length=1, max_length=4096)
    workflow: WorkflowGraph


class ProposalConfirmationRequest(_StrictRequest):
    confirmed: bool


def _template_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, TemplateNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="workflow template or snapshot not found",
        )
    if isinstance(
        exc,
        (
            TemplateVersionConflictError,
            ExecutionSnapshotConflictError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    if isinstance(exc, ExecutionSnapshotIntegrityError):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="execution snapshot integrity check failed",
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    )


def _proposal_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, WorkflowProposalNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="workflow AI proposal not found",
        )
    if isinstance(exc, WorkflowProposalStateError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    if isinstance(exc, WorkflowAIUnavailable):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "local workflow AI is unavailable; manual workflow "
                "editing and task execution remain available"
            ),
        )
    if isinstance(exc, WorkflowAIResponseError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    )


def create_workflow_router(
    template_service: WorkflowTemplateService,
    ai_service: WorkflowAIService,
    run_service: WorkflowRunService,
    run_runner: DynamicWorkflowRunner,
    *,
    auto_run: bool = True,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/workflows",
        tags=["workflows"],
    )

    @router.get("/registry")
    def node_registry() -> dict[str, Any]:
        return serialize_node_registry(template_service.registry)

    @router.post(
        "/runs",
        status_code=status.HTTP_201_CREATED,
    )
    def create_run(request: CreateWorkflowRunRequest) -> dict[str, Any]:
        try:
            run = run_service.create_run(
                request.workflow,
                request.manifest_path,
            )
        except WorkflowRunValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        if auto_run:
            run_runner.submit(str(run["task_id"]))
        return run

    @router.get("/runs/{task_id}")
    def get_run(task_id: str) -> dict[str, Any]:
        run = run_service.get_run(task_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="dynamic workflow run not found",
            )
        return run

    @router.post(
        "/runs/{task_id}/start",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_run(task_id: str) -> dict[str, Any]:
        run = run_service.get_run(task_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="dynamic workflow run not found",
            )
        if run["status"] != "queued":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="dynamic workflow run is not queued",
            )
        submitted = run_runner.submit(task_id)
        if not submitted:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="dynamic workflow run is already active",
            )
        return {"task_id": task_id, "submitted": True}

    @router.get("/runs/{task_id}/logs")
    def get_run_log(task_id: str) -> dict[str, str]:
        try:
            text = run_service.read_log(task_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="dynamic workflow run not found",
            ) from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="dynamic workflow log is not available",
            ) from exc
        return {"text": text}

    @router.get("/templates")
    def list_templates() -> list[dict[str, Any]]:
        return template_service.list_templates()

    @router.get("/templates/{template_id}")
    def get_template(
        template_id: str,
        version: int | None = Query(default=None, ge=1),
    ) -> dict[str, Any]:
        try:
            return template_service.get_template(
                template_id,
                version,
            )
        except WorkflowTemplateError as exc:
            raise _template_http_error(exc) from exc
        except TemplateNotFoundError as exc:
            raise _template_http_error(exc) from exc

    @router.post(
        "/templates",
        status_code=status.HTTP_201_CREATED,
    )
    def create_template(
        request: CreateTemplateRequest,
    ) -> dict[str, Any]:
        try:
            return template_service.save_template(
                name=request.name,
                description=request.description,
                template_id=request.template_id,
                workflow=request.workflow,
            )
        except WorkflowTemplateError as exc:
            raise _template_http_error(exc) from exc

    @router.put("/templates/{template_id}")
    def update_template(
        template_id: str,
        request: UpdateTemplateRequest,
    ) -> dict[str, Any]:
        try:
            return template_service.save_template(
                name=request.name,
                description=request.description,
                template_id=template_id,
                base_version=request.base_version,
                workflow=request.workflow,
            )
        except WorkflowTemplateError as exc:
            raise _template_http_error(exc) from exc

    @router.post(
        "/templates/{template_id}/copy",
        status_code=status.HTTP_201_CREATED,
    )
    def copy_template(
        template_id: str,
        request: CopyTemplateRequest,
    ) -> dict[str, Any]:
        try:
            return template_service.copy_template(
                template_id,
                source_version=request.source_version,
                name=request.name,
                description=request.description,
                template_id=request.template_id,
            )
        except (
            WorkflowTemplateError,
            TemplateNotFoundError,
        ) as exc:
            raise _template_http_error(exc) from exc

    @router.post(
        "/execution-snapshots",
        status_code=status.HTTP_201_CREATED,
    )
    def create_execution_snapshot(
        request: ExecutionSnapshotRequest,
    ) -> dict[str, Any]:
        try:
            return template_service.create_execution_snapshot(
                task_id=request.task_id,
                workflow=request.workflow,
                confirmed_risks=request.confirmed_risks,
                input_files=request.input_files,
                database_profile=request.database_profile,
                resolved_manifest_summary=(
                    request.resolved_manifest_summary
                ),
                compilation_summary=request.compilation_summary,
            )
        except WorkflowTemplateError as exc:
            raise _template_http_error(exc) from exc

    @router.get("/execution-snapshots/{task_id}")
    def get_execution_snapshot(
        task_id: str,
    ) -> dict[str, Any]:
        try:
            return template_service.get_execution_snapshot(task_id)
        except (
            WorkflowTemplateError,
            TemplateNotFoundError,
        ) as exc:
            raise _template_http_error(exc) from exc

    @router.post(
        "/ai/proposals",
        status_code=status.HTTP_201_CREATED,
    )
    def suggest_changes(
        request: WorkflowSuggestionRequest,
    ) -> dict[str, Any]:
        try:
            template = template_service.get_template(
                request.template_id,
                request.version,
            )
            return ai_service.suggest_changes(
                workflow=template["workflow"],
                instruction=request.instruction,
                base_reference={
                    "template_id": template["template_id"],
                    "version": template["version"],
                    "workflow_sha256": template[
                        "workflow_sha256"
                    ],
                },
            )
        except TemplateNotFoundError as exc:
            raise _template_http_error(exc) from exc
        except WorkflowAIError as exc:
            raise _proposal_http_error(exc) from exc

    @router.get("/ai/proposals/{proposal_id}")
    def get_proposal(
        proposal_id: str,
    ) -> dict[str, Any]:
        try:
            return ai_service.get_proposal(proposal_id)
        except WorkflowAIError as exc:
            raise _proposal_http_error(exc) from exc
        except WorkflowProposalNotFoundError as exc:
            raise _proposal_http_error(exc) from exc

    @router.post("/ai/proposals/{proposal_id}/confirmation")
    def confirm_proposal(
        proposal_id: str,
        request: ProposalConfirmationRequest,
    ) -> dict[str, Any]:
        try:
            if not request.confirmed:
                proposal = ai_service.reject_proposal(
                    proposal_id
                )
                return {
                    "proposal": proposal,
                    "template": None,
                }

            def apply_to_template(
                proposed_workflow: dict[str, Any],
                base_reference: dict[str, Any],
            ) -> dict[str, Any]:
                source = template_service.get_template(
                    base_reference["template_id"],
                    base_reference["version"],
                )
                if (
                    source["workflow_sha256"]
                    != base_reference["workflow_sha256"]
                ):
                    raise TemplateVersionConflictError(
                        "proposal base checksum no longer matches"
                    )
                if source["read_only"]:
                    return template_service.save_template(
                        name=source["name"] + "（AI 建议副本）",
                        description=source["description"],
                        workflow=proposed_workflow,
                        copied_from=base_reference,
                    )
                return template_service.save_template(
                    name=source["name"],
                    description=source["description"],
                    workflow=proposed_workflow,
                    template_id=source["template_id"],
                    base_version=source["version"],
                )

            proposal, template = (
                ai_service.apply_confirmed_proposal(
                    proposal_id,
                    apply_to_template,
                )
            )
            return {
                "proposal": proposal,
                "template": template,
            }
        except (
            WorkflowAIError,
            WorkflowProposalNotFoundError,
        ) as exc:
            raise _proposal_http_error(exc) from exc
        except (
            WorkflowTemplateError,
            TemplateNotFoundError,
        ) as exc:
            raise _template_http_error(exc) from exc

    return router
