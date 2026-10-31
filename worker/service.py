"""Authenticated single-team JSON API. No shell execution or mail endpoints."""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import socket
import sqlite3
import ssl
import sys
import threading
import time
from importlib import resources
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from .auth import IdentityStore
from .accounts import Accounts, AuthThrottle, CombinedIdentities, derive, password_bytes
from .browser_auth import BrowserFailure, BrowserPolicy, BrowserSessions, configured_origin
from .oidc import OIDCClient
from .artifacts import ArtifactStore, ArtifactDownload
from .credits import CreditsError, NextCredits
from .db import Store, dump, now
from .github import GitHub
from .pipeline import Pipeline, normalize_repo
from .report import summary
from .workflow import Workflow
from .contact_points import for_project, import_leads, sync_project

LOG = logging.getLogger('sponsorship.api')


class ApiFailure(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


ACTION_ROLES = {
    'qualify': ('reviewer','operator'),
    'assign': ('reviewer','operator'), 'review': ('reviewer',),
    'experience-create': ('reviewer',), 'experience-result': ('reviewer',),
    'maintainer-confirm': ('reviewer','operator'),
    'contact-draft': ('operator',), 'contact-status': ('operator',),
    'interview': ('operator',), 'decision': ('approver',), 'followup': ('operator','reviewer'),
    'followup-schedule': ('operator','reviewer'),
}
RECORDS = {
    'reviews': ('reviewer','operator','approver','disburser','auditor'),
    'qualifications': ('reviewer','operator','approver','disburser','auditor'),
    'experiences': ('reviewer',), 'contacts': ('operator',), 'interviews': ('operator','approver'),
    'decisions': ('approver','disburser','auditor'), 'grants': ('approver','disburser','auditor'),
    'followups': ('operator','reviewer','auditor'), 'events': ('auditor',),
    'jobs': ('operator','auditor'), 'search_coverage': ('operator','reviewer','auditor'),
    'ai_assessments': ('reviewer','operator','auditor'), 'project_rankings': ('reviewer','operator','approver','disburser','auditor'),
    'contact_summaries': ('reviewer','operator','auditor'), 'discovery_runs': ('operator','auditor'),
}
READERS = ('reviewer','operator','approver','disburser','auditor')


class Application:
    def __init__(self, db_path, config, credits_config=None):
        self.db_path, self.config, self.credits_config = db_path, config, credits_config
        self.identities = IdentityStore(config)
        configured_origin(config.get('public_origin'))
        self.accounts = Accounts(db_path)
        initial_password = config.get('initial_admin_password') or os.environ.get('OPENDEV_INITIAL_ADMIN_PASSWORD')
        public_origin = config.get('public_origin') or ''
        if not initial_password and (not public_origin or '127.0.0.1' in public_origin or 'localhost' in public_origin):
            initial_password = '123456'
        self.accounts.ensure_default_admin(initial_password=initial_password, allow_legacy_default=(not public_origin or '127.0.0.1' in public_origin or 'localhost' in public_origin))
        self.identities = CombinedIdentities(config, self.accounts)
        self.auth_throttle = AuthThrottle()
        self.sessions = BrowserSessions(self.identities, config.get('browser_session_seconds', 3600))
        self.oidc = OIDCClient(config['oidc'], config.get('public_origin'), self.identities) if config.get('oidc') is not None else None
        if type(config.get('allow_credit_writes',False)) is not bool:
            raise ValueError('allow_credit_writes must be boolean')
        self.lease_seconds = config.get('agent_lease_seconds', 900)
        if type(self.lease_seconds) is not int or not 60 <= self.lease_seconds <= 3600:
            raise ValueError('agent_lease_seconds must be 60..3600')

    @staticmethod
    def require(principal, *roles):
        if not principal.allows(*roles):
            raise ApiFailure(403, 'This identity does not have the required role')

    def scoped(self, p, project_id):
        if 'sub_admin' in p.roles and project_id not in p.project_ids:
            raise ApiFailure(403, 'Project is outside this sub-admin scope')
    @staticmethod
    def project_id(store, identifier):
        """Resolve the stable public UID while retaining numeric compatibility routes."""
        if re.fullmatch(r'[1-9][0-9]{0,18}', identifier):
            return int(identifier)
        if re.fullmatch(r'prj_[a-f0-9]{32}', identifier):
            row = store.one('SELECT id FROM projects WHERE uid=?', (identifier,))
            if not row:
                raise ApiFailure(404, 'Project not found')
            return row['id']
        raise ApiFailure(404, 'Unknown project identifier')


    def auth_options(self, origin=None):
        return {'sso_enabled': self.oidc is not None, 'login_url': '/auth/sso/login' if self.oidc else None,
                'password_login': True, 'registration_open': False,
                'setup_required': False, 'bootstrap_allowed': False}

    def handle(self, method, target, authorization=None, body=None, request_key=None, *, principal=None):
        path, query = urlsplit(target).path, parse_qs(urlsplit(target).query)
        if path in ('/api/inbox','/api/projects') or path.startswith('/api/developers'):
            # Blank values must be rejected rather than silently becoming defaults.
            query = parse_qs(urlsplit(target).query, keep_blank_values=True)
        if method == 'GET' and path == '/healthz':
            return 200, {'ok': True}
        if method == 'GET' and path == '/openapi.json':
            return 200, openapi()
        if method == 'GET' and path == '/auth/options':
            return 200, self.auth_options()
        principal = principal or self.identities.authenticate(authorization)
        if not principal:
            raise ApiFailure(401, 'Valid Bearer credentials required')
        if method not in ('GET','POST'):
            raise ApiFailure(405, 'Only GET and POST are supported')
        store = Store(self.db_path)
        try:
            if method == 'GET':
                store.conn.execute('BEGIN')
                return 200, self.read(store, principal, path, query)
            if not isinstance(body, dict):
                raise ApiFailure(400, 'JSON body must be an object')
            if 'actor' in body:
                raise ApiFailure(400, 'actor is assigned by the authenticated server identity')
            if not isinstance(request_key,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', request_key):
                raise ApiFailure(400, 'A unique Idempotency-Key header (8..128 simple characters) is required')
            # Check permission even for a replay (a restarted configuration may revoke a role).
            self.authorize_write(principal, path)
            self.preflight_credit(store,principal,path,body)
            fingerprint_body = dict(body)
            if (path == '/api/accounts' or path.startswith('/api/accounts/')) and 'password' in body:
                # Do not leave a fast offline password verifier in api_requests.
                salt = hashlib.sha256((principal.actor + request_key).encode()).digest()[:16]
                fingerprint_body['password'] = derive(password_bytes(body['password'], creating=True), salt)
            fingerprint = hashlib.sha256((path + '\n' + dump(fingerprint_body)).encode()).hexdigest()
            external = bool(re.fullmatch(r'/api/credits/[^/]+/reconcile',path) or
                            (re.fullmatch(r'/api/credits/[^/]+/submit',path) and body.get('execute') is True))
            with store.transaction():
                previous = store.one('SELECT * FROM api_requests WHERE actor=? AND request_key=?', (principal.actor, request_key))
                if previous:
                    if not hmac.compare_digest(previous['fingerprint'], fingerprint):
                        raise ApiFailure(409, 'Idempotency-Key already belongs to a different request')
                    if previous['state'] != 'done':
                        raise ApiFailure(409, 'Request outcome needs reconciliation; do not repeat the write')
                    return previous['status_code'], json.loads(previous['response'])
                store.conn.execute('INSERT INTO api_requests(actor,request_key,fingerprint,state,created_at) VALUES(?,?,?,?,?)',
                                   (principal.actor, request_key, fingerprint, 'running', now()))
                if not external:
                    result = self.write(store, principal, path, body)
                    self.complete(store, principal.actor, request_key, result)
                    return 200, result
            # Do not hold a database transaction while contacting a provider.
            # A crash leaves running + grant state for read-only reconciliation.
            result = self.write(store, principal, path, body)
            with store.transaction():
                self.complete(store, principal.actor, request_key, result)
            return 200, result
        finally:
            store.close()

    @staticmethod
    def complete(store, actor, key, result):
        store.conn.execute("UPDATE api_requests SET state='done',response=?,status_code=200 WHERE actor=? AND request_key=?",
                           (dump(result), actor, key))

    def authorize_write(self, p, path):
        if path == '/api/accounts' or re.fullmatch(r'/api/accounts/account\.[a-f0-9]{32}', path):
            self.require(p, 'admin')
        elif re.fullmatch(r'/api/projects/(?:[1-9][0-9]{0,18}|prj_[a-f0-9]{32})/translations',path):
            self.require(p,'reviewer','operator')
        elif path.startswith('/api/actions/'):
            action = path.removeprefix('/api/actions/')
            if action not in ACTION_ROLES:
                raise ApiFailure(404, 'Action is not exposed by this service')
            self.require(p, *ACTION_ROLES[action])
        elif path in ('/api/import','/api/contact-leads/import','/api/schedule','/api/discovery-runs') or re.fullmatch(r'/api/jobs/\d+/retry',path) or re.fullmatch(r'/api/projects/(?:[1-9][0-9]{0,18}|prj_[a-f0-9]{32})/contacts/discover', path) or re.fullmatch(r'/api/projects/(?:[1-9][0-9]{0,18}|prj_[a-f0-9]{32})/contacts/rescan', path):
            self.require(p, 'operator')
        elif re.fullmatch(r'/api/projects/(?:[1-9][0-9]{0,18}|prj_[a-f0-9]{32})/ai-assess', path):
            self.require(p, 'reviewer', 'operator')
        elif path == '/api/referrals':
            self.require(p, 'operator', 'admin')
        elif path == '/api/community/memberships':
            self.require(p, 'admin', 'operator')
        elif path.startswith('/api/agent/'):
            # Human admins cannot impersonate an Agent worker with their own token.
            if p.roles != frozenset({'agent'}):
                raise ApiFailure(403, 'A dedicated Agent identity is required')
        elif re.fullmatch(r'/api/credits/[^/]+/(plan|submit|reconcile)',path):
            self.require(p, 'disburser')
        else:
            raise ApiFailure(404, 'Unknown API endpoint')

    def preflight_credit(self,s,p,path,d):
        match = re.fullmatch(r'/api/credits/([a-zA-Z0-9_-]+)/(plan|submit|reconcile)',path)
        if not match:
            return
        if not self.credits_config:
            raise ApiFailure(409,'Credits service is not configured')
        if match[2] == 'submit':
            execute = d.get('execute',False)
            if type(execute) is not bool:
                raise ApiFailure(400,'execute must be a boolean')
            if execute:
                if not self.config.get('allow_credit_writes',False):
                    raise ApiFailure(403,'Credits writes are disabled in server configuration')
                row = s.one('SELECT d.actor FROM decisions d JOIN grants g ON g.decision_id=d.id WHERE g.id=?',(match[1],))
                if row and row['actor'] == p.actor:
                    raise ApiFailure(403,'The approver cannot execute their own grant')

    @staticmethod
    def pagination(query):
        try:
            page = int(query.get('page',['1'])[0]); size = int(query.get('page_size',['50'])[0])
        except (ValueError, TypeError, IndexError):
            raise ApiFailure(400, 'Invalid pagination') from None
        if not 1 <= page <= 100000 or not 1 <= size <= 200:
            raise ApiFailure(400, 'page must be positive; page_size must be 1..200')
        return page, size

    def read(self, s, p, path, query):
        if path == '/api/developers' or re.fullmatch(r'/api/developers/[1-9][0-9]{0,17}(?:/records/[a-z_]+)?',path):
            self.require(p,*READERS)
            from . import developers
            if path == '/api/developers':
                return developers.listing(s,query)
            parts=path.split('/')
            identifier=int(parts[3])
            if len(parts)>4:
                kind=parts[5]
                if kind not in developers.HISTORY:
                    raise ApiFailure(404,'Unknown developer record type')
                self.require(p,*RECORDS[kind])
            row=s.one('SELECT * FROM maintainers WHERE id=?',(identifier,))
            if not row:
                raise ApiFailure(404,'Developer or team not found')
            if len(parts)>4:
                return developers.history(s,identifier,kind,query)
            return developers.detail(s,row,query,[kind for kind,roles in RECORDS.items() if p.allows(*roles)])
        if path == '/api/criteria':
            self.require(p,*READERS)
            from .criteria import catalog
            return catalog()
        if path == '/api/accounts':
            self.require(p, 'admin')
            return self.accounts.listing(s, *self.pagination(query))
        if path == '/api/me':
            return {'actor':p.actor, 'roles':sorted(p.roles)}
        if path == '/api/ui-config':
            self.require(p, *READERS)
            financial = p.allows('disburser')
            from .inbox import available_kinds
            return {'actor': p.actor, 'roles': sorted(p.roles),
                    'inbox_kinds': available_kinds(p),
                    'actions': [action for action, roles in ACTION_ROLES.items() if p.allows(*roles)],
                    'records': [table for table, roles in RECORDS.items() if p.allows(*roles)],
                    'capabilities': {'manage_accounts': p.allows('admin'), 'password_change': p.auth_version is not None,
                                     'import': p.allows('operator'), 'schedule': p.allows('operator'),
                                     'credits_plan': financial, 'credits_reconcile': financial,
                                     'credits_submit': financial and bool(self.credits_config) and self.config.get('allow_credit_writes', False)},
                    'credits_configured': bool(self.credits_config),
                    'allow_credit_writes': self.config.get('allow_credit_writes', False),
                    'demo_mode': self.config.get('demo_mode') is True}
        if path == '/api/metrics':
            self.require(p, 'auditor', 'admin')
            return summary(s)
        if path == '/api/referrals':
            self.require(p, 'operator', 'auditor', 'admin')
            page, size = self.pagination(query)
            total = s.one('SELECT COUNT(*) AS n FROM referrals')['n']
            return {'items': s.all('SELECT id,link_id,project_id,submitter,state,created_at FROM referrals ORDER BY rowid DESC LIMIT ? OFFSET ?',
                                   (size, (page - 1) * size)), 'total': total, 'page': page, 'page_size': size}
        if path == '/api/discovery-runs':
            self.require(p, 'operator', 'auditor')
            page, size = self.pagination(query)
            total = s.one('SELECT COUNT(*) AS n FROM discovery_runs')['n']
            return {'items': s.all('SELECT * FROM discovery_runs ORDER BY created_at DESC LIMIT ? OFFSET ?',
                                   (size, (page - 1) * size)), 'total': total, 'page': page, 'page_size': size}
        match = re.fullmatch(r'/api/discovery-runs/([A-Za-z0-9_-]{8,80})', path)
        if match:
            self.require(p, 'operator', 'auditor')
            item = s.one('SELECT * FROM discovery_runs WHERE id=?', (match[1],))
            if not item:
                raise ApiFailure(404, 'Discovery run not found')
            item['jobs'] = s.all('SELECT id,kind,state,attempts,error,created_at FROM jobs WHERE json_extract(payload,\'$.run_id\')=? ORDER BY id', (match[1],))
            return {'item': item}
        if path == '/api/inbox':
            from .inbox import InboxFailure, inbox
            try:
                return inbox(s, p, query)
            except InboxFailure as exc:
                raise ApiFailure(exc.status, str(exc)) from None
        if path == '/api/status' and 'sub_admin' in p.roles:
            raise ApiFailure(403, 'Sub-admin cannot read global status')
        if path == '/api/metrics' and 'sub_admin' in p.roles:
            raise ApiFailure(403, 'Sub-admin cannot read global metrics')
        if path == '/api/status':
            self.require(p,*READERS)
            value = summary(s)
            from .inbox import KIND_ROLES, available_kinds
            visible = available_kinds(p)
            for kind in KIND_ROLES:
                if kind not in visible:
                    value.pop(kind, None)
            # A reviewer needs queue status, not provider errors or grant details.
            if not p.allows('operator','disburser','auditor'):
                value.pop('grants',None)
            return value
        if path == '/api/projects':
            self.require(p,*READERS)
            if 'sub_admin' in p.roles:
                query['_scope_project_ids'] = list(p.project_ids)
            from .projects import list_projects
            return list_projects(s,query)
        match = re.fullmatch(r'/api/projects/([1-9][0-9]{0,18}|prj_[a-f0-9]{32})',path)
        if match:
            self.require(p,*READERS)
            project_id = self.project_id(s, match[1])
            self.scoped(p, project_id)
            project = Workflow(s).project(project_id)
            result = {'project':project, 'reviews':s.all('SELECT * FROM reviews WHERE project_id=? ORDER BY id DESC LIMIT 100',(project['id'],)),
                      'snapshot':s.one('SELECT * FROM snapshots WHERE project_id=? ORDER BY id DESC LIMIT 1',(project['id'],))}
            from .criteria import qualification_status
            from .dossier import dossier
            result['qualification'] = qualification_status(s,project)
            result['dossier'] = dossier(s,project,result['snapshot'])
            from .technical_evidence import technical_evidence
            result['technical_evidence'] = technical_evidence(project,result['snapshot'])
            from .projects import readiness
            result['readiness'] = readiness(s,project)
            record=result['readiness']['next_record']
            if record and not p.allows(*RECORDS[record['kind']]):
                result['readiness']['next_record']=None
            if not p.allows(*RECORDS['followups']):
                result['readiness']['next_review_at']=None
            if not p.allows(*ACTION_ROLES['followup-schedule']):
                result['readiness'].pop('followup_grant_id',None)
            if p.allows('operator','reviewer'):
                maintainers = s.all('SELECT m.id,m.login,m.kind,m.profile,pm.role,pm.confirmed FROM maintainers m JOIN project_maintainers pm ON pm.maintainer_id=m.id WHERE pm.project_id=?',(project['id'],))
                for maintainer in maintainers:
                    try:
                        profile = json.loads(maintainer.pop('profile') or '{}')
                    except (TypeError, ValueError):
                        profile = {}
                    maintainer.update({key: profile.get(key) if isinstance(profile.get(key), str) else None
                                       for key in ('name','bio','company','blog')})
                    maintainer['url'] = 'https://github.com/' + maintainer['login']
                result['maintainers'] = maintainers
            if p.allows('operator'):
                result['contact_points'] = for_project(s, project['id'])
                result['contact_summary'] = s.one('SELECT * FROM contact_summaries WHERE project_id=? ORDER BY created_at DESC LIMIT 1', (project['id'],))
            if p.allows('reviewer','operator','auditor'):
                result['rankings'] = s.all('SELECT * FROM project_rankings WHERE project_id=? ORDER BY captured_at DESC,id DESC LIMIT 50', (project['id'],))
                result['ai_assessments'] = s.all('SELECT * FROM ai_assessments WHERE project_id=? ORDER BY created_at DESC LIMIT 20', (project['id'],))
            return result
        match = re.fullmatch(r'/api/projects/([1-9][0-9]{0,18}|prj_[a-f0-9]{32})/(rankings|ai-assessments|contacts/summary)', path)
        if match:
            project_id, kind = self.project_id(s, match[1]), match[2]
            self.require(p, *(('reviewer','operator','auditor') if kind != 'contacts/summary' else ('reviewer','operator','auditor')))
            if not s.one('SELECT id FROM projects WHERE id=?', (project_id,)):
                raise ApiFailure(404, 'Project not found')
            if kind == 'rankings':
                return {'items': s.all('SELECT * FROM project_rankings WHERE project_id=? ORDER BY captured_at DESC,id DESC LIMIT 200', (project_id,))}
            if kind == 'ai-assessments':
                return {'items': s.all('SELECT * FROM ai_assessments WHERE project_id=? ORDER BY created_at DESC LIMIT 50', (project_id,))}
            return {'item': s.one('SELECT * FROM contact_summaries WHERE project_id=? ORDER BY created_at DESC LIMIT 1', (project_id,))}
        match = re.fullmatch(r'/api/records/([a-z_]+)/([A-Za-z0-9_-]{1,128})', path)
        if match and match[1] in RECORDS:
            table, record_id = match.groups()
            self.require(p, *RECORDS[table])
            key_column = 'job_id' if table == 'search_coverage' else 'id'
            record = s.one(f'SELECT * FROM {table} WHERE {key_column}=?', (record_id,))
            if not record:
                raise ApiFailure(404, 'Record not found')
            return {'item': record}
        match = re.fullmatch(r'/api/records/([a-z_]+)',path)
        if match and match[1] in RECORDS:
            table = match[1]; self.require(p,*RECORDS[table])
            page,size = self.pagination(query)
            clauses, params = [], []
            project_filter = query.get('project_id', [''])[0]
            if project_filter:
                if not re.fullmatch(r'[1-9][0-9]{0,18}', project_filter):
                    raise ApiFailure(400, 'Invalid project_id filter')
                project_id = int(project_filter)
                if project_id > 9223372036854775807:
                    raise ApiFailure(400, 'Invalid project_id filter')
                if table in ('experiences', 'contacts', 'interviews', 'decisions', 'reviews', 'qualifications'):
                    clauses.append('project_id=?')
                elif table == 'grants':
                    clauses.append('decision_id IN (SELECT id FROM decisions WHERE project_id=?)')
                elif table == 'followups':
                    clauses.append('grant_id IN (SELECT g.id FROM grants g JOIN decisions d ON d.id=g.decision_id WHERE d.project_id=?)')
                elif table == 'events':
                    clauses.append("((entity_type='project' AND entity_id=CAST(? AS TEXT)) OR json_extract(payload,'$.input.project_id')=?)")
                    params.append(project_id)
                else:
                    raise ApiFailure(400, 'This record type does not support project_id filtering')
                params.append(project_id)
            state = query.get('status', [''])[0]
            if state:
                columns = {'experiences':'status', 'contacts':'status', 'decisions':'outcome', 'grants':'status',
                           'followups':'status', 'jobs':'state', 'search_coverage':'status'}
                if table not in columns or not re.fullmatch(r'[a-z_]{1,40}', state):
                    raise ApiFailure(400, 'Invalid record status filter')
                clauses.append(columns[table] + '=?'); params.append(state)
            where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
            total = s.one(f'SELECT COUNT(*) AS n FROM {table}' + where, params)['n']
            # Identifiers come only from the constant allowlist above, never user SQL.
            rows = s.all(f'SELECT * FROM {table}' + where + ' ORDER BY rowid DESC LIMIT ? OFFSET ?', params + [size,(page-1)*size])
            return {'items':rows,'total':total,'page':page,'page_size':size}
        match = re.fullmatch(r'/api/experiences/([a-zA-Z0-9_-]+)/artifacts', path)
        if match:
            self.require(p, 'reviewer', 'operator', 'auditor')
            if not s.one('SELECT id FROM experiences WHERE id=?', (match[1],)):
                raise ApiFailure(404, 'Experience not found')
            return {'items': ArtifactStore(s).list(match[1])}
        match = re.fullmatch(r'/api/artifacts/([a-f0-9]{32})/download', path)
        if match:
            self.require(p, 'reviewer', 'operator', 'auditor')
            metadata = s.one('SELECT u.id,u.path,u.size,u.sha256 FROM artifact_uploads u JOIN artifact_files f ON f.id=u.id WHERE u.id=?', (match[1],))
            if not metadata:
                raise ApiFailure(404, 'Completed artifact not found')
            return ArtifactDownload(self.db_path, metadata)
        raise ApiFailure(404, 'Unknown API endpoint')

    def write(self, s, p, path, d):
        if path == '/api/referrals':
            from .community import create_referral_link, submit_referral
            operation = d.get('operation', 'create')
            try:
                if operation == 'create':
                    scope = d.get('scope_project_id')
                    if scope is not None and (type(scope) is not int or scope < 1):
                        raise ValueError('scope_project_id must be a positive integer')
                    return create_referral_link(s, p.actor, scope, d.get('expires_at'))
                if operation == 'submit':
                    if type(d.get('project_id')) is not int or d['project_id'] < 1 or not isinstance(d.get('token'), str):
                        raise ValueError('token and positive project_id are required')
                    return submit_referral(s, d['token'], d['project_id'], p.actor, d.get('evidence', {}))
                raise ValueError('operation must be create or submit')
            except ValueError as exc:
                raise ApiFailure(400, str(exc)) from None
        if path == '/api/community/memberships':
            from .community import grant_sub_admin
            try:
                if type(d.get('maintainer_id')) is not int or type(d.get('project_id')) is not int:
                    raise ValueError('maintainer_id and project_id are required integers')
                return grant_sub_admin(s, d['maintainer_id'], d['project_id'], p.actor, d.get('expires_at'))
            except ValueError as exc:
                raise ApiFailure(400, str(exc)) from None
        match = re.fullmatch(r'/api/projects/([1-9][0-9]{0,18}|prj_[a-f0-9]{32})/contacts/rescan', path)
        if match:
            try:
                return sync_project(s, self.project_id(s, match[1]))
            except ValueError as exc:
                raise ApiFailure(400, str(exc)) from None
        match = re.fullmatch(r'/api/projects/([1-9][0-9]{0,18}|prj_[a-f0-9]{32})/ai-assess', path)
        if match:
            project_id = self.project_id(s, match[1])
            snapshot = s.one('SELECT id FROM snapshots WHERE project_id=? ORDER BY id DESC LIMIT 1', (project_id,))
            if not snapshot:
                raise ApiFailure(409, 'A project snapshot is required before AI assessment')
            job_id = s.enqueue('ai_assess', {'project_id': project_id, 'snapshot_id': snapshot['id']},
                               f"ai:{project_id}:{snapshot['id']}:rules-v1")
            return {'job_id': job_id, 'project_id': project_id, 'snapshot_id': snapshot['id'], 'status': 'queued'}
        match = re.fullmatch(r'/api/projects/([1-9][0-9]{0,18}|prj_[a-f0-9]{32})/translations',path)
        if match:
            from .dossier import save_translation
            return save_translation(s,self.project_id(s, match[1]),d,p.actor)
        if path == '/api/accounts':
            return self.accounts.create(s, p, d)
        if re.fullmatch(r'/api/accounts/account\.[a-f0-9]{32}', path):
            result = self.accounts.update(s, p, path.rsplit('/', 1)[1], d)
            # Persisted auth_version invalidates every old session at resolution.
            return result
        if path.startswith('/api/actions/'):
            action = path.removeprefix('/api/actions/')
            if action == 'experience-result':
                e = s.one('SELECT audience FROM experiences WHERE id=?',(d.get('id'),))
                if e and e['audience'] == 'agent':
                    raise ApiFailure(403,'Agent results must arrive through the authenticated task lease endpoint')
            result = Workflow(s).apply(action, d, p.actor)
            if action == 'qualify' and result.get('outcome') == 'include':
                sync_project(s, int(d['project_id']))
            return result
        if path == '/api/import':
            if not isinstance(d.get('repos'),list) or not 1 <= len(d['repos']) <= 100:
                raise ApiFailure(400,'repos must contain 1..100 repository names')
            if not all(isinstance(x,str) for x in d['repos']):
                raise ApiFailure(400,'Each repository must be a string')
            names = sorted(set(normalize_repo(x) for x in d['repos']))
            run = secrets.token_hex(16)
            ids = [s.enqueue('refresh',{'repo':name,'source':'api:' + p.actor},'api-import:' + run + ':' + name) for name in names]
            s.event('api','import','import',p.actor,{'count':len(ids)})
            return {'job_ids':ids}
        if path == '/api/contact-leads/import':
            if not isinstance(d.get('filename'), str) or not isinstance(d.get('csv_text'), str):
                raise ApiFailure(400, 'filename and csv_text are required')
            try:
                return import_leads(s, d['csv_text'], d['filename'], p.actor)
            except (ValueError, UnicodeError) as exc:
                raise ApiFailure(400, str(exc)) from None
        match = re.fullmatch(r'/api/projects/([1-9][0-9]{0,18}|prj_[a-f0-9]{32})/contacts/discover', path)
        if match:
            try:
                return sync_project(s, self.project_id(s, match[1]))
            except ValueError as exc:
                raise ApiFailure(400, str(exc)) from None
        if path == '/api/schedule':
            config = self.config.get('discovery')
            if not config:
                raise ApiFailure(409,'Configure server discovery queries before scheduling')
            pipeline = Pipeline(s,GitHub(s))
            job_ids = pipeline.schedule(config,d.get('start'),d.get('end'),d.get('source'),p.actor,
                                        d.get('period','daily'),d.get('language'),d.get('max_projects'))
            result = {'run_id': pipeline.last_run_id, 'job_ids': job_ids, 'scheduled_count': len(job_ids),
                      'message': f'{len(job_ids)} discovery tasks queued; the collection worker will process them.'}
            s.event('api','schedule','schedule',p.actor,result)
            return result
        if path == '/api/discovery-runs':
            config = self.config.get('discovery')
            if not config:
                raise ApiFailure(409, 'Configure server discovery queries before scheduling')
            pipeline = Pipeline(s, GitHub(s))
            job_ids = pipeline.schedule(config, d.get('start'), d.get('end'), d.get('source'), p.actor,
                                        d.get('period', 'daily'), d.get('language'), d.get('max_projects'))
            result = {'run_id': pipeline.last_run_id, 'job_ids': job_ids, 'scheduled_count': len(job_ids)}
            s.event('api', pipeline.last_run_id, 'discovery_run_created', p.actor, result)
            return result
        match = re.fullmatch(r'/api/jobs/(\d+)/retry',path)
        if match:
            cur = s.conn.execute("UPDATE jobs SET state='queued',attempts=0,available_at=0,error=NULL WHERE id=? AND state='dead'",(int(match[1]),))
            if not cur.rowcount:
                raise ApiFailure(409,'Job is not in dead-letter state')
            s.event('job',match[1],'retry',p.actor,{})
            return {'id':int(match[1]),'state':'queued'}
        if path.startswith('/api/agent/'):
            return self.agent(s,p,path,d)
        match = re.fullmatch(r'/api/credits/([a-zA-Z0-9_-]+)/(plan|submit|reconcile)',path)
        if match:
            if not self.credits_config:
                raise ApiFailure(409,'Credits service is not configured')
            grant_id, operation = match.groups()
            provider = NextCredits(s,self.credits_config)
            if operation == 'plan':
                return provider.plan(grant_id)
            if operation == 'reconcile':
                return provider.reconcile(grant_id,p.actor)
            execute = d.get('execute',False)
            if type(execute) is not bool:
                raise ApiFailure(400,'execute must be a boolean')
            if execute:
                if not self.config.get('allow_credit_writes',False):
                    raise ApiFailure(403,'Credits writes are disabled in server configuration')
                decision = s.one('SELECT d.actor FROM decisions d JOIN grants g ON g.decision_id=d.id WHERE g.id=?',(grant_id,))
                if not decision:
                    raise ApiFailure(404,'Grant not found')
                if decision['actor'] == p.actor:
                    raise ApiFailure(403,'The approver cannot execute their own grant')
            return provider.submit(grant_id,p.actor,execute)
        raise ApiFailure(404,'Unknown API endpoint')

    def agent(self,s,p,path,d):
        w = Workflow(s)
        if path == '/api/agent/claim':
            current = s.one("SELECT experience_id FROM agent_leases WHERE actor=? AND state='running' AND lease_until>?",(p.actor,time.time()))
            if current:
                raise ApiFailure(409,'This Agent already has a live task; finish or renew it')
            cursor = 0
            def eligible():
                nonlocal cursor
                while True:
                    candidates = s.all("SELECT e.*,e.rowid AS row_num FROM experiences e JOIN projects p ON p.id=e.project_id LEFT JOIN agent_leases l ON e.id=l.experience_id WHERE e.rowid>? AND e.audience='agent' AND e.status='pending' AND e.sha=p.head_sha AND (l.experience_id IS NULL OR (l.state='running' AND l.lease_until<=?)) ORDER BY e.rowid LIMIT 100",(cursor,time.time()))
                    if not candidates:
                        return
                    for row in candidates:
                        cursor = row['row_num']
                        yield row
            for experience in eligible():
                project = w.project(experience['project_id'])
                if project['head_sha'] != experience['sha'] or not w.passed(project,1):
                    continue
                from .runner import build_task
                try:
                    task = build_task(s,experience['id'])
                except ValueError:
                    continue
                task['review_id'] = w.latest_review(project['id'],1)['id']
                task_json = dump(task)
                task_sha256 = hashlib.sha256(task_json.encode()).hexdigest()
                token = secrets.token_urlsafe(32)
                until = time.time() + self.lease_seconds
                s.conn.execute('INSERT INTO agent_leases(experience_id,actor,token_hash,lease_until,state,task,task_sha256) VALUES(?,?,?,?,?,?,?) ON CONFLICT(experience_id) DO UPDATE SET actor=excluded.actor,token_hash=excluded.token_hash,lease_until=excluded.lease_until,state=excluded.state,task=excluded.task,task_sha256=excluded.task_sha256',
                               (experience['id'],p.actor,hashlib.sha256(token.encode()).hexdigest(),until,'running',task_json,task_sha256))
                s.event('experience',experience['id'],'agent_claimed',p.actor,{'lease_until':until})
                return {'task':task,'task_sha256':task_sha256,'lease_token':token,'lease_until':until}
            return {'task':None}
        match = re.fullmatch(r'/api/agent/tasks/([a-zA-Z0-9_-]+)/(result|heartbeat|artifacts)',path)
        if not match:
            raise ApiFailure(404,'Unknown Agent endpoint')
        eid, operation = match.groups()
        token = d.get('lease_token')
        lease = s.one("SELECT * FROM agent_leases WHERE experience_id=? AND actor=? AND state='running' AND lease_until>?",(eid,p.actor,time.time()))
        if not isinstance(token,str) or not lease or not hmac.compare_digest(lease['token_hash'],hashlib.sha256(token.encode()).hexdigest()):
            raise ApiFailure(409,'Agent lease is invalid, expired or owned by another identity')
        experience = s.one('SELECT * FROM experiences WHERE id=?',(eid,))
        from .runner import pending_task
        try:
            pending_task(s,eid)
        except ValueError as e:
            raise ApiFailure(409,str(e)) from None
        project = w.project(experience['project_id'])
        task = json.loads(lease['task'])
        first = w.latest_review(project['id'],1)
        if project['head_sha'] != experience['sha'] or not w.passed(project,1) or not first or first['id'] != task.get('review_id'):
            raise ApiFailure(409,'Task evaluation has become stale; reviewer action required')
        if operation == 'heartbeat':
            until = time.time() + self.lease_seconds
            s.conn.execute('UPDATE agent_leases SET lease_until=? WHERE experience_id=?',(until,eid))
            return {'id':eid,'lease_until':until}
        if operation == 'artifacts':
            return ArtifactStore(s).upload(eid, task['run_id'], p.actor, d)
        if d.get('sha') != experience['sha'] or not isinstance(d.get('result'),dict):
            raise ApiFailure(400,'Result SHA and result object are required')
        from .runner import validate_result
        validate_result(task,d,task_sha256=lease['task_sha256'])
        ArtifactStore(s).link_result(eid, task['run_id'], d['result'].get('artifacts'))
        data = {'id':eid,'sha':d['sha'],'result':dict(d['result'])}
        data['result']['executor'] = p.actor
        result = w.apply('experience-result',data,p.actor)
        s.conn.execute("UPDATE agent_leases SET state='completed' WHERE experience_id=?",(eid,))
        return result


def openapi():
    paths = {}
    for path in ('/api/me','/api/ui-config','/api/criteria','/api/accounts','/api/status','/api/metrics','/api/inbox','/api/projects','/api/projects/{id}','/api/projects/{id}/rankings','/api/projects/{id}/ai-assessments','/api/projects/{id}/contacts/summary','/api/discovery-runs','/api/discovery-runs/{id}','/api/developers','/api/developers/{id}','/api/developers/{id}/records/{table}','/api/records/{table}','/api/records/{table}/{id}',
                 '/api/experiences/{id}/artifacts','/api/artifacts/{id}/download'):
        paths[path] = {'get':{'responses':{'200':{'description':'JSON resource'},'401':{'description':'Unauthorized'},'403':{'description':'Forbidden'}}}}
    from .criteria import CATEGORIES
    from .projects import STAGES
    def query_parameter(name, schema, description=''):
        return {'in':'query','name':name,'schema':schema,'description':description}
    developer_paging=[query_parameter('page',{'type':'integer','minimum':1,'maximum':100000,'default':1}),
                      query_parameter('page_size',{'type':'integer','minimum':1,'maximum':200,'default':15})]
    paths['/api/developers']['get'].update({'description':'GitHub users and organizations aggregated by stable ID, without inferred scores. Public profile fields only; no email, contact preference or private record counts. Relationship filters match at least one linked project; one account may have both candidate and confirmed relationships.',
        'parameters':developer_paging+[
            query_parameter('q',{'type':'string','maxLength':200},'Literal login, public display name or linked repository search.'),
            query_parameter('kind',{'type':'string','enum':['User','Organization']}),
            query_parameter('relationship',{'type':'string','enum':['candidate','confirmed']})]})
    paths['/api/developers/{id}']['get'].update({'description':'Public profile, paginated linked repositories and relationship status. history_kinds follows existing record permissions. Contact preference is operator-only; latest_interview is operator/approver-only and retains original source, actor and time. Profile describes that interview, not inferred or permanent traits.',
        'parameters':list(developer_paging)})
    paths['/api/developers/{id}/records/{table}']['get'].update({'description':'Paginated source record references for this exact GitHub ID, newest first; contacts, interviews, grants and followups retain their existing roles, including counts. No raw contact drafts, notes or funding amounts. Read /api/records/{table}/{id} for the authorized source.',
        'parameters':list(developer_paging)})
    paths['/api/projects']['get'].update({
        'description':'Read-only project page. Search discovery is not selection. Filters use current qualification and source-bound translations; stale translations are not searchable. Unknown or repeated parameters are rejected.',
        'parameters':[
            query_parameter('page',{'type':'integer','minimum':1,'maximum':100000,'default':1}),
            query_parameter('page_size',{'type':'integer','minimum':1,'maximum':200,'default':50}),
            query_parameter('q',{'type':'string','maxLength':200},'Repository name, original description, or current Chinese/English description.'),
            query_parameter('stage',{'type':'string','enum':list(STAGES)}),
            query_parameter('category',{'type':'string','enum':list(CATEGORIES)},'Most recent assessed category; combine with selection to require a current assessment.'),
            query_parameter('selection',{'type':'string','enum':['unassessed','include','hold','exclude','stale']}),
            query_parameter('audience',{'type':'string','enum':['unknown','human','agent','both']}),
            query_parameter('maintainer_id',{'type':'integer','minimum':1,'maximum':999999999999999999},'Stable GitHub entity ID; includes candidate and confirmed relationships.'),
             query_parameter('contact_class',{'type':'string','enum':['with_clue','without_clue','do_not_contact']}),
             query_parameter('source',{'type':'string','maxLength':120}),
             query_parameter('sort',{'type':'string','enum':['updated','name','stars','priority'],'default':'updated'})]})
    paths['/api/projects/{id}']['get']['description'] = (
        'Repository, latest snapshot, latest 100 reviews, current selection assessment, source-bound zh-CN/en dossier and read-only readiness. '
        'technical_evidence presents up to six passive file previews, twenty commits, ten releases and fifteen issues/PRs, with capture gaps and source links; it creates no assessment. '
        'Use paginated records/reviews and records/qualifications for full history. '
        'readiness.allowed describes business gates; combine it with ui-config role capabilities. '
        'Existing provider writes require reconciliation even when evidence expires; issued grants require follow-up. '
        'next_record and next_review_at are filtered by record permissions. followup_grant_id is only returned to members who can schedule follow-up.')
    paths['/api/records/{table}']['get'].update({
        'description':'Role-authorized immutable/business record history, newest first. Reviews and qualifications are readable by all human workflow roles; financial and contact records retain their own permissions.',
        'parameters':[
            query_parameter('page',{'type':'integer','minimum':1,'maximum':100000,'default':1}),
            query_parameter('page_size',{'type':'integer','minimum':1,'maximum':200,'default':50}),
            query_parameter('project_id',{'type':'integer','minimum':1},'Supported for reviews, qualifications, experiences, contacts, interviews, decisions, grants, followups and project events.'),
            query_parameter('status',{'type':'string'},'Record status, or decision outcome. Not supported on reviews/qualifications/interviews/events.')]})
    from .inbox import KIND_ROLES
    paths['/api/inbox']['get'].update({
        'description': 'Role-filtered actionable inbox from the same eligibility snapshot as /api/status. Counts include only visible categories; item href is a fixed local hash route. No workflow mutation.',
        'parameters': [
            {'in': 'query', 'name': 'kind', 'schema': {'type': 'string', 'enum': list(KIND_ROLES), 'default': 'pending_experiences'}},
            {'in': 'query', 'name': 'page', 'schema': {'type': 'integer', 'minimum': 1, 'maximum': 100000, 'default': 1}},
            {'in': 'query', 'name': 'page_size', 'schema': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 15}}],
        'responses': {
            '200': {'description': 'Inbox page', 'content': {'application/json': {'schema': {
                'type': 'object', 'required': ['items', 'counts', 'kind', 'page', 'page_size', 'total'],
                'properties': {'items': {'type': 'array', 'items': {'type': 'object',
                    'required': ['id', 'project_id', 'title', 'reason', 'title_en', 'reason_en', 'href'],
                    'properties': {'id': {'type': ['integer', 'string']}, 'project_id': {'type': ['integer', 'null']},
                                   'title': {'type': 'string'}, 'reason': {'type': 'string'},
                                   'title_en': {'type': 'string'}, 'reason_en': {'type': 'string'},
                                   'href': {'type': 'string', 'pattern': '^#(?:projects/[1-9][0-9]*(?:/(?:selection|reviews))?|experiences|followups|operations)$'}}}},
                    'counts': {'type': 'object', 'additionalProperties': {'type': 'integer', 'minimum': 0}},
                    'kind': {'type': 'string', 'enum': list(KIND_ROLES)}, 'page': {'type': 'integer'},
                    'page_size': {'type': 'integer'}, 'total': {'type': 'integer', 'minimum': 0}}}}}},
            '400': {'description': 'Unknown category, repeated/blank query parameter or invalid pagination'},
            '401': {'description': 'Authentication required'}, '403': {'description': 'Category is not visible to this role'}}})
    for path in ('/api/accounts/{id}','/api/projects/{id}/translations','/api/actions/{action}','/api/import','/api/contact-leads/import','/api/projects/{id}/contacts/discover','/api/projects/{id}/contacts/rescan','/api/projects/{id}/ai-assess','/api/schedule','/api/discovery-runs','/api/referrals','/api/community/memberships','/api/jobs/{id}/retry',
                 '/api/agent/claim','/api/agent/tasks/{id}/result','/api/agent/tasks/{id}/heartbeat','/api/agent/tasks/{id}/artifacts',
                 '/api/credits/{id}/plan','/api/credits/{id}/submit','/api/credits/{id}/reconcile'):
        paths[path] = {'post':{'parameters':[{'in':'header','name':'Idempotency-Key','required':True,'schema':{'type':'string'}}],
                             'requestBody':{'required':True,'content':{'application/json':{'schema':{'type':'object'}}}},
                             'responses':{'200':{'description':'JSON outcome'},'409':{'description':'Conflict or pending reconciliation'},'403':{'description':'Forbidden'}}}}
    for path, operations in paths.items():
        for operation in operations.values():
            for name in re.findall(r'\{([^}]+)\}', path):
                operation.setdefault('parameters', []).append(
                    {'in':'path','name':name,'required':True,'schema':{'type':'string'}})
    action_operation=paths['/api/actions/{action}']['post']
    action_operation['description'] = (
        'Audited workflow mutation. followup-schedule requires operator/reviewer, grant_id and a future due_at (YYYY-MM-DD); '
        'the grant must be issued. An existing pending date is reused, a finalized date rejected. '
        'It does not create invitations or issue Credits. New approvals require at least one future review date.')
    next(p for p in action_operation['parameters'] if p['name']=='action')['schema']['enum']=list(ACTION_ROLES)
    for path in ('/api/records/{table}','/api/records/{table}/{id}'):
        next(p for p in paths[path]['get']['parameters'] if p['name']=='table')['schema']['enum']=list(RECORDS)
    from .developers import HISTORY
    next(p for p in paths['/api/developers/{id}/records/{table}']['get']['parameters'] if p['name']=='table')['schema']['enum']=list(HISTORY)
    paths['/api/artifacts/{id}/download']['get']['responses']['200'] = {
        'description': 'Authorized immutable evidence download; attachment, nosniff, and SHA-256 response header',
        'content': {'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary'}}}}
    paths['/api/agent/tasks/{id}/artifacts']['post']['description'] = (
        'Upload canonical relative-path evidence for the current Agent lease/run. Zero-based fixed 384 KiB chunks, '
        'up to 64 MiB and 100 reserved files per experience across runs; completion verifies total size and SHA-256. '
        'Required JSON fields: lease_token, run_id, path, sha256, size, chunk_index, total_chunks, data_base64. '
        'A successful Agent result may reference only completed files from the same run.')
    for path in ('/auth/me', '/auth/csrf'):
        paths[path] = {'get': {'security': [{'browserSession': []}], 'responses': {'200': {'description': 'Current session'}, '401': {'description': 'No active session'}}}}
    paths['/auth/login'] = {'post': {'security': [], 'description': 'Exchange an existing bearer credential for an HttpOnly session. Exact same-origin Origin header required.',
        'requestBody': {'required': True, 'content': {'application/json': {'schema': {'type': 'object', 'required': ['token'], 'additionalProperties': False,
        'properties': {'token': {'type': 'string', 'writeOnly': True}}}}}}, 'responses': {'200': {'description': 'Session identity, CSRF token and expiry; Set-Cookie rotates the session'}, '401': {'description': 'Invalid credential'}}}}
    paths['/auth/logout'] = {'post': {'security': [{'browserSession': []}], 'description': 'Requires exact same-origin Origin and X-CSRF-Token; invalidates the session.',
        'responses': {'200': {'description': 'Session revoked'}, '403': {'description': 'Origin or CSRF failure'}}}}
    paths['/auth/options'] = {'get': {'security': [], 'responses': {'200': {'description': 'Public sign-in methods; no secrets or provider tokens'}}}}
    paths['/auth/sso/login'] = {'get': {'security': [], 'description': 'Optional configured OIDC Authorization Code + S256 PKCE sign-in; creates a one-use 300-second HttpOnly transaction cookie.',
        'responses': {'302': {'description': 'Redirect to the verified issuer authorization endpoint'}, '404': {'description': 'OIDC not configured'}}}}
    paths['/auth/sso/callback'] = {'get': {'security': [], 'description': 'Validates state and browser transaction cookie, exchanges code, verifies RS256 ID Token claims, and maps fixed issuer subject to an existing local human actor.',
        'parameters': [{'in': 'query', 'name': 'state', 'required': True, 'schema': {'type': 'string'}}, {'in': 'query', 'name': 'code', 'schema': {'type': 'string'}}],
        'responses': {'303': {'description': 'Creates Strict application session and redirects only to /'}, '401': {'description': 'Transaction or ID token invalid'}, '403': {'description': 'Subject not mapped'}}}}
    paths['/auth/options'] = {'get': {'security': [], 'responses': {'200': {'description': 'Sign-in methods; public registration and administrator setup are disabled'}}}}
    for path in ('/auth/password-login',):
        properties = {'username': {'type':'string','minLength':3,'maxLength':64}, 'password': {'type':'string','format':'password','maxLength':128}}
        paths[path] = {'post': {'security': [], 'description': 'Sign in with an active account; exact Origin required. A fresh installation seeds OpenDev once. Members are created by administrators.',
            'requestBody': {'required':True,'content':{'application/json':{'schema':{'type':'object','additionalProperties':False,'required':list(properties),'properties':properties}}}},
            'responses': {'200':{'description':'Authenticated session'},'400':{'description':'Invalid fields'},'401':{'description':'Invalid or unauthorized account'},'403':{'description':'Untrusted origin'},'429':{'description':'Rate limited'}}}}
    paths['/auth/password'] = {'post': {'security':[{'browserSession':[]}], 'description':'Current and new passwords required; exact Origin and CSRF required. Revokes all sessions.',
        'requestBody':{'required':True,'content':{'application/json':{'schema':{'type':'object','additionalProperties':False,'required':['current_password','new_password'],'properties':{'current_password':{'type':'string','format':'password'},'new_password':{'type':'string','format':'password','minLength':6,'maxLength':128}}}}}},
        'responses':{'200':{'description':'Password changed; sign in again'},'403':{'description':'Invalid current password or CSRF'}}}}
    paths['/api/accounts']['get']['description'] = 'Administrator-only paginated accounts; password hashes never returned.'
    paths['/api/accounts']['post'] = {
        'description': 'Administrator-only member creation. Activates the account with selected human roles. Browser writes require exact Origin and CSRF.',
        'parameters': [{'in':'header','name':'Idempotency-Key','required':True,'schema':{'type':'string'}}],
        'requestBody': {'required':True,'content':{'application/json':{'schema':{
            'type':'object','additionalProperties':False,'required':['username','display_name','password','roles'],
            'properties':{'username':{'type':'string','minLength':3,'maxLength':64},'display_name':{'type':'string','minLength':1,'maxLength':80},
                'password':{'type':'string','format':'password','writeOnly':True,'minLength':6,'maxLength':128},
                'roles':{'type':'array','minItems':1,'uniqueItems':True,'items':{'type':'string','enum':['admin','reviewer','operator','approver','disburser','auditor']}}}}}}},
        'responses': {'200':{'description':'Created account without password hash'},'400':{'description':'Invalid fields'},'401':{'description':'Unauthenticated'},'403':{'description':'Administrator role or CSRF required'},'409':{'description':'Username unavailable or idempotency conflict'}}}
    paths['/api/accounts/{id}']['post']['description'] = 'Administrator-only approval, role assignment, disable/reject and optional password reset. Requires status, roles, current auth_version. Retains the last active admin and invalidates prior sessions.'
    from . import __version__
    return {'openapi':'3.1.0','info':{'title':'OpenDev Elite API','version':__version__},
            'security':[{'bearer':[]}, {'browserSession': []}], 'components':{'securitySchemes':{
                'bearer':{'type':'http','scheme':'bearer'}, 'browserSession': {'type': 'apiKey', 'in': 'cookie', 'name': 'sponsorship_session',
                'description': 'HttpOnly SameSite=Strict; named __Host-sponsorship_session over HTTPS. Browser writes also require exact Origin and X-CSRF-Token.'}}},'paths':paths}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32
    allow_reuse_port = False
    allow_reuse_address = sys.platform != 'win32'
    def server_bind(self):
        # Windows SO_REUSEADDR may allow a second app to share a live listener.
        # A test/demo deployment must never intercept production traffic.
        if sys.platform == 'win32':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()
    def __init__(self,address,application):
        self.application = application
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address,Handler)

    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self,request,client_address):
        try:
            super().process_request_thread(request,client_address)
        finally:
            self.slots.release()

    def handle_error(self,request,client_address):
        error = sys.exc_info()[1]
        if isinstance(error,(ssl.SSLError,TimeoutError,ConnectionError)):
            return
        LOG.error('Connection worker failed',exc_info=True)


