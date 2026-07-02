from __future__ import annotations

import json
import os
import queue
import random
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from . import db, downloader
from .downloader import ytdlp_direct_cmdline_args
from .utils import env_bool, human_bytes, redact_sensitive_text, safe_join

PROVIDER_YOUTUBE = "youtube"
KIND_CHANNEL = "channel"
KIND_PLAYLIST = "playlist"
INITIAL_POLICY_FROM_NOW = "from_now"
INITIAL_POLICY_LATEST_N = "latest_n"
INITIAL_POLICY_FULL_BACKFILL = "full_backfill"

CHECK_STATUS_IDLE = "idle"
CHECK_STATUS_DUE = "due"
CHECK_STATUS_CHECKING = "checking"
CHECK_STATUS_BACKOFF = "backoff"
CHECK_STATUS_PAUSED = "paused"
CHECK_STATUS_ERROR = "error"

ITEM_STATUS_KNOWN = "known"
ITEM_STATUS_ELIGIBLE = "eligible"
ITEM_STATUS_QUEUED = "queued"
ITEM_STATUS_DOWNLOADING = "downloading"
ITEM_STATUS_DONE = "done"
ITEM_STATUS_SKIPPED = "skipped"
ITEM_STATUS_FAILED = "failed"
ITEM_STATUS_UNAVAILABLE = "unavailable"

SUBSCRIPTION_CHECK_MAX_CONCURRENT_DEFAULT = 1
SUBSCRIPTION_DOWNLOAD_MAX_CONCURRENT_DEFAULT = 1
SUBSCRIPTION_PER_SOURCE_DOWNLOAD_LIMIT_DEFAULT = 1
SUBSCRIPTION_DEFAULT_CHECK_INTERVAL_SECONDS = 6 * 60 * 60
SUBSCRIPTION_PROMOTE_BATCH_SIZE_DEFAULT = 5
SUBSCRIPTION_STARTUP_JITTER_MIN_SECONDS_DEFAULT = 30
SUBSCRIPTION_STARTUP_JITTER_MAX_SECONDS_DEFAULT = 300
SUBSCRIPTION_RETRY_BACKOFF_SECONDS_DEFAULT = (15 * 60, 60 * 60, 6 * 60 * 60, 24 * 60 * 60)
SUBSCRIPTION_DISCOVERY_RECENT_WINDOW_DEFAULT = 50
SUBSCRIPTION_CHECK_POLL_SECONDS_DEFAULT = 60
SUBSCRIPTION_DOWNLOAD_POLL_SECONDS_DEFAULT = 30
SUBSCRIPTION_DOWNLOAD_MAX_ATTEMPTS_DEFAULT = 3
SUBSCRIPTION_DOWNLOAD_RETRY_BACKOFF_SECONDS_DEFAULT = (30 * 60, 2 * 60 * 60, 12 * 60 * 60)
YOUTUBE_HOSTS = {
    "youtu.be",
    "youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
}

ITEM_STATUSES = (
    ITEM_STATUS_KNOWN,
    ITEM_STATUS_ELIGIBLE,
    ITEM_STATUS_QUEUED,
    ITEM_STATUS_DOWNLOADING,
    ITEM_STATUS_DONE,
    ITEM_STATUS_SKIPPED,
    ITEM_STATUS_FAILED,
    ITEM_STATUS_UNAVAILABLE,
)
ACTIVE_ITEM_STATUSES = (
    ITEM_STATUS_ELIGIBLE,
    ITEM_STATUS_QUEUED,
    ITEM_STATUS_DOWNLOADING,
    ITEM_STATUS_FAILED,
)


@dataclass(frozen=True)
class SubscriptionSource:
    kind: str
    source_url: str
    canonical_id: str
    title: str | None = None


class SubscriptionCheckAlreadyRunning(RuntimeError):
    pass


_scheduler_lock = threading.Lock()
_scheduler_stop_event = threading.Event()
_scheduler_wake_event = threading.Event()
_scheduler_thread: threading.Thread | None = None
_download_stop_event = threading.Event()
_download_wake_event = threading.Event()
_download_thread: threading.Thread | None = None
_active_check_ids: set[int] = set()
_active_download_item_ids: set[int] = set()


def scheduler_status() -> dict[str, Any]:
    return {
        "check_scheduler_enabled": subscription_check_scheduler_enabled(),
        "check_scheduler_running": scheduler_running(),
        "download_scheduler_enabled": subscription_download_scheduler_enabled(),
        "download_scheduler_running": download_scheduler_running(),
        "phase": "scheduled_downloads",
    }


