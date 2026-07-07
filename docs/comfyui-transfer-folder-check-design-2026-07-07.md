# ComfyUI Transfer Folder Check Design

작성일: 2026-07-07

상태: partially implemented. 현재 구현은 `POST /api/transfer/targets/{target_id}/comfyui/check`, `app/transfer.py::check_comfyui_local_mount_target()`, settings pane의 `폴더 체크` UI, 그리고 관련 테스트까지 포함한다. 전송 모달의 destination 자동 입력과 missing folder 생성은 아직 구현하지 않았다. 이 문서는 `local_mount` 전송 대상이 ComfyUI `models` 폴더인지 검사하고, HugCivi `/data` 보관 경로를 ComfyUI 모델 폴더 규격에 맞춰 전송하도록 제안하는 개발 기준이다.

## 결론

기존 HugCivi `/data` 구조는 변경하지 않는다.

HugCivi는 계속 자체 보관함 구조를 유지한다.

```text
/data/stable-diffusion/checkpoints
/data/stable-diffusion/loras
/data/stable-diffusion/diffusion_models
/data/stable-diffusion/vae
/data/stable-diffusion/controlnet
/data/stable-diffusion/embeddings
/data/stable-diffusion/upscalers
/data/huggingface/llm
```

새 기능은 `/data`를 재배치하는 작업이 아니라, 전송 대상이 ComfyUI 쪽일 때만 destination을 검사하고 추천하는 얇은 계층이다.

```text
HugCivi /data archive
  -> copy-only transfer
  -> /data_remote/<target>/ComfyUI/models/<comfyui-folder>
```

즉, HugCivi 안에서는 지금처럼 모아두고, ComfyUI로 공유/전송할 때만 ComfyUI의 `models` 하위 폴더 규격을 확인한다.

## 공식 ComfyUI 기준

공식 ComfyUI 문서와 현재 `ComfyUI/folder_paths.py` 기준으로, ComfyUI는 모델을 `ComfyUI/models` 아래의 타입별 폴더에서 찾는다. 기본 설치 문서와 문제 해결 문서는 대표 폴더로 `checkpoints`, `vae`, `loras`, `controlnet`, `embeddings` 등을 안내하고, `extra_model_paths.yaml`로 외부 또는 공용 모델 폴더를 추가할 수 있다고 설명한다.

현재 코드 기준의 주요 모델 폴더는 다음과 같다.

| ComfyUI key | 기본/호환 폴더 |
| --- | --- |
| `checkpoints` | `models/checkpoints` |
| `configs` | `models/configs` |
| `loras` | `models/loras` |
| `vae` | `models/vae` |
| `text_encoders` | `models/text_encoders`, `models/clip` |
| `diffusion_models` | `models/unet`, `models/diffusion_models` |
| `clip_vision` | `models/clip_vision` |
| `style_models` | `models/style_models` |
| `embeddings` | `models/embeddings` |
| `diffusers` | `models/diffusers` |
| `vae_approx` | `models/vae_approx` |
| `controlnet` | `models/controlnet`, `models/t2i_adapter` |
| `gligen` | `models/gligen` |
| `upscale_models` | `models/upscale_models` |
| `latent_upscale_models` | `models/latent_upscale_models` |
| `custom_nodes` | `custom_nodes` |
| `hypernetworks` | `models/hypernetworks` |
| `photomaker` | `models/photomaker` |
| `classifiers` | `models/classifiers` |
| `model_patches` | `models/model_patches` |
| `audio_encoders` | `models/audio_encoders` |
| `background_removal` | `models/background_removal` |
| `frame_interpolation` | `models/frame_interpolation` |
| `geometry_estimation` | `models/geometry_estimation` |
| `optical_flow` | `models/optical_flow` |
| `detection` | `models/detection` |

주의할 호환 이름:

- `clip`은 현재 `text_encoders`의 호환 폴더로 남아 있다.
- `unet`은 현재 `diffusion_models`의 호환 폴더로 남아 있다.
- `t2i_adapter`는 현재 `controlnet`의 호환 폴더로 남아 있다.
- `custom_nodes`는 `models` 하위가 아니라 ComfyUI root 하위 폴더다. 이 기능의 MVP는 모델 전송이므로 `custom_nodes` 자동 전송은 제외한다.

## HugCivi 현재 구조와의 관계

HugCivi의 `/data`는 다운로드 출처와 라이브러리 관리에 맞춘 archive 구조다. ComfyUI의 `models` 폴더 구조와 1:1로 같지 않은 것이 정상이다.

현재 라우트 기본값:

| HugCivi route | 기본 경로 |
| --- | --- |
| LLM | `huggingface/llm` |
| LoRA | `stable-diffusion/loras` |
| Checkpoint | `stable-diffusion/checkpoints` |
| Diffusion model | `stable-diffusion/diffusion_models` |
| Embedding | `stable-diffusion/embeddings` |
| VAE | `stable-diffusion/vae` |
| ControlNet | `stable-diffusion/controlnet` |
| Upscaler | `stable-diffusion/upscalers` |

따라서 개발 기준은 다음이다.

- `/data` 라우트 기본값은 유지한다.
- 기존 다운로드 저장 위치를 ComfyUI식으로 바꾸지 않는다.
- 전송 대상이 ComfyUI `models` root일 때만 destination subpath를 ComfyUI식으로 추천한다.
- 전송 대상이 이미 `models/checkpoints` 같은 단일 모델 폴더이면 source route와 target folder가 맞는지만 검사한다.

## 기본 매핑

MVP에서 우선 지원할 매핑:

| HugCivi source prefix | ComfyUI destination | 비고 |
| --- | --- | --- |
| `stable-diffusion/checkpoints` | `checkpoints` | 모델 checkpoint 기본 전송 경로. |
| `stable-diffusion/loras` | `loras` | LoRA 기본 전송 경로. |
| `stable-diffusion/diffusion_models` | `diffusion_models` | 새 ComfyUI 명칭 우선. 대상에 `unet`만 있으면 호환 폴더로 안내할 수 있다. |
| `stable-diffusion/vae` | `vae` | VAE 기본 전송 경로. |
| `stable-diffusion/controlnet` | `controlnet` | ControlNet 우선. `t2i_adapter`는 파일/메타데이터로 구분 가능할 때만 추천한다. |
| `stable-diffusion/embeddings` | `embeddings` | Textual inversion/embedding 기본 전송 경로. |
| `stable-diffusion/upscalers` | `upscale_models` | HugCivi는 `upscalers`, ComfyUI는 `upscale_models`를 쓴다. |

후속 후보:

| HugCivi/metadata category | ComfyUI destination | 비고 |
| --- | --- | --- |
| text encoder / CLIP | `text_encoders` | `clip`은 호환 폴더로 인식하되 신규 추천은 `text_encoders` 우선. |
| CLIP vision | `clip_vision` | Civitai/HF 메타데이터가 구분될 때 추가. |
| model patches | `model_patches` | 명확한 분류가 생기면 추가. |
| audio encoders | `audio_encoders` | 오디오/비디오 모델 지원 시 추가. |

`huggingface/llm`은 기본적으로 ComfyUI 모델 폴더로 자동 매핑하지 않는다. LLM archive는 ComfyUI `models`의 일반 checkpoint/LoRA/vae 폴더와 의미가 다르다. Hugging Face에서 받은 개별 파일이 text encoder, diffusion model, VAE 등으로 분류되는 경우에만 별도 매핑한다.

## 제품 흐름

### 설정 화면

전송 설정의 `ComfyUI` 그룹에서 `local_mount` target에 대해 `폴더 체크`를 제공한다.

권장 UX:

```text
대상: PC ComfyUI
remote_path: pc-comfyui/ComfyUI/models

[폴더 체크]

결과:
  ComfyUI models root로 보입니다.
  발견: checkpoints, loras, vae, controlnet, embeddings
  호환: unet -> diffusion_models
  없음: upscale_models
  추천: stable-diffusion/checkpoints -> checkpoints
```

target의 `remote_path`가 다음 중 하나일 수 있다.

| Target base | 판정 |
| --- | --- |
| `pc-comfyui/ComfyUI/models` | ComfyUI models root. 하위 표준 폴더를 검사하고 destination subpath를 자동 추천한다. |
| `pc-comfyui/ComfyUI/models/checkpoints` | 단일 모델 폴더 target. source prefix가 `stable-diffusion/checkpoints`인지 검사하고 destination subpath는 비워둔다. |
| `pc-comfyui/ComfyUI` | ComfyUI root 후보. `models` 하위가 있으면 `models`를 추천한다. |
| `pc-comfyui` | 일반 연결 폴더. 표준 폴더가 직접 있거나 `ComfyUI/models`가 발견될 때만 ComfyUI 후보로 표시한다. |

### 전송 모달

사용자가 `/data/stable-diffusion/checkpoints/Model.safetensors`를 선택하고 ComfyUI models root target을 고르면:

```text
destination_subpath = checkpoints
```

사용자가 같은 파일을 `models/checkpoints` 단일 폴더 target으로 보내면:

```text
destination_subpath = ""
```

폴더 source를 보낼 때는 기존 `preserve_folder_name` 정책을 유지한다. 예를 들어 `/data/stable-diffusion/loras/MyPack`을 `models/loras`로 보내면 최종 목적지는 target base와 destination subpath 아래의 `MyPack`이다.

## Backend 설계

