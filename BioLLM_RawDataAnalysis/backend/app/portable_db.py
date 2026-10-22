"""Persist runtime references relative to the database; resolve at the I/O boundary."""
import json
from pathlib import Path
import sqlite3
import threading

LOCK=threading.Lock()
READY=set()
PREFIX='@runtime/'


def transform(value,root,encode):
    if isinstance(value,dict):return {k:transform(v,root,encode) for k,v in value.items()}
    if isinstance(value,list):return [transform(v,root,encode) for v in value]
    if not isinstance(value,str):return value
    if value.startswith(('{','[')):
        try:
            parsed=json.loads(value);changed=transform(parsed,root,encode)
            return json.dumps(changed,ensure_ascii=False,separators=(',',':')) if changed!=parsed else value
        except ValueError:pass
    text=value.replace('\\','/')
    if not encode:
        if text.startswith(PREFIX):return str(root/text[len(PREFIX):])
        relative=transform(value,root,True)
        if relative.startswith(PREFIX):return str(root/relative[len(PREFIX):])
        return value
    marker='/BioLLM_RawDataAnalysis/runtime/'
    current=root.as_posix().rstrip('/')+'/'
    if text.startswith(current):return PREFIX+text[len(current):]
    if marker in text and (text.startswith('/') or (len(text)>1 and text[1]==':')):return PREFIX+text.split(marker,1)[1]
    return value


class PortableConnection(sqlite3.Connection):
    @property
    def row_factory(self):return getattr(self,'requested_factory',None)
    @row_factory.setter
    def row_factory(self,factory):
        self.requested_factory=factory
        def decode(cursor,row):
            values=tuple(transform(v,self.runtime_root,False) for v in row)
            return factory(cursor,values) if factory else values
        sqlite3.Connection.row_factory.__set__(self,decode)
    def execute(self,sql,parameters=()):
        if isinstance(parameters,dict):values={k:transform(v,self.runtime_root,True) for k,v in parameters.items()}
        else:values=tuple(transform(v,self.runtime_root,True) for v in parameters)
        return super().execute(sql,values)
    def executemany(self,sql,parameters):
        return super().executemany(sql,(tuple(transform(v,self.runtime_root,True) for v in row) for row in parameters))


def connect(path,**kwargs):
    path=Path(path);root=(path.parent.parent if path.parent.name=='state' else path.parent).resolve()
    key=str(path.resolve())
    with LOCK:
        if key not in READY and path.is_file():
            # Back up before the one-time conversion. All table contents and identities remain.
            with sqlite3.connect(path,timeout=30) as db:
                marker=db.execute("SELECT name FROM sqlite_master WHERE name='bioflow_portable_format'").fetchone()
                if not marker:
                    backup=path.with_name(path.name+'.before-relative-paths')
                    if not backup.exists():
                        with sqlite3.connect(backup) as target:db.backup(target)
                    tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                    for table in tables:
                        quoted='"'+table.replace('"','""')+'"'
                        columns=[r[1] for r in db.execute(f'PRAGMA table_info({quoted})')]
                        try:rows=db.execute(f'SELECT rowid,* FROM {quoted}').fetchall()
                        except sqlite3.OperationalError:continue
                        for row in rows:
                            changes=[(name,transform(v,root,True)) for name,v in zip(columns,row[1:]) if transform(v,root,True)!=v]
                            if changes:
                                assignments=','.join('"'+name.replace('"','""')+'"=?' for name,_ in changes)
                                try:db.execute(f'UPDATE {quoted} SET {assignments} WHERE rowid=?',[v for _,v in changes]+[row[0]])
                                except sqlite3.IntegrityError:
                                    # Historical aliases can name the same relocated file. Preserve their IDs.
                                    for name,value in changes:
                                        try:db.execute(f'UPDATE {quoted} SET "{name}"=? WHERE rowid=?',(value,row[0]))
                                        except sqlite3.IntegrityError:pass
                    db.execute('CREATE TABLE bioflow_portable_format(version INTEGER)');db.execute('INSERT INTO bioflow_portable_format VALUES(1)')
            READY.add(key)
    connection=sqlite3.connect(path,factory=PortableConnection,**kwargs)
    connection.runtime_root=root.resolve();connection.row_factory=None
    return connection
