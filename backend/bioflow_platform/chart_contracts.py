from typing import Literal
from pydantic import BaseModel,Field,ConfigDict,field_validator,model_validator

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
    series_palette:Literal['tab20','Set2','Dark2','Paired']='tab20'
    pca_log1p:bool=False
    pca_show_labels:bool=False
    pca_point_size:float=Field(default=45,ge=5,le=250)
    stacked_level:Literal['sample','group']='group'
    stacked_top_n:int=Field(default=10,ge=1,le=20)
    stacked_normalize:bool=True
    shannon_show_band:bool=True
    shannon_band_alpha:float=Field(default=.18,ge=0,le=.6)
    shannon_line_width:float=Field(default=2,ge=.5,le=6)
    shannon_log_depth:bool=False
    shannon_show_points:bool=False
    enrichment_comparison:str=Field(default='',max_length=120)
    enrichment_facet:Literal['none','direction']='none'
    enrichment_palette:Literal['paper','viridis','RdYlBu_r','YlGnBu','magma','cividis']='paper'
    enrichment_color:Literal['pvalue','neg_log10']='pvalue'
    enrichment_size_title:str=Field(default='',max_length=100)
    enrichment_size_ticks:list[float]|None=Field(default=None,min_length=1,max_length=8)
    enrichment_bubble_min:float=Field(default=20,ge=1,le=800)
    enrichment_bubble_max:float=Field(default=320,ge=10,le=1600)
    @model_validator(mode='after')
    def enrichment_values(self):
        import math
        if self.enrichment_bubble_min>=self.enrichment_bubble_max:raise ValueError('气泡基础面积必须小于最大面积')
        ticks=self.enrichment_size_ticks
        if ticks is not None:
            if any(not math.isfinite(v) or v<=0 for v in ticks) or ticks!=sorted(set(ticks)):raise ValueError('大小图例刻度必须为递增、不重复的正数')
            if self.enrichment_size=='ratio' and max(ticks)>1:raise ValueError('GeneRatio 图例刻度不能大于 1')
        return self
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
