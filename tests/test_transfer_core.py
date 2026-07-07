from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.transfer as transfer_module
from app.transfer import (
    COMFYUI_MODEL_FOLDER_ALIASES,
    DEFAULT_DATA_REMOTE_DIR,
    DEFAULT_RCLONE_CONFIG,
    TRANSFER_MAX_CHECKERS,
    TRANSFER_MAX_CONCURRENT_HARD_LIMIT,
    TRANSFER_MAX_TRANSFERS,
    TARGET_KIND_LOCAL_MOUNT,
    TARGET_KIND_RECEIVER,
    build_receiver_destination_path,
    check_comfyui_local_mount_target,
    copy_to_local_mount,
    data_remote_dir,
    ensure_data_remote_is_separate,
    local_mount_preflight,
    local_mount_tree,
    build_remote_destination,
    build_rclone_copy_command,
    normalize_receiver_url,
    normalize_local_mount_remote_path,
    normalize_remote_path,
    receiver_timeout_seconds,
    resolve_data_source_path,
    resolve_local_mount_base,
    rclone_config_path,
    sanitize_policy,
    transfer_max_concurrent,
    validate_target_kind,
    validate_destination_subpath,
    validate_remote_name,
)


def test_comfyui_local_mount_check_detects_models_root_and_suggestions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_remote_root = tmp_path / "data_remote"
    models_root = data_remote_root / "pc-comfyui" / "ComfyUI" / "models"
    for folder in ("checkpoints", "loras", "vae"):
        (models_root / folder).mkdir(parents=True)
    monkeypatch.delenv("DATA_REMOTE_DIR", raising=False)

    result = check_comfyui_local_mount_target(
        {"kind": "local_mount", "remote_path": "pc-comfyui/ComfyUI/models"},
        data_remote_root=data_remote_root,
    )

    assert result["kind"] == "models_root"
    assert result["target_base"] == "pc-comfyui/ComfyUI/models"
    assert result["display_base"] == "pc-comfyui/ComfyUI/models"
    assert result["models_subpath"] == ""
    assert set(result["present"]) >= {"checkpoints", "loras", "vae"}
    assert "upscale_models" in result["missing"]
    checkpoint_suggestion = next(
        item for item in result["suggested_mappings"] if item["source_prefix"] == "stable-diffusion/checkpoints"
    )
    assert checkpoint_suggestion["destination_subpath"] == "checkpoints"
    assert checkpoint_suggestion["status"] == "present"
    serialized = json.dumps(result, ensure_ascii=False)
    assert str(data_remote_root) not in serialized
    assert "/data_remote" not in serialized


def test_comfyui_local_mount_check_detects_alias_folders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_remote_root = tmp_path / "data_remote"
    models_root = data_remote_root / "pc-comfyui" / "ComfyUI" / "models"
    for folder in ("clip", "unet", "t2i_adapter"):
        (models_root / folder).mkdir(parents=True)
    monkeypatch.setenv("DATA_REMOTE_DIR", str(data_remote_root))

    result = check_comfyui_local_mount_target(
        {"kind": "local_mount", "remote_path": "pc-comfyui/ComfyUI/models"}
    )

    assert COMFYUI_MODEL_FOLDER_ALIASES["clip"] == "text_encoders"
    assert COMFYUI_MODEL_FOLDER_ALIASES["unet"] == "diffusion_models"
    assert COMFYUI_MODEL_FOLDER_ALIASES["t2i_adapter"] == "controlnet"
    assert set(result["present"]) >= {"text_encoders", "diffusion_models", "controlnet"}
    assert {
        (alias["canonical"], alias["found"])
        for alias in result["aliases"]
    } == {
        ("text_encoders", "clip"),
        ("diffusion_models", "unet"),
        ("controlnet", "t2i_adapter"),
    }
    diffusion_suggestion = next(
        item for item in result["suggested_mappings"] if item["source_prefix"] == "stable-diffusion/diffusion_models"
    )
    assert diffusion_suggestion["destination_subpath"] == "diffusion_models"
    assert diffusion_suggestion["available_destination_subpath"] == "unet"
    assert diffusion_suggestion["status"] == "alias_present"
    controlnet_suggestion = next(
        item for item in result["suggested_mappings"] if item["source_prefix"] == "stable-diffusion/controlnet"
    )
    assert controlnet_suggestion["available_destination_subpath"] == "t2i_adapter"


