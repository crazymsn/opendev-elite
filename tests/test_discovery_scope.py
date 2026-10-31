import json
from pathlib import Path
import tempfile
import unittest
from worker.db import Store
from worker.pipeline import Pipeline,discovery_fork_mode,public_discovery_query,repository_is_public
from worker.operations import _validate_discovery


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=Store(Path(self.temp.name)/'db');self.addCleanup(self.s.close)
        self.calls=[]
        calls=self.calls
        class Fake:
            def get(self,path,params):
                calls.append(params['q']);return {'items':[{'id':1,'full_name':'person/improved-fork','html_url':'https://github.com/person/improved-fork','fork':True,'private':False}],'total_count':1},{}
            def next_link(self,headers):return None
        self.p=Pipeline(self.s,Fake())

    def test_default_includes_forks_without_confusing_discovery_and_selection(self):
        self.p.schedule({'queries':[{'query':'topic:rag','category':'rag'}]},'2026-01-01','2026-01-01')
        self.p.search(self.s.claim())
        self.assertIn('fork:true',self.calls[0]);self.assertNotIn('fork:false',self.calls[0])
        self.assertIn('is:public',self.calls[0])
        self.assertEqual(self.s.one('SELECT stage FROM projects')['stage'],'discovered')
        self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM qualifications')['n'],0)
        self.assertEqual(json.loads(self.s.one('SELECT details FROM search_coverage')['details'])['fork_mode'],'include')

    def test_explicit_query_wins_and_stored_scope_changes_job_identity(self):
        base={'queries':[{'query':'topic:rag','category':'rag'}]}
        a=self.p.schedule(base,'2026-01-01','2026-01-01')
        b=self.p.schedule(dict(base,fork_mode='only'),'2026-01-01','2026-01-01')
        self.assertNotEqual(a,b)
        self.assertEqual(a,self.p.schedule(base,'2026-01-01','2026-01-01'))
        self.assertEqual(discovery_fork_mode('topic:rag fork:false','include'),'exclude')
        self.assertEqual(discovery_fork_mode('"fork:false" in:readme','include'),'include')
        self.assertEqual(discovery_fork_mode('"mention fork:false in docs" fork:only'),'only')
        for value in ('bad',None,[],True):
            with self.assertRaises(ValueError):_validate_discovery(dict(base,fork_mode=value))
        with self.assertRaises(ValueError):discovery_fork_mode('fork:true fork:only')

    def test_legacy_queued_page_keeps_original_exclusion_scope(self):
        p={'query':'topic:rag','category':'rag','start':'2026-01-01','end':'2026-01-01','stars_min':0,'stars_max':None,'run':'2026-01-01','page':2}
        self.s.enqueue('search',p,'legacy-search-page-2');self.p.search(self.s.claim())
        self.assertIn('fork:false',self.calls[0])
        self.assertIn('is:public',self.calls[0])

    def test_public_query_preserves_literals_and_rejects_nonpublic_scope_atomically(self):
        self.assertEqual(public_discovery_query('"mention is:private in docs" is:public'),
                         '"mention is:private in docs" is:public')
        self.assertEqual(public_discovery_query('topic:rag IS:PUBLIC visibility:public'), 'topic:rag is:public')
        for qualifier in ('is:private','is:internal','visibility:private','visibility:internal','-is:public'):
            config={'queries':[{'query':'topic:rag','category':'rag'},
                               {'query':'topic:agent '+qualifier,'category':'agent'}]}
            with self.subTest(qualifier=qualifier):
                with self.assertRaisesRegex(ValueError,'public repositories only'):_validate_discovery(config)
                with self.assertRaisesRegex(ValueError,'public repositories only'):self.p.schedule(config)
                self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM jobs')['n'],0)

    def test_visibility_requires_positive_evidence_without_conflicting_fields(self):
        for repo in ({'private':False},{'visibility':'public'},{'private':False,'visibility':'public'}):
            self.assertTrue(repository_is_public(repo))
        for repo in ({},{'private':True},{'private':False,'visibility':'internal'},
                     {'private':True,'visibility':'public'},{'private':0},{'private':'false'},
                     {'private':None,'visibility':'public'},None,[]):
            self.assertFalse(repository_is_public(repo))

    def test_search_filters_nonpublic_and_unknown_before_import_and_reports_partial(self):
        items=[{'id':i,'full_name':f'person/tool{i}','html_url':f'https://github.com/person/tool{i}',**flags}
               for i,flags in enumerate([{'private':False},{'private':True},{'visibility':'internal'},{}],1)]
        self.p.gh.get=lambda *args:({'total_count':4,'items':items},{})
        self.p.schedule({'queries':[{'query':'topic:rag','category':'rag'}]},'2026-01-01','2026-01-01')
        self.p.search(self.s.claim())
        self.assertEqual([r['id'] for r in self.s.all('SELECT id FROM projects')],[1])
        self.assertEqual(len(self.s.all("SELECT id FROM jobs WHERE kind='refresh'")),1)
        coverage=self.s.one('SELECT * FROM search_coverage');details=json.loads(coverage['details'])
        self.assertEqual(coverage['status'],'partial');self.assertEqual(coverage['fetched'],4)
        self.assertEqual(details['accepted_public_items'],1);self.assertEqual(details['filtered_visibility_items'],3)

    def test_refresh_refuses_private_or_unknown_before_code_and_author_calls(self):
        for index,flags in enumerate([{'private':True},{'visibility':'internal'},{},{'private':True,'visibility':'public'}]):
            calls=[]
            def get(path,params=None):
                calls.append(path)
                if path!='/repos/person/tool':raise AssertionError('Code or author request must not occur')
                return {'id':1,'full_name':'person/tool','html_url':'https://github.com/person/tool',
                        'owner':{'id':2,'login':'person'},**flags},{}
            self.p.gh.get=get
            self.s.enqueue('refresh',{'repo':'person/tool'},f'blocked-{index}')
            result=self.p.work(1)
            self.assertEqual(result[0]['status'],'dead');self.assertIn('not confirmed public',result[0]['error'])
            self.assertEqual(calls,['/repos/person/tool'])
        self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM snapshots')['n'],0)
        self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM maintainers')['n'],0)

    def test_search_preserves_quoted_phrases_and_per_query_scope(self):
        self.p.schedule({'fork_mode':'exclude','queries':[{'query':'"mention fork:false in docs" fork:only','category':'rag','fork_mode':'include'}]},'2026-01-01','2026-01-01')
        self.p.search(self.s.claim())
        self.assertIn('"mention fork:false in docs"',self.calls[0])
        self.assertEqual(self.calls[0].count('fork:only'),1)
        self.assertEqual(json.loads(self.s.one('SELECT details FROM search_coverage')['details'])['fork_mode'],'only')

    def test_partitioned_queries_keep_selected_scope(self):
        self.p.gh.get=lambda *args:({'total_count':1001,'items':[]},{})
        self.p.schedule({'fork_mode':'only','queries':[{'query':'topic:rag','category':'rag'}]},'2026-01-01','2026-01-02')
        self.p.search(self.s.claim())
        children=self.s.all("SELECT payload FROM jobs WHERE state='queued'")
        self.assertEqual(len(children),2)
        self.assertTrue(all(json.loads(r['payload'])['fork_mode']=='only' for r in children))


if __name__=='__main__':unittest.main()
