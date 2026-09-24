from fastapi import APIRouter, HTTPException, Query

from ..services.tasks import TaskValidationError


def create_dataset_router(service):
    router = APIRouter()

    @router.get('/api/datasets')
    def datasets(q: str = Query('', max_length=200), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        return service.list(q=q, limit=limit, offset=offset)

    @router.get('/api/datasets-unpaired-uploads')
    def pending(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        return service.unpaired_uploads(limit=limit, offset=offset)

    @router.get('/api/datasets/{dataset_id}')
    def detail(dataset_id: str, sample_offset: int = Query(0, ge=0), task_offset: int = Query(0, ge=0), sample_q: str = Query('', max_length=128)):
        try:
            return service.get(dataset_id, sample_offset=sample_offset, task_offset=task_offset, sample_q=sample_q)
        except KeyError:
            raise HTTPException(404, '数据集不存在')

    @router.post('/api/datasets/{dataset_id}/reuse')
    def reuse(dataset_id: str):
        try:
            return service.reuse(dataset_id)
        except KeyError:
            raise HTTPException(404, '数据集不存在')
        except TaskValidationError as exc:
            raise HTTPException(409, str(exc))

    return router
