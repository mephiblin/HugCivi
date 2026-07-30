from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlparse

from .utils import redact_sensitive_text

DEFAULT_RCLONE_CONFIG = "/config/rclone/rclone.conf"
DEFAULT_DATA_ROOT = "/data"
DEFAULT_DATA_REMOTE_DIR = "/data_remote"
TARGET_KIND_RCLONE = "rclone"
TARGET_KIND_RECEIVER = "receiver"
TARGET_KIND_LOCAL_MOUNT = "local_mount"
TRANSFER_DEFAULT_TRANSFERS = 1
TRANSFER_DEFAULT_CHECKERS = 2
TRANSFER_DEFAULT_BWLIMIT = "40M"
TRANSFER_RECEIVER_TIMEOUT_DEFAULT_SECONDS = 300
TRANSFER_RECEIVER_TIMEOUT_MAX_SECONDS = 3600
TRANSFER_MAX_TRANSFERS = 4
TRANSFER_MAX_CHECKERS = 8
TRANSFER_MAX_CONCURRENT_DEFAULT = 1
TRANSFER_MAX_CONCURRENT_HARD_LIMIT = 4

COMFYUI_MODEL_FOLDERS = {
    "checkpoints": ("checkpoints",),
    "configs": ("configs",),
    "loras": ("loras",),
    "vae": ("vae",),
    "text_encoders": ("text_encoders", "clip"),
    "diffusion_models": ("diffusion_models", "unet"),
    "clip_vision": ("clip_vision",),
    "style_models": ("style_models",),
    "embeddings": ("embeddings",),
    "diffusers": ("diffusers",),
    "vae_approx": ("vae_approx",),
    "controlnet": ("controlnet", "t2i_adapter"),
    "gligen": ("gligen",),
    "upscale_models": ("upscale_models",),
    "latent_upscale_models": ("latent_upscale_models",),
    "hypernetworks": ("hypernetworks",),
    "photomaker": ("photomaker",),
    "classifiers": ("classifiers",),
    "model_patches": ("model_patches",),
    "audio_encoders": ("audio_encoders",),
    "background_removal": ("background_removal",),
    "frame_interpolation": ("frame_interpolation",),
    "geometry_estimation": ("geometry_estimation",),
    "optical_flow": ("optical_flow",),
    "detection": ("detection",),
}
COMFYUI_CANONICAL_MODEL_FOLDERS = tuple(COMFYUI_MODEL_FOLDERS)
COMFYUI_MODEL_FOLDER_ALIASES = {
    folder: canonical
    for canonical, folders in COMFYUI_MODEL_FOLDERS.items()
    for folder in folders[1:]
}
COMFYUI_HUGCIVI_ROUTE_MAP = {
    "stable-diffusion/checkpoints": "checkpoints",
    "stable-diffusion/loras": "loras",
    "stable-diffusion/diffusion_models": "diffusion_models",
    "stable-diffusion/vae": "vae",
    "stable-diffusion/controlnet": "controlnet",
    "stable-diffusion/embeddings": "embeddings",
    "stable-diffusion/upscalers": "upscale_models",
}
COMFYUI_RECOMMENDED_MODEL_FOLDERS = tuple(dict.fromkeys(COMFYUI_HUGCIVI_ROUTE_MAP.values()))

_REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_BWLIMIT_RE = re.compile(r"^(?:off|[0-9]+(?:\.[0-9]+)?[bBkKmMgGtTpP]?)$")
_SENSITIVE_USERINFO_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@")

_POLICY_KEYS = {
    "allowed_source_prefixes",
    "bwlimit",
    "category",
    "checkers",
    "comfyui_mappings",
    "include_patterns",
    "preserve_folder_name",
    "require_check",
    "skip_existing",
    "transfers",
}
_COPY_ESCAPE_KEYS = {
    "args",
    "command",
    "delete",
    "delete_after",
    "delete_before",
    "delete_during",
    "delete_excluded",
    "destination",
    "extra_args",
    "mode",
    "move",
    "operation",
    "raw_remote",
    "remote",
    "remote_target",
    "remove_source_files",
    "rclone_args",
    "shell",
    "sync",
    "target",
}
_COPY_ESCAPE_VALUES = {"delete", "delete-after", "delete-before", "delete-during", "move", "sync"}


@dataclass(frozen=True)
class _TransferSourceFile:
    path: Path
    relative_path: Path
    size_bytes: int


def validate_target_kind(value: str | None) -> str:
    kind = str(value or TARGET_KIND_RCLONE).strip().lower()
    if kind not in {TARGET_KIND_RCLONE, TARGET_KIND_RECEIVER, TARGET_KIND_LOCAL_MOUNT}:
        raise ValueError("Unsupported transfer target type")
    return kind


def validate_remote_name(value: str) -> str:
    name = str(value or "").strip()
    if not _REMOTE_NAME_RE.fullmatch(name):
        raise ValueError("Invalid rclone remote name")
    return name


def normalize_remote_path(value: str | None) -> str:
    if value is None:
        return ""

    path = str(value).strip().replace("\\", "/")
    if not path:
        return ""
    if path.startswith("/") or path.startswith("~"):
        raise ValueError("Remote path must be relative")

    parts: list[str] = []
    for raw_segment in path.split("/"):
        segment = raw_segment.strip()
        if segment in {"", "."}:
            continue
        if segment == "..":
            raise ValueError("Remote path must not contain '..'")
        if "\x00" in segment or ":" in segment:
            raise ValueError("Remote path contains an unsafe segment")
        parts.append(segment)
    return "/".join(parts)


def validate_destination_subpath(value: str | None) -> str:
    if value is None or str(value).strip() == "":
        return ""
    if "\\" in str(value):
        raise ValueError("Destination subpath must use forward slashes")
    return normalize_remote_path(str(value))


