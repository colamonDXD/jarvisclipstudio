"""Unit + integration tests for the Reference Style Analyser.

The mapping half is tested with fabricated measurements (the mapping table
in ``src/auto_director/reference_style.py``'s docstring is the contract).
The measurement half is tested end-to-end on three synthetic reference
videos generated with ffmpeg + numpy: a fast-cut captioned music edit, a
slow caption-free cinematic piece, and a static captioned talking head.
Assertions are bands, not exact values - the measurements are heuristics
and the tests say so.
"""

import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import src.auto_director.edl as edl_module
from src.auto_director.edl import build_edl, load_profile
from src.auto_director.niche import NICHE_VALUES
from src.auto_director.reference_style import (
    _cuts_from_diffs_robust,
    _hook_measurements,
    analyse_reference,
    build_profile,
    score_base_niches,
    select_base_niche,
)

FONTS_DIR = Path(__file__).resolve().parents[2] / "fonts"
FONT = FONTS_DIR / "ArchivoBlack-Regular.ttf"
WORDS = [{"word": "test", "start": 0, "end": 0.5}]
RENDERABLE_DEFAULTS = {"hard_cut", "cut", "none", "cross_dissolve", "fade", "slide", "wipe"}
REQUIRED_SECTIONS = {
    "pacing", "transitions", "zooms", "captions",
    "overlays", "broll", "audio", "color", "hook",
}


def _measurements(
    *,
    cpm=15.0,
    cap_frac=0.0,
    cap_position=None,
    bed=None,
    bed_method=None,
    tempo=None,
    speech_present=False,
    saturation=0.2,
    contrast=20.0,
    hue=None,
    face_frac=0.0,
    closeup=False,
    loud_open=False,
    cap_start=False,
    first_cut=None,
    cuts_method="reframing",
):
    audio = None
    if bed is not None or tempo is not None:
        audio = {
            "music_bed_present": bed,
            "music_bed_method": bed_method,
            "tempo_bpm": tempo,
            "speech_present": speech_present,
            "loud_open": loud_open,
        }
    return {
        "version": 1,
        "source": {"file_name": "fabricated.mp4", "duration_sec": 30.0,
                   "width": 480, "height": 854, "fps": 25.0, "has_audio": True},
        "cuts": {"cut_count": 5, "cuts_per_min": cpm,
                 "shot_sec": {"mean": 4.0, "median": 4.0, "p10": 2.0, "p90": 6.0},
                 "first_cut_sec": first_cut, "method": cuts_method},
        "captions": {"frame_fraction": cap_frac, "position": cap_position,
                     "from_start": cap_start, "sampled_frames": 90},
        "colour": {"mean_saturation": saturation, "mean_brightness": 0.5,
                   "luma_contrast": contrast, "mean_hue_deg": hue},
        "audio": audio,
        "hook": {"opens_on_face_closeup": closeup, "face_in_first_3s": closeup,
                 "face_frame_fraction": face_frac, "loud_open": loud_open,
                 "caption_from_start": cap_start, "first_cut_sec": first_cut},
    }


def _assert_schema_valid(profile):
    """The same assertions the repo's own profile schema test enforces."""
    assert REQUIRED_SECTIONS <= set(profile.keys())
    lo, hi = profile["pacing"]["target_cuts_per_min"]
    assert 0 < lo < hi
    assert abs(profile["pacing"]["min_shot_sec"] - 60 / hi) <= 0.1
    assert abs(profile["pacing"]["max_shot_sec"] - 60 / lo) <= 0.1
    assert profile["hook"]["title_seconds"] <= 3.0
    assert profile["transitions"]["default"] in RENDERABLE_DEFAULTS


# ---------------------------------------------------------------------------
# Mapping table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cpm,band", [
    (2.0, [5, 8]), (7.0, [8, 12]), (12.0, [10, 16]), (17.0, [12, 20]),
    (25.0, [15, 30]), (38.0, [25, 40]), (55.0, [60, 120]), (90.0, [60, 120]),
])
def test_pacing_band_mapping_and_invariant(cpm, band):
    profile, _ = build_profile(_measurements(cpm=cpm))
    assert profile["pacing"]["target_cuts_per_min"] == band
    assert profile["pacing"]["min_shot_sec"] == round(60 / band[1], 2)
    assert profile["pacing"]["max_shot_sec"] == round(60 / band[0], 2)
    _assert_schema_valid(profile)


