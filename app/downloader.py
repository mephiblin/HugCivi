from __future__ import annotations

import hashlib
import json
import os
import queue
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlparse, urlunparse, unquote

import requests

from . import db
from .defaults import (
    CIVITAI_IMAGE_MAX_RESOURCE_JOBS_DEFAULT,
    DOWNLOAD_HTTP_MAX_RETRIES_HARD_LIMIT_DEFAULT,
    DOWNLOAD_STALL_TIMEOUT_DEFAULT_SECONDS,
    HITOMI_LISTING_MAX_GALLERIES_DEFAULT,
    MAX_CONCURRENT_DOWNLOADS_HARD_LIMIT_DEFAULT,
    PROCESS_OUTPUT_QUEUE_MAX_LINES_DEFAULT,
    QUEUE_PROVIDER_COOLDOWN_MAX_DEFAULT_SECONDS,
    QUEUE_PROVIDER_COOLDOWN_MIN_DEFAULT_SECONDS,
    QUEUE_PER_PROVIDER_LIMIT_HARD_LIMIT_DEFAULT,
    YT_DLP_DEFAULT_FORMAT,
)
from .metadata import classify_civitai, classify_huggingface, pick_civitai_file
from .models import ParsedDownload
from .utils import human_bytes, redact_sensitive_text, safe_join, sanitize_segment
from .workflows import WorkflowParseError, save_workflow_bundle, workflow_max_bytes
from .ytdlp_sites import is_ytdlp_preferred_host

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))
USER_AGENT = os.getenv("USER_AGENT", "nas-model-archiver/0.1")
CHUNK_SIZE = 1024 * 1024
CIVITAI_API_BASE = os.getenv("CIVITAI_API_BASE", "https://civitai.com/api/v1").rstrip("/")
HF_DEFAULT_SNAPSHOT_WORKERS = 2
HF_DOWNLOAD_SUBCOMMAND = "huggingface-download"
HF_RESULT_PREFIX = "HUGCIVI_HF_RESULT_JSON:"
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

_WORKERS_STARTED = False
_WORKERS_LOCK = threading.Lock()
_SCHEDULER_CONDITION = threading.Condition()
_PENDING_JOB_IDS: list[int] = []
_ACTIVE_GLOBAL_JOBS = 0
_ACTIVE_PROVIDER_JOBS: dict[str, int] = {}
_PROVIDER_COOLDOWN_UNTIL: dict[str, float] = {}
_HOST_THROTTLE_LOCK = threading.Lock()
_HOST_NEXT_REQUEST_AT: dict[str, float] = {}
_FINAL_PATH_LOCKS_LOCK = threading.Lock()
_FINAL_PATH_LOCKS: dict[str, threading.Lock] = {}
_GALLERY_DL_VERSION_LOCK = threading.Lock()
_GALLERY_DL_VERSION_CACHE: str | None = None
_YT_DLP_VERSION_LOCK = threading.Lock()
_YT_DLP_VERSION_CACHE: str | None = None
DOWNLOAD_RUNTIME_METADATA_KEY = "download_runtime"
THUMBNAIL_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp"}
THUMBNAIL_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
}
CIVITAI_IMAGE_METADATA_FILENAME = "_civitai_image_metadata.json"
CIVITAI_IMAGE_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
    "image/bmp": ".bmp",
}
CIVITAI_GENERATION_META_LABELS = {
    "seed": "Seed",
    "steps": "Steps",
    "sampler": "Sampler",
    "scheduler": "Scheduler",
    "cfgscale": "CFG scale",
    "cfg_scale": "CFG scale",
    "scale": "CFG scale",
    "clip_skip": "Clip skip",
    "clipskip": "Clip skip",
    "size": "Size",
    "model": "Model",
    "modelhash": "Model hash",
    "model_hash": "Model hash",
    "vae": "VAE",
    "vaehash": "VAE hash",
    "vae_hash": "VAE hash",
    "denoisingstrength": "Denoising strength",
    "denoising_strength": "Denoising strength",
}
CIVITAI_GENERATION_META_EXCLUDED_KEYS = {
    "prompt",
    "negativeprompt",
    "negative_prompt",
    "resources",
    "civitairesources",
    "civitai_resources",
}
CIVITAI_RESOURCE_ACTIVE_STATUSES = {"queued", "running", "paused", "pausing", "deleting"}
CIVITAI_RESOURCE_FAILED_STATUSES = {"failed", "canceled"}
CIVITAI_RESOURCE_PERMANENT_HTTP_STATUSES = {401, 403, 404, 410}
CIVITAI_RESOURCE_RETRY_DELAY_DEFAULT_SECONDS = 24 * 60 * 60
CIVITAI_MODEL_FILE_EXTENSIONS = {
    ".bin",
    ".ckpt",
    ".ggml",
    ".gguf",
    ".onnx",
    ".pt",
    ".pth",
    ".safetensors",
}
FOLDER_THUMBNAIL_MAX_FILES = 5000
YOUTUBE_HOSTS = {
    "youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "youtu.be",
}
YOUTUBE_MANUAL_SUBTITLE_LANGS = ("ko", "en")
YOUTUBE_AUTO_SUBTITLE_LANGS = ("en",)
YOUTUBE_SUBTITLE_FORMAT = "vtt/srt/best"
YOUTUBE_SUBTITLE_PROBE_TIMEOUT_SECONDS = 45
XHAMSTER_HOST_PATTERN = re.compile(r"^xhamster\d*\.(?:com|desi)$")
HITOMI_GALLERY_OUTPUT_RE = re.compile(
    r"https?://(?:www\.)?hitomi\.la/(?:galleries|reader)/(?:[^/?#]+-)?(?P<id>\d+)(?:\.html)?",
    re.IGNORECASE,
)
HITOMI_ACTIVE_OR_PRESENT_STATUSES = {"queued", "running", "paused", "pausing", "canceling", "done", "deleting"}
YT_DLP_SETTING_ALIASES = {
    "YT_DLP_COOKIES_FILE": ("YT_DLP_COOKIES_FILE", "YTDLP_COOKIES_FILE"),
    "YT_DLP_COOKIES_FROM_BROWSER": ("YT_DLP_COOKIES_FROM_BROWSER", "YTDLP_COOKIES_FROM_BROWSER"),
    "YT_DLP_EXTRA_OPTIONS": ("YT_DLP_EXTRA_OPTIONS", "YTDLP_EXTRA_OPTIONS"),
    "YT_DLP_FORMAT": ("YT_DLP_FORMAT", "YTDLP_FORMAT"),
}
YT_DLP_SHARED_CONFIG_KEYS = {"deprecations", "format", "logging"}
YT_DLP_EXTRACTOR_CONFIG_KEYS = {"enabled", "generic", "generic-category"}
YT_DLP_DOWNLOADER_CONFIG_KEYS = {"forward-cookies"}
YT_DLP_BLOCKED_CMDLINE_OPTIONS = {
    "--cache-dir",
    "--config-location",
    "--config-locations",
    "--download-archive",
    "--downloader",
    "--downloader-args",
    "--external-downloader",
    "--external-downloader-args",
    "--ffmpeg-location",
    "--load-info-json",
    "--output",
    "--paths",
    "--plugin-dirs",
    "--postprocessor-args",
    "--ppa",
    "--use-postprocessor",
}
YT_DLP_BLOCKED_CONFIG_OPTIONS = {
    "cachedir",
    "cache-dir",
    "config-file",
    "config-location",
    "config-locations",
    "download-archive",
    "exec",
    "exec-cmd",
    "external-downloader",
    "external-downloader-args",
    "ffmpeg-location",
    "load-info-json",
    "outtmpl",
    "output",
    "paths",
    "plugin-dirs",
    "postprocessor-args",
    "ppa",
    "use-postprocessor",
}
YT_DLP_SUBTITLE_CMDLINE_OPTIONS = {
    "--all-subs",
    "--convert-subs",
    "--embed-subs",
    "--list-subs",
    "--no-write-auto-subs",
    "--no-write-subs",
    "--sub-format",
    "--sub-langs",
    "--srt-langs",
    "--write-auto-subs",
    "--write-subs",
    "--write-sub",
    "--write-subtitles",
}


