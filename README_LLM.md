# HugCivi LLM README

This is the machine/developer handoff entry point. Keep the public GitHub-facing overview in `README.md`; keep detailed development routing here.

## First Read Order

1. `AGENTS.md`: short repo rules that Codex loads automatically.
2. `docs/index.md`: decide which documents are current and which are historical.
3. `docs/feature-code-map.md`: map features to code files, state, APIs, and tests.
4. `docs/configuration.md`: verify environment variables, UI settings, and compose/Portainer differences.
5. Relevant `SKILL_Dev/skill_*.md`: use only the focused workflow needed for the task.
6. Source code and tests listed by the feature map.

Do not read every dated document at startup. Dated plans and reviews are context, not current behavior, unless current code and current reference docs confirm them.

## Document Roles

| File or Folder | Role | Read When |
| --- | --- | --- |
| `README.md` | Human/GitHub entry point | User-facing overview, install, examples, troubleshooting. |
| `README_LLM.md` | LLM/developer routing | Start of broad coding, docs, handoff, or repo-orientation work. |
| `AGENTS.md` | Persistent Codex project guidance | Automatically loaded by Codex; update only for rules that should apply every session. |
| `docs/index.md` | Current vs historical docs map | Before relying on any dated design/review document. |
| `docs/feature-code-map.md` | Feature-to-code/test map | Before editing a feature or API. |
| `docs/configuration.md` | Settings and environment reference | When adding/changing env vars, UI settings, compose, Portainer, or startup behavior. |
| `docs/architecture.md` | System shape and invariants | Before cross-cutting backend, scheduler, DB, or API changes. |
| `docs/development.md` | Local setup and coding workflow | When preparing a dev environment or choosing verification commands. |
| `docs/operations.md` | NAS/Portainer operations | When deployment, backup, recovery, storage, or tuning changes. |
| `docs/patch-notes/` | Date-based work history | After substantial changes, before commit/release, and during handoff cleanup. |
| `SKILL_Dev/` | Repo-local workflows | When a task matches build, safety, DB/jobs, providers, UI/addon, or docs handoff. |
| `.agents/skills/hugcivi-dev-core/` | Codex auto-discovery pointer | Lets Codex discover the repo-local skill set while keeping browsable content in `SKILL_Dev/`. |

## SKILL_Dev Routing

`SKILL_Dev/SKILL.md` is the index. Use the smallest focused skill:

- `skill_build.md`: verify, commit, image build, GHCR push, git push.
- `skill_project_core.md`: broad architecture and invariants.
- `skill_filesystem_safety.md`: `/data` paths, rename/move/delete, ZIP, symlink safety.
- `skill_database_jobs.md`: SQLite, migrations, settings, external/internal job lifecycle.
- `skill_download_providers.md`: parser/provider/downloader changes.
- `skill_frontend_addon.md`: browser UI, PWA, Chrome extension addon.
- `skill_docs_handoff.md`: docs, feature map, config reference, patch notes.

Codex's documented best split is: `AGENTS.md` for small always-on repo rules, `.agents/skills` for discoverable reusable workflows, and docs for durable human/LLM reference. This repository follows that split with a thin `.agents/skills/hugcivi-dev-core/` discovery pointer while keeping the browsable skill content in the user-requested `SKILL_Dev/` folder.

## Current Behavior Notes

Keep these recent operational behaviors in mind when touching handoff, provider, library, or viewer code:

- Civitai model/version archives now preserve `_civitai_metadata.json`, optional `_civitai_generation_metadata.json`, local `civitai_example_<imageId>.*` previews, model/version/file detail, tensor metadata when available, and `component_downloads`.
- Civitai normal model/version downloads also fetch files marked `metadata.isRequired=true` unless the input requested a specific file or raw download URL. Refresh jobs reuse existing primary/component files and refresh sidecars/previews in the same archive folder.
- Civitai `Workflows` model types whose primary file is an `Archive`/`Other` ZIP are classified as ComfyUI workflow archives under `/data/civitai/workflows/...`; the ZIP is kept, and a verified JSON/PNG workflow entry is copied out as `workflow.json` plus `_workflow_metadata.json` when available.
- The Civitai media viewer can check both model-version resources and local component files through `/api/civitai/resource-health`; model archives send their archive path plus `component_downloads`.
- Civitai image-page downloads may archive an image or video primary asset. When the public images API returns no item but the rendered page still exposes `__NEXT_DATA__`/JSON-LD metadata, the downloader can derive original media URLs, including rendered-page `.webm` videos such as `civitai.red/images/97376108`.
- The library view can explicitly live-scan a selected folder with `/api/library?mode=live&path=<relative-data-path>`. Normal `index` mode does not automatically live-scan missing DB rows; it returns fast `needs_refresh`/`refreshing` status and relies on background indexing, quick scoped `/api/library/sync`, explicit live mode, or `/api/library/reindex` for sidecar-backed restoration after DB/job history loss.
- Completed HugCivi-owned download jobs, subscription downloads, and ComfyUI drag-and-drop imports refresh the completed `target_dir` as a single library index upsert and clear the live-page cache. This gives Plex/LoRA Manager-style immediate cards for files HugCivi just wrote without adding a filesystem watcher; external NAS-side changes still rely on quick sync, background indexing, explicit live mode, or reindex.
- The library grid requests 50-card pages with `/api/library?limit=50&page=N&mode=...&path=...`; sort values include `az`, `za`, `date_desc`, `date_asc`, and `favorite`, with legacy `date` kept as a newest-first alias. `source_group` filters support `civitai`, `gallerydl`, `ytdlp`, `hitomi`, `asmrone`, `generic`, `huggingface`, `comfyui`, `media`, and `unknown`. Legacy `/api/library` array behavior remains available when `limit` and `page` are omitted.
- Selected-folder library navigation is DB-only in normal `index` mode, including source-group/category filters and exact totals when indexed rows exist. Missing indexed rows return `index_status.needs_refresh` without waiting for a NAS live scan. The browser `갱신` action first calls `POST /api/library/sync`, which does a bounded scoped reconcile without clearing existing DB rows; `POST /api/library/reindex` remains the heavier background rebuild path and returns `queued`, `job_id`, `scope`, and additive `deduped`. Explicit `mode=live` still forces a selected-folder filesystem scan; completed selected-folder live scans report known `total_count`/`total_pages` within the internal path budget and are kept in a short in-memory page cache so page/sort changes do not rescan the same NAS folder on every click. If a live scan cannot complete within that budget, the API leaves totals unknown and the UI keeps the previous/current/next fallback. The UI clears stale cards into the loading state on page/sort changes so CJK-titled cards do not appear to linger across pages.
- Library/job card thumbnails use cached JPEGs from `/api/media/thumbnail`, stored under `/config/media-cache/thumbnails`; the library/API payload includes `thumbnail_ready`, cached thumbnail responses use long-lived `Cache-Control`, and the browser only requests cards in or near the viewport while allowing up to 10 ready-thumbnail requests or 3 cold/generating-thumbnail requests at a time.
- The library `썸네일 생성` button queues one `media_thumbnail_backfill` internal job for the selected folder. It scans card representative images only, skips existing thumbnail cache files, and defaults to 3 worker threads.
- Media cache cleanup can be inspected or run through `/api/media/cache` and `/api/media/cache/cleanup` or the settings `유지보수` pane. `MEDIA_CACHE_MAX_BYTES` quota cleanup removes least-recently-accessed files first, and cached media responses update access time when the filesystem supports it.
- Heavy internal jobs (`archive_zip`, `library_reindex`, media transcode/poster, thumbnail backfill) can be started immediately, deferred to a server-local maintenance window, or kept queued through `INTERNAL_JOB_MAINTENANCE_MODE`. Queued decorated jobs may include `maintenance_deferred`.
- Scoped library reindex jobs record selected-folder progress in `library_folder_state`; selected-folder index responses may include additive `index_status.folder_state`.
- `LIBRARY_WATCHER_ENABLED` and `MEDIA_VIDEO_PREVIEW_MODE` are disabled-by-default policy/status settings only in the current build. `/api/library/watcher` and `/api/media/video-preview` report policy state, but no filesystem watcher or video preview/trickplay worker is started yet.
- `/api/folders` stays a bounded initial tree for sidebar compatibility and now uses `initial_folder_tree()` with direct root children only (`FOLDER_TREE_INITIAL_MAX_DEPTH=1`). `/api/folders/children` loads direct child folder rows on demand with `limit`/`cursor` pagination (default 200, max 500), and `/api/folders/search` provides bounded server-side folder search for folders outside the loaded tree. The search UI remains scoped to the selected folder when one is active and reveals matches by lazy-loading ancestors/pages. Hitomi gallery archive folders are treated as leaf nodes, so Tree navigation and folder search do not scan page files inside each downloaded gallery. Optional folder indexing remains the follow-up direction in `docs/folder-tree-scaling-design-2026-07-06.md`.
- The default job table is paginated at 50 rows per page and can be filtered by source. Use `/api/jobs?limit=50&page=N&source=civitai` for the wrapped page payload with `source_counts`; legacy `/api/jobs` array behavior remains available when `page` is omitted.
- Copy-only transfer is registered-target-only outbound work. HugCivi supports `local_mount` targets backed by host-mounted folders under `/data_remote`, `rclone` targets backed by `/config/rclone/rclone.conf`, and `receiver` targets backed by the sibling `/home/inri/문서/HugCivi-Receiver` app. Local mount targets browse target-relative folders through `/api/transfer/targets/{target_id}/local-mount/tree`; Receiver targets browse mounted PC `/receive` folders through `/api/transfer/targets/{target_id}/receiver/tree`. The browser never receives Receiver tokens or raw host paths. The normal context-menu transfer payload sends only `target_id`, `/data` `source_path`, and optional `destination_subpath`.
- Civitai model archive transfers preserve model/version context for ComfyUI-style targets. When a `_civitai_metadata.json` model archive under `stable-diffusion/<route>/<base>/<model>/version_<id>` is transferred, HugCivi appends the model folder to archive-folder transfers and appends `model/version_<id>` to single-file resource transfers, so ComfyUI/LoRA Manager relative names do not collapse to only the version folder.
- `/data` root clone is a separate settings-pane flow for `local_mount` only. It uses `/api/transfer/data-root/preflight` and `/api/transfer/data-root/jobs`, sends no browser-controlled `source_path`, copies `/data` contents into the selected target/subfolder without wrapping them in a `data/` folder, and skips existing files by default.
- `/data_remote` is an optional connected transfer root, not a library root. It must stay separate from `/data`; local mount targets reject `/data_remote` root, path traversal, backslash escapes, symlink escapes, and offline/unwritable destinations. Local mount copies use temp files plus rename and skip existing files by default.
- Queue settings are the user-facing download controls. `QUEUE_PER_PROVIDER_LIMIT` controls Hugging Face job concurrency while HF snapshot internal workers stay fixed at 1 so the provider limit does not multiply; `DOWNLOAD_STALL_TIMEOUT_SECONDS` also drives Hugging Face Hub response waits. Do not add separate HF worker/timeout UI unless the product direction changes.
- Bare `pawchive.pw` and `pawchive.st` HTTP(S) URLs, including their `www` hosts, route directly to the gallery-dl provider. Keep this exact-host match ahead of generic HTTP routing; other Pawchive subdomains and lookalike domains stay generic unless explicitly prefixed with `gallery-dl`/`gdl`.
- ASMR.one downloads keep Unicode/Japanese local paths, record per-file status in `_asmrone_manifest.json`, and treat missing optional image/text/audio leaves as nonfatal when at least one file was saved. Work cover metadata is saved as `cover.jpg` when available, and Cloudflare HTML/error-placeholder image responses are discarded and recorded as failed entries.
- Media archives include image, video, audio, and document items. `.txt`, `.md`, and `.markdown` files are read through `/api/media/text` with a bounded preview size and rendered as escaped text; Markdown is not converted to HTML.

