# 운영 안정성 코드리뷰

검토일: 2026-07-01

요청 범위:

- NAS 운용 서버에 무리를 줄 수 있는 CPU, RAM, thread, subprocess, 스토리지, SQLite 성장 리스크
- 다운로드 실패, 재시도, 대량 큐잉, 외부 downloader 설정으로 상대 서버에 과도한 요청을 보낼 수 있는 리스크
- 다운로드/미디어/큐 기능 전반의 찌꺼기 파일, 캐시, 임시 파일, 무한 루프 가능성

검토 방식:

- Codex 워커 3명을 병렬 사용했습니다.
- 워커 1: CPU/RAM/동시성/스레드/스캔 리스크
- 워커 2: 스토리지 찌꺼기/캐시/임시 파일/DB 용량 리스크
- 워커 3: 외부 사이트 요청/재시도/rate-limit/무한 루프 리스크
- 워커 결과는 현재 worktree 기준으로 다시 코드 라인과 동작을 확인했습니다.

검증 제한:

- 실제 외부 사이트를 대상으로 부하 테스트는 하지 않았습니다.
- 이 문서는 코드리뷰 산출물입니다. 아래 위험요소는 아직 코드로 모두 수정된 상태가 아닙니다.
- 테스트는 현재 동작 회귀 확인용입니다. 대량 파일, 대량 stdout, 장시간 실패 루프, 실제 NAS I/O 압박은 별도 부하 테스트가 필요합니다.

## 결론

현재 구현은 기본값 기준으로 즉시 무한 루프나 무제한 병렬 다운로드가 발생하는 구조는 아닙니다. 전역 큐 제한, 공급자별 제한, provider cooldown, HTTP retry 횟수 제한, host별 request throttle, pause/delete 시 child process 종료 경로가 있습니다.

다만 운영 서버 관점에서는 다음 항목들이 실질 위험입니다.

1. job log와 일부 metadata가 무제한 커지고, job 목록 API가 log까지 반복 반환합니다.
2. gallery-dl/yt-dlp/HF 진행률과 watchdog이 큰 디렉터리를 주기적으로 재귀 스캔합니다.
3. media cache, zip temp, failed partial, HF cache의 보존/청소 정책이 부족합니다.
4. Hitomi listing과 Civitai image resource 자동 확장이 총 요청량 상한 없이 child job을 만들 수 있습니다.
5. `Retry-After`가 긴 서버 응답을 앱의 max sleep 설정으로 짧게 잘라 상대 서버 의도보다 빨리 재시도할 수 있습니다.
6. 사용자가 공격적인 동시성/extra 옵션을 넣을 때의 상한이 없습니다.

## 권장 수정 순서

1. `/api/jobs` 목록 응답에서 `log`와 큰 metadata를 제외하고, 로그 조회는 기존 job log endpoint로만 분리합니다.
2. job log 최대 보존량과 SQLite cleanup/VACUUM 정책을 추가합니다.
3. gallery-dl/yt-dlp/HF 진행률 계산에서 매초 전체 `rglob()`/`directory_size()`를 피하고, scan budget 또는 저빈도 캐시를 둡니다.
4. `MEDIA_CACHE_DIR`, `DOWNLOAD_ARCHIVE_DIR`, failed partial 파일에 TTL/quota/startup sweep을 둡니다.
5. Hitomi listing discovered/queued 개수와 Civitai resource 자동 큐잉 개수에 상한과 확인 모드를 둡니다.
6. `Retry-After`/`RateLimit` 헤더는 서버 값이 앱의 max sleep보다 길어도 줄이지 않도록 변경합니다.
7. `MAX_CONCURRENT_DOWNLOADS`, `QUEUE_PER_PROVIDER_LIMIT`, HTTP retry, transcode concurrency에 운영 상한을 둡니다.
8. ZIP 생성과 ffmpeg transcode를 요청 스레드에서 분리하거나 semaphore/background job으로 제한합니다.

## High Findings

### 1. job log가 무제한 성장하고 목록 API가 log 전체를 반복 반환함

