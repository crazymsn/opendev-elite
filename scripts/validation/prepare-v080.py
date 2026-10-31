"""Back up the existing local deployment for the v0.8.0 upgrade; no secrets printed."""
import datetime as dt
import json
from pathlib import Path
import shutil
import sqlite3

source=Path(__file__).resolve().parents[2]
previous=json.loads((source/'artifacts/validation/release-v074-context.json').read_text(encoding='utf-8-sig'))
root=Path(previous['root']).resolve()
assert root.name=='github-sponsorship-production'
backup=root/'backups'/('before-v080-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir(parents=True,exist_ok=False)
database=root/'worker.db'
with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as original,sqlite3.connect(backup/'worker.db') as copy:
    original.backup(copy)
    assert copy.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    counts={t:copy.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('projects','snapshots','accounts','qualifications','reviews','experiences','contacts','interviews','decisions','grants','followups')}
    running=[dict(zip(('id','attempts','lease_until'),r)) for r in copy.execute("SELECT id,attempts,lease_until FROM jobs WHERE state='running'")]
for config in root.glob('*.local.json'):shutil.copy2(config,backup/config.name)
if (root/'credentials').is_dir():shutil.copytree(root/'credentials',backup/'credentials')
context={'root':str(root),'database':str(database),'backup':str(backup),
         'old_runtime':previous['new_runtime'],'new_runtime':str(root/'runtime/venv-v080/Scripts/python.exe'),
         'counts_before':counts,'running_jobs_at_backup':running,'final_started_at':None}
(source/'artifacts/validation/release-v080-context.json').write_text(json.dumps(context,indent=2),encoding='utf-8')
print(json.dumps({'backup':backup.name,'counts_before':counts,'running_job_count':len(running)},indent=2))
