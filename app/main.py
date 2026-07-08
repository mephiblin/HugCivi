from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
import fnmatch
import hashlib
import json
import mimetypes
import os
import queue
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
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from . import db, internal_jobs, subscriptions, transfer
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
    civitai_model_details,
    controlled_process_kwargs,
    enqueue_job,
    folder_thumbnail_path,
    load_hitomi_listing_metadata,
    notify_queue_settings_changed,
    process_output_queue,
    queue_hitomi_listing_galleries,
    read_process_output,
    remove_pending_job,
    start_workers,
    stop_controlled_process,
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
    internal_jobs.register_handler(INTERNAL_JOB_MEDIA_THUMBNAIL_BACKFILL, run_media_thumbnail_backfill_job)
    internal_jobs.register_handler(INTERNAL_JOB_TRANSFER_COPY, run_transfer_copy_job)


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
CIVITAI_API_BASE = os.getenv("CIVITAI_API_BASE", "https://civitai.com/api/v1").rstrip("/")
DOWNLOAD_ARCHIVE_DIR = Path(os.getenv("DOWNLOAD_ARCHIVE_DIR", "/config/downloads"))
MEDIA_CACHE_DIR = Path(os.getenv("MEDIA_CACHE_DIR", "/config/media-cache"))
MEDIA_THUMBNAIL_CACHE_DIR = MEDIA_CACHE_DIR / "thumbnails"
CHROME_EXTENSION_DIR = Path(os.getenv("HUGCIVI_CHROME_EXTENSION_DIR", str(BASE_DIR.parent / "chrome-extension")))
STARTUP_CONFIG_PATH = Path(os.getenv("HUGCIVI_STARTUP_CONFIG_FILE", str(db.DB_PATH.parent / "startup.env")))
TRANSFER_MANIFEST_DIR = Path(os.getenv("TRANSFER_MANIFEST_DIR", str(db.DB_PATH.parent / "transfer-manifests")))
DATA_REMOTE_ROOT = transfer.data_remote_dir()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
security = HTTPBasic()
PWA_MANIFEST_PATH = BASE_DIR / "static" / "manifest.webmanifest"
PWA_SERVICE_WORKER_PATH = BASE_DIR / "static" / "sw.js"
FOLDER_TREE_MAX_DEPTH = 4
FOLDER_TREE_MAX_ENTRIES = 5000
FOLDER_TREE_MAX_CHILDREN_PER_FOLDER = 1000
FOLDER_TREE_INITIAL_MAX_DEPTH = 1
FOLDER_CHILDREN_DEFAULT_LIMIT = 200
FOLDER_CHILDREN_MAX_LIMIT = 500
HITOMI_ROUTE_ROOT = "hitomi"
HITOMI_LISTING_CONTAINER = "listings"
HITOMI_ARCHIVE_MARKER_FILENAMES = ("_hitomi_metadata.json",)
INSECURE_PASSWORDS = {"", "change-this-password", "replace-with-a-strong-password"}
IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".weba"}
DOCUMENT_EXTENSIONS = {".markdown", ".md", ".txt"}
SUBTITLE_EXTENSIONS = {".srt", ".vtt"}
DOCUMENT_TEXT_MAX_BYTES = 512 * 1024
YTDLP_INFO_SUFFIX = ".info.json"
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
INTERNAL_JOB_MEDIA_THUMBNAIL_BACKFILL = "media_thumbnail_backfill"
INTERNAL_JOB_TRANSFER_COPY = "transfer_copy"
TARGET_KIND_LOCAL_MOUNT = transfer.TARGET_KIND_LOCAL_MOUNT
TRANSFER_COPY_SEMAPHORE = threading.BoundedSemaphore(transfer.transfer_max_concurrent())
JOB_LIST_PAGE_SIZE = 50
LIBRARY_PAGE_SIZE = 50
LIBRARY_PAGE_MAX_SIZE = 100
LIVE_LIBRARY_PAGE_SCAN_MIN_ITEMS = LIBRARY_PAGE_SIZE * 3
LIVE_LIBRARY_PAGE_COUNT_MAX_PATHS = 20_000
MEDIA_THUMBNAIL_DEFAULT_SIZE = 360
MEDIA_THUMBNAIL_MAX_SIZE = 720
MEDIA_THUMBNAIL_BACKFILL_DEFAULT_WORKERS = 3
MEDIA_THUMBNAIL_BACKFILL_MAX_WORKERS = 16
MEDIA_THUMBNAIL_BACKFILL_DEFAULT_MAX_ITEMS = 5000
MEDIA_THUMBNAIL_BACKFILL_HARD_MAX_ITEMS = 20000
DOWNLOAD_ARCHIVE_MAX_FILES_DEFAULT = 50_000
DOWNLOAD_ARCHIVE_MAX_SOURCE_BYTES_DEFAULT = 0
DOWNLOAD_ARCHIVE_MIN_FREE_BYTES_DEFAULT = 0
BROWSER_MP4_EXTENSIONS = {".m4v", ".mp4"}
BROWSER_MP4_VIDEO_CODECS = {"h264"}
BROWSER_MP4_AUDIO_CODECS = {"aac", "mp3"}
MEDIA_TRANSCODE_LOCKS: dict[str, threading.Lock] = {}
MEDIA_TRANSCODE_LOCKS_LOCK = threading.Lock()
MEDIA_THUMBNAIL_LOCKS: dict[str, threading.Lock] = {}
MEDIA_THUMBNAIL_LOCKS_LOCK = threading.Lock()
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
    "_civitai_generation_metadata.json",
    "_civitai_image_metadata.json",
    "_generic_metadata.json",
    "_hitomi_metadata.json",
    "_asmrone_metadata.json",
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
    job_page = jobs_page_payload(limit=JOB_LIST_PAGE_SIZE, page=1)
    library_page = library_items_page_payload(limit=LIBRARY_PAGE_SIZE, page=1)
    ensure_route_folders()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "jobs": job_page["jobs"],
            "jobs_page": job_page,
            "library_items": library_page["items"],
            "library_page": library_page,
            "folder_tree": initial_folder_tree(),
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
    hf_token: str | None = Form(None),
    civitai_token: str | None = Form(None),
    gallery_dl_username: str | None = Form(None),
    gallery_dl_password: str | None = Form(None),
    gallery_dl_cookies_file: str | None = Form(None),
    gallery_dl_cookies_from_browser: str | None = Form(None),
    gallery_dl_extra_options: str | None = Form(None),
    yt_dlp_cookies_file: str | None = Form(None),
    yt_dlp_cookies_from_browser: str | None = Form(None),
    yt_dlp_proxy: str | None = Form(None),
    yt_dlp_format: str = Form(""),
    yt_dlp_extra_options: str | None = Form(None),
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
    save_optional_setting("HF_TOKEN", hf_token)
    save_optional_setting("CIVITAI_TOKEN", civitai_token)

    gallery_dl_fields = {
        "GALLERY_DL_USERNAME": gallery_dl_username,
        "GALLERY_DL_PASSWORD": gallery_dl_password,
        "GALLERY_DL_COOKIES_FILE": gallery_dl_cookies_file,
        "GALLERY_DL_COOKIES_FROM_BROWSER": gallery_dl_cookies_from_browser,
        "GALLERY_DL_EXTRA_OPTIONS": gallery_dl_extra_options,
    }
    for key, value in gallery_dl_fields.items():
        save_optional_setting(key, value)

    yt_dlp_fields = {
        "YT_DLP_COOKIES_FILE": yt_dlp_cookies_file,
        "YT_DLP_COOKIES_FROM_BROWSER": yt_dlp_cookies_from_browser,
        "YT_DLP_PROXY": yt_dlp_proxy,
        "YT_DLP_EXTRA_OPTIONS": yt_dlp_extra_options,
    }
    for key, value in yt_dlp_fields.items():
        save_optional_setting(key, value)
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


def save_optional_setting(key: str, value: str | None) -> None:
    if value is None:
        return
    stripped = value.strip()
    if stripped:
        db.set_setting(key, stripped)
    else:
        db.delete_setting(key)


@app.post("/folders")
def create_folder(folder_path: str = Form(...), _: str = Depends(require_auth)) -> RedirectResponse:
    folder = safe_join(DATA_ROOT, folder_path.strip())
    folder.mkdir(parents=True, exist_ok=True)
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/jobs")
def api_jobs(
    limit: int = 100,
    cursor: int | None = None,
    page: int | None = None,
    source: str | None = None,
    _: str = Depends(require_auth),
) -> JSONResponse:
    source_filter = normalize_job_source_filter(source)
    if page is not None:
        return JSONResponse(jobs_page_payload(limit=limit, page=page, source=source_filter))
    jobs = decorate_jobs(db.list_job_summaries(limit=limit, before_id=cursor, source=source_filter))
    if cursor is None:
        return JSONResponse(jobs)
    next_cursor = jobs[-1]["id"] if len(jobs) >= max(1, min(500, limit)) else None
    return JSONResponse({"ok": True, "jobs": jobs, "next_cursor": next_cursor})


@app.get("/api/transfer/targets")
def api_transfer_targets(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse({"ok": True, "targets": [transfer_target_payload(target) for target in db.list_transfer_targets()]})


@app.post("/api/transfer/targets")
async def api_create_transfer_target(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object is required")
    try:
        clean = transfer_target_fields_from_payload(payload)
        target_id = db.create_transfer_target(**clean)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    target = db.get_transfer_target(target_id)
    return JSONResponse({"ok": True, "target": transfer_target_payload(target) if target else None})


@app.patch("/api/transfer/targets/{target_id}")
async def api_update_transfer_target(
    target_id: int,
    request: Request,
    _: str = Depends(require_auth),
) -> JSONResponse:
    current_target = db.get_transfer_target(target_id)
    if not current_target:
        raise HTTPException(status_code=404, detail="transfer target not found")
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object is required")
    try:
        clean = transfer_target_fields_from_payload(payload, partial=True, current_target=current_target)
        validate_transfer_target_update({**current_target, **clean})
        changed = db.update_transfer_target(target_id, **clean)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    target = db.get_transfer_target(target_id)
    return JSONResponse({"ok": True, "changed": changed, "target": transfer_target_payload(target) if target else None})


@app.delete("/api/transfer/targets/{target_id}")
def api_delete_transfer_target(target_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    if not db.delete_transfer_target(target_id):
        raise HTTPException(status_code=404, detail="transfer target not found")
    return JSONResponse({"ok": True, "deleted": True})


@app.get("/api/transfer/targets/{target_id}/receiver/tree")
def api_transfer_receiver_tree(
    target_id: int,
    path: str = "",
    _: str = Depends(require_auth),
) -> JSONResponse:
    target = db.get_transfer_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="전송 대상을 찾을 수 없습니다.")
    if not bool(target.get("enabled")):
        raise HTTPException(status_code=400, detail="비활성화된 전송 대상입니다.")
    if transfer_target_kind(target) != transfer.TARGET_KIND_RECEIVER:
        raise HTTPException(status_code=400, detail="Receiver 대상만 탐색할 수 있습니다.")
    try:
        clean_path = transfer.validate_destination_subpath(path)
        tree = fetch_receiver_tree(target, clean_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "target": transfer_target_payload(target), **tree})


@app.get("/api/transfer/targets/{target_id}/local-mount/tree")
def api_transfer_local_mount_tree(
    target_id: int,
    path: str = "",
    limit: int = 500,
    cursor: str | None = None,
    _: str = Depends(require_auth),
) -> JSONResponse:
    target = db.get_transfer_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="전송 대상을 찾을 수 없습니다.")
    if not bool(target.get("enabled")):
        raise HTTPException(status_code=400, detail="비활성화된 전송 대상입니다.")
    if transfer_target_kind(target) != TARGET_KIND_LOCAL_MOUNT:
        raise HTTPException(status_code=400, detail="연결 폴더 대상만 탐색할 수 있습니다.")
    try:
        clean_path = transfer.validate_destination_subpath(path)
        transfer.ensure_data_remote_is_separate(data_root=DATA_ROOT, data_remote_root=DATA_REMOTE_ROOT)
        tree = transfer.local_mount_tree(
            str(target.get("remote_path") or ""),
            path=clean_path,
            limit=limit,
            cursor=cursor,
            data_remote_root=DATA_REMOTE_ROOT,
        )
        if not clean_path and isinstance(tree.get("root"), dict):
            tree["root"]["name"] = str(target.get("name") or "연결 폴더")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "target": transfer_target_payload(target), **tree})


