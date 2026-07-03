from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


class JsonRequest:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    async def json(self) -> object:
        return self.payload


@pytest.fixture()
def app_modules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_root = tmp_path / "data"
    config_root = tmp_path / "config"
    data_root.mkdir()
    config_root.mkdir()

    monkeypatch.setenv("DATA_ROOT", str(data_root))
    monkeypatch.setenv("DB_PATH", str(config_root / "jobs.sqlite3"))
    monkeypatch.setenv("APP_PASSWORD", "test-password-that-is-long")

    import app.db as db
    import app.downloader as downloader
    import app.subscriptions as subscriptions
    import app.main as main

    importlib.reload(db)
    importlib.reload(downloader)
    importlib.reload(subscriptions)
    importlib.reload(main)
    db.init_db()
    return db, subscriptions, main, config_root


AGGREGATE_ITEM_STATUSES = (
    "known",
    "eligible",
    "queued",
    "downloading",
    "done",
    "skipped",
    "failed",
    "unavailable",
)


def seed_subscription_items(db, *, title: str = "Aggregate Channel", enabled: bool = True) -> tuple[int, dict[str, int]]:
    subscription_id = db.create_subscription(
        kind="channel",
        source_url=f"https://www.youtube.com/@{title.lower().replace(' ', '-')}",
        canonical_id=f"@{title.lower().replace(' ', '-')}",
        title=title,
        enabled=enabled,
    )
    item_ids: dict[str, int] = {}
    for index, status in enumerate(AGGREGATE_ITEM_STATUSES, start=1):
        item_ids[status] = db.upsert_subscription_item(
            subscription_id=subscription_id,
            provider_item_id=f"{status}-item",
            url=f"https://www.youtube.com/watch?v={status}",
            title=f"{status} video",
            published_at=f"2026-07-02T00:0{index}:00+00:00",
            status=status,
        )
    return subscription_id, item_ids


