# YouTube Subscription Main Panel Design 2026-07-02

Status: implemented in current code.

This document describes the UI step for YouTube subscriptions where, when the left sidebar is in the `구독` category, the main work-list area shows subscription work instead of the normal one-shot `작업 목록`.

The current MVP already keeps subscription state independent from normal `jobs`. This design keeps that boundary and makes the visual surface match it more clearly.

## Goal

When the user selects the sidebar `구독` tab:

- Keep the left sidebar focused on subscription source management.
- Show a subscription-specific work list in the main content area.
- Make queued, downloading, failed, skipped, and completed subscription items visible without opening each subscription row.
- Reuse existing item controls: queue, skip, retry.
- Keep normal one-shot downloads and subscription downloads visually and operationally separate.

When the user selects the sidebar `저장 폴더` tab:

- Keep the current main `작업 목록` behavior for normal `jobs`.

## Non-Goals

- Do not change the YouTube storage layout.
- Do not move existing `/data/gallery-dl/youtube.com/video-*` archives.
- Do not add `/data/subscriptions` or another ownership-based archive root.
- Do not create normal `jobs` rows for subscription items.
- Do not merge normal job controls such as pause/resume/delete with subscription item controls.
- Do not implement duplicate scanning against existing one-shot YouTube archives in this UI pass.

Folder layout remains the current downloader behavior:

```text
/data/gallery-dl/youtube.com/playlist/<playlist-id>
/data/gallery-dl/youtube.com/channel/<channel-name>
```

Subscription ownership and item state remain in `/config/jobs.sqlite3`.

## Current State

Current code has:

- Sidebar tabs in `app/templates/index.html`:
  - `저장 폴더`
  - `구독`
- `activateSidebarTab(tab)` toggles both sidebar panels and the main work-list mode.
- The main content keeps the normal download input visible and switches the list below it between normal `작업 목록` and `구독 작업 목록`.
- Subscription item detail is available inside the sidebar by expanding one subscription row.
- Existing subscription item APIs:
  - `GET /api/subscriptions`
  - `GET /api/subscriptions/items`
  - `GET /api/subscriptions/{id}/items`
  - `POST /api/subscriptions/items/{id}/queue`
  - `POST /api/subscriptions/items/{id}/skip`
  - `POST /api/subscriptions/items/{id}/retry`

The aggregate main-panel view for subscription work is implemented through `GET /api/subscriptions/items` and the `subscription-work-section` UI.

## Product Shape

Use the sidebar tab as the high-level work context:

```text
Sidebar tab: 저장 폴더
  Main top: normal download input
  Main list: normal 작업 목록 from jobs

Sidebar tab: 구독
  Main top: normal download input stays visible for one-shot work
  Main list: 구독 작업 목록 from subscription_items
```

Keeping the main input visible is the conservative first cut. It preserves quick one-shot downloads even while the user is managing subscriptions. The main list below it changes with the sidebar context.

The subscription work list should feel operational, not dashboard-like:

```text
구독 작업 목록
[활성] [대기] [다운로드중] [실패] [완료] [건너뜀]

상태 | 구독 | 항목 | 게시일 | 진행률 | 재시도 | 파일/오류 | 제어
```

Recommended default filter:

```text
active = eligible + queued + downloading + failed
```

This keeps the list useful for day-to-day monitoring. `done`, `skipped`, and `known` remain available through filters.

## Main Panel Behavior

### Desktop

Add a second main list section beside the existing normal jobs section:

```html
<section class="view active mobile-panel" id="home-view" ...>
  <section class="jobs-section" id="jobs-section">...</section>
  <section class="subscription-work-section" id="subscription-work-section" hidden>...</section>
</section>
```

or split the list surface into two sibling sections inside `home-view`. The key requirement is that the normal jobs table and subscription work table do not render as one merged table.

When `activateSidebarTab('subscriptions')` runs:

- Set an explicit main mode, for example `activeWorkMode = 'subscriptions'`.
- Hide the normal jobs section.
- Show the subscription work section.
- Refresh subscription summary and aggregate subscription item rows.

When `activateSidebarTab('folders')` runs:

- Set `activeWorkMode = 'jobs'`.
- Show the normal jobs section.
- Hide the subscription work section.
- Keep normal job polling behavior.

### Mobile

The existing mobile layout uses `data-mobile-panel`. Avoid forcing mobile users into an unexpected panel switch.

Recommended first behavior:

- If mobile active panel is `download`, the visible list changes according to sidebar context.
- If mobile active panel is `library`, keep library visible until the user returns to download/work.
- Do not add a new bottom-tab item unless the current mobile navigation becomes confusing in real use.

## Aggregate API

Add an aggregate item list API so the main panel does not need to request every subscription one by one.

Recommended route:

```text
GET /api/subscriptions/items
```

Query parameters:

```text
status=active | eligible | queued | downloading | failed | done | skipped | known | unavailable | all
subscription_id=<optional integer>
limit=<default 100, max 500>
cursor=<optional integer item id for pagination>
```

Suggested response:

```json
{
  "ok": true,
  "items": [
    {
      "id": 12,
      "subscription_id": 3,
      "subscription_title": "Example Channel",
      "subscription_kind": "channel",
      "subscription_enabled": true,
      "provider_item_id": "abc123",
      "title": "Video title",
      "url": "https://www.youtube.com/watch?v=abc123",
      "published_at": "2026-07-02T00:00:00+00:00",
      "status": "queued",
      "progress_bytes": 0,
      "total_bytes": null,
      "progress_human": "0.0 B",
      "total_human": null,
      "percent": null,
      "attempt_count": 1,
      "next_attempt_at": "2026-07-02T01:00:00+00:00",
      "target_dir": "/data/gallery-dl/youtube.com/channel/example",
      "filename": null,
      "error": null
    }
  ],
  "counts": {
    "known": 0,
    "eligible": 2,
    "queued": 4,
    "downloading": 1,
    "done": 10,
    "skipped": 1,
    "failed": 1,
    "unavailable": 0
  },
  "next_cursor": null,
  "scheduler": {
    "check_scheduler_running": true,
    "download_scheduler_running": true
  }
}
```

Backend helpers:

- Add `db.list_subscription_item_summaries(...)`.
- Join `subscription_items` to `subscriptions`.
- Reuse `subscriptions.item_payload(...)` shape where possible.
- Add display fields such as `progress_human`, `total_human`, and `percent` in `app/subscriptions.py`.
- Keep response additive. Existing item APIs should not change incompatibly.

## Item Controls

The main subscription work list should use the same action semantics as the sidebar item rows.

Suggested actions by status:

| Status | Main actions |
| --- | --- |
| `known` | queue, skip |
| `eligible` | queue, skip |
| `queued` | skip |
| `downloading` | read-only in first cut |
| `failed` | retry, skip |
| `skipped` | retry |
| `done` | no mutation action in first cut |
| `unavailable` | no mutation action in first cut |

Use existing routes:

```text
POST /api/subscriptions/items/{id}/queue
POST /api/subscriptions/items/{id}/skip
POST /api/subscriptions/items/{id}/retry
```

After an action:

- Update the item row optimistically only if the API succeeds.
- Refresh aggregate counts.
- Keep the sidebar subscription row in sync if it is selected.

## Frontend State

Add state near current subscription state in `app/templates/index.html`:

```js
let activeWorkMode = 'jobs';
let subscriptionWorkItems = [];
let subscriptionWorkFilter = 'active';
let subscriptionWorkCursor = null;
let subscriptionWorkLoading = false;
```

Rendering functions:

```text
renderWorkMode()
refreshSubscriptionWorkItems()
renderSubscriptionWorkItems()
renderSubscriptionWorkTable()
renderSubscriptionWorkMobileCards()
handleSubscriptionWorkAction(itemId, action)
```

