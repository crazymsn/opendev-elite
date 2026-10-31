import unittest
import test_browser_auth as fixtures
from worker.demo import ready, review_one
from worker.workflow import Workflow


class InboxV04Tests(unittest.TestCase):
    setUp = fixtures.BrowserAuthTests.setUp
    tearDown = fixtures.BrowserAuthTests.tearDown
    request = fixtures.BrowserAuthTests.request
    login = fixtures.BrowserAuthTests.login

    def test_localized_inbox_preserves_repository_names_and_raw_errors(self):
        from worker.inbox import _item
        item = _item('pending_experiences', {'id':'synthetic-experience','project_id':101,'audience':'human'},
                     {101:'团队/项目代码'}, {})
        self.assertEqual(item['title_en'], 'Team experience · 团队/项目代码')
        self.assertIn('passed technical review', item['reason_en'])
        item = _item('dead_jobs', {'id':1,'error':'原始错误：项目 <source>'}, {}, {})
        self.assertEqual(item['reason_en'], '原始错误：项目 <source>')
        self.assertEqual(item['reason'], item['reason_en'])

    def experience(self):
        w = Workflow(self.store)
        if not w.passed(w.project(101), 1):
            review_one(w)
        return w.apply('experience-create', {'project_id': 101, 'audience': 'human', 'spec': {
            'scenario': 'synthetic task', 'success_criteria': 'correct result', 'environment': 'fixture',
            'input': 'fixture', 'budget': {'seconds': 30}}}, 'reviewer')['id']

    def test_anonymous_cannot_read_inbox_or_specific_record(self):
        eid = self.experience()
        self.assertEqual(self.request('/api/inbox')[0], 401)
        self.assertEqual(self.request('/api/records/experiences/' + eid)[0], 401)

    def test_role_counts_and_status_are_filtered_consistently(self):
        self.experience()
        cookie, _ = self.login('reviewer')
        code, _, inbox = self.request('/api/inbox', cookie=cookie)
        self.assertEqual(code, 200)
        self.assertEqual(inbox['counts']['pending_experiences'], 1)
        self.assertNotIn('ready_for_contact', inbox['counts'])
        self.assertNotIn('dead_jobs', inbox['counts'])
        status = self.request('/api/status', cookie=cookie)[2]
        self.assertEqual(len(status['pending_experiences']), 1)
        self.assertNotIn('blocked_contacts', status)
        self.assertNotIn('dead_jobs', status)
        self.assertIn('due_followups', status)
        self.assertEqual(self.request('/api/inbox?kind=dead_jobs', cookie=cookie)[0], 403)

    def test_operator_sees_only_contact_and_operations_categories(self):
        ready(Workflow(self.store))
        cookie, _ = self.login('operator')
        code, _, inbox = self.request('/api/inbox?kind=ready_for_contact', cookie=cookie)
        self.assertEqual(code, 200)
        self.assertEqual(inbox['total'], 1)
        self.assertEqual(inbox['items'][0]['href'], '#projects/101')
        self.assertNotIn('pending_experiences', inbox['counts'])
        self.assertEqual(self.request('/api/inbox', cookie=cookie)[0], 403)
        self.store.conn.execute('UPDATE maintainers SET do_not_contact=1 WHERE id=201')
        inbox = self.request('/api/inbox?kind=ready_for_contact', cookie=cookie)[2]
        self.assertEqual(inbox['total'], 0)
        self.assertEqual(inbox['counts']['blocked_contacts'], 1)

    def test_strict_query_validation(self):
        cookie, _ = self.login('reviewer')
        for query in ('kind=unknown', 'kind=', 'kind=pending_experiences&kind=running_experiences',
                      'page=0', 'page=-1', 'page=1.2', 'page=', 'page=1&page=2',
                      'page_size=201', 'page_size=', 'page=100001', 'q=secret'):
            with self.subTest(query=query):
                self.assertEqual(self.request('/api/inbox?' + query, cookie=cookie)[0], 400)

    def test_pagination_keeps_counts_and_does_not_mutate(self):
        self.experience()
        self.experience()
        cookie, _ = self.login('reviewer')
        before = self.store.one('SELECT COUNT(*) AS n FROM events')['n']
        first = self.request('/api/inbox?page_size=1', cookie=cookie)[2]
        second = self.request('/api/inbox?page_size=1&page=2', cookie=cookie)[2]
        self.assertEqual(first['total'], 2)
        self.assertEqual(first['counts'], second['counts'])
        self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])
        self.assertEqual(self.store.one('SELECT COUNT(*) AS n FROM events')['n'], before)

    def test_record_deep_link_uses_original_record_permissions(self):
        eid = self.experience()
        reviewer, _ = self.login('reviewer')
        code, _, data = self.request('/api/records/experiences/' + eid, cookie=reviewer)
        self.assertEqual(code, 200)
        self.assertEqual(data['item']['id'], eid)
        self.assertEqual(self.request('/api/records/experiences/missing', cookie=reviewer)[0], 404)
        operator, _ = self.login('operator')
        self.assertEqual(self.request('/api/records/experiences/' + eid, cookie=operator)[0], 403)

    def test_snapshot_invalidity_moves_experience_out_of_actionable_list(self):
        eid = self.experience()
        self.store.conn.execute('UPDATE snapshots SET complete=0 WHERE project_id=101')
        cookie, _ = self.login('reviewer')
        data = self.request('/api/inbox?kind=stale_experiences', cookie=cookie)[2]
        self.assertEqual(data['items'][0]['id'], eid)
        self.assertEqual(data['counts']['pending_experiences'], 0)
        self.assertEqual(data['items'][0]['href'], '#experiences')

    def test_public_login_assets_do_not_expose_records(self):
        self.experience()
        for path in ('/', '/assets/landing.js', '/assets/landing.css', '/assets/elite.css'):
            code, headers, body = self.request(path)
            self.assertEqual(code, 200)
            self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
            self.assertNotIn(b'synthetic-demo/project-101', body)
        html = self.request('/')[2].decode()
        self.assertIn('id="account-login-form"', html)
        self.assertNotIn('landing-hero', html)
        self.assertNotIn('交互示例', html)
        self.assertEqual(self.request('/assets/../service.py')[0], 401)

    def test_second_server_cannot_share_the_same_live_port(self):
        from worker.service import Server
        with self.assertRaises(OSError):
            Server(('127.0.0.1', self.port), self.app)

    def test_coverage_record_uses_its_job_primary_key(self):
        from worker.db import now
        self.store.conn.execute("INSERT INTO jobs(id,kind,payload,dedupe_key,state,available_at,created_at) VALUES(123,'search','{}','fixture-record-read','done',0,?)", (now(),))
        self.store.conn.execute("INSERT INTO search_coverage(job_id,query,total_count,fetched,status,details,updated_at) VALUES(123,'fixture',1,1,'complete','{}',?)", (now(),))
        cookie, _ = self.login('reviewer')
        status, _, body = self.request('/api/records/search_coverage/123', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(body['item']['job_id'], 123)
        self.assertEqual(self.request('/api/records/search_coverage/124', cookie=cookie)[0], 404)


if __name__ == '__main__':
    unittest.main()
