# HugCivi Feature and Code Map

Last updated: 2026-07-02

This document is the first stop for a human or LLM developer who needs to change HugCivi. It maps the product surface to the files, functions, state, and tests that usually need to move together.

## How To Use This Document

1. Find the feature in the feature map.
2. Read the listed code files before editing.
3. Check the data and safety notes for that feature.
4. Run the listed tests or the broader verification set.
5. Update this document when a feature, endpoint, storage location, or test responsibility changes.

## Repository Map

| Path | Role |
| --- | --- |
| `app/main.py` | FastAPI app, auth, routes, HTML rendering, local filesystem APIs, library indexer, internal ZIP/media handlers, storage readout, addon zip endpoint. |
| `app/db.py` | SQLite schema, additive migrations, job/settings/favorites/notes/library/artifact persistence, backup and maintenance helpers. |
| `app/downloader.py` | External download scheduler, provider handlers, queue limits, provider cooldown, progress, retries, metadata sidecars, child job creation. |
| `app/internal_jobs.py` | Separate in-process scheduler for server-local jobs such as ZIP, transcode, and poster generation. |
| `app/parsers.py` | Input routing from text, URLs, shorthands, and CLI-like commands into `ParsedDownload`. |
| `app/models.py` | `ParsedDownload` data contract shared by parser, DB, downloader, and display code. |
| `app/metadata.py` | Hugging Face and Civitai display classification helpers. |
| `app/workflows.py` | ComfyUI workflow extraction from JSON/PNG, storage, viewer graph payloads. |
| `app/utils.py` | Shared path safety, sanitization, redaction, and formatting helpers. |
| `app/ytdlp_sites.py` | Host allow/preference list for yt-dlp routing. |
| `app/templates/index.html` | Single-page web UI markup plus client-side JavaScript state, polling, modals, viewers, and API calls. |
| `app/static/style.css` | Web UI layout, responsive behavior, modals, library cards, media/workflow viewers, top actions. |
| `app/static/manifest.webmanifest`, `app/static/sw.js`, `app/static/icons/*` | PWA install surface and static asset cache. |
| `chrome-extension/*` | Manifest V3 Chrome extension for sending current tab or typed input to HugCivi. |
| `tests/*` | Pytest coverage for parsers, queues, runtime behavior, APIs, archive/media safety, library, and regressions. |
| `Dockerfile`, `docker-compose.yml`, `portainer-stack.yml`, `docker-entrypoint.sh` | Container build, local compose, NAS/Portainer deployment, UID/GID/chown startup behavior. |
| `.github/workflows/container-image.yml` | Manual GHCR multi-arch image build and push. |

## Runtime Shape

```text
Browser or Chrome extension
  -> FastAPI routes in app/main.py
      -> SQLite state in app/db.py
      -> /data archive filesystem
      -> external download scheduler in app/downloader.py
      -> internal job scheduler in app/internal_jobs.py
      -> library indexer thread in app/main.py
```

Authentication is Basic Auth through `require_auth()` in `app/main.py`. Default username is `APP_USERNAME` or `admin`; password must come from `APP_PASSWORD` and cannot be an insecure placeholder.

## Feature Map

