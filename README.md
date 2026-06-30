# NAS Model Archiver

Synology NAS에서 Hugging Face, Civitai, 일반 URL 모델 파일과 ComfyUI 워크플로우를 내려받아 보관하는 웹 앱입니다.

브라우저에서 URL을 붙여넣으면 모델 정보를 읽고, LLM, LoRA, Checkpoint, Embedding 같은 종류에 맞춰 폴더를 자동으로 나눠 저장합니다. ComfyUI 워크플로우 JSON과 워크플로우가 내장된 PNG는 저장하고 뷰어에서 노드 그래프로 확인할 수 있습니다.

## 이런 용도입니다

- NAS에 AI 모델 파일을 모아두고 싶을 때
- Hugging Face 모델, Civitai 모델, 일반 파일 URL을 한 화면에서 받고 싶을 때
- ComfyUI용 `loras`, `checkpoints`, `embeddings` 같은 폴더 구조로 정리하고 싶을 때
- ComfyUI 워크플로우 공유 PNG 또는 JSON을 NAS에 저장하고 나중에 다시 보고 싶을 때
- 다운로드 기록, 진행률, 모델 썸네일과 메타데이터를 같이 보고 싶을 때

## 주요 기능

- Hugging Face 모델, 데이터셋, 스페이스 다운로드
- Civitai 모델 페이지 URL, modelVersionId, API 다운로드 URL 다운로드
- Hitomi 갤러리 URL 또는 gallery ID 다운로드
- 일반 HTTP/HTTPS 파일 URL 다운로드
- ComfyUI 워크플로우 `.json` URL 다운로드
- ComfyUI 워크플로우가 내장된 `.png` URL 다운로드
- 홈 화면 드래그 앤 드롭으로 ComfyUI 워크플로우 PNG/JSON 저장
- ComfyUI 워크플로우 노드 그래프 뷰어, 모델 목록, 원본 JSON 보기
- Civitai 썸네일, 모델 타입, 베이스 모델, 포맷, 정밀도 표시
- Hugging Face 메타데이터 기반 LLM, Embedding, Image 모델 분류
- 폴더 트리에서 저장 위치 선택
- 자동 폴더 분류와 사용자 지정 기본 폴더
- 라이브러리 카드 보기
- 라이브러리 카드 즐겨찾기, URL 바로가기, A-Z/Z-A/날짜/즐겨찾기 정렬
- 폴더 또는 카드 우클릭으로 다운로드, 속성, 이름 변경, 이동, 삭제
- 속성 모달에서 용량, 확장자, 날짜, 원본 URL, 메모 확인과 메모 저장
- 작업 목록에서 다운로드 정지, 재개, 삭제
- 대기열 관리에서 공급자별 동시 다운로드 수, 전체 동시 다운로드 수, 무진행 타임아웃 설정
- 썸네일 블러 토글
- 우측 하단 다운로드 대기열 표시
- HF 토큰, Civitai 토큰을 웹 UI에서 저장
- 요청 간격, 재시도, 낮은 병렬도 기본값으로 rate limit 위험 완화
- Basic Auth 로그인

## 준비물

- Synology NAS 또는 Docker가 실행되는 서버
- Portainer 또는 Synology Container Manager
- GitHub 저장소 URL
- 모델 저장용 NAS 폴더
- 앱 설정 저장용 NAS 폴더

예시 경로:

```text
/volume1/docker/nas-model-archiver/models
/volume1/docker/nas-model-archiver/config
```

권장 Portainer stack은 위 두 폴더를 각각 컨테이너의 `/data`, `/config`에 연결합니다. 기존에 `/volume1/AI_MODELS` 같은 별도 모델 폴더를 쓰고 있다면 `portainer-stack.yml`의 `source` 값만 그 경로로 바꾸면 됩니다.

## 가장 쉬운 설치: Portainer + GitHub

