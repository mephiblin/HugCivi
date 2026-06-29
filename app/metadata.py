from __future__ import annotations

import re
from typing import Any

from .utils import sanitize_segment

LLM_PIPELINES = {
    "text-generation",
    "text2text-generation",
    "conversational",
    "question-answering",
    "summarization",
    "translation",
}
EMBEDDING_PIPELINES = {"feature-extraction", "sentence-similarity"}
IMAGE_PIPELINES = {
    "text-to-image",
    "image-to-image",
    "image-to-3d",
    "unconditional-image-generation",
}
VISION_PIPELINES = {"image-classification", "object-detection", "image-segmentation", "zero-shot-image-classification"}
AUDIO_PIPELINES = {"automatic-speech-recognition", "text-to-speech", "audio-classification", "audio-to-audio"}

QUANT_RE = re.compile(r"\b(?:q[2-8](?:_[a-z0-9]+)*|int[248]|fp(?:8|16|32)|bf16|nf4|gguf)\b", re.IGNORECASE)


def slug(value: str | None, default: str = "unknown") -> str:
    return sanitize_segment((value or default).lower().replace(" ", "-"), default)


def classify_huggingface(metadata: dict[str, Any], repo_type: str, repo_id: str) -> dict[str, Any]:
    if repo_type == "dataset":
        category = "Dataset"
        category_slug = "datasets"
    elif repo_type == "space":
        category = "Space"
        category_slug = "spaces"
    else:
        pipeline = str(metadata.get("pipeline_tag") or "").lower()
        tags = [str(tag).lower() for tag in metadata.get("tags") or []]
        filenames = [str(item.get("rfilename") or "") for item in metadata.get("siblings") or []]
        name_blob = " ".join([pipeline, *tags, *filenames]).lower()

        if pipeline in EMBEDDING_PIPELINES or "sentence-transformers" in tags or "embedding" in name_blob:
            category = "Embedding"
            category_slug = "embeddings"
        elif pipeline in IMAGE_PIPELINES or "stable-diffusion" in name_blob or "diffusers" in tags:
            category = "Image Checkpoint"
            category_slug = "image/checkpoints"
        elif pipeline in LLM_PIPELINES or ".gguf" in name_blob or "causallm" in name_blob:
            category = "LLM"
            category_slug = "llm"
        elif pipeline in VISION_PIPELINES:
            category = "Vision"
            category_slug = "vision"
        elif pipeline in AUDIO_PIPELINES:
            category = "Audio"
            category_slug = "audio"
        else:
            category = "Model"
            category_slug = "models"

    config = metadata.get("config") if isinstance(metadata.get("config"), dict) else {}
    card_data = metadata.get("cardData") if isinstance(metadata.get("cardData"), dict) else {}
    filenames = [str(item.get("rfilename") or "") for item in metadata.get("siblings") or []]
    thumbnail = card_data.get("thumbnail")
    if not isinstance(thumbnail, str):
        thumbnail = None

    return {
        "model_title": metadata.get("modelId") or metadata.get("id") or repo_id,
        "model_category": category,
        "model_type": metadata.get("pipeline_tag") or config.get("model_type") or repo_type,
        "base_model": infer_hf_base_model(metadata),
        "file_format": infer_format(filenames),
        "precision": infer_precision(filenames, metadata),
        "thumbnail_url": thumbnail,
        "target_parts": ["huggingface", category_slug, slug(repo_id.replace("/", "__"), "repo")],
    }


