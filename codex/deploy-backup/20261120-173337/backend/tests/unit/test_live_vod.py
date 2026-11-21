"""Fast clipping of finished streams: ranking, model picks, the session pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx

from src.live import vod as V


def _series(seconds, spikes):
    base = {"vocal": [-40.0] * seconds, "f0": [150.0] * seconds, "laugh": [0.0] * seconds, "voiced_frac": [0.6] * seconds}
    for t in spikes:
        for k, v in (("vocal", -10.0), ("f0", 420.0), ("laugh", 0.9)):
            for d in range(3):
                base[k][t + d] = v
    # small natural variation so the baseline has a spread
    for i in range(seconds):
        base["vocal"][i] += (i % 7) * 0.3
        base["f0"][i] += (i % 5) * 2.0
    return base


def test_heat_at_reads_the_bucket():
    heat = [{"start_time": 0, "end_time": 100, "value": 0.1}, {"start_time": 100, "end_time": 200, "value": 0.9}]
    assert V.heat_at(heat, 150) == 0.9 and V.heat_at(heat, 50) == 0.1 and V.heat_at(heat, 500) == 0.0


def test_candidates_are_spaced_and_rewatched_parts_rank_higher(monkeypatch, tmp_path):
    # The shipped default weights (a trained selection_params.json would change the ranking).
    monkeypatch.setenv("LIVE_SELECTION_PARAMS", str(tmp_path / "none.json"))
    series = _series(1200, [400, 800, 830])
    heat = [{"start_time": 700, "end_time": 900, "value": 1.0}]
    picks = V.find_candidates(series, heat, limit=5)
    onsets = [p["onset"] for p in picks]
    assert picks and abs(onsets[0] - 800) <= 3  # the rewatched spike first
    assert all(abs(a - b) >= V.MIN_GAP for i, a in enumerate(onsets) for b in onsets[i + 1:])


def test_model_picks_are_clamped_to_the_window_and_length(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gw/v1")
    monkeypatch.setenv("LLM", "openai:clip-fast")
    answer = [{"id": 1, "start": 790, "end": 900, "title": "He lost it", "why": "scream", "virality": 88},
              {"id": 9, "start": 1, "end": 30}]

    class R:
        def json(self):
            return {"choices": [{"message": {"content": "Here:\n" + json.dumps(answer)}}]}

    seen = {}
    monkeypatch.setattr(httpx, "post", lambda url, **kw: seen.setdefault("url", url) and R())
    cands = [{"id": 1, "onset": 800.0, "peak": 5.0, "signals": ["scream"], "heat": 0.4,
              "words": [{"text": "NO", "start": 799.0, "end": 799.5}, {"text": "WAY!", "start": 799.6, "end": 800.0}]}]
    picks = V.llm_pick(cands, 3)
    assert seen["url"] == "http://gw/v1/chat/completions"
    assert len(picks) == 1  # unknown id 9 dropped
    p = picks[0]
    assert p["start"] == 790 and p["end"] - p["start"] == V.MAX_LEN and p["title"] == "He lost it"


def test_without_a_model_the_strongest_signals_are_used(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    assert V.llm_pick([{"id": 1, "onset": 10.0}], 1) is None
    picks = V.signal_pick([{"id": 1, "onset": 100.0, "end": 101.0}], [0.8] * 300, 1)
    assert V.MIN_LEN <= picks[0]["end"] - picks[0]["start"] <= 45.0 and picks[0]["start"] <= 100.0


def test_session_runs_audio_first_and_downloads_only_the_picks(monkeypatch, tmp_path):
    calls = {"sections": []}
    monkeypatch.setattr(V, "probe", lambda url: {"duration": 3600.0, "title": "t", "live_status": "was_live", "heatmap": []})

    def fake_audio(url, folder):
        folder.mkdir(parents=True, exist_ok=True)
        f = folder / "audio.m4a"
        f.write_bytes(b"x")
        calls["audio"] = True
        return f

    monkeypatch.setattr(V, "download_audio", fake_audio)
    monkeypatch.setattr(V, "scan_audio", lambda a, d, workers=4, progress=None: _series(3600, [600, 1800]))
    monkeypatch.setattr(V, "transcribe_window", lambda a, s, e: [{"text": "bro", "start": s + 1, "end": s + 2}])
    monkeypatch.setattr(V, "llm_pick", lambda c, n, request=None, creator=None: None)

    def fake_section(url, s, e, out, max_height=1080):
        calls["sections"].append((round(s), round(e)))
        out.write_bytes(b"v")
        return out

    monkeypatch.setattr(V, "download_section", fake_section)
    import src.live.finisher as F

    def fake_render(window, out, **kw):
        Path(out).write_bytes(b"c")
        return {"ok": True, "duration": 25.0}

    monkeypatch.setattr(F, "render_live_clip", fake_render)
    events = []
    settings = SimpleNamespace(url="https://youtu.be/x", niche="gaming", layout_template=None, credit=None,
                               hook_title=None, streamer_words_only=True, max_clips=2)
    session = V.VodSession(settings, tmp_path / "s", events.append)
    session._run()
    assert calls.get("audio") and len(calls["sections"]) == 2
    ready = [e for e in events if e.get("type") == "clip" and e.get("status") == "ready"]
    assert len(ready) == 2
    moments = [e for e in events if e.get("type") == "moment"]
    assert all(m["reason"].startswith("[") for m in moments)  # video position, not wall clock
    assert "onset_wall" not in moments[0]
    assert any("Done: 2 clips" in (e.get("progress") or "") for e in events)


def test_several_windows_go_in_one_request_and_words_map_back(monkeypatch, tmp_path):
    import subprocess

    audio = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=200", str(audio)],
                   check=True)
    import src.live.groq_transcribe as G

    calls = []

    def fake_groq(path):
        calls.append(path)
        # window 0 = 10-70 s (offset 0), window 1 = 100-160 s (offset 62 = 60 + 2 s gap)
        return {"segments": [{"start": 0, "end": 200, "words": [
            {"word": "first", "start": 5.0, "end": 5.5},
            {"word": "gap", "start": 61.0, "end": 61.2},     # inside the silence: dropped
            {"word": "second", "start": 64.0, "end": 64.4}]}]}

    monkeypatch.setattr(G, "groq_enabled", lambda: True)
    monkeypatch.setattr(G, "groq_transcribe_file", fake_groq)
    out = V.transcribe_windows(audio, [(10.0, 70.0), (100.0, 160.0)])
    assert len(calls) == 1
    assert [w["text"] for w in out[0]] == ["first"] and abs(out[0][0]["start"] - 15.0) < 0.01
    assert [w["text"] for w in out[1]] == ["second"] and abs(out[1][0]["start"] - 102.0) < 0.01


# ---- "Find moments where..." ------------------------------------------------

def test_request_words_always_count_even_without_a_model(monkeypatch):
    monkeypatch.setattr(V, "_chat", lambda prompt, timeout=90: None)
    out = V.expand_request("every time he crashes the car")
    assert out["keywords"][:2] == ["crashes", "car"] and out["reaction_weight"] == 0.5


def test_model_expansion_is_merged_and_clamped(monkeypatch):
    monkeypatch.setattr(V, "_chat", lambda prompt, timeout=90: 'sure: {"keywords": ["Crashed", "wrecked", "crashes"], "reaction_weight": 3}')
    out = V.expand_request("crashes")
    assert out["keywords"] == ["crashes", "crashed", "wrecked"] and out["reaction_weight"] == 1.0


def test_search_ranks_windows_by_matching_words_and_reactions():
    segments = [{"start": 100.0, "end": 110.0, "text": "bro I just crashed the car again"},
                {"start": 900.0, "end": 905.0, "text": "nice weather today"},
                {"start": 1500.0, "end": 1510.0, "text": "crashed it, totally wrecked, crashed"}]
    series = _series(1800, [1502])
    picks = V.search_windows(segments, {"keywords": ["crash", "wrecked"], "reaction_weight": 1.0}, series, [], limit=5)
    assert [round(p["onset"]) // 100 for p in picks][:2] == [15, 1]  # more hits + a reaction first
    assert all("weather" not in " ".join(w["text"] for w in p["words"]) for p in picks)


def test_request_with_no_match_returns_nothing_instead_of_random_clips(monkeypatch):
    monkeypatch.setattr(V, "_chat", lambda prompt, timeout=180: "[]")
    assert V.llm_pick([{"id": 1, "onset": 10.0}], 3, request="when he talks about cats") == []
    monkeypatch.setattr(V, "_chat", lambda prompt, timeout=180: None)
    assert V.llm_pick([{"id": 1, "onset": 10.0}], 3, request="x") is None  # model down: caller ranks by signals


def test_the_request_reaches_the_model_prompt(monkeypatch):
    seen = {}
    monkeypatch.setattr(V, "_chat", lambda prompt, timeout=180: seen.setdefault("p", prompt) and "[]")
    V.llm_pick([{"id": 1, "onset": 10.0}], 3, request="every time he crashes")
    assert 'The user asked for: "every time he crashes"' in seen["p"]


def test_without_a_gpu_full_transcription_is_skipped(monkeypatch, tmp_path):
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert V.transcribe_full(tmp_path / "a.m4a", 100.0) is None


# ---------------------------------------------------------------------------
# Captions-fix lane: YouTube captions win the transcript selection
# ---------------------------------------------------------------------------

def _yt_result():
    import json as _json
    from pathlib import Path as _Path

    from src.media.yt_captions import parse_json3_captions

    payload = _json.loads((_Path(__file__).parent.parent / "fixtures" / "yt_captions_asr.json3").read_text(encoding="utf-8"))
    return parse_json3_captions(payload)


def test_transcribe_window_prefers_youtube_captions(monkeypatch, tmp_path):
    monkeypatch.setattr("src.media.yt_captions.fetch_youtube_captions", lambda url: _yt_result())
    monkeypatch.setattr(V, "_local_transcribe", lambda clip: (_ for _ in ()).throw(AssertionError("speech model ran")))
    words = V.transcribe_window(tmp_path / "audio.mp3", 5.5, 8.5, source_url="https://www.youtube.com/watch?v=YE7VzlLtp-4")
    assert [w["text"] for w in words] == ["run", "run", "get", "in", "the", "car", "get", "in", "the", "car"]


def test_transcribe_window_falls_back_when_captions_miss_the_window(monkeypatch, tmp_path):
    monkeypatch.setattr("src.media.yt_captions.fetch_youtube_captions", lambda url: _yt_result())
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("LIVE_TRANSCRIPTION_PROVIDER", "local")
    monkeypatch.setattr(V, "_local_transcribe", lambda clip: {"segments": [
        {"start": 0.0, "end": 1.0, "text": "local words", "words": [{"word": "local", "start": 0.0, "end": 0.5},
                                                                    {"word": "words", "start": 0.5, "end": 1.0}]}]})
    import subprocess

    audio = tmp_path / "audio.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
                    str(audio)], check=True)
    words = V.transcribe_window(audio, 20.0, 25.0, source_url="https://www.youtube.com/watch?v=YE7VzlLtp-4")
    assert [w["text"] for w in words] == ["local", "words"]
    assert words[0]["start"] == 20.0  # re-based to the window's absolute start


def test_transcribe_windows_mixes_captions_and_speech_model(monkeypatch, tmp_path):
    monkeypatch.setattr("src.media.yt_captions.fetch_youtube_captions", lambda url: _yt_result())
    seen = {}

    def fake_batch(audio, windows):
        seen["windows"] = list(windows)
        return [[{"text": "spoken", "start": s, "end": s + 0.4}] for s, _ in windows]

    monkeypatch.setattr(V, "_transcribe_windows_with_speech_model", fake_batch)
    out = V.transcribe_windows(tmp_path / "audio.mp3", [(5.5, 8.5), (20.0, 25.0)],
                               source_url="https://www.youtube.com/watch?v=YE7VzlLtp-4")
    assert [w["text"] for w in out[0]][:3] == ["run", "run", "get"]  # captions
    assert [w["text"] for w in out[1]] == ["spoken"]                  # speech model
    assert seen["windows"] == [(20.0, 25.0)]                          # only the missed window
