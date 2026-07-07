from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_modules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_root = tmp_path / "data"
    data_remote_root = tmp_path / "data_remote"
    config_root = tmp_path / "config"
    data_root.mkdir()
    data_remote_root.mkdir()
    config_root.mkdir()

    monkeypatch.setenv("DATA_ROOT", str(data_root))
    monkeypatch.setenv("DATA_REMOTE_DIR", str(data_remote_root))
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


def create_receiver_target(db) -> int:
    return db.create_transfer_target(
        name="PC Receiver",
        kind="receiver",
        receiver_url="http://receiver.local:8088",
        receiver_token="receiver-secret",
        remote_path="checkpoints",
        policy={
            "allowed_source_prefixes": ["stable-diffusion/checkpoints"],
            "include_patterns": ["*.safetensors", "*.ckpt"],
            "bwlimit": "40M",
            "transfers": 1,
            "checkers": 2,
        },
    )


def create_local_mount_target(db) -> int:
    return db.create_transfer_target(
        name="PC Local",
        kind="local_mount",
        remote_path="pc-comfyui/checkpoints",
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


def test_receiver_transfer_target_api_hides_token_and_preflights(app_modules: tuple) -> None:
    _db, main, data_root, _config_root = app_modules
    source = data_root / "stable-diffusion" / "checkpoints" / "Model.safetensors"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"model")
    client = TestClient(main.app)

    create_response = client.post(
        "/api/transfer/targets",
        json={
            "name": "PC Receiver",
            "kind": "receiver",
            "receiver_url": "http://192.168.0.50:8088",
            "receiver_token": "receiver-secret",
            "remote_path": "checkpoints",
            "policy": {
                "allowed_source_prefixes": ["stable-diffusion/checkpoints"],
                "include_patterns": ["*.safetensors"],
            },
        },
        auth=("admin", "test-password-that-is-long"),
    )

    assert create_response.status_code == 200
    target = create_response.json()["target"]
    assert target["kind"] == "receiver"
    assert target["receiver_url"] == "http://192.168.0.50:8088"
    assert target["receiver_token_set"] is True
    assert "receiver-secret" not in json.dumps(create_response.json())

    preflight_response = client.post(
        "/api/transfer/preflight",
        json={
            "target_id": target["id"],
            "source_path": "stable-diffusion/checkpoints/Model.safetensors",
            "destination_subpath": "picked/folder",
        },
        auth=("admin", "test-password-that-is-long"),
    )
    assert preflight_response.status_code == 200
    assert preflight_response.json()["destination"] == "receiver:/checkpoints/picked/folder/Model.safetensors"