1. 이 프로젝트를 GitHub 저장소에 올립니다.
2. NAS에 모델 저장 폴더를 만듭니다.

```text
/volume1/docker/nas-model-archiver/models
```

3. NAS에 설정 저장 폴더를 만듭니다.

```text
/volume1/docker/nas-model-archiver/config
```

4. Portainer에 접속합니다.
5. 왼쪽 메뉴에서 `Stacks`를 누릅니다.
6. `Add stack`을 누릅니다.
7. `Repository` 방식을 선택합니다.
8. GitHub 저장소 URL을 입력합니다.
9. Compose path에 아래 값을 입력합니다.

```text
portainer-stack.yml
```

10. Branch는 본인 저장소 브랜치에 맞춥니다.

```text
main
```

또는 현재 로컬 기본 브랜치를 그대로 쓰면:

```text
master
```

11. Environment variables에 최소한 아래 값을 추가합니다.

```text
APP_PASSWORD=원하는_긴_비밀번호
```

12. `Deploy the stack`을 누릅니다.
13. 브라우저에서 접속합니다.

```text
http://NAS_IP:8088
```

로그인 기본 아이디:

```text
admin
```

비밀번호는 `APP_PASSWORD`에 넣은 값입니다.

Portainer가 `pull access denied for nas-model-archiver` 오류를 내면 스택이 원격 이미지를 받으려는 상태입니다.
이 저장소의 [portainer-stack.yml](portainer-stack.yml)은 Git 저장소에서 Dockerfile을 직접 빌드하도록 `build`만 사용합니다.

## Portainer에서 꼭 확인할 값

[portainer-stack.yml](portainer-stack.yml)은 기본적으로 아래 NAS 경로를 사용합니다.

```yaml
volumes:
  - /volume1/docker/nas-model-archiver/models:/data
  - /volume1/docker/nas-model-archiver/config:/config
```

내 NAS 경로가 다르면 `portainer-stack.yml`에서 `source` 값을 바꾸세요.

예:

```yaml
source: /volume1/my-models
target: /data
```

## Synology에서 필요한 폴더

Portainer 권장 설정 기준으로 NAS에 직접 만들어야 하는 폴더는 두 개입니다.

```text
/volume1/docker/nas-model-archiver/models
/volume1/docker/nas-model-archiver/config
```

역할:

- `/volume1/docker/nas-model-archiver/models`: 모델 파일, ComfyUI 워크플로우, 일반 다운로드 파일이 저장됩니다. 컨테이너 안에서는 `/data`입니다.
- `/volume1/docker/nas-model-archiver/config`: 작업 DB, UI에서 저장한 HF/Civitai 토큰, 설정값, 메모가 저장됩니다. 컨테이너 안에서는 `/config`입니다.

Synology SSH에서 만들 때 예:

```bash
mkdir -p /volume1/docker/nas-model-archiver/models
mkdir -p /volume1/docker/nas-model-archiver/config
```

앱이 `/data` 아래에 자동으로 만들거나 사용하는 주요 하위 폴더:

```text
/data/huggingface/llm
/data/stable-diffusion/checkpoints
/data/stable-diffusion/loras
/data/stable-diffusion/diffusion_models
/data/stable-diffusion/embeddings
/data/stable-diffusion/vae
/data/stable-diffusion/controlnet
/data/stable-diffusion/upscalers
/data/comfyui/workflows
/data/generic
/data/hitomi
```

이 하위 폴더들은 미리 만들 필요는 없습니다. 앱이 필요한 시점에 생성합니다.

## 첫 사용 방법

1. 웹 UI에 로그인합니다.
2. 왼쪽 아래 사용자 버튼을 누릅니다.
3. Hugging Face Token, Civitai Token을 입력합니다.
4. 필요한 경우 기본 폴더 경로를 바꿉니다.
5. 상단 입력창에 Hugging Face 또는 Civitai URL을 붙여넣습니다.
6. 다운로드 버튼을 누릅니다.
7. 작업 목록에서 진행률과 로그를 확인합니다.