def default_settings() -> dict[str, Any]:
    return {
        "check_max_concurrent": SUBSCRIPTION_CHECK_MAX_CONCURRENT_DEFAULT,
        "download_max_concurrent": SUBSCRIPTION_DOWNLOAD_MAX_CONCURRENT_DEFAULT,
        "per_source_download_limit": SUBSCRIPTION_PER_SOURCE_DOWNLOAD_LIMIT_DEFAULT,
        "default_check_interval_seconds": SUBSCRIPTION_DEFAULT_CHECK_INTERVAL_SECONDS,
        "promote_batch_size": SUBSCRIPTION_PROMOTE_BATCH_SIZE_DEFAULT,
        "startup_jitter_min_seconds": SUBSCRIPTION_STARTUP_JITTER_MIN_SECONDS_DEFAULT,
        "startup_jitter_max_seconds": SUBSCRIPTION_STARTUP_JITTER_MAX_SECONDS_DEFAULT,
        "retry_backoff_seconds": list(SUBSCRIPTION_RETRY_BACKOFF_SECONDS_DEFAULT),
        "discovery_recent_window": SUBSCRIPTION_DISCOVERY_RECENT_WINDOW_DEFAULT,
        "check_poll_seconds": SUBSCRIPTION_CHECK_POLL_SECONDS_DEFAULT,
        "download_poll_seconds": SUBSCRIPTION_DOWNLOAD_POLL_SECONDS_DEFAULT,
        "download_max_attempts": subscription_download_max_attempts(),
    }


def start_workers() -> None:
    global _scheduler_thread, _download_thread
    with _scheduler_lock:
        if subscription_check_scheduler_enabled() and not (_scheduler_thread and _scheduler_thread.is_alive()):
            _scheduler_stop_event.clear()
            _scheduler_wake_event.clear()
            db.recover_interrupted_subscription_checks(utc_now())
            _scheduler_thread = threading.Thread(
                target=subscription_scheduler_loop,
                name="hugcivi-subscription-check-scheduler",
                daemon=True,
            )
            _scheduler_thread.start()
        if subscription_download_scheduler_enabled() and not (_download_thread and _download_thread.is_alive()):
            _download_stop_event.clear()
            _download_wake_event.clear()
            recover_interrupted_subscription_downloads()
            _download_thread = threading.Thread(
                target=subscription_download_loop,
                name="hugcivi-subscription-download-worker",
                daemon=True,
            )
            _download_thread.start()


def stop_workers(timeout: float = 5.0) -> bool:
    global _scheduler_thread, _download_thread
    with _scheduler_lock:
        threads = [thread for thread in (_scheduler_thread, _download_thread) if thread]
        if not threads:
            return True
        _scheduler_stop_event.set()
        _scheduler_wake_event.set()
        _download_stop_event.set()
        _download_wake_event.set()
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.1, deadline - time.monotonic()))
    stopped = all(not thread.is_alive() for thread in threads)
    if stopped:
        with _scheduler_lock:
            if _scheduler_thread and not _scheduler_thread.is_alive():
                _scheduler_thread = None
            if _download_thread and not _download_thread.is_alive():
                _download_thread = None
    return stopped


def notify_scheduler_changed() -> None:
    _scheduler_wake_event.set()


def notify_download_scheduler_changed() -> None:
    _download_wake_event.set()


def scheduler_running() -> bool:
    with _scheduler_lock:
        return bool(_scheduler_thread and _scheduler_thread.is_alive())


def download_scheduler_running() -> bool:
    with _scheduler_lock:
        return bool(_download_thread and _download_thread.is_alive())


def subscription_scheduler_loop() -> None:
    startup_wait = startup_jitter_seconds()
    if startup_wait > 0:
        if _scheduler_wake_event.wait(startup_wait):
            _scheduler_wake_event.clear()
        if _scheduler_stop_event.is_set():
            return

    while not _scheduler_stop_event.is_set():
        ran = run_due_subscription_checks()
        if ran:
            continue
        _scheduler_wake_event.wait(subscription_check_poll_seconds())
        _scheduler_wake_event.clear()


def subscription_download_loop() -> None:
    while not _download_stop_event.is_set():
        ran = run_ready_subscription_downloads()
        if ran:
            continue
        _download_wake_event.wait(subscription_download_poll_seconds())
        _download_wake_event.clear()


def run_due_subscription_checks() -> int:
    due = db.list_due_subscriptions(utc_now(), limit=SUBSCRIPTION_CHECK_MAX_CONCURRENT_DEFAULT)
    ran = 0
    for subscription in due:
        if _scheduler_stop_event.is_set():
            break
        try:
            check_subscription_now(int(subscription["id"]), scheduled=True)
            ran += 1
        except SubscriptionCheckAlreadyRunning:
            continue
        except Exception as exc:
            print(redact_sensitive_text(f"subscription check failed: {exc}"), file=sys.stderr, flush=True)
            ran += 1
    return ran


def run_ready_subscription_downloads() -> int:
    ready = db.list_ready_subscription_items(
        utc_now(),
        limit=SUBSCRIPTION_DOWNLOAD_MAX_CONCURRENT_DEFAULT,
        max_attempts=subscription_download_max_attempts(),
    )
    ran = 0
    for item in ready:
        if _download_stop_event.is_set():
            break
        try:
            download_subscription_item(item)
            ran += 1
        except Exception as exc:
            print(redact_sensitive_text(f"subscription download failed: {exc}"), file=sys.stderr, flush=True)
            ran += 1
    return ran


def recover_interrupted_subscription_downloads() -> None:
    db.recover_interrupted_subscription_downloads(utc_now())


def subscription_check_scheduler_enabled() -> bool:
    return env_bool("SUBSCRIPTION_CHECK_SCHEDULER_ENABLED", True)


def subscription_download_scheduler_enabled() -> bool:
    return env_bool("SUBSCRIPTION_DOWNLOAD_SCHEDULER_ENABLED", True)


