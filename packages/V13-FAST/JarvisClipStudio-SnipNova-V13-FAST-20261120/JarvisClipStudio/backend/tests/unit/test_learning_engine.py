"""Learning engine: aggregation rules, bounds, and the selection proof.

The acceptance core is the simulation: a seeded feedback history must
(a) move exactly the bounded weight the step table promises, for exactly
the niche the feedback was about; (b) flip a real selection ranking when
— and only when — the learned weights are applied; (c) roll back to the
exact pre-learning ranking; (d) change nothing below the evidence
thresholds. Everything here is deterministic: fixed events in, fixed
numbers out.
"""

import hashlib
import json
from pathlib import Path

from src.ai import TranscriptSegment, ViralityAnalysis, dedupe_overlapping_segments
from src.auto_director import learning
from src.auto_director.edl import PROFILES_DIR, build_edl, load_profile
from src.services.video_service import VideoService


def _event(niche, verdict, reason=None, source="explicit", features=None):
    return {
        "niche": niche,
        "verdict": verdict,
        "reason": reason,
        "source": source,
        "weight": learning.SOURCE_WEIGHTS[source],
        "features": features or {},
    }


def _rejects(niche, reason, count, **kwargs):
    return [_event(niche, "reject", reason, **kwargs) for _ in range(count)]


def _accepts(niche, count, **kwargs):
    return [_event(niche, "accept", None, **kwargs) for _ in range(count)]


SEEDED_EVENTS = _rejects("meme_comedy", "hook_weak", 8) + _accepts("podcast_news", 6)


# ---------------------------------------------------------------------------
# (a) Aggregation: bounded deltas, right niche only
# ---------------------------------------------------------------------------


def test_seeded_history_moves_only_the_promised_weight():
    adjustments = learning.compute_adjustments(SEEDED_EVENTS)

    # 8 weighted hook_weak rejects land in the >=6 step: hook weight 1.15.
    assert adjustments["meme_comedy"] == {"score_weights": {"hook_score": 1.15}}
    # Six accepts with no complaints earn podcast_news no adjustment at all.
    assert "podcast_news" not in adjustments


def test_weight_steps_and_bounds():
    events = _rejects("gaming", "hook_weak", 3)
    assert learning.compute_adjustments(events)["gaming"]["score_weights"] == {
        "hook_score": 1.10
    }

    events = _rejects("gaming", "hook_weak", 25)
    weights = learning.compute_adjustments(events)["gaming"]["score_weights"]
    assert weights["hook_score"] == 1.20  # hard ceiling, never more
    assert all(0.80 <= value <= 1.20 for value in weights.values())


def test_uncited_subscores_step_down_only_with_accepts_present():
    # The user rejects a lot in this niche but never for pacing, and they
    # do accept some clips: engagement is relatively less decisive for them.
    events = _rejects("gaming", "hook_weak", 8) + _accepts("gaming", 3)
    weights = learning.compute_adjustments(events)["gaming"]["score_weights"]
    assert weights["hook_score"] == 1.15
    assert weights["engagement_score"] == 0.90
    assert weights["value_score"] == 0.90
    assert weights["shareability_score"] == 0.90

    # With no accepts at all there is no contrast to learn from: no step-down.
    events = _rejects("gaming", "hook_weak", 8)
    weights = learning.compute_adjustments(events)["gaming"]["score_weights"]
    assert weights == {"hook_score": 1.15}


def test_sfx_density_rules():
    events = _rejects("meme_comedy", "sfx", 3)
    assert learning.compute_adjustments(events)["meme_comedy"][
        "sfx_density_multiplier"
    ] == 0.75

    events = _rejects("meme_comedy", "sfx", 6)
    assert learning.compute_adjustments(events)["meme_comedy"][
        "sfx_density_multiplier"
    ] == 0.50

    # Passive (half-weight) signals count, but need twice the pile.
    events = _rejects("meme_comedy", "sfx", 6, source="passive_delete")
    assert learning.compute_adjustments(events)["meme_comedy"][
        "sfx_density_multiplier"
    ] == 0.75

    events = _accepts("meme_comedy", 8)
    assert learning.compute_adjustments(events)["meme_comedy"][
        "sfx_density_multiplier"
    ] == 1.25