위치:

- `app/db.py:164` `list_jobs()`가 `SELECT *`로 `log`까지 읽습니다.
- `app/main.py:361` `/api/jobs`가 `decorate_jobs(db.list_jobs())`를 그대로 반환합니다.
- `app/main.py:773` `decorate_job()`은 `parsed_json`은 제거하지만 `log`는 제거하지 않습니다.
- `app/db.py:498` `append_log()`는 `log = COALESCE(log, '') || ?`로 계속 이어 붙입니다.
- `app/downloader.py:3750` 외부 downloader stdout line을 job log에 저장합니다.

영향:

- 장시간 실패, verbose `gallery-dl`/`yt-dlp`, 대량 listing discovery가 있으면 SQLite row와 `/api/jobs` 응답이 계속 커집니다.
- UI polling 또는 job action이 100개 job의 대형 log를 반복 직렬화해 CPU, RAM, DB lock, 네트워크 트래픽을 늘릴 수 있습니다.
- 작업 기록 삭제 전까지 `/config/jobs.sqlite3`가 커지고, row 삭제 후에도 SQLite 파일이 즉시 줄어드는 것은 아닙니다.

이미 있는 방어:

- `db.list_jobs()` 기본 limit은 100개입니다.
- 외부 stdout 한 줄은 1000자로 잘라 저장합니다.

권장 수정:

- 목록 API에서는 `log`를 제외한 summary row만 반환합니다.
- 기존 `/jobs/{id}/log`처럼 필요한 job log만 조회하고, tail/offset 옵션을 추가합니다.
- job별 log 최대 바이트, 전체 DB 보존 기간, history cleanup 후 VACUUM 또는 incremental vacuum 정책을 둡니다.

### 2. 진행률/watchdog가 큰 디렉터리를 주기적으로 전체 스캔함

위치:

- `app/downloader.py:3718` 외부 process loop
- `app/downloader.py:3756` 1초마다 `update_gallery_dl_progress()`
- `app/downloader.py:3784` `update_gallery_dl_progress()`에서 `gallery_dl_downloaded_files()`와 `directory_size()` 호출
- `app/downloader.py:3795` `gallery_dl_downloaded_files()`가 `target.rglob("*")` 후 정렬
- `app/downloader.py:984` watchdog가 5초마다 target directory size를 봅니다.
- `app/downloader.py:1379` HF progress도 `directory_size(target)` 기반입니다.

영향:

- 수만 파일 Hitomi/gallery-dl archive, 큰 HF snapshot에서 다운로드보다 진행률 스캔이 더 많은 I/O를 만들 수 있습니다.
- NAS HDD 환경에서는 directory traversal 자체가 다운로드/미디어 UI 반응성을 떨어뜨릴 수 있습니다.
- watchdog scan이 오래 걸리면 stall 판단이 지연되거나 왜곡될 수 있습니다.

이미 있는 방어:

- 진행률 업데이트는 gallery-dl/yt-dlp 1초, HF 2초 간격입니다.
- `DOWNLOAD_STALL_TIMEOUT_SECONDS=0`으로 watchdog를 끌 수 있습니다.
- 전역 동시 다운로드 기본값은 3개입니다.

권장 수정:

- stdout progress 또는 downloader가 제공하는 byte count를 우선 사용합니다.
- directory scan은 파일 수/시간 budget을 두고 저빈도로 제한합니다.
- 큰 폴더에 대해서는 “정확한 전체 크기” 대신 “최근 파일명/대략 진행” 표시로 degrade합니다.

### 3. media cache가 무제한 누적되고 원본 삭제/이동과 연결되지 않음

위치:

- `app/main.py:53` 기본 `MEDIA_CACHE_DIR=/config/media-cache`
- `app/main.py:1487` `transcode_video_for_browser()`
- `app/main.py:1492` `*.play.mp4` cache 생성
- `app/main.py:1569` `video_poster_path()`
- `app/main.py:1572` `*.jpg` poster cache 생성
- `app/main.py:526`, `app/main.py:546`, `app/main.py:574` rename/move/delete는 `/data`와 DB 참조만 갱신합니다.

