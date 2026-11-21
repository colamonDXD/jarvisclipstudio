"""A/V sync regression: EDL crossfade joins must not desync the voice.

Diagnosis (research_notes/sync-diagnosis/report.md) convicted one
mechanism: ``build_edl_filter_graph`` compacts the VIDEO timeline at
every xfade segment join (``rendered_duration += next_duration - fade``)
while the voice stayed on the un-compacted timeline, so after each join
the picture ran ahead of the voice by exactly the fade duration,
cumulatively (+400 ms per 0.4 s join, measured). Captions ride the
audio timeline, so they trailed the picture by the same amount.

This module ports the diagnosis fixture + measurement harness into the
suite: a synthetic source whose flash (video) and beep (audio) markers
start at exactly the same instant every 5 s, rendered through the real
production renderer, with per-marker flash-to-beep offsets measured in
the output. Markers sit >= 1 s from every join (a marker inside a blend
is unmeasurable — diagnosis section 5, P7 note) and events pair in
order.

Tolerance (diagnosis section 7): |offset - calibration| <= 40 ms at
every marker and no marker-to-marker step > 40 ms, where calibration is
the fixture's own measured bias (~+1.2 ms).
"""

import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

np = pytest.importorskip("numpy")

from src.media import edl_render  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)

SR = 48000
TOLERANCE_MS = 40.0
FIXTURE_DURATION = 90.0


# --------------------------------------------------------------------------
# Fixture generation (adapted from research_notes/sync-diagnosis/gen_fixture.py)
# --------------------------------------------------------------------------

def _markers(duration: float) -> list[float]:
    return [float(t) for t in range(5, int(duration), 5)]


def _make_audio_wav(path: Path, duration: float) -> None:
    n = int(duration * SR)
    audio = np.zeros(n, dtype=np.float64)
    beep_len = int(0.150 * SR)
    edge = int(0.005 * SR)
    t = np.arange(beep_len) / SR
    env = np.ones(beep_len)
    env[:edge] = 0.5 - 0.5 * np.cos(np.pi * np.arange(edge) / edge)
    env[-edge:] = 0.5 - 0.5 * np.cos(np.pi * np.arange(edge, 0, -1) / edge)
    beep = 0.8 * np.sin(2 * np.pi * 1000.0 * t) * env
    for m in _markers(duration):
        s = int(round(m * SR))
        audio[s:s + beep_len] += beep
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def _make_fixture(path: Path, duration: float = FIXTURE_DURATION, fps: int = 30) -> None:
    w, h = 320, 180
    marks = _markers(duration)
    flash_frames = max(2, round(0.1 * fps))
    flash_idx = set()
    for m in marks:
        start = int(round(m * fps))
        flash_idx.update(range(start, start + flash_frames))
    total = int(duration * fps)
    video_only = path.with_suffix(".video.mp4")
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
         "-pix_fmt", "yuv420p", str(video_only)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for i in range(total):
        t = i / fps
        if i in flash_idx:
            img = np.full((h, w, 3), 255, dtype=np.uint8)
        else:
            img = np.full((h, w, 3), 40, dtype=np.uint8)
            x = int((t * 45) % (w - 25))
            img[h // 2 - 10:h // 2 + 10, x:x + 20] = 200
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    proc.wait()
    wav = path.with_suffix(".wav")
    _make_audio_wav(wav, duration)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_only), "-i", str(wav),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", str(path)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    video_only.unlink()
    wav.unlink()


# --------------------------------------------------------------------------
# Measurement (adapted from research_notes/sync-diagnosis/measure.py)
# --------------------------------------------------------------------------

def _frame_pts(path: Path) -> list[float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "frame=best_effort_timestamp_time",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(
        float(x) for x in (tok.strip(",") for tok in out)
        if x not in ("N/A", "")
    )


def _frame_means(path: Path) -> "np.ndarray":
    w, h = 96, 170
    frame_bytes = w * h
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v",
         "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE,
    )
    means = []
    chunk_frames = 120
    while True:
        buf = proc.stdout.read(frame_bytes * chunk_frames)
        if not buf:
            break
        n = len(buf) // frame_bytes
        frames = np.frombuffer(buf[: n * frame_bytes], dtype=np.uint8)
        means.extend(frames.reshape(n, frame_bytes).mean(axis=1).tolist())
    proc.wait()
    return np.array(means)


