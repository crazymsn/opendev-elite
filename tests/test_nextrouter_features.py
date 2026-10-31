import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from worker.ai_assessment import assess_project_snapshot
from worker.contact_points import put_point
from worker.community import create_referral_link, submit_referral
from worker.db import Store
from worker.pipeline import Pipeline
from worker.projects import list_projects
from worker.trending import parse_trending_html


class NextRouterFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'feature.db')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_uid_ranking_and_priority_sort_are_auditable(self):
        self.store.upsert_project({'id': 1, 'full_name': 'a/b', 'html_url': 'https://github.com/a/b',
                                   'description': 'tool'}, 'github_trending')
        self.store.conn.execute('INSERT INTO discovery_runs(id,source,start_date,end_date,config,requested_by,created_at) VALUES(?,?,?,?,?,?,?)',
                                ('run_test_001', 'github_trending', '2026-10-01', '2026-10-01', '{}', 'tester', '2026-10-02'))
        self.store.conn.execute('INSERT INTO project_rankings(project_id,run_id,board,period,rank,weight,source_url,evidence,captured_at) VALUES(?,?,?,?,?,?,?,?,?)',
                                (1, 'run_test_001', 'github_trending', '2026-10-01', 1, 15, 'https://github.com/trending', '{}', '2026-10-02'))
        self.store.conn.execute('INSERT INTO contact_summaries(project_id,snapshot_id,class,source_mask,point_count,priority,rationale,created_at) VALUES(?,?,?,?,?,?,?,?)',
                                (1, None, 'with_clue', 'readme', 1, 100, 'explicit', '2026-10-02'))
        project = self.store.one('SELECT uid FROM projects WHERE id=1')
        self.assertRegex(project['uid'], r'^prj_[a-f0-9]{32}$')
        result = list_projects(self.store, {'sort': ['priority'], 'page': ['1'], 'page_size': ['10']})
        self.assertEqual(result['items'][0]['uid'], project['uid'])
        self.assertEqual(result['items'][0]['ranking_rank'], 1)
        self.assertEqual(result['items'][0]['contact_class'], 'with_clue')
        self.assertEqual(result['items'][0]['priority_score'], 115)

    def test_contact_point_records_source_kind_and_explicit_flag(self):
        self.store.upsert_project({'id': 1, 'full_name': 'a/b', 'html_url': 'https://github.com/a/b'}, 'x')
        put_point(self.store, 1, None, 'email', 'author@example.com', 'issues',
                  'https://github.com/a/b/issues/1', 'project_issue', {'issue': 1}, 'issues',
                  'https://github.com/a/b/issues/1')
        row = self.store.one('SELECT source_kind,source_ref,explicit FROM contact_points')
        self.assertEqual((row['source_kind'], row['source_ref'], row['explicit']),
                         ('issues', 'https://github.com/a/b/issues/1', 1))

    def test_ai_assessment_is_snapshot_bound_and_idempotent(self):
        repo = {'id': 1, 'full_name': 'a/b', 'html_url': 'https://github.com/a/b',
                'description': 'MCP agent tool', 'topics': ['mcp', 'agents']}
        self.store.upsert_project(repo, 'x')
        evidence = {'repository': repo, 'readme_text': 'Install and run tests',
                    'signals': {'test_paths_sample': ['tests/test_x.py'], 'ci_paths': ['.github/workflows/ci.yml']},
                    'code_sample_paths': ['src/main.py'], 'commits_90d_sample': [{}, {}],
                    'issues_pr_sample': []}
        self.store.conn.execute('INSERT INTO snapshots(project_id,sha,fetched_at,evidence,complete) VALUES(?,?,?,?,1)',
                                (1, 'a' * 40, '2026-10-02', json.dumps(evidence)))
        first = assess_project_snapshot(self.store, 1, 1)
        second = assess_project_snapshot(self.store, 1, 1)
        self.assertFalse(first['duplicate'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM ai_assessments')['n'], 1)

    def test_trending_parser_rejects_empty_layout_and_preserves_rank(self):
        html = '<article class="Box-row"><h2><a href="/owner/repo">owner/repo</a></h2></article>'
        rows = parse_trending_html(html)
        self.assertEqual(rows, [{'full_name': 'owner/repo', 'rank': 1}])
        with self.assertRaises(ValueError):
            parse_trending_html('<html>layout changed</html>')

    def test_schedule_creates_a_deterministic_discovery_run(self):
        pipeline = Pipeline(self.store, None)
        config = {'queries': [{'query': 'topic:rag', 'category': 'rag'}]}
        first = pipeline.schedule(config, '2026-10-01', '2026-10-02', source='github_trending', actor='tester')
        run_id = pipeline.last_run_id
        second = pipeline.schedule(config, '2026-10-01', '2026-10-02', source='github_trending', actor='tester')
        self.assertEqual(first, second)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM discovery_runs')['n'], 1)
        self.assertEqual(self.store.one('SELECT source FROM discovery_runs WHERE id=?', (run_id,))['source'], 'github_trending')
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='trending'")['n'], 1)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='search'")['n'], 0)

    def test_referral_token_is_single_use_per_project_and_never_listed(self):
        self.store.upsert_project({'id': 1, 'full_name': 'a/b', 'html_url': 'https://github.com/a/b'}, 'x')
        link = create_referral_link(self.store, 'operator', 1)
        first = submit_referral(self.store, link['token'], 1, 'author', {'reason': 'community'})
        second = submit_referral(self.store, link['token'], 1, 'author', {'reason': 'duplicate'})
        self.assertFalse(first['duplicate'])
        self.assertTrue(second['duplicate'])
        stored = self.store.one('SELECT token_hash FROM referral_links')
        self.assertIsNotNone(stored)
        self.assertNotEqual(stored['token_hash'], link['token'])
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM referrals')['n'], 1)

    def test_bounded_workers_process_refresh_jobs_in_parallel(self):
        active = [0]
        max_active = [0]
        lock = threading.Lock()

        class FakeGitHub:
            @staticmethod
            def next_link(headers):
                return None

            def get(self, path, params=None):
                with lock:
                    active[0] += 1
                    max_active[0] = max(max_active[0], active[0])
                try:
                    time.sleep(0.02)
                    if path.startswith('/repos/') and path.count('/') == 3:
                        name = path.removeprefix('/repos/')
                        owner = name.split('/')[0]
                        return {'id': 1 if name.endswith('/one') else 2, 'full_name': name,
                                'html_url': 'https://github.com/' + name, 'default_branch': 'main',
                                'private': False, 'owner': {'id': 10 if owner == 'a' else 20,
                                'login': owner, 'type': 'User'}}, {}
                    if path.endswith('/commits/main'):
                        return {'sha': ('a' if path.startswith('/repos/a/') else 'b') * 40}, {}
                    if '/git/trees/' in path:
                        return {'tree': [], 'truncated': False}, {}
                    if path.endswith('/readme'):
                        return {'encoding': 'base64', 'content': 'aGVsbG8='}, {}
                    if path.startswith('/users/'):
                        login = path.removeprefix('/users/')
                        return {'id': 10 if login == 'a' else 20, 'login': login, 'type': 'User'}, {}
                    return [], {}
                finally:
                    with lock:
                        active[0] -= 1

        self.store.enqueue('refresh', {'repo': 'a/one'}, 'parallel-one')
        self.store.enqueue('refresh', {'repo': 'b/two'}, 'parallel-two')
        result = Pipeline(self.store, FakeGitHub()).work(2, workers=2)
        self.assertEqual([item['status'] for item in result], ['done', 'done'])
        self.assertGreaterEqual(max_active[0], 2)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM snapshots')['n'], 2)


if __name__ == '__main__':
    unittest.main()
