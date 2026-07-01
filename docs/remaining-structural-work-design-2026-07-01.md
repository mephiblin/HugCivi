# 남은 구조 변경 설계

작성일: 2026-07-01

기준 상태:

- 운영 리스크 1차 안정화는 완료됨.
- `main` 기준 커밋: `39fe61a Limit expensive maintenance scans`
- 남은 항목은 즉시 장애를 막는 패치가 아니라 UX/API/DB 구조를 바꾸는 장기 개선이다.

## 남은 항목

1. ZIP 생성과 미디어 transcode를 별도 DB job 타입으로 이동
2. 라이브러리 전체를 DB-backed 증분 index로 전환
3. Hitomi listing 결과 확인 UI 추가
4. FastAPI startup/shutdown을 lifespan으로 전환

## 공통 설계 원칙

- 기존 다운로드 큐의 안정성을 깨지 않는다.
- 사용자가 익숙한 URL 추가, 작업 목록, 라이브러리 탐색 흐름은 유지한다.
- 큰 작업은 HTTP 요청 스레드에서 오래 붙잡지 않는다.
- DB schema 변경은 migration 함수와 회귀 테스트를 같이 둔다.
- 기능 전환은 기존 endpoint를 바로 제거하지 않고, 새 endpoint를 먼저 추가한 뒤 프론트를 옮긴다.
- 실패/취소/재시작 후에도 orphan 파일과 orphan DB row가 남지 않게 한다.

## 1. ZIP/Transcode Job 큐화

### 현재 상태

- ZIP 생성은 `/api/fs/download` 요청 안에서 수행된다.
- 미디어 transcode/poster 생성은 `/api/media/play`, `/api/media/poster` 요청 안에서 수행된다.
- 현재는 semaphore, timeout, temp cleanup, startup cleanup으로 부하를 낮춘 상태다.

### 목표

- ZIP 생성과 transcode를 DB job으로 등록한다.
- 작업 목록에서 준비 중/실패/완료 상태를 볼 수 있게 한다.
- 완료 후 파일 다운로드 또는 재생 URL을 반환한다.
- 브라우저 요청이 오래 열린 상태로 대기하지 않게 한다.

### 설계

새 job source/type:

- `archive_zip`
- `media_transcode`
- `media_poster`

새 DB 필드 또는 metadata:

- `artifact_path`: 생성된 ZIP/cache 파일의 실제 경로
- `artifact_url`: 완료 후 프론트가 호출할 다운로드/재생 URL
- `artifact_expires_at`: 임시 파일 TTL
- `job_kind`: 일반 다운로드와 내부 작업 구분

새 API:

- `POST /api/fs/download-jobs`
  - 입력: `path`
  - 동작: 폴더면 ZIP job 생성, 파일이면 기존처럼 바로 다운로드 가능
  - 반환: `job_id`, `status`
- `GET /api/fs/download-jobs/{job_id}`
  - 완료 시 `download_url` 반환
- `POST /api/media/transcode-jobs`
  - 입력: media path
  - 완료 시 playable media URL 반환

작업자 설계:

- 다운로드 worker와 같은 scheduler를 공유하거나, 내부 작업 전용 worker pool을 둔다.
- 권장: 내부 작업 전용 pool.
  - 이유: ZIP/transcode는 CPU/I/O 중심이라 외부 사이트 다운로드 큐와 성격이 다르다.
  - 기본값: `INTERNAL_JOB_MAX_CONCURRENT=1`

파일 정리:

- artifact는 `/config/downloads`, `/config/media-cache` 하위에 저장한다.
- `artifact_expires_at`이 지난 파일은 startup cleanup이 삭제한다.
- job delete 시 artifact도 삭제할 수 있게 옵션을 둔다.

### 진행 순서

1. DB metadata schema와 내부 job runner 추가
2. ZIP job API 추가
3. 프론트 폴더 다운로드 버튼을 ZIP job 방식으로 전환
4. transcode job API 추가
5. 프론트 플레이어가 준비 전이면 pending 상태를 표시하도록 전환
6. 기존 동기 endpoint는 fallback으로 남겼다가 안정화 후 축소

### 검증

