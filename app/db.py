from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .defaults import (
    DOWNLOAD_STALL_TIMEOUT_DEFAULT_SECONDS,
    JOB_LOG_MAX_CHARS_DEFAULT,
    QUEUE_PROVIDER_COOLDOWN_MAX_DEFAULT_SECONDS,
    QUEUE_PROVIDER_COOLDOWN_MIN_DEFAULT_SECONDS,
    YT_DLP_DEFAULT_FORMAT,
)
from .models import ParsedDownload
from .utils import redact_sensitive_text, safe_join

DB_PATH = Path(os.getenv("DB_PATH", "/config/jobs.sqlite3"))
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))
_DB_LOCK = threading.RLock()
JOB_KIND_DOWNLOAD = "download"
ACTIVE_JOB_STATUSES = ("queued", "running", "paused", "pausing", "canceling", "deleting")
ROUTE_SETTING_BY_TYPE = {
    "llm": "ROUTE_LLM_ROOT",
    "lora": "ROUTE_LORA_ROOT",
    "checkpoint": "ROUTE_CHECKPOINT_ROOT",
    "diffusion_model": "ROUTE_DIFFUSION_MODEL_ROOT",
    "embedding": "ROUTE_EMBEDDING_ROOT",
    "vae": "ROUTE_VAE_ROOT",
    "controlnet": "ROUTE_CONTROLNET_ROOT",
    "upscaler": "ROUTE_UPSCALER_ROOT",
}
HF_POSSIBLE_ROUTE_TYPES = ("llm", "checkpoint", "embedding")
CIVITAI_POSSIBLE_ROUTE_TYPES = (
    "lora",
    "checkpoint",
    "diffusion_model",
    "embedding",
    "vae",
    "controlnet",
    "upscaler",
)

ROUTE_DEFAULTS = {
    "LIBRARY_ACTIVE": "ComfyUI",
    "ROUTE_LLM_ROOT": "huggingface/llm",
    "ROUTE_LORA_ROOT": "stable-diffusion/loras",
    "ROUTE_CHECKPOINT_ROOT": "stable-diffusion/checkpoints",
    "ROUTE_DIFFUSION_MODEL_ROOT": "stable-diffusion/diffusion_models",
    "ROUTE_EMBEDDING_ROOT": "stable-diffusion/embeddings",
    "ROUTE_VAE_ROOT": "stable-diffusion/vae",
    "ROUTE_CONTROLNET_ROOT": "stable-diffusion/controlnet",
    "ROUTE_UPSCALER_ROOT": "stable-diffusion/upscalers",
}
ROUTE_SETTING_KEYS = tuple(ROUTE_DEFAULTS.keys())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _DB_LOCK, connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                input_text TEXT NOT NULL,
                parsed_json TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                target_dir TEXT,
                filename TEXT,
                progress_bytes INTEGER DEFAULT 0,
                total_bytes INTEGER,
                error TEXT,
                log TEXT DEFAULT ''
            )
            """
        )
        ensure_job_columns(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS favorites (
                path TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS item_notes (
                path TEXT PRIMARY KEY,
                note TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                url TEXT,
                expires_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_content_refs (
                job_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (job_id, path, role)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS library_items (
                path TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                target_dir TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                size_bytes INTEGER DEFAULT 0,
                mtime_ns INTEGER DEFAULT 0,
                ctime_ns INTEGER DEFAULT 0,
                stale INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL,
                scanned_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS library_scan_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                detail_json TEXT
            )
            """
        )
        conn.commit()


def ensure_job_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    columns = {
        "model_title": "TEXT",
        "model_category": "TEXT",
        "model_type": "TEXT",
        "base_model": "TEXT",
        "file_format": "TEXT",
        "precision": "TEXT",
        "thumbnail_url": "TEXT",
        "metadata_json": "TEXT",
        "job_kind": "TEXT DEFAULT 'download'",
        "artifact_path": "TEXT",
        "artifact_url": "TEXT",
        "artifact_expires_at": "TEXT",
    }
    for name, sql_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {sql_type}")


