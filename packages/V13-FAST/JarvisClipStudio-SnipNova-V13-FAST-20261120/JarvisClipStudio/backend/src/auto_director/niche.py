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


def is_known_profile(value: object) -> bool:
    """True for a stock niche or an existing user reference style.

    User styles (``user-<slug>``, Lane H2) count only while their file
    exists in the user profiles directory — a name whose style was
    deleted stops resolving instead of failing later in the pipeline.
    The classifier's own sets are untouched: it can never *return* a
    user style, users pick those explicitly.
    """
    if not isinstance(value, str):
        return False
    if value in NICHE_VALUES:
        return True
    from .user_profiles import is_user_profile_name, user_profile_exists

    return is_user_profile_name(value) and user_profile_exists(value)


def validate_niche_override(value: object) -> Optional[str]:
    """Return a normalized niche override or raise ``ValueError``.

    ``None`` (and a missing value) means "let the classifier decide". A
    blank string means the same thing: the create form's style picker can
    emit ``""`` for "no override picked" (its Select fires an empty value
    when a reference style is auto-selected before the dropdown has ever
    opened), and a hard 422 for an empty pick the user never typed is a
    dead end — other optional fields on the create route (font family /
    size) likewise treat a blank string as unset. The API layer turns the
    raised error into the repository's standard 422 response. A saved
    reference style (``user-<slug>``) is a valid override while it exists.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"Invalid niche_override. Valid values: {sorted(NICHE_VALUES)}, "
            "a saved reference style (user-...), or null."
        )
    normalized = value.strip()
    if not normalized:
        return None
    if not is_known_profile(normalized):
        raise ValueError(
            f"Invalid niche_override '{value}'. Valid values: {sorted(NICHE_VALUES)}, "
            "a saved reference style (user-...), or null."
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
        if is_known_profile(candidate):
            return candidate
    if classifier_niche in AUTO_NICHE_VALUES:
        return classifier_niche
    return FALLBACK_NICHE
