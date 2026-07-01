from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

from app import downloader
from app.models import ParsedDownload
from app.parsers import parse_input


class FakeDb:
    def __init__(self, job: dict, listed_jobs: list[dict] | None = None) -> None:
        self.job = job
        self.listed_jobs = listed_jobs if listed_jobs is not None else []
        self.created_jobs: list[ParsedDownload] = []
        self.logs: list[str] = []
        self.next_job_id = 500

    def get_job(self, job_id: int) -> dict:
        self.job["id"] = job_id
        return dict(self.job)

    def update_job(self, job_id: int, **fields: object) -> None:
        self.job["id"] = job_id
        self.job.update(fields)

    def append_log(self, job_id: int, message: str) -> None:
        self.logs.append(message)

    def list_jobs(self, limit: int = 100) -> list[dict]:
        return self.listed_jobs[:limit]

    def create_job(self, parsed: ParsedDownload) -> int:
        self.created_jobs.append(parsed)
        self.next_job_id += 1
        return self.next_job_id

    def get_setting(self, name: str) -> str | None:
        return None

    def get_secret(self, name: str) -> str | None:
        return None


def test_hitomi_artist_language_listing_url_parses_as_hitomi_listing() -> None:
    url = "https://hitomi.la/artist/sample-artist-english-1.html"

    parsed = parse_input(url, target_subdir="archive/hitomi")

    assert parsed.source == "hitomi"
    assert parsed.raw_input == url
    assert parsed.target_subdir == "archive/hitomi"
    assert parsed.hitomi_listing_url == url
    assert parsed.hitomi_listing_kind == "artist"
    assert parsed.hitomi_gallery_id is None
    assert parsed.hitomi_gallery_url is None


def test_hitomi_search_url_parses_as_hitomi_listing() -> None:
    url = "https://hitomi.la/search.html?tag%3Afull_color%20language%3Aenglish"

    parsed = parse_input(url)

    assert parsed.source == "hitomi"
    assert parsed.hitomi_listing_url == url
    assert parsed.hitomi_listing_kind == "search"
    assert parsed.hitomi_gallery_id is None


def test_hitomi_gallery_url_still_parses_as_single_gallery() -> None:
    url = "https://hitomi.la/galleries/123456.html"

    parsed = parse_input(url)

    assert parsed.source == "hitomi"
    assert parsed.hitomi_gallery_id == "123456"
    assert parsed.hitomi_gallery_url == url
    assert parsed.hitomi_listing_url is None


def test_hitomi_listing_download_queues_discovered_gallery_jobs() -> None:
    url = "https://hitomi.la/artist/sample-artist-english-1.html"
    parsed = ParsedDownload(
        source="hitomi",
        raw_input=url,
        target_subdir="custom/hitomi",
        hitomi_listing_url=url,
        hitomi_listing_kind="artist",
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "data"
        fake_db = FakeDb({"status": "running", "source": "hitomi"})

        with (
            mock.patch.object(downloader, "DATA_ROOT", root),
            mock.patch.object(downloader, "db", fake_db),
            mock.patch.object(downloader, "gallery_dl_available", return_value=True),
            mock.patch.object(downloader, "gallery_dl_version", return_value="1.32.5"),
            mock.patch.object(
                downloader,
                "discover_hitomi_listing_gallery_urls",
                return_value=[
                    "https://hitomi.la/galleries/111.html",
                    "https://hitomi.la/reader/222.html",
                    "https://hitomi.la/galleries/111.html",
                ],
            ),
            mock.patch.object(downloader, "enqueue_job") as enqueue_job,
        ):
            downloader.download_hitomi(77, parsed)

        assert len(fake_db.created_jobs) == 2
        assert [child.hitomi_gallery_id for child in fake_db.created_jobs] == ["111", "222"]
        assert [child.target_subdir for child in fake_db.created_jobs] == ["custom/hitomi", "custom/hitomi"]
        assert [call.args[0] for call in enqueue_job.call_args_list] == [501, 502]
        assert fake_db.job["filename"] == "2 queued / 2 discovered"
        assert fake_db.job["precision"] == "2 queued, 0 skipped"
        metadata_path = Path(str(fake_db.job["target_dir"])) / "_hitomi_listing_metadata.json"
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert payload["source_url"] == url
        assert payload["queued_count"] == 2
        assert payload["discovered_count"] == 2


def test_hitomi_listing_download_skips_existing_gallery_jobs() -> None:
    url = "https://hitomi.la/artist/sample-artist-english-1.html"
    existing_queued = ParsedDownload(
        source="hitomi",
        raw_input="https://hitomi.la/galleries/111.html",
        hitomi_gallery_id="111",
        hitomi_gallery_url="https://hitomi.la/galleries/111.html",
    )
    existing_done = ParsedDownload(
        source="hitomi",
        raw_input="https://hitomi.la/galleries/222.html",
        hitomi_gallery_id="222",
        hitomi_gallery_url="https://hitomi.la/galleries/222.html",
    )
    parsed = ParsedDownload(
        source="hitomi",
        raw_input=url,
        hitomi_listing_url=url,
        hitomi_listing_kind="artist",
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "data"
        done_target = root / "hitomi" / "222-title"
        done_target.mkdir(parents=True)
        fake_db = FakeDb(
            {"status": "running", "source": "hitomi"},
            listed_jobs=[
                {
                    "id": 44,
                    "source": "hitomi",
                    "status": "queued",
                    "parsed_json": json.dumps(existing_queued.to_dict()),
                    "metadata_json": "",
                    "target_dir": "",
                },
                {
                    "id": 45,
                    "source": "hitomi",
                    "status": "done",
                    "parsed_json": json.dumps(existing_done.to_dict()),
                    "metadata_json": "",
                    "target_dir": str(done_target),
                },
            ],
        )

        with (
            mock.patch.object(downloader, "DATA_ROOT", root),
            mock.patch.object(downloader, "db", fake_db),
            mock.patch.object(downloader, "gallery_dl_available", return_value=True),
            mock.patch.object(downloader, "gallery_dl_version", return_value="1.32.5"),
            mock.patch.object(
                downloader,
                "discover_hitomi_listing_gallery_urls",
                return_value=[
                    "https://hitomi.la/galleries/111.html",
                    "https://hitomi.la/galleries/222.html",
                    "https://hitomi.la/galleries/333.html",
                ],
            ),
            mock.patch.object(downloader, "enqueue_job") as enqueue_job,
        ):
            downloader.download_hitomi(78, parsed)

        assert len(fake_db.created_jobs) == 1
        assert fake_db.created_jobs[0].hitomi_gallery_id == "333"
        enqueue_job.assert_called_once_with(501)
        assert fake_db.job["filename"] == "1 queued / 3 discovered"
        assert fake_db.job["precision"] == "1 queued, 2 skipped"
        payload = json.loads((Path(str(fake_db.job["target_dir"])) / "_hitomi_listing_metadata.json").read_text())
        statuses = {entry["gallery_id"]: entry["status"] for entry in payload["galleries"]}
        assert statuses == {"111": "already_queued", "222": "present", "333": "queued"}
