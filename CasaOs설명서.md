# OpenAI Codex용 HugCivi CasaOS 설치 운영 지시서

## 0. 시작 프롬프트: 점검 질문과 설치 허가

Codex는 이 문서를 받으면 CasaOS/SSH 접속, 읽기 전용 점검, 설치 또는 변경 명령을 시작하기 전에 먼저 아래 질문을 사용자에게 던지고 답을 기다린다. 사용자가 명시적으로 `점검 진행`, `설치 진행`, `승인`, `OK`처럼 허가하기 전에는 Portainer 설치, CasaOS Custom Install 배포, `docker pull`, `docker compose up`, `mkdir`, `chown`, `chmod`, container/volume 삭제, 앱 삭제 옵션 변경 같은 작업을 하지 않는다.

첫 응답은 아래처럼 시작한다.

```markdown
HugCivi를 CasaOS 환경에 설치하거나 업데이트하기 전에 대상 시스템과 허가 범위를 확인하겠습니다.

1. 대상 CasaOS 서버의 접속 방식은 무엇인가요? CasaOS URL, SSH 주소, 사용자명, sudo 가능 여부를 알려주세요.
2. Portainer가 이미 설치되어 있나요? 모르면 제가 읽기 전용 명령으로 확인해도 될까요?
3. Portainer가 없으면 CasaOS Custom Install/Compose를 기본으로 진행할까요, 아니면 Portainer 설치를 원하시나요?
4. 기존 HugCivi 데이터가 있나요? 있으면 `/data`와 `/config`로 쓸 host path를 알려주세요. 기본값은 `/DATA/HugCivi/models`, `/DATA/AppData/hugcivi/config`입니다.
5. HugCivi 외부 접속 포트는 기본 `8088`로 진행할까요? CasaOS 앱 포트, 리버스 프록시, 공유기/방화벽 조건이 있나요?
6. 관리자 ID는 기본 `admin`으로 둘까요? `APP_PASSWORD`는 직접 제공할까요, 제가 생성할까요?
7. 컨테이너 파일 소유자는 현재 CasaOS 사용자 또는 `1000:1000` 중 무엇으로 둘까요? 기존 데이터가 있으면 재귀 `chown`은 별도 승인 후에만 진행합니다.
8. 사용할 이미지 태그는 `ghcr.io/mephiblin/hugcivi:latest`인가요, 특정 `sha-...` 태그인가요?
9. 위 답변을 받은 뒤 먼저 읽기 전용 점검 명령만 실행해도 될까요?
```

읽기 전용 점검 승인을 받으면 1, 4, 5단계의 상태 확인 명령을 실행하고 결과를 요약한다. 점검 승인과 설치 승인은 별개로 취급한다. 점검 뒤에는 다음 형식으로 다시 허가를 받는다.

```markdown
점검 결과를 요약하면 다음과 같습니다.

- Portainer 상태:
- CasaOS/Docker/Compose 상태:
- HugCivi data/config 경로:
- 포트와 접근 경로:
- 기존 데이터 보존 주의점:

아래 계획으로 설치/업데이트를 진행해도 될까요?

- 선택한 배포 방식:
- 사용할 이미지:
- 만들거나 수정할 경로:
- 실행할 변경 명령:
- 변경하지 않을 항목:
```

설치 또는 업데이트는 사용자가 두 번째 허가에 답한 뒤에만 진행한다. 허가가 모호하면 작업 범위를 좁혀 다시 묻고, 데이터 삭제나 대량 권한 변경은 항상 별도 확인을 받는다.

## 1. 첫 단계: Portainer 설치 여부 점검

Codex는 이 문서를 받으면 다른 설치 작업보다 먼저 CasaOS 서버에 Portainer가 이미 설치되어 있는지 확인한다. Docker 권한 오류가 나면 같은 명령을 `sudo`로 재시도한다.

```bash
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' | grep -Ei 'portainer|9000|9443' || true
docker volume ls | grep -Ei 'portainer' || true
ss -ltnp | grep -E ':(9000|9443)\b' || true
curl -kfsS https://127.0.0.1:9443/api/status || curl -fsS http://127.0.0.1:9000/api/status || true
```

판단 기준:

