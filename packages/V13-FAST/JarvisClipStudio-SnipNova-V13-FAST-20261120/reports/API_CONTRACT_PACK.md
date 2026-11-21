# API CONTRACT PACK — SnipNova (JarvisClipStudio)

**Audience:** Luffy's Claude Code frontend work. **Date:** 2026-11-15.
**Frozen backend tip:** `5ea71bf` (worktree `~/workspace/jcs-next`).
**Branch-only additions:** `feat/recipes-foundation` (worktree `~/workspace/jcs-recipes`,
tip `e09c5f2`); Learning Engine verification branch `feat/learning-engine-backend`
(worktree `~/workspace/jcs-learning-be`, tip `4582a0f` — tests only, no behaviour change).

**Sources consolidated here:** the in-tree `docs/api-reference.md` (frozen tip),
`LEARNING_ENGINE_API_CONTRACT.md` (2026-11-15), the Recipes foundation routes, and —
decisively — the backend route handlers themselves. Where this pack and any earlier
doc disagree, this pack follows the code.

**Status legend (per endpoint):**
- `VERIFIED` — method, path, auth, request/response shape and error codes read from
  the route handler in code. (For service-defined payloads the handler's return is
  quoted and the service is named where the shape lives.)
- `DOC-ONLY` — claimed in a doc, no route handler found.
- `DISCREPANCY` — docs and code disagree; both are quoted in the discrepancies table.

**Counts:** 152 backend endpoints VERIFIED at frozen tip `5ea71bf` · 6 Recipes endpoints
VERIFIED on branch `feat/recipes-foundation` (not in the frozen tip) · 0 DOC-ONLY
backend endpoints (every documented backend path exists) · 5 discrepancies (below).

## How to call the API

- **Browser → frontend:** `https://<frontend>/api/...`. Next.js route handlers in
  `frontend/src/app/api` attach the Better Auth session and proxy to the backend with
  HMAC-signed session headers. A missing session returns `401 {"error": "Unauthorized"}`.
- **Backend direct (self-hosted):** `http://localhost:8000/...`; **hosted:**
  `https://api.supoclip.com/...`. FastAPI docs at `/docs`.
- **Auth on the backend** (`backend/src/auth_headers.py`):
  - Most routes resolve the user via `resolve_authenticated_user_id`: a personal API
    key wins when present — `Authorization: Bearer sk_...` or `x-api-key: sk_...`
    (validated against the DB hash; unknown/revoked key → `401`); otherwise the
    frontend's HMAC-signed session headers are used. No key and no signed headers →
    `401` (or `503` if backend auth is unconfigured).
  - API-key *management* (`/api-keys/*`) accepts signed session headers ONLY, never
    an API key, so a leaked key cannot mint more keys.
  - Admin routes (`/admin/*`) additionally require `is_admin` on the user record
    (`require_admin_user`).
  - Public/token-gated exceptions: `GET /tasks/shared/{share_token}` (+ clip file),
    `GET /social/media/{media_token}`, `GET /email/product-updates/unsubscribe?token=...`,
    `GET /` (root, no auth).
- **Error envelope:** FastAPI default `{"detail": "<message>"}` (some handlers return a
  `detail` object, e.g. `{"code": "YOUTUBE_GEO_RESTRICTED", "message": ...}`). Every
  response carries an `x-trace-id` header; quote it when reporting a 500.
- **Common codes:** `400` malformed/invalid body · `401` unauthenticated · `403` not the
  owner / not admin / unverified email (API-key creation) · `404` unknown id or not
  owned · `409` conflict (duplicate, revision conflict, idempotency conflict) ·
  `422` validation (bad enum/value) · `429` rate/queue limit · `500` server error ·
  `502`/`503` downstream/worker unavailable. `307` = redirect to presigned object-storage URL.
- **Idempotency:** `POST /tasks/` accepts an `Idempotency-Key` header
  (`[A-Za-z0-9._:-]{8,200}`; invalid → `400`). For `upload://` sources an identical
  retry returns the already-queued task (`{"task_id", "job_id", "message": "Task already queued"}`).
- **Model/routing contract (as the code defines it, nothing invented):** the runtime LLM
  is the `LLM` setting; the code default is `groq:openai/gpt-oss-120b`
  (`backend/src/config.py:605`). Gemini is not supported anywhere (its API answers 411;
  `GOOGLE_API_KEY` is only a YouTube-metadata fallback). No endpoint in this pack
  changes model routing.

## Discrepancies — read first

| # | Claim | Code | Impact / fix for the frontend |
|---|---|---|---|
| D1 | `LEARNING_ENGINE_API_CONTRACT.md`: "All paths below are the frontend-visible paths (the Next.js proxy adds `/api`)" — i.e. browser calls `POST /api/learning/status` etc. | Backend `GET /learning/status` and `POST /learning/rollback` exist and are VERIFIED. But **no `frontend/src/app/api/learning/` route exists** in `jcs-next` or in `jcs-learning-be`. A browser call to `/api/learning/*` 404s in Next.js today. | Frontend must add a proxy route (`/api/learning/[...path]` → backend `/learning/...`) or call the backend directly. Only the feedback endpoint is already reachable from the browser (via the existing `/api/tasks/[...path]` catch-all). |
| D2 | Recipes foundation implies a frontend-usable `/recipes` surface. | Backend `GET/POST /recipes`, `GET/PATCH/DELETE /recipes/{id}`, `GET /recipes/{id}/fragment` exist on branch `feat/recipes-foundation` and are VERIFIED there. **No `frontend/src/app/api/recipes/` route exists** in `jcs-recipes`, and the router is **not mounted in the frozen tip** (`5ea71bf`) at all. | Branch-only. Do not build UI against `/api/recipes` until the branch merges and a proxy route lands. |
| D3 | `backend/src/api/routes/feature_flags.py` docstring: "clients read `GET /tasks/workflows`". | **No such route exists** — no `/tasks/workflows` handler in any backend file, no frontend route, no test reference. DOC-ONLY phantom. | Do not call it. Workflow gating today is the `flags` map from `GET /feature-flags` plus the `WORKFLOWS_ACCESS` flag. |
| D4 | In-tree `docs/api-reference.md` is presented as the API reference. | It documents ~25 of the 152 backend endpoints. Entirely missing: editor (18), publishing (12), brand-kits (7), reference-styles (4), learning (2), api-keys (3), account (3), channel-monitors (6), watch-folder (2), briefs (2), feature-flags (1), music/luts/sfx libraries (9), multipart upload (5), clip-exports (3), and most task additions (share, token-usage, candidates, select-moments, re-render, hook-variants, edit, ai-edit, compliance, zip). Nothing it *does* list was found wrong except D5. | This pack supersedes `api-reference.md` as the contract. Do not treat absence from that doc as absence from the API. |
| D5 | `api-reference.md` "Admin — `GET /admin` — Admin dashboard page". | There is no backend `GET /admin` route. The backend admin surface is `GET /admin/health` and `GET/PATCH /admin/runtime-settings` (admin-only). `/admin` is a frontend page. | Minor doc imprecision; no frontend action beyond noting it. |

Verified-true claims from the lane docs (not discrepancies): the Learning Engine
contract's endpoint paths, verdict/reason enums, response shapes and error codes all
match the handlers; `sendClipFeedback` in `frontend/src/lib/clip-actions.ts` exists
and posts to `/api/tasks/{taskId}/clips/{clipId}/feedback`, and grep confirms it still
has **no `.tsx` caller** (the accept/reject buttons are unwired — audit §C);
passive learning signals (download → accept, export → accept, delete → reject,
re-render → reject) are all present in `tasks.py`; the queued-export timeouts the
in-tree doc quotes (60 min queue max, 3 min worker heartbeat) match
`CLIP_EXPORT_QUEUE_MAX_SECONDS = 3600` / `CLIP_EXPORT_STALE_SECONDS = 180`.

---

# 1. Tasks

Backend prefix `/tasks` (`backend/src/api/routes/tasks.py`). Frontend: `GET /api/tasks`,
`POST /api/tasks/create`, `GET /api/tasks/billing-summary`, catch-all
`GET|POST|PATCH|DELETE /api/tasks/[...path]` → backend `/tasks/...`. Auth: session
headers or API key (`resolve_authenticated_user_id`); ownership enforced per task
(`403 "Not authorized for this task"` when the task belongs to another user).

## 1.1 List tasks — VERIFIED

```
GET /tasks/?limit=50
```
- Query: `limit` int, default 50.
- Response 200: `{"tasks": [<task record>, ...], "total": n}` (task records as stored;
  list view omits some fields by design — audit INFO F-3, pinned by test).
- Errors: `500`.

## 1.2 Create task — VERIFIED

