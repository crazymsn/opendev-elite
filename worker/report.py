import datetime as dt
import json
import hashlib
import os
import time
from pathlib import Path
from .db import now
from .workflow import Workflow


EARLY_WORK = {
    'unassigned_projects': ('assign', '分配评测负责人和受众', 'Assign an owner and audience', '',
                            '指定负责人，并确认产品面向人、Agent 或两者。', 'Assign responsibility and identify the intended audience.'),
    'awaiting_collection': ('refresh-project', '补齐当前版本证据', 'Refresh current-version evidence', '',
                            '采集或刷新仓库资料，再检查入选条件。', 'Collect or refresh repository evidence before evaluating selection.'),
    'pending_selection': ('qualify', '完成入选评估', 'Assess selection criteria', 'selection',
                          '按真实场景、核心入口、维护及权限风险证据记录入选、暂缓或排除。', 'Assess use case, delivery, maintenance and access risks; record inclusion, hold or exclusion.'),
    'pending_technical_reviews': ('review-one', '完成第一轮技术复核', 'Complete technical review', 'reviews',
                                  '入选条件当前有效，核查核心实现、工程质量、维护和文档。', 'Selection is current. Review core implementation, engineering quality, maintenance and documentation.'),
}


def early_work(store):
    from .projects import readiness
    queues = {kind: [] for kind in EARLY_WORK}
    by_step = {value[0]: kind for kind, value in EARLY_WORK.items()}
    for project in store.all("SELECT * FROM projects WHERE stage NOT IN ('hold','rejected','closed')"):
        metadata = json.loads(project['metadata'])
        if metadata.get('archived') or metadata.get('disabled'):
            continue
        current = readiness(store, project)
        if current['next_step'] in by_step:
            queues[by_step[current['next_step']]].append({
                'id': project['id'], 'project_id': project['id'], 'full_name': project['full_name'], 'owner': project['owner']})
    return queues


def experience_stale_reason(store, workflow, experience):
    """Queue visibility uses the same eligibility facts as the Agent runner."""
    project = workflow.project(experience['project_id'])
    first = workflow.latest_review(project['id'], 1)
    if project['head_sha'] != experience['sha']:
        return 'Repository SHA changed; create a new experience after review'
    if not workflow.passed(project, 1):
        return 'First-round review is missing, expired or no longer passed'
    if experience['created_at'] < first['created_at']:
        return 'Experience predates the current first-round review'
    if project['audience'] not in (experience['audience'], 'both'):
        return 'Project audience no longer matches experience'
    try:
        workflow.evidence_gate(project['id'])
    except ValueError as exc:
        return str(exc)
    return None


