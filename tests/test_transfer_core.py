from __future__ import annotations

from pathlib import Path

import pytest

from app.transfer import (
    DEFAULT_RCLONE_CONFIG,
    TRANSFER_MAX_CHECKERS,
    TRANSFER_MAX_CONCURRENT_HARD_LIMIT,
    TRANSFER_MAX_TRANSFERS,
    build_remote_destination,
    build_rclone_copy_command,
    normalize_remote_path,
    rclone_config_path,
    sanitize_policy,
    transfer_max_concurrent,
    validate_destination_subpath,
    validate_remote_name,
)


def test_remote_name_and_path_validation() -> None:
    assert validate_remote_name("pc-comfyui_1") == "pc-comfyui_1"
    assert normalize_remote_path(r"ComfyUI\models\checkpoints") == "ComfyUI/models/checkpoints"
    assert normalize_remote_path("ComfyUI//models/./checkpoints") == "ComfyUI/models/checkpoints"
    assert build_remote_destination(
        Path("/data/stable-diffusion/checkpoints/model.safetensors"),
        remote_name="pc-comfyui",
        remote_path="ComfyUI/models",
        destination_subpath="checkpoints",
    ) == "pc-comfyui:ComfyUI/models/checkpoints/model.safetensors"

    for remote_name in ("", "-bad", "bad:name", "bad/path"):
        with pytest.raises(ValueError):
            validate_remote_name(remote_name)

    for remote_path in ("/absolute", "../escape", "ComfyUI/../escape", "other:raw"):
        with pytest.raises(ValueError):
            normalize_remote_path(remote_path)


def test_destination_subpath_rejects_escape_inputs() -> None:
    assert validate_destination_subpath("models/checkpoints") == "models/checkpoints"
    assert validate_destination_subpath("") == ""

    for subpath in ("/absolute", "../escape", "models/../escape", r"models\escape", "other:raw"):
        with pytest.raises(ValueError):
            validate_destination_subpath(subpath)


def test_file_source_uses_rclone_copyto(tmp_path: Path) -> None:
    source = tmp_path / "SomeModel.safetensors"
    source.write_text("model", encoding="utf-8")

    command = build_rclone_copy_command(
        source,
        remote_name="pc-comfyui",
        remote_path="ComfyUI/models/checkpoints",
        policy={"bwlimit": "", "transfers": 2, "checkers": 3},
    )

    assert command[:4] == [
        "rclone",
        "copyto",
        str(source),
        "pc-comfyui:ComfyUI/models/checkpoints/SomeModel.safetensors",
    ]
    assert command[command.index("--config") + 1] == DEFAULT_RCLONE_CONFIG
    assert command[command.index("--transfers") + 1] == "2"
    assert command[command.index("--checkers") + 1] == "3"
    assert "sync" not in command
    assert "move" not in command


def test_directory_source_uses_rclone_copy_and_preserves_folder_name(tmp_path: Path) -> None:
    source = tmp_path / "checkpoints"
    source.mkdir()

    command = build_rclone_copy_command(
        source,
        remote_name="pc-comfyui",
        remote_path="ComfyUI/models",
        policy={"bwlimit": ""},
    )

    assert command[:4] == [
        "rclone",
        "copy",
        str(source),
        "pc-comfyui:ComfyUI/models/checkpoints",
    ]


def test_include_patterns_emit_final_exclude(tmp_path: Path) -> None:
    source = tmp_path / "checkpoints"
    source.mkdir()

    command = build_rclone_copy_command(
        source,
        remote_name="pc-comfyui",
        remote_path="ComfyUI/models",
        policy={"bwlimit": "", "include_patterns": ["*.safetensors", "*.ckpt"]},
    )

    assert command[-6:] == ["--include", "*.safetensors", "--include", "*.ckpt", "--exclude", "*"]


def test_default_config_path_and_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "model.ckpt"
    source.write_text("model", encoding="utf-8")

    assert rclone_config_path() == DEFAULT_RCLONE_CONFIG

    monkeypatch.setenv("RCLONE_CONFIG", "/tmp/rclone.conf")
    monkeypatch.setenv("TRANSFER_DEFAULT_TRANSFERS", "3")
    monkeypatch.setenv("TRANSFER_DEFAULT_CHECKERS", "4")
    monkeypatch.setenv("TRANSFER_DEFAULT_BWLIMIT", "25M")

    command = build_rclone_copy_command(source, remote_name="pc-comfyui")

    assert command[command.index("--config") + 1] == "/tmp/rclone.conf"
    assert command[command.index("--transfers") + 1] == "3"
    assert command[command.index("--checkers") + 1] == "4"
    assert command[command.index("--bwlimit") + 1] == "25M"


def test_env_numeric_defaults_are_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSFER_DEFAULT_TRANSFERS", "99")
    monkeypatch.setenv("TRANSFER_DEFAULT_CHECKERS", "99")
    monkeypatch.setenv("TRANSFER_MAX_CONCURRENT", "99")

    policy = sanitize_policy({})

    assert policy["transfers"] == TRANSFER_MAX_TRANSFERS
    assert policy["checkers"] == TRANSFER_MAX_CHECKERS
    assert transfer_max_concurrent() == TRANSFER_MAX_CONCURRENT_HARD_LIMIT


def test_sync_move_delete_policy_inputs_are_rejected() -> None:
    for policy in (
        {"mode": "sync"},
        {"operation": "move"},
        {"delete_excluded": True},
        {"preserve_folder_name": True, "rclone_args": ["--delete-after"]},
    ):
        with pytest.raises(ValueError, match="copy only"):
            sanitize_policy(policy)
