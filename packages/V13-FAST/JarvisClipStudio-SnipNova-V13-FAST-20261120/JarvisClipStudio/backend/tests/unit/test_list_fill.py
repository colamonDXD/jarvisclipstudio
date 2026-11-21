"""Trend-Radar D15: the list_fill hook title style + live list overlay.

D15 adds ``list_fill`` as an AVAILABLE ``hook.title_style`` value
for finance_business (never a default) and the
``selection_templates: ["list_fill_live"]`` declaration on that
profile. The format is the week's strongest measured trend-radar
finding (Volume, 15 Nov 2026: 9 accounts, median 16x their usual
views; top example 69x) — an observation of OTHER creators'
videos, not a promise. No test here measures or claims any audience
outcome. What is pinned is the editorial shape: a list may only be
built from items the speaker actually enumerates in the moment, the
title only from the announcement's own words, and the style declines
— to the profile's own default, with the reason recorded — whenever
a moment cannot carry it honestly: no list announced, a partial
list, a skipped ordinal, or a list that never completes inside the
clip.
"""

import copy
from pathlib import Path

from src.auto_director import hook_variants as hv
from src.auto_director.edl import PROFILES_DIR, load_profile
from src.auto_director.hook_title_styles import (
    HOOK_TITLE_STYLES,
    LIST_FILL_PROFILES,
    LIST_FILL_TITLE_STYLE,
    list_fill_available,
    opening_strategy_for_title_style,
    title_matches_style,
    title_style_guidance,
)
from src.auto_director.list_fill import (
    LIST_FILL_TEMPLATE,
    append_list_fill_overlay,
    build_list_fill_plan,
    build_list_fill_title,
    find_enumeration,
    list_fill_ass_events,
    list_fill_eligibility,
    list_fill_template_available,
    resolve_list_fill_style,
    sentences_from_words,
)
from src.auto_director.plan_service import _profile_summary

# --- Fixtures (clip-relative timed sentences) ---------------------------------

LIST_FILL = [
    {"text": "Here are three money habits for a rich life.", "start": 0.0, "end": 3.0},
    {"text": "First, automate your savings on payday.", "start": 3.0, "end": 8.0},
    {"text": "Second, keep one month of spending in a separate account.", "start": 8.0, "end": 14.0},
    {"text": "Third, invest the rest before you can spend it.", "start": 14.0, "end": 20.0},
    {"text": "Three habits, and the money is still there at month end.", "start": 20.0, "end": 30.0},
]
LIST_FILL_DURATION = 30.0

AGE_TABLE = [
    {"text": "Here is the top 10 percent income by age.", "start": 0.0, "end": 3.0},
    {"text": "By thirty, have one year of salary saved.", "start": 3.0, "end": 8.0},
    {"text": "By forty, have three years of salary saved.", "start": 8.0, "end": 14.0},
    {"text": "By fifty, have six years of salary saved.", "start": 14.0, "end": 20.0},
    {"text": "That is the whole ladder, and it starts at thirty.", "start": 20.0, "end": 30.0},
]

NO_LIST = [
    {"text": "That's pretty good avocado, wow.", "start": 0.0, "end": 5.0},
    {"text": "The avocado came from a 700-year-old tree.", "start": 5.0, "end": 11.0},
    {"text": "Four pieces of sushi cost four hundred dollars here.", "start": 11.0, "end": 19.0},
    {"text": "The sandwich has real gold in it.", "start": 19.0, "end": 25.0},
]

PARTIAL = [
    {"text": "Here are three money habits for a rich life.", "start": 0.0, "end": 3.0},
    {"text": "First, automate your savings on payday.", "start": 3.0, "end": 8.0},
    {"text": "Second, keep one month of spending in a separate account.", "start": 8.0, "end": 14.0},
    {"text": "The third one is the one everyone forgets, and I will tell you next week.", "start": 14.0, "end": 30.0},
]

SKIPPED = [
    {"text": "Here are three money habits for a rich life.", "start": 0.0, "end": 3.0},
    {"text": "First, automate your savings on payday.", "start": 3.0, "end": 8.0},
    {"text": "Third, invest the rest before you can spend it.", "start": 8.0, "end": 14.0},
]

FIRST_PERSON = [
    {"text": "Here are my three money habits for a rich life.", "start": 0.0, "end": 3.0},
    {"text": "First, automate your savings on payday.", "start": 3.0, "end": 8.0},
    {"text": "Second, keep one month of spending in a separate account.", "start": 8.0, "end": 14.0},
    {"text": "Third, invest the rest before you can spend it.", "start": 14.0, "end": 20.0},
]


# --- Registry + profiles ---------------------------------------------------------


