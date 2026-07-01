from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

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


def response_json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def test_media_list_includes_civitai_image_generation_metadata(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "civitai" / "images" / "creator" / "image_135"
    target.mkdir(parents=True)
    (target / "image_135.jpg").write_bytes(b"image")
    (target / "_civitai_image_metadata.json").write_text(
        json.dumps(
            {
                "source": "civitai",
                "kind": "civitai_image_page",
                "source_url": "https://civitai.com/images/135",
                "raw_input": "https://civitai.com/images/135",
                "image": {"id": "135", "width": 800, "height": 1000},
                "generation_data": {
                    "available": True,
                    "prompt": {"text": "best quality"},
                    "negative_prompt": {"text": "low quality"},
                    "resources": [
                        {
                            "name": "Example LoRA",
                            "type": "LORA",
                            "model_id": "123",
                            "model_version_id": "456",
                            "href": "https://civitai.com/models/123",
                        }
                    ],
                    "model_version_ids": ["456"],
                },
                "archive_info": {
                    "model_title": "Civitai image 135",
                    "model_category": "Civitai Image Page",
                    "model_type": "Image",
                },
            }
        ),
        encoding="utf-8",
    )

    assert main.archive_metadata_path(target).name == "_civitai_image_metadata.json"

    payload = response_json(main.api_media_list("civitai/images/creator/image_135", "_"))

    assert payload["ok"] is True
    assert payload["metadata"]["kind"] == "civitai_image_page"
    assert payload["metadata"]["source_url"] == "https://civitai.com/images/135"
    assert payload["metadata"]["generation_data"]["prompt"]["text"] == "best quality"
    assert payload["metadata"]["generation_data"]["resources"][0]["model_version_id"] == "456"
    assert payload["items"][0]["name"] == "image_135.jpg"

    rows = [row for row in main.library_items() if row.get("target_path") == "civitai/images/creator/image_135"]
    assert len(rows) == 1
    assert rows[0]["source"] == "civitai"
    assert rows[0]["model_category"] == "Civitai Image Page"
    assert rows[0]["source_url"] == "https://civitai.com/images/135"
    assert rows[0]["has_media"] is True


def test_media_list_omits_metadata_for_plain_media_folder(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "plain"
    target.mkdir()
    (target / "image.jpg").write_bytes(b"image")

    payload = response_json(main.api_media_list("plain", "_"))

    assert payload["ok"] is True
    assert "metadata" not in payload


def test_civitai_image_source_url_for_job(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    parsed = ParsedDownload(
        source="civitai",
        raw_input="civitai image",
        civitai_image_id="135",
    )

    assert main.source_url_for_job({}, parsed) == "https://civitai.com/images/135"

    parsed.civitai_image_url = "https://civitai.green/images/135?foo=bar"
    assert main.source_url_for_job({}, parsed) == "https://civitai.green/images/135?foo=bar"


def test_civitai_resource_health_detects_done_job_with_model_file(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "stable-diffusion" / "loras" / "example" / "version_456"
    target.mkdir(parents=True)
    (target / "example.safetensors").write_bytes(b"model")
    parsed = ParsedDownload(
        source="civitai",
        raw_input="https://civitai.com/models/123?modelVersionId=456",
        civitai_model_id="123",
        civitai_version_id="456",
    )
    job_id = db.create_job(parsed)
    db.update_job(job_id, status="done", target_dir=str(target))

    rows = main.civitai_resource_health_payload(["456", "999"])

    assert rows[0]["model_version_id"] == "456"
    assert rows[0]["present"] is True
    assert rows[0]["target_path"] == "stable-diffusion/loras/example/version_456"
    assert rows[0]["job_id"] == job_id
    assert rows[1] == {
        "model_version_id": "999",
        "present": False,
        "target_path": "",
        "job_id": None,
    }


def test_civitai_resource_health_detects_sidecar_and_requires_model_file(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    present = data_root / "stable-diffusion" / "checkpoints" / "example" / "version_777"
    present.mkdir(parents=True)
    (present / "checkpoint.safetensors").write_bytes(b"model")
    (present / "_civitai_metadata.json").write_text(
        json.dumps({"source": "civitai", "version_id": "777", "metadata": {"id": 777}}),
        encoding="utf-8",
    )
    missing_file = data_root / "stable-diffusion" / "vae" / "example" / "version_888"
    missing_file.mkdir(parents=True)
    (missing_file / "_civitai_metadata.json").write_text(
        json.dumps({"source": "civitai", "version_id": "888", "metadata": {"id": 888}}),
        encoding="utf-8",
    )

    rows = main.civitai_resource_health_payload(["777", "888"])

    assert rows[0]["present"] is True
    assert rows[0]["target_path"] == "stable-diffusion/checkpoints/example/version_777"
    assert rows[0]["job_id"] is None
    assert rows[1]["present"] is False
