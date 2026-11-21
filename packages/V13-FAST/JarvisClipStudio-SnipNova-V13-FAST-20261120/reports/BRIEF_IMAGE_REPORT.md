# Brief-from-Screenshot — Report

**Lane:** BRIEF-FROM-SCREENSHOT (Luffy's direct ask, 2026-11-12 ~14:57 IST)
**Branch:** `feature-brief-image` off `next-milestone` @ `d3eb750` — worktree `~/workspace/jcs-briefimg`
**Status:** IMPLEMENTED + TESTED in-branch · **NOT MERGED** · user-machine validation pending
**Cost:** ₹0 · briefs system only · no auto-posting · no keys requested or used

Commits (5, `type(scope): summary`):

| Commit | What |
|---|---|
| `cb237b1` | feat(briefs): excluded ranges, flag points and key timestamps in brief schema v2 |
| `d21ce25` | feat(briefs): LLM extraction schema learns excluded ranges, flag points, key timestamps |
| `9b91a7e` | feat(briefs): screenshot ingestion (OCR) + hard exclusion enforcement |
| `5763b5a` | test(briefs): screenshot-brief schema, OCR, extraction and enforcement suites |
| `ec9733e` | feat(briefs): screenshot upload + never-use/flag/key sections in the brief UI (+ CHANGELOG, i18n ×6) |

---

## 1. Step 0 — what already existed vs what was missing

### Already existed (verified in-tree, not trusted from notes)

The Content Requirements system (shipped Nov 9) was real and complete for its original taxonomy:

- Brief schema v2 (`backend/src/auto_director/content_requirements.py`): requirement types `must_mention`, `topic_focus`, `structure_hook`, `structure_cta`, `visual_presence`, `source_restriction` (via allowed ranges), `caption_rule`, `info_only`.
- `POST /briefs/extract`: paste-a-brief → LLM extraction (denylist-guarded) with deterministic fallback → **draft only, persists nothing, never silently applied**. The user reviews/confirms in the panel.
- Campaign brief library (save/load via publishing routes; picker reuses the same panel state).
- Enforcement: S1 candidate clamp + requirement candidates + tier partition (`ai.py`), S2 `_apply_brief_gate` final-slate gate (`video_service.py`), R1 render clamp in `create_single_clip`, R2 last-line audit in `create_optimized_clip` (`video_utils.py`), re-render clamp (`clip_service.py`), post-render `evaluate_clip_compliance`.
- The theme mechanism: a theme sentence can be a mandatory `topic_focus`; it reaches selection via the tier machinery and the brief summary text.

### Proven absent (the actual build scope)

1. **`excluded_ranges`** — hard "never use this footage" ranges. Only the positive (allowed) sign existed.
2. **`flag_points`** — single timestamps with a review category + note.
3. **`key_timestamps`** — the brief's own named preferred moments. Requirement candidates existed for topics/phrases, but nothing built candidates around a brief-named moment.
4. **Image ingestion** — no upload + OCR path into the brief system at all.
5. **Section-aware deterministic extraction** — worse than absent: before this lane, a range pair like `0:47 – 0:53` appearing *anywhere* in pasted/OCR text (including under a "Content to avoid" heading) could be extracted as an **allowed range** — exactly backwards for an avoid list. The extractor now splits at the avoid heading first.

### Image path facts (verified)

- `backend/Dockerfile` lines 9–10 install `tesseract-ocr` + `tesseract-ocr-eng` — the shipped image **does** carry tesseract (the earlier gate note was checked, not trusted).
- In-app OCR precedent exists: `reference_style.py` invokes tesseract via stdin (`--psm 6`, `OMP_THREAD_LIMIT=1`, timeout, silent degradation, `(result, available)` contract). The new `brief_image.py` mirrors that contract.
- Upload patterns (`UploadFile = File(...)`) exist throughout the backend; the Next.js brief proxy forwards the raw body + Content-Type, so multipart needed **no proxy change**.
- **No vision-LLM route exists or was built.** Groq vision is unverified and no vision model is wired anywhere. OCR → text → the existing extraction pipeline is the only honest route, and §2 shows it is sufficient for this fixture.

---

## 2. OCR + extraction score on Luffy's actual screenshot

Fixture: `~/workspace/user/media_library/image/image-479.png` (natural-diamonds / Botswana brief), sandbox tesseract 5.3.4, `--psm 6` (the in-app invocation).

**OCR: 25/25 ground-truth timecodes recovered** — 01:30, 03:18, 17:30, 39:20, 42:51 (keys); 0:47, 0:53 (exclude pair); 03:13, 03:58, 10:21, 10:24, 20:30, 28:23 (plates); 02:24, 14:42, 29:02, 36:20 (branding); 13:55, 18:30 (music); 00:26, 09:46, 21:23 (sensitive); 12:10, 19:09 (review); 29:39 (alcohol). Headings, the theme sentence, category labels and notes all survived OCR readably.

**Deterministic extraction of that OCR text** (what the user gets with no LLM configured):

| Field family | Score |
|---|---|
| Key timestamps | **5/5** (90s, 198s, 1050s, 2360s, 2571s, with descriptions) |
| Excluded ranges | **1/1** — 47–53s as `excluded_ranges`; `allowed_ranges` correctly **empty** (the inversion bug is fixed and pinned by test) |
| Flag points | **18/18 timestamps, 18/18 categories** — licence_plate 6/6, branding 4/4, copyright_music 2/2, sensitive_topic 3/3, review 2/2, alcohol 1/1 |
| Theme | **1/1** — mandatory `topic_focus` with topics `["natural diamonds"]`, full theme sentence preserved |

Honest blemishes (both pinned in the fixture test as-is, not hidden):

- The 17:30 key description truncates at the screenshot's own line wrap: "…education and student" (the word "opportunities" wraps to the next OCR line and is not joined).
- Two copyright-music flag notes carry a trailing OCR glyph: "avoid uncleared audio. ¢".

The LLM extraction schema (`content_requirements_llm.py`) was extended with the same three field families plus prompt instructions (avoid ranges are never allowed ranges; "connect back to X" → mandatory topic_focus). No live LLM call was made in the sandbox (no keys, by design); the LLM path is covered by the existing llm suite in the neighbour run, and its quality on this brief is a user-machine check.

---

## 3. What was built

**Schema (`cb237b1`)** — brief v2 gains, all optional and additively normalised (a brief without them keeps its exact previous shape):

- `excluded_ranges`: same item shape/parsing as allowed ranges, mirror semantics. Range algebra: `subtract_ranges`, `clamp_segment_outside_excluded`, `excluded_overlap_pieces` (0.5s fragment floor, boundary-touch is not overlap).
- `flag_points` (cap 40, note ≤ 200 chars): `{at_seconds, at_label, category, note}`; categories `licence_plate | branding | copyright_music | sensitive_topic | alcohol | review | other`. Unknown categories **coerce to `other`** — the note keeps the brief's own words instead of rejecting the whole brief.
- `key_timestamps` (cap 20): `{at_seconds, at_label, description}`.
- A brief containing only one of the new families no longer normalises to `None`.
- `generate_key_timestamp_candidates`: the transcript line containing each key timestamp expands to clip-duration bounds exactly like requirement candidates (ideal-minimum growth, maximum cap), must sit inside allowed ranges, excluded footage subtracted; a key whose surviving footage falls below the minimum yields nothing.
- Section-aware deterministic extractor (§1, item 5) + the "connect back to X" theme rule.
- Compliance: excluded overlap = hard violation (`non_compliant`, violations listed); a clip containing a flag point is stamped `needs_review` with the flag list, and its status becomes `uncertain` when otherwise clean — flagged is never silently "compliant". `compliance_summary_for` additively surfaces `needs_review` / `flag_points` / `flag_categories` only when flags exist.
- `brief_summary_text` (the prompt-facing summary) now includes excluded ranges and flag/key counts.

**Ingestion + enforcement (`9b91a7e`)**:

- `backend/src/auto_director/brief_image.py`: local tesseract OCR of uploaded bytes (OpenCV decode → PNG via stdin, `-l eng --psm 6`, 30s timeout, one 2×-upscale retry for small images with weak first pass). Returns `(text, available)`; `(None, False)` = binary missing, `(None, True)` = unreadable/no text.
- `POST /briefs/extract-image` (same auth as `/extract`): PNG/JPG/WebP, empty → 400, >10 MB → 413, OCR unavailable → 503, no readable text → 422, recognised text > 20,000 chars → 400. Success returns the draft **plus `brief_text` (the exact OCR text) and `ocr.characters`** — and persists nothing, exactly like `/extract`. Both routes share one `_build_draft`.
- Exclusions are HARD at every layer allowed ranges already guard:
  - **S1** (`ai.py`): whole candidate pool (model picks + requirement candidates + key-timestamp candidates) clamped outside exclusions; drops counted separately (`candidates_dropped_excluded`).
  - **S2** (`video_service._apply_brief_gate`): slate re-clamped; swallowed segments dropped.
  - **R1** (`create_single_clip`): keep-ranges intersected with allowed **and** subtracted by excluded after the last mutator; < 0.5s surviving renders nothing rather than leaking.
  - **R2** (`create_optimized_clip`): post-sentence-extension audit applies the same subtraction (sentence extension cannot push a range back into an excluded zone).
  - **Re-render** (`clip_service`): `_brief_excluded_ranges` sibling helper, localised through the time map like allowed ranges.
  - Call sites pass the new kwargs **only when a brief constrains the task**, so non-brief paths keep their historic call shape.

**Frontend (`ec9733e`)**:

- `content-brief.ts`: payload/stored/draft types for the three families; payload keys emitted **only when non-empty** (briefs without them keep their exact previous payload shape — pinned by the pre-existing exact-equality panel test); `extractBriefImage` (FormData, no manual Content-Type); compliance types learn `needs_review` / `flags` / excluded violations.
- Panel: an "Upload brief screenshot" control beside paste-extract. On success the OCR text lands **in the paste box** (the user reviews exactly what the machine read) and the draft waits for the explicit **Use this draft** click — the never-silently-applied rule is unchanged. Editable sections: never-use ranges (reuses the range row), key timestamps, flag points (time + category + note), all following the existing row patterns and caps. The pre-generation summary mirrors all three families plus a hard-blocked enforcement line.
- Task page: the clip compliance section stamps flagged clips **"Needs review"** (time, category label, the brief's own note) and calls out excluded-footage hits.
- Campaign library: save/load flows through the same payload/panel-state conversions, so campaign briefs carry the new fields with no extra work.
- i18n: all new keys in all six locales (en/de/es/id/pt/zh), placeholders matched; CHANGELOG entry added under 2026-11-12.

---

## 4. Enforcement proof (tests, all serial, logs on disk)

| Suite | Result |
|---|---|
| New: `test_content_requirements_brief_image.py` (19 tests) | **19 passed** |
| Neighbours: 13 files (all content-requirements suites + clip rerender + clip duration) | **163 passed, 0 failed** (content-requirements subset = 144) |
| Render neighbours: 7 files (beat pipeline, brand-kit render, b-roll, caption face guard, clip boundaries, clip edit, clip timeline regressions) | **113 passed, 0 failed** |
| Frontend: content-brief lib + panel + messages parity + load-messages | **32 passed** |
| Frontend neighbours: brief-compliance + campaign-brief-picker | **9 passed** |
| `changelog.test.ts` | **4/4** |
| `tsc --noEmit` | 11 errors — **byte-identical set to the pre-lane base** (missing generated Prisma in this worktree; none in touched files) |

Zero-tolerance cases pinned by test: candidate fully inside an exclusion dropped at S1 and S2; straddling candidate clamped to a surviving piece with **zero overlap**; boundary-touching spans survive whole; sub-0.5s slivers are dropped, never emitted; rendered clip overlapping an exclusion reports `non_compliant`; clip containing a flag point reports `needs_review: true` + `uncertain`; key timestamp inside an exclusion yields no candidate; the fixture's avoid range can never become an allowed range.

Theme carriage is proven, not asserted: `brief_summary_text` includes the theme, and the existing S1 tier machinery fails an off-theme window (Tier C, requirement `failed`) and passes a diamond-saturated one (Tier A) under the extracted fixture brief.

---

## 5. Boundaries — stated plainly

- **Flags do NOT trigger automatic licence-plate blurring** (or any automatic edit). A flag point stamps the clip "needs review" for a human. Auto-blur is explicitly out of scope for this lane.
- **No vision-LLM route was built.** OCR is local tesseract only; the screenshot never leaves the machine, and no image is sent to any model.
- OCR quality is fixture-proven for this screenshot style (clean UI text). A photograph of a paper brief, handwriting, or a heavily compressed screenshot will read worse; the review step (OCR text in the paste box + draft confirmation) is the safeguard, and the 422 response says honestly when no readable text was found.
- Excluded ranges carry no label field — same as allowed ranges in schema v2; the descriptive words live on flag points and key timestamps instead.

## 6. User-machine validation still pending

1. **Live run on Luffy's install**: upload the same screenshot in the create-page panel on his machine (Docker image OCR + real auth/session path), confirm the draft matches §2, create a task, and confirm (a) no clip contains 0:47–0:53, (b) a clip covering 3:13 is stamped needs-review, (c) key-timestamp candidates appear.
2. **LLM extraction quality** with his Groq key on this brief (sandbox had no keys; deterministic scores above are the floor, not the ceiling).
3. This lane is **not merged** and is not in the sealed v4 package (SHA `4a769b1f…` unchanged); it lands in the next package after a merge decision.

*Note: this lane supersedes the multi-provider lane's closing remark (SPRINT_LOG, 2026-11-12 ~15:05 IST) that in-app image upload "would be new scope — his call": the call was this lane's task, and it is built.*
