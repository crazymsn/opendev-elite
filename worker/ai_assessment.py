"""Auditable project assessment worker.

The default provider is a deterministic, evidence-bound heuristic so the
deployment remains usable without a model credential. A future model adapter
can replace ``heuristic_v1`` while keeping the same immutable record and JSON
contract. Heuristic output is explicitly labelled and never changes a human
selection decision.
"""
import hashlib
import json
import re

from .db import dump, now

PROMPT_VERSION = 'rules-v1'


def _text(value):
    return value if isinstance(value, str) else ''


def _score(value):
    return max(0, min(4, int(value)))


def _tags(repo, evidence):
    metadata = repo if isinstance(repo, dict) else {}
    description = (_text(metadata.get('description')) + ' ' + _text(evidence.get('readme_text'))[:8000]).lower()
    topics = metadata.get('topics') or []
    words = ' '.join(str(x).lower() for x in topics) + ' ' + description
    tags = []
    patterns = {
        'agent-infra': r'\bagent(?:s)?\b|mcp|tool[- ]?calling',
        'ai-coding': r'coding agent|code agent|copilot|programming assistant',
        'rag': r'\brag\b|retrieval[- ]augmented|vector database',
        'routing': r'llm router|model routing|inference router',
        'inference': r'inference server|serving|model server',
        'developer-tool': r'developer tool|sdk|cli|developer experience',
        'application': r'web app|application|platform|workflow',
        'early-stage': r'prototype|early stage|experimental|alpha|beta',
    }
    for tag, pattern in patterns.items():
        if re.search(pattern, words, re.I):
            tags.append(tag)
    if not tags:
        tags.append('needs-evidence')
    return sorted(set(tags))


def _build_assessment(project, snapshot):
    repo = json.loads(project['metadata'])
    evidence = json.loads(snapshot['evidence'])
    signals = evidence.get('signals') or {}
    paths = evidence.get('code_sample_paths') or []
    commits = evidence.get('commits_90d_sample') or []
    issues = evidence.get('issues_pr_sample') or []
    releases = evidence.get('releases_sample') or []
    readme = bool(evidence.get('readme_text'))
    tests = bool(signals.get('test_paths_sample')) or any('test' in str(p).lower() for p in paths)
    ci = bool(signals.get('ci_paths'))
    maintenance = len(commits) >= 2 or len(releases) >= 1 or any(bool(x.get('comments')) for x in issues if isinstance(x, dict))
    dimensions = {
        'problem': _score(3 if repo.get('description') else 1),
        'engineering': _score(3 if len(paths) >= 3 and (tests or ci) else 2 if paths else 1),
        'maintenance': _score(3 if maintenance else 1),
        'differentiation': _score(3 if len(_tags(repo, evidence)) > 1 or repo.get('topics') else 2),
        'evaluability': _score(3 if readme and (tests or ci) else 2 if readme else 1),
    }
    weights = {'problem': 20, 'engineering': 25, 'maintenance': 20, 'differentiation': 20, 'evaluability': 15}
    total = round(sum(dimensions[k] / 4 * w for k, w in weights.items()), 2)
    tier = 'A' if total >= 80 and min(dimensions.values()) >= 3 else 'B' if total >= 60 else 'C'
    strengths = []
    weaknesses = []
    if readme:
        strengths.append('公开 README 可用于建立场景和评测入口')
    if tests:
        strengths.append('采集证据中发现测试或测试路径')
    if ci:
        strengths.append('采集证据中发现 CI 配置')
    if maintenance:
        strengths.append('近期开源维护证据可供人工复核')
    for key, value in dimensions.items():
        if value < 3:
            weaknesses.append(f'{key} 证据不足，需人工补证')
    if not weaknesses:
        weaknesses.append('规则评估未发现明显缺口，仍需人工复核')
    value = '可能解除研发、评测或部署瓶颈；需访谈确认具体 Credits 用途。' if total >= 60 else '当前证据不足以判断商业价值，应先补齐场景和交付证据。'
    refs = [
        {'kind': 'snapshot', 'sha': snapshot['sha'], 'field': 'repository'},
        {'kind': 'snapshot', 'sha': snapshot['sha'], 'field': 'signals'},
    ]
    return {
        'tags': [{'name': tag, 'confidence': 0.5, 'evidence_refs': [{'kind': 'snapshot', 'sha': snapshot['sha'], 'field': 'repository'}]} for tag in _tags(repo, evidence)], 'dimensions': dimensions, 'total_score': total, 'tier': tier,
        'strengths': strengths, 'weaknesses': weaknesses, 'business_value': value,
        'panorama': {'summary': _text(repo.get('description')) or '未提供项目简介', 'target_users': [], 'core_flow': [], 'risks': weaknesses, 'alternatives': [], 'contact_recommendation': '人工核验后决定', 'missing_evidence': weaknesses},
        'evidence_refs': refs,
        'uncertainty': '规则引擎建议，不等于人工评审；需要核对当前版本、维护日期和真实体验。',
    }