```
POST /tasks/
```
- Headers: optional `Idempotency-Key` (see above).
- Request JSON (only `source.url` required):

| Field | Type / values | Notes |
|---|---|---|
| `source` | `{"url": string, "title"?: string}` | Required. `url` is a YouTube/other stream URL or an `upload://<file>` path returned by `POST /upload`. Non-`http(s)` unresolvable sources return the historic `404`; rejected `http(s)` stream URLs → `422`. |
| `clip_length` | `"auto"` (default, 15–60s) \| `"short"` (15–30) \| `"medium"` (30–60) \| `"long"` (60–120) | Invalid → `422` before creation/billing. |
| `creation_mode` | `"full_auto"` \| `"moments_manual"` \| `"moments_ai"` \| `"manual_cutter_ai"` \| omit (legacy pipeline) | `manual_ranges` required for — and only accepted with — `manual_cutter_ai`, else `422`. |
| `manual_ranges` | `[{start, end}, ...]` | Only with `manual_cutter_ai`. |
| `template_mode` | `"auto"` \| `"plain"` \| omit | `"plain"` = just cuts, no B-roll/music/SFX (the default create behaviour). |
| `niche_override` | niche id string \| omit | Validated; unknown → `422`. A `user-<slug>` reference-style name is accepted like any stock niche. |
| `music_mode` | `"auto"` \| `"on"` \| `"off"` \| omit | |
| `music_override` | music track id \| omit | |
| `broll_enabled` | bool \| omit | |
| `include_broll` | bool (default `false`) | |
| `separation_opt_in` | bool \| omit | Vocal/instrumental separation, double-gated. |
| `target_clip_count` | int \| omit | Invalid → `400`. |
| `content_brief` | brief object \| omit | Validated (`422` on invalid); re-validated after preflight with duration. |
| `campaign_id` | string \| omit | Must be a non-empty string (`400` otherwise). |
| `brand_kit_id` | string \| omit | Invalid → `422`. |
| `processing_mode` | `"fast"` \| `"balanced"` \| `"quality"` | Invalid value silently falls back to the server default (NOT a 422). |
| `output_format` | `"vertical"` (default) \| `"vertical_fit"` \| `"vertical_pan"` \| `"vertical_split"` \| `"original"` | Invalid → `422`. |
| `add_subtitles` | bool (default `true`) | Non-bool → `422`. |
| `font_options` | `{"font_family"?, "font_size"?, "font_color"?}` | Must be an object (`400` otherwise); values normalised. |
| `caption_template` | string (default `"default"`) | |

- Response 200: `{"task_id": ..., "job_id": ...}` (task created, job enqueued; the exact
  record shape is the created task). An identical `upload://` retry returns
  `{"task_id", "job_id", "message": "Task already queued"}` instead of creating a duplicate.
- Errors: `400` missing/invalid source, bad `font_options`/`campaign_id`/`target_clip_count` ·
  `402` billing cannot create (minutes/quota) · `404` unresolvable non-URL source ·
  `409` idempotency conflict · `422` bad enum/value (see table) · `500`.
- Note: `POST /api/tasks/create` (frontend) is a separate entry point that feeds the
  same backend create; the browser-facing create form posts there.

## 1.3 Billing summary — VERIFIED

```
GET /tasks/billing/summary
```
- Response 200: monetisation status + current usage for the authenticated user
  (`{"...": ...}` — service-defined; includes minutes/usage).
- Errors: `404` user not found · `500`. Frontend: `GET /api/tasks/billing-summary`.

## 1.4 Task detail — VERIFIED

```
GET /tasks/{task_id}
```
- Response 200: the stored task record (id, title, status, source, settings, progress,
  clips summary, timestamps — as stored; field-level shape is the task row).
- Errors: `404` · `500`. (Owner check: reader role; non-owner → `403` via
  `_require_task_reader`.)

## 1.5 Task progress (SSE) — VERIFIED

```
GET /tasks/{task_id}/progress
```
- Response: `text/event-stream` Server-Sent Events; stays open while the task is active;
  Redis-backed progress updates arrive live. Subscribe with `EventSource`, not polling.
- Errors: closes/errors per SSE semantics; auth as §1.

## 1.6 Update task — VERIFIED

```
PATCH /tasks/{task_id}
```
- Request JSON: `{"title": string}` (title only — everything else goes through
  `POST /{task_id}/settings`).
- Response 200: updated task record. Errors: `400` · `500`.

## 1.7 Delete task — VERIFIED

```
DELETE /tasks/{task_id}
```
- Deletes the task and all its clips. Response 200: confirmation payload.
- Errors: `400` · `403` non-owner · `404` · `500`.

## 1.8 Cancel / resume — VERIFIED

```
POST /tasks/{task_id}/cancel
POST /tasks/{task_id}/resume
```
- Cancel: cancels an active queued/processing task. Response 200. Errors: `500`.
- Resume: retries a cancelled/errored task, preserving completed clips. Response 200.
  Errors: `400` · `402` billing · `409` not resumable · `500`.

## 1.9 Task settings — VERIFIED

```
POST /tasks/{task_id}/settings
```
- Request JSON (all optional; **omitted fields keep the task's stored values** — a
  styling-only update never silently switches cleanup/B-roll off):

| Field | Type | Notes |
|---|---|---|
| `font_family` | string \| null | Normalised; must be an accessible font or `400 "Selected font is not available"`. |
| `font_size` | number \| null | Normalised. |
| `font_color` | string \| null | Normalised (`#RRGGBB`). |
| `caption_template` | string (default `"default"`) | |
| `include_broll` | bool | Omitted → keeps stored value. |
| `apply_to_existing` | bool (default `false`) | Apply to all existing clips. |
| cleanup fields | per `updated_cleanup_settings` | Omitted cleanup fields keep stored values (cut pauses / filler removal / thresholds). |

- Response 200: `{"task": <updated task>, "message": "Task settings updated"}`.
- Errors: `400` bad font / invalid value · `404` task not found · `422`
  `{"code": "YOUTUBE_GEO_RESTRICTED", ...}` for geo-restricted sources · `500`.
- Owner-only (`403` non-owner).

## 1.10 Share — VERIFIED

```
POST   /tasks/{task_id}/share
DELETE /tasks/{task_id}/share
GET    /tasks/shared/{share_token}
GET    /tasks/shared/{share_token}/clips/{clip_id}/file
```
- `POST /share`: creates or re-enables a stable read-only share URL for a **completed**
  task. Response 200: `{"share_token", "share_url", ...}`. Errors: `404` · `409`
  (not completed / already shared state).
- `DELETE /share`: disables the public URL; the private result is preserved. 200.
- `GET /shared/{share_token}`: **public, no auth** — read-only generation result for the
  token. Errors: `404`.
- `GET /shared/{share_token}/clips/{clip_id}/file`: **public, token-gated** — serves one
  shared clip (307 redirect to presigned URL when object storage is on, else streams).
  Errors: `404` · `307`.

## 1.11 Token usage — VERIFIED

```
GET /tasks/{task_id}/token-usage
```
- Response 200: LLM token usage recorded for the task by the provider manager
  (per-provider/model rows). Errors: `404` · `500`.

## 1.12 Candidates / select moments (Find-Moments modes) — VERIFIED

```
GET  /tasks/{task_id}/candidates
POST /tasks/{task_id}/select-moments
```
- `GET /candidates`: candidate moments waiting for the user's pick. Response 200:
  `{"candidates": [...]}`. **Note:** a task in a Find-Moments mode parks here until a
  pick is made — the UI must warn that it pauses for a pick; Full AutoClip is the default.
- `POST /select-moments`: body `{"segment_indexes": [<candidate indexes>]}`.
  Queues rendering of the picks. Response 200:
  `{"message": "Selected moments queued for rendering", ...}`. Only valid while the
  task is waiting for a moment selection (`409` otherwise).
  Errors: `400` · `402` · `409` · `422` · `500`.

---

# 2. Clips

All under `/tasks/{task_id}/clips/...`. Owner checks per clip (`403` non-owner,
`404` clip not found / clip does not belong to the task).

## 2.1 List clips — VERIFIED

```
GET /tasks/{task_id}/clips
```
- Response 200: `{"task_id", "clips": [<clip record>, ...], "total_clips": n}`.
- Errors: `404` · `500`.

## 2.2 Clip file / archive — VERIFIED

```
GET /tasks/{task_id}/clips/{clip_id}/file
GET /tasks/{task_id}/clips/zip/preflight
GET /tasks/{task_id}/clips/zip
```
- `.../file`: serves one clip file (307 redirect to presigned S3 URL when object storage
  is on, else streams). Errors: `404` · `500` · `503` storage unavailable.
