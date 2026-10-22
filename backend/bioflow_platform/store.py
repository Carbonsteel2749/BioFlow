"""Persistent project membership; upstream data stays in its owning service."""
import json
import sqlite3
import uuid
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '', knowledge_base_id TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS links(project_id TEXT, kind TEXT, external_id TEXT,
                    PRIMARY KEY(project_id,kind,external_id));
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, project_id TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP, action TEXT, detail TEXT);
                CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'user',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,
                    csrf TEXT NOT NULL,expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY,count INTEGER,reset_at REAL);
                CREATE TABLE IF NOT EXISTS resources(kind TEXT,external_id TEXT,user_id TEXT NOT NULL,
                    archived INTEGER DEFAULT 0,PRIMARY KEY(kind,external_id));
                CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,sha256 TEXT NOT NULL,status TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS assistant_runs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,instruction TEXT NOT NULL,mode TEXT NOT NULL,
                    status TEXT NOT NULL,model TEXT NOT NULL,plan TEXT NOT NULL DEFAULT '{}',
                    result TEXT NOT NULL DEFAULT '{}',created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS project_uploads(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,user_id TEXT NOT NULL,
                    name TEXT NOT NULL,size_bytes INTEGER NOT NULL,chunk_bytes INTEGER NOT NULL,
                    hashes TEXT NOT NULL,fingerprint TEXT NOT NULL,received TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'open',backend_id TEXT,backend_token TEXT,asset_id TEXT,
                    error TEXT NOT NULL DEFAULT '',created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS project_uploads_owner ON project_uploads(project_id,user_id);
                CREATE TABLE IF NOT EXISTS upload_pairs(project_id TEXT NOT NULL,user_id TEXT NOT NULL,sample_id TEXT NOT NULL,
                    read1 TEXT NOT NULL,read2 TEXT NOT NULL,dataset_id TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(project_id,read1,read2));
                CREATE TABLE IF NOT EXISTS catalogue_entries(project_id TEXT NOT NULL,user_id TEXT NOT NULL,
                    id TEXT NOT NULL,collection TEXT NOT NULL,payload TEXT NOT NULL,
                    PRIMARY KEY(project_id,user_id,id));
                CREATE TABLE IF NOT EXISTS catalogue_sync(project_id TEXT NOT NULL,user_id TEXT NOT NULL,
                    updated_at REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',PRIMARY KEY(project_id,user_id));
            ''')
            columns={r[1] for r in db.execute('PRAGMA table_info(projects)')}
            if 'owner_id' not in columns:
                db.execute("ALTER TABLE projects ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''")
            if 'archived' not in columns:
                db.execute('ALTER TABLE projects ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
            if 'metadata' not in {r[1] for r in db.execute('PRAGMA table_info(resources)')}:
                db.execute("ALTER TABLE resources ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def projects(self, owner_id=None):
        with self.connect() as db:
            if owner_id is None:
                return [dict(row) for row in db.execute('SELECT * FROM projects WHERE archived=0 ORDER BY rowid')]
            return [dict(row) for row in db.execute('SELECT * FROM projects WHERE owner_id=? AND archived=0 ORDER BY rowid',(owner_id,))]

    def get(self, pid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
            return dict(row) if row else None

    def create(self, name, description, owner_id=''):
        pid = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO projects(id,name,description,owner_id) VALUES(?,?,?,?)', (pid,name,description,owner_id))
        self.event(pid, '创建项目', name)
        return self.get(pid)

    def bind_kb(self, pid, kb):
        with self.connect() as db:
            db.execute('UPDATE projects SET knowledge_base_id=? WHERE id=?', (kb,pid))
        self.event(pid, '关联知识库', kb)

    def link(self, pid, kind, eid):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO links VALUES(?,?,?)', (pid,kind,eid))
        self.event(pid, '关联'+kind, eid)

    def ids(self, pid, kind):
        with self.connect() as db:
            return [r[0] for r in db.execute('SELECT external_id FROM links WHERE project_id=? AND kind=?', (pid,kind))]

    def event(self, pid, action, detail):
        with self.connect() as db:
            db.execute('INSERT INTO events(project_id,action,detail) VALUES(?,?,?)', (pid,action,json.dumps(detail,ensure_ascii=False)))

    def history(self, pid):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM events WHERE project_id=? ORDER BY id DESC LIMIT 100',(pid,))]

    def claim(self,kind,eid,uid,metadata=None):
        with self.connect() as db:
            db.execute('INSERT INTO resources(kind,external_id,user_id) VALUES(?,?,?) ON CONFLICT(kind,external_id) DO UPDATE SET archived=0 WHERE user_id=excluded.user_id',(kind,eid,uid))
            if metadata:
                db.execute('UPDATE resources SET metadata=? WHERE kind=? AND external_id=? AND user_id=?',(json.dumps(metadata),kind,eid,uid))

    def owns(self,kind,eid,uid):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM resources WHERE kind=? AND external_id=? AND user_id=? AND archived=0',(kind,eid,uid)).fetchone() is not None

    def owned_ids(self,kind,uid):
        with self.connect() as db:
            return {r[0] for r in db.execute('SELECT external_id FROM resources WHERE kind=? AND user_id=? AND archived=0',(kind,uid))}

    def resource_metadata(self,kind,uid):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT metadata FROM resources WHERE kind=? AND user_id=? AND archived=0',(kind,uid))]