토큰은 나중에 입력해도 됩니다. 공개 모델은 토큰 없이 받을 수 있는 경우도 있지만, Hugging Face 게이트 모델이나 속도 제한 완화에는 토큰이 도움이 됩니다.

## 다운로드 입력 예시

### Hugging Face

```text
https://huggingface.co/openai-community/gpt2
https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0
https://huggingface.co/openai-community/gpt2/resolve/main/config.json
hf download openai-community/gpt2 --include "*.json"
hf download hf://datasets/bigcode/the-stack@v1.1
```

### Civitai

```text
https://civitai.com/models/123456/model-name?modelVersionId=456789
https://civitai.com/api/download/models/456789
456789
```

숫자만 입력하면 Civitai model version ID로 처리합니다.

### Hitomi

```text
https://hitomi.la/galleries/123456.html
https://hitomi.la/reader/123456.html
hitomi 123456
```

갤러리는 `/data/hitomi/{gallery_id}-{title}` 폴더에 페이지 이미지로 저장됩니다. 저장된 폴더는 라이브러리에서 다운로드하면 ZIP으로 받을 수 있습니다.

Hitomi 다운로드는 기본적으로 `gallery-dl`을 우선 백엔드로 사용합니다. 컨테이너 시작 시 `gallery-dl` 패키지만 최신 안정 버전 범위로 업그레이드하고, 실패하면 이미지에 포함된 버전으로 계속 실행합니다. `gallery-dl` 실행이 실패했을 때는 내장 Hitomi 다운로더로 한 번 더 시도합니다.

### gallery-dl 범용 다운로드

```text
gallery-dl https://example.com/gallery
gdl https://example.com/gallery
```

`gallery-dl`이 지원하는 사이트를 명시적으로 다운로드할 때 사용합니다. 일반 HTTP 파일 URL과 충돌하지 않도록 `gallery-dl` 또는 `gdl` 접두어를 붙입니다. 기본 저장 경로는 `/data/gallery-dl/{host}/{name}`입니다.

공식 지원 목록의 인증 분류는 앱 설정의 gallery-dl 입력으로 처리합니다.

- `Cookies`: `gallery-dl Cookies File` 또는 `gallery-dl Browser Cookies`
- `OAuth`, `API Key`: `gallery-dl Extra Options`에 `extractor.site.key=value` 형식으로 입력
- `Supported`, `Required`: 사이트에 따라 Username/Password, Cookies File, Extra Options 중 필요한 값을 입력

2026-06-30 기준 공식 지원 목록은 358개 사이트이며, 인증 칼럼은 `none` 297개, `Supported` 32개, `Cookies` 11개, `OAuth` 10개, `API Key` 5개, `Required` 3개로 분류됩니다.
전체 지원 사이트와 인증 분류별 목록은 [docs/gallery-dl-auth.md](docs/gallery-dl-auth.md)에 정리되어 있습니다.

예:

```text
extractor.wallhaven.api-key=...
extractor.deviantart.client-id=...
extractor.deviantart.client-secret=...
```

### ComfyUI 워크플로우

ComfyUI 워크플로우 JSON 또는 워크플로우가 내장된 PNG를 저장할 수 있습니다.

```text
workflow https://example.com/workflow.json
workflow https://example.com/share.png
comfyui https://example.com/share.png
```

일반 PNG와 워크플로우 PNG를 오인하지 않도록, URL 입력에서는 `workflow` 또는 `comfyui` 접두어를 붙이는 방식을 권장합니다.

로컬 파일은 홈 화면 입력 영역에 드래그 앤 드롭하면 됩니다.

지원 파일:

```text
.json
.png
```

저장 기본 경로:

```text
/data/comfyui/workflows/파일이름
```

## 폴더 자동 분류

저장 폴더를 직접 선택하지 않으면 모델 정보를 보고 자동으로 경로를 정합니다.

