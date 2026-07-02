# HugCivi Development Guide

Last updated: 2026-07-02

This document is for changing HugCivi without breaking the personal NAS archive assumptions. The current implementation is intentionally simple: one FastAPI process, SQLite, mounted folders, and in-process background schedulers.

## Local Setup

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Run the app locally:

```bash
APP_PASSWORD=dev-password-that-is-long \
DATA_ROOT="$PWD/data" \
DB_PATH="$PWD/config/jobs.sqlite3" \
DOWNLOAD_ARCHIVE_DIR="$PWD/config/downloads" \
MEDIA_CACHE_DIR="$PWD/config/media-cache" \
uvicorn app.main:app --host 0.0.0.0 --port 8088 --reload
```

For Docker-based local development:

```bash
mkdir -p data config
APP_PASSWORD=dev-password-that-is-long docker compose up -d --build
```

Do not use the development DB for production data unless you intentionally bind the same `/data` and `/config` paths.

## Repository Layout

```text
app/
  main.py            FastAPI routes, lifespan, UI integration, local file/media/internal job handlers
  db.py              SQLite schema, job/settings/favorites/notes/index/maintenance persistence
  defaults.py        Shared defaults for queues, archive, media, scan, and log limits
  downloader.py      External download scheduler and source handlers
  internal_jobs.py   Internal job scheduler for ZIP/transcode/poster work
  parsers.py         Input parsing and source routing
  models.py          ParsedDownload model
  metadata.py        Hugging Face and Civitai classification helpers
  workflows.py       ComfyUI workflow extraction and viewer helpers
  utils.py           Shared safety helpers
  ytdlp_sites.py     Host preference list for yt-dlp routing
  templates/         Jinja HTML shell with client-side app logic
  static/            CSS, PWA manifest, service worker, icons
chrome-extension/    Manifest V3 extension and installable addon package
tests/               Pytest coverage for parsers, queue/runtime, APIs, media, library, and safety fixes
docs/                Design, operation, review, and reference documents
```

When you do not know where to start, use [Feature and Code Map](feature-code-map.md). It maps product features to backend files, frontend functions, data tables/artifacts, and tests.

## Verification Commands

Fast checks:

```bash
python3 -m py_compile app/main.py app/db.py app/downloader.py app/internal_jobs.py
node --check chrome-extension/shared.js
node --check chrome-extension/background.js
node --check chrome-extension/popup.js
node -e "JSON.parse(require('fs').readFileSync('chrome-extension/manifest.json','utf8'))"
git diff --check
```

Full Python test suite:

```bash
python -m pytest -q
```

The suite currently covers parser routing, bulk input, Hitomi listing discovery/confirm, queue behavior, DB maintenance helpers, internal jobs, archive safety, media job artifacts, library indexing, PWA assets, and selected filesystem safety regressions.

Frontend JavaScript currently lives inside `app/templates/index.html`. For syntax checks, extract the script carefully or use a browser/dev-server test path. Do not treat template rendering as proven by a plain JavaScript parser unless Jinja placeholders are handled.

## Coding Principles

Prefer local patterns over new frameworks:

- Use `safe_join`, `relative_data_path`, `existing_data_path`, and nearby helpers for filesystem work.
- Keep `/data` content on the filesystem; do not put large binary content in SQLite.
- Use additive SQLite migrations in `db.init_db()` and `ensure_job_columns()`.
- Keep external downloads and internal expensive operations in separate schedulers.
- Keep startup/shutdown work in the FastAPI lifespan path.
- Keep long CPU/I/O work out of HTTP request handlers.
- Preserve the existing UI response shapes unless the caller has an explicit versioned/new mode.

Avoid hidden destructive behavior:

- Do not delete archive content except through explicit user actions or scoped cache cleanup.
- Do not rewrite unrelated files or revert existing dirty work.
- Do not expose saved secret values in API responses or templates.
- Do not add automatic network-heavy behavior at startup.

## Adding a New Download Source

Use this path when the work downloads remote content into `/data`.

1. Extend parsing in `app/parsers.py`.
2. Add or reuse fields in `ParsedDownload`.
3. Implement the source handler in `app/downloader.py`.
4. Dispatch from `downloader.run_job()`.
5. Ensure `provider_key_for_parsed()` gives a safe concurrency bucket.
6. Write sidecar metadata if the library UI needs durable source details.
7. Add parser tests and at least one handler or metadata test with network calls mocked.

