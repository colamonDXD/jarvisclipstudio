# Wave 4 — Viral Scout Pack, items 1+2: Stage 1 report

**Branch:** `feature-viral-scout-1` off next-milestone @ `49a134e` (HEAD verified first), worktree `~/workspace/jcs-viral1`. Not merged, not packaged, not uploaded.

## Step 0 — Premise check (done first, outcome drove the scope)

**What already exists:**

- **Transition vocabulary + rendering.** `media/edl_render.py` carries the full 58-name FFmpeg xfade catalog (`XFADE_TRANSITIONS`, each mapping to itself in `TRANSITION_MAP`) plus the legacy aliases `wipe → wipeleft`, `slide → slideleft`, `cross_dissolve → fade`, and `cut`/`hard_cut`/`none → None` (hard cut). The same catalog backs the M2 clip editor, where a user can set any catalog name per join. Unknown names degrade to a hard cut in the render graph.
- **Per-cut transition selection in the AUTO path is effectively absent.** `build_edl` (`auto_director/edl.py`) gives *every* join the profile's `transitions.default` + `duration_sec`. All 14 profiles also declare `on_topic_change` / `on_punchline`, but nothing in `build_edl` consumes them per cut — `reference_style.py` writes them when building a style, and `plan_service.py` only quotes them in an LLM prompt. The one real per-join override is the LLM viral plan (`clip_plan.apply_plan_transitions`), applied after `build_edl` for LLM-sourced plans only.
- **Auto SFX placement exists, but transition SFX is one generic sound.** `_create_sfx_events` (edl.py) emits renderer-ready events gated by the profile flag `audio.sfx_from_user_library`. Names resolve by file stem under `assets/sfx` at render; the canonical resolvable set is `SFX_EVENT_NAMES = (whoosh, pop, riser, ding, impact, sting)` and a missing file is skipped silently — emitting a canonical name is safe by design. Transition handling today: **every** crossfade join (join-plan fade > 0) gets the same `whoosh` at −6 dB, at most one per 4 s. There is no per-transition-type pairing. Peaks (impact/pop/riser), info-card dings and the true-crime sting are separate signals and stay untouched.
- **The transition whoosh is dormant for stock profiles.** The only profiles whose default transition is decorative (travel_vlog, japan_travel_cinematic: `wipe` 0.4 s) have `sfx_from_user_library: false`; every SFX-enabled profile defaults to `cut`. So with stock profiles no transition SFX ever fires today — the signature rule below is what first places a decorative transition (and therefore its mapped SFX) into an SFX-enabled clip's opening.
- **Bundled library reality.** `assets/sfx` is empty in the repo tree (the asset library lives on the user's machine, mounted read-only; his copy holds 21 files). The in-repo manifest of names guaranteed resolvable-by-design is `SFX_EVENT_NAMES`; the transition→SFX map therefore only ever emits names from that set (`whoosh`, `pop`). Render proofs plant fixture files under a temp assets dir, following the established harness in `tests/unit/test_edl_transitions_sfx.py`.
- **Plain mode.** `plainify_edl` already forces every segment to `transition_in = "none"`, duration 0, and empties `sfx_events` — signature dressing is stripped by the existing contract; this lane only adds the new provenance stamp to plainify's pop list so a plain EDL never claims a signature it does not contain.

**What was missing (the build scope after the premise check):**

1. **The signature-transition rule — entirely absent.** No per-profile signature, no 3–5 s window placement, no suppression of competing decorative joins in the opening. → Built.
2. **The transition→SFX map — absent.** One generic whoosh for all crossfades. → Built as a data table over the full catalog; unmapped names stay silent (which also aligns SFX with the renderer for unknown names: `_join_plan` counts them as fades, the renderer cuts them, and today they whoosh over a hard cut — after this change they are silent).

**Scope deliberately NOT taken:** `on_topic_change`/`on_punchline` per-cut consumption (a separate pre-existing gap, not part of items 1+2); transition SFX for viral-plan-changed joins (the plan applies after SFX emission — pre-existing ordering, unchanged); Reference Style `build_profile` stamping a signature (styles without one simply get no signature; the niche profiles all carry one).

## Design decisions

- **Opt-in by explicit profile field.** A profile declares `transitions.signature = {"name": ..., "duration_sec": ...}`; all 14 stock profiles gained one (additive-only profile change). `build_edl` applies the rule only when a valid signature is declared — hand-built profiles without the field produce byte-identical EDLs to before, which keeps every pinned neighbour suite green unmodified.
- **Placement.** First join whose boundary falls inside [3.0, 5.0] s (clip-relative, after beat-grid consumption may have moved boundaries). Fallback: the join nearest the window when it is within 2.0 s of it. No join close enough (or a single-segment clip) → no signature; a signature is never forced mid-shot.
- **Suppression.** Decorative joins (positive duration, non-cut name) strictly before the signature join are downgraded to `hard_cut` / 0 so the signature is the first — and in the opening, the only — decorative transition. Joins after the signature keep the profile's normal grammar.
- **Provenance.** The EDL gains `edl["signature_transition"] = {name, duration_sec, segment_index, at_sec, rendered_at_sec, suppressed_segments}`. `plainify_edl` pops it along with the other template blocks.
- **SFX map.** `TRANSITION_SFX_MAP` in the new module covers all 58 catalog names plus the 3 aliases: the movement family (wipes, slides, smooths, covers, reveals, winds, slices, diagonals, open/close, radial, distance, squeezes, crops, zoomin) → `whoosh` −6 dB (exactly today's crossfade behaviour); the fade family (fade, cross_dissolve, dissolve, fadeblack, fadewhite, fadegrays, fadefast, fadeslow, hblur) → `whoosh` −10 dB (a softer swell); `pixelize` → `pop` −3 dB. Every value is a member of `SFX_EVENT_NAMES`. The 4 s transition-SFX spacing and the 0.75 s no-stack rule are unchanged.
- **Per-niche signatures.** gaming `pixelize` 0.35 · meme_comedy `zoomin` 0.30 · sports `slide` 0.30 · football_edit `wipe` 0.35 · anime_funk_edit `radial` 0.35 · music_artist `cross_dissolve` 0.40 · travel_vlog `wipe` 0.40 · japan_travel_cinematic `wipe` 0.40 · podcast_news `fade` 0.30 · hindi_podcast `fade` 0.30 · education_tutorial `fade` 0.30 · finance_business `slide` 0.30 · true_crime `fadeblack` 0.50 · vlog_lifestyle `smoothleft` 0.30. All names resolve in `TRANSITION_MAP` to a real xfade.

## What was built

- **New module** `backend/src/auto_director/signature_transitions.py` (pure, no I/O): `SIGNATURE_WINDOW_SEC = (3.0, 5.0)`, `SIGNATURE_FALLBACK_SEC = 2.0`, `KNOWN_TRANSITION_NAMES` (the 58-name catalog + 3 aliases), `TRANSITION_SFX_MAP` (61 keys), `signature_for_profile()` (validates the declared name is a real crossfade; duration clamped to [0.15, 0.8] s), `choose_signature_join()` (first in-window join → nearest within fallback → None), `apply_signature_transition()` (suppression + stamp, in place).
- **`edl.py`**: imports the two functions; `_join_plan` entries gained `transition_in` (additive — `hook_variants` consumes the plan and only reads the old keys); the `_create_sfx_events` crossfade loop is now map-driven (pairing lookup per join, unmapped → silent; the 4 s spacing and 0.75 s no-stack rules are unchanged); `build_edl` applies the signature after beat-grid consumption and before SFX emission, and stamps `edl["signature_transition"]` with the join's `rendered_at_sec` from the join plan.
- **Profiles**: all 14 stock profiles declare `transitions.signature` (additive key; values in the design table above).
- **`template_mode.py`**: `plainify_edl` pops `signature_transition` with the other template blocks (its forced hard cuts + emptied `sfx_events` already removed the dressing itself).

Commits on `feature-viral-scout-1` (tip `8d4a9f9`, 7 commits): `d80af27` module · `021f5f7` EDL wiring · `b25a13e` profile declarations · `1702a46` plainify stamp · `242250b` new tests · `1038dc1` stock-suite pins · `8d4a9f9` changelog (entry under 2026-11-11; `changelog.test.ts` run immediately after: **4 passed**).

## Measured proof (real renders through the production EDL path)

Fixture: gaming-shaped profile declaring the pixelize signature (0.35 s), SFX on, no peaks; 12 s clip → segments at 0/3/6/9 s. EDL: signature on segment 1 (`at_sec` 3.0, `rendered_at_sec` 2.825), `sfx_events == [pop @ 2.825, −3 dB]` exactly.

- **(a) Signature present at the measured timestamp:** the production filter graph contains `xfade=transition=pixelize:duration=0.350000`; the rendered file is 11.65 s (12 − 0.35 fade) long; a frame at the transition's rendered centre (2.82 s) is dominated (>50 %) by blended purple pixels that are *neither* source colour (the pixelize mosaic), while frames at 1.0 s / 6.0 s are >80 % pure red / pure blue. A hard cut cannot produce the blended frame.
- **(b) Mapped SFX audible in the window:** over a silent source, mean volume in [2.6, 3.2] s is > −45 dB (the pop), and < −60 dB in [6, 7] s (nothing else plays).
- **(c) Plain render of the same EDL contains neither:** after `plainify_edl`, the render is the full 12.0 s, the frame at 2.85 s is >80 % pure red with <5 % blended pixels (a clean cut at 3.0 s), and the [2.6, 3.2] s window measures < −60 dB (no pop).

Unit-level: window/fallback/tie/no-join choice cases; suppression proven on a wipe-default profile (join at 2.0 s downgraded to `hard_cut`, signature at 4.0 s, first SFX event is the signature's whoosh at rendered 3.8 s, later whooshes keep the 4 s spacing); fade signature → soft whoosh −10 dB; unmapped `custom` joins now silent; determinism; map integrity pinned against the renderer's own catalog (`TRANSITION_SFX_MAP` keys == `XFADE_TRANSITIONS ∪ {wipe, slide, cross_dissolve}`; every emitted name ∈ `SFX_EVENT_NAMES`).

## Tests

- **New suite** `tests/unit/test_signature_transitions.py`: **18 passed** (16 unit + 2 real-render proofs).
- **Neighbours, serial runs**: batch 1 (transitions/EDL/viral-plan/template-mode) 180 passed + the 2 deviations below, both fixed and re-verified; batch 2 (13 files incl. render-audio, SFX, beat, pipeline wiring, scene rules) 293 passed + the 1 deviation below, fixed and re-verified (file: 7 passed); batch 3 (reference style, hook variants, caption pacing, music bed, b-roll, learning, niche override, classify guardrails) **214 passed**; batch 4 (EDL render, A/V sync, clip re-render/edit render, force-CPU, SFX upload render) **46 passed**. Final combined confirmation of the seven core files: **207 passed / 0 failed**.

## Deviations (named, with reasons)

- **Three existing tests were updated — the lane's one significant deviation from the "neighbours unmodified" instruction.** The instruction presumed the feature could land without changing pinned behaviour; it cannot, because the pins *are* the old behaviour the feature deliberately replaces: (1) `test_auto_director_edl.py::test_build_edl_podcast_hard_cut_and_punch_in` pinned *every* podcast join to `hard_cut` — now every join but the signature's (fade, in-window, stamp-checked); (2) `test_auto_director_new_niches.py::test_true_crime_...` pinned `segments[1]` to `hard_cut` — now `fadeblack` there (it is the signature join), with all other joins still asserted `hard_cut`; (3) `test_viral_plan_pipeline.py::TestSeam::test_planned_sfx_merge_into_edl` pinned meme_comedy's native events to `[]` — the native EDL now legitimately carries the signature's whoosh (3.183 s), and the merged expectations were re-derived by hand from the join math (impact 5.0→4.7, pop 9.0→8.3, both pulled earlier by the signature's own 0.3 s fade) before being pinned. No assertion was weakened: each update asserts the new intended behaviour at equal or greater precision (signature stamp name/window/index checked explicitly). Flagged here for the merge decision — the alternative (signatures declared by no stock profile) would have shipped a feature that never fires.
- **Design choice recorded:** the signature is opt-in per profile via the explicit `transitions.signature` field rather than a niche-keyed fallback table inside `build_edl`. This keeps every hand-built profile's EDL byte-identical to before (which is what the remaining ~750 neighbour assertions pin) while the 14 stock profiles all opt in. Reference Style profiles carry no signature unless their builder stamps one (not built — see scope note in Step 0).

## Boundaries

- The establishing-shot guard (travel profiles) strips SFX events inside the protected opening; a signature *visual* inside the guard still renders (profile transitions were never guard-stripped), and both travel profiles have SFX off, so no travel signature SFX exists to strip. For SFX-enabled profiles the guard is 0 and the signature SFX always lands.
- Viral-plan (LLM) transition choices are applied after SFX emission and do not gain mapped SFX — pre-existing ordering, unchanged by this lane.
- Beat-grid profiles: the signature applies after `consume_beat_grid`, so it lands on post-snap boundaries; the stamp's `at_sec` is the post-snap join time.

## Five states

- **IMPLEMENTED:** yes — module, EDL wiring, 14 profile declarations, plainify stamp pop.
- **INDIVIDUALLY TESTED:** yes — 18 new tests incl. map-vs-catalog integrity and both render proofs.
- **INTEGRATED:** yes on the branch — production `build_edl` path (the same builder `video_service`/`clip_service` call), renderer untouched and consuming the output; ~750 neighbour assertions green across 30+ suites.
- **CROSS-FEATURE VERIFIED:** partial — Plain stripping proven at EDL *and* render level; viral-plan interplay proven (signature SFX survives the plan merge and correctly re-times plan events); beat-grid ordering handled in code (signature applied post-consumption) but not render-proven with a live beat grid; no service-level task run exists in this sandbox (no Docker/Postgres).
- **USER-MACHINE VALIDATION PENDING:** yes — an Auto-template gaming/meme clip on his install should show the signature transition at ~3–5 s with its mapped sound, and his SFX library must actually contain `whoosh`/`pop` files for the sounds to be heard (missing files skip silently by design).

