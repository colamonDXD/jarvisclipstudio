"""Trend-Radar D17: the trailer_breakdown selection template.

D17 adds ``trailer_breakdown`` as an AVAILABLE hook title style for
gaming (never a default) and as a selection template: 2–3 s of
trailer play, a freeze on a background detail the frame itself
offers, a fast punch-zoom, a red circle on that detail, and 2–3 s
of face-cam reaction; 4–6 beats in a 30–60 s clip; the hook is the
formula "You missed this in the first 3 seconds…" and the clip
ends on a cliffhanger question naming the last detail.

The evidence is practitioner-only (HookMafia, crawled 15–16 Nov
2026; no view counts) and the trailer window it rides on is a
rumour, not an announcement — no test here measures or claims any
audience outcome. What is pinned is the editorial shape: a detail
may only be circled when the frozen frame itself offers it (a
legible text line, a detected face, a bright region), and the
template declines — to the profile's own default, with the reason
recorded — whenever a moment cannot carry it honestly.
"""

import copy

from src.auto_director import hook_variants as hv
from src.auto_director.edl import load_profile
from src.auto_director.hook_title_styles import (
    HOOK_TITLE_STYLES,
    TRAILER_BREAKDOWN_PROFILES,
    TRAILER_BREAKDOWN_TITLE_STYLE,
    opening_strategy_for_title_style,
    title_matches_style,
    title_style_guidance,
    trailer_breakdown_available,
)
from src.auto_director.plan_service import _profile_summary
from src.auto_director.trailer_breakdown import (
    DETAIL_REVEAL_TRIGGER,
    HOOK_FORMULA,
    DetailSpot,
    append_trailer_breakdown_overlay,
    build_breakdown_plan,
    build_cliffhanger,
    find_detail_spots,
    plan_to_edl_segments,
    resolve_trailer_breakdown_style,
    trailer_breakdown_eligibility,
    trailer_breakdown_template_available,
    trailer_breakdown_ass_events,
)
from src.media.ffmpeg import output_offsets_for_ranges

# --- Fixtures: one trailer-reaction moment, 36 s of source -------------------
# Trailer windows [2,5) and [10,13); reaction windows [5,8) and
# [13,16), both face-cam. The freeze at the end of each trailer
# window offers a grounded detail: on-screen text "DOOM" at 5.0 s,
# a background figure at 13.0 s.

TRAILERS = [{"start": 2.0, "end": 5.0}, {"start": 10.0, "end": 13.0}]
REACTIONS = [
    {"start": 5.0, "end": 8.0, "face_present": True},
    {"start": 13.0, "end": 16.0, "face_present": True},
]
SPOTS = {
    5.0: [
        DetailSpot(
            kind="text", box=(0.55, 0.30, 0.75, 0.42), label="DOOM", score=0.9
        )
    ],
    13.0: [
        DetailSpot(
            kind="face",
            box=(0.60, 0.25, 0.72, 0.45),
            label="figure in the background",
            score=0.8,
        )
    ],
}
DURATION = 36.0


def test_style_is_registered_additively():
    assert TRAILER_BREAKDOWN_TITLE_STYLE in HOOK_TITLE_STYLES
    assert set(HOOK_TITLE_STYLES) == {
        "statement", "question", "statistic", "contrast", "story",
        "problem", "curiosity_gap", "trailer_breakdown",
        # Trend-Radar D16/D15 (2026-11-16): registered additively by
        # their own lanes, merged before this one — the union set.
        "mistake_fix", "list_fill",
    }
    # Every pre-existing style is still registered, untouched.
    for style in ("statement", "question", "statistic", "contrast", "story"):
        assert style in HOOK_TITLE_STYLES


def test_guidance_and_opening_mapping():
    guidance = title_style_guidance("trailer_breakdown")
    assert guidance
    assert "segment" in guidance  # grounded in the segment, always
    assert "detail" in guidance
    assert opening_strategy_for_title_style("trailer_breakdown") == (
        "strongest_statement"
    )
    assert hv.strategy_for_title_style("trailer_breakdown") in hv.STRATEGY_KINDS


def test_marker_requires_the_hook_formula():
    assert title_matches_style(HOOK_FORMULA, "trailer_breakdown")
    assert title_matches_style(
        "You missed this detail in the first 3 seconds", "trailer_breakdown"
    )
    assert title_matches_style(
        "Did you catch the figure in the background?", "trailer_breakdown"
    )
    assert not title_matches_style("The trailer looks amazing", "trailer_breakdown")
    assert not title_matches_style("", "trailer_breakdown")


