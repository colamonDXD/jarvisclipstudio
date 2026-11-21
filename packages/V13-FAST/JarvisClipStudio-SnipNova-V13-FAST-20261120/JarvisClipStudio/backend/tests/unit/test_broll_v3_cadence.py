"""Smart B-Roll v3: cadence, lexicon cues, sense traps, guards, protection, PiP.

v3 is the default since 2026-11-17 for profiles carrying ``broll.cadence_per_min``
(the eight stock-B-roll niches). ``BROLL_CADENCE=legacy`` restores v2 exactly;
the v2 pins live in the older B-roll test modules under the ``v2_cadence``
fixture. The real-clip fixture here is TJR (finance, 28.6 s), the same words the
v2 suites use.
"""

from __future__ import annotations

import copy

import pytest

from src.auto_director import edl as E
from src.auto_director.broll_lexicon import LEXICON, lexicon_concepts
from src.auto_director.clip_plan import (
    ClipPlan,
    _profile_broll_concept_cap,
    apply_plan_broll_concepts,
)
from src.auto_director.plan_service import _broll_prompt_line
from src.media.edl_render import build_edl_filter_graph
from test_broll_word_sense import TJR_WORDS

FIN = E.load_profile("finance_business")
TRAVEL = E.load_profile("travel_vlog")
EDU = E.load_profile("education_tutorial")
GAMING = E.load_profile("gaming")


def _words(text: str, start: float = 0.0, step: float = 0.4) -> list[dict]:
    out, t = [], start
    for token in text.split():
        out.append({"word": token, "text": token, "start": round(t, 2), "end": round(t + step * 0.9, 2)})
        t += step
    return out


def _tjr() -> list[dict]:
    return [{"word": w, "start": s, "end": s + 0.3} for w, s in TJR_WORDS]


def _plan(profile, words, duration, **kw):
    sink: list[dict] = []
    placed = E._plan_broll_segments(
        profile, words, duration, [{"start": 0.0, "end": duration}], [], [], dropped_sink=sink, **kw
    )
    return placed, sink


# --- cadence -----------------------------------------------------------------

def test_v3_is_default_and_legacy_switch_restores_v2(monkeypatch):
    assert E.broll_uses_v3(FIN)
    assert E.broll_target_count(FIN, 40.0) == 3  # 4.0/min
    assert E.broll_target_count(FIN, 60.0) == 4
    monkeypatch.setenv("BROLL_CADENCE", "legacy")
    assert not E.broll_uses_v3(FIN)
    assert E.broll_target_count(FIN, 40.0) == 1  # v2: 1.5/min, one per typical clip


def test_profiles_without_cadence_are_unchanged():
    assert not E.broll_uses_v3(GAMING)
    assert E.broll_target_count(GAMING, 60.0) == 0
    custom = {"broll": {"enabled": True, "source": "stock", "density_per_min": 1.5}}
    assert not E.broll_uses_v3(custom) and E.broll_target_count(custom, 40.0) == 1


def test_owner_165s_finance_run_now_plans_about_ten():
    # The owner's real run: four ~41 s finance clips, one insert in 165 s.
    # v3 plans up to 12; guards and candidate quality decide how many land.
    assert sum(E.broll_target_count(FIN, d) for d in (41.0, 42.0, 40.0, 42.0)) == 12


def test_concept_cap_and_prompt_follow_the_v3_cadence():
    assert _profile_broll_concept_cap(FIN, 60.0) == 4
    assert "return up to 4 broll_concepts" in _broll_prompt_line(FIN, 60.0)


# --- cue source + sense traps ---------------------------------------------------

def test_lexicon_is_phrase_based_and_well_formed():
    assert len(LEXICON) >= 100
    for entry in LEXICON:
        assert entry.queries and all(len(q.split()) >= 2 for q in entry.queries), entry.id


def test_real_tjr_clip_gets_its_full_finance_cadence():
    placed, _ = _plan(FIN, _tjr(), 28.6)
    placed = sorted(placed, key=lambda p: p["start"])
    assert [p.get("lexicon_id") for p in placed] == ["trading", "market_fin"]
    assert placed[1]["sense"] == "market:financial"
    assert all(p["cue_source"] == "lexicon" for p in placed)