def test_music_needs_five_consistent_signals():
    events = _rejects("travel_vlog", "music", 4)
    assert "travel_vlog" not in learning.compute_adjustments(events)

    events = _rejects("travel_vlog", "music", 5)
    assert learning.compute_adjustments(events)["travel_vlog"][
        "music_volume_multiplier"
    ] == 0.0

    audible = {"music_audible": True}
    events = [_event("travel_vlog", "accept", features=audible) for _ in range(5)]
    assert learning.compute_adjustments(events)["travel_vlog"][
        "music_volume_multiplier"
    ] == 1.25


def test_caption_affinity_crosses_niches_only_on_complaints():
    # The user keeps accepting clips captioned "hormozi" (podcast_news's
    # preset) and keeps rejecting meme_comedy clips over captions.
    events = [
        _event(
            "podcast_news",
            "accept",
            features={"caption_preset_id": "hormozi"},
        )
        for _ in range(3)
    ] + _rejects("meme_comedy", "captions", 2)
    adjustments = learning.compute_adjustments(
        events, {"meme_comedy": "large_centre_pop", "podcast_news": "hormozi"}
    )
    assert adjustments["meme_comedy"]["preferred_caption_preset"] == "hormozi"
    assert "podcast_news" not in adjustments

    # No caption complaints in the niche -> its own preset stays.
    events = [
        _event(
            "podcast_news",
            "accept",
            features={"caption_preset_id": "hormozi"},
        )
        for _ in range(4)
    ]
    assert "meme_comedy" not in learning.compute_adjustments(
        events, {"meme_comedy": "large_centre_pop"}
    )


# ---------------------------------------------------------------------------
# (d) Below threshold: nothing changes
# ---------------------------------------------------------------------------


def test_below_threshold_feedback_changes_nothing():
    events = _rejects("meme_comedy", "hook_weak", 2)
    assert learning.compute_adjustments(events) == {}
    assert learning.score_weights_for({"niches": {}}, "meme_comedy") is None


def test_events_without_a_niche_teach_nothing():
    events = [_event(None, "reject", "hook_weak") for _ in range(10)]
    assert learning.compute_adjustments(events) == {}


# ---------------------------------------------------------------------------
# (b) + (c) Selection: learning flips a ranking; rollback restores it
# ---------------------------------------------------------------------------


def _segment(start, end, *, hook, engagement, value, shareability, relevance):
    total = hook + engagement + value + shareability
    return TranscriptSegment(
        start_time=start,
        end_time=end,
        text=f"segment {start}",
        relevance_score=relevance,
        virality=ViralityAnalysis(
            hook_score=hook,
            engagement_score=engagement,
            value_score=value,
            shareability_score=shareability,
            total_score=total,
        ),
    )


def _candidates():
    # Same plain total (72). B wins unlearned ranking on relevance; A is
    # the hook-heavy candidate the learned weight should promote.
    high_hook = _segment(
        "00:00", "00:30", hook=24, engagement=16, value=16, shareability=16,
        relevance=0.5,
    )
    balanced = _segment(
        "01:00", "01:30", hook=12, engagement=20, value=20, shareability=20,
        relevance=0.9,
    )
    return [balanced, high_hook]


def _order(segments):
    return [segment.start_time for segment in segments]


def test_selection_flips_with_learning_and_rollback_restores_it():
    document = learning.new_document(
        version=1,
        event_count=len(SEEDED_EVENTS),
        adjustments=learning.compute_adjustments(SEEDED_EVENTS),
        summary=["seeded"],
    )
    weights = learning.score_weights_for(document, "meme_comedy")
    assert weights == {
        "hook_score": 1.15,
        "engagement_score": 1.0,
        "value_score": 1.0,
        "shareability_score": 1.0,
    }

    off = _order(dedupe_overlapping_segments(_candidates()))
    on = _order(dedupe_overlapping_segments(_candidates(), score_weights=weights))
    assert off == ["01:00", "00:00"]  # relevance breaks the total tie
    assert on == ["00:00", "01:00"]  # hook 24 x 1.15 outranks the tie

    # Rollback: a new version carrying the pre-learning (empty)
    # adjustments restores the OFF ranking exactly.
    base = learning.new_document(
        version=1, event_count=0, adjustments={}, summary=["base"]
    )
    learned = learning.new_document(
        version=2,
        event_count=len(SEEDED_EVENTS),
        adjustments=learning.compute_adjustments(SEEDED_EVENTS),
        summary=["learned"],
    )
    rolled = learning.rollback_document(
        [base, learned], 1, event_count=len(SEEDED_EVENTS)
    )
    assert rolled["version"] == 3
    assert rolled["niches"] == {}
    assert learning.score_weights_for(rolled, "meme_comedy") is None
    restored = _order(dedupe_overlapping_segments(_candidates()))
    assert restored == off


