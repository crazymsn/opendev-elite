"""Offline, synthetic-only directory and inbox browser fixture."""
import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from worker.db import Store,dump,now
from worker.demo import seed
from worker.service import Application,Server

parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8882);args=parser.parse_args()
root=Path(tempfile.mkdtemp(prefix='opendev-developers-browser-'));db=root/'synthetic.db'
s=Store(db)
for pid in range(501,520):
    seed(s,project_id=pid,maintainer_id=pid+100)
    s.conn.execute('UPDATE maintainers SET login=?,kind=?,profile=? WHERE id=?',
        ('synthetic-author-'+str(pid),'Organization' if pid%3==0 else 'User',dump({'name':'Fixture '+str(pid),'bio':'SYNTHETIC public profile <script>never execute</script>','company':'Fixture organization','email':'private-fixture@example.invalid','blog':'javascript:alert(1)'}),pid+100))
seed(s,project_id=601,maintainer_id=601)
s.conn.execute('UPDATE project_maintainers SET confirmed=0 WHERE project_id=601')
s.conn.execute('UPDATE maintainers SET do_not_contact=1 WHERE id=601')
s.conn.execute("UPDATE projects SET owner=NULL,audience='unknown' WHERE id=502")
s.conn.execute('DELETE FROM qualifications WHERE project_id=503')
s.conn.execute('UPDATE snapshots SET complete=0 WHERE project_id=504')
s.conn.execute("INSERT INTO contacts VALUES('fixture-contact',501,601,'email','SYNTHETIC private draft','replied','operator','fixture',?)",(now(),))
notes={k:'SYNTHETIC '+k+' evidence' for k in ('users','commitment','roadmap','credit_need','milestone')};notes['consent']=True
s.conn.execute("INSERT INTO interviews VALUES(1,501,'fixture-contact','operator',?,'[\"engineer\"]',?)",(dump(notes),now()))
s.conn.execute("INSERT INTO decisions VALUES('fixture-decision',501,1,'approve','approver','SYNTHETIC','{}',?)",(now(),))
s.conn.execute("INSERT INTO grants VALUES('fixture-grant','fixture-decision','issued',NULL,?)",(now(),))
s.conn.execute("INSERT INTO followups VALUES('fixture-followup','fixture-grant','2099-01-01','pending',NULL)")
s.close()
roles=('reviewer','operator','approver','disburser','auditor')
config={'demo_mode':True,'public_origin':f'http://127.0.0.1:{args.port}',
        'users':[{'actor':role,'roles':[role],'token_sha256':hashlib.sha256(('synthetic-directory-'+role+'-fixture-only').encode()).hexdigest()} for role in roles]}
app=Application(db,config);server=Server(('127.0.0.1',args.port),app)
print(json.dumps({'db':str(db),'port':args.port,'synthetic_only':True}),flush=True)
try:server.serve_forever()
finally:server.server_close()