- Portainer 컨테이너가 실행 중이고 `9443` 또는 `9000` 상태 API가 응답하면 배포 방식 A, Portainer Stack을 우선 사용한다.
- Portainer 흔적은 있으나 중지, 포트 충돌, 볼륨만 남은 상태라면 상태를 보고하고 복구할지 CasaOS fallback으로 갈지 사용자에게 확인한다.
- Portainer가 없으면 사용자의 의도와 운영 난이도를 기준으로 선택한다. 단순히 HugCivi만 올리려는 경우는 배포 방식 B, CasaOS Custom Install/Compose를 기본값으로 둔다. Stack 관리, registry credential, healthcheck, 재배포 이력이 필요하면 Portainer 별도 설치를 제안하고 승인 후 설치한다.
- Portainer 설치 자체는 HugCivi 데이터와 별개 작업이다. 사용자의 명시 승인 없이 Portainer를 새로 설치하지 않는다.

## 2. 목표와 전제

목표는 Codex가 CasaOS 기반 Docker 호스트에 HugCivi를 안전하게 배포하고, 접속 가능한 상태와 영구 저장소 보존 여부를 검증한 뒤 결과를 보고하는 것이다. 이 문서는 사람이 화면을 보며 따라 하는 설명서가 아니라 Codex에게 운영 작업을 맡길 때 쓰는 지시서다.

전제:

- CasaOS 서버에 SSH 또는 터미널 접근 권한이 있다.
- HugCivi 이미지는 `ghcr.io/mephiblin/hugcivi:latest`를 사용한다.
- HugCivi는 컨테이너 내부 `/data`에 archive 파일을, `/config`에 SQLite DB와 설정을 저장한다.
- 기본 CasaOS 경로는 `/DATA/HugCivi/models`와 `/DATA/AppData/hugcivi/config`를 사용하되, 사용자가 다른 디스크나 경로를 지정하면 그 경로를 우선한다.
- 이미지는 일반적으로 `amd64`와 `arm64` 장비를 대상으로 한다. `armv7` 같은 32-bit 장비가 확인되면 배포 전 호환성 위험을 보고한다.

## 3. 금지사항

- 사용자 확인 없이 `/DATA`, `/data`, `/config`, `/DATA/HugCivi`, `/DATA/AppData/hugcivi`, HugCivi archive, SQLite DB, Portainer volume, Docker volume을 삭제하지 않는다.
- `docker compose down -v`, `docker rm -v`, `docker volume rm`, `docker volume prune`, `docker system prune --volumes`, 확인되지 않은 `rsync --delete`, `rm -rf`, CasaOS 앱 삭제 옵션 중 persistent data 제거 옵션은 사용자가 정확한 대상과 백업 상태를 확인하기 전까지 실행하지 않는다.
- `/DATA` 전체에 대한 `chown -R`은 금지한다. 권한 변경은 확인된 HugCivi data/config 경로에만 제한한다.
- 기존 HugCivi 컨테이너가 같은 `/config/jobs.sqlite3`를 사용 중이면 새 컨테이너를 동시에 띄우지 않는다.
- 기존 폴더가 비어 있지 않으면 사용자 확인 없이 재귀 `chown`이나 대량 권한 변경을 하지 않는다.
- `APP_PASSWORD`, 토큰, 쿠키 파일 내용, registry credential을 repo 파일이나 공개 로그에 저장하지 않는다.
- 예시 비밀번호, 빈 비밀번호, 짧은 비밀번호로 배포하지 않는다. HugCivi는 insecure placeholder를 거부할 수 있다.

## 4. Codex가 먼저 수집할 정보

Portainer 점검 후 아래 정보를 수집하고, 불확실한 값은 사용자에게 짧게 확인한다.

