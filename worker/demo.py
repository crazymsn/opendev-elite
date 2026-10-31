"""Synthetic-only end-to-end demonstration. Never contacts a real service."""
import datetime as dt
import json
from pathlib import Path
from .credits import NextCredits
from .db import dump, now
from .report import export_report
from .workflow import Workflow
from .criteria import POLICY_VERSION, SELECTION_POLICY


SHA = 'a' * 40


def selection_evidence():
    return {'scope':'SYNTHETIC: AI developer tool', 'maintenance':'SYNTHETIC: specific fix and release',
            'sources':['fixture:source'], 'reason_code':'none', 'maintenance_kind':'commit',
            'maintenance_date':dt.datetime.now(dt.timezone.utc).date().isoformat(),
            'use_case':'SYNTHETIC: developer processes fixture input into a checkable output',
            'delivery':'SYNTHETIC: local fixture entry and expected output',
            'access_risk':'SYNTHETIC: fixture ownership and use permitted; no external data, costs or capabilities',
            'differentiation':'SYNTHETIC: comparison against a manual fixture workflow, with explicit limits',
            'evaluation_plan':'SYNTHETIC: fixed input/output, negative cases and isolated execution budget',
            'maintenance_dates':[(dt.datetime.now(dt.timezone.utc).date()-dt.timedelta(days=30)).isoformat(),dt.datetime.now(dt.timezone.utc).date().isoformat()],
            'quality':{d['id']:{'score':4,'sources':['fixture:substantive-one','fixture:substantive-two']} for d in SELECTION_POLICY['quality_rubric']['dimensions']}}


def seed(store, project_id=101, audience='both', maintainer_id=201):
    repo = {'id': project_id, 'full_name': f'synthetic-demo/project-{project_id}',
            'html_url': f'https://example.invalid/project-{project_id}', 'stargazers_count': 0, 'archived': False}
    with store.transaction():
        store.upsert_project(repo, 'synthetic-demo')
        store.conn.execute("UPDATE projects SET head_sha=?,refreshed_at=?,audience=?,owner='demo-reviewer',stage='technical_review' WHERE id=?", (SHA, now(), audience, project_id))
        store.conn.execute('INSERT INTO snapshots(project_id,sha,fetched_at,evidence,complete) VALUES(?,?,?,?,1)',
                           (project_id, SHA, now(), dump({'synthetic': True, 'warning': 'Not a real product evaluation'})))
        store.conn.execute('INSERT OR IGNORE INTO maintainers(id,login,kind,profile) VALUES(?,?,?,?)',
                           (maintainer_id, 'synthetic-maintainer', 'User', dump({'email': 'demo@example.invalid'})))
        store.conn.execute('INSERT INTO project_maintainers VALUES(?,?,?,?,?)',
                           (project_id, maintainer_id, 'maintainer', 1, 'synthetic fixture'))
        store.conn.execute('INSERT INTO qualifications(project_id,sha,policy_version,category,scope_fit,maintenance,outcome,audience,rationale,evidence,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (project_id,SHA,POLICY_VERSION,'agent-infra','yes','active','include',audience,'SYNTHETIC SCOPE FIXTURE',
             dump(selection_evidence()),'synthetic-qualifier',now()))


def review_one(workflow, project_id=101):
    return workflow.apply('review', {'project_id': project_id, 'round': 1, 'sha': SHA, 'decision': 'pass',
         'rationale': 'SYNTHETIC DEMO ONLY', 'evidence': {'structure': ['fixture:core'], 'maintenance': ['fixture:commits'],
         'documentation': ['fixture:readme']}}, 'demo-reviewer')


def experience(workflow, audience, project_id=101, success=True):
    e = workflow.apply('experience-create', {'project_id': project_id, 'audience': audience,
        'spec': {'scenario': 'SYNTHETIC: retrieve the fixture answer', 'success_criteria': 'Answer equals fixture',
                 'environment': 'offline fixture', 'input': 'demo query', 'budget': {'seconds': 30}}}, 'demo-reviewer')
    workflow.apply('experience-result', {'id': e['id'], 'sha': SHA,
        'result': {'success': success, 'executor': 'synthetic-fixture-' + audience, 'environment': 'offline fixture',
                   'steps': ['fixture input', 'fixture comparison'], 'actual_result': 'SYNTHETIC EXPECTED ANSWER',
                   'artifacts': ['fixture:demo-result'], 'failure_kind': 'none' if success else 'product'}}, 'demo-reviewer')
    return e['id']


def ready(workflow, audiences=('human','agent'), project_id=101):
    review_one(workflow, project_id)
    ids = [experience(workflow, a, project_id) for a in audiences]
    workflow.apply('review', {'project_id': project_id, 'round': 2, 'sha': SHA, 'decision': 'pass',
        'rationale': 'SYNTHETIC DEMO ONLY', 'evidence': {'real_problem': ['fixture:problem'], 'usability': ['fixture:run'],
        'differentiation': ['fixture:comparison'], 'potential': ['fixture:hypothesis'], 'experience_ids': ids}}, 'demo-reviewer')


