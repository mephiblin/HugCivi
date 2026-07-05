# Folder Tree Scaling Design 2026-07-06

Status: partially implemented. `/api/folders` remains a bounded root-direct compatibility tree, and `/api/folders/children` loads direct child folder rows on demand with `limit`/`cursor` pagination. The current search and move destination UI operate over the loaded tree plus lazy expansions. Server-side folder search and an optional folder index remain follow-up work.

## Background

HugCivi is a collecting and archiving tool. Large folder counts are normal, not an edge case. The sidebar tree should therefore not be treated as the full archive catalog.

As of 2026-07-06, `/api/folders` uses `initial_folder_tree()` and preloads only direct root children:

- initial API depth: 1
- direct child rows expose `has_children` and `children_loaded`
- route folders can be expanded lazily through `/api/folders/children`

The lower-level `build_folder_tree()` helper still has these compatibility guardrails for callers that request a deeper bounded tree:

- maximum depth: 4
- maximum total entries: 5,000
- maximum non-root children per folder: 1,000

Those budgets fix ordinary 100+ sibling folders for bounded-tree callers, but they are still a guardrail. Raising `max_children_per_folder` indefinitely only moves the failure point. The first scale step is lazy child loading: the browser can keep the initial tree bounded and request direct children for a folder only when that folder is expanded.

## Problem

When a single API response tries to include too many folders:

- `/api/folders` gets slower because the server must walk, allocate, and serialize more nodes.
- The JSON response grows and blocks sidebar refreshes.
- The browser creates too many DOM nodes, making expand/collapse, search, and move destination picking sluggish.
- A very large folder can consume most of `max_entries`, hiding deeper or later folders elsewhere.
- Client-only folder search remains limited to the tree nodes that were already loaded.

For an archive, the UI must scale by loading, paging, and searching folders on demand rather than rendering the whole filesystem tree.

## Design Principles

- The sidebar tree is a navigation map, not the complete catalog.
- The library grid is the main catalog view and should remain paged, sortable, and index-backed.
- Folder search can remain client-side while it is explicitly scoped to the loaded tree plus lazy expansions. It must become server-side when users need to find folders that have not been loaded.
- Folder mutation safety remains unchanged: all paths stay `/data` relative and must pass existing path/symlink checks.
- API changes should be additive so current `/api/folders` consumers keep working.
- Large folder operations should show loading state and never block the page while a whole tree is scanned.

## Target UX

### Sidebar Tree

The tree should load enough structure to orient the user:

- root route folders
- expanded ancestors for the current folder
- the currently selected folder's direct children
- direct children for expanded folders
- small child pages for very large folders, if later needed

The implemented baseline loads direct children on demand. If a single folder has too many direct children for one response, add a "load more" row instead of pushing every child into the DOM at once.

Example:

```text
stable-diffusion
  checkpoints
    model-000
    model-001
    ...
    더 보기
```

### Folder Search

Current folder search filters the loaded tree: the initial `/api/folders` nodes plus any child rows fetched through lazy expansion. Results select and expand paths that are already loaded.

Future server-side search should find folders outside the loaded tree. Results should jump the user to the matching folder and expand only the relevant ancestors.

Example:

```text
검색: illustrious

stable-diffusion/checkpoints/illustrious
stable-diffusion/loras/illustrious-character
```

### Move Destination Picker

The move picker should reuse the loaded lazy tree, but keep destination selection state separate from the main sidebar's active download target. Current destination search/selection is limited to loaded tree nodes plus lazy expansions; a later server-side search can extend it without changing the move safety checks.

## Implemented API Baseline

Keep the existing bounded eager endpoint:

```text
GET /api/folders
```

Lazy child loading:

```text
GET /api/folders/children?path=<relative-path>&limit=200&cursor=<child-name>
```

Response shape:

```json
{
  "ok": true,
  "path": "stable-diffusion/checkpoints",
  "items": [
    {
      "name": "model-001",
      "path": "stable-diffusion/checkpoints/model-001",
      "has_children": false,
      "children_loaded": true
    }
  ],
  "limit": 200,
  "next_cursor": "model-200",
  "has_more": true
}
```

Response rules:

- `path` is `/data` relative, never absolute.
- The endpoint returns only direct child folder rows for the requested folder.
- Each child row should be enough for the browser to render a node and know whether it can be expanded.
- Hitomi gallery archive folders are leaf nodes for Tree navigation. `/data/hitomi/<gallery>` and `/data/hitomi/listings/<listing>` should not be scanned for child directories, and a `_hitomi_metadata.json` sidecar marks custom-target Hitomi archives the same way.
- The response must exclude paths that fail the same `/data` and symlink safety checks used by other filesystem APIs.
- `limit` defaults to 200 and is capped at 500.
- `cursor` is the last child folder name from the previous page. Invalid cursors return a bounded 400 error.
- `has_more` and `next_cursor` drive "load more" behavior.

## Remaining Proposed APIs

Add server-side search when users need to find folders outside the loaded tree:

```text
GET /api/folders/search?q=<query>&scope=<relative-path>&limit=50
```

Response shape:

```json
{
  "ok": true,
  "query": "illustrious",
  "scope": "",
  "items": [
    {
      "name": "illustrious",
      "path": "stable-diffusion/checkpoints/illustrious",
      "depth": 3,
      "has_children": true
    }
  ],
  "truncated": false
}
```

Implementation rules:

- `path` and `scope` are `/data` relative, never absolute user paths.
- Results must not include symlink escapes.
- Search should cap visited folders, response size, and elapsed scan time.
- Hidden folders and `.part` paths should follow the same skip policy as the library indexer unless a future setting changes that.

## Folder Index Option

For very large archives, filesystem search can still become expensive. The stronger version is to persist folder rows in SQLite, similar to `library_items`.

Possible table:

```text
folder_items(
  path TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  parent_path TEXT NOT NULL,
  depth INTEGER NOT NULL,
  has_children INTEGER NOT NULL,
  mtime_ns INTEGER,
  stale INTEGER NOT NULL DEFAULT 0,
  scanned_at TEXT NOT NULL
)
```

The existing library indexer could update this table during filesystem walks, or a separate lightweight folder indexer could own it.

Benefits:

- instant folder search
- stable child pagination
- predictable "has more" behavior
- fewer full filesystem walks during UI interaction

Tradeoff:

- index freshness must be managed after create, rename, move, delete, download completion, and manual filesystem changes.

## Implementation Phases

Implemented baseline:

1. Keep current `/api/folders` as the bounded compatibility root tree.
2. Add `GET /api/folders/children` with path safety, direct-child rows, `limit`, `cursor`, `has_more`, and `next_cursor`.
3. Update sidebar expansion to request children for expanded folders.
4. Keep folder search and move destination picking scoped to the loaded tree plus lazy expansions.

Remaining scale work:

1. Add `GET /api/folders/search` as bounded filesystem search when loaded-tree search is not enough.
2. Wire sidebar search and move destination picker to server search.
3. Add optional SQLite folder index if real archives make filesystem search too slow.
4. Revisit `FOLDER_TREE_MAX_ENTRIES` and `FOLDER_TREE_MAX_CHILDREN_PER_FOLDER`; keep them as compatibility guardrails, not scale targets.

## Non-Goals

- Do not make `/api/folders` return the full archive tree.
- Do not rely on larger `max_children_per_folder` values as the primary scaling strategy.
- Do not expose absolute filesystem paths in lazy child responses or future folder search.
- Do not merge move destination selection with the active download target state.

## Verification Plan

Backend tests:

- `/api/folders/children` returns direct children only.
- pagination is stable for more than one page of child folders.
- root and nested paths preserve `/data` safety rules.
- symlink escapes are excluded.
- invalid cursors and missing folders return bounded errors.

Frontend tests:

- expanding a folder fetches its children without reloading the whole page.
- "load more" appends children without losing expanded state.
- folder search includes lazy-expanded folders and stays scoped to loaded nodes.
- move destination picker can lazy-expand and select a folder without changing the main active folder until confirmed.
- future server search result selection expands ancestors and opens the library path.

Future server-search tests:

- `/api/folders/search` finds folders outside the eager tree budget.
- search respects limit and truncated state.

Manual checks:

- 1,000+ sibling folders remain usable in desktop and mobile sidebar.
- 10,000+ total folders do not freeze the page on initial load.
- folder create, rename, move, delete, and download completion keep the visible tree coherent.

## Current Decision

The current implemented direction is to keep `/api/folders` bounded and use lazy folder loading for expansion. That is the right first archive-scale step because it avoids turning the full folder hierarchy into one DOM tree. The next scale step is server-side folder search, and the stronger long-term option is a folder index.
