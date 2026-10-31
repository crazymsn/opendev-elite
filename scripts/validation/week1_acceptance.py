"""Run isolated Week 1 UID/data-model acceptance checks."""
import argparse
import json
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
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


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def check_uid_format(store):
    store.upsert_project(REPO, "manual")
    row = store.one("SELECT uid, uid_version, stage FROM projects WHERE id=42")
    require(re.fullmatch(r"prj_[a-f0-9]{32}", row["uid"]), "UID has invalid format")
    require(row["uid"] == stable_project_uid(42), "UID is not stable")
    require(row["uid_version"] == 1, "UID version is not 1")
    require(row["stage"] == "discovered", "default stage is not discovered")
    return {"uid": row["uid"], "uid_version": row["uid_version"], "stage": row["stage"]}


def check_legacy_uid_backfill(temp_dir):
    path = Path(temp_dir) / "legacy.db"
    connection = sqlite3.connect(path)
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
    store = Store(path)
    try:
        row = store.one("SELECT uid, uid_version FROM projects WHERE id=42")
        require(row["uid"] == stable_project_uid(42), "legacy UID was not backfilled")
        require(row["uid_version"] == 1, "legacy UID version is not 1")
        require(store.schema_version == SCHEMA_VERSION, "schema did not migrate to current version")
        columns = {item["name"] for item in store.all("PRAGMA table_info(projects)")}
        require({"uid", "uid_version"}.issubset(columns), "UID columns were not added")
        index = store.one("SELECT name FROM sqlite_master WHERE type='index' AND name='projects_uid'")
        require(index is not None, "UID unique index is missing")
        return {"uid": row["uid"], "schema_version": store.schema_version}
    finally:
        store.close()


def check_duplicate_import(store):
    store.upsert_project(REPO, "manual")
    first_uid = store.one("SELECT uid FROM projects WHERE id=42")["uid"]
    updated = dict(REPO, full_name="owner/renamed-repo", html_url="https://github.com/owner/renamed-repo",
                   description="Updated description")
    store.upsert_project(updated, "manual")
    require(store.one("SELECT COUNT(*) AS n FROM projects")["n"] == 1, "duplicate project created")
    require(store.one("SELECT COUNT(*) AS n FROM sources")["n"] == 1, "duplicate source created")
    require(store.one("SELECT uid FROM projects WHERE id=42")["uid"] == first_uid, "UID changed on rename")
    return {"projects": 1, "sources": 1, "uid_preserved": True}


def check_ranking_weights(store):
    store.upsert_project(REPO, "github_trending")
    store.conn.execute(
        "INSERT INTO discovery_runs(id,source,start_date,end_date,config,requested_by,created_at) VALUES(?,?,?,?,?,?,?)",
        ("run_week1", "github_trending", "2026-10-19", "2026-10-19", "{}", "tester", "2026-10-19"),
    )
    pipeline = Pipeline(store, None)
    observed = {}
    for rank, expected in ((1, 15), (3, 12), (7, 9), (11, 5)):
        period = f"2026-10-{rank:02d}"
        pipeline._record_ranking(42, "run_week1", "github_trending", period, None, rank,
                                 "https://github.com/trending", {"rank": rank})
        row = store.one("SELECT weight FROM project_rankings WHERE project_id=42 AND period=?", (period,))
        require(row["weight"] == expected, f"rank {rank} has incorrect weight")
        observed[str(rank)] = row["weight"]
    pipeline._record_ranking(42, "run_week1", "github_trending", "2026-10-19", "Python", 1,
                             "https://github.com/trending", {"rank": 1})
    pipeline._record_ranking(42, "run_week1", "github_trending", "2026-10-19", "Python", 4,
                             "https://github.com/trending", {"rank": 4})
    row = store.one(
        "SELECT rank, weight FROM project_rankings WHERE project_id=42 AND board='github_trending' "
        "AND period='2026-10-19' AND language='Python'"
    )
    require((row["rank"], row["weight"]) == (4, 12), "ranking period update is incorrect")
    require(store.one("SELECT COUNT(*) AS n FROM project_rankings WHERE project_id=42 AND period='2026-10-19' AND language='Python'")["n"] == 1,
            "ranking period update created a duplicate")
    return {"weights": observed, "period_update": {"rank": row["rank"], "weight": row["weight"], "rows": 1}}


def check_project_status_default(store):
    store.upsert_project(REPO, "manual")
    row = store.one("SELECT stage FROM projects WHERE id=42")
    require(row["stage"] == "discovered", "default project status is not discovered")
    return {"stage": row["stage"]}


