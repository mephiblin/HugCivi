from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import pytest

from app.models import ParsedDownload


class JsonRequest:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    async def json(self) -> object:
        return self.payload


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


def test_media_list_includes_civitai_model_generation_metadata(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "civitai" / "loras" / "sdxl" / "example-model" / "version_456"
    target.mkdir(parents=True)
    (target / "example.safetensors").write_bytes(b"model")
    (target / "civitai_example_999.jpeg").write_bytes(b"image")
    (target / "_civitai_metadata.json").write_text(
        json.dumps(
            {
                "source": "civitai",
                "raw_input": "https://civitai.com/models/123?modelVersionId=456",
                "model_id": "123",
                "version_id": "456",
                "archive_info": {
                    "model_title": "Example Model",
                    "model_category": "LoRA",
                    "thumbnail_url": "/api/fs/preview?path=civitai/loras/sdxl/example-model/version_456/civitai_example_999.jpeg",
                },
            }
        ),
        encoding="utf-8",
    )
    (target / "_civitai_generation_metadata.json").write_text(
        json.dumps(
            {
                "source": "civitai",
                "kind": "civitai_model_generation_metadata",
                "model_id": "123",
                "version_id": "456",
                "model_name": "Example Model",
                "model_page_url": "https://civitai.com/models/123?modelVersionId=456",
                "image_count": 1,
                "generation_count": 1,
                "component_downloads": [
                    {
                        "role": "primary",
                        "name": "example.safetensors",
                        "type": "Model",
                        "filename": "example.safetensors",
                        "local_file": "example.safetensors",
                        "required": True,
                        "status": "downloaded",
                    }
                ],
                "model_details": {
                    "model": {
                        "id": "123",
                        "name": "Example Model",
                        "type": "LORA",
                        "creator": "Creator",
                        "description": "Main body\nUse with trigger.",
                        "tags": ["style", "anime"],
                        "stats": {"downloadCount": 123, "thumbsUpCount": 45},
                    },
                    "version": {
                        "id": "456",
                        "name": "v1",
                        "base_model": "SDXL",
                        "base_model_type": "Standard",
                        "status": "Published",
                        "published_at": "2026-01-27T19:01:33.766Z",
                        "description": "Version notes",
                        "trained_words": ["trigger", "style token"],
                        "files": [
                            {
                                "name": "example.safetensors",
                                "type": "Model",
                                "format": "SafeTensor",
                                "fp": "bf16",
                                "is_required": True,
                                "hashes": {"AutoV2": "ABC123"},
                            }
                        ],
                    },
                },
                "images": [
                    {
                        "source_url": "https://civitai.com/images/999",
                        "local_file": "civitai_example_999.jpeg",
                        "image": {"id": "999", "width": 1024, "height": 1024},
                        "generation_data": {
                            "available": True,
                            "prompt": {"text": "model prompt"},
                            "negative_prompt": {"text": "bad anatomy"},
                            "metadata": [{"label": "Seed", "value": "12345"}],
                            "resources": [],
                            "model_version_ids": ["456"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert main.archive_metadata_path(target).name == "_civitai_metadata.json"

    payload = response_json(main.api_media_list("civitai/loras/sdxl/example-model/version_456", "_"))

    assert payload["ok"] is True
    assert payload["metadata"]["kind"] == "civitai_model_generation_metadata"
    assert payload["metadata"]["source_url"] == "https://civitai.com/images/999"
    assert payload["metadata"]["model_page_url"] == "https://civitai.com/models/123?modelVersionId=456"
    assert payload["metadata"]["model_details"]["model"]["description"] == "Main body\nUse with trigger."
    assert payload["metadata"]["model_details"]["model"]["tags"] == ["style", "anime"]
    assert payload["metadata"]["model_details"]["model"]["stats"]["downloadCount"] == 123
    assert payload["metadata"]["model_details"]["version"]["status"] == "Published"
    assert payload["metadata"]["model_details"]["version"]["base_model_type"] == "Standard"
    assert payload["metadata"]["model_details"]["version"]["trained_words"] == ["trigger", "style token"]
    assert payload["metadata"]["model_details"]["version"]["files"][0]["hashes"]["AutoV2"] == "ABC123"
    assert payload["metadata"]["component_downloads"][0]["local_file"] == "example.safetensors"
    assert payload["metadata"]["generation_data"]["prompt"]["text"] == "model prompt"
    assert payload["metadata"]["generation_data"]["metadata"][0]["value"] == "12345"
    assert payload["items"][0]["name"] == "civitai_example_999.jpeg"
    assert payload["items"][0]["thumbnail_url"].endswith("civitai_example_999.jpeg")

    rows = [row for row in main.library_items(mode="live") if row.get("target_path") == "civitai/loras/sdxl/example-model/version_456"]
    assert len(rows) == 1
    assert rows[0]["source"] == "civitai"
    assert rows[0]["model_category"] == "LoRA"
    assert rows[0]["thumbnail_url"].endswith("civitai_example_999.jpeg")
    assert rows[0]["has_media"] is True
    assert rows[0]["media_count"] == 1


def test_civitai_model_component_health_checks_local_files(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "stable-diffusion" / "checkpoints" / "zimagebase" / "z-image-base" / "version_2635223"
    target.mkdir(parents=True)
    (target / "z_image_bf16.safetensors").write_bytes(b"model")
    (target / "qwen_3_4b_fp8_mixed.safetensors").write_bytes(b"text encoder")
    (target / "ae.safetensors").write_bytes(b"vae")

    payload = response_json(
        asyncio.run(
            main.api_civitai_resource_health(
                JsonRequest(
                    {
                        "path": "stable-diffusion/checkpoints/zimagebase/z-image-base/version_2635223",
                        "components": [
                            {
                                "key": "component:0:z_image_bf16.safetensors",
                                "role": "primary",
                                "name": "z_image_bf16.safetensors",
                                "filename": "z_image_bf16.safetensors",
                                "local_file": "z_image_bf16.safetensors",
                                "type": "Model",
                                "required": True,
                            },
                            {
                                "key": "component:1:ae.safetensors",
                                "role": "required_component",
                                "name": "ae.safetensors",
                                "filename": "ae.safetensors",
                                "local_file": "ae.safetensors",
                                "type": "VAE",
                                "required": True,
                            },
                            {
                                "key": "component:2:qwen_3_4b_fp8_mixed.safetensors",
                                "role": "required_component",
                                "name": "qwen_3_4b_fp8_mixed.safetensors",
                                "filename": "qwen_3_4b_fp8_mixed.safetensors",
                                "local_file": "qwen_3_4b_fp8_mixed.safetensors",
                                "type": "Text Encoder",
                                "required": True,
                            },
                            {
                                "key": "component:3:missing.safetensors",
                                "role": "required_component",
                                "name": "missing.safetensors",
                                "filename": "missing.safetensors",
                                "local_file": "missing.safetensors",
                                "type": "Model",
                                "required": True,
                            },
                        ],
                    }
                ),
                "_",
            )
        )
    )

    components = {item["key"]: item for item in payload["components"]}
    assert payload["ok"] is True
    assert components["component:0:z_image_bf16.safetensors"]["present"] is True
    assert components["component:1:ae.safetensors"]["present"] is True
    assert components["component:2:qwen_3_4b_fp8_mixed.safetensors"]["present"] is True
    assert components["component:3:missing.safetensors"]["present"] is False
    assert components["component:1:ae.safetensors"]["target_path"].endswith("ae.safetensors")


def test_civitai_model_job_card_enables_media_viewer_from_local_thumbnail(app_modules: tuple) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "civitai" / "loras" / "zimagebase" / "hands" / "version_3012596"
    target.mkdir(parents=True)
    (target / "Hands_zib_v1.safetensors").write_bytes(b"model")
    (target / "civitai_example_100.jpeg").write_bytes(b"image")
    parsed = ParsedDownload(
        source="civitai",
        raw_input="https://civitai.com/models/200255/hands-xl-sd-15-f1d-pony-illustrious-zit-zib",
        civitai_model_id="200255",
        civitai_version_id="3012596",
    )
    job_id = db.create_job(parsed)
    db.update_job(
        job_id,
        status="done",
        target_dir=str(target),
        model_title="Hands XL + SD 1.5 + F1D + Pony + Illustrious + zit + ZIB",
        model_category="LoRA",
    )

    row = main.decorate_jobs(db.list_jobs())[0]

    assert row["thumbnail_url"].endswith("civitai_example_100.jpeg")
    assert row["has_media"] is True
    assert row["media_count"] == 1
    assert row["media_type"] == "image"


def test_media_list_falls_back_to_civitai_model_metadata_sidecar(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "civitai" / "loras" / "zimagebase" / "hands" / "version_3012596"
    target.mkdir(parents=True)
    (target / "Hands_zib_v1.safetensors").write_bytes(b"model")
    (target / "civitai_example_100.jpeg").write_bytes(b"image")
    (target / "_civitai_metadata.json").write_text(
        json.dumps(
            {
                "source": "civitai",
                "raw_input": "https://civitai.com/models/200255/hands-xl-sd-15-f1d-pony-illustrious-zit-zib",
                "model_id": "200255",
                "version_id": "3012596",
                "model_name": "Hands XL + SD 1.5 + F1D + Pony + Illustrious + zit + ZIB",
                "model_page_metadata": {
                    "id": 200255,
                    "name": "Hands XL + SD 1.5 + F1D + Pony + Illustrious + zit + ZIB",
                    "type": "LORA",
                    "description": "<p>Use hands reference prompts.</p>",
                    "tags": ["hands", "pose"],
                    "stats": {"downloadCount": 99},
                },
                "metadata": {
                    "id": 3012596,
                    "name": "v1",
                    "baseModel": "Pony",
                    "status": "Published",
                    "trainedWords": ["hands"],
                    "files": [
                        {
                            "id": 77,
                            "name": "Hands_zib_v1.safetensors",
                            "type": "Model",
                            "primary": True,
                            "metadata": {"format": "SafeTensor", "fp": "fp16"},
                            "hashes": {"AutoV2": "ABC123"},
                        }
                    ],
                },
                "archive_info": {"model_title": "Hands XL", "model_category": "LoRA"},
            }
        ),
        encoding="utf-8",
    )

    payload = response_json(main.api_media_list("civitai/loras/zimagebase/hands/version_3012596", "_"))

    assert payload["ok"] is True
    assert payload["items"][0]["name"] == "civitai_example_100.jpeg"
    assert payload["metadata"]["kind"] == "civitai_model_generation_metadata"
    assert payload["metadata"]["model_page_url"] == "https://civitai.com/models/200255?modelVersionId=3012596"
    assert payload["metadata"]["model_details"]["model"]["description"] == "Use hands reference prompts."
    assert payload["metadata"]["model_details"]["model"]["type"] == "LORA"
    assert payload["metadata"]["model_details"]["version"]["files"][0]["hashes"]["AutoV2"] == "ABC123"


def test_civitai_image_cards_are_media_archives_before_library_scan(app_modules: tuple) -> None:
    _utils, _db, _downloader, main, _data_root, _config_root = app_modules
    template = (main.BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert "function isCivitaiImagePageJob(job)" in template
    assert "if (isCivitaiImagePageJob(job)) return true;" in template
    assert "value === 'civitai image page'" in template
    assert "civitai_model_generation_metadata" in template
    assert "function renderCivitaiModelDetails(details)" in template
    assert "function dedupeGenerationResources(resources)" in template
    assert "Model page body" in template
    assert "Model stats" in template
    assert "Published" in template
    assert "function civitaiStatsText" in template
    assert "function civitaiHashText" in template
    assert "Trigger words" in template
    assert "function openMediaViewerForCard(card)" in template
    assert "if (!isMediaArchiveCard(card)" in template
    assert ".asset-card[data-media-archive=\"true\"]" in template
    assert 'data-action="civitai-refresh"' in template
    assert "data-civitai-refresh=" in template
    assert "async function refreshCivitaiArchive(target)" in template
    assert 'data-civitai-resource-transfer=' in template
    assert 'data-civitai-resources-only' in template
    assert "componentDownloads" in template
    assert "Downloaded components" in template
    assert "Check components" in template
    assert "modelComponentResources(mediaArchiveMetadata)" in template


def test_civitai_refresh_api_queues_existing_model_folder(app_modules: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    _utils, db, _downloader, main, data_root, _config_root = app_modules
    target = data_root / "civitai" / "loras" / "sdxl" / "old-model" / "version_456"
    target.mkdir(parents=True)
    (target / "old.safetensors").write_bytes(b"model")
    (target / "_civitai_metadata.json").write_text(
        json.dumps(
            {
                "source": "civitai",
                "raw_input": "https://civitai.com/models/123?modelVersionId=456",
                "model_id": "123",
                "version_id": "456",
                "file_selector": {"format": "SafeTensor", "primary": True},
                "archive_info": {"model_title": "Old Model", "model_category": "LoRA"},
            }
        ),
        encoding="utf-8",
    )
    enqueued: list[int] = []
    monkeypatch.setattr(main, "enqueue_job", lambda job_id: enqueued.append(job_id))

    payload = response_json(
        asyncio.run(main.api_civitai_refresh(JsonRequest({"path": "civitai/loras/sdxl/old-model/version_456"}), "_"))
    )

    assert payload["ok"] is True
    job_id = payload["job_id"]
    assert enqueued == [job_id]
    job = db.get_job(job_id)
    assert job is not None
    assert job["status"] == "queued"
    assert job["target_dir"] == str(target)
    parsed = ParsedDownload.from_dict(json.loads(job["parsed_json"]))
    assert parsed.source == "civitai"
    assert parsed.civitai_model_id == "123"
    assert parsed.civitai_version_id == "456"
    assert parsed.civitai_file_format == "SafeTensor"
    assert parsed.civitai_file_primary is True
    assert parsed.civitai_refresh is True
    assert parsed.target_subdir == "civitai/loras/sdxl/old-model/version_456"


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
