# hugcivi

Synology NAS에서 Hugging Face, Civitai, Hitomi, ASMR.one, gallery-dl 지원 사이트, YouTube/yt-dlp, 일반 URL 파일과 ComfyUI 워크플로우를 내려받아 보관하는 웹 앱입니다.

브라우저에서 URL을 붙여넣으면 모델, 갤러리, ASMR.one work 정보를 읽고, LLM, LoRA, Checkpoint, Embedding, Hitomi, ASMR.one, gallery-dl, YouTube 같은 종류에 맞춰 폴더를 자동으로 나눠 저장합니다. ComfyUI 워크플로우 JSON과 워크플로우가 내장된 PNG는 저장하고 뷰어에서 노드 그래프로 확인할 수 있습니다.

## 현재 구조 요약

HugCivi는 단일 FastAPI 컨테이너로 동작합니다.

- `/data`: 장기 보관할 실제 archive 파일
- `/config/jobs.sqlite3`: 작업, 설정, 즐겨찾기, 메모, 라이브러리 인덱스, 내부 작업 artifact 상태
- `/config/downloads`: 폴더 ZIP 준비 파일
- `/config/media-cache`: 비디오 transcode, poster cache, 라이브러리/작업 카드용 작은 썸네일 cache

외부 다운로드는 provider별 제한과 cooldown이 있는 다운로드 큐에서 처리하고, ZIP 생성, 비디오 transcode, poster 생성처럼 NAS CPU/I/O를 많이 쓰는 작업은 별도 internal job 큐에서 처리합니다. 라이브러리는 DB-backed 증분 index를 우선 사용하고, 필요할 때 파일시스템 scan으로 보완합니다.

상단 스토리지 표시는 기본적으로 `/data`가 올라간 볼륨 사용량을 보여주고, `계산` 버튼을 누르면 HugCivi archive가 `/data` 안에서 차지하는 용량을 background scan 후 캐시해서 보여줍니다.

상세 문서:

- [아키텍처](docs/architecture.md)
- [운영 가이드](docs/operations.md)
- [개발 가이드](docs/development.md)
- [문서 인덱스](docs/index.md)
- [기능별 코드 맵](docs/feature-code-map.md)
- [구성 레퍼런스](docs/configuration.md)
- [ByeDPI SOCKS5 프록시 가이드](docs/byedpi-socks-proxy.md)
- [LLM/인수인계 README](README_LLM.md)
- [개발 Skill 세트](SKILL_Dev/SKILL.md)
- [프로젝트 철학](docs/philosophy.md)
- [전송 기능 설계](docs/transfer-design-2026-07-02.md)

## 이런 용도입니다

- NAS에 AI 모델 파일을 모아두고 싶을 때
- Hugging Face 모델, Civitai 모델, 일반 파일 URL을 한 화면에서 받고 싶을 때
- Hitomi, ASMR.one, gallery-dl 지원 사이트, YouTube 영상을 폴더로 보관하고 필요할 때 받고 싶을 때
- ComfyUI용 `loras`, `checkpoints`, `embeddings` 같은 폴더 구조로 정리하고 싶을 때
- ComfyUI 워크플로우 공유 PNG 또는 JSON을 NAS에 저장하고 나중에 다시 보고 싶을 때
- 다운로드 기록, 진행률, 모델 썸네일과 메타데이터를 같이 보고 싶을 때

## 주요 기능

- Hugging Face 모델, 데이터셋, 스페이스 다운로드
- Civitai 모델 페이지 URL, modelVersionId, API 다운로드 URL 다운로드와 대표 예시 이미지/generation metadata 보존
- Hitomi 갤러리 URL 또는 gallery ID 다운로드
- Hitomi artist, language, search, index listing URL discovery와 선택 queue confirm UI
- ASMR.one work URL 파일 다운로드
- gallery-dl 지원 사이트 범용 다운로드
- YouTube/yt-dlp URL 다운로드
- 일반 HTTP/HTTPS 파일 URL 다운로드
- 여러 줄 bulk URL 입력과 부분 실패 보고
- ComfyUI 워크플로우 `.json` URL 다운로드
- ComfyUI 워크플로우가 내장된 `.png` URL 다운로드
- 홈 화면 드래그 앤 드롭으로 ComfyUI 워크플로우 PNG/JSON 저장
- ComfyUI 워크플로우 노드 그래프 뷰어, 모델 목록, 원본 JSON 보기
- Civitai 썸네일, 모델 타입, 베이스 모델, 포맷, 정밀도 표시와 기존 모델 카드 갱신
- Hugging Face 메타데이터 기반 LLM, Embedding, Image 모델 분류
- 폴더 트리에서 저장 위치 선택
- 자동 폴더 분류와 사용자 지정 기본 폴더
- 라이브러리 카드 보기
- 라이브러리 카드는 50개 단위 페이지로 표시하고, 카드 썸네일은 작은 캐시 JPEG를 화면 근처 카드부터 최대 3개씩 요청
- 선택한 라이브러리 폴더의 누락 카드 썸네일은 `썸네일 생성`으로 내부 작업에 예약해 대표 이미지만 순차 생성
- 라이브러리 카드 즐겨찾기, URL 바로가기, A-Z/Z-A/날짜/즐겨찾기 정렬
- 라이브러리 카드 상단 블러 바, 공급자 배지, URL/즐겨찾기 상태 표시
- 폴더 또는 카드 우클릭으로 다운로드, 속성, 이름 변경, 이동, 삭제
- 속성 모달에서 용량, 확장자, 날짜, 원본 URL, 메모 확인과 메모 저장
- 작업 목록에서 다운로드 정지, 재개, 삭제, 저장 폴더 이동, 50개 단위 페이지 전환, 소스별 필터
- 대기열 관리에서 공급자별 동시 다운로드 수, 전체 동시 다운로드 수, 무진행 타임아웃 설정
- 공급자별 cooldown 최소/최대 랜덤 대기 설정
- 폴더 ZIP, 비디오 transcode, poster 생성을 internal job으로 처리
- DB-backed 라이브러리 증분 index
- SQLite WAL, checkpoint, optimize, compact, online backup maintenance API
- 썸네일 블러 토글
- 우측 하단 다운로드 대기열 표시
- 크롬 확장으로 현재 탭 URL을 HugCivi 다운로드 큐에 전송
- HF 토큰, Civitai 토큰, gallery-dl, YouTube/yt-dlp 인증 정보를 웹 UI에서 저장
- 요청 간격, 재시도, 낮은 병렬도 기본값으로 rate limit 위험 완화
- Basic Auth 로그인

