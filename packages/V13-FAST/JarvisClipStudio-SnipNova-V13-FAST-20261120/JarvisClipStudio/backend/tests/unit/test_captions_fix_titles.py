"""Captions-fix lane: pick-title cleaning + hook-title accent fallback.

The title pins are Luffy's (2026-11-19): no emojis, never first person,
short, and the burned title must not come out plain white on the
all-white seam templates.
"""
from __future__ import annotations

import pytest

from src.caption_templates import get_template
from src.live import vod as V
from src.media.captions import build_hook_title_ass, hex_to_ass_color

# ---------------------------------------------------------------------------
# clean_pick_title
# ---------------------------------------------------------------------------

def test_quoted_title_loses_its_quotes():
    # The exact failure on the Kai Cenat clips: the model's surrounding
    # quote characters were burned into the video verbatim.
    assert V.clean_pick_title('"Kaicenat hollas for independent women"') == "Kaicenat hollas for independent women"


def test_good_titles_pass_through():
    assert V.clean_pick_title("He lost it") == "He lost it"
    assert V.clean_pick_title("Kai Cenat screams at Arnold") == "Kai Cenat screams at Arnold"


def test_emoji_are_dropped():
    assert V.clean_pick_title("Kai Cenat goes crazy 🔥🔥") == "Kai Cenat goes crazy"


def test_first_person_titles_are_dropped_not_repaired():
    assert V.clean_pick_title("I screamed so loud") is None
    assert V.clean_pick_title("Watch me lose it") is None
    assert V.clean_pick_title("My craziest moment yet") is None


def test_titles_are_capped_at_eight_words():
    title = V.clean_pick_title("one two three four five six seven eight nine ten")
    assert title == "one two three four five six seven eight"


def test_unusable_titles_become_none():
    assert V.clean_pick_title("") is None
    assert V.clean_pick_title("Solo") is None
    assert V.clean_pick_title(None) is None
    assert V.clean_pick_title(42) is None


def test_llm_pick_cleans_the_models_titles(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gw/v1")
    monkeypatch.setenv("LLM", "openai:clip-fast")
    import json

    import httpx

    answer = [{"id": 1, "start": 795, "end": 830, "title": '"I lost my mind here"', "why": "scream", "virality": 90},
              {"id": 2, "start": 795, "end": 830, "title": '"He lost his mind"', "why": "scream", "virality": 80}]

    class R:
        def json(self):
            return {"choices": [{"message": {"content": json.dumps(answer)}}]}

    monkeypatch.setattr(httpx, "post", lambda url, **kw: R())
    cands = [{"id": 1, "onset": 800.0, "peak": 5.0, "signals": ["scream"], "heat": 0.4, "words": []},
             {"id": 2, "onset": 800.0, "peak": 5.0, "signals": ["scream"], "heat": 0.4, "words": []}]
    picks = V.llm_pick(cands, 2)
    by_id = {p["id"]: p for p in picks}
    assert by_id[1]["title"] is None       # first person: dropped
    assert by_id[2]["title"] == "He lost his mind"  # quotes stripped


# ---------------------------------------------------------------------------
# Hook-title accent fallback
# ---------------------------------------------------------------------------

def _ass_text(template, title):
    style, events = build_hook_title_ass(title, template, 1080, 1920, 10.0, "Anton-Regular", 68)
    return style + "\n" + "\n".join(events)


def test_seam_red_title_gets_the_accent_on_power_words():
    # Landing reconciliation (Nov 22 tree): B-0025 (owner pin — karaoke
    # fill) set seam_red's CAPTION highlight to #FFD400, but the title
    # builder resolves its accent from emphasis_color first, which is
    # still #FFFFFF — for the title the template is still all-white, so
    # the fallback still fires and the power words take the system
    # accent, exactly the lane behaviour this test was written for.
    template = get_template("seam_red")
    assert template["font_color"] == template["emphasis_color"] == "#FFFFFF"  # all-white for the title
    assert template["highlight_color"] == "#FFD400"  # B-0025 karaoke caption highlight
    text = _ass_text(template, "Kai Cenat hits 100 on the highway")
    accent = hex_to_ass_color("#FFE000", "#FFE000")
    assert accent in text  # the digit token is accented in the system yellow
    assert accent != hex_to_ass_color("#FFFFFF", "#FFFFFF")


def test_all_white_template_title_falls_back_to_the_system_accent():
    # The fallback's original target: a template whose highlight
    # resolves to the same colour as its primary (the pre-B-0025
    # seam_red setup) gets the caption system's accent on power words.
    template = dict(get_template("seam_red"), highlight_color="#FFFFFF")
    assert template["font_color"] == template["highlight_color"] == "#FFFFFF"  # the all-white setup
    text = _ass_text(template, "Kai Cenat hits 100 on the highway")
    accent = hex_to_ass_color("#FFE000", "#FFE000")
    assert accent in text  # the digit token is accented in the system yellow
    assert accent != hex_to_ass_color("#FFFFFF", "#FFFFFF")


def test_seam_gold_title_keeps_its_own_accent():
    template = get_template("seam_gold")
    text = _ass_text(template, "Kai Cenat hits 100 on the highway")
    assert hex_to_ass_color("#FFD400", "#FFD400") in text
    # ...and the fallback yellow never appears: the template's own
    # highlight wins whenever it differs from the primary colour.
    assert hex_to_ass_color("#FFE000", "#FFE000") not in text


def test_default_template_title_is_unchanged():
    template = get_template("default")
    text = _ass_text(template, "Kai Cenat hits 100 on the highway")
    own = hex_to_ass_color(template.get("emphasis_color") or template.get("highlight_color"), "#FFE000")
    assert own in text
