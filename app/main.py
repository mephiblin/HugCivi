from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .downloader import enqueue_job, start_workers
from .parsers import InputParseError, parse_input
from .utils import human_bytes, safe_join

app = FastAPI(title="NAS Model Archiver", version="0.1.0")
BASE_DIR = Path(__file__).resolve().parent
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data"))
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
    start_workers()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _: str = Depends(require_auth)) -> HTMLResponse:
    jobs = decorate_jobs(db.list_jobs())
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
    _: str = Depends(require_auth),
) -> RedirectResponse:
    if hf_token.strip():
        db.set_setting("HF_TOKEN", hf_token.strip())

    if civitai_token.strip():
        db.set_setting("CIVITAI_TOKEN", civitai_token.strip())

    return RedirectResponse(url="/", status_code=303)


@app.post("/folders")
def create_folder(folder_path: str = Form(...), _: str = Depends(require_auth)) -> RedirectResponse:
    folder = safe_join(DATA_ROOT, folder_path.strip())
    folder.mkdir(parents=True, exist_ok=True)
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/jobs")
def api_jobs(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(decorate_jobs(db.list_jobs()))


@app.get("/api/folders")
def api_folders(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(build_folder_tree(DATA_ROOT))


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
    return [decorate_job(job) for job in jobs]


def decorate_job(job: dict) -> dict:
    progress = job.get("progress_bytes") or 0
    total = job.get("total_bytes")
    percent = None
    if total:
        percent = min(100, round(progress * 100 / total, 1))
    job = dict(job)
    job.pop("parsed_json", None)
    job["progress_human"] = human_bytes(progress)
    job["total_human"] = human_bytes(total)
    job["percent"] = percent
    return job


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
