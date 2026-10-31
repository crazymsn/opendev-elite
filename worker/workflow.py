"""Business gates. All mutations run in a single transaction with an audit event."""
import datetime as dt
import decimal
import json
import uuid
from .db import dump, now
from .criteria import CATEGORIES, POLICY_VERSION, QUALIFICATION_MESSAGES, qualification_status, validate_qualification_evidence


def required(data, *fields):
    if not isinstance(data, dict):
        raise ValueError('Expected an object')
    for field in fields:
        if field not in data or data[field] is None or (isinstance(data[field], str) and not data[field].strip()) or data[field] == [] or data[field] == {}:
            raise ValueError('Required field: ' + field)


def positive_amount(value):
    if isinstance(value, bool):
        raise ValueError('Invalid credit amount')
    try:
        amount = decimal.Decimal(str(value))
    except (decimal.InvalidOperation, ValueError):
        raise ValueError('Invalid credit amount') from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError('Credit amount must be positive and finite')
    return amount


def iso_date(value):
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError()
        return dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError('Expected ISO date YYYY-MM-DD') from None


def evidence_fields(data, *fields):
    required(data, *fields)
    for field in fields:
        value = data[field]
        values = value if isinstance(value, list) else [value]
        if not values or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError('Evidence must contain nonempty text: ' + field)


def uid():
    return uuid.uuid4().hex


