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

## 코드 기준 작업 가능성 검토

2026-07-01 현재 코드 기준으로 문서의 작업은 진행 가능하다. 다만 대부분이 단순 기능 추가가 아니라 lifecycle, DB row 의미, 프론트 상태 표시를 같이 바꾸는 작업이다.

현재 코드 앵커:

- `app/main.py`
  - `app = FastAPI(...)`
  - `@app.on_event("startup")`
  - `/api/fs/download-info`
  - `/api/fs/download`
  - `/api/media/play`
  - `/api/media/poster`
  - `/api/library`
  - `library_items()`
  - `library_item_for_path()`
  - `create_zip_archive()`
- `app/downloader.py`
  - `start_workers()`
  - `scheduler_loop()`
  - `enqueue_existing_jobs()`
  - `enqueue_job()`
  - `run_job()`
  - `download_hitomi_listing()`
  - `create_hitomi_listing_gallery_jobs()`
- `app/db.py`
  - `init_db()`
  - `ensure_job_columns()`
  - `create_job()`
  - `list_jobs()`
  - `update_job()`
- `app/models.py`
  - `SourceType`
  - `ParsedDownload`
- `app/templates/index.html`
  - bulk URL modal
  - job polling/rendering
  - local download queue
  - media viewer

확인된 기반:

- 외부 다운로드용 DB job, worker, queue, retry/pause/delete 흐름은 이미 있다.
- bulk URL 입력은 이미 프론트와 API가 있다.
- provider별 동시성 제한과 cooldown은 이미 있다.
- ZIP, media cache, stale cleanup, semaphore는 이미 있다.
- Hitomi listing URL parse, discovery, child job 생성, 중복 skip, 상한은 이미 있다.
- 라이브러리 카드 생성 함수는 `library_item_for_path()`로 분리되어 DB row serializer로 재사용 가능하다.

확인된 제약:

- 현재 download scheduler는 종료 신호가 없는 daemon loop다. lifespan 전환을 단순 warning 제거로만 처리하면 shutdown 설계가 반쪽이 된다.
- `enqueue_existing_jobs()`는 `queued`/`running` job을 모두 외부 다운로드 scheduler에 넣는다. 내부 job을 같은 테이블에 추가하면 `job_kind` 필터가 없을 때 잘못 실행될 수 있다.
- `run_job()`은 외부 source만 분기한다. `archive_zip`, `media_transcode`, `media_poster`를 `ParsedDownload.source`로 억지 추가하면 외부 다운로드 큐와 충돌한다.
- `/api/fs/download`와 `/api/media/play`는 현재 요청-응답 안에서 결과 파일을 바로 만든다. async job으로 바꾸면 프론트 pending 상태와 polling API가 필요하다.
- `/api/library`는 매번 `library_items()`를 통해 filesystem을 스캔한다. DB index 전환은 단순 table 추가가 아니라 stale 정책과 파일 조작 hook이 필요하다.

결론:

- 즉시 착수 가능 항목은 FastAPI lifespan 전환이다.
- 단, lifespan 전환 안에 `stop_workers()` 또는 stop event 기반 shutdown 설계를 같이 넣어야 한다.
- ZIP/transcode async job은 그 다음 단계의 내부 job abstraction 없이는 진행하지 않는다.
- Hitomi 확인 UI는 기존 자동 queue 흐름을 유지한 채 discover-only 모드를 추가하는 방식으로 안전하게 진행 가능하다.

## 구현 선행 조건

### 1. lifecycle 분리

추가할 함수:

- `startup_tasks()`
- `shutdown_tasks()`
- `stop_workers(timeout_seconds: float = ...)`
- future:
  - `start_internal_workers()`
  - `stop_internal_workers()`
  - `start_library_indexer()`
  - `stop_library_indexer()`

원칙:

- `@app.on_event("startup")`는 제거하고 `FastAPI(lifespan=lifespan)`으로 통일한다.
- `start_workers()`는 여러 번 호출해도 안전해야 한다.
- `stop_workers()`는 scheduler loop가 빠져나올 수 있게 signal을 넣고, 테스트에서는 짧은 timeout으로 검증한다.
- 실제 다운로드 job thread는 강제 kill하지 않는다. shutdown 시 새 작업 배정만 막고, 실행 중 external process는 기존 pause/delete/cancel 경로를 사용한다.

### 2. 내부 job과 외부 download job 분리

권장 DB column:

- `job_kind TEXT DEFAULT 'download'`
- `artifact_path TEXT`
- `artifact_url TEXT`
- `artifact_expires_at TEXT`

