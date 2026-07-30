# HugCivi Architecture

Last updated: 2026-07-09

HugCivi is a single-container personal archive service. The design assumes a Synology NAS or similar Docker host where large archived content lives on a durable filesystem mount and the application keeps only catalog, job, setting, and UI state in SQLite.

## System Shape

Runtime topology:

- FastAPI serves the HTML UI, API, static assets, media previews, and artifact downloads.
- SQLite at `/config/jobs.sqlite3` is the authoritative state store for jobs, settings, favorites, notes, library index rows, artifacts, and maintenance history.
- `/data` is the durable archive root. Model files, images, videos, audio, comics, workflows, sidecar metadata, and user-managed folders stay on the filesystem.
- Background work runs as in-process scheduler threads. HugCivi intentionally avoids Redis, Celery, Elasticsearch, or a second service for the current personal NAS target.
- ffmpeg, gallery-dl, yt-dlp, Deno, rclone, and the Hugging Face CLI paths are invoked only where the matching source type needs them.

The core split is:

```text
Browser
  -> FastAPI app/main.py
      -> SQLite app/db.py in /config/jobs.sqlite3
      -> /data archive tree
      -> download scheduler app/downloader.py
      -> internal job scheduler app/internal_jobs.py
      -> copy-only transfer helpers app/transfer.py
      -> library indexer thread in app/main.py
```

## Data Boundaries

Important paths:

| Path | Role |
| --- | --- |
| `/data` | Long-lived archive content. This is the volume to protect if the downloaded files are the priority. |
| `/data_remote` | Optional connected-folder root for copy-only `local_mount` transfer destinations. It is not a library root and must stay separate from `/data`. |
| `/config/jobs.sqlite3` | SQLite DB with jobs, settings, favorites, notes, library index, artifacts, and maintenance history. |
| `/config/downloads` | Temporary ZIP artifacts prepared for folder downloads. |
| `/config/media-cache` | Browser-playable video transcodes, poster images, and disposable card thumbnails. |
| `/config/rclone/rclone.conf` | Operator-managed rclone remote definitions for copy-only transfer targets. |
| `/config/transfer-manifests` | Copy-only transfer job manifests. Job logs stay in SQLite job log rows. |
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
| `app/transfer.py` | Copy-only transfer target validation, local mount path/copy safety, ComfyUI local mount folder checks, rclone destination/argv construction, HugCivi Receiver destination validation, policy sanitization, and output redaction helpers. |
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
| `library_folder_state` | Folder-level scan state for scoped library reindex progress and selected-folder index status payloads. |
| `maintenance_runs` | WAL, checkpoint, optimize, compact, backup, and media cache cleanup run history. |
| `transfer_targets` | Registered outbound transfer targets with `local_mount`, `rclone`, or `receiver` kind, base paths, enabled state, copy policy JSON, and Receiver URL/token when applicable. |
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
- heavy kinds can be gated by `INTERNAL_JOB_MAINTENANCE_MODE=immediate|window|paused`; window mode uses `INTERNAL_JOB_MAINTENANCE_START_HOUR` and `INTERNAL_JOB_MAINTENANCE_END_HOUR`
- current kinds:
  - `archive_zip`
  - `media_transcode`
  - `media_poster`
  - `media_thumbnail_backfill`
  - `library_reindex`
  - `transfer_copy`

Transfer jobs use the internal scheduler because they operate on files already under `/data`. Their visible source is `transfer`, while their `parsed_json` payload keeps the selected target ID, source path, and request snapshot.

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
   - `DOWNLOAD_STALL_TIMEOUT_SECONDS`
6. `downloader.run_job()` dispatches to the source handler.
7. The handler writes files under `/data`, sidecar metadata when appropriate, progress, logs, and final display metadata.

Provider keys are intentionally coarse for major services and host-based for generic/gallery-dl style inputs. This favors safety and rate-limit avoidance over maximum throughput.

Hugging Face snapshot downloads share the same queue controls: `QUEUE_PER_PROVIDER_LIMIT` limits concurrent Hugging Face jobs, and snapshot internal workers stay fixed at 1 so the provider limit does not multiply inside each job. `DOWNLOAD_STALL_TIMEOUT_SECONDS` controls both the HugCivi watchdog and the Hugging Face Hub response wait.