- `.../zip/preflight`: checks a ZIP download would start, so the browser can surface
  errors first. Errors: `429`.
- `.../zip`: streams every existing clip of the task as one ZIP, built on the fly.
  Errors: `429`.

## 2.3 Delete clip — VERIFIED

```
DELETE /tasks/{task_id}/clips/{clip_id}
```
- Deletes the clip. **Passive learning side effect (verified in code):** records a
  half-strength (0.5) reject against the clip's niche.
- Response 200. Errors: `400` · `403` · `404` · `500`.

## 2.4 Trim / edit clip boundaries — VERIFIED

```
PATCH /tasks/{task_id}/clips/{clip_id}
```
- Request JSON: trim fields (`start`/`end` seconds — validated in-handler; exact keys
  as sent by the editor trim UI). Trims boundaries and regenerates the clip file.
- Response 200: updated clip record. Errors: `400` · `500`.

## 2.5 Split / merge — VERIFIED

```
POST /tasks/{task_id}/clips/{clip_id}/split
POST /tasks/{task_id}/clips/merge
```
- Split: body `{"split_time": <seconds>}`. Splits one clip into two.
  Response 200: the two resulting clips. Errors: `400` · `500`.
- Merge: body `{"clip_ids": [...]}` (ids in order). Response 200: merged clip.
  Errors: `400` · `500`.

## 2.6 Captions — VERIFIED

```
PATCH /tasks/{task_id}/clips/{clip_id}/captions
```
- Request JSON: `{"caption_text": string, "position"?: "bottom"|... (default "bottom"),
  "highlight_words"?: [...], "font_size"?: number, "position_y"?: number}`.
- Response 200: updated clip. Errors: `400` · `422` · `500`.

## 2.7 Regenerate / re-render — VERIFIED

```
POST /tasks/{task_id}/clips/{clip_id}/regenerate
POST /tasks/{task_id}/clips/{clip_id}/re-render        → 202
GET  /tasks/{task_id}/clips/{clip_id}/re-render/{job_id}
```
- Regenerate: re-renders a single clip after timing edits (synchronous-ish; returns the
  updated clip). Errors: `400` · `500`.
- Re-render (202): queues a re-render with a different niche profile. Body:
  `{"niche_override"?: string, ...}` (profile switch fields). Response 202:
  `{"job_id", "status": "queued", ...}`. **Passive learning side effect (verified):**
  records a half-strength reject attributed to the niche being switched TO.
  Errors: `404` · `422` · `500`.
- Re-render status: `{"job_id", "status": "queued"|"rendering"|"succeeded"|"failed",
  "clip"?: <refreshed clip when done>, "error"?: ...}`. Errors: `404` · `500`.

## 2.8 Hook variants — VERIFIED

```
POST /tasks/{task_id}/clips/{clip_id}/hook-variants            → 202
GET  /tasks/{task_id}/clips/{clip_id}/hook-variants/{job_id}
GET  /tasks/{task_id}/clips/{clip_id}/hook-variants
POST /tasks/{task_id}/clips/{clip_id}/hook-variants/{preview_id}/select → 202
GET  /tasks/{task_id}/clips/{clip_id}/hook-variants/{preview_id}/file
```
- Generate (202): queues hook-variant preview renders. Response 202:
  `{"job_id", "status": "queued", ...}`. Errors: `404` · `500`.
- Job status: previews carried when done. Errors: `404` · `500`.
- List: the clip's latest hook-preview batch, with the applied one marked. 200.
  Errors: `404` · `500`.
- Select (202): queues applying a picked preview to the full clip (a re-render through
  the Lane-5 seam). Response 202:
  `{"clip_id", "job_id", "status": "queued", "hook_variant": <variant>}` — poll the
  **re-render** status route with this `job_id`. Errors: `404` preview/clip not found · `500`.
- Preview file: serves one rendered hook preview for inline playback; `?download=true`
  forces download. 307 redirect when object storage is on. Errors: `404` · `422` · `307`.

## 2.9 Edit plan / AI edit — VERIFIED

```
GET  /tasks/{task_id}/clips/{clip_id}/edit
POST /tasks/{task_id}/clips/{clip_id}/edit        → 202
GET  /tasks/{task_id}/clips/{clip_id}/edit/{job_id}
POST /tasks/{task_id}/clips/{clip_id}/ai-edit     → 202
GET  /tasks/{task_id}/clips/{clip_id}/compliance
```
- Get edit plan: the clip's current edit plan (segments + caption overrides). 200.
  Errors: `404` · `500`.
- Edit (202): body `{"operations": [...], "caption_overrides"?: {...}}` (segment ops /
  caption overrides). Response 202: `{"job_id", "status": "queued", ...}`.
  Errors: `404` · `422` · `500`.
- Edit status: shares the re-render outcomes store. Errors: `404` · `500`.
- AI edit (202): body `{"instruction": "<plain-language edit instruction>"}`.
  Translates the instruction into a queued editor edit. Response 202: job payload.
  Errors: `404` · `422` · `500` · `502` AI service failure.
- Compliance: one clip's campaign-brief compliance report (owner only). 200.
  Errors: `404` · `500`.

## 2.10 Clip feedback (Learning Engine record) — VERIFIED

```
POST /tasks/{task_id}/clips/{clip_id}/feedback
```
Full contract in §7.1. Summary: body `{"verdict": "accept"|"reject", "reason"?:
"hook_weak"|"framing"|"captions"|"music"|"sfx"|"pacing"|"other"|null}`; response 200
`{"recorded": bool, "learning_version": int|null}`; errors `400` non-JSON body ·
`403` non-owner · `404` task/clip · `422` bad verdict/reason · `500`. Browser path
`/api/tasks/{taskId}/clips/{clipId}/feedback` works today (existing catch-all proxy);
frontend helper `sendClipFeedback` exists in `frontend/src/lib/clip-actions.ts`
(no `.tsx` caller yet).

---

# 3. Moments — see §1.12

Candidate listing and moment selection live on the task routes (`GET /candidates`,
`POST /select-moments`). No separate `/moments` router exists.

---

# 4. Editor (draft workspace)

Backend prefix `/tasks` (`backend/src/api/routes/editor.py`). All routes owner-checked.
The editor works on a per-clip draft directory on disk; drafts carry a `revision`
number and a `basis` (source clip filename) — saves/exports with a stale `basis`
return `409 "The original clip changed. Reopen the editor."`.

## 4.1 Get editor state — VERIFIED

```
GET /tasks/{task_id}/clips/{clip_id}/editor
```
- Response 200: the editor state object — `{"status": "ready"|"preparing"|"failed",
  "draft": <EditDocument>, "original": <EditDocument>, "duration": <sec>,
  "captionLooks": [...], "aiEditing": bool, "aiEditUsage"?: {...},
  "basis": "<clip filename>", "jobs": [<last 20 job records>], ...}`.
- Errors: `404` clip/task · `500` (handler errors surface as 500 via the shared path).

## 4.2 Prepare preview — VERIFIED

```
POST /tasks/{task_id}/clips/{clip_id}/editor/prepare   → 202
```
- No body. Queues `prepare_editor` on the worker. Response 202:
  `{"status": "preparing"}` (or `{"status": "ready"}` if already prepared).
- Errors: `503` "The rendering worker is unavailable. Try again shortly."

## 4.3 Save draft — VERIFIED

```
PATCH /tasks/{task_id}/clips/{clip_id}/editor
```
- Request JSON: `{"basis": "<clip filename>", "revision": <int >= 0>,
  "document": <EditDocument>}`.
- Response 200: save result (`{"revision": n, ...}` — the saved-document acknowledgement).
- Errors: `404` clip not found · `409` basis changed / editor not ready / revision
  conflict · `400` invalid revision or document validation error.

## 4.4 Editor media — VERIFIED

```
GET /tasks/{task_id}/clips/{clip_id}/editor/media/{asset}
```
- `asset` ∈ `clean.mp4`, `layout.mp4`, `thumbnails.jpg`, or
  `overlay-<32 hex>.(png|jpg|webp)`. Streams the file (307 redirect to presigned URL
  when object storage is on).
- Errors: `404` "Preview not ready" · `503` storage temporarily unavailable · `307`.

## 4.5 Overlay upload — VERIFIED

```
POST /tasks/{task_id}/clips/{clip_id}/editor/overlays/upload
```
- Request: multipart form, `file` = one user image. Stores it in the editor directory
  for the overlay lane. Response 200: stored overlay entry.
- Errors: `400` invalid image · `500`.

## 4.6 Editor exports — VERIFIED