def create_job(parsed: ParsedDownload) -> int:
    now = utc_now()
    parsed_payload = parsed.to_dict()
    parsed_payload["raw_input"] = redact_sensitive_text(parsed.raw_input)
    with _DB_LOCK, connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs
            (created_at, updated_at, input_text, parsed_json, source, status, log, job_kind)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (
                now,
                now,
                redact_sensitive_text(parsed.raw_input),
                json.dumps(parsed_payload, ensure_ascii=False),
                parsed.source,
                f"[{now}] queued\n",
                JOB_KIND_DOWNLOAD,
            ),
        )
        conn.commit()
        if cur.lastrowid is None:
            raise RuntimeError("Failed to create job")
        return int(cur.lastrowid)


def create_internal_job(
    job_kind: str,
    *,
    input_text: str,
    payload: dict[str, Any] | None = None,
    target_dir: str | Path | None = None,
    filename: str | None = None,
    total_bytes: int | None = None,
    metadata: dict[str, Any] | None = None,
    artifact_path: str | Path | None = None,
    artifact_url: str | None = None,
    artifact_expires_at: str | None = None,
) -> int:
    kind = normalized_job_kind(job_kind)
    if kind == JOB_KIND_DOWNLOAD:
        raise ValueError("create_internal_job cannot create download jobs")
    now = utc_now()
    parsed_payload = {
        "job_kind": kind,
        "raw_input": redact_sensitive_text(input_text),
        "payload": payload or {},
    }
    with _DB_LOCK, connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs
            (
                created_at, updated_at, input_text, parsed_json, source, status,
                target_dir, filename, total_bytes, metadata_json, log, job_kind,
                artifact_path, artifact_url, artifact_expires_at
            )
            VALUES (?, ?, ?, ?, 'internal', 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                now,
                redact_sensitive_text(input_text),
                json.dumps(parsed_payload, ensure_ascii=False),
                str(target_dir) if target_dir is not None else None,
                filename,
                total_bytes,
                json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
                f"[{now}] queued internal job kind={kind}\n",
                kind,
                str(artifact_path) if artifact_path is not None else None,
                artifact_url,
                artifact_expires_at,
            ),
        )
        conn.commit()
        if cur.lastrowid is None:
            raise RuntimeError("Failed to create internal job")
        return int(cur.lastrowid)


def normalized_job_kind(value: Any) -> str:
    text = str(value or "").strip()
    return text or JOB_KIND_DOWNLOAD


def is_download_job(job: dict[str, Any]) -> bool:
    return normalized_job_kind(job.get("job_kind")) == JOB_KIND_DOWNLOAD


def is_internal_job(job: dict[str, Any]) -> bool:
    return not is_download_job(job)


def list_jobs(limit: int = 100) -> list[dict[str, Any]]:
    with _DB_LOCK, connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [redact_job_row(dict(row)) for row in rows]


def list_download_jobs_to_resume(limit: int = 500) -> list[dict[str, Any]]:
    with _DB_LOCK, connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE COALESCE(job_kind, ?) = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (JOB_KIND_DOWNLOAD, JOB_KIND_DOWNLOAD, limit),
        ).fetchall()
        return [redact_job_row(dict(row)) for row in rows]


def list_job_summaries(limit: int = 100, before_id: int | None = None) -> list[dict[str, Any]]:
    safe_limit = max(1, min(500, int(limit)))
    columns = """
        id, created_at, updated_at, input_text, parsed_json, source, status,
        target_dir, filename, progress_bytes, total_bytes, error,
        model_title, model_category, model_type, base_model, file_format,
        precision, thumbnail_url, job_kind, artifact_path, artifact_url,
        artifact_expires_at
    """
    with _DB_LOCK, connect() as conn:
        if before_id is None:
            rows = conn.execute(f"SELECT {columns} FROM jobs ORDER BY id DESC LIMIT ?", (safe_limit,)).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {columns} FROM jobs WHERE id < ? ORDER BY id DESC LIMIT ?",
                (before_id, safe_limit),
            ).fetchall()
        return [redact_job_row(dict(row)) for row in rows]


