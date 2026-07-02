# 코드 검토 결과

검토일: 2026-06-30

상태: historical review record. 아래 지적 중 일부는 이후 구현에서 수정되었으므로 현재 동작은 코드와 [문서 인덱스](index.md)의 current reference 문서를 기준으로 확인한다.

범위:

- 백엔드 다운로드, 큐, 파일 시스템 안전성
- 프론트엔드 UI 상태와 API 연동
- Docker, Portainer, Synology/Ubuntu 배포 설정
- README, 패치내역, gallery-dl 인증 문서

검증 제한:

- 현재 Windows 개발 환경에는 `python`과 `docker`가 없어 앱 실행, Docker build, `docker compose config` 검증은 수행하지 못했습니다.
- 아래 내용은 정적 코드리뷰와 배포 파일 검토 기준입니다.
- `mockup-screenshots/` 미추적 폴더는 검토와 커밋 범위에서 제외했습니다.

참고:

- Portainer Git stack에서 compose build 제약은 Portainer 공식 FAQ의 설명을 참고했습니다. Git repository support에서는 docker-compose로 repository 내부 파일을 build하는 경로가 아직 완전히 구현되어 있지 않고, 이미지를 별도로 빌드한 뒤 compose에서 참조하는 방식을 권장합니다.
- Portainer FAQ: https://docs.portainer.io/faqs/troubleshooting/stacks-deployments-and-updates/can-i-build-an-image-while-deploying-a-stack-application-from-git

## 권장 수정 순서

1. 설정 모달에서 토큰과 인증값 실제값이 DOM에 노출되지 않도록 변경
2. symlink가 섞인 `/data` 경로에서 삭제, 이동, 이름 변경이 실제 대상 폴더를 조작하지 않도록 경로 처리 수정
3. queued/running 작업 보호가 `target_dir` 없는 작업도 막도록 개선
4. `.part` 파일을 job 또는 URL 단위로 분리하고 동시 다운로드 충돌 방지
5. Hugging Face와 gallery-dl 다운로드의 pause/delete/stall 반응성 개선
6. Portainer 배포 구조와 bind mount 권한 정책 정리
7. UI 상태 모순과 문서 불일치 정리

## High

### 1. 설정 모달이 토큰과 인증값을 DOM에 노출함

위치:

- `app/templates/index.html:275`
- `app/templates/index.html:281`
- `app/templates/index.html:313`
- `app/db.py:442`

내용:

- `HF_TOKEN`, `CIVITAI_TOKEN`, `GALLERY_DL_EXTRA_OPTIONS` 값이 input `value` 또는 textarea 본문으로 렌더링됩니다.
- `settings_status()`는 DB 값이 없으면 환경변수 값을 그대로 반환합니다.
- 사용자가 설정 모달을 연 뒤 저장하면 환경변수 secret이 UI 저장값으로 DB에 복제될 수 있습니다.

영향:

- 브라우저 DevTools 또는 페이지 소스에서 토큰이 보일 수 있습니다.
- 환경변수로만 관리하려던 secret이 `/config/jobs.sqlite3`에 저장될 수 있습니다.

권장 수정:

- 모든 secret 필드는 password와 동일하게 실제값을 내려주지 않고 placeholder만 표시합니다.
- 빈 값 제출은 기존 값을 유지하되, 별도 "삭제" 동작이 필요한 경우 명시적인 clear 체크박스나 버튼을 둡니다.
- `GALLERY_DL_EXTRA_OPTIONS`는 API Key/OAuth secret을 담을 수 있으므로 token과 동일하게 취급합니다.

### 2. 내부 symlink 조작 시 원본 대상 폴더가 삭제/이동될 수 있음

위치:

- `app/utils.py:48`
- `app/main.py:596`
- `app/main.py:292`

내용:

- `safe_join()`이 요청 경로를 `resolve()`한 결과를 반환합니다.
- `/data/a/link -> /data/b` 같은 내부 symlink가 있을 때 UI에서 `a/link`를 삭제하면 링크가 아니라 실제 대상 `/data/b`가 조작될 수 있습니다.

영향:

- Synology 모델 폴더에 symlink가 있으면 의도치 않은 원본 삭제, 이동, 이름 변경으로 이어질 수 있습니다.

권장 수정:

- 사용자 입력 경로 검증과 실제 조작 경로를 분리합니다.
- 삭제/이동/이름 변경에서는 symlink 자체를 조작하거나, symlink 조작을 명시적으로 차단합니다.
- `resolve()`로 root 내부 여부를 확인하더라도 반환값은 원래 경로의 lexical path를 유지하는 방식으로 재설계합니다.

### 3. `target_dir` 없는 active 작업을 폴더 보호가 놓침

위치:

- `app/db.py:340`
- `app/main.py:241`
- `app/downloader.py:590`
- `app/downloader.py:771`