권장 상태:

- 기존 외부 다운로드 상태:
  - `queued`, `running`, `paused`, `pausing`, `canceling`, `canceled`, `deleting`, `failed`, `done`
- 내부 job 추가 상태:
  - 기존 상태를 최대한 재사용한다.
  - UI에서만 `preparing` 같은 표시가 필요하면 API decorator에서 변환한다.

주의점:

- `db.create_job()`은 당장 외부 다운로드 전용으로 유지한다.
- 내부 job 생성은 `create_internal_job(...)` 같은 별도 함수를 둔다.
- `enqueue_job()`은 외부 다운로드 전용으로 유지한다.
- 내부 job은 `enqueue_internal_job()` 또는 internal runner의 DB polling으로 처리한다.
- `enqueue_existing_jobs()`는 `job_kind IS NULL OR job_kind='download'`만 외부 scheduler에 넣는다.
- job list API는 download/internal job을 같이 반환해도 되지만, action button 정책은 `job_kind`별로 분기한다.

### 3. path safety 공통화

추가할 helper 후보:

- `resolve_data_path_for_job(path: str) -> Path`
- `ensure_safe_archive_source(path: Path) -> None`
- `archive_entry_name(root: Path, file: Path) -> str`
- `preflight_archive_job(path: Path) -> dict`
- `preflight_media_job(path: Path) -> dict`

검증해야 하는 항목:

- DATA_ROOT 밖으로 나가는 symlink 거부
- `..` traversal 거부
- backslash 기반 archive entry traversal 거부
- `.part` 파일 제외 유지
- 파일 수 상한
- 추정 원본 용량 상한
- artifact 저장소 디스크 여유공간

### 4. 프론트 상태 호환

원칙:

- 기존 사용자는 파일 다운로드와 작은 폴더 다운로드에서 최대한 같은 흐름을 경험해야 한다.
- 큰 폴더나 transcode가 필요한 비디오에서만 pending UI를 노출한다.
- 기존 local download queue는 즉시 iframe download 방식이므로 async ZIP job과 함께 동작하려면 polling item 상태가 추가되어야 한다.
- media viewer는 playable URL이 바로 없을 수 있으므로 `preparing` 상태와 retry/polling이 필요하다.

## 단계별 구현 계획

### Phase 0: 기준선 고정

목표:

- 현재 동작을 깨지 않는지 확인하는 테스트 기준선을 고정한다.

실행:

- `python3 -m pytest -q`
- 경고 기준:
  - FastAPI `on_event` deprecation warning은 Phase 1에서 제거
  - 그 외 warning은 새로 만들지 않는다.

추가하면 좋은 테스트:

- startup에서 `db.init_db()`, route folder 생성, stale cleanup, worker start가 호출되는지
- 기존 `/api/fs/download` 파일 다운로드 fallback이 유지되는지
- 기존 bulk URL modal API가 queue row를 만드는지

### Phase 1: FastAPI lifespan과 worker shutdown

작업:

1. `startup_tasks()`로 기존 startup body 분리
2. downloader scheduler에 stop event 추가
3. `stop_workers()` 추가
4. `lifespan(app)` 추가
5. `FastAPI(lifespan=lifespan)`로 app 생성
6. `@app.on_event("startup")` 제거
7. TestClient 기반 lifespan 테스트 추가

완료 조건:

- FastAPI `on_event` deprecation warning이 사라진다.
- startup 동작이 기존과 동일하다.
- 테스트에서 worker stop signal이 scheduler loop를 깨운다.
- 기존 queue 관련 테스트가 통과한다.

하지 말 것:

- 이 단계에서 ZIP/transcode job을 추가하지 않는다.
- 이 단계에서 library indexer를 추가하지 않는다.

### Phase 2: 내부 job DB abstraction

작업:

1. `ensure_job_columns()`에 `job_kind`, `artifact_path`, `artifact_url`, `artifact_expires_at` 추가
2. 기존 row는 migration 후 `job_kind`가 null이어도 download로 취급
3. `create_internal_job()` 추가
4. `list_internal_jobs_to_resume()` 또는 internal pending selector 추가
5. `enqueue_existing_jobs()`가 download job만 외부 scheduler에 넣도록 수정
6. `decorate_job()`이 internal job artifact/status를 표시하도록 보강
7. pause/delete/clear 동작에서 artifact cleanup 정책 추가

완료 조건:

- 기존 외부 다운로드 job 생성/재시도/삭제 테스트가 그대로 통과한다.
- internal job row를 만들어도 외부 downloader `run_job()`으로 들어가지 않는다.
- restart 시 running internal job은 문서화한 정책대로 `queued`, `failed`, 또는 `canceled` 중 하나로 정리된다.

권장 정책:

- ZIP/transcode처럼 재시도 가능한 준비 작업은 restart 후 `queued`로 되돌린다.
- temp artifact는 startup cleanup에서 삭제한다.
- 완료 artifact는 TTL까지 유지한다.

### Phase 3: ZIP async job

작업:

1. `preflight_archive_job()` 추가
2. `create_zip_archive()`의 안전 검사를 helper로 분리
3. `POST /api/fs/download-jobs` 추가
4. `GET /api/fs/download-jobs/{job_id}` 추가
5. 완료 artifact download endpoint 추가
6. 기존 `/api/fs/download`는 fallback으로 유지
7. 프론트 local download queue가 folder async job을 poll하도록 수정

완료 조건:

- 파일은 기존처럼 즉시 다운로드된다.
- 폴더는 async job으로 준비되고 완료 후 다운로드 URL을 받는다.
- path traversal/symlink escape/unsafe entry 테스트가 통과한다.
- archive 생성 실패 시 temp 파일이 남지 않는다.
- artifact TTL cleanup이 동작한다.

하지 말 것:

- 모든 `/api/fs/download` 호출을 한 번에 제거하지 않는다.
- ZIP 압축률 개선이나 포맷 추가는 이 단계 범위가 아니다.

### Phase 4: media transcode/poster async job

작업:

1. `preflight_media_job()` 추가
2. `media_cache_key()` 정책 유지
3. `POST /api/media/transcode-jobs` 추가
4. poster job도 같은 internal runner에 연결
5. `/api/media/play`는 cache hit이면 즉시 반환, cache miss이면 job 필요 응답으로 전환 검토
6. media viewer에서 preparing/polling 상태 추가

완료 조건:

- browser playable 파일이 이미 있으면 기존처럼 바로 재생된다.
- transcode가 필요한 파일은 준비 중 표시 후 재생된다.
- 원본 mtime/size 변경 시 기존 cache를 재사용하지 않는다.
- 동시 transcode 제한과 timeout이 유지된다.

주의:

- 기존 `FileResponse`만 기대하는 프론트 코드를 먼저 바꾸지 않으면 재생 UX가 깨진다.
- mobile viewer에서 preparing 상태가 controls와 겹치지 않는지 확인한다.

### Phase 5: Hitomi listing confirm mode

작업:

1. `HITOMI_LISTING_QUEUE_MODE=auto|confirm` 설정 추가
2. `download_hitomi_listing()`에 discover-only 분기 추가
3. discovery 결과 sidecar/metadata 저장 형식 정리
4. `POST /api/hitomi/listing/discover` 추가
5. `GET /api/hitomi/listing/{job_id}` 추가
6. `POST /api/hitomi/listing/{job_id}/queue` 추가
7. 프론트 결과 선택 UI 추가

완료 조건:

- 기본값 `auto`에서는 기존 URL 입력 즉시 큐잉 UX가 유지된다.
- `confirm`에서는 child job이 자동 생성되지 않는다.
- queue API는 idempotent하다.
- 이미 받은/queued gallery는 다시 만들지 않는다.
- 상한 초과 시 `truncated=true`와 capped count가 보인다.

주의:

- parent job metadata에 모든 gallery 상세 정보를 넣지 않는다.
- 큰 listing은 sidecar 중심, DB metadata는 summary 중심으로 유지한다.

### Phase 6: DB-backed library index

작업:

1. `library_items`, `library_scan_state` table 추가
2. `library_item_for_path()` 결과를 DB row로 저장하는 serializer 추가
3. manual reindex API 추가
4. `/api/library?mode=live` fallback 유지
5. `/api/library` 기본값을 DB 우선으로 전환
6. rename/move/delete API에 index update hook 추가
7. background incremental scan 추가

완료 조건:

- 기존 sidecar 기반 카드와 DB index row 결과가 일치한다.
- 외부 파일 삭제는 stale marker 후 background scan에서 정리된다.
- 앱 내부 rename/move/delete는 즉시 index가 갱신된다.
- `/api/library` polling이 `/data` 전체 재귀 스캔을 반복하지 않는다.

주의:

- startup에서 full scan을 돌리지 않는다.
- 첫 index가 비어 있을 때 UI가 빈 라이브러리로 오해하지 않도록 `indexing` 상태를 제공한다.

