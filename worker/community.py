"""Audited referral and project-scoped community permissions."""
import datetime as dt
import hashlib
import re
import secrets
import uuid
from .db import dump, now


def _expires_at(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('expires_at must be an ISO-8601 UTC timestamp')
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('expires_at must be an ISO-8601 UTC timestamp') from None
    if parsed.tzinfo is None:
        raise ValueError('expires_at must include a timezone')
    parsed = parsed.astimezone(dt.timezone.utc)
    if parsed <= dt.datetime.now(dt.timezone.utc):
        raise ValueError('expires_at must be in the future')
    return parsed.isoformat().replace('+00:00', 'Z')


def _token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_referral_link(store, actor, scope_project_id=None, expires_at=None):
    if scope_project_id is not None and not store.one('SELECT id FROM projects WHERE id=?', (scope_project_id,)):
        raise ValueError('Referral project scope does not exist')
    expires_at = _expires_at(expires_at)
    token = 'ref_' + secrets.token_urlsafe(32)
    identifier = 'rfl_' + uuid.uuid4().hex
    store.conn.execute('''INSERT INTO referral_links
        (id,creator_actor,token_hash,scope_project_id,status,expires_at,created_at)
        VALUES(?,?,?,?,?,?,?)''',
        (identifier, actor, _token_hash(token), scope_project_id, 'active', expires_at, now()))
    store.event('referral_link', identifier, 'created', actor, {'scope_project_id': scope_project_id, 'expires_at': expires_at})
    return {'id': identifier, 'token': token, 'scope_project_id': scope_project_id, 'expires_at': expires_at}


def submit_referral(store, token, project_id, submitter, evidence):
    if not isinstance(token, str) or not token.startswith('ref_'):
        raise ValueError('Invalid referral token')
    link = store.one('SELECT * FROM referral_links WHERE token_hash=? AND status=\'active\'', (_token_hash(token),))
    if not link:
        raise ValueError('Referral link is invalid or revoked')
    if link['expires_at']:
        try:
            expiry = dt.datetime.fromisoformat(link['expires_at'].replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('Referral link has an invalid expiry') from None
        if expiry <= dt.datetime.now(dt.timezone.utc):
            raise ValueError('Referral link has expired')
    if link['creator_actor'] == submitter:
        raise ValueError('Referral creator cannot submit their own referral')
    if link['scope_project_id'] is not None and int(link['scope_project_id']) != int(project_id):
        raise ValueError('Referral link is scoped to another project')
    if not store.one('SELECT id FROM projects WHERE id=?', (project_id,)):
        raise ValueError('Project not found')
    identifier = 'rfr_' + uuid.uuid4().hex
    try:
        store.conn.execute('INSERT INTO referrals(id,link_id,project_id,submitter,evidence,created_at) VALUES(?,?,?,?,?,?)',
                           (identifier, link['id'], project_id, submitter, dump(evidence), now()))
    except Exception as exc:
        if 'UNIQUE' not in str(exc).upper():
            raise
        existing = store.one('SELECT id,state FROM referrals WHERE link_id=? AND project_id=?', (link['id'], project_id))
        return {'id': existing['id'], 'state': existing['state'], 'duplicate': True}
    store.event('referral', identifier, 'submitted', submitter, {'project_id': project_id, 'link_id': link['id']})
    return {'id': identifier, 'state': 'submitted', 'duplicate': False}


def grant_sub_admin(store, maintainer_id, project_id, actor, expires_at=None):
    project = store.one("SELECT id,stage FROM projects WHERE id=?", (project_id,))
    if not project or project['stage'] != 'sponsored':
        raise ValueError('Sub-admin access requires a sponsored project')
    relationship = store.one('''SELECT confirmed FROM project_maintainers
        WHERE project_id=? AND maintainer_id=?''', (project_id, maintainer_id))
    if not relationship or relationship['confirmed'] != 1:
        raise ValueError('Maintainer is not a confirmed maintainer of this project')
    if store.one('SELECT do_not_contact FROM maintainers WHERE id=?', (maintainer_id,))['do_not_contact']:
        raise ValueError('Maintainer has declined contact')
    expires_at = _expires_at(expires_at)
    existing = store.one('''SELECT * FROM community_memberships
        WHERE maintainer_id=? AND project_id=? AND role='sub_admin' AND status='active' ''',
        (maintainer_id, project_id))
    if existing:
        return {'id': existing['id'], 'role': existing['role'], 'project_id': project_id,
                'expires_at': existing['expires_at'], 'duplicate': True}
    identifier = 'cm_' + uuid.uuid4().hex
    store.conn.execute('''INSERT INTO community_memberships
        (id,maintainer_id,project_id,role,status,granted_by,expires_at,created_at)
        VALUES(?,?,?,?,?,?,?,?)''',
        (identifier, maintainer_id, project_id, 'sub_admin', 'active', actor, expires_at, now()))
    store.event('community_membership', identifier, 'granted', actor, {'maintainer_id': maintainer_id, 'project_id': project_id})
    return {'id': identifier, 'role': 'sub_admin', 'project_id': project_id, 'expires_at': expires_at}
