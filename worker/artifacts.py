"""Private SQLite evidence files, uploaded in bounded immutable chunks.

No supplied path is ever opened on the service filesystem. Authorization and
current Agent lease validation happen in service.agent before each upload.
"""
import base64
import binascii
import hashlib
from pathlib import PurePosixPath
import re
import secrets
import sqlite3
from urllib.parse import quote
from .db import Store, now

CHUNK_BYTES = 384 * 1024
MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 100


def artifact_path(value):
    if (not isinstance(value, str) or not value or len(value.encode('utf-8')) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in value) or '\\' in value or ':' in value):
        raise ValueError('Artifact path must be a bounded canonical relative POSIX path')
    path = PurePosixPath(value)
    if (path.is_absolute() or '..' in path.parts or path.as_posix() != value
            or value in ('.', 'result.json') or len(path.parts) > 16):
        raise ValueError('Artifact path must be a canonical relative POSIX file path')
    return value


class ArtifactStore:
    def __init__(self, store):
        self.s = store

    def upload(self, experience_id, run_id, actor, data):
        path = artifact_path(data.get('path'))
        if data.get('run_id') != run_id:
            raise ValueError('Artifact run_id must match the current leased run')
        digest, size = data.get('sha256'), data.get('size')
        index, count = data.get('chunk_index'), data.get('total_chunks')
        encoded = data.get('data_base64')
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('Artifact sha256 must be a lowercase SHA-256 digest')
        if (type(size) is not int or not 0 <= size <= MAX_BYTES or type(index) is not int or type(count) is not int
                or count != max(1, (size + CHUNK_BYTES - 1) // CHUNK_BYTES) or not 0 <= index < count):
            raise ValueError('Artifact size and fixed-size chunk metadata are invalid')
        if not isinstance(encoded, str) or len(encoded) > ((CHUNK_BYTES + 2) // 3) * 4:
            raise ValueError('Artifact chunk exceeds the encoded size limit')
        try:
            chunk = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('Artifact chunk must be valid base64') from None
        expected = min(CHUNK_BYTES, max(0, size - index * CHUNK_BYTES))
        if len(chunk) != expected or base64.b64encode(chunk).decode('ascii') != encoded:
            raise ValueError('Artifact chunk has the wrong size or noncanonical base64 encoding')
        with self.s.transaction():
            row = self.s.one('SELECT * FROM artifact_uploads WHERE experience_id=? AND run_id=? AND path=?', (experience_id, run_id, path))
            if row:
                if (row['sha256'], row['size'], row['total_chunks'], row['actor']) != (digest, size, count, actor):
                    raise ValueError('Artifact upload metadata is immutable')
            else:
                totals = self.s.one('SELECT COUNT(*) AS count,COALESCE(SUM(size),0) AS size FROM artifact_uploads WHERE experience_id=?', (experience_id,))
                if totals['count'] >= MAX_FILES or totals['size'] + size > MAX_BYTES:
                    raise ValueError('Experience artifact quota exceeded: 100 files and 64 MiB including reserved uploads')
                identifier = secrets.token_hex(16)
                self.s.conn.execute('INSERT INTO artifact_uploads VALUES(?,?,?,?,?,?,?,?,?)',
                    (identifier, experience_id, run_id, path, digest, size, count, actor, now()))
                row = self.s.one('SELECT * FROM artifact_uploads WHERE id=?', (identifier,))
            complete = self.s.one('SELECT id FROM artifact_files WHERE id=?', (row['id'],))
            if complete:
                existing = self.s.one('SELECT substr(data,?,?) AS data FROM artifact_files WHERE id=?', (index * CHUNK_BYTES + 1, expected, row['id']))
                if existing['data'] != chunk:
                    raise ValueError('Completed artifact bytes are immutable')
            else:
                existing = self.s.one('SELECT data FROM artifact_chunks WHERE upload_id=? AND chunk_index=?', (row['id'], index))
                if existing and existing['data'] != chunk:
                    raise ValueError('Previously uploaded chunk bytes are immutable')
                if not existing:
                    self.s.conn.execute('INSERT INTO artifact_chunks VALUES(?,?,?)', (row['id'], index, sqlite3.Binary(chunk)))
                received = self.s.one('SELECT COUNT(*) AS count FROM artifact_chunks WHERE upload_id=?', (row['id'],))['count']
                if received == count:
                    # Allocate and stream in the same transaction: no 64 MiB Python
                    # join, and a hash mismatch rolls back completion + this chunk.
                    cursor = self.s.conn.execute('INSERT INTO artifact_files(id,data,created_at) VALUES(?,zeroblob(?),?)', (row['id'], size, now()))
                    hasher, total = hashlib.sha256(), 0
                    with self.s.conn.blobopen('artifact_files', 'data', cursor.lastrowid) as blob:
                        for part in self.s.conn.execute('SELECT data FROM artifact_chunks WHERE upload_id=? ORDER BY chunk_index', (row['id'],)):
                            blob.write(part['data'])
                            hasher.update(part['data'])
                            total += len(part['data'])
                    if total != size or hasher.hexdigest() != digest:
                        raise ValueError('Completed artifact size or SHA-256 does not match the declared evidence')
                    self.s.conn.execute('DELETE FROM artifact_chunks WHERE upload_id=?', (row['id'],))
                    self.s.event('experience', experience_id, 'artifact_completed', actor,
                                 {'artifact_id': row['id'], 'run_id': run_id, 'path': path, 'size': size, 'sha256': digest})
                    complete = True
            return {'id': row['id'], 'experience_id': experience_id, 'run_id': run_id, 'path': path,
                    'sha256': digest, 'size': size, 'chunk_index': index, 'total_chunks': count,
                    'status': 'completed' if complete else 'partial'}

    def list(self, experience_id):
        rows = self.s.all('SELECT u.id,u.experience_id,u.run_id,u.path,u.sha256,u.size,u.actor,f.created_at,EXISTS(SELECT 1 FROM artifact_links l WHERE l.artifact_id=u.id AND l.experience_id=u.experience_id) AS referenced FROM artifact_uploads u JOIN artifact_files f ON f.id=u.id WHERE u.experience_id=? ORDER BY f.created_at,u.id', (experience_id,))
        for row in rows:
            row['referenced'] = bool(row['referenced'])
            row['download_url'] = '/api/artifacts/' + row['id'] + '/download'
        return rows

    def link_result(self, experience_id, run_id, paths):
        if not isinstance(paths, list) or len(paths) > MAX_FILES:
            raise ValueError('Artifact result paths must be a bounded list')
        identifiers, seen = [], set()
        for value in paths:
            path = artifact_path(value)
            if path in seen:
                raise ValueError('Result artifact paths must be unique')
            seen.add(path)
            row = self.s.one('SELECT u.id FROM artifact_uploads u JOIN artifact_files f ON f.id=u.id WHERE u.experience_id=? AND u.run_id=? AND u.path=?', (experience_id, run_id, path))
            if not row:
                raise ValueError('Result references an artifact that is not completed for this leased run: ' + path)
            identifiers.append(row['id'])
        for identifier in identifiers:
            self.s.conn.execute('INSERT INTO artifact_links(experience_id,artifact_id) VALUES(?,?)', (experience_id, identifier))
        return identifiers


class ArtifactDownload:
    """Stream immutable SQLite bytes one chunk at a time after role validation."""
    def __init__(self, db_path, metadata):
        self.db_path, self.metadata = db_path, metadata
        self.size = metadata['size']
        self.content_disposition = ('attachment; filename="artifact-' + metadata['id'] + '.bin"; filename*=UTF-8\'\''
                                    + quote(PurePosixPath(metadata['path']).name, safe=''))

    def chunks(self):
        store = Store(self.db_path)
        try:
            for offset in range(0, self.size, CHUNK_BYTES):
                row = store.one('SELECT substr(data,?,?) AS data FROM artifact_files WHERE id=?', (offset + 1, min(CHUNK_BYTES, self.size - offset), self.metadata['id']))
                if row is None:
                    raise ValueError('Artifact was removed during download')
                yield row['data']
        finally:
            store.close()
