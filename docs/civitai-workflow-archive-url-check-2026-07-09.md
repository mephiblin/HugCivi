# Civitai Workflow Archive URL Check 2026-07-09

Status: snapshot reference

Checked at: 2026-07-09 KST

This note records whether three Civitai `Workflows` model pages fit HugCivi's Civitai workflow archive download path introduced in commit `6927efb`.

## Result

All three URLs match the supported shape:

- host is `civitai.red`, which the parser routes to the Civitai provider
- page model type is `Workflows`
- selected latest API version has a primary file with `type=Archive` and `metadata.format=Other`
- primary file name ends with `.zip`
- HugCivi classifies the item as `ComfyUI Workflow`, stores it under `/data/civitai/workflows/...`, keeps the original ZIP, and extracts a validated workflow JSON/PNG as `workflow.json` when the ZIP contains one

The Civitai download endpoints returned `401 Unauthorized` without a token during this check. In normal HugCivi operation, configure `CIVITAI_TOKEN` in settings when these downloads require authentication.

## URL Matrix

| URL | Model ID | Model Name | Version | Primary File | Size | Expected HugCivi Classification |
| --- | --- | --- | --- | --- | --- | --- |
| `https://civitai.red/models/2031587/qwenedit2509-simple-consistent-character-pose-changer-workflow` | `2031587` | `[Qwen_edit_2509] Simple Consistent character pose changer (workflow)` | `2299258` / `v1.0` | `QwenEdit2509Simple_v10.zip` | `1.57 MB` | `ComfyUI Workflow`, `ZIP` |
| `https://civitai.red/models/1890385/qwen-image-edit-multi-gen` | `1890385` | `Qwen Image Edit Multi Gen` | `2553903` / `2511 v1` | `qwenImageEditMulti_2511V1.zip` | `1.28 MB` | `ComfyUI Workflow`, `ZIP` |
| `https://civitai.red/models/1944963/qwen-clothing-tranfer-comfyui-workflow` | `1944963` | `Qwen Clothing Tranfer - ComfyUI Workflow` | `2212417` / `v1.2` | `qwenClothingTranfer_v12.zip` | `9.67 KB` | `ComfyUI Workflow`, `ZIP` |

## Expected Archive Paths

If no custom target folder is selected, the current classifier routes these URLs to:

```text
civitai/workflows/qwen/qwen_edit_2509_-simple-consistent-character-pose-changer-(workflow)/version_2299258
civitai/workflows/qwen/qwen-image-edit-multi-gen/version_2553903
civitai/workflows/qwen/qwen-clothing-tranfer---comfyui-workflow/version_2212417
```

Each archive folder should contain:

- the original downloaded ZIP
- `_civitai_metadata.json`
- optional `_civitai_generation_metadata.json` and `civitai_example_<imageId>.*` preview files when Civitai exposes example images
- `workflow.json` and `_workflow_metadata.json` when a valid workflow JSON/PNG entry is found inside the ZIP

## Implementation References

- `app/parsers.py::parse_civitai_url`: parses `civitai.red/models/<id>` as a Civitai model job.
- `app/metadata.py::classify_civitai`: keeps the parent `Workflows` model type when the selected file is an `Archive`, then maps it to `ComfyUI Workflow` and `civitai/workflows`.
- `app/downloader.py::download_civitai`: downloads the ZIP through the normal Civitai model path and records Civitai sidecars.
- `app/downloader.py::extract_civitai_workflow_archive`: inspects bounded `.json`/`.png` ZIP entries, validates them with the existing ComfyUI workflow parser, and writes viewable workflow sidecars.

## Verification Notes

Commands and observations:

```text
parse_input(url) -> source=civitai, civitai_model_id=<expected model id>
Civitai API /models/<id> -> type=Workflows, primary type=Archive, format=Other
classify_civitai(...) -> model_category=ComfyUI Workflow, file_format=ZIP
GET /api/download/models/<version_id> without CIVITAI_TOKEN -> 401 Unauthorized for all three URLs
```

Because unauthenticated file downloads returned 401, this note confirms routing and classification. End-to-end file extraction should be verified in an environment with a valid `CIVITAI_TOKEN`.
