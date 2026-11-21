"""Brief-from-screenshot lane: new schema fields, OCR ingestion, enforcement.

Fixture text is the verbatim tesseract (--psm 6) output on Luffy's actual
campaign-brief screenshot (natural diamonds / Botswana), measured
2026-11-12: 25/25 timecodes recovered by OCR.
"""

from __future__ import annotations

import asyncio
import shutil
from types import SimpleNamespace

import pytest

from src import ai
from src.auto_director import brief_image, content_requirements as cr
from src.auto_director.content_requirements import (
    evaluate_clip_compliance,
    extract_brief_deterministically as extract_brief_draft,
    validate_content_brief,
)
from src.services.video_service import VideoService

FIXTURE_OCR_TEXT = """Key timestamps:
.
01:30 — How diamonds helped transform Botswana
e 03:18 — Diamonds supporting infrastructure and education
e 17:30 — University interview discussing diamond-funded education and student
opportunities
e 39:20 - Boat safari conversation about diamonds, education and wildlife
e 42:51 — Safari discussion about diamonds and Botswana's development
Creative Freedom ii
You're also encouraged to clip other moments, combine footage and create engaging montages
from across the entire video!
The key requirement is that every clip should connect back to natural diamonds and our
campaign messaging, either through dialogue, on-screen text or storytelling.
For example, you could combine university footage with discussions about diamond-funded
education, or use Botswana's beautiful safari visuals alongside relevant positive messaging
about natural diamonds.
A. Content to avoid / flag:
e 0:47 - 0:53 (this should not be included in any content)
Licence plates: 03:13, 03:58, 10:21, 10:24, 20:30, 28:23 — blur/crop.
e Third-party branding: 02:24, 14:42, 29:02, 36:20 — avoid prominent logos.
e Copyright music: 13:55, 18:30 — avoid uncleared audio.
¢ Sensitive topics: 00:26 (war), 09:46 (zebra meat), 21:23 (potential cultural
insensitivity).
© Other: 12:10 (Diamonds Impact presentation) and 19:09 — flag for review; 29:39
(alcohol) — avoid.
"""

EXPECTED_KEYS = {90, 198, 1050, 2360, 2571}
EXPECTED_FLAGS = {
    193: "licence_plate", 238: "licence_plate", 621: "licence_plate",
    624: "licence_plate", 1230: "licence_plate", 1703: "licence_plate",
    144: "branding", 882: "branding", 1742: "branding", 2180: "branding",
    835: "copyright_music", 1110: "copyright_music",
    26: "sensitive_topic", 586: "sensitive_topic", 1283: "sensitive_topic",
    730: "review", 1149: "review", 1779: "alcohol",
}
# ---------------------------------------------------------------- schema


def test_new_fields_normalise_and_labels_survive():
    brief = validate_content_brief(
        {
            "excluded_ranges": [{"start": "0:47", "end": "0:53", "label": "do not use"}],
            "flag_points": [
                {"at": "3:13", "category": "licence_plate", "note": "blur/crop"},
                {"at": "0:26", "category": "mystery", "note": "war"},
            ],
            "key_timestamps": [{"at": "1:30", "description": "How diamonds helped"}],
        }
    )
    assert brief["excluded_ranges"][0]["start_seconds"] == 47
    assert brief["excluded_ranges"][0]["start_label"] == "0:47"
    flags = brief["flag_points"]
    assert [f["at_seconds"] for f in flags] == [26, 193]  # sorted by time
    assert flags[0]["category"] == "other"  # unknown category coerces
    assert flags[0]["note"] == "war"  # the brief's own words survive
    assert brief["key_timestamps"][0]["at_seconds"] == 90
    assert brief["key_timestamps"][0]["description"].startswith("How diamonds")


def test_flag_only_brief_is_not_none_and_plain_brief_unchanged():
    flag_only = validate_content_brief(
        {"flag_points": [{"at": "3:13", "category": "licence_plate"}]}
    )
    assert flag_only is not None
    assert flag_only["flag_points"]
    plain = validate_content_brief(
        {"requirements": [{"text": "Expedia must be a major focus.", "kind": "mandatory"}]}
    )
    # A brief without the new fields keeps its exact pre-lane shape.
    assert set(plain.keys()) == {"version", "requirements", "allowed_ranges"}


