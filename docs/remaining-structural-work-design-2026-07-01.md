# 남은 구조 변경 설계

작성일: 2026-07-01

기준 상태:

- 운영 리스크 1차 안정화는 완료됨.
- 기능 기준 커밋: `39fe61a Limit expensive maintenance scans`
- 남은 항목은 즉시 장애를 막는 패치가 아니라 UX/API/DB 구조를 바꾸는 장기 개선이다.
- 2026-07-01 기준 GitHub 후보 프로젝트와 FastAPI 공식 문서를 재검토했다.

## 남은 항목

1. ZIP 생성과 미디어 transcode를 별도 DB job 타입으로 이동
2. 라이브러리 전체를 DB-backed 증분 index로 전환
3. Hitomi listing 결과 확인 UI 추가
4. FastAPI startup/shutdown을 lifespan으로 전환

## 참고 프로젝트 검토

### Tube Archivist

구성:

- Django 기반 백엔드, Celery worker/beat, Redis, Elasticsearch, React 프론트.
- HTTP 앱과 백그라운드 작업자가 분리되어 있고, 다운로드 큐/재색인/백업/수동 import 같은 작업이 task로 운영된다.
- startup에서 남은 task lock/message/cache를 정리하고, index mapping과 기본 스케줄을 검증한다.

참고할 점:

- HugCivi도 HTTP 요청 안에서 ZIP/transcode 같은 큰 작업을 오래 수행하지 말고, 상태 조회 가능한 background job으로 넘기는 방향이 맞다.
- 재시작 후 `running`/lock/임시 파일을 정리하는 startup cleanup은 지금처럼 유지하고, 내부 job 추가 후 더 엄격하게 확장한다.
- 검색/라이브러리 index는 유용하지만 Redis/Elasticsearch를 그대로 들여오면 NAS 단일 컨테이너 환경에는 복잡도와 메모리 부담이 크다.

적용 판단:

- Redis/Elasticsearch/Celery 도입은 보류한다.
- SQLite DB job과 경량 thread runner를 먼저 사용한다.
- 나중에 라이브러리 검색 성능이 실제 병목으로 확인될 때 SQLite FTS를 검토한다.

### pyLoad

구성:

- 순수 Python download manager, web UI, plugin manager, package/file 상태 모델, download thread manager.
- collector와 queue를 분리하고, URL 묶음(package)을 한 번에 추가하는 흐름이 있다.
- plugin/account별 동시 다운로드 제한, 남은 디스크 공간 검사, rate limit 테스트가 있다.

참고할 점:

- 대량 URL 주입은 단순 textarea가 아니라 “묶음 입력 -> queue 등록 -> 개별 job 상태 표시”로 보는 것이 맞다.
- 외부 provider download와 내부 CPU/I/O 작업은 같은 UI 목록에서 볼 수 있더라도 scheduler 성격은 다르다.
- provider별 제한, 디스크 여유공간 검사, queue/collector 같은 중간 상태 개념은 HugCivi의 안정성 검증 항목에 반영한다.

적용 판단:

- pyLoad 같은 범용 plugin 시스템은 도입하지 않는다.
- HugCivi의 기존 source handler를 유지하고, 내부 작업만 `job_kind` 또는 별도 `internal_jobs` 모델로 분리한다.

### File Browser

구성:

- Go/Vue 기반 파일 브라우저, scoped filesystem, listing, raw download, directory archive, preview cache.
- 디렉터리 다운로드는 archive stream으로 처리하지만, archive entry 경로 정규화와 symlink escape 방어가 중요하게 다뤄진다.
- preview cache는 hash key와 scoped lock을 사용한다.

참고할 점:

- 폴더 ZIP 기능은 기능 자체보다 경로 안전성, archive entry 안전성, symlink 처리, 디스크 사용량 예측이 더 위험하다.
- 큰 폴더를 HTTP 요청 중 바로 압축하면 서버 부하와 UX 문제가 생기므로 HugCivi에서는 async 준비 방식이 더 적합하다.
- 캐시 파일은 원본 path/stat 기반 key를 사용하고, 원본 변경 시 무효화해야 한다.

적용 판단:

- ZIP job에는 path traversal/symlink escape/unsafe archive entry 회귀 테스트를 넣는다.
- 파일 수, 추정 용량, 디스크 여유공간 preflight를 job 시작 전에 수행한다.
- preview/transcode cache도 source path, mtime, size를 포함한 key로 관리한다.

### FastAPI 공식 문서

구성:

- 최신 권장 방식은 `lifespan` async context manager다.
- `lifespan`을 쓰면 기존 startup/shutdown event handler와 섞지 않고 한 방식으로 통일해야 한다.
- 테스트에서는 `with TestClient(app)` 형태로 lifespan 실행을 검증한다.

적용 판단:

- 내부 worker/indexer 설계 전에 lifespan 전환을 먼저 해두는 것이 맞다.
- startup/shutdown 책임을 한 곳에 모아야 이후 background job runner 종료 경로를 안전하게 붙일 수 있다.

## 재검토 결론

- 기존 문서의 큰 방향은 맞다.
- 보정이 필요한 부분은 “다운로드 큐 확장”이 아니라 “외부 다운로드 큐와 내부 작업 큐의 구분”이다.
- NAS 단일 컨테이너 환경에서는 Redis/Elasticsearch/Celery보다 SQLite + 경량 runner가 우선이다.
- 라이브러리 index는 Elasticsearch식 검색 index가 아니라 DB-backed cache에서 시작한다.
- ZIP/transcode는 사용자 클릭 흐름을 크게 바꾸지 않되, 큰 작업일 때만 pending job UX를 노출한다.
- Hitomi listing 확인 UI는 안전장치지만 클릭 수를 늘릴 수 있으므로 초기 기본값은 현재처럼 `auto`를 유지하고, 설정으로 `confirm`을 선택하게 한다.

보정된 우선순위:

1. FastAPI lifespan 전환
2. 내부 job abstraction과 DB 상태 모델 정리
3. ZIP async job 전환
4. media transcode/poster async job 전환
5. Hitomi listing 확인 UI
6. DB-backed 라이브러리 증분 index

## 공통 설계 원칙

- 기존 다운로드 큐의 안정성을 깨지 않는다.
- 사용자가 익숙한 URL 추가, 작업 목록, 라이브러리 탐색 흐름은 유지한다.
- 큰 작업은 HTTP 요청 스레드에서 오래 붙잡지 않는다.
- DB schema 변경은 migration 함수와 회귀 테스트를 같이 둔다.
- 기능 전환은 기존 endpoint를 바로 제거하지 않고, 새 endpoint를 먼저 추가한 뒤 프론트를 옮긴다.
- 실패/취소/재시작 후에도 orphan 파일과 orphan DB row가 남지 않게 한다.
- 경로를 받는 API는 항상 앱이 허용한 root 아래로 정규화하고, symlink escape와 unsafe archive entry를 검증한다.

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

- 내부 작업 전용 worker pool을 둔다.
- 이유: ZIP/transcode는 CPU/I/O 중심이라 외부 사이트 다운로드 큐와 성격이 다르다.
- 기본값: `INTERNAL_JOB_MAX_CONCURRENT=1`
- 같은 UI job list에 노출하더라도 scheduler와 concurrency limit은 외부 다운로드와 분리한다.

preflight:

- 대상 path가 허용 root 안에 있는지 확인한다.
- symlink가 root 밖으로 나가면 거부한다.
- archive entry name은 `/`, `..`, backslash 기반 traversal을 모두 거부 또는 무해화한다.
- 파일 수와 추정 압축 전 용량을 계산하고, 상한 초과 시 job 생성 단계에서 사용자에게 실패 이유를 반환한다.
- artifact 저장 위치의 디스크 여유공간이 부족하면 시작하지 않는다.

파일 정리:

- artifact는 `/config/downloads`, `/config/media-cache` 하위에 저장한다.
- `artifact_expires_at`이 지난 파일은 startup cleanup이 삭제한다.
- job delete 시 artifact도 삭제할 수 있게 옵션을 둔다.
- transcode/poster cache key는 source path, mtime, size를 포함해서 원본 변경 후 낡은 cache가 재사용되지 않게 한다.

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
- path traversal/symlink escape/unsafe archive entry 테스트
- 파일 수/용량/디스크 여유공간 preflight 테스트
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
- full scan은 app startup을 막지 않는다.
- 검색이 필요해지면 Elasticsearch가 아니라 SQLite FTS를 선택 기능으로 검토한다.

