import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskRepository:
    TASK_COLUMNS = {
        "name", "status", "updated_at", "started_at", "finished_at", "current_step",
        "retry_allowed", "retry_count", "error_message", "result_archive",
    }
    STEP_COLUMNS = {"status", "started_at", "finished_at", "progress", "error_message"}

    def __init__(self, database_path: Path):
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    manifest_path TEXT NOT NULL,
                    parameters_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    current_step TEXT,
                    retry_allowed INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    result_archive TEXT
                );
                CREATE TABLE IF NOT EXISTS steps (
                    task_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    progress REAL,
                    error_message TEXT,
                    PRIMARY KEY (task_id, name),
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY,
                    original_name TEXT NOT NULL,
                    stored_path TEXT NOT NULL UNIQUE,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            task_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "parameters_json" not in task_columns:
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN parameters_json TEXT NOT NULL DEFAULT '{}'"
                )
            if 'name' not in task_columns:
                connection.execute('ALTER TABLE tasks ADD COLUMN name TEXT')

    def create_task(
        self,
        task_id: str,
        manifest_path: str,
        step_names: Iterable[str],
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        parameters_json = json.dumps(parameters or {}, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO tasks
                   (id, manifest_path, parameters_json, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'queued', ?, ?)""",
                (task_id, manifest_path, parameters_json, now, now),
            )
            connection.executemany(
                "INSERT INTO steps (task_id, ordinal, name, status) VALUES (?, ?, ?, 'pending')",
                [(task_id, ordinal, name) for ordinal, name in enumerate(step_names)],
            )
        task = self.get_task(task_id)
        if task is None:
            raise RuntimeError("created task could not be reloaded")
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            task_row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task_row is None:
                return None
            step_rows = connection.execute(
                "SELECT name, status, started_at, finished_at, progress, error_message FROM steps WHERE task_id = ? ORDER BY ordinal",
                (task_id,),
            ).fetchall()
            alert_rows = connection.execute(
                "SELECT level, message, created_at FROM alerts WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        task = dict(task_row)
        try:
            task["parameters"] = json.loads(task.pop("parameters_json"))
        except (TypeError, ValueError, json.JSONDecodeError):
            task["parameters"] = {}
        task["retry_allowed"] = bool(task["retry_allowed"])
        task["steps"] = [dict(row) for row in step_rows]
        task["alerts"] = [dict(row) for row in alert_rows]
        return task

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            ids = [row[0] for row in connection.execute("SELECT id FROM tasks ORDER BY created_at DESC")]
        return [task for task_id in ids if (task := self.get_task(task_id)) is not None]

    def create_upload(
        self,
        upload_id: str,
        original_name: str,
        stored_path: str,
        size_bytes: int,
        sha256: str,
        status: str = "validated",
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO uploads
                   (id, original_name, stored_path, size_bytes, sha256, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    upload_id,
                    original_name,
                    stored_path,
                    size_bytes,
                    sha256,
                    status,
                    utc_now(),
                ),
            )
        upload = self.get_upload(upload_id)
        if upload is None:
            raise RuntimeError("created upload could not be reloaded")
        return upload

    def get_upload(self, upload_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM uploads WHERE id = ?", (upload_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def update_task(self, task_id: str, **fields: Any) -> None:
        invalid = set(fields) - self.TASK_COLUMNS
        if invalid:
            raise ValueError(f"invalid task fields: {sorted(invalid)}")
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{column} = ?" for column in fields)
        values = [int(value) if column == "retry_allowed" else value for column, value in fields.items()]
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE tasks SET {assignments} WHERE id = ?", (*values, task_id)
            )
            if cursor.rowcount != 1:
                raise KeyError(task_id)

    def update_step(self, task_id: str, step_name: str, **fields: Any) -> None:
        invalid = set(fields) - self.STEP_COLUMNS
        if invalid:
            raise ValueError(f"invalid step fields: {sorted(invalid)}")
        assignments = ", ".join(f"{column} = ?" for column in fields)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE steps SET {assignments} WHERE task_id = ? AND name = ?",
                (*fields.values(), task_id, step_name),
            )
            if cursor.rowcount != 1:
                raise KeyError((task_id, step_name))

    def apply_progress(
        self, task_id: str, updates: dict[str, dict[str, Any]], current_step: str | None
    ) -> None:
        """Atomically apply a poll without racing a user's cancellation."""
        for fields in updates.values():
            invalid = set(fields) - self.STEP_COLUMNS
            if invalid:
                raise ValueError(f"invalid step fields: {sorted(invalid)}")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None or task["status"] not in {"queued", "validating", "running"}:
                return
            for step, fields in updates.items():
                assignments = ", ".join(f"{column} = ?" for column in fields)
                connection.execute(
                    f"UPDATE steps SET {assignments} WHERE task_id = ? AND name = ?",
                    (*fields.values(), task_id, step),
                )
            if current_step is not None:
                connection.execute(
                    "UPDATE tasks SET current_step = ?, updated_at = ? WHERE id = ?",
                    (current_step, utc_now(), task_id),
                )

    def publish_failure_pending(self, task_id: str, step: str, message: str) -> None:
        """Expose the workflow failure before optional diagnosis, preserving cancellation."""
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            task = connection.execute('SELECT status FROM tasks WHERE id=?', (task_id,)).fetchone()
            if task is None or task['status'] not in {'queued', 'validating', 'running'}:
                return
            now = utc_now()
            connection.execute(
                "UPDATE tasks SET status='paused', current_step=?, error_message=?, "
                "retry_allowed=0, updated_at=?, finished_at=? WHERE id=?",
                (step, message, now, now, task_id),
            )
            connection.execute(
                "UPDATE steps SET status='failed', error_message=?, finished_at=? WHERE task_id=? AND name=?",
                (message, now, task_id, step),
            )

    def reset_for_retry(self, task_id: str) -> None:
        now = utc_now()
        with self._connect() as connection:
            task = connection.execute(
                "SELECT status, retry_allowed, retry_count FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise KeyError(task_id)
            if task["status"] != "failed" or not task["retry_allowed"] or task["retry_count"] >= 1:
                raise ValueError("task is not eligible for automatic retry")
            connection.execute(
                """UPDATE tasks SET status='queued', updated_at=?, started_at=NULL, finished_at=NULL,
                   current_step=NULL, retry_allowed=0, retry_count=retry_count+1, error_message=NULL
                   WHERE id=?""",
                (now, task_id),
            )
            connection.execute(
                """UPDATE steps SET status='pending', started_at=NULL, finished_at=NULL,
                   progress=NULL, error_message=NULL WHERE task_id=?""",
                (task_id,),
            )

    def cancel_task(self, task_id: str) -> None:
        now = utc_now()
        with self._connect() as connection:
            task = connection.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise KeyError(task_id)
            if task["status"] == "cancelled":
                return
            if task["status"] not in {"queued", "validating", "running", "paused"}:
                raise ValueError("task is not eligible for cancellation")
            connection.execute(
                """UPDATE tasks SET status='cancelled', updated_at=?, finished_at=?,
                   retry_allowed=0, error_message='cancelled by user' WHERE id=?""",
                (now, now, task_id),
            )
            connection.execute(
                """UPDATE steps SET status='skipped', finished_at=?,
                   error_message='cancelled by user'
                   WHERE task_id=? AND status IN ('pending', 'running')""",
                (now, task_id),
            )

    def add_alert(self, task_id: str, level: str, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO alerts (task_id, level, message, created_at) VALUES (?, ?, ?, ?)",
                (task_id, level, message, utc_now()),
            )

    def pause_inflight_tasks(self) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE tasks SET status='paused', updated_at=?, error_message='API restarted while task was running' WHERE status='running'",
                (now,),
            )
