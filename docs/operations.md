# HugCivi Operations Guide

Last updated: 2026-07-06

This guide covers the operational behavior that matters on Synology NAS, Portainer, or a similar Docker host.

## Persistent Volumes

HugCivi needs two persistent mounts:

```text
/data
/config
```

Recommended Synology defaults from `portainer-stack.yml`:

```text
/volume1/docker/nas-model-archiver/models  -> /data
/volume1/docker/nas-model-archiver/config  -> /config
```

`/data` contains the archive files. If downloaded local files are the only thing that must survive, protect this mount first.

`/config` contains the DB, settings, cached ZIP/media artifacts, and backups. Keep it if you want job history, settings, favorites, notes, and library index state.

Do not run two HugCivi containers against the same `/config/jobs.sqlite3` at the same time.

`portainer-stack.yml` is the production/NAS reference. `docker-compose.yml` is for local development or hosts where building from the checkout is intentional. Some defaults differ between them; document and set the values you care about in Portainer environment variables instead of relying on implicit image defaults.

## Upgrade Checklist

1. Stop the existing HugCivi container.
2. Confirm the `/data` bind mount points to the expected NAS folder.
3. Back up `/config/jobs.sqlite3`.
4. Pull or deploy the new image. The normal production tag is `ghcr.io/mephiblin/hugcivi:latest`; use `ghcr.io/mephiblin/hugcivi:sha-<commit>` if you want to pin a specific pushed build.
5. Start exactly one container with the same `/data` and `/config` mounts.
6. Open the UI and confirm the library and job list load.

Container deletion is safe for archive files only if Portainer does not delete the bind-mounted host folders or named volumes. Avoid options such as removing volumes or deleting persistent data unless you have a separate backup.

## Recommended NAS Defaults

For a small or older NAS:

```text
MAX_CONCURRENT_DOWNLOADS=2
QUEUE_PER_PROVIDER_LIMIT=1
QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS=2
QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS=5
DOWNLOAD_STALL_TIMEOUT_SECONDS=600
INTERNAL_JOB_MAX_CONCURRENT=1
DOWNLOAD_REQUEST_MIN_INTERVAL_SECONDS=1.5
GALLERY_DL_SLEEP_REQUEST_SECONDS=2
YT_DLP_FORMAT=best[ext=mp4][vcodec^=avc1]/best[ext=mp4]/best
```

For a stronger box, increase one value at a time. If the UI, disk, or network becomes unstable, lower internal jobs first for CPU/I/O pressure and download concurrency first for provider/network pressure.

## Queue Controls

Download queue settings apply to external downloads:

- Hugging Face
- Civitai
- Hitomi gallery child jobs
- ASMR.one work downloads
- gallery-dl
- yt-dlp/YouTube
- generic HTTP/HTTPS files
- ComfyUI workflow URL downloads

Settings:

| Setting | Meaning |
| --- | --- |
| `MAX_CONCURRENT_DOWNLOADS` | Global external download job limit. |
| `QUEUE_PER_PROVIDER_LIMIT` | Concurrent jobs allowed for the same provider bucket. Hugging Face snapshot internal workers stay fixed at 1 so this provider limit does not multiply. |
| `QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS` | Minimum cooldown after a provider job finishes. |
| `QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS` | Maximum cooldown after a provider job finishes. |
| `DOWNLOAD_STALL_TIMEOUT_SECONDS` | Watchdog timeout for jobs with no detected progress. Hugging Face Hub response waits also follow this value. `0` disables the timeout semantics. |

Internal server-local jobs use a separate limit:

| Setting | Meaning |
| --- | --- |
| `INTERNAL_JOB_MAX_CONCURRENT` | Concurrent ZIP/transcode/poster jobs. Default is `2`; `1` is recommended on modest NAS hardware. |

Current default note: `portainer-stack.yml` sets `DOWNLOAD_STALL_TIMEOUT_SECONDS` to `${DOWNLOAD_STALL_TIMEOUT_SECONDS:-0}`, while the Dockerfile and local compose path use `600`. If you want stalled downloads to be stopped automatically in Portainer, set this value explicitly.

## Copy-Only Transfer

The `전송` context-menu action copies an existing `/data` file or folder to a registered outbound target. The browser loads targets from `/api/transfer/targets`, checks the selected source with `/api/transfer/preflight`, then creates a `transfer_copy` internal job with `/api/transfer/jobs`. Transfer jobs appear in the normal job list as `Transfer`.

