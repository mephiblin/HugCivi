# HugCivi Feature and Code Map

Last updated: 2026-07-06

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
| `app/internal_jobs.py` | Separate in-process scheduler for server-local jobs such as ZIP, transcode, poster generation, and selected-folder thumbnail backfill. |
| `app/transfer.py` | Copy-only transfer validation, rclone and HugCivi Receiver destination handling, rclone command building, policy sanitization, and output redaction helpers. |
| `app/subscriptions.py` | YouTube subscription payload/default helpers, source URL normalization, CRUD policy validation, manual/scheduled yt-dlp discovery, independent check scheduler, and independent subscription download worker. |
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
| `.github/workflows/container-image.yml`, `SKILL_Dev/skill_build.md` | GHCR image build/push references for GitHub Actions and local Portainer/Synology release flow. |

## Runtime Shape

```text
Browser or Chrome extension
  -> FastAPI routes in app/main.py
      -> SQLite state in app/db.py
      -> /data archive filesystem
      -> external download scheduler in app/downloader.py
      -> internal job scheduler in app/internal_jobs.py
      -> copy-only transfer helpers in app/transfer.py
      -> library indexer thread in app/main.py
```

Authentication is Basic Auth through `require_auth()` in `app/main.py`. Default username is `APP_USERNAME` or `admin`; password must come from `APP_PASSWORD` and cannot be an insecure placeholder.

## Feature Map

