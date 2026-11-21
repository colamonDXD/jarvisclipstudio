"""GA2 hooks/boundaries/captions and editing recommendations for reaction clips."""
from src.auto_director import edit_recommendations as er
from src.auto_director import reaction_hooks as rh


def _moment(mtype="reaction_to_event", kind="laughter", game="screen_flash_banner", r=(40.0, 46.0), g=(36.0, 39.0)):
    return {"start_time": "00:32", "end_time": "00:50", "evidence": {
        "moment_type": mtype, "reaction_kind": kind,
        "reaction_start_sec": r[0], "reaction_end_sec": r[1],
        "game_event": None if game is None else {"start_sec": g[0], "end_sec": g[1], "type": game}}}


def test_event_hook_is_grounded_third_person_and_short():
    t = rh.reaction_hook_title(_moment())
    assert 3 <= len(t.split()) <= 9 and t.startswith("Streamer")
    assert "on-screen" in t and " I " not in f" {t} "


def test_hook_never_names_game_events_the_pictures_did_not_show():
    for game in ("screen_flash_banner", "hud_event_streak", "erratic_motion", "visual_change", None):
        t = rh.reaction_hook_title(_moment(game=game, mtype="reaction_to_event" if game else "commentary_reaction"))
        assert not any(w in t.lower() for w in ("gta", "car", "explod", "kill", "crash", "police", "heist"))


def test_commentary_has_no_invented_trigger():
    t = rh.reaction_hook_title(_moment(mtype="commentary_reaction", game=None))
    assert "on-screen" not in t


def test_hooks_vary_across_clips():
    titles = set()
    for i in range(12):
        m = _moment(); m["start_time"] = f"0{i % 10}:{i:02d}"
        titles.add(rh.reaction_hook_title(m))
    assert len(titles) >= 2


def test_plain_gameplay_moment_gets_no_reaction_hook():
    assert rh.reaction_hook_title({"evidence": {"detector": "frame_evidence_v1"}}) is None


def test_profanity_modes_and_verbatim_quote():
    words = [{"text": "oh", "start": 40.0, "end": 40.2}, {"text": "shit", "start": 40.3, "end": 40.6}]
    assert rh.reaction_quote(words, 40, 46, "keep") == "oh shit"
    assert rh.reaction_quote(words, 40, 46, "mask") == "oh ***"
    assert rh.reaction_quote(words, 40, 46, "drop") == "oh"
    assert rh.reaction_quote([], 40, 46) is None


def test_boundaries_never_cut_a_word_and_keep_the_payoff():
    words = [{"text": "wait", "start": 31.5, "end": 32.4}, {"text": "what", "start": 32.5, "end": 33.0},
             {"text": "haha", "start": 45.6, "end": 46.9}]
    s, e, notes = rh.refine_window(32.0, 46.3, 40.0, 46.0, 120.0, words)
    assert s == 31.5 and e >= 46.9 and notes


def test_boundaries_extend_while_reaction_still_active():
    strength = [0.0] * 120
    for t in range(44, 51):
        strength[t] = 5.0
    s, e, notes = rh.refine_window(32.0, 48.0, 44.0, 47.0, 120.0, None, strength)
    assert e >= 50 + 1.5 and any("extended" in n for n in notes)


def test_caption_plan_puts_emphasis_on_reaction_and_event_cue_on_event():
    words = [{"text": "no", "start": 40.1, "end": 40.3}]
    plan = rh.caption_plan(_moment(), 32.0, words, (0.7, 0.6, 0.25, 0.3), "keep")
    assert plan["emphasis"][0]["start"] == 8.0 and plan["emphasis"][0]["style"] == "laugh"
    assert plan["event_cue"]["at"] == 4.0 and plan["event_cue"]["not_audio_peak"] is True
    # Reaction-led clip with a facecam -> stacked reactor layout -> captions on the pane seam.
    assert plan["position"]["anchor"] == "pane_seam" and "facecam" in plan["position"]["avoid"]
    assert plan["emphasis_words"][0]["text"] == "no"


def test_recommendations_contract_and_bounds():
    m = _moment()
    rec = er.recommend_edits(m, 32.0, 50.0, facecam_roi=(0.7, 0.6, 0.25, 0.3))
    assert rec["schema_version"] == 1
    assert rec["framing"]["layout"] == "stacked_reactor" and rec["framing"]["facecam_roi"]
    assert all(z["to_scale"] <= 1.15 and z["start"] >= 0 and z["end"] <= 18.0 for z in rec["zooms"])
    assert rec["pacing"]["open_on"] == "trigger" and rec["sfx_cues"][0]["requires_library"] is True


def test_no_facecam_means_fit_game_only():
    rec = er.recommend_edits(_moment(), 32.0, 50.0, facecam_roi=None)
    assert rec["framing"]["layout"] == "full" and rec["framing"]["mode"] == "fit"
    assert all(z["target"] == "gameplay" for z in rec["zooms"])


def test_no_trigger_opens_on_reaction():
    rec = er.recommend_edits(_moment(mtype="commentary_reaction", game=None), 32.0, 50.0)
    assert rec["pacing"]["open_on"] == "reaction" and rec["sfx_cues"] == []


def test_quote_rejects_fragments_and_centres_on_the_exclamation():
    assert rh.reaction_quote([{"text": "Come", "start": 40.0, "end": 40.2}], 40, 46) is None
    words = [{"text": t, "start": 40.0 + i * 0.3, "end": 40.2 + i * 0.3} for i, t in
             enumerate(["so", "I", "was", "going", "there", "and", "what", "the", "hell!", "bro"])]
    q = rh.reaction_quote(words, 40, 46, "keep")
    assert "hell!" in q and len(q.split()) <= 6


def test_layout_and_caption_vocabulary_match_the_app_and_the_amendment():
    from src.auto_director.clip_edits import CAPTION_POSITIONS

    layouts = {er.recommend_framing(roi, mt, "shout")["layout"]
               for roi in (None, (0.7, 0.6, 0.25, 0.3)) for mt in ("reaction_to_event", "silent_action")}
    assert layouts <= {"stacked_reactor", "pip_preserved", "full"}
    for roi, layout in ((None, "full"), ((0.7, 0.6, 0.25, 0.3), "stacked_reactor"),
                        ((0.7, 0.6, 0.25, 0.3), "pip_preserved"), ((0.05, 0.05, 0.2, 0.3), "pip_preserved")):
        pos = rh.caption_position(roi, layout)
        assert pos["preset"] in CAPTION_POSITIONS
    assert rh.caption_position((0.7, 0.6, 0.25, 0.3), "pip_preserved")["preset"] == "top"
    assert rh.caption_position((0.05, 0.05, 0.2, 0.3), "pip_preserved")["preset"] == "bottom"
    rec = er.recommend_edits(_moment(), 32.0, 50.0, facecam_roi=(0.7, 0.6, 0.25, 0.3))
    assert {z["target"] for z in rec["zooms"]} <= {"facecam", "gameplay"}
