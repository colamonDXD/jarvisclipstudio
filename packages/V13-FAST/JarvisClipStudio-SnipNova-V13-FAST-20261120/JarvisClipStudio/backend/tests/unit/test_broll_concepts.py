"""Smart B-Roll v2, Slice 2: planner-written B-roll concepts.

The core rule (design §2.1): the brain writes the visual concept BEFORE
anything searches — no bare transcript word ever reaches a stock
provider again. These tests pin the three layers:

- validation (clip_plan): a concept is a filmable sentence + real
  search PHRASES (a bare word is mechanically not a phrase) + the
  resolved sense when the moment turns on a sense-trap word + an avoid
  list, capped by the profile's B-roll density, never anchored on the
  payoff/conclusion;
- generation (plan_service): the concepts ride the EXISTING plan call
  as one more structured field — no new call, no new provider surface;
- consumption (edl + video_service seam): when a validated plan
  provides concepts they become the cue source (concept phrases are
  searched, the concept's sense wins over the WORD_SENSES table), and
  when it does not, the EDL is byte-identical to today's keyword plan.

The golden fixture is the real TJR clip (TJR_WORDS in
test_broll_word_sense.py): "market" at 13.54s in trading speech — the
cue that shipped street-market footage on 2026-11-12.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.auto_director import plan_service
from src.auto_director.clip_plan import (
    CLIP_PLAN_JSON_SCHEMA,
    ClipPlan,
    apply_plan_broll_concepts,
    minimal_plan,
    validate_plan_dict,
)
from src.auto_director.edl import _plan_broll_segments, build_edl, load_profile
from src.media import pixabay_broll
from src.auto_director.plan_service import (
    PLAN_SYSTEM_PROMPT,
    ClipPlanDraft,
    _build_plan_prompt,
    _draft_to_plan_dict,
    generate_clip_plan,
)

from test_broll_word_sense import FARMERS_WORDS, TJR_WORDS


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


TJR_WORD_TIMINGS = _words(TJR_WORDS)
FARMERS_WORD_TIMINGS = _words(FARMERS_WORDS)

PODCAST = load_profile("podcast_news")  # B-roll on, 1.5/min (Slice 5)
TRAVEL = load_profile("travel_vlog")  # B-roll on, 2.0/min
FINANCE = load_profile("finance_business")  # B-roll on at 1.5/min (Slice 5)
MEME = load_profile("meme_comedy")  # B-roll off

TJR_PHASES = [
    {"start_offset": 0.0, "end_offset": 3.0, "intent": "hook"},
    {"start_offset": 3.0, "end_offset": 20.0, "intent": "context"},
    {"start_offset": 20.0, "end_offset": 28.6, "intent": "build"},
]

# What the planner should write for the TJR "market" moment: the
# concept sentence is filmable, every search phrase is a phrase, the
# sense is resolved to the financial market, and the avoid list names
# the street sense that shipped as footage on 2026-11-12.
TJR_CONCEPT = {
    "anchor_sec": 13.54,
    "spoken": "but I'm due to my probabilities within the market",
    "concept": "a trader's hands over a glowing stock chart on multiple monitors",
    "search_queries": [
        "stock market chart screen",
        "trading desk monitors",
        "candlestick graph close up",
    ],
    "sense": "market:financial",
    "media": "either",
    "avoid": ["street", "crowd", "farmers", "market stalls"],
}

FARMERS_CONCEPT = {
    "anchor_sec": 2.6,
    "spoken": "we walked through the farmers market",
    "concept": "shoppers browsing vegetable stalls at an outdoor farmers market",
    "search_queries": ["farmers market stalls", "fresh vegetables market"],
    "sense": "market:street",
    "media": "either",
    "avoid": ["trading desk", "stock chart", "office"],
}


def _raw_plan(concepts, *, phases=None, duration_phases=True):
    raw = {
        "hook": {
            "type": "strong_statement",
            "timestamp": 0.0,
            "confidence": 0.7,
            "reason": "The speaker opens with the number.",
        },
        "broll_concepts": concepts,
    }
    if phases is not None:
        raw["phases"] = phases
    return raw


def _plain_concept(anchor, *, spoken="the chef tastes the soup"):
    return {
        "anchor_sec": anchor,
        "spoken": spoken,
        "concept": "a chef tasting soup in a busy kitchen",
        "search_queries": ["chef tasting soup", "kitchen cooking close up"],
        "sense": None,
        "media": "video",
        "avoid": [],
    }


# ---------------------------------------------------------------------------
# Validation (clip_plan.validate_plan_dict)
# ---------------------------------------------------------------------------


def test_valid_concept_survives_validation():
    plan = validate_plan_dict(
        _raw_plan([TJR_CONCEPT], phases=TJR_PHASES),
        niche="podcast_news",
        duration=28.6,
        profile=PODCAST,
    )
    assert plan.source == "llm"
    assert len(plan.broll_concepts) == 1
    concept = plan.broll_concepts[0]
    assert concept.anchor_sec == 13.54
    assert concept.concept.startswith("a trader's hands")
    assert concept.search_queries == TJR_CONCEPT["search_queries"]
    assert concept.sense == "market:financial"
    assert concept.media == "either"
    assert concept.avoid == ["street", "crowd", "farmers", "market stalls"]


def test_trap_word_concept_without_resolved_sense_is_dropped():
    # The same concept with the sense tag missing — or naming a
    # different trap word — is the bare-word failure in a new coat:
    # validation drops it rather than letting it search untagged.
    for sense in (None, "", "bank:finance", "not-a-tag"):
        raw = _raw_plan([dict(TJR_CONCEPT, sense=sense)], phases=TJR_PHASES)
        plan = validate_plan_dict(
            raw, niche="podcast_news", duration=28.6, profile=PODCAST
        )
        assert plan.broll_concepts == [], f"sense={sense!r} must not survive"


def test_bare_word_is_not_a_search_phrase():
    # A single-token "phrase" is dropped mechanically; a concept left
    # with no real phrases is dropped with it.
    raw = _raw_plan(
        [dict(TJR_CONCEPT, search_queries=["market"])], phases=TJR_PHASES
    )
    plan = validate_plan_dict(
        raw, niche="podcast_news", duration=28.6, profile=PODCAST
    )
    assert plan.broll_concepts == []

    raw = _raw_plan(
        [dict(TJR_CONCEPT, search_queries=["market", "stock market chart"])],
        phases=TJR_PHASES,
    )
    plan = validate_plan_dict(
        raw, niche="podcast_news", duration=28.6, profile=PODCAST
    )
    assert len(plan.broll_concepts) == 1
    assert plan.broll_concepts[0].search_queries == ["stock market chart"]


@pytest.mark.usefixtures("v2_cadence")
def test_concept_cap_follows_profile_density():
    # travel_vlog allows 2.0/min: a 60s clip caps at 2 concepts, and
    # the earliest anchors survive (mirrors the EDL planner's target).
    concepts = [_plain_concept(a) for a in (5.0, 15.0, 25.0, 35.0)]
    phases = [{"start_offset": 0.0, "end_offset": 60.0, "intent": "context"}]
    plan = validate_plan_dict(
        _raw_plan(concepts, phases=phases),
        niche="travel_vlog",
        duration=60.0,
        profile=TRAVEL,
    )
    assert [c.anchor_sec for c in plan.broll_concepts] == [5.0, 15.0]


def test_broll_off_profile_drops_all_concepts():
    # Slice 5 enabled B-roll for finance_business, so the off-profile
    # case is now meme_comedy (still off): the test's intent is
    # unchanged — a profile with B-roll off drops every concept.
    plan = validate_plan_dict(
        _raw_plan([TJR_CONCEPT], phases=TJR_PHASES),
        niche="meme_comedy",
        duration=28.6,
        profile=MEME,
    )
    assert plan.broll_concepts == []


def test_concept_anchored_on_payoff_or_conclusion_is_dropped():
    phases = [
        {"start_offset": 0.0, "end_offset": 20.0, "intent": "context"},
        {"start_offset": 20.0, "end_offset": 40.0, "intent": "payoff"},
        {"start_offset": 40.0, "end_offset": 60.0, "intent": "conclusion"},
    ]
    payoff = _plain_concept(25.0, spoken="and that is the final result")
    payoff["concept"] = "the winner celebrating on a stage"
    payoff["search_queries"] = ["winner celebrating stage", "trophy lift crowd"]
    kept = _plain_concept(10.0)
    plan = validate_plan_dict(
        _raw_plan([payoff, kept], phases=phases),
        niche="travel_vlog",
        duration=60.0,
        profile=TRAVEL,
    )
    assert [c.anchor_sec for c in plan.broll_concepts] == [10.0]


def test_malformed_concepts_drop_individually():
    raw = _raw_plan(
        [
            "garbage",
            {"concept": ""},
            {"concept": "a lighthouse at dusk"},  # no search phrases
            {"anchor_sec": "soon", "concept": "a lighthouse", "search_queries": "nope"},
            TJR_CONCEPT,
        ],
        phases=TJR_PHASES,
    )
    plan = validate_plan_dict(
        raw, niche="podcast_news", duration=28.6, profile=PODCAST
    )
    # The plan itself is unharmed; only the one well-formed concept
    # survives.
    assert plan.source == "llm"
    assert len(plan.broll_concepts) == 1
    assert plan.broll_concepts[0].sense == "market:financial"


def test_media_and_avoid_are_sanitised():
    raw = _raw_plan(
        [dict(TJR_CONCEPT, media="hologram", avoid=[" street ", "", "street", 7])],
        phases=TJR_PHASES,
    )
    plan = validate_plan_dict(
        raw, niche="podcast_news", duration=28.6, profile=PODCAST
    )
    concept = plan.broll_concepts[0]
    assert concept.media == "either"
    assert concept.avoid == ["street"]


def _room_concept(concept, avoid, *, spoken="we filmed it indoors", anchor=6.0):
    return {
        "anchor_sec": anchor,
        "spoken": spoken,
        "concept": concept,
        "search_queries": ["indoor scene footage", "room interior video"],
        "sense": None,
        "media": "video",
        "avoid": avoid,
    }


def test_recording_studio_avoid_completed_with_competing_furniture():
    # The calibration expansion's one missed insert was an apartment
    # passing under a recording-studio cue whose avoid side barely
    # registered. Validation now completes a room concept's avoid list
    # with the competing rooms' signature furniture, planner items
    # first and in order.
    raw = _raw_plan(
        [
            _room_concept(
                "a singer at a microphone in a recording studio", ["crowd"]
            )
        ],
        phases=TJR_PHASES,
    )
    plan = validate_plan_dict(
        raw, niche="podcast_news", duration=28.6, profile=PODCAST
    )
    assert len(plan.broll_concepts) == 1
    assert plan.broll_concepts[0].avoid == [
        "crowd",
        "bed",
        "sofa",
        "kitchen counter",
        "stove",
    ]


def test_apartment_avoid_completed_with_studio_gear():
    raw = _raw_plan(
        [_room_concept("the inside of a small apartment by the window", [])],
        phases=TJR_PHASES,
    )
    plan = validate_plan_dict(
        raw, niche="podcast_news", duration=28.6, profile=PODCAST
    )
    assert plan.broll_concepts[0].avoid == [
        "mixing desk",
        "monitor speakers",
        "acoustic panels",
        "studio microphone",
    ]


def test_kitchen_avoid_completed_and_non_room_avoid_untouched():
    raw = _raw_plan(
        [
            _room_concept(
                "a chef tasting soup in a busy kitchen",
                [],
                spoken="the chef tastes the soup",
            ),
            _room_concept(
                "boats tied up along a small harbour",
                ["cargo crane"],
                spoken="we sailed into the harbour",
                anchor=12.0,
            ),
        ],
        phases=TJR_PHASES,
    )
    plan = validate_plan_dict(
        raw, niche="travel_vlog", duration=90.0, profile=TRAVEL
    )
    by_concept = {c.concept: c for c in plan.broll_concepts}
    assert by_concept["a chef tasting soup in a busy kitchen"].avoid == [
        "bed",
        "mixing desk",
        "acoustic panels",
    ]
    # No room named -> the planner's list is byte-identical.
    assert by_concept["boats tied up along a small harbour"].avoid == [
        "cargo crane"
    ]


def test_room_completion_never_evicts_and_respects_the_cap():
    planner_avoid = ["crowd", "street", "traffic", "city", "noise"]
    raw = _raw_plan(
        [
            _room_concept(
                "a singer at a microphone in a recording studio", planner_avoid
            )
        ],
        phases=TJR_PHASES,
    )
    plan = validate_plan_dict(
        raw, niche="podcast_news", duration=28.6, profile=PODCAST
    )
    avoid = plan.broll_concepts[0].avoid
    assert avoid[:5] == planner_avoid
    assert len(avoid) == 6
    assert avoid[5] == "bed"


def test_room_completion_dedupes_case_insensitively():
    raw = _raw_plan(
        [
            _room_concept(
                "a singer at a microphone in a recording studio", ["Bed", "SOFA"]
            )
        ],
        phases=TJR_PHASES,
    )
    plan = validate_plan_dict(
        raw, niche="podcast_news", duration=28.6, profile=PODCAST
    )
    assert plan.broll_concepts[0].avoid == [
        "Bed",
        "SOFA",
        "kitchen counter",
        "stove",
    ]


def test_plan_without_concepts_validates_empty():
    plan = validate_plan_dict(
        _raw_plan([], phases=TJR_PHASES),
        niche="podcast_news",
        duration=28.6,
        profile=PODCAST,
    )
    assert plan.broll_concepts == []
    # A plan dict with the key absent entirely behaves the same.
    raw = _raw_plan([], phases=TJR_PHASES)
    del raw["broll_concepts"]
    plan = validate_plan_dict(
        raw, niche="podcast_news", duration=28.6, profile=PODCAST
    )
    assert plan.broll_concepts == []


def test_minimal_plan_has_no_concepts():
    plan = minimal_plan("podcast_news", PODCAST, 28.6)
    assert plan.source == "fallback"
    assert plan.broll_concepts == []


def test_concepts_roundtrip_through_plan_dict():
    plan = validate_plan_dict(
        _raw_plan([TJR_CONCEPT], phases=TJR_PHASES),
        niche="podcast_news",
        duration=28.6,
        profile=PODCAST,
    )
    stored = plan.to_dict()
    assert stored["broll_concepts"][0]["sense"] == "market:financial"
    rebuilt = ClipPlan.from_dict(stored)
    assert len(rebuilt.broll_concepts) == 1
    concept = rebuilt.broll_concepts[0]
    assert concept.anchor_sec == 13.54
    assert concept.search_queries == TJR_CONCEPT["search_queries"]
    assert concept.avoid == TJR_CONCEPT["avoid"]


def test_schema_documents_broll_concepts():
    props = CLIP_PLAN_JSON_SCHEMA["properties"]
    assert "broll_concepts" in props
    item_props = props["broll_concepts"]["items"]["properties"]
    for key in ("anchor_sec", "spoken", "concept", "search_queries", "sense", "media", "avoid", "duration_sec"):
        assert key in item_props


# ---------------------------------------------------------------------------
# Generation (plan_service): concepts ride the existing plan call
# ---------------------------------------------------------------------------


def _tjr_draft(**overrides):
    values = {
        "hook_type": "strong_statement",
        "hook_timestamp": 0.0,
        "hook_confidence": 0.8,
        "hook_reason": "He opens with the $96,000 number.",
        "phases": [dict(p) for p in TJR_PHASES],
        "broll_concepts": [dict(TJR_CONCEPT)],
        "use_profile_music": True,
        "music_reason": "Profile bed fits a trading monologue.",
        "plan_confidence": 0.8,
    }
    values.update(overrides)
    return ClipPlanDraft(**values)


def test_draft_to_plan_dict_maps_concepts():
    raw = _draft_to_plan_dict(_tjr_draft())
    assert raw["broll_concepts"] == [
        {
            "anchor_sec": 13.54,
            "spoken": TJR_CONCEPT["spoken"],
            "concept": TJR_CONCEPT["concept"],
            "search_queries": TJR_CONCEPT["search_queries"],
            "sense": "market:financial",
            "media": "either",
            "avoid": TJR_CONCEPT["avoid"],
            "duration_sec": None,
        }
    ]


@pytest.mark.usefixtures("v2_cadence")
def test_prompt_states_broll_state_and_cap():
    prompt = _build_plan_prompt(
        niche="podcast_news",
        profile=PODCAST,
        duration=28.6,
        text="trading talk",
        words=TJR_WORD_TIMINGS,
        audio_features=None,
        selection_scores=None,
    )
    # podcast_news: 1.5/min over 28.6s -> exactly one concept allowed,
    # and the prompt says the same number validation enforces.
    assert "Stock B-roll: ENABLED" in prompt
    assert "up to 1 broll_concepts" in prompt

    prompt = _build_plan_prompt(
        niche="finance_business",
        profile=FINANCE,
        duration=28.6,
        text="trading talk",
        words=TJR_WORD_TIMINGS,
        audio_features=None,
        selection_scores=None,
    )
    # Slice 5 enabled B-roll for finance at 1.5/min: the planner is
    # told it may write concepts for trading speech (up to 1 here).
    assert "Stock B-roll: ENABLED" in prompt
    assert "up to 1 broll_concepts" in prompt


def test_system_prompt_carries_the_concept_rules():
    # The filmability rule, the no-bare-word rule and the sense-trap
    # table are the planner's half of the core rule; pin their presence
    # so a prompt edit cannot silently drop them.
    assert "STOCK B-ROLL CONCEPTS" in PLAN_SYSTEM_PROMPT
    assert "something a camera can point at" in PLAN_SYSTEM_PROMPT
    assert "NEVER a bare single word" in PLAN_SYSTEM_PROMPT
    assert "SENSE-TRAP" in PLAN_SYSTEM_PROMPT
    assert "market (financial/street)" in PLAN_SYSTEM_PROMPT
    assert "farmers, stalls" in PLAN_SYSTEM_PROMPT


def test_system_prompt_carries_subject_and_room_guidance():
    # The calibration expansion's diagnosis, pinned like the concept
    # rules above: 4 of its 5 failures were concept PHRASING (scenery
    # checklists vetoed against correct-sense footage) and the one
    # miss was a room-vs-room pair with a thin avoid side. The prompt
    # must keep asking for a defining subject and for competing-room
    # signature furniture in room concepts' avoid lists.
    assert "DEFINING SUBJECT" in PLAN_SYSTEM_PROMPT
    assert "checklist of scenery" in PLAN_SYSTEM_PROMPT
    assert "a harbour with boats along a coast" in PLAN_SYSTEM_PROMPT
    assert "signature furniture" in PLAN_SYSTEM_PROMPT
    assert "mixing desk, monitor speakers, acoustic panels" in PLAN_SYSTEM_PROMPT
    assert "bed, sofa, kitchen counter" in PLAN_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_generate_clip_plan_carries_concepts(monkeypatch):
    agent = SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(output=_tjr_draft()))
    )
    monkeypatch.setattr(plan_service, "get_plan_agent", lambda: agent)
    plan = await generate_clip_plan(
        niche="podcast_news",
        profile=PODCAST,
        duration=28.6,
        text="trading talk",
        words=TJR_WORD_TIMINGS,
    )
    assert plan.source == "llm"
    assert len(plan.broll_concepts) == 1
    assert plan.broll_concepts[0].sense == "market:financial"


@pytest.mark.asyncio
async def test_generate_failure_yields_conceptless_fallback(monkeypatch):
    agent = SimpleNamespace(run=AsyncMock(side_effect=RuntimeError("LLM down")))
    monkeypatch.setattr(plan_service, "get_plan_agent", lambda: agent)
    plan = await generate_clip_plan(
        niche="podcast_news",
        profile=PODCAST,
        duration=28.6,
        text="trading talk",
        words=TJR_WORD_TIMINGS,
    )
    # The floor guarantee at generation level: any planner failure is
    # the deterministic plan, which carries no concepts — the pipeline
    # then renders exactly today's keyword-planned EDL.
    assert plan.source == "fallback"
    assert plan.broll_concepts == []


# ---------------------------------------------------------------------------
# Consumption: concepts become the cue source
# ---------------------------------------------------------------------------


def _tjr_plan(concepts=None):
    return validate_plan_dict(
        _raw_plan([TJR_CONCEPT] if concepts is None else concepts, phases=TJR_PHASES),
        niche="podcast_news",
        duration=28.6,
        profile=PODCAST,
    )


def _tjr_edl():
    clip = {"id": "tjr", "start": 0, "end": 28.6, "duration": 28.6}
    return build_edl(clip, TJR_WORD_TIMINGS, PODCAST, [])


@pytest.mark.usefixtures("v2_cadence")
def test_tjr_golden_concept_replaces_keyword_cue():
    edl = _tjr_edl()
    # Today's keyword plan is on the EDL first (the Slice-1 behaviour):
    # the market cue exists, searched with the table's sense string.
    keyword_entry = next(e for e in edl["broll"] if e["query"] == "market")
    assert keyword_entry["search_query"].startswith("market ")

    placed = apply_plan_broll_concepts(edl, _tjr_plan(), PODCAST, TJR_WORD_TIMINGS)
    assert placed == 1
    # Replaced, not appended: exactly the concept cue remains.
    assert len(edl["broll"]) == 1
    entry = edl["broll"][0]
    assert entry["start"] == 13.54
    assert entry["query"] == "market"  # provenance: the trigger noun
    # The concept's phrases are what gets searched — no bare "market",
    # and not the WORD_SENSES table's derived string either.
    assert entry["search_query"] == "stock market chart screen"
    assert entry["search_query"] != keyword_entry["search_query"]
    assert entry["search_queries"] == TJR_CONCEPT["search_queries"]
    assert all(q != "market" for q in entry["search_queries"])
    assert entry["sense"] == "market:financial"
    assert entry["concept"].startswith("a trader's hands")
    assert entry["media"] == "either"
    assert "street" in entry["avoid"] and "farmers" in entry["avoid"]


def test_farmers_market_concept_yields_the_opposite_sense():
    phases = [{"start_offset": 0.0, "end_offset": 20.0, "intent": "context"}]
    plan = validate_plan_dict(
        _raw_plan([FARMERS_CONCEPT], phases=phases),
        niche="podcast_news",
        duration=20.0,
        profile=PODCAST,
    )
    assert len(plan.broll_concepts) == 1
    clip = {"id": "farmers", "start": 0, "end": 20, "duration": 20}
    edl = build_edl(clip, FARMERS_WORD_TIMINGS, PODCAST, [])
    placed = apply_plan_broll_concepts(edl, plan, PODCAST, FARMERS_WORD_TIMINGS)
    assert placed == 1
    entry = edl["broll"][0]
    assert entry["start"] == 2.6
    assert entry["sense"] == "market:street"
    assert entry["search_query"] == "farmers market stalls"
    assert "trading desk" in entry["avoid"]


@pytest.mark.usefixtures("v2_cadence")
def test_concept_wins_when_table_disagrees():
    # Precedence, both directions. On TJR's trading speech the
    # WORD_SENSES table resolves "market" to financial (Slice 1); a
    # concept that resolves it to street must still search ITS phrases
    # — the planner read the moment, the table only read markers.
    street_concept = {
        "anchor_sec": 13.54,
        "spoken": "but I'm due to my probabilities within the market",
        "concept": "shoppers weaving between stalls at a busy street market",
        "search_queries": ["street market crowd", "market stalls vegetables"],
        "sense": "market:street",
        "media": "either",
        "avoid": ["trading desk", "stock chart"],
    }
    edl = _tjr_edl()
    plan = _tjr_plan([street_concept])
    assert len(plan.broll_concepts) == 1  # validation keeps the tag
    placed = apply_plan_broll_concepts(edl, plan, PODCAST, TJR_WORD_TIMINGS)
    assert placed == 1
    entry = edl["broll"][0]
    assert entry["sense"] == "market:street"
    assert entry["search_query"] == "street market crowd"


def test_floor_edl_byte_identical_without_concepts():
    # No concepts on the plan -> the consumer does not apply and the
    # EDL keeps today's keyword plan exactly.
    edl = _tjr_edl()
    before = copy.deepcopy(edl["broll"])
    plan = _tjr_plan(concepts=[])
    assert apply_plan_broll_concepts(edl, plan, PODCAST, TJR_WORD_TIMINGS) is None
    assert edl["broll"] == before

    # A fallback plan is record-only even if concepts were hand-set on
    # it: it must never alter an EDL.
    from dataclasses import replace

    fallback = replace(_tjr_plan(), source="fallback")
    assert apply_plan_broll_concepts(edl, fallback, PODCAST, TJR_WORD_TIMINGS) is None
    assert edl["broll"] == before

    # No word timings at this call site (the re-render path) -> no
    # re-placement; the saved B-roll carry stands.
    assert apply_plan_broll_concepts(edl, _tjr_plan(), PODCAST, None) is None
    assert edl["broll"] == before


@pytest.mark.usefixtures("v2_cadence")
def test_keyword_planner_unchanged_when_concepts_param_absent():
    # _plan_broll_segments without the concepts argument is exactly
    # today's planner — the floor at the EDL layer itself.
    clip = {"id": "tjr", "start": 0, "end": 28.6, "duration": 28.6}
    edl = build_edl(clip, TJR_WORD_TIMINGS, PODCAST, [])
    direct = _plan_broll_segments(
        PODCAST,
        TJR_WORD_TIMINGS,
        28.6,
        edl["segments"],
        edl["zoom_keyframes"],
        edl["overlays"],
    )
    assert direct == edl["broll"]
    assert all("concept" not in entry for entry in direct)


@pytest.mark.usefixtures("v2_cadence")
def test_concept_anchor_snaps_to_nearest_spoken_word():
    # The planner's anchor (13.6) sits between words; the cue opens on
    # the nearest spoken word — "market." at 13.54 — not between words.
    plan = _tjr_plan([dict(TJR_CONCEPT, anchor_sec=13.6)])
    edl = _tjr_edl()
    assert apply_plan_broll_concepts(edl, plan, PODCAST, TJR_WORD_TIMINGS) == 1
    assert edl["broll"][0]["start"] == 13.54


def test_face_return_gap_between_concept_cues():
    # Two concepts 1s apart after the first cue's end: the second is
    # dropped by the >=4s face-return rule (not by overlap — it starts
    # after the first cue ends), and the third, far enough out, lands.
    words = _words([("alpha", 10.0), ("bravo", 14.0), ("charlie", 20.0)])

    def concept(anchor):
        return {
            "anchor_sec": anchor,
            "spoken": "a spoken clause",
            "concept": "a lighthouse beam sweeping the harbour",
            "search_queries": ["lighthouse beam night", "harbour lights dusk"],
            "sense": None,
            "media": "either",
            "avoid": [],
        }

    placements = _plan_broll_segments(
        TRAVEL,
        words,
        60.0,
        [{"start": 0.0, "end": 60.0}],
        [],
        [],
        concepts=[concept(10.0), concept(14.0), concept(20.0)],
    )
    assert [p["start"] for p in placements] == [10.0, 20.0]


def test_blocked_concept_is_dropped_not_moved():
    # A punch-in sits on the concept's anchor. A keyword cue would
    # fall back to the next segment boundary (30s); a concept
    # illustrates ONE clause, so a blocked concept is dropped instead
    # of landing over unrelated speech.
    words = _words([("alpha", 10.0), ("bravo", 30.0)])
    concept = {
        "anchor_sec": 10.0,
        "spoken": "a spoken clause",
        "concept": "a lighthouse beam sweeping the harbour",
        "search_queries": ["lighthouse beam night", "harbour lights dusk"],
        "sense": None,
        "media": "either",
        "avoid": [],
    }
    placements = _plan_broll_segments(
        TRAVEL,
        words,
        60.0,
        [{"start": 0.0, "end": 30.0}, {"start": 30.0, "end": 60.0}],
        [{"type": "punch_in", "time": 10.0, "duration": 0.5}],
        [],
        concepts=[concept],
    )
    assert placements == []


# ---------------------------------------------------------------------------
# Resolver: the concept's phrases reach the stock provider
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    pixabay_broll._search_cache.clear()
    yield
    pixabay_broll._search_cache.clear()


@pytest.fixture()
def stock_key(monkeypatch):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")


def _hit(asset_id, tags):
    return {
        "id": asset_id,
        "pageURL": "https://pixabay.com/videos/x",
        "tags": tags,
        "duration": 12,
        "videos": {
            "large": {
                "url": f"https://cdn.pixabay.com/{asset_id}.mp4",
                "width": 1920,
                "height": 1080,
                "size": 1000,
            },
        },
    }


STREET_HIT = _hit(33, "market, street, farmers, market stalls, vegetables, crowd")
TRADING_HIT = _hit(44, "stock market, trading desk, chart, monitors, finance")


@pytest.mark.usefixtures("v2_cadence")
def test_resolver_searches_concept_phrase_and_picks_trading_hit(stock_key, tmp_path):
    edl = _tjr_edl()
    assert apply_plan_broll_concepts(edl, _tjr_plan(), PODCAST, TJR_WORD_TIMINGS) == 1

    searched = []

    def handler(request):
        if "pixabay.com/api" in str(request.url):
            searched.append(request.url.params.get("q"))
            payload = {"total": 2, "totalHits": 2, "hits": [STREET_HIT, TRADING_HIT]}
            return httpx.Response(200, json=payload)
        return httpx.Response(200, content=b"fake-mp4-bytes")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolved = pixabay_broll.resolve_edl_broll(edl, cache_dir=tmp_path, client=client)

    # The provider was asked for the concept's phrase — never the bare
    # word, never the table's derived string.
    assert searched == ["stock market chart screen"]
    assert resolved == 1
    entry = edl["broll"][0]
    # The street hit is still rejected (the Slice-1 gate judging the
    # spoken context is the backstop); the trading hit wins.
    assert entry["asset_id"] == 44
    assert entry["provider"] == "pixabay"
    assert Path(entry["local_path"]).is_file()
