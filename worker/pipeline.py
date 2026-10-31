import base64
import concurrent.futures
import datetime as dt
import hashlib
import json
import re
import threading
import time
import uuid
from urllib.parse import quote, urlencode
from .db import Store, dump, now
from .github import ApiError, GitHub
from src.py import fetcher, userinfo
from .sampling import select_samples
from .trending import fetch_trending, TrendingError

FORK_MODES = {'include':'true', 'exclude':'false', 'only':'only'}
FORK_QUALIFIER = re.compile(r'"(?:\\.|[^"\\])*"|(?<!\S)fork:(true|false|only)(?=\s|$)', re.I)
VISIBILITY_QUALIFIER = re.compile(r'"(?:\\.|[^"\\])*"|(?<!\S)(-?(?:is|visibility)):(public|private|internal)(?=\s|$)', re.I)


def public_discovery_query(query):
    """Keep quoted search text; enforce the public open-source discovery scope."""
    def replace(match):
        if not match.group(1):
            return match.group(0)
        if match.group(1).startswith('-') or match.group(2).lower() != 'public':
            raise ValueError('Discovery supports public repositories only; use is:public')
        return ''
    return VISIBILITY_QUALIFIER.sub(replace, query).strip() + ' is:public'


def repository_is_public(repo):
    """Unknown or contradictory visibility cannot authorize reading code."""
    if not isinstance(repo, dict):
        return False
    if 'private' in repo and repo['private'] is not False:
        return False
    if 'visibility' in repo and repo['visibility'] != 'public':
        return False
    return repo.get('private') is False or repo.get('visibility') == 'public'


def discovery_fork_mode(query, default='include'):
    if not isinstance(default,str) or default not in FORK_MODES:
        raise ValueError('fork_mode must be include, exclude or only')
    qualifiers=[match.group(1) for match in FORK_QUALIFIER.finditer(query) if match.group(1)]
    if len(qualifiers)>1:
        raise ValueError('Use only one explicit fork qualifier per discovery query')
    return {v:k for k,v in FORK_MODES.items()}[qualifiers[0].lower()] if qualifiers else default


def key_for(payload):
    return hashlib.sha256(dump(payload).encode()).hexdigest()


def normalize_repo(value):
    value = value.strip().removeprefix('https://github.com/').removesuffix('/').removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', value) or value.split('/')[-1] in ('.', '..'):
        raise ValueError('Expected owner/repo or an https://github.com/owner/repo URL')
    return value