기본 예시:

```text
/data/huggingface/llm/openai-community__gpt2
/data/stable-diffusion/checkpoints/sdxl-1.0/...
/data/stable-diffusion/loras/sd-1.5/...
/data/stable-diffusion/embeddings/...
/data/stable-diffusion/diffusion_models/...
/data/comfyui/workflows/...
```

왼쪽 폴더 트리에서 폴더를 클릭한 뒤 다운로드하면 자동 분류보다 선택한 폴더가 우선입니다.

## 기본 폴더 설정

왼쪽 아래 사용자 버튼을 누르면 폴더 설정이 나옵니다.

기본값:

```text
LLM: huggingface/llm
LoRA: stable-diffusion/loras
Checkpoint: stable-diffusion/checkpoints
Diffusion Model: stable-diffusion/diffusion_models
Embedding: stable-diffusion/embeddings
VAE: stable-diffusion/vae
ControlNet: stable-diffusion/controlnet
Upscaler: stable-diffusion/upscalers
```

모든 경로는 `/data` 기준 상대경로입니다.

## 폴더 수동 정리

폴더 트리 또는 라이브러리 카드에서 마우스 오른쪽 버튼을 누르면 메뉴가 나옵니다.

- 다운로드
- 속성
- 이름 변경
- 이동
- 삭제

폴더 다운로드는 ZIP 파일로 준비됩니다. 모델 카드가 가리키는 저장 폴더에 파일이 여러 개 있으면 하나의 ZIP으로 내려받습니다.

워크플로우 카드에는 `워크플로 보기`가 추가로 표시됩니다. 속성에서는 용량, 확장자, 날짜, 원본 URL, 메모를 확인할 수 있고 메모를 저장할 수 있습니다.

안전 장치:

- `/data` 루트는 변경하거나 삭제할 수 없습니다.
- `/data` 전체 다운로드는 지원하지 않습니다.
- 실행 중이거나 대기 중인 다운로드가 들어있는 폴더는 이동, 이름 변경, 삭제를 차단합니다.
- 모든 작업은 `/data` 안에서만 허용됩니다.

## 토큰 입력

### Hugging Face Token

Hugging Face에서 발급한 토큰입니다.

필요한 경우:

- 로그인 필요한 모델
- 게이트 모델
- 비공개 모델
- rate limit 완화

환경변수 이름:

```text
HF_TOKEN
```

### Civitai Token

Civitai에서 발급한 API 토큰입니다.

환경변수 이름:

```text
CIVITAI_TOKEN
```

토큰은 웹 UI에서 저장할 수 있습니다. UI로 저장한 값은 `/config/jobs.sqlite3`에 저장됩니다.

## 다운로드 안전 설정

기본값은 빠른 다운로드보다 안정성을 우선합니다.

```text
MAX_CONCURRENT_DOWNLOADS=3
QUEUE_PER_PROVIDER_LIMIT=1
DOWNLOAD_STALL_TIMEOUT_SECONDS=0
HF_SNAPSHOT_MAX_WORKERS=2
DOWNLOAD_REQUEST_MIN_INTERVAL_SECONDS=1.5
DOWNLOAD_HTTP_MAX_RETRIES=3
DOWNLOAD_RETRY_BACKOFF_SECONDS=5
DOWNLOAD_MAX_RETRY_SLEEP_SECONDS=300
HITOMI_BACKEND=auto
GALLERY_DL_AUTO_UPDATE=1
GALLERY_DL_UPDATE_SPEC=gallery-dl<2.0
GALLERY_DL_SLEEP_REQUEST_SECONDS=1.5
GALLERY_DL_USERNAME=
GALLERY_DL_PASSWORD=
GALLERY_DL_COOKIES_FILE=
GALLERY_DL_COOKIES_FROM_BROWSER=
GALLERY_DL_EXTRA_OPTIONS=
```

