"""Smart B-Roll v2, Slice 5: density + profiles + placement guards.

Design §5.1/§5.2. What this pins at the EDL level:

- the shipped per-profile density/coverage table (face-forward 1.5/min,
  vlog 2.0/min, travel/japan 2.0/min at 0.40 coverage, finance_business
  B-roll ON at 1.5/min — trading content no longer needs a
  misclassification to get inserts);
- counts and first-cue >= 5 s on the Japan + TJR fixtures, face-return
  gaps >= 4 s, payoff/conclusion exclusion, no cue opening on the
  clip's final line, and concept-dependent cue lengths (2.5–4.0 s).

The Japan fixture is constructed from the real Japan video's own
vocabulary (avocado, restaurant, city, street, fish, sandwich, sushi,
temple, sunset — the words in its transcript cache); the TJR fixture
is the real golden transcript from test_broll_word_sense.py.
"""

from __future__ import annotations


import pytest
import copy

from src.auto_director import plan_service
from src.auto_director.clip_plan import validate_plan_dict
from src.auto_director.edl import _plan_broll_segments, build_edl, load_profile
from src.auto_director.plan_service import PLAN_SYSTEM_PROMPT

from test_broll_word_sense import TJR_WORDS


@pytest.fixture
def v2_cadence(monkeypatch):
    """Smart B-Roll v2 cadence + keyword cue source (``BROLL_CADENCE=legacy``).

    Tests using it pin v2's exact cues and counts on real profiles. v2 stays
    selectable through that switch, so these pins remain meaningful. Since
    2026-11-17 the default is v3 (``cadence_per_min`` + lexicon cues), whose
    guards, sense handling and counts are covered in test_broll_v3_cadence.py.
    """
    monkeypatch.setenv("BROLL_CADENCE", "legacy")


def _words(pairs):
    return [
        {"word": word, "start": float(start), "end": float(start) + 0.4}
        for word, start in pairs
    ]


# Japan (MrBeast "$1 vs $10,000 Restaurants in Japan"), 60 s clip.
# Trigger nouns in common_nouns order: city, street, restaurant —
# the 2.0/min cap takes the first two, so the planned cues are the
# "city" sentence and the "street" sentence.
JAPAN_WORDS = _words(
    [
        ("That", 0.0),
        ("avocado", 2.0),
        ("is", 2.5),
        ("pretty", 3.0),
        ("good", 3.5),
        ("restaurant", 8.0),
        ("in", 8.5),
        ("the", 9.0),
        ("city", 9.5),
        ("we", 15.0),
        ("walk", 15.5),
        ("down", 16.0),
        ("the", 16.5),
        ("street", 17.0),
        ("try", 22.0),
        ("the", 22.5),
        ("fish", 23.0),
        ("sandwich", 30.0),
        ("for", 30.5),
        ("four", 31.0),
        ("hundred", 31.5),
        ("dollars", 32.0),
        ("this", 40.0),
        ("sushi", 40.5),
        ("is", 41.0),
        ("amazing", 41.5),
        ("temple", 50.0),
        ("at", 50.5),
        ("sunset", 51.0),
        ("goodbye", 58.0),
    ]
)

TJR_WORD_TIMINGS = _words(TJR_WORDS)

PODCAST = load_profile("podcast_news")
TRAVEL = load_profile("travel_vlog")
FINANCE = load_profile("finance_business")


# ---------------------------------------------------------------------------
# The shipped per-profile table (design §5.1)
# ---------------------------------------------------------------------------


