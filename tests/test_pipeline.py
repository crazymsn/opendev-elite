import io
import base64
import json
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from worker.db import Store, dump
from worker.github import GitHub, ApiError
from worker.pipeline import Pipeline
from worker.runner import docker_command


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.s = Store(Path(self.tmp.name) / 'test.db')

    def tearDown(self):
        self.s.close(); self.tmp.cleanup()

    def test_stable_id_handles_rename_and_keeps_stage(self):
        repo = dict(id=1,full_name='a/old',html_url='https://github.com/a/old')
        self.s.upsert_project(repo, 'one')
        self.s.conn.execute("UPDATE projects SET stage='experience'")
        self.s.upsert_project(dict(repo,full_name='a/new',html_url='https://github.com/a/new'), 'two')
        rows = self.s.all('SELECT * FROM projects')
        self.assertEqual(len(rows),1); self.assertEqual(rows[0]['stage'],'experience'); self.assertEqual(rows[0]['full_name'],'a/new')

    def test_job_claim_exclusive_and_old_lease_cannot_finish(self):
        self.s.enqueue('refresh', {'repo':'a/b'}, 'x')
        one = self.s.claim()
        other = Store(self.s.path)
        try:
            self.assertIsNone(other.claim())
            self.s.conn.execute('UPDATE jobs SET lease_until=0')
            two = other.claim()
            self.s.finish(one)
            self.assertEqual(other.one('SELECT state FROM jobs')['state'],'running')
            other.finish(two)
            self.assertEqual(other.one('SELECT state FROM jobs')['state'],'done')
        finally: other.close()

    def test_schedule_is_idempotent(self):
        p = Pipeline(self.s, None)
        config = {'queries':[{'query':'topic:rag','category':'rag'}]}
        first = p.schedule(config,'2026-01-01','2026-01-02')
        self.assertEqual(first,p.schedule(config,'2026-01-01','2026-01-02'))

    def test_refresh_dedupe_key_is_scoped_to_discovery_run(self):
        self.s.upsert_project({'id': 1, 'full_name': 'a/b', 'html_url': 'https://github.com/a/b'}, 'manual')
        p = Pipeline(self.s, None)
        first = p.schedule({'queries': [{'query': 'topic:rag', 'category': 'rag'}]}, '2026-01-01', '2026-01-02')
        first_run = p.last_run_id
        second = p.schedule({'queries': [{'query': 'topic:agent', 'category': 'agent'}]}, '2026-01-01', '2026-01-02')
        second_run = p.last_run_id
        self.assertNotEqual(first_run, second_run)
        rows = self.s.all("SELECT dedupe_key,payload FROM jobs WHERE kind='refresh' ORDER BY id")
        self.assertEqual(len(rows), 2)
        self.assertIn(first_run, rows[0]['dedupe_key'])
        self.assertIn(second_run, rows[1]['dedupe_key'])
        self.assertEqual(json.loads(rows[0]['payload'])['run_id'], first_run)
        self.assertEqual(json.loads(rows[1]['payload'])['run_id'], second_run)

    def test_search_splits_large_date_range(self):
        class Fake:
            def get(self, *args): return {'total_count':1001,'items':[]}, {}
        p = Pipeline(self.s, Fake())
        p.schedule({'queries':[{'query':'topic:rag','category':'rag'}]}, '2026-01-01','2026-01-02')
        job = self.s.claim(); p.search(job)
        self.assertEqual(self.s.one('SELECT status FROM search_coverage')['status'],'split')
        self.assertEqual(self.s.one("SELECT COUNT(*) AS n FROM jobs WHERE state='queued'")['n'],2)

    def test_zero_stars_import_and_incomplete_search_report(self):
        class Fake:
            def get(self, path, params):
                assert 'stars:>=0' in params['q']
                return {'total_count':1,'incomplete_results':True,'items':[dict(id=1,full_name='a/b',html_url='https://github.com/a/b',stargazers_count=0,private=False)]}, {}
            def next_link(self, headers): return None
        p = Pipeline(self.s, Fake())
        p.schedule({'queries':[{'query':'topic:rag','category':'rag'}]},'2026-01-01','2026-01-01')
        p.search(self.s.claim())
        self.assertEqual(self.s.one('SELECT id FROM projects')['id'],1)
        self.assertEqual(self.s.one('SELECT status FROM search_coverage')['status'],'partial')

    def test_rate_limit_defers_batch_and_does_not_succeed(self):
        class Fake:
            def get(self,*args): raise ApiError(429,'rate limited',120,True)
        self.s.enqueue('refresh', {'repo':'a/b'}, 'one'); self.s.enqueue('refresh', {'repo':'a/c'}, 'two')
        result = Pipeline(self.s,Fake()).work(10)
        self.assertEqual(len(result),1); self.assertEqual(result[0]['status'],'retry')
        self.assertTrue(all(j['available_at'] > time.time() for j in self.s.all('SELECT * FROM jobs')))

    def test_network_failure_delays_only_failing_job(self):
        class Fake:
            def get(self,*args): raise ApiError(0,'network failure',30,True)
        self.s.enqueue('refresh', {'repo':'a/b'}, 'one'); self.s.enqueue('refresh', {'repo':'a/c'}, 'two')
        before = time.time()
        result = Pipeline(self.s,Fake()).work(1)
        self.assertEqual(len(result),1); self.assertEqual(result[0]['status'],'retry')
        sibling = self.s.one("SELECT available_at,state FROM jobs WHERE dedupe_key='two'")
        self.assertEqual(sibling['state'],'queued')
        self.assertLessEqual(sibling['available_at'], before + 1)

    def test_unauthorized_not_treated_as_empty_success(self):
        class Fake:
            def get(self,*args): raise ApiError(404,'not found')
        self.s.enqueue('refresh', {'repo':'a/b'}, 'one')
        result = Pipeline(self.s,Fake()).work(1)
        self.assertEqual(result[0]['status'],'dead')
        self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM projects')['n'],0)

    def test_http_etag_and_no_cross_token_cache(self):
        calls=[]
        class Response:
            headers={'ETag':'fixture'}
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,*args): return b'{"id": 1}'
        def transport(req,timeout):
            calls.append(req)
            if req.has_header('If-none-match'):
                raise urllib.error.HTTPError(req.full_url,304,'Not Modified',{},io.BytesIO())
            return Response()
        client=GitHub(self.s,token='one',transport=transport,min_interval=0)
        self.assertEqual(client.get('/repos/a/b')[0],client.get('/repos/a/b')[0])
        GitHub(self.s,token='two',transport=transport,min_interval=0).get('/repos/a/b')
        self.assertFalse(calls[-1].has_header('If-none-match'))

    def test_secondary_limit_from_message(self):
        def transport(req,timeout):
            raise urllib.error.HTTPError(req.full_url,403,'Forbidden',{},io.BytesIO(b'{"message":"secondary rate limit"}'))
        with self.assertRaises(ApiError) as ctx: GitHub(self.s,token='',transport=transport,min_interval=0).get('/repos/a/b')
        self.assertTrue(ctx.exception.retryable); self.assertGreaterEqual(ctx.exception.retry_after,60)

    def test_rate_gate_releases_permit_and_persists_shared_backoff(self):
        def transport(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 429, 'Too Many Requests',
                                         {'Retry-After': '1'}, io.BytesIO(b'{"message":"rate limited"}'))
        client = GitHub(self.s, token='fixture-token', transport=transport,
                        min_interval=0, max_concurrency=2)
        with self.assertRaises(ApiError):
            client.get('/repos/a/b')
        row = self.s.one("SELECT in_flight,next_allowed_at,concurrency FROM provider_limits WHERE provider='github'")
        self.assertEqual(row['in_flight'], 0)
        self.assertEqual(row['concurrency'], 2)
        self.assertGreater(row['next_allowed_at'], time.time())

    def test_default_anonymous_client_respects_hourly_quota(self):
        client = GitHub(self.s, token='')
        self.assertGreaterEqual(client.interval, 60)
        self.assertEqual(client.max_concurrency, 1)

    def test_backup_is_consistent_and_will_not_overwrite(self):
        self.s.enqueue('refresh',{'repo':'a/b'},'one')
        target=Path(self.tmp.name)/'backup.db'; self.s.backup(target)
        other=Store(target)
        try: self.assertEqual(other.one('SELECT kind FROM jobs')['kind'],'refresh')
        finally: other.close()
        with self.assertRaises(ValueError): self.s.backup(target)

    def test_runner_uses_pinned_image_and_no_host_shell(self):
        config={'image':'fixture@sha256:'+'a'*64,'command':['/app/run','literal; not shell'], 'timeout_seconds':30,'memory_mb':512,'cpus':1}
        command=docker_command(Path(self.tmp.name)/'task.json',Path(self.tmp.name)/'out',config,'fixture')
        self.assertIn('--network=none',command); self.assertIn('--read-only',command); self.assertIn('--pull=never',command)
        self.assertEqual(command[-1],'literal; not shell')
        config['image']='fixture:latest'
        with self.assertRaises(ValueError): docker_command('x','y',config,'z')

    def test_refresh_captures_versioned_evidence_and_keeps_full_owner_profile(self):
        sample_paths=['src/server.jl','src/gateway/main.jl','src/core/runtime.jl','test/test_server.jl','Project.toml','.github/workflows/ci.yml']
        sample_calls=[]
        class Fake:
            def next_link(self, headers): return None
            def get(self, path, params=None):
                if path == '/repos/a/b':
                    return {'id':1,'full_name':'a/b','html_url':'https://github.com/a/b','default_branch':'main','private':False,
                            'owner':{'id':2,'login':'a','type':'User'}}, {}
                if path.endswith('/commits/main'): return {'sha':'a'*40}, {}
                if '/git/trees/' in path: return {'tree':[{'path':p,'type':'blob','size':5,'sha':'b'*40} for p in sample_paths], 'truncated':True}, {}
                if path.endswith('/readme'): return {'encoding':'base64','content':base64.b64encode(b'hello').decode()}, {}
                if '/contents/' in path:
                    sample_calls.append((path,params))
                    return {'encoding':'base64','content':base64.b64encode(b'pass').decode(),'sha':'b'*40}, {}
                if path.endswith('/contributors'): return [{'id':2,'login':'a','type':'User'}], {}
                if path == '/users/a': return {'id':2,'login':'a','type':'User','email':'public@example.invalid'}, {}
                return [], {}
        self.s.enqueue('refresh', {'repo':'a/b'}, 'one')
        self.assertEqual(Pipeline(self.s,Fake()).work(1)[0]['status'],'done')
        snapshot=self.s.one('SELECT * FROM snapshots')
        evidence=json.loads(snapshot['evidence'])
        self.assertEqual(snapshot['sha'],'a'*40); self.assertEqual(snapshot['complete'],1)
        self.assertEqual(evidence['readme_text'],'hello')
        self.assertIn('ref='+'a'*40,evidence['sources']['code_sample_0'])
        self.assertEqual(len(sample_calls),6)
        self.assertEqual(set(evidence['code_sample_paths']),set(sample_paths))
        self.assertEqual({x['role'] for x in evidence['code_sampling']['files']},{'implementation','test','dependency_manifest','ci_configuration'})
        self.assertTrue(evidence['code_sampling']['tree_truncated'])
        self.assertTrue(all(params=={'ref':'a'*40} for _,params in sample_calls))
        self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM qualifications')['n'],0)
        profile=json.loads(self.s.one('SELECT profile FROM maintainers WHERE id=2')['profile'])
        self.assertEqual(profile['email'],'public@example.invalid')

    def test_expired_leases_eventually_dead_letter(self):
        self.s.enqueue('refresh', {'repo':'a/b'}, 'one')
        for _ in range(5):
            self.s.claim(); self.s.conn.execute('UPDATE jobs SET lease_until=0')
        self.assertIsNone(self.s.claim())
        self.assertEqual(self.s.one('SELECT state FROM jobs')['state'],'dead')


if __name__ == '__main__': unittest.main()
