# Civitai 이미지 저장 기능 병합 기술 고려 보고서

작성일: 2026-07-01

검토 소스:

- `/home/inri/문서/CivitaiOfflineSaver_restored`
- `/home/inri/문서/CivitaiOfflineSaver_restored/HUGCIVI_MERGE_README.md`
- `/home/inri/문서/CivitaiOfflineSaver_restored/CivitaiOfflineSaver.py`
- `/home/inri/문서/CivitaiOfflineSaver_restored/README.md`

병합 대상:

- `HugCivi`
- 기존 FastAPI 웹 앱, SQLite 작업 DB, 공급자별 큐, Civitai 토큰 저장, 라이브러리 카드, 미디어 미리보기 구조

## 결론

`CivitaiOfflineSaver_restored`를 통째로 병합하는 방식은 적합하지 않습니다. HugCivi에는 이미 웹 UI, 인증 설정, 작업 큐, 저장 경로 관리, Docker/Portainer 배포 구조, Civitai 모델 다운로드 로직이 있으므로 새 독립 앱을 넣을 이유가 없습니다.

병합 대상은 단독 앱의 UI나 실행 구조가 아니라 Civitai 이미지 페이지를 해석하는 백엔드 로직입니다.

권장 병합 형태:

1. 기존 `source="civitai"`를 유지합니다.
2. `ParsedDownload`에 `civitai_image_id`, `civitai_image_url` 필드를 추가합니다.
3. `download_civitai()` 초입에서 `civitai_image_id`가 있으면 모델 파일 다운로드가 아니라 이미지 저장 흐름으로 분기합니다.
4. 저장 결과는 이미지 파일과 sidecar JSON만 남깁니다.
5. 추가 UI는 만들지 않습니다. 기존 입력창, 작업 카드, 라이브러리 카드, 미디어 보기 기능만 사용합니다.

## 비범위

아래 항목은 HugCivi 병합 범위에서 제외하는 것이 좋습니다.

- Tkinter GUI
- restored 프로젝트의 `web_ui.py`
- restored 프로젝트의 `Dockerfile`, `docker-compose.yml`
- PyInstaller exe 빌드 스크립트
- 브라우저 로그인 또는 성인 확인 fallback
- 저장 결과용 standalone `index.html` 렌더러
- Prompt, Negative prompt, COPY ALL 전용 신규 UI
- 리소스 파일 다운로드 옵션을 위한 신규 UI

추가 UI를 만들 계획이 없다면 초기 병합은 "URL을 넣으면 이미지와 메타데이터가 저장되고, 기존 라이브러리에서 이미지 폴더로 보인다"까지만 잡는 것이 가장 작고 안정적입니다.

## restored 프로젝트의 핵심 기능

`CivitaiOfflineSaver.py`에서 HugCivi에 참고할 가치가 큰 부분은 다음입니다.

- Civitai 이미지 URL에서 `/images/{imageId}` 추출
- `/api/v1/images?imageId={id}&withMeta=true` 호출
- 응답 `items[0]`에서 이미지 URL, 작성자, 크기, 생성 시각 추출
- 응답 `meta.meta`에서 prompt, negativePrompt, seed, sampler, steps 등 추출
- `modelVersionIds`가 있을 때 `/api/v1/model-versions/{id}`를 추가 호출해 Resources used 보강
- Civitai resource의 모델명, 타입, 버전명, weight, modelId, modelVersionId, downloadUrl, primary file 정보 정규화
- `copyAllText` 생성
- Civitai API 오류를 401, 403, 404, 429 등으로 구분해 로그에 남기는 정책
- token 또는 secret query가 JSON, HTML, 로그에 남지 않게 redaction하는 처리

반대로 아래 부분은 HugCivi에 직접 가져오지 않는 편이 좋습니다.

- `urllib` 기반 다운로드 구현
- 브라우저 CDP fallback
- static HTML 렌더러
- standalone 저장 폴더 UI
- 자체 웹 서버

HugCivi에는 이미 `requests.Session`, `request_with_safety()`, `stream_download()`, `.part` resume, job control, rate limit, redaction, `thumbnail_url_for_path()`가 있습니다. 따라서 다운로드와 저장은 HugCivi의 기존 공용 함수를 재사용해야 합니다.

