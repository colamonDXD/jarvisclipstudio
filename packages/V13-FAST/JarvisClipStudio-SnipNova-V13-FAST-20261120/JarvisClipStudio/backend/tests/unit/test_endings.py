"""Loop-engineered + payoff-tight endings (viral queue #3/#7).

Pure-function tests for auto_director/endings plus the wiring proofs:
the streamer picker and the VOD pick refinement must be byte-identical
with both switches off, and move ends only as documented with them on.
"""
import numpy as np
import pytest

from src.auto_director import endings as en
from src.auto_director import reaction_moments as rm
from src.live import vod


def W(text, start, end):
    return {"text": text, "start": float(start), "end": float(end)}


def sentence(*spec):
    """Words from (text, start, end) triples."""
    return [W(t, s, e) for t, s, e in spec]


# ---------------------------------------------------------------------------
# Switches
# ---------------------------------------------------------------------------

def test_switches_default_off(monkeypatch):
    monkeypatch.delenv("LOOP_ENDINGS", raising=False)
    monkeypatch.delenv("PAYOFF_TIGHT", raising=False)
    assert en.loop_endings_enabled() is False
    assert en.payoff_tight_enabled() is False
    monkeypatch.setenv("LOOP_ENDINGS", "on")
    monkeypatch.setenv("PAYOFF_TIGHT", "1")
    assert en.loop_endings_enabled() is True
    assert en.payoff_tight_enabled() is True


def test_both_off_returns_end_untouched():
    words = sentence(("go.", 0.0, 0.4))
    new_end, rec = en.refine_clip_end(0.0, 40.0, payoff_end=20.0, words=words,
                                      loop_on=False, tight_on=False)
    assert new_end == 40.0
    assert rec["applied"] is False
    assert rec["reason"] == "switches off"


# ---------------------------------------------------------------------------
# Sentence model + payoff completion
# ---------------------------------------------------------------------------

WORDS = sentence(
    ("we", 0.0, 0.2), ("pushed", 0.25, 0.6), ("the", 0.65, 0.8), ("site.", 0.85, 1.3),
    ("then", 2.0, 2.3), ("everyone", 2.35, 2.9), ("screamed", 2.95, 3.6), ("loudly!", 3.65, 4.3),
    ("and", 6.0, 6.2), ("it", 6.25, 6.4), ("ended", 6.45, 7.0),
)


def test_sentence_end_times():
    # Punctuated ends, plus the final word: the last sentence ends there
    # even when its last word carries no punctuation.
    assert en.sentence_end_times(WORDS) == [1.3, 4.3, 7.0]


def test_payoff_sentence_end_picks_the_payoffs_sentence():
    assert en.payoff_sentence_end(WORDS, 1.0) == 1.3     # inside sentence 1
    assert en.payoff_sentence_end(WORDS, 3.0) == 4.3     # inside sentence 2
    assert en.payoff_sentence_end(WORDS, 6.8) == 7.0     # last sentence never punctuated
    assert en.payoff_sentence_end([], 3.0) is None
    assert en.payoff_sentence_end(None, 3.0) is None


def test_sentence_ends_from_pauses_when_words_have_no_punctuation():
    # faster-whisper / Groq / YouTube-caption words are bare: no word
    # carries punctuation, so pauses are the only sentence evidence.
    # (Measured on the streamer fixture: 607 real Whisper words, zero
    # punctuation marks — a punctuation-only rule put every sentence
    # end at the last word of the file and disabled the refinement.)
    bare = sentence(
        ("fitness", 16.0, 16.4), ("mission", 16.5, 16.9), ("because", 17.0, 17.5),
        ("we", 18.4, 18.6), ("train", 18.7, 19.1), ("hard", 19.2, 19.6),
        ("today", 19.7, 20.3), ("we", 20.4, 20.6), ("push", 20.7, 21.1),
    )
    assert en.sentence_end_times(bare) == [17.5, 21.1]
    assert en.payoff_sentence_end(bare, 19.0) == 21.1
    assert en.payoff_sentence_end(bare, 17.2) == 17.5


def test_payoff_tight_trims_with_bare_whisper_words():
    bare = sentence(
        ("he", 18.0, 18.2), ("screamed", 18.3, 19.2),
        ("wow", 30.0, 30.4),  # after a long pause: a new sentence
    )
    new_end, rec = en.refine_clip_end(0.0, 40.0, payoff_end=19.0, words=bare,
                                      min_seconds=12.0, loop_on=False, tight_on=True)
    assert rec["applied"] is True
    assert new_end == pytest.approx(19.2 + 0.35)


