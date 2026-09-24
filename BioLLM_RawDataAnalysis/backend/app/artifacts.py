from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class ArtifactRepository:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    node_id TEXT,
                    producer TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    sample_scope TEXT NOT NULL,
                    sample_id TEXT,
                    cohort_id TEXT,
                    media_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    downloadable INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS artifacts_task_id_idx
                    ON artifacts(task_id, created_at, artifact_id);
                CREATE INDEX IF NOT EXISTS artifacts_type_idx
                    ON artifacts(task_id, artifact_type);

                CREATE TABLE IF NOT EXISTS artifact_relations (
                    artifact_id TEXT NOT NULL,
                    parent_artifact_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    PRIMARY KEY (artifact_id, parent_artifact_id, relation_type),
                    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
                    FOREIGN KEY (parent_artifact_id) REFERENCES artifacts(artifact_id) ON DELETE RESTRICT
                );
                """
            )

    def insert(
        self,
        record: dict[str, Any],
        parent_artifact_ids: list[str],
    ) -> None:
        columns = (
            "artifact_id",
            "task_id",
            "node_id",
            "producer",
            "artifact_type",
            "schema_version",
            "sample_scope",
            "sample_id",
            "cohort_id",
            "media_type",
            "path",
            "sha256",
            "size_bytes",
            "metadata_json",
            "downloadable",
            "status",
            "created_at",
        )
        values = [record[column] for column in columns]
        placeholders = ", ".join("?" for _ in columns)
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO artifacts ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
            connection.executemany(
                """
                INSERT INTO artifact_relations
                    (artifact_id, parent_artifact_id, relation_type)
                VALUES (?, ?, 'derived_from')
                """,
                [(record["artifact_id"], parent_id) for parent_id in parent_artifact_ids],
            )

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if row is None:
                return None
            record = dict(row)
            record["derived_from"] = [
                relation["parent_artifact_id"]
                for relation in connection.execute(
                    """
                    SELECT parent_artifact_id FROM artifact_relations
                    WHERE artifact_id = ? AND relation_type = 'derived_from'
                    ORDER BY parent_artifact_id
                    """,
                    (artifact_id,),
                ).fetchall()
            ]
            return record

    def list_for_task(
        self,
        task_id: str,
        *,
        artifact_type: str | None = None,
        producer: str | None = None,
        sample_scope: str | None = None,
        artifact_type_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["task_id = ?", "status = 'active'"]
        values: list[Any] = [task_id]
        for column, value in (
            ("artifact_type", artifact_type),
            ("producer", producer),
            ("sample_scope", sample_scope),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        if artifact_type_prefix is not None:
            clauses.append("artifact_type LIKE ?")
            values.append(f"{artifact_type_prefix}%")
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT artifact_id FROM artifacts WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at, artifact_id",
                values,
            ).fetchall()
        return [self.get(row["artifact_id"]) for row in rows]
