"""B-roll word-sense fix: polysemous triggers ("train" the exercise verb).

Evidence (Luffy's upload, 2026-11-10): a clip whose speech is about
TRAINING — hook title "New Mission: Train For Diamond Gym" — got a
full-screen scenic stock clip of a railway bridge over a river as
B-roll. The chain: "train" sits in the EDL trigger vocabulary with the
travel nouns, word-level matching cannot tell the verb from the
vehicle, the Pixabay search was the bare noun "train" (a stock library
reads that as railway-only), and the relevance gate let the hit
through because its tags share the word "train" itself — the gate
never compared the sense the speech indicates with the sense the
hit's tags indicate.

These tests pin the fix at all three layers: the planner judges each
occurrence's sense (skip / re-sense), the search carries the indicated
sense's spoken context words, and the gate vetoes a hit whose own
metadata shows the wrong sense even when it matches the bare word.
"""

from pathlib import Path

import httpx
import pytest

from src import broll
from src.auto_director.edl import (
    _create_broll_queries,
    build_edl,
    load_profile,
)
from src.media import pixabay_broll


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


def _words_with_times(*pairs):
    return [
        {"word": word, "start": float(start), "end": float(start) + 0.4}
        for word, start in pairs
    ]


# The evidence sentence shape: "train" is the exercise verb.
GYM_WORDS = (
    ("New", 0.0), ("mission", 0.5), ("train", 3.0), ("for", 3.4),
    ("Diamond", 3.8), ("Gym", 4.3), ("is", 4.8), ("the", 5.1),
    ("plan", 5.5), ("today", 6.0),
)

RAILWAY_WORDS = (
    ("we", 0.0), ("took", 0.5), ("the", 0.8), ("train", 3.0),
    ("to", 3.4), ("the", 3.6), ("station", 4.0), ("this", 4.6),
    ("morning", 5.0),
)

GYM_CONTEXT = "new mission train for diamond gym"
RAILWAY_CONTEXT = "we took the train to the station this morning"


# --------------------------------------------------------------------------
# Sense machinery (src.broll)
# --------------------------------------------------------------------------

def test_indicated_sense_reads_the_context():
    assert broll.indicated_sense("train", GYM_CONTEXT) == "exercise"
    assert broll.indicated_sense("train", RAILWAY_CONTEXT) == "railway"
    # "training" stays an exercise signal; it must not stem into "train".
    assert broll.indicated_sense("train", "training day at the office") == "exercise"


def test_indicated_sense_needs_a_clear_signal():
    # No markers at all -> no signal -> nothing may be vetoed or skipped.
    assert broll.indicated_sense("train", "the train was late again") is None
    # One marker each way is a tie, not a signal.
    assert broll.indicated_sense("train", "train station gym") is None
    # Unambiguous words have no senses to indicate.
    assert broll.indicated_sense("avocado", "avocado tree") is None


def test_search_query_carries_sense_context():
    assert broll.broll_search_query("train", RAILWAY_CONTEXT) == "train station"
    # No clear signal -> the bare noun, exactly as before.
    assert broll.broll_search_query("train", "the train was late") == "train"
    # Unambiguous nouns never grow extra words.
    assert broll.broll_search_query("avocado", "avocado tree 700 years old") == "avocado"


def test_footage_mismatch_names_the_sense_conflict():
    video = {"title": "train crossing a railway bridge over a river", "tags": []}
    reason = broll.footage_mismatch(video, "train", GYM_CONTEXT)
    assert reason is not None
    assert "exercise" in reason and "railway" in reason
    # Same hit, railway speech: no conflict.
    assert broll.footage_mismatch(video, "train", RAILWAY_CONTEXT) is None


# --------------------------------------------------------------------------
# Planner: occurrence-level sense judgement (auto_director/edl.py)
# --------------------------------------------------------------------------

def test_gym_train_plans_no_railway_cue():
    # The evidence case: with the fix, "train" plans nothing here and
    # the slot goes to the word that was actually meant — "gym".
    profile = load_profile("travel_vlog")
    queries = _create_broll_queries(profile, _words_with_times(*GYM_WORDS), 60.0)
    by_query = {q["query"] for q in queries}
    assert "train" not in by_query
    assert "gym" in by_query


