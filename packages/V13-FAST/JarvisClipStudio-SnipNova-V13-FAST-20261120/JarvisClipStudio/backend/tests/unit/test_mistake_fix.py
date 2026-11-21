"""Trend-Radar D16 + D25: the mistake_fix hook title style.

D16 adds ``mistake_fix`` as an AVAILABLE ``hook.title_style`` value
for finance_business and education_tutorial (never a default). D25
is structural for the style: the fix must land in the first third
of the clip, and the open-loop shape (mistake raised, fix held
back) is ineligible by construction.

The measured basis (Blossom, 14 Nov 2026) is correlation, not a
promise — no test here measures or claims any audience outcome.
What is pinned is the editorial shape: a title may only name a
mistake the segment itself raises, built from the segment's own
words, and the style declines — to the profile's own default, with
the reason recorded — whenever a moment cannot carry it honestly.
"""

import copy

from src.auto_director import hook_variants as hv
from src.auto_director.edl import PROFILES_DIR, load_profile
from src.auto_director.hook_title_styles import (
    HOOK_TITLE_STYLES,
    MISTAKE_FIX_PROFILES,
    MISTAKE_FIX_TITLE_STYLE,
    mistake_fix_available,
    opening_strategy_for_title_style,
    title_matches_style,
    title_style_guidance,
)
from src.auto_director.mistake_fix import (
    build_mistake_fix_title,
    find_mistake_fix,
    mistake_fix_eligibility,
    resolve_mistake_fix_style,
    sentences_from_words,
)
from src.auto_director.plan_service import _profile_summary

# --- Fixtures (clip-relative timed sentences) ---------------------------------

FINANCE_EARLY = [
    {"text": "Stop spending your whole salary in the first week of the month.", "start": 0.0, "end": 4.5},
    {"text": "Instead, move half of it into a separate account on payday.", "start": 4.5, "end": 9.0},
    {"text": "That one change means the money is still there when the month ends.", "start": 9.0, "end": 16.0},
    {"text": "People who do this save thirty percent more without earning extra.", "start": 16.0, "end": 24.0},
    {"text": "It works because the spending never gets the chance to happen.", "start": 24.0, "end": 30.0},
]
FINANCE_DURATION = 30.0

FINANCE_LATE = [
    {"text": "Stop spending your whole salary in the first week of the month.", "start": 0.0, "end": 4.5},
    {"text": "It feels harmless because the money has only just arrived.", "start": 4.5, "end": 10.0},
    {"text": "The rent and the bills all land in the final week of the month.", "start": 10.0, "end": 16.0},
    {"text": "By then the account is empty and the card takes the strain.", "start": 16.0, "end": 21.0},
    {"text": "Instead, move half of it into a separate account on payday.", "start": 21.0, "end": 25.5},
    {"text": "That one change means the money is still there at month end.", "start": 25.5, "end": 30.0},
]

NO_MISTAKE = [
    {"text": "That's pretty good avocado, wow.", "start": 0.0, "end": 5.0},
    {"text": "The avocado came from a 700-year-old tree.", "start": 5.0, "end": 11.0},
    {"text": "Four pieces of sushi cost four hundred dollars here.", "start": 11.0, "end": 19.0},
    {"text": "The sandwich has real gold in it.", "start": 19.0, "end": 25.0},
]

OPEN_LOOP = [
    {"text": "Your salary lands and the month feels full of promise.", "start": 0.0, "end": 6.0},
    {"text": "Then the spending starts and it never really slows down.", "start": 6.0, "end": 13.0},
    {"text": "Stop spending your whole salary in the first week of the month.", "start": 24.0, "end": 30.0},
]

EDU_NOUN = [
    {"text": "The biggest mistake is memorising instead of understanding.", "start": 0.0, "end": 5.0},
    {"text": "Instead, explain every topic out loud as if teaching it.", "start": 5.0, "end": 10.0},
    {"text": "Explaining forces your brain to organise what it knows.", "start": 10.0, "end": 17.0},
]