def test_profile_density_table_as_shipped():
    expected = {
        # Face-forward profiles: 0.5 -> 1.5/min, coverage stays 0.30.
        "podcast_news": (True, 1.5, None),
        "hindi_podcast": (True, 1.5, None),
        "true_crime": (True, 1.5, None),
        "education_tutorial": (True, 1.5, None),
        # Vlog: 1.0 -> 2.0/min.
        "vlog_lifestyle": (True, 2.0, None),
        # Travel/japan: stay 2.0/min, coverage raised to 0.40.
        "travel_vlog": (True, 2.0, 0.4),
        "japan_travel_cinematic": (True, 2.0, 0.4),
        # Finance: B-roll ON at 1.5/min (was off entirely).
        "finance_business": (True, 1.5, None),
        # Still off: no B-roll at all.
        "meme_comedy": (False, 0.0, None),
        "gaming": (False, 0.0, None),
        "sports": (False, 0.0, None),
        "football_edit": (False, 0.0, None),
        "anime_funk_edit": (False, 0.0, None),
        "music_artist": (False, 0.0, None),
    }
    assert set(expected) == {
        "podcast_news",
        "hindi_podcast",
        "true_crime",
        "education_tutorial",
        "vlog_lifestyle",
        "travel_vlog",
        "japan_travel_cinematic",
        "finance_business",
        "meme_comedy",
        "gaming",
        "sports",
        "football_edit",
        "anime_funk_edit",
        "music_artist",
    }
    for niche, (enabled, density, max_coverage) in expected.items():
        broll = load_profile(niche)["broll"]
        assert broll["enabled"] is enabled, niche
        assert broll["density_per_min"] == density, niche
        assert broll.get("max_coverage") == max_coverage, niche
        if enabled:
            # "stock" = the v2 resolver route; the value was the legacy
            # "pexels" label until the B-roll consolidation renamed it.
            # Density and coverage above are the behaviour pins and are
            # unchanged by the rename.
            assert broll["source"] == "stock", niche


# ---------------------------------------------------------------------------
# Fixtures: Japan + TJR (counts, first cue, gaps, coverage)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("v2_cadence")
def test_finance_tjr_keyword_cue():
    # The Slice 5 point: TJR's trading clip gets its "market" cue
    # under its CORRECT finance profile now — no misclassification
    # needed. One cue (28.6 s at 1.5/min rounds to 1), word-anchored.
    clip = {"id": "tjr", "start": 0, "end": 28.6, "duration": 28.6}
    edl = build_edl(clip, TJR_WORD_TIMINGS, FINANCE, [])
    assert len(edl["broll"]) == 1
    entry = edl["broll"][0]
    assert entry["query"] == "market"
    assert entry["start"] == 13.54
    assert entry["end"] == 16.54
    assert entry["start"] >= 5.0  # first cue after the hook lands


@pytest.mark.usefixtures("v2_cadence")
def test_japan_travel_counts_and_guards():
    # 60 s at 2.0/min -> exactly 2 cues; first cue after 5 s; >= 4 s
    # of speaker between cues; coverage inside the 0.40 travel cap.
    clip = {"id": "japan", "start": 0, "end": 60, "duration": 60}
    edl = build_edl(clip, JAPAN_WORDS, TRAVEL, [])
    cues = edl["broll"]
    assert [c["start"] for c in cues] == [9.5, 17.0]
    assert [c["query"] for c in cues] == ["city", "street"]
    assert cues[0]["start"] >= 5.0
    assert cues[1]["start"] - cues[0]["end"] >= 4.0
    covered = sum(c["end"] - c["start"] for c in cues)
    assert covered <= 0.40 * 60
    # No cue opens on the final line ("goodbye" at 58 s).
    assert all(c["start"] < 58.0 for c in cues)


# ---------------------------------------------------------------------------
# Placement guards (design §5.2)
# ---------------------------------------------------------------------------


def _concept(anchor, *, duration_sec=None):
    concept = {
        "anchor_sec": anchor,
        "spoken": "a spoken clause",
        "concept": "a lighthouse beam sweeping the harbour",
        "search_queries": ["lighthouse beam night", "harbour lights dusk"],
        "sense": None,
        "media": "either",
        "avoid": [],
    }
    if duration_sec is not None:
        concept["duration_sec"] = duration_sec
    return concept


@pytest.mark.usefixtures("v2_cadence")
def test_keyword_face_return_gap():
    # Two keyword triggers 4 s apart: the second anchor is 1 s after
    # the first cue ends, so the >= 4 s face-return rule blocks it
    # there and the segment-boundary fallback moves it to 30 s.
    words = _words([("mountain", 10.0), ("beach", 14.0), ("goodbye", 55.0)])
    placements = _plan_broll_segments(
        PODCAST,
        words,
        60.0,
        [{"start": 0.0, "end": 30.0}, {"start": 30.0, "end": 60.0}],
        [],
        [],
    )
    assert [p["start"] for p in placements] == [10.0, 30.0]
    assert placements[1]["start"] - placements[0]["end"] >= 4.0


