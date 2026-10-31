import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from worker.technical_evidence import technical_evidence, file_preview
from worker.db import Store, dump, now
from worker.demo import seed, SHA
from worker.auth import Principal
from worker.service import Application, ApiFailure


def blob(data):
    digest=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    return {'encoding':'base64','content':base64.b64encode(data).decode(),'sha':digest}


class TechnicalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.project={'id':1,'head_sha':SHA,'full_name':'old/name'}
        self.snapshot={'id':2,'sha':SHA,'fetched_at':now(),'complete':1,'evidence':'{}'}

    def present(self,evidence):
        return technical_evidence(self.project,dict(self.snapshot,evidence=json.dumps(evidence)))

    def test_capture_time_and_commit_sources_survive_repository_rename(self):
        value=blob('hello 你好\n'.encode())
        data=self.present({'repository':{'full_name':'captured/name','fork':True,'parent':{'html_url':'https://github.com/upstream/name'}},
            'code_sampling':{'files':[{'path':'src/server.jl','role':'implementation','blob_sha':value['sha']}]},'code_sample_0':value})
        file=data['files'][0]
        self.assertEqual(file['url'],'https://github.com/captured/name/blob/'+SHA+'/src/server.jl')
        self.assertTrue(file['preview']['blob_verified']);self.assertEqual(file['preview']['text'],'hello 你好\n')
        self.assertTrue(data['fork']);self.assertTrue(data['version_matches_head'])
        self.project['head_sha']='b'*40
        self.assertFalse(self.present({})['version_matches_head'])

    def test_preview_is_bounded_and_mismatched_bytes_are_not_presented(self):
        value=blob(b'x\n'*10000)
        result=file_preview(value,value['sha'])
        self.assertTrue(result['truncated']);self.assertEqual(len(result['text'].splitlines()),300)
        value=blob(b'x'*30000)
        self.assertEqual(len(file_preview(value,value['sha'])['text']),12000)
        mismatch=file_preview(value,'b'*40)
        self.assertEqual(mismatch['status'],'sha_mismatch');self.assertFalse(mismatch['text'])
        for data,status in [(b'\xff','invalid_encoding'),(b'x\0y','binary'),(b'x'*80001,'oversized')]:
            value=blob(data);self.assertEqual(file_preview(value,value['sha'])['status'],status)
        self.assertEqual(file_preview({'encoding':'base64','content':'%%%bad'},None)['status'],'invalid_encoding')

    def test_legacy_missing_and_empty_samples_stay_distinct(self):
        value=blob(b'legacy')
        data=self.present({'code_sample_paths':['core.py'],'code_sample_0':value})
        self.assertTrue(data['legacy_sampling']);self.assertFalse(data['files'][0]['preview']['blob_verified'])
        self.assertEqual(data['files'][0]['role'],'unspecified')
        self.assertFalse(self.present({'code_sampling':{'files':[]}})['legacy_sampling'])
        self.assertEqual(technical_evidence(self.project,None),{'available':False})
        self.assertFalse(self.present({})['components']['commits_90d_sample']['observed'])
        self.assertTrue(self.present({'commits_90d_sample':[]})['components']['commits_90d_sample']['observed'])

    def test_unsafe_urls_paths_and_malformed_metadata_are_not_active_links(self):
        data=self.present({'code_sampling':{'files':[{'path':'../outside.py'},{'path':'/absolute.py'},
            {'path':'core.py','role':[]}]},'releases_sample':[{'html_url':'javascript:alert(1)','body':'<script>text only</script>'}],
            'issues_pr_sample':[{'html_url':'https://github.com@evil.invalid/','title':'untrusted'}]})
        self.assertEqual(len(data['files']),1);self.assertEqual(data['files'][0]['key'],'code_sample_2')
        self.assertIsNone(data['releases'][0]['url']);self.assertIsNone(data['discussions'][0]['url'])
        self.assertEqual(data['releases'][0]['body'],'<script>text only</script>')

    def test_paginated_samples_limits_and_failures_are_exposed_without_verdicts(self):
        data=self.present({'commits_90d_sample':[{'sha':'c'*40,'commit':{'message':'fix','committer':{'date':'2026-01-01'}}}]*100,
            'releases_sample':[{}]*15,'issues_pr_sample':[{'pull_request':{}}]*30,
            'sampling':{'commits_90d_sample':{'has_more':True}},'tree':{'truncated':True},
            'errors':[{'component':'owner_profile','status':404}], 'score':100,'auto_approved':True})
        self.assertEqual((len(data['commits']),len(data['releases']),len(data['discussions'])),(20,10,15))
        self.assertEqual(data['components']['commits_90d_sample']['captured'],100)
        self.assertTrue(data['components']['commits_90d_sample']['has_more']);self.assertTrue(data['tree_truncated'])
        self.assertEqual(data['discussions'][0]['kind'],'pull_request')
        self.assertEqual(data['errors'],[{'component':'owner_profile','status':404}])
        self.assertNotIn('score',data);self.assertNotIn('auto_approved',data)

    def test_endpoint_keeps_reader_permissions_and_never_changes_assessments(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'db')
            try:
                seed(store)
                value=blob(b'print("synthetic")\n')
                store.conn.execute('UPDATE snapshots SET evidence=?',(dump({'code_sample_paths':['main.py'],'code_sample_0':value}),))
                app=Application(store.path,{'users':[{'actor':'reviewer','roles':['reviewer'],
                    'token_sha256':hashlib.sha256(b'synthetic-evidence-reader').hexdigest()}]})
                before=store.one('SELECT COUNT(*) AS n FROM qualifications')['n']
                for role in ('reviewer','operator','approver','disburser','auditor'):
                    result=app.handle('GET','/api/projects/101',principal=Principal(role,frozenset({role})))[1]
                    self.assertEqual(result['technical_evidence']['files'][0]['preview']['text'],'print("synthetic")\n')
                for principal in (None,Principal('agent',frozenset({'agent'}))):
                    with self.assertRaises(ApiFailure):app.handle('GET','/api/projects/101',principal=principal)
                self.assertEqual(store.one('SELECT COUNT(*) AS n FROM qualifications')['n'],before)
                self.assertEqual(store.one('SELECT COUNT(*) AS n FROM reviews')['n'],0)
            finally:store.close()


if __name__=='__main__':unittest.main()
