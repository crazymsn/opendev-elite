"""Read-only offline acceptance evidence inventory; never certifies external truth."""
import datetime as dt
import decimal
import hashlib
import json
import re
from urllib.parse import urlsplit

from .auth import IdentityStore, Principal
from .accounts import HUMAN_ROLES
from .db import now
from .operations import doctor
from .workflow import Workflow


_MARKER = re.compile(r'\b(synthetic|fixture|mocked|simulation)\b|fixture:|\.invalid(?:[/:\s"\']|$)|REPLACE_WITH|CHANGEME', re.I)


def _object(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _marked(value):
    return bool(_MARKER.search(json.dumps(value, ensure_ascii=False, default=str)))


def _https_origin(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        host = parsed.hostname or ''
        return bool(parsed.scheme == 'https' and host and not parsed.username and not parsed.password
                    and not parsed.query and not parsed.fragment and not _marked(value)
                    and host not in ('example.com', 'example.org', 'example.net', 'localhost')
                    and not host.endswith(('.example', '.invalid')))
    except ValueError:
        return False


def _service_status(config, store):
    try:
        identities = IdentityStore(config)
    except (ValueError, TypeError, KeyError):
        return False, {'identity_configuration_valid': False, 'separate_operational_roles': False}
    try:
        from .browser_auth import BrowserSessions, configured_origin
        from .oidc import OIDCClient
        configured_origin(config.get('public_origin'))
        BrowserSessions(identities, config.get('browser_session_seconds', 3600))
        if type(config.get('allow_credit_writes', False)) is not bool:
            raise ValueError('Invalid credit-write switch')
        ttl = config.get('agent_lease_seconds', 900)
        if type(ttl) is not int or not 60 <= ttl <= 3600:
            raise ValueError('Invalid Agent lease configuration')
        if config.get('oidc') is not None:
            # Constructor validates local settings/dependencies only. Discovery
            # and token exchange are deliberately not invoked by this audit.
            OIDCClient(config['oidc'], config.get('public_origin'), identities)
    except (ValueError, TypeError, KeyError):
        return False, {'identity_configuration_valid': True, 'browser_configuration_valid': False,
                       'separate_operational_roles': False, 'oidc_configured': config.get('oidc') is not None}
    known_defaults = {hashlib.sha256(s.encode()).hexdigest() for s in ('test', 'changeme', 'password', 'example', 'demo')}
    has_placeholder = any(digest in known_defaults or _marked(principal.actor) for principal, digest in identities.users)
    # The HTTP service combines compatibility tokens with password accounts.
    # Inventory the same active human accounts, without opening a second Store,
    # authenticating, seeding an administrator, or reading password hashes.
    principals = {p.actor:p for p, _ in identities.users}
    account_valid, account_count, conflicts = True, 0, 0
    for row in store.all("SELECT id,roles FROM accounts WHERE status='active'"):
        try:
            account_roles = json.loads(row['roles'])
            valid = (isinstance(row['id'],str) and re.fullmatch(r'account\.[a-f0-9]{32}',row['id'])
                     and isinstance(account_roles,list) and bool(account_roles)
                     and all(isinstance(role,str) and role in HUMAN_ROLES for role in account_roles)
                     and len(account_roles)==len(set(account_roles)))
        except (ValueError,TypeError):
            valid = False
        if not valid:
            account_valid = False
            continue
        if row['id'] in principals:
            # CombinedIdentities resolves the token identity first. Such a
            # collision is a configuration fault, never a second person.
            conflicts += 1
            continue
        principals[row['id']] = Principal(row['id'],frozenset(account_roles))
        account_count += 1
    roles = ('reviewer', 'operator', 'approver', 'disburser', 'agent')
    candidates = {role: [p.actor for p in principals.values() if role in p.roles] for role in roles}
    def distinct(index, used):
        return index == len(roles) or any(distinct(index + 1, used | {actor}) for actor in candidates[roles[index]] if actor not in used)
    separated = distinct(0, set())
    return bool(separated and not has_placeholder and account_valid and not conflicts), {
        'identity_configuration_valid': True, 'known_placeholder_absent': not has_placeholder,
        'browser_configuration_valid': True, 'oidc_configured': config.get('oidc') is not None,
        'oidc_discovery_or_login_checked': False,
        'separate_operational_roles': separated, 'enabled_identity_count': len(principals),
        'enabled_token_identity_count': len(identities.users),
        'enabled_account_identity_count': account_count, 'account_actor_conflicts': conflicts,
        'account_configuration_valid': account_valid,
        'token_entropy_or_compromise_checked': False, 'password_login_or_strength_checked': False}


def audit_readiness(store, service_config=None, credits_config=None, agent_config=None):
    """Inspect configuration and recorded provenance without writes or networking.

    status never means production verified: local database records cannot prove
    genuine external execution, provider receipt authenticity or operator identity.
    Only opaque record IDs/counts are returned, not emails, tokens or raw evidence.
    """
    health = doctor(store, credits_config, agent_config)
    checks = []
    def check(key, layer, status, summary, evidence=None):
        checks.append({'id': key, 'layer': layer, 'status': status,
                       'summary': summary, 'evidence': evidence or {}})
    service_ok, service_evidence = _service_status(service_config, store)
    check('service_identities', 'configuration', 'pass' if service_ok else 'missing_or_invalid',
          'Five separate operational identities from enabled tokens and active human accounts; no login or real-person verification performed.', service_evidence)
    for kind in ('credits', 'agent'):
        ready = health[kind]['configuration_ready']
        check(kind + '_configuration', 'configuration', 'pass' if ready else 'missing_or_invalid',
              'Static settings only; external credentials, endpoints and executor availability are not verified.', health[kind])
    db_ok = all(health['database'][k] for k in ('integrity_ok', 'foreign_keys_ok', 'schema_supported'))
    check('database_integrity', 'local_data', 'pass' if db_ok else 'fail', 'Read-only SQLite integrity, foreign keys and supported schema.', health['database'])
    check('collection_queue', 'local_data', 'pass' if health['healthy'] else 'attention',
          'Dead jobs or expired leases require inspection; a healthy queue does not establish collection completeness.', health['queue'])
    suspect = []
    # Scan business records even when still pending or not eligible for acceptance.
    # The list is fixed in code; no caller-controlled SQL identifiers are used.
    for table, columns in (
        ('experiences', ('spec', 'result')),
        ('reviews', ('actor', 'rationale', 'evidence')),
        ('contacts', ('channel', 'draft', 'evidence')),
        ('interviews', ('actor', 'notes')),
        ('decisions', ('actor', 'rationale', 'terms')),
        ('grants', ('receipt',)),
        ('maintainers', ('login', 'profile')),
    ):
        for row in store.all('SELECT id,' + ','.join(columns) + ' FROM ' + table):
            if any(_marked(row[column]) for column in columns if row[column] is not None):
                suspect.append({'kind': table[:-1], 'id': row['id']})
    valid_projects, snapshots = set(), []
    for p in store.all('SELECT * FROM projects'):
        metadata = _object(p['metadata'])
        sources = [r['source'] for r in store.all('SELECT source FROM sources WHERE project_id=?', (p['id'],))]
        # Do not classify a real README mentioning demos/fixtures as synthetic.
        marked = _marked({'url': p['url'], 'name': p['full_name'], 'sources': sources}) or metadata.get('synthetic') is True
        s = store.one('SELECT * FROM snapshots WHERE project_id=? ORDER BY id DESC LIMIT 1', (p['id'],))
        evidence = _object(s['evidence']) if s else {}
        marked |= evidence.get('synthetic') is True
        if marked:
            suspect.append({'kind': 'project', 'id': p['id']})
            continue
        repo = _object(evidence.get('repository'))
        source_urls = _object(evidence.get('sources'))
        root = 'https://api.github.com/repos/' + p['full_name'] + '/'
        head = _object(evidence.get('head'))
        fresh = False
        try:
            fresh = dt.datetime.fromisoformat(p['refreshed_at']) >= dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
        except (ValueError, TypeError):
            pass
        if (s and s['complete'] == 1 and s['sha'] == p['head_sha'] and fresh and
                isinstance(p['head_sha'], str) and re.fullmatch('[0-9a-f]{40}', p['head_sha']) and
                p['url'] == 'https://github.com/' + p['full_name'] and repo.get('id') == p['id'] and
                repo.get('html_url') == p['url'] and head.get('sha') == p['head_sha'] and
                evidence.get('errors') == [] and isinstance(source_urls.get('head'), str) and
                source_urls['head'].startswith(root) and not metadata.get('archived') and not metadata.get('disabled')):
            valid_projects.add(p['id'])
            snapshots.append(s['id'])
    completed = {'human': [], 'agent': []}
    for e in store.all("SELECT * FROM experiences WHERE status='completed'"):
        spec, result = _object(e['spec']), _object(e['result'])
        if _marked({'scenario': spec.get('scenario'), 'executor': result.get('executor'),
                    'environment': result.get('environment'), 'artifacts': result.get('artifacts')}):
            suspect.append({'kind': 'experience', 'id': e['id']})
            continue
        if e['project_id'] not in valid_projects or result.get('success') is not True:
            continue
        p = store.one('SELECT head_sha FROM projects WHERE id=?', (e['project_id'],))
        if e['sha'] != p['head_sha'] or not result.get('steps') or not result.get('actual_result') or not result.get('artifacts'):
            continue
        if e['audience'] == 'agent':
            lease = store.one("SELECT * FROM agent_leases WHERE experience_id=? AND state='completed'", (e['id'],))
            if not lease or lease['actor'] != result.get('executor') or not lease.get('task_sha256'):
                continue
            task = _object(lease.get('task'))
            if (task.get('id') != e['id'] or task.get('sha') != e['sha'] or not task.get('run_id') or
                    not isinstance(lease.get('task'), str) or
                    hashlib.sha256(lease['task'].encode()).hexdigest() != lease['task_sha256']):
                continue
            paths = result.get('artifacts')
            files = store.all('SELECT u.path,u.size,length(f.data) AS actual_size FROM artifact_links l '
                              'JOIN artifact_uploads u ON u.id=l.artifact_id JOIN artifact_files f ON f.id=u.id '
                              'WHERE l.experience_id=? AND u.experience_id=? AND u.run_id=? AND u.actor=?',
                              (e['id'], e['id'], task['run_id'], lease['actor']))
            if (not isinstance(paths, list) or any(not isinstance(path, str) for path in paths)
                    or len(set(paths)) != len(paths) or set(paths) != {file['path'] for file in files}
                    or any(file['size'] != file['actual_size'] for file in files)):
                continue
        completed[e['audience']].append(e['id'])
    round_ids, reviewed_projects = [], set()
    w = Workflow(store)
    for pid in valid_projects:
        p = w.project(pid)
        try:
            if not w.passed(p, 1) or not w.passed(p, 2):
                continue
        except (ValueError, TypeError):
            continue
        reviews = [w.latest_review(pid, n) for n in (1, 2)]
        if any(_marked({'actor': r['actor'], 'rationale': r['rationale'], 'evidence': _object(r['evidence'])}) for r in reviews):
            suspect.append({'kind': 'review', 'id': reviews[-1]['id']})
            continue
        referenced = _object(reviews[1]['evidence']).get('experience_ids', [])
        if not isinstance(referenced, list) or any(not isinstance(eid, str) for eid in referenced):
            continue
        needed = ('human', 'agent') if p['audience'] == 'both' else (p['audience'],)
        if all(set(referenced).intersection(completed.get(a, [])) for a in needed):
            round_ids.extend(r['id'] for r in reviews)
            reviewed_projects.add(pid)
    issued = []
    for grant in store.all("SELECT g.*,d.project_id,d.terms,d.actor AS approver FROM grants g JOIN decisions d ON d.id=g.decision_id WHERE g.status IN ('issued','closed') AND d.outcome='approve'"):
        receipt, terms = _object(grant['receipt']), _object(grant['terms'])
        if _marked(receipt) or _marked(terms):
            suspect.append({'kind': 'grant', 'id': grant['id']})
            continue
        base = receipt.get('provider_base')
        configured_provider_matches = not isinstance(credits_config, dict) or (
            base == str(credits_config.get('base_url', '')).rstrip('/') and
            receipt.get('event_slug') == credits_config.get('event_slug'))
        account_matches = str(receipt.get('account', '')).lower() == str(terms.get('account', '')).lower()
        try:
            amount = decimal.Decimal(str(receipt.get('amount')))
            amount_matches = amount.is_finite() and amount > 0 and amount == decimal.Decimal(str(terms.get('amount')))
        except decimal.InvalidOperation:
            amount_matches = False
        provider_event = store.one("SELECT id,actor FROM events WHERE entity_type='grant' AND entity_id=? AND action='credits_usage_observed' ORDER BY id DESC LIMIT 1", (grant['id'],))
        if (grant['project_id'] in reviewed_projects and configured_provider_matches and _https_origin(base) and receipt.get('invitation_id') and
                receipt.get('participant_id') and receipt.get('activated_at') and receipt.get('observed_at') and
                receipt.get('event_slug') == terms.get('event_slug') and
                receipt.get('transaction_id') == 'next-credits:' + str(terms.get('event_slug')) + ':' + str(receipt.get('invitation_id')) and
                receipt.get('unit') == terms.get('unit') == 'cents' and account_matches and amount_matches and
                provider_event and provider_event['actor'] != grant['approver']):
            issued.append(grant['id'])
    suspect = list({(str(record['kind']), str(record['id'])): record for record in suspect}.values())
    check('synthetic_data_absent', 'local_data', 'fail' if suspect else 'pass',
          'Explicit synthetic/test markers must be absent from the production evidence database; absence is not proof of truth.',
          {'count': len(suspect), 'records': suspect[:20], 'scan_is_heuristic': True})
    inventory = [('github_collection_evidence', snapshots, 'Current complete snapshots carry matching repository/SHA/API-source provenance.'),
                 ('human_experience_evidence', completed['human'], 'Successful human experiences with declared steps and artifacts are recorded.'),
                 ('agent_experience_evidence', completed['agent'], 'Successful Agent experiences are tied to completed authenticated leases and stored artifacts; file contents require independent review.'),
                 ('two_round_review_evidence', round_ids, 'Current two-round passes refer to qualifying recorded experience IDs.'),
                 ('credits_issuance_evidence', issued, 'Activated participant receipts match terms/provider identifiers and a separate reconciliation actor.')]
    for key, ids, label in inventory:
        check(key, 'external_evidence', 'recorded_unverified' if ids else 'missing', label, {'count': len(ids), 'ids': ids[:20], 'externally_verified': False})
    local_ready = bool(service_ok and health['credits']['configuration_ready'] and health['agent']['configuration_ready'] and db_ok and health['healthy'] and not suspect)
    evidence_present = all(ids for _, ids, _ in inventory)
    status = ('blocked' if not local_ready else 'evidence_recorded_external_verification_required'
              if evidence_present else 'configuration_ready_evidence_missing')
    warnings = ['This offline audit makes no network calls and never certifies execution, source integrity, credential validity or Credits issuance.',
                'Database evidence can be forged by a trusted local database owner; review artifacts and provider audit records independently.',
                'HTTP regression tests and demo fixtures establish software behavior only; they are not real-product or real-funding acceptance.',
                'Docker output-volume quota, reviewed image contents, TLS, backups and operational ownership require deployment verification.']
    if suspect:
        warnings.append('Explicit test/synthetic markers were found; use a separate clean production database and retain this database as test evidence.')
    return {'schema_version': 1, 'generated_at': now(), 'status': status,
            'local_configuration_ready': local_ready, 'production_verified': False,
            'external_evidence_recorded': evidence_present, 'checks': checks, 'warnings': warnings, 'doctor': health}