## 준비물

- Synology NAS 또는 Docker가 실행되는 서버
- Portainer 또는 Synology Container Manager
- 모델 저장용 NAS 폴더
- 앱 설정 저장용 NAS 폴더

예시 경로:

```text
/volume1/docker/nas-model-archiver/models
/volume1/docker/nas-model-archiver/config
```

권장 Portainer stack은 위 두 폴더를 각각 컨테이너의 `/data`, `/config`에 연결합니다. 기존에 `/volume1/AI_MODELS` 같은 별도 모델 폴더를 쓰고 있다면 `portainer-stack.yml`의 `source` 값만 그 경로로 바꾸면 됩니다.

로컬 Ubuntu나 개발 PC에서 바로 실행할 때는 [docker-compose.yml](docker-compose.yml)을 사용할 수 있지만, 운영 배포 기준은 [portainer-stack.yml](portainer-stack.yml)입니다.

상세 설치 매뉴얼:

- [CasaOS 설치 가이드](docs/install-casaos.md)
- [Ubuntu 설치 가이드](docs/install-ubuntu.md)

## 설치 A: Portainer + Repository

Portainer의 Repository stack은 Git 저장소 내부의 `build:` 컨텍스트를 환경에 따라 빌드하지 못할 수 있습니다. Repository 방식에서는 미리 빌드된 컨테이너 이미지를 `image:`로 참조합니다. 기본 이미지는 GHCR에 배포된 `ghcr.io/mephiblin/hugcivi:latest`입니다. 새 기능을 배포한 뒤에는 Portainer에서 이미지를 다시 pull하거나 stack을 재배포하세요.

1. NAS에 모델 저장 폴더를 만듭니다.

```text
/volume1/docker/nas-model-archiver/models
```

2. NAS에 설정 저장 폴더를 만듭니다.

```text
/volume1/docker/nas-model-archiver/config
```

3. Portainer에 접속합니다.
4. 왼쪽 메뉴에서 `Stacks`를 누릅니다.
5. `Add stack`을 누릅니다.
6. `Repository` 방식을 선택합니다.
7. GitHub 저장소 URL을 입력합니다.
8. Compose path에 아래 값을 입력합니다.

```text
portainer-stack.yml
```

9. Branch는 본인 저장소 브랜치에 맞춥니다.

```text
main
```

10. Environment variables에 최소한 아래 값을 추가합니다.

```text
APP_PASSWORD=원하는_긴_비밀번호
PUID=1026
PGID=100
UMASK=022
```

`HUGCIVI_IMAGE`는 선택사항입니다. 기본값은 `ghcr.io/mephiblin/hugcivi:latest`이며, 특정 릴리스로 고정하려면 `ghcr.io/mephiblin/hugcivi:sha-<커밋>` 형태를 넣을 수 있습니다. 다른 레지스트리 이미지를 직접 쓰려면 `HUGCIVI_IMAGE=이미지주소`를 추가하세요.

Synology에서 `PUID`와 `PGID`는 파일을 소유할 DSM 사용자/그룹 ID로 맞춥니다. 권한이 맞지 않는 기존 폴더를 한 번 정리해야 하면 `HUGCIVI_CHOWN_ON_START=1`을 임시로 켤 수 있습니다.

기본 NAS 경로 또는 포트를 바꾸려면 아래 값을 추가로 넣을 수 있습니다.

```text
HUGCIVI_DATA_DIR=/volume1/AI_MODELS
HUGCIVI_CONFIG_DIR=/volume1/docker/nas-model-archiver/config
HUGCIVI_HTTP_PORT=8088
```

YouTube/yt-dlp 쿠키를 Portainer 환경변수로 미리 넣으려면 아래 값을 추가합니다. 여러 줄 옵션은 웹 UI의 `YouTube/yt-dlp Extra Options`에 넣는 편이 관리하기 쉽습니다.

```text
YT_DLP_COOKIES_FILE=/config/yt-dlp/cookies.txt
YT_DLP_COOKIES_FROM_BROWSER=
YT_DLP_PROXY=
YT_DLP_FORMAT=best[ext=mp4][vcodec^=avc1]/best[ext=mp4]/best
YT_DLP_EXTRA_OPTIONS=
```

