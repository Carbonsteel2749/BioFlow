from __future__ import annotations
from ..portable_db import connect as portable_connect

import re
import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

from backend.app.models import utc_now

from .graph import WorkflowNode


_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_PORT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_TRANSITIONS = {
    "pending": frozenset({"queued", "skipped", "cancelled"}),
    "queued": frozenset({"running", "skipped", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "paused", "cancelled"}),
    "paused": frozenset({"queued", "cancelled"}),
    "failed": frozenset({"queued", "cancelled"}),
    "succeeded": frozenset(),
    "skipped": frozenset(),
    "cancelled": frozenset(),
}


class InvalidNodeTransition(ValueError):
    """A requested node state change is not part of the lifecycle."""


class WorkflowRunRepository:
    """Transactional state for compiled, user-defined workflow runs."""

    def __init__(self, database_path: Path):
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = portable_connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    task_id TEXT PRIMARY KEY,
                    graph_hash TEXT NOT NULL,
                    compiled_root TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error_message TEXT,
                    input_manifest TEXT,
                    database_manifest TEXT,
                    log_path TEXT
                );
                CREATE TABLE IF NOT EXISTS workflow_node_runs (
                    task_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    node_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL,
                    started_at TEXT,
                    finished_at TEXT,
                    error_message TEXT,
                    log_path TEXT,
                    cache_id TEXT,
                    PRIMARY KEY (task_id, node_id),
                    FOREIGN KEY (task_id) REFERENCES workflow_runs(task_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS workflow_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    node_id TEXT,
                    port_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (task_id, node_id, port_id, path),
                    FOREIGN KEY (task_id) REFERENCES workflow_runs(task_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (task_id, node_id)
                        REFERENCES workflow_node_runs(task_id, node_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_nodes_status
                    ON workflow_node_runs(task_id, status);
                CREATE INDEX IF NOT EXISTS idx_workflow_artifacts_task
                    ON workflow_artifacts(task_id, node_id);
                """
            )
            run_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(workflow_runs)"
                ).fetchall()
            }
            for column in ("input_manifest", "database_manifest", "log_path"):
                if column not in run_columns:
                    connection.execute(
                        f"ALTER TABLE workflow_runs ADD COLUMN {column} TEXT"
                    )
            if 'attempt' not in run_columns:
                connection.execute('ALTER TABLE workflow_runs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1')
            for column, definition in {
                'name': "TEXT NOT NULL DEFAULT ''",
                'auto_repair': 'INTEGER NOT NULL DEFAULT 0',
                'repair_count': 'INTEGER NOT NULL DEFAULT 0',
                'metadata_snapshot': 'TEXT',
                'metadata_sha256': 'TEXT',
                'execution_root': 'TEXT',
                'failure_node_id': 'TEXT',
                'result_archive': 'TEXT',
            }.items():
                if column not in run_columns:
                    connection.execute(f'ALTER TABLE workflow_runs ADD COLUMN {column} {definition}')
            connection.execute('''CREATE TABLE IF NOT EXISTS workflow_run_attempts (
                task_id TEXT NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
                log_path TEXT, created_at TEXT NOT NULL, finished_at TEXT,
                PRIMARY KEY(task_id,attempt), FOREIGN KEY(task_id) REFERENCES workflow_runs(task_id) ON DELETE CASCADE)''')
            connection.execute('''INSERT OR IGNORE INTO workflow_run_attempts
                (task_id,attempt,status,log_path,created_at,finished_at)
                SELECT task_id,attempt,status,log_path,created_at,finished_at FROM workflow_runs''')

    def list_runs(self, *, limit: int = 20, offset: int = 0, q: str = '', status: str = '') -> dict:
        if not 1 <= limit <= 100 or offset < 0 or len(q) > 200:
            raise ValueError('invalid history pagination')
        clauses, values = [], []
        if q:
            clauses.append('(instr(lower(name),lower(?))>0 OR instr(lower(task_id),lower(?))>0)')
            values.extend([q, q])
        if status:
            if status not in {'queued','running','succeeded','failed','paused','cancelled'}:
                raise ValueError('invalid workflow status')
            clauses.append('status=?')
            values.append(status)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self._connect() as connection:
            total = connection.execute('SELECT count(*) FROM workflow_runs' + where, values).fetchone()[0]
            rows = connection.execute('''SELECT task_id,name,status,created_at,updated_at,
                attempt,auto_repair,repair_count,error_message FROM workflow_runs''' + where +
                ' ORDER BY created_at DESC,task_id DESC LIMIT ? OFFSET ?', [*values,limit,offset]).fetchall()
        return {'items': [{**dict(row), 'auto_repair': bool(row['auto_repair'])} for row in rows],
                'total': total, 'limit': limit, 'offset': offset}

    def reserve_repair(self, task_id: str) -> int | None:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            changed = connection.execute('''UPDATE workflow_runs SET repair_count=repair_count+1
                WHERE task_id=? AND auto_repair=1 AND repair_count<2 AND status='failed' ''', (task_id,))
            if not changed.rowcount:
                return None
            return connection.execute('SELECT repair_count FROM workflow_runs WHERE task_id=?', (task_id,)).fetchone()[0]

    def set_execution_root(self, task_id: str, execution_root: Path) -> None:
        with self._connect() as connection:
            changed = connection.execute("UPDATE workflow_runs SET execution_root=? WHERE task_id=? AND status IN ('failed','paused')", (str(execution_root.resolve()),task_id))
            if not changed.rowcount:
                raise ValueError('workflow is no longer repairable')

    def set_metadata_snapshot(self, task_id: str, path: Path, sha256: str) -> None:
        if not _SHA256.fullmatch(sha256):
            raise ValueError('invalid metadata digest')
        with self._connect() as connection:
            changed = connection.execute('''UPDATE workflow_runs SET metadata_snapshot=?,metadata_sha256=?
                WHERE task_id=? AND metadata_snapshot IS NULL AND status='queued' ''', (str(path.resolve()),sha256,task_id))
            if not changed.rowcount:
                raise ValueError('metadata snapshot is already fixed or task started')

    def pause_inflight_runs(self) -> None:
        """A restarted API cannot retain ownership of its former in-memory queue."""
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            now = utc_now()
            connection.execute("UPDATE workflow_node_runs SET status='paused' WHERE status IN ('pending','queued','running') AND task_id IN (SELECT task_id FROM workflow_runs WHERE status IN ('queued','running'))")
            connection.execute("UPDATE workflow_runs SET status='paused', error_message='API restart interrupted task ownership; inspect processes before resuming', updated_at=? WHERE status IN ('queued','running')", (now,))
            connection.execute("UPDATE workflow_run_attempts SET status='paused' WHERE status IN ('queued','running')")

    def resume_run(self, task_id: str) -> dict[str, object]:
        """Keep completed nodes and compilation immutable; requeue only unfinished work."""
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            run = connection.execute('SELECT status FROM workflow_runs WHERE task_id=?', (task_id,)).fetchone()
            if run is None:
                raise KeyError(task_id)
            if run['status'] not in {'failed', 'paused'}:
                raise ValueError('only failed or paused dynamic runs can resume')
            unfinished = connection.execute("SELECT COUNT(*) FROM workflow_node_runs WHERE task_id=? AND status NOT IN ('succeeded','skipped','cancelled')", (task_id,)).fetchone()[0]
            if not unfinished:
                raise ValueError('no unfinished calculation nodes to resume; inspect launcher failure')
            now = utc_now()
            connection.execute("UPDATE workflow_node_runs SET status='queued', error_message=NULL, finished_at=NULL WHERE task_id=? AND status IN ('failed','paused','running','pending')", (task_id,))
            connection.execute("UPDATE workflow_runs SET status='queued', attempt=attempt+1, error_message=NULL, failure_node_id=NULL, finished_at=NULL, updated_at=? WHERE task_id=?", (now, task_id))
            connection.execute('''INSERT INTO workflow_run_attempts(task_id,attempt,status,created_at)
                SELECT task_id,attempt,'queued',? FROM workflow_runs WHERE task_id=?''', (now,task_id))
        return self.get_run(task_id)

    def cancel_run(self, task_id: str) -> None:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            run = connection.execute('SELECT status FROM workflow_runs WHERE task_id=?', (task_id,)).fetchone()
            if run is None:
                raise KeyError(task_id)
            if run['status'] in {'succeeded', 'cancelled'}:
                raise ValueError('completed dynamic run cannot be cancelled')
            now = utc_now()
            connection.execute("UPDATE workflow_node_runs SET status='cancelled', finished_at=? WHERE task_id=? AND status NOT IN ('succeeded','skipped','cancelled')", (now, task_id))
            connection.execute("UPDATE workflow_runs SET status='cancelled', finished_at=?, updated_at=? WHERE task_id=?", (now, now, task_id))
            connection.execute("UPDATE workflow_run_attempts SET status='cancelled',finished_at=? WHERE task_id=? AND attempt=(SELECT attempt FROM workflow_runs WHERE task_id=?)", (now,task_id,task_id))

    def pause_run(self, task_id: str, reason: str) -> None:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            changed = connection.execute("UPDATE workflow_runs SET status='paused', error_message=?, updated_at=? WHERE task_id=? AND status IN ('queued','failed','paused')", (reason, utc_now(), task_id))
            if changed.rowcount:
                connection.execute("UPDATE workflow_run_attempts SET status='paused' WHERE task_id=? AND attempt=(SELECT attempt FROM workflow_runs WHERE task_id=?)", (task_id,task_id))

    def fail_run(self, task_id: str, reason: str) -> None:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            now = utc_now()
            changed = connection.execute("UPDATE workflow_runs SET status='failed', error_message=?, failure_node_id=NULL, updated_at=?, finished_at=? WHERE task_id=? AND status NOT IN ('cancelled','succeeded')", (reason, now, now, task_id))
            if changed.rowcount:
                connection.execute("UPDATE workflow_node_runs SET status='failed', error_message=?, finished_at=? WHERE task_id=? AND status IN ('pending','queued','running','paused')", (reason, now, task_id))
                connection.execute("UPDATE workflow_run_attempts SET status='failed',finished_at=? WHERE task_id=? AND attempt=(SELECT attempt FROM workflow_runs WHERE task_id=?)", (now,task_id,task_id))

    def create_run(
        self,
        *,
        task_id: str,
        graph_hash: str,
        compiled_root: Path,
        nodes: Iterable[WorkflowNode],
        topological_order: Sequence[str],
        input_manifest: Path | None = None,
        database_manifest: Path | None = None,
        name: str = '',
        auto_repair: bool = False,
    ) -> dict[str, object]:
        if not _SAFE_TASK_ID.fullmatch(task_id):
            raise ValueError("task_id contains unsafe characters")
        if not _SHA256.fullmatch(graph_hash):
            raise ValueError("graph_hash must be a lowercase SHA-256 digest")
        node_by_id = {node.id: node for node in nodes}
        if len(node_by_id) != len(topological_order):
            raise ValueError("topological_order must contain every node exactly once")
        if set(node_by_id) != set(topological_order):
            raise ValueError("topological_order does not match workflow nodes")
        compiled_path = compiled_root.resolve()
        if not isinstance(name, str) or len(name.strip()) > 120 or type(auto_repair) is not bool:
            raise ValueError('invalid workflow name or automatic repair setting')
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO workflow_runs
                   (task_id, graph_hash, compiled_root, status, created_at,
                    updated_at, input_manifest, database_manifest)
                   VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)""",
                (
                    task_id,
                    graph_hash,
                    str(compiled_path),
                    now,
                    now,
                    str(input_manifest.resolve()) if input_manifest else None,
                    (
                        str(database_manifest.resolve())
                        if database_manifest
                        else None
                    ),
                ),
            )
            connection.executemany(
                """INSERT INTO workflow_node_runs
                   (task_id, node_id, ordinal, node_type, status)
                   VALUES (?, ?, ?, ?, 'pending')""",
                [
                    (task_id, node_id, ordinal, node_by_id[node_id].type)
                    for ordinal, node_id in enumerate(topological_order)
                ],
            )
            connection.execute('UPDATE workflow_runs SET name=?,auto_repair=? WHERE task_id=?', (name.strip(),int(auto_repair),task_id))
            connection.execute("INSERT INTO workflow_run_attempts(task_id,attempt,status,created_at) VALUES (?,1,'queued',?)", (task_id,now))
        created = self.get_run(task_id)
        if created is None:
            raise RuntimeError("created workflow run could not be reloaded")
        return created

    def get_run(self, task_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT * FROM workflow_runs WHERE task_id = ?", (task_id,)
            ).fetchone()
            if run is None:
                return None
            nodes = connection.execute(
                """SELECT node_id, ordinal, node_type, status, progress,
                          started_at, finished_at, error_message, log_path, cache_id
                   FROM workflow_node_runs WHERE task_id = ? ORDER BY ordinal""",
                (task_id,),
            ).fetchall()
            artifacts = connection.execute(
                """SELECT id, task_id, node_id, port_id, path, sha256,
                          size_bytes, created_at
                   FROM workflow_artifacts WHERE task_id = ? ORDER BY id""",
                (task_id,),
            ).fetchall()
        result: dict[str, object] = dict(run)
        result['auto_repair'] = bool(result['auto_repair'])
        with self._connect() as connection:
            result['attempts'] = [dict(row) for row in connection.execute('SELECT * FROM workflow_run_attempts WHERE task_id=? ORDER BY attempt', (task_id,))]
        result["nodes"] = [dict(row) for row in nodes]
        result["artifacts"] = [dict(row) for row in artifacts]
        return result

    def set_run_log_path(self, task_id: str, log_path: Path) -> None:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE workflow_runs SET log_path = ?, updated_at = ?
                   WHERE task_id = ?""",
                (str(log_path.resolve()), now, task_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(task_id)
            connection.execute('''UPDATE workflow_run_attempts SET log_path=?
                WHERE task_id=? AND attempt=(SELECT attempt FROM workflow_runs WHERE task_id=?)''', (str(log_path.resolve()),task_id,task_id))

    def update_node_status(
        self,
        task_id: str,
        node_id: str,
        status: str,
        *,
        progress: float | None = None,
        error_message: str | None = None,
        log_path: Path | None = None,
        cache_id: str | None = None,
    ) -> None:
        if progress is not None and not 0 <= progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        if status not in _TRANSITIONS:
            raise ValueError(f"unknown node status: {status}")

        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT status, progress, started_at FROM workflow_node_runs
                   WHERE task_id = ? AND node_id = ?""",
                (task_id, node_id),
            ).fetchone()
            if current is None:
                raise KeyError((task_id, node_id))
            previous = current["status"]
            if status not in _TRANSITIONS[previous]:
                raise InvalidNodeTransition(f"{previous} -> {status} is not allowed")

            started_at = current["started_at"]
            finished_at = None
            next_error = error_message
            next_progress = progress
            if status == "queued":
                started_at = None
                next_error = None
                next_progress = 0.0 if progress is None else progress
            elif status == "running":
                started_at = started_at or now
                next_progress = 0.0 if progress is None else progress
            elif status == "succeeded":
                finished_at = now
                next_error = None
                next_progress = 100.0
            elif status in {"failed", "skipped", "cancelled"}:
                finished_at = now
                next_progress = current["progress"] if progress is None else progress
            elif status == "paused":
                next_progress = current["progress"] if progress is None else progress

            connection.execute(
                """UPDATE workflow_node_runs
                   SET status = ?, progress = ?, started_at = ?, finished_at = ?,
                       error_message = ?, log_path = COALESCE(?, log_path),
                       cache_id = COALESCE(?, cache_id)
                   WHERE task_id = ? AND node_id = ?""",
                (
                    status,
                    next_progress,
                    started_at,
                    finished_at,
                    next_error,
                    str(log_path.resolve()) if log_path is not None else None,
                    cache_id,
                    task_id,
                    node_id,
                ),
            )
            self._synchronize_run(connection, task_id, status, next_error, now)
            if status == 'failed':
                connection.execute('UPDATE workflow_runs SET failure_node_id=? WHERE task_id=?', (node_id,task_id))

    def update_node_progress(
        self,
        task_id: str,
        node_id: str,
        progress: float,
    ) -> None:
        """Refresh progress for a running node without changing its lifecycle."""
        if not 0 <= progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE workflow_node_runs SET progress = ?
                   WHERE task_id = ? AND node_id = ? AND status = 'running'""",
                (progress, task_id, node_id),
            )
            if cursor.rowcount != 1:
                raise InvalidNodeTransition(
                    "progress can only be updated for a running node"
                )
            connection.execute(
                """UPDATE workflow_runs SET updated_at = ?
                   WHERE task_id = ?""",
                (now, task_id),
            )

    def _synchronize_run(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        changed_status: str,
        error_message: str | None,
        now: str,
    ) -> None:
        run = connection.execute(
            "SELECT status, started_at FROM workflow_runs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if run is None:
            raise KeyError(task_id)
        statuses = [
            row[0]
            for row in connection.execute(
                "SELECT status FROM workflow_node_runs WHERE task_id = ?", (task_id,)
            )
        ]
        run_status = run["status"]
        started_at = run["started_at"]
        finished_at = None
        run_error = None
        if changed_status == "failed":
            run_status = "failed"
            finished_at = now
            run_error = error_message
        elif changed_status == "queued" and run_status in {"failed", "paused"}:
            run_status = "queued"
            started_at = None
        elif changed_status == "paused":
            run_status = "paused"
        elif changed_status == "running":
            run_status = "running"
            started_at = started_at or now
        elif statuses and all(item in {"succeeded", "skipped"} for item in statuses):
            run_status = "succeeded"
            finished_at = now
        elif statuses and all(item == "cancelled" for item in statuses):
            run_status = "cancelled"
            finished_at = now

        connection.execute(
            """UPDATE workflow_runs
               SET status = ?, updated_at = ?, started_at = ?, finished_at = ?,
                   error_message = ? WHERE task_id = ?""",
            (run_status, now, started_at, finished_at, run_error, task_id),
        )
        connection.execute('''UPDATE workflow_run_attempts SET status=?,finished_at=?
            WHERE task_id=? AND attempt=(SELECT attempt FROM workflow_runs WHERE task_id=?)''', (run_status,finished_at,task_id,task_id))

    def record_artifact(
        self,
        *,
        task_id: str,
        node_id: str | None,
        port_id: str,
        path: Path,
        sha256: str,
        size_bytes: int,
    ) -> dict[str, object]:
        if not _SAFE_PORT_ID.fullmatch(port_id):
            raise ValueError("port_id contains unsafe characters")
        if not _SHA256.fullmatch(sha256):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        if size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        if not path.is_absolute():
            raise ValueError("artifact path must be absolute")
        now = utc_now()
        with self._connect() as connection:
            run = connection.execute(
                "SELECT 1 FROM workflow_runs WHERE task_id = ?", (task_id,)
            ).fetchone()
            if run is None:
                raise KeyError(task_id)
            if node_id is not None:
                node = connection.execute(
                    """SELECT 1 FROM workflow_node_runs
                       WHERE task_id = ? AND node_id = ?""",
                    (task_id, node_id),
                ).fetchone()
                if node is None:
                    raise KeyError((task_id, node_id))
            cursor = connection.execute(
                """INSERT INTO workflow_artifacts
                   (task_id, node_id, port_id, path, sha256, size_bytes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    node_id,
                    port_id,
                    str(path.resolve()),
                    sha256,
                    size_bytes,
                    now,
                ),
            )
            artifact_id = cursor.lastrowid
            row = connection.execute(
                "SELECT * FROM workflow_artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("created artifact could not be reloaded")
        return dict(row)
