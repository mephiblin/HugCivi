from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from . import db
from .defaults import DOWNLOAD_STALL_TIMEOUT_DEFAULT_SECONDS, YT_DLP_DEFAULT_FORMAT
from .downloader import (
    cleanup_job_local_files,
    cleanup_job_partial_files,
    enqueue_job,
    folder_thumbnail_path,
    notify_queue_settings_changed,
    remove_pending_job,
    start_workers,
    thumbnail_media_type,
    thumbnail_url_for_path,
    update_job_workflow_info,
)
from .models import ParsedDownload
from .parsers import InputParseError, parse_input
from .utils import human_bytes, safe_join, sanitize_segment
from .workflows import WorkflowParseError, find_workflow_png, load_workflow_view, save_workflow_bundle, workflow_max_bytes

app = FastAPI(title="hugcivi", version="0.1.0")
BASE_DIR = Path(__file__).resolve().parent
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))
DOWNLOAD_ARCHIVE_DIR = Path(os.getenv("DOWNLOAD_ARCHIVE_DIR", "/config/downloads"))
MEDIA_CACHE_DIR = Path(os.getenv("MEDIA_CACHE_DIR", "/config/media-cache"))
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
BROWSER_MP4_EXTENSIONS = {".m4v", ".mp4"}
BROWSER_MP4_VIDEO_CODECS = {"h264"}
BROWSER_MP4_AUDIO_CODECS = {"aac", "mp3"}
MEDIA_TRANSCODE_LOCKS: dict[str, threading.Lock] = {}
MEDIA_TRANSCODE_LOCKS_LOCK = threading.Lock()
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


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()
    ensure_route_folders()
    start_workers()


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
    queue_stall_timeout_seconds: str = Form(str(DOWNLOAD_STALL_TIMEOUT_DEFAULT_SECONDS)),
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

    db.set_setting("MAX_CONCURRENT_DOWNLOADS", normalize_int_setting(queue_global_limit, 3, minimum=1))
    db.set_setting("QUEUE_PER_PROVIDER_LIMIT", normalize_int_setting(queue_per_provider_limit, 1, minimum=1))
    db.set_setting(
        "DOWNLOAD_STALL_TIMEOUT_SECONDS",
        normalize_int_setting(queue_stall_timeout_seconds, DOWNLOAD_STALL_TIMEOUT_DEFAULT_SECONDS, minimum=0),
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
def api_jobs(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(decorate_jobs(db.list_jobs()))


@app.get("/api/storage")
def api_storage(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(storage_status())


def jobs_response() -> JSONResponse:
    return JSONResponse({"ok": True, "jobs": decorate_jobs(db.list_jobs())})


def require_job(job_id: int) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.post("/api/jobs/clear")
def api_clear_jobs(_: str = Depends(require_auth)) -> JSONResponse:
    deleted = db.clear_job_history()
    return JSONResponse({"ok": True, "deleted": deleted, "jobs": decorate_jobs(db.list_jobs())})


@app.post("/api/jobs/{job_id}/pause")
def api_pause_job(job_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    job = require_job(job_id)
    status = str(job.get("status"))
    if status == "queued":
        remove_pending_job(job_id)
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
    enqueue_job(job_id)
    return jobs_response()


@app.post("/api/jobs/{job_id}/retry")
def api_retry_job(job_id: int, _: str = Depends(require_auth)) -> JSONResponse:
    job = require_job(job_id)
    status = str(job.get("status"))
    if status not in {"failed", "canceled"}:
        raise HTTPException(status_code=400, detail="재시도할 수 있는 작업 상태가 아닙니다.")
    db.update_job(job_id, status="queued", error=None, progress_bytes=0, total_bytes=None)
    db.append_log(job_id, "retry requested")
    enqueue_job(job_id)
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
        remove_pending_job(job_id)
        cleanup_job_partial_files(job_id)
        cleanup_job_local_files(job_id)
        db.delete_job(job_id)
    return jobs_response()


@app.get("/api/folders")
def api_folders(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(build_folder_tree(DATA_ROOT))


@app.get("/api/library")
def api_library(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(library_items())


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
def api_media_play(path: str, _: str = Depends(require_auth)) -> FileResponse:
    source = existing_data_path(path)
    if not source.is_file() or not is_video_file(source):
        raise HTTPException(status_code=404, detail="동영상 파일을 찾지 못했습니다.")
    playable = browser_playable_video_path(source)
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
def api_media_poster(path: str, _: str = Depends(require_auth)) -> FileResponse:
    source = existing_data_path(path)
    if source.is_file() and is_image_file(source):
        return FileResponse(source, media_type=media_type_for_path(source), filename=source.name)
    if not source.is_file() or not is_video_file(source):
        raise HTTPException(status_code=404, detail="동영상 파일을 찾지 못했습니다.")
    poster = video_poster_path(source)
    return FileResponse(poster, media_type="image/jpeg", filename=f"{source.stem}.jpg")


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
        }
    )


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


def decorate_jobs(jobs: list[dict]) -> list[dict]:
    favorites = db.favorite_paths()
    return [decorate_job(job, favorites) for job in jobs]


def decorate_job(job: dict, favorites: set[str] | None = None) -> dict:
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
    job["progress_human"] = human_bytes(progress)
    job["total_human"] = human_bytes(total)
    job["percent"] = percent
    job["target_path"] = target_path
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


def library_items(max_items: int = 1000) -> list[dict[str, Any]]:
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
    media_files = media_files_for_path(path, limit=1)
    first_media = media_files[0] if media_files else None
    media_count = media_file_count(path)
    relative_path = relative_data_path(path)
    stat = path.stat()
    size = path_size(path)
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


def media_files_for_path(path: Path, limit: int = 500) -> list[Path]:
    if path.is_file():
        return [path] if is_media_file(path) else []
    files: list[Path] = []
    try:
        for item in path.rglob("*"):
            if len(files) >= 10000:
                break
            if item.is_file() and not item.is_symlink() and is_media_file(item):
                files.append(item)
    except OSError:
        return files
    return sorted(files, key=natural_path_key)[:limit]


def media_file_count(path: Path, limit: int = 10000) -> int:
    if path.is_file():
        return 1 if is_media_file(path) else 0
    count = 0
    try:
        for item in path.rglob("*"):
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
    play_url = f"/api/media/play?path={quote(relative_path, safe='/')}" if media_type == "video" else file_url
    return {
        "index": index,
        "path": relative_path,
        "name": path.name,
        "type": media_type,
        "url": play_url,
        "original_url": file_url,
        "thumbnail_url": thumbnail_url,
        "poster_url": thumbnail_url if media_type == "video" else "",
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
    endpoint = "file" if is_image_file(path) else "poster"
    return f"/api/media/{endpoint}?path={quote(relative_path, safe='/')}"


def media_type_for_path(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0]
    if guessed:
        return guessed
    if is_image_file(path):
        return "image/jpeg"
    if is_video_file(path):
        return "video/mp4"
    return "application/octet-stream"


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


def transcode_video_for_browser(source: Path) -> Path:
    if not shutil.which("ffmpeg"):
        raise HTTPException(status_code=500, detail="ffmpeg가 없어 동영상을 브라우저용 MP4로 변환할 수 없습니다.")

    key = media_cache_key(source)
    target = MEDIA_CACHE_DIR / f"{key}.play.mp4"
    if target.exists() and target.stat().st_size > 0:
        return target

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
            subprocess.run(command, check=True, timeout=timeout or None, capture_output=True)
            os.replace(temp, target)
        except subprocess.CalledProcessError as exc:
            cleanup_file(temp)
            detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
            message = f"동영상을 브라우저용 MP4로 변환하지 못했습니다: {detail or exc}"
            raise HTTPException(status_code=500, detail=message[:1000]) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            cleanup_file(temp)
            raise HTTPException(status_code=500, detail=f"동영상을 브라우저용 MP4로 변환하지 못했습니다: {exc}") from exc
    return target


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


def video_poster_path(source: Path) -> Path:
    MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = media_cache_key(source)
    poster = MEDIA_CACHE_DIR / f"{key}.jpg"
    if poster.exists() and poster.stat().st_size > 0:
        return poster
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
        subprocess.run(command, check=True, timeout=30, capture_output=True)
        os.replace(temp, poster)
    except (OSError, subprocess.SubprocessError) as exc:
        cleanup_file(temp)
        raise HTTPException(status_code=500, detail=f"동영상 썸네일을 생성하지 못했습니다: {exc}") from exc
    return poster


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


def normalize_int_setting(value: str, default: int, *, minimum: int = 0) -> str:
    try:
        return str(max(minimum, int(str(value).strip())))
    except ValueError:
        return str(default)


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


def path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
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


def create_zip_archive(source: Path) -> Path:
    DOWNLOAD_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    prefix = f"{sanitize_segment(source.name, 'download')}_"
    fd, temp_name = tempfile.mkstemp(prefix=prefix, suffix=".zip", dir=DOWNLOAD_ARCHIVE_DIR)
    os.close(fd)
    archive_path = Path(temp_name)
    source_root = source.resolve()
    try:
        with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_STORED) as archive:
            for item in sorted(source.rglob("*")):
                if not item.is_file() or item.is_symlink():
                    continue
                resolved = item.resolve()
                if source_root not in resolved.parents and resolved != source_root:
                    continue
                archive.write(resolved, resolved.relative_to(source_root).as_posix())
    except Exception:
        cleanup_file(archive_path)
        raise
    return archive_path


def cleanup_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
