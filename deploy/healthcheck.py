"""Offline DB readiness plus an optional loopback-only HTTP liveness check."""
import argparse
import json
import urllib.request
from worker.db import Store
from worker.deployment import health_target, local_root
from worker.operations import doctor


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='/var/lib/github-sponsorship')
    parser.add_argument('--api', action='store_true', help='Also check the local 127.0.0.1:8765 service')
    parser.add_argument('--readiness-only', action='store_true', help='Use DB integrity/schema readiness; queue alerts must not block the UI needed to repair them')
    args = parser.parse_args()
    root = local_root(args.root)
    if not (root / 'worker.db').is_file():
        raise ValueError('Existing production database required')
    store = Store(root / 'worker.db')
    try:
        result = doctor(store)
    finally:
        store.close()
    if args.api:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            target = health_target(root)
            request = urllib.request.Request(target['health_url'], headers={'Host': target['health_host']})
            with opener.open(request, timeout=5) as response:
                result['api_live'] = json.loads(response.read(4096)).get('ok') is True
        except (OSError, ValueError):
            result['api_live'] = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    ready = result['healthy']
    if args.readiness_only:
        ready = all(result['database'][key] for key in ('integrity_ok', 'foreign_keys_ok', 'schema_supported'))
    return 0 if ready and result.get('api_live', True) else 1


if __name__ == '__main__':
    raise SystemExit(main())
