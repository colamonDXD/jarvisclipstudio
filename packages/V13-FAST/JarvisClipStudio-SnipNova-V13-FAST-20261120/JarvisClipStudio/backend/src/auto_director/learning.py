"""Learning engine: turn clip feedback into bounded, reversible adjustments.

This is deliberately NOT model training. The engine aggregates the user's
explicit accepts/rejects (plus weaker passive signals the app already
produces — downloads, exports, deletions, re-render requests) into a small
set of *bounded parameter adjustments* per (user, niche):

- selection weight nudges on the four virality sub-scores (0.80x–1.20x),
- an SFX density multiplier (0.5x–1.5x) read by the EDL builder,
- a music volume multiplier (0.0 = learned "keep it off"),
- a preferred caption preset, learned across niches.

Every adjustment needs a minimum pile of evidence (see the thresholds
below); below threshold the engine changes nothing. Adjustments live in a
versioned document (see ``learning_repository``); the base niche profiles
on disk are NEVER mutated — the resolver hands callers an adjusted *copy*.

Kill switch: ``LEARNING_ENABLED=false`` makes every resolver entry point
return "no adjustment", so selection and rendering behave exactly as they
did before this module existed.
"""

from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

VERDICTS = ("accept", "reject")

# Why a user rejected a clip. ``hook_weak`` and ``pacing`` map onto virality
# sub-scores; the rest drive their own adjustment channels below.
REASONS = ("hook_weak", "framing", "captions", "music", "sfx", "pacing", "other")

SUBSCORE_FIELDS = (
    "hook_score",
    "engagement_score",
    "value_score",
    "shareability_score",
)

REASON_TO_SUBSCORE = {
    "hook_weak": "hook_score",
    "pacing": "engagement_score",
}

# Feedback sources. Explicit verdicts are full-strength evidence; passive
# signals (things the user did anyway) count at half strength.
SOURCE_WEIGHTS = {
    "explicit": 1.0,
    "passive_download": 0.5,
    "passive_export": 0.5,
    "passive_delete": 0.5,
    "passive_rerender": 0.5,
}

# ---------------------------------------------------------------------------
# Bounds and evidence thresholds (the whole point: small, earned, reversible)
# ---------------------------------------------------------------------------

WEIGHT_FLOOR = 0.80
WEIGHT_CEILING = 1.20
# (minimum weighted rejects citing the mapped reason, weight delta)
SUBSCORE_UP_STEPS = ((10.0, 0.20), (6.0, 0.15), (3.0, 0.10))
# A sub-score the user never complains about, in a niche they complain
# about a lot, is relatively less important to them: step it down.
SUBSCORE_DOWN_STEPS = ((10.0, -0.20), (6.0, -0.10))
MIN_ACCEPT_WEIGHT_FOR_DOWNSTEP = 3.0

SFX_DOWN_STEPS = ((6.0, 0.50), (3.0, 0.75))
# Turning SFX *up* needs more evidence than turning them down: a liked
# clip's SFX may be incidental, while "too many effects" rejects name the
# cause directly.
SFX_UP_STEPS = ((12.0, 1.50), (8.0, 1.25))

# Music is a strong default to flip, so it needs the most consistent
# evidence: five weighted signals pointing the same way.
MIN_MUSIC_SIGNALS = 5.0
MUSIC_UP_MULTIPLIER = 1.25

# Caption affinity: a preset the user keeps accepting (any niche) becomes
# the candidate for a niche whose captions they keep rejecting.
MIN_CAPTION_ACCEPTS = 3.0
MIN_CAPTION_REJECTS_FOR_SWITCH = 2.0


def learning_enabled() -> bool:
    """``LEARNING_ENABLED=false`` (or 0/no/off) disables the whole engine."""
    return os.environ.get("LEARNING_ENABLED", "").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


# ---------------------------------------------------------------------------
# Aggregation: feedback events -> per-niche adjustments (pure)
# ---------------------------------------------------------------------------


