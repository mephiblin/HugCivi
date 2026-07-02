# AGENTS.md

## HugCivi Repository Guidance

- Start broad tasks from `README_LLM.md`, then follow its routing to `docs/index.md`, `docs/feature-code-map.md`, and `SKILL_Dev/`.
- Treat `README.md` as the human/GitHub entry point. Do not turn it into an LLM operations log.
- Treat current code and current reference docs as authoritative over dated planning/review documents.
- Keep large archive content in `/data`; keep app state in `/config/jobs.sqlite3`.
- Keep external downloads in `app/downloader.py` with `job_kind='download'`; keep server-local ZIP/transcode/poster work in `app/internal_jobs.py`.
- Resolve user paths through existing `/data` safety helpers before filesystem mutation or archive work.
- Update docs when behavior changes: feature ownership in `docs/feature-code-map.md`, settings in `docs/configuration.md`, architecture/API groups in `docs/architecture.md`, operations/deploy behavior in `docs/operations.md`, and handoff notes in `docs/patch-notes/`.
- Before commit/build, use `SKILL_Dev/skill_build.md`.