def list_internal_jobs_to_resume(limit: int = 500) -> list[dict[str, Any]]:
    with _DB_LOCK, connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE COALESCE(job_kind, ?) <> ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (JOB_KIND_DOWNLOAD, JOB_KIND_DOWNLOAD, limit),
        ).fetchall()
        return [redact_job_row(dict(row)) for row in rows]


def list_inactive_jobs(limit: int = 5000) -> list[dict[str, Any]]:
    placeholders = ", ".join("?" for _ in ACTIVE_JOB_STATUSES)
    with _DB_LOCK, connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE status NOT IN ({placeholders}) ORDER BY id DESC LIMIT ?",
            (*ACTIVE_JOB_STATUSES, limit),
        ).fetchall()
        return [redact_job_row(dict(row)) for row in rows]


def get_job(job_id: int) -> dict[str, Any] | None:
    with _DB_LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return redact_job_row(dict(row)) if row else None


def clear_job_history() -> int:
    placeholders = ", ".join("?" for _ in ACTIVE_JOB_STATUSES)
    with _DB_LOCK, connect() as conn:
        cur = conn.execute(f"DELETE FROM jobs WHERE status NOT IN ({placeholders})", ACTIVE_JOB_STATUSES)
        conn.commit()
        return int(cur.rowcount or 0)


def vacuum_database() -> None:
    with _DB_LOCK:
        conn = connect()
        try:
            conn.isolation_level = None
            conn.execute("VACUUM")
        finally:
            conn.close()


def set_wal_mode(enabled: bool = True) -> str:
    with _DB_LOCK, connect() as conn:
        mode = "WAL" if enabled else "DELETE"
        row = conn.execute(f"PRAGMA journal_mode={mode}").fetchone()
        conn.commit()
        return str(row[0]) if row else ""


def checkpoint_database(mode: str = "PASSIVE") -> dict[str, Any]:
    normalized = mode.strip().upper()
    if normalized not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
        normalized = "PASSIVE"
    with _DB_LOCK, connect() as conn:
        row = conn.execute(f"PRAGMA wal_checkpoint({normalized})").fetchone()
        conn.commit()
    values = list(row) if row else []
    return {
        "mode": normalized,
        "busy": int(values[0]) if len(values) > 0 else 0,
        "log": int(values[1]) if len(values) > 1 else 0,
        "checkpointed": int(values[2]) if len(values) > 2 else 0,
    }


def optimize_database() -> None:
    with _DB_LOCK, connect() as conn:
        conn.execute("PRAGMA optimize")
        conn.commit()


def backup_database(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _DB_LOCK:
        source = connect()
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()


def create_maintenance_run(kind: str, status: str = "running", detail: dict[str, Any] | None = None) -> int:
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO maintenance_runs (kind, status, started_at, detail_json)
            VALUES (?, ?, ?, ?)
            """,
            (kind, status, now, json.dumps(detail or {}, ensure_ascii=False)),
        )
        conn.commit()
        if cur.lastrowid is None:
            raise RuntimeError("Failed to create maintenance run")
        return int(cur.lastrowid)


def add_job_artifact(
    job_id: int,
    *,
    kind: str,
    path: str | Path,
    url: str | None = None,
    expires_at: str | None = None,
) -> None:
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        conn.execute(
            """
            INSERT INTO job_artifacts (job_id, kind, path, url, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, kind, str(path), url, expires_at, now),
        )
        conn.commit()


def add_job_content_ref(job_id: int, *, path: str | Path, role: str) -> None:
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        conn.execute(
            """
            INSERT INTO job_content_refs (job_id, path, role, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id, path, role) DO NOTHING
            """,
            (job_id, str(path), role, now),
        )
        conn.commit()


