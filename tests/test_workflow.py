import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from worker.db import Store
from worker.workflow import Workflow
from worker.demo import SHA, seed, ready, review_one, experience, approve, FakeCredits
from worker.credits import CreditsError


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 'test.db')
        self.w = Workflow(self.s)
        seed(self.s)

    def tearDown(self):
        self.s.close(); self.tmp.cleanup()

    def test_contact_requires_both_rounds(self):
        with self.assertRaises(ValueError): self.w.contact_gate(101)
        review_one(self.w)
        with self.assertRaises(ValueError): self.w.contact_gate(101)
        self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM contacts')['n'], 0)

    def test_mixed_product_needs_both_audiences(self):
        with self.assertRaisesRegex(ValueError, 'Missing experience'):
            ready(self.w, ('human',))
        self.assertIsNone(self.w.latest_review(101, 2))

    def test_commit_change_invalidates_contact(self):
        ready(self.w)
        self.s.conn.execute("UPDATE projects SET head_sha=?", ('b' * 40,))
        with self.assertRaises(ValueError): self.w.contact_gate(101)

    def test_expired_review_invalidates_contact(self):
        ready(self.w)
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=31)).isoformat()
        self.s.conn.execute('UPDATE reviews SET created_at=?', (old,))
        with self.assertRaises(ValueError): self.w.contact_gate(101)

    def test_rereview_invalidates_round_two(self):
        ready(self.w); review_one(self.w)
        with self.assertRaises(ValueError): self.w.contact_gate(101)

    def test_results_are_immutable(self):
        review_one(self.w)
        eid = experience(self.w, 'agent')
        result = json.loads(self.s.one('SELECT result FROM experiences WHERE id=?', (eid,))['result'])
        self.w.apply('experience-result', {'id': eid, 'sha': SHA, 'result': result}, 'tester')
        result['success'] = False
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.w.apply('experience-result', {'id': eid, 'sha': SHA, 'result': result}, 'tester')

    def test_duplicate_maintainer_contact_and_optout(self):
        ready(self.w)
        seed(self.s, project_id=102, maintainer_id=201)
        ready(self.w, project_id=102)
        payload = dict(project_id=101, maintainer_id=201, channel='fixture', draft='do not send')
        c = self.w.apply('contact-draft', payload, 'tester')
        with self.assertRaisesRegex(ValueError, 'active contact'):
            self.w.apply('contact-draft', dict(payload, project_id=102), 'tester')
        self.w.apply('contact-status', dict(id=c['id'], status='declined', evidence='fixture optout'), 'tester')
        with self.assertRaisesRegex(ValueError, 'contactable'):
            self.w.apply('contact-draft', dict(payload, project_id=102), 'tester')

    def test_credits_create_is_not_issuance_and_reconcile_idempotent(self):
        ready(self.w); gid, config = approve(self.w)
        provider = FakeCredits(self.s, config)
        provider.submit(gid, 'tester')
        self.assertEqual(provider.calls, [])
        self.assertEqual(provider.submit(gid, 'tester', True)['status'], 'awaiting_claim')
        self.assertEqual(provider.reconcile(gid, 'tester')['status'], 'awaiting_claim')
        self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM followups')['n'], 0)
        provider.activate()
        provider.reconcile(gid, 'tester'); provider.reconcile(gid, 'tester')
        self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM followups')['n'], 3)
        self.assertEqual(sum(c['method'] == 'POST' for c in provider.calls), 1)

    def test_credits_timeout_is_unknown_and_cannot_resubmit(self):
        ready(self.w); gid, config = approve(self.w)
        provider = FakeCredits(self.s, config)
        original = provider.request
        def fail(method, path, body=None):
            if method == 'POST':
                original(method, path, body)
                raise CreditsError('simulated response loss after commit')
            return original(method, path, body)
        provider.request = fail
        with self.assertRaises(CreditsError): provider.submit(gid, 'tester', True)
        self.assertEqual(self.s.one('SELECT status FROM grants')['status'], 'unknown')
        with self.assertRaises(ValueError): provider.submit(gid, 'tester', True)
        provider.request = original
        self.assertEqual(provider.reconcile(gid, 'tester')['status'], 'awaiting_claim')
        self.assertEqual(len(provider.rows), 1)

    def test_credits_unrelated_invitation_never_overwritten(self):
        ready(self.w); gid, config = approve(self.w)
        provider = FakeCredits(self.s, config)
        provider.rows = [{'id': 'existing', 'email': 'demo@example.invalid', 'note': 'unrelated', 'grantCents': 1000}]
        with self.assertRaises(CreditsError): provider.submit(gid, 'tester', True)
        self.assertFalse(any(c['method'] == 'POST' for c in provider.calls))

    def test_mismatched_receipt_rolls_back(self):
        ready(self.w); gid, config = approve(self.w)
        with self.assertRaises(ValueError):
            self.w.apply('grant-reconcile', {'id': gid, 'status': 'issued', 'receipt': {
                'transaction_id': 'fixture', 'account': 'wrong@example.invalid', 'amount': 1000,
                'unit': 'cents', 'evidence': 'fixture'}}, 'tester')
        self.assertEqual(self.s.one('SELECT status FROM grants')['status'], 'pending')
        self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM followups')['n'], 0)

    def test_credits_expiry_must_match_event(self):
        ready(self.w); gid, config = approve(self.w)
        config['event_end_date'] = '2099-01-01'
        with self.assertRaisesRegex(ValueError, 'event-level'): FakeCredits(self.s, config).plan(gid)

    def test_failed_experience_cannot_pass(self):
        review_one(self.w)
        ids = [experience(self.w, 'human'), experience(self.w, 'agent', success=False)]
        with self.assertRaisesRegex(ValueError, 'successful'):
            self.w.apply('review', {'project_id': 101,'round': 2,'sha': SHA,'decision': 'pass','rationale': 'fixture',
                         'evidence': {'real_problem':'fixture','usability':'fixture','differentiation':'fixture','potential':'fixture','experience_ids':ids}}, 'tester')

    def test_credits_provider_cannot_change_after_submit(self):
        ready(self.w); gid, config = approve(self.w)
        provider = FakeCredits(self.s, config); provider.submit(gid, 'tester', True)
        changed = dict(config, base_url='https://another.example.invalid')
        with self.assertRaisesRegex(ValueError, 'provider address changed'):
            FakeCredits(self.s,changed).reconcile(gid,'tester')

    def test_credits_reconcile_works_after_expiry(self):
        ready(self.w); gid, config = approve(self.w)
        provider = FakeCredits(self.s,config); provider.submit(gid,'tester',True); provider.activate()
        d=self.s.one('SELECT * FROM decisions')
        terms=json.loads(d['terms']); terms['expires_at']='2020-01-01'
        self.s.conn.execute('UPDATE decisions SET terms=? WHERE id=?',(json.dumps(terms),d['id']))
        config['event_end_date']='2020-01-01'
        with self.assertRaisesRegex(ValueError,'expired'): provider.plan(gid)
        self.assertEqual(provider.reconcile(gid,'tester')['status'],'issued')

    def test_later_rejection_blocks_old_approval(self):
        ready(self.w); gid, config = approve(self.w)
        d = self.s.one('SELECT * FROM decisions')
        self.w.apply('decision', {'project_id':101,'interview_id':d['interview_id'],
                     'outcome':'reject','rationale':'Decision withdrawn after new business evidence'},'approver')
        provider = FakeCredits(self.s,config)
        with self.assertRaisesRegex(ValueError,'superseded'):
            provider.submit(gid,'tester',True)
        self.assertEqual(provider.calls,[])


if __name__ == '__main__': unittest.main()
