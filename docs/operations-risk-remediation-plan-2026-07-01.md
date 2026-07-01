# 운영 안정성 수정 계획

작성일: 2026-07-01

기준 문서:

- `docs/operations-risk-code-review-2026-07-01.md`

## 핵심 판단

다운로드 job으로 등록되는 경로는 대부분 전역 큐를 탄다.

- 단일 URL 추가: DB job 생성 후 `enqueue_job()`
- 대량 URL 추가: 각 URL을 DB job으로 생성 후 `enqueue_job()`
- 재개/재시도: 기존 job을 queued로 되돌린 뒤 `enqueue_job()`
- Hitomi listing 확장: parent job이 child gallery job을 만들고 `enqueue_job()`
- Civitai image resource 확장: child Civitai job을 만들고 `enqueue_job()`

다만 다음 작업은 다운로드 큐 밖에서 HTTP 요청 처리 중 바로 실행된다.

- 워크플로 업로드 import
- 미디어 재생용 ffmpeg transcode
- 동영상 poster 생성
- 폴더 ZIP 생성
- 라이브러리/헬스체크/속성 조회용 파일시스템 스캔

따라서 수정 방향은 두 갈래다.

1. 다운로드 job 큐: 상한, 로그 제한, partial cleanup, 서버 rate-limit 존중.
2. 큐 밖 서버 작업: semaphore, TTL cleanup, stale temp cleanup으로 NAS 부하를 제한.

## UX 유지 원칙

- 기본 사용 흐름은 그대로 둔다. URL 추가, 대량 추가, 작업 목록, 로그 보기, 기록 지우기 버튼은 유지한다.
- 목록 화면은 빠르게 만들되, 로그는 기존 `/jobs/{id}/log`에서 계속 볼 수 있게 한다.
- 기본 동시성은 기존 값과 같게 둔다.
- 위험한 대량 확장은 처음부터 막기보다 높은 기본 상한을 두고, 상한에 닿은 경우 metadata/log에 이유를 남긴다.
- 서버가 명시한 `Retry-After`는 짧게 자르지 않는다. 사용자가 기다리기 싫으면 pause/delete로 제어할 수 있어야 한다.

## 1차 구현 범위

이번 패치에서 바로 구현할 항목:

- `/api/jobs`와 홈 화면 목록 payload에서 `log` 제외.
- job log 최대 보존 글자 수 적용.
- 작업 기록 지우기 전에 failed/canceled/done partial 파일 정리.
- `Retry-After`/`RateLimit` header delay는 앱 max retry sleep으로 단축하지 않음.
- 다운로드 큐 설정과 HTTP retry 설정에 hard cap 적용.
- external downloader stdout queue에 bounded queue 적용.
- Hitomi listing child job 생성 상한 적용.
- Civitai image resource child job 생성 상한 적용.
- `/config/downloads` ZIP temp startup cleanup.
- `/config/media-cache` startup cleanup과 transcode 동시 실행 semaphore 적용.
- ZIP archive에 `.part` 파일 제외.

## 2차 보류 범위

이번 패치에서 구조를 크게 바꾸지 않고 남길 항목:

- gallery-dl/yt-dlp/HF 진행률의 directory scan 제거.
- 라이브러리 전체 스캔 캐시와 증분 index.
- ZIP 생성 자체를 다운로드 job 큐로 이동.
- 미디어 transcode를 별도 job 큐로 이동.
- SQLite VACUUM 자동화.
- Hitomi listing confirmation UI.

보류 이유:

- 위 항목들은 사용자 체감 동작과 API 구조가 바뀔 수 있다.
- 먼저 현재 UX를 유지하는 방어선을 넣고, 이후 성능 병목이 실제로 남는지 관찰하는 편이 안전하다.

## 설정값

새 기본값:

- `JOB_LOG_MAX_CHARS=200000`
- `MAX_CONCURRENT_DOWNLOADS_HARD_LIMIT=12`
- `QUEUE_PER_PROVIDER_LIMIT_HARD_LIMIT=4`
- `DOWNLOAD_HTTP_MAX_RETRIES_HARD_LIMIT=8`
- `HITOMI_LISTING_MAX_GALLERIES=500`
- `CIVITAI_IMAGE_MAX_RESOURCE_JOBS=30`
- `PROCESS_OUTPUT_QUEUE_MAX_LINES=1000`
- `DOWNLOAD_ARCHIVE_TTL_SECONDS=86400`
- `MEDIA_CACHE_TTL_SECONDS=2592000`
- `MEDIA_CACHE_MAX_BYTES=0`
- `MEDIA_TRANSCODE_MAX_CONCURRENT=1`
- `DOWNLOAD_ARCHIVE_MAX_CONCURRENT=1`
- `DOWNLOAD_PROGRESS_SCAN_MAX_FILES=2000`
- `DOWNLOAD_WATCHDOG_SCAN_MAX_FILES=2000`
- `MEDIA_FILE_SCAN_MAX_FILES=5000`
- `LIBRARY_ITEM_SIZE_SCAN_MAX_FILES=2000`
- `SQLITE_VACUUM_AFTER_CLEAR=0`

환경변수로 낮추는 것은 허용한다. hard limit은 올릴 수 있지만, 기본값은 NAS 운용을 우선한다.

## 검증 계획

- log trimming 단위 테스트.
- `/api/jobs` 목록에서 log 제외 테스트.
- `/api/jobs/{id}`와 `/jobs/{id}/log` 로그 접근 유지 테스트.
- clear history 전 partial cleanup 테스트.
- Retry-After 장시간 header 존중 테스트.
- queue/hard cap 테스트.
- Hitomi listing 상한 테스트.
- Civitai image resource 상한 테스트.
- media/zip cleanup 테스트.
- 전체 `python3 -m pytest -q`.

## 구현 결과

1차 구현 범위는 코드에 반영했다.

- 목록 payload에서 `log`, `metadata_json` 제외.
- 상세 job API와 log endpoint는 기존처럼 로그 접근 유지.
- `JOB_LOG_MAX_CHARS` 기반 job log trimming.
- `/api/jobs/clear` 실행 전 inactive job partial cleanup.
- `Retry-After`/`RateLimit` header delay 보존.
- 다운로드 전역/공급자별 설정 hard cap.
- HTTP retry hard cap.
- external downloader stdout bounded queue.
- Hitomi listing child queue limit.
- Civitai image resource child queue limit.
- startup ZIP/media cache cleanup.
- media ffmpeg 변환/포스터 생성 semaphore.
- ZIP archive `.part` 제외.

추가 구현:

- gallery-dl/yt-dlp 진행률 스캔에 `DOWNLOAD_PROGRESS_SCAN_MAX_FILES` budget 적용.
- HF 진행률 스캔에 `DOWNLOAD_PROGRESS_SCAN_MAX_FILES` budget 적용.
- watchdog directory size 스캔에 `DOWNLOAD_WATCHDOG_SCAN_MAX_FILES` budget 적용.
- watchdog 스캔이 budget에 걸리면 stall로 오판하지 않도록 해당 cycle은 진행 중으로 처리.
- 라이브러리 카드 생성용 media/size scan에 `MEDIA_FILE_SCAN_MAX_FILES`, `LIBRARY_ITEM_SIZE_SCAN_MAX_FILES` budget 적용.
- 폴더 속성 상세 조회와 다운로드 완료 후 최종 size 계산은 정확도 우선으로 기존 전체 스캔 유지.
- 폴더 ZIP 생성에 `DOWNLOAD_ARCHIVE_MAX_CONCURRENT` semaphore 적용.
- `SQLITE_VACUUM_AFTER_CLEAR=1`일 때 작업 기록 삭제 후 VACUUM 실행.

검증:

- `python3 -m pytest -q`
- 결과: 107 passed, FastAPI `on_event` deprecation warning만 남음.

이번 구현에서 인터넷 검색은 사용하지 않았다. 변경 대상은 코드 내부 큐/cleanup/retry 정책이고, 외부 downloader 최신 동작 확인이 필요한 수정은 아니었다.

## 남은 구조 변경

다음은 의도적으로 즉시 변경하지 않았다.

- ZIP 생성과 미디어 transcode를 별도 DB job 타입으로 완전히 이동.
- 라이브러리 전체를 DB-backed 증분 index로 전환.
- Hitomi listing 결과 확인 UI.

현재는 semaphore, scan budget, child job limit으로 운영 위험을 낮춘 상태다. 위 구조 변경은 UX/API 변화가 크므로 별도 기능 설계 후 진행하는 편이 안전하다.
