# LEARNING ENGINE — API CONTRACT (for the frontend)

**Audience:** Luffy's Claude Code frontend work. **Backend state:** complete
and verified end-to-end in the sandbox (see "Verification" at the bottom).
**Base:** backend tip `5ea71bf` + branch `feat/learning-engine-backend`
(verification tests only — no backend behaviour changed; everything below
already exists in the shipped backend).

The Learning Engine turns a user's clip verdicts into small, bounded,
reversible adjustments to how THEIR future clips are selected and edited.
It is not model training: adjustments are parameter nudges per
(user, niche), stored in a versioned document, and every consumer treats
"no document" as exactly the pre-learning behaviour.

All paths below are the frontend-visible paths (the Next.js proxy adds
`/api`; the backend routers are `/tasks` and `/learning`). All endpoints
resolve the authenticated user from the same headers as every other task
route, and all data is strictly per-user: one user's feedback can never
affect another user's selection (proven by test, see Verification).

---

## 1. Record an explicit verdict — the accept/reject buttons

```
POST /api/tasks/{taskId}/clips/{clipId}/feedback
```

A client helper already exists and is the intended call:
`sendClipFeedback(taskId, clipId, verdict, reason?)` in
`frontend/src/lib/clip-actions.ts` — it has **no .tsx caller yet**; wiring
it to buttons is the frontend work this contract exists for.

**Request body** (JSON object):

| Field | Type | Rules |
|---|---|---|
| `verdict` | string | Required. `"accept"` or `"reject"` — anything else is a 422. |
| `reason` | string \| null | Optional. Only meaningful on a reject. One of: `hook_weak`, `framing`, `captions`, `music`, `sfx`, `pacing`, `other` — anything else is a 422. |

**Response** (HTTP 200):

```json
{ "recorded": true, "learning_version": 8 }
```

- `recorded: false` (still HTTP 200) means the verdict was NOT stored —
  the engine is disabled (`LEARNING_ENABLED=false`) or storage failed.
  Recording is designed to never fail the user's click; the UI should
  treat `recorded: false` as "no-op", not as an error state.
- `learning_version` is the version of the user's adjustment document
  after this verdict (it advances on every recorded event), or `null`
  when nothing was recorded.

**Errors:** `400` body is not a JSON object · `404` task not found, or
clip not found / clip does not belong to the task · `403` the task
belongs to another user · `422` invalid verdict or reason (the detail
message lists the valid values).

**Semantics for the UI:**
- **Accept** = "more like this." Full-strength evidence (weight 1.0) for
  the clip's niche: its hook/engagement/value/shareability profile, its
  caption preset, and whether its music worked.
- **Reject** = "less like this." Ask for a reason when you can — the
  reason is what makes a reject actionable:
  - `hook_weak` → the engine raises the hook sub-score weight for that niche
  - `pacing` → raises the engagement sub-score weight
  - `sfx` → lowers SFX density for that niche (or raises it on accepts)
  - `music` → enough music rejects (≥5 weighted) mute music for that niche; enough accepts on audible-music clips raise it ×1.25
  - `captions` → enough caption rejects (≥2 weighted) switch that niche to the caption preset the user accepts most elsewhere (needs ≥3 weighted accepts of that preset)
  - `framing`, `other` → recorded as evidence (reject weight) but drive no dedicated channel today
- A verdict is recorded against the clip's **honestly known niche** (clip
  override, else task override). Clips whose niche was only guessed by
  the classifier fallback teach nothing — by design, so the UI never
  needs to explain niche bookkeeping.
- **Not idempotent, deliberately:** the event log is append-only
  evidence; submitting the same verdict twice counts it twice. Treat a
  verdict as one-shot in the UI (disable/replace the buttons after a
  verdict is sent). The blast radius of duplicates is capped: every
  adjustment is bounded (below) and needs multiple weighted signals
  before it moves anything.

## 2. What the engine can change (the adjustments document)

Per niche, any subset of:

| Key | Meaning | Bounds |
|---|---|---|
| `score_weights` | Multipliers on the four virality sub-scores (`hook_score`, `engagement_score`, `value_score`, `shareability_score`) used when ranking candidate moments | each 0.80–1.20 |
| `sfx_density_multiplier` | Scales SFX density in the edit | 0.5–1.5 |
| `music_volume_multiplier` | Scales music volume; `0.0` = learned "keep music off" for this niche | 0.0 or 1.25 |
| `preferred_caption_preset` | Caption preset id learned from the user's accepts elsewhere | a preset id string |

