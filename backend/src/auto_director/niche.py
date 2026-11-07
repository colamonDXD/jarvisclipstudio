"""Niche override validation and effective-profile resolution.

The classifier's eight niches are repeated here as plain data so that the
render-completion logging path never has to import the LLM stack. A unit test
keeps this set in sync with ``classify.NICHE_VALUES``.
"""

from typing import Optional

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
    classifier result. Unknown classifier values fall back to the classifier's
    own fallback niche rather than failing a render.
    """
    for candidate in (clip_override, project_override):
        if candidate in NICHE_VALUES:
            return candidate
    if classifier_niche in NICHE_VALUES:
        return classifier_niche
    return FALLBACK_NICHE
