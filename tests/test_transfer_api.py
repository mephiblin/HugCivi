from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_modules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_root = tmp_path / "data"
    config_root = tmp_path / "config"
    data_root.mkdir()
    config_root.mkdir()

    monkeypatch.setenv("DATA_ROOT", str(data_root))
    monkeypatch.setenv("DB_PATH", str(config_root / "jobs.sqlite3"))
    monkeypatch.setenv("DOWNLOAD_ARCHIVE_DIR", str(config_root / "downloads"))
    monkeypatch.setenv("MEDIA_CACHE_DIR", str(config_root / "media-cache"))
    monkeypatch.setenv("TRANSFER_MANIFEST_DIR", str(config_root / "transfer-manifests"))
    monkeypatch.setenv("APP_PASSWORD", "test-password-that-is-long")

    import app.utils as utils
    import app.db as db
    import app.transfer as transfer
    import app.downloader as downloader
    import app.main as main

    importlib.reload(utils)
    importlib.reload(db)
    importlib.reload(transfer)
    importlib.reload(downloader)
    importlib.reload(main)
    db.init_db()
    return db, main, data_root, config_root


def create_checkpoint_target(db) -> int:
    return db.create_transfer_target(
        name="PC Checkpoints",
        remote_name="pc-comfyui",
        remote_path="ComfyUI/models/checkpoints",
        policy={
            "allowed_source_prefixes": ["stable-diffusion/checkpoints"],
            "include_patterns": ["*.safetensors", "*.ckpt"],
            "bwlimit": "40M",
            "transfers": 1,
            "checkers": 2,
        },
    )


def test_transfer_preflight_and_job_api_create_internal_job(app_modules: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    db, main, data_root, _config_root = app_modules
    create_checkpoint_target(db)
    source = data_root / "stable-diffusion" / "checkpoints" / "Model.safetensors"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"model")
    enqueued: list[int] = []
    monkeypatch.setattr(main.internal_jobs, "enqueue_job", lambda job_id: enqueued.append(job_id))
    client = TestClient(main.app)

    preflight_response = client.post(
        "/api/transfer/preflight",
        json={"target_id": 1, "source_path": "stable-diffusion/checkpoints/Model.safetensors"},
        auth=("admin", "test-password-that-is-long"),
    )

    assert preflight_response.status_code == 200
    preflight = preflight_response.json()
    assert preflight["file_count"] == 1
    assert preflight["source_bytes"] == 5
    assert preflight["destination"] == "pc-comfyui:ComfyUI/models/checkpoints/Model.safetensors"

    job_response = client.post(
        "/api/transfer/jobs",
        json={"target_id": 1, "source_path": "stable-diffusion/checkpoints/Model.safetensors"},
        auth=("admin", "test-password-that-is-long"),
    )

    assert job_response.status_code == 200
    payload = job_response.json()
    assert payload["job"]["job_kind"] == main.INTERNAL_JOB_TRANSFER_COPY
    assert payload["job"]["source"] == "transfer"
    assert enqueued == [payload["job"]["id"]]
    job = db.get_job(payload["job"]["id"])
    assert job is not None
    assert json.loads(job["metadata_json"])["transfer_preflight"]["file_count"] == 1


def test_transfer_target_api_create_list_and_rejects_mode(app_modules: tuple) -> None:
    _db, main, _data_root, _config_root = app_modules
    client = TestClient(main.app)

    create_response = client.post(
        "/api/transfer/targets",
        json={
            "name": "PC Checkpoints",
            "remote_name": "pc-comfyui",
            "remote_path": "ComfyUI/models/checkpoints",
            "policy": {
                "allowed_source_prefixes": ["stable-diffusion/checkpoints"],
                "include_patterns": ["*.safetensors", "*.ckpt"],
            },
        },
        auth=("admin", "test-password-that-is-long"),
    )

    assert create_response.status_code == 200
    target = create_response.json()["target"]
    assert target["remote_name"] == "pc-comfyui"
    assert target["policy"]["allowed_source_prefixes"] == ["stable-diffusion/checkpoints"]

    list_response = client.get("/api/transfer/targets", auth=("admin", "test-password-that-is-long"))
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["targets"]] == [target["id"]]

    mode_response = client.post(
        "/api/transfer/targets",
        json={
            "name": "Bad",
            "remote_name": "pc-comfyui",
            "mode": "sync",
            "policy": {"allowed_source_prefixes": ["stable-diffusion/checkpoints"]},
        },
        auth=("admin", "test-password-that-is-long"),
    )
    assert mode_response.status_code == 400


