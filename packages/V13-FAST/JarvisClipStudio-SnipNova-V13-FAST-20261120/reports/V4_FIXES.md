# V4 Changes — SnipNova Consolidated Release v4 (2026-11-15)

**The v3 build plus everything merged since, in one ZIP.** This tree
is branch `next-milestone` @ `d3eb750be83cae91f13e6fb09a24018ff7668539`
— the v3 tree (`49a134e`) plus **123 commits**. Your installed build
is v3 plus three hand-applied patches (the moment-picker apostrophe
fix, the task-creation INSERT one-file hotfix, and the 8-file titles
patch). **All three patches are in this tree as regular commits** —
after installing v4, the hand patches are simply part of the build
and no patching is ever needed again:

| Your hand patch | In-tree equivalent |
| --- | --- |
| `don&apos;t` in `moment-picker.tsx` (v3 install build failure) | commit `b555eca` — same one-word fix, plus a full-frontend eslint gate item so the class cannot ship again |
| `task_repository.py` INSERT hotfix (DuplicateColumnError broke all task creation) | commit `47eda4a`, merged @ `6362cfe` — each column exactly once, plus a regression test that executes the real statement |
| Titles patch (8 files: third-person titles + creator name) | commits `3b34313`/`bdaabd8` (merged @ `50045d6`) + creator-name lane `d689a67`…`af9eba4` (merged @ `c2bce92`) — same files, same changes, plus the request-shape golden re-pin (`b6c7c22`, merged @ `fca2f7e`) proving the prompt diff is titles-only |

## What else is new since v3

1. **Classification asks the strongest model first** (`25860db`,
   merged @ `eae0595`). The niche call used to ask the local model
   first and accept its answer; the cloud routes now answer first
   and the local model is the backstop. Report:
   `CLASSIFY_ORDER_REPORT.md`.
2. **Free-API gateway fallbacks** (`c1f8b1d`, merged @ `ffd8063`;
   repoint `5136ce1`, merged @ `b94ff52`). DeepSeek and OpenRouter
   `:free` routes sit at the END of every LiteLLM fallback chain.
   Both are **inert until you add your own keys** — no key, no
   traffic. The OpenRouter route points at
   `nvidia/nemotron-3-ultra-550b-a55b:free` (the originally wired
   DeepSeek `:free` ID no longer exists; the fix is in-tree).
   Reports: `FREEAPI_WIRING_REPORT.md`,
   `OPENROUTER_ROUTE_FIX_REPORT.md`.
3. **Viral scout pack** (merges `6767093`, `ab44064`, `1bee376`):
   one signature transition per niche profile at the first natural
   join, a transition→SFX map over all 58 transitions, audio-reactive
   flash pulses on transients/drops for opted-in profiles (Auto
   template only), and render-level proof that the two-pane
   split-screen reaches real pixels. Reports:
   `VIRAL_SCOUT_STAGE1.md`, `FLASHES_REPORT.md`, `SPLIT_REPORT.md`.
4. **Separation Stage 3** (`532d401`): deleting a task now deletes
   its separation stems too (a real leak — ~640 MB per hour of
   source stayed behind forever), plus a benchmark-pack separation
   scenario and a music-only hallucination harness. Report:
   `SEPARATION_STAGE3.md`.
5. **Channel monitoring Stage 2** (`974feaa`; migration
   `20261111_0002_channel_monitoring_stage2.sql` — the one new
   migration in this build, schema 46 → 47): premiere/upcoming
   `waiting` state with a 48 h bound, a yt-dlp fallback detector for
   feed outages, manual re-drive of parked/skipped videos, and
   per-channel campaign/Brand-Kit defaults. Report:
   `CHANNEL_MONITORING_STAGE2.md`.
6. **Reference-look Stage 2** (`895edac`): a margin-gated caption
   font suggestion (render-and-diff against the 25 bundled fonts;
   the gate is deliberately strict — it stays silent unless sure)
   and caption animation-feel measurement. Report:
   `REFERENCE_LOOK_STAGE2.md`.
7. **The SQL safety net** (merges `39c7437`, `0b75ba5`, `6a3b287`,
   `1130cfb`, `06ff0d4`): every raw INSERT (38), UPDATE (68) and
   DELETE (17) statement in the backend now has a test that executes
   the real statement text (SQLite tier + a real-Postgres tier for
   the five Postgres-only statements), and an inventory guard fails
   the gate if a future statement lands untested. This is the net
   built after the duplicate-column INSERT reached your machine.
   Reports: `SQL_EXECUTION_COVERAGE_REPORT.md`,
   `UPDATE_EXECUTION_COVERAGE_REPORT.md`,
   `INVENTORY_GUARD_REPORT.md`, `POSTGRES_TIER_REPORT.md`,
   `DELETE_INVENTORY_REPORT.md`.
