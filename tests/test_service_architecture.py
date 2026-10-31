import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from worker.db import Store, dump, now
from worker.demo import SHA, seed, review_one, ready, approve
from worker.service import ApiFailure, Application
from worker.workflow import Workflow


class ServiceArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.db'
        self.s = Store(self.path)
        self.w = Workflow(self.s)
        seed(self.s)
        self.tokens = {'reviewer': 'r' * 40, 'agent': 'a' * 40, 'demo-approver': 'p' * 40}
        self.config = {'users': [{'actor': actor, 'token_sha256': hashlib.sha256(token.encode()).hexdigest(),
                                  'roles': ['approver', 'disburser'] if actor == 'demo-approver' else [actor]}
                                 for actor, token in self.tokens.items()], 'allow_credit_writes': True}
        self.app = Application(self.path, self.config)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def post(self, path, body, key, actor='reviewer', app=None):
        return (app or self.app).handle('POST', path, 'Bearer ' + self.tokens[actor], body, key)[1]

    def new_experience(self):
        return self.w.apply('experience-create', {'project_id': 101, 'audience': 'agent', 'spec': {
            'scenario': 'offline retrieval', 'success_criteria': 'answer equals fixture',
            'environment': 'offline image', 'input': 'fixture', 'budget': {'seconds': 30}}}, 'reviewer')['id']

    def claim(self, key='claim-task-0001'):
        return self.post('/api/agent/claim', {}, key, actor='agent')

    def test_old_invalid_tasks_do_not_starve_valid_agent_work(self):
        review_one(self.w)
        for number in range(100):
            self.s.conn.execute('INSERT INTO experiences(id,project_id,sha,audience,spec,created_at) VALUES(?,?,?,?,?,?)',
                                ('stale-' + str(number), 101, 'b' * 40, 'agent', '{}', now()))
        fresh = self.new_experience()
        claimed = self.claim()
        self.assertIsNotNone(claimed['task'], 'Stale candidates must not hide later valid work')
        self.assertEqual(claimed['task']['id'], fresh)

    def test_archival_after_claim_invalidates_heartbeat(self):
        review_one(self.w)
        eid = self.new_experience()
        claimed = self.claim()
        metadata = json.loads(self.w.project(101)['metadata'])
        metadata['archived'] = True
        self.s.conn.execute('UPDATE projects SET metadata=? WHERE id=101', (dump(metadata),))
        with self.assertRaises((ValueError, ApiFailure)):
            self.post('/api/agent/tasks/' + eid + '/heartbeat', {'lease_token': claimed['lease_token']}, 'heartbeat-0001', actor='agent')

    def test_actor_override_and_agent_review_privilege_are_rejected(self):
        with self.assertRaises(ApiFailure) as caught:
            self.post('/api/actions/assign', {'actor': 'admin'}, 'override-0001')
        self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(ApiFailure) as caught:
            self.post('/api/actions/review', {}, 'review-0001', actor='agent')
        self.assertEqual(caught.exception.status, 403)

    def test_replay_rechecks_current_permissions(self):
        body = {'project_id': 101, 'audience': 'both', 'owner': 'new-owner'}
        self.post('/api/actions/assign', body, 'assign-0001')
        changed = json.loads(json.dumps(self.config))
        changed['users'][0]['roles'] = ['auditor']
        restricted = Application(self.path, changed)
        with self.assertRaises(ApiFailure) as caught:
            self.post('/api/actions/assign', body, 'assign-0001', app=restricted)
        self.assertEqual(caught.exception.status, 403)

    def test_audit_failure_rolls_back_business_mutation_and_idempotency(self):
        owner = self.w.project(101)['owner']
        with mock.patch.object(Store, 'event', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'audit unavailable'):
                self.post('/api/actions/assign', {'project_id': 101, 'audience': 'both', 'owner': 'changed'}, 'assign-0001')
        self.assertEqual(self.w.project(101)['owner'], owner)
        self.assertEqual(self.s.all('SELECT * FROM api_requests'), [])

    def test_same_key_replays_exact_result_without_duplicate_event(self):
        initial_events = len(self.s.all('SELECT * FROM events'))
        body = {'project_id': 101, 'audience': 'both', 'owner': 'changed'}
        first = self.post('/api/actions/assign', body, 'assign-0001')
        second = self.post('/api/actions/assign', body, 'assign-0001')
        self.assertEqual(first, second)
        self.assertEqual(len(self.s.all('SELECT * FROM events')), initial_events + 1)
        with self.assertRaises(ApiFailure) as caught:
            self.post('/api/actions/assign', dict(body, owner='different'), 'assign-0001')
        self.assertEqual(caught.exception.status, 409)

    def test_dual_role_approver_cannot_execute_own_grant(self):
        ready(self.w)
        gid, credits_config = approve(self.w)
        app = Application(self.path, self.config, credits_config)
        with mock.patch('worker.service.NextCredits') as provider:
            with self.assertRaises(ApiFailure) as caught:
                self.post('/api/credits/' + gid + '/submit', {'execute': True}, 'submit-0001', actor='demo-approver', app=app)
            self.assertEqual(caught.exception.status, 403)
            provider.assert_not_called()
        self.assertEqual(self.s.one('SELECT status FROM grants')['status'], 'pending')
        self.assertEqual(self.s.all('SELECT * FROM api_requests'), [])

    def test_nested_transaction_failure_preserves_outer_work(self):
        with self.s.transaction():
            self.s.conn.execute("UPDATE projects SET owner='outer' WHERE id=101")
            with self.assertRaisesRegex(RuntimeError, 'inner'):
                with self.s.transaction():
                    self.s.conn.execute("UPDATE projects SET owner='inner' WHERE id=101")
                    raise RuntimeError('inner')
            self.assertEqual(self.w.project(101)['owner'], 'outer')
        self.assertEqual(self.w.project(101)['owner'], 'outer')


if __name__ == '__main__':
    unittest.main()
