"""Bridge the private Docker host interface to loopback-only Ollama."""
import argparse,http.client,json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
ALLOWED={'/api/tags','/api/show','/api/chat','/api/embed','/api/embeddings','/api/generate'}
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def forward(self):
  if self.path not in ALLOWED:self.send_error(404);return
  size=int(self.headers.get('Content-Length','0'))
  if size>8*1024**2:self.send_error(413);return
  body=self.rfile.read(size) if size else None
  if body and self.path=='/api/chat':
   try:
    payload=json.loads(body)
    if str(payload.get('model','')).startswith('qwen3:14b'):
     payload['think']=False;options=payload.setdefault('options',{});options.setdefault('num_predict',512);options.setdefault('num_thread',8);body=json.dumps(payload).encode()
   except (ValueError,TypeError):pass
  upstream=http.client.HTTPConnection('127.0.0.1',11434,timeout=240)
  try:
   upstream.request(self.command,self.path,body,{'Content-Type':'application/json'});response=upstream.getresponse()
   self.send_response(response.status);self.send_header('Content-Type',response.getheader('Content-Type','application/json'));self.end_headers()
   while True:
    chunk=response.read(65536)
    if not chunk:break
    self.wfile.write(chunk)
  except (ConnectionError,TimeoutError,OSError):
   try:self.send_error(502,'Model service unavailable')
   except OSError:pass
  finally:upstream.close()
 do_GET=forward
 do_POST=forward
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--bind',default='172.17.0.1');p.add_argument('--port',type=int,default=11435);args=p.parse_args();ThreadingHTTPServer((args.bind,args.port),Handler).serve_forever()
