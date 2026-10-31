import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker.auth import IdentityStore, create_key
from worker.db import Store
from worker.deployment import IDENTITIES, _LogStream, health_target, local_root, process, setup_local


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / 'private-deployment'

    def tearDown(self):
        self.temporary.cleanup()

    def test_setup_creates_six_separate_scoped_keys_without_disclosing_tokens(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network forbidden')):
            result = setup_local(self.root)
        config = json.loads(Path(result['config']).read_text(encoding='utf-8'))
        identities = IdentityStore(config)
        self.assertEqual(len(result['credentials']), 6)
        self.assertFalse(config['allow_credit_writes'])
        self.assertEqual(config['public_origin'], 'http://127.0.0.1:8765')
        tokens = []
        for item in result['credentials']:
            token = Path(item['secret_file']).read_text().strip()
            principal = identities.authenticate('Bearer ' + token)
            self.assertEqual(principal.actor, item['actor'])
            self.assertEqual(principal.roles, frozenset({item['role']}))
            self.assertNotIn(token, json.dumps(result))
            self.assertNotIn(token, json.dumps(config))
            tokens.append(token)
        self.assertEqual(len(set(tokens)), 6)
        self.assertFalse((self.root / 'demo.db').exists())
        store = Store(result['database'])
        try:
            self.assertEqual(store.one('SELECT COUNT(*) AS n FROM projects')['n'], 0)
        finally:
            store.close()

    def test_repeat_setup_preserves_existing_config_credentials_and_database_bytes(self):
        result = setup_local(self.root)
        config_path = Path(result['config'])
        config = json.loads(config_path.read_text())
        config['agent_lease_seconds'] = 1200
        config_path.write_text(json.dumps(config), encoding='utf-8')
        paths = [config_path, Path(result['database']), Path(result['discovery_config'])]
        paths += [Path(c['secret_file']) for c in result['credentials']]
        before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        with patch('worker.deployment.create_key', side_effect=AssertionError('must not rotate')):
            again = setup_local(self.root)
        self.assertEqual(result['credentials'], again['credentials'])
        self.assertEqual(before, {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in paths})

    def test_interrupted_setup_recovers_existing_secret_without_rotation(self):
        self.root.mkdir()
        actor, role = IDENTITIES[0]
        original = create_key(actor, [role], self.root / 'credentials' / (actor + '.token'))
        old = Path(original['secret_file']).read_bytes()
        result = setup_local(self.root)
        self.assertEqual(Path(result['credentials'][0]['secret_file']).read_bytes(), old)
        config = json.loads(Path(result['config']).read_text())
        self.assertEqual(config['users'][0]['token_sha256'], original['identity']['token_sha256'])

    def test_demo_is_opt_in_offline_and_separate_from_production(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network forbidden')):
            result = setup_local(self.root, demo=True)
        production = Store(result['database'])
        demo = Store(result['demo_database'])
        try:
            self.assertEqual(production.one('SELECT COUNT(*) AS n FROM projects')['n'], 0)
            self.assertGreater(demo.one('SELECT COUNT(*) AS n FROM projects')['n'], 0)
            self.assertTrue(all(p['full_name'].startswith('synthetic-demo/') for p in demo.all('SELECT full_name FROM projects')))
        finally:
            production.close()
            demo.close()
        marker = json.loads((self.root / 'demo.SYNTHETIC.json').read_text())
        self.assertTrue(marker['synthetic_only'])
        self.assertEqual(marker['real_credits_issued'], 0)
        before = Path(result['demo_database']).read_bytes()
        setup_local(self.root, demo=True)
        self.assertEqual(before, Path(result['demo_database']).read_bytes())

    def test_mismatched_existing_credential_is_not_overwritten(self):
        result = setup_local(self.root)
        secret = Path(result['credentials'][0]['secret_file'])
        secret.write_text('x' * 48, encoding='utf-8')
        config = Path(result['config']).read_bytes()
        with self.assertRaises(ValueError):
            setup_local(self.root)
        self.assertEqual(secret.read_text(), 'x' * 48)
        self.assertEqual(Path(result['config']).read_bytes(), config)
        self.assertFalse((self.root / '.setup.lock').exists())

    def test_concurrent_setup_lock_is_respected(self):
        self.root.mkdir()
        lock = self.root / '.setup.lock'
        lock.write_text('existing setup')
        with self.assertRaises(FileExistsError):
            setup_local(self.root)
        self.assertEqual(lock.read_text(), 'existing setup')
        self.assertFalse((self.root / 'worker.db').exists())

    def test_rejects_onedrive_ancestors_and_unrelated_existing_directories(self):
        with self.assertRaises(ValueError):
            local_root(Path(self.temporary.name) / 'OneDrive - Team' / 'state')
        with self.assertRaises(ValueError):
            local_root(Path.home())
        cloud = Path(self.temporary.name) / 'cloud'
        with patch.dict(os.environ, {'OneDrive': str(cloud)}):
            with self.assertRaises(ValueError):
                local_root(cloud / 'worker')
        self.root.mkdir()
        (self.root / 'unrelated.txt').write_text('preserve')
        with self.assertRaises(ValueError):
            setup_local(self.root)
        self.assertEqual((self.root / 'unrelated.txt').read_text(), 'preserve')

    def test_log_stream_bounds_single_messages(self):
        messages = []
        class Logger:
            def log(self, level, value):
                messages.append(value)
        output = _LogStream(Logger(), 20)
        self.assertEqual(output.write('x' * 70000), 70000)
        self.assertTrue(all(len(value) <= 16384 for value in messages))
        self.assertEqual(sum(len(value) for value in messages), 70000)

    def test_production_launcher_can_load_explicit_credits_configuration(self):
        result = setup_local(self.root)
        path = Path(result['config'])
        config = json.loads(path.read_text())
        config['allow_credit_writes'] = True
        path.write_text(json.dumps(config), encoding='utf-8')
        credits = {'base_url': 'https://credits.example.invalid', 'event_slug': 'reviewed-event'}
        (self.root / 'credits.local.json').write_text(json.dumps(credits), encoding='utf-8')
        with patch('worker.service.serve') as serve:
            process(self.root, 'serve', 'test-prod')
        self.assertTrue(serve.call_args.args[1]['allow_credit_writes'])
        self.assertEqual(serve.call_args.args[4], credits)

    def test_explicit_credits_enablement_without_configuration_is_rejected(self):
        result = setup_local(self.root)
        path = Path(result['config'])
        config = json.loads(path.read_text())
        config['allow_credit_writes'] = True
        path.write_text(json.dumps(config), encoding='utf-8')
        with patch('worker.service.serve') as serve:
            with self.assertRaisesRegex(ValueError, 'credits.local.json'):
                process(self.root, 'serve', 'test-prod')
        serve.assert_not_called()

    def test_demo_never_loads_real_credits_or_enables_writes(self):
        result = setup_local(self.root, demo=True)
        path = Path(result['config'])
        config = json.loads(path.read_text())
        config['allow_credit_writes'] = True
        path.write_text(json.dumps(config), encoding='utf-8')
        (self.root / 'credits.local.json').write_text('invalid real configuration must not be parsed', encoding='utf-8')
        (self.root / 'github.local.json').write_text('invalid real configuration must not be parsed', encoding='utf-8')
        with patch('worker.service.serve') as serve, patch('worker.github_auth.configure_local_github') as github_auth:
            process(self.root, 'serve', 'test-demo', demo=True)
        github_auth.assert_not_called()
        self.assertFalse(serve.call_args.args[1]['allow_credit_writes'])
        self.assertTrue(serve.call_args.args[1]['demo_mode'])
        self.assertIsNone(serve.call_args.args[4])
        self.assertTrue(json.loads(path.read_text())['allow_credit_writes'])

    def test_both_local_roles_initialize_selected_github_credentials(self):
        setup_local(self.root)
        for role in ('serve','run'):
            with self.subTest(role=role), patch('worker.github_auth.configure_local_github',return_value={'source':'github-cli','configured':True}) as auth, patch('worker.service.serve'), patch('worker.operations.run_loop'):
                process(self.root,role,'synthetic-auth-setup')
                auth.assert_called_once_with(self.root)

    def test_repeat_setup_accepts_and_preserves_github_opt_in_without_loading_credentials(self):
        setup_local(self.root)
        path=self.root/'github.local.json';path.write_text(json.dumps({'credential_source':'github-cli'}))
        before=path.read_bytes()
        with patch('worker.github_auth.configure_local_github',side_effect=AssertionError('setup must not load credentials')):
            setup_local(self.root)
        self.assertEqual(path.read_bytes(),before)

    def test_tls_public_origin_only_changes_local_probe_host_and_display_url(self):
        result = setup_local(self.root)
        path = Path(result['config'])
        config = json.loads(path.read_text())
        config['public_origin'] = 'https://review.example.invalid:9443'
        path.write_text(json.dumps(config), encoding='utf-8')
        target = health_target(self.root)
        self.assertEqual(target['health_url'], 'http://127.0.0.1:8765/healthz')
        self.assertEqual(target['health_host'], 'review.example.invalid:9443')
        self.assertEqual(target['url'], config['public_origin'])
        repeated = setup_local(self.root)
        self.assertEqual(repeated['url'], config['public_origin'])
        self.assertEqual(repeated['health_url'], target['health_url'])


if __name__ == '__main__':
    unittest.main()