## 권장 데이터 모델

`app/models.py`의 `ParsedDownload`에 다음 필드를 추가합니다.

```python
civitai_image_id: str | None = None
civitai_image_url: str | None = None
```

`SourceType`은 초기 병합에서는 변경하지 않는 것이 좋습니다.

권장:

```python
SourceType = Literal["huggingface", "civitai", "generic", "comfyui", "hitomi", "gallerydl"]
```

이유:

- 기존 Civitai 토큰과 queue provider key를 그대로 공유할 수 있습니다.
- `provider_key_for_parsed()`가 이미 `source="civitai"`를 `civitai` bucket으로 묶습니다.
- `db.active_job_roots()`가 `source="civitai"` 작업을 이미 `/data/civitai`와 Civitai route root 후보로 보호합니다.
- `source="civitai_image"`를 새로 만들면 DB, queue, 라이브러리 source normalization, 카드 badge, source URL 계산을 더 많이 수정해야 합니다.

단점:

- 작업 목록의 source만 보면 Civitai 모델 다운로드와 Civitai 이미지 저장이 둘 다 `civitai`로 보입니다.

보완:

- `model_category`를 `Civitai Image Post`로 저장합니다.
- `model_type`을 `Image` 또는 `Image by {username}`으로 저장합니다.
- `source_url_for_job()`에서 `civitai_image_url`을 우선 반환하게 합니다.

## URL 파서

수정 위치:

- `app/parsers.py`

현재 `parse_civitai_url()`은 Civitai model, modelVersion, by-hash, API download URL만 처리합니다. 여기에 이미지 URL 처리를 가장 앞쪽에 추가하는 것이 좋습니다.

지원 대상:

```text
https://civitai.com/images/135240496
https://www.civitai.com/images/135240496
https://civitai.red/images/135240496
https://civitai.green/images/135240496
```

권장 반환:

```python
ParsedDownload(
    source="civitai",
    raw_input=url,
    target_subdir=target_subdir,
    civitai_image_id=image_id,
    civitai_image_url=url,
)
```

주의:

- `/images/{id}`는 model URL보다 먼저 검사해야 합니다.
- query string에 token류 값이 있을 수 있으므로 DB에 들어가는 `raw_input`은 기존 `db.create_job()`의 redaction을 계속 통과시켜야 합니다.
- `CIVITAI_HOSTS`에는 현재 `civitai.com`, `civitai.red`, `civitai.green` 계열이 들어 있습니다. restored 코드에는 `civitaired.com`도 있으나 HugCivi에서 실제로 지원할지 별도 판단이 필요합니다.

## Downloader 흐름

수정 위치:

- `app/downloader.py`

권장 구조:

```python
def download_civitai(job_id: int, parsed: ParsedDownload) -> None:
    if parsed.civitai_image_id:
        download_civitai_image(job_id, parsed)
        return

    # existing Civitai model download flow
```

새 helper는 `download_civitai()` 안에 모두 넣지 말고 함수로 분리하는 편이 좋습니다.

권장 helper:

```python
def download_civitai_image(job_id: int, parsed: ParsedDownload) -> None: ...
def fetch_civitai_image_item(session, image_id, job_id) -> dict[str, Any]: ...
def fetch_civitai_model_versions(session, version_ids, job_id) -> list[dict[str, Any]]: ...
def normalize_civitai_image_record(item, version_resources, source_url) -> dict[str, Any]: ...
def classify_civitai_image(record) -> dict[str, Any]: ...
```

기본 흐름:

1. `db.get_secret("CIVITAI_TOKEN")`로 토큰을 읽습니다.
2. `requests.Session()`에 `User-Agent`와 Civitai token header를 설정합니다.
3. `GET {CIVITAI_API_BASE}/images?imageId={id}&withMeta=true`를 호출합니다.
4. `items`에서 요청한 image id와 일치하는 item을 선택합니다.
5. item의 image URL, username, width, height, createdAt을 정규화합니다.
6. `item.meta.meta`가 있으면 generation data를 정규화합니다.
7. `modelVersionIds`가 있으면 `GET {CIVITAI_API_BASE}/model-versions/{id}`로 보강합니다.
8. target directory를 생성합니다.
9. 이미지 파일을 `stream_download()`로 저장합니다.
10. `_civitai_image_metadata.json` sidecar를 저장합니다.
11. job row의 `target_dir`, `filename`, `thumbnail_url`, `model_title`, `model_category`, `metadata_json`을 갱신합니다.