def check_ranking_period_idempotency(store):
    store.upsert_project(REPO, "github_trending")
    store.conn.execute(
        "INSERT INTO discovery_runs(id,source,start_date,end_date,config,requested_by,created_at) VALUES(?,?,?,?,?,?,?)",
        ("run_period", "github_trending", "2026-10-19", "2026-10-19", "{}", "tester", "2026-10-19"),
    )
    pipeline = Pipeline(store, None)
    for rank in (1, 4):
        pipeline._record_ranking(42, "run_period", "github_trending", "2026-10-19", "Python", rank,
                                 "https://github.com/trending", {"rank": rank})
    row = store.one("SELECT rank, weight FROM project_rankings WHERE project_id=42 AND board='github_trending' AND period='2026-10-19' AND language='Python'")
    count = store.one("SELECT COUNT(*) AS n FROM project_rankings WHERE project_id=42 AND board='github_trending' AND period='2026-10-19' AND language='Python'")
    require((row["rank"], row["weight"], count["n"]) == (4, 12, 1), "ranking period is not idempotent")
    return {"rank": row["rank"], "weight": row["weight"], "rows": count["n"]}


def check_project_list_fields(store):
    store.upsert_project(REPO, "github_trending")
    store.conn.execute(
        "INSERT INTO discovery_runs(id,source,start_date,end_date,config,requested_by,created_at) VALUES(?,?,?,?,?,?,?)",
        ("run_list", "github_trending", "2026-10-19", "2026-10-19", "{}", "tester", "2026-10-19"),
    )
    store.conn.execute(
        "INSERT INTO project_rankings(project_id,run_id,board,period,language,rank,weight,source_url,evidence,captured_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (42, "run_list", "github_trending", "2026-10-19", "Python", 1, 15,
         "https://github.com/trending", "{}", "2026-10-19"),
    )
    store.conn.execute(
        "INSERT INTO contact_summaries(project_id,snapshot_id,class,source_mask,point_count,priority,rationale,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (42, None, "with_clue", "readme", 1, 100, "explicit", "2026-10-19"),
    )
    result = list_projects(store, {"page": ["1"], "page_size": ["10"], "sort": ["priority"]})
    item = result["items"][0]
    for key in ("uid", "stage", "ranking_rank", "ranking_weight", "contact_class", "contact_priority", "priority_score"):
        require(key in item, f"project list is missing {key}")
    require(item["uid"] == stable_project_uid(42), "project list UID is incorrect")
    require(item["stage"] == "discovered", "project list status is incorrect")
    require(item["ranking_rank"] == 1, "project list rank is incorrect")
    require(item["ranking_weight"] == 15, "project list ranking weight is incorrect")
    require(item["contact_class"] == "with_clue", "project list contact class is incorrect")
    require(item["contact_priority"] == 100, "project list contact priority is incorrect")
    require(item["priority_score"] == 115, "project list priority score is incorrect")
    return {key: item[key] for key in ("uid", "stage", "ranking_rank", "ranking_weight", "contact_class", "contact_priority", "priority_score")}



def run(out):
    checks = []
    with tempfile.TemporaryDirectory(prefix="week1-acceptance-") as temp_dir:
        cases = [
            ("uid_format", lambda store: check_uid_format(store)),
            ("legacy_uid_backfill", lambda store: check_legacy_uid_backfill(temp_dir)),
            ("duplicate_import_idempotency", lambda store: check_duplicate_import(store)),
            ("project_status_default", lambda store: check_project_status_default(store)),
            ("ranking_weights", lambda store: check_ranking_weights(store)),
            ("ranking_period_idempotency", lambda store: check_ranking_period_idempotency(store)),
            ("project_list_fields", lambda store: check_project_list_fields(store)),
        ]
        for index, (check_id, check) in enumerate(cases):
            path = Path(temp_dir) / f"check-{index}.db"
            store = Store(path)
            try:
                try:
                    checks.append({"id": check_id, "status": "passed", "evidence": check(store)})
                except Exception as error:
                    checks.append({"id": check_id, "status": "failed", "evidence": {"error": str(error)}})
            finally:
                store.close()
    summary = {
        "passed": sum(item["status"] == "passed" for item in checks),
        "failed": sum(item["status"] == "failed" for item in checks),
        "blocked": 0,
    }
    document = {
        "plan": "NextRouter-PM26.10.01-week1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "summary": summary,
    }
    output = Path(out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="artifacts/validation/week1-acceptance.json")
    args = parser.parse_args()
    summary = run(args.out)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
