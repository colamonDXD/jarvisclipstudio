# First-Person Hook Titles Fix — Report (2026-11-11)

**Lane:** recovery of the stalled titles lane. Branch `fix-hook-titles-person` (worktree `~/workspace/jcs-titles`), off next-milestone `6362cfe`. Commits: `3b34313` (fix) + `bdaabd8` (changelog). **Not merged — main merges after verification.** Draft verdict: the stalled lane's uncommitted draft was fundamentally sound (module, wiring, tests all coherent); it was finished, not rebuilt.

## The defect
Hook titles copied the speaker's first person from the transcript ("I met this", "I did this", "my routine"), so the CLIPPER appeared to claim the creator's actions. Luffy's order (2026-11-11 ~15:50 IST): strict first priority.

## Generation-site map (audited by grep over backend/src)
Title/hook generation sites, and how each is covered:

| Site | Prompt fix | Guardrail |
|---|---|---|
| `src/ai.py` analysis — main prompt HOOK TITLES section + JSON-only prompt + `TranscriptSegment.hook_title` field description | ✅ third-person instruction in all three | ✅ `_validate_segments` runs `sanitize_hook_title(..., fallback_text=segment.text)` |
| `src/services/ai_edit_service.py` — AI-written headline via the `hook_title` setting | ✅ setting description carries the third-person rule | ✅ `_apply_setting` runs the guardrail (empty still removes the title) |
| `src/repositories/clip_repository.py::create_clip` — persistence choke point | n/a | ✅ guardrail with `fallback_text=text` before the INSERT |

`create_clip` is the ONLY write path for generated titles: its callers are the main pipeline (`task_service.py`), clip regeneration (`clip_service.py`) and editor split (`editor_service.py`) — all covered by the single seam. Audited non-sites: hook variants (`hook_variants.py`, `generate_hook_previews`) are opening-segment variants and never write titles; `update_clip` does not write `hook_title`; `social_caption_service` consumes the stored (already guarded) title for post captions and generates no title; plan/visual-moment paths carry `hook_title=None` placeholders that later pass through the analysis sanitiser.

**Creator-name reality check:** no caller currently passes `creator_name`, and tasks store no channel/uploader field (only the video's `source_title`), so in today's pipeline the neutral rewrite is what runs. The attribution path is implemented and unit-proven for any caller that supplies a name; inventing a name source from the video title would be guessing, so none was wired.

## Guardrail design (`src/auto_director/title_person.py`, pure function)
- Detection: word-boundary regexes only — standalone `I`, contractions `I'm/I've/I'll/I'd`, `we`, `my/our/mine/ours`, object `us` (never all-caps `US`), `me`, `myself/ourselves`. "I" inside words never triggers ("Incredible", "Island" pass).
- Clean titles return **byte-identical** ("Walked 25 Mins for a $20k Gym Tour", "$400 Wagyu Sandwich With Real Gold" unchanged, with or without a creator name).
- Rewrite order: (1) creator attribution when a name is known — subjects/possessives/objects substituted, verb agreement fixed for a curated verb set ("I Try" → "TJR Tries", "I'm Trying" → "TJR Is Trying", "My Routine" → "TJR's Routine", "Watch Me Cook" → "Watch TJR Cook"); (2) neutral headline fragment without a name — pronouns dropped, possessives → "the", leading object-verbs gerunded ("Watch Me Build a House" → "Watching Build a House"); (3) strict gate — if anything first-person survives or fewer than 2 content words remain, a neutral title is derived from the moment's own words (`fallback_text` keywords); (4) last resort constant "Must-Watch Moment". The first-person original is NEVER returned; the result is never empty when any input text exists; None/empty input returns None.

## Before → after (measured, this lane)
| Before | After (no creator) | After (creator) |
|---|---|---|
| I Met a Millionaire in LA | Met a Millionaire in LA | TJR Met a Millionaire in LA |
| I Bought the Most Expensive Sandwich | Bought the Most Expensive Sandwich | TJR Bought the Most Expensive Sandwich |
| I Try the World's Spiciest Ramen | (fragment) | TJR Tries the World's Spiciest Ramen |
| I'm Trying the Spiciest Ramen | Trying the Spiciest Ramen | TJR Is Trying the Spiciest Ramen |
| My Morning Routine | The Morning Routine | TJR's Morning Routine |
| Watch Me Cook | — | Watch TJR Cook |
| I Spent 24 Hours in a Haunted House | Spent 24 Hours in a Haunted House | MrBeast Spent 24 Hours in a Haunted House |
| I Did It | neutral fallback from the moment's words (e.g. "Scored Winning Goal Final Minute" with fallback text; "Must-Watch Moment" with none) | — |
| $400 Wagyu Sandwich With Real Gold | unchanged | unchanged |
| Incredible $20k Gym Tour | unchanged (no false trigger) | unchanged |

Known cosmetic limits (disclosed, all safe): a few no-creator fragments read tersely ("Why Left the Job at Google", "Can't Believe Ate This"); a creator rewrite can double-attribute a possessive ("KSI Gave KSI's Friend a Car" — factually correct, slightly clunky). None ships first person, none is empty.

## Test evidence (serial, small batches, sandbox venv)
- `tests/unit/test_title_person.py` (new, table-driven: detection, false-triggers, clean-pass byte-identity, creator rewrites, neutral rewrites, never-first-person sweep, empty input, all three wirings incl. a fake-DB `create_clip` persistence proof): **62 passed / 0 failed**.
- Neighbours batch 1 (`test_ai_edit.py`, `test_hook_title_styles.py`, `test_retention.py`, `test_editor_document.py`): 107 tests — 106 passed, 1 deliberate expectation update (below), re-run of `test_ai_edit.py` after the update: **45 passed / 0 failed**.
- Neighbours batch 2 (`test_task_service.py`, `test_video_service.py`): **47 passed / 0 failed**.
- Neighbours batch 3 (`test_creation_modes_pipeline.py`, `test_viral_plan_pipeline.py`, `test_clip_rerender.py`, `test_editor_service.py`, `test_social_caption_service.py`): **44 passed / 0 failed** (summary on disk; pytest then hit the known interpreter-exit hang and was killed).
- Neighbours batch 4 (`test_ai_edit_assistant.py`, `test_creation_modes.py`, `test_clip_archive.py`): **104 passed / 0 failed**.
- CHANGELOG: entry added under 2026-11-11 (**Fixed**, third-person titles); `frontend/src/lib/changelog.test.ts` **4 passed / 0 failed** (node_modules borrowed via temporary symlink from jcs-rc, removed after).
- Backend total this lane: **364 passed / 0 failed** after the one deliberate test update.

**Deliberate test change (1):** `tests/unit/test_ai_edit.py::test_settings_are_coerced_clamped_and_validated` pinned an AI-written headline stored verbatim (`"  I biked   to work  "` → `"I biked to work"`). The fix changes that behaviour by design; the expectation was re-derived by hand to the guarded value `"Biked to work"` — same exact-equality assertion, nothing weakened. No other existing test was modified.

## State
IMPLEMENTED / INDIVIDUALLY TESTED / INTEGRATED (into the branch) — USER-MACHINE VALIDATION PENDING (ships in the next package; Luffy's live v3+hotfix build does not have it). Delivery shape when main approves: backend files are bind-mounted, so the changed backend files can go as a small patch + `docker compose restart backend worker`, same as the INSERT hotfix.
