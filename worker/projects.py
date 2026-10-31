"""Bounded repository queries and shared, read-only workflow readiness."""
import hashlib
import re
import datetime as dt
import json

from .criteria import CATEGORIES, evaluate_qualification, qualification_status
from .workflow import Workflow, iso_date

STAGES = ('discovered','technical_review','experience','ready_contact','contacted','interviewed','approved','sponsored','closed','hold','rejected')


def _parameter(query,key,default=''):
    values=query.get(key,[default])
    if len(values)!=1 or not isinstance(values[0],str):
        raise ValueError('Project query parameters must occur exactly once')
    return values[0]


def list_projects(store,query):
    if set(query)-{'page','page_size','q','stage','category','selection','audience','sort','maintainer_id','contact_class','source','_scope_project_ids'}:
        raise ValueError('Unknown project query parameter')
    page,size=_parameter(query,'page','1'),_parameter(query,'page_size','50')
    if not re.fullmatch('[1-9][0-9]{0,5}',page) or not re.fullmatch('[1-9][0-9]{0,2}',size) or int(page)>100000 or int(size)>200:
        raise ValueError('Invalid project pagination')
    page,size=int(page),int(size)
    term=_parameter(query,'q').strip()
    if len(term)>200:
        raise ValueError('Search string too long')
    stage,category,selection,audience,sort=(_parameter(query,key) for key in ('stage','category','selection','audience','sort'))
    contact_class,source=_parameter(query,'contact_class'),_parameter(query,'source')
    if stage and stage not in STAGES or category and category not in CATEGORIES or selection and selection not in ('unassessed','include','hold','exclude','stale') or audience and audience not in ('unknown','human','agent','both') or sort and sort not in ('updated','name','stars','priority') or contact_class and contact_class not in ('with_clue','without_clue','do_not_contact') or source and len(source)>120:
        raise ValueError('Invalid project filter')
    captured=dt.datetime.now(dt.timezone.utc)
    def state(head,audience,sha,policy,q_audience,scope,maintenance,outcome,created,evidence):
        if sha is None:
            return 'unassessed'
        result=evaluate_qualification({'head_sha':head,'audience':audience},{'sha':sha,'policy_version':policy,'audience':q_audience,
            'scope_fit':scope,'maintenance':maintenance,'outcome':outcome,'created_at':created,'evidence':evidence},current=captured)
        return outcome if result['reason'] in (None,'qualification_not_selected') else 'stale'
    store.conn.create_function('selection_state',10,state)
    store.conn.create_function('text_sha256',1,lambda value:hashlib.sha256((value or '').encode()).hexdigest(),deterministic=True)
    select_state='selection_state(p.head_sha,p.audience,q.sha,q.policy_version,q.audience,q.scope_fit,q.maintenance,q.outcome,q.created_at,q.evidence)'
    joins=''' FROM projects p
        LEFT JOIN qualifications q ON q.id=(SELECT MAX(id) FROM qualifications WHERE project_id=p.id)
        LEFT JOIN dossier_translations zh ON zh.id=(SELECT MAX(id) FROM dossier_translations WHERE project_id=p.id AND field='description' AND language='zh-CN')
        LEFT JOIN dossier_translations en ON en.id=(SELECT MAX(id) FROM dossier_translations WHERE project_id=p.id AND field='description' AND language='en')'''
    latest_contact="(SELECT class FROM contact_summaries WHERE project_id=p.id ORDER BY created_at DESC LIMIT 1)"
    latest_contact_priority="COALESCE((SELECT priority FROM contact_summaries WHERE project_id=p.id ORDER BY created_at DESC LIMIT 1),0)"
    latest_rank_weight="COALESCE((SELECT MAX(weight) FROM project_rankings WHERE project_id=p.id),0)"
    latest_rank="(SELECT rank FROM project_rankings WHERE project_id=p.id ORDER BY captured_at DESC,id DESC LIMIT 1)"
    current_zh="CASE WHEN zh.source_sha256=text_sha256(json_extract(p.metadata,'$.description')) THEN zh.content END"
    current_en="CASE WHEN en.source_sha256=text_sha256(json_extract(p.metadata,'$.description')) THEN en.content END"
    clauses,params=[],[]
    maintainer_id=_parameter(query,'maintainer_id')
    if 'maintainer_id' in query:
        if not re.fullmatch(r'[1-9][0-9]{0,17}',maintainer_id):
            raise ValueError('Invalid maintainer_id filter')
        clauses.append('EXISTS (SELECT 1 FROM project_maintainers pm WHERE pm.project_id=p.id AND pm.maintainer_id=?)')
        params.append(int(maintainer_id))
    for value,column in ((stage,'p.stage'),(category,'q.category'),(audience,'p.audience'),(selection,select_state)):
        if value:
            clauses.append(column+'=?');params.append(value)
    if contact_class:
        clauses.append(latest_contact+'=?'); params.append(contact_class)
    if source:
        clauses.append('EXISTS (SELECT 1 FROM sources src WHERE src.project_id=p.id AND instr(lower(src.source),lower(?))>0)'); params.append(source)
    if term:
        clauses.append("(instr(lower(p.full_name),lower(?))>0 OR instr(lower(COALESCE(json_extract(p.metadata,'$.description'),'')),lower(?))>0 OR instr(COALESCE("+current_zh+",''),?)>0 OR instr(lower(COALESCE("+current_en+",'')),lower(?))>0)")
        params.extend([term]*4)
    scope_ids = query.get('_scope_project_ids')
    if scope_ids is not None:
        if not isinstance(scope_ids, (list, tuple, set)) or not all(type(x) is int and x > 0 for x in scope_ids):
            raise ValueError('Invalid project scope')
        if not scope_ids:
            clauses.append('1=0')
        else:
            clauses.append('p.id IN (' + ','.join('?' for _ in scope_ids) + ')')
            params.extend(scope_ids)
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    columns=f"""SELECT p.id,p.uid,p.full_name,p.url,p.head_sha,p.audience,p.stage,p.owner,p.discovered_at,p.refreshed_at,
        json_extract(p.metadata,'$.description') AS description,json_extract(p.metadata,'$.stargazers_count') AS stars,
        json_extract(p.metadata,'$.language') AS language,json_extract(p.metadata,'$.pushed_at') AS pushed_at,
        q.category,q.maintenance,{select_state} AS selection,{current_zh} AS description_zh,{current_en} AS description_en,
        {latest_contact} AS contact_class,{latest_contact_priority} AS contact_priority,
        {latest_rank_weight} AS ranking_weight,{latest_rank} AS ranking_rank,
        ({latest_contact_priority}+{latest_rank_weight}) AS priority_score"""
    order={'name':'p.full_name COLLATE NOCASE,p.id','stars':"COALESCE(json_extract(p.metadata,'$.stargazers_count'),0) DESC,p.id",
           'priority':f'priority_score DESC,p.refreshed_at DESC,p.id',
           'updated':'p.refreshed_at DESC,p.id'}.get(sort,'p.refreshed_at DESC,p.id')
    # Count and page share one snapshot, including source-fingerprint checks.
    own=not store.conn.in_transaction
    if own:store.conn.execute('BEGIN')
    try:
        count=store.one('SELECT COUNT(*) AS n'+joins+where,params)['n']
        rows=store.all(columns+joins+where+' ORDER BY '+order+' LIMIT ? OFFSET ?',params+[size,(page-1)*size])
        return {'items':rows,'total':count,'page':page,'page_size':size}
    finally:
        if own:store.conn.execute('ROLLBACK')


