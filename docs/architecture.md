# HugCivi Architecture

Last updated: 2026-07-03

HugCivi is a single-container personal archive service. The design assumes a Synology NAS or similar Docker host where large archived content lives on a durable filesystem mount and the application keeps only catalog, job, setting, and UI state in SQLite.

## System Shape

Runtime topology:

- FastAPI serves the HTML UI, API, static assets, media previews, and artifact downloads.
- SQLite at `/config/jobs.sqlite3` is the authoritative state store for jobs, settings, favorites, notes, library index rows, artifacts, and maintenance history.
- `/data` is the durable archive root. Model files, images, videos, audio, comics, workflows, sidecar metadata, and user-managed folders stay on the filesystem.
- Background work runs as in-process scheduler threads. HugCivi intentionally avoids Redis, Celery, Elasticsearch, or a second service for the current personal NAS target.
- ffmpeg, gallery-dl, yt-dlp, Deno, and the Hugging Face CLI paths are invoked only where the matching source type needs them.

The core split is:

```text
Browser
  -> FastAPI app/main.py
      -> SQLite app/db.py in /config/jobs.sqlite3
      -> /data archive tree
      -> download scheduler app/downloader.py
      -> internal job scheduler app/internal_jobs.py
      -> library indexer thread in app/main.py
```

## Data Boundaries

Important paths:

| Path | Role |
| --- | --- |
| `/data` | Long-lived archive content. This is the volume to protect if the downloaded files are the priority. |
| `/config/jobs.sqlite3` | SQLite DB with jobs, settings, favorites, notes, library index, artifacts, and maintenance history. |
| `/config/downloads` | Temporary ZIP artifacts prepared for folder downloads. |
| `/config/media-cache` | Browser-playable video transcodes and poster images. |
| `/config/startup.env` | Startup setting overrides that must affect the next container boot, currently gallery-dl auto-update. |
| `/config/backups` | Online SQLite backup output from the maintenance API. |

The archive content is not stored inside SQLite. SQLite stores metadata, paths, job state, and cached library payloads. This keeps the DB suitable for large checkpoints, LoRA collections, comics, image folders, and video archives.

## Module Map

| File | Responsibility |
| --- | --- |
| `app/main.py` | FastAPI app, lifespan, API routes, HTML rendering, local file operations, media viewer, ZIP/media internal job handlers, library indexer. |
| `app/db.py` | SQLite connection, schema migration, settings, job CRUD, library index persistence, maintenance operations. |
| `app/downloader.py` | External download scheduler and source handlers for Hugging Face, Civitai, Hitomi, ASMR.one, gallery-dl, yt-dlp, generic files, and ComfyUI workflows. |
| `app/internal_jobs.py` | Lightweight in-process job runner for server-local expensive work. |
| `app/subscriptions.py` | YouTube subscription defaults, API payload helpers, source URL normalization, manual/scheduled yt-dlp discovery, independent subscription check scheduler, and independent subscription download worker. |
| `app/defaults.py` | Shared default values for queue, cache, media, archive, and test-visible limits. |
| `app/parsers.py` | Input parsing and source routing. |
| `app/models.py` | `ParsedDownload` model and source type definition. |
| `app/metadata.py` | Hugging Face and Civitai classification helpers. |
| `app/workflows.py` | ComfyUI workflow extraction, storage, and viewer payloads. |
| `app/utils.py` | Shared safety and formatting helpers such as `safe_join`. |
| `app/ytdlp_sites.py` | Host preference list for yt-dlp routing. |
| `app/templates/index.html` | Main browser application and client-side polling flows. |
| `app/static/style.css` | UI styling. |
| `app/static/manifest.webmanifest`, `app/static/sw.js` | PWA install surface and static asset cache. |
| `chrome-extension/` | Manifest V3 convenience extension that submits current tab or typed input to HugCivi. |

## Lifespan

FastAPI startup and shutdown are centralized through `lifespan` in `app/main.py`.

Startup performs:

- `db.init_db()`
- route folder creation under `/data`
- stale ZIP and media cache cleanup
- internal job handler registration
- external download scheduler start
- internal job scheduler start
- subscription check scheduler start
- library indexer start

Shutdown stops:

- library indexer
- internal job scheduler
- subscription check scheduler
- external download scheduler

Running download subprocesses are not force-killed during ordinary app shutdown. The shutdown path prevents new scheduling and lets existing job control paths handle pause, cancel, and delete semantics.

