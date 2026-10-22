from fastapi import APIRouter, HTTPException

from ..services.artifacts import ArtifactIntegrityError
from ..services.figure_contracts import FigureRequest


def create_figure_router(service):
    router=APIRouter()

    def invoke(callback, *args):
        try:
            return callback(*args)
        except KeyError as exc:
            raise HTTPException(404,'任务或绘图版本不存在') from exc
        except (ValueError,ArtifactIntegrityError) as exc:
            raise HTTPException(409,str(exc)) from exc
        except (OSError,UnicodeError) as exc:
            raise HTTPException(409,'绘图文件不可用，请核对任务产物') from exc

    @router.get('/api/tasks/{task_id}/figures/options')
    def options(task_id:str):
        return invoke(service.catalog,task_id)

    @router.get('/api/tasks/{task_id}/figures/jobs')
    def jobs(task_id:str):
        return invoke(service.list,task_id)

    @router.post('/api/tasks/{task_id}/figures/jobs',status_code=201)
    def create(task_id:str,request:FigureRequest):
        return invoke(service.create,task_id,request.model_dump())

    @router.get('/api/tasks/{task_id}/figures/jobs/{job_id}')
    def job(task_id:str,job_id:str):
        return invoke(service.get,task_id,job_id)

    @router.get('/api/tasks/{task_id}/figures/jobs/{job_id}/artifacts')
    def files(task_id:str,job_id:str):
        job=invoke(service.get,task_id,job_id)
        return [service.artifacts.get(identifier) for identifier in job['result'].get('artifact_ids',[])]

    @router.post('/api/tasks/{task_id}/figures/jobs/{job_id}/cancel')
    def cancel(task_id:str,job_id:str):
        return invoke(service.cancel,task_id,job_id)

    @router.post('/api/tasks/{task_id}/figures/jobs/{job_id}/select')
    def select(task_id:str,job_id:str):
        return invoke(service.select,task_id,job_id)

    @router.post('/api/tasks/{task_id}/figures/archive',status_code=201)
    def archive(task_id:str):
        return invoke(service.refresh_archive,task_id)

    return router
