# Frontend Visual Performance And Maintainability Audit 2026-07-11

Status: verified audit and remediation planning reference.

Baseline: `origin/main` commit `6b57cf31a27e532d33e5ade05ef1ba9b9e20eb3c` (`Index completed downloads immediately`).

This is a dated review record. Current code and the current [Architecture](architecture.md), [Feature and Code Map](feature-code-map.md), and [Operations](operations.md) remain authoritative after later changes. Re-run the checks in this document before treating a finding as unresolved.

## Scope

This audit covers the browser-visible visual path and its long-running process state:

- job information cards in the desktop table and mobile card list
- library asset cards and representative image selection
- `/api/media/thumbnail` generation, cache lookup, and request locking
- deferred thumbnail queues and `IntersectionObserver` lifecycle
- desktop/mobile responsive layout behavior
- the static `frontend-preview.html` design reference
- frontend source organization and regression-test strategy

The review does not evaluate the visual quality of every provider's real production archive. External provider downloads and a real NAS saturation test were not run.

## Architecture Path Reviewed

```text
/data files and sidecars
  -> library_item_for_path() / completed job decoration
      -> representative media and thumbnail_url
          -> SQLite library payload or jobs API payload
              -> renderJobs() / renderLibrary()
                  -> deferred browser thumbnail queue
                      -> /api/media/thumbnail
                          -> /config/media-cache/thumbnails
```

The overall boundary is sound: durable media stays in `/data`, cached visual derivatives stay under `/config/media-cache`, and the browser receives URLs rather than binary data in SQLite. The findings below concern selection determinism, DOM lifecycle, in-memory registry lifecycle, and maintainability at the current frontend size.

## Executive Conclusion

The current card layout and thumbnail delivery baseline work correctly in ordinary desktop/mobile use. The browser rendered a 50-card page without horizontal overflow, used six desktop columns and two mobile columns, and completed deferred thumbnail loading without console errors.

The implementation is not yet safe to call fully stable for a browser tab and server process kept alive for days or weeks. The highest-priority correctness issue is nondeterministic representative-media selection. The main long-running performance issue is unconditional job DOM replacement every 2.5 seconds combined with incomplete thumbnail observer cleanup. Browser and server thumbnail registries also have no eviction policy.

| ID | Priority | Area | Confirmed Risk |
| --- | --- | --- | --- |
| F-01 | High | representative media | Card thumbnail and file type depend on filesystem enumeration order. |
| F-02 | High | job polling/DOM | Unchanged polls replace desktop and mobile job DOM, lose focus, and retain stale observed nodes. |
| F-03 | Medium | in-memory lifecycle | Browser thumbnail states and server thumbnail locks grow without eviction. |
| F-04 | Medium | frontend structure | A 9,400-line template and global script make visual changes increasingly coupled. |
| F-05 | Low | static preview | The documented design preview has drifted from the production mobile layout. |
| F-06 | Medium | regression coverage | Source-string assertions do not verify focus, node identity, observer cleanup, or real layout. |

## Confirmed Strengths

- Library pages are capped and paginated at 50 cards.
- Card images use a stable `3 / 4` aspect ratio and `object-fit: cover`.
- Card layout uses CSS containment, intrinsic height, and `content-visibility: auto` to limit offscreen paint work.
- Thumbnail URLs include a source file version key and cached responses use long-lived private immutable caching.
- Ready thumbnails and cold thumbnails use separate browser lanes, with cold generation capped at three active requests.
- A selected-folder thumbnail backfill exists as one internal job rather than one job row per image.
- Thumbnail files remain on disk rather than in SQLite.
- The backend validates `/data` scope and rejects symlink thumbnail sources.
- The media viewer uses full-size/media endpoints separately from small card JPEGs.
- Desktop and mobile test rendering showed no horizontal overflow and no browser console error.

## F-01: Representative Media Selection Is Nondeterministic

Priority: High

### Evidence

`app/main.py::library_item_for_path()` asks `media_files_for_path()` for one item and uses that item for card category, media type, file format, and thumbnail selection.

`media_files_for_path()` currently:

1. iterates `Path.rglob("*")`
2. appends the first media file returned by the filesystem
3. stops immediately when `limit <= 1`
4. sorts the one-item result afterward

Filesystem enumeration order is not a product-level ordering guarantee. Sorting after the early break cannot change which file was selected.

The current checkout reproduced both existing regression failures:

