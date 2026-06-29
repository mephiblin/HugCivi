from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal

SourceType = Literal["huggingface", "civitai", "generic"]


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
    civitai_download_url: str | None = None

    # Generic URL
    url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "ParsedDownload":
        return ParsedDownload(**data)
