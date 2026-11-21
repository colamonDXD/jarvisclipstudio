"""Classifier -> learning seam (integration phase 2a).

Lane I's selection weights originally applied only to niche-PINNED tasks:
the weights were computed in task_service before the pipeline ran, when
an auto task's niche does not exist yet. The seam now lives in
``VideoService.analyze_transcript`` — the first point where a resolved
(classified) niche honestly exists — fed by the learning document that
task_service already resolves. These tests pin the contract:

* auto task + confidently classified niche + seeded feedback -> the
  niche's learned weights reach the analysis (and bend ranking);
* fallback classification (no confident niche) -> base behaviour;
* a classified niche with no qualifying adjustment -> base behaviour;
* precomputed (pinned) weights always win and classification is not
  consulted at all;
* no learning document -> no classification, base behaviour.
"""

from types import SimpleNamespace

import pytest

import src.auto_director.classify as classify_module
import src.services.video_service as video_service_module
from src.ai import TranscriptSegment, ViralityAnalysis, dedupe_overlapping_segments
from src.auto_director.learning import score_weights_for
from src.services.video_service import VideoService

DOCUMENT = {
    "version": 3,
    "niches": {
        "gaming": {"score_weights": {"hook_score": 1.15}},
        # Present so the fallback test proves the GATE, not an absence:
        # fallback classification must not apply even these weights.
        "podcast_news": {"score_weights": {"hook_score": 1.20}},
    },
}


@pytest.fixture
def captured(monkeypatch):
    """Capture the kwargs analyze_transcript forwards to the analysis."""
    box = {}

    async def fake_analysis(transcript, **kwargs):
        box.update(kwargs)
        return SimpleNamespace(most_relevant_segments=[])

    monkeypatch.setattr(
        video_service_module, "get_most_relevant_parts_by_transcript", fake_analysis
    )
    return box


def stub_classify(monkeypatch, result):
    calls = []

    async def fake_classify(sample, entities, **kwargs):
        calls.append((sample, entities))
        return result

    monkeypatch.setattr(classify_module, "classify_niche", fake_classify)
    return calls


@pytest.mark.asyncio
async def test_auto_task_resolved_niche_gets_adjusted_weights(monkeypatch, captured):
    calls = stub_classify(
        monkeypatch,
        {"niche": "gaming", "confidence": 0.9, "reasons": ["ranked shooter talk"]},
    )
    await VideoService.analyze_transcript(
        "a long transcript about ranked shooters", learning_document=DOCUMENT
    )
    expected = score_weights_for(DOCUMENT, "gaming")
    assert expected is not None and expected["hook_score"] == 1.15
    assert captured["score_weights"] == expected
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_fallback_classification_keeps_base_weights(monkeypatch, captured):
    stub_classify(
        monkeypatch,
        {"niche": "podcast_news", "confidence": 0.0, "reasons": ["fallback"]},
    )
    await VideoService.analyze_transcript(
        "unclassifiable transcript", learning_document=DOCUMENT
    )
    assert captured["score_weights"] is None


@pytest.mark.asyncio
async def test_classified_niche_without_adjustment_stays_base(monkeypatch, captured):
    stub_classify(
        monkeypatch,
        {"niche": "gaming", "confidence": 0.9, "reasons": ["ranked shooter talk"]},
    )
    await VideoService.analyze_transcript(
        "a long transcript about ranked shooters",
        learning_document={"version": 1, "niches": {}},
    )
    assert captured["score_weights"] is None


@pytest.mark.asyncio
async def test_pinned_weights_win_and_classifier_not_consulted(monkeypatch, captured):
    calls = stub_classify(
        monkeypatch,
        {"niche": "gaming", "confidence": 0.9, "reasons": ["ranked shooter talk"]},
    )
    pinned = {
        "hook_score": 1.0,
        "engagement_score": 1.0,
        "value_score": 1.0,
        "shareability_score": 1.0,
    }
    await VideoService.analyze_transcript(
        "a long transcript about ranked shooters",
        score_weights=pinned,
        learning_document=DOCUMENT,
    )
    assert captured["score_weights"] == pinned
    assert calls == []


@pytest.mark.asyncio
async def test_no_document_means_no_classification(monkeypatch, captured):
    calls = stub_classify(
        monkeypatch,
        {"niche": "gaming", "confidence": 0.9, "reasons": ["ranked shooter talk"]},
    )
    await VideoService.analyze_transcript("a long transcript about ranked shooters")
    assert captured["score_weights"] is None
    assert calls == []


def _segment(start, end, hook, engagement, relevance=10.0):
    return TranscriptSegment(
        start_time=start,
        end_time=end,
        text="segment",
        relevance_score=relevance,
        reasoning="test",
        virality=ViralityAnalysis(
            hook_score=hook,
            engagement_score=engagement,
            value_score=0,
            shareability_score=0,
            total_score=hook + engagement,
        ),
    )


def test_resolved_weights_bend_ranking():
    engagement_led = _segment("00:00", "00:10", hook=0, engagement=25)
    hook_led = _segment("00:20", "00:30", hook=24, engagement=0)
    base = dedupe_overlapping_segments([hook_led, engagement_led])
    assert base[0] is engagement_led  # plain totals: 25 beats 24

    weights = score_weights_for(DOCUMENT, "gaming")
    adjusted = dedupe_overlapping_segments(
        [engagement_led, hook_led], score_weights=weights
    )
    assert adjusted[0] is hook_led  # hook x1.15: 27.6 beats 25
