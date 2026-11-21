"""
Learning service - glue between the API/pipeline and the learning engine.

Responsibilities:

- Build the feature snapshot stored with every feedback event (the clip's
  virality sub-scores, hook type, resolved niche, caption preset, music/SFX
  settings) from the task/clip rows the caller already loaded.
- Record feedback so it can NEVER fail the user action that produced it:
  every entry point here swallows and logs its own errors.
- Maintain the versioned adjustment document: explicit feedback triggers a
  recomputation; the resolver used by the pipeline catches up passively
  when new (passive) events have arrived since the last version.
- Resolve the current document for the pipeline. With
  ``LEARNING_ENABLED=false`` — or on any error — the resolver returns
  None, which every consumer treats as "base parameters, untouched".
"""

import logging
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from ..auto_director import learning
from ..auto_director.edl import load_profile
from ..auto_director.niche import NICHE_VALUES, validate_niche_override
from ..repositories.learning_repository import LearningRepository

logger = logging.getLogger(__name__)


def _valid_niche(value: Any) -> Optional[str]:
    try:
        return validate_niche_override(value)
    except ValueError:
        return None


def resolve_snapshot_niche(
    task: Dict[str, Any], clip: Dict[str, Any]
) -> Optional[str]:
    """The niche a clip's feedback belongs to, when it is honestly known.

    Clip override beats task override — the same precedence the pipeline
    uses. The classifier fallback (podcast_news) is deliberately NOT used:
    feedback recorded against a guessed niche would teach the wrong bucket.
    """
    return _valid_niche(clip.get("niche_override")) or _valid_niche(
        task.get("niche_override")
    )