```
POST   /tasks/{task_id}/clips/{clip_id}/editor/exports        → 202
GET    /tasks/{task_id}/clips/{clip_id}/editor/exports/{job_id}
DELETE /tasks/{task_id}/clips/{clip_id}/editor/exports/{job_id}
GET    /tasks/{task_id}/clips/{clip_id}/editor/exports/{job_id}/file
```
- Start (202): body `{"basis": ..., "preset": "tiktok"|"reels"|"shorts" (default "tiktok"),
  "document": <EditDocument>}`. Max 3 active exports per clip (`429` beyond that).
  Response 202: `{"id": <job_id>, "status": "queued", "progress": 0}`.
  Errors: `400` unknown preset / invalid document · `409` editor not ready / basis
  changed · `503` worker unavailable.
- Status: `{"id", "status": "queued"|"rendering"|"succeeded"|"failed", "progress": 0-100,
  "error"?: ...}`. Errors: `404` unknown job.
- Cancel: deletes/cancels the export job. 200.
- File: streams the finished export (307 to presigned URL when object storage is on).
  Errors: `404` · `307`.

## 4.7 Combine drafts — VERIFIED

```
POST /tasks/{task_id}/editor/combine   → 202
```
- Request JSON: combine payload (draft clip ids + output frame spec; Pexels orientation
  and minimum short side for the draft's output frame — fields as sent by the combine UI).
  Response 202: job payload. Errors: `202`-path errors · `500`.

## 4.8 Editor B-roll — VERIFIED

```
GET  /tasks/{task_id}/clips/{clip_id}/editor/broll/search?query=...&...
POST /tasks/{task_id}/clips/{clip_id}/editor/broll/suggest
GET  /tasks/{task_id}/clips/{clip_id}/editor/broll/footage/{video_id}/{size}
POST /tasks/{task_id}/clips/{clip_id}/editor/broll/{broll_id}/next-candidate
GET  /tasks/{task_id}/clips/{clip_id}/editor/broll/footage/library/{provider}/{asset_id}
```
- Search: Pexels footage for the B-roll panel, sized for the draft's frame. Query params
  as sent by the panel (`query`, orientation/size). 200: footage list.
- Suggest: body `{"words"/"segment": ...}` (spoken-word context). AI-picked cutaways for
  the draft's spoken words, avoiding existing B-roll. 200: suggestions.
- Footage `{video_id}/{size}`: streams a cached Pexels rendition for canvas preview. 200.
- Next-candidate: next stock candidate for one B-roll cue in the draft. 200.
- Library footage: streams a Pixabay/Coverr rendition from the shared stock library
  (`provider` ∈ `pixabay`, `coverr`). 200.
- Errors: `404` unknown clip/asset · `500`.

## 4.9 Editor AI edit — VERIFIED

```
GET  /tasks/{task_id}/clips/{clip_id}/editor/ai
POST /tasks/{task_id}/clips/{clip_id}/editor/ai        → 202
GET  /tasks/{task_id}/clips/{clip_id}/editor/ai/{job_id}
```
- Usage (GET): AI-edit allowance/usage summary for the task owner. 200.
- Start (202): body `{"instruction": "<plain-language edit>"}`. Queues a plain-language
  edit for the AI worker. Response 202: job payload. Errors: `500`/`503` worker/AI unavailable.
- Status: job state for the AI edit. 200. Errors: `404`.

---

# 5. Exports

## 5.1 Synchronous preset export (legacy) — VERIFIED

```
GET /tasks/{task_id}/clips/{clip_id}/export?preset=tiktok
```
- Exports with a social platform preset; **the response waits until ffmpeg finishes**.
  Older clients (incl. current iOS builds) keep using this route.
  **Passive learning side effect (verified):** a successful export records a
  half-strength accept for the clip's niche.
- Query: `preset` ∈ `tiktok` | `reels` | `shorts` (default `tiktok`).
- Response 200: the exported file (or redirect). Errors: `400` bad preset ·
  `404` clip not found · `500`.

## 5.2 Queued preset exports — VERIFIED

```
POST /tasks/{task_id}/clips/{clip_id}/exports              → 202
GET  /tasks/{task_id}/clips/{clip_id}/exports/{export_id}
GET  /tasks/{task_id}/clips/{clip_id}/exports/{export_id}/file
```
- Start (202): body `{"preset": "tiktok"|"reels"|"shorts"}` or `?preset=` query
  (default `tiktok`); invalid preset → `400`. Same clip + preset + source file reuses a
  queued/running/succeeded job. Response 202: export job record.
  Errors: `404` clip not found · `503` export worker unavailable.
- Status: `{"id", "status": "queued"|"running"|"succeeded"|"failed", "progress": 0-100,
  "error"?: ..., "download_url"?: ...}`. A queued job is failed if its worker job
  disappears or after 60 min (`CLIP_EXPORT_QUEUE_MAX_SECONDS = 3600`); a running job
  is failed when the worker stops heartbeating for 3 min (`CLIP_EXPORT_STALE_SECONDS = 180`).
  Errors: `404` export not found.
- File: streams a finished export, or 307-redirects to a presigned object URL.
  Errors: `307` · `404` · `409` not finished.

Preset encodings (`backend/src/clip_editor.py`): `tiktok` 1080×1920, 10 Mbps, 192 kbps
audio; `reels` 1080×1920, 12 Mbps, 192 kbps; `shorts` 1080×1920, 8 Mbps (12 Mbps above
30 fps), 384 kbps audio.

---

# 6. Recipes — VERIFIED on branch `feat/recipes-foundation` ONLY

Backend prefix `/recipes` (`backend/src/api/routes/recipes.py`, worktree
`~/workspace/jcs-recipes`, tip `bd5e4e9` — foundation `e09c5f2` plus the
create-path wiring commits). **Not mounted in the frozen tip `5ea71bf`,
and no frontend proxy route exists (see D2).** Auth: session headers or API key
(same resolver as §1). All data is per-user; other users' recipes return `404`.

A recipe is the user's reusable bundle of creation settings — template mode, niche,
music, B-roll, captions, brand kit and the other create-form controls — saved under a
name. Task creation **does** read recipes: `POST /tasks` accepts an optional
`recipe_id` and applies the bundle under the request's explicit choices (§6.7).
Wiring a create-form control to send `recipe_id` is frontend work; the backend
contract below is complete.

## 6.1 List recipes — VERIFIED (branch)

```
GET /recipes
```
- Response 200: `{"recipes": [<recipe>, ...]}` newest first.
- Recipe object: `{"id", "user_id", "name", "settings": {...},
  "created_at": <ISO|null>, "updated_at": <ISO|null>}`.

## 6.2 Create recipe — VERIFIED (branch)

```
POST /recipes   → 201
```
- Request JSON: `{"name": string (required, non-empty), "settings": {...}}`.
  `settings` keys are restricted to the 20 create-path keys
  (`template_mode`, `niche_override`, `music_mode`, `music_override`, `broll_enabled`,
  `include_broll`, `separation_opt_in`, `creation_mode`, `clip_length`,
  `target_clip_count`, `output_format`, `add_subtitles`, `processing_mode`,
  `caption_template`, `font_options`, `brand_kit_id`, `cut_long_pauses`,
  `pause_threshold_ms`, `remove_filler_words`, `filtered_words`); each value is
  validated by the **same validator the create path uses**; unknown keys or invalid
  values → `422`. `font_options` = `{"font_family"?, "font_size"?, "font_color"?}`
  (`#RRGGBB` colour); `processing_mode` ∈ `fast|balanced|quality`;
  `caption_template` must name a real template.
- Response 201: the stored recipe object. Errors: `400` non-JSON body ·
  `422` validation · `500`.

## 6.3 Get recipe — VERIFIED (branch)

```
GET /recipes/{recipe_id}
```
- Response 200: recipe object. Errors: `404`.

## 6.4 Update recipe — VERIFIED (branch)

```
PATCH /recipes/{recipe_id}
```
- Request JSON: any subset of `{"name", "settings"}`. A present `settings` **replaces
  the whole bundle** — it is validated as a complete settings object, never merged.
- Response 200: updated recipe object. Errors: `400` · `422` · `404`.

## 6.5 Delete recipe — VERIFIED (branch)

```
DELETE /recipes/{recipe_id}
```
- Tasks that applied the recipe keep only the recorded recipe id in their source
  metadata (§6.7) — the id then names a bundle that no longer exists, while the
  task's own stored choices are unaffected — so deletion touches no task.
- Response 200: `{"deleted": true, "id": <recipe_id>}`. Errors: `404`.

## 6.6 Apply fragment — VERIFIED (branch)

```
GET /recipes/{recipe_id}/fragment
```
- Response 200: `{"recipe_id", "fragment": <the stored settings, deep-copied>}` — the
  exact task-create payload fragment this recipe contributes; merge it into a
  `POST /tasks/` body. Errors: `404`.

## 6.7 Create a task with a recipe — VERIFIED (branch)

```
POST /tasks   (existing route; one new optional body field)
```
- New field: `"recipe_id": string` (optional). When present, the server loads the
  recipe **for the requesting user** and merges its stored settings into the create
  payload *before* the create path's normal validation runs.
- **Precedence — explicit always wins.** Each recipe setting fills only a choice the
  request left unmade: a field present in the request with a non-`null` value keeps
  the request's value (including `false` / `0`); a field absent or `null` takes the
  recipe's value. `font_options` merges per sub-key (`font_family` / `font_size` /
  `font_color`), so one explicit font choice does not drop the recipe's other two.
