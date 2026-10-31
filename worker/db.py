import contextlib
import datetime as dt
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS projects(
 id INTEGER PRIMARY KEY, full_name TEXT NOT NULL, url TEXT NOT NULL,
 metadata TEXT NOT NULL, head_sha TEXT, audience TEXT NOT NULL DEFAULT 'unknown',
 stage TEXT NOT NULL DEFAULT 'discovered', owner TEXT,
 discovered_at TEXT NOT NULL, refreshed_at TEXT, uid TEXT UNIQUE,
 uid_version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS sources(
 project_id INTEGER REFERENCES projects(id), source TEXT, discovered_at TEXT,
 PRIMARY KEY(project_id, source));
CREATE TABLE IF NOT EXISTS maintainers(
 id INTEGER PRIMARY KEY, login TEXT NOT NULL, kind TEXT, profile TEXT NOT NULL,
 do_not_contact INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS project_maintainers(
 project_id INTEGER REFERENCES projects(id), maintainer_id INTEGER REFERENCES maintainers(id),
 role TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0, evidence TEXT,
 PRIMARY KEY(project_id,maintainer_id));
CREATE TABLE IF NOT EXISTS snapshots(
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER REFERENCES projects(id),
 sha TEXT, fetched_at TEXT NOT NULL, evidence TEXT NOT NULL, complete INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS reviews(
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER REFERENCES projects(id),
 round INTEGER NOT NULL CHECK(round IN (1,2)), sha TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('pass','hold','reject')),
 actor TEXT NOT NULL, rationale TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS experiences(
 id TEXT PRIMARY KEY, project_id INTEGER REFERENCES projects(id), sha TEXT NOT NULL,
 audience TEXT NOT NULL CHECK(audience IN ('human','agent')), spec TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', result TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contacts(
 id TEXT PRIMARY KEY, project_id INTEGER REFERENCES projects(id),
 maintainer_id INTEGER REFERENCES maintainers(id), channel TEXT NOT NULL, draft TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft', actor TEXT NOT NULL, evidence TEXT, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS contact_active ON contacts(maintainer_id)
 WHERE status IN ('draft','sent','replied');
CREATE TABLE IF NOT EXISTS interviews(
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER REFERENCES projects(id),
 contact_id TEXT REFERENCES contacts(id), actor TEXT NOT NULL, notes TEXT NOT NULL,
 profile TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(
 id TEXT PRIMARY KEY, project_id INTEGER REFERENCES projects(id), interview_id INTEGER REFERENCES interviews(id),
 outcome TEXT NOT NULL CHECK(outcome IN ('approve','hold','reject')), actor TEXT NOT NULL,
 rationale TEXT NOT NULL, terms TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS grants(
 id TEXT PRIMARY KEY, decision_id TEXT UNIQUE REFERENCES decisions(id),
 status TEXT NOT NULL DEFAULT 'pending', receipt TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS followups(
 id TEXT PRIMARY KEY, grant_id TEXT REFERENCES grants(id), due_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', result TEXT, UNIQUE(grant_id,due_at));
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
 action TEXT NOT NULL, actor TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
 id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT NOT NULL,
 dedupe_key TEXT UNIQUE NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 available_at REAL NOT NULL, lease_until REAL, lease_token TEXT, error TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state,available_at);
CREATE TABLE IF NOT EXISTS search_coverage(
 job_id INTEGER PRIMARY KEY REFERENCES jobs(id), query TEXT NOT NULL, total_count INTEGER,
 fetched INTEGER NOT NULL, status TEXT NOT NULL, details TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS http_cache(
 url TEXT PRIMARY KEY, etag TEXT, body TEXT NOT NULL, headers TEXT NOT NULL);
"""

SERVICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_requests(
 actor TEXT NOT NULL, request_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
 state TEXT NOT NULL, response TEXT, status_code INTEGER, created_at TEXT NOT NULL,
 PRIMARY KEY(actor,request_key));
CREATE TABLE IF NOT EXISTS agent_leases(
 experience_id TEXT PRIMARY KEY REFERENCES experiences(id), actor TEXT NOT NULL,
 token_hash TEXT NOT NULL, lease_until REAL NOT NULL, state TEXT NOT NULL,
 task TEXT NOT NULL DEFAULT '{}', task_sha256 TEXT);
CREATE INDEX IF NOT EXISTS reviews_latest ON reviews(project_id,round,id DESC);
CREATE INDEX IF NOT EXISTS snapshots_project ON snapshots(project_id,id DESC);
CREATE INDEX IF NOT EXISTS experiences_project ON experiences(project_id,status);
CREATE INDEX IF NOT EXISTS events_entity ON events(entity_type,entity_id,id DESC);
"""

ARTIFACT_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifact_uploads(
 id TEXT PRIMARY KEY, experience_id TEXT NOT NULL REFERENCES experiences(id),
 run_id TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
 total_chunks INTEGER NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(experience_id,run_id,path));
CREATE TABLE IF NOT EXISTS artifact_chunks(
 upload_id TEXT NOT NULL REFERENCES artifact_uploads(id), chunk_index INTEGER NOT NULL,
 data BLOB NOT NULL, PRIMARY KEY(upload_id,chunk_index));
CREATE TABLE IF NOT EXISTS artifact_files(
 id TEXT PRIMARY KEY REFERENCES artifact_uploads(id), data BLOB NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifact_links(
 experience_id TEXT NOT NULL REFERENCES experiences(id), artifact_id TEXT NOT NULL REFERENCES artifact_files(id),
 PRIMARY KEY(experience_id,artifact_id));
CREATE INDEX IF NOT EXISTS artifact_experience ON artifact_uploads(experience_id,run_id);
"""

ACCOUNT_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_state(
 id INTEGER PRIMARY KEY CHECK(id=1), initialized INTEGER NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO account_state(id,initialized) VALUES(1,0);
CREATE TABLE IF NOT EXISTS accounts(
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
 password_hash TEXT NOT NULL, roles TEXT NOT NULL, status TEXT NOT NULL,
 auth_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS accounts_status ON accounts(status,created_at);
"""

DOSSIER_SCHEMA = """
CREATE TABLE IF NOT EXISTS qualifications(
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id),
 sha TEXT NOT NULL, policy_version TEXT NOT NULL, category TEXT NOT NULL,
 scope_fit TEXT NOT NULL, maintenance TEXT NOT NULL, outcome TEXT NOT NULL,
 audience TEXT NOT NULL, rationale TEXT NOT NULL, evidence TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS qualifications_latest ON qualifications(project_id,id DESC);
CREATE TABLE IF NOT EXISTS dossier_translations(
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id),
 field TEXT NOT NULL, language TEXT NOT NULL, source_sha256 TEXT NOT NULL,
 content TEXT NOT NULL, method TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS dossier_translations_latest ON dossier_translations(project_id,field,language,id DESC);
"""

CONTACT_SCHEMA = """
CREATE TABLE IF NOT EXISTS contact_leads(
 id INTEGER PRIMARY KEY AUTOINCREMENT, login TEXT NOT NULL COLLATE NOCASE,
 email TEXT NOT NULL, source_file TEXT NOT NULL, source_sha256 TEXT NOT NULL,
 source_row INTEGER NOT NULL, imported_at TEXT NOT NULL,
 UNIQUE(login,email,source_sha256));
CREATE INDEX IF NOT EXISTS contact_leads_login ON contact_leads(login COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS contact_points(
 id TEXT PRIMARY KEY, project_id INTEGER REFERENCES projects(id),
 maintainer_id INTEGER REFERENCES maintainers(id), channel TEXT NOT NULL, value TEXT NOT NULL,
 source TEXT NOT NULL, source_url TEXT NOT NULL, confidence TEXT NOT NULL,
 evidence TEXT NOT NULL, discovered_at TEXT NOT NULL,
 source_kind TEXT NOT NULL DEFAULT 'other', source_ref TEXT,
 explicit INTEGER NOT NULL DEFAULT 1,
 CHECK(project_id IS NOT NULL OR maintainer_id IS NOT NULL));
CREATE INDEX IF NOT EXISTS contact_points_project ON contact_points(project_id);
CREATE INDEX IF NOT EXISTS contact_points_maintainer ON contact_points(maintainer_id);
CREATE TABLE IF NOT EXISTS contact_summaries(
 project_id INTEGER NOT NULL REFERENCES projects(id), snapshot_id INTEGER REFERENCES snapshots(id),
 class TEXT NOT NULL CHECK(class IN ('with_clue','without_clue','do_not_contact')),
 source_mask TEXT NOT NULL DEFAULT '', point_count INTEGER NOT NULL DEFAULT 0,
 priority INTEGER NOT NULL DEFAULT 0, rationale TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 PRIMARY KEY(project_id, snapshot_id));
CREATE INDEX IF NOT EXISTS contact_summary_latest ON contact_summaries(project_id, created_at DESC);
"""

DISCOVERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS discovery_runs(
 id TEXT PRIMARY KEY, source TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
 config TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', requested_by TEXT NOT NULL,
 created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
 counts TEXT NOT NULL DEFAULT '{}', error TEXT);
CREATE INDEX IF NOT EXISTS discovery_runs_state ON discovery_runs(state, created_at DESC);
CREATE TABLE IF NOT EXISTS project_rankings(
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id),
 run_id TEXT NOT NULL REFERENCES discovery_runs(id), board TEXT NOT NULL, period TEXT NOT NULL,
 language TEXT, rank INTEGER NOT NULL CHECK(rank > 0), rank_delta INTEGER, weight REAL NOT NULL DEFAULT 0,
 source_url TEXT NOT NULL, evidence TEXT NOT NULL, captured_at TEXT NOT NULL,
 UNIQUE(project_id, board, period, language));
CREATE INDEX IF NOT EXISTS project_rankings_latest ON project_rankings(project_id, board, captured_at DESC);
CREATE TABLE IF NOT EXISTS ai_assessments(
 id TEXT PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
 snapshot_id INTEGER NOT NULL REFERENCES snapshots(id), model TEXT NOT NULL, prompt_version TEXT NOT NULL,
 input_sha256 TEXT NOT NULL, status TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
 dimensions TEXT NOT NULL DEFAULT '{}', total_score REAL, tier TEXT,
 strengths TEXT NOT NULL DEFAULT '[]', weaknesses TEXT NOT NULL DEFAULT '[]', business_value TEXT,
 panorama TEXT NOT NULL DEFAULT '{}',
 contactability TEXT, evidence_refs TEXT NOT NULL DEFAULT '[]', uncertainty TEXT,
 created_at TEXT NOT NULL, completed_at TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS ai_assessments_latest ON ai_assessments(project_id, created_at DESC);
CREATE TABLE IF NOT EXISTS provider_limits(
 provider TEXT NOT NULL, token_fingerprint TEXT NOT NULL, remaining INTEGER,
 reset_at REAL, next_allowed_at REAL NOT NULL DEFAULT 0, concurrency INTEGER NOT NULL DEFAULT 1,
 in_flight INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
 PRIMARY KEY(provider, token_fingerprint));
CREATE TABLE IF NOT EXISTS provider_rate_leases(
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, token_fingerprint TEXT NOT NULL,
 lease_until REAL NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS provider_rate_leases_lookup
 ON provider_rate_leases(provider,token_fingerprint,lease_until);
CREATE TABLE IF NOT EXISTS referral_links(
 id TEXT PRIMARY KEY, creator_actor TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
 scope_project_id INTEGER REFERENCES projects(id), status TEXT NOT NULL DEFAULT 'active',
 expires_at TEXT, created_at TEXT NOT NULL, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS referrals(
 id TEXT PRIMARY KEY, link_id TEXT NOT NULL REFERENCES referral_links(id),
 project_id INTEGER NOT NULL REFERENCES projects(id), submitter TEXT NOT NULL,
 evidence TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'submitted', created_at TEXT NOT NULL,
 UNIQUE(link_id, project_id));
CREATE TABLE IF NOT EXISTS community_memberships(
 id TEXT PRIMARY KEY, maintainer_id INTEGER NOT NULL REFERENCES maintainers(id),
 project_id INTEGER REFERENCES projects(id), role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
 granted_by TEXT NOT NULL, expires_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS incentive_ledger(
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, referral_id TEXT REFERENCES referrals(id),
 kind TEXT NOT NULL, points INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 evidence TEXT NOT NULL, reviewed_by TEXT, created_at TEXT NOT NULL, reviewed_at TEXT);
"""

SCHEMA_VERSION = 10


def stable_project_uid(project_id):
    """Return an opaque, deterministic UID for legacy GitHub numeric IDs."""
    raw = hashlib.sha256(('github:' + str(project_id)).encode('utf-8')).hexdigest()[:32]
    return 'prj_' + raw


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA journal_mode=DELETE")
            self._migrate()
        except BaseException:
            self.conn.close()
            raise

    def _migrate(self):
        # Guard before touching business tables. A future schema must never be downgraded.
        with self.transaction():
            exists = self.one("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
            versions = [r['version'] for r in self.all('SELECT version FROM schema_version')] if exists else []
            if any(type(v) is not int or v < 1 or v > SCHEMA_VERSION for v in versions):
                raise ValueError('Database schema is incompatible with this worker version')
            if versions and max(versions) == SCHEMA_VERSION:
                return
            # Schemas deliberately contain no triggers or semicolons in literals.
            for statement in (SCHEMA + SERVICE_SCHEMA + ARTIFACT_SCHEMA + ACCOUNT_SCHEMA + DOSSIER_SCHEMA + CONTACT_SCHEMA + DISCOVERY_SCHEMA).split(';'):
                if statement.strip():
                    self.conn.execute(statement)
            project_columns = {r['name'] for r in self.all('PRAGMA table_info(projects)')}
            if 'uid' not in project_columns:
                self.conn.execute('ALTER TABLE projects ADD COLUMN uid TEXT')
            if 'uid_version' not in project_columns:
                self.conn.execute('ALTER TABLE projects ADD COLUMN uid_version INTEGER NOT NULL DEFAULT 1')
            contact_columns = {r['name'] for r in self.all('PRAGMA table_info(contact_points)')}
            ai_columns = {r['name'] for r in self.all('PRAGMA table_info(ai_assessments)')}
            if 'panorama' not in ai_columns:
                self.conn.execute("ALTER TABLE ai_assessments ADD COLUMN panorama TEXT NOT NULL DEFAULT '{}'")
            if 'source_kind' not in contact_columns:
                self.conn.execute("ALTER TABLE contact_points ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'other'")
            if 'source_ref' not in contact_columns:
                self.conn.execute('ALTER TABLE contact_points ADD COLUMN source_ref TEXT')
            if 'explicit' not in contact_columns:
                self.conn.execute('ALTER TABLE contact_points ADD COLUMN explicit INTEGER NOT NULL DEFAULT 1')
            for row in self.all('SELECT id FROM projects WHERE uid IS NULL OR uid = ""'):
                self.conn.execute('UPDATE projects SET uid=?,uid_version=1 WHERE id=?',
                                  (stable_project_uid(row['id']), row['id']))
            self.conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS projects_uid ON projects(uid)')
            lease_columns = {r['name'] for r in self.all('PRAGMA table_info(agent_leases)')}
            if 'task' not in lease_columns:
                self.conn.execute("ALTER TABLE agent_leases ADD COLUMN task TEXT NOT NULL DEFAULT '{}'")
            if 'task_sha256' not in lease_columns:
                self.conn.execute('ALTER TABLE agent_leases ADD COLUMN task_sha256 TEXT')
            self.conn.execute('DELETE FROM schema_version')
            self.conn.execute('INSERT INTO schema_version VALUES(?)', (SCHEMA_VERSION,))

    @property
    def schema_version(self):
        return self.one('SELECT MAX(version) AS version FROM schema_version')['version']

    def close(self):
        self.conn.close()

    @contextlib.contextmanager
    def transaction(self):
        nested = self.conn.in_transaction
        name = 'sp_' + uuid.uuid4().hex
        self.conn.execute('SAVEPOINT ' + name if nested else "BEGIN IMMEDIATE")
        try:
            yield
            self.conn.execute('RELEASE SAVEPOINT ' + name if nested else "COMMIT")
        except BaseException:
            if nested:
                self.conn.execute('ROLLBACK TO SAVEPOINT ' + name)
                self.conn.execute('RELEASE SAVEPOINT ' + name)
            else:
                self.conn.execute("ROLLBACK")
            raise

    def one(self, sql, args=()):
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, args=()):
        return [dict(r) for r in self.conn.execute(sql, args)]

    def event(self, entity_type, entity_id, action, actor, payload):
        self.conn.execute("INSERT INTO events(entity_type,entity_id,action,actor,payload,created_at) VALUES(?,?,?,?,?,?)",
                          (entity_type, str(entity_id), action, actor, dump(payload), now()))

    def enqueue(self, kind, payload, key, available_at=None):
        self.conn.execute("INSERT OR IGNORE INTO jobs(kind,payload,dedupe_key,available_at,created_at) VALUES(?,?,?,?,?)",
                          (kind, dump(payload), key, time.time() if available_at is None else available_at, now()))
        return self.one("SELECT id FROM jobs WHERE dedupe_key=?", (key,))["id"]

    def claim(self, lease_seconds=900):
        with self.transaction():
            t = time.time()
            self.conn.execute("UPDATE jobs SET state='dead',error='Lease expired after maximum attempts',lease_token=NULL WHERE state='running' AND lease_until<? AND attempts>=5", (t,))
            self.conn.execute("UPDATE jobs SET state='queued',lease_token=NULL WHERE state='running' AND lease_until<?", (t,))
            job = self.one("SELECT * FROM jobs WHERE state='queued' AND available_at<=? ORDER BY available_at,id LIMIT 1", (t,))
            if job is None:
                return None
            token = uuid.uuid4().hex
            self.conn.execute("UPDATE jobs SET state='running',attempts=attempts+1,lease_until=?,lease_token=? WHERE id=?",
                              (t + lease_seconds, token, job['id']))
            job.update(lease_token=token, attempts=job['attempts'] + 1)
            job['payload'] = json.loads(job['payload'])
            return job

    def finish(self, job, error=None, delay=0, retryable=True):
        state = 'done' if error is None else ('queued' if retryable and job['attempts'] < 5 else 'dead')
        t = time.time()
        self.conn.execute("UPDATE jobs SET state=?,error=?,available_at=?,lease_until=NULL,lease_token=NULL WHERE id=? AND lease_token=? AND state='running' AND lease_until>?",
                          (state, error, t + delay, job['id'], job['lease_token'], t))

    def assert_lease(self, job):
        row = self.one("SELECT id FROM jobs WHERE id=? AND state='running' AND lease_token=? AND lease_until>?",
                       (job['id'], job['lease_token'], time.time()))
        if not row:
            raise ValueError('Job lease expired or belongs to another worker')

    def heartbeat(self, job, lease_seconds=900):
        if not 1 <= lease_seconds <= 3600:
            raise ValueError('lease_seconds must be 1..3600')
        t = time.time()
        cursor = self.conn.execute("UPDATE jobs SET lease_until=? WHERE id=? AND state='running' AND lease_token=? AND lease_until>?",
                                   (t + lease_seconds, job['id'], job['lease_token'], t))
        if not cursor.rowcount:
            raise ValueError('Job lease expired or belongs to another worker')

    def upsert_project(self, repo, source):
        self.conn.execute("""INSERT INTO projects(id,full_name,url,metadata,discovered_at)
          VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET full_name=excluded.full_name,
          url=excluded.url,metadata=excluded.metadata""",
                          (repo['id'], repo['full_name'], repo['html_url'], dump(repo), now()))
        self.conn.execute('UPDATE projects SET uid=COALESCE(uid,?),uid_version=COALESCE(uid_version,1) WHERE id=?',
                          (stable_project_uid(repo['id']), repo['id']))
        self.conn.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?)", (repo['id'], source, now()))
        return repo['id']

    def backup(self, destination):
        target = Path(destination)
        # A broken symlink reports exists() == False but sqlite would follow it
        # and create/overwrite a file outside the requested destination.
        # Reject links in the target and its parent chain before opening the
        # destination database.
        if target.is_symlink() or (hasattr(target, 'is_junction') and target.is_junction()):
            raise ValueError('Backup destination must not be a symlink or junction')
        for parent in target.parents:
            if parent == parent.parent:
                break
            if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
                raise ValueError('Backup destination must not be inside a symlink or junction')
        if target.resolve() == self.path.resolve() or target.exists():
            raise ValueError('Backup destination must be a new, separate file')
        target.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(target)) as connection:
            self.conn.backup(connection)
        return str(target.resolve())
