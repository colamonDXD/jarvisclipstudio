# Creator-Name Wiring for Hook Titles — Report (2026-11-11)

**Lane:** creator-name wiring (Luffy's literal ask: titles should read like "TJR did this"). Branch `feature-creator-name-titles` (worktree `~/workspace/jcs-creatorname`), off next-milestone `50045d6` (the merged first-person titles fix). **Not merged — main merges after verification.** Commits: `d689a67` (capture), `eb2060b` (create_clip resolution), `7113763` (analysis threading), `14a18c1` (tests), `af9eba4` (changelog). **No database migration** — the name lives in the task's existing `source_metadata` JSONB. No `classify.py`, no `task_repository.py` INSERT changes, no template/reframe code touched.

## Step-0 premise map — where a creator name exists, per task type

| Task type | Name source | Verdict |
|---|---|---|
| Manual YouTube URL task (`POST /tasks`) | The route already fetches full yt-dlp metadata in `_preflight_youtube_source` → `async_get_youtube_video_info` (youtube_utils.py): `uploader` is populated from yt-dlp's `info["uploader"]`, or from the YouTube API's `snippet.channelTitle` on the API path. Only `title`/`duration` were consumed; the name was discarded. | ✅ Reliable — now captured |
| Manual Twitch/Kick URL task | `_preflight_stream_source` returns the stream metadata dict; the route now reads `uploader`/`channel` opportunistically from it. | ✅ Captured when present |
| Channel-monitor task | `create_task_from_channel_video` runs the same yt-dlp preflight (`_preflight_duration` → `async_get_youtube_video_info`); the monitor's channel is the video's channel by construction. | ✅ Reliable — now captured |
| Watch-folder task | Local files dropped in a folder; no metadata fetch exists. | ❌ No name — neutral titles (correct) |
| File-upload task (`upload://`) | `_preflight_upload_source` measures duration only; an uploaded file has no channel. | ❌ No name — neutral titles (correct) |
| Tasks created before this change | Their `source_metadata` has no `creator_name` key. | ❌ Neutral (unchanged behaviour) |

**Storage:** `tasks.source_metadata` JSONB already exists, with a merge-update writer (`TaskRepository.save_source_metadata`) and reader (`load_source_metadata`) already used for preflight facts (`stream_resolution`, job ids, park stamps). The name is stamped under the `creator_name` key at creation. No schema change, no migration.

**Deliberately NOT wired:** the AI-edit `hook_title` setting (`ai_edit_service`). Its job runs on the AI worker from a Redis payload containing only the editor document + instruction; `EditDocument` carries no clip/task/source identity, so the name is genuinely unreachable there without changing the job contract. Its guardrail still strips first person (neutral) — safety holds, only attribution is absent on that one path.

## What was threaded

1. **Capture (one seam):** `TaskService.create_task_with_source(..., creator_name=None)` — after task creation, the cleaned name (`title_person.clean_creator_name`: whitespace-collapsed, ≤40 chars, first-person "names" rejected) is stamped into `source_metadata`. Best-effort: a failed stamp never fails task creation. Callers passing it: the manual route (YouTube `uploader`/`channel` from the preflight it already ran; streams opportunistically) and the channel monitor (its preflight's `uploader`/`channel`). Watch folder passes nothing.
2. **Persistence write path:** `ClipRepository.create_clip` — when no caller passes `creator_name`, it resolves the task's stored name (`SELECT source_metadata FROM tasks`, parsed via `title_person.creator_name_from_metadata`, dict or JSON-string tolerant) before running the guardrail. Best-effort: any lookup failure → None → neutral rewrite, never a failed clip. This covers all three create_clip callers (main pipeline, clip regeneration, editor split) with zero caller changes. An explicitly passed name still wins over the stored one.
3. **Analysis path (the burned-in title):** the pinned hook title rendered into the video comes from the analysis segment, which is sanitised before rendering — so the name is threaded alongside the existing `source_title` route: `TaskService.process_task` (loads it from the task's stored metadata) → `VideoService.process_video_complete` → `VideoService.analyze_transcript` → `ai.get_most_relevant_parts_by_transcript` → `ai._validate_segments` → `sanitize_hook_title(creator_name=...)` (parameter already existed). Stored title and burned-in title now agree. All new parameters are optional and default to the previous behaviour.

## Before → after (through the REAL create_clip path, fake DB serving a stamped task row)

| Stored task metadata | Raw title | Stored after this lane | Before this lane |
|---|---|---|---|
| `{"creator_name": "TJR"}` | I Try the World's Spiciest Ramen | **TJR Tries the World's Spiciest Ramen** | Trying the Spiciest Ramen |
| `{"creator_name": "TJR"}` | My Morning Routine | **TJR's Morning Routine** | The Morning Routine |
| `{"creator_name": "TJR"}` | I Met a Millionaire in LA | **TJR Met a Millionaire in LA** | Met a Millionaire in LA |
| `{}` / none / task row missing | I Bought the Most Expensive Sandwich | Bought the Most Expensive Sandwich (neutral, unchanged) | same |
| `{"creator_name": "TJR"}` | $400 Wagyu Sandwich With Real Gold | unchanged, byte-identical | same |
| `{"creator_name": "TJR"}` | Walked 25 Mins for a $20k Gym Tour | unchanged, byte-identical | same |
| explicit param `creator_name="MrBeast"` beats stored `TJR` | I Spent 24 Hours in a Haunted House | MrBeast Spent 24 Hours in a Haunted House | — |
| metadata lookup raises | I Bought the Most Expensive Sandwich | Bought the Most Expensive Sandwich (clip still created) | — |

Capture path (stubbed-repo `create_task_with_source`): `creator_name="TJR"` → `save_source_metadata(db, task_id, {"creator_name": "TJR"})`; `"  TJR   Clips "` → stored `"TJR Clips"`; absent or unusable (`"I"`) → nothing stamped.

## Test evidence (serial, small batches, sandbox venv, TMPDIR on workspace)

- `tests/unit/test_creator_name_titles.py` (new, 14 tests: metadata helper incl. JSON-string form + fallback keys, capture through the real `create_task_with_source` with stubbed repos, attribution/neutral/byte-identity/precedence/failure-safety through the real `create_clip`, analysis-seam `sanitize_hook_title` with a creator): **14 passed / 0 failed**.
- `tests/unit/test_title_person.py` (the titles-fix suite, unmodified — its fake DB exercises the new lookup's failure fallback): **62 / 0**.
- Neighbours: `test_task_service.py` + `test_video_service.py` **47 / 0**; `test_ai_edit.py` + `test_hook_title_styles.py` **51 / 0**; `test_channel_monitor.py` + `test_channel_monitor_stage2.py` **40 / 0**; `test_creation_modes_pipeline.py` + `test_clip_rerender.py` + `test_editor_service.py` + `test_social_caption_service.py` **37 / 0**; `test_creation_modes_routes.py` + `test_ai_grounding.py` **9 / 0**; `test_watch_folder.py` + `test_watch_folder_music_mode.py` + `test_creation_modes.py` **88 / 0**.
- CHANGELOG: `**Improved:**` entry added under 2026-11-11; `frontend/src/lib/changelog.test.ts` **4 passed / 0 failed** (node_modules borrowed via temporary symlink from jcs-rc, removed after).
- **Lane total: 348 passed / 0 failed. No existing test was modified.**

## State

IMPLEMENTED / INDIVIDUALLY TESTED / INTEGRATED (into the branch) — USER-MACHINE VALIDATION PENDING (ships in the next package; Luffy's live v3+hotfix build does not have it). Delivery shape when main approves: all changes are backend Python (bind-mounted) + CHANGELOG — the changed backend files can go as a small patch + `docker compose restart backend worker`, same as the INSERT hotfix. Note the visible effect needs a NEW task created after the patch (the name is captured at creation); re-running an older task keeps neutral titles.