def test_subscription_tables_and_indexes_are_created(app_modules: tuple) -> None:
    db, _subscriptions, _main, _config_root = app_modules

    with db.connect() as conn:
        names = {
            str(row["name"])
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type IN ('table', 'index')
                """
            ).fetchall()
        }

    assert "subscriptions" in names
    assert "subscription_items" in names
    assert "idx_subscriptions_provider_canonical" in names
    assert "idx_subscriptions_due" in names
    assert "idx_subscription_items_ready" in names
    assert "idx_subscription_items_provider_item" in names
    with db.connect() as conn:
        item_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(subscription_items)").fetchall()}
    assert "log" in item_columns


def test_subscription_migration_is_additive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_root = tmp_path / "config"
    config_root.mkdir()
    db_path = config_root / "jobs.sqlite3"
    monkeypatch.setenv("DB_PATH", str(db_path))

    import app.db as db

    importlib.reload(db)
    with db.connect() as conn:
        conn.execute(
            """
            CREATE TABLE jobs (
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
        conn.execute(
            """
            INSERT INTO jobs (created_at, updated_at, input_text, parsed_json, source, status)
            VALUES ('2026-07-02T00:00:00+00:00', '2026-07-02T00:00:00+00:00', 'input', '{}', 'generic', 'done')
            """
        )
        conn.commit()

    db.init_db()

    with db.connect() as conn:
        job_count = conn.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()["count"]
        subscription_count = conn.execute("SELECT COUNT(*) AS count FROM subscriptions").fetchone()["count"]

    assert job_count == 1
    assert subscription_count == 0


def test_subscription_helpers_create_list_and_preserve_item_state(app_modules: tuple) -> None:
    db, subscriptions, _main, _config_root = app_modules

    subscription_id = db.create_subscription(
        kind="channel",
        source_url="https://www.youtube.com/@example",
        canonical_id="UC_example",
        title="Example Channel",
        metadata={"channel": "Example Channel"},
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.create_subscription(
            kind="channel",
            source_url="https://www.youtube.com/@example-copy",
            canonical_id="UC_example",
            title="Duplicate",
        )

    item_id = db.upsert_subscription_item(
        subscription_id=subscription_id,
        provider_item_id="abc123",
        url="https://www.youtube.com/watch?v=abc123",
        title="First title",
        published_at="2026-07-02T00:00:00+00:00",
        status="known",
        metadata={"duration": 10},
    )
    same_item_id = db.upsert_subscription_item(
        subscription_id=subscription_id,
        provider_item_id="abc123",
        url="https://www.youtube.com/watch?v=abc123",
        title="Updated title",
        published_at="2026-07-02T00:00:00+00:00",
        status="queued",
        metadata={"duration": 11},
    )

    assert same_item_id == item_id
    done_item_id = db.upsert_subscription_item(
        subscription_id=subscription_id,
        provider_item_id="done123",
        url="https://www.youtube.com/watch?v=done123",
        title="Done title",
        status="done",
    )
    db.update_subscription_item(done_item_id, total_bytes=4096, progress_bytes=4096)
    downloading_item_id = db.upsert_subscription_item(
        subscription_id=subscription_id,
        provider_item_id="downloading123",
        url="https://www.youtube.com/watch?v=downloading123",
        title="Downloading title",
        status="downloading",
    )
    db.update_subscription_item(downloading_item_id, progress_bytes=512)
    items = db.list_subscription_items(subscription_id)
    assert len(items) == 3
    updated_item = next(item for item in items if item["provider_item_id"] == "abc123")
    assert updated_item["status"] == "known"
    assert updated_item["title"] == "Updated title"
    assert json.loads(updated_item["metadata_json"]) == {"duration": 11}

    payload = subscriptions.get_subscription_payload(subscription_id)
    assert payload is not None
    assert payload["title"] == "Example Channel"
    assert payload["metadata"] == {"channel": "Example Channel"}
    assert payload["item_counts"]["known"] == 1
    assert payload["item_counts"]["queued"] == 0
    assert payload["item_counts"]["done"] == 1
    assert payload["item_counts"]["downloading"] == 1
    assert payload["storage_bytes"] == 4608
    assert payload["storage_human"] == "4.5 KB"


def test_subscription_api_returns_disabled_empty_state(app_modules: tuple) -> None:
    _db, _subscriptions, main, _config_root = app_modules

    response = main.api_subscriptions()
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["ok"] is True
    assert payload["subscriptions"] == []
    assert payload["scheduler"]["check_scheduler_enabled"] is True
    assert payload["scheduler"]["check_scheduler_running"] is False
    assert payload["scheduler"]["download_scheduler_enabled"] is True
    assert payload["scheduler"]["download_scheduler_running"] is False
    assert payload["scheduler"]["phase"] == "scheduled_downloads"
    assert payload["settings"]["default_check_interval_seconds"] == 21600


def test_subscription_api_returns_detail_and_items(app_modules: tuple) -> None:
    db, _subscriptions, main, _config_root = app_modules
    subscription_id = db.create_subscription(
        kind="playlist",
        source_url="https://www.youtube.com/playlist?list=PL123",
        canonical_id="PL123",
        title="Playlist",
    )
    db.upsert_subscription_item(
        subscription_id=subscription_id,
        provider_item_id="abc123",
        url="https://www.youtube.com/watch?v=abc123",
        title="Video",
    )

    detail = json.loads(main.api_subscription(subscription_id).body.decode("utf-8"))
    items = json.loads(main.api_subscription_items(subscription_id).body.decode("utf-8"))

    assert detail["subscription"]["canonical_id"] == "PL123"
    assert detail["subscription"]["item_counts"]["known"] == 1
    assert items["subscription"]["id"] == subscription_id
    assert items["items"][0]["provider_item_id"] == "abc123"

    with pytest.raises(HTTPException) as exc_info:
        main.api_subscription(subscription_id + 1)
    assert exc_info.value.status_code == 404


def test_subscription_aggregate_items_default_active_counts_and_metadata(app_modules: tuple) -> None:
    db, _subscriptions, main, _config_root = app_modules
    subscription_id, item_ids = seed_subscription_items(db)
    other_subscription_id = db.create_subscription(
        kind="playlist",
        source_url="https://www.youtube.com/playlist?list=PL-other",
        canonical_id="PL-other",
        title="Other Playlist",
        enabled=False,
    )
    db.upsert_subscription_item(
        subscription_id=other_subscription_id,
        provider_item_id="other-failed",
        url="https://www.youtube.com/watch?v=other",
        title="Other failed",
        status="failed",
    )
    db.update_subscription_item(item_ids["downloading"], progress_bytes=512, total_bytes=1024)

    response = TestClient(main.app).get(
        "/api/subscriptions/items",
        params={"subscription_id": subscription_id},
        auth=("admin", "test-password-that-is-long"),
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["ok"] is True
    assert {item["status"] for item in payload["items"]} == {"eligible", "queued", "downloading", "failed"}
    assert all(item["subscription_id"] == subscription_id for item in payload["items"])
    assert all(item["subscription_title"] == "Aggregate Channel" for item in payload["items"])
    assert all(item["subscription_kind"] == "channel" for item in payload["items"])
    assert all(item["subscription_enabled"] is True for item in payload["items"])
    assert payload["counts"] == {status: 1 for status in AGGREGATE_ITEM_STATUSES}
    assert payload["scheduler"]["check_scheduler_running"] is False
    downloading = next(item for item in payload["items"] if item["status"] == "downloading")
    assert downloading["progress_human"] == "512.0 B"
    assert downloading["total_human"] == "1.0 KB"
    assert downloading["percent"] == 50.0


def test_subscription_aggregate_items_all_filter_and_cursor(app_modules: tuple) -> None:
    db, _subscriptions, main, _config_root = app_modules
    subscription_id, _item_ids = seed_subscription_items(db)

    all_payload = json.loads(
        main.api_subscription_item_summaries(status="all", subscription_id=subscription_id, limit=500).body.decode(
            "utf-8"
        )
    )
    first_page = json.loads(
        main.api_subscription_item_summaries(status="all", subscription_id=subscription_id, limit=3).body.decode(
            "utf-8"
        )
    )
    second_page = json.loads(
        main.api_subscription_item_summaries(
            status="all",
            subscription_id=subscription_id,
            limit=3,
            cursor=first_page["next_cursor"],
        ).body.decode("utf-8")
    )

    assert {item["status"] for item in all_payload["items"]} == set(AGGREGATE_ITEM_STATUSES)
    assert len(first_page["items"]) == 3
    assert first_page["next_cursor"] == first_page["items"][-1]["id"]
    assert {item["id"] for item in first_page["items"]}.isdisjoint({item["id"] for item in second_page["items"]})
    assert all(item["id"] < first_page["next_cursor"] for item in second_page["items"])


def test_subscription_aggregate_items_rejects_invalid_filter_and_missing_subscription(app_modules: tuple) -> None:
    _db, _subscriptions, main, _config_root = app_modules

    with pytest.raises(HTTPException) as invalid:
        main.api_subscription_item_summaries(status="bogus")
    assert invalid.value.status_code == 400

    with pytest.raises(HTTPException) as missing:
        main.api_subscription_item_summaries(subscription_id=999)
    assert missing.value.status_code == 404


def test_subscription_api_create_update_and_delete(app_modules: tuple) -> None:
    _db, _subscriptions, main, _config_root = app_modules

    create_response = asyncio.run(
        main.api_create_subscription(
            JsonRequest(
                {
                    "url": "https://www.youtube.com/playlist?list=PLabc123",
                    "initial_policy": "latest_n",
                    "initial_limit": 3,
                    "check_interval_seconds": 7200,
                    "auto_queue": False,
                }
            )
        )
    )
    created = json.loads(create_response.body.decode("utf-8"))["subscription"]
    assert created["kind"] == "playlist"
    assert created["canonical_id"] == "PLabc123"
    assert created["initial_policy"] == "latest_n"
    assert created["initial_limit"] == 3
    assert created["check_interval_seconds"] == 7200
    assert created["auto_queue"] is False

    with pytest.raises(HTTPException) as duplicate:
        asyncio.run(
            main.api_create_subscription(
                JsonRequest({"url": "https://www.youtube.com/watch?v=abc&list=PLabc123"})
            )
        )
    assert duplicate.value.status_code == 409

    update_response = asyncio.run(
        main.api_update_subscription(
            created["id"],
            JsonRequest({"enabled": False, "auto_queue": True, "check_interval_seconds": 3600, "title": "Saved"}),
        )
    )
    updated = json.loads(update_response.body.decode("utf-8"))["subscription"]
    assert updated["enabled"] is False
    assert updated["auto_queue"] is True
    assert updated["check_status"] == "paused"
    assert updated["check_interval_seconds"] == 3600
    assert updated["title"] == "Saved"

    delete_response = main.api_delete_subscription(created["id"])
    assert json.loads(delete_response.body.decode("utf-8")) == {"ok": True, "deleted": True}
    with pytest.raises(HTTPException) as missing:
        main.api_subscription(created["id"])
    assert missing.value.status_code == 404


def test_subscription_manual_check_discovers_items_without_jobs(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _subscriptions, main, _config_root = app_modules
    create_response = asyncio.run(
        main.api_create_subscription(
            JsonRequest(
                {
                    "url": "https://www.youtube.com/@example",
                    "initial_policy": "latest_n",
                    "initial_limit": 1,
                }
            )
        )
    )
    subscription = json.loads(create_response.body.decode("utf-8"))["subscription"]

    monkeypatch.setattr(
        main.subscriptions,
        "yt_dlp_subscription_info",
        lambda _url: {
            "entries": [
                {
                    "id": "old123",
                    "title": "Old",
                    "webpage_url": "https://www.youtube.com/watch?v=old123",
                    "upload_date": "20260701",
                },
                {
                    "id": "new123",
                    "title": "New",
                    "webpage_url": "https://www.youtube.com/watch?v=new123",
                    "upload_date": "20260702",
                },
            ]
        },
    )

    response = main.api_check_subscription_now(subscription["id"])
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["ok"] is True
    assert payload["result"]["entry_count"] == 2
    assert payload["result"]["new_count"] == 2
    assert payload["result"]["eligible_count"] == 1
    assert payload["subscription"]["first_check_completed"] is True
    assert payload["subscription"]["item_counts"]["eligible"] == 1
    assert payload["subscription"]["item_counts"]["known"] == 1
    assert [item["provider_item_id"] for item in payload["items"]] == ["new123", "old123"]
    assert [item["status"] for item in payload["items"]] == ["eligible", "known"]
    assert db.list_jobs() == []


def test_subscription_scheduler_runs_due_checks_without_jobs(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, subscriptions, main, _config_root = app_modules
    subscription_id = subscriptions.create_subscription(
        {
            "url": "https://www.youtube.com/playlist?list=PLscheduler",
            "initial_policy": "full_backfill",
            "check_interval_seconds": 3600,
        }
    )
    monkeypatch.setattr(subscriptions, "jittered_interval_seconds", lambda interval: interval)
    monkeypatch.setattr(
        subscriptions,
        "yt_dlp_subscription_info",
        lambda _url: {
            "entries": [
                {
                    "id": "sched123",
                    "title": "Scheduled",
                    "webpage_url": "https://www.youtube.com/watch?v=sched123",
                    "upload_date": "20260702",
                }
            ]
        },
    )

    assert db.list_due_subscriptions(subscriptions.utc_now())[-1]["id"] == subscription_id
    assert subscriptions.run_due_subscription_checks() == 1

    payload = json.loads(main.api_subscription(subscription_id).body.decode("utf-8"))["subscription"]
    items = json.loads(main.api_subscription_items(subscription_id).body.decode("utf-8"))["items"]
    assert payload["first_check_completed"] is True
    assert payload["check_status"] == "idle"
    assert payload["next_check_at"] is not None
    assert payload["last_success_at"] is not None
    assert items[0]["provider_item_id"] == "sched123"
    assert db.list_jobs() == []


def test_subscription_download_worker_downloads_ready_items_without_jobs(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, subscriptions, _main, _config_root = app_modules
    subscription_id = db.create_subscription(
        kind="playlist",
        source_url="https://www.youtube.com/playlist?list=PLdownloads",
        canonical_id="PLdownloads",
        title="Downloads",
    )
    item_id = db.upsert_subscription_item(
        subscription_id=subscription_id,
        provider_item_id="down123",
        url="https://www.youtube.com/watch?v=down123",
        title="Download me",
        status="eligible",
    )

    def fake_run(download_item_id: int, _command: list[str], target: Path) -> None:
        assert download_item_id == item_id
        target.mkdir(parents=True, exist_ok=True)
        (target / "Download me [down123].mp4").write_bytes(b"video")

    monkeypatch.setattr(subscriptions, "run_subscription_download_process", fake_run)

    assert subscriptions.run_ready_subscription_downloads() == 1

    item = db.get_subscription_item(item_id)
    assert item is not None
    assert item["status"] == "done"
    assert item["filename"] == "Download me [down123].mp4"
    assert item["target_dir"].endswith("gallery-dl/youtube.com/playlist/PLdownloads")
    assert "saved subscription item" in item["log"]
    assert db.list_jobs() == []


def test_subscription_download_worker_records_retry_backoff_on_failure(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, subscriptions, _main, _config_root = app_modules
    subscription_id = db.create_subscription(
        kind="channel",
        source_url="https://www.youtube.com/@example",
        canonical_id="@example",
        title="Example",
    )
    item_id = db.upsert_subscription_item(
        subscription_id=subscription_id,
        provider_item_id="fail123",
        url="https://www.youtube.com/watch?v=fail123",
        title="Fail me",
        status="eligible",
    )

    def fail_run(_download_item_id: int, _command: list[str], _target: Path) -> None:
        raise RuntimeError("token=secret-value failed")

    monkeypatch.setattr(subscriptions, "run_subscription_download_process", fail_run)

    assert subscriptions.run_ready_subscription_downloads() == 1

    item = db.get_subscription_item(item_id)
    assert item is not None
    assert item["status"] == "queued"
    assert item["attempt_count"] == 1
    assert item["next_attempt_at"] is not None
    assert "secret-value" not in item["error"]
    assert "secret-value" not in item["log"]


def test_subscription_item_action_apis_queue_skip_and_retry(app_modules: tuple) -> None:
    db, _subscriptions, main, _config_root = app_modules
    subscription_id = db.create_subscription(
        kind="channel",
        source_url="https://www.youtube.com/@actions",
        canonical_id="@actions",
        title="Actions",
    )
    item_id = db.upsert_subscription_item(
        subscription_id=subscription_id,
        provider_item_id="action123",
        url="https://www.youtube.com/watch?v=action123",
        title="Action",
        status="known",
    )

    queued = json.loads(main.api_queue_subscription_item(item_id).body.decode("utf-8"))
    assert queued["item"]["status"] == "queued"
    assert queued["subscription"]["item_counts"]["queued"] == 1

    skipped = json.loads(main.api_skip_subscription_item(item_id).body.decode("utf-8"))
    assert skipped["item"]["status"] == "skipped"
    assert skipped["item"]["policy_reason"] == "manual"

    failed_id = db.upsert_subscription_item(
        subscription_id=subscription_id,
        provider_item_id="failed123",
        url="https://www.youtube.com/watch?v=failed123",
        title="Failed",
        status="failed",
    )
    db.update_subscription_item(failed_id, attempt_count=3, error="boom")
    retried = json.loads(main.api_retry_subscription_item(failed_id).body.decode("utf-8"))
    assert retried["item"]["status"] == "queued"
    assert retried["item"]["attempt_count"] == 0
    assert retried["item"]["error"] is None

    with pytest.raises(HTTPException) as missing:
        main.api_queue_subscription_item(failed_id + 999)
    assert missing.value.status_code == 404


def test_subscription_manual_check_records_backoff_on_failure(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db, _subscriptions, main, _config_root = app_modules
    create_response = asyncio.run(
        main.api_create_subscription(JsonRequest({"url": "https://www.youtube.com/@example"}))
    )
    subscription = json.loads(create_response.body.decode("utf-8"))["subscription"]

    def fail_discovery(_url: str) -> dict:
        raise RuntimeError("token=secret-value failed")

    monkeypatch.setattr(main.subscriptions, "yt_dlp_subscription_info", fail_discovery)

    with pytest.raises(HTTPException) as exc_info:
        main.api_check_subscription_now(subscription["id"])
    assert exc_info.value.status_code == 502
    assert "secret-value" not in str(exc_info.value.detail)

    failed = json.loads(main.api_subscription(subscription["id"]).body.decode("utf-8"))["subscription"]
    assert failed["check_status"] == "backoff"
    assert failed["failure_count"] == 1
    assert "secret-value" not in failed["last_error"]
