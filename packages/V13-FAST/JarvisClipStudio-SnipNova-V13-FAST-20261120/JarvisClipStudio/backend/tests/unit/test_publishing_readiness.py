"""Publishing readiness: per-platform checks judged from recorded facts.

The rule inputs are the facts the pipeline already stored (QC report
file facts + caption check, clip duration, QC status). A fact that was
never recorded must come out ``unknown`` — readiness never guesses.
"""

from __future__ import annotations

import json

from src.services.publishing_service import (
    clip_facts,
    compliance_summary,
    compute_readiness,
    needs_submission,
)

VERTICAL = {"width": 1080, "height": 1920, "duration": 42.0,
            "captions_present": True, "qc_status": "pass"}
SQUARE = {"width": 1080, "height": 1080, "duration": 42.0,
          "captions_present": True, "qc_status": "pass"}
LANDSCAPE = {"width": 1920, "height": 1080, "duration": 42.0,
             "captions_present": True, "qc_status": "pass"}


def _by_name(result):
    return {check["name"]: check["status"] for check in result["checks"]}


def test_vertical_clip_is_ready_everywhere():
    for platform in ("youtube_shorts", "instagram_reels", "tiktok"):
        result = compute_readiness(VERTICAL, platform)
        assert result["status"] == "ready"
        assert all(s == "pass" for s in _by_name(result).values())


def test_square_is_a_warning_not_a_block_for_shorts():
    result = compute_readiness(SQUARE, "youtube_shorts")
    assert _by_name(result)["aspect"] == "warn"
    assert result["status"] == "warning"


def test_landscape_fails_shorts_but_only_warns_on_tiktok():
    shorts = compute_readiness(LANDSCAPE, "youtube_shorts")
    assert _by_name(shorts)["aspect"] == "fail"
    assert shorts["status"] == "blocked"
    tiktok = compute_readiness(LANDSCAPE, "tiktok")
    assert _by_name(tiktok)["aspect"] == "warn"
    assert tiktok["status"] == "warning"


def test_shorts_duration_limit_is_three_minutes():
    facts = {**VERTICAL, "duration": 181.0}
    assert _by_name(compute_readiness(facts, "youtube_shorts"))["duration"] == "fail"
    facts = {**VERTICAL, "duration": 180.0}
    assert _by_name(compute_readiness(facts, "youtube_shorts"))["duration"] == "pass"


def test_reels_duration_bands():
    four_minutes = {**VERTICAL, "duration": 240.0}
    assert _by_name(compute_readiness(four_minutes, "instagram_reels"))["duration"] == "warn"
    twenty_five = {**VERTICAL, "duration": 1500.0}
    assert _by_name(compute_readiness(twenty_five, "instagram_reels"))["duration"] == "fail"


def test_tiktok_duration_warns_past_ten_minutes():
    facts = {**VERTICAL, "duration": 660.0}
    assert _by_name(compute_readiness(facts, "tiktok"))["duration"] == "warn"


def test_unrecorded_facts_are_unknown_never_guessed():
    facts = {"width": None, "height": None, "duration": None,
             "captions_present": None, "qc_status": None}
    result = compute_readiness(facts, "youtube_shorts")
    assert result["status"] == "unknown"
    assert all(s == "unknown" for s in _by_name(result).values())


def test_missing_captions_warn_and_failed_qc_blocks():
    facts = {**VERTICAL, "captions_present": False}
    assert _by_name(compute_readiness(facts, "youtube_shorts"))["captions"] == "warn"
    facts = {**VERTICAL, "qc_status": "fail"}
    result = compute_readiness(facts, "youtube_shorts")
    assert _by_name(result)["quality"] == "fail"
    assert result["status"] == "blocked"


def _clip_row(**overrides):
    qc_report = {
        "status": "pass",
        "file": {"duration": 42.0, "width": 1080, "height": 1920},
        "checks": [
            {"name": "caption_presence", "status": "pass"},
            {"name": "hook_window", "status": "pass"},
            {"name": "cut_rate", "status": "pass"},
            {"name": "music", "status": "skip"},
            {"name": "caption_hold", "status": "warn"},
        ],
    }
    plan = {
        "niche": "gaming",
        "hook_application": {"status": "cold_opened", "reason": "hook was late"},
    }
    row = {
        "id": "clip-1",
        "task_id": "task-1",
        "filename": "clip_0.mp4",
        "hook_title": "He said what?!",
        "duration": 42.0,
        "qc_status": "pass",
        "qc_report": json.dumps(qc_report),
        "clip_plan": json.dumps(plan),
        "niche_override": None,
        "task_niche_override": None,
        "source_title": "Big stream",
        "created_at": "2026-11-08T10:00:00+00:00",
        "video_url": "/tasks/task-1/clips/clip-1/file",
    }
    row.update(overrides)
    return row


def test_clip_facts_reads_the_stored_qc_report_and_plan():
    facts = clip_facts(_clip_row())
    assert facts["width"] == 1080 and facts["height"] == 1920
    assert facts["captions_present"] is True
    assert facts["niche"] == "gaming"
    assert facts["hook_status"] == "cold_opened"
    assert facts["title"] == "He said what?!"


def test_clip_facts_falls_back_to_overrides_then_unclassified():
    facts = clip_facts(_clip_row(clip_plan=None, niche_override="sports"))
    assert facts["niche"] == "sports"
    facts = clip_facts(_clip_row(clip_plan=None, niche_override=None))
    assert facts["niche"] is None


def test_clip_facts_without_qc_report_stays_unknown():
    facts = clip_facts(_clip_row(qc_report=None))
    assert facts["width"] is None and facts["captions_present"] is None


def test_caption_absence_in_qc_report_means_no_captions():
    qc = {"file": {}, "checks": [{"name": "caption_presence", "status": "warn"}]}
    facts = clip_facts(_clip_row(qc_report=json.dumps(qc)))
    assert facts["captions_present"] is False


def test_compliance_summary_ignores_skips_and_requires_all_pass():
    summary = compliance_summary(_clip_row())
    assert summary["checked"] is True
    # caption_hold warned, so the clip is not fully compliant.
    assert summary["compliant"] is False
    assert summary["checks"] == {
        "hook_window": "pass",
        "cut_rate": "pass",
        "caption_presence": "pass",
        "caption_hold": "warn",
    }


def test_compliance_summary_unchecked_without_report():
    summary = compliance_summary(_clip_row(qc_report=None))
    assert summary == {"checked": False, "compliant": False, "checks": {}}


def test_needs_submission_until_link_recorded_or_dismissed():
    assert needs_submission({"submission_url": None, "submission_dismissed": False})
    assert not needs_submission({"submission_url": "https://whop.example/x",
                                  "submission_dismissed": False})
    assert not needs_submission({"submission_url": None, "submission_dismissed": True})
