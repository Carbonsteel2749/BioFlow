"""Private, id-only typed dataset registration API."""
from typing import Annotated, Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from ..workflows.input_contracts import InputContractError, FEATURE_TYPES, UNITS, NUMERIC_POLICY, VALIDATOR_CAPS


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Sample(StrictModel):
    sample_id: str
    read1: str
    read2: str


class Mag(StrictModel):
    mag_id: str
    fasta: str


class Common(StrictModel):
    processing_note: str | None = None
    confirmed: bool | None = None


class Reads(Common):
    kind: Literal['reads']
    processing_status: Literal['raw','clean','host_removed'] = 'raw'
    samples: list[Sample] = Field(min_length=1,max_length=1024)


class Contigs(Common):
    kind: Literal['contigs']
    cohort_id: str
    fasta: str


class Mags(Common):
    kind: Literal['mags']
    cohort_id: str
    mags: list[Mag] = Field(min_length=1,max_length=10000)


class Matrix(Common):
    kind: Literal['matrix']
    table: str
    feature_type: Literal['species','gene_families','KO','EC','pathway_abundance','pathway_coverage']
    unit: Literal['fraction','percent','counts','CPM','RPK','RPKM','TPM','HUMAnN_reported','coverage']
    normalization: str


class Registration(StrictModel):
    dataset_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
    spec: Annotated[Reads | Contigs | Mags | Matrix, Field(discriminator='kind')]
    origin: dict | None = None


def create_input_dataset_router(service):
    router=APIRouter(prefix='/api/input-datasets')

    @router.get('/contracts')
    def contracts():
        return {'kinds':['reads','contigs','mags','matrix'],
                'numeric_policy':NUMERIC_POLICY,'validator_caps':VALIDATOR_CAPS,
                'read_states':['raw','clean','host_removed'],'feature_types':list(FEATURE_TYPES),
                'units':list(UNITS),'matrix_columns':['sample_id','feature_id','value'],
                'required_semantics':{'reads':['processing_status','samples'], 'contigs':['cohort_id'],
                    'mags':['cohort_id','mags'], 'matrix':['feature_type','unit','normalization']},
                'external_processed_reads':{'required':['processing_note','confirmed'], 'trust':'user_declared_unverified'}}

    @router.post('/register',status_code=201)
    def register(body:Registration):
        try:return service.register(body.dataset_id,body.spec.model_dump(exclude_unset=True),origin=body.origin)
        except InputContractError as exc:
            conflict='不可变' in str(exc) or '冲突' in str(exc)
            raise HTTPException(409 if conflict else 422,{'code':'input_immutable' if conflict else 'input_contract_invalid',
                'message':'输入不可变，请新建数据集' if conflict else str(exc)[:512]}) from exc
        except OSError as exc:raise HTTPException(409,'输入文件不可读取') from exc

    @router.get('/{dataset_id}')
    def get(dataset_id:str):
        from ..workflows.input_contracts import SAFE_ID
        if not SAFE_ID.fullmatch(dataset_id):raise HTTPException(422,'数据集标识无效')
        if not (service.root/dataset_id/'contract.json').is_file():raise HTTPException(404,'数据集不存在')
        try:return {**service.metadata(dataset_id),'file_integrity':{'state':'not_rechecked',
            'verified_at':'registration','full_verification_required_before_execution':True}}
        except (InputContractError,OSError) as exc:raise HTTPException(409,'数据集完整性校验失败') from exc
    return router