def classify_civitai(metadata: dict[str, Any], version_id: str, model_name: str | None) -> dict[str, Any]:
    model = metadata.get("model") if isinstance(metadata.get("model"), dict) else {}
    files = metadata.get("files") if isinstance(metadata.get("files"), list) else []
    primary_file = pick_primary_file(files)
    file_metadata = primary_file.get("metadata") if isinstance(primary_file.get("metadata"), dict) else {}
    model_type = str(model.get("type") or metadata.get("type") or primary_file.get("type") or "Model")
    base_model = str(metadata.get("baseModel") or "unknown")
    images = metadata.get("images") if isinstance(metadata.get("images"), list) else []
    thumbnail = pick_thumbnail(images)

    type_slug = {
        "checkpoint": "checkpoints",
        "lora": "loras",
        "locon": "loras",
        "textualinversion": "embeddings",
        "embedding": "embeddings",
        "vae": "vae",
        "controlnet": "controlnet",
        "poses": "poses",
        "upscaler": "upscalers",
        "motionmodule": "motion-modules",
    }.get(model_type.replace(" ", "").lower(), slug(model_type, "models"))

    return {
        "model_title": model_name or model.get("name") or f"version_{version_id}",
        "model_category": display_civitai_category(model_type),
        "model_type": model_type,
        "base_model": base_model,
        "file_format": file_metadata.get("format") or infer_format([str(primary_file.get("name") or "")]),
        "precision": file_metadata.get("fp") or file_metadata.get("size") or infer_precision([str(primary_file.get("name") or "")], metadata),
        "thumbnail_url": thumbnail,
        "target_parts": ["civitai", type_slug, slug(base_model), slug(model_name or f"version_{version_id}"), f"version_{version_id}"],
        "primary_file": primary_file,
    }


def pick_primary_file(files: list[Any]) -> dict[str, Any]:
    dict_files = [item for item in files if isinstance(item, dict)]
    for item in dict_files:
        if item.get("primary"):
            return item
    for item in dict_files:
        if str(item.get("type") or "").lower() == "model":
            return item
    return dict_files[0] if dict_files else {}


def pick_thumbnail(images: list[Any]) -> str | None:
    for image in images:
        if not isinstance(image, dict):
            continue
        if image.get("url") and image.get("type") in {None, "image"}:
            return thumbnail_url(str(image["url"]))
    return None


def thumbnail_url(url: str) -> str:
    return url.replace("/original=true/", "/width=256/")


def display_civitai_category(model_type: str) -> str:
    normalized = model_type.replace(" ", "").lower()
    if normalized == "checkpoint":
        return "Image Checkpoint"
    if normalized in {"lora", "locon"}:
        return "LoRA"
    if normalized in {"textualinversion", "embedding"}:
        return "Embedding"
    return model_type


def infer_hf_base_model(metadata: dict[str, Any]) -> str | None:
    tags = [str(tag) for tag in metadata.get("tags") or []]
    config = metadata.get("config") if isinstance(metadata.get("config"), dict) else {}
    for tag in tags:
        lower = tag.lower()
        if lower.startswith("base_model:"):
            return tag.split(":", 1)[1]
        if lower in {"sdxl", "stable-diffusion-xl", "stable-diffusion", "flux", "llama", "mistral", "qwen", "gemma"}:
            return tag
    return config.get("model_type")


def infer_format(filenames: list[str]) -> str | None:
    formats = []
    for filename in filenames:
        lower = filename.lower()
        if lower.endswith(".safetensors"):
            formats.append("SafeTensor")
        elif lower.endswith(".gguf"):
            formats.append("GGUF")
        elif lower.endswith(".bin"):
            formats.append("PyTorch BIN")
        elif lower.endswith(".onnx"):
            formats.append("ONNX")
        elif lower.endswith(".ckpt"):
            formats.append("Checkpoint")
    return formats[0] if formats else None


def infer_precision(filenames: list[str], metadata: dict[str, Any] | None = None) -> str | None:
    for filename in filenames:
        match = QUANT_RE.search(filename)
        if match:
            return match.group(0).upper()
    safetensors = metadata.get("safetensors") if isinstance(metadata, dict) else None
    if isinstance(safetensors, dict):
        parameters = safetensors.get("parameters")
        if isinstance(parameters, dict) and parameters:
            return ", ".join(sorted(str(key) for key in parameters.keys()))
    return None
