# HugCivi Documentation Index

Last updated: 2026-07-02

Use this page to decide which document is authoritative for current development.

## Current Reference

| Document | Status | Purpose |
| --- | --- | --- |
| [README](../README.md) | current user entry | Product overview, install paths, first use, download input examples, troubleshooting links. |
| [Architecture](architecture.md) | current developer reference | Runtime shape, data boundaries, schedulers, DB, API groups, invariants. |
| [Feature and Code Map](feature-code-map.md) | current developer reference | Feature-by-feature file/function/test map. Start here for code changes. |
| [Configuration](configuration.md) | current operator/developer reference | Environment variables, UI-saved settings, compose differences. |
| [Development](development.md) | current developer reference | Local setup, change patterns, verification commands. |
| [Operations](operations.md) | current operator reference | NAS/Portainer behavior, backup, recovery, tuning, troubleshooting. |
| [Project Philosophy](philosophy.md) | current design reference | Project values and boundaries. |
| [gallery-dl Authentication Notes](gallery-dl-auth.md) | snapshot reference | gallery-dl supported-site auth snapshot from 2026-06-30. |

## Historical Or Planning Records

The dated documents below are useful context, but code and current reference docs are authoritative. Treat them as historical unless a current doc links to a specific section.

| Document | Status |
| --- | --- |
| [Transfer Design 2026-07-02](transfer-design-2026-07-02.md) | future design, not implemented unless code later proves otherwise. |
| [Civitai Image Page Implementation Plan 2026-07-01](civitai-image-page-implementation-plan-2026-07-01.md) | implementation planning record. Current code/tests should be checked before using details. |
| [Civitai Image Merge Technical Report 2026-07-01](civitai-image-merge-technical-report-2026-07-01.md) | technical report/history. |
| [Hitomi Listing Queue Development Plan 2026-07-01](hitomi-listing-queue-development-plan-2026-07-01.md) | historical plan. Current code now supports listing URLs. |
| [Operations Risk Code Review 2026-07-01](operations-risk-code-review-2026-07-01.md) | review record. |
| [Operations Risk Remediation Plan 2026-07-01](operations-risk-remediation-plan-2026-07-01.md) | remediation record. |
| [Remaining Structural Work Design 2026-07-01](remaining-structural-work-design-2026-07-01.md) | historical design. Some former current-state notes are superseded by async internal jobs. |
| [Code Review Findings 2026-06-30](code-review-findings-2026-06-30.md) | historical findings, some already addressed. |

## Changelog

[PATCH_NOTES](../PATCH_NOTES.md) is the canonical changelog. README may summarize major features, but detailed release history should live there.
