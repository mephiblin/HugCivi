---
name: hugcivi-frontend-addon
description: Stable workflow for HugCivi browser UI, PWA assets, media/workflow viewers, settings modal, library cards, and Chrome extension addon changes.
---

# HugCivi Frontend And Addon Skill

Use this before editing `app/templates/index.html`, `app/static/style.css`, PWA files, or `chrome-extension/`.

## Stable UI Rules

- The main UI is a single Jinja-rendered page with client-side JavaScript.
- Keep API response shape changes additive unless backend and frontend change together.
- Long work should show queued/running/done state and poll; do not block HTTP requests.
- Preserve mobile panels, settings modal behavior, job controls, library cards, media viewer, workflow viewer, and context menu flows.
- Keep text fitting on desktop and mobile; avoid layout shifts for buttons/toolbars.

## Chrome Extension Rules

- The extension is a convenience remote, not a replacement for the web UI.
- Keep compatibility with `/api/jobs/bulk` and `/api/jobs`.
- Keep request/auth logic in `chrome-extension/shared.js`.
- `background.js` handles shortcut/current tab submission.
- `popup.js` handles settings, typed/current-tab requests, and progress polling.
- `chrome.storage.local` stores server URL, username, password, target folder, and recent activity.
- The web UI `애드온` button must package a loadable folder containing `hugcivi-chrome-extension/manifest.json`.

## Verification

Run:

```bash
node --check chrome-extension/shared.js
node --check chrome-extension/background.js
node --check chrome-extension/popup.js
node -e "JSON.parse(require('fs').readFileSync('chrome-extension/manifest.json','utf8'))"
python3 -m pytest -q -p no:cacheprovider tests/test_review_fixes.py
```

When changing layout-heavy UI, start the app locally and inspect desktop/mobile behavior. When changing addon packaging, verify `/api/addon/chrome-extension` returns a zip containing `hugcivi-chrome-extension/manifest.json`.