- **Validation is unchanged.** The merged payload passes through the create path's
  existing validators exactly as a hand-built payload does — nothing is bypassed or
  relaxed. An explicit invalid value fails even when the recipe holds a valid one
  for the same key, and a stored value the create path would reject (stale or
  hand-edited row) fails the same way.
- **Errors:** unknown id, another user's id, or a malformed id (non-string / blank)
  → `422` (`"Recipe not found"` for unknown/cross-user — the lookup is user-scoped,
  so another user's recipe is indistinguishable from a missing one), and **nothing
  is created** (the check runs before billing, preflight and task creation).
- **Provenance:** the created task records the applied recipe in its source
  metadata as `{"recipe_id": <id>}` (alongside the creator-name stamp). A task
  created without a recipe stamps nothing. There is no `recipe_id` column — the
  stamp lives in the existing source-metadata JSON.
- **No-recipe creates are unchanged:** omitting `recipe_id` produces byte-identical
  service and worker arguments to before this wiring (pinned by test).
- **Per-video / per-campaign keys can never arrive via a recipe:** `source`,
  `manual_ranges`, `content_brief` and `campaign_id` are rejected at recipe-write
  time (§6.2), and the create-path merge additionally ignores any fragment key
  outside the 20 storable settings, so a recipe cannot resurrect them.

---

# 7. Learning Engine

Backend prefix `/learning` (`backend/src/api/routes/learning.py`) + feedback on the
clip routes (§2.10). All routes per-user; one user's feedback never affects another
user's selection (proven by test on the branch). Kill switch: `LEARNING_ENABLED=false`
makes recording a no-op (`recorded: false`) and status report `enabled: false`.

The engine is **not** model training: adjustments are bounded parameter nudges per
(user, niche), stored in a versioned document; "no document" = exactly the
pre-learning behaviour. Bounds: sub-score weights 0.80–1.20 each; SFX density 0.5–1.5;
music volume `0.0` (learned off) or `1.25`; caption preset = a preset id string.
Evidence thresholds (weighted; explicit = 1.0, passive = 0.5): sub-score weights step
at 3 / 6 / 10 weighted rejects citing the matching reason (+0.10 / +0.15 / +0.20);
SFX steps down at 3 / 6 `sfx` rejects, up at 8 / 12 accepts; music needs 5 consistent
signals; captions switch preset at ≥2 weighted rejects once ≥3 weighted accepts of
another preset exist. Below threshold, nothing changes.

## 7.1 Record a verdict — VERIFIED

```
POST /tasks/{task_id}/clips/{clip_id}/feedback
```
- Request JSON:

| Field | Type | Rules |
|---|---|---|
| `verdict` | string | Required. `"accept"` or `"reject"` — anything else → `422 "Invalid verdict. Valid values: ['accept', 'reject']."` |
| `reason` | string \| null | Optional; only meaningful on reject. One of `hook_weak`, `framing`, `captions`, `music`, `sfx`, `pacing`, `other` — anything else → `422` (detail lists the valid values). |

- Response 200: `{"recorded": true, "learning_version": <int>}`.
  `recorded: false` (still 200) means the verdict was NOT stored — engine disabled or
  storage failed. `learning_version` is the user's adjustment-document version after
  this verdict, or `null` when nothing was recorded. Recording never fails the click.
- Errors: `400` body is not a JSON object · `403` task belongs to another user ·
  `404` task not found, or clip not found / clip does not belong to the task ·
  `422` invalid verdict/reason · `500`.
- Semantics: accept = "more like this" (weight 1.0 evidence for the clip's niche:
  hook/engagement/value/shareability profile, caption preset, music). Reject = "less
  like this"; the reason drives the channel (`hook_weak` → hook weight, `pacing` →
  engagement weight, `sfx` → SFX density, `music` → music volume, `captions` → caption
  preset; `framing`/`other` recorded but drive no dedicated channel today).
- **Not idempotent, deliberately:** the event log is append-only; submitting the same
  verdict twice counts it twice. Treat a verdict as one-shot in the UI.
- The verdict is recorded against the clip's **honestly known niche** (clip override,
  else task override). Clips whose niche was only guessed by the classifier fallback
  teach nothing, by design.
- Browser path works today: `/api/tasks/{taskId}/clips/{clipId}/feedback` (catch-all
  proxy). Helper `sendClipFeedback(taskId, clipId, verdict, reason?)` exists in
  `frontend/src/lib/clip-actions.ts` — no `.tsx` caller yet; wiring it to buttons is
  the frontend work.

## 7.2 Status — VERIFIED

```
GET /learning/status
```
- Response 200 (shape verified against `learning_status` in
  `backend/src/services/learning_service.py`):

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

- `enabled: false` = kill switch off; `current_version: null` + empty `adjustments` =
  nothing learned yet (or everything rolled back). `versions[].summary` lines are
  human-readable change descriptions (e.g. `"meme_comedy: hook_score selection weight -> 1.15 (was 1)"`) — safe to render verbatim.
- Note: a status read may itself advance the version when passive events arrived since
  the last computation — versions are cheap and append-only.
- Errors: `500` on read failure. **Browser reachability: BLOCKED by D1** — no
  `/api/learning` frontend proxy route exists yet.

## 7.3 Rollback — VERIFIED

```
POST /learning/rollback
```
- Request JSON: `{"version": <positive integer>}`.
- Response 200: `{"version": <new version>, "rolled_back_to": <target>,
  "adjustments": {...}, "summary": ["Rolled back to version 1 (from version 8)"]}`.
  History is append-only: a rollback creates a NEW version copying the target's
  adjustments; nothing is deleted, and a rollback can itself be rolled back.
- Errors: `400` invalid JSON / body not an object · `422` `version` missing / not a
  positive integer · `404` unknown version · `500`.
- **Browser reachability: BLOCKED by D1** (same missing proxy route).

## 7.4 Passive signals — VERIFIED (no frontend work)

The backend already learns at half strength (0.5) from actions the user takes anyway,
verified in `tasks.py`: downloading a clip → accept (`passive_download`); a successful
preset export → accept (`passive_export`); deleting a clip → reject (`passive_delete`);
requesting a re-render → reject attributed to the niche being switched TO
(`passive_rerender`). The UI does not need to surface these, but the status payload's
evidence counts include them.

**Not this system:** `POST /api/feedback` (bug/feature/general product feedback to a
Discord webhook) is unrelated. Do not wire clip verdicts to it.

---

# 8. Reference Styles

Backend prefix `/reference-styles` (`backend/src/api/routes/reference_styles.py`).
Auth: session headers or API key. A style is a user niche profile derived from a
finished reference video; public names carry the `user-` prefix and can be picked
anywhere a stock niche can. Re-creating a style under a name already used **replaces**
that style (same slug, files overwritten).

## 8.1 List — VERIFIED

```
GET /reference-styles
```
- Response 200: `{"profiles": [<user profile summary>, ...]}` (from
  `list_user_profiles`; each entry carries the profile name, measurements and mapping
  summary).

## 8.2 Create from upload — VERIFIED

```
POST /reference-styles/upload   → 201
```
- Request: multipart form — `file` (video file; extension must be a supported video
  suffix) + `name` (form field, required, ≤120 chars).
- Response 201: the stored-profile summary (`{...summary, "inferred": {...},
  "not_inferred": [...]}`), after the analyser runs and the stored profile is proven
  to load through the real loader (a profile nothing can load is deleted and the
  request fails instead).
- Errors: `400` missing/blank name, name too long, unsupported file type, unanalysable
  video · `413` file exceeds the upload size limit · `500`.

## 8.3 Create from URL — VERIFIED

```
POST /reference-styles/from-url   → 201
```
- Request JSON: `{"url": "<http(s) URL>", "name": "<display name>"}`.
  The video is downloaded with yt-dlp (single file, `best[ext=mp4]/best`, size-capped).
- Response 201: same summary shape as §8.2.
- Errors: `400` invalid JSON, bad name, not an `http(s)` URL, download failure,
  analysis failure.

