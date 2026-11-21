"""
Publishing service: readiness rules, board assembly, analytics.

Two kinds of numbers live here and they are never mixed:

* Production facts — read from the rows the pipeline already stored
  (clip dimensions/duration from the QC report, compliance checks,
  hook outcomes on the plan, per-task stage timings, provider-reported
  token usage). Computed, never invented.
* Performance numbers — ONLY the values the user typed into the
  workspace after posting by hand. Aggregations carry
  ``entered_by_user`` so the UI can label them honestly.

Readiness rules encode the platforms' own published limits (see
PLATFORM_RULES comments); a check whose input was never recorded is
``unknown``, never guessed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Platform rules
#
# YouTube Shorts: a video counts as a Short when it is square or vertical
# and up to 3 minutes long (YouTube Help, "Upload YouTube Shorts", rule in
# force for uploads since 2024-10-15).
# Instagram Reels: shapes from 1.91:1 to 9:16 are accepted; reels up to
# 20 minutes can be recorded/uploaded, but Instagram says reels over
# 3 minutes are not recommended to new audiences (Instagram Help, "Reel
# size" / "Record a reel").
# TikTok: 9:16 is the native shape; videos recorded in the app can be up
# to 10 minutes, uploads may allow more depending on the account (TikTok
# Support, camera/creator tools) — so past 10 minutes is a warning about
# the account limit, not a hard rejection.
# ---------------------------------------------------------------------------

PLATFORMS = ("youtube_shorts", "instagram_reels", "tiktok")

PLATFORM_LABELS = {
    "youtube_shorts": "YouTube Shorts",
    "instagram_reels": "Instagram Reels",
    "tiktok": "TikTok",
    "other": "Other",
}

# Quality Director compliance checks judged from the QC report.
COMPLIANCE_CHECKS = (
    "hook_window",
    "cut_rate",
    "music",
    "caption_presence",
    "caption_hold",
)

_STATUS_RANK = {"pass": 0, "unknown": 1, "warn": 2, "fail": 3}


def _check(name: str, status: str, detail: str) -> Dict[str, Any]:
    return {"name": name, "status": status, "detail": detail}


def _aspect_check(platform: str, width: Optional[float], height: Optional[float]) -> Dict[str, Any]:
    if not width or not height:
        return _check(
            "aspect", "unknown",
            "The rendered size was not recorded for this clip, so its shape cannot be checked",
        )
    w, h = float(width), float(height)
    if platform == "youtube_shorts":
        if h > w:
            return _check("aspect", "pass", "Vertical — fills the Shorts player")
        if h == w:
            return _check(
                "aspect", "warn",
                "Square counts as a Short, but it will not fill the player",
            )
        return _check(
            "aspect", "fail",
            "Landscape — YouTube would treat this as a regular video, not a Short",
        )
    if platform == "instagram_reels":
        if h > w:
            if h / w >= 1.6:
                return _check("aspect", "pass", "Vertical — fills the Reels player")
            return _check(
                "aspect", "warn",
                "Vertical, but squarer than 9:16 — Instagram pads it in the feed",
            )
        if h == w:
            return _check(
                "aspect", "warn",
                "Square is within Instagram's accepted range but shows letterboxed",
            )
        if w / h <= 1.91:
            return _check(
                "aspect", "warn",
                "Landscape within Instagram's accepted range (up to 1.91:1), but it will not fill the screen",
            )
        return _check(
            "aspect", "fail",
            "Wider than the widest Reel shape Instagram accepts (1.91:1)",
        )
    # tiktok
    if h > w:
        return _check("aspect", "pass", "Vertical — TikTok's native shape")
    if h == w:
        return _check(
            "aspect", "warn",
            "Square plays on TikTok, but vertical fills the screen",
        )
    return _check(
        "aspect", "warn",
        "Landscape plays with black bars on TikTok; vertical is the native shape",
    )


def _duration_check(platform: str, duration: Optional[float]) -> Dict[str, Any]:
    if not duration or float(duration) <= 0:
        return _check(
            "duration", "unknown",
            "The clip duration was not recorded, so its length cannot be checked",
        )
    d = float(duration)
    if platform == "youtube_shorts":
        if d <= 180:
            return _check("duration", "pass", "Within YouTube's 3-minute Shorts limit")
        return _check(
            "duration", "fail",
            "Over YouTube's 3-minute Shorts limit — it would upload as a regular video",
        )
    if platform == "instagram_reels":
        if d <= 180:
            return _check("duration", "pass", "Within the 3 minutes Instagram recommends for reaching new viewers")
        if d <= 1200:
            return _check(
                "duration", "warn",
                "Instagram accepts it, but Reels over 3 minutes are not recommended to new audiences",
            )
        return _check("duration", "fail", "Over Instagram's 20-minute Reel limit")
    # tiktok
    if d <= 600:
        return _check("duration", "pass", "Within TikTok's 10-minute in-app limit")
    return _check(
        "duration", "warn",
        "Over 10 minutes — some TikTok accounts cannot post videos this long",
    )


def _captions_check(captions_present: Optional[bool]) -> Dict[str, Any]:
    if captions_present is True:
        return _check("captions", "pass", "Captions are burned into the render")
    if captions_present is False:
        return _check(
            "captions", "warn",
            "No captions were recorded for this render — most short-form viewers watch on mute",
        )
    return _check(
        "captions", "unknown",
        "Caption presence was not recorded for this clip",
    )


def _quality_check(qc_status: Optional[str]) -> Dict[str, Any]:
    if qc_status == "pass":
        return _check("quality", "pass", "Quality control passed this clip")
    if qc_status == "warn":
        return _check("quality", "warn", "Quality control flagged warnings on this clip")
    if qc_status == "fail":
        return _check("quality", "fail", "Quality control failed this clip")
    return _check("quality", "unknown", "No quality-control result was recorded")


def _brief_check(clip: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Campaign-brief readiness check, from the stored compliance report.

    Only for clips whose task carries a brief: non-compliant blocks every
    platform, uncertain warns. No brief (or no report) -> no check, and
    the board stays exactly as it was before the feature.
    """
    if not clip.get("task_content_brief"):
        return None
    status = clip.get("compliance_status")
    if status == "non_compliant":
        report = _parse_json(clip.get("compliance_report"))
        failed_text = ""
        for entry in report.get("requirements") or []:
            if entry.get("kind") == "mandatory" and entry.get("status") == "failed":
                failed_text = entry.get("text") or entry.get("id") or ""
                break
        detail = "Breaks the campaign brief"
        if failed_text:
            detail += f": {failed_text}"
        return _check("brief", "fail", detail)
    if status == "uncertain":
        return _check(
            "brief", "warn", "Brief compliance uncertain — review before posting"
        )
    if status == "compliant":
        return _check("brief", "pass", "Satisfies the campaign brief")
    return None


