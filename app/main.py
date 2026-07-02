from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote, urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from . import db, internal_jobs, subscriptions
from .defaults import (
    DOWNLOAD_ARCHIVE_MAX_CONCURRENT_DEFAULT,
    DOWNLOAD_ARCHIVE_TTL_DEFAULT_SECONDS,
    DOWNLOAD_STALL_TIMEOUT_DEFAULT_SECONDS,
    LIBRARY_ITEM_SIZE_SCAN_MAX_FILES_DEFAULT,
    MAX_CONCURRENT_DOWNLOADS_HARD_LIMIT_DEFAULT,
    MEDIA_CACHE_MAX_BYTES_DEFAULT,
    MEDIA_FILE_SCAN_MAX_FILES_DEFAULT,
    MEDIA_CACHE_TTL_DEFAULT_SECONDS,
    MEDIA_TRANSCODE_MAX_CONCURRENT_DEFAULT,
    QUEUE_PROVIDER_COOLDOWN_MAX_DEFAULT_SECONDS,
    QUEUE_PROVIDER_COOLDOWN_MIN_DEFAULT_SECONDS,
    QUEUE_PER_PROVIDER_LIMIT_HARD_LIMIT_DEFAULT,
    YT_DLP_DEFAULT_FORMAT,
)
from .downloader import (
    cleanup_job_local_files,
    cleanup_job_partial_files,
    enqueue_job,
    folder_thumbnail_path,
    load_hitomi_listing_metadata,
    notify_queue_settings_changed,
    queue_hitomi_listing_galleries,
    remove_pending_job,
    start_workers,
    stop_workers,
    thumbnail_media_type,
    thumbnail_url_for_path,
    update_job_workflow_info,
)
from .models import ParsedDownload
from .parsers import InputParseError, parse_input
from .utils import human_bytes, redact_sensitive_text, safe_join, sanitize_segment
from .workflows import WorkflowParseError, find_workflow_png, load_workflow_view, save_workflow_bundle, workflow_max_bytes


def startup_tasks() -> None:
    db.init_db()
    ensure_route_folders()
    cleanup_stale_download_archives()
    cleanup_stale_media_cache()
    register_internal_job_handlers()
    start_workers()
    internal_jobs.start_workers()
    subscriptions.start_workers()
    start_library_indexer()


def shutdown_tasks() -> None:
    if not stop_library_indexer():
        print("library indexer did not stop before shutdown timeout", flush=True)
    if not internal_jobs.stop_workers():
        print("internal job scheduler did not stop before shutdown timeout", flush=True)
    if not subscriptions.stop_workers():
        print("subscription check scheduler did not stop before shutdown timeout", flush=True)
    if not stop_workers():
        print("download scheduler did not stop before shutdown timeout", flush=True)


def register_internal_job_handlers() -> None:
    internal_jobs.register_handler(INTERNAL_JOB_ARCHIVE_ZIP, run_archive_zip_job)
    internal_jobs.register_handler(INTERNAL_JOB_MEDIA_TRANSCODE, run_media_transcode_job)
    internal_jobs.register_handler(INTERNAL_JOB_MEDIA_POSTER, run_media_poster_job)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup_tasks()
    try:
        yield
    finally:
        shutdown_tasks()