def test_transfer_api_rejects_non_copy_mode_and_disallowed_source(app_modules: tuple) -> None:
    db, main, data_root, _config_root = app_modules
    create_checkpoint_target(db)
    (data_root / "stable-diffusion" / "loras").mkdir(parents=True)
    (data_root / "stable-diffusion" / "loras" / "Lora.safetensors").write_bytes(b"lora")
    client = TestClient(main.app)

    mode_response = client.post(
        "/api/transfer/jobs",
        json={
            "target_id": 1,
            "source_path": "stable-diffusion/checkpoints/Model.safetensors",
            "mode": "sync",
        },
        auth=("admin", "test-password-that-is-long"),
    )
    assert mode_response.status_code == 400

    raw_remote_response = client.post(
        "/api/transfer/jobs",
        json={
            "target_id": 1,
            "source_path": "stable-diffusion/checkpoints/Model.safetensors",
            "raw_remote": "other:ComfyUI/models",
        },
        auth=("admin", "test-password-that-is-long"),
    )
    assert raw_remote_response.status_code == 400

    policy_override_response = client.post(
        "/api/transfer/preflight",
        json={
            "target_id": 1,
            "source_path": "stable-diffusion/checkpoints/Model.safetensors",
            "policy": {"include_patterns": ["*"]},
        },
        auth=("admin", "test-password-that-is-long"),
    )
    assert policy_override_response.status_code == 400

    source_response = client.post(
        "/api/transfer/preflight",
        json={"target_id": 1, "source_path": "stable-diffusion/loras/Lora.safetensors"},
        auth=("admin", "test-password-that-is-long"),
    )
    assert source_response.status_code == 400
    assert "허용되지 않은" in source_response.json()["detail"]


def test_transfer_api_rejects_root_and_symlink_sources(app_modules: tuple) -> None:
    db, main, data_root, _config_root = app_modules
    create_checkpoint_target(db)
    real_file = data_root / "stable-diffusion" / "checkpoints" / "Real.safetensors"
    real_file.parent.mkdir(parents=True)
    real_file.write_bytes(b"real")
    link = data_root / "stable-diffusion" / "checkpoints" / "Link.safetensors"
    try:
        link.symlink_to(real_file)
    except OSError as exc:
        pytest.skip(f"symlink creation is not available: {exc}")
    client = TestClient(main.app)

    root_response = client.post(
        "/api/transfer/preflight",
        json={"target_id": 1, "source_path": ""},
        auth=("admin", "test-password-that-is-long"),
    )
    assert root_response.status_code == 400

    symlink_response = client.post(
        "/api/transfer/preflight",
        json={"target_id": 1, "source_path": "stable-diffusion/checkpoints/Link.safetensors"},
        auth=("admin", "test-password-that-is-long"),
    )
    assert symlink_response.status_code == 400
    assert "symlink" in symlink_response.json()["detail"]


def test_transfer_handler_builds_copy_command_and_manifest(app_modules: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    db, main, data_root, config_root = app_modules
    create_checkpoint_target(db)
    source = data_root / "stable-diffusion" / "checkpoints" / "Model.ckpt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"checkpoint")
    command_seen: list[list[str]] = []

    def fake_run(job_id: int, command: list[str]) -> None:
        command_seen.append(command)

    monkeypatch.setattr(main, "run_transfer_process", fake_run)
    job_id = db.create_internal_job(
        main.INTERNAL_JOB_TRANSFER_COPY,
        input_text="transfer:stable-diffusion/checkpoints/Model.ckpt",
        payload={
            "target_id": 1,
            "source_path": "stable-diffusion/checkpoints/Model.ckpt",
            "destination_subpath": "",
        },
        source="transfer",
    )

    main.run_transfer_copy_job(job_id, db.get_job(job_id) or {})

    assert command_seen
    assert command_seen[0][1] == "copyto"
    assert command_seen[0][3] == "pc-comfyui:ComfyUI/models/checkpoints/Model.ckpt"
    job = db.get_job(job_id)
    assert job is not None
    assert job["progress_bytes"] == len(b"checkpoint")
    assert (config_root / "transfer-manifests" / f"transfer-{job_id}.json").exists()


def test_transfer_handler_rejects_disabled_target_after_queue(app_modules: tuple) -> None:
    db, main, data_root, _config_root = app_modules
    target_id = create_checkpoint_target(db)
    source = data_root / "stable-diffusion" / "checkpoints" / "Model.ckpt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"checkpoint")
    job_id = db.create_internal_job(
        main.INTERNAL_JOB_TRANSFER_COPY,
        input_text="transfer:stable-diffusion/checkpoints/Model.ckpt",
        payload={
            "target_id": target_id,
            "source_path": "stable-diffusion/checkpoints/Model.ckpt",
        },
        source="transfer",
    )
    assert db.update_transfer_target(target_id, enabled=False)

    with pytest.raises(ValueError, match="disabled"):
        main.run_transfer_copy_job(job_id, db.get_job(job_id) or {})


def test_home_template_declares_transfer_ui_without_mode_payload(app_modules: tuple) -> None:
    _db, main, _data_root, _config_root = app_modules
    template = Path(main.BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'data-settings-pane="settings-transfer"' in template
    assert 'id="transfer-modal"' in template
    assert 'data-action="transfer"' in template
    assert "fetch('/api/transfer/targets')" in template
    assert "fetch('/api/transfer/preflight'" in template
    assert "fetch('/api/transfer/jobs'" in template
    payload_start = template.index("function transferJobPayload")
    payload_end = template.index("function renderTransferPreflight", payload_start)
    payload_block = template[payload_start:payload_end]
    assert "target_id" in payload_block
    assert "source_path" in payload_block
    assert "mode" not in payload_block