class JobControlStop(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class FinalPathLockGuard:
    def __init__(self) -> None:
        self.key: str | None = None
        self.lock: threading.Lock | None = None

    def acquire_for(self, path: Path, job_id: int | None = None) -> None:
        key, lock = final_path_lock(path)
        if key == self.key:
            return
        self.release()
        while True:
            if lock.acquire(timeout=1.0):
                break
            if job_id is not None:
                check_job_control(job_id)
        self.key = key
        self.lock = lock

    def release(self) -> None:
        if self.lock is not None:
            self.lock.release()
            self.lock = None
            self.key = None


def stable_hash(value: str, length: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:length]


def partial_download_path(final_path: Path, job_id: int, source_url: str) -> Path:
    source_key = source_url or str(final_path)
    return final_path.with_name(f"{final_path.name}.job-{job_id}-{stable_hash(source_key)}.part")


def final_path_lock_key(path: Path) -> str:
    try:
        return str(path.resolve(strict=False))
    except OSError:
        return str(path.absolute())


def final_path_lock(path: Path) -> tuple[str, threading.Lock]:
    key = final_path_lock_key(path)
    with _FINAL_PATH_LOCKS_LOCK:
        lock = _FINAL_PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _FINAL_PATH_LOCKS[key] = lock
    return key, lock


def parse_metadata_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def register_job_partial_path(job_id: int, part_path: Path, final_path: Path, source_url: str) -> None:
    job = db.get_job(job_id)
    metadata = parse_metadata_json(job.get("metadata_json") if job else None)
    runtime = metadata.get(DOWNLOAD_RUNTIME_METADATA_KEY)
    if not isinstance(runtime, dict):
        runtime = {}

    partials = runtime.get("partial_paths")
    partial_paths = [str(item) for item in partials] if isinstance(partials, list) else []
    part_text = str(part_path)
    if part_text not in partial_paths:
        partial_paths.append(part_text)

    runtime.update(
        {
            "partial_path": part_text,
            "partial_paths": partial_paths,
            "final_path": str(final_path),
            "source_url_hash": stable_hash(source_url or str(final_path)),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    metadata[DOWNLOAD_RUNTIME_METADATA_KEY] = runtime
    db.update_job(job_id, metadata_json=json.dumps(redact_metadata(metadata), ensure_ascii=False))
    db.append_log(job_id, f"partial file: {part_path}")


def job_partial_paths(job_id: int) -> list[Path]:
    job = db.get_job(job_id)
    if not job:
        return []

    paths: list[Path] = []
    metadata = parse_metadata_json(job.get("metadata_json"))
    runtime = metadata.get(DOWNLOAD_RUNTIME_METADATA_KEY)
    if isinstance(runtime, dict):
        partial_path = runtime.get("partial_path")
        if isinstance(partial_path, str) and partial_path:
            paths.append(Path(partial_path))
        partial_paths = runtime.get("partial_paths")
        if isinstance(partial_paths, list):
            paths.extend(Path(str(path)) for path in partial_paths if path)

    target_dir = job.get("target_dir")
    if target_dir:
        target = Path(str(target_dir))
        try:
            paths.extend(target.rglob(f"*.job-{job_id}-*.part"))
        except OSError:
            pass

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = final_path_lock_key(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def is_safe_job_partial_path(path: Path, job_id: int) -> bool:
    if path.suffix != ".part" or f".job-{job_id}-" not in path.name:
        return False
    try:
        root = DATA_ROOT.resolve(strict=False)
        resolved = path.resolve(strict=False)
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def cleanup_job_partial_files(job_id: int) -> list[Path]:
    removed: list[Path] = []
    for path in job_partial_paths(job_id):
        if not is_safe_job_partial_path(path, job_id):
            continue
        try:
            if path.exists():
                path.unlink()
                removed.append(path)
        except OSError as exc:
            db.append_log(job_id, f"partial cleanup failed: {path} ({exc})")
    if removed:
        db.append_log(job_id, "removed partial files: " + ", ".join(str(path) for path in removed))
    return removed


def parse_job_download(job: dict[str, Any]) -> ParsedDownload | None:
    payload = parse_metadata_json(job.get("parsed_json"))
    if not payload:
        return None
    try:
        return ParsedDownload.from_dict(payload)
    except (TypeError, ValueError):
        return None


def is_safe_job_local_path(path: Path) -> bool:
    try:
        root = DATA_ROOT.resolve(strict=False)
        resolved = path.resolve(strict=False)
    except OSError:
        return False
    return resolved != root and root in resolved.parents


def paths_overlap(first: Path, second: Path) -> bool:
    try:
        first_resolved = first.resolve(strict=False)
        second_resolved = second.resolve(strict=False)
    except OSError:
        return False
    return (
        first_resolved == second_resolved
        or first_resolved in second_resolved.parents
        or second_resolved in first_resolved.parents
    )


def job_local_file_path(job: dict[str, Any], target_dir: Path) -> Path | None:
    filename = str(job.get("filename") or "").strip()
    if not filename or filename == "snapshot":
        return None
    candidate = target_dir / filename
    return candidate if is_safe_job_local_path(candidate) else None


def job_uses_custom_target(parsed: ParsedDownload | None) -> bool:
    return bool(parsed and parsed.target_subdir)


def job_local_delete_target(job: dict[str, Any]) -> tuple[Path, str] | None:
    target_dir = str(job.get("target_dir") or "").strip()
    if not target_dir:
        return None

    target = Path(target_dir)
    if not is_safe_job_local_path(target):
        return None

    parsed = parse_job_download(job)
    source = str((parsed.source if parsed else None) or job.get("source") or "")

    if source == "generic":
        file_path = job_local_file_path(job, target)
        return (file_path, "file") if file_path is not None else None

    if source == "huggingface" and (job_uses_custom_target(parsed) or (parsed and parsed.filenames)):
        file_path = job_local_file_path(job, target)
        return (file_path, "file") if file_path is not None else None

    if source == "civitai" and job_uses_custom_target(parsed):
        file_path = job_local_file_path(job, target)
        return (file_path, "file") if file_path is not None else None

    if source in {"civitai", "huggingface", "comfyui", "hitomi", "gallerydl"}:
        return target, "directory"

    file_path = job_local_file_path(job, target)
    return (file_path, "file") if file_path is not None else None


def job_local_target_is_referenced(job_id: int, target: Path, mode: str) -> bool:
    target_key = final_path_lock_key(target)
    for other in db.list_jobs(limit=5000):
        try:
            other_id = int(other.get("id") or 0)
        except (TypeError, ValueError):
            other_id = 0
        if other_id == job_id:
            continue

        other_target_dir = str(other.get("target_dir") or "").strip()
        if not other_target_dir:
            continue
        other_target = Path(other_target_dir)

        if mode == "directory":
            if paths_overlap(target, other_target):
                return True
            continue

        other_file = job_local_file_path(other, other_target)
        if other_file is not None and final_path_lock_key(other_file) == target_key:
            return True
    return False


def cleanup_job_local_files(job_id: int) -> list[Path]:
    job = db.get_job(job_id)
    if not job:
        return []

    target_info = job_local_delete_target(job)
    if target_info is None:
        return []

    target, mode = target_info
    if job_local_target_is_referenced(job_id, target, mode):
        db.append_log(job_id, f"local cleanup skipped: target is referenced by another job ({target})")
        return []

    if not target.exists():
        return []

    removed: list[Path] = []
    try:
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            return []
        removed.append(target)
    except OSError as exc:
        db.append_log(job_id, f"local cleanup failed: {target} ({exc})")
        return []

    db.append_log(job_id, "removed local files: " + ", ".join(str(path) for path in removed))
    return removed


def enqueue_existing_jobs() -> None:
    for job in reversed(db.list_jobs(limit=500)):
        if job["status"] == "deleting":
            cleanup_job_partial_files(int(job["id"]))
            cleanup_job_local_files(int(job["id"]))
            db.delete_job(int(job["id"]))
        elif job["status"] == "canceling":
            db.update_job(int(job["id"]), status="canceled", error=None)
            db.append_log(int(job["id"]), "canceled after restart")
        elif job["status"] == "pausing":
            db.update_job(int(job["id"]), status="paused", error=None)
            db.append_log(int(job["id"]), "paused after restart")
        elif job["status"] in {"queued", "running"}:
            db.update_job(job["id"], status="queued", error=None)
            enqueue_job(int(job["id"]))


def enqueue_job(job_id: int) -> None:
    with _SCHEDULER_CONDITION:
        if job_id not in _PENDING_JOB_IDS:
            _PENDING_JOB_IDS.append(job_id)
        _SCHEDULER_CONDITION.notify_all()


def remove_pending_job(job_id: int) -> None:
    with _SCHEDULER_CONDITION:
        _PENDING_JOB_IDS[:] = [pending_id for pending_id in _PENDING_JOB_IDS if pending_id != job_id]
        _SCHEDULER_CONDITION.notify_all()


def notify_queue_settings_changed() -> None:
    with _SCHEDULER_CONDITION:
        _SCHEDULER_CONDITION.notify_all()


def start_workers() -> None:
    global _WORKERS_STARTED
    with _WORKERS_LOCK:
        if _WORKERS_STARTED:
            return
        print(f"gallery-dl version: {gallery_dl_version()}", flush=True)
        print(f"yt-dlp version: {yt_dlp_version()}", flush=True)
        enqueue_existing_jobs()
        thread = threading.Thread(target=scheduler_loop, name="download-scheduler", daemon=True)
        thread.start()
        _WORKERS_STARTED = True


def scheduler_loop() -> None:
    while True:
        with _SCHEDULER_CONDITION:
            selected: tuple[int, str] | None = None
            while selected is None:
                selected, wait_seconds = pick_next_schedulable_job_locked()
                if selected is None:
                    _SCHEDULER_CONDITION.wait(wait_seconds)
            job_id, provider = selected
            reserve_provider_slot_locked(provider)

        thread = threading.Thread(
            target=job_runner,
            args=(job_id, provider),
            name=f"download-job-{job_id}",
            daemon=True,
        )
        thread.start()


def pick_next_schedulable_job_locked() -> tuple[tuple[int, str] | None, float | None]:
    if _ACTIVE_GLOBAL_JOBS >= queue_global_limit():
        return None, None

    provider_limit = queue_per_provider_limit()
    cooldown_wait: float | None = None
    index = 0
    while index < len(_PENDING_JOB_IDS):
        job_id = _PENDING_JOB_IDS[index]
        job = db.get_job(job_id)
        if not job or job.get("status") != "queued":
            _PENDING_JOB_IDS.pop(index)
            continue
        try:
            provider = provider_key_for_job(job)
        except Exception as exc:  # noqa: BLE001 - malformed persisted payload should not stall the queue
            _PENDING_JOB_IDS.pop(index)
            db.update_job(job_id, status="failed", error=str(exc))
            db.append_log(job_id, f"FAILED: {exc}")
            continue
        if _ACTIVE_PROVIDER_JOBS.get(provider, 0) < provider_limit:
            provider_wait = provider_cooldown_remaining_locked(provider)
            if provider_wait > 0:
                cooldown_wait = provider_wait if cooldown_wait is None else min(cooldown_wait, provider_wait)
                index += 1
                continue
            _PENDING_JOB_IDS.pop(index)
            return (job_id, provider), None
        index += 1
    return None, cooldown_wait


def reserve_provider_slot_locked(provider: str) -> None:
    global _ACTIVE_GLOBAL_JOBS
    _ACTIVE_GLOBAL_JOBS += 1
    _ACTIVE_PROVIDER_JOBS[provider] = _ACTIVE_PROVIDER_JOBS.get(provider, 0) + 1


def release_provider_slot(provider: str) -> None:
    global _ACTIVE_GLOBAL_JOBS
    with _SCHEDULER_CONDITION:
        _ACTIVE_GLOBAL_JOBS = max(0, _ACTIVE_GLOBAL_JOBS - 1)
        cooldown = random_provider_cooldown_seconds()
        if cooldown > 0:
            _PROVIDER_COOLDOWN_UNTIL[provider] = time.monotonic() + cooldown
        else:
            _PROVIDER_COOLDOWN_UNTIL.pop(provider, None)
        current = max(0, _ACTIVE_PROVIDER_JOBS.get(provider, 0) - 1)
        if current:
            _ACTIVE_PROVIDER_JOBS[provider] = current
        else:
            _ACTIVE_PROVIDER_JOBS.pop(provider, None)
        _SCHEDULER_CONDITION.notify_all()


def provider_cooldown_remaining_locked(provider: str) -> float:
    _min_seconds, max_seconds = queue_provider_cooldown_range_seconds()
    if max_seconds <= 0:
        _PROVIDER_COOLDOWN_UNTIL.pop(provider, None)
        return 0.0
    cooldown_until = _PROVIDER_COOLDOWN_UNTIL.get(provider)
    if cooldown_until is None:
        return 0.0
    remaining = cooldown_until - time.monotonic()
    if remaining <= 0:
        _PROVIDER_COOLDOWN_UNTIL.pop(provider, None)
        return 0.0
    return remaining


def job_runner(job_id: int, provider: str) -> None:
    watchdog_stop = threading.Event()
    watchdog = threading.Thread(
        target=job_stall_watchdog,
        args=(job_id, watchdog_stop),
        name=f"download-watchdog-{job_id}",
        daemon=True,
    )
    watchdog.start()
    try:
        try:
            run_job(job_id)
        except JobControlStop as exc:
            handle_control_stop(job_id, exc.status)
        except Exception as exc:  # noqa: BLE001 - log everything for a downloader daemon
            status = current_job_status(job_id)
            if status in {"pausing", "paused"}:
                handle_control_stop(job_id, "paused")
            elif status in {"canceling", "canceled"}:
                handle_control_stop(job_id, "canceled")
            elif status == "deleting":
                handle_control_stop(job_id, "deleted")
            else:
                db.append_log(job_id, f"FAILED: {exc}")
                db.update_job(job_id, status="failed", error=str(exc))
    finally:
        watchdog_stop.set()
        release_provider_slot(provider)


def run_job(job_id: int) -> None:
    job = db.get_job(job_id)
    if not job or job.get("status") not in {"queued", "running"}:
        return
    parsed = db.parse_job_payload(job)
    db.update_job(job_id, status="running", error=None, progress_bytes=0, total_bytes=None)
    db.append_log(job_id, f"started source={parsed.source}")
    check_job_control(job_id)

    if parsed.source == "huggingface":
        download_huggingface(job_id, parsed)
    elif parsed.source == "civitai":
        download_civitai(job_id, parsed)
    elif parsed.source == "generic":
        download_generic(job_id, parsed)
    elif parsed.source == "comfyui":
        download_comfyui(job_id, parsed)
    elif parsed.source == "hitomi":
        download_hitomi(job_id, parsed)
    elif parsed.source == "gallerydl":
        download_gallerydl(job_id, parsed)
    else:
        raise ValueError(f"Unsupported source: {parsed.source}")

    check_job_control(job_id)
    db.update_job(job_id, status="done")
    db.append_log(job_id, "done")


def current_job_status(job_id: int) -> str | None:
    job = db.get_job(job_id)
    return str(job.get("status")) if job else None


def check_job_control(job_id: int) -> None:
    status = current_job_status(job_id)
    if status is None or status == "deleting":
        raise JobControlStop("deleted")
    if status in {"pausing", "paused"}:
        raise JobControlStop("paused")
    if status in {"canceling", "canceled"}:
        raise JobControlStop("canceled")


def handle_control_stop(job_id: int, status: str) -> None:
    if status == "deleted":
        cleanup_job_partial_files(job_id)
        cleanup_job_local_files(job_id)
        db.delete_job(job_id)
        return
    if status == "canceled":
        db.update_job(job_id, status="canceled", error=None)
        db.append_log(job_id, "canceled")
        return
    db.update_job(job_id, status="paused", error=None)
    db.append_log(job_id, "paused")


def base_target(parsed: ParsedDownload, *fallback_parts: str, archive_info: dict[str, Any] | None = None) -> Path:
    if parsed.target_subdir:
        return safe_join(DATA_ROOT, parsed.target_subdir)
    routed_parts = configured_route_parts(archive_info)
    if routed_parts:
        return safe_join(DATA_ROOT, *routed_parts)
    return safe_join(DATA_ROOT, *fallback_parts)


def configured_route_parts(archive_info: dict[str, Any] | None) -> list[str]:
    if not archive_info:
        return []
    route_type = archive_info.get("route_type")
    if not isinstance(route_type, str):
        return []
    setting_key = ROUTE_SETTING_BY_TYPE.get(route_type)
    if not setting_key:
        return []

    route_root = db.library_route_settings().get(setting_key)
    if not route_root:
        return []
    raw_suffix = archive_info.get("target_suffix")
    suffix = raw_suffix if isinstance(raw_suffix, list) else []
    return [route_root, *[str(part) for part in suffix if part]]


def metadata_stamp() -> dict[str, Any]:
    return {"archived_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def write_metadata(target_dir: Path, name: str, payload: dict[str, Any]) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = sanitize_segment(name, "metadata.json")
    if name.startswith("_") and not filename.startswith("_"):
        filename = "_" + filename
    out = target_dir / filename
    safe_payload = redact_metadata(payload)
    out.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def is_thumbnail_image_file(path: Path) -> bool:
    if path.suffix.lower() not in THUMBNAIL_IMAGE_EXTENSIONS or path.name.endswith(".part"):
        return False
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def folder_thumbnail_path(path: Path, *, max_files: int = FOLDER_THUMBNAIL_MAX_FILES) -> Path | None:
    if is_thumbnail_image_file(path):
        return path
    try:
        if not path.is_dir():
            return None
    except OSError:
        return None

    candidates: list[Path] = []
    inspected = 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_symlink() or not item.is_file():
                    continue
            except OSError:
                continue
            inspected += 1
            if inspected > max_files:
                break
            if item.suffix.lower() in THUMBNAIL_IMAGE_EXTENSIONS and not item.name.endswith(".part"):
                candidates.append(item)
    except OSError:
        return None
    if not candidates:
        return None
    return min(candidates, key=lambda item: folder_thumbnail_sort_key(path, item))


def folder_thumbnail_sort_key(root: Path, path: Path) -> tuple[tuple[int, int | str], ...]:
    try:
        relative = path.relative_to(root if root.is_dir() else root.parent)
    except (OSError, ValueError):
        relative = path
    return natural_path_sort_key(relative)


def natural_path_sort_key(path: Path) -> tuple[tuple[int, int | str], ...]:
    key: list[tuple[int, int | str]] = []
    for part in path.parts:
        for token in re.split(r"(\d+)", part.lower()):
            if not token:
                continue
            if token.isdigit():
                key.append((0, int(token)))
            else:
                key.append((1, token))
        key.append((2, "/"))
    return tuple(key)


def data_root_relative_path(path: Path) -> str | None:
    try:
        root = DATA_ROOT.resolve(strict=False)
        resolved = path.resolve(strict=False)
        return resolved.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None


def thumbnail_url_for_path(path: Path) -> str:
    thumbnail = folder_thumbnail_path(path)
    if thumbnail is None:
        return ""
    relative = data_root_relative_path(thumbnail)
    if not relative:
        return ""
    return f"/api/fs/preview?path={quote(relative, safe='/')}"


def thumbnail_media_type(path: Path) -> str:
    return THUMBNAIL_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def redact_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in {"hf_token", "civitai_token", "token", "authorization"}:
                safe[key] = "[REDACTED]"
            else:
                safe[key] = redact_metadata(item)
        return safe
    if isinstance(value, list):
        return [redact_metadata(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def positive_int_env(name: str, default: int, *, maximum: int | None = None) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        value = default
    else:
        try:
            value = max(1, int(raw_value))
        except ValueError:
            value = default
    if maximum is not None and maximum >= 1:
        value = min(value, maximum)
    return value


def nonnegative_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return max(0, int(raw_value))
    except ValueError:
        return default


def positive_int_setting(name: str, default: int, *, maximum: int | None = None) -> int:
    raw_value = db.get_setting(name) or os.getenv(name)
    if raw_value is None:
        value = default
    else:
        try:
            value = max(1, int(raw_value))
        except ValueError:
            value = default
    if maximum is not None and maximum >= 1:
        value = min(value, maximum)
    return value


def nonnegative_int_setting(name: str, default: int) -> int:
    raw_value = db.get_setting(name) or os.getenv(name)
    if raw_value is None:
        return default
    try:
        return max(0, int(raw_value))
    except ValueError:
        return default


def queue_global_limit() -> int:
    return positive_int_setting(
        "MAX_CONCURRENT_DOWNLOADS",
        3,
        maximum=positive_int_env("MAX_CONCURRENT_DOWNLOADS_HARD_LIMIT", MAX_CONCURRENT_DOWNLOADS_HARD_LIMIT_DEFAULT),
    )


def queue_per_provider_limit() -> int:
    return positive_int_setting(
        "QUEUE_PER_PROVIDER_LIMIT",
        1,
        maximum=positive_int_env("QUEUE_PER_PROVIDER_LIMIT_HARD_LIMIT", QUEUE_PER_PROVIDER_LIMIT_HARD_LIMIT_DEFAULT),
    )


def queue_provider_cooldown_range_seconds() -> tuple[int, int]:
    legacy_default = legacy_queue_provider_cooldown_seconds()
    min_seconds = nonnegative_int_setting("QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS", legacy_default)
    max_seconds = nonnegative_int_setting("QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS", legacy_default)
    if max_seconds < min_seconds:
        return max_seconds, min_seconds
    return min_seconds, max_seconds


def legacy_queue_provider_cooldown_seconds() -> int:
    raw_value = db.get_setting("QUEUE_PROVIDER_COOLDOWN_SECONDS") or os.getenv("QUEUE_PROVIDER_COOLDOWN_SECONDS")
    if raw_value is None:
        return QUEUE_PROVIDER_COOLDOWN_MIN_DEFAULT_SECONDS
    try:
        return max(0, int(raw_value))
    except ValueError:
        return QUEUE_PROVIDER_COOLDOWN_MIN_DEFAULT_SECONDS


def random_provider_cooldown_seconds() -> float:
    min_seconds, max_seconds = queue_provider_cooldown_range_seconds()
    if max_seconds <= 0:
        return 0.0
    if min_seconds == max_seconds:
        return float(min_seconds)
    return random.uniform(float(min_seconds), float(max_seconds))


def queue_stall_timeout_seconds() -> int:
    return nonnegative_int_setting("DOWNLOAD_STALL_TIMEOUT_SECONDS", DOWNLOAD_STALL_TIMEOUT_DEFAULT_SECONDS)


def provider_key_for_job(job: dict[str, Any]) -> str:
    return provider_key_for_parsed(db.parse_job_payload(job))


def provider_key_for_parsed(parsed: ParsedDownload) -> str:
    if parsed.source == "huggingface":
        return "huggingface"
    if parsed.source == "civitai":
        return "civitai"
    if parsed.source == "hitomi":
        return "hitomi"
    if parsed.source == "gallerydl":
        return provider_key_from_url("gallerydl", parsed.gallerydl_url)
    if parsed.source == "generic":
        return provider_key_from_url("generic", parsed.url)
    if parsed.source == "comfyui":
        return provider_key_from_url("comfyui", parsed.comfyui_workflow_url)
    return str(parsed.source or "unknown")


def provider_key_from_url(prefix: str, url: str | None) -> str:
    if not url:
        return prefix
    source_url = ytdl_inner_url(url) or url
    host = urlparse(source_url).netloc.lower().removeprefix("www.")
    if host in YOUTUBE_HOSTS:
        host = "youtube.com"
    return f"{prefix}:{host or 'unknown'}"


def job_stall_watchdog(job_id: int, stop_event: threading.Event) -> None:
    last_signature: tuple[Any, ...] | None = None
    last_progress_at = time.monotonic()
    while not stop_event.wait(5.0):
        timeout = queue_stall_timeout_seconds()
        if timeout <= 0:
            last_signature = None
            last_progress_at = time.monotonic()
            continue

        job = db.get_job(job_id)
        if not job or job.get("status") != "running":
            return

        target_size = 0
        target_dir = job.get("target_dir")
        if target_dir:
            try:
                target_size = directory_size(Path(str(target_dir)))
            except OSError:
                target_size = 0
        signature = (
            job.get("progress_bytes") or 0,
            job.get("total_bytes"),
            job.get("filename") or "",
            target_size,
        )
        now = time.monotonic()
        if last_signature is None or signature != last_signature:
            last_signature = signature
            last_progress_at = now
            continue

        if now - last_progress_at >= timeout:
            db.append_log(job_id, f"paused: no download progress for {timeout}s")
            db.update_job(
                job_id,
                status="pausing",
                error=f"No download progress for {timeout}s",
            )
            return


def float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return default


def bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def request_interval_for_url(url: str) -> float:
    host = urlparse(url).netloc.lower()
    default = float_env("DOWNLOAD_REQUEST_MIN_INTERVAL_SECONDS", 1.5)
    if "huggingface.co" in host or host.endswith("hf.co"):
        return float_env("HF_REQUEST_MIN_INTERVAL_SECONDS", default)
    if "civitai." in host or "image.civitai.com" in host:
        return float_env("CIVITAI_REQUEST_MIN_INTERVAL_SECONDS", default)
    if "hitomi.la" in host or "gold-usergeneratedcontent.net" in host:
        return float_env("HITOMI_REQUEST_MIN_INTERVAL_SECONDS", default)
    return default


def wait_for_request_slot(url: str, job_id: int | None = None) -> None:
    interval = request_interval_for_url(url)
    if interval <= 0:
        return
    host = urlparse(url).netloc.lower()
    with _HOST_THROTTLE_LOCK:
        now = time.monotonic()
        next_at = _HOST_NEXT_REQUEST_AT.get(host, now)
        sleep_for = max(0.0, next_at - now)
        _HOST_NEXT_REQUEST_AT[host] = max(now, next_at) + interval
    if sleep_for > 0:
        sleep_with_job_control(job_id, sleep_for)


def retry_after_seconds(response: requests.Response) -> float | None:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        if retry_after.isdigit():
            return float(retry_after)
        try:
            retry_at = parsedate_to_datetime(retry_after)
            return max(0.0, retry_at.timestamp() - time.time())
        except (TypeError, ValueError, OSError):
            pass

    rate_limit = response.headers.get("RateLimit")
    if rate_limit:
        match = re.search(r"(?:^|[;,])\s*t=(\d+)", rate_limit)
        if match:
            return float(match.group(1))
    return None


def retry_delay(response: requests.Response, attempt: int) -> float:
    header_delay = retry_after_seconds(response)
    if header_delay is not None:
        return header_delay
    base = float_env("DOWNLOAD_RETRY_BACKOFF_SECONDS", 5.0)
    delay = base * (2 ** attempt) + random.uniform(0, min(base, 3.0))
    return min(delay, float_env("DOWNLOAD_MAX_RETRY_SLEEP_SECONDS", 300.0))


def request_with_safety(
    session: requests.Session,
    method: str,
    url: str,
    *,
    job_id: int | None = None,
    **kwargs: Any,
) -> requests.Response:
    max_retries = positive_int_env(
        "DOWNLOAD_HTTP_MAX_RETRIES",
        3,
        maximum=positive_int_env("DOWNLOAD_HTTP_MAX_RETRIES_HARD_LIMIT", DOWNLOAD_HTTP_MAX_RETRIES_HARD_LIMIT_DEFAULT),
    )
    retry_statuses = {429, 500, 502, 503, 504}
    response: requests.Response | None = None
    for attempt in range(max_retries + 1):
        if job_id is not None:
            check_job_control(job_id)
        wait_for_request_slot(url, job_id)
        response = session.request(method, url, **kwargs)
        if response.status_code not in retry_statuses or attempt >= max_retries:
            return response

        delay = retry_delay(response, attempt)
        if job_id is not None:
            db.append_log(
                job_id,
                f"HTTP {response.status_code}; retrying in {delay:.1f}s ({attempt + 1}/{max_retries})",
            )
        response.close()
        sleep_with_job_control(job_id, delay)
    if response is None:
        raise RuntimeError("HTTP request was not attempted")
    return response


def sleep_with_job_control(job_id: int | None, seconds: float) -> None:
    if job_id is None:
        time.sleep(seconds)
        return
    deadline = time.time() + seconds
    while True:
        check_job_control(job_id)
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 1.0))


def configure_huggingface_runtime(token: str | None) -> None:
    if token:
        os.environ["HF_TOKEN"] = token
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
    os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "0")
    os.environ.setdefault("HF_XET_NUM_CONCURRENT_RANGE_GETS", "4")
    os.environ.setdefault("HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY", "1")


def verify_huggingface_token(job_id: int, token: str | None) -> None:
    if not token:
        return
    try:
        session = requests.Session()
        response = request_with_safety(
            session,
            "GET",
            "https://huggingface.co/api/whoami-v2",
            job_id=job_id,
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
            timeout=(10, 30),
        )
    except requests.RequestException as exc:
        db.append_log(job_id, f"HF token verification skipped: {exc}")
        return
    if response.status_code in {401, 403}:
        raise ValueError("HF_TOKEN 인증에 실패했습니다. Hugging Face 토큰 값을 확인하세요.")
    response.raise_for_status()
    db.append_log(job_id, "HF token verified")


def download_huggingface(job_id: int, parsed: ParsedDownload) -> None:
    if not parsed.repo_id:
        raise ValueError("Hugging Face repo_id가 없습니다.")

    token = db.get_secret("HF_TOKEN")
    configure_huggingface_runtime(token)

    if token:
        db.append_log(job_id, "HF token configured: authenticated Hub requests enabled")
    else:
        db.append_log(job_id, "HF token not configured: anonymous public Hub access")

    verify_huggingface_token(job_id, token)
    check_job_control(job_id)
    metadata = fetch_huggingface_metadata(parsed, token, job_id)
    check_job_control(job_id)
    archive_info = classify_huggingface(metadata, parsed.repo_type, parsed.repo_id)
    target = base_target(parsed, *archive_info["target_parts"], archive_info=archive_info)
    target.mkdir(parents=True, exist_ok=True)
    update_job_archive_info(job_id, target, archive_info, metadata)
    check_job_control(job_id)

    common = {
        "repo_id": parsed.repo_id,
        "repo_type": parsed.repo_type,
        "revision": parsed.revision,
        "local_dir": str(target),
        "token": token,
    }

    write_metadata(
        target,
        "_archive_metadata.json",
        {
            **metadata_stamp(),
            "source": "huggingface",
            "repo_id": parsed.repo_id,
            "repo_type": parsed.repo_type,
            "revision": parsed.revision,
            "filenames": parsed.filenames,
            "include_patterns": parsed.include_patterns,
            "exclude_patterns": parsed.exclude_patterns,
            "raw_input": parsed.raw_input,
            "archive_info": archive_info,
            "metadata": metadata,
        },
    )

    if parsed.filenames:
        for filename in parsed.filenames:
            check_job_control(job_id)
            db.append_log(job_id, f"HF file download: {parsed.repo_id}/{filename}")
            db.append_log(
                job_id,
                "HF Hub file process started; pause/delete requests can terminate it",
            )
            local_path = run_huggingface_download_process(
                job_id,
                huggingface_download_process_spec(common, mode="file", filename=filename),
                target,
            )
            check_job_control(job_id)
            saved_path = Path(local_path)
            saved_size = saved_path.stat().st_size if saved_path.exists() else 0
            db.append_log(job_id, f"saved: {local_path}")
            db.update_job(
                job_id,
                filename=saved_path.name,
                progress_bytes=saved_size,
                total_bytes=saved_size,
            )
    else:
        check_job_control(job_id)
        db.append_log(job_id, f"HF snapshot download: {parsed.repo_id}")
        db.append_log(
            job_id,
            "HF Hub snapshot process started; pause/delete requests can terminate it",
        )
        local_path = run_huggingface_download_process(
            job_id,
            huggingface_download_process_spec(
                common,
                mode="snapshot",
                allow_patterns=parsed.include_patterns or None,
                ignore_patterns=parsed.exclude_patterns or None,
                max_workers=positive_int_env("HF_SNAPSHOT_MAX_WORKERS", HF_DEFAULT_SNAPSHOT_WORKERS),
            ),
            target,
        )
        check_job_control(job_id)
        saved_size = directory_size(Path(local_path))
        db.append_log(job_id, f"saved snapshot: {local_path} ({human_bytes(saved_size)})")
        db.update_job(job_id, filename="snapshot", progress_bytes=saved_size, total_bytes=saved_size)


def huggingface_download_process_spec(
    common: dict[str, Any],
    *,
    mode: str,
    filename: str | None = None,
    allow_patterns: list[str] | None = None,
    ignore_patterns: list[str] | None = None,
    max_workers: int | None = None,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "repo_id": common["repo_id"],
        "repo_type": common.get("repo_type"),
        "revision": common.get("revision"),
        "local_dir": common["local_dir"],
        "token": common.get("token"),
        "filename": filename,
        "allow_patterns": allow_patterns,
        "ignore_patterns": ignore_patterns,
        "max_workers": max_workers,
    }


def run_huggingface_download_process(job_id: int, spec: dict[str, Any], target: Path) -> str:
    process: subprocess.Popen[str] | None = None
    result: dict[str, Any] = {}
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "app.downloader", HF_DOWNLOAD_SUBCOMMAND],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **controlled_process_kwargs(),
        )
        if process.stdin:
            try:
                process.stdin.write(json.dumps(spec, ensure_ascii=False))
                process.stdin.close()
            except OSError:
                pass

        output_queue = process_output_queue()
        output_done = process.stdout is None
        if process.stdout:
            reader = threading.Thread(
                target=read_process_output,
                args=(process.stdout, output_queue),
                name=f"huggingface-output-{job_id}",
                daemon=True,
            )
            reader.start()

        last_update = 0.0
        while True:
            check_job_control(job_id)
            try:
                event = output_queue.get(timeout=1.0)
            except queue.Empty:
                event = None
            if event is not None and event[0] == "done":
                output_done = True
            elif event is not None and event[1] is not None:
                handle_huggingface_process_output(job_id, event[1], result)

            now = time.time()
            if now - last_update >= 2.0:
                update_huggingface_progress(job_id, target)
                last_update = now
            if process.poll() is not None and output_done and output_queue.empty():
                break

        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(f"Hugging Face download process exited with code {return_code}")

        local_path = result.get("local_path")
        if not isinstance(local_path, str) or not local_path:
            raise RuntimeError("Hugging Face download process did not report a saved path")
        update_huggingface_progress(job_id, target)
        return local_path
    except JobControlStop:
        if process and process.poll() is None:
            stop_controlled_process(job_id, process, "HF download")
        raise
    except Exception:
        if process and process.poll() is None:
            stop_controlled_process(job_id, process, "HF download")
        raise


def handle_huggingface_process_output(job_id: int, line: str, result: dict[str, Any]) -> None:
    message = line.strip()
    if not message:
        return
    if message.startswith(HF_RESULT_PREFIX):
        try:
            payload = json.loads(message[len(HF_RESULT_PREFIX) :])
        except json.JSONDecodeError:
            db.append_log(job_id, "HF download process returned invalid result metadata")
            return
        if isinstance(payload, dict):
            result.update(payload)
        return
    db.append_log(job_id, f"HF: {redact_sensitive_text(message)[:1000]}")


def update_huggingface_progress(job_id: int, target: Path) -> None:
    db.update_job(job_id, progress_bytes=directory_size(target), total_bytes=None)


def civitai_file_selector(parsed: ParsedDownload) -> dict[str, Any]:
    return {
        "file_id": parsed.civitai_file_id,
        "type": parsed.civitai_file_type,
        "format": parsed.civitai_file_format,
        "size": parsed.civitai_file_size,
        "fp": parsed.civitai_file_fp,
        "primary": parsed.civitai_file_primary,
    }


def has_civitai_file_selector(parsed: ParsedDownload) -> bool:
    return any(
        [
            parsed.civitai_file_id,
            parsed.civitai_file_type,
            parsed.civitai_file_format,
            parsed.civitai_file_size,
            parsed.civitai_file_fp,
            parsed.civitai_file_primary,
        ]
    )


def civitai_download_urls(
    parsed: ParsedDownload,
    metadata: dict[str, Any],
    primary_file: dict[str, Any],
    version_id: str,
) -> list[str]:
    urls: list[str] = []
    has_selector = has_civitai_file_selector(parsed)
    if parsed.civitai_download_url:
        urls.append(parsed.civitai_download_url)

    raw_files = metadata.get("files")
    files = raw_files if isinstance(raw_files, list) else []
    selected_file = primary_file or pick_civitai_file(files, civitai_file_selector(parsed))
    raw_mirrors = selected_file.get("mirrors")
    mirrors = raw_mirrors if isinstance(raw_mirrors, list) else []
    for mirror in mirrors:
        if not isinstance(mirror, dict) or mirror.get("deletedAt"):
            continue
        mirror_url = mirror.get("url")
        if isinstance(mirror_url, str):
            urls.append(mirror_url)

    file_download_url = selected_file.get("downloadUrl")
    if isinstance(file_download_url, str):
        urls.append(file_download_url)
    metadata_download_url = metadata.get("downloadUrl")
    if isinstance(metadata_download_url, str) and not has_selector:
        urls.append(metadata_download_url)
    if not has_selector:
        urls.append(f"https://civitai.com/api/download/models/{version_id}")

    seen: set[str] = set()
    normalized: list[str] = []
    for url in urls:
        clean_url = normalize_civitai_download_url(url)
        if clean_url not in seen:
            normalized.append(clean_url)
            seen.add(clean_url)
    return normalized


def normalize_civitai_download_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.lower() in {"civitai.red", "www.civitai.red", "civitai.green", "www.civitai.green"}:
        if parsed.path.startswith("/api/download/"):
            netloc = "civitai.com"
            return urlunparse(parsed._replace(netloc=netloc))
    return url


def fetch_civitai_image_item(session: requests.Session, image_id: str, job_id: int) -> dict[str, Any]:
    meta_url = f"{CIVITAI_API_BASE}/images?imageId={quote(image_id, safe='')}&withMeta=true"
    db.append_log(job_id, f"civitai.image.metadata.start image_id={image_id}")
    db.append_log(job_id, f"Civitai image metadata: {meta_url}")
    data = fetch_json(session, meta_url, job_id=job_id)
    raw_items = data.get("items")
    items = raw_items if isinstance(raw_items, list) else []
    if not items:
        raise ValueError(f"Civitai image {image_id} metadata was not found.")

    for item in items:
        if isinstance(item, dict) and str(item.get("id") or "") == str(image_id):
            return item
    raise ValueError(f"Civitai image API did not return requested imageId={image_id}.")


def civitai_image_source_url(parsed: ParsedDownload, image_id: str) -> str:
    parsed_url = getattr(parsed, "civitai_image_url", None)
    if isinstance(parsed_url, str) and parsed_url.strip():
        return parsed_url.strip()
    return f"https://civitai.com/images/{quote(image_id, safe='')}"


def civitai_image_meta(item: dict[str, Any]) -> dict[str, Any]:
    raw_meta = item.get("meta")
    if not isinstance(raw_meta, dict):
        return {}
    nested = raw_meta.get("meta")
    if isinstance(nested, dict):
        return nested
    return raw_meta


def first_mapping_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    normalized = {normalized_generation_meta_key(key): value for key, value in mapping.items()}
    for key in keys:
        value = normalized.get(normalized_generation_meta_key(key))
        if value not in (None, ""):
            return value
    return None


def text_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            text = str(value)
    return text if text else None


def id_value(value: Any) -> str | None:
    text = text_value(value)
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    return text


def http_status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return int(status_code) if isinstance(status_code, int) else None


def civitai_resource_unavailable_reason(status_code: int | None) -> str:
    if status_code in {401, 403}:
        return "Civitai resource is private or requires authorization."
    if status_code in {404, 410}:
        return "Civitai resource was not found or has been removed."
    return "Civitai resource is unavailable."


def mark_civitai_resource_unavailable(
    resource: dict[str, Any],
    *,
    status_code: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    unavailable = dict(resource)
    unavailable["availability"] = "unavailable"
    unavailable["unavailable_reason"] = reason or civitai_resource_unavailable_reason(status_code)
    if status_code is not None:
        unavailable["status_code"] = str(status_code)
    return unavailable


def normalized_generation_meta_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(key or "").lower())


def generation_meta_label(key: str) -> str:
    normalized = normalized_generation_meta_key(key)
    if normalized in CIVITAI_GENERATION_META_LABELS:
        return CIVITAI_GENERATION_META_LABELS[normalized]
    spaced = re.sub(r"(?<!^)([A-Z])", r" \1", key).replace("_", " ").replace("-", " ")
    return " ".join(spaced.split()).capitalize() or "Metadata"


def civitai_generation_metadata(meta: dict[str, Any]) -> list[dict[str, str]]:
    if not meta:
        return []

    metadata: list[dict[str, str]] = []
    seen: set[str] = set()

    ordered_keys = [
        "seed",
        "steps",
        "sampler",
        "scheduler",
        "cfgScale",
        "cfg_scale",
        "clipSkip",
        "Size",
        "size",
        "Model",
        "model",
        "modelHash",
        "VAE",
        "vae",
        "vaeHash",
        "denoisingStrength",
    ]
    for key in ordered_keys:
        value = first_mapping_value(meta, key)
        normalized = normalized_generation_meta_key(key)
        if normalized in seen:
            continue
        text = text_value(value)
        if text:
            metadata.append({"label": generation_meta_label(key), "value": text})
            seen.add(normalized)

    raw_hashes = meta.get("hashes")
    hashes = raw_hashes if isinstance(raw_hashes, dict) else {}
    for key, value in hashes.items():
        label = f"{generation_meta_label(str(key))} hash"
        normalized = normalized_generation_meta_key(label)
        if normalized in seen:
            continue
        text = text_value(value)
        if text:
            metadata.append({"label": label, "value": text})
            seen.add(normalized)

    for key, value in meta.items():
        normalized = normalized_generation_meta_key(key)
        if normalized in seen or normalized in CIVITAI_GENERATION_META_EXCLUDED_KEYS or normalized == "hashes":
            continue
        if isinstance(value, (dict, list)):
            continue
        text = text_value(value)
        if text:
            metadata.append({"label": generation_meta_label(str(key)), "value": text})
            seen.add(normalized)

    return metadata


def civitai_raw_resource_lists(item: dict[str, Any], meta: dict[str, Any]) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    for raw in (meta.get("resources"), meta.get("civitaiResources"), item.get("resources")):
        if not isinstance(raw, list):
            continue
        resources.extend(resource for resource in raw if isinstance(resource, dict))
    return resources


def normalize_civitai_resource(resource: dict[str, Any]) -> dict[str, str]:
    raw_model = resource.get("model")
    model = raw_model if isinstance(raw_model, dict) else {}
    raw_version = resource.get("modelVersion")
    version_info = raw_version if isinstance(raw_version, dict) else {}

    model_id = id_value(first_mapping_value(resource, "modelId", "model_id", "modelID") or model.get("id"))
    version_id = id_value(
        first_mapping_value(resource, "modelVersionId", "model_version_id", "versionId", "version_id")
        or version_info.get("id")
    )
    name = text_value(first_mapping_value(resource, "name", "modelName", "model_name") or model.get("name"))
    version = text_value(
        first_mapping_value(resource, "version", "versionName", "modelVersionName") or version_info.get("name")
    )
    resource_type = text_value(first_mapping_value(resource, "type", "modelType", "model_type") or model.get("type"))
    weight = text_value(first_mapping_value(resource, "weight", "strength"))
    base_model = text_value(first_mapping_value(resource, "baseModel", "base_model") or version_info.get("baseModel"))
    hash_value = text_value(
        first_mapping_value(resource, "hash", "modelVersionHash", "model_version_hash") or version_info.get("hash")
    )

    normalized: dict[str, str] = {}
    if name:
        normalized["name"] = name
    elif version_id:
        normalized["name"] = f"modelVersion {version_id}"
    else:
        normalized["name"] = "Unknown resource"
    if resource_type:
        normalized["type"] = resource_type
    if version:
        normalized["version"] = version
    if weight:
        normalized["weight"] = weight
    if model_id:
        normalized["model_id"] = model_id
        normalized["href"] = civitai_model_page_url(model_id, version_id)
    if version_id:
        normalized["model_version_id"] = version_id
        normalized.setdefault("href", f"{CIVITAI_API_BASE}/model-versions/{quote(version_id, safe='')}")
    if base_model:
        normalized["base_model"] = base_model
    if hash_value:
        normalized["hash"] = hash_value
    return normalized


def civitai_model_page_url(model_id: str, version_id: str | None = None) -> str:
    url = f"https://civitai.com/models/{quote(model_id, safe='')}"
    if version_id:
        return f"{url}?modelVersionId={quote(version_id, safe='')}"
    return url


def civitai_resource_needs_version_metadata(resource: dict[str, str]) -> bool:
    return bool(resource.get("model_version_id")) and (
        not resource.get("model_id") or not resource.get("name") or not resource.get("type")
    )


def civitai_version_resource(metadata: dict[str, Any], version_id: str) -> dict[str, str]:
    raw_model = metadata.get("model")
    model = raw_model if isinstance(raw_model, dict) else {}
    model_id = id_value(metadata.get("modelId") or model.get("id"))
    normalized = normalize_civitai_resource(
        {
            "name": model.get("name") or metadata.get("modelName"),
            "type": model.get("type") or metadata.get("modelType"),
            "modelId": model_id,
            "modelVersionId": metadata.get("id") or version_id,
            "modelVersionName": metadata.get("name"),
            "baseModel": metadata.get("baseModel"),
        }
    )
    if model_id:
        normalized["href"] = civitai_model_page_url(model_id, id_value(metadata.get("id") or version_id))
    normalized["availability"] = "available"
    return normalized


def merge_civitai_resource(existing: dict[str, str], enriched: dict[str, str]) -> dict[str, str]:
    merged = dict(existing)
    version_id = merged.get("model_version_id") or enriched.get("model_version_id")
    for key, value in enriched.items():
        is_placeholder_name = key == "name" and bool(version_id) and merged.get("name") == f"modelVersion {version_id}"
        if value and (not merged.get(key) or is_placeholder_name):
            merged[key] = value
    if merged.get("model_id"):
        merged["href"] = civitai_model_page_url(str(merged["model_id"]), merged.get("model_version_id"))
    return merged


def enrich_civitai_image_resources(
    session: requests.Session,
    job_id: int,
    resources: list[dict[str, str]],
) -> list[dict[str, str]]:
    cache: dict[str, dict[str, str] | None] = {}
    enriched_resources: list[dict[str, str]] = []
    for resource in resources:
        version_id = id_value(resource.get("model_version_id"))
        if not version_id or not civitai_resource_needs_version_metadata(resource):
            enriched_resources.append(resource)
            continue

        if version_id not in cache:
            meta_url = f"{CIVITAI_API_BASE}/model-versions/{quote(version_id, safe='')}"
            try:
                db.append_log(job_id, f"civitai.image.resource.metadata: {meta_url}")
                metadata = fetch_json(session, meta_url, job_id=job_id)
                cache[version_id] = civitai_version_resource(metadata, version_id)
            except Exception as exc:
                status_code = http_status_code(exc)
                if status_code in CIVITAI_RESOURCE_PERMANENT_HTTP_STATUSES:
                    cache[version_id] = mark_civitai_resource_unavailable(
                        {"model_version_id": version_id},
                        status_code=status_code,
                    )
                else:
                    cache[version_id] = None
                db.append_log(job_id, f"civitai.image.resource.metadata.warning version={version_id}: {exc}")

        enriched = cache.get(version_id)
        enriched_resources.append(merge_civitai_resource(resource, enriched) if enriched else resource)
    return enriched_resources


def normalize_civitai_image_resources(item: dict[str, Any], meta: dict[str, Any]) -> list[dict[str, str]]:
    resources = [normalize_civitai_resource(resource) for resource in civitai_raw_resource_lists(item, meta)]
    resources = [resource for resource in resources if any(resource.values())]

    existing_ids = {resource.get("model_version_id") for resource in resources if resource.get("model_version_id")}
    for version_id in civitai_image_model_version_ids(item, resources):
        if version_id in existing_ids:
            continue
        resources.append(
            {
                "name": f"modelVersion {version_id}",
                "model_version_id": version_id,
                "href": f"{CIVITAI_API_BASE}/model-versions/{quote(version_id, safe='')}",
            }
        )
        existing_ids.add(version_id)
    return resources


def civitai_image_model_version_ids(item: dict[str, Any], resources: list[dict[str, str]]) -> list[str]:
    values: list[Any] = []
    raw_ids = item.get("modelVersionIds") or item.get("model_version_ids")
    if isinstance(raw_ids, list):
        values.extend(raw_ids)
    elif raw_ids not in (None, ""):
        values.append(raw_ids)
    values.extend(resource.get("model_version_id") for resource in resources)

    ids: list[str] = []
    seen: set[str] = set()
    for value in values:
        version_id = id_value(value)
        if not version_id or version_id in seen:
            continue
        ids.append(version_id)
        seen.add(version_id)
    return ids


def civitai_image_original_url(item: dict[str, Any]) -> str | None:
    for key in ("url", "originalUrl", "imageUrl"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raw_image = item.get("image")
    if isinstance(raw_image, dict):
        for key in ("url", "originalUrl", "imageUrl"):
            value = raw_image.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def civitai_image_username(item: dict[str, Any]) -> str:
    username = text_value(item.get("username"))
    if username:
        return username
    raw_user = item.get("user")
    if isinstance(raw_user, dict):
        username = text_value(raw_user.get("username") or raw_user.get("name"))
        if username:
            return username
    return "unknown"


def civitai_image_base_model(meta: dict[str, Any], resources: list[dict[str, str]]) -> str | None:
    for resource in resources:
        base_model = resource.get("base_model")
        if base_model:
            return base_model
    value = first_mapping_value(meta, "baseModel", "base_model", "Model", "model")
    return text_value(value)


def civitai_remote_thumbnail_url(url: str | None) -> str | None:
    if not url:
        return None
    return url.replace("/original=true/", "/width=256/")


def build_civitai_copy_all_text(prompt: str | None, negative_prompt: str | None, metadata: list[dict[str, str]]) -> str:
    lines: list[str] = []
    if prompt:
        lines.append(prompt)
    if negative_prompt:
        lines.append(f"Negative prompt: {negative_prompt}")
    metadata_line = ", ".join(
        f"{item['label']}: {item['value']}"
        for item in metadata
        if item.get("label") and item.get("value")
    )
    if metadata_line:
        lines.append(metadata_line)
    return "\n".join(lines)


def normalize_civitai_image_record(
    item: dict[str, Any],
    *,
    source_url: str,
    raw_input: str,
) -> dict[str, Any]:
    meta = civitai_image_meta(item)
    prompt = text_value(first_mapping_value(meta, "prompt", "positivePrompt", "positive_prompt"))
    negative_prompt = text_value(first_mapping_value(meta, "negativePrompt", "negative_prompt"))
    metadata = civitai_generation_metadata(meta)
    resources = normalize_civitai_image_resources(item, meta)
    model_version_ids = civitai_image_model_version_ids(item, resources)
    original_url = civitai_image_original_url(item)
    image_id = id_value(item.get("id")) or "unknown"
    username = civitai_image_username(item)
    base_model = civitai_image_base_model(meta, resources)
    width = item.get("width")
    height = item.get("height")
    precision = f"{width} x {height}" if width and height else None
    generation_available = bool(prompt or negative_prompt or metadata or resources or model_version_ids)

    return {
        "source": "civitai",
        "kind": "civitai_image_page",
        "source_url": redact_sensitive_text(source_url) or source_url,
        "raw_input": redact_sensitive_text(raw_input) or raw_input,
        "image": {
            "id": image_id,
            "post_id": id_value(item.get("postId") or item.get("post_id")),
            "username": username,
            "width": width,
            "height": height,
            "created_at": item.get("createdAt") or item.get("created_at"),
            "nsfw_level": item.get("nsfwLevel") or item.get("nsfw_level"),
            "original_url": redact_sensitive_text(original_url) if original_url else None,
            "thumbnail_url": civitai_remote_thumbnail_url(redact_sensitive_text(original_url) if original_url else None),
        },
        "generation_data": {
            "available": generation_available,
            "prompt": {"text": prompt or ""},
            "negative_prompt": {"text": negative_prompt or ""},
            "copy_all_text": build_civitai_copy_all_text(prompt, negative_prompt, metadata),
            "metadata": metadata,
            "resources": resources,
            "model_version_ids": model_version_ids,
        },
        "resource_downloads": [],
        "local_files": {},
        "archive_info": {
            "model_title": f"Civitai image {image_id}",
            "model_category": "Civitai Image Page",
            "model_type": "Image",
            "base_model": base_model,
            "file_format": None,
            "precision": precision,
            "thumbnail_url": None,
        },
    }


def civitai_image_target(parsed: ParsedDownload, record: dict[str, Any]) -> Path:
    image = record.get("image") if isinstance(record.get("image"), dict) else {}
    username = str(image.get("username") or "unknown")
    image_id = str(image.get("id") or "unknown")
    return base_target(
        parsed,
        "civitai",
        "images",
        sanitize_segment(username, "unknown"),
        sanitize_segment(f"image_{image_id}", "image_unknown"),
    )


def civitai_image_extension(session: requests.Session, url: str, job_id: int) -> str:
    content_type = civitai_image_content_type(session, url, job_id)
    if content_type:
        media_type = content_type.split(";", 1)[0].strip().lower()
        extension = CIVITAI_IMAGE_CONTENT_TYPE_EXTENSIONS.get(media_type)
        if extension:
            return extension

    suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    if suffix in THUMBNAIL_IMAGE_EXTENSIONS:
        return suffix
    return ".jpg"


def civitai_image_content_type(session: requests.Session, url: str, job_id: int) -> str | None:
    if not bool_env("DOWNLOAD_ENABLE_HEAD_REQUESTS", True):
        return None
    response: requests.Response | None = None
    try:
        response = request_with_safety(
            session,
            "HEAD",
            url,
            job_id=job_id,
            timeout=(10, 30),
            allow_redirects=True,
            headers={"Accept-Encoding": "identity"},
        )
        if response.ok:
            content_type = response.headers.get("content-type")
            return str(content_type) if content_type else None
    except requests.RequestException:
        return None
    finally:
        if response is not None:
            response.close()
    return None


def civitai_image_archive_summary(record: dict[str, Any]) -> dict[str, Any]:
    image = record.get("image") if isinstance(record.get("image"), dict) else {}
    generation_data = record.get("generation_data") if isinstance(record.get("generation_data"), dict) else {}
    archive_info = record.get("archive_info") if isinstance(record.get("archive_info"), dict) else {}
    return {
        "source": "civitai",
        "kind": "civitai_image_page",
        "source_url": record.get("source_url"),
        "image": image,
        "generation_data": {
            "available": generation_data.get("available"),
            "prompt": generation_data.get("prompt"),
            "negative_prompt": generation_data.get("negative_prompt"),
            "copy_all_text": generation_data.get("copy_all_text"),
            "metadata": generation_data.get("metadata"),
            "resources": generation_data.get("resources"),
            "model_version_ids": generation_data.get("model_version_ids"),
        },
        "resource_downloads": record.get("resource_downloads"),
        "local_files": record.get("local_files"),
        "archive_info": archive_info,
    }


def civitai_resource_retry_delay_seconds() -> int:
    return nonnegative_int_setting(
        "CIVITAI_IMAGE_RESOURCE_RETRY_DELAY_SECONDS",
        CIVITAI_RESOURCE_RETRY_DELAY_DEFAULT_SECONDS,
    )


def civitai_image_max_resource_jobs() -> int:
    return nonnegative_int_env("CIVITAI_IMAGE_MAX_RESOURCE_JOBS", CIVITAI_IMAGE_MAX_RESOURCE_JOBS_DEFAULT)


def civitai_resource_download_entry(
    resource: dict[str, Any],
    version_id: str,
    *,
    status: str,
    **fields: Any,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "model_version_id": version_id,
        "model_id": id_value(resource.get("model_id")),
        "name": resource.get("name"),
        "type": resource.get("type"),
        "status": status,
    }
    for key, value in fields.items():
        if value not in (None, ""):
            entry[key] = value
    return entry


def is_civitai_model_file(path: Path) -> bool:
    return path.suffix.lower() in CIVITAI_MODEL_FILE_EXTENSIONS


def civitai_target_has_model_file(path: Path, limit: int = 10000) -> bool:
    try:
        if path.is_file():
            return is_civitai_model_file(path)
        if not path.is_dir():
            return False
        for index, item in enumerate(path.rglob("*")):
            if index >= limit:
                return False
            if item.is_file() and not item.is_symlink() and is_civitai_model_file(item):
                return True
    except OSError:
        return False
    return False


def civitai_health_target_path(path: Path) -> str:
    return data_root_relative_path(path) or str(path)


def civitai_version_ids_from_job(job: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    parsed_payload = parse_metadata_json(job.get("parsed_json"))
    if parsed_payload.get("source") == "civitai":
        version_id = id_value(parsed_payload.get("civitai_version_id"))
        if version_id:
            ids.add(version_id)

    metadata = parse_metadata_json(job.get("metadata_json"))
    for key in ("version_id", "model_version_id", "modelVersionId"):
        version_id = id_value(metadata.get(key))
        if version_id:
            ids.add(version_id)
    nested = metadata.get("metadata")
    if isinstance(nested, dict):
        for key in ("id", "model_version_id", "modelVersionId"):
            version_id = id_value(nested.get(key))
            if version_id:
                ids.add(version_id)
    return ids


def civitai_metadata_version_ids(metadata: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("version_id", "model_version_id", "modelVersionId"):
        version_id = id_value(metadata.get(key))
        if version_id:
            ids.add(version_id)
    nested = metadata.get("metadata")
    if isinstance(nested, dict):
        for key in ("id", "model_version_id", "modelVersionId"):
            version_id = id_value(nested.get(key))
            if version_id:
                ids.add(version_id)
    return ids


def job_updated_at(job: dict[str, Any]) -> datetime | None:
    value = job.get("updated_at") or job.get("created_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def civitai_job_failure_is_permanent(job: dict[str, Any]) -> bool:
    error = str(job.get("error") or "")
    return any(f" {code} " in f" {error} " or f"HTTP {code}" in error for code in CIVITAI_RESOURCE_PERMANENT_HTTP_STATUSES)


def civitai_recent_failed_resource_state(job: dict[str, Any], version_id: str) -> dict[str, Any] | None:
    if civitai_job_failure_is_permanent(job):
        return {
            "status": "unavailable",
            "reason": "previous permanent failure",
            "existing_job_id": int(job["id"]) if job.get("id") is not None else None,
        }

    retry_delay = civitai_resource_retry_delay_seconds()
    if retry_delay <= 0:
        return None
    updated_at = job_updated_at(job)
    if updated_at is None:
        return {
            "status": "retry_deferred",
            "reason": "previous failure",
            "existing_job_id": int(job["id"]) if job.get("id") is not None else None,
        }
    elapsed = (datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)).total_seconds()
    remaining = int(max(0, retry_delay - elapsed))
    if remaining <= 0:
        return None
    return {
        "status": "retry_deferred",
        "reason": "previous failure retry cooldown",
        "retry_after_seconds": remaining,
        "existing_job_id": int(job["id"]) if job.get("id") is not None else None,
    }


def civitai_resource_state_from_jobs(version_id: str) -> dict[str, Any] | None:
    active_state: dict[str, Any] | None = None
    failed_state: dict[str, Any] | None = None
    for job in db.list_jobs(limit=5000):
        if str(job.get("source") or "") != "civitai":
            continue
        if version_id not in civitai_version_ids_from_job(job):
            continue

        job_id = int(job["id"]) if job.get("id") is not None else None
        status = str(job.get("status") or "")
        target_dir = str(job.get("target_dir") or "").strip()
        target = Path(target_dir) if target_dir else None
        if status == "done" and target is not None and civitai_target_has_model_file(target):
            return {
                "status": "present",
                "reason": "already downloaded",
                "target_path": civitai_health_target_path(target),
                "existing_job_id": job_id,
            }
        if status in CIVITAI_RESOURCE_ACTIVE_STATUSES and active_state is None:
            active_state = {
                "status": "already_queued",
                "reason": f"existing job is {status}",
                "existing_job_id": job_id,
            }
        if status in CIVITAI_RESOURCE_FAILED_STATUSES and failed_state is None:
            failed_state = civitai_recent_failed_resource_state(job, version_id)
    return active_state or failed_state


def civitai_resource_state_from_sidecars(version_id: str) -> dict[str, Any] | None:
    try:
        sidecars = DATA_ROOT.rglob("_civitai_metadata.json")
        for sidecar in sidecars:
            try:
                if not sidecar.is_file() or sidecar.is_symlink():
                    continue
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or version_id not in civitai_metadata_version_ids(payload):
                continue
            target = sidecar.parent
            if civitai_target_has_model_file(target):
                return {
                    "status": "present",
                    "reason": "already downloaded",
                    "target_path": civitai_health_target_path(target),
                }
    except OSError:
        return None
    return None


def civitai_existing_resource_state(version_id: str) -> dict[str, Any] | None:
    job_state = civitai_resource_state_from_jobs(version_id)
    if job_state and job_state.get("status") == "present":
        return job_state
    sidecar_state = civitai_resource_state_from_sidecars(version_id)
    if sidecar_state:
        return sidecar_state
    return job_state


def preflight_civitai_resource(
    session: requests.Session | None,
    job_id: int,
    resource: dict[str, Any],
    version_id: str,
) -> dict[str, Any] | None:
    if resource.get("availability") == "unavailable":
        return {
            "status": "unavailable",
            "reason": resource.get("unavailable_reason") or "resource unavailable",
        }
    if resource.get("availability") == "available" or session is None:
        return None

    meta_url = f"{CIVITAI_API_BASE}/model-versions/{quote(version_id, safe='')}"
    try:
        db.append_log(job_id, f"civitai.image.resource.preflight: {meta_url}")
        metadata = fetch_json(session, meta_url, job_id=job_id)
    except Exception as exc:
        status_code = http_status_code(exc)
        if status_code in CIVITAI_RESOURCE_PERMANENT_HTTP_STATUSES:
            reason = civitai_resource_unavailable_reason(status_code)
            resource.update(mark_civitai_resource_unavailable(resource, status_code=status_code, reason=reason))
            return {"status": "unavailable", "reason": reason, "status_code": status_code}
        db.append_log(job_id, f"civitai.image.resource.preflight.warning version={version_id}: {exc}")
        return None

    resource.update(merge_civitai_resource(resource, civitai_version_resource(metadata, version_id)))
    return None


def create_civitai_image_resource_jobs(
    job_id: int,
    resources: list[dict[str, Any]],
    *,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    downloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    queued_count = 0
    max_resource_jobs = civitai_image_max_resource_jobs()
    for resource in resources:
        version_id = id_value(resource.get("model_version_id"))
        if not version_id or version_id in seen:
            continue
        seen.add(version_id)

        existing_state = civitai_existing_resource_state(version_id)
        if existing_state:
            entry = civitai_resource_download_entry(resource, version_id, **existing_state)
            downloads.append(entry)
            db.append_log(
                job_id,
                f"civitai.image.resource.skip modelVersionId={version_id} status={entry['status']} "
                f"reason={entry.get('reason') or '-'}",
            )
            continue

        preflight_state = preflight_civitai_resource(session, job_id, resource, version_id)
        if preflight_state:
            entry = civitai_resource_download_entry(resource, version_id, **preflight_state)
            downloads.append(entry)
            db.append_log(
                job_id,
                f"civitai.image.resource.skip modelVersionId={version_id} status={entry['status']} "
                f"reason={entry.get('reason') or '-'}",
            )
            continue

        if queued_count >= max_resource_jobs:
            entry = civitai_resource_download_entry(
                resource,
                version_id,
                status="skipped_limit",
                reason=f"resource queue limit reached ({max_resource_jobs})",
            )
            downloads.append(entry)
            db.append_log(
                job_id,
                f"civitai.image.resource.skip modelVersionId={version_id} status=skipped_limit "
                f"reason=resource queue limit reached ({max_resource_jobs})",
            )
            continue

        model_id = id_value(resource.get("model_id"))
        if model_id:
            raw_input = f"https://civitai.com/models/{quote(model_id, safe='')}?modelVersionId={quote(version_id, safe='')}"
        else:
            raw_input = f"{CIVITAI_API_BASE}/model-versions/{quote(version_id, safe='')}"

        child = ParsedDownload(
            source="civitai",
            raw_input=raw_input,
            civitai_model_id=model_id,
            civitai_version_id=version_id,
        )
        child_job_id = db.create_job(child)
        enqueue_job(child_job_id)
        queued_count += 1
        entry = civitai_resource_download_entry(
            resource,
            version_id,
            status="queued",
            child_job_id=child_job_id,
        )
        downloads.append(entry)
        db.append_log(
            job_id,
            f"civitai.image.resource.queue modelVersionId={version_id} child_job_id={child_job_id} "
            f"type={resource.get('type') or 'unknown'}",
        )
    return downloads


def update_civitai_image_job(job_id: int, target: Path, saved: Path | None, record: dict[str, Any]) -> None:
    archive_info = record.get("archive_info") if isinstance(record.get("archive_info"), dict) else {}
    metadata_json = json.dumps(redact_metadata(civitai_image_archive_summary(record)), ensure_ascii=False)
    db.update_job(
        job_id,
        target_dir=str(target),
        filename=saved.name if saved else None,
        model_title=archive_info.get("model_title"),
        model_category=archive_info.get("model_category"),
        model_type=archive_info.get("model_type"),
        base_model=archive_info.get("base_model"),
        file_format=archive_info.get("file_format"),
        precision=archive_info.get("precision"),
        thumbnail_url=archive_info.get("thumbnail_url"),
        metadata_json=metadata_json,
    )


def download_civitai_image_page(job_id: int, parsed: ParsedDownload) -> None:
    image_id = id_value(getattr(parsed, "civitai_image_id", None))
    if not image_id:
        raise ValueError("Civitai image_id가 없습니다.")

    token = db.get_secret("CIVITAI_TOKEN")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, **auth_headers(token)})
    source_url = civitai_image_source_url(parsed, image_id)

    item = fetch_civitai_image_item(session, image_id, job_id)
    record = normalize_civitai_image_record(item, source_url=source_url, raw_input=parsed.raw_input)
    generation_data = record.get("generation_data") if isinstance(record.get("generation_data"), dict) else {}
    resources = generation_data.get("resources") if isinstance(generation_data.get("resources"), list) else []
    resources = enrich_civitai_image_resources(session, job_id, resources)
    generation_data["resources"] = resources
    generation_data["model_version_ids"] = civitai_image_model_version_ids(item, resources)
    record["generation_data"] = generation_data
    db.append_log(job_id, f"civitai.image.metadata.ok image_id={image_id} resources={len(resources)}")
    if not generation_data.get("available"):
        db.append_log(job_id, f"civitai.image.metadata.warning image_id={image_id} generation data unavailable")

    image_url = civitai_image_original_url(item)
    if not image_url:
        raise ValueError(f"Civitai image {image_id} has no downloadable image URL.")

    target = civitai_image_target(parsed, record)
    target.mkdir(parents=True, exist_ok=True)
    extension = civitai_image_extension(session, image_url, job_id)
    filename = sanitize_segment(f"image_{image_id}{extension}", f"image_{image_id}.jpg")
    archive_info = record.get("archive_info") if isinstance(record.get("archive_info"), dict) else {}
    archive_info["file_format"] = extension.lstrip(".")
    record["archive_info"] = archive_info
    record["local_files"] = {"primary_image": filename}
    update_civitai_image_job(job_id, target, None, record)

    check_job_control(job_id)
    saved = stream_download(job_id, session, image_url, target, filename_override=filename)
    record["local_files"] = {"primary_image": saved.name}
    archive_info["thumbnail_url"] = thumbnail_url_for_path(saved) or thumbnail_url_for_path(target) or archive_info.get("thumbnail_url")
    record["archive_info"] = archive_info
    db.append_log(job_id, f"civitai.image.asset.saved file={saved.name}")
    write_metadata(target, CIVITAI_IMAGE_METADATA_FILENAME, record)
    update_civitai_image_job(job_id, target, saved, record)

    resource_downloads = create_civitai_image_resource_jobs(job_id, resources, session=session)
    record["resource_downloads"] = resource_downloads
    write_metadata(target, CIVITAI_IMAGE_METADATA_FILENAME, record)
    update_civitai_image_job(job_id, target, saved, record)
    queued_resources = sum(1 for download in resource_downloads if download.get("status") == "queued")
    db.append_log(
        job_id,
        f"civitai.image.done image_id={image_id} resource_entries={len(resource_downloads)} "
        f"queued_resources={queued_resources}",
    )


def download_civitai(job_id: int, parsed: ParsedDownload) -> None:
    if getattr(parsed, "civitai_image_id", None):
        download_civitai_image_page(job_id, parsed)
        return

    token = db.get_secret("CIVITAI_TOKEN")
    headers = auth_headers(token)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, **headers})

    metadata: dict[str, Any] = {}
    model_name = None
    version_id = parsed.civitai_version_id
    file_selector = civitai_file_selector(parsed)

    if version_id:
        meta_url = f"{CIVITAI_API_BASE}/model-versions/{version_id}"
        db.append_log(job_id, f"Civitai metadata: {meta_url}")
        metadata = fetch_json(session, meta_url, job_id=job_id)
        raw_model = metadata.get("model")
        model_info = raw_model if isinstance(raw_model, dict) else {}
        model_name = model_info.get("name") or metadata.get("modelName") or metadata.get("name")
    elif parsed.civitai_hash:
        meta_url = f"{CIVITAI_API_BASE}/model-versions/by-hash/{parsed.civitai_hash}"
        db.append_log(job_id, f"Civitai metadata by hash: {meta_url}")
        metadata = fetch_json(session, meta_url, job_id=job_id)
        version_id = str(metadata.get("id") or "")
        raw_model = metadata.get("model")
        model_info = raw_model if isinstance(raw_model, dict) else {}
        model_name = model_info.get("name") or metadata.get("modelName") or metadata.get("name")
    elif parsed.civitai_model_id:
        meta_url = f"{CIVITAI_API_BASE}/models/{parsed.civitai_model_id}"
        db.append_log(job_id, f"Civitai metadata: {meta_url}")
        metadata = fetch_json(session, meta_url, job_id=job_id)
        model_name = metadata.get("name")
        model_type = metadata.get("type")
        versions = metadata.get("modelVersions") or []
        if not versions:
            raise ValueError("Civitai 모델 버전 목록이 비어 있습니다. modelVersionId가 포함된 URL을 입력해 보세요.")
        # Civitai model page URL에 버전이 없을 때는 API 응답의 첫 번째 버전을 사용합니다.
        version_id = str(versions[0].get("id"))
        metadata = versions[0] | {"model": {"id": parsed.civitai_model_id, "name": model_name, "type": model_type}}
        db.append_log(job_id, f"modelVersionId가 없어 첫 번째 버전 선택: {version_id}")
    else:
        raise ValueError("Civitai model_id 또는 modelVersionId가 없습니다.")

    if not version_id:
        raise ValueError("Civitai modelVersionId를 결정하지 못했습니다.")

    check_job_control(job_id)
    archive_info = classify_civitai(metadata, version_id, model_name, file_selector)
    raw_primary_file = archive_info.get("primary_file")
    primary_file = raw_primary_file if isinstance(raw_primary_file, dict) else {}

    download_urls = civitai_download_urls(parsed, metadata, primary_file, version_id)

    target = base_target(parsed, *archive_info["target_parts"], archive_info=archive_info)
    target.mkdir(parents=True, exist_ok=True)
    update_job_archive_info(job_id, target, archive_info, metadata)

    write_metadata(
        target,
        "_civitai_metadata.json",
        {
            **metadata_stamp(),
            "source": "civitai",
            "model_name": model_name,
            "model_id": parsed.civitai_model_id,
            "version_id": version_id,
            "hash": parsed.civitai_hash,
            "file_selector": file_selector,
            "raw_input": parsed.raw_input,
            "archive_info": archive_info,
            "metadata": metadata,
        },
    )

    if not download_urls:
        raise ValueError("Civitai download URL을 찾지 못했습니다.")

    saved = None
    last_error: Exception | None = None
    for index, download_url in enumerate(download_urls, start=1):
        try:
            check_job_control(job_id)
            db.append_log(
                job_id,
                f"Civitai file download: version={version_id} url={redact_sensitive_text(download_url)}",
            )
            saved = stream_download(job_id, session, download_url, target)
            break
        except requests.RequestException as exc:
            last_error = exc
            db.append_log(job_id, f"Civitai download failed ({index}/{len(download_urls)}): {exc}")

    if saved is None:
        raise RuntimeError(f"Civitai download failed: {last_error}")

    db.update_job(job_id, filename=saved.name)


def download_generic(job_id: int, parsed: ParsedDownload) -> None:
    if not parsed.url:
        raise ValueError("URL이 없습니다.")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    target = base_target(parsed, "generic")
    target.mkdir(parents=True, exist_ok=True)
    check_job_control(job_id)
    db.update_job(job_id, target_dir=str(target), model_category="Generic URL", model_title=parsed.url)
    write_metadata(
        target,
        "_generic_metadata.json",
        {**metadata_stamp(), "source": "generic", "url": parsed.url, "raw_input": parsed.raw_input},
    )
    saved = stream_download(job_id, session, parsed.url, target)
    db.update_job(job_id, filename=saved.name)


def download_comfyui(job_id: int, parsed: ParsedDownload) -> None:
    if not parsed.comfyui_workflow_url:
        raise ValueError("ComfyUI workflow URL이 없습니다.")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    db.append_log(job_id, f"ComfyUI workflow download: {redact_sensitive_text(parsed.comfyui_workflow_url)}")
    data, filename = fetch_workflow_bytes(session, parsed.comfyui_workflow_url, job_id, parsed.comfyui_workflow_filename)
    try:
        result = save_workflow_bundle(
            data,
            filename,
            parsed.raw_input,
            DATA_ROOT,
            target_subdir=parsed.target_subdir,
        )
    except WorkflowParseError as exc:
        raise ValueError(str(exc)) from exc
    update_job_workflow_info(job_id, result, data_size=len(data), source_url=parsed.comfyui_workflow_url)
    db.append_log(job_id, f"saved workflow: {result['workflow_path']} ({human_bytes(len(data))})")


def download_hitomi(job_id: int, parsed: ParsedDownload) -> None:
    if parsed.hitomi_listing_url and not parsed.hitomi_gallery_id:
        download_hitomi_listing(job_id, parsed)
        return

    gallery_id = parsed.hitomi_gallery_id
    if not gallery_id:
        raise ValueError("Hitomi gallery ID is missing.")

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Origin": "https://hitomi.la",
            "Referer": f"https://hitomi.la/reader/{gallery_id}.html",
        }
    )

    backend = hitomi_backend()
    if backend in {"gallery-dl", "auto"}:
        if gallery_dl_available():
            try:
                download_hitomi_gallery_dl(job_id, parsed, session)
                return
            except JobControlStop:
                raise
            except Exception as exc:  # noqa: BLE001 - fallback to built-in extractor when configured
                if backend == "gallery-dl":
                    raise
                db.append_log(job_id, f"gallery-dl failed; falling back to built-in hitomi downloader: {exc}")
        elif backend == "gallery-dl":
            raise RuntimeError("gallery-dl is not available in this container.")

    metadata = fetch_hitomi_gallery_info(session, gallery_id, job_id)
    files = [item for item in metadata.get("files") or [] if isinstance(item, dict) and item.get("hash")]
    if not files:
        raise ValueError("Hitomi gallery file list is empty.")

    gg = fetch_hitomi_gg(session, job_id)
    title = str(metadata.get("title") or f"gallery_{gallery_id}")
    target_name = sanitize_segment(f"{gallery_id}-{title}", f"gallery_{gallery_id}")
    target_root = base_target(parsed, "hitomi")
    target = safe_join(target_root, target_name)
    target.mkdir(parents=True, exist_ok=True)

    source_url = parsed.hitomi_gallery_url or f"https://hitomi.la/galleries/{gallery_id}.html"
    db.update_job(
        job_id,
        target_dir=str(target),
        model_title=title,
        model_category="Hitomi Gallery",
        model_type=str(metadata.get("type") or "Gallery"),
        base_model=str(metadata.get("language") or metadata.get("language_localname") or ""),
        file_format=hitomi_preferred_format().upper(),
        precision=f"{len(files)} pages",
        metadata_json=json.dumps(redact_metadata(hitomi_metadata_summary(metadata)), ensure_ascii=False),
    )
    write_metadata(
        target,
        "_hitomi_metadata.json",
        {
            **metadata_stamp(),
            "source": "hitomi",
            "gallery_id": gallery_id,
            "source_url": source_url,
            "raw_input": parsed.raw_input,
            "metadata": metadata,
        },
    )
    write_metadata(
        target,
        "_archive_metadata.json",
        {
            **metadata_stamp(),
            "source": "hitomi",
            "gallery_id": gallery_id,
            "source_url": source_url,
            "raw_input": parsed.raw_input,
            "title": title,
            "page_count": len(files),
        },
    )

    total_saved = 0
    thumbnail_url = ""
    db.append_log(job_id, f"Hitomi gallery download: {gallery_id} ({len(files)} pages)")
    for index, image in enumerate(files, start=1):
        check_job_control(job_id)
        original_name = str(image.get("name") or f"{index:03d}.jpg")
        saved = None
        last_error: Exception | None = None
        for image_url, extension in hitomi_image_candidates(image, gg):
            filename = hitomi_output_filename(index, original_name, extension)
            try:
                db.append_log(job_id, f"Hitomi page {index}/{len(files)}: {filename}")
                saved = stream_download(job_id, session, image_url, target, filename_override=filename)
                thumbnail_url = thumbnail_url or thumbnail_url_for_path(saved)
                break
            except requests.RequestException as exc:
                last_error = exc
                db.append_log(job_id, f"Hitomi page candidate failed: {exc}")
        if saved is None:
            raise RuntimeError(f"Hitomi page {index} download failed: {last_error}")
        total_saved += saved.stat().st_size
        db.update_job(
            job_id,
            filename=saved.name,
            progress_bytes=total_saved,
            total_bytes=None,
            thumbnail_url=thumbnail_url,
        )

    final_size = directory_size(target)
    db.update_job(
        job_id,
        filename=f"{len(files)} pages",
        progress_bytes=final_size,
        total_bytes=final_size,
        thumbnail_url=thumbnail_url or thumbnail_url_for_path(target) or None,
    )
    db.append_log(job_id, f"saved gallery: {target} ({human_bytes(final_size)})")