def test_gaming_profile_offers_but_does_not_default_it():
    profile = load_profile("gaming")
    assert profile["hook"]["title_style"] == "statement"
    assert "trailer_breakdown" in profile["hook"]["available_title_styles"]
    assert profile["selection_templates"] == ["trailer_breakdown"]
    assert trailer_breakdown_available(profile) is True
    assert trailer_breakdown_template_available(profile) is True
    # The profile's own punch-in trigger is untouched: the
    # template's detail-reveal punches carry their own trigger.
    assert profile["zooms"]["punch_in_trigger"] == "action"
    # Other profiles do not offer it.
    assert trailer_breakdown_available(load_profile("finance_business")) is False
    assert trailer_breakdown_template_available(
        load_profile("finance_business")
    ) is False
    # A profile without the lists falls back to the D17 assignment.
    assert trailer_breakdown_available({"niche": "gaming", "hook": {}}) is True
    assert trailer_breakdown_available({"niche": "podcast_news", "hook": {}}) is False


def test_guidance_reaches_the_planner_summary():
    profile = copy.deepcopy(load_profile("gaming"))
    profile["hook"]["title_style"] = "trailer_breakdown"
    summary = _profile_summary(profile)
    assert "trailer_breakdown" in summary


def test_detail_spots_text_beats_face_beats_bright():
    text_first = find_detail_spots(
        {
            "faces": [{"box": (0.1, 0.1, 0.3, 0.4), "score": 0.9}],
            "text_lines": [{"text": "DOOM", "box": (0.55, 0.30, 0.75, 0.42)}],
            "bright_regions": [{"box": (0.2, 0.2, 0.4, 0.5), "coverage": 0.3}],
        }
    )
    assert len(text_first) == 1
    assert text_first[0].kind == "text"
    assert text_first[0].label == "DOOM"

    face_only = find_detail_spots(
        {"faces": [{"box": (0.60, 0.25, 0.72, 0.45), "score": 0.8}]}
    )
    assert len(face_only) == 1
    assert face_only[0].kind == "face"
    assert face_only[0].label == "figure in the background"

    bright_only = find_detail_spots(
        {"bright_regions": [{"box": (0.4, 0.4, 0.6, 0.6), "coverage": 0.2}]}
    )
    assert len(bright_only) == 1
    assert bright_only[0].kind == "bright"

    # A bright region below the presence floor is not a detail.
    assert find_detail_spots(
        {"bright_regions": [{"box": (0.4, 0.4, 0.6, 0.6), "coverage": 0.01}]}
    ) == []


def test_detail_spots_reject_bad_boxes():
    assert find_detail_spots({"faces": [{"box": (0.5, 0.5, 0.5, 0.7)}]}) == []
    assert find_detail_spots({"faces": [{"box": (-0.1, 0.1, 0.3, 0.4)}]}) == []
    # A box covering most of the frame is the scene, not a detail.
    assert find_detail_spots({"faces": [{"box": (0.05, 0.05, 0.95, 0.95)}]}) == []
    assert find_detail_spots({}) == []
    assert find_detail_spots(None) == []


def test_eligible_fixture_builds_two_beats():
    verdict = trailer_breakdown_eligibility(TRAILERS, REACTIONS, SPOTS, DURATION)
    assert verdict.eligible
    plan = verdict.plan
    assert plan is not None
    assert plan.hook_text == HOOK_FORMULA
    assert len(plan.beats) == 2
    assert plan.clip_duration == 15.0  # (3 + 1.5 + 3) x 2
    assert plan.moment_duration == DURATION
    first, second = plan.beats
    assert (first.trailer_start, first.trailer_end) == (2.0, 5.0)
    assert first.freeze_at == 5.0
    assert first.freeze_duration == 1.5
    assert first.detail.label == "DOOM"
    assert (first.reaction_start, first.reaction_end) == (5.0, 8.0)
    # Clip layout is sequential: trailer, freeze, reaction, repeat.
    assert (first.clip_trailer_start, first.clip_freeze_start) == (0.0, 3.0)
    assert first.clip_reaction_start == 4.5
    assert first.clip_end == 7.5
    assert (second.clip_trailer_start, second.clip_freeze_start) == (7.5, 10.5)
    assert second.clip_reaction_start == 12.0
    assert second.clip_end == 15.0
    # The cliffhanger names the LAST beat's detail, in its own words.
    assert plan.cliffhanger == (
        "Did you catch the figure in the background? Part 2"
    )