def approve(workflow, project_id=101, maintainer_id=201):
    c = workflow.apply('contact-draft', {'project_id': project_id, 'maintainer_id': maintainer_id,
         'channel': 'synthetic-demo', 'draft': 'SYNTHETIC ONLY — NEVER SEND'}, 'demo-operator')
    for status in ('sent','replied'):
        workflow.apply('contact-status', {'id': c['id'], 'status': status, 'evidence': 'synthetic fixture; no message sent'}, 'demo-operator')
    i = workflow.apply('interview', {'project_id': project_id, 'contact_id': c['id'], 'profile': ['undetermined'],
        'notes': {'users': 'fixture', 'commitment': 'fixture', 'roadmap': 'fixture', 'credit_need': 'fixture',
                  'milestone': 'fixture', 'consent': True}}, 'demo-operator')
    today = dt.datetime.now(dt.timezone.utc).date()
    expiry = (today + dt.timedelta(days=90)).isoformat()
    terms = {'amount': 1000, 'unit': 'cents', 'account': 'demo@example.invalid', 'event_slug': 'synthetic-demo',
             'identity_evidence': 'synthetic interview', 'purpose': 'fixture only', 'expires_at': expiry,
             'review_dates': [(today + dt.timedelta(days=x)).isoformat() for x in (7,30,60)]}
    d = workflow.apply('decision', {'project_id': project_id, 'interview_id': i['id'], 'outcome': 'approve',
        'rationale': 'SYNTHETIC DEMO ONLY', 'terms': terms}, 'demo-approver')
    return d['grant_id'], {'base_url': 'https://example.invalid', 'event_slug': 'synthetic-demo',
                          'event_end_date': expiry, 'event_config_evidence': 'synthetic fixture'}


class FakeCredits(NextCredits):
    def __init__(self, store, config):
        super().__init__(store, config)
        self.rows, self.people, self.calls = [], [], []

    def request(self, method, path, body=None):
        self.calls.append({'method': method, 'path': path})
        if path == '/openapi/v1.json':
            return {'paths': {'/api/v1/events/{slug}/invitations': {'get': {}, 'post': {}},
                              '/api/v1/events/{slug}/participants': {'get': {}}}}
        if method == 'POST':
            row = dict(body, id='synthetic-invitation', participant=None, consumedAt=None)
            self.rows.append(row)
            return row
        rows = self.people if '/participants?' in path else self.rows
        return {'items': rows, 'total': len(rows), 'page': 1, 'pageSize': 100}

    def activate(self):
        self.rows[0].update(consumedAt=now(), participant={'id': 'synthetic-participant', 'activatedAt': now()})
        self.people = [{'id': 'synthetic-participant', 'invitationId': 'synthetic-invitation',
                        'activatedAt': now(), 'bannedAt': None, 'grantCents': self.rows[0]['grantCents'],
                        'settledCents': 125, 'user': {'email': self.rows[0]['email']}}]


def run_demo(store, destination):
    if store.one('SELECT id FROM projects LIMIT 1'):
        raise ValueError('Demo requires an empty, dedicated database')
    seed(store)
    workflow = Workflow(store)
    ready(workflow)
    grant_id, config = approve(workflow)
    provider = FakeCredits(store, config)
    plan = provider.submit(grant_id, 'demo-operator')
    invitation = provider.submit(grant_id, 'demo-operator', execute=True)
    pending = provider.reconcile(grant_id, 'demo-operator')
    provider.activate()
    activated = provider.reconcile(grant_id, 'demo-operator')
    due = store.one('SELECT id FROM followups ORDER BY due_at LIMIT 1')
    workflow.apply('followup', {'id': due['id'], 'result': {'usage': 'SYNTHETIC: 125 settled cents',
        'milestone': 'fixture milestone', 'evidence': ['fixture:usage'], 'decision': 'continue',
        'next_action': 'synthetic second review'}}, 'demo-reviewer')
    root = Path(destination); root.mkdir(parents=True, exist_ok=True)
    result = {'synthetic_only': True, 'real_network_requests': 0, 'real_messages_sent': 0, 'real_credits_issued': 0,
              'plan': plan, 'invitation': invitation, 'pending': pending, 'activated': activated,
              'simulated_api_calls': provider.calls}
    (root / 'demo-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    export_report(store, root / 'demo-report.md')
    return {'synthetic_only': True, 'output': str(root.resolve()), 'grant_id': grant_id,
            'events': store.one('SELECT COUNT(*) AS count FROM events')['count']}