## API 응답 정규화

restored 구현 기준으로 Civitai 이미지 API 응답에서 중요한 구조는 다음입니다.

```text
items[0].id
items[0].postId
items[0].url
items[0].width
items[0].height
items[0].username
items[0].createdAt
items[0].nsfwLevel
items[0].modelVersionIds
items[0].meta.meta.prompt
items[0].meta.meta.negativePrompt
items[0].meta.meta.resources
items[0].meta.meta.hashes
```

`meta`는 한 번 더 감싸진 형태입니다.

```python
meta_container = item.get("meta") or {}
meta = meta_container.get("meta") if isinstance(meta_container, dict) else None
```

정규화된 generation data는 화면 DOM이 아니라 JSON을 기준으로 보관해야 합니다.

권장 구조:

```json
{
  "source": "civitai",
  "kind": "civitai_image",
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
      {"label": "Steps", "value": "25"}
    ],
    "resources": [
      {
        "name": "FSThicc - Style LoRA",
        "type": "LORA",
        "version": "v4.0",
        "weight": "0.44",
        "model_id": "2061456",
        "model_version_id": "3059910",
        "href": "https://civitai.com/models/2061456",
        "base_model": "Illustrious"
      }
    ],
    "model_version_ids": ["2130256", "3059910"]
  },
  "local_files": {
    "primary_image": "image_135240496.jpeg"
  },
  "archive_info": {
    "model_title": "Civitai image 135240496",
    "model_category": "Civitai Image Post",
    "model_type": "Image",
    "base_model": "Illustrious",
    "file_format": "jpeg",
    "precision": "800 x 1000"
  }
}
```

필드명은 기존 HugCivi metadata 스타일에 맞춰 snake_case를 권장합니다. restored의 `post-info.json`은 `sourceUrl`, `primaryImage`, `generationData` 같은 camelCase를 사용하지만, HugCivi 내부 metadata에서는 snake_case가 더 자연스럽습니다.

단, 향후 restored 결과와 상호 변환할 가능성이 있다면 `generation_data.raw_civitai_meta` 또는 `raw` 하위에 원본 주요 필드를 보존할 수 있습니다.

## 저장 위치

권장 기본 저장 위치:

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

이 구조의 장점:

- 기존 `db.civitai_expected_roots()`가 `/data/civitai`를 이미 active job 보호 후보에 포함합니다.
- 라이브러리 인덱서가 폴더 안의 이미지 파일을 미디어로 인식할 수 있습니다.
- 기존 media viewer에서 별도 UI 없이 이미지를 열 수 있습니다.
- Civitai 모델 route 설정과 섞이지 않습니다.

필요 수정:

- `app/main.py`의 `SIDECAR_FILENAMES`에 `_civitai_image_metadata.json`을 추가합니다.
- `source_url_from_metadata()` 또는 metadata 자체의 `source_url` 필드를 통해 원본 URL을 표시합니다.

대안:

- sidecar 파일명을 `_archive_metadata.json`으로 쓰면 `SIDECAR_FILENAMES` 수정 없이 바로 라이브러리 폴더 인덱싱이 가능합니다.
- 다만 Civitai 이미지 전용 metadata임이 파일명에서 덜 명확합니다.

보고서 기준 권장안은 `_civitai_image_metadata.json` 추가입니다.

## Job row 표시 전략

추가 UI를 만들지 않는 조건에서는 기존 job row와 library card 필드만 채우는 것이 중요합니다.

권장 job update:

```python
db.update_job(
    job_id,
    target_dir=str(target),
    filename=saved_image.name,
    progress_bytes=final_size,
    total_bytes=final_size,
    model_title=f"Civitai image {image_id}",
    model_category="Civitai Image Post",
    model_type=f"Image by {username}" if username else "Image",
    base_model=base_model,
    file_format=image_ext,
    precision=f"{width} x {height}" if width and height else "",
    thumbnail_url=thumbnail_url_for_path(target),
    metadata_json=json.dumps(redact_metadata(summary), ensure_ascii=False),
)
```