Recommended internal-LAN PC/NAS transfer is a `연결 폴더 (/data_remote)` target. Mount PC SMB shares, Synology remote folders, or other host-managed folders under a host directory, bind that directory to `/data_remote`, then register one or more `local_mount` targets with a `/data_remote`-relative base path. HugCivi browses only the registered target base through `/api/transfer/targets/{target_id}/local-mount/tree`, sends only `target_id`, `/data` `source_path`, and `destination_subpath`, and never accepts raw host paths, SMB URLs, IPs, or credentials from the browser.

For Civitai image-page archives, the library card action row and context menu have `사용 리소스 전송`. This checks the image archive's Resources used metadata, finds locally present model-version archives through jobs/sidecars, resolves each present resource to its primary model file, and queues separate `transfer_copy` jobs through `/api/transfer/civitai-resources/jobs`. With a ComfyUI category target that stores `policy.comfyui_mappings`, checkpoint/LoRA/VAE-style files are placed under the configured ComfyUI model subfolders by their HugCivi `stable-diffusion/...` source path.

For a one-shot archive clone, open Settings -> `전송 대상` -> `종합` -> `/data 전체 복제`, choose a `local_mount` target, optionally enter a destination subfolder, and queue the job. This uses `/api/transfer/data-root/preflight` and `/api/transfer/data-root/jobs`, so the browser does not send a mutable source path. The job copies `/data` contents directly into the selected target/subfolder, skips existing files by default, and still refuses overlapping `/data` and `/data_remote` mounts.

Local mount compose shape:

```yaml
services:
  hugcivi:
    volumes:
      - /volume1/hugcivi/data:/data
      - /volume1/hugcivi/config:/config
      - /volume1/hugcivi/remotes:/data_remote
```

Host layout example:

```text
/volume1/hugcivi/remotes/
  pc-comfyui/
    checkpoints/
    loras/
  studio-nas/
  friend-drop/
```

Register separate targets for narrow destinations and policies:

```text
PC ComfyUI Checkpoints -> kind=local_mount, remote_path=pc-comfyui/checkpoints
PC ComfyUI LoRA        -> kind=local_mount, remote_path=pc-comfyui/loras
Studio NAS Models      -> kind=local_mount, remote_path=studio-nas/models
```

For a ComfyUI install mounted at its models root, you can instead register one `ComfyUI` category target such as `remote_path=pc-comfyui/ComfyUI/models` or `remote_path=pc-comfyui/comfyui-models`. The settings form fills editable mappings from fixed HugCivi sources like `stable-diffusion/checkpoints` and `stable-diffusion/loras` to destination subfolders like `checkpoints` and `loras`; those mappings become the default transfer destination when the transfer modal has not selected another folder.

`/data_remote` is not a second library root. It is destination-only for copy jobs, must stay separate from `/data`, and should be mounted as narrowly as practical. HugCivi refuses the `/data_remote` root as a target, rejects symlink escapes, checks writable/offline state during preflight, writes files through temporary names before rename, and skips existing destination files by default.

Use the sibling HugCivi Receiver project at `/home/inri/문서/HugCivi-Receiver` when the PC-side receiving UI or HTTP token boundary is useful. Run it on the PC as a Docker container, mount the PC folder to `/receive`, then register a `HugCivi Receiver` target in HugCivi with the Receiver URL, token, base path, allowed source prefix, and include patterns. The PC browser UI shows waiting, receiving, done, and failed jobs. Docker still cannot let the web UI pick arbitrary Windows folders; the local folder must be mounted in compose first. Once mounted, HugCivi can query the Receiver folder tree through `/api/transfer/targets/{target_id}/receiver/tree` and the `전송` modal lets the user pick a destination under that mounted receive root.

Receiver compose shape:

```yaml
services:
  hugcivi-receiver:
    build: .
    ports:
      - "8088:8080"
    environment:
      RECEIVER_API_TOKEN: "replace-with-a-long-token"
      RECEIVER_DATA_DIR: "/receive"
      RECEIVER_CONFIG_DIR: "/config"
    volumes:
      - "./receiver-config:/config"
      - "D:/ComfyUI/models:/receive"
```

For mixed destinations, mount several host folders under `/receive` and leave the HugCivi target base path broad enough for selection:

```yaml
volumes:
  - "./receiver-config:/config"
  - "D:/ComfyUI/models:/receive/comfyui"
  - "E:/Videos/YouTube:/receive/youtube"
  - "E:/Archive/Gallery:/receive/gallery"
```

Mounting a very high-level folder such as a drive root also works mechanically, but it gives the Receiver write access to that whole tree. Prefer the narrowest common parent that still gives the desired transfer UX.

rclone remote credentials live outside SQLite:

```text
/config/rclone/rclone.conf
```

Prepare this file through `rclone config --config /config/rclone/rclone.conf` inside the container or by mounting a prepared config file. rclone target rows store the remote name, base path, enabled state, and policy only; do not put passwords or raw host details into target names or notes. Receiver targets store a Receiver URL and token in SQLite; the API does not return the token in target list or job payloads.

Useful defaults:

```text
RCLONE_CONFIG=/config/rclone/rclone.conf
DATA_REMOTE_DIR=/data_remote
TRANSFER_MANIFEST_DIR=/config/transfer-manifests
TRANSFER_MAX_CONCURRENT=1
TRANSFER_DEFAULT_TRANSFERS=1
TRANSFER_DEFAULT_CHECKERS=2
TRANSFER_DEFAULT_BWLIMIT=40M
TRANSFER_RECEIVER_TIMEOUT_SECONDS=300
```

Recommended Receiver setup for ComfyUI checkpoints:

```text
Windows folder: D:\ComfyUI\models
Receiver mount: D:/ComfyUI/models:/receive
Receiver URL: http://PC_IP:8088
Receiver token: long random token shared with HugCivi target settings
PC IP: DHCP reservation or static IP
HugCivi source prefix: stable-diffusion/checkpoints
Receiver base path: checkpoints
Include patterns: *.safetensors, *.ckpt
```

Keep `TRANSFER_MAX_CONCURRENT=1` on NAS hardware until the destination disk behavior is proven stable. If the PC is asleep, a mount is offline, or a Receiver is unavailable, the transfer job fails without changing local `/data` files; fix the mount/Receiver/network condition and retry the job. For broader external-network remotes, keep using rclone targets.

## Civitai Model Archives

Civitai model/version downloads are ordinary external download jobs in the `civitai` provider bucket, so global/per-provider queue limits, cooldowns, retries, and the stall watchdog apply.

Model archive outputs include the model files plus `_civitai_metadata.json`. When Civitai returns generation/example image data, HugCivi also writes `_civitai_generation_metadata.json` and local preview files named `civitai_example_<imageId>.*`. These sidecars carry model/version/file details, generation prompts, tensor summary data when available, preview local paths, and `component_downloads`, so cards and viewer metadata can recover from disk without job history.

For normal model/version URLs, HugCivi downloads the primary model file plus additional Civitai files whose metadata marks them as required. These component files are downloaded in the same job and same archive folder; the job progress/filename may show `N files`. Explicit file selectors or raw Civitai download URLs keep the narrower requested-file behavior.

The model-card `갱신` action queues a Civitai refresh job against the existing archive folder. Refresh requires a Civitai model folder with usable sidecar metadata and no active jobs under that folder. It keeps the existing primary file and any existing expected component files, downloads missing required components, and refreshes sidecars/previews rather than deleting and rebuilding the folder.

The media viewer's Civitai health check uses `/api/civitai/resource-health`. Image-page archives check referenced model-version resources from jobs/sidecars; the card/context `사용 리소스 전송` flow reuses that local presence information before creating copy-only transfer jobs for present primary files. Model archives also send the archive path and `component_downloads`, so `Check components` verifies whether local required files are present in that folder.

## ASMR.one Downloads

ASMR.one `/work/RJ...` and `/work/<id>/DLSITE/RJ...` URLs run as ordinary external download jobs in the `asmrone` provider bucket, so the global/per-provider queue limits and provider cooldown settings apply.

The handler uses `ASMRONE_API_BASE` for work and track metadata, but file bodies are downloaded from each leaf track `mediaDownloadUrl` with `action=download`. `mediaStreamUrl` is intentionally ignored. Output defaults under `/data/asmr.one/...` unless the user chose a target folder, with downloaded track files plus `_asmrone_metadata.json`, redacted `_asmrone_tracks.json`, `_asmrone_manifest.json`, and `_archive_metadata.json`.

