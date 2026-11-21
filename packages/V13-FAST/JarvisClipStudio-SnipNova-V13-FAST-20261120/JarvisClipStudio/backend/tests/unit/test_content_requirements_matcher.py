"""Matcher: transcript search, focus scoring, tiers, supplementary candidates."""

from __future__ import annotations

from src.auto_director.content_requirements import (
    evaluate_candidate_requirements,
    evaluate_focus,
    focus_satisfaction,
    generate_requirement_candidates,
    partition_by_tier,
    search_lines,
    validate_content_brief,
)


def _lines(spec):
    lines = []
    for start, text in spec:
        lines.append(
            {"start_seconds": float(start), "end_seconds": float(start + 10),
             "start_time": f"{start // 60:02d}:{start % 60:02d}",
             "end_time": f"{(start + 10) // 60:02d}:{(start + 10) % 60:02d}",
             "text": text}
        )
    return lines


GENERIC = "they talk about the drive and the weather and lunch plans today"


def _expedia_lines():
    lines = []
    for start in range(0, 600, 10):
        lines.append((start, GENERIC))
    lines[18] = (180, "we booked the hotel on Expedia for the whole trip")
    lines[19] = (190, "Expedia prices were cheaper than everywhere else we checked")
    lines[20] = (200, "the destination we picked on Expedia looks unreal honestly")
    lines[21] = (210, "Expedia showed us the travel experience ratings for each place")
    lines[22] = (220, "so we confirmed everything on Expedia and packed our bags")
    lines[30] = (300, "and thanks to Expedia for sponsoring this trip segment")
    return _lines(lines)


def test_exact_and_fuzzy_search():
    lines = _lines([(0, "we love Expedia deals"), (10, "expidia is great too"), (20, "expenditure reports are dull")])
    hits = search_lines(lines, [{"phrase": "expedia", "source": "topic"}])
    kinds = {h["line_index"]: h["match"] for h in hits}
    assert kinds.get(0) == "exact"
    assert kinds.get(1) == "fuzzy"  # ASR wobble within edit distance 1
    assert 2 not in kinds  # an unrelated word must not hit


def test_focus_major_vs_passing_vs_absent():
    lines = _expedia_lines()
    terms = [{"phrase": "expedia", "source": "topic"}]
    major = evaluate_focus(lines[18:23], terms, window_start=180, window_end=230)
    assert major["focus"] == "major" and major["hit_lines"] == 5
    passing = evaluate_focus(lines[29:33], terms, window_start=290, window_end=330)
    assert passing["focus"] == "passing"
    absent = evaluate_focus(lines[40:44], terms, window_start=400, window_end=440)
    assert absent["focus"] == "absent" and absent["hit_count"] == 0


def test_focus_grey_zone_is_uncertain_for_mandatory():
    # One hit line, span share 0.20 (10s of a 50s window): grey zone.
    lines = _lines([(0, "Expedia is honestly the whole reason this trip worked out well")] + [(s, GENERIC) for s in (10, 20, 30, 40)])
    ev = evaluate_focus(lines, [{"phrase": "expedia", "source": "topic"}], window_start=0, window_end=50)
    assert ev["grey_zone"] is True
    req = {"requires_major": True}
    assert focus_satisfaction(ev, req) == "uncertain"


def test_passing_does_not_satisfy_major_requirement():
    lines = _expedia_lines()
    ev = evaluate_focus(lines[29:33], [{"phrase": "expedia", "source": "topic"}], window_start=290, window_end=330)
    assert focus_satisfaction(ev, {"requires_major": True}) == "failed"
    assert focus_satisfaction(ev, {"requires_major": False}) == "satisfied"


def test_candidate_tiers_and_partition_order():
    brief = validate_content_brief(
        {"requirements": [{"text": "Expedia must be a major focus of the content.", "kind": "mandatory"}]}
    )
    lines = _expedia_lines()
    verdict_a = evaluate_candidate_requirements(lines, 180, 230, brief)
    verdict_c = evaluate_candidate_requirements(lines, 290, 330, brief)
    assert verdict_a["tier"] == "A"
    assert verdict_c["tier"] == "C"  # passing mention, requires_major unmet
    items = ["viral-but-passing", "compliant"]
    ordered = partition_by_tier(items, [verdict_c, verdict_a])
    assert ordered == ["compliant", "viral-but-passing"]


def test_supplementary_candidates_find_the_brief_moment():
    brief = validate_content_brief(
        {
            "requirements": [{"text": "Expedia must be a major focus of the content.", "kind": "mandatory"}],
            "allowed_ranges": [{"start": "2:00", "end": "8:00"}],
        }
    )
    lines = _expedia_lines()
    candidates = generate_requirement_candidates(lines, brief, clip_length="auto")
    assert candidates, "the compliant Expedia stretch must become a candidate"
    starts = [c["start_seconds"] for c in candidates]
    assert any(170 <= s <= 190 for s in starts)
    for cand in candidates:
        assert cand["start_seconds"] >= 120 and cand["end_seconds"] <= 480
        assert "Requirement match" in cand["reasoning"]


def test_supplementary_candidates_never_cross_range_boundary():
    brief = validate_content_brief(
        {
            "requirements": [{"text": "Expedia must be a major focus of the content.", "kind": "mandatory"}],
            "allowed_ranges": [{"start": "0:00", "end": "3:10"}],
        }
    )
    lines = _expedia_lines()
    candidates = generate_requirement_candidates(lines, brief, clip_length="auto")
    for cand in candidates:
        assert cand["end_seconds"] <= 190


def test_topic_focus_min_topics():
    brief = validate_content_brief(
        {
            "requirements": [
                {
                    "text": "Feature one of the big moments.",
                    "type": "topic_focus",
                    "kind": "mandatory",
                    "topics": ["steal a dragon egg", "glowing crystal caves"],
                    "min_topics": 1,
                    "requires_major": True,
                }
            ]
        }
    )
    lines = _lines(
        [(s, "we found the glowing crystal caves and explored the glowing crystal caves all day long today") for s in range(0, 50, 10)]
    )
    verdict = evaluate_candidate_requirements(lines, 0, 50, brief)
    assert verdict["tier"] == "A"
