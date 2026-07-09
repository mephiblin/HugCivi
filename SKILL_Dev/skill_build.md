---
name: hugcivi-build-push
description: Build, verify, and publish HugCivi from the project checkout. Use when working in this repository and the user asks to build, test, push, deploy, update GHCR, or prepare a Portainer/Synology image.
---

# HugCivi Build Skill

Use this repo-local skill when a user asks to verify, build, push, deploy, or refresh the HugCivi container image.

## Scope

This workflow targets the HugCivi repository root:

```bash
/home/inri/문서/HugCivi
```

The normal production image is:

```text
ghcr.io/mephiblin/hugcivi:latest
```

The local build path is for `linux/amd64`. Do not use ARM emulation unless the user explicitly asks for it.

## Environment Check

Run these checks before committing or building:

```bash
pwd
git status --short
git branch --show-current
git remote -v
docker version
docker buildx version
docker info --format '{{.Architecture}} {{.OSType}}'
df -h .
```

Required conditions:

- Current directory is the HugCivi repo.
- Branch is normally `main` for publishing `latest`.
- `origin` points to `https://github.com/mephiblin/HugCivi.git`.
- Docker and buildx are available.
- GHCR login is already configured for image push.
- Worktree changes are intentional. Do not include unrelated dirty files.

If `docker buildx build --push` fails with an auth error, ask the user to run:

```bash
docker login ghcr.io
```

## Verification

Run the fast checks before commit:

```bash
git diff --check
python3 -m py_compile app/main.py app/db.py app/defaults.py app/downloader.py app/internal_jobs.py app/parsers.py app/workflows.py app/metadata.py app/utils.py app/subscriptions.py
node --check chrome-extension/shared.js
node --check chrome-extension/background.js
node --check chrome-extension/popup.js
node -e "JSON.parse(require('fs').readFileSync('chrome-extension/manifest.json','utf8'))"
python3 -m pytest -q -p no:cacheprovider
```

If Chrome extension files do not exist in a future branch, skip only the `node` extension checks and explain why.

For Markdown-only edits, the full pytest run is still preferred before a production image push. At minimum, run `git diff --check` and any tests covering touched code.

## Documentation Handoff

Before commit/build for a substantive change, follow `skill_docs_handoff.md`. Confirm the dated `docs/patch-notes/YYYY-MM-DD.md` entry records verification and deploy impact; update root `PATCH_NOTES.md` only when the user-facing changelog changes.

## Commit

Commit before image build because the image tag uses the commit SHA.

```bash
git status --short
git add <intended files>
git diff --cached --stat
git commit -m "<clear commit message>"
git status --short
git rev-parse --short HEAD
```

Do not commit generated caches, local data, `.pytest_cache`, `config`, or `data`.

## Build And Push Image

Preferred project-local fast path:

```bash
/home/inri/.codex/skills/hugcivi-local-image-push/scripts/local-image-push.sh --push
```

Expected behavior:

- Builds `linux/amd64`.
- Tags `ghcr.io/mephiblin/hugcivi:sha-<short-sha>`.
- Tags `ghcr.io/mephiblin/hugcivi:latest` when on `main`.
- Pushes the tags to GHCR.

Fallback command if the helper script is unavailable:

```bash
SHORT_SHA="$(git rev-parse --short HEAD)"
docker buildx build \
  --platform linux/amd64 \
  -t "ghcr.io/mephiblin/hugcivi:sha-${SHORT_SHA}" \
  -t ghcr.io/mephiblin/hugcivi:latest \
  --push .
```

Do not publish from a dirty worktree unless the user explicitly asks for a temporary local test image. For a local-only smoke build, use `--load` instead of `--push`.

## Git Push

Push git after the image push succeeds:

```bash
git push origin main
```

Then verify:

```bash
git status --short
git log -1 --oneline
```

## Final Report

Report these items to the user:

- Commit SHA and message.
- Verification commands and pass/fail result.
- Image tags pushed.
- Git push result.
- Whether the worktree is clean.
- That Portainer can pull `ghcr.io/mephiblin/hugcivi:latest` when image push succeeded.

## Failure Handling

- If tests fail, stop before commit/build unless the user explicitly accepts the failure.
- If image push fails, do not git push unless the user explicitly asks to publish the code anyway.
- If git push fails after image push, report that GHCR has the image but `origin/main` did not receive the commit.
- If the branch is not `main`, ask before tagging/pushing `latest`.
