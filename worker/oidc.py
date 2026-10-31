"""Optional OpenID Connect code flow; PyJWT owns JWT cryptography.

Implements Core 1.0 ID Token validation and RFC 7636 S256 PKCE. Only
administrator-configured HTTPS issuer endpoints are trusted. Access/refresh
tokens are neither persisted nor exposed to browsers.
"""
import base64
import hashlib
import hmac
import importlib
import json
import math
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from .browser_auth import BrowserFailure, configured_origin


class OIDCError(BrowserFailure):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


class OIDCClient:
    transaction_seconds = 300

    def __init__(self, config, public_origin, identities, transport=None):
        if not isinstance(config, dict):
            raise ValueError('oidc must be a configuration object')
        if not public_origin:
            raise ValueError('OIDC requires an explicit public_origin')
        self.origin = configured_origin(public_origin)
        self.redirect_uri = self.origin + '/auth/sso/callback'
        self.issuer = config.get('issuer')
        if not isinstance(self.issuer, str):
            raise ValueError('OIDC issuer must be an HTTPS URL')
        issuer = urllib.parse.urlsplit(self.issuer)
        try:
            if (issuer.scheme != 'https' or not issuer.hostname or issuer.username or issuer.password
                    or issuer.query or issuer.fragment or issuer.port == 0
                    or not re.fullmatch(r'[a-z0-9.-]+(?::[0-9]{1,5})?', issuer.netloc)
                    or re.search(r'[\s\\]', self.issuer)):
                raise ValueError()
        except ValueError:
            raise ValueError('OIDC issuer must be canonical HTTPS without credentials/query/fragment') from None
        self.issuer_netloc = issuer.netloc
        self.client_id = config.get('client_id')
        if not isinstance(self.client_id, str) or not self.client_id.strip() or len(self.client_id) > 512:
            raise ValueError('OIDC client_id is required')
        self.subjects = config.get('subjects')
        if (not isinstance(self.subjects, dict) or not self.subjects
                or any(not isinstance(sub, str) or not 1 <= len(sub) <= 255 or not isinstance(actor, str)
                       for sub, actor in self.subjects.items())):
            raise ValueError('OIDC subjects must map fixed subject identifiers to existing local actors')
        self.identities = identities
        for actor in self.subjects.values():
            principal = identities.by_actor(actor)
            if not principal or principal.roles == frozenset({'agent'}):
                raise ValueError('OIDC subjects must map to enabled human identities')
        self.auth_method = config.get('token_endpoint_auth_method', 'client_secret_basic')
        self.secret_env = config.get('client_secret_env')
        if self.auth_method not in ('none', 'client_secret_basic'):
            raise ValueError('OIDC supports client_secret_basic or none with PKCE')
        if self.auth_method == 'client_secret_basic':
            if not isinstance(self.secret_env, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', self.secret_env):
                raise ValueError('OIDC client_secret_env is required for confidential clients')
            if not os.environ.get(self.secret_env):
                raise ValueError('Configure the OIDC client secret environment variable')
        elif self.secret_env:
            raise ValueError('OIDC public clients must not configure client_secret_env')
        try:
            self.jwt = importlib.import_module('jwt')
            importlib.import_module('cryptography.hazmat.primitives.asymmetric.rsa')
            version = tuple(int(value) for value in self.jwt.__version__.split('.')[:3])
            if version < (2, 13, 0) or version >= (3,):
                raise ImportError()
        except (ImportError, AttributeError, ValueError):
            raise ValueError('OIDC requires optional PyJWT[crypto]>=2.13.0,<3; install the oidc extra') from None
        self.transport = transport or urllib.request.build_opener(NoRedirect()).open
        self.transactions, self.lock = {}, threading.Lock()
        self.metadata, self.metadata_until = None, 0

    def endpoint(self, value):
        if not isinstance(value, str):
            raise OIDCError(502, 'OIDC discovery endpoint is invalid')
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme != 'https' or parsed.netloc != self.issuer_netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment or re.search(r'[\s\\]', value)):
            raise OIDCError(502, 'OIDC endpoints must use HTTPS on the configured issuer host and port')
        return value

    def request(self, url, form=None, headers=None):
        self.endpoint(url)
        request = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode() if form is not None else None,
            headers={'Accept': 'application/json', **({'Content-Type': 'application/x-www-form-urlencoded'} if form is not None else {}), **(headers or {})},
            method='POST' if form is not None else 'GET')
        try:
            with self.transport(request, timeout=15) as response:
                if response.geturl() != url or response.status != 200:
                    raise OIDCError(502, 'OIDC response status or redirect is invalid')
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise OIDCError(502, 'OIDC response exceeds size limit')
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError()
                return data
        except OIDCError:
            raise
        except urllib.error.HTTPError as error:
            error.close()
            raise OIDCError(502, 'OIDC provider request failed; restart sign-in') from None
        except (urllib.error.URLError, OSError, ValueError, UnicodeError):
            # Provider errors can contain authorization codes or tokens: never echo them.
            raise OIDCError(502, 'OIDC provider request failed; restart sign-in') from None

    def discovery(self):
        with self.lock:
            if self.metadata is not None and self.metadata_until > time.time():
                return dict(self.metadata)
        document = self.request(self.issuer.rstrip('/') + '/.well-known/openid-configuration')
        if document.get('issuer') != self.issuer:
            raise OIDCError(502, 'OIDC discovery issuer mismatch')
        for key in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
            self.endpoint(document.get(key))
        required_support = {'response_types_supported': 'code', 'id_token_signing_alg_values_supported': 'RS256'}
        for key, value in required_support.items():
            if not isinstance(document.get(key), list) or value not in document[key]:
                raise OIDCError(502, 'OIDC provider does not advertise the required code flow and RS256 support')
        if 'code_challenge_methods_supported' in document and (not isinstance(document['code_challenge_methods_supported'], list) or 'S256' not in document['code_challenge_methods_supported']):
            raise OIDCError(502, 'OIDC provider does not support PKCE S256')
        auth_methods = document.get('token_endpoint_auth_methods_supported', ['client_secret_basic'])
        if not isinstance(auth_methods, list) or self.auth_method not in auth_methods:
            raise OIDCError(502, 'OIDC provider does not support the configured client authentication method')
        with self.lock:
            self.metadata, self.metadata_until = dict(document), time.time() + 300
        return document

    def begin(self, previous_session=None, previous_transaction=None):
        metadata = self.discovery()
        state, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        current = time.time()
        with self.lock:
            self.transactions = {key: row for key, row in self.transactions.items()
                                 if row['expires'] > current and (not previous_transaction or row['binding'] != hashlib.sha256(previous_transaction.encode()).hexdigest())}
            if len(self.transactions) >= 1024:
                raise OIDCError(503, 'OIDC sign-in capacity reached')
            self.transactions[hashlib.sha256(state.encode()).hexdigest()] = {
                'binding': hashlib.sha256(binding.encode()).hexdigest(), 'nonce': nonce, 'verifier': verifier,
                'created': current, 'expires': current + self.transaction_seconds,
                'token_endpoint': metadata['token_endpoint'], 'jwks_uri': metadata['jwks_uri'],
                'previous_session': hashlib.sha256(previous_session.encode()).hexdigest() if previous_session else None}
        parameters = {'response_type': 'code', 'client_id': self.client_id, 'redirect_uri': self.redirect_uri,
                      'scope': 'openid', 'state': state, 'nonce': nonce, 'code_challenge_method': 'S256',
                      'code_challenge': b64url(hashlib.sha256(verifier.encode()).digest()), 'response_mode': 'query'}
        return metadata['authorization_endpoint'] + '?' + urllib.parse.urlencode(parameters), binding

    def consume(self, state, binding):
        if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value) for value in (state, binding)):
            raise OIDCError(401, 'OIDC state and browser transaction cookie are required')
        key = hashlib.sha256(state.encode()).hexdigest()
        with self.lock:
            row = self.transactions.get(key)
            if not row or row['expires'] <= time.time() or not hmac.compare_digest(row['binding'], hashlib.sha256(binding.encode()).hexdigest()):
                raise OIDCError(401, 'OIDC transaction is invalid, expired or belongs to another browser')
            # Consume before exchanging the code. A retry must start a fresh login.
            return self.transactions.pop(key)

    def verify(self, encoded, transaction, token_response):
        if not isinstance(encoded, str) or len(encoded) > 32768:
            raise OIDCError(401, 'OIDC ID token is missing or invalid')
        try:
            header = self.jwt.get_unverified_header(encoded)
            if (header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str) or not header['kid']
                    or any(key in header for key in ('jku', 'x5u', 'crit', 'b64'))):
                raise ValueError()
            jwks = self.request(transaction['jwks_uri'])
            keys = jwks.get('keys')
            if not isinstance(keys, list) or len(keys) > 100:
                raise ValueError()
            matches = [key for key in keys if isinstance(key, dict) and key.get('kid') == header['kid']
                       and key.get('kty') == 'RSA' and key.get('use', 'sig') == 'sig' and key.get('alg', 'RS256') == 'RS256'
                       and ('key_ops' not in key or key['key_ops'] == ['verify'])]
            if len(matches) != 1:
                raise ValueError()
            public_key = self.jwt.PyJWK.from_dict(matches[0], algorithm='RS256').key
            if public_key.key_size < 2048:
                raise ValueError()
            claims = self.jwt.decode(encoded, public_key, algorithms=['RS256'], audience=self.client_id, issuer=self.issuer,
                                     options={'require': ['iss', 'aud', 'exp', 'iat', 'sub', 'nonce'], 'enforce_minimum_key_length': True})
            if (claims.get('aud') not in (self.client_id, [self.client_id])
                    or ('azp' in claims and claims['azp'] != self.client_id)
                    or not isinstance(claims['nonce'], str) or not hmac.compare_digest(claims['nonce'], transaction['nonce'])
                    or not isinstance(claims['sub'], str) or not 1 <= len(claims['sub']) <= 255
                    or any(type(claims[k]) not in (int, float) or not math.isfinite(claims[k]) for k in ('iat', 'exp'))
                    or claims['iat'] < transaction['created'] - 60 or claims['exp'] <= claims['iat']):
                raise ValueError()
            if 'at_hash' in claims:
                token = token_response.get('access_token')
                if not isinstance(token, str) or not isinstance(claims['at_hash'], str) or not hmac.compare_digest(
                        claims['at_hash'], b64url(hashlib.sha256(token.encode('ascii')).digest()[:16])):
                    raise ValueError()
        except OIDCError:
            raise
        except (self.jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise OIDCError(401, 'OIDC ID token validation failed') from None
        actor = self.subjects.get(claims['sub'])
        principal = self.identities.by_actor(actor) if actor else None
        if not principal or principal.roles == frozenset({'agent'}):
            raise OIDCError(403, 'OIDC subject is not mapped to an enabled local human identity')
        return principal

    def finish(self, query, binding):
        if any(len(values) != 1 for values in query.values()):
            raise OIDCError(400, 'Duplicate OIDC callback parameters')
        transaction = self.consume(query.get('state', [None])[0], binding)
        if 'error' in query:
            raise OIDCError(401, 'OIDC sign-in was not completed')
        if 'iss' in query and query['iss'][0] != self.issuer:
            raise OIDCError(401, 'OIDC authorization response issuer mismatch')
        code = query.get('code', [None])[0]
        if not isinstance(code, str) or not 1 <= len(code) <= 2048 or any(ord(char) < 32 for char in code):
            raise OIDCError(400, 'OIDC authorization code is missing or invalid')
        form = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': self.redirect_uri,
                'client_id': self.client_id, 'code_verifier': transaction['verifier']}
        headers = {}
        if self.auth_method == 'client_secret_basic':
            secret = os.environ.get(self.secret_env)
            if not secret:
                raise OIDCError(503, 'OIDC client credentials are unavailable')
            basic = urllib.parse.quote_plus(self.client_id) + ':' + urllib.parse.quote_plus(secret)
            headers['Authorization'] = 'Basic ' + base64.b64encode(basic.encode()).decode()
        token_response = self.request(transaction['token_endpoint'], form, headers)
        principal = self.verify(token_response.get('id_token'), transaction, token_response)
        return principal, transaction['previous_session']

    @staticmethod
    def cookie_name(policy):
        return '__Host-sponsorship_oidc' if policy.secure else 'sponsorship_oidc'

    def cookie(self, policy, binding='', clear=False):
        # The one-use transaction needs Lax for the IdP top-level GET callback;
        # the actual application session remains SameSite=Strict.
        return (self.cookie_name(policy) + '=' + binding + '; Path=/; HttpOnly; SameSite=Lax; Max-Age='
                + ('0' if clear else str(self.transaction_seconds)) + ('; Secure' if policy.secure else ''))
