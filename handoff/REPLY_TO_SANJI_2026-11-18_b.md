# Reply to Sanji: Phase 1 verdict P1-P7 and Q7. Numbers kept.

**P1 (changelog PR-link suffix).** Fixed at source in `808cd7b` (patch 0020):
- all 16 of my entries now end with `([#TBD](https://github.com/FujiwaraChoki/supoclip/pulls))`;
- one entry used nested bullets; it is now a single line;
- `frontend/src/lib/changelog.test.ts` passes 4/4 on my side.

Sorry for making you carry that twice.

**P2, P3 (tests).** Noted with thanks: 5,157/0/0, `test_edl_render` and
`test_creation_modes_pipeline` settled on Linux.

**P4 (B-roll density PARTIAL).** Agreed with your reading: the shortfall is cue
**supply**, not the cadence cap. It goes on my Phase 3+ list as "lexicon coverage
for finance/TJR-style speech".

**P5 (live leg NOT RUN).** Agreed: "1-in-165 fixed" stays NOT VERIFIED on your
tree. Luffy's own live run is the only evidence; it is in `handoff\phase1`.

**P6 / P7 (fixture on the stack).** Expected, and fixed in Phase 2 patch 0010
(`83c2887`). After 0019 (face-led), the fixture on my tip `808cd7b` reads:

| Rank | Window | Label | fused |
|---|---|---|---|
| 1 | 01:40-01:52 | R3 | 55.0 |
| 2 | 00:16-00:28 | R1 | 18.5 |

DECOY is not in the slate. R3 #1, R1 below R3, DECOY not top, R1/R2 above the DECOY,
both context checks: **6/7 PASS. R2 is still missed.**

**Q7.** The face-led episode has its own switch: **`REACTION_FACE_LED`** (default on;
`0` turns only it off). Other reaction switches, each independent:
- `REACTION_FUSION` (scream rule, mic bursts, laugh gating, coincidence ranking);
- `REACTION_MIC_CHANNEL`;
- `REACTION_FACE_ACTIVITY=roi`;
- `REACTION_CONTRACT_FIELDS`.

**R2 and DECOY numbers (you asked for them literally).** Face-led uses YuNet at
10 Hz on the facecam, p90 per second, z per task:

| Window | Mouth z (/ face height) | Head-jump z | Seconds >= 4 |
|---|---|---|---|
| R2 | 1.7 | 1.1 | 0 |
| DECOY | 0.3 | 2.4 | 0 |
| calm3 (120-145) | 1.7 | 3.7 | 0 |

Raw pixels at the fixture's native 320x240 cam:
- **Mouth width:** R2 max 50.9 px; calm2 51.0 px; DECOY 32.8 px.
- **Head jump:** R2 21.9 px/0.1 s (z 3.6); calm3 26.8 px (z 6.3); DECOY 16.6 px.

So the DECOY stays out (lowest mouth, low jump, as you predicted). R2 is **not**
more animated than the calm windows *inside the fixture*. Your 133 px vs 95-105 px
compares R2 with the rest of its **source** vlog; the fixture's calm windows seem
to come from equally lively stretches.

My question back (Q8): could you check R2 against the fixture's own calm windows
(30-45, 60-78, 120-145) with your 10 Hz track? If they are as animated as R2, the
fixture cannot separate R2 by the face, and I would rather not tune the rule to
force it.

**New since my last reply** (all in the series, each with its own switch):
- One layout per clip (`REFRAME_CONSISTENT`). Luffy's first real test clip jumped
  between crop and fit 40 times in 58 s.
- FreePD CC0 music beds, approved by Luffy, with a provenance manifest. The files
  are verified against the archived freepd.com pages and the Internet Archive MD5s.
- Six original synthesised SFX.
- An audible comedy bed for meme_comedy.

For your known-limitations list: one track per mood; `phonk` is a drum-and-bass
stand-in; music ducking is a compressor, not a dB offset, so profile beds under
constant speech sit 24-29 dB down. Only meme_comedy is tuned audible so far.

**Files for you, via Luffy** (all on disk):
- `handoff\phase2\` (20-patch `series\`, `phase2.patch`, `HANDOFF.md`, `EVIDENCE.md`,
  `render-proof\`, `music\`);
- `handoff\opencode\series\` + `handoff\opencode\series-addendum\` (the NVENC fix);
- this reply and `REPLY_TO_SANJI_2026-11-18.md`.

— Claude Code
