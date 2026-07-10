# HugCivi Configuration Reference

Last updated: 2026-07-09

This document lists the configuration knobs a developer or operator is likely to meet. Values can come from environment variables, Docker/Portainer stack settings, or the web UI settings table depending on the key.

## Source Priority

Most runtime settings use this order:

1. Value saved in the web UI, stored in SQLite `settings`.
2. Environment variable.
3. Code default from `app/defaults.py`, `app/db.py`, `app/main.py`, or `app/downloader.py`.

Path and startup settings are usually read from environment variables at import or container startup, so changing them may require a process restart.

## Required And Security

| Key | Default | Source | Notes |
| --- | --- | --- | --- |
| `APP_USERNAME` | `admin` | env | Basic Auth username. |
| `APP_PASSWORD` | none | env | Required. Empty or placeholder values return 503. |
| `USER_AGENT` | `nas-model-archiver/0.1` | env | User-Agent for external HTTP requests. |

## Paths And Volumes

| Key | Default | Source | Notes |
| --- | --- | --- | --- |
| `DATA_ROOT` | `/data` | env | Durable archive root. Do not point multiple containers at the same root and DB concurrently. |
| `DB_PATH` | `/config/jobs.sqlite3` | env | SQLite state DB. |
| `DOWNLOAD_ARCHIVE_DIR` | `/config/downloads` | env | Temporary folder ZIP artifacts. |
| `MEDIA_CACHE_DIR` | `/config/media-cache` | env | Browser transcodes, poster cache, and lazy card thumbnails under `thumbnails/`. |
| `HUGCIVI_CHROME_EXTENSION_DIR` | app parent `chrome-extension` | env | Source folder zipped by `/api/addon/chrome-extension`. |
| `HUGCIVI_STARTUP_CONFIG_FILE` | `/config/startup.env` | env | Startup file used by the entrypoint for gallery-dl auto-update. |
| `RCLONE_CONFIG` | `/config/rclone/rclone.conf` | env | rclone config file used by rclone copy-only transfer targets. Keep rclone remote credentials here, not in rclone target rows. |
| `DATA_REMOTE_DIR` | `/data_remote` | env | Optional connected-folder root for `local_mount` copy-only transfer targets. Mount host-managed PC/Synology/remote folders here; HugCivi does not index it as a library root. |
| `TRANSFER_MANIFEST_DIR` | `/config/transfer-manifests` | env | Manifest artifact directory for completed copy-only transfer jobs, including Receiver jobs. |
| `HUGCIVI_DATA_DIR` | stack-specific | compose/Portainer | Host bind mount source for `/data`. |
| `HUGCIVI_CONFIG_DIR` | stack-specific | compose/Portainer | Host bind mount source for `/config`. |

## Container Startup

| Key | Default | Source | Notes |
| --- | --- | --- | --- |
| `PUID` | compose/Portainer `1000`, entrypoint `0` | env | UID used by `gosu`. |
| `PGID` | compose/Portainer `1000`, entrypoint `0` | env | GID used by `gosu`. |
| `UMASK` | `022` | env | File creation mask. |
| `HUGCIVI_CHOWN_ON_START` | `0` | env | If `1`, chowns `/data` and `/config` before starting. Use only when needed. |
| `HUGCIVI_HTTP_PORT` | `8088` | Portainer | Host port binding in `portainer-stack.yml`. |
| `HUGCIVI_IMAGE` | `ghcr.io/mephiblin/hugcivi:latest` | Portainer | Image used by production stack. |
| `GALLERY_DL_AUTO_UPDATE` | `1` | env/UI/startup file | Entry point upgrades gallery-dl inside the container when enabled. |
| `GALLERY_DL_UPDATE_SPEC` | `gallery-dl<2.0` | env | pip requirement used by auto-update. |

## Library Routes

These keys can be saved in the UI and are also read from env fallback.

