"""Self-run audit fixes — B-roll relevance (issues #1, #7).

The audit proved two things on the Japan r1 food window (890-910 s):
the EDL B-roll plan was [] because the trigger vocabulary is a fixed
20-noun list that food/travel words can never hit, and the cars
insertion on Luffy's trading clip came from Pixabay hit #1 with zero
relevance check, placed at a segment boundary over unrelated speech.

These tests pin the fix: a wider curated vocabulary, word-anchored
placement (the cue opens where its query word is spoken, with the
segment boundary kept as fallback), spoken context stamped on the
placement, and a relevance gate on Pixabay hits that skips a cue
rather than inserting unrelated footage. The render test runs real
FFmpeg with a mocked Pixabay search (no key in this sandbox).
"""

import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from src.auto_director.edl import (
    _broll_context,
    _create_broll_queries,
    build_edl,
    load_profile,
)
from src.media import edl_render, pixabay_broll


@pytest.fixture
def v2_cadence(monkeypatch):
    """Smart B-Roll v2 cadence + keyword cue source (``BROLL_CADENCE=legacy``).

    Tests using it pin v2's exact cues and counts on real profiles. v2 stays
    selectable through that switch, so these pins remain meaningful. Since
    2026-11-17 the default is v3 (``cadence_per_min`` + lexicon cues), whose
    guards, sense handling and counts are covered in test_broll_v3_cadence.py.
    """
    monkeypatch.setenv("BROLL_CADENCE", "legacy")


@pytest.fixture(autouse=True)
def _clear_search_cache():
    pixabay_broll._search_cache.clear()
    yield
    pixabay_broll._search_cache.clear()


@pytest.fixture()
def api_key(monkeypatch):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")


# --------------------------------------------------------------------------
# Query derivation: the trigger vocabulary (issue #7)
# --------------------------------------------------------------------------

def _words_with_times(*pairs):
    return [
        {"word": word, "start": float(start), "end": float(start) + 0.4}
        for word, start in pairs
    ]


def test_food_words_trigger_queries_with_spoken_time():
    # The audit's r1 window: avocado/wagyu/sushi/sandwich/gold spoken,
    # zero of the 20 base nouns present — the old plan was [].
    # (A 180 s travel clip: the density cap allows all six queries.)
    profile = load_profile("travel_vlog")
    words = _words_with_times(
        ("avocado", 1.0), ("wagyu", 5.0), ("sushi", 9.0),
        ("sandwich", 13.0), ("gold", 16.0), ("tree", 3.0),
    )
    queries = _create_broll_queries(profile, words, 180.0)
    by_query = {q["query"]: q for q in queries}
    for noun, at in (
        ("avocado", 1.0), ("wagyu", 5.0), ("sushi", 9.0),
        ("sandwich", 13.0), ("gold", 16.0), ("tree", 3.0),
    ):
        assert noun in by_query, f"{noun} should trigger a query"
        assert by_query[noun]["spoken_at"] == at


def test_word_matching_is_not_substring():
    # "careful" must not fire the "car" query; "avocado." with trailing
    # punctuation must still fire "avocado".
    profile = load_profile("podcast_news")
    words = _words_with_times(("careful", 1.0), ("avocado.", 4.0))
    queries = _create_broll_queries(profile, words, 20.0)
    by_query = {q["query"] for q in queries}
    assert "car" not in by_query
    assert "avocado" in by_query


def test_plural_forms_trigger():
    profile = load_profile("travel_vlog")
    words = _words_with_times(("noodles", 2.0), ("trees", 6.0))
    queries = _create_broll_queries(profile, words, 180.0)
    by_query = {q["query"] for q in queries}
    assert "noodle" in by_query
    assert "tree" in by_query


# --------------------------------------------------------------------------
# Placement anchoring (issue #1)
# --------------------------------------------------------------------------

def test_placement_anchors_on_the_spoken_word():
    # A 20 s podcast clip (density 1.5/min -> at most 1 cue): the cue
    # must open where "avocado" is spoken, not at a segment boundary.
    profile = load_profile("podcast_news")
    clip = {"id": "r1", "start": 0, "end": 20, "duration": 20}
    words = _words_with_times(
        ("That's", 0.0), ("pretty", 0.4), ("good", 0.8),
        ("avocado", 6.0), ("Wow", 6.5), ("That's", 7.0),
        ("perfect", 7.5), ("avocado", 8.0),
    )
    edl = build_edl(clip, words, profile, [])
    assert len(edl["broll"]) == 1
    entry = edl["broll"][0]
    assert entry["query"] == "avocado"
    assert entry["start"] == 6.0  # first "avocado", not the 10 s boundary
    assert entry["end"] == 9.0
    assert "avocado" in entry["context"]
    assert "Wow" in entry["context"]