def test_transitions_fast_cut_style():
    profile, _ = build_profile(_measurements(cpm=50.0, cap_frac=0.9))
    t = profile["transitions"]
    assert (t["default"], t["duration_sec"], t["on_punchline"]) == ("cut", 0.0, "zoom_bounce")
    assert profile["audio"]["sfx_from_user_library"] is True
    assert profile["zooms"]["punch_in_scale"] == 1.30
    assert profile["zooms"]["ken_burns"] is False


def test_transitions_scenic_slow_style():
    profile, _ = build_profile(_measurements(cpm=4.0, cap_frac=0.0))
    t = profile["transitions"]
    assert (t["default"], t["duration_sec"]) == ("wipe", 0.4)
    assert t["on_topic_change"] == "fade"
    assert t["on_punchline"] == "wipe"
    assert profile["zooms"]["ken_burns"] is True


def test_transitions_slow_captioned_is_talking_not_scenic():
    profile, _ = build_profile(_measurements(cpm=3.0, cap_frac=0.9, cap_position="lower"))
    t = profile["transitions"]
    assert t["default"] == "hard_cut"
    assert t["on_punchline"] == "cut"
    assert profile["zooms"]["ken_burns"] is False


@pytest.mark.parametrize("kwargs,preset,words,pos", [
    (dict(cap_frac=0.8, cap_position="centre"), "large_centre_pop", 3, "center"),
    (dict(cap_frac=0.8, cap_position="lower", face_frac=0.6), "hormozi", 3, "bottom"),
    (dict(cap_frac=0.8, cap_position="lower", face_frac=0.0), "bold_uppercase", 3, "bottom"),
    (dict(cap_frac=0.25, cap_position="lower"), "classic_box", 4, "bottom"),
    (dict(cap_frac=0.25, cap_position="centre"), "classic_box", 4, "center"),
    (dict(cap_frac=0.02), "minimal", 4, "bottom"),
])
def test_caption_mapping(kwargs, preset, words, pos):
    profile, _ = build_profile(_measurements(**kwargs))
    c = profile["captions"]
    assert (c["preset_id"], c["words_per_line"], c["position"]) == (preset, words, pos)


@pytest.mark.parametrize("tempo,mood", [(150.0, "energetic"), (115.0, "upbeat"),
                                        (80.0, "calm"), (None, "calm")])
def test_audio_mapping_with_bed(tempo, mood):
    profile, _ = build_profile(_measurements(
        bed=True, bed_method="speech_gaps", tempo=tempo, speech_present=True))
    a = profile["audio"]
    assert a["music_mood"] == mood
    assert a["music_volume_0_to_1"] == 0.2
    assert a["ducking_db"] == -12.0


def test_audio_mapping_no_bed_and_unknown_bed():
    profile, report = build_profile(_measurements(bed=False, bed_method="speech_gaps"))
    a = profile["audio"]
    assert (a["music_mood"], a["music_volume_0_to_1"], a["ducking_db"]) == ("none", 0.0, 0.0)

    unknown, report = build_profile(_measurements(bed=None))
    base = load_profile(report["base_niche"])
    assert unknown["audio"]["music_mood"] == base["audio"]["music_mood"]
    assert any("audio.music_*" in item for item in report["not_inferred"])


def test_colour_mapping():
    punchy, _ = build_profile(_measurements(saturation=0.7, contrast=45.0, hue=200.0))
    assert punchy["color"]["lut"] == "punchy"
    warm, _ = build_profile(_measurements(saturation=0.3, contrast=30.0, hue=28.0))
    assert warm["color"]["lut"] == "warm_soft"
    plain, _ = build_profile(_measurements(saturation=0.25, contrast=18.0, hue=210.0))
    assert plain["color"]["lut"] == "none"


def test_zoom_trigger_tempo_needs_a_bed():
    with_bed, _ = build_profile(_measurements(bed=True, tempo=120.0, cap_frac=0.9))
    assert with_bed["zooms"]["punch_in_trigger"] == "energy_peak"
    # The same tempo read on speech alone is the talker's rhythm: the
    # caption-led rule wins instead.
    speech_only, _ = build_profile(_measurements(
        bed=False, tempo=120.0, cap_frac=0.9, cap_position="lower", cpm=5.0))
    assert speech_only["zooms"]["punch_in_trigger"] == "emphasis"


