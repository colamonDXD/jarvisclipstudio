"""EDL (Edit Decision List) builder for auto-director."""

from pathlib import Path
from typing import Any
import json
import re

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
    Load a niche profile from the profiles directory.

    Args:
        niche: The niche name (e.g., "podcast_news", "travel_vlog")

    Returns:
        The profile dict

    Raises:
        ValueError: If niche is unknown
        FileNotFoundError: If profile file doesn't exist
    """
    if niche not in _NICHES:
        raise ValueError(f"Unknown niche: '{niche}'. Valid niches: {sorted(_NICHES)}")

    profile_path = PROFILES_DIR / f"{niche}.json"
    if not profile_path.exists():
        raise FileNotFoundError(f"Profile not found: {profile_path}")

    with open(profile_path, "r", encoding="utf-8") as f:
        return json.load(f)


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

    # Extract key nouns/concepts from transcript for B-roll queries
    text = " ".join(w["word"] for w in transcript_words).lower()
    # Simple noun extraction (in production, would use NER)
    common_nouns = [
        "city", "mountain", "beach", "office", "computer", "phone",
        "car", "building", "street", "park", "restaurant", "hotel",
        "chart", "graph", "data", "money", "stock", "market",
        "game", "player", "screen", "controller", "keyboard"
    ]

    queries = []
    words_per_query = max(1, int(60 / density))  # spread across clip
    for noun in common_nouns:
        if noun in text:
            queries.append({
                "query": noun,
                "source": source,  # "pexels" or "user_assets"
                "url": "",  # empty - no web scraping in this module
                "local_path": ""  # for user_assets
            })

    return queries[:max(1, int(clip_duration / 60 * density))]


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


def build_edl(
    clip: dict[str, Any],
    transcript_words: list[dict[str, Any]],
    profile: dict[str, Any],
    energy_peaks: list[float]
) -> dict[str, Any]:
    """
    Build an Edit Decision List (EDL) for a clip.

    Args:
        clip: Dict with at least 'start', 'end', 'duration' keys
        transcript_words: List of word dicts with 'word', 'start', 'end' keys
        profile: Niche profile dict from load_profile()
        energy_peaks: List of timestamps (seconds) for energy peaks

    Returns:
        EDL dict with segments, transitions, zooms, captions, overlays, broll, sfx, audio, color, hook
    """
    clip_duration = clip.get("duration", clip.get("end", 0) - clip.get("start", 0))

    pacing = profile.get("pacing", {})
    transitions = profile.get("transitions", {})
    captions = profile.get("captions", {})
    audio = profile.get("audio", {})
    color = profile.get("color", {})
    hook = profile.get("hook", {})

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

    # Zoom keyframes
    zoom_keyframes = _create_zoom_keyframes(profile, energy_peaks, clip_duration)

    # Overlay events
    overlay_events = _create_overlay_events(profile, transcript_words, clip_duration)

    # B-roll queries
    broll_queries = _create_broll_queries(profile, transcript_words, clip_duration)

    # SFX markers
    sfx_markers = _create_sfx_markers(profile, energy_peaks)

    return {
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
            "title_seconds": hook.get("title_seconds", 3.0)
        }
    }