def test_receiver_tree_api_proxies_registered_target_token(app_modules: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    db, main, _data_root, _config_root = app_modules
    target_id = create_receiver_target(db)
    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        text = '{"ok": true}'
        content = b'{"ok": true}'

        def json(self) -> dict[str, object]:
            return {
                "ok": True,
                "path": "checkpoints",
                "root": {
                    "name": "checkpoints",
                    "path": "checkpoints",
                    "kind": "directory",
                    "has_children": True,
                    "children": [
                        {
                            "name": "anime",
                            "path": "checkpoints/anime",
                            "kind": "directory",
                            "has_children": False,
                        }
                    ],
                },
            }

    def fake_get(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(main.requests, "get", fake_get)
    client = TestClient(main.app)

    response = client.get(
        f"/api/transfer/targets/{target_id}/receiver/tree?path=checkpoints",
        auth=("admin", "test-password-that-is-long"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["children"][0]["path"] == "checkpoints/anime"
    assert calls == [
        {
            "url": "http://receiver.local:8088/api/browse",
            "params": {"path": "checkpoints", "limit": 500},
            "headers": {"X-Receiver-Token": "receiver-secret"},
            "timeout": 15,
        }
    ]


def test_local_mount_tree_preflight_and_job_payload_hide_raw_mount_paths(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, main, data_root, _config_root = app_modules
    target_base = main.DATA_REMOTE_ROOT / "pc-comfyui" / "checkpoints"
    (target_base / "picked" / "nested").mkdir(parents=True)
    (target_base / "plain-file.txt").write_text("not a folder", encoding="utf-8")
    try:
        (target_base / "linked").symlink_to(target_base / "picked", target_is_directory=True)
    except OSError:
        pass
    source = data_root / "stable-diffusion" / "checkpoints" / "Model.safetensors"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"model")
    enqueued: list[int] = []
    monkeypatch.setattr(main.internal_jobs, "enqueue_job", lambda job_id: enqueued.append(job_id))
    client = TestClient(main.app)

    create_response = client.post(
        "/api/transfer/targets",
        json={
            "name": "PC Local",
            "kind": "local_mount",
            "remote_path": "pc-comfyui/checkpoints",
            "receiver_url": "http://receiver.local:8088",
            "receiver_token": "should-not-survive",
            "policy": {
                "allowed_source_prefixes": ["stable-diffusion/checkpoints"],
                "include_patterns": ["*.safetensors"],
            },
        },
        auth=("admin", "test-password-that-is-long"),
    )

    assert create_response.status_code == 200
    target = create_response.json()["target"]
    assert target["kind"] == "local_mount"
    assert target["remote_path"] == "pc-comfyui/checkpoints"
    assert target["remote_name"] == ""
    assert target["receiver_url"] == ""
    assert target["receiver_token_set"] is False
    assert str(main.DATA_REMOTE_ROOT) not in json.dumps(create_response.json(), ensure_ascii=False)
    assert "should-not-survive" not in json.dumps(create_response.json(), ensure_ascii=False)

    tree_response = client.get(
        f"/api/transfer/targets/{target['id']}/local-mount/tree",
        auth=("admin", "test-password-that-is-long"),
    )
    assert tree_response.status_code == 200
    tree_payload = tree_response.json()
    child_names = [child["name"] for child in tree_payload["children"]]
    assert child_names == ["picked"]
    assert tree_payload["children"][0]["path"] == "picked"
    assert str(main.DATA_REMOTE_ROOT) not in json.dumps(tree_payload, ensure_ascii=False)

    nested_response = client.get(
        f"/api/transfer/targets/{target['id']}/local-mount/tree?path=picked",
        auth=("admin", "test-password-that-is-long"),
    )
    assert nested_response.status_code == 200
    assert nested_response.json()["children"][0]["path"] == "picked/nested"

    preflight_response = client.post(
        "/api/transfer/preflight",
        json={
            "target_id": target["id"],
            "source_path": "stable-diffusion/checkpoints/Model.safetensors",
            "destination_subpath": "picked",
        },
        auth=("admin", "test-password-that-is-long"),
    )
    assert preflight_response.status_code == 200
    assert preflight_response.json()["destination"] == "PC Local/picked/Model.safetensors"
    assert str(main.DATA_REMOTE_ROOT) not in json.dumps(preflight_response.json(), ensure_ascii=False)

    job_response = client.post(
        "/api/transfer/jobs",
        json={
            "target_id": target["id"],
            "source_path": "stable-diffusion/checkpoints/Model.safetensors",
            "destination_subpath": "picked",
        },
        auth=("admin", "test-password-that-is-long"),
    )
    assert job_response.status_code == 200
    job = db.get_job(job_response.json()["job"]["id"])
    assert job is not None
    assert db.parse_internal_job_payload(job) == {
        "target_id": target["id"],
        "source_path": "stable-diffusion/checkpoints/Model.safetensors",
        "destination_subpath": "picked",
    }
    assert enqueued == [job_response.json()["job"]["id"]]
    assert str(main.DATA_REMOTE_ROOT) not in json.dumps(job_response.json(), ensure_ascii=False)


def test_data_root_clone_to_local_mount_preflight_job_and_handler(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, main, data_root, config_root = app_modules
    target_id = create_local_mount_target(db)
    target_base = main.DATA_REMOTE_ROOT / "pc-comfyui" / "checkpoints"
    target_base.mkdir(parents=True)
    (data_root / "stable-diffusion" / "checkpoints").mkdir(parents=True)
    (data_root / "stable-diffusion" / "checkpoints" / "Model.ckpt").write_bytes(b"checkpoint")
    (data_root / "notes.txt").write_text("notes", encoding="utf-8")
    existing = target_base / "snapshot" / "notes.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("old", encoding="utf-8")
    enqueued: list[int] = []
    monkeypatch.setattr(main.internal_jobs, "enqueue_job", lambda job_id: enqueued.append(job_id))
    client = TestClient(main.app)

    preflight_response = client.post(
        "/api/transfer/data-root/preflight",
        json={"target_id": target_id, "destination_subpath": "snapshot"},
        auth=("admin", "test-password-that-is-long"),
    )

    assert preflight_response.status_code == 200
    preflight = preflight_response.json()
    assert preflight["source_name"] == "/data"
    assert preflight["file_count"] == 2
    assert preflight["destination"] == "PC Local/snapshot"
    assert preflight["include_patterns"] == []
    assert str(data_root) not in json.dumps(preflight, ensure_ascii=False)
    assert str(main.DATA_REMOTE_ROOT) not in json.dumps(preflight, ensure_ascii=False)

    job_response = client.post(
        "/api/transfer/data-root/jobs",
        json={"target_id": target_id, "destination_subpath": "snapshot"},
        auth=("admin", "test-password-that-is-long"),
    )

    assert job_response.status_code == 200
    job_id = job_response.json()["job"]["id"]
    assert enqueued == [job_id]
    job = db.get_job(job_id)
    assert job is not None
    assert db.parse_internal_job_payload(job) == {
        "target_id": target_id,
        "destination_subpath": "snapshot",
        "data_root_clone": True,
    }

    main.run_transfer_copy_job(job_id, db.get_job(job_id) or {})

    destination_model = target_base / "snapshot" / "stable-diffusion" / "checkpoints" / "Model.ckpt"
    assert destination_model.read_bytes() == b"checkpoint"
    assert existing.read_text(encoding="utf-8") == "old"
    assert not (target_base / "snapshot" / "data").exists()
    manifest = (config_root / "transfer-manifests" / f"transfer-{job_id}.json").read_text(encoding="utf-8")
    assert '"data_root_clone": true' in manifest
    assert '"source_path": ""' in manifest
    assert '"target_base": "pc-comfyui/checkpoints"' in manifest
    assert str(data_root) not in manifest
    assert str(main.DATA_REMOTE_ROOT) not in manifest


def test_local_mount_target_api_rejects_root_and_escape_paths(app_modules: tuple) -> None:
    _db, main, _data_root, _config_root = app_modules
    client = TestClient(main.app)
    base_payload = {
        "name": "Bad Local",
        "kind": "local_mount",
        "policy": {"allowed_source_prefixes": ["stable-diffusion/checkpoints"]},
    }

    for remote_path in ("", "/absolute", "../escape", r"pc\escape"):
        response = client.post(
            "/api/transfer/targets",
            json={**base_payload, "remote_path": remote_path},
            auth=("admin", "test-password-that-is-long"),
        )
        assert response.status_code == 400


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


def test_data_root_clone_api_rejects_non_local_target_and_extra_fields(app_modules: tuple) -> None:
    db, main, _data_root, _config_root = app_modules
    target_id = create_receiver_target(db)
    client = TestClient(main.app)

    non_local_response = client.post(
        "/api/transfer/data-root/preflight",
        json={"target_id": target_id},
        auth=("admin", "test-password-that-is-long"),
    )
    assert non_local_response.status_code == 400
    assert "연결 폴더" in non_local_response.json()["detail"]

    extra_field_response = client.post(
        "/api/transfer/data-root/jobs",
        json={"target_id": target_id, "source_path": ""},
        auth=("admin", "test-password-that-is-long"),
    )
    assert extra_field_response.status_code == 400


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


def test_transfer_handler_uploads_to_receiver_and_manifest_hides_token(
    app_modules: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, main, data_root, config_root = app_modules
    target_id = create_receiver_target(db)
    source = data_root / "stable-diffusion" / "checkpoints" / "Model.ckpt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"checkpoint")
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]):
            self.status_code = 200
            self._payload = payload
            self.content = json.dumps(payload).encode("utf-8")
            self.text = self.content.decode("utf-8")
            self.reason = "OK"

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeSession:
        def request(self, method: str, url: str, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            if url.endswith("/api/jobs"):
                return FakeResponse({"ok": True, "job": {"id": "receiver-job-1"}})
            return FakeResponse({"ok": True})

        def post(self, url: str, **kwargs):
            data = kwargs.get("data")
            body = data.read() if data is not None else b""
            calls.append({"method": "POST", "url": url, "body": body, **kwargs})
            return FakeResponse({"ok": True})

    monkeypatch.setattr(main.requests, "Session", FakeSession)
    job_id = db.create_internal_job(
        main.INTERNAL_JOB_TRANSFER_COPY,
        input_text="transfer:stable-diffusion/checkpoints/Model.ckpt",
        payload={
            "target_id": target_id,
            "source_path": "stable-diffusion/checkpoints/Model.ckpt",
        },
        source="transfer",
    )

    main.run_transfer_copy_job(job_id, db.get_job(job_id) or {})

    assert calls[0]["url"] == "http://receiver.local:8088/api/jobs"
    assert calls[0]["headers"] == {"X-Receiver-Token": "receiver-secret"}
    upload_call = next(call for call in calls if str(call["url"]).endswith("/files/checkpoints/Model.ckpt"))
    assert upload_call["body"] == b"checkpoint"
    manifest = (config_root / "transfer-manifests" / f"transfer-{job_id}.json").read_text(encoding="utf-8")
    assert "receiver-job-1" in manifest
    assert "receiver-secret" not in manifest


def test_transfer_handler_copies_to_local_mount_and_manifest_hides_absolute_path(app_modules: tuple) -> None:
    db, main, data_root, config_root = app_modules
    target_id = create_local_mount_target(db)
    target_base = main.DATA_REMOTE_ROOT / "pc-comfyui" / "checkpoints"
    (target_base / "picked").mkdir(parents=True)
    source = data_root / "stable-diffusion" / "checkpoints" / "Model.ckpt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"checkpoint")
    job_id = db.create_internal_job(
        main.INTERNAL_JOB_TRANSFER_COPY,
        input_text="transfer:stable-diffusion/checkpoints/Model.ckpt",
        payload={
            "target_id": target_id,
            "source_path": "stable-diffusion/checkpoints/Model.ckpt",
            "destination_subpath": "picked",
        },
        source="transfer",
    )

    main.run_transfer_copy_job(job_id, db.get_job(job_id) or {})

    destination = target_base / "picked" / "Model.ckpt"
    assert destination.read_bytes() == b"checkpoint"
    job = db.get_job(job_id)
    assert job is not None
    assert job["progress_bytes"] == len(b"checkpoint")
    manifest = (config_root / "transfer-manifests" / f"transfer-{job_id}.json").read_text(encoding="utf-8")
    assert '"local_mount"' in manifest
    assert '"target_base": "pc-comfyui/checkpoints"' in manifest
    assert str(main.DATA_REMOTE_ROOT) not in manifest


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
    assert 'id="transfer-setting-kind"' in template
    assert '<option value="local_mount">연결 폴더 (/data_remote)</option>' in template
    assert 'class="transfer-settings-stack"' in template
    assert 'class="transfer-settings-panel"' in template
    assert 'class="transfer-advanced-targets"' in template
    assert '연결 폴더 저장' in template
    assert 'Receiver 또는 rclone 대상 만들기' in template
    assert 'value="civitai, stable-diffusion, huggingface, gallery-dl, hitomi, asmr.one"' in template
    assert 'placeholder="비워두면 모든 파일"' in template
    assert 'id="transfer-setting-receiver-url"' in template
    assert 'id="transfer-receiver-label"' in template
    assert 'id="transfer-root-target"' in template
    assert 'id="transfer-root-submit"' in template
    assert 'data-action="transfer"' in template
    assert "fetch('/api/transfer/targets')" in template
    assert "/${treeKind}/tree?path=" in template
    assert "'local-mount'" in template
    assert "fetch('/api/transfer/preflight'" in template
    assert "fetch('/api/transfer/jobs'" in template
    assert "fetch('/api/transfer/data-root/preflight'" in template
    assert "fetch('/api/transfer/data-root/jobs'" in template
    payload_start = template.index("function transferJobPayload")
    payload_end = template.index("function renderTransferPreflight", payload_start)
    payload_block = template[payload_start:payload_end]
    assert "target_id" in payload_block
    assert "source_path" in payload_block
    assert "destination_subpath" in payload_block
    assert "remote_path" not in payload_block
    assert "receiver_token" not in payload_block
    assert "/data_remote" not in payload_block
    assert "mode" not in payload_block
    root_payload_start = template.index("function transferDataRootPayload")
    root_payload_end = template.index("function clearTransferDataRootPreflight", root_payload_start)
    root_payload_block = template[root_payload_start:root_payload_end]
    assert "target_id" in root_payload_block
    assert "destination_subpath" in root_payload_block
    assert "source_path" not in root_payload_block
    assert "remote_path" not in root_payload_block
    assert "receiver_token" not in root_payload_block
    assert "/data_remote" not in root_payload_block
    fields_start = template.index("function updateTransferSettingKindFields")
    fields_end = template.index("async function saveTransferSettingTarget", fields_start)
    fields_block = template[fields_start:fields_end]
    assert "local_mount" in fields_block
    assert "receiverTokenField.hidden = kind !== 'receiver'" in fields_block