| Key | Default |
| --- | --- |
| `LIBRARY_ACTIVE` | `ComfyUI` |
| `ROUTE_LLM_ROOT` | `huggingface/llm` |
| `ROUTE_LORA_ROOT` | `stable-diffusion/loras` |
| `ROUTE_CHECKPOINT_ROOT` | `stable-diffusion/checkpoints` |
| `ROUTE_DIFFUSION_MODEL_ROOT` | `stable-diffusion/diffusion_models` |
| `ROUTE_EMBEDDING_ROOT` | `stable-diffusion/embeddings` |
| `ROUTE_VAE_ROOT` | `stable-diffusion/vae` |
| `ROUTE_CONTROLNET_ROOT` | `stable-diffusion/controlnet` |
| `ROUTE_UPSCALER_ROOT` | `stable-diffusion/upscalers` |

## Provider Credentials And Options

The authenticated settings modal renders these credential and option values in plaintext so they can be edited during runtime. Saving the form writes UI values to SQLite, and new downloads read them without a process restart. Submitting an empty credential field clears the UI-saved value; if an environment variable exists for the same key, the environment value remains the fallback. Restrict access to the HugCivi UI and treat `/config/jobs.sqlite3` as a credential-bearing file.

| Key | Default | Source | Notes |
| --- | --- | --- | --- |
| `HF_TOKEN` | empty | env/UI | Hugging Face token. |
| `CIVITAI_TOKEN` | empty | env/UI | Civitai token. |
| `GALLERY_DL_USERNAME` | empty | env/UI | Site login username for gallery-dl. |
| `GALLERY_DL_PASSWORD` | empty | env/UI | Site login password for gallery-dl. |
| `GALLERY_DL_COOKIES_FILE` | empty | env/UI | Container path to Netscape cookies file. |
| `GALLERY_DL_COOKIES_FROM_BROWSER` | empty | env/UI | Browser profile mode. Requires profile mounted into the container. |
| `GALLERY_DL_EXTRA_OPTIONS` | empty | env/UI | Lines converted to `gallery-dl -o key=value`. Can contain secrets. |
| `YT_DLP_COOKIES_FILE` | empty | env/UI | Container path to cookies file. Alias `YTDLP_COOKIES_FILE` is also read. |
| `YT_DLP_COOKIES_FROM_BROWSER` | empty | env/UI | Browser profile mode. Alias `YTDLP_COOKIES_FROM_BROWSER` is also read. |
| `YT_DLP_PROXY` | empty | env/UI | HTTP/HTTPS/SOCKS proxy URL passed to yt-dlp as `--proxy`. Alias `YTDLP_PROXY` is also read. Treat authenticated proxy URLs as credentials. |
| `YT_DLP_FORMAT` | H.264 MP4 first | env/UI | Format selector. Alias `YTDLP_FORMAT` is also read. |
| `YT_DLP_EXTRA_OPTIONS` | empty | env/UI | Supports `cmdline-args=` and selected config keys for advanced yt-dlp tuning. Output/path/exec/plugin/downloader/config overrides are blocked. Alias `YTDLP_EXTRA_OPTIONS` is also read. Prefer `YT_DLP_PROXY` for proxy configuration. Explicit subtitle or error-handling options override HugCivi's best-effort default YouTube subtitle behavior. |

## Queue And Request Safety