def subscription_check_poll_seconds() -> int:
    return normalize_optional_int(os.getenv("SUBSCRIPTION_CHECK_POLL_SECONDS"), minimum=1) or SUBSCRIPTION_CHECK_POLL_SECONDS_DEFAULT


def subscription_download_poll_seconds() -> int:
    return normalize_optional_int(os.getenv("SUBSCRIPTION_DOWNLOAD_POLL_SECONDS"), minimum=1) or SUBSCRIPTION_DOWNLOAD_POLL_SECONDS_DEFAULT


def subscription_download_max_attempts() -> int:
    return normalize_optional_int(os.getenv("SUBSCRIPTION_DOWNLOAD_MAX_ATTEMPTS"), minimum=1) or SUBSCRIPTION_DOWNLOAD_MAX_ATTEMPTS_DEFAULT


def startup_jitter_seconds() -> int:
    minimum = normalize_optional_int(os.getenv("SUBSCRIPTION_STARTUP_JITTER_MIN_SECONDS"), minimum=0)
    maximum = normalize_optional_int(os.getenv("SUBSCRIPTION_STARTUP_JITTER_MAX_SECONDS"), minimum=0)
    min_seconds = SUBSCRIPTION_STARTUP_JITTER_MIN_SECONDS_DEFAULT if minimum is None else minimum
    max_seconds = SUBSCRIPTION_STARTUP_JITTER_MAX_SECONDS_DEFAULT if maximum is None else maximum
    if max_seconds < min_seconds:
        min_seconds, max_seconds = max_seconds, min_seconds
    if max_seconds <= 0:
        return 0
    return random.randint(min_seconds, max_seconds)


def list_subscription_payloads(limit: int = 100) -> list[dict[str, Any]]:
    rows = db.list_subscriptions(limit=limit)
    subscription_ids = [int(row["id"]) for row in rows]
    counts = db.subscription_item_counts(subscription_ids)
    storage = db.subscription_item_storage(subscription_ids)
    return [
        subscription_payload(row, counts.get(int(row["id"]), {}), storage.get(int(row["id"]), 0))
        for row in rows
    ]


def get_subscription_payload(subscription_id: int) -> dict[str, Any] | None:
    row = db.get_subscription(subscription_id)
    if not row:
        return None
    counts = db.subscription_item_counts([subscription_id])
    storage = db.subscription_item_storage([subscription_id])
    return subscription_payload(row, counts.get(subscription_id, {}), storage.get(subscription_id, 0))


def list_item_payloads(subscription_id: int, limit: int = 500) -> list[dict[str, Any]]:
    return [item_payload(row) for row in db.list_subscription_items(subscription_id, limit=limit)]


def list_item_summary_payload(
    *,
    status: Any = "active",
    subscription_id: int | None = None,
    limit: int = 100,
    cursor: int | None = None,
) -> dict[str, Any]:
    status_filter, statuses = normalize_item_status_filter(status)
    safe_limit = max(1, min(500, int(limit)))
    before_id = normalize_cursor(cursor)
    rows = db.list_subscription_item_summaries(
        statuses=statuses,
        subscription_id=subscription_id,
        limit=safe_limit,
        before_id=before_id,
    )
    items = [item_payload(row) for row in rows]
    raw_counts = db.subscription_item_status_counts(subscription_id=subscription_id)
    counts = {item_status: int(raw_counts.get(item_status, 0)) for item_status in ITEM_STATUSES}
    return {
        "items": items,
        "counts": counts,
        "next_cursor": items[-1]["id"] if len(items) >= safe_limit else None,
        "status": status_filter,
    }


def get_item_payload(item_id: int) -> dict[str, Any] | None:
    row = db.get_subscription_item(item_id)
    return item_payload(row) if row else None


def create_subscription(payload: dict[str, Any]) -> int:
    source = parse_subscription_source(str(payload.get("url") or payload.get("source_url") or ""))
    initial_policy = normalize_initial_policy(payload.get("initial_policy"))
    initial_limit = normalize_optional_int(payload.get("initial_limit"), minimum=1)
    check_interval_seconds = normalize_check_interval(payload.get("check_interval_seconds"))
    auto_queue = normalize_bool(payload.get("auto_queue"), default=True)
    enabled = normalize_bool(payload.get("enabled"), default=True)
    now = utc_now()
    subscription_id = db.create_subscription(
        provider=PROVIDER_YOUTUBE,
        kind=source.kind,
        source_url=source.source_url,
        canonical_id=source.canonical_id,
        title=source.title,
        enabled=enabled,
        auto_queue=auto_queue,
        initial_policy=initial_policy,
        initial_limit=initial_limit,
        cutoff_published_at=now if initial_policy == INITIAL_POLICY_FROM_NOW else None,
        check_interval_seconds=check_interval_seconds,
        next_check_at=now if enabled else None,
        check_status=CHECK_STATUS_IDLE if enabled else CHECK_STATUS_PAUSED,
        metadata={"source": "youtube", "phase": "created"},
    )
    notify_scheduler_changed()
    return subscription_id