내용:

- `has_active_jobs_under()`는 `target_dir IS NOT NULL`인 작업만 검사합니다.
- queued 작업과 metadata fetch 중인 running 작업은 아직 `target_dir`가 없어 삭제/이동/이름 변경 보호에 걸리지 않습니다.

영향:

- 사용자가 작업을 큐에 넣은 뒤 기본 라우트 폴더를 삭제할 수 있습니다.
- 작업이 나중에 시작되면서 삭제된 경로를 재생성하거나, 이동된 폴더가 아닌 이전 경로에 계속 쓸 수 있습니다.

권장 수정:

- 작업 생성 시점에 예상 target root를 저장하거나, parsed payload와 현재 route 설정으로 active job의 예상 경로를 계산합니다.
- active status 보호에는 `queued`, `running`, `paused`, `pausing`, `deleting` 작업의 예상 경로도 포함합니다.

### 4. `.part` 파일이 job/url 단위가 아니라 파일명 단위라 충돌 가능

위치:

- `app/downloader.py:1550`
- `app/downloader.py:1553`
- `app/downloader.py:1603`
- `app/main.py:223`

내용:

- partial 파일명이 `{filename}.part` 하나로만 정해집니다.
- job ID, URL hash, source provider가 partial 파일명에 포함되지 않습니다.
- paused 작업을 delete하면 DB만 지워지고 `.part`가 남을 수 있습니다.

영향:

- 같은 폴더와 파일명으로 다른 URL을 다운로드하면 잘못 resume할 수 있습니다.
- `QUEUE_PER_PROVIDER_LIMIT > 1`에서 같은 이름의 다운로드가 동시에 실행되면 서로 같은 `.part`에 쓰거나 최종 파일이 손상될 수 있습니다.

권장 수정:

- partial 파일명을 `{filename}.job-{job_id}.part` 또는 URL hash 포함 방식으로 분리합니다.
- 같은 최종 파일 경로에 대한 다운로드 lock을 둡니다.
- 삭제 시 paused 작업의 partial 파일도 찾을 수 있도록 job metadata에 partial path를 저장합니다.

### 5. Hugging Face 다운로드가 pause/delete/stall에 즉시 반응하지 않음

위치:

- `app/downloader.py:623`
- `app/downloader.py:625`
- `app/downloader.py:632`
- `app/downloader.py:413`

내용:

- `hf_hub_download()`와 `snapshot_download()` 호출 중에는 앱의 `check_job_control()`이 실행되지 않습니다.
- stall watchdog이 `pausing`으로 상태를 바꿔도 라이브러리 호출이 끝나기 전까지 다운로드는 계속 진행됩니다.

영향:

- 큰 snapshot 다운로드에서 사용자가 정지/삭제를 눌러도 NAS 용량이 계속 줄어들 수 있습니다.
- 실행 슬롯도 계속 점유됩니다.

권장 수정:

- HF snapshot은 가능하면 파일 목록을 가져와 앱의 `stream_download()` 흐름으로 내려받거나, 다운로드 프로세스를 별도 process로 분리해 종료 가능하게 합니다.
- 최소한 UI에는 Hugging Face snapshot 정지/삭제가 즉시 중단이 아니라 "중단 요청"임을 표시합니다.

### 6. Portainer Repository 배포와 `build: .` 조합이 실패할 수 있음

위치:

- `portainer-stack.yml:10`
- `README.md:61`
- `README.md:120`

내용:

- README는 Portainer `Repository` 방식 배포를 권장합니다.
- `portainer-stack.yml`은 `build.context: .` 방식입니다.
- Portainer 공식 FAQ는 Git repository support에서 docker-compose build가 완전히 구현된 경로가 아니며, 이미지를 별도로 빌드한 뒤 compose에서 참조하는 방식을 안내합니다.

영향:

- Portainer에서 Git stack 배포 중 이미지 빌드 단계가 실패하거나 `pull access denied`류 오류가 날 수 있습니다.

권장 수정:

- 배포 흐름을 둘 중 하나로 명확히 분리합니다.
- 선택지 A: GitHub Actions 또는 수동 build로 이미지를 registry에 push하고, Portainer stack은 `image:`를 참조합니다.
- 선택지 B: Portainer가 build를 지원하는 환경에서만 `build:` stack을 쓰도록 문서를 별도 경로로 분리합니다.

## Medium

### 7. gallery-dl 정지/삭제가 child process를 남길 수 있음

위치:

- `app/downloader.py:1188`
- `app/downloader.py:1233`

내용:

- `run_gallery_dl_process()`는 parent process에만 `terminate()` 또는 `kill()`을 보냅니다.
- gallery-dl이 외부 downloader 또는 child process를 만들면 child가 계속 파일을 쓸 수 있습니다.

권장 수정:

- Linux 컨테이너에서는 process group/session 단위로 종료합니다.
- Windows 개발 환경도 고려한다면 platform별 process group 처리를 분기합니다.

### 8. 컨테이너가 root로 bind mount에 씀

위치:

- `Dockerfile:46`
- `Dockerfile:51`
- `portainer-stack.yml:61`

내용:

- Dockerfile에 별도 `USER`, PUID/PGID, umask, chown 흐름이 없습니다.
- `/data`, `/config` bind mount에 SQLite DB와 다운로드 파일을 root 권한으로 쓸 수 있습니다.

영향:

- Synology DSM, ComfyUI, 일반 Ubuntu 사용자와 파일 소유권 충돌이 날 수 있습니다.
- NAS UI에서 파일 삭제/수정이 어려울 수 있습니다.

권장 수정:

- `PUID`, `PGID`, `UMASK` 기반 entrypoint 또는 compose `user:` 옵션을 지원합니다.
- README에 Synology/Ubuntu 권한 설정 예시를 추가합니다.

### 9. `deleting` 상태에서도 삭제 버튼이 활성화됨

위치:

- `app/templates/index.html:1021`
- `app/templates/index.html:1027`
- `app/main.py:223`

내용:

- 실행 중 작업 삭제 시 서버는 `running/pausing`을 `deleting`으로 바꿉니다.
- UI는 `deleting` 상태에서도 삭제 버튼을 계속 활성화합니다.
- 두 번째 DELETE는 `deleting`을 active stop request로 보지 않고 `db.delete_job()`로 기록을 바로 삭제합니다.

영향:

- 백그라운드 작업이 완전히 멈추기 전에 작업 기록과 로그가 사라질 수 있습니다.

권장 수정:

- `deleting` 상태에서는 삭제 버튼을 disabled 처리합니다.
- API에서도 `deleting` 상태에 대한 DELETE는 no-op으로 응답합니다.

### 10. 라이브러리 카드가 완료된 작업만 표시하지 않음

위치:

- `app/templates/index.html:1077`
- `app/templates/index.html:1094`
- `app/templates/index.html:1165`

내용:

- 라이브러리 카드 렌더링이 `currentJobs` 전체를 대상으로 합니다.
- `queued`, `running`, `failed`, `target_path` 없는 작업도 카드가 될 수 있습니다.

영향:

- 아직 파일이 없는 작업이 카드처럼 보일 수 있습니다.
- 즐겨찾기, 우클릭, 다운로드 액션이 실제 파일 상태와 모순됩니다.

권장 수정:

- 라이브러리 카드는 `status === "done"`이고 `target_path`가 있으며 실제 파일 시스템 경로가 존재하는 항목만 표시합니다.
- 실패/진행 중 작업은 작업 목록에서만 다룹니다.

### 11. ZIP 다운로드 큐가 실제 ZIP 생성 완료를 확인하지 않음

위치:

- `app/templates/index.html:1737`
- `app/templates/index.html:1754`
- `app/templates/index.html:1763`
- `app/main.py:439`

내용:

- 프론트 다운로드 큐는 `/api/fs/download-info`만 확인한 뒤 iframe 다운로드를 시작합니다.
- 1.2초 뒤 무조건 `done`으로 표시합니다.
- 실제 ZIP 생성은 `/api/fs/download` 요청 안에서 수행됩니다.

영향:

- 큰 폴더 ZIP 생성이 끝나기 전 UI가 완료로 표시됩니다.
- 여러 폴더를 연속 다운로드하면 서버 부하와 UI 상태가 어긋납니다.

권장 수정:

- 다운로드 준비 API를 별도로 만들어 ZIP 생성 job을 만들고 완료 후 URL을 반환합니다.
- 또는 UI 문구를 "브라우저 다운로드 요청 완료"로 낮추고 실제 완료처럼 보이지 않게 합니다.

### 12. ZIP 생성 실패 시 임시 파일이 남을 수 있음

위치:

- `app/main.py:731`
- `app/main.py:738`
- `app/main.py:440`

내용:

- `create_zip_archive()`가 `mkstemp()`로 `/config/downloads/*.zip`을 만든 뒤 압축 중 오류가 나면 cleanup이 없습니다.
- 성공 응답일 때만 `BackgroundTask(cleanup_file)`이 붙습니다.

영향:

- 권한 오류, 읽기 실패, 중간 예외가 반복되면 `/config` 볼륨이 찰 수 있습니다.

권장 수정:

- `create_zip_archive()` 내부에서 try/except로 실패 시 archive path를 삭제합니다.
- 가능하면 `ZIP_DEFLATED`와 크기 제한/진행 상태도 검토합니다.

### 13. 배포 기본 경로가 문서와 compose에서 서로 다름

위치:

- `docker-compose.yml:45`
- `portainer-stack.yml:61`
- `README.md:55`

