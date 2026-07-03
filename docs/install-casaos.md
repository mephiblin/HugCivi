# HugCivi CasaOS Install Guide

Last updated: 2026-07-03

This guide is for installing HugCivi on a CasaOS host. CasaOS runs Docker apps under the hood, so the durable data rules are the same as the normal Docker deployment:

- archive files live in `/data` inside the container
- app state lives in `/config/jobs.sqlite3` inside the container
- protect the host folders mounted to `/data` and `/config`

CasaOS install and custom-app UI labels can vary by version. If the dashboard cannot import the Compose file cleanly, use the terminal fallback below; CasaOS will still see the resulting Docker container.

## Prerequisites

- CasaOS is installed and reachable in a browser.
- The HugCivi host has enough free disk space for archived models, galleries, and videos.
- You have a long `APP_PASSWORD`.
- Optional: a host-level or network-level proxy/DPI bypass is already configured, or you have a SOCKS/HTTP proxy URL for yt-dlp sources.

CasaOS official installer:

```bash
curl -fsSL https://get.casaos.io | sudo bash
```

Reference: https://casaos.zimaspace.com/

## Prepare Folders

Use paths that match your CasaOS storage layout. These are examples:

```bash
sudo mkdir -p /DATA/HugCivi/data
sudo mkdir -p /DATA/AppData/hugcivi/config
sudo chown -R "$(id -u):$(id -g)" /DATA/HugCivi /DATA/AppData/hugcivi
```

The first folder becomes HugCivi archive storage. The second folder stores SQLite settings, job state, favorites, notes, and backups.

## Install From CasaOS Custom App

1. Open CasaOS.
2. Open `App Store`.
3. Choose `Custom Install` or the Compose import option.
4. Paste a Compose file like this, replacing password, UID/GID, paths, and proxy as needed:

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
      - /DATA/HugCivi/data:/data
      - /DATA/AppData/hugcivi/config:/config
```

5. Install or deploy the custom app.
6. Open:

```text
http://CASAOS_HOST_IP:8088
```

Login:

```text
username: admin
password: the APP_PASSWORD value
```

## Proxy Notes

If the host or LAN already routes container traffic through a working proxy/DPI bypass, leave `YT_DLP_PROXY` empty.

If only the browser works but HugCivi downloads from yt-dlp-backed sites fail with connection resets, set `YT_DLP_PROXY` to the proxy URL:

```text
YT_DLP_PROXY=socks5://192.168.200.100:1080
```

That example matches the `tazihad/byedpi` SOCKS5 proxy container found on the current Docker host. On another CasaOS server, replace `192.168.200.100` with the host LAN IP that publishes the proxy. See [ByeDPI SOCKS5 Proxy Guide](byedpi-socks-proxy.md) for the Portainer stack and Docker CLI install examples.

`YT_DLP_PROXY` only affects yt-dlp-backed sources and probes, such as YouTube and preferred video hosts. It does not proxy Hugging Face, Civitai, generic HTTP downloads, native Hitomi requests, or internal ZIP/transcode/poster jobs.

You can also save the same value later in the web UI:

```text
Settings -> API Token/Auth -> YouTube/yt-dlp Proxy
```

UI-saved values are stored in `/config/jobs.sqlite3` and take precedence over the environment variable.

## Terminal Fallback

If CasaOS Custom Install cannot import Compose reliably, deploy with Docker Compose from the CasaOS terminal or SSH:

```bash
mkdir -p /DATA/AppData/hugcivi
cd /DATA/AppData/hugcivi
```

Create `compose.yml` with the same Compose content above, then run:

```bash
docker compose -f compose.yml pull
docker compose -f compose.yml up -d
docker compose -f compose.yml logs -f --tail=100
```

Update later:

```bash
cd /DATA/AppData/hugcivi
docker compose -f compose.yml pull
docker compose -f compose.yml up -d
```

## Troubleshooting

- `pull access denied`: confirm `ghcr.io/mephiblin/hugcivi:latest` is reachable from the host.
- Login fails: confirm `APP_PASSWORD` and use username `admin` unless you changed `APP_USERNAME`.
- Permission errors under `/data` or `/config`: check host folder ownership and the `PUID`/`PGID` values.
- yt-dlp sites fail while the browser works: either confirm the whole container network is actually proxied, or set `YT_DLP_PROXY`.
- App state disappeared after reinstall: confirm the same host folder is still mounted to `/config`.
