# Patch Notes Guide

Use this folder for date-based development history and handoff records. The root `PATCH_NOTES.md` can summarize releases, but detailed LLM/developer work notes live here.

## File Naming

Use one file per local date:

```text
docs/patch-notes/YYYY-MM-DD.md
```

Append multiple entries to the same date file when several changes happen on one day.

## Entry Template

```markdown
## Short Change Title

Time: HH:MM KST
Commit: `<sha>` or `pending`
# If one entry covers several related commits, use:
Commits: `<sha1>`, `<sha2>`
Status: implemented | verified | released | planning | reverted

### Summary

- What changed from a user/developer point of view.

### Code And Docs

- Backend:
- Frontend/addon:
- Tests:
- Docs:

### Verification

- Command/result:
- Command/result:

### Deploy Impact

- Image/build/deploy notes, or `None`.

### Handoff Notes

- What the next human or LLM should know before continuing.

### Follow-Up

- Remaining work, risks, or `None`.
```

## When To Write

Write or update an entry after implementation and verification, before commit or release, when the change affects:

- user-visible behavior
- API routes or response shapes
- environment variables or UI settings
- Docker, Portainer, GHCR, startup, or deployment behavior
- DB schema, migrations, jobs, queues, scheduler behavior
- filesystem mutation, path safety, archive ZIP, media cache, or storage usage
- download providers, parser routing, child job creation, or sidecars
- frontend flows, PWA behavior, Chrome extension behavior
- development process, `AGENTS.md`, `README_LLM.md`, `SKILL_Dev/`, or current reference docs

Do not use this folder as a scratchpad for every tiny edit. Use it for records that would help the next developer safely continue.

## Style

- Keep entries factual and command-backed.
- Prefer absolute dates and commit SHAs over relative phrasing.
- Use `Commits:` when a single dated entry summarizes several related commits.
- Separate implemented behavior from planning.
- Link current docs when a dated plan is superseded.
- Record skipped checks and why.