def test_comfyui_local_mount_check_detects_single_folder_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_remote_root = tmp_path / "data_remote"
    (data_remote_root / "pc-comfyui" / "ComfyUI" / "models" / "checkpoints").mkdir(parents=True)
    monkeypatch.setenv("DATA_REMOTE_DIR", str(data_remote_root))

    result = check_comfyui_local_mount_target(
        {"kind": "local_mount", "remote_path": "pc-comfyui/ComfyUI/models/checkpoints"}
    )

    assert result["kind"] == "single_folder"
    assert result["single_folder"] == {"canonical": "checkpoints", "folder": "checkpoints", "alias": False}
    assert result["missing"] == []
    assert result["suggested_mappings"] == [
        {
            "source_prefix": "stable-diffusion/checkpoints",
            "canonical": "checkpoints",
            "destination_subpath": "",
            "folder_present": True,
            "status": "present",
            "found_folder": "checkpoints",
            "found_subpath": "",
        }
    ]


def test_comfyui_local_mount_check_detects_comfyui_root_and_generic_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_remote_root = tmp_path / "data_remote"
    (data_remote_root / "pc-comfyui" / "ComfyUI" / "models" / "checkpoints").mkdir(parents=True)
    (data_remote_root / "plain-target").mkdir(parents=True)
    monkeypatch.setenv("DATA_REMOTE_DIR", str(data_remote_root))

    comfyui_root = check_comfyui_local_mount_target(
        {"kind": "local_mount", "remote_path": "pc-comfyui/ComfyUI"}
    )
    assert comfyui_root["kind"] == "comfyui_root"
    assert comfyui_root["models_subpath"] == "models"
    assert comfyui_root["display_base"] == "pc-comfyui/ComfyUI/models"
    checkpoint_suggestion = next(
        item for item in comfyui_root["suggested_mappings"] if item["source_prefix"] == "stable-diffusion/checkpoints"
    )
    assert checkpoint_suggestion["destination_subpath"] == "models/checkpoints"
    assert checkpoint_suggestion["found_subpath"] == "models/checkpoints"

    generic = check_comfyui_local_mount_target({"kind": "local_mount", "remote_path": "plain-target"})
    assert generic["kind"] == "generic"
    assert generic["display_base"] == "plain-target"
    assert generic["present"] == []
    assert generic["candidate_model_roots"] == []
    assert generic["suggested_mappings"] == []


def test_comfyui_local_mount_check_rejects_unsafe_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_remote_root = tmp_path / "data_remote"
    outside = tmp_path / "outside"
    (data_remote_root / "pc-comfyui" / "ComfyUI" / "models").mkdir(parents=True)
    outside.mkdir()
    monkeypatch.setenv("DATA_REMOTE_DIR", str(data_remote_root))

    with pytest.raises(ValueError, match="local_mount"):
        check_comfyui_local_mount_target({"kind": "rclone", "remote_path": "pc-comfyui"})
    with pytest.raises(ValueError):
        check_comfyui_local_mount_target({"kind": "local_mount", "remote_path": "../escape"})

    try:
        (data_remote_root / "escape").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is not available: {exc}")
    with pytest.raises(ValueError, match="symlink"):
        check_comfyui_local_mount_target({"kind": "local_mount", "remote_path": "escape"})


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


def test_local_mount_target_path_validation_and_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATA_REMOTE_DIR", raising=False)
    data_remote_root = tmp_path / "data_remote"
    (data_remote_root / "pc-comfyui" / "checkpoints").mkdir(parents=True)

    assert data_remote_dir() == Path(DEFAULT_DATA_REMOTE_DIR)
    assert validate_target_kind("local_mount") == TARGET_KIND_LOCAL_MOUNT
    assert normalize_local_mount_remote_path("pc-comfyui//checkpoints/.") == "pc-comfyui/checkpoints"
    assert resolve_local_mount_base(
        "pc-comfyui/checkpoints",
        data_remote_root=data_remote_root,
        require_exists=True,
    ) == data_remote_root / "pc-comfyui" / "checkpoints"

    for remote_path in ("", ".", "/", "/absolute", "../escape", "pc/../escape", r"pc\escape", "smb://host/share"):
        with pytest.raises(ValueError):
            normalize_local_mount_remote_path(remote_path)


def test_local_mount_rejects_symlink_base_and_unsafe_source(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_remote_root = tmp_path / "data_remote"
    outside = tmp_path / "outside"
    source = data_root / "folder" / "model.safetensors"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"model")
    data_remote_root.mkdir()
    outside.mkdir()
    try:
        (data_remote_root / "escape").symlink_to(outside, target_is_directory=True)
        (data_root / "folder" / "link.safetensors").symlink_to(source)
    except OSError as exc:
        pytest.skip(f"symlink creation is not available: {exc}")

    with pytest.raises(ValueError, match="symlink"):
        resolve_local_mount_base("escape", data_remote_root=data_remote_root, require_exists=True)
    with pytest.raises(ValueError, match="symlink"):
        resolve_data_source_path("folder/link.safetensors", data_root=data_root)
    with pytest.raises(ValueError):
        resolve_data_source_path("../outside", data_root=data_root)
    with pytest.raises(ValueError, match="data root"):
        resolve_data_source_path(data_root, data_root=data_root)
    assert resolve_data_source_path("", data_root=data_root, allow_data_root=True) == data_root