def data_root_dir(value: str | Path | None = None) -> Path:
    candidate = value if value is not None else os.getenv("DATA_ROOT")
    return Path(str(candidate or "").strip() or DEFAULT_DATA_ROOT)


def data_remote_dir(value: str | Path | None = None) -> Path:
    candidate = value if value is not None else os.getenv("DATA_REMOTE_DIR")
    return Path(str(candidate or "").strip() or DEFAULT_DATA_REMOTE_DIR)


def ensure_data_remote_is_separate(
    *,
    data_root: str | Path | None = None,
    data_remote_root: str | Path | None = None,
) -> None:
    data_root_resolved = data_root_dir(data_root).resolve(strict=False)
    data_remote_resolved = data_remote_dir(data_remote_root).resolve(strict=False)
    if data_root_resolved == data_remote_resolved:
        raise ValueError("data_remote must be separate from data root")
    if data_root_resolved in data_remote_resolved.parents or data_remote_resolved in data_root_resolved.parents:
        raise ValueError("data_remote must not overlap data root")


def normalize_local_mount_remote_path(value: str | None) -> str:
    return _normalize_strict_relative_path(value, "Local mount path", allow_empty=False)


def resolve_data_source_path(
    source_path: str | Path,
    *,
    data_root: str | Path | None = None,
    allow_data_root: bool = False,
) -> Path:
    root = data_root_dir(data_root)
    root_resolved = root.resolve(strict=False)
    raw_path = Path(source_path)
    if raw_path.is_absolute():
        candidate = raw_path
    else:
        candidate = _join_relative(
            root,
            _normalize_strict_relative_path(str(source_path), "Source path", allow_empty=allow_data_root),
        )

    resolved = candidate.resolve(strict=False)
    if resolved == root_resolved and not allow_data_root:
        raise ValueError("Transfer source must not be the data root")
    _ensure_resolved_inside(resolved, root_resolved, "Transfer source escapes data root")
    if not candidate.exists():
        raise ValueError("Transfer source does not exist")
    if candidate.is_symlink():
        raise ValueError("Transfer source must not be a symlink")
    _reject_symlink_components(candidate, root, "Transfer source must not traverse symlinks")
    if not candidate.is_file() and not candidate.is_dir():
        raise ValueError("Transfer source must be an existing file or directory")
    return candidate


def relative_data_source_path(source: str | Path, *, data_root: str | Path | None = None) -> str:
    root = data_root_dir(data_root).resolve(strict=False)
    path = Path(source).resolve(strict=False)
    _ensure_resolved_inside(path, root, "Transfer source escapes data root")
    return "" if path == root else path.relative_to(root).as_posix()


def resolve_local_mount_base(
    remote_path: str | None,
    *,
    data_remote_root: str | Path | None = None,
    require_exists: bool = False,
) -> Path:
    root = data_remote_dir(data_remote_root)
    root_resolved = root.resolve(strict=False)
    normalized = normalize_local_mount_remote_path(remote_path)
    base = _join_relative(root, normalized)
    base_resolved = base.resolve(strict=False)
    if base_resolved == root_resolved:
        raise ValueError("Local mount target must not be the data_remote root")
    _reject_symlink_components(base, root, "Local mount target must not traverse symlinks")
    _ensure_resolved_inside(base_resolved, root_resolved, "Local mount target escapes data_remote root")
    if require_exists:
        if not base.exists():
            raise ValueError("Local mount target folder does not exist")
        if base.is_symlink():
            raise ValueError("Local mount target folder must not be a symlink")
        if not base.is_dir():
            raise ValueError("Local mount target must be a folder")
    return base


def resolve_local_mount_destination(
    remote_path: str | None,
    destination_subpath: str | None = "",
    *,
    data_remote_root: str | Path | None = None,
    require_exists: bool = False,
) -> Path:
    base = resolve_local_mount_base(remote_path, data_remote_root=data_remote_root, require_exists=True)
    subpath = validate_destination_subpath(destination_subpath)
    destination = _join_relative(base, subpath) if subpath else base
    destination_resolved = destination.resolve(strict=False)
    base_resolved = base.resolve(strict=False)
    _ensure_resolved_inside(destination_resolved, base_resolved, "Local mount destination escapes target base")
    _reject_symlink_components(destination, base, "Local mount destination must not traverse symlinks")
    if require_exists:
        if not destination.exists():
            raise ValueError("Local mount destination folder does not exist")
        if destination.is_symlink():
            raise ValueError("Local mount destination folder must not be a symlink")
        if not destination.is_dir():
            raise ValueError("Local mount destination must be a folder")
    return destination


def build_local_mount_destination_path(
    source: str | Path,
    *,
    destination_subpath: str | None = "",
    preserve_folder_name: bool = True,
) -> str:
    subpath = validate_destination_subpath(destination_subpath)
    source_path = Path(source)
    path_parts = [subpath] if subpath else []
    if source_path.is_file() or preserve_folder_name:
        path_parts.append(_safe_source_name(source_path))
    return "/".join(part for part in path_parts if part)


def local_mount_tree(
    remote_path: str | None,
    *,
    path: str | None = "",
    limit: int = 500,
    cursor: str | None = None,
    data_remote_root: str | Path | None = None,
) -> dict[str, Any]:
    base = resolve_local_mount_base(remote_path, data_remote_root=data_remote_root, require_exists=True)
    clean_path = validate_destination_subpath(path)
    root_path = resolve_local_mount_destination(
        remote_path,
        clean_path,
        data_remote_root=data_remote_root,
        require_exists=True,
    )
    child_limit = _bounded_limit(limit, default=500, maximum=500)
    children = _direct_local_mount_child_dirs(root_path)

    start_index = 0
    if cursor:
        for index, child in enumerate(children):
            if child.name == cursor:
                start_index = index + 1
                break
        else:
            raise ValueError("Local mount tree cursor was not found")

    page = children[start_index : start_index + child_limit]
    has_more = start_index + child_limit < len(children)
    child_payloads = [_local_mount_child_payload(child, base) for child in page]
    root_payload = {
        "name": root_path.name if clean_path else "",
        "path": clean_path,
        "kind": "directory",
        "has_children": bool(children),
        "children_loaded": not has_more,
        "children": child_payloads,
    }
    return {
        "path": clean_path,
        "root": root_payload,
        "children": child_payloads,
        "items": child_payloads,
        "limit": child_limit,
        "next_cursor": page[-1].name if has_more and page else None,
        "has_more": has_more,
    }


