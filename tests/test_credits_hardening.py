import json
from pathlib import Path
import tempfile
import unittest

from worker.credits import CreditsError
from worker.db import Store, dump
from worker.demo import seed, ready, approve, FakeCredits
from worker.workflow import Workflow


class CreditsHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 'test.db')
        self.w = Workflow(self.s)
        seed(self.s)
        ready(self.w)
        self.gid, self.config = approve(self.w)
        self.provider = FakeCredits(self.s, self.config)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def test_approver_cannot_execute_own_approval(self):
        with self.assertRaisesRegex(ValueError, 'different accountable actors'):
            self.provider.submit(self.gid, 'demo-approver', True)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.s.one('SELECT status FROM grants')['status'], 'pending')

    def test_reconcile_requires_accountable_actor(self):
        for actor in ('', ' ', None):
            with self.assertRaises(ValueError):
                self.provider.reconcile(self.gid, actor)
        self.assertEqual(self.provider.calls, [])

    def test_submit_completion_does_not_overwrite_concurrent_issuance(self):
        original = self.provider.request
        def racing(method, path, body=None):
            result = original(method, path, body)
            if method == 'POST':
                self.provider.activate()
                self.provider.reconcile(self.gid, 'auditor')
            return result
        self.provider.request = racing
        with self.assertRaisesRegex(CreditsError, 'state changed'):
            self.provider.submit(self.gid, 'operator', True)
        self.assertEqual(self.s.one('SELECT status FROM grants')['status'], 'issued')
        self.assertEqual(len(self.s.all('SELECT * FROM followups')), 3)
        self.assertEqual(sum(c['method'] == 'POST' for c in self.provider.calls), 1)

    def test_timeout_does_not_overwrite_concurrent_issuance(self):
        original = self.provider.request
        def racing(method, path, body=None):
            result = original(method, path, body)
            if method == 'POST':
                self.provider.activate()
                self.provider.reconcile(self.gid, 'auditor')
                raise CreditsError('lost response')
            return result
        self.provider.request = racing
        with self.assertRaisesRegex(CreditsError, 'lost response'):
            self.provider.submit(self.gid, 'operator', True)
        self.assertEqual(self.s.one('SELECT status FROM grants')['status'], 'issued')

    def test_parallel_second_submit_makes_no_duplicate_write(self):
        original = self.provider.request
        def racing(method, path, body=None):
            if method == 'POST':
                with self.assertRaisesRegex(ValueError, 'Only pending'):
                    self.provider.submit(self.gid, 'other-operator', True)
            return original(method, path, body)
        self.provider.request = racing
        self.provider.submit(self.gid, 'operator', True)
        self.assertEqual(sum(c['method'] == 'POST' for c in self.provider.calls), 1)

    def test_event_cannot_change_after_binding_even_if_terms_changed(self):
        self.provider.submit(self.gid, 'operator', True)
        decision = self.s.one('SELECT * FROM decisions')
        terms = json.loads(decision['terms'])
        terms['event_slug'] = 'another-event'
        self.s.conn.execute('UPDATE decisions SET terms=?', (dump(terms),))
        changed = FakeCredits(self.s, dict(self.config, event_slug='another-event'))
        with self.assertRaisesRegex(ValueError, 'event changed'):
            changed.reconcile(self.gid, 'auditor')
        self.assertEqual(changed.calls, [])

    def test_provider_cannot_replace_invitation_after_binding(self):
        self.provider.submit(self.gid, 'operator', True)
        self.provider.rows[0]['id'] = 'replacement-invitation'
        with self.assertRaisesRegex(CreditsError, 'identity changed'):
            self.provider.reconcile(self.gid, 'auditor')

    def test_finalized_participant_identity_is_immutable(self):
        self.provider.submit(self.gid, 'operator', True)
        self.provider.activate()
        self.provider.reconcile(self.gid, 'auditor')
        original_receipt = self.s.one('SELECT receipt FROM grants')['receipt']
        self.provider.rows[0]['participant']['id'] = 'replacement-participant'
        self.provider.people[0]['id'] = 'replacement-participant'
        with self.assertRaisesRegex(CreditsError, 'identity changed'):
            self.provider.reconcile(self.gid, 'auditor')
        self.assertEqual(self.s.one('SELECT receipt FROM grants')['receipt'], original_receipt)

    def test_issued_receipt_preserves_event_binding(self):
        self.provider.submit(self.gid, 'operator', True)
        self.provider.activate()
        self.provider.reconcile(self.gid, 'auditor')
        self.assertEqual(json.loads(self.s.one('SELECT receipt FROM grants')['receipt'])['event_slug'], self.config['event_slug'])

    def test_mixed_case_approved_email_reconciles(self):
        decision = self.s.one('SELECT * FROM decisions')
        terms = json.loads(decision['terms'])
        terms['account'] = ' Demo@Example.Invalid '
        self.s.conn.execute('UPDATE decisions SET terms=?', (dump(terms),))
        self.provider.submit(self.gid, 'operator', True)
        self.provider.activate()
        self.assertEqual(self.provider.reconcile(self.gid, 'auditor')['status'], 'issued')

    def test_concurrent_reconciliations_are_idempotent(self):
        self.provider.submit(self.gid, 'operator', True)
        self.provider.activate()
        original = self.provider.list_items
        interleave = True
        def racing(slug, resource, email):
            nonlocal interleave
            rows = original(slug, resource, email)
            if resource == 'participants' and interleave:
                interleave = False
                self.provider.reconcile(self.gid, 'other-auditor')
            return rows
        self.provider.list_items = racing
        self.assertEqual(self.provider.reconcile(self.gid, 'auditor')['status'], 'issued')
        self.assertEqual(len(self.s.all('SELECT * FROM followups')), 3)


if __name__ == '__main__':
    unittest.main()