def _video_events(path: Path) -> list[float]:
    pts = _frame_pts(path)
    means = _frame_means(path)
    n = min(len(pts), len(means))
    pts, means = pts[:n], means[:n]
    events = []
    hot = means > 170
    k = 0
    while k < n:
        if hot[k] and (k == 0 or not hot[k - 1]):
            events.append(pts[k])
            while k < n and hot[k]:
                k += 1
        else:
            k += 1
    return events


def _audio_events(path: Path) -> list[float]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a",
         "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    if x.size == 0:
        return []
    bin_n = SR // 1000
    nb = x.size // bin_n
    env = np.abs(x[: nb * bin_n]).reshape(nb, bin_n).max(axis=1)
    events = []
    k = 0
    while k < nb:
        if env[k] > 0.30 and (k == 0 or env[k - 1] <= 0.30):
            lo = max(0, (k - 2) * bin_n)
            hi = min(x.size, (k + 3) * bin_n)
            idx = np.argmax(np.abs(x[lo:hi]) > 0.08)
            events.append((lo + int(idx)) / SR)
            k += 100
        else:
            k += 1
    return events


def _measure(path: Path) -> dict:
    ve = _video_events(path)
    ae = _audio_events(path)
    pairs = [
        {"video_t": round(v, 4), "audio_t": round(a, 4),
         "offset_ms": round((a - v) * 1000.0, 1)}
        for v, a in zip(ve, ae)
    ]
    return {"video_events": len(ve), "audio_events": len(ae), "pairs": pairs}


def _assert_in_sync(result: dict, expected_markers: int, calibration_ms: float) -> None:
    assert result["video_events"] == expected_markers, result
    assert result["audio_events"] == expected_markers, result
    offsets = [p["offset_ms"] for p in result["pairs"]]
    for offset in offsets:
        assert abs(offset - calibration_ms) <= TOLERANCE_MS, (
            f"offset {offset} ms vs calibration {calibration_ms} ms; all: {offsets}"
        )
    steps = [abs(b - a) for a, b in zip(offsets, offsets[1:])]
    assert max(steps, default=0.0) <= TOLERANCE_MS, (
        f"marker-to-marker step in {offsets}"
    )


# --------------------------------------------------------------------------
# EDL shapes (mirroring run_paths.py P2-P5 + a two-range crossfade shape)
# --------------------------------------------------------------------------

def _segments(total: float, n: int, transition=None, tdur: float = 0.0):
    step = total / n
    return [
        {
            "index": i,
            "start": round(i * step, 3),
            "end": round(min((i + 1) * step, total), 3),
            "transition_in": "none" if i == 0 else (transition or "cut"),
            "transition_duration": 0 if i == 0 else tdur,
        }
        for i in range(n)
    ]


def _edl(duration, ranges, segs):
    return {
        "clip_id": "avsync",
        "duration": duration,
        "source_ranges": [[float(a), float(b)] for a, b in ranges],
        "segments": segs,
    }


@pytest.fixture(scope="module")
def fixture_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("avsync") / "fixture30.mp4"
    _make_fixture(path)
    return path


@pytest.fixture(scope="module")
def calibration_ms(fixture_path) -> float:
    result = _measure(fixture_path)
    offsets = [p["offset_ms"] for p in result["pairs"]]
    assert len(offsets) == len(_markers(FIXTURE_DURATION)), result
    # P0 gate: the fixture itself must be internally consistent.
    assert max(offsets) - min(offsets) <= TOLERANCE_MS, offsets
    return float(np.median(offsets))


