"""Single source of truth for sponsorship scope and evidence-based selection."""
import datetime as dt
import json
from pathlib import Path

SELECTION_POLICY = json.loads((Path(__file__).parent / "resources" / "selection-policy.json").read_text(encoding="utf-8"))

POLICY_VERSION = SELECTION_POLICY['version']
CATEGORIES = {
    'agent-infra': {'zh-CN':'Agent 基础设施', 'en':'Agent infrastructure'},
    'ai-coding': {'zh-CN':'AI 编程工具', 'en':'AI coding tools'},
    'rag': {'zh-CN':'RAG 与检索', 'en':'RAG and retrieval'},
    'routing-inference': {'zh-CN':'模型路由与推理', 'en':'Model routing and inference'},
    'applications': {'zh-CN':'AI 应用产品', 'en':'AI applications'},
    'agent-applications': {'zh-CN':'面向 Agent 的应用', 'en':'Applications for Agents'},
    'other-ai': {'zh-CN':'其他相关 AI 工具', 'en':'Other relevant AI tools'},
    'out-of-scope': {'zh-CN':'范围外项目', 'en':'Outside program scope'},
}
CRITERIA = [
    {'id':'scope', 'round':0, 'zh-CN':'面向真实场景的 AI 开发者工具、用户 AI 工具、Agent 基础设施及供 Agent 调用的应用', 'en':'AI developer tools, end-user AI tools, Agent infrastructure and applications callable by Agents for real tasks'},
    {'id':'maintenance', 'round':0, 'zh-CN':'持续维护，有提交、发布或维护者响应等证据；仅有更新时间不足以判定', 'en':'Ongoing maintenance supported by commits, releases, or maintainer responses; a timestamp alone is insufficient'},
    {'id':'structure', 'round':1, 'zh-CN':'工程结构与代码质量：核心实现、模块边界、可读性、关键测试、错误处理与权限边界；按项目规模评估', 'en':'Engineering: core implementation, boundaries, readability, critical tests, error handling and permissions, proportional to project size'},
    {'id':'maintenance', 'round':1, 'zh-CN':'Commit 与持续维护：有效变更、问题处理及维护节奏', 'en':'Commits and maintenance: meaningful changes, issue handling, and maintenance cadence'},
    {'id':'documentation', 'round':1, 'zh-CN':'文档与仓库规范：可复现安装/调用、输入输出、依赖、成本、限制、许可及贡献说明', 'en':'Documentation: reproducible setup/invocation, I/O, dependencies, costs, limitations, licensing and contribution guidance'},
    {'id':'real_problem', 'round':2, 'zh-CN':'真实问题：目标用户、任务和现有方案的不足', 'en':'Real problem: target users, tasks, and gaps in existing solutions'},
    {'id':'usability', 'round':2, 'zh-CN':'实际使用：团队或自有 Agent 运行真实任务及失败场景，记录成功率、质量、延迟、成本与权限行为；环境失败不等于产品失败', 'en':'Actual use: team members or owned Agents run real tasks and failure cases, recording success, quality, latency, cost and permission behavior; environment failure is not product failure'},
    {'id':'differentiation', 'round':2, 'zh-CN':'差异化：与替代方案比较，说明优势与局限', 'en':'Differentiation: compare alternatives and explain strengths and limitations'},
    {'id':'potential', 'round':2, 'zh-CN':'后续潜力：维护能力、需求反馈与可验证的下一阶段目标', 'en':'Future potential: maintenance capacity, user feedback, and verifiable next milestones'},
    {'id':'interview', 'round':3, 'zh-CN':'作者访谈：用户、投入、路线图、Credits 需求、里程碑及沟通同意', 'en':'Author interview: users, commitment, roadmap, Credits needs, milestones, and consent'},
]


