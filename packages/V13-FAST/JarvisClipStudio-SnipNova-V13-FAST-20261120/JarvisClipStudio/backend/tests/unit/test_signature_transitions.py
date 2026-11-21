"""Signature transition rule + transition->SFX map (Viral Scout Pack 1+2).

Covers: the map's integrity against the renderer's own xfade catalog
(every catalog name paired, only canonical SFX names emitted), the
per-profile signature declarations in the stock profiles, the join
choice rules (window, fallback, no-suitable-join), suppression of
competing decorative joins, Plain stripping, and two real FFmpeg
renders proving the signature transition and its mapped SFX land in
the finished clip — and that a Plain render contains neither.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director.edl import SFX_EVENT_NAMES, build_edl, load_profile
from src.auto_director.signature_transitions import (
    KNOWN_TRANSITION_NAMES,
    TRANSITION_SFX_MAP,
    apply_signature_transition,
    choose_signature_join,
    signature_for_profile,
    transition_sfx,
)
from src.auto_director.template_mode import plainify_edl
from src.media import edl_render


def _encoder(*_args, **_kwargs):
    return "libx264", ["-c:v", "libx264"]


# ---------------------------------------------------------------- the map


def test_map_covers_the_full_catalog_plus_aliases():
    expected = set(edl_render.XFADE_TRANSITIONS) | {"wipe", "slide", "cross_dissolve"}
    assert set(TRANSITION_SFX_MAP) == expected
    assert len(TRANSITION_SFX_MAP) == 61


def test_known_names_are_exactly_the_resolvable_transitions():
    resolvable = {
        name for name, target in edl_render.TRANSITION_MAP.items()
        if target is not None
    }
    assert KNOWN_TRANSITION_NAMES == resolvable


def test_map_only_emits_canonical_sfx_names():
    for pairing in TRANSITION_SFX_MAP.values():
        assert pairing is not None
        name, gain_db = pairing
        assert name in SFX_EVENT_NAMES
        assert -12.0 <= gain_db <= 0.0


def test_transition_sfx_pairings():
    assert transition_sfx("wipe") == ("whoosh", -6.0)
    assert transition_sfx("wipeleft") == ("whoosh", -6.0)
    assert transition_sfx("slide") == ("whoosh", -6.0)
    assert transition_sfx("zoomin") == ("whoosh", -6.0)
    assert transition_sfx("fade") == ("whoosh", -10.0)
    assert transition_sfx("cross_dissolve") == ("whoosh", -10.0)
    assert transition_sfx("fadeblack") == ("whoosh", -10.0)
    assert transition_sfx("pixelize") == ("pop", -3.0)
    # Silence: cuts, unknown names, and non-strings invent no sound.
    for silent in ("cut", "hard_cut", "none", "custom", "not_a_transition", "", None):
        assert transition_sfx(silent) is None


# ------------------------------------------------------- profile declarations


def test_every_stock_profile_declares_a_valid_signature():
    from src.auto_director.edl import _NICHES

    assert len(_NICHES) == 14
    for niche in sorted(_NICHES):
        profile = load_profile(niche)
        signature = signature_for_profile(profile)
        assert signature is not None, niche
        name, duration = signature
        assert edl_render.TRANSITION_MAP[name] is not None, niche
        assert transition_sfx(name) is not None, niche
        assert 0.15 <= duration <= 0.8, niche


def test_signature_for_profile_validation():
    def profile(sig):
        return {"transitions": {"signature": sig}}

    assert signature_for_profile(profile({"name": "wipe", "duration_sec": 0.4})) == ("wipe", 0.4)
    # Duration clamps into the visible-but-brief band; absent -> default.
    assert signature_for_profile(profile({"name": "wipe", "duration_sec": 5.0})) == ("wipe", 0.8)
    assert signature_for_profile(profile({"name": "wipe", "duration_sec": 0.01})) == ("wipe", 0.15)
    assert signature_for_profile(profile({"name": "wipe"})) == ("wipe", 0.3)
    # A cut or an unknown name is not a signature; junk is ignored.
    assert signature_for_profile(profile({"name": "cut", "duration_sec": 0.3})) is None
    assert signature_for_profile(profile({"name": "spin_blur_9000"})) is None
    assert signature_for_profile(profile("wipe")) is None
    assert signature_for_profile({"transitions": {}}) is None
    assert signature_for_profile({}) is None
    assert signature_for_profile(None) is None


# ------------------------------------------------------------- join choice


def _segments(starts):
    return [
        {"start": start, "end": start + 1, "transition_in": "cut", "transition_duration": 0}
        for start in starts
    ]


def test_choose_signature_join_window_and_fallback():
    # First join inside [3, 5] wins.
    assert choose_signature_join(_segments([0, 2, 4, 6])) == 2
    assert choose_signature_join(_segments([0, 3.5, 4.5])) == 1
    # Window edges are inclusive.
    assert choose_signature_join(_segments([0, 3.0])) == 1
    assert choose_signature_join(_segments([0, 5.0])) == 1
    # Fallback: nearest join within 2s of the window.
    assert choose_signature_join(_segments([0, 2.0, 8.0])) == 1
    assert choose_signature_join(_segments([0, 7.0])) == 1
    # A distance tie picks the earlier join.
    assert choose_signature_join(_segments([0, 1.0, 7.0])) == 1
    # Too far, or no joins at all: no signature.
    assert choose_signature_join(_segments([0, 7.1])) is None
    assert choose_signature_join(_segments([0, 15.0])) is None
    assert choose_signature_join(_segments([0])) is None
    assert choose_signature_join([]) is None


# --------------------------------------------------------------- build_edl


def _make_profile(transitions=None, pacing=(12, 12), sfx=True, signature=None):
    block = dict(transitions or {"default": "cut", "duration_sec": 0.0})
    if signature is not None:
        block["signature"] = signature
    return {
        "niche": "gaming",
        "pacing": {"target_cuts_per_min": list(pacing)},
        "transitions": block,
        "zooms": {"punch_in_scale": 1.2, "punch_in_trigger": "emphasis", "ken_burns": False},
        "captions": {"preset_id": "default", "words_per_line": 3, "position": "bottom"},
        "overlays": {
            "info_cards": False,
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
_PIXELIZE = {"name": "pixelize", "duration_sec": 0.35}


def test_signature_lands_on_first_join_in_window_with_mapped_sfx():
    profile = _make_profile(pacing=(15, 30), signature=_PIXELIZE)
    edl = build_edl(_clip(12), _WORDS, profile, [])
    segments = edl["segments"]
    assert [s["start"] for s in segments] == [0.0, 3.0, 6.0, 9.0]
    assert segments[1]["transition_in"] == "pixelize"
    assert segments[1]["transition_duration"] == 0.35
    assert all(
        s["transition_in"] == "cut" for s in segments[2:]
    )
    assert edl["signature_transition"] == {
        "name": "pixelize",
        "duration_sec": 0.35,
        "segment_index": 1,
        "at_sec": 3.0,
        "suppressed_segments": [],
        "rendered_at_sec": 2.825,
    }
    # The signature join's paired SFX, at its rendered boundary.
    assert edl["sfx_events"] == [
        {"name": "pop", "at_sec": 2.825, "gain_db": -3.0}
    ]


def test_no_signature_declared_leaves_edl_unchanged():
    profile = _make_profile(transitions={"default": "wipe", "duration_sec": 0.4})
    edl = build_edl(_clip(30), _WORDS, profile, [])
    assert "signature_transition" not in edl
    assert all(
        s["transition_in"] == "wipe" for s in edl["segments"][1:]
    )


def test_decorative_joins_before_the_signature_are_suppressed():
    profile = _make_profile(
        transitions={"default": "wipe", "duration_sec": 0.4},
        pacing=(30, 30),
        signature={"name": "wipe", "duration_sec": 0.4},
    )
    edl = build_edl(_clip(20), _WORDS, profile, [])
    segments = edl["segments"]
    assert segments[1]["start"] == 2.0
    assert segments[1]["transition_in"] == "hard_cut"
    assert segments[1]["transition_duration"] == 0
    assert segments[2]["transition_in"] == "wipe"
    assert segments[2]["transition_duration"] == 0.4
    assert edl["signature_transition"]["suppressed_segments"] == [1]
    assert edl["signature_transition"]["segment_index"] == 2
    # Later joins keep the profile grammar; their whooshes keep the
    # 4-second spacing, and the signature's whoosh is the first event.
    assert all(s["transition_in"] == "wipe" for s in segments[3:])
    assert edl["sfx_events"][0] == {
        "name": "whoosh", "at_sec": 3.8, "gain_db": -6.0,
    }


def test_signature_fallback_and_no_suitable_join():
    # One join at 7.0s: exactly at the fallback reach, so it carries it.
    profile = _make_profile(pacing=(10, 10), signature=_PIXELIZE)
    edl = build_edl(_clip(14), _WORDS, profile, [])
    assert edl["signature_transition"]["segment_index"] == 1
    assert edl["segments"][1]["transition_in"] == "pixelize"

    # One join at 15s: too far from the window — no signature at all.
    profile = _make_profile(pacing=(4, 4), signature=_PIXELIZE)
    edl = build_edl(_clip(30), _WORDS, profile, [])
    assert "signature_transition" not in edl
    assert all(
        s["transition_in"] == "cut" for s in edl["segments"][1:]
    )


def test_fade_signature_plays_the_soft_whoosh():
    profile = _make_profile(
        pacing=(20, 20), signature={"name": "fade", "duration_sec": 0.3}
    )
    edl = build_edl(_clip(12), _WORDS, profile, [])
    assert edl["segments"][1]["transition_in"] == "fade"
    assert edl["segments"][1]["start"] == 3.0
    assert edl["sfx_events"] == [
        {"name": "whoosh", "at_sec": 2.85, "gain_db": -10.0}
    ]


def test_unmapped_transition_stays_silent():
    # "custom" counts as a fade in the join plan but resolves to a hard
    # cut in the renderer — it must not gain a sound it never had.
    profile = _make_profile(transitions={"default": "custom", "duration_sec": 0.4})
    edl = build_edl(_clip(30), _WORDS, profile, [])
    assert edl["sfx_events"] == []


def test_signature_emission_is_deterministic():
    profile = _make_profile(pacing=(15, 30), signature=_PIXELIZE)
    first = build_edl(_clip(12), _WORDS, profile, [])
    second = build_edl(_clip(12), _WORDS, profile, [])
    assert first["signature_transition"] == second["signature_transition"]
    assert first["sfx_events"] == second["sfx_events"]
    assert first["segments"] == second["segments"]


def test_apply_signature_transition_directly():
    segments = _segments([0, 2, 4])
    for segment in segments[1:]:
        segment["transition_in"] = "wipe"
        segment["transition_duration"] = 0.4
    stamp = apply_signature_transition(
        segments, {"transitions": {"signature": {"name": "radial", "duration_sec": 0.35}}}, 6
    )
    assert stamp["segment_index"] == 2
    assert segments[1]["transition_in"] == "hard_cut"
    assert segments[2]["transition_in"] == "radial"
    assert apply_signature_transition(_segments([0, 4]), {}, 8) is None
    assert apply_signature_transition(_segments([0, 4]), {"transitions": {}}, 0) is None


# ------------------------------------------------------------------- plain


def test_plainify_strips_signature_and_its_sfx():
    profile = _make_profile(pacing=(15, 30), signature=_PIXELIZE)
    edl = build_edl(_clip(12), _WORDS, profile, [])
    assert "signature_transition" in edl
    plainify_edl(edl)
    assert "signature_transition" not in edl
    assert edl["sfx_events"] == []
    assert all(
        s["transition_in"] == "none" and s["transition_duration"] == 0
        for s in edl["segments"]
    )


# ------------------------------------------------------------- real FFmpeg

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark_real = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")


def _run(command):
    return subprocess.run(command, capture_output=True, text=True, timeout=600)


def _make_block_source(path: Path, duration: int = 12) -> None:
    """Red for the first 3s, blue after; silent audio. The colour change
    sits exactly on the signature join of the fixture EDL (3.0s), so a
    crossfade there mixes both colours while a plain cut never does."""
    result = _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=red:size=320x240:rate=30:duration=3",
        "-f", "lavfi", "-i", f"color=c=blue:size=320x240:rate=30:duration={duration - 3}",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a",
        "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(path),
    ])
    assert result.returncode == 0, result.stderr


def _make_wav(path: Path, frequency: int, duration: float) -> None:
    result = _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
        "-ac", "2", "-c:a", "pcm_s16le", str(path),
    ])
    assert result.returncode == 0, result.stderr


def _probe_duration(path: Path) -> float:
    result = _run(["ffprobe", "-v", "error", "-show_format", "-of", "json", str(path)])
    assert result.returncode == 0, result.stderr
    return float(json.loads(result.stdout)["format"]["duration"])


def _frame_colour_counts(path: Path, at_sec: float, tmp_path: Path):
    """(red-ish, blue-ish, blended, total) pixel counts of one frame.

    "Blended" pixels are neither source colour: a crossfade between
    the red and blue blocks passes through purple mixtures, while a
    plain cut only ever shows one pure colour or the other.
    """
    import cv2
    import numpy as np

    frame_path = tmp_path / f"frame_{at_sec}.png"
    result = _run([
        "ffmpeg", "-y", "-v", "error", "-ss", str(at_sec), "-i", str(path),
        "-frames:v", "1", str(frame_path),
    ])
    assert result.returncode == 0, result.stderr
    image = cv2.imread(str(frame_path))
    assert image is not None
    blue, green, red = image[:, :, 0], image[:, :, 1], image[:, :, 2]
    redish = int(np.count_nonzero((red > 150) & (green < 100) & (blue < 100)))
    blueish = int(np.count_nonzero((blue > 150) & (green < 100) & (red < 100)))
    total = int(image.shape[0] * image.shape[1])
    return redish, blueish, total - redish - blueish, total


def _window_mean_volume_db(path: Path, start: float, end: float):
    result = _run([
        "ffmpeg", "-i", str(path),
        "-af", f"atrim=start={start}:end={end},volumedetect",
        "-f", "null", "-",
    ])
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            raw = line.rsplit("mean_volume:", 1)[1].strip().replace(" dB", "")
            try:
                return float(raw)
            except ValueError:
                return float("-inf")
    return None


def _signature_edl():
    profile = _make_profile(pacing=(15, 30), signature=_PIXELIZE)
    return build_edl(_clip(12), _WORDS, profile, [])


@pytestmark_real
def test_real_render_signature_transition_and_its_sfx(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    _make_block_source(source)
    assets = tmp_path / "assets"
    (assets / "sfx").mkdir(parents=True)
    _make_wav(assets / "sfx" / "whoosh.wav", 440, 0.5)
    _make_wav(assets / "sfx" / "pop.wav", 660, 0.25)

    edl = _signature_edl()
    assert edl["signature_transition"]["rendered_at_sec"] == 2.825
    assert edl["sfx_events"] == [{"name": "pop", "at_sec": 2.825, "gain_db": -3.0}]

    # The render graph carries the pixelize xfade at the join's offset.
    _, graph, _, _ = edl_render.build_edl_filter_graph(edl, source)
    assert "xfade=transition=pixelize:duration=0.350000" in graph

    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=assets)
    assert ok is True
    # One 0.35s crossfade shortens the 12s clip by exactly the fade.
    assert abs(_probe_duration(output) - 11.65) < 0.4

    # Before the join: pure red. After it: pure blue.
    red, blue, blended, total = _frame_colour_counts(output, 1.0, tmp_path)
    assert red > total * 0.8 and blue == 0
    red, blue, blended, total = _frame_colour_counts(output, 6.0, tmp_path)
    assert blue > total * 0.8 and red == 0
    # Mid-transition (the xfade spans rendered 2.65-3.0s): the pixelize
    # blends the two shots into a purple mosaic, so the frame is
    # dominated by pixels that are NEITHER source colour — a plain
    # cut can never produce this frame.
    red, blue, blended, total = _frame_colour_counts(output, 2.82, tmp_path)
    assert blended > total * 0.5 and red < total * 0.5 and blue < total * 0.5

    # The mapped pop is audible in its window and nowhere else: the
    # source is silent and no other SFX event exists in this EDL.
    in_window = _window_mean_volume_db(output, 2.6, 3.2)
    assert in_window is not None and in_window > -45
    outside = _window_mean_volume_db(output, 6.0, 7.0)
    assert outside is not None and outside < -60


@pytestmark_real
def test_real_render_plain_contains_neither(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    _make_block_source(source)
    assets = tmp_path / "assets"
    (assets / "sfx").mkdir(parents=True)
    _make_wav(assets / "sfx" / "whoosh.wav", 440, 0.5)
    _make_wav(assets / "sfx" / "pop.wav", 660, 0.25)

    edl = _signature_edl()
    plainify_edl(edl)
    assert "signature_transition" not in edl

    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=assets)
    assert ok is True
    assert abs(_probe_duration(output) - 12.0) < 0.4

    # The same instant that was a blended mosaic in the Auto render is
    # a clean single-colour frame here: the join is a plain cut at 3.0s.
    red, blue, blended, total = _frame_colour_counts(output, 2.85, tmp_path)
    assert red > total * 0.8 and blue == 0 and blended < total * 0.05
    red, blue, blended, total = _frame_colour_counts(output, 3.2, tmp_path)
    assert blue > total * 0.8 and red == 0
    # And the signature's pop is nowhere in the audio.
    in_window = _window_mean_volume_db(output, 2.6, 3.2)
    assert in_window is not None and in_window < -60