11. `Deploy the stack`을 누릅니다.
12. 브라우저에서 접속합니다.

```text
http://NAS_IP:8088
```

로그인 기본 아이디:

```text
admin
```

비밀번호는 `APP_PASSWORD`에 넣은 값입니다.

Portainer가 `pull access denied` 오류를 내면 GitHub Packages의 `ghcr.io/mephiblin/hugcivi` 패키지가 공개 상태인지 확인하세요. 비공개로 유지하려면 Portainer의 `Registries`에 GHCR 인증을 추가해야 합니다.

## 설치 B: 로컬 Docker Compose

개발 PC나 Ubuntu 서버처럼 현재 폴더에서 직접 빌드할 수 있는 환경에서만 사용합니다.

Ubuntu 서버에 Docker Engine부터 설치하는 전체 절차는 [Ubuntu 설치 가이드](docs/install-ubuntu.md)를 참고하세요.

```bash
mkdir -p data config
APP_PASSWORD=원하는_긴_비밀번호 docker compose up -d --build
```

다른 저장 위치를 쓰려면 환경변수로 지정합니다.

```bash
HUGCIVI_DATA_DIR=/srv/hugcivi/models \
HUGCIVI_CONFIG_DIR=/srv/hugcivi/config \
PUID="$(id -u)" PGID="$(id -g)" \
APP_PASSWORD=원하는_긴_비밀번호 \
docker compose up -d --build
```

## 설치 C: CasaOS

CasaOS에서는 Custom App 또는 Compose import로 HugCivi 컨테이너를 등록합니다. 경로, `APP_PASSWORD`, `PUID`/`PGID`, 선택적 `YT_DLP_PROXY` 설정은 [CasaOS 설치 가이드](docs/install-casaos.md)에 정리되어 있습니다.

## Portainer에서 꼭 확인할 값

[portainer-stack.yml](portainer-stack.yml)은 기본적으로 아래 NAS 경로를 사용합니다.

```yaml
volumes:
  - ${HUGCIVI_DATA_DIR:-/volume1/docker/nas-model-archiver/models}:/data
  - ${HUGCIVI_CONFIG_DIR:-/volume1/docker/nas-model-archiver/config}:/config
```

내 NAS 경로가 다르면 Portainer Environment variables에서 `HUGCIVI_DATA_DIR`, `HUGCIVI_CONFIG_DIR` 값을 넣거나 `portainer-stack.yml`에서 기본 `source` 값을 바꾸세요.

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
/data/asmr.one
/data/gallery-dl
```

이 하위 폴더들은 미리 만들 필요는 없습니다. 앱이 필요한 시점에 생성합니다.

## 첫 사용 방법

1. 웹 UI에 로그인합니다.
2. 왼쪽 아래 사용자 버튼을 누릅니다.
3. 필요한 경우 Hugging Face Token, Civitai Token, gallery-dl, YouTube/yt-dlp 인증 정보를 입력합니다.
4. 필요한 경우 기본 폴더 경로와 대기열 설정을 바꿉니다.
5. 상단 입력창에 Hugging Face, Civitai, Hitomi, ASMR.one, gallery-dl, YouTube/yt-dlp 또는 일반 URL을 붙여넣습니다.
6. 다운로드 버튼을 누릅니다.
7. 작업 목록에서 진행률과 로그를 확인합니다.

토큰과 인증 정보는 나중에 입력해도 됩니다. 공개 모델과 공개 갤러리는 인증 없이 받을 수 있는 경우도 있지만, Hugging Face 게이트 모델, Civitai 제한 모델, gallery-dl 사이트별 로그인/쿠키 요구사항, YouTube 연령/멤버십/비공개 권한 확인, 속도 제한 완화에는 인증 정보가 도움이 됩니다.

## 크롬 확장

편의용 크롬 확장은 [chrome-extension](chrome-extension) 폴더에 있습니다. 웹 UI 우측 상단의 `애드온` 버튼으로 zip 파일을 받을 수 있고, 로컬 개발 중에는 이 폴더를 직접 선택해도 됩니다. Chrome의 `chrome://extensions`에서 개발자 모드를 켜고 `압축해제된 확장 프로그램을 로드`로 압축을 푼 `hugcivi-chrome-extension` 폴더를 선택합니다.

