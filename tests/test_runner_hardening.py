import copy
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker.db import Store, dump
from worker.demo import SHA, seed, review_one
from worker.workflow import Workflow
from worker import runner


class RunnerHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.s = Store(self.root / 'test.db')
        seed(self.s)
        self.w = Workflow(self.s)
        review_one(self.w)
        self.eid = self.w.apply('experience-create', {
            'project_id': 101, 'audience': 'agent', 'spec': {
                'scenario': 'Check offline fixture', 'success_criteria': 'Exact match',
                'environment': 'offline', 'input': 'fixture', 'budget': {'seconds': 10}}}, 'reviewer')['id']
        self.config = {'image': 'fixture@sha256:' + 'a' * 64, 'command': ['/app/run'],
                       'timeout_seconds': 5, 'memory_mb': 512, 'cpus': 1}

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def response(self, task):
        return {'id': task['id'], 'sha': task['sha'], 'run_id': task['run_id'],
                'task_sha256': 'digest', 'result': {
                    'success': True, 'executor': 'fixture', 'environment': 'offline fixture',
                    'steps': ['Execute fixture', 'Compare output'], 'actual_result': 'Exact match',
                    'artifacts': ['evidence.txt'], 'failure_kind': 'none'}}

    def test_export_has_fresh_run_and_exact_byte_digest(self):
        a = runner.export_task(self.s, self.eid, self.root / 'a.json')
        b = runner.export_task(self.s, self.eid, self.root / 'b.json')
        self.assertNotEqual(a['run_id'], b['run_id'])
        self.assertEqual(a['sha256'], hashlib.sha256(Path(a['path']).read_bytes()).hexdigest())
        self.assertEqual(json.loads(Path(a['path']).read_text(encoding='utf-8'))['schema_version'], 2)
        with self.assertRaises(ValueError):
            runner.export_task(self.s, self.eid, self.root / 'a.json')

    def test_changed_commit_never_launches_container(self):
        self.s.conn.execute('UPDATE projects SET head_sha=?', ('b' * 40,))
        with patch.object(runner, 'execute') as execute:
            with self.assertRaisesRegex(ValueError, 'stale'):
                runner.run_agent(self.s, self.eid, self.root / 'run', self.config)
            execute.assert_not_called()
        self.assertFalse((self.root / 'run').exists())

    def test_expired_review_cannot_export(self):
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=31)).isoformat()
        self.s.conn.execute('UPDATE reviews SET created_at=?', (old,))
        with self.assertRaisesRegex(ValueError, 'stale'):
            runner.build_task(self.s, self.eid)

    def test_new_review_invalidates_old_experience(self):
        review_one(self.w)
        with self.assertRaisesRegex(ValueError, 'stale'):
            runner.build_task(self.s, self.eid)

    def test_expired_or_incomplete_evidence_cannot_export(self):
        self.s.conn.execute("UPDATE projects SET refreshed_at='2020-01-01T00:00:00+00:00'")
        with self.assertRaisesRegex(ValueError, 'expired'):
            runner.build_task(self.s, self.eid)
        self.s.conn.execute('UPDATE projects SET refreshed_at=?', (dt.datetime.now(dt.timezone.utc).isoformat(),))
        self.s.conn.execute('UPDATE snapshots SET complete=0')
        with self.assertRaisesRegex(ValueError, 'Complete evidence'):
            runner.build_task(self.s, self.eid)

    def test_resource_limits_reject_coercion_and_non_finite_values(self):
        for key, value in [('timeout_seconds', True), ('timeout_seconds', 2.1),
                           ('memory_mb', '512'), ('cpus', float('nan')), ('cpus', float('inf'))]:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    runner.resource_limits(dict(self.config, **{key: value}))
        command = runner.docker_command('task', 'out', self.config, 'fixture')
        self.assertIn('--memory-swap=512m', command)
        self.assertIn('--log-driver=none', command)

    def test_docker_client_cannot_inherit_credentials_or_proxy_config(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'secret', 'NEXT_CREDITS_PAT': 'secret',
                                     'DOCKER_HOST': 'tcp://untrusted:2375', 'HTTPS_PROXY': 'secret'}):
            env = runner.client_environment(self.root)
        for name in ('GITHUB_TOKEN', 'NEXT_CREDITS_PAT', 'DOCKER_HOST', 'HTTPS_PROXY'):
            self.assertNotIn(name, env)
        self.assertEqual(json.loads((Path(env['DOCKER_CONFIG']) / 'config.json').read_text()), {})
        self.assertNotEqual(env.get('HOME'), os.environ.get('HOME'))

    def test_result_must_match_experience_sha_run_and_task(self):
        task = runner.build_task(self.s, self.eid)
        data = self.response(task)
        for key in ('id', 'sha', 'run_id', 'task_sha256'):
            changed = dict(data, **{key: 'wrong'})
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    runner.validate_result(task, changed, task_sha256='digest')

    def test_http_validation_never_opens_artifact_files(self):
        task = runner.build_task(self.s, self.eid)
        with patch.object(Path, 'open', side_effect=AssertionError('No filesystem access expected')):
            runner.validate_result(task, self.response(task))

    def test_result_rejects_traversal_absolute_urls_and_fake_evidence(self):
        task = runner.build_task(self.s, self.eid)
        for path in ('../secret', '/etc/passwd', 'C:/secret', 'https://example.com/evidence',
                     'nested\\secret', 'a/../secret', '.', 'result.json', './evidence'):
            data = self.response(task)
            data['result']['artifacts'] = [path]
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    runner.validate_result(task, data)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            runner.validate_result(task, self.response(task), self.root)

    def test_result_requires_consistent_success_and_steps(self):
        task = runner.build_task(self.s, self.eid)
        for key, value in [('success', 'true'), ('failure_kind', 'environment'),
                           ('steps', 'claims without actual steps'), ('artifacts', [])]:
            data = self.response(task)
            data['result'][key] = value
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    runner.validate_result(task, data)

    def test_missing_dependencies_can_report_environment_failure(self):
        task = runner.build_task(self.s, self.eid)
        data = self.response(task)
        data['result'].update(success=False, failure_kind='environment', artifacts=[])
        runner.validate_result(task, data, self.root)

    def test_symlink_parent_cannot_be_mounted(self):
        target = self.root / 'real'
        target.mkdir()
        link = self.root / 'linked'
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks requires OS permission')
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            runner.safe_path(link / 'child')

    def test_hardlinked_artifact_rejected(self):
        (self.root / 'original.txt').write_text('evidence')
        try:
            os.link(self.root / 'original.txt', self.root / 'evidence.txt')
        except OSError:
            self.skipTest('Filesystem does not support hard links')
        task = runner.build_task(self.s, self.eid)
        with self.assertRaisesRegex(ValueError, 'without links'):
            runner.validate_result(task, self.response(task), self.root)

    def test_log_capture_is_bounded_and_stops_noisy_process(self):
        log = self.root / 'limited.log'
        with patch.object(runner, 'MAX_LOG_BYTES', 1024):
            with self.assertRaisesRegex(ValueError, 'log limit'):
                runner.execute([sys.executable, '-c', 'import sys; sys.stdout.write("x" * 1000000)'],
                               log, 5, dict(os.environ))
        self.assertLessEqual(log.stat().st_size, 1024)

    def test_process_timeout_is_real_and_bounded(self):
        with self.assertRaisesRegex(ValueError, 'timed out'):
            runner.execute([sys.executable, '-c', 'import time; time.sleep(10)'],
                           self.root / 'timeout.log', 0.1, dict(os.environ))

    def test_successful_process_records_actual_output(self):
        log = self.root / 'success.log'
        runner.execute([sys.executable, '-c', 'print("actual fixture output")'], log, 5, dict(os.environ))
        self.assertIn('actual fixture output', log.read_text())

    def test_run_requires_bound_result_and_always_requests_cleanup(self):
        def fixture(command, log, timeout, env):
            root = log.parent
            raw = (root / 'task.json').read_bytes()
            task = json.loads(raw)
            data = self.response(task)
            data['task_sha256'] = hashlib.sha256(raw).hexdigest()
            (root / 'output' / 'evidence.txt').write_text('actual fixture evidence')
            (root / 'output' / 'result.json').write_text(dump(data), encoding='utf-8')
            log.write_text('synthetic execution')
        with patch.object(runner, 'execute', side_effect=fixture), patch.object(runner.subprocess, 'run') as cleanup:
            cleanup.return_value.returncode = 0
            result = runner.run_agent(self.s, self.eid, self.root / 'run', self.config)
        self.assertEqual(result['result']['sha'], SHA)
        self.assertEqual(cleanup.call_args.args[0][:3], ['docker', 'rm', '-f'])
        self.assertNotIn('GITHUB_TOKEN', cleanup.call_args.kwargs['env'])
        self.assertEqual(self.s.one('SELECT status FROM experiences WHERE id=?', (self.eid,))['status'], 'pending')

    def test_cleanup_requested_even_when_executor_fails(self):
        with patch.object(runner, 'execute', side_effect=ValueError('Executor timed out')), patch.object(runner.subprocess, 'run') as cleanup:
            cleanup.return_value.returncode = 0
            with self.assertRaisesRegex(ValueError, 'timed out'):
                runner.run_agent(self.s, self.eid, self.root / 'run', self.config)
        cleanup.assert_called_once()

    def test_cleanup_failure_prevents_result_acceptance(self):
        with patch.object(runner, 'execute'), patch.object(runner.subprocess, 'run') as cleanup:
            cleanup.return_value.returncode = 1
            with self.assertRaisesRegex(ValueError, 'cleanup failed'):
                runner.run_agent(self.s, self.eid, self.root / 'run', self.config)

    def test_post_execution_review_change_prevents_acceptance(self):
        def change_review(*args):
            review_one(self.w)
        with patch.object(runner, 'execute', side_effect=change_review), patch.object(runner.subprocess, 'run') as cleanup:
            cleanup.return_value.returncode = 0
            with self.assertRaisesRegex(ValueError, 'stale'):
                runner.run_agent(self.s, self.eid, self.root / 'run', self.config)

    def test_result_size_limit_precedes_json_read(self):
        path = self.root / 'result.json'
        with path.open('wb') as stream:
            stream.truncate(runner.MAX_RESULT_BYTES + 1)
        with self.assertRaisesRegex(ValueError, '1 MiB'):
            runner.read_result(self.root, runner.build_task(self.s, self.eid), 'digest')

    def test_symlink_result_file_rejected(self):
        original = self.root / 'source.json'
        original.write_text('{}')
        try:
            (self.root / 'result.json').symlink_to(original)
        except OSError:
            self.skipTest('Creating symlinks requires OS permission')
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            runner.read_result(self.root, runner.build_task(self.s, self.eid), 'digest')


if __name__ == '__main__':
    unittest.main()