- ZIP job 생성/완료/실패/삭제 테스트
- transcode job 생성/완료/실패/삭제 테스트
- 재시작 시 running 내부 job이 canceled/queued로 정리되는지 테스트
- artifact TTL cleanup 테스트
- UI pending/done/error 상태 Playwright 확인

### 남긴 이유

- API와 프론트 흐름이 바뀐다.
- 작업 목록에 내부 작업이 섞이므로 필터/표시 정책이 필요하다.
- 기존 “클릭하면 바로 다운로드” UX가 “준비 후 다운로드” UX로 일부 바뀐다.

## 2. DB-backed 라이브러리 증분 Index

### 현재 상태

- 라이브러리는 `/data`를 스캔해서 카드 목록을 만든다.
- 현재는 scan budget으로 NAS 부하를 제한한다.
- 대량 파일 환경에서는 여전히 첫 탐색이나 속성 조회가 무거울 수 있다.

### 목표

- 라이브러리 목록은 DB index에서 빠르게 조회한다.
- 파일 변경은 background indexer가 반영한다.
- UI polling이 `/data` 전체 스캔을 반복하지 않게 한다.

### 설계

새 테이블:

- `library_items`
  - `path TEXT PRIMARY KEY`
  - `kind TEXT`
  - `source TEXT`
  - `title TEXT`
  - `category TEXT`
  - `media_count INTEGER`
  - `size_bytes INTEGER`
  - `thumbnail_path TEXT`
  - `metadata_json TEXT`
  - `mtime_ns INTEGER`
  - `indexed_at TEXT`
- `library_scan_state`
  - `root TEXT PRIMARY KEY`
  - `cursor TEXT`
  - `last_full_scan_at TEXT`
  - `status TEXT`

Indexer 방식:

- startup 후 background thread가 낮은 우선순위로 스캔한다.
- 한 tick마다 `LIBRARY_INDEX_SCAN_BATCH_SIZE`개만 처리한다.
- 변경 감지는 우선 mtime/size 기반 polling으로 시작한다.
- inotify/watchdog 도입은 NAS/컨테이너 환경 호환성 검증 후 선택한다.

API 변경:

- `/api/library`는 기본적으로 DB index를 반환한다.
- `?refresh=1`이면 background refresh를 요청한다.
- `?mode=live`는 기존 스캔 fallback으로 남긴다.

삭제/이동 연동:

- 앱 내부 rename/move/delete는 index를 즉시 갱신한다.
- 외부에서 파일이 바뀐 경우 background scan이 stale row를 정리한다.

### 진행 순서

1. index 테이블 migration 추가
2. 기존 `library_item_for_path()` 결과를 row로 저장하는 serializer 추가
3. 수동 reindex API 추가
4. `/api/library`를 DB 우선, live fallback 방식으로 변경
5. 앱 내부 파일 조작 API에 index update hook 추가
6. background incremental scan 추가
7. scan 상태와 마지막 갱신 시간을 UI에 작게 표시

### 검증

- 기존 sidecar 기반 카드 생성 결과와 index row 일치 테스트
- 파일 rename/move/delete 후 index 갱신 테스트
- 외부 파일 삭제를 background scan이 제거하는 테스트
- 큰 fixture에서 `/api/library`가 scan budget 없이 빠르게 반환되는 테스트

### 남긴 이유

- DB schema와 background worker가 추가된다.
- 파일시스템을 사용자가 NAS에서 직접 만질 수 있어 stale index 정책이 필요하다.
- 잘못 만들면 “파일은 있는데 UI에 안 보임” 문제가 생길 수 있다.

## 3. Hitomi Listing 확인 UI

### 현재 상태

- Hitomi listing URL을 넣으면 gallery URL을 discover하고 child job을 큐에 넣는다.
- `HITOMI_LISTING_MAX_GALLERIES` 상한으로 폭주를 막는다.
- 중복/이미 받은 gallery는 skip한다.

### 목표

- listing discovery 결과를 먼저 보여준다.
- 사용자가 전체/일부를 선택한 뒤 큐에 넣는다.
- 넓은 검색 URL이 실수로 수백 개 다운로드를 시작하지 않게 한다.

### 설계

상태 모델:

- parent listing job status:
  - `discovering`
  - `awaiting_selection`
  - `queueing`
  - `done`
  - `failed`