def update_subscription(subscription_id: int, payload: dict[str, Any]) -> None:
    fields: dict[str, Any] = {}
    if "enabled" in payload:
        enabled = normalize_bool(payload.get("enabled"), default=True)
        fields["enabled"] = 1 if enabled else 0
        fields["check_status"] = CHECK_STATUS_IDLE if enabled else CHECK_STATUS_PAUSED
        fields["next_check_at"] = utc_now() if enabled else None
    if "auto_queue" in payload:
        fields["auto_queue"] = 1 if normalize_bool(payload.get("auto_queue"), default=True) else 0
    if "check_interval_seconds" in payload:
        fields["check_interval_seconds"] = normalize_check_interval(payload.get("check_interval_seconds"))
    if "title" in payload:
        title = str(payload.get("title") or "").strip()
        fields["title"] = title or None
    if not fields:
        return
    db.update_subscription(subscription_id, **fields)
    notify_scheduler_changed()


def delete_subscription(subscription_id: int) -> bool:
    deleted = db.delete_subscription(subscription_id)
    if deleted:
        notify_scheduler_changed()
    return deleted


def queue_subscription_item(item_id: int, *, reset_attempts: bool = False) -> dict[str, Any]:
    item = db.get_subscription_item(item_id)
    if not item:
        raise ValueError("subscription item not found")
    status = str(item.get("status") or "")
    if status in {ITEM_STATUS_DONE, ITEM_STATUS_DOWNLOADING, ITEM_STATUS_UNAVAILABLE}:
        raise ValueError(f"subscription item cannot be queued from status {status}")
    now = utc_now()
    fields: dict[str, Any] = {
        "status": ITEM_STATUS_QUEUED,
        "queued_at": now,
        "next_attempt_at": now,
        "error": None,
    }
    if reset_attempts:
        fields["attempt_count"] = 0
    db.update_subscription_item(item_id, **fields)
    db.append_subscription_item_log(item_id, "queued by user")
    notify_download_scheduler_changed()
    queued = db.get_subscription_item(item_id)
    if not queued:
        raise ValueError("subscription item not found")
    return item_payload(queued)


def skip_subscription_item(item_id: int) -> dict[str, Any]:
    item = db.get_subscription_item(item_id)
    if not item:
        raise ValueError("subscription item not found")
    status = str(item.get("status") or "")
    if status == ITEM_STATUS_DOWNLOADING:
        raise ValueError("downloading subscription item cannot be skipped")
    if status == ITEM_STATUS_DONE:
        raise ValueError("done subscription item cannot be skipped")
    db.update_subscription_item(
        item_id,
        status=ITEM_STATUS_SKIPPED,
        policy_reason="manual",
        next_attempt_at=None,
        error=None,
    )
    db.append_subscription_item_log(item_id, "skipped by user")
    skipped = db.get_subscription_item(item_id)
    if not skipped:
        raise ValueError("subscription item not found")
    return item_payload(skipped)


def retry_subscription_item(item_id: int) -> dict[str, Any]:
    item = db.get_subscription_item(item_id)
    if not item:
        raise ValueError("subscription item not found")
    if str(item.get("status") or "") not in {ITEM_STATUS_FAILED, ITEM_STATUS_SKIPPED}:
        raise ValueError("only failed or skipped subscription items can be retried")
    return queue_subscription_item(item_id, reset_attempts=True)


def check_subscription_now(subscription_id: int, *, scheduled: bool = False) -> dict[str, Any]:
    with _scheduler_lock:
        if subscription_id in _active_check_ids:
            raise SubscriptionCheckAlreadyRunning("subscription check already running")
        _active_check_ids.add(subscription_id)
    try:
        return _check_subscription_now(subscription_id, scheduled=scheduled)
    finally:
        with _scheduler_lock:
            _active_check_ids.discard(subscription_id)


def _check_subscription_now(subscription_id: int, *, scheduled: bool = False) -> dict[str, Any]:
    subscription = db.get_subscription(subscription_id)
    if not subscription:
        raise ValueError("subscription not found")
    if scheduled and not subscription.get("enabled"):
        return {
            "entry_count": 0,
            "new_count": 0,
            "eligible_count": 0,
            "skipped": "disabled",
        }

    started_at = utc_now()
    db.update_subscription(
        subscription_id,
        check_status=CHECK_STATUS_CHECKING,
        last_check_started_at=started_at,
        last_error=None,
    )
    try:
        info = yt_dlp_subscription_info(str(subscription.get("source_url") or ""))
        result = store_discovered_items(subscription, info)
    except Exception as exc:
        failure_count = int(subscription.get("failure_count") or 0) + 1
        finished_at = utc_now()
        enabled = bool(subscription.get("enabled"))
        db.update_subscription(
            subscription_id,
            check_status=CHECK_STATUS_BACKOFF if enabled else CHECK_STATUS_PAUSED,
            last_checked_at=finished_at,
            last_check_finished_at=finished_at,
            last_error=str(exc),
            failure_count=failure_count,
            next_check_at=next_failure_check_at(failure_count, finished_at) if enabled else None,
        )
        raise

    finished_at = utc_now()
    enabled = bool(subscription.get("enabled"))
    metadata = parse_json_object(subscription.get("metadata_json"))
    metadata["last_discovery"] = {
        "source": "yt-dlp",
        "entry_count": result["entry_count"],
        "new_count": result["new_count"],
        "eligible_count": result["eligible_count"],
    }
    db.update_subscription(
        subscription_id,
        first_check_completed=1,
        check_status=CHECK_STATUS_IDLE if subscription.get("enabled") else CHECK_STATUS_PAUSED,
        last_checked_at=finished_at,
        last_success_at=finished_at,
        last_check_finished_at=finished_at,
        failure_count=0,
        metadata_json=json.dumps(metadata, ensure_ascii=False),
        last_seen_provider_item_id=result.get("last_seen_provider_item_id"),
        last_seen_published_at=result.get("last_seen_published_at"),
        next_check_at=next_success_check_at(subscription, finished_at) if enabled else None,
    )
    return result


