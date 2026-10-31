import hashlib
import io
import json
import subprocess
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from worker.agent_client import CHUNK_BYTES, AgentService, consume_tasks
from worker.auth import create_key
from worker.db import Store, dump
from worker.demo import review_one, seed
from worker.runner import run_exported_task
from worker.service import ApiFailure, Application
from worker.workflow import Workflow


class Response:
    def __init__(self, value):
        self.raw = dump(value).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return self.raw[:limit]


class LocalServiceTransport:
    def __init__(self, application):
        self.app, self.calls = application, []
        self.lose_claim, self.lose_result, self.fail_heartbeat_after = 0, 0, None
        self.heartbeats = 0
        self.result_ack_override = None
        self.claim_override = None
        self.lose_upload = 0
        self.upload_ack_override = None
        self.fail_during_upload = False

    def __call__(self, request, timeout):
        path = request.full_url.removeprefix('http://127.0.0.1:8765')
        key = request.get_header('Idempotency-key')
        self.calls.append((path, key))
        if path.endswith('/heartbeat'):
            self.heartbeats += 1
            if self.fail_heartbeat_after is not None and self.heartbeats > self.fail_heartbeat_after:
                raise urllib.error.HTTPError(request.full_url, 409, 'Lease lost', {}, io.BytesIO())
        try:
            status, value = self.app.handle('POST', path, request.get_header('Authorization'),
                                            json.loads(request.data), key)
        except ApiFailure as exc:
            raise urllib.error.HTTPError(request.full_url, exc.status, 'Application rejection', {}, io.BytesIO()) from None
        if path.endswith('/claim') and self.lose_claim:
            self.lose_claim -= 1
            raise urllib.error.URLError('response lost after server commit')
        if path.endswith('/result') and self.lose_result:
            self.lose_result -= 1
            raise urllib.error.URLError('response lost after server commit')
        if path.endswith('/result') and self.result_ack_override is not None:
            return Response(self.result_ack_override)
        if path.endswith('/claim') and self.claim_override is not None:
            return Response(self.claim_override)
        if path.endswith('/artifacts') and self.lose_upload:
            self.lose_upload -= 1
            raise urllib.error.URLError('artifact response lost after server commit')
        if path.endswith('/artifacts') and self.upload_ack_override is not None:
            return Response(dict(value, **self.upload_ack_override))
        if path.endswith('/artifacts') and self.fail_during_upload:
            self.fail_heartbeat_after = self.heartbeats
            time.sleep(1.2)
        return Response(value)


class AgentClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'server.db')
        seed(self.store)
        workflow = Workflow(self.store)
        review_one(workflow)
        self.experience = workflow.apply('experience-create', {'project_id': 101, 'audience': 'agent',
            'spec': {'scenario': '真实离线任务', 'success_criteria': 'fixture answer', 'environment': 'offline',
                     'input': 'fixture', 'budget': {'seconds': 5}}}, 'reviewer')['id']
        self.key = create_key('test-agent', ['agent'], self.root / 'agent.token')
        self.transport = LocalServiceTransport(Application(self.store.path, {'users': [self.key['identity']]}))
        self.config = {'image': 'reviewed/runner@sha256:' + 'a' * 64, 'command': ['/app/run'],
                       'timeout_seconds': 5, 'memory_mb': 512, 'cpus': 1}
        self.out = self.root / 'outbox'

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def execute_fixture(self, command, log_path, timeout, env):
        raw = (log_path.parent / 'task.json').read_bytes()
        task = json.loads(raw)
        token = Path(self.key['secret_file']).read_text().strip()
        self.assertNotIn(token, dump(env))
        self.assertNotIn('GITHUB_TOKEN', env)
        self.assertIn('--network=none', command)
        output = log_path.parent / 'output'
        (output / 'evidence.txt').write_text('actual fixture output', encoding='utf-8')
        result = {'id': task['id'], 'sha': task['sha'], 'run_id': task['run_id'],
                  'task_sha256': hashlib.sha256(raw).hexdigest(), 'result': {
                      'success': True, 'executor': 'fixture', 'environment': 'offline fixture',
                      'steps': ['read fixture', 'compare fixture'], 'actual_result': 'fixture answer',
                      'artifacts': ['evidence.txt'], 'failure_kind': 'none'}}
        (output / 'result.json').write_text(dump(result), encoding='utf-8')
        log_path.write_text('fixture executor log', encoding='utf-8')

    def consume(self, **kwargs):
        with patch('worker.agent_client._private_directory', side_effect=lambda p: p.mkdir(parents=True, exist_ok=True)), \
             patch('worker.runner.execute', side_effect=kwargs.pop('execute', self.execute_fixture)), \
             patch('worker.runner.subprocess.run', return_value=subprocess.CompletedProcess([], kwargs.pop('cleanup_code', 0))):
            return consume_tasks('http://127.0.0.1:8765', self.key['secret_file'], self.out, self.config,
                                 transport=self.transport, **kwargs)

    def test_claim_execute_heartbeat_and_submit_uses_shared_runner(self):
        result = self.consume()
        self.assertEqual(result['submitted'], 1)
        self.assertFalse(result['blocked'])
        experience = self.store.one('SELECT * FROM experiences WHERE id=?', (self.experience,))
        self.assertEqual(experience['status'], 'completed')
        self.assertEqual(json.loads(experience['result'])['executor'], 'test-agent')
        paths = [path for path, key in self.transport.calls]
        self.assertEqual(paths[0], '/api/agent/claim')
        self.assertGreaterEqual(sum(path.endswith('/heartbeat') for path in paths), 2)
        self.assertTrue(paths[-1].endswith('/result'))
        state = json.loads((self.out / 'outbox.json').read_text())
        raw = (self.out / state['local_run'] / 'task.json').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), state['claim']['task_sha256'])
        self.assertNotIn(Path(self.key['secret_file']).read_text().strip(), dump(state))
        self.assertNotIn(state['claim']['lease_token'], dump(result))

    def test_idle_queue_does_not_execute_container(self):
        self.store.conn.execute("UPDATE experiences SET status='completed'")
        with patch('worker.runner.execute', side_effect=AssertionError('must not execute')):
            result = self.consume()
        self.assertEqual(result['tasks'][0]['status'], 'idle')
        self.assertEqual(len(self.transport.calls), 1)

    def test_unknown_claim_blocks_new_claim_until_explicit_same_key_resume(self):
        self.transport.lose_claim = 2
        first = self.consume()
        self.assertEqual(first['tasks'][0]['status'], 'claim_unknown')
        self.assertEqual(self.transport.calls[0][1], self.transport.calls[1][1])
        calls = len(self.transport.calls)
        self.consume()
        self.assertEqual(len(self.transport.calls), calls)
        result = self.consume(resume=True)
        self.assertEqual(result['submitted'], 1)
        self.assertEqual(self.transport.calls[2][1], self.transport.calls[0][1])
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM events WHERE action='agent_claimed'")['n'], 1)

    def test_unknown_result_is_replayed_without_reexecution_or_reclaim(self):
        self.transport.lose_result = 2
        result = self.consume(max_tasks=2)
        self.assertEqual(result['tasks'][0]['status'], 'submit_unknown')
        saved = json.loads((self.out / 'outbox.json').read_text())
        result = self.consume(resume=True, execute=lambda *a: self.fail('must not rerun'))
        self.assertEqual(result['submitted'], 1)
        claim_calls = [key for path, key in self.transport.calls if path.endswith('/claim')]
        result_calls = [key for path, key in self.transport.calls if path.endswith('/result')]
        self.assertEqual(len(claim_calls), 1)
        self.assertEqual(set(result_calls), {saved['submit_key']})

    def test_background_heartbeat_failure_blocks_submission(self):
        self.transport.fail_heartbeat_after = 1
        def slow(*args):
            time.sleep(1.2)
            self.execute_fixture(*args)
        result = self.consume(heartbeat_seconds=1, execute=slow)
        self.assertEqual(result['tasks'][0]['status'], 'lease_lost')
        self.assertFalse(any(path.endswith('/result') for path, key in self.transport.calls))
        self.assertEqual(self.store.one('SELECT status FROM experiences WHERE id=?', (self.experience,))['status'], 'pending')

    def test_missing_executor_reports_real_environment_failure(self):
        result = self.consume(execute=lambda *a: (_ for _ in ()).throw(FileNotFoundError('docker executable unavailable')))
        self.assertEqual(result['submitted'], 1)
        data = json.loads(self.store.one('SELECT result FROM experiences WHERE id=?', (self.experience,))['result'])
        self.assertFalse(data['success'])
        self.assertEqual(data['failure_kind'], 'environment')
        self.assertIn('FileNotFoundError', data['actual_result'])

    def test_cleanup_failure_blocks_submission_and_further_tasks(self):
        result = self.consume(cleanup_code=1, max_tasks=2)
        self.assertEqual(result['tasks'][0]['status'], 'cleanup_required')
        self.assertFalse(any(path.endswith('/result') for path, key in self.transport.calls))
        self.assertEqual(len([path for path, key in self.transport.calls if path.endswith('/claim')]), 1)

    def test_interrupted_execution_outbox_is_not_automatically_rerun(self):
        with self.assertRaises(KeyboardInterrupt):
            self.consume(execute=lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
        calls = len(self.transport.calls)
        result = self.consume(resume=True, execute=lambda *a: self.fail('must not rerun'))
        self.assertEqual(result['tasks'][0]['status'], 'executing')
        self.assertEqual(len(self.transport.calls), calls)

    def test_remote_plain_http_and_credentialed_urls_are_rejected(self):
        for url in ('http://example.com', 'https://user:pass@example.com', 'https://example.com/?token=bad', 'https://example.com/api'):
            with self.assertRaises(ValueError):
                AgentService(url, self.key['secret_file'])

    def test_exported_task_hash_tampering_prevents_any_container_execution(self):
        task = {'schema_version': 2, 'id': self.experience, 'run_id': 'run-1', 'sha': 'a' * 40}
        with patch('worker.runner.execute') as execute:
            with self.assertRaisesRegex(ValueError, 'digest'):
                run_exported_task(task, 'f' * 64, self.root / 'tampered', self.config)
        execute.assert_not_called()
        self.assertFalse((self.root / 'tampered').exists())

    def test_wrong_or_empty_result_ack_remains_unknown_and_replays_same_key(self):
        self.transport.result_ack_override = {'id': 'another-experience', 'success': True}
        result = self.consume(max_tasks=2)
        self.assertEqual(result['tasks'][0]['status'], 'submit_unknown')
        self.assertEqual(len([p for p, k in self.transport.calls if p.endswith('/claim')]), 1)
        original_key = self.transport.calls[-1][1]
        self.transport.result_ack_override = {}
        result = self.consume(resume=True, execute=lambda *a: self.fail('must not rerun'))
        self.assertEqual(result['tasks'][0]['status'], 'submit_unknown')
        self.transport.result_ack_override = None
        result = self.consume(resume=True, execute=lambda *a: self.fail('must not rerun'))
        self.assertEqual(result['submitted'], 1)
        self.assertEqual(self.transport.calls[-1][1], original_key)

    def test_lease_loss_cancels_real_subprocess_and_still_requests_container_cleanup(self):
        from worker import runner
        task = {'schema_version': 2, 'id': self.experience, 'run_id': 'cancel-run', 'sha': 'a' * 40}
        deadline = time.monotonic() + 0.25
        def validate():
            if time.monotonic() >= deadline:
                raise ValueError('lease lost during execution')
        command = [sys.executable, '-c', 'import time; time.sleep(20)']
        started = time.monotonic()
        with patch.object(runner, 'docker_command', return_value=command), \
             patch.object(runner.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as cleanup:
            with self.assertRaisesRegex(ValueError, 'lease lost'):
                run_exported_task(task, hashlib.sha256(dump(task).encode()).hexdigest(), self.root / 'cancelled',
                                  dict(self.config, timeout_seconds=30), validate)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(cleanup.call_args.args[0][:3], ['docker', 'rm', '-f'])

    def test_claim_without_task_field_is_unknown_not_idle(self):
        self.transport.claim_override = {}
        result = self.consume(max_tasks=2, execute=lambda *a: self.fail('must not execute malformed claim'))
        self.assertEqual(result['tasks'][0]['status'], 'claim_unknown')
        self.assertTrue(result['blocked'])
        key = self.transport.calls[-1][1]
        self.transport.claim_override = None
        result = self.consume(resume=True)
        self.assertEqual(result['submitted'], 1)
        claim_keys = [k for p, k in self.transport.calls if p.endswith('/claim')]
        self.assertEqual(claim_keys, [key, key])

    def test_artifacts_are_uploaded_in_bounded_chunks_before_result(self):
        def large_fixture(*args):
            self.execute_fixture(*args)
            (args[1].parent / 'output' / 'evidence.txt').write_bytes(b'e' * (CHUNK_BYTES + 37))
        result = self.consume(execute=large_fixture)
        self.assertEqual(result['submitted'], 1)
        uploads = [(p, k) for p, k in self.transport.calls if p.endswith('/artifacts')]
        self.assertEqual(len(uploads), 2)
        self.assertTrue(uploads[0][1].endswith(':0'))
        self.assertTrue(uploads[1][1].endswith(':1'))
        state = json.loads((self.out / 'outbox.json').read_text())
        manifest = state['artifacts'][0]
        self.assertEqual(manifest['next_chunk'], 2)
        self.assertEqual(manifest['size'], CHUNK_BYTES + 37)
        self.assertEqual(manifest['sha256'], hashlib.sha256(b'e' * (CHUNK_BYTES + 37)).hexdigest())
        self.assertTrue(manifest['artifact_id'])
        self.assertTrue(self.transport.calls[-1][0].endswith('/result'))

    def test_upload_timeout_resume_reuses_chunk_key_without_reexecuting(self):
        self.transport.lose_upload = 2
        first = self.consume(max_tasks=2)
        self.assertEqual(first['tasks'][0]['status'], 'upload_unknown')
        self.assertFalse(any(p.endswith('/result') for p, k in self.transport.calls))
        calls = len(self.transport.calls)
        self.consume()
        self.assertEqual(len(self.transport.calls), calls)
        result = self.consume(resume=True, execute=lambda *a: self.fail('must not rerun uploaded task'))
        self.assertEqual(result['submitted'], 1)
        upload_keys = [k for p, k in self.transport.calls if p.endswith('/artifacts')]
        self.assertEqual(len(set(upload_keys)), 1)
        self.assertEqual(len([p for p, k in self.transport.calls if p.endswith('/claim')]), 1)

    def test_wrong_upload_ack_blocks_result_and_preserves_same_key(self):
        self.transport.upload_ack_override = {'sha256': '0' * 64}
        result = self.consume()
        self.assertEqual(result['tasks'][0]['status'], 'upload_unknown')
        self.assertFalse(any(p.endswith('/result') for p, k in self.transport.calls))
        self.transport.upload_ack_override = None
        resumed = self.consume(resume=True, execute=lambda *a: self.fail('must not rerun'))
        self.assertEqual(resumed['submitted'], 1)
        self.assertEqual(len(set(k for p, k in self.transport.calls if p.endswith('/artifacts'))), 1)

    def test_changed_artifact_or_expired_lease_cannot_resume_upload(self):
        self.transport.lose_upload = 2
        self.consume()
        state = json.loads((self.out / 'outbox.json').read_text())
        (self.out / state['local_run'] / 'output' / 'evidence.txt').write_text('tampered evidence')
        calls = len([p for p, k in self.transport.calls if p.endswith('/artifacts')])
        result = self.consume(resume=True, execute=lambda *a: self.fail('must not rerun'))
        self.assertEqual(result['tasks'][0]['status'], 'artifact_changed')
        self.assertEqual(len([p for p, k in self.transport.calls if p.endswith('/artifacts')]), calls)
        self.assertFalse(any(p.endswith('/result') for p, k in self.transport.calls))

    def test_expired_upload_lease_prevents_resume_submission(self):
        self.transport.lose_upload = 2
        self.consume()
        self.store.conn.execute('UPDATE agent_leases SET lease_until=0')
        result = self.consume(resume=True, execute=lambda *a: self.fail('must not rerun'))
        self.assertEqual(result['tasks'][0]['status'], 'lease_lost')
        self.assertFalse(any(p.endswith('/result') for p, k in self.transport.calls))

    def test_empty_artifact_uses_one_authenticated_empty_chunk(self):
        def empty_fixture(*args):
            self.execute_fixture(*args)
            (args[1].parent / 'output' / 'evidence.txt').write_bytes(b'')
        result = self.consume(execute=empty_fixture)
        self.assertEqual(result['submitted'], 1)
        state = json.loads((self.out / 'outbox.json').read_text())
        self.assertEqual(state['artifacts'][0]['size'], 0)
        self.assertEqual(state['artifacts'][0]['total_chunks'], 1)
        self.assertEqual(len([p for p, k in self.transport.calls if p.endswith('/artifacts')]), 1)

    def test_heartbeat_failure_during_upload_blocks_final_result(self):
        self.transport.fail_during_upload = True
        result = self.consume(heartbeat_seconds=1)
        self.assertEqual(result['tasks'][0]['status'], 'lease_lost')
        self.assertFalse(any(p.endswith('/result') for p, k in self.transport.calls))


if __name__ == '__main__':
    unittest.main()