```bash
hostname -I || true
uname -m
cat /etc/os-release 2>/dev/null || true
id
docker version
docker compose version || docker-compose version || true
docker info --format 'Server={{.ServerVersion}} Driver={{.Driver}} Cgroup={{.CgroupDriver}} Root={{.DockerRootDir}}' || true
df -h /DATA / 2>/dev/null || df -h
ss -ltnp | grep -E ':(8088|9000|9443)\b' || true
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

확인할 운영 값:

- HugCivi 접속 포트: 기본 `8088`
- HugCivi 관리자 ID: 기본 `admin`
- `/data` host path: 기본 `/DATA/HugCivi/models`
- `/config` host path: 기본 `/DATA/AppData/hugcivi/config`
- 실행 UID/GID: 기본은 현재 CasaOS 사용자 또는 `1000:1000`
- `APP_PASSWORD`: 사용자가 제공하거나 Codex가 생성
- 선택 배포 방식: Portainer Stack 또는 CasaOS Custom Install/Compose

## 5. Docker 점검

Docker가 실행 중인지, Compose가 사용 가능한지, GHCR 이미지를 받을 수 있는지 확인한다.

```bash
docker ps >/dev/null
docker compose version || docker-compose version
docker pull ghcr.io/mephiblin/hugcivi:latest
```

`docker pull`이 실패하면 다음을 분리해서 판단한다.

- 네트워크/DNS 문제
- GHCR 접근 또는 인증 문제
- CPU architecture 불일치
- CasaOS 또는 Docker daemon 상태 문제

이미지 pull 실패 상태에서 Compose 배포를 계속 진행하지 않는다.

## 6. 폴더와 권한 점검

기본 경로를 사용할 때의 점검 명령이다. 사용자가 다른 경로를 지정하면 변수만 바꿔서 실행한다.

```bash
DATA_DIR=/DATA/HugCivi/models
CONFIG_DIR=/DATA/AppData/hugcivi/config
APP_UID=$(id -u)
APP_GID=$(id -g)

