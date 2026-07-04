# 패치내역

## 2026-07-04

### Civitai

- Civitai 모델 다운로드가 모델 파일과 `_civitai_metadata.json`만 저장하던 흐름에 `_civitai_generation_metadata.json` sidecar를 추가했습니다.
- 모델 버전의 예시 이미지 metadata를 Civitai images API에서 best-effort로 가져와 prompt, negative prompt, seed, steps, sampler, CFG scale 같은 generation 정보를 보존합니다.
- 모델 폴더에 대표 예시 이미지 1장을 저장해 라이브러리 카드 썸네일과 미디어 뷰어 generation panel에서 사용합니다.
- 체크포인트, LoRA, VAE 같은 Civitai 모델의 모델 페이지 본문, 버전 노트, 트리거 단어, 태그, 파일 정보를 저장하고 미디어 뷰어에서 함께 표시합니다.
- Civitai viewer의 모델 상세 영역에 type, creator, status, published date, base model/type, model/version stats, 파일 hash/required/scan 정보를 표시하도록 보강했습니다.
- Civitai 모델 페이지 SSR metadata를 best-effort로 병합해 v1 API의 오래된 파일명 대신 페이지에 보이는 파일명(`z_image_bf16.safetensors`, `qwen_3_4b_fp8_mixed.safetensors` 등)을 우선 보존합니다.
- Civitai tensor metadata summary API를 조회해 primary 파일의 tensor count와 VRAM min/rec 추정치를 sidecar와 viewer 파일 badge에 저장/표시합니다.
- Civitai 모델/이미지 연계 다운로드에서 version 파일 중 `metadata.isRequired=true`인 VAE/Text Encoder 같은 필수 구성요소를 primary 모델 파일과 함께 저장하도록 했습니다. 명시적으로 특정 file id/type을 선택한 경우에는 기존처럼 선택 파일만 받습니다.
- 예시 이미지 metadata 조회가 실패해도 모델 파일 다운로드는 계속 진행합니다.
- Civitai 모델 카드 우클릭 메뉴에 `갱신`을 추가해 기존 모델 파일은 유지하면서 누락되었거나 변경된 metadata sidecar와 대표 예시 이미지를 다시 받아올 수 있게 했습니다.

### 저장 폴더 UI

- 다운로드 완료로 새 폴더가 생겼을 때 왼쪽 저장 폴더 트리가 오래된 상태로 남지 않도록, 완료 상태 전환과 수동 새로고침에서 폴더 트리도 다시 불러오게 했습니다.
- `hitomi`처럼 하위 폴더가 많은 항목이 폴더 트리 표시 예산을 독점해 뒤쪽 `huggingface`, `stable-diffusion` 폴더가 누락되지 않도록 트리 생성 순서와 폴더별 표시 한도를 조정했습니다.

### ASMR.one

- Civitai, HuggingFace, ASMR.one 등 모든 다운로드 source가 공유하는 파일명 정리 규칙에서 일본어, 중국어를 포함한 국제 문자 폴더명과 파일명을 보존하도록 고쳤습니다. 예: `イラスト`, `readme_ろまあぽ.txt`.
- ASMR.one work의 일부 트랙/이미지/부가 항목 다운로드가 실패해도 성공한 파일이 하나 이상 있으면 작업을 완료 처리하고, 실패 항목은 `_asmrone_manifest.json`에 `download_status=failed`와 오류 메시지로 기록하도록 했습니다.
- 실패한 하위 항목 때문에 생긴 빈 폴더는 제거해, 받지 못한 이미지 폴더가 빈 상태로 남지 않게 했습니다.

## 2026-07-03

### 배포

- `ghcr.io/mephiblin/hugcivi:sha-625957b`와 `ghcr.io/mephiblin/hugcivi:latest` 이미지를 로컬 `linux/amd64` 빌드로 GHCR에 push했습니다.
- `docker buildx imagetools inspect` 기준 GHCR `latest`와 `sha-625957b`는 같은 digest(`sha256:5708dffc57beb83f56870c43b586df9d00ab2897e23a03329c194d54b712317b`)입니다.
- `origin/main`은 `112ed0b`까지 push되어 설치/운영 문서 수정은 원격에 반영되어 있지만, 마지막 확인된 GHCR 이미지는 코드 기준 `625957b`입니다.
- ASMR.one 구현 커밋 `6107ece`와 문서 커밋 `b5c60f1`은 현재 로컬 `main`에만 있으며, `sha-6107ece`와 `sha-b5c60f1` GHCR 태그는 아직 없습니다. Portainer/Synology 기본 `latest` 배포에는 ASMR.one 다운로드가 포함되지 않습니다.

