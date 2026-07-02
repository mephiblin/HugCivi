# Civitai 이미지 페이지 저장 및 사용 리소스 다운로드 구현 계획서

작성일: 2026-07-01

상태: historical implementation plan. 현재 코드는 Civitai image URL 저장, generation metadata 표시, resource child job, resource health 흐름을 포함한다. 변경 전 [Feature and Code Map](feature-code-map.md)의 Civitai 항목을 먼저 확인한다.

대상 프로젝트: HugCivi

참고 프로젝트: `/home/inri/문서/CivitaiOfflineSaver_restored`

## 목표

기존 HugCivi 입력 흐름은 유지합니다.

사용자가 기존 입력창에 Civitai 이미지 페이지 URL을 넣으면 다음이 자동으로 수행되어야 합니다.

```text
https://civitai.com/images/135240496
```

1. Civitai 이미지 페이지를 저장합니다.
2. 이미지, prompt, negative prompt, seed, steps, sampler, CFG, Resources used 등 generation data를 JSON sidecar로 저장합니다.
3. 저장된 JSON을 HugCivi의 기존 뷰어에서 사람이 보기 좋은 형태로 렌더링합니다. 뷰어는 왼쪽에 이미지, 오른쪽에 어두운 `Generation data` 패널을 두는 형태를 기준으로 합니다.
4. Resources used에 포함된 Checkpoint, LoRA, VAE, Upscaler 등 Civitai 등록 리소스를 기존 Civitai 모델 페이지 다운로드와 같은 방식으로 다운로드합니다.
5. 리소스 파일은 이미지 폴더의 `resources/`에 몰아넣지 않고, 기존 HugCivi의 Civitai 분류/라우팅 규칙에 따라 LoRA, Checkpoint, VAE, Upscaler 위치에 저장합니다.
6. 리소스 다운로드 결과도 기존 Civitai 모델 다운로드와 동일하게 `_civitai_metadata.json`, 썸네일, 라이브러리 카드가 생성되어야 합니다.
7. Resources used 목록에는 수동 헬스체크 버튼을 둡니다. 버튼을 누를 때만 각 리소스가 현재 HugCivi 보관함에 있는지 확인하고, 결과는 빨간색/녹색 상태점으로 표시합니다.

## 핵심 해석

이번 기능은 "새 다운로드 타입"이라기보다 Civitai 이미지 URL을 "이미지 아카이브 작업 + 연결된 Civitai 모델 다운로드 작업 생성"으로 확장하는 기능입니다.

최종 사용 경험:

1. 사용자는 Civitai 이미지 페이지 URL 하나만 입력합니다.
2. 작업 목록에는 Civitai 이미지 저장 작업이 생성됩니다.
3. 이미지 저장 작업이 Civitai API에서 Resources used를 읽습니다.
4. Resources used의 `modelVersionId`마다 기존 Civitai 모델 다운로드 작업이 추가됩니다.
5. 모델, LoRA, VAE, Upscaler 등은 기존 저장 위치와 기존 카드 형식으로 보입니다.
6. 이미지 페이지 자체도 라이브러리에서 열면 이미지와 generation data를 보기 좋게 확인할 수 있습니다.

## 비범위

아래는 이번 구현의 목표가 아닙니다.

- `CivitaiOfflineSaver_restored`의 Tkinter GUI 병합
- `CivitaiOfflineSaver_restored`의 standalone `web_ui.py` 병합
- 브라우저 CDP fallback
- Civitai 페이지 HTML을 그대로 저장하는 standalone `index.html` 생성
- 이미지 폴더 안에 모델/LoRA 파일을 별도로 중복 저장
- Civitai 원본 사이트와 동일한 UI 복제

단, 기존 HugCivi 뷰어를 확장해 generation data를 렌더링하는 것은 범위에 포함합니다.

## 권장 구조

권장 구조는 부모-자식 작업 방식입니다.

- 부모 작업: Civitai 이미지 페이지 저장
- 자식 작업: 이미지에 사용된 각 Civitai 모델/LoRA/VAE/Upscaler 다운로드

이 구조를 권장하는 이유:

- 기존 `download_civitai()` 모델 다운로드 흐름을 거의 그대로 재사용할 수 있습니다.
- 리소스별 진행률, 실패, 재시도, pause/delete가 기존 작업 단위와 맞습니다.
- 모델/LoRA 저장 위치와 라이브러리 카드가 기존 방식 그대로 유지됩니다.
- 이미지 저장 작업 하나의 `target_dir`, `filename`, `progress_bytes`에 여러 대용량 모델 다운로드 상태를 억지로 섞지 않아도 됩니다.

