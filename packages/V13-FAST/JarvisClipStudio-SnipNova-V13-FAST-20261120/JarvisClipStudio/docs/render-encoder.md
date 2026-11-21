# Render encoder: the NVENC probe, the escape hatch, RENDER_PROFILE

Every final encode picks between `h264_nvenc` (GPU) and `libx264` (CPU).
The decision machinery lives in `backend/src/media/ffmpeg.py` (the probe
and the plain path's selector, `build_final_video_encode_args`) and
`backend/src/media/edl_render.py` (the EDL path's `get_video_encoder`).
Both paths consult the same probe — there is exactly one implementation —
and every render logs the encoder and profile it used
(`EDL encoder: ...`, `Final encode encoder: ...`, and the start/finish
lines of `render_edl_clip_ffmpeg`).

## The probe

`check_nvenc_available()` runs three stages, cheapest first, and caches
the verdict for the life of the process:

| Stage | What it does | What it proves |
| --- | --- | --- |
| Listing | `ffmpeg -encoders` must list `h264_nvenc` | the build knows the encoder |
| One-frame | one 256×256 frame to the null muxer (256×256 clears NVENC's 176×144 minimum frame size) | a session opens at all |
| Realistic | a 5.6s production-shaped encode: the EDL graph skeleton (segment normalisation, punch-in zoompan, `fps`/`settb=AVTB`, an xfade join, a libass subtitles burn, loudnorm) at 1080×1920 with the EDL path's exact encoder arguments into a real faststart MP4, validated by ffprobe (H.264 at 1080×1920, an AAC track, 5.6s ± 0.3) | the encode production actually runs |

The realistic stage is the point: the old check stopped at stage 2 and
passed on machines where every real graph encode then failed. A failure
at any stage is recorded — `last_nvenc_probe_error()` returns the stage
and the tail of its stderr — and logged at WARNING. The probe never
fails silently. `check_nvenc_realistic()` is also callable directly for
diagnostics; unlike the composed check it is not cached.

Deliberately not probed: input decode (production sources vary; decode
is a separate, already-working stage) and concurrent encode sessions
(driver session caps are machine-specific — if concurrent NVENC renders
ever fail with session errors, lower `CLIP_RENDER_CONCURRENCY`).

## The escape hatch

A passed probe can still be wrong for real encodes. When a render that
selected NVENC fails and its stderr names the GPU encoder itself —
an encode-session failure (`OpenEncodeSessionEx`), a driver library
that will not load (`libnvidia`, `libcuda`, `cuda`, `nvenc`), or the
encoder failing to open — `render_edl_clip_ffmpeg`:

1. logs the downgrade at ERROR, in plain words, naming the marker that
   fired;
2. calls `ffmpeg.disable_nvenc()`, so every later encoder choice in the
   process goes to libx264 (there is no re-enable; the next process
   re-probes from scratch);
3. rebuilds the clip with the CPU encoder and retries **once**. A second
   failure is a hard failure, as before.

Failures that do not implicate the encoder (a filter-graph error, a bad
input) never trigger the hatch — they would fail identically on libx264,
so the clip keeps its fail-fast behaviour, and the existing
audio-implicated plain-audio retry is unchanged. Because the disabled
flag is shared, plain-path encodes in the same process also go to CPU
after a hatch trip, but the hatch itself only wraps EDL renders.

## RENDER_PROFILE

`RENDER_PROFILE` (read by `get_render_profile()` in
`backend/src/media/common.py`) trades encode quality for wall time on
final encodes:

| | NVENC | libx264 |
| --- | --- | --- |
| `balanced` (default) | preset `p4`, CQ 22 | the caller's preset/CRF (shared default `medium`/19; EDL fallback `veryfast`/20) |
| `speed` | preset `p2`, CQ 24 | `veryfast`, CRF 21 — caller's preset/CRF are overridden |

Unset means balanced; an unknown value falls back to balanced with a
WARNING, so a typo can never silently change encode quality. The
balanced argument lists are byte-identical to the pre-profile code and
pinned by `tests/unit/test_encoder_profile_matrix.py`.

`RENDER_PROFILE`, `FORCE_CPU_ENCODE`, `CLIP_RENDER_CONCURRENCY` and
`RENDER_TIMEOUT_SEC` are passed through `docker-compose.yml` to the
backend, worker and ai-worker services, so a plain `docker compose up`
honours them — previously they only arrived via override files.

## Verification limits

CI has no GPU. NVENC behaviour is proven by the fake-ffmpeg PATH-shim
suites (`tests/unit/test_nvenc_probe_realistic.py`,
`tests/unit/test_edl_nvenc_escape_hatch.py`) and the argument matrix;
the probe graph itself is executed for real with CPU arguments
substituted (valid 1080×1920, 5.6s output). The NVENC leg of the
realistic probe can only be exercised on a machine with the GPU.
