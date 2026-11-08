"""Transitions catalog + SFX event emission for the auto-director EDL.

Covers: the full 58-name FFmpeg xfade catalog in the renderer's
TRANSITION_MAP (legacy names unchanged, unknown names degrade to a hard
cut), and the EDL builder's sfx_events emission (flag gating, density caps,
bounds, determinism). The final two tests render real FFmpeg output through
Lane 4's audio-finishing path and only run once that lane is applied
(they skip on a tree without build_edl_audio_graph).
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director.edl import build_edl
from src.media import edl_render


def _encoder(*_args, **_kwargs):
    return "libx264", ["-c:v", "libx264"]


# The 58 named xfade transitions (FFmpeg xfade filter documentation).
XFADE_CATALOG = {
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite",
    "radial", "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "vertopen", "vertclose",
    "horzopen", "horzclose", "dissolve", "pixelize",
    "diagtl", "diagtr", "diagbl", "diagbr",
    "hlslice", "hrslice", "vuslice", "vdslice", "hblur", "fadegrays",
    "wipetl", "wipetr", "wipebl", "wipebr", "squeezeh", "squeezev",
    "zoomin", "fadefast", "fadeslow",
    "hlwind", "hrwind", "vuwind", "vdwind",
    "coverleft", "coverright", "coverup", "coverdown",
    "revealleft", "revealright", "revealup", "revealdown",
}


# ------------------------------------------------------------- transitions


def test_xfade_catalog_is_complete_and_maps_to_itself():
    assert len(XFADE_CATALOG) == 58
    assert set(edl_render.XFADE_TRANSITIONS) == XFADE_CATALOG
    for name in XFADE_CATALOG:
        assert edl_render.TRANSITION_MAP[name] == name


def test_legacy_transition_names_unchanged():
    legacy = {
        "hard_cut": None,
        "cut": None,
        "none": None,
        "cross_dissolve": "fade",
        "fade": "fade",
        "slide": "slideleft",
        "wipe": "wipeleft",
    }
    for name, target in legacy.items():
        assert edl_render.TRANSITION_MAP[name] == target


def _two_segment_edl(transition, duration=0.5):
    return {
        "duration": 4,
        "segments": [
            {"start": 0, "end": 2, "transition_in": "none", "transition_duration": 0},
            {"start": 2, "end": 4, "transition_in": transition, "transition_duration": duration},
        ],
    }


@pytest.mark.parametrize("name", sorted(XFADE_CATALOG))
def test_every_catalog_name_produces_an_xfade_graph(monkeypatch, name):
    monkeypatch.setattr(edl_render, "get_video_encoder", _encoder)
    _, graph, _, _ = edl_render.build_edl_filter_graph(_two_segment_edl(name), Path("source.mp4"))
    assert f"xfade=transition={name}:duration=0.500000:offset=1.500000" in graph


@pytest.mark.parametrize("name", ["not_a_transition", "custom", ""])
def test_unknown_transition_degrades_to_hard_cut(monkeypatch, name):
    monkeypatch.setattr(edl_render, "get_video_encoder", _encoder)
    _, graph, _, _ = edl_render.build_edl_filter_graph(_two_segment_edl(name), Path("source.mp4"))
    assert "xfade=" not in graph
    assert "concat=n=2:v=1:a=0" in graph


# ------------------------------------------------------------ SFX emission


def _make_profile(niche="gaming", transitions=None, sfx=True, info_cards=False):
    return {
        "niche": niche,
        "pacing": {"target_cuts_per_min": [12, 12]},
        "transitions": transitions or {"default": "cut", "duration_sec": 0.0},
        "zooms": {"punch_in_scale": 1.2, "punch_in_trigger": "emphasis", "ken_burns": False},
        "captions": {"preset_id": "default", "words_per_line": 3, "position": "bottom"},
        "overlays": {
            "info_cards": info_cards,
            "lower_thirds": False,
            "reference_images": "off",
            "ref_image_at_entity_mentions": False,
        },
        "broll": {"enabled": False, "source": "off", "density_per_min": 0},
        "audio": {
            "music_mood": "none",
            "music_volume_0_to_1": 0.0,
            "ducking_db": 0.0,
            "sfx_from_user_library": sfx,
        },
        "color": {"lut": "none"},
        "hook": {"title_style": "statement", "title_seconds": 2.0},
    }


def _clip(duration):
    return {"id": "clip", "start": 0, "end": duration, "duration": duration}


_WORDS = [{"word": "hello", "start": 0, "end": 0.5}]


def test_sfx_flag_off_emits_no_events():
    profile = _make_profile(sfx=False)
    edl = build_edl(_clip(30), _WORDS, profile, [2.0, 10.0, 20.0])
    assert edl["sfx_events"] == []


def test_peak_events_impact_pop_and_riser():
    profile = _make_profile()
    edl = build_edl(_clip(30), _WORDS, profile, [2.0, 10.0, 20.0])
    assert edl["sfx_events"] == [
        {"name": "riser", "at_sec": 0.8, "gain_db": -6.0},
        {"name": "impact", "at_sec": 2.0, "gain_db": 0.0},
        {"name": "pop", "at_sec": 10.0, "gain_db": -3.0},
        {"name": "pop", "at_sec": 20.0, "gain_db": -3.0},
    ]


def test_peak_density_cap_and_spacing():
    profile = _make_profile()
    peaks = [float(i) for i in range(60)]
    edl = build_edl(_clip(60), _WORDS, profile, peaks)
    hits = [e for e in edl["sfx_events"] if e["name"] in ("impact", "pop")]
    assert len(hits) == 8
    times = [e["at_sec"] for e in hits]
    assert all(b - a >= 3.0 for a, b in zip(times, times[1:]))
    # First peak sits at 0.0, too early for a riser lead-in.
    assert all(e["name"] != "riser" for e in edl["sfx_events"])


def test_peaks_out_of_bounds_are_ignored_and_events_clamped():
    profile = _make_profile()
    edl = build_edl(_clip(30), _WORDS, profile, [-5.0, 0.0, 30.0, 45.0])
    assert edl["sfx_events"] == [
        {"name": "impact", "at_sec": 0.0, "gain_db": 0.0},
        {"name": "pop", "at_sec": 29.95, "gain_db": -3.0},
    ]


def test_whoosh_only_on_crossfade_joins_at_rendered_boundaries():
    profile = _make_profile(transitions={"default": "wipe", "duration_sec": 0.4})
    edl = build_edl(_clip(30), _WORDS, profile, [])
    whooshes = [e for e in edl["sfx_events"] if e["name"] == "whoosh"]
    # 6 segments of 5s, 0.4s crossfades: rendered boundaries accumulate.
    assert [e["at_sec"] for e in whooshes] == [4.8, 9.4, 14.0, 18.6, 23.2]
    assert all(e["gain_db"] == -6.0 for e in whooshes)


def test_no_whoosh_on_cut_joins_even_with_duration():
    profile = _make_profile(transitions={"default": "cut", "duration_sec": 0.4})
    edl = build_edl(_clip(30), _WORDS, profile, [])
    assert [e for e in edl["sfx_events"] if e["name"] == "whoosh"] == []


def test_whoosh_on_legacy_cross_dissolve():
    profile = _make_profile(transitions={"default": "cross_dissolve", "duration_sec": 0.4})
    edl = build_edl(_clip(30), _WORDS, profile, [])
    assert len([e for e in edl["sfx_events"] if e["name"] == "whoosh"]) == 5


def test_true_crime_gets_a_single_sting_on_the_final_peak():
    profile = _make_profile(niche="true_crime")
    edl = build_edl(_clip(45), _WORDS, profile, [5.0, 25.0, 40.0])
    assert edl["sfx_events"] == [{"name": "sting", "at_sec": 40.0, "gain_db": -3.0}]


def test_ding_on_info_cards_capped_at_three():
    profile = _make_profile(info_cards=True)
    fillers = ["growth", "came", "from", "sales", "and", "more",
               "with", "strong", "results", "across", "regions", "during",
               "the", "quarter", "period", "reported"]
    words = (
        [{"word": "hello", "start": 0, "end": 0.4},
         {"word": "42", "start": 0.4, "end": 0.6},
         {"word": "percent", "start": 0.6, "end": 0.9}]
        + [{"word": w, "start": 0, "end": 0} for w in fillers[:6]]
        + [{"word": "50", "start": 0, "end": 0},
           {"word": "percent", "start": 0, "end": 0}]
        + [{"word": w, "start": 0, "end": 0} for w in fillers[6:12]]
        + [{"word": "99", "start": 0, "end": 0},
           {"word": "percent", "start": 0, "end": 0},
           {"word": "today", "start": 0, "end": 0}]
    )
    assert len(words) == 20
    edl = build_edl(_clip(40), words, profile, [])
    dings = [e for e in edl["sfx_events"] if e["name"] == "ding"]
    assert [e["at_sec"] for e in dings] == [2.0, 18.0, 34.0]
    assert all(e["gain_db"] == -6.0 for e in dings)


def test_events_never_stack_on_a_whoosh():
    profile = _make_profile(transitions={"default": "wipe", "duration_sec": 0.4})
    edl = build_edl(_clip(30), _WORDS, profile, [5.2, 12.0])
    events = edl["sfx_events"]
    times = [e["at_sec"] for e in events]
    assert times == sorted(times)
    assert all(b - a >= 0.75 for a, b in zip(times, times[1:]))
    # The peak at 5.2 renders onto the 4.8 whoosh and is dropped; the later
    # peak still lands as a pop at its rendered time (12.0 - 0.8 of fades).
    assert {"name": "whoosh", "at_sec": 4.8, "gain_db": -6.0} in events
    assert {"name": "pop", "at_sec": 11.2, "gain_db": -3.0} in events
    assert all(e["name"] != "impact" for e in events)


def test_emission_is_deterministic():
    profile = _make_profile(transitions={"default": "wipe", "duration_sec": 0.4})
    first = build_edl(_clip(30), _WORDS, profile, [2.0, 10.0, 20.0])
    second = build_edl(_clip(30), _WORDS, profile, [2.0, 10.0, 20.0])
    assert first["sfx_events"] == second["sfx_events"]


def test_profile_transition_name_flows_into_render_graph(monkeypatch):
    monkeypatch.setattr(edl_render, "get_video_encoder", _encoder)
    profile = _make_profile(transitions={"default": "circleopen", "duration_sec": 0.4})
    edl = build_edl(_clip(10), _WORDS, profile, [])
    _, graph, _, _ = edl_render.build_edl_filter_graph(edl, Path("source.mp4"))
    assert "xfade=transition=circleopen:" in graph


# ------------------------------------------------------------- real FFmpeg

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark_real = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")


def _run(command):
    return subprocess.run(command, capture_output=True, text=True, timeout=600)


def _make_source(path: Path, duration: int, silent: bool = False) -> None:
    # A tone by default: the plain render path's loudnorm turns digital
    # silence (anullsrc) into NaN samples on some FFmpeg builds. The SFX
    # mix tests opt into a silent source explicitly — there the mixed-in
    # SFX keep the audio graph's loudnorm input non-silent.
    audio = (
        "anullsrc=channel_layout=stereo:sample_rate=48000"
        if silent
        else f"sine=frequency=440:sample_rate=48000:duration={duration}"
    )
    result = _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=30:duration={duration}",
        "-f", "lavfi", "-i", audio,
        "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ])
    assert result.returncode == 0, result.stderr


def _probe_duration(path: Path) -> float:
    result = _run(["ffprobe", "-v", "error", "-show_format", "-of", "json", str(path)])
    assert result.returncode == 0, result.stderr
    return float(json.loads(result.stdout)["format"]["duration"])


# A family-spanning subset of the catalog, rendered for real.
REAL_RENDER_TRANSITIONS = [
    "fade", "wipeleft", "circleopen", "dissolve", "pixelize", "zoomin",
    "hblur", "fadegrays", "radial", "squeezeh", "diagtl", "hlwind",
    "coverleft", "vertopen",
]


@pytestmark_real
@pytest.mark.parametrize("name", REAL_RENDER_TRANSITIONS)
def test_real_render_with_catalog_transition(tmp_path, name):
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    _make_source(source, duration=2)
    edl = {
        "duration": 2,
        "segments": [
            {"start": 0, "end": 1, "transition_in": "none", "transition_duration": 0},
            {"start": 1, "end": 2, "transition_in": name, "transition_duration": 0.5},
        ],
    }
    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl)
    assert ok is True
    assert abs(_probe_duration(output) - 1.5) < 0.4


# ------------------------------------------- real SFX mixes (needs Lane 4)

_LANE4_APPLIED = hasattr(edl_render, "build_edl_audio_graph")
pytestmark_audio = pytest.mark.skipif(
    ffmpeg_missing or not _LANE4_APPLIED,
    reason="needs ffmpeg and the Lane 4 audio-finishing path",
)


def _make_wav(path: Path, frequency: int, duration: float) -> None:
    result = _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
        "-ac", "2", "-c:a", "pcm_s16le", str(path),
    ])
    assert result.returncode == 0, result.stderr


def _mean_volume_db(path: Path):
    result = _run(["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"])
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.rsplit("mean_volume:", 1)[1].strip().replace(" dB", ""))
            except ValueError:
                return None
    return None


@pytestmark_audio
def test_real_render_emitted_peak_sfx_are_mixed(tmp_path):
    """build_edl emits impact/pop events; over a silent source the rendered
    clip is non-silent only if Lane 4's audio graph actually mixed them."""
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    _make_source(source, duration=6, silent=True)
    assets = tmp_path / "assets"
    (assets / "sfx").mkdir(parents=True)
    _make_wav(assets / "sfx" / "impact.wav", 880, 0.3)
    _make_wav(assets / "sfx" / "pop.wav", 660, 0.2)

    edl = build_edl(_clip(6), _WORDS, _make_profile(), [1.0, 4.0])
    assert [e["name"] for e in edl["sfx_events"]] == ["impact", "pop"]

    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=assets)
    assert ok is True
    mean_volume = _mean_volume_db(output)
    assert mean_volume is not None and mean_volume > -45


@pytestmark_audio
def test_real_render_emitted_whoosh_is_mixed(tmp_path):
    """A whoosh emitted for a wipe join is audible over a silent source."""
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    _make_source(source, duration=10, silent=True)
    assets = tmp_path / "assets"
    (assets / "sfx").mkdir(parents=True)
    _make_wav(assets / "sfx" / "whoosh.wav", 440, 0.5)

    profile = _make_profile(transitions={"default": "wipe", "duration_sec": 0.4})
    edl = build_edl(_clip(10), _WORDS, profile, [])
    assert [e["name"] for e in edl["sfx_events"]] == ["whoosh"]
    assert edl["sfx_events"][0]["at_sec"] == 4.8

    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=assets)
    assert ok is True
    mean_volume = _mean_volume_db(output)
    assert mean_volume is not None and mean_volume > -45