| Feature | User/API Surface | Backend Code | Frontend/Addon Code | State and Artifacts | Tests |
| --- | --- | --- | --- | --- | --- |
| Authentication | All protected routes | `app/main.py::require_auth` | Browser Basic Auth prompt, extension stores same ID/PW | `APP_USERNAME`, `APP_PASSWORD` | `tests/test_review_fixes.py::test_lifespan_runs_startup_tasks_and_stops_workers` indirectly exercises app startup; add direct auth tests when changing auth. |
| Settings and secrets | Settings modal, `POST /settings` | `app/main.py::save_settings`, `app/db.py::settings_status`, `db.set_setting`, `db.get_setting` | settings panes in `app/templates/index.html` | `settings` table, env fallback, `/config/startup.env` for gallery-dl auto-update | `test_settings_status_never_returns_secret_values`, `test_startup_config_writer_persists_gallery_dl_update_toggle` |
| Single URL/job submission | Main input, `POST /add` | `app/main.py::add_job`, `app/parsers.py::parse_input`, `app/db.py::create_job`, `app/downloader.py::enqueue_job` | `#command-form`, `#input_text` in `index.html` | `jobs` row with `job_kind='download'` | Parser tests, downloader runtime tests |
| Bulk URL submission | Bulk modal, `POST /api/jobs/bulk` | `add_jobs_bulk`, `bulk_input_lines`, parser and DB job creation | `submitBulkAdd`, `#bulk-add-modal` | multiple `jobs` rows; failed line report in response | `tests/test_bulk_add.py` |
| Job list and controls | `/api/jobs`, `/api/jobs/{id}`, pause/resume/retry/delete/clear, `/jobs/{id}/log` | `api_jobs`, `api_job`, `api_pause_job`, `api_resume_job`, `api_retry_job`, `api_delete_job`, `job_log`, `decorate_job` | `refreshJobs`, `renderJobs`, `renderMobileJobs`, `handleJobAction`, `clearJobHistory` | `jobs.status`, `jobs.log`, partial cleanup on history clear | `test_job_list_payload_omits_log_but_detail_and_log_endpoint_keep_it`, `test_job_summary_query_omits_heavy_fields_and_supports_cursor`, `test_retry_failed_job_requeues_existing_job`, `test_clear_history_removes_failed_partial_files_before_deleting_rows` |
| External download scheduler | In-process background queue | `app/downloader.py::start_workers`, `scheduler_loop`, `pick_next_schedulable_job_locked`, `run_job`, `provider_key_for_job` | UI only polls job state | in-memory scheduler plus `jobs` table | `tests/test_downloader_runtime.py`, `test_internal_job_rows_are_separate_from_download_resume_list` |
| Queue limits and cooldown | Settings modal queue pane | `queue_global_limit`, `queue_per_provider_limit`, `queue_provider_cooldown_range_seconds`, `notify_queue_settings_changed` | settings queue fields in `index.html` | `settings` table and env defaults | downloader runtime queue tests |
| Hugging Face downloads | HF URL, repo shorthand, `hf download`, `hf://` | `parse_huggingface_url`, `parse_hf_cli`, `parse_hf_uri`, `download_huggingface`, `huggingface_download_worker_main`, `metadata.classify_huggingface` | job list/library display | `/data/huggingface/...`, sidecar metadata, HF CLI subprocess | parser tests and downloader runtime tests |
| Civitai model downloads | Civitai model/version/download/hash/image URLs, numeric version ID | `parse_civitai_url`, `download_civitai`, `civitai_download_urls`, `metadata.classify_civitai`, `pick_civitai_file` | model cards, health resource panel | `/data/stable-diffusion/...`, `_civitai_metadata.json` | `tests/test_civitai_image_parser.py`, `tests/test_civitai_viewer_health.py`, downloader runtime tests |
| Civitai image archive and resource jobs | Civitai image page URL, media viewer generation panel, resource health | `download_civitai_image_page`, `normalize_civitai_image_record`, `create_civitai_image_resource_jobs`, `api_civitai_resource_health` | `normalizeCivitaiImageMetadata`, `renderGenerationPanel`, `checkCivitaiResourceHealth` | `_civitai_image_metadata.json`, child Civitai jobs, health from jobs/sidecars | `tests/test_civitai_viewer_health.py`, downloader runtime Civitai resource tests |
| Hitomi single gallery | Hitomi URL or `hitomi <id>` | `parse_hitomi_url`, `maybe_parse_hitomi_cli`, `download_hitomi`, `download_hitomi_gallery_dl`, native Hitomi helpers | library/media archive display | `/data/hitomi/...`, `_hitomi_metadata.json` | `tests/test_hitomi_listing.py`, downloader runtime tests |
| Hitomi listing discovery | artist/language/search/index URL, confirm modal | `parse_hitomi_url`, `download_hitomi_listing`, `discover_hitomi_listing_gallery_urls`, `queue_hitomi_listing_galleries`, listing API routes | `openHitomiListingModal`, `renderHitomiListing`, `queueHitomiListing` | parent listing job metadata and child Hitomi gallery jobs | `tests/test_hitomi_listing.py` |
| gallery-dl generic downloads | `gallery-dl`, `gdl`, supported HTTP URL | `maybe_parse_gallerydl_cli`, `download_gallerydl`, `gallery_dl_command`, auth/config helpers | job/library display | `/data/gallery-dl/<host>/...`, sidecar metadata when available | downloader runtime gallery-dl tests, `docs/gallery-dl-auth.md` for support reference |
| yt-dlp/YouTube and preferred video sites | YouTube URL, `yt`, `youtube`, `yt-dlp`, `ytdl:` | `maybe_parse_youtube_cli`, `parse_ytdlp_url`, `download_gallerydl`, `yt_dlp_command`, `app/ytdlp_sites.py` | media archive display and subtitles | `/data/gallery-dl/youtube.com/playlist/<id>` or `/data/gallery-dl/youtube.com/channel/<name>` for YouTube, subtitle files, ytdlp config args | `tests/test_youtube_parser.py`, `tests/test_main_urls.py`, downloader runtime yt-dlp tests |
| Generic HTTP file | Any non-provider HTTP/HTTPS URL | `ParsedDownload(source='generic')`, `download_generic`, `stream_download`, `resolve_remote_filename` | job/library display | `/data/generic/...`, `_generic_metadata.json`, scoped partial files | `test_partial_download_path_is_job_and_url_scoped`, generic parser regressions |
| ComfyUI workflow URL download | workflow/comfyui commands, workflow-like `.json` or `.png` URL | `maybe_parse_comfyui_cli`, `download_comfyui`, `fetch_workflow_bytes`, `update_job_workflow_info` | workflow card and viewer | `/data/comfyui/workflows`, `_workflow_metadata.json` | workflow-related review tests and parser coverage |
| ComfyUI drag-and-drop import | Drag PNG/JSON onto main input, `POST /api/workflows/import` | `api_import_workflow`, `workflow_max_bytes`, `save_workflow_bundle`, `find_workflow_png`, `load_workflow_view` | `setupWorkflowDropImport`, `importWorkflowFiles`, `openWorkflowViewer`, graph render functions | saved workflow bundle and job metadata; `WORKFLOW_IMPORT_MAX_BYTES` upload cap | workflow tests in `tests/test_review_fixes.py` |
| Library view and index | Library tab/cards, `/api/library`, `/api/library/reindex` | `library_items`, `start_library_indexer`, `scan_library_index_batch`, `library_item_for_path`, DB library index helpers | `renderLibrary`, `libraryCards`, sorting/favorites/source URL actions | `library_items`, `library_scan_state`, sidecar metadata from `/data` | `test_library_index_scan_populates_db_backed_library_items`, `test_library_items_restore_filesystem_card_after_job_row_deleted`, `test_library_items_index_generic_sidecar_folder` |
| Favorites and notes | Library card favorite, properties modal note | `api_set_favorite`, `api_save_path_note`, DB favorites/notes helpers | `toggleFavorite`, `showProperties`, `renderNoteEditor`, `savePropertiesNote` | `favorites`, `item_notes` tables | path prefix/update tests in `tests/test_review_fixes.py` |
| Filesystem operations | Context menu rename/move/delete/properties/preview/download | `api_rename_path`, `api_move_path`, `api_delete_path`, `api_path_properties`, `existing_data_path`, `ensure_mutable_path`, `ensure_no_active_jobs` | `showContextMenu`, `handleContextAction`, `postFileAction`, `showProperties` | `/data` filesystem, DB path prefix updates, favorites/notes/index maintenance | `test_safe_join_and_relative_path_preserve_internal_symlink_itself`, `test_active_job_protection_includes_jobs_without_target_dir`, `test_prefix_clears_escape_like_wildcards` |
| Browser download of archived files/folders | Context menu download, `/api/fs/download-info`, `/api/fs/download`, `/api/fs/download-jobs` | UI path uses direct files through `api_download_path` and folders through `archive_zip` internal jobs; direct folder ZIP remains a compatibility path | `enqueueLocalDownload`, `processDownloadQueue`, `pollDownloadJob` | ZIP artifacts in `/config/downloads`, `job_artifacts`, `job_content_refs` | archive preflight, semaphore, artifact, cleanup tests in `tests/test_review_fixes.py` |
| Media viewer | Library media card, `/api/media/list`, file/play/poster/subtitle APIs | media listing, subtitle, transcode, poster helpers in `app/main.py` | `openMediaViewer`, `renderMediaViewer`, `setupMediaPlayer`, `prepareMediaPlayback`, `prepareMediaPoster` | media files in `/data`, cache in `/config/media-cache` | `test_uncached_video_payload_requires_async_media_jobs`, media transcode/poster artifact tests, subtitle tests |
| Internal job scheduler | ZIP/transcode/poster work | `app/internal_jobs.py`, `register_internal_job_handlers`, `run_archive_zip_job`, `run_media_transcode_job`, `run_media_poster_job` | UI polls job status and artifact URLs | same `jobs` table with non-download `job_kind` | `test_internal_job_actions_use_internal_queue`, `test_internal_job_rows_are_separate_from_download_resume_list` |
| Storage readout | Top right DATA/HUGCIVI readout, `POST /api/storage/archive-usage` | `storage_status`, `scan_data_root_usage`, storage usage state helpers | `renderStorage`, `calculateStorageUsage`, polling helpers | `/data` disk usage and cached `library_scan_state['storage.data_usage']` | `test_storage_status_reports_data_volume_usage`, `test_storage_status_includes_cached_hugcivi_usage`, `test_storage_usage_scan_counts_data_files_without_following_symlinks` |
| Chrome extension addon download | Top right `애드온` button, `GET /api/addon/chrome-extension` | `api_chrome_extension_addon`, `create_chrome_extension_archive` | plain anchor `.addon-button` | zipped `chrome-extension/` folder, temporary zip cleaned after response | `test_chrome_extension_archive_contains_loadable_folder` |
| PWA install | Web manifest and service worker | `web_manifest`, `service_worker` routes | `<link rel="manifest">`, `app/static/sw.js` | static cache `hugcivi-static-v3` | `test_pwa_manifest_and_service_worker_are_declared` |
| Chrome extension remote | Extension popup and shortcut | HugCivi APIs reused: `/api/jobs/bulk`, `/api/jobs`; addon zip endpoint exposes package | `chrome-extension/manifest.json`, `background.js`, `popup.html`, `popup.js`, `shared.js` | `chrome.storage.local` settings and last activity | JS syntax checks: `node --check chrome-extension/*.js`; manifest JSON parse |
| Database maintenance | Maintenance APIs and optional clear-history vacuum | `api_db_wal`, checkpoint, optimize, compact, backup, `api_clear_jobs`; DB helpers | currently API-only or admin tooling | `maintenance_runs`, `/config/backups`; `SQLITE_VACUUM_AFTER_CLEAR` controls clear-history `VACUUM` | `test_database_backup_uses_sqlite_backup_api` |
| Container deployment | Docker/Portainer/GHCR | Dockerfile and entrypoint only | N/A | `/data`, `/config`, image `ghcr.io/mephiblin/hugcivi` | build smoke/manual CI workflow |