def download_hitomi_listing(job_id: int, parsed: ParsedDownload) -> None:
    source_url = parsed.hitomi_listing_url
    if not source_url:
        raise ValueError("Hitomi listing URL is missing.")
    if not gallery_dl_available():
        raise RuntimeError("gallery-dl is not available in this container.")

    target_root = base_target(parsed, "hitomi")
    target = safe_join(target_root, "listings", hitomi_listing_slug(source_url, parsed.hitomi_listing_kind))
    target.mkdir(parents=True, exist_ok=True)
    db.update_job(
        job_id,
        target_dir=str(target),
        model_title=hitomi_listing_title(source_url),
        model_category="Hitomi Listing",
        model_type=parsed.hitomi_listing_kind or "listing",
        file_format="queue",
        filename="discovering galleries",
    )
    db.append_log(job_id, f"Hitomi listing discovery: {redact_sensitive_text(source_url)}")

    gallery_urls = unique_hitomi_gallery_urls(discover_hitomi_listing_gallery_urls(job_id, source_url))
    entries = create_hitomi_listing_gallery_jobs(job_id, parsed, gallery_urls)
    queued = [entry for entry in entries if entry["status"] == "queued"]
    skipped = [entry for entry in entries if entry["status"] != "queued"]
    capped_count = max(0, len(gallery_urls) - len(entries))
    payload = {
        **metadata_stamp(),
        "source": "hitomi",
        "kind": parsed.hitomi_listing_kind or "listing",
        "source_url": source_url,
        "raw_input": parsed.raw_input,
        "gallery_dl_version": gallery_dl_version(),
        "discovered_count": len(gallery_urls),
        "processed_count": len(entries),
        "queued_count": len(queued),
        "skipped_count": len(skipped),
        "capped_count": capped_count,
        "queue_limit": hitomi_listing_max_galleries(),
        "galleries": entries,
    }
    write_metadata(target, "_hitomi_listing_metadata.json", payload)
    metadata_summary = {
        key: value
        for key, value in payload.items()
        if key != "galleries"
    }
    precision = f"{len(queued)} queued, {len(skipped)} skipped"
    if capped_count:
        precision = f"{precision}, {capped_count} capped"
    db.update_job(
        job_id,
        filename=f"{len(queued)} queued / {len(gallery_urls)} discovered",
        progress_bytes=len(queued),
        total_bytes=len(gallery_urls),
        precision=precision,
        metadata_json=json.dumps(redact_metadata(metadata_summary), ensure_ascii=False),
    )
    db.append_log(
        job_id,
        (
            "queued Hitomi listing galleries: "
            f"queued={len(queued)} skipped={len(skipped)} capped={capped_count} discovered={len(gallery_urls)}"
        ),
    )


