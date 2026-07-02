# Hitomi Listing Queue Development Plan

Date: 2026-07-01

Status: historical implementation plan. Current code supports Hitomi listing discovery with `auto` and `confirm` queue modes; use [Feature and Code Map](feature-code-map.md) before changing the implementation.

## Goal

Allow a user to paste a Hitomi artist/tag/search/index listing URL into the existing HugCivi frontend and have the backend enqueue every matching gallery as individual Hitomi gallery download jobs.

The frontend remains a URL input surface. Discovery and queue expansion happen on the backend.

## Confirmed Behavior

- Current HugCivi supports single Hitomi gallery URLs and `hitomi <gallery_id>`.
- Current HugCivi rejects plain Hitomi listing URLs because `parse_hitomi_url()` expects a gallery ID.
- Explicit `gallery-dl <listing-url>` already works as a single `gallerydl` archive job, but it does not create one HugCivi queue row per gallery.
- Current `gallery-dl 1.32.5` supports Hitomi galleries, site index, search results, and tag searches.
- Hitomi listing pagination is data-backed by `.nozomi` gallery ID lists, not by scraping rendered HTML cards.

References:

- https://gdl-org.github.io/docs/supportedsites.html
- https://gdl-org.github.io/docs/options.html
- https://gdl-org.github.io/docs/configuration.html#extractor-hitomi-format
- https://pypi.org/project/gallery-dl/

## Supported Inputs

Initial implementation should accept:

```text
https://hitomi.la/artist/<artist>-all.html
https://hitomi.la/artist/<artist>-<language>.html
https://hitomi.la/artist/<artist>-<language>-1.html
https://hitomi.la/artist/<artist>-<language>.html?page=2
https://hitomi.la/tag/<tag>-<language>.html
https://hitomi.la/group/<group>-<language>.html
https://hitomi.la/series/<series>-<language>.html
https://hitomi.la/type/<type>-<language>.html
https://hitomi.la/character/<character>-<language>.html
https://hitomi.la/index-<language>.html
https://hitomi.la/search.html?<query>
```

Sorted listing URLs can be added later if needed:

```text
https://hitomi.la/artist/date/published/<artist>-<language>.html
https://hitomi.la/artist/popular/<today|week|month|year>/<artist>-<language>.html
```

## Design

1. Add Hitomi listing fields to `ParsedDownload`:
   - `hitomi_listing_url`
   - `hitomi_listing_kind`

2. Extend `parse_hitomi_url()`:
   - Gallery URLs still parse as existing single-gallery jobs.
   - Listing URLs parse as `source="hitomi"` with `hitomi_listing_url`.
   - Query strings are preserved, because search URLs depend on them and `?page=N` should remain part of the original source URL.

3. Add a Hitomi listing branch at the start of `download_hitomi()`:
   - If `hitomi_listing_url` is present and `hitomi_gallery_id` is absent, enumerate gallery URLs.
   - Create one child `ParsedDownload(source="hitomi", hitomi_gallery_id=..., hitomi_gallery_url=...)` for each discovered gallery.
   - Call `db.create_job(child)` and `enqueue_job(child_job_id)`.
   - Update the parent job with a summary and mark it done through the normal `run_job()` flow.

4. Use `gallery-dl -g` for listing discovery:
   - It is already installed and kept current by the project.
   - It handles Hitomi `.nozomi` range pagination and search/tag semantics.
   - It avoids duplicating brittle endpoint logic in HugCivi.
   - Parse only `https://hitomi.la/galleries/<id>.html` style output lines.

5. Add duplicate suppression:
   - If an existing Hitomi job has the same gallery ID and is `queued`, `running`, `paused`, `pausing`, `canceling`, `deleting`, or `done`, skip creating another child job.
   - Failed or canceled gallery jobs are eligible to be queued again by a later listing expansion.
   - Also skip duplicate IDs inside the same listing expansion.

6. Metadata:
   - Write `_hitomi_listing_metadata.json` under `/data/hitomi/listings/<slug>`.
   - Include source URL, discovered count, queued count, skipped count, and child job IDs.

## Tests

Add or update tests for:

- Parser accepts artist language listing URLs.
- Parser accepts search listing URLs.
- Parser still accepts single gallery URLs as before.
- Downloader listing branch creates child Hitomi gallery jobs from mocked `gallery-dl -g` output.
- Downloader listing branch skips existing queued/done gallery jobs.

## Operational Notes

- This change does not alter downloader concurrency defaults.
- The user's earlier "worker 3" instruction referred to Codex subagents, not HugCivi download worker count.
- Large artist listings can enqueue many jobs. The queue controls and pause/cancel/delete controls remain the throttle mechanism.
- Network failures during listing discovery should fail only the parent listing job before child jobs are created.
