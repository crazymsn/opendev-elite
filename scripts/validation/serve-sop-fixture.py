"""Dedicated offline browser fixture. Uses real workflow gates and an in-memory Credits transport."""
import argparse
import datetime as dt
import hashlib
import json
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker import service
from worker.credits import NextCredits
from worker.db import Store, dump, now
from worker.demo import seed


class OfflineCredits(NextCredits):
    invitations = []
    participants = []
    reads = {}
    lock = threading.Lock()

    def request(self, method, path, body=None):
        # No transport call is permitted from this fixture.
        with self.lock:
            if path == '/openapi/v1.json':
                return {'paths': {'/api/v1/events/{slug}/invitations': {'get': {}, 'post': {}},
                                  '/api/v1/events/{slug}/participants': {'get': {}}}}
            if method == 'POST' and path.endswith('/invitations'):
                row = dict(body, id='offline-invitation-' + str(len(self.invitations) + 1), participant=None, consumedAt=None)
                self.invitations.append(row)
                return dict(row)
            email = urllib.parse.parse_qs(urllib.parse.urlparse(path).query).get('q', [''])[0]
            if method == 'GET' and '/invitations?' in path:
                rows = [r for r in self.invitations if r['email'] == email]
                for row in rows:
                    self.reads[row['id']] = self.reads.get(row['id'], 0) + 1
                    # Simulate the author's claim only after an initial pending reconciliation.
                    if self.reads[row['id']] == 2:
                        participant = {'id': 'participant-' + row['id'], 'activatedAt': now()}
                        row.update(consumedAt=now(), participant=participant)
                        self.participants.append(dict(participant, invitationId=row['id'], bannedAt=None,
                            grantCents=row['grantCents'], settledCents=125, user={'email': email}))
            elif method == 'GET' and '/participants?' in path:
                rows = [r for r in self.participants if r['user']['email'] == email]
            else:
                raise RuntimeError('Unexpected offline fixture request: ' + method + ' ' + path)
            return {'items': rows, 'total': len(rows), 'page': 1, 'pageSize': 100}


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8879)
    args=parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='opendev-sop-browser-'))
    db = root / 'synthetic.db'
    expiry = (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=90)).isoformat()
    config = {'demo_mode': True, 'public_origin': f'http://127.0.0.1:{args.port}', 'allow_credit_writes': True,
        'users': [{'actor': 'offline-disburser', 'roles': ['disburser'],
            'token_sha256': hashlib.sha256(b'offline-sop-disburser-fixture-only').hexdigest()}]}
    store = Store(db)
    try:
        for pid in (401, 402):
            seed(store, project_id=pid, audience='human', maintainer_id=pid+100)
        store.conn.execute('DELETE FROM qualifications')
        store.conn.execute("UPDATE projects SET owner=NULL,audience='unknown',stage='discovered'")
        store.conn.execute('UPDATE project_maintainers SET confirmed=0')
    finally:
        store.close()
    service.NextCredits = OfflineCredits
    server = service.make_server(db, config, port=args.port, credits_config={'base_url':'https://example.invalid',
        'event_slug':'offline-browser', 'event_end_date':expiry, 'event_config_evidence':'SYNTHETIC fixture'})
    print(json.dumps({'database':str(db),'port':args.port,'expiry':expiry,'synthetic_only':True,'real_credits_requests':0}),flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
