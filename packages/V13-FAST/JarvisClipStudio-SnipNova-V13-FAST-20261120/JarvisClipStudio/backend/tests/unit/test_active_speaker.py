"""Active-speaker targeting: landmark speaking scores bias reframe
target selection toward the person actually talking.

The signal (media/active_speaker.py): per-face mouth aperture
(nose->mouth distance / face height) correlated with the audio
envelope per 2 s window. The planner seam (media/reframe_plan.py):
window winners become bounded per-sample weights for subject_series,
and Plain sizes/centres its full-bleed crop on the speaking face.

Everything is flag-gated (ACTIVE_SPEAKER_TARGETING_ENABLED, default
OFF): flag off — or evidence missing — must leave plans byte-identical
to today's area/presence behaviour. Those parity properties are the
point of half these tests.

Synthetic analyses only, mirroring test_reframe_plain.py: samples are
(source_time, [face boxes]) at 3 fps on a 1920x1080 source; speaking
samples are (source_time, audio RMS, [(box, aperture)]) at 15 fps.
"""

import math

import pytest

from src.media import active_speaker
from src.media.reframe_plan import (
    _speaking_bias,
    associate_tracks,
    build_plain_reframe_plan,
    subject_series,
)

# --- Signal math -------------------------------------------------------------


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("ACTIVE_SPEAKER_TARGETING_ENABLED", raising=False)
    assert active_speaker.active_speaker_enabled() is False
    monkeypatch.setenv("ACTIVE_SPEAKER_TARGETING_ENABLED", "true")
    assert active_speaker.active_speaker_enabled() is True
    monkeypatch.setenv("ACTIVE_SPEAKER_TARGETING_ENABLED", "0")
    assert active_speaker.active_speaker_enabled() is False


def test_mouth_aperture_geometry():
    # Nose (50, 110), mouth corners (35, 150) / (65, 150): mid (50, 150),
    # distance 40 over a 200 px face height -> 0.20.
    landmarks = [(30, 80), (70, 80), (50, 110), (35, 150), (65, 150)]
    assert active_speaker.mouth_aperture((0, 0, 100, 200), landmarks) == pytest.approx(0.2)
    assert active_speaker.mouth_aperture((0, 0, 100, 200), landmarks[:3]) is None
    assert active_speaker.mouth_aperture((0, 0, 100, 0), landmarks) is None


def test_pearson_guards():
    assert active_speaker.pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert active_speaker.pearson([1, 1, 1, 1], [1, 2, 3, 4]) is None  # flat
    assert active_speaker.pearson([1, 2], [1, 2]) is None  # too short


def _grid(seconds=6.0, fps=15.0):
    n = int(seconds * fps)
    times = [i / fps for i in range(n)]
    envelope = [abs(math.sin(t * 2.2)) * 0.5 + abs(math.sin(t * 5.7)) * 0.2 for t in times]
    return times, envelope


def test_windowed_correlations_find_the_speaker():
    times, envelope = _grid()
    speaker = [0.10 + 0.18 * e for e in envelope]  # aperture tracks audio
    listener = [0.15] * len(times)  # still face
    windows = active_speaker.windowed_correlations(
        times, {"spk": speaker, "lis": listener}, envelope
    )
    assert windows, "expected at least one scored window"
    for _t0, _t1, scores in windows:
        assert scores["spk"] > 0.9
        # The still face is scored 0.0 (real evidence of not speaking),
        # not dropped — the margin needs a runner-up.
        assert scores["lis"] == 0.0


def test_windowed_correlations_silence_yields_nothing():
    times, _envelope = _grid()
    flat_env = [0.0] * len(times)
    moving = [0.10 + 0.05 * math.sin(t * 3.0) for t in times]
    assert active_speaker.windowed_correlations(
        times, {"a": moving, "b": moving}, flat_env
    ) == []


# --- subject_series: parity + bias --------------------------------------------

# Pinned from the pre-lane implementation on this exact synthetic case
# (big face A samples 0-7, medium face B samples 3-11, both at 3 fps):
# the series follows A for samples 0-4, then B.
def _parity_samples():
    samples = []
    for i in range(12):
        boxes = []
        if i <= 7:
            boxes.append((100.0, 100.0, 300.0, 300.0))
        if i >= 3:
            boxes.append((900.0, 120.0, 200.0, 200.0))
        samples.append((i / 3.0, boxes))
    return samples


def test_subject_series_parity_without_weights():
    samples = _parity_samples()
    series = subject_series(samples, associate_tracks(samples))
    assert series[:5] == [(100.0, 100.0, 300.0, 300.0)] * 5
    assert series[5:] == [(900.0, 120.0, 200.0, 200.0)] * 7


def test_subject_series_explicit_unit_weights_match_default():
    samples = _parity_samples()
    tracks = associate_tracks(samples)
    plain = subject_series(samples, tracks)
    weighted = subject_series(samples, tracks, sample_weights=lambda _ti, _si: 1.0)
    assert weighted == plain