확장 설정에는 HugCivi 웹 UI 접속 주소, 같은 ID/PW, 선택 저장 폴더를 입력합니다. 직접 입력한 URL이나 현재 탭 URL을 기존 `/api/jobs/bulk` API로 보내며, 진행도는 `/api/jobs`에서 받아 표시합니다.

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
https://civitai.com/images/135240496
456789
```

숫자만 입력하면 Civitai model version ID로 처리합니다. Civitai 모델 다운로드는 모델 폴더에 대표 예시 이미지 1장과 prompt, negative prompt, seed, steps 같은 generation metadata sidecar를 저장합니다. 모델 페이지 본문, 버전 노트, 트리거 단어, 태그, 파일 정보도 함께 보존합니다. 대표 예시 이미지는 라이브러리 카드 썸네일로 쓰이며, 카드의 미디어 뷰어에서 모델 본문과 generation metadata를 확인할 수 있습니다. 기존 Civitai 모델 카드는 우클릭 메뉴의 `갱신`으로 모델 파일은 유지하면서 누락된 sidecar, 대표 예시 이미지, 변경된 모델 페이지 metadata를 다시 받아올 수 있습니다. Civitai image URL은 이미지 또는 렌더링 페이지 webm 영상을 저장하고, 가능한 경우 연결된 model resource를 child job으로 대기열에 추가합니다.

### Hitomi

```text
https://hitomi.la/galleries/123456.html
https://hitomi.la/reader/123456.html
https://hitomi.la/artist/example.html
https://hitomi.la/search.html?...
hitomi 123456
```

갤러리는 `/data/hitomi/{gallery_id}-{title}` 폴더에 페이지 이미지로 저장됩니다. 저장된 폴더는 라이브러리에서 다운로드하면 ZIP으로 받을 수 있습니다.

artist, tag, language, search, index 같은 listing URL은 갤러리 URL을 discovery한 뒤 설정에 따라 자동으로 child job을 추가하거나 확인 모달에서 선택 후 추가합니다.

Hitomi 다운로드는 기본적으로 `gallery-dl`을 우선 백엔드로 사용합니다. 컨테이너 시작 시 `gallery-dl` 패키지만 최신 안정 버전 범위로 업그레이드할 수 있고, 실패하면 이미지에 포함된 버전으로 계속 실행합니다. `gallery-dl` 실행이 실패했을 때는 내장 Hitomi 다운로더로 한 번 더 시도합니다.

컨테이너 로그와 작업 로그에는 실행된 `gallery-dl` 버전이 남습니다. 재시작 속도나 재현성이 더 중요하면 설정창의 `시작 시 gallery-dl 자동 업데이트`를 끄거나 `GALLERY_DL_AUTO_UPDATE=0`으로 두고 이미지 빌드 시점의 버전을 그대로 사용하세요. UI에서 저장한 값은 `/config/startup.env`에 기록되어 다음 컨테이너 시작부터 적용됩니다.

### ASMR.one

```text
https://asmr.one/work/RJ361902
https://asmr.one/work/361902/DLSITE/RJ361902
```

ASMR.one work URL은 `/data/asmr.one/{source_id} - {title}` 아래에 API track 폴더 구조대로 파일을 저장합니다. 다운로드에는 각 leaf track의 `mediaDownloadUrl`에 `action=download`를 붙인 URL을 사용하고, stream URL은 저장하지 않습니다. ASMR.one API가 이미지, 텍스트, MP3, WAV 같은 구성 파일에 `mediaDownloadUrl`을 제공하면 확장자 필터 없이 함께 저장합니다.

작업 폴더에는 `_asmrone_metadata.json`, `_asmrone_tracks.json`, `_asmrone_manifest.json`, `_archive_metadata.json` sidecar가 함께 저장됩니다.

저장된 MP3, M4A, FLAC, WAV 같은 오디오 파일은 라이브러리 미디어 뷰어에서 바로 재생할 수 있습니다. `.txt`, `.md`, `.markdown` 파일은 같은 뷰어에서 안전한 텍스트 문서로 읽을 수 있으며, Markdown은 HTML로 실행하지 않고 텍스트로 표시합니다.

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

이 앱은 `gallery-dl`을 `--config-ignore`로 실행하므로 표준 gallery-dl config 파일은 자동으로 읽지 않습니다. UI 또는 환경변수의 Username/Password, Cookies File, Browser Cookies, Extra Options만 CLI 옵션으로 전달됩니다. Browser Cookies는 컨테이너에 브라우저 프로필을 별도로 마운트한 고급 구성에서만 동작하므로, 일반적인 Docker 배포에서는 Cookies File 사용을 권장합니다.

2026-06-30 기준 공식 지원 목록은 358개 사이트이며, 인증 칼럼은 `none` 297개, `Supported` 32개, `Cookies` 11개, `OAuth` 10개, `API Key` 5개, `Required` 3개로 분류됩니다.
전체 지원 사이트와 인증 분류별 목록은 [docs/gallery-dl-auth.md](docs/gallery-dl-auth.md)에 스냅샷 reference로 정리되어 있습니다.

예:

```text
extractor.wallhaven.api-key=...
extractor.deviantart.client-id=...
extractor.deviantart.client-secret=...
```

### YouTube / yt-dlp

```text
https://www.youtube.com/watch?v=dQw4w9WgXcQ
https://youtu.be/dQw4w9WgXcQ
yt-dlp https://www.youtube.com/watch?v=dQw4w9WgXcQ
yt-dlp https://www.youtube.com/playlist?list=PL...
```

YouTube URL은 yt-dlp 백엔드로 처리합니다. 공개 영상 또는 본인 계정에 다운로드 권한이 있는 영상만 받으세요. 저작권, 서비스 약관, 지역 제한, 접근 제한을 우회하기 위한 용도로 사용하면 안 됩니다.

YouTube 저장 경로는 `/data/gallery-dl/youtube.com/` 아래에서 플레이리스트와 채널 두 갈래로 나뉩니다. `list=`가 있는 URL은 `playlist/<플레이리스트ID>`에 저장하고, 일반 영상/채널 URL은 yt-dlp 메타데이터의 채널명을 사용해 `channel/<채널명>`에 저장합니다. 채널명을 확인할 수 없으면 기존처럼 URL 기반 폴더명으로 저장합니다.

단일 YouTube 영상 카드 제목은 폴더명이나 `video-...` ID 대신 yt-dlp가 저장한 `.info.json`의 실제 영상 제목을 우선 표시합니다.

기본 YouTube 자막 다운로드는 best-effort입니다. 한국어/영어 수동 자막을 우선 받고, 없으면 영어 자동 자막을 시도하지만 YouTube가 자막 요청에 429를 반환해도 영상 파일이 저장되면 작업은 성공으로 처리합니다. 자막 옵션을 직접 제어하려면 `YouTube/yt-dlp Extra Options`에 `cmdline-args=--write-auto-subs --sub-langs ...`처럼 명시하세요.

왼쪽 사이드바의 `구독` 탭에서 YouTube 채널/플레이리스트를 구독으로 등록할 수 있습니다. 추가할 때 `오늘 영상부터 다운로드`, `최근 N개만`, `첫 영상부터 다운로드` 정책과 확인 간격을 고릅니다. 구독은 일반 작업 목록과 분리된 `subscription_items` 상태로 관리되며, `자동 대기열`이 켜진 항목은 독립 구독 다운로드 worker가 한 번에 하나씩 받습니다. `구독` 탭이 활성화되면 메인 작업 영역도 `구독 작업 목록`으로 바뀌고, 활성/대기/다운로드중/실패/완료/건너뜀 필터로 구독 항목을 따로 볼 수 있습니다. 구독 행을 펼치면 항목별 대기열 추가, 건너뜀, 재시도 조작과 구독별 저장 용량을 확인할 수 있습니다.

로그인이 필요한 영상은 설정창의 YouTube/yt-dlp 인증 입력을 사용합니다.

- `YouTube/yt-dlp Cookies File`: Netscape 형식 `cookies.txt`를 `/config/yt-dlp/cookies.txt`처럼 컨테이너 안 경로로 마운트해 지정합니다.
- `YouTube/yt-dlp Browser Cookies`: 브라우저 프로필을 컨테이너에 별도로 마운트한 고급 구성에서만 사용합니다.
- `YouTube/yt-dlp Format`: 기본값은 `best[ext=mp4][vcodec^=avc1]/best[ext=mp4]/best`입니다.
- `YouTube/yt-dlp Proxy`: `socks5://192.168.200.100:1080` 같은 HTTP/HTTPS/SOCKS 프록시 URL을 입력합니다. yt-dlp 계열 사이트와 메타데이터 probe에만 적용됩니다. 현재 호스트에서 발견된 ByeDPI 컨테이너와 연결하는 방법은 [ByeDPI SOCKS5 프록시 가이드](docs/byedpi-socks-proxy.md)를 참고하세요.
- `YouTube/yt-dlp Extra Options`: `cmdline-args=--max-filesize 500M`, `raw-options.writesubtitles=true`처럼 한 줄에 하나씩 입력합니다.
  저장 경로, 출력 템플릿, 외부 실행, 플러그인 로더, 외부 다운로더, config 파일 위치를 바꾸는 옵션은 차단됩니다.

