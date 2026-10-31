"""Explicit public-GitHub read check in a NEW private database; no workflow approvals."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from worker.db import Store
from worker.github import GitHub
from worker.pipeline import Pipeline,normalize_repo

parser=argparse.ArgumentParser()
parser.add_argument('--db',required=True)
parser.add_argument('--repo',action='append',required=True)
parser.add_argument('--use-gh-auth',action='store_true',help='Use existing GitHub CLI credentials in memory only; never print or persist the token')
args=parser.parse_args()
if not __import__('os').environ.get('GITHUB_TOKEN') and not args.use_gh_auth:
    print(json.dumps({'skipped': True, 'reason': 'GITHUB_TOKEN or --use-gh-auth is required for real API smoke'}, ensure_ascii=False))
    raise SystemExit(0)

if path.exists():raise SystemExit('Use a new validation database; existing state is never overwritten')
if not 1<=len(args.repo)<=5:raise SystemExit('Select one to five repositories')
repos=[normalize_repo(repo) for repo in args.repo]
token=''
if args.use_gh_auth:
    proc=subprocess.run(['gh','auth','token'],capture_output=True,timeout=30)
    if proc.returncode or not proc.stdout.strip():raise SystemExit('Existing GitHub CLI credentials are unavailable')
    token=proc.stdout.decode().strip()
s=Store(path)
try:
    for repo in repos:s.enqueue('refresh',{'repo':repo},'live-validation:'+repo)
    outcomes=Pipeline(s,GitHub(s,token=token)).work(len(repos))
    if len(outcomes)!=len(repos) or any(row['status']!='done' for row in outcomes):
        raise RuntimeError('Public collection did not complete; inspect the isolated validation database')
    evidence=[]
    for snapshot in s.all('SELECT s.*,p.full_name FROM snapshots s JOIN projects p ON p.id=s.project_id'):
        data=json.loads(snapshot['evidence']);samples=data['code_sampling']
        assert snapshot['complete']==1
        assert len(samples['files'])<=6
        for index,item in enumerate(samples['files']):
            assert data['code_sample_'+str(index)]['sha']==item['blob_sha']
            assert 'ref='+snapshot['sha'] in data['sources']['code_sample_'+str(index)]
        evidence.append({'repo':snapshot['full_name'],'sha':snapshot['sha'],'complete':True,'sampling':samples,'fetched_at':snapshot['fetched_at']})
    assert all(s.one('SELECT COUNT(*) AS n FROM '+table)['n']==0 for table in ('qualifications','reviews','experiences','contacts','interviews','decisions','grants'))
    print(json.dumps({'public_github_reads':True,'repository_code_executed':False,'business_assessments_written':False,'snapshots':evidence},ensure_ascii=False,indent=2))
finally:
    token='';s.close()