@pytest.fixture(scope="module")
def renders(tmp_path_factory, fixture_path) -> dict:
    out_dir = tmp_path_factory.mktemp("avsync_renders")
    made = {}

    def render(name, edl):
        output = out_dir / f"{name}.mp4"
        ok = edl_render.render_edl_clip_ffmpeg(fixture_path, output, edl)
        assert ok, f"render {name} failed"
        made[name] = _measure(output)
        return made[name]

    # (a) hard-cut EDL — control, always in sync.
    render("cut", _edl(60.0, [(10, 70)], _segments(60.0, 5)))
    # (b) crossfade joins 0.4 s x4 — the convicted shape.
    render("xfade", _edl(60.0, [(10, 70)], _segments(60.0, 5, "wipeleft", 0.4)))
    # (c) reframe-plan EDL (Lane F attachment), hard cuts.
    from src.media.reframe_plan import attach_reframe_plan

    e_reframe = _edl(60.0, [(10, 70)], _segments(60.0, 5))
    attach_reframe_plan(e_reframe, fixture_path, [(10.0, 70.0)])
    render("reframe", e_reframe)
    # (d) Lane-5 re-render shape: uniform cross_dissolve 0.3 s override.
    render("rerender", _edl(60.0, [(10, 70)], _segments(60.0, 5, "cross_dissolve", 0.3)))
    # (e) two-range prestage (0.15 s join) + crossfade segment joins.
    # Markers land at clip-local 3..23 and 27.85..47.85 — >= 1.8 s from
    # every join (segment joins at 9.97/19.94/29.91/39.88, prestage join
    # at 24.85).
    render("tworange", _edl(49.85, [(12, 37), (42, 67)], _segments(49.85, 5, "wipeleft", 0.4)))
    return made


# Paths (a)-(d) window the source at (10, 70): markers at source
# 10, 15, ..., 65 land inside — 12 markers, the first at clip-local 0.

def test_hard_cut_edl_stays_in_sync(renders, calibration_ms):
    _assert_in_sync(renders["cut"], 12, calibration_ms)


def test_crossfade_edl_stays_in_sync(renders, calibration_ms):
    _assert_in_sync(renders["xfade"], 12, calibration_ms)


def test_reframe_plan_edl_stays_in_sync(renders, calibration_ms):
    _assert_in_sync(renders["reframe"], 12, calibration_ms)


def test_rerender_shape_stays_in_sync(renders, calibration_ms):
    _assert_in_sync(renders["rerender"], 12, calibration_ms)


def test_two_range_crossfade_edl_stays_in_sync(renders, calibration_ms):
    _assert_in_sync(renders["tworange"], 10, calibration_ms)


# --------------------------------------------------------------------------
# Unit pins: ONE join plan shared by video graph, audio chain, durations
# and the caption shift.
# --------------------------------------------------------------------------

def _mixed_segments():
    return [
        {"index": 0, "start": 0.0, "end": 10.0, "transition_in": "none", "transition_duration": 0},
        {"index": 1, "start": 10.0, "end": 20.0, "transition_in": "wipeleft", "transition_duration": 0.4},
        {"index": 2, "start": 20.0, "end": 30.0, "transition_in": "cross_dissolve", "transition_duration": 0.3},
        {"index": 3, "start": 30.0, "end": 40.0, "transition_in": "cut", "transition_duration": 0.9},
        {"index": 4, "start": 40.0, "end": 50.0, "transition_in": "custom", "transition_duration": 0.5},
    ]


def test_join_plan_is_shared_by_video_audio_and_duration():
    segments = _mixed_segments()
    edl = {"duration": 50.0, "segments": segments}
    expected = [0.4, 0.3, 0.0, 0.0]

    assert edl_render._join_fades(segments, 50.0) == pytest.approx(expected)
    plan = edl_render.segment_join_plan(segments, 50.0)
    assert [entry["fade"] for entry in plan] == pytest.approx(expected)
    assert [entry["source_boundary"] for entry in plan] == [10.0, 20.0, 30.0, 40.0]

    _, graph, _, _ = edl_render.build_edl_filter_graph(edl, Path("in.mp4"))
    video_fades = [float(x) for x in re.findall(r"xfade=transition=\w+:duration=([0-9.]+)", graph)]
    assert video_fades == pytest.approx([0.4, 0.3])
    assert edl_render._rendered_duration(edl) == pytest.approx(50.0 - 0.7)

    fragments, label = edl_render._segment_voice_join_fragments(edl, "[0:a]")
    joined = ";".join(fragments)
    audio_fades = [float(x) for x in re.findall(r"acrossfade=d=([0-9.]+)", joined)]
    assert audio_fades == pytest.approx([0.4, 0.3])
    assert joined.count("concat=n=2:v=0:a=1") == 2  # the two zero-fade joins
    assert label == "[a_segjoin]"