def test_cliffhanger_names_a_text_detail():
    assert build_cliffhanger(
        DetailSpot(kind="text", box=(0.5, 0.3, 0.7, 0.4), label="DOOM", score=0.9)
    ) == "Did you catch DOOM in the background? Part 2"


def test_eligibility_declines_honestly():
    # No trailer at all.
    verdict = trailer_breakdown_eligibility([], REACTIONS, SPOTS, DURATION)
    assert not verdict.eligible
    assert any("trailer" in r for r in verdict.reasons)
    # Trailer too short to carry a play beat.
    verdict = trailer_breakdown_eligibility(
        [{"start": 2.0, "end": 3.5}], REACTIONS, SPOTS, DURATION
    )
    assert not verdict.eligible
    # No reaction window.
    verdict = trailer_breakdown_eligibility(TRAILERS, [], SPOTS, DURATION)
    assert not verdict.eligible
    assert any("reaction" in r for r in verdict.reasons)
    # Reaction without a face on camera is not a reaction beat.
    verdict = trailer_breakdown_eligibility(
        TRAILERS,
        [
            {"start": 5.0, "end": 8.0, "face_present": False},
            {"start": 13.0, "end": 16.0, "face_present": True},
        ],
        SPOTS,
        DURATION,
    )
    assert not verdict.eligible
    assert any("face" in r for r in verdict.reasons)
    # No grounded detail at either freeze point.
    verdict = trailer_breakdown_eligibility(TRAILERS, REACTIONS, {}, DURATION)
    assert not verdict.eligible
    assert any("detail" in r for r in verdict.reasons)
    # Only one beat can be built.
    verdict = trailer_breakdown_eligibility(
        TRAILERS[:1], REACTIONS[:1], SPOTS, DURATION
    )
    assert not verdict.eligible
    assert any("at least 2" in r for r in verdict.reasons)
    # Beats do not fit inside the moment.
    verdict = trailer_breakdown_eligibility(TRAILERS, REACTIONS, SPOTS, 10.0)
    assert not verdict.eligible
    assert any("only 10.0s" in r for r in verdict.reasons)


def test_resolver_grants_only_when_offered_and_eligible():
    profile = load_profile("gaming")
    style, reason = resolve_trailer_breakdown_style(
        "trailer_breakdown", profile, TRAILERS, REACTIONS, SPOTS, DURATION
    )
    assert style == "trailer_breakdown"
    assert reason is None
    # A profile that does not offer it falls back to its default.
    style, reason = resolve_trailer_breakdown_style(
        "trailer_breakdown",
        load_profile("finance_business"),
        TRAILERS,
        REACTIONS,
        SPOTS,
        DURATION,
    )
    assert style == "problem"
    assert reason and "not offered" in reason
    # An ineligible moment falls back, with the reason recorded.
    style, reason = resolve_trailer_breakdown_style(
        "trailer_breakdown", profile, TRAILERS, REACTIONS, {}, DURATION
    )
    assert style == "statement"
    assert reason and "detail" in reason
    # Any other style passes through untouched.
    style, reason = resolve_trailer_breakdown_style(
        "statistic", profile, TRAILERS, REACTIONS, SPOTS, DURATION
    )
    assert (style, reason) == ("statistic", None)


def test_build_plan_returns_none_on_decline():
    assert (
        build_breakdown_plan(TRAILERS, REACTIONS, {}, DURATION) is None
    )
    plan = build_breakdown_plan(TRAILERS, REACTIONS, SPOTS, DURATION)
    assert plan is not None and len(plan.beats) == 2


def test_plan_to_edl_segments_single_range():
    plan = build_breakdown_plan(TRAILERS, REACTIONS, SPOTS, DURATION)
    assert plan is not None
    edl = plan_to_edl_segments(plan, [(0.0, 36.0)])
    assert edl["duration"] == 15.0
    assert edl["breakdown"]["template"] == "trailer_breakdown"
    assert edl["breakdown"]["beats"] == 2
    assert edl["breakdown"]["freeze_audio"] == "silent"
    segments = edl["segments"]
    assert len(segments) == 6  # trailer, freeze, reaction x 2
    trailer, freeze, reaction = segments[0], segments[1], segments[2]
    assert (trailer["start"], trailer["end"]) == (2.0, 5.0)
    assert freeze["freeze"] is True
    assert freeze["freeze_at"] == 5.0
    assert freeze["freeze_duration"] == 1.5
    assert (reaction["start"], reaction["end"]) == (5.0, 8.0)
    # Zoom keyframes: one punch-in per freeze, detail_reveal trigger.
    zooms = edl["zoom_keyframes"]
    assert len(zooms) == 2
    assert zooms[0]["type"] == "punch_in"
    assert zooms[0]["time"] == 3.0
    assert zooms[0]["trigger"] == DETAIL_REVEAL_TRIGGER
    assert zooms[1]["time"] == 10.5