| Key | Default | Source | Notes |
| --- | --- | --- | --- |
| `MAX_CONCURRENT_DOWNLOADS` | `3` | env/UI | Global external download concurrency. |
| `MAX_CONCURRENT_DOWNLOADS_HARD_LIMIT` | `12` | env | Upper cap for UI-saved global concurrency. |
| `QUEUE_PER_PROVIDER_LIMIT` | `1` | env/UI | Concurrent jobs per provider bucket. Hugging Face snapshot internal workers stay fixed at 1 so this provider limit does not multiply. |
| `QUEUE_PER_PROVIDER_LIMIT_HARD_LIMIT` | `4` | env | Upper cap for UI-saved provider limit. |
| `QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS` | `2` | env/UI | Minimum provider cooldown. |
| `QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS` | `2` | env/UI | Maximum provider cooldown. |
| `QUEUE_PROVIDER_COOLDOWN_SECONDS` | `2` | env/UI legacy | Compatibility fallback used when min/max cooldown values are absent. Prefer the min/max keys. |
| `DOWNLOAD_STALL_TIMEOUT_SECONDS` | Docker/local `600`, Portainer `0` | env/UI | Watchdog timeout and Hugging Face Hub response wait. `0` disables timeout semantics. |
| `DOWNLOAD_REQUEST_MIN_INTERVAL_SECONDS` | `1.5` | env | Default host throttle. |
| `HF_REQUEST_MIN_INTERVAL_SECONDS` | default throttle | env | Hugging Face-specific request throttle. |
| `CIVITAI_REQUEST_MIN_INTERVAL_SECONDS` | default throttle | env | Civitai-specific request throttle. |
| `HITOMI_REQUEST_MIN_INTERVAL_SECONDS` | default throttle | env | Hitomi-specific request throttle. |
| `DOWNLOAD_HTTP_MAX_RETRIES` | `3` | env | HTTP retry count. |
| `DOWNLOAD_HTTP_MAX_RETRIES_HARD_LIMIT` | `8` | env | Upper cap for retry count. |
| `DOWNLOAD_RETRY_BACKOFF_SECONDS` | `5` | env | Base retry backoff. |
| `DOWNLOAD_MAX_RETRY_SLEEP_SECONDS` | `300` | env | Max retry sleep. |
| `DOWNLOAD_ENABLE_HEAD_REQUESTS` | `1` | env | Enables HEAD preflight/metadata requests. |
| `DOWNLOAD_PROGRESS_SCAN_MAX_FILES` | `2000` | env | Limits progress directory scans. |
| `DOWNLOAD_WATCHDOG_SCAN_MAX_FILES` | `2000` | env | Limits watchdog directory scans. |
| `PROCESS_OUTPUT_QUEUE_MAX_LINES` | `1000` | env | Bounded subprocess output queue size. |

## Transfer Copy

Transfer targets are stored in SQLite and can use `local_mount`, `receiver`, or `rclone` kind.

- `local_mount` targets copy to a folder already mounted under `DATA_REMOTE_DIR`. The target `remote_path` is relative to `/data_remote`, for example `pc-comfyui/checkpoints`; the browser sees target-relative folder paths only.
- `receiver` targets send files to the PC-side HugCivi Receiver HTTP API. They store the Receiver URL, optional token, base path, and copy policy. The token is used only for outbound HTTP headers and is not returned in target list payloads or job payloads.
- `rclone` targets refer to rclone remotes by name. rclone credentials and host details live in `RCLONE_CONFIG`.

The settings transfer pane starts with category tabs: `종합`, `ComfyUI`, `Hugging Face`, `Civitai`, `Hitomi`, `Movie`, and `ASMR`. Selecting a category filters the registered-target list and changes the registration form's default allowed source prefixes. New targets store the selected category as optional copy-policy metadata so the settings UI can keep them in the intended category without changing transfer behavior. The registration path field shows a focus helper for the selected target type; for `local_mount`, Portainer bind targets such as `/data_remote/comfyui-models` are shown as the HugCivi input value `comfyui-models`, and the frontend strips that known `/data_remote/` prefix before saving.

In the `ComfyUI` category, the settings form includes editable `stable-diffusion/<route>` to destination-subfolder mappings. When the target base path looks like a ComfyUI models root, such as `comfyui-models` or `ComfyUI/models`, the UI fills default mappings like `stable-diffusion/checkpoints -> checkpoints`, `stable-diffusion/loras -> loras`, and `stable-diffusion/upscalers -> upscale_models`. If that untouched default preset is later changed to a single destination folder path, the UI clears the preset mappings so files do not land under duplicated subfolders. Saving stores non-empty mappings in optional copy-policy metadata as `comfyui_mappings`. During normal transfer, if the browser sends no `destination_subpath`, HugCivi applies the first matching mapping to the source path; an explicitly selected destination still takes priority. Civitai model archives with `_civitai_metadata.json` keep their model/version context during transfer, so an archive folder such as `stable-diffusion/loras/sdxl/example-model/version_456` lands under `loras/example-model/version_456`, and a single primary file from that archive lands under `loras/example-model/version_456/<file>`.