def finish_maintenance_run(run_id: int, status: str, detail: dict[str, Any] | None = None) -> None:
    with _DB_LOCK, connect() as conn:
        conn.execute(
            """
            UPDATE maintenance_runs
            SET status = ?, finished_at = ?, detail_json = ?
            WHERE id = ?
            """,
            (status, utc_now(), json.dumps(detail or {}, ensure_ascii=False), run_id),
        )
        conn.commit()


def delete_job(job_id: int) -> bool:
    with _DB_LOCK, connect() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        return bool(cur.rowcount)


def favorite_paths() -> set[str]:
    with _DB_LOCK, connect() as conn:
        rows = conn.execute("SELECT path FROM favorites").fetchall()
    return {str(row["path"]) for row in rows}


def set_favorite(path: str, enabled: bool) -> None:
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        if enabled:
            conn.execute(
                """
                INSERT INTO favorites (path, updated_at)
                VALUES (?, ?)
                ON CONFLICT(path) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (path, now),
            )
        else:
            conn.execute("DELETE FROM favorites WHERE path = ?", (path,))
        conn.commit()


def get_item_note(path: str) -> str:
    normalized = path.strip("/")
    with _DB_LOCK, connect() as conn:
        row = conn.execute("SELECT note FROM item_notes WHERE path = ?", (normalized,)).fetchone()
    return str(row["note"]) if row else ""


def set_item_note(path: str, note: str) -> None:
    normalized = path.strip("/")
    cleaned = note[:20000]
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        if cleaned.strip():
            conn.execute(
                """
                INSERT INTO item_notes (path, note, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET note = excluded.note, updated_at = excluded.updated_at
                """,
                (normalized, cleaned, now),
            )
        else:
            conn.execute("DELETE FROM item_notes WHERE path = ?", (normalized,))
        conn.commit()


def get_next_queued_job() -> dict[str, Any] | None:
    with _DB_LOCK, connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'queued' AND COALESCE(job_kind, ?) = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (JOB_KIND_DOWNLOAD, JOB_KIND_DOWNLOAD),
        ).fetchone()
        return dict(row) if row else None


def update_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = utc_now()
    for key in ("error", "log", "input_text"):
        if key in fields and fields[key] is not None:
            fields[key] = redact_sensitive_text(str(fields[key]))
    keys = list(fields.keys())
    values = [fields[k] for k in keys]
    set_clause = ", ".join([f"{k} = ?" for k in keys])
    with _DB_LOCK, connect() as conn:
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values + [job_id])
        conn.commit()


def update_target_dir_prefix(old_dir: str | Path, new_dir: str | Path) -> None:
    old_text = str(old_dir)
    new_text = str(new_dir)
    old_prefix = old_text.rstrip("\\/") + os.sep
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        rows = conn.execute("SELECT id, target_dir FROM jobs WHERE target_dir IS NOT NULL").fetchall()
        for row in rows:
            target_dir = str(row["target_dir"])
            if target_dir == old_text:
                updated = new_text
            elif target_dir.startswith(old_prefix):
                updated = new_text.rstrip("\\/") + os.sep + target_dir[len(old_prefix) :]
            else:
                continue
            conn.execute("UPDATE jobs SET target_dir = ?, updated_at = ? WHERE id = ?", (updated, now, row["id"]))
        conn.commit()


def update_favorite_path_prefix(old_path: str, new_path: str) -> None:
    old_text = old_path.strip("/")
    new_text = new_path.strip("/")
    old_prefix = old_text.rstrip("/") + "/"
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        rows = conn.execute("SELECT path FROM favorites").fetchall()
        for row in rows:
            path = str(row["path"])
            if path == old_text:
                updated = new_text
            elif path.startswith(old_prefix):
                updated = new_text.rstrip("/") + "/" + path[len(old_prefix) :]
            else:
                continue
            conn.execute("DELETE FROM favorites WHERE path = ?", (path,))
            conn.execute(
                """
                INSERT INTO favorites (path, updated_at)
                VALUES (?, ?)
                ON CONFLICT(path) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (updated, now),
            )
        conn.commit()


def update_note_path_prefix(old_path: str, new_path: str) -> None:
    old_text = old_path.strip("/")
    new_text = new_path.strip("/")
    old_prefix = old_text.rstrip("/") + "/"
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        rows = conn.execute("SELECT path, note FROM item_notes").fetchall()
        for row in rows:
            path = str(row["path"])
            if path == old_text:
                updated = new_text
            elif path.startswith(old_prefix):
                updated = new_text.rstrip("/") + "/" + path[len(old_prefix) :]
            else:
                continue
            conn.execute("DELETE FROM item_notes WHERE path = ?", (path,))
            conn.execute(
                """
                INSERT INTO item_notes (path, note, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET note = excluded.note, updated_at = excluded.updated_at
                """,
                (updated, str(row["note"]), now),
            )
        conn.commit()


def clear_target_dir_prefix(target_root: str | Path) -> None:
    root_text = str(target_root)
    root_prefix = root_text.rstrip("\\/") + os.sep
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        rows = conn.execute("SELECT id, target_dir FROM jobs WHERE target_dir IS NOT NULL").fetchall()
        for row in rows:
            target_dir = str(row["target_dir"])
            if target_dir == root_text or target_dir.startswith(root_prefix):
                conn.execute("UPDATE jobs SET target_dir = NULL, updated_at = ? WHERE id = ?", (now, row["id"]))
        conn.commit()


def clear_favorite_path_prefix(target_path: str) -> None:
    root_text = target_path.strip("/")
    root_prefix = root_text.rstrip("/") + "/"
    with _DB_LOCK, connect() as conn:
        conn.execute(
            "DELETE FROM favorites WHERE path = ? OR path LIKE ? ESCAPE '\\'",
            (root_text, escape_like(root_prefix) + "%"),
        )
        conn.commit()


def clear_note_path_prefix(target_path: str) -> None:
    root_text = target_path.strip("/")
    root_prefix = root_text.rstrip("/") + "/"
    with _DB_LOCK, connect() as conn:
        conn.execute(
            "DELETE FROM item_notes WHERE path = ? OR path LIKE ? ESCAPE '\\'",
            (root_text, escape_like(root_prefix) + "%"),
        )
        conn.commit()


def upsert_library_item(
    path: str,
    *,
    kind: str,
    name: str,
    target_dir: str,
    payload: dict[str, Any],
    size_bytes: int,
    mtime_ns: int,
    ctime_ns: int,
) -> None:
    normalized = path.strip("/")
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        conn.execute(
            """
            INSERT INTO library_items (
                path, kind, name, target_dir, payload_json, size_bytes,
                mtime_ns, ctime_ns, stale, updated_at, scanned_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                kind = excluded.kind,
                name = excluded.name,
                target_dir = excluded.target_dir,
                payload_json = excluded.payload_json,
                size_bytes = excluded.size_bytes,
                mtime_ns = excluded.mtime_ns,
                ctime_ns = excluded.ctime_ns,
                stale = 0,
                updated_at = excluded.updated_at,
                scanned_at = excluded.scanned_at
            """,
            (
                normalized,
                kind,
                name,
                target_dir,
                json.dumps(payload, ensure_ascii=False),
                size_bytes,
                mtime_ns,
                ctime_ns,
                now,
                now,
            ),
        )
        conn.commit()


def list_library_index_items(limit: int = 1000) -> list[dict[str, Any]]:
    with _DB_LOCK, connect() as conn:
        rows = conn.execute(
            """
            SELECT payload_json FROM library_items
            WHERE stale = 0
            ORDER BY lower(path) ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return items


def count_library_index_items() -> int:
    with _DB_LOCK, connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM library_items WHERE stale = 0").fetchone()
    return int(row["count"] or 0) if row else 0


def mark_library_item_stale(path: str) -> None:
    normalized = path.strip("/")
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        conn.execute("UPDATE library_items SET stale = 1, updated_at = ? WHERE path = ?", (now, normalized))
        conn.commit()


def clear_library_item_prefix(target_path: str) -> None:
    root_text = target_path.strip("/")
    root_prefix = root_text.rstrip("/") + "/"
    with _DB_LOCK, connect() as conn:
        conn.execute(
            "DELETE FROM library_items WHERE path = ? OR path LIKE ? ESCAPE '\\'",
            (root_text, escape_like(root_prefix) + "%"),
        )
        conn.commit()


def update_library_item_path_prefix(old_path: str, new_path: str) -> None:
    old_text = old_path.strip("/")
    new_text = new_path.strip("/")
    old_prefix = old_text.rstrip("/") + "/"
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        rows = conn.execute("SELECT path FROM library_items").fetchall()
        for row in rows:
            path = str(row["path"])
            if path == old_text:
                updated = new_text
            elif path.startswith(old_prefix):
                updated = new_text.rstrip("/") + "/" + path[len(old_prefix) :]
            else:
                continue
            conn.execute("DELETE FROM library_items WHERE path = ?", (updated,))
            conn.execute("UPDATE library_items SET path = ?, stale = 1, updated_at = ? WHERE path = ?", (updated, now, path))
        conn.commit()


def prune_missing_library_items(limit: int = 500) -> int:
    removed = 0
    with _DB_LOCK, connect() as conn:
        rows = conn.execute("SELECT path, target_dir FROM library_items WHERE stale = 0 LIMIT ?", (limit,)).fetchall()
        for row in rows:
            target_dir = str(row["target_dir"])
            if target_dir and Path(target_dir).exists():
                continue
            conn.execute("UPDATE library_items SET stale = 1, updated_at = ? WHERE path = ?", (utc_now(), str(row["path"])))
            removed += 1
        conn.commit()
    return removed


def get_library_scan_state(key: str, default: str = "") -> str:
    with _DB_LOCK, connect() as conn:
        row = conn.execute("SELECT value FROM library_scan_state WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_library_scan_state(key: str, value: str) -> None:
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        conn.execute(
            """
            INSERT INTO library_scan_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
        conn.commit()


def clear_library_index() -> None:
    with _DB_LOCK, connect() as conn:
        conn.execute("DELETE FROM library_items")
        conn.execute("DELETE FROM library_scan_state WHERE key LIKE 'library.%'")
        conn.commit()


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def has_active_jobs_under(target_root: str | Path) -> bool:
    root_text = normalized_path_text(target_root)
    placeholders = ", ".join("?" for _ in ACTIVE_JOB_STATUSES)
    with _DB_LOCK, connect() as conn:
        rows = conn.execute(
            f"SELECT target_dir, parsed_json, source FROM jobs WHERE status IN ({placeholders})",
            ACTIVE_JOB_STATUSES,
        ).fetchall()
    route_settings = library_route_settings()
    for row in rows:
        for active_root in active_job_roots(dict(row), route_settings):
            if paths_overlap(active_root, root_text):
                return True
    return False


def active_job_roots(job: dict[str, Any], route_settings: dict[str, str]) -> list[Path]:
    target_dir = job.get("target_dir")
    if target_dir:
        return [Path(str(target_dir))]

    parsed = parsed_download_or_none(job.get("parsed_json"))
    source = parsed.source if parsed else str(job.get("source") or "")
    if parsed and parsed.target_subdir:
        return compact_paths([safe_data_path(parsed.target_subdir)])
    if source == "generic":
        return compact_paths([safe_data_path("generic")])
    if source == "comfyui":
        return compact_paths([safe_data_path("comfyui", "workflows")])
    if source == "hitomi":
        return compact_paths([safe_data_path("hitomi")])
    if source == "gallerydl":
        return compact_paths([safe_data_path("gallery-dl")])
    if source == "huggingface":
        return huggingface_expected_roots(parsed, route_settings)
    if source == "civitai":
        return civitai_expected_roots(route_settings)
    return []


def huggingface_expected_roots(parsed: ParsedDownload | None, route_settings: dict[str, str]) -> list[Path]:
    roots: list[Path | None] = []
    repo_type = parsed.repo_type if parsed else "model"
    if repo_type == "dataset":
        roots.append(safe_data_path("huggingface", "datasets"))
    elif repo_type == "space":
        roots.append(safe_data_path("huggingface", "spaces"))
    else:
        roots.extend(route_root_paths(HF_POSSIBLE_ROUTE_TYPES, route_settings))
        roots.extend(
            [
                safe_data_path("huggingface", "models"),
                safe_data_path("huggingface", "vision"),
                safe_data_path("huggingface", "audio"),
            ]
        )
    return compact_paths(roots)


def civitai_expected_roots(route_settings: dict[str, str]) -> list[Path]:
    roots: list[Path | None] = route_root_paths(CIVITAI_POSSIBLE_ROUTE_TYPES, route_settings)
    roots.append(safe_data_path("civitai"))
    return compact_paths(roots)


def route_root_paths(route_types: tuple[str, ...], route_settings: dict[str, str]) -> list[Path | None]:
    roots: list[Path | None] = []
    for route_type in route_types:
        setting_key = ROUTE_SETTING_BY_TYPE.get(route_type)
        route_path = route_settings.get(setting_key or "")
        if route_path:
            roots.append(safe_data_path(route_path))
    return roots


def safe_data_path(*parts: str) -> Path | None:
    try:
        return safe_join(DATA_ROOT, *parts)
    except ValueError:
        return None


def compact_paths(paths: list[Path | None]) -> list[Path]:
    seen: set[str] = set()
    values: list[Path] = []
    for path in paths:
        if path is None:
            continue
        text = normalized_path_text(path)
        if text in seen:
            continue
        seen.add(text)
        values.append(path)
    return values


def parsed_download_or_none(payload: Any) -> ParsedDownload | None:
    if not payload:
        return None
    try:
        raw = json.loads(str(payload))
        if not isinstance(raw, dict):
            return None
        return ParsedDownload.from_dict(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def paths_overlap(path: str | Path, root: str | Path) -> bool:
    path_text = normalized_path_text(path)
    root_text = normalized_path_text(root)
    path_prefix = path_text.rstrip("\\/") + os.sep
    root_prefix = root_text.rstrip("\\/") + os.sep
    return path_text == root_text or path_text.startswith(root_prefix) or root_text.startswith(path_prefix)


def normalized_path_text(path: str | Path) -> str:
    text = str(path).rstrip("\\/")
    return text or os.sep


def append_log(job_id: int, message: str) -> None:
    stamp = utc_now()
    line = f"[{stamp}] {redact_sensitive_text(message)}\n"
    max_chars = job_log_max_chars()
    with _DB_LOCK, connect() as conn:
        if max_chars > 0:
            conn.execute(
                "UPDATE jobs SET log = substr(COALESCE(log, '') || ?, ?), updated_at = ? WHERE id = ?",
                (line, -max_chars, stamp, job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET log = COALESCE(log, '') || ?, updated_at = ? WHERE id = ?",
                (line, stamp, job_id),
            )
        conn.commit()


def job_log_max_chars() -> int:
    return nonnegative_int_env("JOB_LOG_MAX_CHARS", JOB_LOG_MAX_CHARS_DEFAULT)


def nonnegative_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return max(0, int(raw_value))
    except ValueError:
        return default


def parse_job_payload(job: dict[str, Any]) -> ParsedDownload:
    return ParsedDownload.from_dict(json.loads(job["parsed_json"]))


def parse_internal_job_payload(job: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = json.loads(str(job.get("parsed_json") or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    payload = raw.get("payload")
    return payload if isinstance(payload, dict) else {}


def redact_job_row(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("input_text", "error", "log"):
        if row.get(key) is not None:
            row[key] = redact_sensitive_text(str(row[key]))
    return row


def set_setting(key: str, value: str) -> None:
    now = utc_now()
    with _DB_LOCK, connect() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )
        conn.commit()


def get_setting(key: str) -> str | None:
    with _DB_LOCK, connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None


def get_secret(name: str) -> str | None:
    return get_setting(name) or os.getenv(name) or None


def normalize_route_path(value: str | None, default: str = "") -> str:
    raw = (value or "").strip().replace("\\", "/")
    if not raw:
        return default
    raw = raw.removeprefix("/data/").removeprefix("data/").strip("/")
    segments = [segment.strip() for segment in raw.split("/") if segment.strip() not in {"", ".", ".."}]
    return "/".join(segments) or default


def library_route_settings() -> dict[str, str]:
    values: dict[str, str] = {}
    for key, default in ROUTE_DEFAULTS.items():
        value = get_setting(key) or os.getenv(key) or default
        values[key] = normalize_route_path(value, default) if key != "LIBRARY_ACTIVE" else (value or default)
    return values


def settings_status() -> dict[str, Any]:
    status: dict[str, Any] = {}
    with _DB_LOCK, connect() as conn:
        rows = conn.execute("SELECT key, value, updated_at FROM settings").fetchall()
    db_settings = {
        str(row["key"]): {
            "value": str(row["value"]),
            "updated_at": str(row["updated_at"]),
        }
        for row in rows
    }
    secret_keys = (
        "HF_TOKEN",
        "CIVITAI_TOKEN",
        "GALLERY_DL_USERNAME",
        "GALLERY_DL_PASSWORD",
        "GALLERY_DL_COOKIES_FILE",
        "GALLERY_DL_COOKIES_FROM_BROWSER",
        "GALLERY_DL_EXTRA_OPTIONS",
        "YT_DLP_COOKIES_FILE",
        "YT_DLP_COOKIES_FROM_BROWSER",
        "YT_DLP_EXTRA_OPTIONS",
    )
    for key in secret_keys:
        env_value = os.getenv(key)
        db_value = db_settings.get(key)
        status[key] = {
            "configured": bool(env_value or db_value),
            "source": "ui" if db_value else ("environment" if env_value else None),
            "updated_at": db_value["updated_at"] if db_value else None,
            "value": "",
        }
    status["routes"] = library_route_settings()
    legacy_cooldown_default = legacy_queue_provider_cooldown_default(db_settings)
    status["queue"] = {
        key: settings_status_entry(key, default, db_settings)
        for key, default in {
            "MAX_CONCURRENT_DOWNLOADS": "3",
            "QUEUE_PER_PROVIDER_LIMIT": "1",
            "QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS": legacy_cooldown_default,
            "QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS": legacy_cooldown_default,
            "DOWNLOAD_STALL_TIMEOUT_SECONDS": str(DOWNLOAD_STALL_TIMEOUT_DEFAULT_SECONDS),
            "HITOMI_LISTING_QUEUE_MODE": "auto",
        }.items()
    }
    status["startup"] = {
        "GALLERY_DL_AUTO_UPDATE": settings_status_entry("GALLERY_DL_AUTO_UPDATE", "1", db_settings),
    }
    status["youtube"] = {
        "YT_DLP_FORMAT": settings_status_entry("YT_DLP_FORMAT", YT_DLP_DEFAULT_FORMAT, db_settings),
    }
    return status


def settings_status_entry(key: str, default: str, db_settings: dict[str, dict[str, str]]) -> dict[str, Any]:
    env_value = os.getenv(key)
    db_value = db_settings.get(key)
    return {
        "configured": bool(env_value or db_value),
        "source": "ui" if db_value else ("environment" if env_value else "default"),
        "updated_at": db_value["updated_at"] if db_value else None,
        "value": db_value["value"] if db_value else (env_value or default),
    }


def legacy_queue_provider_cooldown_default(db_settings: dict[str, dict[str, str]]) -> str:
    default = str(QUEUE_PROVIDER_COOLDOWN_MIN_DEFAULT_SECONDS)
    raw_value = db_settings.get("QUEUE_PROVIDER_COOLDOWN_SECONDS", {}).get("value") or os.getenv(
        "QUEUE_PROVIDER_COOLDOWN_SECONDS"
    )
    if raw_value is None:
        return default
    try:
        return str(max(0, int(raw_value)))
    except ValueError:
        return default
