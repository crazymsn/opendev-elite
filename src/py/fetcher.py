"""Refactored local GitHub_Worker crawler used by the production Pipeline.

Historical entry points delegate to the same Pipeline, REST transport and queue.
No separate PyGithub crawler, pandas dependency, or silent error suppression.
"""
import datetime as dt
import json
from pathlib import Path


def search_page(client, query, page=1):
    if not 1 <= page <= 10:
        raise ValueError('GitHub search pages must be between 1 and 10')
    body, headers = client.get('/search/repositories',
                               dict(q=query, sort='updated', order='desc', per_page=100, page=page))
    if not isinstance(body, dict) or not isinstance(body.get('items'), list) or not isinstance(body.get('total_count'), int):
        from worker.github import ApiError
        raise ApiError(502, 'invalid repository search response', 30, True)
    return body, headers


def fetch_repository(client, repo_name):
    from worker.pipeline import normalize_repo
    repo, headers = client.get('/repos/' + normalize_repo(repo_name))
    if not isinstance(repo, dict) or not all(repo.get(k) for k in ('id', 'full_name', 'html_url', 'owner')):
        from worker.github import ApiError
        raise ApiError(502, 'invalid repository response', 30, True)
    return repo, headers


PROJECT_FIELDS = ['name', 'description', 'url', 'stars', 'forks', 'open_issues',
                  'watchers', 'language', 'created_at', 'updated_at', 'contributors',
                  'search_date', 'search_topic', 'has_been_scraped']


def export_projects(store, path, topic=''):
    from src.py.userinfo import write_table
    rows = []
    for row in store.all('SELECT * FROM projects ORDER BY id'):
        repo = json.loads(row['metadata'])
        people = store.all('SELECT m.login,m.profile FROM maintainers m JOIN project_maintainers p ON p.maintainer_id=m.id WHERE p.project_id=? ORDER BY m.id', (row['id'],))
        contributors = '; '.join(p['login'] + ' (' + (json.loads(p['profile']).get('email') or '无') + ')' for p in people)
        rows.append(dict(name=row['full_name'], description=repo.get('description'), url=row['url'],
                         stars=repo.get('stargazers_count', 0), forks=repo.get('forks_count', 0),
                         open_issues=repo.get('open_issues_count', 0), watchers=repo.get('watchers_count', 0),
                         language=repo.get('language'), created_at=repo.get('created_at'), updated_at=repo.get('updated_at'),
                         contributors=contributors, search_date=row['discovered_at'][:10], search_topic=topic,
                         has_been_scraped=bool(row['refreshed_at'])))
    write_table(path, PROJECT_FIELDS, rows, ',')
    return len(rows)


class GitHubFetcher:
    def __init__(self, token, base_dir, topic, date_start, date_end, max_per_day=1000, batch_size=10, *, client=None):
        self.token, self.base_dir, self.topic = token, Path(base_dir), topic
        self.date_start, self.date_end = dt.date.fromisoformat(date_start), dt.date.fromisoformat(date_end)
        if self.date_start > self.date_end or max_per_day < 1 or batch_size < 1:
            raise ValueError('Invalid date range or job budget')
        # max_per_day now bounds job work; unfinished work persists in SQLite.
        self.max_per_day, self.batch_size, self.client = max_per_day, batch_size, client
        self.output_path = self.base_dir / 'Projects_Today.csv'

    def run(self):
        from worker.db import Store
        from worker.github import GitHub
        from worker.pipeline import Pipeline
        from src.py.userinfo import export_library
        store = Store(self.base_dir / 'worker.db')
        try:
            pipeline = Pipeline(store, self.client or GitHub(store, self.token))
            pipeline.schedule({'date_field': 'created', 'queries': [{'query': self.topic, 'category': 'legacy'}]},
                              self.date_start.isoformat(), self.date_end.isoformat())
            budget = self.max_per_day * ((self.date_end - self.date_start).days + 1)
            outcomes = []
            while len(outcomes) < budget:
                batch = pipeline.work(min(self.batch_size, budget - len(outcomes)))
                outcomes.extend(batch)
                if not batch or batch[-1]['status'] == 'retry':
                    break
            export_projects(store, self.output_path, self.topic)
            export_library(store, self.base_dir / 'GitHub_User_Library_Today.csv')
            pending = store.one("SELECT COUNT(*) AS n FROM jobs WHERE state IN ('queued','running')")['n']
            dead = store.one("SELECT COUNT(*) AS n FROM jobs WHERE state='dead'")['n']
            partial = store.one("SELECT COUNT(*) AS n FROM search_coverage WHERE status='partial'")['n']
            return {'outcomes': outcomes, 'pending_jobs': pending, 'failed_jobs': dead,
                    'partial_searches': partial, 'complete': pending == 0 and dead == 0 and partial == 0,
                    'database': str(store.path), 'csv': str(self.output_path)}
        finally:
            store.close()


def fetch_github_repos(token, base_dir, topic='topic:artificial-intelligence', date_start=None, date_end=None, **kwargs):
    end = date_end or dt.date.today().isoformat()
    start = date_start or (dt.date.fromisoformat(end) - dt.timedelta(days=7)).isoformat()
    return GitHubFetcher(token, base_dir, topic, start, end, **kwargs).run()


def main(token, base_dir, topic, date_start, date_end):
    return fetch_github_repos(token, base_dir, topic, date_start, date_end)
