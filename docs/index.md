# HugCivi Documentation Index

Last updated: 2026-07-08

Use this page to decide which document is authoritative for current development.

## Current Reference

| Document | Status | Purpose |
| --- | --- | --- |
| [README](../README.md) | current user entry | Product overview, install paths, first use, download input examples, troubleshooting links. |
| [LLM README](../README_LLM.md) | current LLM/developer entry | Reading order, docs/skills routing, handoff policy, patch-note timing. |
| [AGENTS](../AGENTS.md) | current Codex guidance | Short always-on repo instructions loaded by Codex. |
| [Architecture](architecture.md) | current developer reference | Runtime shape, data boundaries, schedulers, DB, API groups, invariants. |
| [Feature and Code Map](feature-code-map.md) | current developer reference | Feature-by-feature file/function/test map. Start here for code changes. |
| [Configuration](configuration.md) | current operator/developer reference | Environment variables, UI-saved settings, compose differences. |
| [ByeDPI SOCKS5 Proxy Guide](byedpi-socks-proxy.md) | current operator network guide | Recreate the discovered `tazihad/byedpi` SOCKS5 proxy container and apply it to HugCivi's `YT_DLP_PROXY`. |
| [CasaOS Install Guide](install-casaos.md) | current operator install guide | CasaOS Custom App/Compose deployment, durable folders, proxy notes. |
| [Ubuntu Install Guide](install-ubuntu.md) | current operator install guide | Docker Engine setup, Compose deployment, durable folders, proxy notes. |
| [Development](development.md) | current developer reference | Local setup, change patterns, verification commands. |
| [Operations](operations.md) | current operator reference | NAS/Portainer behavior, backup, recovery, tuning, troubleshooting. |
| [Developer Skill Set](../SKILL_Dev/SKILL.md) | current repo-local skill reference | Stable build, safety, DB/job, provider, frontend/addon, and docs handoff skills. |
| [Codex Skill Pointer](../.agents/skills/hugcivi-dev-core/SKILL.md) | current Codex discovery entry | Thin auto-discovery pointer into `README_LLM.md` and `SKILL_Dev/`. |
| [Patch Notes Guide](patch-notes/README.md) | current handoff record policy | Date-based work history format and timing. |
| [Project Philosophy](philosophy.md) | current design reference | Project values and boundaries. |
| [Civitai Workflow Archive URL Check 2026-07-09](civitai-workflow-archive-url-check-2026-07-09.md) | snapshot reference | Documents three Civitai `Workflows` archive URLs, their current API shape, HugCivi classification, expected archive paths, and token requirement. |
| [gallery-dl Authentication Notes](gallery-dl-auth.md) | snapshot reference | gallery-dl supported-site auth snapshot from 2026-06-30. |

## Historical Or Planning Records

The dated documents below are useful context, but code and current reference docs are authoritative. Treat them as historical unless a current doc links to a specific section.

| Document | Status |
| --- | --- |
| [ComfyUI Transfer Folder Check Design 2026-07-07](comfyui-transfer-folder-check-design-2026-07-07.md) | partially implemented planning record. Current code checks `local_mount` ComfyUI `models` folders from the settings pane and returns HugCivi `/data` source-prefix mapping hints; automatic transfer-modal destination filling remains future work. Existing `/data` routes remain unchanged. |
| [Library Category Index Refresh Plan 2026-07-08](library-category-index-refresh-plan-2026-07-08.md) | partially implemented planning record. Current code has SQLite source/category/search columns, DB-only selected-folder index pages with fast `needs_refresh` status, `source_group` UI/API filters, and async scoped `library_reindex` internal jobs from `/api/library/reindex`; deeper provider-specific category UI remains future work. |
| [Folder Tree Scaling Design 2026-07-06](folder-tree-scaling-design-2026-07-06.md) | partially implemented reference. Current code has root-direct `/api/folders`, lazy `/api/folders/children`, and bounded `/api/folders/search`; optional folder indexing remains future work. |
| [`/data_remote` Connected Transfer Design 2026-07-06](data-remote-transfer-design-2026-07-06.md) | implemented reference for destination-only `local_mount` transfer targets. Current code supports `/data_remote` tree browsing, preflight, temp-file copy, skip-existing behavior, and settings-pane `/data` root clone; read-only import/friend-library ideas remain future work. |
| [Library Card Performance Plan 2026-07-05](library-card-performance-plan-2026-07-05.md) | implemented planning record. Current behavior is documented in Architecture, Feature and Code Map, Operations, and the 2026-07-05 patch notes. |
| [Transfer Copy Production Plan 2026-07-06](transfer-copy-production-plan-2026-07-06.md) | implemented planning record for the copy-only NAS-to-PC/remote transfer baseline. Current behavior also includes HugCivi Receiver targets and Receiver destination folder picking, documented in Architecture, Feature and Code Map, Configuration, and Operations. |
| [Transfer Design 2026-07-02](transfer-design-2026-07-02.md) | future design, not implemented unless code later proves otherwise. |
| [Storage Folder Search Design 2026-07-02](storage-folder-search-design-2026-07-02.md) | implemented reference for turning the sidebar `새 폴더` area into a storage folder search box, moving folder creation to the tree context menu, and replacing text-based move prompts with a tree picker. |
| [YouTube Subscription Design 2026-07-02](youtube-subscriptions-design-2026-07-02.md) | implemented MVP reference. Current code has tables, helpers, CRUD APIs, manual/scheduled discovery, sidebar UI, independent subscription downloads, item queue/skip/retry controls, storage readouts, and the main `구독 작업 목록`. |
| [YouTube Subscription Main Panel Design 2026-07-02](youtube-subscription-main-panel-design-2026-07-02.md) | implemented reference for switching the main work-list area to subscription-specific work when the sidebar `구독` tab is active. |
| [Civitai Image Page Implementation Plan 2026-07-01](civitai-image-page-implementation-plan-2026-07-01.md) | implementation planning record. Current code/tests should be checked before using details. |
| [Civitai Image Merge Technical Report 2026-07-01](civitai-image-merge-technical-report-2026-07-01.md) | technical report/history. |
| [Hitomi Listing Queue Development Plan 2026-07-01](hitomi-listing-queue-development-plan-2026-07-01.md) | historical plan. Current code now supports listing URLs. |
| [Operations Risk Code Review 2026-07-01](operations-risk-code-review-2026-07-01.md) | review record. |
| [Operations Risk Remediation Plan 2026-07-01](operations-risk-remediation-plan-2026-07-01.md) | remediation record. |
| [Remaining Structural Work Design 2026-07-01](remaining-structural-work-design-2026-07-01.md) | historical design. Some former current-state notes are superseded by async internal jobs. |
| [Code Review Findings 2026-06-30](code-review-findings-2026-06-30.md) | historical findings, some already addressed. |

## Changelog And Work History

[PATCH_NOTES](../PATCH_NOTES.md) is the canonical changelog. README may summarize major features, but detailed release history should live there.

Date-based developer/LLM work history lives in [patch-notes](patch-notes/). Use [patch-notes/README.md](patch-notes/README.md) for the required entry format.
