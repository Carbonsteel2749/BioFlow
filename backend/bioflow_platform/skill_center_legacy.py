"""Original-system adapters use the central Skill metadata, keeping project execution unchanged."""
import json,os,sqlite3
from pathlib import Path
from datetime import datetime,timezone
from fastapi import HTTPException

MANIFEST=Path(__file__).resolve().parents[2]/'runtime/skill-center-legacy-bindings.json'
def binding(identifier):
 if not MANIFEST.exists():return None,None
 try:
  data=json.loads(MANIFEST.read_text());return data['bindings'].get(identifier),data['center_database']
 except (ValueError,KeyError,OSError):raise HTTPException(503,'Skill 中心绑定登记暂不可读取')
def read(identifier):
 entry,path=binding(identifier)
 if not entry:return None
 try:
  with sqlite3.connect('file:'+path+'?mode=ro',uri=True) as db:row=db.execute('SELECT body FROM skills WHERE id=?',(entry['center_skill_id'],)).fetchone()
  return entry,json.loads(row[0]) if row else None
 except (sqlite3.Error,OSError):raise HTTPException(503,'Skill 中心暂不可读取')
def active(identifier):
 result=read(identifier)
 if result is None:return None
 _,s=result
 return bool(s and not s.get('archived_at') and s.get('enabled') and s.get('published_revision_id'))
def decorate(items):
 result=[]
 for item in items:
  pair=read(item.get('name',item.get('id','')))
  if pair is None:result.append(item);continue
  entry,s=pair
  result.append({**item,'center_skill_id':entry['center_skill_id'],'center_published_revision_id':s.get('published_revision_id') if s else None,'enabled':int(bool(s and not s.get('archived_at') and s.get('enabled') and s.get('published_revision_id'))),'category':s.get('category',item.get('category')) if s else item.get('category'),'description':(s.get('display_summary') or s.get('description') or item.get('description')) if s else item.get('description'),'center_binding_mode':entry['mode']})
 return result
def configure(identifier,body):
 entry,path=binding(identifier)
 if not entry:return False
 with sqlite3.connect(path,timeout=15) as db:
  db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT body FROM skills WHERE id=?',(entry['center_skill_id'],)).fetchone()
  if not row:raise HTTPException(404,'绑定的 Skill 不存在')
  s=json.loads(row[0]);s.update(enabled=body.enabled,category=body.category or s.get('category','通用'),display_summary=body.description,updated_at=datetime.now(timezone.utc).isoformat())
  db.execute('UPDATE skills SET body=? WHERE id=?',(json.dumps(s,ensure_ascii=False),s['id']))
 return True
