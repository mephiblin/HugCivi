from __future__ import annotations

import json
import os
import queue
import random
import re
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse, unquote

import requests

from . import db
from .metadata import classify_civitai, classify_huggingface, pick_civitai_file
from .models import ParsedDownload
from .utils import human_bytes, redact_sensitive_text, safe_join, sanitize_segment

DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))
USER_AGENT = os.getenv("USER_AGENT", "nas-model-archiver/0.1")
CHUNK_SIZE = 1024 * 1024
CIVITAI_API_BASE = os.getenv("CIVITAI_API_BASE", "https://civitai.com/api/v1").rstrip("/")
HF_DEFAULT_SNAPSHOT_WORKERS = 2
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

JOB_QUEUE: queue.Queue[int] = queue.Queue()
_WORKERS_STARTED = False
_WORKERS_LOCK = threading.Lock()
_HOST_THROTTLE_LOCK = threading.Lock()
_HOST_NEXT_REQUEST_AT: dict[str, float] = {}


def enqueue_existing_jobs() -> None:
    for job in reversed(db.list_jobs(limit=500)):
        if job["status"] in {"queued", "running"}:
            db.update_job(job["id"], status="queued", error=None)
            JOB_QUEUE.put(int(job["id"]))


def enqueue_job(job_id: int) -> None:
    JOB_QUEUE.put(job_id)


def start_workers() -> None:
    global _WORKERS_STARTED
    with _WORKERS_LOCK:
        if _WORKERS_STARTED:
            return
        max_workers = positive_int_env("MAX_CONCURRENT_DOWNLOADS", 2)
        enqueue_existing_jobs()
        for index in range(max_workers):
            thread = threading.Thread(target=worker_loop, name=f"download-worker-{index+1}", daemon=True)
            thread.start()
        _WORKERS_STARTED = True


def worker_loop() -> None:
    while True:
        job_id = JOB_QUEUE.get()
        try:
            run_job(job_id)
        except Exception as exc:  # noqa: BLE001 - log everything for a downloader daemon
            db.append_log(job_id, f"FAILED: {exc}")
            db.update_job(job_id, status="failed", error=str(exc))
        finally:
            JOB_QUEUE.task_done()


def run_job(job_id: int) -> None:
    job = db.get_job(job_id)
    if not job:
        return
    parsed = db.parse_job_payload(job)
    db.update_job(job_id, status="running", error=None, progress_bytes=0, total_bytes=None)
    db.append_log(job_id, f"started source={parsed.source}")

    if parsed.source == "huggingface":
        download_huggingface(job_id, parsed)
    elif parsed.source == "civitai":
        download_civitai(job_id, parsed)
    elif parsed.source == "generic":
        download_generic(job_id, parsed)
    else:
        raise ValueError(f"Unsupported source: {parsed.source}")

    db.update_job(job_id, status="done")
    db.append_log(job_id, "done")


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
    out = target_dir / sanitize_segment(name, "metadata.json")
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


def positive_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return max(1, int(raw_value))
    except ValueError:
        return default


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
    return default


def wait_for_request_slot(url: str) -> None:
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
        time.sleep(sleep_for)


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
        delay = header_delay
    else:
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
    max_retries = positive_int_env("DOWNLOAD_HTTP_MAX_RETRIES", 3)
    retry_statuses = {429, 500, 502, 503, 504}
    response: requests.Response | None = None
    for attempt in range(max_retries + 1):
        wait_for_request_slot(url)
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
        time.sleep(delay)
    if response is None:
        raise RuntimeError("HTTP request was not attempted")
    return response


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

    from huggingface_hub import hf_hub_download, snapshot_download

    if token:
        db.append_log(job_id, "HF token configured: authenticated Hub requests enabled")
    else:
        db.append_log(job_id, "HF token not configured: anonymous public Hub access")

    verify_huggingface_token(job_id, token)
    metadata = fetch_huggingface_metadata(parsed, token, job_id)
    archive_info = classify_huggingface(metadata, parsed.repo_type, parsed.repo_id)
    target = base_target(parsed, *archive_info["target_parts"], archive_info=archive_info)
    target.mkdir(parents=True, exist_ok=True)
    update_job_archive_info(job_id, target, archive_info, metadata)

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
            db.append_log(job_id, f"HF file download: {parsed.repo_id}/{filename}")
            local_path = hf_hub_download(filename=filename, **common)
            db.append_log(job_id, f"saved: {local_path}")
            db.update_job(job_id, filename=str(Path(local_path).name))
    else:
        db.append_log(job_id, f"HF snapshot download: {parsed.repo_id}")
        local_path = snapshot_download(
            allow_patterns=parsed.include_patterns or None,
            ignore_patterns=parsed.exclude_patterns or None,
            max_workers=positive_int_env("HF_SNAPSHOT_MAX_WORKERS", HF_DEFAULT_SNAPSHOT_WORKERS),
            **common,
        )
        saved_size = directory_size(Path(local_path))
        db.append_log(job_id, f"saved snapshot: {local_path} ({human_bytes(saved_size)})")
        db.update_job(job_id, filename="snapshot", progress_bytes=saved_size, total_bytes=saved_size)


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


def download_civitai(job_id: int, parsed: ParsedDownload) -> None:
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
    db.update_job(job_id, target_dir=str(target), model_category="Generic URL", model_title=parsed.url)
    write_metadata(
        target,
        "_generic_metadata.json",
        {**metadata_stamp(), "source": "generic", "url": parsed.url, "raw_input": parsed.raw_input},
    )
    saved = stream_download(job_id, session, parsed.url, target)
    db.update_job(job_id, filename=saved.name)


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


def stream_download(job_id: int, session: requests.Session, url: str, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    filename, total_from_head = resolve_remote_filename(session, url, job_id)
    filename = sanitize_segment(filename, default=f"job_{job_id}.bin")
    final_path = target_dir / filename
    part_path = target_dir / f"{filename}.part"

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
        if cd_filename:
            new_filename = sanitize_segment(cd_filename, default=filename)
            if new_filename != filename and existing == 0:
                filename = new_filename
                final_path = target_dir / filename
                part_path = target_dir / f"{filename}.part"

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
    db.update_job(job_id, progress_bytes=final_path.stat().st_size, total_bytes=final_path.stat().st_size)
    db.append_log(job_id, f"saved: {final_path} ({human_bytes(final_path.stat().st_size)})")
    return final_path


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