For `local_mount` targets, HugCivi requires `DATA_REMOTE_DIR` to be separate from `DATA_ROOT`, refuses `/data_remote` root as a target, rejects symlink escapes, and uses temp-file plus rename copies with existing files skipped by default. It never accepts raw host paths, SMB URLs, IPs, or credentials from the browser.

For ComfyUI-oriented `local_mount` targets, the settings transfer pane can run a folder check through `POST /api/transfer/targets/{target_id}/comfyui/check`. The check reads only the registered target base under `DATA_REMOTE_DIR`, identifies ComfyUI `models` roots, ComfyUI roots, single model folders, aliases such as `unet`, `clip`, and `t2i_adapter`, returns HugCivi-to-ComfyUI mapping hints, and reports whether each saved `policy.comfyui_mappings` destination folder exists as a directory. It does not create folders and does not change the existing `/data` archive layout.

The settings transfer pane also has a `/data` root clone action for `local_mount` targets. It uses dedicated `/api/transfer/data-root/*` endpoints, does not accept browser-provided `source_path`, copies the contents of `/data` into the selected target/subfolder, and leaves existing destination files in place by default.

For Receiver targets, the `remote_path` field is the starting folder under the Receiver's mounted `/receive` root. During the `전송` modal flow, HugCivi proxies `GET /api/browse` through `/api/transfer/targets/{target_id}/receiver/tree` with the stored token, then sends the user-selected child folder as `destination_subpath`. Docker mount scope still determines what folders can appear.

| Key | Default | Source | Notes |
| --- | --- | --- | --- |
| `TRANSFER_MAX_CONCURRENT` | `1` | env | Max copy-only transfer jobs allowed to run at once inside the internal job scheduler. |
| `TRANSFER_DEFAULT_TRANSFERS` | `1` | env | Default rclone `--transfers` value when the target policy does not override it. |
| `TRANSFER_DEFAULT_CHECKERS` | `2` | env | Default rclone `--checkers` value when the target policy does not override it. |
| `TRANSFER_DEFAULT_BWLIMIT` | `40M` | env | Default rclone bandwidth limit when the target policy does not override it. Empty disables the default limit. |
| `TRANSFER_RECEIVER_TIMEOUT_SECONDS` | `300` | env | HTTP socket timeout for HugCivi Receiver job/create/upload/complete requests. Clamped to 1-3600 seconds. |

## Provider-Specific Runtime

| Key | Default | Source | Notes |
| --- | --- | --- | --- |
| `HF_XET_HIGH_PERFORMANCE` | `0` | env | Hugging Face Xet behavior. |
| `HF_XET_NUM_CONCURRENT_RANGE_GETS` | `4` | env | Hugging Face Xet concurrency. |
| `HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY` | `1` | env | Hugging Face Xet disk behavior. |
| `CIVITAI_API_BASE` | `https://civitai.com/api/v1` | env | Used for model/version metadata, image metadata, model-file tensor metadata, and refresh metadata. Override only for tests or compatible mirrors. |
| `CIVITAI_IMAGE_RESOURCE_RETRY_DELAY_SECONDS` | `86400` | env/settings fallback | Delay before retrying non-permanent Civitai image resource failures. There is no current visible UI field for this value. |
| `CIVITAI_IMAGE_MAX_RESOURCE_JOBS` | `30` | env | Max child jobs created from one Civitai image page. |
| `ASMRONE_API_BASE` | `https://api.asmr.one/api` | env | Work and tracks metadata API base. File bodies are downloaded from each track `mediaDownloadUrl` with `action=download`; `mediaStreamUrl` is ignored. Override only for tests or compatible ASMR.one API mirrors. |
| `HITOMI_BACKEND` | `auto` | env | `auto`, gallery-dl first with native fallback. |
| `HITOMI_IMAGE_FORMAT` | `webp` | env | Preferred native Hitomi image format. |
| `HITOMI_LISTING_QUEUE_MODE` | `auto` | env/UI | `auto` or `confirm`. |
| `HITOMI_LISTING_MAX_GALLERIES` | `500` | env | Child cap for listing discovery. |
| `GALLERY_DL_SLEEP_REQUEST_SECONDS` | `1.5` | env | Passed into gallery-dl config. |
| `YT_DLP_METADATA_PROBE_TIMEOUT_SECONDS` | `45` | env | Timeout for yt-dlp metadata probing used to choose YouTube channel folders. |
| `YT_DLP_SUBTITLE_PROBE_TIMEOUT_SECONDS` | `45` | env | Timeout for subtitle probing. |

