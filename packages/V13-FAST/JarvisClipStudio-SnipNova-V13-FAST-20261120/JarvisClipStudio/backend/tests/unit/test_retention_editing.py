"""Retention editing lane (2026-11-15): no silent B-roll loss, task-level
B-roll de-duplication, transition discipline, opening hook treatment.

Named by Luffy's review of four real exports from his own machine:
one B-roll insert across 165 s of Auto/finance output with nothing on
the record explaining the missing ones, the same slide transition in
every clip (one of them mid-sentence), and no opening treatment at
all. The resolver is exercised through an httpx MockTransport (no
network), exactly like the Pixabay suites.
"""

import httpx
import pytest

from src.auto_director.clip_plan import (
    ClipPlan,
    OpeningTreatment,
    Phase,
    TransitionChoice,
    apply_plan_opening_punch,
)
from src.auto_director.edl import (
    _plan_broll_segments,
    build_edl,
    enforce_transition_discipline,
    join_in_continuous_speech,
    load_profile,
    speech_gap_at,
)
from src.auto_director.template_mode import plainify_edl
from src.media import coverr_broll, pixabay_broll
from src.services.video_service import VideoService


@pytest.fixture(autouse=True)
def _clear_search_caches():
    pixabay_broll._search_cache.clear()
    pixabay_broll._photo_search_cache.clear()
    coverr_broll._search_cache.clear()
    yield
    pixabay_broll._search_cache.clear()
    pixabay_broll._photo_search_cache.clear()
    coverr_broll._search_cache.clear()


@pytest.fixture()
def pixabay_only(monkeypatch):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")
    monkeypatch.setattr(coverr_broll, "_api_key", lambda: "")


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _video_hit(asset_id, tags):
    return {
        "id": asset_id,
        "pageURL": f"https://pixabay.com/videos/{asset_id}",
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


def _pool_handler(hits):
    def handler(request):
        if "pixabay.com/api" in str(request.url):
            return httpx.Response(
                200, json={"total": len(hits), "totalHits": len(hits), "hits": hits}
            )
        return httpx.Response(200, content=b"fake-mp4-bytes")

    return handler


def _words(duration, step=0.4, gaps=()):
    """Continuous word timings; ``gaps`` are (start, end) silent spans."""
    words = []
    index = 0
    while True:
        t = round(index * step, 3)
        if t >= duration:
            return words
        index += 1
        if any(start <= t < end for start, end in gaps):
            continue
        words.append({"word": "talk", "start": t, "end": round(t + step, 3)})


# ---------------------------------------------------------------------------
# 1. No silent B-roll loss — placement stamps
# ---------------------------------------------------------------------------

def test_placement_drop_is_recorded_with_its_guard():
    profile = load_profile("finance_business")
    # A punch-in sitting on the concept's anchor blocks the window.
    zooms = [{"type": "punch_in", "time": 10.0, "duration": 0.5, "scale": 1.2}]
    concepts = [{
        "anchor_sec": 10.0,
        "spoken": "the money compounds quietly",
        "concept": "hands counting paper money on a desk",
        "search_queries": ["counting money hands", "paper money desk"],
        "sense": None,
        "media": "either",
        "avoid": [],
    }]
    dropped = []
    placements = _plan_broll_segments(
        profile, _words(40), 40.0, [], zooms, [],
        concepts=concepts, dropped_sink=dropped,
    )
    assert placements == []
    assert len(dropped) == 1
    assert dropped[0]["stage"] == "placement"
    assert dropped[0]["reason"] == "blocked_window"
    assert dropped[0]["concept"] == "hands counting paper money on a desk"


def test_build_edl_stamps_broll_dropped():
    profile = load_profile("finance_business")
    # "money" is spoken only at 9.5 s of a 10 s clip: the cue cannot
    # fit before the end guard, and a 10 s finance clip has a single
    # segment, so there is no boundary fallback either.
    words = [
        {"word": "money", "start": 9.5, "end": 9.9},
    ]
    edl = build_edl(
        {"id": "c", "start": 0, "end": 10, "duration": 10}, words, profile, []
    )
    assert edl["broll"] == []
    assert len(edl["broll_dropped"]) == 1
    assert edl["broll_dropped"][0]["stage"] == "placement"


# ---------------------------------------------------------------------------
# 1. No silent B-roll loss — resolve stamps
# ---------------------------------------------------------------------------

def test_resolve_without_any_provider_stamps_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "")
    monkeypatch.setattr(coverr_broll, "_api_key", lambda: "")
    edl = {"broll": [{"query": "money", "local_path": "", "asset_id": None}]}
    assert pixabay_broll.resolve_edl_broll(edl, cache_dir=tmp_path) == 0
    assert edl["broll"][0]["not_inserted"] == {
        "stage": "resolve",
        "reason": "no_provider_configured",
    }


