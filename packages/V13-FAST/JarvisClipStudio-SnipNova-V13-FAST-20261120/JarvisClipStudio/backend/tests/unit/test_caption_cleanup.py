"""Step 5: repetition-loop filter and phrase-split captions (clip1 root cause #6)."""
from __future__ import annotations

from src.media import caption_cleanup as cc


def _words(text: str, step: float = 0.3, gap_after: dict | None = None):
    out, t = [], 0.0
    for i, w in enumerate(text.split()):
        out.append({"text": w, "start": round(t, 3), "end": round(t + step - 0.05, 3)})
        t += step + (gap_after or {}).get(i, 0.0)
    return out


def test_clip1_loop_is_captioned_once():
    phrase = "I'm like, I just want to have a war."
    words = _words("Yes, " + " ".join([phrase] * 6) + " Okay then.")
    kept, dropped = cc.drop_repetition_loops(words)
    text = " ".join(w["text"] for w in kept)
    assert text == "Yes, I'm like, I just want to have a war. Okay then."
    assert dropped == 9 * 5  # nine-word phrase, five extra repeats


def test_real_speech_repeats_are_kept():
    for text in ("no no no no stop", "go go go", "I said yes, I said yes, okay"):
        kept, dropped = cc.drop_repetition_loops(_words(text))
        assert dropped == 0, text


def test_phrase_split_never_leaves_an_orphan_word():
    words = _words("I just want to have a war.")  # 7 words, max 3
    spans = cc.split_word_spans_phrase(words, 3, 0.6)
    lines = [" ".join(w["text"] for w in words[a:b]) for a, b in spans]
    assert all(len(line.split()) >= 2 for line in lines), lines
    assert lines == ["I just want", "to have", "a war."]


def test_phrase_split_breaks_at_commas_and_sentences():
    words = _words("Yes, I'm like, I just want to win. Okay.")
    spans = cc.split_word_spans_phrase(words, 3, 0.6)
    lines = [" ".join(w["text"] for w in words[a:b]) for a, b in spans]
    assert lines[0] == "Yes, I'm like,"
    assert "win." in lines[-2] and lines[-1] == "Okay."
    assert all(not line.endswith(("I", "a", "the", "to")) for line in lines[:-1]), lines


def test_phrase_split_breaks_on_long_pause():
    words = _words("we waited here and then it happened", gap_after={3: 1.0})
    spans = cc.split_word_spans_phrase(words, 4, 0.6)
    assert (0, 4) in spans


def test_switches_default_off(monkeypatch):
    monkeypatch.delenv("CAPTION_LOOP_FILTER", raising=False)
    monkeypatch.delenv("CAPTION_PHRASE_SPLIT", raising=False)
    assert not cc.loop_filter_enabled() and not cc.phrase_split_enabled()
