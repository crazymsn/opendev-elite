import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from worker.browser_auth import BrowserPolicy, BrowserSessions, configured_origin
from worker.db import Store, dump, now
from worker.demo import seed
from worker.service import Application, Server


class BrowserAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'test.db'
        self.store = Store(self.db)
        seed(self.store)
        seed(self.store, project_id=102, maintainer_id=202)
        self.tokens = {'reviewer': 'r' * 48, 'operator': 'o' * 48, 'agent': 'a' * 48, 'admin': 'm' * 48}
        self.config = {'users': [{'actor': actor, 'roles': [actor], 'token_sha256': hashlib.sha256(token.encode()).hexdigest()}
                                 for actor, token in self.tokens.items()]}
        self.app = Application(self.db, self.config)
        self.server = Server(('127.0.0.1', 0), self.app)
        self.port = self.server.server_port
        self.origin = 'http://127.0.0.1:' + str(self.port)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()
        self.tmp.cleanup()

    def request(self, path, method='GET', body=None, cookie=None, csrf=None, origin=None, headers=None):
        req_headers = dict(headers or {})
        if cookie:
            req_headers['Cookie'] = cookie
        if csrf:
            req_headers['X-CSRF-Token'] = csrf
        if origin is not None:
            req_headers['Origin'] = origin
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            req_headers['Content-Type'] = 'application/json'
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            connection.request(method, path, body=data, headers=req_headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), json.loads(raw) if 'application/json' in response.getheader('Content-Type', '') else raw
        finally:
            connection.close()

    def login(self, actor='reviewer', cookie=None):
        status, headers, body = self.request('/auth/login', 'POST', {'token': self.tokens[actor]}, cookie=cookie, origin=self.origin)
        self.assertEqual(status, 200, body)
        return headers['Set-Cookie'].split(';')[0], body

    def test_cookie_login_csrf_and_no_raw_credentials_in_session(self):
        cookie, session = self.login()
        self.assertEqual(session['actor'], 'reviewer')
        status, headers, me = self.request('/auth/me', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(me, session)
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertNotIn('unsafe-inline', headers['Content-Security-Policy'])
        self.assertEqual(self.request('/auth/csrf', cookie=cookie)[2], {'csrf_token': session['csrf_token']})
        self.assertNotIn(self.tokens['reviewer'], repr(self.app.sessions.rows))
        self.assertNotIn(cookie.split('=', 1)[1], repr(self.app.sessions.rows))

    def test_login_requires_exact_origin_and_rejects_agent(self):
        for origin in (None, 'null', 'https://attacker.invalid', self.origin + '.attacker.invalid'):
            status = self.request('/auth/login', 'POST', {'token': self.tokens['reviewer']}, origin=origin)[0]
            self.assertEqual(status, 403)
        self.assertEqual(self.request('/auth/login', 'POST', {'token': self.tokens['agent']}, origin=self.origin)[0], 403)

    def test_cookie_flags_and_rotation_prevent_session_fixation(self):
        status, headers, _ = self.request('/auth/login', 'POST', {'token': self.tokens['reviewer']}, origin=self.origin)
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        first = headers['Set-Cookie'].split(';')[0]
        second, _ = self.login(cookie=first)
        self.assertNotEqual(first, second)
        self.assertEqual(self.request('/auth/me', cookie=first)[0], 401)
        self.assertEqual(self.request('/auth/me', cookie=second)[0], 200)
        invented = 'sponsorship_session=' + 'z' * 43
        self.assertEqual(self.request('/auth/me', cookie=invented)[0], 401)

    def test_browser_mutation_requires_csrf_origin_and_idempotency(self):
        cookie, session = self.login()
        body = {'project_id': 101, 'audience': 'both', 'owner': 'new-owner'}
        headers = {'Idempotency-Key': 'browser-assign-001'}
        path = '/api/actions/assign'
        self.assertEqual(self.request(path, 'POST', body, cookie, origin=self.origin, headers=headers)[0], 403)
        self.assertEqual(self.request(path, 'POST', body, cookie, 'wrong', self.origin, headers)[0], 403)
        self.assertEqual(self.request(path, 'POST', body, cookie, session['csrf_token'], headers=headers)[0], 403)
        self.assertEqual(self.request(path, 'POST', body, cookie, session['csrf_token'], self.origin)[0], 400)
        self.assertEqual(self.request(path, 'POST', body, cookie, session['csrf_token'], self.origin, headers)[0], 200)
        self.assertEqual(self.store.one('SELECT owner FROM projects WHERE id=101')['owner'], 'new-owner')

    def test_session_has_no_extra_role_and_actor_cannot_be_spoofed(self):
        cookie, session = self.login()
        self.assertEqual(self.request('/api/records/grants', cookie=cookie)[0], 403)
        headers = {'Idempotency-Key': 'browser-decision-001'}
        self.assertEqual(self.request('/api/actions/decision', 'POST', {}, cookie, session['csrf_token'], self.origin, headers)[0], 403)
        self.assertEqual(self.request('/api/actions/assign', 'POST', {'actor': 'admin'}, cookie, session['csrf_token'], self.origin, headers)[0], 400)

    def test_logout_destroys_session_and_expiry_is_enforced(self):
        cookie, session = self.login()
        self.assertEqual(self.request('/auth/logout', 'POST', {}, cookie, origin=self.origin)[0], 403)
        status, headers, result = self.request('/auth/logout', 'POST', {}, cookie, session['csrf_token'], self.origin)
        self.assertEqual(status, 200)
        self.assertTrue(result['logged_out'])
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.assertEqual(self.request('/auth/me', cookie=cookie)[0], 401)
        cookie, _ = self.login()
        with self.app.sessions.lock:
            for row in self.app.sessions.rows.values():
                row['expires'] = 0
        self.assertEqual(self.request('/api/projects', cookie=cookie)[0], 401)

    def test_dns_rebinding_host_and_forwarded_header_are_untrusted(self):
        self.assertEqual(self.request('/healthz', headers={'Host': 'attacker.invalid:' + str(self.port)})[0], 403)
        self.assertEqual(self.request('/healthz', headers={'Host': 'attacker.invalid', 'X-Forwarded-Host': '127.0.0.1:' + str(self.port)})[0], 403)
        cookie, _ = self.login()
        self.assertEqual(self.request('/api/projects', cookie=cookie, headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('/auth/login', 'POST', {'token': self.tokens['reviewer']}, origin='http://localhost:' + str(self.port))[0], 403)

    def test_bearer_agent_api_is_compatible_without_origin(self):
        status, _, me = self.request('/api/me', headers={'Authorization': 'Bearer ' + self.tokens['agent']})
        self.assertEqual(status, 200)
        self.assertEqual(me['roles'], ['agent'])
        cookie, _ = self.login()
        self.assertEqual(self.request('/api/me', cookie=cookie, headers={'Authorization': 'Bearer ' + self.tokens['admin']})[0], 400)

    def test_ui_config_exposes_only_role_actions_not_private_config(self):
        self.app.config['deployment_secret'] = 'never-export-this'
        cookie, _ = self.login()
        status, _, config = self.request('/api/ui-config', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn('review', config['actions'])
        self.assertNotIn('decision', config['actions'])
        self.assertNotIn('grants', config['records'])
        self.assertFalse(config['capabilities']['credits_submit'])
        self.assertNotIn('deployment_secret', config)
        self.assertNotIn('users', config)

    def test_filters_are_server_side_and_preserve_role_checks(self):
        self.store.conn.execute("UPDATE projects SET stage='ready_contact' WHERE id=101")
        for project_id in (101, 102):
            self.store.conn.execute('INSERT INTO experiences(id,project_id,sha,audience,spec,created_at) VALUES(?,?,?,?,?,?)',
                                    ('experience-' + str(project_id), project_id, 'a' * 40, 'human', '{}', now()))
        cookie, _ = self.login()
        status, _, projects = self.request('/api/projects?stage=ready_contact&q=101', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual([p['id'] for p in projects['items']], [101])
        status, _, records = self.request('/api/records/experiences?project_id=101&status=pending', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual([r['project_id'] for r in records['items']], [101])
        self.assertEqual(self.request('/api/records/grants?project_id=101', cookie=cookie)[0], 403)
        self.assertEqual(self.request('/api/records/experiences?project_id=9999999999999999999', cookie=cookie)[0], 400)

    def test_https_origin_is_explicit_and_sets_host_cookie_security(self):
        policy = BrowserPolicy('https://sponsor.example:8443', self.port)
        policy.check('sponsor.example:8443', 'https://sponsor.example:8443', require_origin=True)
        self.assertIn('__Host-', policy.cookie('a' * 43, 300))
        self.assertIn('; Secure', policy.cookie('a' * 43, 300))
        for origin in ('https://example.test/', 'http://example.test', 'https://u:p@example.test', 'https://example.test?q=x'):
            with self.assertRaises(ValueError):
                configured_origin(origin)
        self.assertEqual(configured_origin('http://127.0.0.1:8765'), 'http://127.0.0.1:8765')

    def test_restarting_application_and_revoking_identity_invalidate_session(self):
        cookie, _ = self.login()
        second = Application(self.db, self.config)
        self.assertIsNone(second.sessions.resolve(cookie.split('=', 1)[1]))
        self.app.identities.users = [(p, digest) for p, digest in self.app.identities.users if p.actor != 'reviewer']
        self.assertEqual(self.request('/auth/me', cookie=cookie)[0], 401)

    def test_static_assets_have_strict_csp_and_no_path_traversal(self):
        fake = mock.Mock()
        fake.joinpath.return_value.read_bytes.return_value = b'<!doctype html><title>Fixture</title>'
        with mock.patch('worker.service.resources.files', return_value=fake):
            status, headers, body = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertEqual(body, b'<!doctype html><title>Fixture</title>')
        self.assertNotIn('unsafe-inline', headers['Content-Security-Policy'])
        self.assertNotEqual(self.request('/assets/../auth.py')[0], 200)

    def test_cross_site_top_level_ui_navigation_is_allowed(self):
        fake = mock.Mock()
        fake.joinpath.return_value.read_bytes.return_value = b'<!doctype html><title>Fixture</title>'
        headers = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
        with mock.patch('worker.service.resources.files', return_value=fake):
            status, _, body = self.request('/', headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(body, b'<!doctype html><title>Fixture</title>')

    def test_forms_script_is_an_explicit_static_asset(self):
        fake = mock.Mock()
        fake.joinpath.return_value.read_bytes.return_value = b'window.SponsorshipForms = {};'
        with mock.patch('worker.service.resources.files', return_value=fake):
            status, headers, body = self.request('/assets/forms.js')
        self.assertEqual(status, 200)
        self.assertIn('text/javascript', headers['Content-Type'])
        self.assertEqual(body, b'window.SponsorshipForms = {};')
        fake.joinpath.assert_called_once_with('web', 'forms.js')
        self.assertNotIn('unsafe-inline', headers['Content-Security-Policy'])


if __name__ == '__main__':
    unittest.main()
