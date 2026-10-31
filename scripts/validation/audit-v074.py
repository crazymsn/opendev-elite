"""Read-only v0.7.4 installation/data audit; context contains local paths only.

Run with --context pointing to the private release context, outside source
archives. Does not read token contents or create workflow records.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3
import urllib.request
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--context', required=True)
parser.add_argument('--live', action='store_true')
args = parser.parse_args()
source = Path(__file__).resolve().parents[2]
context = json.loads(Path(args.context).read_text(encoding='utf-8-sig'))
runtime = Path(context['new_runtime']).parents[1] / 'Lib' / 'site-packages'
with zipfile.ZipFile(source / 'artifacts/dist/v0.7.4/github_sponsorship_worker-0.7.4-py3-none-any.whl') as wheel:
    files = [name for name in wheel.namelist() if name.startswith(('worker/', 'src/py/'))
             and Path(name).suffix in ('.py', '.html', '.css', '.js', '.json')]
    for name in files:
        assert (source/name).read_bytes() == wheel.read(name) == (runtime/name).read_bytes(), name
result = {'version':'0.7.4', 'runtime_files_matching_source_wheel_install':len(files)}
if args.live:
    def connect(path):
        return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)
    with connect(context['database']) as current, connect(Path(context['backup'])/'worker.db') as before:
        current.execute('BEGIN')
        original = {row[0] for row in before.execute('SELECT id FROM projects')}
        assert original <= {row[0] for row in current.execute('SELECT id FROM projects')}
        assert before.execute('SELECT * FROM accounts ORDER BY id').fetchall() == current.execute('SELECT * FROM accounts ORDER BY id').fetchall()
        assert current.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not current.execute('PRAGMA foreign_key_check').fetchall()
        business = ('qualifications','reviews','experiences','contacts','interviews','decisions','grants','followups')
        counts = {table:current.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                  for table in ('projects','maintainers','snapshots','accounts')+business}
        for table in business:
            assert counts[table] == before.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
        def public(value):
            return (value.get('private') is False or value.get('visibility') == 'public') and value.get('private',False) is False and value.get('visibility','public') == 'public'
        assert all(public(json.loads(row[0])) for row in current.execute('SELECT metadata FROM projects'))
        assert all(public(json.loads(row[0])['repository']) for row in current.execute('SELECT evidence FROM snapshots'))
        fresh = current.execute('SELECT COUNT(*) FROM snapshots WHERE fetched_at>?', (context['final_started_at'],)).fetchone()[0]
        assert fresh > 0, 'Wait for at least one actual refresh after final deployment'
        headers = [json.loads(row[0]) for row in current.execute('SELECT headers FROM http_cache')]
        quota = max((h for h in headers if str(h.get('x-ratelimit-limit')) == '5000'), key=lambda h:h.get('date',''))
        result.update(schema=current.execute('SELECT MAX(version) FROM schema_version').fetchone()[0],
                      backup_name=Path(context['backup']).name, original_projects=len(original),
                      original_projects_preserved=True, account_rows_unchanged=True,
                      business_assessments_unchanged=True, database_integrity='ok', foreign_keys='ok',
                      all_stored_project_and_snapshot_metadata_public=True, counts_after_start=counts,
                      snapshots_in_backup=before.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0],
                      snapshots_after_final_restart=fresh,
                      authenticated_response={k:quota[k] for k in ('date','x-ratelimit-limit','x-ratelimit-remaining')})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for path in ('/healthz','/openapi.json'):
        with opener.open('http://127.0.0.1:8765'+path,timeout=10) as response:
            value=json.load(response)
            if path=='/healthz':assert value['ok'];result['health']=value
            else:assert value['info']['version']=='0.7.4'
    result['checked_at']=dt.datetime.now(dt.timezone.utc).isoformat()
print(json.dumps(result,ensure_ascii=False,indent=2))