## 8.4 Delete — VERIFIED

```
DELETE /reference-styles/{name}
```
- `name` must be a user profile name (`user-...`); stock styles cannot be deleted
  (`400 "Only reference styles (user-...) can be deleted"`).
- Response 200: `{"deleted": "<name>"}`. Errors: `400` · `404` style not found.

---

# 9. Brand Kits

Backend prefix `/brand-kits` (`backend/src/api/routes/brand_kits.py`). Auth: session
headers or API key. A kit is reusable branding (caption colours/template, caption
font, hook-title treatment, default music track, logo watermark); a task opts in via
`brand_kit_id` on creation (§1.2). Kit object:
`{"id", "user_id", "name", "primary_color", "accent_color", "caption_template",
"font_name", "title_style", "music_track", "logo_filename", "logo_position"
(default "bottom_right"), "logo_opacity" (default 0.85), "intro_asset", "outro_asset",
"created_at", "updated_at", "logo_present": bool}`.

## 9.1 List / create / get / update / delete — VERIFIED

```
GET    /brand-kits            → {"kits": [...]} newest first
POST   /brand-kits            → 201, created kit
GET    /brand-kits/{kit_id}   → kit
PATCH  /brand-kits/{kit_id}   → updated kit
DELETE /brand-kits/{kit_id}   → {"deleted": true, "id": ...}
```
- Create/update bodies: any subset of the kit fields above (create requires `name`;
  values validated by `validate_kit_payload` — bad colour/template/font → `422`;
  non-JSON body → `400`). Update validates only the fields present. Delete also
  removes the logo file and clears the opt-in on tasks that used the kit.
- Errors: `400` · `404` kit not found · `422` validation · `500`.

## 9.2 Logo — VERIFIED

```
POST   /brand-kits/{kit_id}/logo   → 201
DELETE /brand-kits/{kit_id}/logo
```
- Upload: multipart form, `file` = logo image (supported image extensions per
  `bk.LOGO_EXTENSIONS`; max 10 MB → `413` over the limit). Response 201: updated kit.
- Delete: clears `logo_filename` and removes the file. Response 200: updated kit.
- Errors: `400` unsupported type · `404` kit not found · `413` over 10 MB.

---

# 10. Settings

## 10.1 Task settings — VERIFIED — see §1.9 (`POST /tasks/{task_id}/settings`)

## 10.2 User preferences (frontend-only) — VERIFIED (frontend code)

```
GET   /api/preferences
PATCH /api/preferences
```
- **No backend route.** Implemented in `frontend/src/app/api/preferences/route.ts`
  against the Prisma `user` row. Session auth (`401` without session).
- GET 200: `{"fontFamily", "fontSize", "fontColor", "notifyOnCompletion", "clipLength"}`
  with defaults `TikTokSans-Regular` / `24` / `"#FFFFFF"` / `true` / `"auto"`.
  `clipLength` ∈ `auto|short|medium|long`.
- PATCH: body with any of those fields (same names, camelCase); validated in-handler
  (`clipLength` via `isClipLength`). Errors: `401` · `404` user not found · `500`.
- Note the naming split: the frontend stores `default_font_*` / `default_clip_length`
  here; task creation reads them as defaults — the frontend passes them explicitly
  per task (§1.2).

## 10.3 Runtime settings (admin) — VERIFIED