def test_resolve_provider_empty_stamps_reason(pixabay_only, tmp_path):
    client = _mock_client(_pool_handler([]))
    edl = {"broll": [{"query": "money", "local_path": "", "asset_id": None}]}
    assert pixabay_broll.resolve_edl_broll(edl, cache_dir=tmp_path, client=client) == 0
    stamp = edl["broll"][0]["not_inserted"]
    assert stamp["stage"] == "resolve"
    assert stamp["reason"] == "provider_empty"


def test_resolve_gate_rejection_stamps_reason(pixabay_only, tmp_path):
    # A hit whose tags share nothing with the query or its context is
    # gate-rejected; the cue goes unfilled WITH its reason recorded.
    hits = [_video_hit(7, "underwater, coral reef, fish, ocean")]
    client = _mock_client(_pool_handler(hits))
    edl = {"broll": [{
        "query": "money", "local_path": "", "asset_id": None,
        "context": "money savings account grows",
    }]}
    assert pixabay_broll.resolve_edl_broll(edl, cache_dir=tmp_path, client=client) == 0
    stamp = edl["broll"][0]["not_inserted"]
    assert stamp["reason"] == "gate_rejected"
    assert stamp["candidates"]["gate_rejected"] == 1


# ---------------------------------------------------------------------------
# 2. Task-level de-duplication
# ---------------------------------------------------------------------------

def test_ledger_gives_sibling_clips_different_assets(pixabay_only, tmp_path):
    hits = [
        _video_hit(101, "money, cash, savings, finance"),
        _video_hit(102, "money, wallet, cash, finance"),
    ]
    client = _mock_client(_pool_handler(hits))
    ledger = pixabay_broll.BrollUsageLedger()

    def _edl():
        return {"broll": [{
            "query": "money", "local_path": "", "asset_id": None,
            "context": "money savings cash",
        }]}

    first, second = _edl(), _edl()
    assert ledger.resolve(first, cache_dir=tmp_path, client=client) == 1
    assert ledger.resolve(second, cache_dir=tmp_path, client=client) == 1
    assert first["broll"][0]["asset_id"] == 101
    # The second clip falls to the NEXT candidate — never a repeat.
    assert second["broll"][0]["asset_id"] == 102
    assert ledger.used_ids == frozenset({"101", "102"})

    # Pool exhausted: fewer inserts, stamped — never a silent repeat.
    third = _edl()
    assert ledger.resolve(third, cache_dir=tmp_path, client=client) == 0
    assert third["broll"][0]["not_inserted"]["reason"] == "duplicate_in_task"


def test_resolve_exclude_ids_falls_to_next_candidate(pixabay_only, tmp_path):
    hits = [
        _video_hit(201, "money, cash, savings, finance"),
        _video_hit(202, "money, wallet, cash, finance"),
    ]
    client = _mock_client(_pool_handler(hits))
    edl = {"broll": [{
        "query": "money", "local_path": "", "asset_id": None,
        "context": "money savings cash",
    }]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=client, exclude_ids=("201",)
    )
    assert resolved == 1
    assert edl["broll"][0]["asset_id"] == 202
    assert "not_inserted" not in edl["broll"][0]


