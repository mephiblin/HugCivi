---
name: hugcivi-docs-handoff
description: Stable workflow for keeping HugCivi README, docs, feature-code map, configuration reference, historical planning records, and developer handoff material current.
---

# HugCivi Docs Handoff Skill

Use this after any user-visible feature, API, setting, deployment, provider, storage, or test responsibility changes.

## Current Authoritative Docs

- `README.md`: user-facing overview, install, first use, examples, troubleshooting.
- `README_LLM.md`: LLM/developer entry, read order, handoff timing.
- `AGENTS.md`: short always-on Codex repo guidance.
- `docs/index.md`: current vs historical document map.
- `docs/feature-code-map.md`: feature-to-code/test map. Update this when code ownership changes.
- `docs/configuration.md`: env vars, UI settings, compose/Portainer differences.
- `docs/architecture.md`: runtime shape, DB, schedulers, API groups, invariants.
- `docs/development.md`: local setup and change workflow.
- `docs/operations.md`: NAS/Portainer operation, backup, recovery, tuning.
- `docs/patch-notes/`: date-based developer/LLM work history.
- `PATCH_NOTES.md`: changelog.

## Stable Rules

- Treat dated design/review docs as historical unless code proves they are current.
- Add a status note to dated docs if they may be mistaken for current behavior.
- Keep examples executable. If `APP_PASSWORD` is required, include it in local run snippets.
- When adding a setting, update `docs/configuration.md`.
- When adding an endpoint or feature, update `docs/feature-code-map.md` and usually `docs/architecture.md`.
- When adding a user-facing input type or flow, update README examples.
- When changing build/deploy behavior, update `skill_build.md`, `docs/operations.md`, and `PATCH_NOTES.md`.
- When changing recurring development process, update `README_LLM.md`, `AGENTS.md` only if the rule must apply every session, and `SKILL_Dev/` if the workflow is reusable.
- Write date-based work records in `docs/patch-notes/YYYY-MM-DD.md` after verification and before commit/release for substantial changes.

## Verification

Run at least:

```bash
git diff --check
rg -n "new_setting_or_endpoint_or_feature" README.md docs PATCH_NOTES.md SKILL_Dev
```

For broad doc updates, verify local Markdown links with a file-relative checker or manually inspect changed links.
