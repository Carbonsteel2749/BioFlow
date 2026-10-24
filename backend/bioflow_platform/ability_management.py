"""Management metadata augments real model/skill discovery; no fake availability."""
from fastapi import HTTPException,Request
from pydantic import BaseModel,Field,ConfigDict
from typing import Literal
from .charts.registry import CHARTS,VERSION

def enabled(store,kind,identifier):
    with store.connect() as db:r=db.execute('SELECT enabled FROM ability_settings WHERE kind=? AND id=?',(kind,identifier)).fetchone()
    return bool(r['enabled']) if r else True

def augment(store,kind,items):
    with store.connect() as db:settings={r['id']:dict(r) for r in db.execute('SELECT * FROM ability_settings WHERE kind=?',(kind,))}
    return [{**entry,**{k:v for k,v in settings.get(entry.get('id',entry.get('name')),{}).items() if k not in {'kind','id'} and (v!='' or k=='enabled')}} for entry in items]

class AbilityBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:bool=True
    category:str=Field(default='',max_length=80)
    description:str=Field(default='',max_length=1000)

def install(app,get_store):
    @app.patch('/api/library/abilities/{kind}/{identifier}')
    def configure(kind:Literal['models','skills'],identifier:str,body:AbilityBody,request:Request):
        if request.state.user['role']!='developer':raise HTTPException(403,'能力配置由开发者管理')
        if kind=='skills':
            from bioflow_skills import list_catalog
            known={s.name for s in list_catalog()}|{'chart:'+c['id'] for c in CHARTS}|{'chart:metagenome'}
            if identifier not in known:raise HTTPException(404,'能力未登记')
        elif identifier not in {'writing-model','literature-chat','literature-embedding'} and not identifier.startswith('local-'):raise HTTPException(404,'模型未登记')
        with get_store().connect() as db:db.execute('INSERT INTO ability_settings(kind,id,enabled,category,description) VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET enabled=excluded.enabled,category=excluded.category,description=excluded.description',
            (kind,identifier,int(body.enabled),body.category,body.description))
        return {'ok':True}

def chart_skills():
    return [{'name':'chart:metagenome','display_name':'宏基因组绘图','description':'物种组成、多样性、PCoA 与功能图表；使用已有分析任务结果。','category':'visualization','version':VERSION,'input_keys':['task_id'],'chart_id':'legacy'}]+[{'name':'chart:'+c['id'],'display_name':c['name'],'description':c['description'],'category':'visualization','version':VERSION,'input_keys':['source_id','parameters'],
             'outputs':['svg','png','pdf'],'chart_id':c['id'],'execution':'project_chart'} for c in CHARTS]
