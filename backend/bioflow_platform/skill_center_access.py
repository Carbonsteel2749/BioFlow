"""Account boundary for the shared Skill center. Business tables remain unchanged."""
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time

from fastapi import HTTPException


class CenterAccess:
    def __init__(self, center_database, ownership_database):
        self.center_database = Path(center_database)
        self.ownership_database = Path(ownership_database)
        self.ownership_database.parent.mkdir(parents=True, exist_ok=True)
        self.secret = secrets.token_bytes(32)
        with self.owners() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS objects(kind TEXT NOT NULL,id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(kind,id));
                CREATE INDEX IF NOT EXISTS objects_owner ON objects(owner_id,kind,created_at);
            ''')
        self.ownership_database.chmod(0o600)

    def owners(self):
        return sqlite3.connect(self.ownership_database, timeout=15)

    def rows(self, table):
        assert table in {'skills','revisions','algorithms','algorithm_versions','runs','datasets'}
        with sqlite3.connect('file:' + str(self.center_database) + '?mode=ro', uri=True, timeout=15) as db:
            return [json.loads(x[0]) for x in db.execute('SELECT body FROM ' + table)]

    def row(self, table, key):
        assert table in {'skills','revisions','algorithms','algorithm_versions','runs','datasets'}
        with sqlite3.connect('file:' + str(self.center_database) + '?mode=ro', uri=True, timeout=15) as db:
            row = db.execute('SELECT body FROM ' + table + ' WHERE id=?', (key,)).fetchone()
        if not row:
            raise HTTPException(404, '记录不存在')
        return json.loads(row[0])

    def bridge_token(self, user, total_token):
        # Only server-to-server; never sent to a browser. Each request first
        # validates the live total-system session, including role and expiry.
        token = hmac.new(self.secret, total_token.encode(), hashlib.sha256).hexdigest()
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        with sqlite3.connect(self.center_database, timeout=15) as db:
            row = db.execute('SELECT body FROM sessions WHERE id=?', (key,)).fetchone()
            if not row or json.loads(row[0])['expires_at'] < now + 60:
                db.execute("DELETE FROM sessions WHERE json_extract(body,'$.username') LIKE 'bioflow:%' AND json_extract(body,'$.expires_at') < ?", (now,))
                record = {'id':key, 'expires_at':now+300, 'username':'bioflow:'+user['id']}
                db.execute('INSERT OR REPLACE INTO sessions(id,body) VALUES(?,?)', (key,json.dumps(record)))
        return token

    def register(self, kind, key, user):
        with self.owners() as db:
            db.execute('INSERT INTO objects VALUES(?,?,?,?)', (kind,key,user['id'],time.time()))

    def require_owner(self, kind, key, user):
        if user['role'] == 'developer':
            return
        with self.owners() as db:
            row = db.execute('SELECT owner_id FROM objects WHERE kind=? AND id=?', (kind,key)).fetchone()
        if not row or row[0] != user['id']:
            raise HTTPException(404, '记录不存在或不属于当前账户')

    def published_skill(self, sid):
        row = self.row('skills',sid)
        if row.get('archived_at') or not row.get('enabled',True) or not row.get('published_revision_id'):
            raise HTTPException(404, '该 Skill 尚未发布或已停用')
        return row

    def skill_view(self, row):
        fields = {'id','skill_name','display_name','description','category','tags',
                  'applicable_modules','published_revision_id','enabled','execution_type','execution_state'}
        return {k:v for k,v in row.items() if k in fields}

    def algorithm_view(self, row):
        if row.get('archived_at') or not row.get('enabled') or not row.get('active_version_id'):
            raise HTTPException(404, '算法尚未发布或已停用')
        version = self.row('algorithm_versions', row['active_version_id'])
        if version.get('validation_status') != 'passed' or not version.get('published_at'):
            raise HTTPException(404, '算法版本尚未通过验证并发布')
        safe_version = {k:v for k,v in version.items() if k in {
            'id','algorithm_id','number','upstream_version','task','defaults','parameters',
            'validation_status','published_at','source_hash','library_source_hash'}}
        fields = {'id','name','display_name','description','category','enabled','active_version_id',
                  'input_requirements','output_description','notes'}
        return {**{k:v for k,v in row.items() if k in fields}, 'active_version':safe_version, 'versions':[safe_version]}

    def plot_allowed(self, template_id):
        matches = [r for r in self.rows('skills') if r.get('skill_name') == 'bioflow-plot-'+template_id]
        return any(not r.get('archived_at') and r.get('enabled',True) and r.get('published_revision_id') for r in matches)

    def local_read(self, path, query, user):
        """Return a safe result, or None for an authorized upstream read."""
        if user['role'] == 'developer':
            return None
        if path in {'api/v1/skills','api/v1/modules'}:
            rows = [self.skill_view(r) for r in self.rows('skills') if
                    not r.get('archived_at') and r.get('enabled',True) and r.get('published_revision_id')]
            if path.endswith('/modules'):
                return {'items':[{'id':m,'name':n,'count':sum(m in r.get('applicable_modules',[]) for r in rows)}
                                 for m,n in [('writing','文献撰写'),('analysis','数据分析与生图')]],'total':len(rows)}
            q=query.get('q','').lower();category=query.get('category');module=query.get('module')
            rows=[r for r in rows if (not q or q in json.dumps(r,ensure_ascii=False).lower()) and
                  (not category or r.get('category')==category) and (not module or module in r.get('applicable_modules',[]))]
            rows.sort(key=lambda r:r.get('display_name',r.get('skill_name','')))
            try:page=max(1,int(query.get('page','1')));size=min(100,max(1,int(query.get('page_size','24'))))
            except ValueError:raise HTTPException(422,'分页参数无效')
            return {'items':rows[(page-1)*size:page*size],'total':len(rows),'page':page,'page_size':size,
                    'categories':sorted({r.get('category','') for r in rows})}
        parts=path.split('/')
        if len(parts)>=4 and parts[:3]==['api','v1','skills']:
            skill=self.published_skill(parts[3]);rid=skill['published_revision_id']
            if len(parts)==4:return self.skill_view(skill)
            if len(parts)>=5 and parts[4]=='revisions':
                if len(parts)>=6 and parts[5]!=rid:raise HTTPException(404,'仅可读取当前发布版本')
                revision=self.row('revisions',rid)
                safe={k:v for k,v in revision.items() if k in {'id','skill_id','platform_version','entry_path','files','content_hash','diagnostics','created_at'}}
                if len(parts)==5:return {'items':[safe]}
                if len(parts)==6:return safe
        if path=='api/v1/workbench/algorithms':
            result=[]
            for row in self.rows('algorithms'):
                try:result.append(self.algorithm_view(row))
                except HTTPException:pass
            return {'items':result}
        if len(parts)==5 and parts[:4]==['api','v1','workbench','algorithms']:
            return self.algorithm_view(self.row('algorithms',parts[4]))
        if path=='api/v1/workbench/runs':
            with self.owners() as db:
                ids={r[0] for r in db.execute("SELECT id FROM objects WHERE kind='runs' AND owner_id=?",(user['id'],))}
            rows=[r for r in self.rows('runs') if r['id'] in ids]
            return {'items':sorted(rows,key=lambda r:r.get('created_at',''),reverse=True)[:100]}
        return None

    def check(self, path, method, payload, query, user):
        if user['role']=='developer':
            return payload
        parts=path.split('/')
        if method in {'GET','HEAD'}:
            if path in {'api/v1/skills','api/v1/modules','api/v1/workbench/algorithms','api/v1/workbench/plots','api/v1/workbench/runs'}:
                return payload
            if len(parts)>=4 and parts[:3]==['api','v1','skills']:
                skill=self.published_skill(parts[3]);rid=skill['published_revision_id']
                if len(parts)==4:return payload
                if parts[4]=='revisions' and len(parts) in {5,6,7}:
                    if len(parts)>=6 and parts[5]!=rid:raise HTTPException(404,'仅可读取当前发布版本')
                    if len(parts)==7 and parts[6]!='files':raise HTTPException(404,'接口不存在')
                    return payload
                if len(parts)==5 and parts[4]=='export':
                    if query.get('revision_id',rid)!=rid:raise HTTPException(404,'仅可导出当前发布版本')
                    return payload
            if len(parts)==5 and parts[:4]==['api','v1','workbench','algorithms']:
                self.algorithm_view(self.row('algorithms',parts[4]));return payload
            if len(parts)>=5 and parts[:3]==['api','v1','workbench']:
                kind,key=parts[3:5]
                if kind=='examples' and len(parts)==5 and key in {'matrix','abundance','importance','workflow'}:return payload
                if kind=='plots' and len(parts)==6 and parts[5]=='preview' and self.plot_allowed(key):return payload
                if kind=='datasets' and len(parts)==5:
                    self.require_owner(kind,key,user);return payload
                if kind=='runs' and (len(parts)==5 or len(parts)==6 and parts[5]=='export' or len(parts)==7 and parts[5]=='files'):
                    self.require_owner(kind,key,user);return payload
        if method=='POST':
            if path=='api/v1/workbench/datasets':return payload
            if path=='api/v1/workbench/runs':
                self.require_owner('datasets',payload.get('dataset_id',''),user)
                if payload.get('kind','algorithm')=='algorithm':
                    algorithm=self.algorithm_view(self.row('algorithms',payload.get('algorithm_id','')))
                    if payload.get('version_id',algorithm['active_version_id'])!=algorithm['active_version_id']:
                        raise HTTPException(403,'只能运行当前发布版本')
                    return {**payload,'version_id':algorithm['active_version_id']}
                if payload.get('kind')=='plot' and self.plot_allowed(payload.get('template_id','')):return payload
            if len(parts)==6 and parts[:4]==['api','v1','workbench','runs'] and parts[5] in {'cancel','replot'}:
                self.require_owner('runs',parts[4],user)
                if parts[5]=='replot' and not self.plot_allowed(payload.get('template_id','')):
                    raise HTTPException(403,'该绘图模板未发布或已停用')
                return payload
        raise HTTPException(403,'当前账户无权管理公共库或访问此接口')

    def after(self, path, method, data, user):
        if method=='POST' and path in {'api/v1/workbench/datasets','api/v1/workbench/runs'}:
            self.register(path.rsplit('/',1)[1],data['id'],user)
        elif method=='POST' and re.fullmatch(r'api/v1/workbench/runs/[^/]+/replot',path):
            self.register('runs',data['id'],user)
        if user['role']!='developer' and method=='GET' and path=='api/v1/workbench/plots':
            data={'items':[r for r in data.get('items',[]) if self.plot_allowed(r['id'])]}
        return data