영향:

- 비브라우저 호환 영상 재생 시 원본급 크기의 `.play.mp4`가 `/config/media-cache`에 남습니다.
- 원본 파일을 삭제, 이동, 교체해도 이전 key의 cache가 orphan으로 남습니다.
- 장기간 미디어 열람이 많으면 `/config` 볼륨이 `/data`와 별도로 압박됩니다.

이미 있는 방어:

- 같은 cache key에는 lock이 있어 동일 영상 동시 transcode 중복은 줄입니다.
- temp 파일은 실패 시 삭제합니다.
- transcode timeout 기본값은 1800초입니다.

권장 수정:

- media cache TTL, 최대 크기, startup sweep을 추가합니다.
- 삭제/이동/이름변경 시 source path 기반 cache를 invalidation합니다.
- `MEDIA_TRANSCODE_MAX_CONCURRENT` 같은 semaphore 설정을 둡니다.

### 4. 작업 기록 지우기가 failed partial 파일을 orphan으로 만들 수 있음

위치:

- `app/main.py:382` `/api/jobs/clear`
- `app/db.py:178` `clear_job_history()`는 비활성 job row를 삭제합니다.
- `app/downloader.py:310` `job_partial_paths()`는 job metadata/target_dir 기반으로 partial을 찾습니다.
- `app/downloader.py:355` `cleanup_job_partial_files()`
- `app/main.py:848` library scan은 `.part` 파일을 숨깁니다.

영향:

- 실패 job row가 삭제되면 `.job-<id>-*.part` 위치를 추적하기 어려워집니다.
- UI 라이브러리에서는 `.part`가 보이지 않아 사용자는 용량만 줄어드는 상황을 놓칠 수 있습니다.

이미 있는 방어:

- 개별 job delete는 partial/local cleanup을 호출합니다.
- app restart 시 `deleting` 상태 job은 partial/local cleanup 후 DB에서 삭제합니다.
- partial 파일명에는 job id와 URL hash가 들어갑니다.

권장 수정:

- `/api/jobs/clear`가 failed/canceled job row를 삭제하기 전에 partial cleanup을 수행합니다.
- startup orphan sweep: `/data/**/*.job-*-*.part` 중 active job에 연결되지 않은 파일을 report 또는 cleanup합니다.
- UI에 “작업 기록 삭제는 다운로드 산출물/partial 삭제가 아니다”를 명확히 표시합니다.

### 5. Hitomi listing 확장이 총 개수 상한 없이 child job을 만들 수 있음

위치:

- `app/parsers.py:41` `HITOMI_LISTING_RE`
- `app/parsers.py:550` `parse_hitomi_url()`
- `app/downloader.py:2630` `download_hitomi_listing()`
- `app/downloader.py:2704` `discover_hitomi_listing_gallery_urls()`
- `app/downloader.py:2825` `create_hitomi_listing_gallery_jobs()`

영향:

- 넓은 tag/search/index URL 하나가 수백-수천 gallery를 discover하고 child job으로 만들 수 있습니다.
- provider limit/cooldown은 속도를 낮추지만 총 요청량은 제한하지 않습니다.
- 상대 서버 입장에서는 장시간 크롤링성 요청으로 보일 수 있습니다.

이미 있는 방어:

- 기존 queued/running/done Hitomi gallery는 중복 큐잉을 피합니다.
- provider별 동시 실행 기본값은 1이고 provider cooldown 기본값은 2~2초입니다.

권장 수정:

- `HITOMI_LISTING_MAX_GALLERIES` 또는 UI 확인 단계를 추가합니다.
- discovery 결과가 일정 개수를 넘으면 parent job을 `paused` 또는 `needs_confirmation`으로 두고 사용자가 일부만 큐잉하게 합니다.
- listing job metadata에는 전체 목록을 sidecar에 쓰되 DB summary만 유지하는 현재 방향을 유지합니다.

