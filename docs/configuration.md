# HugCivi Configuration Reference

Last updated: 2026-07-04

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
| `QUEUE_PER_PROVIDER_LIMIT` | `1` | env/UI | Concurrent jobs per provider bucket. |
| `QUEUE_PER_PROVIDER_LIMIT_HARD_LIMIT` | `4` | env | Upper cap for UI-saved provider limit. |
| `QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS` | `2` | env/UI | Minimum provider cooldown. |
| `QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS` | `2` | env/UI | Maximum provider cooldown. |
| `QUEUE_PROVIDER_COOLDOWN_SECONDS` | `2` | env/UI legacy | Compatibility fallback used when min/max cooldown values are absent. Prefer the min/max keys. |
| `DOWNLOAD_STALL_TIMEOUT_SECONDS` | Docker/local `600`, Portainer `0` | env/UI | `0` disables watchdog timeout. |
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

## Provider-Specific Runtime

| Key | Default | Source | Notes |
| --- | --- | --- | --- |
| `HF_HUB_DOWNLOAD_TIMEOUT` | `120` | env | Hugging Face hub timeout. |
| `HF_XET_HIGH_PERFORMANCE` | `0` | env | Hugging Face Xet behavior. |
| `HF_XET_NUM_CONCURRENT_RANGE_GETS` | `4` | env | Hugging Face Xet concurrency. |
| `HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY` | `1` | env | Hugging Face Xet disk behavior. |
| `HF_SNAPSHOT_MAX_WORKERS` | `2` | env | Hugging Face snapshot worker count. |
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
| `MEDIA_CACHE_TTL_SECONDS` | `2592000` | env | Media cache cleanup age for transcodes, posters, and card thumbnails. |
| `MEDIA_CACHE_MAX_BYTES` | `0` | env | `0` means no media cache size cap. Applies to all files under `MEDIA_CACHE_DIR`. |
| `MEDIA_FILE_SCAN_MAX_FILES` | `5000` | env | Limits media scans. |
| `WORKFLOW_IMPORT_MAX_BYTES` | `104857600` | env | Max uploaded ComfyUI workflow PNG/JSON size. Minimum enforced value is 1 MiB. |
| `LIBRARY_ITEM_SIZE_SCAN_MAX_FILES` | `2000` | env | Limits library item size scans. |
| `LIBRARY_INDEXER_START_DELAY_SECONDS` | `5` | env | Delay before background indexer starts. |
| `LIBRARY_INDEXER_INTERVAL_SECONDS` | `300` | env | Background index interval. |
| `LIBRARY_INDEX_BATCH_SIZE` | `300` | env | Normal indexer batch size. |
| `LIBRARY_REINDEX_BATCH_SIZE` | `5000` | env | Manual reindex batch size. |
| `STORAGE_USAGE_SCAN_BATCH_SIZE` | `1000` | env | Storage usage scan batch size. |
| `STORAGE_USAGE_SCAN_SLEEP_SECONDS` | `0.02` when unset | env | Sleep between storage scan batches. |
| `JOB_LOG_MAX_CHARS` | `200000` | env | Stored job log trim limit. |
| `SQLITE_VACUUM_AFTER_CLEAR` | `0` | env | If truthy, `POST /api/jobs/clear` runs `VACUUM` after deleting inactive job history. Keep disabled during normal NAS use. |

`GET /api/library?mode=live&path=<relative-data-path>` performs a selected-folder live scan. The browser requests 50-card pages with `limit=50&page=<n>` and no separate page-size setting. Live scans are controlled by the same scan budgets as live library fallback. `POST /api/jobs/clear` also clears the library index when it deletes inactive rows, so sidecar-backed cards can be restored from disk; `SQLITE_VACUUM_AFTER_CLEAR` still only controls whether a `VACUUM` follows the delete.

## Compose Default Differences

| Setting | `docker-compose.yml` | `portainer-stack.yml` |
| --- | --- | --- |
| Image/build | builds local checkout | uses `HUGCIVI_IMAGE` |
| `/data` host path | `./data` | `/volume1/docker/nas-model-archiver/models` |
| `/config` host path | `./config` | `/volume1/docker/nas-model-archiver/config` |
| `DOWNLOAD_STALL_TIMEOUT_SECONDS` | `600` | `0` |
| `YT_DLP_FORMAT` | H.264 MP4 first | `best[ext=mp4]/best` |
| healthcheck | none | authenticated HTTP check |
