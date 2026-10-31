"""Offline fake-IdP tests with real RSA signatures; no production IdP calls."""
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

from worker.auth import IdentityStore
from worker.browser_auth import BrowserPolicy
from worker.oidc import OIDCClient, OIDCError, b64url
from worker.service import Application, Server

HAS_CRYPTO = importlib.util.find_spec('jwt') is not None and importlib.util.find_spec('cryptography') is not None
if HAS_CRYPTO:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa


class Reply:
    def __init__(self, url, data, status=200):
        self.url, self.status = url, status
        self.raw = json.dumps(data).encode()
    def geturl(self): return self.url
    def read(self, size): return self.raw[:size]
    def __enter__(self): return self
    def __exit__(self, *_): pass


@unittest.skipUnless(HAS_CRYPTO, 'Install optional oidc extra for signed fake-IdP tests')
class OIDCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        token = 'r' * 48
        self.identities = IdentityStore({'users': [{'actor': 'reviewer', 'roles': ['reviewer'],
            'token_sha256': hashlib.sha256(token.encode()).hexdigest()}]})
        self.config = {'issuer': 'https://idp.example/tenant', 'client_id': 'sponsorship-client',
                       'subjects': {'stable-subject': 'reviewer'}, 'token_endpoint_auth_method': 'none'}
        self.metadata = {'issuer': self.config['issuer'], 'authorization_endpoint': 'https://idp.example/authorize',
                         'token_endpoint': 'https://idp.example/token', 'jwks_uri': 'https://idp.example/jwks',
                         'response_types_supported': ['code'], 'id_token_signing_alg_values_supported': ['RS256'],
                         'code_challenge_methods_supported': ['S256'], 'token_endpoint_auth_methods_supported': ['none', 'client_secret_basic']}
        self.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwk.update(kid='key-one', use='sig', alg='RS256')
        self.keyset = {'keys': [self.jwk]}
        self.requests, self.overrides, self.header = [], {}, {'kid': 'key-one'}
        self.signing_key, self.algorithm = self.key, 'RS256'
        self.client = OIDCClient(self.config, 'http://127.0.0.1:8765', self.identities, transport=self.transport)

    def transport(self, request, timeout):
        self.requests.append(request)
        if '/.well-known/' in request.full_url:
            return Reply(request.full_url, self.metadata)
        if request.full_url.endswith('/jwks'):
            return Reply(request.full_url, self.keyset)
        if request.full_url.endswith('/token'):
            form = parse_qs(request.data.decode())
            self.assertEqual(form['grant_type'], ['authorization_code'])
            self.assertEqual(form['redirect_uri'], [self.client.redirect_uri])
            self.assertEqual(form['code'], ['fixture-code'])
            self.assertEqual(b64url(hashlib.sha256(form['code_verifier'][0].encode()).digest()), self.challenge)
            current = int(time.time())
            claims = {'iss': self.config['issuer'], 'aud': self.config['client_id'], 'sub': 'stable-subject',
                      'nonce': self.nonce, 'iat': current, 'exp': current + 300, **self.overrides}
            for name, value in list(claims.items()):
                if value is None:
                    del claims[name]
            encoded = jwt.encode(claims, self.signing_key, algorithm=self.algorithm, headers=self.header)
            return Reply(request.full_url, {'id_token': encoded, 'access_token': 'fixture-access-token', 'token_type': 'Bearer'})
        raise AssertionError('Unexpected network destination')

    def begin(self):
        location, binding = self.client.begin()
        parameters = parse_qs(urlsplit(location).query)
        self.nonce, self.challenge = parameters['nonce'][0], parameters['code_challenge'][0]
        return parameters['state'][0], binding, parameters

    def finish(self, state, binding):
        return self.client.finish({'state': [state], 'code': ['fixture-code']}, binding)

    def test_signed_code_pkce_flow_maps_only_fixed_subject(self):
        state, binding, params = self.begin()
        self.assertEqual(params['code_challenge_method'], ['S256'])
        self.assertEqual(params['scope'], ['openid'])
        self.assertEqual(params['response_type'], ['code'])
        principal, previous = self.finish(state, binding)
        self.assertEqual(principal.actor, 'reviewer')
        self.assertEqual(principal.roles, frozenset({'reviewer'}))
        self.assertIsNone(previous)
        self.assertEqual(self.client.transactions, {})
        self.assertFalse(any('fixture-access-token' in repr(row) for row in self.client.transactions.values()))

    def test_missing_wrong_and_replayed_cookie_state_never_exchange_code(self):
        state, binding, _ = self.begin()
        for supplied_state, supplied_binding in ((state, None), ('x' * 43, binding), (state, 'x' * 43)):
            with self.assertRaises(OIDCError): self.finish(supplied_state, supplied_binding)
        self.assertFalse(any(r.method == 'POST' for r in self.requests))
        self.finish(state, binding)
        with self.assertRaises(OIDCError): self.finish(state, binding)
        self.assertEqual(sum(r.method == 'POST' for r in self.requests), 1)

    def test_expired_transactions_and_duplicate_query_are_rejected(self):
        state, binding, _ = self.begin()
        for row in self.client.transactions.values(): row['expires'] = 0
        with self.assertRaises(OIDCError): self.finish(state, binding)
        with self.assertRaises(OIDCError):
            self.client.finish({'state': [state, state], 'code': ['fixture-code']}, binding)
        self.assertFalse(any(r.method == 'POST' for r in self.requests))

    def test_invalid_id_token_claims_fail_closed(self):
        bad_claims = [{'nonce': 'wrong'}, {'iss': 'https://idp.example'}, {'aud': 'different-client'},
                      {'aud': [self.config['client_id'], 'untrusted-audience'], 'azp': self.config['client_id']},
                      {'azp': 'different-client'}, {'exp': int(time.time()) - 60}, {'iat': int(time.time()) + 300},
                      {'iat': int(time.time()) - 600}, {'sub': None}, {'nonce': None}, {'exp': None},
                      {'at_hash': 'wrong-access-token-hash'}]
        for overrides in bad_claims:
            with self.subTest(claims=overrides):
                self.overrides = overrides
                state, binding, _ = self.begin()
                with self.assertRaises(OIDCError): self.finish(state, binding)
                with self.assertRaises(OIDCError): self.finish(state, binding)

    def test_signature_and_algorithm_confusion_are_rejected(self):
        self.signing_key = self.wrong_key
        state, binding, _ = self.begin()
        with self.assertRaises(OIDCError): self.finish(state, binding)
        self.algorithm, self.signing_key = 'HS256', 'fake-hmac-secret-long-enough-for-test'
        state, binding, _ = self.begin()
        with self.assertRaises(OIDCError): self.finish(state, binding)

    def test_token_key_urls_and_ambiguous_jwks_are_rejected(self):
        self.header['jku'] = 'https://attacker.invalid/jwks'
        state, binding, _ = self.begin()
        with self.assertRaises(OIDCError): self.finish(state, binding)
        self.header = {'kid': 'key-one'}
        self.keyset['keys'].append(dict(self.jwk))
        state, binding, _ = self.begin()
        with self.assertRaises(OIDCError): self.finish(state, binding)
        self.assertFalse(any('attacker.invalid' in r.full_url for r in self.requests))

    def test_email_and_roles_claims_cannot_create_or_upgrade_identity(self):
        self.overrides = {'sub': 'unknown-subject', 'email': 'reviewer', 'roles': ['admin']}
        state, binding, _ = self.begin()
        with self.assertRaises(OIDCError) as error: self.finish(state, binding)
        self.assertEqual(error.exception.status, 403)
        self.overrides = {'roles': ['admin'], 'email': 'admin@example.test'}
        state, binding, _ = self.begin()
        self.assertEqual(self.finish(state, binding)[0].roles, frozenset({'reviewer'}))

    def test_discovery_issuer_and_cross_host_endpoints_are_rejected(self):
        for key, value in (('issuer', 'https://wrong.example'), ('token_endpoint', 'https://wrong.example/token'),
                           ('jwks_uri', 'http://idp.example/jwks'), ('authorization_endpoint', 'https://idp.example.evil/authorize')):
            old = self.metadata[key]
            self.metadata[key] = value
            self.client.metadata = None
            with self.subTest(key=key), self.assertRaises(OIDCError): self.begin()
            self.metadata[key] = old

    def test_redirects_and_oversized_json_are_never_followed(self):
        self.client.transport = lambda request, timeout: Reply('https://attacker.invalid/', {})
        with self.assertRaises(OIDCError): self.begin()
        class Huge(Reply):
            def read(self, size): return b'x' * size
        self.client.transport = lambda request, timeout: Huge(request.full_url, {})
        with self.assertRaises(OIDCError): self.begin()

    def test_confidential_client_uses_env_secret_and_basic_header(self):
        config = dict(self.config, token_endpoint_auth_method='client_secret_basic', client_secret_env='TEST_OIDC_SECRET')
        with mock.patch.dict(os.environ, {'TEST_OIDC_SECRET': 'fixture secret'}):
            self.client = OIDCClient(config, 'http://127.0.0.1:8765', self.identities, self.transport)
            state, binding, _ = self.begin()
            self.finish(state, binding)
        post = next(request for request in self.requests if request.method == 'POST')
        self.assertTrue(post.get_header('Authorization').startswith('Basic '))
        self.assertNotIn(b'client_secret', post.data)
        self.assertNotIn('fixture secret', repr(self.client.transactions))

    def test_optional_dependency_error_is_explicit(self):
        original = __import__('importlib').import_module
        def missing(name):
            if name == 'jwt': raise ImportError()
            return original(name)
        with mock.patch('worker.oidc.importlib.import_module', side_effect=missing):
            with self.assertRaisesRegex(ValueError, 'optional PyJWT'):
                OIDCClient(self.config, 'http://127.0.0.1:8765', self.identities)

    def test_trailing_slash_issuer_and_valid_optional_claims(self):
        self.config['issuer'] = 'https://idp.example/tenant/'
        self.metadata['issuer'] = self.config['issuer']
        self.client = OIDCClient(self.config, 'http://127.0.0.1:8765', self.identities, self.transport)
        self.overrides = {'aud': [self.config['client_id']], 'azp': self.config['client_id'],
                          'at_hash': b64url(hashlib.sha256(b'fixture-access-token').digest()[:16])}
        state, binding, _ = self.begin()
        self.assertEqual(self.finish(state, binding)[0].actor, 'reviewer')
        self.assertEqual(self.requests[0].full_url, 'https://idp.example/tenant/.well-known/openid-configuration')

    def test_provider_error_consumes_transaction_without_token_exchange(self):
        state, binding, _ = self.begin()
        with self.assertRaises(OIDCError):
            self.client.finish({'state': [state], 'error': ['access_denied'], 'error_description': ['sensitive provider detail']}, binding)
        with self.assertRaises(OIDCError): self.finish(state, binding)
        self.assertFalse(any(request.method == 'POST' for request in self.requests))

    def test_real_http_fake_idp_callback_creates_strict_session(self):
        with tempfile.TemporaryDirectory() as directory:
            user = {'actor': 'reviewer', 'roles': ['reviewer'], 'token_sha256': hashlib.sha256(('r' * 48).encode()).hexdigest()}
            app = Application(Path(directory) / 'test.db', {'users': [user]})
            server = Server(('127.0.0.1', 0), app)
            app.config['public_origin'] = 'http://127.0.0.1:' + str(server.server_port)
            app.oidc = self.client = OIDCClient(self.config, app.config['public_origin'], app.identities, self.transport)
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
            thread.start()
            def request(path, headers=None):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                try:
                    connection.request('GET', path, headers=headers or {})
                    response = connection.getresponse()
                    body = response.read()
                    return response.status, response.getheaders(), body
                finally: connection.close()
            try:
                status, headers, raw = request('/auth/options')
                self.assertEqual(status, 200)
                self.assertTrue(json.loads(raw)['sso_enabled'])
                status, headers, _ = request('/auth/sso/login')
                self.assertEqual(status, 302)
                location = dict(headers)['Location']
                params = parse_qs(urlsplit(location).query)
                self.nonce, self.challenge = params['nonce'][0], params['code_challenge'][0]
                transaction_cookie = dict(headers)['Set-Cookie'].split(';')[0]
                callback = '/auth/sso/callback?' + urlencode({'state': params['state'][0], 'code': 'fixture-code'})
                status, headers, _ = request(callback, {'Cookie': transaction_cookie, 'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate'})
                self.assertEqual(status, 303)
                cookies = [value for key, value in headers if key == 'Set-Cookie']
                self.assertEqual(len(cookies), 2)
                self.assertIn('Max-Age=0', cookies[0])
                self.assertIn('SameSite=Strict', cookies[1])
                status, _, raw = request('/auth/me', {'Cookie': cookies[1].split(';')[0]})
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(raw)['actor'], 'reviewer')
                self.assertNotIn('fixture-access-token', raw.decode())
                self.assertEqual(request(callback, {'Cookie': transaction_cookie})[0], 401)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__': unittest.main()
