"""Live MVP: the fuser only clips moments the stream itself confirms."""
from __future__ import annotations

from src.live.fuser import FuserConfig, fuse


def _audio(events, length=600.0, step=0.5):
    """Calm z ~0.2 with excitement bumps: events = [(start, end, z)]."""
    out, t = [], 0.0
    while t < length:
        v = 0.2
        for a, b, z in events:
            if a <= t < b:
                v = z
        out.append((t, v))
        t += step
    return out


def test_chat_spike_with_audio_reaction_becomes_a_clip_with_preroll_and_payoff():
    audio = _audio([(100.0, 106.0, 5.0)])
    spikes = [{"peak": 110.0, "score": 0.9}]  # chat reacts ~10 s after the scream
    [cand] = fuse(spikes, audio)
    assert cand.sources == ["chat", "audio"]
    assert cand.onset == 100.0
    assert cand.start == 80.0  # 20 s pre-roll: the stimulus before the reaction
    assert 106.0 <= cand.end <= 106.0 + 6.0 + 0.5  # settles, then the post-roll
    assert cand.score > 0.8


def test_chat_alone_never_makes_a_clip():
    assert fuse([{"peak": 300.0, "score": 1.0}], _audio([])) == []


def test_rejected_spikes_are_ignored_even_with_audio():
    audio = _audio([(100.0, 103.0, 3.0)])
    assert fuse([{"peak": 108.0, "score": 1.0, "rejected": "spam_or_bots"}], audio) == []


def test_strong_audio_alone_is_a_lower_score_candidate():
    [cand] = fuse([], _audio([(200.0, 203.0, 6.0)]))
    assert cand.sources == ["audio"] and cand.score < 0.6


def test_one_event_is_one_clip():
    audio = _audio([(100.0, 104.0, 6.0)])
    spikes = [{"peak": 108.0, "score": 0.8}, {"peak": 112.0, "score": 0.6}]
    cands = fuse(spikes, audio)
    assert len(cands) == 1 and set(cands[0].sources) == {"chat", "audio"}


def test_pre_and_post_roll_are_configurable_and_length_is_capped():
    audio = _audio([(100.0, 190.0, 5.0)])  # very long excitement
    cfg = FuserConfig(preroll=30.0, postroll=10.0, max_len=45.0)
    [cand] = fuse([{"peak": 105.0, "score": 0.9}], audio, cfg)
    assert cand.start == 70.0 and cand.end == 115.0


def test_visual_veto_drops_a_candidate():
    audio = _audio([(100.0, 104.0, 6.0)])
    cands = fuse([{"peak": 108.0, "score": 0.9}], audio,
                 visual_confirm=lambda c: (False, "dark screen, nothing happening"))
    assert cands == []


def test_reaction_moments_are_candidates_and_chat_after_them_boosts():
    reactions = [{"onset": 120.0, "start": 114.0, "end": 125.0, "score": 0.72, "reason": "vocal burst after a flash"}]
    [alone] = fuse([], _audio([]), reactions=reactions)
    assert alone.sources == ["reaction"] and alone.score == 0.72
    assert alone.start == 100.0  # pre-roll reaches back to the stimulus
    [boosted] = fuse([{"peak": 128.0, "score": 1.0}], _audio([]), reactions=reactions)
    assert boosted.sources == ["reaction", "chat"] and boosted.score > 0.72
    # a spike long after the reaction does not count as its confirmation
    [late] = fuse([{"peak": 200.0, "score": 1.0}], _audio([]), reactions=reactions)
    assert late.sources == ["reaction"]


def test_weak_picker_items_are_not_clips():
    weak = [{"onset": 50.0, "start": 45.0, "end": 60.0, "score": 0.12, "reason": "event, no reaction"}]
    assert fuse([], _audio([]), reactions=weak) == []


def test_audio_z_is_causal_and_never_rewritten():
    from src.live.signals import AudioTrack

    track = AudioTrack(baseline_sec=60.0, exclude_recent_sec=5.0)
    for i in range(200):  # 100 s quiet at -40 dB, then everything gets louder
        track.times.append(i * 0.5)
        level = -40.0 + (0.5 if i % 2 else -0.5)
        track.level.append(level)
        track.high.append(level - 10)
    track.times.append(100.0); track.level.append(-10.0); track.high.append(-20.0)  # a shout
    first = dict(track.zscores())
    assert first[100.0] > 5.0
    for i in range(201, 400):  # the stream stays loud afterwards
        track.times.append(i * 0.5); track.level.append(-10.0); track.high.append(-20.0)
    assert dict(track.zscores())[100.0] == first[100.0]  # history is not re-scored