def check_comfyui_local_mount_target(
    target: dict,
    *,
    data_remote_root: str | Path | None = None,
) -> dict:
    if not isinstance(target, Mapping):
        raise ValueError("Transfer target is required")
    kind = validate_target_kind(str(target.get("kind") or ""))
    if kind != TARGET_KIND_LOCAL_MOUNT:
        raise ValueError("ComfyUI folder check supports local_mount targets only")

    remote_path = normalize_local_mount_remote_path(str(target.get("remote_path") or ""))
    base = resolve_local_mount_base(remote_path, data_remote_root=data_remote_root, require_exists=True)
    children = _direct_local_mount_child_dirs(base)

    def with_mapping_checks(result: dict[str, Any]) -> dict[str, Any]:
        mapping_checks = _comfyui_mapping_folder_checks(
            target,
            remote_path,
            data_remote_root=data_remote_root,
        )
        return {
            **result,
            "mapping_checks": mapping_checks,
            "mapping_summary": _comfyui_mapping_check_summary(mapping_checks),
        }

    if base.name.lower() == "models":
        return with_mapping_checks(
            _comfyui_models_root_result(
                result_kind="models_root",
                target_base=remote_path,
                models_subpath="",
                models_path=base,
            )
        )

    single_folder = _comfyui_model_folder_match(base.name)
    if single_folder is not None:
        canonical, is_alias = single_folder
        return with_mapping_checks(
            _comfyui_single_folder_result(
                target_base=remote_path,
                canonical=canonical,
                folder=base.name,
                is_alias=is_alias,
            )
        )

    if (base / "models").is_symlink():
        resolve_local_mount_destination(remote_path, "models", data_remote_root=data_remote_root, require_exists=True)

    models_child = _find_child_dir(children, "models")
    if models_child is not None:
        models_subpath = models_child.name
        models_path = resolve_local_mount_destination(
            remote_path,
            models_subpath,
            data_remote_root=data_remote_root,
            require_exists=True,
        )
        return with_mapping_checks(
            _comfyui_models_root_result(
                result_kind="comfyui_root",
                target_base=remote_path,
                models_subpath=models_subpath,
                models_path=models_path,
            )
        )

    direct_found = _comfyui_found_model_folders(children, root_subpath="")
    if direct_found["present"]:
        return with_mapping_checks(
            _comfyui_models_root_result(
                result_kind="models_root",
                target_base=remote_path,
                models_subpath="",
                models_path=base,
                found=direct_found,
            )
        )

    candidates, suggestion_base, suggestion_found = _comfyui_nested_model_root_candidates(
        remote_path,
        children,
        data_remote_root=data_remote_root,
    )
    return with_mapping_checks(
        {
            "ok": True,
            "kind": "generic",
            "target_kind": TARGET_KIND_LOCAL_MOUNT,
            "target_base": remote_path,
            "base_path": "",
            "display_base": suggestion_base or remote_path,
            "models_subpath": suggestion_base,
            "present": [],
            "found_folders": [],
            "aliases": [],
            "missing": [],
            "single_folder": None,
            "candidate_model_roots": candidates,
            "suggested_mappings": _comfyui_destination_suggestions(
                destination_base=suggestion_base,
                found_by_canonical=suggestion_found,
            ),
            "warnings": [],
        }
    )


def local_mount_preflight(
    source_path: str | Path,
    *,
    remote_path: str | None,
    destination_subpath: str | None = "",
    policy: Mapping[str, Any] | None = None,
    data_root: str | Path | None = None,
    data_remote_root: str | Path | None = None,
    allow_data_root: bool = False,
) -> dict[str, Any]:
    clean_policy = sanitize_policy(policy)
    ensure_data_remote_is_separate(data_root=data_root, data_remote_root=data_remote_root)
    source = resolve_data_source_path(source_path, data_root=data_root, allow_data_root=allow_data_root)
    base = resolve_local_mount_base(remote_path, data_remote_root=data_remote_root, require_exists=True)
    destination_root_relative = build_local_mount_destination_path(
        source,
        destination_subpath=destination_subpath,
        preserve_folder_name=bool(clean_policy["preserve_folder_name"]),
    )
    destination_root = _join_relative(base, destination_root_relative) if destination_root_relative else base
    _ensure_local_mount_destination_parent(destination_root, base, source_is_file=source.is_file())
    _ensure_writable_destination(destination_root, base=base, source_is_file=source.is_file())

    files = list(_iter_transfer_source_files(source, clean_policy))
    if not files:
        raise ValueError("No files match the local mount transfer policy")
    source_bytes = sum(item.size_bytes for item in files)
    return {
        "kind": TARGET_KIND_LOCAL_MOUNT,
        "source_path": relative_data_source_path(source, data_root=data_root),
        "source_kind": "folder" if source.is_dir() else "file",
        "source_name": source.name,
        "source_bytes": source_bytes,
        "file_count": len(files),
        "destination": f"local_mount:/{destination_root_relative}" if destination_root_relative else "local_mount:/",
        "destination_subpath": validate_destination_subpath(destination_subpath),
        "destination_path": destination_root_relative,
        "target_base": normalize_local_mount_remote_path(remote_path),
        "include_patterns": list(clean_policy.get("include_patterns") or []),
        "skip_existing": bool(clean_policy.get("skip_existing", True)),
    }