def test_new_field_caps_raise():
    with pytest.raises(ValueError):
        validate_content_brief(
            {"flag_points": [{"at": f"0:{i:02d}"} for i in range(41)]}
        )
    with pytest.raises(ValueError):
        validate_content_brief(
            {"key_timestamps": [{"at": f"0:{i:02d}"} for i in range(21)]}
        )
    with pytest.raises(ValueError):
        validate_content_brief({"flag_points": [{"at": "not-a-time"}]})


# -------------------------------------------------------------- algebra


def test_subtract_ranges_zero_tolerance():
    assert cr.subtract_ranges([(40.0, 60.0)], [(47.0, 53.0)]) == [
        (40.0, 47.0),
        (53.0, 60.0),
    ]
    # A boundary touch is not an overlap.
    assert cr.subtract_ranges([(53.0, 60.0)], [(47.0, 53.0)]) == [(53.0, 60.0)]
    # Fully swallowed -> nothing, never a partial leak.
    assert cr.subtract_ranges([(48.0, 52.0)], [(47.0, 53.0)]) == []
    # Sub-0.5s slivers are dropped, not emitted.
    assert cr.subtract_ranges([(46.8, 53.2)], [(47.0, 53.0)]) == []


def test_excluded_overlap_pieces():
    assert cr.excluded_overlap_pieces([(40.0, 60.0)], [(47.0, 53.0)]) == [
        [47.0, 53.0]
    ]
    assert cr.excluded_overlap_pieces([(53.0, 60.0)], [(47.0, 53.0)]) == []


# ------------------------------------------------- fixture extraction


def test_fixture_ocr_text_extracts_the_full_brief():
    brief = extract_brief_draft(FIXTURE_OCR_TEXT)
    assert brief is not None
    # Hard-exclude: 0:47-0:53 and NOT an allowed range (the inversion bug).
    assert [(r["start_seconds"], r["end_seconds"]) for r in brief["excluded_ranges"]] == [
        (47, 53)
    ]
    assert brief["allowed_ranges"] == []
    # Key timestamps 5/5.
    assert {k["at_seconds"] for k in brief["key_timestamps"]} == EXPECTED_KEYS
    # Flag points 18/18 with exact categories.
    got = {f["at_seconds"]: f["category"] for f in brief["flag_points"]}
    assert got == EXPECTED_FLAGS
    # Theme: mandatory topic focus on natural diamonds.
    themes = [
        r
        for r in brief["requirements"]
        if r["type"] == "topic_focus" and r["kind"] == "mandatory"
    ]
    assert len(themes) == 1
    assert themes[0]["topics"] == ["natural diamonds"]


def test_theme_carried_into_existing_mechanisms():
    brief = extract_brief_draft(FIXTURE_OCR_TEXT)
    # The theme rides the existing brief summary (prompt context)...
    assert "natural diamonds" in cr.brief_summary_text(brief).lower()
    # ...and the existing S1 tier machinery: a window with no diamond
    # talk FAILS the mandatory topic focus (Tier C), one saturated with
    # it satisfies it (Tier A).
    off_theme = [
        {
            "start_time": "00:00",
            "end_time": "00:40",
            "start_seconds": 0,
            "end_seconds": 40,
            "text": "today we walk through the town and look at the shops",
        }
    ]
    verdict = cr.evaluate_candidate_requirements(off_theme, 0.0, 40.0, brief)
    assert verdict["tier"] == "C"
    assert verdict["requirements"][0]["status"] == "failed"
    on_theme = [
        {
            "start_time": "00:00",
            "end_time": "00:40",
            "start_seconds": 0,
            "end_seconds": 40,
            "text": (
                "natural diamonds changed this town and natural diamonds "
                "funded the school for natural diamonds workers children"
            ),
        }
    ]
    verdict = cr.evaluate_candidate_requirements(on_theme, 0.0, 40.0, brief)
    assert verdict["tier"] == "A"


# --------------------------------------------------- key candidates