Civitai model archives are external download jobs. The handler resolves version metadata, merges API/rendered model-page details, records model body/version/file details, fetches tensor summary data when available, saves model-version example images before gallery images, and writes `_civitai_metadata.json` plus optional `_civitai_generation_metadata.json`. When no exact file selector or download URL is supplied, required Civitai component files are downloaded with the primary model file and recorded in `component_downloads`. Civitai `Workflows` model types whose primary file is an `Archive`/`Other` ZIP are classified under `/data/civitai/workflows/...`; the original ZIP is retained, and a verified JSON/PNG workflow entry is copied out as `workflow.json` plus `_workflow_metadata.json` so the workflow viewer can open it when possible. Refresh jobs target the existing folder and keep matching local files while updating sidecars, previews, and card metadata.

Civitai image page archives first use the public image API. If that endpoint returns an empty `items` list for a still-renderable image page, the downloader falls back to the rendered page's `__NEXT_DATA__` and JSON-LD metadata, preserving the original media URL, generation prompt, and model-version resources before writing `_civitai_image_metadata.json` and queueing resource jobs. The primary asset may be an image or a rendered-page video such as `.webm`.

ASMR.one work downloads are normal external download jobs in the `asmrone` provider bucket. The handler reads work and track metadata from `ASMRONE_API_BASE`, creates Unicode-safe local track paths, then downloads file bodies from each leaf track `mediaDownloadUrl` with `action=download`; `mediaStreamUrl` is intentionally not archived. Work cover URLs such as `mainCoverUrl` are downloaded separately as `cover.jpg` when available. Output defaults under `/data/asmr.one/...` and includes ASMR.one sidecars plus `_archive_metadata.json`. `_asmrone_manifest.json` records per-file status and partial failures so the library can recover an archive when at least one file downloaded. Image attachments that resolve to Cloudflare HTML/error-placeholder responses are discarded and recorded as failed entries rather than kept as broken local images.

## Internal Job Flow

Internal jobs protect the server from expensive user-triggered operations.

Folder ZIP:

- files are preflighted for `/data` scope, symlink escape, unsafe archive entries, max file count, optional source size limit, and optional free-space requirement
- folders create an `archive_zip` job via `/api/fs/download-jobs`
- files continue to use direct `/api/fs/download`
- direct `/api/fs/download` can still zip a folder as a compatibility path, but the browser UI uses the async job endpoint for folders
- output ZIPs live in `/config/downloads`

Media transcode:

- `/api/media/list` recognizes image, video, audio, and `.txt`/`.md`/`.markdown` document files
- audio items inherit the archive cover URL from the first local thumbnail image under the folder, so audio cards, strip thumbnails, and the audio player can show `cover.jpg` or another local illustration
- audio files are served directly through `/api/media/file` and do not create internal media jobs
- document files are read through `/api/media/text`, capped to a bounded preview size, and rendered as escaped text in the viewer; Markdown is not converted to HTML
- `/api/media/play` returns the original file if the browser can play it or a cached transcode already exists
- on cache miss it returns `202` with `job_required`
- `/api/media/transcode-jobs` creates a `media_transcode` job
- output MP4 files live in `/config/media-cache`

Poster generation:

- `/api/media/poster` serves images or cached posters
- on cache miss it returns `202` with `job_required`
- `/api/media/poster-jobs` creates a `media_poster` job
- output JPG files live in `/config/media-cache`

Card thumbnail generation:

- library/job cards use `/api/media/thumbnail?path=...` for image thumbnails instead of loading full-size originals
- the frontend delays card thumbnail requests until cards are in or near the viewport, uses API `thumbnail_ready` to run up to 10 cache-ready requests at a time, and keeps cold/generating thumbnail requests capped at 3 so first visits or existing file scans do not fan out 100 image generations at once
- thumbnails are generated lazily as small JPEG files under `/config/media-cache/thumbnails`
- thumbnail URLs include a file-version query token and cache-hit responses use long-lived private immutable `Cache-Control`
- thumbnail cache misses use ffmpeg behind the same media transcode semaphore that protects video work
- the library `썸네일 생성` action creates one `media_thumbnail_backfill` internal job for the selected folder, scans card representative images only, skips existing cache files, and defaults to 3 worker threads
- ordinary image thumbnail requests do not create one internal job row per image; the endpoint either serves a cache hit or generates the file during the request
- cache-hit media responses update cache access time where possible; quota cleanup removes least-recently-accessed files first
- `/api/media/cache` reports media cache totals by category, and `/api/media/cache/cleanup` runs TTL/quota cleanup or clears the thumbnail cache scope
- `MEDIA_VIDEO_PREVIEW_MODE` is currently a disabled-by-default policy/status setting for future preview/trickplay work; no preview job kind is started in this build
- `/api/fs/preview`, `/api/media/file`, and `/api/media/poster` remain available for compatibility and media-viewer/full-size use