def test_placement_clamps_to_the_hook_guard():
    # A word spoken inside the opening hook still gets its cue, at the
    # guard — never over the hook itself.
    profile = load_profile("podcast_news")
    clip = {"id": "r1", "start": 0, "end": 20, "duration": 20}
    words = _words_with_times(("avocado", 0.5), ("tree", 12.0))
    edl = build_edl(clip, words, profile, [])
    assert len(edl["broll"]) == 1
    assert edl["broll"][0]["start"] == 2.0


@pytest.mark.usefixtures("v2_cadence")
def test_cars_style_case_lands_on_the_car_sentence():
    # Luffy's trading clip: "car" spoken once, late, in unrelated
    # speech. The cue must land on that sentence — the fix for
    # unrelated highway footage over unrelated words is the anchor
    # (plus the relevance gate below), not the segment boundary.
    profile = load_profile("podcast_news")
    clip = {"id": "trading", "start": 0, "end": 55, "duration": 55}
    words = (
        [{"word": "stock", "start": 0.5, "end": 0.9}]
        + [{"word": "market", "start": 1.0, "end": 1.4}]
        + [{"word": "car", "start": 40.0, "end": 40.4}]
        + [{"word": "payment", "start": 40.5, "end": 40.9}]
    )
    edl = build_edl(clip, words, profile, [])
    assert len(edl["broll"]) == 1
    entry = edl["broll"][0]
    assert entry["query"] == "car"
    assert entry["start"] == 40.0
    assert "car" in entry["context"]


def test_blocked_anchor_falls_back_to_segment_boundary():
    # An energy peak at 6.0 s puts a punch-in on the anchored window;
    # the cue must move to a free segment boundary, never onto the
    # punch-in.
    profile = load_profile("travel_vlog")
    clip = {"id": "c", "start": 0, "end": 60, "duration": 60}
    words = _words_with_times(("mountain", 6.0), ("beach", 30.0))
    edl = build_edl(clip, words, profile, [6.0])
    assert edl["broll"]
    for entry in edl["broll"]:
        assert not (entry["start"] < 6.75 and 5.75 < entry["end"]), (
            "B-roll must not cover the punch-in at 6.0s"
        )


def test_broll_context_window():
    words = _words_with_times(
        ("before", 2.0), ("avocado", 6.0), ("after", 7.5),
        ("late", 20.0),
    )
    context = _broll_context(words, 6.0)
    assert "before" in context and "avocado" in context
    assert "after" in context  # +2 s after the window
    assert "late" not in context


# --------------------------------------------------------------------------
# Relevance gate on Pixabay hits (issue #1)
# --------------------------------------------------------------------------

def _hit(asset_id, tags, page_url="https://pixabay.com/videos/x"):
    return {
        "id": asset_id,
        "pageURL": page_url,
        "tags": tags,
        "duration": 12,
        "videos": {
            "large": {"url": f"https://cdn.pixabay.com/{asset_id}.mp4",
                      "width": 1920, "height": 1080, "size": 1000},
        },
    }


def _mock_client(payload):
    def handler(request):
        if "pixabay.com/api" in str(request.url):
            return httpx.Response(200, json=payload)
        return httpx.Response(200, content=b"fake-mp4-bytes")
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_keeps_tags_for_the_gate(api_key):
    payload = {"total": 1, "totalHits": 1,
               "hits": [_hit(1, "avocado, fruit, food")]}
    results = pixabay_broll.search_pixabay_videos(
        "avocado", client=_mock_client(payload)
    )
    assert results[0]["tags"] == "avocado, fruit, food"


def test_gate_skips_off_topic_first_hit(api_key, tmp_path):
    # Hit #1 is highway traffic for an avocado cue; hit #2 is the
    # avocado footage. The old code took hit #1 unconditionally.
    payload = {"total": 2, "totalHits": 2, "hits": [
        _hit(11, "highway, traffic, cars, road"),
        _hit(22, "avocado, fruit, food, healthy"),
    ]}
    asset = pixabay_broll.ensure_pixabay_asset(
        "avocado", cache_dir=tmp_path,
        client=_mock_client(payload),
        context="avocado tree 700 years old",
    )
    assert asset is not None
    assert asset["asset_id"] == 22
    assert Path(asset["local_path"]).is_file()