def test_hook_title_seconds_not_inferred():
    """Hook signals never set the title duration (changed 2026-11-09).

    The title is pinned for the whole clip by default; a shorter duration
    exists only as an explicit profile choice. The hook measurements are
    signals, not a measurement of how long a title stays on screen, so
    inference must not write one: the emitted profile keeps the base
    niche's value, never marks it explicit, and the mapping report lists
    hook.title_seconds as not inferred.
    """
    for kwargs in ({"cap_start": True}, {}, {"first_cut": 0.8}):
        profile, report = build_profile(_measurements(**kwargs))
        base = load_profile(report["base_niche"])
        assert profile["hook"]["title_seconds"] == base["hook"]["title_seconds"]
        assert not profile["hook"].get("title_seconds_explicit")
        assert "hook.title_seconds" not in report["inferred"]
        assert any(
            entry.startswith("hook.title_seconds")
            for entry in report["not_inferred"]
        )


def test_base_niche_selection():
    _, fast = build_profile(_measurements(cpm=55.0, bed=True))
    assert fast["base_niche"] in {"anime_funk_edit", "football_edit"}
    _, scenic = build_profile(_measurements(cpm=4.5, cap_frac=0.0, bed=True))
    assert scenic["base_niche"] in {"japan_travel_cinematic", "travel_vlog"}
    _, talking = build_profile(_measurements(cpm=0.0, cap_frac=1.0, bed=False))
    assert talking["base_niche"] == "podcast_news"


def test_base_niche_margin_pins_reproduced_close_calls():
    """The two self-run-audit R4 cases, pinned as honest-display inputs.

    Japan slice (real analyser: 6.0 cpm, 2.2% captions, bed detected):
    japan_travel_cinematic 0.2422 over travel_vlog 0.3211, margin 0.0789
    — a near-tie decided by the ken-burns scenic bonus both travel
    profiles receive. Luffy's funk reference (3.2 cpm, caption-free, no
    bed): podcast_news and true_crime tied at 1.1618, margin 0.0 — the
    alphabetical tie-break picks podcast_news. Both must be flagged
    close calls; the winner itself is unchanged (the fix is honest
    display, not a re-ranking), and the same fields are stored on the
    profile so the API list shows them too.
    """
    profile, japan = build_profile(_measurements(cpm=6.0, cap_frac=0.022, bed=True))
    assert japan["base_niche"] == "japan_travel_cinematic"
    assert japan["base_niche_margin"] == pytest.approx(0.0789, abs=0.0001)
    assert japan["base_niche_runner_up"] == "travel_vlog"
    assert japan["base_niche_close_call"] is True
    stored = profile["reference_style"]
    assert stored["base_niche_margin"] == japan["base_niche_margin"]
    assert stored["base_niche_runner_up"] == "travel_vlog"
    assert stored["base_niche_close_call"] is True

    _, funk = build_profile(_measurements(cpm=3.2, cap_frac=0.03, bed=False))
    assert funk["base_niche"] == "podcast_news"
    assert funk["base_niche_margin"] == 0.0
    assert funk["base_niche_runner_up"] == "true_crime"
    assert funk["base_niche_close_call"] is True


def test_base_niche_margin_confident_when_clear():
    """A clear pacing win stays confident: 32 cpm funk edit, winner
    football_edit 0.0103 over gaming 0.3559, margin 0.3456."""
    _, fast = build_profile(_measurements(cpm=32.0, cap_frac=0.55, bed=True))
    assert fast["base_niche"] == "football_edit"
    assert fast["base_niche_margin"] == pytest.approx(0.3456, abs=0.0001)
    assert fast["base_niche_runner_up"] == "gaming"
    assert fast["base_niche_close_call"] is False


def test_score_base_niches_sorts_by_score_then_name():
    measurements = _measurements(cpm=3.2, cap_frac=0.03, bed=False)
    rows = score_base_niches(measurements)
    assert len(rows) == len(NICHE_VALUES)
    assert rows[0] == {"niche": "podcast_news", "score": 1.1618}
    assert rows[1] == {"niche": "true_crime", "score": 1.1618}
    assert [row["score"] for row in rows] == sorted(row["score"] for row in rows)
    assert select_base_niche(measurements) == rows[0]["niche"]


