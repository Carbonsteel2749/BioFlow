import asyncio
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..services.resumable_uploads import UploadConflictError, UploadSpaceError
from ..services.tasks import TaskValidationError


class CreateUploadSession(BaseModel):
    model_config = ConfigDict(extra='forbid')
    original_name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1, strict=True)
    chunk_sha256: list[Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]] = Field(min_length=1, max_length=16384)
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')


def create_resumable_upload_router(service):
    router = APIRouter(prefix='/api/uploads/sessions')
    receivers = asyncio.Semaphore(4)

    def invoke(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except KeyError:
            raise HTTPException(404, 'upload session unavailable')
        except UploadConflictError as exc:
            raise HTTPException(409, str(exc))
        except (UploadSpaceError, OSError) as exc:
            raise HTTPException(507, str(exc))
        except (TaskValidationError, ValueError) as exc:
            raise HTTPException(422, str(exc))

    @router.get('/config')
    def config():
        return service.config()

    @router.post('', status_code=201)
    def create(payload: CreateUploadSession):
        return invoke(service.create, **payload.model_dump())

    @router.get('/{session_id}')
    def get(session_id: str, x_upload_token: str = Header(default='')):
        return invoke(service.get, session_id, x_upload_token)

    @router.put('/{session_id}/chunks/{index}')
    async def put(session_id: str, index: int, request: Request, x_upload_token: str = Header(default='')):
        limit = invoke(service.chunk_size, session_id, x_upload_token, index)
        async with receivers:
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > limit:
                    raise HTTPException(413, 'chunk exceeds expected size')
                data.extend(chunk)
            # Bounded memory and concurrency; no lifecycle lock on network IO.
            from starlette.concurrency import run_in_threadpool
            return await run_in_threadpool(invoke, service.put, session_id, x_upload_token, index, data)

    @router.post('/{session_id}/complete')
    def complete(session_id: str, x_upload_token: str = Header(default='')):
        return invoke(service.complete, session_id, x_upload_token)

    @router.post('/{session_id}/park')
    def park(session_id: str, x_upload_token: str = Header(default='')):
        return invoke(service.park, session_id, x_upload_token)

    @router.post('/{session_id}/resume')
    def resume(session_id: str, x_upload_token: str = Header(default='')):
        return invoke(service.resume, session_id, x_upload_token)

    return router