# ---------------------------------------------------------------------------
# 3. Transition discipline
# ---------------------------------------------------------------------------

def test_speech_gap_measurement():
    words = _words(10)
    assert speech_gap_at(words, 5.0) == 0.0  # a word straddles the join
    gapped = _words(10, gaps=[(4.0, 5.2)])
    assert speech_gap_at(gapped, 4.6) >= 1.0
    assert join_in_continuous_speech(words, 5.0) is True
    assert join_in_continuous_speech(gapped, 4.6) is False
    # A planner-detected boundary excuses the join even mid-flow.
    assert join_in_continuous_speech(words, 5.0, boundary_times=(5.2,)) is False


def _segments(*joins):
    segments = [{"index": 0, "start": 0.0, "transition_in": "none", "transition_duration": 0}]
    for index, (start, name, duration) in enumerate(joins, start=1):
        segments.append({
            "index": index, "start": start,
            "transition_in": name, "transition_duration": duration,
        })
    return segments


def test_discipline_downgrades_mid_speech_and_repeats():
    words = _words(30, gaps=[(11.0, 12.4)])
    segments = _segments(
        (6.0, "slide", 0.3),    # mid-speech -> cut
        (11.6, "slide", 0.3),   # inside the pause -> survives
        (18.0, "wipe", 0.4),    # mid-speech -> cut
    )
    notes = enforce_transition_discipline(
        segments, words, only_indices={1, 2, 3}
    )
    assert segments[1]["transition_in"] == "cut"
    assert segments[2]["transition_in"] == "slide"
    assert segments[3]["transition_in"] == "cut"
    assert [n["reason"] for n in notes] == ["continuous_speech", "continuous_speech"]


def test_discipline_repeated_transition_back_to_back():
    segments = _segments(
        (5.6, "slide", 0.3),
        (11.6, "slide", 0.3),   # adjacent join, same name -> cut
        (17.6, "slide", 0.3),   # not adjacent to a kept slide -> survives
    )
    gapped = _words(30, gaps=[(5.0, 6.4), (11.0, 12.4), (17.0, 18.4)])
    notes = enforce_transition_discipline(
        segments, gapped, only_indices={1, 2, 3}
    )
    assert segments[1]["transition_in"] == "slide"
    assert segments[2]["transition_in"] == "cut"
    assert segments[3]["transition_in"] == "slide"
    assert [n["reason"] for n in notes] == ["repeated_transition"]


def test_discipline_leaves_profile_grammar_untouched():
    # only_indices scopes the judgement: joins the caller did not make
    # a decision about keep the profile's grammar exactly.
    words = _words(30)
    segments = _segments((6.0, "wipe", 0.4), (12.0, "wipe", 0.4))
    notes = enforce_transition_discipline(segments, words, only_indices=set())
    assert notes == []
    assert all(s["transition_in"] == "wipe" for s in segments[1:])


def test_signature_suppressed_mid_speech_and_kept_at_a_pause():
    profile = load_profile("finance_business")
    clip = {"id": "c", "start": 0, "end": 30, "duration": 30}

    mid_speech = build_edl(clip, _words(30), profile, [])
    assert "signature_transition" not in mid_speech
    assert mid_speech["transition_discipline"][0]["from"] == "slide"
    assert mid_speech["transition_discipline"][0]["reason"] == "continuous_speech"

    # The finance signature join lands at 6 s (first join within the
    # fallback reach of the 3-5 s window); a pause there saves it.
    paused = build_edl(clip, _words(30, gaps=[(5.2, 6.8)]), profile, [])
    assert paused["signature_transition"]["name"] == "slide"