Downloaded ASMR.one audio files are recognized by the media viewer and play through the browser audio element. Non-audio files with a download URL are still stored in the work folder. Japanese and other Unicode track/folder names are preserved in local paths and manifest entries.

Per-file failures are nonfatal when at least one file is saved. Missing optional images, text files, or other leaves are marked with `download_status: failed` and `download_error` in `_asmrone_manifest.json`, `failed_file_count` is written to the ASMR.one sidecars, and empty failed child folders are cleaned up. If every downloadable leaf fails, the job still fails. Audio does not use the internal video transcode/poster queue.

## YouTube Subscriptions

The `구독` sidebar tab manages YouTube channel and playlist subscriptions separately from the normal job list.

Operational behavior:

- Scheduled subscription checks use yt-dlp flat metadata discovery.
- Discovered videos are stored in SQLite `subscription_items`.
- Scheduled checks do not create normal download jobs.
- If `auto_queue` is enabled, the independent subscription download worker downloads eligible items without adding them to the normal job list.
- Manual one-shot YouTube downloads still use the regular external download queue.
- Default YouTube subtitle downloads are best-effort. A subtitle-only HTTP 429 does not fail the item when the media file itself was saved.

Useful controls:

```text
SUBSCRIPTION_CHECK_SCHEDULER_ENABLED=1
SUBSCRIPTION_CHECK_POLL_SECONDS=60
SUBSCRIPTION_STARTUP_JITTER_MIN_SECONDS=30
SUBSCRIPTION_STARTUP_JITTER_MAX_SECONDS=300
SUBSCRIPTION_DISCOVERY_TIMEOUT_SECONDS=90
SUBSCRIPTION_DOWNLOAD_SCHEDULER_ENABLED=1
SUBSCRIPTION_DOWNLOAD_POLL_SECONDS=30
SUBSCRIPTION_DOWNLOAD_MAX_ATTEMPTS=3
```

Set `SUBSCRIPTION_CHECK_SCHEDULER_ENABLED=0` if you want to keep subscriptions in manual-check mode only.

## yt-dlp Proxy

Some video sites may load in a browser but fail from the HugCivi server process because yt-dlp leaves through the server's direct network path. Use `YT_DLP_PROXY` when yt-dlp-supported sources need an HTTP, HTTPS, SOCKS4, or SOCKS5 proxy:

```text
YT_DLP_PROXY=socks5://192.168.200.100:1080
```

The same value can be saved in the web UI settings modal as `YouTube/yt-dlp Proxy`. UI-saved values are stored in `/config/jobs.sqlite3` and take precedence over environment variables. Authenticated proxy URLs can contain credentials, so treat DB backups as credential backups.

The current Docker host has a discovered `byedpi` container from image `tazihad/byedpi`, publishing SOCKS5 on `192.168.200.100:1080`. See [ByeDPI SOCKS5 Proxy Guide](byedpi-socks-proxy.md) for the Portainer compose example and HugCivi setup steps.

This setting only affects yt-dlp-backed downloads and yt-dlp metadata probes, including YouTube, xHamster, Pornhub, and other preferred yt-dlp video hosts. It does not proxy Hugging Face, Civitai, generic HTTP downloads, native Hitomi requests, or internal server-local ZIP/media jobs.

Default YouTube subtitle downloads are treated as optional sidecars. HugCivi still requires at least one non-subtitle media file before marking a yt-dlp job successful, so a subtitle-only partial result is cleaned up or failed instead of becoming a library item.

## Expensive Local Work

Folder downloads:

- Direct file downloads use `/api/fs/download`.
- Folder downloads create an `archive_zip` internal job.
- ZIP files are written under `/config/downloads`.
- Stale ZIP cleanup uses `DOWNLOAD_ARCHIVE_TTL_SECONDS`.

Media:

- Browser-playable videos are served directly.
- Audio files are served directly in the media viewer.
- Unplayable videos create `media_transcode` jobs on demand.
- Missing video posters create `media_poster` jobs on demand.
- Library/job card image thumbnails are generated lazily as small JPEG files under `/config/media-cache/thumbnails`.
- The library `썸네일 생성` button queues a `media_thumbnail_backfill` internal job for the selected folder. It scans card representatives only, skips thumbnails already cached, and defaults to 3 worker threads.
- Media cache files live under `/config/media-cache`.
- First visit or first load of existing files may spend CPU/I/O creating uncached thumbnails, but the browser queues requests for cards in or near the viewport and runs at most 3 thumbnail requests at a time; it should not send 100 thumbnail generations at once.

