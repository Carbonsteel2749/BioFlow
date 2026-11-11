import asyncio
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from backend.bioflow_platform import access

class Store:
    def owns(self,kind,eid,uid):return (kind,eid,uid)==('kb','owned','reader')

def request(kb):
    async def body():return {'knowledgeBaseId':kb,'messages':[{'role':'user','content':'question'}]}
    return SimpleNamespace(state=SimpleNamespace(user={'id':'reader','role':'user'}),method='POST',json=body)

def test_reader_qa_is_limited_to_owned_knowledge_base():
    asyncio.run(access.authorize(Store(),'literature','v1/chat/completions',request('owned'),{}))
    with pytest.raises(HTTPException) as error:asyncio.run(access.authorize(Store(),'literature','v1/chat/completions',request('foreign'),{}))
    assert error.value.status_code==404
    with pytest.raises(HTTPException) as error:asyncio.run(access.authorize(Store(),'literature','v1/chat/completions',request(''),{}))
    assert error.value.status_code==400
