from __future__ import annotations

import json
import os
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .utils import human_bytes, safe_join, sanitize_segment

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
COMFYUI_WORKFLOW_EXTENSIONS = (".json", ".png")
WORKFLOW_ROOT_PARTS = ("comfyui", "workflows")
WORKFLOW_MODEL_KEYS = {
    "ckpt_name",
    "checkpoint",
    "clip_name",
    "control_net_name",
    "controlnet_name",
    "lora_name",
    "model",
    "model_name",
    "unet_name",
    "vae_name",
}
WORKFLOW_MODEL_EXTENSIONS = (
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".bin",
    ".gguf",
    ".onnx",
)


class WorkflowParseError(ValueError):
    pass


def workflow_max_bytes() -> int:
    raw = os.getenv("WORKFLOW_IMPORT_MAX_BYTES", str(100 * 1024 * 1024))
    try:
        return max(1024 * 1024, int(raw))
    except ValueError:
        return 100 * 1024 * 1024


def extract_workflow_bundle(data: bytes, filename: str) -> dict[str, Any]:
    suffix = Path(filename or "").suffix.lower()
    if data.startswith(PNG_SIGNATURE) or suffix == ".png":
        metadata = png_text_chunks(data)
        workflow, source_key = workflow_from_png_metadata(metadata)
        source_format = "PNG"
    else:
        payload = decode_json_bytes(data)
        workflow, source_key = workflow_from_payload(payload)
        metadata = {}
        source_format = "JSON"

    analysis = analyze_workflow(workflow)
    return {
        "workflow": workflow,
        "source_key": source_key,
        "source_format": source_format,
        "metadata_keys": sorted(metadata.keys()),
        **analysis,
    }


def extract_workflow_json_from_bytes(data: bytes, filename: str = "") -> dict[str, Any]:
    suffix = Path(filename or "").suffix.lower()
    if data.startswith(PNG_SIGNATURE) or suffix == ".png":
        return extract_workflow_json_from_png_metadata(png_text_chunks(data))
    return extract_workflow_json_from_json_bytes(data)


def extract_workflow_json_from_json_bytes(data: bytes) -> dict[str, Any]:
    workflow, _source_key = workflow_from_payload(decode_json_bytes(data))
    return ensure_workflow_dict(workflow)


def extract_workflow_json_from_png_metadata(metadata: dict[str, str]) -> dict[str, Any]:
    workflow, _source_key = workflow_from_png_metadata(metadata)
    return ensure_workflow_dict(workflow)


def workflow_json_to_storage_text(workflow: dict[str, Any]) -> str:
    return json.dumps(workflow, ensure_ascii=False, indent=2) + "\n"


def workflow_json_to_storage_bytes(workflow: dict[str, Any]) -> bytes:
    return workflow_json_to_storage_text(workflow).encode("utf-8")


def ensure_workflow_dict(workflow: Any) -> dict[str, Any]:
    if not isinstance(workflow, dict):
        raise WorkflowParseError("ComfyUI workflow JSON은 객체여야 합니다.")
    return workflow


