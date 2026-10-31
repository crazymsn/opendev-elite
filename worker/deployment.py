"""Repeatable local setup and explicitly launched, bounded-log service processes."""
import argparse
import csv
import hashlib
import io
import json
import logging
import logging.handlers
import os
import re
import signal
import subprocess
import sys
import threading
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from .auth import IdentityStore, create_key
from .db import Store

IDENTITIES = (
    ('technical-reviewer', 'reviewer'), ('relationship-operator', 'operator'),
    ('funding-approver', 'approver'), ('credits-operator', 'disburser'),
    ('experience-agent', 'agent'), ('quality-auditor', 'auditor'),
)


def local_root(root=None):
    base = root if root is not None else Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/share'))) / 'github-sponsorship'
    path = Path(os.path.abspath(os.path.expanduser(base)))
    if str(path).startswith(('\\\\', '//')) or any(part.lower().startswith('onedrive') for part in path.parts):
        raise ValueError('Deployment state must be on a local disk outside OneDrive and UNC shares')
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or (hasattr(ancestor, 'is_junction') and ancestor.is_junction()):
            raise ValueError('Deployment state must not use symlinks or directory junctions')
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or Path.home().resolve().is_relative_to(resolved):
        raise ValueError('Use a dedicated deployment subdirectory, not a drive, home, or system ancestor')
    for name in ('OneDrive', 'OneDriveCommercial', 'OneDriveConsumer'):
        if os.environ.get(name) and resolved.is_relative_to(Path(os.environ[name]).resolve()):
            raise ValueError('Deployment state must be outside the configured cloud-sync directory')
    return resolved


def _private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != 'nt':
        path.chmod(0o700)
        return
    # Set an explicit protected DACL: current Windows user, SYSTEM, Administrators.
    # chmod(0600) alone does not protect Windows credential files.
    import ctypes
    from ctypes import wintypes
    result = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True,
                            check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    sid = next(csv.reader(io.StringIO(result.stdout.strip())))[1]
    if not re.fullmatch(r'S-1-[0-9-]+', sid):
        raise ValueError('Unable to identify the current Windows user for private storage')
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi.SetFileSecurityW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    descriptor = ctypes.c_void_p()
    dacl = f'D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)'
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(dacl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)


def _write_new(path, value):
    if path.exists():
        return False
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return True


def health_target(root):
    """Local probe address plus Host derived only from trusted on-disk config."""
    from .browser_auth import configured_origin
    root = local_root(root)
    config = json.loads((root / 'service.local.json').read_text(encoding='utf-8-sig'))
    origin = configured_origin(config.get('public_origin')) or 'http://127.0.0.1:8765'
    return {'url': origin, 'health_url': 'http://127.0.0.1:8765/healthz',
            'health_host': urlsplit(origin).netloc}


