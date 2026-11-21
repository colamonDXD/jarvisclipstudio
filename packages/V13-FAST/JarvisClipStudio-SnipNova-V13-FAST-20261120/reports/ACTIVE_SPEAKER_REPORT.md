# Active-Speaker Targeting — Lane Report

**Verdict: BUILT** (flag-gated, default OFF, NOT merged — main merges).
Branch `feature-active-speaker`, worktree `~/workspace/jcs-asd`, off next-milestone @ `be00fd2`.
Commits: `ab03872` landmarks accessor · `db4713a` evidence module · `e0e8fa3` planner bias · `9722c5f` tests · `2395c80` changelog.

The last buildable adoption from the blueprint (§3.4 TalkNet / Light-ASD, scout ADOPT #5):
in multi-face shots, aim the crop at the person actually **speaking** instead of the
largest/most-central face. Objective framing checks, all sandbox-proven:

| Metric (Bert fixture, ground-truth speech samples) | Speaker picked |
|---|---|
| App baseline — `subject_series` (presence × mean area) | **34.4%** (11/32) |
| Baseline on production analysis samples | 33.3% (11/33) |
| Argmax-area per sample (for reference) | 59.4% (19/32) |
| **Shipped path — full production chain, flag ON** | **66.7% (22/33)** |
| Shipped path — fixture-replay seam (conservative) | 53.1% (17/32) |
| Signal ceiling — window decisions only, 480w/15fps | ~71% |

Baseline → shipped = **+32 points** on the production chain (+19 on the conservative
replay) — clears the lane's ≥15-point build bar through the exact code that ships.

---

## Step 0(a) — Current targeting (code-verified at be00fd2)

- Analysis: `reframing.analyze_vertical_clip` decodes once at **480 px wide, 3 fps**
  (2 fps if source > 1200 s); `YuNetFaceDetector.detect()` returns only
  (x, y, w, h, score) — **YuNet's 5 landmarks were computed and discarded**.
- Target choice: `_Track.score = present_count × mean_area`; `subject_series()`
  follows suffix scores (presence-from-sample × mean area) with **0.8 s hysteresis**.
  In a two-host shot it welds to the bigger face for the whole shot.
- The fixture proves the cost: both hosts present 59/59 samples; the series picks
  Bert (bigger) permanently — Tom, who speaks ~70% of the labelled time, scores 0/21.
- Plug-in points used: `subject_series` suffix weights + Plain's series-subject
  choice. Deliberately NOT touched: split triggers, pair/fit decisions, the
  significant-track rules, the caption face guard, B-roll, selection.

## Step 0(b) — Candidate A: Light-ASD — OUT (say-so-fast, unmeasured)

Licence is clean (repo MIT; weights in-repo, ~1M params; AVA mAP 94.06%). It is out on
**integration shape**, the same failure family as DeepFilterNet:

- Its input contract is **dense 25 fps face-crop sequences (112×112 grey) + MFCC** via
  `python_speech_features`; its own pipeline is PySceneDetect + **S3FD with
  `device='cuda'` hardcoded**, `.cuda()` hardcoded throughout `ASD.py` /
  `Columbia_test.py`.
- Dependencies absent from the app env: **scipy (verified absent)**, plus
  python_speech_features, scenedetect, scikit-learn, pandas (torch present).
- The contract cannot be satisfied by a cost-capped plan-time sampled pass at any
  accuracy — it is a second full-rate detection stack, not a signal. A fixture score
  could not have changed the decision, so none was run.

TalkNet was not separately evaluated: same model family, same dense-input contract,
same dependency weight — the rejection rationale covers it.

## Step 0(c) — Candidate B: zero-dependency landmark signal — the build

YuNet already yields the nose tip and both mouth corners. Aperture = nose→mouth-midpoint
distance ÷ face height. Two findings shaped the signal:

1. **Motion magnitude is a trap.** Lip-motion energy and aperture *level* both scored
   20–44% — the *listener's* aperture is on average HIGHER during the other's speech
   (laughter, reaction mouths, nodding). What separates the speaker is
   **correlation**: the speaker's aperture rises and falls with the audio envelope;
   a reacting face moves on its own rhythm.
2. **Frame rate is set by aliasing, not accuracy.** At 10 fps the sampling Nyquist
   (5 Hz) sits inside the syllabic jaw-motion band and accuracy swings **52–72% with
   sampling phase**. At **15 fps** (Nyquist 7.5 Hz) both phases measure ~71% at 480w,
   and envelope smoothing only hurts. Window 2.0 s hop 1.0 s is the best and most
   phase-consistent size (1.5 s → 59%, 2 s → 71%, 3 s → 68%, phase-averaged).

Window admission: winner correlation ≥ 0.25. Window **weight** = winner's correlation
*margin over the runner-up* (contrast discounts mimicry windows where both faces track
the envelope; margin weighting beat raw-correlation weighting 53.1% vs 50.0% at the
planner seam and is insensitive to the admission bar, where raw weighting collapsed to
9.4% at a 0.30 bar). A still face in an audible window scores 0.0 rather than dropping
out, so the margin has a runner-up. Weights are bounded: 1 + 2 × margin (≤ 3×).

## Step 0(d) — Fixture

Luffy's Bert clip (`e560a01e…mp4`, 1080×1920, 30 fps, 25.83 s): Bert alone 0–4.03 s,
split stack Tom-top / Bert-bottom 4.03–24.0 s, Bert alone 24.0–25.83 s. Ground truth
from a self-generated faster-whisper (medium) transcript + frame inspection; ambiguous
spans ("Stop." 4.14–4.46, "He was like, oh" 16.41–17.35, laughter gaps) excluded.
**TOM:** 6.10–7.42, 10.39–13.53, 13.87–15.09, 15.35–15.89, 21.77–22.77 ·
**BERT:** 17.79–21.55. Full accuracy grid measured at 960w and 480w × 30/15/10 fps ×
windows 1.5/2/3 s × all sampling phases; scripts and data in `~/workspace/.tmp-asd-measure/`.

Known residual errors at the seam: two windows false-fire for Bert (5–7 s, 9–11 s —
reaction/mimicry), and Tom's 11–15 s turns produce no qualifying window at all at 480w.
The bias is advisory and bounded precisely so these cost a window, not the clip.

## What shipped

- `media/face_detection.py`: `YuNetFaceDetector.detect_with_landmarks()` — same scaling
  and clipping as `detect()` (which is untouched), returning boxes + the 5 landmarks.
- `media/active_speaker.py` (new): flag `ACTIVE_SPEAKER_TARGETING_ENABLED` (default
  OFF, separation-style opt-in parsing); span-scoped dense pass at **480 px / 15 fps**
  over keep ranges only; 100 Hz RMS envelope from a 16 kHz decode; per-sample
  (time, envelope, [(box, aperture)]) cached in a `<source>.asd_cache.json` sidecar
  (own schema + source stamp, main reframe cache untouched); pure signal math
  (`mouth_aperture`, `pearson`, `windowed_correlations`) unit-tested without video.
- `media/reframe_plan.py`: `get_source_analysis` attaches `analysis["speaking"]` only
  when the flag is on (any failure → view untouched); `subject_series` gains an
  optional `sample_weights` multiplier (default None = original arithmetic term for
  term, pinned by a golden parity test); `_speaking_bias` matches dense faces to the
  planner's own tracks by IoU and emits bounded weights + a per-shot `speaking` stamp
  (windows in compacted time, winner identified by median-box centre, correlations
  recorded — the SigLIP-stamp pattern); consumed at exactly two sites: the auto
  planner's single-subject series and Plain's series path, where Plain also sizes its
  window from the speaking face. Split/pair/fit decisions never see the bias.
- **Parity guarantees, pinned in tests:** flag off ⇒ plans deep-equal with/without
  speaking data present; unit weights == default weights output; missing/flat/failed
  evidence ⇒ today's behaviour. Plain stays full-bleed, one crop, never split/fit.

**Cost:** dense pass measured 24.9 s wall for a 20 s span on this throttled sandbox
(1.5 shared CPU, portrait 480×852) ≈ 1.25× realtime; landscape frames are smaller.
Cache hit: 0.33 s. Only runs when the flag is on, only over keep ranges.

## Tests

- New `tests/unit/test_active_speaker.py`: **13/13** (flag parsing, aperture geometry,
  Pearson guards, window scoring incl. still-face 0.0 and silence-no-windows,
  subject_series parity pin captured from the pre-lane implementation, bias flip,
  bias gating — flag/evidence/both, Plain flag-off byte-parity, Plain flag-on
  centres + sizes on the speaker + stamp shape).
- Neighbours, serial, one run: **139/139** — the 13 new + reframe plain/wire/
  face-boxes/border-trim/fit-blur-guard/split-render, caption face guard, face text
  guard, face detection, editor reframe.
- `changelog.test.ts` **4/4** (entry under 2026-11-12, flag-gated language).
- End-to-end on the real clip through production code (analysis → dense pass → bias →
  series): 33.3% → **66.7%**; sidecar cache written and re-hit.

## Remaining validation (user machine)

- Flag-on run on a real two-host 16:9 source (the fixture is a portrait render, so
  the planner seam was proven on seams + production analysis functions, not on a
  landscape end-to-end render): confirm crops follow the speaker on a fresh podcast
  clip, dense-pass wall time on the Ryzen, and no interaction with split decisions
  on genuine two-shots.
- Threshold sanity on more speakers/accents: the 0.25 admission bar and margin
  weighting are calibrated on ONE clip (two speakers) — the stamps make field
  evidence reviewable before any retune.
- TRACKER: this lane adds no tracker row by itself; it is the targeting half of the
  reframe-quality story and stays NEEDS REAL-WORLD VALIDATION until the flag-on
  landscape run lands.