def copy_to_local_mount(
    source_path: str | Path,
    *,
    remote_path: str | None,
    destination_subpath: str | None = "",
    policy: Mapping[str, Any] | None = None,
    data_root: str | Path | None = None,
    data_remote_root: str | Path | None = None,
    job_id: int | str | None = None,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, int], None] | None = None,
    control_check: Callable[[], None] | None = None,
    allow_data_root: bool = False,
) -> dict[str, Any]:
    clean_policy = sanitize_policy(policy)
    ensure_data_remote_is_separate(data_root=data_root, data_remote_root=data_remote_root)
    source = resolve_data_source_path(source_path, data_root=data_root, allow_data_root=allow_data_root)
    base = resolve_local_mount_base(remote_path, data_remote_root=data_remote_root, require_exists=True)
    destination_root_relative = build_local_mount_destination_path(
        source,
        destination_subpath=destination_subpath,
        preserve_folder_name=bool(clean_policy["preserve_folder_name"]),
    )
    destination_root = _join_relative(base, destination_root_relative) if destination_root_relative else base
    _ensure_local_mount_destination_parent(destination_root, base, source_is_file=source.is_file())
    _ensure_writable_destination(destination_root, base=base, source_is_file=source.is_file())

    files = list(_iter_transfer_source_files(source, clean_policy))
    if not files:
        raise ValueError("No files match the local mount transfer policy")

    skip_existing = bool(clean_policy.get("skip_existing", True))
    copied_bytes = 0
    skipped_bytes = 0
    completed_bytes = 0
    total_bytes = sum(item.size_bytes for item in files)
    entries: list[dict[str, Any]] = []
    for item in files:
        if control_check is not None:
            control_check()
        destination_file = destination_root if source.is_file() else destination_root / item.relative_path
        action = _copy_source_file_to_local_mount(
            item.path,
            destination_file,
            base=base,
            job_id=job_id,
            skip_existing=skip_existing,
        )
        target_relative = _relative_to_base(destination_file, base)
        entry = {
            "source_path": relative_data_source_path(item.path, data_root=data_root),
            "destination_path": target_relative,
            "bytes": item.size_bytes,
            "action": action,
        }
        if action == "copied":
            copied_bytes += item.size_bytes
            if log is not None:
                log(f"local_mount copied: {target_relative}")
        else:
            skipped_bytes += item.size_bytes
            if log is not None:
                log(f"local_mount skipped existing: {target_relative}")
        entries.append(entry)
        completed_bytes += item.size_bytes
        if progress is not None:
            progress(completed_bytes, total_bytes)

    copied_files = sum(1 for entry in entries if entry.get("action") == "copied")
    skipped_files = sum(1 for entry in entries if entry.get("action") == "skipped_existing")
    return {
        "kind": TARGET_KIND_LOCAL_MOUNT,
        "target_base": normalize_local_mount_remote_path(remote_path),
        "destination_path": destination_root_relative,
        "source_path": relative_data_source_path(source, data_root=data_root),
        "file_count": len(files),
        "copied_files": copied_files,
        "skipped_files": skipped_files,
        "copied_bytes": copied_bytes,
        "skipped_bytes": skipped_bytes,
        "entries": entries,
    }


def rclone_config_path(value: str | None = None) -> str:
    candidate = value if value is not None else os.getenv("RCLONE_CONFIG")
    candidate = str(candidate or "").strip()
    return candidate or DEFAULT_RCLONE_CONFIG


def normalize_receiver_url(value: str | None) -> str:
    url = str(value or "").strip().rstrip("/")
    if not url:
        raise ValueError("Receiver URL is required")
    if "\x00" in url or "\n" in url:
        raise ValueError("Invalid receiver URL")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Receiver URL must be http(s)")
    if parsed.username or parsed.password or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("Receiver URL must not include credentials, query, or fragment")
    return url


def receiver_timeout_seconds(value: str | int | None = None) -> int:
    return _positive_int(
        value if value is not None else os.getenv("TRANSFER_RECEIVER_TIMEOUT_SECONDS"),
        TRANSFER_RECEIVER_TIMEOUT_DEFAULT_SECONDS,
        maximum=TRANSFER_RECEIVER_TIMEOUT_MAX_SECONDS,
    )


def transfer_max_concurrent(value: str | int | None = None) -> int:
    return _positive_int(
        value if value is not None else os.getenv("TRANSFER_MAX_CONCURRENT"),
        TRANSFER_MAX_CONCURRENT_DEFAULT,
        maximum=TRANSFER_MAX_CONCURRENT_HARD_LIMIT,
    )


