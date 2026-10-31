import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import runpy
from unittest.mock import patch

from worker.container import import_config, load_secret_files, main
from worker.deployment import setup_local, health_target


class ContainerDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'state'
        self.origin = 'http://127.0.0.1:8780'

    def tearDown(self):
        self.tmp.cleanup()

    def initialize(self):
        return setup_local(self.root, public_origin=self.origin)

    def test_public_origin_initialization_is_idempotent_and_probes_loopback(self):
        self.initialize()
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.initialize()
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(health_target(self.root)['health_host'], '127.0.0.1:8780')
        self.assertEqual(health_target(self.root)['health_url'], 'http://127.0.0.1:8765/healthz')

    def test_origin_mismatch_fails_without_overwriting_config_or_credentials(self):
        self.initialize()
        before = (self.root / 'service.local.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'differs'):
            setup_local(self.root, public_origin='https://opendev.example.invalid')
        self.assertEqual(before, (self.root / 'service.local.json').read_bytes())
        self.assertFalse((self.root / '.setup.lock').exists())

    def test_remote_plain_http_origin_rejected_before_creating_state(self):
        with self.assertRaises(ValueError):
            setup_local(self.root, public_origin='http://opendev.example.invalid')
        self.assertFalse(self.root.exists())

    def test_container_init_output_omits_raw_credentials(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'OPENDEV_PUBLIC_ORIGIN': self.origin}), contextlib.redirect_stdout(output):
            self.assertEqual(main(['init', '--root', str(self.root)]), 0)
        self.assertEqual(json.loads(output.getvalue())['application'], 'OpenDev Elite')
        for file in (self.root / 'credentials').glob('*.token'):
            self.assertNotIn(file.read_text().strip(), output.getvalue())

    def test_container_roles_require_init_and_matching_origin(self):
        with patch.dict(os.environ, {'OPENDEV_PUBLIC_ORIGIN': self.origin}), patch('worker.container.process') as process:
            with self.assertRaisesRegex(ValueError, 'Initialize'):
                main(['api', '--root', str(self.root)])
            self.initialize()
            main(['api', '--root', str(self.root)])
            self.assertEqual(process.call_args.args[1], 'serve')
            self.assertTrue(process.call_args.kwargs['console'])
            main(['worker', '--root', str(self.root)])
            self.assertEqual(process.call_args.args[1], 'run')
        with patch.dict(os.environ, {'OPENDEV_PUBLIC_ORIGIN': 'https://other.example.invalid'}):
            with self.assertRaisesRegex(ValueError, 'must match'):
                main(['api', '--root', str(self.root)])

    def test_secret_file_loading_is_bounded_and_rejects_ambiguous_sources(self):
        secret = Path(self.tmp.name) / 'secret'
        secret.write_text('synthetic-file-token\n')
        with patch.dict(os.environ, {'GITHUB_TOKEN_FILE': str(secret)}, clear=True):
            load_secret_files()
            self.assertEqual(os.environ['GITHUB_TOKEN'], 'synthetic-file-token')
            with self.assertRaisesRegex(ValueError, 'cannot both'):
                load_secret_files()
        for raw in ('', 'one\ntwo', 'a' * 16385):
            secret.write_text(raw)
            with patch.dict(os.environ, {'NEXT_CREDITS_PAT_FILE': str(secret)}, clear=True):
                with self.assertRaises(ValueError):
                    load_secret_files()
                self.assertNotIn('NEXT_CREDITS_PAT', os.environ)

    def test_config_replacement_preserves_identities_and_has_no_partial_write(self):
        self.initialize()
        path = self.root / 'service.local.json'
        old = path.read_bytes()
        config = json.loads(old)
        new_origin = 'https://opendev.example.invalid'
        config['public_origin'] = new_origin
        with self.assertRaisesRegex(ValueError, 'must match'):
            import_config(self.root, 'service', json.dumps(config).encode(), self.origin)
        self.assertEqual(path.read_bytes(), old)
        with patch('worker.container.os.replace', side_effect=OSError('simulated storage failure')):
            with self.assertRaises(OSError):
                import_config(self.root, 'service', json.dumps(config).encode(), new_origin)
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(list((self.root / 'runtime').glob('config-*')), [])
        import_config(self.root, 'service', json.dumps(config).encode(), new_origin)
        setup_local(self.root, public_origin=new_origin)
        self.assertEqual(json.loads(path.read_text())['users'], json.loads(old)['users'])

    def test_config_import_rejects_nonobjects_large_input_and_unknown_targets(self):
        self.initialize()
        for name, value in [('credits', b'[]'), ('credits', b' ' * (1024 * 1024 + 1)), ('../worker', b'{}')]:
            with self.assertRaises(ValueError):
                import_config(self.root, name, value, self.origin)
        result = import_config(self.root, 'credits', b'{"base_url":"https://credits.example.invalid"}', self.origin)
        self.assertTrue(result['restart_required'])
        self.assertEqual(json.loads((self.root / 'credits.local.json').read_text())['base_url'], 'https://credits.example.invalid')

    def test_dead_queue_does_not_block_ui_readiness_needed_to_repair_it(self):
        from worker.db import Store
        self.initialize()
        store = Store(self.root / 'worker.db')
        try:
            store.enqueue('refresh', {'full_name': 'synthetic/repo'}, 'synthetic-dead-job')
            store.conn.execute("UPDATE jobs SET state='dead'")
        finally:
            store.close()
        entry = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'deploy/healthcheck.py'))
        with contextlib.redirect_stdout(io.StringIO()):
            with patch('sys.argv', ['healthcheck', '--root', str(self.root)]):
                self.assertEqual(entry['main'](), 1)
            with patch('sys.argv', ['healthcheck', '--root', str(self.root), '--readiness-only']):
                self.assertEqual(entry['main'](), 0)


if __name__ == '__main__':
    unittest.main()