def _transcript_lines():
    lines = []
    for start in range(0, 300, 10):
        lines.append(
            {
                "start_time": f"{start // 60:02d}:{start % 60:02d}",
                "end_time": f"{(start + 10) // 60:02d}:{(start + 10) % 60:02d}",
                "start_seconds": start,
                "end_seconds": start + 10,
                "text": "generic words for this line",
            }
        )
    return lines


def test_key_timestamp_candidate_generated():
    brief = validate_content_brief(
        {"key_timestamps": [{"at": "1:30", "description": "The big moment"}]}
    )
    candidates = cr.generate_key_timestamp_candidates(
        _transcript_lines(), brief, clip_length="medium"
    )
    assert len(candidates) == 1
    # The window expands to whole lines around the named moment and must
    # contain it, within the medium duration bounds (30-60s).
    assert candidates[0]["start_seconds"] <= 90 < candidates[0]["end_seconds"]
    assert 30 <= candidates[0]["end_seconds"] - candidates[0]["start_seconds"] <= 60
    assert "key timestamp" in candidates[0]["reasoning"]
    assert "The big moment" in candidates[0]["reasoning"]


def test_key_timestamp_inside_exclusion_yields_nothing():
    brief = validate_content_brief(
        {
            "key_timestamps": [{"at": "0:50", "description": "Forbidden moment"}],
            "excluded_ranges": [{"start": "0:47", "end": "0:53"}],
        }
    )
    assert (
        cr.generate_key_timestamp_candidates(
            _transcript_lines(), brief, clip_length="medium"
        )
        == []
    )


# ------------------------------------------------------- S1 / S2 gates


def test_s1_excluded_clamp_drops_and_keeps():
    lines = _transcript_lines()

    def seg(start_label, end_label):
        return ai.TranscriptSegment(
            start_time=start_label, end_time=end_label, text="some grounded words"
        )

    # Fully inside the exclusion -> dropped.
    assert (
        ai._clamp_segment_outside_excluded_ranges(
            seg("00:48", "00:52"), [(47.0, 53.0)], lines, 5
        )
        is None
    )
    # Straddling a line-aligned exclusion -> clamped to a surviving
    # piece with ZERO overlap with the excluded span.
    kept = ai._clamp_segment_outside_excluded_ranges(
        seg("00:40", "01:10"), [(50.0, 60.0)], lines, 5
    )
    assert kept is not None
    kept_start = cr.parse_timecode(kept.start_time)
    kept_end = cr.parse_timecode(kept.end_time)
    assert kept_end <= 50.0 or kept_start >= 60.0
    # Clear of the exclusion -> untouched.
    clear = seg("01:10", "01:30")
    assert (
        ai._clamp_segment_outside_excluded_ranges(
            clear, [(47.0, 53.0)], lines, 5
        )
        is clear
    )


def test_s2_gate_drops_segment_swallowed_by_exclusion():
    brief = validate_content_brief({"excluded_ranges": [{"start": "0:47", "end": "0:53"}]})
    segments = [
        {
            "start_time": "00:48",
            "end_time": "00:52",
            "text": "forbidden footage",
            "virality_score": 99,
        },
        {
            "start_time": "01:00",
            "end_time": "01:30",
            "text": "ordinary talk",
            "virality_score": 50,
        },
    ]
    transcript = "\n".join(
        f"[{s // 60:02d}:{s % 60:02d} - {(s + 10) // 60:02d}:{(s + 10) % 60:02d}] Speaker A: words"
        for s in range(0, 300, 10)
    )
    gated, report = VideoService._apply_brief_gate(segments, brief, transcript)
    assert all(s["start_time"] != "00:48" for s in gated)
    assert report["candidates_dropped_range"] == 1


# ------------------------------------------------------- compliance


def _diamonds_lines():
    return [
        {
            "start_time": "00:00",
            "end_time": "01:00",
            "start_seconds": 0,
            "end_seconds": 60,
            "text": "we talk about natural diamonds and Botswana schools",
        },
        {
            "start_time": "01:00",
            "end_time": "02:00",
            "start_seconds": 60,
            "end_seconds": 120,
            "text": "more talk about natural diamonds funded classrooms",
        },
    ]