def test_plan_choice_mid_speech_is_downgraded_by_consumption():
    profile = load_profile("finance_business")
    words = _words(30)
    edl = build_edl({"id": "c", "start": 0, "end": 30, "duration": 30}, words, profile, [])
    joins = [s["start"] for s in edl["segments"][1:]]
    plan = ClipPlan(
        niche="finance_business",
        source="llm",
        transition_choices=[
            TransitionChoice(
                name="slide", at_offset=joins[1],
                reason="topic change for emphasis in the edit",
            )
        ],
    )
    VideoService._consume_viral_plan(edl, plan, profile, words)
    target = edl["segments"][2]
    assert target["transition_in"] == "cut"
    assert target["transition_duration"] == 0
    notes = edl["transition_discipline"]
    assert notes[-1]["reason"] == "continuous_speech"
    assert notes[-1]["from"] == "slide"


def test_plan_choice_on_a_phase_boundary_survives():
    profile = load_profile("finance_business")
    words = _words(30)
    edl = build_edl({"id": "c", "start": 0, "end": 30, "duration": 30}, words, profile, [])
    joins = [s["start"] for s in edl["segments"][1:]]
    plan = ClipPlan(
        niche="finance_business",
        source="llm",
        phases=[Phase(start_offset=joins[1], end_offset=joins[1] + 5.0, intent="development")],
        transition_choices=[
            TransitionChoice(
                name="slide", at_offset=joins[1],
                reason="the topic turns to a new lesson here",
            )
        ],
    )
    VideoService._consume_viral_plan(edl, plan, profile, words)
    assert edl["segments"][2]["transition_in"] == "slide"


# ---------------------------------------------------------------------------
# 4. Opening hook treatment
# ---------------------------------------------------------------------------

def _opening_ramps(edl):
    return [
        z for z in edl["zoom_keyframes"]
        if z.get("type") == "ken_burns" and z.get("trigger") == "opening_punch"
    ]


def test_opening_ramp_on_every_auto_clip():
    profile = load_profile("finance_business")
    edl = build_edl(
        {"id": "c", "start": 0, "end": 40, "duration": 40}, _words(40), profile, []
    )
    ramps = _opening_ramps(edl)
    assert len(ramps) == 1
    ramp = ramps[0]
    assert ramp["start"] == 0.0
    assert ramp["end"] == 2.0
    assert ramp["scale_start"] == 1.0
    assert 1.0 < ramp["scale_end"] <= 1.15


def test_opening_ramp_absent_in_plain():
    profile = load_profile("finance_business")
    edl = build_edl(
        {"id": "c", "start": 0, "end": 40, "duration": 40}, _words(40), profile, []
    )
    plainify_edl(edl)
    assert edl["zoom_keyframes"] == []


def test_opening_ramp_skips_short_clips_and_treated_openings():
    profile = load_profile("finance_business")
    short = build_edl(
        {"id": "c", "start": 0, "end": 3, "duration": 3}, _words(3), profile, []
    )
    assert _opening_ramps(short) == []
    # An energy-peak punch inside the window already treats the opening.
    punched = build_edl(
        {"id": "c", "start": 0, "end": 40, "duration": 40},
        _words(40), profile, [1.0],
    )
    assert _opening_ramps(punched) == []
    assert any(z.get("type") == "punch_in" for z in punched["zoom_keyframes"])
    # Travel's own whole-clip drift covers the opening: no second ramp.
    travel = build_edl(
        {"id": "c", "start": 0, "end": 40, "duration": 40},
        _words(40), load_profile("travel_vlog"), [],
    )
    assert _opening_ramps(travel) == []


def test_plan_punch_rescales_the_opening_ramp_instead_of_stacking():
    profile = load_profile("finance_business")
    edl = build_edl(
        {"id": "c", "start": 0, "end": 40, "duration": 40}, _words(40), profile, []
    )
    plan = ClipPlan(
        niche="finance_business",
        source="llm",
        opening_treatment=OpeningTreatment(
            punch_zoom="subtle", at_offset=0.0, reason="hook emphasis"
        ),
    )
    assert apply_plan_opening_punch(edl, plan, profile) is True
    ramps = _opening_ramps(edl)
    assert len(ramps) == 1
    assert ramps[0]["scale_end"] > 1.12  # re-scaled to the plan's subtle punch
    assert not any(z.get("type") == "punch_in" for z in edl["zoom_keyframes"])
