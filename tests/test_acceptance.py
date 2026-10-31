"""Local acceptance regressions. All product/provider data here is synthetic."""
import datetime as dt
import base64
import hashlib
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from worker.acceptance import audit_readiness
from worker.accounts import Accounts
from worker.auth import Principal
from worker.artifacts import ArtifactStore
from worker.db import Store, dump, now
from worker.demo import seed, SHA, FakeCredits
from worker.service import Application, Server


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'acceptance-test.db'
        self.s = Store(self.path)
        self.roles = ('reviewer', 'operator', 'approver', 'disburser', 'agent', 'auditor')
        self.tokens = {role: role + '-' + 'z' * 48 for role in self.roles}
        self.service = {'users': [{'actor': role, 'roles': [role],
            'token_sha256': hashlib.sha256(self.tokens[role].encode()).hexdigest()} for role in self.roles]}
        self.credits = {'base_url': 'https://credits.company.test', 'event_slug': 'developer-2026',
            'event_end_date': (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=90)).isoformat(),
            'event_config_evidence': 'Operator-reviewed deployment record reference 1234'}
        self.agent = {'image': 'approved-agent@sha256:' + 'a' * 64, 'command': ['/app/run'],
            'timeout_seconds': 60, 'memory_mb': 512, 'cpus': 1}

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def audit(self, **kwargs):
        with patch.dict(os.environ, {'NEXT_CREDITS_PAT': 'onc_pat_' + 'k' * 43}):
            return audit_readiness(self.s, kwargs.get('service_config', self.service),
                                  kwargs.get('credits_config', self.credits), kwargs.get('agent_config', self.agent))

    def check(self, report, name):
        return next(check for check in report['checks'] if check['id'] == name)

    def test_empty_database_is_not_production_verified(self):
        result = self.audit()
        self.assertTrue(result['local_configuration_ready'])
        self.assertEqual(result['status'], 'configuration_ready_evidence_missing')
        self.assertFalse(result['production_verified'])
        self.assertFalse(result['external_evidence_recorded'])
        self.assertEqual(self.check(result, 'credits_issuance_evidence')['status'], 'missing')

    def test_audit_is_read_only_and_never_uses_network_or_processes(self):
        before = self.s.conn.total_changes
        self.s.conn.execute('PRAGMA query_only=ON')
        with (patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('No networking')),
              patch('subprocess.Popen', side_effect=AssertionError('No subprocesses'))):
            result = self.audit()
        self.assertEqual(self.s.conn.total_changes, before)
        self.assertFalse(result['production_verified'])

    def test_example_identity_or_missing_role_blocks_configuration(self):
        result = self.audit(service_config={'users': [{'actor': 'operator', 'roles': ['operator'], 'token_sha256': 'REPLACE_WITH_DIGEST'}]})
        self.assertEqual(result['status'], 'blocked')
        only_admin = {'users': [dict(self.service['users'][0], roles=['admin'])]}
        result = self.audit(service_config=only_admin)
        self.assertFalse(self.check(result, 'service_identities')['evidence']['separate_operational_roles'])

    def test_known_default_password_digest_is_not_accepted(self):
        config = json.loads(json.dumps(self.service))
        config['users'][0]['token_sha256'] = hashlib.sha256(b'password').hexdigest()
        self.assertEqual(self.audit(service_config=config)['status'], 'blocked')

    def create_account(self, name, roles):
        return Accounts(self.path).create(self.s, Principal('setup-admin', frozenset({'admin'})),
            {'username':name,'display_name':'Private display '+name,
             'password':'Only for synthetic acceptance tests 2026!','roles':roles})

    def test_password_team_and_agent_token_satisfy_identity_inventory_without_writes(self):
        accounts=[self.create_account('member-'+role,[role]) for role in ('reviewer','operator','approver','disburser')]
        config=dict(self.service,users=[self.service['users'][4]])
        statements=[];self.s.conn.set_trace_callback(statements.append)
        before=self.s.conn.total_changes;self.s.conn.execute('PRAGMA query_only=ON')
        with patch('worker.accounts.verify_password',side_effect=AssertionError('No login during audit')):
            result=self.audit(service_config=config)
        self.assertEqual(self.s.conn.total_changes,before)
        identity=self.check(result,'service_identities')
        self.assertEqual(identity['status'],'pass')
        self.assertEqual(identity['evidence']['enabled_identity_count'],5)
        self.assertEqual(identity['evidence']['enabled_account_identity_count'],4)
        self.assertEqual(identity['evidence']['enabled_token_identity_count'],1)
        self.assertFalse(result['production_verified'])
        raw=json.dumps(result)
        for account in accounts:
            self.assertNotIn(account['id'],raw);self.assertNotIn(account['username'],raw)
            self.assertNotIn(account['display_name'],raw)
        self.assertFalse(any('password_hash' in sql.lower() for sql in statements))

    def test_combined_roles_or_disabled_password_accounts_do_not_fake_separation(self):
        row=self.create_account('combined-member',['reviewer','operator','approver','disburser'])
        config=dict(self.service,users=[self.service['users'][4]])
        self.assertEqual(self.check(self.audit(service_config=config),'service_identities')['status'],'missing_or_invalid')
        self.s.conn.execute('DELETE FROM accounts WHERE id=?',(row['id'],))
        members=[self.create_account('member-'+role,[role]) for role in ('reviewer','operator','approver','disburser')]
        self.s.conn.execute("UPDATE accounts SET status='disabled' WHERE id=?",(members[-1]['id'],))
        identity=self.check(self.audit(service_config=config),'service_identities')
        self.assertEqual(identity['status'],'missing_or_invalid')
        self.assertEqual(identity['evidence']['enabled_account_identity_count'],3)

    def test_invalid_account_roles_and_token_account_collisions_block_identity_inventory(self):
        row=self.create_account('private-member',['reviewer'])
        for invalid in ('not-json','{}','["agent"]','["reviewer", "reviewer"]'):
            self.s.conn.execute('UPDATE accounts SET roles=? WHERE id=?',(invalid,row['id']))
            identity=self.check(self.audit(),'service_identities')
            self.assertEqual(identity['status'],'missing_or_invalid')
            self.assertFalse(identity['evidence']['account_configuration_valid'])
        self.s.conn.execute('UPDATE accounts SET roles=? WHERE id=?',(dump(['reviewer']),row['id']))
        config=json.loads(json.dumps(self.service));config['users'][0]['actor']=row['id']
        identity=self.check(self.audit(service_config=config),'service_identities')
        self.assertEqual(identity['status'],'missing_or_invalid')
        self.assertEqual(identity['evidence']['account_actor_conflicts'],1)

    def test_synthetic_database_cannot_count_as_real_product_evidence(self):
        seed(self.s)
        result = self.audit()
        self.assertEqual(result['status'], 'blocked')
        self.assertGreater(self.check(result, 'synthetic_data_absent')['evidence']['count'], 0)
        self.assertEqual(self.check(result, 'github_collection_evidence')['status'], 'missing')

    def test_matching_provenance_is_recorded_but_never_certified(self):
        # Deliberately constructed data demonstrates the limits of an offline audit.
        repo = {'id': 404, 'full_name': 'acceptance-check/agent-tools',
                'html_url': 'https://github.com/acceptance-check/agent-tools'}
        self.s.upsert_project(repo, 'manual')
        self.s.conn.execute('UPDATE projects SET head_sha=?,refreshed_at=? WHERE id=404', (SHA, now()))
        evidence = {'repository': repo, 'head': {'sha': SHA}, 'errors': [],
                    'sources': {'head': 'https://api.github.com/repos/acceptance-check/agent-tools/commits/main'},
                    'readme_text': 'This real repository could discuss demo and fixture code.'}
        self.s.conn.execute('INSERT INTO snapshots(project_id,sha,fetched_at,evidence,complete) VALUES(?,?,?,?,1)',
                            (404, SHA, now(), dump(evidence)))
        result = self.audit()
        check = self.check(result, 'github_collection_evidence')
        self.assertEqual(check['status'], 'recorded_unverified')
        self.assertFalse(result['production_verified'])
        self.assertEqual(self.check(result, 'synthetic_data_absent')['evidence']['count'], 0)
        self.s.conn.execute('UPDATE snapshots SET sha=?', ('b' * 40,))
        self.assertEqual(self.check(self.audit(), 'github_collection_evidence')['status'], 'missing')

    def test_audit_output_contains_no_credentials_or_receiving_accounts(self):
        seed(self.s)
        raw = json.dumps(self.audit())
        for value in (self.tokens['operator'], self.service['users'][0]['token_sha256'],
                      'onc_pat_' + 'k' * 43, 'demo@example.invalid'):
            self.assertNotIn(value, raw)

    def test_agent_path_strings_require_completed_same_run_artifact_links(self):
        # Constructed records test association logic, never attest execution truth.
        repo = {'id': 404, 'full_name': 'acceptance-check/agent-tools',
                'html_url': 'https://github.com/acceptance-check/agent-tools'}
        self.s.upsert_project(repo, 'manual')
        self.s.conn.execute('UPDATE projects SET head_sha=?,refreshed_at=? WHERE id=404', (SHA, now()))
        evidence = {'repository': repo, 'head': {'sha': SHA}, 'errors': [],
                    'sources': {'head': 'https://api.github.com/repos/acceptance-check/agent-tools/commits/main'}}
        self.s.conn.execute('INSERT INTO snapshots(project_id,sha,fetched_at,evidence,complete) VALUES(?,?,?,?,1)',
                            (404, SHA, now(), dump(evidence)))
        eid, run = 'evidence-check', 'run-binding'
        task = dump({'id': eid, 'sha': SHA, 'run_id': run})
        result = {'success': True, 'executor': 'executor-1', 'environment': 'execution-node',
                  'steps': ['Read input and compare output'], 'actual_result': 'Expected output observed',
                  'artifacts': ['output.txt']}
        self.s.conn.execute('INSERT INTO experiences VALUES(?,?,?,?,?,?,?,?)',
            (eid, 404, SHA, 'agent', dump({'scenario': 'Read and answer question'}), 'completed', dump(result), now()))
        self.s.conn.execute('INSERT INTO agent_leases VALUES(?,?,?,?,?,?,?)',
            (eid, 'executor-1', 'unused', 0, 'completed', task, hashlib.sha256(task.encode()).hexdigest()))
        def status():
            return self.check(self.audit(), 'agent_experience_evidence')['status']
        self.assertEqual(status(), 'missing')
        content = b'Expected output'
        artifacts = ArtifactStore(self.s)
        artifacts.upload(eid, run, 'executor-1', {'run_id': run, 'path': 'output.txt',
            'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content), 'chunk_index': 0,
            'total_chunks': 1, 'data_base64': base64.b64encode(content).decode()})
        self.assertEqual(status(), 'missing')
        artifacts.link_result(eid, run, ['output.txt'])
        self.assertEqual(status(), 'recorded_unverified')
        self.s.conn.execute('UPDATE artifact_uploads SET run_id=?', ('different-run',))
        self.assertEqual(status(), 'missing')

    def test_invalid_provider_and_unpinned_executor_configuration(self):
        self.assertEqual(self.audit(credits_config=dict(self.credits, base_url='https://example.invalid'))['status'], 'blocked')
        self.assertEqual(self.audit(agent_config=dict(self.agent, image='agent:latest'))['status'], 'blocked')

    def test_invalid_browser_or_optional_oidc_config_blocks_local_readiness(self):
        for changed in ({'public_origin': 'https://work.company.test/path'},
                        {'browser_session_seconds': 1}, {'agent_lease_seconds': True},
                        {'allow_credit_writes': 'true'}, {'oidc': {'issuer': 'http://insecure.test'}}):
            with self.subTest(config=changed):
                result = self.audit(service_config=dict(self.service, **changed))
                self.assertEqual(result['status'], 'blocked')
                self.assertFalse(self.check(result, 'service_identities')['evidence']['browser_configuration_valid'])

    def test_local_http_separate_identity_business_chain_and_denials(self):
        """Actual HTTP transport; synthetic product and in-memory Credits only."""
        seed(self.s)
        config = dict(self.service, allow_credit_writes=True)
        provider_config = dict(self.credits, base_url='https://example.invalid', event_slug='synthetic-demo', event_config_evidence='synthetic event')
        state = {'rows': [], 'people': [], 'calls': []}
        def provider(store, settings):
            instance = FakeCredits(store, settings)
            instance.rows, instance.people, instance.calls = state['rows'], state['people'], state['calls']
            return instance
        server = Server(('127.0.0.1', 0), Application(self.path, config, provider_config))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        sequence = 0
        def request(role, path, body=None, status=200, key=None, method='POST'):
            nonlocal sequence
            sequence += 1
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            headers = {'Content-Type': 'application/json', 'Idempotency-Key': key or f'acceptance-http-{sequence:04d}'}
            if role:
                headers['Authorization'] = 'Bearer ' + self.tokens[role]
            try:
                connection.request(method, path, body=dump(body) if body is not None else None, headers=headers)
                response = connection.getresponse()
                value = json.loads(response.read())
                self.assertEqual(response.status, status, value)
                return value
            finally:
                connection.close()
        def action(role, name, body, **kwargs):
            return request(role, '/api/actions/' + name, body, **kwargs)
        try:
            with patch('worker.service.NextCredits', side_effect=provider):
                request(None, '/api/projects', status=401, method='GET')
                action('agent', 'review', {}, status=403)
                action('operator', 'assign', {'actor': 'approver', 'project_id': 101, 'audience': 'both', 'owner': 'reviewer'}, status=400)
                action('operator', 'assign', {'project_id': 101, 'audience': 'both', 'owner': 'reviewer'})
                contact = {'project_id': 101, 'maintainer_id': 201, 'channel': 'synthetic', 'draft': 'Synthetic acceptance only; no outbound message'}
                action('operator', 'contact-draft', contact, status=400)
                action('reviewer', 'review', {'project_id': 101, 'round': 1, 'sha': SHA, 'decision': 'pass',
                    'rationale': 'Synthetic acceptance structure audit', 'evidence': {
                        'structure': 'fixture module analysis', 'maintenance': 'fixture commit analysis', 'documentation': 'fixture docs analysis'}})
                ids = []
                for audience in ('human', 'agent'):
                    e = action('reviewer', 'experience-create', {'project_id': 101, 'audience': audience,
                        'spec': {'scenario': 'Synthetic representative retrieval', 'success_criteria': 'Answer matches fixture',
                                 'environment': 'offline synthetic', 'input': 'fixture query', 'budget': {'seconds': 30}}})
                    ids.append(e['id'])
                    result = {'success': True, 'executor': 'spoofed-identity', 'environment': 'synthetic offline fixture',
                              'steps': ['Synthetic HTTP acceptance task completed'], 'actual_result': 'Synthetic expected output',
                              'artifacts': ['synthetic-output.txt'], 'failure_kind': 'none'}
                    if audience == 'human':
                        action('reviewer', 'experience-result', {'id': e['id'], 'sha': SHA, 'result': result})
                    else:
                        claimed = request('agent', '/api/agent/claim', {})
                        task = claimed['task']
                        payload = dict(id=e['id'], sha=SHA, run_id=task['run_id'], task_sha256=claimed['task_sha256'],
                                       lease_token=claimed['lease_token'], result=result)
                        request('operator', f"/api/agent/tasks/{e['id']}/result", payload, status=403)
                        request('agent', f"/api/agent/tasks/{e['id']}/result", dict(payload, run_id='wrong'), status=400)
                        request('agent', f"/api/agent/tasks/{e['id']}/result", payload, status=400)
                        content = b'Synthetic HTTP acceptance output; no real product evaluation.\n'
                        request('agent', f"/api/agent/tasks/{e['id']}/artifacts", {
                            'lease_token': claimed['lease_token'], 'run_id': task['run_id'],
                            'path': 'synthetic-output.txt', 'sha256': hashlib.sha256(content).hexdigest(),
                            'size': len(content), 'chunk_index': 0, 'total_chunks': 1,
                            'data_base64': base64.b64encode(content).decode()})
                        request('agent', f"/api/agent/tasks/{e['id']}/result", payload)
                        stored = json.loads(self.s.one('SELECT result FROM experiences WHERE id=?', (e['id'],))['result'])
                        self.assertEqual(stored['executor'], 'agent')
                action('operator', 'contact-draft', contact, status=400)
                action('reviewer', 'review', {'project_id': 101, 'round': 2, 'sha': SHA, 'decision': 'pass',
                    'rationale': 'Synthetic product acceptance', 'evidence': {'real_problem': 'synthetic workflow need',
                    'usability': 'synthetic task success', 'differentiation': 'synthetic comparison', 'potential': 'synthetic roadmap', 'experience_ids': ids}})
                c = action('operator', 'contact-draft', contact)
                for status in ('sent', 'replied'):
                    action('operator', 'contact-status', {'id': c['id'], 'status': status, 'evidence': 'Synthetic record only; no message sent'})
                interview = action('operator', 'interview', {'project_id': 101, 'contact_id': c['id'], 'profile': ['undetermined'],
                    'notes': {'users': 'synthetic', 'commitment': 'synthetic', 'roadmap': 'synthetic', 'credit_need': 'synthetic',
                              'milestone': 'synthetic', 'consent': True}})
                expiry = provider_config['event_end_date']
                due = (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=7)).isoformat()
                decision = {'project_id': 101, 'interview_id': interview['id'], 'outcome': 'approve',
                    'rationale': 'Synthetic funding workflow test', 'terms': {'amount': 1000, 'unit': 'cents',
                    'account': 'acceptance@example.invalid', 'purpose': 'synthetic only', 'event_slug': 'synthetic-demo',
                    'identity_evidence': 'synthetic author confirmation', 'expires_at': expiry, 'review_dates': [due]}}
                action('operator', 'decision', decision, status=403)
                approved = action('approver', 'decision', decision)
                gid = approved['grant_id']
                request('approver', f'/api/credits/{gid}/submit', {'execute': True}, status=403)
                planned = request('disburser', f'/api/credits/{gid}/plan', {})
                self.assertEqual(planned['state'], 'pending')
                self.assertEqual(state['calls'], [])
                submitted = request('disburser', f'/api/credits/{gid}/submit', {'execute': True}, key='acceptance-submit-once')
                self.assertEqual(submitted['status'], 'awaiting_claim')
                repeated = request('disburser', f'/api/credits/{gid}/submit', {'execute': True}, key='acceptance-submit-once')
                self.assertEqual(submitted, repeated)
                self.assertEqual(sum(call['method'] == 'POST' for call in state['calls']), 1)
                self.assertEqual(request('disburser', f'/api/credits/{gid}/reconcile', {})['status'], 'awaiting_claim')
                row = state['rows'][0]
                row.update(consumedAt=now(), participant={'id': 'synthetic-participant', 'activatedAt': now()})
                state['people'].append({'id': 'synthetic-participant', 'invitationId': row['id'], 'activatedAt': now(),
                    'bannedAt': None, 'grantCents': 1000, 'settledCents': 0, 'user': {'email': 'acceptance@example.invalid'}})
                self.assertEqual(request('disburser', f'/api/credits/{gid}/reconcile', {})['status'], 'issued')
                self.assertEqual(request('disburser', f'/api/credits/{gid}/reconcile', {})['status'], 'issued')
                self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM followups')['n'], 1)
                followup = self.s.one('SELECT id FROM followups')
                action('operator', 'followup', {'id': followup['id'], 'result': {'usage': 'synthetic usage',
                    'milestone': 'synthetic acceptance', 'evidence': 'synthetic local HTTP record', 'decision': 'end', 'next_action': 'none'}})
                self.assertEqual(self.s.one('SELECT status FROM grants WHERE id=?', (gid,))['status'], 'closed')
                actors = {row['actor'] for row in self.s.all('SELECT DISTINCT actor FROM events')}
                self.assertTrue({'operator', 'reviewer', 'agent', 'approver', 'disburser'}.issubset(actors))
                result = self.audit()
                self.assertEqual(self.check(result, 'credits_issuance_evidence')['status'], 'missing')
                self.assertFalse(result['production_verified'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
