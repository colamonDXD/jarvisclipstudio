"""Synthetic end-to-end proof for Content Requirements (design §7.2).

One 600 s fixture transcript, no Docker/GPU/network:

- Moment A (180-230 s, in-range): five Expedia lines -> major focus.
- Moment B (30-80 s, out-of-range): the SAME five lines verbatim, with a
  HIGHER fake virality score — the trap for text-only implementations.
- Moment C (300-330 s, in-range): one passing sponsor line.
- Moment D: a second brief adds a mandatory "ZQX Travel Club" mention
  that appears nowhere.

Asserts the four-point enforcement story at the seams the pipeline
actually uses: S1 clamp (ai.py), S2 gate (VideoService._apply_brief_gate)
and the post-render compliance check (evaluate_clip_compliance). The
real-encode render leg (sidecar ranges after a real render) is covered
by test_content_requirements_render_clamp.py; this module proves the
selection + judgement legs and the honest no-match outcome.
"""

from __future__ import annotations

from src import ai
from src.auto_director.content_requirements import (
    evaluate_clip_compliance,
    validate_content_brief,
)
from src.services.video_service import VideoService

EXPEDIA_LINE = "we booked on Expedia and Expedia planned the destination trip"
GENERIC_LINE = "generic chatter about lunch and the drive"
SPONSOR_LINE = "thanks to Expedia for sponsoring this"

BRIEF = validate_content_brief(
    {
        "requirements": [
            {"text": "Expedia must be a major focus of the content.", "kind": "mandatory"}
        ],
        "allowed_ranges": [{"start": "2:00", "end": "8:00"}],
    }
)

BRIEF_WITH_ZQX = validate_content_brief(
    {
        "requirements": [
            {"text": "Expedia must be a major focus of the content.", "kind": "mandatory"},
            {"text": "You must mention ZQX Travel Club", "kind": "mandatory"},
        ],
        "allowed_ranges": [{"start": "2:00", "end": "8:00"}],
    }
)

BRIEF_ZQX_ONLY = validate_content_brief(
    {
        "requirements": [
            {"text": "You must mention ZQX Travel Club", "kind": "mandatory"}
        ],
        "allowed_ranges": [{"start": "2:00", "end": "8:00"}],
    }
)

BRIEF_NO_RANGES = validate_content_brief(
    {
        "requirements": [
            {"text": "Expedia must be a major focus of the content.", "kind": "mandatory"}
        ],
        "allowed_ranges": [],
    }
)


def _line_text(start: int) -> str:
    if 30 <= start < 80 or 180 <= start < 230:
        return EXPEDIA_LINE  # Moments B and A: identical wording
    if start == 300:
        return SPONSOR_LINE  # Moment C: a single passing mention
    return GENERIC_LINE


def _transcript_text() -> str:
    rows = []
    for start in range(0, 600, 10):
        rows.append(
            f"[{start // 60:02d}:{start % 60:02d} - "
            f"{(start + 10) // 60:02d}:{(start + 10) % 60:02d}] "
            f"Speaker A: {_line_text(start)}"
        )
    return "\n".join(rows)


def _lines():
    return [
        {
            "start_time": f"{start // 60:02d}:{start % 60:02d}",
            "end_time": f"{(start + 10) // 60:02d}:{(start + 10) % 60:02d}",
            "start_seconds": start,
            "end_seconds": start + 10,
            "text": _line_text(start),
        }
        for start in range(0, 600, 10)
    ]


def _seg_dict(start, end, virality=50):
    return {
        "start_time": f"{start // 60:02d}:{start % 60:02d}",
        "end_time": f"{end // 60:02d}:{end % 60:02d}",
        "text": "x",
        "virality_score": virality,
    }


MOMENT_B = _seg_dict(30, 80, virality=99)    # out-of-range, highest score
MOMENT_C = _seg_dict(300, 330, virality=95)  # in-range, passing only
MOMENT_A = _seg_dict(180, 230, virality=40)  # in-range, major focus


def test_proof_a_selected_b_never_c_not_rendered_while_a_exists():
    gated, report = VideoService._apply_brief_gate(
        [MOMENT_B, MOMENT_C, MOMENT_A], BRIEF, _transcript_text()
    )
    assert [(s["start_time"], s["end_time"]) for s in gated] == [("03:00", "03:50")]
    assert gated[0]["brief_tier"] == "A"
    assert report["outcome"] == "clips_selected"
    # B was dropped by the range rule, C by the mandatory-focus rule.
    assert report["candidates_dropped_range"] == 1
    assert report["candidates_dropped_mandatory"] >= 1


