from __future__ import annotations

import re
import shlex
from urllib.parse import parse_qs, unquote, urlparse

from .models import ParsedDownload
from .workflows import COMFYUI_WORKFLOW_EXTENSIONS

CIVITAI_VERSION_ID_RE = re.compile(r"^\d{3,}$")
CIVITAI_HOSTS = {
    "civitai.com",
    "www.civitai.com",
    "civitai.red",
    "www.civitai.red",
    "civitai.green",
    "www.civitai.green",
}
COMFYUI_EXPLICIT_COMMANDS = {"workflow", "workflows", "comfyui", "comfyui-workflow", "comfyui-workflows"}
COMFYUI_DOWNLOAD_COMMANDS = {"curl", "wget", "aria2c"}
COMFYUI_SUBCOMMANDS = {"workflow", "workflows", "download"}
COMFYUI_URL_HINTS = ("comfyui", "comfy-ui", "workflow", "workflows")


class InputParseError(ValueError):
    pass


def parse_input(raw_input: str, target_subdir: str | None = None) -> ParsedDownload:
    text = (raw_input or "").strip()
    if not text:
        raise InputParseError("입력값이 비어 있습니다.")

    if text.startswith("hf://"):
        return parse_hf_uri(text, raw_input=text, target_subdir=target_subdir)

    if text.startswith("hf ") or text.startswith("huggingface-cli "):
        return parse_hf_cli(text, target_subdir=target_subdir)

    comfyui_cli = maybe_parse_comfyui_cli(text, target_subdir=target_subdir)
    if comfyui_cli is not None:
        return comfyui_cli

    if CIVITAI_VERSION_ID_RE.match(text):
        return ParsedDownload(
            source="civitai",
            raw_input=text,
            target_subdir=target_subdir,
            civitai_version_id=text,
        )

    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"}:
        host = parsed.netloc.lower()
        if host in {"huggingface.co", "www.huggingface.co", "hf.co", "www.hf.co"}:
            return parse_huggingface_url(text, target_subdir=target_subdir)
        if is_civitai_host(host):
            return parse_civitai_url(text, target_subdir=target_subdir)
        if is_comfyui_workflow_url(text, require_hint=True):
            return parse_comfyui_workflow_url(text, target_subdir=target_subdir)
        return ParsedDownload(source="generic", raw_input=text, target_subdir=target_subdir, url=text)

    # Convenient shorthand for HF repo IDs: owner/repo
    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", text):
        return ParsedDownload(
            source="huggingface",
            raw_input=text,
            target_subdir=target_subdir,
            repo_id=text,
            repo_type="model",
        )

    raise InputParseError(
        "지원하지 않는 입력입니다. Hugging Face URL, Civitai URL, 일반 다운로드 URL, "
        "또는 안전한 `hf download ...` 형태를 입력하세요."
    )


def maybe_parse_comfyui_cli(text: str, target_subdir: str | None = None) -> ParsedDownload | None:
    match = re.match(r"^\s*([A-Za-z0-9_.-]+)", text)
    if not match:
        return None

    command = match.group(1).lower()
    if command not in COMFYUI_EXPLICIT_COMMANDS and command not in COMFYUI_DOWNLOAD_COMMANDS and command != "comfy":
        return None

    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise InputParseError(f"CLI 파싱 실패: {exc}") from exc
    if not tokens:
        return None

    command = tokens[0].lower()
    start = 1
    explicit = command in COMFYUI_EXPLICIT_COMMANDS
    if command == "comfy":
        if len(tokens) < 2 or tokens[1].lower() not in COMFYUI_SUBCOMMANDS:
            return None
        explicit = True
        start = 2
    elif command in COMFYUI_DOWNLOAD_COMMANDS:
        explicit = False

    for token in tokens[start:]:
        if token.startswith("-") or token.startswith("@"):
            continue
        if is_comfyui_workflow_url(token, require_hint=not explicit):
            return parse_comfyui_workflow_url(token, raw_input=text, target_subdir=target_subdir)

    if explicit:
        raise InputParseError("ComfyUI workflow 입력에서 .json 또는 .png URL을 찾지 못했습니다.")
    return None


def parse_comfyui_workflow_url(
    url: str,
    raw_input: str | None = None,
    target_subdir: str | None = None,
) -> ParsedDownload:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise InputParseError("ComfyUI workflow URL은 HTTP 또는 HTTPS URL이어야 합니다.")

    filename = comfyui_workflow_filename(url)
    workflow_format = comfyui_workflow_format(filename or "")
    if workflow_format is None:
        raise InputParseError("ComfyUI workflow URL은 .json 또는 .png 파일이어야 합니다.")

    return ParsedDownload(
        source="comfyui",
        raw_input=raw_input or url,
        target_subdir=target_subdir,
        comfyui_workflow_url=url,
        comfyui_workflow_filename=filename,
        comfyui_workflow_format=workflow_format,
    )


def is_comfyui_workflow_url(url: str, *, require_hint: bool = True) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if comfyui_workflow_format(comfyui_workflow_filename(url) or "") is None:
        return False
    return not require_hint or has_comfyui_workflow_hint(url)


