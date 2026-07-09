# Media Library Scaling Comparison 2026-07-09

Status: planning reference before the cache/index maintenance follow-up.

This note compares HugCivi's large library behavior with public Jellyfin and Plex guidance for projects that manage many files, metadata records, and thumbnails.

## Short Conclusion

HugCivi's current DB-first library direction is sound. Mature media servers avoid scanning large storage trees during ordinary page navigation; they separate durable media, local server state, caches, and heavy analysis jobs.

The main gap is not the first-page query path anymore. The next speed and stability gains should come from operator policy:

- local fast `/config` guidance
- cache quotas and cleanup
- maintenance windows for heavy jobs
- richer folder-level scan state
- optional filesystem watching only where reliable
- opt-in video preview/trickplay generation

## Jellyfin And Plex Patterns

### Media, Database, And Cache Separation

Jellyfin and Plex keep source media separate from server state. Jellyfin reads media from the filesystem and recommends keeping the database on local storage rather than network storage. Plex has a dedicated server data directory and warns that putting it on network or external storage can cause poor or unexpected behavior.

HugCivi already follows this shape:

- `/data` stores durable archive content.
- `/config/jobs.sqlite3` stores jobs, settings, favorites, notes, library index, artifacts, and maintenance state.
- `/config/media-cache/thumbnails` stores generated thumbnail files outside SQLite.

The documentation should keep emphasizing that `/config` belongs on fast local storage or SSD-backed storage, while `/data` may be large NAS/archive storage.

### Typed Libraries And Scanner Boundaries

Jellyfin and Plex encourage separate library types such as movies, TV, music, and photos. Mixed libraries are discouraged because metadata matching and scanner assumptions become less reliable.

HugCivi is different. It is not primarily a media identity server. It is a source-aware archive browser for Civitai, gallery-dl, yt-dlp, Hitomi, ASMR.one, Hugging Face, ComfyUI, generic files, and local media. HugCivi should therefore keep source/provider classification and sidecar restoration as first-class concepts instead of forcing everything into movie/show/music categories.

### Heavy Work Runs In Jobs

Jellyfin exposes scheduled and manual tasks such as library scan, chapter image extraction, keyframe extraction, database optimization, and cache cleanup. Plex similarly runs periodic scans, preview thumbnail generation, chapter thumbnail generation, and other analysis during configured maintenance periods.

HugCivi already has the correct internal shape with `library_reindex`, `media_thumbnail_backfill`, poster/transcode jobs, and download jobs separated by `job_kind`. The missing product layer is scheduling and policy around these jobs.

### Thumbnail Generation Is Explicitly Expensive

Plex documents video preview thumbnails as CPU- and storage-heavy, with typical index files measured in tens of MB per media item. Jellyfin 10.10 added faster trickplay generation via optional keyframe extraction, but it remains an explicitly enabled feature.

HugCivi's current card thumbnail system is intentionally lighter: one cached JPEG thumbnail per representative card image, requested only near the viewport, with cold generation concurrency capped. That should stay the default. Any future video timeline preview or trickplay cache should be opt-in, scheduled, quota-managed, and source/folder scoped.

### File Watching Is Useful But Not Universal

Jellyfin supports real-time monitoring where the underlying filesystem supports it, but documents inotify limits and unsupported network/rclone cases.

HugCivi should keep explicit DB reindexing as the reliable default. Filesystem event watching can be added later as an optional accelerator for known-local mounts, not as a replacement for reindex jobs.

## HugCivi Follow-Up Plan

### 1. Stronger Local `/config` Guidance

Update operations/configuration docs to say that `/config` should live on local SSD or other fast local storage whenever possible. Explain that SQLite, job state, thumbnails, and future cache metadata are latency-sensitive.

### 2. Cache Quotas And Cleanup

Add an operator-visible cache policy for generated thumbnails:

- maximum thumbnail cache bytes
- optional age-based cleanup
- least-recently-used cleanup based on file access or metadata
- manual clear controls

The implementation should keep cache files on disk and avoid storing large binary blobs in SQLite.

### 3. Maintenance Windows For Heavy Work

Add a policy layer for expensive internal jobs:

- run immediately
- run only during a configured maintenance window
- manual-only

Initial candidates are library reindex, thumbnail backfill, media poster generation, transcode jobs, and any future video preview generation.

### 4. Folder-Level Scan State

Add or extend scan state so HugCivi can reason about folders, not only indexed items. A future folder index should store normalized path, parent path, last scan timestamps, completion state, and lightweight signatures where safe.

This supports faster "what changed?" decisions and better UI messaging for unindexed folders.

### 5. Optional Filesystem Watcher

Add only after the folder-level scan state exists. Keep it opt-in and local-mount-oriented. Network storage, rclone, and other watcher-hostile environments must continue to rely on explicit reindex.

### 6. Opt-In Video Preview/Trickplay

If HugCivi adds video timeline previews, keep them disabled by default. Use per-source or per-folder controls, scheduled generation, storage estimates, cache quotas, and a fast keyframe-only mode where practical.

## External References

- [Jellyfin Configuration](https://jellyfin.org/docs/general/administration/configuration/)
- [Jellyfin Storage](https://jellyfin.org/docs/general/administration/storage/)
- [Jellyfin Tasks](https://jellyfin.org/docs/general/server/tasks/)
- [Jellyfin Troubleshooting](https://jellyfin.org/docs/general/administration/troubleshooting/)
- [Jellyfin Libraries](https://jellyfin.org/docs/general/server/libraries/)
- [Jellyfin 10.10 release notes](https://jellyfin.org/posts/jellyfin-release-10.10.0/)
- [Plex data directory](https://support.plex.tv/articles/202915258-where-is-the-plex-media-server-data-directory-located/)
- [Plex library settings](https://support.plex.tv/articles/200289526-library/)
- [Plex video preview thumbnails](https://support.plex.tv/articles/202197528-video-preview-thumbnails/)
- [Plex local media assets](https://support.plex.tv/articles/200220677-local-media-assets-movies/)
- [Plex server directory size](https://support.plex.tv/articles/202529153-why-is-my-plex-media-server-directory-so-large/)
