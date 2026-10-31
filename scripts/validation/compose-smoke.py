"""Exercise a real Compose deployment on an isolated project/volume (CI or local).

Requires Docker Engine and Compose v2.20+. No GitHub calls, demo records, messages,
or Credits writes. The worker is started in this bounded smoke.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18780)
    parser.add_argument('--no-build', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    # This exact unique project is the only volume the cleanup command can delete.
    project = 'opendev-smoke-' + uuid.uuid4().hex[:12]
    origin = f'http://127.0.0.1:{args.port}'
    env = dict(os.environ, OPENDEV_PORT=str(args.port), OPENDEV_PUBLIC_ORIGIN=origin,
               GITHUB_TOKEN='', NEXT_CREDITS_PAT='')
    compose = ['docker', 'compose', '--env-file', os.devnull, '-f', str(root / 'compose.yaml'), '-p', project]

    def run(*command, capture=False):
        return subprocess.run(compose + list(command), cwd=root, env=env, check=True,
                              stdout=subprocess.PIPE if capture else None, text=True).stdout

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, method='GET', value=None, headers=None):
        data = None if value is None else json.dumps(value).encode()
        request_headers = {'Content-Type': 'application/json', **(headers or {})}
        req = urllib.request.Request(origin + path, data=data, method=method, headers=request_headers)
        try:
            with opener.open(req, timeout=15) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    try:
        run('config', '--quiet')
        command = ['up', '-d', '--wait', '--wait-timeout', '180']
        # Exercise the default Compose build policy, without a --build shortcut.
        if args.no_build:
            command.append('--no-build')
        run(*command, 'api', 'gateway', 'worker')
        assert json.loads(request('/healthz')[2])['ok'] is True
        assert b'OpenDev Elite' in request('/')[2]
        assert request('/api/projects')[0] == 401
        assert request('/healthz', headers={'Host': 'untrusted.invalid'})[0] == 403
        assert json.loads(request('/auth/options')[2])['bootstrap_allowed'] is False
        account_password = 'synthetic compose password phrase 12345'
        status, admin_headers, admin_body = request('/auth/password-login', 'POST',
            {'username':'OpenDev','password':'123456'}, {'Origin':origin})
        assert status == 200
        admin_cookie = next(v for k,v in admin_headers.items() if k.lower()=='set-cookie').split(';',1)[0]
        admin = json.loads(admin_body)
        assert request('/auth/register', 'POST', {'username':'compose-member','display_name':'Synthetic Compose Member','password':account_password}, {'Origin':origin})[0] == 404
        assert request('/api/accounts', 'POST', {'username':'compose-member','display_name':'Synthetic Compose Member','password':account_password,'roles':['operator']},
            {'Cookie':admin_cookie,'Origin':origin,'X-CSRF-Token':admin['csrf_token'],'Idempotency-Key':'compose-create-member'})[0] == 200
        assert request('/auth/password-login', 'POST', {'username':'compose-member','password':account_password}, {'Origin':origin})[0] == 200
        assert request('/auth/password', 'POST', {'current_password':'123456','new_password':account_password},
            {'Cookie':admin_cookie,'Origin':origin,'X-CSRF-Token':admin['csrf_token']})[0] == 200
        token = run('exec', '-T', 'api', 'cat', '/var/lib/opendev-elite/credentials/relationship-operator.token', capture=True).strip()
        signature = hashlib.sha256(token.encode()).hexdigest()
        status, headers, _ = request('/auth/login', 'POST', {'token': token}, {'Origin': origin})
        assert status == 200, 'Login through gateway must succeed with the public Origin'
        cookie = next(v for k, v in headers.items() if k.lower() == 'set-cookie').split(';', 1)[0]
        assert request('/api/projects', headers={'Cookie': cookie})[0] == 200
        assert request('/auth/login', 'POST', {'token': token}, {'Origin': 'https://untrusted.invalid'})[0] == 403
        # Write a benign persisted operation record directly; no outbound API call.
        script = "from worker.db import Store; s=Store('/var/lib/opendev-elite/worker.db'); s.conn.execute(\"CREATE TABLE compose_smoke_marker (value TEXT)\"); s.conn.execute(\"INSERT INTO compose_smoke_marker VALUES ('persisted')\"); s.conn.commit(); s.close()"
        run('exec', '-T', 'api', 'python', '-I', '-c', script)
        run('run', '--rm', 'maintenance', 'backup')
        run('up', '-d', '--force-recreate', '--wait', '--wait-timeout', '180', 'api', 'gateway')
        token_again = run('exec', '-T', 'api', 'cat', '/var/lib/opendev-elite/credentials/relationship-operator.token', capture=True).strip()
        assert hashlib.sha256(token_again.encode()).hexdigest() == signature
        script = "from worker.db import Store; s=Store('/var/lib/opendev-elite/worker.db'); assert s.one('SELECT value FROM compose_smoke_marker')['value']=='persisted'; s.close()"
        run('exec', '-T', 'api', 'python', '-I', '-c', script)
        assert request('/api/projects', headers={'Cookie': cookie})[0] == 401
        assert request('/auth/login', 'POST', {'token': token_again}, {'Origin': origin})[0] == 200
        assert json.loads(request('/auth/options')[2])['setup_required'] is False
        assert request('/auth/password-login', 'POST', {'username':'OpenDev','password':'123456'}, {'Origin':origin})[0] == 401
        assert request('/auth/password-login', 'POST', {'username':'OpenDev','password':account_password}, {'Origin':origin})[0] == 200
        assert request('/auth/password-login', 'POST', {'username':'compose-member','password':account_password}, {'Origin':origin})[0] == 200
        assert request('/healthz')[0] == 200
        print('PASS: Compose build, readiness, default administrator, managed members, password/Token login, origin rejection, backup, password persistence, recreation and session invalidation')
    finally:
        run('down', '--volumes', '--remove-orphans')


if __name__ == '__main__':
    main()