## Database Schema

SQLite tables created by `db.init_db()`:

| Table | Purpose |
| --- | --- |
| `jobs` | Shared job table for external downloads and internal jobs. |
| `settings` | UI-saved settings and secrets. Environment variables remain fallback/default inputs. |
| `favorites` | Favorite paths for library cards. |
| `item_notes` | User notes keyed by `/data` relative path. |
| `job_artifacts` | Result files generated by internal jobs. |
| `job_content_refs` | Links from jobs to source content paths. |
| `library_items` | DB-backed library index rows. Each row stores a serialized UI payload in `payload_json`. |
| `library_scan_state` | Cursor/status values for incremental indexing and cached storage-usage scan state. |
| `maintenance_runs` | WAL, checkpoint, optimize, compact, and backup run history. |
| `subscriptions` | YouTube subscription sources and scheduling metadata. Manual `check now` and the subscription check scheduler update these rows. |
| `subscription_items` | Discovered/queued/download state model for YouTube subscription media. Manual/scheduled discovery and the subscription download worker update these rows. |

The `jobs` table is migrated additively. Current structural columns include:

- `job_kind`: `download` for external downloads, otherwise an internal job kind.
- `artifact_path`, `artifact_url`, `artifact_expires_at`: result file metadata for internal jobs.
- model/library display columns such as `model_title`, `model_category`, `model_type`, `base_model`, `file_format`, `precision`, `thumbnail_url`, and `metadata_json`.

`settings` may contain tokens, passwords, cookie paths, authenticated proxy URLs, and extra options. A DB backup is therefore also a credential backup.

## Job Model

HugCivi intentionally keeps one visible job list while separating execution schedulers.

Common statuses:

```text
queued
running
paused
pausing
canceling
canceled
deleting
failed
done
```

`canceling` and `canceled` remain valid control/restart states even though the current UI does not expose a direct cancel button.

External download jobs:

- `job_kind='download'`
- created by `db.create_job()`
- scheduled by `app/downloader.py`
- limited by global download concurrency, per-provider concurrency, and provider cooldown
- source types: `huggingface`, `civitai`, `generic`, `comfyui`, `hitomi`, `asmrone`, `gallerydl`

Internal jobs:

- created by `db.create_internal_job()`
- scheduled by `app/internal_jobs.py`
- limited by `INTERNAL_JOB_MAX_CONCURRENT` with default `2`
- current kinds:
  - `archive_zip`
  - `media_transcode`
  - `media_poster`

Restart handling is conservative. `running` jobs are requeued, `pausing` becomes `paused`, `canceling` becomes `canceled`, and `deleting` rows are deleted or cleaned up according to their job family.

## External Download Flow

1. User submits a single URL/input, bulk input, or derived child job.
2. `app/parsers.py` produces a `ParsedDownload`.
3. `db.create_job()` creates a `download` job row.
4. `downloader.enqueue_job()` adds the job ID to an in-memory pending list.
5. The scheduler checks:
   - `MAX_CONCURRENT_DOWNLOADS`
   - `QUEUE_PER_PROVIDER_LIMIT`
   - `QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS`
   - `QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS`
6. `downloader.run_job()` dispatches to the source handler.
7. The handler writes files under `/data`, sidecar metadata when appropriate, progress, logs, and final display metadata.

Provider keys are intentionally coarse for major services and host-based for generic/gallery-dl style inputs. This favors safety and rate-limit avoidance over maximum throughput.

## Internal Job Flow

Internal jobs protect the server from expensive user-triggered operations.

Folder ZIP:

- files are preflighted for `/data` scope, symlink escape, unsafe archive entries, max file count, optional source size limit, and optional free-space requirement
- folders create an `archive_zip` job via `/api/fs/download-jobs`
- files continue to use direct `/api/fs/download`
- direct `/api/fs/download` can still zip a folder as a compatibility path, but the browser UI uses the async job endpoint for folders
- output ZIPs live in `/config/downloads`

Media transcode:

- `/api/media/play` returns the original file if the browser can play it or a cached transcode already exists
- on cache miss it returns `202` with `job_required`
- `/api/media/transcode-jobs` creates a `media_transcode` job
- output MP4 files live in `/config/media-cache`

Poster generation:

- `/api/media/poster` serves images or cached posters
- on cache miss it returns `202` with `job_required`
- `/api/media/poster-jobs` creates a `media_poster` job
- output JPG files live in `/config/media-cache`