class Workflow:
    def __init__(self, store, max_age_days=30):
        self.s, self.max_age_days = store, max_age_days

    def project(self, project_id):
        p = self.s.one('SELECT * FROM projects WHERE id=?', (project_id,))
        if not p:
            raise ValueError('Project not found')
        return p

    def latest_review(self, project_id, round_number):
        return self.s.one('SELECT * FROM reviews WHERE project_id=? AND round=? ORDER BY id DESC LIMIT 1', (project_id, round_number))

    def passed(self, project, round_number):
        qualification = qualification_status(self.s, project, self.max_age_days)
        if not qualification['eligible']:
            return False
        review = self.latest_review(project['id'], round_number)
        if not review or review['decision'] != 'pass' or review['sha'] != project['head_sha']:
            return False
        if review['created_at'] < qualification['assessment']['created_at']:
            return False
        try:
            timestamp = dt.datetime.fromisoformat(review['created_at'])
            current = dt.datetime.now(dt.timezone.utc)
            return current - dt.timedelta(days=self.max_age_days) <= timestamp <= current
        except (ValueError, TypeError):
            return False

    def set_stage(self, project_id, stage):
        # Preserve current business facts, rather than the historical maximum.
        # In particular, a completed old grant must not hide a new approval.
        funded = self.s.one("SELECT g.id FROM grants g JOIN decisions d ON d.id=g.decision_id WHERE d.project_id=? AND g.status='issued' LIMIT 1", (project_id,))
        decision = self.s.one('SELECT d.*,g.status AS grant_status FROM decisions d LEFT JOIN grants g ON g.decision_id=d.id WHERE d.project_id=? ORDER BY d.rowid DESC LIMIT 1', (project_id,))
        if funded:
            stage = 'sponsored'
        elif decision:
            if decision['outcome'] != 'approve':
                stage = 'rejected' if decision['outcome'] == 'reject' else 'hold'
            elif decision['grant_status'] == 'closed':
                stage = 'closed'
            elif decision['grant_status'] == 'cancelled':
                stage = 'hold'
            else:
                stage = 'approved'
        elif self.s.one('SELECT id FROM interviews WHERE project_id=? LIMIT 1', (project_id,)):
            interview = self.s.one('SELECT id FROM interviews WHERE project_id=? ORDER BY id DESC LIMIT 1', (project_id,))
            try:
                self.sponsorship_consent({'project_id': project_id, 'interview_id': interview['id']})
                stage = 'interviewed'
            except ValueError:
                stage = 'hold'
        elif self.s.one("SELECT id FROM contacts WHERE project_id=? AND status IN ('sent','replied') LIMIT 1", (project_id,)):
            stage = 'contacted'
        self.s.conn.execute('UPDATE projects SET stage=? WHERE id=?', (stage, project_id))

    def evidence_gate(self, project_id):
        """A current, complete snapshot is required for each forward evaluation."""
        p = self.project(project_id)
        metadata = json.loads(p['metadata'])
        if metadata.get('archived') or metadata.get('disabled'):
            raise ValueError('Archived or disabled project requires reconsideration')
        current = dt.datetime.now(dt.timezone.utc)
        def fresh(value):
            try:
                timestamp = dt.datetime.fromisoformat(value)
                return current - dt.timedelta(days=self.max_age_days) <= timestamp <= current
            except (ValueError, TypeError):
                return False
        if not fresh(p['refreshed_at']):
            raise ValueError('Project evidence is expired or invalid; refresh before proceeding')
        snapshot = self.s.one('SELECT * FROM snapshots WHERE project_id=? AND sha=? ORDER BY id DESC LIMIT 1', (p['id'], p['head_sha']))
        if not snapshot or snapshot['complete'] != 1:
            raise ValueError('Complete evidence snapshot required for the current commit')
        if not fresh(snapshot['fetched_at']):
            raise ValueError('The current evidence snapshot is expired; refresh before proceeding')
        return p

    def contactable_maintainer(self, project_id, maintainer_id=None):
        params = [project_id]
        where = ''
        if maintainer_id is not None:
            where = ' AND m.id=?'
            params.append(maintainer_id)
        candidates = self.s.all('SELECT m.*,pm.confirmed FROM maintainers m JOIN project_maintainers pm ON pm.maintainer_id=m.id WHERE pm.project_id=?' + where + ' ORDER BY m.id', params)
        eligible = [m for m in candidates if m['confirmed'] and not m['do_not_contact']]
        if not eligible:
            raise ValueError('Confirmed, contactable maintainer required')
        for maintainer in eligible:
            if not self.s.one("SELECT id FROM contacts WHERE maintainer_id=? AND status IN ('draft','sent','replied')", (maintainer['id'],)):
                return maintainer
        raise ValueError('This maintainer already has an active contact record; reuse it')

    def sponsorship_consent(self, decision):
        contact = self.s.one('SELECT c.*,m.do_not_contact,pm.confirmed FROM interviews i JOIN contacts c ON c.id=i.contact_id JOIN maintainers m ON m.id=c.maintainer_id JOIN project_maintainers pm ON pm.maintainer_id=m.id AND pm.project_id=i.project_id WHERE i.id=? AND i.project_id=?', (decision['interview_id'], decision['project_id']))
        if not contact or contact['status'] == 'declined' or contact['do_not_contact'] or not contact['confirmed']:
            raise ValueError('A consenting confirmed maintainer is required for sponsorship')

    def contact_gate(self, project_id):
        p = self.project(project_id)
        if not self.passed(p, 1) or not self.passed(p, 2):
            raise ValueError('Both current, unexpired review rounds must pass before contact or sponsorship')
        return self.evidence_gate(project_id)

    def apply(self, action, data, actor):
        if not isinstance(data, dict):
            raise ValueError('Expected an object')
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError('An accountable actor is required')
        operations = {'qualify': self.qualify, 'assign': self.assign, 'review': self.review, 'experience-create': self.experience_create,
                      'experience-result': self.experience_result, 'maintainer-confirm': self.maintainer_confirm,
                      'contact-draft': self.contact_draft, 'contact-status': self.contact_status,
                      'interview': self.interview, 'decision': self.decision,
                      'grant-reconcile': self.grant_reconcile, 'followup': self.followup,
                      'followup-schedule': self.followup_schedule}
        if action not in operations:
            raise ValueError('Unknown action')
        with self.s.transaction():
            result = operations[action](data, actor)
            self.s.event('workflow', result.get('id', data.get('project_id', '')), action, actor, {'input': data, 'result': result})
            return result

    def assign(self, d, actor):
        required(d, 'project_id', 'audience', 'owner')
        p = self.project(d['project_id'])
        if d['audience'] not in ('human','agent','both'):
            raise ValueError('audience must be human, agent or both')
        if p['audience'] not in ('unknown', d['audience']) and self.latest_review(p['id'], 2):
            raise ValueError('Audience is locked after round 2; use a new project evaluation cycle')
        self.s.conn.execute('UPDATE projects SET audience=?,owner=? WHERE id=?', (d['audience'], d['owner'], p['id']))
        return {'id': p['id'], 'audience': d['audience']}

    def qualify(self, d, actor):
        required(d, 'project_id', 'sha', 'category', 'scope_fit', 'maintenance', 'outcome', 'rationale', 'evidence')
        p = self.project(d['project_id'])
        if not p['head_sha'] or d['sha'] != p['head_sha']:
            raise ValueError('Qualification must refer to the current captured commit')
        if not p['owner'] or p['audience'] == 'unknown':
            raise ValueError('Assign owner and audience first')
        if d['category'] not in CATEGORIES or d['scope_fit'] not in ('yes','no','uncertain') or d['maintenance'] not in ('active','inactive','uncertain') or d['outcome'] not in ('include','hold','exclude'):
            raise ValueError('Invalid project qualification choices')
        evidence_fields(d, 'rationale')
        if not isinstance(d['evidence'], dict):
            raise ValueError('Qualification evidence must be an object')
        evidence_fields(d['evidence'], 'scope', 'maintenance', 'sources')
        validate_qualification_evidence(d)
        if d['evidence']['reason_code'] == 'no_real_value':
            review = self.latest_review(p['id'], 2)
            if not review or review['decision'] != 'reject' or review['sha'] != p['head_sha']:
                raise ValueError('Product-value exclusion requires a current second-round rejection')
            ids = json.loads(review['evidence']).get('experience_ids', [])
            product_evidence = False
            for eid in ids if isinstance(ids, list) else []:
                exp = self.s.one("SELECT * FROM experiences WHERE id=? AND project_id=? AND sha=? AND status='completed'", (eid,p['id'],p['head_sha']))
                if exp:
                    result = json.loads(exp['result'])
                    product_evidence |= result.get('success') is True or result.get('failure_kind') == 'product'
            if not product_evidence:
                raise ValueError('Product-value exclusion requires linked completed product evidence; environment failure is insufficient')
        if d['outcome'] == 'include':
            if d['scope_fit'] != 'yes' or d['maintenance'] != 'active' or d['category'] == 'out-of-scope':
                raise ValueError('Selection requires relevant AI scope and evidence of active maintenance')
            self.evidence_gate(p['id'])
        cur = self.s.conn.execute('INSERT INTO qualifications(project_id,sha,policy_version,category,scope_fit,maintenance,outcome,audience,rationale,evidence,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (p['id'],p['head_sha'],POLICY_VERSION,d['category'],d['scope_fit'],d['maintenance'],d['outcome'],p['audience'],d['rationale'],dump(d['evidence']),actor,now()))
        self.set_stage(p['id'], {'include':'technical_review','hold':'hold','exclude':'rejected'}[d['outcome']])
        return {'id':cur.lastrowid,'project_id':p['id'],'outcome':d['outcome'],'policy_version':POLICY_VERSION}

    def review(self, d, actor):
        required(d, 'project_id', 'round', 'sha', 'decision', 'rationale', 'evidence')
        if not isinstance(d['evidence'], dict):
            raise ValueError('Review evidence must be an object')
        p = self.project(d['project_id'])
        if type(d['round']) is not int or d['round'] not in (1,2) or d['decision'] not in ('pass','hold','reject'):
            raise ValueError('Invalid round or decision')
        if not p['head_sha'] or p['head_sha'] != d['sha']:
            raise ValueError('Review must refer to the current captured commit')
        if not p['owner'] or p['audience'] == 'unknown':
            raise ValueError('Assign owner and audience first')
        if d['decision'] == 'pass':
            qualification = qualification_status(self.s, p, self.max_age_days)
            if not qualification['eligible']:
                raise ValueError(QUALIFICATION_MESSAGES[qualification['reason']])
            self.evidence_gate(p['id'])
        if d['round'] == 1 and d['decision'] == 'pass':
            evidence_fields(d['evidence'], 'structure', 'maintenance', 'documentation')
            if not self.s.one('SELECT id FROM snapshots WHERE project_id=? AND sha=? AND complete=1', (p['id'], d['sha'])):
                raise ValueError('A captured evidence snapshot is required')
        if d['round'] == 2:
            if not self.passed(p, 1):
                raise ValueError('Round 1 must pass first')
            if d['decision'] == 'pass':
                required(d['evidence'], 'real_problem', 'usability', 'differentiation', 'potential', 'experience_ids')
                evidence_fields(d['evidence'], 'real_problem', 'usability', 'differentiation', 'potential')
                if not isinstance(d['evidence']['experience_ids'], list) or any(not isinstance(eid, str) or not eid.strip() for eid in d['evidence']['experience_ids']):
                    raise ValueError('experience_ids must be a list of experience identifiers')
                expected = {'human','agent'} if p['audience'] == 'both' else {p['audience']}
                covered = set()
                first = self.latest_review(p['id'], 1)
                for eid in d['evidence']['experience_ids']:
                    e = self.s.one('SELECT * FROM experiences WHERE id=? AND project_id=? AND sha=?', (eid, p['id'], d['sha']))
                    if not e or e['status'] != 'completed' or e['created_at'] < first['created_at']:
                        raise ValueError('Experience must be completed after the current round 1 review')
                    result = json.loads(e['result'])
                    if result['success'] is not True:
                        raise ValueError('Passing review requires successful representative experiences')
                    covered.add(e['audience'])
                if not expected.issubset(covered):
                    raise ValueError('Missing experience evidence for required audience')
        cur = self.s.conn.execute('INSERT INTO reviews(project_id,round,sha,decision,actor,rationale,evidence,created_at) VALUES(?,?,?,?,?,?,?,?)',
                                 (p['id'], d['round'], d['sha'], d['decision'], actor, d['rationale'], dump(d['evidence']), now()))
        stage = ('experience' if d['round'] == 1 else 'ready_contact') if d['decision'] == 'pass' else ('hold' if d['decision'] == 'hold' else 'rejected')
        self.set_stage(p['id'], stage)
        # A newer first-round review invalidates any previous second round, even at the same SHA.
        if d['round'] == 1 and self.latest_review(p['id'], 2):
            self.s.conn.execute('INSERT INTO reviews(project_id,round,sha,decision,actor,rationale,evidence,created_at) VALUES(?,2,?,\'hold\',?,?,?,?)',
                                (p['id'], d['sha'], actor, 'Round 1 changed; repeat second-round review', dump({'review_id': cur.lastrowid}), now()))
        return {'id': cur.lastrowid, 'stage': self.project(p['id'])['stage']}

    def experience_create(self, d, actor):
        required(d, 'project_id', 'audience', 'spec')
        required(d['spec'], 'scenario', 'success_criteria', 'environment', 'input', 'budget')
        p = self.project(d['project_id'])
        if not self.passed(p, 1):
            raise ValueError('Round 1 must pass before a second-round experience')
        self.evidence_gate(p['id'])
        if d['audience'] not in ('human','agent') or p['audience'] not in (d['audience'], 'both'):
            raise ValueError('Experience audience does not match project')
        eid = uid()
        self.s.conn.execute('INSERT INTO experiences(id,project_id,sha,audience,spec,created_at) VALUES(?,?,?,?,?,?)',
                            (eid, p['id'], p['head_sha'], d['audience'], dump(d['spec']), now()))
        return {'id': eid, 'sha': p['head_sha']}

    def experience_result(self, d, actor):
        required(d, 'id', 'sha', 'result')
        required(d['result'], 'success', 'executor', 'environment', 'steps', 'actual_result', 'failure_kind')
        evidence_fields(d['result'], 'executor', 'environment', 'steps', 'actual_result')
        if type(d['result']['success']) is not bool:
            raise ValueError('success must be a boolean')
        artifacts = d['result'].get('artifacts')
        if not isinstance(artifacts, list) or any(not isinstance(item, str) or not item.strip() for item in artifacts) or (d['result']['success'] and not artifacts):
            raise ValueError('Successful experiences require nonempty artifact evidence')
        e = self.s.one('SELECT * FROM experiences WHERE id=?', (d['id'],))
        if not e or e['sha'] != d['sha']:
            raise ValueError('Unknown experience or wrong SHA')
        if e['status'] == 'completed':
            if e['result'] != dump(d['result']):
                raise ValueError('Experience results are immutable; create a new run')
            return {'id': e['id'], 'duplicate': True}
        if d['result']['failure_kind'] not in ('none', 'product', 'environment') or d['result']['success'] != (d['result']['failure_kind'] == 'none'):
            raise ValueError('Experience success and failure_kind disagree')
        self.s.conn.execute("UPDATE experiences SET status='completed',result=? WHERE id=?", (dump(d['result']), e['id']))
        return {'id': e['id'], 'success': d['result']['success']}

    def maintainer_confirm(self, d, actor):
        required(d, 'project_id', 'maintainer_id', 'evidence')
        cur = self.s.conn.execute("UPDATE project_maintainers SET confirmed=1,role='maintainer',evidence=? WHERE project_id=? AND maintainer_id=?",
                                 (d['evidence'], d['project_id'], d['maintainer_id']))
        if not cur.rowcount:
            raise ValueError('Maintainer relationship not found; import a GitHub profile first')
        return {'id': d['maintainer_id']}

    def contact_draft(self, d, actor):
        required(d, 'project_id', 'maintainer_id', 'channel', 'draft')
        p = self.contact_gate(d['project_id'])
        m = self.contactable_maintainer(p['id'], d['maintainer_id'])
        cid = uid()
        self.s.conn.execute('INSERT INTO contacts(id,project_id,maintainer_id,channel,draft,actor,created_at) VALUES(?,?,?,?,?,?,?)',
                            (cid, p['id'], m['id'], d['channel'], d['draft'], actor, now()))
        return {'id': cid, 'status': 'draft', 'message_sent': False}

    def contact_status(self, d, actor):
        required(d, 'id', 'status', 'evidence')
        c = self.s.one('SELECT * FROM contacts WHERE id=?', (d['id'],))
        allowed = {'draft': {'sent','closed','declined'}, 'sent': {'replied','closed','declined'}, 'replied': {'closed','declined'}}
        if not c or d['status'] not in allowed.get(c['status'], set()):
            raise ValueError('Invalid contact transition')
        if d['status'] == 'sent':
            self.contact_gate(c['project_id'])
            maintainer = self.s.one('SELECT do_not_contact FROM maintainers WHERE id=?', (c['maintainer_id'],))
            if maintainer['do_not_contact']:
                raise ValueError('Maintainer is no longer contactable')
        self.s.conn.execute('UPDATE contacts SET status=?,evidence=? WHERE id=?', (d['status'], d['evidence'], c['id']))
        if d['status'] == 'declined':
            self.s.conn.execute('UPDATE maintainers SET do_not_contact=1 WHERE id=?', (c['maintainer_id'],))
            self.s.conn.execute("UPDATE grants SET status='cancelled',updated_at=? WHERE status IN ('pending','failed') AND decision_id IN (SELECT d.id FROM decisions d JOIN interviews i ON i.id=d.interview_id JOIN contacts c ON c.id=i.contact_id WHERE c.maintainer_id=?)", (now(), c['maintainer_id']))
            self.s.conn.execute("UPDATE contact_summaries SET class='do_not_contact',priority=0,rationale='Maintainer declined contact',created_at=? WHERE project_id=?", (now(), c['project_id']))
            self.s.conn.execute("UPDATE contact_points SET confidence='blocked' WHERE maintainer_id=? OR project_id=?", (c['maintainer_id'], c['project_id']))
            self.set_stage(c['project_id'], 'hold')
        if d['status'] == 'closed':
            self.set_stage(c['project_id'], 'hold')
        if d['status'] in ('sent', 'replied'):
            self.set_stage(c['project_id'], 'contacted')
        return {'id': c['id'], 'status': d['status'], 'message_sent': False}

    def interview(self, d, actor):
        required(d, 'project_id', 'contact_id', 'notes', 'profile')
        self.contact_gate(d['project_id'])
        c = self.s.one("SELECT * FROM contacts WHERE id=? AND status='replied'", (d['contact_id'],))
        if not c or c['project_id'] != d['project_id'] or not self.s.one('SELECT * FROM project_maintainers WHERE project_id=? AND maintainer_id=? AND confirmed=1',
                                   (d['project_id'], c['maintainer_id'])):
            raise ValueError('A reply from a confirmed project maintainer is required')
        if self.s.one('SELECT do_not_contact FROM maintainers WHERE id=?', (c['maintainer_id'],))['do_not_contact']:
            raise ValueError('Maintainer has declined contact')
        required(d['notes'], 'users', 'commitment', 'roadmap', 'credit_need', 'milestone', 'consent')
        evidence_fields(d['notes'], 'users', 'commitment', 'roadmap', 'credit_need', 'milestone')
        if d['notes']['consent'] is not True:
            raise ValueError('Interview consent must be recorded')
        profile=d['profile']
        if (not isinstance(profile,list) or not profile or any(not isinstance(value,str) for value in profile)
                or not set(profile).issubset({'engineer','entrepreneur','undetermined'})
                or len(profile)!=len(set(profile)) or ('undetermined' in profile and len(profile)>1)):
            raise ValueError('Choose engineer and/or entrepreneur, or undetermined alone; duplicate profile tags are not allowed')
        cur = self.s.conn.execute('INSERT INTO interviews(project_id,contact_id,actor,notes,profile,created_at) VALUES(?,?,?,?,?,?)',
                                 (d['project_id'], c['id'], actor, dump(d['notes']), dump(d['profile']), now()))
        self.set_stage(d['project_id'], 'interviewed')
        return {'id': cur.lastrowid}

    def decision(self, d, actor):
        required(d, 'project_id', 'interview_id', 'outcome', 'rationale')
        self.project(d['project_id'])
        if d['outcome'] not in ('approve','hold','reject'):
            raise ValueError('Invalid decision')
        if not self.s.one('SELECT id FROM interviews WHERE id=? AND project_id=?', (d['interview_id'], d['project_id'])):
            raise ValueError('Interview does not belong to this project')
        terms = d.get('terms', {})
        if d['outcome'] == 'approve':
            self.contact_gate(d['project_id'])
            self.sponsorship_consent(d)
            required(terms, 'amount', 'unit', 'account', 'purpose', 'expires_at', 'review_dates')
            evidence_fields(terms, 'unit', 'account', 'purpose')
            if any(not isinstance(terms[key], str) for key in ('unit', 'account', 'purpose')):
                raise ValueError('Unit, account and purpose must be text')
            if 'identity_evidence' in terms:
                evidence_fields(terms, 'identity_evidence')
            amount = positive_amount(terms['amount'])
            if terms['unit'] == 'cents' and (amount != amount.to_integral_value() or amount > 2147483647):
                raise ValueError('Cents must be a positive 32-bit integer')
            today = dt.datetime.now(dt.timezone.utc).date()
            if iso_date(terms['expires_at']) <= today:
                raise ValueError('Expiration must be in the future')
            if not isinstance(terms['review_dates'], list) or not terms['review_dates'] or any(iso_date(x) <= today for x in terms['review_dates']):
                raise ValueError('Review dates must be future ISO dates')
            if len(set(terms['review_dates'])) != len(terms['review_dates']):
                raise ValueError('Review dates must be unique')
            if self.s.one("SELECT d.id FROM decisions d JOIN grants g ON d.id=g.decision_id WHERE d.project_id=? AND g.status NOT IN ('cancelled','closed')", (d['project_id'],)):
                raise ValueError('An active sponsorship exists; reconcile or complete it first')
        did = uid()
        self.s.conn.execute('INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?)',
                            (did, d['project_id'], d['interview_id'], d['outcome'], actor, d['rationale'], dump(terms), now()))
        result = {'id': did, 'outcome': d['outcome']}
        if d['outcome'] == 'approve':
            gid = uid()
            self.s.conn.execute('INSERT INTO grants(id,decision_id,updated_at) VALUES(?,?,?)', (gid, did, now()))
            result['grant_id'] = gid
        if d['outcome'] != 'approve':
            self.s.conn.execute("UPDATE grants SET status='cancelled',updated_at=? WHERE status IN ('pending','failed') AND decision_id IN (SELECT id FROM decisions WHERE project_id=?)", (now(), d['project_id']))
        self.set_stage(d['project_id'], 'approved' if d['outcome'] == 'approve' else d['outcome'])
        return result

    def grant_reconcile(self, d, actor):
        required(d, 'id', 'status', 'receipt')
        if not isinstance(d['receipt'], dict):
            raise ValueError('Receipt must be an object')
        if d['status'] not in ('issued','failed','unknown','cancelled'):
            raise ValueError('Invalid grant status')
        g = self.s.one('SELECT * FROM grants WHERE id=?', (d['id'],))
        if not g or g['status'] in ('issued','closed','cancelled'):
            raise ValueError('Grant is missing or already finalized')
        decision = self.s.one('SELECT * FROM decisions WHERE id=?', (g['decision_id'],))
        terms = json.loads(decision['terms'])
        if d['status'] in ('failed', 'cancelled') and g['status'] in ('submitting', 'unknown', 'awaiting_claim'):
            raise ValueError('Provider write may exist; reconcile it before cancellation or retry')
        if d['status'] == 'issued':
            required(d['receipt'], 'transaction_id', 'account', 'amount', 'unit', 'evidence')
            evidence_fields(d['receipt'], 'transaction_id', 'account', 'unit', 'evidence')
            if str(d['receipt']['account']).strip().lower() != str(terms['account']).strip().lower() or d['receipt']['unit'] != terms['unit'] or positive_amount(d['receipt']['amount']) != positive_amount(terms['amount']):
                raise ValueError('Receipt does not match approved account/amount/unit')
            for row in self.s.all("SELECT receipt FROM grants WHERE status IN ('issued','closed')"):
                if json.loads(row['receipt']).get('transaction_id') == d['receipt']['transaction_id']:
                    raise ValueError('Transaction receipt is already used')
            for due in terms['review_dates']:
                self.s.conn.execute('INSERT OR IGNORE INTO followups(id,grant_id,due_at) VALUES(?,?,?)', (uid(), g['id'], due))
            self.s.conn.execute("UPDATE projects SET stage='sponsored' WHERE id=?", (decision['project_id'],))
        previous = json.loads(g['receipt']) if g['receipt'] else {}
        for key in ('provider_base', 'event_slug', 'invitation_id'):
            if key in previous and key in d['receipt'] and previous[key] != d['receipt'][key]:
                raise ValueError('Receipt cannot change provider binding: ' + key)
        receipt = dict(previous, **d['receipt'])
        self.s.conn.execute('UPDATE grants SET status=?,receipt=?,updated_at=? WHERE id=?', (d['status'], dump(receipt), now(), g['id']))
        return {'id': g['id'], 'status': d['status']}

    def followup_schedule(self, d, actor):
        required(d, 'grant_id', 'due_at')
        grant=self.s.one("SELECT id FROM grants WHERE id=? AND status='issued'",(d['grant_id'],))
        if not grant:
            raise ValueError('Follow-up scheduling requires an issued grant')
        if iso_date(d['due_at'])<=dt.datetime.now(dt.timezone.utc).date():
            raise ValueError('Follow-up date must be in the future')
        existing=self.s.one('SELECT id,status FROM followups WHERE grant_id=? AND due_at=?',(grant['id'],d['due_at']))
        if existing:
            if existing['status']!='pending':
                raise ValueError('That review date already belongs to a finalized followup; choose another date')
            return {'id':existing['id'],'duplicate':True}
        identifier=uid()
        self.s.conn.execute('INSERT INTO followups(id,grant_id,due_at) VALUES(?,?,?)',(identifier,grant['id'],d['due_at']))
        return {'id':identifier,'duplicate':False}

    def followup(self, d, actor):
        required(d, 'id', 'result')
        required(d['result'], 'usage', 'milestone', 'evidence', 'decision', 'next_action')
        if d['result']['decision'] not in ('continue','adjust','pause','end'):
            raise ValueError('Invalid followup decision')
        f = self.s.one('SELECT * FROM followups WHERE id=?', (d['id'],))
        if not f or f['status'] != 'pending':
            raise ValueError('Pending followup not found')
        next_followup = None
        today = dt.datetime.now(dt.timezone.utc).date()
        if d['result']['decision'] != 'end':
            next_date = d['result'].get('next_review_at')
            if next_date is not None:
                if iso_date(next_date) <= today:
                    raise ValueError('next_review_at must be a future ISO date')
                if next_date == f['due_at']:
                    raise ValueError('next_review_at must differ from the review being completed')
                prior = self.s.one('SELECT * FROM followups WHERE grant_id=? AND due_at=?', (f['grant_id'], next_date))
                if prior and prior['status'] != 'pending':
                    raise ValueError('That review date already belongs to a finalized followup; choose another date')
                if not prior:
                    identifier = uid()
                    self.s.conn.execute('INSERT INTO followups(id,grant_id,due_at) VALUES(?,?,?)', (identifier, f['grant_id'], next_date))
                    prior = self.s.one('SELECT * FROM followups WHERE id=?', (identifier,))
                next_followup = prior
            else:
                next_followup = self.s.one("SELECT * FROM followups WHERE grant_id=? AND id<>? AND status='pending' AND due_at>? ORDER BY due_at,id LIMIT 1", (f['grant_id'], f['id'], today.isoformat()))
                if not next_followup:
                    raise ValueError('next_review_at is required when no future pending followup exists')
        recorded = dict(d['result'], next_review_at=next_followup['due_at'] if next_followup else None,
                        next_followup_id=next_followup['id'] if next_followup else None)
        self.s.conn.execute("UPDATE followups SET status='completed',result=? WHERE id=?", (dump(recorded), f['id']))
        if d['result']['decision'] == 'end':
            self.s.conn.execute("UPDATE grants SET status='closed',updated_at=? WHERE id=?", (now(), f['grant_id']))
            self.s.conn.execute("UPDATE followups SET status='cancelled' WHERE grant_id=? AND status='pending'", (f['grant_id'],))
            decision = self.s.one('SELECT d.project_id FROM grants g JOIN decisions d ON d.id=g.decision_id WHERE g.id=?', (f['grant_id'],))
            self.set_stage(decision['project_id'], 'closed')
        return {'id': f['id'], 'decision': d['result']['decision'],
                'next_followup_id': next_followup['id'] if next_followup else None,
                'next_review_at': next_followup['due_at'] if next_followup else None}
