"""Read-only GitHub REST transport, conditional cache and persistent backoff."""
import email.utils
import hashlib
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from .db import dump, now


class ApiError(Exception):
    def __init__(self, status, message, retry_after=0, retryable=False):
        super().__init__(f'GitHub HTTP {status}: {message}')
        self.status, self.retry_after, self.retryable = status, retry_after, retryable


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith('https://api.github.com/'):
            raise ApiError(code, 'redirect outside GitHub API refused')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHub:
    def __init__(self, store, token=None, transport=None, min_interval=None, max_concurrency=None):
        self.store = store
        self.token = (os.environ.get('GITHUB_TOKEN', '') if token is None else token).strip()
        self.transport = transport or urllib.request.build_opener(GitHubRedirect()).open
        # GitHub's unauthenticated REST quota is 60 requests per hour. Keep
        # anonymous collection below that quota instead of creating a burst
        # that can never finish before the next retry window.
        self.interval = (2.5 if self.token else 61.0) if min_interval is None else float(min_interval)
        if self.interval < 0:
            raise ValueError('min_interval must be non-negative')
        default_concurrency = 1
        self.max_concurrency = default_concurrency if max_concurrency is None else max_concurrency
        if type(self.max_concurrency) is not int or not 1 <= self.max_concurrency <= 8:
            raise ValueError('max_concurrency must be an integer between 1 and 8')
        self.last_request = 0
        self.scope = hashlib.sha256(self.token.encode()).hexdigest()[:16]

    def _reserve_rate_slot(self):
        """Coordinate spacing and bounded in-flight requests across processes."""
        # SQLite is the shared coordination point.  Do not hold a transaction
        # while sleeping: another worker must be able to release its permit.
        while True:
            now_ts = time.time()
            with self.store.transaction():
                row = self.store.one('SELECT * FROM provider_limits WHERE provider=? AND token_fingerprint=?',
                                     ('github', self.scope))
                self.store.conn.execute("DELETE FROM provider_rate_leases WHERE lease_until<=?", (now_ts,))
                active = self.store.one("SELECT COUNT(*) AS n FROM provider_rate_leases WHERE provider=? AND token_fingerprint=?", ('github', self.scope))['n']
                in_flight = int(active)
                limit = self.max_concurrency
                if in_flight < limit:
                    next_at = max(now_ts, float(row['next_allowed_at']) if row else 0.0)
                    wait = max(0.0, next_at - now_ts)
                    reserved = next_at + max(0.0, float(self.interval))
                    if row:
                        self.store.conn.execute('''UPDATE provider_limits
                            SET next_allowed_at=?,in_flight=in_flight+1,concurrency=?,updated_at=?
                            WHERE provider=? AND token_fingerprint=?''',
                            (reserved, self.max_concurrency, str(now_ts), 'github', self.scope))
                    else:
                        self.store.conn.execute('''INSERT INTO provider_limits
                            (provider,token_fingerprint,next_allowed_at,concurrency,in_flight,updated_at)
                            VALUES(?,?,?,?,?,?)''',
                            ('github', self.scope, reserved, self.max_concurrency, 1, str(now_ts)))
                    lease_id = 'permit_' + uuid.uuid4().hex
                    self.store.conn.execute('INSERT INTO provider_rate_leases(id,provider,token_fingerprint,lease_until,created_at) VALUES(?,?,?,?,?)', (lease_id, 'github', self.scope, now_ts + 120, now()))
                    return wait, lease_id
            time.sleep(0.05)

    def _release_rate_slot(self, lease_id, defer=0):
        """Release a permit and persist a provider-wide backoff when needed."""
        with self.store.transaction():
            row = self.store.one('SELECT next_allowed_at FROM provider_limits WHERE provider=? AND token_fingerprint=?',
                                 ('github', self.scope))
            next_at = max(float(row['next_allowed_at']) if row else 0.0, time.time() + max(0.0, defer))
            self.store.conn.execute('DELETE FROM provider_rate_leases WHERE id=?', (lease_id,))
            self.store.conn.execute('''UPDATE provider_limits
                SET in_flight=MAX(0,(SELECT COUNT(*) FROM provider_rate_leases WHERE provider=? AND token_fingerprint=?)),next_allowed_at=?,updated_at=?
                WHERE provider=? AND token_fingerprint=?''',
                ('github', self.scope, next_at, str(time.time()), 'github', self.scope))

    def _observe_rate_headers(self, headers):
        try:
            remaining = int(headers.get('x-ratelimit-remaining')) if headers.get('x-ratelimit-remaining') is not None else None
        except (TypeError, ValueError):
            remaining = None
        try:
            reset = float(headers.get('x-ratelimit-reset')) if headers.get('x-ratelimit-reset') is not None else None
        except (TypeError, ValueError):
            reset = None
        with self.store.transaction():
            row = self.store.one('SELECT next_allowed_at FROM provider_limits WHERE provider=? AND token_fingerprint=?',
                                 ('github', self.scope))
            next_at = float(row['next_allowed_at']) if row else 0.0
            if remaining == 0 and reset is not None:
                next_at = max(next_at, reset + 1)
            self.store.conn.execute('''UPDATE provider_limits
                SET remaining=?,reset_at=?,next_allowed_at=?,updated_at=?
                WHERE provider=? AND token_fingerprint=?''',
                (remaining, reset, next_at, str(time.time()), 'github', self.scope))

    def get(self, path, params=None):
        if path.startswith('https://api.github.com/'):
            url = path
        elif path.startswith('/') and not path.startswith('//'):
            url = 'https://api.github.com' + path
        else:
            raise ValueError('Only GitHub API read requests are supported')
        if params:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
        cache_key = self.scope + ':' + url
        cached = self.store.one('SELECT * FROM http_cache WHERE url=?', (cache_key,))
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': 'developer-sponsorship-worker/0.1'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if cached and cached['etag']:
            headers['If-None-Match'] = cached['etag']
        shared_wait, lease_id = self._reserve_rate_slot()
        time.sleep(max(shared_wait, max(0, self.interval - (time.monotonic() - self.last_request))))
        self.last_request = time.monotonic()
        defer = 0
        try:
            with self.transport(urllib.request.Request(url, headers=headers), timeout=30) as response:
                raw_body = response.read(16 * 1024 * 1024 + 1)
                if len(raw_body) > 16 * 1024 * 1024:
                    raise ApiError(502, 'response exceeds 16 MiB; narrow the request', retryable=False)
                if not raw_body and getattr(response, 'status', None) == 204:
                    body = []
                else:
                    try:
                        body = json.loads(raw_body.decode('utf-8'))
                    except (ValueError, UnicodeError):
                        raise ApiError(502, 'invalid JSON response', 30, True) from None
                response_headers = {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as e:
            if e.code == 304 and cached:
                return json.loads(cached['body']), json.loads(cached['headers'])
            h = {k.lower(): v for k, v in (e.headers or {}).items()}
            retry = 0
            limited = e.code == 429 or (e.code == 403 and
                       (h.get('x-ratelimit-remaining') == '0' or 'retry-after' in h))
            raw = e.read(4096).decode('utf-8', errors='replace').lower()
            limited = limited or (e.code == 403 and ('rate limit' in raw or 'abuse' in raw))
            if limited:
                retry = 60
                if 'retry-after' in h:
                    try:
                        retry = max(retry, float(h['retry-after']))
                    except ValueError:
                        try:
                            retry = max(retry, email.utils.parsedate_to_datetime(h['retry-after']).timestamp() - time.time())
                        except (ValueError, TypeError, OverflowError):
                            pass
                if h.get('x-ratelimit-remaining') == '0':
                    try:
                        retry = max(retry, float(h.get('x-ratelimit-reset', 0)) - time.time() + 1)
                    except (ValueError, TypeError, OverflowError):
                        pass
                defer = retry + 1
            raise ApiError(e.code, 'rate limited' if limited else 'request failed',
                           retry + random.uniform(1, 3), limited or e.code >= 500) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ApiError(0, 'network failure', 30, True) from None
        finally:
            self._release_rate_slot(lease_id, defer)
        self._observe_rate_headers(response_headers)
        self.store.conn.execute('INSERT OR REPLACE INTO http_cache VALUES(?,?,?,?)',
                                (cache_key, response_headers.get('etag'), dump(body), dump(response_headers)))
        return body, response_headers

    @staticmethod
    def next_link(headers):
        match = re.search(r'<([^>]+)>;\s*rel="next"', headers.get('link', ''))
        return match.group(1) if match else None

    def pages(self, path, params=None, max_pages=3):
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages < 1:
            raise ValueError('max_pages must be a positive integer')
        items = []
        for _ in range(max_pages):
            body, headers = self.get(path, params)
            if not isinstance(body, list):
                raise ApiError(502, 'expected a paginated list response', 30, True)
            items.extend(body)
            path, params = self.next_link(headers), None
            if not path:
                return items, False
        return items, bool(path)
