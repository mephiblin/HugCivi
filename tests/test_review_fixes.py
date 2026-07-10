from __future__ import annotations

import importlib
import json
import os
import shutil
import zipfile
import zlib
from datetime import datetime
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
    old_thumbnail = media_cache / "thumbnails" / "old.jpg"
    old_thumbnail.parent.mkdir(parents=True, exist_ok=True)
    for path in (old_zip, fresh_zip, old_media, fresh_media, temp_media, old_thumbnail):
        path.write_bytes(b"x")
    os.utime(old_zip, (100, 100))
    os.utime(old_media, (100, 100))
    os.utime(temp_media, (100, 100))
    os.utime(old_thumbnail, (100, 100))
    os.utime(fresh_zip, (990, 990))
    os.utime(fresh_media, (990, 990))
    monkeypatch.setenv("DOWNLOAD_ARCHIVE_TTL_SECONDS", "500")
    monkeypatch.setenv("MEDIA_CACHE_TTL_SECONDS", "500")

    assert main.cleanup_stale_download_archives(now=1000) == 1
    assert main.cleanup_stale_media_cache(now=1000) == 3

    assert not old_zip.exists()
    assert fresh_zip.exists()
    assert not old_media.exists()
    assert fresh_media.exists()
    assert not temp_media.exists()
    assert not old_thumbnail.exists()


