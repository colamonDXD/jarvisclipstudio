# Viral Scout Pack item 3 — Audio-Reactive Flashes

**Lane:** Wave 4, viral-editing scout follow-up (2026-11-09 scout, item 3)
**Branch:** `feature-viral-flashes` off next-milestone @ 6767093, worktree `~/workspace/jcs-flashes`
**Status:** Built, render-verified, NOT merged / packaged / uploaded (per brief).
**Commits:** `66ee479` feat(auto-director) planning+EDL+profiles+tests · `a95f775` feat(render) fragments · `ed8fba8` feat(pipeline) source-grid seam · `0afe43d` docs(changelog)

---

## Step 0 — Premise check (what already existed, what was missing)

1. **Beat grid exists and is rich.** `auto_director/beat_grid.py` already does spectral-flux onset DSP: `analyse_audio_beats()` (sidecar-cached per source) returns `{tempo_bpm, beat_times, drop_time, energy_curve{hop 0.05s, levels_db}}`. `prepare_clip_grid` / `resolve_clip_grid` project it onto a clip window.
2. **But the projected grid reaches `build_edl` only for the 5 beat profiles** (gaming, music_artist, sports, football_edit, anime_funk_edit), and `prepare_clip_grid` refuses tempo-less grids. Styles like meme_comedy never see any audio analysis at EDL time. → The missing piece was a **levels-only route** (levels + drop + beats, no tempo gate), not a new detector.
3. **No timed brightness effect existed.** "Flash In/Out" in the codebase are velocity-curve *speed ramps*, not brightness. `fadewhite` exists only as an xfade transition name (a crossfade, not a pulse). The graph's only `eq brightness` was the static grade chain. → The render pulse had to be built; it follows the studio-look fragment pattern.
4. **No standalone transient detector existed** — detection was built on the grid's existing 50 ms level envelope plus its drop/beat labels, exactly as the brief allowed. No new DSP, no new dependency.
5. **The viral plan had no visual pulse channel** — emphasis today is punch zooms + caption emphasis under restraint budgets (`clip_plan.py`). Profiles are the natural home for the opt-in knob: profiles already carry an `effects` section (sports' `zoom_punch`), so `effects.audio_flashes {enabled, intensity}` fits the existing schema conventions.

**Scope shrink vs. the naive reading of the brief:** no new onset detector, no new analysis pass — the flashes ride the beat grid's cached analysis. New code is: one pure planning module, an EDL stamp, one render fragment builder, one pipeline seam.

---

## What was built

### Planning — `backend/src/auto_director/audio_flashes.py` (new, pure)
- `profile_flash_config(profile)` — reads the profile's additive `effects.audio_flashes` section; returns clamped `{intensity, brightness, gamma, duration_frames, min_rise_db, min_gap_sec, max_per_clip}` or `None`. Junk sections, `enabled: false`, zero/negative intensity → `None`. Brightness = 0.16 × intensity (max 0.16), gamma = 1 + 0.30 × intensity, duration default 3 frames (clamped 1–5).
- `detect_flash_points(levels_db, hop, …)` — a candidate is a 50 ms window rising ≥ 5 dB over the median of the trailing 6 windows, landing at/above both the clip's 55th-percentile loudness and an absolute −30 dBFS floor (a sharp rise into still-quiet audio is a click, not a hit). The flash centre is the loudest window within +2 windows. Labels: within 0.15 s of the grid's **drop** → `drop` (the drop is also admitted on its own measured evidence ≥ 2 dB when detection itself missed it); within 0.06 s of a beat → `beat`; else `transient`. Restraint, strongest-first greedy: **min gap 0.8 s, cap 6 per clip, ≤ 3 in any 3 s window** (exact rolling check). Pulses that would hang off either clip end are refused, not clipped.
- `project_source_levels(analysis, clip_start, clip_duration)` — levels-first projection of the source analysis onto the clip (slices levels, shifts beats/drop); **no tempo gate**, unlike `prepare_clip_grid`. `resolve_flash_grid(...)` mirrors `resolve_clip_grid`'s best-effort contract: any failure → `None`.

### EDL — `auto_director/edl.py`
- `build_edl(..., flash_grid=None)` (new kwarg; the beat grid wins when both are passed — it describes the final mix). After the signature stamp, surviving points are stamped as
  `edl["audio_flashes"] = {points[{at_sec, source, rise_db, rendered_at_sec}], intensity, brightness, gamma, duration_frames, grid_source}`.
- `rendered_at_sec` comes from a new speed-+fade-aware walk (`_rendered_time_in_segments`) over the final join plan, so pulses stay glued to their hit through speed ramps and crossfades — verified in the pre-build sanity check (a point after the signature join shifted by exactly the absorbed crossfade, −0.35 s).
- The establishing-shot guard applies to flashes too (no pulse inside a protected opening shot).
- `template_mode.plainify_edl` pops `audio_flashes` — **Plain contains none of it**, by construction and by test.

### Profiles opted in (intensity 0.5)
gaming, meme_comedy, sports, music_artist, football_edit, anime_funk_edit — the fast-cut grammars. The other 8 profiles are untouched, so their EDLs are byte-identical to before (proven by the unchanged neighbour suites). meme_comedy is the notable addition: it has no beat grid, so its flashes come from the new source-levels route and read the source audio (no music bed exists in that path — declared boundary below).

### Render — `media/edl_render.py`
- `build_flash_fragments(edl, input_label, next_id)` — chains one `eq=brightness=…:gamma=…:enable='between(t,start,end)'` per point on the joined picture, called immediately after the studio-look fragments and **before overlays/captions, so on-screen text is never flashed**. No stamp / empty points / zero brightness → no fragments, label untouched: the graph is byte-identical for every non-flash EDL.

### Pipeline — `services/video_service.py`
- In the existing EDL seam: `flash_grid` is resolved only when template mode ≠ Plain AND no beat grid was resolved AND the profile opts in. Best-effort like the beat seam — any failure builds the EDL without flashes; nothing about existing behaviour changes when resolution fails or the profile doesn't opt in.

---

## Proof (all through the production EDL path)

New suite `backend/tests/unit/test_audio_flashes.py` — **18 passed**, including 2 real FFmpeg renders.

Render fixture: static mid-grey 320×240 source, quiet noise bed (≈ −42 dB) with planted 150 Hz drum bursts at **2.0 s, 4.5 s, 7.0 s** in a 12 s clip; levels derived from the decoded audio via the production `energy_levels_db`; EDL built by `build_edl` with the grid passed as `flash_grid`; rendered by `render_edl_clip_ffmpeg`.

- **(a) Flash lands and is measurable:** exactly 3 points stamped, each within 0.15 s of its planted hit. Frame luma at each flash centre: **165.0 vs baseline 128.0 → delta +37.0 at all three hits** (brightness 0.08 alone predicts +20.4; gamma 1.15 adds the rest). A clear punch; peak 165/255 is nowhere near white-out.
- **(b) No flash at non-transient times:** control frames at 1.25 s, 3.25 s, 5.75 s, 10.5 s all within ±4 luma of baseline (asserted ≤ 4, observed flat).
- **(c) Caps hold on a dense fixture:** hits planted every 0.3 s for 20 s → accepted points obey min gap ≥ 0.8 s, count ≤ 6 per clip, ≤ 3 in any 3 s window; a separate test proves the *strongest* hits win the per-clip cap.
- **(d) Plain:** same EDL after `plainify_edl` — no stamp, the built graph contains no `eq=brightness`, and the Plain render's luma at the flash times is flat within ±4 of baseline.
- Unit coverage also includes: config validation/clamps and the 6 stock opt-ins (the other 8 profiles return `None`); flat and quiet-blip curves yield nothing (absolute −30 dBFS floor); the drop admitted on its own ≥ 2 dB evidence and rejected with none; a hit peaking in the final half-pulse is refused; grid-source precedence (beat grid over flash grid); rendered-time mapping through ramps and fades (hand-derived cases); fragment text shape.

### Neighbour suites — all green, summaries read from log files
| Suite(s) | Result |
|---|---|
| test_signature_transitions, test_template_mode, test_auto_director_edl, test_edl_transitions_sfx, test_viral_plan_pipeline | **148 passed** |
| test_edl_render, test_edl_pipeline_wiring | **28 passed** |
| test_beat_grid / test_beat_edl / test_beat_pipeline / test_beat_velocity_curve / test_beat_render | 12 / 5 / 1 / (14 with new-niches) / 5 passed |
| test_auto_director_new_niches | passed (in the 14 above) |
| frontend changelog.test.ts (run immediately after the CHANGELOG edit) | **4 passed** |

**No pinned test was changed.** Unlike the signature lane, no existing expectation needed re-deriving: non-opted-in profiles produce identical EDLs, and beat-profile suites pass grids whose synthetic levels produce no stamps. The only test-expectation correction in this lane was inside the new suite itself (a projection-window expectation fixed to the slicing contract `prepare_clip_grid` already uses).

*Sandbox note:* pytest processes here intermittently hang at interpreter exit after printing their summary (pre-existing, seen on other lanes). All results above were taken from the summary lines in the on-disk log files, not from process exit codes; one early batched beat run produced conflicting delivery notices and was re-run file-by-file to get trustworthy summaries.

---

## Known boundaries (declared, not hidden)

1. **Editor re-renders get no flashes** — `_build_rerender_edl` calls `build_edl` with no grids, so re-styled/editor renders drop flashes. This is identical to the beat grid's behaviour today (beat consumption is likewise absent on re-render); fixing both belongs to a separate lane.
2. **meme_comedy flashes read the source audio** (no music bed in that path) — its hits are the speaker's/content's own transients, which is the intent for that grammar.
3. **Crossfade-centre convention:** rendered times use the content-time walk; the signature stamp uses the join-midpoint convention. They differ by at most half a fade duration; the content-time mapping is the more correct one for an audio-locked effect.
4. **Intensity is profile-fixed at 0.5** for all six opt-ins in this lane — the knob exists and is honoured; per-style tuning from real renders is a follow-up decision, not a code gap.
5. Nothing here is live-proven on Luffy's machine — sandbox renders only, same evidentiary status as every lane until his render check.

## Five states

- **Implemented:** yes — planning module, EDL stamp, render fragments, pipeline seam, 6 profile opt-ins, Plain stripping.
- **Individually tested:** yes — 18 new tests (detection, caps, gating, projection, EDL, fragments) + 2 real renders.
- **Integrated:** yes — wired into the production EDL seam in `video_service` behind the same best-effort contract as the beat grid; neighbour suites (EDL, render, beat, signature, template-mode, viral-plan, wiring) all green on the branch tip.
- **Cross-feature verified:** partially — Plain stripping proven end-to-end (stamp, graph, rendered luma); interaction with beat consumption, signature joins, ramps and fades proven at EDL level. Not yet combined with Brand Kit bumpers / separation in one render (no reason to expect interaction — flashes sit mid-graph, before overlays — but unproven).
- **User-machine validation:** pending — needs one fresh Auto-template gaming/meme clip on Luffy's machine after a future package ships this branch.