### 설치 운영 문서

- `UbuntuPortainer설명서.md`와 `CasaOs설명서.md` 앞부분에 대상 시스템, 접속 방식, 데이터 경로, 포트, 비밀번호, 이미지 태그를 먼저 확인하는 작동 프롬프트를 추가했습니다.
- 읽기 전용 점검 승인과 설치/변경 승인을 분리해, 사용자가 명시적으로 허가하기 전에는 Docker/Portainer 설치, Stack 배포, `docker pull`, 폴더 생성, 권한 변경, 삭제 작업을 하지 않도록 문서화했습니다.
- CasaOS 문서는 Portainer가 없을 때 CasaOS Custom Install/Compose fallback과 Portainer 설치 중 무엇을 원하는지 먼저 묻도록 했고, Ubuntu Portainer 문서는 Docker/Portainer 설치까지 진행할지 별도 확인하도록 했습니다.
- 현재 Docker 호스트에서 발견된 `tazihad/byedpi` SOCKS5 프록시 컨테이너 재설치 방법과 HugCivi `YT_DLP_PROXY=socks5://192.168.200.100:1080` 적용 방법을 [ByeDPI SOCKS5 프록시 가이드](docs/byedpi-socks-proxy.md)에 추가하고, Ubuntu/CasaOS 설치 문서의 proxy notes에서도 해당 가이드를 연결했습니다.

### ASMR.one

- ASMR.one work URL(`/work/RJ...`, `/work/<id>/DLSITE/RJ...`)을 다운로드 source로 추가했습니다.
- ASMR.one 작업은 `mediaDownloadUrl?action=download`로 실제 track 파일을 저장하고, `_asmrone_metadata.json`, `_asmrone_tracks.json`, `_asmrone_manifest.json`, `_archive_metadata.json` sidecar를 남깁니다.
- 라이브러리와 미디어 viewer가 오디오 파일을 미디어 항목으로 인식하도록 했습니다.
- 이 기능은 현재 소스에는 있지만 새 컨테이너 이미지가 push되기 전까지 GHCR `latest` 배포에서는 사용할 수 없습니다.

### YouTube/yt-dlp

- 기본 YouTube 자막 다운로드를 best-effort로 처리해, YouTube가 자막 요청에 `HTTP 429 Too Many Requests`를 반환해도 영상 파일이 저장되면 작업이 성공할 수 있게 했습니다.
- yt-dlp/gallery-dl 완료 판정에서 `.srt`와 `.vtt` 자막 파일을 실제 미디어 다운로드로 세지 않도록 해, 자막만 남은 실패 작업이 성공 처리되지 않게 했습니다.
- 단일 YouTube/yt-dlp 영상 카드 제목은 `video-...` 폴더 슬러그 대신 yt-dlp `.info.json`의 실제 영상 제목을 우선 표시합니다.

## 2026-07-02

### 구조 안정화

- FastAPI startup/shutdown을 lifespan 중심으로 정리했습니다.
- 외부 다운로드 job과 서버-local internal job을 `job_kind`로 분리했습니다.
- ZIP 생성, 비디오 transcode, poster 생성을 internal job으로 처리하도록 바꿨습니다.
- 다운로드 큐와 internal job 큐를 분리해 provider rate limit과 NAS CPU/I/O 보호 설정을 따로 관리합니다.
- `/api/jobs`는 기본 호환 배열 응답을 유지하면서 cursor pagination과 summary query를 지원합니다.

### DB와 라이브러리

- `job_artifacts`, `job_content_refs`, `library_items`, `library_scan_state`, `maintenance_runs` 테이블을 추가했습니다.
- 라이브러리는 DB-backed 증분 index를 우선 사용하고, 필요 시 filesystem scan으로 보완합니다.
- WAL, checkpoint, optimize, compact, online backup maintenance API를 추가했습니다.
- DB backup은 UI 저장 credential을 포함할 수 있으므로 credential backup으로 취급해야 합니다.

### Hitomi와 미디어 UX