## API Route Index

| Route Group | Routes | Main Code |
| --- | --- | --- |
| Page and static | `GET /`, `GET/HEAD /manifest.webmanifest`, `GET/HEAD /sw.js` | `index`, `web_manifest`, `service_worker` |
| Add downloads | `POST /add`, `POST /api/jobs/bulk` | `add_job`, `add_jobs_bulk` |
| Job state | `GET /api/jobs`, `GET /api/jobs/{id}`, `GET /jobs/{id}/log`, job action routes | job API functions around `app/main.py` job management section |
| Settings/folders | `POST /settings`, `POST /folders`, `GET /api/folders` | `save_settings`, `create_folder`, `api_folders` |
| Library | `GET /api/library`, `POST /api/library/reindex`, `POST /api/favorites` | library index and favorite helpers |
| Filesystem | `/api/fs/rename`, move, delete, preview, properties, note, download info/download jobs/download | filesystem helpers near `existing_data_path` and archive helpers |
| Media | `/api/media/list`, archive, file, play, subtitle, poster, transcode/poster jobs | media helpers and internal job handlers |
| Workflows | `/api/workflows/import`, view, preview | workflow helpers and `app/workflows.py` |
| Hitomi listing | `/api/hitomi/listing/{id}`, `/queue` | listing metadata and queue helpers from downloader |
| Civitai health | `POST /api/civitai/resource-health` | Civitai health helpers in `app/main.py` and downloader state helpers |
| Storage | `GET /api/storage`, `POST /api/storage/archive-usage` | storage readout helpers |
| Addon | `GET /api/addon/chrome-extension` | Chrome extension zip helpers |
| Maintenance | `/api/maintenance/db/*` | DB maintenance API functions |

