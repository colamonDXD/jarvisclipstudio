"""Streamer-reaction fusion: voice/face agreement, event correlation, no loud-audio fakes."""
import numpy as np

from src.auto_director import gameplay_moments as gm
from src.auto_director import reaction_moments as rm


def _voice(seconds=120):
    rng = np.random.default_rng(3)
    return {
        "vocal": 30 + rng.normal(0, 0.5, seconds),
        "f0": 125 + rng.normal(0, 3, seconds),
        "voiced_frac": np.full(seconds, 0.35),
        "laugh": np.abs(rng.normal(0.1, 0.02, seconds)),
        "total_db": np.full(seconds, 40.0),
    }


def _face(seconds=120):
    return 1.0 + np.abs(np.random.default_rng(4).normal(0, 0.05, seconds))


def _game(seconds=120, events=()):
    rng = np.random.default_rng(5)
    base = {"motion": 0.4, "local": 0.5, "tint": 1.5, "shake": 0.05,
            "text": 0.0, "novelty": 1.0, "pursuit": 0.5}
    ev = {f: base[f] + rng.normal(0, 0.02 * max(base[f], 0.2), seconds) for f in gm.FEATURES}
    for a, b in events:
        for s in range(a, b):
            ev["tint"][s] += 12
            ev["local"][s] += 6
    ev["duration"] = np.array([float(seconds)])
    return ev


def _pick(voice, face, game):
    return rm.select_streamer_moments("x.mp4", 120.0, target_count=5, clip_seconds=30,
                                      voice=voice, face=face, game_evidence=game)


def test_calm_streamer_over_ordinary_gameplay_yields_nothing():
    assert _pick(_voice(), _face(), _game()) == []


def test_loud_game_audio_without_voice_is_not_a_reaction():
    v = _voice()
    v["total_db"][50:53] = 80.0           # explosion: loud, but not voiced
    assert _pick(v, _face(), _game()) == []


def test_unreacted_game_event_is_not_selected_when_a_real_reaction_exists():
    v = _voice(); f = _face()
    v["vocal"][90:94] += 15; v["f0"][90:94] += 120   # a shout at 90 s
    f[90:94] += 12
    out = _pick(v, f, _game(events=[(30, 36), (88, 91)]))
    types = [m["hook_type"] for m in out]
    assert types == ["reaction_to_event"]
    assert "00:30" not in out[0]["start_time"]


def test_reaction_to_event_outranks_commentary_and_keeps_trigger_in_window():
    v = _voice(); f = _face()
    for a in (40, 90):                                   # same shout twice
        v["vocal"][a:a + 4] += 15; v["f0"][a:a + 4] += 120; f[a:a + 4] += 12
    out = _pick(v, f, _game(events=[(37, 40)]))          # only the first has a trigger
    by_type = {m["hook_type"]: m for m in out}
    assert by_type["reaction_to_event"]["virality_score"] >= by_type["commentary_reaction"]["virality_score"]
    m = by_type["reaction_to_event"]
    assert m["start_time"] <= "00:37" and m["end_time"] > "00:44"
    assert m["evidence"]["game_event"] is not None


def test_silent_facecam_alone_is_weak_and_dropped():
    f = _face(); f[60:64] += 12
    out = _pick(_voice(), f, _game(events=[(58, 62)]))
    # Only the low-scored "game event the streamer ignored" fallback may remain;
    # visual_moments drops it, so it never becomes a pick on its own.
    assert [m["hook_type"] for m in out] in ([], ["silent_action"])
    assert all(m["virality_score"] <= 40 for m in out)


def test_no_voice_no_face_returns_empty_for_fallback():
    assert rm.select_streamer_moments("x.mp4", 60.0, target_count=3, clip_seconds=30,
                                      voice={}, face=None, game_evidence=_game(60)) == []