너무 많은 요청으로 차단될 가능성을 줄이기 위해 기본값을 보수적으로 잡았습니다.

## 보안 주의

- 이 앱을 인터넷에 직접 공개하지 않는 것을 권장합니다.
- `APP_PASSWORD`는 반드시 긴 비밀번호로 바꾸세요.
- `/config` 폴더에는 작업 DB와 UI 저장 토큰이 들어갈 수 있습니다.
- `/config` 폴더 권한을 NAS에서 제한하세요.
- 모델 파일은 각 사이트의 라이선스와 이용약관을 확인한 뒤 보관하세요.

## 미리보기 파일

[frontend-preview.html](frontend-preview.html)은 디자인 확인용 정적 파일입니다.

실제 Docker 컨테이너에는 포함되지 않습니다. 삭제하지 않아도 설치에는 영향이 없습니다.

## 자주 막히는 부분

### 접속하면 비밀번호 오류가 납니다

Portainer Stack의 Environment variables에서 `APP_PASSWORD`를 설정했는지 확인하세요.

### 앱이 503을 보여줍니다

`APP_PASSWORD`가 기본 예시값이면 앱이 실행을 막습니다. 긴 비밀번호로 바꾸세요.

### 다운로드가 느립니다

기본값은 차단 방지를 위해 느리게 잡혀 있습니다. 먼저 토큰을 입력하고, 그래도 부족하면 `HF_SNAPSHOT_MAX_WORKERS` 또는 `MAX_CONCURRENT_DOWNLOADS`를 조금씩 올리세요.

### 폴더 삭제가 안 됩니다

실행 중이거나 대기 중인 다운로드가 있는 폴더는 삭제할 수 없습니다. 작업이 끝난 뒤 다시 시도하세요.

### Hugging Face 모델 분류가 완벽하지 않습니다

Hugging Face는 Civitai처럼 모델 타입을 항상 명확히 주지 않습니다. `pipeline_tag`, 태그, 파일명, 모델 카드 정보를 조합해 분류합니다.

## 로컬 개발 실행

개발용으로 PC에서 직접 실행할 때 사용합니다.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8088 --reload
```

Windows PowerShell에서는 가상환경 활성화 명령이 다릅니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8088 --reload
```

## 패치내역

### 2026-06-30

- ComfyUI 워크플로우 기능 추가
  - `.json` 워크플로우 URL 저장 지원
  - 워크플로우 metadata가 내장된 `.png` 저장 지원
  - 홈 화면 드래그 앤 드롭으로 ComfyUI PNG/JSON 저장
  - `/data/comfyui/workflows` 기본 저장 경로 추가
  - 워크플로우 노드 그래프 뷰어, 모델 목록, 원본 JSON 보기 추가
  - 라이브러리 카드와 우클릭 메뉴에서 `워크플로 보기` 지원
- 속성 메모 기능 추가
  - 카드/폴더 우클릭 `속성`에서 메모 작성과 저장 지원
  - 이름 변경/이동 시 메모 경로 자동 갱신
  - 삭제 시 관련 메모 자동 삭제
- 작업 목록 제어 기능 추가
  - 다운로드 정지, 재개, 삭제 버튼 추가
  - 스트리밍 다운로드는 삭제 시 `.part` 파일 정리
- 라이브러리 카드 기능 개선
  - 즐겨찾기 버튼 추가
  - URL 바로가기 버튼 추가
  - A-Z, Z-A, 날짜순, 즐겨찾기 정렬 추가
  - 썸네일 블러 토글 추가
- 배포 설정 최신화
  - 다운로드 워커 기본값 `MAX_CONCURRENT_DOWNLOADS=3`
  - Portainer 권장 Synology 기본 폴더를 `/volume1/docker/nas-model-archiver/models`, `/volume1/docker/nas-model-archiver/config`로 정리
  - Portainer stack은 Git 저장소에서 Dockerfile을 직접 빌드하도록 유지
