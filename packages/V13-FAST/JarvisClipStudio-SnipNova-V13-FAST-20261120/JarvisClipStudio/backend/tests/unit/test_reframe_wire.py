"""Lane F: subject-aware reframe wiring for the EDL render path.

Covers the planner (identity association + switch hysteresis, per-shot
modes, the fallback ladder, the delegate-plain seam, the per-source
analysis cache), the EDL graph consumption (legacy EDLs unchanged,
reframe segments composed from the plan), and the warn-only QC
subject-in-frame check.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.media import clip_qc, reframe_plan
from src.media.edl_render import build_edl_filter_graph
from src.media.scene_context import SceneEvidence

W, H = 1920, 1080


def _samples(boxes_per_sample, fps=3.0):
    return [(i / fps, list(boxes)) for i, boxes in enumerate(boxes_per_sample)]


# --- Identity association + hysteresis ---------------------------------------


def test_association_links_across_a_missed_detection():
    face = (100.0, 100.0, 80.0, 100.0)
    samples = _samples([[face], [face], [], [face], [face]])
    tracks = reframe_plan.associate_tracks(samples)
    assert len(tracks) == 1
    assert tracks[0].present_count == 4


def test_association_separates_two_people():
    samples = _samples(
        [[(100.0, 100.0, 80.0, 100.0), (900.0, 100.0, 80.0, 100.0)]] * 6
    )
    tracks = reframe_plan.associate_tracks(samples)
    assert len(tracks) == 2
    assert all(track.present_count == 6 for track in tracks)


def test_transient_challenger_never_steals_the_subject():
    subject = (100.0, 100.0, 80.0, 100.0)
    leaner = (400.0, 100.0, 130.0, 160.0)  # bigger, but only for 2 samples
    rows = [[subject]] * 12
    rows[5] = [subject, leaner]
    rows[6] = [subject, leaner]
    samples = _samples(rows)
    series = reframe_plan.subject_series(samples, reframe_plan.associate_tracks(samples))
    assert all(box == subject for box in series if box is not None)


def test_sustained_challenger_takes_over_after_the_hysteresis_window():
    first = (100.0, 100.0, 160.0, 200.0)   # dominant while present
    second = (900.0, 100.0, 80.0, 100.0)   # smaller, but never leaves
    # The first subject leaves after sample 8; the persistent second must
    # become the subject, without any early hop while the first still
    # dominates the remaining-shot score.
    rows = [[first, second]] * 9 + [[second]] * 9
    samples = _samples(rows)
    series = reframe_plan.subject_series(samples, reframe_plan.associate_tracks(samples))
    assert series[0] == first
    assert series[5] == first
    assert series[-1] == second


def test_subject_series_all_missing_is_all_none():
    samples = _samples([[], [], []])
    assert reframe_plan.subject_series(samples, []) == [None, None, None]


# --- Fallback ladder -----------------------------------------------------------


def _evidence(t, **kwargs):
    return SceneEvidence(time=t, **kwargs)


def test_fallback_order_saliency_then_previous_then_center():
    entry = {"start": 0.0, "end": 4.0}
    context = [
        _evidence(1.0, motion=0.5, motion_center=0.75, detail_left=0.6, detail_right=0.9),
        _evidence(2.0, motion=0.5, motion_center=0.75, detail_left=0.6, detail_right=0.9),
    ]
    x, reason = reframe_plan._fallback_crop(entry, context, W, 607, None)
    assert reason == "saliency_centroid"
    assert x > (W - 607) // 2  # pulled right of centre by the motion centroid


def test_fallback_moving_sample_anchors_on_motion_not_detail_span():
    # The drone light-wall failure: detail spans the whole frame (its
    # midpoint is the dead centre, between the portraits) while the
    # motion sits on the left portrait. A moving sample must anchor on
    # the motion centroid, not the span midpoint.
    entry = {"start": 0.0, "end": 4.0}
    context = [
        _evidence(1.0, motion=0.05, motion_center=0.15, detail_left=0.0, detail_right=1.0),
        _evidence(2.0, motion=0.05, motion_center=0.15, detail_left=0.0, detail_right=1.0),
    ]
    x, reason = reframe_plan._fallback_crop(entry, context, W, 607, None)
    assert reason == "saliency_centroid"
    assert x < (W - 607) // 2 - 100  # pulled well left, onto the motion


def test_fallback_still_sample_anchors_on_detail_span():
    # A still shot (motion below the movement floor) keeps the detail
    # anchor: nothing moves, so where the visible content is wins.
    entry = {"start": 0.0, "end": 4.0}
    context = [
        _evidence(1.0, motion=0.001, motion_center=0.15, detail_left=0.6, detail_right=0.9),
        _evidence(2.0, motion=0.001, motion_center=0.15, detail_left=0.6, detail_right=0.9),
    ]
    x, reason = reframe_plan._fallback_crop(entry, context, W, 607, None)
    assert reason == "saliency_centroid"
    assert x > (W - 607) // 2  # pulled right by the detail centroid

    x, reason = reframe_plan._fallback_crop(entry, [], W, 607, 222)
    assert (x, reason) == (222, "previous_shot_crop")

    x, reason = reframe_plan._fallback_crop(entry, [], W, 607, None)
    assert reason == "fallback_center"
    assert abs(x - (W - 607) // 2) <= 1


def test_plan_logs_the_center_fallback(caplog, monkeypatch):
    # A face-classified shot with no tracks, no context evidence inside
    # the shot and no previous shot to inherit from must end at the centre
    # crop — and the fallback must be logged, never silent.
    monkeypatch.setattr(
        reframe_plan,
        "build_context_layout_plan",
        lambda samples, cuts, duration, frac: [
            {"start": 0.0, "end": 3.0, "kind": "fit",
             "reason": "no_reliable_close_subject", "soft": False},
            {"start": 3.0, "end": duration, "kind": "face",
             "reason": "confident_subject", "soft": False},
        ],
    )
    analysis = {
        "width": W,
        "height": H,
        "samples": [],
        "cuts": [],
        # Evidence exists (so the layout planner runs) but sits entirely
        # inside the first, fit-classified shot.
        "context": [_evidence(1.0, face_area=0.0, face_count=0)],
    }
    with caplog.at_level("WARNING"):
        plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    assert plan is not None and plan["delegate"] is None
    face_shot = plan["shots"][1]
    assert face_shot["mode"] == "static"
    assert face_shot["reason"] == "fallback_center"
    assert "fallback_center" in caplog.text


def test_plan_single_subject_tracks_the_face():
    face = (1400.0, 300.0, 160.0, 200.0)  # far right of the centre crop
    analysis = {
        "width": W,
        "height": H,
        "samples": _samples([[face]] * 18),
        "cuts": [],
        "context": [
            _evidence(t / 3.0, face_area=0.016, face_count=1, face_center=0.77)
            for t in range(18)
        ],
    }
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    assert plan is not None
    shot = plan["shots"][0]
    assert shot["mode"] == "single"
    keys = shot["pieces"][0]["keys"]
    assert keys, "expected trajectory keys"
    # Crop window must contain the face centre (1480), not sit at centre.
    assert keys[0][1] + plan["crop_w"] >= 1480 >= keys[0][1]


def test_plan_splits_a_merged_entry_at_a_complete_face_turnover(monkeypatch):
    # The layout planner sees one confident-face entry, but the people in
    # the second half are entirely different (the difference-based cut
    # detector missed the change on a static background). Association
    # must not span the turnover: two shots, single then pair.
    monkeypatch.setattr(
        reframe_plan,
        "build_context_layout_plan",
        lambda samples, cuts, duration, frac: [
            {"start": 0.0, "end": duration, "kind": "face",
             "reason": "confident_subject", "soft": False},
        ],
    )
    first = (100.0, 300.0, 160.0, 200.0)
    left = (850.0, 300.0, 160.0, 200.0)
    right = (1250.0, 300.0, 160.0, 200.0)
    rows = [[first]] * 9 + [[left, right]] * 9
    analysis = {
        "width": W,
        "height": H,
        "samples": _samples(rows),
        "cuts": [],
        "context": [
            _evidence(t / 3.0, face_area=0.016, face_count=1) for t in range(18)
        ],
    }
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    assert plan is not None
    modes = [shot["mode"] for shot in plan["shots"]]
    assert modes == ["single", "pair"]


def test_plan_wide_shot_with_no_faces_is_fit():
    analysis = {
        "width": W,
        "height": H,
        "samples": _samples([[]] * 18),
        "cuts": [],
        "context": [
            _evidence(t / 3.0, face_area=0.0, face_count=0, motion=0.4)
            for t in range(18)
        ],
    }
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    assert plan is not None
    assert plan["shots"][0]["mode"] == "fit"


def test_plan_delegates_reaction_dominant_clip_to_plain():
    reactor = (1640.0, 90.0, 120.0, 120.0)   # small, static, top-right
    subject = (860.0, 600.0, 200.0, 240.0)   # main content below
    analysis = {
        "width": W,
        "height": H,
        "samples": _samples([[reactor, subject]] * 12),
        "cuts": [],
        "context": [
            _evidence(t / 3.0, face_area=0.03, face_count=2) for t in range(12)
        ],
    }
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 4.0)], 4.0)
    assert plan == {"delegate": "plain", "shots": []}


# --- Analysis cache ------------------------------------------------------------


def _fake_analysis_factory(calls):
    def fake_analyze(path, sample_fps=3.0, start_time=0.0, duration=None):
        calls.append((start_time, duration))
        samples = [
            (start_time + i / sample_fps, [(100.0, 100.0, 80.0, 100.0)])
            for i in range(6)
        ]
        return [], [], samples, []

    return fake_analyze


def test_analysis_cache_reuse_and_invalidation(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake video bytes v1")
    calls: list = []
    monkeypatch.setattr(
        "src.media.reframing.analyze_vertical_clip", _fake_analysis_factory(calls)
    )
    monkeypatch.setattr(reframe_plan, "ffprobe_duration", lambda path: 100.0)
    monkeypatch.setattr(
        "src.media.ffmpeg.ffprobe_video_size", lambda path: (W, H)
    )

    first = reframe_plan.get_source_analysis(source, [(10.0, 20.0)])
    assert first is not None and first["width"] == W
    assert len(calls) == 1
    assert reframe_plan.reframe_cache_path(source).is_file()

    second = reframe_plan.get_source_analysis(source, [(10.0, 20.0)])
    assert len(calls) == 1, "second call must be served from the cache"
    assert second["samples"] == first["samples"]

    # Same file, wider span: only the uncovered part is analyzed.
    reframe_plan.get_source_analysis(source, [(10.0, 30.0)])
    assert len(calls) == 2

    # Source changed on disk: size+mtime stamp no longer matches.
    source.write_bytes(b"fake video bytes v2, different length")
    reframe_plan.get_source_analysis(source, [(10.0, 20.0)])
    assert len(calls) == 3


def test_analysis_failure_degrades_to_empty_evidence(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake")

    def boom(path, **kwargs):
        raise RuntimeError("decoder exploded")

    monkeypatch.setattr("src.media.reframing.analyze_vertical_clip", boom)
    monkeypatch.setattr(reframe_plan, "ffprobe_duration", lambda path: 60.0)
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_video_size", lambda path: (W, H))
    view = reframe_plan.get_source_analysis(source, [(0.0, 10.0)])
    assert view is not None
    assert view["samples"] == [] and view["context"] == []
    # ... and the planner answers all-fit for evidence-less analysis.
    plan = reframe_plan.build_reframe_plan(view, [(0.0, 10.0)], 10.0)
    assert plan["shots"][0]["mode"] == "fit"
    assert plan["shots"][0]["reason"] == "analysis_unavailable"


# --- EDL graph consumption -------------------------------------------------------


def _legacy_graph(edl):
    _extra, graph, _enc, _label = build_edl_filter_graph(edl, Path("in.mp4"))
    return graph


def test_legacy_edl_graph_is_byte_identical_to_the_fixed_chain():
    edl = {
        "duration": 10.0,
        "segments": [
            {"start": 0.0, "end": 5.0},
            {"start": 5.0, "end": 10.0, "transition_in": "cross_dissolve",
             "transition_duration": 0.5},
        ],
        "zoom_keyframes": [
            {"type": "punch_in", "time": 2.0, "scale": 1.3, "duration": 0.5}
        ],
    }
    graph = _legacy_graph(edl)
    legacy0 = (
        "[0:v]trim=start=0.000:end=5.000,setpts=PTS-STARTPTS,fps=30,"
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "setsar=1,format=yuv420p[seg0]"
    )
    legacy1 = (
        "[0:v]trim=start=5.000:end=10.000,setpts=PTS-STARTPTS,fps=30,"
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "setsar=1,format=yuv420p[seg1]"
    )
    assert legacy0 in graph
    assert legacy1 in graph
    # No reframe machinery leaks into a plan-less EDL's graph.
    assert "gblur" not in graph
    assert "split=" not in graph


def test_reframe_crop_segment_uses_the_planned_crop_chain():
    edl = {
        "duration": 5.0,
        "segments": [
            {"start": 0.0, "end": 5.0, "reframe": {
                "crop": {"w": 607, "h": 1080, "x_expr": "874", "y": 12,
                         "intervals": [[0.0, 5.0]]},
            }},
        ],
    }
    graph = _legacy_graph(edl)
    assert "crop=607:1080:x='874':y=12,scale=1080:1920:flags=lanczos" in graph
    assert "gblur" not in graph  # whole-segment crop needs no backdrop


def test_reframe_mixed_segment_composes_gated_layers_over_backdrop():
    edl = {
        "duration": 6.0,
        "segments": [
            {"start": 0.0, "end": 6.0, "reframe": {
                "crop": {"w": 607, "h": 1080, "x_expr": "100", "y": 0,
                         "intervals": [[0.0, 3.0]]},
                "fit_layers": [{
                    "chain": "scale=1080:1920:force_original_aspect_ratio=decrease,setsar=1",
                    "intervals": [[3.0, 6.0]],
                }],
            }},
        ],
    }
    graph = _legacy_graph(edl)
    assert "gblur=sigma=14" in graph
    assert "overlay=0:0:enable='between(t\\,0.000\\,3.000)'" in graph
    assert "overlay=(W-w)/2:(H-h)/2:enable='between(t\\,3.000\\,6.000)'" in graph


def test_attach_reframe_plan_stamps_segments(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake")
    face = (1400.0, 300.0, 160.0, 200.0)
    analysis = {
        "width": W,
        "height": H,
        "samples": _samples([[face]] * 18),
        "cuts": [],
        "context": [
            _evidence(t / 3.0, face_area=0.016, face_count=1, face_center=0.77)
            for t in range(18)
        ],
    }
    monkeypatch.setattr(
        reframe_plan, "get_source_analysis", lambda path, spans: analysis
    )
    edl = {
        "duration": 6.0,
        "segments": [{"start": 0.0, "end": 6.0}],
    }
    assert reframe_plan.attach_reframe_plan(edl, source, [(0.0, 6.0)]) is True
    assert edl["reframe"]["shots"][0]["mode"] == "single"
    segment_reframe = edl["segments"][0]["reframe"]
    assert segment_reframe["crop"]["w"] == 606
    graph = _legacy_graph({**edl, "source_ranges": None})
    assert "crop=606:1080" in graph


# --- QC subject-in-frame (warn-only) ---------------------------------------------


def _tiny_clip(tmp_path) -> Path:
    path = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
            "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _qc_context(**kwargs):
    return clip_qc.ClipQcContext(**kwargs)


def test_qc_subject_check_skips_without_a_plan(monkeypatch):
    calls = []
    monkeypatch.setattr(
        clip_qc, "_detect_faces_bgr", lambda frame: calls.append(1) or []
    )
    result = clip_qc.check_subject_in_frame(
        Path("nope.mp4"), 10.0, _qc_context(reframe_shots=[])
    )
    assert result["status"] == "skip"
    assert calls == [], "no detection may run for a plan-less clip"


def test_qc_subject_check_skips_fit_only_plans(monkeypatch):
    calls = []
    monkeypatch.setattr(
        clip_qc, "_detect_faces_bgr", lambda frame: calls.append(1) or []
    )
    context = _qc_context(
        reframe_shots=[{"start": 0.0, "end": 10.0, "mode": "fit"}]
    )
    result = clip_qc.check_subject_in_frame(Path("nope.mp4"), 10.0, context)
    assert result["status"] == "skip"
    assert calls == []


def test_qc_subject_check_warns_but_never_fails(tmp_path, monkeypatch):
    path = _tiny_clip(tmp_path)
    monkeypatch.setattr(clip_qc, "_detect_faces_bgr", lambda frame: [])
    context = _qc_context(
        expected_duration=3.0,
        reframe_shots=[{"start": 0.0, "end": 3.0, "mode": "single"}],
    )
    report = clip_qc.run_clip_qc(path, context)
    subject = [c for c in report["checks"] if c["name"] == "subject_in_frame"]
    assert subject and subject[0]["status"] == "warn"
    assert "out of frame" in subject[0]["detail"]
    assert report["hard_fail"] is False


def test_qc_subject_check_passes_when_faces_are_found(tmp_path, monkeypatch):
    path = _tiny_clip(tmp_path)
    monkeypatch.setattr(
        clip_qc, "_detect_faces_bgr", lambda frame: [(10.0, 10.0, 50.0, 60.0)]
    )
    context = _qc_context(
        expected_duration=3.0,
        reframe_shots=[{"start": 0.0, "end": 3.0, "mode": "single"}],
    )
    result = clip_qc.check_subject_in_frame(path, 3.0, context)
    assert result["status"] == "pass"
    assert result["evidence"]["found"] == 3


# --- Composition v2: fit entries are re-decided from the face tracks --------
#
# Regression set for the blurred-pad defect in real exports: the context
# planner answers "fit" for exactly the wide shots subject-aware layouts
# exist for (two hosts too far apart for one crop, a lone speaker in a
# wide shot), and the planner used to honour that verbatim.


def _analysis(rows, evidence_kwargs, fps=3.0):
    count = len(rows)
    return {
        "width": W,
        "height": H,
        "samples": _samples(rows, fps=fps),
        "cuts": [],
        "context": [
            _evidence(i / fps, **evidence_kwargs) for i in range(count)
        ],
    }


def test_wide_two_shot_fit_is_rescued_to_split():
    left = (200.0, 300.0, 180.0, 220.0)
    right = (1300.0, 300.0, 180.0, 220.0)
    analysis = _analysis(
        [[left, right]] * 18,
        dict(face_area=0.0175, face_count=2, face_span=0.667),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    assert plan is not None
    shot = plan["shots"][0]
    assert shot["mode"] == "split"
    assert shot["reason"] == "interaction_exceeds_crop"
    assert shot["split"]["left"]["tile_x"] < shot["split"]["right"]["tile_x"]


def test_small_close_pair_fit_is_rescued_to_pair_crop():
    left = (500.0, 300.0, 110.0, 130.0)
    right = (850.0, 300.0, 110.0, 130.0)
    analysis = _analysis(
        [[left, right]] * 18,
        dict(face_area=0.0069, face_count=2, face_span=0.24),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "pair"
    assert shot["reason"] == "pair_fits_crop"


def test_pair_span_wider_than_the_window_splits_instead_of_bisecting():
    # Span 610px vs a 606px crop window: the old 1.05 pair margin let the
    # crop slice ~2px off each face. Now it must split, not pair-crop.
    left = (500.0, 300.0, 160.0, 200.0)
    right = (950.0, 300.0, 160.0, 200.0)
    analysis = _analysis(
        [[left, right]] * 18,
        dict(face_area=0.0154, face_count=2, face_span=0.318),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    assert plan["shots"][0]["mode"] == "split"


def test_wide_single_speaker_fit_is_rescued_to_a_subject_crop():
    face = (200.0, 300.0, 110.0, 140.0)  # 0.74% of frame: "too small" for context
    analysis = _analysis(
        [[face]] * 18,
        dict(face_area=0.0074, face_count=1, face_span=0.057, face_center=0.133),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "single"
    assert shot["reason"] == "wide_single_subject"
    keys = shot["pieces"][0]["keys"]
    assert keys and keys[0][1] <= 255 <= keys[0][1] + plan["crop_w"]


def test_tiny_persistent_single_subject_is_rescued_with_relaxed_thresholds():
    face = (200.0, 300.0, 80.0, 100.0)  # 0.39%: below the normal 0.4% bar
    analysis = _analysis(
        [[face]] * 18,
        dict(face_area=0.0039, face_count=1, face_span=0.042, face_center=0.125),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    assert plan["shots"][0]["mode"] == "single"


def test_faceless_fit_gets_the_explicit_no_subject_reason():
    analysis = _analysis(
        [[]] * 18,
        dict(face_area=0.0, face_count=0, motion=0.4),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "fit"
    assert shot["reason"] == "no_subject"


def test_wide_group_of_three_stays_fit_with_the_wide_group_reason():
    # Vertically centred faces: their blurred-backdrop copies land inside
    # the fit's sharp foreground strip, so the fit-blur guard does not
    # fire and the wide group keeps the full frame. (The same group at
    # y=300 put the middle face's copy in the top pad band; that case is
    # re-decided to a dominant-subject crop — see
    # test_reframe_fit_blur_guard.py.)
    faces = [
        (100.0, 450.0, 150.0, 180.0),
        (850.0, 450.0, 150.0, 180.0),
        (1600.0, 450.0, 150.0, 180.0),
    ]
    analysis = _analysis(
        [faces] * 18,
        dict(face_area=0.013, face_count=3, face_span=0.859),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "fit"
    assert shot["reason"] == "wide_group"


def test_text_heavy_fit_is_redecided_over_a_grounded_face():
    # The content-preservation exemption no longer covers face-bearing
    # fits (the TJR framing flip): this face's backdrop copy lands ~29%
    # in the top pad band, so the fit-blur guard re-decides the shot.
    face = (800.0, 300.0, 200.0, 240.0)
    analysis = _analysis(
        [[face]] * 18,
        dict(face_area=0.023, face_count=1, face_span=0.104, cut_text_lines=2),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "single"
    assert shot["reason"] == "face_blur_avoided"


def test_stable_context_collapse_is_expanded_back_to_shots(monkeypatch):
    monkeypatch.setattr(
        reframe_plan,
        "build_context_layout_plan",
        lambda samples, cuts, duration, frac: [
            {"start": 0.0, "end": duration, "kind": "fit",
             "reason": "stable_context", "soft": False},
        ],
    )
    left = (200.0, 300.0, 180.0, 220.0)
    right = (1300.0, 300.0, 180.0, 220.0)
    single = (800.0, 300.0, 180.0, 220.0)
    rows = [[left, right]] * 18 + [[single]] * 18
    context = [
        _evidence(i / 3.0, face_area=0.0175, face_count=2, face_span=0.667)
        for i in range(18)
    ] + [
        _evidence(6.0 + i / 3.0, face_area=0.019, face_count=1, face_span=0.094)
        for i in range(18)
    ]
    analysis = {
        "width": W, "height": H,
        "samples": _samples(rows), "cuts": [6.0], "context": context,
    }
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 12.0)], 12.0)
    assert plan is not None
    modes = [shot["mode"] for shot in plan["shots"]]
    assert modes == ["split", "single"]


def test_avoid_face_bisection_includes_or_excludes_a_straddling_face():
    face = (50.0, 0.0, 200.0, 200.0)
    # Window 100..706 slices the face at 100: shift left to include it whole.
    assert reframe_plan._avoid_face_bisection(100, 606, [face], W) == 50
    # Window 200..806 slices a face at its right edge; with the subject
    # protected at 600..750 the least move excludes the face whole.
    subject = (600.0, 0.0, 150.0, 150.0)
    other = (750.0, 0.0, 150.0, 150.0)
    result = reframe_plan._avoid_face_bisection(200, 606, [other], W, protect=subject)
    assert result + 606 <= 750 or result >= 900
    assert result <= 600 and 750 <= result + 606
