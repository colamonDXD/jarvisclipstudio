# SETTINGS INTEGRITY AUDIT — Day-1 Lane (Independent Verification)

**Date:** 2026-11-12 (IST) · **Tree audited:** `next-milestone` @ `025ffe9`
(worktree `~/workspace/jcs-audit-settings`, branch `audit-settings-integrity`)
· **Pre-fix reference tree:** `bc2f230` (scratch worktree
`~/workspace/jcs-audit-prefix`) · **Scope:** every user-selectable setting,
traced frontend → API → validation → DB write → DB readback → worker →
pipeline → renderer → exported-output effect.

**Method note.** Nothing in this audit trusts the fix lane's report. The
defect was re-derived from the pre-fix tree, the fix re-proven on the
current tip through the repository's own methods, and the one tier the
fix lane could not run (live PostgreSQL) was run here. Logs:
`~/workspace/audit-logs/` (`prefix-repro.log`, `tip-readback.log`,
`tip-pg-roundtrip.log`, `pg-family.log`, `neighbours.log`,
`matrix-1.log`).

---

## 1. STEP 0 — INDEPENDENT VERDICT ON THE READBACK FIX: **HOLDS**

### 1.1 The original defect, reproduced on the pre-fix tree (bc2f230)

The fix lane's regression suite (`test_task_settings_readback.py`, which
does not exist at bc2f230) was copied into the pre-fix worktree and run
unchanged. Result: **5 failed, 1 passed, 1 skipped** — identical to the
lane's claim. The stored row provably holds the settings while the read
dict drops them:

```
>       assert task.get("template_mode") == "plain"
E       AssertionError: assert None == 'plain'
E        +  where None = <built-in method get of dict object>('template_mode')
tests/unit/test_audit_repro_readback.py:129: AssertionError
```

The pipeline-level failure is the production symptom in miniature: an
EDL built from a round-tripped Plain task's dict on bc2f230 is a full
Auto plan (profile music bed `music_mood: 'calm'`, B-roll cues planned,
no `template` stamp). The 1 pass is the legacy-NULL test (None reads as
None on both trees, by design). The 1 skip is the PostgreSQL test (no
`SQL_EXEC_PG_URL` in that run).

### 1.2 The fix, re-proven on the current tip (025ffe9)

| Run | Env | Result |
|---|---|---|
| `test_task_settings_readback.py` | ExecSession (default) | **6 passed, 1 skipped** |
| `test_postgres_round_trip_task_settings` solo | live PG 16.15 | **1 passed in 0.44s** |
| Readback suite with PG URL | live PG 16.15 | 6 ExecSession + PG round-trip all pass (dots in `tip-readback-pg.log`) |
| SQL-exec family (11 files) + readback + matrix | live PG 16.15 | **151 passed, 7 xfailed, 0 skipped, 0 failed** |
| Neighbour batch (toggle, readback, matrix, source-settings, niche-override, separation) | default | **100 passed, 1 skipped** (the PG test, expected) |

**PG tier — the inch the fix lane left open is now closed.** The lane's
sandbox had lost its PostgreSQL binaries; this sandbox has PostgreSQL
**16.15** installed. A scratch cluster was stood up per the
POSTGRES_TIER_GATE_2479942 recipe (user-namespace, overflow-`nobody`
owner — the hand-written uid-map writes were refused again, exactly as
that report records; data dir under `~/workspace/.pg-tier/`, port
55433, db `sql_exec_tier`). Against it, the readback PG test executes
the **real** `create_task` → `get_task_by_id` / `get_user_tasks`
against real BOOLEAN/INTEGER columns: `template_mode='plain'`,
`music_mode='off'`, `broll_enabled is False`,
`separation_opt_in is True`, `source_duration_seconds=2365` all
survive. The 151 total reconciles exactly: 135 (the 2479942 gate
family, unchanged) + 7 (readback incl. the live PG test) + 9 (the new
matrix suite, §2).

### 1.3 What the fix is (verified in the diff, not the report)

Serializer-only, `backend/src/repositories/task_repository.py`:
`get_task_by_id` gains the four keys at :290 (`separation_opt_in`),
:294 (`music_mode`), :295 (`template_mode`), :297-298
(`source_duration_seconds`); `get_user_tasks` gains the same four at
:591-599 — both mirroring the `broll_enabled` getattr pattern the
toggle lane had added at :289/:593. No SQL text changed (inventory
guard + insert-columns suites pass with unchanged statement counts,
in the 151 above). Legacy NULL rows still read as None (pinned by
`test_legacy_null_settings_read_back_as_none`).