def test_payoff_completion_extends_over_the_die_down():
    assert en.payoff_completion(20.0) == 20.0
    strength = [0.0] * 30
    strength[20] = 1.0
    strength[21] = 0.5
    assert en.payoff_completion(20.0, strength) == 22.0
    assert en.payoff_completion(20.0, [1.0] * 40) == 24.0  # capped at +4 s


# ---------------------------------------------------------------------------
# Payoff-tight
# ---------------------------------------------------------------------------

def test_payoff_tight_trims_the_dead_tail_to_the_sentence_end():
    words = sentence(
        ("he", 18.0, 18.2), ("screamed!", 18.3, 19.2),
        ("wow", 30.0, 30.4),  # the next thought, far away
    )
    new_end, rec = en.refine_clip_end(0.0, 40.0, payoff_end=19.0, words=words,
                                      min_seconds=12.0, loop_on=False, tight_on=True)
    assert rec["applied"] is True
    assert new_end == pytest.approx(19.2 + 0.35)
    assert rec["moved_sec"] == pytest.approx(40.0 - 19.55)
    assert "payoff tail trimmed" in rec["reason"]


def test_payoff_tight_sentence_wins_past_the_one_second_mark():
    # The payoff lands at 19.0 but its sentence runs to 23.5: the sentence
    # ends whole even though that is more than 1 s after the payoff.
    words = sentence(
        ("he", 18.0, 18.2), ("kept", 18.3, 18.8), ("going", 18.9, 19.6),
        ("and", 19.8, 20.1), ("going", 20.2, 21.0), ("until", 21.2, 21.8),
        ("the", 21.9, 22.1), ("very", 22.2, 22.6), ("end.", 22.7, 23.5),
        ("next", 30.0, 30.4),
    )
    new_end, rec = en.refine_clip_end(0.0, 40.0, payoff_end=19.0, words=words,
                                      min_seconds=12.0, loop_on=False, tight_on=True)
    assert new_end == pytest.approx(23.5 + 0.35)
    assert "sentence past the 1s mark wins" in rec["reason"]


def test_payoff_tight_never_starts_the_next_word():
    words = sentence(
        ("he", 18.0, 18.2), ("screamed!", 18.3, 19.2),
        ("wow", 19.45, 19.9),  # next word starts inside the pad
    )
    new_end, _rec = en.refine_clip_end(0.0, 40.0, payoff_end=19.0, words=words,
                                       min_seconds=12.0, loop_on=False, tight_on=True)
    assert new_end == pytest.approx(19.45 - 0.03)


def test_payoff_tight_floor_refuses_the_trim(monkeypatch):
    monkeypatch.setenv("CLIP_MIN_SECONDS", "30")
    words = sentence(("he", 9.0, 9.2), ("screamed!", 9.3, 10.1))
    new_end, rec = en.refine_clip_end(0.0, 32.0, payoff_end=10.0, words=words,
                                      loop_on=False, tight_on=True)  # min_seconds=None -> CLIP_MIN_SECONDS
    assert new_end == 32.0
    assert rec["applied"] is False
    assert "floor" in rec["tight_refused"]


def test_payoff_tight_without_words_uses_the_payoff_plus_pad():
    new_end, rec = en.refine_clip_end(0.0, 40.0, payoff_end=20.0, words=None,
                                      min_seconds=12.0, loop_on=False, tight_on=True)
    assert new_end == pytest.approx(20.35)
    assert rec["applied"] is True


def test_payoff_tight_never_extends():
    new_end, rec = en.refine_clip_end(0.0, 20.0, payoff_end=25.0, words=None,
                                      min_seconds=12.0, loop_on=False, tight_on=True)
    assert new_end == 20.0
    assert rec["applied"] is False


# ---------------------------------------------------------------------------
# Loop endings
# ---------------------------------------------------------------------------

def test_verbal_loop_match_scores():
    opening = sentence(("let's", 0.0, 0.3), ("go", 0.35, 0.6), ("right", 0.65, 1.0), ("now", 1.05, 1.4))
    assert en.verbal_loop_match(opening, sentence(("let's", 9.0, 9.3), ("go!", 9.35, 9.7))) == (0.85, "let's go")
    assert en.verbal_loop_match(opening, sentence(("let's", 9.0, 9.5)))[0] == 0.6
    # A rhyme whose last word is a filler word is not a loop.
    the_opening = sentence(("the", 0.0, 0.2), ("site", 0.25, 0.6))
    assert en.verbal_loop_match(the_opening, sentence(("the", 9.0, 9.2))) == (0.0, "")
    assert en.verbal_loop_match([], opening) == (0.0, "")