Docker 이미지에는 YouTube 추출 안정성을 위해 Deno, yt-dlp EJS 구성요소, ffmpeg가 포함됩니다. 앱은 기본적으로 `--js-runtimes deno`를 yt-dlp에 전달합니다. 다른 런타임을 쓰려면 Extra Options에 `cmdline-args=--js-runtimes ...`를 넣어 기본값을 덮어쓰면 됩니다.

Docker/Portainer 배포에서는 Browser Cookies보다 Cookies File 방식이 더 예측 가능합니다.

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

- 새 폴더
- 다운로드
- 속성
- 이름 변경
- 이동
- 삭제

왼쪽 폴더 트리 아래 검색창으로 현재 표시된 폴더를 빠르게 찾을 수 있습니다. 선택된 폴더가 있으면 그 폴더와 하위 폴더 안에서만 검색하고, `/data` 루트를 선택했거나 선택 폴더가 없으면 전체 트리에서 검색합니다. 폴더 트리에서 `새 폴더`를 누르면 부모 위치를 확인한 뒤 폴더 이름만 입력합니다. `이동`은 경로를 직접 입력하지 않고 폴더 트리 팝업에서 대상 폴더를 고른 뒤 확인합니다.

폴더 다운로드는 ZIP 파일로 준비됩니다. 모델 카드가 가리키는 저장 폴더에 파일이 여러 개 있으면 하나의 ZIP으로 내려받습니다.

파일은 즉시 다운로드하고, 폴더 ZIP은 `archive_zip` internal job으로 준비한 뒤 완료되면 내려받습니다. 큰 폴더를 HTTP 요청 안에서 바로 압축하지 않기 때문에 NAS가 여러 요청으로 갑자기 묶이는 위험을 줄입니다.

워크플로우 카드에는 `워크플로 보기`가 추가로 표시됩니다. 속성에서는 용량, 확장자, 날짜, 원본 URL, 메모를 확인할 수 있고 메모를 저장할 수 있습니다.

안전 장치:

- `/data` 루트는 변경하거나 삭제할 수 없습니다.
- `/data` 전체 다운로드는 지원하지 않습니다.
- 실행 중이거나 대기 중인 다운로드가 들어있는 폴더는 이동, 이름 변경, 삭제를 차단합니다.
- 모든 작업은 `/data` 안에서만 허용됩니다.

## 토큰 및 인증 입력

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

### gallery-dl 인증 정보

gallery-dl이 지원하는 사이트 중 일부는 로그인, 쿠키, OAuth, API Key가 필요합니다.

