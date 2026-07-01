from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


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

    import app.db as db
    import app.downloader as downloader
    import app.main as main
    import app.utils as utils

    importlib.reload(utils)
    importlib.reload(db)
    importlib.reload(downloader)
    importlib.reload(main)
    db.init_db()
    return db, main


def test_bulk_input_lines_ignores_empty_comments_and_normalizes_list_prefixes(app_modules: tuple) -> None:
    _db, main = app_modules

    assert main.bulk_input_lines(
        """
        # later
        - https://example.com/a.bin
        2. https://example.com/b.bin
        * hitomi 123456
        """
    ) == [
        (3, "https://example.com/a.bin"),
        (4, "https://example.com/b.bin"),
        (5, "hitomi 123456"),
    ]


def test_bulk_add_jobs_queues_valid_lines_and_reports_parse_failures(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, main = app_modules
    enqueued: list[int] = []
    monkeypatch.setattr(main, "enqueue_job", lambda job_id: enqueued.append(job_id))

    response = main.add_jobs_bulk(
        input_text="\n".join(
            [
                "https://example.com/a.bin",
                "not a url",
                "https://example.com/b.bin",
            ]
        ),
        target_subdir="bulk/target",
    )
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["submitted_count"] == 3
    assert payload["created_count"] == 2
    assert payload["failed_count"] == 1
    assert payload["failed"][0]["line"] == 2
    assert payload["failed"][0]["input"] == "not a url"
    assert enqueued == [payload["created"][0]["job_id"], payload["created"][1]["job_id"]]

    jobs = db.list_jobs()
    assert len(jobs) == 2
    parsed_payloads = [json.loads(job["parsed_json"]) for job in jobs]
    assert {parsed["target_subdir"] for parsed in parsed_payloads} == {"bulk/target"}
    assert {job["status"] for job in jobs} == {"queued"}
