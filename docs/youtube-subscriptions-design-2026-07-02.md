# YouTube Subscription Design 2026-07-02

Status: implemented MVP with Phase 1 through Phase 6 complete in current code.

This document captures the implemented shape for YouTube channel and playlist subscriptions in HugCivi. Current code supports one-shot YouTube and yt-dlp downloads, including channel/playlist archive folder routing, plus an independent subscription system. Phase 1 added subscription tables, DB helpers, default payload helpers, and subscription read APIs. Phase 2 added backend create/update/delete APIs and manual yt-dlp discovery that stores `subscription_items`. Phase 3 added the left-sidebar `구독` tab, add-subscription modal, subscription list, and manual check controls. Phase 4 added the independent subscription check scheduler with startup jitter, due checks, backoff scheduling, and restart recovery. Phase 5 added the independent subscription download worker with item progress, logs, retry backoff, and no normal `jobs` rows. Phase 6 added item-level queue/skip/retry controls and per-subscription storage readouts.

## Goal

Add a Pinchflat-inspired YouTube subscription layer without turning HugCivi into a YouTube-only media manager.

The feature should let a user:

- Add a YouTube channel or playlist URL as a subscription.
- Choose the initial download policy before activation.
- Let HugCivi periodically discover new videos.
- Keep subscription discovery and subscription downloads visually separate from the current one-shot download job list.
- Reuse existing yt-dlp authentication, format selection, metadata, and archive path behavior where practical.

## Non-Goals

- Do not replace Pinchflat as a full dedicated YouTube archival app.
- Do not mix long-lived subscription state directly into the existing `jobs` queue UI.
- Do not create hundreds of normal download jobs immediately for a large channel backfill.
- Do not add Redis, Celery, or another service unless the project architecture is explicitly changed later.
- Do not automatically move existing YouTube archives into subscription ownership.

## Pinchflat-Inspired Reference Points

Pinchflat's useful ideas for HugCivi are:

- A channel or playlist is a long-lived Source.
- Indexing discovers media first; download decisions are applied after discovery.
- Filters and policy changes should not require a full re-index every time.
- Channels should not be fully indexed on every scheduled check.
- Overly aggressive indexing can make freshness worse by increasing throttling or rate-limit risk.

HugCivi should intentionally differ in these ways:

- One-shot downloads remain first-class and visually separate.
- Subscription downloads use HugCivi's existing archive layout instead of media-center-specific naming by default.
- The first implementation should avoid global media profile complexity. Keep per-subscription policy small: initial range, interval, auto-queue, and enabled/paused.
- Discovery and subscription downloads should be conservative enough for a personal NAS running a single container.

## First Implementation Decision

Use a dedicated subscription module and tables:

```text
app/subscriptions.py
  -> subscription check scheduler
  -> subscription download scheduler
  -> yt-dlp discovery helpers
  -> subscription item state transitions
```

Do not call `download_gallerydl(job_id, parsed)` from subscription workers. That handler assumes the visible `jobs` table and writes job logs/progress. Instead, first extract reusable downloader helpers from `app/downloader.py`:

- YouTube target path resolution.
- yt-dlp command construction.
- yt-dlp auth/format/extra option parsing.
- safe external process execution with progress callbacks.

Then `app/subscriptions.py` can call those helpers while writing progress to `subscription_items`, not to `jobs`.

This preserves the user's desired independence:

```text
Normal downloads
  -> jobs table
  -> app/downloader.py scheduler
  -> main job list

YouTube subscriptions
  -> subscriptions + subscription_items tables
  -> app/subscriptions.py schedulers
  -> subscription tab
```

A compatibility bridge may be added later to create a normal job from a subscription item manually, but it should not be the default execution path.

## Product Shape

Add a new left-sidebar tab next to the storage folder tab:

```text
[Storage folders] [Subscriptions]
```

The existing storage tree remains the default archive browser. The `Subscriptions` tab replaces the tree panel with subscription management:

```text
+ Add subscription

Subscriptions
- Channel or playlist title
- Enabled / paused / error
- Check interval
- Last checked
- Next check
- Discovered / queued / downloading / done / failed counts
- Stored media size
- Expandable item list with queue / skip / retry controls
```

The center content area stays focused on the current download jobs and library. Subscription details stay compact in the left sidebar so long-lived subscription state remains visually separate from one-shot jobs.

## Add Subscription Flow