def test_join_fades_clamp_like_the_video_graph():
    segments = [
        {"index": 0, "start": 0.0, "end": 10.0, "transition_in": "none", "transition_duration": 0},
        {"index": 1, "start": 10.0, "end": 10.3, "transition_in": "wipeleft", "transition_duration": 0.8},
    ]
    # The fade cannot exceed the shorter neighbour (0.3 s segment).
    assert edl_render._join_fades(segments, 10.3) == pytest.approx([0.3])


def test_no_fade_edl_keeps_legacy_render_commands(monkeypatch, tmp_path):
    from src.media.common import LOUDNORM_FILTER
    from src.media.ffmpeg import build_audio_output_args

    monkeypatch.setattr(
        edl_render, "get_video_encoder",
        lambda *_a, **_k: ("libx264", ["-c:v", "libx264"]),
    )
    captured = []
    monkeypatch.setattr(
        edl_render, "run_ffmpeg_command",
        lambda command, timeout=0: captured.append(command) or SimpleNamespace(returncode=0, stderr=""),
    )
    encoder_args = ["-c:v", "libx264"]

    # (i) Intermediate mode (no source ranges): the raw 0:a? plain path.
    edl = {
        "duration": 30,
        "segments": _segments(30.0, 3),
    }
    source, output = tmp_path / "inter.mp4", tmp_path / "out1.mp4"
    assert edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=tmp_path / "assets")
    extra_inputs, graph, _, video_label = edl_render.build_edl_filter_graph(edl, source)
    expected = [
        "ffmpeg", "-y", "-filter_threads", "4", "-i", str(source), *extra_inputs,
        "-filter_complex", graph, "-map", video_label, "-map", "0:a?",
        *encoder_args, *build_audio_output_args(True),
        "-shortest", "-movflags", "+faststart", str(output),
    ]
    assert captured == [expected]

    # (ii) Direct-from-source mode: prestage + in-graph loudnorm, unchanged.
    captured.clear()
    edl2 = {
        "duration": 20,
        "segments": _segments(20.0, 2),
        "source_ranges": [[100.0, 110.0], [120.0, 130.0]],
    }
    source2, output2 = tmp_path / "src.mp4", tmp_path / "out2.mp4"
    assert edl_render.render_edl_clip_ffmpeg(source2, output2, edl2, assets_dir=tmp_path / "assets")
    _, graph_edl, _ = edl_render._source_window(edl2)
    extra2, graph2, _, label2 = edl_render.build_edl_filter_graph(graph_edl, source2)
    audio_prestage, _ = edl_render._source_audio_prestage_fragments(
        edl_render._source_ranges(graph_edl)
    )
    plain_graph = ";".join([*audio_prestage, graph2]) + f";[srca]{LOUDNORM_FILTER}[a_out]"
    expected2 = [
        "ffmpeg", "-y", "-filter_threads", "4", "-ss", "100.000", "-t", "31.000", "-i", str(source2), *extra2,
        "-filter_complex", plain_graph, "-map", label2, "-map", "[a_out]",
        *encoder_args, *build_audio_output_args(True, loudnorm=False),
        "-shortest", "-movflags", "+faststart", str(output2),
    ]
    assert captured == [expected2]


# --------------------------------------------------------------------------
# Caption pin: word times shift through the same join plan.
# --------------------------------------------------------------------------