@app.post("/api/transfer/targets/{target_id}/comfyui/check")
def api_transfer_comfyui_check(target_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    target = db.get_transfer_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="전송 대상을 찾을 수 없습니다.")
    if not bool(target.get("enabled")):
        raise HTTPException(status_code=400, detail="비활성화된 전송 대상입니다.")
    if transfer_target_kind(target) != TARGET_KIND_LOCAL_MOUNT:
        raise HTTPException(status_code=400, detail="ComfyUI 폴더 체크는 연결 폴더 대상만 지원합니다.")
    checker = getattr(transfer, "check_comfyui_local_mount_target", None)
    if checker is None:
        raise HTTPException(status_code=500, detail="ComfyUI 폴더 체크 helper가 없습니다.")
    try:
        transfer.ensure_data_remote_is_separate(data_root=DATA_ROOT, data_remote_root=DATA_REMOTE_ROOT)
        check = checker(target, data_remote_root=DATA_REMOTE_ROOT)
        payload = transfer_comfyui_check_response_payload(target, check)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=transfer_safe_error_detail(str(exc), target)) from exc
    return JSONResponse(payload)


@app.post("/api/transfer/preflight")
async def api_transfer_preflight(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object is required")
    target, source, relative_path, destination_subpath = transfer_request_parts(payload)
    try:
        preflight = transfer_preflight_payload(source, target, destination_subpath, relative_path=relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(
        {
            "ok": True,
            "target": transfer_target_payload(target),
            "preflight": preflight,
            **preflight,
        }
    )


@app.post("/api/transfer/jobs")
async def api_create_transfer_job(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object is required")
    target, source, relative_path, destination_subpath = transfer_request_parts(payload)
    try:
        preflight = transfer_preflight_payload(source, target, destination_subpath, relative_path=relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_payload = {
        "target_id": int(target["id"]),
        "source_path": relative_path,
        "destination_subpath": destination_subpath,
    }
    job_id = db.create_internal_job(
        INTERNAL_JOB_TRANSFER_COPY,
        input_text=f"transfer:{relative_path}",
        payload=job_payload,
        source="transfer",
        target_dir=str(source),
        filename=source.name,
        total_bytes=int(preflight["source_bytes"]),
        metadata={"transfer_preflight": preflight},
    )
    db.add_job_content_ref(job_id, path=source, role="transfer_source")
    internal_jobs.enqueue_job(job_id)
    return JSONResponse({"ok": True, "job": decorate_job(db.get_job(job_id) or {}), "preflight": preflight, **preflight})


@app.post("/api/transfer/civitai-resources/preflight")
async def api_transfer_civitai_resources_preflight(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object is required")
    _target, plan = civitai_resource_transfer_plan(payload)
    return JSONResponse(plan)


@app.post("/api/transfer/civitai-resources/jobs")
async def api_create_transfer_civitai_resource_jobs(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object is required")
    target, plan = civitai_resource_transfer_plan(payload)
    resources = plan.get("resources") if isinstance(plan.get("resources"), list) else []
    if not resources:
        raise HTTPException(status_code=400, detail="전송할 Resources used 파일이 없습니다.")

    job_specs: list[dict[str, Any]] = []
    for resource in resources:
        source_path = str(resource.get("source_path") or "")
        destination_subpath = str(resource.get("destination_subpath") or "")
        source = transfer_source_path(source_path, target)
        relative_path = relative_data_path(source)
        preflight = transfer_preflight_payload(
            source,
            target,
            destination_subpath,
            relative_path=relative_path,
        )
        job_specs.append(
            {
                "payload": {
                    "target_id": int(target["id"]),
                    "source_path": relative_path,
                    "destination_subpath": destination_subpath,
                },
                "source": source,
                "relative_path": relative_path,
                "preflight": preflight,
                "metadata": {
                    "transfer_preflight": preflight,
                    "civitai_resource_transfer": {
                        "archive_path": plan.get("archive_path"),
                        "model_version_id": resource.get("model_version_id"),
                        "resource_name": resource.get("name"),
                        "resource_type": resource.get("type"),
                    },
                },
            }
        )

    jobs: list[dict[str, Any]] = []
    created_job_ids: list[int] = []
    try:
        for spec in job_specs:
            source = spec["source"]
            preflight = spec["preflight"]
            relative_path = str(spec["relative_path"])
            job_id = db.create_internal_job(
                INTERNAL_JOB_TRANSFER_COPY,
                input_text=f"transfer:{relative_path}",
                payload=spec["payload"],
                source="transfer",
                target_dir=str(source),
                filename=source.name,
                total_bytes=int(preflight["source_bytes"]),
                metadata=spec["metadata"],
            )
            created_job_ids.append(job_id)
            db.add_job_content_ref(job_id, path=source, role="transfer_source")
    except Exception:
        for job_id in created_job_ids:
            db.update_job(job_id, status="failed", error="resource transfer batch creation failed before enqueue")
            db.append_log(job_id, "resource transfer batch creation failed before enqueue")
        raise
    for job_id in created_job_ids:
        internal_jobs.enqueue_job(job_id)
        job = db.get_job(job_id)
        if job:
            jobs.append(decorate_job(job, include_log=False))

    return JSONResponse(
        {
            **plan,
            "ok": True,
            "queued_count": len(jobs),
            "job_ids": [job.get("id") for job in jobs],
            "jobs": jobs,
        }
    )


@app.post("/api/transfer/data-root/preflight")
async def api_transfer_data_root_preflight(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object is required")
    target, destination_subpath = transfer_data_root_request_parts(payload)
    try:
        preflight = transfer_data_root_preflight_payload(target, destination_subpath)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(
        {
            "ok": True,
            "target": transfer_target_payload(target),
            "preflight": preflight,
            **preflight,
        }
    )


@app.post("/api/transfer/data-root/jobs")
async def api_create_transfer_data_root_job(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object is required")
    target, destination_subpath = transfer_data_root_request_parts(payload)
    try:
        preflight = transfer_data_root_preflight_payload(target, destination_subpath)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_payload = {
        "target_id": int(target["id"]),
        "destination_subpath": destination_subpath,
        "data_root_clone": True,
    }
    job_id = db.create_internal_job(
        INTERNAL_JOB_TRANSFER_COPY,
        input_text="transfer:/data",
        payload=job_payload,
        source="transfer",
        target_dir=str(DATA_ROOT),
        filename="data",
        total_bytes=int(preflight["source_bytes"]),
        metadata={"transfer_preflight": preflight},
    )
    db.add_job_content_ref(job_id, path=DATA_ROOT, role="transfer_source")
    internal_jobs.enqueue_job(job_id)
    return JSONResponse({"ok": True, "job": decorate_job(db.get_job(job_id) or {}), "preflight": preflight, **preflight})


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


def normalize_job_source_filter(source: str | None) -> str | None:
    value = str(source or "").strip()
    return value[:80] if value else None


def jobs_page_payload(*, limit: int = JOB_LIST_PAGE_SIZE, page: int = 1, source: str | None = None) -> dict[str, Any]:
    safe_limit = max(1, min(500, int(limit)))
    source_filter = normalize_job_source_filter(source)
    total_count = db.count_jobs(source=source_filter)
    total_pages = max(1, (total_count + safe_limit - 1) // safe_limit)
    current_page = max(1, min(total_pages, int(page)))
    offset = (current_page - 1) * safe_limit
    jobs = decorate_jobs(db.list_job_summaries(limit=safe_limit, offset=offset, source=source_filter))
    return {
        "ok": True,
        "jobs": jobs,
        "page": current_page,
        "limit": safe_limit,
        "total_count": total_count,
        "total_pages": total_pages,
        "active_source": source_filter or "",
        "source_counts": db.count_jobs_by_source(),
    }


def transfer_target_fields_from_payload(
    payload: dict[str, Any],
    *,
    partial: bool = False,
    current_target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    forbidden = copy_forbidden_fields(payload)
    if forbidden:
        raise ValueError("전송은 복사만 지원합니다.")

    fields: dict[str, Any] = {}
    if not partial or "kind" in payload:
        fields["kind"] = transfer.validate_target_kind(str(payload.get("kind") or transfer.TARGET_KIND_RCLONE))
    if not partial or "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("전송 대상 이름을 입력하세요.")
        fields["name"] = name
    target_kind = str(
        fields.get("kind")
        or payload.get("kind")
        or (current_target or {}).get("kind")
        or transfer.TARGET_KIND_RCLONE
    )
    target_kind = transfer.validate_target_kind(target_kind)
    if target_kind == transfer.TARGET_KIND_RCLONE and (not partial or "remote_name" in payload):
        fields["remote_name"] = transfer.validate_remote_name(str(payload.get("remote_name") or ""))
    elif not partial or "kind" in payload:
        fields["remote_name"] = ""
    if "remote_path" in payload or not partial:
        raw_remote_path = str(payload.get("remote_path") or "")
        fields["remote_path"] = (
            transfer.normalize_local_mount_remote_path(raw_remote_path)
            if target_kind == TARGET_KIND_LOCAL_MOUNT
            else transfer.normalize_remote_path(raw_remote_path)
        )
    if target_kind == transfer.TARGET_KIND_RECEIVER and (not partial or "receiver_url" in payload):
        fields["receiver_url"] = transfer.normalize_receiver_url(str(payload.get("receiver_url") or ""))
    elif "receiver_url" in payload or "kind" in payload:
        fields["receiver_url"] = ""
    if target_kind == transfer.TARGET_KIND_RECEIVER and "receiver_token" in payload:
        fields["receiver_token"] = str(payload.get("receiver_token") or "").strip()
    elif not partial or "receiver_token" in payload or "kind" in payload:
        fields["receiver_token"] = ""
    if "enabled" in payload:
        fields["enabled"] = bool(payload.get("enabled"))
    elif not partial:
        fields["enabled"] = True
    if "policy" in payload or "policy_json" in payload or not partial:
        raw_policy = payload.get("policy")
        if raw_policy is None and payload.get("policy_json") is not None:
            try:
                raw_policy = json.loads(str(payload.get("policy_json") or "{}"))
            except json.JSONDecodeError as exc:
                raise ValueError("policy_json must be valid JSON") from exc
        policy = transfer.sanitize_policy(raw_policy if isinstance(raw_policy, dict) else {})
        if not policy.get("allowed_source_prefixes"):
            raise ValueError("허용 원본 prefix를 하나 이상 등록하세요.")
        fields["policy"] = policy
    return fields


def validate_transfer_target_update(target: dict[str, Any]) -> None:
    kind = transfer.validate_target_kind(str(target.get("kind") or transfer.TARGET_KIND_RCLONE))
    if kind == transfer.TARGET_KIND_RECEIVER:
        transfer.normalize_receiver_url(str(target.get("receiver_url") or ""))
    elif kind == TARGET_KIND_LOCAL_MOUNT:
        transfer.normalize_local_mount_remote_path(str(target.get("remote_path") or ""))
    else:
        transfer.validate_remote_name(str(target.get("remote_name") or ""))


def copy_forbidden_fields(payload: dict[str, Any]) -> list[str]:
    forbidden_tokens = ("sync", "move", "delete")
    forbidden_exact = {
        "args",
        "command",
        "operation",
        "raw_remote",
        "remote",
        "remote_target",
        "rclone_args",
        "shell",
        "target",
    }
    forbidden_fields: list[str] = []
    for key, value in payload.items():
        text = str(key).strip().lower().replace("-", "_")
        if text == "mode" or text in forbidden_exact or any(token in text for token in forbidden_tokens):
            forbidden_fields.append(str(key))
            continue
        if isinstance(value, str) and value.strip().lower().replace("_", "-") in {
            "sync",
            "move",
            "delete",
            "delete-before",
            "delete-during",
            "delete-excluded",
        }:
            forbidden_fields.append(str(key))
    return forbidden_fields


def transfer_target_payload(target: dict[str, Any] | None) -> dict[str, Any] | None:
    if target is None:
        return None
    policy = target_policy(target)
    kind = transfer.validate_target_kind(str(target.get("kind") or transfer.TARGET_KIND_RCLONE))
    return {
        "id": int(target["id"]),
        "kind": kind,
        "name": str(target.get("name") or ""),
        "remote_name": str(target.get("remote_name") or "") if kind == transfer.TARGET_KIND_RCLONE else "",
        "remote_path": str(target.get("remote_path") or ""),
        "receiver_url": str(target.get("receiver_url") or "") if kind == transfer.TARGET_KIND_RECEIVER else "",
        "receiver_token_set": bool(target.get("receiver_token")) if kind == transfer.TARGET_KIND_RECEIVER else False,
        "enabled": bool(target.get("enabled")),
        "policy": policy,
        "created_at": target.get("created_at"),
        "updated_at": target.get("updated_at"),
    }


def transfer_comfyui_check_response_payload(target: dict[str, Any], check: Any) -> dict[str, Any]:
    if not isinstance(check, dict):
        raise ValueError("ComfyUI 폴더 체크 결과가 올바르지 않습니다.")
    payload = {
        **check,
        "ok": True,
        "target": transfer_target_payload(target),
        "check": check,
    }
    assert_transfer_response_has_no_sensitive_strings(payload, target)
    return payload


def assert_transfer_response_has_no_sensitive_strings(payload: Any, target: dict[str, Any]) -> None:
    sensitive_values = transfer_sensitive_response_values(target)

    def visit(value: Any) -> None:
        if isinstance(value, str):
            if (redact_sensitive_text(value) or "") != value:
                raise ValueError("ComfyUI 폴더 체크 결과에 민감한 값이 포함되어 있습니다.")
            if any(sensitive and sensitive in value for sensitive in sensitive_values):
                raise ValueError("ComfyUI 폴더 체크 결과에 민감한 경로가 포함되어 있습니다.")
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(payload)


def transfer_sensitive_response_values(target: dict[str, Any]) -> list[str]:
    raw_values: list[str] = []
    for path_value in (
        DATA_REMOTE_ROOT,
        DATA_REMOTE_ROOT.resolve(strict=False),
        DATA_ROOT,
        DATA_ROOT.resolve(strict=False),
        BASE_DIR,
        BASE_DIR.resolve(strict=False),
        db.DB_PATH.parent,
        db.DB_PATH.parent.resolve(strict=False),
    ):
        text = str(path_value)
        if text and text != "/":
            raw_values.append(text)
    token = str(target.get("receiver_token") or "").strip()
    if token:
        raw_values.append(token)
    return sorted(set(raw_values), key=len, reverse=True)


def transfer_safe_error_detail(message: str, target: dict[str, Any]) -> str:
    detail = redact_sensitive_text(message) or "ComfyUI 폴더 체크에 실패했습니다."
    for sensitive in transfer_sensitive_response_values(target):
        detail = detail.replace(sensitive, "[redacted]")
    return detail


def target_policy(target: dict[str, Any]) -> dict[str, Any]:
    raw_policy = target.get("policy")
    if not isinstance(raw_policy, dict):
        try:
            raw_policy = json.loads(str(target.get("policy_json") or "{}"))
        except json.JSONDecodeError:
            raw_policy = {}
    return transfer.sanitize_policy(raw_policy if isinstance(raw_policy, dict) else {})


def transfer_request_parts(payload: dict[str, Any]) -> tuple[dict[str, Any], Path, str, str]:
    forbidden = copy_forbidden_fields(payload)
    allowed_request_fields = {"target_id", "source_path", "destination_subpath"}
    forbidden.extend(sorted(set(payload) - allowed_request_fields))
    if forbidden:
        raise HTTPException(status_code=400, detail="전송은 복사만 지원합니다.")
    target = transfer_target_from_payload(payload)
    try:
        destination_subpath = transfer.validate_destination_subpath(str(payload.get("destination_subpath") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        source = transfer_source_path(str(payload.get("source_path") or ""), target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    relative_path = relative_data_path(source)
    if not destination_subpath:
        destination_subpath = transfer.policy_destination_subpath_for_source(relative_path, target_policy(target))
    return target, source, relative_path, destination_subpath


def transfer_target_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        target_id = int(payload.get("target_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="전송 대상을 선택하세요.") from exc
    target = db.get_transfer_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="전송 대상을 찾을 수 없습니다.")
    if not bool(target.get("enabled")):
        raise HTTPException(status_code=400, detail="비활성화된 전송 대상입니다.")
    return target


def transfer_data_root_request_parts(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    forbidden = copy_forbidden_fields(payload)
    allowed_request_fields = {"target_id", "destination_subpath"}
    forbidden.extend(sorted(set(payload) - allowed_request_fields))
    if forbidden:
        raise HTTPException(status_code=400, detail="전송은 복사만 지원합니다.")
    try:
        target_id = int(payload.get("target_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="전송 대상을 선택하세요.") from exc
    target = db.get_transfer_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="전송 대상을 찾을 수 없습니다.")
    if not bool(target.get("enabled")):
        raise HTTPException(status_code=400, detail="비활성화된 전송 대상입니다.")
    if transfer_target_kind(target) != TARGET_KIND_LOCAL_MOUNT:
        raise HTTPException(status_code=400, detail="/data 전체 복제는 연결 폴더 대상만 지원합니다.")
    try:
        destination_subpath = transfer.validate_destination_subpath(str(payload.get("destination_subpath") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return target, destination_subpath


def transfer_source_path(path: str, target: dict[str, Any]) -> Path:
    source = existing_data_path(path)
    ensure_downloadable_path(source)
    if source.is_symlink() or has_symlink_ancestor(source):
        raise HTTPException(status_code=400, detail="symlink 경로는 전송할 수 없습니다.")
    root = DATA_ROOT.resolve(strict=False)
    resolved = source.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise HTTPException(status_code=400, detail="데이터 루트 밖의 경로는 전송할 수 없습니다.")
    relative_path = relative_data_path(source)
    policy = target_policy(target)
    allowed_prefixes = policy.get("allowed_source_prefixes") or []
    if not any(path_is_under_prefix(relative_path, str(prefix)) for prefix in allowed_prefixes):
        raise HTTPException(status_code=400, detail="이 전송 대상에서 허용되지 않은 원본 경로입니다.")
    return source


def civitai_resource_transfer_plan(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    forbidden = copy_forbidden_fields(payload)
    allowed_request_fields = {"target_id", "path"}
    forbidden.extend(sorted(set(payload) - allowed_request_fields))
    if forbidden:
        raise HTTPException(status_code=400, detail="전송은 복사만 지원합니다.")

    target = transfer_target_from_payload(payload)
    archive = existing_data_path(str(payload.get("path") or ""))
    ensure_downloadable_path(archive)
    if archive.is_symlink() or has_symlink_ancestor(archive):
        raise HTTPException(status_code=400, detail="symlink 경로는 전송할 수 없습니다.")

    metadata = civitai_image_archive_metadata(archive)
    if not metadata:
        raise HTTPException(status_code=400, detail="Civitai 이미지 archive metadata를 찾지 못했습니다.")
    resources = civitai_generation_resource_entries(metadata)
    if not resources:
        raise HTTPException(status_code=400, detail="Resources used 정보가 없습니다.")
    if len(resources) > 100:
        raise HTTPException(status_code=400, detail="한 번에 100개 이하의 리소스만 전송할 수 있습니다.")

    ids = [str(resource["model_version_id"]) for resource in resources]
    health_rows = civitai_resource_health_payload(ids)
    resource_by_id = {str(resource["model_version_id"]): resource for resource in resources}
    transferable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    seen_sources: set[str] = set()

    for health in health_rows:
        version_id = str(health.get("model_version_id") or "")
        resource = resource_by_id.get(version_id, {"model_version_id": version_id})
        base_row = {
            "model_version_id": version_id,
            "name": str(resource.get("name") or ""),
            "type": str(resource.get("type") or ""),
            "model_id": str(resource.get("model_id") or ""),
            "href": str(resource.get("href") or ""),
            "target_path": str(health.get("target_path") or ""),
        }
        if not health.get("present"):
            missing.append({**base_row, "reason": "로컬에 다운로드된 리소스를 찾지 못했습니다."})
            continue

        try:
            source = civitai_resource_transfer_source_path(str(health.get("target_path") or ""))
            if source is None:
                skipped.append({**base_row, "reason": "전송할 모델 파일을 찾지 못했습니다."})
                continue
            checked_source = transfer_source_path(relative_data_path(source), target)
            relative_path = relative_data_path(checked_source)
            if relative_path in seen_sources:
                skipped.append({**base_row, "source_path": relative_path, "reason": "이미 같은 파일이 포함되었습니다."})
                continue
            destination_subpath = transfer.policy_destination_subpath_for_source(relative_path, target_policy(target))
            preflight = transfer_preflight_payload(
                checked_source,
                target,
                destination_subpath,
                relative_path=relative_path,
            )
        except HTTPException as exc:
            skipped.append({**base_row, "reason": str(exc.detail)})
            continue
        except (OSError, ValueError) as exc:
            skipped.append({**base_row, "reason": str(exc)})
            continue

        seen_sources.add(relative_path)
        transferable.append(
            {
                **base_row,
                "source_path": relative_path,
                "source_name": checked_source.name,
                "destination_subpath": destination_subpath,
                "destination": str(preflight.get("destination") or ""),
                "source_bytes": int(preflight.get("source_bytes") or 0),
                "source_human": str(preflight.get("source_human") or ""),
                "file_count": int(preflight.get("file_count") or 0),
                "preflight": preflight,
            }
        )

    total_bytes = sum(int(resource.get("source_bytes") or 0) for resource in transferable)
    plan = {
        "ok": True,
        "target": transfer_target_payload(target),
        "archive_path": relative_data_path(archive),
        "requested_count": len(resources),
        "present_count": sum(1 for row in health_rows if row.get("present")),
        "transferable_count": len(transferable),
        "missing_count": len(missing),
        "skipped_count": len(skipped),
        "source_bytes": total_bytes,
        "source_human": human_bytes(total_bytes),
        "file_count": sum(int(resource.get("file_count") or 0) for resource in transferable),
        "resources": transferable,
        "missing_resources": missing,
        "skipped": skipped,
    }
    return target, plan


def civitai_generation_resource_entries(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    generation = metadata.get("generation_data") if isinstance(metadata.get("generation_data"), dict) else {}
    if not generation and isinstance(metadata.get("generationData"), dict):
        generation = metadata["generationData"]

    entries: dict[str, dict[str, Any]] = {}

    def ensure_entry(version_id: Any) -> dict[str, Any] | None:
        text = str(version_id or "").strip()
        if not text:
            return None
        entry = entries.setdefault(text, {"model_version_id": text, "name": "", "type": "", "model_id": "", "href": ""})
        return entry

    raw_ids = generation.get("model_version_ids", generation.get("modelVersionIds"))
    if isinstance(raw_ids, list):
        for value in raw_ids:
            ensure_entry(value)

    raw_resources = generation.get("resources")
    if isinstance(raw_resources, list):
        for resource in raw_resources:
            if not isinstance(resource, dict):
                continue
            version_id = first_metadata_text(
                resource.get("model_version_id"),
                resource.get("modelVersionId"),
                resource.get("version_id"),
                resource.get("versionId"),
            )
            entry = ensure_entry(version_id)
            if entry is None:
                continue
            entry["name"] = first_metadata_text(resource.get("name"), entry.get("name"))
            entry["type"] = first_metadata_text(resource.get("type"), entry.get("type"))
            entry["model_id"] = first_metadata_text(resource.get("model_id"), resource.get("modelId"), entry.get("model_id"))
            entry["href"] = first_metadata_text(resource.get("href"), resource.get("url"), entry.get("href"))

    return list(entries.values())


def civitai_resource_transfer_source_path(target_path: str) -> Path | None:
    if not target_path:
        return None
    source = existing_data_path(target_path)
    ensure_downloadable_path(source)
    if source.is_symlink() or has_symlink_ancestor(source):
        raise HTTPException(status_code=400, detail="symlink 경로는 전송할 수 없습니다.")
    if source.is_file():
        return source if is_model_file(source) else None
    if not source.is_dir():
        return None
    return civitai_primary_model_file_for_archive(source) or first_model_file_under(source)


def civitai_primary_model_file_for_archive(path: Path) -> Path | None:
    metadata = civitai_archive_generation_metadata(path)
    components = metadata.get("component_downloads") if isinstance(metadata.get("component_downloads"), list) else []
    for primary_only in (True, False):
        for component in components:
            if not isinstance(component, dict):
                continue
            role = first_metadata_text(component.get("role")).lower()
            if primary_only and role != "primary":
                continue
            for name in civitai_component_candidate_names(component):
                candidate = path / name
                try:
                    if candidate.is_file() and not candidate.is_symlink() and is_model_file(candidate):
                        return candidate
                except OSError:
                    continue
    return None


def first_model_file_under(path: Path, limit: int = 10000) -> Path | None:
    try:
        if path.is_file():
            return path if is_model_file(path) else None
        if not path.is_dir():
            return None
        for index, item in enumerate(path.rglob("*")):
            if index >= limit:
                return None
            if item.is_file() and not item.is_symlink() and is_model_file(item):
                return item
    except OSError:
        return None
    return None


def path_is_under_prefix(path: str, prefix: str) -> bool:
    normalized_path = path.strip("/")
    normalized_prefix = prefix.strip("/")
    return bool(normalized_prefix) and (
        normalized_path == normalized_prefix or normalized_path.startswith(f"{normalized_prefix}/")
    )


def data_root_clone_policy(target: dict[str, Any]) -> dict[str, Any]:
    policy = target_policy(target)
    return transfer.sanitize_policy(
        {
            "bwlimit": policy.get("bwlimit", ""),
            "checkers": policy.get("checkers", 2),
            "include_patterns": [],
            "preserve_folder_name": False,
            "require_check": False,
            "skip_existing": policy.get("skip_existing", True),
            "transfers": policy.get("transfers", 1),
        }
    )


def transfer_data_root_preflight_payload(target: dict[str, Any], destination_subpath: str) -> dict[str, Any]:
    if transfer_target_kind(target) != TARGET_KIND_LOCAL_MOUNT:
        raise ValueError("/data 전체 복제는 연결 폴더 대상만 지원합니다.")
    policy = data_root_clone_policy(target)
    local_preflight = transfer.local_mount_preflight(
        DATA_ROOT,
        remote_path=str(target.get("remote_path") or ""),
        destination_subpath=destination_subpath,
        policy=policy,
        data_root=DATA_ROOT,
        data_remote_root=DATA_REMOTE_ROOT,
        allow_data_root=True,
    )
    file_count = int(local_preflight["file_count"])
    source_bytes = int(local_preflight["source_bytes"])
    destination_path = str(local_preflight.get("destination_path") or "")
    return {
        "source_path": "",
        "source_kind": "folder",
        "source_name": "/data",
        "source_bytes": source_bytes,
        "source_human": human_bytes(source_bytes),
        "file_count": file_count,
        "destination": local_mount_display_destination(target, destination_path),
        "destination_subpath": destination_subpath,
        "include_patterns": [],
        "skip_existing": bool(policy.get("skip_existing", True)),
        "data_root_clone": True,
    }


def transfer_preflight_payload(
    source: Path,
    target: dict[str, Any],
    destination_subpath: str,
    *,
    relative_path: str | None = None,
) -> dict[str, Any]:
    policy = target_policy(target)
    file_count, source_bytes = transfer_source_stats(source, policy)
    if file_count <= 0:
        raise HTTPException(status_code=400, detail="전송할 파일이 없습니다.")
    kind = transfer_target_kind(target)
    if kind == transfer.TARGET_KIND_RECEIVER:
        destination_path = transfer.build_receiver_destination_path(
            source,
            remote_path=str(target.get("remote_path") or ""),
            destination_subpath=destination_subpath,
            preserve_folder_name=bool(policy.get("preserve_folder_name", True)),
        )
        destination = f"receiver:/{destination_path}" if destination_path else "receiver:/"
    elif kind == TARGET_KIND_LOCAL_MOUNT:
        local_preflight = transfer.local_mount_preflight(
            relative_path or relative_data_path(source),
            remote_path=str(target.get("remote_path") or ""),
            destination_subpath=destination_subpath,
            policy=policy,
            data_root=DATA_ROOT,
            data_remote_root=DATA_REMOTE_ROOT,
        )
        file_count = int(local_preflight["file_count"])
        source_bytes = int(local_preflight["source_bytes"])
        destination_path = str(local_preflight.get("destination_path") or "")
        destination = local_mount_display_destination(target, destination_path)
    else:
        destination = transfer.build_remote_destination(
            source,
            remote_name=str(target.get("remote_name") or ""),
            remote_path=str(target.get("remote_path") or ""),
            destination_subpath=destination_subpath,
            preserve_folder_name=bool(policy.get("preserve_folder_name", True)),
        )
    return {
        "source_path": relative_path or relative_data_path(source),
        "source_kind": "folder" if source.is_dir() else "file",
        "source_name": source.name,
        "source_bytes": source_bytes,
        "source_human": human_bytes(source_bytes),
        "file_count": file_count,
        "destination": destination,
        "destination_subpath": destination_subpath,
        "include_patterns": list(policy.get("include_patterns") or []),
    }


def transfer_target_kind(target: dict[str, Any]) -> str:
    return transfer.validate_target_kind(str(target.get("kind") or transfer.TARGET_KIND_RCLONE))


def fetch_receiver_tree(target: dict[str, Any], path: str) -> dict[str, Any]:
    base_url = transfer.normalize_receiver_url(str(target.get("receiver_url") or ""))
    headers = receiver_auth_headers(str(target.get("receiver_token") or ""))
    try:
        response = requests.get(
            f"{base_url}/api/browse",
            params={"path": path, "limit": 500},
            headers=headers,
            timeout=min(15, transfer.receiver_timeout_seconds()),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Receiver에 연결하지 못했습니다.") from exc

    if response.status_code != 200:
        if response.status_code == 401:
            raise HTTPException(status_code=502, detail="Receiver token이 거부되었습니다.")
        raise HTTPException(status_code=502, detail="Receiver 폴더를 불러오지 못했습니다.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Receiver가 JSON이 아닌 응답을 반환했습니다.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("root"), dict):
        raise HTTPException(status_code=502, detail="Receiver 폴더 응답 형식이 올바르지 않습니다.")
    root = sanitize_receiver_tree_node(payload["root"], fallback_path=path)
    return {
        "path": sanitize_receiver_tree_path(payload.get("path"), fallback_path=root["path"]),
        "root": root,
        "children": list(root.get("children") or []),
    }


def sanitize_receiver_tree_path(value: Any, *, fallback_path: str = "") -> str:
    text = "" if value is None else str(value)
    if not text:
        text = fallback_path
    try:
        return transfer.validate_destination_subpath(text)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Receiver 폴더 응답 형식이 올바르지 않습니다.") from exc


def sanitize_receiver_tree_node(node: dict[str, Any], *, fallback_path: str = "") -> dict[str, Any]:
    clean_path = sanitize_receiver_tree_path(node.get("path"), fallback_path=fallback_path)
    raw_children = node.get("children") if isinstance(node.get("children"), list) else []
    children = [
        sanitize_receiver_tree_node(child)
        for child in raw_children
        if isinstance(child, dict)
    ]
    return {
        "name": str(node.get("name") or (clean_path.rsplit("/", 1)[-1] if clean_path else "receive")),
        "path": clean_path,
        "kind": "directory",
        "has_children": bool(node.get("has_children") or node.get("hasChildren") or children),
        "children_loaded": bool(node.get("children_loaded") or node.get("childrenLoaded") or isinstance(node.get("children"), list)),
        "children": children,
        "truncated": bool(node.get("truncated")),
    }


def local_mount_display_destination(target: dict[str, Any], destination_path: str) -> str:
    target_name = str(target.get("name") or "연결 폴더").strip() or "연결 폴더"
    return f"{target_name}/{destination_path}" if destination_path else target_name


def transfer_source_stats(source: Path, policy: dict[str, Any]) -> tuple[int, int]:
    include_patterns = [str(pattern) for pattern in policy.get("include_patterns") or []]
    if source.is_file():
        if include_patterns and not transfer_matches_include(source, source.name, include_patterns):
            return 0, 0
        return 1, source.stat().st_size

    file_count = 0
    source_bytes = 0
    for item in source.rglob("*"):
        try:
            if item.is_symlink():
                ensure_symlink_stays_in_data_root(item)
                continue
            if not item.is_file():
                continue
            relative_name = item.relative_to(source).as_posix()
            if include_patterns and not transfer_matches_include(item, relative_name, include_patterns):
                continue
            file_count += 1
            source_bytes += item.stat().st_size
        except (OSError, ValueError):
            continue
    return file_count, source_bytes


def transfer_matches_include(path: Path, relative_name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative_name, pattern) for pattern in patterns)


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
    if deleted:
        db.clear_library_index()
    vacuumed = False
    if deleted and bool_env("SQLITE_VACUUM_AFTER_CLEAR", default=False):
        db.vacuum_database()
        vacuumed = True
    return JSONResponse(
        {
            "ok": True,
            "deleted": deleted,
            "vacuumed": vacuumed,
            "library_index_reset": bool(deleted),
            "jobs": decorate_jobs(db.list_jobs()),
        }
    )


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
    return JSONResponse(initial_folder_tree())


@app.get("/api/folders/children")
def api_folder_children(
    path: str = "",
    limit: int = FOLDER_CHILDREN_DEFAULT_LIMIT,
    cursor: str | None = None,
    _: str = Depends(require_auth),
) -> JSONResponse:
    return JSONResponse(folder_children_payload(path=path, limit=limit, cursor=cursor))


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
    return JSONResponse({"ok": True, "path": relative_data_path(target), "folders": initial_folder_tree()})


@app.get("/api/library")
def api_library(
    mode: str = "index",
    path: str = "",
    limit: int | None = None,
    page: int | None = None,
    sort: str = "az",
    _: str = Depends(require_auth),
) -> JSONResponse:
    root = existing_data_path(path) if path else None
    if limit is None and page is None:
        return JSONResponse(library_items(mode=mode, root_path=root))
    return JSONResponse(
        library_items_page_payload(
            mode=mode,
            root_path=root,
            limit=limit if limit is not None else LIBRARY_PAGE_SIZE,
            page=page if page is not None else 1,
            sort=sort,
        )
    )


@app.post("/api/library/reindex")
def api_library_reindex(_: str = Depends(require_auth)) -> JSONResponse:
    result = scan_library_index_batch(max_paths=library_reindex_batch_size(), reset=True)
    return JSONResponse({"ok": True, **result, "items": library_items()})


@app.get("/api/media/list")
def api_media_list(path: str, _: str = Depends(require_auth)) -> JSONResponse:
    source = existing_data_path(path)
    ensure_downloadable_path(source)
    files = media_files_for_path(source)
    archive_cover_url = media_archive_cover_url(source)
    payload: dict[str, Any] = {
        "ok": True,
        "path": relative_data_path(source),
        "name": source.name,
        "cover_url": archive_cover_url,
        "items": [
            media_item_payload(item, index, archive_cover_url=archive_cover_url)
            for index, item in enumerate(files)
        ],
    }
    metadata = civitai_archive_generation_metadata(source)
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
    ids = requested_model_version_ids(payload, required=False)
    components = requested_civitai_components(payload)
    if not ids and not components:
        raise HTTPException(status_code=400, detail="확인할 Civitai 리소스가 없습니다.")
    component_results: list[dict[str, Any]] = []
    if components:
        source = existing_data_path(str(payload.get("path") or ""))
        ensure_downloadable_path(source)
        component_results = civitai_component_health_payload(source, components)
    return JSONResponse(
        {
            "ok": True,
            "resources": civitai_resource_health_payload(ids),
            "components": component_results,
        }
    )


@app.post("/api/civitai/refresh")
async def api_civitai_refresh(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="요청 본문이 올바르지 않습니다.")
    source = existing_data_path(str(payload.get("path") or ""))
    ensure_downloadable_path(source)
    if not source.is_dir():
        raise HTTPException(status_code=400, detail="Civitai 모델 폴더를 선택하세요.")
    ensure_no_active_jobs(source)

    metadata = archive_metadata(source)
    parsed = civitai_refresh_parsed_download(source, metadata)
    job_id = db.create_job(parsed)
    db.update_job(
        job_id,
        target_dir=str(source),
        metadata_json=json.dumps(
            {
                "source": "civitai",
                "refresh": True,
                "target_path": relative_data_path(source),
                "model_id": parsed.civitai_model_id,
                "version_id": parsed.civitai_version_id,
            },
            ensure_ascii=False,
        ),
    )
    enqueue_job(job_id)
    return JSONResponse({"ok": True, "job_id": job_id, "jobs": decorate_jobs(db.list_jobs())})


@app.get("/api/media/file")
def api_media_file(path: str, _: str = Depends(require_auth)) -> FileResponse:
    source = existing_data_path(path)
    if not source.is_file() or not is_media_file(source):
        raise HTTPException(status_code=404, detail="미디어 파일을 찾지 못했습니다.")
    return FileResponse(source, media_type=media_type_for_path(source))


@app.get("/api/media/thumbnail")
def api_media_thumbnail(
    path: str,
    size: int = MEDIA_THUMBNAIL_DEFAULT_SIZE,
    _: str = Depends(require_auth),
) -> FileResponse:
    source = thumbnail_source_for_request(path)
    thumbnail = cached_image_thumbnail_path(source, size=size)
    if not safe_cache_file(MEDIA_CACHE_DIR, thumbnail):
        raise HTTPException(status_code=404, detail="썸네일 파일을 찾지 못했습니다.")
    return FileResponse(thumbnail, media_type="image/jpeg", filename=f"{source.stem}.jpg")


@app.post("/api/media/thumbnail-jobs")
async def api_media_thumbnail_jobs(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="요청 본문이 올바르지 않습니다.")

    requested_path = str(payload.get("path") or "").strip()
    if not requested_path:
        raise HTTPException(status_code=400, detail="썸네일을 생성할 저장 폴더를 선택하세요.")
    source = existing_data_path(requested_path)
    ensure_downloadable_path(source)
    try:
        unsafe_source = source.is_symlink() or has_symlink_ancestor(source)
    except (OSError, ValueError):
        unsafe_source = True
    if unsafe_source:
        raise HTTPException(status_code=400, detail="symlink 폴더는 썸네일 생성 대상으로 사용할 수 없습니다.")

    size = thumbnail_size_value(payload.get("size"))
    workers = thumbnail_backfill_worker_count(payload.get("workers"))
    max_items = thumbnail_backfill_item_limit(payload.get("limit"))
    candidates = thumbnail_backfill_candidates(source, size=size, max_items=max_items)
    source_path = relative_data_path(source)
    if not candidates:
        return JSONResponse(
            {
                "ok": True,
                "queued": False,
                "job_id": None,
                "candidate_count": 0,
                "workers": workers,
                "size": size,
                "path": source_path,
            }
        )

    job_id = db.create_internal_job(
        INTERNAL_JOB_MEDIA_THUMBNAIL_BACKFILL,
        input_text=f"thumbnail backfill:{source_path}",
        payload={
            "path": source_path,
            "size": size,
            "workers": workers,
            "limit": max_items,
            "candidate_count": len(candidates),
        },
        target_dir=source,
        filename=f"{source.name}-thumbnails",
        total_bytes=len(candidates),
        metadata={
            "media_job": {
                "kind": INTERNAL_JOB_MEDIA_THUMBNAIL_BACKFILL,
                "path": source_path,
                "size": size,
                "workers": workers,
                "candidate_count": len(candidates),
            }
        },
    )
    internal_jobs.enqueue_job(job_id)
    job = db.get_job(job_id)
    return JSONResponse(
        {
            "ok": True,
            "queued": True,
            "job_id": job_id,
            "job": decorate_job(job, include_log=False) if job else None,
            "candidate_count": len(candidates),
            "workers": workers,
            "size": size,
            "path": source_path,
        }
    )


@app.get("/api/media/text")
def api_media_text(path: str, _: str = Depends(require_auth)) -> JSONResponse:
    source = existing_data_path(path)
    if not source.is_file() or not is_document_file(source):
        raise HTTPException(status_code=404, detail="문서 파일을 찾지 못했습니다.")
    size = source.stat().st_size
    with source.open("rb") as file:
        data = file.read(DOCUMENT_TEXT_MAX_BYTES + 1)
    truncated = len(data) > DOCUMENT_TEXT_MAX_BYTES
    if truncated:
        data = data[:DOCUMENT_TEXT_MAX_BYTES]
    text, encoding = decode_document_bytes(data)
    return JSONResponse(
        {
            "ok": True,
            "path": relative_data_path(source),
            "name": source.name,
            "format": document_format(source),
            "text": text,
            "encoding": encoding,
            "truncated": truncated,
            "size_bytes": size,
            "read_bytes": len(data),
        }
    )


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
    return JSONResponse({"ok": True, "path": relative_data_path(target), "folders": initial_folder_tree()})


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
        return JSONResponse({"ok": True, "path": relative_data_path(source), "folders": initial_folder_tree()})
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
    return JSONResponse({"ok": True, "path": relative_data_path(target), "folders": initial_folder_tree()})


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
    return JSONResponse({"ok": True, "folders": initial_folder_tree()})


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
            "folders": initial_folder_tree(),
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
    if job["job_kind"] == INTERNAL_JOB_MEDIA_THUMBNAIL_BACKFILL:
        job["progress_human"] = f"{int(progress)}개"
        job["total_human"] = f"{int(total or 0)}개"
    if (
        not job.get("thumbnail_url")
        and job.get("target_dir")
        and job.get("status") in {"done", "failed", "paused", "canceled"}
    ):
        job["thumbnail_url"] = card_thumbnail_url_for_path(Path(str(job.get("target_dir"))))
    else:
        job["thumbnail_url"] = card_thumbnail_url_for_url(str(job.get("thumbnail_url") or ""))
    favorite_paths = favorites if favorites is not None else db.favorite_paths()
    job["favorite"] = bool(target_path and target_path in favorite_paths)
    job["source_url"] = source_url_for_job(job, parsed) or existing_source_url
    job["model_title"] = display_model_title_for_job(job)
    decorate_job_media_flags(job)
    return job


def decorate_job_media_flags(job: dict[str, Any]) -> None:
    thumbnail_url = str(job.get("thumbnail_url") or "")
    local_thumbnail = thumbnail_url.startswith("/api/fs/preview?path=") or thumbnail_url.startswith("/api/media/")
    job["has_media"] = bool(job.get("has_media") or local_thumbnail)
    if job["has_media"]:
        try:
            media_count = int(job.get("media_count") or 0)
        except (TypeError, ValueError):
            media_count = 0
        job["media_count"] = max(1, media_count)
        if not job.get("media_type"):
            job["media_type"] = "image"


def normalize_library_sort(sort: str | None) -> str:
    value = str(sort or "az").strip().lower()
    aliases = {
        "date": "date_desc",
        "newest": "date_desc",
        "oldest": "date_asc",
    }
    value = aliases.get(value, value)
    return value if value in {"az", "za", "date_desc", "date_asc", "favorite"} else "az"


def library_page_limit(limit: int | None) -> int:
    try:
        value = int(limit if limit is not None else LIBRARY_PAGE_SIZE)
    except (TypeError, ValueError):
        value = LIBRARY_PAGE_SIZE
    return max(1, min(LIBRARY_PAGE_MAX_SIZE, value))


def library_page_number(page: int | None) -> int:
    try:
        value = int(page if page is not None else 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, value)


def library_items_page_payload(
    *,
    limit: int | None = LIBRARY_PAGE_SIZE,
    page: int | None = 1,
    mode: str = "index",
    root_path: Path | None = None,
    sort: str = "az",
) -> dict[str, Any]:
    page_limit = library_page_limit(limit)
    page_number = library_page_number(page)
    normalized_sort = normalize_library_sort(sort)
    offset = (page_number - 1) * page_limit
    path_prefix = relative_data_path(root_path) if root_path is not None else ""

    if root_path is None and mode != "live":
        total_count = db.count_library_index_items(path_prefix=path_prefix)
        if total_count > 0:
            if offset >= total_count:
                page_number = max(1, (total_count + page_limit - 1) // page_limit)
                offset = (page_number - 1) * page_limit
            favorites = db.favorite_paths()
            items = [
                normalize_library_item_payload(item, favorites)
                for item in db.list_library_index_items(
                    limit=page_limit,
                    offset=offset,
                    path_prefix=path_prefix,
                    sort=normalized_sort,
                )
            ]
            total_pages = max(1, (total_count + page_limit - 1) // page_limit)
            return {
                "ok": True,
                "items": items,
                "page": page_number,
                "limit": page_limit,
                "total_count": total_count,
                "total_pages": total_pages,
                "has_next": page_number < total_pages,
                "mode": "index",
                "path": path_prefix,
                "sort": normalized_sort,
                "paged": True,
            }
        if db.get_library_scan_state("library.indexing", "0") == "1":
            return {
                "ok": True,
                "items": [],
                "page": page_number,
                "limit": page_limit,
                "total_count": 0,
                "total_pages": 1,
                "has_next": False,
                "mode": "index",
                "path": path_prefix,
                "sort": normalized_sort,
                "paged": True,
            }

    items, page_number, total_count, total_pages, has_next = live_library_items_page(
        limit=page_limit,
        offset=offset,
        root_path=root_path,
        sort=normalized_sort,
    )
    return {
        "ok": True,
        "items": items,
        "page": page_number,
        "limit": page_limit,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_next": has_next,
        "mode": "live",
        "path": path_prefix,
        "sort": normalized_sort,
        "paged": True,
    }


def live_library_items_page(
    *,
    limit: int,
    offset: int = 0,
    root_path: Path | None = None,
    sort: str = "az",
) -> tuple[list[dict[str, Any]], int, int | None, int | None, bool]:
    safe_limit = max(1, int(limit))
    safe_offset = max(0, int(offset))
    page_number = max(1, (safe_offset // safe_limit) + 1)
    if root_path is not None:
        live_items, complete = live_library_items_with_completion(
            max_items=0,
            root_path=root_path,
            max_paths=LIVE_LIBRARY_PAGE_COUNT_MAX_PATHS,
        )
        items = sort_library_items(live_items, sort)
        total_count = len(items) if complete else None
        total_pages = max(1, (total_count + safe_limit - 1) // safe_limit) if total_count is not None else None
        if total_pages is not None and safe_offset >= len(items):
            page_number = total_pages
            safe_offset = (page_number - 1) * safe_limit
        page_items = items[safe_offset : safe_offset + safe_limit]
        has_next = page_number < total_pages if total_pages is not None else len(items) > safe_offset + safe_limit
        return page_items, page_number, total_count, total_pages, has_next

    fetch_limit = max(1, safe_offset + safe_limit + 1)
    scan_limit = max(fetch_limit, LIVE_LIBRARY_PAGE_SCAN_MIN_ITEMS)
    items = sort_library_items(live_library_items(max_items=scan_limit, root_path=root_path), sort)
    page_items = items[safe_offset : safe_offset + safe_limit]
    return page_items, page_number, None, None, len(items) > safe_offset + safe_limit


def sort_library_items(items: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    mode = normalize_library_sort(sort)
    if mode == "date_desc":
        return sorted(items, key=lambda item: (-library_item_timestamp(item), library_item_sort_title(item)))
    if mode == "date_asc":
        return sorted(items, key=lambda item: (library_item_timestamp(item), library_item_sort_title(item)))
    if mode == "za":
        return sorted(items, key=library_item_sort_title, reverse=True)
    if mode == "favorite":
        return sorted(items, key=lambda item: (not bool(item.get("favorite")), library_item_sort_title(item)))
    return sorted(items, key=library_item_sort_title)


def library_item_sort_title(item: dict[str, Any]) -> str:
    return str(item.get("model_title") or item.get("filename") or item.get("target_path") or "").casefold()


def library_item_timestamp(item: dict[str, Any]) -> float:
    for key in ("updated_at", "created_at"):
        value = item.get(key)
        if not value:
            continue
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return 0


def normalize_library_item_payload(item: dict[str, Any], favorites: set[str] | None = None) -> dict[str, Any]:
    payload = dict(item)
    target_path = str(payload.get("target_path") or "").strip("/")
    favorite_paths = favorites if favorites is not None else db.favorite_paths()
    payload["favorite"] = bool(target_path and target_path in favorite_paths)
    thumbnail_url = card_thumbnail_url_for_url(str(payload.get("thumbnail_url") or ""))
    if not thumbnail_url:
        target = library_item_target_path(payload)
        if target is not None:
            thumbnail_url = card_thumbnail_url_for_path(target)
    payload["thumbnail_url"] = thumbnail_url
    return payload


def library_item_target_path(item: dict[str, Any]) -> Path | None:
    target_dir = str(item.get("target_dir") or "").strip()
    target_path = str(item.get("target_path") or "").strip()
    candidates: list[Path] = []
    if target_dir:
        candidates.append(Path(target_dir))
    if target_path:
        try:
            candidates.append(data_path_from_request_path(target_path))
        except HTTPException:
            pass
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def library_items(max_items: int = 1000, *, mode: str = "index", root_path: Path | None = None) -> list[dict[str, Any]]:
    if root_path is not None:
        return live_library_items(max_items=max_items, root_path=root_path)
    if mode != "live":
        indexed_items = db.list_library_index_items(limit=max_items)
        if indexed_items:
            favorites = db.favorite_paths()
            return [normalize_library_item_payload(item, favorites) for item in indexed_items]
        if db.get_library_scan_state("library.indexing", "0") == "1":
            return []

    return live_library_items(max_items=max_items)


def live_library_items(max_items: int = 1000, *, root_path: Path | None = None) -> list[dict[str, Any]]:
    items, _complete = live_library_items_with_completion(max_items=max_items, root_path=root_path)
    return items


def live_library_items_with_completion(
    max_items: int = 1000,
    *,
    root_path: Path | None = None,
    max_paths: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    favorites = db.favorite_paths()
    items: list[dict[str, Any]] = []
    indexed_dirs: set[Path] = set()
    unlimited = max_items <= 0
    path_limit = max_paths if max_paths is not None else (0 if unlimited else max_items * 6)
    paths, paths_complete = iter_data_paths_with_completion(max_items=path_limit, root_path=root_path)
    items_complete = paths_complete

    for path in paths:
        if not unlimited and len(items) >= max_items:
            items_complete = False
            break
        try:
            if path.is_dir() and should_index_directory(path):
                item = library_item_for_path(path, favorites)
                items.append(item)
                indexed_dirs.add(path.resolve())
        except OSError:
            continue

    for path in paths:
        if not unlimited and len(items) >= max_items:
            items_complete = False
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

    return sorted(items, key=lambda item: (str(item.get("target_path") or "").lower())), items_complete


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


def iter_data_paths(*, max_items: int, root_path: Path | None = None) -> list[Path]:
    paths, _complete = iter_data_paths_with_completion(max_items=max_items, root_path=root_path)
    return paths


def iter_data_paths_with_completion(*, max_items: int, root_path: Path | None = None) -> tuple[list[Path], bool]:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    root_path = root_path or DATA_ROOT
    unlimited = max_items <= 0
    paths: list[Path] = []
    try:
        if lexical_absolute(root_path) != lexical_absolute(DATA_ROOT) and not should_skip_index_path(root_path):
            paths.append(root_path)
    except OSError:
        return paths, False
    if not unlimited and len(paths) >= max_items:
        return paths, False
    if not root_path.is_dir():
        return paths, True
    try:
        iterator = root_path.rglob("*")
        for path in iterator:
            if not unlimited and len(paths) >= max_items:
                return paths, False
            if should_skip_index_path(path):
                continue
            paths.append(path)
    except OSError:
        return paths, False
    return paths, True


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
    return is_image_file(path) or is_video_file(path) or is_audio_file(path) or is_document_file(path)


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def is_document_file(path: Path) -> bool:
    return path.suffix.lower() in DOCUMENT_EXTENSIONS


def is_subtitle_file(path: Path) -> bool:
    return path.suffix.lower() in SUBTITLE_EXTENSIONS


def display_model_title_for_job(job: dict[str, Any]) -> str:
    current_title = str(job.get("model_title") or "")
    if str(job.get("source") or "") != "gallerydl" or str(job.get("status") or "") != "done":
        return current_title
    target_dir = str(job.get("target_dir") or "").strip()
    if not target_dir:
        return current_title
    title = ytdlp_single_media_title(Path(target_dir))
    return title or current_title


def library_item_for_path(path: Path, favorites: set[str]) -> dict[str, Any]:
    metadata = archive_metadata(path)
    media_files = media_files_for_path(path, limit=1, max_scan_files=media_file_scan_max_files())
    first_media = media_files[0] if media_files else None
    media_count = media_file_count(path, max_scan_files=media_file_scan_max_files())
    if first_media and media_count == 1:
        metadata = metadata_with_ytdlp_media_info(metadata, first_media)
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
        "thumbnail_url": card_thumbnail_url_for_media(first_media) or card_thumbnail_url_for_path(path),
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


def metadata_with_ytdlp_media_info(metadata: dict[str, Any], media_path: Path) -> dict[str, Any]:
    info = ytdlp_info_for_media(media_path)
    if not info:
        return metadata
    enriched = dict(metadata)
    if not enriched.get("title"):
        title = meaningful_metadata_text(info.get("title") or info.get("fulltitle"))
        if title:
            enriched["title"] = title
    if not is_http_url(str(enriched.get("source_url") or "")):
        source_url = meaningful_metadata_text(info.get("webpage_url") or info.get("original_url"))
        if is_http_url(source_url):
            enriched["source_url"] = source_url
    return enriched


def ytdlp_single_media_title(path: Path) -> str:
    media_file = single_media_file_for_title(path, max_scan_files=media_file_scan_max_files())
    if media_file is None:
        return ""
    info = ytdlp_info_for_media(media_file)
    return meaningful_metadata_text(info.get("title") or info.get("fulltitle"))


def single_media_file_for_title(path: Path, *, max_scan_files: int) -> Path | None:
    if path.is_file():
        return path if is_media_file(path) else None
    found: Path | None = None
    scanned = 0
    try:
        for item in path.rglob("*"):
            if max_scan_files > 0 and scanned >= max_scan_files:
                return None
            scanned += 1
            if not item.is_file() or item.is_symlink() or not is_media_file(item):
                continue
            if found is not None:
                return None
            found = item
    except OSError:
        return None
    return found


def ytdlp_info_for_media(media_path: Path) -> dict[str, Any]:
    candidates: list[Path] = []
    expected = media_path.with_suffix(YTDLP_INFO_SUFFIX)
    if expected.exists():
        candidates.append(expected)
    else:
        try:
            candidates.extend(sorted(media_path.parent.glob(f"*{YTDLP_INFO_SUFFIX}"), key=natural_path_key))
        except OSError:
            return {}
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def meaningful_metadata_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if not text or text.upper() == "NA" else text


def library_item_category(path: Path, metadata: dict[str, Any], first_media: Path | None) -> str:
    archive_info = metadata.get("archive_info") if isinstance(metadata.get("archive_info"), dict) else {}
    for value in (archive_info.get("model_category"), metadata.get("model_category"), metadata.get("source")):
        if value == "hitomi":
            return "Hitomi Gallery"
        if value:
            return str(value)
    if first_media:
        if media_kind(first_media) == "document":
            return "Document Archive" if path.is_dir() else "Document File"
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
        if media_kind(first_media) == "document":
            return document_format(first_media)
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


def civitai_archive_generation_metadata(path: Path) -> dict[str, Any]:
    return (
        civitai_image_archive_metadata(path)
        or civitai_model_generation_archive_metadata(path)
        or civitai_model_archive_metadata(path)
    )


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


def civitai_model_generation_archive_metadata(path: Path) -> dict[str, Any]:
    metadata_path = archive_named_metadata_path(path, "_civitai_generation_metadata.json")
    if metadata_path is None:
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    raw_images = payload.get("images")
    images = raw_images if isinstance(raw_images, list) else []
    selected = next((image for image in images if isinstance(image, dict)), None)
    if selected is None:
        return {}
    generation = selected.get("generation_data") if isinstance(selected.get("generation_data"), dict) else {}
    image = selected.get("image") if isinstance(selected.get("image"), dict) else {}
    source_url = selected.get("source_url") or image.get("source_url") or payload.get("model_page_url") or payload.get("raw_input")
    return {
        "source": "civitai",
        "kind": "civitai_model_generation_metadata",
        "source_url": source_url,
        "model_page_url": payload.get("model_page_url"),
        "model_id": payload.get("model_id"),
        "version_id": payload.get("version_id"),
        "model_name": payload.get("model_name"),
        "image": image,
        "generation_data": generation,
        "model_details": payload.get("model_details") if isinstance(payload.get("model_details"), dict) else {},
        "component_downloads": payload.get("component_downloads") if isinstance(payload.get("component_downloads"), list) else [],
        "image_count": payload.get("image_count", len(images)),
        "generation_count": payload.get("generation_count"),
    }


def civitai_model_archive_metadata(path: Path) -> dict[str, Any]:
    metadata_path = archive_named_metadata_path(path, "_civitai_metadata.json")
    if metadata_path is None:
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if str(payload.get("source") or "").lower() != "civitai":
        return {}
    if str(payload.get("kind") or "").lower() == "civitai_image_page":
        return {}
    model_id = first_metadata_text(payload.get("model_id"), payload.get("modelId"))
    version_id = first_metadata_text(payload.get("version_id"), payload.get("model_version_id"), payload.get("modelVersionId"))
    model_name = first_metadata_text(payload.get("model_name"), payload.get("title"))
    model_page_url = first_metadata_text(payload.get("model_page_url"))
    if not model_page_url and model_id:
        model_page_url = f"https://civitai.com/models/{quote(model_id, safe='')}"
        if version_id:
            model_page_url = f"{model_page_url}?modelVersionId={quote(version_id, safe='')}"
    details = payload.get("model_details") if isinstance(payload.get("model_details"), dict) else {}
    if not details:
        model_page_metadata = payload.get("model_page_metadata") if isinstance(payload.get("model_page_metadata"), dict) else {}
        version_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        details = civitai_model_details(
            model_page_metadata,
            version_metadata,
            model_id=model_id or None,
            version_id=version_id,
            model_name=model_name or None,
        )
    source_url = first_metadata_text(model_page_url, payload.get("source_url"), payload.get("raw_input"))
    return {
        "source": "civitai",
        "kind": "civitai_model_generation_metadata",
        "source_url": source_url,
        "model_page_url": model_page_url or source_url,
        "model_id": model_id,
        "version_id": version_id,
        "model_name": model_name,
        "image": {},
        "generation_data": {},
        "model_details": details if isinstance(details, dict) else {},
        "component_downloads": payload.get("component_downloads") if isinstance(payload.get("component_downloads"), list) else [],
        "image_count": 0,
        "generation_count": 0,
    }


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


def metadata_text_value(value: Any) -> str:
    text = str(value or "").strip()
    return text if text and text.lower() not in {"none", "null"} else ""


def first_metadata_text(*values: Any) -> str:
    for value in values:
        text = metadata_text_value(value)
        if text:
            return text
    return ""


def civitai_refresh_parsed_download(path: Path, metadata: dict[str, Any]) -> ParsedDownload:
    if not metadata or str(metadata.get("source") or "").lower() != "civitai":
        raise HTTPException(status_code=400, detail="Civitai 모델 archive metadata를 찾지 못했습니다.")
    if str(metadata.get("kind") or "").lower() == "civitai_image_page":
        raise HTTPException(status_code=400, detail="Civitai image page archive는 이 갱신 작업 대상이 아닙니다.")

    nested = metadata.get("metadata") if isinstance(metadata.get("metadata"), dict) else {}
    nested_model = nested.get("model") if isinstance(nested.get("model"), dict) else {}
    details = metadata.get("model_details") if isinstance(metadata.get("model_details"), dict) else {}
    detail_model = details.get("model") if isinstance(details.get("model"), dict) else {}
    detail_version = details.get("version") if isinstance(details.get("version"), dict) else {}

    version_id = first_metadata_text(
        metadata.get("version_id"),
        metadata.get("model_version_id"),
        metadata.get("modelVersionId"),
        nested.get("id"),
        nested.get("modelVersionId"),
        detail_version.get("id"),
    )
    model_id = first_metadata_text(
        metadata.get("model_id"),
        metadata.get("modelId"),
        nested.get("modelId"),
        nested_model.get("id"),
        detail_model.get("id"),
    )
    if not version_id:
        raise HTTPException(status_code=400, detail="Civitai modelVersionId를 찾지 못했습니다.")

    raw_input = metadata_text_value(metadata.get("raw_input"))
    if not is_http_url(raw_input):
        if model_id:
            raw_input = f"https://civitai.com/models/{quote(model_id)}?modelVersionId={quote(version_id)}"
        else:
            raw_input = f"{CIVITAI_API_BASE}/model-versions/{quote(version_id)}"

    selector = metadata.get("file_selector") if isinstance(metadata.get("file_selector"), dict) else {}
    return ParsedDownload(
        source="civitai",
        raw_input=raw_input,
        target_subdir=relative_data_path(path),
        civitai_model_id=model_id or None,
        civitai_version_id=version_id,
        civitai_file_id=metadata_text_value(selector.get("file_id")) or None,
        civitai_file_type=metadata_text_value(selector.get("type")) or None,
        civitai_file_format=metadata_text_value(selector.get("format")) or None,
        civitai_file_size=metadata_text_value(selector.get("size")) or None,
        civitai_file_fp=metadata_text_value(selector.get("fp")) or None,
        civitai_file_primary=bool(selector.get("primary")),
        civitai_refresh=True,
    )


def requested_model_version_ids(payload: dict[str, Any], *, required: bool = True) -> list[str]:
    raw_ids = payload.get("model_version_ids", payload.get("modelVersionIds"))
    if not isinstance(raw_ids, list):
        if not required:
            return []
        raise HTTPException(status_code=400, detail="model_version_ids 배열이 필요합니다.")
    ids: list[str] = []
    for value in raw_ids:
        text = str(value).strip()
        if text and text not in ids:
            ids.append(text)
    if len(ids) > 100:
        raise HTTPException(status_code=400, detail="한 번에 100개 이하의 리소스만 확인할 수 있습니다.")
    return ids


def requested_civitai_components(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_components = payload.get("components")
    if raw_components is None:
        return []
    if not isinstance(raw_components, list):
        raise HTTPException(status_code=400, detail="components 배열이 필요합니다.")
    components = [item for item in raw_components if isinstance(item, dict)]
    if len(components) > 100:
        raise HTTPException(status_code=400, detail="한 번에 100개 이하의 컴포넌트만 확인할 수 있습니다.")
    return components


def civitai_component_health_payload(path: Path, components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, component in enumerate(components):
        key = first_metadata_text(component.get("key"), f"component:{index}")
        names = civitai_component_candidate_names(component)
        found = next((path / name for name in names if (path / name).is_file()), None)
        results.append(
            {
                "key": key,
                "name": first_metadata_text(component.get("name"), component.get("filename"), component.get("local_file")),
                "filename": first_metadata_text(component.get("filename"), component.get("local_file")),
                "local_file": found.name if found is not None else first_metadata_text(component.get("local_file")),
                "role": first_metadata_text(component.get("role")),
                "type": first_metadata_text(component.get("type")),
                "required": bool(component.get("required")),
                "present": found is not None,
                "target_path": relative_data_path(found) if found is not None else "",
            }
        )
    return results


def civitai_component_candidate_names(component: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for value in (component.get("local_file"), component.get("localFile"), component.get("filename"), component.get("name")):
        text = first_metadata_text(value)
        if not text:
            continue
        name = Path(text.replace("\\", "/")).name
        if name and name not in names:
            names.append(name)
    return names


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
    if source in {"huggingface", "civitai", "generic", "comfyui", "hitomi", "asmrone"}:
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


def media_item_payload(path: Path, index: int, *, archive_cover_url: str = "") -> dict[str, Any]:
    relative_path = relative_data_path(path)
    media_type = media_kind(path)
    thumbnail_url = thumbnail_url_for_media(path)
    cover_url = archive_cover_url if media_type == "audio" else ""
    if media_type == "audio" and not thumbnail_url:
        thumbnail_url = cover_url
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
        "text_url": f"/api/media/text?path={quote(relative_path, safe='/')}" if media_type == "document" else "",
        "thumbnail_url": thumbnail_url,
        "cover_url": cover_url,
        "poster_url": thumbnail_url if media_type == "video" else "",
        "play_ready": media_type != "video" or ready_playable is not None,
        "play_job_required": media_type == "video" and ready_playable is None,
        "poster_ready": media_type != "video" or bool(thumbnail_url),
        "poster_job_required": media_type == "video" and not thumbnail_url,
        "mime_type": "video/mp4" if media_type == "video" else media_type_for_path(path),
        "subtitles": subtitle_payloads_for_media(path) if media_type == "video" else [],
        "size_bytes": path.stat().st_size,
    }


def media_archive_cover_url(path: Path) -> str:
    if not path.is_dir():
        return ""
    return thumbnail_url_for_path(path)


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
    if is_audio_file(path):
        return "audio"
    if is_document_file(path):
        return "document"
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


def card_thumbnail_url_for_media(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        relative_path = relative_data_path(path)
    except (OSError, ValueError):
        return ""
    if is_image_file(path):
        return f"/api/media/thumbnail?path={quote(relative_path, safe='/')}"
    if is_video_file(path) and video_poster_path_if_ready(path) is not None:
        return f"/api/media/poster?path={quote(relative_path, safe='/')}"
    return ""


def card_thumbnail_url_for_path(path: Path) -> str:
    try:
        thumbnail = folder_thumbnail_path(path)
    except OSError:
        return ""
    if thumbnail is None:
        return ""
    return card_thumbnail_url_for_media(thumbnail)


def card_thumbnail_url_for_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.path == "/api/media/thumbnail":
        return url
    if parsed.path == "/api/media/poster":
        return url
    if parsed.path not in {"/api/fs/preview", "/api/media/file"}:
        return url if parsed.scheme in {"http", "https"} else ""
    query = parse_qs(parsed.query)
    value = query.get("path", [""])[0]
    if not value:
        return ""
    try:
        source = existing_data_path(value)
    except HTTPException:
        return ""
    return card_thumbnail_url_for_media(source)


def thumbnail_backfill_worker_count(value: Any | None = None) -> int:
    raw_value = value if value is not None else os.getenv("MEDIA_THUMBNAIL_BACKFILL_WORKERS")
    try:
        workers = int(raw_value if raw_value is not None else MEDIA_THUMBNAIL_BACKFILL_DEFAULT_WORKERS)
    except (TypeError, ValueError):
        workers = MEDIA_THUMBNAIL_BACKFILL_DEFAULT_WORKERS
    return max(1, min(MEDIA_THUMBNAIL_BACKFILL_MAX_WORKERS, workers))


def thumbnail_backfill_item_limit(value: Any | None = None) -> int:
    raw_value = value if value is not None else os.getenv("MEDIA_THUMBNAIL_BACKFILL_MAX_ITEMS")
    try:
        limit = int(raw_value if raw_value is not None else MEDIA_THUMBNAIL_BACKFILL_DEFAULT_MAX_ITEMS)
    except (TypeError, ValueError):
        limit = MEDIA_THUMBNAIL_BACKFILL_DEFAULT_MAX_ITEMS
    return max(1, min(MEDIA_THUMBNAIL_BACKFILL_HARD_MAX_ITEMS, limit))


def thumbnail_request_path_from_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.path != "/api/media/thumbnail":
        return ""
    values = parse_qs(parsed.query).get("path", [])
    if not values:
        return ""
    return str(values[0] or "").strip().replace("\\", "/").lstrip("/")


def thumbnail_cache_ready(source: Path, size: int) -> bool:
    try:
        target = media_thumbnail_target(source, size)
        return target.exists() and target.stat().st_size > 0
    except OSError:
        return False


def thumbnail_backfill_candidates(root_path: Path, *, size: int, max_items: int) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for item in live_library_items(max_items=max_items, root_path=root_path):
        request_path = thumbnail_request_path_from_url(str(item.get("thumbnail_url") or ""))
        if not request_path:
            continue
        try:
            source = thumbnail_source_for_request(request_path)
            resolved = str(source.resolve(strict=False))
        except (HTTPException, OSError, ValueError):
            continue
        if resolved in seen or thumbnail_cache_ready(source, size):
            continue
        try:
            candidates.append(relative_data_path(source))
        except (OSError, ValueError):
            continue
        seen.add(resolved)
    return candidates


def media_type_for_path(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0]
    if guessed:
        return guessed
    if is_image_file(path):
        return "image/jpeg"
    if is_video_file(path):
        return "video/mp4"
    if is_audio_file(path):
        return "audio/mpeg"
    if is_document_file(path):
        return "text/markdown; charset=utf-8" if document_format(path) == "markdown" else "text/plain; charset=utf-8"
    return "application/octet-stream"


def document_format(path: Path) -> str:
    return "markdown" if path.suffix.lower() in {".markdown", ".md"} else "text"


def decode_document_bytes(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-16", "cp932", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


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


def run_media_thumbnail_backfill_job(job_id: int, job: dict[str, Any]) -> None:
    payload = db.parse_internal_job_payload(job)
    root = existing_data_path(str(payload.get("path") or ""))
    ensure_downloadable_path(root)
    size = thumbnail_size_value(payload.get("size"))
    workers = thumbnail_backfill_worker_count(payload.get("workers"))
    raw_paths = payload.get("paths")
    if isinstance(raw_paths, list):
        candidates = [str(path) for path in raw_paths if isinstance(path, str) and path.strip()]
    else:
        candidates = thumbnail_backfill_candidates(
            root,
            size=size,
            max_items=thumbnail_backfill_item_limit(payload.get("limit")),
        )
    total = len(candidates)
    db.update_job(job_id, progress_bytes=0, total_bytes=total)
    db.append_log(job_id, f"thumbnail backfill candidates={total} workers={workers} size={size}")
    if total <= 0:
        return

    results = {"generated": 0, "skipped": 0, "failed": 0}
    failed_examples: list[str] = []

    def generate(relative_path: str) -> tuple[str, str, str]:
        internal_jobs.check_job_control(job_id)
        try:
            source = thumbnail_source_for_request(relative_path)
            if thumbnail_cache_ready(source, size):
                return "skipped", relative_path, ""
            thumbnail = cached_image_thumbnail_path(source, size=size)
            if not safe_cache_file(MEDIA_CACHE_DIR, thumbnail):
                return "failed", relative_path, "thumbnail cache path rejected"
            return "generated", relative_path, ""
        except HTTPException as exc:
            return "failed", relative_path, str(exc.detail)
        except Exception as exc:  # noqa: BLE001 - keep the batch moving past corrupt images
            return "failed", relative_path, str(exc)

    completed = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"thumbnail-backfill-{job_id}") as executor:
        futures = [executor.submit(generate, path) for path in candidates]
        for future in as_completed(futures):
            internal_jobs.check_job_control(job_id)
            status, relative_path, error = future.result()
            completed += 1
            if status in results:
                results[status] += 1
            if status == "failed" and len(failed_examples) < 10:
                failed_examples.append(f"{relative_path}: {error}")
            db.update_job(job_id, progress_bytes=completed, total_bytes=total)

    detail = {
        "media_job": {
            "kind": INTERNAL_JOB_MEDIA_THUMBNAIL_BACKFILL,
            "path": relative_data_path(root),
            "size": size,
            "workers": workers,
            "candidate_count": total,
            **results,
            "failed_examples": failed_examples,
        }
    }
    db.update_job(job_id, progress_bytes=completed, total_bytes=total, metadata_json=json.dumps(detail, ensure_ascii=False))
    db.append_log(
        job_id,
        "thumbnail backfill done "
        f"generated={results['generated']} skipped={results['skipped']} failed={results['failed']}",
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


def thumbnail_source_for_request(path: str) -> Path:
    source = existing_data_path(path)
    ensure_downloadable_path(source)
    try:
        unsafe_source = source.is_symlink() or has_symlink_ancestor(source)
    except (OSError, ValueError):
        unsafe_source = True
    if unsafe_source:
        raise HTTPException(status_code=400, detail="symlink 이미지는 썸네일로 사용할 수 없습니다.")
    if source.is_dir():
        representative = folder_thumbnail_path(source)
        if representative is None:
            raise HTTPException(status_code=404, detail="대표 이미지를 찾지 못했습니다.")
        source = representative
    if not source.is_file() or not is_image_file(source) or source.name.endswith(".part"):
        raise HTTPException(status_code=404, detail="이미지 파일을 찾지 못했습니다.")
    try:
        unsafe_symlink = source.is_symlink() or has_symlink_ancestor(source)
    except (OSError, ValueError):
        unsafe_symlink = True
    if unsafe_symlink:
        raise HTTPException(status_code=400, detail="symlink 이미지는 썸네일로 사용할 수 없습니다.")
    return source


def thumbnail_size_value(size: int | None) -> int:
    try:
        value = int(size if size is not None else MEDIA_THUMBNAIL_DEFAULT_SIZE)
    except (TypeError, ValueError):
        value = MEDIA_THUMBNAIL_DEFAULT_SIZE
    return max(1, min(MEDIA_THUMBNAIL_MAX_SIZE, value))


def media_thumbnail_cache_key(source: Path, size: int) -> str:
    stat = source.stat()
    source_key = f"thumb-v1:{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{size}:jpg"
    return hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:32]


def media_thumbnail_target(source: Path, size: int) -> Path:
    return MEDIA_THUMBNAIL_CACHE_DIR / f"{media_thumbnail_cache_key(source, size)}.jpg"


def media_thumbnail_lock(key: str) -> threading.Lock:
    with MEDIA_THUMBNAIL_LOCKS_LOCK:
        lock = MEDIA_THUMBNAIL_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            MEDIA_THUMBNAIL_LOCKS[key] = lock
        return lock


def cached_image_thumbnail_path(source: Path, *, size: int | None = None) -> Path:
    normalized_size = thumbnail_size_value(size)
    key = media_thumbnail_cache_key(source, normalized_size)
    target = media_thumbnail_target(source, normalized_size)
    if target.exists() and target.stat().st_size > 0:
        return target
    lock = media_thumbnail_lock(key)
    with lock:
        if target.exists() and target.stat().st_size > 0:
            return target
        if not shutil.which("ffmpeg"):
            raise HTTPException(status_code=500, detail="ffmpeg가 없어 이미지 썸네일을 생성할 수 없습니다.")
        MEDIA_THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        temp = MEDIA_THUMBNAIL_CACHE_DIR / f".{key}.tmp.jpg"
        cleanup_file(temp)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            f"scale=w='min(iw,{normalized_size})':h='min(ih,{normalized_size})':force_original_aspect_ratio=decrease",
            "-q:v",
            "4",
            "-map_metadata",
            "-1",
            "-y",
            str(temp),
        ]
        try:
            with media_transcode_semaphore():
                subprocess.run(command, check=True, timeout=30, capture_output=True)
            os.replace(temp, target)
        except subprocess.CalledProcessError as exc:
            cleanup_file(temp)
            detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
            message = f"이미지 썸네일을 생성하지 못했습니다: {detail or exc}"
            raise HTTPException(status_code=500, detail=message[:1000]) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            cleanup_file(temp)
            raise HTTPException(status_code=500, detail=f"이미지 썸네일을 생성하지 못했습니다: {exc}") from exc
    return target


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
    if parsed.source == "asmrone" and is_http_url(parsed.asmrone_url or ""):
        return parsed.asmrone_url or ""
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


def build_folder_tree(
    root: Path,
    max_depth: int = FOLDER_TREE_MAX_DEPTH,
    max_entries: int = FOLDER_TREE_MAX_ENTRIES,
    max_children_per_folder: int = FOLDER_TREE_MAX_CHILDREN_PER_FOLDER,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    remaining = {"count": max_entries}

    def tree_node(path: Path) -> dict[str, Any]:
        has_children = folder_tree_has_expandable_children(path)
        return {
            "name": path.name or str(path),
            "path": "" if path == root else path.relative_to(root).as_posix(),
            "children": [],
            "has_children": has_children,
            "children_loaded": not has_children,
        }

    root_node = tree_node(root)
    queue: list[tuple[Path, dict[str, Any], int]] = [(root, root_node, 0)]
    index = 0
    while index < len(queue) and remaining["count"] > 0:
        path, node, depth = queue[index]
        index += 1
        if depth >= max_depth or is_hitomi_archive_leaf_folder(path):
            continue
        children: list[dict[str, Any]] = []
        folders = direct_child_directories(path)
        child_limit = len(folders) if depth == 0 else max(0, max_children_per_folder)
        for child in folders[:child_limit]:
            if remaining["count"] <= 0:
                break
            child_node = tree_node(child)
            remaining["count"] -= 1
            children.append(child_node)
            queue.append((child, child_node, depth + 1))
        node["children"] = children
        node["has_children"] = bool(folders)
        node["children_loaded"] = len(children) == len(folders)

    return root_node


def initial_folder_tree() -> dict[str, Any]:
    return build_folder_tree(DATA_ROOT, max_depth=FOLDER_TREE_INITIAL_MAX_DEPTH)


def folder_children_payload(*, path: str, limit: int, cursor: str | None = None) -> dict[str, Any]:
    source = existing_data_path(path)
    if not source.is_dir():
        raise HTTPException(status_code=400, detail="폴더 경로를 선택하세요.")
    ensure_real_directory_destination(source)

    safe_limit = folder_child_limit(limit)
    if is_hitomi_archive_leaf_folder(source):
        return {
            "ok": True,
            "path": relative_data_path(source),
            "items": [],
            "limit": safe_limit,
            "next_cursor": None,
            "has_more": False,
        }

    folders = direct_child_directories(source)
    start_index = 0
    if cursor:
        for index, child in enumerate(folders):
            if child.name == cursor:
                start_index = index + 1
                break
        else:
            raise HTTPException(status_code=400, detail="폴더 커서를 찾을 수 없습니다.")

    page = folders[start_index : start_index + safe_limit]
    has_more = start_index + safe_limit < len(folders)
    return {
        "ok": True,
        "path": relative_data_path(source),
        "items": [folder_child_item(child) for child in page],
        "limit": safe_limit,
        "next_cursor": page[-1].name if has_more and page else None,
        "has_more": has_more,
    }


def folder_child_limit(limit: int) -> int:
    if limit < 1:
        raise HTTPException(status_code=400, detail="limit은 1 이상이어야 합니다.")
    return min(FOLDER_CHILDREN_MAX_LIMIT, limit)


def folder_child_item(path: Path) -> dict[str, Any]:
    has_children = folder_tree_has_expandable_children(path)
    return {
        "name": path.name,
        "path": relative_data_path(path),
        "has_children": has_children,
        "children_loaded": not has_children,
    }


def folder_tree_has_expandable_children(path: Path) -> bool:
    if is_hitomi_archive_leaf_folder(path):
        return False
    return folder_has_child_directories(path)


def is_hitomi_archive_leaf_folder(path: Path) -> bool:
    if hitomi_archive_marker_exists(path):
        return True
    relative = relative_data_path(path)
    if not relative:
        return False
    parts = Path(relative).parts
    if len(parts) == 2 and parts[0] == HITOMI_ROUTE_ROOT and parts[1] != HITOMI_LISTING_CONTAINER:
        return True
    return len(parts) >= 3 and parts[0] == HITOMI_ROUTE_ROOT and parts[1] == HITOMI_LISTING_CONTAINER


def hitomi_archive_marker_exists(path: Path) -> bool:
    for filename in HITOMI_ARCHIVE_MARKER_FILENAMES:
        try:
            if (path / filename).is_file():
                return True
        except OSError:
            continue
    return False


def direct_child_directories(path: Path) -> list[Path]:
    folders: list[Path] = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        folders.append(Path(entry.path))
                except OSError:
                    continue
    except OSError:
        return []
    return sorted(folders, key=folder_sort_key)


def folder_has_child_directories(path: Path) -> bool:
    try:
        if path.is_symlink():
            return False
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def folder_sort_key(path: Path) -> tuple[str, str]:
    name = path.name
    return (name.casefold(), name)


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


def run_transfer_copy_job(job_id: int, job: dict[str, Any]) -> None:
    payload = db.parse_internal_job_payload(job)
    data_root_clone = bool(payload.get("data_root_clone"))
    source_path = str(payload.get("source_path") or "")
    target_id = int(payload.get("target_id") or 0)
    target = db.get_transfer_target(target_id)
    if not target:
        raise ValueError("transfer target not found")
    if not bool(target.get("enabled")):
        raise ValueError("transfer target disabled")
    destination_subpath = transfer.validate_destination_subpath(str(payload.get("destination_subpath") or ""))
    kind = transfer_target_kind(target)
    if data_root_clone:
        if kind != TARGET_KIND_LOCAL_MOUNT:
            raise ValueError("data root clone requires a local mount target")
        source = DATA_ROOT
        policy = data_root_clone_policy(target)
        preflight = transfer_data_root_preflight_payload(target, destination_subpath)
        source_filename = "data"
    else:
        source = transfer_source_path(source_path, target)
        policy = target_policy(target)
        preflight = transfer_preflight_payload(
            source,
            target,
            destination_subpath,
            relative_path=relative_data_path(source),
        )
        source_filename = source.name
    metadata = parse_json_object(job.get("metadata_json"))
    metadata["transfer_preflight"] = preflight
    db.update_job(
        job_id,
        target_dir=str(source),
        filename=source_filename,
        progress_bytes=0,
        total_bytes=int(preflight["source_bytes"]),
        metadata_json=json.dumps(metadata, ensure_ascii=False),
    )
    with TRANSFER_COPY_SEMAPHORE:
        if kind == transfer.TARGET_KIND_RECEIVER:
            db.append_log(job_id, "receiver copy started")
            receiver_result = run_receiver_transfer(job_id, source, target, destination_subpath, preflight, policy)
            manifest_path = write_transfer_manifest(job_id, preflight, receiver=receiver_result)
        elif kind == TARGET_KIND_LOCAL_MOUNT:
            db.append_log(job_id, "local mount copy started")
            local_mount_result = transfer.copy_to_local_mount(
                source if data_root_clone else relative_data_path(source),
                remote_path=str(target.get("remote_path") or ""),
                destination_subpath=destination_subpath,
                policy=policy,
                data_root=DATA_ROOT,
                data_remote_root=DATA_REMOTE_ROOT,
                job_id=job_id,
                log=lambda message: db.append_log(job_id, message),
                progress=lambda completed, total: db.update_job(
                    job_id,
                    progress_bytes=completed,
                    total_bytes=int(preflight.get("source_bytes") or total or 0),
                ),
                control_check=lambda: internal_jobs.check_job_control(job_id),
                allow_data_root=data_root_clone,
            )
            manifest_path = write_transfer_manifest(job_id, preflight, local_mount=local_mount_result)
        else:
            command = transfer.build_rclone_copy_command(
                source,
                remote_name=str(target.get("remote_name") or ""),
                remote_path=str(target.get("remote_path") or ""),
                destination_subpath=destination_subpath,
                policy=policy,
            )
            db.append_log(job_id, "rclone copy started")
            run_transfer_process(job_id, command)
            manifest_path = write_transfer_manifest(job_id, preflight, command=command)
    db.update_job(job_id, progress_bytes=int(preflight["source_bytes"]), total_bytes=int(preflight["source_bytes"]))
    db.add_job_artifact(job_id, kind="transfer_manifest", path=manifest_path)
    db.add_job_content_ref(job_id, path=source, role="transfer_source")
    db.append_log(job_id, f"transfer manifest: {manifest_path}")


def run_transfer_process(job_id: int, command: list[str]) -> None:
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **controlled_process_kwargs(),
        )
        output_queue = process_output_queue()
        output_done = process.stdout is None
        if process.stdout:
            reader = threading.Thread(
                target=read_process_output,
                args=(process.stdout, output_queue),
                name=f"rclone-output-{job_id}",
                daemon=True,
            )
            reader.start()

        while True:
            internal_jobs.check_job_control(job_id)
            try:
                event = output_queue.get(timeout=1.0)
            except queue.Empty:
                event = None
            if event is not None and event[0] == "done":
                output_done = True
            elif event is not None and event[1] is not None:
                message = transfer.redact_transfer_output(event[1].strip())
                if message:
                    db.append_log(job_id, f"rclone: {message[:1000]}")
            if process.poll() is not None and output_done and output_queue.empty():
                break
        process.wait()
        if process.returncode:
            raise RuntimeError(f"rclone exited with code {process.returncode}")
    except internal_jobs.InternalJobControlStop:
        if process and process.poll() is None:
            stop_controlled_process(job_id, process, "rclone")
        raise
    except Exception:
        if process and process.poll() is None:
            stop_controlled_process(job_id, process, "rclone")
        raise


def run_receiver_transfer(
    job_id: int,
    source: Path,
    target: dict[str, Any],
    destination_subpath: str,
    preflight: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    base_url = transfer.normalize_receiver_url(str(target.get("receiver_url") or ""))
    token = str(target.get("receiver_token") or "")
    destination_root = transfer.build_receiver_destination_path(
        source,
        remote_path=str(target.get("remote_path") or ""),
        destination_subpath=destination_subpath,
        preserve_folder_name=bool(policy.get("preserve_folder_name", True)),
    )
    session = requests.Session()
    headers = receiver_auth_headers(token)
    remote_job = receiver_request_json(
        session,
        "POST",
        f"{base_url}/api/jobs",
        headers=headers,
        json_payload={
            "name": source.name,
            "source_path": preflight.get("source_path") or relative_data_path(source),
            "destination_path": destination_root,
            "source_kind": preflight.get("source_kind") or ("folder" if source.is_dir() else "file"),
            "total_bytes": int(preflight.get("source_bytes") or 0),
            "file_count": int(preflight.get("file_count") or 0),
            "metadata": {
                "sender": "hugcivi",
                "source_path": preflight.get("source_path") or relative_data_path(source),
            },
        },
    )
    receiver_job = remote_job.get("job") if isinstance(remote_job.get("job"), dict) else remote_job
    receiver_job_id = str(receiver_job.get("id") if isinstance(receiver_job, dict) else "").strip()
    if not receiver_job_id:
        raise RuntimeError("receiver did not return a job id")

    db.append_log(job_id, f"receiver job created: {receiver_job_id}")
    uploaded_bytes = 0
    try:
        for local_path, receive_path, size_bytes in receiver_transfer_files(source, destination_root, policy):
            internal_jobs.check_job_control(job_id)
            receiver_upload_file(session, base_url, receiver_job_id, receive_path, local_path, headers=headers)
            uploaded_bytes += size_bytes
            db.update_job(
                job_id,
                progress_bytes=uploaded_bytes,
                total_bytes=int(preflight.get("source_bytes") or 0),
            )
            db.append_log(job_id, f"receiver uploaded: {receive_path}")
        receiver_request_json(session, "POST", f"{base_url}/api/jobs/{quote(receiver_job_id, safe='')}/complete", headers=headers)
    except Exception as exc:
        try:
            receiver_request_json(
                session,
                "POST",
                f"{base_url}/api/jobs/{quote(receiver_job_id, safe='')}/fail",
                headers=headers,
                json_payload={"error": str(exc)[:1000]},
            )
        except Exception:
            pass
        raise

    return {
        "kind": transfer.TARGET_KIND_RECEIVER,
        "receiver_url": base_url,
        "receiver_job_id": receiver_job_id,
        "destination_path": destination_root,
    }


def receiver_auth_headers(token: str) -> dict[str, str]:
    return {"X-Receiver-Token": token} if token else {}


def receiver_request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timeout = transfer.receiver_timeout_seconds()
    response = session.request(method, url, headers=headers or {}, json=json_payload, timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f"receiver returned {response.status_code}: {receiver_error_detail(response)}")
    if not response.content:
        return {}
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("receiver returned non-JSON response") from exc
    return data if isinstance(data, dict) else {}


def receiver_upload_file(
    session: requests.Session,
    base_url: str,
    receiver_job_id: str,
    receive_path: str,
    local_path: Path,
    *,
    headers: dict[str, str],
) -> None:
    timeout = transfer.receiver_timeout_seconds()
    upload_url = f"{base_url}/api/jobs/{quote(receiver_job_id, safe='')}/files/{quote(receive_path, safe='/')}"
    upload_headers = {"Content-Type": "application/octet-stream", **headers}
    with local_path.open("rb") as handle:
        response = session.post(upload_url, headers=upload_headers, data=handle, timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f"receiver upload returned {response.status_code}: {receiver_error_detail(response)}")


def receiver_error_detail(response: requests.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict) and data.get("detail"):
            return str(data.get("detail"))[:500]
    except ValueError:
        pass
    return (response.text or response.reason or "request failed")[:500]


def receiver_transfer_files(source: Path, destination_root: str, policy: dict[str, Any]) -> list[tuple[Path, str, int]]:
    include_patterns = [str(pattern) for pattern in policy.get("include_patterns") or []]
    items: list[tuple[Path, str, int]] = []
    if source.is_file():
        if include_patterns and not transfer_matches_include(source, source.name, include_patterns):
            return items
        receive_path = destination_root or source.name
        items.append((source, receive_path, source.stat().st_size))
        return items

    for item in source.rglob("*"):
        try:
            if item.is_symlink() or not item.is_file():
                continue
            relative_name = item.relative_to(source).as_posix()
            if include_patterns and not transfer_matches_include(item, relative_name, include_patterns):
                continue
            receive_path = "/".join(part for part in (destination_root, relative_name) if part)
            items.append((item, receive_path, item.stat().st_size))
        except (OSError, ValueError):
            continue
    return items


def write_transfer_manifest(
    job_id: int,
    preflight: dict[str, Any],
    command: list[str] | None = None,
    receiver: dict[str, Any] | None = None,
    local_mount: dict[str, Any] | None = None,
) -> Path:
    TRANSFER_MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = TRANSFER_MANIFEST_DIR / f"transfer-{job_id}.json"
    manifest = {
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "preflight": preflight,
    }
    if command is not None:
        manifest["command"] = [transfer.redact_transfer_output(part) for part in command]
    if receiver is not None:
        manifest["receiver"] = receiver
    if local_mount is not None:
        manifest["local_mount"] = local_mount
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


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
