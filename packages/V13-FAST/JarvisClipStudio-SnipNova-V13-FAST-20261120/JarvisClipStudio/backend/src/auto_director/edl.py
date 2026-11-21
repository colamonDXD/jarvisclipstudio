"""EDL (Edit Decision List) builder for auto-director."""

from pathlib import Path
from typing import Any
import json
import os
import re

from .audio_flashes import plan_audio_flashes
from .beat_grid import consume_beat_grid, profile_beat_config
from .scene_rules import (
    SPEAKER_END_GUARD_SEC,
    SPEAKER_HOOK_GUARD_SEC,
    SPEAKER_STACK_GAP_SEC,
    establishing_guard_sec,
    scene_rules,
    speaker_change_times,
)
from .signature_transitions import (
    apply_signature_transition,
    transition_sfx,
)

PROFILES_DIR = Path(__file__).parent / "profiles"
_NICHES = {
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


def load_profile(niche: str) -> dict[str, Any]:
    """
    Load a niche profile by name.

    Stock niches load from the profiles directory. User-generated
    reference styles (``user-<slug>``, Lane H2) load from the user
    profiles directory instead — the stock branch is checked first and
    the ``user-`` prefix exists on no stock name, so a user profile can
    never shadow a stock profile.

    Args:
        niche: The niche name (e.g., "podcast_news", "travel_vlog",
            "user-my-style")

    Returns:
        The profile dict

    Raises:
        ValueError: If niche is unknown
        FileNotFoundError: If profile file doesn't exist
    """
    if niche in _NICHES:
        profile_path = PROFILES_DIR / f"{niche}.json"
        if not profile_path.exists():
            raise FileNotFoundError(f"Profile not found: {profile_path}")

        with open(profile_path, "r", encoding="utf-8") as f:
            return json.load(f)

    from .user_profiles import is_user_profile_name, user_profile_file

    if is_user_profile_name(niche):
        profile_path = user_profile_file(niche)
        if profile_path is None:
            raise FileNotFoundError(f"User profile not found: {niche}")
        with open(profile_path, "r", encoding="utf-8") as f:
            return json.load(f)

    raise ValueError(
        f"Unknown niche: '{niche}'. Valid niches: {sorted(_NICHES)} "
        "or a saved reference style (user-...)"
    )


def _extract_verbatim_quotes_and_numbers(transcript_words: list[dict]) -> list[dict]:
    """
    Extract verbatim quotes and numbers from transcript words for info cards.

    Only returns exact quotes (3+ words) and exact numbers from the transcript.
    """
    info_card_candidates = []

    # Join words to find sentences/quotes
    text = " ".join(w["word"] for w in transcript_words)

    # Find numbers (including currency, percentages, etc.)
    number_pattern = r'\b(?:\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?:%|percent|dollars?|USD|EUR|GBP|K|M|B|million|billion|trillion|k\b)\b'
    for match in re.finditer(number_pattern, text, re.IGNORECASE):
        info_card_candidates.append({
            "type": "number",
            "text": match.group(0).strip(),
            "start_char": match.start(),
            "end_char": match.end()
        })

    # Find quoted statements (verbatim quotes of 3+ words)
    # Look for sentences with strong claim/statement patterns
    sentence_pattern = r'[^.!?]+[.!?]'
    for match in re.finditer(sentence_pattern, text):
        sentence = match.group(0).strip()
        words = sentence.split()
        if len(words) >= 3:
            # Check if it's a strong claim (contains emphatic language)
            lower = sentence.lower()
            if any(marker in lower for marker in [
                "the truth is", "the reality is", "fact is", "data shows",
                "studies show", "research shows", "proves", "reveals",
                "never", "always", "must", "critical", "essential",
                "biggest", "smallest", "only", "first", "last"
            ]):
                info_card_candidates.append({
                    "type": "quote",
                    "text": sentence,
                    "start_char": match.start(),
                    "end_char": match.end()
                })

    return info_card_candidates


def _map_to_word_indices(candidates: list[dict], transcript_words: list[dict]) -> list[dict]:
    """Map character positions to word indices."""
    char_to_word = []
    char_pos = 0
    for i, w in enumerate(transcript_words):
        word_len = len(w["word"])
        char_to_word.append((char_pos, char_pos + word_len, i))
        char_pos += word_len + 1  # +1 for space

    result = []
    for c in candidates:
        start_word = None
        end_word = None
        for c_start, c_end, w_idx in char_to_word:
            if c_start <= c["start_char"] < c_end:
                start_word = w_idx
            if c_start < c["end_char"] <= c_end:
                end_word = w_idx
        if start_word is not None and end_word is not None:
            result.append({
                "type": c["type"],
                "text": c["text"],
                "start_word_idx": start_word,
                "end_word_idx": end_word
            })
    return result


def _create_zoom_keyframes(profile: dict, energy_peaks: list[float], clip_duration: float) -> list[dict]:
    """
    Create zoom keyframes at energy peaks based on profile's punch-in rules.

    Args:
        profile: The niche profile
        energy_peaks: List of timestamps (seconds) where energy peaks occur
        clip_duration: Total clip duration in seconds

    Returns:
        List of zoom keyframe dicts
    """
    zooms = profile.get("zooms", {})
    punch_in_scale = zooms.get("punch_in_scale", 1.0)
    punch_in_trigger = zooms.get("punch_in_trigger", "emphasis")
    ken_burns = zooms.get("ken_burns", False)

    keyframes = []

    # Ken Burns slow zoom (for travel_vlog)
    if ken_burns:
        keyframes.append({
            "type": "ken_burns",
            "start": 0.0,
            "end": clip_duration,
            "scale_start": 1.0,
            "scale_end": 1.05
        })

    # Punch-in at energy peaks
    for peak_time in energy_peaks:
        if 0 <= peak_time <= clip_duration:
            keyframes.append({
                "type": "punch_in",
                "trigger": punch_in_trigger,
                "time": peak_time,
                "scale": punch_in_scale,
                "duration": 0.5  # punch-in holds for ~0.5s
            })

    return keyframes


def _create_speaker_punches(
    profile: dict,
    transcript_words: list[dict],
    zoom_keyframes: list[dict],
    clip_duration: float,
) -> list[dict]:
    """
    Punch-in keyframes on diarized speaker changes (dialogue niches).

    TRIGGER: the profile opts in via ``scene_rules.speaker_punch_in``
    and the clip's words carry speaker labels showing a genuine change
    (see scene_rules.speaker_change_times — short interjections are
    absorbed, single-speaker and unlabelled transcripts yield nothing).
    The scale is the profile's own punch scale: the niche's grammar,
    fired by who is speaking instead of by loudness alone.

    RESTRAINT: no punch inside the opening second or the last breath,
    at least ``min_gap_sec`` between speaker punches, never stacked
    within 0.75 s of an existing punch-in (an energy peak that already
    earned one wins), and a hard per-clip cap.
    """
    rules = scene_rules(profile).get("speaker_punch_in")
    if not rules or clip_duration <= 0:
        return []
    try:
        scale = float(profile.get("zooms", {}).get("punch_in_scale", 1.0) or 1.0)
    except (TypeError, ValueError):
        return []
    if scale <= 1.0:
        return []
    existing = [
        float(z.get("time", -10))
        for z in zoom_keyframes
        if z.get("type") == "punch_in"
    ]
    punches: list[dict] = []
    last_taken = float("-inf")
    for at in speaker_change_times(
        transcript_words, min_turn_sec=float(rules["min_turn_sec"])
    ):
        if at < SPEAKER_HOOK_GUARD_SEC or at > clip_duration - SPEAKER_END_GUARD_SEC:
            continue
        if at - last_taken < float(rules["min_gap_sec"]):
            continue
        if any(abs(at - taken) < SPEAKER_STACK_GAP_SEC for taken in existing):
            continue
        punches.append({
            "type": "punch_in",
            "trigger": "speaker_change",
            "time": round(at, 3),
            "scale": scale,
            "duration": 0.5,
        })
        existing.append(at)
        last_taken = at
        if len(punches) >= int(rules["max_per_clip"]):
            break
    return punches


# Re-hook punches: in fast niches the edit re-grabs attention every
# 8–15 s with a punch-in on a phrase/segment boundary. They share the
# SFX stacking discipline — never within 0.75 s of another punch — and
# a hard total cap, so a long clip cannot turn into a zoom strobe.
_REHOOK_STACK_GAP_SEC = 0.75
_REHOOK_MAX_PUNCHES = 10


def _create_rehook_punches(
    profile: dict,
    segments: list[dict],
    zoom_keyframes: list[dict],
    clip_duration: float,
) -> list[dict]:
    """
    Punch-in keyframes on the profile's re-hook interval (beat_grid
    opt-in profiles only). Each punch lands on the nearest segment
    boundary to its ideal time — a zoom mid-shot reads as a mistake;
    on a cut it reads as intent.
    """
    cfg = profile_beat_config(profile)
    if cfg is None:
        return []
    try:
        interval = float(cfg.get("rehook_interval_sec") or 0)
        scale = float(profile.get("zooms", {}).get("punch_in_scale", 1.0) or 1.0)
    except (TypeError, ValueError):
        return []
    if interval < 4.0 or scale <= 1.0 or clip_duration <= interval:
        return []
    existing = [
        float(z.get("time", -10))
        for z in zoom_keyframes
        if z.get("type") == "punch_in"
    ]
    boundaries = [float(s.get("start", 0)) for s in segments[1:]]
    punches: list[dict] = []
    t = interval
    while t < clip_duration - 1.0:
        if boundaries:
            nearest = min(boundaries, key=lambda b: abs(b - t))
            at = nearest if abs(nearest - t) <= interval * 0.35 else t
        else:
            at = t
        if all(abs(at - taken) >= _REHOOK_STACK_GAP_SEC for taken in existing):
            punches.append({
                "type": "punch_in",
                "trigger": "rehook",
                "time": round(at, 3),
                "scale": scale,
                "duration": 0.5,
            })
            existing.append(at)
            if len(existing) >= _REHOOK_MAX_PUNCHES:
                break
        t += interval
    return punches


def _segment_render_duration(segment: dict) -> float:
    """A segment's duration on the rendered timeline.

    A speed-ramped segment occupies (end - start) / speed seconds of
    output; a freeze-frame segment (EDL ``freeze: True``, assigned by
    the trailer-breakdown template) occupies ``freeze_duration``
    seconds — its source bounds are a single frame, held for the
    beat. Every other segment occupies its natural length. The join
    math below (and the renderer's mirror of it) must agree on this
    or SFX/transition placement drifts off the picture.
    """
    if isinstance(segment, dict) and bool(segment.get("freeze")):
        try:
            held = float(segment.get("freeze_duration") or 0.0)
        except (TypeError, ValueError):
            held = 0.0
        return held if held > 0 else 1.5
    duration = float(segment.get("end", 0)) - float(segment.get("start", 0))
    speed = segment.get("speed")
    if isinstance(speed, (int, float)) and speed > 0 and speed != 1.0:
        return duration / float(speed)
    return duration


def _create_overlay_events(profile: dict, transcript_words: list[dict], clip_duration: float) -> list[dict]:
    """
    Create overlay events (info cards, lower thirds, reference images).

    HARD RULE: info-card overlay text must be a verbatim quote or number
    copied exactly from transcript_words.
    """
    overlays = profile.get("overlays", {})
    events = []

    if overlays.get("info_cards") or overlays.get("lower_thirds"):
        candidates = _extract_verbatim_quotes_and_numbers(transcript_words)
        mapped = _map_to_word_indices(candidates, transcript_words)

        for c in mapped:
            # Calculate time from word index (approximate)
            word_idx = c["start_word_idx"]
            time = (word_idx / len(transcript_words)) * clip_duration if transcript_words else 0

            if c["type"] == "number" and overlays.get("info_cards"):
                events.append({
                    "type": "info_card",
                    "time": time,
                    "duration": 3.0,
                    "text": c["text"],
                    "source": "transcript_verbatim"
                })
            elif c["type"] == "quote" and overlays.get("info_cards"):
                events.append({
                    "type": "info_card",
                    "time": time,
                    "duration": 4.0,
                    "text": c["text"],
                    "source": "transcript_verbatim"
                })

    # Lower thirds (name/title overlays) - placeholder for now
    if overlays.get("lower_thirds"):
        # Could extract speaker names from transcript_words if available
        pass

    # Reference images
    if overlays.get("reference_images") == "open_web" and overlays.get("ref_image_at_entity_mentions"):
        # Entity mentions would be detected elsewhere; placeholder for structure
        # We don't scrape - just mark where ref images should go
        pass
    elif overlays.get("reference_images") == "user_assets":
        # Local file paths only
        pass

    return events


def _broll_token(word: str) -> str:
    """One transcript word, normalised for B-roll matching (lowercased,
    punctuation stripped). Transcript words can carry trailing punctuation
    ("avocado.") that must not hide the noun."""
    return re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", (word or "").lower())


def _broll_word_matches(token: str, noun: str) -> bool:
    """Whether a transcript token names ``noun`` (exact, or a simple plural)."""
    if token == noun:
        return True
    if len(noun) >= 3 and token == noun + "s":
        return True
    if len(token) >= 4 and token.endswith("s") and token[:-1] == noun:
        return True
    return False


# B-roll trigger vocabulary, beyond the base list below. Self-run audit
# issue #7: the 20-noun base list can never fire on food/travel content
# words (avocado, wagyu, sushi...), while generic words (car, street,
# market) fire on passing mentions. This extension is curated and
# additive only — the base list is unchanged — and every entry names
# something filmable, so a trigger is a plausible cutaway subject.
_BROLL_TRIGGER_EXTENSION = [
    # Food & drink (the Japan fixture's actual content words first).
    "avocado", "wagyu", "sushi", "sandwich", "steak", "burger", "pizza",
    "ramen", "noodle", "coffee", "chocolate", "cake", "bread", "cheese",
    "fish", "meat", "rice", "pasta", "salad", "dessert", "breakfast",
    "lunch", "dinner", "kitchen", "chef", "recipe", "food", "drink",
    "wine", "beer", "fruit", "vegetable", "tree", "gold", "egg", "chicken",
    "pork", "beef", "seafood", "tea", "juice", "snack", "meal", "taste",
    "flavor", "spice", "sauce", "soup", "bowl",
    # Travel & place.
    "temple", "shrine", "castle", "museum", "train", "station", "airport",
    "flight", "island", "ocean", "sea", "river", "lake", "forest",
    "garden", "tower", "bridge", "village", "town", "school", "hospital",
    "library", "shop", "store", "mall", "theater", "stadium", "gym",
    "pool",
    # Nature & animals.
    "dog", "cat", "bird", "horse", "elephant", "lion", "tiger", "bear",
    "monkey", "snake", "flower", "rose", "sunset", "sunrise", "snow",
    "rain", "cloud", "sky", "star", "moon", "sun",
    # Sport, music & culture.
    "football", "soccer", "basketball", "tennis", "golf", "race",
    "marathon", "workout", "yoga", "dance", "music", "song", "guitar",
    "piano", "concert", "movie", "film", "book", "art", "photo",
    "camera", "video",
]


def _broll_sense_helpers():
    """(WORD_SENSES, indicated_sense, broll_search_query) from src.broll.

    src.broll owns the word-sense table because the relevance gate uses
    the same one (footage_mismatch). Lazy import, mirroring
    media/pixabay_broll.py: sense handling is a refinement and an import
    problem here must never cost the planner its cues.
    """
    try:
        from ..broll import WORD_SENSES, broll_search_query, indicated_sense
    except Exception:
        return None
    return WORD_SENSES, indicated_sense, broll_search_query


# Spoken words either side of a trigger occurrence that decide its
# sense. Kept tight on purpose: sense is a local property, and a wide
# window lets an earlier use of the same spelling in another sense
# (exercise "train" five words before a railway "train") bleed its
# markers into this occurrence's judgement.
_BROLL_SENSE_WINDOW_WORDS = 4


def _broll_sense_at(transcript_words: list[dict], index: int, noun: str) -> tuple[str, bool]:
    """(query, keep) for the ``noun`` occurrence at ``index``.

    An unambiguous noun always plans as itself. A polysemous trigger is
    judged by the words around that occurrence: "train" next to gym
    words is the exercise verb and plans NO railway cue, while a
    billiards "pool" plans a "billiards" cue under its own unambiguous
    name. Occurrences are judged one by one, so a later genuine use of
    the same spelling still plans its cue.
    """
    helpers = _broll_sense_helpers()
    if helpers is None:
        return noun, True
    word_senses, indicated_sense, _ = helpers
    entry = word_senses.get(noun)
    if not entry:
        return noun, True
    window = " ".join(
        str(w.get("word") or "")
        for w in transcript_words[
            max(0, index - _BROLL_SENSE_WINDOW_WORDS):
            index + _BROLL_SENSE_WINDOW_WORDS + 1
        ]
    )
    sense = indicated_sense(noun, window)
    if sense is None or sense == entry["vocab"]:
        return noun, True
    action = entry.get("actions", {}).get(sense, "skip")
    if action.startswith("resense:"):
        return action.split(":", 1)[1], True
    return noun, False


def _create_broll_queries(profile: dict, transcript_words: list[dict], clip_duration: float) -> list[dict]:
    """
    Create B-roll queries based on profile and transcript content.

    Reference overlays may only carry a local file path or an empty
    Pexels/Wikimedia URL field - no web scraping in this module.
    """
    broll = profile.get("broll", {})
    if not broll.get("enabled"):
        return []

    source = broll.get("source", "off")
    density = broll.get("density_per_min", 0)
    if source == "off" or density <= 0:
        return []

    # Extract key nouns/concepts from transcript for B-roll queries.
    # Word-level matching (not substring): "car" must not fire on
    # "careful", and a trailing full stop must not hide "avocado".
    tokens = [
        (_broll_token(w.get("word", "")), float(w.get("start", 0) or 0))
        for w in transcript_words
    ]
    # Simple noun extraction (in production, would use NER)
    common_nouns = [
        "city", "mountain", "beach", "office", "computer", "phone",
        "car", "building", "street", "park", "restaurant", "hotel",
        "chart", "graph", "data", "money", "stock", "market",
        "game", "player", "screen", "controller", "keyboard"
    ]
    # The base list is the floor; the curated extension adds the
    # food/travel (and other filmable) nouns the base list can never see.
    all_nouns = common_nouns + [
        noun for noun in _BROLL_TRIGGER_EXTENSION if noun not in common_nouns
    ]

    queries = []
    words_per_query = max(1, int(60 / density))  # spread across clip
    for noun in all_nouns:
        # First occurrence that survives the word-sense check: a
        # polysemous trigger spoken in its wrong sense ("train" the
        # exercise verb) plans no cue for that occurrence; a later
        # genuine occurrence of the same spelling still can. ``tokens``
        # is index-aligned with ``transcript_words``.
        chosen = None
        for index, (token, start) in enumerate(tokens):
            if not _broll_word_matches(token, noun):
                continue
            query_word, keep = _broll_sense_at(transcript_words, index, noun)
            if not keep:
                continue
            chosen = (query_word, start)
            break
        if chosen is not None:
            query_word, first_spoken = chosen
            queries.append({
                "query": query_word,
                "source": source,  # "stock" (the v2 resolver route) or "user_assets"
                "url": "",  # empty - no web scraping in this module
                "local_path": "",  # for user_assets
                # Clip-relative time the query word is first spoken.
                # _plan_broll_segments anchors the cutaway on it, so the
                # footage lands over the words it illustrates instead of
                # over whatever the next segment boundary happens to hold.
                "spoken_at": round(first_spoken, 3),
            })

    # The query cap uses the same +0.5 rounding as the planner's target
    # count (and the concept cap in clip_plan.py), so the density the
    # profile declares is the density the clip can actually reach: a
    # 60 s clip at 1.5/min may plan 2 cues, not 1.
    return queries[:max(1, int(clip_duration / 60 * density + 0.5))]


# B-roll placement. Stock cutaways land on topic changes (segment
# boundaries) — never over the opening hook, never on top of a punch-in
# zoom (the face-focused emphasis moments) or an info card, and never
# for more than BROLL_MAX_COVERAGE of the clip's duration.
BROLL_HOOK_GUARD_SEC = 2.0
BROLL_END_GUARD_SEC = 1.0
BROLL_CUE_SEC = 3.0
BROLL_MIN_CUE_SEC = 1.5
BROLL_MAX_COVERAGE = 0.30
# Per-profile coverage ceiling (design §5.1): a profile may raise its
# own cap up to this hard ceiling — travel/japan carry 0.40, because
# there B-roll IS the content. Nothing may exceed it anywhere: at ~70%
# coverage the measured completion fell back down.
BROLL_MAX_COVERAGE_CEILING = 0.40
_PUNCH_IN_PAD_SEC = 0.25
# At least this much speaker sits between two cutaways, so inserts
# punctuate the face instead of replacing it (design §5.2). Applies to
# every cue the planner places, concept or keyword.
BROLL_FACE_RETURN_SEC = 4.0
# First cue at >= this (design §5.2): a SOFT target, not a hard floor —
# the 2.0 s hook guard above stays the floor. It only orders the
# keyword planner's segment-boundary fallback (a boundary at/after
# this is tried first); a word-anchored cue is never moved off the word
# it illustrates, and a concept cue never moves off its clause.
BROLL_FIRST_CUE_SEC = 5.0
# Concept cue lengths (design §5.1): the fixed 3.0 s stays unless the
# concept carries a duration hint, which is honoured inside this band.
BROLL_CONCEPT_MIN_CUE_SEC = 2.5
BROLL_CONCEPT_MAX_CUE_SEC = 4.0
# A concept's anchor snaps to the nearest spoken word within this
# window, so the cutaway opens on speech rather than between words.
BROLL_CONCEPT_SNAP_SEC = 2.0


# Words spoken around a cutaway that its footage is judged against
# (mirrors broll.py's cue_context for the legacy system: from just
# before the cutaway to just after it ENDS). The tail matters: a sense
# marker spoken while the footage is on screen — "strategy", two
# seconds into the TJR "market" cue — is part of what the cue means,
# and a window that stops a moment after the cue starts starves the
# word-sense check on real speech.
BROLL_CONTEXT_BEFORE_SEC = 5.0
BROLL_CONTEXT_AFTER_SEC = 2.0

# Smart B-Roll v3 cadence (Phase 1, 2026-11-17). The v2 cadence was
# ``density_per_min`` (1.5-2.0/min) rounded PER CLIP, so a 30-45 s clip got
# exactly one planned cutaway and the owner's 165 s finance run could get at
# most ~4 (measured: one landed). Profiles that carry ``broll.cadence_per_min``
# now plan at that rate, with the lexicon cue source (broll_lexicon) filling
# the slots. ``BROLL_CADENCE=legacy`` restores the v2 arithmetic and cue
# source exactly; a profile without ``cadence_per_min`` is unchanged either way.
BROLL_CADENCE_ENV = "BROLL_CADENCE"


def broll_cadence_v3() -> bool:
    """True unless the deployment opted back to the v2 cadence."""
    return os.getenv(BROLL_CADENCE_ENV, "v3").strip().lower() not in (
        "legacy", "v2", "0", "off", "false",
    )


def broll_uses_v3(profile: dict) -> bool:
    """Whether this profile plans B-roll with the v3 cadence + cue source."""
    broll = (profile or {}).get("broll", {}) or {}
    return broll_cadence_v3() and broll.get("cadence_per_min") is not None


def broll_target_count(profile: dict, clip_duration: float) -> int:
    """Cutaways one clip may carry. v2: ``max(1, round-half-up(dur/60 *
    density_per_min))``. v3 (``broll_uses_v3``): the same shape at
    ``cadence_per_min``. Zero when the rate or the clip length is zero."""
    broll = (profile or {}).get("broll", {}) or {}
    key = "cadence_per_min" if broll_uses_v3(profile) else "density_per_min"
    try:
        rate = float(broll.get(key, 0) or 0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate <= 0 or clip_duration <= 0:
        return 0
    return max(1, int(clip_duration / 60 * rate + 0.5))


def _broll_context(
    transcript_words: list[dict], start: float, duration: float = BROLL_CUE_SEC
) -> str:
    """The words spoken in/near a B-roll window, for the relevance gate.

    The resolver (media/pixabay_broll.py) judges a stock hit against the
    query noun plus these words, so a hit must match what is actually
    being said while the cutaway is on screen.
    """
    return " ".join(
        str(w.get("word") or "")
        for w in transcript_words
        if start - BROLL_CONTEXT_BEFORE_SEC <= float(w.get("start", 0) or 0)
        <= start + duration + BROLL_CONTEXT_AFTER_SEC
    )


def _concept_broll_queries(
    profile: dict,
    concepts: list[dict],
    transcript_words: list[dict],
) -> list[dict]:
    """Turn validated plan concepts into placement-ready cue dicts.

    PRECEDENCE (Smart B-Roll v2): a concept is the planner's own
    sense-resolved reading of the moment, so it outranks the
    deterministic WORD_SENSES table wherever the two could disagree —
    the concept's phrases are what gets searched, its sense tag is the
    cue's sense, and the table's skip/re-sense machinery
    (``_broll_sense_at``) never drops or renames a concept cue. The
    table remains the only sense mechanism for concept-less (keyword)
    cues, and its relevance gate still judges every hit at resolve
    time, where the concept's ``avoid`` list rides along for the
    concept-aware gate (a later slice) to consume.

    ``query`` keeps the keyword path's provenance shape: the trigger
    noun when the concept carries a sense tag, else the first search
    phrase. The full concept payload rides on ``_concept`` for
    ``_try_place`` to stamp onto the entry.
    """
    source = (profile.get("broll", {}) or {}).get("source", "off")
    word_starts = sorted(
        float(w.get("start", 0) or 0) for w in transcript_words
    )
    queries: list[dict] = []
    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        search_queries = [
            str(q).strip()
            for q in (concept.get("search_queries") or [])
            if isinstance(q, str) and q.strip()
        ]
        if not search_queries:
            continue
        try:
            anchor = float(concept.get("anchor_sec", 0) or 0)
        except (TypeError, ValueError):
            anchor = 0.0
        snapped = anchor
        if word_starts:
            nearest = min(word_starts, key=lambda s: abs(s - anchor))
            if abs(nearest - anchor) <= BROLL_CONCEPT_SNAP_SEC:
                snapped = nearest
        sense = concept.get("sense") or None
        trigger = (
            str(sense).split(":", 1)[0] if sense else search_queries[0]
        )
        # Concept-dependent cue length (design §5.1): the planner may
        # hint a duration inside the 2.5–4.0 s band — a slow chart pan
        # earns longer than a quick product flash. Anything outside the
        # band clamps to it; no hint (or a non-numeric one) leaves the
        # fixed 3.0 s untouched. The hint is clamped here, not trusted.
        duration_hint = None
        try:
            raw_duration = concept.get("duration_sec")
            duration_hint = (
                float(raw_duration) if raw_duration is not None else None
            )
        except (TypeError, ValueError):
            duration_hint = None
        if duration_hint is not None:
            duration_hint = min(
                max(duration_hint, BROLL_CONCEPT_MIN_CUE_SEC),
                BROLL_CONCEPT_MAX_CUE_SEC,
            )
        queries.append({
            "query": trigger,
            "source": source,
            "url": "",
            "local_path": "",
            "spoken_at": round(snapped, 3),
            "_duration_sec": duration_hint,
            "_concept": {
                "concept": str(concept.get("concept") or ""),
                "spoken": str(concept.get("spoken") or ""),
                "sense": sense,
                "search_queries": search_queries,
                "media": concept.get("media") or "either",
                "avoid": [
                    str(a) for a in (concept.get("avoid") or []) if str(a).strip()
                ],
                **({"cue_source": str(concept["cue_source"])} if concept.get("cue_source") else {}),
                **({"lexicon_id": str(concept["lexicon_id"])} if concept.get("lexicon_id") else {}),
            },
        })
    return queries


def _lexicon_broll_queries(
    profile: dict, transcript_words: list[dict], clip_duration: float
) -> list[dict]:
    """v3 keyless cue source: phrase-level concepts from broll_lexicon,
    best first, as placement-ready cue dicts (the concept path's shape, so
    every concept guard applies). Falls back to the v2 keyword scan when the
    lexicon finds nothing, so v3 never plans fewer cues than v2 would."""
    broll = profile.get("broll", {}) or {}
    if not broll.get("enabled") or broll.get("source", "off") == "off":
        return []
    from .broll_lexicon import lexicon_concepts

    target = broll_target_count(profile, clip_duration)
    concepts = lexicon_concepts(
        profile, transcript_words, clip_duration, limit=max(1, 3 * target)
    )
    queries = _concept_broll_queries(profile, concepts, transcript_words)
    return queries or _create_broll_queries(profile, transcript_words, clip_duration)


def _plan_broll_segments(
    profile: dict,
    transcript_words: list[dict],
    clip_duration: float,
    segments: list[dict],
    zoom_keyframes: list[dict],
    overlay_events: list[dict],
    hook_guard_sec: float = BROLL_HOOK_GUARD_SEC,
    concepts: list[dict] | None = None,
    phases: list[dict] | None = None,
    dropped_sink: list[dict] | None = None,
) -> list[dict]:
    """
    Turn the profile's B-roll queries into timed cutaway placements.

    Each entry keeps the query shape from _create_broll_queries and adds
    a start/end window on the clip timeline. The footage itself is
    resolved later, at render time (media/pixabay_broll.py) — this
    module stays offline and deterministic: same inputs, same
    placements.

    Placement is word-anchored (self-run audit issue #1): a cue starts
    where its query word is actually spoken (``spoken_at``), so the
    footage illustrates the words under it — a "car" cutaway lands on
    the sentence about a car, never over unrelated speech. When the
    anchored window is blocked (hook/end guard, punch-in, info card,
    coverage cap, or an earlier cue), the cue falls back to the first
    free segment boundary, exactly as before.

    When ``concepts`` is given (Smart B-Roll v2, Slice 2), the plan's
    concepts are the cue source INSTEAD of the keyword scan: each
    concept places at its (word-snapped) anchor only — a concept
    detached from its clause is a wrong insert waiting to happen, so
    there is no segment-boundary fallback — and two concept cues keep
    at least ``BROLL_FACE_RETURN_SEC`` of speaker between them. With
    ``concepts=None`` every behaviour above is today's, unchanged.

    Slice 5 placement guards (design §5.2), applied to every cue
    placed here, concept or keyword:

    - coverage is per profile: ``broll.max_coverage`` up to the 0.40
      hard ceiling (travel/japan), 0.30 everywhere else;
    - the face-return gap (≥ 4 s of speaker between cues) applies to
      keyword cues as well as concept cues;
    - no cue window may overlap a payoff/conclusion phase (``phases``
      carries the plan's phases; without phases there is nothing to
      consult and the guard is a no-op);
    - no cue may START at or after the last spoken word — the clip's
      final line opens on the face, never under a cutaway — but only
      when the speaker is still talking in the final quarter of the
      clip (a transcript ending earlier leaves an unscored tail, not a
      closing line). (Start-based on purpose: a cue that opened earlier
      and runs over the final word is today's pinned behaviour, e.g.
      the avocado cue in the audit tests — the guard stops cutaways
      from OPENING over the closing line, it does not rewrite cues
      already running.);
    - the first keyword cue prefers a segment boundary at/after
      ``BROLL_FIRST_CUE_SEC`` (soft target; the hook guard stays the
      hard floor). Word anchors and concept anchors are never moved
      for this — the hook + opening punch beat belong to the face, but
      a cutaway's job is to illustrate the words under it;
    - a concept cue runs for its hinted duration (2.5–4.0 s) when the
      concept carries one, else the fixed 3.0 s.
    """
    v3 = broll_uses_v3(profile)
    if concepts is not None:
        queries = _concept_broll_queries(profile, concepts, transcript_words)
    elif v3:
        queries = _lexicon_broll_queries(profile, transcript_words, clip_duration)
    else:
        queries = _create_broll_queries(profile, transcript_words, clip_duration)
    if not queries or clip_duration <= 0:
        return []

    broll = profile.get("broll", {})
    target_count = min(len(queries), broll_target_count(profile, clip_duration))
    # v3 visual protection (transcript-based; the reframe stage's face/text
    # evidence is not available here): no cutaway over a spoken
    # demonstration ("look at this", "this chart"), and a live reaction keeps
    # the face visible — the cue becomes a picture-in-picture.
    demo_spans: list[tuple[float, float]] = []
    reaction_at: list[float] = []
    default_layout = "cutaway"
    if v3:
        from .broll_lexicon import (
            DEMONSTRATION_PHRASES,
            phrase_spans,
            reaction_times,
        )

        demo_spans = phrase_spans(transcript_words, DEMONSTRATION_PHRASES)
        reaction_at = reaction_times(transcript_words)
        default_layout = "pip" if broll.get("layout") == "pip" else "cutaway"

    punch_ins = [
        (
            float(z.get("time", 0)) - _PUNCH_IN_PAD_SEC,
            float(z.get("time", 0)) + float(z.get("duration", 0.5)) + _PUNCH_IN_PAD_SEC,
        )
        for z in zoom_keyframes
        if z.get("type") == "punch_in"
    ]
    cards = [
        (
            float(o.get("time", 0)),
            float(o.get("time", 0)) + float(o.get("duration", 3)),
        )
        for o in overlay_events
        if o.get("type") == "info_card"
    ]

    def _blocked(start: float, end: float) -> bool:
        return any(
            start < block_end and block_start < end
            for block_start, block_end in punch_ins + cards
        )

    placements: list[dict] = []
    covered = 0.0
    # Per-profile coverage (design §5.1): a profile may raise its cap
    # up to the 0.40 ceiling; anything missing, malformed or above the
    # ceiling falls back to the 0.30 default — never silently above it.
    try:
        coverage_frac = float(broll.get("max_coverage", BROLL_MAX_COVERAGE))
    except (TypeError, ValueError):
        coverage_frac = BROLL_MAX_COVERAGE
    if not 0 < coverage_frac <= BROLL_MAX_COVERAGE_CEILING:
        coverage_frac = BROLL_MAX_COVERAGE
    max_coverage = coverage_frac * clip_duration

    # Payoff/conclusion phases (when the caller supplies the plan's
    # phases): no cue window may overlap them — the speaker's face
    # owns the payoff and the closing line. Concept anchors inside
    # them were already dropped at validation; this is the
    # defence-in-depth for a cue whose window RUNS INTO one.
    blocked_phases = [
        (float(p.get("start_offset", 0)), float(p.get("end_offset", 0)))
        for p in (phases or [])
        if isinstance(p, dict) and p.get("intent") in ("payoff", "conclusion")
    ]
    # The clip's final line starts at its last spoken word (the latest
    # word start — word lists are not assumed sorted). No cue may open
    # on or after it. The guard only applies when the speaker is still
    # talking in the final quarter of the clip: a transcript that ends
    # earlier leaves an unscored tail (music, montage, silence), not a
    # closing line — and a cue over that tail illustrates nothing
    # spoken, so there is nothing to protect. Without the quarter
    # qualification a three-word fixture transcript ending at 2 s in a
    # 60 s clip would ban every cue in the clip.
    word_starts = [float(w.get("start", 0) or 0) for w in transcript_words]
    final_line_start = max(word_starts) if word_starts else None
    if (
        final_line_start is None
        or final_line_start < clip_duration * 0.75
    ):
        final_line_start = None

    def _try_place(query: dict, start: float) -> str | None:
        """Place one query at ``start`` when every guard passes.

        Returns None when the cue placed, else the guard that refused
        it — placement drops used to vanish without a trace (a planned
        insert simply never appeared in the export and nothing on the
        record said why), so the caller can stamp the refusal on the
        EDL via ``dropped_sink``.
        """
        nonlocal covered
        start = max(start, hook_guard_sec)
        cue_sec = float(query.get("_duration_sec") or BROLL_CUE_SEC)
        end = min(start + cue_sec, clip_duration - BROLL_END_GUARD_SEC)
        if end - start < BROLL_MIN_CUE_SEC:
            return "cue_too_short"
        if covered + (end - start) > max_coverage:
            return "coverage_cap"
        if _blocked(start, end):
            return "blocked_window"
        if any(
            start < block_end and block_start < end
            for block_start, block_end in blocked_phases
        ):
            return "payoff_phase"
        if final_line_start is not None and start >= final_line_start:
            return "final_line"
        if v3 and any(
            start - 0.5 < span_end and span_start < end + 0.5
            for span_start, span_end in demo_spans
        ):
            return "demonstration"
        if any(
            start < float(p["end"]) and float(p["start"]) < end
            for p in placements
        ):
            return "overlap"
        if any(
            not (
                end + BROLL_FACE_RETURN_SEC <= float(p["start"])
                or start >= float(p["end"]) + BROLL_FACE_RETURN_SEC
            )
            for p in placements
        ):
            # Face-return discipline (every cue): the speaker gets at
            # least BROLL_FACE_RETURN_SEC between cutaways.
            return "face_return"
        entry = dict(query)
        concept_payload = entry.pop("_concept", None)
        entry.pop("_duration_sec", None)
        context = _broll_context(transcript_words, start, end - start)
        entry.update({
            "start": round(start, 3),
            "end": round(end, 3),
            "source_start": 0.0,
            "asset_id": None,
            "provider": "pixabay",
            "context": context,
        })
        if v3:
            reacting = any(start - 0.5 <= t <= end + 0.5 for t in reaction_at)
            entry["layout"] = "pip" if reacting else default_layout
            if reacting:
                entry["layout_reason"] = "live reaction: the face stays visible"
            if concept_payload is not None and concept_payload.get("spoken"):
                # Decision log for the review UI: what the speaker said here.
                entry["spoken"] = concept_payload["spoken"]
            if concept_payload is not None and concept_payload.get("cue_source"):
                entry["cue_source"] = concept_payload["cue_source"]
                if concept_payload.get("lexicon_id"):
                    entry["lexicon_id"] = concept_payload["lexicon_id"]
            elif concept_payload is not None:
                entry["cue_source"] = "plan"
        if concept_payload is not None:
            # PRECEDENCE: the concept's own phrases are what the stock
            # search runs — never the WORD_SENSES-derived string and
            # never the bare trigger noun. The concept, its resolved
            # sense, the full phrase list, the media preference and the
            # avoid list ride on the entry as provenance (and as the
            # concept-aware gate's inputs in a later slice).
            entry.update({
                "concept": concept_payload["concept"],
                "sense": concept_payload["sense"],
                "search_queries": list(concept_payload["search_queries"]),
                "media": concept_payload["media"],
                "avoid": list(concept_payload["avoid"]),
                "search_query": concept_payload["search_queries"][0],
            })
        else:
            helpers = _broll_sense_helpers()
            entry.update({
                # The stock search string: for an ambiguous trigger it
                # carries the indicated sense's spoken context words
                # ("train station"), never just the bare noun a stock
                # library reads one way only. ``query`` itself stays
                # the planned noun — it is the cue's provenance.
                "search_query": (
                    helpers[2](query["query"], context)
                    if helpers
                    else query["query"]
                ),
            })
        placements.append(entry)
        covered += end - start
        return None

    def _record_drop(query: dict, reason: str) -> None:
        if dropped_sink is None:
            return
        concept_payload = query.get("_concept") or {}
        dropped_sink.append({
            "query": query.get("query", ""),
            "concept": concept_payload.get("concept", ""),
            "stage": "placement",
            "reason": reason,
        })

    for query in queries:
        if len(placements) >= target_count:
            _record_drop(query, "density_cap")
            continue
        # 1. Word-anchored: the cue opens on the spoken query word.
        spoken_at = query.get("spoken_at")
        anchor_reason = "no_anchor"
        if spoken_at is not None:
            anchor_reason = _try_place(query, float(spoken_at))
            if anchor_reason is None:
                continue
        if "_concept" in query:
            # Concept cues place at their anchor or not at all: the
            # concept illustrates one specific clause, and a cue moved
            # to a free boundary illustrates nothing.
            _record_drop(query, anchor_reason)
            continue
        # 2. Segment-boundary fallback: first free topic-change slot.
        # For the FIRST cue the boundaries at/after the first-cue
        # target are tried first (design §5.2 soft target — the hook
        # belongs to the face); only if none of those is free does the
        # fallback take an earlier boundary, and the hook guard below
        # stays the hard floor either way.
        boundaries = [float(segment.get("start", 0)) for segment in segments[1:]]
        if not placements:
            boundaries = [b for b in boundaries if b >= BROLL_FIRST_CUE_SEC] + [
                b for b in boundaries if b < BROLL_FIRST_CUE_SEC
            ]
        placed = False
        last_reason = anchor_reason
        for boundary in boundaries:
            last_reason = _try_place(query, boundary)
            if last_reason is None:
                placed = True
                break
        if not placed:
            _record_drop(query, last_reason)
    return placements


def _create_sfx_markers(profile: dict, energy_peaks: list[float]) -> list[dict]:
    """Create SFX markers from user library at energy peaks."""
    audio = profile.get("audio", {})
    if not audio.get("sfx_from_user_library"):
        return []

    sfx = []
    for peak in energy_peaks:
        sfx.append({
            "time": peak,
            "type": "impact",
            "source": "user_library"
        })
    return sfx


# Canonical SFX event names for the renderer's audio finisher. Each name
# resolves by file stem in the user's asset library (assets/sfx/<name>.<ext>)
# at render time, and files that are missing are skipped silently — so
# emitting a name the user has not downloaded yet is safe by design.
SFX_EVENT_NAMES = ("whoosh", "pop", "riser", "ding", "impact", "sting")

# Density caps: SFX punctuate an edit, they never wallpaper it.
_SFX_PEAK_MIN_GAP_SEC = 3.0      # at most one peak hit per 3 seconds
_SFX_PEAK_MAX_EVENTS = 8         # hard cap on peak hits per clip
_SFX_WHOOSH_MIN_GAP_SEC = 4.0    # at most one transition SFX per 4 seconds
_SFX_DING_MIN_GAP_SEC = 4.0
_SFX_DING_MAX_EVENTS = 3
_SFX_STACK_GAP_SEC = 0.75        # two events closer than this count as stacked
_SFX_RISER_LEAD_SEC = 1.2        # riser starts this far ahead of the first peak
_CUT_TRANSITION_NAMES = {"cut", "hard_cut", "none"}


def _join_plan(segments: list[dict]) -> list[dict]:
    """
    Per-join timing for the rendered (output) timeline.

    Mirrors the renderer's join math: a crossfade join overlaps the
    accumulated output by the fade duration, so rendered boundaries sit
    earlier than the raw segment starts. A join counts as a crossfade when
    it carries a positive duration and a non-cut transition name — for every
    transition value the profiles can produce, that is exactly the set the
    renderer's TRANSITION_MAP resolves to an xfade.
    """
    plan = []
    if not segments:
        return plan
    rendered = _segment_render_duration(segments[0])
    for segment in segments[1:]:
        seg_duration = _segment_render_duration(segment)
        requested = max(0.0, float(segment.get("transition_duration", 0) or 0))
        name = str(segment.get("transition_in", "cut")).lower()
        fade = min(requested, rendered, seg_duration) if requested > 0 and name not in _CUT_TRANSITION_NAMES else 0.0
        plan.append({
            "source_boundary": float(segment.get("start", 0)),
            "transition_in": name,
            "fade": fade,
            "rendered_boundary": rendered - fade / 2 if fade else rendered,
        })
        rendered += seg_duration - fade
    return plan


def _to_rendered_time(source_time: float, join_plan: list[dict]) -> float:
    """Map a clip-timeline time to the rendered timeline (crossfades pull
    everything after a join earlier by that join's fade duration)."""
    shift = sum(join["fade"] for join in join_plan if source_time >= join["source_boundary"])
    return source_time - shift


def _rendered_time_in_segments(source_time: float, segments: list[dict]) -> float:
    """Map a clip-timeline time to the rendered timeline, segment-aware.

    The walk ``_join_plan`` does for boundaries, extended inside a
    segment: a speed ramp compresses its own segment's span, and each
    crossfade pulls everything after it earlier by the fade duration.
    Used for point effects (audio flashes) whose centres can sit
    anywhere inside a segment, not just on its joins.
    """
    if not segments:
        return round(float(source_time), 3)
    joins = _join_plan(segments)
    rendered_start = 0.0
    for index, segment in enumerate(segments):
        start = float(segment.get("start", 0))
        end = float(segment.get("end", start))
        speed = segment.get("speed")
        speed = (
            float(speed)
            if isinstance(speed, (int, float)) and speed > 0
            else 1.0
        )
        if source_time <= end or index == len(segments) - 1:
            return round(rendered_start + max(0.0, source_time - start) / speed, 3)
        fade = joins[index]["fade"] if index < len(joins) else 0.0
        # The advance uses the segment's rendered duration (a freeze
        # holds freeze_duration, a ramp (end-start)/speed) — the raw
        # span would short-change every later point by the hold.
        rendered_start += _segment_render_duration(segment) - fade
    return round(rendered_start, 3)


def _create_sfx_events(
    profile: dict,
    segments: list[dict],
    overlay_events: list[dict],
    energy_peaks: list[float],
    clip_duration: float
) -> list[dict]:
    """
    Emit renderer-ready SFX events ({name, at_sec, gain_db}) for a clip.

    Signals: energy peaks (the punchline/emphasis markers that also drive
    punch-in zooms), crossfade transition points, and info-card overlays.
    Gated by the profile's sfx_from_user_library flag; density-capped per
    the constants above. Deterministic: same inputs -> same events.
    """
    audio = profile.get("audio", {})
    if not audio.get("sfx_from_user_library") or clip_duration <= 0:
        return []

    # Learned SFX density (auto_director.learning stamps
    # audio.sfx_density_multiplier onto adjusted profile copies). Absent or
    # 1.0 keeps the shipped caps exactly; the value is clamped to the
    # engine's bounds here too so a hand-built profile cannot exceed them.
    try:
        sfx_multiplier = float(audio.get("sfx_density_multiplier", 1.0))
    except (TypeError, ValueError):
        sfx_multiplier = 1.0
    sfx_multiplier = min(1.5, max(0.5, sfx_multiplier))
    peak_cap = max(0, round(_SFX_PEAK_MAX_EVENTS * sfx_multiplier))
    ding_cap = max(0, round(_SFX_DING_MAX_EVENTS * sfx_multiplier))

    niche = profile.get("niche", "")
    joins = _join_plan(segments)
    events: list[dict] = []
    occupied: list[float] = []

    def _is_free(at_sec: float) -> bool:
        return all(abs(at_sec - taken) >= _SFX_STACK_GAP_SEC for taken in occupied)

    def _add(name: str, at_sec: float, gain_db: float) -> None:
        at_sec = round(min(max(at_sec, 0.0), max(0.0, clip_duration - 0.05)), 3)
        events.append({"name": name, "at_sec": at_sec, "gain_db": gain_db})
        occupied.append(at_sec)

    # Transition SFX: each crossfade join plays the sound its
    # transition type is paired with (signature_transitions.
    # TRANSITION_SFX_MAP) — a wipe whooshes at the level crossfades
    # have always played, a fade swells more softly, a pixelize pops.
    # A transition with no pairing stays silent: no sound is invented
    # for it (this also covers names the renderer itself would cut).
    last_transition_sfx = float("-inf")
    for join in joins:
        if join["fade"] <= 0:
            continue
        pairing = transition_sfx(join["transition_in"])
        if pairing is None:
            continue
        sfx_name, sfx_gain = pairing
        at_sec = join["rendered_boundary"]
        if at_sec - last_transition_sfx < _SFX_WHOOSH_MIN_GAP_SEC or not _is_free(at_sec):
            continue
        _add(sfx_name, at_sec, sfx_gain)
        last_transition_sfx = at_sec

    # Energy peaks. True crime stays near-dry by design: a single low sting
    # on the final reveal peak, nothing else.
    peaks = sorted(p for p in energy_peaks if 0 <= p <= clip_duration)
    if niche == "true_crime":
        if peaks:
            at_sec = _to_rendered_time(peaks[-1], joins)
            if _is_free(at_sec):
                _add("sting", at_sec, -3.0)
    elif peaks:
        chosen: list[float] = []
        last_peak = float("-inf")
        for peak in peaks:
            if peak - last_peak >= _SFX_PEAK_MIN_GAP_SEC:
                chosen.append(peak)
                last_peak = peak
            if len(chosen) >= peak_cap:
                break
        # A riser builds into the first peak when there is room for it.
        if chosen and chosen[0] >= _SFX_RISER_LEAD_SEC + 0.3:
            at_sec = _to_rendered_time(chosen[0], joins) - _SFX_RISER_LEAD_SEC
            if at_sec >= 0 and _is_free(at_sec):
                _add("riser", at_sec, -6.0)
        for index, peak in enumerate(chosen):
            at_sec = _to_rendered_time(peak, joins)
            if not _is_free(at_sec):
                continue
            if index == 0:
                _add("impact", at_sec, 0.0)
            else:
                _add("pop", at_sec, -3.0)

    # A soft ding punctuates info cards (verbatim numbers/quotes).
    last_ding = float("-inf")
    ding_count = 0
    cards = sorted(
        (o for o in overlay_events if o.get("type") == "info_card"),
        key=lambda o: float(o.get("time", 0)),
    )
    for card in cards:
        if ding_count >= ding_cap:
            break
        card_time = float(card.get("time", 0))
        if card_time - last_ding < _SFX_DING_MIN_GAP_SEC:
            continue
        at_sec = _to_rendered_time(card_time, joins)
        if not _is_free(at_sec):
            continue
        _add("ding", at_sec, -6.0)
        last_ding = card_time
        ding_count += 1

    events.sort(key=lambda e: e["at_sec"])
    return events


# --- Transition discipline -------------------------------------------------
#
# Named by Luffy's complaint (2026-11-15): a gradual slide sat in the
# middle of a sentence, and every clip carried the same transition.
# EDL segments are synthetic divisions, not detected scene boundaries,
# so a decorative join is only honest where the speech itself breaks:
# a real pause at the join, or a topic boundary the planner detected
# (its phase starts). A join inside continuous speech is a hard cut,
# and the same decorative transition never lands twice in a row.

#: A pause at least this long at a join counts as a break in speech.
SPEECH_GAP_SEC = 0.5
#: A join this close to a planner phase start counts as sitting on the
#: boundary the planner detected.
BOUNDARY_ALIGN_SEC = 0.75


def speech_gap_at(transcript_words: list[dict], at: float) -> float:
    """Seconds of silence straddling ``at`` in the word timings.

    0.0 when a word is being spoken across the moment (or the moment
    sits within SPEECH_GAP_SEC of speech on both sides is NOT assumed —
    the measured gap is returned as-is): the gap between the last word
    ending at/before ``at`` and the first word starting after it. With
    no words on one side (clip edge), the gap is unbounded — speech
    cannot be continuous past the words there are.
    """
    prev_end: float | None = None
    next_start: float | None = None
    for word in transcript_words or []:
        try:
            start = float(word.get("start", 0) or 0)
            end = float(word.get("end", start) or start)
        except (TypeError, ValueError):
            continue
        if start <= at < end:
            return 0.0
        if end <= at and (prev_end is None or end > prev_end):
            prev_end = end
        if start >= at and (next_start is None or start < next_start):
            next_start = start
    if prev_end is None or next_start is None:
        return float("inf")
    return max(0.0, next_start - prev_end)


def join_in_continuous_speech(
    transcript_words: list[dict], at: float, boundary_times: tuple = ()
) -> bool:
    """True when a decorative transition at ``at`` would cut mid-speech.

    A planner-detected boundary (a phase start within
    BOUNDARY_ALIGN_SEC) excuses the join: the planner already decided
    the topic turns there. Without word timings there is nothing to
    judge against and the join is left alone.
    """
    if any(abs(at - float(boundary)) <= BOUNDARY_ALIGN_SEC for boundary in boundary_times):
        return False
    if not transcript_words:
        return False
    return speech_gap_at(transcript_words, at) < SPEECH_GAP_SEC


def enforce_transition_discipline(
    segments: list[dict],
    transcript_words: list[dict],
    boundary_times: tuple = (),
    only_indices: set[int] | None = None,
) -> list[dict]:
    """Downgrade dishonest decorative joins to hard cuts, in place.

    Two rules, applied in join order:
    - a non-cut join inside continuous speech becomes a hard cut;
    - a non-cut join repeating the previous candidate join's
      transition on the immediately preceding join becomes a hard
      cut (no identical transition back-to-back).

    ``only_indices`` scopes the judgement to the joins a decision
    actually made — the signature join, or the joins a plan's
    transition choices changed. The profile's declared default,
    repeated on every join by the niche's own grammar, is NOT a
    per-join decision and is never judged here: changing a profile's
    grammar is a product-design change, not a discipline fix.

    Returns the list of downgrades ({segment_index, from, reason}) so
    the caller can stamp them on the EDL — a suppressed transition is
    a decision the record should show, exactly like a placed one.
    """
    notes: list[dict] = []
    previous: tuple[int, str] | None = None
    for position, segment in enumerate(segments):
        if position == 0:
            continue
        if only_indices is not None and position not in only_indices:
            continue
        name = str(segment.get("transition_in", "cut")).lower()
        if name in _CUT_TRANSITION_NAMES:
            previous = None
            continue
        join_at = float(segment.get("start", 0) or 0)
        reason = None
        if join_in_continuous_speech(transcript_words, join_at, boundary_times):
            reason = "continuous_speech"
        elif (
            previous is not None
            and previous[0] == position - 1
            and previous[1] == name
        ):
            reason = "repeated_transition"
        if reason is not None:
            segment["transition_in"] = "cut"
            segment["transition_duration"] = 0
            notes.append({
                "segment_index": position,
                "from": name,
                "reason": reason,
            })
            previous = None
        else:
            previous = (position, name)
    return notes


# --- Opening hook treatment --------------------------------------------------
#
# The first seconds decide whether a viewer stays, and the EDL builder
# gave them nothing: punch-ins fire only at energy peaks (the pipeline
# passes none), speaker punches are dialogue-only, and the plan's own
# opening punch exists only when an LLM plan chose one. Every Auto
# clip now opens with a gentle push-in — a scale ramp over the first
# OPENING_RAMP_SEC, built on the renderer's existing ken-burns ramp
# machinery, centred on the segment's (face-tracked) reframe crop.
# Plain never sees it: plainify_edl empties zoom_keyframes wholesale.

OPENING_RAMP_SEC = 2.0
OPENING_RAMP_SCALE = 1.12
OPENING_RAMP_MIN_CLIP_SEC = 4.0


def _opening_ramp_keyframe(clip_duration: float) -> dict:
    return {
        "type": "ken_burns",
        "trigger": "opening_punch",
        "start": 0.0,
        "end": min(OPENING_RAMP_SEC, clip_duration),
        "scale_start": 1.0,
        "scale_end": OPENING_RAMP_SCALE,
    }


def _apply_opening_ramp(
    zoom_keyframes: list[dict], clip_duration: float
) -> list[dict]:
    """Add the opening push-in unless the opening is already treated.

    Skipped when the clip is too short for a ramp to read, when a
    ken-burns drift already covers the opening (travel profiles — a
    second ramp would compound the zoom), or when a punch-in already
    lands inside the ramp window (an energy-peak or plan punch owns
    that moment).
    """
    if clip_duration < OPENING_RAMP_MIN_CLIP_SEC:
        return zoom_keyframes
    # HookFinish v2 (media.hook_v2) owns the opening while its switch
    # is on: its settle-zoom replaces this push-in — two opening zooms
    # must not compound. With the switch off this changes nothing.
    from ..media.hook_v2 import hook_finish_v2_enabled

    if hook_finish_v2_enabled():
        return zoom_keyframes
    for keyframe in zoom_keyframes:
        if keyframe.get("type") == "ken_burns" and float(
            keyframe.get("start", 0) or 0
        ) < OPENING_RAMP_SEC:
            return zoom_keyframes
        if keyframe.get("type") == "punch_in" and float(
            keyframe.get("time", 0) or 0
        ) < OPENING_RAMP_SEC:
            return zoom_keyframes
    return zoom_keyframes + [_opening_ramp_keyframe(clip_duration)]


def build_edl(
    clip: dict[str, Any],
    transcript_words: list[dict[str, Any]],
    profile: dict[str, Any],
    energy_peaks: list[float],
    beat_grid: dict[str, Any] | None = None,
    flash_grid: dict[str, Any] | None = None,
    broll_enabled: bool = True
) -> dict[str, Any]:
    """
    Build an Edit Decision List (EDL) for a clip.

    Args:
        clip: Dict with at least 'start', 'end', 'duration' keys
        transcript_words: List of word dicts with 'word', 'start', 'end' keys
        profile: Niche profile dict from load_profile()
        energy_peaks: List of timestamps (seconds) for energy peaks
        beat_grid: Optional clip-relative beat grid (auto_director.
            beat_grid.prepare_clip_grid). Only profiles carrying an
            enabled ``beat_grid`` section consume it — boundaries snap
            to beats, the drop anchors the payoff, speed ramps are
            assigned — and every other profile's EDL is exactly what
            it was without the grid.
        flash_grid: Optional clip-relative level grid for profiles
            that opt into audio-reactive flashes without a beat grid
            (auto_director.audio_flashes.resolve_flash_grid). When a
            beat grid is present it is the flash signal instead — it
            already describes the audio that will be heard. Profiles
            that do not opt in ignore both grids for this purpose.

    Returns:
        EDL dict with segments, transitions, zooms, captions, overlays, broll, sfx, sfx_events, audio, color, hook
    """
    clip_duration = clip.get("duration", clip.get("end", 0) - clip.get("start", 0))

    pacing = profile.get("pacing", {})
    transitions = profile.get("transitions", {})
    captions = profile.get("captions", {})
    audio = profile.get("audio", {})
    color = profile.get("color", {})
    hook = profile.get("hook", {})

    # Editorial restraint (auto_director.content_cuts): cut only at a
    # source shot change or a sentence end, the cuts-per-minute target
    # being a ceiling. EDL_CONTENT_CUTS=off restores the quota slicing.
    from .content_cuts import (
        content_cuts_enabled,
        decorative_grid,
        plan_content_segments,
        restrain_zooms,
        signature_on_shot_change,
    )

    content_mode = content_cuts_enabled()
    restraint: dict[str, Any] = {}

    # Build segments based on target cuts per minute
    target_cuts = pacing.get("target_cuts_per_min", [5, 8])
    avg_cuts = sum(target_cuts) / 2
    num_segments = max(1, int(clip_duration / 60 * avg_cuts))

    segments = []
    segment_duration = clip_duration / num_segments

    for i in range(num_segments):
        seg_start = i * segment_duration
        seg_end = min((i + 1) * segment_duration, clip_duration)

        # Transition in
        if i == 0:
            trans_in = "none"
            trans_dur = 0
        else:
            trans_in = transitions.get("default", "cut")
            trans_dur = transitions.get("duration_sec", 0)

        segments.append({
            "index": i,
            "start": seg_start,
            "end": seg_end,
            "duration": seg_end - seg_start,
            "transition_in": trans_in,
            "transition_duration": trans_dur
        })

    if content_mode:
        segments, cut_notes = plan_content_segments(
            profile, clip_duration, transcript_words, clip.get("scene_cuts")
        )
        restraint["cuts"] = cut_notes
        restraint["segments"] = len(segments)

    # Beat-grid consumption (opt-in profiles only): snap boundaries to
    # beats, anchor the drop, quantise the payoff, assign speed ramps.
    beat_cfg = profile_beat_config(profile)
    if content_mode and decorative_grid(beat_cfg):
        # The grid follows the music bed the app adds: snapping cuts,
        # punching and flashing to it edits to decoration, not footage.
        restraint["beat_grid"] = "skipped: grid follows the added music bed"
        beat_cfg = None
        beat_grid = None
    beat_info = None
    if beat_cfg is not None and beat_grid is not None:
        segments, beat_info = consume_beat_grid(
            segments, beat_grid, beat_cfg, profile, clip_duration
        )

    # Signature transition (Viral Scout Pack): when the profile
    # declares one, it lands on the first suitable join inside the
    # 3-5s window and decorative joins before it are downgraded, so
    # the opening has exactly one decorative moment. Runs after the
    # beat grid (which may have moved the boundaries) and before SFX
    # emission, so the signature join's mapped SFX is emitted by the
    # normal transition-SFX pass. Profiles without a declared
    # signature are untouched.
    # Snapshot the joins before the signature lands: the signature is
    # a per-join DECISION, so transition discipline judges it (below),
    # and a suppressed signature restores exactly what the profile's
    # grammar had there.
    pre_signature_joins = [
        (segment.get("transition_in"), segment.get("transition_duration"))
        for segment in segments
    ]
    signature_info = apply_signature_transition(
        segments, profile, clip_duration
    )
    if (
        content_mode
        and signature_info is not None
        and not signature_on_shot_change(segments, int(signature_info["segment_index"]))
    ):
        index = int(signature_info["segment_index"])
        name, duration = pre_signature_joins[index]
        segments[index]["transition_in"] = name
        segments[index]["transition_duration"] = duration
        restraint["signature"] = "suppressed: not on a source shot change"
        signature_info = None

    # Transition discipline for the signature: a slide planted in the
    # middle of a sentence is the exact defect Luffy flagged, so the
    # signature survives only where the speech itself breaks. A
    # suppressed signature loses its stamp too — the record must not
    # claim a transition the render will not show.
    transition_notes: list[dict] = []
    if signature_info is not None:
        signature_index = int(signature_info["segment_index"])
        transition_notes = enforce_transition_discipline(
            segments,
            transcript_words,
            only_indices={signature_index},
        )
        if transition_notes:
            name, duration = pre_signature_joins[signature_index]
            segments[signature_index]["transition_in"] = name
            segments[signature_index]["transition_duration"] = duration
            signature_info = None

    # Scene rules (additive profile section; absent = today's behaviour):
    # the travel establishing guard protects the opening context shot,
    # and dialogue profiles punch in on speaker changes.
    establishing_sec = establishing_guard_sec(profile, clip_duration)

    # Zoom keyframes
    zoom_keyframes = _create_zoom_keyframes(profile, energy_peaks, clip_duration)

    # Re-hook punches for fast-pace profiles (interval-driven, capped).
    if beat_cfg is not None:
        zoom_keyframes = zoom_keyframes + _create_rehook_punches(
            profile, segments, zoom_keyframes, clip_duration
        )

    # Speaker-change punches for dialogue profiles (diarization-gated).
    zoom_keyframes = zoom_keyframes + _create_speaker_punches(
        profile, transcript_words, zoom_keyframes, clip_duration
    )

    # Overlay events
    overlay_events = _create_overlay_events(profile, transcript_words, clip_duration)

    # B-roll placements (timed query slots; footage resolves at render).
    # The establishing guard extends the hook guard for travel profiles:
    # no cutaway lands inside the protected opening shot either.
    # The per-task B-roll switch sits ABOVE this planning: when the
    # caller resolved it Off (auto_director.broll_setting), no cues are
    # planned at all, so nothing downstream can resolve or composite a
    # cutaway for the task and no stock provider is ever called.
    broll_dropped: list[dict] = []
    if broll_enabled:
        broll_queries = _plan_broll_segments(
            profile, transcript_words, clip_duration, segments, zoom_keyframes,
            overlay_events,
            hook_guard_sec=max(BROLL_HOOK_GUARD_SEC, establishing_sec),
            dropped_sink=broll_dropped,
        )
    else:
        broll_queries = []

    # SFX markers
    sfx_markers = _create_sfx_markers(profile, energy_peaks)

    # Renderer-ready SFX events (consumed by the audio finisher)
    sfx_events = _create_sfx_events(profile, segments, overlay_events, energy_peaks, clip_duration)

    # Establishing-shot guard: the protected opening plays clean — no
    # punch-in zooms and no SFX accents inside it. Ken Burns is exempt
    # (the slow drift is the establishing move itself).
    if establishing_sec > 0:
        zoom_keyframes = [
            z for z in zoom_keyframes
            if z.get("type") != "punch_in"
            or float(z.get("time", 0)) >= establishing_sec
        ]
        sfx_events = [
            e for e in sfx_events if float(e.get("at_sec", 0)) >= establishing_sec
        ]
        sfx_markers = [
            m for m in sfx_markers if float(m.get("time", 0)) >= establishing_sec
        ]

    # Opening hook treatment: a gentle push-in over the first seconds
    # of every Auto clip (see _apply_opening_ramp). Runs after the
    # establishing filter; the ramp is a ken-burns keyframe, which the
    # guard exempts by design, and Plain strips all keyframes anyway.
    if not content_mode or (profile.get("zooms") or {}).get("opening_ramp"):
        zoom_keyframes = _apply_opening_ramp(zoom_keyframes, clip_duration)
    if content_mode:
        zoom_keyframes, zooms_dropped = restrain_zooms(
            zoom_keyframes, transcript_words, clip_duration
        )
        if zooms_dropped:
            restraint["zooms_dropped"] = zooms_dropped

    # Persistent hook title (user request, 2026-11-09): the title stays on
    # screen for the WHOLE clip by default. A shorter on-screen duration
    # applies only when the profile records it as an explicit choice
    # (hook.title_seconds_explicit = true — e.g. a reference style whose
    # title duration was genuinely measured shorter, or a user-set style).
    # Stock profile title_seconds values are nominal pacing hints and
    # never cap the title silently.
    _raw_title_seconds = hook.get("title_seconds")
    _title_explicit = (
        bool(hook.get("title_seconds_explicit")) and _raw_title_seconds is not None
    )
    resolved_title_seconds = (
        float(_raw_title_seconds) if _title_explicit else float(clip_duration)
    )

    edl = {
        "clip_id": clip.get("id", "unknown"),
        "duration": clip_duration,
        "segments": segments,
        "zoom_keyframes": zoom_keyframes,
        "captions": {
            "preset_id": captions.get("preset_id", "default"),
            "words_per_line": captions.get("words_per_line", 3),
            "position": captions.get("position", "bottom")
        },
        "overlays": overlay_events,
        "broll": broll_queries,
        "sfx": sfx_markers,
        "sfx_events": sfx_events,
        "audio": {
            "music_mood": audio.get("music_mood", "none"),
            "music_volume": audio.get("music_volume_0_to_1", 0.0),
            "ducking_db": audio.get("ducking_db", 0.0)
        },
        "color": {
            "lut": color.get("lut", "none")
        },
        "hook": {
            "title_style": hook.get("title_style", "statement"),
            "title_seconds": resolved_title_seconds,
            "title_persistent": not _title_explicit
        }
    }
    if audio.get("sfx_pack"):
        # The profile's SFX pack (assets/sfx/library/<pack>/<cue>): the
        # renderer draws each cue from the pack's variants first.
        edl["audio"]["sfx_pack"] = str(audio["sfx_pack"])
    if broll_dropped:
        # Planned inserts that placement refused, with the guard that
        # refused each — a missing cutaway must be explainable from
        # the record, never a silent absence.
        edl["broll_dropped"] = broll_dropped
    if transition_notes:
        edl["transition_discipline"] = transition_notes
    scene_info: dict[str, Any] = {}
    if establishing_sec > 0:
        scene_info["establishing_shot_sec"] = establishing_sec
    speaker_punch_count = sum(
        1 for z in zoom_keyframes if z.get("trigger") == "speaker_change"
    )
    if speaker_punch_count:
        scene_info["speaker_punches"] = speaker_punch_count
    if scene_info:
        edl["scene_rules"] = scene_info
    if beat_info is not None:
        edl["beat_grid"] = beat_info
    if signature_info is not None:
        # Signature provenance, with the rendered time of the chosen
        # join (crossfades pull rendered boundaries earlier than the
        # source boundary; consumers like QC read this, not at_sec).
        joins = _join_plan(segments)
        join_index = signature_info["segment_index"] - 1
        if 0 <= join_index < len(joins):
            rendered_at = joins[join_index]["rendered_boundary"]
        else:
            rendered_at = signature_info["at_sec"]
        edl["signature_transition"] = {
            **signature_info,
            "rendered_at_sec": round(float(rendered_at), 3),
        }
    # Audio-reactive flashes (Viral Scout Pack item 3): the profile
    # opts in via effects.audio_flashes and the clip's level grid —
    # the beat grid when one was resolved, else the flash grid —
    # names its loudest hits. Points carry rendered times (speed
    # ramps and crossfades move them off the clip timeline) for the
    # renderer's timed eq pulses. The establishing guard applies the
    # same law as punch-ins and SFX: the protected opening plays
    # clean, so a flash inside it is dropped, never moved.
    flash_plan = plan_audio_flashes(
        profile,
        beat_grid if beat_grid is not None else flash_grid,
        clip_duration,
    )
    if flash_plan is not None:
        points = [
            {
                **point,
                "rendered_at_sec": _rendered_time_in_segments(
                    point["at_sec"], segments
                ),
            }
            for point in flash_plan["points"]
            if float(point["at_sec"]) >= establishing_sec
        ]
        if points:
            edl["audio_flashes"] = {**flash_plan, "points": points}
    if content_mode:
        edl["edit_restraint"] = restraint
    look_override = captions.get("look_override")
    if isinstance(look_override, dict) and look_override:
        # Reference Style caption look: a partial caption template the
        # render path merges under the Brand Kit (precedence: task >
        # Brand Kit > reference look > stock preset). Profiles without
        # a measured look stamp nothing, so their EDLs are unchanged.
        edl["reference_look_override"] = dict(look_override)
    return edl