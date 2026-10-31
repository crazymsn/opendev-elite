"""Run with installed Python -I from outside the source directory."""
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import worker
from worker.db import Store
from worker.demo import seed, review_one
from worker.workflow import Workflow
from worker.service import Application, Server


def main():
    token = 'synthetic-wheel-verification-only-000000000000'
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / 'smoke.db'
        store = Store(database)
        seed(store)
        workflow = Workflow(store)
        review_one(workflow)
        workflow.apply('experience-create', {'project_id': 101, 'audience': 'human', 'spec': {
            'scenario': 'fixture', 'success_criteria': 'fixture', 'environment': 'offline',
            'input': 'fixture', 'budget': {'seconds': 30}}}, 'synthetic-reviewer')
        app = Application(database, {'users': [{'actor': 'synthetic-reviewer', 'roles': ['reviewer'],
            'token_sha256': hashlib.sha256(token.encode()).hexdigest()}]})
        server = Server(('127.0.0.1', 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        paths = ['/', '/assets/app.js', '/assets/forms.js', '/assets/accounts.js', '/assets/app.css',
                 '/assets/landing.js', '/assets/landing.css', '/assets/i18n.js', '/assets/i18n.en.js',
                 '/assets/views.js', '/assets/developers.js', '/assets/workspace.css', '/assets/elite.css', '/auth/options', '/openapi.json']
        verified = []
        try:
            for path in paths + ['/api/inbox','/api/developers','/api/developers/201','/api/inbox?kind=pending_selection','/api/projects/101']:
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                connection.request('GET', path, headers={'Authorization': 'Bearer ' + token} if path.startswith('/api/') else {})
                response = connection.getresponse()
                body = response.read()
                assert response.status == 200, (path, response.status)
                if path == '/api/inbox':
                    assert json.loads(body)['total'] == 1
                if path == '/api/projects/101':
                    evidence = json.loads(body)['technical_evidence']
                    assert evidence['available'] and evidence['synthetic'] and not evidence['files']
                if path == '/':
                    assert 'id="account-login-form"' in body.decode() and 'landing-hero' not in body.decode()
                if path == '/openapi.json':
                    assert json.loads(body)['info']['version'] == worker.__version__ == '0.8.0'
                verified.append(path)
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            store.close()
        print(json.dumps({'version': worker.__version__, 'loaded_from': worker.__file__,
                          'isolated_import': True, 'paths_verified': verified, 'synthetic_only': True}, indent=2))


if __name__ == '__main__':
    main()
