import sqlite3
import tempfile
import unittest
from pathlib import Path
from worker.db import Store, SCHEMA, SCHEMA_VERSION


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'test.db'

    def tearDown(self): self.temp.cleanup()

    def test_v1_upgrade_preserves_existing_data(self):
        connection=sqlite3.connect(self.path)
        connection.executescript(SCHEMA)
        connection.execute('INSERT INTO schema_version VALUES(1)')
        connection.execute("INSERT INTO projects(id,full_name,url,metadata,discovered_at) VALUES(1,'a/b','https://github.com/a/b','{}','2026-01-01')")
        connection.commit();connection.close()
        store=Store(self.path)
        try:
            self.assertEqual(store.schema_version,SCHEMA_VERSION)
            self.assertEqual(store.one('SELECT full_name FROM projects')['full_name'],'a/b')
            self.assertTrue(store.one("SELECT name FROM sqlite_master WHERE name='api_requests'"))
        finally:store.close()

    def test_v2_lease_upgrade_preserves_records(self):
        connection=sqlite3.connect(self.path)
        connection.executescript(SCHEMA)
        connection.execute('INSERT INTO schema_version VALUES(2)')
        connection.execute('CREATE TABLE agent_leases(experience_id TEXT PRIMARY KEY,actor TEXT,token_hash TEXT,lease_until REAL,state TEXT)')
        connection.commit();connection.close()
        store=Store(self.path)
        try:
            self.assertEqual(store.schema_version,SCHEMA_VERSION)
            self.assertIn('task_sha256',{r['name'] for r in store.all('PRAGMA table_info(agent_leases)')})
        finally:store.close()

    def test_future_schema_refused_before_business_changes(self):
        connection=sqlite3.connect(self.path)
        connection.executescript('CREATE TABLE schema_version(version INTEGER PRIMARY KEY); INSERT INTO schema_version VALUES(999);')
        connection.close()
        with self.assertRaises(ValueError):Store(self.path)
        connection=sqlite3.connect(self.path)
        try:self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='projects'").fetchone())
        finally:connection.close()

    def test_expired_job_cannot_finish_without_reclaim(self):
        store=Store(self.path)
        try:
            store.enqueue('refresh',{'repo':'a/b'},'x');job=store.claim()
            store.conn.execute('UPDATE jobs SET lease_until=0')
            store.finish(job,error='late error',retryable=False)
            self.assertEqual(store.one('SELECT state FROM jobs')['state'],'running')
            with self.assertRaises(ValueError):store.heartbeat(job)
            with self.assertRaises(ValueError):store.assert_lease(job)
        finally:store.close()


if __name__ == '__main__':unittest.main()