Evidence thresholds (weighted; explicit = 1.0, passive = 0.5): a
sub-score weight steps at 3 / 6 / 10 weighted rejects citing its reason
(+0.10 / +0.15 / +0.20); SFX steps down at 3 / 6 `sfx` rejects, up at
8 / 12 accepts; music needs 5 consistent signals. Below threshold,
nothing changes — the status payload (next section) is how the UI can
show "still learning" honestly instead of pretending.

**Where the adjustments act (verified, not aspirational):**
- **Selection:** candidate moments are ranked by the weighted sub-score
  total for the task's niche (pinned niche up front; auto tasks resolve
  the niche at analysis time — a fallback classification never applies
  learned weights).
- **Editing (EDL):** the niche's profile is adjusted on a copy before
  the edit is built — SFX density, music volume, caption preset. The
  shipped profile files are never mutated.
- Base profiles are never changed on disk; rollback (below) restores any
  prior state.

## 3. Status — show the user what has been learned

```
GET /api/learning/status
```

**Response:**

```json
{
  "enabled": true,
  "current_version": 8,
  "event_count": 8,
  "adjustments": { "meme_comedy": { "score_weights": { "hook_score": 1.15 } } },
  "evidence": {
    "meme_comedy": {
      "events": 8, "accept_weight": 0.0, "reject_weight": 8.0,
      "reason_reject_weights": { "hook_weak": 8.0 },
      "accepts_with_music": 0.0, "caption_accept_weights": {}
    }
  },
  "versions": [
    { "version": 1, "created_at": "…", "event_count": 1, "summary": ["…"] }
  ]
}
```

- `enabled: false` = the kill switch is off; `current_version: null` and
  empty `adjustments` = nothing learned yet (or everything rolled back).
- `versions[].summary` lines are human-readable change descriptions
  (e.g. `"meme_comedy: hook_score selection weight -> 1.15 (was 1)"`) —
  safe to render verbatim.
- Note: a status read may itself advance the version when passive events
  arrived since the last computation — versions are cheap and append-only.

## 4. Rollback — undo to any earlier version

```
POST /api/learning/rollback
{ "version": 1 }
```

**Response:** `{ "version": 9, "rolled_back_to": 1, "adjustments": {…},
"summary": ["Rolled back to version 1 (from version 8)"] }`

History is append-only: a rollback creates a NEW version whose
adjustments copy the target's — nothing is deleted, and a rollback can
itself be rolled back. **Errors:** `400` invalid JSON · `422` `version`
missing / not a positive integer · `404` unknown version.

## 5. Passive signals (no frontend work needed — for awareness)

The backend already learns at half strength (0.5) from actions the user
takes anyway, recorded on the existing routes: downloading a clip →
accept · a successful on-device export → accept · deleting a clip →
reject · requesting a re-render → reject (attributed to the niche being
switched TO). The UI does not need to surface these, but the status
page's evidence counts include them.

**Not this system:** `POST /api/feedback` (bug/feature/general) is the
unrelated Discord-webhook product-feedback route. Do not wire clip
verdicts to it.

---

## Verification (sandbox, 2026-11-15, branch `feat/learning-engine-backend`)

- **Step-0 trace:** every link WIRED — endpoint (validation 422,
  ownership 403, clip/task match 404) → append-only SQL storage
  (migration `20261108_0003_learning_engine.sql`) → versioned document
  refresh → selection ranking (`weighted_total` in the candidate rank
  key) and EDL profile adjustment consumers. No link was
  stored-but-never-read; no production code change was needed.
- **New proof** (`backend/tests/unit/test_learning_loop_end_to_end.py`,
  5 tests, PASS): feedback submitted through the real service with the
  REAL repository SQL (SQLite via the repo's sql_exec harness) flips
  the next selection's pick order on a fixed fixture (base
  `["01:00","00:00"]` → learned `["00:00","01:00"]` under the earned
  hook weight 1.15); rollback through the real service restores the
  base order; a second user is unaffected; below-threshold feedback
  changes nothing; the status payload matches real storage.
- **Pre-existing suites** (PASS, unchanged): engine rules + pure
  selection flip (13), service/route tests incl. validation, non-owner
  403, kill switch (13), auto-niche seam (6). Family total with the new
  suite and the SQL-exec neighbours: **50 passed / 0 failed**.
- **Live/PostgreSQL tier: NOT RUN** (unit tier + SQLite SQL execution,
  the repo's established pattern). **User-machine validation: NOT RUN.**