def brief_summary_for_clip(clip: Dict[str, Any]) -> Dict[str, Any]:
    """Per-clip brief block for the board (design §5.4)."""
    report = _parse_json(clip.get("compliance_report"))
    failed, uncertain = [], []
    for entry in report.get("requirements") or []:
        if entry.get("kind") != "mandatory":
            continue
        if entry.get("status") == "failed":
            failed.append(entry.get("id"))
        elif entry.get("status") in ("uncertain", "not_checked"):
            uncertain.append(entry.get("id"))
    return {
        "has_brief": bool(clip.get("task_content_brief")),
        "status": clip.get("compliance_status"),
        "mandatory_failed": failed,
        "mandatory_uncertain": uncertain,
    }


def compute_readiness(
    facts: Dict[str, Any], platform: str, clip: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Per-platform readiness for one clip, judged from recorded facts."""
    checks = [
        _aspect_check(platform, facts.get("width"), facts.get("height")),
        _duration_check(platform, facts.get("duration")),
        _captions_check(facts.get("captions_present")),
        _quality_check(facts.get("qc_status")),
    ]
    if clip is not None:
        brief_check = _brief_check(clip)
        if brief_check is not None:
            checks.append(brief_check)
    worst = max(_STATUS_RANK[c["status"]] for c in checks)
    overall = {0: "ready", 1: "unknown", 2: "warning", 3: "blocked"}[worst]
    return {"platform": platform, "status": overall, "checks": checks}


# ---------------------------------------------------------------------------
# Clip facts from stored rows
# ---------------------------------------------------------------------------


def _parse_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def clip_facts(clip: Dict[str, Any]) -> Dict[str, Any]:
    """Distil the stored clip row into the facts the workspace judges.

    Dimensions and caption presence come from the QC report the pipeline
    stored on the clip (its ``file`` facts and ``caption_presence``
    check). Anything not recorded stays None and reads as ``unknown``
    downstream — never guessed.
    """
    qc = _parse_json(clip.get("qc_report"))
    file_info = qc.get("file") if isinstance(qc.get("file"), dict) else {}
    checks = {}
    for entry in qc.get("checks") or []:
        if isinstance(entry, dict) and entry.get("name"):
            checks[str(entry["name"])] = entry

    caption_status = (checks.get("caption_presence") or {}).get("status")
    if caption_status == "pass":
        captions_present: Optional[bool] = True
    elif caption_status in ("warn", "fail"):
        captions_present = False
    else:
        captions_present = None

    plan = _parse_json(clip.get("clip_plan"))
    hook_application = plan.get("hook_application")
    if not isinstance(hook_application, dict):
        hook_application = {}
    niche = (
        plan.get("niche")
        or clip.get("niche_override")
        or clip.get("task_niche_override")
        or None
    )
    return {
        "clip_id": clip.get("id"),
        "task_id": clip.get("task_id"),
        "title": clip.get("hook_title") or None,
        "filename": clip.get("filename"),
        "source_title": clip.get("source_title"),
        "duration": clip.get("duration") or file_info.get("duration"),
        "width": file_info.get("width"),
        "height": file_info.get("height"),
        "niche": niche,
        "qc_status": clip.get("qc_status"),
        "captions_present": captions_present,
        "hook_status": hook_application.get("status") or None,
        "hook_reason": hook_application.get("reason") or None,
        "created_at": clip.get("created_at"),
        "video_url": clip.get("video_url"),
    }


def compliance_summary(clip: Dict[str, Any]) -> Dict[str, Any]:
    """Quality Director compliance verdicts recorded on one clip."""
    qc = _parse_json(clip.get("qc_report"))
    checks = {
        str(entry["name"]): entry.get("status")
        for entry in (qc.get("checks") or [])
        if isinstance(entry, dict) and entry.get("name")
    }
    judged = {
        name: checks[name]
        for name in COMPLIANCE_CHECKS
        if checks.get(name) not in (None, "skip")
    }
    return {
        "checked": bool(judged),
        "compliant": bool(judged) and all(s == "pass" for s in judged.values()),
        "checks": judged,
    }


def needs_submission(post: Dict[str, Any]) -> bool:
    """Whop rule: a posted clip still owes its campaign link submission."""
    if post.get("submission_url"):
        return False
    if post.get("submission_dismissed"):
        return False
    return True


def build_board(
    clips: List[Dict[str, Any]],
    posts: List[Dict[str, Any]],
    campaigns: List[Dict[str, Any]],
    stats: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Assemble the ready-to-post board: one entry per finished clip."""
    campaign_names = {c["id"]: c.get("name") for c in campaigns}
    stats_by_post = {s["post_id"]: s for s in stats}
    posts_by_clip: Dict[str, List[Dict[str, Any]]] = {}
    for post in posts:
        enriched = {
            **post,
            "campaign_name": campaign_names.get(post.get("campaign_id") or ""),
            "needs_submission": needs_submission(post),
            "stats": stats_by_post.get(post["id"]),
        }
        posts_by_clip.setdefault(post["clip_id"], []).append(enriched)

    board = []
    for clip in clips:
        facts = clip_facts(clip)
        board.append(
            {
                **facts,
                "compliance": compliance_summary(clip),
                "brief": brief_summary_for_clip(clip),
                "readiness": {
                    platform: compute_readiness(facts, platform, clip)
                    for platform in PLATFORMS
                },
                "posts": posts_by_clip.get(clip["id"], []),
            }
        )
    return board


# ---------------------------------------------------------------------------
# Analytics aggregation (pure over row dicts)
# ---------------------------------------------------------------------------


def _day_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value)
    return text[:10] if len(text) >= 10 else None