DOING_WRONG = [
    {"text": "You're saving your money wrong.", "start": 0.0, "end": 4.0},
    {"text": "Instead, pay yourself first on the day the salary lands.", "start": 4.0, "end": 9.0},
    {"text": "What is left after that is what you can safely spend.", "start": 9.0, "end": 15.0},
]

FIRST_PERSON = [
    {"text": "Stop making my rookie mistake with your savings.", "start": 0.0, "end": 4.0},
    {"text": "Instead, move half of it into a separate account on payday.", "start": 4.0, "end": 9.0},
]


# --- Registry + profiles ---------------------------------------------------------


def test_style_registered_additively():
    assert MISTAKE_FIX_TITLE_STYLE == "mistake_fix"
    assert "mistake_fix" in HOOK_TITLE_STYLES
    # Every pre-existing style is still registered, untouched.
    for style in (
        "statement", "question", "statistic", "contrast", "story",
        "problem", "curiosity_gap",
    ):
        assert style in HOOK_TITLE_STYLES
    guidance = title_style_guidance("mistake_fix")
    assert guidance
    assert "first third" in guidance  # the D25 rule is in the guidance
    assert opening_strategy_for_title_style("mistake_fix") == "strongest_statement"
    assert hv.strategy_for_title_style("mistake_fix") in hv.STRATEGY_KINDS


def test_profiles_offer_mistake_fix_without_changing_defaults():
    assert set(MISTAKE_FIX_PROFILES) == {"finance_business", "education_tutorial"}
    for niche in MISTAKE_FIX_PROFILES:
        profile = load_profile(niche)
        # Available, explicitly listed...
        assert "mistake_fix" in profile["hook"]["available_title_styles"]
        assert mistake_fix_available(profile)
        # ...but NOT the default: the shipped default is untouched.
        assert profile["hook"]["title_style"] == "problem"
    # No other stock profile offers it.
    for path in sorted(PROFILES_DIR.glob("*.json")):
        niche = path.stem
        if niche in MISTAKE_FIX_PROFILES:
            continue
        profile = load_profile(niche)
        assert "mistake_fix" not in (
            profile.get("hook", {}).get("available_title_styles") or []
        ), niche
        assert not mistake_fix_available(profile), niche


def test_availability_is_explicit_never_inferred():
    profile = copy.deepcopy(load_profile("finance_business"))
    profile["hook"]["available_title_styles"] = ["problem"]
    assert not mistake_fix_available(profile)
    # A profile with no list at all falls back to the recorded
    # assignment by niche name.
    legacy = {"niche": "finance_business", "hook": {"title_style": "problem"}}
    assert mistake_fix_available(legacy)
    assert not mistake_fix_available({"niche": "sports", "hook": {}})
    assert not mistake_fix_available(None)
    assert not mistake_fix_available({})


def test_profile_schema_accepts_mistake_fix_value():
    # A profile that selects the style (user-tuned or A/B variant)
    # gets its guidance in the planner summary, like any style.
    profile = copy.deepcopy(load_profile("finance_business"))
    profile["hook"]["title_style"] = "mistake_fix"
    summary = _profile_summary(profile)
    assert "mistake_fix" in summary
    assert "first third" in summary


def test_mistake_fix_marker_requires_the_mistake_in_the_words():
    assert title_matches_style("Stop Spending Your Whole Salary", "mistake_fix")
    assert title_matches_style("The Biggest Mistake: Memorising", "mistake_fix")
    assert title_matches_style("Saving Your Money Wrong", "mistake_fix")
    assert not title_matches_style("A Perfect Day In Kyoto", "mistake_fix")
    assert not title_matches_style("Save Thirty Percent More", "mistake_fix")
    assert not title_matches_style("", "mistake_fix")


# --- Detection + title generation -------------------------------------------------


def test_generation_declines_when_no_mistake_is_raised():
    assert find_mistake_fix(NO_MISTAKE) is None
    assert build_mistake_fix_title(NO_MISTAKE) is None
    verdict = mistake_fix_eligibility(NO_MISTAKE, 25.0)
    assert not verdict.eligible
    assert "no mistake" in verdict.reasons[0]


