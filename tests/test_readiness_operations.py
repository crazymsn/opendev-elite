"""Exercise the real workflow from reviews to closed/renewed sponsorships."""
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from worker.auth import Principal
from worker.db import Store, dump
from worker.demo import seed, ready, approve
from worker.dossier import sources
from worker.projects import readiness
from worker.service import Application, ApiFailure
from worker.workflow import Workflow


class OperationalReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.temp.name) / 'test.db')
        seed(self.s)
        self.w = Workflow(self.s)
        self.today = dt.datetime.now(dt.timezone.utc).date()

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()

    def status(self):
        return readiness(self.s, self.w.project(101))

    def application(self):
        return Application(self.s.path, {'users': [{'actor': 'test-reader', 'roles': ['reviewer'],
            'token_sha256': hashlib.sha256(b'offline-operational-readiness-fixture').hexdigest()}]})

    def approved(self):
        ready(self.w)
        return approve(self.w)[0]

    def issued(self):
        identifier = self.approved()
        self.w.apply('grant-reconcile', {'id': identifier, 'status': 'issued', 'receipt': {
            'transaction_id': 'synthetic-receipt', 'account': 'demo@example.invalid',
            'amount': 1000, 'unit': 'cents', 'evidence': 'offline test fixture'}}, 'demo-disburser')
        return identifier

    def test_contact_progression_is_not_repeated_draft_advice(self):
        ready(self.w)
        self.assertEqual(self.status()['next_step'], 'contact')
        c = self.w.apply('contact-draft', {'project_id': 101, 'maintainer_id': 201,
            'channel': 'synthetic', 'draft': 'fixture; no message sent'}, 'operator')
        for state, next_step in (('draft', 'contact-send'), ('sent', 'awaiting-reply'), ('replied', 'interview')):
            if state != 'draft':
                self.w.apply('contact-status', {'id': c['id'], 'status': state, 'evidence': 'synthetic'}, 'operator')
            self.assertEqual(self.status()['next_step'], next_step)
            self.assertEqual(self.status()['next_record'], {'kind': 'contacts', 'id': c['id']})
        self.w.apply('contact-status', {'id': c['id'], 'status': 'declined', 'evidence': 'synthetic'}, 'operator')
        self.assertEqual(self.status()['next_step'], 'contact-declined')
        self.assertFalse(self.status()['allowed']['contact-draft'])

    def test_existing_provider_obligations_take_priority_over_stale_evidence(self):
        gid = self.approved()
        self.assertEqual(self.status()['next_step'], 'credits-plan')
        self.s.conn.execute("UPDATE projects SET head_sha='changed' WHERE id=101")
        for state, next_step in (('pending', 'refresh-project'), ('submitting', 'credits-reconcile'),
                                ('unknown', 'credits-reconcile'), ('awaiting_claim', 'credits-awaiting-claim')):
            self.s.conn.execute('UPDATE grants SET status=? WHERE id=?', (state, gid))
            r = self.status()
            self.assertEqual(r['next_step'], next_step)
            self.assertFalse(r['round_one_current'])
            self.assertFalse(r['round_two_current'])

    def test_issued_grant_tracks_due_and_missing_reviews_even_after_archive(self):
        gid = self.issued()
        self.assertEqual(self.status()['next_step'], 'followup-wait')
        self.s.conn.execute("UPDATE projects SET metadata=json_set(metadata,'$.archived',1),head_sha='changed' WHERE id=101")
        self.s.conn.execute('UPDATE followups SET due_at=? WHERE id=(SELECT id FROM followups ORDER BY due_at LIMIT 1)', (self.today.isoformat(),))
        r = self.status()
        self.assertEqual(r['next_step'], 'followup-due')
        self.assertEqual(r['next_review_at'], self.today.isoformat())
        self.assertEqual(r['next_record']['kind'], 'followups')
        self.s.conn.execute('DELETE FROM followups')  # Legacy issued grant without a scheduled review.
        r = self.status()
        self.assertEqual(r['next_step'], 'followup-plan')
        self.assertEqual(r['followup_grant_id'], gid)

    def test_rejection_and_hold_cancel_unissued_grant(self):
        self.approved()
        interview = self.s.one('SELECT id FROM interviews')['id']
        for outcome, expected in (('hold', 'decision-hold'), ('reject', 'decision-rejected')):
            self.w.apply('decision', {'project_id': 101, 'interview_id': interview,
                'outcome': outcome, 'rationale': 'synthetic evidence'}, 'approver')
            self.assertEqual(self.status()['next_step'], expected)
            self.assertEqual(self.s.one('SELECT status FROM grants')['status'], 'cancelled')

    def test_closed_cycle_and_renewal_use_latest_grant(self):
        old = self.issued()
        followup = self.s.one('SELECT id FROM followups ORDER BY due_at LIMIT 1')['id']
        self.w.apply('followup', {'id': followup, 'result': {'usage': 'synthetic', 'milestone': 'synthetic',
            'evidence': ['fixture'], 'decision': 'end', 'next_action': 'cycle complete'}}, 'reviewer')
        self.assertEqual(self.status()['next_step'], 'cycle-closed')
        decision = self.s.one('SELECT * FROM decisions')
        new = self.w.apply('decision', {'project_id': 101, 'interview_id': decision['interview_id'],
            'outcome': 'approve', 'rationale': 'synthetic renewal', 'terms': json.loads(decision['terms'])}, 'approver')['grant_id']
        self.assertNotEqual(new, old)
        self.assertEqual(self.status()['next_step'], 'credits-plan')
        self.assertEqual(self.status()['next_record']['id'], new)

    def test_expired_or_malformed_legacy_terms_are_not_ready_for_issuance(self):
        self.approved()
        for expiry in (self.today.isoformat(), None, 12, 'not-a-date'):
            terms = json.loads(self.s.one('SELECT terms FROM decisions')['terms'])
            terms['expires_at'] = expiry
            self.s.conn.execute('UPDATE decisions SET terms=?', (dump(terms),))
            self.assertEqual(self.status()['next_step'], 'decision-expired')

    def test_followup_schedule_is_issued_only_future_and_idempotent(self):
        gid = self.approved()
        due = (self.today + dt.timedelta(days=100)).isoformat()
        with self.assertRaisesRegex(ValueError, 'issued'):
            self.w.apply('followup-schedule', {'grant_id': gid, 'due_at': due}, 'operator')
        self.w.apply('grant-reconcile', {'id': gid, 'status': 'issued', 'receipt': {
            'transaction_id': 'fixture', 'account': 'demo@example.invalid', 'amount': 1000,
            'unit': 'cents', 'evidence': 'synthetic'}}, 'disburser')
        for date in (self.today.isoformat(), '2026-99-99', 42):
            with self.assertRaises(ValueError):
                self.w.apply('followup-schedule', {'grant_id': gid, 'due_at': date}, 'operator')
        first = self.w.apply('followup-schedule', {'grant_id': gid, 'due_at': due}, 'operator')
        replay = self.w.apply('followup-schedule', {'grant_id': gid, 'due_at': due}, 'operator')
        self.assertEqual(first['id'], replay['id'])
        self.assertTrue(replay['duplicate'])
        self.assertEqual(self.s.one('SELECT COUNT(*) n FROM followups')['n'], 4)
        self.s.conn.execute("UPDATE followups SET status='completed' WHERE id=?", (first['id'],))
        with self.assertRaisesRegex(ValueError, 'finalized'):
            self.w.apply('followup-schedule', {'grant_id': gid, 'due_at': due}, 'operator')
        self.s.conn.execute("UPDATE grants SET status='closed' WHERE id=?", (gid,))
        with self.assertRaisesRegex(ValueError, 'issued'):
            self.w.apply('followup-schedule', {'grant_id': gid, 'due_at': due}, 'operator')

    def test_approval_requires_at_least_one_followup(self):
        gid = self.approved()
        decision = self.s.one('SELECT * FROM decisions')
        terms = json.loads(decision['terms']); terms['review_dates'] = []
        self.s.conn.execute("UPDATE grants SET status='cancelled' WHERE id=?", (gid,))
        with self.assertRaisesRegex(ValueError, 'review_dates|Review dates'):
            self.w.apply('decision', {'project_id': 101, 'interview_id': decision['interview_id'],
                'outcome': 'approve', 'rationale': 'fixture', 'terms': terms}, 'approver')
        self.assertEqual(self.s.one('SELECT COUNT(*) n FROM decisions')['n'], 1)

    def test_service_roles_only_receive_permitted_record_links(self):
        gid = self.issued()
        app = self.application()
        for role in ('operator', 'reviewer', 'auditor', 'approver', 'disburser'):
            p = Principal(role, frozenset({role}))
            r = app.handle('GET', '/api/projects/101', principal=p)[1]['readiness']
            self.assertEqual(r['next_step'], 'followup-wait')
            self.assertEqual(bool(r['next_record']), role in ('operator', 'reviewer', 'auditor'))
            self.assertEqual(bool(r['next_review_at']), role in ('operator', 'reviewer', 'auditor'))
        self.s.conn.execute('DELETE FROM followups')
        for role in ('operator', 'reviewer', 'auditor', 'approver', 'disburser'):
            p = Principal(role, frozenset({role}))
            r = app.handle('GET', '/api/projects/101', principal=p)[1]['readiness']
            self.assertEqual('followup_grant_id' in r, role in ('operator', 'reviewer'))
            payload = {'grant_id': gid, 'due_at': (self.today + dt.timedelta(days=100)).isoformat()}
            if role in ('operator', 'reviewer'):
                result = app.handle('POST', '/api/actions/followup-schedule', body=payload,
                    request_key='schedule-test-' + role, principal=p)
                self.assertEqual(result[0], 200)
                self.assertEqual(app.handle('POST', '/api/actions/followup-schedule', body=payload,
                    request_key='schedule-test-' + role, principal=p), result)
                self.s.conn.execute('DELETE FROM followups')
            else:
                with self.assertRaises(ApiFailure) as error:
                    app.handle('POST', '/api/actions/followup-schedule', body=payload,
                        request_key='schedule-test-' + role, principal=p)
                self.assertEqual(error.exception.status, 403)

    def test_history_is_paginated_project_scoped_and_agent_denied(self):
        ready(self.w)
        seed(self.s, project_id=102)
        app = self.application()
        for table, expected in (('reviews', 2), ('qualifications', 1)):
            for role in ('operator', 'reviewer', 'auditor', 'approver', 'disburser'):
                data = app.handle('GET', f'/api/records/{table}?project_id=101&page_size=1',
                    principal=Principal(role, frozenset({role})))[1]
                self.assertEqual(data['total'], expected)
                self.assertEqual(len(data['items']), 1)
                self.assertEqual(data['items'][0]['project_id'], 101)
            with self.assertRaises(ApiFailure):
                app.handle('GET', f'/api/records/{table}?project_id=101',
                    principal=Principal('agent', frozenset({'agent'})))

    def test_readme_excerpt_metadata_does_not_change_source_digest(self):
        project = self.w.project(101)
        for text, flag, expected in (('x'*40000, None, True), ('x'*40000, False, False), ('small', None, False), ('small', True, True)):
            evidence = {'readme_text': text}
            if flag is not None: evidence['readme_text_truncated'] = flag
            result = sources(project, {'evidence': dump(evidence), 'fetched_at': 'fixture'})
            self.assertEqual(result['readme']['truncated'], expected)
            self.assertEqual(result['readme_summary']['truncated'], expected)
            self.assertEqual(result['readme']['sha256'], hashlib.sha256(text.encode()).hexdigest())
            self.assertFalse(result['description']['truncated'])


if __name__ == '__main__':
    unittest.main()
