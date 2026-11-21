"""Trend-Radar D1 candidate: problem / curiosity_gap hook title styles.

Additive registry + guidance. The candidate assignment was applied to
the shipped profiles on the 2026-11-09 A/B verdict (adopt): candidate
titles stayed honest on all five regression ranges while the shipped
titles spent their reveals in the title itself. The verdict is
editorial only (honesty, style fidelity, word counts) — no test here
measures or claims any audience outcome.
"""

from src.auto_director import hook_variants as hv
from src.auto_director.edl import load_profile
from src.auto_director.hook_title_styles import (
    PROPOSED_CANDIDATE_DEFAULTS,
    CANDIDATE_TITLE_STYLES,
    HOOK_TITLE_STYLES,
    opening_strategy_for_title_style,
    title_matches_style,
    title_style_guidance,
)
from src.auto_director.plan_service import _profile_summary


def test_candidate_styles_are_registered_additively():
    assert set(CANDIDATE_TITLE_STYLES) == {"problem", "curiosity_gap"}
    # Every pre-existing style is still registered, untouched.
    for style in ("statement", "question", "statistic", "contrast", "story"):
        assert style in HOOK_TITLE_STYLES
    assert set(HOOK_TITLE_STYLES) == {
        "statement", "question", "statistic", "contrast", "story",
        "problem", "curiosity_gap",
        # Trend-Radar D16 (2026-11-16): registered additively; the
        # style's own tests live in test_mistake_fix.py.
        "mistake_fix",
        # Trend-Radar D15 (2026-11-16): registered additively; the
        # style's own tests live in test_list_fill.py.
        "list_fill",
        # Trend-Radar D17 (2026-11-16): registered additively; the
        # style's own tests live in test_trailer_breakdown.py.
        "trailer_breakdown",
    }


def test_candidate_defaults_are_shipped_after_d1_adoption():
    # The D1 A/B verdict (2026-11-09) adopted the candidate assignment,
    # so the shipped profiles now carry exactly the candidate styles.
    # This pin exists so the defaults can never drift silently — any
    # future change must update the map and this test together.
    for niche, style in PROPOSED_CANDIDATE_DEFAULTS.items():
        assert style in CANDIDATE_TITLE_STYLES, niche
        assert load_profile(niche)["hook"]["title_style"] == style, niche
    # Profiles outside the candidate set keep their shipped defaults.
    assert load_profile("sports")["hook"]["title_style"] == "statistic"
    assert load_profile("meme_comedy")["hook"]["title_style"] == "contrast"
    assert load_profile("travel_vlog")["hook"]["title_style"] == "question"


def test_guidance_exists_for_candidates_and_reaches_the_planner_summary():
    for style in CANDIDATE_TITLE_STYLES:
        guidance = title_style_guidance(style)
        assert guidance
        assert "segment" in guidance  # grounded in the segment, always
    # A profile that selects a candidate style (user-tuned or
    # reference-derived) gets its guidance in the planner summary.
    import copy

    profile = copy.deepcopy(load_profile("podcast_news"))
    profile["hook"]["title_style"] = "curiosity_gap"
    summary = _profile_summary(profile)
    assert "curiosity_gap" in summary
    assert "withhold" in summary
    profile = copy.deepcopy(load_profile("finance_business"))
    profile["hook"]["title_style"] = "problem"
    summary = _profile_summary(profile)
    assert "problem" in summary


def test_problem_marker_requires_a_problem_in_the_words():
    assert title_matches_style("The $400 Mistake Everyone Makes", "problem")
    assert title_matches_style("Why Your Rent Is Too Expensive", "problem")
    assert not title_matches_style("A Perfect Day In Kyoto", "problem")
    assert not title_matches_style("", "problem")


def test_curiosity_gap_marker_requires_a_gap_and_a_subject():
    assert title_matches_style("The Secret Inside This $400 Sandwich", "curiosity_gap")
    assert title_matches_style("Why Nobody Orders The Gold Sandwich Twice", "curiosity_gap")
    # A plain statement names no gap.
    assert not title_matches_style("This Sandwich Costs $400 In Tokyo", "curiosity_gap")
    # A gap with no subject named is not a curiosity_gap title.
    assert not title_matches_style("Why This One Thing?", "curiosity_gap")


def test_title_style_maps_to_an_opening_strategy():
    assert opening_strategy_for_title_style("problem") == "strongest_statement"
    assert opening_strategy_for_title_style("curiosity_gap") == "question"
    assert hv.strategy_for_title_style("problem") in hv.STRATEGY_KINDS
    assert hv.strategy_for_title_style("curiosity_gap") in hv.STRATEGY_KINDS
    assert opening_strategy_for_title_style("nonsense") is None