def test_rollback_to_unknown_version_is_an_error():
    document = learning.new_document(
        version=1, event_count=0, adjustments={}, summary=[]
    )
    try:
        learning.rollback_document([document], 99, event_count=0)
    except ValueError as exc:
        assert "99" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("rollback to a missing version must fail")


# ---------------------------------------------------------------------------
# Profile application: copies only, shipped profiles untouched
# ---------------------------------------------------------------------------


def _profile_hashes():
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(PROFILES_DIR.glob("*.json"))
    }


def test_apply_profile_adjustments_never_mutates_the_input():
    before = _profile_hashes()
    profile = load_profile("travel_vlog")
    snapshot = json.loads(json.dumps(profile))

    adjusted = learning.apply_profile_adjustments(
        profile,
        {
            "sfx_density_multiplier": 0.5,
            "music_volume_multiplier": 0.0,
            "preferred_caption_preset": "hormozi",
        },
    )

    assert profile == snapshot  # input untouched
    assert adjusted["audio"]["music_volume_0_to_1"] == 0.0
    assert adjusted["audio"]["sfx_density_multiplier"] == 0.5
    assert adjusted["captions"]["preset_id"] == "hormozi"
    assert _profile_hashes() == before  # shipped profiles byte-identical

    up = learning.apply_profile_adjustments(
        load_profile("travel_vlog"), {"music_volume_multiplier": 1.25}
    )
    assert up["audio"]["music_volume_0_to_1"] == 0.3125  # 0.25 x 1.25


# ---------------------------------------------------------------------------
# EDL seam: adjustments land in the built EDL, guarded by niche
# ---------------------------------------------------------------------------


def _segment_dict():
    return {"start_time": "00:00", "end_time": "00:30", "text": "a calm walk"}


def _words():
    return [
        {"text": "welcome", "start": 0.0, "end": 0.4},
        {"text": "back", "start": 0.4, "end": 0.7},
    ]


def test_pipeline_edl_applies_music_adjustment_only_to_its_niche():
    base = VideoService.build_pipeline_edl(
        _segment_dict(), _words(), niche_override="travel_vlog"
    )
    assert base["audio"]["music_volume"] == 0.25

    silenced = VideoService.build_pipeline_edl(
        _segment_dict(),
        _words(),
        niche_override="travel_vlog",
        profile_adjustments={"music_volume_multiplier": 0.0},
        profile_adjustments_niche="travel_vlog",
    )
    assert silenced["audio"]["music_volume"] == 0.0

    wrong_niche = VideoService.build_pipeline_edl(
        _segment_dict(),
        _words(),
        niche_override="travel_vlog",
        profile_adjustments={"music_volume_multiplier": 0.0},
        profile_adjustments_niche="meme_comedy",
    )
    assert wrong_niche["audio"]["music_volume"] == 0.25


def test_sfx_density_multiplier_scales_peak_caps():
    profile = load_profile("meme_comedy")  # sfx_from_user_library = True
    clip = {"id": "c1", "start": 0.0, "end": 90.0, "duration": 90.0}
    peaks = [float(t) for t in range(2, 90, 3)]  # 29 peaks, 3s apart

    full = build_edl(clip, [], profile, peaks)
    peak_events = [
        e for e in full["sfx_events"] if e["name"] in ("impact", "pop")
    ]
    assert len(peak_events) == 8  # shipped cap

    halved_profile = learning.apply_profile_adjustments(
        profile, {"sfx_density_multiplier": 0.5}
    )
    halved = build_edl(clip, [], halved_profile, peaks)
    peak_events = [
        e for e in halved["sfx_events"] if e["name"] in ("impact", "pop")
    ]
    assert len(peak_events) == 4

    # No multiplier stamped -> identical events to the shipped profile.
    plain = build_edl(clip, [], load_profile("meme_comedy"), peaks)
    assert plain["sfx_events"] == full["sfx_events"]