def test_subject_series_bias_flips_to_smaller_speaker():
    # Both faces present throughout; A is bigger (area wins by default),
    # B is the one talking. A sustained 3x weight on B must hand the
    # series to B — and only with the weights.
    samples = [
        (i / 3.0, [(100.0, 100.0, 300.0, 300.0), (900.0, 120.0, 200.0, 200.0)])
        for i in range(12)
    ]
    tracks = associate_tracks(samples)
    assert subject_series(samples, tracks) == [(100.0, 100.0, 300.0, 300.0)] * 12
    biased = subject_series(
        samples, tracks, sample_weights=lambda ti, _si: 3.0 if ti == 1 else 1.0
    )
    assert biased == [(900.0, 120.0, 200.0, 200.0)] * 12


# --- _speaking_bias gating ------------------------------------------------------

BIG = (150.0, 300.0, 300.0, 300.0)    # area-dominant listener, left
SMALL = (1500.0, 320.0, 200.0, 200.0)  # smaller speaker, right


def _two_face_samples(seconds=6.0):
    return [
        (i / 3.0, [BIG, SMALL]) for i in range(int(seconds * 3))
    ]


def _speaking_samples(seconds=6.0):
    times, envelope = _grid(seconds)
    out = []
    for t, env in zip(times, envelope):
        faces = [(BIG, 0.15), (SMALL, 0.10 + 0.18 * env)]
        out.append((t, env, faces))
    return out


def _pieces(seconds=6.0):
    return [(0.0, seconds, 0.0, seconds, 0.0, 0.0)]


def test_speaking_bias_requires_flag(monkeypatch):
    monkeypatch.delenv("ACTIVE_SPEAKER_TARGETING_ENABLED", raising=False)
    samples = _two_face_samples()
    tracks = associate_tracks(samples)
    analysis = {"speaking": {"samples": _speaking_samples()}}
    assert _speaking_bias(analysis, samples, tracks, _pieces()) is None


def test_speaking_bias_requires_evidence(monkeypatch):
    monkeypatch.setenv("ACTIVE_SPEAKER_TARGETING_ENABLED", "1")
    samples = _two_face_samples()
    tracks = associate_tracks(samples)
    assert _speaking_bias({}, samples, tracks, _pieces()) is None
    assert _speaking_bias({"speaking": {"samples": []}}, samples, tracks, _pieces()) is None
    assert _speaking_bias({"speaking": None}, samples, tracks, _pieces()) is None


def test_speaking_bias_picks_the_speaker(monkeypatch):
    monkeypatch.setenv("ACTIVE_SPEAKER_TARGETING_ENABLED", "1")
    samples = _two_face_samples()
    tracks = associate_tracks(samples)
    small_index = next(
        i for i, t in enumerate(tracks) if t.median_box() == SMALL
    )
    analysis = {"speaking": {"samples": _speaking_samples()}}
    bias = _speaking_bias(analysis, samples, tracks, _pieces())
    assert bias is not None
    assert bias["winner_track"] == small_index
    # Weights: > 1 for the speaker inside a window, exactly 1 for the
    # listener (preference reorders; nothing is ever suppressed).
    mid = len(samples) // 2
    assert bias["weights"](small_index, mid) > 1.0
    assert bias["weights"](1 - small_index, mid) == 1.0


# --- Plain planner integration ---------------------------------------------------


def _plain_analysis(with_speaking: bool, seconds=6.0):
    analysis = {
        "width": 1920,
        "height": 1080,
        "samples": _two_face_samples(seconds),
        "context": [],
        "cuts": [],
    }
    if with_speaking:
        analysis["speaking"] = {"samples": _speaking_samples(seconds)}
    return analysis


def _plain_crop_centre(plan):
    (shot,) = plan["shots"]
    xs = [x for piece in shot["pieces"] for _t, x in piece["keys"]]
    assert xs, "expected trajectory keys on the plain shot"
    return sum(xs) / len(xs) + shot["crop_w"] / 2.0, shot


def test_plain_flag_off_speaking_data_is_ignored(monkeypatch):
    # Byte-parity: with the flag off, an analysis that happens to carry
    # speaking evidence must produce exactly the plan without it.
    monkeypatch.delenv("ACTIVE_SPEAKER_TARGETING_ENABLED", raising=False)
    with_data = build_plain_reframe_plan(_plain_analysis(True), [(0.0, 6.0)], 6.0)
    without = build_plain_reframe_plan(_plain_analysis(False), [(0.0, 6.0)], 6.0)
    assert with_data == without
    centre, shot = _plain_crop_centre(with_data)
    assert "speaking" not in shot
    assert centre == pytest.approx(BIG[0] + BIG[2] / 2.0, abs=60.0)


def test_plain_flag_on_centres_the_speaking_face(monkeypatch):
    monkeypatch.setenv("ACTIVE_SPEAKER_TARGETING_ENABLED", "1")
    plan = build_plain_reframe_plan(_plain_analysis(True), [(0.0, 6.0)], 6.0)
    centre, shot = _plain_crop_centre(plan)
    # The crop follows the smaller SPEAKING face, and the shot carries
    # the evidence stamp in compacted time.
    assert centre == pytest.approx(SMALL[0] + SMALL[2] / 2.0, abs=60.0)
    assert shot["speaking"]["windows"]
    win = shot["speaking"]["windows"][0]
    assert win["winner_cx"] == pytest.approx(SMALL[0] + SMALL[2] / 2.0)
    assert win["corr"] > 0.9
    # Plain's contract is untouched: one full-bleed crop, no split/fit.
    assert shot["mode"] == "plain"