### 1.4 Combination matrix on the real path (new suite)

`backend/tests/unit/test_settings_integrity_matrix.py` (this lane,
**9 passed**) drives repository round-trips → the render loop's exact
reads (`task_service.py:2313-2317` project_* values →
`build_pipeline_edl`, the readback suite's reproduction pattern):

| Combination | Proven outcome |
|---|---|
| Plain × Music **off** (readback suite) | Plain EDL, silent, zero cues, zero stock calls |
| Plain × Music **on** (matrix) | Plain EDL, **silent** — the template contract beats the music control (`template_mode.py` docstring), stored `broll_enabled=True` cannot switch B-roll on either |
| Plain × Music **auto/NULL** (matrix) | Plain EDL, silent, zero cues |
| Auto × Music **off** (readback suite) | Silent EDL (`music_source='user_none'`, volume 0.0) |
| Auto × Music **on** (matrix) | Audible bed (source ≠ `user_none`, volume > 0) |
| Auto × Music auto + override | Override branch wins over profile bed (`video_service.py:1311-1344`, chain order off → override → on → auto) |
| Auto × B-roll **explicit off** (matrix) | Zero cues **and zero stock-provider calls** (gate sits above cue planning) |
| Auto × B-roll **NULL** (matrix) | Template default On: cues planned, resolver called once |
| Auto × B-roll **explicit on** (matrix) | Cues planned, resolver called once |
| separation on/off | Both survive the round-trip (readback suite); consumer chain §3 row 6 |
| niche_override set/unset | Survives (matrix full-surface test); render loop reads it at `task_service.py:2313` |
| Legacy NULL row | Reads as None → documented legacy rule (behaves as Auto), pinned |
| **Invalid stored value** (`'bogus'` planted by direct UPDATE) | Serializer carries it faithfully; downstream it resolves as **Auto** — see finding **F-2** |

### 1.5 Lifecycle paths — all read through the same fixed path

Every pipeline consumer loads the task via `get_task_by_id` (grep-
verified across `task_service.py`, `clip_service.py`,
`api/routes/tasks.py`); `get_user_tasks` has exactly **one** consumer,
the task-list route (`tasks.py:561`).

- **Process:** `task_service.py:1878-1879` (settings_row: target count,
  separation :1894, brief, creation mode), render loop :2311-2317.
- **Retry (route):** `tasks.py:2790` re-reads via `get_task_by_id` and
  rebuilds the job arguments from it (fonts, caption template,
  processing mode) + source_metadata (output format, subtitles,
  cleanup, clip length) at :2859-2883.
- **Resume / moment-pick:** `task_service.py:619` `apply_moment_selection`
  loads via `get_task_by_id` (:642) + `load_source_metadata`, re-enqueues
  at :762 with the same rebuilt arguments.
- **Re-render / regenerate (per clip):** `clip_service.py` loads via
  `get_task_by_id` (:175/:685/:1192/:1557/:1834/:1989); the re-render
  EDL builder reads `task.get("template_mode")`,
  `task.get("broll_enabled")` (:218, :816-818, :836, :1411-1415) and
  the Brand Kit Plain guard (:492).
- **Creation sources:** API form (`tasks.py:846`), channel monitor
  (`channel_monitor_service.py:697` — passes the monitor row's own
  template/music/niche/creation/target/brief/campaign/brand values;
  the monitor repository serialises `music_mode`/`template_mode` from
  its row at `channel_monitor_repository.py:52-54`), watch folder
  (`watch_folder_service.py:413` — folder settings: processing mode,
  niche, music fields via `music_task_fields` :71, target count; it
  exposes no template/B-roll/separation control, so those store
  NULL/false and take the documented defaults). All three funnel into
  `TaskService.create_task_with_source` → the single
  `TaskRepository.create_task` INSERT (`task_service.py:1090`).

---

## 2. SETTINGS REGISTER — the 9-stage trace

Stages: **FE** frontend field · **API** API field + validation ·
**DB** column/store · **RB** readback path · **WK** worker/pipeline
use · **FX** renderer/output effect. Status: **PROVEN** (evidence
named) · **ABSENT-BY-DESIGN** · **DEFECT** (see §4).

| # | Setting | FE | API / validation | DB | RB | WK → FX | Evidence | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | template_mode (Auto/Plain) | home-app.tsx:468 (always sent) | tasks.py:670 `validate_template_mode` (422 on garbage) | tasks.template_mode | get_task_by_id :295 / get_user_tasks :592 | render loop task_service:2316 → plainify (template_mode.py:119 stamps `template='plain'`, strips bed/B-roll/transitions/LUT/brand) | readback + matrix suites | **PROVEN** |
| 2 | music_mode (auto/on/off) | home-app.tsx:464 (omitted when auto) | tasks.py:666 `validate_music_mode` | tasks.music_mode | :294 / :591 | render loop :2315 → music chain video_service:1311-1344 (off → `apply_music_off`; on → profile/auto bed; auto → auto bed) | readback + matrix | **PROVEN** |
| 3 | music_override (picked track) | home-app.tsx:461 | tasks.py:662 `validate_music_override` | tasks.music_override | serializer (both) | render loop :2314 → override branch (loses to mode=off and to Plain); clip_service:909/:1415 Plain guard | matrix full-surface; music suites | **PROVEN** |
| 4 | broll_enabled (per-task switch) | home-app.tsx:472 (forced false for Plain) | tasks.py:671 `validate_broll_enabled` | tasks.broll_enabled (migration 20261112_0001) | :289 / :593 | `resolve_broll_enabled` gate above cue planning (broll_setting.py); OFF ⇒ zero cues + zero stock calls | test_broll_toggle.py (23) + matrix | **PROVEN** |
| 5 | include_broll (legacy styling flag) | styling editor | update route tasks.py:2514 → clip_service:141 → repo `update_task_settings` | tasks.include_broll | serializer (both paths read via get_task_by_id) | clip_service:217 ANDs it with `resolve_broll_enabled` on regenerate | matrix full-surface | **PROVEN** |
| 6 | separation_opt_in | create form toggle | tasks.py:672 `validate_separation_opt_in` (checked against the deployment flag) | tasks.separation_opt_in | :290 / :594 | task_service:1894 read → :2095 transcription separation + :2353 vocals-stem gate | readback suite; test_separation*.py family | **PROVEN** |
| 7 | niche_override (task) | home-app.tsx:459 | tasks.py:658 `validate_niche_override` | tasks.niche_override | serializer (both) | render loop :2313 `project_niche_override`; process :2099 | matrix full-surface; test_niche_override.py | **PROVEN** |
| 8 | niche_override (per clip) | clip re-render UI | tasks.py:1641 re-render route — required, validated (422) | generated_clips.niche_override | clip_repository get_clip_by_id (explicit column list = dict keys, verified) | re-render rebuilds the clip's profile styling | test_niche_override.py; rerender suites | **PROVEN** |
| 9 | target_clip_count | home-app.tsx:476 | tasks.py:678 `validate_target_clip_count` | tasks.target_clip_count | serializer (both) | process_task **re-validates the stored value** and warns on invalid (task_service:1882-1890) → clip-count resolution | matrix full-surface | **PROVEN** |
| 10 | clip_length (duration preset) | home-app.tsx:449 | tasks.py:654 `validate_clip_length` | tasks.source_metadata JSONB (saved tasks.py:885) | `load_source_metadata` | task_service:611/:776, clip_service:308/:1080/:1466/:2642 `normalize_clip_length(metadata…)` → process :2465 `max_clip_seconds` | source-settings suites | **PROVEN** |
| 11 | creation_mode + manual_ranges | home-app.tsx:482/:485 | tasks.py:713-714 validators | tasks.creation_mode / manual_ranges_json | serializer (both) | process **re-validates both with loud warnings** (task_service:1916-1935); `plan_for_mode` :895 gates the moment-pick park | matrix full-surface; creation-mode suites | **PROVEN** |
| 12 | content_brief (allowed/excluded ranges, flag points, key timestamps) | brief paste/upload + screenshot draft | tasks.py:687 `validate_content_brief` | tasks.content_brief (JSON) | `_parsed_brief` in both serializers | process `normalize_brief_v2` + warn-on-malformed (:1896-1910); excluded ranges hard-block picking/slate/render, flags stamp `needs_review` | test_content_requirements_* family (parser/matcher/compliance/extract/brief-image) | **PROVEN** |
| 13 | campaign_id | brief/campaign flow | create kwargs | tasks.campaign_id | serializer (both) | campaign/brief compliance reporting | compliance suites | **PROVEN** |
| 14 | brand_kit_id + intro/outro assets | home-app.tsx:474 | create kwargs; unknown/foreign kit **refused** (task_service:1041-1048) | tasks.brand_kit_id / intro_asset / outro_asset (kit values filled at creation :1119-1121) | serializer (get_task_by_id) | clip_service:492 — brand blocks applied for Auto, **suppressed for Plain**; plainify also strips kit blocks | matrix full-surface; brand-kit suites | **PROVEN** |
| 15 | font_family / font_size / font_color + caption_template | home-app.tsx:445 (`font_options`) + template picker | tasks.py:633-638 normalisers (400 on non-object) | tasks.font_* / caption_template | serializer (get_task_by_id); styling updates via `update_task_settings` | job arguments at create/resume → video_service clip build (:1546-1549) → caption rendering | matrix full-surface | **PROVEN** (list dict omits them — F-3, consumer-free) |
| 16 | processing_mode (quality: fast/balanced/quality) | autopilot settings page + monitor section expose it; **main create form hardcodes `"fast"`** (home-app.tsx:447/:513) | monitor/watch-folder validate against the 3-value set with config fallback (channel_monitor_service:691-693) | tasks.processing_mode | serializer (both) | clip_service cache keys :541/:2348 (`or config.default`); process checkpoint key task_service:1965 | matrix full-surface | **PROVEN** for API/autopilot/monitor creators; form exposure **ABSENT-BY-DESIGN** (F-5) |
| 17 | output_format (aspect: vertical/original) | create form | tasks.py:647-649 — invalid value **coerced to `"vertical"`** (F-4) | source_metadata (merge helper re-checks membership, tasks.py:363-364) | `load_source_metadata`; resume rebuilds from it | video_service:1014 (`== "original"` branch) / :1071 → render size | source-settings suites | **PROVEN** with F-4 caveat |
| 18 | add_subtitles | create form | tasks.py:650-652 — non-bool **coerced to True** (F-4) | source_metadata | as above | worker arg → process_task → caption burn-in decision | source-settings suites | **PROVEN** with F-4 caveat |
| 19 | cleanup settings (cut_long_pauses, pause_threshold_ms, remove_filler_words, filtered_words) | create form | clip_cleanup.py:98-109 `new_task_cleanup_settings` (omitted toggles get documented new-task defaults; explicit false honoured) | source_metadata (merged keys) | `load_source_metadata`; `updated_cleanup_settings` for task updates (:112) | clip_service:217/:227 cleanup recompute gate → EDL silence/filler tightening | cleanup suites | **PROVEN** |
| 20 | source_duration_seconds | (derived at preflight, not typed) | `charged_duration` at create (task_service:1036-1041 measures when absent) | tasks.source_duration_seconds | :297-298 / :598-599 | task_service:1804 stream seeding; clip_service:278/:567/:2386; editor_service:547 | readback suite (stored 2365 → read 2365, ExecSession **and** live PG) | **PROVEN** |
| 21 | hook enforcement | — (no per-task control) | — | env `HOOK_ENFORCEMENT_ENABLED` | hook_enforcement.py:55-57 (default on) | hook guardrail in the EDL/title path (:283) | test_hook_enforcement.py | **ABSENT-BY-DESIGN** per-task; global PROVEN |
| 22 | voiceover (per-clip request) | clip editor | tasks.py:1691/:2144 validator; refused unless deployment flag on | request-scoped (not task-stored) | n/a | media/voiceover.py:59 `VOICEOVER_ENABLED` (default OFF) gates Kokoro VO | voiceover suites | **PROVEN** as designed |
| 23 | clip editor settings (caption/look/audio overrides, SFX placements) | clip editor UI | tasks.py:1686-1692 validators | sidecar store next to the clip (clip_source_map.py:149 save / :163 load) | loaded at every clip_service re-render path (:710/:1129/:1205/:1586/:1839) | re-render EDL rebuild consumes them | editor suites | **PROVEN** |
| 24 | model selection / LLM provider | admin UI (app/admin/page.tsx + api/admin/runtime-settings) | runtime-settings API | runtime settings store (config.py `_get_runtime_setting` / `as_runtime_settings`) | `load_runtime_settings_cache` at worker start (workers/tasks.py) | LiteLLM gateway chains (ladder v2: DeepSeek → Groq-120B → NIM Ultra → NIM Flash → OpenRouter :free → Groq-20B → Ollama) | ladder v2 suites + drills | **PROVEN** (global, not per-task — by design) |
| 25 | watch-folder settings (folder, processing_mode, niche, music, target count) | settings/autopilot page | watch_folder_service settings read + 3-value mode validation | folder settings store | settings dict at task creation (:413) | tasks created with those values; template/B-roll/separation not exposed → NULL defaults (documented legacy rule) | watch-folder suites | **PROVEN** with noted absences |
| 26 | language | — | — | — | — | transcription language is auto-detected (no field exists in the create route or form — grep-verified) | — | **ABSENT-BY-DESIGN** |

**Register totals:** 26 entries — **22 PROVEN end-to-end**, 3
ABSENT-BY-DESIGN (16-form-exposure, 21, 26 — each stated plainly, none
masquerading as a working control), 1 PROVEN-with-caveats pair counted
inside rows 17/18 (F-4). **No new dropped-field defect of the readback
class was found**: the create surface of `create_task` (25 columns)
was diffed field-by-field against both serializer dicts — after the
fix, every create-written field with a downstream consumer is carried
by `get_task_by_id`; the matrix full-surface test now pins all of them
in one round-trip so a future serializer edit cannot silently drop
one again.

---

## 3. FAIL-SAFE TABLE — missing / invalid at pipeline time

The directive's rule: *a missing setting must never silently switch
modes.* The codebase already has the intended pattern in one place —
`process_task` **re-validates stored values and logs a warning** for
target_clip_count (:1882-1890), content_brief (:1903-1910),
creation_mode and manual_ranges (:1916-1935). The table below judges
every setting against that standard.

| Setting | Missing (NULL) → | Invalid stored → | Documented? | User told? | Verdict |
|---|---|---|---|---|---|
| template_mode | Behaves as Auto — the documented legacy rule (template_mode.py module docstring; the form always sends it, so NULL = legacy/non-form only) | **Silently behaves as Auto** — render-loop reads are raw `.get()` (task_service:2316), no re-validation, no log | NULL rule yes; invalid rule **no** | No | **F-2 (LOW)** |
| music_mode | Auto bed — documented (form omits = auto; music chain else-branch comment, video_service:1337+) | **Silently lands in the Auto branch** (any unrecognised string) | Partially (branch comment names the else "Music = Auto") | No | **F-2 (LOW)** |
| broll_enabled | Template default (Auto on / Plain off) — documented in broll_setting.py module docstring | n/a (boolean column; validator 422s non-booleans at the API) | Yes | n/a | OK |
| separation_opt_in | Off — `bool()` coercion with an explicit comment ("Anything malformed reads as off — the original-audio path", task_service:1892-1894) | Same documented coercion | Yes | No (safe direction: original audio) | OK |
| target_clip_count | Config/default count | **Re-validated; warning logged** with the offending value (:1885-1890) | Yes | Log only | OK — the exemplar |
| creation_mode / manual_ranges | Legacy pipeline (no mode) — commented at :1913-1915 | **Re-validated; warning logged** (:1922-1935) | Yes | Log only | OK |
| content_brief | No brief enforcement | **Warning logged**, brief ignored rather than enforced wrongly (:1907-1910) | Yes | Log only | OK |
| niche_override | Classifier decides | Unknown profile falls back defensively (build_pipeline_edl docstring, video_service:1129-1138) | Yes | No | OK |
| output_format | "vertical" | **Coerced to "vertical" at the API** without error (tasks.py:647-649) | No | No | **F-4 (LOW)** |
| add_subtitles | True | **Coerced to True at the API** for non-booleans (:650-652) | No | No | **F-4 (LOW)** |
| processing_mode | `config.default_processing_mode` at every read (clip_service:541-542 etc.) | Monitor/watch-folder creators re-validate against the 3-value set with config fallback | Yes | No | OK |
| brand_kit_id | No kit applied | Unknown/foreign kit **refused at creation** (ValueError, task_service:1045-1048) — never silently ignored | Yes (code comment) | Yes (creation fails) | OK |
| clip_length | "auto" via `normalize_clip_length` | Normaliser maps unknown → "auto" (documented preset semantics) | Yes | No | OK |

---

## 4. FINDINGS

**F-1 — The readback defect (CRITICAL, fixed before this audit,
independently verified here).** `template_mode`, `music_mode`,
`separation_opt_in`, `source_duration_seconds` were written at
creation and dropped by both task serializers, so shipped v3/v4/v5
rendered every Plain task as Auto, ignored Music Off, and never
separated. Fix @ ef87820 (serializer-only, +14 lines). This audit
reproduced the defect on bc2f230 (5 failed / 1 passed / 1 skipped,
`assert None == 'plain'`), re-proved the fix on 025ffe9, and closed
the live-PostgreSQL tier the lane could not run (PG round-trip 1
passed; family 151/7/0). Old tasks heal on re-render because the
re-render paths read the same dict. **Status: CLOSED.**

**F-2 — Render-loop reads of template_mode / music_mode lack the
re-validate-and-warn treatment (LOW).** `process_task` re-validates
stored target_clip_count, brief, creation_mode and manual_ranges with
loud warnings, but the render loop (task_service:2313-2317) and the
clip_service re-render builders read template/music raw. An
unrecognised stored value silently behaves as Auto (pinned by
`test_unrecognised_stored_values_resolve_as_auto`). Unreachable
through the API today (route validators 422 garbage; both columns are
only written by `create_task`), so this is corruption/future-writer
insurance, not a live user bug. **Proposed fix (not built here — it
touches resolution call-sites, beyond serializer class):** apply
`validate_template_mode` / `validate_music_mode` with the existing
try/warn pattern where the settings_row is first read in
`process_task`, and pass the validated values down, mirroring
target_clip_count exactly.

**F-3 — `get_user_tasks` projection omits styling/brand fields
(INFO — verified non-defect).** The list dict lacks font_family,
font_size, font_color, caption_template, include_broll, brand_kit_id,
intro/outro_asset, manual_ranges_json (fix lane noted the same).
Verified consumer-free: the dict's only consumer is the task-list
route (tasks.py:561); both resume paths and every pipeline reader use
`get_task_by_id`. Pinned by
`test_get_user_tasks_projection_carries_pipeline_fields` so a future
consumer added against the list dict fails loudly here first.
**Status: documented, no fix.**

**F-4 — API silently coerces invalid output_format / add_subtitles
(LOW).** tasks.py:647-652 substitutes `"vertical"` / `True` instead of
returning the 422 every sibling validator returns. A malformed client
gets a different aspect/subtitle choice than it asked for, with no
signal. Benign for the shipped frontend (it only sends valid values).
**Proposed fix:** raise 422 like the sibling validators, or log a
warning at the coercion site; either is a one-site change for a
follow-up lane.

**F-5 — processing_mode is not exposed on the main create form
(INFO).** home-app.tsx hardcodes `processing_mode: "fast"` (:447,
:513); the value is honoured end-to-end when supplied by the
autopilot settings, channel monitors, or API/MCP clients. Whether the
create form should offer balanced/quality is a product decision, not
a correctness defect. **Status: listed for the parent.**

---

## 5. WHAT THIS LANE CHANGED

- **New test file only:** `backend/tests/unit/test_settings_integrity_matrix.py`
  (9 tests, all passing; §1.4/§2). No production code was changed —
  the audit found **no** provable same-class defect left to fix:
  F-2/F-4 are listed with proposed fixes per the lane's scope rule
  (minimal serializer-class diffs only in-lane).
- **No CHANGELOG entry:** test-only change with no product-behaviour
  change (house rule exempts it; stated here as the reason).
- Suites touched stay green: readback 6+1 PG, matrix 9, toggle 23,
  SQL family 135 + settings 16 under live PG (151 total), neighbour
  batch 100 passed / 1 skipped.

## 6. EVIDENCE INDEX

- `~/workspace/audit-logs/prefix-repro.log` — bc2f230: 5 failed / 1 passed / 1 skipped.
- `~/workspace/audit-logs/tip-readback.log` — 025ffe9 default env: 6 passed / 1 skipped.
- `~/workspace/audit-logs/tip-pg-roundtrip.log` — live PG solo: 1 passed.
- `~/workspace/audit-logs/pg-family.log` — live PG family + settings: 151 passed / 7 xfailed / 0 skipped.
- `~/workspace/audit-logs/matrix-1.log` — matrix suite: 9 passed.
- `~/workspace/audit-logs/neighbours.log` — 100 passed / 1 skipped.
- Scratch cluster: PostgreSQL 16.15, was `~/workspace/.pg-tier/`, port 55433 — stopped and removed at lane teardown; re-stand it per the POSTGRES_TIER_GATE_2479942 recipe when the Day-3 gate needs it.
