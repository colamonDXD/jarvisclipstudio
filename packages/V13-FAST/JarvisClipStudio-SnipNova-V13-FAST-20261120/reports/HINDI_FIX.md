# Fix report — Audit #2 findings A2-1 (Hindi guardrail) + A2-3 (broll_applied)

**Branch:** `fix-hindi-guardrail` off next-milestone @ `ae7e50d`, worktree `~/workspace/jcs-hindifix`, tree clean.
**Commits:** `1736134` fix(classify) — A2-1 · `9d4d70c` fix(render) — A2-3. Not merged, not packaged, not uploaded.

---

## A2-1 — Hindi content can never be guardrail-rescued to hindi_podcast (headline)

**Built, exactly in the audit's specified shape.** `backend/src/auto_director/classify.py` gains a language-based scorer, `_hindi_evidence_score(title, transcript_sample) -> (score, devanagari_dominant, share)`, wired into the existing `_evidence_guardrail_rescue`:

- **Primary signal (objective):** Devanagari share of letters, computed by `media.hinglish.devanagari_share` and judged against `media.hinglish.DEVANAGARI_SHARE_THRESHOLD` — the threshold is **imported, not re-typed**, so the classifier and the caption romanizer can never drift apart on what "Hindi-dominant" means. Dominance scores a decisive 6 (above the ≥ 4 rescue floor on its own: in pure Devanagari a transcript contains almost no Latin topic keywords, so it must not be held hostage to them).
- **Lock interaction (the audit's design call, decided as specified):** when the podcast lock is engaged, keyword rivals stay barred, but a Devanagari-dominant transcript may still rescue — to hindi_podcast only, competing against silence (runner-up 0). Rationale, recorded in the code: a Hindi podcast's correct profile IS hindi_podcast, so the lock's purpose (protecting genuine podcasts from rival niches) is not served by barring it. The lock is overridden by the language signal regardless of which lock leg fired (title, cues, or talking-head visual).
- **Secondary signal (shipped — precision was pinnable):** romanized-Hinglish markers — 29 precision-chosen Hindi function words (`kya`, `kyunki`, `nahin`, `matlab`, `doston`, `yaar`, `bhai`, `achha/accha`, `bahut`, `aur`, `mein`, …), each distinct marker +1. Unlike `_keyword_score`'s substring matching, markers are **word-boundary matched** (`\b`), which kills the substring collisions (`hai` inside "Thai"/"Shanghai", `aur` inside "restaurant"/"dinosaur", `tera` inside "terabyte"). Markers are keyword-class evidence: they compete normally and **never override the lock** — pinned by a dedicated test (a romanized-Hinglish podcast with 8+ markers stays podcast_news).
- **Pattern fidelity:** the scorer runs only inside the failure-path rescue (fallback results only — success path untouched by construction, and the rescue's `is_fallback` gate is unchanged), uses the same ≥ 4 floor and ≥ 2 margin, the same confidence clamp (0.55–0.70), the same reasons/evidence stamping, plus `evidence["devanagari_share"]` (rounded via the existing `_round3`) when hindi wins, so the rescue stays auditable. `exclude` is honoured for hindi too (the substituted-agent path's `exclude={travel_vlog}` call shape is pinned by test). The section's design-rules comment was amended to document the one deliberate lock exception. Import of hinglish is function-level, following the house pattern for auto_director→media imports (no import-time cv2/media.common drag into classify).

**Cases pinned** (12 new tests in `test_auto_director_classify_guardrails.py`, fixtures copied verbatim from the audit's `leg1_classify.py`):
- Audit variant A — podcast-titled Devanagari transcript (share 0.90, locked) → **hindi_podcast rescued**, `guardrail_rescued=True`, `is_fallback=True`, evidence share recorded.
- Audit variant B — Devanagari business talk, no "podcast" anywhere (share 1.00) → **hindi_podcast rescued** (before: all rivals 0, no rescue attempted).
- Romanized-Hinglish rescue (share 0.0, markers only, unlocked) → hindi_podcast.
- End-to-end through `classify_niche` with every candidate failing → hindi_podcast + `load_profile` loads the Hindi profile.
- English podcast control (audit fixture) → stays podcast_news, no rescue stamp.
- Romanized markers under the lock → **no rescue** (deliberate boundary: a romanized-Hinglish show that presents as a podcast keeps podcast_news; only the script signal passes the lock).
- Devanagari-light English finance talk (a few Hindi words + one "yaar", share < 0.30) → **finance_business wins on its own evidence**, hindi score sub-threshold.
- Marker-collision precision: "chow mein" + a neighbour named Abhi in plain English → hindi score exactly 1, no rescue.
- Mechanics: fixture-dominance hygiene (both audit fixtures verified ≥ 0.30 so the tests can't silently rot), the 0.30 boundary itself (3 Devanagari letters in 10 = dominant; in 11 = not), distinct-marker counting (spam counts once).
- Independent smoke run of the audit's own 7-case driver against the new code: gaming/music/music-audio/finance unchanged, both Hindi variants land, control holds.

**Known remaining miss (deliberate):** a *romanized* (Latin-script) Hindi podcast — Whisper's minority output shape — that also trips the podcast lock stays on podcast_news. That is the task brief's own boundary (keyword-class signals may not override the lock), accepted for precision.

## A2-3 — layout_report.broll_applied blind on the EDL path (secondary; contained, so built)

**Judgement: contained field-wiring fix — built as separate commit `9d4d70c`, not a QC-semantics change.** The flag's only consumers keep-or-clear the clip's recorded B-roll placements by it (`video_service.py:1957`, `clip_service.py:2383` → `save_clip_broll`), and those placements are what re-renders carry — so the wrong False was also silently stripping EDL-path clips' carry. No consumer re-renders or re-judges by the flag.

- New predicate `edl_broll_will_composite(edl)` in `media/edl_render.py`, placed beside `build_edl_filter_graph` and **mirroring its exact participation rule**: dict entry, `local_path` an existing file, positive `end > start` window. Unresolved entries (no `local_path` — the no-provider-key case) never count.
- `create_optimized_clip` (`video_utils.py`) now computes the flag as: successful render AND (legacy overlays actually used by the surviving render OR the EDL predicate). The retry-without-overlays case is handled explicitly (the retry still passes the EDL, so its entries count when the retry is the successful render). Docstring updated.
- Tests (`test_layout_report_broll_applied.py`, 9): predicate unit pins (resolved/unresolved/missing-file/bad-window/junk-entries/empty) + real-FFmpeg wiring through `create_optimized_clip` — resolved EDL entry reports **True and its footage measurably composites** (>80% of frame pixels inside the window, <5% outside); unresolved entry renders clean and reports **False**; legacy overlays parameter still reports True (its False case remains pinned in test_broll.py, green).

## Verification (all serial, TMPDIR → ~/workspace/.tmp-hindifix, summaries from log files)

| Suite | Result |
|---|---|
| Classify family (guardrails, battery, classify, evidence, chain, classification persistence) | **91 passed / 0 failed** |
| — of which the 12 new Hindi tests | **12 passed** |
| A2-3 new file (predicate + real-render wiring) | **9 passed** |
| B-roll family (broll, audit-fixes, rerender-render, word-sense, pixabay, clip-rerender) | **103 passed / 0 failed** |
| EDL family (edl_render, edl_render_audio, pipeline wiring, force-cpu, nvenc escape, transitions/SFX, video_utils effects) | **157 passed / 0 failed** |
| A/V sync (renders through the changed create_optimized_clip) | **11 passed / 0 failed** |
| changelog.test.ts (run immediately after EACH CHANGELOG edit) | **4/4, twice** |

No existing test was changed or weakened anywhere in this lane. CHANGELOG entries: one **Fixed** per finding, both in the 2026-11-11 section. Full-suite gate not run in-lane (main's gate covers the merged tip).

## Five states

- **IMPLEMENTED:** yes — both findings, branch `fix-hindi-guardrail` @ `9d4d70c`.
- **INDIVIDUALLY TESTED:** yes — 12 Hindi pins + 9 A2-3 pins, all green; audit's own 7-case driver re-run against the new code lands 7/7.
- **INTEGRATED:** branch-only — the scorer sits inside the existing rescue used by both production failure paths (candidate-chain failure, outer exception) and the substituted-agent path; A2-3 sits in the single `create_optimized_clip` seam both services consume. Not merged.
- **CROSS-FEATURE VERIFIED:** partially — rescued profile load pinned; B-roll/QC neighbour families green (103 + 157 + 11); the A2-3 wiring proven against real compositing renders. Not exercised: a full pipeline task end-to-end with a failing classifier on Hindi source (no provider keys in sandbox — same blocked leg as the audit).
- **USER-MACHINE VALIDATION PENDING:** a Hindi video run on his build with classification forced/observed failing — expect hindi_podcast with Roman-Hinglish captions instead of the podcast fallback; and an Auto-template clip with B-roll keeping its placements record (and carrying them into a re-render).
