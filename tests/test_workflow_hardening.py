import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from worker.db import Store, dump
from worker.demo import SHA, seed, ready, review_one, approve, FakeCredits
from worker.workflow import Workflow


class WorkflowHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 'test.db')
        self.w = Workflow(self.s)
        seed(self.s)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def approved(self):
        ready(self.w)
        gid, config = approve(self.w)
        return gid, config, self.s.one('SELECT * FROM decisions')

    def test_incomplete_snapshot_cannot_pass(self):
        self.s.conn.execute('UPDATE snapshots SET complete=0')
        with self.assertRaisesRegex(ValueError, 'snapshot'):
            review_one(self.w)
        self.assertEqual(self.s.all('SELECT * FROM reviews'), [])

    def test_blank_or_nontext_evidence_cannot_pass(self):
        for invalid in ('   ', ['  '], True, 7, {'fake': True}):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                self.w.apply('review', {'project_id': 101, 'round': 1, 'sha': SHA,
                    'decision': 'pass', 'rationale': 'reason', 'evidence': {
                    'structure': invalid, 'maintenance': 'commits', 'documentation': 'readme'}}, 'reviewer')
        self.assertEqual(self.s.all('SELECT * FROM reviews'), [])

    def test_future_or_naive_review_timestamp_does_not_pass(self):
        review_one(self.w)
        for timestamp in ('2099-01-01T00:00:00+00:00', '2099-01-01T00:00:00'):
            self.s.conn.execute('UPDATE reviews SET created_at=?', (timestamp,))
            self.assertFalse(self.w.passed(self.w.project(101), 1))

    def test_cross_project_contact_cannot_supply_interview(self):
        ready(self.w)
        seed(self.s, project_id=102)
        ready(self.w, project_id=102)
        _, _ = approve(self.w)
        interview = self.s.one('SELECT * FROM interviews')
        with self.assertRaisesRegex(ValueError, 'reply'):
            self.w.apply('interview', {'project_id': 102, 'contact_id': interview['contact_id'],
                'notes': json.loads(interview['notes']), 'profile': ['engineer']}, 'operator')
        self.assertEqual(len(self.s.all('SELECT * FROM interviews')), 1)

    def test_author_decline_cancels_pending_and_prevents_approval(self):
        gid, _, decision = self.approved()
        contact = self.s.one('SELECT * FROM contacts')
        self.w.apply('contact-status', {'id': contact['id'], 'status': 'declined', 'evidence': 'author opt-out'}, 'operator')
        self.assertEqual(self.s.one('SELECT status FROM grants')['status'], 'cancelled')
        with self.assertRaisesRegex(ValueError, 'consenting'):
            self.w.apply('decision', {'project_id': 101, 'interview_id': decision['interview_id'],
                'outcome': 'approve', 'rationale': 'reconsider', 'terms': json.loads(decision['terms'])}, 'approver')

    def test_withdrawal_still_works_with_stale_technical_evidence(self):
        gid, _, decision = self.approved()
        self.s.conn.execute('UPDATE projects SET head_sha=?', ('b' * 40,))
        self.w.apply('decision', {'project_id': 101, 'interview_id': decision['interview_id'],
            'outcome': 'reject', 'rationale': 'approval revoked'}, 'approver')
        self.assertEqual(self.s.one('SELECT status FROM grants')['status'], 'cancelled')

    def test_unsafe_write_outcomes_cannot_be_marked_retryable_or_cancelled(self):
        gid, _, _ = self.approved()
        for state in ('submitting', 'unknown', 'awaiting_claim'):
            self.s.conn.execute('UPDATE grants SET status=?', (state,))
            for target in ('failed', 'cancelled'):
                with self.subTest(state=state, target=target), self.assertRaisesRegex(ValueError, 'Provider write'):
                    self.w.apply('grant-reconcile', {'id': gid, 'status': target, 'receipt': {'evidence': 'operator assertion'}}, 'operator')
                self.assertEqual(self.s.one('SELECT status FROM grants')['status'], state)

    def test_provider_binding_survives_manual_unknown_note(self):
        gid, config, _ = self.approved()
        FakeCredits(self.s, config).submit(gid, 'operator', True)
        original = json.loads(self.s.one('SELECT receipt FROM grants')['receipt'])
        self.w.apply('grant-reconcile', {'id': gid, 'status': 'unknown', 'receipt': {'evidence': 'investigate'}}, 'operator')
        updated = json.loads(self.s.one('SELECT receipt FROM grants')['receipt'])
        for key in ('provider_base', 'event_slug', 'invitation_id'):
            self.assertEqual(updated[key], original[key])

    def test_invalid_terms_rollback_without_new_decision(self):
        gid, _, decision = self.approved()
        self.s.conn.execute("UPDATE grants SET status='cancelled'")
        original = json.loads(decision['terms'])
        cases = [('amount', True), ('amount', 'NaN'), ('amount', 'Infinity'),
                 ('amount', '1.5'), ('amount', 2147483648), ('review_dates', ['2099-01-01'] * 2),
                 ('expires_at', 20990101), ('review_dates', [True])]
        for key, value in cases:
            terms = dict(original, **{key: value})
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.w.apply('decision', {'project_id': 101, 'interview_id': decision['interview_id'],
                    'outcome': 'approve', 'rationale': 'new award', 'terms': terms}, 'approver')
        self.assertEqual(len(self.s.all('SELECT * FROM decisions')), 1)

    def test_new_review_cannot_erase_sponsorship_stage(self):
        gid, config, _ = self.approved()
        provider = FakeCredits(self.s, config)
        provider.submit(gid, 'operator', True)
        provider.activate()
        provider.reconcile(gid, 'operator')
        review_one(self.w)
        self.assertEqual(self.w.project(101)['stage'], 'sponsored')
        self.assertEqual(self.s.one('SELECT status FROM grants')['status'], 'issued')

    def test_environment_failure_can_complete_without_artifact(self):
        review_one(self.w)
        e = self.w.apply('experience-create', {'project_id': 101, 'audience': 'agent', 'spec': {
            'scenario': 'install dependency', 'success_criteria': 'dependency imports',
            'environment': 'offline image', 'input': 'fixture', 'budget': {'seconds': 30}}}, 'reviewer')
        result = {'success': False, 'executor': 'agent', 'environment': 'offline image',
                  'steps': ['checked source'], 'actual_result': 'source missing',
                  'artifacts': [], 'failure_kind': 'environment'}
        self.w.apply('experience-result', {'id': e['id'], 'sha': SHA, 'result': result}, 'agent')
        self.assertEqual(self.s.one('SELECT status FROM experiences WHERE id=?', (e['id'],))['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
