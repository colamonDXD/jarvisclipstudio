"""Post-render typed compliance checks (design §3.6 + Addendum A.2/A.3)."""

from __future__ import annotations

from src.auto_director.content_requirements import (
    compliance_summary_for,
    evaluate_clip_compliance,
    validate_content_brief,
)


def _lines(spec):
    return [
        {"start_seconds": float(s), "end_seconds": float(s + 10), "text": t}
        for s, t in spec
    ]


EXPEDIA_LINES = _lines(
    [(s, "we booked on Expedia and Expedia planned the whole destination trip") for s in range(180, 230, 10)]
    + [(s, "generic talk about lunch and the drive home today") for s in range(230, 480, 10)]
)


def _brief(requirements, ranges=None):
    payload = {"requirements": requirements}
    if ranges:
        payload["allowed_ranges"] = ranges
    return validate_content_brief(payload)


def test_compliant_major_focus_in_range():
    brief = _brief(
        [{"text": "Expedia must be a major focus of the content.", "kind": "mandatory"}],
        ranges=[{"start": "2:00", "end": "8:00"}],
    )
    report = evaluate_clip_compliance(brief, [(180.0, 230.0)], EXPEDIA_LINES)
    assert report["status"] == "compliant"
    assert report["version"] == 1 and "checked_at" in report
    entry = report["requirements"][0]
    assert entry["status"] == "satisfied" and entry["focus"] == "major"
    assert entry["evidence"], "verbatim evidence excerpts must be stored"
    assert report["ranges"]["violations"] == []


def test_passing_mandatory_is_non_compliant():
    brief = _brief(
        [{"text": "Expedia must be a major focus of the content.", "kind": "mandatory"}]
    )
    lines = _lines([(300, "thanks to Expedia for sponsoring")] + [(s, "generic chatter") for s in range(310, 340, 10)])
    report = evaluate_clip_compliance(brief, [(300.0, 340.0)], lines)
    assert report["status"] == "non_compliant"
    assert report["requirements"][0]["status"] == "failed"


def test_range_violation_is_non_compliant():
    brief = _brief(
        [{"text": "Expedia must be a major focus of the content.", "kind": "mandatory"}],
        ranges=[{"start": "2:00", "end": "8:00"}],
    )
    report = evaluate_clip_compliance(brief, [(60.0, 230.0)], EXPEDIA_LINES)
    assert report["status"] == "non_compliant"
    assert report["ranges"]["violations"] == [[60.0, 230.0]]


def test_must_mention_caption_only_hit_satisfies_and_lists_sources():
    brief = _brief(
        [{"text": "Mention the code", "type": "must_mention", "kind": "mandatory", "phrase": "CREATOR"}]
    )
    report = evaluate_clip_compliance(
        brief, [(0.0, 30.0)], _lines([(0, "playing the game with friends")]),
        caption_text="use code CREATOR for a free starter kit",
        title_copy="My clip title",
    )
    entry = report["requirements"][0]
    assert entry["status"] == "satisfied"
    assert entry["hit_sources"] == ["captions"]
    assert entry["sources"]["transcript"] == "searched_no_hit"
    assert entry["sources"]["title_copy"] == "searched_no_hit"
    assert report["status"] == "compliant"


def test_must_mention_absent_title_copy_is_uncertain_not_satisfied():
    brief = _brief(
        [{"text": "Mention the code", "type": "must_mention", "kind": "mandatory", "phrase": "CREATOR"}]
    )
    report = evaluate_clip_compliance(
        brief, [(0.0, 30.0)], _lines([(0, "playing the game with friends")]),
        caption_text="just gameplay captions here",
        title_copy=None,
    )
    entry = report["requirements"][0]
    assert entry["sources"]["title_copy"] == "absent"
    assert entry["status"] == "uncertain"
    assert report["status"] == "uncertain"


def test_caption_rule_hashtag_needs_the_hash():
    brief = _brief(
        [{"text": "Use the tag", "type": "caption_rule", "kind": "mandatory", "hashtags": ["#IShowSpeed"]}]
    )
    bare = evaluate_clip_compliance(
        brief, [(0.0, 30.0)], _lines([(0, "x")]),
        caption_text="shoutout to IShowSpeed fans", title_copy="title",
    )
    assert bare["requirements"][0]["status"] == "failed"
    tagged = evaluate_clip_compliance(
        brief, [(0.0, 30.0)], _lines([(0, "x")]),
        caption_text="watch this #ishowspeed moment", title_copy="title",
    )
    assert tagged["requirements"][0]["status"] == "satisfied"
    assert tagged["requirements"][0]["accuracy"] == "presence_only"