## 데이터 모델 변경

파일:

- `app/models.py`

`ParsedDownload`에 Civitai 이미지 페이지 필드를 추가합니다.

```python
civitai_image_id: str | None = None
civitai_image_url: str | None = None
```

`SourceType`은 우선 그대로 유지합니다.

```python
SourceType = Literal["huggingface", "civitai", "generic", "comfyui", "hitomi", "gallerydl"]
```

`source="civitai"`를 유지하는 이유:

- 기존 Civitai 토큰 저장을 그대로 사용합니다.
- 기존 Civitai provider queue 제한을 공유합니다.
- 기존 `normalize_library_source()`와 source badge를 크게 바꾸지 않아도 됩니다.
- 자식 작업도 기존 Civitai 모델 다운로드 작업과 완전히 같은 source를 가집니다.

이미지 작업과 모델 작업의 구분은 다음 필드로 합니다.

```python
if parsed.source == "civitai" and parsed.civitai_image_id:
    # Civitai image page archive
else:
    # existing Civitai model/resource download
```

## URL 파서 변경

파일:

- `app/parsers.py`

`parse_civitai_url()`에서 `/images/{id}`를 인식합니다.

지원 URL:

```text
https://civitai.com/images/135240496
https://www.civitai.com/images/135240496
https://civitai.red/images/135240496
https://civitai.green/images/135240496
```

권장 구현:

```python
if len(parts) >= 2 and parts[0] == "images" and parts[1].isdigit():
    return ParsedDownload(
        source="civitai",
        raw_input=url,
        target_subdir=target_subdir,
        civitai_image_id=parts[1],
        civitai_image_url=url,
    )
```

주의:

- 이 분기는 model URL 처리보다 앞에 있어야 합니다.
- `raw_input`은 기존 DB 저장 흐름에서 redaction됩니다.
- Civitai image URL은 모델 다운로드 URL과 다르므로 `civitai_model_id` 또는 `civitai_version_id`에 넣지 않습니다.

## Downloader 분기

파일:

- `app/downloader.py`

`download_civitai()`의 초입에 이미지 페이지 분기를 추가합니다.

```python
def download_civitai(job_id: int, parsed: ParsedDownload) -> None:
    if parsed.civitai_image_id:
        download_civitai_image_page(job_id, parsed)
        return

    # existing model download flow
```

새 함수:

```python
def download_civitai_image_page(job_id: int, parsed: ParsedDownload) -> None:
    ...
```

주요 역할:

1. Civitai 이미지 API 조회
2. 이미지 파일 저장
3. generation metadata sidecar 저장
4. job row를 이미지 아카이브로 업데이트
5. Resources used를 기존 Civitai 모델 다운로드 job으로 생성

## Civitai 이미지 API 조회

restored 프로젝트의 핵심 호출:

```text
GET https://civitai.com/api/v1/images?imageId={id}&withMeta=true
```

HugCivi에서는 기존 상수 사용:

```python
meta_url = f"{CIVITAI_API_BASE}/images?{query}"
```

권장 helper:

```python
def fetch_civitai_image_item(
    session: requests.Session,
    image_id: str,
    job_id: int,
) -> dict[str, Any]:
    ...
```

선택 규칙:

- `data["items"]`가 비어 있으면 실패
- `items` 안에서 `id == image_id`인 항목을 우선 선택
- 일치 항목이 없지만 items가 있으면 첫 항목 사용 여부는 보수적으로 결정

권장:

- image id 불일치는 실패 처리합니다.
- Civitai API가 예상과 다르게 응답했을 때 다른 이미지를 저장하는 위험을 줄입니다.

## Generation Data 정규화

restored 프로젝트에서 참고할 함수:

- `normalize_api_generation_data`
- `label_for_meta`
- `_build_copy_all_text`
- `resource_weight_for_version`
- `resource_files_for_version`
- `primary_resource_file`

HugCivi에 맞춘 권장 helper:

```python
def normalize_civitai_image_record(
    item: dict[str, Any],
    version_resources: list[dict[str, Any]],
    source_url: str,
) -> dict[str, Any]:
    ...
```

저장할 핵심 정보:

- image id
- post id
- username
- original image URL
- thumbnail URL 후보
- width, height
- createdAt
- nsfwLevel
- prompt
- negative prompt
- metadata chips
- copy all text
- resources used
- modelVersionIds

권장 sidecar 구조:

```json
{
  "source": "civitai",
  "kind": "civitai_image_page",
  "source_url": "https://civitai.com/images/135240496",
  "raw_input": "https://civitai.com/images/135240496",
  "image": {
    "id": "135240496",
    "post_id": "29477144",
    "username": "creator",
    "width": 800,
    "height": 1000,
    "created_at": "2026-06-29T14:19:52.635Z",
    "nsfw_level": 0,
    "original_url": "https://image.civitai.com/..."
  },
  "generation_data": {
    "available": true,
    "prompt": {"text": "..."},
    "negative_prompt": {"text": "..."},
    "copy_all_text": "...",
    "metadata": [
      {"label": "Seed", "value": "2484449105"},
      {"label": "Steps", "value": "25"},
      {"label": "Sampler", "value": "Euler a"}
    ],
    "resources": [
      {
        "name": "Example LoRA",
        "type": "LORA",
        "version": "v4.0",
        "weight": "0.44",
        "model_id": "2061456",
        "model_version_id": "3059910",
        "base_model": "Illustrious",
        "href": "https://civitai.com/models/2061456"
      }
    ],
    "model_version_ids": ["3059910"]
  },
  "resource_downloads": [
    {
      "model_version_id": "3059910",
      "child_job_id": 123,
      "name": "Example LoRA",
      "type": "LORA",
      "status": "queued"
    }
  ],
  "archive_info": {
    "model_title": "Civitai image 135240496",
    "model_category": "Civitai Image Page",
    "model_type": "Image",
    "base_model": "Illustrious",
    "file_format": "jpg",
    "precision": "800 x 1000"
  }
}
```

원칙:

- 내부 HugCivi metadata는 snake_case를 사용합니다.
- Civitai 원본 구조를 전부 그대로 노출하지 않습니다.
- token류 query와 Authorization 값은 저장 전 redaction합니다.

## 이미지 페이지 저장 위치

권장 기본 경로:

```text
/data/civitai/images/{username-or-unknown}/image_{image_id}/
  image_{image_id}.jpg
  _civitai_image_metadata.json
```

예:

```text
/data/civitai/images/chumofchance/image_135240496/
  image_135240496.jpeg
  _civitai_image_metadata.json
```

필요 변경:

- `app/main.py`의 `SIDECAR_FILENAMES`에 `_civitai_image_metadata.json` 추가

```python
SIDECAR_FILENAMES = (
    "_archive_metadata.json",
    "_civitai_metadata.json",
    "_civitai_image_metadata.json",
    ...
)
```

이미지 파일명:

```text
image_{image_id}.{ext}
```

확장자 선택:

- HTTP `Content-Type` 우선
- 실패 시 원본 URL path suffix
- 그래도 없으면 `.jpg`

이미지 다운로드:

- 기존 `stream_download()` 사용을 우선 고려합니다.
- 단, 원본 URL의 파일명이 UUID 기반일 수 있으므로 `filename_override`로 안정적인 이름을 지정합니다.

## 기존 뷰어 렌더링

요구사항:

JSON으로 저장하더라도 HugCivi 뷰어에서 보기 좋은 형태로 보여야 합니다.

기준 레이아웃:

- 화면 왼쪽에는 저장된 원본 이미지를 크게 표시합니다.
- 화면 오른쪽에는 어두운 배경의 `Generation data` 패널을 둡니다.
- 패널은 `Resources used`, `Prompt`, `Negative prompt`, `Other metadata` 순서로 구성합니다.
- 데스크톱에서는 좌우 2열 구조를 우선 사용합니다.
- 모바일 또는 좁은 화면에서는 이미지 아래에 generation data 패널이 내려오는 1열 구조로 전환합니다.

대상:

- 이미지
- 이미지 페이지 원본 주소
- Prompt
- Negative prompt
- Seed
- Steps
- Sampler
- CFG Scale
- Width/Height
- Model hash 등 metadata
- Resources used 목록

권장 방향:

- 새 standalone UI를 만들지 않습니다.
- 기존 media viewer 또는 asset detail/properties 흐름에 Civitai image metadata panel을 추가합니다.
- `_civitai_image_metadata.json`이 있는 폴더를 열면 media viewer payload에 `generation_data`를 포함합니다.
- Civitai 이미지 archive는 일반 미디어 폴더와 다르게 generation data 패널이 켜진 상세 모드로 표시합니다.
- 이미지 페이지 원본 주소는 패널 상단 또는 이미지 caption에 하이퍼링크로 표시합니다.

변경 후보:

- `app/main.py`
  - `/api/media/list` 응답에 archive metadata 일부 포함
  - 또는 새 endpoint `/api/civitai/image-metadata?path=...` 추가
