"""EDL freeze-frame segments (Trend-Radar D17, trailer breakdown).

A freeze segment (``freeze: True``) holds one source frame for
``freeze_duration`` seconds. It is a ramp-model sibling: the
effective duration, the join accounting, the per-segment voice
chain (silence for the hold) and the caption-time mapping all ride
the same accounting the speed ramps already use — so a freeze can
never desync the picture, the voice or the captions.

Blast radius is the point of most of these tests: an EDL with no
freeze segment must render byte-identically to before — no freeze
key, no behaviour change. The graph tests assert the freeze
fragments appear only when a segment carries the flag, and the
rendered-proof test (test_trailer_breakdown_render.py) proves the
hold, the punch-zoom and the silence on real ffmpeg output.
"""

from pathlib import Path

from src.auto_director.edl import _rendered_time_in_segments, _segment_render_duration
from src.media.edl_render import (
    _effective_segment_duration,
    _freeze_duration,
    _is_freeze,
    _ramped_voice_fragments,
    _rendered_duration,
    build_edl_audio_graph,
    build_edl_filter_graph,
)

# Two breakdown beats over one 36 s source range (prestage == source):
# trailer [2,5), freeze at 5.0 held 1.5 s, reaction [5,8),
# trailer [10,13), freeze at 13.0 held 1.5 s, reaction [13,16).
FREEZE_EDL = {
    "duration": 15.0,
    "source_ranges": [[0.0, 36.0]],
    "segments": [
        {"start": 2.0, "end": 5.0, "transition_in": "cut"},
        {
            "start": 5.0,
            "end": 5.0 + 1.0 / 30.0,
            "freeze": True,
            "freeze_at": 5.0,
            "freeze_duration": 1.5,
            "transition_in": "cut",
        },
        {"start": 5.0, "end": 8.0, "transition_in": "cut"},
        {"start": 10.0, "end": 13.0, "transition_in": "cut"},
        {
            "start": 13.0,
            "end": 13.0 + 1.0 / 30.0,
            "freeze": True,
            "freeze_at": 13.0,
            "freeze_duration": 1.5,
            "transition_in": "cut",
        },
        {"start": 13.0, "end": 16.0, "transition_in": "cut"},
    ],
    "zoom_keyframes": [
        {
            "type": "punch_in",
            "time": 3.0,
            "scale": 1.2,
            "duration": 0.5,
            "trigger": "detail_reveal",
        },
        {
            "type": "punch_in",
            "time": 10.5,
            "scale": 1.2,
            "duration": 0.5,
            "trigger": "detail_reveal",
        },
    ],
}

PLAIN_EDL = {
    "duration": 12.0,
    "source_ranges": [[0.0, 36.0]],
    "segments": [
        {"start": 2.0, "end": 5.0, "transition_in": "cut"},
        {"start": 5.0, "end": 8.0, "transition_in": "cut"},
        {"start": 10.0, "end": 13.0, "transition_in": "cut"},
        {"start": 13.0, "end": 16.0, "transition_in": "cut"},
    ],
    "zoom_keyframes": [],
}


def test_is_freeze_and_duration():
    freeze = FREEZE_EDL["segments"][1]
    assert _is_freeze(freeze) is True
    assert _freeze_duration(freeze) == 1.5
    assert _is_freeze(FREEZE_EDL["segments"][0]) is False
    assert _is_freeze({}) is False
    assert _is_freeze({"freeze": False, "freeze_duration": 2.0}) is False
    # A missing or invalid duration falls back to the 1.5 s default.
    assert _freeze_duration({"freeze": True}) == 1.5
    assert _freeze_duration({"freeze": True, "freeze_duration": 0}) == 1.5
    assert _freeze_duration({"freeze": True, "freeze_duration": "x"}) == 1.5