## 공통 설계 원칙

- 기존 다운로드 큐의 안정성을 깨지 않는다.
- 사용자가 익숙한 URL 추가, 작업 목록, 라이브러리 탐색 흐름은 유지한다.
- 큰 작업은 HTTP 요청 스레드에서 오래 붙잡지 않는다.
- DB schema 변경은 migration 함수와 회귀 테스트를 같이 둔다.
- 기능 전환은 기존 endpoint를 바로 제거하지 않고, 새 endpoint를 먼저 추가한 뒤 프론트를 옮긴다.
- 실패/취소/재시작 후에도 orphan 파일과 orphan DB row가 남지 않게 한다.
- 경로를 받는 API는 항상 앱이 허용한 root 아래로 정규화하고, symlink escape와 unsafe archive entry를 검증한다.
- 기존 source parser와 `ParsedDownload`는 외부 다운로드 입력 표현으로 유지한다.
- 내부 작업은 source 확장이 아니라 `job_kind` 확장으로 표현한다.
- 새 기능은 기본값에서 기존 UX를 유지하고, 변경된 UX는 설정 또는 cache miss 상황부터 노출한다.

## DB 중장기 운영 설계

HugCivi는 NAS에서 장기간 쓰는 앱이므로 “오늘 기능이 동작하는가”보다 “수개월 뒤 수만 파일과 수천 job row가 있어도 느려지거나 DB가 비대해지지 않는가”를 같이 봐야 한다.

### 예상 증가 지점

증가하는 데이터:

- `jobs` row
- `jobs.log`
- `jobs.metadata_json`
- future `library_items`
- future `internal_jobs` 또는 `jobs.job_kind='internal'`
- sidecar JSON 파일
- media/ZIP artifact cache

다량 아카이빙 시 위험:

- `/api/jobs` polling이 오래된 job row와 큰 metadata/log를 반복 조회하면 느려진다.
- Hitomi/gallery-dl/Civitai image 계열은 하나의 parent가 여러 child job을 만들 수 있어 row 수가 빨리 늘어난다.
- library index를 파일 단위로 너무 상세하게 만들면 DB가 불필요하게 커진다.
- sidecar metadata와 DB metadata가 중복으로 커지면 백업/복구 시간이 길어진다.
- SQLite는 충분히 버틸 수 있지만, 인덱스 없이 status/source/path를 계속 scan하면 NAS I/O가 병목이 된다.

### DB 역할 분리

DB에 저장할 것:

- job 상태와 현재 UI에 필요한 summary
- artifact 위치와 TTL
- library card에 필요한 최소 summary
- stale 여부와 마지막 scan 시각
- 사용자가 직접 입력한 note/favorite/settings

DB에 저장하지 않을 것:

- 원본 API 전체 응답의 큰 payload
- gallery/file 목록 전체
- 긴 stdout 로그 전체
- per-file hash/checksum 전체
- 이미지 generation metadata 전문

큰 데이터 저장 위치:

- 상세 metadata는 sidecar JSON에 저장한다.
- DB `metadata_json`에는 summary와 sidecar path만 저장한다.
- 긴 discovery 결과는 `/data/.../_hitomi_listing_metadata.json` 같은 sidecar에 저장하고 DB에는 count, truncated, queued_count만 둔다.
- job log는 현재처럼 DB에 둘 수 있지만, 길이 제한과 별도 log endpoint 방식을 유지한다.

### SQLite 운영 정책

권장 설정:

- WAL mode 검토:
  - read polling과 background write가 겹치는 구조에 유리하다.
  - 컨테이너/NAS volume에서 WAL 파일 백업 방식을 함께 문서화해야 한다.
- `busy_timeout`은 현재 connection timeout과 함께 유지한다.
- 주기적인 `PRAGMA optimize` 실행을 검토한다.
- 대규모 delete 후 `VACUUM`은 자동으로 돌리지 않는다.
  - 이유: NAS에서 오래 걸리고 I/O가 크다.
  - 관리 API 또는 maintenance action으로 수동 실행한다.

권장 index:

- `jobs(status, updated_at)`
- `jobs(job_kind, status, updated_at)`
- `jobs(source, status, updated_at)`
- `jobs(target_dir)`
- `library_items(path)`
- `library_items(source, indexed_at)`
- `library_items(stale, indexed_at)`

주의:

- index는 조회 패턴이 확정된 뒤 추가한다.
- 너무 많은 index는 write 비용을 키운다.
- library index는 파일 단위가 아니라 “카드로 보일 archive/folder/file 단위”를 기본 row로 한다.

### job row 보존 정책

기본 정책:

- 활성 job은 보존한다.
- `done`, `failed`, `canceled` job은 사용자가 clear하기 전까지 보존한다.
- `/api/jobs` 목록은 최신 N개만 반환하고 상세 로그는 `/jobs/{id}/log`에서 따로 본다.
- 오래된 inactive job을 지울 때 target archive 파일은 삭제하지 않는다.

추가할 설정 후보:

- `JOB_HISTORY_MAX_ROWS`
- `JOB_HISTORY_MAX_AGE_DAYS`
- `JOB_LOG_MAX_BYTES`
- `JOB_METADATA_MAX_BYTES`

동작:

- history limit을 넘으면 inactive job 중 오래된 row부터 삭제 후보로 잡는다.
- 사용자가 “기록 지우기”를 눌렀을 때 row 삭제와 artifact 삭제를 분리한다.
- `target_dir`가 있는 done job row를 삭제해도 library card는 filesystem/sidecar에서 복원되어야 한다.

### library index 보존 정책

row 단위:

- archive/folder/file card 하나를 `library_items` row 하나로 둔다.
- gallery 내부 이미지 수천 장을 각각 row로 만들지 않는다.
- media viewer가 필요할 때만 현재 폴더 안의 media list를 live scan하거나 작은 cache를 둔다.

stale 정책:

- 파일이 사라진 것을 감지하면 즉시 삭제하지 않고 `stale=1`, `missing_since`를 남긴다.
- 다음 scan tick에서도 없으면 삭제한다.
- 외부 NAS 조작으로 일시적으로 mount가 늦게 보이는 상황을 오삭제로 처리하지 않는다.

refresh 정책:

- `mtime_ns`, `size_bytes`, sidecar mtime을 비교해 변경된 항목만 갱신한다.
- full scan은 background batch로만 수행한다.
- `/api/library?refresh=1`은 즉시 scan이 아니라 refresh request flag만 남긴다.

### sidecar와 DB 일관성

우선순위:

1. 실제 파일 존재 여부
2. sidecar metadata
3. DB index row
4. old job row

복구 정책:

- DB를 삭제하거나 job history를 clear해도 `/data`와 sidecar가 있으면 library card는 복원 가능해야 한다.
- sidecar가 없고 DB row만 남은 경우에는 stale 후보로 처리한다.
- sidecar schema version을 저장해 향후 migration 없이도 읽을 수 있게 한다.

권장 sidecar 공통 필드:

- `schema_version`
- `source`
- `source_url`
- `created_at`
- `updated_at`
- `archive_info`
- `content_summary`

### 백업/복구 관점

백업 대상:

- `/config/jobs.sqlite3`
- `/config/startup.env`
- `/data` 전체 또는 사용자가 선택한 archive root
- `/data` 안의 sidecar JSON

백업 제외 가능:

- `/config/downloads`의 만료 가능한 ZIP artifact
- `/config/media-cache`의 transcode/poster cache
- `.part`, `.tmp` 파일

복구 후 기대 동작:

- DB가 있으면 job history와 settings가 복원된다.
- DB가 없어도 sidecar 기반 library scan으로 archive 목록은 복원된다.
- running job은 복구 후 queued 또는 canceled로 정리된다.

### 장기 규모별 기준

초기 기준:

- job row 수: 1만 단위까지 UI polling이 느려지지 않아야 한다.
- library item row 수: 5만 단위까지 `/api/library` 기본 조회가 빠르게 반환되어야 한다.
- media file 수: archive 내부 수천 파일이 있어도 library card 조회는 폴더 단위 row만 읽어야 한다.

중장기 기준:

- job history 10만 row 이상이 필요해지면 history archive table 또는 export 기능을 검토한다.
- library item 10만 row 이상에서 검색 요구가 생기면 SQLite FTS를 검토한다.
- SQLite write contention이 실제 문제로 확인될 때만 외부 DB 또는 queue broker를 검토한다.

하지 말 것:

- 처음부터 Redis/Elasticsearch/PostgreSQL을 요구하지 않는다.
- archive 내부 모든 파일을 기본 DB row로 만들지 않는다.
- `/api/jobs`나 `/api/library`에서 큰 JSON/log를 매번 반환하지 않는다.
- startup에서 전체 `/data` scan이나 VACUUM을 자동 실행하지 않는다.

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

새 internal job type:

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
