"""17 s clip floor, and a chosen gaming layout winning over the framing choice."""
from __future__ import annotations

from pathlib import Path

from src import video_utils
from src.media import reframe_plan


def test_short_clip_is_lengthened_forward(monkeypatch):
    monkeypatch.setattr(video_utils, "ffprobe_duration", lambda path: 600.0)
    out = video_utils.enforce_min_clip_duration(Path("x.mp4"), [(197.0, 200.7)], minimum=17.0)
    assert out == [(197.0, 214.0)]


def test_multi_range_clip_extends_its_last_range(monkeypatch):
    monkeypatch.setattr(video_utils, "ffprobe_duration", lambda path: 600.0)
    out = video_utils.enforce_min_clip_duration(Path("x.mp4"), [(10.0, 15.0), (20.0, 25.0)], minimum=17.0)
    assert out == [(10.0, 15.0), (20.0, 32.0)]


def test_clip_at_the_end_of_the_source_starts_earlier(monkeypatch):
    monkeypatch.setattr(video_utils, "ffprobe_duration", lambda path: 100.0)
    out = video_utils.enforce_min_clip_duration(Path("x.mp4"), [(95.0, 99.0)], minimum=17.0)
    assert out[-1][1] == 100.0 and abs(sum(b - a for a, b in out) - 17.0) < 1e-6


def test_long_clips_and_a_zero_floor_are_untouched(monkeypatch):
    monkeypatch.setattr(video_utils, "ffprobe_duration", lambda path: 600.0)
    assert video_utils.enforce_min_clip_duration(Path("x.mp4"), [(0.0, 30.0)], minimum=17.0) == [(0.0, 30.0)]
    monkeypatch.setenv("CLIP_MIN_SECONDS", "0")
    assert video_utils.enforce_min_clip_duration(Path("x.mp4"), [(0.0, 3.0)]) == [(0.0, 3.0)]


def test_multi_range_floor_never_moves_the_first_range_start(monkeypatch):
    # A cold-opened clip: the hook range was transplanted to the front.
    # Extending THAT range backward would open the export with the source's
    # original first sentence instead of the hook (the regression the
    # hook-enforcement pipeline test caught once the floor reached 30 s).
    monkeypatch.setattr(video_utils, "ffprobe_duration", lambda path: 11.0)
    ranges = [(4.9, 7.4), (0.0, 4.9), (7.4, 11.0)]
    out = video_utils.enforce_min_clip_duration(Path("x.mp4"), ranges, minimum=30.0)
    assert out[0] == (4.9, 7.4)
    # The source is exhausted, so the floor yields: no range may overlap
    # another in source time and the total stays the source's 11 s.
    assert abs(sum(b - a for a, b in out) - 11.0) < 1e-6


def test_multi_range_floor_extends_last_range_into_its_lead_in(monkeypatch):
    monkeypatch.setattr(video_utils, "ffprobe_duration", lambda path: 25.0)
    out = video_utils.enforce_min_clip_duration(
        Path("x.mp4"), [(10.0, 15.0), (20.0, 25.0)], minimum=17.0
    )
    # The last range already ends at the source end, so it grows backward
    # to the previous range's end instead of moving the first range.
    assert out == [(10.0, 15.0), (15.0, 25.0)]


def test_default_floor_is_30(monkeypatch):
    # Owner change 2026-11-19: "minimum clip should be 30 sec strict rule".
    monkeypatch.delenv("CLIP_MIN_SECONDS", raising=False)
    assert video_utils.min_clip_seconds() == 30.0


def _edl():
    return {"clip_id": "c", "duration": 15.0, "segments": [{"start": 0.0, "end": 15.0}]}


def test_layout_with_facecam_beats_fit(monkeypatch):
    monkeypatch.setattr(reframe_plan, "get_source_analysis", lambda p, r: {"width": 1920, "height": 1080, "samples": []})
    monkeypatch.setattr(reframe_plan, "_layout_facecam_found", lambda p, a, r: True)
    edl = _edl()
    assert reframe_plan.attach_reframe_plan(edl, Path("s.mp4"), [(30.0, 45.0)], False, framing="fit",
                                            layout_template="facecam_red")
    assert edl["reframe"]["delegate"] == "plain" and edl["reframe"]["layout_template"] == "facecam_red"


def test_without_a_facecam_the_framing_choice_stands(monkeypatch):
    monkeypatch.setattr(reframe_plan, "get_source_analysis", lambda p, r: {"width": 1920, "height": 1080, "samples": []})
    monkeypatch.setattr(reframe_plan, "_layout_facecam_found", lambda p, a, r: False)
    seen = {}
    monkeypatch.setattr(reframe_plan, "build_fit_reframe_plan",
                        lambda a, r, d: seen.setdefault("fit", True) and None)
    edl = _edl()
    reframe_plan.attach_reframe_plan(edl, Path("s.mp4"), [(30.0, 45.0)], False, framing="fit",
                                     layout_template="facecam_red")
    assert seen == {"fit": True} and "reframe" not in edl