def sanitize_policy(policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw_policy = dict(policy or {})
    _reject_non_copy_policy(raw_policy)

    unknown_keys = sorted(set(raw_policy) - _POLICY_KEYS)
    if unknown_keys:
        raise ValueError(f"Unsupported transfer policy key: {unknown_keys[0]}")

    transfers_default = _positive_int(
        os.getenv("TRANSFER_DEFAULT_TRANSFERS"),
        TRANSFER_DEFAULT_TRANSFERS,
        maximum=TRANSFER_MAX_TRANSFERS,
    )
    checkers_default = _positive_int(
        os.getenv("TRANSFER_DEFAULT_CHECKERS"),
        TRANSFER_DEFAULT_CHECKERS,
        maximum=TRANSFER_MAX_CHECKERS,
    )
    bwlimit_default = os.getenv("TRANSFER_DEFAULT_BWLIMIT", TRANSFER_DEFAULT_BWLIMIT)

    include_patterns = _normalize_include_patterns(raw_policy.get("include_patterns"))
    allowed_source_prefixes = [
        normalized
        for raw_prefix in _list_value(raw_policy.get("allowed_source_prefixes"))
        if (normalized := normalize_remote_path(str(raw_prefix)))
    ]

    return {
        "allowed_source_prefixes": allowed_source_prefixes,
        "bwlimit": _normalize_bwlimit(raw_policy.get("bwlimit", bwlimit_default)),
        "category": _normalize_policy_category(raw_policy.get("category")),
        "checkers": _positive_int(raw_policy.get("checkers"), checkers_default, maximum=TRANSFER_MAX_CHECKERS),
        "comfyui_mappings": _normalize_comfyui_mappings(raw_policy.get("comfyui_mappings")),
        "include_patterns": include_patterns,
        "preserve_folder_name": _bool_value(raw_policy.get("preserve_folder_name"), default=True),
        "require_check": _bool_value(raw_policy.get("require_check"), default=False),
        "skip_existing": _bool_value(raw_policy.get("skip_existing"), default=True),
        "transfers": _positive_int(raw_policy.get("transfers"), transfers_default, maximum=TRANSFER_MAX_TRANSFERS),
    }


def build_remote_destination(
    source: str | Path,
    *,
    remote_name: str,
    remote_path: str | None = "",
    destination_subpath: str | None = "",
    preserve_folder_name: bool = True,
) -> str:
    remote = validate_remote_name(remote_name)
    base_path = normalize_remote_path(remote_path)
    subpath = validate_destination_subpath(destination_subpath)

    source_path = Path(source)
    path_parts = [part for part in (base_path, subpath) if part]
    if source_path.is_file() or preserve_folder_name:
        path_parts.append(_safe_source_name(source_path))

    destination_path = "/".join(path_parts)
    return f"{remote}:{destination_path}" if destination_path else f"{remote}:"


def policy_destination_subpath_for_source(source_path: str | Path, policy: Mapping[str, Any] | None = None) -> str:
    clean_policy = sanitize_policy(policy)
    mappings = clean_policy.get("comfyui_mappings") or {}
    if not isinstance(mappings, dict):
        return ""
    relative_path = normalize_remote_path(str(source_path))
    best_prefix = ""
    best_destination = ""
    for prefix, destination_subpath in mappings.items():
        clean_prefix = normalize_remote_path(str(prefix))
        if not clean_prefix:
            continue
        if relative_path == clean_prefix or relative_path.startswith(f"{clean_prefix}/"):
            if len(clean_prefix) > len(best_prefix):
                best_prefix = clean_prefix
                best_destination = validate_destination_subpath(str(destination_subpath or ""))
    return best_destination


def default_comfyui_destination_subpath_for_source(source_path: str | Path) -> str:
    relative_path = normalize_remote_path(str(source_path))
    best_prefix = ""
    best_destination = ""
    for prefix, destination_subpath in COMFYUI_HUGCIVI_ROUTE_MAP.items():
        if relative_path == prefix or relative_path.startswith(f"{prefix}/"):
            if len(prefix) > len(best_prefix):
                best_prefix = prefix
                best_destination = destination_subpath
    return validate_destination_subpath(best_destination)


def path_looks_like_comfyui_models_root(path: str | Path | None) -> bool:
    normalized = normalize_remote_path(str(path or "")).lower()
    if not normalized:
        return False
    basename = normalized.rsplit("/", 1)[-1]
    return basename == "models" or bool(re.search(r"(?:^|[-_ ])comfyui[-_ ]models$", basename))


def build_receiver_destination_path(
    source: str | Path,
    *,
    remote_path: str | None = "",
    destination_subpath: str | None = "",
    preserve_folder_name: bool = True,
) -> str:
    base_path = normalize_remote_path(remote_path)
    subpath = validate_destination_subpath(destination_subpath)
    source_path = Path(source)
    path_parts = [part for part in (base_path, subpath) if part]
    if source_path.is_file() or preserve_folder_name:
        path_parts.append(_safe_source_name(source_path))
    return "/".join(path_parts)


def build_rclone_copy_command(
    source: str | Path,
    *,
    remote_name: str,
    remote_path: str | None = "",
    destination_subpath: str | None = "",
    policy: Mapping[str, Any] | None = None,
    config_path: str | None = None,
    rclone_bin: str = "rclone",
) -> list[str]:
    source_path = Path(source)
    if source_path.is_file():
        operation = "copyto"
    elif source_path.is_dir():
        operation = "copy"
    else:
        raise ValueError("Transfer source must be an existing file or directory")

    clean_policy = sanitize_policy(policy)
    destination = build_remote_destination(
        source_path,
        remote_name=remote_name,
        remote_path=remote_path,
        destination_subpath=destination_subpath,
        preserve_folder_name=bool(clean_policy["preserve_folder_name"]),
    )

    argv = [
        rclone_bin,
        operation,
        str(source_path),
        destination,
        "--config",
        rclone_config_path(config_path),
        "--transfers",
        str(clean_policy["transfers"]),
        "--checkers",
        str(clean_policy["checkers"]),
    ]
    if clean_policy["bwlimit"]:
        argv.extend(["--bwlimit", str(clean_policy["bwlimit"])])
    for pattern in clean_policy["include_patterns"]:
        argv.extend(["--include", pattern])
    if clean_policy["include_patterns"]:
        argv.extend(["--exclude", "*"])
    return argv


def redact_transfer_output(value: str | None) -> str:
    redacted = redact_sensitive_text(value) or ""
    return _SENSITIVE_USERINFO_RE.sub(r"\1[REDACTED]@", redacted)


def _comfyui_models_root_result(
    *,
    result_kind: str,
    target_base: str,
    models_subpath: str,
    models_path: Path,
    found: dict[str, Any] | None = None,
) -> dict[str, Any]:
    found = found or _comfyui_found_model_folders(
        _direct_local_mount_child_dirs(models_path),
        root_subpath=models_subpath,
    )
    return {
        "ok": True,
        "kind": result_kind,
        "target_kind": TARGET_KIND_LOCAL_MOUNT,
        "target_base": target_base,
        "base_path": "",
        "display_base": _join_posix(target_base, models_subpath),
        "models_subpath": models_subpath,
        "present": found["present"],
        "found_folders": found["found_folders"],
        "aliases": found["aliases"],
        "missing": found["missing"],
        "single_folder": None,
        "candidate_model_roots": [],
        "suggested_mappings": _comfyui_destination_suggestions(
            destination_base=models_subpath,
            found_by_canonical=found["found_by_canonical"],
        ),
        "warnings": [],
    }


def _comfyui_single_folder_result(
    *,
    target_base: str,
    canonical: str,
    folder: str,
    is_alias: bool,
) -> dict[str, Any]:
    found_entry = {
        "canonical": canonical,
        "folder": folder,
        "path": "",
        "alias": is_alias,
    }
    aliases = [{"canonical": canonical, "found": folder, "path": ""}] if is_alias else []
    return {
        "ok": True,
        "kind": "single_folder",
        "target_kind": TARGET_KIND_LOCAL_MOUNT,
        "target_base": target_base,
        "base_path": "",
        "display_base": target_base,
        "models_subpath": None,
        "present": [canonical],
        "found_folders": [found_entry],
        "aliases": aliases,
        "missing": [],
        "single_folder": {
            "canonical": canonical,
            "folder": folder,
            "alias": is_alias,
        },
        "candidate_model_roots": [],
        "suggested_mappings": _comfyui_destination_suggestions(
            destination_base=None,
            found_by_canonical={canonical: found_entry},
            single_canonical=canonical,
        ),
        "warnings": [],
    }


def _comfyui_found_model_folders(children: list[Path], *, root_subpath: str) -> dict[str, Any]:
    found_by_canonical: dict[str, dict[str, Any]] = {}
    aliases: list[dict[str, str]] = []

    for child in children:
        match = _comfyui_model_folder_match(child.name)
        if match is None:
            continue
        canonical, is_alias = match
        path = _join_posix(root_subpath, child.name)
        entry = {
            "canonical": canonical,
            "folder": child.name,
            "path": path,
            "alias": is_alias,
        }
        current = found_by_canonical.get(canonical)
        if current is None or (current.get("alias") and not is_alias):
            found_by_canonical[canonical] = entry
        if is_alias:
            aliases.append({"canonical": canonical, "found": child.name, "path": path})

    present = [canonical for canonical in COMFYUI_CANONICAL_MODEL_FOLDERS if canonical in found_by_canonical]
    return {
        "present": present,
        "found_folders": [found_by_canonical[canonical] for canonical in present],
        "aliases": aliases,
        "missing": [canonical for canonical in COMFYUI_RECOMMENDED_MODEL_FOLDERS if canonical not in found_by_canonical],
        "found_by_canonical": found_by_canonical,
    }


def _comfyui_destination_suggestions(
    *,
    destination_base: str | None,
    found_by_canonical: Mapping[str, Mapping[str, Any]],
    single_canonical: str | None = None,
) -> list[dict[str, Any]]:
    if destination_base is None and single_canonical is None:
        return []

    suggestions: list[dict[str, Any]] = []
    for source_prefix, canonical in COMFYUI_HUGCIVI_ROUTE_MAP.items():
        if single_canonical is not None:
            if canonical != single_canonical:
                continue
            destination_subpath = ""
        else:
            destination_subpath = _join_posix(destination_base or "", canonical)

        found = found_by_canonical.get(canonical)
        if found is None:
            status = "missing"
        elif bool(found.get("alias")):
            status = "alias_present"
        else:
            status = "present"

        suggestion = {
            "source_prefix": source_prefix,
            "canonical": canonical,
            "destination_subpath": destination_subpath,
            "folder_present": found is not None,
            "status": status,
        }
        if found is not None:
            found_subpath = str(found.get("path") or "")
            suggestion["found_folder"] = str(found.get("folder") or "")
            suggestion["found_subpath"] = found_subpath
            if found_subpath != destination_subpath:
                suggestion["available_destination_subpath"] = found_subpath
        suggestions.append(suggestion)
    return suggestions


def _comfyui_mapping_folder_checks(
    target: Mapping[str, Any],
    remote_path: str,
    *,
    data_remote_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    raw_policy = target.get("policy") if isinstance(target, Mapping) else None
    raw_mappings = raw_policy.get("comfyui_mappings") if isinstance(raw_policy, Mapping) else None
    mappings = _normalize_comfyui_mappings(raw_mappings)
    checks: list[dict[str, Any]] = []

    for source_prefix, destination_subpath in mappings.items():
        entry: dict[str, Any] = {
            "source_prefix": source_prefix,
            "destination_subpath": destination_subpath,
            "exists": False,
            "is_dir": False,
            "status": "missing",
        }
        try:
            destination = resolve_local_mount_destination(
                remote_path,
                destination_subpath,
                data_remote_root=data_remote_root,
                require_exists=False,
            )
            if destination.exists():
                entry["exists"] = True
                if destination.is_dir():
                    entry["is_dir"] = True
                    entry["status"] = "present"
                else:
                    entry["status"] = "not_directory"
                    entry["message"] = "Destination path exists but is not a folder"
        except ValueError:
            entry["status"] = "invalid"
            entry["message"] = "Destination folder path is invalid"
        except OSError:
            entry["status"] = "unavailable"
            entry["message"] = "Destination folder status could not be read"
        checks.append(entry)
    return checks


def _comfyui_mapping_check_summary(checks: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        "total": len(checks),
        "present": 0,
        "missing": 0,
        "not_directory": 0,
        "invalid": 0,
        "unavailable": 0,
    }
    for item in checks:
        status = str(item.get("status") or "missing")
        if status in counts:
            counts[status] += 1
    counts["ok"] = counts["total"] == counts["present"]
    return counts


def _comfyui_nested_model_root_candidates(
    remote_path: str,
    children: list[Path],
    *,
    data_remote_root: str | Path | None = None,
) -> tuple[list[dict[str, Any]], str | None, dict[str, dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    suggestion_base: str | None = None
    suggestion_found: dict[str, dict[str, Any]] = {}

    for child in children:
        if child.name.lower() != "comfyui":
            continue
        nested = child / "models"
        try:
            if not nested.exists() or not nested.is_dir():
                continue
        except OSError:
            continue
        models_subpath = _join_posix(child.name, "models")
        models_path = resolve_local_mount_destination(
            remote_path,
            models_subpath,
            data_remote_root=data_remote_root,
            require_exists=True,
        )
        found = _comfyui_found_model_folders(
            _direct_local_mount_child_dirs(models_path),
            root_subpath=models_subpath,
        )
        candidates.append(
            {
                "path": models_subpath,
                "present": found["present"],
                "found_folders": found["found_folders"],
                "aliases": found["aliases"],
                "missing": found["missing"],
            }
        )
        if suggestion_base is None:
            suggestion_base = models_subpath
            suggestion_found = found["found_by_canonical"]

    return candidates, suggestion_base, suggestion_found


def _comfyui_model_folder_match(name: str) -> tuple[str, bool] | None:
    folder = str(name or "").strip().lower()
    if folder in COMFYUI_MODEL_FOLDERS:
        return folder, False
    canonical = COMFYUI_MODEL_FOLDER_ALIASES.get(folder)
    if canonical:
        return canonical, True
    return None


def _find_child_dir(children: list[Path], name: str) -> Path | None:
    wanted = name.lower()
    for child in children:
        if child.name.lower() == wanted:
            return child
    return None


def _join_posix(*parts: str | None) -> str:
    return "/".join(str(part).strip("/") for part in parts if str(part or "").strip("/"))


def _reject_non_copy_policy(policy: Mapping[str, Any]) -> None:
    for key, value in policy.items():
        normalized_key = str(key).strip().lower().replace("-", "_")
        if normalized_key in _COPY_ESCAPE_KEYS:
            raise ValueError("Transfer supports copy only")
        if any(token in normalized_key for token in ("delete", "move", "sync")):
            raise ValueError("Transfer supports copy only")
        if isinstance(value, str) and value.strip().lower().replace("_", "-") in _COPY_ESCAPE_VALUES:
            raise ValueError("Transfer supports copy only")


def _positive_int(value: Any, default: int, *, maximum: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(maximum, parsed))


def _normalize_bwlimit(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "\x00" in text or "\n" in text or text.startswith("-") or not _BWLIMIT_RE.fullmatch(text):
        raise ValueError("Invalid transfer bandwidth limit")
    return text


def _normalize_include_patterns(value: Any) -> list[str]:
    patterns = _list_value(value)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_pattern in patterns:
        pattern = str(raw_pattern or "").strip()
        if not pattern:
            continue
        if "\x00" in pattern or "\n" in pattern or "\\" in pattern or pattern.startswith("-"):
            raise ValueError("Invalid transfer include pattern")
        if pattern not in seen:
            normalized.append(pattern)
            seen.add(pattern)
    return normalized


def _normalize_policy_category(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "-")
    if not text:
        return ""
    if "\x00" in text or "\n" in text or "\\" in text or text.startswith("-"):
        raise ValueError("Invalid transfer policy category")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", text):
        raise ValueError("Invalid transfer policy category")
    return text


def _normalize_comfyui_mappings(value: Any) -> dict[str, str]:
    if value is None or value == "":
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("Transfer ComfyUI mappings must be an object")
    mappings: dict[str, str] = {}
    for raw_source_prefix, raw_destination in value.items():
        source_prefix = normalize_remote_path(str(raw_source_prefix or ""))
        if not source_prefix:
            continue
        if not path_is_stable_diffusion_prefix(source_prefix):
            raise ValueError("Transfer ComfyUI mapping source must be under stable-diffusion")
        destination_subpath = validate_destination_subpath(str(raw_destination or ""))
        if destination_subpath:
            mappings[source_prefix] = destination_subpath
    return mappings


def path_is_stable_diffusion_prefix(path: str) -> bool:
    normalized = normalize_remote_path(path)
    return normalized == "stable-diffusion" or normalized.startswith("stable-diffusion/")


def _list_value(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple):
        return list(value)
    raise ValueError("Transfer policy value must be a list")


def _bool_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _normalize_strict_relative_path(value: str | None, label: str, *, allow_empty: bool) -> str:
    text = str(value or "").strip()
    if "\\" in text:
        raise ValueError(f"{label} must use forward slashes")
    if not text or text == ".":
        if allow_empty:
            return ""
        raise ValueError(f"{label} is required")
    if text.startswith("/") or text.startswith("~"):
        raise ValueError(f"{label} must be relative")

    parts: list[str] = []
    for raw_segment in text.split("/"):
        segment = raw_segment.strip()
        if segment in {"", "."}:
            continue
        if segment == "..":
            raise ValueError(f"{label} must not contain '..'")
        if "\x00" in segment or ":" in segment:
            raise ValueError(f"{label} contains an unsafe segment")
        parts.append(segment)
    if not parts and not allow_empty:
        raise ValueError(f"{label} is required")
    return "/".join(parts)


def _join_relative(root: Path, relative_path: str) -> Path:
    current = root
    for segment in str(relative_path or "").split("/"):
        if segment:
            current = current / segment
    return current


def _ensure_resolved_inside(path: Path, root: Path, message: str) -> None:
    if path == root:
        return
    if root not in path.parents:
        raise ValueError(message)


def _reject_symlink_components(path: Path, root: Path, message: str) -> None:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        try:
            relative_parts = path.resolve(strict=False).relative_to(root.resolve(strict=False)).parts
        except ValueError:
            return

    if root.exists() and root.is_symlink():
        raise ValueError(message)

    current = root
    for part in relative_parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(message)
        if not current.exists():
            break


def _bounded_limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(maximum, parsed))


def _direct_local_mount_child_dirs(path: Path) -> list[Path]:
    children: list[Path] = []
    try:
        entries = list(path.iterdir())
    except OSError as exc:
        raise ValueError(f"Local mount folder cannot be listed: {exc}") from exc
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                children.append(entry)
        except OSError:
            continue
    return sorted(children, key=lambda child: child.name.lower())


def _local_mount_child_payload(path: Path, base: Path) -> dict[str, Any]:
    has_children = _local_mount_has_child_dirs(path)
    return {
        "name": path.name,
        "path": _relative_to_base(path, base),
        "kind": "directory",
        "has_children": has_children,
        "children_loaded": not has_children,
    }


def _local_mount_has_child_dirs(path: Path) -> bool:
    try:
        for entry in path.iterdir():
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def _ensure_local_mount_destination_parent(destination: Path, base: Path, *, source_is_file: bool) -> None:
    base_resolved = base.resolve(strict=False)
    target = destination.parent if source_is_file else destination
    target_resolved = target.resolve(strict=False)
    _ensure_resolved_inside(target_resolved, base_resolved, "Local mount destination escapes target base")
    _reject_symlink_components(target, base, "Local mount destination must not traverse symlinks")
    if target.exists() and not target.is_dir():
        raise ValueError("Local mount destination parent is not a folder")
    existing = _nearest_existing_directory(target, base)
    _reject_symlink_components(existing, base, "Local mount destination must not traverse symlinks")


def _ensure_writable_destination(destination: Path, *, base: Path, source_is_file: bool) -> None:
    target = destination.parent if source_is_file else destination
    existing = _nearest_existing_directory(target, base)
    if not os.access(existing, os.W_OK | os.X_OK):
        raise ValueError("Local mount destination is not writable")


def _nearest_existing_directory(path: Path, stop: Path) -> Path:
    current = path
    while True:
        if current.exists():
            if not current.is_dir():
                raise ValueError("Local mount destination parent is not a folder")
            return current
        if current == stop or current.parent == current:
            return stop
        current = current.parent


def _iter_transfer_source_files(source: Path, policy: Mapping[str, Any]) -> Iterator[_TransferSourceFile]:
    include_patterns = [str(pattern) for pattern in policy.get("include_patterns") or []]
    if source.is_file():
        if include_patterns and not _matches_include(source, source.name, include_patterns):
            return
        yield _TransferSourceFile(source, Path(source.name), source.stat().st_size)
        return

    stack: list[tuple[Path, Path]] = [(source, Path(""))]
    while stack:
        current, relative_parent = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda child: child.name.lower())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                relative_path = relative_parent / entry.name
                if entry.is_dir():
                    stack.append((entry, relative_path))
                    continue
                if not entry.is_file():
                    continue
                relative_name = relative_path.as_posix()
                if include_patterns and not _matches_include(entry, relative_name, include_patterns):
                    continue
                yield _TransferSourceFile(entry, relative_path, entry.stat().st_size)
            except OSError:
                continue


def _matches_include(path: Path, relative_name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative_name, pattern) for pattern in patterns)


def _copy_source_file_to_local_mount(
    source: Path,
    destination: Path,
    *,
    base: Path,
    job_id: int | str | None,
    skip_existing: bool,
) -> str:
    base_resolved = base.resolve(strict=False)
    destination_resolved = destination.resolve(strict=False)
    _ensure_resolved_inside(destination_resolved, base_resolved, "Local mount destination escapes target base")
    _reject_symlink_components(destination.parent, base, "Local mount destination must not traverse symlinks")

    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(destination.parent, base, "Local mount destination must not traverse symlinks")
    if destination.exists():
        if destination.is_symlink():
            raise ValueError("Local mount destination file must not be a symlink")
        if skip_existing:
            return "skipped_existing"
        raise ValueError("Local mount destination file already exists")

    temp_path = _temporary_destination_path(destination, job_id)
    try:
        with source.open("rb") as source_handle, temp_path.open("xb") as temp_handle:
            shutil.copyfileobj(source_handle, temp_handle, length=1024 * 1024)
        try:
            shutil.copystat(source, temp_path, follow_symlinks=False)
        except OSError:
            pass
        return _finalize_local_mount_temp_file(temp_path, destination, skip_existing=skip_existing)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _finalize_local_mount_temp_file(temp_path: Path, destination: Path, *, skip_existing: bool) -> str:
    try:
        os.link(temp_path, destination)
    except FileExistsError:
        if skip_existing:
            return "skipped_existing"
        raise ValueError("Local mount destination file already exists")
    except OSError:
        if destination.exists():
            if skip_existing:
                return "skipped_existing"
            raise ValueError("Local mount destination file already exists")
        temp_path.rename(destination)
        return "copied"
    return "copied"


def _temporary_destination_path(destination: Path, job_id: int | str | None) -> Path:
    job_part = str(job_id or "copy").strip() or "copy"
    token = uuid.uuid4().hex
    return destination.with_name(f".{destination.name}.part.{job_part}.{token}.tmp")


def _relative_to_base(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.resolve(strict=False).relative_to(base.resolve(strict=False)).as_posix()


def _safe_source_name(source: Path) -> str:
    name = source.name
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name or ":" in name:
        raise ValueError("Transfer source has an unsafe name")
    return name
