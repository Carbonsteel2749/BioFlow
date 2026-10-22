"""Content-bound uploads; only newly owned session files are ever replaced."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import time
import uuid
from contextlib import closing

from .input_lifecycle import input_lock
from .tasks import TaskValidationError
from .uploads import FASTQ_SUFFIXES, _safe_original_name, _validate_first_fastq_record


class UploadConflictError(ValueError):
    pass


class UploadSpaceError(ValueError):
    pass


def _finalizer_running(settings, row):
    path=settings.input_root/'upload_sessions'/row['id']/'finalize.lock'
    if any(p.is_symlink() for p in (path,path.parent,path.parent.parent)):
        return True  # Unsafe/ambiguous state must not release a reservation.
    if not path.exists():
        return False
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or os.name!='posix':
        return True
    import fcntl
    try:
        with path.open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            fcntl.flock(lock,fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True


def _live(settings,row):
    if row['status']=='open':
        return row['expires_at']>time.time()
    return row['status']=='finalizing' and (row['expires_at']>time.time() or _finalizer_running(settings,row))


def _remaining(settings,row):
    stored=sum(min(row['chunk_bytes'],row['size_bytes']-i*row['chunk_bytes']) for i in json.loads(row['received_json']))
    suffix=next(s for s in FASTQ_SUFFIXES if row['original_name'].lower().endswith(s))
    directory=settings.input_root/'uploads'/row['id']
    allocated=0
    if not directory.is_symlink() and not directory.parent.is_symlink():
        for path in (directory/'assembling.tmp',directory/f'reads{suffix}'):
            try:
                info=path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_nlink==1:
                allocated+=min(info.st_size,row['size_bytes'])
    # Scratch and publication already consume physical free space. They are
    # verified or exclusively rebuilt on completion, not allocated twice.
    return max(0,2*row['size_bytes']-stored-allocated)


def reserved_upload_bytes(settings):
    """Read durable reservations without creating a DB during read-only preflight."""
    if not settings.database_path.exists():
        return 0
    with closing(sqlite3.connect(settings.database_path.absolute().as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='upload_sessions'").fetchone():
            return 0
        reserved = 0
        for row in db.execute("SELECT * FROM upload_sessions WHERE status='finalizing' OR (status='open' AND expires_at>?)", (time.time(),)):
            if not _live(settings,row):
                continue
            reserved += _remaining(settings,row)
        return reserved


class ResumableUploadService:
    def __init__(self, settings, repository):
        self.settings, self.repository = settings, repository
        with repository._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS upload_sessions (
                id TEXT PRIMARY KEY, token_sha TEXT NOT NULL, original_name TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, chunk_bytes INTEGER NOT NULL,
                hashes_json TEXT NOT NULL, fingerprint TEXT NOT NULL,
                received_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'open',
                upload_json TEXT)''')
            if 'expires_at' not in {r[1] for r in db.execute('PRAGMA table_info(upload_sessions)')}:
                db.execute('ALTER TABLE upload_sessions ADD COLUMN expires_at REAL NOT NULL DEFAULT 0')
                db.execute('UPDATE upload_sessions SET expires_at=?', (time.time()+86400,))

    def config(self):
        return {'chunk_bytes': self.settings.upload_chunk_bytes,
                'max_file_bytes': self.settings.max_upload_bytes, 'parallel_files': 2}

    def _directory(self, session_id):
        if not re.fullmatch(r'[0-9a-f]{32}', session_id):
            raise KeyError('upload session unavailable')
        root = self.settings.input_root.resolve(strict=True)
        current = root
        for part in ('upload_sessions', session_id):
            current = current / part
            if current.is_symlink():
                raise TaskValidationError('unsafe upload session path')
            current.mkdir(exist_ok=True)
            if not current.is_dir() or current.resolve().parent != current.parent.resolve():
                raise TaskValidationError('unsafe upload session path')
        return current

    def _row(self, session_id, token):
        with self.repository._connect() as db:
            row = db.execute('SELECT * FROM upload_sessions WHERE id=?', (session_id,)).fetchone()
        digest = hashlib.sha256((token or '').encode()).hexdigest()
        if row is None or not secrets.compare_digest(row['token_sha'], digest):
            raise KeyError('upload session unavailable')
        return dict(row)

    def _space(self):
        # Reservation is remaining peak space, in addition to bytes already on disk.
        reserved = reserved_upload_bytes(self.settings)
        return shutil.disk_usage(self.settings.input_root).free - reserved - self.settings.minimum_free_disk_bytes

    def create(self, original_name, size_bytes, chunk_sha256, fingerprint):
        name = _safe_original_name(original_name)
        if not any(name.lower().endswith(s) for s in FASTQ_SUFFIXES):
            raise TaskValidationError('file must have a FASTQ suffix')
        chunk_bytes = self.settings.upload_chunk_bytes
        count = (size_bytes + chunk_bytes - 1) // chunk_bytes
        if not 1 <= size_bytes <= self.settings.max_upload_bytes or count != len(chunk_sha256):
            raise TaskValidationError('file size or chunk count is invalid')
        if any(not re.fullmatch('[0-9a-f]{64}', h) for h in chunk_sha256):
            raise TaskValidationError('invalid chunk SHA-256')
        expected = hashlib.sha256(f"{size_bytes}:{chunk_bytes}:{':'.join(chunk_sha256)}".encode()).hexdigest()
        if not secrets.compare_digest(expected, fingerprint):
            raise TaskValidationError('content fingerprint does not match chunk hashes')
        with input_lock(self.settings):
            with self.repository._connect() as db:
                active = self._active_count(db)
            if active >= self.settings.max_upload_sessions:
                raise UploadConflictError('too many unfinished uploads; resume an existing upload')
            if self._space() < 2 * size_bytes:
                raise UploadSpaceError('insufficient disk space after unfinished upload reservations and safety floor')
            session_id, token = uuid.uuid4().hex, secrets.token_hex(32)
            self._directory(session_id)
            with self.repository._connect() as db:
                db.execute('''INSERT INTO upload_sessions
                    (id,token_sha,original_name,size_bytes,chunk_bytes,hashes_json,fingerprint,expires_at)
                    VALUES (?,?,?,?,?,?,?,?)''', (session_id, hashlib.sha256(token.encode()).hexdigest(),
                    name, size_bytes, chunk_bytes, json.dumps(chunk_sha256), fingerprint, time.time()+86400))
            return {**self.get(session_id, token), 'token': token}

    def get(self, session_id, token):
        row = self._row(session_id, token)
        if row['status'] == 'completed':
            self._published(row)
        status = row['status'] if row['status']=='completed' or _live(self.settings,row) else 'parked'
        received = json.loads(row['received_json'])
        return {'id': row['id'], 'original_name': row['original_name'], 'size_bytes': row['size_bytes'],
                'chunk_bytes': row['chunk_bytes'], 'fingerprint': row['fingerprint'], 'status': status,
                'received_chunks': received,
                'received_bytes': sum(min(row['chunk_bytes'], row['size_bytes'] - i * row['chunk_bytes']) for i in received),
                'upload': json.loads(row['upload_json']) if row['upload_json'] else None}

    def _active_count(self, db):
        return sum(_live(self.settings,row) for row in db.execute("SELECT * FROM upload_sessions WHERE status IN ('open','finalizing')"))

    def park(self, session_id, token):
        with input_lock(self.settings):
            row=self._row(session_id, token)
            if row['status']=='completed':
                return {'id':session_id,'status':'completed'}  # No live reservation, even if publication was lost.
            if row['status']=='finalizing' and _finalizer_running(self.settings,row):
                raise UploadConflictError('upload is being finalized; wait before releasing reservation')
            if row['status']!='completed':
                with self.repository._connect() as db:
                    db.execute("UPDATE upload_sessions SET status='parked' WHERE id=?", (session_id,))
            return self.get(session_id, token)

    def resume(self, session_id, token):
        with input_lock(self.settings):
            row=self._row(session_id, token)
            if row['status']!='completed' and not _live(self.settings,row):
                with self.repository._connect() as db:
                    if self._active_count(db)>=self.settings.max_upload_sessions:
                        raise UploadConflictError('too many unfinished uploads; release another reservation first')
                if self._space()<_remaining(self.settings,row):
                    raise UploadSpaceError('insufficient disk space to reacquire upload reservation')
                with self.repository._connect() as db:
                    db.execute("UPDATE upload_sessions SET status='open',expires_at=? WHERE id=?", (time.time()+86400,session_id))
            return self.get(session_id, token)

    def _published(self, row):
        published=self.repository.get_upload(row['id'])
        if published is None:
            raise UploadConflictError('published upload is unavailable')
        path=Path(published['stored_path'])
        try:
            path.resolve(strict=True).relative_to(self.settings.input_root.resolve(strict=True))
            if path.is_symlink() or not path.is_file() or path.stat().st_size!=row['size_bytes']:
                raise OSError('published file changed')
        except (OSError, ValueError):
            raise UploadConflictError('published upload is unavailable')
        return json.loads(row['upload_json'])

    def chunk_size(self, session_id, token, index):
        row = self._row(session_id, token)
        if index < 0 or index >= len(json.loads(row['hashes_json'])):
            raise TaskValidationError('chunk index is invalid')
        return min(row['chunk_bytes'], row['size_bytes'] - index * row['chunk_bytes'])

    def put(self, session_id, token, index, data):
        with input_lock(self.settings):
            row = self._row(session_id, token)
            expected_size = self.chunk_size(session_id, token, index)
            hashes = json.loads(row['hashes_json'])
            if len(data) != expected_size or hashlib.sha256(data).hexdigest() != hashes[index]:
                raise TaskValidationError('chunk size or SHA-256 mismatch; reselect the original file')
            if row['status'] == 'completed':
                return self.get(session_id, token)
            if row['status'] != 'open' or row['expires_at']<=time.time():
                raise UploadConflictError('upload must be resumed before receiving chunks')
            directory = self._directory(session_id)
            destination = directory / f'{index:08d}.part'
            if destination.is_symlink():
                raise TaskValidationError('unsafe chunk path')
            if destination.exists():
                if destination.stat().st_size != expected_size or hashlib.sha256(destination.read_bytes()).hexdigest() != hashes[index]:
                    raise UploadConflictError('stored chunk is corrupt; manual inspection required')
            else:
                if self._space() < 0:
                    raise UploadSpaceError('disk free space fell below reserved upload requirement')
                temporary = directory / (uuid.uuid4().hex + '.tmp')
                try:
                    with temporary.open('xb') as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
            received = sorted(set(json.loads(row['received_json'])) | {index})
            with self.repository._connect() as db:
                db.execute('UPDATE upload_sessions SET received_json=?,expires_at=? WHERE id=?', (json.dumps(received), time.time()+86400,session_id))
            return self.get(session_id, token)

    def complete(self, session_id, token):
        # Per-session flock survives workers and is released by the OS on crashes.
        directory = self._directory(self._row(session_id, token)['id'])
        lock_path = directory / 'finalize.lock'
        if lock_path.is_symlink():
            raise TaskValidationError('unsafe finalize lock')
        import fcntl
        with lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._complete_locked(session_id, token, directory)

    def _complete_locked(self, session_id, token, directory):
        row=self._row(session_id, token)
        suffix = next(s for s in FASTQ_SUFFIXES if row['original_name'].lower().endswith(s))
        root = self.settings.input_root.resolve(strict=True)
        parent = root / 'uploads'
        upload_dir = parent / session_id
        for path in (parent, upload_dir):
            if path.is_symlink():
                raise TaskValidationError('unsafe upload destination')
            path.mkdir(exist_ok=True)
        destination, temporary = upload_dir / f'reads{suffix}', upload_dir / 'assembling.tmp'
        with input_lock(self.settings):
            row = self._row(session_id, token)
            if row['status'] == 'completed':
                return self._published(row)
            if row['status']=='parked' or row['expires_at']<=time.time():
                raise UploadConflictError('upload must be resumed before finalizing')
            hashes = json.loads(row['hashes_json'])
            if json.loads(row['received_json']) != list(range(len(hashes))):
                raise UploadConflictError('upload is incomplete')
            for path in (destination,temporary):
                if path.is_symlink():
                    raise TaskValidationError('unsafe upload destination')
                if path.exists():
                    info=path.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                        raise TaskValidationError('unsafe linked or nonregular upload destination')
            # Scratch is private and unpublished. Remove its entry, never truncate
            # a possibly linked file, before rechecking remaining peak space.
            temporary.unlink(missing_ok=True)
            if not destination.exists() and self._space() < 0:
                raise UploadSpaceError('insufficient free space to assemble upload')
            with self.repository._connect() as db:
                db.execute("UPDATE upload_sessions SET status='finalizing' WHERE id=?", (session_id,))
        try:
            digest, size = hashlib.sha256(), 0
            # A rename-before-registration crash already has the whole file;
            # verify it against the original chunk manifest without another copy.
            recovered=destination.exists()
            from contextlib import nullcontext
            with nullcontext(None) if recovered else temporary.open('xb') as output:
                for index, expected in enumerate(hashes):
                    part = directory / f'{index:08d}.part'
                    if part.is_symlink() or not part.is_file():
                        raise UploadConflictError('stored chunk is unavailable')
                    part_digest, part_size = hashlib.sha256(), 0
                    with part.open('rb') as source:
                        while chunk := source.read(1024 * 1024):
                            part_digest.update(chunk)
                            part_size += len(chunk)
                            if part_size > min(row['chunk_bytes'], row['size_bytes'] - index * row['chunk_bytes']):
                                raise UploadConflictError('stored chunk size changed')
                            digest.update(chunk)
                            size += len(chunk)
                            if output is not None:
                                output.write(chunk)
                    if part_digest.hexdigest() != expected:
                        raise UploadConflictError('stored chunk SHA-256 changed')
                if output is not None:
                    output.flush()
                    os.fsync(output.fileno())
            if size != row['size_bytes']:
                raise UploadConflictError('assembled size mismatch')
            if recovered:
                found=hashlib.sha256()
                with destination.open('rb') as source:
                    while chunk:=source.read(1024*1024):
                        found.update(chunk)
                if destination.stat().st_size!=size or found.hexdigest()!=digest.hexdigest():
                    raise UploadConflictError('existing destination differs; refusing overwrite')
            _validate_first_fastq_record(destination if recovered else temporary, compressed=suffix.endswith('.gz'))
            with input_lock(self.settings):
                existing = self.repository.get_upload(session_id)
                if existing:
                    if existing['sha256'] != digest.hexdigest() or existing['stored_path'] != str(destination):
                        raise UploadConflictError('existing upload differs; refusing overwrite')
                if not destination.exists():
                    os.replace(temporary, destination)
                if not existing:
                    self.repository.create_upload(upload_id=session_id, original_name=row['original_name'],
                        stored_path=str(destination), size_bytes=size, sha256=digest.hexdigest())
                result = {'id': session_id, 'original_name': row['original_name'], 'size': size,
                          'checksum': 'sha256:' + digest.hexdigest(), 'status': 'validated'}
                with self.repository._connect() as db:
                    db.execute("UPDATE upload_sessions SET status='completed',upload_json=? WHERE id=?",
                               (json.dumps(result), session_id))
                # Only transient parts created by this session, after durable publication.
                for index in range(len(hashes)):
                    (directory / f'{index:08d}.part').unlink(missing_ok=True)
                temporary.unlink(missing_ok=True)
                return result
        except Exception:
            with input_lock(self.settings), self.repository._connect() as db:
                db.execute("UPDATE upload_sessions SET status='open' WHERE id=? AND status!='completed'", (session_id,))
            raise