def test_not_inferred_fields_are_inherited():
    measurements = _measurements(cpm=18.0, bed=True)
    profile, report = build_profile(measurements)
    base = load_profile(report["base_niche"])
    assert profile["overlays"] == base["overlays"]
    assert profile["broll"] == base["broll"]
    assert profile["hook"]["title_style"] == base["hook"]["title_style"]
    assert profile["niche"] == report["base_niche"]
    assert profile["reference_style"]["base_niche"] == report["base_niche"]


# ---------------------------------------------------------------------------
# Measurement helpers (pure)
# ---------------------------------------------------------------------------

def test_hook_measurements_face_area_threshold():
    big = [(0.5, [(0, 0, 240, 240)]), (4.0, [])]       # 14% of a 480x854 frame
    small = [(0.5, [(0, 0, 60, 60)]), (4.0, [])]       # 0.9%
    hook = _hook_measurements(big, 480, 854, None, False, None)
    assert hook["opens_on_face_closeup"] is True
    assert hook["face_frame_fraction"] == 0.5
    hook = _hook_measurements(small, 480, 854, None, False, None)
    assert hook["opens_on_face_closeup"] is False
    assert hook["face_in_first_3s"] is True


def test_robust_cut_detector_dense_and_quiet():
    # 1 s shots: a big spike every other sample at 2 samples/shot.
    diffs = []
    for i in range(40):
        diffs.append((i * 0.5, 70.0 if i % 2 == 0 and i > 0 else 3.0))
    cuts = _cuts_from_diffs_robust(diffs)
    assert len(cuts) >= 15
    # Caption changes only: small diffs, no cuts.
    quiet = [(i * 0.5, 5.0 if i % 6 == 0 else 0.5) for i in range(40)]
    assert _cuts_from_diffs_robust(quiet) == []


# ---------------------------------------------------------------------------
# Loader proof: emitted profiles load through the EXISTING loader unchanged
# ---------------------------------------------------------------------------

def _write_profile_into(tmp_path, profile):
    """Copy the stock profiles dir, drop the emitted profile in under its
    base niche's file name, and point the loader at the copy."""
    import shutil

    target = tmp_path / "profiles"
    shutil.copytree(edl_module.PROFILES_DIR, target)
    (target / f"{profile['niche']}.json").write_text(
        json.dumps(profile), encoding="utf-8")
    return target


@pytest.mark.parametrize("measurements", [
    _measurements(cpm=55.0, cap_frac=0.9, cap_position="lower", bed=True, tempo=132.0),
    _measurements(cpm=4.0, cap_frac=0.0, bed=True),
    _measurements(cpm=2.0, cap_frac=0.95, cap_position="lower", bed=False),
])
def test_emitted_profile_loads_through_existing_loader(tmp_path, monkeypatch, measurements):
    profile, _ = build_profile(measurements)
    target = _write_profile_into(tmp_path, profile)
    monkeypatch.setattr(edl_module, "PROFILES_DIR", target)

    loaded = load_profile(profile["niche"])
    assert loaded == profile
    _assert_schema_valid(loaded)

    clip = {"id": "clip", "start": 0, "end": 30, "duration": 30}
    edl = build_edl(clip, WORDS, loaded, [5.0])
    assert edl["segments"]
    assert edl["captions"]["preset_id"] == profile["captions"]["preset_id"]



# ---------------------------------------------------------------------------
# Integration: three synthetic reference videos, generated with ffmpeg.
# The generator mirrors the calibration harness the lane used; assertions
# are bands because every measurement here is a heuristic over real
# decoding, detection and audio analysis.
# ---------------------------------------------------------------------------

_SR = 44100


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-1000:]


def _write_wav(path: Path, samples: np.ndarray):
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_SR)
        w.writeframes(pcm.tobytes())


def _voice_bursts(t, period=1.0, duty=0.45, amp=0.5):
    gate = ((t % period) < duty * period).astype(np.float64)
    k = max(1, int(0.008 * _SR))
    gate = np.convolve(gate, np.ones(k) / k, mode="same")
    return amp * (np.sin(2 * np.pi * 150 * t) + 0.4 * np.sin(2 * np.pi * 310 * t)
                  + 0.3 * np.sin(2 * np.pi * 520 * t)) * gate