API 변경:

- `/api/library`는 기본적으로 DB index를 반환한다.
- `?refresh=1`이면 background refresh를 요청한다.
- `?mode=live`는 기존 스캔 fallback으로 남긴다.

삭제/이동 연동:

- 앱 내부 rename/move/delete는 index를 즉시 갱신한다.
- 외부에서 파일이 바뀐 경우 background scan이 stale row를 정리한다.
- DB row에는 stale marker를 둘 수 있게 하여, 파일이 사라진 항목을 즉시 삭제하기보다 scan tick에서 확정 정리한다.

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
- 외부 검색 엔진을 넣으면 운영 부담이 커지므로 SQLite 한계가 실제로 확인될 때까지 보류한다.

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

metadata 저장 정책:

- discovery 결과가 `HITOMI_LISTING_MAX_GALLERIES`보다 많으면 상한까지만 저장하고 `truncated=true`를 남긴다.
- parent job metadata가 너무 커지지 않도록 gallery별 최소 필드만 저장한다.
- child job 생성은 queue API에서 idempotent하게 처리한다.

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
- queue API idempotency 테스트
- metadata 상한/truncated 표시 테스트
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
- 기존 `@app.on_event`는 모두 제거하고 한 방식으로 통일
- startup:
  - `db.init_db()`
  - `ensure_route_folders()`
  - cleanup
  - workers start
  - future indexer start
- shutdown:
  - future internal worker/indexer stop event signal
  - graceful timeout 후 종료
- 테스트는 `with TestClient(app)` 또는 lifespan-aware fixture로 통일

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
2. 내부 job abstraction과 DB 상태 모델 정리
   - ZIP/transcode를 바로 붙이기 전에 상태, artifact, cleanup, 재시작 처리를 공통화한다.
3. ZIP job 큐화
   - 사용자 영향은 제한적이고, 큰 I/O 작업을 요청 스레드에서 분리하는 효과가 크다.
4. media transcode job 큐화
   - 플레이어 UX 변경이 있어 ZIP보다 섬세하게 진행한다.
5. Hitomi listing 확인 UI
   - 안전성은 이미 상한으로 확보되어 있으므로 UX 설계를 먼저 한다.
6. DB-backed 라이브러리 index
   - 효과는 크지만 schema/background/stale 정책이 가장 복잡하므로 마지막에 진행한다.

## 완료 기준

- 각 단계마다 문서, migration, 테스트, UI 확인이 함께 들어간다.
- 기본 사용 흐름이 기존보다 느려지거나 클릭 수가 늘어나는 경우 설정값으로 기존 동작을 유지한다.
- 새 background 작업은 pause/delete/restart 시나리오를 반드시 통과한다.
- 전체 검증은 최소 `python3 -m pytest -q`, 프론트 변경이 있으면 Playwright screenshot까지 포함한다.
- ZIP/media cache는 disk pressure 상황에서도 NAS를 오래 붙잡지 않도록 concurrency, timeout, cleanup, preflight를 모두 검증한다.
- 파일 작업은 path traversal, symlink escape, archive entry traversal 회귀 테스트를 포함한다.

## 참고 링크

- Tube Archivist: <https://github.com/tubearchivist/tubearchivist>
- Tube Archivist docker compose: <https://raw.githubusercontent.com/tubearchivist/tubearchivist/master/docker-compose.yml>
- Tube Archivist Unraid 설치 문서: <https://docs.tubearchivist.com/installation/unraid/>
- pyLoad: <https://github.com/pyload/pyload>
- File Browser: <https://github.com/filebrowser/filebrowser>
- File Browser folder download issue: <https://github.com/filebrowser/filebrowser/issues/2224>
- FastAPI lifespan 문서: <https://fastapi.tiangolo.com/advanced/events/>
- FastAPI lifespan test 문서: <https://fastapi.tiangolo.com/advanced/testing-events/>