## State and Storage

| Storage | Owner | Notes |
| --- | --- | --- |
| `/data` | archive content | User-managed durable archive. All user paths must be resolved through safety helpers. |
| `/config/jobs.sqlite3` | app state | Jobs, settings, favorites, notes, library index, artifacts, scan state, maintenance runs. Treat backups as credential backups. |
| `/config/downloads` | ZIP artifacts | Folder download artifacts and temporary archives. Cleaned by TTL and response cleanup. |
| `/config/media-cache` | media cache | Browser MP4 transcodes and poster files. TTL and optional max-byte cleanup. |
| `/config/backups` | DB backup API | SQLite online backup output. |
| `/config/startup.env` | restart-affecting settings | Currently used for gallery-dl auto-update setting. |
| `chrome.storage.local` | Chrome extension | Extension server URL, username, password, target folder, recent activity. |

## Tests By File

| Test File | Main Coverage |
| --- | --- |
| `tests/test_bulk_add.py` | Bulk input normalization and `/api/jobs/bulk` job creation behavior. |
| `tests/test_civitai_image_parser.py` | Civitai URL routing regressions, especially image versus model/download URLs. |
| `tests/test_civitai_viewer_health.py` | Civitai image metadata in media viewer and resource health from jobs/sidecars. |
| `tests/test_downloader_runtime.py` | Scheduler behavior, partial files, cleanup, provider handlers, Civitai/Hitomi/gallery-dl/yt-dlp runtime helpers. |
| `tests/test_hitomi_listing.py` | Hitomi listing parse, discovery, confirm mode, selected/all queueing, dedupe, caps. |
| `tests/test_main_urls.py` | Display source URL behavior for wrapped yt-dlp/gallery-dl jobs. |
| `tests/test_review_fixes.py` | Security and regression coverage across settings, path safety, archive/media internal jobs, lifespan, DB backup, library index, PWA, storage, addon zip, subtitles. |
| `tests/test_youtube_parser.py` | YouTube and yt-dlp routing, wrapped `ytdl:` handling, preferred host behavior. |