def _gradient_shot(path: Path, c0: str, c1: str, seconds: int, text=None):
    vf = f"fps=25"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
           "-i", f"gradients=s=480x854:r=25:d={seconds}:c0={c0}:c1={c1}:x0=0:y0=0:x1=480:y1=854"]
    if text:
        vf += (f",drawtext=fontfile={FONT}:text='{text}':fontsize=54:fontcolor=white:"
               f"borderw=2:bordercolor=black:x=(w-text_w)/2:y=h-150")
    _run(cmd + ["-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", str(path)])


def _concat_with_audio(directory: Path, shots, wav: Path, out: Path):
    lst = directory / f"{out.stem}_list.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in shots))
    _run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-i", str(wav), "-c:v", "copy", "-c:a", "aac", "-shortest", str(out)])


def _make_fast(directory: Path) -> Path:
    colours = ["0xE53935", "0xFDD835", "0x1E88E5", "0x43A047", "0x8E24AA", "0xFB8C00"]
    shots = []
    for i in range(30):
        shot = directory / f"fast_shot_{i:02d}.mp4"
        _gradient_shot(shot, colours[i % 6], colours[(i + 3) % 6], 1, text=f"SHOT {i + 1:02d}")
        shots.append(shot)
    t = np.arange(_SR * 30) / _SR
    bed = 0.05 * (np.sin(2 * np.pi * 196 * t) + 0.6 * np.sin(2 * np.pi * 294 * t)) \
        * (0.75 + 0.25 * np.sin(2 * np.pi * 2 * t))
    wav = directory / "fast_audio.wav"
    _write_wav(wav, (bed + _voice_bursts(t)) * 0.9)
    out = directory / "ref_fast.mp4"
    _concat_with_audio(directory, shots, wav, out)
    return out


def _make_cinematic(directory: Path) -> Path:
    pairs = [("0xCBB59C", "0x9A7B5F"), ("0xB99B78", "0x77644A"),
             ("0xD6C1A3", "0x8C6A4F"), ("0xC4A484", "0x6E5138")]
    shots = []
    for i, (c0, c1) in enumerate(pairs):
        shot = directory / f"cine_shot_{i}.mp4"
        _gradient_shot(shot, c0, c1, 10)
        shots.append(shot)
    t = np.arange(_SR * 40) / _SR
    pad = 0.22 * (np.sin(2 * np.pi * 110 * t) + 0.7 * np.sin(2 * np.pi * 165 * t)
                  + 0.5 * np.sin(2 * np.pi * 220 * t)) \
        * (0.8 + 0.2 * np.sin(2 * np.pi * 0.05 * t))
    wav = directory / "cine_audio.wav"
    _write_wav(wav, pad * 0.9)
    out = directory / "ref_cinematic.mp4"
    _concat_with_audio(directory, shots, wav, out)
    return out


def _make_talking(directory: Path) -> Path:
    texts = ["HELLO EVERYONE", "WELCOME BACK", "TODAY WE LEARN",
             "THE KEY POINT IS", "WATCH THIS PART", "THANKS FOR WATCHING"]
    filters = ",".join(
        f"drawtext=fontfile={FONT}:text='{text}':fontsize=40:fontcolor=white:"
        f"box=1:boxcolor=black@0.55:boxborderw=12:x=(w-text_w)/2:y=h-140:"
        f"enable='between(t,{i * 5},{(i + 1) * 5})'"
        for i, text in enumerate(texts))
    video = directory / "talk_video.mp4"
    _run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
          "-i", "color=c=0x4A5A6A:s=480x854:r=25:d=30",
          "-vf", filters, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
          str(video)])
    t = np.arange(_SR * 30) / _SR
    wav = directory / "talk_audio.wav"
    _write_wav(wav, _voice_bursts(t, period=1.4, duty=0.57) * 0.9)
    out = directory / "ref_talking.mp4"
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(wav),
          "-c:v", "copy", "-c:a", "aac", "-shortest", str(out)])
    return out