Civitai model archive sidecars, preview image saves, required component downloads, refresh reuse of existing files, and viewer local component checks do not have separate settings. They use the Civitai provider queue, `CIVITAI_API_BASE`, `CIVITAI_TOKEN` when present, and the normal download retry/throttle settings.

ASMR.one Unicode local paths and nonfatal per-file failures are runtime behavior, not configurable settings. A job succeeds when at least one downloadable leaf is saved; failed leaves are recorded in ASMR.one sidecars.

## YouTube Subscriptions

These settings affect the independent YouTube subscription discovery layer. They do not change the normal one-shot download queue.

| Key | Default | Source | Notes |
| --- | --- | --- | --- |
| `SUBSCRIPTION_CHECK_SCHEDULER_ENABLED` | `1` | env | If false, subscriptions can still be managed and checked manually, but scheduled discovery does not run. |
| `SUBSCRIPTION_CHECK_POLL_SECONDS` | `60` | env | Scheduler wake interval while no subscription is immediately due. |
| `SUBSCRIPTION_STARTUP_JITTER_MIN_SECONDS` | `30` | env | Minimum startup delay before the check scheduler starts due work. |
| `SUBSCRIPTION_STARTUP_JITTER_MAX_SECONDS` | `300` | env | Maximum startup delay before the check scheduler starts due work. |
| `SUBSCRIPTION_DISCOVERY_TIMEOUT_SECONDS` | `90` | env | Timeout for yt-dlp flat metadata discovery used by manual and scheduled checks. |
| `SUBSCRIPTION_DOWNLOAD_SCHEDULER_ENABLED` | `1` | env | If false, discovery still runs but eligible items are not downloaded automatically. |
| `SUBSCRIPTION_DOWNLOAD_POLL_SECONDS` | `30` | env | Subscription download worker wake interval while no item is immediately ready. |
| `SUBSCRIPTION_DOWNLOAD_MAX_ATTEMPTS` | `3` | env | Max automatic attempts per subscription item before it stays failed. |

Current implementation status:

- Scheduled checks discover videos into `subscription_items`.
- Subscription checks do not create normal `jobs` rows.
- If `auto_queue` is enabled, the independent subscription download worker downloads eligible items without creating normal `jobs` rows.

## Internal Jobs, Archive, Media, Index