def aggregate_production(
    tasks: List[Dict[str, Any]],
    clips: List[Dict[str, Any]],
    token_totals: Dict[str, Any],
) -> Dict[str, Any]:
    """Production aggregates from pipeline-stored rows only."""
    clips_by_day: Dict[str, int] = {}
    niche_counts: Dict[str, int] = {}
    qc_distribution: Dict[str, int] = {"pass": 0, "warn": 0, "fail": 0, "none": 0}
    hook_distribution: Dict[str, int] = {}
    compliance_checked = 0
    compliance_passed = 0
    for clip in clips:
        day = _day_key(clip.get("created_at"))
        if day:
            clips_by_day[day] = clips_by_day.get(day, 0) + 1
        facts = clip_facts(clip)
        niche = facts["niche"] or "unclassified"
        niche_counts[niche] = niche_counts.get(niche, 0) + 1
        status = clip.get("qc_status") or "none"
        if status not in qc_distribution:
            status = "none"
        qc_distribution[status] += 1
        hook_status = facts["hook_status"] or "not_recorded"
        hook_distribution[hook_status] = hook_distribution.get(hook_status, 0) + 1
        summary = compliance_summary(clip)
        if summary["checked"]:
            compliance_checked += 1
            if summary["compliant"]:
                compliance_passed += 1

    stage_totals: Dict[str, float] = {}
    stage_counts: Dict[str, int] = {}
    for task in tasks:
        timings = _parse_json(task.get("stage_timings_json"))
        for stage, seconds in timings.items():
            if isinstance(seconds, (int, float)) and seconds >= 0:
                stage_totals[stage] = stage_totals.get(stage, 0.0) + float(seconds)
                stage_counts[stage] = stage_counts.get(stage, 0) + 1
    stage_means = {
        stage: {
            "mean_seconds": round(stage_totals[stage] / stage_counts[stage], 2),
            "tasks": stage_counts[stage],
        }
        for stage in sorted(stage_totals)
    }

    return {
        "clips_total": len(clips),
        "tasks_total": len(tasks),
        "clips_by_day": [
            {"date": day, "clips": clips_by_day[day]}
            for day in sorted(clips_by_day)
        ],
        "niche_counts": [
            {"niche": niche, "clips": count}
            for niche, count in sorted(
                niche_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "qc_distribution": qc_distribution,
        "compliance": {
            "checked_clips": compliance_checked,
            "compliant_clips": compliance_passed,
            "rate": (
                round(compliance_passed / compliance_checked, 4)
                if compliance_checked
                else None
            ),
        },
        "hook_distribution": [
            {"status": status, "clips": count}
            for status, count in sorted(
                hook_distribution.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "stage_timings": stage_means,
        "token_totals": token_totals,
    }


def aggregate_performance(
    posts: List[Dict[str, Any]],
    stats: List[Dict[str, Any]],
    campaigns: List[Dict[str, Any]],
    clips: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Rollups over ONLY the numbers the user typed in.

    Posts without a stats row count as posted-but-unmeasured and never
    contribute a zero: a missing number is not a zero.
    """
    stats_by_post = {s["post_id"]: s for s in stats}
    clip_titles = {
        c["id"]: (clip_facts(c)["title"] or c.get("filename"))
        for c in clips
    }
    campaign_names = {c["id"]: c.get("name") for c in campaigns}

    totals = {"views": 0, "likes": 0, "comments": 0, "earnings": 0.0}
    measured_posts = 0
    per_clip: List[Dict[str, Any]] = []
    per_campaign: Dict[str, Dict[str, Any]] = {}
    for post in posts:
        entry = stats_by_post.get(post["id"])
        row = {
            "post_id": post["id"],
            "clip_id": post["clip_id"],
            "title": clip_titles.get(post["clip_id"]),
            "platform": post["platform"],
            "post_url": post.get("post_url") or "",
            "posted_at": post.get("posted_at"),
            "campaign_id": post.get("campaign_id"),
            "campaign_name": campaign_names.get(post.get("campaign_id") or ""),
            "stats": entry,
        }
        per_clip.append(row)
        if entry is None:
            continue
        measured_posts += 1
        for field in ("views", "likes", "comments"):
            if entry.get(field) is not None:
                totals[field] += int(entry[field])
        if entry.get("earnings") is not None:
            totals["earnings"] += float(entry["earnings"])
        key = post.get("campaign_id") or ""
        bucket = per_campaign.setdefault(
            key,
            {
                "campaign_id": post.get("campaign_id"),
                "campaign_name": campaign_names.get(key),
                "posts": 0,
                "views": 0,
                "likes": 0,
                "comments": 0,
                "earnings": 0.0,
            },
        )
        bucket["posts"] += 1
        for field in ("views", "likes", "comments"):
            if entry.get(field) is not None:
                bucket[field] += int(entry[field])
        if entry.get("earnings") is not None:
            bucket["earnings"] += float(entry["earnings"])

    totals["earnings"] = round(totals["earnings"], 2)
    for bucket in per_campaign.values():
        bucket["earnings"] = round(bucket["earnings"], 2)

    return {
        "entered_by_user": True,
        "posts_total": len(posts),
        "posts_measured": measured_posts,
        "totals": totals,
        "per_clip": per_clip,
        "per_campaign": sorted(
            per_campaign.values(),
            key=lambda b: (b["campaign_name"] is None, b["campaign_name"] or ""),
        ),
    }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
