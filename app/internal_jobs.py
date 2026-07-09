from __future__ import annotations

from datetime import datetime
import os
import threading
from typing import Any, Callable

from . import db
from .defaults import (
    INTERNAL_JOB_MAINTENANCE_END_HOUR_DEFAULT,
    INTERNAL_JOB_MAINTENANCE_START_HOUR_DEFAULT,
)

InternalJobHandler = Callable[[int, dict[str, Any]], None]

_HANDLERS: dict[str, InternalJobHandler] = {}
_WORKERS_STARTED = False
_WORKERS_LOCK = threading.Lock()
_SCHEDULER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()
_CONDITION = threading.Condition()
_PENDING_JOB_IDS: list[int] = []
_ACTIVE_JOBS = 0
INTERNAL_JOB_MAX_CONCURRENT_DEFAULT = 2
HEAVY_INTERNAL_JOB_KINDS_DEFAULT = {
    "archive_zip",
    "library_reindex",
    "media_poster",
    "media_thumbnail_backfill",
    "media_transcode",
}
INTERNAL_JOB_MAINTENANCE_POLL_SECONDS = 60.0


class InternalJobControlStop(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


def register_handler(job_kind: str, handler: InternalJobHandler) -> None:
    kind = db.normalized_job_kind(job_kind)
    if kind == db.JOB_KIND_DOWNLOAD:
        raise ValueError("download jobs are handled by downloader workers")
    _HANDLERS[kind] = handler


def enqueue_existing_jobs() -> None:
    for job in reversed(db.list_internal_jobs_to_resume(limit=500)):
        job_id = int(job["id"])
        status = str(job.get("status"))
        if status == "deleting":
            db.delete_job(job_id)
        elif status == "canceling":
            db.update_job(job_id, status="canceled", error=None)
            db.append_log(job_id, "canceled after restart")
        elif status == "pausing":
            db.update_job(job_id, status="paused", error=None)
            db.append_log(job_id, "paused after restart")
        elif status in {"queued", "running"}:
            db.update_job(job_id, status="queued", error=None)
            if status == "running":
                db.append_log(job_id, "requeued after restart")
            enqueue_job(job_id)


def enqueue_job(job_id: int) -> None:
    with _CONDITION:
        if job_id not in _PENDING_JOB_IDS:
            _PENDING_JOB_IDS.append(job_id)
        _CONDITION.notify_all()


def notify_policy_changed() -> None:
    with _CONDITION:
        _CONDITION.notify_all()


def remove_pending_job(job_id: int) -> None:
    with _CONDITION:
        _PENDING_JOB_IDS[:] = [pending_id for pending_id in _PENDING_JOB_IDS if pending_id != job_id]
        _CONDITION.notify_all()


def start_workers() -> None:
    global _SCHEDULER_THREAD, _WORKERS_STARTED
    with _WORKERS_LOCK:
        if _WORKERS_STARTED and _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
            return
        _WORKERS_STARTED = False
        _SCHEDULER_THREAD = None
        _STOP_EVENT.clear()
        enqueue_existing_jobs()
        thread = threading.Thread(target=scheduler_loop, name="internal-job-scheduler", daemon=True)
        _SCHEDULER_THREAD = thread
        thread.start()
        _WORKERS_STARTED = True


def stop_workers(timeout_seconds: float = 5.0) -> bool:
    global _SCHEDULER_THREAD, _WORKERS_STARTED
    with _WORKERS_LOCK:
        thread = _SCHEDULER_THREAD
        if thread is None or not thread.is_alive():
            _WORKERS_STARTED = False
            _SCHEDULER_THREAD = None
            _STOP_EVENT.set()
            return True
        _STOP_EVENT.set()

    with _CONDITION:
        _CONDITION.notify_all()

    if thread is not threading.current_thread():
        thread.join(timeout=max(0.0, timeout_seconds))

    stopped = not thread.is_alive()
    if stopped:
        with _WORKERS_LOCK:
            if _SCHEDULER_THREAD is thread:
                _SCHEDULER_THREAD = None
                _WORKERS_STARTED = False
    return stopped


def scheduler_loop() -> None:
    global _ACTIVE_JOBS, _SCHEDULER_THREAD, _WORKERS_STARTED
    try:
        while True:
            with _CONDITION:
                while not _PENDING_JOB_IDS or _ACTIVE_JOBS >= max_concurrent_jobs():
                    if _STOP_EVENT.is_set():
                        return
                    _CONDITION.wait()
                if _STOP_EVENT.is_set():
                    return
                job_id = pop_next_startable_job_locked()
                if job_id is None:
                    _CONDITION.wait(INTERNAL_JOB_MAINTENANCE_POLL_SECONDS)
                    continue
                _ACTIVE_JOBS += 1

            thread = threading.Thread(
                target=job_runner,
                args=(job_id,),
                name=f"internal-job-{job_id}",
                daemon=True,
            )
            thread.start()
    finally:
        with _WORKERS_LOCK:
            if _SCHEDULER_THREAD is threading.current_thread():
                _SCHEDULER_THREAD = None
                _WORKERS_STARTED = False


def job_runner(job_id: int) -> None:
    global _ACTIVE_JOBS
    try:
        try:
            run_job(job_id)
        except InternalJobControlStop as exc:
            handle_control_stop(job_id, exc.status)
        except Exception as exc:  # noqa: BLE001 - keep the internal worker alive
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
        with _CONDITION:
            _ACTIVE_JOBS = max(0, _ACTIVE_JOBS - 1)
            _CONDITION.notify_all()


def max_concurrent_jobs() -> int:
    raw_value = os.getenv("INTERNAL_JOB_MAX_CONCURRENT")
    if raw_value is None:
        return INTERNAL_JOB_MAX_CONCURRENT_DEFAULT
    try:
        return max(1, int(raw_value))
    except ValueError:
        return INTERNAL_JOB_MAX_CONCURRENT_DEFAULT


def pop_next_startable_job_locked() -> int | None:
    for index, job_id in enumerate(list(_PENDING_JOB_IDS)):
        if pending_job_start_allowed(job_id):
            return _PENDING_JOB_IDS.pop(index)
    return None


def pending_job_start_allowed(job_id: int) -> bool:
    job = db.get_job(job_id)
    if not job or not db.is_internal_job(job) or job.get("status") not in {"queued", "running"}:
        return True
    return job_start_allowed(job)


def job_start_allowed(job: dict[str, Any], *, now: datetime | None = None) -> bool:
    job_kind = db.normalized_job_kind(job.get("job_kind"))
    if job_kind not in heavy_internal_job_kinds():
        return True
    mode = internal_job_maintenance_mode()
    if mode == "immediate":
        return True
    if mode == "paused":
        return False
    return maintenance_window_allows(now=now)


def internal_job_maintenance_mode() -> str:
    raw_value = setting_or_env("INTERNAL_JOB_MAINTENANCE_MODE", "immediate").strip().lower()
    normalized = raw_value.replace("-", "_")
    if normalized in {"window", "maintenance_window", "scheduled"}:
        return "window"
    if normalized in {"paused", "pause", "manual", "manual_only", "manual_only_queue"}:
        return "paused"
    return "immediate"


def maintenance_window_allows(*, now: datetime | None = None) -> bool:
    current = now or datetime.now()
    start_hour = hour_setting("INTERNAL_JOB_MAINTENANCE_START_HOUR", INTERNAL_JOB_MAINTENANCE_START_HOUR_DEFAULT)
    end_hour = hour_setting("INTERNAL_JOB_MAINTENANCE_END_HOUR", INTERNAL_JOB_MAINTENANCE_END_HOUR_DEFAULT)
    current_hour = int(current.hour)
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= current_hour < end_hour
    return current_hour >= start_hour or current_hour < end_hour


def heavy_internal_job_kinds() -> set[str]:
    raw_value = setting_or_env("INTERNAL_JOB_MAINTENANCE_JOB_KINDS", "")
    if not raw_value.strip():
        return set(HEAVY_INTERNAL_JOB_KINDS_DEFAULT)
    values = {
        db.normalized_job_kind(value)
        for value in raw_value.replace(";", ",").split(",")
        if value.strip()
    }
    return values or set(HEAVY_INTERNAL_JOB_KINDS_DEFAULT)


def hour_setting(name: str, default: int) -> int:
    raw_value = setting_or_env(name, str(default))
    try:
        return max(0, min(23, int(raw_value)))
    except ValueError:
        return max(0, min(23, default))


def setting_or_env(name: str, default: str) -> str:
    try:
        value = db.get_setting(name)
    except Exception:  # noqa: BLE001 - scheduler settings should fall back during early startup
        value = None
    return str(value if value is not None else os.getenv(name, default))


def run_job(job_id: int) -> None:
    job = db.get_job(job_id)
    if not job or not db.is_internal_job(job) or job.get("status") not in {"queued", "running"}:
        return

    job_kind = db.normalized_job_kind(job.get("job_kind"))
    handler = _HANDLERS.get(job_kind)
    if handler is None:
        raise ValueError(f"Unsupported internal job kind: {job_kind}")

    db.update_job(job_id, status="running", error=None)
    db.append_log(job_id, f"started internal job kind={job_kind}")
    check_job_control(job_id)
    handler(job_id, job)
    check_job_control(job_id)

    current = db.get_job(job_id)
    if current and current.get("status") == "running":
        db.update_job(job_id, status="done")
        db.append_log(job_id, "done")


def current_job_status(job_id: int) -> str | None:
    job = db.get_job(job_id)
    return str(job.get("status")) if job else None


def check_job_control(job_id: int) -> None:
    status = current_job_status(job_id)
    if status is None or status == "deleting":
        raise InternalJobControlStop("deleted")
    if status in {"pausing", "paused"}:
        raise InternalJobControlStop("paused")
    if status in {"canceling", "canceled"}:
        raise InternalJobControlStop("canceled")


def handle_control_stop(job_id: int, status: str) -> None:
    if status == "deleted":
        db.delete_job(job_id)
        return
    if status == "canceled":
        db.update_job(job_id, status="canceled", error=None)
        db.append_log(job_id, "canceled")
        return
    db.update_job(job_id, status="paused", error=None)
    db.append_log(job_id, "paused")