class Pipeline:
    def __init__(self, store, github):
        self.s, self.gh = store, github
        self.last_run_id = None

    def schedule(self, config, start=None, end=None, source=None, actor='scheduler', period='daily', language=None, max_projects=None):
        end = end or dt.date.today().isoformat()
        start = start or (dt.date.fromisoformat(end) - dt.timedelta(days=config.get('lookback_days', 7))).isoformat()
        if dt.date.fromisoformat(start) > dt.date.fromisoformat(end):
            raise ValueError('start must not be after end')
        source = source or config.get('source', 'github_search')
        if source not in ('github_search', 'github_trending', 'manual'):
            raise ValueError('source must be github_search, github_trending or manual')
        run = end
        run_id = 'run_' + key_for({'source': source, 'start': start, 'end': end, 'period': period,
                                   'language': language, 'max_projects': max_projects, 'config': config})[:32]
        self.last_run_id = run_id
        date_field = config.get('date_field', 'pushed')
        if date_field not in ('pushed', 'created'):
            raise ValueError('date_field must be pushed or created')
        ids = []
        default_mode=discovery_fork_mode('',config.get('fork_mode','include'))
        with self.s.transaction():
            self.s.conn.execute('''INSERT OR IGNORE INTO discovery_runs
                (id,source,start_date,end_date,config,state,requested_by,created_at,counts)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (run_id, source, start, end, dump({'period': period, 'language': language,
                 'max_projects': max_projects, 'config': config}), 'queued', actor, now(), '{}'))
            if source == 'github_trending':
                payload = {'run_id': run_id, 'start': start, 'end': end, 'period': period,
                           'language': language, 'max_projects': max_projects or 25,
                           'source': source}
                ids.append(self.s.enqueue('trending', payload, 'trending:' + run_id))
            # A run has one explicit discovery source. Trending is a bounded,
            # auditable source of its own; silently adding every configured
            # Search query would duplicate collection and defeat its low-cost
            # fallback semantics. Search queries are queued only for an
            # explicit github_search run.
            if source == 'github_search':
                for item in config['queries']:
                    public_discovery_query(item['query'])
                    mode=discovery_fork_mode(item['query'],item.get('fork_mode',default_mode))
                    p = dict(query=item['query'], category=item['category'], start=start, end=end,
                             stars_min=0, stars_max=None, run=run, run_id=run_id, page=1, date_field=date_field,
                             fork_mode=mode, source=source)
                    ids.append(self.s.enqueue('search', p, 'search:' + key_for(p)))
            for project in self.s.all('SELECT id,full_name FROM projects'):
                ids.append(self.s.enqueue('refresh', {'repo': project['full_name'], 'run_id': run_id}, f"refresh:{project['id']}:{run_id}"))
            if not ids:
                self.s.conn.execute("UPDATE discovery_runs SET state='done',finished_at=? WHERE id=?", (now(), run_id))
        return ids

    def _maybe_finish_run(self, run_id):
        if not run_id:
            return
        pending = self.s.one("SELECT COUNT(*) AS n FROM jobs WHERE json_extract(payload,'$.run_id')=? AND state IN ('queued','running')", (run_id,))['n']
        if pending:
            return
        failed = self.s.one("SELECT COUNT(*) AS n FROM jobs WHERE json_extract(payload,'$.run_id')=? AND state='dead'", (run_id,))['n']
        state = 'failed' if failed else 'done'
        self.s.conn.execute("UPDATE discovery_runs SET state=?,finished_at=COALESCE(finished_at,?) WHERE id=? AND state IN ('queued','running','partial')", (state, now(), run_id))

    def _run_counts(self, run_id, **updates):
        row = self.s.one('SELECT counts FROM discovery_runs WHERE id=?', (run_id,))
        if not row:
            return
        counts = json.loads(row['counts'] or '{}')
        for key, value in updates.items():
            counts[key] = counts.get(key, 0) + value if isinstance(value, int) else value
        self.s.conn.execute('UPDATE discovery_runs SET counts=? WHERE id=?', (dump(counts), run_id))

    def _record_terminal_failure(self, job):
        run_id = job.get('payload', {}).get('run_id')
        if run_id:
            self._run_counts(run_id, failed=1)


    def _record_ranking(self, project_id, run_id, board, period, language, rank, source_url, evidence):
        if not run_id:
            return
        if rank <= 0:
            raise ValueError('ranking rank must be positive')
        weight = 15 if rank == 1 else 12 if rank <= 5 else 9 if rank <= 10 else 5
        self.s.conn.execute('''INSERT INTO project_rankings
            (project_id,run_id,board,period,language,rank,weight,source_url,evidence,captured_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id,board,period,language) DO UPDATE SET
              run_id=excluded.run_id,rank=excluded.rank,weight=excluded.weight,
              source_url=excluded.source_url,evidence=excluded.evidence,captured_at=excluded.captured_at''',
            (project_id, run_id, board, period, language, rank, weight, source_url, dump(evidence), now()))

    def coverage(self, job, query, total, fetched, status, details):
        self.s.conn.execute('INSERT OR REPLACE INTO search_coverage VALUES(?,?,?,?,?,?,?)',
                            (job['id'], query, total, fetched, status, dump(details), now()))

    def search(self, job):
        self.s.heartbeat(job)
        p = job['payload']
        stars = f"stars:{p['stars_min']}..{p['stars_max']}" if p['stars_max'] is not None else f"stars:>={p['stars_min']}"
        date_field = p.get('date_field', 'pushed')
        # Legacy queued pages retain the old scope; new searches carry explicit
        # scope in their idempotency key, so pagination never changes mid-search.
        mode=discovery_fork_mode(p['query'],p.get('fork_mode','exclude'))
        base=FORK_QUALIFIER.sub(lambda match:'' if match.group(1) else match.group(0),p['query']).strip()
        query = f"{public_discovery_query(base)} archived:false fork:{FORK_MODES[mode]} {date_field}:{p['start']}..{p['end']} {stars}"
        body, headers = fetcher.search_page(self.gh, query, p['page'])
        total = body['total_count']
        if total > 1000 and p['page'] == 1:
            start, end = dt.date.fromisoformat(p['start']), dt.date.fromisoformat(p['end'])
            children = []
            if start < end:
                mid = start + (end - start) // 2
                children = [dict(p, end=mid.isoformat()), dict(p, start=(mid + dt.timedelta(days=1)).isoformat())]
            elif p['stars_max'] is None:
                bound = max(1, p['stars_min'] * 2)
                children = [dict(p, stars_max=bound), dict(p, stars_min=bound + 1)]
            elif p['stars_min'] < p['stars_max']:
                mid = (p['stars_min'] + p['stars_max']) // 2
                children = [dict(p, stars_max=mid), dict(p, stars_min=mid + 1)]
            if children:
                with self.s.transaction():
                    self.s.assert_lease(job)
                    for child in children:
                        self.s.enqueue('search', child, 'search:' + key_for(child))
                    self.coverage(job, query, total, 0, 'split', {'children': len(children),'fork_mode':mode,'visibility':'public'})
                    self._run_counts(p.get('run_id'), split_jobs=len(children))
                return
        incomplete = body.get('incomplete_results', False)
        status = 'partial' if incomplete or total > 1000 else 'complete'
        next_url = self.gh.next_link(headers)
        # GitHub can omit Link for an incomplete search response. Never claim
        # full coverage merely because this one page has no continuation link.
        missing_page = not next_url and p['page'] * 100 < min(total, 1000)
        if missing_page:
            status = 'partial'
        accepted = [repo for repo in body['items'] if repository_is_public(repo)]
        filtered = len(body['items']) - len(accepted)
        if filtered:
            status = 'partial'
        with self.s.transaction():
            self.s.assert_lease(job)
            for repo in accepted:
                project_id = self.s.upsert_project(repo, p['category'] + ':' + p['query'])
                self.s.enqueue('refresh', {'repo': repo['full_name'], 'run_id': p.get('run_id')},
                               f"refresh:{project_id}:{p.get('run_id') or p['run']}")
                if p.get('source') == 'github_trending':
                    self._record_ranking(project_id, p.get('run_id'), 'github_trending', p.get('end', p['run']),
                                         p.get('language'), p['page'] * 100 + accepted.index(repo) + 1,
                                         'https://github.com/trending', {'query': query, 'page': p['page']})
            if (next_url or missing_page) and p['page'] < 10:
                child = dict(p, page=p['page'] + 1)
                self.s.enqueue('search', child, 'search:' + key_for(child))
                status = 'partial' if status == 'partial' else 'page_complete'
            self.coverage(job, query, total, len(body['items']), status,
                          {'incomplete_results': incomplete, 'page': p['page'], 'next_page': bool(next_url or missing_page),
                           'fork_mode':mode,
                           'visibility':'public', 'accepted_public_items':len(accepted),
                           'filtered_visibility_items':filtered,
                           'missing_pagination_link': missing_page,
                           'scope': 'retrieved page; split jobs and queued pages must also finish'})
            self._run_counts(p.get('run_id'), search_pages=1, search_results=len(body['items']),
                             discovered=len(accepted), filtered=filtered)
            self._maybe_finish_run(p.get('run_id'))

    def trending(self, job):
        self.s.heartbeat(job)
        p = job['payload']
        run_id = p['run_id']
        self.s.conn.execute("UPDATE discovery_runs SET state='running',started_at=COALESCE(started_at,?) WHERE id=?", (now(), run_id))
        rows, source_url = fetch_trending(p.get('language'), p.get('period', 'daily'))
        limit = max(1, min(100, int(p.get('max_projects', 25))))
        accepted = 0
        with self.s.transaction():
            self.s.assert_lease(job)
            for row in rows[:limit]:
                repo, _ = fetcher.fetch_repository(self.gh, row['full_name'])
                if not repo or not repository_is_public(repo):
                    continue
                project_id = self.s.upsert_project(repo, 'github_trending')
                self._record_ranking(project_id, run_id, 'github_trending', p.get('end', dt.date.today().isoformat()),
                                     p.get('language'), row['rank'], source_url, row)
                self.s.enqueue('refresh', {'repo': repo['full_name'], 'source': 'github_trending', 'run_id': run_id},
                               f"refresh:{project_id}:{run_id}")
                accepted += 1
            status = 'complete' if rows and accepted == min(len(rows), limit) else 'partial'
            self.s.conn.execute("UPDATE discovery_runs SET state=?,finished_at=?,counts=? WHERE id=?",
                                (status, now(), dump({'found': len(rows), 'accepted': accepted,
                                                      'discovered': accepted}), run_id))

    def refresh(self, job):
        self.s.heartbeat(job)
        repo_name = normalize_repo(job['payload']['repo'])
        run_id = job['payload'].get('run_id')
        repo, _ = fetcher.fetch_repository(self.gh, repo_name)
        if not repository_is_public(repo):
            raise ApiError(403, 'repository visibility is not confirmed public; code collection refused')
        # GitHub may return the canonical renamed repository.
        root = '/repos/' + repo['full_name']
        evidence = {'repository': repo, 'errors': [], 'sampling': {}, 'sources': {}}
        def get(name, suffix, params=None):
            self.s.heartbeat(job)
            try:
                value, headers = self.gh.get(root + suffix, params)
                evidence[name] = value
                evidence['sources'][name] = 'https://api.github.com' + root + suffix + ('?' + urlencode(params) if params else '')
                evidence['sampling'][name] = {'has_more': bool(self.gh.next_link(headers))}
                return value
            except ApiError as e:
                if e.retryable or e.status in (401, 403):
                    raise
                evidence['errors'].append({'component': name, 'status': e.status})
                return None
        commit = get('head', '/commits/' + quote(repo.get('default_branch', 'HEAD'), safe=''))
        sha = commit['sha'] if commit else None
        if sha:
            get('tree', '/git/trees/' + sha, {'recursive': 1})
            readme = get('readme', '/readme', {'ref': sha})
            if readme and readme.get('encoding') == 'base64':
                decoded_readme = base64.b64decode(readme.get('content', '')).decode('utf-8', errors='replace')
                evidence['readme_text'] = decoded_readme[:40000]
                evidence['readme_text_truncated'] = len(decoded_readme)>40000
                evidence['readme_text_characters'] = len(decoded_readme)
            tree = (evidence.get('tree') or {}).get('tree', [])
            evidence['code_sampling'] = select_samples(tree)
            evidence['code_sampling']['tree_truncated'] = (evidence.get('tree') or {}).get('truncated',False)
            evidence['code_sample_paths'] = [x['path'] for x in evidence['code_sampling']['files']]
            for index, item in enumerate(evidence['code_sampling']['files']):
                get('code_sample_' + str(index), '/contents/' + quote(item['path'], safe='/'), {'ref': sha})
            since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)).isoformat()
            get('commits_90d_sample', '/commits', {'sha': sha, 'since': since, 'per_page': 100})
        get('releases_sample', '/releases', {'per_page': 10})
        issues = get('issues_pr_sample', '/issues', {'state': 'all', 'sort': 'updated', 'per_page': 30}) or []
        issue_comments = {}
        for issue in issues[:10]:
            if not isinstance(issue, dict) or issue.get('pull_request') or not issue.get('number'):
                continue
            comments = get('issue_comments_' + str(issue['number']),
                           '/issues/' + str(issue['number']) + '/comments', {'per_page': 20})
            issue_comments[str(issue['number'])] = comments if isinstance(comments, list) else []
        evidence['issue_comments_sample'] = issue_comments
        contributors = get('contributors_sample', '/contributors', {'per_page': 10}) or []
        owner = repo['owner']
        try:
            self.s.heartbeat(job)
            profile = userinfo.fetch_profile(owner['login'], self.gh)
        except ApiError as e:
            if e.retryable or e.status in (401, 403):
                raise
            profile = owner
            evidence['errors'].append({'component': 'owner_profile', 'status': e.status})
        evidence['owner_profile'] = profile
        evidence['sources']['owner_profile'] = 'https://api.github.com/users/' + owner['login']
        # Contributors are candidates, never confirmed maintainers. Fetch their
        # public profiles so an organization-owned repository can expose human
        # contact candidates without treating the organization as its author.
        people = {profile['id']: (profile, 'owner_candidate')}
        for contributor in contributors:
            if contributor.get('type') != 'User' or not contributor.get('id') or not contributor.get('login') or contributor['id'] in people:
                continue
            person = contributor
            try:
                self.s.heartbeat(job)
                person = userinfo.fetch_profile(contributor['login'], self.gh)
            except ApiError as e:
                if e.retryable or e.status in (401, 403):
                    raise
                evidence['errors'].append({'component': 'contributor_profile:' + contributor['login'], 'status': e.status})
            people[person['id']] = (person, 'contributor_candidate')
        evidence['maintainer_candidates'] = [{'profile': person, 'role': role, 'confirmed': False}
                                              for person, role in people.values()]
        paths = [x['path'] for x in (evidence.get('tree') or {}).get('tree', [])]
        commits = evidence.get('commits_90d_sample') or []
        evidence['signals'] = {
            'readme_available': bool(evidence.get('readme_text')),
            'license': (repo.get('license') or {}).get('spdx_id'),
            'ci_paths': [p for p in paths if p.startswith('.github/workflows/')][:20],
            'test_paths_sample': [p for p in paths if 'test' in p.lower()][:30],
            'tree_truncated': (evidence.get('tree') or {}).get('truncated', False),
            'sampled_commit_count': len(commits),
            'sampled_known_user_commits': sum(1 for c in commits if (c.get('author') or {}).get('type') == 'User'),
            'sampled_unknown_authors': sum(1 for c in commits if not c.get('author')),
            'warning': 'Signals are evidence pointers, not a code-quality score or a maintenance verdict.'}
        with self.s.transaction():
            self.s.assert_lease(job)
            pid = self.s.upsert_project(repo, job['payload'].get('source', 'refresh'))
            old = self.s.one('SELECT * FROM projects WHERE id=?', (pid,))
            stage = old['stage']
            if sha != old['head_sha'] and stage in ('discovered','technical_review','experience','ready_contact','hold','rejected'):
                stage = 'technical_review'
            self.s.conn.execute('UPDATE projects SET head_sha=?,refreshed_at=?,stage=? WHERE id=?', (sha, now(), stage, pid))
            cur = self.s.conn.execute('INSERT INTO snapshots(project_id,sha,fetched_at,evidence,complete) VALUES(?,?,?,?,?)',
                                      (pid, sha, now(), dump(evidence), int(not evidence['errors'] and bool(sha))))
            self._run_counts(run_id, refreshed=1, complete=int(not evidence['errors'] and bool(sha)))
            for person, role in people.values():
                self.s.conn.execute('INSERT INTO maintainers(id,login,kind,profile) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET login=excluded.login,kind=excluded.kind,profile=excluded.profile',
                                    (person['id'], person['login'], person.get('type'), dump(person)))
                self.s.conn.execute('INSERT INTO project_maintainers(project_id,maintainer_id,role,evidence) VALUES(?,?,?,?) ON CONFLICT(project_id,maintainer_id) DO UPDATE SET role=excluded.role,evidence=excluded.evidence WHERE project_maintainers.confirmed=0',
                                    (pid, person['id'], role, person.get('html_url', repo['html_url'])))
            if run_id:
                self.s.conn.execute("UPDATE discovery_runs SET state='running' WHERE id=? AND state='queued'", (run_id,))
            self.s.enqueue('contact_extract', {'project_id': pid, 'snapshot_id': cur.lastrowid},
                           f"contact:{pid}:{cur.lastrowid}")
            self.s.event('project', pid, 'refreshed', 'collector', {'sha': sha, 'errors': evidence['errors'], 'old_sha': old['head_sha']})
            self._maybe_finish_run(job['payload'].get('run_id'))

    def contact_extract(self, job):
        from .contact_points import sync_project
        self.s.heartbeat(job)
        result = sync_project(self.s, int(job['payload']['project_id']), int(job['payload']['snapshot_id']))
        self.s.enqueue('ai_assess', {'project_id': int(job['payload']['project_id']), 'snapshot_id': int(job['payload']['snapshot_id'])},
                       f"ai:{job['payload']['project_id']}:{job['payload']['snapshot_id']}:rules-v1")
        self.s.event('project', job['payload']['project_id'], 'contacts_classified', 'collector', result)

    def ai_assess(self, job):
        from .ai_assessment import assess_project_snapshot
        self.s.heartbeat(job)
        result = assess_project_snapshot(self.s, int(job['payload']['project_id']), int(job['payload']['snapshot_id']))
        self.s.event('project', job['payload']['project_id'], 'ai_assessed', 'ai-worker', result)

    def _run_claimed_job(self, job):
        """Execute one already leased job and return its outcome plus pause flag."""
        try:
            if job['kind'] == 'search':
                self.search(job)
            elif job['kind'] == 'trending':
                self.trending(job)
            elif job['kind'] == 'refresh':
                self.refresh(job)
            elif job['kind'] == 'contact_extract':
                self.contact_extract(job)
            elif job['kind'] == 'ai_assess':
                self.ai_assess(job)
            else:
                raise ValueError('Unknown job kind: ' + job['kind'])
            self.s.finish(job)
            self._maybe_finish_run(job['payload'].get('run_id'))
            return {'job': job['id'], 'status': 'done'}, False
        except ApiError as e:
            delay = max(e.retry_after, min(3600, 30 * 2 ** job['attempts']))
            self.s.finish(job, str(e), delay, e.retryable)
            self._maybe_finish_run(job['payload'].get('run_id'))
            if not e.retryable or job['attempts'] >= 5:
                self._record_terminal_failure(job)
            paused = e.retryable and (e.status == 429 or 'rate limited' in str(e).lower())
            # Rate limits pause the queued backlog; network/5xx failures delay
            # only this job so unrelated work can keep moving.
            if paused:
                self.s.conn.execute("UPDATE jobs SET available_at=MAX(available_at,?) WHERE state='queued'",
                                    (time.time() + delay,))
            return {'job': job['id'], 'status': 'retry' if e.retryable and job['attempts'] < 5 else 'dead',
                    'error': str(e)}, paused
        except TrendingError as e:
            delay = min(3600, 30 * 2 ** job['attempts'])
            self.s.finish(job, str(e), delay, retryable=True)
            self._maybe_finish_run(job['payload'].get('run_id'))
            if job['attempts'] >= 5:
                self._record_terminal_failure(job)
            return {'job': job['id'], 'status': 'retry' if job['attempts'] < 5 else 'dead', 'error': str(e)}, False
        except Exception as e:
            self.s.finish(job, type(e).__name__ + ': ' + str(e), retryable=False)
            self._maybe_finish_run(job['payload'].get('run_id'))
            self._record_terminal_failure(job)
            return {'job': job['id'], 'status': 'dead', 'error': type(e).__name__ + ': ' + str(e)}, False


    def _parallel_worker(self, max_jobs, claimed, claim_lock, output):
        """Claim and process jobs on a dedicated SQLite connection."""
        local_store = Store(self.s.path)
        try:
            if isinstance(self.gh, GitHub):
                local_github = GitHub(local_store, token=self.gh.token, transport=self.gh.transport,
                                      min_interval=self.gh.interval, max_concurrency=self.gh.max_concurrency)
            else:
                # Test doubles and custom providers are safe to share; their
                # database writes still go through this worker's Store.
                local_github = self.gh
            worker = Pipeline(local_store, local_github)
            while True:
                with claim_lock:
                    if claimed[0] >= max_jobs:
                        break
                    job = local_store.claim()
                    if not job:
                        break
                    claimed[0] += 1
                outcome, _ = worker._run_claimed_job(job)
                output.append(outcome)
        finally:
            local_store.close()

    def work(self, max_jobs=20, workers=1):
        """Process a bounded queue, optionally with a small worker pool.

        Each parallel worker owns its SQLite connection and job lease.  GitHub
        requests share the persistent provider gate, so increasing workers
        cannot create an unbounded request burst.
        """
        if type(max_jobs) is not int or not 1 <= max_jobs <= 1000:
            raise ValueError('max_jobs must be an integer between 1 and 1000')
        if type(workers) is not int or not 1 <= workers <= 8:
            raise ValueError('workers must be an integer between 1 and 8')
        if workers == 1:
            outcomes = []
            paused = False
            for _ in range(max_jobs):
                job = self.s.claim()
                if not job:
                    break
                outcome, paused = self._run_claimed_job(job)
                outcomes.append(outcome)
                if paused:
                    break
            return outcomes
        claimed = [0]
        claim_lock = threading.Lock()
        outcomes = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix='sponsorship-worker') as pool:
            futures = [pool.submit(self._parallel_worker, max_jobs, claimed, claim_lock, outcomes)
                       for _ in range(workers)]
            for future in futures:
                future.result()
        return sorted(outcomes, key=lambda item: item['job'])
