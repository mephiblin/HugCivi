---
name: hugcivi-database-jobs
description: Stable rules for HugCivi SQLite schema, migrations, settings, secrets, external download jobs, internal jobs, restart handling, and maintenance APIs.
---

# HugCivi Database And Jobs Skill

Use this before changing `app/db.py`, job status flow, settings, scheduler behavior, maintenance APIs, or internal jobs.

## Schema Rules

- Use additive SQLite migrations in `db.init_db()` and `ensure_job_columns()`.
- Avoid destructive migrations without a backup and rollback story.
- Treat `/config/jobs.sqlite3` backups as credential backups.
- Keep `_DB_LOCK` assumptions in mind; this app is one process with in-process threads.
- Keep `settings` compatible with environment variable fallback.
- The authenticated settings editor intentionally receives saved credential values in plaintext for runtime editing. Do not expose those values in job payloads, logs, public APIs, or unauthenticated templates.

## Job Rules

- External remote downloads use `job_kind='download'` and `db.create_job()`.
- Server-local expensive work uses `db.create_internal_job()` and `app/internal_jobs.py`.
- Do not enqueue internal jobs into `app/downloader.py`.
- Do not enqueue download jobs into `app/internal_jobs.py`.
- Valid lifecycle states include `queued`, `running`, `paused`, `pausing`, `canceling`, `canceled`, `deleting`, `failed`, `done`.
- Restart handling must stay conservative: requeue running downloads, pause pausing jobs, cancel canceling jobs, and clean deleting jobs according to job family.

## Code To Read

```bash
sed -n '1,260p' app/db.py
sed -n '1,220p' app/internal_jobs.py
rg -n "job_kind|create_internal_job|is_internal_job|register_internal_job_handlers|start_workers|stop_workers|resume_incomplete_jobs|maintenance" app
```

## Required Test Focus

Run:

```bash
python3 -m pytest -q -p no:cacheprovider tests/test_review_fixes.py tests/test_downloader_runtime.py
```

For settings/credential changes, include direct visibility tests for the settings editor and redaction tests for job/log/non-settings surfaces. For migrations, include tests that initialize old or partial schemas when practical.