Polling:

- Normal jobs keep the existing job polling.
- Subscription work items should poll only when `activeWorkMode === 'subscriptions'`.
- Use a moderate interval such as 5 seconds while subscription work mode is visible.
- Keep the existing 30 second subscription summary refresh for sidebar state.

## UI Details

Desktop table columns:

```text
상태
구독
항목
게시일/발견일
진행률
재시도
파일/오류
제어
```

Mobile card fields:

```text
status pill
subscription title
video title
published/discovered date
progress
filename or error
actions
```

Empty states:

- Active filter: `진행 중인 구독 항목이 없습니다.`
- Failed filter: `실패한 구독 항목이 없습니다.`
- Done filter: `완료된 구독 항목이 아직 없습니다.`

Do not add explanatory marketing copy. Keep the list operational and compact.

## Implementation Phases

Phase 1: backend aggregate API

- Add DB summary query for subscription items.
- Add `GET /api/subscriptions/items`.
- Add status filter normalization.
- Add pagination by item id or created/discovered ordering.
- Add tests for active/default filtering, counts, subscription metadata, and missing/invalid filters.

Phase 2: frontend main work-mode switch

- Add `activeWorkMode`.
- Update `activateSidebarTab()` to switch main list mode.
- Add subscription work section markup.
- Hide normal jobs table when subscription mode is active.
- Keep the normal download input visible.

Phase 3: subscription work rendering and controls

- Render desktop table and mobile cards.
- Wire filters and refresh button.
- Reuse item queue/skip/retry routes.
- Update sidebar subscription state after item actions.

Phase 4: verification and polish

- Add template tests for the new section and route string.
- Run inline template script syntax check.
- Run subscription tests and review regression tests.
- Smoke-test desktop and mobile widths.

## Test Plan

Backend tests:

- Aggregate API returns only active statuses by default.
- `status=all` returns known/done/skipped/unavailable too.
- Counts include all item statuses for the filtered subscription scope.
- Returned items include subscription title/kind/enabled metadata.
- Item action APIs still return refreshed item/subscription payloads.

Frontend/template tests:

- Template declares `subscription-work-section`.
- Template contains aggregate API fetch for `/api/subscriptions/items`.
- Template keeps normal `jobs-section` and subscription work section separate.
- CSS contains subscription work table/card classes.
- Inline script passes `node --check` after Jinja constants are replaced.

Regression tests:

- Normal `/api/jobs` and normal job rendering are unchanged.
- `저장 폴더` tab still shows normal jobs.
- `구독` tab triggers subscription refresh and subscription work refresh.
- Library view does not lose selected folder state when sidebar context changes.

Manual smoke:

- Open app on desktop.
- Select `구독`; main list changes to `구독 작업 목록`.
- Select `저장 폴더`; main list returns to normal `작업 목록`.
- Queue/skip/retry a mocked or seeded subscription item and confirm row/count refresh.
- Check mobile width for non-overlapping cards and buttons.

## Risks

- If the aggregate API is skipped and the frontend fans out to every subscription's item API, a user with many subscriptions may make too many requests. Prefer the aggregate API.
- If main mode is tied too tightly to mobile panel state, library navigation may feel jumpy. Keep work mode separate from library mode.
- If normal jobs and subscription items share one table renderer, control semantics can blur. Keep separate renderers even if they share small formatting helpers.
- If subscription work polling runs while hidden, it wastes requests. Gate polling by visible mode.

## Open Questions

- Should the normal download input remain visible while in `구독` mode? Recommended first answer: yes.
- Should completed subscription items default to a recent window, such as last 50, instead of all done rows? Recommended first answer: yes, use pagination.
- Should clicking a completed item open its folder or the media viewer? Leave for a later library integration pass.
- Should `downloading` get a pause action? Leave for a later worker-control design; first cut is read-only for active downloads.