def test_style_registered_additively():
    assert LIST_FILL_TITLE_STYLE == "list_fill"
    assert "list_fill" in HOOK_TITLE_STYLES
    # Every pre-existing style is still registered, untouched.
    for style in (
        "statement", "question", "statistic", "contrast", "story",
        "problem", "curiosity_gap",
    ):
        assert style in HOOK_TITLE_STYLES
    guidance = title_style_guidance("list_fill")
    assert guidance
    assert "complete" in guidance  # the list must complete on screen
    assert opening_strategy_for_title_style("list_fill") == "strongest_statement"
    assert hv.strategy_for_title_style("list_fill") in hv.STRATEGY_KINDS


def test_profiles_offer_list_fill_without_changing_defaults():
    assert set(LIST_FILL_PROFILES) == {"finance_business"}
    for niche in LIST_FILL_PROFILES:
        profile = load_profile(niche)
        # Available, explicitly listed...
        assert "list_fill" in profile["hook"]["available_title_styles"]
        assert list_fill_available(profile)
        # ...but NOT the default: the shipped default is untouched.
        assert profile["hook"]["title_style"] == "problem"
        # The selection template is declared on the profile.
        assert LIST_FILL_TEMPLATE in profile["selection_templates"]
        assert list_fill_template_available(profile)
    # No other stock profile offers it.
    for path in sorted(PROFILES_DIR.glob("*.json")):
        niche = path.stem
        if niche in LIST_FILL_PROFILES:
            continue
        profile = load_profile(niche)
        assert "list_fill" not in (
            profile.get("hook", {}).get("available_title_styles") or []
        ), niche
        assert not list_fill_available(profile), niche
        assert LIST_FILL_TEMPLATE not in (
            profile.get("selection_templates") or []
        ), niche
        assert not list_fill_template_available(profile), niche


def test_availability_is_explicit_never_inferred():
    profile = copy.deepcopy(load_profile("finance_business"))
    profile["hook"]["available_title_styles"] = ["problem"]
    assert not list_fill_available(profile)
    profile["selection_templates"] = []
    assert not list_fill_template_available(profile)
    # A profile with no lists at all falls back to the recorded
    # assignment by niche name.
    legacy = {"niche": "finance_business", "hook": {"title_style": "problem"}}
    assert list_fill_available(legacy)
    assert list_fill_template_available(legacy)
    assert not list_fill_available({"niche": "sports", "hook": {}})
    assert not list_fill_template_available({"niche": "sports"})
    assert not list_fill_available(None)
    assert not list_fill_template_available(None)
    assert not list_fill_available({})
    assert not list_fill_template_available({})


def test_profile_schema_accepts_list_fill_value():
    # A profile that selects the style (user-tuned or A/B variant)
    # gets its guidance in the planner summary, like any style.
    profile = copy.deepcopy(load_profile("finance_business"))
    profile["hook"]["title_style"] = "list_fill"
    summary = _profile_summary(profile)
    assert "list_fill" in summary
    assert "complete" in summary


def test_list_fill_marker_requires_count_and_outcome():
    assert title_matches_style("Three Money Habits for a Rich Life", "list_fill")
    assert title_matches_style("Top 10 Percent Income by Age", "list_fill")
    assert title_matches_style("5 Rules to Retire Early", "list_fill")
    assert not title_matches_style("Money Habits for a Rich Life", "list_fill")
    assert not title_matches_style("Three Money Habits", "list_fill")
    assert not title_matches_style("A Perfect Day In Kyoto", "list_fill")
    assert not title_matches_style("", "list_fill")


# --- Extraction + title generation -------------------------------------------------


def test_extraction_finds_ordinal_enumeration_with_reveal_times():
    found = find_enumeration(LIST_FILL)
    assert found is not None
    assert found.kind == "ordinal"
    assert found.announced_count == 3
    assert [item.index for item in found.items] == [1, 2, 3]
    assert found.items[0].text == "Automate your savings on payday"
    assert found.items[1].text == "Keep one month of spending in a separate account"
    assert found.items[2].text == "Invest the rest before you can spend it"
    # Reveal timing tracks the transcript: each item appears when the
    # speaker reaches it.
    assert [item.reveal_start for item in found.items] == [3.0, 8.0, 14.0]
    assert found.items[-1].reveal_end == 20.0


def test_generation_builds_grounded_title_from_announcement_words():
    title = build_list_fill_title(LIST_FILL)
    assert title == "Three money habits for a rich life"
    # Grounding: every word of the title is the announcement's own word.
    source_words = {
        tok.strip(".,;:!?").lower()
        for tok in LIST_FILL[0]["text"].split()
    }
    for tok in title.split():
        assert tok.strip(".,;:!?").lower() in source_words
    assert title_matches_style(title, "list_fill")