@pytest.fixture(scope="module")
def analysed(tmp_path_factory):
    directory = tmp_path_factory.mktemp("refstyle")
    results = {}
    for key, maker in (("fast", _make_fast), ("cinematic", _make_cinematic),
                       ("talking", _make_talking)):
        video = maker(directory)
        results[key] = analyse_reference(video, name=key)
    return results


def test_fast_cut_reference_measures_and_profile(analysed):
    m, profile, _ = analysed["fast"]
    assert 40 <= m["cuts"]["cuts_per_min"] <= 70
    assert m["cuts"]["shot_sec"]["median"] <= 1.5
    assert m["captions"]["frame_fraction"] >= 0.8
    assert m["captions"]["position"] == "lower"
    assert m["audio"]["music_bed_present"] is True
    assert m["audio"]["music_bed_method"] == "speech_gaps"
    assert profile["pacing"]["target_cuts_per_min"][1] >= 60
    assert profile["transitions"]["default"] == "cut"
    assert profile["audio"]["sfx_from_user_library"] is True
    assert profile["audio"]["music_mood"] != "none"
    assert profile["audio"]["music_volume_0_to_1"] == 0.2
    assert profile["color"]["lut"] == "punchy"
    assert profile["zooms"]["ken_burns"] is False
    _assert_schema_valid(profile)


def test_cinematic_reference_measures_and_profile(analysed):
    m, profile, _ = analysed["cinematic"]
    assert 2 <= m["cuts"]["cuts_per_min"] <= 7
    assert m["captions"]["frame_fraction"] <= 0.05
    assert m["audio"]["music_bed_present"] is True
    assert m["audio"]["music_bed_method"] == "continuous_tonal"
    assert m["audio"]["tempo_bpm"] is None
    assert profile["transitions"]["default"] == "wipe"
    assert profile["transitions"]["duration_sec"] == 0.4
    assert profile["zooms"]["ken_burns"] is True
    assert profile["captions"]["preset_id"] == "minimal"
    assert profile["audio"]["music_mood"] == "calm"
    assert profile["color"]["lut"] == "warm_soft"
    _assert_schema_valid(profile)


def test_talking_head_reference_measures_and_profile(analysed):
    m, profile, _ = analysed["talking"]
    assert m["cuts"]["cut_count"] <= 2
    assert m["captions"]["frame_fraction"] >= 0.8
    assert m["captions"]["from_start"] is True
    assert m["audio"]["music_bed_present"] is False
    assert profile["audio"]["music_mood"] == "none"
    assert profile["audio"]["music_volume_0_to_1"] == 0.0
    assert profile["transitions"]["default"] == "hard_cut"
    assert profile["color"]["lut"] == "none"
    assert profile["hook"]["title_seconds"] == 2.0
    _assert_schema_valid(profile)


def test_profiles_differ_in_the_expected_fields(analysed):
    fast = analysed["fast"][1]
    cinematic = analysed["cinematic"][1]
    talking = analysed["talking"][1]
    # Pacing: fast is at least 4x denser than both slow styles.
    assert fast["pacing"]["target_cuts_per_min"][0] >= 4 * cinematic["pacing"]["target_cuts_per_min"][0]
    assert fast["pacing"]["target_cuts_per_min"][0] >= 4 * talking["pacing"]["target_cuts_per_min"][0]
    # Transitions: three different defaults across the three styles.
    defaults = {fast["transitions"]["default"], cinematic["transitions"]["default"],
                talking["transitions"]["default"]}
    assert defaults == {"cut", "wipe", "hard_cut"}
    # Captions: cinematic is the only caption-free style.
    assert cinematic["captions"]["preset_id"] == "minimal"
    assert fast["captions"]["preset_id"] != "minimal"
    assert talking["captions"]["preset_id"] != "minimal"
    # Audio: only the talking head is dry.
    assert talking["audio"]["music_mood"] == "none"
    assert fast["audio"]["music_mood"] != "none"
    assert cinematic["audio"]["music_mood"] != "none"
    # Only the fast style gets SFX punctuation; only the cinematic gets ken burns.
    assert fast["audio"]["sfx_from_user_library"] is True
    assert cinematic["audio"]["sfx_from_user_library"] is False
    assert cinematic["zooms"]["ken_burns"] is True
    assert fast["zooms"]["ken_burns"] is False