def catalog():
    return {**SELECTION_POLICY, 'version':POLICY_VERSION, 'categories':CATEGORIES, 'criteria':CRITERIA,
        'principles':[
            {'zh-CN':'全球范围，个人、小团队及早期项目均可。', 'en':'Global scope; individuals, small teams, and early projects are welcome.'},
            {'zh-CN':'Stars、地域、融资和团队规模不作为统一淘汰门槛。', 'en':'Stars, geography, funding, and team size are not universal rejection gates.'},
            {'zh-CN':'搜索命中仅进入候选池；自动信号不能替代人工复核。', 'en':'Search hits enter the candidate pool; automated signals do not replace human review.'},
            {'zh-CN':'信息不足先暂缓，联系作者必须通过当前有效的前两轮筛选。', 'en':'Hold when evidence is insufficient; contact requires both current review rounds to pass.'}],
        'decisions': {'include':{'zh-CN':'符合范围，进入技术复核','en':'In scope; proceed to technical review'},
                      'hold':{'zh-CN':'待补证据','en':'More evidence needed'},
                      'exclude':{'zh-CN':'暂不入选','en':'Not selected'}}}


def qualification_status(store, project, max_age_days=30):
    row = store.one('SELECT * FROM qualifications WHERE project_id=? ORDER BY id DESC LIMIT 1', (project['id'],))
    return evaluate_qualification(project,row,max_age_days)


def evaluate_qualification(project, row, max_age_days=30, current=None):
    reason = 'qualification_missing'
    parsed_evidence = {}
    if row:
        # Qualification rows are operator supplied data and may be damaged by
        # an interrupted/manual database operation.  Keep the read path
        # fail-closed instead of allowing JSONDecodeError to escape from
        # project listing, readiness or workflow gates.
        try:
            parsed = json.loads(row.get('evidence', '{}'))
            parsed_evidence = parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            parsed_evidence = {}
        try:
            stamp = dt.datetime.fromisoformat(row['created_at'])
            current = current or dt.datetime.now(dt.timezone.utc)
            fresh = current-dt.timedelta(days=max_age_days) <= stamp <= current
        except (ValueError,TypeError):
            fresh = False
        if row['policy_version'] != POLICY_VERSION:
            reason = 'qualification_policy_changed'
        elif row['sha'] != project['head_sha'] or row['audience'] != project['audience']:
            reason = 'qualification_source_changed'
        elif not fresh:
            reason = 'qualification_expired'
        elif row['outcome'] != 'include' or row['scope_fit'] != 'yes' or row['maintenance'] != 'active':
            reason = 'qualification_not_selected'
        else:
            try:
                validate_qualification_evidence({**row, 'evidence': parsed_evidence})
                reason = None
            except (ValueError,TypeError):
                reason = 'qualification_evidence_incomplete'
        row = {**row, 'evidence': parsed_evidence}
    quality = None
    if row and row['policy_version'] == POLICY_VERSION:
        try:
            quality = quality_assessment(row['evidence'])
        except (ValueError,TypeError):
            pass
    return {'eligible':reason is None, 'reason':reason, 'assessment':row, 'quality':quality}


QUALIFICATION_MESSAGES = {
    'qualification_evidence_incomplete':'Selection evidence is incomplete under the current policy; reassess qualification',
    'qualification_missing':'Record project scope and maintenance qualification before proceeding',
    'qualification_policy_changed':'Selection criteria changed; reassess project qualification',
    'qualification_source_changed':'Project commit or audience changed; reassess qualification',
    'qualification_expired':'Project qualification expired; reassess current maintenance and scope',
    'qualification_not_selected':'Project is on hold or outside the current selection criteria',
}


