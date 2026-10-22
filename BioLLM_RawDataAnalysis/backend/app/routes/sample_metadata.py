from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..services.sample_metadata import MetadataConflictError


class SampleGroup(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sample_id: str = Field(min_length=1, max_length=128)
    group: str = Field(max_length=128)


class ReplaceMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rows: list[SampleGroup] = Field(max_length=10000)
    expected_version: int = Field(ge=0, strict=True)


class ImportMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(max_length=5 * 1024 * 1024)
    format: Literal['csv', 'tsv']
    expected_version: int = Field(ge=0, strict=True)


class PreviewMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rows: list[SampleGroup] | None = Field(default=None, max_length=10000)
    text: str | None = Field(default=None, max_length=5 * 1024 * 1024)
    format: Literal['csv', 'tsv'] | None = None
    expected_version: int = Field(ge=0, strict=True)


def create_sample_metadata_router(service):
    router = APIRouter()

    def invoke(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except KeyError:
            raise HTTPException(404, '数据集不存在')
        except MetadataConflictError as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @router.get('/api/datasets/{dataset_id}/sample-metadata')
    def get_metadata(dataset_id: str):
        return invoke(service.get, dataset_id)

    @router.put('/api/datasets/{dataset_id}/sample-metadata')
    def replace_metadata(dataset_id: str, payload: ReplaceMetadata):
        return invoke(service.replace, dataset_id, [row.model_dump() for row in payload.rows], payload.expected_version)

    @router.post('/api/datasets/{dataset_id}/sample-metadata/import')
    def import_metadata(dataset_id: str, payload: ImportMetadata):
        return invoke(service.import_text, dataset_id, payload.text, payload.format, payload.expected_version)

    @router.post('/api/datasets/{dataset_id}/sample-metadata/validate')
    def validate_metadata(dataset_id: str, payload: PreviewMetadata):
        return invoke(service.preview, dataset_id, payload.expected_version,
                      rows=[row.model_dump() for row in payload.rows] if payload.rows is not None else None,
                      text=payload.text, format=payload.format)

    return router