def hitomi_listing_slug(source_url: str, kind: str | None = None) -> str:
    parsed = urlparse(source_url)
    path = unquote(parsed.path.strip("/")) or "listing"
    prefix = sanitize_segment(kind or "listing", "listing")
    if parsed.query:
        return sanitize_segment(f"{prefix}-{path}-{stable_hash(parsed.query, 8)}", "listing")
    return sanitize_segment(f"{prefix}-{path}", "listing")


def hitomi_listing_title(source_url: str) -> str:
    parsed = urlparse(source_url)
    name = Path(unquote(parsed.path.strip("/"))).name or "listing"
    if parsed.query:
        return f"{name}?{stable_hash(parsed.query, 8)}"
    return name


def discover_hitomi_listing_gallery_urls(job_id: int, source_url: str) -> list[str]:
    command = gallery_dl_get_urls_command(source_url)
    db.append_log(
        job_id,
        (
            f"gallery-dl discovery start: source={redact_sensitive_text(source_url)} "
            f"version={gallery_dl_version()} auth={gallery_dl_auth_summary(source_url)}"
        ),
    )
    urls = run_gallery_dl_get_urls_process(job_id, command)
    if not urls:
        raise RuntimeError("No Hitomi gallery URLs were discovered from the listing.")
    db.append_log(job_id, f"gallery-dl discovery found {len(urls)} gallery URLs")
    return urls