| Feature | User/API Surface | Backend Code | Frontend/Addon Code | State and Artifacts | Tests |
| --- | --- | --- | --- | --- | --- |
| Authentication | All protected routes | `app/main.py::require_auth` | Browser Basic Auth prompt, extension stores same ID/PW | `APP_USERNAME`, `APP_PASSWORD` | `tests/test_review_fixes.py::test_lifespan_runs_startup_tasks_and_stops_workers` indirectly exercises app startup; add direct auth tests when changing auth. |
| Settings and credentials | Settings modal, `POST /settings` | `app/main.py::save_settings`, `app/db.py::settings_status`, `db.set_setting`, `db.get_setting`; downloader queue helpers consume queue settings for provider scheduling and Hugging Face Hub response waits | settings panes in `app/templates/index.html`; credential values render in plaintext in the authenticated editor | `settings` table, env fallback, `/config/startup.env` for gallery-dl auto-update; saved tokens, cookies, proxy URLs, and extra options are credential-bearing settings; `QUEUE_PER_PROVIDER_LIMIT` is the user-facing control for Hugging Face job concurrency while snapshot internal workers stay fixed at 1, and `DOWNLOAD_STALL_TIMEOUT_SECONDS` controls Hugging Face Hub response waits | `test_settings_status_returns_credential_values_for_settings_form`, `test_settings_post_updates_runtime_auth_values`, `test_startup_config_writer_persists_gallery_dl_update_toggle`, `test_huggingface_snapshot_uses_queue_timeout_and_single_internal_worker` |
| Single URL/job submission | Main input, `POST /add` | `app/main.py::add_job`, `app/parsers.py::parse_input`, `app/db.py::create_job`, `app/downloader.py::enqueue_job` | `#command-form`, `#input_text` in `index.html` | `jobs` row with `job_kind='download'` | Parser tests, downloader runtime tests |
| Bulk URL submission | Bulk modal, `POST /api/jobs/bulk` | `add_jobs_bulk`, `bulk_input_lines`, parser and DB job creation | `submitBulkAdd`, `#bulk-add-modal` | multiple `jobs` rows; failed line report in response | `tests/test_bulk_add.py` |
| Job list and controls | `/api/jobs`, `/api/jobs?limit=50&page=N`, `/api/jobs?limit=50&page=N&source=civitai`, `/api/jobs/{id}`, pause/resume/retry/delete/clear, `/jobs/{id}/log`, row `이동` jump to target folder | `api_jobs`, `jobs_page_payload`, `normalize_job_source_filter`, `api_job`, `api_pause_job`, `api_resume_job`, `api_retry_job`, `api_delete_job`, `job_log`, `decorate_job`, `db.count_jobs`, `db.count_jobs_by_source`, `db.list_job_summaries` | `refreshJobs`, `renderJobs`, `renderJobSourceFilters`, `renderJobsPagination`, `renderMobileJobs`, `handleJobAction`, `goToJobFolder`, `clearJobHistory` | `jobs.status`, `jobs.source`, `jobs.log`, `jobs.target_dir`/decorated `target_path`, 50-row numbered page state, source filter state, partial cleanup on history clear | `test_job_list_payload_omits_log_but_detail_and_log_endpoint_keep_it`, `test_job_summary_query_omits_heavy_fields_and_supports_cursor`, `test_job_page_payload_supports_source_filters`, `test_retry_failed_job_requeues_existing_job`, `test_clear_history_removes_failed_partial_files_before_deleting_rows`, `test_home_template_declares_storage_folder_search_ui` |
| External download scheduler | In-process background queue | `app/downloader.py::start_workers`, `scheduler_loop`, `pick_next_schedulable_job_locked`, `run_job`, `provider_key_for_job` | UI only polls job state | in-memory scheduler plus `jobs` table | `tests/test_downloader_runtime.py`, `test_internal_job_rows_are_separate_from_download_resume_list` |
| Queue limits and cooldown | Settings modal queue pane | `queue_global_limit`, `queue_per_provider_limit`, `queue_provider_cooldown_range_seconds`, `notify_queue_settings_changed` | settings queue fields in `index.html` | `settings` table and env defaults | downloader runtime queue tests |
| Hugging Face downloads | HF URL, repo shorthand, `hf download`, `hf://` | `parse_huggingface_url`, `parse_hf_cli`, `parse_hf_uri`, `download_huggingface`, `huggingface_download_worker_main`, `metadata.classify_huggingface` | job list/library display | `/data/huggingface/...`, sidecar metadata, HF CLI subprocess | parser tests and downloader runtime tests |
| Civitai model downloads | Civitai model/version/download/hash/image URLs, numeric version ID, model-card `갱신` action through `POST /api/civitai/refresh`, model archive component checks through `POST /api/civitai/resource-health` | `parse_civitai_url`, `download_civitai`, `civitai_download_urls`, `civitai_required_component_files`, `fetch_civitai_model_page_metadata`, `fetch_civitai_rendered_model_page_metadata`, `merge_civitai_model_page_metadata`, `attach_civitai_tensor_metadata_summary`, `civitai_model_details`, `collect_civitai_model_generation_metadata`, `civitai_model_version_example_entries`, `save_civitai_model_preview_images`, `civitai_model_archive_metadata`, `civitai_refresh_parsed_download`, `api_civitai_refresh`, `api_civitai_resource_health`, `civitai_component_health_payload`, `metadata.classify_civitai`, `pick_civitai_file` | model cards, `refreshCivitaiArchive`, local preview thumbnail, media viewer generation/model-details panel, `modelComponentResources`, `checkCivitaiResourceHealth` | `/data/stable-diffusion/...`, `_civitai_metadata.json` with model body/version details, tensor summary, and `component_downloads`; optional `_civitai_generation_metadata.json` with model-version example images before gallery images; representative `civitai_example_<imageId>.*` preview images; primary plus required component files; refresh keeps existing files when present; sidecars restore model cards/panels after DB/history loss | `tests/test_civitai_image_parser.py`, `tests/test_civitai_viewer_health.py`, downloader runtime Civitai model tests |
| Civitai image archive and resource jobs | Civitai image page URL, media viewer generation panel, model-version resource health | `download_civitai_image_page`, `fetch_civitai_image_item`, `fetch_civitai_rendered_image_page_item`, `extract_civitai_next_image_item`, `rendered_civitai_media_url`, `civitai_original_media_url_from_rendered`, `normalize_civitai_image_record`, `civitai_image_thumbnail_url`, `create_civitai_image_resource_jobs`, `civitai_existing_resource_state`, `api_civitai_resource_health` | `normalizeCivitaiImageMetadata`, `renderGenerationPanel`, `generationModelVersionIds`, `checkCivitaiResourceHealth` | `_civitai_image_metadata.json`, downloaded image/video asset including rendered-page webm videos, child Civitai jobs, health from jobs/sidecars | `tests/test_civitai_viewer_health.py`, downloader runtime Civitai resource tests |
| Hitomi single gallery | Hitomi URL or `hitomi <id>` | `parse_hitomi_url`, `maybe_parse_hitomi_cli`, `download_hitomi`, `download_hitomi_gallery_dl`, native Hitomi helpers | library/media archive display | `/data/hitomi/...`, `_hitomi_metadata.json` | `tests/test_hitomi_listing.py`, downloader runtime tests |
| Hitomi listing discovery | artist/language/search/index URL, confirm modal | `parse_hitomi_url`, `download_hitomi_listing`, `discover_hitomi_listing_gallery_urls`, `queue_hitomi_listing_galleries`, listing API routes | `openHitomiListingModal`, `renderHitomiListing`, `queueHitomiListing` | parent listing job metadata and child Hitomi gallery jobs | `tests/test_hitomi_listing.py` |
| ASMR.one work downloads | ASMR.one `/work/RJ...` and `/work/<id>/DLSITE/RJ...` URLs; normal external queue under the `asmrone` provider bucket | `parse_asmrone_url`, `download_asmrone`, `asmrone_manifest_entries`, `asmrone_work_cover_url`, `save_asmrone_cover_image`, `validate_asmrone_downloaded_file`, `asmrone_download_action_url`, `provider_key_for_parsed`; file bodies use track `mediaDownloadUrl` plus `action=download`, not `mediaStreamUrl` | job list/library display and audio media viewer support in `index.html`/`style.css`; audio archive cards/viewers use folder cover thumbnails | `/data/asmr.one/...`, Unicode-safe track paths, downloaded track files, optional `cover.jpg`, `_asmrone_metadata.json`, redacted `_asmrone_tracks.json`, `_asmrone_manifest.json` with per-file `download_status` and `failed_file_count`, `_archive_metadata.json`; partial file failures and Cloudflare error-image responses are preserved as manifest failures when at least one file downloads | `tests/test_asmrone_provider.py` |
| gallery-dl generic downloads | `gallery-dl`, `gdl`, supported HTTP URL | `maybe_parse_gallerydl_cli`, `download_gallerydl`, `gallery_dl_command`, auth/config helpers | job/library display | `/data/gallery-dl/<host>/...`, sidecar metadata when available | downloader runtime gallery-dl tests, `docs/gallery-dl-auth.md` for support reference |
| yt-dlp/YouTube and preferred video sites | YouTube URL, `yt`, `youtube`, `yt-dlp`, `ytdl:` | `maybe_parse_youtube_cli`, `parse_ytdlp_url`, `download_gallerydl`, `yt_dlp_command`, `gallery_dl_downloaded_files`, `app/ytdlp_sites.py` | media archive display, best-effort subtitles, settings modal `YouTube/yt-dlp Proxy` | `/data/gallery-dl/youtube.com/playlist/<id>` or `/data/gallery-dl/youtube.com/channel/<name>` for YouTube, optional subtitle sidecars, ytdlp config args, `YT_DLP_PROXY` env/UI setting | `tests/test_youtube_parser.py`, `tests/test_main_urls.py`, downloader runtime yt-dlp tests |
| YouTube subscriptions | Sidebar `구독` tab, add-subscription modal, list/create/update/delete subscriptions, manual `check now`, scheduled discovery, independent subscription downloads, item list/action APIs, per-subscription storage readout, main-panel subscription work list | `app/db.py` subscription tables/helpers, item counts/storage/summary helpers, `app/subscriptions.py`, subscription routes and lifespan hooks in `app/main.py` | sidebar subscription tab, expandable item list, main `구독 작업 목록`, item queue/skip/retry controls, and modal in `app/templates/index.html`, styles in `app/static/style.css` | `subscriptions`, `subscription_items`; discovery and download state stay separate from normal `jobs` | `tests/test_youtube_subscriptions.py`, `tests/test_review_fixes.py::test_home_template_declares_subscription_sidebar_ui`, `tests/test_review_fixes.py::test_lifespan_runs_startup_tasks_and_stops_workers` |
| Generic HTTP file | Any non-provider HTTP/HTTPS URL | `ParsedDownload(source='generic')`, `download_generic`, `stream_download`, `resolve_remote_filename` | job/library display | `/data/generic/...`, `_generic_metadata.json`, scoped partial files | `test_partial_download_path_is_job_and_url_scoped`, generic parser regressions |
| ComfyUI workflow URL download | workflow/comfyui commands, workflow-like `.json` or `.png` URL | `maybe_parse_comfyui_cli`, `download_comfyui`, `fetch_workflow_bytes`, `update_job_workflow_info` | workflow card and viewer | `/data/comfyui/workflows`, `_workflow_metadata.json` | workflow-related review tests and parser coverage |
| ComfyUI drag-and-drop import | Drag PNG/JSON onto main input, `POST /api/workflows/import` | `api_import_workflow`, `workflow_max_bytes`, `save_workflow_bundle`, `find_workflow_png`, `load_workflow_view` | `setupWorkflowDropImport`, `importWorkflowFiles`, `openWorkflowViewer`, graph render functions | saved workflow bundle and job metadata; `WORKFLOW_IMPORT_MAX_BYTES` upload cap | workflow tests in `tests/test_review_fixes.py` |
| Library view and index | Library tab/cards, legacy array `/api/library`, paged `/api/library?limit=50&page=N&sort=...` with A-Z/Z-A/newest/oldest/favorite sort values, `/api/library?mode=live&path=...`, `/api/library/reindex`, selected-folder `POST /api/media/thumbnail-jobs` | `api_library`, `library_items_page_payload`, `library_items`, `live_library_items`, `iter_data_paths`, `start_library_indexer`, `scan_library_index_batch`, `library_item_for_path`, card thumbnail helpers, `thumbnail_backfill_candidates`, `api_media_thumbnail_jobs`, yt-dlp `.info.json` title helpers, Civitai sidecar restore helpers, DB library index helpers, `api_clear_jobs` index reset | `renderLibrary`, `renderLibraryPagination`, `refreshLibraryItems`, `refreshLibraryForActivePath`, `queueLibraryThumbnailBackfill`, `libraryJobSignature`, sorting/favorites/source URL actions; folder navigation requests 50-card live pages from a bounded stable sorted live-scan window, defaulting to three pages for small folders; card thumbnail requests are viewport-based and capped at 3 concurrent `/api/media/thumbnail` fetches; selected folders can queue representative-thumbnail backfill jobs | `library_items`, `library_scan_state`, sidecar metadata from `/data`, yt-dlp info sidecars; clearing job history resets stale index rows and live scans restore filesystem-backed cards; first visits and existing filesystem cards request thumbnails only as cards enter the viewport, so large pages do not dispatch 100 uncached thumbnail generations at once; selected-folder thumbnail backfill scans card representatives only and writes disposable JPEGs under `/config/media-cache/thumbnails` | `test_library_api_keeps_legacy_array_and_returns_paged_wrapper`, `test_library_api_date_sort_supports_newest_oldest_and_legacy_alias`, `test_live_library_pagination_sorts_stable_scan_window`, `test_library_index_scan_populates_db_backed_library_items`, `test_library_items_restore_filesystem_card_after_job_row_deleted`, `test_library_live_path_finds_child_model_cards_when_index_is_stale`, `test_clear_history_resets_stale_library_index_so_existing_model_cards_survive`, `test_media_thumbnail_endpoint_creates_and_reuses_cached_image`, `test_media_thumbnail_endpoint_rejects_root_and_symlink`, `test_media_thumbnail_backfill_job_uses_card_representatives` |
| Favorites and notes | Library card favorite, properties modal note | `api_set_favorite`, `api_save_path_note`, DB favorites/notes helpers | `toggleFavorite`, `showProperties`, `renderNoteEditor`, `savePropertiesNote` | `favorites`, `item_notes` tables | path prefix/update tests in `tests/test_review_fixes.py` |
| Filesystem operations | Context menu create folder/rename/move/delete/properties/preview/download; selected-folder scoped folder search; move destination tree picker; manual folder-tree refresh; `GET /api/folders/children` lazy folder expansion | `api_create_folder`, `api_folder_children`, `folder_children_payload`, `folder_child_item`, `folder_tree_has_expandable_children`, `is_hitomi_archive_leaf_folder`, `direct_child_directories`, `folder_has_child_directories`, `api_rename_path`, `api_move_path`, `api_delete_path`, `api_path_properties`, `existing_data_path`, `ensure_mutable_path`, `ensure_no_active_jobs`; `initial_folder_tree` serves root-direct bounded `/api/folders` while `/api/folders/children` loads direct child folder rows on demand with `limit`/`cursor` pagination | `selectFolderPath`, folder search/create/move modal helpers, lazy folder expansion, `showContextMenu`, `handleContextAction`, `postFileAction`, `refreshFolders`, `showProperties`; folder search and move destination selection operate on the loaded tree plus lazy expansions; file actions update tree/library in place and keep the active folder when possible | `/data` filesystem, DB path prefix updates, favorites/notes/index maintenance; folder tree payloads expose `/data`-relative paths only, skip symlink folder entries, and treat Hitomi gallery archives as Tree leaves | `test_home_template_declares_storage_folder_search_ui`, `test_api_folders_initial_tree_is_root_direct_children_only`, `test_api_folder_children_returns_direct_children_with_pagination`, `test_api_folder_children_treats_hitomi_archives_as_leaf_without_child_scan`, `test_api_folder_children_skips_symlinks_and_rejects_symlink_parent`, `test_api_create_folder_creates_child_and_rejects_nested_name`, `test_safe_join_and_relative_path_preserve_internal_symlink_itself`, `test_active_job_protection_includes_jobs_without_target_dir`, `test_prefix_clears_escape_like_wildcards` |
| Browser download of archived files/folders | Context menu download, `/api/fs/download-info`, `/api/fs/download`, `/api/fs/download-jobs` | UI path uses direct files through `api_download_path` and folders through `archive_zip` internal jobs; direct folder ZIP remains a compatibility path | `enqueueLocalDownload`, `processDownloadQueue`, `pollDownloadJob` | ZIP artifacts in `/config/downloads`, `job_artifacts`, `job_content_refs` | archive preflight, semaphore, artifact, cleanup tests in `tests/test_review_fixes.py` |
| Copy-only transfer | Context menu `전송`, settings pane target management for HugCivi Receiver or rclone targets, registered target APIs, Receiver folder tree proxy, `POST /api/transfer/preflight`, `POST /api/transfer/jobs` | Transfer API/routes/runner in `app/main.py`, transfer target persistence in `app/db.py`, copy command and Receiver helpers in `app/transfer.py`, `transfer_copy` handler registered with `app/internal_jobs.py` | `openTransferModal`, `loadTransferTargets`, `refreshTransferReceiverBrowser`, `refreshTransferPreflight`, `submitTransferJob`, `loadTransferSettingTargets`, `saveTransferSettingTarget`; Receiver modal submits `target_id`, `/data` `source_path`, and selected `destination_subpath` while rclone keeps the default target base path | `transfer_targets` table with `kind`, rclone remote fields, Receiver URL/token fields, and copy policy; Receiver `/api/browse` responses are proxied without exposing the token; `jobs` rows with `job_kind='transfer_copy'` and `source='transfer'`; `/config/rclone/rclone.conf` for rclone credentials; manifest artifacts under `/config/transfer-manifests`; SQLite job logs | `tests/test_transfer_api.py`, `tests/test_transfer_core.py`, `tests/test_transfer_db.py`; template checks for context menu, modal fields, settings pane, Receiver fields, token hiding, tree proxy, and payload shape |
| Media viewer | Library media card, `/api/media/list`, `/api/media/thumbnail`, `/api/media/thumbnail-jobs`, `/api/media/text`, file/play/poster/subtitle APIs | image/video/audio/document listing, direct audio playback, lazy card thumbnail generation, selected-folder thumbnail backfill queueing, bounded text/Markdown reading, archive cover payloads, video subtitle/transcode/poster helpers in `app/main.py` | `openMediaViewer`, `renderMediaViewer`, `setupMediaPlayer`, `setupMediaDocument`, `prepareMediaPlayback`, `prepareMediaPoster`, audio cover and escaped document rendering | media files in `/data`; `.txt`/`.md`/`.markdown` documents rendered as escaped text, not HTML; audio archive covers from local folder thumbnails; video transcode/poster cache in `/config/media-cache`; card JPEG thumbnails live under `/config/media-cache/thumbnails` and are requested through the viewport queue or generated by selected-folder backfill | `test_uncached_video_payload_requires_async_media_jobs`, media transcode/poster artifact tests, thumbnail cache endpoint tests, thumbnail backfill tests, subtitle tests, `test_asmrone_audio_files_are_media_items`, `test_audio_archive_uses_folder_cover_for_card_and_media_items`, `test_text_and_markdown_files_are_readable_media_cards`, `test_media_text_endpoint_limits_large_documents` |
| Internal job scheduler | ZIP/transcode/poster/thumbnail backfill/transfer work | `app/internal_jobs.py`, `register_internal_job_handlers`, `run_archive_zip_job`, `run_media_transcode_job`, `run_media_poster_job`, `run_media_thumbnail_backfill_job`, `run_transfer_copy_job` | UI polls job status and artifact URLs; library thumbnail button queues `media_thumbnail_backfill`; transfer modal queues `transfer_copy` | same `jobs` table with non-download `job_kind`; `media_thumbnail_backfill` stores candidate counts and results in metadata; `transfer_copy` stores the request snapshot, SQLite job log, and manifest artifact refs | `test_internal_job_actions_use_internal_queue`, `test_internal_job_rows_are_separate_from_download_resume_list`, `test_media_thumbnail_backfill_job_uses_card_representatives`, transfer internal job tests in `tests/test_transfer_api.py` |
| Storage readout | Top right DATA/HUGCIVI readout, `POST /api/storage/archive-usage` | `storage_status`, `scan_data_root_usage`, storage usage state helpers | `renderStorage`, `calculateStorageUsage`, polling helpers | `/data` disk usage and cached `library_scan_state['storage.data_usage']` | `test_storage_status_reports_data_volume_usage`, `test_storage_status_includes_cached_hugcivi_usage`, `test_storage_usage_scan_counts_data_files_without_following_symlinks` |
| Chrome extension addon download | Top right `애드온` button, `GET /api/addon/chrome-extension` | `api_chrome_extension_addon`, `create_chrome_extension_archive` | plain anchor `.addon-button` | zipped `chrome-extension/` folder, temporary zip cleaned after response | `test_chrome_extension_archive_contains_loadable_folder` |
| PWA install | Web manifest and service worker | `web_manifest`, `service_worker` routes | `<link rel="manifest">`, `app/static/sw.js` | static cache `hugcivi-static-v3` | `test_pwa_manifest_and_service_worker_are_declared` |
| Chrome extension remote | Extension popup and shortcut | HugCivi APIs reused: `/api/jobs/bulk`, `/api/jobs`; addon zip endpoint exposes package | `chrome-extension/manifest.json`, `background.js`, `popup.html`, `popup.js`, `shared.js` | `chrome.storage.local` settings and last activity | JS syntax checks: `node --check chrome-extension/*.js`; manifest JSON parse |
| Database maintenance | Maintenance APIs and optional clear-history vacuum | `api_db_wal`, checkpoint, optimize, compact, backup, `api_clear_jobs`; DB helpers | currently API-only or admin tooling | `maintenance_runs`, `/config/backups`; `SQLITE_VACUUM_AFTER_CLEAR` controls clear-history `VACUUM` | `test_database_backup_uses_sqlite_backup_api` |
| Container deployment | Docker/Portainer/GHCR | Dockerfile, entrypoint, `portainer-stack.yml`, build skill workflow | N/A | `/data`, `/config`, image `ghcr.io/mephiblin/hugcivi:latest` and `sha-<commit>` tags | build smoke/manual CI workflow; local image push verification in patch notes |

