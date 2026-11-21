"""Speed ramps in the EDL renderer: duration math, graph assembly,
and a real synthetic render proving audio and video stay in sync."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.media import edl_render


def _ramped_edl():
    return {
        "clip_id": "ramp-demo",
        "duration": 12.0,
        "segments": [
            {
                "index": 0, "start": 0.0, "end": 6.0, "duration": 6.0,
                "transition_in": "none", "transition_duration": 0.0,
                "speed": 0.5,
                "speed_reason": "slowed build into the drop at 00:06",
                "speed_at": 0.0,
            },
            {
                "index": 1, "start": 6.0, "end": 12.0, "duration": 6.0,
                "transition_in": "cut", "transition_duration": 0.0,
                "speed": 1.5,
                "speed_reason": "quiet span at -40.0 dB rushed",
                "speed_at": 6.0,
            },
        ],
        "zoom_keyframes": [],
        "overlays": [],
        "broll": [],
        "sfx_events": [],
        "audio": {"music_mood": "none", "music_volume": 0.0, "ducking_db": 0.0},
    }


def _plain_edl():
    edl = _ramped_edl()
    for segment in edl["segments"]:
        for key in ("speed", "speed_reason", "speed_at"):
            segment.pop(key, None)
    return edl


def test_atempo_chain_factors():
    assert edl_render._atempo_chain(0.7) == "atempo=0.700000"
    assert edl_render._atempo_chain(1.5) == "atempo=1.500000"
    assert edl_render._atempo_chain(0.25) == "atempo=0.500000,atempo=0.500000"
    assert edl_render._atempo_chain(3.0) == "atempo=2.000000,atempo=1.500000"


def test_rendered_duration_accounts_for_ramps():
    # 6s at 0.5x -> 12s of output; 6s at 1.5x -> 4s; total 16s.
    assert edl_render._rendered_duration(_ramped_edl()) == 16.0
    assert edl_render._rendered_duration(_plain_edl()) == 12.0


def test_video_graph_carries_setpts_only_when_ramped():
    _, ramped_graph, _, _ = edl_render.build_edl_filter_graph(
        _ramped_edl(), "in.mp4", None, None
    )
    assert "setpts=PTS/0.500000" in ramped_graph
    assert "setpts=PTS/1.500000" in ramped_graph
    _, plain_graph, _, _ = edl_render.build_edl_filter_graph(
        _plain_edl(), "in.mp4", None, None
    )
    assert "setpts=PTS/" not in plain_graph


def test_audio_graph_retimes_voice_for_ramps_without_music():
    plan = edl_render.build_edl_audio_graph(_ramped_edl(), 1)
    assert plan is not None, "ramped EDL must never take the plain audio path"
    _, graph, label = plan
    assert label == "[a_out]"
    assert "atempo=0.500000" in graph
    assert "atempo=1.500000" in graph
    # No music and no SFX: exactly the voice chain + loudnorm.
    assert edl_render.build_edl_audio_graph(_plain_edl(), 1) is None


def _make_source(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "12",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(path),
        ],
        check=True, capture_output=True, timeout=300,
    )


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return json.loads(out.stdout)


def test_ramped_render_keeps_audio_video_in_sync(tmp_path):
    source = tmp_path / "source.mp4"
    _make_source(source)
    output = tmp_path / "ramped.mp4"
    assert edl_render.render_edl_clip_ffmpeg(source, output, _ramped_edl())
    probe = _probe(output)
    streams = {s["codec_type"]: float(s["duration"]) for s in probe["streams"]}
    # 6s slowed to 12s + 6s rushed to 4s = 16s of output.
    assert abs(streams["video"] - 16.0) <= 0.6
    assert abs(streams["audio"] - streams["video"]) <= 0.15
