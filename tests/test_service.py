import base64
import hashlib
import http.client
import json
import socket
import ssl
import shutil
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from worker.auth import IdentityStore, create_key
from worker.db import Store
from worker.demo import seed, review_one, SHA
from worker.service import Application, ApiFailure, Server, make_server
from worker.workflow import Workflow


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'test.db'
        self.s = Store(self.path); seed(self.s)
        roles = {'reviewer':['reviewer'],'operator':['operator'],'agent-one':['agent'],
                 'agent-two':['agent'],'approver':['approver'],'disburser':['disburser'],'auditor':['auditor']}
        self.tokens = {name:name+'-'+'x'*48 for name in roles}
        self.config = {'users':[{'actor':name,'roles':r,'token_sha256':hashlib.sha256(self.tokens[name].encode()).hexdigest()} for name,r in roles.items()]}
        self.app = Application(self.path,self.config)

    def tearDown(self):
        self.s.close(); self.temp.cleanup()

    def call(self,path,body=None,actor='reviewer',key='test-request-001',method='POST'):
        return self.app.handle(method,path,'Bearer '+self.tokens[actor],body,key)

    def pending(self):
        w = Workflow(self.s); review_one(w)
        return w.apply('experience-create',{'project_id':101,'audience':'agent','spec':{
            'scenario':'synthetic task','success_criteria':'expected answer','environment':'offline',
            'input':'fixture','budget':{'seconds':60}}},'reviewer')['id']

    def result(self,claimed,success=True):
        task = claimed['task']
        return {'id':task['id'],'sha':task['sha'],'run_id':task['run_id'],
                'task_sha256':claimed['task_sha256'],'lease_token':claimed['lease_token'],
                'result':{'success':success,'executor':'untrusted-name','environment':'offline',
                          'steps':['performed task'],'actual_result':'expected answer' if success else 'missing dependency',
                          'artifacts':['run.log'] if success else [],'failure_kind':'none' if success else 'environment'}}

    def test_anonymous_and_role_denial(self):
        with self.assertRaises(ApiFailure) as error:
            self.app.handle('GET','/api/projects')
        self.assertEqual(error.exception.status,401)
        with self.assertRaises(ApiFailure) as error:
            self.call('/api/actions/review',{},actor='agent-one')
        self.assertEqual(error.exception.status,403)
        with self.assertRaises(ApiFailure): self.call('/api/records/grants',actor='reviewer',method='GET')
        self.assertEqual(self.app.handle('GET','/healthz'),(200,{'ok':True}))

    def test_stable_project_uid_routes_resolve_to_legacy_project(self):
        uid = self.s.one('SELECT uid FROM projects WHERE id=101')['uid']
        status, detail = self.call('/api/projects/' + uid, method='GET')
        self.assertEqual(status, 200)
        self.assertEqual(detail['project']['id'], 101)
        status, rankings = self.call('/api/projects/' + uid + '/rankings', method='GET')
        self.assertEqual(status, 200)
        self.assertIn('items', rankings)

    def test_actor_cannot_be_spoofed(self):
        with self.assertRaises(ApiFailure):
            self.call('/api/actions/assign',{'actor':'approver','project_id':101,'owner':'x','audience':'both'})
        self.call('/api/actions/assign',{'project_id':101,'owner':'person','audience':'both'})
        self.assertEqual(self.s.one('SELECT actor FROM events ORDER BY id DESC LIMIT 1')['actor'],'reviewer')

    def test_local_idempotency_under_concurrent_requests(self):
        payload = {'project_id':101,'owner':'person','audience':'both'}
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(lambda _:self.call('/api/actions/assign',payload),range(2)))
        self.assertEqual(values[0],values[1])
        self.assertEqual(self.s.one("SELECT COUNT(*) AS n FROM events WHERE action='assign'")['n'],1)
        with self.assertRaises(ApiFailure) as error:
            self.call('/api/actions/assign',dict(payload,owner='different'))
        self.assertEqual(error.exception.status,409)

    def test_manual_receipt_is_not_exposed(self):
        with self.assertRaises(ApiFailure) as error:
            self.call('/api/actions/grant-reconcile',{},actor='disburser')
        self.assertEqual(error.exception.status,404)

    def test_agent_claim_binding_wrong_worker_and_current_round(self):
        eid = self.pending()
        _, claimed = self.call('/api/agent/claim',{},actor='agent-one')
        payload = self.result(claimed)
        with self.assertRaises(ApiFailure):
            self.call(f'/api/agent/tasks/{eid}/result',payload,actor='agent-two',key='result-other-worker')
        wrong = dict(payload,run_id='wrong')
        with self.assertRaises(ValueError):
            self.call(f'/api/agent/tasks/{eid}/result',wrong,actor='agent-one',key='result-wrong-run')
        evidence = b'Synthetic offline run output'
        self.call(f'/api/agent/tasks/{eid}/artifacts', {'lease_token': claimed['lease_token'],
            'run_id': claimed['task']['run_id'], 'path': 'run.log', 'sha256': hashlib.sha256(evidence).hexdigest(),
            'size': len(evidence), 'chunk_index': 0, 'total_chunks': 1,
            'data_base64': base64.b64encode(evidence).decode()}, actor='agent-one', key='upload-run-log')
        _, result = self.call(f'/api/agent/tasks/{eid}/result',payload,actor='agent-one',key='result-correct-run')
        self.assertTrue(result['success'])
        stored = json.loads(self.s.one('SELECT result FROM experiences WHERE id=?',(eid,))['result'])
        self.assertEqual(stored['executor'],'agent-one')
        self.assertIsNone(Workflow(self.s).latest_review(101,2))

    def test_old_lease_cannot_complete_reassigned_task(self):
        eid = self.pending()
        _, first = self.call('/api/agent/claim',{},actor='agent-one')
        self.s.conn.execute('UPDATE agent_leases SET lease_until=0')
        _, second = self.call('/api/agent/claim',{},actor='agent-two')
        self.assertNotEqual(first['task']['run_id'],second['task']['run_id'])
        with self.assertRaises(ApiFailure):
            self.call(f'/api/agent/tasks/{eid}/result',self.result(first),actor='agent-one',key='stale-completion')
        self.call(f'/api/agent/tasks/{eid}/result',self.result(second,False),actor='agent-two',key='new-completion')
        self.assertEqual(self.s.one('SELECT status FROM experiences WHERE id=?',(eid,))['status'],'completed')

    def test_same_sha_new_review_invalidates_agent_lease(self):
        eid = self.pending()
        _, claimed = self.call('/api/agent/claim',{},actor='agent-one')
        review_one(Workflow(self.s))
        with self.assertRaises(ApiFailure):
            self.call(f'/api/agent/tasks/{eid}/heartbeat',{'lease_token':claimed['lease_token']},actor='agent-one',key='stale-heartbeat')

    def test_reviewer_cannot_bypass_agent_lease(self):
        eid=self.pending()
        with self.assertRaises(ApiFailure):
            self.call('/api/actions/experience-result',{'id':eid,'sha':SHA,'result':{}},key='manual-agent-bypass')

    def test_import_deduplication_and_invalid_input(self):
        _, result = self.call('/api/import',{'repos':['a/b','https://github.com/a/b']},actor='operator')
        self.assertEqual(len(result['job_ids']),1)
        with self.assertRaises(ApiFailure):
            self.call('/api/import',{'repos':[123]},actor='operator',key='bad-repo-input')

    def test_key_file_is_exclusive_and_not_returned_as_plaintext(self):
        secret = Path(self.temp.name)/'reviewer.token'
        outcome = create_key('reviewer',['reviewer'],secret)
        token = secret.read_text().strip()
        self.assertNotIn(token,json.dumps(outcome))
        identity = IdentityStore({'users':[outcome['identity']]})
        self.assertEqual(identity.authenticate('Bearer '+token).actor,'reviewer')
        with self.assertRaises(FileExistsError): create_key('reviewer',['reviewer'],secret)
        with self.assertRaises(ValueError): IdentityStore({'users':[dict(outcome['identity'],roles=['agent','approver'])]})

    def test_real_http_boundary_valid_and_malformed_requests(self):
        server = Server(('127.0.0.1',0),self.app)
        thread = threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.05},daemon=True)
        thread.start()
        def request(method,path,body=None,headers=None):
            connection = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            try:
                connection.request(method,path,body=body,headers=headers or {})
                response=connection.getresponse()
                return response.status,json.loads(response.read()),dict(response.getheaders())
            finally: connection.close()
        try:
            self.assertEqual(request('GET','/healthz')[0],200)
            self.assertEqual(request('GET','/api/projects')[0],401)
            headers={'Authorization':'Bearer '+self.tokens['operator'],'Content-Type':'application/json','Idempotency-Key':'http-import-001'}
            self.assertEqual(request('POST','/api/import','{"repos":["a/b"]}',headers)[0],200)
            self.assertEqual(request('POST','/api/import','{broken',headers)[0],400)
            self.assertEqual(request('POST','/api/import','{"repos":[NaN]}',headers)[0],400)
            self.assertEqual(request('POST','/api/import','{}',dict(headers,Origin='https://untrusted.invalid'))[0],403)
            self.assertEqual(request('DELETE','/api/projects',headers=headers)[0],405)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    @unittest.skipUnless(shutil.which('openssl'),'OpenSSL executable is needed for ephemeral TLS test certificate')
    def test_idle_tls_connection_cannot_block_other_requests(self):
        cert=Path(self.temp.name)/'test.crt';key=Path(self.temp.name)/'test.key'
        subprocess.run([shutil.which('openssl'),'req','-x509','-newkey','rsa:2048','-nodes',
                        '-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost'],
                       check=True,timeout=20,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        server=make_server(self.path,self.config,port=0,tls_cert=cert,tls_key=key)
        # A peer withholding ClientHello must occupy only its own worker thread.
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.05},daemon=True)
        thread.start();idle=None;connection=None
        try:
            idle=socket.create_connection(('127.0.0.1',server.server_port),timeout=3)
            connection=http.client.HTTPSConnection('127.0.0.1',server.server_port,timeout=3,
                                                   context=ssl._create_unverified_context())
            connection.request('GET','/healthz');response=connection.getresponse()
            self.assertEqual(response.status,200);self.assertEqual(json.loads(response.read()),{'ok':True})
        finally:
            if connection:connection.close()
            if idle:idle.close()
            server.shutdown();server.server_close();thread.join(timeout=2)

    def test_remote_plain_http_bind_is_refused(self):
        with self.assertRaises(ValueError):make_server(self.path,self.config,host='0.0.0.0',port=0)


if __name__ == '__main__': unittest.main()