def gallery_dl_get_urls_command(source_url: str) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "gallery_dl",
        "--config-ignore",
        "--no-input",
        "-g",
    ]
    sleep_request = os.getenv("GALLERY_DL_SLEEP_REQUEST_SECONDS", "").strip()
    if sleep_request:
        command.extend(["--sleep-request", sleep_request])
    command.extend(gallery_dl_auth_args())
    command.append(gallery_dl_extractor_url(source_url))
    return command


def run_gallery_dl_get_urls_process(job_id: int, command: list[str]) -> list[str]:
    process: subprocess.Popen[str] | None = None
    urls: list[str] = []
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **gallery_dl_process_kwargs(),
        )
        output_queue = process_output_queue()
        output_done = process.stdout is None
        if process.stdout:
            reader = threading.Thread(
                target=read_process_output,
                args=(process.stdout, output_queue),
                name=f"gallery-dl-discovery-output-{job_id}",
                daemon=True,
            )
            reader.start()

        while True:
            check_job_control(job_id)
            try:
                event = output_queue.get(timeout=1.0)
            except queue.Empty:
                event = None
            if event is not None and event[0] == "done":
                output_done = True
            elif event is not None and event[1] is not None:
                message = event[1].strip()
                if not message:
                    continue
                gallery_url = normalized_hitomi_gallery_url_from_text(message)
                if gallery_url:
                    urls.append(gallery_url)
                else:
                    db.append_log(job_id, f"gallery-dl.discovery: {message[:1000]}")

            if process.poll() is not None and output_done and output_queue.empty():
                break

        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(f"gallery-dl discovery exited with code {return_code}")
    except JobControlStop:
        if process and process.poll() is None:
            stop_gallery_dl_process(job_id, process)
        raise
    except Exception:
        if process and process.poll() is None:
            stop_gallery_dl_process(job_id, process)
        raise

    return unique_hitomi_gallery_urls(urls)