## Change Playbooks

### Add A New Remote Download Source

1. Extend `ParsedDownload` in `app/models.py` only if existing fields cannot represent the source.
2. Extend parsing in `app/parsers.py`.
3. Add route/concurrency bucket logic in `app/downloader.py::provider_key_for_parsed`.
4. Implement the handler in `app/downloader.py` and dispatch from `run_job`.
5. Write metadata sidecars if the library UI should recover after DB loss.
6. Add parser tests and mocked runtime tests.
7. Update this document, `docs/architecture.md`, and user-facing README sections if the source is user-visible.

### Add A New Server-Local Long Job

1. Define a stable job kind.
2. Register it in `register_internal_job_handlers()`.
3. Create rows with `db.create_internal_job()`.
4. Enqueue with `internal_jobs.enqueue_job()`.
5. Update progress and call `internal_jobs.check_job_control(job_id)` in long loops.
6. Record artifacts and content refs when output is user-visible.
7. Add internal job routing and artifact tests.

### Change Filesystem Behavior

1. Start from `existing_data_path`, `data_path_from_request_path`, `ensure_mutable_path`, and `ensure_downloadable_path`.
2. Preserve `/data` root protection.
3. Preserve symlink escape checks for archive and mutation paths.
4. Update DB path prefixes for jobs, favorites, notes, and library index when paths move.
5. Run `tests/test_review_fixes.py` and targeted filesystem tests.

