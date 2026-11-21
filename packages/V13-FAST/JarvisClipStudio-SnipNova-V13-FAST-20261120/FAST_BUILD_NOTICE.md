# FAST BUILD NOTICE — SnipNova V13 FAST (2026-11-20)

**Read this before installing.** This is a FAST build, made at
Luffy's direct order ("give fastt that package", 2026-11-20) so the
new code could be in hand immediately. A full V13 package (with the
switch defaults flipped on, the view-sequencing peak fix, a targeted
re-gate of the final combination, an install rehearsal and a
deterministic double-build) follows as a separate delivery.

## What this ZIP is

- Source: branch `land/wave2` @ `35975edf` **plus the swear-mask fix
  only** (`fix/swear-full-stars` @ `27edda10`), combined in the
  scratch merge `d02b6b59` — the exact combination the wave-2
  landing lane pre-verified (affected tests 51 passed / 0 failed,
  changelog test 4/4).
- The base tree IS the fully gated `land/wave2`: backend unit
  **5,894 passed / 0 failed**, integration FAILED sets identical to
  the unmodified base (zero regressions), frontend **986 vitest /
  0 failed, tsc 0, next build 76/76**, licence gate PASS, fixtures
  streamer 6/7 + gameplay recall 1.0.
- Swear words now mask as full stars `***` everywhere (captions,
  titles, reaction quotes, clip questions) — Luffy's ruling.

## What is NOT in this fast ZIP (stated plainly)

- **The switch-default flips are NOT included.** All wave-2
  switches still ship OFF in this build: `HOOK_FINISH_V2`,
  `VIEW_SEQUENCING`, `LOOP_ENDINGS`, `PAYOFF_TIGHT`,
  `COMMENT_BAIT`, `BEAT_DENSITY`, `CAPTION_WORD_FIX`,
  `TRANSLIT_HINGLISH` (and `SENSEVOICE_FEATURES` stays off in
  every build — its tag recall is unproven). You can turn any of
  them on per-install via `.env`; the full V13 package flips the
  proven ones on by default.
- **The view-sequencing peak fix is NOT included** (it is still
  in its lane). Known effect in this build: with
  `VIEW_SEQUENCING` on and no recorded peak stamped, the layout
  sequence can anchor on the first re-hook punch-in instead of
  the loudest second and fire ~4.8 s early. Clips with a recorded
  peak are unaffected.
- **No re-gate of this exact combination** beyond the landing
  lane's scratch verification (51/51 affected tests, changelog
  4/4). The full V13 package runs the targeted re-gate.
- **No install rehearsal** was run on this ZIP, and it was
  **not** built with the deterministic double-build check. The
  installer scripts are the proven V12.1 ones, carried
  unchanged (their headers still say V12.1 — cosmetic only in
  this fast build; `verify.ps1`'s 218-expectation check list
  still matches this tree: 55 migrations, every listed feature
  file present, 8 containers).
- Smoke acceptance: NOT RUN for this build.

## Verified findings carried in this build

- **live/vod.py creator_on_camera (Item 1 — VERIFIED on this
  tree):** the VOD pick path already rejects every moment whose
  on-camera check fails, INCLUDING the "no facecam found" case —
  `live/vod.py:909-912` rejects on any `not ok` with no carve-out
  (Claude's series-B patch 0024 removed the old
  `"no facecam" not in why` tolerance from patch 0009), and
  `live/session.py:184-188` rejects the same way. No change was
  needed.
- **Live Groq captions verified on a real Kai Cenat VOD
  (28 clips) on the user's machine, 2026-11-19.** (This corrects
  the V12.1-era "not yet proven" line — see
  KNOWN_LIMITATIONS_V13_FAST.md.)

## UI

This build carries the V12.1 look: composer-hero create page,
bento tiles, liquid glass, vivid Galaxy starfield. Wave 2
changed no frontend files.