## Patch Notes Policy

Detailed work history belongs in `docs/patch-notes/YYYY-MM-DD.md`.

Use patch notes when:

- A feature, API, provider, queue, DB schema, filesystem behavior, UI flow, addon behavior, deployment rule, or safety invariant changes.
- A bug fix changes expected behavior or operational risk.
- A docs-only change materially affects how future humans/LLMs start work.
- A release/image push happens.

Do not use patch notes for tiny typo-only edits unless the typo changed an instruction or command.

Patch note timing:

1. During implementation, keep notes rough if useful.
2. After verification and before commit, write the final dated entry.
3. Before release/build push, confirm the date entry mentions verification and deploy impact.
4. During handoff cleanup, update `docs/index.md`, `docs/feature-code-map.md`, `docs/configuration.md`, and the relevant `SKILL_Dev` file if the change affects future development.

See `docs/patch-notes/README.md` for the exact template.

## Handoff Update Checklist

After a non-trivial change, update:

- `README.md` only for user-visible behavior, install/deploy, examples, or troubleshooting.
- `README_LLM.md` when reading order, folder roles, handoff policy, or high-value current behavior notes change.
- `AGENTS.md` only for recurring rules that should apply every Codex session.
- `docs/feature-code-map.md` when code ownership, endpoint responsibility, state, or test coverage changes.
- `docs/configuration.md` when any setting/env var/default changes.
- `docs/index.md` when adding, moving, or reclassifying docs.
- `SKILL_Dev/` when a repeatable workflow changes.
- `docs/patch-notes/YYYY-MM-DD.md` for the actual work record.

## Verification Baseline

Use targeted checks from the relevant skill. The broad baseline remains:

```bash
git diff --check
python3 -m py_compile app/main.py app/db.py app/defaults.py app/downloader.py app/internal_jobs.py app/parsers.py app/workflows.py app/metadata.py app/utils.py app/subscriptions.py
node --check chrome-extension/shared.js
node --check chrome-extension/background.js
node --check chrome-extension/popup.js
node -e "JSON.parse(require('fs').readFileSync('chrome-extension/manifest.json','utf8'))"
python3 -m pytest -q -p no:cacheprovider
```

If a check is skipped, record why in the final response and, for substantial work, in the patch note.
