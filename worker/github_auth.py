"""Opt-in local GitHub CLI credentials; secrets remain in process memory."""
import json
import os
from pathlib import Path
import shutil
import subprocess

MAX_TOKEN_BYTES = 16384


def _token(raw):
    try:
        value = raw.decode('ascii').strip() if isinstance(raw, bytes) else raw.strip()
    except (UnicodeError, AttributeError):
        raise ValueError('GitHub credential must be a single ASCII token') from None
    if not value or len(value) > MAX_TOKEN_BYTES or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError('GitHub credential must be a single ASCII token within the size limit')
    return value


def configure_local_github(root):
    """Explicit environment/file credentials take priority over CLI opt-in.

    Never call GitHub CLI unless github.local.json explicitly selects it. An
    unavailable selected source fails startup instead of silently going anonymous.
    No credential, subprocess output or credential digest is returned or written.
    """
    if os.environ.get('GITHUB_TOKEN'):
        os.environ['GITHUB_TOKEN'] = _token(os.environ['GITHUB_TOKEN'])
        return {'source': 'environment', 'configured': True}
    if os.environ.get('GITHUB_TOKEN_FILE'):
        try:
            with Path(os.environ['GITHUB_TOKEN_FILE']).open('rb') as stream:
                raw = stream.read(MAX_TOKEN_BYTES + 1)
        except OSError:
            raise ValueError('Configured GitHub credential file cannot be read') from None
        if len(raw) > MAX_TOKEN_BYTES:
            raise ValueError('GitHub credential file exceeds the size limit')
        os.environ['GITHUB_TOKEN'] = _token(raw)
        return {'source': 'secret_file', 'configured': True}
    config_path = Path(root) / 'github.local.json'
    if not config_path.exists():
        return {'source': 'anonymous', 'configured': False}
    try:
        with config_path.open('rb') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError()
        config = json.loads(raw.decode('utf-8-sig'))
        if config != {'credential_source': 'github-cli'}:
            raise ValueError()
    except (ValueError, OSError, UnicodeError):
        raise ValueError('github.local.json must select only credential_source: github-cli') from None
    executable = shutil.which('gh')
    if not executable:
        raise ValueError('GitHub CLI was selected but gh is unavailable; install it or configure GITHUB_TOKEN')
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        result = subprocess.run([executable, 'auth', 'token', '--hostname', 'github.com'],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False, **options)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('GitHub CLI credential lookup failed; check the local gh login') from None
    if result.returncode != 0:
        raise ValueError('GitHub CLI has no usable github.com login; run gh auth login locally')
    if len(result.stdout) > MAX_TOKEN_BYTES:
        raise ValueError('GitHub CLI credential exceeds the size limit')
    os.environ['GITHUB_TOKEN'] = _token(result.stdout)
    return {'source': 'github-cli', 'configured': True}
