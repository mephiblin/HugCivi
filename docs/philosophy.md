# HugCivi Philosophy

Last updated: 2026-07-02

HugCivi is a personal archive tool for people who collect large local files: model checkpoints, LoRAs, datasets, comics, images, videos, and ComfyUI workflows. The design is not trying to be a public SaaS, a full media server, or a distributed download farm.

## Core Belief

The archive is the product. The web app exists to help collect, classify, inspect, and preserve files without making the NAS fragile.

That leads to a few hard preferences:

- Files belong in `/data`, not inside a DB.
- App state belongs in `/config/jobs.sqlite3`.
- The container should be replaceable.
- The archive folder should remain understandable from the NAS file browser.
- Expensive work should be queued and visible.
- Defaults should protect the server and remote providers before optimizing speed.

## Filesystem First

HugCivi keeps the filesystem meaningful.

If the DB disappears but `/data` survives, the important archive files should still be there. Some UI state is lost, but the collection is not trapped inside an application-specific blob store.

This is why the app uses:

- sidecar metadata files near downloaded content
- `/data` relative paths for favorites and notes
- DB-backed library indexing as a cache/catalog, not the only source of truth
- live filesystem fallback when the library index is empty or explicitly requested

## SQLite Is a Ledger

SQLite is used for durable state:

- job history and progress
- settings and credentials
- favorites
- notes
- internal job artifacts
- content references
- library index payloads
- maintenance history

It is not used for heavy binaries. This keeps the DB small enough for online backup, checkpoint, and compact operations while allowing `/data` to grow independently.

## Conservative by Default

The app talks to external providers and runs on small servers. Defaults therefore favor safety:

- low global download concurrency
- provider-level concurrency limits
- provider cooldown between jobs
- limited Hugging Face worker count
- retry backoff
- optional Hitomi listing confirmation before adding many child jobs
- internal job queue for ZIP/transcode/poster work

Fast downloads are useful. A NAS that stays responsive is more important.

## One Container Until Reality Says Otherwise

HugCivi deliberately avoids mandatory Redis, Celery, PostgreSQL, Elasticsearch, or a separate worker container.

Reasons:

- Portainer/Synology deployment should stay simple.
- The failure modes should be visible to one user.
- SQLite is sufficient for the current state model.
- Most bottlenecks are network, disk, and ffmpeg CPU rather than DB concurrency.

The project can grow into more services later, but only after real usage proves the single-container model is the limiting factor.

## User Control Over Automation

Automation should remove repetitive work, not surprise the owner of the archive.

Examples:

- automatic route selection for LLM, LoRA, Checkpoint, Embedding, VAE, ControlNet, Upscaler, Hitomi, gallery-dl, and generic downloads
- bulk URL entry for deliberate queueing
- Hitomi listing `auto` mode for convenience and `confirm` mode when the user wants review
- local folder operations through explicit context menus
- DB maintenance through explicit API calls rather than hidden frequent pragmas

## Server Protection Is a Feature

ZIP creation, video transcoding, and poster extraction can hurt a small NAS more than the download itself. HugCivi treats these as jobs, not incidental request work.

This means:

- a folder ZIP request creates an `archive_zip` job
- an unplayable video cache miss creates a `media_transcode` job
- a missing video poster creates a `media_poster` job
- the UI polls instead of holding a request open
- `INTERNAL_JOB_MAX_CONCURRENT` controls local expensive work separately from external downloads

## Security Posture

HugCivi assumes trusted personal access, not hostile public internet exposure.

Even so:

- Basic Auth is required.
- insecure default passwords are refused.
- secret settings are not echoed back to the UI.
- paths are scoped to `/data`.
- dangerous yt-dlp output/path/execution options are blocked.
- DB backups are treated as credential backups.

The recommended deployment is behind a private network, VPN, or trusted reverse proxy.

## What HugCivi Is Not

HugCivi is not:

- a multi-user permission system
- a public file sharing service
- a Plex or Jellyfin replacement
- a torrent/download automation suite
- a full-text search engine
- a replacement for real NAS backups

It can preview media and help collect videos, but its main job is archive/download/catalog management.

## Decision Heuristics

When choosing between two designs:

- Pick the design that preserves `/data` as the durable archive.
- Pick the design that is restartable after a container replacement.
- Pick the design that makes expensive work visible in the job list.
- Pick the design that keeps DB changes additive.
- Pick the design that prevents accidental deletion or path escape.
- Pick the design that a single NAS owner can operate without extra infrastructure.