def normalized_hitomi_gallery_url_from_text(value: str) -> str | None:
    match = HITOMI_GALLERY_OUTPUT_RE.search(value)
    if not match:
        return None
    return hitomi_gallery_url_for_id(match.group("id"))


def unique_hitomi_gallery_urls(urls: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        gallery_id = hitomi_gallery_id_from_url(url)
        if not gallery_id or gallery_id in seen:
            continue
        seen.add(gallery_id)
        unique.append(hitomi_gallery_url_for_id(gallery_id))
    return unique


def hitomi_gallery_url_for_id(gallery_id: str) -> str:
    return f"https://hitomi.la/galleries/{quote(str(gallery_id), safe='')}.html"


def hitomi_gallery_id_from_url(url: str) -> str | None:
    match = HITOMI_GALLERY_OUTPUT_RE.search(url)
    return match.group("id") if match else None


def create_hitomi_listing_gallery_jobs(
    job_id: int,
    parsed: ParsedDownload,
    gallery_urls: list[str],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    max_galleries = hitomi_listing_max_galleries()
    for gallery_url in gallery_urls:
        gallery_id = hitomi_gallery_id_from_url(gallery_url)
        if not gallery_id or gallery_id in seen:
            continue
        seen.add(gallery_id)
        if len(entries) >= max_galleries:
            db.append_log(job_id, f"hitomi.listing.limit reached max_galleries={max_galleries}")
            break
        existing = hitomi_existing_gallery_state(gallery_id)
        if existing:
            entry = {
                "gallery_id": gallery_id,
                "gallery_url": gallery_url,
                **existing,
            }
            entries.append(entry)
            db.append_log(
                job_id,
                f"hitomi.listing.skip gallery_id={gallery_id} status={entry['status']} "
                f"reason={entry.get('reason') or '-'}",
            )
            continue

        child = ParsedDownload(
            source="hitomi",
            raw_input=gallery_url,
            target_subdir=parsed.target_subdir,
            hitomi_gallery_id=gallery_id,
            hitomi_gallery_url=gallery_url,
        )
        child_job_id = db.create_job(child)
        enqueue_job(child_job_id)
        entries.append(
            {
                "gallery_id": gallery_id,
                "gallery_url": gallery_url,
                "status": "queued",
                "child_job_id": child_job_id,
            }
        )
        db.append_log(job_id, f"hitomi.listing.queue gallery_id={gallery_id} child_job_id={child_job_id}")
    return entries


def hitomi_listing_max_galleries() -> int:
    return nonnegative_int_env("HITOMI_LISTING_MAX_GALLERIES", HITOMI_LISTING_MAX_GALLERIES_DEFAULT)


def hitomi_existing_gallery_state(gallery_id: str) -> dict[str, Any] | None:
    for job in db.list_jobs(limit=5000):
        if str(job.get("source") or "") != "hitomi":
            continue
        if hitomi_gallery_id_from_job(job) != gallery_id:
            continue
        status = str(job.get("status") or "")
        if status not in HITOMI_ACTIVE_OR_PRESENT_STATUSES:
            continue
        job_id = int(job["id"]) if job.get("id") is not None else None
        if status == "done":
            state: dict[str, Any] = {
                "status": "present",
                "reason": "already downloaded",
                "existing_job_id": job_id,
            }
            target_dir = str(job.get("target_dir") or "").strip()
            if target_dir:
                state["target_path"] = data_root_relative_path(Path(target_dir)) or target_dir
            return state
        return {
            "status": "already_queued",
            "reason": f"existing job is {status}",
            "existing_job_id": job_id,
        }
    return None


def hitomi_gallery_id_from_job(job: dict[str, Any]) -> str | None:
    payload = parse_metadata_json(job.get("parsed_json"))
    if payload.get("source") == "hitomi":
        gallery_id = payload.get("hitomi_gallery_id")
        if gallery_id:
            return str(gallery_id)
    metadata = parse_metadata_json(job.get("metadata_json"))
    gallery_id = metadata.get("gallery_id")
    if gallery_id:
        return str(gallery_id)
    nested = metadata.get("metadata")
    if isinstance(nested, dict) and nested.get("id"):
        return str(nested["id"])
    return None


def download_gallerydl(job_id: int, parsed: ParsedDownload) -> None:
    if not parsed.gallerydl_url:
        raise ValueError("gallery-dl URL is missing.")
    uses_ytdlp = gallery_dl_uses_ytdlp(parsed.gallerydl_url)
    if not uses_ytdlp and not gallery_dl_available():
        raise RuntimeError("gallery-dl is not available in this container.")
    if uses_ytdlp and not yt_dlp_available():
        raise RuntimeError("yt-dlp is not available in this container.")

    source_url = parsed.gallerydl_url
    host, slug = gallery_dl_target_parts(source_url)
    target_root = base_target(parsed, "gallery-dl")
    target = safe_join(target_root, host, slug)
    target.mkdir(parents=True, exist_ok=True)

    db.update_job(
        job_id,
        target_dir=str(target),
        model_title=slug,
        model_category="gallery-dl",
        model_type=host,
        file_format="gallery-dl",
    )
    write_metadata(
        target,
        "_archive_metadata.json",
        {
            **metadata_stamp(),
            "source": "gallery-dl",
            "source_url": source_url,
            "raw_input": parsed.raw_input,
            "host": host,
            "gallery_dl_version": None if uses_ytdlp else gallery_dl_version(),
            "yt_dlp_version": yt_dlp_version() if uses_ytdlp else None,
        },
    )

    try:
        if uses_ytdlp:
            command = yt_dlp_command(source_url, target)
            log_ytdlp_start(job_id, source_url, target)
            run_ytdlp_process(job_id, command, target)
        else:
            command = gallery_dl_command(source_url, target)
            log_gallery_dl_start(job_id, source_url, target)
            run_gallery_dl_process(job_id, command, target)
    except Exception:
        cleanup_empty_gallery_archive_target(job_id, target)
        raise

    final_size = directory_size(target)
    files = gallery_dl_downloaded_files(target)
    if not files:
        cleanup_empty_gallery_archive_target(job_id, target)
        raise RuntimeError("No media files were downloaded. Check the job log for extractor or network errors.")
    thumbnail_url = thumbnail_url_for_path(target)
    db.update_job(
        job_id,
        filename=f"{len(files) or 'downloaded'} files",
        progress_bytes=final_size,
        total_bytes=final_size,
        precision=f"{len(files)} files" if files else None,
        thumbnail_url=thumbnail_url or None,
    )
    db.append_log(job_id, f"saved gallery-dl archive: {target} ({human_bytes(final_size)})")


def hitomi_backend() -> str:
    value = os.getenv("HITOMI_BACKEND", "auto").strip().lower()
    return value if value in {"gallery-dl", "builtin", "auto"} else "auto"


def gallery_dl_available() -> bool:
    return not gallery_dl_version().startswith("unavailable")


def gallery_dl_version() -> str:
    global _GALLERY_DL_VERSION_CACHE
    with _GALLERY_DL_VERSION_LOCK:
        if _GALLERY_DL_VERSION_CACHE is not None:
            return _GALLERY_DL_VERSION_CACHE

    try:
        result = subprocess.run(
            [sys.executable, "-m", "gallery_dl", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        version = f"unavailable ({exc})"
    else:
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode == 0:
            version = output or "unknown"
        else:
            version = f"unavailable (exit {result.returncode}: {output})"

    with _GALLERY_DL_VERSION_LOCK:
        _GALLERY_DL_VERSION_CACHE = version
    return version


def yt_dlp_available() -> bool:
    return not yt_dlp_version().startswith("unavailable")


def yt_dlp_version() -> str:
    global _YT_DLP_VERSION_CACHE
    with _YT_DLP_VERSION_LOCK:
        if _YT_DLP_VERSION_CACHE is not None:
            return _YT_DLP_VERSION_CACHE

    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        version = f"unavailable ({exc})"
    else:
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode == 0:
            version = output or "unknown"
        else:
            version = f"unavailable (exit {result.returncode}: {output})"

    with _YT_DLP_VERSION_LOCK:
        _YT_DLP_VERSION_CACHE = version
    return version


def download_hitomi_gallery_dl(job_id: int, parsed: ParsedDownload, session: requests.Session) -> None:
    gallery_id = parsed.hitomi_gallery_id or ""
    source_url = parsed.hitomi_gallery_url or f"https://hitomi.la/galleries/{gallery_id}.html"
    metadata = fetch_hitomi_metadata_best_effort(session, gallery_id, job_id)
    title = str(metadata.get("title") or f"gallery_{gallery_id}")
    target_name = sanitize_segment(f"{gallery_id}-{title}", f"gallery_{gallery_id}")
    target_root = base_target(parsed, "hitomi")
    target = safe_join(target_root, target_name)
    target.mkdir(parents=True, exist_ok=True)

    page_count = len(metadata.get("files") or []) if metadata else 0
    db.update_job(
        job_id,
        target_dir=str(target),
        model_title=title,
        model_category="Hitomi Gallery",
        model_type=str(metadata.get("type") or "Gallery"),
        base_model=str(metadata.get("language") or metadata.get("language_localname") or ""),
        file_format="gallery-dl",
        precision=f"{page_count} pages" if page_count else None,
        metadata_json=json.dumps(redact_metadata(hitomi_metadata_summary(metadata)), ensure_ascii=False) if metadata else None,
    )
    write_metadata(
        target,
        "_archive_metadata.json",
        {
            **metadata_stamp(),
            "source": "hitomi",
            "backend": "gallery-dl",
            "gallery_id": gallery_id,
            "source_url": source_url,
            "raw_input": parsed.raw_input,
            "title": title,
            "page_count": page_count or None,
            "gallery_dl_version": gallery_dl_version(),
        },
    )
    if metadata:
        write_metadata(
            target,
            "_hitomi_metadata.json",
            {
                **metadata_stamp(),
                "source": "hitomi",
                "backend": "gallery-dl",
                "gallery_id": gallery_id,
                "source_url": source_url,
                "raw_input": parsed.raw_input,
                "metadata": metadata,
            },
        )

    command = gallery_dl_command(source_url, target, filename_format="{num:>03}_{filename}.{extension}")
    log_gallery_dl_start(job_id, source_url, target)
    run_gallery_dl_process(job_id, command, target)

    final_size = directory_size(target)
    page_files = hitomi_gallery_files(target)
    thumbnail_url = thumbnail_url_for_path(page_files[0] if page_files else target)
    db.update_job(
        job_id,
        filename=f"{len(page_files) or page_count or 'downloaded'} pages",
        progress_bytes=final_size,
        total_bytes=final_size,
        precision=f"{len(page_files)} pages" if page_files else (f"{page_count} pages" if page_count else None),
        thumbnail_url=thumbnail_url or None,
    )
    db.append_log(job_id, f"saved gallery with gallery-dl: {target} ({human_bytes(final_size)})")


def fetch_hitomi_metadata_best_effort(session: requests.Session, gallery_id: str, job_id: int) -> dict[str, Any]:
    try:
        return fetch_hitomi_gallery_info(session, gallery_id, job_id)
    except JobControlStop:
        raise
    except Exception as exc:  # noqa: BLE001 - gallery-dl can still download without our metadata parser
        db.append_log(job_id, f"Hitomi metadata fetch skipped: {exc}")
        return {}


def gallery_dl_command(source_url: str, target: Path, filename_format: str | None = None) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "gallery_dl",
        "--config-ignore",
        "--no-input",
        "-D",
        str(target),
        "--write-info-json",
    ]
    if filename_format:
        command.extend(["-f", filename_format])
    sleep_request = os.getenv("GALLERY_DL_SLEEP_REQUEST_SECONDS", "").strip()
    if sleep_request:
        command.extend(["--sleep-request", sleep_request])
    command.extend(gallery_dl_auth_args())
    if gallery_dl_uses_ytdlp(source_url):
        command.extend(ytdl_gallery_dl_args(source_url))
    command.append(gallery_dl_extractor_url(source_url))
    return command


def yt_dlp_command(source_url: str, target: Path) -> list[str]:
    url = ytdl_inner_url(source_url) or source_url
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-config",
        "--newline",
        "--socket-timeout",
        "30",
        "--extractor-retries",
        "3",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--write-info-json",
        "--paths",
        str(target),
        "--output",
        "%(title).180B [%(id)s].%(ext)s",
    ]

    ytdlp_format = ytdlp_setting("YT_DLP_FORMAT") or YT_DLP_DEFAULT_FORMAT
    if ytdlp_format:
        command.extend(["--format", ytdlp_format])
    extra_args = ytdlp_direct_cmdline_args()
    subtitle_args = default_youtube_subtitle_cmdline_args(url, extra_args)
    command.extend(default_ytdlp_site_cmdline_args(url, extra_args + subtitle_args))
    command.extend(subtitle_args)
    command.extend(extra_args)
    command.append(url)
    return command


def gallery_dl_auth_args() -> list[str]:
    args: list[str] = []
    username = db.get_secret("GALLERY_DL_USERNAME")
    password = db.get_secret("GALLERY_DL_PASSWORD")
    cookies_file = db.get_secret("GALLERY_DL_COOKIES_FILE")
    cookies_from_browser = db.get_secret("GALLERY_DL_COOKIES_FROM_BROWSER")
    extra_options = db.get_secret("GALLERY_DL_EXTRA_OPTIONS")

    if cookies_file:
        args.extend(["--cookies", cookies_file])
    if cookies_from_browser:
        args.extend(["--cookies-from-browser", cookies_from_browser])
    if username:
        args.extend(["-u", username])
    if password:
        args.extend(["-p", password])
    if extra_options:
        for line in extra_options.splitlines():
            option = line.strip()
            if not option or option.startswith("#"):
                continue
            if "=" not in option:
                continue
            args.extend(["-o", option])
    return args


def ytdl_gallery_dl_args(source_url: str) -> list[str]:
    cmdline_args: list[str] = []
    config_options: dict[str, Any] = {}

    cookies_file = ytdlp_setting("YT_DLP_COOKIES_FILE")
    cookies_from_browser = ytdlp_setting("YT_DLP_COOKIES_FROM_BROWSER")
    extra_options = ytdlp_setting("YT_DLP_EXTRA_OPTIONS")
    if cookies_file:
        cmdline_args.extend(["--cookies", cookies_file])
    if cookies_from_browser:
        cmdline_args.extend(["--cookies-from-browser", cookies_from_browser])
    parse_ytdlp_extra_options(extra_options, cmdline_args, config_options)
    cmdline_args.extend(default_ytdlp_site_cmdline_args(ytdl_inner_url(source_url) or source_url, cmdline_args))
    if not has_ytdlp_cmdline_option(cmdline_args, "--js-runtimes"):
        cmdline_args.extend(default_ytdlp_js_runtime_args())

    ytdlp_format = ytdlp_setting("YT_DLP_FORMAT") or YT_DLP_DEFAULT_FORMAT
    if ytdlp_format:
        config_options["extractor.ytdl.format"] = ytdlp_format
        config_options["downloader.ytdl.format"] = ytdlp_format
    if cmdline_args:
        config_options["extractor.ytdl.cmdline-args"] = cmdline_args
        config_options["downloader.ytdl.cmdline-args"] = cmdline_args

    config_options["extractor.ytdl.enabled"] = True
    config_options["extractor.ytdl.module"] = "yt_dlp"
    config_options["downloader.ytdl.module"] = "yt_dlp"
    return gallery_dl_config_args(config_options)


def ytdlp_direct_cmdline_args() -> list[str]:
    args: list[str] = []
    cookies_file = ytdlp_setting("YT_DLP_COOKIES_FILE")
    cookies_from_browser = ytdlp_setting("YT_DLP_COOKIES_FROM_BROWSER")
    extra_options = ytdlp_setting("YT_DLP_EXTRA_OPTIONS")
    if cookies_file:
        args.extend(["--cookies", cookies_file])
    if cookies_from_browser:
        args.extend(["--cookies-from-browser", cookies_from_browser])
    args.extend(parse_ytdlp_extra_cmdline_args(extra_options))
    if not has_ytdlp_cmdline_option(args, "--js-runtimes"):
        args.extend(default_ytdlp_js_runtime_args())
    return args


def default_youtube_subtitle_cmdline_args(url: str, existing_args: list[str]) -> list[str]:
    if not is_youtube_url(url) or has_any_ytdlp_cmdline_option(existing_args, YT_DLP_SUBTITLE_CMDLINE_OPTIONS):
        return []

    info = yt_dlp_subtitle_info(url, existing_args)
    manual_languages = preferred_subtitle_languages(info.get("subtitles"), YOUTUBE_MANUAL_SUBTITLE_LANGS)
    if manual_languages:
        return [
            "--write-subs",
            "--sub-langs",
            ",".join(manual_languages),
            "--sub-format",
            YOUTUBE_SUBTITLE_FORMAT,
            "--convert-subs",
            "srt",
        ]

    auto_languages = preferred_subtitle_languages(info.get("automatic_captions"), YOUTUBE_AUTO_SUBTITLE_LANGS)
    if auto_languages:
        return [
            "--write-auto-subs",
            "--sub-langs",
            ",".join(auto_languages),
            "--sub-format",
            YOUTUBE_SUBTITLE_FORMAT,
            "--convert-subs",
            "srt",
        ]
    return []


def yt_dlp_subtitle_info(url: str, existing_args: list[str]) -> dict[str, Any]:
    try:
        timeout = int(os.getenv("YT_DLP_SUBTITLE_PROBE_TIMEOUT_SECONDS", str(YOUTUBE_SUBTITLE_PROBE_TIMEOUT_SECONDS)))
    except ValueError:
        timeout = YOUTUBE_SUBTITLE_PROBE_TIMEOUT_SECONDS
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-config",
        "--skip-download",
        "--dump-single-json",
        "--no-warnings",
        *existing_args,
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        parsed = json.loads(completed.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def preferred_subtitle_languages(candidates: Any, preferred_languages: tuple[str, ...]) -> list[str]:
    if not isinstance(candidates, dict):
        return []
    available = [str(language).strip() for language in candidates if str(language).strip()]
    selected: list[str] = []
    for preferred in preferred_languages:
        match = preferred_language_match(available, preferred)
        if match and match not in selected:
            selected.append(match)
    return selected


def preferred_language_match(available: list[str], preferred: str) -> str:
    preferred_lower = preferred.lower()
    for language in available:
        if language.lower() == preferred_lower:
            return language
    for language in available:
        language_lower = language.lower()
        if language_lower.startswith(f"{preferred_lower}-") or language_lower.startswith(f"{preferred_lower}."):
            return language
    return ""


def parse_ytdlp_extra_cmdline_args(extra_options: str | None) -> list[str]:
    args: list[str] = []
    if not extra_options:
        return args
    for line in extra_options.splitlines():
        option = line.strip()
        if not option or option.startswith("#"):
            continue
        if option.startswith("-") or "=" not in option:
            args.extend(parse_ytdlp_cmdline_args(option))
            continue
        key, value = option.split("=", 1)
        key = key.strip()
        if key in {"cmdline-args", "ytdl.cmdline-args", "extractor.ytdl.cmdline-args", "downloader.ytdl.cmdline-args"}:
            args.extend(parse_ytdlp_cmdline_args(value.strip()))
    return args


def default_ytdlp_js_runtime_args() -> list[str]:
    return ["--js-runtimes", "deno"] if shutil.which("deno") else []


def default_ytdlp_site_cmdline_args(url: str, existing_args: list[str]) -> list[str]:
    if not is_xhamster_url(url):
        return []
    args: list[str] = []
    if not has_ytdlp_cmdline_option(existing_args, "--impersonate"):
        args.extend(["--impersonate", "chrome"])
    if not has_ytdlp_cmdline_option(existing_args, "--referer"):
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            args.extend(["--referer", f"{parsed.scheme}://{parsed.netloc}/"])
    if not has_ytdlp_cmdline_option(existing_args, "--playlist-items") and not has_ytdlp_cmdline_option(
        existing_args, "--no-playlist"
    ):
        args.extend(["--playlist-items", "1"])
    if not has_ytdlp_cmdline_option(existing_args, "--force-ipv4") and not has_ytdlp_cmdline_option(
        existing_args, "--force-ipv6"
    ):
        args.append("--force-ipv4")
    return args


def is_xhamster_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().strip(".").removeprefix("www.")
    while host:
        if host in {"xhamster.com", "xhamster.desi", "xhamster.one"} or XHAMSTER_HOST_PATTERN.match(host):
            return True
        _subdomain, separator, remainder = host.partition(".")
        if not separator:
            return False
        host = remainder
    return False


def has_ytdlp_cmdline_option(tokens: list[str], option_name: str) -> bool:
    return any(token == option_name or token.startswith(f"{option_name}=") for token in tokens)


def has_any_ytdlp_cmdline_option(tokens: list[str], option_names: set[str]) -> bool:
    return any(
        token.split("=", 1)[0] in option_names or token in option_names
        for token in tokens
    )


def ytdlp_setting(name: str) -> str | None:
    for key in YT_DLP_SETTING_ALIASES.get(name, (name,)):
        value = db.get_secret(key)
        if value and value.strip():
            return value.strip()
    return None


def parse_ytdlp_extra_options(
    extra_options: str | None,
    cmdline_args: list[str],
    config_options: dict[str, Any],
) -> None:
    if not extra_options:
        return
    for line in extra_options.splitlines():
        option = line.strip()
        if not option or option.startswith("#"):
            continue
        if option.startswith("-") or "=" not in option:
            cmdline_args.extend(parse_ytdlp_cmdline_args(option))
            continue
        key, value = option.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if key == "cmdline-args":
            cmdline_args.extend(parse_ytdlp_cmdline_args(value))
        else:
            add_ytdlp_config_option(config_options, key, parse_gallery_dl_option_value(value))


def parse_ytdlp_cmdline_args(value: str) -> list[str]:
    try:
        tokens = shlex.split(value, comments=True)
    except ValueError as exc:
        raise ValueError(f"Invalid YT_DLP_EXTRA_OPTIONS command line: {exc}") from exc
    for token in tokens:
        option = token.split("=", 1)[0]
        if (
            option in YT_DLP_BLOCKED_CMDLINE_OPTIONS
            or option.startswith("--exec")
            or (token.startswith("-o") and not token.startswith("--"))
            or (token.startswith("-P") and not token.startswith("--"))
        ):
            raise ValueError(f"YT_DLP_EXTRA_OPTIONS cannot set output/path/exec option: {option}")
    return tokens


def parse_gallery_dl_option_value(value: str) -> Any:
    try:
        return json.loads(value)
    except ValueError:
        return value


def add_ytdlp_config_option(config_options: dict[str, Any], key: str, value: Any) -> None:
    validate_ytdlp_config_option(key)
    if key.startswith(("extractor.ytdl.", "downloader.ytdl.")):
        config_options[key] = value
        return
    if key.startswith("ytdl."):
        key = key.removeprefix("ytdl.")
    if key.startswith("raw-options."):
        set_ytdlp_shared_config(config_options, key, value)
    elif key in YT_DLP_SHARED_CONFIG_KEYS:
        set_ytdlp_shared_config(config_options, key, value)
    elif key in YT_DLP_EXTRACTOR_CONFIG_KEYS:
        config_options[f"extractor.ytdl.{key}"] = value
    elif key in YT_DLP_DOWNLOADER_CONFIG_KEYS:
        config_options[f"downloader.ytdl.{key}"] = value
    else:
        set_ytdlp_shared_config(config_options, f"raw-options.{key}", value)


def validate_ytdlp_config_option(key: str) -> None:
    option_key = ytdlp_config_option_key(key)
    if option_key in YT_DLP_BLOCKED_CONFIG_OPTIONS or option_key.startswith(
        tuple(f"{blocked}." for blocked in YT_DLP_BLOCKED_CONFIG_OPTIONS)
    ):
        raise ValueError(f"YT_DLP_EXTRA_OPTIONS cannot set unsafe yt-dlp option: {key}")


def ytdlp_config_option_key(key: str) -> str:
    option_key = key.strip()
    for prefix in ("extractor.ytdl.", "downloader.ytdl.", "ytdl."):
        if option_key.startswith(prefix):
            option_key = option_key.removeprefix(prefix)
            break
    if option_key.startswith("raw-options."):
        option_key = option_key.removeprefix("raw-options.")
    return option_key.lower().replace("_", "-")


def set_ytdlp_shared_config(config_options: dict[str, Any], key: str, value: Any) -> None:
    config_options[f"extractor.ytdl.{key}"] = value
    config_options[f"downloader.ytdl.{key}"] = value


def gallery_dl_config_args(config_options: dict[str, Any]) -> list[str]:
    args: list[str] = []
    for key, value in config_options.items():
        encoded = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        args.extend(["-o", f"{key}={encoded}"])
    return args


def gallery_dl_auth_summary(source_url: str | None = None) -> str:
    values = []
    if db.get_secret("GALLERY_DL_COOKIES_FILE"):
        values.append("cookies-file")
    if db.get_secret("GALLERY_DL_COOKIES_FROM_BROWSER"):
        values.append("browser-cookies")
    if db.get_secret("GALLERY_DL_USERNAME"):
        values.append("username")
    if db.get_secret("GALLERY_DL_PASSWORD"):
        values.append("password")
    if db.get_secret("GALLERY_DL_EXTRA_OPTIONS"):
        values.append("extra-options")
    if source_url and gallery_dl_uses_ytdlp(source_url):
        if ytdlp_setting("YT_DLP_COOKIES_FILE"):
            values.append("yt-dlp-cookies-file")
        if ytdlp_setting("YT_DLP_COOKIES_FROM_BROWSER"):
            values.append("yt-dlp-browser-cookies")
        if ytdlp_setting("YT_DLP_EXTRA_OPTIONS"):
            values.append("yt-dlp-extra-options")
    return ", ".join(values) if values else "none"


def ytdlp_auth_summary() -> str:
    values = []
    if ytdlp_setting("YT_DLP_COOKIES_FILE"):
        values.append("cookies-file")
    if ytdlp_setting("YT_DLP_COOKIES_FROM_BROWSER"):
        values.append("browser-cookies")
    if ytdlp_setting("YT_DLP_EXTRA_OPTIONS"):
        values.append("extra-options")
    return ", ".join(values) if values else "none"


def log_gallery_dl_start(job_id: int, source_url: str, target: Path) -> None:
    db.append_log(
        job_id,
        (
            f"gallery-dl start: source={redact_sensitive_text(source_url)} target={target} "
            f"version={gallery_dl_version()} yt-dlp={yt_dlp_version() if gallery_dl_uses_ytdlp(source_url) else 'n/a'} "
            f"auth={gallery_dl_auth_summary(source_url)}"
        ),
    )


def log_ytdlp_start(job_id: int, source_url: str, target: Path) -> None:
    db.append_log(
        job_id,
        (
            f"yt-dlp start: source={redact_sensitive_text(ytdl_inner_url(source_url) or source_url)} "
            f"target={target} version={yt_dlp_version()} auth={ytdlp_auth_summary()}"
        ),
    )


def is_ytdl_url(url: str) -> bool:
    return urlparse(url).scheme.lower() == "ytdl"


def ytdl_inner_url(url: str) -> str:
    return url.split(":", 1)[1] if is_ytdl_url(url) else ""


def gallery_dl_uses_ytdlp(url: str) -> bool:
    return is_ytdl_url(url) or is_ytdlp_preferred_url(ytdl_inner_url(url) or url)


def gallery_dl_extractor_url(url: str) -> str:
    return f"ytdl:{url}" if is_ytdlp_preferred_url(url) and not is_ytdl_url(url) else url


def is_youtube_url(url: str) -> bool:
    return normalized_url_host(url) in YOUTUBE_HOSTS


def is_ytdlp_preferred_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and (
        normalized_url_host(url) in YOUTUBE_HOSTS or is_ytdlp_preferred_host(parsed.hostname or "")
    )


def normalized_url_host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def gallery_dl_target_parts(source_url: str) -> tuple[str, str]:
    display_url = ytdl_inner_url(source_url) or source_url
    parsed_url = urlparse(display_url)
    host = sanitize_segment(canonical_gallery_host(parsed_url), "site")
    slug = sanitize_segment(youtube_slug(parsed_url) or generic_gallery_slug(parsed_url, host), "archive")
    return host, slug


def canonical_gallery_host(parsed_url: Any) -> str:
    host = parsed_url.netloc.lower().removeprefix("www.")
    return "youtube.com" if host in YOUTUBE_HOSTS else host


def youtube_slug(parsed_url: Any) -> str:
    host = parsed_url.netloc.lower().removeprefix("www.")
    if host == "youtu.be":
        video_id = Path(unquote(parsed_url.path).strip("/")).name
        return f"video-{video_id}" if video_id else ""
    if host in {"youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com"}:
        query = dict(parse_qsl(parsed_url.query, keep_blank_values=False))
        if query.get("v"):
            return f"video-{query['v']}"
        if query.get("list"):
            return f"playlist-{query['list']}"
        parts = [part for part in parsed_url.path.strip("/").split("/") if part]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
            return f"{parts[0]}-{parts[1]}"
        if parts and parts[0].startswith("@"):
            return "-".join(parts[:2])
    return ""


def generic_gallery_slug(parsed_url: Any, host: str) -> str:
    path_name = Path(unquote(parsed_url.path).strip("/")).name
    if path_name:
        stem = Path(path_name).stem
        if stem:
            return stem
    if parsed_url.query:
        return f"{host}-{stable_hash(parsed_url.query, 8)}"
    return host


def controlled_process_kwargs() -> dict[str, Any]:
    if os.name == "posix":
        return {"start_new_session": True}
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return {"creationflags": creationflags} if creationflags else {}


def gallery_dl_process_kwargs() -> dict[str, Any]:
    return controlled_process_kwargs()


def stop_controlled_process(job_id: int, process: subprocess.Popen[str], label: str) -> None:
    if process.poll() is not None:
        return

    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
            db.append_log(job_id, f"{label} process group terminate requested")
        except ProcessLookupError:
            return
        except OSError as exc:
            db.append_log(job_id, f"{label} process group terminate failed: {exc}; terminating parent")
            process.terminate()
    else:
        process.terminate()
        db.append_log(job_id, f"{label} process terminate requested")

    try:
        process.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass

    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            db.append_log(job_id, f"{label} process group kill requested")
        except ProcessLookupError:
            return
        except OSError as exc:
            db.append_log(job_id, f"{label} process group kill failed: {exc}; killing parent")
            process.kill()
    else:
        process.kill()
        db.append_log(job_id, f"{label} process kill requested")

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        db.append_log(job_id, f"{label} process did not exit after kill request")


def stop_gallery_dl_process(job_id: int, process: subprocess.Popen[str]) -> None:
    stop_controlled_process(job_id, process, "gallery-dl")


def run_gallery_dl_process(job_id: int, command: list[str], target: Path) -> None:
    run_external_download_process(job_id, command, target, "gallery-dl")


def run_ytdlp_process(job_id: int, command: list[str], target: Path) -> None:
    run_external_download_process(job_id, command, target, "yt-dlp")


def run_external_download_process(job_id: int, command: list[str], target: Path, label: str) -> None:
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **gallery_dl_process_kwargs(),
        )
        output_queue = process_output_queue()
        output_done = process.stdout is None
        if process.stdout:
            reader = threading.Thread(
                target=read_process_output,
                args=(process.stdout, output_queue),
                name=f"gallery-dl-output-{job_id}",
                daemon=True,
            )
            reader.start()

        last_update = 0.0
        while True:
            check_job_control(job_id)
            try:
                event = output_queue.get(timeout=1.0)
            except queue.Empty:
                event = None
            if event is not None and event[0] == "done":
                output_done = True
            elif event is not None and event[1] is not None:
                line = event[1]
                message = line.strip()
                if message:
                    db.append_log(job_id, f"{label}: {message[:1000]}")

            now = time.time()
            if now - last_update >= 1.0:
                update_gallery_dl_progress(job_id, target)
                last_update = now
            if process.poll() is not None and output_done and output_queue.empty():
                break

        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(f"{label} exited with code {return_code}")
    except JobControlStop:
        if process and process.poll() is None:
            stop_controlled_process(job_id, process, label)
        raise
    except Exception:
        if process and process.poll() is None:
            stop_controlled_process(job_id, process, label)
        raise


def process_output_queue() -> queue.Queue[tuple[str, str | None]]:
    return queue.Queue(
        maxsize=positive_int_env("PROCESS_OUTPUT_QUEUE_MAX_LINES", PROCESS_OUTPUT_QUEUE_MAX_LINES_DEFAULT)
    )


def read_process_output(pipe: Any, output_queue: queue.Queue[tuple[str, str | None]]) -> None:
    try:
        for line in pipe:
            output_queue.put(("line", line))
    finally:
        output_queue.put(("done", None))


def update_gallery_dl_progress(job_id: int, target: Path) -> None:
    page_files = gallery_dl_downloaded_files(target)
    latest_name = page_files[-1].name if page_files else None
    db.update_job(
        job_id,
        filename=latest_name,
        progress_bytes=directory_size(target),
        total_bytes=None,
    )


def gallery_dl_downloaded_files(target: Path) -> list[Path]:
    ignored_suffixes = {".part", ".json", ".txt"}
    files = [
        item
        for item in target.rglob("*")
        if item.is_file()
        and item.suffix.lower() not in ignored_suffixes
        and not item.name.endswith(".part")
    ]
    return sorted(files, key=lambda item: item.name.lower())


def cleanup_empty_gallery_archive_target(job_id: int, target: Path) -> None:
    if gallery_dl_downloaded_files(target) or not is_safe_gallery_archive_target(target):
        return
    try:
        if not target.exists():
            return
        for item in sorted(target.rglob("*"), key=lambda child: len(child.parts), reverse=True):
            if item.is_symlink():
                continue
            if item.is_file():
                suffix = item.suffix.lower()
                if suffix in {".json", ".txt", ".part"} or item.name.endswith(".part"):
                    item.unlink()
            elif item.is_dir():
                try:
                    item.rmdir()
                except OSError:
                    pass
        target.rmdir()
        db.append_log(job_id, f"removed empty gallery-dl archive: {target}")
    except OSError as exc:
        db.append_log(job_id, f"empty gallery-dl archive cleanup failed: {target} ({exc})")


def is_safe_gallery_archive_target(target: Path) -> bool:
    try:
        root = DATA_ROOT.resolve(strict=False)
        resolved = target.resolve(strict=False)
    except OSError:
        return False
    return resolved != root and root in resolved.parents


def hitomi_gallery_files(target: Path) -> list[Path]:
    extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
    files = [
        item
        for item in target.rglob("*")
        if item.is_file()
        and item.suffix.lower() in extensions
        and not item.name.endswith(".part")
    ]
    return sorted(files, key=lambda item: item.name.lower())


def fetch_hitomi_gallery_info(session: requests.Session, gallery_id: str, job_id: int) -> dict[str, Any]:
    url = f"https://ltn.gold-usergeneratedcontent.net/galleries/{gallery_id}.js"
    db.append_log(job_id, f"Hitomi metadata: {url}")
    response = request_with_safety(session, "GET", url, job_id=job_id, timeout=(20, 60))
    response.raise_for_status()
    match = re.search(r"var\s+galleryinfo\s*=\s*(\{.*\})\s*;?\s*$", response.text, flags=re.DOTALL)
    if not match:
        raise ValueError("Could not parse Hitomi gallery metadata.")
    return json.loads(match.group(1))


def fetch_hitomi_gg(session: requests.Session, job_id: int) -> dict[str, Any]:
    url = "https://ltn.gold-usergeneratedcontent.net/gg.js"
    response = request_with_safety(session, "GET", url, job_id=job_id, timeout=(20, 60))
    response.raise_for_status()
    page = response.text
    mapping: dict[int, int] = {}
    keys: list[int] = []
    for match in re.finditer(r"case\s+(\d+):(?:\s*o\s*=\s*(\d+))?", page):
        keys.append(int(match.group(1)))
        if match.group(2) is not None:
            value = int(match.group(2))
            for key in keys:
                mapping[key] = value
            keys.clear()
    for match in re.finditer(r"if\s+\(g\s*===?\s*(\d+)\)[\s{]*o\s*=\s*(\d+)", page):
        mapping[int(match.group(1))] = int(match.group(2))
    default_match = re.search(r"(?:var\s+|default:)\s*o\s*=\s*(\d+)", page)
    prefix_match = re.search(r"b:\s*[\"']([^\"']+)[\"']", page)
    return {
        "mapping": mapping,
        "default": int(default_match.group(1)) if default_match else 0,
        "prefix": (prefix_match.group(1).strip("/") if prefix_match else ""),
    }


def hitomi_preferred_format() -> str:
    value = os.getenv("HITOMI_IMAGE_FORMAT", "webp").strip().lower()
    return value if value in {"webp", "avif", "original"} else "webp"


def hitomi_image_candidates(image: dict[str, Any], gg: dict[str, Any]) -> list[tuple[str, str]]:
    original_ext = Path(str(image.get("name") or "image.jpg")).suffix.lower().removeprefix(".") or "jpg"
    preferred = hitomi_preferred_format()
    requested = [preferred, "webp", "avif", "original"]
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for value in requested:
        ext = original_ext if value == "original" else value
        if ext in seen:
            continue
        if value == "webp" and not image.get("haswebp"):
            continue
        if value == "avif" and not image.get("hasavif"):
            continue
        seen.add(ext)
        candidates.append((hitomi_image_url(str(image["hash"]), ext, gg), ext))
    return candidates


def hitomi_image_url(image_hash: str, extension: str, gg: dict[str, Any]) -> str:
    number = int(image_hash[-1] + image_hash[-3:-1], 16)
    mapping = gg.get("mapping") if isinstance(gg.get("mapping"), dict) else {}
    shard = int(mapping.get(number, gg.get("default", 0))) + 1
    prefix = str(gg.get("prefix") or "").strip("/")
    directory = "images" if extension not in {"webp", "avif"} else extension
    path_prefix = f"{prefix}/" if prefix else ""
    return (
        f"https://{extension[0]}{shard}.gold-usergeneratedcontent.net/"
        f"{directory}/{path_prefix}{number}/{image_hash}.{extension}"
    )


def hitomi_output_filename(index: int, original_name: str, extension: str) -> str:
    stem = Path(original_name).stem or f"page_{index:03d}"
    return sanitize_segment(f"{index:03d}_{stem}.{extension}", f"{index:03d}.{extension}")


def hitomi_metadata_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": metadata.get("id"),
        "title": metadata.get("title"),
        "japanese_title": metadata.get("japanese_title"),
        "type": metadata.get("type"),
        "language": metadata.get("language"),
        "language_localname": metadata.get("language_localname"),
        "date": metadata.get("date"),
        "datepublished": metadata.get("datepublished"),
        "artists": metadata.get("artists"),
        "groups": metadata.get("groups"),
        "parodys": metadata.get("parodys"),
        "characters": metadata.get("characters"),
        "tags": metadata.get("tags"),
        "page_count": len(metadata.get("files") or []),
    }


def fetch_workflow_bytes(
    session: requests.Session,
    url: str,
    job_id: int,
    fallback_filename: str | None = None,
) -> tuple[bytes, str]:
    response = request_with_safety(session, "GET", url, job_id=job_id, stream=True, timeout=(20, 120), allow_redirects=True)
    with response:
        response.raise_for_status()
        filename = filename_from_content_disposition(response.headers.get("content-disposition"))
        if not filename:
            filename = fallback_filename or url_basename(url)
        total = response.headers.get("content-length")
        total_bytes = int(total) if total and total.isdigit() else None
        if total_bytes and total_bytes > workflow_max_bytes():
            raise ValueError(f"워크플로우 파일이 너무 큽니다. 최대 {human_bytes(workflow_max_bytes())}까지 지원합니다.")
        db.update_job(job_id, filename=filename, total_bytes=total_bytes, progress_bytes=0)
        chunks: list[bytes] = []
        downloaded = 0
        last_update = 0.0
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            check_job_control(job_id)
            if not chunk:
                continue
            chunks.append(chunk)
            downloaded += len(chunk)
            if downloaded > workflow_max_bytes():
                raise ValueError(f"워크플로우 파일이 너무 큽니다. 최대 {human_bytes(workflow_max_bytes())}까지 지원합니다.")
            now = time.time()
            if now - last_update >= 1.0:
                db.update_job(job_id, progress_bytes=downloaded, total_bytes=total_bytes)
                last_update = now
    db.update_job(job_id, progress_bytes=downloaded, total_bytes=total_bytes or downloaded)
    return b"".join(chunks), filename or f"workflow_{job_id}.json"


def fetch_json(session: requests.Session, url: str, job_id: int | None = None) -> dict[str, Any]:
    response = request_with_safety(session, "GET", url, job_id=job_id, timeout=(20, 60))
    response.raise_for_status()
    return response.json()


def fetch_huggingface_metadata(parsed: ParsedDownload, token: str | None, job_id: int) -> dict[str, Any]:
    endpoint = {
        "model": "models",
        "dataset": "datasets",
        "space": "spaces",
    }.get(parsed.repo_type, "models")
    url = f"https://huggingface.co/api/{endpoint}/{quote(parsed.repo_id or '', safe='/')}"
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    session = requests.Session()
    try:
        response = request_with_safety(session, "GET", url, job_id=job_id, headers=headers, timeout=(20, 60))
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        return {
            "id": parsed.repo_id,
            "modelId": parsed.repo_id,
            "pipeline_tag": None,
            "tags": [],
            "siblings": [{"rfilename": filename} for filename in parsed.filenames],
            "metadata_error": str(exc),
        }


def update_job_archive_info(job_id: int, target: Path, archive_info: dict[str, Any], metadata: dict[str, Any]) -> None:
    db.update_job(
        job_id,
        target_dir=str(target),
        model_title=archive_info.get("model_title"),
        model_category=archive_info.get("model_category"),
        model_type=archive_info.get("model_type"),
        base_model=archive_info.get("base_model"),
        file_format=archive_info.get("file_format"),
        precision=archive_info.get("precision"),
        thumbnail_url=archive_info.get("thumbnail_url"),
        metadata_json=json.dumps(redact_metadata(metadata_summary(metadata)), ensure_ascii=False),
    )


def update_job_workflow_info(
    job_id: int,
    result: dict[str, Any],
    *,
    data_size: int,
    source_url: str | None = None,
) -> None:
    target = result["target_dir"]
    target_path = Path(target)
    thumbnail_path = result.get("thumbnail_path")
    thumbnail_url = ""
    if isinstance(thumbnail_path, Path):
        try:
            relative = thumbnail_path.relative_to(DATA_ROOT.resolve()).as_posix()
            thumbnail_url = f"/api/workflows/preview?path={quote(relative, safe='/')}"
        except ValueError:
            thumbnail_url = ""
    metadata = {
        "source": "comfyui",
        "source_url": source_url,
        "source_format": result.get("source_format"),
        "source_key": result.get("source_key"),
        "node_count": result.get("node_count"),
        "link_count": result.get("link_count"),
        "models": result.get("models"),
    }
    db.update_job(
        job_id,
        target_dir=str(target_path),
        filename=str(result.get("filename") or "workflow.json"),
        progress_bytes=data_size,
        total_bytes=data_size,
        model_title=result.get("title") or target_path.name,
        model_category="ComfyUI Workflow",
        model_type=f"{result.get('node_count', 0)} nodes",
        base_model=f"{result.get('link_count', 0)} links",
        file_format=str(result.get("source_format") or "Workflow"),
        precision=f"{len(result.get('models') or [])} models",
        thumbnail_url=thumbnail_url,
        metadata_json=json.dumps(redact_metadata(metadata), ensure_ascii=False),
    )


def metadata_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    raw_model = metadata.get("model")
    model = raw_model if isinstance(raw_model, dict) else {}
    raw_images = metadata.get("images")
    images = raw_images if isinstance(raw_images, list) else None
    return {
        "id": metadata.get("id") or metadata.get("modelId"),
        "name": metadata.get("name") or metadata.get("modelId"),
        "type": metadata.get("type") or model.get("type"),
        "pipeline_tag": metadata.get("pipeline_tag"),
        "tags": metadata.get("tags"),
        "baseModel": metadata.get("baseModel"),
        "baseModelType": metadata.get("baseModelType"),
        "files": metadata.get("files"),
        "images": images[:3] if images else None,
        "stats": metadata.get("stats"),
        "config": metadata.get("config"),
        "cardData": metadata.get("cardData"),
        "safetensors": metadata.get("safetensors"),
        "metadata_error": metadata.get("metadata_error"),
    }


def auth_headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def stream_download(
    job_id: int,
    session: requests.Session,
    url: str,
    target_dir: Path,
    filename_override: str | None = None,
) -> Path:
    check_job_control(job_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    filename, total_from_head = resolve_remote_filename(session, url, job_id)
    if filename_override:
        filename = filename_override
    filename = sanitize_segment(filename, default=f"job_{job_id}.bin")
    final_path = target_dir / filename
    lock_guard = FinalPathLockGuard()
    lock_guard.acquire_for(final_path, job_id)
    part_path = partial_download_path(final_path, job_id, url)

    try:
        existing = part_path.stat().st_size if part_path.exists() else 0
        headers: dict[str, str] = {"Accept-Encoding": "identity"}
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
            db.append_log(job_id, f"resume from {human_bytes(existing)}")

        with request_with_safety(
            session,
            "GET",
            url,
            job_id=job_id,
            stream=True,
            timeout=(20, 120),
            allow_redirects=True,
            headers=headers,
        ) as response:
            if response.status_code == 416 and final_path.exists():
                db.append_log(job_id, "server says range already complete")
                return final_path
            response.raise_for_status()

            if existing > 0 and response.status_code != 206:
                db.append_log(job_id, "server did not accept resume; restarting partial file")
                existing = 0
                part_path.unlink(missing_ok=True)

            cd_filename = filename_from_content_disposition(response.headers.get("content-disposition"))
            if cd_filename and not filename_override:
                new_filename = sanitize_segment(cd_filename, default=filename)
                if new_filename != filename and existing == 0:
                    filename = new_filename
                    final_path = target_dir / filename
                    lock_guard.acquire_for(final_path, job_id)
                    part_path = partial_download_path(final_path, job_id, url)
                    if part_path.exists():
                        db.append_log(job_id, "content-disposition filename changed; restarting matching partial file")
                        part_path.unlink(missing_ok=True)

            register_job_partial_path(job_id, part_path, final_path, url)

            content_length = response.headers.get("content-length")
            total = None
            if response.status_code == 206:
                total = parse_content_range_total(response.headers.get("content-range"))
            if total is None and content_length and content_length.isdigit():
                total = int(content_length) + existing
            if total is None:
                total = total_from_head

            db.update_job(job_id, filename=filename, total_bytes=total, progress_bytes=existing)
            db.append_log(job_id, f"saving to {final_path}")

            downloaded = existing
            mode = "ab" if existing > 0 else "wb"
            last_update = 0.0
            with part_path.open(mode) as file:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    check_job_control(job_id)
                    if not chunk:
                        continue
                    file.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_update >= 1.0:
                        db.update_job(job_id, progress_bytes=downloaded, total_bytes=total)
                        last_update = now

        if total is not None and downloaded < total:
            raise requests.ConnectionError(f"incomplete download: {human_bytes(downloaded)} / {human_bytes(total)}")

        part_path.replace(final_path)
        final_size = final_path.stat().st_size
        db.update_job(job_id, progress_bytes=final_size, total_bytes=final_size)
        db.append_log(job_id, f"saved: {final_path} ({human_bytes(final_size)})")
        return final_path
    except JobControlStop as exc:
        if exc.status in {"canceled", "deleted"}:
            part_path.unlink(missing_ok=True)
            cleanup_job_partial_files(job_id)
        raise
    finally:
        lock_guard.release()


def resolve_remote_filename(session: requests.Session, url: str, job_id: int | None = None) -> tuple[str, int | None]:
    filename = url_basename(url)
    total = None
    if not bool_env("DOWNLOAD_ENABLE_HEAD_REQUESTS", True):
        return filename, total
    try:
        response = request_with_safety(
            session,
            "HEAD",
            url,
            job_id=job_id,
            timeout=(10, 30),
            allow_redirects=True,
            headers={"Accept-Encoding": "identity"},
        )
        if response.ok:
            cd_filename = filename_from_content_disposition(response.headers.get("content-disposition"))
            if cd_filename:
                filename = cd_filename
            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit():
                total = int(content_length)
    except requests.RequestException:
        pass
    return filename, total


def url_basename(url: str) -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name
    return name or "download.bin"


def filename_from_content_disposition(value: str | None) -> str | None:
    if not value:
        return None
    # RFC 5987: filename*=UTF-8''file.safetensors
    match = re.search(r"filename\*=([^;]+)", value, flags=re.IGNORECASE)
    if match:
        raw = match.group(1).strip().strip('"')
        if "''" in raw:
            raw = raw.split("''", 1)[1]
        return unquote(raw)
    match = re.search(r"filename=([^;]+)", value, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip().strip('"')
    return None


def parse_content_range_total(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"/(\d+)$", value)
    if match:
        return int(match.group(1))
    return None


def huggingface_download_worker_main() -> int:
    raw_spec = sys.stdin.read()
    try:
        spec = json.loads(raw_spec)
    except json.JSONDecodeError as exc:
        print(f"invalid Hugging Face download spec: {exc}", file=sys.stderr, flush=True)
        return 2
    if not isinstance(spec, dict):
        print("invalid Hugging Face download spec: expected object", file=sys.stderr, flush=True)
        return 2

    try:
        token = spec.get("token") or None
        configure_huggingface_runtime(str(token) if token else None)

        from huggingface_hub import hf_hub_download, snapshot_download

        common = {
            "repo_id": spec["repo_id"],
            "repo_type": spec.get("repo_type"),
            "revision": spec.get("revision"),
            "local_dir": spec["local_dir"],
            "token": token,
        }
        mode = spec.get("mode")
        if mode == "file":
            local_path = hf_hub_download(filename=spec["filename"], **common)
        elif mode == "snapshot":
            local_path = snapshot_download(
                allow_patterns=spec.get("allow_patterns"),
                ignore_patterns=spec.get("ignore_patterns"),
                max_workers=int(spec.get("max_workers") or HF_DEFAULT_SNAPSHOT_WORKERS),
                **common,
            )
        else:
            raise ValueError(f"Unsupported Hugging Face download mode: {mode}")

        print(
            HF_RESULT_PREFIX + json.dumps({"local_path": str(local_path)}, ensure_ascii=False),
            flush=True,
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - child process must report failures to parent
        print(redact_sensitive_text(f"Hugging Face download failed: {exc}"), file=sys.stderr, flush=True)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == [HF_DOWNLOAD_SUBCOMMAND]:
        return huggingface_download_worker_main()
    print("usage: python -m app.downloader huggingface-download", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
