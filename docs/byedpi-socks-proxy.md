# ByeDPI SOCKS5 Proxy Guide

Last updated: 2026-07-03

This guide records the DPI-bypass/SOCKS5 proxy container found on the current Docker host and how to apply it to HugCivi's yt-dlp downloads.

Use this only on networks and for content where you have permission. HugCivi still expects you to follow service terms, copyright rules, and local network policy.

## What Was Installed

The current host has a running ByeDPI-style SOCKS5 proxy container:

| Field | Value |
| --- | --- |
| Container name | `byedpi` |
| Image | `tazihad/byedpi` |
| Entrypoint | `/opt/byedpi/ciadpi` |
| Command | `--disorder 1 --fake 0 --ttl 1 --auto=torst --tlsrec 1+s` |
| Published port | `192.168.200.100:1080 -> 1080/tcp` |
| SOCKS5 URL | `socks5://192.168.200.100:1080` |
| Docker restart policy found | `no` |
| Portainer stack labels found | none |

Because no Portainer stack labels were present, this looks like a standalone container or a manually created Portainer container rather than a named stack.

The upstream Docker image documents the same base command shape and SOCKS5 usage, with a localhost example. The current host uses the LAN address `192.168.200.100` so other containers and LAN clients can reach it.

References:

- Docker Hub: <https://hub.docker.com/r/tazihad/byedpi>
- Docker image source: <https://github.com/tazihad/byedpi-docker>
- ByeDPI project: <https://github.com/hufrea/byedpi>

## Portainer Stack Install

Use this when you want Portainer to manage the proxy as a stack.

1. Open Portainer.
2. Go to `Stacks`.
3. Choose `Add stack`.
4. Name it `byedpi`.
5. Paste this compose file, changing `192.168.200.100` if the NAS/server LAN IP is different.

```yaml
services:
  byedpi:
    image: tazihad/byedpi:latest
    container_name: byedpi
    command:
      - --disorder
      - "1"
      - --fake
      - "0"
      - --ttl
      - "1"
      - --auto=torst
      - --tlsrec
      - 1+s
    ports:
      - "192.168.200.100:1080:1080/tcp"
    restart: unless-stopped
```

The existing container was found with restart policy `no`. The compose example uses `unless-stopped` because it is the safer operational default for a persistent NAS service.

Avoid publishing this proxy as `0.0.0.0:1080:1080` unless you have a firewall rule limiting access. The proxy has no HugCivi authentication layer.

## Docker CLI Equivalent

For a one-off manual install:

```bash
docker run -d \
  --name byedpi \
  -p 192.168.200.100:1080:1080/tcp \
  --restart unless-stopped \
  tazihad/byedpi \
  --disorder 1 --fake 0 --ttl 1 --auto=torst --tlsrec 1+s
```

To reproduce the currently discovered restart policy exactly, omit `--restart unless-stopped`.

## Apply To HugCivi

HugCivi uses the proxy through the yt-dlp-specific setting `YT_DLP_PROXY`.

Set this value:

```text
socks5://192.168.200.100:1080
```

### Web UI

1. Open HugCivi.
2. Open the user/settings modal.
3. Go to the API token/auth settings area.
4. Set `YouTube/yt-dlp Proxy` to:

```text
socks5://192.168.200.100:1080
```

5. Save settings.
6. Start a new YouTube/yt-dlp-backed job.

UI-saved settings are stored in `/config/jobs.sqlite3` and take precedence over environment variables.

### Portainer Environment Variable

In the HugCivi stack environment, set:

```yaml
YT_DLP_PROXY: "socks5://192.168.200.100:1080"
```

or in plain environment-variable form:

```text
YT_DLP_PROXY=socks5://192.168.200.100:1080
```

`YTDLP_PROXY` is also read as a legacy alias, but `YT_DLP_PROXY` is the preferred key.

## Scope And Limits

`YT_DLP_PROXY` is passed to yt-dlp as `--proxy`.

It affects:

- YouTube one-shot downloads
- YouTube subscription item downloads
- yt-dlp preferred video sites
- yt-dlp metadata probes used to choose titles and folders

It does not affect:

- Hugging Face downloads
- Civitai downloads
- ASMR.one downloads
- generic HTTP file downloads
- native Hitomi requests
- internal ZIP/transcode/poster jobs
- the HugCivi web UI itself

If every outbound request from the container must use a proxy or DPI-bypass path, solve that at the Docker network, host routing, VPN, or gateway layer instead of relying on `YT_DLP_PROXY`.

## Quick Checks

From the Docker host:

```bash
docker ps --filter name=byedpi
docker port byedpi
```

Expected port shape:

```text
1080/tcp -> 192.168.200.100:1080
```

If `curl` is available, a basic SOCKS check from the host is:

```bash
curl --socks5-hostname 192.168.200.100:1080 https://example.com/
```

From inside the HugCivi container, the proxy URL should use the host LAN IP, not `127.0.0.1`, unless the proxy runs in the same container namespace.
