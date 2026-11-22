"""Shared, versioned contracts for table-first BioFlow research."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = 'bioflow.research.v1'

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class DataOptions(Strict):
    role: Literal['taxonomy','functional','metadata','results','table']
    unit: Literal['auto','fraction','percent','counts','other'] = 'auto'
    transpose: bool = False
    sample_id_rule: Literal['none','strip_combined'] = 'none'
    sheet: str | None = None
    id_column: str | None = None
    header_row: int = Field(default=1, ge=1, le=1000)
    delimiter: Literal['auto','tab','comma','semicolon'] = 'auto'
    encoding: Literal['auto','utf-8-sig','gb18030'] = 'auto'
    description: str = Field(default='', max_length=4000)

class RegisterData(Strict):
    asset_id: str = Field(min_length=1, max_length=100)
    options: DataOptions

class PlanRequest(Strict):
    objective: str = Field(min_length=1, max_length=8000)
    dataset_ids: list[str] = Field(min_length=1, max_length=40)
    mode: Literal['manual','ai'] = 'manual'
    steps: list[dict] = Field(default_factory=list, max_length=30)
    study: dict = Field(default_factory=dict)

class EditPlan(Strict):
    objective: str = Field(min_length=1, max_length=8000)
    steps: list[dict] = Field(min_length=1, max_length=30)
    study: dict = Field(default_factory=dict)

class ConfirmPlan(Strict):
    fingerprint: str = Field(min_length=64, max_length=64)
