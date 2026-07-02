---
name: hugcivi-project-core
description: Stable HugCivi architecture and development invariants. Use before broad backend, frontend, deployment, or cross-cutting changes.
---

# HugCivi Project Core

## Stable Shape

HugCivi is a single-container personal NAS archive app:

- FastAPI serves UI, API, static assets, media, and artifacts.
- SQLite at `/config/jobs.sqlite3` stores jobs, settings, favorites, notes, library index, artifacts, and maintenance state.
- `/data` stores durable archive content.
- `app/downloader.py` runs external download jobs.
- `app/internal_jobs.py` runs server-local ZIP/transcode/poster jobs.
- `app/main.py` owns routes, UI integration, filesystem APIs, media helpers, storage readout, and library indexer.

## Stable Invariants

- Do not store large archive binaries in SQLite.
- Do not add Redis, Celery, Elasticsearch, or a second service unless the user explicitly accepts that architecture change.
- Keep user path operations inside `/data` and mediated by safety helpers.
- Keep secrets out of API/template responses. Show configured/source metadata, not secret values.
- Keep API shape changes additive unless frontend and tests are updated in the same change.
- Keep old dated docs historical; current references are `docs/index.md`, `docs/feature-code-map.md`, `docs/architecture.md`, `docs/configuration.md`, `docs/development.md`, and `docs/operations.md`.

## Before Editing

Read:

```bash
sed -n '1,260p' docs/feature-code-map.md
sed -n '1,280p' docs/architecture.md
```

Find relevant code with `rg` before changing abstractions:

```bash
rg -n "feature_or_endpoint_name" app tests docs
```

## Verification Bias

- For backend changes, run targeted pytest plus full pytest when practical.
- For UI/addon changes, run extension syntax checks and inspect rendered behavior when layout risk exists.
- For deployment changes, use [skill_build.md](skill_build.md).