@pytest.mark.usefixtures("v2_cadence")
def test_first_cue_prefers_late_boundary():
    # The only trigger is spoken at 6.0 s, on top of a punch-in. The
    # segment-boundary fallback would try the free 2.0 s boundary
    # first — but the first cue belongs after the hook, so the >= 5 s
    # boundaries are tried first and the cue lands at 8.0 s instead.
    words = _words([("mountain", 6.0), ("beach", 25.0)])
    placements = _plan_broll_segments(
        TRAVEL,
        words,
        30.0,
        [
            {"start": 0.0, "end": 2.0},
            {"start": 2.0, "end": 8.0},
            {"start": 8.0, "end": 16.0},
            {"start": 16.0, "end": 30.0},
        ],
        [{"type": "punch_in", "time": 6.0, "duration": 0.5}],
        [],
    )
    assert [p["start"] for p in placements] == [8.0]


def test_no_cue_over_payoff_or_conclusion():
    # Phases from the plan: payoff 36–51 s, conclusion 51–60 s. The
    # 10 s concept lands; the 34 s concept's window RUNS INTO the
    # payoff (34–37 s) and is dropped even though its anchor sits
    # before it; the 40 s concept is anchored inside the payoff.
    phases = [
        {"start_offset": 0.0, "end_offset": 36.0, "intent": "context"},
        {"start_offset": 36.0, "end_offset": 51.0, "intent": "payoff"},
        {"start_offset": 51.0, "end_offset": 60.0, "intent": "conclusion"},
    ]
    words = _words(
        [("alpha", 10.0), ("bravo", 34.0), ("charlie", 40.0), ("goodbye", 55.0)]
    )
    placements = _plan_broll_segments(
        TRAVEL,
        words,
        60.0,
        [{"start": 0.0, "end": 60.0}],
        [],
        [],
        concepts=[_concept(10.0), _concept(34.0), _concept(40.0)],
        phases=phases,
    )
    assert [p["start"] for p in placements] == [10.0]


@pytest.mark.usefixtures("v2_cadence")
def test_no_cue_starts_on_final_line():
    # "beach" IS the last spoken word (50.0 s). A cue may not open on
    # the final line, so its anchor is refused and the fallback moves
    # it to the 30.0 s boundary instead of onto the closing line.
    words = _words([("mountain", 10.0), ("beach", 50.0)])
    placements = _plan_broll_segments(
        TRAVEL,
        words,
        60.0,
        [{"start": 0.0, "end": 30.0}, {"start": 30.0, "end": 60.0}],
        [],
        [],
    )
    assert [p["start"] for p in placements] == [10.0, 30.0]
    assert all(p["start"] < 50.0 for p in placements)


@pytest.mark.usefixtures("v2_cadence")
def test_no_cue_starts_inside_face_emphasis_window():
    # The punch-in zoom keyframe IS the face shot's emphasis window
    # stamped on the EDL: a cue may not start inside it. The blocked
    # keyword anchor falls back to the next free boundary instead.
    words = _words([("mountain", 6.0), ("goodbye", 55.0)])
    placements = _plan_broll_segments(
        TRAVEL,
        words,
        60.0,
        [{"start": 0.0, "end": 30.0}, {"start": 30.0, "end": 60.0}],
        [{"type": "punch_in", "time": 6.0, "duration": 0.5}],
        [],
    )
    assert [p["start"] for p in placements] == [30.0]
    assert all(not (5.75 <= p["start"] < 6.75) for p in placements)


# ---------------------------------------------------------------------------
# Coverage: the per-profile cap (design §5.1)
# ---------------------------------------------------------------------------


