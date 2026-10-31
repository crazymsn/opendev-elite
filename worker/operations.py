"""Explicitly started local collection cycles and offline operational diagnostics.

Nothing in this module registers a scheduler, sends notifications, starts Agent
containers, or writes to the Credits service. A cycle only schedules/collects
GitHub evidence and writes operator-requested local reports.
"""
import datetime as dt
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from .db import SCHEMA_VERSION, Store, now
from .github import GitHub
from .pipeline import Pipeline, discovery_fork_mode, public_discovery_query
from .report import export_report, summary
from .runner import resource_limits
from .workflow import evidence_fields


def _job_budget(max_jobs):
    if type(max_jobs) is not int or not 1 <= max_jobs <= 1000:
        raise ValueError('max_jobs must be an integer between 1 and 1000')


def _validate_discovery(config):
    if not isinstance(config, dict) or not isinstance(config.get('queries'), list):
        raise ValueError('Discovery configuration must contain a queries array')
    for query in config['queries']:
        if not isinstance(query, dict) or any(not isinstance(query.get(k), str) or not query[k].strip()
                                              for k in ('query', 'category')):
            raise ValueError('Each discovery query needs nonempty query and category strings')
        discovery_fork_mode(query['query'],query.get('fork_mode',config.get('fork_mode','include')))
        public_discovery_query(query['query'])
    discovery_fork_mode('',config.get('fork_mode','include'))
    lookback = config.get('lookback_days', 7)
    if type(lookback) is not int or not 0 <= lookback <= 3650:
        raise ValueError('lookback_days must be an integer between 0 and 3650')
    if config.get('date_field', 'pushed') not in ('pushed', 'created'):
        raise ValueError('date_field must be pushed or created')
    if 'interval_seconds' in config:
        interval = config['interval_seconds']
        if type(interval) not in (int, float) or not math.isfinite(interval) or not 1 <= interval <= 86400:
            raise ValueError('interval_seconds must be finite and between 1 and 86400')
    if 'max_jobs' in config:
        _job_budget(config['max_jobs'])
    if 'workers' in config:
        if type(config['workers']) is not int or not 1 <= config['workers'] <= 8:
            raise ValueError('workers must be an integer between 1 and 8')


def _placeholder(value):
    return not isinstance(value, str) or bool(re.search(r'YOUR[-_ ]|REPLACE|CHANGEME|<[^>]+>', value, re.IGNORECASE))


def _credits_status(config):
    configured = config is not None
    config = config if isinstance(config, dict) else {}
    base = config.get('base_url', '')
    try:
        parsed = urlparse(base) if isinstance(base, str) else urlparse('')
        hostname = parsed.hostname or ''
        base_valid = bool(parsed.scheme == 'https' and hostname and not parsed.username and not parsed.password
                          and not parsed.query and not parsed.fragment and not _placeholder(base)
                          and hostname not in ('example.com', 'example.org', 'example.net')
                          and not hostname.endswith(('.invalid', '.example')))
        _ = parsed.port
    except ValueError:
        base_valid = False
    slug = config.get('event_slug')
    slug_valid = isinstance(slug, str) and bool(re.fullmatch(r'[a-z0-9-]{2,64}', slug)) and not _placeholder(slug)
    try:
        expiry_valid = dt.date.fromisoformat(config.get('event_end_date')) > dt.datetime.now(dt.timezone.utc).date()
    except (ValueError, TypeError):
        expiry_valid = False
    try:
        evidence_fields(config, 'event_config_evidence')
        evidence_present = not _placeholder(config.get('event_config_evidence'))
    except (ValueError, TypeError, KeyError):
        evidence_present = False
    token = os.environ.get('NEXT_CREDITS_PAT', '')
    token_valid = bool(re.fullmatch(r'onc_pat_[A-Za-z0-9_-]{43}', token))
    return {'configured': configured, 'base_url_valid': base_valid, 'event_slug_valid': slug_valid,
            'expiry_valid': expiry_valid, 'event_evidence_present': evidence_present,
            'pat_configured': bool(token.strip()), 'pat_format_valid': token_valid,
            'configuration_ready': bool(configured and base_valid and slug_valid and expiry_valid and evidence_present and token_valid),
            'connectivity_checked': False, 'writes_enabled': False}