- `test_media_thumbnail_backfill_job_uses_card_representatives` expected `cover.jpg`, but the backfill used `z-extra.png`.
- `test_text_and_markdown_files_are_readable_media_cards` expected the naturally first `00_readme_...txt`, but the card was classified from `notes.md` as `markdown`.

The same archive may therefore display a different image or format after copying to another filesystem, restoring a backup, or rebuilding a directory.

### Impact

- ASMR and other archives can show an attachment instead of the intended cover.
- Card category, file format, and media type may vary across hosts.
- Thumbnail backfill can permanently cache the wrong representative image.
- DB-backed library results can retain the wrong selection until the next sync/reindex.
- A scan-budget optimization has become a correctness regression.

### Recommended Remediation

- Preserve `MEDIA_FILE_SCAN_MAX_FILES`; do not restore an unbounded recursive scan.
- For `limit=1`, scan only up to the configured budget while maintaining the smallest candidate by `natural_path_key` instead of breaking at the first media file.
- For larger limits, maintain deterministic bounded selection before returning sorted results.
- Centralize representative-media policy so `library_item_for_path()`, thumbnail backfill, and archive cover logic cannot disagree.
- Give explicit sidecar or archive cover metadata precedence when available; use natural path ordering as the fallback.
- Mark affected library rows stale or document that a scoped sync/reindex is required after the fix.

### Acceptance Criteria

- Reversing file creation/enumeration order does not change the selected card representative.
- `cover.jpg` remains the representative for the existing ASMR regression fixture.
- `00_readme_...txt` remains the first document for the existing mixed text/Markdown fixture.
- The scan never visits more than `MEDIA_FILE_SCAN_MAX_FILES` when that budget is enabled.
- The full test suite passes on filesystems with different directory enumeration behavior.

## F-02: Job Polling Replaces Unchanged DOM And Leaks Observer Targets

Priority: High

### Evidence

The page schedules `refreshJobs` every 2.5 seconds. Every successful response calls `renderJobs(currentJobs)` without comparing a job-view signature.

`renderJobs()` replaces the desktop table body with `innerHTML`; `renderMobileJobs()` also replaces the full mobile list. Both representations are built even though only one is visible at a given viewport size.

Library rendering calls `cleanupDeferredThumbnails(libraryGrid)` before replacing card HTML. The desktop jobs table and mobile jobs list do not perform equivalent cleanup before replacement.

Browser verification produced these results:

- A focused job delete button became `document.body` after the next polling render.
- With a 12-second cold-thumbnail delay to represent slow NAS/ffmpeg work, three polling cycles left 340 targets registered with the thumbnail observer.
- 255 of those 340 targets were already disconnected from the DOM.

### Impact

- Keyboard users lose focus every 2.5 seconds while the jobs view is open.
- Text selection, hover state, and transient element state are discarded.
- The browser allocates and parses desktop plus mobile job markup repeatedly when job data has not changed.
- `IntersectionObserver` can retain removed image elements until page reload.
- Long-lived tabs with unviewed cold thumbnails can accumulate detached DOM targets.

### Recommended Remediation

- Compute a job-render signature from fields that affect visible output and skip `renderJobs()` when it has not changed.
- Prefer keyed updates using `data-job-id`; patch status, progress, filename, actions, and thumbnail state without replacing the full row/card.
- Preserve the focused control by stable job ID/action when a changed row must be replaced.
- Call `cleanupDeferredThumbnails()` on both the jobs table body and mobile jobs list before any fallback `innerHTML` replacement.
- Avoid building the hidden desktop/mobile representation when a safe viewport-specific path is practical, or at minimum avoid replacing the hidden representation when its data is unchanged.
- Pause or reduce purely visual polling when `document.visibilityState !== "visible"`; server job execution must remain unaffected.

### Acceptance Criteria

- Three unchanged job polls keep the same row/card node identity.
- Focus on a job action remains on the same logical action across unchanged polls.
- Replacing/removing a job leaves zero disconnected elements registered in the thumbnail observer.
- No additional thumbnail request is scheduled solely because an unchanged poll occurred.
- Desktop and mobile job displays still update within the current polling interval when visible state actually changes.

## F-03: Thumbnail Registries Have No Eviction Policy

Priority: Medium

### Evidence

The browser holds one `thumbnailRequests` Map entry per unique thumbnail URL. Loaded and failed states are retained; no `delete()` or bounded cleanup path exists.

