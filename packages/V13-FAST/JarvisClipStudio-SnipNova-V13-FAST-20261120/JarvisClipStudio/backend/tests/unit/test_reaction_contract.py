"""REACTION_API_CONTRACT §2 (Sanji, 2026-11-17): additive fields on reaction moments.

signals.* are 0-1 or None (None = not measured, never 0), fused_score is 0-100,
reason is one plain sentence, layout_recommendation uses the contract names.
Existing fields keep their names (§3: additive only).
"""

import numpy as np
import pytest

from src.auto_director import reaction_moments as rm
from tests.unit.test_reaction_moments import _face, _game, _pick, _voice


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.delenv("REACTION_CONTRACT_FIELDS", raising=False)


def _shout(v, f, at):
    v["vocal"][at:at + 4] += 15
    v["f0"][at:at + 4] += 120
    f[at:at + 4] += 12


def test_reaction_to_event_carries_every_contract_field():
    v, f = _voice(), _face()
    _shout(v, f, 90)
    out = _pick(v, f, _game(events=[(88, 91)]))
    m = out[0]
    # Existing fields unchanged.
    for key in ("start_time", "end_time", "text", "reasoning", "virality_score",
                "relevance_score", "hook_title", "evidence"):
        assert key in m
    s = m["signals"]
    assert set(s) == {"facecam_reaction", "voice_burst", "transcript_emotion", "gameplay_event"}
    assert s["facecam_reaction"] >= 0.5 and s["voice_burst"] >= 0.5
    assert s["gameplay_event"] >= 0.5
    # No transcript was given: unmeasured is None, never 0.
    assert s["transcript_emotion"] is None
    assert 0 <= m["fused_score"] <= 100
    assert m["fused_score"] == pytest.approx(m["virality_score"], abs=0.6)
    assert "reacts" in m["reason"] and "gameplay event" in m["reason"]
    assert m["layout_recommendation"] in {"stacked_reactor", "pip_preserved", "facecam_full", "gameplay_only"}


def test_decoy_window_measures_zero_reaction_not_none():
    zs = {"vocal": np.zeros(120), "pitch": np.zeros(120), "laugh": np.zeros(120),
          "face": np.zeros(120)}
    game = np.zeros(120)
    game[80:86] = 9.0          # the loudest gameplay event, nobody reacts
    fields = rm.contract_fields(78, 90, 35.0, zs, voice_present=True, transcript_present=False,
                                facecam_present=True, game_event=game)
    s = fields["signals"]
    assert s["facecam_reaction"] == 0.0 and s["voice_burst"] == 0.0   # measured zeros
    assert s["gameplay_event"] == 1.0
    assert s["transcript_emotion"] is None
    assert fields["reason"] == "Something happens in the game, but the streamer does not react."


def test_no_facecam_and_no_voice_are_unmeasured():
    fields = rm.contract_fields(10, 20, 40.0, {"lexical": np.full(60, 4.0)}, voice_present=False,
                                transcript_present=True, facecam_present=False, game_event=None)
    s = fields["signals"]
    assert s["facecam_reaction"] is None and s["voice_burst"] is None and s["gameplay_event"] is None
    assert s["transcript_emotion"] == pytest.approx(4.0 / 6.0, abs=0.001)


def test_layout_names_map_to_the_contract():
    moments = [
        {"fused_score": 50, "edit_recommendations": {"framing": {"layout": "full"}}},
        {"fused_score": 50, "edit_recommendations": {"framing": {"layout": "stacked_reactor"}}},
        {"fused_score": 50, "edit_recommendations": {"framing": {"layout": "pip_preserved"}}},
        {"fused_score": 50},
        {"edit_recommendations": {"framing": {"layout": "full"}}},     # contract fields off
    ]
    rm.apply_contract_layout(moments)
    assert [m.get("layout_recommendation") for m in moments] == [
        "gameplay_only", "stacked_reactor", "pip_preserved", None, None]
    # The producer's own field keeps its name and value.
    assert moments[0]["edit_recommendations"]["framing"]["layout"] == "full"


def test_off_switch_removes_every_contract_field(monkeypatch):
    monkeypatch.setenv("REACTION_CONTRACT_FIELDS", "0")
    v, f = _voice(), _face()
    _shout(v, f, 90)
    m = _pick(v, f, _game(events=[(88, 91)]))[0]
    for key in ("signals", "fused_score", "reason", "layout_recommendation"):
        assert key not in m