## API Route Index

| Route Group | Routes | Main Code |
| --- | --- | --- |
| Page and static | `GET /`, `GET/HEAD /manifest.webmanifest`, `GET/HEAD /sw.js` | `index`, `web_manifest`, `service_worker` |
| Add downloads | `POST /add`, `POST /api/jobs/bulk` | `add_job`, `add_jobs_bulk` |
| Job state | `GET /api/jobs`, `GET /api/jobs/{id}`, `GET /jobs/{id}/log`, job action routes | job API functions around `app/main.py` job management section |
| YouTube subscriptions | `/api/subscriptions`, aggregate `/api/subscriptions/items`, `/api/subscriptions/{id}`, `/api/subscriptions/{id}/items`, create/update/delete, manual `/check`, item `/queue`, `/skip`, `/retry` | subscription API functions around `app/main.py` subscription section |
| Settings/folders | `POST /settings`, legacy `POST /folders`, `GET/POST /api/folders`, `GET /api/folders/children` | `save_settings`, `create_folder`, `api_folders`, `api_folder_children`, `api_create_folder`, `initial_folder_tree`, `build_folder_tree`, `folder_children_payload` |
| Library | `GET /api/library`, paged `GET /api/library?limit=50&page=N`, `POST /api/library/reindex`, `POST /api/favorites` | library index and favorite helpers |
| Filesystem | `/api/fs/rename`, move, delete, preview, properties, note, download info/download jobs/download | filesystem helpers near `existing_data_path` and archive helpers |
| Transfer | `GET/POST /api/transfer/targets`, `PATCH/DELETE /api/transfer/targets/{target_id}`, `POST /api/transfer/preflight`, `POST /api/transfer/jobs` | copy-only transfer target validation, preflight, and `transfer_copy` internal job creation |
| Media | `/api/media/list`, archive, file, thumbnail, thumbnail backfill jobs, play, subtitle, poster, transcode/poster jobs | media helpers and internal job handlers |
| Workflows | `/api/workflows/import`, view, preview | workflow helpers and `app/workflows.py` |
| Hitomi listing | `/api/hitomi/listing/{id}`, `/queue` | listing metadata and queue helpers from downloader |
| Civitai health/refresh | `POST /api/civitai/resource-health`, `POST /api/civitai/refresh` | Resource health checks model-version IDs against jobs/sidecars and model archive components against local files under the requested `/data` path; refresh queues a Civitai download job pointed at the existing model folder. |
| Storage | `GET /api/storage`, `POST /api/storage/archive-usage` | storage readout helpers |
| Addon | `GET /api/addon/chrome-extension` | Chrome extension zip helpers |
| Maintenance | `/api/maintenance/db/*` | DB maintenance API functions |

