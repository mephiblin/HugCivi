from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse, unquote

SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._()\-가-힣]+")
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "api-key",
    "auth",
    "authorization",
    "client_secret",
    "client-secret",
    "civitai_token",
    "hf_token",
    "key",
    "key-pair-id",
    "password",
    "passwd",
    "policy",
    "secret",
    "sig",
    "signature",
    "token",
    "x-amz-credential",
    "x-amz-security-token",
    "x-amz-signature",
    "x-goog-credential",
    "x-goog-signature",
}
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:access_token|api[_-]?key|authorization|client[_-]?secret|civitai_token|hf_token|password|passwd|secret|token)\b\s*[=:]\s*)([^\s&]+)"
)
BEARER_RE = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]+)")


def sanitize_segment(value: str, default: str = "item") -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = unquote(value).strip().replace("/", "_").replace("\\", "_")
    value = SAFE_CHARS_RE.sub("_", value).strip("._ ")
    return value[:180] or default


def safe_join(root: str | Path, *parts: str) -> Path:
    root_path = Path(root).resolve()
    current = Path(root)
    for part in parts:
        if not part:
            continue
        for segment in str(part).replace("\\", "/").split("/"):
            if segment in ("", ".", ".."):
                continue
            current = current / sanitize_segment(segment)
    resolved = current.resolve()
    if root_path not in resolved.parents and resolved != root_path:
        raise ValueError("Target path escapes data root")
    return current


def human_bytes(num: int | float | None) -> str:
    if num is None:
        return "-"
    num = float(num)
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} EB"


def redact_sensitive_text(value: str | None) -> str | None:
    if value is None:
        return None

    text = str(value)
    text = BEARER_RE.sub(r"\1[REDACTED]", text)
    text = SENSITIVE_ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
    return redact_url_query(text)


def redact_url_query(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        url = match.group(0)
        parsed = urlparse(url)
        if not parsed.query:
            return url
        query = [
            (key, "[REDACTED]" if key.lower() in SENSITIVE_QUERY_KEYS else item_value)
            for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        return urlunparse(parsed._replace(query=urlencode(query, safe="[]")))

    return re.sub(r"https?://[^\s\"'<>]+", replace, value)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}