def store_discovered_items(subscription: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    subscription_id = int(subscription["id"])
    entries = subscription_entries(info)
    policy = str(subscription.get("initial_policy") or INITIAL_POLICY_FROM_NOW)
    first_check_completed = bool(subscription.get("first_check_completed"))
    latest_limit = int(subscription.get("initial_limit") or SUBSCRIPTION_PROMOTE_BATCH_SIZE_DEFAULT)
    cutoff = parse_datetime(str(subscription.get("cutoff_published_at") or "")) if policy == INITIAL_POLICY_FROM_NOW else None
    existing_ids = {str(item.get("provider_item_id")) for item in db.list_subscription_items(subscription_id, limit=1000)}

    new_count = 0
    eligible_count = 0
    last_seen_provider_item_id = ""
    last_seen_published_at = ""
    for index, entry in enumerate(entries):
        video_id = entry.get("id")
        if not video_id:
            continue
        published_at = entry.get("published_at") or ""
        if index == 0:
            last_seen_provider_item_id = video_id
            last_seen_published_at = published_at
        status, policy_reason = item_policy(subscription, entry, index, first_check_completed, latest_limit, cutoff)
        if video_id in existing_ids:
            new_count += 0
        else:
            new_count += 1
            if status == ITEM_STATUS_ELIGIBLE:
                eligible_count += 1
        db.upsert_subscription_item(
            subscription_id=subscription_id,
            provider_item_id=video_id,
            url=entry["url"],
            title=entry.get("title"),
            published_at=published_at or None,
            status=status,
            policy_reason=policy_reason,
            metadata=entry.get("metadata") or {},
        )

    if eligible_count:
        notify_download_scheduler_changed()

    return {
        "entry_count": len(entries),
        "new_count": new_count,
        "eligible_count": eligible_count,
        "last_seen_provider_item_id": last_seen_provider_item_id or None,
        "last_seen_published_at": last_seen_published_at or None,
    }


def item_policy(
    subscription: dict[str, Any],
    entry: dict[str, Any],
    index: int,
    first_check_completed: bool,
    latest_limit: int,
    cutoff: datetime | None,
) -> tuple[str, str]:
    if first_check_completed:
        return ITEM_STATUS_ELIGIBLE, "new_after_first_check"
    policy = str(subscription.get("initial_policy") or INITIAL_POLICY_FROM_NOW)
    if policy == INITIAL_POLICY_FULL_BACKFILL:
        return ITEM_STATUS_ELIGIBLE, INITIAL_POLICY_FULL_BACKFILL
    if policy == INITIAL_POLICY_LATEST_N:
        if index < latest_limit:
            return ITEM_STATUS_ELIGIBLE, INITIAL_POLICY_LATEST_N
        return ITEM_STATUS_KNOWN, "outside_latest_n"
    published_at = parse_datetime(str(entry.get("published_at") or ""))
    if cutoff is not None and published_at is not None and published_at >= cutoff:
        return ITEM_STATUS_ELIGIBLE, INITIAL_POLICY_FROM_NOW
    return ITEM_STATUS_KNOWN, "older_than_cutoff"


def download_subscription_item(item: dict[str, Any]) -> None:
    item_id = int(item["id"])
    with _scheduler_lock:
        if item_id in _active_download_item_ids:
            return
        _active_download_item_ids.add(item_id)
    try:
        _download_subscription_item(item)
    finally:
        with _scheduler_lock:
            _active_download_item_ids.discard(item_id)


def _download_subscription_item(item: dict[str, Any]) -> None:
    item_id = int(item["id"])
    source_url = str(item.get("url") or "")
    if not source_url:
        db.update_subscription_item(item_id, status=ITEM_STATUS_SKIPPED, error="Subscription item URL is missing.")
        return

    started_at = utc_now()
    attempt_count = int(item.get("attempt_count") or 0) + 1
    db.update_subscription_item(
        item_id,
        status=ITEM_STATUS_DOWNLOADING,
        queued_at=item.get("queued_at") or started_at,
        download_started_at=started_at,
        download_finished_at=None,
        last_attempt_at=started_at,
        next_attempt_at=None,
        attempt_count=attempt_count,
        error=None,
        progress_bytes=0,
        total_bytes=None,
    )
    db.append_subscription_item_log(item_id, f"yt-dlp start: source={redact_sensitive_text(source_url)}")

    target = subscription_item_target_path(item)
    target.mkdir(parents=True, exist_ok=True)
    db.update_subscription_item(item_id, target_dir=str(target))
    write_subscription_archive_metadata(target, item)

    try:
        command = downloader.yt_dlp_command(f"ytdl:{source_url}", target, extra_args=ytdlp_direct_cmdline_args())
        run_subscription_download_process(item_id, command, target)
        files = downloader.gallery_dl_downloaded_files(target)
        selected = subscription_downloaded_file(item, files)
        if not selected:
            raise RuntimeError("No media file was downloaded for the subscription item.")
        size = selected.stat().st_size
        finished_at = utc_now()
        db.update_subscription_item(
            item_id,
            status=ITEM_STATUS_DONE,
            download_finished_at=finished_at,
            target_dir=str(target),
            filename=selected.name,
            progress_bytes=size,
            total_bytes=size,
            next_attempt_at=None,
            error=None,
        )
        db.append_subscription_item_log(item_id, f"saved subscription item: {selected} ({downloader.human_bytes(size)})")
    except Exception as exc:
        failed_at = utc_now()
        retryable = attempt_count < subscription_download_max_attempts()
        next_attempt = next_download_retry_at(attempt_count, failed_at) if retryable else None
        db.update_subscription_item(
            item_id,
            status=ITEM_STATUS_QUEUED if retryable else ITEM_STATUS_FAILED,
            download_finished_at=failed_at,
            next_attempt_at=next_attempt,
            error=str(exc),
        )
        db.append_subscription_item_log(item_id, f"download failed: {exc}")
        raise


def subscription_item_target_path(item: dict[str, Any]) -> Path:
    subscription_source = str(item.get("subscription_source_url") or item.get("url") or "")
    source_for_target = f"ytdl:{subscription_source}" if not subscription_source.startswith("ytdl:") else subscription_source
    info = {
        "channel": item.get("subscription_title"),
        "uploader": item.get("subscription_title"),
        "playlist_uploader": item.get("subscription_title"),
    }
    target_parts = downloader.gallery_dl_target_path_parts(source_for_target, info)
    return safe_join(downloader.DATA_ROOT, "gallery-dl", *target_parts)


def write_subscription_archive_metadata(target: Path, item: dict[str, Any]) -> None:
    downloader.write_metadata(
        target,
        "_subscription_metadata.json",
        {
            **downloader.metadata_stamp(),
            "source": "youtube-subscription",
            "subscription_id": item.get("subscription_id"),
            "subscription_source_url": item.get("subscription_source_url"),
            "provider_item_id": item.get("provider_item_id"),
            "item_url": item.get("url"),
            "title": item.get("title"),
        },
    )


def run_subscription_download_process(item_id: int, command: list[str], target: Path) -> None:
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **downloader.controlled_process_kwargs(),
        )
        output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        output_done = process.stdout is None
        if process.stdout:
            reader = threading.Thread(
                target=read_subscription_process_output,
                args=(process.stdout, output_queue),
                name=f"subscription-download-output-{item_id}",
                daemon=True,
            )
            reader.start()

        last_update = 0.0
        while True:
            if _download_stop_event.is_set():
                raise RuntimeError("subscription download stopped")
            try:
                event = output_queue.get(timeout=1.0)
            except queue.Empty:
                event = None
            if event is not None and event[0] == "done":
                output_done = True
            elif event is not None and event[1] is not None:
                message = event[1].strip()
                if message:
                    db.append_subscription_item_log(item_id, f"yt-dlp: {message[:1000]}")

            now = time.time()
            if now - last_update >= 1.0:
                update_subscription_download_progress(item_id, target)
                last_update = now
            if process.poll() is not None and output_done and output_queue.empty():
                break

        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(f"yt-dlp exited with code {return_code}")
    except Exception:
        if process and process.poll() is None:
            stop_subscription_process(item_id, process)
        raise


