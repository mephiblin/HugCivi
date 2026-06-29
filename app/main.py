from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from . import db
from .downloader import enqueue_job, start_workers
from .models import ParsedDownload
from .parsers import InputParseError, parse_input
from .utils import human_bytes, safe_join, sanitize_segment

app = FastAPI(title="NAS Model Archiver", version="0.1.0")
BASE_DIR = Path(__file__).resolve().parent
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))
DOWNLOAD_ARCHIVE_DIR = Path(os.getenv("DOWNLOAD_ARCHIVE_DIR", "/config/downloads"))
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
security = HTTPBasic()
INSECURE_PASSWORDS = {"", "change-this-password", "replace-with-a-strong-password"}


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


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _: str = Depends(require_auth)) -> HTMLResponse:
    jobs = decorate_jobs(db.list_jobs())
    ensure_route_folders()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "jobs": jobs,
            "folder_tree": build_folder_tree(DATA_ROOT),
            "settings": db.settings_status(),
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
    library_active: str = Form("ComfyUI"),
    route_llm_root: str = Form(""),
    route_lora_root: str = Form(""),
    route_checkpoint_root: str = Form(""),
    route_diffusion_model_root: str = Form(""),
    route_embedding_root: str = Form(""),
    route_vae_root: str = Form(""),
    route_controlnet_root: str = Form(""),
    route_upscaler_root: str = Form(""),
    _: str = Depends(require_auth),
) -> RedirectResponse:
    if hf_token.strip():
        db.set_setting("HF_TOKEN", hf_token.strip())

    if civitai_token.strip():
        db.set_setting("CIVITAI_TOKEN", civitai_token.strip())

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

    return RedirectResponse(url="/", status_code=303)


@app.post("/folders")
def create_folder(folder_path: str = Form(...), _: str = Depends(require_auth)) -> RedirectResponse:
    folder = safe_join(DATA_ROOT, folder_path.strip())
    folder.mkdir(parents=True, exist_ok=True)
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/jobs")
def api_jobs(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(decorate_jobs(db.list_jobs()))


@app.post("/api/jobs/clear")
def api_clear_jobs(_: str = Depends(require_auth)) -> JSONResponse:
    deleted = db.clear_job_history()
    return JSONResponse({"ok": True, "deleted": deleted, "jobs": decorate_jobs(db.list_jobs())})


@app.get("/api/folders")
def api_folders(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(build_folder_tree(DATA_ROOT))


@app.post("/api/fs/rename")
async def api_rename_path(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    source = existing_data_path(str(payload.get("path") or ""))
    ensure_mutable_path(source)
    ensure_no_active_jobs(source)

    new_name = clean_item_name(str(payload.get("new_name") or ""))
    target = safe_join(DATA_ROOT, source.parent.relative_to(DATA_ROOT.resolve()).as_posix(), new_name)
    if target.exists():
        raise HTTPException(status_code=409, detail="같은 이름의 폴더가 이미 있습니다.")
    old_relative = relative_data_path(source)
    new_relative = relative_data_path(target)
    source.rename(target)
    db.update_target_dir_prefix(source, target)
    db.update_favorite_path_prefix(old_relative, new_relative)
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
    target = safe_join(DATA_ROOT, destination.relative_to(DATA_ROOT.resolve()).as_posix(), source.name)
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
    return JSONResponse({"ok": True, "path": relative_data_path(target), "folders": build_folder_tree(DATA_ROOT)})


@app.post("/api/fs/delete")
async def api_delete_path(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    source = existing_data_path(str(payload.get("path") or ""))
    ensure_mutable_path(source)
    ensure_no_active_jobs(source)
    relative_path = relative_data_path(source)

    if source.is_dir():
        shutil.rmtree(source)
    else:
        source.unlink()
    db.clear_target_dir_prefix(source)
    db.clear_favorite_path_prefix(relative_path)
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
    stat = source.stat()
    size = path_size(source)
    urls = source_input_values(source)
    return JSONResponse(
        {
            "ok": True,
            "path": relative_data_path(source),
            "name": source.name,
            "kind": "folder" if source.is_dir() else "file",
            "size_bytes": size,
            "size_human": human_bytes(size),
            "extensions": path_extensions(source),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "urls": urls,
        }
    )


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
    job = dict(job)
    job.pop("parsed_json", None)
    job["progress_human"] = human_bytes(progress)
    job["total_human"] = human_bytes(total)
    job["percent"] = percent
    job["target_path"] = target_path
    favorite_paths = favorites if favorites is not None else db.favorite_paths()
    job["favorite"] = bool(target_path and target_path in favorite_paths)
    job["source_url"] = source_url_for_job(job, parsed)
    return job


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
        if parsed.civitai_model_id:
            return f"https://civitai.com/models/{quote(parsed.civitai_model_id)}"
        if is_http_url(parsed.civitai_download_url or ""):
            return parsed.civitai_download_url or ""
    if parsed.source == "generic" and is_http_url(parsed.url or ""):
        return parsed.url or ""
    return ""


def is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


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
    target = safe_join(DATA_ROOT, path.strip())
    if not target.exists():
        raise HTTPException(status_code=404, detail="대상 경로를 찾을 수 없습니다.")
    return target


def ensure_mutable_path(path: Path) -> None:
    if path.resolve() == DATA_ROOT.resolve():
        raise HTTPException(status_code=400, detail="/data 루트는 이름변경, 이동, 삭제할 수 없습니다.")


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
    root = DATA_ROOT.resolve()
    resolved = path.resolve()
    if resolved == root:
        return ""
    return resolved.relative_to(root).as_posix()


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
    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for item in sorted(source.rglob("*")):
            if not item.is_file() or item.is_symlink():
                continue
            resolved = item.resolve()
            if source_root not in resolved.parents and resolved != source_root:
                continue
            archive.write(resolved, resolved.relative_to(source_root).as_posix())
    return archive_path


def cleanup_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
