# Library Card Performance Plan 2026-07-05

Status: proposed plan, not implemented.

## Purpose

HugCivi library cards are becoming slow to show after refresh or startup as archive files grow. The current bottleneck is not one single slow function; it is the combined cost of preparing many library payloads, embedding them into the first page, rebuilding all card DOM nodes, and loading original-size thumbnail images.

This plan follows the repository guidance in `AGENTS.md`: current code and current reference docs are authoritative, large archive content stays in `/data`, app/cache state stays in `/config`, filesystem paths must go through existing safety helpers, and behavior changes should later update the feature map, architecture/configuration/operations docs, and patch notes.

## Feasibility Decision

The proposed changes are feasible and fit the existing architecture, with three important constraints:

- Keep existing `/api/library` array responses compatible unless the frontend explicitly asks for a paged payload.
- Make library pagination real enough to reduce card DOM and image work; `requestAnimationFrame` chunking is not the preferred strategy because it still leaves very large card sets in one view.
- Put generated card thumbnails under `/config/media-cache`, not `/data`, because thumbnails are disposable derived artifacts like video posters and transcodes.

## Current Behavior

- `GET /` renders `index.html` with `library_items()` already included in the template context.
- `GET /api/library` currently returns a plain array.
- `library_items()` defaults to up to 1000 records and falls back to live filesystem scanning when the DB index is empty or live mode is requested.
- Selecting a folder calls `/api/library?mode=live&path=...`, which helps restore filesystem-backed cards before the global index catches up, but can add disk I/O on large folders.
- `renderLibrary()` filters, sorts, and renders every matching card with a single `innerHTML = matches.map(...).join('')`.
- `refreshJobs()` runs every 2.5 seconds and calls `renderLibrary()` whenever the library view is active, even when only job progress changed.
- Image cards often use `/api/media/file` or `/api/fs/preview`, which serves original images rather than small card thumbnails.

Relevant code:

- `app/main.py::index`
- `app/main.py::api_library`
- `app/main.py::library_items`
- `app/main.py::live_library_items`
- `app/db.py::list_library_index_items`
- `app/templates/index.html::refreshJobs`
- `app/templates/index.html::refreshLibraryItems`
- `app/templates/index.html::renderLibrary`
- `app/downloader.py::thumbnail_url_for_path`
- `app/main.py::thumbnail_url_for_media`
- `app/main.py` media cache and poster helpers

## Scope

Implement in three stages:

1. Library card pagination at 50 cards per page.
2. Avoid full library rerender during job polling unless visible library card data changed.
3. Add lazy cached card thumbnails for image/folder cover thumbnails.

These stages can ship separately, but pagination and refresh gating should come first because they reduce browser work without adding new media generation behavior.

## Non-Goals

- Do not add a full search engine.
- Do not put cache thumbnails inside `/data`.
- Do not create one internal job row per image thumbnail request.
- Do not remove `/api/fs/preview` or `/api/media/file`; keep them for compatibility and full-size viewer use.
- Do not change Chrome extension APIs.

## Stage 1: Library Pagination

### Desired UX

- Library cards show 50 items per page.
- Add numbered page controls similar to the job table, placed below the library grid to fit mobile better.
- `libraryCount` should show total count when known, for example `총 123개 · 1 / 3`.
- For live scans where total count is unknown, show a bounded count and next/previous controls based on `has_next`.
- Folder changes and sort changes reset to page 1.
- Refreshes clamp the current page if the number of pages shrinks.

### API Shape

Keep legacy behavior:

- `GET /api/library` with no `limit` or `page` returns the current plain array.

Add paged behavior only when the client sends `limit` or `page`:

```json
{
  "ok": true,
  "items": [],
  "page": 1,
  "limit": 50,
  "total_count": 123,
  "total_pages": 3,
  "has_next": true,
  "mode": "index",
  "path": "",
  "source": "index",
  "indexing": false
}
```

Recommended query parameters:

- `limit`: default `50` for paged requests, clamped to a small maximum such as `100`.
- `page`: 1-based page number.
- `path`: normalized `/data` relative path.
- `mode`: existing `index` or `live`.
- `sort`: `az`, `za`, `date`, `favorite`, matching the current UI.

### Index Mode

For indexed rows, add DB helpers rather than changing the existing array helper in place:

- `db.list_library_index_items(limit=50, offset=0, path_prefix="", sort="az")`
- `db.count_library_index_items(path_prefix="")`
- `library_items_page_payload(...)`

If preserving exact current sort semantics becomes too complex, prefer an additive sort path:

- Use existing columns for the first pass: `name`, `mtime_ns`, `path`.
- Revisit additional sort columns only if metadata-title ordering is visibly wrong.
- Document that the DB indexer may need to refresh rows before all card titles sort ideally.

### Live Mode

For `mode=live`, avoid scanning the full tree just to compute exact totals.

- Scan until `offset + limit + 1` matching items are found.
- Return `items` sliced to `limit`.
- Return `has_next` from the extra item.
- Return `total_count: null` and `total_pages: null`.

This keeps live folder recovery useful without turning every page request into a full recursive count.

### Frontend Changes

Add state:

- `LIBRARY_PAGE_SIZE = 50`
- `currentLibraryPage`
- `currentLibraryPageInfo`
- `currentLibrarySort`
- optional `lastLibraryCardSignature`

Change `refreshLibraryItems()` to request:

```text
/api/library?limit=50&page=<page>&path=<activeLibraryPath>&mode=<mode>&sort=<sort>
```

Change response handling:

- If the response is an array, keep legacy behavior.
- If the response is an object with `items`, update page info and render only those items.

Change `renderLibrary()`:

- Stop building every matching card when a paged payload is active.
- Render the current page's `items`.
- Render a separate pagination control under the grid.

## Stage 2: Avoid Full Library Rerender During Job Polling

Current `refreshJobs()` always rerenders the library if `activeLibraryPath !== null`.

Replace that with a signature gate:

1. Before replacing `currentJobs`, compute a signature of done jobs that can affect library cards for the active folder.
2. Replace `currentJobs` and render the job table.
3. Compute the new signature.
4. Call `renderLibrary()` or `refreshLibraryForActivePath()` only if the signature changed.

The signature should include card-affecting fields:

- `id`
- `status`
- `target_path` or `target_dir`
- `model_title`
- `filename`
- `input_text`
- `source`
- `model_category`
- `model_type`
- `base_model`
- `precision`
- `file_format`
- `thumbnail_url`
- `favorite`
- `source_url`
- `updated_at`
- `created_at`
- `has_media`
- `media_count`
- `media_type`

Keep special handling for completion:

- If a job changes into `done` and has a target path, refresh folders as today.
- If the completed target is inside the active library folder, refresh the active library page so newly created sidecar-backed cards appear.

Important detail: `activeLibraryPath === ''` means `/data` root. Do not replace `activeLibraryPath !== null` checks with truthy checks.

## Stage 3: Cached Card Thumbnails

### Cache Location

Use:

```text
/config/media-cache/thumbnails/
```

Rationale:

- `/data` remains user archive content.
- `/config/media-cache` already holds disposable video poster/transcode artifacts.
- Existing media cache TTL/quota cleanup can include thumbnail files automatically.

### API

Add:

```text
GET /api/media/thumbnail?path=<relative-data-path>&size=360
```

Behavior:

- Resolve `path` through `existing_data_path()`.
- Reject `/data` root and unsafe/symlink escape cases.
- If `path` is a directory, find its representative image through existing folder thumbnail logic.
- If `path` is an image file, thumbnail that image.
- If the cached thumbnail exists, return it immediately.
- On cache miss, generate a small JPEG thumbnail and return it.

Do not expose the cache file path to the client.

### Generation Strategy

Use lazy-on-request generation from the thumbnail endpoint.

Do not generate thumbnails during `library_items()` or `scan_library_index_batch()`, because that would turn listing/indexing into expensive media processing.

Do not create internal job rows for image thumbnails in the first implementation. `<img>` tags do not naturally fit a queued/polling response model, and one job per visible image would make the job table noisy.

Use existing concurrency controls where possible:

- Per-cache-key lock to avoid duplicate generation.
- Bounded semaphore shared with existing media work or a small dedicated thumbnail semaphore.
- Atomic temp file plus `os.replace()`.

Implementation choice:

- Prefer `ffmpeg` first because the Docker image already installs it.
- Keep a Pillow-based option out of scope unless ffmpeg thumbnail quality or format support proves poor.

### Cache Key

Include:

- source resolved path
- source size
- source `mtime_ns`
- requested size
- output format/version marker

This makes cache invalidation automatic when an image changes.

### Card URL Migration

For card-sized images, prefer the new thumbnail endpoint:

- `thumbnail_url_for_media(image)` should return `/api/media/thumbnail?...` for library/card contexts.
- `thumbnail_url_for_path(folder)` should keep returning existing `/api/fs/preview?...` for backward compatibility unless a new card-specific helper is introduced.

Recommended clean design:

- Add `card_thumbnail_url_for_media()`.
- Add `card_thumbnail_url_for_path()`.
- Use those in library card payloads and job card decoration.
- Leave full media viewer image URLs untouched.

Also consider normalizing old DB index payloads at response time so stale `thumbnail_url` values using `/api/fs/preview` can be converted to `/api/media/thumbnail` without forcing an immediate full reindex.

## Risks And Mitigations

| Risk | Mitigation |
| --- | --- |
| Existing tests or clients expect `/api/library` to return an array. | Preserve array response unless `limit` or `page` is present. |
| Server-side pagination can change sort semantics. | Add `sort` parameter and document/index-supported ordering. Reset page on sort changes. |
| Live folder scans can still be expensive. | Scan only enough to fill the requested page plus one item for `has_next`. |
| Favorite sorting may move a card off the current page. | After favorite toggle, re-fetch the current page and clamp if needed. |
| Thumbnail cache competes with video transcode cache quota. | Document shared `MEDIA_CACHE_MAX_BYTES`; consider separate quota only if needed later. |
| First visit to a page still generates up to 50 thumbnails. | Use lazy `<img loading="lazy" decoding="async">`, cache hits afterward, and bounded generation concurrency. |
| `activeLibraryPath` root handling can break. | Preserve `null` versus empty string semantics in frontend state. |
| Old indexed thumbnail URLs remain original-size. | Normalize card thumbnail URLs when serving indexed payloads or reindex after deployment. |

## Verification Plan

Backend tests:

- Legacy `GET /api/library` returns an array.
- Paged `GET /api/library?limit=50&page=1` returns wrapper shape.
- Indexed page payload reports `total_count`, `total_pages`, and clamped page values.
- `mode=live&path=...&limit=50&page=1` returns up to 50 items and `has_next`.
- Favorite state is reflected in paged items.
- Cached thumbnail endpoint rejects unsafe paths, root paths, and symlinks.
- Thumbnail cache miss creates a file; cache hit reuses it.
- Source size or mtime change creates a new cache key.
- Media cache cleanup removes thumbnail files under `media-cache/thumbnails`.

Frontend/template tests:

- Template declares library pagination state and controls.
- `refreshLibraryItems()` can handle both array and wrapper responses.
- `refreshJobs()` no longer calls `renderLibrary()` on progress-only changes.
- Folder change and sort change reset library page to 1.

Manual checks:

- `/data` root with 0, 1-49, 50, and 51+ cards.
- Nested folder with 51+ cards.
- Sort modes on page 1 and page 2.
- Favorite toggle while sorted by favorite.
- Job completion while a matching library folder is open.
- Desktop and mobile layout around the library grid and pagination.
- First thumbnail load versus second thumbnail load in browser dev tools.

## Documentation Updates After Implementation

When implementation lands, update:

- `docs/feature-code-map.md`: library API pagination, frontend state, thumbnail endpoint/tests.
- `docs/architecture.md`: library pagination and `/config/media-cache/thumbnails`.
- `docs/configuration.md`: any new thumbnail env vars; existing media cache note if no new env vars.
- `docs/operations.md`: cache behavior, cleanup, and possible first-page thumbnail generation cost.
- `docs/patch-notes/YYYY-MM-DD.md`: behavior change and verification.
- `README.md`: only if the user-facing behavior needs explanation.

## Worker Review Summary

Three read-only workers reviewed the plan area:

- Backend/API worker: feasible without schema changes; preserve `/api/library` legacy array and add wrapper only for paged requests.
- Frontend worker: feasible; add page state and page controls, and gate `refreshJobs()` rerenders through a card signature.
- Thumbnail/cache worker: feasible; use `/config/media-cache/thumbnails`, lazy endpoint generation, existing safety helpers, and no thumbnail job rows in the first implementation.