def read_subscription_process_output(pipe: Any, output_queue: queue.Queue[tuple[str, str | None]]) -> None:
    try:
        for line in pipe:
            output_queue.put(("line", line))
    finally:
        output_queue.put(("done", None))


def stop_subscription_process(item_id: int, process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
            db.append_subscription_item_log(item_id, "yt-dlp process group terminate requested")
        except ProcessLookupError:
            return
        except OSError as exc:
            db.append_subscription_item_log(item_id, f"yt-dlp process group terminate failed: {exc}; terminating parent")
            process.terminate()
    else:
        process.terminate()
        db.append_subscription_item_log(item_id, "yt-dlp process terminate requested")
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
                db.append_subscription_item_log(item_id, "yt-dlp process group kill requested")
            except ProcessLookupError:
                return
            except OSError as exc:
                db.append_subscription_item_log(item_id, f"yt-dlp process group kill failed: {exc}; killing parent")
                process.kill()
        else:
            process.kill()


def update_subscription_download_progress(item_id: int, target: Path) -> None:
    latest_name, size = downloader.gallery_dl_progress_snapshot(target, max_items=downloader.progress_scan_max_files())
    db.update_subscription_item(
        item_id,
        filename=latest_name,
        progress_bytes=size,
        total_bytes=None,
    )


def subscription_downloaded_file(item: dict[str, Any], files: list[Path]) -> Path | None:
    if not files:
        return None
    provider_item_id = str(item.get("provider_item_id") or "")
    if provider_item_id:
        for path in sorted(files, key=lambda candidate: candidate.stat().st_mtime, reverse=True):
            if provider_item_id in path.stem or provider_item_id in path.name:
                return path
    return max(files, key=lambda candidate: candidate.stat().st_mtime)


def next_success_check_at(subscription: dict[str, Any], from_time: str) -> str:
    interval = normalize_check_interval(subscription.get("check_interval_seconds"))
    return iso_after_seconds(from_time, jittered_interval_seconds(interval))


def next_failure_check_at(failure_count: int, from_time: str) -> str:
    return iso_after_seconds(from_time, failure_backoff_seconds(failure_count))


def jittered_interval_seconds(interval_seconds: int) -> int:
    spread = max(0, int(interval_seconds * 0.1))
    if spread <= 0:
        return interval_seconds
    return max(3600, interval_seconds + random.randint(-spread, spread))


def failure_backoff_seconds(failure_count: int) -> int:
    index = max(0, int(failure_count) - 1)
    return SUBSCRIPTION_RETRY_BACKOFF_SECONDS_DEFAULT[min(index, len(SUBSCRIPTION_RETRY_BACKOFF_SECONDS_DEFAULT) - 1)]


def next_download_retry_at(attempt_count: int, from_time: str) -> str:
    index = max(0, int(attempt_count) - 1)
    delay = SUBSCRIPTION_DOWNLOAD_RETRY_BACKOFF_SECONDS_DEFAULT[
        min(index, len(SUBSCRIPTION_DOWNLOAD_RETRY_BACKOFF_SECONDS_DEFAULT) - 1)
    ]
    return iso_after_seconds(from_time, delay)


def iso_after_seconds(from_time: str, seconds: int) -> str:
    base = parse_datetime(from_time) or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base + timedelta(seconds=max(0, int(seconds)))).isoformat(timespec="seconds")


