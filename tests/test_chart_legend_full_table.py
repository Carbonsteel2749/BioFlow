import hashlib,json
import pytest
from fastapi import FastAPI,APIRouter
from fastapi.testclient import TestClient
from backend.bioflow_platform.chart_workbench import install_workbench


def test_full_table_includes_rows_after_preview_window(tmp_path):
 p=tmp_path/'matrix.csv';p.write_text('sample,x\n'+''.join(f'S{i},{i}\n' for i in range(215)))
 app=FastAPI()
 @app.middleware('http')
 async def auth(request,call_next):request.state.user={'id':'u'};return await call_next(request)
 router=APIRouter(prefix='/p/{pid}');install_workbench(router,lambda:None,lambda p,r:None,lambda p,u,s:({'id':'x','simulated':True},p0),lambda *a:None,lambda *a:None);p0=p;app.include_router(router);client=TestClient(app)
 small=client.get('/p/test/chart-table?source_id=x').json();assert len(small['rows'])==30 and small['more']
 full=client.get('/p/test/chart-table?source_id=x&full=true').json();assert len(full['rows'])==215 and full['rows'][-1]['sample']=='S214' and not full['more']


def test_legend_order_visibility_color_and_custom_export(tmp_path):
 pytest.importorskip('matplotlib')
 from backend.bioflow_platform.chart_runner import render
 data=b'sample,taxon,abundance\nS1,AlphaTaxon,2\nS1,BetaTaxon,3\nS2,AlphaTaxon,3\nS2,BetaTaxon,1\n';(tmp_path/'source.csv').write_bytes(data)
 custom='custom:'+'a'*32;p={'stacked_level':'sample','legend_order':['taxon:BetaTaxon','taxon:AlphaTaxon',custom],'legend_hidden':['taxon:AlphaTaxon'],'legend_labels':{'taxon:BetaTaxon':'Edited Beta'},'series_colors':{'taxon:BetaTaxon':'#123456'},'extra_legends':[{'id':custom,'label':'Annotation','color':'#abcdef'}]}
 spec={'chart':'stacked','source':{'sha256':hashlib.sha256(data).hexdigest()},'parameters':p};(tmp_path/'request.json').write_text(json.dumps(spec));render(tmp_path)
 svg=(tmp_path/'figure.svg').read_text();assert 'Edited Beta' in svg and 'Annotation' in svg and 'AlphaTaxon' not in svg and '#123456' in svg and '#abcdef' in svg
 result=json.loads((tmp_path/'result.json').read_text());assert result['rows']==4 and len(result['legend'])==3
 assert {s['id'] for s in result['legend']}=={'taxon:BetaTaxon','taxon:AlphaTaxon',custom}
