"""Contact evidence from supplied CSVs and captured public GitHub sources.

CSV logins are leads, never GitHub identities or confirmed project relationships.
No message delivery, guessed addresses or commit email mining occurs here.
"""
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, quote

from .db import dump, now
from .criteria import qualification_status

LOGIN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?')
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}")


def email_address(value):
    value = value.strip() if isinstance(value, str) else ''
    if len(value) > 254 or not EMAIL.fullmatch(value) or '..' in value or any(c in value for c in '?#&=%'):
        return None
    local, domain = value.rsplit('@', 1)
    if local.startswith('.') or local.endswith('.') or len(local) > 64:
        return None
    if domain.lower() in ('example.com', 'example.org', 'example.net') or domain.lower().endswith(('.invalid', '.example')):
        return None
    if 'noreply' in value.lower() or 'no-reply' in value.lower():
        return None
    return local + '@' + domain.lower()


def web_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        return None
    value = value.strip()
    if any(ord(c) < 33 for c in value):
        return None
    if '://' not in value and ':' not in value:
        value = 'https://' + value
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or '.' not in parsed.hostname or parsed.username or parsed.password:
            return None
        _ = parsed.port
    except ValueError:
        return None
    return value


def parse_leads(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > 800000:
        raise ValueError('Contact CSV must be UTF-8 text up to 800 KB')
    stats = dict(rows=0, blank_rows=0, invalid_login=0, invalid_email=0, duplicates=0, accepted=0)
    records, seen = [], set()
    reader = csv.reader(io.StringIO(text.lstrip('\ufeff'), newline=''), strict=True)
    try:
        for number, row in enumerate(reader, 1):
            stats['rows'] += 1
            if stats['rows'] > 12000:
                raise ValueError('Contact CSV exceeds 12000 rows')
            if not any(cell.strip() for cell in row):
                stats['blank_rows'] += 1
                continue
            if len(row) != 20:
                raise ValueError(f'Contact CSV row {number} must have 20 columns (no header)')
            login, email = row[0].strip(), email_address(row[8])
            valid_login = bool(LOGIN.fullmatch(login))
            stats['invalid_login'] += int(not valid_login)
            stats['invalid_email'] += int(email is None)
            if not valid_login or not email:
                continue
            key = (login.lower(), email.lower())
            if key in seen:
                stats['duplicates'] += 1
                continue
            seen.add(key)
            records.append((login.lower(), email, number))
    except csv.Error as exc:
        raise ValueError('Invalid contact CSV structure') from exc
    stats['accepted'] = len(records)
    return records, stats


def put_point(store, project_id, maintainer_id, channel, value, source, source_url, confidence,
              evidence, source_kind=None, source_ref=None, explicit=True):
    source_kind = source_kind or {
        'github_profile': 'profile', 'readme': 'readme', 'issues': 'issues',
        'user_csv': 'csv', 'repository': 'repository',
    }.get(source, 'other')
    key = hashlib.sha256(dump([project_id, maintainer_id, channel, value.lower(), source, source_url,
                               source_kind, source_ref]).encode()).hexdigest()
    store.conn.execute('''INSERT INTO contact_points
        (id,project_id,maintainer_id,channel,value,source,source_url,confidence,evidence,discovered_at,
         source_kind,source_ref,explicit)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET evidence=excluded.evidence,discovered_at=excluded.discovered_at,
          source_kind=excluded.source_kind,source_ref=excluded.source_ref,explicit=excluded.explicit''',
        (key, project_id, maintainer_id, channel, value, source, source_url, confidence, dump(evidence), now(),
         source_kind, source_ref, int(bool(explicit))))


def sync_person(store, person):
    profile = json.loads(person['profile'])
    source = 'https://github.com/' + quote(person['login'], safe='')
    # Rebuild derived public points so removed public emails do not remain active.
    store.conn.execute("DELETE FROM contact_points WHERE maintainer_id=? AND source IN ('github_profile','user_csv')", (person['id'],))
    put_point(store, None, person['id'], 'github', source, 'github_profile', source, 'public_profile', {}, 'profile', source)
    if email := email_address(profile.get('email')):
        put_point(store, None, person['id'], 'email', email, 'github_profile', source, 'public_profile', {}, 'profile', source)
    if website := web_url(profile.get('blog')):
        put_point(store, None, person['id'], 'website', website, 'github_profile', source, 'public_profile', {}, 'profile', source)
    twitter = profile.get('twitter_username')
    if isinstance(twitter, str) and re.fullmatch('[A-Za-z0-9_]{1,15}', twitter):
        put_point(store, None, person['id'], 'social', 'https://x.com/' + twitter, 'github_profile', source, 'public_profile', {}, 'profile', source)
    # A login match is only a clue: accounts can be renamed or reassigned.
    for lead in store.all('SELECT * FROM contact_leads WHERE login=? COLLATE NOCASE', (person['login'],)):
        put_point(store, None, person['id'], 'email', lead['email'], 'user_csv',
                  lead['source_sha256'], 'unverified',
                  {k: lead[k] for k in ('source_file', 'source_row', 'login', 'imported_at')},
                  'csv', lead['source_sha256'], False)


def sync_project(store, project_id, snapshot_id=None):
    project = store.one('SELECT * FROM projects WHERE id=?', (project_id,))
    if not project:
        raise ValueError('Project not found')
    if not qualification_status(store, project)['eligible'] or project['stage'] in ('hold', 'rejected'):
        snapshot = store.one('SELECT id FROM snapshots WHERE project_id=? ORDER BY id DESC LIMIT 1', (project_id,))
        if snapshot:
            store.conn.execute('''INSERT OR REPLACE INTO contact_summaries
                (project_id,snapshot_id,class,source_mask,point_count,priority,rationale,created_at)
                VALUES(?,?,?,?,?,?,?,?)''',
                (project_id, snapshot['id'], 'without_clue', '', 0, 0,
                 'Contact extraction is deferred until the project passes selection', now()))
        return {'project_id': project_id, 'eligible': False, 'count': 0, 'class': 'without_clue'}
    with store.transaction():
        people = store.all('SELECT m.* FROM maintainers m JOIN project_maintainers pm ON pm.maintainer_id=m.id WHERE pm.project_id=?', (project_id,))
        for person in people:
            sync_person(store, person)
        store.conn.execute('DELETE FROM contact_points WHERE project_id=?', (project_id,))
        metadata = json.loads(project['metadata'])
        if homepage := web_url(metadata.get('homepage')):
            put_point(store, project_id, None, 'website', homepage, 'repository', project['url'], 'project_entry', {}, 'repository', project['url'])
        snapshot = store.one('SELECT * FROM snapshots WHERE id=? AND project_id=?', (snapshot_id, project_id)) if snapshot_id is not None else store.one('SELECT * FROM snapshots WHERE project_id=? AND sha=? ORDER BY id DESC LIMIT 1', (project_id, project['head_sha']))
        if snapshot:
            evidence = json.loads(snapshot['evidence'])
            readme = evidence.get('readme_text', '')
            readme = readme if isinstance(readme, str) else ''
            source = project['url'] + '/tree/' + quote(project['head_sha'], safe='') + '#readme'
            # Explicit email links only. Text examples and commit authors are not contacts.
            for candidate in set(re.findall(r'(?<![\w.+-])([A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63})(?![\w.-])', readme[:40000], re.I)):
                if address := email_address(candidate):
                    put_point(store, project_id, None, 'email', address, 'readme', source, 'project_entry', {'sha': project['head_sha']}, 'readme', source)
            for label, link in re.findall(r'\[([^\]\n]{1,120})\]\((https?://[^\s)]+)\)', readme[:40000]):
                if re.search(r'contact|联系|discord|discussions|support|community|社区', label, re.I) and (url := web_url(link)):
                    put_point(store, project_id, None, 'website', url, 'readme', source, 'project_entry', {'label': label, 'sha': project['head_sha']}, 'readme', source)
            # Issue bodies and public comments are high-weight explicit project
            # sources.  Only explicit mailto/URLs are accepted; author emails
            # and guessed addresses are intentionally ignored.
            issue_items = evidence.get('issues_pr_sample') or []
            issue_comments = evidence.get('issue_comments_sample') or {}
            for issue in issue_items:
                if not isinstance(issue, dict) or issue.get('pull_request'):
                    continue
                number = issue.get('number')
                issue_url = issue.get('html_url') or (project['url'] + '/issues/' + str(number))
                texts = [issue.get('body') or '']
                if number is not None:
                    texts.extend((comment or {}).get('body', '') for comment in issue_comments.get(str(number), [])
                                 if isinstance(comment, dict))
                for text in texts:
                    for candidate in set(re.findall(r'mailto:([\s<>")\]?]+)', text, re.I)):
                        if address := email_address(candidate):
                            put_point(store, project_id, None, 'email', address, 'issues', issue_url,
                                      'project_issue', {'issue': number, 'sha': project['head_sha']}, 'issues', issue_url)
                    for label, link in re.findall(r'\[([^\]\n]{1,120})\]\((https?://[^\s)]+)\)', text):
                        if re.search(r'contact|联系|discord|discussions|support|community|社区', label, re.I) and (url := web_url(link)):
                            put_point(store, project_id, None, 'website', url, 'issues', issue_url,
                                      'project_issue', {'issue': number, 'label': label, 'sha': project['head_sha']}, 'issues', issue_url)
            snapshot_id = snapshot['id'] if snapshot else None
            if snapshot_id is not None:
                points = store.all('SELECT source_kind,confidence FROM contact_points WHERE project_id=?', (project_id,))
                maintainers = store.all('SELECT do_not_contact FROM maintainers m JOIN project_maintainers pm ON pm.maintainer_id=m.id WHERE pm.project_id=?', (project_id,))
                blocked = any(row['do_not_contact'] for row in maintainers)
                kinds = sorted({row['source_kind'] for row in points if row['source_kind'] in ('readme','profile','issues','csv')})
                direct = any(row['source_kind'] in ('readme','profile','issues') and row['confidence'] != 'unverified' for row in points)
                classification = 'do_not_contact' if blocked else ('with_clue' if direct else 'without_clue')
                has_author = any(row['source_kind'] == 'profile' and row['confidence'] == 'public_profile' for row in points)
                has_email = any(row['source_kind'] in ('readme','issues') and row['confidence'] in ('project_entry','project_issue') for row in points)
                has_community = any(row['channel'] == 'website' and row['source_kind'] in ('readme','issues') for row in store.all('SELECT channel,source_kind FROM contact_points WHERE project_id=?', (project_id,)))
                priority = 0 if blocked else (100 if has_author else 80 if has_email else 60 if has_community else 20)
                store.conn.execute('''INSERT OR REPLACE INTO contact_summaries
                    (project_id,snapshot_id,class,source_mask,point_count,priority,rationale,created_at)
                    VALUES(?,?,?,?,?,?,?,?)''',
                    (project_id, snapshot_id, classification, ','.join(kinds), len(points), priority,
                     'Explicit public contact evidence from README, profile or issues' if direct else 'No explicit high-weight public contact clue', now()))
    return {'project_id': project_id, 'eligible': True, 'count': len(for_project(store, project_id)['items'])}


def for_project(store, project_id):
    project = store.one('SELECT * FROM projects WHERE id=?', (project_id,))
    if not project:
        raise ValueError('Project not found')
    eligible = qualification_status(store, project)['eligible'] and project['stage'] not in ('hold', 'rejected')
    items = store.all('''SELECT cp.*,json_extract(cp.evidence,'$.source_file') AS source_file,
        json_extract(cp.evidence,'$.source_row') AS source_row,m.login,m.do_not_contact,pm.confirmed,pm.role FROM contact_points cp
        LEFT JOIN maintainers m ON m.id=cp.maintainer_id
        LEFT JOIN project_maintainers pm ON pm.maintainer_id=cp.maintainer_id AND pm.project_id=?
        WHERE cp.project_id=? OR (cp.project_id IS NULL AND pm.project_id=?)
        ORDER BY cp.maintainer_id,cp.channel,cp.source,cp.value''', (project_id, project_id, project_id)) if eligible else []
    return {'eligible': bool(eligible), 'items': items}


def import_leads(store, text, filename, actor):
    if not isinstance(filename, str) or not filename.strip() or len(filename) > 200:
        raise ValueError('A contact CSV filename is required (max 200 characters)')
    filename = Path(filename.replace('\\', '/')).name
    records, stats = parse_leads(text)
    sha = hashlib.sha256(text.encode()).hexdigest()
    added = 0
    with store.transaction():
        for login, email, row in records:
            added += store.conn.execute('''INSERT OR IGNORE INTO contact_leads
                (login,email,source_file,source_sha256,source_row,imported_at) VALUES(?,?,?,?,?,?)''',
                (login, email, filename, sha, row, now())).rowcount
        stats['inserted'] = added
        stats['matched_accounts'] = store.one('''SELECT COUNT(DISTINCT m.id) AS n FROM maintainers m
            JOIN contact_leads l ON l.login=m.login COLLATE NOCASE WHERE l.source_sha256=?''', (sha,))['n']
        # Populate contacts for already selected projects as well as future selections.
        if added:
            for p in store.all('SELECT id FROM projects'):
                sync_project(store, p['id'])
        store.event('contact_leads', sha, 'import', actor, {'filename': filename, **stats})
    return stats


def bootstrap_files(store, paths, actor='deployment-seed'):
    """Idempotently import operator-provided CSVs shipped with a deployment."""
    results = []
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        results.append(import_leads(store, path.read_text(encoding='utf-8-sig'), path.name, actor))
    return results
