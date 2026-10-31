"""Local password accounts with a seeded administrator and managed membership."""
from collections import deque
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid

from .auth import IdentityStore, Principal, ROLES
from .browser_auth import BrowserFailure
from .db import Store, dump, now

PASSWORD_ITERATIONS = 600_000
HUMAN_ROLES = ROLES - {'agent'}
_HASH_SLOTS = threading.BoundedSemaphore(2)
_DUMMY_HASH = 'pbkdf2_sha256$600000$' + '0' * 32 + '$' + '0' * 64


def username(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{2,63}', value):
        raise BrowserFailure(400, '用户名需为 3–64 位字母、数字或 _.@-，以字母或数字开头。')
    return value.lower()


def password_bytes(value, creating=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or len(value.encode('utf-8')) > 512:
        raise BrowserFailure(400, '密码长度须为 6–128 个字符。')
    if creating and (len(value) < 6 or value.isspace()):
        raise BrowserFailure(400, '密码至少 6 个字符，建议使用容易记住的长短语。')
    return value.encode('utf-8')


def derive(password, salt):
    if not _HASH_SLOTS.acquire(blocking=False):
        raise BrowserFailure(429, '登录服务繁忙，请稍后重试。')
    try:
        return hashlib.pbkdf2_hmac('sha256', password, salt, PASSWORD_ITERATIONS).hex()
    finally:
        _HASH_SLOTS.release()


def hash_password(value):
    raw = password_bytes(value, creating=True)
    salt = secrets.token_bytes(16)
    return f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${derive(raw, salt)}'


def verify_password(value, encoded):
    raw = password_bytes(value)
    if not isinstance(encoded, str) or not re.fullmatch(r'pbkdf2_sha256\$600000\$[a-f0-9]{32}\$[a-f0-9]{64}', encoded):
        return False
    _, _, salt, expected = encoded.split('$')
    return hmac.compare_digest(derive(raw, bytes.fromhex(salt)), expected)


def public_account(row):
    return {key: (json.loads(row[key]) if key == 'roles' else row[key]) for key in
            ('id', 'username', 'display_name', 'roles', 'status', 'auth_version', 'created_at', 'updated_at')}


def principal_for(row, project_ids=()):
    return Principal(row['id'], frozenset(json.loads(row['roles'])), row['auth_version'], row['display_name'], frozenset(project_ids))


class AuthThrottle:
    """Bounded process-local rate limits; never key on forwarded client headers."""
    def __init__(self):
        self.lock, self.rows = threading.Lock(), {}

    def check(self, peer, account=''):
        stamp = time.monotonic()
        keys = [('global', 120, 300), ('peer:' + peer, 60, 300)]
        if account:
            keys.append(('name:' + account[:64].lower(), 10, 300))
        with self.lock:
            self.rows = {key: deque(t for t in values if t > stamp - 300)
                         for key, values in self.rows.items() if values and values[-1] > stamp - 300}
            for key, limit, _ in keys:
                if len(self.rows.get(key, ())) >= limit:
                    raise BrowserFailure(429, '尝试次数过多，请在 5 分钟后重试。')
            if len(self.rows) + len(keys) > 4096:
                raise BrowserFailure(429, '登录服务繁忙，请稍后重试。')
            for key, _, _ in keys:
                self.rows.setdefault(key, deque()).append(stamp)


class Accounts:
    def __init__(self, db_path):
        self.db_path = db_path
        with self.store():
            pass

    @contextlib.contextmanager
    def store(self):
        store = Store(self.db_path)
        try:
            yield store
        finally:
            store.close()

    def initialized(self):
        with self.store() as s:
            return bool(s.one('SELECT initialized FROM account_state WHERE id=1')['initialized'])

    def ensure_default_admin(self, initial_password=None, allow_legacy_default=True):
        """Seed once atomically; external deployments must supply a strong password."""
        with self.store() as s, s.transaction():
            if s.one('SELECT initialized FROM account_state WHERE id=1')['initialized']:
                return
            if s.one('SELECT id FROM accounts LIMIT 1'):
                # Preserve existing installations, including their authorization state.
                s.conn.execute('UPDATE account_state SET initialized=1 WHERE id=1')
                return
            salt = secrets.token_bytes(16)
            password = initial_password or os.environ.get('OPENDEV_INITIAL_ADMIN_PASSWORD')
            if not isinstance(password, str) or len(password) < 6:
                if allow_legacy_default:
                    password = '123456'
                else:
                    raise BrowserFailure(503, 'Initial administrator password must be supplied through OPENDEV_INITIAL_ADMIN_PASSWORD')
            encoded = f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${derive(password.encode(), salt)}'
            account_id = 'account.' + uuid.uuid4().hex
            s.conn.execute('INSERT INTO accounts(id,username,display_name,password_hash,roles,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                           (account_id, 'opendev', 'OpenDev', encoded, dump(['admin']), 'active', now(), now()))
            s.conn.execute('UPDATE account_state SET initialized=1 WHERE id=1')
            s.event('account', account_id, 'default-administrator-initialized', 'deployment-owner',
                    {'username': 'opendev', 'roles': ['admin']})

    @staticmethod
    def require_current_admin(s, admin):
        if not admin.allows('admin'):
            raise BrowserFailure(403, '只有管理员可以管理成员。')
        if admin.auth_version is not None:
            current = s.one('SELECT * FROM accounts WHERE id=?', (admin.actor,))
            if not current or current['status'] != 'active' or 'admin' not in json.loads(current['roles']) or current['auth_version'] != admin.auth_version:
                raise BrowserFailure(403, '管理员权限已变更，请重新登录。')

    def create(self, s, admin, body):
        self.require_current_admin(s, admin)
        if not isinstance(body, dict) or set(body) != {'username', 'display_name', 'password', 'roles'}:
            raise BrowserFailure(400, '请提供用户名、显示名称、初始密码和成员角色。')
        name = username(body['username'])
        display = body['display_name']
        if not isinstance(display, str) or not 1 <= len(display.strip()) <= 80 or any(ord(c) < 32 for c in display):
            raise BrowserFailure(400, '显示名称需为 1–80 个可见字符。')
        roles = body['roles']
        if not isinstance(roles, list) or not roles or any(not isinstance(r, str) or r not in HUMAN_ROLES for r in roles) or len(set(roles)) != len(roles):
            raise BrowserFailure(400, '请至少选择一个有效的成员角色。')
        password_hash = hash_password(body['password'])
        with s.transaction():
            self.require_current_admin(s, admin)
            if s.one('SELECT id FROM accounts WHERE username=?', (name,)):
                raise BrowserFailure(409, '此用户名不可用，请选择其他用户名。')
            if s.one('SELECT COUNT(*) AS n FROM accounts')['n'] >= 10000:
                raise BrowserFailure(409, '工作空间账号数量已达上限，请联系管理员。')
            account_id = 'account.' + uuid.uuid4().hex
            status = 'active'
            s.conn.execute('INSERT INTO accounts(id,username,display_name,password_hash,roles,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                           (account_id, name, display.strip(), password_hash, dump(roles), status, now(), now()))
            s.event('account', account_id, 'member-created', admin.actor,
                    {'username': name, 'status': status, 'roles': roles})
            return public_account(s.one('SELECT * FROM accounts WHERE id=?', (account_id,)))

    def by_actor(self, actor):
        with self.store() as s:
            row = s.one("SELECT * FROM accounts WHERE id=? AND status='active'", (actor,))
        return principal_for(row) if row else None

    def authenticate(self, body):
        if not isinstance(body, dict) or set(body) != {'username', 'password'}:
            raise BrowserFailure(400, '请输入用户名和密码。')
        name = username(body['username'])
        with self.store() as s:
            row = s.one('SELECT * FROM accounts WHERE username=?', (name,))
            membership_ids = [x['project_id'] for x in s.all('''SELECT project_id FROM community_memberships
                WHERE maintainer_id IN (SELECT id FROM maintainers WHERE login=? COLLATE NOCASE)
                AND role='sub_admin' AND status='active' AND (expires_at IS NULL OR expires_at>?)''', (name, now()))]
        valid = verify_password(body['password'], row['password_hash'] if row else _DUMMY_HASH)
        if not row or not valid or row['status'] != 'active':
            raise BrowserFailure(401, '用户名或密码不正确，或账号尚未获得授权。')
        roles = frozenset(json.loads(row['roles']))
        if membership_ids:
            roles = roles | frozenset({'sub_admin'})
        return Principal(row['id'], roles, row['auth_version'], row['display_name'], frozenset(membership_ids))

    def listing(self, s, page=1, size=50):
        return {'items': [public_account(row) for row in s.all('SELECT * FROM accounts ORDER BY CASE status WHEN \'pending\' THEN 0 ELSE 1 END,created_at DESC,id LIMIT ? OFFSET ?', (size, (page - 1) * size))],
                'total': s.one('SELECT COUNT(*) AS n FROM accounts')['n'], 'page': page, 'page_size': size}

    def update(self, s, admin, account_id, body):
        if not admin.allows('admin'):
            raise BrowserFailure(403, '只有管理员可以授权成员。')
        if not isinstance(body, dict) or not set(body) <= {'status', 'roles', 'auth_version', 'password'} or not {'status','roles','auth_version'} <= set(body):
            raise BrowserFailure(400, '请提供账号状态、角色和当前版本。')
        status, roles, version = body['status'], body['roles'], body['auth_version']
        if status not in ('active', 'pending', 'disabled', 'rejected') or type(version) is not int:
            raise BrowserFailure(400, '账号状态或版本无效。')
        if not isinstance(roles, list) or any(not isinstance(r, str) or r not in HUMAN_ROLES for r in roles) or len(set(roles)) != len(roles):
            raise BrowserFailure(400, '请选择有效的成员角色；Agent 仍使用专用凭证。')
        if status == 'active' and not roles:
            raise BrowserFailure(400, '授权启用成员时至少选择一个角色。')
        new_hash = hash_password(body['password']) if 'password' in body else None
        with s.transaction():
            self.require_current_admin(s, admin)
            row = s.one('SELECT * FROM accounts WHERE id=?', (account_id,))
            if not row:
                raise BrowserFailure(404, '账号不存在。')
            if row['auth_version'] != version:
                raise BrowserFailure(409, '账号已被更新，请刷新后重试。')
            if row['status'] == 'active' and 'admin' in json.loads(row['roles']) and (status != 'active' or 'admin' not in roles):
                remaining = [r for r in s.all("SELECT id,roles FROM accounts WHERE status='active' AND id<>?", (account_id,)) if 'admin' in json.loads(r['roles'])]
                if not remaining:
                    raise BrowserFailure(409, '必须保留至少一位启用的管理员。')
            s.conn.execute('UPDATE accounts SET status=?,roles=?,password_hash=?,auth_version=auth_version+1,updated_at=? WHERE id=?',
                           (status, dump(roles), new_hash or row['password_hash'], now(), account_id))
            s.event('account', account_id, 'authorization-updated', admin.actor,
                    {'status': status, 'roles': roles, 'password_reset': new_hash is not None})
            return public_account(s.one('SELECT * FROM accounts WHERE id=?', (account_id,)))

    def change_password(self, principal, body):
        if not isinstance(body, dict) or set(body) != {'current_password', 'new_password'}:
            raise BrowserFailure(400, '请输入当前密码和新密码。')
        if principal.auth_version is None:
            raise BrowserFailure(409, '此登录方式使用 Token 或单点登录，没有本地密码。')
        with self.store() as s:
            row = s.one('SELECT * FROM accounts WHERE id=?', (principal.actor,))
            if not row or not verify_password(body['current_password'], row['password_hash']):
                raise BrowserFailure(403, '当前密码不正确。')
            new_hash = hash_password(body['new_password'])
            with s.transaction():
                changed = s.conn.execute("UPDATE accounts SET password_hash=?,auth_version=auth_version+1,updated_at=? WHERE id=? AND auth_version=? AND status='active'",
                                         (new_hash, now(), principal.actor, principal.auth_version)).rowcount
                if not changed:
                    raise BrowserFailure(409, '账号已变更，请重新登录。')
                s.event('account', principal.actor, 'password-changed', principal.actor, {})
        return {'logged_out': True}


class CombinedIdentities(IdentityStore):
    def __init__(self, config, accounts):
        super().__init__(config)
        self.accounts = accounts

    def by_actor(self, actor):
        return super().by_actor(actor) or self.accounts.by_actor(actor)


def reset_local_password(db_path, name, password):
    """Recovery for an operator already holding local database filesystem access."""
    from pathlib import Path
    if not Path(db_path).is_file():
        raise ValueError('Existing database required')
    normalized, encoded = username(name), hash_password(password)
    accounts = Accounts(db_path)
    with accounts.store() as store, store.transaction():
        row = store.one('SELECT id FROM accounts WHERE username=?', (normalized,))
        if not row:
            raise ValueError('Account not found')
        store.conn.execute('UPDATE accounts SET password_hash=?,auth_version=auth_version+1,updated_at=? WHERE id=?',
                           (encoded, now(), row['id']))
        store.event('account', row['id'], 'password-recovered-locally', 'deployment-owner', {})


if __name__ == '__main__':
    import argparse
    import getpass
    parser = argparse.ArgumentParser(description='OpenDev Elite local account recovery')
    parser.add_argument('command', choices=('reset-password',))
    parser.add_argument('--db', required=True)
    parser.add_argument('--username', required=True)
    args = parser.parse_args()
    password = getpass.getpass('New password (6+ characters): ')
    if password != getpass.getpass('Confirm password: '):
        raise SystemExit('Passwords do not match; nothing changed')
    reset_local_password(args.db, args.username, password)
    print('Password reset. Existing sessions invalidated; account status and roles preserved.')
