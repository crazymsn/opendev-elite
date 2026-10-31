import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from worker.db import Store
from worker.github import ApiError, GitHub
from worker.pipeline import Pipeline, normalize_repo


class CrawlerHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'test.db')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_missing_link_enqueues_remaining_search_page_and_marks_partial(self):
        class Fake:
            def get(self, *args):
                return {'total_count': 200, 'items': []}, {}

            def next_link(self, headers):
                return None
        pipeline = Pipeline(self.store, Fake())
        pipeline.schedule({'queries': [{'query': 'topic:agent', 'category': 'agent'}]}, '2026-01-01', '2026-01-01')
        pipeline.search(self.store.claim())
        coverage = self.store.one('SELECT * FROM search_coverage')
        self.assertEqual(coverage['status'], 'partial')
        queued = self.store.one("SELECT payload FROM jobs WHERE state='queued'")
        self.assertEqual(json.loads(queued['payload'])['page'], 2)

    def test_exhausted_single_day_single_star_partition_is_explicitly_partial(self):
        class Fake:
            def get(self, *args):
                return {'total_count': 1200, 'items': []}, {}

            def next_link(self, headers):
                return None
        payload = dict(query='topic:agent', category='agent', start='2026-01-01', end='2026-01-01',
                       stars_min=0, stars_max=0, run='2026-01-01', page=10)
        self.store.enqueue('search', payload, 'search')
        Pipeline(self.store, Fake()).search(self.store.claim())
        self.assertEqual(self.store.one('SELECT status FROM search_coverage')['status'], 'partial')
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM jobs WHERE state='queued'")['n'], 0)

    def test_expired_lease_during_http_response_cannot_write_projects_or_coverage(self):
        store = self.store
        class Fake:
            def get(self, *args):
                store.conn.execute('UPDATE jobs SET lease_until=0')
                return {'total_count': 1, 'items': [{'id': 1, 'full_name': 'a/b', 'html_url': 'https://github.com/a/b', 'private': False}]}, {}

            def next_link(self, headers):
                return None
        pipeline = Pipeline(self.store, Fake())
        pipeline.schedule({'queries': [{'query': 'topic:agent', 'category': 'agent'}]}, '2026-01-01', '2026-01-01')
        with self.assertRaises(ValueError):
            pipeline.search(self.store.claim())
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM projects')['n'], 0)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM search_coverage')['n'], 0)

    def test_invalid_retry_headers_still_defer(self):
        def transport(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden',
                                         {'retry-after': 'nonsense', 'x-ratelimit-remaining': '0', 'x-ratelimit-reset': 'bad'},
                                         io.BytesIO(b'{"message":"rate limit"}'))
        with self.assertRaises(ApiError) as caught:
            GitHub(self.store, token='', transport=transport, min_interval=0).get('/repos/a/b')
        self.assertTrue(caught.exception.retryable)
        self.assertGreaterEqual(caught.exception.retry_after, 60)

    def test_invalid_json_is_retryable_and_not_cached(self):
        class Response:
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args): return b'<html>upstream unavailable</html>'
        with self.assertRaises(ApiError) as caught:
            GitHub(self.store, token='', transport=lambda *a, **k: Response(), min_interval=0).get('/repos/a/b')
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM http_cache')['n'], 0)

    def test_no_content_list_endpoint_returns_empty_list(self):
        class Response:
            status, headers = 204, {}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, *args): return b''
        client = GitHub(self.store, token='', transport=lambda *a, **k: Response(), min_interval=0)
        self.assertEqual(client.pages('/repos/a/b/contributors'), ([], False))

    def test_pages_enforces_shape_and_pagination_budget(self):
        client = GitHub(self.store, token='', min_interval=0)
        client.get = lambda *a, **k: ({'message': 'not a list'}, {})
        with self.assertRaises(ApiError):
            client.pages('/repos/a/b/contributors')
        with self.assertRaises(ValueError):
            client.pages('/repos/a/b/contributors', max_pages=0)
        client.get = lambda *a, **k: ([{'id': 1}], {'link': '<https://api.github.com/repos/a/b/contributors?page=2>; rel="next"'})
        self.assertEqual(client.pages('/repos/a/b/contributors', max_pages=1), ([{'id': 1}], True))

    def test_traversal_repository_names_are_rejected(self):
        for repo in ('../b', 'a/..', 'a/.', '//outside/b', 'a/b/../c'):
            with self.assertRaises(ValueError):
                normalize_repo(repo)

    def test_global_authorization_failure_is_not_partial_success(self):
        class Fake:
            def get(self, path, params=None):
                if path == '/repos/a/b':
                    return {'id': 1, 'full_name': 'a/b', 'html_url': 'https://github.com/a/b', 'private': False,
                            'owner': {'id': 2, 'login': 'a'}, 'default_branch': 'main'}, {}
                raise ApiError(401, 'unauthorized')
        self.store.enqueue('refresh', {'repo': 'a/b'}, 'one')
        result = Pipeline(self.store, Fake()).work(1)
        self.assertEqual(result[0]['status'], 'dead')
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM snapshots')['n'], 0)


if __name__ == '__main__':
    unittest.main()