새 metadata:

- `listing_discovery`
  - `gallery_id`
  - `gallery_url`
  - `title`
  - `status`
  - `existing_job_id`
  - `selected`

새 API:

- `POST /api/hitomi/listing/discover`
  - listing URL을 discover-only job으로 등록
- `GET /api/hitomi/listing/{job_id}`
  - discovered gallery 목록 반환
- `POST /api/hitomi/listing/{job_id}/queue`
  - 선택된 gallery만 child job 생성

프론트:

- 검색 모달 또는 작업 상세에 listing 결과 패널 추가
- 전체 선택, 미다운로드만 선택, 이미 받은 항목 숨김
- 상한 초과 시 “처음 N개만 선택됨” 표시

### 진행 순서

1. parent job이 child job을 바로 만들지 않는 discover-only 모드 추가
2. listing 결과 metadata 저장 구조 정리
3. queue 선택 API 추가
4. 프론트 선택 UI 추가
5. 기존 즉시 큐잉 방식은 설정값으로 유지:
   - `HITOMI_LISTING_QUEUE_MODE=auto|confirm`
   - 초기 기본값은 `auto`
   - 안정화 후 `confirm` 기본 전환 검토

### 검증

- discover-only가 child job을 만들지 않는 테스트
- 선택된 항목만 큐에 들어가는 테스트
- 이미 받은/queued 항목 skip 테스트
- 상한 초과 UI 표시 테스트

### 남긴 이유

- 기존 사용감은 URL 입력 즉시 대기열 등록이다.
- 확인 UI를 기본값으로 바꾸면 클릭 수가 늘어난다.
- parent job 상태와 child queue 생성 시점이 분리되어 API 설계가 필요하다.

## 4. FastAPI Lifespan 전환

### 현재 상태

- `@app.on_event("startup")`를 사용한다.
- 테스트에서 deprecation warning이 나온다.
- 기능 문제는 아니다.

### 목표

- FastAPI lifespan handler로 startup/shutdown을 전환한다.
- background worker, cleanup, future indexer 종료 경로를 명확히 한다.

### 설계

- `lifespan(app)` async contextmanager 추가
- startup:
  - `db.init_db()`
  - `ensure_route_folders()`
  - cleanup
  - workers start
  - future indexer start
- shutdown:
  - future internal worker/indexer stop event signal
  - graceful timeout 후 종료

### 진행 순서

1. 현재 startup 함수를 `startup_tasks()`로 분리
2. lifespan 추가
3. 테스트 fixture가 lifespan을 직접 호출하거나 TestClient로 startup을 검증하게 조정
4. warning 제거 확인

### 검증

- `python3 -m pytest -q`에서 FastAPI on_event warning 제거
- startup cleanup/start_workers 호출 테스트
- future background worker shutdown 테스트

### 남긴 이유

- 단순 warning이지만 app 생성 흐름과 테스트 fixture가 바뀐다.
- 내부 worker/indexer 구조 변경과 같이 처리하면 중복 수정을 줄일 수 있다.

## 권장 진행 순서

1. FastAPI lifespan 전환
   - 규모가 작고 이후 background worker 설계의 기반이 된다.
2. ZIP job 큐화
   - 사용자 영향은 제한적이고, 큰 I/O 작업을 요청 스레드에서 분리하는 효과가 크다.
3. media transcode job 큐화
   - 플레이어 UX 변경이 있어 ZIP보다 섬세하게 진행한다.
4. Hitomi listing 확인 UI
   - 안전성은 이미 상한으로 확보되어 있으므로 UX 설계를 먼저 한다.
5. DB-backed 라이브러리 index
   - 효과는 크지만 schema/background/stale 정책이 가장 복잡하므로 마지막에 진행한다.

## 완료 기준

- 각 단계마다 문서, migration, 테스트, UI 확인이 함께 들어간다.
- 기본 사용 흐름이 기존보다 느려지거나 클릭 수가 늘어나는 경우 설정값으로 기존 동작을 유지한다.
- 새 background 작업은 pause/delete/restart 시나리오를 반드시 통과한다.
- 전체 검증은 최소 `python3 -m pytest -q`, 프론트 변경이 있으면 Playwright screenshot까지 포함한다.