class Handler(BaseHTTPRequestHandler):
    server_version = 'SponsorshipAPI'
    sys_version = ''

    def setup(self):
        self.request.settimeout(15)
        if isinstance(self.request,ssl.SSLSocket):
            self.request.do_handshake()
        super().setup()

    def log_message(self,format,*args):
        # No headers, tokens, body or URL query strings in access logs.
        pass

    def respond(self,status,value,headers=None,content_type='application/json; charset=utf-8'):
        download = isinstance(value, ArtifactDownload)
        encoded = b'' if download else value if isinstance(value, bytes) else dump(value).encode('utf-8')
        if download:
            content_type = 'application/octet-stream'
            headers = dict(headers or {}, **{'Content-Disposition': value.content_disposition,
                                             'X-Artifact-SHA256': value.metadata['sha256']})
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(value.size if download else len(encoded)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Connection','close')
        for key, header_value in (headers or {}).items():
            for item in header_value if isinstance(header_value, list) else [header_value]:
                self.send_header(key, item)
        if status == 401:
            self.send_header('WWW-Authenticate','Bearer')
        self.end_headers()
        if download:
            for chunk in value.chunks():
                self.wfile.write(chunk)
        else:
            self.wfile.write(encoded)
        self.close_connection = True

    def dispatch(self):
        request_id = secrets.token_hex(8)
        response_headers, content_type = {}, 'application/json; charset=utf-8'
        try:
            for header in ('Host', 'Origin', 'Authorization', 'Idempotency-Key', 'Cookie', 'X-CSRF-Token'):
                if len(self.headers.get_all(header, [])) > 1:
                    raise ApiFailure(400, 'Duplicate security header: ' + header)
            policy = BrowserPolicy(self.server.application.config.get('public_origin'), self.server.server_port,
                                   secure=isinstance(self.request, ssl.SSLSocket))
            path = urlsplit(self.path).path
            callback = self.command == 'GET' and path == '/auth/sso/callback'
            body, raw_body = None, None
            if self.command == 'POST':
                if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[])) != 1:
                    raise ApiFailure(400,'Exactly one Content-Length is required; chunked requests are unsupported')
                try:
                    length = int(self.headers['Content-Length'])
                except ValueError:
                    raise ApiFailure(400,'Invalid Content-Length') from None
                if not 0 < length <= 1024 * 1024:
                    raise ApiFailure(413,'Body must be between 1 byte and 1 MiB')
                # Drain a valid bounded frame before rejecting Origin/Host. Closing
                # with unread POST bytes can reset TCP on Windows and discard 403.
                # No JSON parsing, authentication or business action occurs yet.
                raw_body = self.rfile.read(length)
                if len(raw_body) != length:
                    raise ApiFailure(400, 'Incomplete request body')
            if callback:
                # Cross-site top-level GET is the protocol return channel; state +
                # the one-use Lax transaction cookie replace normal CSRF here.
                policy.check(self.headers.get('Host'))
                if self.headers.get('Sec-Fetch-Mode') not in (None, 'navigate'):
                    raise ApiFailure(403, 'OIDC callback requires top-level navigation')
            else:
                # A user may open the public UI from another site (for example by
                # clicking a shared deployment link). Allow that top-level document
                # navigation, while retaining the cross-site guard for API reads,
                # browser writes, and subresource requests. The session cookie is
                # SameSite=Strict, so this cannot bootstrap an authenticated session.
                fetch_site = self.headers.get('Sec-Fetch-Site')
                top_level_ui_navigation = (
                    self.command == 'GET'
                    and path == '/'
                    and self.headers.get('Sec-Fetch-Mode') == 'navigate'
                    and self.headers.get('Sec-Fetch-Dest') == 'document'
                )
                if top_level_ui_navigation and fetch_site == 'cross-site':
                    fetch_site = None
                policy.check(self.headers.get('Host'), self.headers.get('Origin'), fetch_site)
            if raw_body is not None:
                if self.headers.get_content_type() != 'application/json':
                    raise ApiFailure(415,'Content-Type must be application/json')
                def invalid_constant(_): raise ValueError('Non-finite JSON numbers are not supported')
                body = json.loads(raw_body.decode('utf-8'),parse_constant=invalid_constant)
            application = self.server.application
            path = urlsplit(self.path).path
            session_id = policy.cookie_id(self.headers.get('Cookie'))
            session = application.sessions.resolve(session_id)
            principal = None
            if path == '/favicon.ico' and self.command == 'GET':
                status, value = 204, b''
            elif path == '/auth/options' and self.command == 'GET':
                status, value = 200, application.auth_options(next(iter(policy.origins)))
            elif path in ('/auth/bootstrap', '/auth/register'):
                raise ApiFailure(404, '公开注册已关闭，请联系管理员创建成员账号。')
            elif path == '/auth/password-login' and self.command == 'POST':
                policy.check(self.headers.get('Host'), self.headers.get('Origin'), self.headers.get('Sec-Fetch-Site'), require_origin=True)
                if not isinstance(body, dict):
                    raise ApiFailure(400, '请输入有效的账号资料。')
                application.auth_throttle.check(self.client_address[0], str(body.get('username', '')))
                principal = application.accounts.authenticate(body)
                sid, logged_in = application.sessions.create(principal, previous=session_id)
                response_headers['Set-Cookie'] = policy.cookie(sid, application.sessions.ttl)
                status, value = 200, {key: item for key, item in logged_in.items() if key != 'principal'}
            elif path == '/auth/sso/login' and self.command == 'GET':
                if not application.oidc:
                    raise ApiFailure(404, 'OIDC is not configured')
                prior = policy.cookie_id(self.headers.get('Cookie'), application.oidc.cookie_name(policy))
                location, binding = application.oidc.begin(previous_session=session_id, previous_transaction=prior)
                response_headers.update({'Location': location, 'Set-Cookie': application.oidc.cookie(policy, binding)})
                status, value = 302, {'redirect': True}
            elif callback:
                if not application.oidc:
                    raise ApiFailure(404, 'OIDC is not configured')
                binding = policy.cookie_id(self.headers.get('Cookie'), application.oidc.cookie_name(policy))
                response_headers['Set-Cookie'] = application.oidc.cookie(policy, clear=True)
                principal, previous_digest = application.oidc.finish(parse_qs(urlsplit(self.path).query, keep_blank_values=True), binding)
                application.sessions.revoke_digest(previous_digest)
                sid, logged_in = application.sessions.create(principal, previous=session_id)
                response_headers['Set-Cookie'] = [application.oidc.cookie(policy, clear=True), policy.cookie(sid, application.sessions.ttl)]
                response_headers['Location'] = '/'
                status, value = 303, {'authenticated': True}
            elif path == '/auth/login' and self.command == 'POST':
                policy.check(self.headers.get('Host'), self.headers.get('Origin'), self.headers.get('Sec-Fetch-Site'), require_origin=True)
                application.auth_throttle.check(self.client_address[0])
                if not isinstance(body, dict) or set(body) != {'token'} or not isinstance(body['token'], str):
                    raise ApiFailure(400, 'Login requires only a token string')
                principal = application.identities.authenticate('Bearer ' + body['token'])
                if not principal:
                    raise ApiFailure(401, 'Invalid credentials')
                sid, logged_in = application.sessions.create(principal, previous=session_id)
                response_headers['Set-Cookie'] = policy.cookie(sid, application.sessions.ttl)
                status, value = 200, {key: item for key, item in logged_in.items() if key != 'principal'}
            elif path.startswith('/auth/'):
                if not session:
                    raise ApiFailure(401, 'Browser session required')
                if path in ('/auth/me', '/auth/csrf') and self.command == 'GET':
                    value = ({key: item for key, item in session.items() if key != 'principal'} if path == '/auth/me'
                             else {'csrf_token': session['csrf_token']})
                    status = 200
                elif path == '/auth/logout' and self.command == 'POST':
                    policy.check(self.headers.get('Host'), self.headers.get('Origin'), self.headers.get('Sec-Fetch-Site'), require_origin=True)
                    application.sessions.verify_csrf(session, self.headers.get('X-CSRF-Token'))
                    application.sessions.revoke(session_id)
                    response_headers['Set-Cookie'] = policy.cookie()
                    status, value = 200, {'logged_out': True}
                elif path == '/auth/password' and self.command == 'POST':
                    policy.check(self.headers.get('Host'), self.headers.get('Origin'), self.headers.get('Sec-Fetch-Site'), require_origin=True)
                    application.sessions.verify_csrf(session, self.headers.get('X-CSRF-Token'))
                    application.auth_throttle.check(self.client_address[0], session['actor'])
                    value = application.accounts.change_password(session['principal'], body)
                    application.sessions.revoke_actor(session['actor'])
                    response_headers['Set-Cookie'] = policy.cookie()
                    status = 200
                else:
                    raise ApiFailure(404, 'Unknown browser authentication endpoint')
            elif self.command == 'GET' and path in ('/', '/assets/app.js', '/assets/forms.js', '/assets/accounts.js', '/assets/app.css', '/assets/landing.css', '/assets/landing.js', '/assets/i18n.en.js', '/assets/i18n.js', '/assets/views.js', '/assets/developers.js', '/assets/workspace.css', '/assets/elite.css'):
                names = {'/': ('index.html', 'text/html; charset=utf-8'), '/assets/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                         '/assets/forms.js': ('forms.js', 'text/javascript; charset=utf-8'),
                         '/assets/accounts.js': ('accounts.js', 'text/javascript; charset=utf-8'),
                         '/assets/i18n.en.js': ('i18n.en.js', 'text/javascript; charset=utf-8'),
                         '/assets/i18n.js': ('i18n.js', 'text/javascript; charset=utf-8'),
                         '/assets/views.js': ('views.js', 'text/javascript; charset=utf-8'),
                         '/assets/developers.js': ('developers.js', 'text/javascript; charset=utf-8'),
                         '/assets/workspace.css': ('workspace.css', 'text/css; charset=utf-8'),
                         '/assets/elite.css': ('elite.css', 'text/css; charset=utf-8'),
                         '/assets/landing.js': ('landing.js', 'text/javascript; charset=utf-8'),
                         '/assets/landing.css': ('landing.css', 'text/css; charset=utf-8'),
                         '/assets/app.css': ('app.css', 'text/css; charset=utf-8')}
                filename, content_type = names[path]
                try:
                    value = resources.files('worker').joinpath('web', filename).read_bytes()
                except FileNotFoundError:
                    raise ApiFailure(404, 'Management UI is not installed') from None
                status = 200
            else:
                if session_id and self.headers.get('Authorization'):
                    raise ApiFailure(400, 'Do not combine browser session and Bearer authentication')
                if session:
                    principal = session['principal']
                    if self.command != 'GET':
                        policy.check(self.headers.get('Host'), self.headers.get('Origin'), self.headers.get('Sec-Fetch-Site'), require_origin=True)
                        application.sessions.verify_csrf(session, self.headers.get('X-CSRF-Token'))
                status,value = application.handle(self.command,self.path,self.headers.get('Authorization'),body,
                                                self.headers.get('Idempotency-Key'), principal=principal)
        except (ApiFailure, BrowserFailure) as e:
            status,value = e.status,{'error':e.message,'request_id':request_id}
        except (ValueError,TypeError,KeyError,UnicodeError) as e:
            status,value = 400,{'error':str(e),'request_id':request_id}
        except CreditsError:
            status,value = 502,{'error':'Credits provider operation needs inspection or reconciliation','request_id':request_id}
        except sqlite3.OperationalError:
            status,value = 503,{'error':'Database temporarily unavailable','request_id':request_id}
        except sqlite3.IntegrityError:
            status,value = 409,{'error':'Record conflicts with an existing business constraint','request_id':request_id}
        except Exception:
            LOG.exception('API failure request_id=%s',request_id)
            status,value = 500,{'error':'Internal service error','request_id':request_id}
        try:
            if status >= 400:
                content_type = 'application/json; charset=utf-8'
            self.respond(status,value,response_headers,content_type)
        except (BrokenPipeError,ConnectionError,TimeoutError):
            pass

    do_GET = dispatch
    do_POST = dispatch
    do_PUT = dispatch
    do_DELETE = dispatch
    do_PATCH = dispatch