## Medium Findings

### 6. `Retry-After`가 서버 의도보다 짧게 잘릴 수 있음

위치:

- `app/downloader.py:1070` `retry_after_seconds()`
- `app/downloader.py:1089` `retry_delay()`
- `app/downloader.py:1096` `min(delay, DOWNLOAD_MAX_RETRY_SLEEP_SECONDS)`
- `app/downloader.py:1099` `request_with_safety()`

영향:

- 서버가 `Retry-After: 3600`을 보내도 기본 `DOWNLOAD_MAX_RETRY_SLEEP_SECONDS=300` 때문에 300초 뒤 재시도합니다.
- 429/rate-limit 상황에서 상대 서버가 요구한 대기보다 이른 요청이 됩니다.

이미 있는 방어:

- retry 대상은 429/500/502/503/504로 제한됩니다.
- 기본 retry 횟수는 3회입니다.
- sleep 중 job control을 확인합니다.

권장 수정:

- header 기반 delay는 앱 max sleep으로 줄이지 않습니다.
- 앱 max sleep은 exponential backoff fallback에만 적용하거나, `DOWNLOAD_RESPECT_RETRY_AFTER=1`을 기본값으로 둡니다.

### 7. Civitai image resource 자동 확장에도 총 개수 상한이 없음

위치:

- `app/downloader.py:2310` Civitai image page download entry
- `app/downloader.py:1728` 부족한 resource metadata 조회
- `app/downloader.py:2212` resource preflight
- `app/downloader.py:2227` resource child job queue

영향:

- image metadata에 unique modelVersionId가 많으면 parent job 하나가 여러 Civitai API 요청과 child download job을 만듭니다.
- 중복/최근 실패/persistent failure skip은 있지만 “한 parent가 만들 수 있는 총 child 수” 상한은 없습니다.

권장 수정:

- `CIVITAI_IMAGE_MAX_RESOURCE_JOBS` 상한과 UI 확인을 둡니다.
- resource health/check와 실제 queue를 분리해 사용자가 선택하도록 합니다.

### 8. 공격적인 운영 설정값에 상한이 없음

위치:

- `app/main.py:316` `MAX_CONCURRENT_DOWNLOADS` 저장
- `app/main.py:317` `QUEUE_PER_PROVIDER_LIMIT` 저장
- `app/downloader.py:914` `queue_global_limit()`
- `app/downloader.py:918` `queue_per_provider_limit()`
- `app/downloader.py:1107` `DOWNLOAD_HTTP_MAX_RETRIES`

영향:

- `MAX_CONCURRENT_DOWNLOADS=50`, `QUEUE_PER_PROVIDER_LIMIT=50`, cooldown 0, request interval 0, retry 100 같은 설정은 NAS와 상대 서버 모두에 무리입니다.
- job마다 runner thread와 watchdog thread, 외부 subprocess가 생길 수 있습니다.

이미 있는 방어:

- 기본값은 전역 3, provider별 1입니다.
- 새 provider cooldown 기본값은 최소 2초/최대 2초입니다.
- bulk add는 500개, 입력 200KB 제한이 있습니다.

권장 수정:

- UI와 env 양쪽에 hard cap 또는 warning cap을 둡니다.
- 권장 운영값:
  - `MAX_CONCURRENT_DOWNLOADS=2~3`
  - `QUEUE_PER_PROVIDER_LIMIT=1`
  - `QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS=2`, `QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS=8` 이상
  - `DOWNLOAD_HTTP_MAX_RETRIES=3`
  - `DOWNLOAD_REQUEST_MIN_INTERVAL_SECONDS>=1.5`
  - Hitomi/gallery-dl 계열은 `GALLERY_DL_SLEEP_REQUEST_SECONDS=2~5` 검토

### 9. subprocess stdout 큐가 무제한임

위치:

- `app/downloader.py:2737` `run_gallery_dl_get_urls_process()`
- `app/downloader.py:2750` unbounded `queue.Queue()`
- `app/downloader.py:3718` `run_external_download_process()`
- `app/downloader.py:3730` unbounded `queue.Queue()`
- `app/downloader.py:3776` reader thread가 모든 line을 enqueue

영향:

- noisy extractor, verbose option, 긴 discovery output이 DB append 속도보다 빠르면 RAM이 일시적으로 커집니다.

이미 있는 방어:

- 별도 reader thread가 있어 OS pipe deadlock은 줄입니다.
- pause/delete 시 process group terminate/kill 경로가 있습니다.

권장 수정:

- bounded queue와 drop/coalesce 정책을 둡니다.
- progress성 stdout은 최신 줄만 보관하고, 상세 log는 tail limit을 둡니다.

### 10. library/media 목록 API가 동기 파일시스템 스캔을 반복함

위치:

- `app/main.py:453` `/api/library`
- `app/main.py:458` `/api/media/list`
- `app/main.py:800` `library_items()`
- `app/main.py:832` `iter_data_paths()`
- `app/main.py:917` `library_item_for_path()`
- `app/main.py:1852` `path_size()`

영향:

- `/data`에 많은 미디어/모델 파일이 있으면 화면 열기/새로고침만으로 많은 `rglob`, `stat`, sort가 발생합니다.
- 여러 브라우저나 모바일/PWA가 동시에 열리면 NAS I/O가 커집니다.

이미 있는 방어:

- library item 기본 1000개, path 탐색 3000/6000개 제한이 있습니다.
- media list 반환 기본 500개 제한이 있습니다.

권장 수정:

- filesystem index cache, pagination, background refresh를 도입합니다.
- `path_size()`는 요청 시점 계산 대신 cached size 또는 optional detail endpoint로 분리합니다.

### 11. ffmpeg transcode가 요청 스레드에서 수행되고 동시성 상한이 없음

위치:

- `app/main.py:496` `/api/media/play`
- `app/main.py:1446` `browser_playable_video_path()`
- `app/main.py:1487` `transcode_video_for_browser()`
- `app/main.py:1533` ffmpeg timeout
- `app/main.py:1553` `MEDIA_TRANSCODE_LOCKS`

영향:

- 서로 다른 비호환 영상 여러 개를 동시에 재생하면 ffmpeg process가 동시에 뜹니다.
- `MEDIA_TRANSCODE_LOCKS`는 key별 lock을 저장하지만 eviction이 없어 많은 파일을 열람하면 작은 메모리 누수가 됩니다.

권장 수정:

- transcode semaphore와 queue를 둡니다.
- lock dict를 LRU/weakref/TTL 방식으로 정리합니다.
- 큰 영상은 background transcode job으로 전환하고 UI에는 준비 상태를 보여줍니다.

### 12. ZIP 다운로드 임시 파일은 restart/응답 중단 시 남을 수 있음

위치:

- `app/main.py:737` `/api/fs/download`
- `app/main.py:742` `create_zip_archive(source)`
- `app/main.py:747` `BackgroundTask(cleanup_file, archive_path)`
- `app/main.py:1942` `create_zip_archive()`
- `app/main.py:1951` `sorted(source.rglob("*"))`

영향:

- 큰 폴더 다운로드는 `/config/downloads`에 ZIP을 먼저 만들어 원본만큼 추가 공간을 씁니다.
- container kill/restart 또는 background task 미실행 시 ZIP이 남을 수 있습니다.
- ZIP 생성은 `.part` 파일도 제외하지 않습니다.

이미 있는 방어:

- `/data` 전체 ZIP 다운로드는 막습니다.
- ZIP 생성 중 예외가 나면 temp zip을 삭제합니다.
- `tests/test_review_fixes.py`에 zip 실패 cleanup 테스트가 있습니다.

권장 수정:

- startup sweep으로 오래된 `/config/downloads/*.zip`을 삭제합니다.
- ZIP 대상 총 크기/파일 수 상한 또는 streaming zip을 검토합니다.
- `.part`, `.tmp`, sidecar 제외 옵션을 둡니다.

