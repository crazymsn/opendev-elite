import datetime as dt
import hashlib
import tempfile
import unittest
from pathlib import Path

from worker.db import Store
from worker.demo import SHA, seed, ready, review_one, approve
from worker.workflow import Workflow
from worker.report import summary, export_report


class ReportHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.s = Store(self.root / 'test.db')
        seed(self.s)
        self.w = Workflow(self.s)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def pending(self, audience='agent'):
        return self.w.apply('experience-create', {'project_id': 101, 'audience': audience,
            'spec': {'scenario': 'Representative task', 'success_criteria': 'Expected answer',
                     'environment': 'offline fixture', 'input': 'query', 'budget': {'seconds': 30}}}, 'reviewer')['id']

    def test_only_current_reviewed_ready_stage_is_contact_candidate(self):
        self.assertEqual(summary(self.s)['ready_for_contact'], [])
        ready(self.w)
        self.assertEqual([p['id'] for p in summary(self.s)['ready_for_contact']], [101])
        c = self.w.apply('contact-draft', {'project_id': 101, 'maintainer_id': 201,
            'channel': 'fixture', 'draft': 'No message sent'}, 'operator')
        self.assertEqual(summary(self.s)['ready_for_contact'], [])
        self.w.apply('contact-status', {'id': c['id'], 'status': 'sent', 'evidence': 'Synthetic record only'}, 'operator')
        self.w.apply('contact-status', {'id': c['id'], 'status': 'closed', 'evidence': 'Synthetic closure'}, 'operator')
        self.assertEqual(self.w.project(101)['stage'], 'hold')
        self.assertEqual(summary(self.s)['ready_for_contact'], [])

    def test_collection_metrics_use_current_complete_snapshot_and_collection_jobs(self):
        self.s.upsert_project({'id': 102, 'full_name': 'synthetic-demo/uncollected',
                               'html_url': 'https://example.invalid/uncollected'}, 'synthetic-demo')
        self.s.enqueue('refresh', {'repo': 'synthetic-demo/uncollected'}, 'refresh-pending')
        self.s.enqueue('contact_extract', {'project_id': 101}, 'contact-done')
        self.s.conn.execute("UPDATE jobs SET state='done' WHERE dedupe_key='contact-done'")
        self.s.enqueue('refresh', {'repo': 'synthetic-demo/project-101'}, 'refresh-done')
        self.s.conn.execute("UPDATE jobs SET state='done' WHERE dedupe_key='refresh-done'")

        report = summary(self.s)
        self.assertEqual(report['workspace_projects'], 2)
        self.assertEqual(report['collected_projects'], 1)
        self.assertEqual(report['collection_pending_projects'], 1)
        self.assertEqual(report['collection_jobs_done'], 1)
        self.assertEqual(report['collection_jobs_queued'], 1)

        self.s.conn.execute('UPDATE projects SET head_sha=? WHERE id=101', ('b' * 40,))
        self.assertEqual(summary(self.s)['collected_projects'], 0)

    def test_provider_backoff_moves_ready_jobs_to_rate_limit_waiting(self):
        self.s.enqueue('search', {'query': 'fixture'}, 'search-rate-limit')
        scope = hashlib.sha256(b'').hexdigest()[:16]
        self.s.conn.execute('''INSERT INTO provider_limits
            (provider,token_fingerprint,remaining,reset_at,next_allowed_at,concurrency,in_flight,updated_at)
            VALUES(?,?,?,?,?,?,?,?)''',
            ('github', scope, 0, 9999999999, 9999999999, 1, 0, '2099-01-01T00:00:00+00:00'))
        report = summary(self.s)
        self.assertEqual(report['queue_ready'], 0)
        self.assertEqual(report['queue_delayed'], 1)
        self.assertTrue(report['github_rate_limit']['backoff_active'])

    def test_expired_reviews_are_not_ready(self):
        ready(self.w)
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=31)).isoformat()
        self.s.conn.execute('UPDATE reviews SET created_at=?', (old,))
        self.assertEqual(summary(self.s)['ready_for_contact'], [])

    def test_new_product_review_does_not_reopen_rejected_decision(self):
        ready(self.w)
        approve(self.w)
        decision = self.s.one('SELECT * FROM decisions ORDER BY created_at DESC LIMIT 1')
        self.w.apply('decision', {'project_id': 101, 'interview_id': decision['interview_id'],
            'outcome': 'reject', 'rationale': 'No sponsorship after interview'}, 'approver')
        contact = self.s.one('SELECT * FROM contacts')
        self.w.apply('contact-status', {'id': contact['id'], 'status': 'closed', 'evidence': 'Cycle closed'}, 'operator')
        # A new product review must preserve the actual sponsorship decision.
        import json
        previous = self.w.latest_review(101, 2)
        self.w.apply('review', {'project_id': 101, 'round': 2, 'sha': SHA, 'decision': 'pass',
            'rationale': 'Usability remains good', 'evidence': json.loads(previous['evidence'])}, 'reviewer')
        self.assertEqual(self.w.project(101)['stage'], 'rejected')
        self.assertEqual(summary(self.s)['ready_for_contact'], [])

    def test_approved_projects_are_not_new_contact_candidates(self):
        ready(self.w)
        approve(self.w)
        self.assertEqual(summary(self.s)['ready_for_contact'], [])

    def test_current_human_and_agent_experiences_stay_actionable(self):
        review_one(self.w)
        ids = {self.pending('human'), self.pending('agent')}
        report = summary(self.s)
        self.assertEqual({e['id'] for e in report['pending_experiences']}, ids)
        self.assertEqual(report['stale_experiences'], [])

    def test_sha_change_moves_experiences_to_stale_with_reason(self):
        review_one(self.w)
        eid = self.pending()
        self.s.conn.execute('UPDATE projects SET head_sha=?', ('b' * 40,))
        report = summary(self.s)
        self.assertEqual(report['pending_experiences'], [])
        self.assertEqual(report['stale_experiences'][0]['id'], eid)
        self.assertIn('SHA changed', report['stale_experiences'][0]['reason'])
        self.assertEqual(self.s.one('SELECT status FROM experiences WHERE id=?', (eid,))['status'], 'pending')

    def test_rereview_makes_previous_pending_human_experience_stale(self):
        review_one(self.w)
        eid = self.pending('human')
        review_one(self.w)
        report = summary(self.s)
        self.assertEqual(report['pending_experiences'], [])
        self.assertEqual(report['stale_experiences'][0]['id'], eid)
        self.assertIn('predates', report['stale_experiences'][0]['reason'])

    def test_expired_or_incomplete_snapshot_is_visible_as_stale(self):
        review_one(self.w)
        self.pending()
        self.s.conn.execute("UPDATE projects SET refreshed_at='2020-01-01T00:00:00+00:00'")
        self.assertIn('expired', summary(self.s)['stale_experiences'][0]['reason'])
        self.s.conn.execute('UPDATE projects SET refreshed_at=?', (dt.datetime.now(dt.timezone.utc).isoformat(),))
        self.s.conn.execute('UPDATE snapshots SET complete=0')
        self.assertIn('snapshot', summary(self.s)['stale_experiences'][0]['reason'])

    def test_export_includes_both_queues_and_preserves_summary_keys(self):
        review_one(self.w)
        self.pending()
        self.s.conn.execute('UPDATE projects SET head_sha=?', ('b' * 40,))
        report = summary(self.s)
        expected = {'generated_at', 'projects', 'jobs', 'coverage', 'ready_for_contact',
                    'pending_experiences', 'grants', 'due_followups', 'dead_jobs', 'notice'}
        self.assertTrue(expected.issubset(report))
        path = Path(export_report(self.s, self.root / 'report.md'))
        text = path.read_text(encoding='utf-8')
        self.assertIn('当前可执行体验', text)
        self.assertIn('需刷新或重建的体验', text)
        self.assertIn('SHA changed', text)


if __name__ == '__main__':
    unittest.main()
