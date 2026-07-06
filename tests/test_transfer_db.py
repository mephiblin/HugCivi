from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture()
def transfer_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config_root = tmp_path / "config"
    config_root.mkdir()
    monkeypatch.setenv("DB_PATH", str(config_root / "jobs.sqlite3"))

    import app.db as db

    importlib.reload(db)
    db.init_db()
    return db


def test_transfer_targets_migration_is_additive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_root = tmp_path / "config"
    config_root.mkdir()
    db_path = config_root / "jobs.sqlite3"
    monkeypatch.setenv("DB_PATH", str(db_path))

    import app.db as db

    importlib.reload(db)
    with db.connect() as conn:
        conn.execute(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                input_text TEXT NOT NULL,
                parsed_json TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                target_dir TEXT,
                filename TEXT,
                progress_bytes INTEGER DEFAULT 0,
                total_bytes INTEGER,
                error TEXT,
                log TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO jobs (created_at, updated_at, input_text, parsed_json, source, status)
            VALUES ('2026-07-06T00:00:00+00:00', '2026-07-06T00:00:00+00:00', 'input', '{}', 'generic', 'done')
            """
        )
        conn.commit()

    db.init_db()

    with db.connect() as conn:
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(transfer_targets)").fetchall()}
        job_count = int(conn.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()["count"])

    assert columns == {
        "id",
        "name",
        "kind",
        "remote_name",
        "remote_path",
        "receiver_url",
        "receiver_token",
        "enabled",
        "policy_json",
        "created_at",
        "updated_at",
    }
    assert "mode" not in columns
    assert job_count == 1


def test_transfer_target_crud_and_policy_round_trip(transfer_db) -> None:
    db = transfer_db
    policy = {
        "bwlimit": "40M",
        "transfers": 1,
        "checkers": 2,
        "allowed_source_prefixes": ["stable-diffusion/checkpoints"],
        "include_patterns": ["*.safetensors", "*.ckpt"],
        "preserve_folder_name": True,
    }

    target_id = db.create_transfer_target(
        name="PC ComfyUI Checkpoints",
        remote_name="pc-comfyui",
        remote_path="ComfyUI/models/checkpoints",
        enabled=True,
        policy=policy,
    )

    target = db.get_transfer_target(target_id)
    assert target is not None
    assert target["name"] == "PC ComfyUI Checkpoints"
    assert target["kind"] == "rclone"
    assert target["remote_name"] == "pc-comfyui"
    assert target["remote_path"] == "ComfyUI/models/checkpoints"
    assert target["enabled"] is True
    assert target["policy_json"] == json.dumps(policy, ensure_ascii=False)
    assert target["policy"] == policy
    assert [item["id"] for item in db.list_transfer_targets()] == [target_id]

    replacement_policy_json = json.dumps({"include_patterns": ["*.ckpt"], "transfers": 1})
    assert db.update_transfer_target(
        target_id,
        name="PC Checkpoints",
        enabled=False,
        policy_json=replacement_policy_json,
    )

    updated = db.get_transfer_target(target_id)
    assert updated is not None
    assert updated["name"] == "PC Checkpoints"
    assert updated["enabled"] is False
    assert updated["policy_json"] == replacement_policy_json
    assert updated["policy"] == {"include_patterns": ["*.ckpt"], "transfers": 1}
    assert db.list_transfer_targets(include_disabled=False) == []

    assert db.delete_transfer_target(target_id)
    assert db.get_transfer_target(target_id) is None
    assert db.list_transfer_targets() == []
    assert db.delete_transfer_target(target_id) is False


def test_receiver_transfer_target_round_trip(transfer_db) -> None:
    db = transfer_db

    target_id = db.create_transfer_target(
        name="PC Receiver",
        kind="receiver",
        receiver_url="http://192.168.0.50:8088",
        receiver_token="secret-token",
        remote_path="checkpoints",
        policy={"allowed_source_prefixes": ["stable-diffusion/checkpoints"]},
    )

    target = db.get_transfer_target(target_id)
    assert target is not None
    assert target["kind"] == "receiver"
    assert target["remote_name"] == ""
    assert target["receiver_url"] == "http://192.168.0.50:8088"
    assert target["receiver_token"] == "secret-token"


def test_transfer_target_policy_json_requires_object(transfer_db) -> None:
    db = transfer_db

    with pytest.raises(ValueError):
        db.create_transfer_target(name="Bad", remote_name="remote", policy_json="[1, 2, 3]")

    with pytest.raises(ValueError):
        db.create_transfer_target(name="Bad", remote_name="remote", policy_json="{")


def test_create_internal_job_source_supports_transfer_and_default(transfer_db) -> None:
    db = transfer_db

    default_id = db.create_internal_job("archive_zip", input_text="prepare zip", payload={"path": "folder"})
    transfer_id = db.create_internal_job(
        "transfer_copy",
        input_text="copy model",
        payload={"target_id": 1},
        source="transfer",
    )

    default_job = db.get_job(default_id)
    transfer_job = db.get_job(transfer_id)
    assert default_job is not None
    assert transfer_job is not None
    assert default_job["source"] == "internal"
    assert transfer_job["source"] == "transfer"
    assert transfer_job["job_kind"] == "transfer_copy"

    with pytest.raises(ValueError):
        db.create_internal_job(
            "transfer_copy",
            input_text="copy model",
            payload={"target_id": 1},
            source="https://user:secret@example.com",
        )