웹 UI의 API 토큰 패널에서 아래 값을 저장할 수 있습니다.

- `gallery-dl Username`
- `gallery-dl Password`
- `gallery-dl Cookies File`
- `gallery-dl Browser Cookies`
- `gallery-dl Extra Options`

`gallery-dl Extra Options`에는 gallery-dl 설정 키를 `extractor.site.key=value` 형식으로 한 줄씩 입력합니다. 공식 지원 사이트별 인증 분류는 [docs/gallery-dl-auth.md](docs/gallery-dl-auth.md)를 참고하세요.

### YouTube/yt-dlp 인증 정보

웹 UI의 API 토큰 패널에서 아래 값을 저장할 수 있습니다.

- `YouTube/yt-dlp Cookies File`
- `YouTube/yt-dlp Browser Cookies`
- `YouTube/yt-dlp Proxy`
- `YouTube/yt-dlp Format`
- `YouTube/yt-dlp Extra Options`

환경변수 이름:

```text
YT_DLP_COOKIES_FILE
YT_DLP_COOKIES_FROM_BROWSER
YT_DLP_PROXY
YT_DLP_FORMAT
YT_DLP_EXTRA_OPTIONS
```

`Cookies File`에는 컨테이너 안에서 읽을 수 있는 Netscape 형식 cookies.txt 경로를 넣습니다. `Browser Cookies`는 브라우저 프로필을 컨테이너에 마운트한 경우에만 사용하세요. `Proxy`는 yt-dlp에 `--proxy`로 전달되는 HTTP/HTTPS/SOCKS URL입니다. 현재 호스트의 ByeDPI 예시는 `socks5://192.168.200.100:1080`입니다. `Format`은 `yt-dlp`의 format selector이며 기본값은 `best[ext=mp4][vcodec^=avc1]/best[ext=mp4]/best`입니다. `Extra Options`에는 `cmdline-args=...`, `raw-options.*=...` 또는 `extractor.ytdl.*=...` 형식의 옵션을 한 줄에 하나씩 넣습니다. 저장 경로, 출력 템플릿, 외부 실행, 플러그인 로더, 외부 다운로더, config 파일 위치를 바꾸는 옵션은 앱이 차단합니다. 프록시는 `Extra Options` 대신 `YT_DLP_PROXY`를 사용하세요.

토큰과 인증 정보는 웹 UI에서 저장할 수 있습니다. UI로 저장한 값은 `/config/jobs.sqlite3`에 저장되며, 설정창을 다시 열면 평문으로 표시됩니다. 저장한 값은 새 다운로드 작업부터 재시작 없이 적용됩니다. 값을 비워 저장하면 UI 저장값은 삭제되며, 같은 이름의 환경변수가 있으면 환경변수 값으로 fallback됩니다.

## 다운로드 안전 설정

기본값은 빠른 다운로드보다 안정성을 우선합니다.

```text
MAX_CONCURRENT_DOWNLOADS=3
QUEUE_PER_PROVIDER_LIMIT=1
QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS=2
QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS=2
DOWNLOAD_STALL_TIMEOUT_SECONDS=600
INTERNAL_JOB_MAX_CONCURRENT=2
DOWNLOAD_REQUEST_MIN_INTERVAL_SECONDS=1.5
DOWNLOAD_HTTP_MAX_RETRIES=3
DOWNLOAD_RETRY_BACKOFF_SECONDS=5
DOWNLOAD_MAX_RETRY_SLEEP_SECONDS=300
DOWNLOAD_ARCHIVE_MAX_CONCURRENT=1
DOWNLOAD_ARCHIVE_TTL_SECONDS=86400
DOWNLOAD_ARCHIVE_MAX_FILES=50000
STORAGE_USAGE_SCAN_BATCH_SIZE=1000
STORAGE_USAGE_SCAN_SLEEP_SECONDS=0.02
MEDIA_TRANSCODE_MAX_CONCURRENT=1
MEDIA_TRANSCODE_TIMEOUT_SECONDS=1800
MEDIA_CACHE_TTL_SECONDS=2592000
MEDIA_CACHE_MAX_BYTES=0
MEDIA_THUMBNAIL_BACKFILL_WORKERS=3
MEDIA_THUMBNAIL_BACKFILL_MAX_ITEMS=5000
HITOMI_BACKEND=auto
HITOMI_LISTING_QUEUE_MODE=auto
GALLERY_DL_AUTO_UPDATE=1
GALLERY_DL_UPDATE_SPEC=gallery-dl<2.0
HUGCIVI_STARTUP_CONFIG_FILE=/config/startup.env
GALLERY_DL_SLEEP_REQUEST_SECONDS=1.5
GALLERY_DL_USERNAME=
GALLERY_DL_PASSWORD=
GALLERY_DL_COOKIES_FILE=
GALLERY_DL_COOKIES_FROM_BROWSER=
GALLERY_DL_EXTRA_OPTIONS=
ASMRONE_API_BASE=https://api.asmr.one/api
YT_DLP_COOKIES_FILE=
YT_DLP_COOKIES_FROM_BROWSER=
YT_DLP_PROXY=
YT_DLP_FORMAT=best[ext=mp4][vcodec^=avc1]/best[ext=mp4]/best
YT_DLP_EXTRA_OPTIONS=
```

너무 많은 요청으로 차단될 가능성을 줄이기 위해 기본값을 보수적으로 잡았습니다.

