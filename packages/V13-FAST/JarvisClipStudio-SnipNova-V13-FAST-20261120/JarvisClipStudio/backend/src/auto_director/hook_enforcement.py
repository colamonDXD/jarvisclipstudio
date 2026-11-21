"""Quality Director: hook enforcement + cold-open reordering.

The Viral Intelligence Engine's plan names a clip's hook moment, but
nothing used to check WHERE that moment lands: on the user's real
2026-11-08 podcast run the hook sat mid-clip and the clips "wouldn't
survive 2 seconds" of scrolling. This module is the deterministic
enforcement layer over the finished plan — no new AI calls:

1. If the planned hook already lands inside the first
   ``HOOK_WINDOW_SECONDS`` of the clip, nothing changes
   (``already_leading``).
2. Otherwise the hook's whole SENTENCE (the segment) is moved to the
   front of the clip — a cold open — by reordering the keep ranges, so
   picture, voice and captions move together and no thought is cut.
3. Context guards run first, and context preservation beats the
   2-second rule whenever they conflict (the user's acceptance gate):
   a segment that opens on a pronoun / continuation / back-reference,
   a punchline, or a payoff whose setup sits earlier in the clip is
   NEVER moved; the clip keeps its original order and is marked
   ``kept_original_context`` with the reason on the record.

Every outcome is recorded on the clip's plan (``hook_application``)
with the hook timestamps before/after, so QC and the user can see what
was decided and why. The whole behaviour is gated by
``HOOK_ENFORCEMENT_ENABLED`` (default on); disabling it restores the
previous pipeline exactly — no reordering, no record.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from typing import Any, Optional

from .clip_plan import ClipPlan, Phase, PlannedSfxEvent

logger = logging.getLogger(__name__)

# The hook moment must land within this many seconds of the clip start.
HOOK_WINDOW_SECONDS = 2.0
# A sentence shorter than this cannot carry an opening on its own.
BLOCK_MIN_SECONDS = 1.0
# After moving the block, at least this much clip must remain after it.
REMAINDER_MIN_SECONDS = 1.0
# Moving a block that spans most of the clip is not a cold open.
BLOCK_MAX_FRACTION = 0.8
# Lead-in before the hook sentence's first word (clamped to the previous
# word's end), so the moved segment does not start mid-breath.
BLOCK_LEAD_IN_SECONDS = 0.1
# Phase intents that constitute a setup a payoff depends on.
SETUP_INTENTS = ("context", "build")


def hook_enforcement_enabled() -> bool:
    """HOOK_ENFORCEMENT_ENABLED=false (or 0/no/off) disables enforcement."""
    return os.environ.get("HOOK_ENFORCEMENT_ENABLED", "").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


@dataclass
class HookDecision:
    """The enforcement outcome for one clip.

    ``outcome`` is the record persisted on the clip's plan (None when
    enforcement is disabled — the pipeline then behaves exactly as
    before). ``keep_ranges``/``plan`` are set only for a cold open: the
    reordered ranges and the plan remapped onto the new timeline.
    """

    outcome: Optional[dict[str, Any]] = None
    keep_ranges: Optional[list] = None
    plan: Optional[ClipPlan] = None


def _word_text(word: dict) -> str:
    return str(word.get("text") or word.get("word") or "")


def _word_time(word: dict, key: str) -> float:
    try:
        return float(word.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def sentence_block(
    words: list[dict], hook_ts: float
) -> Optional[tuple[float, float, int, int]]:
    """The sentence containing ``hook_ts``: (start, end, first_idx, last_idx).

    Sentence boundaries come from the timeline module's own rules
    (terminal punctuation or a clear pause), the same rules the clip
    start snapper uses. The block start carries a small lead-in clamped
    to the previous word's end. Returns None when the hook moment cannot
    be located in the words (untimed/degraded word lists).
    """
    if not words:
        return None
    if all(_word_time(w, "end") <= 0.0 for w in words):
        return None  # degraded untimed words: the hook cannot be located
    from ..media.timeline import _word_starts_sentence, word_ends_sentence
    from ..media.common import SENTENCE_PAUSE_BOUNDARY_SECONDS

    hook_idx: Optional[int] = None
    for index, word in enumerate(words):
        if _word_time(word, "end") > hook_ts:
            hook_idx = index
            break
    if hook_idx is None:
        return None

    first = hook_idx
    while first > 0 and not _word_starts_sentence(words, first):
        first -= 1

    last = hook_idx
    while last < len(words) - 1:
        current = words[last]
        following = words[last + 1]
        if word_ends_sentence(_word_text(current)):
            break
        if _word_time(following, "start") - _word_time(current, "end") >= (
            SENTENCE_PAUSE_BOUNDARY_SECONDS
        ):
            break
        last += 1

    block_end = _word_time(words[last], "end")
    block_start = _word_time(words[first], "start")
    if first > 0:
        block_start = max(
            _word_time(words[first - 1], "end"),
            block_start - BLOCK_LEAD_IN_SECONDS,
        )
    else:
        block_start = max(0.0, block_start)
    if block_end - block_start <= 0:
        return None
    return (round(block_start, 3), round(block_end, 3), first, last)


def _payoff_needs_setup(plan: ClipPlan, hook_ts: float) -> Optional[str]:
    """Reason the hook must not move, when it is a payoff/punchline."""
    if plan.hook.type == "punchline":
        return (
            "the hook is a punchline — it only lands with its setup in "
            "front of it, so the original order is kept"
        )
    for phase in plan.phases:
        if phase.start_offset <= hook_ts < phase.end_offset:
            if phase.intent == "payoff" and any(
                other.intent in SETUP_INTENTS and other.end_offset <= phase.start_offset
                for other in plan.phases
            ):
                return (
                    "the plan marks the hook moment as a payoff whose "
                    "setup plays earlier in the clip; moving it would "
                    "orphan the payoff from its setup"
                )
            return None
    return None


def _map_offset(t: float, block_start: float, block_end: float) -> float:
    """Map a clip-timeline offset through the [B, A, C] block move.

    Blocks: A = [0, block_start), B = [block_start, block_end) (the hook
    sentence, moved to the front), C = [block_end, duration). New order
    is B, A, C — A shifts later by B's length, C keeps its offsets
    exactly (B and A swap places in front of it).
    """
    if block_start <= t < block_end:
        return t - block_start
    if t < block_start:
        return t + (block_end - block_start)
    return t


def remap_plan(plan: ClipPlan, block_start: float, block_end: float) -> ClipPlan:
    """A copy of ``plan`` with its timed decisions on the reordered timeline.

    Transition choices target EDL joins, which the reorder rebuilds —
    like Lane K's cold open, they are dropped rather than re-aimed at
    joins that no longer mean the same thing. Phases that straddled a
    block boundary map to disjoint spans and are dropped; the rest are
    remapped exactly (blocks move wholesale).
    """
    phases: list[Phase] = []
    for phase in plan.phases:
        start = _map_offset(phase.start_offset, block_start, block_end)
        end = _map_offset(phase.end_offset, block_start, block_end)
        if end > start:
            phases.append(Phase(start_offset=start, end_offset=end, intent=phase.intent))
    return replace(
        plan,
        hook=replace(
            plan.hook,
            timestamp=_map_offset(plan.hook.timestamp, block_start, block_end),
        ),
        opening_treatment=replace(
            plan.opening_treatment,
            at_offset=_map_offset(
                plan.opening_treatment.at_offset, block_start, block_end
            ),
        ),
        transition_choices=[],
        phases=phases,
        sfx_events=[
            PlannedSfxEvent(
                at_offset=_map_offset(event.at_offset, block_start, block_end),
                name=event.name,
                reason=event.reason,
            )
            for event in plan.sfx_events
        ],
    )


def _reorder_keep_ranges(
    keep_ranges: list, block_start: float, block_end: float, duration: float
) -> Optional[list]:
    """[block, before, after] keep ranges for the cold open, or None.

    Slicing happens on the rendered (crossfade-compacted) timeline via
    the source-map helpers the editor's trims use, so picture, voice
    and captions — all derived from the keep ranges — move together.
    Aborts (None) when the slices do not add back up to the original
    footage: a reorder must never silently drop content.
    """
    from ..clip_source_map import slice_source_timeline
    from ..media.ffmpeg import output_offsets_for_ranges

    offsets = output_offsets_for_ranges(keep_ranges)
    before, _ = slice_source_timeline(keep_ranges, 0.0, block_start, offsets)
    block, _ = slice_source_timeline(keep_ranges, block_start, block_end, offsets)
    after, _ = slice_source_timeline(keep_ranges, block_end, duration, offsets)
    if not block or not (before or after):
        return None
    reordered = [*block, *before, *after]
    original_total = sum(end - start for start, end in keep_ranges)
    new_total = sum(end - start for start, end in reordered)
    if abs(new_total - original_total) > 0.15:
        logger.warning(
            "Cold-open slicing changed the footage total (%.3fs -> %.3fs); "
            "keeping the original order",
            original_total,
            new_total,
        )
        return None
    return reordered


def evaluate_bold_claim_opener(
    plan: Optional[ClipPlan],
    *,
    words: list[dict],
    keep_ranges: list,
    duration: float,
    profile: Optional[dict],
) -> HookDecision:
    """Trend-Radar D7 candidate: deterministic bold-claim opener (travel).

    Fallback layer, wired in the pipeline ONLY where hook enforcement
    found nothing to do (the planner named no hook): travel profiles
    carrying ``scene_rules.bold_claim_opener`` nominate the clip's
    strongest claim/cost sentence (scene_rules.pick_bold_claim_sentence,
    deterministic markers) and move it to the front under the SAME
    context guards evaluate_hook applies — weak-opener check, block
    shape limits, clean slicing. It never overrides a planner hook,
    never cuts or rewords a sentence, and records its outcome in the
    same shape (status ``bold_claim_opener`` / ``already_claim`` /
    ``no_claim``) so QC sees what happened.

    The radar's breakout numbers are motivation only — this rule claims
    an editorial improvement (a concrete opening over a descriptive
    one), never a virality effect.
    """
    if not hook_enforcement_enabled():
        return HookDecision()
    from .scene_rules import pick_bold_claim_sentence, scene_rules

    rule = scene_rules(profile).get("bold_claim_opener")
    if not rule or not keep_ranges or duration <= 0:
        return HookDecision()
    picked = pick_bold_claim_sentence(
        words,
        min_score=int(rule["min_score"]),
        window_sec=float(rule["window_sec"]),
    )
    status = picked.get("status")
    if status == "already_claim":
        return HookDecision(
            outcome={
                "status": "already_claim",
                "reason": (
                    "the clip already opens on a claim/cost line "
                    f"(marker score {picked['opener']['score']}); "
                    "the travel opener rule changes nothing"
                ),
                "window_seconds": HOOK_WINDOW_SECONDS,
            }
        )
    if status != "candidate":
        return HookDecision(
            outcome={
                "status": "no_claim",
                "reason": (
                    "no claim/cost sentence was found in the opener "
                    "window; the original opening stands"
                ),
                "window_seconds": HOOK_WINDOW_SECONDS,
            }
        )

    candidate = picked["candidate"]
    block_start = float(candidate["start"])
    block_end = float(candidate["end"])
    base = {
        "block_start": round(block_start, 3),
        "block_end": round(block_end, 3),
        "claim_score": candidate["score"],
        "claim_text": candidate["text"][:200],
        "window_seconds": HOOK_WINDOW_SECONDS,
    }

    def kept(reason: str) -> HookDecision:
        return HookDecision(
            outcome={**base, "status": "kept_original_context", "reason": reason}
        )

    block_len = block_end - block_start
    if block_start <= 0.05:
        return kept("the claim sentence is the opening sentence after all")
    if block_len < BLOCK_MIN_SECONDS:
        return kept(
            f"the claim sentence is only {block_len:.1f}s — too short "
            "to open the clip on its own"
        )
    if duration - block_end + block_start < REMAINDER_MIN_SECONDS:
        return kept("almost nothing of the clip would remain after the claim sentence")
    if block_len > BLOCK_MAX_FRACTION * duration:
        return kept("the claim sentence spans most of the clip; there is no separate opening to move")

    # Context guard: the sentence must stand on its own (same judgement
    # evaluate_hook trusts — pronouns, back-references, continuations).
    try:
        from ..media.timeline import _opening_is_weak

        timed = [
            w for w in words
            if isinstance(w, dict)
            and isinstance(w.get("start"), (int, float))
            and isinstance(w.get("end"), (int, float))
            and float(w.get("end", 0)) > 0
        ]
        if _opening_is_weak(timed, int(candidate["first_idx"])):
            opener = _word_text(timed[int(candidate["first_idx"])]).strip(" .,!?\"'").lower()
            return kept(
                f"the claim sentence opens with '{opener}', which points "
                "back at context the viewer would not have seen; the "
                "original order is kept"
            )
    except Exception:
        logger.warning("Bold-claim context guard (weak opener) failed", exc_info=True)

    # A planner punchline/payoff keeps its setup even here.
    if plan is not None:
        setup_reason = _payoff_needs_setup(plan, block_start + 0.01)
        if setup_reason:
            return kept(setup_reason)

    reordered = _reorder_keep_ranges(keep_ranges, block_start, block_end, duration)
    if reordered is None:
        return kept(
            "the claim sentence could not be separated cleanly from "
            "the clip's footage, so the original order is kept"
        )
    new_plan = remap_plan(plan, block_start, block_end) if plan is not None else None
    logger.info(
        "Bold-claim opener applied: claim sentence [%.2f, %.2f] (score %d) "
        "moved to front",
        block_start,
        block_end,
        candidate["score"],
    )
    return HookDecision(
        outcome={
            **base,
            "status": "bold_claim_opener",
            "reason": (
                f"the clip opened on a descriptive line; its strongest "
                f"claim sentence ({block_start:.1f}s-{block_end:.1f}s, "
                f"marker score {candidate['score']}) was moved to the "
                "front — no words were cut or reworded"
            ),
        },
        keep_ranges=reordered,
        plan=new_plan,
    )


def evaluate_hook(
    plan: Optional[ClipPlan],
    *,
    words: list[dict],
    keep_ranges: list,
    duration: float,
) -> HookDecision:
    """Decide the hook enforcement for one planned clip.

    Pure decision + slicing: the caller applies the returned keep
    ranges (rebuilding words/EDL on the new timeline) and persists the
    outcome on the clip's plan. Never raises on odd input — a decision
    problem must not cost the user a clip; the worst case is the
    original order with the reason recorded.
    """
    if not hook_enforcement_enabled():
        return HookDecision()
    if plan is None or plan.source != "llm" or plan.hook.type == "none_strong":
        return HookDecision(
            outcome={
                "status": "no_hook",
                "reason": "the planner found no hook moment in this clip",
                "hook_timestamp_before": round(float(plan.hook.timestamp), 3)
                if plan is not None
                else None,
                "hook_timestamp_after": round(float(plan.hook.timestamp), 3)
                if plan is not None
                else None,
                "window_seconds": HOOK_WINDOW_SECONDS,
            }
        )

    hook_ts = float(plan.hook.timestamp)
    base = {
        "hook_timestamp_before": round(hook_ts, 3),
        "hook_timestamp_after": round(hook_ts, 3),
        "window_seconds": HOOK_WINDOW_SECONDS,
    }
    if hook_ts <= HOOK_WINDOW_SECONDS:
        return HookDecision(
            outcome={
                **base,
                "status": "already_leading",
                "reason": (
                    f"the planned hook lands at {hook_ts:.1f}s, inside "
                    f"the first {HOOK_WINDOW_SECONDS:.0f}s"
                ),
            }
        )
    if not keep_ranges or duration <= 0:
        return HookDecision(
            outcome={
                **base,
                "status": "no_hook",
                "reason": "the clip has no renderable ranges to reorder",
            }
        )

    block = sentence_block(words, hook_ts)
    if block is None:
        return HookDecision(
            outcome={
                **base,
                "status": "no_hook",
                "reason": (
                    "the hook moment could not be located in the clip's "
                    "word timings, so nothing was moved"
                ),
            }
        )
    block_start, block_end, first_idx, _last_idx = block
    base = {**base, "block_start": block_start, "block_end": block_end}

    def kept(reason: str) -> HookDecision:
        return HookDecision(outcome={**base, "status": "kept_original_context", "reason": reason})

    if block_start <= 0.05:
        return kept(
            "the hook sits inside the clip's opening sentence; moving "
            "that sentence cannot bring it earlier without cutting the "
            "sentence in half"
        )
    block_len = block_end - block_start
    if block_len < BLOCK_MIN_SECONDS:
        return kept(
            f"the hook's sentence is only {block_len:.1f}s — too short "
            "to open the clip on its own"
        )
    if duration - block_end + block_start < REMAINDER_MIN_SECONDS:
        return kept("almost nothing of the clip would remain after the hook sentence")
    if block_len > BLOCK_MAX_FRACTION * duration:
        return kept(
            "the hook's sentence spans most of the clip; there is no "
            "separate opening to move"
        )

    # Context guard 1: the sentence must stand on its own. The timeline
    # module's opening judgement (pronouns, agreement openers,
    # back-references like "as I said") is the same one the clip-start
    # snapper trusts.
    try:
        from ..media.timeline import _opening_is_weak

        if _opening_is_weak(words, first_idx):
            opener = _word_text(words[first_idx]).strip(" .,!?\"'").lower()
            return kept(
                f"the hook sentence opens with '{opener}', which points "
                "back at context the viewer would not have seen; the "
                "original order is kept"
            )
    except Exception:
        logger.warning("Hook context guard (weak opener) failed", exc_info=True)

    # Context guard 2: payoff / punchline hooks need their setup.
    setup_reason = _payoff_needs_setup(plan, hook_ts)
    if setup_reason:
        return kept(setup_reason)

    reordered = _reorder_keep_ranges(keep_ranges, block_start, block_end, duration)
    if reordered is None:
        return kept(
            "the hook sentence could not be separated cleanly from the "
            "clip's footage, so the original order is kept"
        )

    new_plan = remap_plan(plan, block_start, block_end)
    logger.info(
        "Cold open applied: hook sentence [%.2f, %.2f] moved to front; "
        "hook lands at %.2fs (was %.2fs)",
        block_start,
        block_end,
        new_plan.hook.timestamp,
        hook_ts,
    )
    return HookDecision(
        outcome={
            **base,
            "status": "cold_opened",
            "reason": (
                f"the planned hook landed at {hook_ts:.1f}s; its whole "
                f"sentence ({block_start:.1f}s-{block_end:.1f}s) was "
                "moved to the front as a cold open — no words were cut "
                "or reworded"
            ),
            "hook_timestamp_after": round(float(new_plan.hook.timestamp), 3),
        },
        keep_ranges=reordered,
        plan=new_plan,
    )
