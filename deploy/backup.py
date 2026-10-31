"""Create a new SQLite online backup. Does not overwrite or delete old backups."""
import argparse
import datetime as dt
import json
import uuid
from worker.db import Store
from worker.deployment import local_root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='/var/lib/github-sponsorship')
    args = parser.parse_args()
    root = local_root(args.root)
    database = root / 'worker.db'
    if not database.is_file():
        raise ValueError('Existing production database required')
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = root / 'backups' / ('worker-' + stamp + '-' + uuid.uuid4().hex[:8] + '.db')
    store = Store(database)
    try:
        print(json.dumps({'backup': store.backup(target), 'credentials_included': False}))
    finally:
        store.close()


if __name__ == '__main__':
    main()