def has_comfyui_workflow_hint(url: str) -> bool:
    parsed = urlparse(url)
    haystack = unquote(f"{parsed.netloc} {parsed.path} {parsed.query}").lower()
    return any(hint in haystack for hint in COMFYUI_URL_HINTS)


def comfyui_workflow_filename(url: str) -> str | None:
    parsed = urlparse(url)
    path_name = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])
    candidates = [path_name] if path_name else []

    query = parse_qs(parsed.query)
    for key in ("filename", "file", "name", "download"):
        values = query.get(key)
        if values:
            candidates.extend(unquote(value) for value in values if value)

    for candidate in candidates:
        if comfyui_workflow_format(candidate) is not None:
            return candidate.rsplit("/", 1)[-1]
    return path_name or None


def comfyui_workflow_format(filename: str) -> str | None:
    lower = filename.lower().split("?", 1)[0]
    for extension in COMFYUI_WORKFLOW_EXTENSIONS:
        if lower.endswith(extension):
            return extension.removeprefix(".")
    return None


def parse_huggingface_url(url: str, target_subdir: str | None = None) -> ParsedDownload:
    u = urlparse(url)
    parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise InputParseError("Hugging Face URL에서 repo_id를 찾지 못했습니다.")

    repo_type = "model"
    offset = 0
    if parts[0] in {"datasets", "dataset"}:
        repo_type = "dataset"
        offset = 1
    elif parts[0] in {"spaces", "space"}:
        repo_type = "space"
        offset = 1
    elif parts[0] in {"models", "model"}:
        repo_type = "model"
        offset = 1

    if len(parts) < offset + 2:
        raise InputParseError("Hugging Face URL에서 owner/repo를 찾지 못했습니다.")

    repo_id = f"{parts[offset]}/{parts[offset + 1]}"
    remaining = parts[offset + 2 :]
    revision = None
    filenames: list[str] = []

    if remaining and remaining[0] in {"resolve", "blob", "raw", "tree"}:
        # Most URLs are /resolve/main/path or /blob/main/path.
        # Branch names containing slashes are ambiguous in plain URLs; hf:// is preferred for those.
        if len(remaining) >= 2:
            revision = remaining[1]
        if len(remaining) >= 3 and remaining[0] != "tree":
            filenames = ["/".join(remaining[2:])]
    elif remaining:
        filenames = ["/".join(remaining)]

    return ParsedDownload(
        source="huggingface",
        raw_input=url,
        target_subdir=target_subdir,
        repo_id=repo_id,
        repo_type=repo_type,
        revision=revision,
        filenames=filenames,
    )


def parse_hf_uri(uri: str, raw_input: str | None = None, target_subdir: str | None = None) -> ParsedDownload:
    if not uri.startswith("hf://"):
        raise InputParseError("hf:// URI가 아닙니다.")
    body = uri[len("hf://") :]
    parts = [unquote(p) for p in body.split("/") if p]
    if not parts:
        raise InputParseError("hf:// URI에서 repo_id를 찾지 못했습니다.")

    repo_type = "model"
    idx = 0
    if parts[0] in {"models", "model"}:
        repo_type = "model"
        idx = 1
    elif parts[0] in {"datasets", "dataset"}:
        repo_type = "dataset"
        idx = 1
    elif parts[0] in {"spaces", "space"}:
        repo_type = "space"
        idx = 1

    if len(parts) < idx + 2:
        raise InputParseError("hf:// URI에서 owner/repo를 찾지 못했습니다.")

    owner = parts[idx]
    repo_and_revision = parts[idx + 1]
    revision = None
    if "@" in repo_and_revision:
        repo_name, revision = repo_and_revision.split("@", 1)
    else:
        repo_name = repo_and_revision
    repo_id = f"{owner}/{repo_name}"
    filename = "/".join(parts[idx + 2 :]) if len(parts) > idx + 2 else None

    return ParsedDownload(
        source="huggingface",
        raw_input=raw_input or uri,
        target_subdir=target_subdir,
        repo_id=repo_id,
        repo_type=repo_type,
        revision=revision,
        filenames=[filename] if filename else [],
    )