sudo mkdir -p "$DATA_DIR" "$CONFIG_DIR"
ls -ld "$DATA_DIR" "$CONFIG_DIR"
find "$DATA_DIR" "$CONFIG_DIR" -maxdepth 1 -mindepth 1 -print -quit
```

새 폴더이거나 비어 있는 폴더라면 소유권을 실행 사용자에 맞춘다.

```bash
sudo chown -R "$APP_UID:$APP_GID" "$DATA_DIR" "$CONFIG_DIR"
sudo chmod 755 "$DATA_DIR" "$CONFIG_DIR"
```

기존 데이터가 있으면 먼저 용량과 소유권만 보고한다.

```bash
du -sh "$DATA_DIR" "$CONFIG_DIR" 2>/dev/null || true
find "$DATA_DIR" "$CONFIG_DIR" -maxdepth 1 -printf '%u:%g %m %p\n' 2>/dev/null | head -50
```

권한 문제가 예상될 때는 다음 순서로 처리한다.

1. 기존 데이터가 새 설치 대상인지 사용자에게 확인한다.
2. 필요한 경우에만 `PUID`, `PGID`를 실제 소유자에 맞춘다.
3. 그래도 쓰기 실패가 나면 `HUGCIVI_CHOWN_ON_START=1`을 한 번만 쓰는 방안을 제안한다.
4. 대량 `chown`은 대상과 백업 상태를 확인한 뒤 실행한다.

## 7. APP_PASSWORD 준비

`APP_PASSWORD`는 배포 전에 반드시 준비한다. 사용자가 직접 지정하지 않았다면 Codex가 서버에서 생성한다.

```bash
openssl rand -hex 32
```

운영 규칙:

- 생성한 비밀번호는 Compose에만 넣고 repo 파일에는 저장하지 않는다.
- 최종 보고에는 비밀번호 전체를 반복 노출하지 않는다. 사용자가 별도 전달을 요구하면 그 방식에 맞춘다.
- `admin/admin`, `password`, `changeme`, `REPLACE_ME`, 빈 값 같은 placeholder는 사용하지 않는다.

## 8. 배포 방식 A: Portainer Stack

Portainer가 정상 동작하면 Stack 배포를 우선한다. Codex가 Portainer UI 또는 API 접근 권한을 가지고 있으면 직접 배포하고, 인증이나 2FA 때문에 직접 조작할 수 없으면 정확한 Stack 내용과 환경값을 사용자에게 전달한 뒤 확인을 받아 계속 검증한다.

Portainer Stack 원칙:

- Stack 이름은 `hugcivi`로 둔다.
- `build:`가 아니라 `image: ghcr.io/mephiblin/hugcivi:latest`를 사용한다.
- `/data`와 `/config`는 bind mount로 고정한다.
- Stack 환경 변수나 Compose 내용에 `APP_PASSWORD`, `PUID`, `PGID`, `HUGCIVI_DATA_DIR`, `HUGCIVI_CONFIG_DIR`, `HUGCIVI_HTTP_PORT`를 명확히 둔다.
- 기존 HugCivi Stack이 있으면 먼저 현재 mount와 DB 위치를 확인하고, 업데이트인지 신규 설치인지 사용자에게 확인한다.

권장 Portainer 환경값:

```text
HUGCIVI_IMAGE=ghcr.io/mephiblin/hugcivi:latest
HUGCIVI_HTTP_PORT=8088
APP_USERNAME=admin
APP_PASSWORD=<long-secret>
PUID=1000
PGID=1000
UMASK=022
HUGCIVI_CHOWN_ON_START=0
HUGCIVI_DATA_DIR=/DATA/HugCivi/models
HUGCIVI_CONFIG_DIR=/DATA/AppData/hugcivi/config
MAX_CONCURRENT_DOWNLOADS=2
QUEUE_PER_PROVIDER_LIMIT=1
QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS=2
QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS=5
DOWNLOAD_STALL_TIMEOUT_SECONDS=600
INTERNAL_JOB_MAX_CONCURRENT=1
DOWNLOAD_REQUEST_MIN_INTERVAL_SECONDS=1.5
GALLERY_DL_SLEEP_REQUEST_SECONDS=2
YT_DLP_FORMAT=best[ext=mp4][vcodec^=avc1]/best[ext=mp4]/best
```

Portainer Stack Compose는 이 문서의 Compose 예시를 쓰거나 repo의 `portainer-stack.yml`을 CasaOS 경로에 맞춰 적용한다.

## 9. 배포 방식 B: CasaOS Custom Install fallback

Portainer가 없고 사용자가 별도 Portainer 설치를 원하지 않으면 CasaOS 기본 Custom Install 또는 Compose import 방식으로 배포한다.

Codex의 판단 기준:

- 단일 앱 설치, 간단한 재배포, CasaOS UI만 쓰는 환경이면 Custom Install fallback을 선택한다.
- 여러 Stack 관리, registry credential 관리, healthcheck와 업데이트 이력을 Portainer에서 보고 싶다는 요구가 있으면 Portainer 설치를 제안한다.
- 사용자가 "판단해서 진행"이라고 맡겼고 Portainer 운영 이유가 뚜렷하지 않으면 CasaOS Custom Install fallback으로 진행한다.

CasaOS fallback 원칙:

- CasaOS가 Compose 변수 치환을 제한할 수 있으므로 가능한 한 실제 값이 들어간 Compose를 사용한다.
- `APP_PASSWORD`, 경로, UID/GID, 포트는 배포 전 확정한다.
- CasaOS UI를 Codex가 직접 조작할 수 없으면 Compose 전체를 사용자에게 전달하고, 사용자의 설치 완료 신호를 받은 뒤 검증 명령으로 이어간다.

## 10. 배포 방식 C: Portainer 별도 설치 승인 시

Portainer가 없고 사용자가 CasaOS에도 Portainer를 설치해 관리하겠다고 명시 승인한 경우에만 진행한다. 기존 Portainer 흔적이나 `portainer_data` volume이 있으면 새로 만들지 말고 먼저 복구 또는 재사용 가능성을 보고한다.

```bash
docker volume ls | grep -Ei 'portainer' || true
docker ps -a --filter name=portainer --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' || true
```

승인 후 기본 설치:

```bash
docker volume create portainer_data
docker run -d \
  -p 8000:8000 \
  -p 9443:9443 \
  --name portainer \
  --restart=always \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v portainer_data:/data \
  portainer/portainer-ce:sts
```

검증:

```bash
docker ps --filter name=portainer --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
curl -kfsS -I https://127.0.0.1:9443/ || true
```

접속 주소는 `https://<CasaOS_IP>:9443`이다. Portainer 설치가 완료되면 배포 방식 A로 돌아가 HugCivi Stack을 만든다.

## 11. Compose 예시

아래 예시는 CasaOS 기본 경로와 약한 NAS 또는 미니 PC에 맞춘 보수적인 값이다. 배포 전에 `APP_PASSWORD`, `PUID`, `PGID`, 경로, 포트를 실제 환경에 맞춘다.