## State and Storage

| Storage | Owner | Notes |
| --- | --- | --- |
| `/data` | archive content | User-managed durable archive. All user paths must be resolved through safety helpers. |
| `/config/jobs.sqlite3` | app state | Jobs, settings, favorites, notes, library index, artifacts, scan state, maintenance runs. Treat backups as credential backups. |
| `/config/downloads` | ZIP artifacts | Folder download artifacts and temporary archives. Cleaned by TTL and response cleanup. |
| `/config/media-cache` | media cache | Browser MP4 transcodes, poster files, and lazy/backfilled card thumbnails under `thumbnails/`. Card thumbnail requests are paced by the viewport/concurrency queue, and selected-folder backfill jobs skip already-cached files. TTL and optional max-byte cleanup. |
| `/config/rclone/rclone.conf` | transfer config | Operator-managed rclone remote definitions. rclone credentials stay in this file, not in rclone transfer target rows. HugCivi Receiver tokens are stored in SQLite but hidden from target list/job payloads and logs. |
| `/config/transfer-manifests` | transfer artifacts | Manifest files from completed `transfer_copy` internal jobs. Transfer logs stay in SQLite job log rows. |
| `/config/backups` | DB backup API | SQLite online backup output. |
| `/config/startup.env` | restart-affecting settings | Currently used for gallery-dl auto-update setting. |
| `chrome.storage.local` | Chrome extension | Extension server URL, username, password, target folder, recent activity. |

