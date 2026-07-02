# OpenAI Codex용 HugCivi Ubuntu Portainer 설치 운영 지시서

## 1. 시작: Portainer 설치 여부 점검

Codex는 이 작업을 반드시 Portainer 설치 여부 점검에서 시작한다. 사용자가 "HugCivi를 Ubuntu 서버의 Portainer로 설치해줘"라고 맡기면, 먼저 서버에 접속해 아래 읽기 중심 명령으로 현재 상태를 확인하고 결과를 기록한다.

```bash
printf 'docker binary: '
command -v docker || true

sudo docker ps -a --filter "name=portainer" \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' || true

sudo docker volume ls --filter name=portainer_data || true
sudo ss -lntp | grep -E ':(9443|9000|8000)\b' || true
curl -kfsS -I https://127.0.0.1:9443/ || true
```

판정 기준:

- `portainer` 컨테이너가 실행 중이고 `9443`이 열려 있으면 기존 Portainer를 사용한다.
- `portainer` 컨테이너가 중지되어 있으면 `sudo docker logs --tail=100 portainer`로 원인을 확인하고, 삭제하거나 재생성하기 전에 사용자에게 확인한다.
- `docker` 명령이 없거나 Docker daemon에 연결할 수 없으면 Portainer가 Docker 방식으로 설치되어 있지 않은 것으로 보고 Docker 점검 단계로 간다.
- `9443`을 다른 서비스가 쓰고 있으면 Portainer 포트를 바꾸기 전에 사용자에게 충돌 내용을 보고하고 선택을 받는다.

## 2. 목표와 전제

목표는 일반 Ubuntu 서버에 이미 있는 Portainer를 우선 활용해 HugCivi Stack을 배포하는 것이다. Portainer가 없으면 Docker 설치 상태를 점검한 뒤 Docker Engine과 Compose plugin을 준비하고, Portainer CE를 설치한 다음 HugCivi Stack을 배포한다.

전제:

- 대상 서버는 Ubuntu 22.04, 24.04, 26.04 LTS 계열을 우선한다.
- Codex는 SSH, sudo, Portainer UI 접근 권한 중 작업에 필요한 권한을 사용자에게 확인한다.
- HugCivi 데이터는 `/srv/hugcivi/models`를 컨테이너의 `/data`에, 설정과 SQLite 상태는 `/srv/hugcivi/config`를 컨테이너의 `/config`에 bind mount한다.
- HugCivi 이미지는 기본적으로 `ghcr.io/mephiblin/hugcivi:latest`를 사용한다.
- HugCivi는 Basic Auth를 사용하며 `APP_PASSWORD`가 비어 있거나 예시값이면 정상 기동하지 않는다.

## 3. 금지사항

Codex는 사용자 확인 없이 데이터를 삭제하거나 초기화하지 않는다.

금지 명령과 행동:

- `rm -rf /srv/hugcivi`, `rm -rf /var/lib/docker`, `docker rm -v`, `docker volume rm`, `docker volume prune`, `docker system prune --volumes`, `docker compose down -v`, Portainer Stack 삭제 시 volume 제거 옵션 사용
- 기존 `/srv/hugcivi/models`, `/srv/hugcivi/config`, `/config/jobs.sqlite3` 덮어쓰기 또는 삭제
- 기존 Portainer 컨테이너, Docker volume, 다른 Stack, 다른 컨테이너를 확인 없이 중지, 삭제, 재생성
- `rsync --delete` 사용
- `/srv` 전체에 대한 `chown -R` 실행
- `APP_PASSWORD`, 토큰, 쿠키 파일 내용을 최종 보고에 평문으로 노출

기존 데이터가 있으면 먼저 백업 위치와 이관 방식을 사용자에게 확인한다. 특히 같은 `/config/jobs.sqlite3`를 두 HugCivi 컨테이너가 동시에 사용하게 만들면 안 된다.

## 4. Codex가 먼저 수집할 정보

Portainer 존재 여부를 확인한 직후, 변경 작업 전에 아래 정보를 수집한다.

서버 상태 명령:

```bash
hostname -I || true
cat /etc/os-release
uname -m
id
df -h / /srv 2>/dev/null || df -h /
sudo ufw status verbose || true
sudo docker version || true
sudo docker compose version || docker compose version || true
```

사용자에게 확인할 항목:

- 서버 접속 주소 또는 도메인
- Portainer 관리자 계정 접근 가능 여부
- HugCivi 외부 접속 포트, 기본값은 `8088`
- `/srv/hugcivi`를 사용할지, 기존 데이터 경로를 이관할지
- 컨테이너 파일 소유자로 쓸 UID/GID, 기본값은 `1000:1000`
- `APP_USERNAME`, 기본값은 `admin`
- `APP_PASSWORD`를 사용자가 제공할지 Codex가 생성할지
- Web editor 방식으로 즉시 배포할지, Git repository 방식으로 관리할지
- UFW, 클라우드 보안 그룹, 공유기 포트포워딩, 리버스 프록시 사용 여부
- GHCR 이미지가 비공개일 경우 registry 로그인 정보 제공 방식

## 5. Docker와 Compose 점검

Portainer가 없거나 Docker 상태가 불명확하면 Docker를 점검한다. Portainer가 이미 있어도 HugCivi 배포 전 Docker daemon과 Compose plugin 상태는 확인한다.

```bash
command -v docker || true
sudo systemctl is-active docker || true
sudo systemctl status docker --no-pager || true
sudo docker version || true
sudo docker run --rm hello-world || true
sudo docker compose version || docker compose version || true
dpkg -l docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc 2>/dev/null || true
```

Docker가 없으면 Docker 공식 apt 저장소 방식으로 설치한다. 충돌 패키지가 이미 설치되어 있거나 기존 컨테이너가 있으면 제거 전에 사용자에게 영향 범위를 보고한다. `/var/lib/docker`는 삭제하지 않는다.

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run --rm hello-world
sudo docker compose version
```

현재 사용자를 `docker` 그룹에 넣는 것은 필수가 아니다. 필요하면 사용자에게 보안 의미를 설명한 뒤 선택적으로 진행한다.

```bash
sudo usermod -aG docker "$USER"
```

## 6. Portainer CE 설치

1단계 점검 결과 Portainer가 없을 때만 설치한다. 기존 `portainer` 컨테이너나 `portainer_data` volume이 있으면 재사용, 시작, 복구 중 무엇을 할지 먼저 판단하고 삭제는 사용자 확인 뒤에만 한다.

기본 설치 명령:

```bash
sudo docker volume create portainer_data

sudo docker run -d \
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
sudo docker ps --filter "name=portainer" \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
sudo docker logs --tail=80 portainer
curl -kfsS -I https://127.0.0.1:9443/ || true
```

접속 주소는 `https://서버_IP:9443`이다. Codex가 브라우저나 Portainer API를 사용할 수 있으면 초기 관리자 생성과 local Docker environment 선택까지 직접 진행한다. UI 접근 권한이 없으면 필요한 값과 다음 입력 항목을 사용자에게 요청하고, 임의로 우회하지 않는다.

UFW를 사용하는 경우 필요한 포트를 연다. Portainer Edge Agent를 쓰지 않으면 외부 방화벽에서 `8000`을 열 필요는 없다.

```bash
sudo ufw allow 9443/tcp
sudo ufw allow 8088/tcp
sudo ufw status verbose
```

Docker의 포트 노출은 UFW 정책과 다르게 동작할 수 있으므로 클라우드 보안 그룹, 공유기, 리버스 프록시도 별도로 확인한다.

## 7. `/srv/hugcivi` 폴더와 권한 점검

HugCivi 배포 전 영구 저장 경로를 준비한다.

```bash
sudo mkdir -p /srv/hugcivi/models /srv/hugcivi/config
sudo ls -ldn /srv/hugcivi /srv/hugcivi/models /srv/hugcivi/config
sudo find /srv/hugcivi -maxdepth 2 -mindepth 1 -print | head -50
```

새 설치이고 기본 UID/GID `1000:1000`을 쓸 때:

```bash
sudo chown -R 1000:1000 /srv/hugcivi/models /srv/hugcivi/config
sudo chmod 0755 /srv/hugcivi /srv/hugcivi/models /srv/hugcivi/config
```

기존 데이터가 있으면 재귀 `chown` 전에 사용자에게 확인한다. 대량 모델 폴더는 권한 변경도 시간이 오래 걸리고 운영 중인 다른 서비스에 영향을 줄 수 있다. 한 번만 권한 정리가 필요하면 Stack 환경변수 `HUGCIVI_CHOWN_ON_START=1`을 임시로 사용할 수 있지만, 정상 동작 확인 후 `0`으로 되돌리는 것을 기본으로 한다.