def test_extraction_finds_age_table_with_ascending_rows():
    found = find_enumeration(AGE_TABLE)
    assert found is not None
    assert found.kind == "age_table"
    assert found.announced_count is None  # a percentile, not a count
    assert [item.index for item in found.items] == [1, 2, 3]
    assert found.items[0].text == "By thirty: have one year of salary saved"
    assert found.items[1].text == "By forty: have three years of salary saved"
    assert found.items[2].text == "By fifty: have six years of salary saved"
    assert [item.reveal_start for item in found.items] == [3.0, 8.0, 14.0]
    title = build_list_fill_title(AGE_TABLE)
    assert title == "The top 10 percent income by age"
    assert title_matches_style(title, "list_fill")


def test_generation_declines_when_no_list_is_enumerated():
    assert find_enumeration(NO_LIST) is None
    assert build_list_fill_title(NO_LIST) is None
    verdict = list_fill_eligibility(NO_LIST, 25.0)
    assert not verdict.eligible
    assert "no listable" in verdict.reasons[0]


def test_generation_declines_a_partial_list():
    found = find_enumeration(PARTIAL)
    assert found is not None
    assert len(found.items) == 2  # the third item is promised, never given
    assert build_list_fill_title(PARTIAL) == "Three money habits for a rich life"
    verdict = list_fill_eligibility(PARTIAL, LIST_FILL_DURATION)
    assert not verdict.eligible
    assert "announces 3" in verdict.reasons[0]
    assert "only 2" in verdict.reasons[0]


def test_generation_declines_a_skipped_ordinal():
    # "First ... Third ..." with no "Second": an item the camera (or
    # the transcript) missed, so the enumeration is broken, not partial.
    assert find_enumeration(SKIPPED) is None
    assert build_list_fill_title(SKIPPED) is None
    verdict = list_fill_eligibility(SKIPPED, LIST_FILL_DURATION)
    assert not verdict.eligible


def test_first_person_never_survives_into_a_title():
    # The moment is real and eligible — but its announcement is in
    # the speaker's first person, so no title may be built from it.
    assert find_enumeration(FIRST_PERSON) is not None
    assert build_list_fill_title(FIRST_PERSON) is None
    verdict = list_fill_eligibility(FIRST_PERSON, LIST_FILL_DURATION)
    assert not verdict.eligible


def test_sentences_from_words_roundtrip():
    words = []
    for sentence in LIST_FILL:
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
    found = find_enumeration(sentences)
    assert found is not None
    assert found.announced_count == 3
    assert abs(found.items[0].reveal_start - 3.0) < 0.6
    assert abs(found.items[2].reveal_start - 14.0) < 0.6
    assert build_list_fill_title(sentences) == "Three money habits for a rich life"


# --- Eligibility ---------------------------------------------------------------------


def test_eligibility_accepts_a_completed_list():
    verdict = list_fill_eligibility(LIST_FILL, LIST_FILL_DURATION)
    assert verdict.eligible
    assert verdict.plan is not None
    assert verdict.plan.title == "Three money habits for a rich life"
    assert verdict.plan.kind == "ordinal"
    assert len(verdict.plan.items) == 3
    assert verdict.plan.clip_duration == LIST_FILL_DURATION


def test_eligibility_accepts_a_completed_age_table():
    verdict = list_fill_eligibility(AGE_TABLE, LIST_FILL_DURATION)
    assert verdict.eligible
    assert verdict.plan is not None
    assert verdict.plan.title == "The top 10 percent income by age"
    assert verdict.plan.kind == "age_table"


def test_list_running_past_the_clip_end_is_ineligible():
    # The same moment cut at 18 s: the third item ends at 20 s, so
    # the list never completes on screen.
    verdict = list_fill_eligibility(LIST_FILL, 18.0)
    assert not verdict.eligible
    assert "never completes" in verdict.reasons[0]
    assert build_list_fill_plan(LIST_FILL, 18.0) is None


def test_resolve_grants_the_style_when_eligible_and_offered():
    profile = load_profile("finance_business")
    style, reason = resolve_list_fill_style(
        "list_fill", profile, LIST_FILL, LIST_FILL_DURATION
    )
    assert style == "list_fill"
    assert reason is None


def test_resolve_declines_to_the_default_with_the_reason():
    profile = load_profile("finance_business")
    style, reason = resolve_list_fill_style(
        "list_fill", profile, PARTIAL, LIST_FILL_DURATION
    )
    assert style == "problem"  # declined to the profile's own default
    assert reason and "announces 3" in reason
    # The same moment stays eligible for other styles: they pass
    # through the resolver untouched.
    style, reason = resolve_list_fill_style(
        "statistic", profile, PARTIAL, LIST_FILL_DURATION
    )
    assert style == "statistic"
    assert reason is None