def test_proof_b_in_no_candidate_or_keep_range_at_s1():
    lines = _lines()
    seg_b = ai.TranscriptSegment(start_time="00:30", end_time="01:20", text=EXPEDIA_LINE)
    assert ai._clamp_segment_to_allowed_ranges(seg_b, [(120.0, 480.0)], lines, 15) is None
    # A straddling segment is clamped INTO the range, never across it.
    seg_straddle = ai.TranscriptSegment(
        start_time="01:40", end_time="03:20", text="some grounded words here"
    )
    kept = ai._clamp_segment_to_allowed_ranges(seg_straddle, [(120.0, 480.0)], lines, 15)
    assert kept is not None
    assert kept.start_time == "02:00"


def test_proof_c_alone_is_no_compliant_moments_not_a_clip():
    gated, report = VideoService._apply_brief_gate([MOMENT_C], BRIEF, _transcript_text())
    assert gated == []
    assert report["outcome"] == "no_compliant_moments"
    assert report["candidates_compliant"] == 0


def test_proof_no_match_brief_records_search_and_selects_nothing():
    # No candidate satisfies ANY mandatory requirement (the ZQX mention
    # exists nowhere): every candidate is Tier C, the slate empties and
    # the outcome is the honest no-match — zero clips, no fallback.
    gated, report = VideoService._apply_brief_gate(
        [MOMENT_B, MOMENT_C, MOMENT_A], BRIEF_ZQX_ONLY, _transcript_text()
    )
    assert gated == []
    assert report["outcome"] == "no_compliant_moments"
    assert report["candidates_compliant"] == 0
    searched_terms = [
        term.lower() for req in report["requirements"] for term in req.get("terms", [])
    ]
    assert any("zqx" in term for term in searched_terms)
    assert report["searched_ranges"], "the searched ranges must be recorded"


def test_proof_partial_satisfaction_is_tier_b_and_flagged_non_compliant():
    # Design §3.4 tier semantics: a candidate satisfying SOME mandatory
    # requirements is Tier B — it may render, but the post-render check
    # must flag it non_compliant (never silently compliant).
    gated, report = VideoService._apply_brief_gate(
        [MOMENT_A], BRIEF_WITH_ZQX, _transcript_text()
    )
    assert len(gated) == 1 and gated[0]["brief_tier"] == "B"
    assert report["outcome"] == "clips_selected"
    compliance = evaluate_clip_compliance(BRIEF_WITH_ZQX, [(180.0, 230.0)], _lines())
    assert compliance["status"] == "non_compliant"
    by_text = {entry["text"]: entry for entry in compliance["requirements"]}
    assert by_text["You must mention ZQX Travel Club"]["status"] == "failed"
    assert (
        by_text["Expedia must be a major focus of the content."]["status"]
        == "satisfied"
    )


def test_proof_post_render_compliance_for_a_and_c():
    lines = _lines()
    report_a = evaluate_clip_compliance(BRIEF, [(180.0, 230.0)], lines)
    assert report_a["status"] == "compliant"
    entry = report_a["requirements"][0]
    assert entry["status"] == "satisfied"
    assert entry["focus"] == "major"
    assert entry["evidence"], "verbatim evidence excerpts are required"
    assert "Expedia" in entry["evidence"][0]["excerpt"]
    assert report_a["source_timestamps"] == {"start": "03:00", "end": "03:50"}
    assert report_a["ranges"]["violations"] == []

    report_c = evaluate_clip_compliance(BRIEF, [(300.0, 330.0)], lines)
    assert report_c["status"] == "non_compliant"
    assert report_c["requirements"][0]["status"] == "failed"
    assert report_c["requirements"][0]["focus"] == "passing"


def test_proof_no_ranges_control_b_is_pickable():
    # Control: the same brief WITHOUT allowed ranges lets B through the
    # gate as Tier A — proving the range rule, not the text match, is
    # what excluded B above. (In ai.py the S1 clamp is only invoked when
    # the brief has ranges at all: `if allowed:`.)
    from src.auto_director.content_requirements import allowed_range_tuples

    assert allowed_range_tuples(BRIEF_NO_RANGES) == []
    gated, report = VideoService._apply_brief_gate(
        [MOMENT_B], BRIEF_NO_RANGES, _transcript_text()
    )
    assert [(s["start_time"], s["end_time"]) for s in gated] == [("00:30", "01:20")]
    assert gated[0]["brief_tier"] == "A"
    assert report["outcome"] == "clips_selected"
