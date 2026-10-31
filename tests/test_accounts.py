from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import uuid

from worker.accounts import Accounts, AuthThrottle, hash_password, verify_password
from worker.auth import Principal
from worker.browser_auth import BrowserFailure, BrowserSessions
from worker.db import Store, SCHEMA_VERSION
from worker.service import Application, Server

PASSWORD = 'synthetic password phrase 12345'
NEW_PASSWORD = 'different synthetic phrase 67890'
LEGACY_TOKEN = 'synthetic-legacy-reviewer-token-00000000000'


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'worker.db'
        self.config = {'users':[{'actor':'legacy-reviewer','roles':['reviewer'],
            'token_sha256':hashlib.sha256(LEGACY_TOKEN.encode()).hexdigest()}]}
        self.app = Application(self.path, self.config)
        self.server = Server(('127.0.0.1',0), self.app)
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':.01},daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temp.cleanup()

    def request(self, path, body=None, session=None, headers=None):
        extra = dict(headers or {})
        if session:
            extra['Cookie'], extra['X-CSRF-Token'] = session['cookie'], session['csrf_token']
        if body is not None:
            extra.setdefault('Origin',self.origin)
            extra['Content-Type'] = 'application/json'
            extra.setdefault('Idempotency-Key','account-test-' + uuid.uuid4().hex)
        connection = http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        connection.request('GET' if body is None else 'POST',path,
                           body=None if body is None else json.dumps(body),headers=extra)
        response = connection.getresponse()
        value = json.loads(response.read())
        result = response.status, dict(response.getheaders()), value
        connection.close()
        return result

    def registration(self, name='owner', password=PASSWORD):
        return {'username':name,'display_name':name.title(),'password':password}

    def login(self,name='OpenDev',password=None):
        password = password if password is not None else ('123456' if name.lower() == 'opendev' else PASSWORD)
        status, headers, value = self.request('/auth/password-login',{'username':name,'password':password})
        self.assertEqual(status,200,value)
        return {**value,'cookie':headers['Set-Cookie'].split(';')[0]}

    def bootstrap(self):
        return self.login()

    def member(self, admin, name='member', roles=None):
        body = {**self.registration(name),'roles':roles or ['reviewer']}
        status,_,row = self.request('/api/accounts',body,admin)
        self.assertEqual(status,200,row)
        return row

    def test_password_hashes_are_salted_slow_and_bounded(self):
        first, second = hash_password(PASSWORD), hash_password(PASSWORD)
        self.assertNotEqual(first,second)
        self.assertNotIn(PASSWORD,first)
        self.assertTrue(verify_password(PASSWORD,first))
        self.assertFalse(verify_password(NEW_PASSWORD,first))
        for password in ['short',' ' * 15,'x'*129]:
            with self.assertRaises(BrowserFailure): hash_password(password)
        self.assertFalse(verify_password(PASSWORD,'pbkdf2_sha256$999999999$invalid'))

    def test_default_admin_exists_without_setup_and_private_data_stays_private(self):
        options = self.request('/auth/options')[2]
        self.assertFalse(options['bootstrap_allowed'])
        self.assertFalse(options['registration_open'])
        self.assertEqual(self.request('/api/accounts')[0],401)
        self.assertEqual(self.request('/api/projects')[0],401)
        admin = self.bootstrap()
        self.assertEqual(admin['roles'],['admin'])
        self.assertEqual(admin['display_name'],'OpenDev')
        self.assertFalse(self.request('/auth/options')[2]['setup_required'])
        self.assertEqual(self.request('/auth/bootstrap',self.registration('other'))[0],404)
        self.assertEqual(self.request('/api/accounts',session=admin)[0],200)

    def test_public_registration_is_closed_and_admin_creates_active_members(self):
        self.assertEqual(self.request('/auth/register',self.registration('before'))[0],404)
        admin = self.bootstrap()
        self.assertEqual(self.request('/auth/register',{**self.registration('attacker'),'roles':['admin']})[0],404)
        row = self.member(admin)
        self.assertEqual(self.request('/auth/password-login',{'username':'member','password':PASSWORD})[0],200)
        self.assertEqual(row['status'],'active'); self.assertEqual(row['roles'],['reviewer'])
        self.assertNotIn('password_hash',row)

    def test_remote_origin_and_cross_origin_cannot_bootstrap(self):
        self.app.config['public_origin'] = 'https://opendev.example.invalid'
        headers = {'Host':'opendev.example.invalid','Origin':'https://opendev.example.invalid'}
        self.assertFalse(self.request('/auth/options',headers=headers)[2]['bootstrap_allowed'])
        self.assertEqual(self.request('/auth/bootstrap',self.registration(),headers=headers)[0],404)
        self.assertEqual(self.request('/auth/bootstrap',self.registration())[0],403)
        self.app.config.pop('public_origin')
        self.assertEqual(self.request('/auth/bootstrap',self.registration(),headers={'Origin':'https://evil.invalid'})[0],403)
        self.assertTrue(self.app.accounts.initialized())

    def test_repeated_valid_post_frames_receive_explicit_origin_denial(self):
        for _ in range(30):
            status, _, _ = self.request('/auth/password-login', {'username':'outsider','password':PASSWORD},
                                         headers={'Origin':'https://untrusted.invalid'})
            self.assertEqual(status,403)
        self.assertTrue(self.app.accounts.initialized())

    def test_concurrent_seed_creates_exactly_one_admin(self):
        path = Path(self.temp.name) / 'concurrent.db'
        accounts = Accounts(path)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: accounts.ensure_default_admin(allow_legacy_default=True), range(2)))
        with accounts.store() as store:
            self.assertEqual(store.one('SELECT COUNT(*) AS n FROM accounts')['n'],1)
        self.assertEqual(accounts.authenticate({'username':'OpenDev','password':'123456'}).roles,frozenset({'admin'}))

    def test_approved_member_cannot_authorize_accounts_and_csrf_is_required(self):
        admin = self.bootstrap(); row = self.member(admin)
        member = self.login('member')
        self.assertEqual(self.request('/api/accounts',session=member)[0],403)
        body = {'status':'active','roles':['admin'],'auth_version':row['auth_version']}
        self.assertEqual(self.request('/api/accounts/'+row['id'],body,member)[0],403)
        self.assertEqual(self.request('/api/accounts/'+row['id'],body,headers={'Cookie':admin['cookie']})[0],403)
        self.assertEqual(self.request('/api/projects',session=member)[0],200)

    def test_disable_role_change_and_reset_invalidate_existing_sessions(self):
        admin = self.bootstrap(); row = self.member(admin)
        old = self.login('member')
        body = {'status':'active','roles':['operator'],'auth_version':row['auth_version']}
        status,_,row = self.request('/api/accounts/'+row['id'],body,admin)
        self.assertEqual(status,200)
        self.assertEqual(self.request('/api/projects',session=old)[0],401)
        current = self.login('member')
        body = {'status':'disabled','roles':['operator'],'auth_version':row['auth_version']}
        self.assertEqual(self.request('/api/accounts/'+row['id'],body,admin)[0],200)
        self.assertEqual(self.request('/auth/me',session=current)[0],401)
        self.assertEqual(self.request('/auth/password-login',{'username':'member','password':PASSWORD})[0],401)

    def test_last_admin_and_stale_updates_are_rejected(self):
        admin = self.bootstrap()
        row = self.request('/api/accounts',session=admin)[2]['items'][0]
        body = {'status':'disabled','roles':[],'auth_version':row['auth_version']}
        self.assertEqual(self.request('/api/accounts/'+row['id'],body,admin)[0],409)
        member = self.member(admin)
        body = {'status':'active','roles':['auditor'],'auth_version':999}
        self.assertEqual(self.request('/api/accounts/'+member['id'],body,admin)[0],409)

    def test_password_change_requires_current_password_and_revokes_all_sessions(self):
        admin = self.bootstrap(); self.member(admin)
        one, two = self.login('member'), self.login('member')
        body = {'current_password':'wrong password phrase','new_password':NEW_PASSWORD}
        self.assertEqual(self.request('/auth/password',body,one)[0],403)
        body['current_password'] = PASSWORD
        self.assertEqual(self.request('/auth/password',body,one)[0],200)
        self.assertEqual(self.request('/auth/me',session=two)[0],401)
        self.assertEqual(self.request('/auth/password-login',{'username':'member','password':PASSWORD})[0],401)
        self.login('member',NEW_PASSWORD)

    def test_admin_reset_password_is_not_logged_or_fingerprinted_with_fast_hash(self):
        admin = self.bootstrap(); row = self.member(admin)
        member = self.login('member')
        path = '/api/accounts/'+row['id']
        body = {'status':'active','roles':['reviewer'],'auth_version':row['auth_version'],'password':NEW_PASSWORD}
        headers = {'Idempotency-Key':'reset-password-test'}
        self.assertEqual(self.request(path,body,admin,headers)[0],200)
        self.assertEqual(self.request(path,body,admin,headers)[0],200)
        self.assertEqual(self.request('/auth/me',session=member)[0],401)
        self.login('member',NEW_PASSWORD)
        with self.app.accounts.store() as store:
            serialized = json.dumps(store.all('SELECT * FROM api_requests')+store.all('SELECT * FROM events'))
            self.assertNotIn(PASSWORD,serialized); self.assertNotIn(NEW_PASSWORD,serialized)
            row = store.one('SELECT fingerprint FROM api_requests WHERE request_key=?',('reset-password-test',))
            raw = json.dumps(body,ensure_ascii=False,sort_keys=True)
            self.assertNotEqual(row['fingerprint'],hashlib.sha256((path+'\n'+raw).encode()).hexdigest())

    def test_restart_keeps_accounts_but_invalidates_sessions(self):
        self.bootstrap()
        second = Application(self.path,self.config)
        self.assertTrue(second.accounts.initialized())
        self.assertEqual(second.accounts.authenticate({'username':'OPENDEV','password':'123456'}).roles,frozenset({'admin'}))
        self.assertEqual(second.sessions.rows,{})

    def test_old_authenticated_principal_cannot_create_a_fresh_session_after_reset(self):
        admin = self.bootstrap(); row = self.member(admin)
        principal = self.app.accounts.authenticate({'username':'member','password':PASSWORD})
        self.request('/api/accounts/'+row['id'],{'status':'disabled','roles':row['roles'],'auth_version':row['auth_version']},admin)
        with self.assertRaises(BrowserFailure): self.app.sessions.create(principal)

    def test_rate_limit_is_bounded_and_does_not_use_forwarded_ip(self):
        limiter = AuthThrottle()
        for _ in range(10): limiter.check('127.0.0.1','member')
        with self.assertRaises(BrowserFailure) as raised: limiter.check('different-peer','member')
        self.assertEqual(raised.exception.status,429)
        self.assertLess(len(limiter.rows),10)

    def test_local_password_recovery_preserves_role_status_and_invalidates_session(self):
        from worker.accounts import reset_local_password
        admin = self.bootstrap()
        reset_local_password(self.path, 'OpenDev', NEW_PASSWORD)
        self.assertEqual(self.request('/auth/me',session=admin)[0],401)
        self.assertEqual(self.login('OpenDev',NEW_PASSWORD)['roles'],['admin'])
        self.assertTrue(self.app.accounts.initialized())
        with self.assertRaises(ValueError):
            reset_local_password(Path(self.temp.name)/'missing.db','owner',NEW_PASSWORD)

    def test_v4_migration_and_backup_preserve_business_and_accounts(self):
        admin = self.bootstrap()
        with self.app.accounts.store() as store:
            backup = Path(self.temp.name)/'backup.db'
            store.backup(backup)
        copied = Accounts(backup)
        self.assertTrue(copied.initialized())
        self.assertEqual(copied.authenticate({'username':'OpenDev','password':'123456'}).actor,admin['actor'])
        path = Path(self.temp.name)/'old.db'
        store = Store(path)
        store.conn.execute("INSERT INTO events(entity_type,entity_id,action,actor,payload,created_at) VALUES('fixture','1','keep','original','{}','2026-01-01')")
        store.conn.execute('DROP TABLE accounts'); store.conn.execute('DROP TABLE account_state')
        store.conn.execute('UPDATE schema_version SET version=4'); store.close()
        migrated = Store(path)
        try:
            self.assertEqual(migrated.schema_version,SCHEMA_VERSION)
            self.assertEqual(migrated.one('SELECT actor FROM events')['actor'],'original')
            self.assertEqual(migrated.one('SELECT initialized FROM account_state')['initialized'],0)
        finally: migrated.close()

    def test_member_creation_requires_admin_csrf_roles_and_unique_username(self):
        admin = self.bootstrap()
        body = {**self.registration('member'), 'roles':['reviewer']}
        self.assertEqual(self.request('/api/accounts', body)[0],401)
        self.assertEqual(self.request('/api/accounts', body, headers={'Cookie':admin['cookie']})[0],403)
        for roles in ([], ['agent'], ['admin','admin'], ['unknown'], [False]):
            self.assertEqual(self.request('/api/accounts', {**body,'roles':roles},admin)[0],400)
        self.assertEqual(self.request('/api/accounts', {**body,'password':'12345'},admin)[0],400)
        self.member(admin)
        self.assertEqual(self.request('/api/accounts', {**body,'username':'MEMBER'},admin)[0],409)
        member = self.login('member')
        self.assertEqual(self.request('/api/accounts', {**body,'username':'newuser'},member)[0],403)

    def test_member_creation_idempotency_and_secret_redaction(self):
        admin = self.bootstrap()
        body = {**self.registration('created'), 'roles':['operator']}
        headers = {'Idempotency-Key':'member-create-replay'}
        first = self.request('/api/accounts',body,admin,headers)
        replay = self.request('/api/accounts',body,admin,headers)
        self.assertEqual(first[0],200); self.assertEqual(first[2],replay[2])
        self.assertEqual(self.request('/api/accounts',{**body,'password':NEW_PASSWORD},admin,headers)[0],409)
        with self.app.accounts.store() as store:
            self.assertEqual(store.one('SELECT COUNT(*) AS n FROM accounts')['n'],2)
            rows = store.all('SELECT * FROM api_requests') + store.all('SELECT * FROM events')
            self.assertNotIn(PASSWORD,json.dumps(rows))
            fingerprint = store.one('SELECT fingerprint FROM api_requests WHERE request_key=?',('member-create-replay',))['fingerprint']
            raw = json.dumps(body,ensure_ascii=False,sort_keys=True)
            self.assertNotEqual(fingerprint,hashlib.sha256(('/api/accounts\n'+raw).encode()).hexdigest())

    def test_restart_never_resets_default_password_or_disabled_account(self):
        admin = self.bootstrap()
        self.member(admin,'second-admin',['admin'])
        second = self.login('second-admin')
        self.assertEqual(self.request('/auth/password',{'current_password':'123456','new_password':NEW_PASSWORD},admin)[0],200)
        restarted = Application(self.path,self.config)
        with self.assertRaises(BrowserFailure): restarted.accounts.authenticate({'username':'OpenDev','password':'123456'})
        restarted.accounts.authenticate({'username':'OpenDev','password':NEW_PASSWORD})
        row = next(row for row in self.request('/api/accounts',session=second)[2]['items'] if row['username']=='opendev')
        self.assertEqual(self.request('/api/accounts/'+row['id'],{'status':'disabled','roles':['admin'],'auth_version':row['auth_version']},second)[0],200)
        restarted = Application(self.path,self.config)
        with self.assertRaises(BrowserFailure): restarted.accounts.authenticate({'username':'OpenDev','password':NEW_PASSWORD})

    def test_existing_v5_installation_never_gains_a_default_admin(self):
        with self.app.accounts.store() as store:
            store.conn.execute("UPDATE accounts SET username='existing-owner',password_hash=?", (hash_password(PASSWORD),))
        restarted = Application(self.path,self.config)
        with self.assertRaises(BrowserFailure): restarted.accounts.authenticate({'username':'OpenDev','password':'123456'})
        restarted.accounts.authenticate({'username':'existing-owner','password':PASSWORD})

    def test_revoked_admin_cannot_create_members_with_stale_principal(self):
        admin = self.bootstrap()
        self.member(admin,'second-admin',['admin'])
        second = self.login('second-admin')
        principal = self.app.accounts.authenticate({'username':'OpenDev','password':'123456'})
        row = next(row for row in self.request('/api/accounts',session=second)[2]['items'] if row['username']=='opendev')
        self.request('/api/accounts/'+row['id'],{'status':'disabled','roles':['admin'],'auth_version':row['auth_version']},second)
        with self.app.accounts.store() as store, self.assertRaises(BrowserFailure):
            self.app.accounts.create(store, principal, {**self.registration('intruder'),'roles':['admin']})


if __name__ == '__main__':
    unittest.main()
