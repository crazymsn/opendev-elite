"""Bounded remote Agent consumer with private outbox and idempotent recovery."""
import contextlib
import base64
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .db import dump
from .deployment import _private_directory, local_root
from .runner import CleanupError, MAX_ARTIFACT_BYTES, resource_limits, run_exported_task, safe_path, validate_result

CHUNK_BYTES = 384 * 1024


class AgentClientError(Exception):
    def __init__(self, status=0):
        self.status = status
        super().__init__('Agent service request failed' + (f' (HTTP {status})' if status else ' (transport unavailable)'))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class AgentService:
    def __init__(self, base_url, token_file, transport=None):
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('', '/')
                or (parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'))):
            raise ValueError('Use an HTTPS service origin or loopback HTTP origin without credentials, paths or query')
        _ = parsed.port
        self.base = base_url.rstrip('/')
        token_path = local_root(token_file)
        self.token = token_path.read_text(encoding='utf-8').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', self.token):
            raise ValueError('Invalid Agent credential file')
        self.fingerprint = hashlib.sha256(self.token.encode()).hexdigest()
        self.transport = transport or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open

    def post(self, path, body, key):
        # At most one automatic retry, always with the exact same key and bytes.
        request = urllib.request.Request(self.base + path, data=dump(body).encode('utf-8'), method='POST',
                                         headers={'Authorization': 'Bearer ' + self.token,
                                                  'Content-Type': 'application/json', 'Idempotency-Key': key})
        for attempt in range(2):
            try:
                with self.transport(request, timeout=10) as response:
                    raw = response.read(16 * 1024 * 1024 + 1)
                    if len(raw) > 16 * 1024 * 1024:
                        raise AgentClientError()
                    def invalid(_):
                        raise ValueError('non-finite response')
                    value = json.loads(raw.decode('utf-8'), parse_constant=invalid)
                    if not isinstance(value, dict):
                        raise AgentClientError()
                    return value
            except urllib.error.HTTPError as exc:
                if attempt == 0 and exc.code in (429, 502, 503, 504):
                    continue
                raise AgentClientError(exc.code) from None
            except (urllib.error.URLError, OSError, ValueError):
                if attempt == 0:
                    continue
                raise AgentClientError() from None


def _save(path, value):
    # Use a unique, exclusive temporary file.  Reusing a predictable
    # ``.tmp`` path with O_TRUNC would allow a same-user symlink to redirect
    # sensitive outbox state during recovery.
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(dump(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextlib.contextmanager
def _locked(root):
    with (root / '.agent-client.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Another Agent client owns this output directory') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class _Lease:
    def __init__(self, service, claim, interval):
        self.service, self.claim = service, claim
        self.until = claim['lease_until']
        self.interval = min(interval, max(1, (self.until - time.time()) / 3))
        self.finished, self.failed = threading.Event(), threading.Event()
        self.thread = None

    def heartbeat(self):
        try:
            reply = self.service.post('/api/agent/tasks/' + self.claim['task']['id'] + '/heartbeat',
                                      {'lease_token': self.claim['lease_token']}, 'heartbeat-' + uuid.uuid4().hex)
            until = reply.get('lease_until')
            if type(until) not in (int, float) or not math.isfinite(until) or until <= time.time():
                raise AgentClientError()
            self.until = until
        except AgentClientError:
            self.failed.set()
            raise

    def check(self):
        if self.failed.is_set() or self.until <= time.time():
            raise ValueError('Agent lease is unavailable; result submission is blocked')

    def start(self):
        self.heartbeat()
        def maintain():
            while not self.finished.wait(self.interval):
                try:
                    self.heartbeat()
                except AgentClientError:
                    return
        self.thread = threading.Thread(target=maintain, daemon=True)
        self.thread.start()

    def close(self):
        self.finished.set()
        if self.thread:
            self.thread.join(timeout=25)
            if self.thread.is_alive():
                self.failed.set()


def _bound_claim(value):
    if 'task' not in value:
        raise ValueError('Claim response must explicitly contain task or task:null')
    task = value.get('task')
    if task is None:
        return False
    if (not isinstance(task, dict) or task.get('schema_version') != 2 or not isinstance(task.get('id'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]+', task['id'])
            or not isinstance(task.get('run_id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]+', task['run_id'])
            or not isinstance(task.get('sha'), str) or not re.fullmatch(r'[a-f0-9]{40,64}', task['sha'])
            or hashlib.sha256(dump(task).encode()).hexdigest() != value.get('task_sha256')
            or not isinstance(value.get('lease_token'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', value['lease_token'])
            or type(value.get('lease_until')) not in (int, float)
            or not math.isfinite(value['lease_until']) or value['lease_until'] <= time.time()):
        raise ValueError('Invalid or expired server task/lease binding')
    return True


def _public(state, path):
    return {'status': state['state'], 'experience_id': (state.get('claim', {}).get('task') or {}).get('id'),
            'outbox': str(path), 'requires_reconciliation': state['state'] not in ('submitted', 'idle')}


def _file_digest(path):
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while chunk := stream.read(CHUNK_BYTES):
            size += len(chunk)
            if size > MAX_ARTIFACT_BYTES:
                raise ValueError('Artifact exceeds 64 MiB')
            digest.update(chunk)
    return size, digest.hexdigest()


def _artifact_manifest(state, root):
    output = safe_path(root / state['local_run'] / 'output')
    claim, result = state['claim'], state['result']
    validate_result(claim['task'], result, output, claim['task_sha256'])
    manifest, seen = [], set()
    for relative in result['result']['artifacts']:
        if relative in seen:
            continue
        seen.add(relative)
        size, digest = _file_digest(safe_path(output / relative))
        prefix = hashlib.sha256((state['local_run'] + '\n' + relative + '\n' + digest).encode()).hexdigest()
        manifest.append({'path': relative, 'size': size, 'sha256': digest,
                         'total_chunks': max(1, (size + CHUNK_BYTES - 1) // CHUNK_BYTES),
                         'next_chunk': 0, 'request_prefix': 'artifact-' + prefix, 'artifact_id': None})
    return manifest


def _upload_artifacts(service, root, state, state_path, lease):
    claim = state['claim']
    output = safe_path(root / state['local_run'] / 'output')
    # Recheck every referenced file on resume, including already uploaded files.
    # A changed local artifact must never become a different payload under a key.
    validate_result(claim['task'], state['result'], output, claim['task_sha256'])
    for artifact in state['artifacts']:
        path = safe_path(output / artifact['path'])
        if _file_digest(path) != (artifact['size'], artifact['sha256']):
            raise ValueError('Artifact changed after manifest creation; do not reuse its upload key')
        with path.open('rb') as stream:
            for index in range(artifact['next_chunk'], artifact['total_chunks']):
                lease.check()
                stream.seek(index * CHUNK_BYTES)
                chunk = stream.read(CHUNK_BYTES)
                expected_bytes = min(CHUNK_BYTES, max(0, artifact['size'] - index * CHUNK_BYTES))
                if len(chunk) != expected_bytes:
                    raise ValueError('Artifact size changed during upload')
                body = {'lease_token': claim['lease_token'], 'run_id': claim['task']['run_id'],
                        'path': artifact['path'], 'sha256': artifact['sha256'], 'size': artifact['size'],
                        'chunk_index': index, 'total_chunks': artifact['total_chunks'],
                        'data_base64': base64.b64encode(chunk).decode('ascii')}
                key = artifact['request_prefix'] + ':' + str(index)
                state.update(state='uploading', active_upload_key=key)
                _save(state_path, state)
                try:
                    ack = service.post('/api/agent/tasks/' + claim['task']['id'] + '/artifacts', body, key)
                except AgentClientError as exc:
                    state.update(state='upload_unknown', last_http_status=exc.status)
                    _save(state_path, state)
                    return False
                last = index == artifact['total_chunks'] - 1
                valid = (isinstance(ack.get('id'), str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,128}', ack['id']))
                         and ack.get('experience_id') == claim['task']['id']
                         and (artifact['artifact_id'] is None or artifact['artifact_id'] == ack['id'])
                         and ack.get('run_id') == claim['task']['run_id'] and ack.get('path') == artifact['path']
                         and ack.get('sha256') == artifact['sha256'] and type(ack.get('size')) is int and ack['size'] == artifact['size']
                         and type(ack.get('chunk_index')) is int and ack['chunk_index'] == index
                         and type(ack.get('total_chunks')) is int and ack['total_chunks'] == artifact['total_chunks']
                         and ack.get('status') == ('completed' if last else 'partial'))
                if not valid:
                    state.update(state='upload_unknown', last_http_status=200)
                    _save(state_path, state)
                    return False
                artifact.update(next_chunk=index + 1, artifact_id=ack['id'])
                _save(state_path, state)
                lease.check()
    state['state'] = 'result_ready'
    _save(state_path, state)
    return True


def _consume_one(service, root, state_path, config, heartbeat_seconds, resume):
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, ValueError, TypeError):
            raise ValueError('Agent outbox state is unreadable or malformed; inspect it before continuing') from None
        if not isinstance(state, dict) or not isinstance(state.get('state'), str):
            raise ValueError('Agent outbox state is malformed; inspect it before continuing')
    else:
        state = None
    if state and state['state'] not in ('submitted', 'idle'):
        if state['service'] != service.base or state['credential_sha256'] != service.fingerprint:
            raise ValueError('Pending outbox belongs to another service or Agent credential')
        if not resume or state['state'] not in ('claiming', 'claim_unknown', 'preparing_artifacts', 'uploading', 'upload_unknown', 'result_ready', 'submitting', 'submit_unknown'):
            return _public(state, state_path)
    else:
        run = uuid.uuid4().hex
        state = {'state': 'claiming', 'service': service.base, 'credential_sha256': service.fingerprint,
                 'claim_key': 'claim-' + run, 'submit_key': 'result-' + run, 'local_run': run}
        _save(state_path, state)
    if state['state'] in ('claiming', 'claim_unknown'):
        try:
            claim = service.post('/api/agent/claim', {}, state['claim_key'])
        except AgentClientError as exc:
            state.update(state='claim_unknown', last_http_status=exc.status)
            _save(state_path, state)
            return _public(state, state_path)
        try:
            has_task = _bound_claim(claim)
        except ValueError:
            state.update(state='claim_unknown', last_http_status=200)
            _save(state_path, state)
            return _public(state, state_path)
        if not has_task:
            state['state'] = 'idle'
            _save(state_path, state)
            return _public(state, state_path)
        state.update(state='executing', claim=claim)
        _save(state_path, state)
        lease = _Lease(service, claim, heartbeat_seconds)
        run_root = root / state['local_run']
        try:
            lease.start()
            try:
                executed = run_exported_task(claim['task'], claim['task_sha256'], run_root, config, lease.check)
                result = executed['result']
            except CleanupError:
                state['state'] = 'cleanup_required'
                _save(state_path, state)
                return _public(state, state_path)
            except (OSError, ValueError) as exc:
                # This is a real execution/protocol/environment failure. Never
                # promote it to a successful product evaluation.
                run_root.mkdir(parents=True, exist_ok=True)
                with (run_root / ('host-failure-' + uuid.uuid4().hex + '.txt')).open('x', encoding='utf-8') as log:
                    log.write(type(exc).__name__ + ': ' + str(exc))
                result = {'id': claim['task']['id'], 'sha': claim['task']['sha'], 'run_id': claim['task']['run_id'],
                          'task_sha256': claim['task_sha256'], 'result': {
                              'success': False, 'executor': 'remote-agent-client', 'environment': 'reviewed offline Docker runner',
                              'steps': ['Attempted bounded execution using the configured pinned image'],
                              'actual_result': type(exc).__name__ + ': ' + str(exc),
                              'artifacts': [], 'failure_kind': 'environment'}}
                validate_result(claim['task'], result, task_sha256=claim['task_sha256'])
            lease.close()
            lease.check()
            lease.heartbeat()
            lease.check()
        except (AgentClientError, ValueError):
            state['state'] = 'lease_lost'
            _save(state_path, state)
            return _public(state, state_path)
        finally:
            lease.close()
        state.update(state='preparing_artifacts', result=result)
        _save(state_path, state)
    if state['state'] == 'preparing_artifacts':
        try:
            state['artifacts'] = _artifact_manifest(state, root)
        except (OSError, ValueError):
            state['state'] = 'artifact_changed'
            _save(state_path, state)
            return _public(state, state_path)
        state['state'] = 'uploading' if state['artifacts'] else 'result_ready'
        _save(state_path, state)
    claim = state['claim']
    validate_result(claim['task'], state['result'], task_sha256=claim['task_sha256'])
    # A response-lost result can be replayed even after the lease expired: the
    # server's idempotency receipt is authoritative. Fresh uploads/results need
    # a current lease and keep heartbeats running through the final ACK.
    replaying_result = state['state'] in ('submitting', 'submit_unknown')
    lease = None if replaying_result else _Lease(service, claim, heartbeat_seconds)
    try:
        if lease:
            try:
                lease.start()
                if state['state'] in ('uploading', 'upload_unknown'):
                    if not _upload_artifacts(service, root, state, state_path, lease):
                        return _public(state, state_path)
                lease.check()
            except AgentClientError:
                state['state'] = 'lease_lost'
                _save(state_path, state)
                return _public(state, state_path)
            except (OSError, ValueError):
                state['state'] = 'lease_lost' if lease.failed.is_set() or lease.until <= time.time() else 'artifact_changed'
                _save(state_path, state)
                return _public(state, state_path)
        return _submit_result(service, root, state, state_path)
    finally:
        if lease:
            lease.close()


def _submit_result(service, root, state, state_path):
    claim = state['claim']
    state['state'] = 'submitting'
    _save(state_path, state)
    body = dict(state['result'], lease_token=claim['lease_token'])
    try:
        receipt = service.post('/api/agent/tasks/' + claim['task']['id'] + '/result', body, state['submit_key'])
    except AgentClientError as exc:
        state.update(state='submit_unknown', last_http_status=exc.status)
        _save(state_path, state)
        return _public(state, state_path)
    acknowledged = (receipt.get('id') == claim['task']['id'] and
                    ((type(receipt.get('success')) is bool and receipt['success'] == state['result']['result']['success'])
                     or (receipt.get('duplicate') is True and 'success' not in receipt)))
    if not acknowledged:
        state.update(state='submit_unknown', last_http_status=200)
        _save(state_path, state)
        return _public(state, state_path)
    state.update(state='submitted', receipt=receipt)
    _save(state_path, state)
    # Keep an audit copy per run; it remains protected and contains no login token.
    _save(root / (state['local_run'] + '.outbox.json'), state)
    return _public(state, state_path)


def consume_tasks(base_url, token_file, destination, config, max_tasks=1,
                  heartbeat_seconds=30, resume=False, transport=None):
    """Consume at most max_tasks; default one, no daemon or external scheduler.

    Pending unknown actions block fresh claims. Explicit resume only replays the
    saved claim/upload/result with its original idempotency key; it never reruns a
    possibly interrupted container. Lease failures block result submission.
    """
    resource_limits(config)
    if type(max_tasks) is not int or not 1 <= max_tasks <= 100:
        raise ValueError('max_tasks must be an integer between 1 and 100')
    if type(heartbeat_seconds) not in (int, float) or not math.isfinite(heartbeat_seconds) or not 1 <= heartbeat_seconds <= 60:
        raise ValueError('heartbeat_seconds must be between 1 and 60')
    root = local_root(destination)
    if any((ancestor / '.git').exists() for ancestor in (root, *root.parents)):
        raise ValueError('Agent outbox must be outside source repositories')
    _private_directory(root)
    root = local_root(root)
    service = AgentService(base_url, token_file, transport)
    results = []
    with _locked(root):
        for _ in range(max_tasks):
            result = _consume_one(service, root, root / 'outbox.json', config, heartbeat_seconds, resume)
            results.append(result)
            if result['status'] != 'submitted':
                break
            resume = False
    return {'tasks': results, 'submitted': sum(r['status'] == 'submitted' for r in results),
            'blocked': any(r['requires_reconciliation'] for r in results), 'outbox_directory': str(root)}
