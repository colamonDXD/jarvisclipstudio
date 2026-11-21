"""Editorial quality scoring v2: dimension sub-scores with reasons.

Fixtures are stored-fact dicts in the repository clip shape (flat
selection sub-scores plus ``qc_report`` / ``clip_plan`` JSON). Every
assertion runs the real builder — the scoring logic is never mocked.
"""

import json
from pathlib import Path

import pytest

from src.editorial_scoring import WEIGHTS, build_editorial_quality

TEXT = (
    "Why does this trick work every single time? Because the setup hides "
    "the answer until the very last second of the story. First we tried "
    "the obvious approach and it failed in front of everyone watching. "
    "Then one small change flipped the whole result completely. That is "
    "the moment everything finally made sense for us."
)


def _qc(checks):
    return json.dumps({"version": 1, "status": "pass", "checks": checks})


def _check(name, status, detail="", **evidence):
    return {"name": name, "status": status, "detail": detail, "evidence": evidence}


RICH_QC = _qc(
    [
        _check("resolution", "pass", width=1080, height=1920),
        _check("black_frames", "pass"),
        _check("frozen_frame", "pass"),
        _check("subject_in_frame", "pass", found=3, measured=3),
        _check("audio", "pass", rms_db=-16.0),
        _check("music", "pass"),
        _check(
            "hook_window", "pass",
            hook_status="cold_opened", hook_timestamp=0.7,
        ),
        _check("cut_rate", "pass", cuts_per_min=12.0, band=[10.0, 15.0], segments=4),
        _check("caption_presence", "pass", chunks=10),
        _check("caption_hold", "pass", max_hold_seconds=2.0, cap_seconds=3.5),
    ]
)

RICH_PLAN = json.dumps(
    {
        "niche": "podcast_news",
        "hook": {"type": "question", "timestamp": 0.7, "confidence": 0.9, "reason": "opens with a question"},
        "phases": [
            {"start_offset": 0, "end_offset": 3, "intent": "hook"},
            {"start_offset": 3, "end_offset": 11, "intent": "context"},
            {"start_offset": 11, "end_offset": 15, "intent": "build"},
            {"start_offset": 15, "end_offset": 19, "intent": "payoff"},
            {"start_offset": 19, "end_offset": 20, "intent": "conclusion"},
        ],
    }
)


def rich_clip(**overrides):
    clip = {
        "text": TEXT,
        "duration": 20.0,
        "relevance_score": 0.85,
        "virality_score": 75,
        "hook_score": 20,
        "engagement_score": 18,
        "value_score": 22,
        "shareability_score": 15,
        "hook_type": "question",
        "niche_override": "podcast_news",
        "qc_report": RICH_QC,
        "clip_plan": RICH_PLAN,
    }
    clip.update(overrides)
    return clip


def dims(clip):
    return build_editorial_quality(clip)["dimensions"]


# --- Per-dimension evidence ---------------------------------------------------


def test_hook_strength_blends_llm_subscore_with_measured_hook_window():
    hook = dims(rich_clip())["hook_strength"]
    # 0.7 * (20/25 * 100) + 0.3 * 100 (QC pass) = 86
    assert hook["score"] == 86
    assert hook["source"] == "derived"
    assert "20/25" in hook["reason"]
    assert "0.7s" in hook["reason"]


def test_hook_strength_llm_only_is_labelled_llm():
    hook = dims({"hook_score": 25, "engagement_score": 10, "value_score": 10,
                 "shareability_score": 10, "virality_score": 55})["hook_strength"]
    assert hook["score"] == 100
    assert hook["source"] == "llm"


def test_story_coherence_from_sentences_and_plan_phases():
    story = dims(rich_clip())["story_coherence"]
    assert story["score"] >= 85
    assert story["source"] == "derived"
    assert "complete sentence" in story["reason"]
    assert "payoff" in story["reason"]


def test_story_coherence_fragment_text_scores_low():
    story = dims({"text": "and then he just", "duration": 5.0})["story_coherence"]
    assert story["score"] is not None and story["score"] <= 45


def test_context_preservation_from_context_phase_and_words():
    context = dims(rich_clip())["context_preservation"]
    assert 70 <= context["score"] <= 90
    assert "context phase covers 40%" in context["reason"]