def test_gym_train_build_edl_has_no_train_broll():
    profile = load_profile("podcast_news")
    clip = {"id": "gym", "start": 0, "end": 20, "duration": 20}
    edl = build_edl(clip, _words_with_times(*GYM_WORDS), profile, [])
    assert all(entry["query"] != "train" for entry in edl["broll"])


def test_railway_train_still_plans_with_context_search():
    # A genuine railway sentence keeps its cue, anchored on the word,
    # and the stock search carries the sense context: "train station",
    # not the bare noun.
    profile = load_profile("travel_vlog")
    words = _words_with_times(*RAILWAY_WORDS)
    queries = _create_broll_queries(profile, words, 60.0)
    train = next(q for q in queries if q["query"] == "train")
    assert train["spoken_at"] == 3.0

    clip = {"id": "rail", "start": 0, "end": 20, "duration": 20}
    edl = build_edl(clip, words, profile, [])
    entry = next(e for e in edl["broll"] if e["query"] == "train")
    assert entry["start"] == 3.0
    assert entry["search_query"] == "train station"


def test_later_genuine_occurrence_still_plans():
    # Occurrences are judged one by one: the exercise use at 3 s is
    # skipped, the railway use at 30 s still plans its cue.
    profile = load_profile("travel_vlog")
    words = _words_with_times(
        ("train", 3.0), ("for", 3.4), ("the", 3.8), ("gym", 4.2),
        ("then", 28.0), ("the", 28.5), ("train", 30.0), ("to", 30.4),
        ("the", 30.6), ("station", 31.0), ("home", 31.6),
    )
    queries = _create_broll_queries(profile, words, 60.0)
    train = [q for q in queries if q["query"] == "train"]
    assert len(train) == 1
    assert train[0]["spoken_at"] == 30.0


def test_pool_resenses_to_billiards_and_swimming_keeps_pool():
    profile = load_profile("travel_vlog")
    billiards = _create_broll_queries(profile, _words_with_times(
        ("we", 0.0), ("played", 0.5), ("pool", 3.0), ("at", 3.4),
        ("the", 3.6), ("snooker", 4.0), ("hall", 4.6), ("cue", 5.2),
        ("in", 5.6), ("hand", 6.0),
    ), 60.0)
    by_query = {q["query"] for q in billiards}
    assert "billiards" in by_query
    assert "pool" not in by_query

    swimming = _create_broll_queries(profile, _words_with_times(
        ("the", 0.0), ("kids", 0.5), ("were", 1.0), ("swimming", 1.5),
        ("in", 2.2), ("the", 2.5), ("pool", 3.0), ("doing", 3.6),
        ("laps", 4.0), ("all", 4.5), ("day", 5.0),
    ), 60.0)
    assert "pool" in {q["query"] for q in swimming}


def test_race_humanity_skips_and_competition_keeps():
    profile = load_profile("travel_vlog")
    humanity = _create_broll_queries(profile, _words_with_times(
        ("the", 0.0), ("human", 0.5), ("race", 1.2), ("has", 1.8),
        ("always", 2.2), ("moved", 2.8), ("across", 3.4), ("lands", 4.0),
    ), 60.0)
    assert "race" not in {q["query"] for q in humanity}

    competition = _create_broll_queries(profile, _words_with_times(
        ("he", 0.0), ("won", 0.5), ("the", 0.9), ("race", 1.4),
        ("at", 2.0), ("the", 2.3), ("marathon", 2.8), ("finish", 3.6),
        ("line", 4.2),
    ), 60.0)
    assert "race" in {q["query"] for q in competition}


# --------------------------------------------------------------------------
# Gate: wrong-sense hits are vetoed at resolve time (pixabay_broll.py)
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


RAILWAY_HIT = _hit(11, "train, railway, bridge, river, sunset, landscape")
FITNESS_HIT = _hit(22, "gym, workout, fitness, training, dumbbell")


def test_gate_rejects_railway_hit_for_gym_speech(api_key, tmp_path):
    # Defence in depth: even if a "train" cue reaches the resolver
    # with gym speech stamped as its context, the railway hit's own
    # tags convict it — sharing the bare word does not save it.
    payload = {"total": 1, "totalHits": 1, "hits": [RAILWAY_HIT]}
    asset = pixabay_broll.ensure_pixabay_asset(
        "train", cache_dir=tmp_path,
        client=_mock_client(payload), context=GYM_CONTEXT,
    )
    assert asset is None
    assert list(tmp_path.iterdir()) == []  # nothing downloaded


