# Live clipping MVP: status 2026-11-18 ~17:10 IST (honest)

Approved by Luffy at ~16:10. Branch `lane-a/live-mvp` on top of `e833f37`. Everything is behind `LIVE_CLIPPING` (off). It is NOT wired to routes, workers or UI yet, and NOT for V12.

## Works (verified)

| Piece | Evidence |
|---|---|
| Rolling recorder (yt-dlp resolve → ffmpeg stream copy into segments, watchdog, reconnect as a new run, wall-clock per segment) | Real public-domain live stream (NASA): 35 s buffered, 0 gaps; `real_live_cut_window.mp4` / `.png`. Fake-live tests: a killed pull reconnects, the gap is reported |
| Cut from the past (pre-roll from footage already on disk) | Window cut from 20–5 s before the request on the real stream; tests |
| Fuser rules (chat alone never clips; spam/raid spikes ignored; chat after a reaction boosts; configurable pre/post-roll; de-dupe; visual veto; weak picker items dropped) | `test_live_fuser` 10/10 (synthetic) |
| Reaction picker on buffer windows | Finds the 02:01 vocal burst on the Kai sample with its trigger named |
| Fast voice pass (picker's voice rules, causal baseline) | `test_live_voice_scan` 3/3; flagged laughter at 31 s / 34 s on the Kai sample, live |
| Quick preview | 5–16 s render after a candidate; one audio-led candidate had its preview **46 s after the moment while live** |

## Not good enough yet (measured)

1. **Confirmation latency.** The full picker costs ~75–100 s per call even on a 30 s window: a fixed per-call cost (facecam search + detector setup), not proportional to length. Flags queue behind each other (the 2nd flag waited 102 s).
   **Next:** keep the facecam ROI and detectors alive across calls (one-time search per session); confirm on the face signal only; run confirmations in parallel with the voice pass.
2. **Recall.** The voice pass did not flag the 02:01 vocal burst that the full picker finds.
   **Next:** align with the picker's episode rule (agreement over a 2 s window, laugh/pitch floors) and replay the Phase 2 fixture (R1/R3) as fake live to measure recall against ground truth.
3. **Bug.** One confirmed reaction did not become a candidate in the demo loop; to trace (wall-clock mapping or the de-dupe key).
4. **Chat** comes from Antigravity's `chat_signals` module (brief in `handoff\opencode-live\`); fuser support for its contract is done.
5. **Not built:** live API routes + SSE events + LIVE_MVP_CONTRACT.md for Sanji's UI; the live-ingest compose service; the final render through the normal pipeline; YouTube chat (needs Luffy's free API key later).

## Files
- `backend/src/live/`: `recorder.py`, `buffer.py`, `signals.py`, `reaction_scan.py`, `voice_scan.py`, `fuser.py`, `preview.py`
- `tools/live/demo_fake_live.py`
- Tests: `test_live_buffer` (6), `test_live_fuser` (10), `test_live_voice_scan` (3)
