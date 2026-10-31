"""Inventory a real local deployment without initializing or mutating its DB."""
import argparse
import json
from pathlib import Path
import sqlite3

from worker import __version__
from worker.acceptance import audit_readiness
from worker.db import Store

parser=argparse.ArgumentParser()
parser.add_argument('--root',required=True)
args=parser.parse_args()
root=Path(args.root).resolve()
def config(name):
    path=root/name
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else None

# Do not invoke Store's initializer: an audit must not create tables/accounts.
store=Store.__new__(Store)
store.path=root/'worker.db'
store.conn=sqlite3.connect(store.path.as_uri()+'?mode=ro',uri=True,isolation_level=None)
store.conn.row_factory=sqlite3.Row
try:
    store.conn.execute('PRAGMA query_only=ON')
    store.conn.execute('BEGIN')
    report=audit_readiness(store,config('service.local.json'),config('credits.local.json'),config('agent.local.json'))
    assert store.conn.total_changes==0
    print(json.dumps({'installed_version':__version__,'read_only_connection':True,
        'github_environment_describes_audit_process':True,'report':report},ensure_ascii=False,indent=2))
finally:
    store.close()