- Hitomi listing URL은 기존 자동 queue 외에 확인 후 선택 queue하는 `confirm` 모드를 지원합니다.
- 미디어 viewer는 cache miss 시 transcode/poster job을 만들고 polling 후 재생/표시합니다.
- 영상, 이미지, Civitai 이미지 페이지처럼 미디어 뷰어가 있는 라이브러리 카드는 우클릭 메뉴 없이 카드 본문 클릭으로 바로 미디어 뷰어를 엽니다.
- 폴더 다운로드는 ZIP 준비 job을 만들고 완료 후 artifact를 다운로드합니다.

### 문서

- README에 현재 구조 요약과 최신 기능을 반영했습니다.
- [아키텍처](docs/architecture.md), [운영 가이드](docs/operations.md), [개발 가이드](docs/development.md), [프로젝트 철학](docs/philosophy.md)을 추가했습니다.
- [문서 인덱스](docs/index.md), [기능별 코드 맵](docs/feature-code-map.md), [구성 레퍼런스](docs/configuration.md)를 추가해 사람이든 LLM이든 바로 코드 위치와 설정을 찾을 수 있게 했습니다.
- [CasaOS 설치 가이드](docs/install-casaos.md)와 [Ubuntu 설치 가이드](docs/install-ubuntu.md)에 Docker/Compose 배포, `/data`와 `/config` 영속 폴더, `YT_DLP_PROXY` 사용 기준을 추가했습니다.
- LLM/인수인계 시작점은 [README_LLM.md](README_LLM.md)에 두고, 날짜별 상세 작업 내역은 [docs/patch-notes](docs/patch-notes)에 기록하도록 정리했습니다.

### 크롬 확장

- 현재 탭 URL이나 직접 입력한 URL을 HugCivi 서버의 `/api/jobs/bulk`로 보내는 Manifest V3 편의 확장을 추가했습니다.
- 웹 UI 우측 상단 용량 표시 옆에 `애드온` 버튼을 추가하고, `/api/addon/chrome-extension`에서 설치용 zip을 받을 수 있게 했습니다.
- Docker 이미지가 `chrome-extension/` 폴더를 포함하도록 빌드 구성을 갱신했습니다.

### 저장 폴더 UI

- 왼쪽 저장 폴더 하단 입력을 폴더 검색창으로 바꿨습니다.
- 폴더 검색은 선택된 폴더와 하위 폴더로 범위를 좁히고, `/data` 루트에서는 전체 폴더 트리를 검색합니다.
- 폴더 생성은 폴더 트리 우클릭 메뉴의 `새 폴더` 팝업으로 옮겼습니다.
- 우클릭 `이동`은 텍스트 경로 입력 대신 폴더 트리에서 이동 대상을 선택하는 팝업을 사용합니다.

### YouTube/yt-dlp

- YouTube playlist URL은 `/data/gallery-dl/youtube.com/playlist/<playlist-id>`에 저장하도록 정리했습니다.
- 일반 YouTube 영상과 채널 URL은 yt-dlp metadata의 채널명을 사용해 `/data/gallery-dl/youtube.com/channel/<channel-name>`에 저장합니다.
- 채널명을 확인할 수 없는 경우에는 기존 URL 기반 폴더명으로 fallback합니다.
- yt-dlp 전용 프록시 설정 `YT_DLP_PROXY`와 웹 UI `YouTube/yt-dlp Proxy` 입력을 추가했습니다. 이 값은 yt-dlp에 `--proxy`로 전달되며, 전체 컨테이너 프록시가 이미 동작하는 환경에서는 비워둘 수 있습니다.
- [YouTube 구독 설계](docs/youtube-subscriptions-design-2026-07-02.md)를 구현 기준 문서로 정리해 독립 구독 탭, 구독 scheduler, 구독 queue, 초기 다운로드 정책을 문서화했습니다.
- YouTube 구독 Phase 1-6으로 `subscriptions`, `subscription_items` SQLite 테이블, subscription CRUD API, 수동/예약 discovery, 왼쪽 `구독` 탭, 추가 모달, 독립 구독 다운로드 worker, 항목별 queue/skip/retry 조작, 구독별 저장 용량 표시를 추가했습니다.
- `구독` 탭이 활성화되면 메인 작업 영역이 일반 `작업 목록`에서 `구독 작업 목록`으로 전환되며, aggregate `/api/subscriptions/items` API와 상태 필터로 구독 항목을 일반 `jobs`와 분리해 확인할 수 있습니다.

## 2026-06-30

### 다운로드 기능 확장

