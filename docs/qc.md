# Clip quality control (QC)

Every clip the main pipeline renders is inspected before the task moves on.
The checks live in `backend/src/media/clip_qc.py`; the pipeline seam is
`TaskService._qc_persisted_clip`, which runs right after each clip is
persisted in `process_task`. The verdict (`qc_status`: `pass`, `warn`,
`fail`, or `error`) and the full evidence report (`qc_report`, JSON) are
stored on the clip row (migration `20261108_0002_clip_qc.sql`) and returned
by the clip serializers, so a bad render is visible in the API instead of
being silently shipped.

QC never fails a task. If QC itself crashes, the clip is stored with
`qc_status = "error"` and processing continues.

## Checks

| Check | What it measures | Pass | Warn | Fail |
| --- | --- | --- | --- | --- |
| `file` | File exists and ffprobe can read a duration and a video size | probes cleanly | — | missing or unprobeable (corrupt) |
| `duration` | Probed duration vs the duration recorded for the clip | within ±0.5s | — | differs by more than 0.5s |
| `resolution` | Displayed size vs the format's canvas (vertical family = 1080×1920, or the plan's short-side cap) | aspect within 2% of 9:16 and short side within 4px | — | anything else (`original` format always passes: it keeps the source size) |
| `audio` | Audio stream exists; overall RMS of 50ms windows over the whole clip | stream present, RMS above −55 dBFS | no audio stream, or RMS at/below −55 dBFS (digital silence) | — |
| `music` | Only when music was expected (a `music_override` pick, or the effective profile's `music_mood` at volume > 0): audio level inside speech gaps | gap RMS above −50 dB, or no silent gaps and a 5th-percentile floor at/above −42 dB | **`MUSIC MISSING`** — gaps totalling ≥0.8s measure at/below −50 dB RMS | — (never a hard failure) |
| `black_frames` | Mean luma of frames sampled at 10/25/50/75/90% (64×36 gray) | no sample at/below luma 10 | some samples near-black | **all** samples near-black |
| `frozen_frame` | Largest frame-to-frame mean difference over the clip (2 fps, 64×36 gray) | max difference above 1.0 | frozen but audio is audible (static content can be legitimate) | frozen **and** audio silent/missing |
| `duplicate` | Same task: source-range overlap (IoU) with sibling clips, or an identical SHA-256 (read from siblings' stored QC reports) | no sibling matches | IoU ≥ 0.5 with a sibling range, or byte-identical file | — |

Checks that cannot judge from the available evidence report `skip` with the
reason (for example a music-expected clip with too little speech-gap time to
measure). A `skip` never affects the overall status.

## How the thresholds were chosen

- **Duration ±0.5s** — renders legitimately drift by a frame or two from the
  planned length (crossfades, end padding); anything larger means the wrong
  footage or a truncated encode.
- **Near-black luma 10/255** — an encoded black frame measures 0–2 even with
  H.264 noise; the darkest real scene content sits well above 10 at 64×36.
- **Frozen difference 1.0/255** — a static slate measures ~0 frame-to-frame;
  any camera or screen motion is far above 1.
- **Silence at −55 dBFS RMS** — finished clips are loudnormed to −14 LUFS,
  so even quiet room tone sits far above −55. AAC-encoded digital silence
  measures ≈ −69 dB with this windowing; lossless silence ≈ −120 dB.
- **Music gaps** — speech gaps are windows at least 18 dB under the clip's
  speech level (75th percentile). Pause cleanup retains ~0.4s of silence at
  junctions, so a 0.3s run is a real gap and 0.8s of total gap is enough to
  judge. A ducked music bed in a gap plays at the profile volume — typically
  −25…−40 dBFS RMS — while a music-less render leaves the gap at digital
  silence, so the absent/present line is drawn at −50 dB gap RMS.

## The music check (why it exists)

On 2026-11-07 the pipeline shipped clips with no background music at all:
the EDL builder had no caller in the main flow, so clips rendered plain,
and nothing inspected the output. The wiring was fixed, and this check is
the tripwire that makes the class of bug impossible to ship silently again:
when a profile or an explicit user pick says a clip should have music, QC
measures the speech gaps and warns — naming the expected track — when they
are silent. It is a warning, not a failure, because a re-render cannot fix a
missing library file; the fix is in the library, and the warning says which
track to look for.

## Automatic re-render

A **hard failure** (any check with status `fail`: missing/corrupt file,
duration, resolution, all-black, frozen-and-silent) triggers **exactly one**
automatic re-render of that clip through the existing Lane-5 re-render
service (`ClipService.rerender_clip`), keeping the clip's effective niche
and the task's `music_override`, followed by a second QC pass. The second
report replaces the first, with `attempts: 2` and a `retry` marker recorded
in it.

The re-render deliberately does **not**:

- loop — a clip that fails twice stays in the task, flagged `qc_status =
  "fail"` with the full report;
- fail or block the task — the remaining clips render normally;
- trigger on warnings — warnings are information for the user, not render
  defects a second encode can fix;
- fire when QC itself errored — an `error` verdict means QC could not
  judge, not that the clip is bad.

If the re-render raises (for example the source video is gone), the
original flagged report is kept and annotated
`retry: {attempted: true, succeeded: false}`.

One related fix rides along: `rerender_clip` accepts an optional
`music_override` and applies it to the rebuilt EDL, mirroring the main
pipeline — previously a re-render silently dropped the user's music pick.

## Error log

There is no separate QC log table; the codebase's per-task trail is the
worker log plus the stored report. Every non-pass check is logged at
warning/error level with the task id, clip id, check name and measured
detail, and the full report — checks, evidence numbers, attempt count — is
on the clip row itself (`qc_report`).

## Honest limits — what QC does NOT check (yet)

- **Caption synchronisation** — QC does not compare burned captions against
  word timings. (Directive Part I lists it; it needs a caption-timing
  comparison pass that does not exist in this lane.)
- **Face cropping / face presence** — QC does not verify faces are in frame.
- **Incomplete sentences** — QC does not judge whether a clip cuts a thought
  in half; selection/cleanup already guards that upstream.
- **Bad ducking** — the music check hears *whether* a bed is present in the
  gaps; it does not grade ducking depth while speech is playing.
- **Music under continuous speech** — a clip with no measurable speech gaps
  and a low audio floor is reported `skip` for the music check rather than
  guessed at.
- **Duplicate detection scope** — range overlap is checked against all clips
  of the task; the byte-identical check covers clips whose QC reports are
  stored (clips rendered before QC existed have no hash on record, though
  their ranges are still compared).
- **Frozen sampling cap** — motion is sampled over at most the first 300s;
  finished clips are far shorter than that today.

## Rollback

The migration is additive. To roll back:
`ALTER TABLE generated_clips DROP COLUMN IF EXISTS qc_report;`
`ALTER TABLE generated_clips DROP COLUMN IF EXISTS qc_status;`
The QC seam treats a missing column through the repositories' existing
pre-migration fallback selects, and the QC module itself is self-contained.
