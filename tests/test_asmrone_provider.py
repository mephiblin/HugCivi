from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import requests

from app import downloader
from app import main
from app.main import source_url_for_job
from app.models import ParsedDownload
from app.parsers import parse_input


class FakeDb:
    def __init__(self) -> None:
        self.job = {"id": 77, "status": "running", "metadata_json": ""}
        self.logs: list[str] = []

    def get_job(self, job_id: int) -> dict:
        self.job["id"] = job_id
        return dict(self.job)

    def update_job(self, job_id: int, **fields: object) -> None:
        self.job["id"] = job_id
        self.job.update(fields)

    def append_log(self, job_id: int, message: str) -> None:
        self.logs.append(message)


def test_asmrone_work_url_routes_to_asmrone_source() -> None:
    parsed = parse_input("https://asmr.one/work/RJ361902", target_subdir="audio/asmr")

    assert parsed.source == "asmrone"
    assert parsed.target_subdir == "audio/asmr"
    assert parsed.asmrone_work_id == "361902"
    assert parsed.asmrone_source_id == "RJ361902"
    assert parsed.asmrone_url == "https://asmr.one/work/RJ361902"


def test_asmrone_numeric_work_url_keeps_nested_source_id() -> None:
    parsed = parse_input("https://asmr.one/work/361902/DLSITE/RJ361902")

    assert parsed.source == "asmrone"
    assert parsed.asmrone_work_id == "361902"
    assert parsed.asmrone_source_id == "RJ361902"


def test_source_url_for_asmrone_job_uses_work_url() -> None:
    parsed = ParsedDownload(
        source="asmrone",
        raw_input="https://asmr.one/work/RJ361902",
        asmrone_url="https://asmr.one/work/RJ361902",
        asmrone_work_id="361902",
        asmrone_source_id="RJ361902",
    )

    assert source_url_for_job({}, parsed) == "https://asmr.one/work/RJ361902"


def test_asmrone_downloads_media_download_url_not_stream_url() -> None:
    parsed = ParsedDownload(
        source="asmrone",
        raw_input="https://asmr.one/work/RJ361902",
        asmrone_url="https://asmr.one/work/RJ361902",
        asmrone_work_id="361902",
        asmrone_source_id="RJ361902",
    )
    work = {
        "id": 361902,
        "source_id": "RJ361902",
        "title": "Sample Work",
        "name": "Circle",
        "release": "2021-12-24",
    }
    audio_bytes = b"sample audio"
    tracks = [
        {
            "type": "folder",
            "title": "01_main",
            "children": [
                {
                    "type": "audio",
                    "hash": "361902/1",
                    "title": "01_sample.mp3",
                    "size": len(audio_bytes),
                    "duration": 12.3,
                    "mediaStreamUrl": "https://stream.example/media/stream/RJ361902/01_sample.mp3",
                    "mediaDownloadUrl": "https://download.example/media/download/RJ361902/01_sample.mp3",
                }
            ],
        }
    ]
    head_response = requests.Response()
    head_response.status_code = 200
    head_response.url = "https://download.example/media/download/RJ361902/01_sample.mp3?action=download"
    head_response.headers["content-type"] = "audio/mpeg"
    head_response.headers["content-length"] = str(len(audio_bytes))
    head_response.headers["accept-ranges"] = "bytes"
    head_response.headers["content-disposition"] = "attachment"
    head_response._content = b""
    head_response._content_consumed = True
    get_response = requests.Response()
    get_response.status_code = 200
    get_response.url = "https://download.example/media/download/RJ361902/01_sample.mp3?action=download"
    get_response.headers["content-type"] = "audio/mpeg"
    get_response.headers["content-length"] = str(len(audio_bytes))
    get_response._content = audio_bytes
    get_response._content_consumed = True
    fake_db = FakeDb()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "data"
        with (
            mock.patch.object(downloader, "DATA_ROOT", root),
            mock.patch.object(downloader, "db", fake_db),
            mock.patch.object(downloader, "fetch_json_value", side_effect=[work, tracks]),
            mock.patch.object(downloader, "request_with_safety", side_effect=[head_response, get_response]) as request_with_safety,
        ):
            downloader.download_asmrone(77, parsed)

        target = Path(str(fake_db.job["target_dir"]))
        manifest_text = (target / "_asmrone_manifest.json").read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)
        saved = target / "01_main" / "01_sample.mp3"
        saved_bytes = saved.read_bytes()
        archive_metadata = json.loads((target / "_archive_metadata.json").read_text(encoding="utf-8"))

    assert request_with_safety.call_count == 2
    request_methods_and_urls = [(call.args[1], call.args[2]) for call in request_with_safety.call_args_list]
    assert request_methods_and_urls == [
        ("HEAD", "https://download.example/media/download/RJ361902/01_sample.mp3?action=download"),
        ("GET", "https://download.example/media/download/RJ361902/01_sample.mp3?action=download"),
    ]
    assert saved_bytes == audio_bytes
    assert "stream.example" not in manifest_text
    assert manifest["download_url_strategy"] == "Use mediaDownloadUrl with action=download; ignore mediaStreamUrl."
    assert manifest["media_download_enabled"] is True
    assert manifest["downloaded_file_count"] == 1
    assert manifest["files"][0]["download_url_kind"] == "mediaDownloadUrl"
    assert manifest["files"][0]["media_stream_url_ignored"] is True
    assert manifest["files"][0]["download_status"] == "downloaded"
    assert manifest["files"][0]["downloaded_size"] == len(audio_bytes)
    assert manifest["files"][0]["local_path"] == "01_main/01_sample.mp3"
    assert archive_metadata["source"] == "asmrone"
    assert fake_db.job["model_category"] == "ASMR.one Work"
    assert fake_db.job["filename"] == "1 files"


