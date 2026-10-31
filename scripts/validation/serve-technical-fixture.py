"""Read-only technical-evidence UI fixture; no third-party repository code."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from worker.db import Store,dump,now
from worker.demo import seed,SHA
from worker.service import Application,Server

parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8884);args=parser.parse_args()
root=Path(tempfile.mkdtemp(prefix='opendev-technical-fixture-'));db=root/'synthetic.db';s=Store(db)
seed(s,project_id=711,maintainer_id=811)
e={'synthetic':True,'repository':{'full_name':'synthetic-evidence/sample','fork':True,'license':{'spdx_id':'MIT'},'parent':{'html_url':'https://github.com/synthetic-evidence/upstream'}},
   'code_sampling':{'strategy':'repository-evidence-v2','files':[]},'tree':{'truncated':True},
   'errors':[{'component':'owner_profile','status':404}],
   'commits_90d_sample':[{'sha':'b'*40,'commit':{'message':'SYNTHETIC fix <img src=x onerror="window.evidenceXss=1">','author':{'name':'Synthetic author'},'committer':{'date':'2026-09-01T00:00:00Z'}}}],
   'releases_sample':[{'name':'Synthetic preview','published_at':'2026-09-02T00:00:00Z','prerelease':True,'body':'SYNTHETIC notes <script>window.evidenceXss=1</script>','html_url':'javascript:alert(1)'}],
   'issues_pr_sample':[{'number':1,'title':'SYNTHETIC issue','state':'open','updated_at':'2026-09-03T00:00:00Z','html_url':'https://github.com/synthetic-evidence/sample/issues/1'}],
   'sampling':{'commits_90d_sample':{'has_more':True}},'sources':{'commits_90d_sample':'https://api.github.com/repos/synthetic-evidence/sample/commits?sha='+SHA}}
paths=[('src/server.jl','implementation'),('src/dispatch.py','implementation'),('src/call.py','implementation'),('test/test_server.jl','test'),('Project.toml','dependency_manifest'),('.github/workflows/ci.yml','ci_configuration')]
for index,(path,role) in enumerate(paths):
    content=('SYNTHETIC passive code <script>window.evidenceXss=1</script>\n'+'long_line_'*25+'\n')*400 if index==0 else '# SYNTHETIC sample only\n'
    raw=content.encode();raw=raw[:79000];digest=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    e['code_sampling']['files'].append({'path':path,'role':role,'blob_sha':digest,'size':len(raw)})
    e['code_sample_'+str(index)]={'encoding':'base64','content':base64.b64encode(raw).decode(),'sha':digest}
s.conn.execute('UPDATE snapshots SET evidence=?,complete=0 WHERE project_id=711',(dump(e),))
seed(s,project_id=712,maintainer_id=812);s.conn.execute('UPDATE projects SET head_sha=? WHERE id=712',('c'*40,))
s.conn.execute('UPDATE snapshots SET evidence=? WHERE project_id=712',(dump({'synthetic':True,'code_sample_paths':['legacy.py'],'code_sample_0':{'encoding':'base64','content':'bad%%%'}}),))
s.upsert_project({'id':713,'full_name':'synthetic-evidence/not-collected','html_url':'https://example.invalid/713'},'synthetic')
s.close()
config={'demo_mode':True,'public_origin':f'http://127.0.0.1:{args.port}','users':[{'actor':'reviewer','roles':['reviewer'],'token_sha256':hashlib.sha256(b'synthetic-technical-reader-only').hexdigest()}]}
server=Server(('127.0.0.1',args.port),Application(db,config));print(json.dumps({'port':args.port,'db':str(db),'synthetic_only':True}),flush=True)
try:server.serve_forever()
finally:server.server_close()