def test_generation_builds_grounded_title_from_source_words():
    title = build_mistake_fix_title(FINANCE_EARLY)
    assert title == "Stop spending your whole salary in the first week"
    # Grounding: every word of the title is the segment's own word.
    source_words = {
        tok.strip(".,;:!?").lower()
        for sentence in FINANCE_EARLY
        for tok in sentence["text"].split()
    }
    for tok in title.split():
        assert tok.strip(".,;:!?").lower() in source_words
    assert title_matches_style(title, "mistake_fix")


def test_generation_builds_named_mistake_title():
    title = build_mistake_fix_title(EDU_NOUN)
    assert title == "The biggest mistake: memorising instead of understanding"
    assert title_matches_style(title, "mistake_fix")


def test_generation_builds_doing_wrong_title():
    title = build_mistake_fix_title(DOING_WRONG)
    assert title == "Saving your money wrong"
    assert title_matches_style(title, "mistake_fix")


def test_first_person_never_survives_into_a_title():
    # The moment is real and eligible — but its warning clause is in
    # the speaker's first person, so no title may be built from it.
    assert find_mistake_fix(FIRST_PERSON) is not None
    assert build_mistake_fix_title(FIRST_PERSON) is None


def test_sentences_from_words_roundtrip():
    words = []
    for sentence in FINANCE_EARLY:
        toks = sentence["text"].split()
        span = (sentence["end"] - sentence["start"]) / len(toks)
        for i, tok in enumerate(toks):
            words.append(
                {
                    "word": tok,
                    "start": sentence["start"] + i * span,
                    "end": sentence["start"] + (i + 1) * span,
                }
            )
    sentences = sentences_from_words(words)
    finding = find_mistake_fix(sentences)
    assert finding is not None
    assert finding.fix_start is not None
    assert abs(finding.fix_start - 4.5) < 0.6
    assert build_mistake_fix_title(sentences) == (
        "Stop spending your whole salary in the first week"
    )


# --- Eligibility (D25) -------------------------------------------------------------


def test_eligibility_accepts_an_early_fix():
    verdict = mistake_fix_eligibility(FINANCE_EARLY, FINANCE_DURATION)
    assert verdict.eligible
    assert verdict.finding is not None
    assert verdict.finding.fix_start == 4.5
    assert verdict.finding.fix_explicit is True


def test_late_fix_is_ineligible_for_mistake_fix_only():
    verdict = mistake_fix_eligibility(FINANCE_LATE, FINANCE_DURATION)
    assert not verdict.eligible
    assert "first third" in verdict.reasons[0]
    profile = load_profile("finance_business")
    style, reason = resolve_mistake_fix_style(
        "mistake_fix", profile, FINANCE_LATE, FINANCE_DURATION
    )
    assert style == "problem"  # declined to the profile's own default
    assert reason and "first third" in reason
    # The same moment stays eligible for other styles: they pass
    # through the resolver untouched.
    style, reason = resolve_mistake_fix_style(
        "statistic", profile, FINANCE_LATE, FINANCE_DURATION
    )
    assert style == "statistic"
    assert reason is None


def test_open_loop_shape_is_structurally_ineligible():
    finding = find_mistake_fix(OPEN_LOOP)
    assert finding is not None
    assert finding.fix_start is None  # raised, never resolved in the moment
    verdict = mistake_fix_eligibility(OPEN_LOOP, FINANCE_DURATION)
    assert not verdict.eligible
    assert "open-loop" in verdict.reasons[0]


def test_resolve_grants_the_style_when_eligible_and_offered():
    profile = load_profile("finance_business")
    style, reason = resolve_mistake_fix_style(
        "mistake_fix", profile, FINANCE_EARLY, FINANCE_DURATION
    )
    assert style == "mistake_fix"
    assert reason is None


def test_resolve_declines_when_the_profile_does_not_offer_it():
    profile = load_profile("podcast_news")  # default: curiosity_gap
    style, reason = resolve_mistake_fix_style(
        "mistake_fix", profile, FINANCE_EARLY, FINANCE_DURATION
    )
    assert style == "curiosity_gap"
    assert reason and "not offered" in reason
