---
name: hugcivi-filesystem-safety
description: Stable rules for HugCivi /data path handling, archive ZIPs, rename/move/delete, symlink safety, partial cleanup, and filesystem API changes.
---

# HugCivi Filesystem Safety Skill

Use this before editing file operations, folder downloads, archive ZIPs, media paths, workflow imports, favorites, notes, or library path updates.

## Core Rules

- `/data` is the only durable archive root.
- Never trust a client path until it is resolved through existing helpers.
- Do not allow rename, move, delete, or ZIP download of `/data` root itself.
- Do not follow symlink escapes outside `/data` for archive or mutation operations.
- Keep archive ZIP entry names relative and safe.
- Keep active job protection for queued/running/paused/pausing/canceling/deleting work.
- When moving or deleting paths, update related DB prefixes for jobs, favorites, notes, and library index.

## Code To Read

```bash
rg -n "def (existing_data_path|data_path_from_request_path|relative_data_path|ensure_mutable_path|ensure_downloadable_path|ensure_no_active_jobs|archive_zip|api_rename_path|api_move_path|api_delete_path|api_download_path|api_create_download_job)" app/main.py app/db.py
```

Also inspect:

- `app/utils.py::safe_join`
- `tests/test_review_fixes.py` filesystem/archive tests
- `docs/feature-code-map.md` Filesystem and Browser download rows

## Required Test Focus

Run at least:

```bash
python3 -m pytest -q -p no:cacheprovider tests/test_review_fixes.py
```

Add or update tests when changing:

- symlink behavior
- relative path normalization
- active job protection
- ZIP preflight limits
- path prefix updates for DB rows
- partial file cleanup