def test_caption_words_shift_through_the_join_plan():
    from src.media import captions

    plan = [
        {"source_boundary": 10.0, "fade": 0.4},
        {"source_boundary": 20.0, "fade": 0.3},
    ]
    words = [
        {"text": "a", "start": 5.0, "end": 5.4},
        {"text": "b", "start": 10.0, "end": 10.4},
        {"text": "c", "start": 15.0, "end": 15.4},
        {"text": "d", "start": 25.0, "end": 25.4},
    ]
    shifted = captions.shift_words_for_join_plan(words, plan)
    assert [(w["start"], w["end"]) for w in shifted] == [
        (5.0, 5.4), (9.6, 10.0), (14.6, 15.0), (24.3, 24.7),
    ]
    # The caller's words are not mutated.
    assert words[1]["start"] == 10.0
    # A plan with no fades is the identity.
    identity = captions.shift_words_for_join_plan(
        words, [{"source_boundary": 10.0, "fade": 0.0}]
    )
    assert [(w["start"], w["end"]) for w in identity] == [
        (w["start"], w["end"]) for w in words
    ]


def test_caption_ass_uses_rendered_timeline_for_edl_renders(tmp_path, monkeypatch):
    from src.media import captions

    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    plan = [
        {"source_boundary": 10.0, "fade": 0.4},
        {"source_boundary": 20.0, "fade": 0.3},
    ]
    words = [
        {"text": f"w{i}", "start": 21.0 + i * 0.4, "end": 21.35 + i * 0.4}
        for i in range(4)
    ]

    def _first_dialogue_start(**kwargs):
        output = tmp_path / "captions.ass"
        assert captions.build_assemblyai_ass_subtitles(
            tmp_path / "missing.mp4", 0.0, 30.0, 1080, 1920, output,
            caption_template="default", caption_words=[dict(w) for w in words],
            **kwargs,
        )
        starts = []
        for line in output.read_text(encoding="utf-8").splitlines():
            if line.startswith("Dialogue: 0,"):
                stamp = line.split(",")[1]
                hours, minutes, seconds = stamp.split(":")
                starts.append(int(hours) * 3600 + int(minutes) * 60 + float(seconds))
        return min(starts)

    # Both fades precede these words: 21.0 - (0.4 + 0.3) = 20.30.
    assert _first_dialogue_start(edl_join_plan=plan) == pytest.approx(20.30, abs=0.02)
    assert _first_dialogue_start() == pytest.approx(21.00, abs=0.02)


# --------------------------------------------------------------------------
# Profile sweep (diagnosis section 7): planned vs renderer durations and
# join plans agree by construction for every stock profile.
# --------------------------------------------------------------------------

def test_stock_profiles_planned_and_rendered_timelines_agree():
    from src.auto_director.edl import (
        _join_plan,
        _segment_render_duration,
        build_edl,
        load_profile,
    )

    niches = [
        "podcast_news", "travel_vlog", "meme_comedy", "gaming", "sports",
        "education_tutorial", "finance_business", "vlog_lifestyle",
        "true_crime", "hindi_podcast", "music_artist", "football_edit",
        "japan_travel_cinematic", "anime_funk_edit",
    ]
    words = [
        {"word": "w", "start": float(i * 1000), "end": float(i * 1000 + 400)}
        for i in range(45)
    ]
    for niche in niches:
        profile = load_profile(niche)
        edl = build_edl(
            {"id": "sweep", "start": 0.0, "end": 45.0, "duration": 45.0},
            words, profile, [],
        )
        segments = edl["segments"]
        planned = sum(_segment_render_duration(s) for s in segments) - sum(
            j["fade"] for j in _join_plan(segments)
        )
        assert planned == pytest.approx(edl_render._rendered_duration(edl), abs=1e-6), niche
        assert [j["fade"] for j in _join_plan(segments)] == pytest.approx(
            edl_render._join_fades(segments, 45.0)
        ), niche
        assert [e["fade"] for e in edl_render.segment_join_plan(segments, 45.0)] == (
            pytest.approx([j["fade"] for j in _join_plan(segments)])
        ), niche


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
