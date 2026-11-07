"""Niche override validation and effective-profile resolution.

The classifier's niches are repeated here as plain data so that the
render-completion logging path never has to import the LLM stack. A unit test
keeps these sets in sync with ``classify``'s ``NICHE_VALUES``,
``AUTO_NICHE_VALUES`` and ``MANUAL_ONLY_NICHES``.
"""

from typing import Optional

# Every niche a user may select explicitly (manual override / dropdown).
NICHE_VALUES = frozenset(
    {
        "podcast_news",
        "travel_vlog",
        "meme_comedy",
        "gaming",
        "sports",
        "education_tutorial",
        "finance_business",
        "vlog_lifestyle",
        "true_crime",
        "hindi_podcast",
        "music_artist",
        "football_edit",
        "japan_travel_cinematic",
        "anime_funk_edit",
    }
)

# Niches the transcript classifier may return (mirrors classify's
# AUTO_NICHE_VALUES / NicheLiteral).
AUTO_NICHE_VALUES = frozenset(
    {
        "podcast_news",
        "travel_vlog",
        "meme_comedy",
        "gaming",
        "sports",
        "education_tutorial",
        "finance_business",
        "vlog_lifestyle",
        "true_crime",
        "hindi_podcast",
        "music_artist",
    }
)

# Visual/beat-driven niches with no usable transcript signal: selectable
# only via an explicit override, never from the classifier.
MANUAL_ONLY_NICHES = frozenset(
    {
        "football_edit",
        "japan_travel_cinematic",
        "anime_funk_edit",
    }
)

FALLBACK_NICHE = "podcast_news"


def validate_niche_override(value: object) -> Optional[str]:
    """Return a normalized niche override or raise ``ValueError``.

    ``None`` (and a missing value) means "let the classifier decide". The API
    layer turns the raised error into the repository's standard 422 response.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"Invalid niche_override. Valid values: {sorted(NICHE_VALUES)} or null."
        )
    normalized = value.strip()
    if normalized not in NICHE_VALUES:
        raise ValueError(
            f"Invalid niche_override '{value}'. Valid values: {sorted(NICHE_VALUES)} or null."
        )
    return normalized


def resolve_effective_profile(
    clip_override: Optional[str] = None,
    project_override: Optional[str] = None,
    classifier_niche: Optional[str] = None,
) -> str:
    """Resolve the niche profile used to build a clip's EDL.

    Precedence: explicit clip override, then project/task override, then the
    classifier result. Overrides may name any known niche, including the
    manual-only ones; the classifier result is only honoured when it is an
    auto-detectable niche. Unknown classifier values fall back to the
    classifier's own fallback niche rather than failing a render.
    """
    for candidate in (clip_override, project_override):
        if candidate in NICHE_VALUES:
            return candidate
    if classifier_niche in AUTO_NICHE_VALUES:
        return classifier_niche
    return FALLBACK_NICHE
