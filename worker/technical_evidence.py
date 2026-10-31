"""Bounded, read-only presentation of captured evidence, never an assessment."""
import base64
import binascii
import hashlib
import json
import re
from urllib.parse import quote, urlsplit

MAX_FILES = 6
MAX_FILE_BYTES = 80000
MAX_PREVIEW_CHARS = 12000
MAX_PREVIEW_LINES = 300
ROLES = {'implementation', 'test', 'dependency_manifest', 'ci_configuration'}


def obj(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return value if isinstance(value, list) else []


def text(value, limit=1000):
    return value[:limit] if isinstance(value, str) else ''


def source_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme == 'https' and url.netloc in ('github.com', 'api.github.com'):
            return value
    except ValueError:
        pass
    return None


def valid_path(value):
    return (isinstance(value, str) and 0 < len(value) <= 2048 and '\\' not in value
            and not any(ord(c) < 32 for c in value)
            and all(p not in ('', '.', '..') for p in value.split('/')))


def sha(value):
    return value if isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) else None


def file_preview(value, expected_sha):
    value = obj(value)
    result = {'status': 'unavailable', 'text': '', 'truncated': False, 'blob_verified': False}
    if value.get('encoding') != 'base64' or not isinstance(value.get('content'), str):
        return result
    # Bound decoding even when reading imported or legacy snapshot content.
    encoded = value['content']
    if len(encoded) > 120000:
        return dict(result, status='oversized')
    try:
        data = base64.b64decode(''.join(encoded.split()), validate=True)
    except (ValueError, binascii.Error):
        return dict(result, status='invalid_encoding')
    if len(data) > MAX_FILE_BYTES:
        return dict(result, status='oversized')
    actual_sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    declared_sha = sha(value.get('sha'))
    expected_sha = sha(expected_sha)
    if any(s and s != actual_sha for s in (declared_sha, expected_sha)):
        return dict(result, status='sha_mismatch')
    result['blob_verified'] = bool(declared_sha and expected_sha)
    try:
        decoded = data.decode('utf-8')
    except UnicodeDecodeError:
        return dict(result, status='invalid_encoding')
    if '\0' in decoded:
        return dict(result, status='binary')
    lines = decoded.splitlines(keepends=True)
    preview = ''.join(lines[:MAX_PREVIEW_LINES])[:MAX_PREVIEW_CHARS]
    return dict(result, status='available', text=preview, truncated=preview != decoded,
                bytes=len(data), total_lines=len(lines), sha=actual_sha)


def technical_evidence(project, snapshot):
    if not snapshot:
        return {'available': False}
    try:
        evidence = obj(json.loads(snapshot['evidence']))
    except (ValueError, TypeError):
        evidence = {}
    repo = obj(evidence.get('repository'))
    name = text(repo.get('full_name') or project.get('full_name'), 300)
    repo_url = 'https://github.com/' + name if re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', name) else None
    captured_sha = sha(snapshot.get('sha'))
    sampling = obj(evidence.get('code_sampling'))
    descriptors = rows(sampling.get('files'))
    legacy = not isinstance(sampling.get('files'), list)
    if legacy:
        descriptors = [{'path': p} for p in rows(evidence.get('code_sample_paths'))]
    files = []
    for index, item in enumerate(descriptors[:MAX_FILES]):
        item = obj(item)
        path = item.get('path')
        if not valid_path(path):
            continue
        key = 'code_sample_' + str(index)
        files.append({'key': key, 'path': path, 'role': item.get('role') if isinstance(item.get('role'), str) and item['role'] in ROLES else 'unspecified',
                      'url': repo_url + '/blob/' + captured_sha + '/' + quote(path, safe='/') if repo_url and captured_sha else None,
                      'preview': file_preview(evidence.get(key), item.get('blob_sha'))})
    commits = []
    for item in rows(evidence.get('commits_90d_sample'))[:20]:
        item = obj(item); commit = obj(item.get('commit')); author = obj(commit.get('author'))
        item_sha = sha(item.get('sha'))
        commits.append({'sha': item_sha, 'message': text(commit.get('message'), 1500),
                        'date': text(obj(commit.get('committer')).get('date'), 50), 'author': text(author.get('name'), 150),
                        'url': repo_url + '/commit/' + item_sha if repo_url and item_sha else None})
    releases = []
    for item in rows(evidence.get('releases_sample'))[:10]:
        item = obj(item)
        releases.append({'title': text(item.get('name') or item.get('tag_name'), 250),
                         'date': text(item.get('published_at'), 50), 'url': source_url(item.get('html_url')),
                         'body': text(item.get('body'), 1800), 'body_truncated': len(text(item.get('body'), 1801)) > 1800,
                         'draft': item.get('draft') is True, 'prerelease': item.get('prerelease') is True})
    discussions = []
    for item in rows(evidence.get('issues_pr_sample'))[:15]:
        item = obj(item)
        discussions.append({'number': item.get('number') if type(item.get('number')) is int else None,
                            'title': text(item.get('title'), 500), 'state': text(item.get('state'), 30),
                            'kind': 'pull_request' if isinstance(item.get('pull_request'), dict) else 'issue',
                            'updated_at': text(item.get('updated_at'), 50), 'url': source_url(item.get('html_url'))})
    components = {}
    for key, limit in [('commits_90d_sample', 20), ('releases_sample', 10), ('issues_pr_sample', 15)]:
        observed = isinstance(evidence.get(key), list)
        components[key] = {'observed': observed, 'captured': len(rows(evidence.get(key))),
                           'display_limit': limit, 'has_more': bool(obj(obj(evidence.get('sampling')).get(key)).get('has_more')),
                           'url': source_url(obj(evidence.get('sources')).get(key))}
    license = obj(repo.get('license'))
    return {'available': True, 'snapshot_id': snapshot['id'], 'sha': captured_sha,
            'captured_at': snapshot.get('fetched_at'), 'version_matches_head': bool(captured_sha and captured_sha == project.get('head_sha')),
            'collection_complete': snapshot.get('complete') == 1, 'synthetic': evidence.get('synthetic') is True,
            'tree_truncated': bool(obj(evidence.get('tree')).get('truncated') or sampling.get('tree_truncated')),
            'legacy_sampling': legacy, 'files': files, 'sample_count': len(descriptors),
            'strategy': text(sampling.get('strategy'), 80), 'repo_url': repo_url,
            'fork': repo.get('fork') if type(repo.get('fork')) is bool else None,
            'upstream_url': source_url(obj(repo.get('parent')).get('html_url')),
            'license': text(license.get('spdx_id') or license.get('name'), 150),
            'commits': commits, 'releases': releases, 'discussions': discussions, 'components': components,
            'errors': [{'component': text(obj(e).get('component'), 100),
                        'status': obj(e).get('status') if type(obj(e).get('status')) is int else None}
                       for e in rows(evidence.get('errors'))[:20]]}