def test_compliance_excluded_overlap_is_non_compliant():
    brief = validate_content_brief(
        {
            "excluded_ranges": [{"start": "0:47", "end": "0:53"}],
            "flag_points": [{"at": "3:13", "category": "licence_plate", "note": "blur/crop"}],
        }
    )
    report = evaluate_clip_compliance(brief, [(40.0, 60.0)], _diamonds_lines())
    assert report["status"] == "non_compliant"
    assert report["ranges"]["excluded_violations"] == [[47.0, 53.0]]
    assert report["needs_review"] is False  # the plate point is elsewhere


def test_compliance_flag_point_stamps_needs_review():
    brief = validate_content_brief(
        {
            "excluded_ranges": [{"start": "0:47", "end": "0:53"}],
            "flag_points": [{"at": "3:13", "category": "licence_plate", "note": "blur/crop"}],
        }
    )
    report = evaluate_clip_compliance(brief, [(180.0, 230.0)], _diamonds_lines())
    assert report["needs_review"] is True
    assert report["flags"][0]["category"] == "licence_plate"
    assert report["flags"][0]["at_seconds"] == 193
    assert report["status"] == "uncertain"  # flagged, never silently compliant
    summary = cr.compliance_summary_for(report)
    assert summary["needs_review"] is True
    assert summary["flag_points"] == 1
    assert summary["flag_categories"] == ["licence_plate"]


# ------------------------------------------------------------- OCR


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract absent")
def test_ocr_reads_the_real_screenshot():
    import pathlib

    path = pathlib.Path.home() / "workspace/user/media_library/image/image-479.png"
    if not path.exists():
        pytest.skip("fixture screenshot not present in this environment")
    text, available = brief_image.ocr_image_bytes(path.read_bytes())
    assert available is True
    assert text is not None
    for code in ("01:30", "03:18", "17:30", "39:20", "42:51", "0:47", "0:53"):
        assert code in text


def test_ocr_unreadable_bytes():
    text, available = brief_image.ocr_image_bytes(b"this is not an image")
    assert text is None
    assert available is True  # binary present, image unreadable


def test_ocr_binary_absent(monkeypatch):
    monkeypatch.setattr(brief_image.shutil, "which", lambda name: None)
    text, available = brief_image.ocr_image_bytes(b"whatever")
    assert text is None and available is False


# ------------------------------------------------------------ route


class _Upload:
    def __init__(self, filename, data):
        self.filename = filename
        self._data = data

    async def read(self):
        return self._data


def _run_route(monkeypatch, upload):
    from unittest.mock import AsyncMock

    from src.api.routes import briefs as briefs_route

    monkeypatch.setattr(
        briefs_route,
        "resolve_authenticated_user_id",
        AsyncMock(return_value="user-1"),
    )
    monkeypatch.setattr(
        briefs_route, "extract_brief_with_llm", AsyncMock(return_value=None)
    )
    return asyncio.run(
        briefs_route.extract_brief_image(
            request=SimpleNamespace(headers={}), db=None, file=upload
        )
    )


def test_extract_image_route_full_pipeline(monkeypatch, tmp_path):
    import pathlib

    fixture = pathlib.Path.home() / "workspace/user/media_library/image/image-479.png"
    if not fixture.exists() or shutil.which("tesseract") is None:
        pytest.skip("fixture or tesseract unavailable")
    result = _run_route(monkeypatch, _Upload("brief.png", fixture.read_bytes()))
    assert "Key timestamps" in result["brief_text"]
    assert result["ocr"]["characters"] > 500
    brief = result["content_brief"]
    assert brief["excluded_ranges"][0]["start_seconds"] == 47
    assert len(brief["flag_points"]) == 18
    assert len(brief["key_timestamps"]) == 5


def test_extract_image_route_rejects_bad_extension(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        _run_route(monkeypatch, _Upload("brief.gif", b"GIF89a"))
    assert excinfo.value.status_code == 400


def test_extract_image_route_rejects_oversize(monkeypatch):
    from fastapi import HTTPException

    big = b"x" * (brief_image.MAX_BRIEF_IMAGE_BYTES + 1)
    with pytest.raises(HTTPException) as excinfo:
        _run_route(monkeypatch, _Upload("brief.png", big))
    assert excinfo.value.status_code == 413