Transfer copy:

- the context menu `전송` action opens a compact modal that reads registered targets from `/api/transfer/targets`
- the settings `전송/연결 폴더` pane starts with category tabs, then shows the selected category's registered targets and registration form; newly saved targets include optional `policy.category` metadata for UI grouping
- ComfyUI settings targets can store optional `policy.comfyui_mappings` from fixed HugCivi `stable-diffusion/<route>` prefixes to target destination subfolders; transfer preflight/jobs apply those mappings only when the request does not already provide a `destination_subpath`, Civitai resource transfer falls back to the standard mappings when a target is clearly rooted at `ComfyUI/models` but has no saved mapping, and Civitai model archives keep their model/version folder context when those mapped destinations are built
- for `local_mount` targets, `/api/transfer/targets/{target_id}/local-mount/tree` browses target-relative folders under `/data_remote/<target>` without exposing host absolute paths
- for ComfyUI-like `local_mount` targets, `/api/transfer/targets/{target_id}/comfyui/check` checks whether the mounted folder is a ComfyUI `models` root, a ComfyUI root with `models`, a single model folder, or a generic folder, then returns target-relative folder/mapping hints plus saved mapping destination health without exposing `/data_remote` absolute paths
- for HugCivi Receiver targets, `/api/transfer/targets/{target_id}/receiver/tree` proxies the Receiver `/api/browse` folder tree with the stored token so the browser can pick a mounted PC destination without seeing the token; disabled targets are rejected and returned Receiver paths are revalidated as destination-relative paths
- `/api/transfer/preflight` validates the selected `/data` source against the target policy and returns a destination preview plus estimated file/byte counts when available
- `/api/transfer/jobs` creates a `transfer_copy` internal job; the browser refreshes the shared job list and labels the source as `Transfer`
- Civitai image-page cards expose a `사용 리소스 전송` card action and context-menu flow; `/api/transfer/civitai-resources/preflight` reads `_civitai_image_metadata.json` Resources used model-version IDs, checks local jobs/sidecars, resolves each present resource to its primary local model file, applies saved ComfyUI mappings or standard model-root fallback mappings, preserves the source archive's model/version folder context in the destination, and previews per-file destinations before `/api/transfer/civitai-resources/jobs` queues one `transfer_copy` job per transferable file
- `/api/transfer/data-root/preflight` and `/api/transfer/data-root/jobs` are the settings-pane-only `/data` root clone flow for `local_mount` targets; they do not accept browser-controlled source paths and copy `/data` contents into the selected target/subfolder
- local mount targets copy with Python filesystem helpers to registered `/data_remote` target bases, using temp files and rename, skipping existing files by default, and recording target-relative manifest entries
- rclone targets use argv lists built from target policy only, using `RCLONE_CONFIG` and conservative `TRANSFER_*` defaults
- HugCivi Receiver targets create a remote receive job, upload matching files by HTTP, then mark the PC-side job done or failed; Receiver tokens are used only in headers and are not logged or copied into job payloads
- `app/main.py` runs the selected transfer backend through the internal job handler, writes the SQLite job log, and records a manifest under `/config/transfer-manifests`
- rclone credentials should remain in `/config/rclone/rclone.conf`; Receiver tokens live in the authenticated target editor and SQLite state

The UI polls job status and then swaps to the artifact URL when the job is done.

## Library Index

The library view uses a DB-backed incremental index when possible.

Indexer behavior:

- startup launches a background library indexer after `LIBRARY_INDEXER_START_DELAY_SECONDS`
- batches are controlled by `LIBRARY_INDEX_BATCH_SIZE`
- interval is controlled by `LIBRARY_INDEXER_INTERVAL_SECONDS`
- completed HugCivi-owned external download jobs, subscription downloads, and ComfyUI workflow imports call a single-target library index refresh for their completed `target_dir`, then invalidate the selected-folder live-page cache; this is not a filesystem watcher and does not scan the whole selected folder
- `/api/library/sync` performs a bounded scoped reconcile without clearing existing rows. The browser `갱신` button uses this path first so newly added or removed cards can be persisted in SQLite without immediately queueing a full selected-folder rebuild.
- `/api/library/reindex` queues or reuses an active `library_reindex` internal job for the same normalized scope; optional `path`, `source_group`, and `category` parameters scope the refresh to a selected folder/provider/category, `LIBRARY_REINDEX_BATCH_SIZE` controls each scan batch, and each batch writes indexed rows through one bulk SQLite upsert transaction
- scoped reindex records selected-folder progress in `library_folder_state`; selected-folder index responses may include this as additive `index_status.folder_state`
- `/api/library?mode=live` can force filesystem scan behavior
- selected-folder `mode=index` pages query SQLite only, including `source_group`/`category` filters, so indexed folders can return exact totals without a filesystem scan and unindexed scopes return fast `needs_refresh`/`refreshing` status instead of waiting on a live scan
- `/api/library?mode=live&path=...` explicitly scopes a live scan to the selected folder; completed selected-folder scans report `total_count` and `total_pages`, so ordinary folders show their full page list. The completed item list is reused by a short in-memory cache for page/sort navigation so page clicks do not repeat the same full folder scan. If the scan cannot complete within the internal path budget, totals stay unknown and the browser falls back to previous/current/next navigation.
- `/api/library` without `limit` or `page` keeps the legacy plain-array response
- `/api/library?limit=50&page=N&sort=...` returns a wrapper with `items`, `page`, `limit`, `total_count`/`total_pages` when known, and `has_next`; supported sort values are `az`, `za`, `date_desc`, `date_asc`, and `favorite`, with legacy `date` kept as a newest-first alias. Optional `source_group` values are `civitai`, `gallerydl`, `ytdlp`, `hitomi`, `asmrone`, `generic`, `huggingface`, `comfyui`, `media`, and `unknown`. Sort SQL uses stored `sort_title`, `path`, and `mtime_ns` columns so SQLite indexes can avoid expression sorting for common pages.
- the browser renders one 50-card page at a time and avoids rerendering the library during job polling unless completed/visible card metadata changed; manually queued reindex jobs are tracked by `job_id` through `/api/jobs/{id}` so completion is not dependent on the currently visible jobs page/filter
- `LIBRARY_WATCHER_ENABLED` is currently a disabled-by-default policy/status setting reported by `/api/library/watcher`; explicit reindex and the background indexer remain authoritative, especially on network storage.

The index row stores the same kind of payload the UI already expects in `payload_json`, plus lightweight searchable columns (`source`, `source_group`, `model_category`, `parent_path`, and `sort_title`) for source/category/path navigation. This is a pragmatic cache/index for a personal archive UI, not a full search engine.

Single-media yt-dlp/gallery-dl archives can enrich their library payload from the saved `*.info.json` sidecar, using the real media title and webpage URL for the card when the folder metadata only has a fallback slug. Multi-video channel or playlist folders keep folder-level titles so one child video title does not rename the whole archive.

Filesystem operations from the app update related path state:

- rename/move update job target prefixes, favorites, notes, and library index prefixes
- delete clears affected favorites, notes, target prefixes, and library index rows
- clearing job history resets stale library index rows so filesystem-backed cards can be restored from sidecars
- app-driven folder create/rename/move/delete, completed HugCivi download/import indexing, manual reindex, and job-history clear also invalidate the selected-folder live page cache
- external filesystem edits made outside HugCivi are eventually reflected by the indexer or live fallback; the sidebar folder-tree refresh action rereads `/data` directly without relying on cached DB rows

Civitai model cards can be restored from `_civitai_metadata.json` even after the original job row is gone. If `_civitai_generation_metadata.json` exists, media viewer metadata uses its selected example image and generation payload; otherwise it falls back to the model metadata sidecar and reconstructs the model-details panel from stored model/version fields.

## Folder Tree Navigation

Folder navigation is intentionally split between a bounded compatibility tree, lazy expansion, and bounded search. `/api/folders` remains the initial sidebar payload and uses `initial_folder_tree()` with `FOLDER_TREE_INITIAL_MAX_DEPTH=1`, so it only preloads direct root children and marks expandable rows with `has_children`/`children_loaded`. `/api/folders/children?path=...&limit=200&cursor=...` loads direct child folder rows for an expanded folder on demand, with `FOLDER_CHILDREN_MAX_LIMIT=500` as the per-request cap. `/api/folders/search?q=...&scope=...&limit=50` performs a bounded filesystem folder search under `/data` or the selected folder scope, caps results and visited folders, and returns `/data`-relative results.

Hitomi gallery downloads are archive leaves for Tree purposes. Direct children such as `/data/hitomi/<gallery>` and listing archive folders under `/data/hitomi/listings/<listing>` are reported as non-expandable without scanning their page files; a `_hitomi_metadata.json` sidecar also marks custom-target Hitomi archives as leaves.