### Change The Main Web UI

1. Locate state variables and DOM IDs in `app/templates/index.html` near the client script start.
2. Keep API response shapes additive unless backend and frontend change together.
3. Use existing modal, card, and polling patterns.
4. Check desktop and mobile layouts in `app/static/style.css`.
5. For long operations, show queued/running/done state and poll instead of blocking an HTTP request.

### Change The Chrome Extension

1. Keep API compatibility with `/api/jobs/bulk` and `/api/jobs`.
2. Update `chrome-extension/manifest.json` when permissions, commands, or icons change.
3. Keep shared request/auth logic in `chrome-extension/shared.js`.
4. Run `node --check` on extension JavaScript and parse the manifest JSON.
5. Verify the web UI `애드온` download still packages a loadable folder.

## Verification Checklist

Fast local checks:

```bash
python3 -m py_compile app/main.py app/db.py app/downloader.py app/internal_jobs.py
node --check chrome-extension/shared.js
node --check chrome-extension/background.js
node --check chrome-extension/popup.js
node -e "JSON.parse(require('fs').readFileSync('chrome-extension/manifest.json','utf8'))"
python3 -m pytest -q -p no:cacheprovider tests/test_bulk_add.py tests/test_review_fixes.py
```

Broader check:

```bash
python3 -m pytest -q -p no:cacheprovider
```

Manual checks worth doing after UI or deployment changes:

- Open the main page and confirm the top actions, input, jobs table, library cards, settings modal, and mobile tabs still fit.
- Add a harmless generic URL in a dev environment and confirm a queued job appears.
- Use `애드온` and confirm the zip contains `hugcivi-chrome-extension/manifest.json`.
- Load the extension through `chrome://extensions` after changing `chrome-extension/*`.
- Confirm `/api/jobs` still returns an array without `cursor`.

## Current Documentation Set

| Document | Use |
| --- | --- |
| `README.md` | User-facing overview, install, first use, source examples, settings, troubleshooting. |
| `README_LLM.md` | LLM/developer entry point, reading order, handoff update timing. |
| `AGENTS.md` | Short persistent Codex project guidance loaded before work. |
| `docs/index.md` | Current versus historical documentation map. |
| `docs/architecture.md` | System shape, data boundaries, schedulers, DB, API groups, invariants. |
| `docs/configuration.md` | Environment variables, UI settings, and compose/Portainer default differences. |
| `docs/development.md` | Local setup, coding rules, change patterns, verification commands. |
| `docs/operations.md` | NAS/Portainer operation, queue defaults, storage, backup, recovery, troubleshooting. |
| `docs/feature-code-map.md` | Feature-to-code index for humans and LLMs. |
| `docs/gallery-dl-auth.md` | gallery-dl support/auth reference. |
| `docs/patch-notes/` | Date-based work history and handoff notes. |
| `SKILL_Dev/` | Repo-local stable skills for build/release, project core, filesystem safety, DB/jobs, providers, frontend/addon, and docs handoff. |
| `.agents/skills/hugcivi-dev-core/` | Codex auto-discovery pointer for the repo-local skill set. |
| dated design/review docs | Historical plans and risk notes. Do not treat them as current behavior unless code confirms it. |