```yaml
name: hugcivi

services:
  hugcivi:
    image: ghcr.io/mephiblin/hugcivi:latest
    container_name: hugcivi
    restart: unless-stopped
    ports:
      - "8088:8088"
    environment:
      TZ: "Asia/Seoul"
      APP_USERNAME: "admin"
      APP_PASSWORD: "<long-secret>"
      PUID: "1000"
      PGID: "1000"
      UMASK: "022"
      HUGCIVI_CHOWN_ON_START: "0"

      HF_TOKEN: ""
      CIVITAI_TOKEN: ""

      LIBRARY_ACTIVE: "ComfyUI"
      ROUTE_LLM_ROOT: "huggingface/llm"
      ROUTE_LORA_ROOT: "stable-diffusion/loras"
      ROUTE_CHECKPOINT_ROOT: "stable-diffusion/checkpoints"
      ROUTE_DIFFUSION_MODEL_ROOT: "stable-diffusion/diffusion_models"
      ROUTE_EMBEDDING_ROOT: "stable-diffusion/embeddings"
      ROUTE_VAE_ROOT: "stable-diffusion/vae"
      ROUTE_CONTROLNET_ROOT: "stable-diffusion/controlnet"
      ROUTE_UPSCALER_ROOT: "stable-diffusion/upscalers"

      HITOMI_BACKEND: "auto"
      GALLERY_DL_AUTO_UPDATE: "1"
      GALLERY_DL_UPDATE_SPEC: "gallery-dl<2.0"
      HUGCIVI_STARTUP_CONFIG_FILE: "/config/startup.env"
      GALLERY_DL_SLEEP_REQUEST_SECONDS: "2"
      GALLERY_DL_USERNAME: ""
      GALLERY_DL_PASSWORD: ""
      GALLERY_DL_COOKIES_FILE: ""
      GALLERY_DL_COOKIES_FROM_BROWSER: ""
      GALLERY_DL_EXTRA_OPTIONS: ""
      YT_DLP_COOKIES_FILE: ""
      YT_DLP_COOKIES_FROM_BROWSER: ""
      YT_DLP_FORMAT: "best[ext=mp4][vcodec^=avc1]/best[ext=mp4]/best"
      YT_DLP_EXTRA_OPTIONS: ""

      MAX_CONCURRENT_DOWNLOADS: "2"
      QUEUE_PER_PROVIDER_LIMIT: "1"
      QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS: "2"
      QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS: "5"
      DOWNLOAD_STALL_TIMEOUT_SECONDS: "600"
      INTERNAL_JOB_MAX_CONCURRENT: "1"
      HF_XET_HIGH_PERFORMANCE: "0"
      HF_XET_NUM_CONCURRENT_RANGE_GETS: "4"
      HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY: "1"
      DOWNLOAD_REQUEST_MIN_INTERVAL_SECONDS: "1.5"
      DOWNLOAD_HTTP_MAX_RETRIES: "3"
      DOWNLOAD_RETRY_BACKOFF_SECONDS: "5"
      DOWNLOAD_MAX_RETRY_SLEEP_SECONDS: "300"
      DOWNLOAD_ENABLE_HEAD_REQUESTS: "1"
    volumes:
      - type: bind
        source: /DATA/HugCivi/models
        target: /data
      - type: bind
        source: /DATA/AppData/hugcivi/config
        target: /config
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS -u \"$${APP_USERNAME}:$${APP_PASSWORD}\" http://127.0.0.1:8088/ >/dev/null || exit 1"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 30s
```

선택 토큰이나 쿠키 파일은 설치 후 웹 UI 설정에서 저장하는 것을 우선한다. 환경변수로 넣어야 한다면 secret 값이 최종 보고나 shell history에 남지 않게 주의한다.

## 12. 배포 후 검증

배포 직후 컨테이너, 로그, HTTP, 볼륨 쓰기 상태를 확인한다.

```bash
docker ps --filter name=hugcivi --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
CONTAINER=$(docker ps --filter name=hugcivi --format '{{.Names}}' | head -n1)
docker logs --tail=120 "$CONTAINER"
docker exec "$CONTAINER" sh -lc 'id && ls -ld /data /config && test -w /data && test -w /config'
```

HTTP 검증은 password가 shell history에 남지 않도록 환경변수로 처리한다.

```bash
APP_USERNAME=admin
APP_PASSWORD='<long-secret>'
curl -fS -u "${APP_USERNAME}:${APP_PASSWORD}" http://127.0.0.1:8088/ >/tmp/hugcivi-home.html
grep -Ei 'HugCivi|작업|library|라이브러리' /tmp/hugcivi-home.html | head
```

