"""Thin service client. No browser cookies, private algorithm copies or implicit execution."""
import hashlib
import io
import json
import urllib.error
import urllib.parse
import urllib.request
import zipfile


class IntegrationClient:
    def __init__(self, base_url, token, timeout=30):
        self.base_url=base_url.rstrip('/')+'/api/v1/integration'
        self.token=token
        self.timeout=timeout

    def request(self, path, payload=None, method=None, binary=False, expected_fingerprint=None):
        data=json.dumps(payload).encode() if payload is not None else None
        req=urllib.request.Request(self.base_url+path,data=data,method=method or ('POST' if payload is not None else 'GET'),
                                   headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        if expected_fingerprint:req.add_header('X-BioFlow-Plan-Fingerprint',expected_fingerprint)
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as response:
                raw=response.read(50*1024**2+1)
        except urllib.error.HTTPError as exc:
            error=exc.read(10000).decode('utf-8','replace')
            raise RuntimeError(f'Integration HTTP {exc.code}: {error}') from None
        if len(raw)>50*1024**2:raise ValueError('Response exceeds client limit')
        return raw if binary else json.loads(raw)

    def catalog(self):return self.request('/catalog')
    def resolve(self, revision_id):return self.request('/resolve/'+urllib.parse.quote(revision_id,safe=''))
    def preflight(self, node, payload):return self.request('/nodes/'+urllib.parse.quote(node,safe='')+'/preflight',payload)
    def run(self, node, payload, expected_fingerprint=None):return self.request('/nodes/'+urllib.parse.quote(node,safe='')+'/runs',payload,expected_fingerprint=expected_fingerprint)
    def task(self, key):return self.request('/runs/'+urllib.parse.quote(key,safe=''))
    def cancel(self, key):return self.request('/runs/'+urllib.parse.quote(key,safe='')+'/cancel',{})
    def resume(self, key):return self.request('/runs/'+urllib.parse.quote(key,safe='')+'/resume',{})
    def result(self, key, path):return self.request('/runs/'+urllib.parse.quote(key,safe='')+'/files?'+urllib.parse.urlencode({'path':path}),binary=True)

    def source_package(self, revision_id):
        manifest=self.resolve(revision_id)
        raw=self.request('/packages/'+urllib.parse.quote(revision_id,safe=''),binary=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            expected={f['path']:f for f in manifest['files']}
            names=z.namelist()
            if len(names)!=len(set(names)) or set(names)-{'.bioflow-export.json'}!=set(expected):raise ValueError('Package file list mismatch')
            if sum(x.file_size for x in z.infolist())>200*1024**2:raise ValueError('Package exceeds unpacked limit')
            digest=hashlib.sha256()
            for name,f in sorted(expected.items()):
                data=z.read(name)
                if len(data)!=f['size'] or hashlib.sha256(data).hexdigest()!=f['sha256']:raise ValueError('Package file integrity mismatch')
                digest.update(name.encode()+b'\0'+f['sha256'].encode()+b'\n')
            if digest.hexdigest()!=manifest['content_hash']:raise ValueError('Package hash mismatch')
        return raw,manifest