def test_context_preservation_kept_hook_is_strong_evidence():
    qc = _qc([_check("hook_window", "warn", hook_status="kept_original_context",
                     hook_timestamp=5.6)])
    context = dims({"qc_report": qc})["context_preservation"]
    assert context["score"] == 90
    assert context["source"] == "measured"
    assert "preserve context" in context["reason"]


def test_visual_quality_is_measured_from_qc_checks():
    visual = dims(rich_clip())["visual_quality"]
    assert visual["score"] == 100
    assert visual["source"] == "measured"
    assert "1080x1920" in visual["reason"]
    assert "3/3" in visual["reason"]


def test_visual_quality_drops_with_failed_and_warned_checks():
    qc = _qc([
        _check("resolution", "fail", width=640, height=360),
        _check("black_frames", "warn"),
        _check("frozen_frame", "pass"),
        _check("subject_in_frame", "warn", found=1, measured=3),
    ])
    visual = dims({"qc_report": qc})["visual_quality"]
    # (0 + 50 + 100 + 50) / 4 = 50
    assert visual["score"] == 50


def test_audio_quality_cites_measured_rms():
    audio = dims(rich_clip())["audio_quality"]
    assert audio["score"] == 100
    assert audio["source"] == "measured"
    assert "-16.0 dBFS" in audio["reason"]


def test_audio_quality_silent_clip_scores_low():
    qc = _qc([_check("audio", "warn", rms_db=-80.0)])
    audio = dims({"qc_report": qc})["audio_quality"]
    assert audio["score"] == 35


def test_caption_accuracy_from_presence_and_hold_with_scope_note():
    caption = dims(rich_clip())["caption_accuracy"]
    assert caption["score"] == 100
    assert caption["source"] == "measured"
    assert "10 chunks" in caption["reason"]
    assert "2.0s" in caption["reason"]
    # Honest scope: pacing/presence was measured, wording was not.
    assert "not compared" in caption["reason"]


def test_pacing_from_cut_rate_caption_hold_and_speech_rate():
    pacing = dims(rich_clip())["pacing"]
    assert pacing["score"] >= 80
    assert "cut rate 12/min" in pacing["reason"]
    assert "words/min" in pacing["reason"]


def test_pacing_speech_rate_alone_is_derived():
    pacing = dims({"text": TEXT, "duration": 20.0})["pacing"]
    assert pacing["score"] is not None
    assert pacing["source"] == "derived"


def test_emotional_interest_reuses_engagement_subscore():
    emotion = dims(rich_clip())["emotional_interest"]
    assert emotion["score"] == 72  # 18/25 scaled
    assert emotion["source"] == "llm"
    assert "18/25" in emotion["reason"]


def test_payoff_blends_value_subscore_with_late_payoff_phase():
    payoff = dims(rich_clip())["payoff"]
    # 0.5 * (22/25 * 100) + 0.5 * 90 (payoff phase ends at 95%) = 89
    assert payoff["score"] == 89
    assert payoff["source"] == "derived"
    assert "95%" in payoff["reason"]


def test_niche_relevance_reuses_relevance_score():
    niche = dims(rich_clip())["niche_relevance"]
    assert niche["score"] == 85
    assert niche["source"] == "llm"
    assert "0.85" in niche["reason"]
    assert "podcast_news" in niche["reason"]


# --- Null policy ----------------------------------------------------------------


def test_empty_clip_measures_nothing_and_fabricates_nothing():
    result = build_editorial_quality({})
    assert result["aggregate"] is None
    assert result["scored_dimensions"] == 0
    assert result["legacy_selection_total"] is None
    for key, dimension in result["dimensions"].items():
        assert dimension["score"] is None, key
        assert dimension["reason"] == "not measured", key
        assert dimension["source"] is None, key


def test_all_zero_subscores_are_the_absent_sentinel_not_real_zeros():
    clip = {"hook_score": 0, "engagement_score": 0, "value_score": 0,
            "shareability_score": 0, "virality_score": 0}
    result = dims(clip)
    assert result["hook_strength"]["score"] is None
    assert result["emotional_interest"]["score"] is None
    assert result["payoff"]["score"] is None


def test_measured_dimensions_are_null_without_a_qc_report():
    result = dims({"text": TEXT, "duration": 20.0, "relevance_score": 0.5})
    for key in ("visual_quality", "audio_quality", "caption_accuracy"):
        assert result[key]["score"] is None, key
        assert result[key]["reason"] == "not measured", key


