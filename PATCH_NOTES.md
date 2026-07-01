# 패치내역

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
- 폴더 다운로드는 ZIP 준비 job을 만들고 완료 후 artifact를 다운로드합니다.

### 문서

- README에 현재 구조 요약과 최신 기능을 반영했습니다.
- [아키텍처](docs/architecture.md), [운영 가이드](docs/operations.md), [개발 가이드](docs/development.md), [프로젝트 철학](docs/philosophy.md)을 추가했습니다.

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
