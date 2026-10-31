"""Package a reviewed source tree; never include local state or credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib
import zipfile
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test-log', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    from worker.db import SCHEMA_VERSION
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    log = Path(args.test_log).resolve()
    evidence = log.read_text(encoding='utf-8-sig')
    count = re.search(r'Ran (\d+) tests', evidence)
    if not count or not re.search(r'\nOK\s*$', evidence):
        raise ValueError('A complete passing unittest log without skips is required')
    dist = root / 'artifacts' / 'dist'
    wheel = dist / f'github_sponsorship_worker-{version}-py3-none-any.whl'
    if not wheel.is_file():
        raise ValueError('Build the matching wheel with pip wheel first')
    selected = [root / name for name in ('main.py', 'pyproject.toml', 'requirements.txt', 'README.md', 'Makefile', '.gitignore',
                                        'Dockerfile', 'compose.yaml', '.dockerignore', '.env.example')]
    for directory in ('worker', 'src', 'tests', 'docs', 'deploy', 'scripts', '.github', 'examples', 'design-system'):
        selected.extend(p for p in (root / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts
                        and p.suffix not in ('.pyc', '.db', '.token', '.key') and '.local.' not in p.name)
    selected.append(root / 'config/discovery.json')
    selected.extend((root / 'config').glob('*.example.json'))
    archive = dist / f'github-sponsorship-v{version}-source.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(set(selected)):
            package.write(path, Path('GitHub_Worker') / path.relative_to(root))
    with zipfile.ZipFile(archive) as package:
        for path in selected:
            if package.read((Path('GitHub_Worker') / path.relative_to(root)).as_posix()) != path.read_bytes():
                raise ValueError('Source changed during packaging: ' + str(path))
    manifest = {'version': version, 'schema_version': SCHEMA_VERSION, 'tests_passed': int(count[1]),
                'test_log_sha256': hashlib.sha256(log.read_bytes()).hexdigest(), 'artifacts': []}
    for path in (wheel, archive):
        manifest['artifacts'].append({'file': path.name, 'bytes': path.stat().st_size,
                                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (dist / 'release-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
