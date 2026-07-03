---
name: hugcivi-download-providers
description: Stable workflow for changing HugCivi input parsing and download providers: Hugging Face, Civitai, Hitomi, ASMR.one, gallery-dl, yt-dlp, generic HTTP, and ComfyUI workflow downloads.
---

# HugCivi Download Providers Skill

Use this before adding or changing URL parsing, provider classification, external download behavior, child jobs, retries, throttling, or provider metadata.

## Provider Flow

1. `app/parsers.py` converts input text into `ParsedDownload`.
2. `app/db.py::create_job()` stores a `download` job.
3. `app/downloader.py` scheduler selects work under global/per-provider limits.
4. `download_*` handler writes files under `/data`.
5. Handler writes sidecar metadata when the library should survive DB loss.
6. UI polls `/api/jobs` and library/media APIs.

## Stable Rules

- Keep major providers in conservative provider buckets for rate-limit safety.
- Keep network calls mocked in tests.
- Respect `DOWNLOAD_REQUEST_MIN_INTERVAL_SECONDS`, provider-specific throttles, retry caps, and `Retry-After` behavior.
- Write sidecars for recoverable library cards when content is user-visible.
- Cap child job creation for listing/image-resource expansion.
- Keep parser behavior backward compatible for existing command aliases.

## Code To Read

```bash
sed -n '1,280p' app/parsers.py
sed -n '1,260p' app/models.py
rg -n "def (provider_key_for_parsed|run_job|download_huggingface|download_civitai|download_civitai_image_page|download_hitomi|download_hitomi_listing|download_asmrone|download_gallerydl|download_generic|download_comfyui)" app/downloader.py
```

## Test Focus

Run relevant parser/runtime tests:

```bash
python3 -m pytest -q -p no:cacheprovider \
  tests/test_bulk_add.py \
  tests/test_civitai_image_parser.py \
  tests/test_hitomi_listing.py \
  tests/test_youtube_parser.py \
  tests/test_main_urls.py \
  tests/test_downloader_runtime.py
```

Update `docs/feature-code-map.md`, README examples, and `docs/configuration.md` when user-visible inputs, settings, sidecars, or queues change.
