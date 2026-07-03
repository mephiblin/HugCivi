# HugCivi Operations Guide

Last updated: 2026-07-03

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
HF_SNAPSHOT_MAX_WORKERS=2
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
| `QUEUE_PER_PROVIDER_LIMIT` | Concurrent jobs allowed for the same provider bucket. |
| `QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS` | Minimum cooldown after a provider job finishes. |
| `QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS` | Maximum cooldown after a provider job finishes. |
| `DOWNLOAD_STALL_TIMEOUT_SECONDS` | Watchdog timeout for jobs with no detected progress. `0` disables the timeout. |

Internal server-local jobs use a separate limit:

| Setting | Meaning |
| --- | --- |
| `INTERNAL_JOB_MAX_CONCURRENT` | Concurrent ZIP/transcode/poster jobs. Default is `2`; `1` is recommended on modest NAS hardware. |

Current default note: `portainer-stack.yml` sets `DOWNLOAD_STALL_TIMEOUT_SECONDS` to `${DOWNLOAD_STALL_TIMEOUT_SECONDS:-0}`, while the Dockerfile and local compose path use `600`. If you want stalled downloads to be stopped automatically in Portainer, set this value explicitly.

## ASMR.one Downloads

ASMR.one `/work/RJ...` and `/work/<id>/DLSITE/RJ...` URLs run as ordinary external download jobs in the `asmrone` provider bucket, so the global/per-provider queue limits and provider cooldown settings apply.

The handler uses `ASMRONE_API_BASE` for work and track metadata, but file bodies are downloaded from each leaf track `mediaDownloadUrl` with `action=download`. `mediaStreamUrl` is intentionally ignored. Output defaults under `/data/asmr.one/...` unless the user chose a target folder, with downloaded track files plus `_asmrone_metadata.json`, redacted `_asmrone_tracks.json`, `_asmrone_manifest.json`, and `_archive_metadata.json`.

Downloaded ASMR.one audio files are recognized by the media viewer and play through the browser audio element. Non-audio files with a download URL are still stored in the work folder. Audio does not use the internal video transcode/poster queue.

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
- Media cache files live under `/config/media-cache`.

Useful media/cache settings:

```text
MEDIA_TRANSCODE_MAX_CONCURRENT=1
MEDIA_TRANSCODE_TIMEOUT_SECONDS=1800
MEDIA_TRANSCODE_PRESET=veryfast
MEDIA_TRANSCODE_CRF=23
MEDIA_TRANSCODE_AUDIO_BITRATE=160k
MEDIA_CACHE_TTL_SECONDS=2592000
MEDIA_CACHE_MAX_BYTES=0
DOWNLOAD_ARCHIVE_TTL_SECONDS=86400
DOWNLOAD_ARCHIVE_MAX_CONCURRENT=1
DOWNLOAD_ARCHIVE_MAX_FILES=50000
DOWNLOAD_ARCHIVE_MAX_SOURCE_BYTES=0
DOWNLOAD_ARCHIVE_MIN_FREE_BYTES=0
```

`0` usually means unlimited or disabled for max/threshold-style settings. Use conservative values if the NAS volume is tight.

## Library Index

The library UI uses `library_items` when indexed rows are available.

Background controls:

```text
LIBRARY_INDEXER_START_DELAY_SECONDS=5
LIBRARY_INDEXER_INTERVAL_SECONDS=300
LIBRARY_INDEX_BATCH_SIZE=300
LIBRARY_REINDEX_BATCH_SIZE=5000
```

Operational notes:

- First indexing pass may take time on large archives.
- `/api/library?mode=live` can force live filesystem scanning.
- `/api/library/reindex` resets and scans a large batch.
- App-driven rename/move/delete updates the index and path-linked state.
- NAS-side manual file changes are picked up by later indexer passes or live fallback.

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
- increase `MAX_CONCURRENT_DOWNLOADS` slowly
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
