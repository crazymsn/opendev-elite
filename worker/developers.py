"""Source-backed GitHub entities; no inferred talent scores or identity merging."""
import json
import re
from urllib.parse import quote

from .projects import list_projects


HISTORY = {
    'contacts': ("contacts r JOIN projects p ON p.id=r.project_id", 'r.maintainer_id',
                 'r.id,r.project_id,p.full_name,r.status,r.actor,r.created_at'),
    'interviews': ("interviews r JOIN contacts c ON c.id=r.contact_id JOIN projects p ON p.id=r.project_id", 'c.maintainer_id',
                   'r.id,r.project_id,p.full_name,r.profile,r.actor,r.created_at'),
    'grants': ("grants r JOIN decisions d ON d.id=r.decision_id JOIN interviews i ON i.id=d.interview_id JOIN contacts c ON c.id=i.contact_id JOIN projects p ON p.id=d.project_id", 'c.maintainer_id',
               'r.id,d.project_id,p.full_name,r.status,r.updated_at'),
    'followups': ("followups r JOIN grants g ON g.id=r.grant_id JOIN decisions d ON d.id=g.decision_id JOIN interviews i ON i.id=d.interview_id JOIN contacts c ON c.id=i.contact_id JOIN projects p ON p.id=d.project_id", 'c.maintainer_id',
                  'r.id,d.project_id,p.full_name,r.status,r.due_at'),
}


def parameter(query, key, default=''):
    values = query.get(key, [default])
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str):
        raise ValueError('Developer query parameters must occur exactly once')
    return values[0]


def pagination(query, allowed=()):
    if set(query) - {'page', 'page_size', *allowed}:
        raise ValueError('Unknown developer query parameter')
    values = [parameter(query, 'page', '1'), parameter(query, 'page_size', '15')]
    if any(not re.fullmatch(r'[1-9][0-9]{0,5}', value) for value in values):
        raise ValueError('Invalid developer pagination')
    page, size = map(int, values)
    if page > 100000 or size > 200:
        raise ValueError('Invalid developer pagination')
    return page, size


def public_entity(row):
    try:
        profile = json.loads(row['profile'])
    except (ValueError, TypeError):
        profile = {}
    if not isinstance(profile, dict):
        profile = {}
    result = {key: row[key] for key in ('id', 'login', 'kind')}
    # Explicit projection: never expose email, raw payloads or contact preferences.
    result.update({key: profile.get(key) if isinstance(profile.get(key), str) else None
                   for key in ('name', 'bio', 'company', 'blog')})
    result['url'] = 'https://github.com/' + quote(row['login'], safe='')
    return result


def listing(store, query):
    page, size = pagination(query, ('q', 'kind', 'relationship'))
    term, kind, relationship = (parameter(query, key).strip() for key in ('q', 'kind', 'relationship'))
    if len(term) > 200 or kind not in ('', 'User', 'Organization') or relationship not in ('', 'confirmed', 'candidate'):
        raise ValueError('Invalid developer filter')
    clauses, params = [], []
    if term:
        clauses.append("""(instr(lower(m.login),lower(?))>0 OR instr(lower(COALESCE(json_extract(m.profile,'$.name'),'')),lower(?))>0
            OR EXISTS (SELECT 1 FROM project_maintainers pm JOIN projects p ON p.id=pm.project_id
                       WHERE pm.maintainer_id=m.id AND instr(lower(p.full_name),lower(?))>0))""")
        params.extend([term] * 3)
    if kind:
        clauses.append('m.kind=?'); params.append(kind)
    if relationship:
        clauses.append('EXISTS (SELECT 1 FROM project_maintainers pm WHERE pm.maintainer_id=m.id AND pm.confirmed=?)')
        params.append(1 if relationship == 'confirmed' else 0)
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    count = store.one('SELECT COUNT(*) AS n FROM maintainers m' + where, params)['n']
    rows = store.all('''SELECT m.*,
        (SELECT COUNT(*) FROM project_maintainers WHERE maintainer_id=m.id) AS project_count,
        (SELECT COUNT(*) FROM project_maintainers WHERE maintainer_id=m.id AND confirmed=1) AS confirmed_count
        FROM maintainers m''' + where + ' ORDER BY m.login COLLATE NOCASE,m.id LIMIT ? OFFSET ?', params + [size, (page-1)*size])
    return {'items': [dict(public_entity(row), project_count=row['project_count'], confirmed_count=row['confirmed_count']) for row in rows],
            'total': count, 'page': page, 'page_size': size}


def detail(store, row, query, visible_records):
    page, size = pagination(query)
    projects = list_projects(store, {'maintainer_id': [str(row['id'])], 'page': [str(page)], 'page_size': [str(size)], 'sort': ['name']})
    relations = {r['project_id']: r for r in store.all('SELECT project_id,role,confirmed FROM project_maintainers WHERE maintainer_id=?', (row['id'],))}
    for project in projects['items']:
        project['relationship'] = relations[project['id']]
    result = {'entity': public_entity(row), 'projects': projects,
              'history_kinds': [kind for kind in HISTORY if kind in visible_records]}
    if 'contacts' in visible_records:
        result['contact_preference'] = 'declined' if row['do_not_contact'] else 'not_declined'
    if 'interviews' in visible_records:
        latest = store.one('''SELECT i.id,i.project_id,p.full_name,i.profile,i.actor,i.created_at
            FROM interviews i JOIN contacts c ON c.id=i.contact_id JOIN projects p ON p.id=i.project_id
            WHERE c.maintainer_id=? ORDER BY i.id DESC LIMIT 1''', (row['id'],))
        result['latest_interview'] = latest
    return result


def history(store, identifier, kind, query):
    page, size = pagination(query)
    joins, column, projection = HISTORY[kind]
    where = ' WHERE ' + column + '=?'
    count = store.one('SELECT COUNT(*) AS n FROM ' + joins + where, (identifier,))['n']
    rows = store.all('SELECT ' + projection + ' FROM ' + joins + where + ' ORDER BY r.rowid DESC LIMIT ? OFFSET ?',
                     (identifier, size, (page-1)*size))
    return {'items': rows, 'total': count, 'page': page, 'page_size': size, 'kind': kind}