def test_mandatory_caption_rule_caps_at_uncertain_without_evidence():
    # v1 brief, presentation requirement: checked in v2 against caption
    # text; with no caption/title evidence recorded it can never pass.
    brief2 = validate_content_brief(
        {"version": 1, "requirements": [{"text": "The caption must use #IShowSpeed", "kind": "mandatory"}]}
    )
    assert brief2["requirements"][0]["type"] == "caption_rule"
    report = evaluate_clip_compliance(brief2, [(0.0, 30.0)], _lines([(0, "x")]))
    assert report["status"] == "uncertain"
    assert report["requirements"][0]["status"] == "uncertain"


def test_structure_hook_from_recorded_facts():
    brief = _brief([{"text": "Hook in first 2 seconds", "type": "structure_hook", "kind": "mandatory"}])
    ok = evaluate_clip_compliance(
        brief, [(0.0, 30.0)], _lines([(0, "x")]),
        hook_facts={"status": "already_leading", "hook_timestamp_after": 2.0},
    )
    assert ok["requirements"][0]["status"] == "satisfied"
    late = evaluate_clip_compliance(
        brief, [(0.0, 30.0)], _lines([(0, "x")]),
        hook_facts={"status": "already_leading", "hook_timestamp_after": 2.5},
    )
    assert late["requirements"][0]["status"] == "failed"
    missing = evaluate_clip_compliance(brief, [(0.0, 30.0)], _lines([(0, "x")]))
    assert missing["requirements"][0]["status"] == "uncertain"


def test_structure_cta_window_and_form():
    brief = _brief(
        [{"text": "CTA at the end", "type": "structure_cta", "kind": "mandatory",
          "phrases": ["play Minerscave on Roblox"]}]
    )
    lines = _lines([(s, "gameplay happening here") for s in range(0, 100, 10)]
                   + [(100, "go play Minerscave on Roblox right now friends")])
    report = evaluate_clip_compliance(brief, [(0.0, 110.0)], lines, caption_text="gameplay")
    entry = report["requirements"][0]
    assert entry["status"] == "satisfied" and entry["form"] == "spoken"
    # The same phrase at the START is outside the final-stretch window.
    early = _lines([(0, "go play Minerscave on Roblox right now friends")]
                   + [(s, "gameplay happening here") for s in range(10, 110, 10)])
    report2 = evaluate_clip_compliance(brief, [(0.0, 110.0)], early, caption_text="gameplay")
    assert report2["requirements"][0]["form"] == "none"
    assert report2["requirements"][0]["status"] == "failed"


def test_structure_cta_generic_fallback_is_labelled():
    brief = _brief([{"text": "CTA at the end", "type": "structure_cta", "kind": "mandatory"}])
    lines = _lines([(s, "gameplay") for s in range(0, 100, 10)] + [(100, "download it and play today")])
    report = evaluate_clip_compliance(brief, [(0.0, 110.0)], lines)
    entry = report["requirements"][0]
    assert entry["matched_by"] == "generic_cta_pattern"
    assert entry["status"] == "satisfied"


def test_visual_presence_stubbed_judgement():
    brief = _brief([{"text": "Show the game", "type": "visual_presence", "kind": "mandatory"}])
    present = evaluate_clip_compliance(
        brief, [(0.0, 30.0)], _lines([(0, "x")]), visual_judgement={"present": True}
    )
    assert present["requirements"][0]["status"] == "satisfied"
    absent = evaluate_clip_compliance(
        brief, [(0.0, 30.0)], _lines([(0, "x")]), visual_judgement={"present": False}
    )
    assert absent["status"] == "non_compliant"
    unavailable = evaluate_clip_compliance(brief, [(0.0, 30.0)], _lines([(0, "x")]))
    assert unavailable["status"] == "uncertain"  # mandatory visual caps at uncertain
    assert unavailable["requirements"][0]["judgement_source"] == "model_judgement"


def test_info_only_never_checked():
    brief = _brief(
        [
            {"text": "Payouts start at 10,000 views.", "type": "info_only"},
            {"text": "Expedia must be a major focus of the content.", "kind": "mandatory"},
        ]
    )
    report = evaluate_clip_compliance(brief, [(180.0, 230.0)], EXPEDIA_LINES)
    assert [r["type"] for r in report["requirements"]] == ["topic_focus"]
    assert report["status"] == "compliant"


def test_compliance_summary_shape():
    brief = _brief(
        [{"text": "Expedia must be a major focus of the content.", "kind": "mandatory"}]
    )
    lines = _lines([(300, "thanks to Expedia for sponsoring")] + [(s, "generic") for s in range(310, 340, 10)])
    report = evaluate_clip_compliance(brief, [(300.0, 340.0)], lines)
    summary = compliance_summary_for(report)
    assert summary["status"] == "non_compliant"
    assert summary["mandatory_failed"] == ["r1"]
    assert compliance_summary_for(None) is None
