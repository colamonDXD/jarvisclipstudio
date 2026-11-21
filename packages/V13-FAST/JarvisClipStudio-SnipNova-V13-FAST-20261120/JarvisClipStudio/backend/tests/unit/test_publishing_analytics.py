"""Publishing analytics: aggregation over seeded rows.

Every expectation below is hand-computed from the seed data. The
performance side must never treat a missing stats row as zeros — a
post the user has not measured yet contributes nothing.
"""

from __future__ import annotations

import json

from src.services.publishing_service import (
    aggregate_performance,
    aggregate_production,
    build_board,
)


def _clip(clip_id, *, day, niche="gaming", qc="pass", hook=None, checks=None,
          title=None):
    plan = {"niche": niche}
    if hook is not None:
        plan["hook_application"] = {"status": hook}
    qc_report = None
    if checks is not None:
        qc_report = json.dumps({
            "file": {"width": 1080, "height": 1920, "duration": 30.0},
            "checks": checks,
        })
    return {
        "id": clip_id,
        "task_id": "task-1",
        "filename": f"{clip_id}.mp4",
        "hook_title": title,
        "duration": 30.0,
        "qc_status": qc,
        "qc_report": qc_report,
        "clip_plan": json.dumps(plan),
        "niche_override": None,
        "task_niche_override": None,
        "source_title": "Source",
        "created_at": f"{day}T10:00:00+00:00",
        "video_url": f"/tasks/task-1/clips/{clip_id}/file",
    }


COMPLIANT = [
    {"name": "hook_window", "status": "pass"},
    {"name": "caption_presence", "status": "pass"},
]
NONCOMPLIANT = [
    {"name": "hook_window", "status": "warn"},
    {"name": "caption_presence", "status": "pass"},
]

CLIPS = [
    _clip("c1", day="2026-11-07", niche="gaming", hook="cold_opened",
          checks=COMPLIANT, title="First"),
    _clip("c2", day="2026-11-07", niche="gaming", hook="already_leading",
          checks=NONCOMPLIANT, title="Second"),
    _clip("c3", day="2026-11-08", niche="sports", qc="warn", hook=None,
          checks=None, title="Third"),
    _clip("c4", day="2026-11-08", niche="sports", qc=None, hook="no_hook",
          checks=COMPLIANT, title="Fourth"),
]

TASKS = [
    {"id": "t1", "status": "completed",
     "stage_timings_json": json.dumps(
         {"download_seconds": 10.0, "analysis_seconds": 30.0})},
    {"id": "t2", "status": "completed",
     "stage_timings_json": json.dumps(
         {"download_seconds": 20.0, "analysis_seconds": 50.0})},
    {"id": "t3", "status": "failed", "stage_timings_json": None},
]

TOKENS = {"calls": 9, "prompt_tokens": 1000, "completion_tokens": 250,
          "total_tokens": 1250, "unknown_usage_calls": 1}


def test_production_rollups_match_hand_computed_values():
    result = aggregate_production(TASKS, CLIPS, TOKENS)
    assert result["clips_total"] == 4
    assert result["tasks_total"] == 3
    assert result["clips_by_day"] == [
        {"date": "2026-11-07", "clips": 2},
        {"date": "2026-11-08", "clips": 2},
    ]
    assert result["niche_counts"] == [
        {"niche": "gaming", "clips": 2},
        {"niche": "sports", "clips": 2},
    ]
    assert result["qc_distribution"] == {
        "pass": 2, "warn": 1, "fail": 0, "none": 1,
    }
    # c1 + c4 compliant of the three clips with judged checks (c3 has none).
    assert result["compliance"] == {
        "checked_clips": 3, "compliant_clips": 2, "rate": round(2 / 3, 4),
    }
    assert result["hook_distribution"] == [
        {"status": "already_leading", "clips": 1},
        {"status": "cold_opened", "clips": 1},
        {"status": "no_hook", "clips": 1},
        {"status": "not_recorded", "clips": 1},
    ]
    assert result["stage_timings"] == {
        "analysis_seconds": {"mean_seconds": 40.0, "tasks": 2},
        "download_seconds": {"mean_seconds": 15.0, "tasks": 2},
    }
    assert result["token_totals"] == TOKENS


def test_production_empty_inputs_are_honest_zeros():
    result = aggregate_production([], [], TOKENS)
    assert result["clips_total"] == 0
    assert result["clips_by_day"] == []
    assert result["compliance"]["rate"] is None


CAMPAIGNS = [
    {"id": "camp-1", "name": "Whop Push", "platform": "youtube_shorts"},
    {"id": "camp-2", "name": "Reels Test", "platform": "instagram_reels"},
]

POSTS = [
    {"id": "p1", "clip_id": "c1", "platform": "youtube_shorts",
     "post_url": "https://youtube.com/shorts/a", "posted_at": "2026-11-08T12:00:00+00:00",
     "campaign_id": "camp-1", "submission_url": "https://whop.example/sub/1",
     "submission_dismissed": False},
    {"id": "p2", "clip_id": "c2", "platform": "youtube_shorts",
     "post_url": "https://youtube.com/shorts/b", "posted_at": "2026-11-08T13:00:00+00:00",
     "campaign_id": "camp-1", "submission_url": None,
     "submission_dismissed": False},
    {"id": "p3", "clip_id": "c3", "platform": "tiktok",
     "post_url": "https://tiktok.com/@x/video/1", "posted_at": "2026-11-08T14:00:00+00:00",
     "campaign_id": None, "submission_url": None,
     "submission_dismissed": True},
]

STATS = [
    {"post_id": "p1", "views": 1000, "likes": 90, "comments": 12,
     "earnings": 25.5},
    {"post_id": "p2", "views": 500, "likes": None, "comments": 3,
     "earnings": None},
    # p3 deliberately has no stats row: posted but not measured.
]


def test_performance_rollups_only_count_entered_numbers():
    result = aggregate_performance(POSTS, STATS, CAMPAIGNS, CLIPS)
    assert result["entered_by_user"] is True
    assert result["posts_total"] == 3
    assert result["posts_measured"] == 2
    assert result["totals"] == {
        "views": 1500, "likes": 90, "comments": 15, "earnings": 25.5,
    }
    per_campaign = {b["campaign_name"]: b for b in result["per_campaign"]}
    assert per_campaign["Whop Push"]["posts"] == 2
    assert per_campaign["Whop Push"]["views"] == 1500
    assert per_campaign["Whop Push"]["earnings"] == 25.5
    per_clip = {row["post_id"]: row for row in result["per_clip"]}
    assert per_clip["p1"]["title"] == "First"
    assert per_clip["p3"]["stats"] is None


def test_board_marks_the_unsubmitted_whop_post():
    board = build_board(CLIPS, POSTS, CAMPAIGNS, STATS)
    by_id = {entry["clip_id"]: entry for entry in board}
    p2 = next(p for p in by_id["c2"]["posts"] if p["id"] == "p2")
    assert p2["needs_submission"] is True
    assert p2["campaign_name"] == "Whop Push"
    p1 = next(p for p in by_id["c1"]["posts"] if p["id"] == "p1")
    assert p1["needs_submission"] is False
    assert p1["stats"]["views"] == 1000
    # A clip with no posts still appears, with readiness for all platforms.
    assert by_id["c4"]["posts"] == []
    assert set(by_id["c4"]["readiness"]) == {
        "youtube_shorts", "instagram_reels", "tiktok",
    }
