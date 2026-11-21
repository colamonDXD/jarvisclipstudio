"""Phase 2 QC checks: platform loudness and complete-thought boundaries (warn-only, opt-in)."""
from src.media.clip_qc import check_loudness, check_thought_boundaries


def _w(text, start, end):
    return {"text": text, "start": start, "end": end}


WORDS = [
    _w("Okay.", 0.0, 0.4), _w("Stocks", 1.2, 1.6), _w("are", 1.6, 1.8), _w("ownership.", 1.8, 2.4),
    _w("When", 2.6, 2.8), _w("you", 2.8, 3.0), _w("own", 3.0, 3.2), _w("stock", 3.2, 3.6), _w("you", 3.7, 3.8),
    _w("own", 3.8, 4.0), _w("a", 4.0, 4.1), _w("company.", 4.1, 4.7), _w("Bonds", 5.5, 5.9), _w("are", 5.9, 6.1),
]


def test_clean_thought_passes():
    r = check_thought_boundaries(WORDS, 1.2, 4.8)
    assert r["status"] == "pass"


def test_opening_mid_sentence_warns():
    r = check_thought_boundaries(WORDS, 2.8, 4.8)
    assert r["status"] == "warn" and "opens mid-sentence" in r["detail"]


def test_ending_mid_sentence_and_cut_word_warn():
    r = check_thought_boundaries(WORDS, 1.2, 3.5)
    assert r["status"] == "warn" and "ends mid-sentence" in r["detail"] and "cuts a word" in r["detail"]


def test_speech_free_clip_is_skipped():
    assert check_thought_boundaries(WORDS, 20.0, 30.0)["status"] == "skip"


def test_loudness_bands(tmp_path):
    f = tmp_path / "x.mp4"
    assert check_loudness(f, -14.0, measured=-14.8)["status"] == "pass"
    assert check_loudness(f, -14.0, measured=-23.0)["status"] == "warn"
    assert "too quiet" in check_loudness(f, -14.0, measured=-23.0)["detail"]
    assert "too loud" in check_loudness(f, -14.0, measured=-9.0)["detail"]
