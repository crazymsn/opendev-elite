"""OpenDev Elite container entrypoint; application code stays unprivileged."""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import threading

from .browser_auth import configured_origin
from .deployment import local_root, process, setup_local


def load_secret_files():
    """Support optional Compose secrets without printing or persisting values."""
    for name in ('GITHUB_TOKEN', 'NEXT_CREDITS_PAT'):
        filename = os.environ.get(name + '_FILE')
        if not filename:
            continue
        if os.environ.get(name):
            raise ValueError(name + ' and ' + name + '_FILE cannot both be set')
        with Path(filename).open('rb') as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError(name + ' secret file exceeds the size limit')
        value = raw.decode('utf-8').strip()
        if not value or any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise ValueError(name + ' secret must be a single nonempty ASCII token')
        os.environ[name] = value


def import_config(root, name, raw, origin):
    """Explicit operator replacement of one configuration, using an atomic write."""
    from .auth import IdentityStore
    if name not in ('service', 'discovery', 'credits'):
        raise ValueError('Unsupported configuration')
    if len(raw) > 1024 * 1024:
        raise ValueError('Configuration exceeds 1 MiB')
    config = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(config, dict):
        raise ValueError('Configuration must be a JSON object')
    if name == 'service':
        IdentityStore(config)
        if configured_origin(config.get('public_origin')) != origin:
            raise ValueError('public_origin must match OPENDEV_PUBLIC_ORIGIN')
    if name == 'discovery':
        from .operations import _validate_discovery
        _validate_discovery(config)
    target = local_root(root / (name + '.local.json'))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root / 'runtime',
                                         prefix='config-', suffix='.json', delete=False) as output:
            temporary = Path(output.name)
            json.dump(config, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {'configuration': str(target), 'restart_required': name in ('service', 'credits', 'discovery')}


def main(argv=None):
    parser = argparse.ArgumentParser(description='OpenDev Elite Docker deployment')
    parser.add_argument('command', choices=('init', 'api', 'worker', 'combined', 'health', 'health-api', 'backup', 'configure'))
    parser.add_argument('--root', default=os.environ.get('OPENDEV_DATA_ROOT', '/var/lib/opendev-elite'))
    parser.add_argument('--name', choices=('service', 'discovery', 'credits'))
    args = parser.parse_args(argv)
    root = local_root(args.root)
    origin = configured_origin(os.environ.get('OPENDEV_PUBLIC_ORIGIN', 'http://127.0.0.1:8780'))
    if args.command == 'init':
        result = setup_local(root, public_origin=origin)
        print(json.dumps({'application': 'OpenDev Elite', 'root': result['root'],
                          'url': result['url'], 'credentials': result['credentials']}, ensure_ascii=False))
        return 0
    if not (root / 'service.local.json').is_file() or not (root / 'worker.db').is_file():
        raise ValueError('Initialize the data volume first: docker compose run --rm init')
    if args.command == 'configure':
        if not args.name:
            parser.error('configure requires --name')
        print(json.dumps(import_config(root, args.name, sys.stdin.buffer.read(1024 * 1024 + 1), origin)))
        return 0
    if args.command in ('api', 'worker', 'combined'):
        config = json.loads((root / 'service.local.json').read_text(encoding='utf-8-sig'))
        if config.get('public_origin') != origin:
            raise ValueError('service.local.json public_origin must match OPENDEV_PUBLIC_ORIGIN')
        load_secret_files()
        if args.command == 'combined':
            # OpenDeploy provisions one web container. Keep the API responsive
            # while the bounded collection loop claims queued discovery jobs.
            from .service import Application, serve
            from .operations import run_loop
            database = root / 'worker.db'
            credits_config = None
            credits_path = root / 'credits.local.json'
            if credits_path.is_file():
                credits_config = json.loads(credits_path.read_text(encoding='utf-8-sig'))
            # Validate account/config initialization synchronously. If this fails,
            # the container must exit before the platform probes port 8080.
            Application(database, config, credits_config)
            discovery = json.loads((root / 'discovery.local.json').read_text(encoding='utf-8-sig'))
            interval = discovery.get('interval_seconds', 30)
            max_jobs = discovery.get('max_jobs', 40)
            api = threading.Thread(target=serve, args=(database, config, '0.0.0.0', int(os.environ.get('PORT', '8080')), credits_config),
                                    name='opendev-api', daemon=True)
            api.start()
            run_loop(database, discovery, interval_seconds=interval, max_jobs=max_jobs,
                     out_dir=root / 'reports')
        else:
            process(root, 'serve' if args.command == 'api' else 'run', 'docker-compose', console=True)
        return 0
    # Reuse the same health and SQLite online backup implementation as systemd.
    old_argv = sys.argv
    script = Path('/opt/opendev/deploy') / ('backup.py' if args.command == 'backup' else 'healthcheck.py')
    sys.argv = [str(script), '--root', str(root)]
    if args.command != 'backup':
        sys.argv.append('--readiness-only')
    if args.command == 'health-api':
        sys.argv.append('--api')
    try:
        entry = runpy.run_path(str(script))
        return entry['main']()
    finally:
        sys.argv = old_argv


if __name__ == '__main__':
    raise SystemExit(main())
