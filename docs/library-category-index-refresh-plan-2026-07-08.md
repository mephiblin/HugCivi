# Library Category Index Refresh Plan 2026-07-08

Status: partially implemented on 2026-07-08. SQLite `library_items` now has source/category/search columns, selected-folder pages are DB-first in normal index mode, `source_group` filters are wired through the API/UI, and scoped synchronous `/api/library/reindex` supports `path`, `source_group`, and `category`. Internal `library_reindex` jobs, direct parent-only navigation, and provider-specific subcategory UI remain future work.

## Purpose

Selected-folder library pagination now shows exact page totals, but the first completed live scan can still be expensive on NAS storage. The current short in-memory page cache improves page turns after the scan, but it does not remove the underlying filesystem scan cost.

The next performance step is to make SQLite the primary materialized catalog for folder and category navigation. The app should use DB rows for ordinary library pages, selected folders, and source/category filters, then reserve live scanning for fallback and explicit refresh work.

This plan keeps HugCivi's current architecture:

- archive files remain under `/data`
- app state and indexes remain in `/config/jobs.sqlite3`
- no Redis, Celery, Elasticsearch, or second service
- schema changes are additive
- existing `/api/library` legacy behavior remains compatible

## Current Baseline

Current `library_items` rows include:

- `path`
- `kind`
- `name`
- `target_dir`
- `payload_json`
- `size_bytes`
- `mtime_ns`
- `ctime_ns`
- `stale`
- `updated_at`
- `scanned_at`

The serialized payload already carries many useful display fields, including provider/source-like values for Civitai, ASMR.one, gallery-dl, generic downloads, documents, and media archives. However, SQLite cannot efficiently filter or sort those values while they remain only inside `payload_json`.

Current request behavior:

- root/index pages can use `library_items` with `LIMIT`/`OFFSET`
- selected folders use `/api/library?mode=live&path=...`
- completed selected-folder live scans are cached briefly in memory
- very large or incomplete live scans keep unknown totals
- manual `/api/library/reindex` resets and scans a larger global batch synchronously

The remaining bottleneck is that selected folders and source/category groupings are not yet first-class DB queries.

## Goals

- Make selected-folder page navigation use SQLite first when the folder's indexed rows are usable.
- Add source/category-aware DB columns and indexes so pages can filter by `civitai`, `gallerydl`, `yt-dlp`, `hitomi`, `asmrone`, `generic`, and related display categories.
- Add manual refresh for a selected folder or source/category without forcing a full `/data` rescan.
- Keep live scanning as a correctness fallback for newly restored files, incomplete indexes, and operator-side NAS edits.
- Keep exact `total_count` and `total_pages` for DB-backed pages.
- Keep memory caching small and short-lived; SQLite should do the durable acceleration.

## Non-Goals

- Do not store large archive binaries, thumbnails, or media payloads in SQLite.
- Do not add a full-text search engine or second service.
- Do not remove live mode; it remains the recovery/fallback path.
- Do not rely only on folder names for category detection.
- Do not make every thumbnail or index refresh a separate visible job row unless the work is long-running.

## Data Model Plan

Add searchable columns to `library_items` through additive migrations:

```sql
ALTER TABLE library_items ADD COLUMN source TEXT NOT NULL DEFAULT '';
ALTER TABLE library_items ADD COLUMN model_category TEXT NOT NULL DEFAULT '';
ALTER TABLE library_items ADD COLUMN parent_path TEXT NOT NULL DEFAULT '';
ALTER TABLE library_items ADD COLUMN sort_title TEXT NOT NULL DEFAULT '';
ALTER TABLE library_items ADD COLUMN source_group TEXT NOT NULL DEFAULT '';
```

Recommended indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_library_items_parent_sort
  ON library_items(stale, parent_path, sort_title, path);

CREATE INDEX IF NOT EXISTS idx_library_items_path_sort
  ON library_items(stale, path, sort_title);

CREATE INDEX IF NOT EXISTS idx_library_items_source_sort
  ON library_items(stale, source_group, sort_title, path);