## 8. `APP_PASSWORD` 준비

Codex는 배포 전에 반드시 긴 `APP_PASSWORD`를 준비한다. 사용자가 제공하지 않으면 아래처럼 생성하고, 사용자에게 안전한 보관을 요청한다.

```bash
openssl rand -base64 36
```

운영 규칙:

- `APP_PASSWORD`는 빈 값, `password`, `changeme`, `admin`, `여기에_긴_비밀번호` 같은 예시값이면 안 된다.
- Stack 환경변수 또는 Compose에 넣되 저장소 파일에는 커밋하지 않는다.
- 최종 보고에는 `APP_PASSWORD=설정됨(비공개)`처럼만 적는다.
- Hugging Face, Civitai, gallery-dl, yt-dlp 토큰과 쿠키도 같은 방식으로 비공개 처리한다.

## 9. Portainer Stack 배포 방식 선택

### Web editor 방식

Ubuntu 서버 1대에 빠르게 설치할 때 기본 선택이다. Codex가 Portainer UI를 조작할 수 있으면 아래 작업을 직접 수행한다.

- `Stacks`에서 `Add stack` 선택
- Stack 이름은 `hugcivi`
- Build method는 `Web editor`
- Compose 예시를 붙여넣고 `APP_PASSWORD`, UID/GID, 포트를 실제 값으로 변경
- `Deploy the stack` 실행

Codex가 UI를 직접 조작할 수 없으면, 최종 보고로 끝내지 말고 사용자에게 "아래 값으로 Portainer Stack Web editor에 입력해도 되는지" 확인을 요청한다.

### Git repository 방식

Compose 파일을 저장소 기준으로 관리하려면 Git repository 방식을 사용한다.

- Repository URL: HugCivi 저장소 URL
- Branch: `main`
- Compose path: `portainer-stack.yml`
- Stack environment variables에는 Ubuntu 경로를 반드시 넣는다.

필수 환경변수 예시:

```text
APP_USERNAME=admin
APP_PASSWORD=긴_비밀번호
PUID=1000
PGID=1000
HUGCIVI_DATA_DIR=/srv/hugcivi/models
HUGCIVI_CONFIG_DIR=/srv/hugcivi/config
HUGCIVI_HTTP_PORT=8088
HUGCIVI_CHOWN_ON_START=0
DOWNLOAD_STALL_TIMEOUT_SECONDS=600
INTERNAL_JOB_MAX_CONCURRENT=1
HUGCIVI_IMAGE=ghcr.io/mephiblin/hugcivi:latest
```

주의: 저장소의 `portainer-stack.yml`은 NAS 기본 경로를 포함할 수 있으므로, Ubuntu에서는 위 `HUGCIVI_DATA_DIR`와 `HUGCIVI_CONFIG_DIR`를 빼먹으면 안 된다.

## 10. Web editor용 Compose 예시

아래 예시는 일반 Ubuntu 서버의 `/srv/hugcivi` 기준이다. Codex는 실제 서버 값에 맞게 `APP_PASSWORD`, UID/GID, 포트를 조정한 뒤 배포한다.

```yaml
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
      APP_PASSWORD: "REPLACE_WITH_LONG_PASSWORD"
      PUID: "1000"
      PGID: "1000"
      UMASK: "022"
      HUGCIVI_CHOWN_ON_START: "0"
      GALLERY_DL_AUTO_UPDATE: "1"
      GALLERY_DL_UPDATE_SPEC: "gallery-dl<2.0"
      HUGCIVI_STARTUP_CONFIG_FILE: "/config/startup.env"

      MAX_CONCURRENT_DOWNLOADS: "2"
      QUEUE_PER_PROVIDER_LIMIT: "1"
      QUEUE_PROVIDER_COOLDOWN_MIN_SECONDS: "2"
      QUEUE_PROVIDER_COOLDOWN_MAX_SECONDS: "5"
      DOWNLOAD_STALL_TIMEOUT_SECONDS: "600"
      INTERNAL_JOB_MAX_CONCURRENT: "1"
      HF_SNAPSHOT_MAX_WORKERS: "2"
      DOWNLOAD_REQUEST_MIN_INTERVAL_SECONDS: "1.5"
      GALLERY_DL_SLEEP_REQUEST_SECONDS: "2"
      YT_DLP_FORMAT: "best[ext=mp4][vcodec^=avc1]/best[ext=mp4]/best"
      MEDIA_TRANSCODE_MAX_CONCURRENT: "1"
    volumes:
      - type: bind
        source: /srv/hugcivi/models
        target: /data
      - type: bind
        source: /srv/hugcivi/config
        target: /config
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS -u \"$${APP_USERNAME}:$${APP_PASSWORD}\" http://127.0.0.1:8088/ >/dev/null || exit 1"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 30s
```

