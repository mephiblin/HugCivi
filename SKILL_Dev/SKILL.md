---
name: hugcivi-dev-core
description: Repo-local development skills for HugCivi. Use when an agent or developer needs stable project rules for architecture, safety, database/jobs, download providers, frontend/addon work, documentation, verification, build, or deployment.
---

# HugCivi Dev Skill Index

Use this directory before changing HugCivi. These files capture rules expected to remain stable even as features evolve.

Codex auto-discovery uses `.agents/skills/hugcivi-dev-core/SKILL.md` as a small pointer into this directory.

## Choose The Focused Skill

| Task | Read |
| --- | --- |
| Build, verify, image push, Git push, Portainer release | [skill_build.md](skill_build.md) |
| First orientation or broad architecture changes | [skill_project_core.md](skill_project_core.md) |
| Rename, move, delete, archive, path handling, symlink safety | [skill_filesystem_safety.md](skill_filesystem_safety.md) |
| SQLite schema, job lifecycle, internal/external queues, settings | [skill_database_jobs.md](skill_database_jobs.md) |
| Parser/provider/downloader changes for HF, Civitai, Hitomi, gallery-dl, yt-dlp, generic, ComfyUI | [skill_download_providers.md](skill_download_providers.md) |
| Browser UI, PWA, Chrome extension, frontend API compatibility | [skill_frontend_addon.md](skill_frontend_addon.md) |
| Updating README/LLM README/docs/feature-code map/config docs/patch notes | [skill_docs_handoff.md](skill_docs_handoff.md) |

## Always Check

- Start with [README_LLM.md](../README_LLM.md), [docs/index.md](../docs/index.md), and [docs/feature-code-map.md](../docs/feature-code-map.md).
- Treat current code and tests as authoritative over dated planning documents.
- Keep `AGENTS.md` short; put repeatable procedures in `SKILL_Dev/` and durable handoff detail in `docs/`.
- Keep `/data` archive content on the filesystem and app state in `/config/jobs.sqlite3`.
- Keep external download jobs and internal server-local jobs separated by `job_kind`.
- Prefer additive DB/API changes and focused tests.
