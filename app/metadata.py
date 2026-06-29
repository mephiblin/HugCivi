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
CIVITAI_MODEL_FILE_TYPES = {"model", "negative", "diffusion model", "unet"}


def slug(value: str | None, default: str = "unknown") -> str:
    return sanitize_segment((value or default).lower().replace(" ", "-"), default)


def classify_huggingface(metadata: dict[str, Any], repo_type: str, repo_id: str) -> dict[str, Any]:
    route_type = None
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
            route_type = "embedding"
        elif pipeline in IMAGE_PIPELINES or "stable-diffusion" in name_blob or "diffusers" in tags:
            category = "Image Checkpoint"
            category_slug = "image/checkpoints"
            route_type = "checkpoint"
        elif pipeline in LLM_PIPELINES or ".gguf" in name_blob or "causallm" in name_blob:
            category = "LLM"
            category_slug = "llm"
            route_type = "llm"
        elif pipeline in VISION_PIPELINES:
            category = "Vision"
            category_slug = "vision"
        elif pipeline in AUDIO_PIPELINES:
            category = "Audio"
            category_slug = "audio"
        else:
            category = "Model"
            category_slug = "models"

    raw_config = metadata.get("config")
    config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
    raw_card_data = metadata.get("cardData")
    card_data: dict[str, Any] = raw_card_data if isinstance(raw_card_data, dict) else {}
    filenames = [str(item.get("rfilename") or "") for item in metadata.get("siblings") or []]
    thumbnail = card_data.get("thumbnail")
    if not isinstance(thumbnail, str):
        thumbnail = None

    repo_slug = slug(repo_id.replace("/", "__"), "repo")
    return {
        "model_title": metadata.get("modelId") or metadata.get("id") or repo_id,
        "model_category": category,
        "model_type": metadata.get("pipeline_tag") or config.get("model_type") or repo_type,
        "base_model": infer_hf_base_model(metadata),
        "file_format": infer_format(filenames),
        "precision": infer_precision(filenames, metadata),
        "thumbnail_url": thumbnail,
        "route_type": route_type,
        "target_suffix": [repo_slug],
        "target_parts": ["huggingface", category_slug, repo_slug],
    }


def classify_civitai(
    metadata: dict[str, Any],
    version_id: str,
    model_name: str | None,
    file_selector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_model = metadata.get("model")
    model = raw_model if isinstance(raw_model, dict) else {}
    raw_files = metadata.get("files")
    files = raw_files if isinstance(raw_files, list) else []
    primary_file = pick_civitai_file(files, file_selector)
    raw_file_metadata = primary_file.get("metadata")
    file_metadata = raw_file_metadata if isinstance(raw_file_metadata, dict) else {}
    parent_type = str(model.get("type") or metadata.get("type") or "")
    selected_file_type = str(primary_file.get("type") or "")
    if selected_file_type and normalized_text(selected_file_type) != "model":
        model_type = selected_file_type
    else:
        model_type = parent_type or selected_file_type or "Model"
    base_model = str(metadata.get("baseModel") or "unknown")
    raw_images = metadata.get("images")
    images = raw_images if isinstance(raw_images, list) else []
    thumbnail = pick_thumbnail(images)

    type_slug = {
        "checkpoint": "checkpoints",
        "lora": "loras",
        "locon": "loras",
        "textualinversion": "embeddings",
        "embedding": "embeddings",
        "vae": "vae",
        "controlnet": "controlnet",
        "diffusionmodel": "diffusion_models",
        "unet": "diffusion_models",
        "poses": "poses",
        "upscaler": "upscalers",
        "motionmodule": "motion-modules",
    }.get(model_type.replace(" ", "").lower(), slug(model_type, "models"))
    route_type = {
        "checkpoints": "checkpoint",
        "loras": "lora",
        "embeddings": "embedding",
        "vae": "vae",
        "controlnet": "controlnet",
        "diffusion_models": "diffusion_model",
        "upscalers": "upscaler",
    }.get(type_slug)
    target_suffix = [slug(base_model), slug(model_name or f"version_{version_id}"), f"version_{version_id}"]

    return {
        "model_title": model_name or model.get("name") or f"version_{version_id}",
        "model_category": display_civitai_category(model_type),
        "model_type": model_type,
        "base_model": base_model,
        "file_format": file_metadata.get("format") or infer_format([str(primary_file.get("name") or "")]),
        "precision": file_metadata.get("fp") or file_metadata.get("size") or infer_precision([str(primary_file.get("name") or "")], metadata),
        "thumbnail_url": thumbnail,
        "route_type": route_type,
        "target_suffix": target_suffix,
        "target_parts": ["civitai", type_slug, *target_suffix],
        "primary_file": primary_file,
    }


def pick_primary_file(files: list[Any]) -> dict[str, Any]:
    return pick_civitai_file(files)


def pick_civitai_file(files: list[Any], selector: dict[str, Any] | None = None) -> dict[str, Any]:
    dict_files = [item for item in files if isinstance(item, dict)]
    selector = selector or {}
    file_id = normalized_text(selector.get("file_id"))
    requested_type = normalized_text(selector.get("type"))
    requested_format = normalized_text(selector.get("format"))
    requested_size = normalized_text(selector.get("size"))
    requested_fp = normalized_text(selector.get("fp"))
    has_explicit_selector = bool(file_id or requested_type or requested_format or requested_size or requested_fp)

    if file_id:
        for item in dict_files:
            if normalized_text(item.get("id")) == file_id:
                return item

    if requested_type or requested_format or requested_size or requested_fp:
        for item in dict_files:
            raw_metadata = item.get("metadata")
            metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            if requested_type and normalized_text(item.get("type")) != requested_type:
                continue
            if requested_format and normalized_text(metadata.get("format")) != requested_format:
                continue
            if requested_size and normalized_text(metadata.get("size")) != requested_size:
                continue
            if requested_fp and normalized_text(metadata.get("fp")) != requested_fp:
                continue
            return item

    if has_explicit_selector:
        return {}

    if selector.get("primary"):
        for item in dict_files:
            if item.get("primary") and normalized_text(item.get("type")) in CIVITAI_MODEL_FILE_TYPES:
                return item

    for item in dict_files:
        if item.get("primary") and normalized_text(item.get("type")) in CIVITAI_MODEL_FILE_TYPES:
            return item
    for item in dict_files:
        if normalized_text(item.get("type")) in CIVITAI_MODEL_FILE_TYPES:
            return item
    for item in dict_files:
        if item.get("primary"):
            return item
    return dict_files[0] if dict_files else {}


def normalized_text(value: Any) -> str:
    return str(value or "").strip().lower()


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
    raw_config = metadata.get("config")
    config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
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