def decode_json_bytes(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowParseError("ComfyUI workflow JSON을 읽지 못했습니다.") from exc


def workflow_from_payload(payload: Any) -> tuple[Any, str]:
    if isinstance(payload, dict):
        for key in ("workflow", "prompt"):
            if key in payload:
                nested = parse_json_value(payload[key], key)
                if looks_like_workflow(nested):
                    return nested, key
        extra = payload.get("extra_pnginfo")
        if isinstance(extra, dict):
            for key in ("workflow", "prompt"):
                if key in extra:
                    nested = parse_json_value(extra[key], f"extra_pnginfo.{key}")
                    if looks_like_workflow(nested):
                        return nested, f"extra_pnginfo.{key}"
        if looks_like_workflow(payload):
            return payload, "root"
    raise WorkflowParseError("ComfyUI workflow 구조를 찾지 못했습니다.")


def workflow_from_png_metadata(metadata: dict[str, str]) -> tuple[Any, str]:
    candidates = [
        key for key in metadata
        if key.lower() in {"workflow", "prompt", "comfyui_workflow"}
    ]
    candidates.sort(key=lambda key: 0 if key.lower() in {"workflow", "comfyui_workflow"} else 1)
    for key in candidates:
        try:
            payload = parse_json_value(metadata[key], key)
        except WorkflowParseError:
            continue
        if looks_like_workflow(payload):
            return payload, key
    raise WorkflowParseError("PNG 안에서 ComfyUI workflow metadata를 찾지 못했습니다.")


def parse_json_value(value: Any, key: str) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise WorkflowParseError(f"{key} metadata가 JSON 형식이 아닙니다.") from exc
    return value


def looks_like_workflow(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if isinstance(value.get("nodes"), list):
        return True
    if all(str(key).isdigit() and isinstance(item, dict) for key, item in value.items()):
        return True
    return any(key in value for key in ("last_node_id", "links", "groups", "extra"))


def png_text_chunks(data: bytes) -> dict[str, str]:
    if not data.startswith(PNG_SIGNATURE):
        raise WorkflowParseError("PNG 파일이 아닙니다.")
    chunks: dict[str, str] = {}
    offset = len(PNG_SIGNATURE)
    while offset + 8 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        chunk_type = data[offset + 4:offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        if chunk_end + 4 > len(data):
            break
        payload = data[chunk_start:chunk_end]
        try:
            if chunk_type == b"tEXt":
                key, text = parse_text_chunk(payload)
                chunks[key] = text
            elif chunk_type == b"zTXt":
                key, text = parse_ztxt_chunk(payload)
                chunks[key] = text
            elif chunk_type == b"iTXt":
                key, text = parse_itxt_chunk(payload)
                chunks[key] = text
        except (UnicodeDecodeError, zlib.error, ValueError):
            pass
        offset = chunk_end + 4
        if chunk_type == b"IEND":
            break
    return chunks


def parse_text_chunk(payload: bytes) -> tuple[str, str]:
    key_bytes, text_bytes = payload.split(b"\x00", 1)
    return key_bytes.decode("latin-1"), decode_text_value(text_bytes)


def parse_ztxt_chunk(payload: bytes) -> tuple[str, str]:
    key_bytes, rest = payload.split(b"\x00", 1)
    if not rest or rest[0] != 0:
        raise ValueError("unsupported zTXt compression method")
    return key_bytes.decode("latin-1"), decode_text_value(zlib.decompress(rest[1:]))


def parse_itxt_chunk(payload: bytes) -> tuple[str, str]:
    key_bytes, rest = payload.split(b"\x00", 1)
    if len(rest) < 2:
        raise ValueError("invalid iTXt chunk")
    compressed = rest[0] == 1
    if rest[1] != 0:
        raise ValueError("unsupported iTXt compression method")
    rest = rest[2:]
    _language, rest = rest.split(b"\x00", 1)
    _translated, text_bytes = rest.split(b"\x00", 1)
    if compressed:
        text_bytes = zlib.decompress(text_bytes)
    return key_bytes.decode("latin-1"), text_bytes.decode("utf-8")


def decode_text_value(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("latin-1")


def analyze_workflow(workflow: Any) -> dict[str, Any]:
    nodes = workflow_nodes(workflow)
    links = workflow_links(workflow)
    models = workflow_models(nodes)
    return {
        "nodes": nodes,
        "links": links,
        "models": models,
        "node_count": len(nodes),
        "link_count": len(links),
    }


def workflow_nodes(workflow: Any) -> list[dict[str, Any]]:
    if not isinstance(workflow, dict):
        return []
    raw_nodes = workflow.get("nodes")
    if isinstance(raw_nodes, list):
        nodes = []
        for index, node in enumerate(raw_nodes):
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id") or index)
            title = str(node.get("title") or node.get("type") or node.get("class_type") or f"Node {node_id}")
            node_type = str(node.get("type") or node.get("class_type") or title)
            values = node.get("widgets_values")
            inputs = node.get("inputs")
            nodes.append(
                {
                    "id": node_id,
                    "title": title,
                    "type": node_type,
                    "models": extract_models_from_node(node_type, values, inputs),
                }
            )
        return nodes

    nodes = []
    for node_id, node in sorted(workflow.items(), key=lambda item: numeric_sort_key(str(item[0]))):
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("class_type") or node.get("type") or f"Node {node_id}")
        inputs = node.get("inputs")
        nodes.append(
            {
                "id": str(node_id),
                "title": node_type,
                "type": node_type,
                "models": extract_models_from_node(node_type, None, inputs),
            }
        )
    return nodes


def workflow_links(workflow: Any) -> list[dict[str, str]]:
    if not isinstance(workflow, dict):
        return []
    raw_links = workflow.get("links")
    links: list[dict[str, str]] = []
    if isinstance(raw_links, list):
        for item in raw_links:
            if isinstance(item, list) and len(item) >= 4:
                links.append({"from": str(item[1]), "to": str(item[3])})
            elif isinstance(item, dict):
                source = item.get("origin_id") or item.get("from") or item.get("source")
                target = item.get("target_id") or item.get("to") or item.get("target")
                if source is not None and target is not None:
                    links.append({"from": str(source), "to": str(target)})
        return links

    for target_id, node in workflow.items():
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        for value in node["inputs"].values():
            if isinstance(value, list) and value and str(value[0]).isdigit():
                links.append({"from": str(value[0]), "to": str(target_id)})
    return links


def workflow_models(nodes: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    models: list[str] = []
    for node in nodes:
        for value in node.get("models") or []:
            if value not in seen:
                seen.add(value)
                models.append(value)
    return models[:80]


def extract_models_from_node(node_type: str, widgets: Any, inputs: Any) -> list[str]:
    values: list[str] = []
    collect_model_values(values, widgets)
    if isinstance(inputs, dict):
        for key, value in inputs.items():
            if str(key).lower() in WORKFLOW_MODEL_KEYS:
                collect_model_values(values, value, force=True)
            else:
                collect_model_values(values, value)
    if any(token in node_type.lower() for token in ("loader", "lora", "checkpoint", "vae", "controlnet")):
        collect_model_values(values, widgets, force=True)
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        clean = value.strip()
        if clean and clean not in seen:
            seen.add(clean)
            normalized.append(clean)
    return normalized[:12]


def collect_model_values(out: list[str], value: Any, force: bool = False) -> None:
    if isinstance(value, str):
        lower = value.lower()
        if force or any(lower.endswith(ext) for ext in WORKFLOW_MODEL_EXTENSIONS):
            out.append(value)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            collect_model_values(out, item, force or str(key).lower() in WORKFLOW_MODEL_KEYS)
        return
    if isinstance(value, list):
        if len(value) == 2 and str(value[0]).isdigit():
            return
        for item in value:
            collect_model_values(out, item, force)


def numeric_sort_key(value: str) -> tuple[int, str]:
    return (int(value), value) if value.isdigit() else (10**9, value)


def save_workflow_bundle(
    data: bytes,
    filename: str,
    raw_input: str,
    data_root: Path,
    target_subdir: str | None = None,
) -> dict[str, Any]:
    if len(data) > workflow_max_bytes():
        raise WorkflowParseError(f"워크플로우 파일이 너무 큽니다. 최대 {human_bytes(workflow_max_bytes())}까지 지원합니다.")

    bundle = extract_workflow_bundle(data, filename)
    title = sanitize_segment(Path(filename or "workflow").stem, "workflow")
    base = safe_join(data_root, target_subdir, title) if target_subdir else safe_join(data_root, *WORKFLOW_ROOT_PARTS, title)
    target = unique_directory(base)
    target.mkdir(parents=True, exist_ok=True)

    original_suffix = Path(filename or "").suffix.lower()
    original_name = sanitize_segment(Path(filename or "workflow").name, f"{title}{original_suffix or '.json'}")
    if not Path(original_name).suffix:
        original_name = f"{original_name}.json"
    original_path = target / original_name
    original_path.write_bytes(data)

    workflow_path = target / "workflow.json"
    workflow_path.write_text(json.dumps(bundle["workflow"], ensure_ascii=False, indent=2), encoding="utf-8")

    metadata = {
        "archived_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "comfyui",
        "raw_input": raw_input,
        "original_filename": filename,
        "original_file": original_path.name,
        "workflow_file": workflow_path.name,
        "source_key": bundle["source_key"],
        "source_format": bundle["source_format"],
        "metadata_keys": bundle["metadata_keys"],
        "node_count": bundle["node_count"],
        "link_count": bundle["link_count"],
        "models": bundle["models"],
    }
    for name in ("_workflow_metadata.json", "_archive_metadata.json"):
        (target / name).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        **bundle,
        "title": Path(filename or "workflow").stem or "workflow",
        "target_dir": target,
        "workflow_path": workflow_path,
        "original_path": original_path,
        "filename": workflow_path.name,
        "thumbnail_path": original_path if original_path.suffix.lower() == ".png" else None,
        "metadata": metadata,
    }


def unique_directory(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
    raise WorkflowParseError("저장할 워크플로우 폴더 이름을 만들지 못했습니다.")


def find_workflow_json(path: Path) -> Path:
    if path.is_file() and path.suffix.lower() == ".json":
        return path
    if path.is_dir():
        preferred = path / "workflow.json"
        if preferred.exists():
            return preferred
        for item in sorted(path.glob("*.json"), key=lambda file: file.name.lower()):
            if item.name.startswith("_"):
                continue
            return item
    raise WorkflowParseError("워크플로우 JSON 파일을 찾지 못했습니다.")


def find_workflow_png(path: Path) -> Path | None:
    if path.is_file() and path.suffix.lower() == ".png":
        return path
    if path.is_dir():
        for item in sorted(path.glob("*.png"), key=lambda file: file.name.lower()):
            return item
    return None


def load_workflow_view(path: Path) -> dict[str, Any]:
    workflow_path = find_workflow_json(path)
    workflow = decode_json_bytes(workflow_path.read_bytes())
    analysis = analyze_workflow(workflow)
    return {
        "workflow": workflow,
        "nodes": analysis["nodes"],
        "links": analysis["links"],
        "models": analysis["models"],
        "node_count": analysis["node_count"],
        "link_count": analysis["link_count"],
        "filename": workflow_path.name,
    }
