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
- `README_LLM.md` when reading order, folder roles, or handoff policy changes.
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
python3 -m py_compile app/main.py app/db.py app/downloader.py app/internal_jobs.py app/parsers.py app/workflows.py app/metadata.py app/utils.py app/subscriptions.py
node --check chrome-extension/shared.js
node --check chrome-extension/background.js
node --check chrome-extension/popup.js
node -e "JSON.parse(require('fs').readFileSync('chrome-extension/manifest.json','utf8'))"
python3 -m pytest -q -p no:cacheprovider
```

If a check is skipped, record why in the final response and, for substantial work, in the patch note.
