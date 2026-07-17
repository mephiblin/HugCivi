---
name: hugcivi-build-push
description: Verify, commit, and publish HugCivi through either the commit-push Git workflow or the commit-build-push AMD64 GHCR release workflow. Use when the user asks to commit, push, build, release, update GHCR, or prepare a Portainer/Synology image.
---

# HugCivi Commit And Release Skill

Use one of these explicit workflows from `/home/inri/문서/HugCivi`:

1. `commit-push` (`커밋-푸시`): verify, selectively commit, and push Git to `origin/main`. Do not build an image.
2. `commit-build-push` (`커밋-빌드-푸시`): verify, selectively commit, build and push a `linux/amd64` image to GHCR, then push Git to `origin/main` unless the user explicitly requests Git push first.

The production image is `ghcr.io/mephiblin/hugcivi:latest`. Never publish an ARM64-only image under `latest`; Synology and ordinary Intel/AMD Ubuntu targets require `linux/amd64`.

## Common Preflight

Run before either workflow:

```bash
pwd
git status --short
git branch --show-current
git remote -v
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
df -h .
```

Require:

- Repository root `/home/inri/문서/HugCivi`.
- Normally branch `main` and remote `https://github.com/mephiblin/HugCivi.git`.
- Intentional commit scope. Never include unrelated dirty or untracked files.
- No behind commits on `origin/main`; reconcile before committing if behind.

For `commit-build-push`, also run:

```bash
docker version
docker buildx version
docker info --format '{{.Architecture}} {{.OSType}}'
docker buildx ls
```

## Verification

Run before committing:

```bash
git diff --check
python3 -m py_compile app/main.py app/db.py app/defaults.py app/downloader.py app/internal_jobs.py app/parsers.py app/workflows.py app/metadata.py app/utils.py app/subscriptions.py
node --check chrome-extension/shared.js
node --check chrome-extension/background.js
node --check chrome-extension/popup.js
node -e "JSON.parse(require('fs').readFileSync('chrome-extension/manifest.json','utf8'))"
python3 -m pytest -q -p no:cacheprovider
```

If pytest is unavailable, use a temporary virtual environment populated from `requirements.txt`; do not add test dependencies to the repository. If existing unrelated tests fail, reproduce them against a clean `origin/main` worktree and obtain explicit user acceptance before proceeding.

Before commit, follow `skill_docs_handoff.md`. Record substantive verification and deploy impact in `docs/patch-notes/YYYY-MM-DD.md`; update root `PATCH_NOTES.md` only for a user-facing changelog change.

## Selective Commit

Always commit before building because the immutable image tag uses the commit SHA.

```bash
git add <intended-files-only>
git diff --cached --check
git diff --cached --stat
git diff --cached --name-status
git commit -m "<clear commit message>"
git rev-parse --short HEAD
git status --short
```

Do not commit generated caches, local data, `.pytest_cache`, `config`, `data`, or unrelated user work.

## Workflow 1: `commit-push`

Push the committed code directly and verify synchronization:

```bash
git push origin main
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
git log -1 --oneline
git status --short
```

Stop here. Report explicitly that no container image was built.

## Workflow 2: `commit-build-push`

### Ensure AMD64 Build Support

The output must be `linux/amd64`, even when the local Docker host is ARM64. A `commit-build-push` request explicitly authorizes using the already-installed AMD64 emulator and, when necessary on an ARM64 host, registering QEMU/binfmt with the following privileged container:

```bash
docker run --privileged --rm tonistiigi/binfmt --install amd64
docker buildx inspect --bootstrap
docker buildx ls
```

Continue only when buildx lists `linux/amd64`. Do not use emulation during `commit-push`, and do not substitute an ARM64 image for the production `latest` tag.

### Build From A Clean Commit

Do not publish the current directory when it contains unrelated changes. Prefer a detached temporary worktree so user changes remain untouched:

```bash
SHORT_SHA="$(git rev-parse --short HEAD)"
BUILD_DIR="/tmp/hugcivi-build-${SHORT_SHA}"
git worktree add --detach "$BUILD_DIR" HEAD
git -C "$BUILD_DIR" status --short
git -C "$BUILD_DIR" diff --check
```

The worktree status must be empty.

### Build And Push GHCR

Use the project-local helper only when it is available and the primary `main` worktree is clean, so its branch-based `latest` behavior remains valid:

```bash
/home/inri/.codex/skills/hugcivi-local-image-push/scripts/local-image-push.sh --push
```

When it is unavailable, the primary worktree is dirty, or it cannot target the clean worktree, use the explicit detached-worktree build:

```bash
docker buildx build \
  --platform linux/amd64 \
  -t "ghcr.io/mephiblin/hugcivi:sha-${SHORT_SHA}" \
  -t ghcr.io/mephiblin/hugcivi:latest \
  --push "$BUILD_DIR"
```

Verify both tags resolve to an AMD64 manifest:

```bash
docker buildx imagetools inspect "ghcr.io/mephiblin/hugcivi:sha-${SHORT_SHA}"
docker buildx imagetools inspect ghcr.io/mephiblin/hugcivi:latest
```

Remove only the temporary worktree created by this workflow:

```bash
git worktree remove "$BUILD_DIR" --force
```

If the user requested the normal order, push Git only after the image push succeeds:

```bash
git push origin main
```

If the user explicitly requested Git push first, verify `origin/main` already contains `HEAD`, then continue with the image build and report that ordering deviation.

## Failure Handling

- Stop before commit/build on test failures unless the user explicitly accepts verified baseline failures.
- On GHCR authentication failure, ask the user to run `docker login ghcr.io`.
- If image push fails in the normal order, do not Git-push unless the user explicitly asks to publish code anyway.
- If Git was explicitly pushed first and the image later fails, report that `origin/main` contains the commit but GHCR does not contain the matching release.
- If Git push fails after image success, report that GHCR has the image while `origin/main` lacks the commit.
- If the branch is not `main`, ask before tagging or pushing `latest`.

## Final Report

Report:

- Workflow used.
- Commit SHA and message.
- Verification pass/fail, including accepted baseline failures.
- For build workflow: `linux/amd64` confirmation and both GHCR tags.
- Git push result and local/remote ahead/behind count.
- Whether the main worktree is clean; identify preserved unrelated files if not.
- When image push succeeds, confirm Portainer can pull `ghcr.io/mephiblin/hugcivi:latest`.