def test_asmrone_keeps_successful_files_when_optional_entry_fails() -> None:
    parsed = ParsedDownload(
        source="asmrone",
        raw_input="https://asmr.one/work/RJ361902",
        asmrone_url="https://asmr.one/work/RJ361902",
        asmrone_work_id="361902",
        asmrone_source_id="RJ361902",
    )
    work = {
        "id": 361902,
        "source_id": "RJ361902",
        "title": "Sample Work",
    }
    tracks = [
        {
            "type": "audio",
            "title": "01_sample.mp3",
            "size": 4,
            "mediaDownloadUrl": "https://download.example/audio.mp3",
        },
        {
            "type": "folder",
            "title": "イラスト",
            "children": [
                {
                    "type": "image",
                    "title": "cover.jpg",
                    "size": 9,
                    "mediaDownloadUrl": "https://download.example/missing-cover.jpg",
                }
            ],
        },
    ]
    fake_db = FakeDb()

    def fake_stream_download(
        _job_id: int,
        _session: object,
        url: str,
        target_dir: Path,
        filename_override: str | None = None,
        **_kwargs: object,
    ) -> Path:
        if "missing-cover" in url:
            target_dir.mkdir(parents=True, exist_ok=True)
            raise requests.ConnectionError("cover unavailable")
        target_dir.mkdir(parents=True, exist_ok=True)
        saved = target_dir / (filename_override or "download.bin")
        saved.write_bytes(b"ok")
        return saved

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "data"
        with (
            mock.patch.object(downloader, "DATA_ROOT", root),
            mock.patch.object(downloader, "db", fake_db),
            mock.patch.object(downloader, "fetch_json_value", side_effect=[work, tracks]),
            mock.patch.object(downloader, "stream_download", side_effect=fake_stream_download),
        ):
            downloader.download_asmrone(77, parsed)

        target = Path(str(fake_db.job["target_dir"]))
        manifest = json.loads((target / "_asmrone_manifest.json").read_text(encoding="utf-8"))
        archive_metadata = json.loads((target / "_archive_metadata.json").read_text(encoding="utf-8"))
        audio_exists = (target / "01_sample.mp3").exists()
        cover_exists = (target / "イラスト" / "cover.jpg").exists()
        empty_failed_folder_exists = (target / "イラスト").exists()

    assert audio_exists
    assert not cover_exists
    assert not empty_failed_folder_exists
    assert manifest["downloaded_file_count"] == 1
    assert manifest["failed_file_count"] == 1
    assert manifest["files"][0]["download_status"] == "downloaded"
    assert manifest["files"][1]["download_status"] == "failed"
    assert "cover unavailable" in manifest["files"][1]["download_error"]
    assert archive_metadata["failed_file_count"] == 1
    assert fake_db.job["filename"] == "1 files"
    assert fake_db.job["precision"] == "1 files, 1 failed"
    assert any("ASMR.one file warning" in message for message in fake_db.logs)


def test_asmrone_manifest_entries_preserve_japanese_paths() -> None:
    tracks = [
        {
            "type": "folder",
            "title": "イラスト",
            "children": [
                {
                    "type": "image",
                    "title": "叔母ちゃん_サムネイルイラスト.jpg",
                    "size": 123,
                    "mediaDownloadUrl": "https://download.example/illustration.jpg",
                }
            ],
        },
        {
            "type": "text",
            "title": "readme_ろまあぽ.txt",
            "size": 45,
            "mediaDownloadUrl": "https://download.example/readme.txt",
        },
    ]

    entries = downloader.asmrone_manifest_entries(tracks, source_id="RJ01589746")

    assert entries[0]["local_path"] == "イラスト/叔母ちゃん_サムネイルイラスト.jpg"
    assert entries[1]["local_path"] == "readme_ろまあぽ.txt"


def test_asmrone_audio_files_are_media_items() -> None:
    assert main.is_media_file(Path("sample.mp3"))
    assert main.media_kind(Path("sample.mp3")) == "audio"
