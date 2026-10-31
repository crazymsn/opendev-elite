"""Public GitHub profiles shared by the Pipeline and legacy CSV/TSV exports."""
import csv
import json
import os
import re
import tempfile
from pathlib import Path

USER_FIELDS = ['GitHub_ID', 'Name', 'Followers', 'Org', 'Bio', 'Location', 'Email',
               'Twitter', 'Website', 'Homepage_URL']


def fetch_profile(username, client):
    if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9-]+(?:\[bot\])?', username):
        raise ValueError('Invalid GitHub login')
    profile, _ = client.get('/users/' + username)
    if not isinstance(profile, dict) or not profile.get('id') or not profile.get('login'):
        from worker.github import ApiError
        raise ApiError(502, 'invalid public profile response', 30, True)
    return profile


def profile_row(profile):
    return dict(GitHub_ID=profile['login'], Name=profile.get('name') or '',
                Followers=profile.get('followers', ''), Org=profile.get('company') or '',
                Bio=profile.get('bio') or '', Location=profile.get('location') or '',
                Email=profile.get('email') or '', Twitter=profile.get('twitter_username') or '',
                Website=profile.get('blog') or '', Homepage_URL=profile.get('html_url') or '')


def get_user_info(username, gh_client):
    """Original name, now accepting the shared worker.github.GitHub client."""
    return profile_row(fetch_profile(username, gh_client))


def write_table(path, fields, rows, delimiter=','):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter=delimiter, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_library(store, path):
    rows = [profile_row(json.loads(p['profile'])) for p in store.all('SELECT profile FROM maintainers ORDER BY id')]
    write_table(path, USER_FIELDS, rows)
    return len(rows)


def update_library(library_path, token, batch_size=5, *, client=None):
    """Fill missing public values, preserving comma CSV or tab TSV delimiters.

    Missing Homepage_URL is supported. API errors propagate; files stay intact.
    """
    from worker.db import Store
    from worker.github import GitHub
    path = Path(library_path)
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        sample = stream.read(8192)
        stream.seek(0)
        header = sample.splitlines()[0] if sample else ''
        delimiter = '\t' if '\t' in header else ','
        reader = csv.DictReader(stream, delimiter=delimiter)
        fields, rows = list(reader.fieldnames or []), list(reader)
    if 'GitHub_ID' not in fields:
        raise ValueError('Author library must contain GitHub_ID')
    fields += [f for f in USER_FIELDS if f not in fields]
    store = Store(path.parent / 'worker.db') if client is None else None
    try:
        client = client or GitHub(store, token)
        profiles = {}
        for row in rows:
            username = (row.get('GitHub_ID') or '').strip()
            if not username:
                continue
            if username not in profiles:
                profiles[username] = get_user_info(username, client)
            for key, value in profiles[username].items():
                if row.get(key) in (None, '', 'NA', 'N/A', '无'):
                    row[key] = value
        write_table(path, fields, rows, delimiter)
        return len(profiles)
    finally:
        if store:
            store.close()


update_user_library = update_library
