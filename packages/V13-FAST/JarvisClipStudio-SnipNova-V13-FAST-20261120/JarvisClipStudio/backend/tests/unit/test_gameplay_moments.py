"""Frame-evidence moment fusion: decoy-proof, multi-signal, context-aware."""
import numpy as np

from src.auto_director import gameplay_moments as gm


def _evidence(seconds=120):
    rng = np.random.default_rng(7)
    base = {
        "motion": 0.4, "local": 0.5, "tint": 1.5, "shake": 0.05,
        "text": 0.0, "novelty": 1.0, "pursuit": 0.5,
    }
    ev = {f: base[f] + rng.normal(0, 0.02 * max(base[f], 0.2), seconds) for f in gm.FEATURES}
    ev["duration"] = np.array([float(seconds)])
    return ev


def _grid(seconds, bursts):
    levels = np.full(int(seconds / 0.05), -40.0)
    for t, db in bursts:
        levels[int(t / 0.05): int((t + 1.5) / 0.05)] = db
    return {"energy_curve": {"hop_sec": 0.05, "levels_db": levels.tolist()}, "beat_times": []}


def _run(ev, grid=None):
    seconds = len(ev["motion"])
    audio = gm.audio_support_series(grid, seconds)
    event, zs = gm.fuse_evidence(ev, audio)
    return gm.find_episodes(event, zs, audio)


def test_loud_audio_with_no_visual_change_is_not_a_moment():
    ev = _evidence()
    assert _run(ev, _grid(120, [(60, -3.0)])) == []


def test_one_frame_flash_is_not_a_moment():
    ev = _evidence()
    ev["local"][40] += 8
    assert _run(ev) == []


def test_several_agreeing_signals_make_an_episode_audio_only_supports():
    ev = _evidence()
    for sec in range(30, 36):
        ev["tint"][sec] += 12
        ev["text"][sec] += 0.05
        ev["local"][sec] += 6
    quiet = _run(ev)
    loud = _run(ev, _grid(120, [(31, -8.0)]))
    assert len(quiet) == 1 and 30 <= quiet[0].start <= 31
    assert loud[0].score >= quiet[0].score
    assert quiet[0].audio_support < 2.5


def test_single_signal_sustained_for_seconds_counts():
    ev = _evidence()
    ev["pursuit"][70:78] += 9
    eps = _run(ev)
    assert len(eps) == 1 and eps[0].event_type == "erratic_motion"


def test_close_episodes_become_one_connected_sequence():
    ev = _evidence()
    for sec in list(range(20, 23)) + list(range(26, 29)):
        ev["tint"][sec] += 10
        ev["local"][sec] += 6
    eps = _run(ev)
    assert len(eps) == 1 and eps[0].parts == 2


def test_window_keeps_lead_in_and_ends_after_payoff():
    ep = gm.Episode(start=50, end=58, peak=56, strength=40, features={f: 0 for f in gm.FEATURES})
    start, end, lead, tail = gm.shape_window(ep, 300.0, 30.0, np.zeros(300))
    assert start < 50 < 58 < end and lead >= 3 and tail >= 2
    assert 12 <= end - start <= 48


def test_selection_returns_reasoned_moments_without_ffmpeg():
    ev = _evidence()
    for sec in range(30, 36):
        ev["tint"][sec] += 12
        ev["local"][sec] += 6
    out = gm.select_gameplay_moments("unused.mp4", 120.0, target_count=3, clip_seconds=30, evidence=ev)
    assert len(out) == 1
    m = out[0]
    assert m["evidence"]["detector"] == "frame_evidence_v1" and "agreeing" in m["reasoning"] or "agree" in m["reasoning"]
    assert m["start_time"] < "00:30" and m["end_time"] > "00:36"


def test_unreadable_video_returns_empty_for_fallback():
    assert gm.select_gameplay_moments("does-not-exist.mp4", 60.0, target_count=2, clip_seconds=30) == []