CREATE INDEX IF NOT EXISTS idx_library_items_source_parent_sort
  ON library_items(stale, source_group, parent_path, sort_title, path);

CREATE INDEX IF NOT EXISTS idx_library_items_category_sort
  ON library_items(stale, model_category, sort_title, path);

CREATE INDEX IF NOT EXISTS idx_library_items_mtime
  ON library_items(stale, mtime_ns, path);
```

`sort_title` should store a normalized lowercase title used for A-Z/Z-A ordering. This avoids depending on SQLite expression indexes and keeps ordering predictable across hosts.

`parent_path` should store the `/data`-relative parent folder for direct parent queries. Prefix queries can still use `path = ? OR path LIKE ?`, but direct-folder views should prefer `parent_path = ?` when the UI means "children of this selected folder only."

`source_group` should be the stable filter value used by the UI/API. Suggested values:

| Source Group | Inputs |
| --- | --- |
| `civitai` | Civitai model/version/image sidecars, Civitai jobs |
| `gallerydl` | gallery-dl archives and wrapped gallery-dl jobs |
| `ytdlp` | yt-dlp/YouTube archives, including `ytdl:` source URLs |
| `hitomi` | Hitomi gallery and listing outputs |
| `asmrone` | ASMR.one work archives |
| `generic` | Generic HTTP downloads |
| `huggingface` | Hugging Face archive folders |
| `comfyui` | ComfyUI workflow bundles |
| `media` | Plain media/document folders not tied to a provider |
| `unknown` | Valid library item with no reliable source |

## Classification Rules

Classification must prefer metadata over path guesses.

Recommended priority:

1. Current library payload fields from `library_item_for_path`.
2. Provider sidecars such as `_civitai_metadata.json`, `_civitai_image_metadata.json`, `_asmrone_metadata.json`, `_hitomi_metadata.json`, `_archive_metadata.json`, and yt-dlp `*.info.json`.
3. Existing job row metadata when the target path still maps to a known job.
4. Path prefix as a final fallback only.

The extractor should be centralized, for example:

- `library_index_fields_for_item(item, path)`
- `normalize_library_source_group(source, source_url, metadata, path)`
- `library_item_sort_title(item)`

This keeps DB inserts, refresh jobs, and tests aligned.

## Refresh Model

Use SQLite as the durable cache and keep refresh work scoped.

### Refresh Scopes

Supported scopes should be additive:

| Scope | Meaning |
| --- | --- |
| `all` | Existing full/global indexing behavior. |
| `path` | Refresh one selected folder or subtree. |
| `source_group` | Refresh items for one provider/category group. |
| `path + source_group` | Refresh a category inside a selected folder. |

### Refresh State

Use existing `library_scan_state` for scoped progress before adding a new table. Suggested keys:

```text
library.refresh.<scope_hash>.running
library.refresh.<scope_hash>.cursor
library.refresh.<scope_hash>.processed
library.refresh.<scope_hash>.indexed
library.refresh.<scope_hash>.complete
library.refresh.<scope_hash>.started_at
library.refresh.<scope_hash>.finished_at
library.refresh.<scope_hash>.error
```

The scope hash should be derived from stable JSON, such as:

```json
{"path":"stable-diffusion/loras","source_group":"civitai"}
```

For long-running refresh, prefer an internal job kind such as `library_reindex` because it is server-local work over `/data`, not an external download. The internal job payload should include only `/data`-relative paths and filter values.

### Refresh Triggers

Initial implementation should support:

- manual selected-folder refresh from the library UI
- manual category refresh from the library UI
- `/api/library/reindex` extended with optional `path` and `source_group`
- background indexer continues normal global incremental scans
- download completion opportunistically upserts the finished target folder into `library_items`
- app-driven create/rename/move/delete continues to update/clear related index rows and page cache

NAS-side DSM edits should be picked up by manual refresh, the background indexer, or live fallback. Avoid trying to watch the filesystem continuously.

## API Plan

Keep existing compatibility:

- `GET /api/library` without `limit` or `page` can still return the legacy array.
- Existing `mode=index|live`, `path`, `limit`, `page`, and `sort` remain valid.

Add optional filters:

```text
GET /api/library?limit=50&page=1&path=<relative-path>&source_group=civitai&category=LoRA&sort=az
```

Response remains the paged wrapper:

```json
{
  "ok": true,
  "items": [],
  "page": 1,
  "limit": 50,
  "total_count": 0,
  "total_pages": 1,
  "has_next": false,
  "mode": "index",
  "path": "stable-diffusion/loras",
  "sort": "az",
  "paged": true,
  "source_group": "civitai",
  "category": "LoRA",
  "index_status": {
    "usable": true,
    "refreshing": false
  }
}
```

Extend refresh endpoints without breaking the current reindex button:

```text
POST /api/library/reindex
POST /api/library/reindex?path=<relative-path>
POST /api/library/reindex?source_group=civitai
POST /api/library/reindex?path=<relative-path>&source_group=civitai
```

If refresh can exceed one request budget, return an internal job or refresh state:

```json
{
  "ok": true,
  "queued": true,
  "job_id": 123,
  "scope": {"path":"stable-diffusion/loras","source_group":"civitai"}
}
```

## Query Strategy

DB-backed selected-folder pages should be preferred when there are usable indexed rows or a completed scoped refresh for that path.

Recommended order for `/api/library` with `path`:

1. Normalize and safety-check `path` through existing `/data` helpers.
2. If `mode=live`, keep current live behavior.
3. If `mode=index` or mode is omitted:
   - query DB with `path_prefix`/`parent_path` and optional `source_group`/`category`
   - return exact totals when rows are available or scoped index state is usable
   - include `index_status`
4. If DB has no usable rows and indexing is incomplete:
   - use current selected-folder live fallback
   - optionally trigger a background scoped refresh

This preserves correctness while making the common path cheap.

## Frontend Plan

Add library-level source filters after the selected-folder controls:

- `전체`
- `Civitai`
- `gallery-dl`
- `yt-dlp`
- `Hitomi`
- `ASMR`
- `Generic`
- `Media`

Add refresh affordances:

- refresh active folder
- refresh active category
- refresh active folder + category when both are selected

Use icon buttons with tooltips rather than explanatory in-app text. Show a compact status when refresh is active, such as a spinner beside the refresh button and the current indexed count in the existing library count area.

Frontend state additions:

- `currentLibrarySourceGroup`
- `currentLibraryCategory`
- `currentLibraryIndexStatus`

Changing folder, source group, category, or sort should reset the library page to 1.

## Implementation Phases

### Phase 0: Baseline Metrics

- Add lightweight timing logs or test instrumentation around selected-folder library requests.
- Capture before/after timings for:
  - first selected-folder page
  - page 2 after page 1
  - category filter page
  - manual refresh

### Phase 1: DB Columns And Helpers

- Add additive migrations for `source`, `source_group`, `model_category`, `parent_path`, and `sort_title`.
- Update `db.upsert_library_item`.
- Backfill new columns during normal index scans and manual reindex.
- Add DB helpers:
  - `list_library_index_items(..., source_group="", category="")`
  - `count_library_index_items(..., source_group="", category="")`
  - `library_index_where(...)` with filter support
- Keep `payload_json` as the UI payload source.

### Phase 2: Scoped Refresh Backend

- Add scoped path/source iterators that can skip unrelated provider roots when safe.
- Add refresh state keys in `library_scan_state`.
- Add a `library_reindex` internal job if scoped refresh cannot reliably finish within a normal request.
- Extend `/api/library/reindex` with optional `path`, `source_group`, and `category`.
- Clear selected-folder live page cache after refresh completion.

### Phase 3: API Uses DB First For Selected Folders

- Update `library_items_page_payload` so selected-folder `mode=index` queries SQLite before live fallback.
- Keep `mode=live` as an explicit force-live option.
- Return `index_status` metadata for the browser.
- Preserve exact totals for DB-backed pages.
- Preserve unknown totals for incomplete live fallback.

### Phase 4: Frontend Filters And Refresh Controls

- Add source/category filter state.
- Add compact category filter controls.
- Add refresh icon buttons for the active scope.
- Make page/sort/filter/folder changes request the filtered paged API.
- Keep stale cards cleared into loading state while refreshing.

### Phase 5: Opportunistic Upserts

- On successful download completion, upsert the finished target path into `library_items` immediately when possible.
- On Civitai refresh completion, update the existing item row.
- On ASMR.one/gallery-dl/yt-dlp/generic completion, upsert from the written sidecars.
- Do not block job completion on noncritical index failures; log and let the background indexer recover.

### Phase 6: Documentation And Release

- Update `docs/architecture.md`, `docs/feature-code-map.md`, `docs/configuration.md`, and `docs/operations.md`.
- Add patch notes with schema/API/UI details and verification.
- Use `SKILL_Dev/skill_build.md` before any image push.

## Test Plan

Backend tests:

- old DB schemas migrate with new columns and indexes
- `upsert_library_item` fills searchable columns from payload metadata
- Civitai, ASMR.one, gallery-dl, yt-dlp, generic, Hitomi, and plain media fixtures classify into expected `source_group`
- DB list/count helpers filter by path, source group, category, and combined path + source group
- selected-folder index mode does not call live scan when DB rows are usable
- selected-folder fallback still calls live scan when DB rows are missing and index state is incomplete
- manual scoped refresh updates rows and clears stale/deleted rows within scope
- app rename/move/delete keeps new columns and prefixes consistent
- favorite sorting still uses current favorites, not stale payload state

Frontend/template tests:

- library API requests include active source/category filters
- changing source/category resets page to 1
- refresh buttons call scoped reindex payloads
- known totals still render all page numbers
- unknown live fallback still renders previous/current/next

Runtime tests:

- downloader completion upserts a target row without breaking job completion
- Civitai refresh updates DB item source/category fields
- gallery-dl/yt-dlp sidecar title enrichment still works after DB filtering

Verification commands:

```bash
git diff --check
python3 -m py_compile app/main.py app/db.py app/internal_jobs.py app/downloader.py
python3 -m pytest -q -p no:cacheprovider tests/test_review_fixes.py tests/test_downloader_runtime.py
python3 -m pytest -q -p no:cacheprovider
```

## Acceptance Criteria

- Indexed selected-folder page 1 and page 2 do not recursively scan the NAS folder.
- A selected folder with 201 indexed cards reports 5 pages and returns only 1 card on page 5.
- Category filters return exact counts and stable page totals from SQLite.
- Manual refresh for `source_group=civitai` does not reset unrelated gallery-dl or ASMR.one rows.
- Manual refresh for a selected folder updates new/removed cards from DSM-side edits.
- Existing live fallback still restores filesystem-backed cards when the DB index is empty or incomplete.
- Page turns after an indexed query are dominated by SQLite queries, not filesystem traversal.
- No large binary archive content is stored in SQLite.

## Risks And Mitigations

| Risk | Mitigation |
| --- | --- |
| DB rows become stale after DSM-side edits. | Keep manual refresh and background indexer; live fallback remains available. |
| Category classification differs between providers. | Centralize normalization and cover provider fixtures in tests. |
| JSON payload and extracted columns drift. | Fill columns from the same normalized item payload at upsert time. |
| Scoped refresh deletes unrelated rows. | Scope stale marking by normalized path/source filters and test mixed-provider folders. |
| Large refresh blocks API requests. | Use an internal `library_reindex` job for long-running scopes. |
| Source naming becomes inconsistent (`gallerydl` vs `gallery-dl`). | Store stable API values in `source_group`, keep labels in frontend only. |

## Open Decisions

- Whether library source filters should be shown as a horizontal segmented control or a compact menu on mobile.
- Whether category filters should expose provider-specific subcategories immediately or wait until the source-group index is proven stable.
- Whether scoped refresh should start as synchronous for small scopes and later promote to an internal job, or always use an internal job for one consistent model.
- Whether `parent_path` should represent direct-child views only while prefix queries remain separate, or whether the UI should expose both direct and recursive selected-folder modes.
