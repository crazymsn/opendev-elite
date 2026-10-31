import argparse
import csv
import logging
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from .credits import CreditsError, NextCredits
from .db import Store, dump
from .github import GitHub
from .pipeline import Pipeline, normalize_repo
from .report import export_report, summary
from .runner import export_task, run_agent
from .workflow import Workflow


def read_json(path):
    def invalid_constant(_):
        raise ValueError('Non-finite JSON numbers are not supported')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'),parse_constant=invalid_constant)


def discovery_config(path=None):
    if path:
        return read_json(path)
    from importlib.resources import files
    return json.loads(files('worker').joinpath('resources/discovery.json').read_text(encoding='utf-8'))


def parser():
    p = argparse.ArgumentParser(description='GitHub discovery → evidence → reviews → sponsorship')
    default = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/share'))) / 'github-sponsorship' / 'worker.db'
    p.add_argument('--db', default=os.environ.get('SPONSORSHIP_DB', str(default)))
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    a = commands.add_parser('schedule'); a.add_argument('--config'); a.add_argument('--start'); a.add_argument('--end')
    a = commands.add_parser('import'); a.add_argument('repos', nargs='+')
    a = commands.add_parser('import-csv'); a.add_argument('file')
    a = commands.add_parser('work'); a.add_argument('--max-jobs', type=int, default=20); a.add_argument('--workers', type=int, default=1)
    a = commands.add_parser('retry-job'); a.add_argument('id', type=int)
    commands.add_parser('status')
    a = commands.add_parser('report'); a.add_argument('--out', default='artifacts/report.md')
    a = commands.add_parser('project'); a.add_argument('id', type=int); a.add_argument('--out')
    a = commands.add_parser('translations-export'); a.add_argument('--field',choices=('description','readme','readme_summary'),default='description'); a.add_argument('--language',choices=('zh-CN','en'),default='zh-CN'); a.add_argument('--limit',type=int,default=200); a.add_argument('--out',required=True)
    a = commands.add_parser('translations-import'); a.add_argument('--file',required=True); a.add_argument('--actor',required=True)
    a = commands.add_parser('action'); a.add_argument('action'); a.add_argument('--file', required=True); a.add_argument('--actor', required=True)
    a = commands.add_parser('agent-export'); a.add_argument('id'); a.add_argument('--out', required=True)
    a = commands.add_parser('agent-run'); a.add_argument('id'); a.add_argument('--out', required=True); a.add_argument('--config', required=True)
    a = commands.add_parser('agent-poll'); a.add_argument('--server',required=True); a.add_argument('--token-file',required=True); a.add_argument('--out',required=True); a.add_argument('--config',required=True); a.add_argument('--max-tasks',type=int,default=1); a.add_argument('--heartbeat',type=int,default=30); a.add_argument('--resume',action='store_true')
    for name in ('credits-plan','credits-submit','credits-reconcile'):
        a = commands.add_parser(name); a.add_argument('id'); a.add_argument('--config', required=True)
        if name != 'credits-plan':
            a.add_argument('--actor', required=True)
        if name == 'credits-submit':
            a.add_argument('--execute', action='store_true', help='Create approved invitation in configured Credits service; does not send email')
    a = commands.add_parser('backup'); a.add_argument('destination')
    a = commands.add_parser('demo'); a.add_argument('--out', default='artifacts/demo')
    a = commands.add_parser('auth-key'); a.add_argument('--actor', required=True); a.add_argument('--roles', nargs='+', required=True); a.add_argument('--secret-out',required=True)
    a = commands.add_parser('serve'); a.add_argument('--config',required=True); a.add_argument('--credits-config'); a.add_argument('--host',default='127.0.0.1'); a.add_argument('--port',type=int,default=8765); a.add_argument('--tls-cert'); a.add_argument('--tls-key')
    a = commands.add_parser('doctor'); a.add_argument('--credits-config'); a.add_argument('--agent-config')
    a = commands.add_parser('setup-local'); a.add_argument('--root'); a.add_argument('--demo',action='store_true')
    a = commands.add_parser('acceptance'); a.add_argument('--service-config'); a.add_argument('--credits-config'); a.add_argument('--agent-config'); a.add_argument('--out')
    for name in ('tick','run'):
        a = commands.add_parser(name); a.add_argument('--config'); a.add_argument('--max-jobs',type=int,default=20); a.add_argument('--out')
        if name == 'run':
            a.add_argument('--interval',type=int,default=300); a.add_argument('--max-cycles',type=int,default=0)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    s = None
    try:
        c = args.command
        if c == 'agent-poll':
            from .agent_client import consume_tasks
            result = consume_tasks(args.server,args.token_file,args.out,read_json(args.config),
                                   args.max_tasks,args.heartbeat,args.resume)
            print(dump(result))
            return 1 if result['blocked'] else 0
        if c == 'setup-local':
            from .deployment import setup_local
            print(dump(setup_local(args.root,args.demo)))
            return 0
        if c == 'auth-key':
            from .auth import create_key
            print(dump(create_key(args.actor,args.roles,args.secret_out)))
            return 0
        if c == 'serve':
            from .service import serve
            logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
            serve(args.db,read_json(args.config),args.host,args.port,
                  read_json(args.credits_config) if args.credits_config else None,args.tls_cert,args.tls_key)
            return 0
        if c == 'run':
            from .operations import run_loop
            result = run_loop(args.db,discovery_config(args.config),args.interval,args.max_cycles,args.max_jobs,args.out)
            print(dump(result))
            return 0
        s = Store(args.db)
        if c == 'init':
            result = {'database': str(s.path.resolve()), 'schema_version': s.schema_version}
        elif c == 'schedule':
            result = {'job_ids': Pipeline(s, GitHub(s)).schedule(discovery_config(args.config), args.start, args.end)}
        elif c in ('import','import-csv'):
            repos = args.repos if c == 'import' else []
            if c == 'import-csv':
                with Path(args.file).open(encoding='utf-8-sig', newline='') as stream:
                    sample = stream.read(4096); stream.seek(0)
                    dialect = csv.Sniffer().sniff(sample, delimiters=',\t')
                    for row in csv.DictReader(stream, dialect=dialect):
                        value = row.get('url') or row.get('full_name') or row.get('name')
                        if not value:
                            raise ValueError('CSV must contain url, full_name or name')
                        repos.append(value)
            names = sorted(set(normalize_repo(x) for x in repos))
            run = uuid.uuid4().hex
            with s.transaction():
                result = {'job_ids': [s.enqueue('refresh', {'repo': name, 'source': 'manual' if c == 'import' else 'legacy_csv'}, 'import:' + name + ':' + run) for name in names]}
        elif c == 'work':
            if not 1 <= args.max_jobs <= 1000:
                raise ValueError('max-jobs must be 1..1000')
            if not 1 <= args.workers <= 8:
                raise ValueError('workers must be 1..8')
            result = {'jobs': Pipeline(s, GitHub(s)).work(args.max_jobs, args.workers)}
        elif c == 'retry-job':
            with s.transaction():
                cur = s.conn.execute("UPDATE jobs SET state='queued',attempts=0,available_at=0,error=NULL WHERE id=? AND state='dead'", (args.id,))
                if not cur.rowcount:
                    raise ValueError('Dead job not found')
                s.event('job', args.id, 'retry', 'local-operator', {})
            result = {'id': args.id, 'state': 'queued'}
        elif c == 'status':
            result = summary(s)
        elif c == 'report':
            result = {'path': export_report(s, args.out)}
        elif c == 'project':
            project = Workflow(s).project(args.id)
            result = {'project': project, 'snapshots': s.all('SELECT * FROM snapshots WHERE project_id=? ORDER BY id DESC', (args.id,)),
                      'reviews': s.all('SELECT * FROM reviews WHERE project_id=? ORDER BY id', (args.id,)),
                      'maintainers': s.all('SELECT m.*,pm.role,pm.confirmed FROM maintainers m JOIN project_maintainers pm ON pm.maintainer_id=m.id WHERE pm.project_id=?', (args.id,))}
            from .criteria import qualification_status
            from .dossier import dossier
            result['qualification'] = qualification_status(s,project)
            result['dossier'] = dossier(s,project)
            from .projects import readiness
            result['readiness'] = readiness(s,project)
            result['qualifications'] = s.all('SELECT * FROM qualifications WHERE project_id=? ORDER BY id DESC', (args.id,))
            if args.out:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(dump(result), encoding='utf-8'); result = {'path': str(Path(args.out).resolve())}
        elif c == 'translations-export':
            from .dossier import export_translation_work
            document=export_translation_work(s,args.field,args.language,args.limit)
            path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(dump(document),encoding='utf-8')
            result={'path':str(path.resolve()),'count':document['count']}
        elif c == 'translations-import':
            from .dossier import import_translations
            result=import_translations(s,read_json(args.file),args.actor)
        elif c == 'action':
            result = Workflow(s).apply(args.action, read_json(args.file), args.actor)
        elif c == 'agent-export':
            result = export_task(s, args.id, args.out)
        elif c == 'agent-run':
            result = run_agent(s, args.id, args.out, read_json(args.config))
        elif c.startswith('credits-'):
            adapter = NextCredits(s, read_json(args.config))
            if c == 'credits-plan': result = adapter.plan(args.id)
            elif c == 'credits-submit': result = adapter.submit(args.id, args.actor, args.execute)
            else: result = adapter.reconcile(args.id, args.actor)
        elif c == 'backup':
            result = {'path': s.backup(args.destination)}
        elif c == 'demo':
            from .demo import run_demo
            result = run_demo(s, args.out)
        elif c == 'doctor':
            from .operations import doctor
            result = doctor(s,read_json(args.credits_config) if args.credits_config else None,
                            read_json(args.agent_config) if args.agent_config else None)
        elif c == 'tick':
            from .operations import tick
            result = tick(s,discovery_config(args.config),args.max_jobs,args.out)
        elif c == 'acceptance':
            from .acceptance import audit_readiness
            result = audit_readiness(s,read_json(args.service_config) if args.service_config else None,
                                    read_json(args.credits_config) if args.credits_config else None,
                                    read_json(args.agent_config) if args.agent_config else None)
            if args.out:
                path = Path(args.out)
                path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('x',encoding='utf-8') as stream:
                    json.dump(result,stream,ensure_ascii=False,indent=2)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if c == 'work' and any(x['status'] == 'dead' for x in result['jobs']):
            return 1
        return 0
    except KeyboardInterrupt:
        print(dump({'status':'stopped'}))
        return 130
    except (ValueError, KeyError, TypeError, csv.Error, CreditsError, sqlite3.Error, OSError) as e:
        print(dump({'error': type(e).__name__, 'message': str(e)}), file=sys.stderr)
        return 2
    finally:
        if s is not None:
            s.close()