8. **Selection gold set** (`6204c3b`): 20 labelled windows on the
   Japan validation source (5 editor-validated + 15 trap-derived)
   with an offline scoring harness — the benchmark future picker
   changes are graded against. Report: `GOLDSET_REPORT.md`.
9. **Brain upgrade** (`537d55e`): the moment picker's final passes
   now run at **high reasoning effort** on the Groq routes (it was
   silently at the default medium before; a rejection of the
   parameter degrades to a normal call — it can never fail a task),
   the gated /100 quality rubric with hook-archetype calibration is
   in the selection prompt, the source title/channel/description and
   scene-cut boundaries reach the prompt, the Japan gold windows are
   in as calibration examples, and the picker over-asks by +3.
   Report: `BRAIN_UPGRADE_REPORT.md`; full gate on this exact merge:
   `NEXT_GATE_537D55E.md` (backend 4,134/0, frontend 846/0).
10. **Caption/title face guard** (`54c8b8a`): burned-in captions and
    the pinned hook title can no longer sit on a face — placement is
    checked against the per-shot face boxes in output coordinates
    (a +12 px margin), in single and split shots alike. Measured on
    your Bert clip's frames: caption∩face ≈ 49,500 px² → 0,
    title∩face up to ≈ 54,200 px² → 0. Report:
    `CAPTION_FACE_OVERLAP_REPORT.md`; gate: `NEXT_GATE_54C8B8A.md`.
11. **SigLIP B-roll vision veto** (`77dbbde`): in Auto template
    mode, a stock hit whose actual picture does not match the spoken
    subject can be vetoed by a vision model (the railway-bridge
    class, one level deeper than the word-sense fix). **Flag-gated
    OFF by default** (`BROLL_VISION_VETO_ENABLED`, passed through
    the base compose file to backend + worker). Calibration: 6/6
    planted mismatches vetoed, 0/5 false vetoes, with an honestly
    thin score gap — it stays off until it proves itself on your
    footage. Report: `SIGLIP_VETO_REPORT.md`.
12. **Split-tile source-border trim** (`be00fd2`): the thin slivers
    of the source's own border furniture at split-tile edges (seen
    on your Bert clip) are detected and trimmed. **On by default** —
    `BORDER_TRIM_ENABLED` is only its kill switch. Report:
    `BORDER_TRIM_REPORT.md`.
13. **Active-speaker targeting** (`e5705bc`): reframe plans can bias
    the crop toward the face that is actually speaking (mouth
    aperture × audio envelope correlation). **Flag-gated OFF by
    default** (`ACTIVE_SPEAKER_TARGETING_ENABLED`); flag off =
    byte-identical plans. Report: `ACTIVE_SPEAKER_REPORT.md`.
14. **Both reframe switches now work from `.env`** (`5a0bb0d`,
    merged @ `4537b84`; pinned by the compose test suite
    `eab9a99`). The base compose file passes
    `ACTIVE_SPEAKER_TARGETING_ENABLED` and `BORDER_TRIM_ENABLED`
    into backend + worker, closing known gap **G-V4-1** from the
    first v4 staging: speaking-aware crops now enable with the
    `.env` line alone, and `BORDER_TRIM_ENABLED=false` is a
    working kill switch for the trim.
15. **Default AI is Groq-first, Gemini nowhere** (`5d744f1`,
    merged @ `f363b6f`; 13-test suite
    `test_config_default_llm.py`, bite-proven against the old
    code). `Config._infer_default_llm()` used to return
    `google-gla:gemini-3-flash-preview` as its final fallback and
    never consulted `GROQ_API_KEY` — while the main pipeline
    (selection, classification, plan, AI edit) resolves its model
    through this path with no `LLM` set. It now returns
    `groq:openai/gpt-oss-120b` when your Groq key is present and
    falls back to the local `ollama:qwen3:8b`; a present Google
    key can no longer resurrect Gemini. Report:
    `GEMINI_FALLBACK_FIX_REPORT.md`.
16. **Setup docs show Groq, not Gemini** (`2678ca1`, merged @
    `d3eb750`; 18 files). Every setup example still showed the
    Gemini model even though it answers 411; `start.sh` never
    mentioned the Groq key. Examples, `.env.example` files, the
    self-hosting guide and all six locale strings now lead with
    Groq; a full-tree sweep finds zero Gemini defaults or
    examples left user-facing. Report:
    `GEMINI_DOCS_CLEANUP_REPORT.md`.

## Deliberately NOT in this build

- **Denoise** (DeepFilterNet/FFmpeg-native pre-stage): the lane's
  report exists (`research_notes` only) but the feature is **not
  merged into this tree** and is not in this ZIP.
- The **Postgres execution tier** runs only when a test Postgres is
  provided; it is a test-tier, not a runtime feature.
- No new niche profiles; additive-only rule unchanged.