def summary(store):
    w = Workflow(store)
    ready, blocked = [], []
    for project in store.all("SELECT * FROM projects WHERE stage='ready_contact'"):
        if store.one('SELECT id FROM decisions WHERE project_id=? LIMIT 1', (project['id'],)):
            continue
        if store.one("SELECT id FROM contacts WHERE project_id=? AND status IN ('draft','sent','replied')", (project['id'],)):
            continue
        try:
            w.contact_gate(project['id'])
            w.contactable_maintainer(project['id'])
            ready.append({'id': project['id'], 'full_name': project['full_name'], 'owner': project['owner']})
        except ValueError as exc:
            blocked.append({'id': project['id'], 'project_id': project['id'], 'full_name': project['full_name'],
                            'owner': project['owner'], 'reason': str(exc)})
    pending, running, stale = [], [], []
    for experience in store.all("SELECT * FROM experiences WHERE status='pending'"):
        item = {key: experience[key] for key in ('id', 'project_id', 'audience', 'sha')}
        try:
            reason = experience_stale_reason(store, w, experience)
        except ValueError as exc:
            reason = str(exc)
        if reason:
            stale.append(dict(item, reason=reason))
        else:
            lease = store.one("SELECT * FROM agent_leases WHERE experience_id=? AND state='running' AND lease_until>?", (experience['id'], time.time())) if experience['audience'] == 'agent' else None
            if lease:
                try:
                    task = json.loads(lease['task'])
                    if task.get('review_id') != w.latest_review(experience['project_id'], 1)['id']:
                        raise ValueError('Agent lease refers to an earlier first-round review')
                    running.append(dict(item, actor=lease['actor'], lease_until=lease['lease_until'], run_id=task.get('run_id')))
                except (ValueError, AttributeError) as exc:
                    stale.append(dict(item, reason=str(exc)))
            else:
                pending.append(item)
    token = os.environ.get('GITHUB_TOKEN', '').strip()
    token_scope = hashlib.sha256(token.encode()).hexdigest()[:16]
    provider = store.one("SELECT remaining,reset_at,next_allowed_at,updated_at FROM provider_limits WHERE provider='github' AND token_fingerprint=?", (token_scope,))
    timestamp = time.time()
    provider_blocked = bool(provider and float(provider['next_allowed_at'] or 0) > timestamp)
    queue_ready = 0 if provider_blocked else store.one("SELECT COUNT(*) AS count FROM jobs WHERE state='queued' AND available_at<=?", (timestamp,))['count']
    queue_delayed = store.one("SELECT COUNT(*) AS count FROM jobs WHERE state='queued' AND (available_at>? OR ?)", (timestamp, int(provider_blocked)))['count']
    selection = {row['outcome']: row['count'] for row in store.all('''SELECT outcome,COUNT(*) AS count FROM (
        SELECT q.outcome FROM qualifications q
        WHERE q.id=(SELECT MAX(q2.id) FROM qualifications q2 WHERE q2.project_id=q.project_id)
    ) GROUP BY outcome''')}
    workspace_projects = store.one('SELECT COUNT(*) AS count FROM projects')['count']
    collected = store.one('''SELECT COUNT(*) AS count FROM projects p
        WHERE EXISTS (
            SELECT 1 FROM snapshots s
            WHERE s.project_id=p.id
              AND s.id=(SELECT MAX(s2.id) FROM snapshots s2 WHERE s2.project_id=p.id)
              AND s.complete=1 AND s.sha=p.head_sha
        )''')['count']
    collection_pending = max(0, workspace_projects - collected)
    collection_jobs = store.all("SELECT state,COUNT(*) AS count FROM jobs WHERE kind IN ('search','trending','refresh') GROUP BY state")
    collection_job_counts = {row['state']: row['count'] for row in collection_jobs}
    github_rate_limit = {
        'token_configured': bool(token),
        'remaining': provider['remaining'] if provider else None,
        'reset_at': provider['reset_at'] if provider else None,
        'next_allowed_at': provider['next_allowed_at'] if provider else 0,
        'backoff_active': bool(provider and float(provider['next_allowed_at'] or 0) > time.time()),
        'updated_at': provider['updated_at'] if provider else None,
    }
    screened = sum(selection.get(key, 0) for key in ('include', 'hold', 'exclude'))
    contact_count = store.one('SELECT COUNT(*) AS count FROM contact_points')['count']
    contact_classes = {row['class']: row['count'] for row in store.all('SELECT class,COUNT(*) AS count FROM contact_summaries GROUP BY class')}
    ai_states = {row['status']: row['count'] for row in store.all('SELECT status,COUNT(*) AS count FROM ai_assessments GROUP BY status')}
    ranking_count = store.one('SELECT COUNT(DISTINCT project_id) AS count FROM project_rankings')['count']
    return {**early_work(store), 'generated_at': now(),
            'workspace_projects': workspace_projects,
            'collected_projects': collected,
            'collection_pending_projects': collection_pending,
            'collection_jobs': collection_jobs,
            'collection_jobs_done': collection_job_counts.get('done', 0),
            'collection_jobs_queued': collection_job_counts.get('queued', 0),
            'collection_jobs_running': collection_job_counts.get('running', 0),
            'collection_jobs_dead': collection_job_counts.get('dead', 0),
            'github_rate_limit': github_rate_limit,
            'screened_projects': screened,
            'selected_projects': selection.get('include', 0),
            'rejected_projects': selection.get('exclude', 0),
            'hold_projects': selection.get('hold', 0),
            'pending_screening_projects': max(0, collected - screened),
            'contact_points': contact_count,
            'contact_with_clue_projects': contact_classes.get('with_clue', 0),
            'contact_without_clue_projects': contact_classes.get('without_clue', 0),
            'contact_do_not_contact_projects': contact_classes.get('do_not_contact', 0),
            'contact_class_counts': contact_classes,
            'ranked_projects': ranking_count,
            'ai_assessments': ai_states,
            'discovery_runs': store.all('SELECT state,COUNT(*) AS count FROM discovery_runs GROUP BY state'),
            'selection_counts': selection,
            'projects': store.all('SELECT stage,COUNT(*) AS count FROM projects GROUP BY stage'),
            'jobs': store.all('SELECT state,COUNT(*) AS count FROM jobs GROUP BY state'),
            'queue_ready': queue_ready,
            'queue_delayed': queue_delayed,
            'coverage': store.all('SELECT status,COUNT(*) AS count FROM search_coverage GROUP BY status'),
            'ready_for_contact': ready,
            'blocked_contacts': blocked,
            'pending_experiences': pending,
            'running_experiences': running,
            'stale_experiences': stale,
            'grants': store.all('SELECT status,COUNT(*) AS count FROM grants GROUP BY status'),
            'due_followups': store.all("SELECT id,grant_id,due_at FROM followups WHERE status='pending' AND due_at<=? ORDER BY due_at", (dt.datetime.now(dt.timezone.utc).date().isoformat(),)),
            'dead_jobs': store.all("SELECT id,kind,error FROM jobs WHERE state='dead'"),
            'notice': '项目统计来自当前工作空间快照；已筛选只计当前最新入选评估，未评估项目不会计为淘汰。联系方式只来自用户 CSV 或 GitHub/项目公开页面，并保留来源和可信度。'
                    ' Stage counts are a current snapshot, not cohort conversion rates. Coverage is per search job/page.'}


def export_report(store, destination):
    report = summary(store)
    text = ['# 开发者赞助计划运行报告', '', '生成时间：' + report['generated_at'], '',
            '以下为当前快照；不将尚未评审的项目计为淘汰，不将邀请创建计为到账。', '']
    for name, label in [('projects','项目阶段'),('jobs','采集任务'),('coverage','检索覆盖'),
                        ('unassigned_projects','待分配负责人和受众'),('awaiting_collection','当前版本证据待补齐'),
                        ('pending_selection','待入选评估'),('pending_technical_reviews','待第一轮技术复核'),
                        ('ready_for_contact','满足两轮审核的候选'),('blocked_contacts','联系前需补齐的条件'),
                        ('pending_experiences','当前可执行体验'),('running_experiences','执行中的 Agent 体验'),
                        ('stale_experiences','需刷新或重建的体验'),
                        ('grants','赞助状态'),('due_followups','到期复盘'),('dead_jobs','失败任务')]:
        text += ['## ' + label, '', '```json', json.dumps(report[name], ensure_ascii=False, indent=2), '```', '']
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(text), encoding='utf-8')
    return str(path.resolve())