Useful media/cache settings:

```text
MEDIA_TRANSCODE_MAX_CONCURRENT=1
MEDIA_TRANSCODE_TIMEOUT_SECONDS=1800
MEDIA_TRANSCODE_PRESET=veryfast
MEDIA_TRANSCODE_CRF=23
MEDIA_TRANSCODE_AUDIO_BITRATE=160k
MEDIA_CACHE_TTL_SECONDS=2592000
MEDIA_CACHE_MAX_BYTES=0
MEDIA_THUMBNAIL_BACKFILL_WORKERS=3
MEDIA_THUMBNAIL_BACKFILL_MAX_ITEMS=5000
DOWNLOAD_ARCHIVE_TTL_SECONDS=86400
DOWNLOAD_ARCHIVE_MAX_CONCURRENT=1
DOWNLOAD_ARCHIVE_MAX_FILES=50000
DOWNLOAD_ARCHIVE_MAX_SOURCE_BYTES=0
DOWNLOAD_ARCHIVE_MIN_FREE_BYTES=0
```

`0` usually means unlimited or disabled for max/threshold-style settings. Use conservative values if the NAS volume is tight. `MEDIA_CACHE_TTL_SECONDS` and `MEDIA_CACHE_MAX_BYTES` apply to transcodes, posters, and thumbnail files together. Thumbnail generation shares `MEDIA_TRANSCODE_MAX_CONCURRENT`, so lowering it also limits image thumbnail ffmpeg work. Thumbnail backfill jobs use 3 queue workers by default, but each ffmpeg invocation still passes through the media semaphore.

Deploy note: no DB migrations or volume changes are required for thumbnail backfill jobs. Rebuild and redeploy the web image to ship the new library action; existing `/config/media-cache` contents remain valid and the existing TTL/quota cleanup continues to cover `/config/media-cache/thumbnails`.

## Library Index

The library UI uses `library_items` when indexed rows are available.

Background controls:

```text
LIBRARY_INDEXER_START_DELAY_SECONDS=5
LIBRARY_INDEXER_INTERVAL_SECONDS=300
LIBRARY_INDEX_BATCH_SIZE=300
LIBRARY_REINDEX_BATCH_SIZE=5000
LIVE_LIBRARY_PAGE_CACHE_TTL_SECONDS=60
```

Operational notes:

- First indexing pass may take time on large archives.
- The browser requests library cards in 50-card pages. Legacy `/api/library` array responses still exist for compatibility, but the UI uses `limit=50&page=<n>` plus optional source filters.
- `/api/library?mode=live` can force live filesystem scanning.
- Normal selected-folder library pages query the SQLite index first and use live scanning only as fallback. `source_group` filters include `civitai`, `gallerydl`, `ytdlp`, `hitomi`, `asmrone`, `generic`, `huggingface`, `comfyui`, `media`, and `unknown`.
- `/api/library?mode=live&path=<relative-data-path>` explicitly live-scans only a selected folder. Completed selected-folder scans show known page totals and reuse a short in-memory item-list cache for page/sort navigation; very large or incomplete scans keep previous/current/next fallback navigation.
- Job polling no longer rebuilds all visible library cards for progress-only updates; a matching completed job refreshes the active library page.
- `/api/library/reindex` resets and scans a large batch. Optional `path`, `source_group`, and `category` query parameters refresh only that selected folder/provider/category scope.
- `POST /api/jobs/clear` resets the library index when it deletes inactive job rows and returns `library_index_reset: true`. The next library load may do a live scan or wait for reindexing; sidecar-backed Civitai and media cards can reappear from `/data` without job rows.
- App-driven create/rename/move/delete, manual library reindex, and inactive job-history clear invalidate the selected-folder live page cache immediately. NAS-side manual changes are picked up when the folder signature changes or the cache TTL expires.
- App-driven rename/move/delete updates the index and path-linked state.
- App-driven rename/move/delete updates the folder tree, library cards, and storage readout in place so the browser stays on the active folder when possible.
- NAS-side manual file changes are picked up by later indexer passes or live fallback. The sidebar `저장 폴더` refresh button immediately reloads the folder tree from the current `/data` filesystem view when an operator deletes or creates folders outside HugCivi.