- `app/templates/index.html`
  - media viewer에서 metadata가 있으면 오른쪽 또는 하단 패널에 prompt/resources 표시
  - Civitai image metadata가 있는 경우 오른쪽 generation data 패널을 표시

권장 endpoint 방식:

```text
GET /api/media/list?path=civitai/images/user/image_135240496
```

응답에 다음을 추가합니다.

```json
{
  "metadata": {
    "kind": "civitai_image_page",
    "generation_data": {...},
    "image": {...},
    "resource_downloads": [...]
  }
}
```

장점:

- media viewer는 이미 폴더의 이미지 목록을 불러옵니다.
- 같은 응답에 generation data를 붙이면 별도 round-trip이 필요 없습니다.
- 새 route를 만들지 않아도 됩니다.

렌더링 정책:

- prompt와 negative prompt는 `<pre>` 계열로 줄바꿈 유지
- metadata는 label/value chip 또는 table 형태
- resources는 name, type, version, weight, modelVersionId, child job status, health status를 표시
- 각 resource의 name은 Civitai 원본 모델 페이지로 가는 하이퍼링크로 표시
- 이미지 페이지 원본 주소도 하이퍼링크로 표시
- `COPY ALL` 버튼을 generation data 패널 상단에 둡니다.
- Prompt 영역에는 `COPY` 버튼을 둡니다.
- Negative prompt 영역에도 `COPY` 버튼을 둡니다.
- 복사 버튼은 클릭 후 짧게 `COPIED` 또는 비슷한 상태로 피드백을 줍니다.
- resources badge는 타입과 weight를 짧게 표시합니다. 예: `LORA`, `0.77`, `CHECKPOINT`
- health status는 자동 실행하지 않고, 사용자가 `Check resources` 또는 유사 버튼을 눌렀을 때만 표시 갱신합니다.
- health status 색상은 단순하게 녹색과 빨간색만 사용합니다.
  - 녹색: 해당 modelVersionId에 대응하는 다운로드 결과가 현재 HugCivi 보관함에 있음
  - 빨간색: 현재 보관함에서 찾지 못함

주의:

- 사용자가 "추가 UI"를 원하지 않는다는 뜻은 새 앱 화면이나 복잡한 신규 흐름을 만들지 말자는 의미로 해석합니다.
- generation data를 기존 뷰어 안에 표시하는 최소 패널은 기능 요구에 포함됩니다.

## Resources used 헬스체크

요구사항:

- Civitai 이미지 뷰어에서 사용된 모델, LoRA, VAE, Upscaler 등이 현재 HugCivi 보관함에 다운로드되어 있는지 확인할 수 있어야 합니다.
- 헬스체크는 자동 polling 또는 주기 실행을 하지 않습니다.
- 뷰어에 있는 버튼을 사용자가 눌렀을 때만 확인합니다.
- 결과는 각 resource row 옆에 빨간색/녹색으로만 표시합니다.

권장 UX:

- `Generation data` 패널의 `Resources used` 헤더 옆에 `Check resources` 버튼을 둡니다.
- 버튼 클릭 전에는 상태점을 회색 또는 표시 없음으로 둡니다.
- 버튼 클릭 중에는 버튼에 loading 상태를 표시합니다.
- 응답 후 각 resource에 다음 상태를 표시합니다.
  - 녹색: 다운로드되어 있음
  - 빨간색: 다운로드되어 있지 않음
- 상태 텍스트는 길게 설명하지 않고 tooltip 또는 짧은 title만 둡니다.

권장 API:

```text
POST /api/civitai/resource-health
```

요청:

```json
{
  "model_version_ids": ["2130256", "3059910"]
}
```

응답:

```json
{
  "ok": true,
  "resources": [
    {
      "model_version_id": "2130256",
      "present": true,
      "target_path": "stable-diffusion/checkpoints/illustrious/vixon/version_2130256",
      "job_id": 123
    },
    {
      "model_version_id": "3059910",
      "present": false,
      "target_path": "",
      "job_id": null
    }
  ]
}
```

헬스체크 판단 기준:

1. 완료된 job row 중 `source="civitai"`이고 parsed payload 또는 metadata에 같은 `civitai_version_id`가 있는지 확인합니다.
2. 해당 job의 `target_dir`이 존재하고, 그 안에 실제 모델 파일이 있는지 확인합니다.
3. DB row가 삭제된 경우를 대비해 `/data` 아래 `_civitai_metadata.json` sidecar도 확인합니다.
4. sidecar의 `version_id` 또는 `metadata.id`가 요청한 modelVersionId와 일치하고 실제 모델 파일이 있으면 present로 봅니다.

성능 정책:

- 모든 라이브러리 카드에서 자동 health check를 돌리지 않습니다.
- Civitai 이미지 뷰어가 열릴 때도 자동으로 돌리지 않습니다.
- 사용자가 버튼을 눌렀을 때만 요청합니다.
- 요청 modelVersionId 목록은 현재 열려 있는 이미지의 Resources used로 제한합니다.
- 서버 응답은 짧은 시간 메모리 캐시를 둘 수 있지만 필수는 아닙니다.

## Resources used 다운로드

요구사항:

- 이미지에 사용된 모델, LoRA, VAE, Upscaler 등을 다운로드합니다.
- 저장 위치는 기존 Civitai 모델 페이지 다운로드와 동일해야 합니다.
- 썸네일과 metadata도 기존과 같은 방식으로 나와야 합니다.

권장 구현:

이미지 부모 작업이 resources를 직접 파일로 다운로드하지 않습니다. 대신 기존 Civitai 모델 다운로드 job을 생성합니다.

자식 job 생성 예:

```python
child = ParsedDownload(
    source="civitai",
    raw_input=f"https://civitai.com/api/v1/model-versions/{model_version_id}",
    civitai_version_id=str(model_version_id),
)
child_job_id = db.create_job(child)
enqueue_job(child_job_id)
```

또는 raw input을 사람이 보기 좋은 URL로 저장합니다.

```python
raw_input=f"https://civitai.com/models/{model_id}?modelVersionId={model_version_id}"
```

권장:

- `model_id`가 있으면 model page URL을 raw_input으로 사용
- `model_id`가 없으면 API model-version URL 사용
- 실제 다운로드 기준은 `civitai_version_id`

중복 방지:

- 같은 이미지 안에서 같은 `modelVersionId`가 여러 번 나오면 한 번만 생성
- 이미 완료된 동일 version download가 있는지 DB나 파일시스템 기준으로 dedupe할지 결정 필요

초기 구현 권장:

- 같은 부모 작업 내부 중복만 제거합니다.
- 전역 중복 다운로드 방지는 후속 개선으로 둡니다.

이유:

- 기존 HugCivi도 같은 URL을 다시 넣으면 새 작업을 만들 수 있습니다.
- 전역 dedupe는 target route, file selector, 기존 파일 존재 여부까지 봐야 해서 범위가 커집니다.

## Resources type 처리

Civitai image generation data의 resource type은 다음처럼 들어올 수 있습니다.

```text
Checkpoint
LORA
VAE
Upscaler
ControlNet
Embedding
```

자식 job은 기존 `download_civitai()`가 `/api/v1/model-versions/{id}`를 다시 조회하고 `classify_civitai()`로 분류합니다.

따라서 부모 작업에서 resource type별 저장 위치를 직접 결정하지 않습니다.

원칙:

- 부모 작업은 `modelVersionId`만 전달합니다.
- 저장 위치 결정은 기존 `classify_civitai()`와 route 설정이 담당합니다.
- 썸네일 결정도 기존 Civitai model version metadata의 `images`를 사용합니다.

이렇게 해야 "기존 Civitai 모델페이지 URL로 다운받듯 다 정리되고 썸네일까지 나오는 방식"이 유지됩니다.

## Parent Job Metadata

이미지 부모 작업은 자식 job id를 기록해야 합니다.

저장 위치:

- `_civitai_image_metadata.json`
- parent job `metadata_json`

권장 필드:

```json
{
  "resource_downloads": [
    {
      "model_version_id": "3059910",
      "model_id": "2061456",
      "name": "Example LoRA",
      "type": "LORA",
      "child_job_id": 123,
      "status": "queued"
    }
  ]
}
```

자식 작업 상태가 이후 바뀌더라도 sidecar JSON의 status를 실시간 갱신할 필요는 없습니다.

뷰어에서는 `child_job_id`가 있으면 현재 job list에서 상태를 매칭해 보여줄 수 있습니다. 초기 구현에서는 sidecar에 기록된 `queued` 상태만 보여줘도 됩니다.

## 작업 생성 시점

권장 순서:

1. 이미지 API 조회
2. generation data 정규화
3. 이미지 파일 다운로드
4. sidecar JSON 저장
5. parent job을 done 가능한 상태로 업데이트
6. resource child jobs 생성 및 enqueue
7. sidecar JSON에 child job id 반영

주의:

- 이미지 저장이 실패하면 child job을 만들지 않습니다.
- generation data가 없으면 child job도 만들 수 없습니다.
- resource 일부가 `modelVersionId`가 없으면 skip합니다.

로그 예:

```text
civitai.image.metadata.start image_id=135240496
civitai.image.metadata.ok image_id=135240496 resources=2
civitai.image.asset.saved file=image_135240496.jpeg
civitai.image.resource.queue modelVersionId=2130256 child_job_id=123 type=Checkpoint
civitai.image.resource.queue modelVersionId=3059910 child_job_id=124 type=LORA
civitai.image.done image_id=135240496 queued_resources=2
```

## 실패 정책

이미지 API 실패:

- parent job 실패
- child job 생성 없음

이미지 다운로드 실패:

- parent job 실패
- child job 생성 없음

generation metadata 없음:

- 이미지 URL이 있으면 이미지 저장은 성공
- `generation_data.available=false`
- child job 생성 없음
- parent job log에 warning

일부 resource에 modelVersionId 없음:

- 해당 resource만 skip
- parent job은 성공

자식 job 실패:

- parent job은 이미 이미지 저장 완료 상태입니다.
- 자식 job은 기존 Civitai 다운로드 실패 정책을 따릅니다.
- parent sidecar를 실패 상태로 되돌리지 않습니다.

## 기존 Civitai 다운로드 재사용 포인트

반드시 재사용할 기존 함수:

- `download_civitai()`
- `fetch_json()`
- `auth_headers()`
- `stream_download()`
- `classify_civitai()`
- `pick_civitai_file()`
- `update_job_archive_info()`
- `write_metadata()`
- `thumbnail_url_for_path()`
- `redact_metadata()`
- `redact_sensitive_text()`

새로 만드는 함수는 이미지 페이지 저장과 자식 job 생성에 집중합니다.

## DB 및 Queue 고려

자식 작업 생성은 기존 `db.create_job()`과 `enqueue_job()`을 사용합니다.

주의:

- `db.create_job()`은 `raw_input`을 redaction합니다.
- child job 생성 후 `enqueue_job(child_job_id)`를 호출해야 worker가 처리합니다.
- parent job 실행 중 child job을 생성하면 scheduler가 같은 Civitai provider bucket에서 순차 처리합니다.

provider limit:

- parent job도 `civitai`
- child jobs도 `civitai`
- `QUEUE_PER_PROVIDER_LIMIT=1`이면 부모가 끝난 후 자식들이 차례로 실행됩니다.
- `QUEUE_PER_PROVIDER_LIMIT>1`이면 리소스들이 병렬 처리될 수 있습니다.

이 동작은 기존 provider limit 정책과 일치합니다.

## 라이브러리 카드

이미지 페이지 archive 카드:

- source: `civitai`
- model_category: `Civitai Image Page`
- model_type: `Image`
- thumbnail_url: 저장된 이미지 파일 preview
- source_url: Civitai image page URL
- has_media: true

자식 모델/LoRA 카드:

- 기존 Civitai 모델 다운로드와 동일
- source: `civitai`
- model_category: `LoRA`, `Image Checkpoint`, `VAE`, `Upscaler` 등
- thumbnail_url: Civitai model metadata 기반
- target_dir: 기존 route 설정 기반
- sidecar: `_civitai_metadata.json`

## Source URL 처리

파일:

- `app/main.py`

`source_url_for_job()`에 Civitai image 분기를 추가합니다.

```python
if parsed.source == "civitai":
    if parsed.civitai_image_url:
        return parsed.civitai_image_url
    if parsed.civitai_image_id:
        return f"https://civitai.com/images/{quote(parsed.civitai_image_id)}"
    ...
```

sidecar metadata에도 `source_url`을 저장해, job row가 삭제되어도 library card에서 원본 URL을 복원할 수 있게 합니다.

## 보안 고려

토큰:

- Civitai token은 DB setting 또는 env에서만 읽습니다.
- token 값을 sidecar JSON, parent metadata, child parsed raw_input, 로그에 저장하지 않습니다.

URL:

- 원본 URL의 query token은 `redact_sensitive_text()`를 통과시킵니다.
- Civitai image original URL도 저장 전 redaction합니다.

Authorization:

- Civitai API 요청에는 Authorization 허용
- `image.civitai.com` 이미지 요청에는 필요 시 Authorization 허용
- 외부 host redirect에는 Authorization이 붙지 않도록 검토 필요

경로:

- username, image id, filename은 `sanitize_segment()` 또는 `safe_join()` 경로를 통과시킵니다.
- filename은 원본 URL basename을 그대로 쓰지 않고 `image_{id}.{ext}`로 제한합니다.

## 테스트 계획

### Parser

테스트 파일 후보:

- `tests/test_civitai_image_parser.py`

검증:

- Civitai image URL이 `civitai_image_id`로 파싱되는지
- red/green host도 파싱되는지
- 기존 Civitai model URL 파싱이 깨지지 않았는지

### Normalize

테스트 파일 후보:

- `tests/test_civitai_image_metadata.py`

검증:

- prompt 추출
- negative prompt 추출
- seed, steps, sampler 등 metadata 변환
- modelVersionIds 추출
- resources used 보강
- copy all text 생성
- metadata missing 시 `available=false`

### Downloader

테스트 파일 후보:

- `tests/test_civitai_image_downloader.py`

mock 대상:

- `fetch_json()`
- `stream_download()`
- `db.create_job()`
- `enqueue_job()`

검증:

- 이미지 파일 저장 경로
- `_civitai_image_metadata.json` 생성
- parent job update 필드
- resources used child job 생성
- 같은 modelVersionId 중복 제거
- modelVersionId 없는 resource skip
- token 미노출

### Library

테스트 파일 후보:

- 기존 `tests/test_review_fixes.py` 또는 새 테스트

검증:

- `_civitai_image_metadata.json`이 sidecar로 인식되는지
- 이미지 archive 폴더가 library item으로 보이는지
- thumbnail_url이 생성되는지
- source_url이 Civitai image URL로 복원되는지

### Viewer Payload

검증:

- `/api/media/list` 응답에 `metadata.kind == "civitai_image_page"` 포함
- `generation_data.prompt.text` 포함
- resources 배열 포함
- resource별 `href`, `model_version_id`, `type`, `weight` 포함
- 이미지 페이지 원본 `source_url` 포함
- 기존 일반 media folder 응답에는 영향 없음

### Viewer Rendering

검증:

- Civitai image archive는 왼쪽 이미지, 오른쪽 `Generation data` 패널로 표시
- 원본 이미지 페이지 URL이 하이퍼링크로 표시
- Resources used의 각 이름이 Civitai 원본 모델 페이지 하이퍼링크로 표시
- `COPY ALL`, Prompt `COPY`, Negative prompt `COPY` 버튼이 표시
- Prompt, Negative prompt 텍스트 복사가 동작
- metadata chip 또는 table에 seed, steps, sampler 등 주요 값 표시
- 일반 media archive에는 generation data 패널이 나타나지 않음

### Resource Health

검증:

- health check는 뷰어 진입 시 자동 호출되지 않음
- `Check resources` 버튼 클릭 시에만 health endpoint 호출
- 다운로드된 modelVersionId는 녹색 상태로 표시
- 보관함에서 찾지 못한 modelVersionId는 빨간색 상태로 표시
- DB row가 없어도 `_civitai_metadata.json` sidecar와 실제 파일이 있으면 present로 판단
- 많은 라이브러리 카드가 있어도 health check는 현재 열린 이미지의 resources만 검사

## 구현 단계

### 1단계: 파서와 모델 필드

- `ParsedDownload`에 `civitai_image_id`, `civitai_image_url` 추가
- `parse_civitai_url()`에 `/images/{id}` 분기 추가
- parser 테스트 추가

완료 기준:

- Civitai 이미지 URL이 job으로 생성 가능
- 기존 Civitai 모델 URL 테스트 통과

### 2단계: 이미지 API 조회와 metadata normalize

- Civitai image API fetch helper 추가
- generation data normalize helper 추가
- copy all text 생성 helper 추가
- normalize 테스트 추가

완료 기준:

- fixture payload에서 prompt, negative prompt, resources, metadata가 안정적으로 추출됨

### 3단계: 이미지 파일 저장

- `download_civitai()`에 image 분기 추가
- `/data/civitai/images/...` target 생성
- `stream_download()`로 이미지 저장
- `_civitai_image_metadata.json` 저장
- parent job summary 업데이트

완료 기준:

- Civitai image URL 하나로 이미지 archive folder 생성
- 라이브러리 카드 썸네일 표시

### 4단계: Resources used child jobs

- normalize 결과에서 `model_version_id` 있는 resource 수집
- parent 작업 내부 중복 제거
- 기존 Civitai model download용 `ParsedDownload` 생성
- `db.create_job()` 및 `enqueue_job()` 호출
- child job id를 parent sidecar에 기록

완료 기준:

- 이미지 URL 입력 후 LoRA/Checkpoint 등이 기존 Civitai 작업으로 큐에 추가됨
- 각 자식 작업은 기존 저장 위치와 기존 카드 형식으로 저장됨

### 5단계: 기존 뷰어 metadata 렌더링