When the user enters a YouTube channel or playlist URL in the subscription tab, show a modal before creating the subscription.

Recommended modal fields:

```text
Subscription URL
[ YouTube channel or playlist URL ]

Initial download policy
(*) Download videos from today onward
( ) Download latest N videos
( ) Download from the first video

Check interval
[ 1 hour ] [ 3 hours ] [ 6 hours ] [ 12 hours ] [ Daily ] [ Weekly ] [ Custom ]

Options
[x] Keep subscription enabled
[ ] Queue discovered videos automatically
```

Default policy:

```text
Initial policy: Download videos from today onward
Check interval: 6 hours
Auto queue: enabled, but capped by subscription queue policy
```

`Download from the first video` must show a warning:

```text
This may discover and download hundreds or thousands of videos and consume significant disk space.
Consider "latest N videos" first.
```

## Queue Model

Keep subscriptions independent from the current one-shot download scheduler from a product and UI perspective.

Use two subscription-specific layers:

```text
subscription_check scheduler
  -> discovers remote media into subscription_items

subscription_download scheduler
  -> promotes eligible subscription_items into download execution
```

The implementation can still reuse downloader internals for yt-dlp command construction and archive path logic, but subscription state and subscription UI should not be represented as ordinary user-submitted download jobs.

Recommended defaults:

```text
Subscription check concurrency: 1
Subscription download concurrency: 1
Per-subscription active downloads: 1
Manual one-shot downloads take priority over subscription downloads
Max new items promoted per check: 5
Failure backoff: 15 minutes -> 1 hour -> 6 hours -> 24 hours
Startup jitter: 30 to 300 seconds
Per-source check jitter: +/- 10 percent of interval
```

The scheduler should add jitter to checks so multiple subscriptions do not hammer YouTube at the same instant after app startup.

Interval semantics:

- `next_check_at` is calculated from the end of the previous check, not the start.
- If a check is still running when the next interval would arrive, skip the overlapping run and schedule after the current run completes.
- "Check now" should enqueue a single immediate check if no check is already active for that subscription.
- Failed checks should schedule by backoff unless the user manually clicks "check now".
- The UI should show both `last_checked_at` and `next_check_at` so users understand why nothing is happening.

Recommended user-facing interval presets:

```text
1 hour
3 hours
6 hours
12 hours
daily
weekly
custom hours
```

Default to 6 hours. Use 1 hour and 3 hours as explicit "fast" choices, not as the default.

## Storage Layout

Use the existing YouTube archive layout for actual media files:

```text
/data/gallery-dl/youtube.com/playlist/<playlist-id>
/data/gallery-dl/youtube.com/channel/<channel-name>
```

Subscription sidecar metadata can be written into those folders later, but the authoritative subscription state should live in SQLite so a folder can contain both one-shot and subscribed downloads without requiring ownership migration.

## Proposed Database Tables

Use additive SQLite migrations.

`subscriptions`:

```text
id INTEGER PRIMARY KEY
provider TEXT NOT NULL DEFAULT 'youtube'
kind TEXT NOT NULL              -- channel | playlist
source_url TEXT NOT NULL
canonical_id TEXT
title TEXT
enabled INTEGER NOT NULL DEFAULT 1
auto_queue INTEGER NOT NULL DEFAULT 1
initial_policy TEXT NOT NULL    -- from_now | latest_n | full_backfill
initial_limit INTEGER
cutoff_published_at TEXT
first_check_completed INTEGER NOT NULL DEFAULT 0
check_interval_seconds INTEGER NOT NULL
next_check_at TEXT
last_checked_at TEXT
last_success_at TEXT
last_error TEXT
failure_count INTEGER NOT NULL DEFAULT 0
check_status TEXT NOT NULL DEFAULT 'idle'  -- idle | due | checking | backoff | paused | error
last_check_started_at TEXT
last_check_finished_at TEXT
last_seen_provider_item_id TEXT
last_seen_published_at TEXT
metadata_json TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

`subscription_items`:

```text
id INTEGER PRIMARY KEY
subscription_id INTEGER NOT NULL
provider_item_id TEXT NOT NULL  -- YouTube video ID
url TEXT NOT NULL
title TEXT
published_at TEXT
discovered_at TEXT NOT NULL
status TEXT NOT NULL            -- known | eligible | queued | downloading | done | skipped | failed | unavailable
policy_reason TEXT              -- from_now | latest_n | full_backfill | manual | older_than_cutoff | duplicate
queued_at TEXT
download_started_at TEXT
download_finished_at TEXT
target_dir TEXT
filename TEXT
progress_bytes INTEGER DEFAULT 0
total_bytes INTEGER
attempt_count INTEGER NOT NULL DEFAULT 0
last_attempt_at TEXT
next_attempt_at TEXT
error TEXT
metadata_json TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
UNIQUE(subscription_id, provider_item_id)
```

The first implementation can use these tables directly. A later version can add history tables for per-check audit logs if needed.

Recommended indexes:

```text
CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_provider_canonical
  ON subscriptions(provider, kind, canonical_id)
  WHERE canonical_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_subscriptions_due
  ON subscriptions(enabled, next_check_at, check_status);