def test_resolve_declines_when_the_profile_does_not_offer_it():
    profile = load_profile("podcast_news")  # default: curiosity_gap
    style, reason = resolve_list_fill_style(
        "list_fill", profile, LIST_FILL, LIST_FILL_DURATION
    )
    assert style == "curiosity_gap"
    assert reason and "not offered" in reason


# --- Render: the timed ASS overlay ---------------------------------------------------


def test_ass_events_reveal_progressively_from_the_transcript():
    plan = build_list_fill_plan(LIST_FILL, LIST_FILL_DURATION)
    assert plan is not None
    style_line, events = list_fill_ass_events(plan, 540, 960, "Montserrat")
    assert style_line.startswith("Style: ListFill,Montserrat,")
    # 1 heading + 3 slots + 3 items.
    assert len(events) == 7
    assert all(e.startswith("Dialogue:") for e in events)
    heading = events[0]
    assert "0:00:00.00,0:00:30.00" in heading
    assert "Three money habits for a rich life" in heading
    # Slots run from second 0 until the speaker reaches the item;
    # items run from their reveal time to the end of the clip.
    slot_lines = [e for e in events if ". —" in e]
    item_lines = [e for e in events if "Automate" in e or "Keep one month" in e or "Invest the rest" in e]
    assert len(slot_lines) == 3
    assert len(item_lines) == 3
    assert "0:00:00.00,0:00:03.00" in slot_lines[0]
    assert "0:00:03.00,0:00:30.00" in item_lines[0]
    assert "0:00:08.00,0:00:30.00" in item_lines[1]
    assert "0:00:14.00,0:00:30.00" in item_lines[2]
    # Rows never run off the frame: the 49-character second row wraps
    # to two lines, while the short heading stays on one.
    assert "\\N" in item_lines[1]
    assert "separate account" in item_lines[1]
    assert "\\N" not in heading
    # The line break is one literal backslash-N in the file — never
    # the escaped double backslash (that renders a stray "\" on
    # screen and breaks the caption reader's plain-text join).
    assert "\\\\N" not in item_lines[1]
    assert "\\\\N" not in heading
    # Rows stack downwards: each item sits lower than the last.
    def _y(event):
        return int(event.split("\\pos(")[1].split(",")[1].split(")")[0])

    ys = [_y(e) for e in item_lines]
    assert ys == sorted(ys) and len(set(ys)) == 3
    assert _y(heading) < ys[0]  # the heading sits above the rows


def test_wrap_row_text_keeps_rows_inside_the_frame():
    from src.auto_director.list_fill import _wrap_row_text

    assert _wrap_row_text("Automate your savings") == "Automate your savings"
    wrapped = _wrap_row_text("2. Keep one month of spending in a separate account")
    assert wrapped == "2. Keep one month of spending in a\\Nseparate account"
    for line in wrapped.split("\\N"):
        assert len(line) <= 36
    # A row that would need three lines is cut after the second —
    # the full sentence is always in the captions.
    long_row = _wrap_row_text(
        "9. Keep one month of spending in a separate account for a rainy "
        "day fund starting now"
    )
    assert long_row.count("\\N") == 1
    assert "starting" not in long_row
    assert "now" not in long_row


def test_append_overlay_is_idempotent_and_uses_the_files_font(tmp_path):
    plan = build_list_fill_plan(LIST_FILL, LIST_FILL_DURATION)
    assert plan is not None
    ass = tmp_path / "captions.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Montserrat,40,&H00FFFFFF,&H000000FF,&H00000000,"
        "&H00000000,1,0,0,0,100,100,0,0,1,2,0,5,60,60,60,1\n"
        "Style: Hook,Montserrat,44,&H00FFFFFF,&H000000FF,&H00000000,"
        "&H00000000,1,0,0,0,100,100,0,0,1,2,0,8,60,60,60,1\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n",
        encoding="utf-8",
    )
    assert append_list_fill_overlay(ass, plan, 540, 960) is True
    text = ass.read_text(encoding="utf-8")
    assert "Style: ListFill,Montserrat," in text
    assert text.count("ListFill") == 1 + 7  # one style + seven Dialogue lines
    assert "Three money habits for a rich life" in text
    # A second append refuses: the overlay is never doubled.
    assert append_list_fill_overlay(ass, plan, 540, 960) is False
    assert ass.read_text(encoding="utf-8").count("ListFill") == 8


def test_append_overlay_refuses_a_file_without_events(tmp_path):
    plan = build_list_fill_plan(LIST_FILL, LIST_FILL_DURATION)
    assert plan is not None
    ass = tmp_path / "empty.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")
    assert append_list_fill_overlay(ass, plan, 540, 960) is False