@pytest.mark.parametrize("text, forbidden", [
    ("i went to the farmers market to buy fresh vegetables and fruit", "market_fin"),
    ("we train at the gym every morning to stay fit", "train_rail"),
    ("let steve jobs inspire you with this story", "office_work"),
    ("the new trade deal between the two countries", "trading"),
    ("please be patient with the process", "doctor_health"),
    # found on real speech (Phase 1 review of the clipping-tools video):
    ("do you think that was the right thing for Tim Cook to have done", "cooking"),
    ("which keeps the question and reaction visible in interview footage", "hiring"),
    ("the free interface has large ads and the crop sits close", "sales_marketing"),
    ("it does not just write down what is said in the skill file", "learning"),
])
def test_sense_traps_do_not_fire(text, forbidden):
    found = [c["lexicon_id"] for c in lexicon_concepts(FIN, _words(text), 20.0, limit=5)]
    assert forbidden not in found


def test_trap_words_fire_with_their_sense_context():
    found = lexicon_concepts(FIN, _words("interest rates went up so the central bank changed policy"), 20.0, limit=5)
    assert found[0]["lexicon_id"] == "interest_rate" and found[0]["sense"] == "interest:financial"


def test_niche_domain_ranks_first():
    text = "the stock market fell while i was at the beach with my dog"
    finance_first = lexicon_concepts(FIN, _words(text), 20.0, limit=3)[0]["lexicon_id"]
    travel_first = lexicon_concepts(TRAVEL, _words(text), 20.0, limit=3)[0]["lexicon_id"]
    assert finance_first == "stock_market" and travel_first == "beach"


# --- guards on the v3 path --------------------------------------------------------

def _long_finance_words() -> list[dict]:
    text = ("today we talk about the stock market and why interest rates matter for your savings account "
            "because inflation keeps rising and the central bank keeps changing policy while investors watch "
            "bitcoin and crypto prices and real estate and property prices in every city skyline while "
            "startup founders raise venture capital and the company revenue and profit keep growing")
    return _words(text, start=0.0, step=0.45)


def test_v3_keeps_every_placement_guard():
    words = _long_finance_words()
    duration = 40.0
    placed, _ = _plan(FIN, words, duration)
    assert 2 <= len(placed) <= E.broll_target_count(FIN, duration)
    starts = sorted((p["start"], p["end"]) for p in placed)
    assert starts[0][0] >= E.BROLL_HOOK_GUARD_SEC
    for (a0, a1), (b0, _b1) in zip(starts, starts[1:]):
        assert b0 - a1 >= E.BROLL_FACE_RETURN_SEC - 1e-6  # face returns between cutaways
    assert sum(b - a for a, b in starts) <= 0.30 * duration + 1e-6
    assert all(b <= duration - E.BROLL_END_GUARD_SEC + 1e-6 for _a, b in starts)


def test_v3_respects_payoff_phases():
    words = _long_finance_words()
    phases = [{"start_offset": 0.0, "end_offset": 40.0, "intent": "payoff"}]
    placed, sink = _plan(FIN, words, 40.0, phases=phases)
    assert placed == [] and {d["reason"] for d in sink} <= {"payoff_phase", "density_cap"}


def test_no_cutaway_over_a_spoken_demonstration():
    words = _words("hello everyone so look at this chart it shows revenue growth and profit for the company today", step=0.5)
    placed, sink = _plan(FIN, words, 20.0)
    assert not any(p["start"] < 6.0 for p in placed)
    assert "demonstration" in {d["reason"] for d in sink}


def test_live_reaction_turns_the_cue_into_picture_in_picture():
    words = _words("okay so haha that is insane! the bitcoin price doubled overnight wow", step=0.5)
    placed, _ = _plan(FIN, words, 15.0)
    assert placed and placed[0]["layout"] == "pip" and "reaction" in placed[0]["layout_reason"]


def test_tutorial_profile_defaults_to_picture_in_picture():
    words = _words("in this lesson students learn math equations and the scientist explains the experiment in the laboratory", step=0.5)
    placed, _ = _plan(EDU, words, 20.0)
    assert placed and all(p["layout"] == "pip" for p in placed)


# --- the LLM plan keeps priority; the lexicon tops up ------------------------------

