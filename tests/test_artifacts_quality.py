"""Independent artifact protocol counterexamples; all task evidence is synthetic."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from worker.artifacts import CHUNK_BYTES, MAX_BYTES
from worker.db import Store
from worker.demo import SHA, seed, review_one
from worker.service import Application, Server
from worker.workflow import Workflow


class ArtifactQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'qc.db'
        self.s = Store(self.path)
        seed(self.s)
        workflow = Workflow(self.s)
        review_one(workflow)
        self.eid = workflow.apply('experience-create', {'project_id': 101, 'audience': 'agent', 'spec': {
            'scenario': 'synthetic QC', 'success_criteria': 'fixture matches', 'environment': 'offline',
            'input': 'fixture', 'budget': {'seconds': 30}}}, 'reviewer')['id']
        self.tokens = {role: role + '-' + 'q' * 48 for role in ('agent', 'reviewer')}
        self.app = Application(self.path, {'users': [{'actor': role, 'roles': [role],
            'token_sha256': hashlib.sha256(token.encode()).hexdigest()} for role, token in self.tokens.items()]})
        self.claim = self.call('/api/agent/claim', {})

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def call(self, path, body, role='agent'):
        return self.app.handle('POST', path, 'Bearer ' + self.tokens[role], body, str(uuid.uuid4()))[1]

    def upload(self, content, path='qc.txt', index=0, **overrides):
        return self.call(f'/api/agent/tasks/{self.eid}/artifacts', {
            'lease_token': self.claim['lease_token'], 'run_id': self.claim['task']['run_id'],
            'path': path, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
            'chunk_index': index, 'total_chunks': max(1, (len(content) + CHUNK_BYTES - 1) // CHUNK_BYTES),
            'data_base64': base64.b64encode(content[index * CHUNK_BYTES:(index + 1) * CHUNK_BYTES]).decode(), **overrides})

    def result(self, paths):
        return self.call(f'/api/agent/tasks/{self.eid}/result', {
            'id': self.eid, 'sha': SHA, 'lease_token': self.claim['lease_token'],
            'run_id': self.claim['task']['run_id'], 'task_sha256': self.claim['task_sha256'],
            'result': {'success': True, 'executor': 'agent', 'environment': 'synthetic',
                'steps': ['synthetic protocol check'], 'actual_result': 'fixture output',
                'artifacts': paths, 'failure_kind': 'none'}})

    def test_out_of_order_chunks_have_ordered_digest_and_only_one_completion_event(self):
        content = b'a' * CHUNK_BYTES + b'b' * CHUNK_BYTES + b'last'
        self.assertEqual(self.upload(content, index=2)['status'], 'partial')
        self.assertEqual(self.upload(content, index=0)['status'], 'partial')
        final = self.upload(content, index=1)
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(self.upload(content, index=1), final)
        self.assertEqual(bytes(self.s.one('SELECT data FROM artifact_files')['data']), content)
        self.assertEqual(self.s.one("SELECT COUNT(*) AS n FROM events WHERE action='artifact_completed'")['n'], 1)

    def test_missing_later_artifact_and_duplicate_paths_do_not_partially_commit_result(self):
        self.upload(b'first')
        for paths in (['qc.txt', 'missing.txt'], ['qc.txt', 'qc.txt']):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.result(paths)
            self.assertEqual(self.s.all('SELECT * FROM artifact_links'), [])
            self.assertEqual(self.s.one('SELECT status FROM experiences WHERE id=?', (self.eid,))['status'], 'pending')
            self.assertEqual(self.s.one('SELECT state FROM agent_leases WHERE experience_id=?', (self.eid,))['state'], 'running')
        self.assertTrue(self.result(['qc.txt'])['success'])

    def test_concurrent_reservations_cannot_exceed_byte_quota(self):
        barrier = threading.Barrier(2)
        size = 40 * 1024 * 1024
        def reserve(name):
            barrier.wait(timeout=3)
            try:
                return self.upload(b'x' * CHUNK_BYTES, path=name, size=size, sha256='0' * 64,
                    total_chunks=(size + CHUNK_BYTES - 1) // CHUNK_BYTES)['status']
            except ValueError as error:
                self.assertIn('quota', str(error))
                return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(reserve, ('one.txt', 'two.txt')))
        self.assertCountEqual(statuses, ['partial', 'rejected'])
        totals = self.s.one('SELECT SUM(size) AS size,COUNT(*) AS n FROM artifact_uploads')
        self.assertLessEqual(totals['size'], MAX_BYTES)
        self.assertEqual(totals['n'], 1)
        self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM artifact_chunks')['n'], 1)

    def test_http_cookie_download_uses_no_url_token_and_logout_revokes_access(self):
        content = b'<script>window.shouldNeverRun=true</script> synthetic QC\n'
        artifact = self.upload(content, path='proof/\u8bc1\u636e"; download.html')
        self.result(['proof/\u8bc1\u636e"; download.html'])
        server = Server(('127.0.0.1', 0), self.app)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            try:
                connection.request(method, path, body=json.dumps(body) if body is not None else None,
                    headers={'Content-Type': 'application/json', **(headers or {})})
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()
        try:
            status, headers, payload = request('POST', '/auth/login', {'token': self.tokens['reviewer']}, {'Origin': origin})
            self.assertEqual(status, 200)
            self.assertIn('HttpOnly', headers['Set-Cookie'])
            session = json.loads(payload)
            cookie = headers['Set-Cookie'].split(';', 1)[0]
            url = '/api/artifacts/' + artifact['id'] + '/download'
            self.assertNotIn('?', url)
            self.assertEqual(request('GET', url)[0], 401)
            status, headers, payload = request('GET', url, headers={'Cookie': cookie})
            self.assertEqual(status, 200)
            self.assertEqual(payload, content)
            self.assertEqual(headers['X-Artifact-SHA256'], hashlib.sha256(payload).hexdigest())
            self.assertIn('%22%3B%20download.html', headers['Content-Disposition'])
            self.assertEqual(headers['Content-Type'], 'application/octet-stream')
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(request('POST', '/auth/logout', {}, {'Cookie': cookie, 'Origin': origin,
                'X-CSRF-Token': session['csrf_token']})[0], 200)
            self.assertEqual(request('GET', url, headers={'Cookie': cookie})[0], 401)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
