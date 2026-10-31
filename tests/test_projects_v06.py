import json
import tempfile
import unittest
from pathlib import Path
from worker.db import Store,dump
from worker.demo import seed,ready
from worker.dossier import save_translation
from worker.projects import list_projects,readiness
from worker.workflow import Workflow
import hashlib

class ProjectQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name)/'db')
        seed(self.s);seed(self.s,project_id=102)
        self.s.conn.execute('DELETE FROM qualifications WHERE project_id=102')
        p=Workflow(self.s).project(101);m=json.loads(p['metadata']);m['description']='Agent runtime for developers'
        self.s.conn.execute('UPDATE projects SET metadata=? WHERE id=101',(dump(m),))
        self.translation={'field':'description','language':'zh-CN','source_sha256':hashlib.sha256(m['description'].encode()).hexdigest(),'content':'开发者使用的智能体运行时','method':'human'}
        save_translation(self.s,101,self.translation,'translator')
    def tearDown(self):self.s.close();self.temp.cleanup()
    def test_chinese_and_english_search_return_current_description(self):
        for term in ('开发者','RUNTIME','project-101'):
            r=list_projects(self.s,{'q':[term]});self.assertEqual(r['total'],1)
            self.assertEqual(r['items'][0]['description_zh'],self.translation['content'])
        self.assertEqual(list_projects(self.s,{'q':['%']})['total'],0)
    def test_stale_translation_is_neither_displayed_nor_searchable(self):
        m=json.loads(Workflow(self.s).project(101)['metadata']);m['description']='New description'
        self.s.conn.execute('UPDATE projects SET metadata=? WHERE id=101',(dump(m),))
        self.assertEqual(list_projects(self.s,{'q':['开发者']})['total'],0)
        self.assertIsNone(list_projects(self.s,{'q':['New description']})['items'][0]['description_zh'])
    def test_qualification_filters_share_gate_logic(self):
        self.assertEqual(list_projects(self.s,{'selection':['include']})['total'],1)
        self.assertEqual(list_projects(self.s,{'selection':['unassessed']})['total'],1)
        self.s.conn.execute("UPDATE projects SET head_sha='new-commit' WHERE id=101")
        self.assertEqual(list_projects(self.s,{'selection':['include']})['total'],0)
        self.assertEqual(list_projects(self.s,{'selection':['stale']})['total'],1)
    def test_incomplete_selection_evidence_is_stale_in_list_and_detail(self):
        self.s.conn.execute("UPDATE qualifications SET evidence='{}' WHERE project_id=101")
        self.assertEqual(list_projects(self.s,{'selection':['include']})['total'],0)
        self.assertEqual(list_projects(self.s,{'selection':['stale']})['total'],1)
        self.assertFalse(readiness(self.s,Workflow(self.s).project(101))['qualification_current'])

    def test_strict_filters_and_pagination(self):
        for query in ({'q':['a','b']},{'stage':['bogus']},{'page':['0']},{'selection':['approve']},{'unknown':['x']}):
            with self.assertRaises(ValueError):list_projects(self.s,query)
        first=list_projects(self.s,{'page_size':['1'],'sort':['name']})
        second=list_projects(self.s,{'page_size':['1'],'page':['2'],'sort':['name']})
        self.assertEqual(first['total'],2);self.assertNotEqual(first['items'][0]['id'],second['items'][0]['id'])
    def test_readiness_has_current_reviews_and_real_audience_requirements(self):
        p=Workflow(self.s).project(101)
        r=readiness(self.s,p);self.assertEqual(r['next_step'],'review-one')
        self.assertFalse(r['allowed']['experience-create'])
        ready(Workflow(self.s));r=readiness(self.s,p)
        self.assertTrue(r['round_two_current']);self.assertTrue(r['allowed']['contact-draft'])
        self.assertEqual(r['required_audiences'],['human','agent'])

if __name__=='__main__':unittest.main()
