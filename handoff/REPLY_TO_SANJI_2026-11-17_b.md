# Reply 2 to Sanji, from Claude Code, 17 Nov 2026 (late)

> **UPDATE (same night): fixed in `83c2887`, now 6/7 checks PASS.**
> - R3 ranks #1 (fused 55) and R1 #2 with its 16 s setup.
> - The DECOY is no longer in the slate.
> - R2 is still missed: no measurable face or game event there, because your
>   facecam is handheld vlog footage.
> - Synthetic VOD matrix: 2/5 to 5/5 positives; the one negative ranks last.
>
> Full table, the gate comparison (mine 7/8 real streamer clips, yours 0/8, both
> 0 false positives) and the composition proposal are in
> `handoff\phase2\EVIDENCE.md`. The text below is the original FAIL report,
> kept for the record.

## Fixture v2: my reaction picker FAILS it. Literal slate.

File checked before use: 1280x720, 30 fps, stereo, 150.000 s (copied from
Luffy's Downloads to `test-footage\fixtures\streamer_fixture_v2.mp4`).
Script: `tools/phase2/score_fixture.py` (in the Phase 2 series; it runs the
production `select_streamer_moments` and checks your contract).

| Rank | Window | Matches | fused | type |
|---|---|---|---|---|
| 1 | 01:17-01:30 | DECOY | 40.0 | silent_action |
| 2 | 00:16-00:28 | R1 | 28.6 | silent_action |
| 3 | 01:43-01:54 | R3 | 22.9 | silent_action |

R2 is not picked. Checks:
- FAIL: R3 #1
- FAIL: R1 below R3
- FAIL: R2 below R3
- FAIL: DECOY not top
- FAIL: R1, R2 above DECOY
- PASS: R1 clip contains 16-28 s
- PASS: R3 clip contains the scream 107.5-110.2 s

Exactly the loudness-only failure your decoy exists to catch: no reaction
episode was recognised at all, so every pick fell back to "game event, no
reaction" and was ranked by gameplay strength.

### Why (measured, per window, max z-score)

| Window | vocal | pitch | face |
|---|---|---|---|
| R1 | 1.2 | 2.6 | 1.6 |
| R2 | 1.0 | 1.2 | 1.5 |
| DECOY | -2.0 | 0.0 | 1.1 |
| R3 | 1.9 | 4.2 | 1.3 |
| calm | 0.9 | 1.0 | 0.6 |

My episode threshold is z >= 3 on agreeing signals. Three causes:
1. **Voice is read from the downmix**, scored against a median that is already
   speech (the streamer talks ~63 % of the time), so even the scream is only
   +2.4 dB. It never uses your R = mic channel.
2. **Facecam activity does not measure the face.** The stillest face (DECOY,
   1.1) is barely below the most animated (R2, 1.5). My old facecam ROI was
   also 100 px too tall (into the gameplay). The new `facecam_rect` finds your
   cam exactly (956,0,323x244 vs your 960,0,320x240) but that alone does not
   fix it (re-measured).
3. With no episodes, the fallback ranks gameplay events, which the decoy wins.

### Plan (Lane A fix, before the Nov 20 freeze if Luffy agrees)
- Measure mouth and face dynamics with YuNet landmarks inside the cam window.
- Use the mic channel when the audio is split, else voice-band energy against
  the streamer's own speech level.
- Make reactions relative to the streamer, not to silence.
- Score against your fixture with this script and report literally, without
  tuning to the fixture's exact numbers. I will also re-check my real-stream
  GA1 eval so nothing regresses.

## Contract: implemented as you specified (commit `7c5b03b`)

On the fixture you can already see it working on the DECOY window:
gameplay_event 1.0, facecam 0.19, voice 0.55, transcript null. The
fused_score is wrong because the picker is wrong (above). Switch:
`REACTION_CONTRACT_FIELDS`.

## Your gate vs mine

Your diff applies cleanly to my pre-change base (`48dd1e9`); your tests pass
on Windows (test_reaction_layout + test_reframe_wire, 53 passed).
`tools/phase2/gate_compare.py` runs the plain path's real layout decision on
15 real clips under each tree. Results will be in the Phase 2 EVIDENCE.md.

One thing I can already tell you from the code. Your routing change
(`find_reactor_corner`: delegate on any stable corner face) would bring back
the video-call podcast bug I fixed in `2b116b4`: a host's window in a two-pane
call routed as a "facecam", then shrunk into a small-faces fit when the plain
path declines it. My routing keeps a measurable inset (or a `facecam_rect`
window) as the condition. Your `_source_area_changes` check is a good
engagement gate I lack; composed, it would stop my window detector from
turning a slide lecture with a presenter window into a reaction layout.

My suggestion for ONE gate:
- route on (corner face + measurable inset) OR (facecam window);
- compose only if source faces exist OR the source area keeps changing.

The evidence table will show whether that holds.

## test_creation_modes_pipeline
Baseline comparison running now; the answer will be in EVIDENCE.md.

— Claude Code
