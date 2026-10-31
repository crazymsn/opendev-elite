"""Adapter verified against openai-next-credits commit 057b78a1.

Uses only PAT-enabled endpoints. Creating an invitation is NOT issuance.
No email, claim-link, activation, API-key retrieval or quota overwrite calls.
"""
import datetime as dt
import decimal
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from .db import dump, now
from .workflow import Workflow, required, positive_amount, evidence_fields


class CreditsError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NextCredits:
    def __init__(self, store, config, transport=None):
        self.s, self.config = store, config
        self.base = config.get('base_url', '').rstrip('/')
        self.transport = transport or urllib.request.build_opener(NoRedirect()).open

    def request(self, method, path, body=None):
        parsed = urllib.parse.urlparse(self.base)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise CreditsError('Configure an HTTPS API base URL without credentials/query/fragment')
        token = os.environ.get('NEXT_CREDITS_PAT', '')
        if not re.fullmatch(r'onc_pat_[A-Za-z0-9_-]{43}', token):
            raise CreditsError('Configure NEXT_CREDITS_PAT in the process environment')
        request = urllib.request.Request(self.base + path,
                  data=dump(body).encode() if body is not None else None,
                  headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                           'Accept': 'application/json'}, method=method)
        try:
            with self.transport(request, timeout=30) as response:
                return json.loads(response.read(4 * 1024 * 1024).decode())
        except urllib.error.HTTPError as e:
            raise CreditsError(f'Credits HTTP {e.code}; inspect provider audit log before retrying writes') from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise CreditsError('Credits response unavailable or invalid; write outcome may be unknown') from None

    def plan(self, grant_id, require_active=True):
        g = self.s.one('SELECT * FROM grants WHERE id=?', (grant_id,))
        if not g:
            raise ValueError('Grant not found')
        previous = json.loads(g['receipt']) if g['receipt'] else {}
        if previous.get('provider_base') and previous['provider_base'] != self.base:
            raise ValueError('Credits provider address changed; reconcile against the originally bound service')
        d = self.s.one('SELECT * FROM decisions WHERE id=?', (g['decision_id'],))
        if d['outcome'] != 'approve':
            raise ValueError('Approved decision required')
        latest = self.s.one('SELECT id FROM decisions WHERE project_id=? ORDER BY rowid DESC LIMIT 1', (d['project_id'],))
        if require_active and latest['id'] != d['id']:
            raise ValueError('This approval has been superseded; no new provider writes allowed')
        terms = json.loads(d['terms'])
        required(self.config, 'event_slug', 'event_end_date', 'event_config_evidence')
        required(terms, 'event_slug', 'identity_evidence')
        evidence_fields(self.config, 'event_config_evidence')
        evidence_fields(terms, 'identity_evidence')
        slug = self.config['event_slug']
        if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9-]{2,64}', slug) or terms['event_slug'] != slug:
            raise ValueError('Approved event slug must match configured event')
        if previous.get('event_slug') and previous['event_slug'] != slug:
            raise ValueError('Credits event changed; reconcile against the originally bound event')
        amount = positive_amount(terms['amount'])
        if terms['unit'] != 'cents' or not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value() or amount > 2147483647:
            raise ValueError('Next Credits requires a positive 32-bit integer amount in cents')
        email = str(terms['account']).strip().lower()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise ValueError('Account must be the author-confirmed receiving email')
        if terms['expires_at'] != self.config['event_end_date']:
            raise ValueError('Credits expiry is event-level; approved date must match verified event date')
        if require_active and dt.date.fromisoformat(terms['expires_at']) <= dt.datetime.now(dt.timezone.utc).date():
            raise ValueError('Sponsorship expired')
        marker = 'sponsorship:' + g['id']
        return {'grant_id': g['id'], 'project_id': d['project_id'], 'state': g['status'],
                'method': 'POST', 'path': f'/api/v1/events/{slug}/invitations',
                'body': {'email': email, 'grantCents': int(amount), 'note': marker},
                'marker': marker, 'event_slug': slug, 'expires_at': terms['expires_at'],
                'meaning': 'Create pending invitation; issuance requires activated participant',
                'sends_email': False}

    def list_items(self, slug, resource, email):
        items = []
        for page in range(1, 101):
            query = urllib.parse.urlencode({'q': email, 'page': page, 'pageSize': 100})
            response = self.request('GET', f'/api/v1/events/{slug}/{resource}?{query}')
            if not isinstance(response, dict) or not isinstance(response.get('items'), list) or type(response.get('total')) is not int or response['total'] < 0 or any(not isinstance(item, dict) for item in response['items']):
                raise CreditsError('Unexpected list response')
            items.extend(response['items'])
            if page * 100 >= response['total']:
                return items
        raise CreditsError('Result too large; no writes performed')

    def invitation(self, plan):
        email = plan['body']['email']
        rows = [r for r in self.list_items(plan['event_slug'], 'invitations', email)
                if str(r.get('email') or '').strip().lower() == email]
        if len(rows) > 1:
            raise CreditsError('Ambiguous invitations')
        if rows and (rows[0].get('note') != plan['marker'] or rows[0].get('grantCents') != plan['body']['grantCents']):
            raise CreditsError('Existing invitation belongs to another approval or has changed; do not overwrite')
        if rows:
            required(rows[0], 'id', 'email', 'grantCents', 'note')
            bound = self.s.one('SELECT receipt FROM grants WHERE id=?', (plan['grant_id'],))
            receipt = json.loads(bound['receipt']) if bound['receipt'] else {}
            if receipt.get('invitation_id') and receipt['invitation_id'] != rows[0]['id']:
                raise CreditsError('Provider invitation identity changed; manual investigation required')
        return rows[0] if rows else None

    def check_contract(self):
        document = self.request('GET', '/openapi/v1.json')
        expected = {'/api/v1/events/{slug}/invitations': ('get','post'),
                    '/api/v1/events/{slug}/participants': ('get',)}
        for path, methods in expected.items():
            for method in methods:
                if method not in document.get('paths', {}).get(path, {}):
                    raise CreditsError('Live OpenAPI lacks required PAT endpoint: ' + method + ' ' + path)
        return {'compatible_paths': True}

    def submit(self, grant_id, actor, execute=False):
        plan = self.plan(grant_id)
        if not execute:
            return dict(plan, dry_run=True)
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError('Accountable actor required')
        # Claim before network I/O. A crash stays unknown/submitting; no blind resubmission.
        with self.s.transaction():
            # Re-read approval under the lock: it can be revoked after dry planning.
            plan = self.plan(grant_id)
            Workflow(self.s).contact_gate(plan['project_id'])
            decision = self.s.one('SELECT d.* FROM decisions d JOIN grants g ON g.decision_id=d.id WHERE g.id=?', (grant_id,))
            Workflow(self.s).sponsorship_consent(decision)
            if decision['actor'] == actor:
                raise ValueError('Approval and Credits execution require different accountable actors')
            row = self.s.one('SELECT status FROM grants WHERE id=?', (grant_id,))
            if row['status'] not in ('pending','failed'):
                raise ValueError('Only pending/confirmed-failed grants can submit; reconcile all other states')
            claim = dump({'provider_base': self.base, 'event_slug': plan['event_slug']})
            self.s.conn.execute("UPDATE grants SET status='submitting',receipt=?,updated_at=? WHERE id=?",
                                (claim, now(), grant_id))
            self.s.event('grant', grant_id, 'credits_submit_started', actor, {'event_slug': plan['event_slug']})
        try:
            self.check_contract()
            invitation = self.invitation(plan)
            if invitation is None:
                participants = self.list_items(plan['event_slug'], 'participants', plan['body']['email'])
                if any(str((p.get('user') or {}).get('email') or '').strip().lower() == plan['body']['email'] for p in participants):
                    raise CreditsError('Account already participates in this event; top-up requires a separate reviewed path')
                invitation = self.request('POST', plan['path'], plan['body'])
            required(invitation, 'id', 'email', 'grantCents', 'note')
            if invitation['email'].strip().lower() != plan['body']['email'] or invitation['grantCents'] != plan['body']['grantCents'] or invitation['note'] != plan['marker']:
                raise CreditsError('Invitation response does not match approval')
            with self.s.transaction():
                changed = self.s.conn.execute("UPDATE grants SET status='awaiting_claim',receipt=?,updated_at=? WHERE id=? AND status='submitting' AND receipt=?",
                                    (dump({'invitation_id': invitation['id'], 'event_slug': plan['event_slug'], 'provider_base': self.base}), now(), grant_id, claim))
                if changed.rowcount != 1:
                    raise CreditsError('Grant state changed during submission; reconcile without overwriting it')
                self.s.event('grant', grant_id, 'credits_invitation_created', actor, {'invitation_id': invitation['id']})
            return {'id': grant_id, 'status': 'awaiting_claim', 'invitation_id': invitation['id'], 'message_sent': False}
        except Exception:
            with self.s.transaction():
                self.s.conn.execute("UPDATE grants SET status='unknown',updated_at=? WHERE id=? AND status='submitting' AND receipt=?", (now(), grant_id, claim))
                self.s.event('grant', grant_id, 'credits_requires_reconciliation', actor, {})
            raise

    def reconcile(self, grant_id, actor):
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError('Accountable actor required')
        plan = self.plan(grant_id, require_active=False)
        initial = self.s.one('SELECT status,receipt FROM grants WHERE id=?', (grant_id,))
        if initial['status'] == 'cancelled':
            raise CreditsError('Cancelled grant requires manual investigation')
        invitation = self.invitation(plan)
        if not invitation:
            return {'id': grant_id, 'status': 'not_found', 'action': 'Check provider audit log; do not automatically recreate'}
        participant = invitation.get('participant')
        if not invitation.get('consumedAt') or not participant or not participant.get('activatedAt'):
            with self.s.transaction():
                current = self.s.one('SELECT status FROM grants WHERE id=?', (grant_id,))
                if current['status'] in ('issued','closed','cancelled'):
                    raise CreditsError('Provider state regressed; manual investigation required')
                changed = self.s.conn.execute("UPDATE grants SET status='awaiting_claim',receipt=?,updated_at=? WHERE id=? AND status=? AND receipt IS ?",
                                    (dump({'invitation_id': invitation['id'], 'event_slug': plan['event_slug'], 'provider_base': self.base}), now(), grant_id, initial['status'], initial['receipt']))
                if changed.rowcount != 1:
                    raise CreditsError('Grant state changed during reconciliation; retry the read')
                self.s.event('grant', grant_id, 'credits_pending_claim', actor, {'invitation_id': invitation['id']})
            return {'id': grant_id, 'status': 'awaiting_claim'}
        matches = [p for p in self.list_items(plan['event_slug'], 'participants', plan['body']['email'])
                   if p.get('id') == participant['id'] and p.get('invitationId') == invitation['id']]
        if len(matches) != 1:
            raise CreditsError('Activated participant could not be verified')
        p = matches[0]
        if p.get('bannedAt') or not p.get('activatedAt') or p.get('grantCents') != plan['body']['grantCents'] or str(p.get('user', {}).get('email', '')).strip().lower() != plan['body']['email']:
            raise CreditsError('Participant is banned or account/quota does not match approval')
        receipt = {'transaction_id': 'next-credits:' + plan['event_slug'] + ':' + invitation['id'],
                   'account': plan['body']['email'], 'amount': p['grantCents'], 'unit': 'cents',
                   'evidence': self.base + '/api/v1/events/' + plan['event_slug'] + '/participants',
                   'invitation_id': invitation['id'], 'participant_id': p['id'], 'provider_base': self.base, 'event_slug': plan['event_slug'],
                   'activated_at': p['activatedAt'], 'settled_cents': p.get('settledCents'), 'observed_at': now()}
        with self.s.transaction():
            current = self.s.one('SELECT status,receipt FROM grants WHERE id=?', (grant_id,))
            if current['status'] not in ('issued','closed'):
                Workflow(self.s).apply('grant-reconcile', {'id': grant_id, 'status': 'issued', 'receipt': receipt}, actor)
            else:
                stored = json.loads(current['receipt'])
                for key in ('transaction_id', 'provider_base', 'event_slug', 'invitation_id', 'participant_id'):
                    if stored.get(key) != receipt.get(key):
                        raise CreditsError('Finalized receipt identity changed; manual investigation required')
            self.s.event('grant', grant_id, 'credits_usage_observed', actor,
                         {'settled_cents': p.get('settledCents'), 'grant_cents': p['grantCents'],
                          'scope': 'Settled usage only; excludes current active-key consumption'})
        return {'id': grant_id, 'status': current['status'] if current['status'] == 'closed' else 'issued', 'receipt': receipt}
