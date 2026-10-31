import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from worker.db import Store, dump, now
from worker.demo import SHA, seed, ready, review_one, approve, FakeCredits
from worker.report import summary
from worker.runner import build_task, pending_task
from worker.workflow import Workflow


class WorkflowV04Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.db')
        seed(self.store)
        self.w = Workflow(self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    @staticmethod
    def future(days):
        return (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=days)).isoformat()

    def funded(self):
        ready(self.w)
        grant, config = approve(self.w)
        provider = FakeCredits(self.store, config)
        provider.submit(grant, 'operator', True)
        provider.activate()
        provider.reconcile(grant, 'operator')
        return grant

    def followup(self, identifier, outcome='continue', **extra):
        return self.w.apply('followup', {'id': identifier, 'result': {
            'usage': 'synthetic usage', 'milestone': 'synthetic milestone', 'evidence': ['fixture:review'],
            'decision': outcome, 'next_action': 'agreed next step', **extra}}, 'operator')

    def experience(self, audience='agent'):
        return self.w.apply('experience-create', {'project_id': 101, 'audience': audience, 'spec': {
            'scenario': 'fixture task', 'success_criteria': 'fixture result', 'environment': 'offline',
            'input': 'fixture', 'budget': {'seconds': 30}}}, 'reviewer')['id']

    def test_closed_historical_grant_does_not_hide_new_approval(self):
        self.funded()
        first = self.store.one('SELECT id FROM followups ORDER BY due_at LIMIT 1')
        self.followup(first['id'], 'end')
        previous = self.store.one('SELECT * FROM decisions')
        next_decision = self.w.apply('decision', {'project_id': 101, 'interview_id': previous['interview_id'],
            'outcome': 'approve', 'terms': json.loads(previous['terms']), 'rationale': 'new sponsored period'}, 'new-approver')
        self.assertEqual(self.w.project(101)['stage'], 'approved')
        self.assertEqual(self.store.one('SELECT status FROM grants WHERE id=?', (next_decision['grant_id'],))['status'], 'pending')

    def test_new_reviews_preserve_contact_interview_and_approval_stages(self):
        ready(self.w)
        contact = self.w.apply('contact-draft', {'project_id': 101, 'maintainer_id': 201, 'channel': 'fixture', 'draft': 'offline'}, 'operator')
        self.w.apply('contact-status', {'id': contact['id'], 'status': 'sent', 'evidence': 'fixture'}, 'operator')
        review_one(self.w)
        self.assertEqual(self.w.project(101)['stage'], 'contacted')
        ready(self.w)
        self.w.apply('contact-status', {'id': contact['id'], 'status': 'replied', 'evidence': 'fixture'}, 'operator')
        interview = self.w.apply('interview', {'project_id': 101, 'contact_id': contact['id'], 'profile': ['engineer'],
            'notes': {'users': 'fixture', 'commitment': 'fixture', 'roadmap': 'fixture', 'credit_need': 'fixture',
                      'milestone': 'fixture', 'consent': True}}, 'operator')
        review_one(self.w)
        self.assertEqual(self.w.project(101)['stage'], 'interviewed')

    def test_pending_approval_remains_approved_after_new_review(self):
        ready(self.w)
        approve(self.w)
        review_one(self.w)
        self.assertEqual(self.w.project(101)['stage'], 'approved')
        self.assertEqual(summary(self.store)['ready_for_contact'], [])

    def test_author_decline_does_not_leave_cancelled_grant_in_approved_stage(self):
        ready(self.w)
        approve(self.w)
        contact = self.store.one('SELECT id FROM contacts')
        self.w.apply('contact-status', {'id': contact['id'], 'status': 'declined', 'evidence': 'author opted out'}, 'operator')
        self.assertEqual(self.w.project(101)['stage'], 'hold')
        self.assertEqual(self.store.one('SELECT status FROM grants')['status'], 'cancelled')

    def test_inbound_reply_after_commit_change_is_recorded_but_interview_blocked(self):
        ready(self.w)
        contact = self.w.apply('contact-draft', {'project_id': 101, 'maintainer_id': 201, 'channel': 'fixture', 'draft': 'offline'}, 'operator')
        self.w.apply('contact-status', {'id': contact['id'], 'status': 'sent', 'evidence': 'fixture'}, 'operator')
        self.store.conn.execute('UPDATE projects SET head_sha=?', ('b' * 40,))
        self.w.apply('contact-status', {'id': contact['id'], 'status': 'replied', 'evidence': 'actual incoming reply'}, 'operator')
        self.assertEqual(self.store.one('SELECT status FROM contacts')['status'], 'replied')
        self.assertEqual(self.w.project(101)['stage'], 'contacted')
        with self.assertRaisesRegex(ValueError, 'Both current'):
            self.w.apply('interview', {'project_id': 101, 'contact_id': contact['id'], 'notes': {'consent': True}, 'profile': ['engineer']}, 'operator')

    def test_inbound_reply_after_expiration_does_not_enable_new_outbound(self):
        ready(self.w)
        contact = self.w.apply('contact-draft', {'project_id': 101, 'maintainer_id': 201, 'channel': 'fixture', 'draft': 'offline'}, 'operator')
        self.w.apply('contact-status', {'id': contact['id'], 'status': 'sent', 'evidence': 'fixture'}, 'operator')
        self.store.conn.execute('UPDATE reviews SET created_at=?', ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=31)).isoformat(),))
        self.w.apply('contact-status', {'id': contact['id'], 'status': 'replied', 'evidence': 'incoming'}, 'operator')
        with self.assertRaisesRegex(ValueError, 'Both current'): self.w.contact_gate(101)

    def test_current_incomplete_snapshot_blocks_all_forward_evaluation_paths(self):
        ready(self.w)
        self.store.conn.execute('INSERT INTO snapshots(project_id,sha,fetched_at,evidence,complete) VALUES(?,?,?,?,0)', (101, SHA, now(), dump({'errors': ['missing evidence']})))
        with self.assertRaisesRegex(ValueError, 'snapshot'): self.w.contact_gate(101)
        with self.assertRaisesRegex(ValueError, 'snapshot'): review_one(self.w)
        for audience in ('human', 'agent'):
            with self.assertRaisesRegex(ValueError, 'snapshot'): self.experience(audience)
        report = summary(self.store)
        self.assertEqual(report['ready_for_contact'], [])
        self.assertIn('snapshot', report['blocked_contacts'][0]['reason'])

    def test_runner_and_report_use_the_same_snapshot_time_gate(self):
        review_one(self.w)
        eid = self.experience()
        for timestamp in ('2099-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00', '2026-01-01T00:00:00'):
            self.store.conn.execute('UPDATE snapshots SET fetched_at=?', (timestamp,))
            with self.assertRaisesRegex(ValueError, 'snapshot'): pending_task(self.store, eid)
            report = summary(self.store)
            self.assertEqual(report['pending_experiences'], [])
            self.assertIn('snapshot', report['stale_experiences'][0]['reason'])

    def test_ready_queue_requires_an_available_confirmed_contactable_author(self):
        ready(self.w)
        for sql in ('UPDATE project_maintainers SET confirmed=0', 'UPDATE project_maintainers SET confirmed=1', 'UPDATE maintainers SET do_not_contact=1'):
            self.store.conn.execute(sql)
            report = summary(self.store)
            if 'confirmed=1' in sql:
                self.assertEqual(len(report['ready_for_contact']), 1)
            else:
                self.assertEqual(report['ready_for_contact'], [])
                self.assertIn('contactable', report['blocked_contacts'][0]['reason'])

    def test_author_active_on_another_project_is_blocked_without_duplicate_own_task(self):
        ready(self.w)
        seed(self.store, project_id=102, maintainer_id=201)
        ready(self.w, project_id=102)
        self.w.apply('contact-draft', {'project_id': 101, 'maintainer_id': 201, 'channel': 'fixture', 'draft': 'offline'}, 'operator')
        report = summary(self.store)
        self.assertEqual(report['ready_for_contact'], [])
        self.assertEqual([p['project_id'] for p in report['blocked_contacts']], [102])

    def test_active_agent_lease_is_running_expired_lease_becomes_pending(self):
        review_one(self.w)
        eid = self.experience()
        task = build_task(self.store, eid)
        task['review_id'] = self.w.latest_review(101, 1)['id']
        self.store.conn.execute('INSERT INTO agent_leases(experience_id,actor,token_hash,lease_until,state,task,task_sha256) VALUES(?,?,?,?,?,?,?)',
            (eid, 'agent-one', hashlib.sha256(b'fixture-token').hexdigest(), 9999999999, 'running', dump(task), hashlib.sha256(dump(task).encode()).hexdigest()))
        report = summary(self.store)
        self.assertEqual(report['pending_experiences'], [])
        self.assertEqual(report['running_experiences'][0]['actor'], 'agent-one')
        self.assertEqual(report['running_experiences'][0]['run_id'], task['run_id'])
        self.store.conn.execute('UPDATE agent_leases SET lease_until=0')
        report = summary(self.store)
        self.assertEqual(report['running_experiences'], [])
        self.assertEqual(report['pending_experiences'][0]['id'], eid)

    def test_final_followup_requires_next_date_and_rolls_back_when_missing(self):
        self.funded()
        rows = self.store.all('SELECT * FROM followups ORDER BY due_at')
        for row in rows[:-1]:
            result = self.followup(row['id'])
            self.assertIsNotNone(result['next_followup_id'])
        for outcome in ('continue', 'adjust', 'pause'):
            with self.subTest(outcome=outcome), self.assertRaisesRegex(ValueError, 'next_review_at'):
                self.followup(rows[-1]['id'], outcome)
            self.assertEqual(self.store.one('SELECT status FROM followups WHERE id=?', (rows[-1]['id'],))['status'], 'pending')

    def test_explicit_followup_date_creates_unique_structured_next_task(self):
        self.funded()
        rows = self.store.all('SELECT * FROM followups ORDER BY due_at')
        next_date = self.future(75)
        first = self.followup(rows[0]['id'], 'pause', next_review_at=next_date)
        second = self.followup(rows[1]['id'], 'adjust', next_review_at=next_date)
        self.assertEqual(first['next_followup_id'], second['next_followup_id'])
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM followups WHERE due_at=?', (next_date,))['n'], 1)
        recorded = json.loads(self.store.one('SELECT result FROM followups WHERE id=?', (rows[0]['id'],))['result'])
        self.assertEqual(recorded['next_review_at'], next_date)
        self.assertEqual(recorded['next_followup_id'], first['next_followup_id'])

    def test_bad_followup_date_rolls_back_and_end_cancels_all_remaining(self):
        self.funded()
        row = self.store.one('SELECT * FROM followups ORDER BY due_at LIMIT 1')
        for value in ('', '2000-01-01', 20990101, True, self.future(0), row['due_at']):
            with self.subTest(value=value), self.assertRaises(ValueError): self.followup(row['id'], next_review_at=value)
        ended = self.followup(row['id'], 'end')
        self.assertIsNone(ended['next_review_at'])
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM followups WHERE status='pending'")['n'], 0)
        self.assertEqual(self.w.project(101)['stage'], 'closed')


if __name__ == '__main__': unittest.main()
