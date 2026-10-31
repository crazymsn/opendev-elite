"""Bounded in-memory browser sessions and explicit origin trust.

Sessions contain identity names, never their bearer credentials. Restarting the
service invalidates all sessions; this is local/team authentication, not SSO.
"""
import datetime as dt
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import re
import secrets
import threading
import time
from urllib.parse import urlsplit


class BrowserFailure(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def configured_origin(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('public_origin must be a canonical HTTPS origin')
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError('Invalid public_origin port') from None
    allowed_scheme = parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1'))
    if (not allowed_scheme or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or value != parsed.scheme + '://' + parsed.netloc
            or not re.fullmatch(r'[a-z0-9.-]+(?::[0-9]{1,5})?', parsed.netloc)
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError('public_origin must be a canonical HTTPS origin without path or credentials')
    return value


class BrowserPolicy:
    def __init__(self, public_origin, port, secure=False):
        configured = configured_origin(public_origin)
        scheme = 'https' if secure else 'http'
        suffix = '' if port == (443 if secure else 80) else ':' + str(port)
        self.origins = {configured} if configured else {scheme + '://127.0.0.1' + suffix, scheme + '://localhost' + suffix}
        self.hosts = {urlsplit(origin).netloc for origin in self.origins}
        self.secure = configured.startswith('https://') if configured else secure
        self.cookie_name = '__Host-sponsorship_session' if self.secure else 'sponsorship_session'

    def check(self, host, origin=None, fetch_site=None, require_origin=False):
        # Never derive trust from Host, Forwarded, X-Forwarded-Host or Origin.
        if host not in self.hosts:
            raise BrowserFailure(403, 'Untrusted Host header')
        if (origin is not None and (origin not in self.origins or urlsplit(origin).netloc != host)) or (require_origin and origin not in self.origins):
            raise BrowserFailure(403, 'Exact same-origin request required')
        if fetch_site in ('cross-site', 'same-site'):
            raise BrowserFailure(403, 'Cross-origin browser requests are not supported')

    def cookie_id(self, header, name=None):
        if not header:
            return None
        if len(header) > 8192:
            raise BrowserFailure(400, 'Cookie header too long')
        name = name or self.cookie_name
        matching = [part for part in header.split(';') if part.strip().split('=', 1)[0] == name]
        if len(matching) > 1:
            raise BrowserFailure(400, 'Ambiguous session cookie')
        try:
            cookies = SimpleCookie()
            cookies.load(header)
        except CookieError:
            raise BrowserFailure(400, 'Invalid cookie') from None
        value = cookies[name].value if name in cookies else None
        return value if value and re.fullmatch(r'[A-Za-z0-9_-]{43}', value) else None

    def cookie(self, session_id='', ttl=0):
        return (self.cookie_name + '=' + session_id + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=' + str(ttl)
                + ('; Secure' if self.secure else ''))


class BrowserSessions:
    def __init__(self, identities, ttl=3600):
        if type(ttl) is not int or not 300 <= ttl <= 28800:
            raise ValueError('browser_session_seconds must be 300..28800')
        self.identities, self.ttl = identities, ttl
        self.rows, self.lock = {}, threading.Lock()

    @staticmethod
    def key(session_id):
        return hashlib.sha256(session_id.encode()).hexdigest()

    def create(self, principal, previous=None):
        if principal.roles == frozenset({'agent'}):
            raise BrowserFailure(403, 'Agent credentials cannot create browser sessions')
        session_id, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.lock:
            current = time.time()
            self.rows = {k: v for k, v in self.rows.items() if v['expires'] > current}
            if previous:
                self.rows.pop(self.key(previous), None)
            if len(self.rows) >= 2048:
                raise BrowserFailure(503, 'Browser session capacity reached')
            self.rows[self.key(session_id)] = {'actor': principal.actor, 'csrf': csrf, 'expires': current + self.ttl,
                                               'auth_version': principal.auth_version}
        resolved = self.resolve(session_id)
        if resolved is None:
            raise BrowserFailure(401, 'Account changed; please sign in again')
        return session_id, resolved

    def resolve(self, session_id):
        if not session_id:
            return None
        with self.lock:
            row = self.rows.get(self.key(session_id))
            if not row:
                return None
            principal = self.identities.by_actor(row['actor'])
            if (row['expires'] <= time.time() or principal is None or principal.roles == frozenset({'agent'})
                    or row['auth_version'] != principal.auth_version):
                self.rows.pop(self.key(session_id), None)
                return None
            return {'principal': principal, 'actor': principal.actor, 'roles': sorted(principal.roles),
                    'display_name': principal.display_name or principal.actor,
                    'csrf_token': row['csrf'], 'expires_at': dt.datetime.fromtimestamp(row['expires'], dt.timezone.utc).isoformat()}

    def verify_csrf(self, session, csrf):
        if not session or not isinstance(csrf, str) or not hmac.compare_digest(session['csrf_token'], csrf):
            raise BrowserFailure(403, 'Valid session CSRF token required')

    def revoke(self, session_id):
        if session_id:
            with self.lock:
                self.rows.pop(self.key(session_id), None)

    def revoke_digest(self, digest):
        if digest:
            with self.lock:
                self.rows.pop(digest, None)

    def revoke_actor(self, actor):
        with self.lock:
            self.rows = {key: row for key, row in self.rows.items() if row['actor'] != actor}