## Storage Readout

The top storage readout always includes `/data` volume usage from the filesystem that backs the `/data` mount. This is volume usage, not the exact HugCivi folder size.

The `계산` button starts a manual background scan of `/data` and caches the HugCivi archive usage in SQLite. The UI reads the cached value; it does not recursively scan `/data` on every refresh.

The `애드온` button next to the storage readout downloads the Chrome extension package from `/api/addon/chrome-extension`. The package is generated from the bundled `chrome-extension/` directory and is Basic Auth protected like the rest of the UI.

NAS-safe scan controls:

```text
STORAGE_USAGE_SCAN_BATCH_SIZE=1000
STORAGE_USAGE_SCAN_SLEEP_SECONDS=0.02
```

Lower the batch size or raise the sleep value if the NAS feels busy during a manual calculation.

## Database Maintenance

Maintenance APIs are Basic Auth protected:

| Endpoint | Operation |
| --- | --- |
| `POST /api/maintenance/db/wal` | Set `journal_mode=WAL` or `DELETE`. |
| `POST /api/maintenance/db/checkpoint` | Run SQLite WAL checkpoint. |
| `POST /api/maintenance/db/optimize` | Run `PRAGMA optimize`. |
| `POST /api/maintenance/db/compact` | Run `VACUUM`. |
| `POST /api/maintenance/db/backup` | Use SQLite online backup API into `/config/backups`. |

Recommended backup approach:

1. Use `/api/maintenance/db/backup`, or stop the container before copying the DB.
2. Back up `/data` separately.
3. Treat DB backups as credential backups because settings may include tokens, passwords, cookie paths, and extra options.

If WAL is enabled, do not rely on copying only `jobs.sqlite3` while the app is running. Use the online backup API or checkpoint and copy the DB files consistently.

`GALLERY_DL_AUTO_UPDATE` is also written to `/config/startup.env` so the next container start can honor the UI setting. Treat that file as operational configuration and keep `/config` permissions restricted.

## Security

Minimum requirements:

- Set a long `APP_PASSWORD`.
- Keep the app off the public internet when possible.
- Put it behind VPN, private LAN, or a trusted reverse proxy.
- Restrict NAS permissions on `/config`.
- Back up `/config` carefully because it may contain credentials.
- Review site licenses and terms before archiving content.

The app refuses insecure placeholder passwords. The authenticated settings modal shows saved tokens, passwords, cookie paths, proxy URLs, and extra options in plaintext so they can be edited at runtime; keep the UI behind a trusted LAN, VPN, or reverse proxy.

## Recovery Notes

If `/config` is lost but `/data` remains:

- archive files survive
- job history, UI settings, favorites, notes, and index state are lost
- the library can still rebuild from filesystem scan and sidecar metadata where available

If `/data` is lost but `/config` remains:

- job history and notes may still exist
- archive files are gone unless restored from NAS backup
- library index rows may become stale

If both are backed up:

- restore `/data`
- restore `/config/jobs.sqlite3`
- start one container
- run or wait for library reindex if paths changed

## Troubleshooting

Slow downloads:

- add provider tokens/cookies where appropriate
- keep `QUEUE_PER_PROVIDER_LIMIT=1`
- increase the provider/global concurrency settings slowly
- check provider rate limits before increasing retry pressure

NAS becomes sluggish:

- set `INTERNAL_JOB_MAX_CONCURRENT=1`
- keep `MEDIA_TRANSCODE_MAX_CONCURRENT=1`
- reduce download concurrency
- avoid huge folder ZIP jobs during active downloads

Library looks incomplete:

- wait for indexer batches
- call `/api/library?mode=live`
- trigger `/api/library/reindex`
- check whether files were moved outside the app

Old container after upgrade:

- avoid downgrading after schema changes unless you have a DB backup
- never run old and new containers simultaneously on the same DB

Portainer pull failure:

- confirm `HUGCIVI_IMAGE`
- confirm GHCR package visibility or registry authentication
- redeploy after Portainer registry credentials are fixed

Addon button returns 404:

- confirm the running image includes `chrome-extension/`
- confirm `HUGCIVI_CHROME_EXTENSION_DIR` was not pointed at a missing path
- rebuild/redeploy the image if the extension was added after the current image was built
