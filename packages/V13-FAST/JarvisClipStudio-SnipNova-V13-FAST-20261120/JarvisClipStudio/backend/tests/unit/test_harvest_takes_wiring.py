"""Wiring tests: campaign rules inside the S2 brief gate; slate pre-filter."""

from src.auto_director.content_requirements import normalize_brief_v2
from src.services.video_service import VideoService


def _brief():
    return normalize_brief_v2(
        {
            "campaign_rules": {
                "must_mention": ["whop"],
                "banned": ["spoiler"],
                "min_len": 20,
                "max_len": 90,
            }
        }
    )


def test_brief_gate_enforces_campaign_rules_with_reasons():
    brief = _brief()
    assert brief is not None and "campaign_rules" in brief
    transcript = (
        "[00:00 - 00:40] he posted the clip on whop and everyone loved it\n"
        "[00:40 - 01:20] a massive spoiler about the ending nobody wanted\n"
        "[01:20 - 02:00] another great moment on whop today"
    )
    segments = [
        {"start_time": "00:00", "end_time": "00:40", "text": "he posted the clip on whop and everyone loved it"},
        {"start_time": "00:40", "end_time": "01:20", "text": "a massive spoiler about the ending nobody wanted"},
        {"start_time": "01:20", "end_time": "02:00", "text": "another great moment on whop today"},
    ]
    gated, report = VideoService._apply_brief_gate(segments, brief, transcript)
    kept_starts = [s["start_time"] for s in gated]
    assert kept_starts == ["00:00", "01:20"]
    rejections = report.get("campaign_rejections") or []
    assert len(rejections) == 1
    assert rejections[0]["start_time"] == "00:40"
    assert any("banned" in r for r in rejections[0]["reasons"])
    assert report.get("candidates_dropped_campaign") == 1


def test_brief_gate_without_campaign_rules_unchanged():
    brief = normalize_brief_v2(
        {"requirements": [{"type": "topic_focus", "text": "whop"}]}
    )
    assert brief is not None and "campaign_rules" not in brief
    transcript = "[00:00 - 00:40] talking about whop all day long"
    segments = [
        {"start_time": "00:00", "end_time": "00:40", "text": "talking about whop all day long"}
    ]
    gated, report = VideoService._apply_brief_gate(segments, brief, transcript)
    assert len(gated) == 1
    assert "campaign_rejections" not in report


def test_drop_slate_segments_uses_map(monkeypatch, tmp_path):
    import src.media.slate_exclusion as slate

    monkeypatch.setattr(slate, "static_ranges", lambda path: [(10.0, 26.0)])
    segments = [
        {"start_time": "00:00", "end_time": "00:08", "text": "real"},
        {"start_time": "00:12", "end_time": "00:24", "text": "over the BRB card"},
        {"start_time": "00:30", "end_time": "00:40", "text": "gameplay"},
    ]
    kept = VideoService._drop_slate_segments(tmp_path / "x.mp4", segments)
    assert [s["text"] for s in kept] == ["real", "gameplay"]


def test_drop_slate_segments_switch_off(monkeypatch, tmp_path):
    import src.media.slate_exclusion as slate

    monkeypatch.setenv("SLATE_EXCLUSION", "off")
    monkeypatch.setattr(slate, "static_ranges", lambda path: [(0.0, 100.0)])
    segments = [{"start_time": "00:00", "end_time": "00:30", "text": "kept"}]
    kept = VideoService._drop_slate_segments(tmp_path / "x.mp4", segments)
    assert kept == segments