CREATE INDEX IF NOT EXISTS idx_subscription_items_ready
  ON subscription_items(status, next_attempt_at, subscription_id);

CREATE INDEX IF NOT EXISTS idx_subscription_items_provider_item
  ON subscription_items(provider_item_id);
```

Use helper functions in `app/db.py` or a small `app/subscription_db.py`; either is acceptable, but keep all SQLite writes behind the existing `_DB_LOCK` pattern.

DB backup note: subscription URLs, metadata, and possibly private/unlisted video titles become part of `/config/jobs.sqlite3`, so backups remain privacy-sensitive.

## State Machines

Subscription check state:

```text
idle
  -> due
  -> checking
  -> idle       on success
  -> backoff    on retryable failure
  -> error      on repeated failure
paused          when enabled = 0
```

Subscription item state:

```text
known
  -> eligible
  -> queued
  -> downloading
  -> done

known/eligible/queued
  -> skipped

downloading
  -> queued     retryable failure with next_attempt_at
  -> failed     retry cap hit or no retry scheduled
  -> done
```

Meaning:

- `known`: discovered but not currently selected for download.
- `eligible`: selected by policy and ready to be queued.
- `queued`: waiting for the subscription download worker.
- `downloading`: a subscription worker owns this item.
- `done`: media is present or successfully downloaded.
- `skipped`: user or policy skipped it.
- `failed`: no retry is currently scheduled, or retry cap was hit.
- `unavailable`: yt-dlp reports the video is deleted, private, region-blocked, or otherwise inaccessible.

## Proposed API Surface

Keep API shapes additive.

```text
GET    /api/subscriptions
POST   /api/subscriptions
GET    /api/subscriptions/{id}
PATCH  /api/subscriptions/{id}
DELETE /api/subscriptions/{id}
POST   /api/subscriptions/{id}/check
GET    /api/subscriptions/{id}/items
POST   /api/subscriptions/items/{id}/queue
POST   /api/subscriptions/items/{id}/skip
POST   /api/subscriptions/items/{id}/retry
```

`POST /api/subscriptions` should accept:

```json
{
  "url": "https://www.youtube.com/@example",
  "initial_policy": "from_now",
  "initial_limit": null,
  "check_interval_seconds": 21600,
  "auto_queue": true
}
```

Subscription payloads include `item_counts`, `storage_bytes`, and `storage_human` so the sidebar can show both item state and saved media size without scanning `/data`.

## Discovery Behavior

Use yt-dlp in metadata/listing mode for discovery:

```text
python -m yt_dlp --no-config --skip-download --dump-single-json --flat-playlist ...
```

Reuse existing YouTube/yt-dlp settings:

- `YT_DLP_COOKIES_FILE`
- `YT_DLP_COOKIES_FROM_BROWSER`
- `YT_DLP_FORMAT`
- `YT_DLP_EXTRA_OPTIONS`, excluding unsafe path/output/exec overrides as today

Discovery should not download media. It should store item metadata and decide whether each item is eligible based on the initial policy and existing item rows.

Discovery depth should follow policy:

- `from_now`: on create, record `cutoff_published_at` and do not full-backfill. Scheduled checks only need a recent window large enough to catch newly published items and date corrections.
- `latest_n`: first check may use a bounded playlist/channel listing and mark only the newest `N` as eligible.
- `full_backfill`: allow a full listing scan, but show a warning and avoid auto-promoting everything at once.

For channel sources, prefer incremental checks after the first successful check:

- Keep `last_seen_provider_item_id` and `last_seen_published_at`.
- Stop scanning once already-known recent items are encountered, unless the user forces a full check.
- A forced full check from the UI should be explicit because it can be expensive.

Duplicate handling:

- If an item already exists for the subscription, update metadata but preserve `done`, `skipped`, and in-progress states.
- If a one-shot YouTube download already produced an info JSON or filename containing the same YouTube ID, the subscription worker may mark the item `done` with `policy_reason='duplicate'` instead of downloading again. This can be added after the MVP if scanning existing files is too expensive.

## Initial Policy Semantics

`from_now`:

- During creation, record a cutoff timestamp.
- Existing older videos are recorded as skipped or ignored.
- Future videos become discovered/queued.

`latest_n`:

- During first check, make only the latest `N` items eligible.
- Older discovered items are skipped or left undiscovered depending on the discovery result size.

`full_backfill`:

- All discoverable items become eligible.
- UI must warn about disk and rate-limit risk before enabling.

## Execution Options

Use the direct subscription execution path first:

- Keep `subscription_items` as the visible subscription queue.
- When an item is ready to download, call shared downloader helper code from the subscription worker.
- Write logs and progress into `subscription_items.metadata_json`, `progress_bytes`, `total_bytes`, and `error`.
- Do not create a normal `jobs` row by default.

Current implementation reuses existing downloader helpers for the pieces needed by subscription downloads:

```text
yt_dlp_command(source_url, target, extra_args=None)
gallery_dl_target_path_parts(source_url, info=None)
gallery_dl_downloaded_files(target)
gallery_dl_progress_snapshot(target)
```

`download_gallerydl()` keeps the old visible-job behavior. Subscription downloads run their own subprocess loop and write progress/log/error state into `subscription_items`.

## Configuration Defaults

Runtime setting surface:

```text
SUBSCRIPTION_CHECK_MAX_CONCURRENT=1
SUBSCRIPTION_DOWNLOAD_MAX_CONCURRENT=1
SUBSCRIPTION_PER_SOURCE_DOWNLOAD_LIMIT=1
SUBSCRIPTION_DEFAULT_CHECK_INTERVAL_SECONDS=21600
SUBSCRIPTION_PROMOTE_BATCH_SIZE=5
SUBSCRIPTION_STARTUP_JITTER_MIN_SECONDS=30
SUBSCRIPTION_STARTUP_JITTER_MAX_SECONDS=300
SUBSCRIPTION_RETRY_BACKOFF_SECONDS=900,3600,21600,86400
SUBSCRIPTION_DISCOVERY_RECENT_WINDOW=50
SUBSCRIPTION_CHECK_POLL_SECONDS=60
SUBSCRIPTION_DOWNLOAD_POLL_SECONDS=30
SUBSCRIPTION_DOWNLOAD_MAX_ATTEMPTS=3
```

The UI exposes the default interval and auto-queue behavior through the add-subscription modal.

## UI Notes

The subscription tab should use existing HugCivi modal and polling patterns.

Expected controls:

- Add subscription button.
- Enable/pause toggle per subscription.
- Check now button.
- Delete subscription button.
- Interval selector.
- Initial policy labels.
- Per-subscription counters.
- Item detail list with queue/skip/retry controls.
- Per-subscription storage readout.

Avoid making subscription management a marketing-style dashboard. It should feel like a compact operational panel in the left-side management area.

Initial UI cut:

- Left-sidebar segmented tabs: `저장 폴더`, `구독`.
- `구독` tab content lives inside the existing sidebar width.
- Add button opens the subscription modal.
- Subscription list rows show title, state, interval, next check, and a compact count line.
- Selecting a subscription expands item rows inside the `구독` sidebar panel.

Modal copy should be direct:

```text
오늘 이후 새 영상부터
최근 N개만
첫 영상부터 전체 다운로드
```

For `첫 영상부터 전체 다운로드`, require an explicit confirmation checkbox before enabling save.

## Safety And Restart Rules

- Persist all subscription state in `/config/jobs.sqlite3`.
- Register subscription schedulers in FastAPI lifespan after `db.init_db()` and before the library indexer starts.
- Stop subscription schedulers on shutdown the same way external and internal workers are stopped.
- On app startup, resume due checks conservatively.
- Do not auto-start a full backfill unless the subscription was already enabled and the user explicitly chose full backfill.
- Reset `checking` subscriptions to `due` or `backoff` after restart.
- Requeue `downloading` subscription items to `queued` or `failed` after restart depending on whether partial files are safe to continue.
- Track partial files under `/data` and clean only files known to belong to the subscription item.
- Keep deletion scoped: deleting a subscription should not delete archived media by default.
- Add a separate "delete downloaded files" flow only with explicit confirmation.
- Keep saved cookies and credentials out of subscription API responses.
- Avoid following symlinks when scanning for duplicate existing YouTube files.

## Implementation Phases

Phase 1: data model and disabled backend

- Done: add migrations for `subscriptions` and `subscription_items`.
- Done: add DB helper tests.
- Done: add default settings constants but do not start schedulers yet.
- Done: add API read/list endpoints returning data-model state.

Phase 2: manual discovery

- Done: add create/list/update/delete subscription APIs.
- Done: add manual `check now`.
- Done: store discovered items without downloading them.
- Done: mock yt-dlp discovery in tests.

Phase 3: sidebar UI

- Done: add left-sidebar `Subscriptions` tab.
- Done: add add-subscription modal with initial policy and interval.
- Done: add subscription list and manual check button.

Phase 4: independent subscription check scheduler

- Done: add subscription scheduler thread.
- Done: add jitter, backoff, and restart handling.
- Done: keep auto-download disabled until item discovery is stable.

Phase 5: independent subscription download worker

- Done: reuse shared yt-dlp downloader helpers for command construction and YouTube archive target routing.
- Done: add subscription download worker with conservative defaults.
- Done: add progress/log persistence on `subscription_items`.
- Done: add retry/backoff for failed subscription item downloads.

Phase 6: polish

- Done: add storage readouts per subscription.
- Done: add expandable item detail rows in the sidebar.
- Done: add item-level queue, skip, and retry controls.
- Future optional: add per-subscription format/profile overrides only if needed.
- Future optional: add import/export or backup notes if subscription state becomes operationally important.

## Test Plan

DB tests:

- `db.init_db()` creates both tables on a fresh DB.
- Migration from an older DB without subscription tables is additive.
- Unique constraints prevent duplicate subscription item rows.
- Settings/status APIs do not leak secrets.

Discovery tests:

- Channel URL canonicalization stores `kind='channel'`.
- Playlist URL canonicalization stores `kind='playlist'`.
- `from_now` does not mark older items eligible.
- `latest_n` marks only N newest items eligible.
- Existing item rows preserve `done` and `skipped` states when rediscovered.
- yt-dlp failures schedule backoff and record redacted errors.

Scheduler tests:

- Two checks for the same subscription cannot run concurrently.
- Startup resets stale `checking` state conservatively.
- Due subscriptions are checked with concurrency 1 by default.
- Manual one-shot downloads are not blocked by subscription schedulers.

Download tests:

- Subscription downloads write under the current YouTube archive layout.
- Per-subscription active download limit is 1.
- Retryable failed downloads increment `attempt_count`, requeue the item, and set `next_attempt_at`.
- Failed downloads that hit the retry cap remain `failed` without `next_attempt_at`.
- Deleting a subscription does not delete files.
- Item queue/skip/retry APIs update item status and return refreshed subscription/item payloads.
- Per-subscription storage payloads sum completed media size and in-progress downloaded bytes.

UI tests:

- Existing storage folder tree still renders as default.
- Switching to `구독` tab does not mutate folder state.
- Add modal enforces explicit confirmation for full backfill.
- Subscription rows expose item controls in the sidebar.
- Saved secret values are never returned to the browser.

## Resolved Decisions And Future Work

- Current decision: subscription downloads stay separate from normal one-shot downloads and run with conservative subscription-specific concurrency. Normal one-shot downloads do not wait behind subscription items because they use the existing `jobs` scheduler.
- Current decision: `from_now` records older videos returned by bounded discovery as `known`, not eligible. This keeps recent context without auto-backfilling.
- Current decision: `latest_n` uses 5 as the UI default.
- Current decision: playlist subscriptions store under `playlist/<id>` even if the playlist owner also has a channel subscription.
- Current implementation: the sidebar `구독` tab switches the main work-list area to subscription-specific work. See [YouTube Subscription Main Panel Design 2026-07-02](youtube-subscription-main-panel-design-2026-07-02.md).
- Future optional: duplicate detection against existing `/data/gallery-dl/youtube.com` info JSON files can be added through the library index rather than inside the Phase 6 subscription worker.