다운로드 큐 설정은 외부 provider 다운로드에 적용됩니다. `QUEUE_PER_PROVIDER_LIMIT`는 같은 공급자 작업 수를 제한하고, Hugging Face snapshot 내부 병렬은 1로 고정해 공급자 제한값이 곱해지지 않게 합니다. `DOWNLOAD_STALL_TIMEOUT_SECONDS`는 무진행 watchdog과 Hugging Face Hub 응답 대기에 함께 적용되며, `0`은 사용 안 함입니다. ZIP, transcode, poster, 썸네일 백필 같은 서버-local 작업은 `INTERNAL_JOB_MAX_CONCURRENT`와 작업별 semaphore 설정을 사용합니다. Synology NAS가 약하거나 동영상 transcode가 무겁다면 `INTERNAL_JOB_MAX_CONCURRENT=1`을 권장합니다.

운영 기준인 `portainer-stack.yml`과 로컬 개발용 `docker-compose.yml`은 일부 기본값이 다릅니다. 특히 Portainer stack의 `DOWNLOAD_STALL_TIMEOUT_SECONDS` 기본값은 현재 `0`이며, 이는 watchdog timeout 비활성화를 의미합니다. 운영에서 무진행 job을 자동으로 끊고 싶으면 Portainer 환경변수나 UI 설정에서 `600` 같은 값을 명시하세요.

## 보안 주의

- 이 앱을 인터넷에 직접 공개하지 않는 것을 권장합니다.
- `APP_PASSWORD`는 반드시 긴 비밀번호로 바꾸세요.
- `/config` 폴더에는 작업 DB와 UI 저장 토큰이 들어갈 수 있습니다.
- `/config/jobs.sqlite3` 백업은 credential 백업입니다. 백업 파일도 토큰/비밀번호/cookie path를 포함할 수 있습니다.
- 설정창은 토큰, 비밀번호, cookie path, 프록시 URL을 평문으로 표시합니다. 신뢰하는 LAN/VPN 또는 reverse proxy 뒤에서만 사용하세요.
- `/config` 폴더 권한을 NAS에서 제한하세요.
- 모델 파일은 각 사이트의 라이선스와 이용약관을 확인한 뒤 보관하세요.
- YouTube/yt-dlp는 공개 영상 또는 본인에게 명시적으로 다운로드 권한이 있는 영상에만 사용하세요.

## 미리보기 파일

[frontend-preview.html](frontend-preview.html)은 디자인 확인용 정적 파일입니다.

실제 Docker 컨테이너에는 포함되지 않습니다. 삭제하지 않아도 설치에는 영향이 없습니다.

## 문서

- [아키텍처](docs/architecture.md)
- [운영 가이드](docs/operations.md)
- [개발 가이드](docs/development.md)
- [문서 인덱스](docs/index.md)
- [기능별 코드 맵](docs/feature-code-map.md)
- [구성 레퍼런스](docs/configuration.md)
- [LLM/인수인계 README](README_LLM.md)
- [개발 Skill 세트](SKILL_Dev/SKILL.md)
- [프로젝트 철학](docs/philosophy.md)
- [전송 기능 설계](docs/transfer-design-2026-07-02.md)
- [gallery-dl 인증 분류](docs/gallery-dl-auth.md)
- [2026-06-30 코드 검토 결과](docs/code-review-findings-2026-06-30.md)

## 자주 막히는 부분

### 접속하면 비밀번호 오류가 납니다

Portainer Stack의 Environment variables에서 `APP_PASSWORD`를 설정했는지 확인하세요.

### 앱이 503을 보여줍니다

`APP_PASSWORD`가 기본 예시값이면 앱이 실행을 막습니다. 긴 비밀번호로 바꾸세요.

### 다운로드가 느립니다

기본값은 차단 방지를 위해 느리게 잡혀 있습니다. 먼저 토큰을 입력하고, 그래도 부족하면 설정창의 공급자당/전체 동시 다운로드 수를 조금씩 올리세요.

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
APP_PASSWORD=dev-password-that-is-long \
DATA_ROOT="$PWD/data" \
DB_PATH="$PWD/config/jobs.sqlite3" \
DOWNLOAD_ARCHIVE_DIR="$PWD/config/downloads" \
MEDIA_CACHE_DIR="$PWD/config/media-cache" \
uvicorn app.main:app --host 0.0.0.0 --port 8088 --reload
```

Windows PowerShell에서는 가상환경 활성화 명령이 다릅니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:APP_PASSWORD="dev-password-that-is-long"
$env:DATA_ROOT="$PWD\data"
$env:DB_PATH="$PWD\config\jobs.sqlite3"
$env:DOWNLOAD_ARCHIVE_DIR="$PWD\config\downloads"
$env:MEDIA_CACHE_DIR="$PWD\config\media-cache"
uvicorn app.main:app --host 0.0.0.0 --port 8088 --reload
```

테스트까지 실행할 때는 개발 의존성을 설치합니다.

```bash
pip install -r requirements-dev.txt
python -m pytest -q -p no:cacheprovider
```

## 패치내역

자세한 변경 내용은 [PATCH_NOTES.md](PATCH_NOTES.md)에 정리되어 있습니다.

### 2026-07-06

