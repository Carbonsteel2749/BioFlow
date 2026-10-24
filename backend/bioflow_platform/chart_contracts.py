from typing import Literal
from pydantic import BaseModel,Field,ConfigDict,field_validator

class DisplayParameters(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str|None=Field(default=None,max_length=300)
    x_label:str|None=Field(default=None,max_length=200)
    y_label:str|None=Field(default=None,max_length=200)
    legend_labels:dict[str,str]=Field(default_factory=dict,max_length=80)
    legend_order:list[str]=Field(default_factory=list,max_length=80)
    legend_position:Literal['best','upper right','upper left','lower right','lower left','outside_right','outside_bottom']='best'
    standardize:bool=False
    width:float=Field(default=9,ge=4,le=20)
    height:float=Field(default=6,ge=3,le=16)
    font_size:float=Field(default=10,ge=6,le=24)
    enrichment_x:Literal['ratio','count']='ratio'
    enrichment_size:Literal['count','ratio']='count'
    @field_validator('legend_labels')
    @classmethod
    def labels(cls,value):
        if any(len(k)>240 or len(v)>200 or not v.strip() for k,v in value.items()):raise ValueError('图例名称应为 1–200 字符')
        return value
    @field_validator('legend_order')
    @classmethod
    def order(cls,value):
        if len(value)!=len(set(value)) or any(len(v)>240 for v in value):raise ValueError('图例顺序不能重复')
        return value

class FigureBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    chart:str=Field(min_length=1,max_length=50)
    source_id:str=Field(default='',max_length=100)
    parent_id:str=Field(default='',max_length=100)
    columns:dict[str,str]=Field(default_factory=dict,max_length=100)
    parameters:DisplayParameters=Field(default_factory=DisplayParameters)
    @field_validator('columns')
    @classmethod
    def column_mapping(cls,value):
        if any(not k.strip() or not v.strip() or len(k)>200 or len(v)>200 for k,v in value.items()) or len(set(value.values()))!=len(value):raise ValueError('列名对应重复或格式不正确')
        return value