- `_civitai_image_metadata.json`을 sidecar 목록에 추가
- `/api/media/list` 응답에 Civitai image metadata 포함
- 기존 media viewer에 오른쪽 generation data panel 추가
- 왼쪽 이미지, 오른쪽 generation data 형태의 레이아웃 적용
- 이미지 페이지 원본 URL 하이퍼링크 표시
- resources 이름을 Civitai 원본 모델 페이지 하이퍼링크로 표시
- prompt, negative prompt, metadata, resources 렌더링
- `COPY ALL`, Prompt `COPY`, Negative prompt `COPY` 버튼 추가

완료 기준:

- 이미지 archive를 열면 이미지와 generation data를 한 화면에서 확인 가능
- Prompt, Negative prompt, COPY ALL 복사가 가능
- Resources used에서 원본 모델 페이지로 이동 가능
- 일반 media archive 표시에는 회귀 없음

### 6단계: Resources used 수동 헬스체크

- resource health endpoint 추가
- DB job row에서 `civitai_version_id` 기반 present 여부 확인
- `/data` sidecar `_civitai_metadata.json` 기반 fallback 확인
- 실제 모델 파일 존재 여부 확인
- viewer에 `Check resources` 버튼 추가
- 버튼 클릭 시에만 health endpoint 호출
- 각 resource row에 빨간색/녹색 status 표시

완료 기준:

- health check가 자동으로 돌지 않음
- 버튼 클릭 후 다운로드된 리소스는 녹색, 없는 리소스는 빨간색으로 표시
- 많은 이미지 archive가 있어도 현재 열린 이미지의 resources만 검사

### 7단계: 보안과 오류 메시지 정리

- token redaction 테스트
- 401/403/404/429 로그 메시지 정리
- metadata missing 경고 처리
- resource skip 로그 처리

완료 기준:

- 실패 원인이 job log에서 읽힘
- token이 로그와 JSON에 남지 않음

### 8단계: 문서 갱신

- README 주요 기능에 Civitai 이미지 페이지 URL 지원 추가
- 제한 사항에 "Resources used는 Civitai에 등록된 modelVersionId가 있는 항목만 자동 작업 생성" 명시
- 뷰어에서 resource health check는 버튼 클릭 시에만 실행된다는 점 명시

## 예상 변경 파일

필수:

- `app/models.py`
- `app/parsers.py`
- `app/downloader.py`
- `app/main.py`
- `app/templates/index.html`
- `app/static/style.css`
- `tests/`

가능:

- `README.md`

## 최종 완료 조건

기능 완료 기준:

1. 기존 입력창에 Civitai image URL 입력 가능
2. 이미지 페이지 archive가 `/data/civitai/images/...`에 저장됨
3. archive sidecar에 prompt, negative prompt, metadata, resources used 저장됨
4. 기존 HugCivi 뷰어에서 왼쪽 이미지, 오른쪽 generation data 형태로 보기 좋게 렌더링됨
5. Resources used의 `modelVersionId`마다 기존 Civitai 다운로드 작업이 자동 생성됨
6. LoRA, Checkpoint, VAE, Upscaler 등이 기존 저장 위치와 기존 카드 방식으로 정리됨
7. Prompt, Negative prompt, COPY ALL 복사 버튼이 동작함
8. 이미지 페이지 원본 주소와 Resources used 이름이 하이퍼링크로 동작함
9. resource health check는 버튼 클릭 시에만 실행되고, 다운로드 여부를 빨간색/녹색으로 표시함
10. token이 로그, JSON, HTML에 남지 않음
11. 기존 Civitai 모델 URL 다운로드 기능에 회귀 없음

## 결정 사항

현재 계획의 결정 사항:

- `source="civitai"` 유지
- Civitai image URL은 `civitai_image_id`로 구분
- Resources used 다운로드는 child job 방식
- 리소스 저장 위치 결정은 기존 `classify_civitai()`에 위임
- 이미지 archive는 `/data/civitai/images/...`
- generation data는 기존 media viewer를 확장해 렌더링
- 뷰어 레이아웃은 왼쪽 이미지, 오른쪽 `Generation data` 패널
- Prompt, Negative prompt, COPY ALL 복사 버튼 포함
- 이미지 페이지 원본 URL과 Resources used 모델명은 하이퍼링크
- Resources used 다운로드 여부 헬스체크는 버튼 클릭 시에만 실행
- 헬스체크 결과는 빨간색/녹색으로만 표시

추후 결정 필요:

- 전역 중복 modelVersionId 다운로드 방지 여부
- metadata 없는 이미지도 성공 처리할지 여부
- child job 상태를 parent sidecar에 실시간 반영할지 여부
