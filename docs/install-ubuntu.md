# HugCivi Ubuntu Install Guide

Last updated: 2026-07-02

This guide installs HugCivi on an Ubuntu server with Docker Engine and Docker Compose V2. It is intended for a home server, NAS-like Ubuntu box, or development PC used as a long-running archive host.

HugCivi stores archive content and app state separately:

- `/data` inside the container: long-lived archive files
- `/config/jobs.sqlite3` inside the container: jobs, settings, favorites, notes, library index, and backups

Use stable host folders and back up `/config` as credential-bearing app state.

## Install Docker Engine

Follow Docker's official Ubuntu repository method. The abbreviated command set is:

Reference: https://docs.docker.com/engine/install/ubuntu/

```bash
sudo apt remove -y docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc || true
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Optional non-root Docker CLI access:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
docker run hello-world
```

If you use `ufw`, remember that Docker published ports can bypass ordinary `ufw` rules. Restrict exposure with your router/firewall or Docker's `DOCKER-USER` chain when needed.

## Prepare Folders

Example layout:

```bash
sudo mkdir -p /srv/hugcivi/data
sudo mkdir -p /srv/hugcivi/config
sudo chown -R "$(id -u):$(id -g)" /srv/hugcivi
```

Use a disk with enough space for models and videos. If `/srv` is on the OS disk and space is limited, mount a larger volume and use that path instead.

## Create Compose File

Create `/opt/hugcivi/compose.yml`:

```bash
sudo mkdir -p /opt/hugcivi
sudo chown -R "$(id -u):$(id -g)" /opt/hugcivi
nano /opt/hugcivi/compose.yml
```

Paste:

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
      APP_PASSWORD: "replace-with-a-long-password"
      PUID: "1000"
      PGID: "1000"
      UMASK: "022"
      HF_TOKEN: ""
      CIVITAI_TOKEN: ""
      GALLERY_DL_USERNAME: ""
      GALLERY_DL_PASSWORD: ""
      GALLERY_DL_COOKIES_FILE: ""
      GALLERY_DL_COOKIES_FROM_BROWSER: ""
      GALLERY_DL_EXTRA_OPTIONS: ""
      YT_DLP_COOKIES_FILE: ""
      YT_DLP_COOKIES_FROM_BROWSER: ""
      YT_DLP_PROXY: ""
      YT_DLP_FORMAT: "best[ext=mp4][vcodec^=avc1]/best[ext=mp4]/best"
      YT_DLP_EXTRA_OPTIONS: ""
      MAX_CONCURRENT_DOWNLOADS: "3"
      QUEUE_PER_PROVIDER_LIMIT: "1"
      DOWNLOAD_STALL_TIMEOUT_SECONDS: "600"
      INTERNAL_JOB_MAX_CONCURRENT: "1"
    volumes:
      - /srv/hugcivi/data:/data
      - /srv/hugcivi/config:/config
```

Set `PUID` and `PGID` to the Linux user that should own files:

```bash
id -u
id -g
```

## Start HugCivi

```bash
cd /opt/hugcivi
docker compose pull
docker compose up -d
docker compose logs -f --tail=100
```

Open:

```text
http://SERVER_IP:8088
```

Login:

```text
username: admin
password: the APP_PASSWORD value
```

## Proxy Notes

If the Ubuntu host or Docker bridge already routes container traffic through your proxy/DPI bypass, leave `YT_DLP_PROXY` empty.

If only browser traffic works and HugCivi's yt-dlp downloads fail with connection resets, set a dedicated yt-dlp proxy:

```text
YT_DLP_PROXY=socks5://192.168.200.100:1080
```

Then restart:

```bash
cd /opt/hugcivi
docker compose up -d
```

`YT_DLP_PROXY` is passed to yt-dlp as `--proxy`. It affects yt-dlp-backed sources and metadata probes only. It does not make the whole container use the proxy.

You can alternatively save the same value in the web UI under:

```text
Settings -> API Token/Auth -> YouTube/yt-dlp Proxy
```

UI-saved settings are stored in `/srv/hugcivi/config/jobs.sqlite3` and take precedence over environment variables.

## Update

```bash
cd /opt/hugcivi
docker compose pull
docker compose up -d
```

## Backup

Back up both folders:

```text
/srv/hugcivi/data
/srv/hugcivi/config
```

`/srv/hugcivi/config/jobs.sqlite3` may contain tokens, cookie paths, proxy URLs, and extra options. Treat it as a credential backup.

## Troubleshooting

- `pull access denied`: confirm the host can reach GHCR and the image name is `ghcr.io/mephiblin/hugcivi:latest`.
- `APP_PASSWORD` error or 503 page: set a long non-placeholder `APP_PASSWORD` and restart.
- Permission errors: verify `/srv/hugcivi/*` ownership and `PUID`/`PGID`.
- Browser opens a site but HugCivi cannot download it: verify whether Docker traffic is covered by your whole-network proxy, or set `YT_DLP_PROXY`.
- Port already used: change `"8088:8088"` to another host port, for example `"18088:8088"`.
