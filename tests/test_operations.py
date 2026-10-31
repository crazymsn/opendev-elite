import datetime as dt
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from worker.db import SCHEMA_VERSION, Store
from worker.operations import doctor, run_loop, tick


DISCOVERY = {'lookback_days': 7, 'queries': [{'query': 'topic:agent', 'category': 'agent'}]}


class EmptyGitHub:
    def __init__(self, store):
        self.store = store

    def get(self, path, params=None):
        if path != '/search/repositories':
            raise AssertionError('Unexpected API operation: ' + path)
        return {'total_count': 0, 'items': []}, {}

    def next_link(self, headers):
        return None


class NoWaitEvent:
    def __init__(self, stop_on_wait=False):
        self.stopped, self.waits, self.stop_on_wait = False, [], stop_on_wait

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        self.waits.append(timeout)
        self.stopped = self.stop_on_wait
        return self.stopped


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'worker.db')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_tick_skips_autoschedule_when_claimable_backlog_is_large(self):
        for index in range(10):
            self.store.enqueue('refresh', {'repo': 'a/' + str(index)}, 'backlog-' + str(index))
        with patch('worker.operations.GitHub', EmptyGitHub), \
             patch('worker.operations.Pipeline.schedule', side_effect=AssertionError('must not schedule')) as schedule, \
             patch('worker.operations.Pipeline.work', return_value=[]):
            result = tick(self.store, DISCOVERY, max_jobs=2)
        self.assertEqual(result['scheduled_job_ids'], [])
        schedule.assert_not_called()

    def test_tick_uses_existing_pipeline_writes_local_reports_and_preserves_caller_store(self):
        with patch('worker.operations.GitHub', EmptyGitHub), patch('worker.operations.shutil.which', return_value=None):
            result = tick(self.store, DISCOVERY, max_jobs=2, out_dir=self.root / 'reports')
            again = tick(self.store, DISCOVERY, max_jobs=2)
        self.assertEqual(result['jobs'][0]['status'], 'done')
        self.assertEqual(again['jobs'], [])
        self.assertEqual(result['scheduled_job_ids'], again['scheduled_job_ids'])
        self.assertEqual(self.store.one('SELECT COUNT(*) AS count FROM jobs')['count'], 1)
        self.assertTrue(result['health']['healthy'])
        report_path = Path(result['report_paths']['report'])
        health_path = Path(result['report_paths']['health'])
        self.assertTrue(report_path.is_file())
        self.assertEqual(json.loads(health_path.read_text(encoding='utf-8')), result['health'])
        self.assertEqual(sorted(p.name for p in report_path.parent.iterdir()), ['health.json', 'report.md'])
        self.assertEqual(again['report_paths'], {})
        self.assertEqual(self.store.one('SELECT COUNT(*) AS count FROM grants')['count'], 0)

    def test_doctor_does_not_request_network_or_execute_docker_and_never_returns_secrets(self):
        secrets = {'GITHUB_TOKEN': 'github-secret-marker', 'NEXT_CREDITS_PAT': 'credits-secret-marker'}
        with patch.dict('os.environ', secrets), \
             patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network forbidden')), \
             patch('subprocess.run', side_effect=AssertionError('subprocess forbidden')), \
             patch('worker.operations.shutil.which', return_value='C:/docker.exe'):
            result = doctor(self.store)
        serialized = json.dumps(result)
        for secret in secrets.values():
            self.assertNotIn(secret, serialized)
        self.assertTrue(result['github']['token_configured'])
        self.assertTrue(result['credits']['pat_configured'])
        self.assertFalse(result['credits']['pat_format_valid'])
        self.assertTrue(result['agent']['docker_command_available'])
        self.assertFalse(result['agent']['daemon_checked'])
        self.assertFalse(result['github']['connectivity_checked'])
        self.assertEqual(result['database']['schema_version'], SCHEMA_VERSION)

    def test_doctor_reports_queue_degradation_without_job_payload_or_errors(self):
        self.store.enqueue('refresh', {'repo': 'a/b', 'secret': 'never-print'}, 'one')
        self.store.enqueue('refresh', {'repo': 'a/c'}, 'two')
        self.store.claim()
        self.store.conn.execute('UPDATE jobs SET lease_until=0 WHERE id=1')
        self.store.conn.execute("UPDATE jobs SET state='dead',error='private-error-secret' WHERE id=2")
        with patch('worker.operations.shutil.which', return_value=None):
            result = doctor(self.store)
        self.assertFalse(result['healthy'])
        self.assertEqual(result['queue']['expired_leases'], 1)
        self.assertEqual(result['queue']['dead'], 1)
        self.assertNotIn('private-error-secret', json.dumps(result))
        self.assertNotIn('never-print', json.dumps(result))

    def test_valid_static_configs_are_ready_but_not_claimed_connected(self):
        credits = {'base_url': 'https://credits.internal.test', 'event_slug': 'sponsorship',
                   'event_end_date': '2099-12-31', 'event_config_evidence': 'Administrator confirmed settings in signed local record.'}
        agent = {'image': 'approved/runner@sha256:' + 'a' * 64, 'command': ['/app/runner'],
                 'timeout_seconds': 300, 'memory_mb': 1024, 'cpus': 1}
        with patch.dict('os.environ', {'NEXT_CREDITS_PAT': 'onc_pat_' + 'x' * 43}), \
             patch('worker.operations.shutil.which', return_value=None):
            result = doctor(self.store, credits, agent)
        self.assertTrue(result['credits']['configuration_ready'])
        self.assertTrue(result['agent']['configuration_ready'])
        self.assertFalse(result['credits']['writes_enabled'])
        self.assertFalse(result['credits']['connectivity_checked'])
        self.assertFalse(result['agent']['docker_command_available'])
        self.assertNotIn(credits['base_url'], json.dumps(result))

    def test_placeholder_and_expired_configs_fail_readiness_without_exposing_values(self):
        credits = {'base_url': 'https://YOUR-CREDITS-HOST', 'event_slug': 'sponsorship',
                   'event_end_date': '2000-01-01', 'event_config_evidence': 'REPLACE administrator evidence'}
        agent = {'image': 'your-runner@sha256:REPLACE', 'command': ['/app/runner'],
                 'timeout_seconds': 300, 'memory_mb': 1024, 'cpus': 1}
        with patch('worker.operations.shutil.which', return_value=None):
            result = doctor(self.store, credits, agent)
        self.assertFalse(result['credits']['configuration_ready'])
        self.assertFalse(result['credits']['base_url_valid'])
        self.assertFalse(result['credits']['expiry_valid'])
        self.assertFalse(result['credits']['event_evidence_present'])
        self.assertFalse(result['agent']['configuration_ready'])

    def test_loop_is_bounded_and_opens_and_closes_each_cycle(self):
        opened = []
        def factory(path):
            store = Store(path)
            opened.append(store)
            return store
        event = NoWaitEvent()
        with patch('worker.operations.Store', side_effect=factory), \
             patch('worker.operations.tick', return_value={'healthy': True}) as cycle:
            result = run_loop(self.root / 'loop.db', DISCOVERY, interval_seconds=60, max_cycles=2, stop_event=event)
        self.assertEqual(result, {'cycles': 2, 'stopped': False, 'last_tick': {'healthy': True}})
        self.assertEqual(len(opened), 3)
        self.assertIsNot(opened[0], opened[1])
        self.assertEqual(cycle.call_count, 2)
        self.assertEqual(event.waits, [60])
        for store in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                store.one('SELECT 1')

    def test_loop_wakes_early_when_delayed_jobs_become_ready(self):
        db = self.root / 'delay.db'
        store = Store(db)
        store.enqueue('refresh', {'repo': 'a/b'}, 'one', available_at=time.time() + 8)
        store.close()
        event = NoWaitEvent()
        with patch('worker.operations.tick', return_value={'jobs': []}) as cycle:
            result = run_loop(db, DISCOVERY, interval_seconds=60, max_cycles=2, stop_event=event)
        self.assertEqual(result['cycles'], 2)
        self.assertEqual(cycle.call_count, 2)
        self.assertEqual(len(event.waits), 1)
        self.assertGreaterEqual(event.waits[0], 1)
        self.assertLessEqual(event.waits[0], 12)

    def test_stop_event_interrupts_wait_before_next_cycle(self):
        event = NoWaitEvent(stop_on_wait=True)
        with patch('worker.operations.tick', return_value={'fixture': True}) as cycle, \
             patch('worker.operations._cycle_wait_seconds', return_value=86400):
            result = run_loop(self.root / 'loop.db', DISCOVERY, interval_seconds=86400, stop_event=event)
        self.assertTrue(result['stopped'])
        self.assertEqual(result['cycles'], 1)
        self.assertEqual(cycle.call_count, 1)
        self.assertEqual(event.waits, [86400])

    def test_preset_stop_event_does_not_open_database(self):
        event = threading.Event()
        event.set()
        with patch('worker.operations.Store', side_effect=AssertionError('must not open')):
            result = run_loop(self.root / 'unused.db', DISCOVERY, stop_event=event)
        self.assertEqual(result, {'cycles': 0, 'stopped': True, 'last_tick': None})

    def test_cycle_error_closes_database_and_propagates(self):
        other = Store(self.root / 'loop.db')
        with patch('worker.operations.Store', return_value=other), \
             patch('worker.operations.tick', side_effect=OSError('report directory unavailable')):
            with self.assertRaises(OSError):
                run_loop(self.root / 'loop.db', DISCOVERY, max_cycles=1)
        with self.assertRaises(sqlite3.ProgrammingError):
            other.one('SELECT 1')

    def test_keyboard_interrupt_closes_database_and_returns_stopped(self):
        other = Store(self.root / 'loop.db')
        with patch('worker.operations.Store', return_value=other), patch('worker.operations.tick', side_effect=KeyboardInterrupt):
            result = run_loop(self.root / 'loop.db', DISCOVERY)
        self.assertTrue(result['stopped'])
        with self.assertRaises(sqlite3.ProgrammingError):
            other.one('SELECT 1')

    def test_invalid_limits_are_rejected_before_starting_work(self):
        with patch('worker.operations.GitHub', side_effect=AssertionError('must not instantiate')):
            for maximum in (0, -1, 1001, True, 1.5):
                with self.assertRaises(ValueError):
                    tick(self.store, DISCOVERY, max_jobs=maximum)
            with self.assertRaises(ValueError):
                tick(self.store, {'queries': [{'query': '', 'category': 'test'}]})
        with patch('worker.operations.Store', side_effect=AssertionError('must not open')):
            for interval in (0, True, float('nan'), float('inf'), 86401):
                with self.assertRaises(ValueError):
                    run_loop('unused.db', DISCOVERY, interval_seconds=interval, max_cycles=1)
            for maximum in (-1, True, 1.5):
                with self.assertRaises(ValueError):
                    run_loop('unused.db', DISCOVERY, max_cycles=maximum)


if __name__ == '__main__':
    unittest.main()