def make_server(db_path,config,host='127.0.0.1',port=8765,credits_config=None,tls_cert=None,tls_key=None):
    # OpenDeploy terminates HTTPS at its ingress and forwards to the container.
    # Keep the safe default for direct deployments; only an explicit platform
    # proxy opt-in permits the container to bind on a non-loopback interface.
    proxy_terminated_tls = os.environ.get('OPENDEV_TRUST_PROXY') == '1'
    if host not in ('127.0.0.1','localhost') and not (tls_cert and tls_key) and not proxy_terminated_tls:
        raise ValueError('Non-loopback binding requires TLS certificate and key; otherwise use a local TLS reverse proxy')
    if bool(tls_cert) != bool(tls_key):
        raise ValueError('Both TLS certificate and key are required')
    server = Server((host,port),Application(db_path,config,credits_config))
    try:
        if tls_cert:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(tls_cert,tls_key)
            server.socket = context.wrap_socket(server.socket,server_side=True,do_handshake_on_connect=False)
        return server
    except BaseException:
        server.server_close()
        raise


def serve(db_path,config,host='127.0.0.1',port=8765,credits_config=None,tls_cert=None,tls_key=None):
    server = make_server(db_path,config,host,port,credits_config,tls_cert,tls_key)
    try:
        LOG.warning('Sponsorship API listening on %s:%s',host,server.server_port)
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