The UI polls job status and then swaps to the artifact URL when the job is done.

## Library Index

The library view uses a DB-backed incremental index when possible.

Indexer behavior:

- startup launches a background library indexer after `LIBRARY_INDEXER_START_DELAY_SECONDS`
- batches are controlled by `LIBRARY_INDEX_BATCH_SIZE`
- interval is controlled by `LIBRARY_INDEXER_INTERVAL_SECONDS`
- `/api/library/reindex` can reset and scan a larger batch
- `/api/library?mode=live` can force filesystem scan behavior

The index row stores the same kind of payload the UI already expects, rather than normalizing every display field into separate columns. This is a pragmatic cache/index for a personal archive UI, not a full search engine.

Single-media yt-dlp/gallery-dl archives can enrich their library payload from the saved `*.info.json` sidecar, using the real media title and webpage URL for the card when the folder metadata only has a fallback slug. Multi-video channel or playlist folders keep folder-level titles so one child video title does not rename the whole archive.

Filesystem operations from the app update related path state:

- rename/move update job target prefixes, favorites, notes, and library index prefixes
- delete clears affected favorites, notes, target prefixes, and library index rows
- external filesystem edits made outside HugCivi are eventually reflected by the indexer or live fallback

## API Groups

Main API groups:

| Group | Examples |
| --- | --- |
| Job management | `/api/jobs`, `/api/jobs/bulk`, `/api/jobs/{id}`, pause, resume, retry, delete, clear |
| YouTube subscriptions | `/api/subscriptions`, aggregate `/api/subscriptions/items`, `/api/subscriptions/{id}`, `/api/subscriptions/{id}/items`, create/update/delete, manual `/check`, item `/queue`, `/skip`, `/retry` |
| Settings | `/settings` |
| Folders/library | `GET/POST /api/folders`, `/api/library`, `/api/library/reindex` |
| Filesystem operations | `/api/fs/rename`, `/api/fs/move`, `/api/fs/delete`, `/api/fs/properties`, `/api/fs/note`, `/api/fs/download*` |
| Media | `/api/media/list`, `/api/media/archive`, `/api/media/file`, `/api/media/play`, `/api/media/poster`, subtitle and async job endpoints |
| Workflows | `/api/workflows/import`, `/api/workflows/view`, `/api/workflows/preview` |
| Hitomi listing confirm | `/api/hitomi/listing/{job_id}`, `/api/hitomi/listing/{job_id}/queue` |
| Civitai resource health | `/api/civitai/resource-health` |
| Storage | `/api/storage`, `/api/storage/archive-usage` |
| Addon package | `/api/addon/chrome-extension` |
| DB maintenance | `/api/maintenance/db/wal`, checkpoint, optimize, compact, backup |

`/api/jobs` keeps the legacy array response when called without a cursor. Cursor pagination returns a wrapper with `jobs` and `next_cursor`.

For a feature-by-feature map from product behavior to code files and tests, see [Feature and Code Map](feature-code-map.md).

## Safety Invariants

Important invariants for future changes:

- `/data` is the only archive root. User-supplied paths must be resolved through safe path helpers.
- `/data` root itself is not downloadable as a ZIP and cannot be renamed, moved, or deleted.
- Symlink folders are not accepted as archive roots. ZIP preflight rejects symlinks that leave `/data`.
- Internal jobs must not be enqueued into the external download scheduler.
- External download jobs must keep `job_kind='download'`.
- YouTube subscription state must stay separate from the visible `jobs` queue unless a future compatibility bridge is explicitly added.
- DB migrations should be additive unless a separate migration plan and backup path exist.
- Credential settings are returned to the authenticated settings editor in plaintext for runtime editing. Job payloads, logs, and non-settings APIs should still redact or avoid credentials.
- Do not run frequent `PRAGMA optimize` from hot DB paths. Keep it in maintenance or migration flows.

## Scaling Position

This architecture is enough for a personal NAS archive with large files and many folders. The likely bottlenecks are disk I/O, network rate limits, ffmpeg CPU use, and very large filesystem scans, not SQLite row count in ordinary use.

Possible future moves if real usage proves the need:

- SQLite FTS for catalog search.
- A duplicate internal job coalescer for repeated ZIP/transcode requests on the same source.
- A maintenance UI for backup/checkpoint/compact controls.
- More explicit artifact retention policy rows.
- A multi-process worker only if the single-container model becomes a real operational constraint.
