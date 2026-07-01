from __future__ import annotations

import importlib
import json
import zlib
from pathlib import Path

import pytest
from fastapi import HTTPException

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


def test_settings_status_never_returns_secret_values(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _utils, db, _downloader, _main, _data_root, _config_root = app_modules
    monkeypatch.setenv("CIVITAI_TOKEN", "env-secret")
    db.set_setting("HF_TOKEN", "db-secret")
    db.set_setting("GALLERY_DL_USERNAME", "login-name")
    db.set_setting("GALLERY_DL_EXTRA_OPTIONS", "extractor.example.api-key=secret")
    db.set_setting("YT_DLP_COOKIES_FILE", "/config/yt-dlp/cookies.txt")
    db.set_setting("YT_DLP_EXTRA_OPTIONS", "cmdline-args=--cookies /tmp/private.txt")

    status = db.settings_status()

    for key in (
        "HF_TOKEN",
        "CIVITAI_TOKEN",
        "GALLERY_DL_USERNAME",
        "GALLERY_DL_PASSWORD",
        "GALLERY_DL_COOKIES_FILE",
        "GALLERY_DL_COOKIES_FROM_BROWSER",
        "GALLERY_DL_EXTRA_OPTIONS",
        "YT_DLP_COOKIES_FILE",
        "YT_DLP_COOKIES_FROM_BROWSER",
        "YT_DLP_EXTRA_OPTIONS",
    ):
        assert status[key]["value"] == ""
    assert status["HF_TOKEN"]["configured"] is True
    assert status["CIVITAI_TOKEN"]["configured"] is True
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
