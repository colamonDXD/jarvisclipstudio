# Viral Intelligence Engine (per-clip viral plans)

The niche profile used to be the whole edit: the pipeline picked ONE
profile per video and applied it uniformly to every clip. The Viral
Intelligence Engine adds a per-clip decision layer on top. For each
selected segment, one planner call (through the app's existing LLM
configuration — the same model plumbing the niche classifier uses)
decides how THIS clip should be edited, from its own transcript, word
timings, audio features and selection scores:

- what its hook is, where the hook moment actually starts, and why;
- how the opening is treated (punch zoom, first-caption emphasis,
  an opening sound accent);
- which internal joins earn a different transition than the profile's;
- whether this clip suits a different caption style than the profile's;
- where sound-effect accents belong;
- whether the profile's music should play under this clip.

Every decision carries a plain-language reason that names the content
moment justifying it, and the plan is stored on the clip record
(`clip_plan`, migration `20261108_0005_clip_plan.sql`). The clips API
serves a derived `hook_report` — `{type, timestamp, why,
opening_strategy, confidence}` — and the Library clip card shows the
hook type and reason as a small line.

Code: `backend/src/auto_director/clip_plan.py` (schema, validation,
consumers), `backend/src/auto_director/plan_service.py` (generation,
audio features). The pipeline seam is
`VideoService._apply_viral_plan`, called from `create_single_clip`
immediately after the pipeline EDL is built and before rendering.

## Explainability and restraint

Decisions must come from the content, not from randomly sampling the
58-name transition library. Validation enforces this in
`validate_plan_dict`:

- **No reason, no effect.** An effect decision — a transition that
  differs from the profile default, a punch zoom, a caption style that
  differs from the profile default, any SFX event, switching the
  profile's music off — whose reason is missing, empty, or boilerplate
  ("for engagement", "to make it viral", …) is dropped back to the
  profile default, and the drop is logged.
- **Restraint budgets.** At most 2 non-default transitions per clip
  (`MAX_NON_DEFAULT_TRANSITIONS`) and at most 2 zoom accents
  (`MAX_ZOOM_ACCENTS`; in v1 the plan carries exactly one zoom
  decision — the opening punch — so the zoom budget is structural).
  Excess transition choices are dropped weakest-last: the earliest
  survive. SFX accents keep the density discipline of the EDL emitter
  (profile gate, 0.35s edge guard, 0.8s minimum gap, at most 4).
- **Vocabulary gates.** Transition names must resolve in the renderer's
  transition map and caption styles in the caption template registry;
  anything else drops to the profile default, logged. SFX names must be
  in the canonical vocabulary (`whoosh, pop, riser, ding, impact,
  sting`).

The planner prompt instructs the same restraint: default to NO effect;
an effect is chosen only when a specific moment justifies it, and the
reason must name that moment.

## What the render actually consumes (v1)

| Decision | Consumed | How |
| --- | --- | --- |
| SFX events | Yes | Merged into the EDL's `sfx_events` (EDL-native events win within ±0.3s). Plan offsets are clip-timeline; before merging they are mapped through the final join plan onto the rendered timeline, so an accent lands on the content moment the planner named even after a crossfade. |
| Transition choices | Yes | Each choice lands on the EDL join nearest its offset: that segment's `transition_in`/`transition_duration` are set (a crossfade on a cut-length profile gets a visible 0.4s). The renderer's existing xfade/concat join assembly does the rest. A clip with fewer than two EDL segments has no internal joins — the choice stays on the plan record, unapplied. |
| Opening punch zoom | Yes | A `punch_in` zoom keyframe at the hook offset, in the exact shape the profile machinery emits. The scale is anchored on the profile's own punch scale: subtle stays ≤1.25, strong is ≥1.30 — the plan intensifies the niche's grammar instead of inventing one. An existing punch keyframe within 0.3s is re-scaled, never duplicated. |
| Caption style | Recorded + re-render seam | The choice is written to the EDL's `captions.preset_id` and stored on the plan. Rendered caption styling in the main pipeline comes from the task-level caption template burn, not the EDL, so v1 consumes the style where a per-clip template seam exists: on re-render, when the user has not explicitly picked a template, the stored plan's caption style becomes the re-render's effective template. |
| Music decision | Record only | Stored and reported, not applied to renders in v1: music resolution already has a precedence chain (user override → profile mood → library) and flipping it per clip risks silencing music the user explicitly chose. Phase 2. |
| Hook / phases / opening emphasis | Record only | The hook call drives the Hook Report; reordering the clip to open on the hook moment (cold-open) is phase 2. |

## Fail-safe contract

Plan generation must never fail or stall a render:

- `generate_clip_plan` never raises. Any error — LLM down, timeout
  (`VIRAL_PLAN_TIMEOUT_SEC`, default 30s), invalid output — returns the
  deterministic minimal plan (`source: "fallback"`), which records
  exactly what the pipeline already does without a plan.
- The seam itself is wrapped the same way: any failure logs a warning
  and returns `None` with the EDL untouched.
- Only planner-produced plans (`source == "llm"`) are consumed into the
  EDL. The fallback plan is record-only — consuming it would change
  renders exactly when the planner is down.
- Kill switch: `VIRAL_PLAN_ENABLED=false` (or `0`/`no`/`off`) skips
  planning entirely; EDLs are then byte-identical to the pre-engine
  behaviour (pinned by a test).

## Honest limits (v1)

- The planner sees the transcript, word timings, selection scores and
  cheap audio features (energy peaks / pauses from 50ms window levels).
  It does not see the picture: hook types like `surprising_visual` are
  inferred from speech, not vision. Visual features are phase 2.
- Hook timestamps may differ from the segment start, but the render is
  not reordered in v1 — the hook is reported, not re-opened on.
- Audio features are computed over the segment's source range; when
  pause-cleanup trims the clip head, offsets are a close approximation.
- A plan is advice with a confidence, not a view prediction. Nothing in
  the engine claims a score forecasts performance.

## Phase 2 (deliberately out of v1)

- Cold-open segment reordering (open on the hook moment).
- Visual / visual-interest features for the planner.
- Hook-variant lightweight previews for top clips.
- Applying the music decision to renders.
- Selection-ranking changes (the plan never feeds back into which
  segments are picked in v1).
