# Week 1 UID and Data Model Acceptance Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce repeatable evidence that the Week 1 requirements are complete: stable project UID, historical UID backfill, duplicate-import idempotency, explicit project status, and auditable ranking fields.

**Architecture:** Keep the existing schema 9 and business behavior as the source of truth. Add a focused acceptance test module that creates isolated SQLite stores, exercises migration and import paths, and emits a machine-readable report. Validate the deployed service separately through public health, auth-options, and OpenAPI endpoints; protected project data remains a login-gated verification item.

**Tech Stack:** Python 3.11+, standard-library `unittest`, SQLite, existing `worker.db`, `worker.pipeline`, `worker.projects`, deployed HTTPS API.

---

### Task 1: Add the Week 1 acceptance test suite

**Files:**
- Create: `tests/test_week1_acceptance.py`
- Reference: `worker/db.py`, `worker/projects.py`, `worker/pipeline.py`

- [ ] **Step 1: Write isolated tests for UID and status requirements**

Create tests that:

```python
store = Store(temp_path)
store.upsert_project(repo, "manual")
row = store.one("SELECT uid, uid_version, stage FROM projects WHERE id=1")
self.assertRegex(row["uid"], r"^prj_[a-f0-9]{32}$")
self.assertEqual(row["uid_version"], 1)
self.assertEqual(row["stage"], "discovered")
self.assertEqual(
    store.one("SELECT uid FROM projects WHERE id=1")["uid"],
    store.one("SELECT uid FROM projects WHERE id=1")["uid"],
)
```

Add a migration test that creates a legacy `projects` table without `uid`, inserts project ID 42, sets `schema_version` to 1, opens `Store`, and asserts the migrated UID equals `stable_project_uid(42)` and is unique.

Add a duplicate import test that calls `upsert_project()` twice with the same GitHub numeric ID and changed metadata, then asserts one project row, one UID, and one source row for the same source.

- [ ] **Step 2: Run only the new tests to establish the baseline**

Run:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m unittest tests.test_week1_acceptance -v
```

Expected: PASS against the existing schema 9 implementation. If a test exposes a real contract gap, keep the test and fix the smallest production surface in the next step.

- [ ] **Step 3: Add ranking and duplicate-period tests**

Exercise `Pipeline._record_ranking()` with ranks 1, 3, 7, and 11 and assert weights 15, 12, 9, and 5. Insert the same project/board/period/language twice with a changed rank and assert the unique constraint updates one row rather than creating a duplicate.

- [ ] **Step 4: Add project-list acceptance assertions**

Insert a project, ranking, and contact summary, call `list_projects()` with `sort=priority`, and assert the returned record includes `uid`, `stage`, `ranking_rank`, `ranking_weight`, `contact_class`, and a computed `priority_score`.

- [ ] **Step 5: Run the focused suite**

Run:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m unittest tests.test_week1_acceptance -v
```

Expected: all Week 1 acceptance tests pass.

- [ ] **Step 6: Commit if a repository is available**

Run:

```powershell
git status --short
```

If the directory is a Git worktree, commit the test file with:

```powershell
git add tests/test_week1_acceptance.py
git commit -m "test: add week one data model acceptance"
```

If it is not a Git repository, record that no commit is possible and continue with filesystem evidence.

### Task 2: Add a machine-readable Week 1 acceptance runner

**Files:**
- Create: `scripts/validation/week1_acceptance.py`
- Create: `artifacts/validation/week1-acceptance.json`

- [ ] **Step 1: Define the report contract**

The script must create a fresh temporary SQLite database, run the same acceptance checks through public functions, and write JSON with:

```json
{
  "plan": "NextRouter-PM26.10.01-week1",
  "generated_at": "ISO-8601 UTC",
  "checks": [
    {"id": "uid_format", "status": "passed", "evidence": {}},
    {"id": "legacy_uid_backfill", "status": "passed", "evidence": {}},
    {"id": "duplicate_import_idempotency", "status": "passed", "evidence": {}},
    {"id": "project_status_default", "status": "passed", "evidence": {}},
    {"id": "ranking_weights", "status": "passed", "evidence": {}},
    {"id": "ranking_period_idempotency", "status": "passed", "evidence": {}},
    {"id": "project_list_fields", "status": "passed", "evidence": {}}
  ],
  "summary": {"passed": 7, "failed": 0, "blocked": 0}
}
```

- [ ] **Step 2: Implement fail-closed execution**

Run checks sequentially, catch assertion/SQLite errors per check, mark that check `failed`, and exit nonzero if any check fails. Do not use real GitHub credentials or contact external services.

- [ ] **Step 3: Run the report generator**

Run:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python scripts/validation/week1_acceptance.py --out artifacts/validation/week1-acceptance.json
```

Expected: exit code 0, seven passed checks, zero failed checks.

- [ ] **Step 4: Inspect the generated report**

Run:

```powershell
Get-Content artifacts/validation/week1-acceptance.json -Raw | ConvertFrom-Json | Select-Object plan,summary
```

Expected: plan is `NextRouter-PM26.10.01-week1`; summary shows `passed=7`, `failed=0`, `blocked=0`.

### Task 3: Run regression and deployment-contract verification

**Files:**
- Modify: `docs/NextRouter-PM26.10.01实施审计与验收-20261016.md` only if the new evidence needs a dated addendum
- Create: `docs/NextRouter-PM26.10.01第一周验收-20261027.md`

- [ ] **Step 1: Run the complete Python suite**

Run from `GitHub_Worker`:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m unittest discover -s tests -q
python -m compileall -q worker main.py
```

Expected: 375 tests pass, zero failures, compileall exits 0.

- [ ] **Step 2: Check public deployed endpoints**

Run:

```powershell
curl.exe -fsS https://ee06e589.opendeploy.site/healthz
curl.exe -fsS https://ee06e589.opendeploy.site/auth/options
curl.exe -fsS https://ee06e589.opendeploy.site/openapi.json
```

Expected: health returns `{"ok":true}`; auth options show password login enabled and public registration disabled; OpenAPI includes project list, project rankings, discovery runs, and status endpoints.

- [ ] **Step 3: Write the dated acceptance report**

Document:

- local isolated acceptance results;
- full regression and compile results;
- deployed public endpoint results;
- the fact that project counts and historical 930-project data require an authenticated session;
- the fact that no real GitHub token, author contact, or Credits write was used;
- remaining operator action: log in and verify the protected project list/count against the historical data source.

- [ ] **Step 4: Final verification**

Run:

```powershell
python scripts/validation/week1_acceptance.py --out artifacts/validation/week1-acceptance.json
python -m unittest discover -s tests -q
```

Expected: acceptance report exits 0 and the full suite remains green.