The server holds `MEDIA_THUMBNAIL_LOCKS`, keyed by a hash that includes resolved source path, file size, modification time, requested size, and cache version. `media_thumbnail_lock()` inserts new locks but never removes them.

Changing a file creates a new key, so old lock entries remain even if media-cache cleanup deletes the corresponding JPEG.

### Impact

- Browsing many pages grows browser state for the lifetime of the tab.
- Large thumbnail backfills and modified source files grow server lock state for the lifetime of the process.
- Media cache TTL/quota cleanup removes files but does not reclaim these in-memory registries.
- The growth is individually small per entry but unbounded over long-running personal NAS use.

### Recommended Remediation

- Remove browser states when they are not queued/loading, contain no connected elements, and exceed a bounded recent-use cache.
- Use an LRU/TTL bound for reusable loaded/error URL states; rely on HTTP/browser cache for repeated image bytes.
- Add explicit queue removal for URLs whose last element was detached before request start.
- Replace the server lock dictionary with a ref-counted keyed lock manager that removes a lock only after no caller can still acquire or wait on it.
- Include thumbnail/transcode lock-registry size in a debug/status payload only if an operator-facing diagnostic is useful; do not expose source paths.

### Acceptance Criteria

- Visiting thousands of unique card URLs does not leave thousands of inactive browser states indefinitely.
- Completing thousands of unique thumbnail generations leaves the server lock registry close to active/waiting work, or below a documented bound.
- Two simultaneous requests for the same uncached source still generate only one thumbnail.
- Cache cleanup and source-file replacement do not create permanent registry growth.

## F-04: Frontend Source Has Exceeded A Safe Single-File Size

Priority: Medium

### Evidence

At the audited commit:

- `app/templates/index.html`: 9,400 lines, approximately 436 KB
- `app/static/style.css`: 5,934 lines, approximately 103 KB

The template contains markup, Jinja bootstrap data, global state, API calls, event listeners, render functions, thumbnail scheduling, transfer settings, subscriptions, media viewer logic, workflow viewer logic, and modal focus behavior in one script scope.

### Impact

- A visual optimization can unintentionally affect jobs, library, media, subscriptions, and settings.
- State ownership and cleanup responsibilities are difficult to identify.
- Source-string tests become attractive because functions are not importable in isolation.
- Merge conflicts and review cost increase as unrelated UI work touches the same file.
- The observer cleanup omission in F-02 is an example of lifecycle behavior diverging between surfaces in the same file.

### Recommended Remediation

Keep the current FastAPI/Jinja/single-container architecture. A framework rewrite is not required. Split incrementally into browser-native ES modules:

1. `app/static/js/api.js` and pure formatting/path helpers
2. `app/static/js/thumbnail-loader.js`
3. `app/static/js/jobs-view.js`
4. `app/static/js/library-view.js`
5. `app/static/js/media-viewer.js` and `workflow-viewer.js`
6. settings/transfer/subscription modules after stable boundaries exist

Pass Jinja initial data through one JSON bootstrap element or a small explicit initializer rather than relying on many globals. Keep API shapes additive and do not split the backend into a second service.

### Acceptance Criteria

- Thumbnail queue state has one clear owner and a documented teardown API.
- Jobs and library rendering can be tested without evaluating the whole template.
- Global mutable variables are limited to one explicit application bootstrap object.
- Current desktop/mobile flows and Basic Auth behavior remain unchanged.
- No React/Vue build tool or second runtime service is introduced unless separately approved.

## F-05: Static Frontend Preview Has Drifted

Priority: Low

### Evidence

`README.md` describes `frontend-preview.html` as the design-check file. The preview still renders five mobile actions (`입력`, `작업`, `보관함`, `폴더`, `설정`), while the production template renders four (`입력/작업`, `보관함`, `폴더`, `설정`).

The shared mobile CSS defines four columns. At 390 px, the stale fifth preview action wraps to a second row and makes the preview tab bar approximately 99 px tall instead of the production bar's approximately 55 px.

The preview also predates current source filtering, pagination, transfer, maintenance, and thumbnail lifecycle behavior.

### Impact

- Manual visual review can approve or reject a layout that is not deployed.
- Future contributors may fix preview-only defects or miss production defects.
- The file's documented purpose is no longer reliable.

### Recommended Remediation

Choose one policy:

- generate the preview from the production template with deterministic fixture data, or
- replace it with a small development fixture route, or
- mark the file historical/remove the design-check claim if it will not be maintained.

Avoid manually duplicating production navigation and card markup.

### Acceptance Criteria

- Preview and production share the same mobile navigation count and labels.
- A simple test compares critical navigation/card controls between preview and production, or the preview is explicitly marked historical.
- Mobile preview uses one bottom navigation row at supported widths.

## F-06: Frontend Tests Verify Markers More Than Behavior

Priority: Medium

### Evidence

The deferred-thumbnail regression test mainly reads `index.html` and `style.css` as text and asserts that function names and CSS markers exist. This confirms wiring but does not execute polling, focus, observer, or layout behavior.

The full Python suite on the audited checkout reported:

```text
296 passed, 2 failed, 1 warning
```

Both failures are real representative-selection regressions from F-01. The same tests can pass on a filesystem whose enumeration order happens to match the expectation, so the fixture must explicitly vary ordering.

### Recommended Remediation

Add a small browser smoke suite, preferably with Playwright or an equivalent existing project-approved browser runner:

- desktop and mobile overflow/card-column check
- unchanged polling preserves node identity and focus
- removed card/job thumbnails are unobserved
- ready/cold concurrency limits are respected
- page navigation does not retain unbounded inactive thumbnail states
- media card keyboard activation still opens the viewer

Keep fast source-marker assertions only for static invariants that are impractical to exercise behaviorally.

### Acceptance Criteria

- CI executes at least one authenticated production-template browser test.
- The F-02 focus and detached-observer reproductions fail before the fix and pass afterward.
- Representative-media tests supply or mock reversed enumeration orders.
- Responsive checks assert `scrollWidth <= clientWidth` at desktop and mobile widths.

## Recommended Remediation Order

### Immediate: Correctness Baseline

1. Fix F-01 while preserving the scan budget.
2. Make the two currently failing tests deterministic across enumeration orders.
3. Run scoped library sync/reindex verification with the corrected representative policy.

### Next: Long-Running Browser Safety

1. Add a job-view signature and skip unchanged rerenders.
2. Add jobs/mobile thumbnail cleanup before fallback DOM replacement.
3. Bound browser thumbnail states and server lock states.
4. Add behavioral tests for focus and observer teardown.

### Then: Maintainability

1. Extract the thumbnail loader as the first ES module.
2. Extract jobs and library rendering with keyed update boundaries.
3. Add authenticated browser smoke coverage.
4. Synchronize or retire `frontend-preview.html`.

## Non-Goals

- Do not add Redis, Celery, Elasticsearch, or a second frontend service for these findings.
- Do not store thumbnail binaries in SQLite.
- Do not remove current `/data` path safety or symlink checks.
- Do not increase cold thumbnail concurrency as a substitute for lifecycle cleanup.
- Do not add a filesystem watcher as part of this remediation.
- Do not perform a framework rewrite before the measured lifecycle issues are fixed.

## Verification Record

Audit environment:

- date: 2026-07-10 to 2026-07-11 KST
- desktop viewport: 1440 x 1000
- mobile viewport: 390 x 844
- fixture: 80 library folders with representative JPEGs and 50 completed jobs
- browser: headless Chromium against the real FastAPI/Jinja page with Basic Auth

Observed results:

- desktop library: six columns, no horizontal overflow
- mobile library: two columns, no horizontal overflow
- normal browser console: no errors
- library deferred thumbnails: all 50 loaded after scrolling the page
- unchanged job polling: focused button lost focus after rerender
- delayed-thumbnail observer audit: 340 observed targets, 255 detached after about 7.5 seconds
- static preview mobile navigation: five buttons in a four-column grid, two rows
- Python/extension syntax checks: passed
- full pytest: 296 passed, 2 failed, one existing Starlette deprecation warning

## Handoff Checklist

When implementing the remediation:

- update this document's status or add a dated remediation record
- update `docs/feature-code-map.md` if frontend ownership or tests move
- update `docs/architecture.md` only if runtime boundaries change
- update `docs/operations.md` if polling/cache/maintenance operator behavior changes
- update `README_LLM.md` current behavior notes after verified behavior changes
- add a dated `docs/patch-notes/YYYY-MM-DD.md` entry
- run `SKILL_Dev/skill_frontend_addon.md` verification and the full pytest suite
