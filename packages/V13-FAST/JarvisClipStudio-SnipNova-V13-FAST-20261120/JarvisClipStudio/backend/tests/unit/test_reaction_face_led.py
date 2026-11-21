"""Face-led reaction candidates (Sanji Q2/Q7, 2026-11-18; switch REACTION_FACE_LED).

A run of >= 2 s where mouth-corner width or head jump (both / face height,
10 Hz, per-task z) reaches z >= 4 is a candidate even with a quiet game:
capped at 45 alone, up to 55 when a game event coincides.
"""

import numpy as np
import pytest

from src.auto_director import reaction_moments as rm
from src.auto_director.gameplay_moments import Episode


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv("REACTION_FACE_LED", raising=False)


def _dynamics(seconds=120, mouth_spike=None, jump_spike=None):
    rng = np.random.default_rng(11)
    mouth = 0.42 + rng.normal(0, 0.01, seconds)
    jump = 0.05 + np.abs(rng.normal(0, 0.01, seconds))
    if mouth_spike:
        a, b = mouth_spike
        mouth[a:b] += 0.2
    if jump_spike:
        a, b = jump_spike
        jump[a:b] += 0.3
    return {"mouth": mouth, "jump": jump}


def test_a_sustained_face_reaction_becomes_a_capped_candidate():
    items = rm.face_led_items(_dynamics(mouth_spike=(50, 54)), [], [])
    assert len(items) == 1
    it = items[0]
    assert (it["start"], it["end"]) == (50.0, 54.0)
    assert it["type"] == "face_reaction" and it["event"] is None
    assert it["features"]["face_mouth"] >= rm._FACE_LED_Z
    assert rm._score(it) <= rm._FACE_LED_CAP


def test_a_one_second_twitch_is_not_a_reaction():
    assert rm.face_led_items(_dynamics(jump_spike=(30, 31)), [], []) == []


def test_existing_reactions_are_not_duplicated():
    existing = [{"type": "reaction_to_event", "start": 49.0, "end": 55.0}]
    assert rm.face_led_items(_dynamics(mouth_spike=(50, 54)), existing, []) == []
    # An unreacted game event does not block it.
    ignored = [{"type": "silent_action", "start": 49.0, "end": 55.0}]
    assert len(rm.face_led_items(_dynamics(mouth_spike=(50, 54)), ignored, [])) == 1


def test_a_coinciding_game_event_lifts_the_cap():
    event = Episode(start=49.0, end=51.0, peak=50.0, strength=20.0, features={})
    it = rm.face_led_items(_dynamics(jump_spike=(50, 53)), [], [event])[0]
    assert it["event"] is event
    alone = {**it, "event": None}
    assert rm._score(alone) <= rm._FACE_LED_CAP
    assert rm._score(it) <= rm._FACE_LED_WITH_EVENT_CAP
    assert rm._score(it) >= rm._score(alone)


def test_switch(monkeypatch):
    assert rm.face_led_enabled() is True
    monkeypatch.setenv("REACTION_FACE_LED", "0")
    assert rm.face_led_enabled() is False
