import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from worker.criteria import qualification_status
from worker.db import Store, dump, now
from worker.demo import seed, review_one, SHA, ready, selection_evidence
from worker.dossier import dossier, save_translation, export_translation_work, import_translations
from worker.workflow import Workflow


class CriteriaDossierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name)/'test.db')
        seed(self.store)
        self.workflow = Workflow(self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def qualification(self, **overrides):
        return {'project_id':101,'sha':SHA,'category':'agent-infra','scope_fit':'yes','maintenance':'active',
                'outcome':'include','rationale':'Synthetic scope check','evidence':{**selection_evidence(),
                'reason_code':{'include':'none','hold':'evidence_missing','exclude':'content_only'}[overrides.get('outcome','include')]},**overrides}

    def test_search_candidate_cannot_skip_selection(self):
        self.store.conn.execute('DELETE FROM qualifications')
        with self.assertRaisesRegex(ValueError,'scope and maintenance'):
            review_one(self.workflow)
        self.assertIsNone(self.workflow.latest_review(101,1))

    def test_inclusion_requires_relevant_scope_maintenance_and_evidence(self):
        for patch in ({'scope_fit':'no'},{'maintenance':'uncertain'},{'category':'out-of-scope'},{'evidence':{'scope':'x','maintenance':'x','sources':[]}}):
            with self.assertRaises(ValueError):
                self.workflow.apply('qualify',self.qualification(**patch),'reviewer')
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM qualifications')['n'],1)

    def test_zero_stars_and_early_project_can_qualify(self):
        self.workflow.apply('qualify',self.qualification(),'reviewer')
        review_one(self.workflow)
        self.assertTrue(self.workflow.passed(self.workflow.project(101),1))

    def test_hold_then_reassessment_does_not_revive_prior_reviews(self):
        ready(self.workflow)
        self.workflow.apply('qualify',self.qualification(outcome='hold',maintenance='uncertain'),'reviewer')
        with self.assertRaises(ValueError): self.workflow.contact_gate(101)
        self.workflow.apply('qualify',self.qualification(),'reviewer')
        self.assertFalse(self.workflow.passed(self.workflow.project(101),1))
        self.assertFalse(self.workflow.passed(self.workflow.project(101),2))

    def test_commit_audience_and_policy_changes_invalidate_qualification(self):
        for column,value in [('sha','b'*40),('audience','human'),('policy_version','retired')]:
            with self.subTest(column=column):
                old=self.store.one('SELECT '+column+' FROM qualifications')[column]
                self.store.conn.execute('UPDATE qualifications SET '+column+'=?',(value,))
                self.assertFalse(qualification_status(self.store,self.workflow.project(101))['eligible'])
                self.store.conn.execute('UPDATE qualifications SET '+column+'=?',(old,))

    def test_expired_or_future_qualification_is_not_current(self):
        for days in (-31,1):
            stamp=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=days)).isoformat()
            self.store.conn.execute('UPDATE qualifications SET created_at=?',(stamp,))
            self.assertEqual(qualification_status(self.store,self.workflow.project(101))['reason'],'qualification_expired')

    def test_selection_cannot_pass_archived_or_incomplete_snapshot(self):
        self.store.conn.execute('UPDATE snapshots SET complete=0')
        with self.assertRaisesRegex(ValueError,'Complete evidence'):
            self.workflow.apply('qualify',self.qualification(),'reviewer')
        self.workflow.apply('qualify',self.qualification(outcome='hold'),'reviewer')
        self.assertFalse(qualification_status(self.store,self.workflow.project(101))['eligible'])

    def setup_description(self):
        metadata=json.loads(self.workflow.project(101)['metadata'])
        metadata['description']='An open source Agent runtime.'
        self.store.conn.execute('UPDATE projects SET metadata=? WHERE id=101',(dump(metadata),))
        return {'field':'description','language':'zh-CN','source_sha256':hashlib.sha256(metadata['description'].encode()).hexdigest(),
                'content':'一个开源 Agent 运行时。','method':'assistant'}

    def test_translation_preserves_original_and_records_source(self):
        data=self.setup_description()
        save_translation(self.store,101,data,'reviewer')
        result=dossier(self.store,self.workflow.project(101))['fields']['description']
        self.assertEqual(result['source']['text'],'An open source Agent runtime.')
        self.assertEqual(result['languages']['zh-CN']['translation']['content'],data['content'])
        self.assertEqual(result['languages']['en']['status'],'missing')
        self.assertEqual(self.store.one("SELECT actor FROM events WHERE action='dossier-translated'")['actor'],'reviewer')

    def test_source_change_hides_stale_translation_and_rejects_outdated_save(self):
        data=self.setup_description()
        save_translation(self.store,101,data,'reviewer')
        metadata=json.loads(self.workflow.project(101)['metadata']);metadata['description']='Different purpose.'
        self.store.conn.execute('UPDATE projects SET metadata=?',(dump(metadata),))
        field=dossier(self.store,self.workflow.project(101))['fields']['description']['languages']['zh-CN']
        self.assertEqual(field['status'],'stale');self.assertIsNone(field['translation'])
        with self.assertRaisesRegex(ValueError,'source changed'):
            save_translation(self.store,101,data,'reviewer')

    def test_translation_update_is_immutable_idempotent_history(self):
        data=self.setup_description()
        first=save_translation(self.store,101,data,'reviewer')
        replay=save_translation(self.store,101,data,'reviewer')
        self.assertEqual(first['id'],replay['id']);self.assertTrue(replay['duplicate'])
        save_translation(self.store,101,{**data,'content':'修订后的中文译文。'},'editor')
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM dossier_translations')['n'],2)
        self.assertEqual(self.store.one('SELECT content FROM dossier_translations WHERE id=?',(first['id'],))['content'],data['content'])

    def test_missing_readme_cannot_be_marked_translated(self):
        data=self.setup_description()
        with self.assertRaisesRegex(ValueError,'missing'):
            save_translation(self.store,101,{**data,'field':'readme','source_sha256':hashlib.sha256(b'').hexdigest()},'reviewer')

    def test_readme_summary_and_translation_are_distinct(self):
        source='A detailed README with setup instructions.'
        self.store.conn.execute('UPDATE snapshots SET evidence=?',(dump({'readme_text':source}),))
        save_translation(self.store,101,{'field':'readme_summary','language':'zh-CN','source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'content':'此文档介绍项目安装。','method':'human'},'reviewer')
        result=dossier(self.store,self.workflow.project(101))['fields']
        self.assertEqual(result['readme_summary']['languages']['zh-CN']['status'],'current')
        self.assertEqual(result['readme']['languages']['zh-CN']['status'],'missing')

    def test_translation_batch_import_rolls_back_if_any_source_is_stale(self):
        self.setup_description()
        work=export_translation_work(self.store)
        self.assertEqual(work['count'],1)
        item={**work['items'][0],'content':'已翻译的简介。'}
        with self.assertRaises(ValueError):
            import_translations(self.store,{'items':[item,{**item,'source_sha256':'0'*64}]},'translator')
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM dossier_translations')['n'],0)
        self.assertEqual(import_translations(self.store,{'items':[item]},'translator')['count'],1)
        self.assertEqual(export_translation_work(self.store)['count'],0)

    def test_criteria_and_translation_endpoints_preserve_role_boundaries(self):
        from worker.service import Application, ApiFailure
        from worker.auth import Principal
        app=Application(self.store.path,{'users':[{'actor':'reviewer','roles':['reviewer'],'token_sha256':hashlib.sha256(b'synthetic-criteria-review-token').hexdigest()}]})
        reader=Principal('reader',frozenset({'auditor'}))
        operator=Principal('operator',frozenset({'operator'}))
        agent=Principal('agent',frozenset({'agent'}))
        self.assertEqual(app.handle('GET','/api/criteria',principal=reader)[0],200)
        with self.assertRaises(ApiFailure): app.handle('GET','/api/criteria',principal=agent)
        data=self.setup_description()
        path='/api/projects/101/translations'
        with self.assertRaises(ApiFailure): app.handle('POST',path,body=data,request_key='translation-key-1',principal=reader)
        response=app.handle('POST',path,body=data,request_key='translation-key-1',principal=operator)
        self.assertEqual(response[0],200)
        self.assertEqual(app.handle('POST',path,body=data,request_key='translation-key-1',principal=operator),response)
        detail=app.handle('GET','/api/projects/101',principal=reader)[1]
        self.assertTrue(detail['qualification']['eligible'])
        self.assertEqual(detail['dossier']['fields']['description']['languages']['zh-CN']['status'],'current')

    def test_schema_five_migration_preserves_accounts_and_business(self):
        from worker.accounts import Accounts
        from worker.db import SCHEMA_VERSION
        accounts=Accounts(self.store.path);accounts.ensure_default_admin(allow_legacy_default=True)
        actor=accounts.authenticate({'username':'OpenDev','password':'123456'}).actor
        self.store.conn.execute('DROP TABLE qualifications')
        self.store.conn.execute('DROP TABLE dossier_translations')
        self.store.conn.execute('UPDATE schema_version SET version=5')
        self.store.close()
        self.store=Store(Path(self.temp.name)/'test.db')
        self.workflow=Workflow(self.store)
        self.assertEqual(self.store.schema_version,SCHEMA_VERSION)
        self.assertEqual(self.workflow.project(101)['head_sha'],SHA)
        self.assertEqual(Accounts(self.store.path).authenticate({'username':'OpenDev','password':'123456'}).actor,actor)
        self.assertIsNone(qualification_status(self.store,self.workflow.project(101))['assessment'])


if __name__=='__main__': unittest.main()