def test_plan_to_edl_segments_maps_multi_range_prestage():
    plan = build_breakdown_plan(TRAILERS, REACTIONS, SPOTS, DURATION)
    assert plan is not None
    ranges = [(2.0, 8.0), (10.0, 16.0)]
    edl = plan_to_edl_segments(plan, ranges)
    offsets = output_offsets_for_ranges(ranges)
    segments = edl["segments"]
    # Trailer 1 [2,5) is range 0: prestage [0,3).
    assert (segments[0]["start"], segments[0]["end"]) == (0.0, 3.0)
    # Freeze 1 at source 5.0 -> prestage 3.0 (end of range 0).
    assert segments[1]["freeze_at"] == 3.0
    # Reaction 1 [5,8) -> prestage [3,6).
    assert (segments[2]["start"], segments[2]["end"]) == (3.0, 6.0)
    # Trailer 2 [10,13) starts at the second range's offset.
    assert segments[3]["start"] == offsets[1]
    assert segments[3]["end"] == offsets[1] + 3.0
    # Freeze 2 at source 13.0 -> second range offset + 3.0.
    assert segments[4]["freeze_at"] == offsets[1] + 3.0
    # Reaction 2 [13,16) -> [offset+3, offset+6).
    assert (segments[5]["start"], segments[5]["end"]) == (
        offsets[1] + 3.0,
        offsets[1] + 6.0,
    )


def test_plan_to_edl_segments_rejects_out_of_range_freeze():
    plan = build_breakdown_plan(TRAILERS, REACTIONS, SPOTS, DURATION)
    assert plan is not None
    try:
        plan_to_edl_segments(plan, [(20.0, 30.0)])
    except ValueError as exc:
        assert "outside the EDL source ranges" in str(exc)
    else:
        raise AssertionError("expected ValueError for out-of-range freeze")


def test_ass_overlay_events_and_append(tmp_path):
    plan = build_breakdown_plan(TRAILERS, REACTIONS, SPOTS, DURATION)
    assert plan is not None
    style_line, events = trailer_breakdown_ass_events(plan, 540, 960)
    assert style_line.startswith("Style: TrailerBreak,")
    # One ring per beat, drawn for exactly its freeze window.
    rings = [e for e in events if "\\p1" in e]
    assert len(rings) == 2
    assert "0:00:03.00,0:00:04.50" in rings[0]
    assert "0:00:10.50,0:00:12.00" in rings[1]
    assert "\\3c&H000000FF&" in rings[0]  # red outline
    assert "\\1a&HFF&" in rings[0]  # transparent fill: a ring, not a disc
    # The ring is pinned with \an7\pos at the detail box's top-left
    # and a transposed, origin-relative path (this pipeline's libass
    # renders path points transposed — see _ellipse_path). Beat 1's
    # box (0.55,0.30,0.75,0.42) lands at (281,259); beat 2's
    # (0.60,0.25,0.72,0.45) at (308,211). The path starts at the
    # transposed origin: (0, ry) for beat 1, (0, rx) for beat 2.
    assert "\\an7\\pos(281,259)" in rings[0]
    assert "m 0,70" in rings[0]
    assert "\\an7\\pos(308,211)" in rings[1]
    assert "m 0,48" in rings[1]
    # The cliffhanger rides the last 2.5 s.
    cliff = [e for e in events if "Did you catch" in e]
    assert len(cliff) == 1
    assert "0:00:12.50,0:00:15.00" in cliff[0]
    assert "Part 2" in cliff[0]

    ass = tmp_path / "test.ass"
    ass.write_text(
        "[Script Info]\n"
        "Title: test\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,"
        "&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n",
        encoding="utf-8",
    )
    assert append_trailer_breakdown_overlay(ass, plan, 540, 960) is True
    text = ass.read_text(encoding="utf-8")
    assert "Style: TrailerBreak," in text
    assert "\\p1" in text
    assert "Did you catch the figure in the background? Part 2" in text
    # Never append twice.
    assert append_trailer_breakdown_overlay(ass, plan, 540, 960) is False
