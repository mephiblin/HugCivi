# YouTube Subscription Design 2026-07-02

Status: future design, not implemented.

This document captures the planned shape for YouTube channel and playlist subscriptions in HugCivi. Current code supports one-shot YouTube and yt-dlp downloads, including channel/playlist archive folder routing, but it does not yet implement long-lived subscriptions, periodic checks, or a separate subscription queue.

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
```

The center content area can stay focused on the current download jobs and library at first. A later UI pass may add a dedicated subscription detail view, but the first implementation should keep the subscription surface compact and predictable.

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
```

The scheduler should add jitter to checks so multiple subscriptions do not hammer YouTube at the same instant after app startup.

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
check_interval_seconds INTEGER NOT NULL
next_check_at TEXT
last_checked_at TEXT
last_success_at TEXT
last_error TEXT
failure_count INTEGER NOT NULL DEFAULT 0
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
status TEXT NOT NULL            -- discovered | queued | downloading | done | skipped | failed
job_id INTEGER
target_dir TEXT
error TEXT
metadata_json TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
UNIQUE(subscription_id, provider_item_id)
```

The first implementation can use these tables directly. A later version can add history tables for per-check audit logs if needed.

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

There are two viable implementation paths.

Preferred first implementation:

- Keep `subscription_items` as the visible subscription queue.
- When an item is ready to download, call shared downloader helper code directly from a subscription worker.
- Do not create a normal `jobs` row unless a single visible job history entry is still desired for compatibility.

Compatibility-first alternative:

- Subscription download worker promotes one item at a time into `db.create_job()`.
- Mark the job metadata as subscription-owned.
- Hide subscription-owned jobs from the normal job list by default, or show them only in the subscription tab.

The preferred approach gives the cleanest UI separation. The compatibility-first approach is less code but risks mixing subscription history back into the existing job list.

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

Avoid making subscription management a marketing-style dashboard. It should feel like a compact operational panel in the left-side management area.

## Safety And Restart Rules

- Persist all subscription state in `/config/jobs.sqlite3`.
- On app startup, resume due checks conservatively.
- Do not auto-start a full backfill unless the subscription was already enabled and the user explicitly chose full backfill.
- Requeue `downloading` subscription items to `queued` or `failed` after restart depending on whether partial files are safe to continue.
- Keep deletion scoped: deleting a subscription should not delete archived media by default.
- Add a separate "delete downloaded files" flow only with explicit confirmation.

## Implementation Phases

Phase 1: documentation and data model

- Add this design document.
- Add migrations for `subscriptions` and `subscription_items`.
- Add DB helper tests.

Phase 2: backend discovery

- Add create/list/update/delete subscription APIs.
- Add manual `check now`.
- Store discovered items without downloading them.

Phase 3: independent subscription queue

- Add subscription scheduler thread.
- Add subscription download worker with conservative defaults.
- Add retry/backoff and restart handling.

Phase 4: UI

- Add left-sidebar `Subscriptions` tab.
- Add add-subscription modal with initial policy and interval.
- Add subscription list and item controls.

Phase 5: polish

- Add storage readouts per subscription.
- Add optional per-subscription format/profile overrides only if needed.
- Add import/export or backup notes if subscription state becomes operationally important.

## Open Questions

- Should subscription downloads create hidden `jobs` rows for compatibility, or should they stay entirely in `subscription_items`?
- Should normal one-shot jobs always preempt subscription downloads, or should this be a user setting?
- Should `from_now` record skipped older videos, or ignore them to keep the DB small?
- Should `latest_n` default to 5, 10, or be hidden behind an advanced option?
- Should playlist subscriptions always store under `playlist/<id>` even when the playlist belongs to a channel that also has a channel subscription?