External download jobs must remain `job_kind='download'` and must be created through `db.create_job()`.

## Adding a New Internal Job

Use this path when the work is local CPU or disk I/O, such as ZIP preparation, ffmpeg work, preview generation, or index maintenance.

1. Define a stable job kind constant in `app/main.py` or a focused module.
2. Register the handler in `register_internal_job_handlers()`.
3. Create rows with `db.create_internal_job()`.
4. Enqueue with `internal_jobs.enqueue_job()`.
5. In the handler, update progress and call `internal_jobs.check_job_control(job_id)` during long loops.
6. Store outputs in a safe cache/artifact location.
7. Record `job_artifacts` and `job_content_refs` when the result should be traceable.
8. Add tests for queue separation, artifact recording, and cancellation/pause-sensitive long loops where applicable.

Internal jobs must not be added to the external downloader queue.

## Database Changes

Rules:

- Default DB path is `/config/jobs.sqlite3`; `DB_PATH` overrides it.
- Add tables with `CREATE TABLE IF NOT EXISTS`.
- Add job columns through `ensure_job_columns()`.
- Avoid non-additive migrations unless the migration has a rollback and backup story.
- Keep `settings` behavior compatible with environment variable fallback.
- Remember `_DB_LOCK` serializes DB access inside the process.
- Keep `PRAGMA optimize`, `VACUUM`, checkpoint, and backup inside maintenance flows.

Current important tables:

- `jobs`
- `settings`
- `favorites`
- `item_notes`
- `job_artifacts`
- `job_content_refs`
- `library_items`
- `library_scan_state`
- `maintenance_runs`

## API Compatibility

The browser app relies on current response shapes.

Important compatibility points:

- `/api/jobs` without cursor returns an array.
- `/api/jobs` with cursor returns a wrapper containing `jobs` and `next_cursor`.
- `/api/fs/download-jobs` returns direct file download info for files and a job for folders.
- `/api/media/play` and `/api/media/poster` may return `202` JSON on cache miss.
- Job action endpoints must route by `job_kind`.

When adding a new API response field, prefer additive fields. When changing a shape, keep a compatibility mode or update the frontend in the same change.

## Frontend Notes

The current UI is a single Jinja-rendered page with client-side JavaScript.

Keep these flows aligned:

- job list polling and job controls
- local file browser and context menus
- media viewer readiness and polling
- Hitomi listing confirm modal
- settings panes and `/settings` form field names
- library cards and favorites/notes

The UI should reflect background work as pending/running/done rather than blocking on a long HTTP request.

## Chrome Extension Notes

The extension in `chrome-extension/` is a convenience remote, not a replacement for the web UI.

Key files:

- `manifest.json`: MV3 metadata, permissions, icons, and `Alt+Shift+H` command.
- `shared.js`: settings normalization, Basic Auth, `/api/jobs/bulk`, `/api/jobs`, progress helpers.
- `background.js`: command handling, active tab URL submission, notifications and badge state.
- `popup.html`, `popup.css`, `popup.js`: settings, typed/current-tab submission, and recent job progress.

The web UI exposes the package through `/api/addon/chrome-extension` and the top-right `애드온` button. The Docker image includes `chrome-extension/`; if the package disappears in production, check the Dockerfile copy step and `HUGCIVI_CHROME_EXTENSION_DIR`.

## Testing Focus

High-value tests for this project:

- path traversal and symlink escape prevention
- restart behavior for queued/running/pausing/canceling/deleting jobs
- queue limit and provider cooldown behavior
- secret redaction
- DB migration compatibility with old rows
- filesystem operation hooks for jobs/favorites/notes/library index
- cache and artifact path validation
- parser regressions for source routing
- Civitai and Hitomi child job deduplication/capping

Prefer tests that avoid real network calls. Mock subprocesses, HTTP responses, and filesystem roots.

## Release Flow

For a normal code change:

1. Run the verification commands.
2. Commit only the intended files.
3. Push only when explicitly requested.
4. Build/deploy only when explicitly requested.

For production upgrades:

- Back up `/config/jobs.sqlite3`.
- Keep `/data` and `/config` bind mounts unchanged.
- Stop the old container before starting the new one.
- Do not run old and new containers against the same DB at the same time.