def assess_project_snapshot(store, project_id, snapshot_id, model='heuristic-v1', prompt_version=PROMPT_VERSION):
    project = store.one('SELECT * FROM projects WHERE id=?', (project_id,))
    snapshot = store.one('SELECT * FROM snapshots WHERE id=? AND project_id=?', (snapshot_id, project_id))
    if not project or not snapshot:
        raise ValueError('Project snapshot not found')
    current = store.one('SELECT head_sha FROM projects WHERE id=?', (project_id,))
    latest = store.one('SELECT sha FROM snapshots WHERE project_id=? AND complete=1 ORDER BY id DESC LIMIT 1', (project_id,))
    if not current or not latest or not (current['head_sha'] is None and latest['sha'] == snapshot['sha']) and (current['head_sha'] != snapshot['sha'] or latest['sha'] != snapshot['sha']):
        raise ValueError('Snapshot is stale; AI assessment must use the current complete snapshot')
    input_sha = hashlib.sha256((snapshot['sha'] + '\n' + snapshot['evidence']).encode()).hexdigest()
    assessment_id = 'ai_' + hashlib.sha256((str(project_id) + ':' + str(snapshot_id) + ':' + prompt_version).encode()).hexdigest()[:32]
    existing = store.one('SELECT id,status,total_score FROM ai_assessments WHERE id=?', (assessment_id,))
    if existing and existing['status'] == 'completed':
        return {'id': assessment_id, 'status': 'completed', 'duplicate': True, 'total_score': existing['total_score']}
    assessment = _build_assessment(project, snapshot)
    contact = store.one('SELECT class FROM contact_summaries WHERE project_id=? ORDER BY created_at DESC LIMIT 1', (project_id,))
    assessment['contactability'] = contact['class'] if contact else 'unknown'
    required = {'tags','dimensions','total_score','tier','strengths','weaknesses','business_value','panorama','contactability','evidence_refs','uncertainty'}
    if set(assessment) != required or not all(isinstance(item, dict) and 0 <= item.get('confidence', -1) <= 1 and item.get('evidence_refs') for item in assessment['tags']):
        raise ValueError('AI assessment failed schema validation')
    with store.transaction():
        store.conn.execute('''INSERT INTO ai_assessments
            (id,project_id,snapshot_id,model,prompt_version,input_sha256,status,tags,dimensions,total_score,tier,
             strengths,weaknesses,business_value,panorama,contactability,evidence_refs,uncertainty,created_at,completed_at,error)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET input_sha256=excluded.input_sha256,status=excluded.status,
              tags=excluded.tags,dimensions=excluded.dimensions,total_score=excluded.total_score,tier=excluded.tier,
              strengths=excluded.strengths,weaknesses=excluded.weaknesses,business_value=excluded.business_value,panorama=excluded.panorama,
              contactability=excluded.contactability,evidence_refs=excluded.evidence_refs,uncertainty=excluded.uncertainty,
              completed_at=excluded.completed_at,error=NULL''',
            (assessment_id, project_id, snapshot_id, model, prompt_version, input_sha, 'completed',
             dump(assessment['tags']), dump(assessment['dimensions']), assessment['total_score'], assessment['tier'],
             dump(assessment['strengths']), dump(assessment['weaknesses']), assessment['business_value'], dump(assessment['panorama']),
             assessment['contactability'], dump(assessment['evidence_refs']), assessment['uncertainty'], now(), now(), None))
    return {'id': assessment_id, 'status': 'completed', 'duplicate': False, 'total_score': assessment['total_score'],
            'tier': assessment['tier']}