def test_local_mount_rejects_overlapping_data_roots(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_remote_root = data_root / "remote"
    data_remote_parent = tmp_path
    data_root.mkdir()

    with pytest.raises(ValueError, match="separate"):
        ensure_data_remote_is_separate(data_root=data_root, data_remote_root=data_root)
    with pytest.raises(ValueError, match="overlap"):
        ensure_data_remote_is_separate(data_root=data_root, data_remote_root=data_remote_root)
    with pytest.raises(ValueError, match="overlap"):
        ensure_data_remote_is_separate(data_root=data_root, data_remote_root=data_remote_parent)


def test_local_mount_tree_returns_target_relative_folders_and_cursor(tmp_path: Path) -> None:
    data_remote_root = tmp_path / "data_remote"
    base = data_remote_root / "pc-comfyui"
    (base / "alpha" / "nested").mkdir(parents=True)
    (base / "beta").mkdir()
    (base / "file.txt").write_text("skip", encoding="utf-8")
    try:
        (base / "linked").symlink_to(base / "alpha", target_is_directory=True)
    except OSError:
        pass

    first = local_mount_tree("pc-comfyui", data_remote_root=data_remote_root, limit=1)
    assert first["path"] == ""
    assert [child["path"] for child in first["children"]] == ["alpha"]
    assert first["next_cursor"] == "alpha"
    assert first["has_more"] is True

    second = local_mount_tree("pc-comfyui", data_remote_root=data_remote_root, limit=10, cursor="alpha")
    assert [child["path"] for child in second["children"]] == ["beta"]
    assert second["next_cursor"] is None

    nested = local_mount_tree("pc-comfyui", path="alpha", data_remote_root=data_remote_root)
    assert nested["root"]["path"] == "alpha"
    assert [child["path"] for child in nested["children"]] == ["alpha/nested"]


def test_local_mount_preflight_and_copy_skip_existing_with_manifest_entries(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_remote_root = tmp_path / "data_remote"
    source = data_root / "stable-diffusion" / "checkpoints"
    target_base = data_remote_root / "pc-comfyui" / "drop"
    source.mkdir(parents=True)
    target_base.mkdir(parents=True)
    (source / "Model.ckpt").write_bytes(b"checkpoint")
    (source / "ignore.txt").write_text("skip", encoding="utf-8")
    (source / "nested").mkdir()
    (source / "nested" / "Extra.safetensors").write_bytes(b"extra")
    existing = target_base / "picked" / "checkpoints" / "Model.ckpt"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"old")

    policy = {
        "include_patterns": ["*.ckpt", "*.safetensors"],
        "allowed_source_prefixes": ["stable-diffusion/checkpoints"],
    }
    preflight = local_mount_preflight(
        "stable-diffusion/checkpoints",
        remote_path="pc-comfyui/drop",
        destination_subpath="picked",
        policy=policy,
        data_root=data_root,
        data_remote_root=data_remote_root,
    )
    assert preflight["file_count"] == 2
    assert preflight["destination"] == "local_mount:/picked/checkpoints"

    logs: list[str] = []
    result = copy_to_local_mount(
        "stable-diffusion/checkpoints",
        remote_path="pc-comfyui/drop",
        destination_subpath="picked",
        policy=policy,
        data_root=data_root,
        data_remote_root=data_remote_root,
        job_id=42,
        log=logs.append,
    )

    assert existing.read_bytes() == b"old"
    assert (target_base / "picked" / "checkpoints" / "nested" / "Extra.safetensors").read_bytes() == b"extra"
    assert not (target_base / "picked" / "checkpoints" / "ignore.txt").exists()
    assert result["copied_files"] == 1
    assert result["skipped_files"] == 1
    assert {entry["action"] for entry in result["entries"]} == {"copied", "skipped_existing"}
    assert all(not part.name.startswith(".") or ".part." not in part.name for part in target_base.rglob("*"))
    assert str(data_root) not in str(result)
    assert str(data_remote_root) not in str(result)
    assert logs and all(str(data_remote_root) not in line for line in logs)


def test_local_mount_copy_skip_existing_does_not_replace_late_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    data_remote_root = tmp_path / "data_remote"
    source = data_root / "stable-diffusion" / "checkpoints" / "Model.ckpt"
    destination = data_remote_root / "pc-comfyui" / "checkpoints" / "Model.ckpt"
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    source.write_bytes(b"new")

    def fake_link(_temp_path: Path | str, destination_path: Path | str) -> None:
        Path(destination_path).write_bytes(b"late")
        raise FileExistsError

    monkeypatch.setattr(transfer_module.os, "link", fake_link)

    action = transfer_module._copy_source_file_to_local_mount(
        source,
        destination,
        base=data_remote_root / "pc-comfyui" / "checkpoints",
        job_id=99,
        skip_existing=True,
    )

    assert action == "skipped_existing"
    assert destination.read_bytes() == b"late"
    assert all(".part." not in part.name for part in destination.parent.iterdir())


def test_local_mount_data_root_clone_copies_contents_without_data_folder(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_remote_root = tmp_path / "data_remote"
    target_base = data_remote_root / "pc-comfyui" / "backup"
    (data_root / "stable-diffusion" / "checkpoints").mkdir(parents=True)
    target_base.mkdir(parents=True)
    (data_root / "stable-diffusion" / "checkpoints" / "Model.ckpt").write_bytes(b"checkpoint")
    (data_root / "notes.txt").write_text("notes", encoding="utf-8")
    existing = target_base / "snapshot" / "notes.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("old", encoding="utf-8")
    try:
        (data_root / "linked").symlink_to(data_root / "notes.txt")
    except OSError:
        pass

    policy = {"preserve_folder_name": False, "skip_existing": True}
    preflight = local_mount_preflight(
        "",
        remote_path="pc-comfyui/backup",
        destination_subpath="snapshot",
        policy=policy,
        data_root=data_root,
        data_remote_root=data_remote_root,
        allow_data_root=True,
    )
    assert preflight["source_path"] == ""
    assert preflight["source_name"] == "data"
    assert preflight["destination"] == "local_mount:/snapshot"
    assert preflight["file_count"] == 2

    result = copy_to_local_mount(
        data_root,
        remote_path="pc-comfyui/backup",
        destination_subpath="snapshot",
        policy=policy,
        data_root=data_root,
        data_remote_root=data_remote_root,
        allow_data_root=True,
    )

    assert (target_base / "snapshot" / "stable-diffusion" / "checkpoints" / "Model.ckpt").read_bytes() == b"checkpoint"
    assert existing.read_text(encoding="utf-8") == "old"
    assert not (target_base / "snapshot" / "data").exists()
    assert result["copied_files"] == 1
    assert result["skipped_files"] == 1
    assert str(data_root) not in str(result)
    assert str(data_remote_root) not in str(result)


def test_receiver_target_validation_and_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    assert validate_target_kind("receiver") == TARGET_KIND_RECEIVER
    assert normalize_receiver_url("http://192.168.0.50:8088/") == "http://192.168.0.50:8088"
    assert build_receiver_destination_path(
        Path("/data/stable-diffusion/checkpoints/model.safetensors"),
        remote_path="checkpoints",
    ) == "checkpoints/model.safetensors"

    monkeypatch.setenv("TRANSFER_RECEIVER_TIMEOUT_SECONDS", "99999")
    assert receiver_timeout_seconds() == 3600

    for value in ("ftp://host", "http://user:pass@example.com", "http://host/path?x=1"):
        with pytest.raises(ValueError):
            normalize_receiver_url(value)


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


def test_policy_category_hint_is_sanitized() -> None:
    policy = sanitize_policy({"category": "civitai", "allowed_source_prefixes": ["stable-diffusion"]})

    assert policy["category"] == "civitai"

    with pytest.raises(ValueError, match="policy category"):
        sanitize_policy({"category": "../escape"})


def test_comfyui_policy_mappings_are_sanitized() -> None:
    policy = sanitize_policy(
        {
            "allowed_source_prefixes": ["stable-diffusion"],
            "comfyui_mappings": {
                "stable-diffusion/checkpoints": "checkpoints",
                "stable-diffusion/loras": "custom/loras",
                "stable-diffusion/vae": "",
            },
        }
    )

    assert policy["comfyui_mappings"] == {
        "stable-diffusion/checkpoints": "checkpoints",
        "stable-diffusion/loras": "custom/loras",
    }

    with pytest.raises(ValueError, match="under stable-diffusion"):
        sanitize_policy({"comfyui_mappings": {"huggingface/models": "models"}})

    with pytest.raises(ValueError, match="Remote path"):
        sanitize_policy({"comfyui_mappings": {"stable-diffusion/checkpoints": "../escape"}})


def test_sync_move_delete_policy_inputs_are_rejected() -> None:
    for policy in (
        {"mode": "sync"},
        {"operation": "move"},
        {"delete_excluded": True},
        {"preserve_folder_name": True, "rclone_args": ["--delete-after"]},
    ):
        with pytest.raises(ValueError, match="copy only"):
            sanitize_policy(policy)
