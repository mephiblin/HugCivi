from __future__ import annotations

import importlib
import json
import os
import zipfile
import zlib
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.defaults import (
    DOWNLOAD_STALL_TIMEOUT_DEFAULT_SECONDS,
    QUEUE_PROVIDER_COOLDOWN_MAX_DEFAULT_SECONDS,
    QUEUE_PROVIDER_COOLDOWN_MIN_DEFAULT_SECONDS,
    YT_DLP_DEFAULT_FORMAT,
)
from app.models import ParsedDownload


@pytest.fixture()
def app_modules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_root = tmp_path / "data"
    config_root = tmp_path / "config"
    archive_root = config_root / "downloads"
    data_root.mkdir()
    config_root.mkdir()

    monkeypatch.setenv("DATA_ROOT", str(data_root))
    monkeypatch.setenv("DB_PATH", str(config_root / "jobs.sqlite3"))
    monkeypatch.setenv("DOWNLOAD_ARCHIVE_DIR", str(archive_root))
    monkeypatch.setenv("MEDIA_CACHE_DIR", str(config_root / "media-cache"))
    monkeypatch.setenv("APP_PASSWORD", "test-password-that-is-long")

    import app.utils as utils
    import app.db as db
    import app.downloader as downloader
    import app.main as main

    importlib.reload(utils)
    importlib.reload(db)
    importlib.reload(downloader)
    importlib.reload(main)
    db.init_db()
    return utils, db, downloader, main, data_root, config_root