| Key | Default | Source | Notes |
| --- | --- | --- | --- |
| `INTERNAL_JOB_MAX_CONCURRENT` | `2` | env | Global internal job concurrency. Use `1` on weak NAS hardware. |
| `INTERNAL_JOB_MAINTENANCE_MODE` | `immediate` | env/settings fallback | Heavy internal job start policy: `immediate`, `window`, or `paused`. Applies to ZIP, library reindex, media transcode/poster, and thumbnail backfill jobs. |
| `INTERNAL_JOB_MAINTENANCE_JOB_KINDS` | built-in heavy set | env/settings fallback | Optional comma/semicolon-separated override for which internal `job_kind` values are gated by maintenance mode. Leave unset unless debugging a specific scheduler policy. |
| `INTERNAL_JOB_MAINTENANCE_START_HOUR` | `1` | env/settings fallback | Server-local start hour, 0-23, used when `INTERNAL_JOB_MAINTENANCE_MODE=window`. |
| `INTERNAL_JOB_MAINTENANCE_END_HOUR` | `6` | env/settings fallback | Server-local end hour, 0-23. Same start/end means all day. |
| `DOWNLOAD_ARCHIVE_TTL_SECONDS` | `86400` | env | Stale ZIP cleanup age. |
| `DOWNLOAD_ARCHIVE_MAX_CONCURRENT` | `1` | env | ZIP creation semaphore. |
| `DOWNLOAD_ARCHIVE_MAX_FILES` | `50000` | env | ZIP preflight file cap. |
| `DOWNLOAD_ARCHIVE_MAX_SOURCE_BYTES` | `0` | env | `0` means no source size cap. |
| `DOWNLOAD_ARCHIVE_MIN_FREE_BYTES` | `0` | env | Extra free-space requirement before ZIP. |
| `MEDIA_TRANSCODE_MAX_CONCURRENT` | `1` | env | ffmpeg transcode semaphore, also shared by lazy card thumbnail generation. |
| `MEDIA_TRANSCODE_TIMEOUT_SECONDS` | `1800` | env | ffmpeg timeout. |
| `MEDIA_TRANSCODE_PRESET` | `veryfast` | env | ffmpeg x264 preset. |
| `MEDIA_TRANSCODE_CRF` | `23` | env | ffmpeg quality. |
| `MEDIA_TRANSCODE_AUDIO_BITRATE` | `160k` | env | ffmpeg audio bitrate. |
| `MEDIA_CACHE_TTL_SECONDS` | `2592000` | env/settings fallback | Media cache cleanup age for transcodes, posters, and card thumbnails. `0` disables TTL cleanup. |
| `MEDIA_CACHE_MAX_BYTES` | `0` | env/settings fallback | `0` means no media cache size cap. Applies to all files under `MEDIA_CACHE_DIR`; quota cleanup removes least-recently-accessed files first. |
| `MEDIA_THUMBNAIL_BACKFILL_WORKERS` | `3` | env/settings fallback | Worker threads used by selected-folder card thumbnail backfill jobs. Clamped to 1-16 and still subject to the ffmpeg media semaphore. |
| `MEDIA_THUMBNAIL_BACKFILL_MAX_ITEMS` | `5000` | env/settings fallback | Max library card items scanned when queueing a thumbnail backfill job. Clamped to 1-20000. |
| `LIBRARY_WATCHER_ENABLED` | `0` | env/settings fallback | Stores the opt-in watcher policy. Current build reports status through `/api/library/watcher` but does not start a watcher worker. |
| `LIBRARY_WATCHER_LOCAL_ONLY` | `1` | env/settings fallback | Reserved watcher safety policy for local filesystems. Network/rclone-style storage should keep explicit reindex as authoritative. |
| `MEDIA_VIDEO_PREVIEW_MODE` | `off` | env/settings fallback | Reserved video preview/trickplay policy: `off`, `keyframes`, or `full`. Current build stores/reports policy only and does not enqueue preview jobs. |
| `MEDIA_FILE_SCAN_MAX_FILES` | `5000` | env | Limits media scans. |
| `WORKFLOW_IMPORT_MAX_BYTES` | `104857600` | env | Max uploaded ComfyUI workflow PNG/JSON size. Minimum enforced value is 1 MiB. |
| `LIBRARY_ITEM_SIZE_SCAN_MAX_FILES` | `2000` | env | Limits library item size scans. |
| `LIBRARY_INDEXER_START_DELAY_SECONDS` | `5` | env | Delay before background indexer starts. |
| `LIBRARY_INDEXER_INTERVAL_SECONDS` | `300` | env | Background index interval. |
| `LIBRARY_INDEX_BATCH_SIZE` | `300` | env | Normal indexer batch size. |
| `LIBRARY_REINDEX_BATCH_SIZE` | `5000` | env | Per-batch path budget for `library_reindex` internal jobs. |
| `LIBRARY_SYNC_MAX_PATHS` | `2000` | env | Per-request path budget for quick scoped `/api/library/sync` reconcile work used by the browser `갱신` action. |
| `LIBRARY_SYNC_PRUNE_LIMIT` | `1000` | env | Maximum indexed rows checked for missing files during one quick scoped `/api/library/sync` request. |
| `LIVE_LIBRARY_PAGE_CACHE_TTL_SECONDS` | `60` | env | Short in-memory cache TTL for completed selected-folder live page scans. Page/sort changes reuse the scanned card list until the folder signature changes or the TTL expires. Set `0` to disable. |
| `STORAGE_USAGE_SCAN_BATCH_SIZE` | `1000` | env | Storage usage scan batch size. |
| `STORAGE_USAGE_SCAN_SLEEP_SECONDS` | `0.02` when unset | env | Sleep between storage scan batches. |
| `JOB_LOG_MAX_CHARS` | `200000` | env | Stored job log trim limit. |
| `SQLITE_VACUUM_AFTER_CLEAR` | `0` | env | If truthy, `POST /api/jobs/clear` runs `VACUUM` after deleting inactive job history. Keep disabled during normal NAS use. |

