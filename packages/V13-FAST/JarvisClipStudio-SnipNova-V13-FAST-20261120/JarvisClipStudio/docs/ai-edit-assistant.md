# AI edit assistant (clip editor)

Type what you want in plain words — *"make the intro shorter, drop the
last segment, captions at the top"* — and the clip editor does it. This
is the Final Directive's "AI Edit Prompt box" (Part P item 10), built
entirely on the clip editor's existing seams: the same operation
applier, the same worker job, the same re-render path as a manual edit.
It is distinct from the clip *studio's* document AI edit
(`docs/clip-studio.md`), which edits a different document model.

## How it works

1. In a finished clip's **Clip editor** panel, type an instruction in
   the **AI edit** box and press **Apply AI edit**.
2. `POST /api/tasks/{task_id}/clips/{clip_id}/ai-edit` with
   `{"instruction": "..."}` (max 600 characters) asks the app's
   configured LLM — the same provider plumbing the pipeline uses
   (`get_config().llm` through `src/ai.py`'s model factory; Groq
   `openai/gpt-oss-120b` via LiteLLM in production, no new keys) — to
   translate the instruction into the editor's operations, given the
   clip's current edit plan (segments, durations, caption state).
3. The reply is **schema-validated and dry-run** before anything is
   queued (see the contract below). Any failure — unreachable LLM
   (502), unparseable reply, unknown operation, an edit that cannot fit
   this clip (422) — leaves the clip completely untouched.
4. A valid edit queues the same `edit_clip_task` job as a manual edit
   and returns `202` with the `job_id`, the validated operations, and a
   plain-language `summary`. The panel polls the existing edit-status
   route and swaps in the re-rendered clip, then reloads the plan so
   the segment rows show the clip as it now is.

## The op contract

The model may only emit a JSON object with these keys (anything else —
extra fields, prose, a different op — is rejected wholesale):

- `operations`: the editor's four segment operations, exactly as
  `apply_segment_operations` defines them —
  `{"type": "trim", "index", "start"?, "end"?}` (shrink-only),
  `{"type": "delete", "index"}`,
  `{"type": "reorder", "order"}` (a full permutation),
  `{"type": "split", "index", "at"}`.
- `captions`: the Caption Studio override fields, validated by
  `normalize_caption_overrides` plus the same template/font registry
  checks the manual edit route applies.
- `music_override`: a music-library filename, validated against the
  live library like the manual edit route.

Deliberately **excluded**, because the editor has no operation for them
(documented here so nobody "fixes" the schema by inventing one):

- **Per-segment transition changes.** Join transitions belong to
  timeline positions in an edit plan (see `docs/editor.md`); there is
  no transition operation for the assistant to emit. A whole-clip
  transition *restyle* exists on the separate re-render route, outside
  the editor seam this lane builds on.
- **Zoom emphasis.** Zooms are computed from the niche profile when
  the EDL is built; the editor cannot add or strengthen one.

Also not possible, by the same token: changing the spoken words,
re-timing captions per word, adding footage, or free-form timeline
invention of any kind. The assistant rearranges and restyles what the
editor already knows how to rearrange and restyle — nothing more.

## The summary is generated, not written

The `summary` in the 202 response ("Removed segment 2 (5.0s). Captions:
template set to hormozi. Clip is now 9.0s (was 14.0s).") is built by
`describe_ai_edit` from the operations that passed the dry-run, replayed
against the clip's actual segment state. The model never writes it, so
what the UI says happened is exactly what was queued.

## Honest limits

- **One instruction, one edit.** Compound or vague instructions are
  translated as literally as the op set allows; if the result cannot
  fit the clip, the whole edit is rejected rather than partially
  applied. There is no clarification dialogue.
- **Quality depends on the configured model.** The schema and dry-run
  guarantee the edit is *valid and applicable*; they cannot guarantee
  it is what you meant. The summary line is the check: read it before
  assuming, and the manual editor remains available for anything the
  assistant gets wrong (an AI edit is a normal edit plan afterwards —
  it can be edited further, like any other).
- **Indexes are positional.** The model sees the same segment list the
  panel shows; if you have unsaved manual changes in the panel, the AI
  edit applies to the last *rendered* plan, and the panel reloads its
  rows afterwards.
- **No niche restyle.** The assistant keeps the clip's current niche;
  style changes stay with the re-render control and the Caption Studio.