def test_hints_text_format(monkeypatch):
    v = _voice(); f = _face()
    v["vocal"][90:94] += 15; v["f0"][90:94] += 120; f[90:94] += 12
    monkeypatch.setattr(rm, "extract_voice_evidence", lambda p: v)
    monkeypatch.setattr(rm, "find_facecam", lambda p, d: (0.7, 0.6, 0.25, 0.3))
    monkeypatch.setattr(rm, "extract_facecam_activity", lambda p, r: f)
    monkeypatch.setattr(rm, "extract_frame_evidence", lambda p, mask_roi=None: _game(events=[(88, 91)]))
    text = rm.build_reaction_hints("x.mp4", 120.0)
    assert text.startswith("Measured streamer-reaction signals") and "reaction to event" in text


def test_single_modality_is_capped_below_corroborated_moments():
    v = _voice()
    v["vocal"][90:94] += 15; v["f0"][90:94] += 120          # voice only: no face, no event
    solo = _pick(v, None, _game())
    assert solo and solo[0]["virality_score"] <= 55 and solo[0]["evidence"]["modalities"] == ["voice"]
    f = _face(); f[90:94] += 12
    backed = _pick(v, f, _game(events=[(88, 91)]))
    assert backed[0]["virality_score"] > solo[0]["virality_score"]
    assert set(backed[0]["evidence"]["modalities"]) == {"voice", "face", "context"}


def test_voice_source_mouth_rule():
    still = np.full(14, 0.01)
    assert rm.voice_is_streamers(still, (6, 10)) is False          # voice rose, mouth did not move
    moving = still.copy(); moving[6:10] = 0.05
    assert rm.voice_is_streamers(moving, (6, 10)) is True
    assert rm.voice_is_streamers(None, (6, 10)) is None             # no face: unknown, fail open
    assert rm.voice_is_streamers(np.full(14, np.nan), (6, 10)) is None


def test_game_audio_voice_is_demoted(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x")
    item = {"type": "commentary_reaction", "start": 40.0, "end": 44.0, "mult": 1.0,
            "features": {"vocal": 8.0, "pitch": 5.0, "laugh": 0.0, "face": 0.5}}
    monkeypatch.setattr(rm, "mouth_motion", lambda *a, **k: np.full(14, 0.01))
    rm.check_voice_source([item], video, (0.1, 0.1, 0.2, 0.3))
    assert item["voice_source"].startswith("likely game audio") and item["mult"] == 0.5
    assert item["features"]["vocal"] == 0.0 and "voice" not in rm.modalities(item)


def test_lexical_series_counts_reaction_words_and_repeats():
    words = [{"text": "No,", "start": 10.1}, {"text": "no,", "start": 10.4}, {"text": "no!", "start": 10.7},
             {"text": "the", "start": 20.0}, {"text": "que", "start": 30.0}, {"text": "¿Qué?", "start": 31.0}]
    lex = rm.lexical_series(words, 40)
    assert lex[10] >= 3.5 and lex[20] == 0 and lex[30] == 0 and lex[31] >= 1


def test_words_in_milliseconds_are_converted():
    w = rm._words_in_seconds([{"text": "yo", "start": 12000, "end": 12300}], 60.0)
    assert w[0]["start"] == 12.0


def test_reaction_words_alone_never_make_a_moment_but_corroborate_one():
    words = [{"text": t, "start": 90.0 + i * 0.3, "end": 90.2 + i * 0.3} for i, t in enumerate(["no", "no", "no", "what!"])]
    assert _pick(_voice(), _face(), _game()) == []
    alone = rm.select_streamer_moments("x.mp4", 120.0, target_count=3, clip_seconds=30, voice=_voice(),
                                       face=_face(), game_evidence=_game(), words=words)
    assert [m for m in alone if m["hook_type"] != "silent_action"] == []
    f = _face(); f[90:93] += 12
    backed = rm.select_streamer_moments("x.mp4", 120.0, target_count=3, clip_seconds=30, voice=_voice(),
                                        face=f, game_evidence=_game(), words=words)
    assert backed and backed[0]["evidence"]["signals"].get("lexical", 0) >= 3