def test_gate_picks_fitness_hit_for_gym_speech(api_key, tmp_path):
    payload = {"total": 2, "totalHits": 2, "hits": [RAILWAY_HIT, FITNESS_HIT]}
    asset = pixabay_broll.ensure_pixabay_asset(
        "train", cache_dir=tmp_path,
        client=_mock_client(payload), context=GYM_CONTEXT,
    )
    assert asset is not None
    assert asset["asset_id"] == 22
    assert Path(asset["local_path"]).is_file()


def test_gate_rejects_exercise_hit_for_railway_speech(api_key, tmp_path):
    # The fitness hit's tags stem-match the bare word ("training" ->
    # "train"), so the old off-topic check alone would have passed it.
    payload = {"total": 1, "totalHits": 1, "hits": [FITNESS_HIT]}
    asset = pixabay_broll.ensure_pixabay_asset(
        "train", cache_dir=tmp_path,
        client=_mock_client(payload), context=RAILWAY_CONTEXT,
    )
    assert asset is None


def test_gate_accepts_railway_hit_for_railway_speech(api_key, tmp_path):
    payload = {"total": 1, "totalHits": 1, "hits": [RAILWAY_HIT]}
    asset = pixabay_broll.ensure_pixabay_asset(
        "train", cache_dir=tmp_path,
        client=_mock_client(payload), context=RAILWAY_CONTEXT,
    )
    assert asset is not None
    assert asset["asset_id"] == 11


def test_gate_no_veto_without_a_sense_signal(api_key, tmp_path):
    # Speech that gives no sense signal must not start vetoing hits.
    payload = {"total": 1, "totalHits": 1, "hits": [RAILWAY_HIT]}
    asset = pixabay_broll.ensure_pixabay_asset(
        "train", cache_dir=tmp_path,
        client=_mock_client(payload), context="the train was late again today",
    )
    assert asset is not None
    assert asset["asset_id"] == 11