## Tests By File

| Test File | Main Coverage |
| --- | --- |
| `tests/test_bulk_add.py` | Bulk input normalization and `/api/jobs/bulk` job creation behavior. |
| `tests/test_asmrone_provider.py` | ASMR.one URL parsing, source URL display, Unicode-safe local paths, mediaDownloadUrl body download handling, partial failure manifests, sidecar metadata, and audio media recognition. |
| `tests/test_civitai_image_parser.py` | Civitai URL routing regressions, especially image versus model/download URLs. |
| `tests/test_civitai_viewer_health.py` | Civitai image/model metadata in media viewer, model-card restoration from sidecars, local component health, and resource health from jobs/sidecars. |
| `tests/test_downloader_runtime.py` | Scheduler behavior, partial files, cleanup, provider handlers, Civitai model sidecars/example images/required components, Hitomi/gallery-dl/yt-dlp runtime helpers. |
| `tests/test_hitomi_listing.py` | Hitomi listing parse, discovery, confirm mode, selected/all queueing, dedupe, caps. |
| `tests/test_main_urls.py` | Display source URL behavior for wrapped yt-dlp/gallery-dl jobs. |
| `tests/test_review_fixes.py` | Security and regression coverage across settings, path safety, archive/media internal jobs, lifespan, DB backup, library index, PWA, storage, addon zip, subtitles. |
| `tests/test_transfer_api.py` | Transfer API target CRUD, copy-only API validation, source/path safety, internal job creation, manifest writing, and transfer template affordances. |
| `tests/test_transfer_core.py` | rclone remote/path validation, copy/copyto argv construction, env defaults, policy clamping, and sync/move/delete rejection. |
| `tests/test_transfer_db.py` | Transfer target schema/CRUD/policy persistence and `create_internal_job(source='transfer')` behavior. |
| `tests/test_youtube_parser.py` | YouTube and yt-dlp routing, wrapped `ytdl:` handling, preferred host behavior. |
| `tests/test_youtube_subscriptions.py` | YouTube subscription schema, helper, API CRUD, manual discovery, independent download worker, item action APIs, storage payload, and error-redaction coverage. |

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