def _coverage_words():
    nouns = [
        ("city", 5.0),
        ("mountain", 12.0),
        ("beach", 19.0),
        ("office", 26.0),
        ("computer", 33.0),
        ("phone", 40.0),
        ("car", 47.0),
        ("building", 54.0),
        ("street", 61.0),
        ("park", 68.0),
        ("restaurant", 75.0),
        ("hotel", 82.0),
        ("chart", 89.0),
        ("graph", 96.0),
        ("goodbye", 115.0),
    ]
    return _words(nouns)


@pytest.mark.usefixtures("v2_cadence")
def test_travel_coverage_cap_040():
    # Fourteen trigger nouns, 7 s apart (so the 4 s face-return gap
    # never binds), on a 120 s clip at a synthetic 10/min density so
    # the coverage cap — not the density — is what binds. At the 0.30
    # default only 12 cues fit (36.0 s); with the travel profile's
    # 0.40 cap all 14 fit (42.0 s). Nothing may exceed 0.40. (The
    # default case uses a podcast-profile copy: travel itself now
    # carries max_coverage 0.4, so a travel copy would not test the
    # default at all.)
    words = _coverage_words()
    segments = [{"start": 0.0, "end": 120.0}]

    dense = copy.deepcopy(PODCAST)
    dense["broll"]["density_per_min"] = 10.0
    default = _plan_broll_segments(dense, words, 120.0, segments, [], [])
    assert len(default) == 12
    assert sum(p["end"] - p["start"] for p in default) == 36.0

    raised = copy.deepcopy(TRAVEL)
    raised["broll"]["density_per_min"] = 10.0
    raised["broll"]["max_coverage"] = 0.4
    placed = _plan_broll_segments(raised, words, 120.0, segments, [], [])
    assert len(placed) == 14
    assert sum(p["end"] - p["start"] for p in placed) == 42.0
    assert sum(p["end"] - p["start"] for p in placed) <= 0.40 * 120


# ---------------------------------------------------------------------------
# Concept-dependent cue length (design §5.1): 2.5–4.0 s, else 3.0 s
# ---------------------------------------------------------------------------


def test_concept_duration_hint_sets_cue_length():
    words = _words(
        [
            ("alpha", 10.0),
            ("bravo", 20.0),
            ("charlie", 30.0),
            ("delta", 40.0),
            ("goodbye", 110.0),
        ]
    )
    concepts = [
        _concept(10.0, duration_sec=4.0),
        _concept(20.0, duration_sec=2.5),
        _concept(30.0, duration_sec=9.0),  # clamps to the 4.0 s band top
        _concept(40.0),  # no hint: the fixed 3.0 s
    ]
    placements = _plan_broll_segments(
        TRAVEL,
        words,
        120.0,
        [{"start": 0.0, "end": 120.0}],
        [],
        [],
        concepts=concepts,
    )
    assert [p["start"] for p in placements] == [10.0, 20.0, 30.0, 40.0]
    assert [round(p["end"] - p["start"], 3) for p in placements] == [
        4.0,
        2.5,
        4.0,
        3.0,
    ]


def test_concept_duration_validation_clamps():
    def _plan_with(duration_sec):
        concept = _concept(10.0)
        if duration_sec != "absent":
            concept["duration_sec"] = duration_sec
        raw = {
            "hook": {
                "type": "strong_statement",
                "timestamp": 0.0,
                "confidence": 0.7,
                "reason": "The speaker opens with the number.",
            },
            "broll_concepts": [concept],
            "phases": [
                {"start_offset": 0.0, "end_offset": 60.0, "intent": "context"}
            ],
        }
        return validate_plan_dict(
            raw, niche="travel_vlog", duration=60.0, profile=TRAVEL
        )

    assert _plan_with(4.0).broll_concepts[0].duration_sec == 4.0
    assert _plan_with(9.0).broll_concepts[0].duration_sec == 4.0
    assert _plan_with(2.0).broll_concepts[0].duration_sec == 2.5
    assert _plan_with("soon").broll_concepts[0].duration_sec is None
    assert _plan_with("absent").broll_concepts[0].duration_sec is None


def test_system_prompt_carries_duration_rule():
    # The planner can only hint a length the prompt tells it about.
    assert "duration_sec" in PLAN_SYSTEM_PROMPT
    assert "2.5-4.0" in PLAN_SYSTEM_PROMPT
