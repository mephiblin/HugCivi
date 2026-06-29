from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Literal

SourceType = Literal["huggingface", "civitai", "generic", "comfyui"]


@dataclass
class ParsedDownload:
    source: SourceType
    raw_input: str
    target_subdir: str | None = None

    # Hugging Face
    repo_id: str | None = None
    repo_type: str = "model"
    revision: str | None = None
    filenames: list[str] = field(default_factory=list)
    include_patterns: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)

    # Civitai
    civitai_model_id: str | None = None
    civitai_version_id: str | None = None
    civitai_hash: str | None = None
    civitai_download_url: str | None = None
    civitai_file_id: str | None = None
    civitai_file_type: str | None = None
    civitai_file_format: str | None = None
    civitai_file_size: str | None = None
    civitai_file_fp: str | None = None
    civitai_file_primary: bool = False

    # Generic URL
    url: str | None = None

    # ComfyUI workflow
    comfyui_workflow_url: str | None = None
    comfyui_workflow_filename: str | None = None
    comfyui_workflow_format: str | None = None
    comfyui_workflow_json: dict[str, Any] | None = None
    comfyui_workflow_metadata_key: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "ParsedDownload":
        allowed = {field.name for field in fields(ParsedDownload)}
        return ParsedDownload(**{key: value for key, value in data.items() if key in allowed})
