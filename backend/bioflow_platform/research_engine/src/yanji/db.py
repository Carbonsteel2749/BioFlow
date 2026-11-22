"""SQLite persistence. All workers share the same file and transactions."""

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import runtime_root


def uid() -> str:
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "records"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    project_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class SchemaVersion(Base):
    __tablename__ = "schema_versions"
    version: Mapped[str] = mapped_column(String(30), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


_engine = None
_factory = None


def engine():
    global _engine, _factory
    url = f"sqlite:///{runtime_root() / 'yanji.sqlite3'}"
    if _engine is None or str(_engine.url) != url:
        _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(_engine, "connect")
        def pragmas(connection, record):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")

        _factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def initialize():
    from .migrations import upgrade

    upgrade(engine(), Base.metadata)


@contextmanager
def session():
    engine()
    with _factory() as db:
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


def serialize(record: Record) -> dict:
    return {
        "id": record.id,
        "kind": record.kind,
        "project_id": record.project_id,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        **record.data,
    }


def create(kind, project_id=None, **data):
    with session() as db:
        row = Record(kind=kind, project_id=project_id, data=data)
        db.add(row)
        db.flush()
        return serialize(row)


def get(record_id, kind=None):
    with session() as db:
        row = db.get(Record, record_id)
        if row is None or (kind and row.kind != kind):
            raise KeyError("记录不存在")
        return serialize(row)


def list_records(kind, project_id=None):
    from sqlalchemy import select

    with session() as db:
        stmt = select(Record).where(Record.kind == kind)
        if project_id is not None:
            stmt = stmt.where(Record.project_id == project_id)
        return [serialize(r) for r in db.scalars(stmt.order_by(Record.created_at)).all()]


def update(record_id, **patch):
    with session() as db:
        row = db.get(Record, record_id)
        if row is None:
            raise KeyError("记录不存在")
        row.data = {**row.data, **patch}
        db.flush()
        return serialize(row)