def setup_local(root=None, demo=False, public_origin=None):
    """Create missing local files only; never overwrite config, tokens or databases.

    Six separately scoped identity tokens live in independent credential files.
    A rerun validates existing token/config binding, rather than rotating keys.
    Synthetic demo data exists only when explicitly requested, in demo.db.
    """
    if type(demo) is not bool:
        raise ValueError('demo must be boolean')
    from .browser_auth import configured_origin
    origin = configured_origin(public_origin)
    root = local_root(root)
    if root.exists():
        allowed = {'credentials', 'logs', 'reports', 'backups', 'runtime', '.setup.lock',
                   'service.local.json', 'discovery.local.json', 'worker.db', 'worker.db-journal',
                   'demo.db', 'demo.db-journal', 'demo.SYNTHETIC.json', 'credits.local.json', 'agent.local.json', 'github.local.json'}
        if any(child.name not in allowed for child in root.iterdir()):
            raise ValueError('Deployment root contains unrelated files; use an empty dedicated directory')
    _private_directory(root)
    # Windows packaged-app filesystem redirection can expose a different final
    # path after creation. Persist that actual path so future PID checks agree.
    root = local_root(root)
    lock = root / '.setup.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        for name in ('credentials', 'logs', 'reports', 'backups', 'runtime'):
            target = root / name
            local_root(target)
            _private_directory(target)
        config_path = root / 'service.local.json'
        discovery_path = root / 'discovery.local.json'
        for target in (config_path, discovery_path, root / 'worker.db', root / 'demo.db'):
            local_root(target)
        discovery = json.loads(files('worker').joinpath('resources/discovery.json').read_text(encoding='utf-8'))
        _write_new(discovery_path, discovery)
        existing = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else None
        if existing is not None:
            IdentityStore(existing)
            if origin is not None and existing.get('public_origin') != origin:
                raise ValueError('Configured public_origin differs from OPENDEV_PUBLIC_ORIGIN; update the existing service.local.json explicitly')
        users, credentials = [], []
        for actor, role in IDENTITIES:
            secret = local_root(root / 'credentials' / (actor + '.token'))
            if existing is not None and not secret.is_file():
                raise ValueError('Existing service configuration has a missing credential file; recover or rotate explicitly')
            if secret.exists():
                token = secret.read_text(encoding='utf-8').strip()
                if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
                    raise ValueError('Existing credential file is invalid; preserve it and recover explicitly')
                identity = {'actor': actor, 'roles': [role], 'token_sha256': hashlib.sha256(token.encode()).hexdigest()}
                if existing is not None:
                    binding = next((u for u in existing['users'] if u['actor'] == actor), None)
                    if binding is None or binding.get('token_sha256') != identity['token_sha256']:
                        raise ValueError('Existing credential does not match service configuration; no files were replaced')
            else:
                identity = create_key(actor, [role], secret)['identity']
            users.append(identity)
            credentials.append({'actor': actor, 'role': role, 'secret_file': str(secret)})
        _write_new(config_path, {'users': users, 'allow_credit_writes': False, 'agent_lease_seconds': 900,
                                'public_origin': origin or 'http://127.0.0.1:8765', 'discovery': discovery})
        database = root / 'worker.db'
        if not database.exists():
            store = Store(database)
            store.close()
        demo_path = root / 'demo.db'
        if demo and not demo_path.exists():
            from .demo import run_demo
            store = Store(demo_path)
            try:
                run_demo(store, root / 'reports' / 'SYNTHETIC-DEMO')
            finally:
                store.close()
            _write_new(root / 'demo.SYNTHETIC.json', {'synthetic_only': True, 'database': 'demo.db',
                                                     'real_messages_sent': 0, 'real_credits_issued': 0})
        if demo and not (root / 'demo.SYNTHETIC.json').exists():
            raise ValueError('Existing demo.db is not marked synthetic; it was preserved and must be inspected')
        return {'root': str(root), 'config': str(config_path), 'discovery_config': str(discovery_path),
                'database': str(database), 'demo_database': str(demo_path) if demo else None,
                'synthetic_demo_requested': demo, 'credentials': credentials,
                **health_target(root),
                'commands': {'serve_argv': [sys.executable, '-m', 'worker.deployment', 'process', '--root', str(root), '--kind', 'serve', '--instance', 'manual-serve'] + (['--demo'] if demo else []),
                             'run_argv': [sys.executable, '-m', 'worker.deployment', 'process', '--root', str(root), '--kind', 'run', '--instance', 'manual-run'] + (['--demo'] if demo else [])},
                'external_configuration': {'github_token_present': bool(os.environ.get('GITHUB_TOKEN', '').strip()),
                                           'credits_configuration_installed': (root / 'credits.local.json').is_file() and not demo,
                                           'agent_configuration_installed': (root / 'agent.local.json').is_file(),
                                           'credit_writes_enabled': not demo and (existing or {}).get('allow_credit_writes') is True},
                'note': 'Credentials contain bearer tokens. Share each private file only with its assigned team member; no raw tokens are returned.'}
    finally:
        lock.unlink()


class _LogStream:
    def __init__(self, logger, level):
        self.logger, self.level = logger, level

    def write(self, value):
        # Bound individual messages as well as rotated files, even for huge writes.
        if value.strip():
            for offset in range(0, len(value), 16384):
                self.logger.log(self.level, value[offset:offset + 16384].rstrip())
        return len(value)

    def flush(self):
        pass