def doctor(store, credits_config=None, agent_config=None):
    """Read local readiness without network probes, subprocesses or secret output.

    Configuration readiness is static validation, not confirmation of remote
    credentials, event settings, Docker daemon health, or image availability.
    """
    integrity = [row[0] for row in store.conn.execute('PRAGMA integrity_check(20)')]
    integrity_ok = integrity == ['ok']
    foreign_keys_ok = store.conn.execute('PRAGMA foreign_key_check').fetchone() is None
    version = store.schema_version
    schema_ok = version == SCHEMA_VERSION
    queue = {state: 0 for state in ('queued', 'running', 'done', 'dead')}
    queue.update({row['state']: row['count'] for row in store.all('SELECT state,COUNT(*) AS count FROM jobs GROUP BY state')})
    timestamp = time.time()
    queue['ready'] = store.one("SELECT COUNT(*) AS count FROM jobs WHERE state='queued' AND available_at<=?", (timestamp,))['count']
    queue['delayed'] = store.one("SELECT COUNT(*) AS count FROM jobs WHERE state='queued' AND available_at>?", (timestamp,))['count']
    queue['expired_leases'] = store.one("SELECT COUNT(*) AS count FROM jobs WHERE state='running' AND lease_until<=?", (timestamp,))['count']
    docker_available = shutil.which('docker') is not None
    try:
        resource_limits(agent_config)
        agent_valid = not _placeholder(agent_config['image'])
    except (ValueError, TypeError, KeyError):
        agent_valid = False
    return {'generated_at': now(),
            'healthy': bool(integrity_ok and foreign_keys_ok and schema_ok and not queue['dead'] and not queue['expired_leases']),
            'database': {'integrity_ok': integrity_ok, 'foreign_keys_ok': foreign_keys_ok,
                         'schema_version': version, 'schema_supported': schema_ok},
            'queue': queue,
            'github': {'token_configured': bool(os.environ.get('GITHUB_TOKEN', '').strip()), 'connectivity_checked': False},
            'credits': _credits_status(credits_config),
            'agent': {'configured': agent_config is not None, 'configuration_ready': agent_valid,
                      'docker_command_available': docker_available, 'daemon_checked': False, 'image_checked': False},
            'external_notifications_enabled': False}


def _write_reports(store, health, destination):
    """Replace each local report atomically; do not leave partial report files."""
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, filename in (('report', 'report.md'), ('health', 'health.json')):
        fd, temporary = tempfile.mkstemp(prefix=filename + '.', suffix='.tmp', dir=root)
        os.close(fd)
        try:
            if key == 'report':
                export_report(store, temporary)
            else:
                Path(temporary).write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding='utf-8')
            target = root / filename
            os.replace(temporary, target)
            paths[key] = str(target.resolve())
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return paths


def tick(store, discovery_config, max_jobs=20, out_dir=None):
    """Run one bounded collection cycle; the caller retains ownership of store."""
    _job_budget(max_jobs)
    _validate_discovery(discovery_config)
    pipeline = Pipeline(store, GitHub(store))
    # Do not keep fan-out scheduling while a large claimable backlog already exists.
    # Operators can still call /api/schedule explicitly; this only gates the run loop.
    ready = store.one("SELECT COUNT(*) AS count FROM jobs WHERE state='queued' AND available_at<=?", (time.time(),))['count']
    scheduled = [] if ready >= max_jobs * 3 else pipeline.schedule(discovery_config)
    jobs = pipeline.work(max_jobs, workers=discovery_config.get('workers', 1))
    report = summary(store)
    health = doctor(store)
    paths = _write_reports(store, health, out_dir) if out_dir is not None else {}
    return {'scheduled_job_ids': scheduled, 'jobs': jobs, 'summary': report,
            'health': health, 'report_paths': paths}


def _cycle_wait_seconds(db_path, interval_seconds):
    """Prefer waking when delayed jobs become ready instead of idling the full interval."""
    store = Store(db_path)
    try:
        now_ts = time.time()
        ready = store.one("SELECT COUNT(*) AS count FROM jobs WHERE state='queued' AND available_at<=?", (now_ts,))['count']
        if ready:
            return min(interval_seconds, 5)
        nxt = store.one("SELECT MIN(available_at) AS available_at FROM jobs WHERE state='queued' AND available_at>?", (now_ts,))
        if nxt and nxt['available_at'] is not None:
            return max(1.0, min(interval_seconds, float(nxt['available_at']) - now_ts + 0.5))
        return interval_seconds
    finally:
        store.close()


def run_loop(db_path, discovery_config, interval_seconds=300, max_cycles=0,
             max_jobs=20, out_dir=None, stop_event=None):
    """Run explicitly requested cycles until stopped; max_cycles=0 is unbounded.

    Open/close the database per cycle and keep only the latest result in memory.
    stop_event can interrupt waiting immediately; an in-flight bounded cycle
    finishes before shutdown. Unexpected errors propagate to the operator.
    """
    _job_budget(max_jobs)
    _validate_discovery(discovery_config)
    if type(interval_seconds) not in (int, float) or not math.isfinite(interval_seconds) or not 1 <= interval_seconds <= 86400:
        raise ValueError('interval_seconds must be finite and between 1 and 86400')
    if type(max_cycles) is not int or max_cycles < 0:
        raise ValueError('max_cycles must be a nonnegative integer')
    event = stop_event if stop_event is not None else threading.Event()
    cycles, last_tick, interrupted = 0, None, False
    try:
        while not event.is_set() and (not max_cycles or cycles < max_cycles):
            store = Store(db_path)
            try:
                last_tick = tick(store, discovery_config, max_jobs, out_dir)
            finally:
                store.close()
            cycles += 1
            if (max_cycles and cycles >= max_cycles) or event.is_set():
                break
            if event.wait(_cycle_wait_seconds(db_path, interval_seconds)):
                break
    except KeyboardInterrupt:
        interrupted = True
    return {'cycles': cycles, 'stopped': bool(event.is_set() or interrupted), 'last_tick': last_tick}
