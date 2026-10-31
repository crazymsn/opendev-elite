import base64
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.py import bootstrap, fetcher, userinfo
from worker.db import Store
from worker.github import ApiError
from worker.pipeline import Pipeline


class FixtureGitHub:
    def __init__(self):
        self.calls = []

    def next_link(self, headers):
        return None

    def get(self, path, params=None):
        self.calls.append((path, params))
        repo = {'id': 1, 'full_name': 'org/tool', 'html_url': 'https://github.com/org/tool', 'private': False,
                'default_branch': 'main', 'owner': {'id': 2, 'login': 'org', 'type': 'Organization'}}
        if path == '/search/repositories':
            return {'items': [repo], 'total_count': 1, 'incomplete_results': False}, {}
        if path == '/repos/org/tool':
            return repo, {}
        if path.endswith('/commits/main'):
            return {'sha': 'a' * 40}, {}
        if '/git/trees/' in path:
            return {'tree': [], 'truncated': False}, {}
        if path.endswith('/readme'):
            return {'encoding': 'base64', 'content': base64.b64encode(b'# tool').decode()}, {}
        if path.endswith('/contributors'):
            return [{'id': 3, 'login': 'human', 'type': 'User'},
                    {'id': 4, 'login': 'ci[bot]', 'type': 'Bot'}, {'name': 'anonymous'}], {}
        if path == '/users/org':
            return {'id': 2, 'login': 'org', 'type': 'Organization', 'html_url': 'https://github.com/org'}, {}
        if path == '/users/human':
            return {'id': 3, 'login': 'human', 'type': 'User', 'email': 'public@example.invalid',
                    'html_url': 'https://github.com/human', 'bio': 'comma, tab\tand newline\nremain intact'}, {}
        return [], {}


class LegacyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'test.db')
        self.client = FixtureGitHub()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_production_pipeline_calls_refactored_original_modules(self):
        with patch.object(fetcher, 'search_page', wraps=fetcher.search_page) as search, \
             patch.object(fetcher, 'fetch_repository', wraps=fetcher.fetch_repository) as repository, \
             patch.object(userinfo, 'fetch_profile', wraps=userinfo.fetch_profile) as profile:
            pipeline = Pipeline(self.store, self.client)
            pipeline.schedule({'queries': [{'query': 'topic:agent', 'category': 'agent'}]}, '2026-01-01', '2026-01-02')
            self.assertTrue(all(r['status'] == 'done' for r in pipeline.work(10)))
            search.assert_called_once()
            repository.assert_called_once_with(self.client, 'org/tool')
            self.assertEqual(profile.call_count, 2)
        candidates = self.store.all('SELECT * FROM project_maintainers')
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(p['confirmed'] == 0 for p in candidates))
        human = json.loads(self.store.one('SELECT profile FROM maintainers WHERE id=3')['profile'])
        self.assertEqual(human['email'], 'public@example.invalid')
        self.assertFalse(any(path == '/users/ci[bot]' for path, _ in self.client.calls))

    def test_original_bootstrap_runs_same_pipeline_and_outputs_both_libraries(self):
        result = bootstrap.main({'base_dir': str(self.root), 'topic': 'topic:agent',
                                 'date_start': '2026-01-01', 'date_end': '2026-01-01', 'client': self.client})
        self.assertTrue(result['complete'])
        searches = [p['q'] for path, p in self.client.calls if path == '/search/repositories']
        self.assertIn('created:2026-01-01..2026-01-01', searches[0])
        with (self.root / 'Projects_Today.csv').open(encoding='utf-8-sig', newline='') as stream:
            projects = list(csv.DictReader(stream))
        with (self.root / 'GitHub_User_Library_Today.csv').open(encoding='utf-8-sig', newline='') as stream:
            users = list(csv.DictReader(stream))
        self.assertEqual(projects[0]['name'], 'org/tool')
        self.assertEqual(projects[0]['has_been_scraped'], 'True')
        self.assertEqual(len(users), 2)
        self.assertEqual(users[1]['Bio'], 'comma, tab\tand newline\nremain intact')
        result2 = bootstrap.main({'base_dir': str(self.root), 'topic': 'topic:agent',
                                  'date_start': '2026-01-01', 'date_end': '2026-01-01', 'client': self.client})
        self.assertEqual(result2['outcomes'], [])

    def test_csv_and_mislabeled_tsv_missing_homepage_are_supported(self):
        for delimiter in (',', '\t'):
            path = self.root / ('comma.csv' if delimiter == ',' else 'tab.csv')
            userinfo.write_table(path, ['GitHub_ID', 'Name'], [{'GitHub_ID': 'human', 'Name': 'manual name'}], delimiter)
            self.assertEqual(userinfo.update_user_library(path, '', client=self.client), 1)
            with path.open(encoding='utf-8-sig', newline='') as stream:
                row = next(csv.DictReader(stream, delimiter=delimiter))
            self.assertEqual(row['Name'], 'manual name')
            self.assertEqual(row['Email'], 'public@example.invalid')
            self.assertEqual(row['Homepage_URL'], 'https://github.com/human')

    def test_author_update_error_preserves_existing_file(self):
        path = self.root / 'people.csv'
        userinfo.write_table(path, ['GitHub_ID'], [{'GitHub_ID': 'human'}])
        original = path.read_bytes()
        with patch.object(self.client, 'get', side_effect=ApiError(429, 'limited', 90, True)):
            with self.assertRaises(ApiError):
                userinfo.update_library(path, '', client=self.client)
        self.assertEqual(original, path.read_bytes())

    def test_owner_candidate_role_and_manual_confirmation_are_not_overwritten(self):
        self.store.enqueue('refresh', {'repo': 'org/tool'}, 'one')
        Pipeline(self.store, self.client).work(1)
        self.assertEqual(self.store.one('SELECT role FROM project_maintainers WHERE maintainer_id=2')['role'], 'owner_candidate')
        self.store.conn.execute("UPDATE project_maintainers SET confirmed=1,role='verified maintainer',evidence='interview' WHERE maintainer_id=3")
        self.store.enqueue('refresh', {'repo': 'org/tool'}, 'two')
        Pipeline(self.store, self.client).work(1)
        relation = self.store.one('SELECT * FROM project_maintainers WHERE maintainer_id=3')
        self.assertEqual((relation['confirmed'], relation['role'], relation['evidence']), (1, 'verified maintainer', 'interview'))


if __name__ == '__main__':
    unittest.main()
