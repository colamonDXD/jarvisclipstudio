"""N1 (E1 transitions): on_topic_change / on_punchline are director
guidance only, never stamped onto EDL joins.

Decision record: ~/workspace/research_notes/sprint-7day/N1_TRANSITIONS_FIX.md

Only transitions.default + duration_sec reach segment joins in build_edl.
The two other fields are measured and stored (reference_style.build_profile
and the stock profiles), and both are handed to the AI director in the plan
prompt (plan_service._profile_summary), but no per-join topic-change or
punchline signal exists at build_edl time: segments are uniform pacing
cuts, a speaker change is not a topic change, and energy peaks are
emphasis markers, not punchlines. "zoom_bounce" (meme_comedy's
on_punchline, and the reference-style derivation for fast-cut styles) is
not a renderer transition name at all: the renderer degrades unknown names
to a hard cut, so stamping it would silently do nothing.

These tests pin that narrowed claim so a future change cannot silently
re-present the fields as applied transitions, and cannot stamp the
unrenderable "zoom_bounce" onto a join.
"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director.edl import build_edl, load_profile
from src.auto_director.plan_service import _profile_summary
from src.media.edl_render import TRANSITION_MAP, _join_fades

WORDS = [
    {"word": "The", "start": 0.5, "end": 0.6},
    {"word": "truth", "start": 0.6, "end": 0.8},
    {"word": "is", "start": 0.8, "end": 0.9},
    {"word": "we", "start": 0.9, "end": 1.0},
    {"word": "need", "start": 1.0, "end": 1.2},
]


def _profile_without_signature(niche):
    """A stock profile with the signature transition removed, so the
    signature join cannot mask what build_edl itself stamps."""
    profile = copy.deepcopy(load_profile(niche))
    profile.get("transitions", {}).pop("signature", None)
    return profile


def _edl(niche):
    profile = _profile_without_signature(niche)
    clip = {"id": "clip-n1", "start": 0, "end": 30, "duration": 30}
    return profile, build_edl(clip, WORDS, profile, [])


def test_build_edl_never_stamps_on_topic_change():
    """Profiles whose on_topic_change differs from default still stamp
    only the default on every join (education: fade vs cut, finance:
    slide vs hard_cut)."""
    for niche, on_topic in [
        ("education_tutorial", "fade"),
        ("finance_business", "slide"),
    ]:
        profile, edl = _edl(niche)
        transitions = profile["transitions"]
        assert transitions["on_topic_change"] == on_topic
        assert transitions["on_topic_change"] != transitions["default"]
        assert len(edl["segments"]) >= 3
        for i, seg in enumerate(edl["segments"]):
            if i == 0:
                assert seg["transition_in"] == "none"
            else:
                assert seg["transition_in"] == transitions["default"]
                assert seg["transition_duration"] == transitions["duration_sec"]
                assert seg["transition_in"] != on_topic


def test_build_edl_never_stamps_on_punchline_zoom_bounce():
    """meme_comedy's on_punchline is "zoom_bounce", which is not a
    renderer transition: no join may carry it, every join is the
    profile default."""
    profile, edl = _edl("meme_comedy")
    transitions = profile["transitions"]
    assert transitions["on_punchline"] == "zoom_bounce"
    assert transitions["on_punchline"] != transitions["default"]
    assert "zoom_bounce" not in TRANSITION_MAP
    assert len(edl["segments"]) >= 3
    for i, seg in enumerate(edl["segments"]):
        if i == 0:
            assert seg["transition_in"] == "none"
        else:
            assert seg["transition_in"] == transitions["default"]
            assert seg["transition_in"] != "zoom_bounce"


def test_zoom_bounce_join_degrades_to_hard_cut_in_renderer():
    """A join stamped "zoom_bounce" would get a zero fade from the
    renderer (TRANSITION_MAP.get returns None), i.e. a plain concat
    hard cut: the value cannot express a transition, which is why
    build_edl must never stamp it."""
    segments = [
        {"start": 0, "end": 5, "duration": 5},
        {
            "start": 5,
            "end": 10,
            "duration": 5,
            "transition_in": "zoom_bounce",
            "transition_duration": 0.4,
        },
    ]
    assert _join_fades(segments, 10) == [0.0]


def test_profile_summary_hands_both_fields_to_the_director():
    """Both measured fields reach the AI director in the plan prompt:
    on_punchline (as before) and on_topic_change (N1: previously
    fully dead, now the same prompt-level consumer)."""
    education = _profile_summary(load_profile("education_tutorial"))
    assert "transition on punchline: cut" in education
    assert "transition on topic change: fade" in education
    meme = _profile_summary(load_profile("meme_comedy"))
    assert "transition on punchline: zoom_bounce" in meme
    assert "transition on topic change: cut" in meme