### 13. Hugging Face Hub cache 위치/정리 정책이 명시적이지 않음

위치:

- `app/downloader.py:1144` `configure_huggingface_runtime()`
- `app/downloader.py:4291` `hf_hub_download()`
- `app/downloader.py:4293` `snapshot_download()`

영향:

- `local_dir` 산출물과 별도로 Hugging Face 내부 cache가 컨테이너 writable layer 또는 `/config` 아래에 쌓일 수 있습니다.
- 운영자가 보는 `/data` 용량과 실제 볼륨 압박이 다를 수 있습니다.

권장 수정:

- 배포 환경에서 `HF_HOME`/`HF_HUB_CACHE`를 명시적으로 `/config/huggingface` 같은 볼륨으로 고정합니다.
- cache cleanup 절차와 주기 점검 명령을 문서화합니다.

### 14. 명시적 yt-dlp URL은 playlist/listing을 넓게 처리할 수 있음

위치:

- `app/parsers.py:213` 명시적 `yt-dlp` 입력에서 임의 HTTP URL 허용
- `app/downloader.py:3156` direct `yt_dlp_command()`
- `app/downloader.py:3372` xHamster에만 `--playlist-items 1` 기본 추가

영향:

- 사용자가 channel/playlist/listing URL을 명시적으로 넣으면 yt-dlp가 다수 entry를 조회/다운로드할 수 있습니다.
- `YT_DLP_EXTRA_OPTIONS`로 playlist 범위를 넓히면 총 요청량이 커집니다.

권장 수정:

- 기본 direct yt-dlp에도 `--playlist-items 1` 또는 `--no-playlist`를 기본으로 둘지 검토합니다.
- playlist 의도는 별도 UI 확인 또는 명령 prefix로만 허용합니다.

## Low Findings

### 15. ZIP 생성은 파일 목록 전체를 정렬해 메모리에 올림

위치:

- `app/main.py:1951` `for item in sorted(source.rglob("*"))`

영향:

- 큰 폴더 다운로드 하나가 파일 목록 전체 메모리화와 긴 동기 ZIP 생성을 유발합니다.

권장 수정:

- streaming generator 또는 정렬 없는 traversal로 변경합니다.
- 큰 폴더는 사전 size/file-count estimate 후 사용자 확인을 받습니다.

## 현재 방어 장치

- 큐 scheduler는 전역/공급자 동시 실행 제한을 적용합니다: `app/downloader.py:574`.
- provider별 랜덤 cooldown 설정이 있습니다: `app/downloader.py:612`, `app/downloader.py:922`.
- 자체 HTTP 요청은 host별 최소 간격과 job-control sleep을 거칩니다: `app/downloader.py:1044`, `app/downloader.py:1056`.
- 자체 HTTP retry는 429/5xx만 대상으로 하고 기본 3회입니다: `app/downloader.py:1099`.
- pause/delete 시 외부 process group terminate/kill 경로가 있습니다: `app/downloader.py:3664`, `app/downloader.py:3718`.
- partial 파일명은 job id와 URL hash를 포함합니다: `app/downloader.py:251`.
- 개별 job delete는 partial/local cleanup을 호출합니다: `app/main.py:431`.
- gallery-dl/yt-dlp 실패 후 빈 archive target cleanup이 있습니다: `app/downloader.py:3807`.
- workflow upload는 최대 크기 제한을 둡니다: `app/main.py:613`.
- ytdlp unsafe extra option 일부는 차단됩니다: `app/downloader.py:160`, `app/downloader.py:3454`.

## 운영 권장값

기본 운용:

- `MAX_CONCURRENT_DOWNLOADS=2` 또는 `3`
- `QUEUE_PER_PROVIDER_LIMIT=1`
- `QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS=2`
- `QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS=8`
- `DOWNLOAD_HTTP_MAX_RETRIES=3`
- `DOWNLOAD_REQUEST_MIN_INTERVAL_SECONDS=1.5` 이상
- `DOWNLOAD_STALL_TIMEOUT_SECONDS=600`, 큰 HF snapshot을 많이 받는 경우에는 오탐을 보고 0 또는 더 큰 값 검토