def validate_qualification_evidence(data):
    """Validate accountable evidence structure; never infer truth from text or popularity."""
    evidence = data['evidence']
    if not isinstance(evidence, dict):
        raise ValueError('Qualification evidence must be an object')
    sources = evidence.get('sources')
    if not isinstance(sources, list) or not sources or any(not isinstance(s, str) or not s.strip() for s in sources):
        raise ValueError('Selection requires a list of verifiable evidence sources')
    outcome = data['outcome']
    code = evidence.get('reason_code')
    allowed = {'include':{'none'}, 'hold':{r['id'] for r in SELECTION_POLICY['hold_reasons']},
               'exclude':{r['id'] for r in SELECTION_POLICY['exclusion_reasons']}}
    if not isinstance(code, str) or code not in allowed[outcome]:
        raise ValueError('Selection reason must match include, hold or exclude')
    kind = evidence.get('maintenance_kind', 'unknown')
    if not isinstance(kind, str) or kind not in {k['id'] for k in SELECTION_POLICY['maintenance_kinds']}:
        raise ValueError('Invalid maintenance evidence kind')
    if data['maintenance'] == 'active':
        if kind not in ('commit', 'release', 'response', 'mixed'):
            raise ValueError('Active maintenance requires meaningful changes, releases or responses, not timestamps alone')
        date = evidence.get('maintenance_date')
        try:
            if not isinstance(date, str) or len(date) != 10:
                raise ValueError()
            stamp = dt.date.fromisoformat(date)
            if stamp > dt.datetime.now(dt.timezone.utc).date():
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Maintenance evidence requires a valid observation date that is not in the future') from None
    if code == 'outside_scope' and data['scope_fit'] != 'no':
        raise ValueError('Outside-scope exclusion requires a negative scope assessment')
    if code == 'abandoned' and data['maintenance'] != 'inactive':
        raise ValueError('Abandonment exclusion requires confirmed inactive maintenance')
    if outcome == 'include':
        for field in SELECTION_POLICY['assessment_fields']:
            value = evidence.get(field['name'])
            if not isinstance(value, str) or not value.strip():
                raise ValueError('Selection requires use-case, delivery and access/risk evidence')
        quality_assessment(evidence, require_pass=True)


def quality_assessment(evidence, require_pass=False):
    """Recompute the rubric from accountable inputs; never accept a client total."""
    rubric = SELECTION_POLICY['quality_rubric']
    scores = evidence.get('quality', {})
    if not isinstance(scores, dict):
        raise ValueError('Quality assessment must be an object')
    rows, total = [], 0
    for dimension in rubric['dimensions']:
        item = scores.get(dimension['id'], {})
        if not isinstance(item, dict):
            raise ValueError('Each quality dimension needs a score and evidence sources')
        score, sources = item.get('score'), item.get('sources')
        narrative = evidence.get(dimension['evidence_field'])
        valid = (type(score) is int and 0 <= score <= 4 and isinstance(sources,list)
                 and bool(sources) and all(isinstance(s,str) and bool(s.strip()) for s in sources)
                 and isinstance(narrative,str) and bool(narrative.strip()))
        if require_pass and not valid:
            raise ValueError('All five quality dimensions require an integer score, rationale and sources')
        points = score * dimension['weight'] / 4 if valid else 0
        rows.append({'id':dimension['id'],'score':score if valid else None,'weight':dimension['weight'],'points':points,'evidence_complete':bool(valid)})
        total += points
    dates = evidence.get('maintenance_dates', [])
    try:
        if not isinstance(dates,list) or len(dates)<2 or not all(isinstance(d,str) and len(d)==10 for d in dates):
            raise ValueError()
        stamps = {dt.date.fromisoformat(d) for d in dates}
        today = dt.datetime.now(dt.timezone.utc).date()
        maintenance_ok = (len(stamps)>=2 and all(today-dt.timedelta(days=365)<=d<=today for d in stamps)
                          and evidence.get('maintenance_date') in dates
                          and len({s.strip() for s in scores.get('maintenance',{}).get('sources',[])})>=2)
    except (ValueError,TypeError,AttributeError):
        maintenance_ok = False
    if require_pass and not maintenance_ok:
        raise ValueError('Inclusion requires maintenance on two distinct dates within 365 days and two distinct sources')
    passed = (all(r['evidence_complete'] and r['score']>=rubric['dimension_floor'] for r in rows)
              and total>=rubric['threshold'] and maintenance_ok)
    if require_pass and not passed:
        raise ValueError('Quality threshold not met: at least 80/100 and every dimension at least 3/4; hold for improvement')
    return {'total':total,'threshold':rubric['threshold'],'dimension_floor':rubric['dimension_floor'],
            'dimensions':rows,'maintenance_evidence_complete':bool(maintenance_ok),'passed':bool(passed)}