def test_media_cache_quota_uses_access_time_lru(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, _data_root, config_root = app_modules
    media_cache = config_root / "media-cache"
    media_cache.mkdir(parents=True, exist_ok=True)
    old_access = media_cache / "old-access.play.mp4"
    recent_access = media_cache / "recent-access.play.mp4"
    active_temp = media_cache / ".active.tmp.mp4"
    old_access.write_bytes(b"1234")
    recent_access.write_bytes(b"5678")
    active_temp.write_bytes(b"x" * 100)
    os.utime(old_access, (100, 900))
    os.utime(recent_access, (800, 200))
    os.utime(active_temp, (50, 50))

    assert main.cleanup_cache_quota(media_cache, 4) == 1
    assert not old_access.exists()
    assert recent_access.exists()
    assert active_temp.exists()


def test_media_cache_cleanup_uses_saved_quota_settings(app_modules: tuple) -> None:
    _utils, db, _downloader, main, _data_root, config_root = app_modules
    media_cache = config_root / "media-cache"
    media_cache.mkdir(parents=True, exist_ok=True)
    old_access = media_cache / "old.play.mp4"
    recent_access = media_cache / "recent.play.mp4"
    old_access.write_bytes(b"1234")
    recent_access.write_bytes(b"5678")
    os.utime(old_access, (100, 900))
    os.utime(recent_access, (800, 200))
    db.set_setting("MEDIA_CACHE_TTL_SECONDS", "0")
    db.set_setting("MEDIA_CACHE_MAX_BYTES", "4")
    client = TestClient(main.app)

    status = client.get("/api/media/cache", auth=("admin", "test-password-that-is-long"))
    assert status.status_code == 200
    assert status.json()["cache"]["policy"]["max_bytes"] == 4

    cleanup = client.post(
        "/api/media/cache/cleanup",
        json={"scope": "all"},
        auth=("admin", "test-password-that-is-long"),
    )

    assert cleanup.status_code == 200
    assert cleanup.json()["removed"] == 1
    assert not old_access.exists()
    assert recent_access.exists()


def test_media_cache_status_and_manual_thumbnail_clear(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, _data_root, config_root = app_modules
    media_cache = config_root / "media-cache"
    thumbnail_cache = media_cache / "thumbnails"
    thumbnail_cache.mkdir(parents=True, exist_ok=True)
    (media_cache / "video.play.mp4").write_bytes(b"transcode")
    (thumbnail_cache / "thumb.jpg").write_bytes(b"thumbnail")
    client = TestClient(main.app)

    status = client.get("/api/media/cache", auth=("admin", "test-password-that-is-long"))
    assert status.status_code == 200
    cache = status.json()["cache"]
    assert cache["file_count"] == 2
    assert cache["categories"]["thumbnails"]["files"] == 1
    assert cache["categories"]["transcodes"]["files"] == 1

    cleared = client.post(
        "/api/media/cache/cleanup",
        json={"scope": "thumbnails", "clear": True},
        auth=("admin", "test-password-that-is-long"),
    )
    assert cleared.status_code == 200
    payload = cleared.json()
    assert payload["removed"] == 1
    assert not (thumbnail_cache / "thumb.jpg").exists()
    assert (media_cache / "video.play.mp4").exists()


def test_media_thumbnail_endpoint_creates_and_reuses_cached_image(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, _db, _downloader, main, data_root, config_root = app_modules
    source = data_root / "covers" / "cover.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> None:
        calls.append(command)
        Path(command[-1]).write_bytes(b"jpg")

    monkeypatch.setattr(main.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(main.subprocess, "run", fake_run)

    first = main.api_media_thumbnail(path="covers/cover.png", _="_")
    second = main.api_media_thumbnail(path="covers/cover.png", _="_")
    cached = Path(first.path)

    assert cached.parent == config_root / "media-cache" / "thumbnails"
    assert cached.exists()
    assert Path(second.path) == cached
    assert first.headers["cache-control"] == main.MEDIA_THUMBNAIL_CACHE_CONTROL
    assert len(calls) == 1
    assert calls[0][-1].endswith(".tmp.jpg")


def test_library_item_marks_ready_card_thumbnail_cache(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, config_root = app_modules
    folder = data_root / "cards" / "alpha"
    cover = folder / "cover.png"
    cover.parent.mkdir(parents=True)
    cover.write_bytes(b"png")

    cold = main.library_item_for_path(folder, favorites=set())

    assert cold["thumbnail_url"].startswith("/api/media/thumbnail?path=cards/alpha/cover.png")
    assert "&v=" in cold["thumbnail_url"]
    assert cold["thumbnail_ready"] is False

    cached = main.media_thumbnail_target(cover, main.MEDIA_THUMBNAIL_DEFAULT_SIZE)
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"jpg")

    ready = main.library_item_for_path(folder, favorites=set())

    assert ready["thumbnail_url"] == cold["thumbnail_url"]
    assert ready["thumbnail_ready"] is True
    assert cached.parent == config_root / "media-cache" / "thumbnails"


def test_media_thumbnail_endpoint_rejects_root_and_symlink(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    source = data_root / "cover.png"
    source.write_bytes(b"png")
    link = data_root / "link.png"
    link.symlink_to(source)
    outside = data_root.parent / "outside"
    outside.mkdir()
    (outside / "cover.png").write_bytes(b"png")
    link_dir = data_root / "linked-dir"
    link_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(HTTPException) as root_error:
        main.api_media_thumbnail(path="", _="_")
    assert root_error.value.status_code == 400

    with pytest.raises(HTTPException) as link_error:
        main.api_media_thumbnail(path="link.png", _="_")
    assert link_error.value.status_code == 400

    with pytest.raises(HTTPException) as link_dir_error:
        main.api_media_thumbnail(path="linked-dir", _="_")
    assert link_dir_error.value.status_code == 400


def test_media_thumbnail_backfill_job_uses_card_representatives(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    archive = data_root / "asmr.one" / "RJ123456 - Sample"
    archive.mkdir(parents=True)
    cover = archive / "cover.jpg"
    cover.write_bytes(b"cover")
    extra = archive / "z-extra.png"
    extra.write_bytes(b"extra")
    (archive / "track.mp3").write_bytes(b"audio")
    queued: list[int] = []
    calls: list[str] = []

    def fake_cached_thumbnail(source: Path, *, size: int | None = None) -> Path:
        calls.append(main.relative_data_path(source))
        target = main.media_thumbnail_target(source, main.thumbnail_size_value(size))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"jpg")
        return target

    monkeypatch.setattr(main.internal_jobs, "enqueue_job", lambda job_id: queued.append(job_id))
    monkeypatch.setattr(main, "cached_image_thumbnail_path", fake_cached_thumbnail)
    client = TestClient(main.app)

    response = client.post(
        "/api/media/thumbnail-jobs",
        json={"path": "asmr.one", "workers": 3},
        auth=("admin", "test-password-that-is-long"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["queued"] is True
    assert payload["candidate_count"] == 1
    assert payload["workers"] == 3
    assert queued == [payload["job_id"]]

    job = db.get_job(payload["job_id"])
    assert job is not None
    assert job["job_kind"] == main.INTERNAL_JOB_MEDIA_THUMBNAIL_BACKFILL
    parsed_payload = db.parse_internal_job_payload(job)
    assert "paths" not in parsed_payload
    assert parsed_payload["candidate_count"] == 1

    main.register_internal_job_handlers()
    main.internal_jobs.run_job(payload["job_id"])

    updated = db.get_job(payload["job_id"])
    assert updated is not None
    assert updated["status"] == "done"
    assert updated["progress_bytes"] == 1
    assert updated["total_bytes"] == 1
    assert calls == ["asmr.one/RJ123456 - Sample/cover.jpg"]
    assert main.media_thumbnail_target(cover, main.MEDIA_THUMBNAIL_DEFAULT_SIZE).exists()
    metadata = json.loads(updated["metadata_json"])
    assert metadata["media_job"]["generated"] == 1
    assert metadata["media_job"]["workers"] == 3


def test_settings_post_saves_cache_and_maintenance_policy(app_modules: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    _utils, db, _downloader, main, _data_root, _config_root = app_modules
    monkeypatch.setenv("LIBRARY_WATCHER_LOCAL_ONLY", "0")
    client = TestClient(main.app)

    response = client.post(
        "/settings",
        data={
            "media_cache_ttl_seconds": "60",
            "media_cache_max_bytes": "4096",
            "media_thumbnail_backfill_workers": "5",
            "media_thumbnail_backfill_max_items": "250",
            "internal_job_maintenance_mode": "window",
            "internal_job_maintenance_start_hour": "22",
            "internal_job_maintenance_end_hour": "5",
            "library_watcher_enabled": "1",
            "media_video_preview_mode": "keyframes",
        },
        auth=("admin", "test-password-that-is-long"),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert db.get_setting("MEDIA_CACHE_TTL_SECONDS") == "60"
    assert db.get_setting("MEDIA_CACHE_MAX_BYTES") == "4096"
    assert db.get_setting("MEDIA_THUMBNAIL_BACKFILL_WORKERS") == "5"
    assert db.get_setting("MEDIA_THUMBNAIL_BACKFILL_MAX_ITEMS") == "250"
    assert db.get_setting("INTERNAL_JOB_MAINTENANCE_MODE") == "window"
    assert db.get_setting("INTERNAL_JOB_MAINTENANCE_START_HOUR") == "22"
    assert db.get_setting("INTERNAL_JOB_MAINTENANCE_END_HOUR") == "5"
    assert db.get_setting("LIBRARY_WATCHER_ENABLED") == "1"
    assert db.get_setting("LIBRARY_WATCHER_LOCAL_ONLY") is None
    assert db.get_setting("MEDIA_VIDEO_PREVIEW_MODE") == "keyframes"
    assert main.thumbnail_backfill_worker_count() == 5
    watcher = client.get("/api/library/watcher", auth=("admin", "test-password-that-is-long"))
    assert watcher.status_code == 200
    assert watcher.json()["watcher"]["local_only"] is False


def test_internal_job_maintenance_policy_defers_heavy_jobs(app_modules: tuple) -> None:
    _utils, db, _downloader, main, _data_root, _config_root = app_modules
    heavy_id = db.create_internal_job(main.INTERNAL_JOB_LIBRARY_REINDEX, input_text="reindex", payload={})
    light_id = db.create_internal_job(main.INTERNAL_JOB_TRANSFER_COPY, input_text="transfer", payload={})
    heavy_job = db.get_job(heavy_id)
    light_job = db.get_job(light_id)
    assert heavy_job is not None
    assert light_job is not None

    assert main.internal_jobs.job_start_allowed(heavy_job) is True
    db.set_setting("INTERNAL_JOB_MAINTENANCE_MODE", "paused")
    assert main.internal_jobs.job_start_allowed(heavy_job) is False
    assert main.internal_jobs.job_start_allowed(light_job) is True

    db.set_setting("INTERNAL_JOB_MAINTENANCE_MODE", "window")
    db.set_setting("INTERNAL_JOB_MAINTENANCE_START_HOUR", "1")
    db.set_setting("INTERNAL_JOB_MAINTENANCE_END_HOUR", "3")
    assert main.internal_jobs.job_start_allowed(heavy_job, now=datetime(2026, 7, 9, 2, 0, 0)) is True
    assert main.internal_jobs.job_start_allowed(heavy_job, now=datetime(2026, 7, 9, 4, 0, 0)) is False


def test_policy_status_endpoints_default_disabled(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    client = TestClient(main.app)

    watcher = client.get("/api/library/watcher", auth=("admin", "test-password-that-is-long"))
    preview = client.get("/api/media/video-preview", auth=("admin", "test-password-that-is-long"))

    assert watcher.status_code == 200
    assert watcher.json()["watcher"]["status"] == "disabled"
    assert preview.status_code == 200
    assert preview.json()["preview"]["mode"] == "off"
    assert preview.json()["preview"]["enabled"] is False


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
    page_response = main.api_jobs(limit=2, page=2, _="_")
    page_payload = json.loads(page_response.body.decode("utf-8"))

    assert len(first_page) == 2
    assert "log" not in first_page[0]
    assert "metadata_json" not in first_page[0]
    assert payload["ok"] is True
    assert [job["id"] for job in payload["jobs"]] == [ids[1], ids[0]]
    assert payload["next_cursor"] == ids[0]
    assert page_payload["ok"] is True
    assert page_payload["page"] == 2
    assert page_payload["limit"] == 2
    assert page_payload["total_count"] == 3
    assert page_payload["total_pages"] == 2
    assert [job["id"] for job in page_payload["jobs"]] == [ids[0]]


def test_job_page_payload_supports_source_filters(app_modules: tuple) -> None:
    _utils, db, _downloader, main, _data_root, _config_root = app_modules
    generic_first = db.create_job(
        ParsedDownload(source="generic", raw_input="https://example.com/one.bin", url="https://example.com/one.bin")
    )
    db.create_job(
        ParsedDownload(
            source="hitomi",
            raw_input="hitomi 123456",
            hitomi_gallery_id="123456",
            hitomi_gallery_url="https://hitomi.la/galleries/123456.html",
        )
    )
    db.create_job(
        ParsedDownload(
            source="asmrone",
            raw_input="https://asmr.one/work/RJ1",
            asmrone_url="https://asmr.one/work/RJ1",
            asmrone_work_id="RJ1",
        )
    )
    generic_second = db.create_job(
        ParsedDownload(source="generic", raw_input="https://example.com/two.bin", url="https://example.com/two.bin")
    )

    payload = main.jobs_page_payload(limit=1, page=1, source="generic")
    api_payload = json.loads(main.api_jobs(limit=5, page=1, source="hitomi", _="_").body.decode("utf-8"))
    cursor_payload = json.loads(main.api_jobs(limit=5, cursor=generic_second + 1, source="generic", _="_").body.decode("utf-8"))
    source_counts = {item["source"]: item["count"] for item in payload["source_counts"]}

    assert payload["active_source"] == "generic"
    assert payload["total_count"] == 2
    assert payload["total_pages"] == 2
    assert [job["id"] for job in payload["jobs"]] == [generic_second]
    assert source_counts == {"generic": 2, "asmrone": 1, "hitomi": 1}
    assert api_payload["total_count"] == 1
    assert [job["source"] for job in api_payload["jobs"]] == ["hitomi"]
    assert [job["id"] for job in cursor_payload["jobs"]] == [generic_second, generic_first]


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
        for row in main.library_items(mode="live")
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
    assert "civitai_example_134865393.jpg" in rows[0]["thumbnail_url"]
    assert "&v=" in rows[0]["thumbnail_url"]


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

    assert [
        row
        for row in main.library_items()
        if row.get("target_path") == "stable-diffusion/loras/sdxl/disk-card/version_1"
    ] == []

    rows = [
        row
        for row in main.library_items(mode="live")
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


def test_library_index_schema_stores_source_group_fields(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "stable-diffusion" / "loras" / "schema-card"
    target.mkdir(parents=True)
    (target / "model.safetensors").write_text("model", encoding="utf-8")
    (target / "_civitai_metadata.json").write_text(
        json.dumps(
            {
                "source": "civitai",
                "raw_input": "https://civitai.com/models/100",
                "archive_info": {"model_title": "Schema Card", "model_category": "LoRA"},
            }
        ),
        encoding="utf-8",
    )

    main.scan_library_index_batch(max_paths=100, reset=True)

    with db.connect() as conn:
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(library_items)").fetchall()}
        indexes = {str(row["name"]) for row in conn.execute("PRAGMA index_list(library_items)").fetchall()}
        row = conn.execute(
            """
            SELECT source, source_group, model_category, parent_path, sort_title
            FROM library_items
            WHERE path = ?
            """,
            ("stable-diffusion/loras/schema-card",),
        ).fetchone()

    assert {"source", "source_group", "model_category", "parent_path", "sort_title"}.issubset(columns)
    assert {
        "idx_library_items_global_sort",
        "idx_library_items_source_sort",
        "idx_library_items_source_parent_sort",
        "idx_library_items_source_path_sort",
        "idx_library_items_category_sort",
        "idx_library_items_category_path_sort",
        "idx_library_items_mtime_desc",
        "idx_library_items_source_mtime_desc",
        "idx_library_items_category_mtime_desc",
        "idx_library_items_source_category_sort",
        "idx_library_items_source_category_mtime_desc",
    }.issubset(indexes)
    assert row is not None
    assert row["source"] == "civitai"
    assert row["source_group"] == "civitai"
    assert row["model_category"] == "LoRA"
    assert row["parent_path"] == "stable-diffusion/loras"
    assert row["sort_title"] == "schema card"


def test_library_index_global_sort_uses_stored_order_columns(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    root = data_root / "sort-plan"
    for name in ("bravo", "alpha"):
        target = root / name
        target.mkdir(parents=True)
        (target / "model.safetensors").write_text("model", encoding="utf-8")
        (target / "_civitai_metadata.json").write_text(
            json.dumps({"source": "civitai", "archive_info": {"model_title": name.title(), "model_category": "LoRA"}}),
            encoding="utf-8",
        )

    main.scan_library_index_batch(max_paths=100, reset=True)

    order_sql = db.library_index_order("az")
    assert "lower(" not in order_sql.lower()
    assert "case when sort_title" not in order_sql.lower()
    with db.connect() as conn:
        plan = [
            str(row["detail"])
            for row in conn.execute(
                f"EXPLAIN QUERY PLAN SELECT payload_json FROM library_items WHERE stale = 0 {order_sql} LIMIT 10"
            ).fetchall()
        ]

    assert not any("USE TEMP B-TREE" in detail.upper() for detail in plan)


@pytest.mark.parametrize(
    ("relative_path", "metadata", "filename", "expected"),
    [
        (
            "stable-diffusion/loras/civitai-card",
            {"source": "civitai", "archive_info": {"model_title": "Civitai", "model_category": "LoRA"}},
            "model.safetensors",
            "civitai",
        ),
        (
            "gallery-dl/example.com/gallery",
            {"source": "gallery-dl", "source_url": "https://example.com/gallery"},
            "image.jpg",
            "gallerydl",
        ),
        (
            "gallery-dl/youtube.com/video",
            {"source": "gallery-dl", "source_url": "ytdl:https://www.youtube.com/watch?v=abc"},
            "video.mp4",
            "ytdlp",
        ),
        ("hitomi/123", {"source": "hitomi"}, "page.jpg", "hitomi"),
        ("asmr.one/RJ123", {"source": "asmrone", "model_category": "ASMR.one Work"}, "cover.jpg", "asmrone"),
        ("generic/file", {"source": "generic", "raw_input": "https://example.com/file.bin"}, "file.bin", "generic"),
        ("huggingface/model", {"source": "huggingface", "repo_id": "org/model"}, "model.safetensors", "huggingface"),
        ("comfyui/workflows/flow", {"source": "comfyui", "title": "Flow"}, "flow.workflow.json", "comfyui"),
        ("plain-media", {}, "cover.jpg", "media"),
        ("local-model", {}, "model.safetensors", "unknown"),
    ],
)
def test_library_source_group_classification(
    app_modules: tuple,
    relative_path: str,
    metadata: dict[str, object],
    filename: str,
    expected: str,
) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / relative_path
    target.mkdir(parents=True)
    if metadata:
        (target / "_archive_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    payload = b"{}" if filename.endswith(".json") else b"content"
    (target / filename).write_bytes(payload)

    item = main.library_item_for_path(target, set())

    assert item["source_group"] == expected


def test_civitai_workflow_archive_requires_extracted_workflow_for_viewer_flag(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "civitai" / "workflows" / "qwen" / "flow"
    target.mkdir(parents=True)
    (target / "workflow.zip").write_bytes(b"zip")
    (target / "_civitai_metadata.json").write_text(
        json.dumps(
            {
                "source": "civitai",
                "archive_info": {
                    "model_title": "Qwen Flow",
                    "model_category": "ComfyUI Workflow",
                    "model_type": "Workflows",
                    "file_format": "ZIP",
                },
            }
        ),
        encoding="utf-8",
    )

    cold = main.library_item_for_path(target, set())
    (target / "workflow.json").write_text('{"nodes":[],"links":[]}', encoding="utf-8")
    ready = main.library_item_for_path(target, set())
    generic = data_root / "generic" / "json-config"
    generic.mkdir(parents=True)
    (generic / "_generic_metadata.json").write_text(json.dumps({"source": "generic"}), encoding="utf-8")
    (generic / "config.json").write_text("{}", encoding="utf-8")
    generic_item = main.library_item_for_path(generic, set())
    template = (main.BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert cold["model_category"] == "ComfyUI Workflow"
    assert cold["has_workflow"] is False
    assert ready["has_workflow"] is True
    assert generic_item["has_workflow"] is False
    assert "job.has_workflow === false" in template


def test_library_api_selected_folder_uses_index_before_live_scan(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    root = data_root / "stable-diffusion" / "loras"
    for index in range(3):
        target = root / f"indexed-{index}"
        target.mkdir(parents=True)
        (target / "model.safetensors").write_text("model", encoding="utf-8")
        (target / "_civitai_metadata.json").write_text(
            json.dumps(
                {
                    "source": "civitai",
                    "archive_info": {"model_title": f"Indexed {index}", "model_category": "LoRA"},
                }
            ),
            encoding="utf-8",
        )

    main.scan_library_index_batch(max_paths=100, reset=True)

    def fail_live_scan(*_args, **_kwargs):
        raise AssertionError("selected indexed folder should not use live scan")

    monkeypatch.setattr(main, "live_library_items_with_completion", fail_live_scan)

    payload = json.loads(
        main.api_library(
            path="stable-diffusion/loras",
            limit=2,
            page=1,
            source_group="civitai",
            sort="az",
            _="_",
        ).body.decode("utf-8")
    )

    assert payload["mode"] == "index"
    assert payload["source_group"] == "civitai"
    assert payload["total_count"] == 3
    assert payload["total_pages"] == 2
    assert [item["source_group"] for item in payload["items"]] == ["civitai", "civitai"]


def test_library_api_index_mode_does_not_live_scan_unindexed_scope(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "unindexed" / "disk-card"
    target.mkdir(parents=True)
    (target / "preview.jpg").write_bytes(b"image")

    def fail_live_scan(*_args, **_kwargs):
        raise AssertionError("index mode should not use live scan for unindexed folders")

    monkeypatch.setattr(main, "live_library_items_with_completion", fail_live_scan)

    payload = json.loads(
        main.api_library(path="unindexed", limit=50, page=1, sort="az", _="_").body.decode("utf-8")
    )

    assert payload["mode"] == "index"
    assert payload["items"] == []
    assert payload["total_count"] == 0
    assert payload["index_status"]["needs_refresh"] is True
    assert payload["index_status"]["fallback"] is False


def test_library_reindex_scope_filters_source_without_clearing_unrelated_rows(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    civitai = data_root / "mixed" / "civitai-card"
    gallery = data_root / "mixed" / "gallery-card"
    civitai.mkdir(parents=True)
    gallery.mkdir(parents=True)
    (civitai / "model.safetensors").write_text("model", encoding="utf-8")
    (civitai / "_civitai_metadata.json").write_text(
        json.dumps({"source": "civitai", "archive_info": {"model_title": "Civitai Card", "model_category": "LoRA"}}),
        encoding="utf-8",
    )
    (gallery / "image.jpg").write_bytes(b"image")
    (gallery / "_archive_metadata.json").write_text(
        json.dumps({"source": "gallery-dl", "source_url": "https://example.com/gallery"}),
        encoding="utf-8",
    )
    main.scan_library_index_batch(max_paths=100, reset=True)
    assert db.count_library_index_items(path_prefix="mixed") == 2

    (civitai / "_civitai_metadata.json").write_text(
        json.dumps({"source": "civitai", "archive_info": {"model_title": "Civitai Updated", "model_category": "LoRA"}}),
        encoding="utf-8",
    )

    result = json.loads(main.api_library_reindex(path="mixed", source_group="civitai", _="_").body.decode("utf-8"))
    job_id = int(result["job_id"])
    job = db.get_job(job_id)
    assert result["queued"] is True
    assert job is not None
    assert job["job_kind"] == main.INTERNAL_JOB_LIBRARY_REINDEX
    decorated = main.decorate_job(job)
    assert decorated["library_reindex_scope"] == {"path": "mixed", "source_group": "civitai", "category": ""}

    main.run_library_reindex_job(job_id, job)
    db.update_job(job_id, status="done")
    rows = db.list_library_index_items(path_prefix="mixed", sort="az")
    titles = {str(item["target_path"]): str(item["model_title"]) for item in rows}

    assert result["scope"]["path"] == "mixed"
    assert result["scope"]["source_group"] == "civitai"
    assert db.count_library_index_items(path_prefix="mixed", source_group="civitai") == 1
    assert db.count_library_index_items(path_prefix="mixed", source_group="gallerydl") == 1
    assert titles["mixed/civitai-card"] == "Civitai Updated"
    assert "mixed/gallery-card" in titles


def test_library_reindex_dedupes_active_scope(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    root = data_root / "dedupe"
    card = root / "card"
    card.mkdir(parents=True)
    (card / "model.safetensors").write_text("model", encoding="utf-8")
    (card / "_civitai_metadata.json").write_text(
        json.dumps({"source": "civitai", "archive_info": {"model_title": "Dedupe", "model_category": "LoRA"}}),
        encoding="utf-8",
    )

    first = json.loads(main.api_library_reindex(path="dedupe", source_group="civitai", _="_").body.decode("utf-8"))
    second = json.loads(main.api_library_reindex(path="dedupe", source_group="civitai", _="_").body.decode("utf-8"))

    jobs = [
        job
        for job in db.list_internal_jobs_to_resume()
        if job["job_kind"] == main.INTERNAL_JOB_LIBRARY_REINDEX
    ]
    assert first["job_id"] == second["job_id"]
    assert first["deduped"] is False
    assert second["deduped"] is True
    assert len(jobs) == 1

    other_scope = json.loads(
        main.api_library_reindex(path="dedupe", source_group="gallerydl", _="_").body.decode("utf-8")
    )
    assert other_scope["job_id"] != first["job_id"]
    assert other_scope["deduped"] is False

    db.update_job(int(first["job_id"]), status="done")
    after_done = json.loads(main.api_library_reindex(path="dedupe", source_group="civitai", _="_").body.decode("utf-8"))
    assert after_done["job_id"] != first["job_id"]
    assert after_done["deduped"] is False


def test_library_sync_scope_upserts_new_card_without_reindex_job(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "sync-scope" / "card"
    target.mkdir(parents=True)
    (target / "model.safetensors").write_text("model", encoding="utf-8")
    (target / "_civitai_metadata.json").write_text(
        json.dumps({"source": "civitai", "archive_info": {"model_title": "Synced Card", "model_category": "LoRA"}}),
        encoding="utf-8",
    )

    payload = json.loads(main.api_library_sync(path="sync-scope", _="_").body.decode("utf-8"))
    rows = db.list_library_index_items(path_prefix="sync-scope")

    assert payload["synced"] is True
    assert payload["complete"] is True
    assert payload["indexed"] == 1
    assert [row["model_title"] for row in rows] == ["Synced Card"]


def test_library_sync_scope_prunes_missing_index_rows(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    root = data_root / "sync-delete"
    target = root / "card"
    target.mkdir(parents=True)
    (target / "model.safetensors").write_text("model", encoding="utf-8")
    (target / "_civitai_metadata.json").write_text(
        json.dumps({"source": "civitai", "archive_info": {"model_title": "Deleted Card", "model_category": "LoRA"}}),
        encoding="utf-8",
    )
    main.scan_library_index_batch(max_paths=100, reset=True)
    assert db.count_library_index_items(path_prefix="sync-delete") == 1

    shutil.rmtree(target)
    payload = json.loads(main.api_library_sync(path="sync-delete", _="_").body.decode("utf-8"))

    assert payload["stale"] >= 1
    assert db.count_library_index_items(path_prefix="sync-delete") == 0


def test_library_reindex_job_resumes_scoped_batches(app_modules: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    root = data_root / "batched"
    for index in range(4):
        target = root / f"card-{index:03d}"
        target.mkdir(parents=True)
        (target / "model.safetensors").write_text("model", encoding="utf-8")
        (target / "_civitai_metadata.json").write_text(
            json.dumps(
                {
                    "source": "civitai",
                    "archive_info": {"model_title": f"Card {index:03d}", "model_category": "LoRA"},
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setenv("LIBRARY_REINDEX_BATCH_SIZE", "2")
    result = json.loads(main.api_library_reindex(path="batched", source_group="civitai", _="_").body.decode("utf-8"))
    job_id = int(result["job_id"])
    job = db.get_job(job_id)
    assert job is not None

    main.run_library_reindex_job(job_id, job)

    assert db.count_library_index_items(path_prefix="batched", source_group="civitai") == 4
    scope_key = main.library_index_scope_key(path_prefix="batched", source_group="civitai")
    assert db.get_library_scan_state(f"{scope_key}.complete", "0") == "1"
    assert db.get_library_scan_state(f"{scope_key}.cursor", "not-empty") == ""
    folder_state = db.get_library_folder_state("batched")
    assert folder_state is not None
    assert folder_state["status"] == "done"
    assert folder_state["complete"] is True
    assert folder_state["processed_count"] >= 4
    assert folder_state["indexed_count"] >= 4
    page = main.library_items_page_payload(root_path=root)
    assert page["index_status"]["folder_state"]["path"] == "batched"


def test_library_reindex_failure_clears_indexing_and_marks_folder_failed(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    root = data_root / "fails"
    root.mkdir()
    result = json.loads(main.api_library_reindex(path="fails", _="_").body.decode("utf-8"))
    job_id = int(result["job_id"])
    job = db.get_job(job_id)
    assert job is not None

    def fail_iter_library_scan_paths(*_args, **_kwargs):
        raise RuntimeError("scan boom")

    monkeypatch.setattr(main, "iter_library_scan_paths", fail_iter_library_scan_paths)

    with pytest.raises(RuntimeError, match="scan boom"):
        main.run_library_reindex_job(job_id, job)

    assert db.get_library_scan_state("library.indexing", "1") == "0"
    folder_state = db.get_library_folder_state("fails")
    assert folder_state is not None
    assert folder_state["status"] == "failed"
    assert folder_state["complete"] is False
    assert folder_state["detail"]["error"] == "scan boom"


def test_library_scan_batch_uses_bulk_upsert(app_modules: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    root = data_root / "bulk"
    for index in range(3):
        card = root / f"card-{index}"
        card.mkdir(parents=True)
        (card / "model.safetensors").write_text("model", encoding="utf-8")
        (card / "_civitai_metadata.json").write_text(
            json.dumps(
                {
                    "source": "civitai",
                    "archive_info": {"model_title": f"Bulk {index}", "model_category": "LoRA"},
                }
            ),
            encoding="utf-8",
        )

    calls: list[int] = []
    original = db.upsert_library_items

    def counting_upsert(items: list[dict]) -> int:
        calls.append(len(items))
        return original(items)

    monkeypatch.setattr(db, "upsert_library_items", counting_upsert)

    result = main.scan_library_index_batch(max_paths=100, reset=True)

    assert result["indexed"] == 3
    assert calls == [3]
    assert db.count_library_index_items(path_prefix="bulk", source_group="civitai") == 3


def test_library_api_keeps_legacy_array_and_returns_paged_wrapper(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    root = data_root / "stable-diffusion" / "loras"
    for name in ("alpha", "bravo", "charlie"):
        target = root / name
        target.mkdir(parents=True)
        (target / "model.safetensors").write_text("model", encoding="utf-8")
        (target / "_civitai_metadata.json").write_text(
            json.dumps(
                {
                    "source": "civitai",
                    "archive_info": {"model_title": name.title(), "model_category": "LoRA"},
                }
            ),
            encoding="utf-8",
        )

    main.scan_library_index_batch(max_paths=100, reset=True)

    legacy = json.loads(main.api_library(_="_").body.decode("utf-8"))
    page = json.loads(main.api_library(limit=2, page=1, sort="az", _="_").body.decode("utf-8"))

    assert isinstance(legacy, list)
    assert page["paged"] is True
    assert page["limit"] == 2
    assert page["page"] == 1
    assert page["total_count"] == 3
    assert page["total_pages"] == 2
    assert page["has_next"] is True
    assert [item["target_path"].rsplit("/", 1)[-1] for item in page["items"]] == ["alpha", "bravo"]


def test_library_api_date_sort_supports_newest_oldest_and_legacy_alias(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    root = data_root / "sort-fixtures"
    fixtures = (
        ("oldest-card", 1_700_000_000),
        ("middle-card", 1_700_000_100),
        ("newest-card", 1_700_000_200),
    )
    for name, timestamp in fixtures:
        target = root / name
        target.mkdir(parents=True)
        (target / "preview.jpg").write_bytes(b"image")
        os.utime(target, (timestamp, timestamp))

    main.scan_library_index_batch(max_paths=100, reset=True)

    def item_names(payload: dict[str, object]) -> list[str]:
        items = payload.get("items")
        assert isinstance(items, list)
        assert all(isinstance(item, dict) for item in items)
        return [str(item["target_path"]).rsplit("/", 1)[-1] for item in items]

    newest = json.loads(main.api_library(limit=10, page=1, sort="date_desc", _="_").body.decode("utf-8"))
    legacy = json.loads(main.api_library(limit=10, page=1, sort="date", _="_").body.decode("utf-8"))
    oldest = json.loads(main.api_library(limit=10, page=1, sort="date_asc", _="_").body.decode("utf-8"))
    live_oldest = json.loads(
        main.api_library(mode="live", path="sort-fixtures", limit=10, page=1, sort="date_asc", _="_").body.decode(
            "utf-8"
        )
    )

    assert newest["sort"] == "date_desc"
    assert legacy["sort"] == "date_desc"
    assert oldest["sort"] == "date_asc"
    assert live_oldest["total_count"] == 3
    assert live_oldest["total_pages"] == 1
    assert live_oldest["has_next"] is False
    assert item_names(newest) == ["newest-card", "middle-card", "oldest-card"]
    assert item_names(legacy) == item_names(newest)
    assert item_names(oldest) == ["oldest-card", "middle-card", "newest-card"]
    assert item_names(live_oldest) == ["oldest-card", "middle-card", "newest-card"]


def test_selected_folder_live_library_reports_total_pages_beyond_stable_scan_window(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    root = data_root / "live-total-fixtures"
    for index in range(151):
        target = root / f"card-{index:03d}"
        target.mkdir(parents=True)
        (target / "preview.jpg").write_bytes(b"image")

    first = json.loads(
        main.api_library(mode="live", path="live-total-fixtures", limit=50, page=1, sort="az", _="_").body.decode(
            "utf-8"
        )
    )
    fourth = json.loads(
        main.api_library(mode="live", path="live-total-fixtures", limit=50, page=4, sort="az", _="_").body.decode(
            "utf-8"
        )
    )
    out_of_range = json.loads(
        main.api_library(mode="live", path="live-total-fixtures", limit=50, page=99, sort="az", _="_").body.decode(
            "utf-8"
        )
    )

    assert first["mode"] == "live"
    assert first["path"] == "live-total-fixtures"
    assert first["total_count"] == 151
    assert first["total_pages"] == 4
    assert first["has_next"] is True
    assert len(first["items"]) == 50
    assert first["items"][0]["target_path"].endswith("card-000")

    assert fourth["page"] == 4
    assert fourth["total_count"] == 151
    assert fourth["total_pages"] == 4
    assert fourth["has_next"] is False
    assert len(fourth["items"]) == 1
    assert fourth["items"][0]["target_path"].endswith("card-150")

    assert out_of_range["page"] == 4
    assert out_of_range["total_pages"] == 4
    assert out_of_range["items"][0]["target_path"].endswith("card-150")


def test_selected_folder_live_library_five_page_boundaries_do_not_backfill_cards(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    root = data_root / "live-five-page-fixtures"
    for index in range(201):
        target = root / f"card-{index:03d}"
        target.mkdir(parents=True)
        (target / "preview.jpg").write_bytes(b"image")

    payloads = [
        json.loads(
            main.api_library(mode="live", path="live-five-page-fixtures", limit=50, page=page, sort="az", _="_")
            .body.decode("utf-8")
        )
        for page in range(1, 6)
    ]
    pages = {payload["page"]: payload for payload in payloads}

    assert sorted(pages) == [1, 2, 3, 4, 5]
    assert {payload["total_count"] for payload in payloads} == {201}
    assert {payload["total_pages"] for payload in payloads} == {5}
    assert [len(pages[page]["items"]) for page in range(1, 6)] == [50, 50, 50, 50, 1]
    assert [pages[page]["has_next"] for page in range(1, 6)] == [True, True, True, True, False]

    page_paths = {
        page: [str(item["target_path"]).rsplit("/", 1)[-1] for item in pages[page]["items"]]
        for page in range(1, 6)
    }
    assert page_paths[1] == [f"card-{index:03d}" for index in range(0, 50)]
    assert page_paths[2] == [f"card-{index:03d}" for index in range(50, 100)]
    assert page_paths[3] == [f"card-{index:03d}" for index in range(100, 150)]
    assert page_paths[4] == [f"card-{index:03d}" for index in range(150, 200)]
    assert page_paths[5] == ["card-200"]
    assert len({path for paths in page_paths.values() for path in paths}) == 201

    out_of_range = json.loads(
        main.api_library(mode="live", path="live-five-page-fixtures", limit=50, page=99, sort="az", _="_")
        .body.decode("utf-8")
    )
    assert out_of_range["page"] == 5
    assert len(out_of_range["items"]) == 1
    assert str(out_of_range["items"][0]["target_path"]).endswith("card-200")


def test_selected_folder_live_library_reuses_completed_scan_for_page_navigation(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    root = data_root / "live-cache-fixtures"
    for index in range(101):
        target = root / f"card-{index:03d}"
        target.mkdir(parents=True)
        (target / "preview.jpg").write_bytes(b"image")

    main.clear_live_library_page_cache()
    original = main.live_library_items_with_completion
    calls: list[Path | None] = []

    def counting_live_library_items_with_completion(*args, **kwargs):
        calls.append(kwargs.get("root_path"))
        return original(*args, **kwargs)

    monkeypatch.setattr(main, "live_library_items_with_completion", counting_live_library_items_with_completion)

    first = json.loads(
        main.api_library(mode="live", path="live-cache-fixtures", limit=50, page=1, sort="az", _="_").body.decode(
            "utf-8"
        )
    )
    second = json.loads(
        main.api_library(mode="live", path="live-cache-fixtures", limit=50, page=2, sort="az", _="_").body.decode(
            "utf-8"
        )
    )
    third_newest = json.loads(
        main.api_library(
            mode="live",
            path="live-cache-fixtures",
            limit=50,
            page=3,
            sort="date_desc",
            _="_",
        ).body.decode("utf-8")
    )

    assert len(calls) == 1
    assert calls[0] == root
    assert first["total_count"] == 101
    assert second["total_pages"] == 3
    assert len(third_newest["items"]) == 1


def test_selected_folder_live_library_keeps_unknown_totals_when_scan_is_incomplete(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    root = data_root / "live-incomplete-fixtures"
    for index in range(3):
        target = root / f"card-{index:03d}"
        target.mkdir(parents=True)
        (target / "preview.jpg").write_bytes(b"image")

    monkeypatch.setattr(main, "LIVE_LIBRARY_PAGE_COUNT_MAX_PATHS", 2)

    payload = json.loads(
        main.api_library(mode="live", path="live-incomplete-fixtures", limit=2, page=1, sort="az", _="_").body.decode(
            "utf-8"
        )
    )

    assert payload["mode"] == "live"
    assert payload["path"] == "live-incomplete-fixtures"
    assert payload["total_count"] is None
    assert payload["total_pages"] is None


def test_live_library_pagination_sorts_stable_scan_window(app_modules: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    source_items = [
        {"target_path": f"late/z-card-{index:03d}", "model_title": f"zzz card {index:03d}"}
        for index in range(60)
    ] + [
        {"target_path": f"early/a-card-{index:03d}", "model_title": f"aaa 漢字カード {index:03d}"}
        for index in range(60)
    ]
    scan_limits: list[int] = []

    def fake_live_library_items(max_items: int = 1000, *, root_path: Path | None = None) -> list[dict[str, str]]:
        scan_limits.append(max_items)
        return source_items[:max_items]

    monkeypatch.setattr(main, "live_library_items", fake_live_library_items)

    first_page, first_page_number, first_total_count, first_total_pages, first_has_next = main.live_library_items_page(
        limit=50, offset=0, sort="az"
    )
    second_page, second_page_number, second_total_count, second_total_pages, second_has_next = (
        main.live_library_items_page(limit=50, offset=50, sort="az")
    )
    first_paths = {str(item["target_path"]) for item in first_page}
    second_paths = {str(item["target_path"]) for item in second_page}

    assert first_page_number == 1
    assert second_page_number == 2
    assert first_total_count is None
    assert second_total_count is None
    assert first_total_pages is None
    assert second_total_pages is None
    assert first_has_next is True
    assert second_has_next is True
    assert main.LIVE_LIBRARY_PAGE_SCAN_MIN_ITEMS == main.LIBRARY_PAGE_SIZE * 3
    assert scan_limits == [main.LIVE_LIBRARY_PAGE_SCAN_MIN_ITEMS, main.LIVE_LIBRARY_PAGE_SCAN_MIN_ITEMS]
    assert len(first_paths) == 50
    assert len(second_paths) == 50
    assert first_paths.isdisjoint(second_paths)
    assert [str(item["target_path"]) for item in first_page[:2]] == ["early/a-card-000", "early/a-card-001"]


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
    assert 'id="job-source-filters"' in template
    assert 'data-job-source-filter' in template
    assert "function renderJobSourceFilters()" in template
    assert "params.set('source', requestedSource);" in template
    assert ".job-source-filters" in stylesheet
    assert 'id="refresh-button"' not in template
    assert ".refresh-corner" not in stylesheet
    top_corner = template[template.index('<div class="top-corner-actions">'):template.index('<section class="hero')]
    assert (
        top_corner.index('id="storage-readout"')
        < top_corner.index('id="storage-calc-button"')
        < top_corner.index('class="addon-button"')
        < top_corner.index('id="thumbnail-blur-toggle"')
    )
    assert '<th class="col-move">이동</th>' in template
    assert 'data-job-action="goto-folder"' in template
    assert 'function goToJobFolder(jobId)' in template
    assert 'body.innerHTML = \'<tr><td colspan="10" class="empty-row">아직 작업이 없습니다.</td></tr>\';' in template
    assert 'id="jobs-pagination"' in template
    assert "const initialJobsPage =" in template
    assert "function renderJobsPagination()" in template
    assert "data-jobs-page" in template
    assert "fetch(`/api/jobs?${params.toString()}`)" in template
    assert "coverUrl:" in template
    assert "media-audio-cover" in template
    assert ".media-audio-cover" in stylesheet
    assert "'document'" in template
    assert "setupMediaDocument(item)" in template
    assert "media-document-text" in template
    assert ".media-document-panel" in stylesheet
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
    assert "async function refreshLibraryItems(options = {})" in template
    assert "async function refreshLibraryForActivePath(options = {})" in template
    assert "function showLibraryLoading(path, mode = 'index', options = {})" in template
    assert "refreshLibraryForActivePath({page: 1});" in template
    assert "refreshLibraryForActivePath({page: 1, loading: true});" in template
    assert "refreshLibraryForActivePath({page, loading: true});" in template
    assert 'id="library-source-group"' in template
    assert 'id="library-refresh-button"' in template
    for value in ("civitai", "gallerydl", "ytdlp", "asmrone", "huggingface", "media", "unknown"):
        assert f'value="{value}"' in template
    assert "let activeLibrarySourceGroup" in template
    assert "function normalizeLibrarySourceGroup(value)" in template
    assert "params.set('source_group', requestedSourceGroup);" in template
    assert "let libraryRequestController" in template
    assert "new AbortController()" in template
    assert "const LIBRARY_REINDEX_POLL_INTERVAL_MS" in template
    assert "const trackedLibraryReindexJobs = new Map();" in template
    assert "function trackLibraryReindexJob(jobId, scope)" in template
    assert "async function pollTrackedLibraryReindexJobs()" in template
    assert "fetch(`/api/jobs/${encodeURIComponent(jobId)}`)" in template
    assert "async function refreshLibraryIndexScope(event)" in template
    assert "fetch(query ? `/api/library/sync?${query}` : '/api/library/sync'" in template
    assert "function libraryReindexCompletedForActiveScope(previousJobs, nextJobs)" in template
    assert "아직 색인된 카드가 없습니다." in template
    assert "function visibleUnknownTotalLibraryPages(page, hasNext)" in template
    assert "function allKnownLibraryPages(totalPages)" in template
    assert "? allKnownLibraryPages(totalPages)\n        : visibleUnknownTotalLibraryPages(page, hasNext);" in template
    assert "return Array.from(new Set(candidates)).filter(value => value >= 1);" in template
    assert "return Array.from({length: count}, (_, index) => index + 1);" in template
    assert '<option value="date_desc">최신순</option>' in template
    assert '<option value="date_asc">오래된순</option>' in template
    assert '<option value="date">날짜순</option>' not in template
    assert "params.set('path', normalizePath(options.path || ''))" in template
    assert "function nextPathAfterFileAction(url, payload, data, previousLibraryPath, affectedPath, options = {})" in template
    assert "window.location.reload();" not in template
    assert "function folderSearchScopePath()" in template
    assert "function isFolderRowInSearchScope(item, scopePath = folderSearchScopePath())" in template
    assert "return normalized ? `/data/${normalized} 내부` : '/data 전체';" in template
    assert "path === normalizedScope || path.startsWith(`${normalizedScope}/`)" in template
    assert "const FOLDER_SEARCH_ENDPOINT = '/api/folders/search';" in template
    assert "async function fetchFolderSearchResults(rawQuery, scopePath)" in template
    assert "function localFolderSearchResults(rawQuery, scopePath)" in template
    assert "renderFolderSearchResults([], scopePath, {loading: true});" in template
    assert "folder-search-result-text" in template
    assert template.count("if (folderSearchInput?.value.trim()) updateFolderSearch();") == 2
    assert "const rows = collectFolderRows();" in template
    assert ".folder-search-form" in stylesheet
    assert ".folder-refresh-button" in stylesheet
    assert ".col-move" in stylesheet
    assert ".jobs-pagination" in stylesheet
    assert ".library-pagination" in stylesheet
    assert ".library-refresh-button" in stylesheet
    assert "flex-wrap: wrap;" in stylesheet
    assert "overflow-wrap: anywhere;" in stylesheet
    assert "word-break: break-word;" in stylesheet
    asset_title_style = stylesheet[
        stylesheet.index(".asset-overlay strong {") : stylesheet.index(".asset-overlay span {")
    ]
    assert "max-height: 2.56em;" in asset_title_style
    assert "-webkit-line-clamp: 2;" in asset_title_style
    assert ".folder-search-result" in stylesheet
    assert ".folder-modal-tree" in stylesheet
    assert ".folder-modal-row.selected" in stylesheet


def test_home_template_declares_deferred_thumbnail_queue(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    template = (main.BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    stylesheet = (main.BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")

    render_jobs = template[
        template.index("function renderJobs(jobs)") : template.index("function renderJobSourceFilters()")
    ]
    render_mobile_jobs = template[
        template.index("function renderMobileJobs(jobs)") : template.index("function renderMobileJobCard(job)")
    ]
    render_mobile_job_card = template[
        template.index("function renderMobileJobCard(job)") : template.index("function renderJobProgress(job)")
    ]
    render_model_info = template[
        template.index("function renderModelInfo(job)") : template.index("function renderLibrary()")
    ]
    render_library = template[
        template.index("function renderLibrary()") : template.index("function libraryCountLabel(visibleCount)")
    ]

    assert "THUMBNAIL_READY_REQUEST_MAX_ACTIVE = 10" in template
    assert "THUMBNAIL_COLD_REQUEST_MAX_ACTIVE = 3" in template
    assert "THUMBNAIL_REQUEST_TIMEOUT_MS = 60000" in template
    assert "function setupDeferredThumbnails" in template
    assert "function thumbnailImageReady" in template
    assert "thumbnailReadyQueue" in template
    assert "thumbnailColdQueue" in template
    assert 'id="library-thumbnail-job-button"' in template
    assert "fetch('/api/media/thumbnail-jobs'" in template
    assert "workers: THUMBNAIL_COLD_REQUEST_MAX_ACTIVE" not in template
    assert "media_thumbnail_backfill: 'Thumbnail'" in template
    assert ".library-thumbnail-job-button" in stylesheet
    assert "data-thumbnail-src" in render_model_info
    assert "data-thumbnail-src" in render_mobile_job_card
    assert "data-thumbnail-src" in render_library
    assert "data-thumbnail-ready" in template
    assert "IntersectionObserver" in template
    assert "new IntersectionObserver" in template
    assert "window.setTimeout(" in template
    assert "setupDeferredThumbnails(" in render_jobs
    assert "setupDeferredThumbnails(" in render_mobile_jobs
    assert "setupDeferredThumbnails(" in render_library
    assert render_jobs.rindex("renderMobileJobs(jobs);") < render_jobs.rindex("setupDeferredThumbnails(")
    assert (
        render_mobile_jobs.index("mobileJobsList.innerHTML = jobs.map(renderMobileJobCard).join('');")
        < render_mobile_jobs.rindex("setupDeferredThumbnails(")
    )
    assert (
        render_library.index("libraryGrid.innerHTML = matches.map(job => {")
        < render_library.rindex("setupDeferredThumbnails(")
    )


def test_home_template_declares_maintenance_cache_controls(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    template = (main.BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'data-settings-pane="settings-maintenance"' in template
    assert 'id="media_cache_ttl_seconds"' in template
    assert 'id="media_cache_max_bytes"' in template
    assert 'id="internal_job_maintenance_mode"' in template
    assert 'id="library_watcher_enabled"' in template
    assert 'id="media_video_preview_mode"' in template
    assert "fetch('/api/media/cache')" in template
    assert "fetch('/api/media/cache/cleanup'" in template


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


def test_api_folders_initial_tree_is_root_direct_children_only(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    parent = data_root / "stable-diffusion" / "checkpoints"
    parent.mkdir(parents=True)
    for index in range(151):
        (parent / f"model-{index:03d}").mkdir()

    client = TestClient(main.app)
    response = client.get("/api/folders", auth=("admin", "test-password-that-is-long"))

    assert response.status_code == 200
    payload = response.json()
    stable = next(child for child in payload["children"] if child["name"] == "stable-diffusion")
    assert payload["has_children"] is True
    assert payload["children_loaded"] is True
    assert stable["children"] == []
    assert stable["has_children"] is True
    assert stable["children_loaded"] is False


def test_api_folder_children_returns_direct_children_with_pagination(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    parent = data_root / "stable-diffusion" / "checkpoints"
    parent.mkdir(parents=True)
    for index in range(7):
        child = parent / f"model-{index:03d}"
        child.mkdir()
        if index == 0:
            (child / "nested").mkdir()

    client = TestClient(main.app)
    first_response = client.get(
        "/api/folders/children",
        params={"path": "stable-diffusion/checkpoints", "limit": 3},
        auth=("admin", "test-password-that-is-long"),
    )
    second_response = client.get(
        "/api/folders/children",
        params={"path": "stable-diffusion/checkpoints", "limit": 3, "cursor": "model-002"},
        auth=("admin", "test-password-that-is-long"),
    )
    bad_cursor_response = client.get(
        "/api/folders/children",
        params={"path": "stable-diffusion/checkpoints", "cursor": "missing"},
        auth=("admin", "test-password-that-is-long"),
    )

    assert first_response.status_code == 200
    first_payload = first_response.json()
    assert first_payload["path"] == "stable-diffusion/checkpoints"
    assert first_payload["limit"] == 3
    assert first_payload["has_more"] is True
    assert first_payload["next_cursor"] == "model-002"
    assert [item["name"] for item in first_payload["items"]] == ["model-000", "model-001", "model-002"]
    assert first_payload["items"][0]["path"] == "stable-diffusion/checkpoints/model-000"
    assert first_payload["items"][0]["has_children"] is True
    assert first_payload["items"][0]["children_loaded"] is False
    assert "children" not in first_payload["items"][0]

    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert [item["name"] for item in second_payload["items"]] == ["model-003", "model-004", "model-005"]
    assert second_payload["next_cursor"] == "model-005"
    assert second_payload["has_more"] is True
    assert bad_cursor_response.status_code == 400


def test_api_folder_search_finds_unloaded_nested_folders_and_respects_scope(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    checkpoints = data_root / "stable-diffusion" / "checkpoints"
    loras = data_root / "stable-diffusion" / "loras"
    (checkpoints / "illustrious-xl").mkdir(parents=True)
    (checkpoints / "pony-xl").mkdir()
    (loras / "character-pack").mkdir(parents=True)

    client = TestClient(main.app)
    response = client.get(
        "/api/folders/search",
        params={"q": "illustrious", "limit": 10},
        auth=("admin", "test-password-that-is-long"),
    )
    path_response = client.get(
        "/api/folders/search",
        params={"q": "stable checkpoints", "limit": 10},
        auth=("admin", "test-password-that-is-long"),
    )
    scoped_response = client.get(
        "/api/folders/search",
        params={"q": "illustrious", "scope": "stable-diffusion/loras", "limit": 10},
        auth=("admin", "test-password-that-is-long"),
    )
    limited_response = client.get(
        "/api/folders/search",
        params={"q": "xl", "limit": 1},
        auth=("admin", "test-password-that-is-long"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == ""
    assert payload["query"] == "illustrious"
    assert [item["path"] for item in payload["items"]] == ["stable-diffusion/checkpoints/illustrious-xl"]
    assert payload["items"][0]["depth"] == 3
    assert payload["items"][0]["children_loaded"] is True

    assert path_response.status_code == 200
    assert "stable-diffusion/checkpoints/illustrious-xl" in {
        item["path"] for item in path_response.json()["items"]
    }

    assert scoped_response.status_code == 200
    assert scoped_response.json()["scope"] == "stable-diffusion/loras"
    assert scoped_response.json()["items"] == []

    assert limited_response.status_code == 200
    assert len(limited_response.json()["items"]) == 1
    assert limited_response.json()["truncated"] is True


def test_api_folder_search_skips_hidden_part_symlink_and_hitomi_leaf(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, config_root = app_modules
    (data_root / ".hidden" / "needle-hidden").mkdir(parents=True)
    (data_root / "visible.part" / "needle-part").mkdir(parents=True)
    (data_root / "normal" / "needle-visible").mkdir(parents=True)
    (data_root / "link").symlink_to(config_root, target_is_directory=True)
    archive = data_root / "hitomi" / "123-gallery"
    (archive / "needle-page-folder").mkdir(parents=True)
    (archive / "_hitomi_metadata.json").write_text("{}", encoding="utf-8")

    client = TestClient(main.app)
    response = client.get(
        "/api/folders/search",
        params={"q": "needle", "limit": 20},
        auth=("admin", "test-password-that-is-long"),
    )
    symlink_scope_response = client.get(
        "/api/folders/search",
        params={"q": "anything", "scope": "link"},
        auth=("admin", "test-password-that-is-long"),
    )
    hidden_scope_response = client.get(
        "/api/folders/search",
        params={"q": "needle", "scope": ".hidden"},
        auth=("admin", "test-password-that-is-long"),
    )
    part_scope_response = client.get(
        "/api/folders/search",
        params={"q": "needle", "scope": "visible.part"},
        auth=("admin", "test-password-that-is-long"),
    )

    assert response.status_code == 200
    paths = {item["path"] for item in response.json()["items"]}
    assert paths == {"normal/needle-visible"}
    assert symlink_scope_response.status_code == 400
    assert hidden_scope_response.status_code == 400
    assert part_scope_response.status_code == 400


def test_api_folder_children_treats_hitomi_archives_as_leaf_without_child_scan(
    app_modules: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    hitomi_root = data_root / "hitomi"
    archive = hitomi_root / "123-gallery"
    listing_archive = hitomi_root / "listings" / "artist-page"
    archive.mkdir(parents=True)
    listing_archive.mkdir(parents=True)
    (archive / "_hitomi_metadata.json").write_text("{}", encoding="utf-8")
    (archive / "001.jpg").write_bytes(b"image")
    (listing_archive / "_hitomi_listing_metadata.json").write_text("{}", encoding="utf-8")

    original_has_child_directories = main.folder_has_child_directories
    original_direct_child_directories = main.direct_child_directories

    def fail_on_archive_child_probe(path: Path) -> bool:
        if path.resolve() in {archive.resolve(), listing_archive.resolve()}:
            raise AssertionError("Hitomi archive folders should not be child-probed")
        return original_has_child_directories(path)

    def fail_on_archive_direct_scan(path: Path) -> list[Path]:
        if path.resolve() == archive.resolve():
            raise AssertionError("Hitomi archive folders should not be expanded")
        return original_direct_child_directories(path)

    monkeypatch.setattr(main, "folder_has_child_directories", fail_on_archive_child_probe)
    monkeypatch.setattr(main, "direct_child_directories", fail_on_archive_direct_scan)

    client = TestClient(main.app)
    root_response = client.get(
        "/api/folders/children",
        params={"path": "hitomi"},
        auth=("admin", "test-password-that-is-long"),
    )
    listing_response = client.get(
        "/api/folders/children",
        params={"path": "hitomi/listings"},
        auth=("admin", "test-password-that-is-long"),
    )
    archive_response = client.get(
        "/api/folders/children",
        params={"path": "hitomi/123-gallery"},
        auth=("admin", "test-password-that-is-long"),
    )

    assert root_response.status_code == 200
    root_items = {item["path"]: item for item in root_response.json()["items"]}
    assert root_items["hitomi/123-gallery"]["has_children"] is False
    assert root_items["hitomi/123-gallery"]["children_loaded"] is True
    assert root_items["hitomi/listings"]["has_children"] is True

    assert listing_response.status_code == 200
    listing_item = listing_response.json()["items"][0]
    assert listing_item["path"] == "hitomi/listings/artist-page"
    assert listing_item["has_children"] is False
    assert listing_item["children_loaded"] is True

    assert archive_response.status_code == 200
    assert archive_response.json()["items"] == []


def test_api_folder_children_skips_symlinks_and_rejects_symlink_parent(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, config_root = app_modules
    parent = data_root / "folder"
    parent.mkdir()
    (parent / "real").mkdir()
    internal_target = data_root / "internal-target"
    internal_target.mkdir()
    (parent / "internal-link").symlink_to(internal_target, target_is_directory=True)
    (parent / "external-link").symlink_to(config_root, target_is_directory=True)

    client = TestClient(main.app)
    response = client.get(
        "/api/folders/children",
        params={"path": "folder"},
        auth=("admin", "test-password-that-is-long"),
    )
    internal_parent_response = client.get(
        "/api/folders/children",
        params={"path": "folder/internal-link"},
        auth=("admin", "test-password-that-is-long"),
    )
    external_parent_response = client.get(
        "/api/folders/children",
        params={"path": "folder/external-link"},
        auth=("admin", "test-password-that-is-long"),
    )

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["items"]] == ["real"]
    assert internal_parent_response.status_code == 400
    assert external_parent_response.status_code == 400


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

    main.scan_library_index_batch(max_paths=100, reset=True)
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

    rows = [
        row
        for row in main.library_items(mode="live")
        if row.get("target_path") == "gallery-dl/xhamster3.com/failed"
    ]

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

    main.scan_library_index_batch(max_paths=100, reset=True)
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


def test_audio_archive_uses_folder_cover_for_card_and_media_items(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "asmr.one" / "RJ123456 - Sample"
    target.mkdir(parents=True)
    (target / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0cover")
    (target / "01_sample.mp3").write_bytes(b"audio")
    (target / "_archive_metadata.json").write_text(
        json.dumps(
            {
                "source": "asmrone",
                "title": "Sample ASMR",
                "model_category": "ASMR.one Work",
                "file_format": "audio",
            }
        ),
        encoding="utf-8",
    )

    row = main.library_item_for_path(target, set())
    response = main.api_media_list(path="asmr.one/RJ123456 - Sample", _="_")
    payload = json.loads(response.body.decode("utf-8"))
    audio_item = next(item for item in payload["items"] if item["type"] == "audio")

    assert "cover.jpg" in row["thumbnail_url"]
    assert "&v=" in row["thumbnail_url"]
    assert payload["cover_url"].endswith("cover.jpg")
    assert audio_item["thumbnail_url"].endswith("cover.jpg")
    assert audio_item["cover_url"].endswith("cover.jpg")


def test_text_and_markdown_files_are_readable_media_cards(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "asmr.one" / "RJ123456 - Notes"
    target.mkdir(parents=True)
    text_file = target / "00_readme_ろまあぽ.txt"
    markdown_file = target / "notes.md"
    text_file.write_text("こんにちは\n<script>alert(1)</script>", encoding="utf-8")
    markdown_file.write_text("# Title\n\n**bold**", encoding="utf-8")

    row = main.library_item_for_path(target, set())
    media_response = main.api_media_list(path="asmr.one/RJ123456 - Notes", _="_")
    media_payload = json.loads(media_response.body.decode("utf-8"))
    text_response = main.api_media_text(path="asmr.one/RJ123456 - Notes/00_readme_ろまあぽ.txt", _="_")
    text_payload = json.loads(text_response.body.decode("utf-8"))

    document_items = [item for item in media_payload["items"] if item["type"] == "document"]
    assert row["has_media"] is True
    assert row["model_category"] == "Document Archive"
    assert row["file_format"] == "text"
    assert len(document_items) == 2
    assert document_items[0]["text_url"].startswith("/api/media/text?path=asmr.one/RJ123456%20-%20Notes/")
    by_name = {item["name"]: item for item in document_items}
    assert by_name["notes.md"]["mime_type"].startswith("text/markdown")
    assert text_payload["text"] == "こんにちは\n<script>alert(1)</script>"
    assert text_payload["encoding"] == "utf-8-sig"
    assert text_payload["truncated"] is False


def test_media_text_endpoint_limits_large_documents(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    note = data_root / "notes.txt"
    note.write_text("x" * (main.DOCUMENT_TEXT_MAX_BYTES + 32), encoding="utf-8")

    response = main.api_media_text(path="notes.txt", _="_")
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["truncated"] is True
    assert payload["read_bytes"] == main.DOCUMENT_TEXT_MAX_BYTES
    assert len(payload["text"]) == main.DOCUMENT_TEXT_MAX_BYTES


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