- Civitai image page가 public API에서 비어 있어도 렌더링 페이지 metadata를 fallback으로 읽고, webm 영상 asset은 원본 `.webm` 파일로 저장할 수 있게 했습니다.
- 라이브러리 날짜 정렬을 `최신순`/`오래된순`으로 분리하고 legacy `sort=date` 요청은 최신순 alias로 유지했습니다.
- 선택 폴더 live pagination의 카드 반복과 페이지/정렬 전환 중 이전 카드 잔상을 줄이고, 긴 일본어/한자 카드 제목을 overlay 안에서 2줄로 제한했습니다.
- 선택 폴더 live scan의 기본 안정 window를 작은 폴더 기준 3페이지분으로 낮춰, 100개 안팎 카드 폴더가 1000개 후보를 스캔하며 느려지는 문제를 완화했습니다.
- 저장 폴더 tree는 root direct child만 담는 `/api/folders` 초기 tree와 `/api/folders/children`의 펼친 폴더 direct child lazy loading으로 나누어 초기 로드 비용을 줄였습니다.
- 폴더 검색과 이동 대상 선택은 현재 로드된 tree와 lazy 확장분을 대상으로 동작하며, archive-scale 전체 검색은 server-side folder search와 optional folder index로 확장하는 방향을 남겼습니다.
- 배포 이미지는 로컬 빌드/푸시 흐름에서 `ghcr.io/mephiblin/hugcivi:sha-<commit>`와 `ghcr.io/mephiblin/hugcivi:latest` 태그로 갱신합니다.

### 2026-07-05

- `.txt`, `.md`, `.markdown` 파일을 라이브러리 카드와 미디어 뷰어에서 안전한 텍스트 문서로 볼 수 있게 했습니다.
- 라이브러리 카드를 50개 단위 페이지로 넘기고, 카드 썸네일을 `/config/media-cache/thumbnails` 캐시와 화면 근처 최대 3개 동시 요청 큐로 처리해 큰 폴더 첫 로드 부담을 줄였습니다.
- 선택한 라이브러리 폴더의 누락 카드 대표 썸네일을 `media_thumbnail_backfill` 내부 작업으로 예약하는 버튼을 추가했습니다.
- 작업 목록을 50개 단위 숫자 페이지네이션으로 표시하고, `ALL` 및 Civitai/Hitomi/ASMR.one 같은 현재 작업 소스별 필터 버튼을 추가했습니다.
- 저장 폴더 전용 새로고침, 우상단 버튼 순서 정리, 불필요한 전체 새로고침 아이콘 제거를 반영했습니다.

### 2026-07-02

- FastAPI lifespan, external download scheduler, internal ZIP/media job scheduler를 분리해 구조를 안정화했습니다.
- 폴더 ZIP, 비디오 transcode, poster 생성을 internal job으로 처리하고 `/api/jobs` 호환 응답을 유지했습니다.
- DB-backed 라이브러리 인덱스, storage usage 계산, DB maintenance/backup API를 추가했습니다.
- Hitomi listing confirm UI, Civitai image resource health, media viewer polling 흐름을 반영했습니다.
- 크롬 확장과 웹 UI `애드온` 다운로드 버튼을 추가했습니다.
- 개발 인수인계용 문서 인덱스, 기능별 코드 맵, 구성 레퍼런스를 추가했습니다.

### 2026-06-30

- Hitomi 및 gallery-dl 다운로드 추가
  - Hitomi 갤러리 URL, reader URL, `hitomi {gallery_id}` 입력 지원
  - gallery-dl 범용 다운로드를 `gallery-dl` 또는 `gdl` 접두어로 지원
  - `/data/hitomi`, `/data/gallery-dl` 기본 저장 경로 추가
  - 컨테이너 시작 시 gallery-dl 최신 안정 버전 범위 자동 업데이트 지원
  - 공식 gallery-dl 지원 사이트 358개와 인증 분류 문서화
- 인증 설정 확장
  - HF Token, Civitai Token 외에 gallery-dl Username/Password, Cookies File, Browser Cookies, Extra Options 저장 지원
  - 사이트별 쿠키, OAuth, API Key 요구사항을 앱 설정값과 연결
- 대기열 관리 추가
  - 공급자별 동시 다운로드 수 제한
  - 전체 동시 다운로드 수 제한
  - 다운로드 무진행 타임아웃 설정
  - Hugging Face, Civitai, Hitomi, gallery-dl, 일반 URL을 공급자별로 분리된 대기열에서 처리
- 작업 목록 제어 개선
  - `취소` 버튼 제거
  - `정지` 후 같은 버튼이 `재개`로 바뀌도록 UI와 상태 처리 정리
  - 삭제 시 스트리밍 `.part` 파일 정리
- 라이브러리 카드 디자인 개선
  - 카드 상단을 블러 오버레이 바로 변경
  - 공급자 배지, URL 바로가기 globe 아이콘, 즐겨찾기 비활성/활성 상태 표시 개선
  - A-Z, Z-A, 최신순, 오래된순, 즐겨찾기 정렬과 썸네일 블러 토글 유지
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
- 배포 설정 최신화
  - 다운로드 워커 기본값 `MAX_CONCURRENT_DOWNLOADS=3`
  - Portainer 권장 Synology 기본 폴더를 `/volume1/docker/nas-model-archiver/models`, `/volume1/docker/nas-model-archiver/config`로 정리
  - Portainer stack은 미리 빌드해 push한 `HUGCIVI_IMAGE`를 참조하도록 정리