def process(root, kind, instance, demo=False, console=False):
    """Run one supervised role in this Python process; never spawn a shell."""
    if kind not in ('serve', 'run') or not re.fullmatch(r'[A-Za-z0-9-]{1,64}', instance):
        raise ValueError('Invalid process role or instance marker')
    root = local_root(root)
    config = json.loads((root / 'service.local.json').read_text(encoding='utf-8-sig'))
    config['demo_mode'] = demo
    credits_config = None
    if demo:
        config['allow_credit_writes'] = False
    else:
        credits_path = local_root(root / 'credits.local.json')
        if credits_path.is_file():
            credits_config = json.loads(credits_path.read_text(encoding='utf-8-sig'))
        if config.get('allow_credit_writes', False) and credits_config is None:
            raise ValueError('Explicit Credits write enablement requires credits.local.json')
    database = root / ('demo.db' if demo else 'worker.db')
    if not database.exists() or (demo and not (root / 'demo.SYNTHETIC.json').is_file()):
        raise ValueError('Run setup-local first; demo requires an explicitly marked synthetic database')
    handler = logging.handlers.RotatingFileHandler(root / 'logs' / (kind + '.log'), maxBytes=4 * 1024 * 1024,
                                                  backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    handlers = [handler, logging.StreamHandler(sys.stdout)] if console else [handler]
    logging.basicConfig(handlers=handlers, level=logging.INFO, force=True)
    logger = logging.getLogger('sponsorship.local')
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _LogStream(logger, logging.INFO), _LogStream(logger, logging.ERROR)
    stop = threading.Event()
    def stopping(*_):
        stop.set()
    try:
        logger.info('Starting role=%s synthetic=%s instance=%s', kind, demo, instance)
        if not demo:
            from .github_auth import configure_local_github
            github_auth = configure_local_github(root)
            logger.info('GitHub authentication source=%s configured=%s', github_auth['source'], github_auth['configured'])
        if kind == 'serve':
            from .service import serve
            serve(database, config, '127.0.0.1', 8765, credits_config)
        else:
            signal.signal(signal.SIGTERM, stopping)
            from .operations import run_loop, doctor
            if demo:
                # Never refresh synthetic GitHub names or contact external APIs.
                from .report import export_report
                while not stop.is_set():
                    store = Store(database)
                    try:
                        export_report(store, root / 'reports' / 'SYNTHETIC-DEMO' / 'live-report.md')
                        logger.info('Synthetic report cycle healthy=%s', doctor(store)['healthy'])
                    finally:
                        store.close()
                    stop.wait(300)
            else:
                discovery = json.loads((root / 'discovery.local.json').read_text(encoding='utf-8-sig'))
                interval = discovery.get('interval_seconds', 30)
                max_jobs = discovery.get('max_jobs', 40)
                if type(interval) not in (int, float) or not 1 <= interval <= 86400:
                    raise ValueError('discovery.local.json interval_seconds must be 1..86400')
                if type(max_jobs) is not int or not 1 <= max_jobs <= 1000:
                    raise ValueError('discovery.local.json max_jobs must be an integer 1..1000')
                logger.info('Collection loop interval_seconds=%s max_jobs=%s', interval, max_jobs)
                run_loop(database, discovery, interval_seconds=interval, max_jobs=max_jobs,
                         out_dir=root / 'reports', stop_event=stop)
    except KeyboardInterrupt:
        logger.info('Stopped by operator')
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        handler.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local deployment setup/process support')
    sub = parser.add_subparsers(dest='command', required=True)
    setup = sub.add_parser('setup'); setup.add_argument('--root'); setup.add_argument('--demo', action='store_true')
    worker = sub.add_parser('process'); worker.add_argument('--root', required=True); worker.add_argument('--kind', choices=['serve', 'run'], required=True)
    worker.add_argument('--instance', required=True); worker.add_argument('--demo', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'setup':
        print(json.dumps(setup_local(args.root, args.demo), ensure_ascii=False, indent=2))
    else:
        process(args.root, args.kind, args.instance, args.demo)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
