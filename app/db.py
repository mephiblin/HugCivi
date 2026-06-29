from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ParsedDownload
from .utils import redact_sensitive_text

DB_PATH = Path(os.getenv("DB_PATH", "/config/jobs.sqlite3"))
_DB_LOCK = threading.RLock()

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
            (created_at, updated_at, input_text, parsed_json, source, status, log)
            VALUES (?, ?, ?, ?, ?, 'queued', ?)
            """,
            (
                now,
                now,
                redact_sensitive_text(parsed.raw_input),
                json.dumps(parsed_payload, ensure_ascii=False),
                parsed.source,
                f"[{now}] queued\n",
            ),
        )
        conn.commit()
        if cur.lastrowid is None:
            raise RuntimeError("Failed to create job")
        return int(cur.lastrowid)


def list_jobs(limit: int = 100) -> list[dict[str, Any]]:
    with _DB_LOCK, connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [redact_job_row(dict(row)) for row in rows]


def get_job(job_id: int) -> dict[str, Any] | None:
    with _DB_LOCK, connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return redact_job_row(dict(row)) if row else None


def get_next_queued_job() -> dict[str, Any] | None:
    with _DB_LOCK, connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY id ASC LIMIT 1"
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


def has_active_jobs_under(target_root: str | Path) -> bool:
    root_text = str(target_root)
    root_prefix = root_text.rstrip("\\/") + os.sep
    with _DB_LOCK, connect() as conn:
        rows = conn.execute(
            "SELECT target_dir FROM jobs WHERE status IN ('queued', 'running') AND target_dir IS NOT NULL"
        ).fetchall()
    for row in rows:
        target_dir = str(row["target_dir"])
        if target_dir == root_text or target_dir.startswith(root_prefix):
            return True
    return False


def append_log(job_id: int, message: str) -> None:
    stamp = utc_now()
    line = f"[{stamp}] {redact_sensitive_text(message)}\n"
    with _DB_LOCK, connect() as conn:
        conn.execute(
            "UPDATE jobs SET log = COALESCE(log, '') || ?, updated_at = ? WHERE id = ?",
            (line, stamp, job_id),
        )
        conn.commit()


def parse_job_payload(job: dict[str, Any]) -> ParsedDownload:
    return ParsedDownload.from_dict(json.loads(job["parsed_json"]))


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
    for key in ("HF_TOKEN", "CIVITAI_TOKEN"):
        env_value = os.getenv(key)
        db_value = db_settings.get(key)
        status[key] = {
            "configured": bool(env_value or db_value),
            "source": "ui" if db_value else ("environment" if env_value else None),
            "updated_at": db_value["updated_at"] if db_value else None,
            "value": db_value["value"] if db_value else (env_value or ""),
        }
    status["routes"] = library_route_settings()
    return status
