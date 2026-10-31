import json
import hashlib
from pathlib import Path
import tempfile
import unittest

from worker.auth import Principal
from worker.db import Store, dump, now
from worker.demo import seed
from worker.service import Application, ApiFailure


class DevelopersTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'db'
        self.store=Store(self.path)
        self.addCleanup(self.store.close)
        seed(self.store);seed(self.store,project_id=102)
        self.store.conn.execute("UPDATE maintainers SET login='same-login',profile=?,do_not_contact=1 WHERE id=201",
                                (dump({'name':'公开名字','bio':'<script>source text</script>','email':'private@example.invalid','company':'Self-reported Co','private':'not projected'}),))
        self.store.conn.execute('UPDATE project_maintainers SET confirmed=0 WHERE project_id=102')
        seed(self.store,project_id=103,maintainer_id=202)
        self.store.conn.execute("UPDATE maintainers SET login='same-login',kind='Organization' WHERE id=202")
        self.store.conn.execute("INSERT INTO contacts VALUES('source-contact',101,201,'email','PRIVATE DRAFT','replied','operator','fixture',?)",(now(),))
        self.store.conn.execute("INSERT INTO interviews VALUES(1,101,'source-contact','operator',?,?,?)",(dump({'private_notes':'PRIVATE NOTES'}),dump(['engineer']),now()))
        self.store.conn.execute("INSERT INTO decisions VALUES('decision-source',101,1,'approve','approver','PRIVATE RATIONALE','{}',?)",(now(),))
        self.store.conn.execute("INSERT INTO grants VALUES('grant-source','decision-source','issued','PRIVATE RECEIPT',?)",(now(),))
        self.store.conn.execute("INSERT INTO followups VALUES('followup-source','grant-source','2099-01-01','pending',NULL)")
        self.app=Application(self.path,{'users':[{'actor':'fixture','roles':['admin'],'token_sha256':hashlib.sha256(b'synthetic-developer-test-credential').hexdigest()}]})

    def get(self,path,role='reviewer'):
        return self.app.handle('GET',path,principal=Principal(role,frozenset({role})))[1]

    def test_stable_id_grouping_candidate_relations_and_public_projection(self):
        data=self.get('/api/developers?q=same-login')
        self.assertEqual(data['total'],2)  # same login is not sufficient to merge identities
        row=next(r for r in data['items'] if r['id']==201)
        self.assertEqual((row['project_count'],row['confirmed_count']),(2,1))
        self.assertNotIn('private@example.invalid',dump(data));self.assertNotIn('do_not_contact',dump(data))
        self.assertEqual(row['bio'],'<script>source text</script>')
        self.assertEqual(self.get('/api/developers?q=公开名字')['total'],1)
        self.assertEqual(self.get('/api/developers?q=project-102')['total'],1)
        self.assertEqual(self.get('/api/developers?q=%25')['total'],0)
        self.assertEqual(self.get('/api/developers?kind=Organization')['total'],1)
        self.assertEqual(self.get('/api/developers?relationship=candidate')['total'],1)
        self.assertEqual(self.get('/api/developers?relationship=confirmed')['total'],2)

    def test_role_projection_applies_to_private_metadata_and_history_counts(self):
        expectations={'reviewer':['followups'],'operator':['contacts','interviews','followups'],
                      'approver':['interviews','grants'],'disburser':['grants'],'auditor':['grants','followups'],
                      'admin':['contacts','interviews','grants','followups']}
        for role,kinds in expectations.items():
            with self.subTest(role=role):
                d=self.get('/api/developers/201',role)
                self.assertEqual(d['history_kinds'],kinds)
                self.assertEqual('latest_interview' in d,'interviews' in kinds)
                self.assertEqual('contact_preference' in d,'contacts' in kinds)
                for kind in ('contacts','interviews','grants','followups'):
                    if kind in kinds:
                        records=self.get('/api/developers/201/records/'+kind,role)
                        self.assertEqual(records['total'],1)
                        self.assertNotIn('PRIVATE',dump(records))
                    else:
                        with self.assertRaises(ApiFailure) as exc:self.get('/api/developers/201/records/'+kind,role)
                        self.assertEqual(exc.exception.status,403)
        self.assertEqual(self.get('/api/developers/201','operator')['contact_preference'],'declined')
        self.assertEqual(self.get('/api/developers/201','approver')['latest_interview']['id'],1)
        self.assertEqual(self.get('/api/developers/202/records/interviews','operator')['total'],0)
        self.assertIsNone(self.get('/api/developers/202','operator')['latest_interview'])

    def test_detail_paginates_relationships_and_retains_project_selection(self):
        first=self.get('/api/developers/201?page_size=1')['projects']
        second=self.get('/api/developers/201?page_size=1&page=2')['projects']
        self.assertEqual(first['total'],2)
        self.assertNotEqual(first['items'][0]['id'],second['items'][0]['id'])
        self.assertEqual(first['items'][0]['selection'],'include')
        self.assertEqual(second['items'][0]['relationship']['confirmed'],0)
        self.assertEqual(self.get('/api/projects?maintainer_id=201')['total'],2)

    def test_strict_queries_auth_and_read_only(self):
        count=self.store.one('SELECT COUNT(*) AS n FROM events')['n']
        for suffix in ('page=0','page=','page=1&page=2','page_size=201','kind=Bot','kind=User&kind=Organization',
                       'profile=engineer','relationship=unknown','q='+('a'*201)):
            with self.assertRaises(ValueError):self.get('/api/developers?'+suffix)
        for suffix in ('maintainer_id=','maintainer_id=0','maintainer_id=-1','maintainer_id=201&maintainer_id=202'):
            with self.assertRaises(ValueError):self.get('/api/projects?'+suffix)
        with self.assertRaises(ValueError):self.get('/api/developers/201/records/interviews?q=private','operator')
        for path in ('/api/developers','/api/developers/201','/api/developers/201/records/interviews'):
            with self.assertRaises(ApiFailure) as exc:self.get(path,'agent')
            self.assertEqual(exc.exception.status,403)
            with self.assertRaises(ApiFailure) as exc:self.app.handle('GET',path)
            self.assertEqual(exc.exception.status,401)
        with self.assertRaises(ApiFailure) as exc:self.get('/api/developers/999')
        self.assertEqual(exc.exception.status,404)
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM events')['n'],count)


if __name__=='__main__':unittest.main()