def test_malformed_stored_json_degrades_to_not_measured():
    result = build_editorial_quality(
        {"qc_report": "{not json", "clip_plan": "nope", "text": TEXT, "duration": 20.0}
    )
    assert result["dimensions"]["visual_quality"]["score"] is None
    # Transcript-derived dimensions still work from the text alone.
    assert result["dimensions"]["story_coherence"]["score"] is not None


def test_qc_dict_shape_is_accepted_as_well_as_json_string():
    report = json.loads(RICH_QC)
    assert dims(rich_clip(qc_report=report))["visual_quality"]["score"] == 100


def test_nested_segment_shape_is_supported():
    clip = {"virality": {"hook_score": 20, "engagement_score": 20,
                         "value_score": 20, "shareability_score": 20,
                         "total_score": 80}}
    result = build_editorial_quality(clip)
    assert result["dimensions"]["hook_strength"]["score"] == 80
    assert result["legacy_selection_total"] == 80


# --- Campaign compliance (optional) ---------------------------------------------


def test_campaign_dimension_is_absent_without_compliance_data():
    assert "campaign_compliance" not in dims(rich_clip())


def test_campaign_dimension_appears_from_a_stored_record():
    clip = rich_clip(campaign_compliance={"score": 70, "reason": "brief check: logo present, link missing"})
    campaign = dims(clip)["campaign_compliance"]
    assert campaign["score"] == 70
    assert "logo present" in campaign["reason"]


def test_campaign_dimension_appears_from_a_qc_check():
    qc = _qc([_check("campaign_compliance", "pass", detail="all brief items present")])
    campaign = dims({"qc_report": qc})["campaign_compliance"]
    assert campaign["score"] == 100
    assert campaign["source"] == "measured"


# --- Aggregate --------------------------------------------------------------------


def test_aggregate_is_the_documented_weighted_mean():
    result = build_editorial_quality(rich_clip())
    dimensions = result["dimensions"]
    expected = round(
        sum(d["score"] * WEIGHTS[k] for k, d in dimensions.items() if d["score"] is not None)
        / sum(WEIGHTS[k] for k, d in dimensions.items() if d["score"] is not None)
    )
    assert result["aggregate"] == expected
    assert result["scored_dimensions"] == 10
    assert result["label"] == "Editorial quality"
    assert result["legacy_selection_total"] == 75


def test_aggregate_renormalises_over_available_dimensions_only():
    # Only niche relevance has evidence: the aggregate is that score.
    result = build_editorial_quality({"relevance_score": 0.5})
    assert result["aggregate"] == 50
    assert result["scored_dimensions"] == 1


def test_aggregate_includes_campaign_only_when_present():
    base = build_editorial_quality(rich_clip())
    with_campaign = build_editorial_quality(
        rich_clip(campaign_compliance={"score": 0, "reason": "brief check failed"})
    )
    assert with_campaign["aggregate"] < base["aggregate"]
    assert with_campaign["scored_dimensions"] == 11


# --- No outcome claims --------------------------------------------------------------


def test_no_dimension_or_label_claims_an_audience_outcome():
    # Forbidden phrases are assembled from fragments so this test file
    # itself never contains them as literals.
    forbidden = [
        "will go " + "vir" + "al",
        "pred" + "icted views",
        "expected " + "views",
        "view " + "pred" + "iction",
        "vir" + "ality score",
        "guaranteed " + "views",
    ]
    result = build_editorial_quality(rich_clip())
    surfaces = [result["label"], result["note"]]
    for dimension in result["dimensions"].values():
        surfaces += [dimension["label"], dimension["reason"]]
    for surface in surfaces:
        for phrase in forbidden:
            assert phrase not in surface.lower(), (phrase, surface)


def test_scoring_module_source_contains_no_outcome_claims():
    forbidden = [
        "will go " + "vir" + "al",
        "pred" + "icted views",
        "expected " + "views",
        "guaranteed " + "views",
        "views " + "pred" + "iction",
    ]
    source = (
        Path(__file__).resolve().parents[2] / "src" / "editorial_scoring.py"
    ).read_text(encoding="utf-8").lower()
    for phrase in forbidden:
        assert phrase not in source, phrase