def yt_dlp_subscription_info(source_url: str) -> dict[str, Any]:
    timeout = normalize_optional_int(os.getenv("SUBSCRIPTION_DISCOVERY_TIMEOUT_SECONDS"), minimum=1) or 90
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-config",
        "--skip-download",
        "--dump-single-json",
        "--flat-playlist",
        "--no-warnings",
        *ytdlp_direct_cmdline_args(),
        source_url,
    ]
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
    return parsed if isinstance(parsed, dict) else {}


def subscription_entries(info: dict[str, Any]) -> list[dict[str, Any]]:
    raw_entries = info.get("entries")
    candidates = raw_entries if isinstance(raw_entries, list) else [info]
    entries: list[dict[str, Any]] = []
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        video_id = meaningful_text(raw.get("id")) or youtube_video_id_from_url(meaningful_text(raw.get("url")))
        if not video_id:
            continue
        url = entry_url(raw, video_id)
        title = meaningful_text(raw.get("title"))
        published_at = published_at_from_entry(raw)
        entries.append(
            {
                "id": video_id,
                "url": url,
                "title": title or None,
                "published_at": published_at,
                "metadata": {
                    key: raw.get(key)
                    for key in ("id", "title", "channel", "uploader", "duration", "view_count", "webpage_url")
                    if raw.get(key) is not None
                },
            }
        )
    return sorted(entries, key=lambda item: item.get("published_at") or "", reverse=True)


def parse_subscription_source(value: str) -> SubscriptionSource:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("YouTube channel or playlist URL is required.")
    host = normalized_host(parsed.netloc)
    if host not in YOUTUBE_HOSTS:
        raise ValueError("Only YouTube channel or playlist URLs are supported.")

    playlist_id = youtube_playlist_id(parsed)
    if playlist_id:
        return SubscriptionSource(KIND_PLAYLIST, url, playlist_id, playlist_id)

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if host == "youtu.be":
        raise ValueError("A video URL is not a subscription source.")
    if parts and parts[0].startswith("@"):
        handle = parts[0].lstrip("@")
        return SubscriptionSource(KIND_CHANNEL, url, f"@{handle}", handle)
    if len(parts) >= 2 and parts[0] in {"channel", "c", "user"}:
        return SubscriptionSource(KIND_CHANNEL, url, "/".join(parts[:2]), parts[1])
    raise ValueError("YouTube channel or playlist URL is required.")


def normalize_initial_policy(value: Any) -> str:
    policy = str(value or INITIAL_POLICY_FROM_NOW).strip().lower()
    if policy in {INITIAL_POLICY_FROM_NOW, INITIAL_POLICY_LATEST_N, INITIAL_POLICY_FULL_BACKFILL}:
        return policy
    return INITIAL_POLICY_FROM_NOW


def normalize_check_interval(value: Any) -> int:
    interval = normalize_optional_int(value, minimum=3600)
    return interval or SUBSCRIPTION_DEFAULT_CHECK_INTERVAL_SECONDS


def normalize_item_status_filter(value: Any) -> tuple[str, tuple[str, ...] | None]:
    status = str(value or "active").strip().lower() or "active"
    if status in {"active", "default"}:
        return "active", ACTIVE_ITEM_STATUSES
    if status == "all":
        return "all", None
    if status in ITEM_STATUSES:
        return status, (status,)
    raise ValueError(f"unsupported subscription item status filter: {status}")