def _loop_words():
    """Opening 'let's go right now'; the payoff sentence ends at 30.5; a
    closing sentence ends at 36.0 on the same 'let's go'; speech runs on."""
    return sentence(
        ("let's", 10.0, 10.3), ("go", 10.35, 10.6), ("right", 10.65, 11.0), ("now", 11.05, 11.4),
        ("he", 28.0, 28.2), ("saw", 28.3, 28.7), ("it.", 28.8, 29.4),
        ("that", 29.6, 29.9), ("was", 30.0, 30.2), ("insane.", 30.25, 30.5),
        ("we", 34.0, 34.2), ("did", 34.3, 34.6), ("it,", 34.7, 35.1), ("let's", 35.2, 35.5), ("go!", 35.6, 36.0),
        ("and", 36.5, 36.8), ("then", 36.9, 37.3), ("we", 37.4, 37.7), ("left", 37.8, 38.2),
    )


def test_loop_pulls_the_end_to_the_rhyming_sentence():
    new_end, rec = en.refine_clip_end(10.0, 38.0, payoff_end=30.0, words=_loop_words(),
                                      min_seconds=12.0, loop_on=True, tight_on=False)
    assert rec["applied"] is True
    assert new_end == pytest.approx(36.0 + 0.35)
    assert "let's go" in rec["reason"]


def test_loop_bonus_is_bounded_by_epsilon():
    # The only rhyming sentence end is the payoff's own, 7+ s before the
    # current end: even a perfect rhyme (bonus <= LOOP_ENDING_EPSILON)
    # cannot pull the end that far. The end stays.
    words = sentence(
        ("let's", 10.0, 10.3), ("go", 10.35, 10.6),
        ("he", 29.0, 29.2), ("shouted", 29.3, 29.9), ("let's", 30.0, 30.3), ("go!", 30.4, 30.5),
        ("and", 36.5, 36.8), ("then", 36.9, 37.3), ("we", 37.4, 37.7), ("left.", 37.8, 38.2),
    )
    new_end, rec = en.refine_clip_end(10.0, 38.2, payoff_end=30.0, words=words,
                                      min_seconds=12.0, loop_on=True, tight_on=False)
    assert new_end == 38.2
    assert rec["applied"] is False
    # The bound itself: max pull = EPSILON / RATE seconds.
    assert en.LOOP_ENDING_EPSILON / en.LOOP_DISTANCE_RATE == pytest.approx(2.5)


def test_loop_never_cuts_the_payoffs_sentence():
    # A rhyming sentence ends BEFORE the payoff's sentence completes:
    # it is not a legal candidate, however well it rhymes.
    words = sentence(
        ("let's", 10.0, 10.3), ("go", 10.35, 10.6),
        ("let's", 24.0, 24.3), ("go!", 24.4, 24.8),
        ("he", 29.0, 29.2), ("kept", 29.3, 29.8), ("screaming", 29.9, 30.6), ("loudly.", 30.7, 31.4),
        ("and", 36.5, 36.8), ("then", 36.9, 37.3), ("we", 37.4, 37.7), ("left", 37.8, 38.2),
    )
    new_end, rec = en.refine_clip_end(10.0, 38.0, payoff_end=30.0, words=words,
                                      min_seconds=12.0, loop_on=True, tight_on=False)
    assert new_end == 38.0
    assert rec["applied"] is False


def test_loop_visual_rhyme_alone_moves_only_a_near_tie():
    # No words: candidates are the anchor and the current end. A perfect
    # frame rhyme (visual weight 0.3 of the epsilon budget) pulls at most
    # 0.75 s — a 0.5 s gap moves, a 2 s gap does not.
    visual = lambda c: 1.0 if abs(c - 20.35) < 0.01 else 0.0  # only the anchor frame rhymes
    near, rec = en.refine_clip_end(0.0, 20.5, payoff_end=20.0, words=None, min_seconds=12.0,
                                   loop_on=True, tight_on=False, visual=visual)
    assert near == pytest.approx(20.35)
    assert rec["applied"] is True
    far, rec2 = en.refine_clip_end(0.0, 22.0, payoff_end=20.0, words=None, min_seconds=12.0,
                                   loop_on=True, tight_on=False, visual=visual)
    assert far == 22.0
    assert rec2["applied"] is False