The sidebar search box uses `/api/folders/search` first, so it can find matching folders that are not yet loaded in the sidebar tree. Selecting a result lazy-loads the required ancestors and sibling pages before scrolling the folder into view. The move destination picker still uses the lazy tree selection flow so destination changes stay explicit. If real archives need faster search than bounded filesystem scanning can provide, the optional folder index described in [Folder Tree Scaling Design 2026-07-06](folder-tree-scaling-design-2026-07-06.md) is the next step.

## API Groups

Main API groups:

| Group | Examples |
| --- | --- |
| Job management | `/api/jobs`, `/api/jobs/bulk`, `/api/jobs/{id}`, pause, resume, retry, delete, clear |
| YouTube subscriptions | `/api/subscriptions`, aggregate `/api/subscriptions/items`, `/api/subscriptions/{id}`, `/api/subscriptions/{id}/items`, create/update/delete, manual `/check`, item `/queue`, `/skip`, `/retry` |
| Settings | `/settings` |
| Folders/library | `GET/POST /api/folders`, `GET /api/folders/children`, `GET /api/folders/search`, `/api/library`, paged `/api/library?limit=50&page=N`, `/api/library/sync`, `/api/library/reindex`, `/api/library/watcher` |
| Filesystem operations | `/api/fs/rename`, `/api/fs/move`, `/api/fs/delete`, `/api/fs/properties`, `/api/fs/note`, `/api/fs/download*` |
| Transfer | `/api/transfer/targets`, `/api/transfer/targets/{target_id}/local-mount/tree`, `/api/transfer/targets/{target_id}/receiver/tree`, `/api/transfer/targets/{target_id}/comfyui/check`, `/api/transfer/preflight`, `/api/transfer/jobs`, `/api/transfer/civitai-resources/preflight`, `/api/transfer/civitai-resources/jobs`, `/api/transfer/data-root/preflight`, `/api/transfer/data-root/jobs` |
| Media | `/api/media/list`, `/api/media/archive`, `/api/media/file`, `/api/media/thumbnail`, `/api/media/thumbnail-jobs`, `/api/media/cache`, `/api/media/cache/cleanup`, `/api/media/video-preview`, `/api/media/play`, `/api/media/poster`, subtitle and async job endpoints |
| Workflows | `/api/workflows/import`, `/api/workflows/view`, `/api/workflows/preview` |
| Hitomi listing confirm | `/api/hitomi/listing/{job_id}`, `/api/hitomi/listing/{job_id}/queue` |
| Civitai health/refresh | `/api/civitai/resource-health`, `/api/civitai/refresh` |
| Storage | `/api/storage`, `/api/storage/archive-usage` |
| Addon package | `/api/addon/chrome-extension` |
| DB maintenance | `/api/maintenance/db/wal`, checkpoint, optimize, compact, backup |

`/api/jobs` keeps the legacy array response when called without pagination parameters. Cursor pagination returns a wrapper with `jobs` and `next_cursor`. Numbered pagination with `limit` and `page` returns a wrapper with `jobs`, `page`, `limit`, `total_count`, `total_pages`, `active_source`, and `source_counts`; the browser job table uses this mode with 50 rows per page. `source=<provider>` filters the visible job list server-side while `source_counts` keeps the `ALL` and provider filter buttons in sync with the full job history.

Civitai resource health accepts either model-version IDs or model archive components. Model-version checks report presence from completed jobs and Civitai sidecars. Component checks require a `/data` path and compare requested component filenames against local files in that archive folder.

For a feature-by-feature map from product behavior to code files and tests, see [Feature and Code Map](feature-code-map.md).

## Safety Invariants

Important invariants for future changes:

- `/data` is the only archive root. User-supplied paths must be resolved through safe path helpers.
- `/data` root itself is not downloadable as a ZIP and cannot be renamed, moved, or deleted.
- Symlink folders are not accepted as archive roots. ZIP preflight rejects symlinks that leave `/data`.
- Internal jobs must not be enqueued into the external download scheduler.
- Transfer is copy-only outbound work: jobs must use registered target IDs, validated `/data` source paths, and target allowed source prefixes. Browser payloads must not include modes, raw host paths, remotes, credentials, or command arguments.
- Transfer target rows store copy policy and local mount paths, rclone remote names, or Receiver URL/token. `/data_remote` must not overlap `/data`; rclone credentials belong in `/config/rclone/rclone.conf`; Receiver tokens and ComfyUI folder-check payloads must not return secrets or absolute host paths.
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
- Actual local-filesystem watcher and opt-in video preview/trickplay workers, using the policy/status settings already exposed in the current build.
- More explicit artifact retention policy rows.
- A multi-process worker only if the single-container model becomes a real operational constraint.
