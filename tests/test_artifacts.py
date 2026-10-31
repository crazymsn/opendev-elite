import base64
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from worker.artifacts import ArtifactDownload, ArtifactStore, CHUNK_BYTES, MAX_BYTES
from worker.db import SCHEMA, SERVICE_SCHEMA, SCHEMA_VERSION, Store
from worker.demo import SHA, seed, review_one
from worker.service import ApiFailure, Application, Server
from worker.workflow import Workflow


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.db'
        self.s = Store(self.path)
        seed(self.s)
        self.w = Workflow(self.s)
        review_one(self.w)
        self.eid = self.w.apply('experience-create', {'project_id': 101, 'audience': 'agent', 'spec': {
            'scenario': 'fixture', 'success_criteria': 'matches fixture', 'environment': 'offline',
            'input': 'fixture', 'budget': {'seconds': 30}}}, 'reviewer')['id']
        self.tokens = {actor: actor + '-' + 'x' * 48 for actor in ('agent', 'other-agent', 'reviewer', 'operator', 'auditor', 'approver')}
        self.config = {'users': [{'actor': actor, 'roles': ['agent' if actor == 'other-agent' else actor],
                         'token_sha256': hashlib.sha256(token.encode()).hexdigest()} for actor, token in self.tokens.items()]}
        self.app = Application(self.path, self.config)
        self.counter = 0
        self.claim = self.call('/api/agent/claim', {})

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()

    def call(self, path, body=None, actor='agent', method='POST'):
        self.counter += 1
        return self.app.handle(method, path, 'Bearer ' + self.tokens[actor], body, 'artifact-test-' + str(self.counter))[1]

    def upload(self, content=b'evidence', path='report.txt', index=0, claim=None, actor='agent', **overrides):
        claim = claim or self.claim
        body = {'lease_token': claim['lease_token'], 'run_id': claim['task']['run_id'], 'path': path,
                'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content),
                'chunk_index': index, 'total_chunks': max(1, (len(content) + CHUNK_BYTES - 1) // CHUNK_BYTES),
                'data_base64': base64.b64encode(content[index * CHUNK_BYTES:(index + 1) * CHUNK_BYTES]).decode(), **overrides}
        return self.call('/api/agent/tasks/' + self.eid + '/artifacts', body, actor)

    def result(self, paths, claim=None, actor='agent', success=True):
        claim = claim or self.claim
        body = {'id': self.eid, 'lease_token': claim['lease_token'], 'run_id': claim['task']['run_id'],
                'sha': SHA, 'task_sha256': claim['task_sha256'], 'result': {'success': success, 'executor': 'claimed-agent',
                'environment': 'offline', 'steps': ['executed'], 'actual_result': 'fixture output',
                'artifacts': paths, 'failure_kind': 'none' if success else 'environment'}}
        return self.call('/api/agent/tasks/' + self.eid + '/result', body, actor)

    def test_partial_is_not_evidence_and_completed_bytes_are_immutable(self):
        data = b'a' * CHUNK_BYTES + b'last'
        first = self.upload(data)
        self.assertEqual(first['status'], 'partial')
        self.assertEqual(self.call('/api/experiences/' + self.eid + '/artifacts', actor='reviewer', method='GET')['items'], [])
        with self.assertRaisesRegex(ValueError, 'not completed'): self.result(['report.txt'])
        self.assertEqual(self.s.all('SELECT * FROM artifact_links'), [])
        self.assertEqual(self.upload(data), first)
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.upload(data, data_base64=base64.b64encode(b'b' * CHUNK_BYTES).decode())
        final = self.upload(data, index=1)
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['id'], first['id'])
        self.assertEqual(self.s.all('SELECT * FROM artifact_chunks'), [])
        self.assertEqual(self.upload(data, index=1), final)
        self.result(['report.txt'])
        items = self.call('/api/experiences/' + self.eid + '/artifacts', actor='reviewer', method='GET')['items']
        self.assertTrue(items[0]['referenced'])
        download = self.call(items[0]['download_url'], actor='reviewer', method='GET')
        self.assertIsInstance(download, ArtifactDownload)
        self.assertEqual(b''.join(download.chunks()), data)

    def test_hash_failure_rolls_back_final_chunk_and_completion(self):
        data = b'a' * CHUNK_BYTES + b'last'
        self.upload(data)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.upload(data, index=1, data_base64=base64.b64encode(b'fake').decode())
        self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM artifact_chunks')['n'], 1)
        self.assertEqual(self.s.all('SELECT * FROM artifact_files'), [])
        self.assertEqual(self.upload(data, index=1)['status'], 'completed')

    def test_traversal_absolute_and_malformed_paths_are_rejected(self):
        for path in ('../secret', '/secret', 'C:/secret', 'a\\b', 'a//b', 'a/./b', 'result.json', 'x\nheader', 'a/' * 18 + 'file', 'a' * 513):
            with self.subTest(path=path), self.assertRaises(ValueError): self.upload(path=path)
        self.assertEqual(self.s.all('SELECT * FROM artifact_uploads'), [])

    def test_wrong_actor_expired_lease_stale_review_and_wrong_run_rejected(self):
        with self.assertRaises(ApiFailure): self.upload(actor='other-agent')
        with self.assertRaisesRegex(ValueError, 'run_id'): self.upload(run_id='old-run')
        self.s.conn.execute('UPDATE agent_leases SET lease_until=0')
        with self.assertRaises(ApiFailure): self.upload()
        self.s.conn.execute('UPDATE agent_leases SET lease_until=9999999999')
        review_one(self.w)
        with self.assertRaises(ApiFailure): self.upload()
        self.assertEqual(self.s.all('SELECT * FROM artifact_uploads'), [])

    def test_previous_run_files_cannot_satisfy_reassigned_run_result(self):
        old = self.upload()
        self.s.conn.execute('UPDATE agent_leases SET lease_until=0')
        new = self.call('/api/agent/claim', {}, actor='other-agent')
        self.assertNotEqual(new['task']['run_id'], self.claim['task']['run_id'])
        with self.assertRaisesRegex(ValueError, 'not completed'):
            self.result(['report.txt'], new, 'other-agent')
        fresh = self.upload(claim=new, actor='other-agent')
        self.assertNotEqual(fresh['id'], old['id'])
        self.result(['report.txt'], new, 'other-agent')
        self.assertEqual(self.s.one('SELECT artifact_id FROM artifact_links')['artifact_id'], fresh['id'])

    def test_empty_failure_artifacts_remain_valid_without_upload(self):
        self.assertFalse(self.result([], success=False)['success'])
        self.assertEqual(self.s.all('SELECT * FROM artifact_links'), [])

    def test_size_metadata_base64_and_chunk_boundaries_are_checked(self):
        for override in ({'size': MAX_BYTES + 1}, {'size': True}, {'chunk_index': True}, {'total_chunks': 2},
                         {'sha256': 'x' * 64}, {'data_base64': '!'}, {'data_base64': 'YQ=='},
                         {'data_base64': 'A' * (CHUNK_BYTES * 2)}):
            with self.subTest(override=tuple(override)), self.assertRaises(ValueError): self.upload(**override)

    def test_per_experience_reservations_enforce_total_byte_quota(self):
        chunk = b'x' * CHUNK_BYTES
        reserved = self.upload(content=chunk, size=MAX_BYTES, sha256='0' * 64,
                               total_chunks=(MAX_BYTES + CHUNK_BYTES - 1) // CHUNK_BYTES)
        self.assertEqual(reserved['status'], 'partial')
        with self.assertRaisesRegex(ValueError, 'quota'):
            self.upload(b'x', path='another.txt')

    def test_zero_byte_files_and_file_count_quota(self):
        for index in range(100):
            self.assertEqual(self.upload(b'', path='empty-' + str(index))['status'], 'completed')
        with self.assertRaisesRegex(ValueError, 'quota'): self.upload(b'', path='one-too-many')

    def test_download_and_metadata_are_authorized_without_invalidating_history(self):
        file = self.upload()
        path = '/api/artifacts/' + file['id'] + '/download'
        for actor in ('agent', 'approver'):
            with self.assertRaises(ApiFailure): self.call(path, actor=actor, method='GET')
        with self.assertRaises(ApiFailure): self.app.handle('GET', path)
        self.s.conn.execute('UPDATE projects SET head_sha=?', ('b' * 40,))
        for actor in ('reviewer', 'operator', 'auditor'):
            self.assertEqual(b''.join(self.call(path, actor=actor, method='GET').chunks()), b'evidence')

    def test_http_download_forces_attachment_octet_stream_and_nosniff(self):
        payload = b'<script>alert("not inline")</script>'
        artifact = self.upload(payload, path='evidence.html')
        server = Server(('127.0.0.1', 0), self.app)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            connection.request('GET', '/api/artifacts/' + artifact['id'] + '/download',
                               headers={'Authorization': 'Bearer ' + self.tokens['reviewer']})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader('Content-Type'), 'application/octet-stream')
            self.assertTrue(response.getheader('Content-Disposition').startswith('attachment;'))
            self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
            self.assertEqual(response.getheader('X-Artifact-SHA256'), hashlib.sha256(payload).hexdigest())
            self.assertEqual(response.read(), payload)
        finally:
            connection.close(); server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_version_three_migrates_without_losing_existing_records(self):
        older = Path(self.temp.name) / 'v3.db'
        connection = sqlite3.connect(older)
        connection.executescript(SCHEMA + SERVICE_SCHEMA)
        connection.execute('INSERT INTO schema_version VALUES(3)')
        connection.execute("INSERT INTO maintainers(id,login,profile) VALUES(999,'existing','{}')")
        connection.commit(); connection.close()
        migrated = Store(older)
        try:
            self.assertEqual(migrated.schema_version, SCHEMA_VERSION)
            self.assertEqual(migrated.one('SELECT login FROM maintainers WHERE id=999')['login'], 'existing')
            self.assertEqual(migrated.all('SELECT * FROM artifact_files'), [])
        finally: migrated.close()


if __name__ == '__main__': unittest.main()
