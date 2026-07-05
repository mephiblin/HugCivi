# Folder Tree Scaling Design 2026-07-06

Status: future design. Current code still uses bounded eager folder trees; see `app/main.py::build_folder_tree`.

## Background

HugCivi is a collecting and archiving tool. Large folder counts are normal, not an edge case. The sidebar tree should therefore not be treated as the full archive catalog.

As of 2026-07-06, `/api/folders` builds an eager tree with these default budgets:

- maximum depth: 4
- maximum total entries: 5,000
- maximum non-root children per folder: 1,000

Those budgets fix ordinary 100+ sibling folders, but they are still a guardrail. Raising `max_children_per_folder` indefinitely only moves the failure point.

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
- Folder search must be server-side when the archive is larger than the eager tree budget.
- Folder mutation safety remains unchanged: all paths stay `/data` relative and must pass existing path/symlink checks.
- API changes should be additive so current `/api/folders` consumers keep working.
- Large folder operations should show loading state and never block the page while a whole tree is scanned.

## Target UX

### Sidebar Tree

The tree should load enough structure to orient the user:

- root route folders
- expanded ancestors for the current folder
- the currently selected folder's direct children
- small child pages for large folders

When a folder has many children, show a "load more" row instead of pushing every child into the DOM at once.

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

Folder search should find folders outside the loaded tree. Results should jump the user to the matching folder and expand only the relevant ancestors.

Example:

```text
검색: illustrious

stable-diffusion/checkpoints/illustrious
stable-diffusion/loras/illustrious-character
```

### Move Destination Picker

The move picker should reuse the lazy tree/search behavior, but keep destination selection state separate from the main sidebar's active download target.

## Proposed APIs

Keep the existing eager endpoint:

```text
GET /api/folders
```

Add lazy child loading:

```text
GET /api/folders/children?path=<relative-path>&limit=200&cursor=<cursor>
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
      "mtime": "2026-07-06T00:00:00+00:00"
    }
  ],
  "limit": 200,
  "next_cursor": "model-200",
  "has_more": true
}
```

Add server-side search:

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

1. Keep current `/api/folders` as the compatibility root tree.
2. Add `GET /api/folders/children` with path safety, limit, cursor, and tests.
3. Update sidebar expansion to request children only for the expanded folder.
4. Add a "load more" row for child pagination.
5. Add `GET /api/folders/search` as bounded filesystem search.
6. Wire sidebar search and move destination picker to server search.
7. Add optional SQLite folder index if real archives make filesystem search too slow.
8. Revisit `FOLDER_TREE_MAX_ENTRIES` and `FOLDER_TREE_MAX_CHILDREN_PER_FOLDER`; keep them as compatibility guardrails, not scale targets.

## Non-Goals

- Do not make `/api/folders` return the full archive tree.
- Do not rely on larger `max_children_per_folder` values as the primary scaling strategy.
- Do not expose absolute filesystem paths in folder search or lazy child responses.
- Do not merge move destination selection with the active download target state.

## Verification Plan

Backend tests:

- `/api/folders/children` returns direct children only.
- pagination is stable for more than one page of child folders.
- root and nested paths preserve `/data` safety rules.
- symlink escapes are excluded.
- invalid cursors and missing folders return bounded errors.
- `/api/folders/search` finds folders outside the eager tree budget.
- search respects limit and truncated state.

Frontend tests:

- expanding a folder fetches its children without reloading the whole page.
- "load more" appends children without losing expanded state.
- server search result selection expands ancestors and opens the library path.
- move destination picker can search and select a folder without changing the main active folder until confirmed.

Manual checks:

- 1,000+ sibling folders remain usable in desktop and mobile sidebar.
- 10,000+ total folders do not freeze the page on initial load.
- folder create, rename, move, delete, and download completion keep the visible tree coherent.

## Current Decision

The current larger eager-tree budget is acceptable as an immediate fix, but the long-term archive design should be lazy folder loading plus server-side folder search. For a collecting/archive product, the full folder hierarchy belongs in an index and search surface, not in one DOM tree.