def test_resolve_searches_with_the_stamped_search_query(api_key, tmp_path):
    seen = {}

    def handler(request):
        if "pixabay.com/api" in str(request.url):
            seen["q"] = request.url.params.get("q")
            return httpx.Response(
                200, json={"total": 1, "totalHits": 1, "hits": [RAILWAY_HIT]}
            )
        return httpx.Response(200, content=b"fake-mp4-bytes")

    edl = {"broll": [{
        "query": "train", "search_query": "train station",
        "local_path": "", "context": RAILWAY_CONTEXT,
    }]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert resolved == 1
    assert seen["q"] == "train station"
    # Provenance still records the planned noun, not the search string.
    assert edl["broll"][0]["asset_id"] == 11


# --------------------------------------------------------------------------
# "market": the financial sense vs the street/farmers sense
#
# Evidence (Luffy's upload, 2026-11-12): a TJR trading clip whose only
# B-roll trigger noun is "market" — spoken at 13.54 s inside "but I'm
# due to my probabilities within the market", surrounded by "$96,000 in
# profit", "loss today", "execute on my strategy", "higher odds of
# winning than losing". "market" had no WORD_SENSES entry, so it was
# searched bare, Pixabay's top hits were street markets, and the tag
# gate passed them because their tags contain the word "market" itself.
# TJR_WORDS below is the golden fixture: the full faster-whisper (small)
# word list of that real 28.6 s clip, re-transcribed in the sandbox on
# 2026-11-12 (the design lane's transcript survives only in its session
# log; the segment text matches it word for word).
# --------------------------------------------------------------------------

TJR_WORDS = (
    ("trade", 0.0), ("worth", 0.36), ("$96", 0.66), (",000", 1.44),
    ("in", 1.88), ("profit.", 2.34), ("This", 2.96), ("loss", 3.02),
    ("today,", 3.34), ("or", 3.94), ("these", 4.06), ("two", 4.26),
    ("loss", 4.5), ("today,", 4.68), ("are", 5.26), ("actually", 5.48),
    ("helping", 5.78), ("me", 6.02), ("get", 6.22), ("closer", 6.44),
    ("to", 6.82), ("my", 7.06), ("next", 7.26), ("win,", 7.5),
    ("that", 8.02), ("I", 8.2), ("know,", 8.44), ("it", 9.06),
    ("sounds", 9.22), ("stupid,", 9.78), ("but", 10.28), ("I'm", 10.32),
    ("due", 10.62), ("to", 11.06), ("my", 11.62),
    ("probabilities", 12.46), ("within", 13.1), ("the", 13.44),
    ("market.", 13.54), ("I", 13.94), ("know", 14.0), ("that", 14.14),
    ("10", 14.34), ("times", 15.66), ("out", 15.92), ("of", 16.08),
    ("10,", 16.16), ("when", 16.66), ("I", 16.82), ("execute", 16.94),
    ("on", 17.24), ("my", 17.42), ("strategy,", 17.52), ("I", 18.22),
    ("have", 18.4), ("higher", 18.64), ("odds", 19.0), ("of", 19.44),
    ("winning", 19.68), ("than", 19.98), ("losing,", 20.5),
    ("and", 21.0), ("I", 21.08), ("know", 21.18), ("that", 21.38),
    ("when", 21.54), ("I", 21.68), ("win,", 21.82), ("my", 22.28),
    ("winners", 22.38), ("are", 22.6), ("going", 22.76), ("to", 22.88),
    ("be", 22.96), ("larger", 23.08), ("than", 23.34), ("my", 23.52),
    ("losers,", 23.66), ("and", 24.1), ("that's", 24.22),
    ("all", 24.42), ("that", 24.58), ("I", 24.7), ("care", 24.8),
    ("about.", 24.96), ("So", 25.46), ("whenever", 25.56),
    ("my", 25.76), ("strategy", 25.96), ("presents", 26.3),
    ("itself,", 26.58), ("I'm", 27.1), ("taking", 27.26),
    ("that", 27.48), ("fucking", 27.68), ("trade,", 27.84),
)

# The clip's speech up to the end of the "care about" sentence — every
# financial marker the sense judgement needs is inside it.
TJR_CONTEXT = (
    "trade worth $96,000 in profit. this loss today, or these two loss "
    "today, are actually helping me get closer to my next win, that i "
    "know, it sounds stupid, but i'm due to my probabilities within the "
    "market. i know that 10 times out of 10, when i execute on my "
    "strategy, i have higher odds of winning than losing, and i know "
    "that when i win, my winners are going to be larger than my "
    "losers, and that's all that i care about."
)

FARMERS_WORDS = (
    ("we", 0.0), ("walked", 0.5), ("through", 1.1), ("the", 1.7),
    ("farmers", 2.0), ("market", 2.6), ("and", 3.2), ("bought", 3.5),
    ("fresh", 4.0), ("vegetables", 4.4), ("from", 5.0), ("the", 5.4),
    ("stalls", 5.7), ("all", 6.2), ("morning", 6.6),
)

FARMERS_CONTEXT = (
    "we walked through the farmers market and bought fresh "
    "vegetables from the stalls all morning"
)


def test_market_indicated_sense_reads_the_context():
    assert broll.indicated_sense("market", TJR_CONTEXT) == "financial"
    assert broll.indicated_sense("market", FARMERS_CONTEXT) == "street"
    # No markers at all -> no signal -> nothing may be vetoed or skipped.
    assert broll.indicated_sense("market", "the market was busy today") is None
    # One marker each way is a tie, not a signal.
    assert broll.indicated_sense("market", "market profit stall") is None


def test_market_search_query_carries_sense_context():
    # The first spoken financial markers ride along with the noun.
    assert broll.broll_search_query("market", TJR_CONTEXT) == "market trade profit"
    assert broll.broll_search_query("market", FARMERS_CONTEXT) == "market farmers vegetables"
    # No clear signal -> the bare noun, exactly as before.
    assert broll.broll_search_query("market", "the market was busy") == "market"


def test_market_footage_mismatch_names_the_sense_conflict():
    video = {"title": "busy street market with farmers stalls", "tags": []}
    reason = broll.footage_mismatch(video, "market", TJR_CONTEXT)
    assert reason is not None
    assert "financial" in reason and "street" in reason
    # Same hit, farmers-market speech: no conflict.
    assert broll.footage_mismatch(video, "market", FARMERS_CONTEXT) is None
    # A trading-desk hit fits trading speech.
    trading = {"title": "stock market charts on trading monitors", "tags": []}
    assert broll.footage_mismatch(trading, "market", TJR_CONTEXT) is None


@pytest.mark.usefixtures("v2_cadence")
def test_tjr_market_plans_finance_sensed_search():
    # The evidence case, end to end at planner level: the real clip's
    # only trigger noun plans a cue anchored on the spoken word, and
    # the stamped stock search carries the financial sense — never the
    # bare noun that retrieved street markets.
    profile = load_profile("travel_vlog")
    words = _words_with_times(*TJR_WORDS)
    queries = _create_broll_queries(profile, words, 60.0)
    market = next(q for q in queries if q["query"] == "market")
    assert market["spoken_at"] == 13.54

    clip = {"id": "tjr", "start": 0, "end": 28.6, "duration": 28.6}
    edl = build_edl(clip, words, load_profile("podcast_news"), [])
    entry = next(e for e in edl["broll"] if e["query"] == "market")
    assert entry["start"] == 13.54
    assert entry["search_query"] != "market"
    assert entry["search_query"].startswith("market ")
    extras = entry["search_query"].split()[1:]
    assert extras and all(
        extra in broll.WORD_SENSES["market"]["markers"]["financial"]
        for extra in extras
    )


@pytest.mark.usefixtures("v2_cadence")
def test_farmers_market_plans_the_street_sense():
    # The same spelling in a farmers-market sentence still plans its
    # cue, and the search carries the street sense, not the financial
    # one.
    profile = load_profile("travel_vlog")
    words = _words_with_times(*FARMERS_WORDS)
    queries = _create_broll_queries(profile, words, 60.0)
    market = next(q for q in queries if q["query"] == "market")
    assert market["spoken_at"] == 2.6

    clip = {"id": "farmers", "start": 0, "end": 20, "duration": 20}
    edl = build_edl(clip, words, profile, [])
    entry = next(e for e in edl["broll"] if e["query"] == "market")
    assert entry["search_query"] == "market farmers vegetables"


STREET_MARKET_HIT = _hit(
    33, "market, street, farmers, market stalls, vegetables, crowd, shopping"
)
TRADING_DESK_HIT = _hit(
    44, "stock market, trading desk, chart, monitors, finance, business"
)


def test_gate_rejects_street_market_hit_for_trading_speech(api_key, tmp_path):
    payload = {"total": 1, "totalHits": 1, "hits": [STREET_MARKET_HIT]}
    asset = pixabay_broll.ensure_pixabay_asset(
        "market", cache_dir=tmp_path,
        client=_mock_client(payload), context=TJR_CONTEXT,
    )
    assert asset is None
    assert list(tmp_path.iterdir()) == []  # nothing downloaded


def test_gate_picks_trading_hit_for_trading_speech(api_key, tmp_path):
    payload = {"total": 2, "totalHits": 2, "hits": [STREET_MARKET_HIT, TRADING_DESK_HIT]}
    asset = pixabay_broll.ensure_pixabay_asset(
        "market", cache_dir=tmp_path,
        client=_mock_client(payload), context=TJR_CONTEXT,
    )
    assert asset is not None
    assert asset["asset_id"] == 44
    assert Path(asset["local_path"]).is_file()


def test_gate_accepts_street_market_hit_for_farmers_speech(api_key, tmp_path):
    payload = {"total": 1, "totalHits": 1, "hits": [STREET_MARKET_HIT]}
    asset = pixabay_broll.ensure_pixabay_asset(
        "market", cache_dir=tmp_path,
        client=_mock_client(payload), context=FARMERS_CONTEXT,
    )
    assert asset is not None
    assert asset["asset_id"] == 33


def test_gate_no_market_veto_without_a_sense_signal(api_key, tmp_path):
    # Speech that gives no sense signal must not start vetoing hits.
    payload = {"total": 1, "totalHits": 1, "hits": [STREET_MARKET_HIT]}
    asset = pixabay_broll.ensure_pixabay_asset(
        "market", cache_dir=tmp_path,
        client=_mock_client(payload), context="the market was busy today",
    )
    assert asset is not None
    assert asset["asset_id"] == 33
