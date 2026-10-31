"""Role-filtered, paginated inbox derived from the operational summary."""
import re

from . import report


KIND_ROLES = {
    'unassigned_projects': ('reviewer', 'operator'),
    'awaiting_collection': ('operator',),
    'pending_selection': ('reviewer', 'operator'),
    'pending_technical_reviews': ('reviewer',),
    'pending_experiences': ('reviewer',),
    'running_experiences': ('reviewer',),
    'stale_experiences': ('reviewer',),
    'ready_for_contact': ('operator',),
    'blocked_contacts': ('operator',),
    'due_followups': ('operator', 'reviewer'),
    'dead_jobs': ('operator', 'auditor'),
}


class InboxFailure(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def available_kinds(principal):
    return [kind for kind, roles in KIND_ROLES.items() if principal.allows(*roles)]


def _value(query, key, default):
    values = query.get(key, [default])
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str):
        raise InboxFailure(400, 'Inbox query parameters must occur exactly once')
    return values[0]


def _pagination(query):
    values = [_value(query, 'page', '1'), _value(query, 'page_size', '15')]
    if any(not re.fullmatch(r'[1-9][0-9]{0,5}', value) for value in values):
        raise InboxFailure(400, 'Inbox page and page_size must be positive integers')
    page, size = map(int, values)
    if page > 100000 or size > 200:
        raise InboxFailure(400, 'Inbox page must be 1..100000 and page_size 1..200')
    return page, size


def _record_order(item):
    value = item['id']
    return (item.get('due_at', ''), (0, value) if type(value) is int else (1, str(value)))


def _project_id(value):
    return value if type(value) is int and value > 0 else None


def _item(kind, row, projects, followup_projects):
    record_id = row['id']
    project_id = _project_id(row.get('project_id'))
    if kind in ('ready_for_contact', 'blocked_contacts'):
        project_id = _project_id(row.get('project_id', record_id))
    if kind == 'due_followups':
        project_id = _project_id(followup_projects.get(record_id))
    name = projects.get(project_id, row.get('full_name') or '项目')
    if kind in report.EARLY_WORK:
        _, zh, en, tab, reason, reason_en = report.EARLY_WORK[kind]
        return {'id': record_id, 'project_id': project_id, 'title': f'{zh} · {name}',
                'title_en': f'{en} · {name}', 'reason': reason, 'reason_en': reason_en,
                'href': '#projects/' + str(project_id) + ('/' + tab if tab else '')}
    if kind == 'pending_experiences':
        agent = row.get('audience') == 'agent'
        title = f"{'Agent' if agent else '团队'}体验 · {name}"
        reason = '当前版本已通过第一轮审核，等待 Agent 领取执行。' if agent else '当前版本已通过第一轮审核，等待团队实际体验并记录结果。'
        href = '#experiences'
    elif kind == 'running_experiences':
        title = f'执行中的 Agent 体验 · {name}'
        reason = 'Agent 已持有有效租约；等待本次执行结果，不应重复领取。'
        href = '#experiences'
    elif kind == 'stale_experiences':
        title = f'更新体验条件 · {name}'
        reason = row['reason']
        href = '#experiences'
    elif kind == 'ready_for_contact':
        title = f'起草作者联系 · {name}'
        reason = '当前两轮审核与作者联系资格满足，可以起草联系记录。'
        href = '#projects/' + str(project_id) if project_id else '#experiences'
    elif kind == 'blocked_contacts':
        title = f'补齐联系条件 · {name}'
        reason = row['reason']
        href = '#projects/' + str(project_id) if project_id else '#experiences'
    elif kind == 'due_followups':
        title = f'到期赞助复盘 · {name}'
        reason = '计划复盘日期 ' + str(row['due_at']) + ' 已到，请记录使用情况、里程碑和后续决定。'
        href = '#followups'
    else:
        title = '处理失败采集任务 #' + str(record_id)
        reason = row.get('error') or '采集任务已进入失败队列，需要检查后重试。'
        href = '#operations'
    name_en = projects.get(project_id, row.get('full_name') or 'Project')
    title_en = {
        'pending_experiences': f"{'Agent' if row.get('audience') == 'agent' else 'Team'} experience · {name_en}",
        'running_experiences': f'Agent experience in progress · {name_en}',
        'stale_experiences': f'Update experience prerequisites · {name_en}',
        'ready_for_contact': f'Draft author contact · {name_en}',
        'blocked_contacts': f'Complete contact prerequisites · {name_en}',
        'due_followups': f'Sponsorship follow-up due · {name_en}',
        'dead_jobs': f'Resolve failed collection job #{record_id}',
    }[kind]
    reason_en = {
        'pending_experiences': ('The current version passed technical review. An Agent can claim the task.'
            if row.get('audience') == 'agent' else 'The current version passed technical review. The team must test it and record the results.'),
        'running_experiences': 'An Agent holds an active lease. Await its results; do not claim the task again.',
        'stale_experiences': row.get('reason', ''),
        'ready_for_contact': 'Both current reviews and maintainer eligibility pass. A contact draft can be prepared.',
        'blocked_contacts': row.get('reason', ''),
        'due_followups': f"The planned review date {row.get('due_at', '')} has arrived. Record usage, milestones, and the next decision.",
        'dead_jobs': row.get('error') or 'Collection failed. Investigate the job before retrying.',
    }[kind]
    # Links are synthesized from fixed local routes and positive numeric IDs.
    # Repository URLs, errors, notes, and other stored text never become hrefs.
    return {'id': record_id, 'project_id': project_id, 'title': str(title),
            'reason': str(reason), 'title_en': str(title_en), 'reason_en': str(reason_en), 'href': href}


def inbox(store, principal, query=None):
    query = query or {}
    if set(query) - {'kind', 'page', 'page_size'}:
        raise InboxFailure(400, 'Unknown inbox query parameter')
    kind = _value(query, 'kind', 'pending_experiences')
    if kind not in KIND_ROLES:
        raise InboxFailure(400, 'Unknown inbox kind')
    page, size = _pagination(query)
    visible = available_kinds(principal)
    if kind not in visible:
        raise InboxFailure(403, 'This identity cannot read the requested inbox category')
    # Summary, counts, page contents, and project bindings come from one SQLite
    # read snapshot. The inbox never repairs eligibility or mutates business state.
    owns_snapshot = not store.conn.in_transaction
    if owns_snapshot:
        store.conn.execute('BEGIN')
    try:
        source = report.summary(store)
        counts = {name: len(source[name]) for name in visible}
        records = sorted(source[kind], key=_record_order)
        total = len(records)
        records = records[(page - 1) * size:page * size]
        projects = {row['id']: row['full_name'] for row in store.all('SELECT id,full_name FROM projects')}
        followup_projects = {}
        if kind == 'due_followups':
            followup_projects = {row['id']: row['project_id'] for row in store.all(
                'SELECT f.id,d.project_id FROM followups f JOIN grants g ON g.id=f.grant_id JOIN decisions d ON d.id=g.decision_id')}
        return {'items': [_item(kind, row, projects, followup_projects) for row in records],
                'counts': counts, 'kind': kind, 'page': page, 'page_size': size, 'total': total}
    finally:
        if owns_snapshot:
            store.conn.execute('ROLLBACK')