def _next_step(store, workflow, project, qualification, first, second, evidence_error):
    """Prioritize existing obligations before prerequisites for new decisions."""
    def step(name, kind=None, identifier=None, due=None):
        return {'next_step':name, 'next_record':{'kind':kind,'id':identifier} if kind else None,
                'next_review_at':due}

    def evaluation_step():
        if not project['owner'] or project['audience']=='unknown':
            return step('assign')
        metadata=json.loads(project['metadata'])
        if metadata.get('archived') or metadata.get('disabled'):
            return step('reconsider')
        if qualification['reason']=='qualification_not_selected':
            return step('selection-hold' if qualification['assessment']['outcome']=='hold' else 'selection-excluded')
        if evidence_error:
            return step('refresh-project')
        if not qualification['eligible']:
            return step('qualify')
        if not first:
            return step('review-one')
        if not second:
            return step('experience')
        return None

    active=store.one("""SELECT g.*,d.interview_id,d.project_id,d.terms FROM grants g
        JOIN decisions d ON d.id=g.decision_id WHERE d.project_id=?
        AND g.status NOT IN ('closed','cancelled') ORDER BY d.rowid DESC LIMIT 1""",(project['id'],))
    if active:
        status=active['status']
        if status in ('submitting','unknown'):
            return step('credits-reconcile','grants',active['id'])
        if status=='awaiting_claim':
            return step('credits-awaiting-claim','grants',active['id'])
        if status=='issued':
            followup=store.one("SELECT * FROM followups WHERE grant_id=? AND status='pending' ORDER BY due_at,id LIMIT 1",(active['id'],))
            if not followup:
                return {**step('followup-plan','grants',active['id']),'followup_grant_id':active['id']}
            today=dt.datetime.now(dt.timezone.utc).date().isoformat()
            return step('followup-due' if followup['due_at']<=today else 'followup-wait',
                        'followups',followup['id'],followup['due_at'])
        prerequisite=evaluation_step()
        if prerequisite:
            return prerequisite
        try:
            workflow.sponsorship_consent(active)
        except ValueError:
            return step('consent')
        try:
            expired=iso_date(json.loads(active['terms']).get('expires_at'))<=dt.datetime.now(dt.timezone.utc).date()
        except (ValueError, TypeError, AttributeError):
            expired=True
        if expired:
            return step('decision-expired','grants',active['id'])
        return step('credits-plan','grants',active['id'])

    decision=store.one('SELECT d.*,g.status AS grant_status FROM decisions d LEFT JOIN grants g ON g.decision_id=d.id WHERE d.project_id=? ORDER BY d.rowid DESC LIMIT 1',(project['id'],))
    if decision:
        if decision['outcome'] in ('hold','reject'):
            return step('decision-hold' if decision['outcome']=='hold' else 'decision-rejected')
        if decision['grant_status'] in ('closed','cancelled'):
            return step('cycle-closed' if decision['grant_status']=='closed' else 'sponsorship-cancelled')

    prerequisite=evaluation_step()
    if prerequisite:
        return prerequisite
    interview=store.one('SELECT * FROM interviews WHERE project_id=? ORDER BY id DESC LIMIT 1',(project['id'],))
    if interview:
        try:
            workflow.sponsorship_consent({'project_id':project['id'],'interview_id':interview['id']})
        except ValueError:
            return step('consent')
        return step('decision')
    contact=store.one("SELECT * FROM contacts WHERE project_id=? AND status IN ('draft','sent','replied') ORDER BY rowid DESC LIMIT 1",(project['id'],))
    if contact:
        return step({'draft':'contact-send','sent':'awaiting-reply','replied':'interview'}[contact['status']], 'contacts',contact['id'])
    previous=store.one('SELECT status FROM contacts WHERE project_id=? ORDER BY rowid DESC LIMIT 1',(project['id'],))
    if previous:
        return step('contact-declined' if previous['status']=='declined' else 'contact-closed')
    return step('contact')


def readiness(store,project):
    workflow=Workflow(store)
    qualification=qualification_status(store,project)
    evidence_error=None
    try:workflow.evidence_gate(project['id'])
    except ValueError as error:evidence_error=str(error)
    assigned=bool(project['owner']) and project['audience']!='unknown'
    first=not evidence_error and workflow.passed(project,1)
    second=first and workflow.passed(project,2)
    contact_error=None
    try:
        workflow.contact_gate(project['id'])
        workflow.contactable_maintainer(project['id'])
    except ValueError as error:contact_error=str(error)
    allowed={'assign':True,'qualify':assigned and bool(project['head_sha']),
        'review-one':assigned and qualification['eligible'] and not evidence_error,
        'experience-create':first and not evidence_error,'review-two':first and not evidence_error,
        'contact-draft':not contact_error}
    return {'allowed':allowed,**_next_step(store,workflow,project,qualification,first,second,evidence_error),
        'evidence_error':evidence_error,'contact_error':contact_error,
        'qualification_current':qualification['eligible'],'round_one_current':first,'round_two_current':second,
        'required_audiences':['human','agent'] if project['audience']=='both' else [project['audience']]}