외부 접속 주소도 확인한다.

```text
http://<CasaOS_IP>:8088
```

검증 성공 기준:

- 컨테이너가 `Up` 상태다.
- 로그에 `APP_PASSWORD` 오류, bind mount 쓰기 오류, DB 초기화 오류가 없다.
- `/data`와 `/config`가 컨테이너 내부에서 쓰기 가능하다.
- Basic Auth로 홈 화면이 `200 OK`를 반환한다.
- Portainer를 사용했다면 Stack healthcheck가 healthy 또는 starting 후 healthy로 바뀐다.

## 13. 업데이트, 이관, 문제 해결

업데이트 절차:

1. 현재 `/data`와 `/config` bind mount 경로를 확인한다.
2. `/config/jobs.sqlite3`를 백업한다. 가능하면 HugCivi 유지보수 API의 DB backup을 쓰거나 컨테이너를 멈춘 뒤 복사한다.
3. `docker pull ghcr.io/mephiblin/hugcivi:latest`를 실행한다.
4. Portainer Stack redeploy 또는 CasaOS Custom Install 재배포를 수행한다.
5. 같은 `/data`와 `/config`로 컨테이너 하나만 실행 중인지 확인한다.
6. UI, job list, library를 검증한다.

이관 절차:

- 기존 컨테이너를 먼저 멈춘다.
- 기존 `/data` 내용을 새 `/DATA/HugCivi/models` 또는 사용자가 지정한 archive 경로로 복사한다.
- 기존 `/config` 내용을 새 `/DATA/AppData/hugcivi/config` 또는 사용자가 지정한 config 경로로 복사한다.
- 최소한 `/config/jobs.sqlite3`는 별도 백업한다.
- 경로가 바뀌어도 컨테이너 내부 mount는 `/data`, `/config`로 유지한다.
- 같은 `jobs.sqlite3`를 두 HugCivi 컨테이너가 동시에 쓰지 않게 한다.

문제 해결 기준:

- `503` 또는 시작 거부: `APP_PASSWORD`가 비어 있거나 placeholder인지 확인하고 긴 값으로 재배포한다.
- 이미지 pull 실패: GHCR 접근성, registry login, architecture, DNS를 확인한다.
- 포트 충돌: `ss -ltnp | grep ':8088'`로 점유 프로세스를 찾고 사용자와 포트 변경을 결정한다.
- 저장 실패: host path, UID/GID, bind mount 오타, `/data`와 `/config` 쓰기 권한을 확인한다.
- 느린 NAS: `MAX_CONCURRENT_DOWNLOADS=2`, `QUEUE_PER_PROVIDER_LIMIT=1`, `INTERNAL_JOB_MAX_CONCURRENT=1`, `MEDIA_TRANSCODE_MAX_CONCURRENT=1`을 유지한다.
- 라이브러리 누락: 인덱서 완료를 기다리거나 UI의 reindex를 실행한다.
- 업데이트 후 예전 컨테이너가 남음: 같은 DB를 쓰는 중복 컨테이너를 중지한다. persistent data 삭제 옵션은 쓰지 않는다.

## 14. 최종 보고 형식

Codex는 작업 종료 시 아래 형식으로 간결하게 보고한다. secret은 전체 값을 반복 노출하지 말고, 사용자가 별도로 요청한 전달 방식이 있을 때만 제공한다.

```markdown
## HugCivi CasaOS 배포 결과

- 배포 방식: Portainer Stack / CasaOS Custom Install / 보류
- Portainer 점검: 설치됨/없음/복구 필요, 확인 근거 요약
- Docker 점검: version, compose 가능 여부, image pull 결과
- 접속 주소: http://<CasaOS_IP>:8088
- 관리자 ID: admin 또는 변경값
- APP_PASSWORD: 설정 완료, 전체 값은 별도 전달 여부 명시
- `/data` host path: ...
- `/config` host path: ...
- UID/GID: ...
- 기존 데이터 처리: 신규/기존 유지/이관/백업 위치
- 검증 결과: container 상태, HTTP 상태, volume 쓰기, 로그 핵심
- 남은 주의사항: Portainer/CasaOS 수동 확인, 토큰 설정, 백업, 업데이트 방식
```