def build_clip_snapshot(
    task: Dict[str, Any],
    clip: Dict[str, Any],
    niche_override: Optional[str] = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """(niche, features) snapshot for one feedback event.

    Profile-derived fields come from a fresh ``load_profile`` of the
    resolved niche — the same base profile the pipeline rendered with.
    ``niche_override`` wins when the signal is about a niche other than
    the clip's stored one (a re-render request targets the NEW niche).
    Everything is best-effort: a snapshot gap must never block recording.
    """
    niche = _valid_niche(niche_override) or resolve_snapshot_niche(task, clip)
    features: Dict[str, Any] = {
        "virality_score": clip.get("virality_score"),
        "hook_score": clip.get("hook_score"),
        "engagement_score": clip.get("engagement_score"),
        "value_score": clip.get("value_score"),
        "shareability_score": clip.get("shareability_score"),
        "hook_type": clip.get("hook_type"),
        "relevance_score": clip.get("relevance_score"),
        "caption_template": task.get("caption_template"),
        "music_override": task.get("music_override"),
    }
    music_audible = bool(task.get("music_override"))
    if niche:
        try:
            profile = load_profile(niche)
            audio = profile.get("audio") or {}
            mood = audio.get("music_mood")
            try:
                volume = float(audio.get("music_volume_0_to_1") or 0.0)
            except (TypeError, ValueError):
                volume = 0.0
            features["profile"] = niche
            features["caption_preset_id"] = (profile.get("captions") or {}).get(
                "preset_id"
            )
            features["music_mood"] = mood
            features["music_volume"] = volume
            features["sfx_enabled"] = bool(audio.get("sfx_from_user_library"))
            music_audible = music_audible or (
                bool(mood) and mood != "none" and volume > 0
            )
        except Exception:
            logger.warning(
                "Could not snapshot profile features for niche %s",
                niche,
                exc_info=True,
            )
    features["music_audible"] = music_audible
    return niche, features


def _base_caption_presets() -> Dict[str, Optional[str]]:
    """niche -> the caption preset its shipped profile uses today."""
    presets: Dict[str, Optional[str]] = {}
    for niche in sorted(NICHE_VALUES):
        try:
            profile = load_profile(niche)
            presets[niche] = (profile.get("captions") or {}).get("preset_id")
        except Exception:
            presets[niche] = None
    return presets


async def refresh_learning_document(
    db: AsyncSession, user_id: str
) -> Optional[Dict[str, Any]]:
    """Recompute the user's adjustment document if events have moved on.

    Writes a new version only when the event count advanced past the
    current document's; returns the current (or new) document, or None
    when the user has no feedback yet.
    """
    events = await LearningRepository.list_events(db, user_id)
    if not events:
        return None
    current = await LearningRepository.current_version(db, user_id)
    if current and current["event_count"] >= len(events):
        return current
    adjustments = learning.compute_adjustments(events, _base_caption_presets())
    summary = learning.describe_adjustments(
        adjustments, (current or {}).get("niches")
    )
    if not summary:
        summary = [
            f"No adjustment change — evidence still below thresholds "
            f"({len(events)} events)"
        ]
    document = learning.new_document(
        version=(current["version"] + 1) if current else 1,
        event_count=len(events),
        adjustments=adjustments,
        summary=summary,
    )
    await LearningRepository.insert_version(db, user_id, document)
    logger.info(
        "Learning document for user %s advanced to version %s (%s events)",
        user_id,
        document["version"],
        len(events),
    )
    return document


async def resolve_learning_document(
    db: AsyncSession, user_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    """The current adjustment document for pipeline consumers, or None.

    None means "no learned adjustments" — disabled, no feedback yet, or
    any failure along the way. Consumers must treat None as base
    behaviour; learning must never break a render.
    """
    if not user_id or not learning.learning_enabled():
        return None
    try:
        return await refresh_learning_document(db, user_id)
    except Exception:
        logger.warning(
            "Learning resolver failed; continuing with base parameters",
            exc_info=True,
        )
        return None


async def record_feedback_safely(
    db: AsyncSession,
    *,
    user_id: str,
    task: Dict[str, Any],
    clip: Dict[str, Any],
    verdict: str,
    reason: Optional[str],
    source: str,
    niche_override: Optional[str] = None,
) -> bool:
    """Append one feedback event; NEVER raises. Returns True if stored."""
    if not learning.learning_enabled():
        return False
    try:
        niche, features = build_clip_snapshot(task, clip, niche_override)
        await LearningRepository.record_event(
            db,
            user_id=user_id,
            task_id=str(task.get("id") or clip.get("task_id") or ""),
            clip_id=str(clip.get("id") or ""),
            niche=niche,
            verdict=verdict,
            reason=reason,
            source=source,
            weight=learning.SOURCE_WEIGHTS.get(source, 1.0),
            features=features,
        )
        return True
    except Exception:
        logger.warning(
            "Could not record %s feedback for clip %s",
            source,
            clip.get("id"),
            exc_info=True,
        )
        return False


async def record_explicit_feedback(
    db: AsyncSession,
    *,
    user_id: str,
    task: Dict[str, Any],
    clip: Dict[str, Any],
    verdict: str,
    reason: Optional[str],
) -> Dict[str, Any]:
    """Record an explicit verdict and refresh the adjustment document.

    Returns ``{"recorded": bool, "learning_version": int | None}``. Like
    the passive path this never raises: a storage hiccup degrades to
    "not recorded" instead of failing the user's click.
    """
    if not learning.learning_enabled():
        return {"recorded": False, "learning_version": None}
    recorded = await record_feedback_safely(
        db,
        user_id=user_id,
        task=task,
        clip=clip,
        verdict=verdict,
        reason=reason,
        source="explicit",
    )
    if not recorded:
        return {"recorded": False, "learning_version": None}
    try:
        document = await refresh_learning_document(db, user_id)
    except Exception:
        logger.warning(
            "Feedback recorded but the learning document could not refresh",
            exc_info=True,
        )
        document = None
    return {
        "recorded": True,
        "learning_version": document["version"] if document else None,
    }


async def rollback_learning(
    db: AsyncSession, user_id: str, target_version: int
) -> Dict[str, Any]:
    """Append a new version restoring ``target_version``'s adjustments."""
    versions = await LearningRepository.list_versions(db, user_id)
    event_count = await LearningRepository.count_events(db, user_id)
    document = learning.rollback_document(
        versions, target_version, event_count=event_count
    )
    await LearningRepository.insert_version(db, user_id, document)
    return document


async def learning_status(db: AsyncSession, user_id: str) -> Dict[str, Any]:
    """Plain-data status payload for GET /api/learning/status."""
    enabled = learning.learning_enabled()
    document = await resolve_learning_document(db, user_id) if enabled else None
    events = await LearningRepository.list_events(db, user_id)
    versions = await LearningRepository.list_versions(db, user_id)
    return {
        "enabled": enabled,
        "current_version": document["version"] if document else None,
        "event_count": len(events),
        "adjustments": (document or {}).get("niches") or {},
        "evidence": learning.evidence_summary(events),
        "versions": [
            {
                "version": doc["version"],
                "created_at": doc["created_at"],
                "event_count": doc["event_count"],
                "summary": doc["summary"],
            }
            for doc in versions
        ],
    }
