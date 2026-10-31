import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from worker.db import SCHEMA, SCHEMA_VERSION, Store, stable_project_uid
from worker.pipeline import Pipeline
from worker.projects import list_projects


REPO = {
    "id": 42,
    "full_name": "owner/repo",
    "html_url": "https://github.com/owner/repo",
    "description": "A project",
    "stargazers_count": 12,
}


class Week1AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "week1.db"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_new_project_has_stable_uid_and_discovered_status(self):
        self.store.upsert_project(REPO, "manual")
        row = self.store.one("SELECT uid, uid_version, stage FROM projects WHERE id=?", (42,))
        self.assertRegex(row["uid"], r"^prj_[a-f0-9]{32}$")
        self.assertEqual(row["uid"], stable_project_uid(42))
        self.assertEqual(row["uid_version"], 1)
        self.assertEqual(row["stage"], "discovered")

    def test_legacy_project_gets_deterministic_uid_during_migration(self):
        self.store.close()
        legacy_path = Path(self.temp.name) / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE schema_version(version INTEGER PRIMARY KEY);
            CREATE TABLE projects(
                id INTEGER PRIMARY KEY, full_name TEXT NOT NULL, url TEXT NOT NULL,
                metadata TEXT NOT NULL, head_sha TEXT, audience TEXT NOT NULL DEFAULT 'unknown',
                stage TEXT NOT NULL DEFAULT 'discovered', owner TEXT,
                discovered_at TEXT NOT NULL, refreshed_at TEXT
            );
            """
        )
        connection.execute("INSERT INTO schema_version VALUES(?)", (1,))
        connection.execute(
            "INSERT INTO projects(id,full_name,url,metadata,discovered_at) VALUES(?,?,?,?,?)",
            (42, "owner/repo", REPO["html_url"], "{}", "2026-10-19"),
        )
        connection.commit()
        connection.close()

        migrated = Store(legacy_path)
        row = migrated.one("SELECT uid, uid_version FROM projects WHERE id=42")
        self.assertEqual(row["uid"], stable_project_uid(42))
        self.assertEqual(row["uid_version"], 1)
        columns = {item["name"] for item in migrated.all("PRAGMA table_info(projects)")}
        self.assertTrue({"uid", "uid_version"}.issubset(columns))
        self.assertIsNotNone(migrated.one("SELECT name FROM sqlite_master WHERE type='index' AND name='projects_uid'"))
        self.assertEqual(migrated.schema_version, SCHEMA_VERSION)
        migrated.close()
        self.store = Store(self.path)

    def test_duplicate_import_is_idempotent_and_preserves_uid(self):
        self.store.upsert_project(REPO, "manual")
        first_uid = self.store.one("SELECT uid FROM projects WHERE id=42")["uid"]
        updated = dict(REPO, full_name="owner/renamed-repo", html_url="https://github.com/owner/renamed-repo",
                       description="Updated description")
        self.store.upsert_project(updated, "manual")
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM projects")["n"], 1)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM sources")["n"], 1)
        self.assertEqual(self.store.one("SELECT uid FROM projects WHERE id=42")["uid"], first_uid)
        self.assertEqual(
            json.loads(self.store.one("SELECT metadata FROM projects WHERE id=42")["metadata"])["description"],
            "Updated description",
        )
        self.assertEqual(self.store.one("SELECT full_name FROM projects WHERE id=42")["full_name"], "owner/renamed-repo")

    def test_ranking_weights_and_period_upsert_are_auditable(self):
        self.store.upsert_project(REPO, "github_trending")
        run_id = "run_week1"
        self.store.conn.execute(
            "INSERT INTO discovery_runs(id,source,start_date,end_date,config,requested_by,created_at) VALUES(?,?,?,?,?,?,?)",
            (run_id, "github_trending", "2026-10-19", "2026-10-19", "{}", "tester", "2026-10-19"),
        )
        pipeline = Pipeline(self.store, None)
        for rank, expected_weight in ((1, 15), (3, 12), (7, 9), (11, 5)):
            pipeline._record_ranking(42, run_id, "github_trending", f"2026-10-{rank:02d}", None, rank,
                                     "https://github.com/trending", {"rank": rank})
            row = self.store.one(
                "SELECT weight FROM project_rankings WHERE project_id=42 AND period=?", (f"2026-10-{rank:02d}",)
            )
            self.assertEqual(row["weight"], expected_weight)

        pipeline._record_ranking(42, run_id, "github_trending", "2026-10-19", "Python", 1,
                                 "https://github.com/trending", {"rank": 1})
        pipeline._record_ranking(42, run_id, "github_trending", "2026-10-19", "Python", 4,
                                 "https://github.com/trending", {"rank": 4})
        row = self.store.one(
            "SELECT rank, weight, evidence FROM project_rankings "
            "WHERE project_id=42 AND board='github_trending' AND period='2026-10-19' AND language='Python'"
        )
        self.assertEqual((row["rank"], row["weight"]), (4, 12))
        self.assertEqual(json.loads(row["evidence"])["rank"], 4)

    def test_project_list_exposes_week1_fields_and_priority(self):
        self.store.upsert_project(REPO, "github_trending")
        self.store.conn.execute(
            "INSERT INTO discovery_runs(id,source,start_date,end_date,config,requested_by,created_at) VALUES(?,?,?,?,?,?,?)",
            ("run_list", "github_trending", "2026-10-19", "2026-10-19", "{}", "tester", "2026-10-19"),
        )
        self.store.conn.execute(
            "INSERT INTO project_rankings(project_id,run_id,board,period,language,rank,weight,source_url,evidence,captured_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (42, "run_list", "github_trending", "2026-10-19", "Python", 1, 15,
             "https://github.com/trending", "{}", "2026-10-19"),
        )
        self.store.conn.execute(
            "INSERT INTO contact_summaries(project_id,snapshot_id,class,source_mask,point_count,priority,rationale,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (42, None, "with_clue", "readme", 1, 100, "explicit", "2026-10-19"),
        )
        self.store.conn.execute(
            "INSERT INTO contact_summaries(project_id,snapshot_id,class,source_mask,point_count,priority,rationale,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (42, None, "with_clue", "readme", 1, 100, "explicit", "2026-10-19"),
        )
        result = list_projects(self.store, {"page": ["1"], "page_size": ["10"], "sort": ["priority"]})
        item = result["items"][0]
        self.assertEqual(item["contact_class"], "with_clue")
        self.assertEqual(item["contact_priority"], 100)
        for key in ("uid", "stage", "ranking_rank", "ranking_weight", "contact_class", "contact_priority", "priority_score"):
            self.assertIn(key, item)
        self.assertEqual(item["contact_class"], "with_clue")
        self.assertEqual(item["contact_priority"], 100)
        self.assertEqual(item["uid"], stable_project_uid(42))
        self.assertEqual(item["stage"], "discovered")
        self.assertEqual(item["ranking_rank"], 1)
        self.assertEqual(item["ranking_weight"], 15)
        self.assertEqual(item["priority_score"], 115)


if __name__ == "__main__":
    unittest.main()
