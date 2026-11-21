"""Scene-aware editing rules: triggers fire, restraints hold.

Each implemented rule is pinned twice — once on its trigger fixture
(the evidence that should fire it) and once off-trigger (the restraint:
the same fixture minus the evidence must produce today's behaviour).

Rules covered:
- podcast/hindi speaker-change punch-ins (EDL, diarization-gated),
- travel establishing-shot guard (EDL: zooms/SFX/B-roll window),
- comedy plan caps + pause protection (clip_plan.validate_plan_dict),
- planner prompt carries the scene rules (plan_service).
"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director.clip_plan import validate_plan_dict
from src.auto_director.edl import build_edl, load_profile
from src.auto_director.plan_service import _profile_summary
from src.auto_director.scene_rules import (
    establishing_guard_sec,
    scene_rules,
    speaker_change_times,
)


def _words(speaker, start, end, step=0.5):
    """Timed word dicts for one speaker run."""
    out = []
    t = start
    while t < end:
        entry = {"word": "w", "start": round(t, 2), "end": round(min(t + step, end), 2)}
        if speaker is not None:
            entry["speaker"] = speaker
        out.append(entry)
        t += step
    return out


def _punches(edl, trigger=None):
    return [
        z for z in edl["zoom_keyframes"]
        if z["type"] == "punch_in" and (trigger is None or z.get("trigger") == trigger)
    ]


CLIP = {"id": "c1", "start": 0, "end": 30, "duration": 30}


# --- Speaker-change punch-ins ----------------------------------------------


def test_speaker_punch_fires_on_speaker_change():
    profile = load_profile("podcast_news")
    words = _words("A", 0, 4) + _words("B", 5, 10) + _words("A", 13, 18)
    edl = build_edl(CLIP, words, profile, [])
    punches = _punches(edl, "speaker_change")
    assert [p["time"] for p in punches] == [5.0, 13.0]
    assert all(p["scale"] == 1.25 for p in punches)  # the profile's own scale
    assert edl["scene_rules"]["speaker_punches"] == 2


def test_speaker_punch_restraint_single_speaker():
    profile = load_profile("podcast_news")
    edl = build_edl(CLIP, _words("A", 0, 20), profile, [])
    assert _punches(edl, "speaker_change") == []


def test_speaker_punch_restraint_no_labels():
    """faster-whisper transcripts carry speaker=None: no labels, no punches."""
    profile = load_profile("podcast_news")
    words = _words(None, 0, 10) + _words(None, 11, 20)
    for w in words:
        w["speaker"] = None
    edl = build_edl(CLIP, words, profile, [])
    assert _punches(edl, "speaker_change") == []


def test_speaker_punch_restraint_short_interjection_absorbed():
    profile = load_profile("podcast_news")
    words = _words("A", 0, 5) + _words("B", 5.5, 6.0) + _words("A", 6.0, 12)
    edl = build_edl(CLIP, words, profile, [])
    assert _punches(edl, "speaker_change") == []


def test_speaker_punch_restraint_profile_not_opted_in():
    profile = load_profile("sports")
    words = _words("A", 0, 4) + _words("B", 5, 10)
    edl = build_edl(CLIP, words, profile, [])
    assert _punches(edl, "speaker_change") == []


def test_speaker_punch_restraint_min_gap():
    profile = load_profile("podcast_news")
    # Changes at 5.0 and 6.5 are 1.5 s apart; min_gap is 3.0 s.
    words = _words("A", 0, 4) + _words("B", 5, 6.4) + _words("A", 6.5, 12)
    edl = build_edl(CLIP, words, profile, [])
    assert [p["time"] for p in _punches(edl, "speaker_change")] == [5.0]


def test_speaker_punch_dedupes_against_energy_punch():
    profile = load_profile("podcast_news")
    words = _words("A", 0, 4) + _words("B", 5, 10)
    edl = build_edl(CLIP, words, profile, [5.2])
    # The energy punch at 5.2 already covers the moment; no stacked zoom.
    assert _punches(edl, "speaker_change") == []
    assert len(_punches(edl)) == 1


def test_speaker_punch_cap():
    profile = load_profile("podcast_news")
    words = []
    t = 0.0
    speaker = "A"
    while t < 58:
        words += _words(speaker, t, t + 3.5)
        t += 4.0
        speaker = "B" if speaker == "A" else "A"
    clip = {"id": "c2", "start": 0, "end": 60, "duration": 60}
    edl = build_edl(clip, words, profile, [])
    assert len(_punches(edl, "speaker_change")) == 6  # max_per_clip


def test_speaker_change_times_untimed_words_yield_nothing():
    words = [{"word": "a", "start": 0.0, "end": 0.0, "speaker": "A"},
             {"word": "b", "start": 0.0, "end": 0.0, "speaker": "B"}]
    assert speaker_change_times(words) == []


# --- Establishing-shot guard -------------------------------------------------


def test_establishing_guard_drops_early_punch_keeps_later_and_ken_burns():
    profile = load_profile("travel_vlog")
    edl = build_edl(CLIP, _words(None, 0, 10), profile, [1.0, 10.0])
    times = [p["time"] for p in _punches(edl)]
    assert times == [10.0]
    assert any(z["type"] == "ken_burns" for z in edl["zoom_keyframes"])
    assert edl["scene_rules"]["establishing_shot_sec"] == 3.0


def test_establishing_guard_japan_window():
    profile = load_profile("japan_travel_cinematic")
    edl = build_edl(CLIP, _words(None, 0, 10), profile, [3.2, 4.0])
    assert [p["time"] for p in _punches(edl)] == [4.0]


def test_establishing_guard_restraint_short_clip():
    """A 5 s clip cannot spare a 3 s guard: the rule switches off."""
    profile = load_profile("travel_vlog")
    clip = {"id": "c3", "start": 0, "end": 5, "duration": 5}
    edl = build_edl(clip, _words(None, 0, 4), profile, [1.0])
    assert [p["time"] for p in _punches(edl)] == [1.0]
    assert establishing_guard_sec(profile, 5.0) == 0.0
    assert establishing_guard_sec(profile, 30.0) == 3.0


def test_establishing_guard_restraint_other_niches_untouched():
    profile = load_profile("podcast_news")
    edl = build_edl(CLIP, _words(None, 0, 10), profile, [1.0])
    assert [p["time"] for p in _punches(edl)] == [1.0]
    assert "scene_rules" not in edl


# --- Comedy plan caps + pause protection -------------------------------------


def _no_rules(profile):
    clone = copy.deepcopy(profile)
    clone.pop("scene_rules", None)
    return clone


def _raw_plan(**overrides):
    raw = {
        "hook": {"type": "strong_statement", "timestamp": 1.0,
                 "confidence": 0.7, "reason": "The guest makes a bold claim."},
        "opening_treatment": {"punch_zoom": "strong",
                              "first_caption_emphasis": False,
                              "sfx_accent": None, "at_offset": 1.0,
                              "reason": "The bold claim lands on this line."},
        "transition_choices": [],
        "sfx_events": [],
        "music_decision": {"use_profile_music": True, "reason": "Profile fits."},
        "plan_confidence": 0.7,
    }
    raw.update(overrides)
    return raw


def _sfx_events():
    return [
        {"at_offset": 2.0, "name": "pop", "reason": "the first joke lands here"},
        {"at_offset": 5.0, "name": "impact", "reason": "the table slap punctuates it"},
        {"at_offset": 8.0, "name": "ding", "reason": "the callback to the earlier bit"},
        {"at_offset": 11.0, "name": "whoosh", "reason": "the scene change into the story"},
    ]


def test_comedy_sfx_cap_fires_and_control_uses_global_cap():
    comedy = load_profile("meme_comedy")
    plan = validate_plan_dict(
        _raw_plan(sfx_events=_sfx_events()),
        niche="meme_comedy", duration=20.0, profile=comedy,
    )
    assert len(plan.sfx_events) == 2  # scene-rule cap, not the global 4
    control = validate_plan_dict(
        _raw_plan(sfx_events=_sfx_events()),
        niche="meme_comedy", duration=20.0, profile=_no_rules(comedy),
    )
    assert len(control.sfx_events) == 4


def test_comedy_transition_cap():
    transitions = [
        {"name": "fade", "at_offset": 3.0, "reason": "the topic shifts to childhood"},
        {"name": "dissolve", "at_offset": 6.0, "reason": "the flashback begins here"},
        {"name": "wipeleft", "at_offset": 9.0, "reason": "the location changes on screen"},
    ]
    comedy = load_profile("meme_comedy")
    plan = validate_plan_dict(
        _raw_plan(transition_choices=transitions),
        niche="meme_comedy", duration=20.0, profile=comedy,
    )
    assert len(plan.transition_choices) == 1
    control = validate_plan_dict(
        _raw_plan(transition_choices=transitions),
        niche="meme_comedy", duration=20.0, profile=_no_rules(comedy),
    )
    assert len(control.transition_choices) == 2  # global budget


def test_comedy_strong_punch_downgraded_without_punchline():
    comedy = load_profile("meme_comedy")
    plan = validate_plan_dict(
        _raw_plan(), niche="meme_comedy", duration=20.0, profile=comedy,
    )
    assert plan.opening_treatment.punch_zoom == "subtle"


def test_comedy_strong_punch_kept_for_punchline_hook():
    comedy = load_profile("meme_comedy")
    raw = _raw_plan()
    raw["hook"] = {"type": "punchline", "timestamp": 1.0, "confidence": 0.9,
                   "reason": "The deadpan answer is the joke."}
    plan = validate_plan_dict(
        raw, niche="meme_comedy", duration=20.0, profile=comedy,
    )
    assert plan.opening_treatment.punch_zoom == "strong"
    control = validate_plan_dict(
        _raw_plan(), niche="meme_comedy", duration=20.0,
        profile=_no_rules(comedy),
    )
    assert control.opening_treatment.punch_zoom == "strong"


def test_comedy_pause_protection_drops_accent_on_pause():
    comedy = load_profile("meme_comedy")
    events = [
        {"at_offset": 5.5, "name": "pop", "reason": "a short accent for the beat"},
        {"at_offset": 10.0, "name": "ding", "reason": "the callback lands here"},
    ]
    pauses = [{"start": 5.0, "end": 6.2}]
    plan = validate_plan_dict(
        _raw_plan(sfx_events=events),
        niche="meme_comedy", duration=20.0, profile=comedy, pauses=pauses,
    )
    assert [e.at_offset for e in plan.sfx_events] == [10.0]


def test_comedy_pause_protection_inert_without_pauses_or_rule():
    comedy = load_profile("meme_comedy")
    events = [
        {"at_offset": 5.5, "name": "pop", "reason": "a short accent for the beat"},
    ]
    # No measured pauses supplied -> nothing to protect against.
    plan = validate_plan_dict(
        _raw_plan(sfx_events=events),
        niche="meme_comedy", duration=20.0, profile=comedy,
    )
    assert len(plan.sfx_events) == 1
    # Same pauses, profile without the rule -> accent stands.
    plan = validate_plan_dict(
        _raw_plan(sfx_events=events),
        niche="meme_comedy", duration=20.0, profile=_no_rules(comedy),
        pauses=[{"start": 5.0, "end": 6.2}],
    )
    assert len(plan.sfx_events) == 1


# --- Prompt + profile hygiene -------------------------------------------------


def test_planner_prompt_carries_scene_rules():
    comedy_summary = _profile_summary(load_profile("meme_comedy"))
    assert "Scene rule" in comedy_summary and "pauses are protected" in comedy_summary
    podcast_summary = _profile_summary(load_profile("podcast_news"))
    assert "speaker changes" in podcast_summary
    assert "Scene rule" not in _profile_summary(load_profile("sports"))


# --- Bold-claim opener (Trend-Radar D7) ---------------------------------------


def _sentence_words(sentences):
    """Word dicts for [(text, start)] sentences, 0.25 s per word.

    Uses the production "text" key: timeline word dicts carry "text"
    (src/media/timeline.py), and the reused _opening_is_weak guard reads
    only that key — a "word"-keyed fixture would silently bypass it.
    """
    words = []
    for text, start in sentences:
        t = start
        for token in text.split():
            words.append({"text": token, "start": round(t, 2), "end": round(t + 0.25, 2)})
            t += 0.3
    return words


_TRAVEL_SENTENCES = [
    ("Welcome back to the channel.", 0.0),
    ("Today we are walking around the old town and looking at shops.", 3.5),
    ("This tiny sandwich cost $400 and it was worth it.", 9.0),
]


def test_claim_marker_scores():
    from src.auto_director.scene_rules import claim_marker_score

    assert claim_marker_score("This tiny sandwich cost $400.") >= 2  # money = strong
    assert claim_marker_score("The most expensive meal in Japan.") >= 2  # 2 mediums
    assert claim_marker_score("Things I wish I knew before visiting.") >= 2  # strong phrase
    assert claim_marker_score("A hidden place we found.") == 1  # one medium is not enough
    assert claim_marker_score("We walked around the old town.") == 0


def test_bold_claim_picker_candidate_already_and_none():
    from src.auto_director.scene_rules import pick_bold_claim_sentence

    picked = pick_bold_claim_sentence(_sentence_words(_TRAVEL_SENTENCES))
    assert picked["status"] == "candidate"
    assert picked["candidate"]["score"] >= 2
    assert "sandwich" in picked["candidate"]["text"]

    claim_first = [
        ("This meal cost $400 in total.", 0.0),
        ("Then we walked around town.", 4.0),
    ]
    assert pick_bold_claim_sentence(_sentence_words(claim_first))["status"] == "already_claim"

    plain = [
        ("Welcome back to the channel.", 0.0),
        ("We walked around the old town.", 3.5),
    ]
    assert pick_bold_claim_sentence(_sentence_words(plain))["status"] == "no_claim"


def test_bold_claim_picker_window_restraint():
    from src.auto_director.scene_rules import pick_bold_claim_sentence

    late_claim = [
        ("Welcome back to the channel.", 0.0),
        ("We walked around the old town for a while.", 3.5),
        ("Much later this cost $900 in total.", 40.0),
    ]
    picked = pick_bold_claim_sentence(_sentence_words(late_claim), window_sec=25.0)
    assert picked["status"] == "no_claim"  # outside the opener window


def test_bold_claim_opener_reorders_and_records():
    from src.auto_director.hook_enforcement import evaluate_bold_claim_opener

    profile = load_profile("travel_vlog")
    words = _sentence_words(_TRAVEL_SENTENCES)
    decision = evaluate_bold_claim_opener(
        None, words=words, keep_ranges=[(0.0, 30.0)], duration=30.0, profile=profile,
    )
    assert decision.outcome is not None
    assert decision.outcome["status"] == "bold_claim_opener"
    assert decision.keep_ranges is not None
    total = sum(end - start for start, end in decision.keep_ranges)
    assert abs(total - 30.0) < 0.15  # reorder only: no footage lost


def test_bold_claim_opener_profile_gate():
    from src.auto_director.hook_enforcement import evaluate_bold_claim_opener

    words = _sentence_words(_TRAVEL_SENTENCES)
    decision = evaluate_bold_claim_opener(
        None, words=words, keep_ranges=[(0.0, 30.0)], duration=30.0,
        profile=load_profile("podcast_news"),
    )
    assert decision.outcome is None and decision.keep_ranges is None


def test_bold_claim_opener_weak_opener_guard():
    from src.auto_director.hook_enforcement import evaluate_bold_claim_opener

    sentences = [
        ("Welcome back to the channel.", 0.0),
        ("We are walking around the old town today.", 3.5),
        ("He charged us $400 for it, the most expensive bite of the trip.", 9.0),
    ]
    decision = evaluate_bold_claim_opener(
        None, words=_sentence_words(sentences), keep_ranges=[(0.0, 30.0)],
        duration=30.0, profile=load_profile("travel_vlog"),
    )
    # The claim sentence opens on a pronoun ("He") pointing at a person the
    # viewer has not met yet — the codebase's own weak-opener judgement
    # (_opening_is_weak) — so the guard keeps the original order.
    assert decision.keep_ranges is None
    assert decision.outcome is not None
    assert decision.outcome["status"] == "kept_original_context"


def test_scene_rules_malformed_section_is_off_not_fatal():
    assert scene_rules({"scene_rules": {"establishing_shot_sec": "abc"}}) == {}
    assert scene_rules({"scene_rules": "nope"}) == {}
    assert scene_rules({}) == {}
    assert establishing_guard_sec({"scene_rules": {"establishing_shot_sec": 3.0}}, 30.0) == 3.0