def test_tight_wins_over_loop_when_both_on():
    new_end, rec = en.refine_clip_end(10.0, 38.0, payoff_end=30.0, words=_loop_words(),
                                      min_seconds=12.0, loop_on=True, tight_on=True)
    # Tight places the end on the payoff anchor (sentence end 30.5 + pad);
    # the loop stage then has no legal candidate left to choose.
    assert new_end == pytest.approx(30.5 + 0.35)
    assert "payoff tail trimmed" in rec["reason"]


# ---------------------------------------------------------------------------
# Wiring: the streamer picker
# ---------------------------------------------------------------------------

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
    from src.auto_director import gameplay_moments as gm

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


def _shout_pick(burst=(90, 94)):
    v, f = _voice(), _face()
    a, b = burst
    v["vocal"][a:b] += 15
    v["f0"][a:b] += 120
    f[a:b] += 12
    return rm.select_streamer_moments("x.mp4", 120.0, target_count=5, clip_seconds=30,
                                       voice=v, face=f, game_evidence=_game(events=[(88, 91)]))


def test_picker_identical_with_switches_off(monkeypatch):
    monkeypatch.delenv("LOOP_ENDINGS", raising=False)
    monkeypatch.delenv("PAYOFF_TIGHT", raising=False)
    out = _shout_pick()
    assert out, "the synthetic shout must produce a moment"
    assert all("ending" not in (m.get("evidence") or {}) for m in out)


def test_picker_payoff_tight_trims_the_window_end(monkeypatch):
    # A 10 s burst shapes a ~19 s window: long enough that trimming the
    # tail to the payoff anchor stays above the picker's 12 s floor.
    monkeypatch.delenv("LOOP_ENDINGS", raising=False)
    monkeypatch.delenv("PAYOFF_TIGHT", raising=False)
    off = _shout_pick(burst=(90, 100))
    monkeypatch.setenv("PAYOFF_TIGHT", "on")
    on = _shout_pick(burst=(90, 100))
    assert len(on) == len(off) == 1
    assert on[0]["end_time"] < off[0]["end_time"]
    rec = on[0]["evidence"]["ending"]
    assert rec["applied"] is True
    assert "ending" not in off[0]["evidence"]


# ---------------------------------------------------------------------------
# Wiring: VOD pick refinement
# ---------------------------------------------------------------------------

def _vod_words():
    return sentence(
        ("he", 133.0, 133.2), ("screamed!", 133.3, 134.2),
        ("wow", 150.0, 150.4),
    )


def test_vod_refine_pick_endings_off_is_untouched(monkeypatch):
    monkeypatch.delenv("LOOP_ENDINGS", raising=False)
    monkeypatch.delenv("PAYOFF_TIGHT", raising=False)
    picks = [{"id": 1, "start": 100.0, "end": 150.0, "onset": 115.0, "words": _vod_words()}]
    candidates = [{"id": 1, "onset": 115.0, "end": 119.0, "words": _vod_words()}]
    assert vod.refine_pick_endings(picks, candidates) == picks


def test_vod_refine_pick_endings_trims_and_respects_the_floor(monkeypatch):
    monkeypatch.setenv("CLIP_MIN_SECONDS", "30")
    monkeypatch.setenv("PAYOFF_TIGHT", "on")
    words = _vod_words()
    picks = [{"id": 1, "start": 100.0, "end": 160.0, "onset": 130.0, "words": words},
             {"id": 2, "start": 200.0, "end": 232.0, "onset": 205.0, "words": []}]
    candidates = [{"id": 1, "onset": 130.0, "end": 134.0, "words": words},
                  {"id": 2, "onset": 205.0, "end": 206.0, "words": []}]
    out = vod.refine_pick_endings(picks, candidates)
    # Pick 1: payoff burst ends 134.0, its sentence ends 134.2 -> 134.55,
    # a 34.55 s clip: above the 30 s floor, so the trim stands.
    assert out[0]["end"] == pytest.approx(134.55)
    assert out[0]["ending"]["applied"] is True
    # Pick 2: tightening to ~206 would break the 30 s floor -> refused.
    assert out[1]["end"] == 232.0
    assert "ending" not in out[1]
