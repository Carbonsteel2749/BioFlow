"""Versioned, transactional upgrades protected against concurrent process startup."""

import fcntl
from pathlib import Path

from sqlalchemy import select


def upgrade(engine, metadata):
    lock_path = Path(engine.url.database).with_suffix(".migration.lock")
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        versions = metadata.tables["schema_versions"]
        versions.create(engine, checkfirst=True)

        def initial(connection):
            metadata.create_all(connection)

        migrations = [("001_initial", initial)]
        with engine.begin() as connection:
            applied = set(connection.execute(select(versions.c.version)).scalars())
            for name, apply in migrations:
                if name not in applied:
                    apply(connection)
                    connection.execute(versions.insert().values(version=name))