`GET /api/library?path=<relative-data-path>&limit=50&page=<n>` uses the SQLite index for selected-folder pages and accepts optional `source_group`/`category` filters. In normal `index` mode, missing indexed rows return quickly with `index_status.needs_refresh`/`refreshing` instead of automatically live-scanning the filesystem. Selected-folder index responses may include additive `index_status.folder_state` from the `library_folder_state` table when a scoped reindex or sync has recorded folder scan progress. `POST /api/library/sync` performs a bounded scoped reconcile without deleting existing rows first; the browser `갱신` action uses this route for fast DB persistence before any heavier rebuild. `GET /api/library?mode=live&path=<relative-data-path>` explicitly performs a selected-folder live scan for manual recovery. Completed selected-folder live scans return known `total_count` and `total_pages`, so ordinary folders show a full page list; scans that hit the internal path budget keep totals unknown and use previous/current/next navigation. The completed selected-folder live item list is reused for page/sort navigation through `LIVE_LIBRARY_PAGE_CACHE_TTL_SECONDS`; app-driven folder create/rename/move/delete, library sync/reindex, and job-history clear operations invalidate that cache immediately. Library/job card payloads include `thumbnail_ready`; the browser uses it to request already-cached thumbnails with a faster 10-request lane while leaving cold/generating thumbnail requests capped at 3. `GET /api/media/thumbnail` returns file-versioned JPEG URLs with long-lived private immutable `Cache-Control` for browser cache hits and updates cache access time for LRU cleanup. `GET /api/media/cache` reports media cache size/category/policy, and `POST /api/media/cache/cleanup` can run TTL/quota cleanup or clear the thumbnail scope. `POST /api/library/reindex` can take optional `path`, `source_group`, and `category` query parameters and queues or reuses a heavier `library_reindex` internal job that reports `queued`, `job_id`, `scope`, additive `deduped`, and a decorated job summary. `POST /api/media/thumbnail-jobs` queues an internal job for the selected folder's missing card-representative thumbnails; it uses `MEDIA_THUMBNAIL_BACKFILL_WORKERS` unless an API caller supplies a clamped `workers` value. `GET /api/library/watcher` and `GET /api/media/video-preview` expose disabled-by-default policy status for future watcher/trickplay work. `POST /api/jobs/clear` also clears the library index when it deletes inactive rows, so sidecar-backed cards can be restored from disk through explicit live mode, quick sync, background indexing, or a `library_reindex` job; `SQLITE_VACUUM_AFTER_CLEAR` still only controls whether a `VACUUM` follows the delete.

## Compose Default Differences

| Setting | `docker-compose.yml` | `portainer-stack.yml` |
| --- | --- | --- |
| Image/build | builds local checkout | uses `HUGCIVI_IMAGE` |
| `/data` host path | `./data` | `/volume1/docker/nas-model-archiver/models` |
| `/config` host path | `./config` | `/volume1/docker/nas-model-archiver/config` |
| `DOWNLOAD_STALL_TIMEOUT_SECONDS` | `600` | `0` |
| `YT_DLP_FORMAT` | H.264 MP4 first | `best[ext=mp4]/best` |
| healthcheck | none | authenticated HTTP check |
