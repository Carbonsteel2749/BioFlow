from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..services.resource_scheduler import ResourceScheduler
from ..services.resource_preflight import MAX_HINT_BYTES, ResourcePreflight
from ..services.tasks import TaskValidationError
from ..config import Settings


class ResourceLimitRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    max_concurrent_tasks: int = Field(ge=1, le=16, strict=True)
    confirm_resource_risk: bool = False


def create_resource_router(scheduler: ResourceScheduler, settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix='/api/resources', tags=['resources'])

    if settings is not None:
        @router.get('/preflight')
        def preflight(manifest_path: str | None = Query(default=None, max_length=4096),
                      threads: int | None = Query(default=None, ge=1),
                      enable_mags: bool = False,
                      input_bytes: int | None = Query(default=None, ge=0, le=MAX_HINT_BYTES)):
            try:
                report = ResourcePreflight(settings).inspect(manifest_path=manifest_path,
                    threads=threads, enable_mags=enable_mags, input_bytes=input_bytes)
                cpu = scheduler.cpu_headroom(maximum=report['cpu']['max_threads'])
                report['cpu'].update(cpu)
                remaining = cpu['suggested_remaining_threads']
                if remaining is None:
                    report['warnings'].append({'code':'cpu_usage_unknown', 'message':'Some running tasks have unknown thread configurations; CPU headroom cannot be estimated.'})
                elif threads is not None and threads > remaining:
                    report['warnings'].append({'code':'threads_competition', 'message':'Requested threads exceed the suggested headroom; concurrent tasks may compete for CPU.'})
                return report
            except (TaskValidationError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get('/limits')
    def limits():
        return scheduler.snapshot()

    @router.put('/limits')
    def update_limits(request: ResourceLimitRequest):
        try:
            return scheduler.configure(request.max_concurrent_tasks,
                confirm_resource_risk=request.confirm_resource_risk)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
