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


def test_asmrone_audio_files_are_media_items() -> None:
    assert main.is_media_file(Path("sample.mp3"))
    assert main.media_kind(Path("sample.mp3")) == "audio"
