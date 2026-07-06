from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .utils import redact_sensitive_text

DEFAULT_RCLONE_CONFIG = "/config/rclone/rclone.conf"
TARGET_KIND_RCLONE = "rclone"
TARGET_KIND_RECEIVER = "receiver"
TRANSFER_DEFAULT_TRANSFERS = 1
TRANSFER_DEFAULT_CHECKERS = 2
TRANSFER_DEFAULT_BWLIMIT = "40M"
TRANSFER_RECEIVER_TIMEOUT_DEFAULT_SECONDS = 300
TRANSFER_RECEIVER_TIMEOUT_MAX_SECONDS = 3600
TRANSFER_MAX_TRANSFERS = 4
TRANSFER_MAX_CHECKERS = 8
TRANSFER_MAX_CONCURRENT_DEFAULT = 1
TRANSFER_MAX_CONCURRENT_HARD_LIMIT = 4

_REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_BWLIMIT_RE = re.compile(r"^(?:off|[0-9]+(?:\.[0-9]+)?[bBkKmMgGtTpP]?)$")
_SENSITIVE_USERINFO_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@")

_POLICY_KEYS = {
    "allowed_source_prefixes",
    "bwlimit",
    "checkers",
    "include_patterns",
    "preserve_folder_name",
    "require_check",
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


def validate_target_kind(value: str | None) -> str:
    kind = str(value or TARGET_KIND_RCLONE).strip().lower()
    if kind not in {TARGET_KIND_RCLONE, TARGET_KIND_RECEIVER}:
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
        "checkers": _positive_int(raw_policy.get("checkers"), checkers_default, maximum=TRANSFER_MAX_CHECKERS),
        "include_patterns": include_patterns,
        "preserve_folder_name": _bool_value(raw_policy.get("preserve_folder_name"), default=True),
        "require_check": _bool_value(raw_policy.get("require_check"), default=False),
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


def _safe_source_name(source: Path) -> str:
    name = source.name
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name or ":" in name:
        raise ValueError("Transfer source has an unsafe name")
    return name