def normalize_cursor(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        cursor = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("cursor must be a subscription item id") from exc
    if cursor < 1:
        raise ValueError("cursor must be a subscription item id")
    return cursor


def normalize_optional_int(value: Any, *, minimum: int = 0) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return None


def normalize_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def normalized_host(value: str) -> str:
    return value.lower().strip(".").removeprefix("www.")


def youtube_playlist_id(parsed: Any) -> str:
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        if key == "list" and value:
            return value
    return ""


def youtube_video_id_from_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    if normalized_host(parsed.netloc) == "youtu.be":
        return parsed.path.strip("/")
    for key, query_value in parse_qsl(parsed.query, keep_blank_values=False):
        if key == "v" and query_value:
            return query_value
    return ""


def entry_url(raw: dict[str, Any], video_id: str) -> str:
    for key in ("webpage_url", "url"):
        value = meaningful_text(raw.get(key))
        if value.startswith(("http://", "https://")):
            return value
    return f"https://www.youtube.com/watch?v={video_id}"


def published_at_from_entry(raw: dict[str, Any]) -> str:
    timestamp = normalize_optional_int(raw.get("timestamp") or raw.get("release_timestamp"), minimum=0)
    if timestamp is not None:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")
    upload_date = meaningful_text(raw.get("upload_date"))
    if len(upload_date) == 8 and upload_date.isdecimal():
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}T00:00:00+00:00"
    return ""


def parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def meaningful_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if not text or text.upper() == "NA" else text


def subscription_payload(
    row: dict[str, Any],
    counts: dict[str, int] | None = None,
    storage_bytes: int = 0,
) -> dict[str, Any]:
    item_counts = {status: int((counts or {}).get(status, 0)) for status in ITEM_STATUSES}
    stored_bytes = max(0, int(storage_bytes or 0))
    return {
        "id": int(row["id"]),
        "provider": str(row.get("provider") or PROVIDER_YOUTUBE),
        "kind": str(row.get("kind") or ""),
        "source_url": str(row.get("source_url") or ""),
        "canonical_id": row.get("canonical_id"),
        "title": row.get("title"),
        "enabled": bool(row.get("enabled")),
        "auto_queue": bool(row.get("auto_queue")),
        "initial_policy": str(row.get("initial_policy") or INITIAL_POLICY_FROM_NOW),
        "initial_limit": row.get("initial_limit"),
        "cutoff_published_at": row.get("cutoff_published_at"),
        "first_check_completed": bool(row.get("first_check_completed")),
        "check_interval_seconds": int(row.get("check_interval_seconds") or SUBSCRIPTION_DEFAULT_CHECK_INTERVAL_SECONDS),
        "next_check_at": row.get("next_check_at"),
        "last_checked_at": row.get("last_checked_at"),
        "last_success_at": row.get("last_success_at"),
        "last_error": row.get("last_error"),
        "failure_count": int(row.get("failure_count") or 0),
        "check_status": str(row.get("check_status") or CHECK_STATUS_IDLE),
        "last_check_started_at": row.get("last_check_started_at"),
        "last_check_finished_at": row.get("last_check_finished_at"),
        "last_seen_provider_item_id": row.get("last_seen_provider_item_id"),
        "last_seen_published_at": row.get("last_seen_published_at"),
        "metadata": parse_json_object(row.get("metadata_json")),
        "item_counts": item_counts,
        "storage_bytes": stored_bytes,
        "storage_human": human_bytes(stored_bytes),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def item_payload(row: dict[str, Any]) -> dict[str, Any]:
    progress_bytes = int(row.get("progress_bytes") or 0)
    total_bytes = normalize_optional_int(row.get("total_bytes"), minimum=0)
    percent = None
    if total_bytes and total_bytes > 0:
        percent = round(min(100.0, max(0.0, progress_bytes * 100.0 / total_bytes)), 1)
    payload = {
        "id": int(row["id"]),
        "subscription_id": int(row["subscription_id"]),
        "provider_item_id": str(row.get("provider_item_id") or ""),
        "url": str(row.get("url") or ""),
        "title": row.get("title"),
        "published_at": row.get("published_at"),
        "discovered_at": row.get("discovered_at"),
        "status": str(row.get("status") or ITEM_STATUS_KNOWN),
        "policy_reason": row.get("policy_reason"),
        "queued_at": row.get("queued_at"),
        "download_started_at": row.get("download_started_at"),
        "download_finished_at": row.get("download_finished_at"),
        "target_dir": row.get("target_dir"),
        "filename": row.get("filename"),
        "progress_bytes": progress_bytes,
        "total_bytes": total_bytes,
        "progress_human": human_bytes(progress_bytes),
        "total_human": human_bytes(total_bytes) if total_bytes is not None else None,
        "percent": percent,
        "attempt_count": int(row.get("attempt_count") or 0),
        "last_attempt_at": row.get("last_attempt_at"),
        "next_attempt_at": row.get("next_attempt_at"),
        "error": row.get("error"),
        "log": row.get("log") or "",
        "metadata": parse_json_object(row.get("metadata_json")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    if "subscription_title" in row or "subscription_kind" in row or "subscription_enabled" in row:
        payload.update(
            {
                "subscription_title": row.get("subscription_title"),
                "subscription_kind": row.get("subscription_kind"),
                "subscription_enabled": bool(row.get("subscription_enabled")),
                "subscription_provider": row.get("subscription_provider"),
                "subscription_source_url": row.get("subscription_source_url"),
                "subscription_canonical_id": row.get("subscription_canonical_id"),
                "subscription_auto_queue": bool(row.get("subscription_auto_queue")),
            }
        )
    return payload


def parse_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