def test_plan_concepts_keep_priority_and_lexicon_tops_up():
    words = _long_finance_words()
    edl = {"duration": 40.0, "segments": [{"start": 0.0, "end": 40.0}], "zoom_keyframes": [], "overlays": []}
    plan = ClipPlan.from_dict({
        "source": "llm", "niche": "finance_business", "duration": 40.0,
        "broll_concepts": [{
            "anchor_sec": 2.25, "spoken": "stock market", "concept": "stock trading screens in an office",
            "search_queries": ["stock trading screens", "stock market chart monitor"],
            "sense": "market:financial", "media": "video", "avoid": [],
        }],
    })
    placed = apply_plan_broll_concepts(edl, plan, FIN, words)
    cues = edl["broll"]
    assert placed == len(cues) and len(cues) >= 2
    assert cues[0]["cue_source"] == "plan" and cues[0]["search_query"] == "stock trading screens"
    assert all(c["cue_source"] == "lexicon" for c in cues[1:])
    assert all(abs(c["start"] - 2.25) >= 5.0 for c in cues[1:])


# --- render -------------------------------------------------------------------------

def _render_graph(tmp_path, layout):
    stock = tmp_path / "stock.mp4"
    stock.write_bytes(b"x")
    entry = {"start": 2.0, "end": 5.0, "source_start": 0.0, "local_path": str(stock), "query": "stock market"}
    if layout:
        entry["layout"] = layout
    edl = {"duration": 10.0, "segments": [{"start": 0.0, "end": 10.0, "transition_in": "none"}], "broll": [entry]}
    _inputs, graph, _encoder, _label = build_edl_filter_graph(copy.deepcopy(edl), tmp_path / "in.mp4")
    return graph


def test_pip_renders_as_a_framed_inset_above_the_captions(tmp_path):
    g = _render_graph(tmp_path, "pip")
    assert "overlay=x=(W-w)/2:y=H*0.7-h" in g and "pad=iw+16:ih+16:8:8:color=white" in g


def test_cutaway_render_is_unchanged(tmp_path):
    assert _render_graph(tmp_path, None) == _render_graph(tmp_path, "cutaway")
    assert "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" in _render_graph(tmp_path, None)


# --- diagnosability: a rejected key is a provider error, not an empty library -------

def test_rejected_key_is_stamped_provider_error(monkeypatch, tmp_path):
    import httpx
    from src.media import pixabay_broll

    class _Rejecting:
        def get(self, *a, **k):
            request = httpx.Request("GET", "https://pixabay.com/api/videos/")
            raise httpx.HTTPStatusError("401", request=request, response=httpx.Response(401, request=request))

        def close(self):
            pass

    monkeypatch.setattr(pixabay_broll, "pixabay_configured", lambda: True)
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "present-but-rejected")
    monkeypatch.setattr(pixabay_broll, "_new_client", lambda: _Rejecting())
    monkeypatch.setattr("src.media.coverr_broll.coverr_configured", lambda: False)
    monkeypatch.setattr(pixabay_broll, "broll_cache_dir", lambda: tmp_path)
    edl = {"broll": [{"query": "stock market", "search_query": "stock market trading screen", "start": 2.0,
                      "end": 5.0, "source": "stock", "local_path": "", "context": "the stock market"}]}
    pixabay_broll.resolve_edl_broll(edl)
    assert edl["broll"][0]["not_inserted"]["reason"] == "provider_error"


# --- Phase 2 robustness: a GPU without CUDA libraries falls back to CPU -------------

def test_missing_cuda_library_is_a_cpu_fallback_error(monkeypatch):
    from src.media import transcription as T

    assert T._is_cuda_unusable_error(RuntimeError("Library cublas64_12.dll is not found or cannot be loaded"))
    assert T._is_cuda_unusable_error(RuntimeError("CUDA failed with error out of memory"))
    assert not T._is_cuda_unusable_error(RuntimeError("invalid audio file"))
    monkeypatch.setenv("WHISPER_DEVICE", "cpu")
    assert T._faster_whisper_device_and_compute_type() == ("cpu", "int8")


def test_cuda_unusable_is_remembered_for_the_process(monkeypatch):
    from src.media import transcription as T

    monkeypatch.setattr(T, "_CUDA_UNUSABLE_REASON", None)
    monkeypatch.delenv("WHISPER_DEVICE", raising=False)
    T._mark_cuda_unusable(RuntimeError("Library cublas64_12.dll is not found or cannot be loaded"))
    assert T._faster_whisper_device_and_compute_type() == ("cpu", "int8")