def _event_weight(event: Dict[str, Any]) -> float:
    try:
        weight = float(event.get("weight"))
    except (TypeError, ValueError):
        weight = SOURCE_WEIGHTS.get(str(event.get("source") or "explicit"), 1.0)
    return max(0.0, weight)


def _niche_evidence(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Tally one niche's events into the numbers the rules below read."""
    evidence: Dict[str, Any] = {
        "events": 0,
        "accept_weight": 0.0,
        "reject_weight": 0.0,
        "reason_reject_weights": {},  # reason -> weighted rejects citing it
        "accepts_with_music": 0.0,  # accepts on clips that actually had music
        "caption_accept_weights": {},  # caption preset -> weighted accepts
    }
    for event in events:
        weight = _event_weight(event)
        if weight <= 0:
            continue
        evidence["events"] += 1
        features = event.get("features") or {}
        if event.get("verdict") == "accept":
            evidence["accept_weight"] += weight
            if features.get("music_audible"):
                evidence["accepts_with_music"] += weight
            preset = features.get("caption_preset_id")
            if preset:
                weights = evidence["caption_accept_weights"]
                weights[preset] = weights.get(preset, 0.0) + weight
        elif event.get("verdict") == "reject":
            evidence["reject_weight"] += weight
            reason = event.get("reason")
            if reason:
                weights = evidence["reason_reject_weights"]
                weights[reason] = weights.get(reason, 0.0) + weight
    return evidence


def _stepped(steps: Iterable[tuple], value: float) -> Optional[float]:
    """First (highest-threshold-first) step whose threshold ``value`` meets."""
    for threshold, outcome in steps:
        if value >= threshold:
            return outcome
    return None


def _niche_adjustment(
    evidence: Dict[str, Any],
    *,
    favourite_caption_preset: Optional[str],
    base_caption_preset: Optional[str],
) -> Dict[str, Any]:
    """Apply every rule to one niche's evidence. Empty dict = no change."""
    adjustment: Dict[str, Any] = {}
    reason_rejects = evidence["reason_reject_weights"]

    # --- Virality sub-score weights -------------------------------------
    weights: Dict[str, float] = {}
    for reason, field in REASON_TO_SUBSCORE.items():
        delta = _stepped(SUBSCORE_UP_STEPS, reason_rejects.get(reason, 0.0))
        if delta is not None:
            weights[field] = round(1.0 + delta, 4)
    if evidence["reject_weight"] >= SUBSCORE_DOWN_STEPS[-1][0] and (
        evidence["accept_weight"] >= MIN_ACCEPT_WEIGHT_FOR_DOWNSTEP
    ):
        for field in SUBSCORE_FIELDS:
            if field in weights:
                continue
            cited = any(
                REASON_TO_SUBSCORE.get(reason) == field for reason in reason_rejects
            )
            if cited:
                continue
            delta = _stepped(SUBSCORE_DOWN_STEPS, evidence["reject_weight"])
            if delta is not None:
                weights[field] = round(1.0 + delta, 4)
    weights = {
        field: min(WEIGHT_CEILING, max(WEIGHT_FLOOR, value))
        for field, value in weights.items()
        if value != 1.0
    }
    if weights:
        adjustment["score_weights"] = weights

    # --- SFX density ------------------------------------------------------
    sfx_rejects = reason_rejects.get("sfx", 0.0)
    sfx_multiplier = _stepped(SFX_DOWN_STEPS, sfx_rejects)
    if sfx_multiplier is None and sfx_rejects == 0.0:
        sfx_multiplier = _stepped(SFX_UP_STEPS, evidence["accept_weight"])
    if sfx_multiplier is not None and sfx_multiplier != 1.0:
        adjustment["sfx_density_multiplier"] = sfx_multiplier

    # --- Music ------------------------------------------------------------
    if reason_rejects.get("music", 0.0) >= MIN_MUSIC_SIGNALS:
        adjustment["music_volume_multiplier"] = 0.0
    elif evidence["accepts_with_music"] >= MIN_MUSIC_SIGNALS:
        adjustment["music_volume_multiplier"] = MUSIC_UP_MULTIPLIER

    # --- Caption preset affinity ------------------------------------------
    caption_rejects = reason_rejects.get("captions", 0.0)
    if (
        favourite_caption_preset
        and caption_rejects >= MIN_CAPTION_REJECTS_FOR_SWITCH
        and favourite_caption_preset != base_caption_preset
    ):
        adjustment["preferred_caption_preset"] = favourite_caption_preset

    return adjustment


def compute_adjustments(
    events: Iterable[Dict[str, Any]],
    base_caption_presets: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Aggregate feedback events into per-niche adjustments (pure).

    ``events`` are dicts with ``niche``, ``verdict``, ``reason``, ``source``,
    ``weight`` and ``features`` keys (the shape the repository stores).
    Events without a niche are counted nowhere: an adjustment is only ever
    applied to the niche its evidence came from. ``base_caption_presets``
    maps niche -> the preset its profile ships today, so caption affinity
    never "learns" the preset a niche already uses.
    """
    by_niche: Dict[str, List[Dict[str, Any]]] = {}
    global_caption_accepts: Dict[str, float] = {}
    for event in events:
        features = event.get("features") or {}
        if event.get("verdict") == "accept":
            preset = features.get("caption_preset_id")
            if preset:
                weight = _event_weight(event)
                global_caption_accepts[preset] = (
                    global_caption_accepts.get(preset, 0.0) + weight
                )
        niche = event.get("niche")
        if niche:
            by_niche.setdefault(str(niche), []).append(event)

    favourite_preset: Optional[str] = None
    if global_caption_accepts:
        top_preset, top_weight = max(
            global_caption_accepts.items(), key=lambda item: item[1]
        )
        if top_weight >= MIN_CAPTION_ACCEPTS:
            favourite_preset = top_preset

    base_caption_presets = base_caption_presets or {}
    adjustments: Dict[str, Dict[str, Any]] = {}
    for niche, niche_events in by_niche.items():
        adjustment = _niche_adjustment(
            _niche_evidence(niche_events),
            favourite_caption_preset=favourite_preset,
            base_caption_preset=base_caption_presets.get(niche),
        )
        if adjustment:
            adjustments[niche] = adjustment
    return adjustments


def evidence_summary(events: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-niche evidence tallies (for the status endpoint; no decisions)."""
    by_niche: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        niche = event.get("niche")
        if niche:
            by_niche.setdefault(str(niche), []).append(event)
    return {niche: _niche_evidence(items) for niche, items in by_niche.items()}


def describe_adjustments(
    adjustments: Dict[str, Dict[str, Any]],
    previous: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[str]:
    """Human-readable lines for a version's change summary."""
    previous = previous or {}
    lines: List[str] = []
    for niche in sorted(set(adjustments) | set(previous)):
        new = adjustments.get(niche) or {}
        old = previous.get(niche) or {}
        if new == old:
            continue
        for field, value in sorted((new.get("score_weights") or {}).items()):
            lines.append(
                f"{niche}: {field} selection weight -> {value:g} "
                f"(was {(old.get('score_weights') or {}).get(field, 1.0):g})"
            )
        for key, label in (
            ("sfx_density_multiplier", "SFX density"),
            ("music_volume_multiplier", "music volume"),
            ("preferred_caption_preset", "preferred caption preset"),
        ):
            if new.get(key) != old.get(key):
                lines.append(
                    f"{niche}: {label} -> {new.get(key)} (was {old.get(key)})"
                )
        if not new and old:
            lines.append(f"{niche}: all learned adjustments cleared")
    return lines


# ---------------------------------------------------------------------------
# Version documents
# ---------------------------------------------------------------------------


def new_document(
    *,
    version: int,
    event_count: int,
    adjustments: Dict[str, Dict[str, Any]],
    summary: List[str],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """One immutable version of a user's learned adjustments."""
    return {
        "version": int(version),
        "event_count": int(event_count),
        "created_at": (now or datetime.now(timezone.utc)).isoformat(),
        "summary": list(summary),
        "niches": copy.deepcopy(adjustments),
    }


def rollback_document(
    versions: List[Dict[str, Any]],
    target_version: int,
    *,
    event_count: int,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the new version that restores ``target_version``'s adjustments.

    History is append-only: a rollback is a new version whose adjustments
    are the target's, never an edit of the target itself.
    """
    target = next(
        (doc for doc in versions if doc.get("version") == target_version), None
    )
    if target is None:
        raise ValueError(f"Unknown learning version: {target_version}")
    current_version = max((doc.get("version", 0) for doc in versions), default=0)
    return new_document(
        version=current_version + 1,
        event_count=event_count,
        adjustments=target.get("niches") or {},
        summary=[
            f"Rolled back to version {target_version} "
            f"(from version {current_version})"
        ],
        now=now,
    )


# ---------------------------------------------------------------------------
# Resolver: what the pipeline actually consumes
# ---------------------------------------------------------------------------


def score_weights_for(
    document: Optional[Dict[str, Any]], niche: Optional[str]
) -> Optional[Dict[str, float]]:
    """Full four-field weight dict for a niche, or None for "no change".

    Returning None (rather than all-1.0 weights) lets callers skip the
    adjusted code path entirely, which is what keeps the disabled /
    no-evidence behaviour identical to the pre-learning pipeline.
    """
    if not document or not niche:
        return None
    niche_adjustments = (document.get("niches") or {}).get(niche) or {}
    partial = niche_adjustments.get("score_weights") or {}
    if not partial:
        return None
    return {field: float(partial.get(field, 1.0)) for field in SUBSCORE_FIELDS}


def profile_adjustment_for(
    document: Optional[Dict[str, Any]], niche: Optional[str]
) -> Optional[Dict[str, Any]]:
    """The EDL-facing adjustments for a niche, or None for "no change"."""
    if not document or not niche:
        return None
    adjustment = (document.get("niches") or {}).get(niche) or {}
    relevant = {
        key: adjustment[key]
        for key in (
            "sfx_density_multiplier",
            "music_volume_multiplier",
            "preferred_caption_preset",
        )
        if key in adjustment
    }
    return relevant or None


def weighted_total(scores: Dict[str, Any], weights: Dict[str, float]) -> float:
    """Selection score under learned weights (plain sum when all are 1.0)."""
    total = 0.0
    for field in SUBSCORE_FIELDS:
        try:
            value = float(scores.get(field) or 0)
        except (TypeError, ValueError):
            value = 0.0
        total += value * float(weights.get(field, 1.0))
    return total


def apply_profile_adjustments(
    profile: Dict[str, Any], adjustment: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Return a profile copy with the learned adjustments applied.

    The input profile (freshly loaded from disk) is never mutated. The SFX
    multiplier is stamped onto the copy's audio section, where the EDL
    builder reads it; music volume is scaled in place on the copy; the
    caption affinity replaces the copy's preset id.
    """
    if not adjustment:
        return profile
    adjusted = copy.deepcopy(profile)

    sfx_multiplier = adjustment.get("sfx_density_multiplier")
    if sfx_multiplier is not None:
        audio = adjusted.setdefault("audio", {})
        audio["sfx_density_multiplier"] = min(1.5, max(0.5, float(sfx_multiplier)))

    music_multiplier = adjustment.get("music_volume_multiplier")
    if music_multiplier is not None:
        audio = adjusted.setdefault("audio", {})
        try:
            volume = float(audio.get("music_volume_0_to_1") or 0.0)
        except (TypeError, ValueError):
            volume = 0.0
        audio["music_volume_0_to_1"] = round(
            min(1.0, max(0.0, volume * float(music_multiplier))), 4
        )

    preset = adjustment.get("preferred_caption_preset")
    if preset:
        captions = adjusted.setdefault("captions", {})
        captions["preset_id"] = preset

    return adjusted