def test_gate_skips_the_cue_when_no_hit_is_relevant(api_key, tmp_path):
    payload = {"total": 1, "totalHits": 1, "hits": [
        _hit(11, "highway, traffic, cars, road"),
    ]}
    asset = pixabay_broll.ensure_pixabay_asset(
        "avocado", cache_dir=tmp_path,
        client=_mock_client(payload),
        context="avocado tree 700 years old",
    )
    assert asset is None
    assert list(tmp_path.iterdir()) == []  # nothing downloaded


def test_gate_rejects_sensitive_theme_hit(api_key, tmp_path):
    payload = {"total": 1, "totalHits": 1, "hits": [
        _hit(33, "depressed woman lying on bed with pills"),
    ]}
    asset = pixabay_broll.ensure_pixabay_asset(
        "bedroom", cache_dir=tmp_path,
        client=_mock_client(payload),
        context="the bedroom looks nice",
    )
    assert asset is None


def test_gate_allows_untagged_hit(api_key, tmp_path):
    # A hit with no tags cannot be judged; the legacy system allows
    # undescribed footage, and so does this gate.
    payload = {"total": 1, "totalHits": 1, "hits": [_hit(44, "")]}
    asset = pixabay_broll.ensure_pixabay_asset(
        "avocado", cache_dir=tmp_path, client=_mock_client(payload)
    )
    assert asset is not None
    assert asset["asset_id"] == 44


def test_resolve_passes_spoken_context(api_key, tmp_path):
    # The entry's stamped context is what the gate judges against.
    payload = {"total": 1, "totalHits": 1, "hits": [
        _hit(11, "highway, traffic, cars, road"),
    ]}
    edl = {"broll": [{
        "query": "avocado", "local_path": "",
        "context": "avocado tree 700 years old",
    }]}
    assert pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(payload)
    ) == 0
    assert edl["broll"][0]["local_path"] == ""


# --------------------------------------------------------------------------
# Render-level proof (real FFmpeg, mocked Pixabay search)
# --------------------------------------------------------------------------

def _make_video(path: Path, color: str, duration: int, audio_freq: int = 440):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:size=320x240:rate=30:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency={audio_freq}:sample_rate=48000:duration={duration}",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True, capture_output=True, timeout=120,
    )


def _frame_rgb(path: Path, at: float) -> tuple[float, float, float]:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x64", "-",
        ],
        check=True, capture_output=True, timeout=120,
    )
    data = result.stdout
    n = len(data) // 3
    return (
        sum(data[0::3]) / n, sum(data[1::3]) / n, sum(data[2::3]) / n,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
@pytest.mark.usefixtures("v2_cadence")
def test_render_food_window_plans_relevant_cue_and_composites_it(
    api_key, tmp_path
):
    # End to end on the audit's shape: a 20 s podcast clip whose words
    # are the r1 food window. The plan now carries an avocado cue
    # anchored on the spoken word; the mocked Pixabay search returns a
    # traffic hit first (rejected by the gate) and an avocado hit second
    # (accepted); the render composites the avocado stock exactly over
    # the anchored window and nowhere else.
    source = tmp_path / "source.mp4"
    _make_video(source, "red", 20)
    stock = tmp_path / "pixabay_22.mp4"
    _make_video(stock, "blue", 8, audio_freq=1200)

    profile = load_profile("podcast_news")
    clip = {"id": "r1", "start": 0, "end": 20, "duration": 20}
    words = _words_with_times(
        ("That's", 0.0), ("pretty", 0.4), ("good", 0.8),
        ("avocado", 6.0), ("Wow", 6.5), ("perfect", 7.0),
    )
    edl = build_edl(clip, words, profile, [])
    assert len(edl["broll"]) == 1
    assert edl["broll"][0]["query"] == "avocado"
    assert edl["broll"][0]["start"] == 6.0

    payload = {"total": 2, "totalHits": 2, "hits": [
        _hit(11, "highway, traffic, cars, road"),
        _hit(22, "avocado, fruit, food, healthy"),
    ]}

    def handler(request):
        if "pixabay.com/api" in str(request.url):
            return httpx.Response(200, json=payload)
        return httpx.Response(200, content=stock.read_bytes())

    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert resolved == 1
    assert edl["broll"][0]["asset_id"] == 22

    output = tmp_path / "out.mp4"
    assert edl_render.render_edl_clip_ffmpeg(source, output, edl) is True

    # Before the window the speaker (red); inside it the stock (blue);
    # after it the speaker again.
    r_before, _, b_before = _frame_rgb(output, 1.0)
    r_in, _, b_in = _frame_rgb(output, 7.5)
    r_after, _, b_after = _frame_rgb(output, 12.0)
    assert r_before > 120 and b_before < 90
    assert b_in > 120 and r_in < 90
    assert r_after > 120 and b_after < 90