상대 서버 보호:

- Hitomi listing URL은 broad search/index/tag보다 작가+언어처럼 범위를 좁힌 URL을 우선 사용합니다.
- 대량 URL 입력은 처음에는 10~30개 단위로 시험한 뒤 늘립니다.
- 429/rate-limit가 보이면 즉시 provider cooldown과 `GALLERY_DL_SLEEP_REQUEST_SECONDS`를 올립니다.
- `YT_DLP_EXTRA_OPTIONS`/`GALLERY_DL_EXTRA_OPTIONS`에서 retry/playlist/sleep 관련 옵션은 요청량을 늘릴 수 있으므로 보수적으로 둡니다.

NAS 보호:

- 비디오 transcode가 많은 환경에서는 `/config/media-cache` 용량을 주기적으로 확인합니다.
- 큰 폴더 ZIP 다운로드는 `/config/downloads`에 원본만큼 추가 공간이 필요하다는 점을 전제로 합니다.
- `/data`에 수만 파일이 쌓이는 폴더는 library/media 화면 열람만으로도 I/O가 커질 수 있습니다.

## 운영 점검 명령

아래 명령은 삭제하지 않고 점검만 합니다.

```bash
du -sh /data /config /config/downloads /config/media-cache 2>/dev/null
find /config/downloads -type f -name '*.zip' -mtime +1 -print
find /config/media-cache -type f \( -name '*.play.mp4' -o -name '*.jpg' \) -mtime +30 -print
find /data -type f -name '*.job-*-*.part' -mtime +7 -print
sqlite3 /config/jobs.sqlite3 "select id,status,length(log),length(metadata_json),substr(input_text,1,120) from jobs order by length(log) desc limit 20;"
```

삭제 전 확인용 후보:

```bash
find /config/downloads -type f -name '*.zip' -mtime +1 -ls
find /config/media-cache -type f \( -name '*.play.mp4' -o -name '*.jpg' \) -mtime +30 -ls
find /data -type f -name '*.job-*-*.part' -mtime +7 -ls
```

SQLite 파일 크기가 계속 커지는 경우:

```bash
sqlite3 /config/jobs.sqlite3 "select count(*), sum(length(log)), sum(length(metadata_json)) from jobs;"
```

VACUUM은 앱이 멈춰 있거나 접근이 적은 시간에만 검토합니다. WAL/동시 접근 환경에서는 사전 백업 후 실행해야 합니다.

## 테스트 커버리지 현황

이미 커버되는 부분:

- partial path/job-scoped cleanup
- local cleanup이 `/data` 밖 또는 다른 job 참조를 피하는지
- gallery-dl 빈 archive cleanup
- 외부 process pause/delete stop
- zip 생성 실패 시 temp zip cleanup
- provider cooldown 기본/비활성/랜덤 동작
- bulk input 제한과 부분 실패 처리

부족한 부분:

- 대형 job log가 `/api/jobs` payload를 키우는 문제
- stdout queue backpressure/drop 정책
- 수만 파일 `directory_size()`/`rglob()` 비용
- media cache TTL/quota/invalidation
- `/api/jobs/clear` 전 partial cleanup
- zip response 중단/restart 후 temp sweep
- long `Retry-After`를 줄이지 않는 정책
- Hitomi listing/Civitai resource child job 상한
- 공격적 동시성/extra option 상한

## 추적용 작업 제안

1. API/DB 경량화: job summary endpoint에서 log 제외, 기존 log endpoint에 tail/offset 추가
2. Cleanup: media-cache/download zip/orphan partial startup sweep
3. External courtesy: Hitomi/Civitai child cap, Retry-After 존중 수정
4. Scan 비용: progress/watchdog/library size 계산 budget화
5. Transcode/ZIP 제한: semaphore/background job/크기 제한
6. 운영 설정 hard cap: UI/env validation과 warning 추가
