"""Range parser + brief validation (Content Requirements, design §4/A.1)."""

from __future__ import annotations

import pytest

from src.auto_director.content_requirements import (
    normalize_brief_v2,
    parse_allowed_ranges,
    parse_timecode,
    validate_content_brief,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10529, 10529.0),
        (10529.5, 10529.5),
        ("10529", 10529.0),
        ("45", 45.0),  # raw seconds, NOT 45 minutes
        ("20:45", 1245.0),
        ("2:56:29", 10589.0),
        ("176:29", 10589.0),  # unbounded-minutes form
        ("20:45.5", 1245.5),
        ("0:00", 0.0),
    ],
)
def test_parse_timecode_accepted_forms(value, expected):
    assert parse_timecode(value) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "abc", "1:99", "1:2:99", "1:2:3:4", "-5", -5, "", "12:", ":30",
        "1.5:30", True, None, float("nan"),
    ],
)
def test_parse_timecode_rejections(bad):
    with pytest.raises(ValueError):
        parse_timecode(bad)


def test_ranges_open_end_and_labels():
    ranges = parse_allowed_ranges([{"start": "2:56:29", "end": None}])
    assert ranges == [
        {
            "start_seconds": 10589.0,
            "end_seconds": None,
            "start_label": "2:56:29",
            "end_label": None,
        }
    ]
    ranges = parse_allowed_ranges([{"start": "20:45", "end": "end"}])
    assert ranges[0]["end_seconds"] is None
    ranges = parse_allowed_ranges([{"start": 10, "end": ""}])
    assert ranges[0]["end_seconds"] is None


def test_ranges_end_clamped_to_source_duration():
    ranges = parse_allowed_ranges(
        [{"start": "0:10", "end": "9:99" if False else "20:00"}], source_duration=600
    )
    assert ranges[0]["end_seconds"] == 600.0


def test_ranges_start_beyond_duration_rejected():
    with pytest.raises(ValueError):
        parse_allowed_ranges([{"start": "20:00", "end": None}], source_duration=600)


def test_ranges_end_before_start_rejected():
    with pytest.raises(ValueError):
        parse_allowed_ranges([{"start": "1:00", "end": "0:30"}])


def test_ranges_sorted_and_merged():
    ranges = parse_allowed_ranges(
        [
            {"start": 300, "end": 400},
            {"start": 100, "end": 200},
            {"start": 190, "end": 310},
        ]
    )
    assert [(r["start_seconds"], r["end_seconds"]) for r in ranges] == [
        (100.0, 400.0)
    ]


def test_range_limits():
    with pytest.raises(ValueError):
        parse_allowed_ranges([{"start": i * 100, "end": i * 100 + 10} for i in range(9)])


def test_empty_brief_normalises_to_none():
    assert validate_content_brief(None) is None
    assert validate_content_brief({}) is None
    assert validate_content_brief({"requirements": [], "allowed_ranges": []}) is None


def test_brief_v2_shape_and_ids():
    brief = validate_content_brief(
        {
            "requirements": [
                {"text": "Expedia must be a major focus of the content.", "kind": "mandatory"},
                {"text": "Payouts start at 10,000 views.", "type": "info_only"},
            ],
            "allowed_ranges": [{"start": "2:56:29", "end": None}],
        }
    )
    assert brief["version"] == 2
    first, second = brief["requirements"]
    assert first["id"] == "r1" and first["type"] == "topic_focus"
    assert first["requires_major"] is True
    assert second["id"] == "r2" and second["type"] == "info_only"
    assert "kind" not in second  # info_only carries no kind


def test_brief_requirement_limits_and_kinds():
    with pytest.raises(ValueError):
        validate_content_brief(
            {"requirements": [{"text": "x", "kind": "sometimes"}]}
        )
    with pytest.raises(ValueError):
        validate_content_brief({"requirements": [{"text": "x" * 501}]})
    with pytest.raises(ValueError):
        validate_content_brief(
            {"requirements": [{"text": f"req {i}"} for i in range(11)]}
        )
    with pytest.raises(ValueError):
        validate_content_brief({"requirements": "not-a-list"})
    with pytest.raises(ValueError):
        validate_content_brief({"requirements": [{"text": "x", "type": "bogus"}]})


def test_typed_requirement_fields_validated():
    brief = validate_content_brief(
        {
            "requirements": [
                {"text": "Mention the code", "type": "must_mention", "phrase": "CREATOR"},
                {"text": "Hook fast", "type": "structure_hook"},
                {"text": "Use the tag", "type": "caption_rule", "hashtags": ["IShowSpeed"]},
            ]
        }
    )
    reqs = brief["requirements"]
    assert reqs[0]["phrase"] == "CREATOR"
    assert reqs[0]["check_in"] == ["transcript", "captions", "title_copy"]
    assert reqs[1]["within_seconds"] == 2.0
    assert reqs[2]["hashtags"] == ["#IShowSpeed"]
    with pytest.raises(ValueError):
        validate_content_brief(
            {"requirements": [{"text": "x", "type": "must_mention", "check_in": ["mars"]}]}
        )


def test_v1_brief_normalises_to_v2():
    v1 = {
        "version": 1,
        "requirements": [
            {"id": "r1", "text": "Expedia must be a major focus of the content.", "kind": "mandatory"},
            {"id": "r2", "text": "The caption must use #IShowSpeed", "kind": "mandatory"},
        ],
        "allowed_ranges": [
            {"start_seconds": 10589.0, "end_seconds": None, "start_label": "2:56:29", "end_label": None}
        ],
    }
    brief = normalize_brief_v2(v1)
    assert brief["version"] == 2
    assert brief["requirements"][0]["type"] == "topic_focus"
    # Presentation-targeting v1 text maps to caption_rule (Addendum A.1).
    assert brief["requirements"][1]["type"] == "caption_rule"
    assert brief["allowed_ranges"][0]["start_seconds"] == 10589.0
    assert brief["allowed_ranges"][0]["end_seconds"] is None