선택 토큰은 설치 후 HugCivi 웹 UI 설정에서 저장하는 것을 우선한다. 환경변수로 미리 넣어야 하면 Compose에 추가하되 최종 보고에는 값을 숨긴다.

```yaml
      HF_TOKEN: "hf_xxx"
      CIVITAI_TOKEN: "xxx"
      YT_DLP_COOKIES_FILE: "/config/yt-dlp/cookies.txt"
```

## 11. 배포 후 검증

Stack 배포 직후 Codex는 Portainer UI와 CLI 양쪽에서 확인한다.

```bash
sudo docker ps --filter "name=hugcivi" \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
sudo docker logs --tail=120 hugcivi
sudo docker inspect hugcivi --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
sudo test -d /srv/hugcivi/models && sudo test -d /srv/hugcivi/config
sudo ls -lah /srv/hugcivi/config | head
```

인증 HTTP 확인은 비밀번호가 shell history나 보고서에 남지 않게 처리한다.

```bash
read -r -s APP_PASSWORD
curl -fsS -u "admin:${APP_PASSWORD}" http://127.0.0.1:8088/ >/dev/null && echo "HugCivi HTTP OK"
unset APP_PASSWORD
```

브라우저 접속 주소:

```text
http://서버_IP:8088
```

확인할 결과:

- Portainer Stack `hugcivi`가 running 또는 healthy 상태
- HugCivi UI가 Basic Auth 뒤에 열린다.
- `/srv/hugcivi/config/jobs.sqlite3`가 생성되거나 기존 DB가 유지된다.
- `/srv/hugcivi/models`와 `/srv/hugcivi/config` mount가 각각 `/data`, `/config`로 연결된다.
- 컨테이너 로그에 `APP_PASSWORD` 누락, permission denied, bind mount 오류가 없다.

## 12. 업데이트

업데이트 전 Codex는 `/srv/hugcivi/config/jobs.sqlite3` 백업 가능 여부를 확인한다. 실행 중 백업은 앱의 DB 백업 기능을 우선하고, 파일 복사는 컨테이너 정지 또는 SQLite 일관성 확보 후 수행한다.

Portainer 방식:

- `Stacks`에서 `hugcivi` 선택
- Compose와 환경변수 확인
- 최신 이미지 pull 옵션을 켜거나, Portainer가 제공하는 re-pull/redeploy 흐름 사용
- `Update the stack` 실행
- 배포 후 11단계 검증 반복

CLI 보조 확인:

```bash
sudo docker pull ghcr.io/mephiblin/hugcivi:latest
sudo docker image ls ghcr.io/mephiblin/hugcivi
```

Stack 삭제 후 재생성은 최후 수단이다. 이 경우에도 Portainer volume 제거, bind mount 데이터 삭제, `/srv/hugcivi` 삭제는 사용자 확인 없이는 금지다.

## 13. 기존 설치 이관

이관은 새 설치보다 위험하므로 Codex는 먼저 원본 경로, 대상 경로, 정지 가능 시간, 백업 위치를 사용자에게 확인한다.

원칙:

- 기존 HugCivi 컨테이너를 멈춘 뒤 복사한다.
- 같은 `/config/jobs.sqlite3`를 두 컨테이너가 동시에 열지 않게 한다.
- `rsync --delete`는 쓰지 않는다.
- 복사 후 원본은 사용자가 폐기 승인하기 전까지 보존한다.

예시:

```bash
sudo docker stop 기존_hugcivi_컨테이너명
sudo mkdir -p /srv/hugcivi/models /srv/hugcivi/config
sudo rsync -aH --numeric-ids /기존/data/ /srv/hugcivi/models/
sudo rsync -aH --numeric-ids /기존/config/ /srv/hugcivi/config/
sudo ls -lah /srv/hugcivi/config/jobs.sqlite3
```

복사 완료 후 권한을 점검하고, 필요한 경우 사용자 확인 뒤 `chown`을 수행한다.

## 14. 문제 해결