`source_url_for_job()`에는 다음 분기를 추가합니다.

```python
if parsed.source == "civitai" and parsed.civitai_image_url:
    return parsed.civitai_image_url
if parsed.source == "civitai" and parsed.civitai_image_id:
    return f"https://civitai.com/images/{quote(parsed.civitai_image_id)}"
```

이 정도만 해도 기존 UI에서 다음이 가능합니다.

- 작업 목록에 Civitai 이미지 작업 표시
- 완료 후 라이브러리 카드 표시
- 카드 썸네일 표시
- URL 바로가기
- 기존 미디어 보기로 이미지 열기

## 인증과 secret 처리

HugCivi에는 이미 `CIVITAI_TOKEN` 저장, `auth_headers()`, `redact_sensitive_text()`, `redact_metadata()`가 있습니다. 이를 재사용합니다.

반드시 지킬 것:

- Civitai token 값을 `parsed_json`, `metadata_json`, sidecar JSON, log에 저장하지 않습니다.
- 원본 URL query에 `token`, `api_key`, `auth`, `signature` 등이 있으면 기존 redaction을 통과시킵니다.
- metadata sidecar 저장 전 `redact_metadata()`를 적용합니다.
- Civitai API 요청과 `image.civitai.com` 이미지 요청에만 Authorization header가 붙도록 합니다.

주의할 점:

- 현재 `download_civitai()`는 `session.headers`에 Authorization을 전역으로 넣습니다.
- Civitai model download URL이나 image URL이 다른 host로 redirect될 때 `requests`가 Authorization을 유지하는지 확인해야 합니다.
- 특히 향후 resource mirror URL을 받을 경우 외부 host에 Authorization이 붙지 않도록 별도 session 또는 per-request header 구성이 더 안전합니다.

초기 병합에서는 이미지 원본 URL이 `image.civitai.com` 계열인지 확인하고, 그렇지 않은 경우 Authorization 없는 요청으로 다운로드하는 방식을 검토할 수 있습니다.

## 오류 처리

restored 프로젝트는 다음 로그 구분을 갖고 있습니다.

- 401: token invalid 또는 required
- 403: forbidden, age check, browsing level 문제 가능성
- 404: 삭제 또는 숨김
- 429: rate limited
- API items empty: 삭제, 비공개, 성인 설정, browsing level 문제 가능성
- metadata missing: generation data 숨김 또는 접근 제한

HugCivi에서는 `requests.HTTPError` 메시지 그대로 노출하는 것보다 job log에 짧은 원인 코드를 남기는 편이 좋습니다.

권장 로그:

```text
civitai.image.metadata.start image_id=135240496
civitai.image.metadata.ok image_id=135240496 resources=2
civitai.image.metadata.missing image_id=135240496
civitai.image.asset.download url=...
civitai.image.asset.saved path=...
civitai.image.failed status=403 reason=forbidden_or_age_check
civitai.image.failed status=429 reason=rate_limited
```

정책 결정이 필요한 부분:

- 이미지 URL은 있는데 generation metadata가 없을 때 작업을 실패시킬지, 이미지와 `generation_data.available=false`를 저장하고 경고로 끝낼지 결정해야 합니다.

권장:

- 이미지 자체를 받을 수 있으면 작업은 성공 처리합니다.
- generation metadata가 없으면 sidecar에 `generation_data.available=false`와 `metadata_warning`을 남깁니다.
- 이미지 URL도 없거나 API item을 찾지 못하면 실패 처리합니다.

이유:

- HugCivi는 보관 도구이므로 이미지 저장 성공 자체가 가치가 있습니다.
- 프롬프트 정보가 비공개인 게시물도 있을 수 있습니다.
- 실패 정책을 너무 엄격하게 잡으면 공개 이미지인데 metadata 숨김 때문에 보관이 불가능해집니다.

## HTML fallback 여부

restored 프로젝트는 API 실패 시 HTML의 `__NEXT_DATA__`에서 `image.get`, `image.getGenerationData`를 읽는 fallback을 갖고 있습니다.

초기 HugCivi 병합에서는 API-only를 권장합니다.

이유:

- Docker/Portainer 환경에서 브라우저 fallback은 부담이 큽니다.
- Civitai의 Next.js 내부 구조는 API보다 더 자주 깨질 수 있습니다.
- HugCivi는 이미 서버 앱이므로 사람에게 "브라우저 로그인 창"을 띄우는 방식과 맞지 않습니다.
- 추가 UI를 만들지 않는 조건과도 맞지 않습니다.

다만 API가 자주 metadata를 누락한다면 2차 작업으로 HTML fallback을 pure HTTP 파서만 추가할 수 있습니다. 이 경우에도 브라우저 CDP fallback은 제외하는 편이 좋습니다.

## Resources used 파일 다운로드

restored 프로젝트에는 선택 시 이미지에 연결된 모델/LoRA primary file을 `resources/`에 받는 기능이 있습니다.

초기 HugCivi 병합에서는 비활성화가 안전합니다.

이유:

- 추가 UI를 만들지 않으면 사용자가 "모델/LoRA까지 같이 다운로드" 여부를 선택할 방법이 없습니다.
- Checkpoint와 LoRA 파일은 수백 MB에서 수 GB까지 커질 수 있습니다.
- 이미지 저장 작업이라고 생각한 사용자가 예기치 않게 대용량 모델 다운로드를 시작할 수 있습니다.
- HugCivi는 이미 Civitai 모델 URL 다운로드를 지원하므로 resource 파일은 사용자가 필요한 모델 URL을 별도로 넣어도 됩니다.

향후 필요 시 고려할 수 있는 방식:

- 기본값은 무조건 OFF
- UI 없이 환경변수 또는 DB setting으로만 enable
- `modelVersionId`가 있는 resource만 대상
- `pick_civitai_file()`과 `stream_download()` 재사용
- 외부 mirror는 제외하고 Civitai API download URL만 사용
- resource download 결과는 `resource_files` 배열에 status, file_name, local_path, size_bytes를 기록

## 라이브러리 인덱싱

기존 라이브러리 인덱싱 흐름은 Civitai 이미지 저장과 잘 맞습니다.

관련 코드:

- `app/main.py`
- `SIDECAR_FILENAMES`
- `library_items()`
- `library_item_for_path()`
- `media_files_for_path()`
- `thumbnail_url_for_media()`
- `/api/media/list`

이미지 저장 폴더에 이미지 파일이 있으면:

- `should_index_directory()`가 media file이 있는 directory를 인덱싱합니다.
- `library_item_for_path()`가 첫 미디어 파일을 thumbnail로 씁니다.
- `isMediaArchiveJob()`가 `has_media` 또는 `media_count`로 기존 media viewer를 활성화합니다.

따라서 추가 UI 없이도 저장 이미지를 볼 수 있습니다.

필요 보강:

- `_civitai_image_metadata.json`을 sidecar 목록에 추가
- `library_item_category()`가 `model_category="Civitai Image Post"`를 우선 사용하도록 sidecar에 archive_info 저장
- `normalize_library_source()`는 `source="civitai"`를 그대로 쓰면 변경 불필요

## 보안 고려

경로:

- 이미지 ID, username, filename은 반드시 `safe_join()`과 `sanitize_segment()`를 통과시킵니다.
- username이 비어 있으면 `unknown`을 씁니다.
- URL path의 원본 파일명은 신뢰하지 말고 최종 저장명은 `image_{id}.{ext}` 형태로 제한하는 편이 안전합니다.

URL:

- `source_url`은 redaction된 canonical image page URL만 저장합니다.
- `original_url`은 이미지 원본 URL이므로 token query 제거 후 저장합니다.
- API token은 어떤 JSON에도 저장하지 않습니다.

네트워크:

- `request_with_safety()`의 retry, rate limit, job control을 사용합니다.
- image download도 `stream_download()`를 사용해 pause/delete/stall 처리를 기존 다운로드와 맞춥니다.
- Civitai request interval 설정을 그대로 적용합니다.

메타데이터:

- `rawMeta`를 전부 저장하면 prompt뿐 아니라 알 수 없는 필드가 들어올 수 있습니다.
- token류 key나 URL query는 `redact_metadata()`를 통과시킨 뒤 저장합니다.
- 관리용 summary에는 필요한 필드만 저장하고, 원본 raw는 별도 하위 key에 최소화해서 보관하는 편이 좋습니다.

## 테스트 계획

최소 테스트:

1. `parse_input("https://civitai.com/images/135240496")`
   - `source == "civitai"`
   - `civitai_image_id == "135240496"`
   - `civitai_image_url` 보존

2. `parse_input("https://civitai.green/images/135240496")`
   - green/red host도 허용

3. 기존 Civitai model URL 파싱 회귀
   - `https://civitai.com/models/2061456?modelVersionId=3059910`
   - 기존 `civitai_model_id`, `civitai_version_id` 동작 유지

4. Civitai image API normalize unit test
   - fake `items[0]` payload로 prompt, negativePrompt, metadata, resources 추출
   - `modelVersionIds`와 fetched model version payload를 합쳐 resource 보강

5. metadata missing test
   - image URL은 저장 가능
   - `generation_data.available=false`
   - job은 성공 또는 경고 정책대로 처리

6. downloader unit test
   - `fetch_json()` mock
   - `stream_download()` mock
   - target path와 sidecar JSON 생성 확인
   - `db.update_job()` 필드 확인

7. token redaction test
   - raw input query token 제거
   - sidecar JSON에 token 미포함
   - log에 token 미포함

8. library indexing test
   - `_civitai_image_metadata.json`과 이미지 파일이 있는 폴더가 library item으로 표시
   - thumbnail URL이 `/api/fs/preview?...`로 생성
   - `source_url`이 Civitai image page URL

9. 기존 Civitai model download 회귀
   - `download_civitai()`가 `civitai_image_id` 없을 때 기존 모델 다운로드 흐름을 그대로 탑니다.

## 구현 순서

권장 순서:

1. `ParsedDownload`에 Civitai image 필드 추가
2. `parse_civitai_url()`에 `/images/{id}` 파싱 추가
3. `source_url_for_job()`에 Civitai image URL 처리 추가
4. `_civitai_image_metadata.json`을 `SIDECAR_FILENAMES`에 추가
5. Civitai image API fetch와 normalize helper 추가
6. `download_civitai()`에 image 분기 추가
7. 이미지 파일 저장과 sidecar metadata 저장
8. job row summary와 thumbnail 갱신
9. parser, normalize, downloader, library indexing 테스트 추가
10. README에 "Civitai 이미지 URL도 입력 가능" 한 줄만 추가

## 주요 리스크

### Civitai API 구조 변경

Civitai API의 `items[0].meta.meta` 구조가 바뀌면 prompt 추출이 깨질 수 있습니다. normalize helper는 가능한 한 missing field에 관대해야 합니다.

### 접근 제한과 성인 설정

토큰이 없거나 browsing level 제한이 있으면 API `items`가 비거나 metadata가 누락될 수 있습니다. 실패 메시지는 "삭제됨"으로 단정하지 말고 hidden, age check, browsing level 가능성을 같이 남겨야 합니다.

### Authorization header 범위

전역 session header 방식은 구현이 쉽지만 redirect나 외부 URL에 token이 붙을 위험을 검토해야 합니다. 초기 구현은 Civitai API와 image.civitai.com 다운로드에만 token을 보내는 방식이 더 안전합니다.

### 리소스 파일 대용량 다운로드

Resources used 다운로드를 자동으로 켜면 사용자가 예상하지 못한 대용량 다운로드가 발생할 수 있습니다. 초기 병합에서는 제외하는 것이 맞습니다.

### source 타입 확장 유혹

`source="civitai_image"`를 새로 만들면 명확해 보이지만 실제 변경 범위가 커집니다. 추가 UI가 없다면 `source="civitai"`와 `model_category="Civitai Image Post"` 조합이 더 실용적입니다.

## 최종 권장안

초기 병합 목표는 다음으로 제한합니다.

- 기존 HugCivi 입력창에 Civitai image URL 입력
- API-only 방식으로 이미지 item 조회
- 이미지 파일 다운로드
- prompt, negative prompt, metadata, resources used를 sidecar JSON으로 저장
- 기존 라이브러리 카드와 media viewer에서 이미지 확인
- 모델/LoRA resource 파일 다운로드는 하지 않음
- 신규 UI는 만들지 않음

이 범위라면 restored 프로젝트의 핵심 가치는 가져오면서도 HugCivi의 기존 구조를 해치지 않고 작게 병합할 수 있습니다.