def test_effective_duration_counts_the_hold():
    segments = FREEZE_EDL["segments"]
    assert _effective_segment_duration(segments[0], 15.0) == 3.0
    assert _effective_segment_duration(segments[1], 15.0) == 1.5
    assert _effective_segment_duration(segments[2], 15.0) == 3.0
    assert _rendered_duration(FREEZE_EDL) == 15.0
    assert _rendered_duration(PLAIN_EDL) == 12.0


def test_filter_graph_holds_the_frame_and_punches_on_it(tmp_path):
    _, graph, _, _ = build_edl_filter_graph(
        FREEZE_EDL, tmp_path / "source.mp4"
    )
    # The freeze branch: one held frame, looped for the beat.
    assert graph.count("loop=loop=-1:size=1:start=0") == 2
    assert "trim=start=5.000:end=5.100" in graph
    assert "trim=start=13.000:end=13.100" in graph
    # The punch-zoom rides the freeze: zoompan present twice (once
    # per freeze segment), at the clip freeze starts.
    assert graph.count("zoompan=") == 2
    # A plain EDL's graph carries no freeze machinery at all.
    _, plain_graph, _, _ = build_edl_filter_graph(
        PLAIN_EDL, tmp_path / "source.mp4"
    )
    assert "loop=loop=-1" not in plain_graph
    assert "zoompan=" not in plain_graph


def test_ramped_voice_chain_is_silent_for_the_hold():
    fragments, label = _ramped_voice_fragments(FREEZE_EDL, "[0:a]")
    assert label == "[a_ramped]"
    joined = ";".join(fragments)
    # Two silence branches (one per freeze), each exactly the hold.
    assert joined.count("anullsrc=r=48000:cl=stereo,atrim=0:1.500") == 2
    # No atempo anywhere: nothing is stretched into noise.
    assert "atempo" not in joined
    # The talking segments are still cut from the source audio.
    assert "atrim=start=2.000:end=5.000" in joined
    assert "atrim=start=5.000:end=8.000" in joined
    # The split feeds only the segments that cut audio: four cut
    # segments (indices 0, 2, 3, 5) — a branch for a freeze would
    # sit unconnected and ffmpeg refuses the graph.
    assert "asplit=4[araw0][araw2][araw3][araw5]" in joined
    assert "[araw1]" not in joined and "[araw4]" not in joined


def test_audio_plan_is_forced_by_freezes_alone():
    # No music, no SFX, no studio, no voiceover — a plain EDL
    # would get no audio plan at all (legacy path). A freeze EDL
    # must get one, or the plain path would play the voice straight
    # through the hold and desync everything after it.
    assert build_edl_audio_graph(FREEZE_EDL, 1) is not None
    assert build_edl_audio_graph(PLAIN_EDL, 1) is None


def test_edl_side_render_duration_agrees_with_renderer():
    segments = FREEZE_EDL["segments"]
    assert sum(_segment_render_duration(s) for s in segments) == 15.0
    plain = PLAIN_EDL["segments"]
    assert sum(_segment_render_duration(s) for s in plain) == 12.0


def test_rendered_time_shifts_across_the_hold():
    segments = FREEZE_EDL["segments"]
    # The walk maps a SOURCE time to the rendered timeline: the clip
    # starts at source 2.0, so source 2.5 sits 0.5 s into the render.
    assert _rendered_time_in_segments(2.5, segments) == 0.5
    # At the freeze instant: the end of the first trailer, rendered 3.0.
    assert _rendered_time_in_segments(5.0, segments) == 3.0
    # Just after the hold: the reaction's source time is shifted
    # later by exactly the 1.5 s hold (5.5 vs the plain cut's 4.0).
    assert _rendered_time_in_segments(6.0, segments) == 5.5
    assert _rendered_time_in_segments(8.0, segments) == 7.5
    # After the second hold: shifted by both holds (3.0 s).
    assert _rendered_time_in_segments(14.0, segments) == 13.0
    # The plain EDL shifts nothing.
    assert _rendered_time_in_segments(6.0, PLAIN_EDL["segments"]) == 4.0
    assert _rendered_time_in_segments(14.0, PLAIN_EDL["segments"]) == 10.0