### HugCivi가 503을 보여줌

`APP_PASSWORD`가 비어 있거나 예시값일 가능성이 높다. Stack 환경변수 또는 Compose 값을 긴 비밀번호로 바꾼 뒤 재배포한다.

```bash
sudo docker logs --tail=120 hugcivi
```

### Permission denied 또는 저장 실패

호스트 폴더 소유권과 `PUID`, `PGID`가 맞지 않을 수 있다.

```bash
sudo ls -ldn /srv/hugcivi/models /srv/hugcivi/config
sudo docker inspect hugcivi --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(PUID|PGID|HUGCIVI_CHOWN_ON_START)='
```

기존 데이터가 새 설치 데이터이고 UID/GID를 `1000:1000`으로 쓰기로 확인했을 때만:

```bash
sudo chown -R 1000:1000 /srv/hugcivi/models /srv/hugcivi/config
```

### 이미지 pull 실패

이미지 이름과 GHCR 인증 상태를 확인한다.

```bash
sudo docker pull ghcr.io/mephiblin/hugcivi:latest
sudo docker login ghcr.io
```

비공개 이미지면 Portainer `Registries`에 GHCR 인증을 추가하고 Stack 배포 시 registry를 선택한다.

### 포트 접속 실패

컨테이너 포트, 호스트 리스닝 포트, 방화벽, 리버스 프록시를 함께 확인한다.

```bash
sudo docker ps --filter "name=hugcivi"
sudo ss -lntp | grep -E ':(8088|9443)\b' || true
sudo ufw status verbose || true
curl -fsS http://127.0.0.1:8088/ >/dev/null || true
```

### Git Stack이 `/srv/hugcivi`를 쓰지 않음

Git repository 방식에서 `HUGCIVI_DATA_DIR`와 `HUGCIVI_CONFIG_DIR` 환경변수가 빠졌을 가능성이 높다. Stack 환경변수에 아래 값을 넣고 재배포한다.

```text
HUGCIVI_DATA_DIR=/srv/hugcivi/models
HUGCIVI_CONFIG_DIR=/srv/hugcivi/config
```

### 서버가 느려짐

약한 서버에서는 동시 작업 수를 낮춘다.

```text
MAX_CONCURRENT_DOWNLOADS=2
QUEUE_PER_PROVIDER_LIMIT=1
INTERNAL_JOB_MAX_CONCURRENT=1
MEDIA_TRANSCODE_MAX_CONCURRENT=1
```

대량 다운로드, ZIP 생성, 비디오 transcode를 동시에 실행하지 않도록 사용자에게 운영 기준을 보고한다.

## 15. 최종 보고 형식

Codex는 작업 완료 후 아래 형식으로 사용자에게 보고한다.

```text
작업 파일/대상:
- 서버: <호스트 또는 IP>
- 배포 방식: Portainer Web editor 또는 Git repository
- Stack 이름: hugcivi

설치/점검 결과:
- Portainer: 기존 사용 또는 신규 설치, 접속 URL
- Docker: 버전과 동작 확인 결과
- Compose plugin: 버전 확인 결과
- HugCivi 이미지: ghcr.io/mephiblin/hugcivi:<태그>

영구 경로:
- /data -> /srv/hugcivi/models
- /config -> /srv/hugcivi/config
- UID/GID: <값>

보안:
- APP_USERNAME: <값>
- APP_PASSWORD: 설정됨(비공개)
- 추가 토큰/쿠키: 설정 여부만 보고

검증:
- 컨테이너 상태: <running/healthy>
- HTTP 확인: <성공/실패>
- 로그 이상: <없음/요약>
- 방화벽/포트: <확인 결과>

주의/후속:
- 기존 데이터 보존 여부
- 백업 위치
- 사용자가 추가로 해야 할 일
```

문제가 남아 있으면 "완료"라고 쓰지 말고, 막힌 지점, 실행한 명령, 관찰한 오류, 사용자에게 필요한 결정을 구체적으로 보고한다.

## 16. 외부 기준 확인

- Docker Engine Ubuntu 설치: `https://docs.docker.com/engine/install/ubuntu/`
- Docker Compose plugin 설치: `https://docs.docker.com/compose/install/linux/`
- Portainer CE Docker Linux 설치: `https://docs.portainer.io/start/install-ce/server/docker/linux`
- Portainer Stack 추가: `https://docs.portainer.io/user/docker/stacks/add`
