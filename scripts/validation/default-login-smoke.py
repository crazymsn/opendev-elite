"""Verify the explicitly configured initial account, without business mutations."""
import argparse
import http.cookiejar
import json
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--origin', default='http://127.0.0.1:8765')
parser.add_argument('--expected-version', default='0.8.0')
args = parser.parse_args()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

def request(path, body=None, csrf=None):
    headers = {'Origin':args.origin, 'Content-Type':'application/json'}
    if csrf:
        headers['X-CSRF-Token'] = csrf
    req = urllib.request.Request(args.origin + path, headers=headers, data=None if body is None else json.dumps(body).encode())
    with opener.open(req, timeout=15) as response:
        return json.load(response)

health = request('/healthz')
info = request('/openapi.json')['info']
options = request('/auth/options')
assert info['version'] == args.expected_version and health['ok']
assert not options['bootstrap_allowed'] and not options['registration_open']
session = request('/auth/password-login', {'username':'OpenDev','password':'123456'})
try:
    assert session['roles'] == ['admin'] and session['display_name'] == 'OpenDev'
    accounts = request('/api/accounts')
    assert any(row['username']=='opendev' and row['status']=='active' for row in accounts['items'])
finally:
    assert request('/auth/logout', {}, session['csrf_token'])['logged_out']
print(json.dumps({'version':info['version'], 'health':health, 'default_admin_login':True,
    'registration_closed':True, 'accounts_total':accounts['total'], 'test_session_logged_out':True,
    'business_mutations':False}, indent=2))