def test_settings_status_returns_credential_values_for_settings_form(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, db, _downloader, _main, _data_root, _config_root = app_modules
    monkeypatch.setenv("CIVITAI_TOKEN", "env-secret")
    db.set_setting("HF_TOKEN", "db-secret")
    db.set_setting("GALLERY_DL_USERNAME", "login-name")
    db.set_setting("GALLERY_DL_PASSWORD", "site-password")
    db.set_setting("GALLERY_DL_EXTRA_OPTIONS", "extractor.example.api-key=secret")
    db.set_setting("YT_DLP_COOKIES_FILE", "/config/yt-dlp/cookies.txt")
    db.set_setting("YT_DLP_PROXY", "socks5://user:secret@proxy.local:1080")
    db.set_setting("YT_DLP_EXTRA_OPTIONS", "cmdline-args=--cookies /tmp/private.txt")

    status = db.settings_status()

    assert status["HF_TOKEN"]["value"] == "db-secret"
    assert status["CIVITAI_TOKEN"]["value"] == "env-secret"
    assert status["GALLERY_DL_USERNAME"]["value"] == "login-name"
    assert status["GALLERY_DL_PASSWORD"]["value"] == "site-password"
    assert status["GALLERY_DL_EXTRA_OPTIONS"]["value"] == "extractor.example.api-key=secret"
    assert status["YT_DLP_COOKIES_FILE"]["value"] == "/config/yt-dlp/cookies.txt"
    assert status["YT_DLP_PROXY"]["value"] == "socks5://user:secret@proxy.local:1080"
    assert status["YT_DLP_EXTRA_OPTIONS"]["value"] == "cmdline-args=--cookies /tmp/private.txt"
    assert status["YT_DLP_COOKIES_FROM_BROWSER"]["value"] == ""
    assert status["HF_TOKEN"]["configured"] is True
    assert status["CIVITAI_TOKEN"]["configured"] is True
    assert status["HF_TOKEN"]["source"] == "ui"
    assert status["CIVITAI_TOKEN"]["source"] == "environment"
    assert status["youtube"]["YT_DLP_FORMAT"]["value"] == YT_DLP_DEFAULT_FORMAT
    assert status["queue"]["QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS"]["value"] == str(
        QUEUE_PROVIDER_COOLDOWN_MIN_DEFAULT_SECONDS
    )
    assert status["queue"]["QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS"]["value"] == str(
        QUEUE_PROVIDER_COOLDOWN_MAX_DEFAULT_SECONDS
    )
    assert status["queue"]["DOWNLOAD_STALL_TIMEOUT_SECONDS"]["value"] == str(DOWNLOAD_STALL_TIMEOUT_DEFAULT_SECONDS)
    assert status["startup"]["GALLERY_DL_AUTO_UPDATE"]["value"] == "1"


def test_startup_config_writer_persists_gallery_dl_update_toggle(app_modules: tuple) -> None:
    _utils, db, _downloader, main, _data_root, config_root = app_modules

    main.write_startup_config({"GALLERY_DL_AUTO_UPDATE": "0"})
    db.set_setting("GALLERY_DL_AUTO_UPDATE", "0")

    assert (config_root / "startup.env").read_text(encoding="utf-8") == "GALLERY_DL_AUTO_UPDATE=0\n"
    assert db.settings_status()["startup"]["GALLERY_DL_AUTO_UPDATE"]["value"] == "0"

    main.write_startup_config({"GALLERY_DL_AUTO_UPDATE": "on"})

    assert (config_root / "startup.env").read_text(encoding="utf-8") == "GALLERY_DL_AUTO_UPDATE=1\n"


def test_settings_post_updates_runtime_auth_values(app_modules: tuple) -> None:
    _utils, db, _downloader, main, _data_root, _config_root = app_modules
    client = TestClient(main.app)

    response = client.post(
        "/settings",
        data={
            "hf_token": "hf-runtime-token",
            "civitai_token": "civitai-runtime-token",
            "gallery_dl_password": "gallery-runtime-password",
            "yt_dlp_cookies_file": "/config/yt-dlp/cookies.txt",
            "yt_dlp_proxy": "socks5://192.168.200.100:1080",
            "yt_dlp_format": "best[ext=mp4]/best",
        },
        auth=("admin", "test-password-that-is-long"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db.get_secret("HF_TOKEN") == "hf-runtime-token"
    assert db.get_secret("CIVITAI_TOKEN") == "civitai-runtime-token"
    assert db.get_secret("GALLERY_DL_PASSWORD") == "gallery-runtime-password"
    assert db.get_secret("YT_DLP_COOKIES_FILE") == "/config/yt-dlp/cookies.txt"
    assert db.get_secret("YT_DLP_PROXY") == "socks5://192.168.200.100:1080"
    assert db.get_setting("YT_DLP_FORMAT") == "best[ext=mp4]/best"


def test_settings_post_can_clear_submitted_runtime_auth_values(app_modules: tuple) -> None:
    _utils, db, _downloader, main, _data_root, _config_root = app_modules
    db.set_setting("HF_TOKEN", "old-hf-token")
    db.set_setting("YT_DLP_PROXY", "socks5://old-proxy:1080")
    db.set_setting("GALLERY_DL_PASSWORD", "old-gallery-password")
    client = TestClient(main.app)

    response = client.post(
        "/settings",
        data={
            "hf_token": "",
            "yt_dlp_proxy": "",
            "yt_dlp_format": "best[ext=mp4]/best",
        },
        auth=("admin", "test-password-that-is-long"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db.get_setting("HF_TOKEN") is None
    assert db.get_setting("YT_DLP_PROXY") is None
    assert db.get_setting("GALLERY_DL_PASSWORD") == "old-gallery-password"


def test_safe_join_and_relative_path_preserve_internal_symlink_itself(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    link_parent = data_root / "a"
    target = data_root / "b"
    link_parent.mkdir()
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    link = link_parent / "link"
    link.symlink_to(target, target_is_directory=True)

    source = main.existing_data_path("a/link")

    assert source == link
    assert main.relative_data_path(source) == "a/link"
    source.unlink()
    assert not link.exists()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_active_job_protection_includes_jobs_without_target_dir(app_modules: tuple) -> None:
    _utils, db, _downloader, _main, data_root, _config_root = app_modules
    generic = ParsedDownload(
        source="generic",
        raw_input="https://example.com/model.bin",
        url="https://example.com/model.bin",
    )
    custom = ParsedDownload(
        source="generic",
        raw_input="https://example.com/other.bin",
        target_subdir="custom/folder",
        url="https://example.com/other.bin",
    )

    db.create_job(generic)
    db.create_job(custom)

    assert db.has_active_jobs_under(data_root / "generic") is True
    assert db.has_active_jobs_under(data_root / "custom") is True
    assert db.has_active_jobs_under(data_root / "custom" / "folder") is True
    assert db.has_active_jobs_under(data_root / "unrelated") is False


def test_prefix_clears_escape_like_wildcards(app_modules: tuple) -> None:
    _utils, db, _downloader, _main, _data_root, _config_root = app_modules
    db.set_favorite("foo_%/item", True)
    db.set_favorite("foo_X/item", True)
    db.set_item_note("foo_%/item", "delete me")
    db.set_item_note("foo_X/item", "keep me")

    db.clear_favorite_path_prefix("foo_%")
    db.clear_note_path_prefix("foo_%")

    assert "foo_%/item" not in db.favorite_paths()
    assert "foo_X/item" in db.favorite_paths()
    assert db.get_item_note("foo_%/item") == ""
    assert db.get_item_note("foo_X/item") == "keep me"


def test_zip_archive_failure_removes_temp_file(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, _db, _downloader, main, data_root, config_root = app_modules
    source = data_root / "folder"
    source.mkdir()
    (source / "file.txt").write_text("content", encoding="utf-8")

    def fail_zipfile(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("zip failed")

    monkeypatch.setattr(main.zipfile, "ZipFile", fail_zipfile)

    with pytest.raises(RuntimeError, match="zip failed"):
        main.create_zip_archive(source)

    assert list((config_root / "downloads").glob("*.zip")) == []


def test_zip_archive_excludes_partial_files(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    source = data_root / "folder"
    source.mkdir()
    (source / "file.txt").write_text("content", encoding="utf-8")
    (source / "file.txt.job-1-deadbeef.part").write_text("partial", encoding="utf-8")

    archive_path = main.create_zip_archive(source)

    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["file.txt"]


def test_zip_archive_uses_archive_semaphore(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    source = data_root / "folder"
    source.mkdir()
    (source / "file.txt").write_text("content", encoding="utf-8")
    entered = []

    class FakeSemaphore:
        def __enter__(self) -> None:
            entered.append(True)

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(main, "download_archive_semaphore", lambda: FakeSemaphore())

    main.create_zip_archive(source)

    assert entered == [True]


def test_archive_preflight_rejects_escaping_symlink(app_modules: tuple, tmp_path: Path) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    source = data_root / "folder"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (source / "outside-link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        main.preflight_archive_job(source)


def test_archive_preflight_rejects_unsafe_entry_name(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    source = data_root / "folder"
    source.mkdir()
    (source / "bad\\name.txt").write_text("content", encoding="utf-8")

    with pytest.raises(ValueError, match="entry"):
        main.preflight_archive_job(source)


def test_archive_zip_internal_job_creates_download_artifact(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    source = data_root / "folder"
    source.mkdir()
    (source / "file.txt").write_text("content", encoding="utf-8")
    (source / "file.txt.job-1-deadbeef.part").write_text("partial", encoding="utf-8")
    main.register_internal_job_handlers()
    job_id = db.create_internal_job(
        main.INTERNAL_JOB_ARCHIVE_ZIP,
        input_text="zip:folder",
        payload={"path": "folder"},
        target_dir=source,
        filename="folder.zip",
    )

    main.internal_jobs.run_job(job_id)

    job = db.get_job(job_id)
    assert job is not None
    assert job["status"] == "done"
    assert job["artifact_url"] == f"/api/fs/download-jobs/{job_id}/file"
    archive_path = Path(str(job["artifact_path"]))
    assert archive_path.exists()
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["file.txt"]


def test_uncached_video_payload_requires_async_media_jobs(app_modules: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    video = data_root / "clip.mkv"
    video.write_bytes(b"video")
    monkeypatch.setattr(main, "is_browser_mp4_video", lambda _source: False)

    payload = main.media_item_payload(video, 0)
    play_response = main.api_media_play("clip.mkv", "_")
    poster_response = main.api_media_poster("clip.mkv", "_")

    assert payload["url"] == ""
    assert payload["play_job_required"] is True
    assert payload["poster_job_required"] is True
    assert play_response.status_code == 202
    assert poster_response.status_code == 202


def test_media_transcode_internal_job_records_artifact(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, db, _downloader, main, data_root, config_root = app_modules
    video = data_root / "clip.mkv"
    video.write_bytes(b"video")
    artifact = config_root / "media-cache" / "clip.play.mp4"
    artifact.parent.mkdir(parents=True, exist_ok=True)

    def fake_transcode(_source: Path, *, job_id: int | None = None) -> Path:
        artifact.write_bytes(b"mp4")
        if job_id is not None:
            db.update_job(job_id, progress_bytes=5, total_bytes=5)
        return artifact

    monkeypatch.setattr(main, "is_browser_mp4_video", lambda _source: False)
    monkeypatch.setattr(main, "transcode_video_for_browser", fake_transcode)
    main.register_internal_job_handlers()
    job_id = db.create_internal_job(main.INTERNAL_JOB_MEDIA_TRANSCODE, input_text="transcode:clip.mkv", payload={"path": "clip.mkv"})

    main.internal_jobs.run_job(job_id)

    job = db.get_job(job_id)
    assert job is not None
    assert job["status"] == "done"
    assert job["artifact_path"] == str(artifact)
    assert job["artifact_url"] == "/api/media/play?path=clip.mkv"


def test_media_poster_internal_job_records_artifact(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, db, _downloader, main, data_root, config_root = app_modules
    video = data_root / "clip.mkv"
    video.write_bytes(b"video")
    poster = config_root / "media-cache" / "clip.jpg"
    poster.parent.mkdir(parents=True, exist_ok=True)

    def fake_poster(_source: Path, *, job_id: int | None = None) -> Path:
        poster.write_bytes(b"jpg")
        if job_id is not None:
            db.update_job(job_id, progress_bytes=1, total_bytes=1)
        return poster

    monkeypatch.setattr(main, "video_poster_path", fake_poster)
    main.register_internal_job_handlers()
    job_id = db.create_internal_job(main.INTERNAL_JOB_MEDIA_POSTER, input_text="poster:clip.mkv", payload={"path": "clip.mkv"})

    main.internal_jobs.run_job(job_id)

    job = db.get_job(job_id)
    assert job is not None
    assert job["status"] == "done"
    assert job["artifact_path"] == str(poster)
    assert job["artifact_url"] == "/api/media/poster?path=clip.mkv"


def test_startup_cleanup_removes_stale_archives_and_media_cache(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, _db, _downloader, main, _data_root, config_root = app_modules
    archive_root = config_root / "downloads"
    media_cache = config_root / "media-cache"
    archive_root.mkdir(parents=True, exist_ok=True)
    media_cache.mkdir(parents=True, exist_ok=True)

    old_zip = archive_root / "old.zip"
    fresh_zip = archive_root / "fresh.zip"
    old_media = media_cache / "old.play.mp4"
    fresh_media = media_cache / "fresh.play.mp4"
    temp_media = media_cache / ".stale.tmp.mp4"
    for path in (old_zip, fresh_zip, old_media, fresh_media, temp_media):
        path.write_bytes(b"x")
    os.utime(old_zip, (100, 100))
    os.utime(old_media, (100, 100))
    os.utime(temp_media, (100, 100))
    os.utime(fresh_zip, (990, 990))
    os.utime(fresh_media, (990, 990))
    monkeypatch.setenv("DOWNLOAD_ARCHIVE_TTL_SECONDS", "500")
    monkeypatch.setenv("MEDIA_CACHE_TTL_SECONDS", "500")

    assert main.cleanup_stale_download_archives(now=1000) == 1
    assert main.cleanup_stale_media_cache(now=1000) == 2

    assert not old_zip.exists()
    assert fresh_zip.exists()
    assert not old_media.exists()
    assert fresh_media.exists()
    assert not temp_media.exists()


def test_lifespan_runs_startup_tasks_and_stops_workers(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    calls: list[str] = []

    monkeypatch.setattr(main.db, "init_db", lambda: calls.append("init_db"))
    monkeypatch.setattr(main, "ensure_route_folders", lambda: calls.append("ensure_route_folders"))
    monkeypatch.setattr(main, "cleanup_stale_download_archives", lambda: calls.append("cleanup_archives"))
    monkeypatch.setattr(main, "cleanup_stale_media_cache", lambda: calls.append("cleanup_media"))
    monkeypatch.setattr(main, "start_workers", lambda: calls.append("start_workers"))
    monkeypatch.setattr(main.internal_jobs, "start_workers", lambda: calls.append("start_internal_workers"))
    monkeypatch.setattr(main.subscriptions, "start_workers", lambda: calls.append("start_subscription_workers"))
    monkeypatch.setattr(main.internal_jobs, "stop_workers", lambda: calls.append("stop_internal_workers") or True)
    monkeypatch.setattr(main.subscriptions, "stop_workers", lambda: calls.append("stop_subscription_workers") or True)
    monkeypatch.setattr(main, "start_library_indexer", lambda: calls.append("start_library_indexer"))
    monkeypatch.setattr(main, "stop_library_indexer", lambda: calls.append("stop_library_indexer") or True)
    monkeypatch.setattr(main, "stop_workers", lambda: calls.append("stop_workers") or True)

    with TestClient(main.app):
        assert calls == [
            "init_db",
            "ensure_route_folders",
            "cleanup_archives",
            "cleanup_media",
            "start_workers",
            "start_internal_workers",
            "start_subscription_workers",
            "start_library_indexer",
        ]

    assert calls[-4:] == [
        "stop_library_indexer",
        "stop_internal_workers",
        "stop_subscription_workers",
        "stop_workers",
    ]


def test_job_list_payload_omits_log_but_detail_and_log_endpoint_keep_it(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, db, _downloader, main, _data_root, _config_root = app_modules
    monkeypatch.setenv("JOB_LOG_MAX_CHARS", "120")
    parsed = ParsedDownload(source="generic", raw_input="https://example.com/model.bin", url="https://example.com/model.bin")
    job_id = db.create_job(parsed)

    db.append_log(job_id, "x" * 240)

    job = db.get_job(job_id)
    assert job is not None
    assert len(job["log"]) <= 120
    assert "x" * 20 in job["log"]

    summary = main.decorate_jobs(db.list_jobs())[0]
    assert "log" not in summary
    assert "metadata_json" not in summary

    detail_payload = json.loads(main.api_job(job_id, "_").body.decode("utf-8"))
    assert "log" in detail_payload
    assert "metadata_json" in detail_payload
    assert main.job_log(job_id, "_").body.decode("utf-8") == detail_payload["log"]


def test_job_summary_query_omits_heavy_fields_and_supports_cursor(app_modules: tuple) -> None:
    _utils, db, _downloader, main, _data_root, _config_root = app_modules
    ids = []
    for index in range(3):
        parsed = ParsedDownload(source="generic", raw_input=f"https://example.com/{index}.bin", url=f"https://example.com/{index}.bin")
        job_id = db.create_job(parsed)
        db.update_job(job_id, metadata_json=json.dumps({"large": "x" * 100}), log="x" * 100)
        ids.append(job_id)

    first_page = db.list_job_summaries(limit=2)
    response = main.api_jobs(limit=2, cursor=ids[-1], _="_")
    payload = json.loads(response.body.decode("utf-8"))

    assert len(first_page) == 2
    assert "log" not in first_page[0]
    assert "metadata_json" not in first_page[0]
    assert payload["ok"] is True
    assert [job["id"] for job in payload["jobs"]] == [ids[1], ids[0]]
    assert payload["next_cursor"] == ids[0]


def test_database_backup_uses_sqlite_backup_api(app_modules: tuple) -> None:
    _utils, db, _downloader, _main, _data_root, config_root = app_modules
    parsed = ParsedDownload(source="generic", raw_input="https://example.com/model.bin", url="https://example.com/model.bin")
    db.create_job(parsed)
    backup_path = config_root / "backups" / "unit.sqlite3"

    run_id = db.create_maintenance_run("db_backup")
    db.backup_database(backup_path)
    db.finish_maintenance_run(run_id, "done", {"path": str(backup_path)})

    assert backup_path.exists()
    assert backup_path.stat().st_size > 0


def test_internal_job_rows_are_separate_from_download_resume_list(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    parsed = ParsedDownload(source="generic", raw_input="https://example.com/model.bin", url="https://example.com/model.bin")
    download_id = db.create_job(parsed)
    internal_id = db.create_internal_job(
        "archive_zip",
        input_text="prepare zip: folder",
        payload={"path": "folder"},
        target_dir=data_root / "folder",
        filename="folder.zip",
        total_bytes=123,
    )

    download_job = db.get_job(download_id)
    internal_job = db.get_job(internal_id)

    assert download_job is not None
    assert internal_job is not None
    assert db.is_download_job(download_job)
    assert db.is_internal_job(internal_job)
    assert internal_job["job_kind"] == "archive_zip"
    assert internal_job["source"] == "internal"
    assert internal_job["filename"] == "folder.zip"
    assert {job["id"] for job in db.list_download_jobs_to_resume()} == {download_id}
    assert {job["id"] for job in db.list_internal_jobs_to_resume()} == {internal_id}

    decorated = main.decorate_job(internal_job)
    assert decorated["job_kind"] == "archive_zip"
    assert decorated["source"] == "archive_zip"


def test_internal_job_actions_use_internal_queue(app_modules: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    _utils, db, _downloader, main, _data_root, _config_root = app_modules
    job_id = db.create_internal_job("archive_zip", input_text="prepare zip", payload={"path": "folder"})
    enqueued: list[int] = []
    download_enqueued: list[int] = []

    monkeypatch.setattr(main.internal_jobs, "enqueue_job", lambda queued_id: enqueued.append(queued_id))
    monkeypatch.setattr(main, "enqueue_job", lambda queued_id: download_enqueued.append(queued_id))

    db.update_job(job_id, status="failed", error="boom")
    main.api_retry_job(job_id, "tester")

    assert enqueued == [job_id]
    assert download_enqueued == []


def test_clear_history_removes_failed_partial_files_before_deleting_rows(app_modules: tuple) -> None:
    _utils, db, downloader, main, data_root, _config_root = app_modules
    target = data_root / "generic"
    target.mkdir()
    final_path = target / "model.bin"
    parsed = ParsedDownload(source="generic", raw_input="https://example.com/model.bin", url="https://example.com/model.bin")
    job_id = db.create_job(parsed)
    db.update_job(job_id, status="failed", target_dir=str(target))
    part_path = downloader.partial_download_path(final_path, job_id, "https://example.com/model.bin")
    part_path.write_bytes(b"partial")
    downloader.register_job_partial_path(job_id, part_path, final_path, "https://example.com/model.bin")

    payload = json.loads(main.api_clear_jobs("_").body.decode("utf-8"))

    assert payload["deleted"] == 1
    assert not part_path.exists()
    assert db.get_job(job_id) is None


def test_clear_history_can_vacuum_when_enabled(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, db, _downloader, main, _data_root, _config_root = app_modules
    parsed = ParsedDownload(source="generic", raw_input="https://example.com/model.bin", url="https://example.com/model.bin")
    job_id = db.create_job(parsed)
    db.update_job(job_id, status="done")
    called = []
    monkeypatch.setenv("SQLITE_VACUUM_AFTER_CLEAR", "1")
    monkeypatch.setattr(db, "vacuum_database", lambda: called.append(True))

    payload = json.loads(main.api_clear_jobs("_").body.decode("utf-8"))

    assert payload["deleted"] == 1
    assert payload["vacuumed"] is True
    assert called == [True]


def test_clear_history_resets_stale_library_index_so_existing_model_cards_survive(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    indexed = data_root / "gallery-dl" / "example"
    indexed.mkdir(parents=True)
    (indexed / "page.jpg").write_bytes(b"image")
    main.scan_library_index_batch(max_paths=100, reset=True)

    target = data_root / "stable-diffusion" / "loras" / "zimagebase" / "hands" / "version_3012596"
    target.mkdir(parents=True)
    (target / "Hands_zib_v1.safetensors").write_bytes(b"model")
    (target / "_civitai_metadata.json").write_text(
        json.dumps(
            {
                "source": "civitai",
                "raw_input": "https://civitai.com/models/200255",
                "model_id": "200255",
                "version_id": "3012596",
                "archive_info": {"model_title": "Hands XL", "model_category": "LoRA"},
            }
        ),
        encoding="utf-8",
    )
    parsed = ParsedDownload(
        source="civitai",
        raw_input="https://civitai.com/models/200255",
        civitai_model_id="200255",
        civitai_version_id="3012596",
    )
    job_id = db.create_job(parsed)
    db.update_job(job_id, status="done", target_dir=str(target), model_title="DB Row")

    assert [
        row
        for row in main.library_items()
        if row.get("target_path") == "stable-diffusion/loras/zimagebase/hands/version_3012596"
    ] == []

    payload = json.loads(main.api_clear_jobs("_").body.decode("utf-8"))

    rows = [
        row
        for row in main.library_items()
        if row.get("target_path") == "stable-diffusion/loras/zimagebase/hands/version_3012596"
    ]
    assert payload["deleted"] == 1
    assert payload["library_index_reset"] is True
    assert db.get_job(job_id) is None
    assert len(rows) == 1
    assert rows[0]["model_title"] == "Hands XL"
    assert rows[0]["source"] == "civitai"
    assert rows[0]["has_media"] is False


def test_library_live_path_finds_child_model_cards_when_index_is_stale(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    indexed = data_root / "gallery-dl" / "example"
    indexed.mkdir(parents=True)
    (indexed / "page.jpg").write_bytes(b"image")
    main.scan_library_index_batch(max_paths=100, reset=True)

    parent = data_root / "stable-diffusion" / "loras" / "zimagebase" / "hands-xl-_-sd-1.5-_-f1d-_-pony"
    target = parent / "version_3012596"
    target.mkdir(parents=True)
    (target / "Hands_zib_v1.safetensors").write_bytes(b"model")
    (target / "civitai_example_134865393.jpg").write_bytes(b"image")
    (target / "_civitai_metadata.json").write_text(
        json.dumps(
            {
                "source": "civitai",
                "raw_input": "https://civitai.com/models/200255",
                "model_id": "200255",
                "version_id": "3012596",
                "archive_info": {"model_title": "Hands XL", "model_category": "LoRA"},
            }
        ),
        encoding="utf-8",
    )

    assert [
        row
        for row in main.library_items()
        if row.get("target_path") == "stable-diffusion/loras/zimagebase/hands-xl-_-sd-1.5-_-f1d-_-pony/version_3012596"
    ] == []

    payload = json.loads(
        main.api_library(
            mode="live",
            path="stable-diffusion/loras/zimagebase/hands-xl-_-sd-1.5-_-f1d-_-pony",
            _="_",
        ).body.decode("utf-8")
    )

    rows = [
        row
        for row in payload
        if row.get("target_path") == "stable-diffusion/loras/zimagebase/hands-xl-_-sd-1.5-_-f1d-_-pony/version_3012596"
    ]
    assert len(rows) == 1
    assert rows[0]["model_title"] == "Hands XL"
    assert rows[0]["has_media"] is True
    assert rows[0]["thumbnail_url"].endswith("civitai_example_134865393.jpg")


def test_library_item_uses_scan_budgets_for_large_folders(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    folder = data_root / "media"
    folder.mkdir()
    for index in range(5):
        (folder / f"{index}.mp4").write_bytes(b"x" * 10)
    monkeypatch.setenv("MEDIA_FILE_SCAN_MAX_FILES", "2")
    monkeypatch.setenv("LIBRARY_ITEM_SIZE_SCAN_MAX_FILES", "2")

    item = main.library_item_for_path(folder, set())

    assert item["media_count"] <= 2
    assert item["progress_bytes"] <= 20
    assert main.path_size(folder) == 50


def test_partial_download_path_is_job_and_url_scoped(app_modules: tuple) -> None:
    _utils, _db, downloader, _main, data_root, _config_root = app_modules
    final_path = data_root / "models" / "same-name.bin"

    first = downloader.partial_download_path(final_path, 1, "https://example.com/a")
    second = downloader.partial_download_path(final_path, 2, "https://example.com/a")
    third = downloader.partial_download_path(final_path, 1, "https://example.com/b")

    assert first != second
    assert first != third
    assert ".job-1-" in first.name
    assert first.name.endswith(".part")


def test_library_items_restore_filesystem_card_after_job_row_deleted(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "stable-diffusion" / "loras" / "sdxl" / "disk-card" / "version_1"
    target.mkdir(parents=True)
    (target / "model.safetensors").write_text("model", encoding="utf-8")
    (target / "_civitai_metadata.json").write_text(
        json.dumps(
            {
                "source": "civitai",
                "raw_input": "https://civitai.com/models/123",
                "model_id": "123",
                "version_id": "456",
                "archive_info": {
                    "model_title": "Disk Card",
                    "model_category": "LoRA",
                    "base_model": "SDXL",
                    "file_format": "SafeTensor",
                    "precision": "fp16",
                },
            }
        ),
        encoding="utf-8",
    )
    parsed = ParsedDownload(
        source="civitai",
        raw_input="https://civitai.com/models/123",
        civitai_model_id="123",
        civitai_version_id="456",
    )
    job_id = db.create_job(parsed)
    db.update_job(job_id, status="done", target_dir=str(target), model_title="DB Row")

    rows = [
        row
        for row in main.decorate_jobs(db.list_jobs())
        if row.get("target_path") == "stable-diffusion/loras/sdxl/disk-card/version_1"
    ]

    assert len(rows) == 1
    assert rows[0]["model_title"] == "DB Row"

    db.delete_job(job_id)
    assert main.decorate_jobs(db.list_jobs()) == []

    rows = [
        row
        for row in main.library_items()
        if row.get("target_path") == "stable-diffusion/loras/sdxl/disk-card/version_1"
    ]

    assert len(rows) == 1
    assert rows[0]["model_title"] == "Disk Card"
    assert rows[0]["source"] == "civitai"
    assert rows[0]["status"] == "done"
    assert rows[0]["source_url"] == "https://civitai.com/models/123"
    assert rows[0]["favorite"] is False


def test_library_index_scan_populates_db_backed_library_items(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "stable-diffusion" / "loras" / "indexed-card"
    target.mkdir(parents=True)
    (target / "model.safetensors").write_text("model", encoding="utf-8")
    (target / "_civitai_metadata.json").write_text(
        json.dumps(
            {
                "source": "civitai",
                "raw_input": "https://civitai.com/models/999",
                "archive_info": {"model_title": "Indexed Card", "model_category": "LoRA"},
            }
        ),
        encoding="utf-8",
    )

    result = main.scan_library_index_batch(max_paths=100, reset=True)
    db.set_favorite("stable-diffusion/loras/indexed-card", True)
    rows = [row for row in main.library_items() if row.get("target_path") == "stable-diffusion/loras/indexed-card"]

    assert result["indexed"] >= 1
    assert db.count_library_index_items() >= 1
    assert len(rows) == 1
    assert rows[0]["model_title"] == "Indexed Card"
    assert rows[0]["favorite"] is True

    db.clear_library_item_prefix("stable-diffusion/loras/indexed-card")
    assert [
        row for row in db.list_library_index_items() if row.get("target_path") == "stable-diffusion/loras/indexed-card"
    ] == []


def test_retry_failed_job_requeues_existing_job(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, db, _downloader, main, _data_root, _config_root = app_modules
    parsed = ParsedDownload(
        source="civitai",
        raw_input="https://civitai.com/models/123?modelVersionId=456",
        civitai_model_id="123",
        civitai_version_id="456",
    )
    job_id = db.create_job(parsed)
    db.update_job(job_id, status="failed", error="temporary failure", progress_bytes=123, total_bytes=456)
    enqueued: list[int] = []
    monkeypatch.setattr(main, "enqueue_job", lambda queued_id: enqueued.append(queued_id))

    main.api_retry_job(job_id, "tester")

    job = db.get_job(job_id)
    assert job is not None
    assert job["status"] == "queued"
    assert job["error"] is None
    assert job["progress_bytes"] == 0
    assert job["total_bytes"] is None
    assert enqueued == [job_id]


def test_pwa_manifest_and_service_worker_are_declared(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    manifest_path = main.BASE_DIR / "static" / "manifest.webmanifest"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["name"] == "hugcivi"
    assert manifest["display"] == "standalone"
    assert manifest["start_url"].startswith("/")
    assert any(icon["sizes"] == "192x192" for icon in manifest["icons"])
    assert any(icon["purpose"] == "maskable" for icon in manifest["icons"])

    template = (main.BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    assert '<link rel="manifest" href="/manifest.webmanifest">' in template
    assert '<link rel="icon" type="image/png" href="/static/icons/hugcivi-192.png">' in template
    assert "navigator.serviceWorker.register('/sw.js')" in template
    for filename in ("hugcivi-180.png", "hugcivi-192.png", "hugcivi-512.png", "hugcivi-maskable-512.png"):
        assert png_top_left_alpha(main.BASE_DIR / "static" / "icons" / filename) == 0

    manifest_response = main.web_manifest()
    assert manifest_response.media_type == "application/manifest+json"

    service_worker_response = main.service_worker()
    assert service_worker_response.media_type == "application/javascript"
    assert service_worker_response.headers["service-worker-allowed"] == "/"
    assert service_worker_response.headers["cache-control"] == "no-cache"


def test_home_template_declares_subscription_sidebar_ui(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    template = (main.BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    stylesheet = (main.BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")

    assert 'data-sidebar-tab="subscriptions"' in template
    assert 'id="jobs-section"' in template
    assert 'id="subscription-work-section"' in template
    assert 'data-subscription-work-filter="active"' in template
    assert 'data-subscription-work-filter="downloading"' in template
    assert 'id="subscription-modal"' in template
    assert 'name="initial_policy" value="from_now"' in template
    assert 'name="initial_policy" value="full_backfill"' in template
    assert "fetch('/api/subscriptions'" in template
    assert "`/api/subscriptions/items?${params.toString()}`" in template
    assert "`/api/subscriptions/${encodeURIComponent(subscriptionId)}/check`" in template
    assert 'data-subscription-item-action="${escapeHtml(action)}"' in template
    assert 'data-subscription-work-action="${escapeHtml(action)}"' in template
    assert "let activeWorkMode = 'jobs';" in template
    assert "activeWorkMode === 'subscriptions'" in template
    assert "`/api/subscriptions/items/${encodeURIComponent(itemId)}/${action}`" in template
    assert ".subscription-list" in stylesheet
    assert ".subscription-items" in stylesheet
    assert ".subscription-modal-panel" in stylesheet
    assert ".subscription-work-section" in stylesheet
    assert ".subscription-work-table" in stylesheet
    assert ".subscription-work-mobile-card" in stylesheet


def test_home_template_declares_ytdlp_proxy_setting(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    template = (main.BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'id="yt_dlp_proxy"' in template
    assert 'name="yt_dlp_proxy"' in template
    assert "YouTube/yt-dlp Proxy" in template
    assert 'value="{{ settings.YT_DLP_PROXY.value }}"' in template


def test_media_archive_card_click_stays_in_viewer_flow(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    template = (main.BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'data-media-archive="${mediaArchive ? \'true\' : \'false\'}"' in template
    assert "openMediaViewerForCard(card);" in template
    assert "if (!isMediaArchiveCard(card) || event.target.closest('button, a, input, select, textarea')) return;" in template
    assert "event.stopPropagation();\n      openMediaViewerForCard(card);" in template


def test_home_template_renders_credentials_as_plain_text_values(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    template = (main.BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'value="{{ settings.HF_TOKEN.value }}"' in template
    assert 'value="{{ settings.CIVITAI_TOKEN.value }}"' in template
    assert 'id="gallery_dl_password" name="gallery_dl_password" type="text"' in template
    assert 'value="{{ settings.GALLERY_DL_PASSWORD.value }}"' in template
    assert "{{ settings.GALLERY_DL_EXTRA_OPTIONS.value }}</textarea>" in template
    assert 'value="{{ settings.YT_DLP_COOKIES_FILE.value }}"' in template
    assert 'value="{{ settings.YT_DLP_COOKIES_FROM_BROWSER.value }}"' in template
    assert "{{ settings.YT_DLP_EXTRA_OPTIONS.value }}</textarea>" in template


def test_home_page_displays_saved_credentials_plaintext(app_modules: tuple) -> None:
    _utils, db, _downloader, main, _data_root, _config_root = app_modules
    db.set_setting("HF_TOKEN", "hf-visible-token")
    db.set_setting("GALLERY_DL_PASSWORD", "gallery-visible-password")
    db.set_setting("GALLERY_DL_EXTRA_OPTIONS", "extractor.example.api-key=visible")
    db.set_setting("YT_DLP_COOKIES_FILE", "/config/yt-dlp/cookies.txt")
    db.set_setting("YT_DLP_PROXY", "socks5://192.168.200.100:1080")
    client = TestClient(main.app)

    response = client.get("/", auth=("admin", "test-password-that-is-long"))

    assert response.status_code == 200
    html = response.text
    assert 'value="hf-visible-token"' in html
    assert 'value="gallery-visible-password"' in html
    assert "extractor.example.api-key=visible</textarea>" in html
    assert 'value="/config/yt-dlp/cookies.txt"' in html
    assert 'value="socks5://192.168.200.100:1080"' in html
    assert 'id="gallery_dl_password" name="gallery_dl_password" type="text"' in html
    assert 'id="gallery_dl_password" name="gallery_dl_password" type="password"' not in html


def test_home_template_declares_storage_folder_search_ui(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    template = (main.BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    stylesheet = (main.BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")

    assert 'id="folder-search-form"' in template
    assert 'id="folder_search"' in template
    assert 'id="folder-search-results"' in template
    assert 'id="folder-refresh-button"' in template
    assert 'data-action="create-folder"' in template
    assert 'id="folder-create-modal"' in template
    assert 'id="folder-move-modal"' in template
    assert "이동할 대상 폴더를 /data 기준 경로로 입력하세요." not in template
    assert "{% if depth == 0 %} expanded{% endif %}" in template
    assert "fetch('/api/folders'" in template
    assert "async function refreshFolders(options = {})" in template
    assert "await refreshFolders({manual: true, preserveExpanded: true});" in template
    assert "function jobsCompletedWithTarget(previousJobs, nextJobs)" in template
    assert "const refreshFoldersAfterRender = jobsCompletedWithTarget(currentJobs, nextJobs);" in template
    assert "await Promise.all([refreshJobs(), refreshLibraryItems(), refreshFolders(), refreshStorage(), refreshSubscriptions()]);" in template
    assert "async function refreshLibraryItems(options = {})" in template
    assert "async function refreshLibraryForActivePath(options = {})" in template
    assert "refreshLibraryItems({mode: 'live', path: activeLibraryPath});" in template
    assert "params.set('path', normalizePath(options.path || ''))" in template
    assert "await refreshLibraryItems({mode: 'live'});" in template
    assert "function nextPathAfterFileAction(url, payload, data, previousLibraryPath, affectedPath, options = {})" in template
    assert "window.location.reload();" not in template
    assert "function folderSearchScopePath()" in template
    assert "function isFolderRowInSearchScope(item, scopePath = folderSearchScopePath())" in template
    assert "return normalized ? `/data/${normalized} 내부` : '/data 전체';" in template
    assert "path === normalizedScope || path.startsWith(`${normalizedScope}/`)" in template
    assert "collectFolderRows().filter(item => isFolderRowInSearchScope(item, scopePath)).filter(item => (" in template
    assert template.count("if (folderSearchInput?.value.trim()) updateFolderSearch();") == 2
    assert "const rows = collectFolderRows();" in template
    assert ".folder-search-form" in stylesheet
    assert ".folder-refresh-button" in stylesheet
    assert ".folder-search-result" in stylesheet
    assert ".folder-modal-tree" in stylesheet
    assert ".folder-modal-row.selected" in stylesheet


def test_folder_tree_large_sibling_does_not_hide_later_route_roots(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    for name in ("asmr.one", "gallery-dl", "hitomi", "huggingface"):
        (data_root / name).mkdir()
    for index in range(20):
        (data_root / "hitomi" / f"{index:03d}-gallery").mkdir()
    (data_root / "stable-diffusion" / "loras").mkdir(parents=True)

    tree = main.build_folder_tree(data_root, max_depth=3, max_entries=12, max_children_per_folder=4)
    root_children = {child["name"]: child for child in tree["children"]}
    stable_children = {child["name"] for child in root_children["stable-diffusion"]["children"]}

    assert {"asmr.one", "gallery-dl", "hitomi", "huggingface", "stable-diffusion"} <= set(root_children)
    assert len(root_children["hitomi"]["children"]) == 4
    assert "loras" in stable_children


def test_api_create_folder_creates_child_and_rejects_nested_name(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    parent = data_root / "stable-diffusion"
    parent.mkdir()
    client = TestClient(main.app)

    response = client.post(
        "/api/folders",
        json={"parent_path": "stable-diffusion", "folder_name": "checkpoints"},
        auth=("admin", "test-password-that-is-long"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == "stable-diffusion/checkpoints"
    assert (parent / "checkpoints").is_dir()
    assert payload["folders"]["children"]

    bad_response = client.post(
        "/api/folders",
        json={"parent_path": "stable-diffusion", "folder_name": "bad/name"},
        auth=("admin", "test-password-that-is-long"),
    )

    assert bad_response.status_code == 400


def png_top_left_alpha(path: Path) -> int:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    width = 0
    color_type = None
    compressed = bytearray()
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width = int.from_bytes(chunk_data[0:4], "big")
            bit_depth = chunk_data[8]
            color_type = chunk_data[9]
            assert bit_depth == 8
            assert color_type == 6
        elif chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        elif chunk_type == b"IEND":
            break
    assert width > 0
    assert color_type == 6
    raw = zlib.decompress(bytes(compressed))
    return raw[4]


def test_storage_status_reports_data_volume_usage(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules

    class Usage:
        total = 1000
        used = 375
        free = 625

    seen_paths: list[Path] = []

    def fake_disk_usage(path: Path) -> Usage:
        seen_paths.append(path)
        return Usage()

    monkeypatch.setattr(main.shutil, "disk_usage", fake_disk_usage)

    payload = main.storage_status()

    assert seen_paths == [data_root]
    assert payload["path"] == "/data"
    assert payload["used_bytes"] == 375
    assert payload["free_bytes"] == 625
    assert payload["total_bytes"] == 1000
    assert payload["used_human"] == "375.0 B"
    assert payload["total_human"] == "1000.0 B"
    assert payload["percent"] == 37.5

    response_payload = json.loads(main.api_storage("tester").body.decode("utf-8"))
    assert response_payload["used_bytes"] == 375


def test_storage_status_includes_cached_hugcivi_usage(app_modules: tuple) -> None:
    _utils, db, _downloader, main, _data_root, _config_root = app_modules
    db.set_library_scan_state(
        main.STORAGE_USAGE_STATE_KEY,
        json.dumps(
            {
                "status": "done",
                "path": "/data",
                "used_bytes": 1536,
                "file_count": 2,
                "dir_count": 1,
                "skipped_count": 0,
                "scanned_entries": 3,
                "scanned_at": "2026-07-02T00:00:00+00:00",
            }
        ),
    )

    payload = main.storage_status()

    assert payload["archive_usage"]["status"] == "done"
    assert payload["archive_usage"]["used_bytes"] == 1536
    assert payload["archive_usage"]["used_human"] == "1.5 KB"
    assert payload["archive_usage"]["file_count"] == 2


def test_chrome_extension_archive_contains_loadable_folder(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    extension_dir = tmp_path / "extension"
    extension_dir.mkdir()
    (extension_dir / "manifest.json").write_text('{"manifest_version":3}', encoding="utf-8")
    (extension_dir / "popup.html").write_text("<!doctype html>", encoding="utf-8")
    (extension_dir / ".DS_Store").write_text("skip", encoding="utf-8")
    monkeypatch.setattr(main, "CHROME_EXTENSION_DIR", extension_dir)

    archive_path = main.create_chrome_extension_archive()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
    finally:
        main.cleanup_file(archive_path)

    assert "hugcivi-chrome-extension/manifest.json" in names
    assert "hugcivi-chrome-extension/popup.html" in names
    assert "hugcivi-chrome-extension/.DS_Store" not in names


def test_storage_usage_scan_counts_data_files_without_following_symlinks(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, _db, _downloader, main, data_root, config_root = app_modules
    monkeypatch.setenv("STORAGE_USAGE_SCAN_BATCH_SIZE", "1")
    monkeypatch.setenv("STORAGE_USAGE_SCAN_SLEEP_SECONDS", "0")
    (data_root / "a.bin").write_bytes(b"abc")
    nested = data_root / "nested"
    nested.mkdir()
    (nested / "b.bin").write_bytes(b"de")
    (data_root / "escape").symlink_to(config_root, target_is_directory=True)

    progress: list[dict[str, int]] = []
    result = main.scan_data_root_usage(progress_callback=progress.append)

    assert result["used_bytes"] == 5
    assert result["file_count"] == 2
    assert result["dir_count"] == 1
    assert result["skipped_count"] == 1
    assert result["scanned_entries"] == 4
    assert progress


def test_library_items_index_generic_sidecar_folder(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "generic"
    target.mkdir()
    (target / "model.bin").write_text("model", encoding="utf-8")
    (target / "_generic_metadata.json").write_text(
        json.dumps(
            {
                "source": "generic",
                "url": "https://example.com/model.bin",
                "raw_input": "https://example.com/model.bin",
            }
        ),
        encoding="utf-8",
    )

    rows = [row for row in main.library_items() if row.get("target_path") == "generic"]

    assert len(rows) == 1
    assert rows[0]["source"] == "generic"
    assert rows[0]["source_url"] == "https://example.com/model.bin"
    assert rows[0]["status"] == "done"


def test_library_items_skip_empty_archive_metadata_folder(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "gallery-dl" / "xhamster3.com" / "failed"
    target.mkdir(parents=True)
    (target / "_archive_metadata.json").write_text(
        json.dumps(
            {
                "source": "gallery-dl",
                "source_url": "https://xhamster3.com/videos/failed",
                "raw_input": "https://xhamster3.com/videos/failed",
            }
        ),
        encoding="utf-8",
    )

    rows = [row for row in main.library_items() if row.get("target_path") == "gallery-dl/xhamster3.com/failed"]

    assert rows == []


def test_library_items_use_ytdlp_info_title_for_single_video_archive(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "gallery-dl" / "youtube.com" / "video-abc123"
    target.mkdir(parents=True)
    (target / "_archive_metadata.json").write_text(
        json.dumps(
            {
                "source": "gallery-dl",
                "source_url": "ytdl:https://www.youtube.com/watch?v=abc123",
                "raw_input": "https://www.youtube.com/watch?v=abc123",
                "host": "youtube.com",
            }
        ),
        encoding="utf-8",
    )
    (target / "Actual Video [abc123].mp4").write_bytes(b"video")
    (target / "Actual Video [abc123].info.json").write_text(
        json.dumps(
            {
                "title": "Actual Video Title",
                "webpage_url": "https://www.youtube.com/watch?v=abc123",
            }
        ),
        encoding="utf-8",
    )

    rows = [row for row in main.library_items() if row.get("target_path") == "gallery-dl/youtube.com/video-abc123"]

    assert len(rows) == 1
    assert rows[0]["model_title"] == "Actual Video Title"
    assert rows[0]["source_url"] == "https://www.youtube.com/watch?v=abc123"


def test_decorated_gallerydl_job_uses_ytdlp_info_title_for_single_video(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "gallery-dl" / "youtube.com" / "video-abc123"
    target.mkdir(parents=True)
    (target / "Actual Video [abc123].mp4").write_bytes(b"video")
    (target / "Actual Video [abc123].info.json").write_text(
        json.dumps({"title": "Actual Video Title"}),
        encoding="utf-8",
    )
    parsed = ParsedDownload(
        source="gallerydl",
        raw_input="https://www.youtube.com/watch?v=abc123",
        gallerydl_url="ytdl:https://www.youtube.com/watch?v=abc123",
    )
    job_id = db.create_job(parsed)
    db.update_job(
        job_id,
        status="done",
        target_dir=str(target),
        model_title="video-abc123",
        model_category="gallery-dl",
        model_type="youtube.com",
    )

    rows = main.decorate_jobs(db.list_jobs())

    assert len(rows) == 1
    assert rows[0]["model_title"] == "Actual Video Title"


def test_existing_data_path_preserves_downloaded_media_filename_punctuation(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "gallery-dl" / "youtube.com" / "video-XlFu9nJCA1A"
    target.mkdir(parents=True)
    filename = "진짜 자동으로 다~해줍니다!!! 크롤링, 예약, 구매, 쇼핑, E2E - Aside [XlFu9nJCA1A].mp4"
    media = target / filename
    media.write_bytes(b"video")

    relative = f"gallery-dl/youtube.com/video-XlFu9nJCA1A/{filename}"

    assert main.existing_data_path(relative) == media
    with pytest.raises(HTTPException) as exc_info:
        main.data_path_from_request_path("../outside")
    assert exc_info.value.status_code == 400


def test_media_item_payload_exposes_sidecar_subtitles(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "gallery-dl" / "youtube.com" / "video-abc123"
    target.mkdir(parents=True)
    media = target / "Sample Video [abc123].mp4"
    media.write_bytes(b"video")
    (target / "Sample Video [abc123].en.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    (target / "Sample Video [abc123].ko.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n", encoding="utf-8")

    payload = main.media_item_payload(media, 0)

    assert [track["language"] for track in payload["subtitles"]] == ["ko", "en"]
    assert payload["subtitles"][0]["label"] == "한국어"
    assert payload["subtitles"][1]["label"] == "English"
    assert payload["subtitles"][0]["url"].startswith("/api/media/subtitle?path=gallery-dl/youtube.com/video-abc123/")


def test_media_subtitle_endpoint_converts_srt_to_vtt(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    subtitle = data_root / "gallery-dl" / "youtube.com" / "video-abc123" / "Sample Video [abc123].en.srt"
    subtitle.parent.mkdir(parents=True)
    subtitle.write_text("1\n00:00:01,500 --> 00:00:02,750\nHello\n", encoding="utf-8")

    response = main.api_media_subtitle("gallery-dl/youtube.com/video-abc123/Sample Video [abc123].en.srt", "_")

    assert response.media_type == "text/vtt"
    body = response.body.decode("utf-8")
    assert body.startswith("WEBVTT")
    assert "00:00:01.500 --> 00:00:02.750" in body
    assert "\n1\n" not in body
