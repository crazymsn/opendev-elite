import json
from pathlib import Path
import tempfile
import unittest
from worker.auth import Principal
from worker.db import Store, dump, now
from worker.demo import seed, ready
from worker.inbox import inbox, InboxFailure
from worker.projects import readiness
from worker.report import summary, EARLY_WORK
from worker.workflow import Workflow


class EarlyInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=Store(Path(self.temp.name)/'db');self.addCleanup(self.s.close)
        for pid in range(101,109):seed(self.s,project_id=pid,maintainer_id=pid+100)
        self.s.conn.execute("UPDATE projects SET owner=NULL,audience='unknown' WHERE id=101")
        self.s.conn.execute('UPDATE snapshots SET complete=0 WHERE project_id=102')
        self.s.conn.execute('DELETE FROM qualifications WHERE project_id=103')
        for pid,stage in ((105,'hold'),(106,'rejected'),(107,'closed')):
            self.s.conn.execute('UPDATE projects SET owner=NULL,stage=? WHERE id=?',(stage,pid))
        p=Workflow(self.s).project(108);metadata=json.loads(p['metadata']);metadata['archived']=True
        self.s.conn.execute('UPDATE projects SET metadata=? WHERE id=108',(dump(metadata),))

    def test_exactly_one_early_queue_uses_shared_next_step(self):
        before=self.s.one('SELECT COUNT(*) AS n FROM events')['n']
        result=summary(self.s)
        expected={'unassigned_projects':[101],'awaiting_collection':[102],'pending_selection':[103],'pending_technical_reviews':[104]}
        for kind,ids in expected.items():
            self.assertEqual([r['id'] for r in result[kind]],ids)
            for pid in ids:self.assertEqual(readiness(self.s,Workflow(self.s).project(pid))['next_step'],EARLY_WORK[kind][0])
        self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM events')['n'],before)

    def test_status_role_filter_count_and_destination(self):
        reviewer=Principal('reviewer',frozenset({'reviewer'}))
        data=inbox(self.s,reviewer,{'kind':['pending_selection']})
        self.assertEqual(data['items'][0]['href'],'#projects/103/selection')
        self.assertNotIn('awaiting_collection',data['counts'])
        self.assertEqual(data['counts']['unassigned_projects'],1)
        self.assertEqual(inbox(self.s,reviewer,{'kind':['pending_technical_reviews']})['items'][0]['href'],'#projects/104/reviews')
        operator=Principal('operator',frozenset({'operator'}))
        data=inbox(self.s,operator,{'kind':['awaiting_collection']})
        self.assertEqual(data['items'][0]['href'],'#projects/102')
        self.assertNotIn('pending_technical_reviews',data['counts'])
        with self.assertRaises(InboxFailure):inbox(self.s,operator,{'kind':['pending_technical_reviews']})

    def test_issued_grant_followup_takes_priority_even_with_missing_assignment(self):
        self.s.conn.execute("INSERT INTO contacts VALUES('fixture-contact',101,201,'email','fixture','replied','operator','fixture',?)",(now(),))
        self.s.conn.execute("INSERT INTO interviews VALUES(1,101,'fixture-contact','operator','{}','[\"undetermined\"]',?)",(now(),))
        self.s.conn.execute("INSERT INTO decisions VALUES('fixture-decision',101,1,'approve','approver','fixture','{}',?)",(now(),))
        self.s.conn.execute("INSERT INTO grants VALUES('fixture-grant','fixture-decision','issued',NULL,?)",(now(),))
        self.assertEqual(summary(self.s)['unassigned_projects'],[])
        self.assertEqual(readiness(self.s,Workflow(self.s).project(101))['next_step'],'followup-plan')

    def test_profile_requires_valid_noncontradictory_tags_and_real_text(self):
        w=Workflow(self.s);ready(w,project_id=104)
        c=w.apply('contact-draft',{'project_id':104,'maintainer_id':204,'channel':'email','draft':'SYNTHETIC'},'operator')['id']
        w.apply('contact-status',{'id':c,'status':'sent','evidence':'SYNTHETIC'},'operator')
        w.apply('contact-status',{'id':c,'status':'replied','evidence':'SYNTHETIC'},'operator')
        notes={k:'SYNTHETIC interview evidence' for k in ('users','commitment','roadmap','credit_need','milestone')};notes['consent']=True
        body={'project_id':104,'contact_id':c,'notes':notes,'profile':['engineer']}
        for profile in ([],['undetermined','engineer'],['engineer','engineer'],[{}],['invalid']):
            with self.assertRaises(ValueError):w.apply('interview',dict(body,profile=profile),'operator')
        for value in (True,23,{},'   '):
            with self.assertRaises(ValueError):w.apply('interview',dict(body,notes=dict(notes,users=value)),'operator')
        for profile in (['undetermined'],['engineer'],['engineer','entrepreneur']):
            w.apply('interview',dict(body,profile=profile),'operator')
        self.assertEqual(self.s.one('SELECT COUNT(*) AS n FROM interviews')['n'],3)


if __name__=='__main__':unittest.main()
