"""Isolated v0.8 quality/login UI fixtures. Never use a production database."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from worker.db import Store,dump
from worker.demo import seed,selection_evidence
from worker.service import Application,Server

root=Path(tempfile.mkdtemp(prefix='opendev-quality-fixture-'));db=root/'synthetic.db'
s=Store(db)
for pid in (801,802,803):seed(s,project_id=pid,maintainer_id=pid+100)
evidence=selection_evidence()
for value in evidence['quality'].values():value['score']=3
s.conn.execute('UPDATE qualifications SET evidence=? WHERE project_id=802',(dump(evidence),))
s.conn.execute("UPDATE qualifications SET policy_version='sop-2026-09-v2' WHERE project_id=803")
s.close()
config={'demo_mode':True,'public_origin':'http://127.0.0.1:8886',
        'users':[{'actor':'synthetic-quality-reviewer','roles':['reviewer'],
                  'token_sha256':hashlib.sha256(b'synthetic-quality-reviewer-token-2026').hexdigest()}]}
server=Server(('127.0.0.1',8886),Application(db,config))
print(json.dumps({'port':8886,'db':str(db),'synthetic_only':True}),flush=True)
try:server.serve_forever()
finally:server.server_close()