내용:

- `docker-compose.yml`은 `/volume1/AI_MODELS:/data`를 사용합니다.
- README와 Portainer stack은 `/volume1/docker/nas-model-archiver/models:/data`를 권장합니다.

영향:

- 설치자가 어떤 compose 파일을 쓰느냐에 따라 모델 저장 위치가 달라집니다.
- Ubuntu PC에서는 `/volume1/...` 기본값 자체가 부적절할 수 있습니다.

권장 수정:

- Synology용 stack과 Ubuntu/local compose를 분리합니다.
- README에서 두 경로를 명확히 나눠 설명합니다.

### 14. gallery-dl 자동 업데이트가 재현성을 낮춤

위치:

- `docker-entrypoint.sh:4`
- `docker-entrypoint.sh:6`
- `requirements.txt:7`

내용:

- 이미지 빌드 때 gallery-dl을 설치하고, 컨테이너 시작 때 다시 `pip install --upgrade`를 수행합니다.
- 재시작 시점의 PyPI 상태에 따라 실제 실행 버전이 달라질 수 있습니다.

영향:

- 오프라인 NAS에서는 업데이트 시도가 실패한 뒤 bundled 버전으로 실행됩니다.
- 문제 재현 시 "어떤 gallery-dl 버전으로 받았는지"가 불명확해질 수 있습니다.

권장 수정:

- 시작 로그와 job metadata에 gallery-dl 버전을 남깁니다.
- 기본값을 자동 업데이트 on으로 유지하더라도, 운영 문서에 재현성 tradeoff를 명시합니다.

## Low

### 15. Civitai 카드 배지가 `NAI`로 표시됨

위치:

- `app/templates/index.html:1825`

내용:

- `shortSource("civitai")`가 `NAI`를 반환합니다.

권장 수정:

- Civitai는 `CIV` 또는 `CV`로 표시합니다.

### 16. gallery-dl 표준 config 파일이 무시됨

위치:

- `app/downloader.py:1120`
- `README.md:237`
- `docs/gallery-dl-auth.md:20`

내용:

- gallery-dl은 `--config-ignore`로 실행됩니다.
- 사용자가 표준 gallery-dl config 파일을 `/config` 등에 넣어도 적용되지 않습니다.

권장 수정:

- 문서에 "이 앱은 UI/env에서 만든 CLI 옵션만 사용한다"고 명확히 씁니다.
- 또는 명시적인 config file path 옵션을 추가합니다.

### 17. 즐겨찾기/메모 prefix 삭제 쿼리가 LIKE wildcard를 escape하지 않음

위치:

- `app/db.py:324`
- `app/db.py:332`

내용:

- `_` 또는 `%`가 포함된 경로를 삭제할 때 LIKE wildcard로 해석될 수 있습니다.

영향:

- 실제 파일 삭제는 아니지만 즐겨찾기/메모 메타데이터가 과하게 삭제될 수 있습니다.

권장 수정:

- LIKE escape를 적용하거나, prefix 비교를 Python에서 수행합니다.

### 18. 모바일 상단 고정 버튼이 헤더와 겹칠 수 있음

위치:

- `app/static/style.css:2109`
- `app/static/style.css:2229`
- `app/static/style.css:2393`

내용:

- 모바일에서 `.top-corner-actions`가 fixed로 유지됩니다.
- 작업/라이브러리 섹션의 상단 여백이 버튼 높이보다 작아 겹칠 수 있습니다.

권장 수정:

- 모바일 탭별로 상단 여백을 키우거나, top-corner-actions를 현재 탭 컨텍스트 안으로 이동합니다.

### 19. Browser Cookies 설정은 기본 compose mount만으로 동작하지 않음

위치:

- `app/templates/index.html:303`
- `docker-compose.yml:44`
- `portainer-stack.yml:59`

내용:

- UI는 `gallery-dl Browser Cookies` 입력을 제공하지만, 기본 compose/stack은 브라우저 프로필을 컨테이너에 마운트하지 않습니다.

권장 수정:

- 문서에서 Browser Cookies는 고급 옵션이며 별도 profile mount가 필요하다고 강조합니다.
- 일반 사용자는 Cookies File 사용을 권장합니다.

### 20. CRLF/LF 혼재 관리 필요

위치:

- `Dockerfile`
- `docker-compose.yml`
- `portainer-stack.yml`
- `README.md`

내용:

- Windows 개발 환경에서 파일별 줄바꿈이 섞일 수 있습니다.
- 현재 `docker-entrypoint.sh`는 LF 상태라 즉시 문제는 보이지 않습니다.

권장 수정:

- `.gitattributes`로 shell script는 LF를 강제합니다.

```gitattributes
*.sh text eol=lf
Dockerfile text eol=lf
*.yml text eol=lf
*.yaml text eol=lf
```