app = FastAPI(title="hugcivi", version="0.1.0", lifespan=lifespan)
BASE_DIR = Path(__file__).resolve().parent
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))
DOWNLOAD_ARCHIVE_DIR = Path(os.getenv("DOWNLOAD_ARCHIVE_DIR", "/config/downloads"))
MEDIA_CACHE_DIR = Path(os.getenv("MEDIA_CACHE_DIR", "/config/media-cache"))
CHROME_EXTENSION_DIR = Path(os.getenv("HUGCIVI_CHROME_EXTENSION_DIR", str(BASE_DIR.parent / "chrome-extension")))
STARTUP_CONFIG_PATH = Path(os.getenv("HUGCIVI_STARTUP_CONFIG_FILE", str(db.DB_PATH.parent / "startup.env")))
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
security = HTTPBasic()
PWA_MANIFEST_PATH = BASE_DIR / "static" / "manifest.webmanifest"
PWA_SERVICE_WORKER_PATH = BASE_DIR / "static" / "sw.js"
INSECURE_PASSWORDS = {"", "change-this-password", "replace-with-a-strong-password"}
IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
SUBTITLE_EXTENSIONS = {".srt", ".vtt"}
SUBTITLE_LANGUAGE_LABELS = {
    "ko": "한국어",
    "en": "English",
}
BULK_ADD_MAX_ITEMS = 500
BULK_ADD_MAX_TEXT_LENGTH = 200_000
BULK_LINE_PREFIX_RE = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")
INTERNAL_JOB_ARCHIVE_ZIP = "archive_zip"
INTERNAL_JOB_MEDIA_TRANSCODE = "media_transcode"
INTERNAL_JOB_MEDIA_POSTER = "media_poster"
DOWNLOAD_ARCHIVE_MAX_FILES_DEFAULT = 50_000
DOWNLOAD_ARCHIVE_MAX_SOURCE_BYTES_DEFAULT = 0
DOWNLOAD_ARCHIVE_MIN_FREE_BYTES_DEFAULT = 0
BROWSER_MP4_EXTENSIONS = {".m4v", ".mp4"}
BROWSER_MP4_VIDEO_CODECS = {"h264"}
BROWSER_MP4_AUDIO_CODECS = {"aac", "mp3"}
MEDIA_TRANSCODE_LOCKS: dict[str, threading.Lock] = {}
MEDIA_TRANSCODE_LOCKS_LOCK = threading.Lock()
MEDIA_TRANSCODE_SEMAPHORE: threading.BoundedSemaphore | None = None
MEDIA_TRANSCODE_SEMAPHORE_LOCK = threading.Lock()
DOWNLOAD_ARCHIVE_SEMAPHORE: threading.BoundedSemaphore | None = None
DOWNLOAD_ARCHIVE_SEMAPHORE_LOCK = threading.Lock()
LIBRARY_INDEXER_THREAD: threading.Thread | None = None
LIBRARY_INDEXER_STOP = threading.Event()
LIBRARY_INDEXER_LOCK = threading.Lock()
STORAGE_USAGE_STATE_KEY = "storage.data_usage"
STORAGE_USAGE_THREAD: threading.Thread | None = None
STORAGE_USAGE_LOCK = threading.Lock()
MODEL_EXTENSIONS = {
    ".bin",
    ".ckpt",
    ".gguf",
    ".ggml",
    ".onnx",
    ".pt",
    ".pth",
    ".safetensors",
}
SIDECAR_FILENAMES = (
    "_archive_metadata.json",
    "_civitai_metadata.json",
    "_civitai_image_metadata.json",
    "_generic_metadata.json",
    "_hitomi_metadata.json",
    "_workflow_metadata.json",
)


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    expected_user = os.getenv("APP_USERNAME", "admin")
    expected_password = os.getenv("APP_PASSWORD", "")
    if expected_password in INSECURE_PASSWORDS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="APP_PASSWORD must be set to a strong password before use.",
        )
    user_ok = secrets.compare_digest(credentials.username, expected_user)
    password_ok = secrets.compare_digest(credentials.password, expected_password)
    if not (user_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@app.head("/manifest.webmanifest", include_in_schema=False)
@app.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest() -> FileResponse:
    return FileResponse(PWA_MANIFEST_PATH, media_type="application/manifest+json")


@app.head("/sw.js", include_in_schema=False)
@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(
        PWA_SERVICE_WORKER_PATH,
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


def storage_status() -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(DATA_ROOT)
    except OSError as exc:
        return {
            "path": "/data",
            "error": str(exc),
            "archive_usage": storage_usage_state(),
        }
    percent = round((usage.used / usage.total) * 100, 1) if usage.total else None
    return {
        "path": "/data",
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "used_human": human_bytes(usage.used),
        "free_human": human_bytes(usage.free),
        "total_human": human_bytes(usage.total),
        "percent": percent,
        "archive_usage": storage_usage_state(),
    }


def storage_usage_state() -> dict[str, Any]:
    raw_value = db.get_library_scan_state(STORAGE_USAGE_STATE_KEY, "")
    if not raw_value:
        state: dict[str, Any] = {"status": "not_calculated"}
    else:
        try:
            parsed = json.loads(raw_value)
            state = parsed if isinstance(parsed, dict) else {"status": "not_calculated"}
        except json.JSONDecodeError:
            state = {"status": "not_calculated"}
    status = str(state.get("status") or "not_calculated")
    if status == "scanning" and not storage_usage_scan_is_running():
        state["status"] = "interrupted"
        state.setdefault("error", "이전 계산이 완료되기 전에 중단되었습니다.")
    used_bytes = state.get("used_bytes")
    if isinstance(used_bytes, int):
        state["used_human"] = human_bytes(used_bytes)
    return state


def set_storage_usage_state(state: dict[str, Any]) -> None:
    db.set_library_scan_state(STORAGE_USAGE_STATE_KEY, json.dumps(state, ensure_ascii=False))


def storage_usage_scan_is_running() -> bool:
    with STORAGE_USAGE_LOCK:
        return STORAGE_USAGE_THREAD is not None and STORAGE_USAGE_THREAD.is_alive()


def start_storage_usage_scan() -> dict[str, Any]:
    global STORAGE_USAGE_THREAD
    with STORAGE_USAGE_LOCK:
        if STORAGE_USAGE_THREAD is not None and STORAGE_USAGE_THREAD.is_alive():
            return {
                **storage_usage_state_without_thread_check(),
                "status": "scanning",
            }
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        set_storage_usage_state(
            {
                "status": "scanning",
                "path": "/data",
                "used_bytes": 0,
                "file_count": 0,
                "dir_count": 0,
                "skipped_count": 0,
                "scanned_entries": 0,
                "started_at": now,
                "updated_at": now,
            }
        )
        thread = threading.Thread(target=storage_usage_scan_worker, name="storage-usage-scan", daemon=True)
        STORAGE_USAGE_THREAD = thread
        thread.start()
    return storage_usage_state()


def storage_usage_state_without_thread_check() -> dict[str, Any]:
    raw_value = db.get_library_scan_state(STORAGE_USAGE_STATE_KEY, "")
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def storage_usage_scan_worker() -> None:
    global STORAGE_USAGE_THREAD
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def update_progress(progress: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        set_storage_usage_state(
            {
                "status": "scanning",
                "path": "/data",
                "started_at": started_at,
                "updated_at": now,
                **progress,
            }
        )

    try:
        result = scan_data_root_usage(progress_callback=update_progress)
    except Exception as exc:  # noqa: BLE001 - background scan must report failure instead of killing the app
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        set_storage_usage_state(
            {
                "status": "failed",
                "path": "/data",
                "started_at": started_at,
                "finished_at": now,
                "updated_at": now,
                "error": str(exc),
            }
        )
    else:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        set_storage_usage_state(
            {
                "status": "done",
                "path": "/data",
                "started_at": started_at,
                "finished_at": now,
                "scanned_at": now,
                "updated_at": now,
                **result,
            }
        )
    finally:
        with STORAGE_USAGE_LOCK:
            if STORAGE_USAGE_THREAD is threading.current_thread():
                STORAGE_USAGE_THREAD = None


def scan_data_root_usage(progress_callback: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    if not DATA_ROOT.exists() or not DATA_ROOT.is_dir():
        raise FileNotFoundError(f"{DATA_ROOT} does not exist")
    batch_size = max(1, nonnegative_int_env("STORAGE_USAGE_SCAN_BATCH_SIZE", 1000))
    sleep_seconds = storage_usage_scan_sleep_seconds()
    stack = [DATA_ROOT]
    used_bytes = 0
    file_count = 0
    dir_count = 0
    skipped_count = 0
    scanned_entries = 0
    last_progress_at = 0.0

    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    scanned_entries += 1
                    try:
                        if entry.is_symlink():
                            skipped_count += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            dir_count += 1
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            file_count += 1
                            used_bytes += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        skipped_count += 1
                    if scanned_entries % batch_size == 0:
                        now = time.monotonic()
                        if progress_callback is not None and now - last_progress_at >= 1.0:
                            progress_callback(
                                {
                                    "used_bytes": used_bytes,
                                    "file_count": file_count,
                                    "dir_count": dir_count,
                                    "skipped_count": skipped_count,
                                    "scanned_entries": scanned_entries,
                                }
                            )
                            last_progress_at = now
                        if sleep_seconds > 0:
                            time.sleep(sleep_seconds)
        except OSError:
            skipped_count += 1

    return {
        "used_bytes": used_bytes,
        "file_count": file_count,
        "dir_count": dir_count,
        "skipped_count": skipped_count,
        "scanned_entries": scanned_entries,
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _: str = Depends(require_auth)) -> HTMLResponse:
    jobs = decorate_jobs(db.list_jobs())
    ensure_route_folders()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "jobs": jobs,
            "library_items": library_items(),
            "folder_tree": build_folder_tree(DATA_ROOT),
            "settings": db.settings_status(),
            "storage": storage_status(),
        },
    )


@app.post("/add")
def add_job(
    input_text: str = Form(...),
    target_subdir: str = Form(""),
    _: str = Depends(require_auth),
) -> RedirectResponse:
    try:
        parsed = parse_input(input_text, target_subdir.strip() or None)
        job_id = db.create_job(parsed)
        enqueue_job(job_id)
    except InputParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/jobs/bulk")
def add_jobs_bulk(
    input_text: str = Form(...),
    target_subdir: str = Form(""),
    _: str = Depends(require_auth),
) -> JSONResponse:
    if len(input_text) > BULK_ADD_MAX_TEXT_LENGTH:
        raise HTTPException(status_code=413, detail="입력 내용이 너무 큽니다.")
    lines = bulk_input_lines(input_text)
    if not lines:
        raise HTTPException(status_code=400, detail="추가할 URL이 없습니다.")
    if len(lines) > BULK_ADD_MAX_ITEMS:
        raise HTTPException(status_code=400, detail=f"한 번에 최대 {BULK_ADD_MAX_ITEMS}개까지 추가할 수 있습니다.")

    target = target_subdir.strip() or None
    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for line_number, value in lines:
        try:
            parsed = parse_input(value, target)
            job_id = db.create_job(parsed)
            enqueue_job(job_id)
        except InputParseError as exc:
            failed.append({"line": line_number, "input": value, "error": str(exc)})
            continue
        created.append({"line": line_number, "input": value, "job_id": job_id, "source": parsed.source})

    return JSONResponse(
        {
            "submitted_count": len(lines),
            "created_count": len(created),
            "failed_count": len(failed),
            "created": created,
            "failed": failed,
            "jobs": decorate_jobs(db.list_jobs()),
        }
    )


def bulk_input_lines(input_text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for line_number, line in enumerate(input_text.splitlines(), start=1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        value = BULK_LINE_PREFIX_RE.sub("", value).strip()
        if value:
            lines.append((line_number, value))
    return lines


@app.post("/settings")
def save_settings(
    hf_token: str = Form(""),
    civitai_token: str = Form(""),
    gallery_dl_username: str = Form(""),
    gallery_dl_password: str = Form(""),
    gallery_dl_cookies_file: str = Form(""),
    gallery_dl_cookies_from_browser: str = Form(""),
    gallery_dl_extra_options: str = Form(""),
    yt_dlp_cookies_file: str = Form(""),
    yt_dlp_cookies_from_browser: str = Form(""),
    yt_dlp_proxy: str = Form(""),
    yt_dlp_format: str = Form(""),
    yt_dlp_extra_options: str = Form(""),
    library_active: str = Form("ComfyUI"),
    route_llm_root: str = Form(""),
    route_lora_root: str = Form(""),
    route_checkpoint_root: str = Form(""),
    route_diffusion_model_root: str = Form(""),
    route_embedding_root: str = Form(""),
    route_vae_root: str = Form(""),
    route_controlnet_root: str = Form(""),
    route_upscaler_root: str = Form(""),
    queue_global_limit: str = Form("3"),
    queue_per_provider_limit: str = Form("1"),
    queue_provider_cooldown_min_seconds: str = Form(str(QUEUE_PROVIDER_COOLDOWN_MIN_DEFAULT_SECONDS)),
    queue_provider_cooldown_max_seconds: str = Form(str(QUEUE_PROVIDER_COOLDOWN_MAX_DEFAULT_SECONDS)),
    queue_stall_timeout_seconds: str = Form(str(DOWNLOAD_STALL_TIMEOUT_DEFAULT_SECONDS)),
    hitomi_listing_queue_mode: str = Form("auto"),
    gallery_dl_auto_update: str = Form("0"),
    _: str = Depends(require_auth),
) -> RedirectResponse:
    if hf_token.strip():
        db.set_setting("HF_TOKEN", hf_token.strip())

    if civitai_token.strip():
        db.set_setting("CIVITAI_TOKEN", civitai_token.strip())

    gallery_dl_fields = {
        "GALLERY_DL_USERNAME": gallery_dl_username,
        "GALLERY_DL_PASSWORD": gallery_dl_password,
        "GALLERY_DL_COOKIES_FILE": gallery_dl_cookies_file,
        "GALLERY_DL_COOKIES_FROM_BROWSER": gallery_dl_cookies_from_browser,
        "GALLERY_DL_EXTRA_OPTIONS": gallery_dl_extra_options,
    }
    for key, value in gallery_dl_fields.items():
        if value.strip():
            db.set_setting(key, value.strip())

    yt_dlp_fields = {
        "YT_DLP_COOKIES_FILE": yt_dlp_cookies_file,
        "YT_DLP_COOKIES_FROM_BROWSER": yt_dlp_cookies_from_browser,
        "YT_DLP_PROXY": yt_dlp_proxy,
        "YT_DLP_EXTRA_OPTIONS": yt_dlp_extra_options,
    }
    for key, value in yt_dlp_fields.items():
        if value.strip():
            db.set_setting(key, value.strip())
    db.set_setting("YT_DLP_FORMAT", yt_dlp_format.strip() or YT_DLP_DEFAULT_FORMAT)

    db.set_setting("LIBRARY_ACTIVE", library_active.strip() or db.ROUTE_DEFAULTS["LIBRARY_ACTIVE"])
    route_fields = {
        "ROUTE_LLM_ROOT": route_llm_root,
        "ROUTE_LORA_ROOT": route_lora_root,
        "ROUTE_CHECKPOINT_ROOT": route_checkpoint_root,
        "ROUTE_DIFFUSION_MODEL_ROOT": route_diffusion_model_root,
        "ROUTE_EMBEDDING_ROOT": route_embedding_root,
        "ROUTE_VAE_ROOT": route_vae_root,
        "ROUTE_CONTROLNET_ROOT": route_controlnet_root,
        "ROUTE_UPSCALER_ROOT": route_upscaler_root,
    }
    for key, value in route_fields.items():
        route_path = db.normalize_route_path(value, db.ROUTE_DEFAULTS[key])
        db.set_setting(key, route_path)
        safe_join(DATA_ROOT, route_path).mkdir(parents=True, exist_ok=True)

    db.set_setting(
        "MAX_CONCURRENT_DOWNLOADS",
        normalize_int_setting(
            queue_global_limit,
            3,
            minimum=1,
            maximum=max(
                1,
                nonnegative_int_env("MAX_CONCURRENT_DOWNLOADS_HARD_LIMIT", MAX_CONCURRENT_DOWNLOADS_HARD_LIMIT_DEFAULT),
            ),
        ),
    )
    db.set_setting(
        "QUEUE_PER_PROVIDER_LIMIT",
        normalize_int_setting(
            queue_per_provider_limit,
            1,
            minimum=1,
            maximum=max(
                1,
                nonnegative_int_env("QUEUE_PER_PROVIDER_LIMIT_HARD_LIMIT", QUEUE_PER_PROVIDER_LIMIT_HARD_LIMIT_DEFAULT),
            ),
        ),
    )
    cooldown_min = int(
        normalize_int_setting(
            queue_provider_cooldown_min_seconds,
            QUEUE_PROVIDER_COOLDOWN_MIN_DEFAULT_SECONDS,
            minimum=0,
        )
    )
    cooldown_max = int(
        normalize_int_setting(
            queue_provider_cooldown_max_seconds,
            QUEUE_PROVIDER_COOLDOWN_MAX_DEFAULT_SECONDS,
            minimum=0,
        )
    )
    if cooldown_max < cooldown_min:
        cooldown_min, cooldown_max = cooldown_max, cooldown_min
    db.set_setting(
        "QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS",
        str(cooldown_min),
    )
    db.set_setting(
        "QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS",
        str(cooldown_max),
    )
    db.set_setting(
        "DOWNLOAD_STALL_TIMEOUT_SECONDS",
        normalize_int_setting(queue_stall_timeout_seconds, DOWNLOAD_STALL_TIMEOUT_DEFAULT_SECONDS, minimum=0),
    )
    db.set_setting(
        "HITOMI_LISTING_QUEUE_MODE",
        "confirm" if hitomi_listing_queue_mode.strip().lower() == "confirm" else "auto",
    )
    gallery_dl_auto_update_value = normalize_bool_setting(gallery_dl_auto_update, default=True)
    db.set_setting("GALLERY_DL_AUTO_UPDATE", gallery_dl_auto_update_value)
    write_startup_config({"GALLERY_DL_AUTO_UPDATE": gallery_dl_auto_update_value})
    notify_queue_settings_changed()

    return RedirectResponse(url="/", status_code=303)


@app.post("/folders")
def create_folder(folder_path: str = Form(...), _: str = Depends(require_auth)) -> RedirectResponse:
    folder = safe_join(DATA_ROOT, folder_path.strip())
    folder.mkdir(parents=True, exist_ok=True)
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/jobs")
def api_jobs(
    limit: int = 100,
    cursor: int | None = None,
    _: str = Depends(require_auth),
) -> JSONResponse:
    jobs = decorate_jobs(db.list_job_summaries(limit=limit, before_id=cursor))
    if cursor is None:
        return JSONResponse(jobs)
    next_cursor = jobs[-1]["id"] if len(jobs) >= max(1, min(500, limit)) else None
    return JSONResponse({"ok": True, "jobs": jobs, "next_cursor": next_cursor})


@app.get("/api/subscriptions")
def api_subscriptions(
    limit: int = 100,
    _: str = Depends(require_auth),
) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "subscriptions": subscriptions.list_subscription_payloads(limit=limit),
            "scheduler": subscriptions.scheduler_status(),
            "settings": subscriptions.default_settings(),
        }
    )


@app.post("/api/subscriptions")
async def api_create_subscription(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object is required")
    try:
        subscription_id = subscriptions.create_subscription(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="subscription already exists") from exc
    subscription = subscriptions.get_subscription_payload(subscription_id)
    return JSONResponse({"ok": True, "subscription": subscription})


@app.get("/api/subscriptions/items")
def api_subscription_item_summaries(
    status: str = "active",
    subscription_id: int | None = None,
    limit: int = 100,
    cursor: int | None = None,
    _: str = Depends(require_auth),
) -> JSONResponse:
    if subscription_id is not None and not db.get_subscription(subscription_id):
        raise HTTPException(status_code=404, detail="subscription not found")
    try:
        summary = subscriptions.list_item_summary_payload(
            status=status,
            subscription_id=subscription_id,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"ok": True, **summary, "scheduler": subscriptions.scheduler_status()})


@app.get("/api/subscriptions/{subscription_id}/items")
def api_subscription_items(
    subscription_id: int,
    limit: int = 500,
    _: str = Depends(require_auth),
) -> JSONResponse:
    subscription = subscriptions.get_subscription_payload(subscription_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="subscription not found")
    return JSONResponse(
        {
            "ok": True,
            "subscription": subscription,
            "items": subscriptions.list_item_payloads(subscription_id, limit=limit),
        }
    )


@app.get("/api/subscriptions/{subscription_id}")
def api_subscription(
    subscription_id: int,
    _: str = Depends(require_auth),
) -> JSONResponse:
    subscription = subscriptions.get_subscription_payload(subscription_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="subscription not found")
    return JSONResponse({"ok": True, "subscription": subscription})


def subscription_item_action_response(item: dict[str, Any]) -> JSONResponse:
    subscription_id = int(item["subscription_id"])
    return JSONResponse(
        {
            "ok": True,
            "item": item,
            "subscription": subscriptions.get_subscription_payload(subscription_id),
            "items": subscriptions.list_item_payloads(subscription_id),
        }
    )


@app.post("/api/subscriptions/items/{item_id}/queue")
def api_queue_subscription_item(item_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    if not db.get_subscription_item(item_id):
        raise HTTPException(status_code=404, detail="subscription item not found")
    try:
        item = subscriptions.queue_subscription_item(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return subscription_item_action_response(item)


@app.post("/api/subscriptions/items/{item_id}/skip")
def api_skip_subscription_item(item_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    if not db.get_subscription_item(item_id):
        raise HTTPException(status_code=404, detail="subscription item not found")
    try:
        item = subscriptions.skip_subscription_item(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return subscription_item_action_response(item)


@app.post("/api/subscriptions/items/{item_id}/retry")
def api_retry_subscription_item(item_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    if not db.get_subscription_item(item_id):
        raise HTTPException(status_code=404, detail="subscription item not found")
    try:
        item = subscriptions.retry_subscription_item(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return subscription_item_action_response(item)


@app.patch("/api/subscriptions/{subscription_id}")
async def api_update_subscription(
    subscription_id: int,
    request: Request,
    _: str = Depends(require_auth),
) -> JSONResponse:
    if not db.get_subscription(subscription_id):
        raise HTTPException(status_code=404, detail="subscription not found")
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object is required")
    try:
        subscriptions.update_subscription(subscription_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "subscription": subscriptions.get_subscription_payload(subscription_id)})


@app.delete("/api/subscriptions/{subscription_id}")
def api_delete_subscription(subscription_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    if not subscriptions.delete_subscription(subscription_id):
        raise HTTPException(status_code=404, detail="subscription not found")
    return JSONResponse({"ok": True, "deleted": True})


@app.post("/api/subscriptions/{subscription_id}/check")
def api_check_subscription_now(subscription_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    try:
        result = subscriptions.check_subscription_now(subscription_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except subscriptions.SubscriptionCheckAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=redact_sensitive_text(str(exc))) from exc
    return JSONResponse(
        {
            "ok": True,
            "result": result,
            "subscription": subscriptions.get_subscription_payload(subscription_id),
            "items": subscriptions.list_item_payloads(subscription_id),
        }
    )


@app.get("/api/storage")
def api_storage(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(storage_status())


@app.post("/api/storage/archive-usage")
def api_start_storage_archive_usage(_: str = Depends(require_auth)) -> JSONResponse:
    start_storage_usage_scan()
    return JSONResponse({"ok": True, "storage": storage_status()})


@app.get("/api/addon/chrome-extension")
def api_chrome_extension_addon(_: str = Depends(require_auth)) -> FileResponse:
    archive_path = create_chrome_extension_archive()
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename="hugcivi-chrome-extension.zip",
        background=BackgroundTask(cleanup_file, archive_path),
    )


@app.post("/api/maintenance/db/wal")
async def api_db_wal(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    enabled = bool(payload.get("enabled", True))
    run_id = db.create_maintenance_run("db_wal", detail={"enabled": enabled})
    try:
        mode = db.set_wal_mode(enabled)
    except Exception as exc:
        db.finish_maintenance_run(run_id, "failed", {"error": str(exc)})
        raise
    detail = {"journal_mode": mode, "enabled": enabled}
    db.finish_maintenance_run(run_id, "done", detail)
    return JSONResponse({"ok": True, "run_id": run_id, **detail})


@app.post("/api/maintenance/db/checkpoint")
async def api_db_checkpoint(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    mode = str(payload.get("mode") or "PASSIVE")
    run_id = db.create_maintenance_run("db_checkpoint", detail={"mode": mode})
    try:
        detail = db.checkpoint_database(mode)
    except Exception as exc:
        db.finish_maintenance_run(run_id, "failed", {"error": str(exc)})
        raise
    db.finish_maintenance_run(run_id, "done", detail)
    return JSONResponse({"ok": True, "run_id": run_id, **detail})


@app.post("/api/maintenance/db/optimize")
def api_db_optimize(_: str = Depends(require_auth)) -> JSONResponse:
    run_id = db.create_maintenance_run("db_optimize")
    try:
        db.optimize_database()
    except Exception as exc:
        db.finish_maintenance_run(run_id, "failed", {"error": str(exc)})
        raise
    db.finish_maintenance_run(run_id, "done")
    return JSONResponse({"ok": True, "run_id": run_id})


@app.post("/api/maintenance/db/compact")
def api_db_compact(_: str = Depends(require_auth)) -> JSONResponse:
    run_id = db.create_maintenance_run("db_compact")
    try:
        db.vacuum_database()
    except Exception as exc:
        db.finish_maintenance_run(run_id, "failed", {"error": str(exc)})
        raise
    db.finish_maintenance_run(run_id, "done")
    return JSONResponse({"ok": True, "run_id": run_id})


@app.post("/api/maintenance/db/backup")
async def api_db_backup(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    filename = sanitize_segment(
        str(payload.get("filename") or f"jobs-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.sqlite3"),
        "jobs-backup.sqlite3",
    )
    if not filename.endswith(".sqlite3"):
        filename = f"{filename}.sqlite3"
    destination = db.DB_PATH.parent / "backups" / filename
    run_id = db.create_maintenance_run("db_backup", detail={"path": str(destination)})
    try:
        db.backup_database(destination)
    except Exception as exc:
        db.finish_maintenance_run(run_id, "failed", {"error": str(exc), "path": str(destination)})
        raise
    detail = {
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
        "credential_backup": True,
    }
    db.finish_maintenance_run(run_id, "done", detail)
    return JSONResponse({"ok": True, "run_id": run_id, **detail})


def jobs_response() -> JSONResponse:
    return JSONResponse({"ok": True, "jobs": decorate_jobs(db.list_jobs())})


def require_job(job_id: int) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


def enqueue_job_for_row(job_id: int, job: dict[str, Any]) -> None:
    if db.is_internal_job(job):
        internal_jobs.enqueue_job(job_id)
    else:
        enqueue_job(job_id)


def remove_pending_job_for_row(job_id: int, job: dict[str, Any]) -> None:
    if db.is_internal_job(job):
        internal_jobs.remove_pending_job(job_id)
    else:
        remove_pending_job(job_id)


def cleanup_job_files_for_row(job_id: int, job: dict[str, Any]) -> None:
    if db.is_download_job(job):
        cleanup_job_partial_files(job_id)
        cleanup_job_local_files(job_id)


@app.post("/api/jobs/clear")
def api_clear_jobs(_: str = Depends(require_auth)) -> JSONResponse:
    cleanup_inactive_job_partial_files()
    deleted = db.clear_job_history()
    vacuumed = False
    if deleted and bool_env("SQLITE_VACUUM_AFTER_CLEAR", default=False):
        db.vacuum_database()
        vacuumed = True
    return JSONResponse({"ok": True, "deleted": deleted, "vacuumed": vacuumed, "jobs": decorate_jobs(db.list_jobs())})


def cleanup_inactive_job_partial_files(limit: int = 5000) -> int:
    cleaned = 0
    for job in db.list_inactive_jobs(limit=limit):
        if db.is_internal_job(job):
            continue
        try:
            cleanup_job_partial_files(int(job["id"]))
            cleaned += 1
        except (KeyError, TypeError, ValueError, OSError):
            continue
    return cleaned


@app.post("/api/jobs/{job_id}/pause")
def api_pause_job(job_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    job = require_job(job_id)
    status = str(job.get("status"))
    if status == "queued":
        remove_pending_job_for_row(job_id, job)
        db.update_job(job_id, status="paused", error=None)
        db.append_log(job_id, "pause requested")
    elif status == "running":
        db.update_job(job_id, status="pausing", error=None)
        db.append_log(job_id, "pause requested")
    elif status in {"paused", "pausing"}:
        pass
    else:
        raise HTTPException(status_code=400, detail="정지할 수 있는 작업 상태가 아닙니다.")
    return jobs_response()


@app.post("/api/jobs/{job_id}/resume")
def api_resume_job(job_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    job = require_job(job_id)
    status = str(job.get("status"))
    if status != "paused":
        raise HTTPException(status_code=400, detail="재개할 수 있는 작업 상태가 아닙니다.")
    db.update_job(job_id, status="queued", error=None)
    db.append_log(job_id, "resume requested")
    enqueue_job_for_row(job_id, job)
    return jobs_response()


@app.post("/api/jobs/{job_id}/retry")
def api_retry_job(job_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    job = require_job(job_id)
    status = str(job.get("status"))
    if status not in {"failed", "canceled"}:
        raise HTTPException(status_code=400, detail="재시도할 수 있는 작업 상태가 아닙니다.")
    db.update_job(job_id, status="queued", error=None, progress_bytes=0, total_bytes=None)
    db.append_log(job_id, "retry requested")
    enqueue_job_for_row(job_id, job)
    return jobs_response()


@app.delete("/api/jobs/{job_id}")
def api_delete_job(job_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    job = require_job(job_id)
    status = str(job.get("status"))
    if status in {"running", "pausing"}:
        db.update_job(job_id, status="deleting", error=None)
        db.append_log(job_id, "delete requested")
    elif status == "deleting":
        pass
    else:
        remove_pending_job_for_row(job_id, job)
        cleanup_job_files_for_row(job_id, job)
        db.delete_job(job_id)
    return jobs_response()


@app.get("/api/folders")
def api_folders(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(build_folder_tree(DATA_ROOT))


@app.post("/api/folders")
async def api_create_folder(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    parent = existing_data_path(str(payload.get("parent_path") or ""))
    if not parent.is_dir():
        raise HTTPException(status_code=400, detail="부모 경로는 폴더여야 합니다.")
    ensure_real_directory_destination(parent)

    folder_name = clean_item_name(str(payload.get("folder_name") or ""))
    target = safe_join(DATA_ROOT, relative_data_path(parent), folder_name)
    if target.exists():
        raise HTTPException(status_code=409, detail="같은 이름의 폴더가 이미 있습니다.")
    target.mkdir()
    return JSONResponse({"ok": True, "path": relative_data_path(target), "folders": build_folder_tree(DATA_ROOT)})


@app.get("/api/library")
def api_library(mode: str = "index", _: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(library_items(mode=mode))


@app.post("/api/library/reindex")
def api_library_reindex(_: str = Depends(require_auth)) -> JSONResponse:
    result = scan_library_index_batch(max_paths=library_reindex_batch_size(), reset=True)
    return JSONResponse({"ok": True, **result, "items": db.list_library_index_items()})


@app.get("/api/media/list")
def api_media_list(path: str, _: str = Depends(require_auth)) -> JSONResponse:
    source = existing_data_path(path)
    ensure_downloadable_path(source)
    files = media_files_for_path(source)
    payload: dict[str, Any] = {
        "ok": True,
        "path": relative_data_path(source),
        "name": source.name,
        "items": [media_item_payload(item, index) for index, item in enumerate(files)],
    }
    metadata = civitai_image_archive_metadata(source)
    if metadata:
        payload["metadata"] = metadata
    return JSONResponse(payload)


@app.get("/api/media/archive")
def api_media_archive(path: str, _: str = Depends(require_auth)) -> JSONResponse:
    return api_media_list(path, _)


@app.post("/api/civitai/resource-health")
async def api_civitai_resource_health(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="요청 본문이 올바르지 않습니다.")
    ids = requested_model_version_ids(payload)
    return JSONResponse({"ok": True, "resources": civitai_resource_health_payload(ids)})


@app.get("/api/media/file")
def api_media_file(path: str, _: str = Depends(require_auth)) -> FileResponse:
    source = existing_data_path(path)
    if not source.is_file() or not is_media_file(source):
        raise HTTPException(status_code=404, detail="미디어 파일을 찾지 못했습니다.")
    return FileResponse(source, media_type=media_type_for_path(source))


@app.get("/api/media/play")
def api_media_play(path: str, _: str = Depends(require_auth)) -> Response:
    source = existing_data_path(path)
    if not source.is_file() or not is_video_file(source):
        raise HTTPException(status_code=404, detail="동영상 파일을 찾지 못했습니다.")
    playable = browser_playable_video_path_if_ready(source)
    if playable is None:
        return JSONResponse(
            {
                "ok": False,
                "job_required": True,
                "job_kind": INTERNAL_JOB_MEDIA_TRANSCODE,
                "path": relative_data_path(source),
            },
            status_code=202,
        )
    return FileResponse(playable, media_type="video/mp4")


@app.get("/api/media/subtitle")
def api_media_subtitle(path: str, _: str = Depends(require_auth)) -> Response:
    source = existing_data_path(path)
    if not source.is_file() or not is_subtitle_file(source):
        raise HTTPException(status_code=404, detail="자막 파일을 찾지 못했습니다.")
    if source.suffix.lower() == ".vtt":
        return FileResponse(source, media_type="text/vtt; charset=utf-8", filename=source.name)
    return PlainTextResponse(srt_to_vtt(source.read_text(encoding="utf-8-sig", errors="replace")), media_type="text/vtt")


@app.get("/api/media/poster")
def api_media_poster(path: str, _: str = Depends(require_auth)) -> Response:
    source = existing_data_path(path)
    if source.is_file() and is_image_file(source):
        return FileResponse(source, media_type=media_type_for_path(source), filename=source.name)
    if not source.is_file() or not is_video_file(source):
        raise HTTPException(status_code=404, detail="동영상 파일을 찾지 못했습니다.")
    poster = video_poster_path_if_ready(source)
    if poster is None:
        return JSONResponse(
            {
                "ok": False,
                "job_required": True,
                "job_kind": INTERNAL_JOB_MEDIA_POSTER,
                "path": relative_data_path(source),
            },
            status_code=202,
        )
    return FileResponse(poster, media_type="image/jpeg", filename=f"{source.stem}.jpg")


@app.post("/api/media/transcode-jobs")
async def api_create_media_transcode_job(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    source = existing_video_path(str(payload.get("path") or ""))
    ready = browser_playable_video_path_if_ready(source)
    if ready is not None:
        return JSONResponse(
            {
                "ok": True,
                "ready": True,
                "path": relative_data_path(source),
                "url": media_play_url_for_ready_source(source, ready),
            }
        )
    job_id = db.create_internal_job(
        INTERNAL_JOB_MEDIA_TRANSCODE,
        input_text=f"transcode:{relative_data_path(source)}",
        payload={"path": relative_data_path(source)},
        target_dir=source.parent,
        filename=f"{source.stem}.mp4",
        total_bytes=source.stat().st_size,
        metadata={"media_job": {"kind": INTERNAL_JOB_MEDIA_TRANSCODE, "path": relative_data_path(source)}},
    )
    internal_jobs.enqueue_job(job_id)
    return JSONResponse({"ok": True, "ready": False, "job": decorate_job(db.get_job(job_id) or {})})


@app.get("/api/media/transcode-jobs/{job_id}")
def api_media_transcode_job(job_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    job = require_internal_job_kind(job_id, INTERNAL_JOB_MEDIA_TRANSCODE)
    return JSONResponse({"ok": True, "job": decorate_job(job)})


@app.get("/api/media/transcode-jobs/{job_id}/file")
def api_media_transcode_job_file(job_id: int, _: str = Depends(require_auth)) -> FileResponse:
    job = require_internal_job_kind(job_id, INTERNAL_JOB_MEDIA_TRANSCODE)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="아직 재생 파일이 준비되지 않았습니다.")
    artifact_path = Path(str(job.get("artifact_path") or ""))
    if not safe_media_artifact_file(artifact_path):
        raise HTTPException(status_code=404, detail="재생 파일을 찾지 못했습니다.")
    return FileResponse(artifact_path, media_type="video/mp4", filename=str(job.get("filename") or artifact_path.name))


@app.post("/api/media/poster-jobs")
async def api_create_media_poster_job(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    source = existing_video_path(str(payload.get("path") or ""))
    ready = video_poster_path_if_ready(source)
    if ready is not None:
        return JSONResponse(
            {
                "ok": True,
                "ready": True,
                "path": relative_data_path(source),
                "url": f"/api/media/poster?path={quote(relative_data_path(source), safe='/')}",
            }
        )
    job_id = db.create_internal_job(
        INTERNAL_JOB_MEDIA_POSTER,
        input_text=f"poster:{relative_data_path(source)}",
        payload={"path": relative_data_path(source)},
        target_dir=source.parent,
        filename=f"{source.stem}.jpg",
        total_bytes=1,
        metadata={"media_job": {"kind": INTERNAL_JOB_MEDIA_POSTER, "path": relative_data_path(source)}},
    )
    internal_jobs.enqueue_job(job_id)
    return JSONResponse({"ok": True, "ready": False, "job": decorate_job(db.get_job(job_id) or {})})


@app.get("/api/media/poster-jobs/{job_id}")
def api_media_poster_job(job_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    job = require_internal_job_kind(job_id, INTERNAL_JOB_MEDIA_POSTER)
    return JSONResponse({"ok": True, "job": decorate_job(job)})


@app.get("/api/media/poster-jobs/{job_id}/file")
def api_media_poster_job_file(job_id: int, _: str = Depends(require_auth)) -> FileResponse:
    job = require_internal_job_kind(job_id, INTERNAL_JOB_MEDIA_POSTER)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="아직 썸네일이 준비되지 않았습니다.")
    artifact_path = Path(str(job.get("artifact_path") or ""))
    if not safe_media_artifact_file(artifact_path):
        raise HTTPException(status_code=404, detail="썸네일 파일을 찾지 못했습니다.")
    return FileResponse(artifact_path, media_type="image/jpeg", filename=str(job.get("filename") or artifact_path.name))


@app.post("/api/fs/rename")
async def api_rename_path(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    source = existing_data_path(str(payload.get("path") or ""))
    ensure_mutable_path(source)
    ensure_no_active_jobs(source)

    new_name = clean_item_name(str(payload.get("new_name") or ""))
    target = safe_join(DATA_ROOT, relative_data_path(source.parent), new_name)
    if target.exists():
        raise HTTPException(status_code=409, detail="같은 이름의 폴더가 이미 있습니다.")
    old_relative = relative_data_path(source)
    new_relative = relative_data_path(target)
    source.rename(target)
    db.update_target_dir_prefix(source, target)
    db.update_favorite_path_prefix(old_relative, new_relative)
    db.update_note_path_prefix(old_relative, new_relative)
    db.update_library_item_path_prefix(old_relative, new_relative)
    return JSONResponse({"ok": True, "path": relative_data_path(target), "folders": build_folder_tree(DATA_ROOT)})


@app.post("/api/fs/move")
async def api_move_path(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    source = existing_data_path(str(payload.get("path") or ""))
    ensure_mutable_path(source)
    ensure_no_active_jobs(source)

    destination = existing_data_path(str(payload.get("destination") or ""))
    if not destination.is_dir():
        raise HTTPException(status_code=400, detail="이동 대상은 폴더여야 합니다.")
    ensure_real_directory_destination(destination)
    target = safe_join(DATA_ROOT, relative_data_path(destination), source.name)
    if target == source:
        return JSONResponse({"ok": True, "path": relative_data_path(source), "folders": build_folder_tree(DATA_ROOT)})
    if source in target.parents:
        raise HTTPException(status_code=400, detail="자기 자신의 하위 폴더로 이동할 수 없습니다.")
    if target.exists():
        raise HTTPException(status_code=409, detail="이동 대상에 같은 이름의 폴더가 이미 있습니다.")

    old_relative = relative_data_path(source)
    new_relative = relative_data_path(target)
    shutil.move(str(source), str(target))
    db.update_target_dir_prefix(source, target)
    db.update_favorite_path_prefix(old_relative, new_relative)
    db.update_note_path_prefix(old_relative, new_relative)
    db.update_library_item_path_prefix(old_relative, new_relative)
    return JSONResponse({"ok": True, "path": relative_data_path(target), "folders": build_folder_tree(DATA_ROOT)})


@app.post("/api/fs/delete")
async def api_delete_path(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    source = existing_data_path(str(payload.get("path") or ""))
    ensure_mutable_path(source)
    ensure_no_active_jobs(source)
    relative_path = relative_data_path(source)

    if source.is_symlink():
        source.unlink()
    elif source.is_dir():
        shutil.rmtree(source)
    else:
        source.unlink()
    db.clear_target_dir_prefix(source)
    db.clear_favorite_path_prefix(relative_path)
    db.clear_note_path_prefix(relative_path)
    db.clear_library_item_prefix(relative_path)
    return JSONResponse({"ok": True, "folders": build_folder_tree(DATA_ROOT)})


@app.post("/api/favorites")
async def api_set_favorite(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    source = existing_data_path(str(payload.get("path") or ""))
    ensure_downloadable_path(source)
    relative_path = relative_data_path(source)
    requested = payload.get("favorite")
    enabled = bool(requested) if requested is not None else relative_path not in db.favorite_paths()
    db.set_favorite(relative_path, enabled)
    return JSONResponse({"ok": True, "path": relative_path, "favorite": enabled})


@app.post("/api/workflows/import")
async def api_import_workflow(
    file: UploadFile = File(...),
    target_subdir: str = Form(""),
    _: str = Depends(require_auth),
) -> JSONResponse:
    filename = sanitize_segment(file.filename or "workflow.json", "workflow.json")
    data = await file.read(workflow_max_bytes() + 1)
    if len(data) > workflow_max_bytes():
        raise HTTPException(status_code=413, detail=f"워크플로우 파일은 최대 {human_bytes(workflow_max_bytes())}까지 지원합니다.")

    parsed = ParsedDownload(
        source="comfyui",
        raw_input=f"upload:{filename}",
        target_subdir=target_subdir.strip() or None,
        comfyui_workflow_filename=filename,
        comfyui_workflow_format=Path(filename).suffix.lower().removeprefix(".") or None,
    )
    job_id = db.create_job(parsed)
    db.update_job(job_id, status="running", filename=filename, progress_bytes=0, total_bytes=len(data), error=None)
    db.append_log(job_id, f"started source=comfyui upload={filename}")
    try:
        result = save_workflow_bundle(
            data,
            filename,
            parsed.raw_input,
            DATA_ROOT,
            target_subdir=parsed.target_subdir,
        )
        update_job_workflow_info(job_id, result, data_size=len(data))
    except WorkflowParseError as exc:
        db.append_log(job_id, f"FAILED: {exc}")
        db.update_job(job_id, status="failed", error=str(exc), progress_bytes=len(data), total_bytes=len(data))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.update_job(job_id, status="done")
    db.append_log(job_id, "done")
    return JSONResponse(
        {
            "ok": True,
            "job_id": job_id,
            "path": display_target_path(db.get_job(job_id) or {}),
            "jobs": decorate_jobs(db.list_jobs()),
            "folders": build_folder_tree(DATA_ROOT),
        }
    )


@app.get("/api/workflows/view")
def api_workflow_view(path: str, _: str = Depends(require_auth)) -> JSONResponse:
    source = existing_data_path(path)
    try:
        payload = load_workflow_view(source)
    except WorkflowParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(
        {
            "ok": True,
            "path": relative_data_path(source),
            "name": source.name,
            **payload,
        }
    )


@app.get("/api/workflows/preview")
def api_workflow_preview(path: str, _: str = Depends(require_auth)) -> FileResponse:
    source = existing_data_path(path)
    preview = find_workflow_png(source)
    if not preview:
        raise HTTPException(status_code=404, detail="워크플로우 PNG를 찾지 못했습니다.")
    return FileResponse(preview, media_type="image/png", filename=preview.name)


@app.get("/api/fs/preview")
def api_fs_preview(path: str, _: str = Depends(require_auth)) -> FileResponse:
    source = existing_data_path(path)
    preview = folder_thumbnail_path(source)
    if not preview:
        raise HTTPException(status_code=404, detail="이미지 미리보기를 찾지 못했습니다.")
    return FileResponse(preview, media_type=thumbnail_media_type(preview), filename=preview.name)


@app.get("/api/fs/download-info")
def api_download_info(path: str, _: str = Depends(require_auth)) -> JSONResponse:
    source = existing_data_path(path)
    ensure_downloadable_path(source)
    return JSONResponse(
        {
            "ok": True,
            "path": relative_data_path(source),
            "name": source.name,
            "kind": "folder" if source.is_dir() else "file",
            "filename": download_filename(source),
            "async_job": source.is_dir(),
        }
    )


@app.post("/api/fs/download-jobs")
async def api_create_download_job(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    source = existing_data_path(str(payload.get("path") or ""))
    ensure_downloadable_path(source)
    if not source.is_dir():
        return JSONResponse(
            {
                "ok": True,
                "kind": "file",
                "download_url": f"/api/fs/download?path={quote(relative_data_path(source))}",
                "filename": source.name,
            }
        )

    try:
        preflight = preflight_archive_job(source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc

    relative_path = relative_data_path(source)
    job_id = db.create_internal_job(
        INTERNAL_JOB_ARCHIVE_ZIP,
        input_text=f"zip:{relative_path}",
        payload={"path": relative_path},
        target_dir=str(source),
        filename=download_filename(source),
        total_bytes=int(preflight["source_bytes"]),
        metadata={"archive_preflight": preflight},
    )
    internal_jobs.enqueue_job(job_id)
    return JSONResponse(
        {
            "ok": True,
            "kind": "folder",
            "job": decorate_job(db.get_job(job_id) or {}),
        }
    )


@app.get("/api/fs/download-jobs/{job_id}")
def api_download_job(job_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    job = require_archive_job(job_id)
    return JSONResponse({"ok": True, "job": decorate_job(job)})


@app.get("/api/fs/download-jobs/{job_id}/file")
def api_download_job_file(job_id: int, _: str = Depends(require_auth)) -> FileResponse:
    job = require_archive_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="아직 다운로드 파일이 준비되지 않았습니다.")
    artifact_path = Path(str(job.get("artifact_path") or ""))
    if not safe_cache_file(DOWNLOAD_ARCHIVE_DIR, artifact_path):
        raise HTTPException(status_code=404, detail="다운로드 파일을 찾지 못했습니다.")
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="다운로드 파일을 찾지 못했습니다.")
    return FileResponse(artifact_path, media_type="application/zip", filename=str(job.get("filename") or artifact_path.name))


@app.get("/api/fs/properties")
def api_path_properties(path: str, _: str = Depends(require_auth)) -> JSONResponse:
    source = existing_data_path(path)
    relative_path = relative_data_path(source)
    stat = source.stat()
    size = path_size(source)
    urls = source_input_values(source)
    return JSONResponse(
        {
            "ok": True,
            "path": relative_path,
            "name": source.name,
            "kind": "folder" if source.is_dir() else "file",
            "size_bytes": size,
            "size_human": human_bytes(size),
            "extensions": path_extensions(source),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "urls": urls,
            "note": db.get_item_note(relative_path),
        }
    )


@app.post("/api/fs/note")
async def api_save_path_note(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    source = existing_data_path(str(payload.get("path") or ""))
    relative_path = relative_data_path(source)
    note = str(payload.get("note") or "")
    db.set_item_note(relative_path, note)
    return JSONResponse({"ok": True, "path": relative_path, "note": db.get_item_note(relative_path)})


@app.get("/api/fs/download")
def api_download_path(path: str, _: str = Depends(require_auth)) -> FileResponse:
    source = existing_data_path(path)
    ensure_downloadable_path(source)
    if source.is_dir():
        archive_path = create_zip_archive(source)
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=download_filename(source),
            background=BackgroundTask(cleanup_file, archive_path),
        )
    return FileResponse(source, media_type="application/octet-stream", filename=source.name)


@app.get("/api/jobs/{job_id}")
def api_job(job_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(decorate_job(job))


@app.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
def job_log(job_id: int, _: str = Depends(require_auth)) -> PlainTextResponse:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return PlainTextResponse(job.get("log") or "")


@app.get("/api/hitomi/listing/{job_id}")
def api_hitomi_listing(job_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    try:
        metadata = load_hitomi_listing_metadata(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "listing": metadata})


@app.post("/api/hitomi/listing/{job_id}/queue")
async def api_queue_hitomi_listing(job_id: int, request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    raw_ids = payload.get("gallery_ids") or payload.get("galleryIds") or []
    if payload.get("all") is True:
        gallery_ids: list[str] | None = None
    elif isinstance(raw_ids, list):
        gallery_ids = [str(value).strip() for value in raw_ids if str(value).strip()]
    else:
        raise HTTPException(status_code=400, detail="gallery_ids 배열이 필요합니다.")
    try:
        result = queue_hitomi_listing_galleries(job_id, gallery_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(
        {
            "ok": True,
            "queued": result["queued"],
            "skipped": result["skipped"],
            "listing": result["metadata"],
            "jobs": decorate_jobs(db.list_jobs()),
        }
    )


def decorate_jobs(jobs: list[dict], *, include_log: bool = False) -> list[dict]:
    favorites = db.favorite_paths()
    return [decorate_job(job, favorites, include_log=include_log) for job in jobs]


def decorate_job(job: dict, favorites: set[str] | None = None, *, include_log: bool = True) -> dict:
    progress = job.get("progress_bytes") or 0
    total = job.get("total_bytes")
    percent = None
    if total:
        percent = min(100, round(progress * 100 / total, 1))
    parsed = parse_job_for_display(job)
    target_path = display_target_path(job)
    existing_source_url = str(job.get("source_url") or "")
    job = dict(job)
    job.pop("parsed_json", None)
    if not include_log:
        job.pop("log", None)
        job.pop("metadata_json", None)
    job["progress_human"] = human_bytes(progress)
    job["total_human"] = human_bytes(total)
    job["percent"] = percent
    job["target_path"] = target_path
    job["job_kind"] = db.normalized_job_kind(job.get("job_kind"))
    if job["job_kind"] != db.JOB_KIND_DOWNLOAD and str(job.get("source") or "") == "internal":
        job["source"] = job["job_kind"]
    if (
        not job.get("thumbnail_url")
        and job.get("target_dir")
        and job.get("status") in {"done", "failed", "paused", "canceled"}
    ):
        job["thumbnail_url"] = thumbnail_url_for_path(Path(str(job.get("target_dir"))))
    favorite_paths = favorites if favorites is not None else db.favorite_paths()
    job["favorite"] = bool(target_path and target_path in favorite_paths)
    job["source_url"] = source_url_for_job(job, parsed) or existing_source_url
    return job


def library_items(max_items: int = 1000, *, mode: str = "index") -> list[dict[str, Any]]:
    if mode != "live":
        indexed_items = db.list_library_index_items(limit=max_items)
        if indexed_items:
            favorites = db.favorite_paths()
            for item in indexed_items:
                target_path = str(item.get("target_path") or "")
                item["favorite"] = bool(target_path and target_path in favorites)
            return indexed_items
        if db.get_library_scan_state("library.indexing", "0") == "1":
            return []

    favorites = db.favorite_paths()
    items: list[dict[str, Any]] = []
    indexed_dirs: set[Path] = set()

    for path in iter_data_paths(max_items=max_items * 3):
        if len(items) >= max_items:
            break
        try:
            if path.is_dir() and should_index_directory(path):
                item = library_item_for_path(path, favorites)
                items.append(item)
                indexed_dirs.add(path.resolve())
        except OSError:
            continue

    for path in iter_data_paths(max_items=max_items * 6):
        if len(items) >= max_items:
            break
        try:
            if not path.is_file() or not is_library_file(path):
                continue
            resolved_parent = path.parent.resolve()
            if any(indexed == resolved_parent or indexed in resolved_parent.parents for indexed in indexed_dirs):
                continue
            items.append(library_item_for_path(path, favorites))
        except OSError:
            continue

    return sorted(items, key=lambda item: (str(item.get("target_path") or "").lower()))


def start_library_indexer() -> None:
    global LIBRARY_INDEXER_THREAD
    with LIBRARY_INDEXER_LOCK:
        if LIBRARY_INDEXER_THREAD is not None and LIBRARY_INDEXER_THREAD.is_alive():
            return
        LIBRARY_INDEXER_STOP.clear()
        thread = threading.Thread(target=library_indexer_loop, name="library-indexer", daemon=True)
        LIBRARY_INDEXER_THREAD = thread
        thread.start()


def stop_library_indexer(timeout_seconds: float = 5.0) -> bool:
    global LIBRARY_INDEXER_THREAD
    with LIBRARY_INDEXER_LOCK:
        thread = LIBRARY_INDEXER_THREAD
        if thread is None or not thread.is_alive():
            LIBRARY_INDEXER_THREAD = None
            LIBRARY_INDEXER_STOP.set()
            return True
        LIBRARY_INDEXER_STOP.set()
    thread.join(timeout=max(0.0, timeout_seconds))
    stopped = not thread.is_alive()
    if stopped:
        with LIBRARY_INDEXER_LOCK:
            if LIBRARY_INDEXER_THREAD is thread:
                LIBRARY_INDEXER_THREAD = None
    return stopped


def library_indexer_loop() -> None:
    initial_delay = nonnegative_int_env("LIBRARY_INDEXER_START_DELAY_SECONDS", 5)
    if LIBRARY_INDEXER_STOP.wait(initial_delay):
        return
    while not LIBRARY_INDEXER_STOP.is_set():
        try:
            scan_library_index_batch(max_paths=library_index_batch_size())
            db.prune_missing_library_items(limit=library_index_batch_size())
        except Exception as exc:  # noqa: BLE001 - background indexer must keep running
            print(f"library indexer failed: {exc}", flush=True)
        interval = nonnegative_int_env("LIBRARY_INDEXER_INTERVAL_SECONDS", 300)
        if LIBRARY_INDEXER_STOP.wait(max(1, interval)):
            return


def library_index_batch_size() -> int:
    return max(1, nonnegative_int_env("LIBRARY_INDEX_BATCH_SIZE", 300))


def library_reindex_batch_size() -> int:
    return max(1, nonnegative_int_env("LIBRARY_REINDEX_BATCH_SIZE", 5000))


def scan_library_index_batch(*, max_paths: int, reset: bool = False) -> dict[str, Any]:
    if reset:
        db.clear_library_index()
    db.set_library_scan_state("library.indexing", "1")
    cursor = "" if reset else db.get_library_scan_state("library.cursor", "")
    processed = 0
    indexed = 0
    last_path = cursor
    reached_end = True
    favorites = db.favorite_paths()

    for path in iter_library_scan_paths(cursor=cursor):
        if LIBRARY_INDEXER_STOP.is_set():
            reached_end = False
            break
        relative = relative_data_path(path)
        processed += 1
        last_path = relative
        if index_library_path(path, favorites):
            indexed += 1
        if processed >= max_paths:
            reached_end = False
            break

    db.set_library_scan_state("library.cursor", "" if reached_end else last_path)
    db.set_library_scan_state("library.indexing", "0")
    db.set_library_scan_state("library.last_scan_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return {
        "processed": processed,
        "indexed": indexed,
        "cursor": "" if reached_end else last_path,
        "complete": reached_end,
    }


def iter_library_scan_paths(*, cursor: str):
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    cursor_text = cursor.strip("/")
    try:
        for root, dirs, files in os.walk(DATA_ROOT):
            dirs[:] = sorted([name for name in dirs if not name.startswith(".")])
            names = dirs + sorted(files)
            for name in names:
                path = Path(root) / name
                try:
                    if should_skip_index_path(path):
                        continue
                    relative = relative_data_path(path)
                except (OSError, ValueError):
                    continue
                if cursor_text and relative <= cursor_text:
                    continue
                yield path
    except OSError:
        return


def index_library_path(path: Path, favorites: set[str]) -> bool:
    try:
        if path.is_dir():
            if not should_index_directory(path):
                return False
        elif path.is_file():
            if not is_library_file(path) or has_indexed_library_ancestor(path):
                return False
        else:
            return False
        item = library_item_for_path(path, favorites)
        stat = path.stat()
    except (OSError, ValueError):
        try:
            db.mark_library_item_stale(relative_data_path(path))
        except Exception:
            pass
        return False
    db.upsert_library_item(
        str(item.get("target_path") or relative_data_path(path)),
        kind=str(item.get("kind") or ("folder" if path.is_dir() else "file")),
        name=str(item.get("filename") or path.name),
        target_dir=str(path),
        payload=item,
        size_bytes=int(item.get("progress_bytes") or 0),
        mtime_ns=stat.st_mtime_ns,
        ctime_ns=stat.st_ctime_ns,
    )
    return True


def has_indexed_library_ancestor(path: Path) -> bool:
    current = path.parent
    root = DATA_ROOT.resolve(strict=False)
    while True:
        try:
            resolved = current.resolve(strict=False)
        except OSError:
            return True
        if resolved == root:
            return False
        if root not in resolved.parents:
            return True
        if should_index_directory(current):
            return True
        current = current.parent


def iter_data_paths(*, max_items: int) -> list[Path]:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    try:
        iterator = DATA_ROOT.rglob("*")
        for path in iterator:
            if len(paths) >= max_items:
                break
            if should_skip_index_path(path):
                continue
            paths.append(path)
    except OSError:
        return paths
    return paths


def should_skip_index_path(path: Path) -> bool:
    name = path.name
    if name.startswith(".") or name.endswith(".part"):
        return True
    try:
        if path.is_symlink():
            return True
    except OSError:
        return True
    return False


def should_index_directory(path: Path) -> bool:
    if lexical_absolute(path) == lexical_absolute(DATA_ROOT):
        return False
    if archive_metadata_path(path) is not None:
        return has_library_content_in_directory(path)
    return any(is_media_file(child) for child in direct_files(path))


def has_library_content_in_directory(path: Path, limit: int = 10000) -> bool:
    try:
        for index, item in enumerate(path.rglob("*")):
            if index >= limit:
                return False
            if item.is_file() and not item.is_symlink() and is_library_file(item):
                return True
    except OSError:
        return False
    return False


def direct_files(path: Path) -> list[Path]:
    try:
        return [item for item in path.iterdir() if item.is_file() and not item.is_symlink()]
    except OSError:
        return []


def is_library_file(path: Path) -> bool:
    if path.name in SIDECAR_FILENAMES:
        return False
    return is_model_file(path) or is_media_file(path) or is_workflow_file(path)


def is_model_file(path: Path) -> bool:
    return path.suffix.lower() in MODEL_EXTENSIONS


def is_workflow_file(path: Path) -> bool:
    return path.suffix.lower() in {".json", ".png"} and "workflow" in path.name.lower()


def is_media_file(path: Path) -> bool:
    return is_image_file(path) or is_video_file(path)


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_subtitle_file(path: Path) -> bool:
    return path.suffix.lower() in SUBTITLE_EXTENSIONS


def library_item_for_path(path: Path, favorites: set[str]) -> dict[str, Any]:
    metadata = archive_metadata(path)
    media_files = media_files_for_path(path, limit=1, max_scan_files=media_file_scan_max_files())
    first_media = media_files[0] if media_files else None
    media_count = media_file_count(path, max_scan_files=media_file_scan_max_files())
    relative_path = relative_data_path(path)
    stat = path.stat()
    size = path_size(path, max_items=library_item_size_scan_max_files())
    source = normalize_library_source(metadata.get("source") or ("media" if first_media else "filesystem"))
    title = library_item_title(path, metadata)
    category = library_item_category(path, metadata, first_media)
    media_type = media_kind(first_media) if first_media else ""
    return {
        "id": f"fs:{stable_path_id(relative_path)}",
        "status": "done",
        "source": source,
        "input_text": str(metadata.get("raw_input") or metadata.get("source_url") or ""),
        "filename": path.name,
        "progress_bytes": size,
        "total_bytes": size,
        "progress_human": human_bytes(size),
        "total_human": human_bytes(size),
        "percent": 100,
        "target_dir": str(path),
        "target_path": relative_path,
        "model_title": title,
        "model_category": category,
        "model_type": str(metadata.get("model_type") or metadata.get("host") or media_type or ("folder" if path.is_dir() else "file")),
        "base_model": str(metadata.get("base_model") or ""),
        "file_format": library_item_format(path, metadata, first_media),
        "precision": library_item_precision(path, metadata, media_count),
        "thumbnail_url": thumbnail_url_for_media(first_media),
        "favorite": relative_path in favorites,
        "source_url": str(metadata.get("source_url") or source_url_from_metadata(metadata)),
        "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
        "created_at": datetime.fromtimestamp(stat.st_ctime, timezone.utc).isoformat(timespec="seconds"),
        "is_filesystem_item": True,
        "has_media": first_media is not None,
        "media_count": media_count,
        "media_type": media_type,
    }


def library_item_title(path: Path, metadata: dict[str, Any]) -> str:
    archive_info = metadata.get("archive_info") if isinstance(metadata.get("archive_info"), dict) else {}
    for value in (
        archive_info.get("model_title"),
        metadata.get("title"),
        metadata.get("model_name"),
        metadata.get("repo_id"),
        metadata.get("gallery_id"),
    ):
        if value:
            return str(value)
    return path.stem if path.is_file() else path.name


def library_item_category(path: Path, metadata: dict[str, Any], first_media: Path | None) -> str:
    archive_info = metadata.get("archive_info") if isinstance(metadata.get("archive_info"), dict) else {}
    for value in (archive_info.get("model_category"), metadata.get("model_category"), metadata.get("source")):
        if value == "hitomi":
            return "Hitomi Gallery"
        if value:
            return str(value)
    if first_media:
        return "Media Gallery" if path.is_dir() else "Media File"
    if is_workflow_file(path):
        return "ComfyUI Workflow"
    return "Local File" if path.is_file() else "Local Folder"


def library_item_format(path: Path, metadata: dict[str, Any], first_media: Path | None) -> str:
    archive_info = metadata.get("archive_info") if isinstance(metadata.get("archive_info"), dict) else {}
    value = archive_info.get("file_format") or metadata.get("file_format")
    if value:
        return str(value)
    if first_media:
        return media_kind(first_media)
    return path.suffix.lower().removeprefix(".") if path.is_file() else "folder"


def library_item_precision(path: Path, metadata: dict[str, Any], media_count: int) -> str:
    archive_info = metadata.get("archive_info") if isinstance(metadata.get("archive_info"), dict) else {}
    value = archive_info.get("precision") or metadata.get("precision")
    if value:
        return str(value)
    page_count = metadata.get("page_count")
    if page_count:
        return f"{page_count} pages"
    if media_count:
        return f"{media_count} media"
    return ""


def archive_metadata_path(path: Path) -> Path | None:
    for name in SIDECAR_FILENAMES:
        candidate = path / name
        if candidate.exists():
            return candidate
    return None


def civitai_image_archive_metadata(path: Path) -> dict[str, Any]:
    metadata_path = archive_named_metadata_path(path, "_civitai_image_metadata.json")
    if metadata_path is None:
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    metadata = dict(payload)
    metadata.setdefault("kind", "civitai_image_page")
    if not metadata.get("source_url"):
        source_url = source_url_from_metadata(metadata)
        if source_url:
            metadata["source_url"] = source_url
    return metadata


def archive_named_metadata_path(path: Path, filename: str) -> Path | None:
    current = path if path.is_dir() else path.parent
    root = DATA_ROOT.resolve()
    while True:
        try:
            resolved = current.resolve()
        except OSError:
            return None
        if resolved != root and root not in resolved.parents:
            return None

        candidate = current / filename
        if candidate.exists():
            return candidate

        if resolved == root:
            return None
        current = current.parent


def archive_metadata(path: Path) -> dict[str, Any]:
    current = path if path.is_dir() else path.parent
    root = DATA_ROOT.resolve()
    while True:
        try:
            resolved = current.resolve()
        except OSError:
            return {}
        if resolved != root and root not in resolved.parents:
            return {}

        metadata_path = archive_metadata_path(current)
        if metadata_path is not None:
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            return payload if isinstance(payload, dict) else {}

        if resolved == root:
            return {}
        current = current.parent


def source_url_from_metadata(metadata: dict[str, Any]) -> str:
    raw_input = metadata.get("raw_input")
    if isinstance(raw_input, str) and is_http_url(raw_input):
        return raw_input
    repo_id = metadata.get("repo_id")
    if isinstance(repo_id, str) and repo_id:
        repo = quote(repo_id, safe="/")
        repo_type = metadata.get("repo_type")
        if repo_type == "dataset":
            return f"https://huggingface.co/datasets/{repo}"
        if repo_type == "space":
            return f"https://huggingface.co/spaces/{repo}"
        return f"https://huggingface.co/{repo}"
    return ""


def requested_model_version_ids(payload: dict[str, Any]) -> list[str]:
    raw_ids = payload.get("model_version_ids", payload.get("modelVersionIds"))
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="model_version_ids 배열이 필요합니다.")
    ids: list[str] = []
    for value in raw_ids:
        text = str(value).strip()
        if text and text not in ids:
            ids.append(text)
    if len(ids) > 100:
        raise HTTPException(status_code=400, detail="한 번에 100개 이하의 리소스만 확인할 수 있습니다.")
    return ids


def civitai_resource_health_payload(model_version_ids: list[str]) -> list[dict[str, Any]]:
    ids = unique_model_version_ids(model_version_ids)
    results = {
        model_version_id: {
            "model_version_id": model_version_id,
            "present": False,
            "target_path": "",
            "job_id": None,
        }
        for model_version_id in ids
    }
    if not ids:
        return []

    mark_civitai_job_health(results)
    missing = {model_version_id for model_version_id, result in results.items() if not result["present"]}
    if missing:
        mark_civitai_sidecar_health(results, missing)
    return [results[model_version_id] for model_version_id in ids]


def unique_model_version_ids(values: list[str]) -> list[str]:
    ids: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in ids:
            ids.append(text)
    return ids


def mark_civitai_job_health(results: dict[str, dict[str, Any]]) -> None:
    wanted = set(results)
    if not wanted:
        return
    for job in db.list_jobs(limit=5000):
        if str(job.get("source") or "") != "civitai" or str(job.get("status") or "") != "done":
            continue
        target_dir = str(job.get("target_dir") or "").strip()
        if not target_dir:
            continue
        target = Path(target_dir)
        if not directory_has_model_file(target):
            continue
        matching_ids = civitai_model_version_ids_from_job(job) & wanted
        if not matching_ids:
            continue
        for model_version_id in matching_ids:
            if results[model_version_id]["present"]:
                continue
            results[model_version_id].update(
                {
                    "present": True,
                    "target_path": health_target_path(target),
                    "job_id": int(job["id"]) if job.get("id") is not None else None,
                }
            )


def mark_civitai_sidecar_health(results: dict[str, dict[str, Any]], wanted: set[str]) -> None:
    try:
        sidecars = DATA_ROOT.rglob("_civitai_metadata.json")
        for sidecar in sidecars:
            if not wanted:
                return
            try:
                if not sidecar.is_file() or sidecar.is_symlink():
                    continue
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            target = sidecar.parent
            if not directory_has_model_file(target):
                continue
            matching_ids = civitai_model_version_ids_from_metadata(payload, include_top_id=False) & wanted
            for model_version_id in matching_ids:
                results[model_version_id].update(
                    {
                        "present": True,
                        "target_path": health_target_path(target),
                        "job_id": None,
                    }
                )
                wanted.discard(model_version_id)
    except OSError:
        return


def civitai_model_version_ids_from_job(job: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    parsed = parse_job_for_display(job)
    if parsed and parsed.source == "civitai" and parsed.civitai_version_id:
        ids.add(str(parsed.civitai_version_id))
    metadata = parse_json_object(job.get("metadata_json"))
    ids.update(civitai_model_version_ids_from_metadata(metadata, include_top_id=True))
    return ids


def civitai_model_version_ids_from_metadata(metadata: dict[str, Any], *, include_top_id: bool) -> set[str]:
    ids: set[str] = set()

    def add(value: Any) -> None:
        if value is None:
            return
        text = str(value).strip()
        if text:
            ids.add(text)

    for key in ("version_id", "model_version_id", "modelVersionId"):
        add(metadata.get(key))
    if include_top_id:
        add(metadata.get("id"))

    nested = metadata.get("metadata")
    if isinstance(nested, dict):
        for key in ("id", "model_version_id", "modelVersionId"):
            add(nested.get(key))

    return ids


def parse_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def directory_has_model_file(path: Path, limit: int = 10000) -> bool:
    try:
        if path.is_file():
            return is_model_file(path)
        if not path.is_dir():
            return False
        for index, item in enumerate(path.rglob("*")):
            if index >= limit:
                return False
            if item.is_file() and not item.is_symlink() and is_model_file(item):
                return True
    except OSError:
        return False
    return False


def health_target_path(path: Path) -> str:
    try:
        return relative_data_path(path)
    except (OSError, ValueError):
        return str(path)


def normalize_library_source(value: Any) -> str:
    source = str(value or "").strip().lower().replace("_", "-")
    if source in {"gallery-dl", "gallerydl"}:
        return "gallerydl"
    if source in {"huggingface", "civitai", "generic", "comfyui", "hitomi"}:
        return source
    return str(value or "").strip() or "filesystem"


def stable_path_id(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8", errors="replace")).hexdigest()[:16]


def media_file_scan_max_files() -> int:
    return nonnegative_int_env("MEDIA_FILE_SCAN_MAX_FILES", MEDIA_FILE_SCAN_MAX_FILES_DEFAULT)


def library_item_size_scan_max_files() -> int:
    return nonnegative_int_env("LIBRARY_ITEM_SIZE_SCAN_MAX_FILES", LIBRARY_ITEM_SIZE_SCAN_MAX_FILES_DEFAULT)


def media_files_for_path(path: Path, limit: int = 500, *, max_scan_files: int = 0) -> list[Path]:
    if path.is_file():
        return [path] if is_media_file(path) else []
    files: list[Path] = []
    scanned = 0
    try:
        for item in path.rglob("*"):
            if max_scan_files > 0 and scanned >= max_scan_files:
                break
            scanned += 1
            if item.is_file() and not item.is_symlink() and is_media_file(item):
                files.append(item)
                if len(files) >= limit and limit <= 1:
                    break
    except OSError:
        return files
    return sorted(files, key=natural_path_key)[:limit]


def media_file_count(path: Path, limit: int = 10000, *, max_scan_files: int = 0) -> int:
    if path.is_file():
        return 1 if is_media_file(path) else 0
    count = 0
    scanned = 0
    try:
        for item in path.rglob("*"):
            if max_scan_files > 0 and scanned >= max_scan_files:
                return count
            scanned += 1
            if item.is_file() and not item.is_symlink() and is_media_file(item):
                count += 1
                if count >= limit:
                    return count
    except OSError:
        return count
    return count


def natural_path_key(path: Path) -> list[Any]:
    relative = relative_data_path(path)
    parts = re_split_digits(relative.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def re_split_digits(value: str) -> list[str]:
    import re

    return re.split(r"(\d+)", value)


def media_item_payload(path: Path, index: int) -> dict[str, Any]:
    relative_path = relative_data_path(path)
    media_type = media_kind(path)
    thumbnail_url = thumbnail_url_for_media(path)
    file_url = f"/api/media/file?path={quote(relative_path, safe='/')}"
    ready_playable = browser_playable_video_path_if_ready(path) if media_type == "video" else None
    play_url = media_play_url_for_ready_source(path, ready_playable) if ready_playable is not None else file_url
    if media_type == "video" and ready_playable is None:
        play_url = ""
    return {
        "index": index,
        "path": relative_path,
        "name": path.name,
        "type": media_type,
        "url": play_url,
        "original_url": file_url,
        "thumbnail_url": thumbnail_url,
        "poster_url": thumbnail_url if media_type == "video" else "",
        "play_ready": media_type != "video" or ready_playable is not None,
        "play_job_required": media_type == "video" and ready_playable is None,
        "poster_ready": media_type != "video" or bool(thumbnail_url),
        "poster_job_required": media_type == "video" and not thumbnail_url,
        "mime_type": "video/mp4" if media_type == "video" else media_type_for_path(path),
        "subtitles": subtitle_payloads_for_media(path) if media_type == "video" else [],
        "size_bytes": path.stat().st_size,
    }


def subtitle_payloads_for_media(path: Path) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for index, subtitle in enumerate(subtitle_files_for_media(path)):
        relative_path = relative_data_path(subtitle)
        language = subtitle_language_for_media(subtitle, path)
        payloads.append(
            {
                "index": index,
                "path": relative_path,
                "name": subtitle.name,
                "url": f"/api/media/subtitle?path={quote(relative_path, safe='/')}",
                "language": language,
                "label": subtitle_label(language, subtitle),
                "format": subtitle.suffix.lower().removeprefix("."),
            }
        )
    return payloads


def subtitle_files_for_media(path: Path) -> list[Path]:
    if not path.is_file():
        return []
    media_stem = path.stem
    try:
        candidates = [
            item
            for item in path.parent.iterdir()
            if item.is_file()
            and not item.is_symlink()
            and is_subtitle_file(item)
            and (item.stem == media_stem or item.stem.startswith(f"{media_stem}."))
        ]
    except OSError:
        return []
    return sorted(candidates, key=lambda item: subtitle_sort_key(item, path))


def subtitle_sort_key(path: Path, media_path: Path) -> tuple[int, list[Any], str]:
    language = subtitle_language_for_media(path, media_path).lower()
    preferred = {"ko": 0, "en": 1}.get(language.split("-", 1)[0], 2)
    return preferred, natural_path_key(path), path.name.lower()


def subtitle_language_for_media(path: Path, media_path: Path) -> str:
    prefix = f"{media_path.stem}."
    stem = path.stem
    if not stem.startswith(prefix):
        return ""
    language = stem.removeprefix(prefix).split(".", 1)[0].strip()
    return language.lower()


def subtitle_label(language: str, path: Path) -> str:
    if language:
        base = SUBTITLE_LANGUAGE_LABELS.get(language.split("-", 1)[0], language)
        return f"{base} ({language})" if base == language else base
    return path.stem


def srt_to_vtt(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    output = ["WEBVTT", ""]
    for index, line in enumerate(lines):
        stripped = line.strip()
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if stripped.isdigit() and "-->" in next_line:
            continue
        if "-->" in line:
            line = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", line)
        output.append(line)
    return "\n".join(output).strip() + "\n"


def media_kind(path: Path | None) -> str:
    if path is None:
        return ""
    if is_image_file(path):
        return "image"
    if is_video_file(path):
        return "video"
    return "file"


def thumbnail_url_for_media(path: Path | None) -> str:
    if path is None:
        return ""
    relative_path = relative_data_path(path)
    if is_image_file(path):
        return f"/api/media/file?path={quote(relative_path, safe='/')}"
    if is_video_file(path) and video_poster_path_if_ready(path) is not None:
        return f"/api/media/poster?path={quote(relative_path, safe='/')}"
    return ""


def media_type_for_path(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0]
    if guessed:
        return guessed
    if is_image_file(path):
        return "image/jpeg"
    if is_video_file(path):
        return "video/mp4"
    return "application/octet-stream"


def existing_video_path(path: str) -> Path:
    source = existing_data_path(path)
    if not source.is_file() or not is_video_file(source):
        raise HTTPException(status_code=404, detail="동영상 파일을 찾지 못했습니다.")
    return source


def require_internal_job_kind(job_id: int, job_kind: str) -> dict[str, Any]:
    job = require_job(job_id)
    if db.normalized_job_kind(job.get("job_kind")) != job_kind:
        raise HTTPException(status_code=404, detail="job not found")
    return job


def browser_playable_video_path_if_ready(source: Path) -> Path | None:
    if is_browser_mp4_video(source):
        return source
    target = media_transcode_target(source)
    if target.exists() and target.stat().st_size > 0:
        return target
    return None


def media_play_url_for_ready_source(source: Path, ready: Path | None) -> str:
    relative_path = relative_data_path(source)
    if ready is not None and ready.resolve(strict=False) == source.resolve(strict=False):
        return f"/api/media/file?path={quote(relative_path, safe='/')}"
    return f"/api/media/play?path={quote(relative_path, safe='/')}"


def browser_playable_video_path(source: Path) -> Path:
    if is_browser_mp4_video(source):
        return source
    return transcode_video_for_browser(source)


def is_browser_mp4_video(source: Path) -> bool:
    if source.suffix.lower() not in BROWSER_MP4_EXTENSIONS:
        return False
    streams = ffprobe_streams(source)
    if not streams:
        return False
    video_codecs = [stream.get("codec_name", "") for stream in streams if stream.get("codec_type") == "video"]
    audio_codecs = [stream.get("codec_name", "") for stream in streams if stream.get("codec_type") == "audio"]
    if not video_codecs or video_codecs[0] not in BROWSER_MP4_VIDEO_CODECS:
        return False
    return all(codec in BROWSER_MP4_AUDIO_CODECS for codec in audio_codecs)


def ffprobe_streams(source: Path) -> list[dict[str, Any]]:
    if not shutil.which("ffprobe"):
        return []
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name",
        "-of",
        "json",
        str(source),
    ]
    try:
        completed = subprocess.run(command, check=True, timeout=15, capture_output=True, text=True)
        data = json.loads(completed.stdout or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    streams = data.get("streams", [])
    return streams if isinstance(streams, list) else []


def run_media_transcode_job(job_id: int, job: dict[str, Any]) -> None:
    payload = db.parse_internal_job_payload(job)
    source = existing_video_path(str(payload.get("path") or ""))
    ready = browser_playable_video_path_if_ready(source)
    if ready is None:
        db.update_job(job_id, progress_bytes=0, total_bytes=source.stat().st_size)
        try:
            ready = transcode_video_for_browser(source, job_id=job_id)
        except HTTPException as exc:
            raise RuntimeError(str(exc.detail)) from exc
    artifact_url = media_play_url_for_ready_source(source, ready)
    ttl_seconds = nonnegative_int_env("MEDIA_CACHE_TTL_SECONDS", MEDIA_CACHE_TTL_DEFAULT_SECONDS)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    db.update_job(
        job_id,
        artifact_path=str(ready),
        artifact_url=artifact_url,
        artifact_expires_at=expires_at.isoformat(timespec="seconds"),
        filename=ready.name,
        progress_bytes=source.stat().st_size,
        total_bytes=source.stat().st_size,
    )
    db.add_job_content_ref(job_id, path=source, role="media_source")
    db.add_job_artifact(
        job_id,
        kind="play",
        path=ready,
        url=artifact_url,
        expires_at=expires_at.isoformat(timespec="seconds"),
    )


def run_media_poster_job(job_id: int, job: dict[str, Any]) -> None:
    payload = db.parse_internal_job_payload(job)
    source = existing_video_path(str(payload.get("path") or ""))
    poster = video_poster_path_if_ready(source)
    if poster is None:
        db.update_job(job_id, progress_bytes=0, total_bytes=1)
        try:
            poster = video_poster_path(source, job_id=job_id)
        except HTTPException as exc:
            raise RuntimeError(str(exc.detail)) from exc
    ttl_seconds = nonnegative_int_env("MEDIA_CACHE_TTL_SECONDS", MEDIA_CACHE_TTL_DEFAULT_SECONDS)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    db.update_job(
        job_id,
        artifact_path=str(poster),
        artifact_url=f"/api/media/poster?path={quote(relative_data_path(source), safe='/')}",
        artifact_expires_at=expires_at.isoformat(timespec="seconds"),
        filename=poster.name,
        progress_bytes=1,
        total_bytes=1,
    )
    poster_url = f"/api/media/poster?path={quote(relative_data_path(source), safe='/')}"
    db.add_job_content_ref(job_id, path=source, role="media_source")
    db.add_job_artifact(
        job_id,
        kind="poster",
        path=poster,
        url=poster_url,
        expires_at=expires_at.isoformat(timespec="seconds"),
    )


def transcode_video_for_browser(source: Path, *, job_id: int | None = None) -> Path:
    if not shutil.which("ffmpeg"):
        raise HTTPException(status_code=500, detail="ffmpeg가 없어 동영상을 브라우저용 MP4로 변환할 수 없습니다.")

    target = media_transcode_target(source)
    if target.exists() and target.stat().st_size > 0:
        return target

    key = media_cache_key(source)
    lock = media_transcode_lock(key)
    with lock:
        if target.exists() and target.stat().st_size > 0:
            return target
        MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        temp = MEDIA_CACHE_DIR / f".{key}.play.tmp.mp4"
        cleanup_file(temp)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            os.getenv("MEDIA_TRANSCODE_PRESET", "veryfast"),
            "-crf",
            os.getenv("MEDIA_TRANSCODE_CRF", "23"),
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            os.getenv("MEDIA_TRANSCODE_AUDIO_BITRATE", "160k"),
            "-movflags",
            "+faststart",
            "-y",
            str(temp),
        ]
        timeout = media_transcode_timeout_seconds()
        try:
            if job_id is not None:
                db.update_job(job_id, progress_bytes=0, total_bytes=source.stat().st_size)
                internal_jobs.check_job_control(job_id)
            with media_transcode_semaphore():
                subprocess.run(command, check=True, timeout=timeout or None, capture_output=True)
            os.replace(temp, target)
            if job_id is not None:
                db.update_job(job_id, progress_bytes=source.stat().st_size, total_bytes=source.stat().st_size)
        except subprocess.CalledProcessError as exc:
            cleanup_file(temp)
            detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
            message = f"동영상을 브라우저용 MP4로 변환하지 못했습니다: {detail or exc}"
            raise HTTPException(status_code=500, detail=message[:1000]) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            cleanup_file(temp)
            raise HTTPException(status_code=500, detail=f"동영상을 브라우저용 MP4로 변환하지 못했습니다: {exc}") from exc
    return target


def media_transcode_target(source: Path) -> Path:
    return MEDIA_CACHE_DIR / f"{media_cache_key(source)}.play.mp4"


def media_cache_key(source: Path) -> str:
    stat = source.stat()
    return hashlib.sha256(f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")).hexdigest()[:24]


def media_transcode_lock(key: str) -> threading.Lock:
    with MEDIA_TRANSCODE_LOCKS_LOCK:
        lock = MEDIA_TRANSCODE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            MEDIA_TRANSCODE_LOCKS[key] = lock
        return lock


def media_transcode_timeout_seconds() -> int:
    try:
        return max(0, int(os.getenv("MEDIA_TRANSCODE_TIMEOUT_SECONDS", "1800")))
    except ValueError:
        return 1800


def media_transcode_semaphore() -> threading.BoundedSemaphore:
    global MEDIA_TRANSCODE_SEMAPHORE
    with MEDIA_TRANSCODE_SEMAPHORE_LOCK:
        if MEDIA_TRANSCODE_SEMAPHORE is None:
            limit = max(
                1,
                nonnegative_int_env("MEDIA_TRANSCODE_MAX_CONCURRENT", MEDIA_TRANSCODE_MAX_CONCURRENT_DEFAULT),
            )
            MEDIA_TRANSCODE_SEMAPHORE = threading.BoundedSemaphore(limit)
        return MEDIA_TRANSCODE_SEMAPHORE


def video_poster_path_if_ready(source: Path) -> Path | None:
    poster = media_poster_target(source)
    if poster.exists() and poster.stat().st_size > 0:
        return poster
    return None


def video_poster_path(source: Path, *, job_id: int | None = None) -> Path:
    MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    poster = media_poster_target(source)
    if poster.exists() and poster.stat().st_size > 0:
        return poster
    key = media_cache_key(source)
    temp = MEDIA_CACHE_DIR / f".{key}.tmp.jpg"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        "1",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        "scale=480:-2",
        "-q:v",
        "4",
        "-y",
        str(temp),
    ]
    try:
        if job_id is not None:
            db.update_job(job_id, progress_bytes=0, total_bytes=1)
            internal_jobs.check_job_control(job_id)
        with media_transcode_semaphore():
            subprocess.run(command, check=True, timeout=30, capture_output=True)
        os.replace(temp, poster)
        if job_id is not None:
            db.update_job(job_id, progress_bytes=1, total_bytes=1)
    except (OSError, subprocess.SubprocessError) as exc:
        cleanup_file(temp)
        raise HTTPException(status_code=500, detail=f"동영상 썸네일을 생성하지 못했습니다: {exc}") from exc
    return poster


def media_poster_target(source: Path) -> Path:
    return MEDIA_CACHE_DIR / f"{media_cache_key(source)}.jpg"


def safe_media_artifact_file(path: Path) -> bool:
    return safe_cache_file(MEDIA_CACHE_DIR, path) or safe_data_artifact_file(path)


def safe_data_artifact_file(path: Path) -> bool:
    try:
        root = DATA_ROOT.resolve(strict=False)
        resolved = path.resolve(strict=False)
    except OSError:
        return False
    return resolved != root and root in resolved.parents and path.is_file() and not path.is_symlink()


def parse_job_for_display(job: dict) -> ParsedDownload | None:
    payload = job.get("parsed_json")
    if not payload:
        return None
    try:
        return ParsedDownload.from_dict(json.loads(str(payload)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def display_target_path(job: dict) -> str:
    value = str(job.get("target_dir") or "").strip()
    if not value:
        return ""
    normalized = value.replace("\\", "/").strip("/")
    data_root = DATA_ROOT.as_posix().strip("/")
    if normalized == data_root:
        return ""
    data_prefix = f"{data_root}/"
    if normalized.startswith(data_prefix):
        return normalized[len(data_prefix) :].strip("/")
    return normalized.removeprefix("data/").strip("/")


def source_url_for_job(job: dict, parsed: ParsedDownload | None) -> str:
    raw_input = str(job.get("input_text") or "").strip()
    if is_http_url(raw_input):
        return raw_input
    if not parsed:
        return ""
    if parsed.source == "huggingface" and parsed.repo_id:
        repo = quote(parsed.repo_id, safe="/")
        if parsed.repo_type == "dataset":
            return f"https://huggingface.co/datasets/{repo}"
        if parsed.repo_type == "space":
            return f"https://huggingface.co/spaces/{repo}"
        return f"https://huggingface.co/{repo}"
    if parsed.source == "civitai":
        if parsed.civitai_image_url and is_http_url(parsed.civitai_image_url):
            return parsed.civitai_image_url
        if parsed.civitai_image_id:
            return f"https://civitai.com/images/{quote(parsed.civitai_image_id)}"
        if parsed.civitai_model_id:
            return f"https://civitai.com/models/{quote(parsed.civitai_model_id)}"
        if is_http_url(parsed.civitai_download_url or ""):
            return parsed.civitai_download_url or ""
    if parsed.source == "generic" and is_http_url(parsed.url or ""):
        return parsed.url or ""
    if parsed.source == "comfyui" and is_http_url(parsed.comfyui_workflow_url or ""):
        return parsed.comfyui_workflow_url or ""
    if parsed.source == "hitomi":
        if is_http_url(parsed.hitomi_listing_url or ""):
            return parsed.hitomi_listing_url or ""
        if is_http_url(parsed.hitomi_gallery_url or ""):
            return parsed.hitomi_gallery_url or ""
        if parsed.hitomi_gallery_id:
            return f"https://hitomi.la/galleries/{quote(parsed.hitomi_gallery_id)}.html"
    if parsed.source == "gallerydl":
        gallery_url = parsed.gallerydl_url or ""
        if gallery_url.startswith("ytdl:"):
            gallery_url = gallery_url.split(":", 1)[1]
        if is_http_url(gallery_url):
            return gallery_url
    return ""


def is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def normalize_int_setting(value: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> str:
    try:
        parsed = max(minimum, int(str(value).strip()))
    except ValueError:
        parsed = default
    if maximum is not None and maximum >= minimum:
        parsed = min(parsed, maximum)
    return str(parsed)


def nonnegative_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return max(0, int(raw_value))
    except ValueError:
        return default


def storage_usage_scan_sleep_seconds() -> float:
    raw_value = os.getenv("STORAGE_USAGE_SCAN_SLEEP_SECONDS")
    if raw_value is None:
        return 0.02
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return 0.02


def bool_env(name: str, *, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_bool_setting(value: str | None, *, default: bool = False) -> str:
    if value is None:
        return "1" if default else "0"
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "on", "yes", "y"}:
        return "1"
    if normalized in {"0", "false", "off", "no", "n", ""}:
        return "0"
    return "1" if default else "0"


def write_startup_config(values: dict[str, str]) -> None:
    gallery_dl_auto_update = normalize_bool_setting(values.get("GALLERY_DL_AUTO_UPDATE"), default=True)
    STARTUP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".startup.", dir=STARTUP_CONFIG_PATH.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"GALLERY_DL_AUTO_UPDATE={gallery_dl_auto_update}\n")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, STARTUP_CONFIG_PATH)
    except Exception:
        cleanup_file(temp_path)
        raise


def build_folder_tree(root: Path, max_depth: int = 4, max_entries: int = 300) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    remaining = {"count": max_entries}

    def walk(path: Path, depth: int) -> dict[str, Any]:
        node = {
            "name": path.name or str(path),
            "path": "" if path == root else path.relative_to(root).as_posix(),
            "children": [],
        }
        if depth >= max_depth or remaining["count"] <= 0:
            return node

        children: list[dict[str, Any]] = []
        try:
            folders = sorted([item for item in path.iterdir() if item.is_dir()], key=lambda item: item.name.lower())
        except OSError:
            folders = []
        for child in folders:
            if remaining["count"] <= 0:
                break
            remaining["count"] -= 1
            children.append(walk(child, depth + 1))
        node["children"] = children
        return node

    return walk(root, 0)


def ensure_route_folders() -> None:
    for key, route_path in db.library_route_settings().items():
        if key == "LIBRARY_ACTIVE":
            continue
        safe_join(DATA_ROOT, route_path).mkdir(parents=True, exist_ok=True)


def existing_data_path(path: str) -> Path:
    target = data_path_from_request_path(path)
    if target.exists():
        return target

    decoded_path = unquote(path)
    if decoded_path != path:
        target = data_path_from_request_path(decoded_path)
        if target.exists():
            return target

    raise HTTPException(status_code=404, detail="대상 경로를 찾을 수 없습니다.")


def data_path_from_request_path(path: str) -> Path:
    try:
        target = raw_data_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="데이터 루트 밖의 경로는 사용할 수 없습니다.") from exc
    return target


def raw_data_path(path: str) -> Path:
    root = DATA_ROOT.resolve(strict=False)
    current = DATA_ROOT
    for segment in str(path or "").strip().replace("\\", "/").lstrip("/").split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            raise ValueError("Target path escapes data root")
        current = current / segment
    resolved = current.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ValueError("Target path escapes data root")
    return current


def ensure_mutable_path(path: Path) -> None:
    if lexical_absolute(path) == lexical_absolute(DATA_ROOT):
        raise HTTPException(status_code=400, detail="/data 루트는 이름변경, 이동, 삭제할 수 없습니다.")
    if has_symlink_ancestor(path):
        raise HTTPException(status_code=400, detail="symlink 폴더 내부 경로는 직접 변경할 수 없습니다.")


def ensure_real_directory_destination(path: Path) -> None:
    if path.is_symlink() or has_symlink_ancestor(path):
        raise HTTPException(status_code=400, detail="symlink 폴더는 이동 대상으로 사용할 수 없습니다.")


def ensure_downloadable_path(path: Path) -> None:
    if path.resolve() == DATA_ROOT.resolve():
        raise HTTPException(status_code=400, detail="/data 전체 다운로드는 지원하지 않습니다. 모델 폴더를 선택하세요.")


def ensure_no_active_jobs(path: Path) -> None:
    if db.has_active_jobs_under(path):
        raise HTTPException(status_code=409, detail="실행 중이거나 대기 중인 다운로드가 있는 폴더입니다.")


def clean_item_name(name: str) -> str:
    raw_name = name.strip()
    if not raw_name or raw_name in {".", ".."} or "/" in raw_name or "\\" in raw_name:
        raise HTTPException(status_code=400, detail="올바른 이름을 입력하세요.")
    cleaned = sanitize_segment(raw_name, "item")
    if not cleaned:
        raise HTTPException(status_code=400, detail="올바른 이름을 입력하세요.")
    return cleaned


def relative_data_path(path: Path) -> str:
    root_resolved = DATA_ROOT.resolve()
    root_lexical = lexical_absolute(DATA_ROOT)
    path_lexical = lexical_absolute(path)
    if path_lexical == root_lexical:
        return ""

    resolved = path.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError("Target path escapes data root")

    try:
        relative = path_lexical.relative_to(root_lexical)
    except ValueError:
        relative = resolved.relative_to(root_resolved)
    return relative.as_posix()


def lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def has_symlink_ancestor(path: Path) -> bool:
    relative = relative_data_path(path)
    if not relative:
        return False
    current = DATA_ROOT
    for part in Path(relative).parts[:-1]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def download_filename(path: Path) -> str:
    name = sanitize_segment(path.name, "download")
    return f"{name}.zip" if path.is_dir() else path.name


def path_size(path: Path, *, max_items: int = 0) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    inspected = 0
    for item in path.rglob("*"):
        if max_items > 0 and inspected >= max_items:
            break
        inspected += 1
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def path_extensions(path: Path, limit: int = 8) -> list[str]:
    if path.is_file():
        return [path.suffix.lower() or "없음"]
    extensions: set[str] = set()
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                extensions.add(item.suffix.lower() or "없음")
        except OSError:
            continue
    values = sorted(extensions)
    if len(values) > limit:
        return values[:limit] + [f"+{len(values) - limit}개"]
    return values


def source_input_values(source: Path) -> list[str]:
    values: list[str] = []

    def append(value: str | None) -> None:
        if value and value not in values:
            values.append(value)

    for value in metadata_input_values(source):
        append(value)

    source_resolved = source.resolve()
    for job in db.list_jobs(limit=500):
        target_dir = job.get("target_dir")
        if not target_dir:
            continue
        try:
            target_resolved = Path(str(target_dir)).resolve()
        except OSError:
            continue
        if paths_overlap(source_resolved, target_resolved):
            append(str(job.get("input_text") or ""))

    return values


def metadata_input_values(source: Path) -> list[str]:
    values: list[str] = []
    root = DATA_ROOT.resolve()
    current = source if source.is_dir() else source.parent
    while True:
        try:
            current_resolved = current.resolve()
        except OSError:
            break
        if current_resolved != root and root not in current_resolved.parents:
            break

        metadata_path = current / "_archive_metadata.json"
        if metadata_path.exists():
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            raw_input = payload.get("raw_input")
            if isinstance(raw_input, str) and raw_input:
                values.append(raw_input)

        if current_resolved == root:
            break
        current = current.parent
    return values


def paths_overlap(first: Path, second: Path) -> bool:
    return (
        first == second
        or first in second.parents
        or second in first.parents
    )


def cleanup_stale_download_archives(now: float | None = None) -> int:
    return cleanup_stale_files(
        DOWNLOAD_ARCHIVE_DIR,
        patterns=("*.zip",),
        ttl_seconds=nonnegative_int_env("DOWNLOAD_ARCHIVE_TTL_SECONDS", DOWNLOAD_ARCHIVE_TTL_DEFAULT_SECONDS),
        now=now,
    )


def cleanup_stale_media_cache(now: float | None = None) -> int:
    ttl_seconds = nonnegative_int_env("MEDIA_CACHE_TTL_SECONDS", MEDIA_CACHE_TTL_DEFAULT_SECONDS)
    max_bytes = nonnegative_int_env("MEDIA_CACHE_MAX_BYTES", MEDIA_CACHE_MAX_BYTES_DEFAULT)
    removed = cleanup_stale_files(
        MEDIA_CACHE_DIR,
        patterns=("*",),
        ttl_seconds=ttl_seconds,
        temp_ttl_seconds=min(ttl_seconds, 24 * 60 * 60) if ttl_seconds > 0 else 24 * 60 * 60,
        now=now,
    )
    if max_bytes > 0:
        removed += cleanup_cache_quota(MEDIA_CACHE_DIR, max_bytes)
    return removed


def cleanup_stale_files(
    root: Path,
    *,
    patterns: tuple[str, ...],
    ttl_seconds: int,
    temp_ttl_seconds: int | None = None,
    now: float | None = None,
) -> int:
    if ttl_seconds <= 0 and not temp_ttl_seconds:
        return 0
    if not root.exists():
        return 0
    current_time = time.time() if now is None else now
    removed = 0
    seen: set[Path] = set()
    for pattern in patterns:
        for path in root.rglob(pattern):
            if path in seen:
                continue
            seen.add(path)
            if not removable_cache_file(path):
                continue
            try:
                age = current_time - path.stat().st_mtime
            except OSError:
                continue
            is_temp = path.name.startswith(".") or ".tmp" in path.name
            if is_temp and temp_ttl_seconds is not None and age >= temp_ttl_seconds:
                cleanup_file(path)
                removed += 1
            elif ttl_seconds > 0 and age >= ttl_seconds:
                cleanup_file(path)
                removed += 1
    return removed


def cleanup_cache_quota(root: Path, max_bytes: int) -> int:
    if max_bytes <= 0 or not root.exists():
        return 0
    files: list[tuple[float, int, Path]] = []
    total = 0
    for path in root.rglob("*"):
        if not removable_cache_file(path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        total += stat.st_size
        files.append((stat.st_mtime, stat.st_size, path))
    if total <= max_bytes:
        return 0
    removed = 0
    for _mtime, size, path in sorted(files):
        cleanup_file(path)
        total -= size
        removed += 1
        if total <= max_bytes:
            break
    return removed


def download_archive_semaphore() -> threading.BoundedSemaphore:
    global DOWNLOAD_ARCHIVE_SEMAPHORE
    with DOWNLOAD_ARCHIVE_SEMAPHORE_LOCK:
        if DOWNLOAD_ARCHIVE_SEMAPHORE is None:
            limit = max(
                1,
                nonnegative_int_env("DOWNLOAD_ARCHIVE_MAX_CONCURRENT", DOWNLOAD_ARCHIVE_MAX_CONCURRENT_DEFAULT),
            )
            DOWNLOAD_ARCHIVE_SEMAPHORE = threading.BoundedSemaphore(limit)
        return DOWNLOAD_ARCHIVE_SEMAPHORE


def require_archive_job(job_id: int) -> dict[str, Any]:
    job = require_job(job_id)
    if db.normalized_job_kind(job.get("job_kind")) != INTERNAL_JOB_ARCHIVE_ZIP:
        raise HTTPException(status_code=404, detail="download job not found")
    return job


def run_archive_zip_job(job_id: int, job: dict[str, Any]) -> None:
    payload = db.parse_internal_job_payload(job)
    source = existing_data_path(str(payload.get("path") or ""))
    ensure_downloadable_path(source)
    preflight = preflight_archive_job(source)
    db.update_job(
        job_id,
        target_dir=str(source),
        filename=download_filename(source),
        progress_bytes=0,
        total_bytes=int(preflight["source_bytes"]),
        metadata_json=json.dumps({"archive_preflight": preflight}, ensure_ascii=False),
    )
    archive_path = create_zip_archive(source, job_id=job_id)
    ttl_seconds = nonnegative_int_env("DOWNLOAD_ARCHIVE_TTL_SECONDS", DOWNLOAD_ARCHIVE_TTL_DEFAULT_SECONDS)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    db.update_job(
        job_id,
        artifact_path=str(archive_path),
        artifact_url=f"/api/fs/download-jobs/{job_id}/file",
        artifact_expires_at=expires_at.isoformat(timespec="seconds"),
        progress_bytes=int(preflight["source_bytes"]),
        total_bytes=int(preflight["source_bytes"]),
    )
    db.add_job_content_ref(job_id, path=source, role="archive_source")
    db.add_job_artifact(
        job_id,
        kind="zip",
        path=archive_path,
        url=f"/api/fs/download-jobs/{job_id}/file",
        expires_at=expires_at.isoformat(timespec="seconds"),
    )


def preflight_archive_job(source: Path) -> dict[str, Any]:
    ensure_safe_archive_source(source)
    max_files = nonnegative_int_env("DOWNLOAD_ARCHIVE_MAX_FILES", DOWNLOAD_ARCHIVE_MAX_FILES_DEFAULT)
    max_bytes = nonnegative_int_env("DOWNLOAD_ARCHIVE_MAX_SOURCE_BYTES", DOWNLOAD_ARCHIVE_MAX_SOURCE_BYTES_DEFAULT)
    min_free = nonnegative_int_env("DOWNLOAD_ARCHIVE_MIN_FREE_BYTES", DOWNLOAD_ARCHIVE_MIN_FREE_BYTES_DEFAULT)
    file_count = 0
    source_bytes = 0

    for item in sorted(source.rglob("*")):
        if item.is_symlink():
            ensure_symlink_stays_in_data_root(item)
            continue
        if not item.is_file():
            continue
        if item.name.endswith(".part"):
            continue
        archive_entry_name(source, item)
        try:
            size = item.stat().st_size
        except OSError:
            continue
        file_count += 1
        source_bytes += size
        if max_files > 0 and file_count > max_files:
            raise ValueError(f"압축할 파일 수가 너무 많습니다. 최대 {max_files}개까지 지원합니다.")
        if max_bytes > 0 and source_bytes > max_bytes:
            raise ValueError(f"압축할 원본 크기가 너무 큽니다. 최대 {human_bytes(max_bytes)}까지 지원합니다.")

    try:
        usage = shutil.disk_usage(DOWNLOAD_ARCHIVE_DIR if DOWNLOAD_ARCHIVE_DIR.exists() else DOWNLOAD_ARCHIVE_DIR.parent)
    except OSError:
        usage = None
    if usage is not None and usage.free < source_bytes + min_free:
        raise OSError(
            f"압축 파일을 만들 여유 공간이 부족합니다. 필요 {human_bytes(source_bytes + min_free)}, 여유 {human_bytes(usage.free)}"
        )

    return {
        "path": relative_data_path(source),
        "file_count": file_count,
        "source_bytes": source_bytes,
        "source_human": human_bytes(source_bytes),
        "max_files": max_files,
        "max_source_bytes": max_bytes,
        "min_free_bytes": min_free,
    }


def ensure_safe_archive_source(source: Path) -> None:
    if not source.exists() or not source.is_dir():
        raise ValueError("압축할 폴더를 찾지 못했습니다.")
    if source.is_symlink() or has_symlink_ancestor(source):
        raise ValueError("symlink 폴더는 압축할 수 없습니다.")
    root = DATA_ROOT.resolve(strict=False)
    resolved = source.resolve(strict=False)
    if resolved == root:
        raise ValueError("/data 전체 다운로드는 지원하지 않습니다. 모델 폴더를 선택하세요.")
    if root not in resolved.parents:
        raise ValueError("데이터 루트 밖의 경로는 압축할 수 없습니다.")


def ensure_symlink_stays_in_data_root(path: Path) -> None:
    root = DATA_ROOT.resolve(strict=False)
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise ValueError(f"symlink 경로를 확인하지 못했습니다: {path}") from exc
    if resolved != root and root not in resolved.parents:
        raise ValueError("데이터 루트 밖으로 나가는 symlink는 압축할 수 없습니다.")


def archive_entry_name(root: Path, file: Path) -> str:
    source_root = root.resolve(strict=False)
    resolved = file.resolve(strict=False)
    if resolved != source_root and source_root not in resolved.parents:
        raise ValueError("데이터 루트 밖의 파일은 압축할 수 없습니다.")
    relative = resolved.relative_to(source_root).as_posix()
    parts = relative.split("/")
    if not relative or relative.startswith("/") or "\\" in relative or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("안전하지 않은 압축 entry 경로입니다.")
    return relative


def safe_cache_file(root: Path, path: Path) -> bool:
    try:
        root_resolved = root.resolve(strict=False)
        resolved = path.resolve(strict=False)
    except OSError:
        return False
    return resolved != root_resolved and root_resolved in resolved.parents and path.is_file() and not path.is_symlink()


def removable_cache_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def create_zip_archive(source: Path, *, job_id: int | None = None) -> Path:
    ensure_safe_archive_source(source)
    DOWNLOAD_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    prefix = f"{sanitize_segment(source.name, 'download')}_"
    fd, temp_name = tempfile.mkstemp(prefix=prefix, suffix=".zip", dir=DOWNLOAD_ARCHIVE_DIR)
    os.close(fd)
    archive_path = Path(temp_name)
    source_root = source.resolve(strict=False)
    written_bytes = 0
    last_progress_at = 0
    try:
        with download_archive_semaphore():
            with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_STORED) as archive:
                for item in sorted(source.rglob("*")):
                    if item.is_symlink():
                        ensure_symlink_stays_in_data_root(item)
                        continue
                    if not item.is_file():
                        continue
                    if item.name.endswith(".part"):
                        continue
                    entry_name = archive_entry_name(source_root, item)
                    resolved = item.resolve(strict=False)
                    archive.write(resolved, entry_name)
                    if job_id is not None:
                        try:
                            written_bytes += item.stat().st_size
                        except OSError:
                            pass
                        if written_bytes - last_progress_at >= 8 * 1024 * 1024:
                            db.update_job(job_id, progress_bytes=written_bytes)
                            internal_jobs.check_job_control(job_id)
                            last_progress_at = written_bytes
                if job_id is not None:
                    db.update_job(job_id, progress_bytes=written_bytes)
    except Exception:
        cleanup_file(archive_path)
        raise
    return archive_path


def create_chrome_extension_archive() -> Path:
    source = CHROME_EXTENSION_DIR
    if not source.exists() or not source.is_dir():
        raise HTTPException(status_code=404, detail="chrome extension not found")
    if not (source / "manifest.json").is_file():
        raise HTTPException(status_code=404, detail="chrome extension manifest not found")

    fd, temp_name = tempfile.mkstemp(prefix="hugcivi-chrome-extension-", suffix=".zip")
    os.close(fd)
    archive_path = Path(temp_name)
    try:
        with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(source.rglob("*")):
                if not file.is_file() or should_skip_addon_file(file):
                    continue
                relative = file.relative_to(source).as_posix()
                archive.write(file, f"hugcivi-chrome-extension/{relative}")
    except Exception:
        cleanup_file(archive_path)
        raise
    return archive_path


def should_skip_addon_file(path: Path) -> bool:
    ignored_parts = {"__pycache__", ".DS_Store", "Thumbs.db"}
    return any(part in ignored_parts for part in path.parts)


def cleanup_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