- Hitomi 갤러리 URL, reader URL, `hitomi {gallery_id}` 입력 다운로드를 추가했습니다.
- `gallery-dl`과 `gdl` 접두어로 gallery-dl 지원 사이트 범용 다운로드를 사용할 수 있게 했습니다.
- `/data/hitomi`, `/data/gallery-dl` 저장 루트를 추가했습니다.
- Hitomi 다운로드는 갤러리 페이지 이미지를 폴더에 저장하고, 프론트에서 폴더 다운로드를 요청하면 ZIP으로 제공합니다.
- 컨테이너 시작 시 gallery-dl을 설정된 안정 버전 범위 안에서 자동 업데이트합니다.
- 공식 gallery-dl 지원 사이트와 인증 분류는 [docs/gallery-dl-auth.md](docs/gallery-dl-auth.md)에 정리했습니다.

### 인증 설정

- 설정 모달의 API 토큰 패널을 Hugging Face/Civitai 전용에서 gallery-dl 인증까지 확장했습니다.
- gallery-dl Username/Password, Cookies File, Browser Cookies, Extra Options 입력을 추가했습니다.
- gallery-dl의 `Cookies`, `OAuth`, `API Key`, `Supported`, `Required` 인증 유형을 앱 설정 방식과 연결했습니다.
- 설정 모달은 저장된 토큰, 비밀번호, cookie path, 프록시 URL, Extra Options 값을 평문으로 다시 표시합니다. 저장한 값은 새 다운로드 작업부터 재시작 없이 적용되며, 값을 비워 저장하면 UI 저장값을 삭제합니다.

### 대기열 관리

- 사용자 설정 모달에 대기열 관리 패널을 추가했습니다.
- 공급자별 동시 다운로드 수 제한을 추가했습니다.
- 전체 동시 다운로드 수 제한을 추가했습니다.
- 지정한 시간 동안 진행률 변화가 없으면 다운로드를 멈출 수 있는 무진행 타임아웃을 추가했습니다.
- Hugging Face, Civitai, Hitomi, gallery-dl, 일반 URL 작업을 공급자별 대기열로 분리하면서 전체 제한도 함께 지키도록 했습니다.

### 작업 제어

- 기존 `취소` 액션을 제거했습니다.
- `정지`한 작업은 같은 버튼이 `재개`로 바뀌도록 UI와 상태 처리를 정리했습니다.
- 스트리밍 다운로드 삭제 시 `.part` 파일 정리는 유지했습니다.

### 라이브러리 UI

- 라이브러리 카드를 상단 블러 오버레이 바 형태로 개편했습니다.
- 공급자 배지를 별도 스타일로 분리했습니다.
- URL 바로가기 아이콘을 문서 아이콘에서 globe 아이콘으로 바꿨습니다.
- 즐겨찾기 비활성 상태는 중립 별, 활성 상태는 분홍 원형 배지로 보이게 했습니다.
- A-Z, Z-A, 날짜순, 즐겨찾기 정렬과 썸네일 블러 토글은 유지했습니다.

### ComfyUI 워크플로우

- ComfyUI 워크플로우 JSON 다운로드를 추가했습니다.
- PNG에 내장된 워크플로우 metadata 추출을 추가했습니다.
- 홈 화면 드래그 앤 드롭으로 워크플로우 PNG/JSON을 저장할 수 있게 했습니다.
- 노드 그래프, 모델 목록, 원본 JSON을 볼 수 있는 워크플로우 뷰어를 추가했습니다.
- `/data/comfyui/workflows`를 기본 워크플로우 저장 경로로 추가했습니다.

### 파일 관리

- 속성 모달에 메모 기능을 추가했습니다.
- 이름 변경과 이동 시 메모 경로가 함께 갱신되도록 했습니다.
- 삭제된 경로의 메모는 함께 삭제되도록 했습니다.
- 파일 작업은 `/data` 내부에서만 허용되도록 안전 장치를 유지했습니다.

### 배포 기본값

- Synology, Ubuntu, Docker, Portainer 설치를 고려해 보수적인 기본값을 유지했습니다.
- 전체 워커 기본값은 `MAX_CONCURRENT_DOWNLOADS=3`입니다.
- 공급자별 기본 제한은 `QUEUE_PER_PROVIDER_LIMIT=1`입니다.
- Docker compose 예시에 gallery-dl 업데이트와 인증 관련 환경변수를 추가했습니다.