def parse_hf_cli(text: str, target_subdir: str | None = None) -> ParsedDownload:
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise InputParseError(f"CLI 파싱 실패: {exc}") from exc

    if len(tokens) < 3 or tokens[1] != "download":
        raise InputParseError("지원되는 CLI는 `hf download ...` 또는 `huggingface-cli download ...` 형태입니다.")

    repo_type = "model"
    revision = None
    include_patterns: list[str] = []
    exclude_patterns: list[str] = []
    positional: list[str] = []

    i = 2
    while i < len(tokens):
        token = tokens[i]
        if token in {"--repo-type", "--revision", "--include", "--exclude", "--local-dir", "--cache-dir", "--token"}:
            if i + 1 >= len(tokens):
                raise InputParseError(f"{token} 옵션 값이 없습니다.")
            value = tokens[i + 1]
            if token == "--repo-type":
                repo_type = value
            elif token == "--revision":
                revision = value
            elif token == "--include":
                include_patterns.append(value)
            elif token == "--exclude":
                exclude_patterns.append(value)
            # --local-dir, --cache-dir, --token are intentionally ignored for NAS safety.
            i += 2
            continue
        if token.startswith("--repo-type="):
            repo_type = token.split("=", 1)[1]
        elif token.startswith("--revision="):
            revision = token.split("=", 1)[1]
        elif token.startswith("--include="):
            include_patterns.append(token.split("=", 1)[1])
        elif token.startswith("--exclude="):
            exclude_patterns.append(token.split("=", 1)[1])
        elif token.startswith("--local-dir=") or token.startswith("--cache-dir=") or token.startswith("--token="):
            pass
        elif token.startswith("-"):
            # Ignore harmless flags like --quiet or --dry-run; unsupported booleans are not executed anyway.
            pass
        else:
            positional.append(token)
        i += 1

    if not positional:
        raise InputParseError("`hf download` 뒤에 repo_id 또는 hf:// URI가 필요합니다.")

    repo_or_uri = positional[0]
    filenames = positional[1:]
    if repo_or_uri.startswith("hf://"):
        parsed = parse_hf_uri(repo_or_uri, raw_input=text, target_subdir=target_subdir)
        if filenames:
            parsed.filenames.extend(filenames)
        parsed.include_patterns.extend(include_patterns)
        parsed.exclude_patterns.extend(exclude_patterns)
        return parsed

    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo_or_uri):
        raise InputParseError("repo_id는 owner/repo 형태여야 합니다.")

    if repo_type in {"models", "model"}:
        repo_type = "model"
    elif repo_type in {"datasets", "dataset"}:
        repo_type = "dataset"
    elif repo_type in {"spaces", "space"}:
        repo_type = "space"
    else:
        raise InputParseError("repo_type은 model, dataset, space 중 하나여야 합니다.")

    return ParsedDownload(
        source="huggingface",
        raw_input=text,
        target_subdir=target_subdir,
        repo_id=repo_or_uri,
        repo_type=repo_type,
        revision=revision,
        filenames=filenames,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    )


def is_civitai_host(host: str) -> bool:
    return host.lower() in CIVITAI_HOSTS


def first_query(query: dict[str, list[str]], *keys: str) -> str | None:
    for key in keys:
        values = query.get(key)
        if values and values[0]:
            return values[0]
    return None


def query_bool(query: dict[str, list[str]], *keys: str) -> bool:
    value = first_query(query, *keys)
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def civitai_file_params(query: dict[str, list[str]]) -> dict:
    return {
        "civitai_file_id": first_query(query, "fileId", "file_id", "fileID"),
        "civitai_file_type": first_query(query, "type"),
        "civitai_file_format": first_query(query, "format"),
        "civitai_file_size": first_query(query, "size"),
        "civitai_file_fp": first_query(query, "fp"),
        "civitai_file_primary": query_bool(query, "primary", "isPrimary", "is_primary"),
    }


def parse_civitai_url(url: str, target_subdir: str | None = None) -> ParsedDownload:
    u = urlparse(url)
    parts = [p for p in u.path.strip("/").split("/") if p]
    query = parse_qs(u.query)
    file_params = civitai_file_params(query)

    version_id = None
    model_id = None

    for key in ("modelVersionId", "versionId"):
        if key in query and query[key]:
            version_id = query[key][0]
            break

    if len(parts) >= 4 and parts[0] == "api" and parts[1] == "download" and parts[2] == "models":
        version_id = parts[3]
        return ParsedDownload(
            source="civitai",
            raw_input=url,
            target_subdir=target_subdir,
            civitai_version_id=version_id,
            civitai_download_url=url,
            **file_params,
        )

    if (
        len(parts) >= 5
        and parts[0] == "api"
        and parts[1] == "v1"
        and parts[2] in {"model-versions", "modelVersions"}
        and parts[3] == "by-hash"
    ):
        return ParsedDownload(
            source="civitai",
            raw_input=url,
            target_subdir=target_subdir,
            civitai_hash=parts[4],
            **file_params,
        )

    if len(parts) >= 4 and parts[0] == "api" and parts[1] == "v1" and parts[2] in {"model-versions", "modelVersions"}:
        version_id = parts[3]
        return ParsedDownload(
            source="civitai",
            raw_input=url,
            target_subdir=target_subdir,
            civitai_version_id=version_id,
            **file_params,
        )

    if len(parts) >= 4 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "models":
        model_id = parts[3]
        return ParsedDownload(
            source="civitai",
            raw_input=url,
            target_subdir=target_subdir,
            civitai_model_id=model_id,
            **file_params,
        )

    if len(parts) >= 2 and parts[0] == "models":
        model_id = parts[1]
        return ParsedDownload(
            source="civitai",
            raw_input=url,
            target_subdir=target_subdir,
            civitai_model_id=model_id,
            civitai_version_id=version_id,
            **file_params,
        )

    raise InputParseError("Civitai URL에서 모델 ID 또는 버전 ID를 찾지 못했습니다.")
