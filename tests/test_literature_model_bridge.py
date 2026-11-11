import http.client
import io,json,threading
import pytest
from http.server import ThreadingHTTPServer
from deploy import literature_model_bridge as bridge

@pytest.fixture
def client(monkeypatch):
    original=http.client.HTTPConnection;calls=[]
    class Response:
        status=200
        def __init__(self):self.body=io.BytesIO(b'{"ok":true}')
        def getheader(self,name,default=None):return 'application/json'
        def read(self,n):return self.body.read(n)
    class Upstream:
        def __init__(self,*args,**kwargs):pass
        def request(self,method,path,body,headers):calls.append((method,path,body))
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr(bridge.http.client,'HTTPConnection',Upstream)
    server=ThreadingHTTPServer(('127.0.0.1',0),bridge.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def send(path,body=None,headers=None):
        c=original('127.0.0.1',server.server_port,timeout=3);c.request('POST' if body is not None else 'GET',path,body,headers or {});r=c.getresponse();status=r.status;data=r.read();c.close();return status,data
    yield send,calls
    server.shutdown();server.server_close();thread.join(timeout=3)

def test_bridge_limits_paths_and_request_size(client):
    send,calls=client
    assert send('/api/pull')[0]==404 and calls==[]
    assert send('/api/chat',b'',{'Content-Length':str(8*1024**2+1)})[0]==413 and calls==[]

def test_qwen_simple_output_policy_preserves_user_messages(client):
    send,calls=client
    message={'role':'user','content':'Test question'}
    assert send('/api/chat',json.dumps({'model':'qwen3:14b','messages':[message],'think':True}).encode())[0]==200
    payload=json.loads(calls[-1][2]);assert payload['messages']==[message]
    assert payload['think'] is False and payload['options']['num_predict']==512 and payload['options']['num_thread']==8