```
GET   /admin/runtime-settings
PATCH /admin/runtime-settings
```
- Admin-only (`require_admin_user`). GET returns
  `{"settings": [{"key", "label", "description", "input_type", "source":
  "admin"|"environment"|"unset", "configured": bool, "has_admin_value": bool,
  "has_env_value": bool, "prefer_admin_value": bool, "overridden_by_env": bool,
  "updated_at": ...}, ...]}` for the keys in `RUNTIME_SETTING_KEYS`
  (`ASSEMBLY_AI_API_KEY`, `LLM`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`,
  `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`, `OLLAMA_API_KEY`,
  `YOUTUBE_DATA_API_KEY`, `APIFY_API_TOKEN`, `PEXELS_API_KEY`).
- PATCH body: `{"updates": {key: value}, "delete_keys": [key, ...],
  "prefer_admin_values": {key: bool}}`. Unknown keys → `400`; an `LLM` value whose
  provider key is missing → `400`. Values are encrypted at rest. Response: the same
  `{"settings": [...]}` list. Errors: `400` · `403` non-admin.

## 10.4 Feature flags — VERIFIED

```
GET /feature-flags
```
- Auth: session headers or API key. Response 200:
  `{"platform": "web"|"ios", "flags": {<key>: <bool>, ...}}` (evaluated for the caller
  and platform). A feature may have its own gate on top (workflows also need
  `WORKFLOWS_ACCESS`). See D3: there is no `GET /tasks/workflows` route.

## 10.5 Watch folder — VERIFIED

```
GET /watch-folder
PUT /watch-folder/settings
```
- GET: `{"settings": {...}}` — the user's watch-folder settings (folder path, enabled,
  defaults; shape from the repository's `_public_settings`).
- PUT: body `{"settings": {...}}` (a settings object is required — missing/invalid →
  `400`; invalid values → `422`). Response: updated `{"settings": {...}}`.

## 10.6 Channel monitors — VERIFIED

```
GET    /channel-monitors
PUT    /channel-monitors/settings
POST   /channel-monitors                        → 201
PUT    /channel-monitors/{monitor_id}
POST   /channel-monitors/videos/{video_row_id}/retry
DELETE /channel-monitors/{monitor_id}
```
- GET: the user's channel monitors + settings. PUT settings: body is the settings
  object (invalid → `422`).
- POST: body `{"channel_url"/"channel_id", ...}` (monitor fields as sent by the
  monitors UI; validated in-handler, invalid → `422`). Response 201: created monitor.
- PUT `{monitor_id}`: partial monitor update. Errors: `404` · `422`.
- POST retry: manually re-drives one activity-list row (parked/skipped video).
  Errors: `404`.
- DELETE: removes the monitor. Errors: `404`.

---

# 11. Media libraries

## 11.1 Fonts — VERIFIED

```
GET    /fonts
GET    /fonts/{font_name}
POST   /fonts/upload
DELETE /fonts/{font_name}
```
- Auth: session headers or API key (the api-reference's documented API-key scope).
- List: available fonts (bundled + user uploads). File: streams one font file.
  Upload: multipart `.ttf`/`.otf` (invalid → `400`; `403` when the caller may not
  upload). Delete: removes a custom font owned by the caller (`403` not owned ·
  `404` unknown). Errors: `404` · `500`.
- Frontend: `GET /api/fonts`, `GET /api/fonts/[fontName]`, `POST /api/fonts/upload`.

## 11.2 Caption templates / transitions / B-roll status — VERIFIED

```
GET /caption-templates
GET /transitions
GET /broll/status
```
- No auth beyond the shared resolver (public lists; `broll/status` returns whether
  B-roll integrations are configured — `400` when misconfigured state is detected).
- Caption templates: available subtitle template definitions. Transitions: available
  transition effects. No request body.
- Frontend: `GET /api/caption-templates`, `GET /api/broll/status`.

## 11.3 Music / SFX / LUT libraries — VERIFIED

```
GET  /music/library          GET  /music/file/{filename}     POST /music/upload → 201
GET  /sfx/library            GET  /sfx/file/{filename}       POST /sfx/upload   → 201
GET  /luts/library                                            POST /luts/upload
                                                              DELETE /luts/{filename}
```
- Libraries list every track/effect/LUT (bundled first, then user uploads). File routes
  stream one entry for the create-bar/editor preview players (`400` bad name ·
  `404` unknown).
- Uploads: multipart `file` (audio for music/SFX, `.cube` for LUTs; over-size →
  `413` music/SFX, `400` invalid LUT). Response 201: the stored library entry.
  LUT delete: `404` unknown filename.
- Frontend proxies: `/api/music/[...path]`, `/api/sfx/[...path]`, `/api/luts/[...path]`.

---

# 12. Uploads

Backend prefix `/` (`backend/src/api/routes/media.py`). Auth: session headers or API
key. Frontend: `POST /api/upload`, `POST /api/upload/[action]`,
`POST /api/upload/authorization`.

## 12.1 Single upload — VERIFIED

```
POST /upload
```
- Request: multipart form, `file` field named `video` (optional `Idempotency-Key`
  header). Size-capped at `max_video_upload_bytes`.
- Response 200: `{"message": "Video uploaded successfully", "video_path": "upload://<uuid>.<ext>"}`.
  Pass `video_path` as `source.url` to `POST /tasks/`. Identical bytes (or the same
  idempotency key) reuse the stored file. Object-storage copy and 1080p scaling happen
  after the response.
- Errors: `400` no video file · `409` idempotency conflict · `429` upload limit reached ·
  `500`.

## 12.2 Multipart upload — VERIFIED (route/auth/errors; part shapes not enumerated)

```
POST /upload/authorize
POST /upload/parts
POST /upload/complete
POST /upload/abort
```
- The chunked-upload session flow for large files (authorize → upload parts →
  complete/abort). Exact part-request/response fields live in the handlers and were
  not enumerated for this pack — see "What was not checked".

---

# 13. Publishing state

Two systems exist side by side; do not confuse them.

## 13.1 Publishing board / campaigns / posts (in-app tracker) — VERIFIED

Backend prefix `/publishing` (`backend/src/api/routes/publishing.py`). Auth: session
headers or API key. This is the in-app publishing tracker (board, campaigns, manual
post records, stats). It never posts to a platform by itself.

```
GET    /publishing/board
GET    /publishing/clips/{clip_id}/bundle
GET    /publishing/campaigns
POST   /publishing/campaigns                        → 201
PATCH  /publishing/campaigns/{campaign_id}
GET    /publishing/campaigns/{campaign_id}/brief
DELETE /publishing/campaigns/{campaign_id}
POST   /publishing/posts                            → 201
PATCH  /publishing/posts/{post_id}
DELETE /publishing/posts/{post_id}
PUT    /publishing/posts/{post_id}/stats
GET    /publishing/analytics
```
- Board: `{"clips": <board rows built from clips+posts+campaigns+stats>, "campaigns": [...]}`.
- Bundle: downloads a ZIP of the clip's deliverable bundle folder (or the lone clip
  file). Errors: `404` clip not found / file unavailable.
- Campaigns: list returns campaigns enriched with `post_count` and `entered_stats`
  (`{views, likes, comments, earnings}`) when stats exist. Create body:
  `{"name" (required, ≤200 chars), "platform"?, "campaign_url"?, "budget"?: number,
  "notes"? (≤4000), "brief"?: <validated brief>, "example_url"?}` — missing name →
  `400`. Response 201: `{"campaign": {...}}`. Update: any of those fields
  (`400` bad value · `404`). Brief: the campaign's stored brief, for the Create-page
  picker (`404` unknown campaign). Delete: `404` unknown.
- Posts: create body `{"task_id"?, "clip_id"?, "title"?, "caption"?, "status"?,
  "campaign_id"?, "scheduled_for"?, ...}` (fields as sent by the board UI; validated
  in-handler). Response 201: `{"post": {...}}`. Update: partial fields (`404`).
  Delete: `404`. Stats: `PUT /posts/{post_id}/stats` body is the stats object
  (`{views, likes, comments, shares, earnings, ...}`); `404` unknown post.
- Analytics: aggregate publishing analytics (service-defined shape). 200.
- Frontend proxy: `/api/publishing/[...path]`.

## 13.2 Social publishing (platform OAuth + posting) — VERIFIED

Backend prefix `/social` (`backend/src/api/routes/social.py`). Connecting/disconnecting
requires signed session headers; posting and performance routes also accept API keys.
Providers: YouTube, TikTok, Instagram (each implements a genuine `publish()`).
Post status flow: `scheduled → queued → publishing → published | failed | cancelled`.
A worker claims a queued post atomically, always publishes the clip's current render,
runs a cron every minute to promote due posts / poll async publishes / fail posts
stuck in `publishing` for 2 h, and hourly to refresh metrics. An upload the platform
may already have accepted fails with "Upload outcome unknown" and is never re-uploaded
automatically. Deleting a clip/task cancels its scheduled and queued posts.

```
GET    /social/providers
GET    /social/connections
GET    /social/connections/{account_id}/publishing-options
POST   /social/connections/{provider}/authorize
POST   /social/connections/{provider}/callback
DELETE /social/connections
DELETE /social/connections/{account_id}
GET    /social/posts?task_id=&clip_id=&status=
POST   /social/captions
POST   /social/posts
GET    /social/performance
GET    /social/performance/timeseries?days=30
GET    /social/posts/{post_id}
POST   /social/posts/{post_id}/cancel
POST   /social/posts/{post_id}/retry
POST   /social/posts/{post_id}/refresh-metrics
DELETE /social/posts/{post_id}
GET    /social/media/{media_token}
```
- Providers: which platforms are configured, supported privacy levels and
  `default_privacy_level` (TikTok `public`/`private`, default `private`). 200.
- Connections: the user's connected accounts (never includes tokens). 200.
- Publishing-options: per-account publish options (privacy levels, etc.). 200.
- Authorize: starts the OAuth handshake; returns `{"authorize_url", "state", ...}` —
  the caller redirects the browser there. 200.
- Callback: body `{"code", "state"}`; exchanges the code and stores encrypted tokens.
  Errors via `SocialError` mapping (4xx/5xx by cause).
- Disconnect one: `404` unknown account. Disconnect all: used on account deletion;
  cancels scheduled posts and revokes the grant at the platform (best effort;
  Instagram has no revocation endpoint).
- List posts: filters `task_id`, `clip_id`, `status`. Returns posts with latest metrics.
- Captions: body `{"task_id", "clip_id", "provider"}` (all required → `422` if missing).
  Drafts a social caption without publishing or scheduling anything.
- Create post: body `{"task_id", "clip_id", "social_account_id" (all required → 422),
  "title"?, "caption"?, "hashtags"? (string or array), "privacy_level"?,
  "scheduled_for"? (ISO timestamp)}`. Without `scheduled_for` the post is queued
  immediately; with one it is scheduled. Response: `{"post": {...}}`.
- Performance: views/likes/comments/shares aggregated by hook type, platform and clip
  length, plus top clips. The same aggregation is injected into the AI selection prompt
  once `SOCIAL_PERFORMANCE_MIN_POSTS` posts have metrics.
- Timeseries: daily new views across the user's published posts (`?days=` default 30).
- Get post: one post including metrics history. Cancel: cancels a scheduled/queued post.
  Retry: re-queues a failed/cancelled post (an upload the platform already accepted is
  only checked again, never uploaded twice). Refresh-metrics: pulls fresh metrics now.
  Delete: removes the post record (the platform post is untouched); not while queued
  or publishing (`404` unknown; state errors via `SocialError`).
- Media: **public, token-gated** — streams a clip for a platform fetch while the token
  is valid (a few hours around publishing); redirects to a presigned URL with
  `MEDIA_STORAGE=s3`. Errors: `404` · `307`.
- Frontend: `GET /api/social/connect/[provider]`, `GET /api/social/callback/[provider]`,
  `GET /api/social/media/[token]`, catch-all `GET|POST|DELETE /api/social/[...path]`.

## 13.3 Briefs (campaign brief extraction) — VERIFIED

Backend prefix `/briefs` (`backend/src/api/routes/briefs.py`). Auth: session headers
or API key. Persists nothing.

```
POST /briefs/extract
POST /briefs/extract-image
```
- Extract: body `{"brief_text": string (required, length-capped)}`. Response 200:
  `{"content_brief": <draft brief object>, "extraction": {...}}`. Errors: `400`
  missing/over-long text.
- Extract-image: multipart form, `file` = brief screenshot image. Response 200: same
  shape. Errors: `400` · `413` over size · `422` unreadable image · `503` extraction
  service unavailable.

---

# 14. Health / observability

## 14.1 Root — VERIFIED

```
GET /
```
- No auth. Response 200: `{"name": "SupoClip API", "version": "0.2.0",
  "status": "running", "docs": "/docs", "architecture": "job queue", ...}`.

## 14.2 Admin health — VERIFIED

```
GET /admin/health
```
- Admin-only. Response 200: `{"status": "ok"}`. (This is the backend-reachability
  check; there is no public `/health` route.)

## 14.3 Performance metrics / dead-letter — VERIFIED

```
GET /tasks/metrics/performance
GET /tasks/dead-letter/list
```
- Performance: aggregate processing performance metrics by mode. 200.
  Errors: `500`.
- Dead-letter: tasks that exhausted retries and landed in the dead-letter store. 200.

---

# 15. Account / billing / feedback

## 15.1 API keys — VERIFIED

Backend prefix `/api-keys`. Auth: **signed session headers only** (never an API key).

```
GET    /api-keys/          → {"api_keys": [...metadata only, no secrets...], "total": n}
POST   /api-keys/          → {"api_key": {..., "key": "sk_..."}, "message": "API key created. Copy it now — it won't be shown again."}
DELETE /api-keys/{key_id}  → {"message": "API key revoked"}
```
- Create body: `{"name"?: string (default "API Key")}`. The plaintext `sk_...` key is
  shown exactly once; only its SHA-256 hash is stored. Requires a verified email:
  unverified user → `403 "Verify your email address before creating an API key."`
  (use **Send verification email** in the app; the link lasts one hour; self-hosted
  installs need SES configured). Key limit exceeded → `409`. Revoking takes effect
  immediately. Delete of an unknown key → `404`.
- Keys authenticate `/tasks/*`, `/fonts` and `/upload` (per the in-tree doc; in code
  the resolver is used by all routers listed in this pack unless noted otherwise).

## 15.2 Account emails — VERIFIED

Backend prefix `/account` (`backend/src/api/routes/account.py`). These are
server-to-server email triggers called by the frontend; not general-purpose client
endpoints.

```
POST /account/password-reset-email
POST /account/verification-email
POST /account/affiliate-email
```
- Bodies: the account/email payloads the frontend sends (`{"email", ...}` — fields as
  sent by the auth/affiliate flows). Errors: `400` · `404` · `502`/`503` mail service
  unavailable.

## 15.3 Billing email — VERIFIED

```
POST /billing/subscription-email
```
- Body: `{"event": "subscribed"|"unsubscribed"|"cancellation_scheduled", ...}`.
  Scheduled-cancellation mail reads the user's current Stripe state and access end
  date from the DB and skips if the cancellation is no longer scheduled.
  Errors: `400` · `404` · `502`/`503`.
- Frontend billing routes (Stripe, frontend-only): `POST /api/billing/checkout`,
  `POST /api/billing/portal`, `POST /api/billing/resume`, `POST /api/billing/webhook`,
  `POST /api/billing/revenuecat-webhook`. No backend billing routes exist beyond
  `GET /tasks/billing/summary` (§1.3) and this email trigger.

## 15.4 Product feedback (Discord webhook) — VERIFIED

```
POST /feedback
```
- Auth: signed session headers. Body: `{"category": "bug"|"feature"|"general"|"sales"|
  "cancellation", "message": string (1–2000 chars)}`. `sales` routes to its own
  webhook; the rest to the feedback webhook.
- Response 200: `{"status": "ok"}`. Errors: `400` feedback not configured (no webhook
  URL) · `422` invalid category · `500` webhook delivery failed.
- **Unrelated to clip verdicts** — do not wire accept/reject buttons here (see §7).

## 15.5 Email preferences (unsubscribe) — VERIFIED

```
GET  /email/product-updates/unsubscribe?token=...
POST /email/product-updates/unsubscribe?token=...
```
- Public, token-gated; returns/accepts an HTML page (not JSON). `token` query param
  required. This is the product-updates unsubscribe flow, not an API for the app UI.

---

# 16. Frontend-only routes (inventory only — not backend-contracted)

These exist as Next.js route handlers in `frontend/src/app/api` with **no backend
route behind them**. Listed so the frontend does not mistake them for backend
endpoints; their bodies were not verified beyond the two noted.

| Frontend route | Notes |
|---|---|
| `GET/PATCH /api/preferences` | VERIFIED (§10.2) — Prisma user row. |
| `GET /api/service-status/video-downloads` | Service-status probe; body not enumerated here. |
| `GET /api/share/[...path]` | Share-page data; body not enumerated here. |
| `GET/POST /api/attribution`, `/api/attribution/clip-export` | Attribution tracking; body not enumerated here. |
| `GET/POST /api/affiliates`, `/api/affiliates/offer`, `/api/affiliates/slug` | Creator/affiliate program; body not enumerated here. |
| `GET /api/announcements`, admin announcements routes | In-app announcements; body not enumerated here. |
| `/api/admin/*` (users, feature-flags, runtime-settings) | Admin pages; `runtime-settings` proxies §10.3. |
| `POST /api/billing/*` (checkout, portal, resume, webhooks) | Stripe; see §15.3. |
| `GET /api/auth/[...all]` | Better Auth handler. |

Frontend proxy routes (thin pass-throughs to the backend sections above):
`/api/tasks/*` (§1–2, §5, §7.1, §14.3), `/api/brand-kits/*` (§9),
`/api/briefs/*` (§13.3), `/api/broll/status` (§11.2), `/api/caption-templates` (§11.2),
`/api/channel-monitors/*` (§10.6), `/api/feature-flags` (§10.4),
`/api/feedback` (§15.4), `/api/fonts/*` (§11.1), `/api/luts/*` (§11.3),
`/api/music/*` (§11.3), `/api/publishing/*` (§13.1), `/api/reference-styles/*` (§8),
`/api/sfx/*` (§11.3), `/api/social/*` + connect/callback/media (§13.2),
`/api/upload/*` (§12), `/api/watch-folder/*` (§10.5), `/api/api-keys/*` (§15.1).

---

# Not yet contracted

Endpoints the audit found with no contract, or surfaces that exist in only one layer:

1. **Learning status/rollback browser path** — backend VERIFIED, but no frontend proxy
   route (D1). Contracted here at the backend level; the proxy route is the missing piece.
2. **Recipes browser path** — backend VERIFIED on its branch, not in the frozen tip,
   no frontend proxy (D2). Not shippable until the branch merges.
3. **`GET /tasks/workflows`** — referenced in a docstring, no code anywhere (D3).
   Do not build against it.
4. **Trend Radar system** (tracker 5.10) — no module, scheduler, API or UI exists
   (audit §C). Nothing to contract.
5. **Separation UI** (audit F49) — backend complete (`separation_opt_in` on create,
   §1.2); zero frontend surface. The setting is contracted; the UI is unwired.
6. **Multipart upload part flow** (§12.2) — routes VERIFIED, part request/response
   fields not enumerated in this pack.
7. **Frontend-only routes** (§16) — inventory only; their request/response bodies were
   not verified for this pack.
8. **MCP server** (`mcp/`) — wraps this API for AI agents; its tool surface is not
   part of this pack.

# What was NOT checked

- **No live verification:** no server was run, no endpoint was called (lane rules:
  docs-only, no test runs/renders). Every VERIFIED status above means "read the route
  handler in code", not "observed over HTTP".
- **Service-defined payload shapes:** for list endpoints whose rows are built in
  services/repositories (social posts/performance, publishing board/analytics, task
  detail record fields, channel-monitor rows, editor `EditDocument` schema, watch-folder
  settings object), the route, auth and error codes are VERIFIED but the inner field
  lists are quoted from the handler's return where read and otherwise named as
  service-defined — the frontend should confirm field names against `/docs` or the
  service code before relying on unlisted fields.
- **Multipart upload part flow** (§12.2) field shapes; **clip trim/caption/edit
  operation payload keys** beyond what is stated (validated in-handler; the editor UI
  is the reference sender); **SSE event payload** fields for `GET /tasks/{id}/progress`
  beyond the stream contract.
- **Frontend proxy routes other than** `/api/tasks/[...path]` and `/api/preferences`:
  listed from the route inventory only; their pass-through behaviour was not traced
  handler by handler.
- **The frozen tip is a snapshot:** branch work (`feat/recipes-foundation`,
  `feat/learning-engine-backend`) is covered only as labelled above; anything merged
  after `5ea71bf` is out of scope for this pack.

# Sources

- `~/workspace/jcs-next/backend/src/api/routes/*.py` + `backend/src/main.py` (frozen tip `5ea71bf`) — route handlers, prefixes, auth, validators.
- `~/workspace/jcs-next/backend/src/auth_headers.py`, `backend/src/config.py:605`,
  `backend/src/clip_editor.py` (export presets), `backend/src/services/learning_service.py`,
  `backend/src/services/clip_export_service.py` (timeouts), `backend/src/auto_director/creation_modes.py`,
  `template_mode.py`, `media/music_library.py`, `media/common.py`.
- `~/workspace/jcs-next/frontend/src/app/api/**/route.ts` (inventory),
  `frontend/src/lib/clip-actions.ts` (`sendClipFeedback`).
- `~/workspace/jcs-recipes/backend/src/api/routes/recipes.py`,
  `backend/src/recipes.py`, `backend/src/repositories/recipe_repository.py`,
  `backend/src/main.py` (branch `feat/recipes-foundation`, tip `e09c5f2`).
- `~/workspace/jcs-learning-be/backend/src/api/routes/learning.py`,
  `backend/src/api/routes/tasks.py` (branch `feat/learning-engine-backend`, tip
  `4582a0f` — diff vs `5ea71bf` is tests only; routes identical).
- `~/workspace/research_notes/sprint-7day/LEARNING_ENGINE_API_CONTRACT.md`,
  `~/workspace/jcs-next/docs/api-reference.md`,
  `~/workspace/research_notes/sprint-7day/FULL_SCOPE_AUDIT.md` (§C forgotten connections).
