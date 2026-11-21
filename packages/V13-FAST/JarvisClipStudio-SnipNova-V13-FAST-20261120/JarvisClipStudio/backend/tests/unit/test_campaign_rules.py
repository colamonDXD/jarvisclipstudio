"""Campaign rules (Chopify take): schema, heuristic parse, cut-time enforcement."""

from src.auto_director.campaign_rules import (
    campaign_rules_of,
    enforce_campaign_rules,
    evaluate_segment_against_rules,
    normalize_campaign_rules,
    parse_campaign_brief_text,
)
from src.auto_director.content_requirements import normalize_brief_v2


def test_normalize_empty_is_none():
    assert normalize_campaign_rules(None) is None
    assert normalize_campaign_rules({}) is None
    assert normalize_campaign_rules({"goal": "awareness"}) is None
    assert normalize_campaign_rules("nope") is None


def test_normalize_clamps_and_swaps_lengths():
    rules = normalize_campaign_rules(
        {"must_mention": "Whop", "min_len": 90, "max_len": 30, "aspect": "4:3"}
    )
    assert rules is not None
    assert rules["must_mention"] == ["whop"]
    assert rules["min_len"] == 30 and rules["max_len"] == 90
    assert rules["aspect"] == ""  # unknown aspect dropped, not enforced


def test_normalize_defaults_do_not_constrain_length():
    rules = normalize_campaign_rules({"banned": ["competitor"]})
    assert rules is not None
    assert rules["min_len"] is None and rules["max_len"] is None


def test_heuristic_parse_extracts_rules():
    rules = parse_campaign_brief_text(
        "Whop campaign for GTA clips.\n"
        "Must mention: whop, link in bio\n"
        "Avoid: spoilers, leaks\n"
        "Clips 30-60s, vertical 9:16.\n"
        "CTA: join via the link"
    )
    assert rules is not None
    assert "whop" in rules["must_mention"]
    assert "spoilers" in rules["banned"]
    assert rules["min_len"] == 30 and rules["max_len"] == 60
    assert rules["aspect"] == "9:16"
    assert rules["source"] == "heuristic"
    assert "warning" in rules


def test_evaluate_banned_and_must_mention():
    rules = normalize_campaign_rules(
        {"must_mention": ["whop"], "banned": ["spoiler"]}
    )
    ok = evaluate_segment_against_rules(
        text="he screamed and posted it on whop", rules=rules
    )
    assert ok["ok"] and ok["reasons"] == []
    bad = evaluate_segment_against_rules(
        text="huge spoiler at the end", rules=rules
    )
    assert not bad["ok"]
    assert any("banned" in r for r in bad["reasons"])
    assert any("must-mention" in r for r in bad["reasons"])


def test_evaluate_length_and_aspect():
    rules = normalize_campaign_rules(
        {"min_len": 30, "max_len": 60, "aspect": "9:16"}
    )
    short = evaluate_segment_against_rules(text="x", duration=12.0, rules=rules)
    assert not short["ok"] and any("too short" in r for r in short["reasons"])
    long = evaluate_segment_against_rules(text="x", duration=90.0, rules=rules)
    assert not long["ok"] and any("too long" in r for r in long["reasons"])
    wrong_aspect = evaluate_segment_against_rules(
        text="x", duration=45.0, aspect="16:9", rules=rules
    )
    assert not wrong_aspect["ok"]
    # Aspect unknown to the caller: not checked, not a violation.
    unknown = evaluate_segment_against_rules(text="x", duration=45.0, rules=rules)
    assert unknown["ok"]


def test_enforce_splits_with_reasons_and_keeps_order():
    rules = normalize_campaign_rules({"must_mention": ["whop"], "banned": ["leak"]})
    segments = [
        {"start_time": "00:00", "end_time": "00:40", "text": "posted on whop today"},
        {"start_time": "00:40", "end_time": "01:20", "text": "a leak nobody wanted"},
        {"start_time": "01:20", "end_time": "02:00", "text": "no mention here"},
        {"start_time": "02:00", "end_time": "02:40", "text": "whop again, great moment"},
    ]
    kept, rejected = enforce_campaign_rules(segments, rules)
    assert [s["start_time"] for s in kept] == ["00:00", "02:00"]
    assert [s["start_time"] for s in rejected] == ["00:40", "01:20"]
    assert all(r["campaign_rejection"] for r in rejected)


def test_enforce_no_rules_is_noop():
    segments = [{"start_time": 0, "end_time": 10, "text": "anything"}]
    kept, rejected = enforce_campaign_rules(segments, None)
    assert kept == segments and rejected == []


def test_brief_normalisation_preserves_campaign_rules():
    brief = normalize_brief_v2(
        {
            "requirements": [{"type": "must_mention", "text": "whop"}],
            "campaign_rules": {"must_mention": ["whop"], "banned": ["spoiler"], "min_len": 30},
        }
    )
    assert brief is not None
    rules = campaign_rules_of(brief)
    assert rules is not None
    assert rules["must_mention"] == ["whop"]
    assert rules["min_len"] == 30


def test_brief_without_campaign_rules_has_none():
    brief = normalize_brief_v2(
        {"requirements": [{"type": "topic_focus", "text": "reactions"}]}
    )
    assert brief is not None
    assert "campaign_rules" not in brief
    assert campaign_rules_of(brief) is None