기본 구현 위치는 `app/transfer.py`다. 전송은 다운로드 provider가 아니므로 `app/downloader.py`에는 넣지 않는다.

추가할 상수/헬퍼 후보:

```python
COMFYUI_MODEL_FOLDERS = {
    "checkpoints": ["checkpoints"],
    "loras": ["loras"],
    "vae": ["vae"],
    "embeddings": ["embeddings"],
    "controlnet": ["controlnet", "t2i_adapter"],
    "diffusion_models": ["diffusion_models", "unet"],
    "text_encoders": ["text_encoders", "clip"],
    "upscale_models": ["upscale_models"],
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
```

후보 함수:

```python
def check_comfyui_local_mount_target(target: dict) -> dict: ...
def detect_comfyui_models_base(target_path: Path) -> dict: ...
def suggest_comfyui_destination_subpath(source_path: str, check_result: dict) -> str | None: ...
```

검사 결과 예:

```json
{
  "kind": "models_root",
  "base_path": "",
  "present": ["checkpoints", "loras", "vae"],
  "aliases": [{"canonical": "diffusion_models", "found": "unet"}],
  "missing": ["embeddings", "controlnet", "upscale_models"],
  "suggested_mappings": [
    {"source_prefix": "stable-diffusion/checkpoints", "destination_subpath": "checkpoints"},
    {"source_prefix": "stable-diffusion/loras", "destination_subpath": "loras"}
  ],
  "warnings": []
}
```

MVP API 후보:

```text
POST /api/transfer/targets/{target_id}/comfyui/check
```

요구사항:

- `kind='local_mount'` target만 허용한다.
- `remote_path`는 기존 `normalize_local_mount_remote_path()`와 `/data_remote` resolver를 사용한다.
- browser에는 `/data_remote` absolute path를 반환하지 않는다.
- target base 밖으로 나가는 상대 경로, backslash escape, symlink escape는 기존 local mount 규칙대로 거부한다.
- check API는 기본적으로 폴더를 생성하지 않는다.
- missing folder 생성은 후속 기능으로 따로 둔다.

추가 API는 MVP에서는 만들지 않아도 된다. 전송 모달의 destination 추천은 check 결과와 source path를 프론트에서 결합하거나, 기존 `POST /api/transfer/preflight` 응답에 optional recommendation을 추가하는 쪽이 작다.

## DB와 설정

MVP는 DB schema 변경 없이 시작한다.

ComfyUI target 여부는 다음 정보로 추론할 수 있다.

- target group이 `ComfyUI`로 분류되는지
- `kind='local_mount'`인지
- `remote_path`가 `models`, `ComfyUI/models`, `checkpoints`, `loras` 같은 표준 폴더 구조를 가리키는지
- `allowed_source_prefixes`가 stable-diffusion model route를 포함하는지

후속으로 사용자가 target profile을 명시해야 할 필요가 생기면 additive migration으로 `policy_json.target_profile = "comfyui_models"`를 추가하는 편이 작다. 별도 컬럼보다 policy 확장이 현재 구조와 잘 맞는다.

## 안전 조건

필수 불변 조건:

- `/data` 구조를 바꾸지 않는다.
- `/data_remote`는 라이브러리 root로 index하지 않는다.
- ComfyUI 폴더 체크는 `local_mount` target에만 적용한다.
- remote check는 `/data_remote/<target remote_path>` 아래에서만 수행한다.
- target 등록, tree browse, preflight, copy job에서 같은 path safety helper를 재사용한다.
- root `/data_remote` 자체를 target으로 삼지 않는다.
- 절대 경로, `..`, backslash escape, symlink escape를 거부한다.
- 기본 체크는 direct child 폴더 중심으로만 본다. 원격 mount 전체를 깊게 재귀 탐색하지 않는다.
- 전송은 copy-only다. sync, move, mirror, remote delete는 계속 금지한다.
- 폴더 자동 생성은 MVP에서 하지 않는다. 나중에 추가하면 별도 버튼과 preflight를 둔다.

쓰기 가능 여부:

- check API에서 optional writable probe를 할 수 있다.
- probe 파일을 만든다면 숨김 임시 파일을 만들고 즉시 삭제한다.
- probe 실패는 target 전체 실패가 아니라 "읽기 전용 또는 권한 문제" warning으로 표시할 수 있다.
- 실제 job 생성 전에는 기존 preflight의 writable/offline 검사를 계속 수행한다.

## Frontend 설계

변경 위치는 `app/templates/index.html`의 transfer settings pane과 transfer modal이다. 스타일은 `app/static/style.css`에 둔다.

설정 pane:

- `ComfyUI` 그룹 target에 `폴더 체크` 버튼을 표시한다.
- 결과는 target 카드 안의 compact panel로 보여준다.
- `models root`, `단일 폴더`, `ComfyUI root 후보`, `일반 폴더` 상태를 구분한다.
- 표준 폴더 발견/누락/호환 이름을 목록으로 보여준다.
- `/data_remote` absolute path는 표시하지 않는다.

전송 modal:

- ComfyUI target 선택 시 source prefix 기반 추천 destination을 자동 채운다.
- target이 단일 모델 폴더이면 destination field를 비워두고, source category가 맞지 않으면 경고한다.
- 사용자가 직접 destination folder를 바꿀 수는 있지만 기존 safety validation을 통과해야 한다.
- 추천이 불확실한 `huggingface/llm`, 기타 일반 폴더는 자동 매핑하지 않는다.

## 테스트 계획

Unit/core:

- `tests/test_transfer_core.py`
  - ComfyUI 표준 폴더 감지.
  - `clip`, `unet`, `t2i_adapter` alias 처리.
  - HugCivi source prefix to ComfyUI destination 매핑.
  - 단일 모델 폴더 target 판정.
  - `/data_remote` escape 거부.

API:

- `tests/test_transfer_api.py`
  - `local_mount` target만 `comfyui/check` 허용.
  - `receiver`, `rclone` target은 400 또는 404로 거부.
  - missing target, disabled target, offline mount 결과.
  - response에 host absolute path가 없는지 확인.
  - symlink escape fixture는 기존 local mount 규칙대로 차단.

Frontend template:

- `tests/test_transfer_api.py::test_home_template_declares_transfer_ui_without_mode_payload`
  - `폴더 체크` 버튼과 ComfyUI result renderer hook 확인.
  - group filter와 folder check UI가 함께 존재하는지 확인.

Browser verification:

- 임시 `DATA_REMOTE_DIR`에 `pc-comfyui/ComfyUI/models/checkpoints`, `loras`, `unet` 등을 만들고 settings pane에서 체크한다.
- desktop/mobile에서 결과 panel과 그룹 버튼이 겹치지 않는지 확인한다.
- transfer modal에서 checkpoint/LoRA/upscaler source별 destination 추천이 맞는지 확인한다.

기본 검증:

```bash
git diff --check
python3 -m pytest -q -p no:cacheprovider tests/test_transfer_api.py tests/test_transfer_core.py tests/test_transfer_db.py
python3 -m pytest -q -p no:cacheprovider tests/test_review_fixes.py
python3 -m pytest -q -p no:cacheprovider
```

## 구현 순서

1. `app/transfer.py`에 ComfyUI 표준 폴더 상수와 검사 helper를 추가한다.
2. `app/main.py`에 `POST /api/transfer/targets/{target_id}/comfyui/check`를 추가한다.
3. `tests/test_transfer_core.py`와 `tests/test_transfer_api.py`에 local mount 검사 테스트를 먼저 추가한다.
4. settings pane의 ComfyUI target 카드에 `폴더 체크` 버튼과 결과 panel을 붙인다.
5. transfer modal에 source prefix 기반 destination 추천을 붙인다.
6. Playwright로 실제 브라우저에서 저장된 target, folder check, modal 추천을 확인한다.
7. `docs/feature-code-map.md`, `docs/configuration.md`, `docs/architecture.md`, `docs/patch-notes/YYYY-MM-DD.md`를 구현 결과에 맞춰 갱신한다.

## 열린 질문

- missing ComfyUI 표준 폴더를 HugCivi가 생성해도 되는가? MVP 답은 "아니오"다. 후속으로 넣더라도 명시적 버튼과 preflight를 둔다.
- Civitai/Hugging Face의 text encoder, CLIP vision, model patch 같은 세부 컴포넌트를 어디까지 자동 분류할 것인가?
- target profile을 `policy_json.target_profile`로 저장할 필요가 있는가, 아니면 현재 group 추론과 folder check 결과만으로 충분한가?
- ComfyUI의 `extra_model_paths.yaml` 생성까지 도와줄 것인가? 이 문서의 범위는 전송 대상 폴더 검사이고 config 파일 생성은 제외한다.

## 참고 자료

- [ComfyUI model files documentation](https://docs.comfy.org/development/core-concepts/models)
- [ComfyUI model troubleshooting](https://docs.comfy.org/troubleshooting/model-issues)
- [ComfyUI `folder_paths.py`](https://github.com/Comfy-Org/ComfyUI/blob/master/folder_paths.py)
- [ComfyUI `extra_model_paths.yaml.example`](https://github.com/Comfy-Org/ComfyUI/blob/master/extra_model_paths.yaml.example)
- [HugCivi configuration reference](configuration.md)
- [`/data_remote` connected transfer design](data-remote-transfer-design-2026-07-06.md)
- [Copy-only transfer production plan](transfer-copy-production-plan-2026-07-06.md)
