# KNOWN LIMITATIONS — V13 FAST build (2026-11-20)

- **Live Groq captions verified on a real Kai Cenat VOD (28 clips) on the user's machine, 2026-11-19.**
  (This replaces the V12.1-era lines that called live Groq captions
  unverified / not yet proven — those are stale and must not be
  carried forward.)
- Wave-2 finishing features (HookFinish v2 opening, view
  sequencing, loop/payoff-tight endings, comment bait, beat-density
  audit + repair, caption-word fixer, Hinglish transliteration)
  ship with their switches OFF in this fast build; turn them on
  per-install via `.env`. The full V13 package flips the proven
  ones on by default.
- View sequencing: with no recorded peak stamped, the sequence can
  anchor ~4.8 s early (first re-hook punch-in instead of the
  loudest second). The stamped production path is unaffected; the
  fix is in its lane and lands in the full V13 package.
- SenseVoice emotion/event tags ship OFF and unproven: tag recall
  on genuine laughter/applause/cheering audio is NOT PROVEN.
- B-roll density on finance speech: ~1.67 inserts/min (target
  3/min) — PARTIAL.
- Very quiet reactions can be missed by the picker (the R2 class;
  streamer fixture 6/7 — the accepted, measured borderline).
- DeepSeek is dormant (no account credit); Groq
  openai/gpt-oss-120b is the effective primary brain.
- Landscape sources above 1080p are worked in a 1920×1080 box
  (vertical clips pass through untouched).
- Music beds can sit quiet under speech on some profiles.
- Green-screen / virtual facecams can be missed by facecam
  detection.
